"""Explicit, source-bound question-page intent and classroom answer entrances.

This is a work-copy presentation choice. It never changes Original, block roles,
student visibility, image bytes or review/publication qualification. Answers
bound to source identities use current work-copy text only in the classroom.
"""
from copy import deepcopy
import math

SCHEMA = 'shchem.question-answer-reveal.v1'
FIELD = 'question_answer_reveal'
DEFAULT_FRAME = {'x': 12.0, 'y': 7.0, 'width': 296.0, 'height': 166.0}
FRAME_KEYS = {'x', 'y', 'width', 'height'}


def active_question_page(item, format):
    return format == '16:9' and FIELD in item


def _eligible(item):
    original = item.get('original', {})
    rows = original.get('blocks', [])
    def question_content(blocks):
        return any(b.get('role') == 'question' and
            (b.get('kind') == 'image' or isinstance(b.get('value'), str) and b['value'].strip())
            for b in blocks)
    if (original.get('kind') != 'complete_question'
            or original.get('evidence', {}).get('scope_status') != 'complete_native_scope'
            or not question_content(rows) or not question_content(item.get('blocks', []))):
        raise ValueError('整题同页与原答案点击只适用于保留完整学生题面的原生完整题。')


def _answer_supported(row):
    if row['kind'] != 'text' or not row['value'].strip():
        return '同页点击目前仅支持一块短文字原答案。'
    presentation = row.get('presentation', {})
    if presentation.get('images') or presentation.get('eqs') or presentation.get('scene'):
        return '带原图或公式对象的答案尚未支持标题旁点击显示，请保留教师解析。'
    if '\n' in row['value'] or '\r' in row['value']:
        return '多行原答案不能放入标题旁的一行点击层。'
    return ''


def validate_question_answer_reveal(item):
    """Validate the optional source binding without Qt, I/O or recursion."""
    if FIELD not in item:
        return
    _eligible(item)
    value = item[FIELD]
    if (not isinstance(value, dict)
            or set(value) != {'schema', 'original_sha256', 'answer_block_ids', 'classroom_frame'}
            or value['schema'] != SCHEMA
            or value['original_sha256'] != item['original_sha256']):
        raise ValueError('原答案点击配置与当前 Original 摘要失配，请重新选择原答案。')
    ids = value['answer_block_ids']
    if (not isinstance(ids, list) or len(ids) > 1
            or any(not isinstance(identity, str) for identity in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('标题旁点击目前只支持明确选择一块短原答案；可不选答案而仅保整题同页。')
    originals = {b['id']: b for b in item['original']['blocks']}
    working = {b['id']: b for b in item['blocks']}
    for identity in ids:
        row = originals.get(identity)
        if row is None or row['role'] != 'answer':
            raise ValueError('点击答案须引用当前 Original 中确切的 answer 块。')
        reason = _answer_supported(working[identity])
        if reason:
            raise ValueError(reason)
    frame = value['classroom_frame']
    if (not isinstance(frame, dict) or set(frame) != FRAME_KEYS
            or any(type(frame[k]) not in (int, float) or not math.isfinite(frame[k]) for k in FRAME_KEYS)
            or frame['x'] < 0 or frame['y'] < 0 or frame['width'] <= 0 or frame['height'] <= 0
            or frame['x'] + frame['width'] > 320 or frame['y'] + frame['height'] > 180):
        raise ValueError('整题同页的课堂外框必须完整位于16:9画幅内。')


def answer_reveal_options(item):
    """Return exact Original choices; no role-wide default or inferred answer."""
    from .desktop_lesson_composition import validate_resource
    item = validate_resource(item)
    _eligible(item)
    selected = item.get(FIELD, {}).get('answer_block_ids', [])
    working = {b['id']: b for b in item['blocks']}
    choices = []
    for row in item['original']['blocks']:
        if row['role'] != 'answer':
            continue
        changed = working[row['id']]['value'] != row['value']
        current = working[row['id']]
        reason = _answer_supported(current)
        choices.append({'block_id': row['id'], 'source_index': row['source_index'],
            'text': current['value'] if row['kind'] == 'text' else '原答案图片',
            'original_text': row['value'] if row['kind'] == 'text' else '原答案图片',
            'kind': row['kind'], 'has_images': row['kind'] == 'image' or bool(row.get('presentation', {}).get('images')),
            'working_value_changed': changed, 'supported': not reason, 'reason': reason,
            'selected': row['id'] in selected})
    return choices


def _selected(value, resource_id):
    if not isinstance(resource_id, str) or resource_id not in value['placements']:
        raise ValueError('请选择当前作品中的完整题。')
    return next(r for r in value['resources'] if r['id'] == resource_id)


def configure_question_answer_reveal(composition, resource_id, block_ids, assets=(), image_data=None):
    """Atomically measure and locally reflow one question; inputs stay unchanged.

    An empty explicit list enables only the complete-question page. A4/A3 keep
    existing classroom intent inactive; configuring a new intent requires 16:9.
    """
    from .desktop_lesson_composition import validate_composition
    from .desktop_composition_resource_layout import arrange_resource_layout
    value = validate_composition(composition)
    if value['format'] != '16:9':
        raise ValueError('请切换到16:9课堂投影后设置整题同页与原答案点击。')
    item = _selected(value, resource_id)
    _eligible(item)
    frame = item.get(FIELD, {}).get('classroom_frame', DEFAULT_FRAME)
    item[FIELD] = {'schema': SCHEMA, 'original_sha256': item['original_sha256'],
        'answer_block_ids': deepcopy(block_ids), 'classroom_frame': deepcopy(frame)}
    reveals = [identity for identity in value.get('click_reveals', []) if identity != resource_id]
    if reveals:
        value['click_reveals'] = reveals
    else:
        value.pop('click_reveals', None)
    value = validate_composition(value)
    result = arrange_resource_layout(value, resource_id, assets, image_data or {})
    errors = question_answer_reveal_errors(result, resource_id, assets, image_data or {})
    if errors:
        raise ValueError('\n'.join(errors))
    return result


def clear_question_answer_reveal(composition, resource_id):
    from .desktop_lesson_composition import validate_composition
    value = validate_composition(composition)
    _selected(value, resource_id).pop(FIELD, None)
    return validate_composition(value)


def question_answer_reveal_errors(composition, resource_id, assets=(), image_data=None):
    """Check authored and independent scopes plus other authored frame overlap."""
    from .desktop_lesson_composition import resource_frames, validate_composition
    from .desktop_composition_resource_layout import resource_layout_errors
    from .desktop_composition_render import _overlaps
    value = validate_composition(composition)
    item = _selected(value, resource_id)
    if not active_question_page(item, value['format']):
        return []
    errors = resource_layout_errors(value, resource_id, assets, image_data or {})
    if 'student_arrangement' in value:
        authored = deepcopy(value); authored.pop('student_arrangement')
        errors.extend(resource_layout_errors(authored, resource_id, assets, image_data or {}))
    for frame in resource_frames(value['placements'][resource_id]):
        for other_id, placement in value['placements'].items():
            if other_id == resource_id:
                continue
            for other in resource_frames(placement):
                if frame['page_id'] == other['page_id'] and _overlaps(frame, other):
                    errors.append('整题同页外框与同页其他素材重叠，请先清除此配置或调整外框。')
    return list(dict.fromkeys(errors))


def classroom_answer_reveals(composition):
    """Audit bindings for the active classroom scope only."""
    if composition['format'] != '16:9':
        return []
    return [{'resource_id': item['id'], 'original_sha256': item['original_sha256'],
             'answer_block_ids': list(item[FIELD]['answer_block_ids'])}
            for item in composition['resources']
            if FIELD in item and item[FIELD]['answer_block_ids']]


# Short spellings are useful to pure clients; descriptive names remain public.
options = answer_reveal_options
configure = configure_question_answer_reveal
clear = clear_question_answer_reveal
