"""A restored profile must be usable by the existing external-settings store."""
from pathlib import Path
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QFileDialog
from test_lesson_backup_ui import window, dialog_for
from test_lesson_backup import files
from test_work_organization import save_draft
from integrations.deeptutor_shchem_v1.desktop_backup import create_backup,plan_backup,inspect_backup


def test_restore_inside_application_is_rejected_before_any_write(window,tmp_path,monkeypatch):
    win,app=window;save_draft(win.facade)
    archive=tmp_path/'outside.zip'
    create_backup(plan_backup(win.facade.paths.state_root),archive)
    dialog=dialog_for(win);dialog.show()
    try:
        dialog.checked=inspect_backup(archive);dialog.archive_path=str(archive)
        project=win.facade.paths.workspace_root
        before=files(project)
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a,**k:str(project))
        dialog.restore()
        assert dialog._active is None and dialog.restored_directory is None
        assert '软件或源码目录以外' in dialog.status.text()
        assert files(project)==before
    finally:dialog.close();dialog.deleteLater()
