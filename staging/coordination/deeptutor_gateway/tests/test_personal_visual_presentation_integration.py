"""Presentation must not invalidate the already reviewed personal data."""

from copy import deepcopy

from test_personal_visual_questions import (
    BATCH_ID,
)
from test_personal_visual_questions import (
    imported_visual_batch as imported_visual_batch,  # noqa: PLC0414
)


def test_detail_view_preserves_catalog_revisions_selection_and_compilation(imported_visual_batch):
    service = imported_visual_batch["service"]
    before = service.catalog()
    row = before["items"][0]
    selection = [{field: row[field] for field in ("batch_id", "key", "revision")}]
    compiled = deepcopy(service.reference(selection))
    state = deepcopy(service.state.snapshot())
    snapshot = deepcopy(service._batch(BATCH_ID)[0])

    detail = service.detail(row["batch_id"], row["key"], row["revision"])
    presentation = detail.pop("presentation")
    assert detail == row
    assert presentation["binding_revision"] == row["revision"]
    assert presentation["full_text"] == {
        field: row[field] for field in ("question_text", "shared_text", "answer_text")
    }
    assert service.catalog() == before
    assert service.reference(selection) == compiled
    assert service.state.snapshot() == state
    assert service._batch(BATCH_ID)[0] == snapshot


def test_detail_does_not_accept_an_old_source_revision(imported_visual_batch):
    import pytest

    from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
        PersonalVisualQuestionError,
    )

    service = imported_visual_batch["service"]
    row = service.catalog()["items"][0]
    with pytest.raises(PersonalVisualQuestionError):
        service.detail(row["batch_id"], row["key"], "old-revision")
