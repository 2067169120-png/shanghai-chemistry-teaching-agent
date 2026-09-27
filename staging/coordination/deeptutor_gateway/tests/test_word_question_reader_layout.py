"""Reader short-window geometry and real synthetic action contracts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea

from test_word_question_dialog import _isolated_qt_app, WORKBENCH_STYLE
from runtime.deeptutor_shchem.word_question_reader_layout_qa import (
    QueuedTasks, ReaderFixture, footer_evidence, fully_visible, prepare, reveal, settle,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog


@pytest.fixture
def qt_app():
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        create_application(["synthetic-reader-contract"])
        yield app


@pytest.mark.parametrize("width,height", [(360, 520), (420, 520), (900, 760), (1200, 820)])
@pytest.mark.parametrize("mode", ["ready", "source_empty", "search_empty", "loading", "error"])
def test_status_and_close_fully_visible_with_actual_wrapped_text(qt_app, width, height, mode):
    dialog, fixture, tasks = prepare(mode, width, height, qt_app)
    original_status = dialog.status
    assert (dialog.width(), dialog.height()) == (width, height)
    assert dialog.reader_footer.isAncestorOf(dialog.status)
    assert not dialog.body_scroll.isAncestorOf(dialog.status)
    assert not dialog.body_scroll.isAncestorOf(dialog.close_button)
    for amount in (0, dialog.body_scroll.verticalScrollBar().maximum()):
        dialog.body_scroll.verticalScrollBar().setValue(amount)
        settle(qt_app)
        evidence = footer_evidence(dialog)
        assert evidence["status_complete"] and evidence["close_complete"], evidence
        assert evidence["status_unclipped"], evidence
        assert evidence["horizontal_overflow"] == 0
        assert evidence["body_height"] >= 100
        assert dialog.status is original_status
    if mode == "source_empty":
        assert dialog.detail_title.text() == "原来源暂无可用题目"
        assert "返回查看原文或原批次进度" in dialog._panel_layouts[0].itemAt(0).widget().text()
        assert not dialog.source_combo.isEnabled()
    if mode == "search_empty":
        assert dialog.detail_title.text() == "没有符合条件的题目"
        assert "筛选条件或搜索词" in dialog._panel_layouts[0].itemAt(0).widget().text()
    if mode == "error":
        assert dialog.status.objectName() == "StatusError" and fixture.error == dialog.status.text()
    assert not any(call[0] in {"save", "basket", "reference"} for call in fixture.calls)
    dialog.close(); tasks.flush()


def test_empty_height_reacts_to_loading_filter_and_selected_preview_without_resize(qt_app):
    fixture, tasks = ReaderFixture(saved=[{"key": "Q2", "revision": "revision-Q2", "points": 4}]), QueuedTasks()
    dialog = WordQuestionDialog(fixture, tasks, required_source_id="MISSING")
    dialog.resize(360, 520); dialog.show(); settle(qt_app)
    empty_height = dialog.tabs.minimumHeight()
    tasks.flush(); settle(qt_app)
    assert dialog.tabs.minimumHeight() == empty_height == dialog.tabs.maximumHeight()
    assert dialog.question_list.count() == 0 and len(dialog.selections) == 1
    dialog.preparation_toggle.click(); dialog.include_images.setChecked(False)
    reveal(dialog, dialog.preview_button, qt_app)
    dialog.preview_button.click(); tasks.flush(); settle(qt_app)
    assert dialog.tabs.currentIndex() == 2 and dialog.tabs.minimumHeight() > empty_height
    assert dialog.import_button.isEnabled()
    dialog.tabs.setCurrentIndex(0); settle(qt_app)
    assert dialog.tabs.minimumHeight() == empty_height
    assert fixture.saved == [{"key": "Q2", "revision": "revision-Q2", "points": 4}]
    dialog.close(); tasks.flush()


@pytest.mark.parametrize("width", [360, 420])
def test_height_tracks_short_tall_and_empty_transitions_and_retains_nested_scroll(qt_app, width):
    dialog, _fixture, tasks = prepare("ready", width, 520, qt_app)
    short = dialog.tabs.minimumHeight()
    assert isinstance(dialog.tabs.widget(0), QScrollArea)
    assert isinstance(dialog.tabs.widget(1), QScrollArea)
    assert "长题面末尾" in "\n".join(label.text() for label in dialog._panels[0].findChildren(QLabel))
    dialog.resize(width, 900); settle(qt_app)
    assert dialog.tabs.minimumHeight() > short
    dialog.resize(width, 520); settle(qt_app)
    assert dialog.tabs.minimumHeight() == short
    dialog.search.setText("不存在的记录"); dialog._filter_items(); settle(qt_app)
    assert dialog.tabs.minimumHeight() < short
    dialog.search.clear(); dialog._filter_items(); settle(qt_app)
    assert dialog.tabs.minimumHeight() == short
    dialog.close(); tasks.flush()


@pytest.mark.parametrize("width", [759, 760])
def test_width_breakpoint_keeps_fixed_footer(qt_app, width):
    dialog, _fixture, tasks = prepare("ready", width, 520, qt_app)
    assert dialog.splitter.orientation() == (Qt.Orientation.Vertical if width < 760 else Qt.Orientation.Horizontal)
    assert footer_evidence(dialog)["status_complete"]
    dialog.close(); tasks.flush()


@pytest.mark.parametrize("width", [360, 420])
def test_disclosures_have_chinese_state_accessibility_and_reachable_controls(qt_app, width):
    dialog, _fixture, tasks = prepare("ready", width, 520, qt_app)
    for button, panel, title in [(dialog.question_tools_button, dialog.question_tools, "题目设置与标签"),
                                  (dialog.preparation_toggle, dialog.preparation_controls, "用所选题备课")]:
        reveal(dialog, button, qt_app)
        assert button.text() == title + "（展开）" and button.accessibleName() == "展开" + title
        button.click(); settle(qt_app)
        assert panel.isVisible() and button.text() == title + "（收起）"
        assert button.accessibleName() == "收起" + title
        assert "▸" not in button.text()
        controls = (dialog.range_button, dialog.attributes_button) if panel is dialog.question_tools else (dialog.preview_button, dialog.import_button)
        for control in controls:
            reveal(dialog, control, qt_app)
            assert fully_visible(control, dialog.body_scroll.viewport())
        assert footer_evidence(dialog)["status_unclipped"]
        reveal(dialog, button, qt_app)
        button.click(); settle(qt_app)
        assert not panel.isVisible() and button.text() == title + "（展开）"
    dialog.search.setFocus()
    QTest.keyClick(dialog.search, Qt.Key.Key_Return); settle(qt_app)
    assert not dialog._closed and not dialog.selections
    dialog.close(); tasks.flush()


@pytest.mark.parametrize("width", [360, 420])
def test_visible_bottom_actions_preserve_preview_then_explicit_basket_commit(qt_app, width):
    dialog, fixture, tasks = prepare("ready", width, 520, qt_app)
    reveal(dialog, dialog.select_current_button, qt_app)
    dialog.select_current_button.click(); dialog._persist_selection(); tasks.flush(); settle(qt_app)
    assert not dialog.basket_add_button.isEnabled()
    reveal(dialog, dialog.basket_preview_button, qt_app)
    dialog.basket_preview_button.click(); tasks.flush(); settle(qt_app)
    preview = dialog._basket_preview_dialog
    assert preview is not None and not preview.confirm_button.isEnabled()
    assert not any(call[0] == "basket" for call in fixture.calls)
    preview.tabs.setCurrentIndex(1); settle(qt_app)
    assert preview.confirm_button.isEnabled()
    preview.confirm_button.click(); preview.back_button.click(); settle(qt_app)
    assert dialog.basket_add_button.isEnabled()
    reveal(dialog, dialog.basket_add_button, qt_app)
    assert fully_visible(dialog.basket_add_button, dialog.body_scroll.viewport())
    dialog.basket_add_button.click()
    assert dialog._basket_busy and not dialog.basket_add_button.isEnabled()
    dialog.close_button.click(); settle(qt_app)
    assert not dialog._closed and "尚未完成" in dialog.status.text()
    assert footer_evidence(dialog)["status_unclipped"]
    tasks.flush(); settle(qt_app)
    assert len([call for call in fixture.calls if call[0] == "basket"]) == 1
    assert "已加入统一题篮" in dialog.status.text()
    dialog.close_button.click(); tasks.flush()
    assert dialog._closed


@pytest.mark.parametrize("width", [360, 420])
def test_preparation_confirmation_remains_explicit_and_rechecks_in_short_window(qt_app, width):
    dialog, fixture, tasks = prepare("ready", width, 520, qt_app)
    reveal(dialog, dialog.select_current_button, qt_app)
    dialog.select_current_button.click(); dialog._persist_selection(); tasks.flush()
    reveal(dialog, dialog.preparation_toggle, qt_app)
    dialog.preparation_toggle.click(); dialog.include_images.setChecked(False)
    reveal(dialog, dialog.preview_button, qt_app)
    dialog.preview_button.click(); tasks.flush(); settle(qt_app)
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.preparation_reference is None
    reveal(dialog, dialog.import_button, qt_app)
    dialog.import_button.click(); tasks.flush(); settle(qt_app)
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.preparation_reference
    assert len([call for call in fixture.calls if call[0] == "reference"]) == 2
    assert not any(call[0] == "basket" for call in fixture.calls)


def test_fixed_close_preserves_pending_selection_and_ignores_late_catalog(qt_app):
    dialog, fixture, tasks = prepare("ready", 360, 520, qt_app)
    dialog.select_current_button.click()
    dialog.close_button.click(); settle(qt_app)
    assert not dialog._closed and "正在保存本次勾选" in dialog.status.text()
    assert footer_evidence(dialog)["status_unclipped"]
    tasks.flush()
    assert dialog._closed and fixture.saved == dialog.selections
    late, late_fixture, late_tasks = prepare("loading", 360, 520, qt_app)
    late.close_button.click()
    assert late._closed
    late_tasks.finish(allow_cancelled=True)
    assert not late._items and late_fixture.saved == []
