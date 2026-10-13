"""Responsive reading must leave usable space without reloading or accepting materials."""
from copy import deepcopy

from PySide6.QtCore import Qt
from test_textbook_reading_preparation_ui import (
    app as app, notes_page as notes_page, shelf_fixture as shelf_fixture,
    asset_fixture as asset_fixture, section_fixture as section_fixture, set_section,
)
from test_textbook_parallel_reading_ui import open_note
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE


def test_narrow_reading_collapses_directory_and_leaves_room_for_actual_page(notes_page, app):
    value, bridge, _ = notes_page
    value.setStyleSheet(WORKBENCH_STYLE);value.resize(540, 900);app.processEvents()
    reader = open_note(value, bridge, app)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked);app.processEvents()
    assert not value.tree.isVisible() and not value.query.isVisible()
    assert value.directory_button.isVisible() and not value.directory_button.isChecked()
    assert reader.view.viewport().height() >= 300
    for button in (value.directory_button, value.focus_button, value.preview_button, value.confirm_button):
        origin = button.mapTo(value, button.rect().topLeft())
        assert button.isVisible() and 0 <= origin.x() and origin.x() + button.width() <= value.width()
    assert not value.confirm_button.isEnabled() and not bridge.pending


def test_expanding_directory_keeps_page_and_chapter_change_releases_old_reader(notes_page, app):
    value, bridge, _ = notes_page
    value.resize(540, 900);app.processEvents()
    reader = open_note(value, bridge, app)
    value.directory_button.click();app.processEvents()
    assert value.tree.isVisible() and value.query.isVisible() and value._reader is reader
    assert reader.pages.currentData() == 10 and not bridge.pending
    set_section(value, 'TB-E2-C1:1.2');app.processEvents()
    assert value._reader is None and reader._closed and value.tree.isVisible()
    assert not value.focus_button.isChecked() and not value.focus_button.isEnabled()


def test_focus_and_return_restore_parallel_note_without_reload_or_selection_change(notes_page, app):
    value, bridge, _ = notes_page
    value.resize(1400, 900);app.processEvents()
    reader = open_note(value, bridge, app)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    chosen = deepcopy(value._note_checked)
    old_width = reader.view.viewport().width()
    value.focus_button.click();app.processEvents()
    assert reader.parentWidget() is value.reading and value.tabs.currentWidget() is value.reading
    assert not value.tree.isVisible() and not value.tabs.tabBar().isVisible()
    assert reader.view.viewport().width() > old_width * 1.5
    assert value.focus_button.text() == '返回选材' and value.status.isVisible()
    value.focus_button.click();app.processEvents()
    assert reader.parentWidget() is value.parallel_reader and value.tabs.currentIndex() == value.note_tab
    assert value.tree.isVisible() and value.tabs.tabBar().isVisible()
    assert reader.pages.currentData() == 10 and value._note_checked == chosen and not bridge.pending


def test_preview_exits_focus_and_shows_review_before_explicit_confirmation(notes_page, app):
    value, bridge, _ = notes_page
    value.resize(1400, 900);app.processEvents()
    reader = open_note(value, bridge, app)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    received = [];value.preparation_reference_requested.connect(received.append)
    value.focus_button.click();app.processEvents();value.preview_button.click()
    bridge.succeed(bridge.take('核对教材备课参考'), run=True);app.processEvents()
    assert not value.focus_button.isChecked() and value.tabs.currentIndex() == value.reference_tab
    assert value.reference_preview.isVisible() and value._reader is reader and not received
    assert value.confirm_button.isEnabled()


def test_same_breakpoint_resize_preserves_teacher_adjusted_directory_size(notes_page, app):
    value, _, _ = notes_page
    value.resize(1400, 900);app.processEvents();value.splitter.setSizes([380, 980]);app.processEvents()
    before = value.splitter.sizes();fraction = before[0] / sum(before)
    value.resize(1450, 900);app.processEvents()
    after = value.splitter.sizes()
    assert abs(after[0] / sum(after) - fraction) < 0.02


def test_reader_failure_remains_visible_in_focus_and_cannot_enable_confirmation(notes_page, app):
    value, bridge, _ = notes_page
    value.resize(1400, 900);app.processEvents();value._open_note();app.processEvents()
    reader = value._reader;pending = bridge.take('读取本节教材原页')
    value.focus_button.click();app.processEvents()
    bridge.fail(pending, '教材原文件版本已变化');app.processEvents()
    assert reader.status.isVisible() and '版本已变化' in reader.status.text()
    assert reader.document.pageCount() == 0 and not value.confirm_button.isEnabled()
    assert not value._note_checked and value.focus_button.isVisible()
