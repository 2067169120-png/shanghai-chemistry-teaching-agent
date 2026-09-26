from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from test_desktop_visual_import_ui import qt_app, _manual_task_bridge, _receipt, _settle, _Facade
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopNativeImportFile
from integrations.deeptutor_shchem_v1.desktop_workbench.import_batch_dialog import ImportBatchDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog


def _failed_receipt():
    return replace(_receipt(status="failed", visual_status="not_required", visual_queue_count=0),
        native_failed_count=1, native_completed_count=1, native_revision="private-revision",
        native_files=(DesktopNativeImportFile("success-id", "已完成（解析版）.docx", "completed", 1),
                      DesktopNativeImportFile("failed-id", "待重试（解析版）.docx", "failed", 1, "docx_parse_failed")))


class Facade(_Facade):
    def __init__(self):
        super().__init__()
        self.receipt = _failed_receipt()
        self.calls = []

    def import_batch_details(self, batch_id):
        assert batch_id == self.receipt.batch_id
        return self.receipt

    def retry_failed_word_import_files(self, batch_id, **kwargs):
        self.calls.append((batch_id, kwargs))
        self.receipt = replace(self.receipt, status="candidate_ready_for_review", native_failed_count=0,
            native_completed_count=2, native_revision="new-revision",
            native_files=(self.receipt.native_files[0], replace(self.receipt.native_files[1], status="completed", attempt_count=2)))
        return self.receipt


def test_explicit_selection_confirmation_and_save_readback(qt_app, monkeypatch):
    facade, tasks = Facade(), _manual_task_bridge()
    dialog = ImportBatchDialog(facade, tasks, facade.receipt.batch_id)
    dialog.show()
    assert not dialog.retry_button.isEnabled()
    assert not dialog.files.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    dialog.files.item(1).setCheckState(Qt.CheckState.Checked)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    dialog.retry_button.click()
    assert tasks.pending == []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.retry_button.click()
    assert len(tasks.pending) == 1 and not dialog.close_button.isEnabled()
    dialog.accept()
    assert dialog.isVisible()
    task = tasks.pending[0]
    task.on_success(task.operation(lambda *_: None, lambda: False))
    tasks.task_finished.emit(task.task_id)
    assert facade.calls[0][1] == {"expected_revision": "private-revision", "source_ids": ["failed-id"]}
    assert dialog.changed and "失败 0" in dialog.summary.text()
    assert not dialog.retry_button.isEnabled()
    assert dialog.files.item(1).checkState() == Qt.CheckState.Unchecked
    assert dialog.close_button.isEnabled()
    dialog.close()


def test_failure_discards_stale_selection_until_refresh(qt_app, monkeypatch):
    facade, tasks = Facade(), _manual_task_bridge()
    dialog = ImportBatchDialog(facade, tasks, facade.receipt.batch_id)
    dialog.files.item(1).setCheckState(Qt.CheckState.Checked)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.retry_button.click()
    task = tasks.pending[0]
    task.on_failure("状态已变化")
    tasks.task_finished.emit(task.task_id)
    assert dialog.receipt is None and not dialog.retry_button.isEnabled()
    assert dialog.files.count() == 0
    dialog.refresh()
    assert dialog.files.count() == 2 and not dialog.retry_button.isEnabled()
    dialog.close()


def test_history_selection_opens_chosen_batch_and_keeps_other_batches(qt_app):
    facade, tasks = Facade(), _manual_task_bridge()
    first = replace(_receipt(), batch_id="first", created_at="2026-09-26T09:00:00Z")
    second = replace(_receipt(), batch_id="second", created_at="2026-09-27T09:00:00Z")
    facade.list_import_batches = lambda: (first, second)
    dialog = ImportDialog(facade, tasks)
    dialog.show()
    _settle(qt_app)
    assert dialog.resume_combo.count() == 2
    dialog.resume_combo.setCurrentIndex(0)
    dialog.resume_button.click()
    assert dialog._saved_visual_receipt.batch_id == "first"
    assert dialog.resume_card.isVisible()
    dialog._load_resumable_batches()
    assert dialog.resume_combo.currentData() == "first"
    dialog.close()
