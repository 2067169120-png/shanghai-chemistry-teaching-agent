"""Durable, window-scoped undo; all input and output are synthetic tmp_path data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from integrations.deeptutor_shchem_v1 import desktop_state as state_module
from integrations.deeptutor_shchem_v1.desktop_state import (
    BasketConflictError, DesktopStateError, DesktopStateStore, STATE_SCHEMA,
)


def mixed_rows():
    return [
        {"key": "word:synthetic", "item_kind": "word_question", "source_zh": "合成Word来源",
         "source_ref": {"key": "native-word", "revision": "word-r1", "source_sha256": "a" * 64},
         "teacher_metadata": {"points": 4.5, "tags": ["教师标签"], "note": "保留原文"}},
        {"key": "visual:synthetic", "item_kind": "personal_visual_theme", "source_zh": "合成图片来源",
         "source_ref": {"scope": "personal", "selections": [{"key": "part-a", "revision": "v1"}]},
         "visual_selections": [{"key": "part-a", "revision": "v1"}], "pages": [1, 2]},
        {"key": "core:synthetic", "item_kind": "core_theme", "scope": "supplemental",
         "source_identity_sha256": "b" * 64, "data_snapshot_id": "c" * 64,
         "theme_id": "same-theme-id-across-scopes", "atomic_total": 2},
    ]


@pytest.fixture
def opened(tmp_path):
    store = DesktopStateStore(tmp_path / "state")
    store.add_many_to_basket(mixed_rows())
    session = store.open_basket_session()
    session.read()
    return store, session


@pytest.mark.parametrize("index", [0, 1, 2])
def test_remove_undo_restores_exact_identity_order_metadata_and_selection(opened, index):
    store, session = opened
    before = store.basket()
    selected = before[index]["key"]
    first_revision = store.basket_snapshot().revision
    assert session.change(selected, 0)["count"] == 2
    assert selected not in [row["key"] for row in store.basket()]
    result = session.undo()
    assert result["selected_key"] == selected and result["count"] == 3
    assert store.basket() == before
    assert store.basket_snapshot().revision != first_revision
    assert session.undo_count == 0


@pytest.mark.parametrize("delta", [-1, 1])
def test_multi_step_undo_uses_current_revision_but_original_full_rows(opened, delta):
    store, session = opened
    before = store.basket()
    key = before[1]["key"]
    session.change(key, delta)
    after_move = store.basket()
    session.change(key, 0)
    assert session.undo_count == 2
    session.undo()
    assert store.basket() == after_move
    session.undo()
    assert store.basket() == before


def test_noop_does_not_write_or_add_history_and_invalid_parameters_do_not_submit(opened):
    store, session = opened
    before = store.path.read_bytes()
    assert not session.change(mixed_rows()[0]["key"], -1)["changed"]
    assert not session.change(mixed_rows()[-1]["key"], 1)["changed"]
    assert session.undo_count == 0 and store.path.read_bytes() == before
    for key, delta in (("", 0), ("key", True), ("key", 2)):
        with pytest.raises(DesktopStateError):
            session.change(key, delta)
    assert store.path.read_bytes() == before


def test_undo_merges_only_basket_into_latest_window_drafts_and_other_metadata(opened):
    store, session = opened
    before = store.basket()
    session.change(before[0]["key"], 0)
    external = DesktopStateStore(store.root)
    external.save_window_state(geometry="teacher-new-window", layout="new-layout")
    external.save_draft("paper-current", {"section_order": ["independent", "draft"], "scores": [9, 2]})
    external.save_studio_favorites(["teacher-choice"])
    other = external.snapshot()
    assert not session.read().history_reset and session.undo_count == 1
    session.undo()
    after = external.snapshot()
    assert after["basket"] == before
    for field in ("window", "drafts", "studio"):
        assert after[field] == other[field]


@pytest.mark.parametrize("operation", ["add", "remove", "move", "clear", "bulk", "private_update"])
def test_every_existing_basket_writer_invalidates_old_session_without_overwrite(opened, operation):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    other = DesktopStateStore(store.root)
    actions = {
        "add": lambda: other.add_to_basket({"key": "external"}),
        "bulk": lambda: other.add_many_to_basket([{"key": "external-a"}, {"key": "external-b"}]),
        "remove": lambda: other.remove_basket_item(mixed_rows()[1]["key"]),
        "move": lambda: other.move_basket_item(mixed_rows()[1]["key"], 1),
        "clear": other.clear_basket,
        "private_update": lambda: other._update(lambda value: value["basket"].append({"key": "external"})),
    }
    actions[operation]()
    durable = store.path.read_bytes()
    with pytest.raises(BasketConflictError):
        session.undo()
    assert store.path.read_bytes() == durable and session.undo_count == 0
    with pytest.raises(DesktopStateError, match="读取"):
        session.change(mixed_rows()[1]["key"], 0)
    assert session.read().rows == tuple(other.basket())


@pytest.mark.parametrize("aba", ["round_trip", "metadata_round_trip"])
def test_same_content_aba_is_rejected_by_durable_revision(opened, aba):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    before = store.basket_snapshot()
    other = DesktopStateStore(store.root)
    key = other.basket()[0]["key"]
    if aba == "round_trip":
        other.move_basket_item(key, 1)
        other.move_basket_item(key, -1)
    else:
        original = other.basket()
        altered = deepcopy(original)
        altered[0]["teacher_note"] = "external edit, later removed"
        other.add_many_to_basket(altered)
        other.add_many_to_basket(original)
    after = other.basket_snapshot()
    assert after.rows == before.rows and after.revision != before.revision
    frozen = store.path.read_bytes()
    with pytest.raises(BasketConflictError):
        session.undo()
    assert store.path.read_bytes() == frozen


@pytest.mark.parametrize("noop", ["same_batch", "same_first_item", "same_last_item", "missing_remove", "missing_move", "boundary_move"])
def test_legacy_noop_preserves_file_revision_and_other_windows_history(opened, noop):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    other = DesktopStateStore(store.root)
    rows = other.basket()
    before = store.path.read_bytes()
    actions = {
        "same_batch": lambda: other.add_many_to_basket(rows),
        "same_first_item": lambda: other.add_to_basket(rows[0]),
        "same_last_item": lambda: other.add_to_basket(rows[-1]),
        "missing_remove": lambda: other.remove_basket_item("not-present"),
        "missing_move": lambda: other.move_basket_item("not-present", 1),
        "boundary_move": lambda: other.move_basket_item(rows[0]["key"], -1),
    }
    actions[noop]()
    assert store.path.read_bytes() == before
    assert not session.read().history_reset and session.undo_count == 1
    session.undo()
    assert store.basket() == mixed_rows()


def test_legacy_empty_clear_does_not_invalidate_removal_undo(opened):
    store, session = opened
    for row in mixed_rows():
        session.change(row["key"], 0)
    before = store.path.read_bytes()
    DesktopStateStore(store.root).clear_basket()
    assert store.path.read_bytes() == before
    assert not session.read().history_reset and session.undo_count == 3
    session.undo()
    assert store.basket() == [mixed_rows()[-1]]


def test_two_windows_cannot_use_each_others_history_or_overwrite_stale_selection(opened):
    store, first = opened
    second = DesktopStateStore(store.root).open_basket_session()
    second.read()
    first.change(mixed_rows()[1]["key"], -1)
    with pytest.raises(DesktopStateError, match="没有可撤销"):
        second.undo()
    frozen = store.path.read_bytes()
    with pytest.raises(BasketConflictError):
        second.change(mixed_rows()[0]["key"], 0)
    assert store.path.read_bytes() == frozen
    second.read()
    second.change(mixed_rows()[0]["key"], 0)
    assert first.read().history_reset and first.undo_count == 0


def test_external_metadata_change_without_revision_is_also_a_conflict(opened):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["basket"][0]["source_zh"] = "external source correction"
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    frozen = store.path.read_bytes()
    with pytest.raises(BasketConflictError):
        session.undo()
    assert store.path.read_bytes() == frozen


@pytest.mark.parametrize("operation", ["change", "undo"])
@pytest.mark.parametrize("failure", ["before_replace", "after_replace"])
def test_uncertain_save_never_creates_or_replays_undo_history(opened, monkeypatch, operation, failure):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    before = store.path.read_bytes()
    original = store._write_unlocked
    calls = []
    def broken(value, **kwargs):
        calls.append(True)
        if failure == "after_replace":
            original(value, **kwargs)
        raise OSError("synthetic private failure")
    monkeypatch.setattr(store, "_write_unlocked", broken)
    with pytest.raises(DesktopStateError):
        session.undo() if operation == "undo" else session.change(mixed_rows()[1]["key"], 0)
    assert len(calls) == 1 and session.undo_count == 0
    assert (store.path.read_bytes() == before) == (failure == "before_replace")
    with pytest.raises(DesktopStateError):
        session.undo()
    assert len(calls) == 1
    monkeypatch.setattr(store, "_write_unlocked", original)
    session.read()
    assert session.undo_count == 0


def test_failed_os_replace_preserves_bytes_and_cleans_temporary_file(opened, monkeypatch):
    store, session = opened
    before = store.path.read_bytes()
    def denied(*args):
        raise PermissionError("synthetic denied replacement")
    monkeypatch.setattr(state_module.os, "replace", denied)
    with pytest.raises(DesktopStateError):
        session.change(mixed_rows()[0]["key"], 0)
    assert store.path.read_bytes() == before and session.undo_count == 0
    assert not list(store.root.glob(".desktop-state-*.tmp"))


def test_read_failure_retires_history_even_if_original_bytes_later_return(opened):
    store, session = opened
    session.change(mixed_rows()[0]["key"], 0)
    valid = store.path.read_bytes()
    store.path.write_bytes(b"{broken")
    with pytest.raises(DesktopStateError):
        session.read()
    assert session.undo_count == 0 and store.path.read_bytes() == b"{broken"
    store.path.write_bytes(valid)
    session.read()
    with pytest.raises(DesktopStateError):
        session.undo()
    assert store.path.read_bytes() == valid


@pytest.mark.parametrize("rows", [[None], [{"key": "same"}, {"key": "same"}], [{"title": "missing-key"}]])
def test_malformed_old_rows_are_not_silently_filtered_or_rewritten(tmp_path, rows):
    store = DesktopStateStore(tmp_path)
    store.path.write_text(json.dumps({"schema_version": STATE_SCHEMA, "basket": rows}), encoding="utf-8")
    before = store.path.read_bytes()
    for operation in (store.basket, store.open_basket_session().read,
                      lambda: store.save_draft("new", {}), lambda: store.add_to_basket({"key": "new"})):
        with pytest.raises(DesktopStateError):
            operation()
    assert store.path.read_bytes() == before


def test_legacy_read_and_empty_undo_do_not_create_state_or_migrate_on_read(tmp_path):
    store = DesktopStateStore(tmp_path / "missing")
    session = store.open_basket_session()
    assert session.read().rows == ()
    with pytest.raises(DesktopStateError):
        session.undo()
    assert not store.root.exists()
    store.root.mkdir()
    store.path.write_text(json.dumps({"schema_version": STATE_SCHEMA, "basket": [{"key": "legacy"}]}), encoding="utf-8")
    before = store.path.read_bytes()
    session.read()
    assert store.path.read_bytes() == before
    session.change("legacy", 0)
    assert store.basket() == []
    session.undo()
    assert store.basket() == [{"key": "legacy"}]


def test_close_and_new_session_never_revive_history_and_views_cannot_mutate_it(opened):
    store, session = opened
    before = store.basket()
    exposed = session.read()
    exposed.rows[0]["teacher_metadata"]["tags"].append("not durable")
    session.change(before[0]["key"], 0)
    session.undo()
    assert store.basket() == before
    session.change(before[0]["key"], 0)
    frozen = store.path.read_bytes()
    session.close()
    with pytest.raises(DesktopStateError, match="关闭"):
        session.read()
    with pytest.raises(DesktopStateError):
        session.undo()
    new = store.open_basket_session()
    new.read()
    assert new.undo_count == 0 and store.path.read_bytes() == frozen


def test_history_has_explicit_bound_and_noop_does_not_evict_valid_step(opened):
    store, session = opened
    key = mixed_rows()[0]["key"]
    for index in range(session.HISTORY_LIMIT + 2):
        session.change(key, 1 if index % 2 == 0 else -1)
    assert session.undo_count == session.HISTORY_LIMIT
    frozen = store.path.read_bytes()
    assert not session.change(key, -1)["changed"]
    assert session.undo_count == session.HISTORY_LIMIT and store.path.read_bytes() == frozen
    for _ in range(session.HISTORY_LIMIT):
        session.undo()
    assert store.basket() == mixed_rows()


def test_all_store_instances_share_lock_and_atomic_writers_do_not_lose_rows(tmp_path):
    first, second = DesktopStateStore(tmp_path), DesktopStateStore(tmp_path)
    assert first._lock is second._lock
    def add(index):
        DesktopStateStore(tmp_path).add_to_basket({"key": str(index)})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add, range(25)))
    assert {row["key"] for row in first.basket()} == {str(index) for index in range(25)}


def test_os_lock_blocks_legacy_and_unrelated_writers_from_another_process(opened):
    store, session = opened
    code = (
        "from pathlib import Path\nimport sys\n"
        "from integrations.deeptutor_shchem_v1.desktop_state import _state_file_lock\n"
        "with _state_file_lock(Path(sys.argv[1])):\n"
        " print('locked', flush=True)\n sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(store.path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        frozen = store.path.read_bytes()
        for operation in (store.clear_basket, lambda: store.save_draft("new", {}),
                          lambda: session.change(mixed_rows()[0]["key"], 0)):
            with pytest.raises(DesktopStateError):
                operation()
        assert store.path.read_bytes() == frozen
    finally:
        child.communicate("release\n", timeout=10)
    assert child.returncode == 0
    store.add_to_basket({"key": "after-release"})
    assert len(store.basket()) == 4


def test_observed_direct_file_replacement_before_commit_is_not_overwritten(opened, monkeypatch):
    store, session = opened
    original = state_module.tempfile.mkstemp
    external = deepcopy(store.snapshot())
    external["drafts"]["external"] = {"teacher-note": "keep"}
    external_bytes = json.dumps(external).encode("utf-8")
    def intervening(*args, **kwargs):
        descriptor, name = original(*args, **kwargs)
        store.path.write_bytes(external_bytes)
        return descriptor, name
    monkeypatch.setattr(state_module.tempfile, "mkstemp", intervening)
    with pytest.raises(BasketConflictError):
        session.change(mixed_rows()[0]["key"], 0)
    assert store.path.read_bytes() == external_bytes and session.undo_count == 0
    assert not list(store.root.glob(".desktop-state-*.tmp"))


# Reuse existing synthetic source builders, never personal sources or providers.
from test_desktop_mixed_paper_service import setup as mixed_setup, _add_word, _core_item  # noqa: E402,F401
from test_personal_visual_questions import imported_visual_batch  # noqa: E402,F401


def test_word_and_same_native_id_core_scopes_project_unchanged_after_undo(mixed_setup):
    service, store, words, _, _ = mixed_setup
    store.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    before = service.projection()
    session = store.open_basket_session()
    session.read()
    key = store.basket()[-1]["key"]
    session.change(key, -1)
    session.change(store.basket()[0]["key"], 0)
    session.undo()
    session.undo()
    assert service.projection() == before
    assert [row.get("scope") for row in store.basket()[:2]] == ["master", "supplemental"]


def test_restored_visual_theme_keeps_scope_assets_and_exports_from_frozen_preview(imported_visual_batch):
    from test_visual_mixed_paper_integration import _service, _request, _read_all, _protected, _sha
    context = imported_visual_batch
    service, rows = _service(context)
    protected = _protected(context)
    service.add_visual_questions([rows[0]])
    before = service.state.basket()
    projection = service.projection()
    session = service.state.open_basket_session()
    session.read()
    session.change(before[0]["key"], 0)
    assert service.state.basket() == []
    session.undo()
    assert service.state.basket() == before and service.projection() == projection
    content = service.create_preview(_request(service))
    paginated = service.prepare_pagination(content.preview_id, content.preview_hash)
    _read_all(service, paginated)
    service.approve(paginated.preview_id, paginated.preview_hash)
    result = service.export(paginated.preview_id, paginated.preview_hash)
    assert result["status"] == "completed"
    assert {row["artifact_id"] for row in result["artifacts"]} == {
        "student_docx", "teacher_docx", "student_pdf", "teacher_pdf"}
    for artifact in result["artifacts"]:
        assert _sha(Path(artifact["path"]).read_bytes()) == artifact["sha256"]
    assert _protected(context) == protected
