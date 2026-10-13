"""Source-bound teaching framework cards in the editable lesson canvas."""
from copy import deepcopy
import re

from .desktop_teaching_framework import FIELDS, SCHEMA, framework_from_design, validate_framework
from .desktop_lesson_composition import (DESIGN_SCHEMA, FORMATS, block, digest, new_composition,
    resource, resource_frames, validate_composition, validate_resource)

SNAPSHOT_SCHEMA = 'shchem.framework-card-source.v1'
SECTIONS = (
    ('goals', '本课目标', ('knowledge_skills', 'process_methods', 'values', 'focus', 'difficulty', 'methods')),
    ('introduction', '导入新课', ('introduction',)),
    ('consolidation', '巩固小结', ('consolidation',)),
    ('board_design', '板书设计', ('board_design',)),
    ('homework', '课后任务', ('homework',)),
)
BY_SECTION = {key: (title, fields) for key, title, fields in SECTIONS}
LABELS = dict(FIELDS)


def validate_snapshot(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'design_id', 'section', 'fields'}
            or value['schema'] != SNAPSHOT_SCHEMA
            or not isinstance(value['design_id'], str)
            or not re.fullmatch(r'LD-[0-9a-f]{32}', value['design_id'])
            or not isinstance(value['section'], str) or value['section'] not in BY_SECTION
            or not isinstance(value['fields'], dict)
            or set(value['fields']) != set(BY_SECTION[value['section']][1])):
        raise ValueError('教案栏目来源记录不完整。')
    validate_framework({'schema_version': SCHEMA,
        **{key: value['fields'].get(key, '') for key, _ in FIELDS}})
    return deepcopy(value)


def framework_snapshot(plan, section):
    if section not in BY_SECTION:
        raise ValueError('请选择已有的教案栏目。')
    framework = framework_from_design({'lesson_design': plan})
    return validate_snapshot({'schema': SNAPSHOT_SCHEMA, 'design_id': plan['id'],
        'section': section, 'fields': {key: framework[key] for key in BY_SECTION[section][1]}})


def block_specs(snapshot):
    snapshot = validate_snapshot(snapshot)
    fields = snapshot['fields']
    if snapshot['section'] == 'goals':
        join = lambda keys: '\n\n'.join(LABELS[key] + '：\n' + fields[key]
            for key in keys if fields[key].strip())
        return [('text', 'material', join(('knowledge_skills', 'process_methods', 'values'))),
                ('text', 'teacher_note', join(('focus', 'difficulty', 'methods')))]
    return [('text', 'material', fields[BY_SECTION[snapshot['section']][1][0]])]


def framework_resource(plan, section):
    snapshot = framework_snapshot(plan, section)
    return resource('lesson_framework', BY_SECTION[section][0],
        [block(kind, role, text) for kind, role, text in block_specs(snapshot)],
        {'source_path': '当前教案框架', 'source_sha256': 'unknown',
         'source_revision': digest(snapshot), 'design_id': plan['id'],
         'section': section, 'framework_snapshot': snapshot})


def validate_framework_resource(item):
    evidence = item['original']['evidence']
    snapshot = validate_snapshot(evidence['framework_snapshot'])
    link = item['framework_link']
    if (not isinstance(link, dict) or set(link) != {'design_id', 'section', 'mode'}
            or link['design_id'] != snapshot['design_id'] or link['section'] != snapshot['section']
            or link['mode'] not in {'linked', 'snapshot'}):
        raise ValueError('教案栏目须关联当前框架，或保留为课件副本。')
    original = item['original']
    if (original['title'] != BY_SECTION[snapshot['section']][0]
            or [(b['kind'], b['role'], b['value']) for b in original['blocks']] != block_specs(snapshot)
            or any(b['source_index'] is not None or 'presentation' in b for b in original['blocks'])):
        raise ValueError('教案栏目内容及教师／学生范围与来源记录不一致。')


def work_copy_changed(item):
    return item['title'] != item['original']['title'] or item['blocks'] != item['original']['blocks']


def framework_status(plan, item):
    validate_resource(item)
    if item['original']['kind'] != 'lesson_framework':
        return None
    link = item['framework_link']
    if link['mode'] == 'snapshot':
        return 'snapshot'
    if link['design_id'] != plan['id']:
        return 'missing'
    return ('current' if digest(framework_snapshot(plan, link['section'])) ==
            item['original']['evidence']['source_revision'] else 'changed')


def checked_framework_links(plan):
    results = []
    for item in plan.get('composition', {}).get('resources', []):
        if item['original']['kind'] != 'lesson_framework':
            continue
        state = framework_status(plan, item)
        if state in {'changed', 'missing'}:
            raise ValueError('课件中的“' + item['title'] + '”仍使用旧教案内容，请同步栏目后生成。')
        results.append({'resource_id': item['id'], **item['framework_link'], 'status': state,
                        'source_revision': item['original']['evidence']['source_revision']})
    return results


def _isolated_layout(item, format):
    from .desktop_composition_render import arrange_composition
    value = new_composition(format)
    w, h = FORMATS[format]
    value['resources'] = [item]
    value['placements'] = {item['id']: {'page_id': value['pages'][0], 'x': 12, 'y': 12,
                                      'width': w - 24, 'height': h - 24}}
    return arrange_composition(value, [], {})


def _prune_old_pages(value, pages):
    occupied = {frame['page_id'] for placement in value['placements'].values()
                for frame in resource_frames(placement)}
    value['pages'] = [page for page in value['pages'] if page not in pages or page in occupied]


def _new_position(value, section):
    priorities = [row[0] for row in SECTIONS]
    priority = priorities.index(section)
    for index, item in enumerate(value['resources']):
        if item['original']['kind'] == 'lesson_framework':
            if priorities.index(item['framework_link']['section']) > priority:
                return index
        elif priority < 2:
            return index
    return len(value['resources'])


def synchronize_framework(plan, sections, *, replace_edited=False, expected=None):
    """Update selected cards atomically; other resources and frames stay exact.

    An edited work copy is explicitly detached and retained unless replacement
    was selected. Only a changed framework card that no longer fits is repaged.
    """
    from .desktop_lesson_design import validate_design
    from .desktop_composition_render import layout_composition
    if expected is not None and digest(plan) != expected:
        raise ValueError('作品已变化，请重新预览教案栏目。')
    if (not isinstance(sections, (list, tuple)) or any(not isinstance(key, str) for key in sections)
            or len(set(sections)) != len(sections)
            or any(key not in BY_SECTION for key in sections) or type(replace_edited) is not bool):
        raise ValueError('教案栏目选择无效。')
    result = validate_design(plan)
    value = result.get('composition', new_composition('16:9'))
    if not value['resources'] and len(value['pages']) == 1:
        value['pages'] = []
    changes = []
    for section, title, _ in SECTIONS:
        if section not in sections:
            continue
        old = next((r for r in value['resources'] if r['original']['kind'] == 'lesson_framework'
                    and r['framework_link']['section'] == section), None)
        fresh = framework_resource(result, section)
        has_content = any(b['value'].strip() for b in fresh['blocks'])
        action = 'unchanged'
        if old is not None:
            if old['framework_link']['design_id'] != result['id']:
                raise ValueError('该教案栏目属于另一份作品，未替换内容。')
            if framework_status(result, old) == 'current':
                changes.append({'section': section, 'title': title, 'action': action}); continue
            if (work_copy_changed(old) or old['framework_link']['mode'] == 'snapshot') and not replace_edited:
                old['framework_link']['mode'] = 'snapshot'
                changes.append({'section': section, 'title': title, 'action': 'kept_copy'}); continue
            index = value['resources'].index(old)
            old_frames = resource_frames(value['placements'][old['id']])
            old_pages = {frame['page_id'] for frame in old_frames}
            page_index = min(value['pages'].index(frame['page_id']) for frame in old_frames)
            if not has_content:
                value['resources'].pop(index); value['placements'].pop(old['id'])
                _prune_old_pages(value, old_pages)
                action = 'removed_empty'
            else:
                fresh['id'], fresh['locked'] = old['id'], old['locked']
                for old_block, new_block in zip(old['original']['blocks'], fresh['original']['blocks']):
                    new_block['id'] = old_block['id']
                fresh['blocks'] = deepcopy(fresh['original']['blocks'])
                fresh['original_sha256'] = digest(fresh['original'])
                value['resources'][index] = fresh
                isolated = deepcopy(value)
                isolated['resources'] = [fresh]
                isolated['placements'] = {fresh['id']: value['placements'][fresh['id']]}
                if layout_composition(isolated, [], {}, student=False)['errors']:
                    value['placements'].pop(fresh['id']); _prune_old_pages(value, old_pages)
                    arranged = _isolated_layout(fresh, value['format'])
                    page_index = min(page_index, len(value['pages']))
                    value['pages'][page_index:page_index] = arranged['pages']
                    value['placements'][fresh['id']] = arranged['placements'][fresh['id']]
                action = 'updated'
        elif has_content:
            index = _new_position(value, section)
            page_index = (min(value['pages'].index(frame['page_id']) for frame in
                          resource_frames(value['placements'][value['resources'][index]['id']]))
                          if index < len(value['resources']) else len(value['pages']))
            arranged = _isolated_layout(fresh, value['format'])
            value['resources'].insert(index, fresh)
            value['pages'][page_index:page_index] = arranged['pages']
            value['placements'][fresh['id']] = arranged['placements'][fresh['id']]
            action = 'added'
        changes.append({'section': section, 'title': title, 'action': action})
    if not value['pages']:
        value['pages'] = new_composition(value['format'])['pages']
    if not value['resources'] and 'composition' not in result:
        return {'plan': result, 'changes': changes}
    result['composition'] = validate_composition(value)
    result['schema_version'] = DESIGN_SCHEMA
    return {'plan': validate_design(result), 'changes': changes}


def refresh_linked_framework(plan):
    sections = [r['framework_link']['section'] for r in plan.get('composition', {}).get('resources', [])
                if r['original']['kind'] == 'lesson_framework' and r['framework_link']['mode'] == 'linked']
    return synchronize_framework(plan, sections)['plan'] if sections else deepcopy(plan)
