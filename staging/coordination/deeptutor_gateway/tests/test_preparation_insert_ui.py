from PySide6.QtWidgets import QDialog, QTableWidgetItem
from test_preparation_insert import insertion
from test_preparation_sequence import sequence_candidate
from test_preparation_sequence_ui import close, select, view_for

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_insert_page_dialog import (
    PreparationInsertPageDialog,
)

pytest_plugins = ("test_desktop_ui",)


def fill(view):
    page = insertion()["page"]
    view.title.setText(page["title"])
    view.purpose.setText(page["purpose"])
    view.source_reference.setPlainText(page["source_reference"])
    view.content.setPlainText("\n".join(page["content"]))
    view.teacher_notes.setPlainText(page["teacher_notes"])
    view.minutes.setValue(2)


def test_form_preserves_inputs_after_error_and_only_previews(qt_app):
    view = PreparationInsertPageDialog(sequence_candidate(), "S04")
    view.add_button.click()
    assert not view.operation and "标题" in view.status.text()
    fill(view)
    assert "原页剩余8分钟" in view.remaining.text()
    view.add_button.click()
    assert view.operation == insertion()
    assert view.result() == QDialog.DialogCode.Accepted


def test_table_does_not_drop_unheaded_third_column_and_has_real_cells(qt_app):
    view = PreparationInsertPageDialog(sequence_candidate(), "S04")
    fill(view)
    view.kind.setCurrentIndex(1)
    assert view.tabs.isTabEnabled(1)
    for row, cells in enumerate(
        [
            ["维度", "甲", "乙"],
            ["定义", "甲的定义", "乙的定义"],
            ["条件", "甲的条件", "乙的条件"],
        ]
    ):
        for column, text in enumerate(cells):
            view.table.setItem(row, column, QTableWidgetItem(text))
    view.table.setItem(1, 3, QTableWidgetItem("不能丢失的多余内容"))
    view.add_button.click()
    assert not view.operation
    assert "不会截断" in view.status.text()
    assert view.table.item(1, 3).text() == "不能丢失的多余内容"
    view.table.item(1, 3).setText("")
    view.add_button.click()
    assert view.operation["page"]["visual"]["comparison"]["rows"] == [
        {"label": "定义", "values": ["甲的定义", "乙的定义"]},
        {"label": "条件", "values": ["甲的条件", "乙的条件"]},
    ]


def test_real_entry_queues_page_refreshes_lists_and_undo_removes_it(
    qt_app, monkeypatch
):
    view, _ = view_for(sequence_candidate())
    assert not view.sequence.insert_button.isEnabled()
    select(view, ["S04"])
    assert view.sequence.insert_button.isEnabled()

    def accept_editor(editor):
        fill(editor)
        editor.add_button.click()
        return editor.result()

    monkeypatch.setattr(PreparationInsertPageDialog, "exec", accept_editor)
    view.sequence.insert_button.click()
    assert view.structure.operations == [insertion()]
    assert view.sequence.pages[3]["id"] == "SLOCAL001"
    assert view.structure.slide.findData("SLOCAL001") >= 0
    assert view.save_button.isEnabled()
    select(view, ["SLOCAL001"])
    assert "完整知识句" in view.sequence.student_text.toPlainText()
    assert "测试讲义" not in view.sequence.student_text.toPlainText()
    assert "测试讲义" in view.sequence.teacher_text.toPlainText()
    view.sequence.undo_button.click()
    assert len(view.sequence.pages) == 6 and not view.save_button.isEnabled()
    close(view)
