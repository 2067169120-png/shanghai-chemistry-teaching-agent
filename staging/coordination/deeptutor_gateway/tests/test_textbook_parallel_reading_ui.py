"""Simultaneous original-page checks keep one verified reader and explicit selections."""
import pytest
from PySide6.QtCore import Qt
from test_textbook_reading_preparation_ui import (
    app as app, notes_page as notes_page, shelf_fixture as shelf_fixture,
    asset_fixture as asset_fixture, section_fixture as section_fixture, set_section, preview,
)
from test_textbook_bookshelf_ui import painted


def open_note(value, bridge, app):
    value.tabs.setCurrentIndex(value.note_tab)
    value._open_note();app.processEvents()
    bridge.succeed(bridge.take('读取本节教材原页'),run=True)
    app.processEvents()
    return value._reader


def test_wide_note_check_shows_original_and_full_note_together(notes_page, app):
    value,bridge,_=notes_page
    value.resize(1400,900);app.processEvents()
    reader=open_note(value,bridge,app)
    assert value.compare_button.isChecked() and value.parallel_reader.isVisible()
    assert reader.parentWidget() is value.parallel_reader
    assert value.tabs.currentIndex()==value.note_tab and value.note_detail.isVisible()
    assert '研读摘要专用内容' in value.note_detail.toPlainText()
    assert reader.pages.currentData()==10 and painted(app,reader)>10
    assert not value._checked and not value._note_checked and not value.confirm_button.isEnabled()


def test_resize_keeps_same_pdf_page_and_selection_without_rereading(notes_page, app):
    value,bridge,_=notes_page
    value.resize(1400,900);app.processEvents()
    reader=open_note(value,bridge,app)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    chosen=dict(value._note_checked)
    value.resize(420,900);app.processEvents()
    assert value._reader is reader and reader.parentWidget() is value.reading
    assert not value.parallel_reader.isVisible() and value.tabs.currentWidget() is value.reading
    assert reader.pages.currentData()==10 and value._note_checked==chosen and not bridge.pending
    assert painted(app,reader)>10
    value.resize(1400,900);app.processEvents()
    assert value._reader is reader and reader.parentWidget() is value.parallel_reader
    assert value.tabs.currentIndex()==value.note_tab and reader.pages.currentData()==10
    assert value._note_checked==chosen and not bridge.pending


def test_preview_keeps_original_visible_and_confirmation_remains_explicit(notes_page, app):
    value,bridge,_=notes_page
    value.resize(1400,900);app.processEvents()
    reader=open_note(value,bridge,app)
    received=[];value.preparation_reference_requested.connect(received.append)
    preview(value,bridge);app.processEvents()
    assert value.parallel_reader.isVisible() and value._reader is reader
    assert value.tabs.currentIndex()==value.reference_tab and not received
    value._confirm_reference();bridge.succeed(bridge.take('核对教材备课参考'),run=True)
    assert len(received)==1 and '"human_reviewed": false' in received[0]['materials']


def test_section_switch_closes_parallel_pdf_and_discards_late_reader_result(notes_page, app):
    value,bridge,f=notes_page
    value.resize(1400,900);app.processEvents()
    value._open_note();app.processEvents()
    pending=bridge.take('读取本节教材原页');old=value._reader
    set_section(value,'TB-E2-C1:1.2');app.processEvents()
    assert value._reader is None and old._closed and not value.parallel_reader.isVisible()
    bridge.succeed(pending,run=True);app.processEvents()
    assert value._reader is None and old.document.pageCount()==0 and not value._note_checked


def test_changed_original_fails_in_parallel_view_without_selecting(notes_page, app):
    value,bridge,f=notes_page
    value.resize(1400,900);app.processEvents()
    value._open_note();app.processEvents();pending=bridge.take('读取本节教材原页')
    source=f.root/f.row['source_path']
    with source.open('ab') as stream:stream.write(b'changed source')
    with pytest.raises(Exception):pending.operation()
    bridge.fail(pending,'教材原文件版本已变化')
    assert value._reader.document.pageCount()==0 and not value._note_checked
    assert '版本已变化' in value._reader.status.text() and not value.confirm_button.isEnabled()


def test_explicit_single_column_toggle_preserves_pdf_and_note_selection(notes_page, app):
    value,bridge,_=notes_page
    value.resize(1400,900);app.processEvents()
    reader=open_note(value,bridge,app)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    chosen=dict(value._note_checked)
    value.compare_button.setChecked(False);app.processEvents()
    assert value._reader is reader and reader.parentWidget() is value.reading
    assert value.tabs.isTabVisible(0) and value.tabs.currentWidget() is value.reading
    value.compare_button.setChecked(True);app.processEvents()
    assert value._reader is reader and reader.parentWidget() is value.parallel_reader
    assert value.tabs.currentIndex()==value.note_tab and value._note_checked==chosen
    assert not bridge.pending
