"""Reviewed projections of local teaching nodes into the same lesson canvas.

Linked cards keep a sealed source snapshot and an editable work copy. Source
edits never replace that copy implicitly. No chemical or publication gate is
opened by importing a teacher's draft or by refreshing its association.
"""
from copy import deepcopy

from .desktop_lesson_composition import block, digest, resource, validate_composition, validate_resource

SNAPSHOT_SCHEMA = 'shchem.lesson-activity-source.v1'


def validate_activity_snapshot(value):
    from .desktop_lesson_design import SCHEMA, validate_design
    from .desktop_preparation_images import normalize_image_assets
    if (not isinstance(value, dict) or set(value) != {'schema', 'design_id', 'node', 'objectives', 'image_assets'}
            or value['schema'] != SNAPSHOT_SCHEMA):
        raise ValueError('教学环节来源快照不完整。')
    # Validate the isolated node without recursing into its containing canvas.
    design = validate_design({'schema_version': SCHEMA, 'id': value['design_id'],
                              'nodes': [value['node']], 'objectives': value['objectives'], 'exports': []})
    assets = normalize_image_assets(value['image_assets'])
    node = design['nodes'][0]
    if (assets != value['image_assets'] or [a['asset_id'] for a in assets] != node['image_ids']
            or [g['id'] for g in design['objectives']] != node['objective_ids']):
        raise ValueError('环节的目标和图片须保留实际关联顺序。')
    return deepcopy(value)


def activity_snapshot(plan, identity, assets):
    from .desktop_lesson_design import validate_design
    from .desktop_preparation_images import normalize_image_assets
    plan = validate_design(plan)
    node = next((n for n in plan['nodes'] if n['id'] == identity), None)
    if node is None:
        raise ValueError('关联教学环节已移除；可以保留原副本或移除画布素材。')
    by_id = {a['asset_id']: a for a in normalize_image_assets(assets)}
    if not set(node['image_ids']) <= set(by_id):
        raise ValueError('教学环节引用的本地图片缺失，请重新关联。')
    goals = {g['id']: g for g in plan['objectives']}
    return validate_activity_snapshot({'schema': SNAPSHOT_SCHEMA, 'design_id': plan['id'], 'node': node,
        'objectives': [goals[g] for g in node['objective_ids']],
        'image_assets': [by_id[i] for i in node['image_ids']]})


def activity_title(snapshot):
    return snapshot['node']['title'] or '未命名教学环节'


def activity_block_specs(snapshot):
    """Scope comes from typed design fields, never from answer keywords."""
    n = snapshot['node']; rows = []
    minutes = '尚未估时' if n['minutes'] is None else f"{n['minutes']}分钟"
    rows.append(('text', 'teacher_note', '环节：' + n['kind'] + '　用时：' + minutes))
    goals = '；'.join(g['text'] for g in snapshot['objectives'])
    if goals: rows.append(('text', 'teacher_note', '教学目标：' + goals))
    scope = 'material' if n['student_material'] else 'teacher_note'
    if n['material_text']: rows.append(('text', scope, '材料：' + n['material_text']))
    for asset in snapshot['image_assets']:
        rows.extend([('image', scope, asset['asset_id']),
                     ('text', scope, asset['caption'] + '　来源：' + asset['source'])])
    for key, caption, role in (
            ('teacher_action', '教师活动', 'teacher_note'), ('student_task', '学生任务', 'student_task'),
            ('expected_output', '预期产出', 'teacher_note'), ('criteria', '评价依据', 'teacher_note'),
            ('teacher_answer', '教师答案', 'answer'), ('notes', '教师备注', 'teacher_note')):
        if n[key]: rows.append(('text', role, caption + '：' + n[key]))
    return rows


def validate_activity_resource(item):
    evidence = item['original']['evidence']; snapshot = evidence['activity_snapshot']
    link = item['activity_link']
    if (not isinstance(link, dict) or set(link) != {'design_id', 'node_id', 'mode'}
            or link['design_id'] != evidence['design_id'] or link['node_id'] != evidence['node_id']
            or link['mode'] not in {'linked', 'snapshot'}):
        raise ValueError('教学环节关联须指向原草稿，或明确保留独立副本。')
    source = item['original']
    actual = [(b['kind'], b['role'], b['value']) for b in source['blocks']]
    if (source['title'] != activity_title(snapshot) or actual != activity_block_specs(snapshot)
            or any(b['source_index'] is not None or 'presentation' in b for b in source['blocks'])):
        raise ValueError('教学环节的原始内容及教师／学生范围必须与来源快照一致。')


def activity_resource(plan, identity, assets):
    snapshot = activity_snapshot(plan, identity, assets)
    return resource('lesson_activity', activity_title(snapshot),
                    [block(k, r, v) for k, r, v in activity_block_specs(snapshot)],
                    {'source_path': '当前本地教学设计草稿', 'source_sha256': 'unknown',
                     'source_revision': digest(snapshot), 'design_id': snapshot['design_id'],
                     'node_id': snapshot['node']['id'], 'activity_snapshot': snapshot})


def activity_status(plan, item, assets):
    validate_resource(item)
    if item['original']['kind'] != 'lesson_activity': return None
    link = item['activity_link']
    if link['mode'] == 'snapshot': return 'snapshot'
    if plan['id'] != link['design_id'] or not any(n['id'] == link['node_id'] for n in plan['nodes']):
        return 'missing'
    current = activity_snapshot(plan, link['node_id'], assets)
    return 'current' if digest(current) == item['original']['evidence']['source_revision'] else 'changed'


def activity_options(plan, assets):
    linked = {r['activity_link']['node_id'] for r in plan.get('composition', {}).get('resources', [])
              if r['original']['kind'] == 'lesson_activity' and r['activity_link']['mode'] == 'linked'
              and r['activity_link']['design_id'] == plan['id']}
    return [{'node_id': n['id'], 'snapshot': activity_snapshot(plan, n['id'], assets)}
            for n in plan['nodes'] if n['id'] not in linked]


def import_activities(plan, identities, assets):
    if not isinstance(identities, list) or not identities or len(identities) != len(set(identities)):
        raise ValueError('请选择尚未加入画布的教学环节。')
    available = {r['node_id'] for r in activity_options(plan, assets)}
    if not set(identities) <= available:
        raise ValueError('教学环节已变化或已经加入；请重新选择。')
    chosen = set(identities)
    return {'resources': [activity_resource(plan, n['id'], assets) for n in plan['nodes'] if n['id'] in chosen]}


def work_copy_changed(item):
    return item['title'] != item['original']['title'] or item['blocks'] != item['original']['blocks']


def refresh_activity(composition, identity, plan, assets, *, expected=None, replace_edited=False):
    value = validate_composition(composition)
    if expected is not None and digest(value) != expected:
        raise ValueError('画布副本已修改，请重新核对关联更新。')
    old = next((r for r in value['resources'] if r['id'] == identity), None)
    if old is None or old['original']['kind'] != 'lesson_activity' or old['activity_link']['mode'] != 'linked':
        raise ValueError('请选择仍与教学设计关联的环节。')
    if plan['id'] != old['activity_link']['design_id']:
        raise ValueError('关联教学设计不同，未替换来源。')
    if work_copy_changed(old) and not replace_edited:
        raise ValueError('现用副本已有编辑；请先核对并明确允许替换，或保留为独立副本。')
    fresh = activity_resource(plan, old['activity_link']['node_id'], assets)
    fresh['id'] = old['id']; fresh['locked'] = old['locked']
    value['resources'][value['resources'].index(old)] = fresh
    # Keep every frame and the user's reading order. New overflow is measured by
    # the UI/exporter, never silently solved by cropping or moving another card.
    return validate_composition(value)


def keep_activity_snapshot(composition, identity):
    value = validate_composition(composition)
    item = next((r for r in value['resources'] if r['id'] == identity), None)
    if item is None or item['original']['kind'] != 'lesson_activity':
        raise ValueError('请选择教学环节素材。')
    item['activity_link']['mode'] = 'snapshot'
    return validate_composition(value)


def checked_activity_links(plan, assets):
    """Block new exports of unresolved stale links; old bundles stay usable."""
    results = []
    for item in plan.get('composition', {}).get('resources', []):
        if item['original']['kind'] != 'lesson_activity': continue
        state = activity_status(plan, item, assets)
        if state in {'changed', 'missing'}:
            raise ValueError('教学环节“' + item['title'] + '”的关联已变化；请在画布更新关联，或明确保留独立副本后再生成。')
        results.append({'resource_id': item['id'], **item['activity_link'], 'status': state,
                        'source_revision': item['original']['evidence']['source_revision']})
    return results
