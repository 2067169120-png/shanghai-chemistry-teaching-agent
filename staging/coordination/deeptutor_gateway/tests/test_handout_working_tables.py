"""Cell edits bind source coordinates and each picture occurrence, never assets."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from integrations.deeptutor_shchem_v1.desktop_handout_knowledge import (
    SOURCE_SCHEMA, SECTION_SCHEMA, SOURCE_ROLES_SCHEMA)
from integrations.deeptutor_shchem_v1.desktop_handout_table_projection import source_table_projection
from integrations.deeptutor_shchem_v1.desktop_handout_working_tables import (
    SCHEMA, editable_table_cells, table_cell_model)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, block, digest, edit_resource, new_composition, resource, validate_resource)
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import _readable_table

MARKER = '【待查看原文：图片或图形】'
OLD_MARKER = '【待查看原文：嵌入对象或旧公式】'


def row(*values, before=0, after=0):
    return {'grid_before': before, 'grid_after': after, 'cells': [
        {'column_span': v[0], 'vertical_merge': v[2] if len(v) > 2 else 'none', 'text': v[1]}
        if isinstance(v, tuple) else {'column_span': 1, 'vertical_merge': 'none', 'text': v}
        for v in values]}


def synthetic_item(rows=None, *, second=False, teacher=False, legacy=False):
    """In-memory synthetic evidence only; never MainWindow, recovery or profile."""
    from integrations.deeptutor_shchem_v1.desktop_composition_inline import MARKER as pattern
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info
    rows = rows if rows is not None else [
        row((2, '标题'), '观察', ''),
        row((1, '纵向起点', 'restart'), '前' + MARKER + OLD_MARKER + '后', 'CH_{3}', '  \n '),
        row((1, '', 'continue'), (2, '合并格'), MARKER)]
    buffer = BytesIO(); Image.new('RGB', (48, 24), (30, 90, 140)).save(buffer, 'PNG')
    raw = buffer.getvalue(); image_sha = sha256(raw).hexdigest()
    asset = {'asset_id': 'IMG-' + image_sha, 'sha256': image_sha, 'caption': '合成原图',
        'source': '合成Word来源', 'purpose': '仅用于保序验证', **image_info(raw)}
    source = dict(schema=SOURCE_SCHEMA, batch_id='synthetic', source_id='source', source_name='合成来源.docx',
        source_sha256='a' * 64, source_role='handout', source_path='合成来源.docx', source_revision='b' * 64)
    role = 'teacher_note' if teacher else 'knowledge'
    native, blocks, table_ids = [], [], []
    for index, table_rows in enumerate([rows] + ([deepcopy(rows)] if second else []), 94):
        text = _readable_table(table_rows)
        refs = [dict(block_index=index, position=ordinal, source_asset_id=f'image-{ordinal}',
            source_sha256=source['source_sha256'], original_sha256=image_sha,
            original_mime_type='image/png', asset_id=asset['asset_id'], preview_sha256=image_sha,
            derived_preview=False, renderer_revision=None, source_role='handout', source_branch='direct',
            status='included', container_xml_sha256=sha256(f'{index}/{ordinal}'.encode()).hexdigest(),
            width_mm=12.0, height_mm=6.0) for ordinal, _ in enumerate(pattern.findall(text), 1)]
        native.append(dict(index=index, text=text, images=refs, table_rows=deepcopy(table_rows)))
        text_block = block('text', role, text, index); table_ids.append(text_block['id'])
        blocks.extend([text_block, *[block('image', role, r['asset_id'], index) for r in refs]])
    tail_index = native[-1]['index'] + 1
    native.append(dict(index=tail_index, text='完整表后原文。', images=[]))
    blocks.append(block('text', role, native[-1]['text'], tail_index))
    assets = [asset] if any(b['images'] for b in native) else []
    scope = dict(teacher_only=teacher, document_role='handout',
        images=[dict(asset_id=a['asset_id'], roles=[role]) for a in assets])
    if not legacy:
        scope['schema'] = SOURCE_ROLES_SCHEMA
    snapshot = dict(schema=SECTION_SCHEMA, outline_revision='c' * 64,
        section=dict(id='word-heading-b94', level=3, title='完整比较', start=94, end=tail_index), blocks=native)
    item = resource('handout_knowledge', '合成来源.docx · 完整比较', blocks,
        dict(source_path=source['source_path'], source_sha256=source['source_sha256'], source_revision=source['source_revision'],
            handout_source=source, section_snapshot=snapshot, image_assets=assets, source_roles=scope))
    return item, table_ids, assets, ({asset['asset_id']: raw} if assets else {})


def composition_for(item, *, unlocked=True):
    value = new_composition('16:9')
    value = add_resource(value, item, dict(page_id=value['pages'][0], x=12, y=12, width=296, height=156))
    return edit_resource(value, item['id'], locked=False) if unlocked else value


def changed(item, block_id, *, cell_index=0, segment=0, text='已改标题'):
    cells = editable_table_cells(item, block_id)
    cells[cell_index]['segments'][segment] = text
    return cells


def edited(item, block_id, cells=None):
    return edit_resource(composition_for(item), item['id'],
        table_cells={block_id: cells if cells is not None else changed(item, block_id)})['resources'][0]


def text_block(item, identity):
    return next(b for b in item['blocks'] if b['id'] == identity)


def test_open_cancel_noop_and_empty_patch_do_not_create_sidecar_or_migrate():
    item, ids, _, _ = synthetic_item(); before = deepcopy(item)
    model = table_cell_model(item, ids[0])
    assert len(model['cells']) == 10 and model['editing_available']
    assert [(c['row'], c['column']) for c in model['cells']] == [
        (1, 1), (1, 3), (1, 4), (2, 1), (2, 2), (2, 3), (2, 4), (3, 1), (3, 2), (3, 4)]
    assert [c['editable'] for c in model['cells']].count(False) == 1
    assert model['cells'][4]['segments'] == ['前', '', '后']
    assert model['cells'][-1]['segments'] == ['', '']
    model['cells'][0]['segments'][0] = '取消的输入'
    assert item == before and 'working_tables' not in item
    value = composition_for(item)
    assert edit_resource(value, item['id'], table_cells={}) == value
    assert edit_resource(value, item['id'], table_cells={ids[0]: editable_table_cells(item, ids[0])}) == value
    assert validate_resource(json.loads(json.dumps(item))) == before


@pytest.mark.parametrize('teacher,legacy', [(False, False), (True, False), (True, True)])
def test_atomic_edit_keeps_original_roles_order_images_and_recomputes_offsets(teacher, legacy):
    item, ids, assets, data = synthetic_item(teacher=teacher, legacy=legacy); before = deepcopy(item)
    original_projection = source_table_projection(item, text_block(item, ids[0]))
    cells = changed(item, ids[0], cell_index=4, text='  图前\nCH_{3} / Fe^{2+}  ')
    cells[4]['segments'][1] = '\n中间  '
    cells[4]['segments'][2] = ' 图后\n'
    out = edited(item, ids[0], cells)
    projection = source_table_projection(out, text_block(out, ids[0]))
    assert projection['image_block_ids'] == original_projection['image_block_ids']
    assert len(set(projection['image_block_ids'])) == 3
    assert len({o['asset_id'] for o in projection['occurrences']}) == 1
    assert out['original'] == before['original'] and out['original_sha256'] == before['original_sha256']
    assert item == before
    assert [b for b in out['blocks'] if b['kind'] == 'image'] == [b for b in item['blocks'] if b['kind'] == 'image']
    assert [{k: v for k, v in b.items() if k != 'value'} for b in out['blocks']] == [
        {k: v for k, v in b.items() if k != 'value'} for b in item['blocks']]
    assert out['blocks'][-1] == item['blocks'][-1]
    assert text_block(out, ids[0])['value'] == _readable_table(projection['rows'])
    for old, new in zip(original_projection['occurrences'], projection['occurrences']):
        assert {k: v for k, v in new.items() if k not in {'text_start', 'text_end'}} == {
            k: v for k, v in old.items() if k not in {'text_start', 'text_end'}}
        c = next(c for c in table_cell_model(out, ids[0])['cells'] if (c['row'], c['column']) == (new['row'], new['column']))
        assert c['slots']
        text = projection['rows'][new['row'] - 1]['cells'][1 if new['row'] == 2 else 2]['text']
        assert text[new['text_start']:new['text_end']] == new['placeholder']
        assert sha256(data[new['asset_id']]).hexdigest() == new['preview_sha256']
    assert projection['occurrences'][0]['text_start'] == len(cells[4]['segments'][0])
    assert assets == out['original']['evidence']['image_assets']


@pytest.mark.parametrize('content', ['', '  ', '\n\n', '  前\n\n后  ', 'CH_{3}－COOH / Fe^{2+}'])
def test_empty_whitespace_multiline_and_explicit_subsup_stay_exact(content):
    item, ids, _, _ = synthetic_item()
    cells = changed(item, ids[0], cell_index=4, text=content)
    out = edited(item, ids[0], cells)
    assert editable_table_cells(out, ids[0]) == cells
    projection = source_table_projection(out, text_block(out, ids[0]))
    assert projection['rows'][1]['cells'][1]['text'] == content + MARKER + OLD_MARKER + '后'


def test_restore_removes_only_reverted_table_and_last_sidecar():
    item, ids, _, _ = synthetic_item(second=True)
    value = composition_for(item)
    out = edit_resource(value, item['id'], table_cells={i: changed(item, i) for i in ids})
    first = edit_resource(out, item['id'], table_cells={ids[0]: editable_table_cells(item, ids[0])})
    assert set(first['resources'][0]['working_tables']['tables']) == {ids[1]}
    final = edit_resource(first, item['id'], table_cells={ids[1]: editable_table_cells(item, ids[1])})
    assert final == value


def test_lock_same_request_cas_collision_and_failed_transaction_leave_input_intact():
    item, ids, _, _ = synthetic_item(second=True); cells = changed(item, ids[0])
    locked = composition_for(item, unlocked=False); before = deepcopy(locked)
    with pytest.raises(ValueError, match='锁定'):
        edit_resource(locked, item['id'], locked=False, table_cells={ids[0]: cells})
    assert locked == before
    value = composition_for(item); before = deepcopy(value)
    with pytest.raises(ValueError, match='编排已修改'):
        edit_resource(value, item['id'], table_cells={ids[0]: cells}, expected='d' * 64)
    with pytest.raises(ValueError, match='同时提交'):
        edit_resource(value, item['id'], values={ids[0]: '另一个覆盖'}, table_cells={ids[0]: cells})
    invalid = changed(item, ids[1]); invalid.pop()
    with pytest.raises(ValueError, match='全部原生'):
        edit_resource(value, item['id'], title='暂存标题', values={item['blocks'][-1]['id']: '暂存尾文'},
            table_cells={ids[0]: cells, ids[1]: invalid})
    assert value == before
    result = edit_resource(value, item['id'], title='已改标题', values={item['blocks'][-1]['id']: '已改尾文'},
        table_cells={ids[0]: cells}, locked=True, expected=digest(value))
    assert result['resources'][0]['locked'] and result['resources'][0]['blocks'][-1]['value'] == '已改尾文'


@pytest.mark.parametrize('damage', [
    'source_hash', 'table_hash', 'schema', 'top_extra', 'table_extra', 'empty_tables',
    'wrong_block', 'omit_cell', 'duplicate_cell', 'reorder', 'coordinate', 'bool_coordinate',
    'cell_extra', 'omit_slot', 'extra_slot', 'token_object', 'segments_string', 'marker', 'partial_marker',
    'continue', 'text_mismatch', 'redundant', 'too_long', 'block_too_long'])
def test_strict_corruption_never_falls_back_to_free_text(damage):
    item, ids, _, _ = synthetic_item(); out = edited(item, ids[0])
    spec = out['working_tables']; table = spec['tables'][ids[0]]; cells = table['cells']
    if damage == 'source_hash': spec['source_original_sha256'] = 'd' * 64
    elif damage == 'table_hash': table['source_table_sha256'] = 'd' * 64
    elif damage == 'schema': spec['schema'] = 'unknown'
    elif damage == 'top_extra': spec['images'] = []
    elif damage == 'table_extra': table['grid'] = []
    elif damage == 'empty_tables': spec['tables'] = {}
    elif damage == 'wrong_block': spec['tables'][out['blocks'][-1]['id']] = spec['tables'].pop(ids[0])
    elif damage == 'omit_cell': cells.pop()
    elif damage == 'duplicate_cell': cells[1] = deepcopy(cells[0])
    elif damage == 'reorder': cells[0], cells[1] = cells[1], cells[0]
    elif damage == 'coordinate': cells[1]['column'] = 2
    elif damage == 'bool_coordinate': cells[0]['row'] = True
    elif damage == 'cell_extra': cells[0]['column_span'] = 99
    elif damage == 'omit_slot': cells[4]['segments'].pop()
    elif damage == 'extra_slot': cells[4]['segments'].append('')
    elif damage == 'token_object': cells[0]['segments'][0] = {'kind': 'image', 'asset_id': 'x'}
    elif damage == 'segments_string': cells[0]['segments'] = '标题'
    elif damage == 'marker': cells[0]['segments'][0] = MARKER
    elif damage == 'partial_marker': cells[0]['segments'][0] = '【待查看原文：不完整'
    elif damage == 'continue': cells[7]['segments'][0] = '隐藏的新文'
    elif damage == 'text_mismatch': text_block(out, ids[0])['value'] += '静默损坏'
    elif damage == 'redundant': table['cells'] = editable_table_cells(item, ids[0])
    elif damage == 'too_long': cells[0]['segments'][0] = 'x' * 120001
    elif damage == 'block_too_long':
        cells[0]['segments'][0] = 'x' * 70000; cells[1]['segments'][0] = 'y' * 70000
    before = deepcopy(out)
    with pytest.raises(ValueError): validate_resource(out)
    with pytest.raises(ValueError): source_table_projection(out, text_block(out, ids[0]))
    with pytest.raises(ValueError): table_cell_model(out, ids[0])
    assert out == before


def test_sidecar_cannot_attach_to_other_kind_other_resource_or_changed_original():
    item, ids, _, _ = synthetic_item(); out = edited(item, ids[0])
    personal = resource('personal_text', '个人稿', [block('text', 'personal', '正文')],
        dict(source_path='作者输入', source_sha256='unknown', source_revision='unknown'))
    personal['working_tables'] = deepcopy(out['working_tables'])
    with pytest.raises(ValueError): validate_resource(personal)
    other, _, _, _ = synthetic_item(); other['working_tables'] = deepcopy(out['working_tables'])
    with pytest.raises(ValueError): validate_resource(other)
    bad = deepcopy(out); bad['original']['evidence']['source_roles']['schema'] = 'unknown'
    bad['original_sha256'] = digest(bad['original'])
    bad['working_tables']['source_original_sha256'] = bad['original_sha256']
    with pytest.raises(ValueError): validate_resource(bad)


@pytest.mark.parametrize('marker', ['【待查看原文：未知对象】', '【待查看原文：未完成'])
def test_unmapped_source_is_not_upgraded_or_parsed(marker):
    item, ids, _, _ = synthetic_item([row(marker, '原文')])
    assert source_table_projection(item, text_block(item, ids[0])) is None
    assert table_cell_model(item, ids[0]) is None
    with pytest.raises(ValueError, match='完整映射'):
        edited(item, ids[0], [{'row': 1, 'column': 1, 'segments': ['重写']},
                              {'row': 1, 'column': 2, 'segments': ['原文']}])


def test_legacy_free_text_and_independent_images_remain_unchanged():
    item, ids, _, _ = synthetic_item(); cells = editable_table_cells(item, ids[0])
    text_block(item, ids[0])['value'] = '教师的既有自由改稿\n〔第1行·第1列〕\n不可逆解析'
    before = deepcopy(item)
    assert validate_resource(item) == before
    assert table_cell_model(item, ids[0]) is None
    assert source_table_projection(item, text_block(item, ids[0])) is None
    with pytest.raises(ValueError, match='自由文字改稿'):
        edited(item, ids[0], cells)
    assert item == before and len([b for b in item['blocks'] if b['kind'] == 'image']) == 3


def test_notes_inspect_source_but_require_explicit_plain_layout_for_actual_edit():
    from integrations.deeptutor_shchem_v1.desktop_composition_exposition import SCHEMA as notes_schema
    item, ids, _, _ = synthetic_item([row('原文', '观察'), row('甲', '乙')])
    item['exposition'] = {'schema': notes_schema, 'kind': 'notes'}
    assert validate_resource(item) == item
    assert not table_cell_model(item, ids[0])['editing_available']
    with pytest.raises(ValueError, match='普通图文排版'):
        edited(item, ids[0])
    plain = deepcopy(item); plain.pop('exposition')
    out = edited(plain, ids[0]); out['exposition'] = deepcopy(item['exposition'])
    with pytest.raises(ValueError, match='普通图文排版'): validate_resource(out)


def test_omitted_grid_and_nonempty_continue_are_not_new_editable_cells():
    rows = [row('甲', '乙', '丙'), row((1, '起点', 'restart'), '右', after=1),
        row((1, '原续接内容', 'continue'), '右', after=1), row('中间', before=1, after=1)]
    item, ids, _, _ = synthetic_item(rows)
    model = table_cell_model(item, ids[0]); assert model['cells'][-1]['column'] == 2
    continuation = next(c for c in model['cells'] if c['vertical_merge'] == 'continue')
    assert not continuation['editable'] and continuation['segments'] == ['原续接内容']
    out = edited(item, ids[0]); projection = source_table_projection(out, text_block(out, ids[0]))
    assert projection['rows'][2]['cells'][0]['text'] == '原续接内容'
    assert projection['rows'][-1]['grid_before'] == projection['rows'][-1]['grid_after'] == 1


def test_public_projection_validates_once_without_recursive_sidecar(monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_lesson_composition as module
    item, ids, _, _ = synthetic_item(); out = edited(item, ids[0])
    original, calls = module.validate_resource, []
    def counted(value):
        calls.append(value['id'])
        assert len(calls) <= 1
        return original(value)
    monkeypatch.setattr(module, 'validate_resource', counted)
    assert source_table_projection(out, text_block(out, ids[0]))['occurrences']
    assert calls == [out['id']]


REAL_ROOT = Path('E:/项目/上海高中化学等级考/outputs')
REAL = {
    '062': (REAL_ROOT / '2026-10-08-native-image-table-workbench/native-expanded-course-office-v10/preparation-payload.json',
        'ebb17e33708150c5f9930bda177bd891deca40a2f944ecdd56454438ca0cad32',
        '4bf36447638b9835792e99c25ce5fed43dbddc13f326e69e585efe64f3f7cc91'),
    '072': (REAL_ROOT / '2026-10-08-polymer-paint-preview-workbench/native-inserted-classroom-office-v1/preparation-payload.json',
        '78da13c3c0278814fa90bed9aa3a16c790c7e8d7de622fb9522bb19f63493221',
        '01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33'),
}


def read_real(code):
    path, expected, source_sha = REAL[code]
    if not path.is_file(): pytest.skip('Exact frozen local acceptance fixture unavailable')
    before = (sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
    assert before[0] == expected, 'Frozen payload changed; do not substitute a similar source'
    payload = json.loads(path.read_text('utf-8-sig'))
    resources = payload['lesson_design']['composition']['resources']
    selected = [r for r in resources if r['original']['kind'] == 'handout_knowledge'
        and r['original']['evidence']['source_sha256'] == source_sha]
    assert selected
    sources = {r['original']['evidence']['source_path'] for r in selected}
    manifests = {path: before}
    for filename in sources:
        source = Path(filename)
        assert source.is_file(), 'Exact sealed source path unavailable'
        manifests[source] = (sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source.stat().st_mtime_ns)
        assert manifests[source][0] == source_sha
    return payload, manifests


def assert_manifests_unchanged(manifests):
    for path, before in manifests.items():
        assert (sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('code,index,cell_count,picture_count', [
    ('062', 45, 21, 9), ('072', 108, 11, 3), ('072', 112, 18, 2),
    ('072', 270, 8, 4), ('072', 305, 9, 1), ('072', 307, 9, 1)])
def test_frozen_real_source_edit_preserves_every_neighbor_and_occurrence(code, index, cell_count, picture_count):
    payload, manifests = read_real(code); before = deepcopy(payload)
    candidates = [(r, b) for r in payload['lesson_design']['composition']['resources']
        if r['original']['kind'] == 'handout_knowledge' and r['original']['evidence']['source_sha256'] == REAL[code][2]
        for b in r['blocks'] if b['kind'] == 'text' and b['source_index'] == index]
    assert len(candidates) == 1
    item, native = candidates[0]
    model = table_cell_model(item, native['id'])
    assert model is not None and len(model['cells']) == cell_count and len(model['occurrences']) == picture_count
    source_projection = source_table_projection(item, native)
    chosen = next(i for i, c in enumerate(model['cells']) if c['editable'])
    cells = changed(item, native['id'], cell_index=chosen, text=model['cells'][chosen]['segments'][0] + '\n观察结构')
    out = edited(item, native['id'], cells)
    after = source_table_projection(out, text_block(out, native['id']))
    assert after['image_block_ids'] == source_projection['image_block_ids']
    assert after['occurrences'] == source_projection['occurrences']
    assert out['original'] == item['original'] and out['original_sha256'] == item['original_sha256']
    assert [b for b in out['blocks'] if b['id'] != native['id']] == [b for b in item['blocks'] if b['id'] != native['id']]
    assert out['original']['evidence']['source_roles'] == item['original']['evidence']['source_roles']
    if code == '062':
        assert all(b['role'] == 'teacher_note' for b in out['blocks'])
        assert 'schema' not in out['original']['evidence']['source_roles']
    if index == 270:
        refs = [ref for b in out['original']['evidence']['section_snapshot']['blocks'] for ref in b['images']]
        assert sum('transform' in r for r in refs) == 2
        assert sum(r.get('source_object', {}).get('prog_id') == 'PBrush' for r in refs) == 2
        assert out['original']['evidence']['source_roles']['schema'] == SOURCE_ROLES_SCHEMA
    assert payload == before
    assert_manifests_unchanged(manifests)


def test_frozen_real_legacy_b101_b245_b247_do_not_migrate():
    payload, manifests = read_real('062'); before = deepcopy(payload)
    seen = set()
    for item in payload['lesson_design']['composition']['resources']:
        if item['original']['kind'] != 'handout_knowledge': continue
        if item['original']['evidence']['source_sha256'] != REAL['062'][2]: continue
        originals = {b['id']: b for b in item['original']['blocks']}
        for b in item['blocks']:
            if b['kind'] == 'text' and b['source_index'] in (101, 245, 247):
                assert b['value'] != originals[b['id']]['value']
                assert source_table_projection(item, b) is None
                assert table_cell_model(item, b['id']) is None
                seen.add(b['source_index'])
    assert seen == {101, 245, 247} and payload == before
    assert_manifests_unchanged(manifests)
