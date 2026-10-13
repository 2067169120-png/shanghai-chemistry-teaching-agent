"""Read a lesson crop's original PDF through its sealed source and receipt.

This port never creates an image store, writes a receipt, or changes source
qualifications. Image hashes identify bytes, while source identity and page
are checked separately so identical pictures cannot select another book.
"""
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re

from .desktop_lesson_composition import digest, validate_resource
from .desktop_preparation_images import normalize_image_assets, verify_image_bytes
from .desktop_textbook_preparation_crops import (
    SCHEMA, TextbookPreparationCropService, _bounds, _json, _render_page,
    _safe_state_path,
)
from .reader_cancellation import check_read_cancelled


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _receipt_bytes(path):
    _safe_state_path(path)
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError('教材裁选记录缺失或无法读取，请重新关联原教材。')
    with path.open('rb') as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError('教材裁选记录超出读取范围，请重新关联原教材。')
    return raw


def _read_receipt(path):
    raw = _receipt_bytes(path)
    def pairs(rows):
        result = {}
        for key,value in rows:
            if key in result:
                raise ValueError('教材裁选记录包含重复字段，请重新关联原教材。')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('教材裁选记录含无效数字，请重新关联原教材。')
    value = json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    if not isinstance(value, dict) or not isinstance(value.get('created_at_utc'), str):
        raise ValueError('教材裁选记录不完整，请重新关联原教材。')
    record = {key: row for key, row in value.items() if key != 'created_at_utc'}
    asset = normalize_image_assets([record.get('image_asset')])[0]
    provenance = {key: row for key, row in record.items() if key not in {'crop_id', 'image_asset'}}
    if (record.get('schema_version') != SCHEMA or not _sha(record.get('crop_id'))
            or path.name != record['crop_id'] + '.json'
            or hashlib.sha256(_json({'provenance': provenance, 'caption': asset['caption'],
                                    'purpose': asset['purpose']})).hexdigest() != record['crop_id']
            or record.get('image_sha256') != asset['sha256']
            or record.get('candidate_only') is not True
            or any(record.get(key) is not False for key in (
                'human_reviewed', 'retrieval_ready', 'generation_allowed', 'publication_allowed'))):
        raise ValueError('教材裁选记录与原裁片绑定不一致，请重新关联原教材。')
    return record, raw


def _origin(record):
    keys = ('visual_asset_id', 'revision', 'volume_id', 'source_title',
            'source_sha256', 'pdf_page', 'printed_page')
    result = {key: record[key] for key in keys}
    if record.get('source_kind') == 'section_textbook_page':
        from .desktop_section_page_crops import ORIGIN_FIELDS
        result.update({key: record[key] for key in ORIGIN_FIELDS})
    elif record.get('source_kind') == 'personal_textbook_page':
        result.update({key: record[key] for key in ('source_kind', 'source_id')})
    elif record.get('source_kind') == 'knowledge_note_page':
        result.update({key: record[key] for key in ('source_kind', 'record_id', 'section_key',
                                                   'note_revision', 'directory_revision')})
    elif record.get('source_kind') == 'textbook_concept_page':
        result.update({key: record[key] for key in ('source_kind', 'record_id', 'concept_revision',
                                                   'directory_revision', 'concept_location')})
    elif 'source_kind' in record:
        raise ValueError('教材裁选来源类型无法核对，请重新关联原教材。')
    return result


def _source_path(facade, source, personal_books):
    if source.get('source_kind') == 'personal_textbook_page':
        return personal_books.root / (source['source_sha256'] + '.pdf')
    from .desktop_textbook_bookshelf import TextbookBookshelfService
    registry, _revision, _parents, _sections = TextbookBookshelfService(
        facade.paths.workspace_root)._directory()
    volumes = [row for row in registry['volumes'] if row['volume_id'] == source['volume_id']
               and row['source_sha256'] == source['source_sha256']]
    if len(volumes) != 1:
        raise ValueError('教材原书身份已变化，请重新关联原教材。')
    return Path(facade.paths.workspace_root) / volumes[0]['source_path']


def _image_bytes(task_root, asset):
    # PreparationImageStore.__init__ creates directories even for a read.
    path = task_root / 'preparation-v1' / 'images' / (asset['sha256'] + '.image')
    _safe_state_path(path)
    if not path.is_file():
        raise ValueError('课件教材裁片的本地原图缺失，请恢复图片后重试。')
    from .desktop_preparation_images import MAX_IMAGE_BYTES
    with path.open('rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return verify_image_bytes(asset, data)


def _reader(facade, item, image_assets, *, allow_current_location=False, include_proof=False):
    check_read_cancelled()
    card = deepcopy(validate_resource(item))
    if card['original']['kind'] != 'textbook_image':
        raise ValueError('请先选择一张教材裁片。')
    evidence = card['original']['evidence']
    identities = [row['value'] for row in card['original']['blocks'] if row['kind'] == 'image']
    if (len(identities) != 1 or len(evidence['pdf_pages']) != 1
            or [row['value'] for row in card['blocks'] if row['kind'] == 'image'] != identities):
        raise ValueError('教材裁片与单个原页的绑定不完整，请重新关联原教材。')
    assets = normalize_image_assets(image_assets)
    asset = next((row for row in assets if row['asset_id'] == identities[0]), None)
    if asset is None:
        raise ValueError('课件教材裁片的图片记录已移除，请恢复图片后重试。')
    task_root = Path(facade.paths.task_root).absolute()
    data = _image_bytes(task_root, asset)
    folder = task_root / 'preparation-v1' / 'textbook-crops'
    _safe_state_path(folder)
    if not folder.is_dir():
        raise ValueError('教材裁选记录缺失，请重新关联原教材。')
    matches = []
    for path in sorted(folder.glob('*.json')):
        check_read_cancelled()
        # Unrelated receipts do not choose this resource and cannot substitute
        # their book identity merely because they contain the same image bytes.
        if not re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
            continue
        try:
            candidate = json.loads(_receipt_bytes(path).decode('utf-8'))
        except (ValueError, OSError, UnicodeError):
            continue
        if not isinstance(candidate, dict):continue
        record = candidate
        printed = ([record['printed_page']] if type(record.get('printed_page')) is int else 'unknown')
        if (record.get('image_sha256') == asset['sha256']
                and record.get('source_sha256') == evidence['source_sha256']
                and record.get('revision') == evidence['source_revision']
                and type(record.get('pdf_page')) is int
                and [record['pdf_page']] == evidence['pdf_pages']
                and printed == evidence['printed_pages']
                and ('crop_id' not in evidence or record.get('crop_id') == evidence['crop_id'])):
            record, raw = _read_receipt(path)
            matches.append((path, record, raw))
    if len(matches) != 1:
        raise ValueError('教材裁片的原书、页码或裁选版本缺失、变化或身份重复，请重新关联原教材。')
    path, record, raw = matches[0]
    original = _origin(record)
    recipe = record.get('recipe')
    if (not isinstance(recipe, dict) or set(recipe) != {'renderer', 'scale', 'width', 'height', 'raster_sha256'}
            or recipe['renderer'] != 'pypdfium2.page.render.rgb.v1'
            or not _sha(recipe['raster_sha256'])
            or type(recipe['scale']) not in (int,float) or not math.isfinite(recipe['scale']) or recipe['scale'] <= 0
            or any(type(recipe[key]) is not int or recipe[key] <= 0 for key in ('width','height'))
            or record.get('page_revision') != hashlib.sha256(
                _json({'origin': original, 'recipe': recipe})).hexdigest()):
        raise ValueError('教材裁图的原页显示版本无法核对，请重新关联原教材。')
    personal = original.get('source_kind') == 'personal_textbook_page'
    books = facade.textbook_workspace() if personal else None
    service = TextbookPreparationCropService(facade.paths.workspace_root, task_root, personal_books=books)
    selection = {key: original[key] for key in ('visual_asset_id', 'revision')}
    source = (service._source(selection, expected_origin=original,
        original_source_path=evidence['source_path']) if original.get('source_kind') == 'section_textbook_page'
        else service._source(selection))
    if any(source.get(key) != row for key, row in original.items()):
        raise ValueError('教材原书、目录版本或页码已变化，请重新关联原教材。')
    pdf_bytes = source['pdf_bytes']
    if hashlib.sha256(pdf_bytes).hexdigest() != evidence['source_sha256']:
        raise ValueError('教材原文件内容已变化，请重新关联原教材。')
    actual_path = _source_path(facade,source,books).absolute()
    _safe_state_path(actual_path)
    from .desktop_textbook_source_locations import (
        TextbookSourceRelocationRequired,location_descriptor,location_snapshot,
    )
    descriptor = location_descriptor(original,evidence['source_path'])
    relocated = Path(evidence['source_path']).absolute() != actual_path
    location = location_snapshot(task_root,descriptor) if relocated else None
    if relocated and not allow_current_location:
        if location is None:
            raise TextbookSourceRelocationRequired('教材原书位置与课堂保存位置不同，请确认关联当前原书位置。')
        if Path(location['record']['current_source_path']).absolute() != actual_path:
            raise TextbookSourceRelocationRequired('教材原书位置再次变化，请确认关联当前原书位置。')
    raster = _render_page(pdf_bytes, original['pdf_page'])
    if (raster['width'] != recipe['width'] or raster['height'] != recipe['height']
            or type(recipe['scale']) not in (int, float)
            or abs(raster['scale'] - recipe['scale']) > 1e-9
            or hashlib.sha256(raster['page_data']).hexdigest() != recipe['raster_sha256']):
        raise ValueError('教材裁图的显示尺寸或渲染版本已变化，请重新关联原教材。')
    edges = _bounds(record.get('bounds'), raster['width'], raster['height'])
    from PIL import Image
    with Image.open(io.BytesIO(raster['page_data'])) as page, Image.open(io.BytesIO(data)) as crop:
        with page.crop(edges) as rebuilt:
            if (crop.format != 'PNG' or rebuilt.size != crop.size
                    or rebuilt.convert('RGBA').tobytes() != crop.convert('RGBA').tobytes()):
                raise ValueError('教材裁片像素与绑定的原页不一致，请重新关联原教材。')
    # PNG serialization can differ without a pixel change. The saved asset SHA
    # and receipt SHA remain exact; reconstruction compares decoded RGB pixels.
    check_read_cancelled()
    latest = (service._source(selection, expected_origin=original,
        original_source_path=evidence['source_path']) if original.get('source_kind') == 'section_textbook_page'
        else service._source(selection))
    if (any(latest.get(key) != row for key, row in original.items())
            or actual_path != _source_path(facade, latest, books).absolute()
            or latest['pdf_bytes'] != pdf_bytes or _read_receipt(path)[1] != raw
            or _image_bytes(task_root, asset) != data or validate_resource(item) != card
            or normalize_image_assets(image_assets) != assets):
        raise ValueError('教材原页或裁片在读取期间变化，请重新打开原页。')
    if relocated:
        latest_location = location_snapshot(task_root,descriptor)
        if (latest_location['revision'] if latest_location else None) != (location['revision'] if location else None):
            raise ValueError('教材原书位置在读取期间变化，请重新打开原页。')
    check_read_cancelled()
    binding = {'resource_id': card['id'], 'original_sha256': card['original_sha256'],
               'source': original, 'crop_id': record['crop_id'],
               'receipt_sha256': hashlib.sha256(raw).hexdigest(), 'image_sha256': asset['sha256']}
    if relocated and location is not None and not allow_current_location:
        binding['source_location'] = {'revision':location['revision'],'current_path':str(actual_path)}
    result = {'selection': selection, 'reading_mode': 'asset', 'binding_sha256': digest(binding),
            'binding': binding, 'crop_pixels_verified': True,
            'source': {**deepcopy(source), 'reading_mode': 'asset',
                'pdf_pages': [original['pdf_page']], 'title': card['original']['title'],
                'statement': '课件所用教材裁片对应的完整原页。',
                'volume_title': original['source_title'], 'source_name': original['source_title'],
                'visual_assets': {'assets': [], 'notices': []},
                'reading_hints': {'notes': [], 'notices': []}}}
    if relocated and allow_current_location:
        result['_relocation_candidate'] = {'descriptor':descriptor,'current_path':str(actual_path),
            'previous_location_revision':location['revision'] if location else None}
    if include_proof:
        result['_relocation_proof'] = {'original':original,'receipt_path':path,'receipt_raw':raw,
            'asset':asset,'image_bytes':data,'card':card,'assets':assets,'actual_path':actual_path,
            'pdf_bytes':pdf_bytes}
    return result


def _recheck_relocation_proof(facade,item,image_assets,context):
    """Final source/metadata recheck without rerendering a page on the UI thread."""
    proof = context['_relocation_proof']
    original = proof['original']
    books = facade.textbook_workspace() if original.get('source_kind') == 'personal_textbook_page' else None
    service = TextbookPreparationCropService(facade.paths.workspace_root,facade.paths.task_root,personal_books=books)
    latest = (service._source(context['selection'], expected_origin=original,
        original_source_path=proof['card']['original']['evidence']['source_path']) if original.get('source_kind') == 'section_textbook_page'
        else service._source(context['selection']))
    _safe_state_path(proof['actual_path'])
    if (any(latest.get(key) != row for key,row in original.items())
            or _source_path(facade,latest,books).absolute() != proof['actual_path']
            or latest['pdf_bytes'] != proof['pdf_bytes']
            or _read_receipt(proof['receipt_path'])[1] != proof['receipt_raw']
            or _image_bytes(Path(facade.paths.task_root),proof['asset']) != proof['image_bytes']
            or validate_resource(item) != proof['card']
            or normalize_image_assets(image_assets) != proof['assets']):
        raise ValueError('教材原书、裁片或来源在确认期间变化，请重新关联。')
    check_read_cancelled()


def textbook_crop_reader(facade, item, image_assets, *, expected_binding=None):
    """Return hash-checked original PDF bytes and an identity-bound viewer scope."""
    try:
        result = _reader(facade, item, image_assets)
        if expected_binding is not None and result['binding_sha256'] != expected_binding:
            raise ValueError('教材裁片来源在阅读期间变化，请重新打开原页。')
        return result
    except (KeyError, TypeError, StopIteration, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('教材原页或裁选记录无法核对，请恢复来源后重试。') from exc
