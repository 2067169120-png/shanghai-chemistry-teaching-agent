import json
from copy import deepcopy

import pytest
from test_desktop_blueprint_generation import (  # noqa: F401
    candidate,
    configured,
    preview,
)
from test_desktop_blueprint_review import ReviewTransport
from test_desktop_preparation_provider import _Transport

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    DRAFT_KIND,
    BlueprintDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_blueprint_review import (
    candidate_revision,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from staging.coordination.deeptutor_gateway.tests.test_desktop_facade import (  # noqa: F401
    desktop_paths,
)


def _teacher_ready(configured):  # noqa: F811 - imported pytest fixture
    facade, provider, local = configured
    generated = facade.generate_prompt_blueprint(
        local["preview_id"], "teacher-text", provider.revision, teacher_confirmed=True
    )
    edited = deepcopy(generated["candidate"])
    edited["theme_center"] = "教师草稿独有主题中心"
    edited["question_chain"][0]["answer_outline"] = "教师草稿独有答案规划"
    draft = BlueprintDraftService(facade.state_store).save(
        local["preview_id"],
        local["preview_id"],
        candidate_revision(generated["candidate"]),
        edited,
        note="教师草稿测试",
    )
    facade._blueprint_review_transport = ReviewTransport()
    return facade, provider, local, generated, draft, edited


@pytest.fixture
def teacher_ready(configured):  # noqa: F811 - imported pytest fixture
    return _teacher_ready(configured)


def _invoke(ready, **changes):
    facade, provider, local, _generated, draft, edited = ready
    values = {
        "preview_id": local["preview_id"],
        "source_revision": candidate_revision(edited),
        "profile_id": "teacher-text",
        "expected_profile_revision": provider.revision,
        "teacher_confirmed": True,
        "focus": "检查教师草稿",
        "source_draft_id": draft["draft_id"],
    }
    values.update(changes)
    return facade.review_prompt_blueprint(**values)


def _foreign_draft(ready):
    facade, _provider, local, generated, _draft, edited = ready
    state = facade.state_store
    records = state.snapshot()["drafts"]
    foreign_preview_id = "BLUEPRINT-FOREIGN-PREVIEW"
    foreign_root = deepcopy(records[local["preview_id"]])
    foreign_root["preview_id"] = foreign_preview_id
    state.save_draft(foreign_preview_id, foreign_root)
    return BlueprintDraftService(state).save(
        foreign_preview_id,
        foreign_preview_id,
        candidate_revision(generated["candidate"]),
        edited,
    )


def test_teacher_draft_is_sent_to_both_stages_without_mutating_sources(teacher_ready):
    facade, _provider, local, generated, draft, edited = teacher_ready
    before_root = deepcopy(facade.state_store.snapshot()["drafts"][local["preview_id"]])
    before_draft = deepcopy(facade.state_store.snapshot()["drafts"][draft["draft_id"]])

    result = _invoke(teacher_ready)
    requests = facade._blueprint_review_transport.requests
    assert len(requests) == 2
    for request in requests:
        body = request.body.decode("utf-8")
        assert "教师草稿独有主题中心" in body
        assert "教师草稿独有答案规划" in body
    assert result["source_draft_id"] == draft["draft_id"]
    assert result["source_candidate_revision"] == candidate_revision(edited)
    assert result["root_candidate_revision"] == candidate_revision(
        generated["candidate"]
    )
    assert result["chemistry_correctness_verified"] is False
    assert result["teacher_review_required"] is True
    assert result["publication_allowed"] is False
    assert result["bank_ingest_allowed"] is False
    records = facade.state_store.snapshot()["drafts"]
    assert records[local["preview_id"]] == before_root
    assert records[draft["draft_id"]] == before_draft


def test_same_hash_drafts_have_separate_history_and_resume_identity(teacher_ready):
    facade, provider, local, generated, first, edited = teacher_ready
    second = BlueprintDraftService(facade.state_store).save(
        local["preview_id"],
        local["preview_id"],
        candidate_revision(generated["candidate"]),
        edited,
        note="第二条相同内容草稿",
    )
    assert first["draft_id"] != second["draft_id"]
    source_revision = candidate_revision(edited)

    result = _invoke(teacher_ready, source_draft_id=first["draft_id"])
    assert facade.prompt_blueprint_reviews(
        local["preview_id"], source_revision, source_draft_id=first["draft_id"]
    )[0]["review_id"] == result["review_id"]
    assert (
        facade.prompt_blueprint_reviews(
            local["preview_id"], source_revision, source_draft_id=second["draft_id"]
        )
        == []
    )
    assert facade.prompt_blueprint_reviews(local["preview_id"], source_revision) == []

    borrowed = len(provider.borrow_calls)
    requests = len(facade._blueprint_review_transport.requests)
    with pytest.raises(DesktopFacadeError):
        _invoke(
            teacher_ready,
            source_draft_id=second["draft_id"],
            resume_review_id=result["review_id"],
        )
    assert len(provider.borrow_calls) == borrowed
    assert len(facade._blueprint_review_transport.requests) == requests


@pytest.mark.parametrize("invalid_source", ["stale", "cross_preview", "non_teacher"])
def test_invalid_teacher_source_is_rejected_before_key_access(
    teacher_ready, invalid_source
):
    facade, provider, local, generated, draft, edited = teacher_ready
    values = {
        "source_revision": candidate_revision(edited),
        "source_draft_id": draft["draft_id"],
    }
    if invalid_source == "stale":
        values["source_revision"] = "stale-teacher-draft-revision"
    elif invalid_source == "cross_preview":
        foreign = _foreign_draft(teacher_ready)
        values["source_draft_id"] = foreign["draft_id"]
        values["source_revision"] = candidate_revision(foreign["candidate"])
    else:
        values["source_draft_id"] = local["preview_id"]
        values["source_revision"] = candidate_revision(generated["candidate"])
    before = facade.state_store.snapshot()
    borrowed = len(provider.borrow_calls)
    requests = len(facade._blueprint_review_transport.requests)

    with pytest.raises(DesktopFacadeError):
        _invoke(teacher_ready, **values)

    assert len(provider.borrow_calls) == borrowed
    assert len(facade._blueprint_review_transport.requests) == requests
    assert facade.state_store.snapshot() == before


def test_completed_ai_revision_can_be_saved_as_a_teacher_draft(teacher_ready):
    facade, _provider, local, _generated, _draft, _edited = teacher_ready
    result = _invoke(teacher_ready)
    service = BlueprintDraftService(facade.state_store)
    saved = service.save(
        local["preview_id"],
        result["review_id"],
        candidate_revision(result["candidate"]),
        result["candidate"],
        note="保留 AI 修订稿供教师继续编辑",
    )

    assert saved["kind"] == DRAFT_KIND
    assert saved["source_id"] == result["review_id"]
    assert saved["source_revision"] == candidate_revision(result["candidate"])
    assert saved["candidate"] == result["candidate"]
    sources = {
        item["source_id"]: item for item in service.sources(local["preview_id"])
    }
    assert sources[saved["draft_id"]]["source_kind"] == DRAFT_KIND
    assert sources[saved["draft_id"]]["candidate"] == result["candidate"]


def test_teacher_draft_resume_after_revision_failure_sends_only_one_stage(
    teacher_ready,
):
    facade, _provider, local, _generated, draft, edited = teacher_ready
    source_revision = candidate_revision(edited)

    class FailsSecond(ReviewTransport):
        def send(self, request, **kwargs):
            name = json.loads(request.body)["text"]["format"]["name"]
            if name == "shchem_blueprint_revision_v1":
                records = facade.prompt_blueprint_reviews(
                    local["preview_id"],
                    source_revision,
                    source_draft_id=draft["draft_id"],
                )
                assert len(records) == 1
                assert records[0]["diagnosis_result"]["candidate"]["issues"]
                self.candidate = {"invalid_revision": True}
                return _Transport.send(self, request, **kwargs)
            return super().send(request, **kwargs)

    facade._blueprint_review_transport = FailsSecond()
    with pytest.raises(DesktopFacadeError):
        _invoke(teacher_ready)
    failed = facade.prompt_blueprint_reviews(
        local["preview_id"], source_revision, source_draft_id=draft["draft_id"]
    )[0]
    assert failed["status"] == "failed"
    assert failed["stage"] == "revision"
    assert failed["source_draft_id"] == draft["draft_id"]
    assert failed["root_candidate_revision"]

    facade._blueprint_review_transport = ReviewTransport()
    result = _invoke(teacher_ready, resume_review_id=failed["review_id"])
    requests = facade._blueprint_review_transport.requests
    assert len(requests) == 1
    assert (
        json.loads(requests[0].body)["text"]["format"]["name"]
        == "shchem_blueprint_revision_v1"
    )
    assert "saved_diagnosis" in requests[0].body.decode("utf-8")
    assert result["source_draft_id"] == draft["draft_id"]
    assert result["source_candidate_revision"] == source_revision
