"""Actual Qt buttons on the backup workflow, no model/network calls."""
from pathlib import Path
from copy import deepcopy
import threading

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QFileDialog, QDialog
from test_desktop_studio_ui import window, settle
from test_lesson_backup import seed, files
from test_work_organization import save_draft, row, change
from integrations.deeptutor_shchem_v1.desktop_backup import (plan_backup,create_backup,inspect_backup,manifest_revision,restore_backup)
from integrations.deeptutor_shchem_v1.desktop_workbench.backup_dialog import BackupDialog


def dialog_for(win):
    return BackupDialog(win.facade.paths,win.tasks,win,flush_editor=win.preparation_page.recovery.flush)


def test_backup_entry_is_wired_to_real_dialog(window,monkeypatch):
    win,app=window;seen=[]
    monkeypatch.setattr(BackupDialog,'exec',lambda dialog:seen.append(dialog.paths) or QDialog.DialogCode.Rejected)
    win.navigate('mywork');win.my_work_page.backup_button.click()
    assert seen==[win.facade.paths]


def test_plan_flushes_partial_form_and_scope_change_invalidates_old_plan(window):
    win,app=window; save_draft(win.facade)
    win.preparation_page.topic.setText('还未写完的备课')
    dialog=dialog_for(win);dialog.show()
    try:
        dialog.plan_button.click();settle(app,lambda:dialog._active is None and dialog.plan is not None)
        assert dialog.plan.summary['drafts']==1 and dialog.plan.summary['recovery']
        assert dialog.save_button.isEnabled()
        dialog.images.setChecked(True)
        assert dialog.plan is None and not dialog.save_button.isEnabled()
    finally:dialog.close();dialog.deleteLater()


def test_real_save_check_restore_buttons_keep_current_state(window,tmp_path,monkeypatch):
    win,app=window;save_draft(win.facade)
    win.preparation_page.materials.setPlainText('未保存也应带走的合成材料')
    dialog=dialog_for(win);dialog.show();archive=tmp_path/'备份.zip';parent=tmp_path/'restore';parent.mkdir()
    try:
        dialog.plan_button.click();settle(app,lambda:dialog.plan is not None and dialog._active is None)
        before=win.facade.state_store.path.read_bytes();payload=deepcopy(win.preparation_page._payload())
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(archive),''))
        dialog.save_button.click();settle(app,lambda:dialog._active is None and archive.exists())
        dialog.tabs.setCurrentIndex(1)
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(archive),''))
        dialog.inspect_button.click();settle(app,lambda:dialog._active is None and dialog.checked is not None)
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a,**k:str(parent))
        dialog.restore_button.click();settle(app,lambda:dialog._active is None and dialog.restored_directory is not None)
        assert Path(dialog.restored_directory).is_dir() and dialog.open_button.isEnabled()
        assert win.facade.state_store.path.read_bytes()==before and win.preparation_page._payload()==payload
        calls=[]
        monkeypatch.setattr(QProcess,'startDetached',lambda *a:calls.append(a) or (True,123))
        dialog.open_button.click()
        assert calls[0][1][-2:]==['--personal-state',dialog.restored_directory]
    finally:dialog.close();dialog.deleteLater()


def test_cancelled_file_picker_does_not_write(window,monkeypatch):
    win,app=window;save_draft(win.facade)
    dialog=dialog_for(win);dialog.show()
    try:
        dialog.plan_button.click();settle(app,lambda:dialog.plan is not None and dialog._active is None)
        before=files(win.facade.paths.state_root)
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:('',''))
        dialog.save_button.click()
        assert dialog._active is None and files(win.facade.paths.state_root)==before
    finally:dialog.close();dialog.deleteLater()


def test_failed_recovery_flush_does_not_start_a_backup(window):
    win,app=window
    dialog=BackupDialog(win.facade.paths,win.tasks,win,flush_editor=lambda:False);dialog.show()
    dialog.plan_button.click()
    assert dialog._active is None and dialog.plan is None and '未能保存' in dialog.status.text()
    dialog.close();dialog.deleteLater()


def test_close_while_working_requests_cooperative_cancel(window):
    win,app=window;dialog=dialog_for(win);dialog.show()
    entered=threading.Event()
    def operation(cancel):
        from integrations.deeptutor_shchem_v1.desktop_backup import BackupCancelled
        import time
        entered.set()
        while not cancel():time.sleep(.01)
        raise BackupCancelled('cancelled')
    dialog._run('合成慢任务',operation,lambda _:None)
    settle(app,entered.is_set)
    dialog.close();assert dialog.isVisible()
    settle(app,lambda:dialog._active is None)
    assert '已取消' in dialog.status.text()
    dialog.close();dialog.deleteLater()


def test_bad_archive_error_reenables_controls_without_restore(window,tmp_path,monkeypatch):
    win,app=window;dialog=dialog_for(win);dialog.show()
    path=tmp_path/'broken.zip';path.write_bytes(b'broken')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),''))
    dialog.check_backup();settle(app,lambda:dialog._active is None)
    assert dialog.checked is None and not dialog.restore_button.isEnabled() and dialog.inspect_button.isEnabled()
    assert '无法' in dialog.status.text()
    dialog.close();dialog.deleteLater()
