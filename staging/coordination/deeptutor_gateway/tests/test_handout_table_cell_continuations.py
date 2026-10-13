"""Complete source cells continue in measured chunks, never copied substrings."""
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_handout_knowledge_tables import table_chunks
from test_handout_knowledge_tables import row, fake_lines


def measured_lines(text, width, *, block_id, font_px, bold=False):
    return [dict(kind='text', text=part, width=width, height=25 if text != '标题' else 10,
        block_id=block_id, font_px=font_px, bold=bold) for part in text.split('|')]


def check_geometry(panels, width, height):
    for panel in panels:
        assert panel['height'] <= height + .001
        for part in panel['_parts']:
            assert part['offset_x'] >= 0 and part['offset_y'] >= 0
            assert part['offset_x'] + part['width'] <= width + .001
            assert part['offset_y'] + part['height'] <= panel['height'] + .001


def test_complete_long_cells_keep_all_chunks_grid_spans_order_and_body_font():
    rows = [row('标题', '标题', '标题'), row((2, '甲|乙|丙|丁|戊'), '一|二|三')]
    sealed = deepcopy(rows)
    panels = table_chunks(rows, 'native-table', 300, measured_lines, 32,
        max_height=70, first_height=20, column_weights=(1, 2, 2))
    assert rows == sealed
    assert len(panels) == 3 and panels[0]['_table_restarted_page']
    assert [p['_table_source_rows'] for p in panels] == [[1, 2]] * 3
    assert [p['_table_row_fragments'][1] for p in panels] == [(2, 1, 3), (2, 2, 3), (2, 3, 3)]
    check_geometry(panels, 300, 70)
    for ci, span, expected in [(1, 2, ['甲', '乙', '丙', '丁', '戊']), (3, 1, ['一', '二', '三'])]:
        parts = [part for panel in panels for part in panel['_parts']
            if part['kind'] == 'text' and part['_table_cell'] == (2, ci, span)]
        assert [part['text'] for part in parts] == expected
        assert [part['_table_source_chunk'] for part in parts] == list(range(1, len(expected) + 1))
        assert all(part['font_px'] == 32 for part in parts)
        boxes = [part for panel in panels for part in panel['_parts']
            if part['kind'] == 'diagram_box' and part['_table_cell'] == (2, ci, span)]
        assert len(boxes) == 3
        assert all(part['width'] == pytest.approx(180 if ci == 1 else 120) for part in boxes)
    assert [part['text'] for part in panels[-1]['_parts'] if part.get('text') and part['_table_cell'][0] != 1] == ['戊']


def test_rich_line_with_whole_equation_and_picture_is_never_split(monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_rich_render as rich
    eq = dict(kind='diagram', width=20, height=8, offset_x=20, offset_y=0,
        _parts=[dict(kind='text', text='催化剂', width=20, height=4, offset_x=0, offset_y=0),
            dict(kind='image', asset_id='source-arrow', width=20, height=4, offset_x=0, offset_y=4)])
    line = dict(kind='rich_line', width=100, height=45, _parts=[eq], seal='whole-EQ-line')
    tail = dict(kind='rich_line', width=100, height=25,
        _parts=[dict(kind='text', text='完整后文', width=70, height=8, offset_x=0, offset_y=0)])
    monkeypatch.setattr(rich, 'rich_lines', lambda *a, **kw: [line, tail])
    rows = [row('标题'), row('原图和条件原文')]
    panels = table_chunks(rows, 'b', 150, measured_lines, 32, max_height=70,
        cell_tokens={(1, 0): [dict(kind='eq')]})
    assert len(panels) == 2
    emitted = [p for panel in panels for p in panel['_parts'] if p['kind'] == 'rich_line']
    assert len(emitted) == 2 and emitted[0]['_parts'] is line['_parts']
    assert emitted[0]['seal'] == 'whole-EQ-line' and emitted[1]['_parts'] is tail['_parts']
    check_geometry(panels, 150, 70)


def test_indivisible_measured_primitive_is_refused_before_output():
    def tall(text, width, **kw):
        return [dict(kind='rich_line', width=width, height=10 if text == '标题' else 60,
            _parts=[dict(kind='image', asset_id='whole-source-image', width=10, height=60)])]
    with pytest.raises(ValueError, match='单个文字行、图片或公式'):
        table_chunks([row('标题'), row('原图')], 'b', 200, tall, 32, max_height=70)


def test_empty_vertical_continuation_can_cross_source_row_boundary_without_copying_restart():
    rows = [row('标题', '标题'), row((1, '实例', 'restart'), '甲|乙'),
        row((1, '', 'continue'), '丙|丁')]
    panels = table_chunks(rows, 'b', 200, measured_lines, 32, max_height=70)
    assert [p['_table_source_rows'] for p in panels] == [[1, 2], [1, 3]]
    check_geometry(panels, 200, 70)
    labels = [p['text'] for panel in panels for p in panel['_parts'] if p.get('text') == '实例']
    assert labels == ['实例']
    box = next(p for p in panels[1]['_parts'] if p['kind'] == 'diagram_box' and p['_table_cell'] == (3, 1, 1))
    assert box['_table_merge_origin'] == (2, 1, 1) and box['_table_vertical_merge'] == 'continue'
    assert [p['text'] for panel in panels for p in panel['_parts'] if p['kind'] == 'text' and p['_table_cell'][1] == 2 and p['_table_cell'][0] != 1] == ['甲', '乙', '丙', '丁']


def test_nonempty_merge_continuation_keeps_original_guard():
    rows = [row('标题', '标题'), row((1, '实例', 'restart'), '甲|乙'),
        row((1, '不能猜测的续接文字', 'continue'), '丙|丁')]
    with pytest.raises(ValueError, match='纵向合并续接含内容'):
        table_chunks(rows, 'b', 200, measured_lines, 32, max_height=70)


def test_merged_source_row_cannot_split_inside_the_row():
    rows = [row('标题', '标题'), row((1, '实例', 'restart'), '甲|乙|丙'),
        row((1, '', 'continue'), '丁')]
    with pytest.raises(ValueError, match='包含纵向合并'):
        table_chunks(rows, 'b', 200, measured_lines, 32, max_height=70)


def test_projection_object_cannot_hide_inside_an_empty_merge_continuation(monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_rich_render as rich
    monkeypatch.setattr(rich, 'rich_lines', lambda *a, **kw:
        [dict(kind='rich_line', width=80, height=10, _parts=[])])
    rows = [row('标题', '标题'), row((1, '实例', 'restart'), '甲|乙'),
        row((1, '', 'continue'), '丙|丁')]
    with pytest.raises(ValueError, match='续接包含显示对象'):
        table_chunks(rows, 'b', 200, measured_lines, 32, max_height=70,
            cell_tokens={(2, 0): [dict(kind='image')]})


def test_first_source_row_merge_and_long_single_row_are_not_invented_as_headers():
    for rows in ([row((1, '甲|乙|丙', 'restart')), row((1, '', 'continue'))],
            [row('甲|乙|丙')]):
        with pytest.raises(ValueError):
            table_chunks(rows, 'b', 200, measured_lines, 32, max_height=70)


def test_ordinary_fitting_table_has_no_new_fragment_metadata_or_pagination():
    panels = table_chunks([row('列甲', '列乙'), row('甲', '乙'), row('丙', '丁')],
        'b', 200, fake_lines, 32, max_height=50)
    assert [p['_table_source_rows'] for p in panels] == [[1, 2, 3]]
    assert not any('_table_row_fragments' in p for p in panels)
