"""Production paper/basket paths, isolated sources and new profile instances."""
from copy import copy, deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event

import pytest

from test_desktop_mixed_paper_service import setup, _add_word, _core_item
from test_independent_paper import use_builders
from test_mixed_paper_draft_undo import edited
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError
from integrations.deeptutor_shchem_v1.desktop_paper_history import PAPER_HISTORY_ID
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DesktopStateError, DraftConflictError


@pytest.fixture
def paper_history(setup):
    service, store, words, _, _ = setup
    service.facade._reader_stop_event = Event()
    service.facade._state = store
    store.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    library = IndependentPaperLibrary(service.facade)
    paper = use_builders(library.create(), service)
    editor = paper.open_mixed_paper_draft_session()
    editor.read(paper.paper_basket_projection())
    return service, store, words, paper, editor


def reopened(data, *, root=None, identity=None):
    service, store, _, paper, _ = data
    facade = copy(service.facade)
    facade.state_store = DesktopStateStore(root or store.root)
    facade._state = facade.state_store
    facade.paths = copy(facade.paths)
    facade.paths.state_root = facade.state_store.root
    current = use_builders(IndependentPaperLibrary(facade).open(identity or paper.paper_id), service)
    editor = current.open_mixed_paper_draft_session()
    result = editor.read(current.paper_basket_projection())
    return current, editor, result


@pytest.mark.parametrize("kind", ["remove", "move", "word_points", "core_points", "space", "atomic", "layout"])
def test_independent_paper_new_instance_undo_redo_keeps_exact_identity_and_scope(paper_history, kind):
    service, store, _, paper, editor = paper_history
    original = deepcopy(editor.current.record)
    refs = deepcopy(paper.basket())
    other_paper = IndependentPaperLibrary(service.facade).create()
    other = store.draft_snapshot(other_paper.paper_id + ":" + DRAFT_ID)
    changed = edited(original["payload"], kind)
    selected = original["payload"]["order"][0]
    editor.save(changed, action=kind, selected=selected)
    editor.close()
    store.clear_basket()
    store.add_to_basket({"key": "unrelated-public-choice"})
    store.save_window_state(geometry="keep-window", layout="keep-layout")
    current, new, _ = reopened(paper_history)
    assert new.undo_count == 1 and new.redo_count == 0
    result = new.undo()
    assert result["selected"] == selected and result["record"] == original
    assert new.redo_count == 1 and current.basket() == refs
    new.close()
    _, again, _ = reopened(paper_history)
    assert again.undo_count == 0 and again.redo_count == 1
    assert again.redo()["record"]["payload"] == changed
    assert store.basket() == [{"key": "unrelated-public-choice"}]
    assert store.draft_snapshot(other_paper.paper_id + ":" + DRAFT_ID) == other
    assert store.window_state() == {"geometry": "keep-window", "layout": "keep-layout"}
    assert DRAFT_ID not in store.snapshot()["drafts"]


def test_redo_branch_cleared_only_by_changed_edit_and_history_is_bounded(paper_history):
    _, store, _, paper, editor = paper_history
    initial = editor.current.record["payload"]
    for number in range(35):
        changed = deepcopy(initial)
        changed["settings_ui"]["subtitle"] = str(number)
        editor.save(changed)
    _, new, _ = reopened(paper_history)
    assert new.undo_count == 30
    new.undo()
    frozen = store.path.read_bytes()
    assert not new.save(new.current.record["payload"], remember=False)["changed"]
    assert new.redo_count == 1 and store.path.read_bytes() == frozen
    changed = deepcopy(new.current.record["payload"])
    changed["settings_ui"]["title"] = "branch"
    new.save(changed)
    assert new.redo_count == 0 and new.undo_count == 30
    _, final, _ = reopened(paper_history)
    assert final.undo_count == 30 and final.redo_count == 0
    while final.undo_count:
        final.undo()
    assert final.current.record["payload"]["settings_ui"]["subtitle"] == "4"
    assert final.redo_count == 30
    assert len(store.path.read_bytes()) < 8 * 1024 * 1024


def test_history_byte_limit_trims_old_steps_without_rejecting_saved_edit(paper_history, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_paper_history
    monkeypatch.setattr(desktop_paper_history, "MAX_BYTES", 12000)
    _, store, _, paper, editor = paper_history
    for number in range(35):
        changed = deepcopy(editor.current.record["payload"])
        changed["settings_ui"]["subtitle"] = str(number)
        assert editor.save(changed)["changed"]
    raw = store.draft_snapshot(paper.paper_id + ":" + PAPER_HISTORY_ID).record
    assert len(desktop_paper_history.encoded(raw)) <= 12000
    _, fresh, read = reopened(paper_history)
    assert not read["history_reset"] and 0 < fresh.undo_count < 30
    assert fresh.current.record["payload"]["settings_ui"]["subtitle"] == "34"
    fresh.undo()
    assert fresh.current.record["payload"]["settings_ui"]["subtitle"] == "33"


def test_restarted_python_process_uses_actual_independent_paper_undo_and_redo(paper_history):
    _, store, _, paper, editor = paper_history
    initial = deepcopy(editor.current.record)
    changed = edited(initial["payload"], "atomic")
    editor.save(changed, action="逐题设置")
    code = """
import json, sys
from threading import Event
from types import SimpleNamespace
from test_desktop_mixed_paper_service import Words, _catalog
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
store = DesktopStateStore(sys.argv[1])
words = Words()
facade = SimpleNamespace(state_store=store, paths=SimpleNamespace(state_root=store.root),
    _word_questions=lambda: words, paper_theme_catalog=_catalog,
    _paper_catalog_snapshot_id=lambda catalog: catalog['data_snapshot_id'], _reader_stop_event=Event())
paper = IndependentPaperLibrary(facade).open(sys.argv[2])
editor = paper.open_mixed_paper_draft_session()
editor.read(paper.paper_basket_projection())
result = getattr(editor, sys.argv[3])()
print(json.dumps({'record': result['record'], 'undo': editor.undo_count, 'redo': editor.redo_count}))
editor.close()
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), str(Path.cwd())]),
               TEMP="E:/临时文件/UserTemp/shchem-full-closure-20261013", TMP="E:/临时文件/UserTemp/shchem-full-closure-20261013")
    def run(action):
        result = subprocess.run([sys.executable, "-B", "-c", code, str(store.root), paper.paper_id, action],
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    undo = run("undo")
    assert undo["record"] == initial and undo["undo"] == 0 and undo["redo"] == 1
    redo = run("redo")
    assert redo["record"]["payload"] == changed and redo["undo"] == 1 and redo["redo"] == 0


@pytest.mark.parametrize("change", ["draft", "aba", "history"])
def test_stale_window_cannot_overwrite_saved_paper_or_changed_history(paper_history, change):
    _, store, _, paper, editor = paper_history
    editor.save(edited(editor.current.record["payload"], "move"))
    _, second, _ = reopened(paper_history)
    if change == "history":
        key = paper.paper_id + ":" + PAPER_HISTORY_ID
        raw = store.draft_snapshot(key).record
        raw["signature"] = "0" * 64
        store.save_draft(key, raw)
    else:
        key = paper.paper_id + ":" + DRAFT_ID
        original = store.draft_snapshot(key).record
        external = deepcopy(original)
        external["payload"]["settings_ui"]["title"] = "external window"
        store.save_draft(key, external)
        if change == "aba":
            store.save_draft(key, original)
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError):
        second.undo()
    assert not second.undo_count and not second.redo_count
    assert store.path.read_bytes() == frozen
    _, fresh, read = reopened(paper_history)
    assert read["history_reset"] and fresh.undo_count == 0


@pytest.mark.parametrize("damage", ["signature", "state", "scope", "count", "malformed"])
def test_corrupt_history_is_never_restore_input_and_does_not_hide_current_paper(paper_history, damage):
    _, store, _, paper, editor = paper_history
    editor.save(edited(editor.current.record["payload"], "layout"))
    original = deepcopy(editor.current.record)
    key = paper.paper_id + ":" + PAPER_HISTORY_ID
    raw = store.draft_snapshot(key).record
    if damage == "signature": raw["signature"] = "f" * 64
    elif damage == "state": raw["undo"][0]["state"]["payload"]["order"] = ["foreign-original"]
    elif damage == "scope": raw["scope"] = "public-mixed-paper"
    elif damage == "count": raw["undo"] *= 31
    else: raw = []
    store._update(lambda value: value["drafts"].__setitem__(key, raw))
    frozen = store.path.read_bytes()
    _, fresh, read = reopened(paper_history)
    assert fresh.current.record == original and read["history_reset"]
    assert fresh.undo_count == fresh.redo_count == 0 and store.path.read_bytes() == frozen
    fresh.save(edited(original["payload"], "move"))
    assert fresh.undo_count == 1
    assert fresh.undo()["record"] == original


def test_profile_copy_even_with_trust_key_cannot_import_history(paper_history, tmp_path):
    _, store, _, _, editor = paper_history
    editor.save(edited(editor.current.record["payload"], "layout"))
    destination = tmp_path / "copied-profile"
    destination.mkdir()
    shutil.copy2(store.path, destination / store.path.name)
    shutil.copy2(store.root / ".paper-history-trust.v1.key", destination / ".paper-history-trust.v1.key")
    _, fresh, result = reopened(paper_history, root=destination)
    assert result["history_reset"] and not fresh.undo_count
    assert fresh.current.record == editor.current.record


@pytest.mark.parametrize("change", ["revision", "range", "labels", "catalog"])
def test_source_change_blocks_old_history_without_losing_saved_state(paper_history, change):
    _, store, words, _, editor = paper_history
    editor.save(edited(editor.current.record["payload"], "move"))
    if change == "revision": words.row["revision"] = "new-revision"
    elif change == "range": words.row["question_blocks"].append({"index": 4, "text": "changed", "assets": []})
    elif change == "labels": words.row["attributes"] = {"revision": "new-tags"}
    else: words._read_attribute_catalog = lambda: {"changed": True}
    frozen = store.path.read_bytes()
    with pytest.raises((DesktopStateError, MixedPaperError)):
        editor.undo()
    assert store.path.read_bytes() == frozen and editor.undo_count == editor.redo_count == 0
    with pytest.raises((DesktopStateError, MixedPaperError)):
        reopened(paper_history)
    assert store.path.read_bytes() == frozen


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("operation", ["undo", "redo", "save"])
def test_uncertain_transaction_keeps_saved_state_and_matching_history_for_explicit_reopen(paper_history, monkeypatch, when, operation):
    _, store, _, _, editor = paper_history
    payload = deepcopy(editor.current.record["payload"])
    editor.save(edited(payload, "move"))
    if operation == "redo": editor.undo()
    frozen = store.path.read_bytes()
    write, calls = store._write_unlocked, []
    def fail(value, **kwargs):
        calls.append(True)
        if when == "after": write(value, **kwargs)
        raise OSError("synthetic uncertain write")
    monkeypatch.setattr(store, "_write_unlocked", fail)
    with pytest.raises((OSError, DesktopStateError)):
        editor.save(edited(payload, "layout")) if operation == "save" else getattr(editor, operation)()
    assert len(calls) == 1 and editor.undo_count == editor.redo_count == 0
    with pytest.raises(DesktopStateError): editor.undo()
    assert len(calls) == 1
    assert (store.path.read_bytes() == frozen) == (when == "before")
    monkeypatch.setattr(store, "_write_unlocked", write)
    _, fresh, read = reopened(paper_history)
    assert not read["history_reset"]
    if operation == "undo":
        assert fresh.undo_count == (1 if when == "before" else 0)
        assert fresh.redo_count == (0 if when == "before" else 1)
    elif operation == "redo":
        assert fresh.undo_count == (0 if when == "before" else 1)
        assert fresh.redo_count == (1 if when == "before" else 0)
    else:
        assert fresh.undo_count == (1 if when == "before" else 2)


def test_basket_facade_new_window_restore_source_identity_then_redo(paper_history):
    service, store, _, _, _ = paper_history
    first = DesktopWorkbenchFacade.open_basket_session(service.facade)
    initial = first.read()
    key = initial.rows[-1]["key"]
    first.change(key, 0)
    first.close()
    fresh_facade = copy(service.facade)
    fresh_facade._state = DesktopStateStore(store.root)
    fresh_facade.state_store = fresh_facade._state
    new = DesktopWorkbenchFacade.open_basket_session(fresh_facade)
    new.read()
    assert new.undo_count == 1
    new.undo()
    assert new.read().rows == initial.rows
    new.close()
    final = DesktopWorkbenchFacade.open_basket_session(fresh_facade)
    final.read()
    assert final.redo_count == 1
    final.redo()
    assert key not in {row["key"] for row in store.basket()}


def test_removed_basket_source_change_cannot_be_restored(paper_history):
    service, store, words, _, _ = paper_history
    first = DesktopWorkbenchFacade.open_basket_session(service.facade)
    rows = first.read().rows
    first.change(rows[-1]["key"], 0)
    words.row["revision"] = "changed"
    second = DesktopWorkbenchFacade.open_basket_session(service.facade)
    second.read()
    frozen = store.path.read_bytes()
    with pytest.raises(MixedPaperError): second.undo()
    assert store.path.read_bytes() == frozen and not second.undo_count


def test_legacy_basket_locator_cannot_hide_changed_source_content(paper_history):
    service, store, _, _, _ = paper_history
    rows = store.basket()
    rows[0].pop("data_snapshot_id")
    store._update(lambda value: value.__setitem__("basket", rows))
    first = DesktopWorkbenchFacade.open_basket_session(service.facade)
    first.read()
    first.change(rows[0]["key"], 0)
    old_loader = service.facade.paper_theme_catalog
    def changed_catalog(scope):
        result = old_loader(scope)
        if scope == "master":
            result["papers"][0]["theme_groups"][0]["atomic_chain"].append({"atomic_part_id": "new-part"})
        return result
    service.facade.paper_theme_catalog = changed_catalog
    second = DesktopWorkbenchFacade.open_basket_session(service.facade)
    second.read()
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError, match="来源依据"):
        second.undo()
    assert store.path.read_bytes() == frozen and not second.undo_count


def test_independent_scope_rejects_injected_history(paper_history):
    _, store, _, paper, _ = paper_history
    frozen = store.path.read_bytes()
    with pytest.raises(DesktopStateError, match="导入"):
        paper.state_store._update(lambda value: value["drafts"].__setitem__(PAPER_HISTORY_ID, {"undo": []}))
    assert store.path.read_bytes() == frozen


def test_actual_ui_new_window_undo_redo_and_shortcuts(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_word_question_dialog import _isolated_qt_app
    from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import SyntheticFacade, Tasks, MixedPaperPanel, PaperComposerModel, settle
    from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog
    with _isolated_qt_app() as app:
        facade, tasks = SyntheticFacade(tmp_path / "ui-state"), Tasks()
        panel = MixedPaperPanel(facade, tasks, PaperComposerModel())
        panel.load(); tasks.flush()
        before = facade.state_store.draft_snapshot(DRAFT_ID).record
        panel.duration.setValue(80)
        panel.close()
        reopened_panel = MixedPaperPanel(facade, tasks, PaperComposerModel())
        reopened_panel.load(); tasks.flush()
        reopened_panel.show(); settle(app)
        assert reopened_panel.undo_button.isEnabled()
        reopened_panel.duration.setFocus()
        QTest.keyClick(reopened_panel.duration, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        settle(app)
        assert facade.state_store.draft_snapshot(DRAFT_ID).record == before
        assert reopened_panel.redo_button.isEnabled()
        QTest.keyClick(reopened_panel.duration, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        settle(app)
        assert reopened_panel.duration.value() == 80
        reopened_panel.close()
        facade.open_basket_session = facade.state_store.open_basket_session
        basket = ExplorerBasketDialog(facade)
        basket.list.setCurrentRow(1)
        basket.change(0)
        basket.close()
        basket = ExplorerBasketDialog(facade)
        basket.show(); settle(app)
        assert basket.undo_button.isEnabled()
        basket.list.setFocus()
        QTest.keyClick(basket.list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        settle(app)
        assert basket.list.count() == 3 and basket.redo_button.isEnabled()
        QTest.keyClick(basket.list, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        settle(app)
        assert basket.list.count() == 2
        basket.close()
