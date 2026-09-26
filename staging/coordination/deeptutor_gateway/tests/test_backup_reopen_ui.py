"""Revisit an existing independent recovery after the creation dialog is closed."""
from copy import deepcopy
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QFileDialog
from test_lesson_backup_ui import window, dialog_for
from test_work_organization import save_draft
from integrations.deeptutor_shchem_v1.desktop_backup import (
    create_backup, inspect_backup, manifest_revision, plan_backup, restore_backup,
)


def independent_copy(win, tmp_path):
    save_draft(win.facade)
    archive = tmp_path / "original.zip"
    create_backup(plan_backup(win.facade.paths.state_root), archive)
    checked = inspect_backup(archive)
    target = tmp_path / "先前已经恢复的备课"
    restore_backup(archive, target, expected_manifest=manifest_revision(checked))
    return target


def test_reopen_previously_restored_directory_keeps_current_editor(window, tmp_path, monkeypatch):
    win, _ = window
    target = independent_copy(win, tmp_path)
    prep = win.preparation_page
    prep.topic.setText("当前尚未保存的另一课题")
    prep.recovery.flush()
    state, payload = deepcopy(win.facade.state_store.snapshot()), deepcopy(prep._payload())
    recovery = prep.recovery.store.path.read_bytes()
    calls = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(target))
    monkeypatch.setattr(QProcess, "startDetached", lambda *a: calls.append(a) or (True, 123))
    dialog = dialog_for(win)
    try:
        dialog.show(); dialog.tabs.setCurrentIndex(1)
        assert dialog.restored_directory is None
        dialog.existing_button.click()
        assert dialog.restored_directory == str(target.resolve())
        assert calls[0][1][-2:] == ["--personal-state", str(target.resolve())]
        assert win.facade.state_store.snapshot() == state
        assert prep._payload() == payload
        assert prep.recovery.store.path.read_bytes() == recovery
    finally:
        dialog.close(); dialog.deleteLater()


@pytest.mark.parametrize("choice", ["cancel", "invalid"])
def test_cancel_or_invalid_restore_folder_never_replaces_open_target(window, tmp_path, monkeypatch, choice):
    win, _ = window
    target = independent_copy(win, tmp_path)
    calls = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "" if choice == "cancel" else str(tmp_path))
    monkeypatch.setattr(QProcess, "startDetached", lambda *a: calls.append(a))
    dialog = dialog_for(win)
    try:
        dialog.restored_directory = str(target)
        dialog.open_existing()
        assert not calls
        assert dialog.restored_directory == str(target)
        if choice == "invalid":
            assert "不是已完成校验" in dialog.status.text()
    finally:
        dialog.close(); dialog.deleteLater()
