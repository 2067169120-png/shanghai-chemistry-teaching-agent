"""Selected native-table reflow never rearranges the rest of the lesson."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

import pytest

from integrations.deeptutor_shchem_v1.desktop_composition_resource_layout import (
    arrange_resource_layout, refresh_resource_layout_if_needed, resource_layout_errors)
from integrations.deeptutor_shchem_v1.desktop_composition_render import arrange_composition, layout_composition
from integrations.deeptutor_shchem_v1.desktop_composition_rich_render import flatten_elements
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FORMATS, add_resource, block, edit_resource, resource, resource_frames,
    set_click_reveal, set_student_arrangement)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import verify_image_bytes
from integrations.deeptutor_shchem_v1.desktop_handout_table_projection import source_table_projection
from test_handout_working_tables import (
    REAL_ROOT, row, synthetic_item, composition_for, changed, assert_manifests_unchanged)
from test_handout_working_tables_outputs import app


def edit_in_frames(value, item, table_id, text, *, cell_index=0):
    return edit_resource(value, item['id'], table_cells={table_id:
        changed(item, table_id, cell_index=cell_index, text=text)})


def assert_only_selected_placement_changes(before, after, identity):
    assert after['resources'] == before['resources']
    assert {k: v for k, v in after.items() if k not in {'pages', 'placements'}} == {
        k: v for k, v in before.items() if k not in {'pages', 'placements'}}
    assert {k: v for k, v in after['placements'].items() if k != identity} == {
        k: v for k, v in before['placements'].items() if k != identity}
    old_pages = {f['page_id'] for f in resource_frames(before['placements'][identity])}
    shared = {f['page_id'] for rid, p in before['placements'].items() if rid != identity for f in resource_frames(p)}
    removed = old_pages - shared
    assert [p for p in after['pages'] if p in before['pages']] == [p for p in before['pages'] if p not in removed]
    new_pages = [p for p in after['pages'] if p not in before['pages']]
    insertion = before['pages'].index(next(p for p in before['pages'] if p in old_pages))
    assert after['pages'][insertion:insertion + len(new_pages)] == new_pages


def test_fitting_multiline_edit_does_not_change_page_ids_frames_or_resource(app):
    item, ids, assets, data = synthetic_item([row('甲', '乙'), row('短内容', '原文')])
    value = composition_for(item); value['placements'][item['id']]['height'] = 156
    value = edit_in_frames(value, item, ids[0], '短内容\n观察结构', cell_index=2)
    before = deepcopy(value)
    assert not resource_layout_errors(value, item['id'], assets, data)
    out = refresh_resource_layout_if_needed(value, item['id'], assets, data)
    assert out == before and value == before and out is not value
    out['resources'][0]['title'] = '脱离结果'
    assert value == before


def test_empty_image_data_is_sufficient_for_pure_text_table_reflow(app):
    item, ids, assets, data = synthetic_item([row('甲', '乙'), row('原文', '完整右列')])
    assert assets == [] and data == {}
    value = arrange_composition(composition_for(item), assets, data)
    text = '\n'.join(f'完整内容{i:03d}' for i in range(60))
    edited = edit_in_frames(value, item, ids[0], text, cell_index=2); before = deepcopy(edited)
    assert resource_layout_errors(edited, item['id'], [], {})
    result = refresh_resource_layout_if_needed(edited, item['id'], [], {})
    assert not resource_layout_errors(result, item['id'], [], {})
    assert len(resource_frames(result['placements'][item['id']])) > 1
    assert_only_selected_placement_changes(before, result, item['id'])
    assert edited == before
    for student, reveals in ((False, False), (True, False), (True, True)):
        layout = layout_composition(result, [], {}, student=student, include_reveals=reveals)
        leaves = [p for page in layout['pages'].values() for p in flatten_elements(page)]
        words = ''.join(p.get('text', '') for p in leaves)
        assert all(f'完整内容{i:03d}' in words for i in range(60))
        assert all(p['font_px'] == 32 for p in leaves if p['kind'] == 'text' and '_table_cell' in p)
    assert refresh_resource_layout_if_needed(result, item['id'], [], {}) == result


@pytest.mark.parametrize('format', list(FORMATS))
def test_shared_pages_blanks_other_unfinished_resources_and_policies_are_preserved(app, format):
    item, ids, assets, data = synthetic_item([row('甲', '乙'), row('短内容', '原文')])
    value = composition_for(item); value['format'] = format
    width, height = FORMATS[format]
    other = resource('personal_text', '另一个未完成素材', [block('text', 'personal', '完整条件'*100)],
        dict(source_path='作者输入', source_sha256='unknown', source_revision='unknown'))
    pages = ['CP-' + uuid4().hex for _ in range(5)]
    value['pages'] = pages
    value['placements'][item['id']] = dict(page_id=pages[1], x=12, y=12, width=width - 24, height=12,
        continuations=[dict(page_id=pages[3], x=12, y=12, width=width - 24, height=12)])
    value = add_resource(value, other, dict(page_id=pages[1], x=12, y=40, width=width - 24, height=12))
    value = set_student_arrangement(value, 1)
    if format == '16:9':
        value = set_click_reveal(value, item['id'], True)
    value = edit_in_frames(value, item, ids[0], '短内容\n观察结构', cell_index=2)
    before = deepcopy(value)
    assert resource_layout_errors(value, item['id'], assets, data)
    result = refresh_resource_layout_if_needed(value, item['id'], assets, data)
    assert not resource_layout_errors(result, item['id'], assets, data)
    assert_only_selected_placement_changes(before, result, item['id'])
    assert pages[1] in result['pages'] and pages[3] not in result['pages']
    assert value == before
    # The other unfinished resource is not a reason to edit or reject this one.
    assert layout_composition(result, assets, data, student=False)['errors']


@pytest.mark.parametrize('hidden', ['teacher_only', 'click'])
def test_hidden_student_scope_does_not_block_complete_local_reflow(app, hidden):
    item, ids, assets, data = synthetic_item([row('甲', '乙'), row('内容', '右列')], teacher=hidden == 'teacher_only')
    value = arrange_composition(composition_for(item), assets, data)
    if hidden == 'click':
        value = set_click_reveal(value, item['id'], True)
    value = edit_in_frames(value, item, ids[0], '\n'.join('观察结构' for _ in range(20)), cell_index=2)
    result = refresh_resource_layout_if_needed(value, item['id'], assets, data)
    assert not resource_layout_errors(result, item['id'], assets, data)
    assert result['resources'] == value['resources']
    static = layout_composition(result, assets, data, student=True)
    assert not static['pages']
    classroom = layout_composition(result, assets, data, student=True, include_reveals=True)
    assert bool(classroom['pages']) == (hidden == 'click')
    assert result.get('click_reveals') == value.get('click_reveals')


def test_unsafe_first_row_vertical_merge_fails_without_any_input_mutation(app):
    item, ids, _, _ = synthetic_item([row((1, '首行起点', 'restart'), '右列'),
        row((1, '', 'continue'), '下一行')])
    value = composition_for(item)
    value = edit_in_frames(value, item, ids[0], '\n'.join('完整行' for _ in range(100)))
    before = deepcopy(value)
    assert any('首行纵向合并' in e for e in resource_layout_errors(value, item['id'], [], {}))
    with pytest.raises(ValueError, match='首行纵向合并'):
        refresh_resource_layout_if_needed(value, item['id'], [], {})
    assert value == before


def test_reflow_rejects_missing_original_image_bytes_without_mutation(app):
    item, ids, assets, _ = synthetic_item(); value = composition_for(item)
    value = edit_in_frames(value, item, ids[0], '标题\n观察结构'); before = deepcopy(value)
    with pytest.raises(ValueError): refresh_resource_layout_if_needed(value, item['id'], assets, {})
    assert value == before


def test_image_entry_preserves_its_old_eligibility_errors(app):
    from integrations.deeptutor_shchem_v1.desktop_composition_image_display import arrange_image_resource
    item, _, assets, data = synthetic_item([row('甲', '乙')]); value = composition_for(item)
    with pytest.raises(ValueError, match='含有原图'): arrange_image_resource(value, item['id'], assets, data)
    with pytest.raises(ValueError, match='原图素材'): arrange_image_resource(value, 'missing', assets, data)
    assert arrange_resource_layout(value, item['id'], assets, data)['resources'] == value['resources']


def test_160_page_guard_is_atomic_for_local_reflow(app):
    item, ids, _, _ = synthetic_item([row('甲', '乙'), row('内容', '右列')])
    value = composition_for(item); selected_page = value['pages'][0]
    value['pages'] += ['CP-' + uuid4().hex for _ in range(159)]
    value['placements'][item['id']]['height'] = 12
    value = edit_in_frames(value, item, ids[0], '\n'.join('完整行' for _ in range(80)), cell_index=2)
    before = deepcopy(value)
    with pytest.raises(ValueError, match='160页'):
        refresh_resource_layout_if_needed(value, item['id'], [], {})
    assert value == before and selected_page in value['pages']


def test_actual072_multiline_edit_reflows_only_13_frame_resource_inside_75_resource_course(app, tmp_path):
    path = REAL_ROOT / '2026-10-08-native-table-cell-workbench/native-layout-probe-v1/preparation-payload.json'
    if not path.is_file():
        pytest.skip('Exact root multiline-failure fixture unavailable')
    manifests = {path: (sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)}
    assert manifests[path][0] == '957b522fdc020bf6a2778a60b1eb5d8d4e04148eb75a95c77227601927a8244c'
    payload = json.loads(path.read_text('utf-8-sig')); value = payload['lesson_design']['composition']
    assert len(value['resources']) == 75
    identity = 'CR-e21ecb21798f442bbdf1fb706b333c07'
    item = next(r for r in value['resources'] if r['id'] == identity)
    cells = item['working_tables']['tables']['CB-ed56fb94e9ff45a495d36290aabfb36d']['cells']
    assert cells[0]['segments'] == ['羟甲基苯酚\n观察结构']
    assert len(resource_frames(value['placements'][identity])) == 13
    source = Path(item['original']['evidence']['source_path'])
    manifests[source] = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
    assert manifests[source][0] == '01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33'
    assets = item['original']['evidence']['image_assets']
    store = REAL_ROOT / '2026-10-08-polymer-paint-preview-workbench/native-complete-source-insertion-v5/isolated-task/profile/tasks/preparation-v1/images'
    data = {}
    for asset in assets:
        image = store / (asset['sha256'] + '.image')
        manifests[image] = (sha256(image.read_bytes()).hexdigest(), image.stat().st_size, image.stat().st_mtime_ns)
        data[asset['asset_id']] = verify_image_bytes(asset, image.read_bytes())
    baseline = deepcopy(value)
    baseline_errors = resource_layout_errors(value, identity, assets, data)
    # The current native-table pagination already fits this historical edit.
    # Refreshing it must not revive the old overflow or rearrange the course.
    assert baseline_errors == []
    assert refresh_resource_layout_if_needed(value, identity, assets, data) == baseline
    assert value == baseline
    source_view = deepcopy(baseline); source_view['resources'] = [item]
    source_view['placements'] = {identity: baseline['placements'][identity]}
    if 'click_reveals' in source_view:
        source_view['click_reveals'] = [r for r in source_view['click_reveals'] if r == identity]
    source_layout = layout_composition(source_view, assets, data, student=False)
    assert not source_layout['errors']
    source_pictures = {p['image_block_id']: p for page in source_layout['pages'].values()
        for p in flatten_elements(page) if p['kind'] == 'image'}
    # Exercise a new working-copy edit after the first existing picture slot.
    # Preserve the source heading, grid and all picture occurrences exactly.
    work_lines = [f'工作副本记录{i:02d}：观察结构' for i in range(20)]
    value = edit_resource(value, identity, table_cells={
        'CB-ed56fb94e9ff45a495d36290aabfb36d': changed(item,
            'CB-ed56fb94e9ff45a495d36290aabfb36d', cell_index=4, segment=1,
            text='\n'+'\n'.join(work_lines))})
    edited = next(r for r in value['resources'] if r['id'] == identity)
    assert edited['original'] == item['original']
    assert edited['original_sha256'] == item['original_sha256']
    assert [b for b in edited['blocks'] if b['kind'] == 'image'] == [
        b for b in item['blocks'] if b['kind'] == 'image']
    assert [{k:v for k,v in b.items() if k!='value'} for b in edited['blocks']] == [
        {k:v for k,v in b.items() if k!='value'} for b in item['blocks']]
    assert [r for r in value['resources'] if r['id'] != identity] == [
        r for r in baseline['resources'] if r['id'] != identity]
    assert value['pages'] == baseline['pages'] and value['placements'] == baseline['placements']
    assert edited['working_tables']['tables']['CB-ed56fb94e9ff45a495d36290aabfb36d']['cells'][0] == cells[0]
    before = deepcopy(value)
    errors = resource_layout_errors(value, identity, assets, data)
    assert any('完整内容尚未放完' in e for e in errors)
    result = refresh_resource_layout_if_needed(value, identity, assets, data)
    assert not resource_layout_errors(result, identity, assets, data)
    assert_only_selected_placement_changes(before, result, identity)
    assert value == before
    assert refresh_resource_layout_if_needed(result, identity, assets, data) == result
    # Check actual native occurrence order at full classroom font size in each scope.
    isolated = deepcopy(result); isolated['resources'] = [edited]
    isolated['placements'] = {identity: result['placements'][identity]}
    if 'click_reveals' in isolated:
        isolated['click_reveals'] = [r for r in isolated['click_reveals'] if r == identity]
    observed = {}
    def plain(text):
        # Match display text across line wraps and explicit script notation;
        # all chemical letters, numbers, signs and conditions remain checked.
        text = re.sub(r'【待查看原文：[^】]*】', '', text)
        text = re.sub(r'[_^]\{([^{}]*)\}', r'\1', text)
        return ''.join(unicodedata.normalize('NFKC', text).split()).replace('−','-')
    for name, student, reveals in (('teacher', False, False), ('static', True, False), ('classroom', True, True)):
        layout = layout_composition(isolated, assets, data, student=student, include_reveals=reveals)
        assert not layout['errors']
        leaves = [p for page in layout['pages'].values() for p in flatten_elements(page)]
        table_text = [p for p in leaves if p['kind'] == 'text' and p.get('block_id') == 'CB-ed56fb94e9ff45a495d36290aabfb36d']
        assert '观察结构' in ''.join(p['text'] for p in table_text)
        words = ''.join(p['text'] for p in table_text if p.get('_table_cell') == (2,1,1))
        assert all(line in words for line in work_lines), {
            'missing_lines': [line for line in work_lines if line not in words],
            'observed_cell_text': words}
        assert all(p['font_px'] == 32 for p in table_text)
        # Check complete source/working scientific text in every block/cell,
        # rather than relying only on the edited cell's visible marker.
        for row in edited['blocks']:
            if row['kind'] != 'text': continue
            projected = source_table_projection(edited, row)
            if projected is None:
                words = ''.join(p.get('text','') for p in leaves if p.get('block_id') == row['id'])
                assert plain(row['value']) == plain(words)
            else:
                for ri, source_row in enumerate(projected['rows'],1):
                    column = source_row['grid_before']+1
                    for cell in source_row['cells']:
                        key = (ri,column,cell['column_span'])
                        words = ''.join(p.get('text','') for p in leaves
                            if p.get('block_id') == row['id'] and p.get('_table_cell') == key)
                        if plain(cell['text']): assert plain(cell['text']) in plain(words)
                        column += cell['column_span']
        original_images = [b['id'] for b in edited['blocks'] if b['kind'] == 'image']
        assert [p['image_block_id'] for p in leaves if p['kind'] == 'image'] == original_images
        assert all(p['_image'] == source_pictures[p['image_block_id']]['_image']
            and p['asset_id'] == source_pictures[p['image_block_id']]['asset_id']
            and p.get('crop') == source_pictures[p['image_block_id']].get('crop')
            and p.get('transform') == source_pictures[p['image_block_id']].get('transform')
            for p in leaves if p['kind'] == 'image')
        observed[name] = {'fragments': len(layout['fragments']), 'image_occurrences': len(original_images), 'errors': layout['errors']}
    (tmp_path / 'verification.json').write_text(json.dumps({'baseline_errors': baseline_errors,
        'working_edit_lines': work_lines, 'before_errors': errors, 'after_scopes': observed,
        'before_frames': 13, 'after_frames': len(resource_frames(result['placements'][identity])),
        'other_resources_unchanged': 74, 'source_manifests': [
            {'path': str(p), 'sha256': m[0], 'bytes': m[1], 'mtime_ns': m[2]} for p, m in manifests.items()]},
        ensure_ascii=False, indent=2), encoding='utf-8')
    (tmp_path / 'reflowed-composition.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    assert_manifests_unchanged(manifests)
