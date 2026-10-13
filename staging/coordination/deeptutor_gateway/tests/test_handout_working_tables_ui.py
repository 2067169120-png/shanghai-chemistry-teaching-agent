"""Isolated Qt cell-edit transactions using only in-memory source evidence.

The fixtures in this file are self-contained: QApplication plus installed font,
small memory PNGs, validated memory resources and a memory history owner. They
never construct MainWindow/Recovery or any source, profile or image store.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
pytest.importorskip('PySide6')
from PIL import Image
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QWidget

from integrations.deeptutor_shchem_v1.desktop_handout_knowledge import (
    SECTION_SCHEMA, SOURCE_SCHEMA, SOURCE_ROLES_SCHEMA)
from integrations.deeptutor_shchem_v1.desktop_handout_table_projection import source_table_projection
from integrations.deeptutor_shchem_v1.desktop_handout_working_tables import table_cell_model
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FORMATS, add_resource, block, edit_resource, enable_composition, new_composition, resource)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, new_design
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import _readable_table
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import (
    LessonCompositionPanel, ResourceEditor)
from integrations.deeptutor_shchem_v1.desktop_workbench.native_table_cell_editor import NativeTableCellEditor, SourceTextEdit
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    install_ui_font(application)
    yield application
    application.processEvents()


def _cell(text, span=1, merge='none'):
    return {'text': text, 'column_span': span, 'vertical_merge': merge}


def _row(*cells, before=0, after=0):
    return {'grid_before': before, 'grid_after': after,
        'cells': [_cell(c) if isinstance(c, str) else c for c in cells]}


def materials(*, images=True, notes=False, legacy=False, continuation_text='', first_text='名称', source_segments=None,
        page_format='16:9'):
    stream = BytesIO()
    Image.new('RGB', (120, 60), '#407752').save(stream, format='PNG')
    data = stream.getvalue(); fingerprint = sha256(data).hexdigest()
    asset = {'asset_id': 'IMG-' + fingerprint, 'sha256': fingerprint,
        'caption': '合成测试原图', 'source': '内存测试资料', 'purpose': '固定槽位测试', **image_info(data)}
    marker = '【待查看原文：图片或图形】'
    rich = 'CH_{3}－' + marker * 2 + '\nCOOH' if images else 'CH_{3}－COOH'
    if source_segments is not None:rich = marker.join(source_segments)
    rows = [_row(_cell(first_text, 2), '性质', '用途'),
        _row(_cell('合并起点', merge='restart'), rich, '性质内容', marker if images else '用途内容'),
        _row(_cell(continuation_text, merge='continue'), '第二个例子', 'CH_{2}与Fe^{2+}', '完整尾部'),
        _row('省略列后真实单元格', '末行', before=1, after=1)]
    refs = [dict(block_index=2, position=i, source_asset_id=f'memory-image-{i}',
        source_sha256='a' * 64, original_sha256=fingerprint, original_mime_type='image/png',
        asset_id=asset['asset_id'], preview_sha256=fingerprint, derived_preview=False,
        renderer_revision=None, source_role='handout', source_branch='direct', status='included',
        container_xml_sha256=str(i) * 64, width_mm=25, height_mm=12.5) for i in range(1, 4)] if images else []
    source = dict(schema=SOURCE_SCHEMA, batch_id='memory', source_id='memory-source',
        source_name='内存讲义.docx', source_sha256='a' * 64, source_role='handout',
        source_path='内存讲义.docx', source_revision='b' * 64)
    native = [dict(index=1, text='完整前文', images=[]),
        dict(index=2, text=_readable_table(rows), images=refs, table_rows=rows),
        dict(index=3, text='完整后文', images=[])]
    snapshot = dict(schema=SECTION_SCHEMA, outline_revision='c' * 64,
        section=dict(id='word-heading-b1', level=3, title='完整表格', start=1, end=3), blocks=native)
    assets = [asset] if images else []
    blocks = [part for row in native for part in [block('text', 'knowledge', row['text'], row['index']),
        *[block('image', 'knowledge', ref['asset_id'], row['index']) for ref in row['images']]]]
    item = resource('handout_knowledge', source['source_name'] + ' · 完整表格', blocks,
        dict(source_path=source['source_path'], source_sha256=source['source_sha256'],
            source_revision=source['source_revision'], handout_source=source, section_snapshot=snapshot,
            image_assets=assets, source_roles=dict(schema=SOURCE_ROLES_SCHEMA, teacher_only=False,
                document_role='handout', images=[{'asset_id': asset['asset_id'], 'roles': ['knowledge']}] if images else [])))
    text_id = item['blocks'][1]['id']
    if legacy:
        item['blocks'][1]['value'] = legacy if isinstance(legacy, str) else '教师完整自由改稿。\n保留原独立图片。'
    if notes:
        from integrations.deeptutor_shchem_v1.desktop_composition_exposition import SCHEMA
        item['exposition'] = {'schema': SCHEMA, 'kind': 'notes'}
    value = new_composition(page_format);width, height = FORMATS[page_format]
    value = add_resource(value, item, dict(page_id=value['pages'][0], x=12, y=12, width=width-24, height=height-24))
    return value, assets, {asset['asset_id']: data} if images else {}, item['id'], text_id


@pytest.fixture
def editors(app):
    opened = []
    def build(**kwargs):
        values = materials(**kwargs)
        dialog = ResourceEditor(values[0], values[3], assets=values[1], image_data=values[2])
        opened.append(dialog)
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(values[4]))
        return dialog, values
    yield build
    for dialog in opened:
        dialog.reject(); dialog.deleteLater()
    app.processEvents()


def _text(value, identity):
    return next(b for b in value['resources'][0]['blocks'] if b['id'] == identity)


def test_locked_open_cancel_and_noop_apply_do_not_create_working_tables(editors):
    dialog, values = editors()
    before = deepcopy(values)
    assert dialog.locked.isChecked()
    assert all(edit.isReadOnly() for edit in dialog.table_editor.segment_editors)
    dialog.table_editor.select_cell(2, 2)
    assert len(dialog.table_editor.segment_editors) == 3
    assert len(dialog.table_editor.image_previews) == 2
    assert all(not preview.source_pixmap.isNull() for preview in dialog.table_editor.image_previews)
    assert len({p.property('image_block_id') for p in dialog.table_editor.image_previews}) == 2
    dialog.apply_button.click()
    assert dialog.result_value == values[0]
    assert values == before
    assert 'working_tables' not in dialog.result_value['resources'][0]
    cancelled, source = editors()
    cancelled.locked.setChecked(False)
    cancelled.table_editor.segment_editors[0].setPlainText('取消的文字')
    cancelled.cancel_button.click()
    assert cancelled.result_value is None and 'working_tables' not in source[0]['resources'][0]


def test_locked_title_edit_keeps_plain_body_table_and_source_intact(editors):
    dialog, values = editors();before = deepcopy(values[0])
    dialog.blocks.setCurrentIndex(0)
    assert dialog.body.isReadOnly() and not dialog.title.isReadOnly()
    dialog.title.setText('我的课堂比较表');dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()
    item = dialog.result_value['resources'][0]
    assert item['title'] == '我的课堂比较表' and item['locked']
    assert item['blocks'] == before['resources'][0]['blocks']
    assert item['original'] == before['resources'][0]['original'] and 'working_tables' not in item


def test_two_tables_preserve_independent_staged_cells(app):
    value, assets, data, _, first_id = materials()
    first = value['resources'][0]
    evidence = deepcopy(first['original']['evidence'])
    second_native = deepcopy(evidence['section_snapshot']['blocks'][1]);second_native['index'] = 4
    for ref in second_native['images']:ref['block_index'] = 4
    evidence['section_snapshot']['blocks'].append(second_native)
    evidence['section_snapshot']['section']['end'] = 4
    blocks = deepcopy(first['original']['blocks'])
    second_text = block('text', 'knowledge', second_native['text'], 4)
    blocks.extend([second_text, *[block('image', 'knowledge', ref['asset_id'], 4) for ref in second_native['images']]])
    item = resource('handout_knowledge', first['original']['title'], blocks, evidence)
    value = new_composition('16:9')
    value = add_resource(value, item, dict(page_id=value['pages'][0], x=12, y=12, width=296, height=156))
    dialog = ResourceEditor(value, item['id'], assets=assets, image_data=data)
    try:
        dialog.locked.setChecked(False)
        for identity, replacement in ((first_id, '第一张表改稿'), (second_text['id'], '第二张表改稿')):
            dialog.blocks.setCurrentIndex(dialog.blocks.findData(identity))
            dialog.table_editor.select_cell(1, 1)
            dialog.table_editor.segment_editors[0].setPlainText(replacement)
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(first_id))
        assert dialog.table_editor.segment_editors[0].toPlainText() == '第一张表改稿'
        dialog.locked.setChecked(True);dialog.apply_button.click()
        assert dialog.result_value is not None, dialog.status.text()
        assert set(dialog.result_value['resources'][0]['working_tables']['tables']) == {first_id, second_text['id']}
    finally:dialog.deleteLater();app.processEvents()


def test_staging_survives_cells_plain_blocks_images_and_lockback(editors):
    dialog, values = editors(); before = deepcopy(values)
    dialog.locked.setChecked(False)
    table = dialog.table_editor
    table.select_cell(2, 2)
    replacement = ['  CH_{3}－\n', '', '\nCOOH与Fe^{2+}  ']
    for edit, text in zip(table.segment_editors, replacement):edit.setPlainText(text)
    table.select_cell(3, 3)
    table.segment_editors[0].setPlainText('CH_{2}与Fe^{3+}')
    dialog.blocks.setCurrentIndex(0)
    assert dialog.table_editor is None
    dialog.body.setPlainText('完整前文\n本作品补充')
    picture = next(b for b in dialog.item['blocks'] if b['kind'] == 'image')
    dialog.blocks.setCurrentIndex(dialog.blocks.findData(picture['id']))
    assert not dialog.image_preview.source_pixmap.isNull()
    assert picture['id'] not in dialog.image_description.text()
    dialog.image_location_button.click()
    assert dialog.table_editor is table
    assert [edit.toPlainText() for edit in table.segment_editors] == replacement
    dialog.locked.setChecked(True)
    assert dialog.item['locked'] and all(edit.isReadOnly() for edit in table.segment_editors)
    dialog.apply_button.click()
    result = dialog.result_value
    assert result is not None, dialog.status.text()
    item = result['resources'][0]
    assert item['original'] == before[0]['resources'][0]['original']
    assert item['blocks'][0]['value'] == '完整前文\n本作品补充'
    model = table_cell_model(item, values[4])
    assert next(c for c in model['cells'] if (c['row'], c['column']) == (2, 2))['segments'] == replacement
    assert len(source_table_projection(item, _text(result, values[4]))['occurrences']) == 3
    assert values == before


def test_lockback_validation_keeps_unlock_and_unsaved_text(editors):
    dialog, _ = editors(); dialog.locked.setChecked(False)
    dialog.table_editor.segment_editors[0].setPlainText('【待查看原文：注入')
    dialog.locked.setChecked(True)
    assert not dialog.locked.isChecked() and not dialog.item['locked']
    assert not dialog.table_editor.segment_editors[0].isReadOnly()
    assert dialog.table_editor.segment_editors[0].toPlainText() == '【待查看原文：注入'
    assert dialog.result_value is None and '占位' in dialog.status.text()
    dialog.table_editor.segment_editors[0].setPlainText('可保存的文字')
    dialog.locked.setChecked(True); dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()


def test_full_restore_omits_sidecar_and_preserves_chemistry_notation(editors):
    dialog, values = editors(); dialog.locked.setChecked(False)
    table = dialog.table_editor; table.select_cell(2, 2)
    original = [edit.toPlainText() for edit in table.segment_editors]
    table.segment_editors[0].setPlainText('临时修改');dialog.locked.setChecked(True)
    assert 'working_tables' in dialog.item
    dialog.locked.setChecked(False)
    for edit, text in zip(table.segment_editors, original):edit.setPlainText(text)
    dialog.locked.setChecked(True);dialog.apply_button.click()
    assert dialog.result_value == values[0]


@pytest.mark.parametrize('text', ['A\u00a0B', 'A\r\nB', 'A\u2028B', 'A\u2029B', 'A\rB\nC'])
def test_no_edit_navigation_and_lockback_preserve_exact_source_whitespace(editors, text):
    dialog, values = editors(first_text=text)
    dialog.locked.setChecked(False)
    dialog.table_editor.select_cell(2, 2);dialog.table_editor.select_cell(1, 1)
    dialog.blocks.setCurrentIndex(0);dialog.blocks.setCurrentIndex(dialog.blocks.findData(values[4]))
    dialog.locked.setChecked(True);dialog.apply_button.click()
    assert dialog.result_value == values[0], dialog.status.text()


def test_editing_one_image_segment_preserves_other_segments_and_special_breaks(editors):
    segments = ['前\u00a0文\r\n', '🌟重复\n重复\r\n尾', '\u2028图后\u2029尾']
    dialog, values = editors(source_segments=segments, page_format='A3');dialog.locked.setChecked(False)
    dialog.table_editor.select_cell(2, 2);edit = dialog.table_editor.segment_editors[1]
    cursor = edit.textCursor();cursor.setPosition(2);cursor.insertText('新\u00a0')
    expected = [segments[0], '🌟新\u00a0重复\n重复\r\n尾', segments[2]]
    dialog.locked.setChecked(True);dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()
    model = table_cell_model(dialog.result_value['resources'][0], values[4])
    assert next(c for c in model['cells'] if (c['row'], c['column']) == (2, 2))['segments'] == expected


def test_duplicate_lines_utf16_cursor_delete_and_undo_keep_exact_breaks(app):
    edit = SourceTextEdit();original = '🌟A\r\nB\nB\r\nC\u00a0'
    edit.set_source_text(original)
    try:
        cursor = edit.textCursor();cursor.setPosition(3);cursor.insertText('改')
        first = '🌟A改\r\nB\nB\r\nC\u00a0'
        assert edit.source_text() == first
        cursor = edit.textCursor();cursor.setPosition(5)
        cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor);cursor.removeSelectedText()
        after = '🌟A改\r\nB\r\nC\u00a0'
        assert edit.source_text() == after
        edit.undo();assert edit.source_text() == first
        edit.redo();assert edit.source_text() == after
        edit.undo();edit.undo();assert edit.source_text() == original
    finally:edit.deleteLater();app.processEvents()


def test_duplicate_raw_document_states_on_new_undo_branch_keep_distinct_breaks(app):
    edit = SourceTextEdit();original = 'A\r\nB\nB\r\nC';edit.set_source_text(original)
    def remove(start, end):
        cursor = edit.textCursor();cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor);cursor.removeSelectedText()
    try:
        remove(2, 4)
        assert edit.source_text() == 'A\r\nB\r\nC'
        edit.undo();assert edit.source_text() == original
        remove(4, 6)
        assert edit.source_text() == 'A\r\nB\nC'
        edit.undo();assert edit.source_text() == original
        edit.redo();assert edit.source_text() == 'A\r\nB\nC'
    finally:edit.deleteLater();app.processEvents()


def test_legacy_plain_text_noop_retains_exact_unicode_without_migration(editors):
    dialog, values = editors(legacy='旧稿\u00a0\r\n下一行\u2028末行\u2029尾')
    assert dialog.table_editor is None
    dialog.locked.setChecked(False);dialog.blocks.setCurrentIndex(0)
    dialog.blocks.setCurrentIndex(dialog.blocks.findData(values[4]))
    dialog.locked.setChecked(True);dialog.apply_button.click()
    assert dialog.result_value == values[0]


def test_merges_omitted_columns_and_continuations_do_not_offer_new_cells(app):
    value, assets, data, _, text_id = materials(continuation_text='原续接说明')
    model = table_cell_model(value['resources'][0], text_id)
    widget = NativeTableCellEditor(model, assets, data)
    try:
        assert widget.grid.columnSpan(0, 0) == 2 and widget.grid.rowSpan(1, 0) == 2
        widget.set_read_only(False)
        assert widget.select_cell(3, 1) and widget._current == (2, 1)
        readonly = [e for e in widget.cell_scroll.findChildren(QPlainTextEdit) if e.isReadOnly()]
        assert [e.toPlainText() for e in readonly] == ['原续接说明']
        widget.segment_editors[0].setPlainText('修改合并起点')
        continuation = next(c for c in widget.cells_value() if (c['row'], c['column']) == (3, 1))
        assert continuation['segments'] == ['原续接说明']
        assert widget.select_cell(1, 2) and widget._current == (1, 1)
        assert not widget.select_cell(4, 1) and not widget.select_cell(4, 4)
        assert widget.select_cell(4, 2) and widget._current == (4, 2)
        assert not widget.grid.item(3, 0).flags() & Qt.ItemFlag.ItemIsSelectable
        assert not widget.grid.item(3, 3).flags() & Qt.ItemFlag.ItemIsSelectable
        # The input model remains a detached copy.
        assert next(c for c in model['cells'] if (c['row'], c['column']) == (2, 1))['segments'] == ['合并起点']
    finally:widget.deleteLater()


def test_image_only_cell_and_between_images_keep_empty_slots_and_occurrences(editors):
    dialog, values = editors();dialog.locked.setChecked(False)
    table = dialog.table_editor;table.select_cell(2, 4)
    assert [edit.toPlainText() for edit in table.segment_editors] == ['', '']
    table.segment_editors[1].setPlainText('\n图后说明')
    table.select_cell(2, 2)
    table.segment_editors[1].setPlainText('图间说明')
    dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()
    item = dialog.result_value['resources'][0]
    projection = source_table_projection(item, _text(dialog.result_value, values[4]))
    source = source_table_projection(values[0]['resources'][0], _text(values[0], values[4]))
    assert projection['image_block_ids'] == source['image_block_ids']
    assert [(o['row'], o['column'], o['asset_id']) for o in projection['occurrences']] == [
        (o['row'], o['column'], o['asset_id']) for o in source['occurrences']]
    assert [t['kind'] for t in projection['cell_tokens'][1, 1]] == ['text', 'image', 'text', 'image', 'text']
    assert projection['cell_tokens'][1, 3][-1]['text'] == '\n图后说明'


def test_legacy_free_text_is_never_parsed_or_migrated(editors):
    dialog, values = editors(legacy=True)
    assert dialog.table_editor is None and values[4] not in dialog._models
    assert dialog.body.toPlainText() == _text(values[0], values[4])['value']
    dialog.locked.setChecked(False);dialog.body.appendPlainText('再补充一行')
    dialog.apply_button.click()
    assert dialog.result_value is not None
    assert 'working_tables' not in dialog.result_value['resources'][0]
    assert _text(dialog.result_value, values[4])['value'].endswith('再补充一行')


def test_notes_require_explicit_plain_layout_and_cannot_hide_staged_table(editors):
    dialog, values = editors(images=False, notes=True)
    dialog.show();QApplication.processEvents()
    assert dialog.exposition.currentData() == 'notes'
    assert dialog.table_mode_button.isVisible()
    dialog.locked.setChecked(False)
    assert dialog.table_editor.segment_editors[0].isReadOnly()
    dialog.table_mode_button.click()
    assert dialog.exposition.currentData() is None
    assert not dialog.table_editor.segment_editors[0].isReadOnly()
    dialog.table_editor.segment_editors[0].setPlainText('新表头')
    dialog.exposition.setCurrentIndex(dialog.exposition.findData('notes'))
    assert dialog.exposition.currentData() is None and '单元格修改' in dialog.status.text()
    dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()
    assert 'exposition' not in dialog.result_value['resources'][0]
    assert values[0]['resources'][0]['exposition']['kind'] == 'notes'


def test_unmodified_notes_and_rejected_notes_switch_do_not_restyle(editors):
    dialog, values = editors(images=False, notes=True)
    dialog.apply_button.click()
    assert dialog.result_value == values[0]
    other, original = editors(images=False, notes=True)
    other.table_mode_button.click();other.reject()
    assert other.result_value is None and original[0]['resources'][0]['exposition']['kind'] == 'notes'


def test_corrupt_image_bytes_are_not_shown_as_originals(app):
    value, assets, data, identity, text_id = materials()
    dialog = ResourceEditor(value, identity, assets=assets, image_data={k: b'wrong' for k in data})
    try:
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(text_id));dialog.table_editor.select_cell(2, 2)
        assert all(preview.source_pixmap.isNull() for preview in dialog.table_editor.image_previews)
        assert all('无法显示' in preview.text() for preview in dialog.table_editor.image_previews)
    finally:dialog.deleteLater()


@pytest.mark.parametrize('size', [(900, 700), (520, 640), (360, 620)])
def test_small_window_controls_and_all_cell_segments_remain_reachable(editors, app, size):
    dialog, _ = editors();dialog.resize(*size);dialog.show();app.processEvents()
    dialog.locked.setChecked(False);table = dialog.table_editor;table.select_cell(2, 2);app.processEvents()
    for control in (dialog.apply_button, dialog.cancel_button, dialog.blocks, dialog.locked, dialog.table_continuation,
            table.previous_button, table.next_button):
        top = control.mapTo(dialog, QPoint(0, 0))
        assert control.isVisible() and dialog.rect().contains(top)
        assert top.x() + control.width() <= dialog.width()
        assert top.y() + control.height() <= dialog.height()
    assert dialog.width() == size[0]
    for edit in table.segment_editors:
        table.cell_scroll.ensureVisible(0, edit.y() + edit.height() // 2, 0, edit.height() // 2)
        app.processEvents()
        top = edit.mapTo(table.cell_scroll.viewport(), QPoint(0, 0))
        assert 0 <= top.x() and top.x() + edit.width() <= table.cell_scroll.viewport().width()
        assert 0 <= top.y() and top.y() + edit.height() <= table.cell_scroll.viewport().height()
    table.grid.setFocus();QTest.keyClick(table.grid, Qt.Key.Key_Right);app.processEvents()
    assert table._current != (2, 2)


class _PanelHarness(QWidget):
    """Run the real parent methods without MainWindow or disk-backed services."""
    composition = LessonCompositionPanel.composition
    commit = LessonCompositionPanel.commit
    _check_question_pages = LessonCompositionPanel._check_question_pages
    edit_selected = LessonCompositionPanel.edit_selected

    def __init__(self, values):
        super().__init__()
        value, assets, data, identity, _ = values
        plan = enable_composition(new_design({}));plan['composition'] = deepcopy(value)
        self.owner = SimpleNamespace(history=DesignHistory(plan), sync_page=lambda: None,
            facade=SimpleNamespace(preparation_image_bytes=lambda asset:data[asset['asset_id']]),
            page=SimpleNamespace(image_assets_widget=SimpleNamespace(assets=lambda:deepcopy(assets))))
        self.selected = identity;self.payload = {'image_assets': assets};self.status = QLabel()


@pytest.mark.parametrize('action', ['apply', 'cancel', 'noop', 'stale'])
def test_parent_modal_atomic_history_cancel_noop_and_cas(app, action):
    values = materials();panel = _PanelHarness(values);before = deepcopy(panel.owner.history.value)
    failures = []
    def interact():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ResourceEditor)
            dialog.blocks.setCurrentIndex(dialog.blocks.findData(values[4]))
            if action != 'noop':
                dialog.locked.setChecked(False)
                dialog.table_editor.select_cell(1, 1)
                dialog.table_editor.segment_editors[0].setPlainText('名称\n观察结构')
            if action == 'stale':
                newer = deepcopy(panel.owner.history.value)
                newer['composition'] = edit_resource(newer['composition'], values[3], title='另一处已修改')
                panel.owner.history.put(newer)
            if action == 'cancel':dialog.cancel_button.click()
            else:dialog.apply_button.click()
            if action != 'cancel':assert dialog.result_value is not None, dialog.status.text()
        except BaseException as exc:
            failures.append(exc)
            if dialog is not None:dialog.reject()
    QTimer.singleShot(0, interact)
    try:
        panel.edit_selected()
        assert not failures, failures
        if action == 'apply':
            assert len(panel.owner.history.back) == 1
            after = deepcopy(panel.owner.history.value)
            assert 'working_tables' in after['composition']['resources'][0]
            assert after['composition']['resources'][0]['original'] == before['composition']['resources'][0]['original']
            assert after['composition']['resources'][0]['original_sha256'] == before['composition']['resources'][0]['original_sha256']
            assert panel.payload['image_assets'] == values[1]
            assert panel.owner.page.image_assets_widget.assets() == values[1]
            assert panel.owner.history.travel() == before
            assert panel.owner.history.travel(True) == after
        elif action == 'stale':
            assert '处理期间变化' in panel.status.text()
            assert len(panel.owner.history.back) == 1
            assert panel.owner.history.value['composition']['resources'][0]['title'] == '另一处已修改'
            assert 'working_tables' not in panel.owner.history.value['composition']['resources'][0]
        else:
            assert panel.owner.history.value == before and not panel.owner.history.back
    finally:panel.deleteLater();app.processEvents()


def test_resource_continuation_checkbox_is_explicit_and_only_shown_for_native_tables(editors, app):
    dialog, _ = editors();dialog.show();app.processEvents()
    assert dialog.table_continuation.isChecked() and dialog.table_continuation.isVisible()
    assert dialog.table_continuation.text() == '内容增加时只为这项素材续页'
    legacy, _ = editors(legacy=True);legacy.show();app.processEvents()
    assert not legacy.table_continuation.isVisible()


@pytest.mark.parametrize('action', ['noop', 'title', 'lock', 'restore', 'cancel'])
@pytest.mark.parametrize('saved_table', [False, True])
def test_no_effective_table_change_never_measures_or_reflows_pages(app, monkeypatch, action, saved_table):
    from integrations.deeptutor_shchem_v1 import desktop_composition_resource_layout as local_layout
    from integrations.deeptutor_shchem_v1.desktop_handout_working_tables import editable_table_cells
    values = materials(images=False);value, assets, data, identity, text_id = values
    if saved_table:
        cells = editable_table_cells(value['resources'][0], text_id);cells[0]['segments'] = ['已保存表头']
        value = edit_resource(value, identity, locked=False)
        value = edit_resource(value, identity, table_cells={text_id: cells}, locked=True)
    def unexpected(*_):pytest.fail('No effective table edit must not measure or reflow')
    monkeypatch.setattr(local_layout, 'refresh_resource_layout_if_needed', unexpected)
    monkeypatch.setattr(local_layout, 'resource_layout_errors', unexpected)
    before = deepcopy(value)
    dialog = ResourceEditor(value, identity, assets=assets, image_data=data)
    try:
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(text_id))
        if action == 'title':dialog.title.setText('只修改标题')
        elif action == 'lock':dialog.locked.setChecked(False)
        elif action in {'restore', 'cancel'}:
            original = dialog.table_editor.segment_editors[0].source_text()
            dialog.locked.setChecked(False)
            dialog.table_editor.segment_editors[0].setPlainText('暂存文字')
            dialog.locked.setChecked(True)
            if action == 'restore':
                dialog.locked.setChecked(False)
                dialog.table_editor.segment_editors[0].setPlainText(original)
                dialog.locked.setChecked(True)
        if action == 'cancel':dialog.reject()
        else:
            dialog.apply_button.click()
            assert dialog.result_value is not None, dialog.status.text()
            assert dialog.result_value['pages'] == before['pages']
            assert dialog.result_value['placements'] == before['placements']
        assert value == before
    finally:dialog.deleteLater();app.processEvents()


def test_effective_table_change_survives_lockback_and_refreshes_once(app, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_resource_layout as local_layout
    from integrations.deeptutor_shchem_v1.desktop_handout_working_tables import editable_table_cells
    value, assets, data, identity, text_id = materials(images=False)
    cells = editable_table_cells(value['resources'][0], text_id);cells[0]['segments'] = ['已保存的表头']
    value = edit_resource(value, identity, locked=False)
    value = edit_resource(value, identity, table_cells={text_id: cells}, locked=True)
    calls = []
    def refresh(candidate, selected, given_assets, given_data):
        calls.append((deepcopy(candidate), selected, given_assets, given_data));return deepcopy(candidate)
    monkeypatch.setattr(local_layout, 'refresh_resource_layout_if_needed', refresh)
    dialog = ResourceEditor(value, identity, assets=assets, image_data=data)
    try:
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(text_id));dialog.locked.setChecked(False)
        dialog.table_editor.segment_editors[0].setPlainText('名称')
        dialog.locked.setChecked(True)
        assert not calls and not dialog.table_cells
        assert 'working_tables' not in dialog.item
        dialog.apply_button.click()
        assert dialog.result_value is not None, dialog.status.text()
        assert len(calls) == 1 and calls[0][1:] == (identity, assets, data)
        assert 'working_tables' not in calls[0][0]['resources'][0]
    finally:dialog.deleteLater();app.processEvents()


def _small_frame_with_unrelated_image():
    value, assets, data, identity, text_id = materials(images=False)
    other = resource('personal_text', '另一项素材及图片', [block('text', 'personal', '保留原位置'),
        block('image', 'personal', 'IMG-' + 'f' * 64)],
        dict(source_path='内存合成内容', source_sha256='unknown', source_revision='unknown'))
    value = add_resource(value, other, dict(page_id=value['pages'][0], x=160, y=15, width=120, height=60))
    value['placements'][identity]['height'] = 30
    return value, assets, data, identity, text_id, other['id']


def test_changed_table_continues_only_selected_resource_with_partial_image_injection(app):
    from integrations.deeptutor_shchem_v1.desktop_composition_resource_layout import resource_layout_errors
    value, assets, data, identity, text_id, other_id = _small_frame_with_unrelated_image();before = deepcopy(value)
    dialog = ResourceEditor(value, identity, assets=assets, image_data=data)
    try:
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(text_id));dialog.locked.setChecked(False)
        dialog.table_editor.segment_editors[0].setPlainText('名称\n观察结构')
        dialog.locked.setChecked(True)
        assert dialog.value['pages'] == before['pages'] and dialog.value['placements'] == before['placements']
        dialog.apply_button.click();result = dialog.result_value
        assert result is not None, dialog.status.text()
        assert result['placements'][identity] != before['placements'][identity]
        assert result['placements'][other_id] == before['placements'][other_id]
        assert result['resources'][1] == before['resources'][1]
        assert before['pages'][0] in result['pages']  # The original page is shared.
        assert not resource_layout_errors(result, identity, assets, data)
        assert value == before
    finally:dialog.deleteLater();app.processEvents()


def test_unchecked_overflow_keeps_dialog_text_and_geometry_then_can_continue(app):
    value, assets, data, identity, text_id, _ = _small_frame_with_unrelated_image();before = deepcopy(value)
    dialog = ResourceEditor(value, identity, assets=assets, image_data=data)
    try:
        dialog.blocks.setCurrentIndex(dialog.blocks.findData(text_id));dialog.locked.setChecked(False)
        dialog.table_editor.segment_editors[0].setPlainText('名称\n保留全部新增内容')
        dialog.table_continuation.setChecked(False);dialog.apply_button.click()
        assert dialog.result_value is None
        assert '现有页面放不下' in dialog.status.text() and '请勾选' in dialog.status.text()
        assert dialog.table_editor.segment_editors[0].source_text() == '名称\n保留全部新增内容'
        assert dialog.value['pages'] == before['pages'] and dialog.value['placements'] == before['placements']
        assert value == before
        dialog.table_continuation.setChecked(True);dialog.apply_button.click()
        assert dialog.result_value is not None, dialog.status.text()
    finally:dialog.deleteLater();app.processEvents()


@pytest.mark.parametrize('checked', [True, False])
def test_short_fitting_table_edit_keeps_existing_pages_and_placement(editors, checked):
    dialog, values = editors(images=False);before = deepcopy(values[0])
    dialog.locked.setChecked(False);dialog.table_editor.segment_editors[0].setPlainText('名称对照')
    dialog.table_continuation.setChecked(checked);dialog.apply_button.click()
    assert dialog.result_value is not None, dialog.status.text()
    assert dialog.result_value['pages'] == before['pages']
    assert dialog.result_value['placements'] == before['placements']


def test_layout_failure_preserves_staged_text_and_never_assigns_partial_candidate(editors, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_resource_layout as local_layout
    dialog, values = editors(images=False);dialog.locked.setChecked(False)
    dialog.table_editor.segment_editors[0].setPlainText('待续页的完整修改')
    before = deepcopy(dialog.value)
    def failed(*_):raise ValueError('合并单元格当前无法完整续页，请调整文字后重试。')
    monkeypatch.setattr(local_layout, 'refresh_resource_layout_if_needed', failed)
    dialog.apply_button.click()
    assert dialog.result_value is None and '无法完整续页' in dialog.status.text()
    assert dialog.value == before and values[0]['resources'][0]['locked']
    assert dialog.table_editor.segment_editors[0].source_text() == '待续页的完整修改'
