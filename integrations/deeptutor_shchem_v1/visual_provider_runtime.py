from __future__ import annotations

"""Shared, image-native provider runtime.

This module deliberately has no text-recognition dependency.  Callers provide
trusted manifest text and page pixels; the pixels are attached directly to a
multimodal request.  The paper-intake and private student-analysis domains keep
their own schemas and persistence models.
"""

import base64
import io
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
from .model_provider_settings import MAX_MODEL_TOKEN_LIMIT, ModelProviderProbeContext

MAX_VISUAL_REQUEST_BYTES = 48 * 1024 * 1024
MAX_VISUAL_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STRUCTURED_TEXT_REQUEST_BYTES = 2 * 1024 * 1024
MAX_USER_TOKEN_LIMIT = MAX_MODEL_TOKEN_LIMIT
MAX_INPUT_IMAGE_PIXELS = 64_000_000
MAX_TOTAL_INPUT_IMAGE_PIXELS = 320_000_000
INPUT_TOKEN_FRAMING_RESERVE = 1024
INPUT_IMAGE_TILE_SIDE = 512
INPUT_IMAGE_BASE_TOKENS = 256
INPUT_IMAGE_TILE_TOKENS = 1024


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


def inline_image_payload_size(sizes: Sequence[int]) -> int:
    """Check the base64 portion before allocating a potentially huge request.

    This is a lower bound for the full JSON body, not a replacement for the
    final exact wire-size check including the prompt, schema and image headers.
    """
    if any(type(size) is not int or size < 0 for size in sizes):
        raise VisualProviderRuntimeError("image_size_invalid", "image size is invalid")
    encoded_bytes = sum(4 * ((size + 2) // 3) for size in sizes)
    if encoded_bytes > MAX_VISUAL_REQUEST_BYTES:
        raise VisualProviderRuntimeError(
            "visual_request_too_large",
            "page images exceed the visual request limit",
            409,
        )
    return encoded_bytes


def _optional_token_limit(context: ModelProviderProbeContext, field: str) -> int | None:
    value = getattr(context, field, None)
    if value is not None and (
        type(value) is not int or not 1 <= value <= MAX_USER_TOKEN_LIMIT
    ):
        raise VisualProviderRuntimeError(
            f"{field}_invalid", "provider token limit is invalid"
        )
    return value


def _effective_output_limit(
    context: ModelProviderProbeContext, caller_limit: int, legacy_maximum: int
) -> int:
    selected = _optional_token_limit(context, "max_output_tokens")
    if selected is not None:
        return selected
    if type(caller_limit) is not int or not 256 <= caller_limit <= legacy_maximum:
        raise VisualProviderRuntimeError(
            "max_output_tokens_invalid", "structured output limit is invalid"
        )
    return caller_limit


def estimate_input_tokens(prompt: str, pages: Sequence[tuple[str, bytes]]) -> int:
    """Return a local conservative planning estimate, never provider usage.

    Text contributes one token per UTF-8 byte (a deliberately generous byte
    bound, not a tokenizer), plus 1,024 reserved framing tokens. Each verified
    static image contributes 256 + 1,024 * ceil(width/512) * ceil(height/512).
    Native dimensions are used without vendor downsampling. Image accounting
    is a conservative software heuristic, not a universal upper bound on
    vendor-specific tokenization. Builders additionally include schema/model
    text. No network, paths, OCR, resizing, truncation or credential access.

    The image bytes, format and dimensions are verified before they contribute
    an estimate; decoding is sequential and bounded. Empty pages is valid for
    the structured-text sibling. Nothing is sent by this function.
    """
    from PIL import Image

    if not isinstance(prompt, str):
        raise VisualProviderRuntimeError("prompt_invalid", "input prompt is invalid")
    try:
        text_size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError:
        raise VisualProviderRuntimeError(
            "prompt_invalid", "input prompt is invalid"
        ) from None
    if text_size > MAX_VISUAL_REQUEST_BYTES:
        raise VisualProviderRuntimeError(
            "visual_request_too_large", "input exceeds the request limit", 409
        )
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        raise VisualProviderRuntimeError("image_data_invalid", "image data is invalid")
    if len(pages) > 60:
        raise VisualProviderRuntimeError(
            "page_count_unsupported", "visual page count is unsupported", 409
        )
    if any(
        not isinstance(page, (tuple, list))
        or len(page) != 2
        or not isinstance(page[0], str)
        or not isinstance(page[1], bytes)
        or not page[1]
        for page in pages
    ):
        raise VisualProviderRuntimeError("image_data_invalid", "image data is invalid")
    inline_image_payload_size([len(raw) for _mime, raw in pages])
    estimate = text_size + INPUT_TOKEN_FRAMING_RESERVE
    total_pixels = 0
    mime_types = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    for mime, raw in pages:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                width, height = source.size
                if (
                    mime_types.get(source.format) != mime
                    or getattr(source, "n_frames", 1) != 1
                    or width < 1
                    or height < 1
                ):
                    raise VisualProviderRuntimeError(
                        "image_data_invalid", "image data is invalid"
                    )
                total_pixels += width * height
                if (
                    width * height > MAX_INPUT_IMAGE_PIXELS
                    or total_pixels > MAX_TOTAL_INPUT_IMAGE_PIXELS
                ):
                    raise VisualProviderRuntimeError(
                        "image_resource_limit",
                        "image decoding exceeds the resource limit",
                        409,
                    )
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
        except VisualProviderRuntimeError:
            raise
        except Exception:  # noqa: BLE001 - no decoder text or source bytes may escape
            raise VisualProviderRuntimeError(
                "image_data_invalid", "image data is invalid"
            ) from None
        tiles = ((width + INPUT_IMAGE_TILE_SIDE - 1) // INPUT_IMAGE_TILE_SIDE) * (
            (height + INPUT_IMAGE_TILE_SIDE - 1) // INPUT_IMAGE_TILE_SIDE
        )
        estimate += INPUT_IMAGE_BASE_TOKENS + INPUT_IMAGE_TILE_TOKENS * tiles
    return estimate


def _check_input_budget(
    context: ModelProviderProbeContext,
    *,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    pages: Sequence[tuple[str, bytes]],
) -> None:
    selected = _optional_token_limit(context, "max_input_tokens")
    if selected is None:
        # Existing profiles did not impose a token estimate. Do not apply a
        # new default to a previously accepted long lesson or source bundle.
        return
    try:
        auxiliary_text = canonical_json_bytes(
            {
                "schema": dict(schema),
                "schema_name": schema_name,
                "model": context.model_id,
            }
        ).decode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise VisualProviderRuntimeError(
            "schema_invalid", "structured schema is invalid"
        ) from None
    if estimate_input_tokens(prompt + auxiliary_text, pages) > selected:
        raise VisualProviderRuntimeError(
            "input_budget_exceeded",
            "本次完整输入的本机估算 token 超过自定上限；未截断文字或图片，未发送请求。",
            409,
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

    ``prompt`` contains caller-approved instructions and source manifest data.
    A crop-review call may also include prior visual-model observations as
    explicitly untrusted context to compare against the attached real pixels.
    No OCR/document-text fallback parameter replaces the images.
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
    max_output_tokens = _effective_output_limit(context, max_output_tokens, 32000)
    if any(not isinstance(raw, bytes) for _mime, raw in pages):
        raise VisualProviderRuntimeError("image_data_invalid", "image data is invalid")
    inline_image_payload_size([len(raw) for _mime, raw in pages])
    _check_input_budget(
        context, prompt=prompt, schema=schema, schema_name=schema_name, pages=pages
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
    """Return the user's output choice, otherwise the legacy recommendation.

    Official Responses docs count reasoning inside max_output_tokens. The
    documented V4 maximum is larger; 65,536 is our default preparation budget.
    Recommendations do not constrain an explicit user setting.
    https://api-docs.deepseek.com/quick_start/pricing/
    """
    selected = _optional_token_limit(context, "max_output_tokens")
    if selected is not None:
        return selected
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
    max_output_tokens = _effective_output_limit(
        context, max_output_tokens, structured_text_output_limit(context)
    )
    _check_input_budget(
        context, prompt=prompt, schema=schema, schema_name=schema_name, pages=()
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


def structured_response_summary(api_style: str, raw: bytes) -> dict[str, Any]:
    """Closed, bounded diagnostic fields; never provider text or reasoning."""
    if api_style not in {"responses", "chat_completions"}:
        return {}
    if not isinstance(raw, bytes) or len(raw) > MAX_VISUAL_RESPONSE_BYTES:
        return {}
    try:
        payload = strict_json_loads(raw)
    except (ValueError, TypeError, RecursionError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    summary: dict[str, Any] = {}

    def enum_value(value: Any, allowed: set[str]) -> str:
        return (
            value
            if isinstance(value, str) and value in allowed
            else "missing_or_unknown"
        )

    if api_style == "responses":
        summary["status"] = enum_value(
            payload.get("status"),
            {"completed", "incomplete", "failed", "in_progress", "queued", "cancelled"},
        )
        details = payload.get("incomplete_details")
        summary["incomplete_reason"] = enum_value(
            details.get("reason") if isinstance(details, Mapping) else None,
            {"max_output_tokens", "content_filter"},
        )
    else:
        choices = payload.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list)
            and len(choices) == 1
            and isinstance(choices[0], Mapping)
            else {}
        )
        summary["finish_reason"] = enum_value(
            choice.get("finish_reason"),
            {"stop", "length", "content_filter", "tool_calls", "function_call"},
        )
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        summary["usage"] = {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
            if type(usage.get(k)) is int and 0 <= usage[k] <= 10_000_000
        }
        detail_key = (
            "output_tokens_details"
            if api_style == "responses"
            else "completion_tokens_details"
        )
        details = usage.get(detail_key)
        count = (
            details.get("reasoning_tokens") if isinstance(details, Mapping) else None
        )
        if type(count) is int and 0 <= count <= 10_000_000:
            summary["reasoning_tokens"] = count
    return summary


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
    "estimate_input_tokens",
    "inline_image_payload_size",
    "parse_structured_visual_response",
    "prepare_egress_image",
    "strict_json_loads",
    "structured_response_summary",
    "structured_text_output_limit",
]
