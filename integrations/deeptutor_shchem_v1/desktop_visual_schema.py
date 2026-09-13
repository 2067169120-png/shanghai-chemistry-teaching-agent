from __future__ import annotations

"""Equivalent wire dialect for the probed official DeepSeek visual endpoint.

This is deliberately limited to the visual-observation contract's dialect.
The canonical schema and local semantic validators remain unchanged. New or
unsupported schema constructs fail before transport instead of losing rules.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from .model_provider_settings import ModelProviderProbeContext

_MAX_SCHEMA_DEPTH = 64
_MAX_SCHEMA_NODES = 10_000
VISUAL_OBSERVATION_PROMPT_VERSION = "normalized-xywh-roles-pages-v4"
_SCHEMA_KEYS = {
    "$ref",
    "type",
    "enum",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "maxItems",
    "anyOf",
}


class DesktopVisualSchemaError(ValueError):
    """The narrow wire adapter cannot preserve the supplied schema safely."""


def visual_import_request_policy(
    base_url: str, model_id: str, api_style: str
) -> dict[str, Any]:
    """Expose the same route-specific limits to approval and request building."""
    try:
        endpoint = urlsplit(base_url)
        official_endpoint = (
            endpoint.scheme == "https"
            and endpoint.hostname == "api.deepseek.com"
            and endpoint.port in {None, 443}
            and endpoint.username is None
            and endpoint.password is None
            and not endpoint.query
            and not endpoint.fragment
        )
    except ValueError:
        official_endpoint = False
    if (
        official_endpoint
        and api_style == "responses"
        and model_id == "deepseek-v4-flash-vision-exp"
    ):
        # A real 8,000-token response used the entire output allowance for
        # reasoning and returned no fragment. Keep reasoning enabled and make
        # the bounded increase visible in the matching approval snapshot.
        return {
            "max_output_tokens": 32000,
            "timeout_seconds": 300,
            "schema_dialect": "inline-v1",
            "observation_prompt_version": VISUAL_OBSERVATION_PROMPT_VERSION,
        }
    return {
        "max_output_tokens": 8000,
        "timeout_seconds": 90,
        "schema_dialect": "canonical-v2",
        "observation_prompt_version": VISUAL_OBSERVATION_PROMPT_VERSION,
    }


def desktop_visual_wire_schema(
    context: ModelProviderProbeContext, schema: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Return the original schema except for the exact probed provider route."""
    policy = visual_import_request_policy(
        context.base_url, context.model_id, context.api_style
    )
    if policy["schema_dialect"] == "canonical-v2":
        return schema
    return _inline_observation_schema(schema)


def desktop_visual_role_schema(
    schema: Mapping[str, Any], source_role: str
) -> dict[str, Any]:
    """Express the existing local role gate in the outbound schema as well."""
    if source_role not in {"question", "answer", "handout"}:
        raise DesktopVisualSchemaError("unsupported visual source role")
    forbidden = "theme_fragments" if source_role == "answer" else "answer_candidates"
    properties = schema.get("properties")
    array = properties.get(forbidden) if isinstance(properties, Mapping) else None
    if not isinstance(array, Mapping) or array.get("type") != "array":
        raise DesktopVisualSchemaError("role-restricted array is missing")
    result = deepcopy(dict(schema))
    # Keep the full item contract while allowing only the empty array. This
    # avoids the boolean item schema rejected by the live provider route.
    result["properties"][forbidden]["maxItems"] = 0
    return result


def desktop_visual_page_schema(
    schema: Mapping[str, Any], pages: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Bind each evidence row to one exact supplied page-manifest tuple."""
    if not isinstance(pages, (list, tuple)) or not 1 <= len(pages) <= 60:
        raise DesktopVisualSchemaError("page binding count is invalid")
    properties = schema.get("properties")
    array = properties.get("evidence") if isinstance(properties, Mapping) else None
    definitions = schema.get("$defs")
    evidence = definitions.get("evidence") if isinstance(definitions, Mapping) else None
    if (
        not isinstance(array, Mapping)
        or array.get("type") != "array"
        or array.get("items") != {"$ref": "#/$defs/evidence"}
        or not isinstance(evidence, Mapping)
        or evidence.get("type") != "object"
        or not isinstance(evidence.get("properties"), Mapping)
    ):
        raise DesktopVisualSchemaError("page-bound evidence schema is unsupported")
    binding_types = {
        "source_file_id": "string",
        "source_role": "string",
        "page_number": "integer",
        "page_sha256": "string",
    }
    branches = []
    for page in pages:
        if not isinstance(page, Mapping):
            raise DesktopVisualSchemaError("page binding manifest is invalid")
        branch = deepcopy(dict(evidence))
        for field, field_type in binding_types.items():
            value = page.get(field)
            field_schema = branch["properties"].get(field)
            if (
                not isinstance(field_schema, Mapping)
                or field_schema.get("type") not in (None, field_type)
                or ("enum" in field_schema and value not in field_schema["enum"])
                or (
                    field_type == "string" and (not isinstance(value, str) or not value)
                )
                or (field_type == "integer" and (type(value) is not int or value < 1))
            ):
                raise DesktopVisualSchemaError("page binding field is invalid")
            if field == "page_sha256" and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise DesktopVisualSchemaError("page binding digest is invalid")
            branch["properties"][field] = {
                **field_schema,
                "type": field_type,
                "enum": [value],
            }
        branches.append(branch)
    result = deepcopy(dict(schema))
    result["properties"]["evidence"]["items"] = {"anyOf": branches}
    return result


def _inline_observation_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, Mapping):
        raise DesktopVisualSchemaError("schema definitions must be an object")
    if schema.get("$schema") not in {
        None,
        "https://json-schema.org/draft/2020-12/schema",
    }:
        raise DesktopVisualSchemaError("unsupported schema dialect")
    if any(
        not isinstance(name, str) or "/" in name or "~" in name for name in definitions
    ):
        raise DesktopVisualSchemaError("unsupported definition name")
    visited = 0

    def expand(node: Any, active_refs: tuple[str, ...] = (), depth: int = 0) -> Any:
        nonlocal visited
        visited += 1
        if depth > _MAX_SCHEMA_DEPTH or visited > _MAX_SCHEMA_NODES:
            raise DesktopVisualSchemaError("schema expansion limit exceeded")
        if isinstance(node, bool):
            return node
        if not isinstance(node, Mapping):
            raise DesktopVisualSchemaError("schema node must be an object or boolean")
        if set(node) - _SCHEMA_KEYS:
            raise DesktopVisualSchemaError("unsupported schema keyword")
        if "$ref" in node:
            ref = node["$ref"]
            if (
                set(node) != {"$ref"}
                or not isinstance(ref, str)
                or not ref.startswith("#/$defs/")
                or ref.removeprefix("#/$defs/") not in definitions
            ):
                raise DesktopVisualSchemaError("unsupported schema reference")
            if ref in active_refs:
                raise DesktopVisualSchemaError("cyclic schema reference")
            return expand(
                definitions[ref.removeprefix("#/$defs/")],
                (*active_refs, ref),
                depth + 1,
            )
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties":
                if not isinstance(value, Mapping):
                    raise DesktopVisualSchemaError(
                        "schema properties must be an object"
                    )
                result[key] = {
                    name: expand(child, active_refs, depth + 1)
                    for name, child in value.items()
                }
            elif key in {"items", "additionalProperties"}:
                result[key] = expand(value, active_refs, depth + 1)
            elif key == "anyOf":
                if not isinstance(value, list) or not value:
                    raise DesktopVisualSchemaError("schema anyOf must be nonempty")
                result[key] = [expand(child, active_refs, depth + 1) for child in value]
            elif key == "maxItems":
                if type(value) is not int or value < 0:
                    raise DesktopVisualSchemaError(
                        "schema maxItems must be a nonnegative integer"
                    )
                result[key] = value
            else:
                # Literal enum and required values are data, not schema nodes.
                result[key] = deepcopy(value)
        node_type = result.get("type")
        if isinstance(node_type, list):
            if (
                node_type not in (["string", "null"], ["null", "string"])
                or "anyOf" in result
            ):
                raise DesktopVisualSchemaError("unsupported schema type union")
            del result["type"]
            result["anyOf"] = [{"type": member} for member in node_type]
        enum = result.get("enum")
        if (
            "type" not in result
            and isinstance(enum, list)
            and enum
            and all(isinstance(value, str) for value in enum)
        ):
            result["type"] = "string"
        return result

    # Check even unused definitions before removing their root container.
    # Expansion is intentionally uncached so the bound covers output growth.
    for name, definition in definitions.items():
        expand(definition, (f"#/$defs/{name}",))
    expanded = expand(
        {key: value for key, value in schema.items() if key not in {"$schema", "$defs"}}
    )
    if not isinstance(expanded, dict):
        raise DesktopVisualSchemaError("root schema must remain an object")
    return expanded


__all__ = [
    "VISUAL_OBSERVATION_PROMPT_VERSION",
    "DesktopVisualSchemaError",
    "desktop_visual_page_schema",
    "desktop_visual_role_schema",
    "desktop_visual_wire_schema",
    "visual_import_request_policy",
]
