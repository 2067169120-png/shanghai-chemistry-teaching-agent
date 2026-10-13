"""Source-bound work text between immutable native Word table picture slots.

Only cell text segments are persisted. Grids, merges, images and their ordered
occurrence identities always come from the validated Original. The internal
helpers deliberately do not call the resource validator, so validation and
display can share exactly one projection without a recursive validation path.
"""
from copy import deepcopy

from .desktop_composition_inline import MARKER

SCHEMA = 'shchem.native-word-table-working.v1'
_PREFIX = '【待查看原文：'
_LIMIT = 120000
_PICTURE_MARKERS = {
    '【待查看原文：图片或图形】', '【待查看原文：图片或旧式图形】',
    '【待查看原文：嵌入对象或旧公式】',
}


def _sealed_table_context(item, block_id):
    """Index one table in an already validated resource, without reading files."""
    original = item['original']
    if original['kind'] != 'handout_knowledge':
        return None
    native = [b for b in original['blocks'] if b['id'] == block_id]
    working = [b for b in item['blocks'] if b['id'] == block_id]
    if (len(native) != 1 or len(working) != 1 or native[0]['kind'] != 'text'
            or 'presentation' in native[0] or 'presentation' in working[0]
            or working[0]['kind'] != 'text'
            or working[0]['source_index'] != native[0]['source_index']):
        return None
    native, working = native[0], working[0]
    matches = [b for b in original['evidence']['section_snapshot']['blocks']
        if b['index'] == native['source_index']]
    if len(matches) != 1 or 'table_rows' not in matches[0]:
        return None
    sealed = matches[0]
    if sealed['text'] != native['value']:
        raise ValueError('讲义表格封存原文与Original区块不一致。')
    references = sealed['images']
    pictures = [b for b in original['blocks']
        if b['source_index'] == native['source_index'] and b['kind'] == 'image']
    working_pictures = [b for b in item['blocks']
        if b['source_index'] == native['source_index'] and b['kind'] == 'image']
    if working_pictures != pictures:
        raise ValueError('表格工作图片与封存出现位置不一致，未套用旧表格绑定。')
    markers = [m.group() for r in sealed['table_rows'] for c in r['cells']
        for m in MARKER.finditer(c['text'])]
    if (markers != MARKER.findall(sealed['text']) or len(markers) != len(references)
            or len(pictures) != len(references)
            or any(m not in _PICTURE_MARKERS for m in markers)):
        return None
    if any(_PREFIX in MARKER.sub('', c['text'])
            for r in sealed['table_rows'] for c in r['cells']):
        return None
    cells, ordinal = [], 0
    for ri, source_row in enumerate(sealed['table_rows'], 1):
        column = source_row['grid_before'] + 1
        for ci, cell in enumerate(source_row['cells']):
            segments, slots, cursor = [], [], 0
            for marker in MARKER.finditer(cell['text']):
                segments.append(cell['text'][cursor:marker.start()])
                ref, picture = references[ordinal], pictures[ordinal]
                if (picture['role'] != native['role'] or picture['value'] != ref['asset_id']
                        or ref['position'] != ordinal + 1
                        or ref['block_index'] != native['source_index']):
                    raise ValueError('表格原图位置、使用范围或出现次序不一致。')
                slots.append({**deepcopy(ref), 'image_block_id': picture['id'],
                    'placeholder': marker.group(), 'row': ri, 'column': column,
                    'column_span': cell['column_span'],
                    'text_start': marker.start(), 'text_end': marker.end()})
                cursor = marker.end(); ordinal += 1
            segments.append(cell['text'][cursor:])
            cells.append({'row': ri, 'column': column, 'cell_index': ci,
                'column_span': cell['column_span'], 'vertical_merge': cell['vertical_merge'],
                'editable': cell['vertical_merge'] != 'continue',
                'original_segments': segments, 'slots': slots})
            column += cell['column_span']
    return {'block_id': block_id, 'native': native, 'working': working,
        'sealed': sealed, 'cells': cells, 'image_block_ids': [p['id'] for p in pictures]}


def _original_cells(context):
    return [{'row': c['row'], 'column': c['column'],
             'segments': deepcopy(c['original_segments'])} for c in context['cells']]


def _validate_cells(context, cells):
    if not isinstance(cells, list) or len(cells) != len(context['cells']):
        raise ValueError('工作表格须按原顺序保留全部原生单元格。')
    for source, cell in zip(context['cells'], cells):
        if (not isinstance(cell, dict) or set(cell) != {'row', 'column', 'segments'}
                or type(cell['row']) is not int or type(cell['column']) is not int
                or (cell['row'], cell['column']) != (source['row'], source['column'])):
            raise ValueError('工作表格单元格坐标、顺序或字段与原网格不一致。')
        segments = cell['segments']
        if (not isinstance(segments, list) or len(segments) != len(source['slots']) + 1
                or any(not isinstance(t, str) or len(t) > _LIMIT for t in segments)):
            raise ValueError('工作表格文字片段须完整对应每次原图前后的固定槽位。')
        if any(_PREFIX in t for t in segments):
            raise ValueError('单元格工作文字不能包含原图占位标记；原图槽位由来源固定。')
        if not source['editable'] and segments != source['original_segments']:
            raise ValueError('纵向合并续接单元格不可编辑，请编辑原合并起点。')
        if sum(map(len, segments)) + sum(len(s['placeholder']) for s in source['slots']) > _LIMIT:
            raise ValueError('工作表格单元格文字超出草稿容量。')


def _table_projection(context, cells=None):
    """Compile the only table projection used by editing, canvas and Office."""
    cells = _original_cells(context) if cells is None else cells
    rows = deepcopy(context['sealed']['table_rows'])
    tokens_by_cell, occurrences = {}, []
    for source, cell in zip(context['cells'], cells):
        text, tokens, cursor = [], [], 0
        for index, segment in enumerate(cell['segments']):
            text.append(segment)
            if segment:
                tokens.append({'kind': 'text', 'text': segment, 'script': 'baseline'})
            cursor += len(segment)
            if index < len(source['slots']):
                slot = source['slots'][index]
                occurrence = {**deepcopy(slot), 'text_start': cursor,
                    'text_end': cursor + len(slot['placeholder'])}
                occurrences.append(occurrence)
                tokens.append({'kind': 'image', **occurrence})
                text.append(slot['placeholder']); cursor = occurrence['text_end']
        rows[source['row'] - 1]['cells'][source['cell_index']]['text'] = ''.join(text)
        if source['slots']:
            tokens_by_cell[(source['row'] - 1, source['column'] - 1)] = tokens
    return {'rows': rows, 'cell_tokens': tokens_by_cell, 'occurrences': occurrences,
        'image_block_ids': list(context['image_block_ids'])}


def _working_text(projection):
    from .desktop_preparation_sources import _readable_table
    text = _readable_table(projection['rows'])
    if len(text) > _LIMIT:
        raise ValueError('工作表格完整文字超出草稿容量。')
    return text


def validate_working_tables(item):
    """Check sidecar after Original/source validation; never call it recursively."""
    if 'working_tables' not in item:
        return
    from .desktop_lesson_composition import digest
    spec = item['working_tables']
    if (item['original']['kind'] != 'handout_knowledge' or not isinstance(spec, dict)
            or set(spec) != {'schema', 'source_original_sha256', 'tables'}
            or spec['schema'] != SCHEMA
            or spec['source_original_sha256'] != item['original_sha256']
            or not isinstance(spec['tables'], dict) or not spec['tables']):
        raise ValueError('工作表格侧车字段、来源摘要或资源类型无效。')
    if 'exposition' in item:
        raise ValueError('工作表格单元格编辑须使用普通图文排版，不能与知识讲解与笔记共存。')
    for block_id, table in spec['tables'].items():
        context = _sealed_table_context(item, block_id)
        if (context is None or not isinstance(table, dict)
                or set(table) != {'source_table_sha256', 'cells'}
                or table['source_table_sha256'] != digest(context['sealed'])):
            raise ValueError('工作表格须绑定当前Original中可完整映射的唯一原表及其摘要。')
        _validate_cells(context, table['cells'])
        if table['cells'] == _original_cells(context):
            raise ValueError('没有实际改单元格的表格不能保存工作侧车。')
        if _working_text(_table_projection(context, table['cells'])) != context['working']['value']:
            raise ValueError('工作表格单元格与完整工作文字不一致，请核对同一次编辑事务。')


def _apply_table_cells(item, changes):
    """Stage changes on a validated, detached, unlocked resource transaction."""
    from .desktop_lesson_composition import digest
    if not isinstance(changes, dict):
        raise ValueError('工作表格编辑须按原表文字区块标识提交。')
    tables = deepcopy(item.get('working_tables', {}).get('tables', {}))
    for block_id, cells in changes.items():
        context = _sealed_table_context(item, block_id)
        if context is None:
            raise ValueError('当前区块不能完整映射到原生表格单元格。')
        if block_id not in tables and context['working']['value'] != context['native']['value']:
            raise ValueError('已有自由文字改稿不能自动迁移为原表单元格编辑。')
        _validate_cells(context, cells)
        text = _working_text(_table_projection(context, cells))
        if cells == _original_cells(context):
            tables.pop(block_id, None)
        else:
            tables[block_id] = {'source_table_sha256': digest(context['sealed']),
                'cells': deepcopy(cells)}
        context['working']['value'] = text
    if tables:
        item['working_tables'] = {'schema': SCHEMA,
            'source_original_sha256': item['original_sha256'], 'tables': tables}
    else:
        item.pop('working_tables', None)


def table_cell_model(item, block_id):
    """Return a detached editing model; opening it never adds a sidecar.

    Existing free-text edits and unmappable source markers return None. Notes
    may inspect a source table, but must explicitly leave notes before editing.
    """
    from .desktop_lesson_composition import validate_resource
    item = validate_resource(item)
    context = _sealed_table_context(item, block_id)
    if context is None:
        return None
    table = item.get('working_tables', {}).get('tables', {}).get(block_id)
    if table is None and context['working']['value'] != context['native']['value']:
        return None
    cells = table['cells'] if table is not None else _original_cells(context)
    projection = _table_projection(context, cells)
    result = []
    for source, cell in zip(context['cells'], cells):
        slots = [o for o in projection['occurrences']
            if (o['row'], o['column']) == (cell['row'], cell['column'])]
        result.append({k: deepcopy(source[k]) for k in
            ('row', 'column', 'column_span', 'vertical_merge', 'editable', 'original_segments')})
        result[-1].update(segments=deepcopy(cell['segments']), slots=deepcopy(slots))
    return {'block_id': block_id, 'rows': projection['rows'], 'cells': result,
        'occurrences': projection['occurrences'], 'image_block_ids': projection['image_block_ids'],
        'editing_available': 'exposition' not in item}


def editable_table_cells(item, block_id):
    """Return only the strict full cell payload accepted by edit_resource."""
    model = table_cell_model(item, block_id)
    if model is None:
        return None
    return [{k: deepcopy(c[k]) for k in ('row', 'column', 'segments')} for c in model['cells']]
