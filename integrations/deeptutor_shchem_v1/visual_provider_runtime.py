from __future__ import annotations

"""Shared, image-native provider runtime.

This module deliberately has no text-recognition dependency.  Callers provide
trusted manifest text and page pixels; the pixels are attached directly to a
multimodal request.  The paper-intake and private student-analysis domains keep
their own schemas and persistence models.
"""

import base64
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .intake_imports import (
    LocalPageRenderer,
    PageRenderer,
    PinnedVisualTransport,
    RenderedPage,
    VisualProviderRequest,
    VisualTransport,
    _egress_image,
)
from .model_provider_settings import ModelProviderProbeContext

MAX_VISUAL_REQUEST_BYTES = 48 * 1024 * 1024
MAX_VISUAL_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STRUCTURED_TEXT_REQUEST_BYTES = 2 * 1024 * 1024


class VisualProviderRuntimeError(ValueError):
    """Sanitized error raised at the generic visual-provider boundary."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def strict_json_loads(raw: bytes | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(
        text,
        object_pairs_hook=unique,
        parse_constant=reject_constant,
    )


def prepare_egress_image(path: Path, mime_type: str) -> tuple[str, bytes, int, int]:
    """Prepare bounded pixels without extracting or synthesizing text."""

    try:
        return _egress_image(path, mime_type)
    except Exception as exc:  # noqa: BLE001 - sanitized shared renderer boundary
        code = getattr(exc, "code", "image_invalid")
        status = getattr(exc, "status", 409)
        raise VisualProviderRuntimeError(
            str(code), "page image cannot be prepared for visual analysis", int(status)
        ) from None


def _endpoint(context: ModelProviderProbeContext) -> tuple[str, str, int, str, str]:
    api_style = context.api_style or (
        "responses" if context.provider_id == "openai" else "chat_completions"
    )
    if api_style not in {"responses", "chat_completions"}:
        raise VisualProviderRuntimeError(
            "provider_endpoint_invalid", "visual provider endpoint is invalid", 409
        )
    parsed = urlsplit(context.base_url)
    endpoint_scope = (
        "loopback"
        if context.base_url_policy == "openai_compatible_loopback_v1"
        else "public_https"
    )
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise VisualProviderRuntimeError(
            "provider_endpoint_invalid", "visual provider endpoint is invalid", 409
        ) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and endpoint_scope != "loopback")
    ):
        raise VisualProviderRuntimeError(
            "provider_endpoint_invalid", "visual provider endpoint is invalid", 409
        )
    suffix = "/responses" if api_style == "responses" else "/chat/completions"
    return (
        api_style,
        parsed.hostname.casefold(),
        port,
        parsed.path.rstrip("/") + suffix,
        endpoint_scope,
    )


def build_structured_visual_request(
    context: ModelProviderProbeContext,
    *,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    pages: Sequence[tuple[str, bytes]],
    max_output_tokens: int = 8000,
) -> VisualProviderRequest:
    """Attach page bytes directly to a strict multimodal request.

    ``prompt`` must contain only caller-approved manifest/rubric data.  No
    recognized text, derived text boxes, or fallback text parameter exists.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise VisualProviderRuntimeError("prompt_invalid", "visual prompt is invalid")
    if not isinstance(schema_name, str) or not schema_name.replace("_", "").isalnum():
        raise VisualProviderRuntimeError(
            "schema_name_invalid", "visual schema name is invalid"
        )
    if not 1 <= len(pages) <= 60:
        raise VisualProviderRuntimeError(
            "page_count_unsupported", "visual page count is unsupported", 409
        )
    if not 256 <= max_output_tokens <= 32000:
        raise VisualProviderRuntimeError(
            "max_output_tokens_invalid", "visual output limit is invalid"
        )
    api_style, host, port, path, endpoint_scope = _endpoint(context)
    if api_style == "responses":
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for mime_type, raw in pages:
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{mime_type};base64,"
                        f"{base64.b64encode(raw).decode('ascii')}"
                    ),
                    "detail": "high",
                }
            )
        body_value: dict[str, Any] = {
            "background": False,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_output_tokens,
            "model": context.model_id,
            "store": False,
            "stream": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                }
            },
            "tool_choice": "none",
            "tools": [],
        }
    else:
        content = [{"type": "text", "text": prompt}]
        for mime_type, raw in pages:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{mime_type};base64,"
                            f"{base64.b64encode(raw).decode('ascii')}"
                        ),
                        "detail": "high",
                    },
                }
            )
        body_value = {
            "max_tokens": max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly one JSON object matching the supplied schema.",
                },
                {"role": "user", "content": content},
            ],
            "model": context.model_id,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            },
            "stream": False,
        }
    body = canonical_json_bytes(body_value)
    if len(body) > MAX_VISUAL_REQUEST_BYTES:
        raise VisualProviderRuntimeError(
            "visual_request_too_large",
            "page images exceed the visual request limit",
            409,
        )
    return VisualProviderRequest(
        provider_id=context.provider_id,
        model_id=context.model_id,
        host=host,
        port=port,
        path=path,
        body=body,
        api_key=context.api_key,
        scheme=urlsplit(context.base_url).scheme,
        api_style=api_style,
        base_url_policy=context.base_url_policy,
        endpoint_scope=endpoint_scope,
    )


def structured_text_output_limit(context: ModelProviderProbeContext) -> int:
    """Bound known V4 reasoning+text budgets without changing other providers.

    Official Responses docs count reasoning inside max_output_tokens. The
    documented V4 maximum is larger; 65,536 is our bounded preparation budget.
    https://api-docs.deepseek.com/quick_start/pricing/
    """
    if urlsplit(
        context.base_url
    ).hostname == "api.deepseek.com" and context.model_id in {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash-vision-exp",
    }:
        return 65536
    return 32000


def build_structured_text_request(
    context: ModelProviderProbeContext,
    *,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    max_output_tokens: int = 16000,
) -> VisualProviderRequest:
    """Build one strict, text-only structured-output request.

    The desktop preparation flow shares the already-audited endpoint and
    pinned-transport boundary with visual intake, but it must not pretend that
    a text brief contains page pixels.  This sibling builder therefore emits
    no image blocks and has a much smaller request-size ceiling.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise VisualProviderRuntimeError(
            "prompt_invalid", "structured text prompt is invalid"
        )
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise VisualProviderRuntimeError(
            "schema_invalid", "structured text schema is invalid"
        )
    if not isinstance(schema_name, str) or not schema_name.replace("_", "").isalnum():
        raise VisualProviderRuntimeError(
            "schema_name_invalid", "structured text schema name is invalid"
        )
    if type(
        max_output_tokens
    ) is not int or not 256 <= max_output_tokens <= structured_text_output_limit(
        context
    ):
        raise VisualProviderRuntimeError(
            "max_output_tokens_invalid", "structured output limit is invalid"
        )
    api_style, host, port, path, endpoint_scope = _endpoint(context)
    if api_style == "responses":
        body_value: dict[str, Any] = {
            "background": False,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": max_output_tokens,
            "model": context.model_id,
            "store": False,
            "stream": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                }
            },
            "tool_choice": "none",
            "tools": [],
        }
    else:
        body_value = {
            "max_tokens": max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object matching the supplied "
                        "schema. Do not wrap it in Markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "model": context.model_id,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            },
            "stream": False,
        }
    body = canonical_json_bytes(body_value)
    if len(body) > MAX_STRUCTURED_TEXT_REQUEST_BYTES:
        raise VisualProviderRuntimeError(
            "structured_request_too_large",
            "structured text request exceeds the request limit",
            409,
        )
    return VisualProviderRequest(
        provider_id=context.provider_id,
        model_id=context.model_id,
        host=host,
        port=port,
        path=path,
        body=body,
        api_key=context.api_key,
        scheme=urlsplit(context.base_url).scheme,
        api_style=api_style,
        base_url_policy=context.base_url_policy,
        endpoint_scope=endpoint_scope,
    )


def parse_structured_visual_response(
    api_style: str, raw: bytes
) -> tuple[Any, dict[str, int] | None]:
    if len(raw) > MAX_VISUAL_RESPONSE_BYTES:
        raise VisualProviderRuntimeError(
            "provider_response_too_large", "visual provider response is too large", 502
        )
    try:
        payload = strict_json_loads(raw)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise VisualProviderRuntimeError(
            "provider_response_invalid", "visual provider response is invalid", 502
        ) from None
    if not isinstance(payload, Mapping):
        raise VisualProviderRuntimeError(
            "provider_response_invalid", "visual provider response is invalid", 502
        )
    text: Any = None
    missing_content = False
    if api_style == "responses":
        if (
            payload.get("status") != "completed"
            or payload.get("error") is not None
            or payload.get("incomplete_details") is not None
        ):
            raise VisualProviderRuntimeError(
                "provider_response_incomplete",
                "visual provider did not complete the response",
                502,
            )
        output = payload.get("output")
        if isinstance(output, list):
            if any(
                isinstance(block, Mapping)
                and isinstance(block.get("content"), list)
                and any(
                    isinstance(item, Mapping)
                    and (
                        item.get("type") == "refusal"
                        or item.get("refusal") not in {None, ""}
                    )
                    for item in block["content"]
                )
                for block in output
            ):
                raise VisualProviderRuntimeError(
                    "provider_response_refused",
                    "visual provider refused the response",
                    502,
                )
            texts = [
                item.get("text")
                for block in output
                if isinstance(block, Mapping) and isinstance(block.get("content"), list)
                for item in block["content"]
                if isinstance(item, Mapping) and item.get("type") == "output_text"
            ]
            valid = [item for item in texts if isinstance(item, str)]
            missing_content = not texts
            if len(valid) == 1:
                text = valid[0]
        if text is None and isinstance(payload.get("output_text"), str):
            text = payload["output_text"]
    elif api_style == "chat_completions":
        choices = payload.get("choices")
        if (
            isinstance(choices, list)
            and len(choices) == 1
            and isinstance(choices[0], Mapping)
        ):
            if choices[0].get("finish_reason") != "stop":
                raise VisualProviderRuntimeError(
                    "provider_response_incomplete",
                    "visual provider did not complete the response",
                    502,
                )
            message = choices[0].get("message")
            if isinstance(message, Mapping) and message.get("refusal") in {None, ""}:
                text = message.get("content")
                missing_content = text is None
            elif isinstance(message, Mapping):
                raise VisualProviderRuntimeError(
                    "provider_response_refused",
                    "visual provider refused the response",
                    502,
                )
    else:
        raise VisualProviderRuntimeError(
            "provider_endpoint_invalid", "visual provider endpoint is invalid", 409
        )
    if (isinstance(text, str) and not text.strip()) or (
        text is None and missing_content
    ):
        raise VisualProviderRuntimeError(
            "provider_response_empty",
            "visual provider completed without readable output content",
            502,
        )
    if not isinstance(text, str):
        raise VisualProviderRuntimeError(
            "provider_response_invalid", "visual provider response is invalid", 502
        )
    try:
        decoded = strict_json_loads(text)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise VisualProviderRuntimeError(
            "provider_output_invalid", "visual provider output is not valid JSON", 502
        ) from None
    usage_value = payload.get("usage")
    usage: dict[str, int] | None = None
    if isinstance(usage_value, Mapping):
        candidate: dict[str, int] = {}
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        ):
            value = usage_value.get(key)
            if type(value) is int and 0 <= value <= 10_000_000:
                candidate[key] = value
        usage = candidate or None
    return decoded, usage


__all__ = [
    "LocalPageRenderer",
    "PageRenderer",
    "PinnedVisualTransport",
    "RenderedPage",
    "VisualProviderRequest",
    "VisualProviderRuntimeError",
    "VisualTransport",
    "build_structured_text_request",
    "build_structured_visual_request",
    "canonical_json_bytes",
    "parse_structured_visual_response",
    "prepare_egress_image",
    "strict_json_loads",
]
