"""Explicit handoff of original Word pictures; no inferred chemical classification."""
from copy import deepcopy
from hashlib import sha256
import re

from .desktop_lesson_composition import block, resource, ROLES
from .desktop_composition_images import image_roles, teacher_image
from .desktop_preparation_images import (PreparationImageStore, normalize_image_assets,
    verify_image_bytes, image_info, MAX_IMAGES)
from .desktop_teaching_framework import classroom_text
from .reader_cancellation import check_read_cancelled

SCHEMA = 'shchem.handout-picture.v1'
_SOURCE_FIELDS = {'schema', 'batch_id', 'source_id', 'source_name', 'source_path',
    'source_sha256', 'source_revision', 'asset_id', 'block_index', 'mime_type', 'sha256', 'bytes_count'}
_IMAGE_FIELDS = ('asset_id', 'block_index', 'mime_type', 'sha256', 'bytes_count')
_FORMATS = {'image/png', 'image/jpeg', 'image/webp'}
DERIVATION_SCHEMA = 'shchem.handout-image-derivation.v1'
_DERIVATION_FIELDS = {'schema', 'original_sha256', 'original_mime_type',
    'original_bytes_count', 'preview_sha256', 'preview_mime_type',
    'preview_bytes_count', 'renderer_revision'}


def handout_image_roles(resources, row):
    """Resolve existing audiences through raw-image bindings, including PNG derivatives."""
    row = validate_source(row)
    identities = {'IMG-' + row['sha256']}
    for item in resources:
        original = item['original']; evidence = original['evidence']
        if (original['kind'] == 'handout_image'
                and evidence['handout_source']['sha256'] == row['sha256']):
            identities.add(evidence['image_asset']['asset_id'])
        elif original['kind'] == 'handout_knowledge':
            for native in evidence['section_snapshot']['blocks']:
                identities.update(ref['asset_id'] for ref in native['images']
                    if ref['original_sha256'] == row['sha256'])
    return sorted({role for identity in identities for role in image_roles(resources, identity)})


def validate_source(row):
    if not isinstance(row, dict) or set(row) != _SOURCE_FIELDS or row['schema'] != SCHEMA:
        raise ValueError('讲义原图的来源记录不完整，请重新选图。')
    for key in ('batch_id', 'source_id', 'source_name', 'source_path', 'asset_id', 'mime_type'):
        if not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > 2000:
            raise ValueError('讲义原图的来源位置不完整。')
    for key in ('source_sha256', 'source_revision', 'sha256'):
        if not isinstance(row[key], str) or not re.fullmatch(r'[a-f0-9]{64}', row[key]):
            raise ValueError('讲义原图的来源版本不完整。')
    if type(row['block_index']) is not int or row['block_index'] < 1 or type(row['bytes_count']) is not int or row['bytes_count'] < 1:
        raise ValueError('讲义原图须保留原文区块与原文件大小。')
    return deepcopy(row)


def handout_image_options(facade):
    """Read-only catalog: cancellation creates no preview caches or image copies."""
    rows = []; issues = []; count = 0; contexts = {}
    for batch in facade.list_imported_word_batches():
        check_read_cancelled()
        for source in facade.imported_word_sources(batch.batch_id):
            check_read_cancelled(); count += 1
            try:
                preview = facade.imported_word_preview(batch.batch_id, source['source_id'], read_only=True)
                if preview['source_sha256'] != source['source_sha256']:
                    raise ValueError('讲义版本已变化，请重新读取。')
                path = facade.imported_word_path(batch.batch_id, source['source_id'])
                for asset in preview['assets']:
                    rows.append(validate_source({'schema': SCHEMA, 'batch_id': batch.batch_id,
                        'source_id': source['source_id'], 'source_name': preview['source_name'],
                        'source_path': path, 'source_sha256': preview['source_sha256'],
                        'source_revision': preview['revision'], **{k: asset[k] for k in _IMAGE_FIELDS}}))
                contexts.update(_context_labels(preview, batch.batch_id, source['source_id']))
            except (ValueError, OSError) as exc:
                issues.append({'source_id': source['source_id'], 'source_name': source['source_name'], 'message': str(exc)})
    check_read_cancelled()
    return {'images': rows, 'sources_read': count, 'issues': issues, 'contexts': contexts}


def image_key(row):
    """An occurrence key, independent of editable classroom captions."""
    return json_key(row['batch_id'], row['source_id'], row['asset_id'])


def json_key(batch_id, source_id, asset_id):
    import json
    return json.dumps([batch_id, source_id, asset_id], ensure_ascii=False)


def _context_labels(preview, batch_id, source_id):
    """Display/search excerpts only: no inferred figure titles or chemistry tags."""
    blocks = preview.get('blocks', [])
    valid = [b for b in blocks if isinstance(b, dict) and type(b.get('index')) is int
             and isinstance(b.get('text'), str)]
    result = {}
    for asset in preview['assets']:
        index = asset['block_index']
        nearby = [_excerpt_text(b['text']) for b in valid if index - 3 <= b['index'] <= index + 1]
        nearby = [text for text in nearby if text]
        sections = [s for s in preview.get('sections', []) if isinstance(s, dict)
                    and type(s.get('start')) is int and type(s.get('end')) is int
                    and s['start'] <= index <= s['end'] and isinstance(s.get('title'), str)]
        section = min(sections, key=lambda s: s['end'] - s['start'])['title'] if sections else ''
        result[json_key(batch_id, source_id, asset['asset_id'])] = {
            'section': section, 'excerpt': '\n'.join(nearby)[:600]}
    return result


def _excerpt_text(text):
    """Compact navigation labels, never a substitute for original Word text."""
    # Flattened tables and extractor placeholders are preserved by the reader.
    # They cannot help identify an image in a short navigation list.
    if '【表格开始：' in text: return ''
    text = re.sub(r'【待查看原文：[\s\S]*?】', '', text)
    return ' '.join(text.split())


def same_occurrence(first, second):
    """A restored file path doesn't create a second classroom use of a figure."""
    return all(first.get(key) == second.get(key) for key in _SOURCE_FIELDS - {'source_path'})


def _resolve(facade, row):
    row = validate_source(row); check_read_cancelled()
    preview = facade.imported_word_preview(row['batch_id'], row['source_id'], read_only=True)
    # A backup or local profile copy moves the physical file. The immutable
    # old path remains provenance; the facade reads the current file and checks
    # its bytes. Source version and exact image occurrence are the identity.
    if (preview['source_sha256'] != row['source_sha256'] or preview['revision'] != row['source_revision']
            or preview['source_name'] != row['source_name']):
        raise ValueError('讲义内容或来源版本已变化，请重新选图。')
    matches = [a for a in preview['assets'] if a['asset_id'] == row['asset_id']]
    if len(matches) != 1 or any(matches[0][k] != row[k] for k in _IMAGE_FIELDS):
        raise ValueError('所选讲义原图或原文位置已变化，请重新选图。')
    from .desktop_word_metafile_preview import can_attempt_metafile
    if row['mime_type'] not in _FORMATS and not can_attempt_metafile(row):
        raise ValueError('这张原图请在原Word中查看；可直接加入PNG、JPEG或WebP原图。')
    return preview


def handout_image_context(facade, row):
    """Read the whole, version-bound lecture around a selected original image."""
    preview = _resolve(facade, row)
    images = [validate_source({**row, **{k: asset[k] for k in _IMAGE_FIELDS}})
              for asset in preview['assets']]
    check_read_cancelled()
    return {'source': deepcopy(row), 'preview': deepcopy(preview), 'images': images,
            'contexts': _context_labels(preview, row['batch_id'], row['source_id'])}


def handout_image_preview(facade, row):
    _resolve(facade, row)
    payload = facade.imported_word_asset(row['batch_id'], row['source_id'], row['asset_id'])
    data = payload['bytes']
    info = image_info(data)
    preview_sha = sha256(data).hexdigest()
    derived = row['mime_type'] not in _FORMATS
    revision = payload.get('renderer_revision') if derived else None
    if derived:
        if (payload.get('derived_preview') is not True or info['content_type'] != 'image/png'
                or payload.get('mime_type') != 'image/png'
                or payload.get('original_sha256') != row['sha256']
                or payload.get('original_mime_type') != row['mime_type']
                or payload.get('preview_sha256') != preview_sha
                or not isinstance(revision, str) or not revision.strip() or len(revision) > 256
                or any(ord(c) < 32 for c in revision)):
            raise ValueError('原矢量图与本地显示图片的来源绑定不一致，请重新选图。')
    elif (preview_sha != row['sha256'] or len(data) != row['bytes_count']
            or info['content_type'] != row['mime_type'] or payload.get('derived_preview', False) is not False):
        raise ValueError('讲义原图在读取期间变化，请重新选图。')
    _resolve(facade, row); check_read_cancelled()
    return {'bytes': data, 'info': info, 'source': deepcopy(row),
        'derived_preview': derived, 'renderer_revision': revision,
        'original_sha256': row['sha256'], 'original_mime_type': row['mime_type'],
        'preview_sha256': preview_sha}


def validate_handout_evidence(evidence):
    row = validate_source(evidence['handout_source'])
    asset = normalize_image_assets([evidence['image_asset']])[0]; roles = evidence['source_roles']
    if (not isinstance(roles, list) or any(not isinstance(r, str) or r not in ROLES for r in roles)
            or len(roles) != len(set(roles))):
        raise ValueError('讲义原图须保留已有图片的可见范围。')
    if (evidence['source_path'] != row['source_path'] or evidence['source_sha256'] != row['source_sha256']
            or evidence['source_revision'] != row['source_revision']):
        raise ValueError('讲义来源、原图摘要和本课图片不一致。')
    if 'image_derivation' in evidence:
        from .desktop_word_metafile_preview import can_attempt_metafile
        derived = evidence['image_derivation']
        if (not isinstance(derived, dict) or set(derived) != _DERIVATION_FIELDS
                or derived['schema'] != DERIVATION_SCHEMA or not can_attempt_metafile(row)
                or derived['original_sha256'] != row['sha256']
                or derived['original_mime_type'] != row['mime_type']
                or type(derived['original_bytes_count']) is not int
                or derived['original_bytes_count'] != row['bytes_count']
                or derived['preview_sha256'] != asset['sha256']
                or derived['preview_mime_type'] != 'image/png' or asset['content_type'] != 'image/png'
                or type(derived['preview_bytes_count']) is not int or not 0 < derived['preview_bytes_count'] <= 10 * 1024 * 1024
                or not isinstance(derived['renderer_revision'], str) or not derived['renderer_revision'].strip()
                or len(derived['renderer_revision']) > 256 or any(ord(c) < 32 for c in derived['renderer_revision'])):
            raise ValueError('原矢量图与派生PNG的完整证据不一致。')
    elif (asset['sha256'] != row['sha256'] or row['mime_type'] not in _FORMATS
            or asset['content_type'] != row['mime_type']):
        raise ValueError('讲义来源、原图摘要和本课图片不一致。')
    return row, asset


def validate_handout_resource(value):
    original = value['original']; row, asset = validate_handout_evidence(original['evidence'])
    images = [b for b in original['blocks'] if b['kind'] == 'image']
    if (len(images) != 1 or images[0]['value'] != asset['asset_id']
            or any(b['source_index'] != row['block_index'] or b['role'] != images[0]['role'] for b in original['blocks'])):
        raise ValueError('讲义图须完整绑定所选原图、原文位置和使用范围。')
    if teacher_image(original['evidence']['source_roles']) and images[0]['role'] != 'teacher_note':
        raise ValueError('已有教师答案图片继续仅教师可见。')


def import_handout_image(facade, assets, resources, selection):
    row = validate_source(selection['source']); assets = normalize_image_assets(assets)
    title = classroom_text(selection['title']); purpose = classroom_text(selection['purpose'])
    if not title or len(title) > 160 or len(purpose) > 500 or type(selection['teacher_only']) is not bool:
        raise ValueError('请填写课堂图题；讲解用途最多500字。')
    picture = handout_image_preview(facade, row); identity = 'IMG-' + picture['preview_sha256']
    roles = sorted(set(handout_image_roles(resources, row)) | set(image_roles(resources, identity)))
    role = 'teacher_note' if teacher_image(roles) or selection['teacher_only'] else 'material'
    for old in resources:
        if (old['original']['kind'] == 'handout_image' and same_occurrence(old['original']['evidence']['handout_source'], row)
                and old['original']['blocks'][0]['role'] == role
                and '\n'.join(b['value'] for b in old['original']['blocks'] if b['kind'] == 'text') == purpose):
            # Work-copy titles and explanations can change after selection.
            # The sealed occurrence, use and audience still identify that use.
            return {'resources': [], 'image_assets': deepcopy(assets), 'duplicate': True}
    asset = next((a for a in assets if a['asset_id'] == identity), None)
    if asset is None and len(assets) >= MAX_IMAGES:
        raise ValueError('本课图片已满，请先移除不用的图片。')
    if asset is not None: verify_image_bytes(asset, picture['bytes'])
    check_read_cancelled()
    if asset is None:
        store = PreparationImageStore(facade.paths.task_root / 'preparation-v1' / 'images')
        asset = store.import_bytes(picture['bytes'], caption=title,
            source=row['source_name'] + f" · Word原文区块{row['block_index']}", purpose=purpose or '本课讲义原图')
    rows = [block('image', role, asset['asset_id'], row['block_index'])]
    if purpose: rows.append(block('text', role, purpose, row['block_index']))
    evidence = {'source_path': row['source_path'], 'source_sha256': row['source_sha256'],
        'source_revision': row['source_revision'], 'handout_source': row,
        'image_asset': deepcopy(asset), 'source_roles': roles}
    if picture['derived_preview']:
        evidence['image_derivation'] = {'schema': DERIVATION_SCHEMA,
            'original_sha256': row['sha256'], 'original_mime_type': row['mime_type'],
            'original_bytes_count': row['bytes_count'], 'preview_sha256': picture['preview_sha256'],
            'preview_mime_type': picture['info']['content_type'], 'preview_bytes_count': len(picture['bytes']),
            'renderer_revision': picture['renderer_revision']}
    item = resource('handout_image', row['source_name'] + f" · 原图区块{row['block_index']}", rows, evidence)
    item['title'] = title
    merged = deepcopy(assets)
    if not any(a['asset_id'] == asset['asset_id'] for a in merged): merged.append(deepcopy(asset))
    return {'resources': [item], 'image_assets': merged, 'duplicate': False}
