"""Read-only, version-bound reuse of pictures from saved teacher preparations."""
from copy import deepcopy
import re

from .desktop_lesson_composition import FLAGS, ROLES, TEACHER_ROLES, digest, validate_resource
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes
from .reader_cancellation import check_read_cancelled

_FIELDS = {'draft_id', 'draft_revision', 'payload_sha256', 'source_asset',
           'source_roles', 'source_resources'}


def _load(facade, draft_id, revision):
    check_read_cancelled()
    value = facade.load_preparation_draft(draft_id, revision)
    if (not isinstance(value, dict) or set(value) != {'draft_id', 'revision', 'payload'}
            or value['draft_id'] != draft_id or value['revision'] != revision
            or not isinstance(value['payload'], dict)):
        raise ValueError('备课版本已变化，请重新选择。')
    from .desktop_preparation import normalize_preparation_payload
    normalize_preparation_payload(value['payload'])
    payload = deepcopy(value['payload'])
    assets = normalize_image_assets(payload.get('image_assets', []))
    if assets != payload.get('image_assets', []):
        raise ValueError('原备课的图片记录已变化，请重新选择。')
    plan = payload.get('lesson_design', {})
    # A dangling image reference is unknown scope, never an unreferenced picture.
    known = {a['asset_id'] for a in assets}
    if (any(b['value'] not in known for r in plan.get('composition', {}).get('resources', [])
            for b in r['original']['blocks'] if b['kind'] == 'image')
            or any(not set(n['image_ids']) <= known for n in plan.get('nodes', []))):
        raise ValueError('原备课的图片引用不完整，请重新选择。')
    check_read_cancelled()
    return payload


def _resource_reference(item):
    item = validate_resource(item)
    return {'resource_id': item['id'], 'original_sha256': item['original_sha256'],
            'original': deepcopy(item['original'])}


def _validate_reference(row):
    if not isinstance(row, dict):
        raise ValueError('原备课的图片关联记录不完整。')
    if set(row) == {'node_id', 'snapshot_sha256', 'snapshot'}:
        from .desktop_composition_activities import validate_activity_snapshot
        snapshot = validate_activity_snapshot(row['snapshot'])
        if row['node_id'] != snapshot['node']['id'] or row['snapshot_sha256'] != digest(snapshot):
            raise ValueError('原备课的环节图片关联失配。')
        return deepcopy(row)
    if set(row) != {'resource_id', 'original_sha256', 'original'}:
        raise ValueError('原备课的图片素材快照不完整。')
    original = row['original']
    if not isinstance(original, dict):
        raise ValueError('原备课的图片素材快照不完整。')
    value = {'id': row['resource_id'], 'original': deepcopy(original),
             'original_sha256': row['original_sha256'], 'title': original.get('title'),
             'blocks': deepcopy(original.get('blocks')), 'locked': True, **FLAGS}
    evidence = original.get('evidence', {})
    if original.get('kind') == 'lesson_activity':
        value['activity_link'] = {'design_id': evidence.get('design_id'),
                                 'node_id': evidence.get('node_id'), 'mode': 'snapshot'}
    if original.get('kind') == 'lesson_framework':
        value['framework_link'] = {'design_id': evidence.get('design_id'),
                                  'section': evidence.get('section'), 'mode': 'snapshot'}
    validate_resource(value)
    return deepcopy(row)


def _reference_scope(row, asset):
    """Compare pixels/typed occurrences; source-native IDs are provenance aliases."""
    identity = asset['asset_id']; image_sha = asset['sha256']
    if 'snapshot' in row:
        snapshot = row['snapshot']
        matches = [a for a in snapshot['image_assets'] if a['sha256'] == image_sha]
        if not matches:
            return None
        if any(any(a[k] != asset[k] for k in ('asset_id', 'width', 'height', 'content_type')) for a in matches):
            raise ValueError('原备课的同图记录尺寸或格式失配。')
        return ({'material' if snapshot['node']['student_material'] else 'teacher_note'}, False)
    original = row['original']; evidence = original['evidence']
    matching = [b for b in original['blocks'] if b['kind'] == 'image' and b['value'] == identity]
    metadata = evidence.get('image_asset', {})
    native = evidence.get('handout_source', {})
    saved = original.get('selected_from_preparation', {})
    same_pixels = (metadata.get('sha256') == image_sha or native.get('sha256') == image_sha
                   or saved.get('source_asset', {}).get('sha256') == image_sha)
    if not matching and not same_pixels:
        return None
    roles = {b['role'] for b in matching}
    if metadata.get('sha256') == image_sha and any(metadata[k] != asset[k] for k in ('asset_id', 'width', 'height', 'content_type')):
        raise ValueError('原备课的同图记录尺寸或格式失配。')
    if same_pixels:
        roles.update(evidence.get('source_roles', []))
        roles.update(saved.get('source_roles', []))
    question = original['kind'] == 'complete_question'
    if saved:
        question = question or _binding_scope(saved)[1]
    return roles, question


def _binding_scope(binding):
    roles = set(); question = binding['source_asset']['purpose'].startswith('完整题原图')
    for ref in binding['source_resources']:
        scope = _reference_scope(ref, binding['source_asset'])
        if scope is None:
            raise ValueError('原备课的图片关联指向了其他原图。')
        roles.update(scope[0]); question = question or scope[1]
    return sorted(roles), question


def validate_saved_preparation_binding(binding):
    if (not isinstance(binding, dict) or set(binding) != _FIELDS
            or not isinstance(binding['draft_id'], str) or not binding['draft_id'].strip()
            or len(binding['draft_id']) > 300
            or any(not isinstance(binding[k], str) or not re.fullmatch(r'[a-f0-9]{64}', binding[k])
                   for k in ('draft_revision', 'payload_sha256'))):
        raise ValueError('原备课的版本记录不完整，请重新选图。')
    asset = normalize_image_assets([binding['source_asset']])[0]
    if asset != binding['source_asset']:
        raise ValueError('原备课的图片记录失配。')
    roles = binding['source_roles']; refs = binding['source_resources']
    if (not isinstance(roles, list) or any(not isinstance(r, str) or r not in ROLES for r in roles)
            or roles != sorted(set(roles)) or not isinstance(refs, list) or len(refs) > 160):
        raise ValueError('原备课的图片范围记录不完整。')
    seen = set()
    for ref in refs:
        check_read_cancelled(); _validate_reference(ref)
        key = ('node', ref['node_id']) if 'node_id' in ref else ('resource', ref['resource_id'])
        if key in seen:
            raise ValueError('原备课的图片关联重复。')
        seen.add(key)
    if _binding_scope(binding)[0] != roles:
        raise ValueError('原备课的图片范围与封存素材失配。')
    return deepcopy(binding)


def _references(payload, assets):
    refs = [_resource_reference(r) for r in payload.get('lesson_design', {}).get('composition', {}).get('resources', [])]
    plan = payload.get('lesson_design', {})
    if plan.get('nodes'):
        from .desktop_composition_activities import activity_snapshot
        for node in plan['nodes']:
            check_read_cancelled()
            if node['image_ids']:
                snapshot = activity_snapshot(plan, node['id'], assets)
                refs.append({'node_id': node['id'], 'snapshot_sha256': digest(snapshot), 'snapshot': snapshot})
    return refs


def _binding(draft_id, revision, payload, asset, refs):
    selected = [deepcopy(r) for r in refs if _reference_scope(r, asset) is not None]
    value = {'draft_id': draft_id, 'draft_revision': revision, 'payload_sha256': digest(payload),
             'source_asset': deepcopy(asset), 'source_roles': [], 'source_resources': selected}
    value['source_roles'] = _binding_scope(value)[0]
    return validate_saved_preparation_binding(value)


def saved_preparation_image_options(facade):
    """The current recent saved-course list; cancellation/read errors write nothing."""
    check_read_cancelled()
    result = []
    for option in facade.preparation_draft_options():
        check_read_cancelled()
        try:
            payload = _load(facade, option['draft_id'], option['revision'])
            assets = normalize_image_assets(payload.get('image_assets', []))
            if assets:
                result.append({**deepcopy(option), 'image_count': len(assets)})
        except (ValueError, OSError):
            # Invalid or concurrently changed individual drafts do not become choices.
            continue
    check_read_cancelled()
    return result


def saved_preparation_image_page(facade, *, query='', offset=0, limit=25):
    """Search current courses first, then read only this page's image metadata.

    Empty courses remain visible (and cannot be selected for pictures). This
    keeps pagination tied to the draft search, without loading every course or
    decoding any pixels to discover which courses contain pictures.
    """
    if (not isinstance(query, str) or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= 50):
        raise ValueError('备课图片检索参数不正确。')
    check_read_cancelled()
    page = facade.search_preparation_drafts(query=query, offset=offset, limit=limit, include_hidden=False)
    if (not isinstance(page, dict) or not isinstance(page.get('items'), list)
            or len(page['items']) > limit or type(page.get('total')) is not int
            or page['total'] < 0 or page.get('offset') != offset or page.get('limit') != limit):
        raise ValueError('备课检索结果已变化，请重新搜索。')
    rows = []; unavailable = 0; seen = set()
    for option in page['items']:
        check_read_cancelled()
        if (not isinstance(option, dict) or not isinstance(option.get('draft_id'), str)
                or not option['draft_id'] or option['draft_id'] in seen
                or not isinstance(option.get('revision'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', option['revision'])
                or not isinstance(option.get('title'), str)):
            raise ValueError('备课检索结果不完整，请重新搜索。')
        seen.add(option['draft_id'])
        try:
            payload = _load(facade, option['draft_id'], option['revision'])
        except (ValueError, OSError):
            unavailable += 1
            continue
        rows.append({**deepcopy(option), 'image_count': len(payload.get('image_assets', []))})
    check_read_cancelled()
    return {'items': rows, 'total': page['total'], 'offset': offset, 'limit': limit,
            'has_more': offset + limit < page['total'], 'unavailable_count': unavailable}


def saved_preparation_images(facade, draft_id, expected_revision):
    """Compatibility full-pixel validation of every picture in one saved course."""
    return _saved_preparation_images(facade, draft_id, expected_revision, verify_pixels=True)


def saved_preparation_image_catalog(facade, draft_id, expected_revision):
    """Version-bound metadata for lazy visible thumbnails and selected previews.

    Pixel checks happen when the UI reads a visible picture and again at the
    explicit import boundary. All source references and role restrictions are
    sealed here, including aliases of the same pixels.
    """
    return _saved_preparation_images(facade, draft_id, expected_revision, verify_pixels=False)


def _saved_preparation_images(facade, draft_id, expected_revision, *, verify_pixels):
    from .desktop_composition_images import image_options
    payload = _load(facade, draft_id, expected_revision)
    assets = normalize_image_assets(payload.get('image_assets', [])); refs = _references(payload, assets)
    title = payload['topic']
    option_reader = getattr(facade, 'preparation_draft_option', None)
    if callable(option_reader):
        option = option_reader(draft_id)
        if option['revision'] != expected_revision:
            raise ValueError('备课版本已变化，请重新选择。')
        title = option['title']
    result = []
    for row in image_options(assets, payload.get('lesson_design', {}).get('composition', {}).get('resources', [])):
        check_read_cancelled()
        binding = _binding(draft_id, expected_revision, payload, row['asset'], refs)
        roles, question = _binding_scope(binding)
        if verify_pixels:
            verify_image_bytes(row['asset'], facade.preparation_image_bytes(row['asset']))
        row.update(source_roles=roles, teacher_only=bool(set(roles) & TEACHER_ROLES),
                   via_question=question, saved_title=title, saved_preparation=binding)
        result.append(row)
    if digest(_load(facade, draft_id, expected_revision)) != digest(payload):
        raise ValueError('备课内容在读取图片后变化，请重新选择。')
    check_read_cancelled()
    return result


def resolve_saved_preparation_image(facade, binding):
    binding = validate_saved_preparation_binding(binding)
    payload = _load(facade, binding['draft_id'], binding['draft_revision'])
    if digest(payload) != binding['payload_sha256']:
        raise ValueError('原备课的内容已变化，请重新选图。')
    assets = normalize_image_assets(payload.get('image_assets', []))
    asset = next((a for a in assets if a['asset_id'] == binding['source_asset']['asset_id']), None)
    if asset != binding['source_asset']:
        raise ValueError('原备课的图片记录已变化，请重新选图。')
    current = _binding(binding['draft_id'], binding['draft_revision'], payload, asset, _references(payload, assets))
    if current != binding:
        raise ValueError('原备课的图片关联或范围已变化，请重新选图。')
    check_read_cancelled()
    return asset


def target_image_scope(resources, asset):
    roles = set(); question = asset['purpose'].startswith('完整题原图')
    for item in resources:
        check_read_cancelled()
        scope = _reference_scope(_resource_reference(item), asset)
        if scope is not None:
            roles.update(scope[0]); question = question or scope[1]
    return sorted(roles), question


def validate_saved_preparation_resource(original):
    if original['kind'] != 'preparation_image':
        raise ValueError('备课选图来源只能绑定教学图片。')
    binding = validate_saved_preparation_binding(original['selected_from_preparation'])
    asset = normalize_image_assets([original['evidence']['image_asset']])[0]
    roles, question = _binding_scope(binding)
    if (question
            or any(asset[k] != binding['source_asset'][k] for k in ('asset_id', 'sha256', 'width', 'height', 'content_type'))
            or not set(roles) <= set(original['evidence']['source_roles'])):
        raise ValueError('复用图片须保留原图及完整题的原有范围。')
    if set(original['evidence']['source_roles']) & TEACHER_ROLES and any(b['role'] != 'teacher_note' for b in original['blocks']):
        raise ValueError('原备课教师图片继续仅教师可见。')


def bind_saved_preparation_selection(item, binding):
    item = validate_resource(item)
    item['original']['selected_from_preparation'] = validate_saved_preparation_binding(binding)
    item['original_sha256'] = digest(item['original'])
    return validate_resource(item)
