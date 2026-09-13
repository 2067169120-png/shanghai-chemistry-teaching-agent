from __future__ import annotations

import base64
import hashlib
import io
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1 import desktop_preparation_provider as module
from integrations.deeptutor_shchem_v1 import visual_provider_runtime
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    MAX_IMAGES,
    image_info,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import ProbeTransportResponse
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)


def _context(style="responses"):
    return ModelProviderProbeContext(
        profile_id="teacher-visual",
        provider_id="openai_compatible",
        model_id="declared-visual-model",
        base_url_policy="openai_compatible_public_https_v1",
        base_url="https://models.example/v1",
        revision="REVISION-1",
        api_key="fixture-not-a-real-credential",
        provider_kind="openai_compatible",
        api_style=style,
        local_endpoint_policy="deny",
    )


def _asset(index, fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (40 + index, 30 + index), (40 * index, 50, 80)).save(stream, fmt)
    data = stream.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    return {
        "asset_id": "IMG-" + digest,
        "sha256": digest,
        "caption": f"教学图 {index}",
        "source": "合成测试图片",
        "purpose": "对应知识点的观察与讲评",
        **image_info(data),
    }, data


def _selection(count=3):
    formats = ("PNG", "JPEG", "WEBP")
    pairs = [_asset(index, formats[(index - 1) % len(formats)]) for index in range(1, count + 1)]
    payload = {
        "topic": "有机结构",
        "lesson_route": "讲练课",
        "image_input_mode": "vision",
        "image_assets": [asset for asset, _ in pairs],
    }
    # Deliberately reverse mapping insertion order; selected metadata owns order.
    image_data = {asset["asset_id"]: data for asset, data in reversed(pairs)}
    return payload, image_data


class _Transport:
    def __init__(self):
        self.requests = []

    def send(self, request, *, cancel_event, deadline_monotonic):
        assert not cancel_event.is_set()
        assert deadline_monotonic > 0
        self.requests.append(request)
        candidate = {"synthetic_candidate": True}
        if request.api_style == "responses":
            response = {
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output_text": json.dumps(candidate),
            }
        else:
            response = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(candidate), "refusal": None},
                    }
                ]
            }
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=json.dumps(response).encode(),
            latency_ms=1,
            model_invoked=True,
        )


def _content(request):
    body = json.loads(request.body)
    if request.api_style == "responses":
        content = body["input"][0]["content"]
        return body, content[0]["text"], [item["image_url"] for item in content[1:]]
    content = body["messages"][1]["content"]
    if isinstance(content, str):
        return body, content, []
    return body, content[0]["text"], [item["image_url"]["url"] for item in content[1:]]


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("entrypoint", ["generate", "call"])
def test_visual_request_preserves_exact_selected_pixels_and_order(style, entrypoint):
    payload, image_data = _selection()
    frozen_payload, frozen_data = deepcopy(payload), dict(image_data)
    transport = _Transport()
    provider = module.StructuredPreparationProvider(
        _context(style), transport=transport
    )
    invoke = provider.generate if entrypoint == "generate" else provider
    result = invoke(
        payload,
        profile_binding={
            "profile_id": "teacher-visual",
            "profile_revision": "REVISION-1",
        },
        image_data=image_data,
    )
    assert result == {"synthetic_candidate": True}
    assert len(transport.requests) == 1
    body, prompt, urls = _content(transport.requests[0])
    assert len(urls) == 3
    for number, (asset, url) in enumerate(
        zip(payload["image_assets"], urls, strict=True), start=1
    ):
        header, encoded = url.split(",", 1)
        assert header == f"data:{asset['content_type']};base64"
        raw = base64.b64decode(encoded, validate=True)
        assert raw == image_data[asset["asset_id"]]
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"]
        assert (
            f'"attachment_number":{number},"asset_id":"{asset["asset_id"]}"' in prompt
        )
    assert "随本请求附上图片像素" in prompt
    assert "图片转写，待教师核对" in prompt
    assert "uncertainties" in prompt
    assert "答案与解析图仅用于相应题目的后续讲评页" in prompt
    assert "没有看到图片像素" not in prompt
    assert "图中模糊" in prompt
    assert "fixture-not-a-real-credential" not in prompt
    assert "data:image" not in prompt
    assert body["stream"] is False
    if style == "responses":
        assert body["text"]["format"]["strict"] is True
        assert body["store"] is False
        assert body["tools"] == []
    else:
        assert body["response_format"]["json_schema"]["strict"] is True
    assert payload == frozen_payload
    assert image_data == frozen_data


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_visual_request_accepts_twelve_images_without_dropping_pixels(style):
    payload, image_data = _selection(12)
    transport = _Transport()
    provider = module.StructuredPreparationProvider(
        _context(style), transport=transport
    )
    provider.generate(payload, image_data=image_data)
    body, prompt, urls = _content(transport.requests[0])
    assert len(urls) == 12
    for number, (asset, url) in enumerate(
        zip(payload["image_assets"], urls, strict=True), start=1
    ):
        _header, encoded = url.split(",", 1)
        assert base64.b64decode(encoded, validate=True) == image_data[asset["asset_id"]]
        assert f'"attachment_number":{number},"asset_id":"{asset["asset_id"]}"' in prompt
    assert body["stream"] is False


@pytest.mark.parametrize("mode", [None, "local_only"])
@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_legacy_and_local_only_send_metadata_but_no_pixels(mode, style):
    payload, _ = _selection()
    if mode is None:
        payload.pop("image_input_mode")
    else:
        payload["image_input_mode"] = mode
    transport = _Transport()
    module.StructuredPreparationProvider(_context(style), transport=transport).generate(
        payload
    )
    _, prompt, urls = _content(transport.requests[0])
    assert urls == []
    assert "没有看到图片像素" in prompt
    assert "attachment_number" not in prompt
    assert "data:image" not in transport.requests[0].body.decode()


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("empty_data", [None, {}])
def test_explicit_vision_without_assets_uses_text_and_discloses_no_pixels(
    style, empty_data
):
    payload = {"topic": "化学平衡", "image_input_mode": "vision", "image_assets": []}
    transport = _Transport()
    module.StructuredPreparationProvider(_context(style), transport=transport).generate(
        payload, image_data=empty_data
    )
    _, prompt, urls = _content(transport.requests[0])
    assert urls == []
    assert "图片清单为空" in prompt
    assert "没有收到任何图片像素" in prompt
    assert '"image_input_mode":"vision"' in prompt
    assert payload["image_input_mode"] == "vision"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_all",
        "missing_one",
        "extra",
        "wrong_bytes",
        "non_bytes",
        "wrong_mime",
        "wrong_dimensions",
        "duplicate_asset",
        "private_path",
        "bad_mapping",
    ],
)
def test_invalid_visual_pixels_fail_before_transport(fault):
    payload, image_data = _selection()
    first_id = payload["image_assets"][0]["asset_id"]
    if fault == "missing_all":
        image_data = None
    elif fault == "missing_one":
        image_data.pop(first_id)
    elif fault == "extra":
        image_data["IMG-" + "f" * 64] = b"extra"
    elif fault == "wrong_bytes":
        image_data[first_id] = b"wrong pixels"
    elif fault == "non_bytes":
        image_data[first_id] = "not bytes"
    elif fault == "wrong_mime":
        payload["image_assets"][0]["content_type"] = "image/jpeg"
    elif fault == "wrong_dimensions":
        payload["image_assets"][0]["width"] += 1
    elif fault == "duplicate_asset":
        payload["image_assets"].append(deepcopy(payload["image_assets"][0]))
    elif fault == "private_path":
        payload["image_assets"][0]["local_path"] = "C:/private/not-read.png"
    elif fault == "bad_mapping":
        image_data = []
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data
        )
    assert caught.value.retryable is False
    assert transport.requests == []
    assert "C:/private" not in str(caught.value)


@pytest.mark.parametrize("mode", [None, "local_only"])
def test_local_only_rejects_ambiguous_extra_pixel_data(mode):
    payload, image_data = _selection()
    if mode is None:
        payload.pop("image_input_mode")
    else:
        payload["image_input_mode"] = mode
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data
        )
    assert caught.value.code == "preparation_image_data_unexpected"
    assert transport.requests == []


@pytest.mark.parametrize("mode", ["auto", True, None, 1])
def test_invalid_explicit_mode_is_not_silently_downgraded(mode):
    payload, image_data = _selection()
    payload["image_input_mode"] = mode
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data
        )
    assert caught.value.retryable is False
    assert transport.requests == []


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_visual_deepseek_output_budget_is_bounded_without_changing_text_budget(style):
    payload, image_data = _selection()
    context = replace(
        _context(style),
        base_url="https://api.deepseek.com",
        model_id="deepseek-v4-flash-vision-exp",
    )
    transport = _Transport()
    provider = module.StructuredPreparationProvider(context, transport=transport)
    provider.generate(payload, image_data=image_data)
    payload["image_input_mode"] = "local_only"
    provider.generate(payload)
    key = "max_output_tokens" if style == "responses" else "max_tokens"
    assert json.loads(transport.requests[0].body)[key] == 32000
    assert json.loads(transport.requests[1].body)[key] == 65536


def test_visual_request_size_limit_is_checked_before_transport(monkeypatch):
    payload, image_data = _selection()
    monkeypatch.setattr(visual_provider_runtime, "MAX_VISUAL_REQUEST_BYTES", 100)
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data
        )
    assert caught.value.code == "visual_request_too_large"
    assert caught.value.retryable is False
    assert transport.requests == []


@pytest.mark.parametrize("when", ["before_validation", "after_validation"])
def test_visual_cancellation_never_sends_after_cancel(monkeypatch, when):
    payload, image_data = _selection()
    state = {"cancelled": when == "before_validation"}
    original = module.verify_image_bytes

    def verify(asset, data):
        checked = original(asset, data)
        state["cancelled"] = True
        return checked

    if when == "after_validation":
        monkeypatch.setattr(module, "verify_image_bytes", verify)
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data, is_cancelled=lambda: state["cancelled"]
        )
    assert caught.value.code == "preparation_cancelled"
    assert transport.requests == []


def test_visual_stale_binding_is_rejected_before_pixel_loading(monkeypatch):
    payload, image_data = _selection()

    def forbidden(*_args):
        raise AssertionError("stale binding must not load pixels")

    monkeypatch.setattr(module, "verify_image_bytes", forbidden)
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload,
            profile_binding={"profile_id": "teacher-visual", "profile_revision": "OLD"},
            image_data=image_data,
        )
    assert caught.value.code == "preparation_profile_stale"
    assert transport.requests == []


def test_visual_transport_cancellation_is_reported_once_without_retry():
    payload, image_data = _selection()
    state = {"cancelled": False}

    class CancelTransport(_Transport):
        def send(self, request, *, cancel_event, deadline_monotonic):
            self.requests.append(request)
            assert deadline_monotonic > 0
            state["cancelled"] = True
            assert cancel_event.is_set()
            raise visual_provider_runtime.VisualProviderRuntimeError(
                "cancelled", "synthetic cancellation"
            )

    transport = CancelTransport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data, is_cancelled=lambda: state["cancelled"]
        )
    assert caught.value.code == "cancelled"
    assert caught.value.message_zh == "备课生成已取消。"
    assert len(transport.requests) == 1


def test_visual_selection_over_max_images_is_rejected_before_transport():
    payload, image_data = _selection(MAX_IMAGES + 1)
    transport = _Transport()
    with pytest.raises(module.DesktopPreparationProviderError) as caught:
        module.StructuredPreparationProvider(_context(), transport=transport).generate(
            payload, image_data=image_data
        )
    assert f"最多选择{MAX_IMAGES}张" in caught.value.message_zh
    assert transport.requests == []
