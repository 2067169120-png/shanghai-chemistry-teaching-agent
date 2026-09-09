from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    DRAFT_KIND,
    BlueprintDraftError,
    BlueprintDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    REVIEW_KIND,
    candidate_revision,
)
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    PRODUCT_PATH,
    HandoutCandidateError,
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from staging.coordination.deeptutor_gateway.tests.test_desktop_blueprint_drafts import (
    candidate,
)

PREVIEW_ID = "BLUEPRINT-EVIDENCE-PAGES"
HANDOUT_KEY = "handout-candidate-2"
HANDOUT_REVISION = "r" * 64
SOURCE_DOCUMENT = {"sha256": "d" * 64, "path": "讲义（原卷版）.docx"}
EDITABLE_SOURCE = {
    "path": "讲义（原卷版）.docx",
    "question_locators": ["word/document.xml#/w:document/w:body/w:p[12]"],
}


def _handout_evidence(label: str) -> dict:
    return {
        "source_type": "user_handout_question_reference",
        "scope": label,
        "supports": ["讲义原题"],
    }


def _provenance(
    *,
    key: str = HANDOUT_KEY,
    revision: str = HANDOUT_REVISION,
    source_document: dict | None = None,
    editable_source: dict | None = None,
) -> dict:
    return {
        "key": key,
        "revision": revision,
        "source_document": deepcopy(source_document or SOURCE_DOCUMENT),
        "editable_source": deepcopy(editable_source or EDITABLE_SOURCE),
    }


def _root_record(
    *,
    handout_evidence: list[dict] | None = None,
    provenance: list[dict] | None = None,
    include_answers: bool = False,
    all_evidence: list[dict] | None = None,
) -> dict:
    handout_evidence = deepcopy(handout_evidence or [_handout_evidence("讲义第2题")])
    provenance = deepcopy(provenance or [_provenance()])
    if all_evidence is None:
        all_evidence = [
            {"scope": "教材摘要", "supports": ["前置非讲义资料"]},
            {"scope": "课堂补充资料", "supports": ["另一项前置资料"]},
            *handout_evidence,
        ]
    return {
        "kind": "textbook_prompt_blueprint",
        "status": "completed",
        "preview": {
            "title": "讲义原页绑定测试蓝图",
            "evidence": deepcopy(all_evidence),
            "handout_reference": {
                "evidence": handout_evidence,
                "local_provenance": provenance,
                "include_answers": include_answers,
            },
        },
        "result": {"candidate": candidate()},
        "preview_id": PREVIEW_ID,
        "created_at": "2026-09-08T08:00:00Z",
    }


def _state(tmp_path: Path, **kwargs) -> DesktopStateStore:
    state = DesktopStateStore(tmp_path / "personal-state")
    state.save_draft(PREVIEW_ID, _root_record(**kwargs))
    return state


def _source_records(state: DesktopStateStore) -> None:
    root = state.snapshot()["drafts"][PREVIEW_ID]
    revision = candidate_revision(root["result"]["candidate"])
    edited = candidate()
    edited["theme_center"] = "教师草稿与 AI 修订共享原始资料映射"
    state.save_draft(
        "TEACHER-DRAFT-1",
        {
            "kind": DRAFT_KIND,
            "preview_id": PREVIEW_ID,
            "root_revision": revision,
            "candidate": edited,
            "created_at": "2026-09-08T08:02:00Z",
            "note": "仅测试来源列表",
        },
    )
    state.save_draft(
        "AI-REVIEW-1",
        {
            "kind": REVIEW_KIND,
            "preview_id": PREVIEW_ID,
            "source_candidate_revision": revision,
            "status": "completed",
            "result": {"candidate": candidate()},
            "created_at": "2026-09-08T08:03:00Z",
        },
    )


def _facade(state: DesktopStateStore, service: HandoutCandidateService):
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._state = state
    facade._handout_candidates = lambda: service
    return facade


class _Reader:
    def __init__(self, value: dict):
        self.value = value

    def get(self, key: str) -> dict:
        assert key == self.value["key"]
        return deepcopy(self.value)

    def catalog(self) -> dict:
        return {"items": [deepcopy(self.value)], "batches": [], "warnings": []}


def _item() -> dict:
    return {
        "key": HANDOUT_KEY,
        "revision": HANDOUT_REVISION,
        "batch_id": "fixture-batch",
        "package_id": "fixture-package",
        "title": "原生题组 · 第 2 题",
        "classification": "native_text_complete",
        "question_text": "合成题面，不作化学判断。",
        "answer_text": "合成非官方参考答案。",
        "printed_number": "2",
        "atomic_count": 1,
        "parent_title": "合成题组",
        "source_name": "合成讲义",
        "question_pages": [],
        "answer_pages": [],
        "blockers": [],
        "source_document": deepcopy(SOURCE_DOCUMENT),
        "editable_source": deepcopy(EDITABLE_SOURCE),
        "candidate_only": True,
    }


def _handout_service(tmp_path: Path, item: dict) -> HandoutCandidateService:
    state = DesktopStateStore(tmp_path / "candidate-state")
    reader = _Reader(item)
    return HandoutCandidateService(
        tmp_path,
        state,
        reader_factory=lambda _workspace: reader,
    )


def test_handout_binding_uses_exact_e_tail_after_non_handout_evidence(tmp_path):
    handout_a = _handout_evidence("讲义第2题")
    handout_b = _handout_evidence("讲义第3题")
    state = _state(
        tmp_path,
        handout_evidence=[handout_a, handout_b],
        provenance=[_provenance(), _provenance(key="handout-candidate-3")],
    )
    service = BlueprintDraftService(state)

    assert service.handout_binding(PREVIEW_ID, "E3")["key"] == HANDOUT_KEY
    assert service.handout_binding(PREVIEW_ID, "E4")["key"] == "handout-candidate-3"
    with pytest.raises(BlueprintDraftError) as error:
        service.handout_binding(PREVIEW_ID, "E2")
    assert error.value.code == "blueprint_evidence_page_unavailable"


@pytest.mark.parametrize("mutation", ["missing", "misordered", "incomplete", "wrong_type"])
def test_incomplete_or_non_exact_handout_binding_never_guesses(tmp_path, mutation):
    handout_a = _handout_evidence("讲义第2题")
    handout_b = _handout_evidence("讲义第3题")
    provenance = [_provenance(), _provenance(key="handout-candidate-3")]
    kwargs = {
        "handout_evidence": [handout_a, handout_b],
        "provenance": provenance,
    }
    if mutation == "missing":
        root = _root_record(**kwargs)
        root["preview"].pop("handout_reference")
    elif mutation == "misordered":
        root = _root_record(
            **kwargs,
            all_evidence=[
                {"scope": "教材摘要", "supports": ["前置非讲义资料"]},
                {"scope": "课堂补充资料", "supports": ["另一项前置资料"]},
                handout_b,
                handout_a,
            ],
        )
    elif mutation == "incomplete":
        root = _root_record(
            handout_evidence=kwargs["handout_evidence"],
            provenance=provenance[:1],
        )
    else:
        malformed = {"scope": "讲义第2题", "supports": ["同名但非绑定资料"]}
        root = _root_record(
            handout_evidence=[malformed], provenance=[_provenance()]
        )
    state = DesktopStateStore(tmp_path / mutation)
    state.save_draft(PREVIEW_ID, root)
    service = BlueprintDraftService(state)

    assert service.sources(PREVIEW_ID)[0]["handout_evidence_ids"] == []
    with pytest.raises(BlueprintDraftError) as error:
        service.handout_binding(PREVIEW_ID, "E3")
    assert error.value.code == "blueprint_evidence_page_unavailable"


def test_teacher_and_ai_sources_reuse_root_handout_e_mapping_without_state_changes(tmp_path):
    state = _state(tmp_path)
    _source_records(state)
    before = state.snapshot()

    sources = BlueprintDraftService(state).sources(PREVIEW_ID)

    assert {item["source_id"] for item in sources} == {
        PREVIEW_ID,
        "TEACHER-DRAFT-1",
        "AI-REVIEW-1",
    }
    assert all(item["handout_evidence_ids"] == ["E3"] for item in sources)
    assert state.snapshot() == before


@pytest.mark.parametrize("changed_field", ["revision", "source_document", "editable_source"])
def test_facade_rejects_current_candidate_or_source_changes(tmp_path, changed_field):
    state = _state(tmp_path)
    item = _item()
    reader = _Reader(item)
    service = _handout_service(tmp_path, item)
    facade = _facade(state, service)

    if changed_field == "revision":
        reader.value["revision"] = "n" * 64
    elif changed_field == "source_document":
        reader.value["source_document"] = {"sha256": "changed"}
    else:
        reader.value["editable_source"] = {"path": "changed.docx"}

    with pytest.raises(DesktopFacadeError) as error:
        facade.prompt_blueprint_evidence_pages(PREVIEW_ID, "E3")
    assert error.value.code == "blueprint_evidence_source_changed"


def test_answers_not_selected_cannot_be_listed_or_fetched(tmp_path):
    state = _state(tmp_path, include_answers=False)
    item = _item()
    item["question_pages"] = [{"page": 1, "path": "unused.png", "sha256": "a" * 64}]
    item["answer_pages"] = [{"page": 2, "path": "unused-answer.png", "sha256": "b" * 64}]
    service = _handout_service(tmp_path, item)
    facade = _facade(state, service)

    pages = facade.prompt_blueprint_evidence_pages(PREVIEW_ID, "E3")
    assert pages and all(page["role"] == "question" for page in pages)
    with pytest.raises(DesktopFacadeError) as error:
        facade.prompt_blueprint_evidence_page(PREVIEW_ID, "E3", "answer", 0)
    assert error.value.code == "blueprint_evidence_role_unavailable"


def test_valid_question_page_uses_existing_service_with_frozen_parameters(tmp_path):
    state = _state(tmp_path, include_answers=True)
    item = _item()
    relative = "batches/render/question-page.png"
    path = tmp_path / PRODUCT_PATH / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-question-page")
    item["question_pages"] = [
        {"page": 7, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        {"page": 8, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    ]
    item["answer_pages"] = [{"page": 9, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    service = _handout_service(tmp_path, item)
    calls = []
    original_page_bytes = service.page_bytes

    def page_bytes(key, revision, role, index):
        calls.append((key, revision, role, index))
        return original_page_bytes(key, revision, role, index)

    service.page_bytes = page_bytes
    facade = _facade(state, service)

    assert facade.prompt_blueprint_evidence_page(PREVIEW_ID, "E3", "question", 1) == b"synthetic-question-page"
    assert calls == [(HANDOUT_KEY, HANDOUT_REVISION, "question", 1)]


def test_invalid_page_index_remains_rejected_by_existing_service(tmp_path):
    state = _state(tmp_path, include_answers=True)
    item = _item()
    item["question_pages"] = [{"page": 1, "path": "unused.png", "sha256": "a" * 64}]
    service = _handout_service(tmp_path, item)
    facade = _facade(state, service)

    with pytest.raises(HandoutCandidateError) as error:
        facade.prompt_blueprint_evidence_page(PREVIEW_ID, "E3", "question", 99)
    assert error.value.code == "handout_page_missing"
