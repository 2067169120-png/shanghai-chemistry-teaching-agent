from __future__ import annotations

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    BlueprintDraftError,
    BlueprintDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_preparation import (
    BlueprintPreparationService,
    append_reference,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    REVIEW_KIND,
    candidate_revision,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_limits import MAX_MATERIALS
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

PREVIEW_ID = "BLUEPRINT-PREPARATION-1"
INCOMPLETE_PREVIEW_ID = "BLUEPRINT-PREPARATION-INCOMPLETE"


def _candidate(theme: str = "工业废水中的物质转化与证据链") -> dict:
    return {
        "theme_center": theme,
        "shared_material_plan": [
            {
                "material_id": "M1",
                "purpose": "提供样品组成与初步实验现象",
                "evidence_refs": ["E1"],
            },
            {
                "material_id": "M2",
                "purpose": "提供后续条件和可核验数据",
                "evidence_refs": ["E2"],
            },
        ],
        "question_chain": [
            {
                "printed_question_id": "Q1",
                "atomic_part_id": "Q1-A1",
                "task_plan": "从共同材料中提取关键现象",
                "response_form": "简答",
                "material_refs": ["M1"],
                "depends_on": [],
                "answer_outline": "先整理现象，再指出需要核验的关系。",
                "knowledge_evidence": "依据 E1；具体教材对应关系待核验。",
                "difficulty_evidence": "一步信息提取；实测 unknown。",
            },
            {
                "printed_question_id": "Q1",
                "atomic_part_id": "Q1-A2",
                "task_plan": "结合条件说明实验设计依据",
                "response_form": "方程式书写",
                "material_refs": ["M1", "M2"],
                "depends_on": ["Q1-A1"],
                "answer_outline": "使用前一问提取的现象，补充必要条件。",
                "knowledge_evidence": "依据 E1、E2；仍需核对化学事实。",
                "difficulty_evidence": "两步关系推理；实测 unknown。",
            },
            {
                "printed_question_id": "Q2",
                "atomic_part_id": "Q2-A1",
                "task_plan": "根据前序结论组织迁移解释",
                "response_form": "填空",
                "material_refs": ["M2"],
                "depends_on": ["Q1-A2"],
                "answer_outline": "沿用前序结论完成条件变化下的解释。",
                "knowledge_evidence": "依据 E2；缺少实测数据时写 unknown。",
                "difficulty_evidence": "含前序结论迁移；实测 unknown。",
            },
        ],
        "unknowns": ["完整题面、数据和解答仍须教师核验"],
    }


def _evidence() -> list[dict]:
    return [
        {
            "source_type": "textbook_excerpt",
            "scope": "上海教材·物质转化·实验现象",
            "supports": ["物质转化", "现象整理"],
            "source_document": r"C:\private\textbook\chemistry.docx",
            "editable_source": {"path": r"C:\private\textbook\chemistry.docx"},
            "image_path": r"C:\private\textbook\page-03.png",
        },
        {
            "source_type": "user_handout_question_reference",
            "scope": "讲义第 2 题·条件关系",
            "supports": ["实验条件", "方程式书写"],
            "source_document": r"C:\private\handout\question-02.docx",
            "editable_source": {"path": r"C:\private\handout\question-02.docx"},
            "image_path": r"C:\private\handout\page-06.png",
        },
        {
            "source_type": "local_rule",
            "scope": "本地题库结构规则",
            "supports": ["主题内小题连续", "依赖关系"],
            "source_document": r"C:\private\rules\local.md",
            "editable_source": {"path": r"C:\private\rules\local.md"},
            "image_path": r"C:\private\rules\diagram.png",
        },
    ]


def _root_record(
    preview_id: str,
    value: dict,
    *,
    status: str = "completed",
    evidence: list[dict] | None = None,
) -> dict:
    return {
        "kind": "textbook_prompt_blueprint",
        "status": status,
        "preview": {
            "title": "工业废水主题",
            "evidence": deepcopy(_evidence() if evidence is None else evidence),
        },
        "result": {"candidate": deepcopy(value)},
        "preview_id": preview_id,
        "created_at": "2026-09-08T08:00:00Z",
    }


def _state(tmp_path, *, include_incomplete: bool = False) -> DesktopStateStore:
    state = DesktopStateStore(tmp_path)
    state.save_draft(PREVIEW_ID, _root_record(PREVIEW_ID, _candidate()))
    if include_incomplete:
        state.save_draft(
            INCOMPLETE_PREVIEW_ID,
            _root_record(
                INCOMPLETE_PREVIEW_ID,
                _candidate("未完成主题"),
                status="previewed",
            ),
        )
    return state


def _save_teacher_source(state: DesktopStateStore) -> dict:
    value = _candidate("教师修订后的工业废水证据链")
    value["unknowns"].append("样品浓度和评价条件仍需课堂核验")
    return BlueprintDraftService(state).save(
        PREVIEW_ID,
        PREVIEW_ID,
        candidate_revision(_candidate()),
        value,
        note="课堂活动先核对材料关系，再决定是否改写任务。",
    )


def _save_ai_source(
    state: DesktopStateStore,
    *,
    status: str = "completed",
) -> dict:
    record = {
        "kind": REVIEW_KIND,
        "review_id": "BLUEPRINT-REVIEW-PREPARATION-1",
        "preview_id": PREVIEW_ID,
        "source_candidate_revision": candidate_revision(_candidate()),
        "status": status,
        "result": {
            "candidate": _candidate("AI修订后的工业废水证据链"),
        },
        "created_at": "2026-09-08T08:02:00Z",
    }
    state.save_draft(record["review_id"], record)
    return record


def test_options_lists_original_and_completed_teacher_sources(tmp_path):
    state = _state(tmp_path)
    teacher = _save_teacher_source(state)

    options = BlueprintPreparationService(state).options()
    selected = {(item["preview_id"], item["source_id"]) for item in options}
    teacher_option = next(
        item for item in options if item["source_id"] == teacher["draft_id"]
    )

    assert (PREVIEW_ID, PREVIEW_ID) in selected
    assert (PREVIEW_ID, teacher["draft_id"]) in selected
    assert teacher_option["source_revision"] == candidate_revision(teacher["candidate"])
    assert all(item["source_revision"] for item in options)
    assert all(
        "教师修订后的工业废水证据链" in item["label"] or item["source_id"] == PREVIEW_ID
        for item in options
    )


def test_options_and_reference_include_completed_ai_source(tmp_path):
    state = _state(tmp_path)
    ai = _save_ai_source(state)
    service = BlueprintPreparationService(state)

    options = service.options()
    ai_option = next(item for item in options if item["source_id"] == ai["review_id"])
    result = service.reference(
        PREVIEW_ID,
        ai["review_id"],
        ai_option["source_revision"],
    )

    assert ai_option["preview_id"] == PREVIEW_ID
    assert "AI修订后的工业废水证据链" in ai_option["label"]
    assert result["source_id"] == ai["review_id"]
    assert result["source_revision"] == ai_option["source_revision"]
    assert "AI修订后的工业废水证据链" in result["materials"]


def test_options_exclude_unfinished_ai_source(tmp_path):
    state = _state(tmp_path)
    ai = _save_ai_source(state, status="running")

    options = BlueprintPreparationService(state).options()

    assert ai["review_id"] not in {item["source_id"] for item in options}
    with pytest.raises(BlueprintDraftError) as error:
        BlueprintPreparationService(state).reference(
            PREVIEW_ID,
            ai["review_id"],
            candidate_revision(_candidate("AI修订后的工业废水证据链")),
        )
    assert error.value.code == "blueprint_draft_source_invalid"


def test_reference_preserves_blueprint_unknowns_dependencies_and_all_evidence_without_paths(
    tmp_path,
):
    state = _state(tmp_path)
    service = BlueprintPreparationService(state)
    revision = candidate_revision(_candidate())
    before = state.snapshot()

    result = service.reference(PREVIEW_ID, PREVIEW_ID, revision)
    materials = result["materials"]

    assert result["source_id"] == PREVIEW_ID
    assert result["source_revision"] == revision
    assert "工业废水中的物质转化与证据链" in materials
    assert "完整题面、数据和解答仍须教师核验" in materials
    assert "前序依赖：Q1-A1" in materials
    assert "前序依赖：Q1-A2" in materials
    assert "上海教材·物质转化·实验现象" in materials
    assert "讲义第 2 题·条件关系" in materials
    assert "本地题库结构规则" in materials
    assert "物质转化" in materials
    assert "依赖关系" in materials
    assert materials.index("资料依据快照") < materials.index("蓝图设计参考")
    assert materials.index("本地题库结构规则") < materials.index("蓝图设计参考")
    assert materials.index("蓝图设计参考") < materials.index("前序依赖：Q1-A1")
    assert "对应Word讲义的知识结构与教材提炼内容" in materials
    assert "题目链不等于课堂讲解顺序" in materials
    for index, evidence in enumerate(_evidence(), 1):
        assert f"E{index}" in materials
        assert evidence["source_type"] in materials
        assert evidence["scope"] in materials
        for supported in evidence["supports"]:
            assert supported in materials
        assert evidence["source_document"] not in materials
        assert evidence["editable_source"]["path"] not in materials
        assert evidence["image_path"] not in materials

    assert state.snapshot() == before


def test_reference_supports_teacher_source_and_keeps_teacher_note(tmp_path):
    state = _state(tmp_path)
    teacher = _save_teacher_source(state)
    teacher_source = BlueprintDraftService(state).load_source(
        PREVIEW_ID, teacher["draft_id"]
    )

    result = BlueprintPreparationService(state).reference(
        PREVIEW_ID,
        teacher["draft_id"],
        teacher_source["source_revision"],
    )

    assert result["source_id"] == teacher["draft_id"]
    assert result["source_revision"] == teacher_source["source_revision"]
    assert "教师修订后的工业废水证据链" in result["materials"]
    assert (
        "教师修订说明：课堂活动先核对材料关系，再决定是否改写任务。"
        in result["materials"]
    )
    assert "样品浓度和评价条件仍需课堂核验" in result["materials"]


def test_reference_rejects_stale_and_incomplete_sources_without_state_write(tmp_path):
    state = _state(tmp_path, include_incomplete=True)
    service = BlueprintPreparationService(state)
    before = state.snapshot()

    with pytest.raises(BlueprintDraftError) as stale:
        service.reference(PREVIEW_ID, PREVIEW_ID, "stale-revision")
    assert stale.value.code == "blueprint_draft_stale"

    with pytest.raises(BlueprintDraftError) as incomplete:
        service.reference(
            INCOMPLETE_PREVIEW_ID,
            INCOMPLETE_PREVIEW_ID,
            candidate_revision(_candidate("未完成主题")),
        )
    assert incomplete.value.code == "blueprint_draft_source_missing"
    assert state.snapshot() == before


def test_options_and_reference_do_not_change_state_file_bytes(tmp_path):
    state = _state(tmp_path)
    ai = _save_ai_source(state)
    service = BlueprintPreparationService(state)
    ai_option = next(
        item for item in service.options() if item["source_id"] == ai["review_id"]
    )
    before = state.path.read_bytes()

    service.options()
    service.reference(PREVIEW_ID, PREVIEW_ID, candidate_revision(_candidate()))
    service.reference(PREVIEW_ID, ai["review_id"], ai_option["source_revision"])

    assert state.path.read_bytes() == before


def test_reference_rejects_materials_over_limit_without_truncating(tmp_path):
    value = _candidate()
    value["unknowns"] = ["待核验信息：" + "X" * MAX_MATERIALS]
    state = _state(tmp_path)
    state.save_draft(PREVIEW_ID, _root_record(PREVIEW_ID, value))
    service = BlueprintPreparationService(state)
    before = state.snapshot()

    with pytest.raises(BlueprintDraftError) as error:
        service.reference(PREVIEW_ID, PREVIEW_ID, candidate_revision(value))
    assert error.value.code == "blueprint_preparation_too_large"
    assert state.snapshot() == before


def test_append_reference_preserves_existing_text_and_rejects_duplicate(tmp_path):
    del tmp_path
    existing = "教师已经填写的课堂资料"
    reference = "蓝图备课参考摘录"

    combined = append_reference(existing, reference)

    assert combined == existing + "\n\n" + reference
    with pytest.raises(BlueprintDraftError) as duplicate:
        append_reference(combined, reference)
    assert duplicate.value.code == "blueprint_preparation_duplicate"
    assert existing in combined


def test_append_reference_rejects_over_limit_without_returning_truncated_text():
    existing = "原有资料"
    reference = "R" * MAX_MATERIALS

    with pytest.raises(BlueprintDraftError) as error:
        append_reference(existing, reference)
    assert error.value.code == "blueprint_preparation_too_large"
    assert len(reference) == MAX_MATERIALS
