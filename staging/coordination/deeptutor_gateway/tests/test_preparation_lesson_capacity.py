from copy import deepcopy
from dataclasses import replace

import pytest
from test_desktop_preparation_facade import _payload
from test_preparation_image_input import image_facade
from test_preparation_visual_provider import _asset, _context, _Transport

from integrations.deeptutor_shchem_v1 import visual_provider_runtime as runtime
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_image_input import (
    PreparationImageInputError,
    validate_preparation_image_dimensions,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    DesktopPreparationProviderError,
    StructuredPreparationProvider,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import MAX_MATERIALS


@pytest.fixture
def desktop_paths(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(workspace, state_root=tmp_path / "personal-state")


def _policy(**overrides):
    return {
        "base_url": "https://api.deepseek.com/v1",
        "model_id": "deepseek-v4-flash-vision-exp",
        **overrides,
    }


@pytest.mark.parametrize(
    "count,side,allowed", [(14, 8192, True), (14, 8193, False),
                           (15, 4096, True), (15, 4097, False),
                           (28, 4096, True), (48, 4097, False)]
)
def test_official_vision_dimensions_depend_on_complete_image_count(count, side, allowed):
    assets = [{"width": side, "height": 1} for _ in range(count)]
    before = deepcopy(assets)
    if allowed:
        validate_preparation_image_dimensions(_policy(), assets)
    else:
        with pytest.raises(PreparationImageInputError) as error:
            validate_preparation_image_dimensions(_policy(), assets)
        assert error.value.code == "preparation_image_dimensions_unsupported"
        assert "未自动缩图或拆成多次调用" in str(error.value)
    assert assets == before


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_official_text_model_cannot_be_promoted_to_vision_by_declaration(model):
    with pytest.raises(PreparationImageInputError, match="仅处理文字"):
        validate_preparation_image_dimensions(_policy(model_id=model), [{"width": 1, "height": 1}])
    validate_preparation_image_dimensions(_policy(model_id=model), [])


@pytest.mark.parametrize("base", ["https://models.example/v1", "http://127.0.0.1:8080/v1",
                                    "https://api.deepseek.com.example/v1", "not-a-url", None])
def test_other_endpoints_are_not_assigned_vendor_dimensions(base):
    validate_preparation_image_dimensions(_policy(base_url=base), [{"width": 10000, "height": 1}] * 28)


def test_inline_payload_budget_uses_per_file_base64_rounding():
    assert runtime.inline_image_payload_size([1, 2, 3, 4]) == 20
    assert runtime.inline_image_payload_size([]) == 0


@pytest.mark.parametrize("size", [-1, True, 1.5, "12", None])
def test_inline_payload_budget_rejects_invalid_lengths(size):
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.inline_image_payload_size([size])
    assert error.value.code == "image_size_invalid"


def test_oversized_visual_request_is_rejected_before_base64_allocation(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_VISUAL_REQUEST_BYTES", 128)
    monkeypatch.setattr(runtime.base64, "b64encode", lambda _: pytest.fail("must not encode"))
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.build_structured_visual_request(_context(), prompt="bounded", schema={},
            schema_name="test_request", pages=[("image/png", b"x" * 97)])
    assert error.value.code == "visual_request_too_large"


def test_final_wire_budget_still_includes_text_and_schema(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_VISUAL_REQUEST_BYTES", 512)
    assert runtime.inline_image_payload_size([3]) == 4
    with pytest.raises(runtime.VisualProviderRuntimeError) as error:
        runtime.build_structured_visual_request(_context(), prompt="full prompt" * 150,
            schema={}, schema_name="test_request", pages=[("image/png", b"abc")])
    assert error.value.code == "visual_request_too_large"


@pytest.mark.parametrize("api_style", ["responses", "chat_completions"])
def test_provider_rechecks_known_text_only_model_without_transport(api_style):
    asset, pixels = _asset(1)
    transport = _Transport()
    context = replace(_context(api_style), base_url="https://api.deepseek.com/v1", model_id="deepseek-v4-pro")
    provider = StructuredPreparationProvider(context, transport=transport)
    with pytest.raises(DesktopPreparationProviderError) as error:
        provider.generate({"topic": "测试", "image_input_mode": "vision", "image_assets": [asset]},
                          profile_binding={"profile_id": context.profile_id, "profile_revision": context.revision},
                          image_data={asset["asset_id"]: pixels})
    assert error.value.code == "vision_capability_unconfirmed"
    assert transport.requests == []


def test_40000_char_materials_survive_draft_and_frozen_request(desktop_paths, tmp_path):
    facade, manager, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    assert MAX_MATERIALS == 40000
    payload["materials"] = "原" * (MAX_MATERIALS - 7) + "完整资料终点。"
    normalized = normalize_preparation_payload(payload)
    assert normalized["materials"] == payload["materials"]
    receipt = facade.create_preparation_draft(payload)
    option = next(r for r in facade.preparation_draft_options() if r["draft_id"] == receipt.draft_id)
    loaded = facade.load_preparation_draft(receipt.draft_id, option["revision"])["payload"]
    assert loaded == payload
    task = facade.prepare_preparation(loaded, "teacher-text", store.revision)
    assert manager.egress_snapshot(task.task_id)["image_assets"] == payload["image_assets"]
    # Loading a draft and preparing a frozen task never borrows a credential.
    assert store.borrow_calls == [] and transport.calls == 0


def test_excess_materials_rejected_without_truncation_and_other_fields_keep_limit():
    payload = {**_payload(), "materials": "原" * 40001}
    before = deepcopy(payload)
    with pytest.raises(DesktopPreparationError, match="40000"):
        normalize_preparation_payload(payload)
    assert payload == before
    with pytest.raises(DesktopPreparationError, match="20000"):
        normalize_preparation_payload({**_payload(), "topic": "题" * 20001})


def test_preview_rejects_vendor_dimensions_before_creating_task_or_borrow(desktop_paths, tmp_path):
    facade, manager, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    store.policy.update(_policy())
    payload["image_assets"][0]["width"] = 8193
    with pytest.raises(DesktopFacadeError) as error:
        facade.prepare_preparation(payload, "teacher-text", store.revision)
    assert error.value.code == "preparation_image_dimensions_unsupported"
    assert manager.list_tasks() == ()
    assert store.borrow_calls == [] and transport.calls == 0


@pytest.mark.parametrize("history", [False, True])
def test_preview_and_history_reject_encoded_budget_without_network(desktop_paths, tmp_path, monkeypatch, history):
    facade, manager, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    task = facade.prepare_preparation(payload, "teacher-text", store.revision) if history else None
    before = manager.list_tasks()
    monkeypatch.setattr(runtime, "MAX_VISUAL_REQUEST_BYTES", 4)
    with pytest.raises(DesktopFacadeError) as error:
        if history:
            facade.preparation_task_egress_preview(task.task_id)
        else:
            facade.prepare_preparation(payload, "teacher-text", store.revision)
    assert error.value.code == "visual_request_too_large"
    assert manager.list_tasks() == before
    assert store.borrow_calls == [] and transport.calls == 0


def test_local_only_has_no_egress_byte_limit(desktop_paths, tmp_path, monkeypatch):
    facade, _, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    monkeypatch.setattr(runtime, "MAX_VISUAL_REQUEST_BYTES", 4)
    payload["image_input_mode"] = "local_only"
    result = facade.preparation_egress_preview(payload, "teacher-text", store.revision)
    assert result["image_count"] == 0
    assert store.borrow_calls == [] and transport.calls == 0
