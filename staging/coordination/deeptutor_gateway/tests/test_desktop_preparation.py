from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    PREPARATION_CANONICAL_SCHEMA_VERSION,
    PREPARATION_REQUEST_SCHEMA_VERSION,
    DesktopPreparationError,
    DesktopPreparationManager,
    _allocate_minutes,
    _canonical_candidate_digest,
    _validate_canonical_candidate,
    normalize_preparation_candidate,
    normalize_preparation_payload,
    preparation_candidate_schema,
)


def _payload(*, output_kind: str = "joint") -> dict[str, Any]:
    return {
        "output_kind": output_kind,
        "topic": "氧化还原反应复习",
        "audience": "高三等级考复习班",
        "lesson_route": "复习",
        "lesson_timing": "1课时×40分钟",
        "objective": "用电子守恒解释并解决氧化还原反应问题",
        "materials": "教师本页输入的章节范围与例题方向",
        "advanced": {
            "learning_and_experiment": "安排一次小组解释活动",
            "template_and_delivery": "投影文字简洁",
            "homework_and_strategy": "含一道迁移题",
        },
    }


def _raw_candidate() -> dict[str, Any]:
    return {
        "title": "氧化还原反应：守恒与迁移",
        "objectives": [
            {"statement": "能从化合价变化识别氧化剂和还原剂。"},
            {"statement": "能用电子守恒完成定量推理。"},
        ],
        "activities": [
            {
                "title": "证据辨析",
                "objective_numbers": [1],
                "minutes": 10,
                "teacher_action": "呈现反应并追问化合价变化。",
                "student_action": "标注化合价并说明判断依据。",
                "materials": ["教师提供的反应实例"],
            },
            {
                "title": "守恒迁移",
                "objective_numbers": [2],
                "minutes": 30,
                "teacher_action": "组织分步列式并比较两种解法。",
                "student_action": "独立计算后互相核对电子得失。",
                "materials": ["教师提供的迁移题"],
            },
        ],
        "assessments": [
            {
                "title": "概念出口条",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "evidence_of_learning": "学生能写出判断及依据。",
                "success_criteria": ["氧化剂判断正确", "依据包含化合价变化"],
            },
            {
                "title": "守恒计算检查",
                "objective_numbers": [2],
                "activity_numbers": [2],
                "evidence_of_learning": "学生的电子得失和数量关系闭合。",
                "success_criteria": ["电子守恒关系正确", "单位完整"],
            },
        ],
        "slides": [
            {
                "title": "课题与目标",
                "purpose": "建立学习方向",
                "objective_numbers": [1, 2],
                "activity_numbers": [],
                "assessment_numbers": [],
                "minutes": 5,
                "content": ["识别角色", "应用电子守恒"],
                "teacher_notes": "先让学生说出已有判断方法。",
            },
            {
                "title": "从证据到守恒",
                "purpose": "完成核心学习活动",
                "objective_numbers": [1, 2],
                "activity_numbers": [1, 2],
                "assessment_numbers": [1],
                "minutes": 25,
                "content": ["化合价变化是可见证据", "电子得失必须守恒"],
                "teacher_notes": "化学事实和例题数据使用前由教师复核。",
            },
            {
                "title": "检测与作业",
                "purpose": "收集学习证据并迁移",
                "objective_numbers": [2],
                "activity_numbers": [2],
                "assessment_numbers": [2],
                "minutes": 10,
                "content": ["先独立作答", "再核对守恒关系"],
                "teacher_notes": "不得表述为官方评分点。",
            },
        ],
        "lesson_stages": [
            {
                "title": "证据建模",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "assessment_numbers": [1],
                "minutes": 15,
                "teacher_action": "提出问题并记录学生依据。",
                "student_action": "完成标注、交流与修正。",
                "materials": ["反应实例"],
                "assessment": "依据出口条即时调整追问。",
            },
            {
                "title": "守恒迁移",
                "objective_numbers": [2],
                "activity_numbers": [2],
                "assessment_numbers": [2],
                "minutes": 25,
                "teacher_action": "组织独立作答和同伴核验。",
                "student_action": "列式、计算并说明数量关系。",
                "materials": ["迁移题"],
                "assessment": "检查电子守恒、计算与单位。",
            },
        ],
        "homework": {
            "title": "课后迁移",
            "tasks": [
                {
                    "instruction": "完成一道同结构迁移题并写出守恒依据。",
                    "objective_numbers": [2],
                }
            ],
            "estimated_minutes": 15,
        },
        "uncertainties": [
            {
                "field": "example_data",
                "description": "具体例题数据需由教师补入并核对。",
                "teacher_action": "授课前核对题面、答案与单位。",
            }
        ],
    }


class RecordingProvider:
    def __init__(self, candidate: dict[str, Any] | None = None) -> None:
        self.candidate = candidate or _raw_candidate()
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        payload: dict[str, Any],
        candidate_schema: dict[str, Any],
        profile_binding: dict[str, Any],
        report_progress: Any,
        is_cancelled: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "payload": deepcopy(payload),
                "schema": deepcopy(candidate_schema),
                "binding": deepcopy(profile_binding),
                "cancelled": bool(is_cancelled()),
            }
        )
        report_progress(
            {
                "stage": "provider_fixture",
                "percent": 31,
                "message_zh": "测试候选已生成。",
            }
        )
        return deepcopy(self.candidate)


class FileRenderer:
    def __init__(self, *, fail_calls: int = 0) -> None:
        self.fail_calls = fail_calls
        self.calls: list[dict[str, Any]] = []

    def render(
        self,
        candidate: dict[str, Any],
        *,
        output_kind: str,
        output_dir: Path,
        report_progress: Any,
        is_cancelled: Any,
    ) -> dict[str, Any]:
        call_number = len(self.calls) + 1
        self.calls.append(
            {
                "candidate": deepcopy(candidate),
                "output_kind": output_kind,
                "output_dir": output_dir,
            }
        )
        output_dir.joinpath("attempt-marker.txt").write_text(
            f"attempt {call_number}", encoding="utf-8"
        )
        if call_number <= self.fail_calls:
            raise RuntimeError("synthetic local renderer failure")
        assert not is_cancelled()
        report_progress(
            {
                "stage": "renderer_fixture",
                "percent": 80,
                "message_zh": "测试产物已渲染。",
            }
        )
        pptx = output_dir / "teacher-candidate.pptx"
        pptx.write_bytes(b"synthetic-pptx-v1")
        return {
            "artifacts": [
                {
                    "artifact_id": "pptx",
                    "filename": pptx.name,
                    "content_type": (
                        "application/vnd.openxmlformats-officedocument."
                        "presentationml.presentation"
                    ),
                    "path": pptx,
                    # Deliberately false input receipts: the manager must ignore
                    # these and calculate its own values from the local file.
                    "size_bytes": 1,
                    "sha256": "0" * 64,
                }
            ],
            "slide_count": len(candidate["slides"]),
            "quality": {"status": "machine_render_complete"},
        }


def _walk_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", ())) == set(schema.get("properties", ()))
        for child in schema["properties"].values():
            _walk_schema(child)
    items = schema.get("items")
    if isinstance(items, dict):
        _walk_schema(items)
    for child in schema.get("$defs", {}).values():
        _walk_schema(child)


def test_candidate_schema_is_closed_and_accepts_a_real_nonempty_candidate() -> None:
    schema = preparation_candidate_schema()
    Draft202012Validator.check_schema(schema)
    current_candidate = _raw_candidate()
    for activity in current_candidate["activities"]:
        activity["worksheet"] = None
    for slide in current_candidate["slides"]:
        slide["visual"] = None
        slide["image"] = None
    Draft202012Validator(schema).validate(current_candidate)
    _walk_schema(schema)

    serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        '"const"',
        '"pattern"',
        '"minimum"',
        '"maximum"',
        '"minItems"',
        '"maxItems"',
        '"uniqueItems"',
        '"allOf"',
        '"not"',
        '"if"',
        '"then"',
    ):
        assert forbidden not in serialized


def test_payload_and_candidate_normalization_close_boundaries_and_time() -> None:
    payload = normalize_preparation_payload(_payload())
    assert payload["schema_version"] == PREPARATION_REQUEST_SCHEMA_VERSION
    assert payload["artifact_mode"] == "linked_bundle"
    assert payload["lesson_route"] == "review"
    assert payload["timing"] == {
        "periods": 1,
        "minutes_per_period": 40,
        "total_minutes": 40,
    }
    assert payload["source_basis"]["mode"] == "teacher_input_only"
    assert payload["source_basis"]["evidence_ids"] == []
    assert payload["candidate_only"] is True
    assert payload["teacher_review_required"] is True
    assert payload["publication_allowed"] is False
    assert payload["official_claim_allowed"] is False

    first = normalize_preparation_candidate(_raw_candidate(), payload)
    second = normalize_preparation_candidate(_raw_candidate(), payload)
    assert first == second
    assert first["schema_version"] == PREPARATION_CANONICAL_SCHEMA_VERSION
    assert first["candidate_id"].startswith("PREPCAND-")
    assert [row["id"] for row in first["objectives"]] == ["O01", "O02"]
    assert [row["id"] for row in first["activities"]] == ["A01", "A02"]
    assert [row["id"] for row in first["assessments"]] == ["E01", "E02"]
    assert first["activities"][1]["objective_ids"] == ["O02"]
    for field in ("activities", "slides", "lesson_stages"):
        assert sum(row["minutes"] for row in first[field]) == 40
        assert all(row["minutes"] >= 1 for row in first[field])
    assert first["slides"][0]["content"] == ["识别角色", "应用电子守恒"]
    assert first["uncertainties"][-1]["field"] == "source_basis"
    assert first["source_basis"]["evidence_ids"] == []
    assert first["candidate_only"] is True
    assert first["teacher_review_required"] is True
    assert first["publication_allowed"] is False


def test_candidate_rejects_unknown_fields_and_broken_linkage() -> None:
    unknown = _raw_candidate()
    unknown["slides"][0]["invented"] = "not in contract"
    with pytest.raises(DesktopPreparationError) as caught:
        normalize_preparation_candidate(unknown, _payload())
    assert caught.value.code == "preparation_candidate_invalid"

    broken = _raw_candidate()
    broken["assessments"][1]["objective_numbers"] = [1]
    with pytest.raises(DesktopPreparationError) as caught:
        normalize_preparation_candidate(broken, _payload())
    assert caught.value.code == "preparation_candidate_linkage_invalid"


@pytest.mark.parametrize("minutes", [[2, 4, 5, 6, 7, 4, 6, 6], [10, 30], [5, 25, 10]])
def test_already_closed_minutes_are_not_reallocated(minutes):
    assert _allocate_minutes(minutes, 40, "测试") == minutes


def _partitioned_timing_candidate():
    candidate = _raw_candidate()
    candidate["slides"][0]["activity_numbers"] = [1]
    candidate["slides"][1]["activity_numbers"] = [1]
    candidate["slides"][2]["activity_numbers"] = [2]
    return candidate


def test_timing_uses_stage_budget_for_activities_and_linked_slides():
    raw = _partitioned_timing_candidate()
    original = deepcopy(raw)
    value = normalize_preparation_candidate(raw, _payload())
    assert [row["minutes"] for row in value["lesson_stages"]] == [15, 25]
    assert [row["minutes"] for row in value["activities"]] == [15, 25]
    assert sum(row["minutes"] for row in value["slides"][:2]) == 15
    assert value["slides"][2]["minutes"] == 25
    assert any(row["field"] == "timing_adjustment" for row in value["uncertainties"])
    assert not any(row["field"] == "timing_alignment" for row in value["uncertainties"])
    assert raw == original
    assert _validate_canonical_candidate(value, _payload()) == value


@pytest.mark.parametrize(
    "case", ["unlinked_cover", "shared_slide", "different_order", "too_many_slides"]
)
def test_ambiguous_slide_timing_is_preserved_and_explained(case):
    raw = _partitioned_timing_candidate()
    if case == "unlinked_cover":
        raw["slides"][0]["activity_numbers"] = []
    elif case == "shared_slide":
        raw["slides"][0]["activity_numbers"] = [1, 2]
    elif case == "different_order":
        raw["slides"][0]["activity_numbers"] = [2]
    else:
        raw["lesson_stages"][0]["minutes"] = 1
        raw["lesson_stages"][1]["minutes"] = 39
    value = normalize_preparation_candidate(raw, _payload())
    assert [row["minutes"] for row in value["slides"]] == [5, 25, 10]
    assert any(row["field"] == "timing_alignment" for row in value["uncertainties"])
    assert sum(row["minutes"] for row in value["activities"]) == 40


def test_shared_stage_does_not_double_count_or_guess_activity_minutes():
    raw = _partitioned_timing_candidate()
    raw["lesson_stages"][0]["activity_numbers"] = [1, 2]
    value = normalize_preparation_candidate(raw, _payload())
    assert [row["minutes"] for row in value["activities"]] == [10, 30]
    assert [row["minutes"] for row in value["slides"]] == [5, 25, 10]
    assert any(row["field"] == "timing_alignment" for row in value["uncertainties"])


def test_already_aligned_candidate_keeps_all_times_without_adjustment_note():
    raw = _partitioned_timing_candidate()
    raw["activities"][0]["minutes"], raw["activities"][1]["minutes"] = 15, 25
    for row, minutes in zip(raw["slides"], [5, 10, 25], strict=True):
        row["minutes"] = minutes
    value = normalize_preparation_candidate(raw, _payload())
    for field in ("activities", "slides", "lesson_stages"):
        assert [row["minutes"] for row in value[field]] == [
            row["minutes"] for row in raw[field]
        ]
    assert not any(row["field"].startswith("timing_") for row in value["uncertainties"])


def _already_timed_grouped_candidate():
    raw = _partitioned_timing_candidate()
    raw["lesson_stages"] = [raw["lesson_stages"][0]]
    raw["lesson_stages"][0]["activity_numbers"] = [1, 2]
    raw["lesson_stages"][0]["minutes"] = 40
    for row, minutes in zip(raw["slides"], [4, 6, 30], strict=True):
        row["minutes"] = minutes
    return raw


def test_grouped_stage_accepts_existing_activity_and_slide_budgets_without_guessing():
    raw = _already_timed_grouped_candidate()
    before = deepcopy(raw)
    value = normalize_preparation_candidate(raw, _payload())
    assert raw == before
    for field in ("activities", "slides", "lesson_stages"):
        assert [r["minutes"] for r in value[field]] == [
            r["minutes"] for r in raw[field]
        ]
    assert not any(row["field"].startswith("timing_") for row in value["uncertainties"])
    assert _validate_canonical_candidate(value, _payload()) == value


@pytest.mark.parametrize(
    "case",
    [
        "overlap",
        "uncovered",
        "reversed",
        "shared_slide",
        "unlinked",
        "unequal",
        "interleaved",
    ],
)
def test_grouped_stage_disagreement_remains_unresolved_and_never_changes_minutes(case):
    raw = _already_timed_grouped_candidate()
    if case == "overlap":
        extra = deepcopy(raw["lesson_stages"][0])
        extra["minutes"] = 10
        extra["activity_numbers"] = [2]
        raw["lesson_stages"][0]["minutes"] = 30
        raw["lesson_stages"].append(extra)
    elif case == "uncovered":
        raw["slides"][2]["activity_numbers"] = [1]
    elif case == "reversed":
        raw["lesson_stages"][0]["activity_numbers"] = [2, 1]
    elif case == "shared_slide":
        raw["slides"][0]["activity_numbers"] = [1, 2]
    elif case == "unlinked":
        raw["slides"][0]["activity_numbers"] = []
    elif case == "unequal":
        raw["activities"][0]["minutes"] = 11
        raw["activities"][1]["minutes"] = 29
    else:
        raw["slides"][1]["activity_numbers"] = [2]
        raw["slides"][2]["activity_numbers"] = [1]
    before = deepcopy(raw)
    value = normalize_preparation_candidate(raw, _payload())
    assert raw == before
    for field in ("activities", "slides", "lesson_stages"):
        assert [r["minutes"] for r in value[field]] == [
            r["minutes"] for r in raw[field]
        ]
    assert any(row["field"] == "timing_alignment" for row in value["uncertainties"])


def test_grouped_stage_checks_each_group_not_only_the_overall_total():
    from integrations.deeptutor_shchem_v1.desktop_preparation import (
        _grouped_stage_times_agree,
    )

    activities = [
        {"id": "A01", "minutes": 5},
        {"id": "A02", "minutes": 12},
        {"id": "A03", "minutes": 23},
    ]
    slides = [
        {"activity_ids": [row["id"]], "minutes": row["minutes"]} for row in activities
    ]
    stages = [
        {"activity_ids": ["A01", "A02"], "minutes": 17},
        {"activity_ids": ["A03"], "minutes": 23},
    ]
    assert _grouped_stage_times_agree(activities, slides, stages)
    stages[0]["minutes"], stages[1]["minutes"] = 18, 22
    assert not _grouped_stage_times_agree(activities, slides, stages)


def test_existing_frozen_candidate_keeps_legacy_times_on_replay():
    frozen = normalize_preparation_candidate(_raw_candidate(), _payload())
    for row, minutes in zip(frozen["activities"], [11, 29], strict=True):
        row["minutes"] = minutes
    frozen["uncertainties"] = [
        row for row in frozen["uncertainties"] if not row["field"].startswith("timing_")
    ]
    frozen["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(frozen)[:32]
    original = deepcopy(frozen)
    assert _validate_canonical_candidate(frozen, _payload()) == original
    assert frozen == original


@pytest.mark.parametrize("weights", [[0, 0, 0], [1, 100, 1], [2, 4, 5, 6, 7, 4, 6, 4]])
def test_rescaled_minutes_stay_positive_closed_and_deterministic(weights):
    result = _allocate_minutes(weights, 40, "测试")
    assert all(type(value) is int and value >= 1 for value in result)
    assert sum(result) == 40
    assert result == _allocate_minutes(weights, 40, "测试")


def test_lesson_stage_requires_real_activity_but_cover_slide_may_have_none():
    candidate = _raw_candidate()
    assert candidate["slides"][0]["activity_numbers"] == []
    normalized = normalize_preparation_candidate(candidate, _payload())
    assert normalized["slides"][0]["activity_ids"] == []
    candidate["lesson_stages"][0]["activity_numbers"] = []
    with pytest.raises(DesktopPreparationError, match="教案环节1/活动引用不完整"):
        normalize_preparation_candidate(candidate, _payload())


def test_manager_success_pins_profile_and_recalculates_artifact_receipts(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()
    renderer = FileRenderer()
    manager = DesktopPreparationManager(tmp_path / "preparation", renderer)
    prepared = manager.prepare(_payload(), "PROFILE-TEXT", "REV-7")
    duplicate = manager.prepare(_payload(), "PROFILE-TEXT", "REV-7")
    assert duplicate["task_id"] == prepared["task_id"]
    assert duplicate["attempt"] == 0
    assert prepared["status"] == "prepared"
    assert prepared["candidate_available"] is False

    progress: list[dict[str, Any]] = []
    completed = manager.run(
        prepared["task_id"],
        provider,
        progress.append,
        lambda: False,
    )
    assert completed["status"] == "completed"
    assert completed["attempt"] == 1
    assert completed["candidate_available"] is True
    assert completed["candidate_id"].startswith("PREPCAND-")
    assert completed["slide_count"] == 3
    assert completed["quality"]["candidate_only"] is True
    assert completed["quality"]["teacher_review_required"] is True
    assert completed["quality"]["publication_allowed"] is False
    assert [row["artifact_id"] for row in completed["artifacts"]] == ["pptx"]
    assert completed["artifacts"][0]["size_bytes"] == len(b"synthetic-pptx-v1")
    assert completed["artifacts"][0]["sha256"] != "0" * 64
    assert progress[-1]["stage"] == "teacher_review_pending"

    assert len(provider.calls) == 1
    assert provider.calls[0]["payload"]["source_basis"]["evidence_ids"] == []
    assert provider.calls[0]["binding"] == {
        "profile_id": "PROFILE-TEXT",
        "profile_revision": "REV-7",
    }
    _walk_schema(provider.calls[0]["schema"])
    assert renderer.calls[0]["output_kind"] == "linked_bundle"
    assert renderer.calls[0]["candidate"]["candidate_id"] == completed["candidate_id"]

    artifact_path, content_type = manager.artifact_path(completed["task_id"], "pptx")
    assert artifact_path.read_bytes() == b"synthetic-pptx-v1"
    assert content_type.endswith("presentation")
    state_text = (
        (tmp_path / "preparation" / "tasks")
        .joinpath(f"{completed['task_id']}.json")
        .read_text(encoding="utf-8")
    )
    assert "api_key" not in state_text.casefold()
    assert "credential_value" not in state_text.casefold()


def test_manager_accepts_the_native_renderer_contract_end_to_end(
    tmp_path: Path,
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
        NativePreparationRenderer,
    )

    manager = DesktopPreparationManager(
        tmp_path / "native-preparation", NativePreparationRenderer()
    )
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-NATIVE")
    completed = manager.run(
        task["task_id"], RecordingProvider(), lambda _value: None, lambda: False
    )

    assert completed["status"] == "completed"
    assert completed["slide_count"] == 3
    assert {row["artifact_id"] for row in completed["artifacts"]} == {
        "candidate_json",
        "deck_json",
        "pptx",
        "preview_montage",
        "lesson_plan_docx",
        "qa_report",
    }
    assert completed["quality"]["machine_layout_check_only"] is True
    assert completed["quality"]["human_visual_review_performed"] is False
    for artifact in completed["artifacts"]:
        path, content_type = manager.artifact_path(
            completed["task_id"], artifact["artifact_id"]
        )
        assert path.is_file()
        assert path.stat().st_size == artifact["size_bytes"]
        assert content_type == artifact["content_type"]


def test_renderer_failure_freezes_seed_and_retry_does_not_call_provider_again(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()
    renderer = FileRenderer(fail_calls=1)
    manager = DesktopPreparationManager(tmp_path / "preparation", renderer)
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-8")

    failed = manager.run(task["task_id"], provider, lambda _value: None, lambda: False)
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "preparation_renderer_failed"
    assert failed["candidate_available"] is True
    assert failed["artifacts"] == []
    assert len(provider.calls) == 1
    first_root = renderer.calls[0]["output_dir"]
    assert first_root.name == "attempt-0001"
    assert first_root.joinpath("attempt-marker.txt").read_text(encoding="utf-8") == (
        "attempt 1"
    )

    retried = manager.retry(task["task_id"])
    assert retried["status"] == "prepared"
    completed = manager.run(
        task["task_id"], provider, lambda _value: None, lambda: False
    )
    assert completed["status"] == "completed"
    assert completed["attempt"] == 2
    assert len(provider.calls) == 1
    assert len(renderer.calls) == 2
    second_root = renderer.calls[1]["output_dir"]
    assert second_root.name == "attempt-0002"
    assert second_root != first_root
    assert first_root.joinpath("attempt-marker.txt").read_text(encoding="utf-8") == (
        "attempt 1"
    )


def test_restart_recovers_running_task_and_reuses_frozen_seed(tmp_path: Path) -> None:
    provider = RecordingProvider()

    class InterruptedRenderer:
        def render(self, candidate: dict[str, Any], **_kwargs: Any) -> Any:
            assert candidate["candidate_id"].startswith("PREPCAND-")
            raise KeyboardInterrupt

    root = tmp_path / "preparation"
    manager = DesktopPreparationManager(root, InterruptedRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-9")
    with pytest.raises(KeyboardInterrupt):
        manager.run(task["task_id"], provider, lambda _value: None, lambda: False)
    assert len(provider.calls) == 1

    renderer = FileRenderer()
    restarted = DesktopPreparationManager(root, renderer)
    recovered = restarted.get_task(task["task_id"])
    assert recovered["status"] == "failed"
    assert recovered["error"]["code"] == "preparation_interrupted"
    assert recovered["candidate_available"] is True
    restarted.retry(task["task_id"])

    def provider_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a frozen seed must be reused after restart")

    completed = restarted.run(
        task["task_id"],
        provider_must_not_run,
        lambda _value: None,
        lambda: False,
    )
    assert completed["status"] == "completed"
    assert completed["attempt"] == 2


def test_cancel_has_no_registered_partial_artifacts_and_is_retryable(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider()
    holder: dict[str, Any] = {}

    class CancellingRenderer:
        def render(
            self,
            candidate: dict[str, Any],
            *,
            output_kind: str,
            output_dir: Path,
            report_progress: Any,
            is_cancelled: Any,
        ) -> dict[str, Any]:
            holder["manager"].cancel(holder["task_id"])
            path = output_dir / "partial.pptx"
            path.write_bytes(b"partial")
            return {
                "artifacts": [
                    {
                        "artifact_id": "pptx",
                        "filename": path.name,
                        "content_type": "application/octet-stream",
                        "path": path,
                    }
                ],
                "slide_count": len(candidate["slides"]),
                "quality": {},
            }

    manager = DesktopPreparationManager(tmp_path / "preparation", CancellingRenderer())
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-10")
    holder.update(manager=manager, task_id=task["task_id"])
    cancelled = manager.run(
        task["task_id"], provider, lambda _value: None, lambda: False
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["artifacts"] == []
    assert cancelled["error"]["retryable"] is True
    with pytest.raises(DesktopPreparationError) as caught:
        manager.artifact_path(task["task_id"], "pptx")
    assert caught.value.code == "preparation_artifact_not_ready"
    assert manager.cancel(task["task_id"])["status"] == "cancelled"
    assert manager.retry(task["task_id"])["status"] == "prepared"


def test_artifact_path_detects_post_completion_drift(tmp_path: Path) -> None:
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    task = manager.prepare(_payload(output_kind="ppt"), "PROFILE-TEXT", "REV-11")
    completed = manager.run(
        task["task_id"], RecordingProvider(), lambda _value: None, lambda: False
    )
    path, _content_type = manager.artifact_path(completed["task_id"], "pptx")
    path.write_bytes(b"changed-after-registration")
    with pytest.raises(DesktopPreparationError) as caught:
        manager.artifact_path(completed["task_id"], "pptx")
    assert caught.value.code == "preparation_artifact_drift"


def test_list_tasks_is_bounded_and_sensitive_fields_are_rejected(
    tmp_path: Path,
) -> None:
    manager = DesktopPreparationManager(tmp_path / "preparation", FileRenderer())
    first = manager.prepare(_payload(), "PROFILE-TEXT", "REV-A")
    second = manager.prepare(_payload(), "PROFILE-TEXT", "REV-B")
    assert first["task_id"] != second["task_id"]
    assert len(manager.list_tasks(limit=1)) == 1
    assert {row["task_id"] for row in manager.list_tasks()} == {
        first["task_id"],
        second["task_id"],
    }
    with pytest.raises(DesktopPreparationError) as caught:
        manager.prepare(
            {**_payload(), "advanced": {"api_key": "must-never-persist"}},
            "PROFILE-TEXT",
            "REV-C",
        )
    assert caught.value.code == "preparation_sensitive_field_forbidden"
