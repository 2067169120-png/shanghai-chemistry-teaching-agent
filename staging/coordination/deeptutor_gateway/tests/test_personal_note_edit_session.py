"""Pure note-session behavior; no Qt, facade, state store or recovery imports."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib.util
from pathlib import Path
import sys

import pytest


# Loading this standalone module directly keeps the workbench package's eager
# application import out of these pure, in-memory tests.
_MODULE_PATH = Path(__file__).resolve().parents[4] / (
    "integrations/deeptutor_shchem_v1/desktop_workbench/personal_note_edit_session.py")
_SPEC = importlib.util.spec_from_file_location("_pure_personal_note_session", _MODULE_PATH)
_MODEL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODEL
_SPEC.loader.exec_module(_MODEL)
NoteEditSession = _MODEL.NoteEditSession
NoteFields = _MODEL.NoteFields
NoteEditConflict = _MODEL.NoteEditConflict
NEW_NOTE = _MODEL.NEW_NOTE


def saved_view(source="book-a", sha="sha-a"):
    return {
        "book": {"source_id": source, "revision": sha, "source_sha256": sha,
                 "source_name": "同名教材"},
        "study_revision": "study-1",
        "sections": [
            {"source_id": source, "source_sha256": sha, "section_id": "section-a",
             "revision": "section-a-1", "title": "同名章节", "pdf_start": 2, "pdf_end": 8},
            {"source_id": source, "source_sha256": sha, "section_id": "section-b",
             "revision": "section-b-1", "title": "同名章节", "pdf_start": 9, "pdf_end": 12},
        ],
        "notes": [
            {"note_id": "note-a", "section_id": "section-a", "revision": "note-a-1",
             "title": " 同名笔记 ", "text": "A\r\nB\n末尾  ", "pdf_page": 3,
             "teacher_confirmed": True},
            {"note_id": "note-b", "section_id": "section-a", "revision": "note-b-1",
             "title": " 同名笔记 ", "text": "同名的另一条", "pdf_page": 4,
             "teacher_confirmed": True},
            {"note_id": "note-c", "section_id": "section-b", "revision": "note-c-1",
             "title": " 同名笔记 ", "text": "另一章的同名条目", "pdf_page": 10,
             "teacher_confirmed": True},
        ],
    }


def edit(session, draft, **changes):
    fields = {"title": draft.fields.title, "text": draft.fields.text,
              "pdf_page": draft.fields.pdf_page}
    fields.update(changes)
    return session.update(draft.key, **fields)


def confirm(session, draft):
    return edit(session, draft, confirmed=True)


def saved_result(view, token, *, new_id="note-new", revision="note-saved"):
    result = deepcopy(view)
    result["study_revision"] = "study-saved"
    note = next((row for row in result["notes"] if row["note_id"] == token.note_id), None)
    if note is None:
        note = {"note_id": new_id, "section_id": token.section_id}
        result["notes"].append(note)
    note.update(title=token.fields.title, text=token.fields.text, pdf_page=token.fields.pdf_page,
                revision=revision, teacher_confirmed=True)
    return result


def draft_to_save(session, view, *, note_id="note-a", text="尚未保存的修订"):
    draft = session.open(view, "section-a", note_id)
    draft = edit(session, draft, text=text, title="手工新笔记" if note_id is None else draft.fields.title)
    draft = confirm(session, draft)
    return draft, session.freeze_save(draft.key, view)


def test_same_names_are_isolated_by_source_sha_section_and_note_identity():
    session = NoteEditSession()
    original = saved_view()
    untouched = deepcopy(original)
    cases = [(original, "section-a", "note-a"), (original, "section-a", "note-b"),
             (original, "section-b", "note-c"),
             (saved_view("book-b", "sha-a"), "section-a", "note-a"),
             (saved_view("book-a", "sha-new"), "section-a", "note-a")]
    keys = []
    for index, (view, section, note) in enumerate(cases):
        draft = session.open(view, section, note)
        keys.append(draft.key)
        edit(session, draft, text=f"原文不同的草稿 {index} \n")
    assert len(set(keys)) == 5
    for index, ((view, section, note), key) in enumerate(zip(cases, keys)):
        assert session.open(view, section, note).fields.text == f"原文不同的草稿 {index} \n"
        assert session.last_key(view, section) == key
    assert set(session.dirty_keys()) == set(keys)
    assert original == untouched


@pytest.mark.parametrize("changes", [
    {"title": " 同名笔记  "}, {"text": "A\nB\n末尾  "},
    {"text": " \t\r\n "}, {"title": " ", "text": "x" * 1201}, {"pdf_page": 7},
])
def test_exact_invalid_or_page_only_input_is_preserved_and_real_edit_clears_confirmation(changes):
    session, view = NoteEditSession(), saved_view()
    draft = session.open(view, "section-a", "note-a")
    assert draft.confirmed is False  # Durable confirmation is never adopted.
    draft = confirm(session, draft)
    assert draft.confirmed is True and not draft.dirty
    draft = edit(session, draft, **changes, confirmed=True)
    assert draft.dirty and draft.confirmed is False
    for name, value in changes.items():
        assert getattr(draft.fields, name) == value
    draft = confirm(session, draft)
    session.open(view, "section-a", "note-b")
    restored = session.open(view, "section-a", "note-a")
    assert restored.fields == draft.fields and restored.confirmed is True
    assert edit(session, restored).confirmed is True


def test_one_new_slot_per_section_restores_repeat_add_and_page_only_draft():
    session, view = NoteEditSession(), saved_view()
    new = session.open(view, "section-a")
    assert new.key[-1] == NEW_NOTE and new.fields == NoteFields("", "", 2)
    new = edit(session, new, pdf_page=6)
    session.open(view, "section-a", "note-b")
    assert session.open(view, "section-a").fields == NoteFields("", "", 6)
    assert session.open(view, "section-a", NEW_NOTE).key == new.key
    other = session.open(view, "section-b")
    assert other.fields == NoteFields("", "", 9) and not other.dirty
    assert session.last_key(view, "section-a") == new.key
    assert session.dirty_keys() == (new.key,)


def test_snapshots_tokens_and_inputs_are_detached_and_immutable():
    session, view = NoteEditSession(), saved_view()
    before = deepcopy(view)
    draft, token = draft_to_save(session, view)
    assert view == before
    with pytest.raises(FrozenInstanceError):
        draft.fields.text = "不能改写"
    with pytest.raises(FrozenInstanceError):
        token.confirmed = False
    draft.selection["source_id"] = "别的书"
    token.selection["revision"] = "别的PDF"
    view["notes"][0]["text"] = "外部字典后来变化"
    view["sections"][0]["pdf_start"] = 1
    assert session.get(draft.key).baseline.text == "A\r\nB\n末尾  "
    assert session.get(draft.key).fields == token.fields
    assert token.selection == {"source_id": "book-a", "revision": "sha-a"}
    assert session.get(draft.key).base_pdf_start == 2


def test_unrelated_study_revision_allows_save_with_latest_cas_without_adopting_confirmation():
    session, view = NoteEditSession(), saved_view()
    draft, old_token = draft_to_save(session, view)
    session.cancel_save(old_token)
    fresh = deepcopy(view)
    fresh["study_revision"] = "study-other-note"
    fresh["notes"][1].update(text="他条更新", revision="note-b-2")
    session.reconcile(fresh)
    assert session.get(draft.key).confirmed is True
    token = session.freeze_save(draft.key, fresh)
    assert token.study_revision == "study-other-note"
    assert token.base_note_revision == "note-a-1"
    assert token.fields.text == draft.fields.text


@pytest.mark.parametrize("change", ["note", "same_text_revision", "delete_note", "move_note",
                                          "delete_section", "range", "sha"])
def test_external_changes_keep_old_owner_text_and_page_visible_and_block_save(change):
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    session.cancel_save(token)
    draft = edit(session, draft, pdf_page=8)
    draft = confirm(session, draft)
    fresh = deepcopy(view)
    fresh["study_revision"] = "study-external"
    if change == "note":
        fresh["notes"][0].update(text="外部保存的另一版", revision="note-a-2")
    elif change == "same_text_revision":
        fresh["notes"][0].update(text=draft.fields.text, pdf_page=8, revision="note-a-2")
    elif change == "delete_note":
        fresh["notes"].pop(0)
    elif change == "move_note":
        fresh["notes"][0].update(section_id="section-b", revision="note-a-2")
    elif change == "delete_section":
        fresh["sections"].pop(0)
    elif change == "range":
        fresh["sections"][0].update(pdf_end=5, revision="section-a-2")
    elif change == "sha":
        fresh["book"].update(revision="sha-new", source_sha256="sha-new")
        for row in fresh["sections"]:
            row["source_sha256"] = "sha-new"
    session.reconcile(fresh)
    restored = session.get(draft.key)
    assert restored.fields == draft.fields and restored.baseline == draft.baseline
    assert restored.dirty and restored.conflict and not restored.confirmed
    assert session.first_dirty().key == draft.key
    assert session.last_key_for_source("book-a", "section-a") == draft.key
    with pytest.raises(NoteEditConflict):
        session.freeze_save(draft.key, fresh)
    if change == "range":
        assert restored.fields.pdf_page == 8 and restored.pdf_end == 5
        assert not restored.page_in_range
    if change == "note":
        assert restored.saved_fields.text == "外部保存的另一版"


def test_discard_is_local_and_explicitly_adopts_latest_baseline_or_clears_new():
    session, view = NoteEditSession(), saved_view()
    first, token = draft_to_save(session, view)
    second = edit(session, session.open(view, "section-a", "note-b"), text="保留别条稿")
    new = edit(session, session.open(view, "section-b"), title="新稿", text="保留新稿")
    fresh = deepcopy(view)
    fresh["notes"][0].update(text="最新保存正文", revision="note-a-2")
    session.reconcile(fresh)
    restored = session.discard(first.key, fresh)
    assert restored.fields.text == "最新保存正文" and not restored.dirty and not restored.confirmed
    assert not session.is_save_active(token)
    assert session.acknowledge_save(token, saved_result(view, token)) is None
    assert session.get(second.key).fields == second.fields
    assert session.get(new.key).fields == new.fields
    cleared = session.discard(new.key)
    assert cleared.fields == NoteFields("", "", 9) and not cleared.dirty
    assert session.dirty_keys() == (second.key,)


def test_discard_missing_owner_does_not_silently_recreate_it_or_touch_another_draft():
    session, view = NoteEditSession(), saved_view()
    first, _ = draft_to_save(session, view)
    second = edit(session, session.open(view, "section-b"), title="另一节")
    missing = deepcopy(view)
    missing["sections"].pop(0)
    assert session.discard(first.key, missing) is None
    assert session.get(first.key) is None
    assert session.get(second.key).fields == second.fields


def test_receipt_saves_only_frozen_owner_and_preserves_later_typing_and_other_drafts():
    session, view = NoteEditSession(), saved_view()
    first, token = draft_to_save(session, view)
    later = edit(session, first, text="提交后继续输入 \r\n ")
    second = edit(session, session.open(view, "section-a", "note-b"), text="另一条未存")
    result = saved_result(view, token)
    acknowledged = session.acknowledge_save(token, result)
    assert acknowledged.key == first.key
    assert acknowledged.baseline == token.fields
    assert acknowledged.fields == later.fields and acknowledged.dirty and not acknowledged.confirmed
    assert acknowledged.base_note_revision == "note-saved"
    assert session.get(second.key).fields == second.fields
    assert session.last_key(view, "section-a") == second.key
    assert session.acknowledge_save(token, result) is None


def test_new_receipt_rekeys_exact_new_note_without_clearing_other_section_new_slot():
    session, view = NoteEditSession(), saved_view()
    new, token = draft_to_save(session, view, note_id=None, text="新笔记原文")
    other = edit(session, session.open(view, "section-b"), title="另一节新稿")
    result = saved_result(view, token)
    acknowledged = session.acknowledge_save(token, result, "note-new")
    assert acknowledged.key == ("book-a", "sha-a", "section-a", "note-new")
    assert acknowledged.fields == token.fields and not acknowledged.dirty
    assert session.get(new.key) is None
    assert session.get(other.key).fields == other.fields
    assert session.last_key(view, "section-a") == acknowledged.key
    assert session.open(result, "section-a").fields == NoteFields("", "", 2)


def test_wrong_or_ambiguous_receipt_never_clears_input():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view, note_id=None)
    result = saved_result(view, token)
    wrong = deepcopy(result)
    wrong["notes"][-1]["text"] += " "
    assert session.acknowledge_save(token, wrong, "note-new") is None
    duplicate = deepcopy(result["notes"][-1])
    duplicate["note_id"] = "also-new"
    result["notes"].append(duplicate)
    assert session.acknowledge_save(token, result) is None
    assert session.acknowledge_save(token, result, "note-a") is None
    assert session.get(draft.key).fields == draft.fields and session.get(draft.key).dirty


def test_discard_and_reopen_same_key_make_old_token_ineligible():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    session.discard(draft.key)
    replacement = edit(session, session.open(view, "section-a", "note-a"), text="后来重新录入")
    assert not session.is_save_active(token)
    assert not session.mark_save_started(token)
    assert session.acknowledge_save(token, saved_result(view, token)) is None
    assert session.get(draft.key).fields == replacement.fields


def test_cancel_before_operation_cannot_use_external_same_text_as_a_save_receipt():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    session.cancel_save(token)
    assert not session.is_save_active(token) and not session.mark_save_started(token)
    session.reconcile(saved_result(view, token))
    restored = session.get(draft.key)
    assert restored.fields == draft.fields and restored.dirty and restored.conflict


@pytest.mark.parametrize("note_id", [None, "note-a"])
@pytest.mark.parametrize("later_text", [None, "提交后更改的原文\r\n "])
def test_started_then_stale_callback_can_reconcile_exact_readback_without_losing_new_input(note_id, later_text):
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view, note_id=note_id)
    assert session.mark_save_started(token)
    if later_text is not None:
        edit(session, draft, text=later_text)
    session.cancel_save(token)
    assert not session.is_save_active(token)
    result = saved_result(view, token)
    session.reconcile(result)
    key = ("book-a", "sha-a", "section-a", note_id or "note-new")
    restored = session.get(key)
    assert restored.baseline == token.fields and restored.conflict is None
    assert restored.fields.text == (later_text or token.fields.text)
    assert restored.dirty == (later_text is not None)


def test_newer_observed_external_version_is_not_replaced_by_an_old_success():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    assert session.mark_save_started(token)
    old_receipt = saved_result(view, token)
    external = deepcopy(old_receipt)
    external["notes"][0].update(text="保存之后外部又更新", revision="note-even-newer")
    external["study_revision"] = "study-even-newer"
    session.reconcile(external)
    assert session.acknowledge_save(token, old_receipt) is None
    restored = session.get(draft.key)
    assert restored.fields == draft.fields and restored.dirty and restored.conflict
    assert restored.saved_fields.text == "保存之后外部又更新"


def test_discard_all_revokes_started_receipts_and_close_has_no_dirty_entries():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    assert session.mark_save_started(token)
    session.discard_all()
    result = saved_result(view, token)
    session.reconcile(result)
    assert not session.dirty_keys() and session.first_dirty() is None
    assert session.get(draft.key) is None and not session.is_save_active(token)
    assert session.acknowledge_save(token, result) is None


def test_delayed_new_save_keeps_later_new_input_findable_without_stealing_section_selection():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view, note_id=None)
    edit(session, draft, text="新稿提交以后又打的字")
    selected = session.open(view, "section-a", "note-b")
    acknowledged = session.acknowledge_save(token, saved_result(view, token), "note-new")
    assert acknowledged.dirty and acknowledged.fields.text == "新稿提交以后又打的字"
    assert session.last_key(view, "section-a") == selected.key
    assert session.last_key_for_source("book-a", "section-a") == acknowledged.key


def test_receipt_cannot_cross_source_or_supersede_a_more_recent_success():
    session, view = NoteEditSession(), saved_view()
    draft, first = draft_to_save(session, view)
    wrong_source = saved_result(saved_view("book-b", "sha-b"), first)
    assert session.acknowledge_save(first, wrong_source) is None
    later = confirm(session, edit(session, draft, text="第二次提交原文"))
    second = session.freeze_save(later.key, view)
    assert session.acknowledge_save(second, saved_result(view, second, revision="note-newest"))
    newest = session.get(draft.key)
    newest = edit(session, newest, text="第二次成功以后新输入")
    assert session.acknowledge_save(first, saved_result(view, first)) is None
    assert session.get(draft.key).fields == newest.fields
    assert session.get(draft.key).base_note_revision == "note-newest"


def test_wrong_range_with_unchanged_revision_cannot_acknowledge_a_save():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    result = saved_result(view, token)
    result["sections"][0]["pdf_end"] = 4
    assert session.acknowledge_save(token, result) is None
    assert session.get(draft.key).fields == draft.fields


def test_confirmation_never_transfers_to_another_owner_and_unconfirmed_save_is_refused():
    session, view = NoteEditSession(), saved_view()
    first = confirm(session, session.open(view, "section-a", "note-a"))
    second = session.open(view, "section-a", "note-b")
    assert first.confirmed and not second.confirmed
    with pytest.raises(NoteEditConflict, match="明确确认"):
        session.freeze_save(second.key, view)
    assert session.get(first.key).confirmed


def test_cancelled_started_save_evidence_is_forgotten_by_explicit_discard():
    session, view = NoteEditSession(), saved_view()
    draft, token = draft_to_save(session, view)
    assert session.mark_save_started(token)
    session.cancel_save(token)
    session.discard(draft.key)
    newer = edit(session, session.open(view, "section-a", "note-a"), text=token.fields.text)
    session.reconcile(saved_result(view, token))
    retained = session.get(newer.key)
    assert retained.dirty and retained.conflict and retained.fields == newer.fields
