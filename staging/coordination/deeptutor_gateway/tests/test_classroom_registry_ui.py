"""Actual Qt roster/attendance and explicit spreadsheet profile handoffs."""

from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
)

from test_student_review_desk_core import case
from test_classroom_registry import roster, work
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.classroom_dialog import (
    ClassroomDialog,
    ExamRosterBindingDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.exam_import_dialog import (
    ExamImportDialog,
)
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_mapping_profiles import (
    ExamMappingProfiles,
)


@pytest.fixture
def desk(case):
    app = QApplication.instance() or QApplication([])
    f, _, _, _, _ = case
    saved = roster(f)
    dialog = ClassroomDialog(f)
    dialog.classes.setCurrentIndex(dialog.classes.findData(saved["id"]))
    dialog.show()
    settle(app)
    yield dialog, app, case, saved
    dialog.class_dirty = dialog.work_dirty = False
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_roster_reopen_preserves_ids_and_explicit_profiles(desk):
    dialog, _, _, saved = desk
    assert dialog.members.rowCount() == 4
    assert not dialog.class_dirty
    assert (
        dialog.members.item(0, 0).data(Qt.ItemDataRole.UserRole)
        == saved["members"][0]["member_id"]
    )
    assert (
        dialog.members.cellWidget(0, 2).currentData()
        == saved["members"][0]["profile_id"]
    )


def test_actual_attendance_ui_saves_and_emits_existing_batch(desk):
    dialog, app, (f, _, subs, _, _), saved = desk
    task = work(f, saved)
    dialog._reload_works(task["id"])
    row = next(
        i
        for i, member in enumerate(saved["members"])
        if member["profile_id"] == subs[0].student_id
    )
    status = dialog.entries.cellWidget(row, 1)
    status.setCurrentIndex(status.findData("submitted"))
    sources = dialog.entries.cellWidget(row, 2)
    sources.setCurrentIndex(sources.findData(subs[0].submission_id))
    assert dialog.work_dirty and dialog.save_work()
    assert (
        "已交 1" in dialog.work_summary.text()
        and "未录入 3" in dialog.work_summary.text()
    )
    captured = []
    dialog.batch_requested.connect(captured.append)
    dialog.open_review()
    settle(app)
    assert len(captured) == 1 and dialog.result() == QDialog.DialogCode.Accepted


def test_saving_roster_does_not_discard_unsaved_attendance(desk):
    dialog, _, (f, _, _, _, _), saved = desk
    task = work(f, saved)
    dialog._reload_works(task["id"])
    dialog.entries.item(0, 3).setText("尚未保存的收交依据")
    assert dialog.work_dirty
    dialog.label.setText("新班名")
    assert dialog.save_class()
    assert (
        dialog.work_dirty and dialog.entries.item(0, 3).text() == "尚未保存的收交依据"
    )


def test_cancel_roster_switch_keeps_editor_and_original_scope(desk, monkeypatch):
    dialog, _, (f, _, _, _, _), saved = desk
    other = roster(f, label="另一班")
    dialog._reload_classes(saved["id"])
    dialog.members.item(0, 0).setText("未保存的新名字")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    dialog.classes.setCurrentIndex(dialog.classes.findData(other["id"]))
    assert dialog.classroom["id"] == saved["id"]
    assert dialog.classes.currentData() == saved["id"]
    assert dialog.members.item(0, 0).text() == "未保存的新名字"


def test_short_window_keeps_roster_and_work_actions_inside_dialog(desk):
    dialog, app, _, _ = desk
    dialog.resize(800, 700)
    settle(app)
    assert dialog.width() == 800 and dialog.height() == 700
    for tab in range(2):
        dialog.tabs.setCurrentIndex(tab)
        settle(app)
        for widget in (
            *dialog.findChildren(QTableWidget),
            *dialog.findChildren(QPushButton),
        ):
            if widget.isVisible():
                assert dialog.rect().contains(
                    QRect(widget.mapTo(dialog, widget.rect().topLeft()), widget.size())
                )


def test_exam_roster_dialog_does_not_guess_by_matching_names(desk, tmp_path):
    dialog, app, (f, _, _, _, _), saved = desk
    _, _, exam = example(tmp_path / "exam.xlsx")
    exam["students"][0]["local_label"] = saved["members"][0]["label"]
    binding = ExamRosterBindingDialog(f, exam)
    assert all(
        not binding.table.cellWidget(r, 2).currentData()
        for r in range(binding.table.rowCount())
    )
    selected = binding.table.cellWidget(0, 2)
    selected.setCurrentIndex(selected.findData(saved["members"][0]["member_id"]))
    binding.commit()
    assert binding.result() == QDialog.DialogCode.Accepted
    assert dialog.registry.exam_binding(exam)["valid"]
    binding.deleteLater()
    app.processEvents()


def test_apply_profile_through_confirmation_preserves_current_exam_title(
    desk, tmp_path
):
    _, app, (f, _, _, _, _), _ = desk
    book, config, _ = example(tmp_path / "scores.xlsx")
    ExamMappingProfiles(f._state).save("已核对的方案", book, config)
    dialog = ExamImportDialog(book, state=f._state)
    dialog.title.setText("本次考试名称")
    dialog.profiles.setCurrentIndex(1)

    def approve():
        QApplication.activeModalWidget().accept()

    QTimer.singleShot(80, approve)
    dialog.apply_profile()
    assert dialog.config()["pass_score"] == config["pass_score"]
    assert dialog.title.text() == "本次考试名称" and "已复用" in dialog.message.text()
    changed = deepcopy(book)
    changed["sheets"][0]["rows"][0][0] = "不同表头"
    dialog.book = changed
    before = dialog.config()
    QTimer.singleShot(80, lambda: QApplication.activeModalWidget().reject())
    dialog.apply_profile()
    assert dialog.config() == before
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("change", ["class", "clear"])
def test_cancelling_binding_scope_change_preserves_unsaved_row_selection(
    desk, tmp_path, monkeypatch, change
):
    _, app, (f, _, _, _, _), saved = desk
    roster(f, label="另一班")
    _, _, exam = example(tmp_path / "exam.xlsx")
    dialog = ExamRosterBindingDialog(f, exam)
    dialog.classes.setCurrentIndex(dialog.classes.findData(saved))
    chosen = dialog.table.cellWidget(0, 2)
    chosen.setCurrentIndex(chosen.findData(saved["members"][0]["member_id"]))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    if change == "class":
        dialog.classes.setCurrentIndex(1 - dialog.classes.currentIndex())
    else:
        dialog.clear_existing.setChecked(True)
    assert dialog.classes.currentData()["id"] == saved["id"]
    assert not dialog.clear_existing.isChecked()
    assert (
        dialog.table.cellWidget(0, 2).currentData() == saved["members"][0]["member_id"]
    )
    assert dialog._dirty
    assert not dialog.registry.exam_binding(exam)["binding"]
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()
