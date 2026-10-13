"""Read at the original occurrence, return to classroom caption, and cancel cleanly."""
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from test_handout_images import lecture
from test_handout_image_context import contextual_lecture
from test_desktop_library_ui import _ManualBridge, qt_app
from test_desktop_studio_ui import settle
from test_composition_ui import desk, personal
from test_question_explorer_ui import window
from integrations.deeptutor_shchem_v1 import desktop_handout_images as handout
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_image_reader import HandoutImageReader
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_image_picker import HandoutImagePicker


@pytest.fixture
def reader(qt_app, contextual_lecture):
    f = contextual_lecture; bridge = _ManualBridge()
    value = HandoutImageReader(f.facade, handout.handout_image_context(f.facade, f.row), tasks=bridge)
    value.show(); settle(qt_app)
    yield value, bridge, f, qt_app
    value.reject(); value.deleteLater(); settle(qt_app)


def test_full_context_keeps_text_and_initial_original_image_and_explicit_selection(reader):
    value, bridge, f, app = reader
    assert '原题答案保留在原文中' in value.reader.browser.toPlainText()
    assert '选此段备课' not in value.reader.browser.toPlainText()
    assert not value.confirm.isEnabled() and value.selection is None
    bridge.succeed(bridge.take('在讲义原文中读取原图'), run=True)
    assert value.confirm.isEnabled() and value.reader._image_asset_id == f.row['asset_id']
    value.choose(); assert value.selection == f.row
    assert not f.facade.paths.task_root.exists()


def test_other_original_occurrence_is_selected_without_losing_whole_context(reader):
    value, bridge, f, app = reader; old = bridge.take('在讲义原文中读取原图')
    value.reader.image_requested.emit('word-b24-image1')
    bridge.succeed(old, handout.handout_image_preview(f.facade, f.row))
    assert not value.confirm.isEnabled() and value._previewed is None
    bridge.succeed(bridge.take('在讲义原文中读取原图'), run=True)
    assert value._previewed['asset_id'] == 'word-b24-image1'
    value.choose(); assert value.selection['block_index'] == 24
    assert '知识结构网' in value.reader.browser.toPlainText()


def test_image_reading_omits_extractor_reminders_and_keeps_original_snapshot(reader):
    value, bridge, f, app = reader
    original = deepcopy(f.value)
    original['warnings'] = ['自动解析提醒']
    block = next(b for b in original['blocks'] if b['index'] == 20)
    block.update(text='【待查看原文：图片或图形】', label='20 · 【待查看原文：图片或图形】', warnings=['图形未提取'])
    value.reader.set_source(original)
    text = value.reader.browser.toPlainText()
    assert '自动解析提醒' not in text and '图形未提取' not in text and '待查看原文' not in text
    assert '区块 20 · 原图' in text and '原题答案保留在原文中' in text
    assert value.reader._source == original


def test_close_during_image_read_ignores_late_original_pixels(reader):
    value, bridge, f, app = reader; pending = bridge.take('在讲义原文中读取原图')
    value.reject(); bridge.succeed(pending, handout.handout_image_preview(f.facade, f.row))
    assert value.selection is None and value._previewed is None and not value.confirm.isEnabled()
    assert pending.task_id in bridge.cancelled


def test_stale_original_image_blocks_selection_in_context(reader):
    value, bridge, f, app = reader; f.value['revision'] = 'c' * 64
    pending = bridge.take('在讲义原文中读取原图')
    with pytest.raises(ValueError, match='版本已变化'): pending.operation()
    bridge.fail(pending, '讲义版本已变化，请重新选择。')
    value.choose()
    assert not value.confirm.isEnabled() and value.selection is None and value._previewed is None


@pytest.mark.parametrize('width', [420, 1000])
def test_context_reading_and_confirm_are_reachable_with_wrapped_text(reader, width):
    value, bridge, f, app = reader; bridge.succeed(bridge.take('在讲义原文中读取原图'), run=True)
    value.resize(width, 740); settle(app)
    for widget in (value.reader, value.confirm, value.status):
        pos = widget.mapTo(value, widget.rect().topLeft())
        assert pos.x() >= 0 and pos.y() >= 0
        assert pos.x() + widget.width() <= value.width() and pos.y() + widget.height() <= value.height()
    assert value.reader.browser.horizontalScrollBar().maximum() == 0


def test_picker_searches_original_context_and_returns_from_reader_to_exact_occurrence(qt_app, contextual_lecture, monkeypatch):
    f = contextual_lecture; bridge = _ManualBridge()
    picker = HandoutImagePicker(f.facade, handout.handout_image_options(f.facade), [], tasks=bridge)
    picker.search.setText('氢键的形成条件')
    assert picker.list.count() == 1 and picker.current['block_index'] == 24
    picker.search.clear(); picker.focus_image(f.row)
    assert picker.source.currentIndex() == 1 and picker.source.currentText()
    assert tuple(picker.source.currentData()) == (f.row['batch_id'], f.row['source_id'])
    pending = bridge.take('读取讲义原图')
    # Filter transitions may have left cancelled operations queued.
    while pending.task_id in bridge.cancelled: pending = bridge.take('读取讲义原图')
    bridge.succeed(pending, run=True)
    picker.title.setText('结构怎样影响性质？'); picker.purpose.setPlainText('先看知识网再解释性质。')
    second = handout.handout_image_context(f.facade, f.row)['images'][1]
    def choose(value): value.selection = second; value.accept(); return QDialog.DialogCode.Accepted
    monkeypatch.setattr(HandoutImageReader, 'exec', choose)
    picker.read_original(); bridge.succeed(bridge.take('打开讲义原图的完整原文'), run=True)
    assert picker.current == second and picker.selection is None
    assert picker.edits[(f.row['batch_id'], f.row['source_id'], f.row['asset_id'])][0] == '结构怎样影响性质？'
    picker.reject(); picker.deleteLater(); settle(qt_app)
    assert not f.facade.paths.task_root.exists()


def test_selected_handout_continues_only_its_source_and_cancels_without_changing_lesson(desk, contextual_lecture, monkeypatch):
    d, panel, app, page = desk; f = contextual_lecture
    result = handout.import_handout_image(f.facade, [], [], f.selection)
    monkeypatch.setattr(d.facade, 'preparation_image_bytes', lambda _: f.data, raising=False)
    panel.add_result(result, digest(d.history.value)); before = deepcopy(d.history.value); calls = []
    original_context = handout.handout_image_context
    def context(_facade, row): calls.append(row); return original_context(f.facade, row)
    monkeypatch.setattr(handout, 'handout_image_context', context)
    monkeypatch.setattr(d, 'run', lambda _label, operation, done, **kw: done(operation()))
    monkeypatch.setattr(HandoutImagePicker, 'exec', lambda _: QDialog.DialogCode.Rejected)
    panel.add_handout_from_selected()
    assert calls == [f.row] and d.history.value == before


def test_scoped_picker_visibly_selects_source_and_bounds_large_context(qt_app, contextual_lecture):
    f = contextual_lecture
    f.value['blocks'][0]['text'] = '知识结构网' * 300
    bridge = _ManualBridge()
    value = HandoutImagePicker(f.facade, handout.handout_image_context(f.facade, f.row), [], tasks=bridge)
    value.resize(1080, 850); value.show(); settle(qt_app)
    assert value.source.currentIndex() == 1 and value.source.currentText().startswith('合成讲义')
    assert value.context.height() == 68 and value.context.toPlainText()
    assert value.editor_scroll.verticalScrollBar().value() == 0
    assert value.list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    value.reject(); value.deleteLater(); settle(qt_app)


def test_cancelled_context_read_never_opens_a_late_reader(qt_app, contextual_lecture, monkeypatch):
    f = contextual_lecture; bridge = _ManualBridge(); opened = []
    picker = HandoutImagePicker(f.facade, handout.handout_image_options(f.facade), [], tasks=bridge)
    bridge.succeed(bridge.take('读取讲义原图'), run=True)
    picker.read_original(); pending = bridge.take('打开讲义原图的完整原文')
    monkeypatch.setattr(HandoutImageReader, 'exec', lambda _: opened.append(True))
    picker.reject(); bridge.succeed(pending, run=True)
    assert not opened and picker.selection is None and not f.facade.paths.task_root.exists()
    picker.deleteLater(); settle(qt_app)
