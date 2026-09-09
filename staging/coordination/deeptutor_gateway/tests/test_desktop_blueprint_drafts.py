from __future__ import annotations

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    BlueprintDraftError,
    BlueprintDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    REVIEW_KIND,
    candidate_revision,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

PREVIEW_ID = "BLUEPRINT-PREVIEW-1"
SECOND_PREVIEW_ID = "BLUEPRINT-PREVIEW-2"


def candidate() -> dict:
    return {
        "theme_center": "工业废水中的物质转化与证据链",
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


def _root_record(
    preview_id: str,
    value: dict,
    *,
    status: str = "completed",
    evidence: list[dict] | None = None,
) -> dict:
    if evidence is None:
        evidence = [
            {"scope": "教材摘要一", "supports": ["物质转化"]},
            {"scope": "讲义摘要二", "supports": ["实验条件"]},
        ]
    return {
        "kind": "textbook_prompt_blueprint",
        "status": status,
        "preview": {
            "title": "工业废水主题",
            "evidence": evidence,
        },
        "result": {"candidate": deepcopy(value)},
        "preview_id": preview_id,
        "created_at": "2026-09-08T08:00:00Z",
    }


def _state(tmp_path, *, second_preview: bool = False) -> DesktopStateStore:
    state = DesktopStateStore(tmp_path)
    state.save_draft(PREVIEW_ID, _root_record(PREVIEW_ID, candidate()))
    if second_preview:
        other = candidate()
        other["theme_center"] = "第二份主题中的独立语境"
        state.save_draft(
            SECOND_PREVIEW_ID,
            _root_record(SECOND_PREVIEW_ID, other),
        )
    return state


def _review_record(
    preview_id: str,
    review_id: str,
    value: dict,
    *,
    source_revision: str,
    status: str = "completed",
    created_at: str = "2026-09-08T08:01:00Z",
) -> dict:
    return {
        "kind": REVIEW_KIND,
        "review_id": review_id,
        "preview_id": preview_id,
        "source_candidate_revision": source_revision,
        "status": status,
        "result": {"candidate": deepcopy(value)},
        "created_at": created_at,
    }


def _legal_edit(value: dict) -> dict:
    edited = deepcopy(value)
    edited["theme_center"] = "教师改写后的工业废水证据链"
    edited["shared_material_plan"][0]["purpose"] = "教师补充的样品背景说明"
    edited["shared_material_plan"][0]["evidence_refs"] = ["E2"]
    edited["question_chain"][0]["material_refs"] = ["M2"]
    edited["question_chain"][1]["depends_on"] = []
    edited["question_chain"][1]["answer_outline"] = "教师补充必要条件并留待核验。"
    edited["question_chain"][2]["depends_on"] = ["Q1-A1"]
    return edited


def test_sources_include_completed_root_review_and_teacher_drafts(tmp_path):
    state = _state(tmp_path)
    root = candidate()
    root_revision = candidate_revision(root)
    reviewed = deepcopy(root)
    reviewed["theme_center"] = "AI修订后的工业废水证据链"
    state.save_draft(
        "review-1",
        _review_record(
            PREVIEW_ID,
            "review-1",
            reviewed,
            source_revision=root_revision,
        ),
    )
    state.save_draft(
        "review-old",
        _review_record(
            PREVIEW_ID,
            "review-old",
            reviewed,
            source_revision="old-root-revision",
        ),
    )
    state.save_draft(
        "review-running",
        _review_record(
            PREVIEW_ID,
            "review-running",
            reviewed,
            source_revision=root_revision,
            status="running",
        ),
    )

    service = BlueprintDraftService(state)
    teacher = service.save(
        PREVIEW_ID,
        PREVIEW_ID,
        root_revision,
        _legal_edit(root),
        note="保留原编号，教师后续复核化学事实。",
    )

    sources = service.sources(PREVIEW_ID)
    by_id = {item["source_id"]: item for item in sources}
    assert set(by_id) == {PREVIEW_ID, "review-1", teacher["draft_id"]}
    assert "review-old" not in by_id and "review-running" not in by_id

    assert by_id[PREVIEW_ID]["source_revision"] == root_revision
    assert by_id[PREVIEW_ID]["source_label"] == "原始生成蓝图"
    assert by_id["review-1"]["source_revision"] == candidate_revision(reviewed)
    assert "AI修订" in by_id["review-1"]["source_label"]
    assert by_id[teacher["draft_id"]]["source_revision"] == candidate_revision(
        teacher["candidate"]
    )
    assert "教师草稿" in by_id[teacher["draft_id"]]["source_label"]
    for source in sources:
        assert source["root_revision"] == root_revision
        assert source["evidence"] == state.snapshot()["drafts"][PREVIEW_ID]["preview"]["evidence"]


def test_save_is_append_only_reopens_and_keeps_metadata_non_certifying(tmp_path):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)
    root = service.load_source(PREVIEW_ID, PREVIEW_ID)
    before = state.snapshot()
    edited = _legal_edit(root["candidate"])

    saved = service.save(
        PREVIEW_ID,
        PREVIEW_ID,
        root["source_revision"],
        edited,
        note="  教师复核说明  ",
    )

    after = state.snapshot()
    assert after["drafts"][PREVIEW_ID] == before["drafts"][PREVIEW_ID]
    assert saved["draft_id"].startswith("BLUEPRINT-TEACHER-")
    assert saved["kind"] == "textbook_blueprint_teacher_draft"
    assert saved["preview_id"] == PREVIEW_ID
    assert saved["root_revision"] == root["root_revision"]
    assert saved["source_id"] == PREVIEW_ID
    assert saved["source_revision"] == root["source_revision"]
    assert saved["candidate"] == edited
    assert saved["note"] == "教师复核说明"
    assert saved["candidate_only"] is True
    assert saved["teacher_review_required"] is True
    assert saved["chemistry_correctness_verified"] is False
    assert saved["publication_allowed"] is False
    assert saved["bank_ingest_allowed"] is False
    assert saved["candidate"].get("chemistry_correctness_verified") is None

    reopened = BlueprintDraftService(DesktopStateStore(state.root)).load_source(
        PREVIEW_ID,
        saved["draft_id"],
        candidate_revision(saved["candidate"]),
    )
    assert reopened["candidate"] == edited
    assert reopened["root_revision"] == root["root_revision"]


def test_branch_saves_are_independent_and_teacher_draft_can_be_continued(tmp_path):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)
    root = service.load_source(PREVIEW_ID, PREVIEW_ID)
    first_candidate = _legal_edit(root["candidate"])
    second_candidate = _legal_edit(root["candidate"])
    second_candidate["theme_center"] = "另一条教师改写分支"

    first = service.save(PREVIEW_ID, PREVIEW_ID, root["source_revision"], first_candidate)
    second = service.save(PREVIEW_ID, PREVIEW_ID, root["source_revision"], second_candidate)
    continued_candidate = deepcopy(first_candidate)
    continued_candidate["theme_center"] = "从第一条教师草稿继续修改"
    continued = service.save(
        PREVIEW_ID,
        first["draft_id"],
        candidate_revision(first["candidate"]),
        continued_candidate,
    )

    assert first["draft_id"] != second["draft_id"]
    assert continued["draft_id"] not in {first["draft_id"], second["draft_id"]}
    assert first["source_id"] == second["source_id"] == PREVIEW_ID
    assert continued["source_id"] == first["draft_id"]
    assert continued["source_revision"] == candidate_revision(first_candidate)
    assert continued["root_revision"] == root["root_revision"]
    assert state.snapshot()["drafts"][first["draft_id"]]["candidate"] == first_candidate
    assert state.snapshot()["drafts"][second["draft_id"]]["candidate"] == second_candidate
    assert (
        service.load_source(
            PREVIEW_ID,
            continued["draft_id"],
            candidate_revision(continued["candidate"]),
        )["candidate"]
        == continued_candidate
    )


def test_cross_preview_and_unfinished_sources_are_rejected(tmp_path):
    state = _state(tmp_path, second_preview=True)
    service = BlueprintDraftService(state)
    second = service.load_source(SECOND_PREVIEW_ID, SECOND_PREVIEW_ID)
    second_draft = service.save(
        SECOND_PREVIEW_ID,
        SECOND_PREVIEW_ID,
        second["source_revision"],
        _legal_edit(second["candidate"]),
    )
    state.save_draft(
        "review-pending",
        _review_record(
            PREVIEW_ID,
            "review-pending",
            candidate(),
            source_revision=candidate_revision(candidate()),
            status="running",
        ),
    )

    with pytest.raises(BlueprintDraftError) as cross_preview:
        service.load_source(PREVIEW_ID, second_draft["draft_id"])
    assert cross_preview.value.code == "blueprint_draft_source_invalid"

    assert "review-pending" not in {
        item["source_id"] for item in service.sources(PREVIEW_ID)
    }
    with pytest.raises(BlueprintDraftError) as unfinished:
        service.load_source(PREVIEW_ID, "review-pending")
    assert unfinished.value.code == "blueprint_draft_source_invalid"

    with pytest.raises(BlueprintDraftError) as missing:
        service.sources("not-a-preview")
    assert missing.value.code == "blueprint_draft_source_missing"


def test_stale_source_revision_is_rejected_before_save(tmp_path):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)
    edited = _legal_edit(candidate())

    with pytest.raises(BlueprintDraftError) as load_error:
        service.load_source(PREVIEW_ID, PREVIEW_ID, "stale-revision")
    assert load_error.value.code == "blueprint_draft_stale"

    with pytest.raises(BlueprintDraftError) as save_error:
        service.save(PREVIEW_ID, PREVIEW_ID, "stale-revision", edited)
    assert save_error.value.code == "blueprint_draft_stale"
    assert set(state.snapshot()["drafts"]) == {PREVIEW_ID}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("bad_material", "blueprint_chain_invalid"),
        ("bad_evidence", "blueprint_material_invalid"),
        ("missing_field", "blueprint_output_invalid"),
        ("not_object", "blueprint_output_invalid"),
    ],
)
def test_invalid_references_and_non_object_candidates_are_rejected(
    tmp_path, mutation, code
):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)
    root = service.load_source(PREVIEW_ID, PREVIEW_ID)
    value = deepcopy(root["candidate"])
    if mutation == "bad_material":
        value["question_chain"][0]["material_refs"] = ["MISSING-MATERIAL"]
    elif mutation == "bad_evidence":
        value["shared_material_plan"][0]["evidence_refs"] = ["E999"]
    elif mutation == "missing_field":
        value["question_chain"][0].pop("answer_outline")
    else:
        value = [value]

    with pytest.raises(BlueprintDraftError) as error:
        service.save(PREVIEW_ID, PREVIEW_ID, root["source_revision"], value)
    assert error.value.code == code
    assert set(state.snapshot()["drafts"]) == {PREVIEW_ID}


@pytest.mark.parametrize(
    "mutation",
    ["atomic_identity", "printed_identity", "question_count", "material_order", "material_count"],
)
def test_structure_identity_cannot_be_changed(mutation, tmp_path):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)
    root = service.load_source(PREVIEW_ID, PREVIEW_ID)
    value = deepcopy(root["candidate"])
    if mutation == "atomic_identity":
        value["question_chain"][0]["atomic_part_id"] = "Q1-A9"
        value["question_chain"][1]["depends_on"] = []
    elif mutation == "printed_identity":
        value["question_chain"][1]["printed_question_id"] = "Q3"
        value["question_chain"][1]["depends_on"] = []
        value["question_chain"][2]["depends_on"] = ["Q1-A2"]
    elif mutation == "question_count":
        value["question_chain"].pop()
    elif mutation == "material_order":
        value["shared_material_plan"] = list(reversed(value["shared_material_plan"]))
    else:
        value["shared_material_plan"].pop()
        for item in value["question_chain"]:
            item["material_refs"] = ["M1"]

    with pytest.raises(BlueprintDraftError) as error:
        service.save(PREVIEW_ID, PREVIEW_ID, root["source_revision"], value)
    assert error.value.code == "blueprint_draft_structure_changed"
    assert set(state.snapshot()["drafts"]) == {PREVIEW_ID}


def test_incomplete_root_or_missing_evidence_cannot_be_used(tmp_path):
    state = DesktopStateStore(tmp_path)
    state.save_draft(
        PREVIEW_ID,
        _root_record(PREVIEW_ID, candidate(), status="previewed"),
    )
    with pytest.raises(BlueprintDraftError) as unfinished:
        BlueprintDraftService(state).sources(PREVIEW_ID)
    assert unfinished.value.code == "blueprint_draft_source_missing"

    state.save_draft(
        PREVIEW_ID,
        _root_record(PREVIEW_ID, candidate(), evidence=[]),
    )
    with pytest.raises(BlueprintDraftError) as no_evidence:
        BlueprintDraftService(state).sources(PREVIEW_ID)
    assert no_evidence.value.code == "blueprint_draft_source_missing"


def test_save_requires_an_explicit_source_revision(tmp_path):
    state = _state(tmp_path)
    service = BlueprintDraftService(state)

    with pytest.raises(BlueprintDraftError) as error:
        service.save(PREVIEW_ID, PREVIEW_ID, None, candidate())
    assert error.value.code == "blueprint_draft_stale"
    assert set(state.snapshot()["drafts"]) == {PREVIEW_ID}
