from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import json
from jsonschema import Draft202012Validator


class SchemaValidationError(ValueError):
    pass


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(instance: Any, schema: dict, path: str = "$", root_schema: dict | None = None) -> None:
    if root_schema is None and path == "$":
        try:
            Draft202012Validator.check_schema(schema)
            errors = sorted(
                Draft202012Validator(schema).iter_errors(instance),
                key=lambda error: [str(component) for component in error.absolute_path],
            )
        except Exception as exc:
            raise SchemaValidationError(f"$: invalid Draft 2020-12 schema: {exc}") from exc
        if errors:
            error = errors[0]
            location = "$" + "".join(
                f"[{component}]" if isinstance(component, int) else f".{component}"
                for component in error.absolute_path
            )
            raise SchemaValidationError(f"{location}: {error.message}")
        return
    if root_schema is None:
        root_schema = schema
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise SchemaValidationError(f"{path}: unsupported reference {reference}")
        target: Any = root_schema
        for token in reference[2:].split("/"):
            target = target[token.replace("~1", "/").replace("~0", "~")]
        validate(instance, target, path, root_schema)
        return
    expected_type = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    expected_python_type = (
        tuple(type_map[name] for name in expected_type)
        if isinstance(expected_type, list)
        else type_map.get(expected_type)
    )
    if expected_type and expected_python_type and not isinstance(instance, expected_python_type):
        raise SchemaValidationError(f"{path}: expected {expected_type}")
    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: not in enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            raise SchemaValidationError(f"{path}: missing {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(properties))
            if extra:
                raise SchemaValidationError(f"{path}: unexpected {extra}")
        for name, subschema in properties.items():
            if name in instance:
                validate(instance[name], subschema, f"{path}.{name}", root_schema)

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path}: too few items")
        if schema.get("uniqueItems"):
            markers = [repr(item) for item in instance]
            if len(markers) != len(set(markers)):
                raise SchemaValidationError(f"{path}: duplicate items")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate(item, schema["items"], f"{path}[{index}]", root_schema)

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path}: string too short")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, instance) is None:
            raise SchemaValidationError(f"{path}: pattern mismatch")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: below minimum")
