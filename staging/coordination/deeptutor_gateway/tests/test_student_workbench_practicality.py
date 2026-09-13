from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.service import (
    ApiError,
    GatewayService,
    StateStore,
    _parse_student_csv,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
STUDENT_ID = "00000000-0000-4000-8000-000000000001"


class CapturingStudentBridge:
    def __init__(self, *, available: bool = True):
        self.available = available
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def status(self):
        return {
            "available": self.available,
            "configured": self.available,
            "interface_version": "student-learning-gateway.v1",
        }

    def append_structured_input(self, *, profile_id, kind, payload):
        self.events.append((profile_id, kind, payload))
        return {"event_id": "event-1", "event_type": kind}


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        ("grades", b"earned,maximum\n72,100\n", {"earned": 72, "maximum": 100}),
        (
            "grades",
            b"earned,maximum,assessment_ref\n72.5,100,exam-1",
            {"earned": 72.5, "maximum": 100, "assessment_ref": "exam-1"},
        ),
        (
            "progress",
            "module,completion_percent,semester\n酸碱平衡,50,高二下".encode(),
            {"module": "酸碱平衡", "completion_percent": 50, "semester": "高二下"},
        ),
    ],
)
def test_strict_student_csv_parses_only_the_minimal_domain_payload(kind, payload, expected):
    assert _parse_student_csv(payload, kind) == expected


@pytest.mark.parametrize(
    ("kind", "payload", "code"),
    [
        ("grades", b"earned,maximum,unknown\n72,100,x", "invalid_student_csv_header"),
        ("grades", b"maximum,earned\n100,72", "invalid_student_csv_header"),
        ("grades", b"earned,maximum\n=1+1,100", "student_csv_formula_injection_rejected"),
        ("progress", b"module,completion_percent\n@cmd,50", "student_csv_formula_injection_rejected"),
        ("grades", b"earned,maximum\nNaN,100", "invalid_student_csv_number"),
        ("grades", b"earned,maximum\n101,100", "invalid_student_csv_score_range"),
        ("progress", b"module,completion_percent\nmodule,101", "invalid_student_csv_progress_range"),
        ("grades", b"earned,maximum\n72,100\n73,100", "invalid_student_csv_shape"),
        ("grades", b"earned,maximum\n\n72,100", "invalid_student_csv_shape"),
        ("attempt", b"earned,maximum\n72,100", "student_csv_kind_not_supported"),
    ],
)
def test_strict_student_csv_rejects_unknown_columns_bad_values_formulae_and_extra_text(
    kind, payload, code
):
    with pytest.raises(ApiError) as error:
        _parse_student_csv(payload, kind)
    assert error.value.code == code


def test_csv_upload_records_parsed_numbers_and_rejects_before_raw_save(tmp_path):
    bridge = CapturingStudentBridge()
    store = StateStore(tmp_path, 4096, None, bridge, None)
    receipt = store.save_upload(
        STUDENT_ID,
        {
            "filename": "grades.csv",
            "mime_type": "text/csv",
            "kind": "grades",
            "content_base64": base64.b64encode(b"earned,maximum\n72,100\n").decode(),
        },
    )
    assert bridge.events == [(STUDENT_ID, "grades", {"earned": 72, "maximum": 100})]
    assert receipt["deidentification"]["student_domain_binding"]["recorded"] is True
    assert (
        receipt["deidentification"]["derived_copy"]["transformation"]
        == "strict_single_row_csv_to_structured_json_then_identifier_redaction"
    )

    rejected_root = tmp_path / "rejected"
    rejected_store = StateStore(rejected_root, 4096, None, bridge, None)
    with pytest.raises(ApiError) as error:
        rejected_store.save_upload(
            STUDENT_ID,
            {
                "filename": "grades.csv",
                "mime_type": "text/csv",
                "kind": "grades",
                "content_base64": base64.b64encode(
                    b"earned,maximum\n=2+2,100\n"
                ).decode(),
            },
        )
    assert error.value.code == "student_csv_formula_injection_rejected"
    assert not list(rejected_root.rglob("*"))


def bare_service(*, students, bridge_available):
    service = GatewayService.__new__(GatewayService)
    service.config = SimpleNamespace(students=tuple(students), shchem_root=SHCHEM_ROOT)
    service.student_bridge = CapturingStudentBridge(available=bridge_available)
    return service


def test_student_listing_reports_zero_profiles_and_unavailable_bridge_without_delivery_claims():
    principal = Principal("teacher", "teacher", token_digest("teacher-token-0123456789"), ("*",))
    zero = bare_service(students=(), bridge_available=True).list_students(principal)
    assert zero["count"] == 0
    assert zero["workflow"]["ready"] is False
    assert zero["workflow"]["blockers"] == ["no_authorized_student_profiles"]

    unavailable = bare_service(students=(STUDENT_ID,), bridge_available=False).list_students(principal)
    assert unavailable["workflow"]["ready"] is False
    assert unavailable["workflow"]["blockers"] == ["student_domain_bridge_unavailable"]
    assert unavailable["workflow"]["formal_candidate_practice_items"] == 0
    assert unavailable["workflow"]["automatic_scoring_items"] == 0
    assert unavailable["workflow"]["candidate_delivery_entry_present"] is False


def test_learning_view_projection_preserves_real_snapshot_and_honest_empty_fields():
    service = bare_service(students=(STUDENT_ID,), bridge_available=True)
    value = {
        "profile_id": STUDENT_ID,
        "metadata": {"grade": "高二"},
        "input_event_count": 4,
        "timeline_event_count": 8,
        "mastery_history": [
            {
                "cycle_index": 2,
                "snapshot": [
                    {
                        "dimension": "K",
                        "tag_id": "K10",
                        "status": "provisional_weakness",
                        "loss_rate": 0.25,
                        "valid_atomic_part_count": 4,
                        "independent_source_count": 2,
                    }
                ],
            }
        ],
        "focus_history": [],
        "spaced_review_due": [
            {
                "dimension": "K",
                "tag_id": "K10",
                "source_cycle_index": 2,
                "due_cycle_index": 3,
                "basis": "provisional_weakness",
            }
        ],
        "next_week_objectives": [
            {
                "target_cycle_index": 3,
                "focus_role": "main",
                "tag_id": "K10",
                "objective": "复核新情境损失率",
            }
        ],
        "weekly_activities": [
            {"week_day": 1, "minutes": 60, "phase": "基础巩固", "focus": "K10", "task": "复核"}
        ],
        "history_head_sha256": "a" * 64,
        "long_term_teaching_effectiveness_verified": False,
        "machine_only": True,
        "human_reviewed": False,
    }
    projected = service._project_learning_view(value, STUDENT_ID)
    assert projected["latest_mastery_snapshot"] == value["mastery_history"][-1]["snapshot"]
    assert projected["spaced_review_due"] == value["spaced_review_due"]
    assert projected["next_week_objectives"] == value["next_week_objectives"]
    assert projected["weekly_activities"] == value["weekly_activities"]
    assert projected["field_availability"] == {
        "mastery_history": True,
        "latest_mastery_snapshot": True,
        "spaced_review_due": True,
        "next_week_objectives": True,
        "weekly_activities": True,
    }
    assert projected["practice_delivery"] == {
        "formal_candidate_practice_items": 0,
        "automatic_scoring_items": 0,
        "candidate_delivery_entry_present": False,
    }

    empty = service._project_learning_view(
        {
            "profile_id": STUDENT_ID,
            "human_reviewed": False,
            "long_term_teaching_effectiveness_verified": False,
        },
        STUDENT_ID,
    )
    assert empty["mastery_history"] == []
    assert empty["latest_mastery_snapshot"] == []
    assert empty["spaced_review_due"] == []
    assert empty["next_week_objectives"] == []
    assert empty["weekly_activities"] == []
    assert not any(empty["field_availability"].values())


def test_student_webui_gates_empty_profiles_bridge_failure_and_never_builds_placeholder_week():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    service = (
        WORKSPACE / "integrations/deeptutor_shchem_v1/service.py"
    ).read_text(encoding="utf-8")
    combined = app + html
    for marker in (
        "function studentWorkflowReadiness",
        "function updateStudentWorkflowControls",
        "当前教师令牌下没有可用学生 profile",
        "学生学习桥不可用",
        "尚未选择学生",
        "未拼接学生上传路径",
        "未拼接空 student_id 路径",
        'id="studentWorkflowState"',
        'id="latestMasterySnapshot"',
        'id="spacedReviewDue"',
        'id="nextWeekObjectives"',
        'id="weeklyActivities"',
        "后端未提供实际 weekly activities；不生成 7 天占位活动",
        "候选题正式练习投放：0；自动判分：0；本工作台没有候选题投放入口",
    ):
        assert marker in combined
    assert "strict_single_row_csv_to_structured_json_then_identifier_redaction" in service
    assert "application/json,text/csv,text/plain" not in html
    assert 'accept="application/json,text/csv,.json,.csv"' in html
    assert "Array.from({ length: 7 }" not in app
    assert "Number(minutes?.main_focus ?? 252)" not in app
    assert "state.candidateReviewMasterVisualScanProjectionByNode" in app
    assert "projectionByMaster.size !== 169" in app


def test_student_phase1_manifests_are_closed_and_bind_static_files():
    expected = {
        "scope": "student_workbench_practicality_phase1",
        "empty_profile_controls_enabled": False,
        "bridge_unavailable_controls_enabled": False,
        "empty_student_id_path_allowed": False,
        "grades_progress_json_allowed": True,
        "grades_progress_strict_csv_allowed": True,
        "attempt_timing_json_only": True,
        "text_plain_structured_input_allowed": False,
        "csv_unknown_columns_allowed": False,
        "csv_formula_cells_allowed": False,
        "placeholder_weekly_activities_generated": False,
        "formal_candidate_practice_items": 0,
        "automatic_scoring_items": 0,
        "candidate_delivery_entry_present": False,
        "human_reviewed": False,
        "teaching_effectiveness_verified": False,
        "publication_allowed": False,
    }
    for path in (
        OVERLAY / "overlay.manifest.json",
        WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json",
    ):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["student_workbench_phase1"] == expected
        for filename, descriptor in manifest["files"].items():
            import hashlib

            assert hashlib.sha256((OVERLAY / filename).read_bytes()).hexdigest() == descriptor["sha256"]
