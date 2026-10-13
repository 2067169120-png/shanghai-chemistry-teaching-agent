from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from test_desktop_library_ui import _ManualBridge
from test_desktop_studio_ui import window as window
from test_textbook_bookshelf_ui import app as app, facade_for, leaf, painted
from test_textbook_reading_preparation import (shelf_fixture as shelf_fixture, write_notes,
    asset_fixture as asset_fixture, section_fixture as section_fixture)


def set_section(value, key):
    value.tree.setCurrentItem(leaf(value, key))


@pytest.fixture
def notes_page(app, shelf_fixture):
    f = shelf_fixture
    write_notes(f)
    bridge = _ManualBridge()
    value = TextbookBookshelfPage(facade_for(f), bridge)
    value.resize(1100, 850);value.show();value.refresh()
    bridge.succeed(bridge.take("读取教材书架"), run=True)
    value.book.setCurrentIndex(value.book.findData(f.row["volume_id"]))
    set_section(value, f.row["section_key"])
    app.processEvents()
    yield value, bridge, f
    value.close();value.deleteLater();app.processEvents()


def preview(value, bridge):
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    value._preview_reference()
    bridge.succeed(bridge.take("核对教材备课参考"), run=True)


def test_reading_note_entry_locates_original_page_without_selecting(notes_page, app):
    value, bridge, _ = notes_page
    assert value.tree.currentItem().text(1) == "研读 1条"
    assert "1 条研读提示" in value.section_status.text()
    assert not value._checked and not value._note_checked
    value.tabs.setCurrentIndex(value.note_tab)
    assert "课堂核对建议" in value.note_detail.toPlainText()
    value._open_note()
    app.processEvents()
    bridge.succeed(bridge.take("读取本节教材原页"), run=True)
    assert value._reader.pages.currentData() == 10
    assert painted(app, value._reader) > 10
    assert not value._note_checked and not value.confirm_button.isEnabled()


def test_note_only_preview_and_confirmation_are_separate(notes_page):
    value, bridge, _ = notes_page
    received = []
    value.preparation_reference_requested.connect(received.append)
    preview(value, bridge)
    assert "研读摘要专用内容" in value.reference_preview.toPlainText()
    assert "限定于所列模型与条件" in value.reference_preview.toPlainText()
    assert "SHA-256" not in value.reference_preview.toPlainText()
    assert "SHA-256" in value.reference_details.toPlainText()
    assert not received and value.confirm_button.isEnabled() and not value._checked
    value._confirm_reference()
    assert not received
    bridge.succeed(bridge.take("核对教材备课参考"), run=True)
    assert len(received) == 1 and '"generation_allowed": false' in received[0]["materials"]
    value.reference_appended()
    assert not value.confirm_button.isEnabled()
    value._clear_selection()
    assert not value._note_checked and not value.preview_button.isEnabled()


@pytest.mark.parametrize("action", ["uncheck", "close"])
def test_late_preview_cannot_restore_a_discarded_note_selection(notes_page, action):
    value, bridge, _ = notes_page
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    value._preview_reference();pending = bridge.take("核对教材备课参考")
    if action == "uncheck":
        value.note_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    else:
        value.close()
    bridge.succeed(pending, run=True)
    assert value._reference is None and not value.confirm_button.isEnabled()


def test_changed_note_blocks_confirmation_then_refresh_drops_choice(notes_page):
    value, bridge, f = notes_page
    received = [];value.preparation_reference_requested.connect(received.append)
    preview(value, bridge)
    write_notes(f, summary="更新后的候选")
    value._confirm_reference();pending = bridge.take("核对教材备课参考")
    with pytest.raises(PreparationSourceError):
        pending.operation()
    bridge.fail(pending, "研读提示版本已变化")
    assert not received and value._reference is None
    value.refresh();bridge.succeed(bridge.take("读取教材书架"), run=True)
    assert not value._note_checked and "研读提示版本变化" in value.status.text()


def test_search_and_cross_chapter_selection_keep_the_correct_note(notes_page):
    value, _, f = notes_page
    value.query.setText("研读摘要专用内容")
    assert value.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == f.row["section_key"]
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    value.query.clear()
    set_section(value, "TB-E2-C1:1.2")
    assert value.note_list.count() == 0 and len(value._note_checked) == 1
    assert value.tree.currentItem().text(1) == "暂无研读提示"
    set_section(value, f.row["section_key"])
    assert value.note_list.item(0).checkState() == Qt.CheckState.Checked


def test_narrow_note_controls_fit_and_keep_full_detail(notes_page, app):
    value, _, _ = notes_page
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    value.setStyleSheet(WORKBENCH_STYLE);value.resize(420, 900)
    value.tabs.setCurrentIndex(value.note_tab);app.processEvents()
    for control in (value.book, value.tree, value.note_list, value.note_detail, value.note_button,
                    value.preview_button, value.confirm_button):
        origin = control.mapTo(value, control.rect().topLeft())
        assert control.isVisible() and 0 <= origin.x() and origin.x() + control.width() <= value.width()
    assert "限定于所列模型与条件" in value.note_detail.toPlainText()


def test_main_window_receives_note_reference_without_replacing_teacher_form(window, notes_page):
    win, app = window
    value, bridge, _ = notes_page
    prep = win.preparation_page
    prep.topic.setText("教师原课题");prep.objective.setPlainText("教师原目标")
    prep.materials.setPlainText("教师已有材料")
    before = deepcopy(prep._payload())
    value.preparation_reference_requested.connect(win._textbook_to_preparation)
    preview(value, bridge)
    value._confirm_reference();bridge.succeed(bridge.take("核对教材备课参考"), run=True);app.processEvents()
    after = prep._payload()
    assert after["materials"].startswith("教师已有材料") and "研读摘要专用内容" in after["materials"]
    after["materials"] = before["materials"]
    assert after == before and win.stack.currentWidget() is prep
