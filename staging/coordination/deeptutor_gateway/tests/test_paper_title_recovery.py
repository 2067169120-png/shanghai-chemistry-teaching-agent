"""Title-only buffers: authentication, CAS, bounded text and abrupt UI reopen."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from integrations.deeptutor_shchem_v1 import desktop_paper_title_recovery as recovery_module
from integrations.deeptutor_shchem_v1.desktop_paper_title_recovery import PaperTitleRecovery, MAX_BYTES
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from test_paper_title_save_coalescing import title_editor, current, typed
from test_persistent_paper_history import paper_history
from test_desktop_mixed_paper_service import setup
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import Tasks
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel


def binding(revision="0" * 32, basis="2" * 64):
    return {"draft": {"revision": revision, "content_sha256": "1" * 64}, "basis_sha256": basis}


def test_new_reader_recovers_only_matching_profile_scope_draft_and_source(tmp_path):
    root = tmp_path / "state"
    writer = PaperTitleRecovery(root, "paper:A")
    assert writer.read(binding())["status"] == "missing" and not root.exists()
    writer.write({"title": "unsaved", "subtitle": "range"}, binding())
    reader = PaperTitleRecovery(root, "paper:A")
    assert reader.read(binding())["texts"] == {"title": "unsaved", "subtitle": "range"}
    assert reader.read(binding("3" * 32))["status"] == "stale"
    assert reader.read(binding(basis="4" * 64))["status"] == "stale"
    assert reader.path.exists() and reader.read(binding())["status"] == "matched"
    assert len(reader.path.read_bytes()) <= MAX_BYTES


def test_two_windows_and_a_b_a_cannot_overwrite_with_obsolete_buffer_token(tmp_path):
    first, second = PaperTitleRecovery(tmp_path, "paper:A"), PaperTitleRecovery(tmp_path, "paper:A")
    first.read(binding()); second.read(binding())
    first.write({"title": "A"}, binding())
    with pytest.raises(DesktopStateError): second.write({"title": "B"}, binding())
    second.read(binding())
    second.write({"title": "B"}, binding())
    second.write({"title": "A"}, binding())
    frozen = first.path.read_bytes()
    with pytest.raises(DesktopStateError): first.write({"title": "older A"}, binding())
    assert first.path.read_bytes() == frozen


def test_direct_file_a_b_a_and_two_scope_cleanup_preserve_other_input(tmp_path):
    first, other = PaperTitleRecovery(tmp_path, "paper:A"), PaperTitleRecovery(tmp_path, "paper:B")
    first.read(binding()); other.read(binding())
    first.write({"title": "A"}, binding()); other.write({"title": "B"}, binding())
    original = first.path.read_bytes()
    first.path.write_bytes(b"temporary direct replacement")
    first.path.write_bytes(original)
    with pytest.raises(DesktopStateError): first.write({"title": "stale"}, binding())
    first.read(binding())
    assert first.clear({"title": "A"}) and not first.path.exists()
    assert other.path.exists() and other.read(binding())["texts"]["title"] == "B"


def test_cross_profile_or_scope_copy_is_readable_but_never_applied_or_cleared(tmp_path):
    writer = PaperTitleRecovery(tmp_path / "one", "paper:A")
    writer.read(binding()); writer.write({"title": "old private input"}, binding())
    copied = PaperTitleRecovery(tmp_path / "two", "paper:A")
    copied.directory.mkdir(parents=True)
    shutil.copy2(writer.path, copied.path)
    shutil.copy2(writer.key_path, copied.key_path)
    result = copied.read(binding())
    assert result["status"] == "stale" and result["texts"]["title"] == "old private input"
    assert not result["can_discard"] and not copied.clear(discard=True)
    foreign = PaperTitleRecovery(writer.root, "paper:B")
    shutil.copy2(writer.path, foreign.path)
    assert foreign.read(binding())["status"] == "stale"
    assert not foreign.clear(discard=True)


def _junction(link, target):
    if os.name != "nt": pytest.skip("Windows junction boundary")
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, timeout=10)
    if result.returncode: pytest.skip("junction unsupported by fixture filesystem")
    assert link.is_junction()


def test_profile_entry_junction_resolves_to_supported_target_before_binding(tmp_path):
    target, entry = tmp_path / "state-target", tmp_path / "state-entry"
    target.mkdir(); _junction(entry, target)
    try:
        buffer = PaperTitleRecovery(entry, "paper:A")
        assert buffer.root == target.resolve()
        buffer.read(binding()); buffer.write({"title": "safe"}, binding())
        assert buffer.read(binding())["status"] == "matched"
    finally:
        entry.rmdir()


def test_descendant_junction_cannot_write_or_clear_profile_external_original(tmp_path):
    buffer = PaperTitleRecovery(tmp_path / "state", "paper:A")
    buffer.read(binding()); buffer.write({"title": "old input"}, binding())
    original_directory = buffer.directory.with_name("kept-recovery-directory")
    buffer.directory.rename(original_directory)
    outside = tmp_path / "outside"; outside.mkdir()
    original = outside / buffer.path.name
    original.write_bytes(b"protected external original")
    frozen = hashlib.sha256(original.read_bytes()).hexdigest()
    _junction(buffer.directory, outside)
    try:
        with pytest.raises(DesktopStateError): buffer.write({"title": "overwrite"}, binding())
        with pytest.raises(DesktopStateError): buffer.clear(discard=True)
        assert buffer.read(binding())["status"] == "invalid"
        assert hashlib.sha256(original.read_bytes()).hexdigest() == frozen
        assert (original_directory / buffer.path.name).exists()
    finally:
        buffer.directory.rmdir()


@pytest.mark.parametrize("alias", ["trust", "buffer"])
def test_hardlink_alias_is_rejected_before_write_or_clear_and_preserves_original(tmp_path, alias):
    buffer = PaperTitleRecovery(tmp_path / "state", "paper:A")
    buffer.read(binding()); buffer.write({"title": "old input"}, binding())
    target = buffer.key_path if alias == "trust" else buffer.path
    original = tmp_path / ("outside-" + alias)
    original.write_bytes(target.read_bytes()); target.unlink()
    try: os.link(original, target)
    except OSError: pytest.skip("hardlinks unsupported by fixture filesystem")
    frozen = hashlib.sha256(original.read_bytes()).hexdigest()
    with pytest.raises(DesktopStateError): buffer.write({"title": "overwrite"}, binding())
    with pytest.raises(DesktopStateError): buffer.clear(discard=True)
    assert buffer.read(binding())["status"] == "invalid"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == frozen


@pytest.mark.parametrize("damage", ["json", "signature", "size", "deep"])
def test_damage_keeps_raw_buffer_and_cannot_authorize_restoration(tmp_path, damage):
    writer = PaperTitleRecovery(tmp_path, "paper:A")
    writer.read(binding()); writer.write({"title": "kept"}, binding())
    if damage == "json": writer.path.write_bytes(b"{broken")
    elif damage == "size": writer.path.write_bytes(b"x" * (MAX_BYTES + 1))
    elif damage == "deep": writer.path.write_bytes(b"[" * 1500 + b"]" * 1500)
    else:
        record = json.loads(writer.path.read_bytes())
        record["signature"] = "0" * 64
        writer.path.write_text(json.dumps(record), encoding="utf-8")
    frozen = writer.path.read_bytes()
    reader = PaperTitleRecovery(tmp_path, "paper:A")
    assert reader.read(binding())["status"] == "invalid"
    with pytest.raises(DesktopStateError): reader.write({"title": "overwrite"}, binding())
    assert reader.path.read_bytes() == frozen


@pytest.mark.parametrize("texts", [{"title": "x" * 121}, {"subtitle": "x" * 181}, {"body": "untrusted"}, {"title": 1}])
def test_only_two_bounded_text_fields_can_enter_buffer(tmp_path, texts):
    buffer = PaperTitleRecovery(tmp_path, "paper:A")
    buffer.read(binding())
    with pytest.raises(DesktopStateError): buffer.write(texts, binding())
    assert not buffer.path.exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_uncertain_replace_keeps_current_buffer_and_requires_explicit_reread(tmp_path, monkeypatch, when):
    buffer = PaperTitleRecovery(tmp_path, "paper:A")
    buffer.read(binding()); buffer.write({"title": "before"}, binding())
    replace = recovery_module.os.replace
    calls = []
    def fail(source, target):
        calls.append(True)
        if when == "after": replace(source, target)
        raise OSError("synthetic uncertain replace")
    with monkeypatch.context() as patch:
        patch.setattr(recovery_module.os, "replace", fail)
        with pytest.raises(DesktopStateError): buffer.write({"title": "after"}, binding())
    expected = "before" if when == "before" else "after"
    with pytest.raises(DesktopStateError): buffer.write({"title": "unreviewed retry"}, binding())
    assert PaperTitleRecovery(tmp_path, "paper:A").read(binding())["texts"]["title"] == expected
    assert len(calls) == 1 and not list(buffer.directory.glob(".title-*.tmp"))
    assert buffer.read(binding())["status"] == "matched"
    buffer.write({"title": "reviewed retry"}, binding())
    assert buffer.read(binding())["texts"]["title"] == "reviewed retry"


def test_abrupt_new_python_process_reopens_unsaved_text_as_pending_only(title_editor):
    data = title_editor
    before = data.store.path.read_bytes()
    code = """
import os, sys
from threading import Event
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from test_desktop_mixed_paper_service import Words, _catalog
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import Tasks
store = DesktopStateStore(sys.argv[1]); words = Words(); app = QApplication([])
facade = SimpleNamespace(state_store=store, paths=SimpleNamespace(state_root=store.root),
 _word_questions=lambda:words, paper_theme_catalog=_catalog,
 _paper_catalog_snapshot_id=lambda catalog:catalog['data_snapshot_id'], _reader_stop_event=Event())
paper = IndependentPaperLibrary(facade).open(sys.argv[2]); tasks=Tasks()
panel = MixedPaperPanel(paper,tasks,PaperComposerModel()); panel.load(); tasks.flush()
panel.title.setText('unfinished title before abrupt exit')
assert panel._pending_title_fields and not panel._title_buffer_error
os._exit(0)
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parent), str(Path.cwd())]),
               TEMP="E:/临时文件/UserTemp/shchem-full-closure-20261013", TMP="E:/临时文件/UserTemp/shchem-full-closure-20261013")
    result = subprocess.run([sys.executable, "-B", "-c", code, str(data.store.root), data.paper.paper_id],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert data.store.path.read_bytes() == before
    tasks = Tasks()
    reopened = MixedPaperPanel(data.paper, tasks, PaperComposerModel())
    reopened.load(); tasks.flush()
    assert reopened.title.text() == "unfinished title before abrupt exit"
    assert reopened._pending_title_fields and reopened._draft_session.undo_count == 0
    assert data.store.path.read_bytes() == before and "尚未正式保存" in reopened.status.text()
    assert not reopened.mode.isEnabled() and reopened.save_recovered_title_button.isEnabled()
    reopened.title.editingFinished.emit()
    assert data.store.path.read_bytes() == before and reopened._pending_title_fields
    assert not reopened.close() and not reopened._closed
    assert data.store.path.read_bytes() == before
    reopened.save_recovered_title_button.click()
    assert current(data)["payload"]["settings_ui"]["title"] == reopened.title.text()
    assert reopened._draft_session.undo_count == 1 and not reopened._title_recovery.path.exists()
    assert reopened.mode.isEnabled() and not reopened.save_recovered_title_button.isVisible()
    reopened.close(); reopened.deleteLater()


def test_buffer_write_failure_retains_text_and_explicit_formal_save_still_works(title_editor, monkeypatch):
    data = title_editor
    buffer = data.panel._title_recovery_service()
    calls = []
    def fail(*_args): calls.append(True); raise OSError("synthetic buffer failure")
    with monkeypatch.context() as patch:
        patch.setattr(buffer, "write", fail)
        typed(data.panel)
        assert len(calls) == 1 and data.panel._title_buffer_error
        assert data.panel.title.text() == "chemistry123" and "未能确认" in data.panel.status.text()
    assert data.panel.prepare_leave()
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    assert not data.panel._pending_title_fields


def test_two_loaded_windows_do_not_rebase_a_missing_buffer_and_overwrite_new_input(title_editor):
    data = title_editor
    tasks = Tasks()
    other = MixedPaperPanel(data.paper, tasks, PaperComposerModel())
    other.load(); tasks.flush()
    assert data.panel._title_recovery.initialized and other._title_recovery.initialized
    other.title.setText("input in the second window")
    frozen = other._title_recovery.path.read_bytes()
    data.panel.title.setText("input in the first window")
    assert data.panel._title_buffer_error and data.panel.title.text() == "input in the first window"
    assert other._title_recovery.path.read_bytes() == frozen
    assert other.title.text() == "input in the second window"
    # A successful formal save still cannot clear B's independently newer buffer.
    assert data.panel.prepare_leave()
    assert other._title_recovery.path.read_bytes() == frozen
    other._pending_title_fields.clear()
    other.close(); other.deleteLater()


def test_deeply_malformed_buffer_cannot_block_verified_formal_paper_load(title_editor):
    data = title_editor
    recovery = data.panel._title_recovery
    recovery.directory.mkdir(parents=True, exist_ok=True)
    recovery.path.write_bytes(b"[" * 1500 + b"]" * 1500)
    frozen = data.store.path.read_bytes()
    tasks = Tasks()
    new = MixedPaperPanel(data.paper, tasks, PaperComposerModel())
    new.load(); tasks.flush()
    assert new._loaded_once and not new._restore_failed and not new._pending_title_fields
    assert new._title_buffer_error and recovery.path.exists()
    assert data.store.path.read_bytes() == frozen
    new.close(); new.deleteLater()


def test_changed_formal_binding_preserves_buffer_for_explanation_without_applying(title_editor):
    data = title_editor
    typed(data.panel)
    buffer_path = data.panel._title_recovery.path
    record = deepcopy(current(data))
    record["payload"]["settings_ui"]["title"] = "external saved title"
    data.store.save_draft(data.paper.paper_id + ":" + DRAFT_ID, record)
    tasks = Tasks()
    new = MixedPaperPanel(data.paper, tasks, PaperComposerModel())
    new.load(); tasks.flush()
    assert new.title.text() == "external saved title" and not new._pending_title_fields
    assert buffer_path.exists() and "chemistry123" in new.title_recovery_note.text()
    new.discard_title_buffer_button.click()
    assert not buffer_path.exists() and current(data) == record
    new.close(); new.deleteLater()
