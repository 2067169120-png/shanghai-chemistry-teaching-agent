"""Temporary source references, existing services and scoped transactions."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from threading import Event

import pytest
from test_desktop_mixed_paper_service import setup, _add_word, _core_item, _catalog
from mixed_pagination_test_support import paginate_and_read
from test_mixed_paper_draft_undo import request, initial_payload
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary, PREFIX
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DesktopStateError, DraftConflictError
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError, _ACTIVE
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup, practice_request
from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection, TaskPaperSession
from test_personal_visual_questions import imported_visual_batch, BATCH_ID
from test_visual_mixed_paper_integration import _change_source
from mixed_pagination_test_support import synthetic_pagination


def use_builders(session, original):
    for name in ("_core_bundle_builder", "_docx_builder", "_word_validator", "_core_context"):
        setattr(session.service, name, getattr(original, name))
    return session


@pytest.fixture
def paper(setup):
    service, store, words, _, _ = setup
    service.facade._reader_stop_event = Event()
    store.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    library = IndependentPaperLibrary(service.facade)
    session = use_builders(library.create(), service)
    editor = session.open_mixed_paper_draft_session()
    editor.read(session.paper_basket_projection())
    return service, store, words, library, session, editor


def test_public_basket_changes_cannot_remove_add_or_reorder_saved_paper_and_real_export_path(paper):
    service, store, _, library, session, editor = paper
    before = editor.current.record
    refs = deepcopy(session.basket())
    store.clear_basket()
    store.add_to_basket({"key": "unrelated-new-selection", "title_zh": "不属于此卷"})
    reopened = use_builders(library.open(session.paper_id), service)
    current = reopened.open_mixed_paper_draft_session()
    current.read(reopened.paper_basket_projection())
    assert current.current.record == before and reopened.basket() == refs
    payload = deepcopy(before["payload"])
    payload["order"].reverse()
    removed = payload["order"].pop()
    payload["excluded"].append(removed)
    payload["settings"][payload["order"][0]]["points"] = 7.5
    current.save(payload)
    from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import MixedPaperComposerModel
    model = MixedPaperComposerModel()
    model.merge(reopened.paper_basket_projection())
    assert model.restore(payload)
    details = model.details(duration_minutes=40)
    assert [row["key"] for row in details["rows"]] == payload["order"]
    preview = paginate_and_read(reopened.service, reopened.create_paper_preview(request(reopened.service, current)))
    reopened.approve_paper_preview(preview.preview_id, preview.preview_hash)
    result = reopened.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert result["status"] == "completed" and len(result["artifacts"]) == 4
    assert all(Path(row["path"]).is_file() for row in result["artifacts"])
    assert [row["key"] for row in preview.preview_model["sections"]] == payload["order"]
    assert [row["key"] for row in store.basket()] == ["unrelated-new-selection"]
    assert DRAFT_ID not in store.snapshot()["drafts"] and _ACTIVE not in store.snapshot()["drafts"]


def test_two_papers_and_task_have_separate_drafts_active_previews_and_approval(paper, tmp_path):
    service, store, _, library, first, editor = paper
    second = use_builders(library.create(), service)
    exam = example(tmp_path / "exam.xlsx")[2]
    task = freeze_selection(create_followup(exam, "1", ["S0001"], "合成目标", date.today().isoformat()),
                            store.basket(), [store.basket()[-1]["key"]])
    task_session = use_builders(TaskPaperSession(service.facade, task), service)
    task_preview = paginate_and_read(task_session.service, task_session.create_paper_preview(
        practice_request(task, task_session.basket(), task_session.paper_basket_projection())))
    task_session.approve_paper_preview(task_preview.preview_id, task_preview.preview_hash)
    previews = []
    for session in (first, second):
        edit = session.open_mixed_paper_draft_session()
        edit.read(session.paper_basket_projection())
        preview = paginate_and_read(session.service, session.create_paper_preview(request(session.service, edit)))
        session.approve_paper_preview(preview.preview_id, preview.preview_hash)
        previews.append(preview)
    store.clear_basket()
    for session, preview in zip((first, second), previews):
        assert session.export_paper_preview(preview.preview_id, preview.preview_hash)["status"] == "completed"
        with pytest.raises(MixedPaperError):
            session.service._load(task_preview.preview_id)
    assert task_session.export_paper_preview(task_preview.preview_id, task_preview.preview_hash)["status"] == "completed"
    changed = deepcopy(editor.current.record["payload"])
    changed["settings_ui"]["title"] = "新版标题"
    editor.save(changed)
    with pytest.raises(DraftConflictError):
        first.export_paper_preview(previews[0].preview_id, previews[0].preview_hash)
    assert second.export_paper_preview(previews[1].preview_id, previews[1].preview_hash)["status"] == "completed"


@pytest.mark.parametrize("target", ["basket", "window", "foreign", "revision", "delete", "sources"])
def test_scope_cannot_write_foreign_state(paper, target):
    _, store, _, _, session, _ = paper
    frozen = store.path.read_bytes()
    def illegal(value):
        if target == "basket": value["basket"].clear()
        elif target == "window": value["window"] = {"geometry": "foreign"}
        elif target == "foreign": value["drafts"]["paper-current"] = {"title": "foreign"}
        elif target == "revision": value["draft_revisions"].clear()
        elif target == "delete": value["drafts"].pop(DRAFT_ID)
        else: value["drafts"][DRAFT_ID]["source_basis"] = {}
    with pytest.raises(DesktopStateError): session.state_store._update(illegal)
    assert store.path.read_bytes() == frozen


@pytest.mark.parametrize("change", ["revision", "range", "labels", "catalog"])
def test_changed_source_or_tag_dependencies_block_reopen_preview_and_old_export(paper, change):
    service, store, words, library, session, editor = paper
    preview = paginate_and_read(session.service, session.create_paper_preview(request(session.service, editor)))
    session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    if change == "revision": words.row["revision"] = "changed"
    elif change == "range": words.row["question_blocks"].append({"index": 4, "text": "changed", "assets": []})
    elif change == "labels": words.row["attributes"] = {"revision": "new-tags", "primary_knowledge": "K03"}
    else: words._read_attribute_catalog = lambda: {"changed": True}
    frozen = store.path.read_bytes()
    for operation in (lambda: library.open(session.paper_id), lambda: session.paper_basket_projection(),
                      lambda: session.export_paper_preview(preview.preview_id, preview.preview_hash)):
        with pytest.raises((DesktopStateError, MixedPaperError)): operation()
    assert store.path.read_bytes() == frozen


def test_core_missing_legacy_snapshot_is_pinned_and_later_content_changes_block(paper):
    service, store, _, library, _, _ = paper
    rows = store.basket()
    rows[0].pop("data_snapshot_id")
    store._update(lambda value: value.__setitem__("basket", rows))
    session = library.create()
    assert session.basket()[0]["data_snapshot_id"]
    changed = _catalog("master")
    changed["papers"][0]["theme_groups"][0]["atomic_chain"].pop()
    service.facade.paper_theme_catalog = lambda scope: changed if scope == "master" else _catalog(scope)
    with pytest.raises(DraftConflictError): library.open(session.paper_id)


@pytest.mark.parametrize("operation", ["edit", "aba", "references"])
def test_concurrent_changes_fail_closed_without_overwrite(paper, operation):
    _, store, _, _, session, editor = paper
    other = DesktopStateStore(store.root)
    key = session.paper_id if operation == "references" else session.paper_id + ":" + DRAFT_ID
    before = other.draft_snapshot(key).record
    changed = deepcopy(before)
    if operation == "references": changed["created_at"] = "changed"
    else: changed["payload"]["settings_ui"]["title"] = "另一窗口标题"
    other.save_draft(key, changed)
    if operation == "aba": other.save_draft(key, before)
    frozen = store.path.read_bytes()
    with pytest.raises(DraftConflictError): editor.save(editor.current.record["payload"])
    assert store.path.read_bytes() == frozen and editor.current is None


@pytest.mark.parametrize("when", ["before", "after"])
def test_uncertain_save_is_not_retried_and_reopen_observes_disk(paper, monkeypatch, when):
    _, store, _, library, session, editor = paper
    original = store._write_unlocked
    calls = []
    def failed(value, **kwargs):
        calls.append(1)
        if when == "after": original(value, **kwargs)
        raise OSError("synthetic uncertain write")
    monkeypatch.setattr(store, "_write_unlocked", failed)
    changed = deepcopy(editor.current.record["payload"])
    changed["settings_ui"]["title"] = "不确定保存"
    with pytest.raises(DesktopStateError): editor.save(changed)
    with pytest.raises(DesktopStateError): editor.save(changed)
    assert calls == [1]
    monkeypatch.setattr(store, "_write_unlocked", original)
    reopened = library.open(session.paper_id)
    saved = reopened.state_store.draft_snapshot(DRAFT_ID).record
    assert (saved["payload"] == changed) == (when == "after")


def test_explicit_legacy_continue_retains_exact_old_record_after_basket_clear(paper):
    service, store, _, library, _, _ = paper
    legacy = service.open_draft_session()
    projection = service.projection()
    legacy.read(projection)
    payload = initial_payload(projection)
    payload["order"].reverse()
    legacy.save(payload)
    old = store.draft_snapshot(DRAFT_ID)
    store.clear_basket()
    assert any(row["id"] == DRAFT_ID and row["legacy"] for row in library.list())
    session = library.create(legacy_id=DRAFT_ID)
    assert session.state_store.draft_snapshot(DRAFT_ID).record["payload"] == payload
    assert store.draft_snapshot(DRAFT_ID) == old
    assert library.open(session.paper_id).basket() == session.basket()
    assert store.basket() == []


def test_invalid_legacy_record_is_never_repaired_by_new_or_continue(paper):
    _, store, _, library, _, _ = paper
    store.save_draft(DRAFT_ID, {"payload": {"schema_version": "future-version"}})
    old = store.draft_snapshot(DRAFT_ID)
    with pytest.raises(DesktopStateError): library.create(legacy_id=DRAFT_ID)
    library.create()
    assert store.draft_snapshot(DRAFT_ID) == old


def test_basket_change_during_create_cannot_commit_a_different_selection(paper, monkeypatch):
    service, store, words, library, _, _ = paper
    original = words._resolve
    before = len(library.list())
    def changed(selections):
        result = original(selections)
        store.clear_basket()
        return result
    monkeypatch.setattr(words, "_resolve", changed)
    with pytest.raises(DraftConflictError): library.create()
    assert len(library.list()) == before


def test_actual_visual_source_reopens_exports_without_public_basket_and_crop_changes_block(imported_visual_batch):
    # The imported fixture uses a local deterministic adapter, never a network model.
    context = imported_visual_batch
    facade = context["facade"]
    rows = context["service"].catalog(BATCH_ID)["items"]
    facade.add_personal_visual_questions_to_basket([rows[0]])
    library = facade.independent_paper_library()
    session = library.create()
    facade.state_store.clear_basket()
    reopened = library.open(session.paper_id)
    reopened.service._pagination_builder = synthetic_pagination
    editor = reopened.open_mixed_paper_draft_session()
    editor.read(reopened.paper_basket_projection())
    assert len(editor.items) == 1 and next(iter(editor.items.values()))["kind"] == "personal_visual_theme"
    preview = paginate_and_read(reopened.service, reopened.create_paper_preview(request(reopened.service, editor)))
    reopened.approve_paper_preview(preview.preview_id, preview.preview_hash)
    result = reopened.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert len(result["artifacts"]) == 4
    _change_source(context, "crop")
    frozen = facade.state_store.path.read_bytes()
    with pytest.raises(Exception, match="变化|过期|重新"):
        library.open(session.paper_id)
    with pytest.raises(Exception, match="变化|过期|重新"):
        reopened.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert facade.state_store.path.read_bytes() == frozen


@pytest.mark.parametrize("change", ["draft", "active", "record", "references"])
def test_final_approval_uses_fresh_parent_transaction_and_preserves_interleaved_write(paper, monkeypatch, change):
    _, store, _, _, session, editor = paper
    preview = paginate_and_read(session.service, session.create_paper_preview(request(session.service, editor)))
    original = store._update
    other = DesktopStateStore(store.root)
    writes = []
    def interleaved(operation, **kwargs):
        local_key = {"draft": DRAFT_ID, "active": _ACTIVE, "record": preview.preview_id}.get(change)
        key = session.paper_id + ":" + local_key if local_key else session.paper_id
        changed = other.draft_snapshot(key).record
        if change == "draft": changed["payload"]["settings_ui"]["title"] = "另一窗口标题"
        elif change == "active": changed["preview_hash"] = "a" * 64
        elif change == "record": changed["pages_read"] = []
        else: changed["created_at"] = "changed"
        other.save_draft(key, changed)
        writes.append(other.path.read_bytes())
        return original(operation, **kwargs)
    monkeypatch.setattr(store, "_update", interleaved)
    with pytest.raises((DesktopStateError, MixedPaperError)):
        session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    assert len(writes) == 1 and store.path.read_bytes() == writes[0]


def test_preview_identity_owned_by_another_paper_cannot_be_copied_into_scope(paper):
    service, store, _, library, session, editor = paper
    preview = session.create_paper_preview(request(session.service, editor))
    foreign = library.create()
    original = session.state_store.snapshot()["drafts"][preview.preview_id]
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError):
        foreign.state_store.save_draft(preview.preview_id, original)
    assert store.path.read_bytes() == frozen


@pytest.mark.parametrize("change", ["missing_source_basis", "missing_key", "unknown_version", "missing_draft"])
def test_incomplete_independent_draft_cannot_silently_rebase_or_repair(paper, change):
    _, store, _, library, session, _ = paper
    key = session.paper_id + ":" + DRAFT_ID
    old = store.draft_snapshot(key).record
    if change == "missing_source_basis": old.pop("source_basis")
    elif change == "unknown_version": old["payload"]["schema_version"] = "future"
    elif change == "missing_key":
        removed = old["payload"]["order"].pop()
        old["payload"]["settings"].pop(removed)
    if change == "missing_draft": store._update(lambda value: value["drafts"].pop(key))
    else: store.save_draft(key, old)
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError): library.open(session.paper_id)
    with pytest.raises(DesktopStateError): session.paper_basket_projection()
    assert store.path.read_bytes() == frozen


def test_real_word_reader_detects_teacher_label_and_range_changes_without_touching_original(tmp_path):
    from runtime.deeptutor_shchem.independent_paper_fixture import seed
    facade, source, rows, providers = seed(tmp_path / "actual-word")
    try:
        original = source.read_bytes()
        library = facade.independent_paper_library()
        first = library.create()
        row = rows[0]
        options = facade.word_question_attribute_options(row["key"], row["revision"])
        facade.word_question_save_attributes(row["key"], row["revision"],
            {"teacher_note": "合成教师标签依据已修改"},
            expected_attribute_revision=options["attributes"]["revision"],
            expected_stored_revision=options["stored_revision"])
        frozen = facade.state_store.path.read_bytes()
        with pytest.raises(DraftConflictError): library.open(first.paper_id)
        assert facade.state_store.path.read_bytes() == frozen
        second = library.create()
        facade.word_question_update_range(row["key"], row["revision"],
            block_start=row["question_blocks"][0]["index"], question_end=row["question_blocks"][-1]["index"],
            answer_start=row["answer_blocks"][0]["index"], block_end=row["answer_blocks"][-1]["index"],
            context_start=1, context_end=1)
        frozen = facade.state_store.path.read_bytes()
        with pytest.raises(Exception, match="变化|过期|重新"):
            library.open(second.paper_id)
        assert facade.state_store.path.read_bytes() == frozen
        assert source.read_bytes() == original and providers.calls == 0
    finally:
        facade.shutdown()
