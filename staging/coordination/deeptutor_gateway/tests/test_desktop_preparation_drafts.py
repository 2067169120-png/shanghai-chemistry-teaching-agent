from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import (
    PreparationDraftError,
    PreparationDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


def _payload(*, topic: str = "化学平衡") -> dict:
    return {
        "output_kind": "joint",
        "topic": topic,
        "audience": "高二学生",
        "lesson_route": "新授",
        "lesson_timing": "2课时×40分钟",
        "objective": "建立平衡移动的证据链，落实上海教材中的实验与图表阅读。",
        "materials": (
            "教师已填写的教材摘录\n\n"
            "【蓝图备课参考摘录：待教师核验】\n"
            "蓝图记录：BLUEPRINT-1\n"
            "E1：教材证据；E2：讲义关系\n"
            "不把解答规划当作核定答案。"
        ),
        "advanced": {
            "learning_and_experiment": "安排颜色变化实验并记录控制变量。",
            "template_and_delivery": "16:9，先展示现象再追问。",
            "homework_and_strategy": "布置一题迁移题，课后教师复核。",
        },
    }


def _record(payload: dict, *, created_at: str = "2026-09-08T08:00:00Z") -> dict:
    fields = {
        key: payload[key]
        for key in (
            "topic",
            "audience",
            "lesson_route",
            "lesson_timing",
            "objective",
            "materials",
        )
    }
    return {
        "kind": "preparation",
        "contract_version": "lesson-blueprint/2.0.0",
        "output_kind": payload["output_kind"],
        "core_fields": fields,
        "advanced": deepcopy(payload["advanced"]),
        "status": "draft",
        "created_at": created_at,
    }


def _state(tmp_path, *items: tuple[str, dict]) -> DesktopStateStore:
    state = DesktopStateStore(tmp_path)
    for draft_id, record in items:
        state.save_draft(draft_id, record)
    return state


def _expected_revision(record: dict) -> str:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_options_are_latest_first_and_load_survives_state_restart(tmp_path):
    older = _record(_payload(topic="旧课题"), created_at="2026-09-07T08:00:00Z")
    newer = _record(_payload(), created_at="2026-09-08T08:00:00Z")
    state = _state(tmp_path, ("prep-old", older), ("prep-new", newer))

    service = PreparationDraftService(DesktopStateStore(state.root))
    options = service.options()

    assert [item["draft_id"] for item in options] == ["prep-new", "prep-old"]
    latest = options[0]
    loaded = service.load(latest["draft_id"], latest["revision"])
    assert loaded == {
        "draft_id": "prep-new",
        "revision": _expected_revision(newer),
        "payload": _payload(),
    }


def test_load_preserves_all_six_fields_advanced_and_blueprint_reference(tmp_path):
    payload = _payload()
    record = _record(payload)
    state = _state(tmp_path, ("prep-blueprint", record))
    option = PreparationDraftService(state).options()[0]

    result = PreparationDraftService(DesktopStateStore(state.root)).load(
        "prep-blueprint", option["revision"]
    )

    assert result["payload"] == payload
    assert "BLUEPRINT-1" in result["payload"]["materials"]
    assert result["payload"]["advanced"] == payload["advanced"]


def test_options_exclude_blueprints_tasks_errors_and_invalid_drafts(tmp_path):
    valid = _record(_payload())
    blueprint = {"kind": "textbook_prompt_blueprint", "status": "completed"}
    failed_task = {"kind": "preparation", "status": "failed", "error_code": "x"}
    invalid = _record(_payload())
    invalid["core_fields"].pop("materials")
    state = _state(
        tmp_path,
        ("valid", valid),
        ("blueprint", blueprint),
        ("failed", failed_task),
        ("invalid", invalid),
    )

    service = PreparationDraftService(state)
    assert [item["draft_id"] for item in service.options()] == ["valid"]
    with pytest.raises(PreparationDraftError) as error:
        service.load("blueprint", service.options()[0]["revision"])
    assert error.value.code == "preparation_draft_invalid"


def test_revision_covers_complete_record_and_stale_load_does_not_write(tmp_path):
    record = _record(_payload())
    state = _state(tmp_path, ("prep-1", record))
    service = PreparationDraftService(state)
    option = service.options()[0]
    before = state.path.read_bytes()

    changed = deepcopy(record)
    changed["teacher_note"] = "新增但仍属于原始完整记录的字段"
    state.save_draft("prep-1", changed)
    changed_revision = service.options()[0]["revision"]
    assert changed_revision == _expected_revision(changed)
    assert changed_revision != option["revision"]

    before_read = state.path.read_bytes()
    with pytest.raises(PreparationDraftError) as stale:
        service.load("prep-1", option["revision"])
    assert stale.value.code == "preparation_draft_stale"
    with pytest.raises(PreparationDraftError) as missing:
        service.load("missing", changed_revision)
    assert missing.value.code == "preparation_draft_missing"
    assert state.path.read_bytes() == before_read
    assert before != before_read


def test_options_cap_at_fifty_valid_drafts(tmp_path):
    state = DesktopStateStore(tmp_path)
    for index in range(55):
        state.save_draft(
            f"prep-{index:02d}",
            _record(
                _payload(topic=f"课题 {index}"),
                created_at=f"2026-09-08T08:{index:02d}:00Z",
            ),
        )

    options = PreparationDraftService(DesktopStateStore(state.root)).options()
    assert len(options) == 50
    assert options[0]["draft_id"] == "prep-54"
    assert options[-1]["draft_id"] == "prep-05"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("bad_output", "preparation_draft_invalid"),
        ("bad_output_object", "preparation_draft_invalid"),
        ("bad_timing", "preparation_draft_invalid"),
        ("bad_advanced", "preparation_draft_invalid"),
    ],
)
def test_load_rejects_invalid_payload_fields_without_state_write(tmp_path, mutation, code):
    record = _record(_payload())
    if mutation == "bad_output":
        record["output_kind"] = "unknown"
    elif mutation == "bad_output_object":
        record["output_kind"] = ["joint"]
    elif mutation == "bad_timing":
        record["core_fields"]["lesson_timing"] = "不是课时"
    else:
        record["advanced"] = {"unexpected": "field"}
    state = _state(tmp_path, ("prep-invalid", record))
    before = state.path.read_bytes()

    with pytest.raises(PreparationDraftError) as error:
        PreparationDraftService(state).load("prep-invalid", "anything")
    assert error.value.code == code
    assert state.path.read_bytes() == before
