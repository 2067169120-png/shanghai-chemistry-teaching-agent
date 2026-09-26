from __future__ import annotations

import base64
import json
from copy import deepcopy

import pytest
from PIL import Image
from test_desktop_preparation import FileRenderer, _raw_candidate
from test_desktop_preparation_facade import (
    _facade,
    _payload,
    _ProviderStore,
    _Transport,
)

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    DesktopPreparationManager,
    _invoke_provider,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_image_input import (
    PreparationImageInputError,
    require_preparation_vision_policy,
)


@pytest.fixture
def desktop_paths(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )


class VisionStore(_ProviderStore):
    def __init__(self):
        super().__init__(
            capabilities=("text", "structured_output", "vision"),
            allowed_data_classes=("question_text_redacted", "source_page_image"),
        )
        self.policy = {
            "capability_evidence": {
                "declared": ["vision"],
                "catalog": [],
                "probed": [],
            },
            "effective_capabilities": ["text", "structured_output", "vision"],
            "allowed_data_classes": ["question_text_redacted", "source_page_image"],
            "image_egress": "teacher_confirmed_visual_pages",
        }
        self.policy_calls = 0

    def invocation_policy(self, profile_id, *, expected_revision):
        assert profile_id == "teacher-text" and expected_revision == self.revision
        self.policy_calls += 1
        return deepcopy(self.policy)


class ImageRenderer(FileRenderer):
    def render(self, candidate, *, image_data=None, **kwargs):
        self.received_images = image_data
        return super().render(candidate, **kwargs)


def image_facade(desktop_paths, tmp_path, *, renderer=None):
    store, transport = VisionStore(), _Transport()
    manager = DesktopPreparationManager(
        desktop_paths.task_root / "preparation-v1", renderer or ImageRenderer()
    )
    facade = _facade(desktop_paths, store, transport, manager=manager)
    source = tmp_path / "never-send-local-filename.png"
    Image.new("RGB", (80, 40), "#124578").save(source)
    asset = facade.import_preparation_image(
        str(source), "原任务实验图", "教师自备", "读取标签"
    )
    payload = {
        **_payload(output_kind="ppt"),
        "image_assets": [asset],
        "image_input_mode": "vision",
    }
    return facade, manager, store, transport, payload, source.read_bytes()


@pytest.mark.parametrize("mode", [None, "auto", "VISION", True, 1, {}, []])
def test_invalid_mode_never_downgrades(mode):
    with pytest.raises(DesktopPreparationError) as error:
        normalize_preparation_payload({**_payload(), "image_input_mode": mode})
    assert error.value.code == "preparation_image_mode_invalid"


def test_old_payload_and_explicit_default_have_identical_normalization():
    old = normalize_preparation_payload(_payload())
    assert old == normalize_preparation_payload(
        {**_payload(), "image_input_mode": "local_only"}
    )
    assert "image_input_mode" not in old
    assert (
        normalize_preparation_payload({**_payload(), "image_input_mode": "vision"})[
            "image_input_mode"
        ]
        == "vision"
    )


@pytest.mark.parametrize(
    "case",
    [
        "evidence",
        "probed_only",
        "ineffective",
        "structured",
        "class",
        "egress",
        "malformed",
    ],
)
def test_vision_policy_is_explicit_and_closed(case):
    policy = VisionStore().policy
    if case == "evidence":
        policy["capability_evidence"] = None
    elif case == "probed_only":
        policy["capability_evidence"] = {
            "declared": [],
            "catalog": [],
            "probed": ["vision"],
        }
    elif case == "ineffective":
        policy["effective_capabilities"].remove("vision")
    elif case == "structured":
        policy["effective_capabilities"].remove("structured_output")
    elif case == "class":
        policy["allowed_data_classes"] = ["question_text_redacted"]
    elif case == "egress":
        policy["image_egress"] = "deny"
    else:
        policy["capability_evidence"] = {"declared": "vision"}
    with pytest.raises(PreparationImageInputError):
        require_preparation_vision_policy(policy)


def test_catalog_can_declare_vision_without_a_synthetic_vision_probe():
    policy = VisionStore().policy
    policy["capability_evidence"] = {
        "declared": [],
        "catalog": ["vision"],
        "probed": [],
    }
    require_preparation_vision_policy(policy)


def test_invoke_keeps_pixels_out_of_local_provider_and_never_falls_back():
    calls = []

    def legacy(payload):
        calls.append(payload)
        return _raw_candidate()

    payload = {**_payload(), "image_assets": [{"asset_id": "fixture"}]}
    _invoke_provider(
        legacy,
        payload,
        {},
        lambda x: None,
        lambda: False,
        image_data={"fixture": b"pixels"},
    )
    assert len(calls) == 1 and "image_data" not in calls[0]
    payload["image_input_mode"] = "vision"
    with pytest.raises(DesktopPreparationError, match="签名不兼容"):
        _invoke_provider(
            legacy,
            payload,
            {},
            lambda x: None,
            lambda: False,
            image_data={"fixture": b"pixels"},
        )
    assert len(calls) == 1


def test_invoke_passes_complete_bytes_only_in_separate_keyword():
    seen = []

    def vision(payload, *, image_data):
        seen.append((payload, image_data))

    payload = {
        **_payload(),
        "image_input_mode": "vision",
        "image_assets": [{"asset_id": "fixture"}],
    }
    _invoke_provider(
        vision,
        payload,
        {},
        lambda x: None,
        lambda: False,
        image_data={"fixture": b"pixels"},
    )
    assert seen == [(payload, {"fixture": b"pixels"})]
    assert "pixels" not in json.dumps(seen[0][0])


def test_mode_changes_identity_and_draft_roundtrip(desktop_paths, tmp_path):
    facade, manager, store, transport, payload, _ = image_facade(
        desktop_paths, tmp_path
    )
    vision = facade.prepare_preparation(payload, "teacher-text", store.revision)
    local = facade.prepare_preparation(
        {**payload, "image_input_mode": "local_only"}, "teacher-text", store.revision
    )
    assert vision.task_id != local.task_id
    assert manager.egress_snapshot(vision.task_id)["image_input_mode"] == "vision"
    receipt = facade.create_preparation_draft(payload)
    option = next(
        row
        for row in facade.preparation_draft_options()
        if row["draft_id"] == receipt.draft_id
    )
    assert (
        facade.load_preparation_draft(receipt.draft_id, option["revision"])["payload"]
        == payload
    )
    assert store.borrow_calls == [] and transport.calls == 0


def test_preflight_is_read_only_and_history_uses_frozen_scope(desktop_paths, tmp_path):
    facade, manager, store, transport, payload, _ = image_facade(
        desktop_paths, tmp_path
    )
    preview = facade.preparation_egress_preview(payload, "teacher-text", store.revision)
    assert manager.list_tasks() == ()
    assert (
        preview["image_count"] == 1
        and preview["images"][0]["caption"] == "原任务实验图"
    )
    assert "teacher-model" in preview["model_label"]
    assert "models.example" in preview["confirmation_text"]
    assert "元数据" in preview["confirmation_text"]
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    payload["image_input_mode"] = "local_only"
    payload["image_assets"] = []
    frozen = facade.preparation_task_egress_preview(task.task_id)
    assert frozen["image_count"] == 1 and frozen["image_input_mode"] == "vision"
    assert "原任务实验图" in frozen["confirmation_text"]
    assert store.borrow_calls == [] and transport.calls == 0


def test_no_vision_preflight_stops_before_task_secret_or_network(
    desktop_paths, tmp_path
):
    facade, manager, store, transport, payload, _ = image_facade(
        desktop_paths, tmp_path
    )
    store.policy["capability_evidence"]["declared"] = []
    with pytest.raises(DesktopFacadeError) as error:
        facade.prepare_preparation(payload, "teacher-text", store.revision)
    assert error.value.code == "vision_capability_unconfirmed"
    assert manager.list_tasks() == ()
    assert store.borrow_calls == [] and transport.calls == 0


def test_zero_images_vision_uses_no_vision_policy(desktop_paths, tmp_path):
    facade, _, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    payload["image_assets"] = []
    store.policy["capability_evidence"]["declared"] = []
    preview = facade.preparation_egress_preview(payload, "teacher-text", store.revision)
    assert preview["image_input_mode"] == "vision" and preview["image_count"] == 0
    assert store.policy_calls == 0 and store.borrow_calls == [] and transport.calls == 0


def test_changed_policy_is_rechecked_before_borrow(desktop_paths, tmp_path):
    facade, _, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    store.policy["image_egress"] = "deny"
    result = facade.generate_preparation(task.task_id, teacher_confirmed=True)
    assert result.status == "failed" and "未允许" in result.message_zh
    assert store.borrow_calls == [] and transport.calls == 0


def test_confirmed_vision_reaches_transport_and_receipt_without_paths(
    desktop_paths, tmp_path
):
    facade, manager, store, transport, payload, pixels = image_facade(
        desktop_paths, tmp_path
    )
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    with pytest.raises(DesktopFacadeError):
        facade.generate_preparation(task.task_id, teacher_confirmed=False)
    assert store.borrow_calls == []
    result = facade.generate_preparation(task.task_id, teacher_confirmed=True)
    assert result.status == "completed", result.message_zh
    body = json.loads(transport.request_bodies[0])
    content = body["input"][0]["content"]
    image = next(item for item in content if item["type"] == "input_image")
    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == pixels
    assert "never-send-local-filename" not in transport.request_bodies[0].decode()
    receipt = manager.get_task(task.task_id)["last_egress_confirmation"]
    assert receipt["images"] == [
        {key: payload["image_assets"][0][key] for key in ("asset_id", "sha256")}
    ]
    assert "data:image" not in json.dumps(manager.get_task(task.task_id))


def test_seed_retry_discloses_local_operation_without_provider_policy(
    desktop_paths, tmp_path
):
    renderer = ImageRenderer(fail_calls=1)
    facade, manager, store, transport, payload, _ = image_facade(
        desktop_paths, tmp_path, renderer=renderer
    )
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    assert (
        facade.generate_preparation(task.task_id, teacher_confirmed=True).status
        == "failed"
    )
    assert manager.get_task(task.task_id)["candidate_available"] is True
    store.revision = "PROFILE-REMOVED-OR-CHANGED"
    count = store.policy_calls
    preview = facade.preparation_task_egress_preview(task.task_id)
    assert preview["local_only_operation"] is True and preview["image_count"] == 0
    assert "不调用模型" in preview["confirmation_text"] and store.policy_calls == count
    facade.retry_preparation(task.task_id)
    assert (
        facade.generate_preparation(task.task_id, teacher_confirmed=True).status
        == "completed"
    )
    assert len(store.borrow_calls) == 1 and transport.calls == 1


def test_local_mode_remains_no_pixel_egress_end_to_end(desktop_paths, tmp_path):
    facade, _, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    payload["image_input_mode"] = "local_only"
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    assert (
        facade.generate_preparation(task.task_id, teacher_confirmed=True).status
        == "completed"
    )
    body = transport.request_bodies[0].decode()
    assert "data:image/" not in body and "原任务实验图" in body
    assert store.policy_calls == 0


@pytest.mark.parametrize("historical", [False, True])
def test_cold_preview_never_initializes_or_recovers_manager(
    desktop_paths, tmp_path, monkeypatch, historical
):
    facade, _, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    facade._preparation_manager = None

    def forbidden():
        raise AssertionError("read-only preview cannot initialize/recover tasks")

    monkeypatch.setattr(facade, "_preparation_manager_instance", forbidden)
    with pytest.raises(DesktopFacadeError) as error:
        if historical:
            facade.preparation_task_egress_preview(task.task_id)
        else:
            facade.preparation_egress_preview(payload, "teacher-text", store.revision)
    assert error.value.code == "preparation_not_initialized"
    assert store.borrow_calls == [] and transport.calls == 0


def test_changed_local_image_after_confirmation_never_borrows_key(desktop_paths, tmp_path):
    facade, manager, store, transport, payload, _ = image_facade(desktop_paths, tmp_path)
    task = facade.prepare_preparation(payload, "teacher-text", store.revision)
    asset = payload["image_assets"][0]
    copied = manager.image_store.root / (asset["sha256"] + ".image")
    copied.write_bytes(b"changed synthetic fixture bytes")
    with pytest.raises(DesktopFacadeError):
        facade.generate_preparation(task.task_id, teacher_confirmed=True)
    assert store.borrow_calls == [] and transport.calls == 0
