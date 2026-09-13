"""Synthetic-only user token budgets: no credentials, network or source pages."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import replace

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import visual_provider_runtime as runtime
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    DesktopPreparationProviderError,
    StructuredPreparationProvider,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)


def _context(style="responses", **overrides):
    context = ModelProviderProbeContext(
        profile_id="synthetic-budget",
        provider_id="openai_compatible",
        model_id="synthetic-model",
        base_url_policy="openai_compatible_public_https_v1",
        base_url="https://models.example/v1",
        revision="synthetic-revision",
        api_key="synthetic-not-a-credential",
        api_style=style,
    )
    return replace(context, **overrides)


def _image(size=(32, 24), fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, (19, 89, 137)).save(stream, format=fmt)
    return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[
        fmt
    ], stream.getvalue()


def _build(context, kind="text", *, prompt="合成预算测试终点。", pages=None, **extra):
    arguments = {
        "prompt": prompt,
        "schema": {"type": "object"},
        "schema_name": "synthetic_budget",
        **extra,
    }
    if kind == "visual":
        return runtime.build_structured_visual_request(
            context, pages=pages if pages is not None else [_image()], **arguments
        )
    return runtime.build_structured_text_request(context, **arguments)


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("kind", ["text", "visual"])
@pytest.mark.parametrize("selected", [1, 128, 65536, 1000000])
def test_user_output_overrides_caller_clamp_in_both_wire_styles(style, kind, selected):
    request = _build(
        _context(style, max_output_tokens=selected), kind, max_output_tokens=32000
    )
    body = json.loads(request.body)
    key = "max_output_tokens" if style == "responses" else "max_tokens"
    assert body[key] == selected
    assert "max_input_tokens" not in body
    assert "synthetic-not-a-credential" not in request.body.decode()


@pytest.mark.parametrize("kind", ["text", "visual"])
def test_user_output_replaces_invalid_or_legacy_caller_default(kind):
    body = json.loads(
        _build(_context(max_output_tokens=64000), kind, max_output_tokens=True).body
    )
    assert body["max_output_tokens"] == 64000


@pytest.mark.parametrize("kind,expected", [("text", 16000), ("visual", 8000)])
def test_none_output_preserves_caller_default_and_old_validation(kind, expected):
    assert json.loads(_build(_context(), kind).body)["max_output_tokens"] == expected
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        _build(_context(), kind, max_output_tokens=65536)
    assert error.value.code == "max_output_tokens_invalid"


def test_output_recommendation_is_not_the_user_ceiling():
    generic = _context()
    known = replace(
        generic, base_url="https://api.deepseek.com", model_id="deepseek-v4-pro"
    )
    assert runtime.structured_text_output_limit(generic) == 32000
    assert runtime.structured_text_output_limit(known) == 65536
    assert (
        runtime.structured_text_output_limit(replace(known, max_output_tokens=12)) == 12
    )
    assert (
        runtime.structured_text_output_limit(replace(generic, max_output_tokens=900000))
        == 900000
    )


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("value", [0, -1, 1000001, True, 256.5, "64000", float("nan")])
@pytest.mark.parametrize("kind", ["text", "visual"])
def test_direct_context_invalid_budget_fails_sanitized(field, value, kind):
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        _build(_context(**{field: value}), kind)
    assert error.value.code == field + "_invalid"
    assert "synthetic-not-a-credential" not in str(error.value)
    assert "合成预算" not in str(error.value)


def test_estimate_uses_utf8_bytes_and_explainable_native_image_tiles():
    prompt = "ASCII汉字🙂"
    pages = [_image((513, 1025))]
    expected = len(prompt.encode("utf-8")) + 1024 + 256 + 1024 * 2 * 3
    assert runtime.estimate_input_tokens(prompt, pages) == expected
    assert (
        runtime.estimate_input_tokens(prompt, []) == len(prompt.encode("utf-8")) + 1024
    )


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_estimate_validates_supported_bytes_without_changing_them(fmt):
    page = _image((20, 20), fmt)
    before = page
    assert runtime.estimate_input_tokens("synthetic", [page]) == 9 + 1024 + 256 + 1024
    assert page == before


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("kind", ["text", "visual"])
def test_exact_user_input_boundary_includes_full_schema_and_preserves_payload(
    style, kind
):
    context = _context(style)
    prompt = '完整输入终点"\\。'
    pages = [_image()] if kind == "visual" else []
    metadata = runtime.canonical_json_bytes(
        {
            "schema": {"type": "object"},
            "schema_name": "synthetic_budget",
            "model": context.model_id,
        }
    ).decode("utf-8")
    estimate = runtime.estimate_input_tokens(prompt + metadata, pages)
    context = replace(context, max_input_tokens=estimate)
    body = json.loads(_build(context, kind, prompt=prompt, pages=pages).body)
    assert "max_input_tokens" not in body
    if style == "responses":
        content = body["input"][0]["content"]
        assert content[0]["text"] == prompt
        urls = [item["image_url"] for item in content if item["type"] == "input_image"]
    else:
        content = body["messages"][1]["content"]
        assert content[0]["text"] == prompt if kind == "visual" else content == prompt
        urls = [
            item["image_url"]["url"]
            for item in content
            if isinstance(item, dict) and item["type"] == "image_url"
        ]
    assert len(urls) == len(pages)
    for url, (mime, raw) in zip(urls, pages, strict=True):
        assert url.startswith(f"data:{mime};base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == raw
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        _build(
            replace(context, max_input_tokens=estimate - 1),
            kind,
            prompt=prompt,
            pages=pages,
        )
    assert error.value.code == "input_budget_exceeded"
    assert "未截断" in str(error.value)


def test_over_budget_stops_before_image_encoding_or_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime.base64, "b64encode", lambda _: pytest.fail("must not encode")
    )
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        request = _build(_context(max_input_tokens=1), "visual")
        calls.append(request)  # the caller cannot send a request that was not built
    assert error.value.code == "input_budget_exceeded"
    assert calls == []


def test_preparation_caller_does_not_reach_transport_when_input_budget_fails():
    class ForbiddenTransport:
        def send(self, *args, **kwargs):
            pytest.fail("input budget must reject before transport")

    provider = StructuredPreparationProvider(
        _context(max_input_tokens=1), transport=ForbiddenTransport()
    )
    with pytest.raises(DesktopPreparationProviderError) as error:
        provider.generate({"topic": "synthetic local budget"})
    assert error.value.code == "input_budget_exceeded"


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("selected", [128, 64000])
def test_actual_preparation_visual_caller_clamp_cannot_change_user_output(
    style, selected
):
    class CaptureTransport:
        def __init__(self):
            self.requests = []

        def send(self, request, **kwargs):
            self.requests.append(request)
            payload = (
                {"status": "completed", "output_text": "{}"}
                if style == "responses"
                else {
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]
                }
            )
            return ProbeTransportResponse(
                http_status=200,
                content_type="application/json",
                content_encoding=None,
                body=json.dumps(payload).encode(),
                latency_ms=1,
                model_invoked=True,
            )

    mime, raw = _image()
    digest = hashlib.sha256(raw).hexdigest()
    asset = {
        "asset_id": "IMG-" + digest,
        "sha256": digest,
        "caption": "synthetic fixture",
        "source": "generated in memory",
        "purpose": "request budget test",
        "content_type": mime,
        "width": 32,
        "height": 24,
    }
    transport = CaptureTransport()
    provider = StructuredPreparationProvider(
        _context(style, max_output_tokens=selected), transport=transport
    )
    assert (
        provider.generate(
            {
                "topic": "synthetic",
                "image_input_mode": "vision",
                "image_assets": [asset],
            },
            image_data={asset["asset_id"]: raw},
        )
        == {}
    )
    assert len(transport.requests) == 1
    body = json.loads(transport.requests[0].body)
    assert (
        body["max_output_tokens" if style == "responses" else "max_tokens"] == selected
    )


@pytest.mark.parametrize("kind", ["text", "visual"])
def test_none_input_does_not_retroactively_block_long_material(kind):
    prompt = "合成" * 40000 + "完整终点"
    request = _build(_context(), kind, prompt=prompt)
    assert prompt in request.body.decode("utf-8")
    assert runtime.estimate_input_tokens(prompt, []) > 64000


@pytest.mark.parametrize(
    "page",
    [
        ("image/png", b"not-an-image"),
        ("image/jpeg", _image()[1]),
        ("image/png", _image()[1][:-20]),
        ("image/png", b""),
        ("image/png", bytearray(b"bad")),
        ("text/plain", b"private-decoder-details"),
        ("image/png",),
    ],
)
def test_estimate_rejects_bad_image_bytes_or_identity_without_echo(page):
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.estimate_input_tokens("synthetic", [page])
    assert error.value.code == "image_data_invalid"
    assert "private-decoder-details" not in str(error.value)


def test_animated_image_is_not_estimated_as_a_single_static_frame():
    stream = io.BytesIO()
    one = Image.new("RGB", (20, 20), "red")
    two = Image.new("RGB", (20, 20), "blue")
    one.save(stream, format="WEBP", save_all=True, append_images=[two], duration=100)
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.estimate_input_tokens("synthetic", [("image/webp", stream.getvalue())])
    assert error.value.code == "image_data_invalid"


@pytest.mark.parametrize(
    "limit_name,limit,pages",
    [
        ("MAX_INPUT_IMAGE_PIXELS", 99, [_image((10, 10))]),
        ("MAX_TOTAL_INPUT_IMAGE_PIXELS", 199, [_image((10, 10)), _image((10, 10))]),
    ],
)
def test_image_resource_bounds_are_checked_before_overlimit_decode(
    monkeypatch, limit_name, limit, pages
):
    monkeypatch.setattr(runtime, limit_name, limit)
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.estimate_input_tokens("synthetic", pages)
    assert error.value.code == "image_resource_limit"


def test_request_byte_safety_remains_even_with_maximum_user_tokens(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_STRUCTURED_TEXT_REQUEST_BYTES", 128)
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        _build(
            _context(max_input_tokens=1000000, max_output_tokens=1000000),
            prompt="a" * 129,
        )
    assert error.value.code == "structured_request_too_large"


def test_inline_byte_safety_precedes_decoder_and_base64(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_VISUAL_REQUEST_BYTES", 64)
    monkeypatch.setattr(
        runtime.base64, "b64encode", lambda _: pytest.fail("must not encode")
    )
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        _build(
            _context(max_input_tokens=1000000),
            "visual",
            pages=[("image/png", b"x" * 49)],
        )
    assert error.value.code == "visual_request_too_large"


def test_input_estimate_rejects_nonfinite_types_and_excess_page_count():
    with pytest.raises(runtime.VisualProviderRuntimeError, match="prompt"):
        runtime.estimate_input_tokens(float("nan"), [])
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.estimate_input_tokens("synthetic", [_image()] * 61)
    assert error.value.code == "page_count_unsupported"
