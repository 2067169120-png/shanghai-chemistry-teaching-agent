"""Explicit local teaching-picture handoff with sealed pixels and source roles."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .desktop_lesson_composition import block, resource, digest, TEACHER_ROLES, ROLES
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes, image_info, MAX_IMAGE_BYTES, MAX_IMAGES
from .desktop_teaching_framework import classroom_text


def image_roles(resources, identity):
    return sorted({b['role'] for r in resources for b in r['original']['blocks']
                   if b['kind'] == 'image' and b['value'] == identity})


def teacher_image(roles):
    return bool(roles) and all(role in TEACHER_ROLES for role in roles)


def image_options(assets, resources):
    result = []
    for asset in normalize_image_assets(assets):
        roles = image_roles(resources, asset['asset_id'])
        title = classroom_text(asset['caption']) or '教学图片'
        purpose = classroom_text(asset['purpose'])
        if ' · 区块' in title or purpose.startswith('完整题原图'):
            title = '教师讲解图' if teacher_image(roles) else '题目插图'
            purpose = ''
        result.append({'asset': deepcopy(asset), 'source_roles': roles,
                       'teacher_only': teacher_image(roles), 'title': title, 'purpose': purpose,
                       'via_question': not roles and asset['purpose'].startswith('完整题原图')})
    return result


def read_local_image(path):
    path = Path(path)
    if str(path).startswith(('\\\\', '//')) or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('请选择不超过10MB的本机PNG、JPEG或WebP图片。')
    with path.open('rb') as stream: data = stream.read(MAX_IMAGE_BYTES+1)
    image_info(data)
    return data


def local_image_request(path, caption, source, purpose):
    data = read_local_image(path)
    return {'file_path': str(path), 'sha256': sha256(data).hexdigest(),
            'caption': caption, 'source': source, 'purpose': purpose}


def validate_image_evidence(evidence):
    asset = normalize_image_assets([evidence['image_asset']])[0]
    roles = evidence['source_roles']
    if (not isinstance(roles, list) or any(not isinstance(role, str) or role not in ROLES for role in roles)
            or len(roles) != len(set(roles))):
        raise ValueError('图片须保留原素材的可见范围。')
    if (evidence['source_sha256'] != asset['sha256'] or evidence['source_revision'] != digest(asset)
            or evidence['source_path'] != asset['source']):
        raise ValueError('图片的来源记录、版本和原图摘要不一致。')
    return asset


def validate_image_resource(value):
    original = value['original']; asset = validate_image_evidence(original['evidence'])
    rows = original['blocks']; images = [r for r in rows if r['kind'] == 'image']
    if len(images) != 1 or images[0]['value'] != asset['asset_id']:
        raise ValueError('教学图片须绑定所选原图。')
    roles = original['evidence']['source_roles']
    if teacher_image(roles) and any(r['role'] != 'teacher_note' for r in rows):
        raise ValueError('原教师答案图片继续仅教师可见。')
    if any(r['role'] != images[0]['role'] for r in rows):
        raise ValueError('图片与本次用途须保持同一可见范围。')


def import_image_result(facade, assets, resources, selection):
    """Read/import only after the picker accepts; preserve prior metadata."""
    if isinstance(selection, dict) and 'saved_preparation' in selection:
        return _import_saved_image_result(facade, assets, resources, selection)
    assets = normalize_image_assets(assets)
    if 'local' in selection:
        request = selection['local']
        if sha256(read_local_image(request['file_path'])).hexdigest() != request['sha256']:
            raise ValueError('所选图片在预览后变化，请重新选择。')
        if len(assets) >= MAX_IMAGES and not any(a['sha256'] == request['sha256'] for a in assets):
            raise ValueError('本课图片已满，请先移除不用的图片。')
        asset = normalize_image_assets([facade.import_preparation_image(request['file_path'],
            request['caption'], request['source'], request['purpose'])])[0]
        if asset['sha256'] != request['sha256']:
            raise ValueError('所选图片在导入时变化，请重新预览。')
        # Shared bytes retain the existing metadata; this use has its own title/task.
        asset = next((a for a in assets if a['asset_id'] == asset['asset_id']), asset)
    else:
        snapshot = normalize_image_assets([selection['asset']])[0]
        asset = next((a for a in assets if a['asset_id'] == snapshot['asset_id']), None)
        if asset != snapshot: raise ValueError('所选图片记录已变化，请重新选择。')
        if image_roles(resources, asset['asset_id']) != selection['source_roles']:
            raise ValueError('图片原素材范围已变化，请重新选择。')
    verify_image_bytes(asset, facade.preparation_image_bytes(asset))
    roles = image_roles(resources, asset['asset_id'])
    if not roles and asset['purpose'].startswith('完整题原图'):
        raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
    role = 'teacher_note' if teacher_image(roles) or selection['teacher_only'] else 'material'
    title = classroom_text(selection['title']); purpose = classroom_text(selection['purpose'])
    if not title or len(title) > 160 or len(purpose) > 500:
        raise ValueError('请填写不超过160字的图题与不超过500字的课堂用途。')
    signature = (asset['asset_id'], role, title, purpose)
    for existing in resources:
        if existing['original']['kind'] != 'preparation_image': continue
        rows = existing['blocks']
        if (next(b['value'] for b in rows if b['kind'] == 'image'), rows[0]['role'],
            existing['title'], '\n'.join(b['value'] for b in rows if b['kind'] == 'text')) == signature:
            return {'resources': [], 'image_assets': deepcopy(assets), 'duplicate': True}
    rows = [block('image', role, asset['asset_id'])]
    if purpose: rows.append(block('text', role, purpose))
    evidence = {'source_path': asset['source'], 'source_sha256': asset['sha256'],
                'source_revision': digest(asset), 'image_asset': deepcopy(asset), 'source_roles': roles}
    item = resource('preparation_image', asset['caption'], rows, evidence); item['title'] = title
    merged = deepcopy(assets)
    if not any(a['asset_id'] == asset['asset_id'] for a in merged): merged.append(deepcopy(asset))
    normalize_image_assets(merged)
    return {'resources': [item], 'image_assets': merged, 'duplicate': False}


def _import_saved_image_result(facade, assets, resources, selection):
    from .desktop_saved_preparation_images import (validate_saved_preparation_binding,
        resolve_saved_preparation_image, target_image_scope, bind_saved_preparation_selection,
        _binding_scope)
    from .reader_cancellation import check_read_cancelled
    check_read_cancelled()
    if (set(selection) != {'saved_preparation', 'title', 'purpose', 'teacher_only'}
            or type(selection['teacher_only']) is not bool):
        raise ValueError('请重新选择备课图片和课堂用途。')
    title = classroom_text(selection['title']); purpose = classroom_text(selection['purpose'])
    if not title or len(title) > 160 or len(purpose) > 500:
        raise ValueError('请填写不超过160字的图题与不超过500字的课堂用途。')
    binding = validate_saved_preparation_binding(selection['saved_preparation'])
    source = resolve_saved_preparation_image(facade, binding)
    source_roles, question = _binding_scope(binding)
    if question:
        raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
    assets = normalize_image_assets(assets)
    asset = next((a for a in assets if a['sha256'] == source['sha256']), source)
    roles, target_question = target_image_scope(resources, asset)
    if target_question:
        raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
    roles = sorted(set(roles) | set(source_roles))
    role = 'teacher_note' if set(roles) & TEACHER_ROLES or selection['teacher_only'] else 'material'
    merged = deepcopy(assets)
    if not any(a['asset_id'] == asset['asset_id'] for a in merged):
        merged.append(deepcopy(asset))
    normalize_image_assets(merged)
    data = facade.preparation_image_bytes(source)
    verify_image_bytes(source, data); verify_image_bytes(asset, data)
    check_read_cancelled()
    duplicate = any(old['original']['kind'] == 'preparation_image'
        and (next(b['value'] for b in old['blocks'] if b['kind'] == 'image'), old['blocks'][0]['role'],
             old['title'], '\n'.join(b['value'] for b in old['blocks'] if b['kind'] == 'text'))
            == (asset['asset_id'], role, title, purpose) for old in resources)
    items = []
    if not duplicate:
        rows = [block('image', role, asset['asset_id'])]
        if purpose: rows.append(block('text', role, purpose))
        evidence = {'source_path': asset['source'], 'source_sha256': asset['sha256'],
                    'source_revision': digest(asset), 'image_asset': deepcopy(asset), 'source_roles': roles}
        item = resource('preparation_image', asset['caption'], rows, evidence); item['title'] = title
        items.append(bind_saved_preparation_selection(item, binding))
    # Nothing is copied or published: even a late Stop or source edit returns no handoff.
    verify_image_bytes(source, facade.preparation_image_bytes(source))
    resolve_saved_preparation_image(facade, binding)
    check_read_cancelled()
    return {'resources': items, 'image_assets': merged, 'duplicate': duplicate}
