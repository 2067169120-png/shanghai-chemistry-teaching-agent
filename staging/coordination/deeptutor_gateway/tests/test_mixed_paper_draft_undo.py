"""Saved mixed drafts and preview revisions; only synthetic temporary sources."""
from copy import deepcopy
import json

import pytest
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import (
    DRAFT_ID, DRAFT_SCHEMA, MixedPaperDraftSession, binding,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DesktopStateError, DraftConflictError
from test_desktop_mixed_paper_service import setup as mixed_setup, _add_word, _core_item, paginate_and_read  # noqa: F401


def initial_payload(projection):
    return {"schema_version": DRAFT_SCHEMA, "order": [row["key"] for row in projection["items"]],
            "excluded": [], "settings": {row["key"]: deepcopy(row["settings"]) for row in projection["items"]},
            "settings_ui": {"title": "合成方向练习", "subtitle": "仅用于软件测试", "mode": "daily_practice",
                            "duration_minutes": 40, "show_question_scores": False}}


def request(service, session):
    payload = deepcopy(session.current.record["payload"])
    return {"schema_version": "shchem.desktop-mixed-paper-request.v1", **payload["settings_ui"],
            "basket_sha256": service.projection()["basket_sha256"], "section_order": payload["order"],
            "settings_by_key": {key: payload["settings"][key] for key in payload["order"]},
            "draft_binding": session.check()}


@pytest.fixture
def opened(mixed_setup):
    service, store, words, _, _ = mixed_setup
    store.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    projection = service.projection()
    session = service.open_draft_session()
    session.read(projection)
    payload = initial_payload(projection)
    session.save(payload, remember=False)
    return service, store, session, payload


def edited(payload, kind):
    result = deepcopy(payload)
    key = result["order"][0]
    if kind == "remove":
        result["order"].remove(key)
        result["excluded"].append(key)
    elif kind == "move":
        result["order"] = result["order"][::-1]
    elif kind == "word_points":
        result["settings"][result["order"][-1]]["points"] = 6.5
    elif kind == "core_points":
        result["settings"][key]["score_per_atomic"] = 8
    elif kind == "space":
        result["settings"][key]["answer_space_lines"] = 4
    elif kind == "atomic":
        result["settings"][key]["atomic_settings"] = {"A1": {"score": 4, "answer_space_lines": 2}}
    elif kind == "layout":
        result["settings_ui"].update(show_question_scores=True, duration_minutes=80, mode="mock_exam", subtitle="新范围")
    return result


@pytest.mark.parametrize("kind", ["remove", "move", "word_points", "core_points", "space", "atomic", "layout"])
def test_saved_edits_undo_restore_only_current_draft_and_preserve_sources(opened, kind):
    service, store, session, payload = opened
    before = store.draft_snapshot(DRAFT_ID)
    basket = store.basket_snapshot()
    store.save_draft("paper-current", {"teacher": "independent legacy draft"})
    store.save_draft("another-lesson", {"teacher": "independent lesson"})
    original_basis = deepcopy(before.record["source_basis"])
    assert session.save(edited(payload, kind), action=kind, selected=payload["order"][0])["changed"]
    assert store.draft_snapshot(DRAFT_ID).record["payload"] == edited(payload, kind)
    # A freshly opened editor sees the committed edit; it inherits no undo.
    second = service.open_draft_session()
    assert second.read(service.projection())["record"]["payload"] == edited(payload, kind)
    assert second.undo_count == 0
    store.save_window_state(geometry="new-window", layout="new-layout")
    result = session.undo()
    assert result["selected"] == payload["order"][0]
    restored = store.draft_snapshot(DRAFT_ID)
    assert restored.record == before.record and restored.revision != before.revision
    assert restored.record["source_basis"] == original_basis
    assert store.basket_snapshot() == basket
    assert store.snapshot()["drafts"]["paper-current"] == {"teacher": "independent legacy draft"}
    assert store.snapshot()["drafts"]["another-lesson"] == {"teacher": "independent lesson"}
    assert store.window_state()["geometry"] == "new-window"


def test_multiple_undo_restores_order_removed_and_atomic_settings_exactly(opened):
    _, store, session, payload = opened
    first = edited(payload, "atomic")
    session.save(first)
    second = edited(first, "move")
    session.save(second)
    third = edited(second, "remove")
    session.save(third)
    for expected in (second, first, payload):
        session.undo()
        assert store.draft_snapshot(DRAFT_ID).record["payload"] == expected
    assert session.undo_count == 0


def test_noop_and_other_draft_updates_do_not_expire_history(opened):
    _, store, session, payload = opened
    changed = edited(payload, "move")
    session.save(changed)
    before = store.path.read_bytes()
    assert not session.save(changed)["changed"]
    other = DesktopStateStore(store.root)
    other.save_draft(DRAFT_ID, store.draft_snapshot(DRAFT_ID).record)
    assert store.path.read_bytes() == before and session.undo_count == 1
    other.save_draft("other", {"title": "external other draft"})
    session.undo()
    assert store.draft_snapshot(DRAFT_ID).record["payload"] == payload
    assert other.draft_snapshot("other").record == {"title": "external other draft"}


@pytest.mark.parametrize("operation", ["save", "private_update", "delete", "aba"])
def test_external_same_draft_changes_are_not_overwritten(opened, operation):
    service, store, session, payload = opened
    session.save(edited(payload, "move"))
    original = store.draft_snapshot(DRAFT_ID).record
    other = DesktopStateStore(store.root)
    changed = deepcopy(original)
    changed["payload"]["settings_ui"]["title"] = "另一窗口标题"
    if operation in {"save", "aba"}:
        other.save_draft(DRAFT_ID, changed)
        if operation == "aba":
            other.save_draft(DRAFT_ID, original)
    elif operation == "private_update":
        other._update(lambda value: value["drafts"].__setitem__(DRAFT_ID, changed))
    else:
        other._update(lambda value: value["drafts"].pop(DRAFT_ID))
    frozen = store.path.read_bytes()
    with pytest.raises(DraftConflictError):
        session.undo()
    assert store.path.read_bytes() == frozen and session.undo_count == 0
    assert session.read(service.projection())["record"] == other.draft_snapshot(DRAFT_ID).record


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("action", ["save", "undo"])
def test_uncertain_save_requires_reread_and_never_replays_history(opened, monkeypatch, when, action):
    service, store, session, payload = opened
    session.save(edited(payload, "move"))
    original, calls = store._write_unlocked, []
    def broken(value, **kwargs):
        calls.append(True)
        if when == "after":
            original(value, **kwargs)
        raise OSError("private synthetic failure")
    monkeypatch.setattr(store, "_write_unlocked", broken)
    with pytest.raises(DesktopStateError):
        session.undo() if action == "undo" else session.save(edited(payload, "layout"))
    assert len(calls) == 1 and session.undo_count == 0
    with pytest.raises(DesktopStateError):
        session.undo()
    assert len(calls) == 1
    monkeypatch.setattr(store, "_write_unlocked", original)
    session.read(service.projection())
    assert session.undo_count == 0


def test_read_failure_retires_history_and_preserves_raw_bad_record(opened):
    service, store, session, payload = opened
    projection = service.projection()
    session.save(edited(payload, "move"))
    valid = store.path.read_bytes()
    store.path.write_bytes(b"{broken")
    with pytest.raises(DesktopStateError):
        session.read(projection)
    assert store.path.read_bytes() == b"{broken" and session.undo_count == 0
    store.path.write_bytes(valid)
    session.read(projection)
    with pytest.raises(DesktopStateError):
        session.undo()


@pytest.mark.parametrize("corrupt", [None, "bad", {"payload": []}, {"payload": {"schema_version": "unknown"}}])
def test_corrupt_target_draft_cannot_be_overwritten_by_initialization(opened, corrupt):
    service, store, _, _ = opened
    store._update(lambda value: value["drafts"].__setitem__(DRAFT_ID, corrupt))
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError):
        service.open_draft_session().read(service.projection())
    assert store.path.read_bytes() == frozen


def test_source_projection_refresh_clears_history_without_rebinding_existing_sources(opened):
    service, store, session, payload = opened
    session.save(edited(payload, "move"))
    store.move_basket_item(store.basket()[0]["key"], 1)
    frozen = store.path.read_bytes()
    with pytest.raises(DraftConflictError):
        session.undo()
    assert store.path.read_bytes() == frozen
    projection = service.projection()
    session.read(projection)
    assert session.undo_count == 0
    fake = deepcopy(projection)
    fake["items"][0]["source_ref"]["theme_id"] = "another-identity"
    with pytest.raises(DraftConflictError, match="来源身份"):
        session.read(fake)
    assert store.path.read_bytes() == frozen


def test_window_close_reopen_restores_current_draft_without_history(opened):
    service, store, session, payload = opened
    session.save(edited(payload, "layout"))
    frozen = store.path.read_bytes()
    session.close()
    with pytest.raises(DesktopStateError):
        session.undo()
    second = service.open_draft_session()
    assert second.read(service.projection())["record"]["payload"] == edited(payload, "layout")
    assert second.undo_count == 0 and store.path.read_bytes() == frozen


def test_old_approved_preview_cannot_export_after_edit_then_undo_same_payload(opened):
    service, store, session, payload = opened
    content = service.create_preview(request(service, session))
    preview = paginate_and_read(service, content)
    service.approve(preview.preview_id, preview.preview_hash)
    original = store.draft_snapshot(DRAFT_ID).record
    session.save(edited(payload, "move"))
    session.undo()
    assert store.draft_snapshot(DRAFT_ID).record == original
    with pytest.raises(DraftConflictError):
        service.export(preview.preview_id, preview.preview_hash)
    with pytest.raises(DraftConflictError):
        service.approve(preview.preview_id, preview.preview_hash)
    fresh = paginate_and_read(service, service.create_preview(request(service, session)))
    service.approve(fresh.preview_id, fresh.preview_hash)
    result = service.export(fresh.preview_id, fresh.preview_hash)
    assert result["status"] == "completed" and len(result["artifacts"]) == 4


@pytest.mark.parametrize("tamper", ["missing_binding", "wrong_revision", "different_settings"])
def test_preview_request_must_match_saved_draft_and_revision(opened, tamper):
    service, store, session, _ = opened
    chosen = request(service, session)
    if tamper == "missing_binding":
        chosen.pop("draft_binding")
    elif tamper == "wrong_revision":
        chosen["draft_binding"]["revision"] = "0" * 32
    else:
        chosen["show_question_scores"] = True
    frozen = store.path.read_bytes()
    with pytest.raises(DraftConflictError):
        service.create_preview(chosen)
    assert store.path.read_bytes() == frozen


def test_draft_change_during_preview_does_not_publish_active_candidate(opened, monkeypatch):
    service, store, session, _ = opened
    chosen = request(service, session)
    original = service._word_validator
    def change(rows):
        original(rows)
        record = store.draft_snapshot(DRAFT_ID).record
        record["payload"]["settings_ui"]["title"] = "并发修改"
        DesktopStateStore(store.root).save_draft(DRAFT_ID, record)
    monkeypatch.setattr(service, "_word_validator", change)
    with pytest.raises(DraftConflictError):
        service.create_preview(chosen)
    assert not any(key.startswith("mixed-preview-") for key in store.snapshot()["drafts"])


def test_draft_version_change_does_not_invalidate_public_basket_undo(opened):
    _, store, session, payload = opened
    basket = store.open_basket_session()
    before = basket.read().rows
    basket.change(before[0]["key"], 0)
    # Independent direct draft save is valid, and must not change basket revision.
    record = store.draft_snapshot(DRAFT_ID).record
    record["payload"] = edited(payload, "layout")
    store.save_draft(DRAFT_ID, record)
    basket.undo()
    assert tuple(store.basket()) == before
    assert store.draft_snapshot(DRAFT_ID).record == record


def changed_source_projection(service):
    projection = deepcopy(service.projection())
    projection["items"][0]["source_ref"]["theme_id"] = "new-synthetic-source-identity"
    return projection


def test_source_mismatch_restart_archives_exact_old_record_without_carrying_scores(opened):
    service, store, session, payload = opened
    session.save(edited(payload, "atomic"))
    old = store.draft_snapshot(DRAFT_ID).record
    store.save_draft("another-draft", {"teacher": "keep"})
    before_basket = store.basket_snapshot()
    projection = changed_source_projection(service)
    before = store.path.read_bytes()
    with pytest.raises(DraftConflictError):
        session.read(projection)
    assert session.can_restart and store.path.read_bytes() == before
    # Merely reading or dismissing a UI confirmation performs no repair write.
    assert not session.undo_count
    result = session.restart(projection)
    assert store.draft_snapshot(result["archive_id"]).record == old
    assert store.draft_snapshot(DRAFT_ID).record["payload"]["settings"][payload["order"][0]] == projection["items"][0]["settings"]
    assert store.basket_snapshot() == before_basket
    assert store.draft_snapshot("another-draft").record == {"teacher": "keep"}
    assert not session.can_restart and not session.undo_count
    assert service.open_draft_session().read(projection)["record"] == result["record"]
    with pytest.raises(DesktopStateError):
        session.restart(projection)
    assert len([key for key in store.snapshot()["drafts"] if key.startswith("paper-mixed-before-reset-")]) == 1


@pytest.mark.parametrize("change", ["draft", "draft_aba", "basket", "basket_aba", "projection"])
def test_restart_confirmation_is_bound_to_proposed_draft_basket_and_sources(opened, change):
    service, store, session, _ = opened
    projection = changed_source_projection(service)
    with pytest.raises(DraftConflictError):
        session.read(projection)
    other = DesktopStateStore(store.root)
    if change.startswith("draft"):
        old = other.draft_snapshot(DRAFT_ID).record
        changed = deepcopy(old)
        changed["teacher_note"] = "another window"
        other.save_draft(DRAFT_ID, changed)
        if change.endswith("aba"):
            other.save_draft(DRAFT_ID, old)
    elif change.startswith("basket"):
        other.move_basket_item(other.basket()[0]["key"], 1)
        if change.endswith("aba"):
            other.move_basket_item(other.basket()[1]["key"], -1)
    else:
        projection["items"][0]["settings"]["score_per_atomic"] = 5
    frozen = store.path.read_bytes()
    with pytest.raises(DraftConflictError):
        session.restart(projection)
    assert store.path.read_bytes() == frozen and not session.can_restart


@pytest.mark.parametrize("when", ["before", "after"])
def test_restart_uncertain_write_cannot_duplicate_old_record(opened, monkeypatch, when):
    service, store, session, _ = opened
    projection = changed_source_projection(service)
    with pytest.raises(DraftConflictError):
        session.read(projection)
    original, calls = store._write_unlocked, []
    def fail(value, **kwargs):
        calls.append(True)
        if when == "after":
            original(value, **kwargs)
        raise OSError("synthetic uncertain save")
    monkeypatch.setattr(store, "_write_unlocked", fail)
    with pytest.raises(DesktopStateError):
        session.restart(projection)
    with pytest.raises(DesktopStateError):
        session.restart(projection)
    assert len(calls) == 1 and not session.can_restart
    monkeypatch.setattr(store, "_write_unlocked", original)
    archives = [key for key in store.snapshot()["drafts"] if key.startswith("paper-mixed-before-reset-")]
    assert len(archives) == (1 if when == "after" else 0)


@pytest.mark.parametrize("problem", ["missing_settings_key", "extra_settings_key", "unknown_setting"])
def test_legacy_incomplete_or_unknown_settings_require_explicit_preserved_restart(opened, problem):
    service, store, _, payload = opened
    old = store.draft_snapshot(DRAFT_ID).record
    old.pop("source_basis")
    if problem == "missing_settings_key":
        old["payload"]["settings"].pop(payload["order"][0])
    elif problem == "extra_settings_key":
        old["payload"]["settings"]["not-a-section"] = {"points": 5}
    else:
        old["payload"]["settings"][payload["order"][0]]["future_format"] = "keep"
    store.save_draft(DRAFT_ID, old)
    frozen = store.path.read_bytes()
    session = service.open_draft_session()
    projection = service.projection()
    with pytest.raises(DesktopStateError):
        session.read(projection)
    assert session.can_restart and store.path.read_bytes() == frozen
    result = session.restart(projection)
    assert store.draft_snapshot(result["archive_id"]).record == old


def test_legacy_valid_draft_without_revision_or_source_basis_can_reopen_and_undo(opened):
    service, store, _, payload = opened
    value = store.snapshot()
    value["drafts"][DRAFT_ID].pop("source_basis")
    value.pop("draft_revisions")
    store.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    session = service.open_draft_session()
    session.read(service.projection())
    assert session.current.revision.startswith("legacy:")
    session.save(payload, remember=False)
    session.save(edited(payload, "layout"))
    session.undo()
    assert store.draft_snapshot(DRAFT_ID).record["payload"] == payload


def test_history_limit_and_visual_metadata_restore_without_source_changes(opened):
    service, store, session, payload = opened
    for index in range(35):
        payload = deepcopy(payload)
        payload["settings_ui"]["subtitle"] = f"合成版式 {index}"
        session.save(payload)
    assert session.undo_count == 30
    while session.undo_count:
        session.undo()
    assert store.draft_snapshot(DRAFT_ID).record["payload"]["settings_ui"]["subtitle"] == "合成版式 4"
    # Session semantics cover the third source kind without opening any image.
    store.clear_basket()
    store.add_to_basket({"key": "visual-synthetic", "item_kind": "personal_visual_theme", "visual_selections": [{"key": "v", "revision": "v1"}]})
    projection = {"schema_version": "shchem.desktop-mixed-basket.v1", "basket_sha256": store.basket_snapshot().content_sha256,
                  "items": [{"key": "visual-synthetic", "kind": "personal_visual_theme", "source_ref": {"selections": [{"key": "v", "revision": "v1"}]},
                             "settings": {"use_source_scores": True}, "content": {}}]}
    session.read(projection)
    payload = initial_payload(projection)
    session.save(payload, remember=False)
    before = store.draft_snapshot(DRAFT_ID).record
    session.save(edited(payload, "remove"))
    session.undo()
    assert store.draft_snapshot(DRAFT_ID).record == before


@pytest.mark.parametrize("problem", ["missing_row", "content", "settings"])
def test_incomplete_fresh_projection_cannot_offer_restart(opened, problem):
    service, store, session, _ = opened
    projection = changed_source_projection(service)
    if problem == "missing_row":
        projection["items"].pop()
    else:
        projection["items"][0][problem] = None
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError):
        session.read(projection)
    assert not session.can_restart and store.path.read_bytes() == frozen


def test_draft_limit_matches_existing_public_basket_limit(tmp_path):
    store = DesktopStateStore(tmp_path)
    rows = [{"key": f"synthetic-{number}", "kind": "word_question", "source_ref": {"key": str(number)},
             "settings": {"points": 2}, "content": {}} for number in range(100)]
    store.add_many_to_basket(rows)
    projection = {"schema_version": "shchem.desktop-mixed-basket.v1", "basket_sha256": store.basket_snapshot().content_sha256, "items": rows}
    session = MixedPaperDraftSession(store)
    session.read(projection)
    session.save(initial_payload(projection), remember=False)
    frozen = store.path.read_bytes()
    projection["items"] = rows + [{**rows[0], "key": "overflow"}]
    with pytest.raises(DesktopStateError):
        session.read(projection)
    assert store.path.read_bytes() == frozen
