"""Actionable C08 backlog, using temporary synthetic storage and real editors."""
from copy import deepcopy

import pytest

from test_word_question_dialog import (
    _AttributeFacade, _Tasks, _teaching_catalog, qt_app,
    WordQuestionAttributesDialog, WordQuestionDialog,
)
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from integrations.deeptutor_shchem_v1.desktop_library_progress import collect_library_progress
from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import LibraryProgressDialog


class ProgressFacade(_AttributeFacade):
    def __init__(self, root):
        super().__init__(root)
        self.catalog["attribute_catalog"] = _teaching_catalog()

    def personal_visual_questions(self):
        return {"items": [], "warnings": []}


@pytest.fixture
def progress(qt_app, tmp_path):
    facade, tasks = ProgressFacade(tmp_path / "personal"), _Tasks()
    dialog = LibraryProgressDialog(facade, tasks)
    tasks.flush()
    dialog.show()
    qt_app.processEvents()
    yield dialog, facade, tasks
    dialog.reject()
    tasks.flush()
    dialog.deleteLater()
    qt_app.processEvents()


def _select(dialog, key):
    index = next(i for i, row in enumerate(dialog.records) if row["key"] == key)
    dialog.table.selectRow(index)


def test_filters_cover_missing_fields_protection_and_question_search(progress):
    dialog, facade, tasks = progress
    assert len(dialog.records) == 3 and dialog.open_button.isEnabled()
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("primary"))
    dialog.search.setText("葡萄糖")
    dialog._filter_rows()
    assert [row["key"] for row in dialog.records] == ["Q2"]
    dialog.protection_filter.setCurrentIndex(dialog.protection_filter.findData(True))
    assert not dialog.records and not dialog.open_button.isEnabled()
    assert "筛选" in dialog.empty_state.text()
    facade.catalog["items"][1]["attribute_warning"] = "原教师标签需重新核对"
    dialog.refresh()
    tasks.flush()
    assert [row["key"] for row in dialog.records] == ["Q2"]
    assert "人工核对" in dialog.selection_detail.text()
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("stale"))
    assert dialog.records[0]["key"] == "Q2"
    assert not facade.store.path.exists()


@pytest.mark.parametrize("save_failure", [False, True])
def test_clickthrough_real_editor_reopen_retains_filter_identity_and_current_tags(
        progress, qt_app, monkeypatch, save_failure):
    dialog, facade, tasks = progress
    dialog.source_filter.setCurrentIndex(dialog.source_filter.findData("A"))
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("exam"))
    _select(dialog, "Q3")
    opened = []

    def accept_editor(editor):
        editor.teacher_note.setPlainText("合成教师明确保存的备注")
        editor._preview()
        editor._confirm()
        return editor.result()

    monkeypatch.setattr(WordQuestionAttributesDialog, "exec", accept_editor)
    if save_failure:
        def failed_save(*args, **kwargs):
            raise RuntimeError("synthetic save failure")
        monkeypatch.setattr(facade, "word_question_save_attributes", failed_save)

    def open_word(word):
        tasks.flush()
        opened.append((word._current_key, word._items[word._current_key]["revision"]))
        assert word._current_key == "Q3"
        assert word.question_tools_button.isChecked()
        if len(opened) == 1:
            word.attributes_button.click()
            tasks.flush()
            if save_failure:
                assert word.status.objectName() == "StatusError"
            else:
                assert "合成教师明确保存" in word.attributes_preview.toPlainText()
        word.reject()
        return word.result()

    monkeypatch.setattr(WordQuestionDialog, "exec", open_word)
    # Click enters the actual WordQuestionDialog and its actual teaching editor.
    dialog.open_button.click()
    tasks.flush()
    assert dialog.source_filter.currentData() == "A"
    assert dialog.todo_filter.currentData() == "exam"
    assert dialog.records[dialog.table.currentRow()]["key"] == "Q3"
    assert "exam" in dialog.records[dialog.table.currentRow()]["todo_keys"]
    assert dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()
    assert facade.store.path.exists() is not save_failure
    dialog.table.setFocus()
    QTest.keyClick(dialog.table, Qt.Key.Key_Return)
    tasks.flush()
    assert opened == [("Q3", "revision-Q3"), ("Q3", "revision-Q3")]
    row = next(row for row in dialog.report["word"]["rows"] if row["key"] == "Q3")
    assert row["protected"] is not save_failure


def test_cancel_editor_never_writes_and_still_refreshes(progress, monkeypatch):
    dialog, facade, tasks = progress
    catalog_calls = len([call for call in facade.calls if call[0] == "catalog"])

    def cancel(editor):
        editor.teacher_note.setPlainText("取消的预览，不应保存")
        editor._preview()
        editor.reject()
        return editor.result()

    def browse(word):
        tasks.flush()
        word.attributes_button.click()
        tasks.flush()
        word.reject()
        return word.result()

    monkeypatch.setattr(WordQuestionAttributesDialog, "exec", cancel)
    monkeypatch.setattr(WordQuestionDialog, "exec", browse)
    dialog.open_button.click()
    tasks.flush()
    assert not facade.store.path.exists()
    assert len([call for call in facade.calls if call[0] == "catalog"]) >= catalog_calls + 2
    assert len(dialog.records) == 3


def test_refresh_keeps_identity_after_reorder_then_nearest_remaining_position(progress):
    dialog, facade, tasks = progress
    _select(dialog, "Q2")
    facade.catalog["items"] = list(reversed(facade.catalog["items"]))
    dialog.refresh()
    tasks.flush()
    assert dialog.records[dialog.table.currentRow()]["key"] == "Q2"
    facade.catalog["items"] = [row for row in facade.catalog["items"] if row["key"] != "Q2"]
    dialog.refresh()
    tasks.flush()
    assert dialog.table.currentRow() == 1
    assert dialog.records[1]["key"] == "Q1"


def test_resolved_filter_retains_source_and_does_not_jump_to_other_sources(progress):
    dialog, facade, tasks = progress
    dialog.source_filter.setCurrentIndex(dialog.source_filter.findData("B"))
    facade.catalog["items"] = [row for row in facade.catalog["items"] if row["source_id"] != "B"]
    dialog.refresh()
    tasks.flush()
    assert dialog.source_filter.currentData() == "B"
    assert not dialog.records and not dialog.open_button.isEnabled()
    assert "筛选" in dialog.empty_state.text()


def test_resolved_todo_leaves_other_gaps_and_selects_next_at_same_position(progress):
    dialog, facade, tasks = progress
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("exam"))
    _select(dialog, "Q2")
    item = facade.catalog["items"][1]
    item["attributes"] = {"key": item["key"], "question_revision": item["revision"],
        "source_sha256": item["source_sha256"], "original_source": {
            "exam_type": {"value": "school_exam", "status": "source_observed"}}}
    dialog.refresh()
    tasks.flush()
    assert dialog.todo_filter.currentData() == "exam"
    assert dialog.records[dialog.table.currentRow()]["key"] == "Q3"
    unresolved = next(row for row in dialog.report["word"]["rows"] if row["key"] == "Q2")
    assert "primary" in unresolved["todo_keys"] and "exam" not in unresolved["todo_keys"]


def test_large_backlog_renders_bounded_table_and_searches_all_rows(progress):
    dialog, facade, tasks = progress
    base = facade.catalog["items"][0]
    facade.catalog["items"] = [{**deepcopy(base), "key": f"large-{i}", "title": f"合成题名-{i}"}
                                for i in range(1255)]
    dialog.refresh()
    tasks.flush()
    assert dialog.table.rowCount() == 1255
    assert dialog.word_count.text() == "1,255 条"
    assert dialog.table.height() <= 302
    dialog.search.setText("合成题名-1254")
    dialog._filter_rows()
    assert [row["key"] for row in dialog.records] == ["large-1254"]
    assert dialog.open_button.isEnabled()


def test_double_click_opens_once_and_open_failure_refreshes(progress, monkeypatch):
    dialog, facade, tasks = progress
    seen = []

    def unavailable(*args, **kwargs):
        seen.append(True)
        raise RuntimeError("synthetic open failure")

    monkeypatch.setattr(WordQuestionDialog, "exec", unavailable)
    dialog.table.cellDoubleClicked.emit(0, 1)
    tasks.flush()
    assert seen == [True]
    assert "原题窗口未能打开" in dialog.status.text()
    assert dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()


def test_zero_is_distinct_from_failure_and_retry_keeps_filters(progress):
    dialog, facade, tasks = progress
    dialog.todo_filter.setCurrentIndex(dialog.todo_filter.findData("exam"))
    facade.catalog["items"] = []
    dialog.refresh()
    tasks.flush()
    assert dialog.word_count.text() == "0 条" and dialog.visual_count.text() == "0 道"
    assert "没有标签缺项" in dialog.empty_state.text()
    facade.word_question_catalog = lambda: (_ for _ in ()).throw(RuntimeError("private path"))
    dialog.refresh()
    tasks.flush()
    assert dialog.word_count.text() == "暂不可读" and dialog.visual_count.text() == "0 道"
    assert "暂不可读" in dialog.empty_state.text()
    assert not dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()
    assert dialog.todo_filter.currentData() == "exam"
    assert "private path" not in dialog.status.text()


def test_stale_success_failure_cancel_and_close_callbacks_cannot_restore_actions(progress):
    dialog, facade, tasks = progress
    previous = deepcopy(dialog.report)
    dialog.refresh()
    stale = tasks.pending[-1]
    dialog.refresh()
    active = tasks.pending[-1]
    assert stale["task_id"] in tasks.cancelled
    active["on_success"](previous)
    stale["on_failure"]("旧错误")
    stale["on_success"]({**previous, "word": None})
    assert dialog.word_count.text() == "3 条" and dialog.open_button.isEnabled()
    dialog.refresh()
    cancelled = tasks.pending[-1]
    dialog.cancel_button.click()
    cancelled["on_success"](previous)
    assert not dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()
    dialog.refresh()
    closed = tasks.pending[-1]
    dialog.reject()
    final = dialog.status.text()
    closed["on_success"](previous)
    closed["on_failure"]("窗口关闭后的错误")
    assert dialog.status.text() == final and closed["task_id"] in tasks.cancelled


def test_failure_then_retry_and_export_failure_leave_retry_accessible(progress, monkeypatch, tmp_path):
    dialog, facade, tasks = progress
    dialog.refresh()
    tasks.pending[-1]["on_failure"]("合成读取失败")
    assert not dialog.open_button.isEnabled() and dialog.refresh_button.isEnabled()
    dialog.refresh()
    tasks.flush()
    assert dialog.open_button.isEnabled()
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_progress_dialog import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path), "JSON"))
    dialog.save.click()
    assert "未保存" in dialog.status.text()
    assert dialog.refresh_button.isEnabled() and dialog.open_button.isEnabled()


@pytest.mark.parametrize("width,height", [(1080, 840), (420, 700), (360, 560)])
def test_narrow_layout_has_no_hidden_horizontal_controls_and_all_actions_reachable(progress, qt_app, width, height):
    dialog, _, _ = progress
    dialog.resize(width, height)
    for _ in range(4):
        qt_app.processEvents()
    assert dialog.width() == width
    assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
    for widget in (dialog.search, dialog.source_filter, dialog.todo_filter, dialog.protection_filter,
                   dialog.table, dialog.open_button, dialog.refresh_button, dialog.save, dialog.close_button):
        dialog.body_scroll.ensureWidgetVisible(widget, 0, 0)
        qt_app.processEvents()
        origin = widget.mapTo(dialog.body_scroll.viewport(), QPoint(0, 0))
        assert origin.x() >= 0
        assert origin.x() + widget.width() <= dialog.body_scroll.viewport().width()
        if widget is not dialog.table:
            assert origin.y() < dialog.body_scroll.viewport().height()
            assert origin.y() + widget.height() > 0


def test_word_deep_link_uses_fresh_source_and_revision_and_missing_key_is_not_replaced(qt_app, tmp_path):
    facade, tasks = ProgressFacade(tmp_path), _Tasks()
    facade.catalog["items"][1]["revision"] = "new-source-revision"
    dialog = WordQuestionDialog(facade, tasks, initial_source_id="A", initial_question_key="Q2")
    tasks.flush()
    assert dialog._current_key == "Q2"
    assert dialog.source_combo.currentData() == "B"
    assert dialog._items["Q2"]["revision"] == "new-source-revision"
    dialog.reject()
    tasks.flush()
    missing = WordQuestionDialog(facade, tasks, initial_source_id="A", initial_question_key="deleted-key")
    tasks.flush()
    assert missing._current_key is None and not missing.attributes_button.isEnabled()
    assert "未选中其他题" in missing.status.text()
    missing.reject()
    tasks.flush()
