"""Explicit local locations for unchanged textbook originals.

Only the current registered source can become a location. These records do
not rewrite a crop, Original, source identity, review state or source revision.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .desktop_textbook_preparation_crops import _safe_state_path
from .reader_cancellation import check_read_cancelled, publication_guard

SCHEMA = 'shchem.textbook-source-location.v1'
MAX_BYTES = 16 * 1024
_LOCK = RLock()
_FLAGS = {'candidate_only': True, 'human_reviewed': False, 'retrieval_ready': False,
          'generation_allowed': False, 'publication_allowed': False}


class TextbookSourceRelocationRequired(ValueError):
    """The registered identity is unchanged, but its physical location differs."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('原书位置记录包含重复字段，请重新核对。')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('原书位置记录含无效数字，请重新核对。')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def location_descriptor(origin, original_path):
    kind = origin.get('source_kind', 'registered_textbook_page')
    if kind not in {'registered_textbook_page', 'personal_textbook_page',
                    'knowledge_note_page', 'textbook_concept_page', 'section_textbook_page'}:
        raise ValueError('原书来源类型无法核对。')
    sha = origin.get('source_sha256')
    if not isinstance(sha, str) or re.fullmatch('[a-f0-9]{64}', sha) is None:
        raise ValueError('原书内容身份不完整。')
    identity = {'source_kind': kind, 'source_sha256': sha, 'volume_id': origin.get('volume_id')}
    if kind == 'personal_textbook_page':
        identity['source_id'] = origin.get('source_id')
        if identity['source_id'] != 'BOOK-' + sha or identity['volume_id'] is not None:
            raise ValueError('个人教材身份无法核对。')
    elif not isinstance(identity['volume_id'], str) or not identity['volume_id']:
        raise ValueError('登记教材身份不完整。')
    if not isinstance(original_path, str) or not original_path.strip():
        raise ValueError('教材封存位置不完整。')
    return {'source_identity': identity, 'original_source_path': str(Path(original_path).absolute())}


def _binding_id(descriptor):
    return hashlib.sha256(_json(descriptor)).hexdigest()


def location_path(task_root, descriptor):
    return Path(task_root).absolute() / 'preparation-v1' / 'textbook-source-locations' / (_binding_id(descriptor)+'.json')


def location_snapshot(task_root, descriptor):
    path = location_path(task_root, descriptor)
    _safe_state_path(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('原书位置记录无法读取，请重新核对。')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('原书位置记录超出读取范围。')
    value = _strict_json(raw)
    keys = {'schema_version','binding_id','source_identity','original_source_path',
            'current_source_path','created_at_utc', *_FLAGS}
    if (not isinstance(value, dict) or set(value) != keys or value['schema_version'] != SCHEMA
            or value['binding_id'] != _binding_id(descriptor)
            or any(value.get(key) != item for key,item in descriptor.items())
            or any(value.get(key) is not item for key,item in _FLAGS.items())
            or not isinstance(value['current_source_path'],str)
            or not Path(value['current_source_path']).is_absolute()
            or str(Path(value['current_source_path']).absolute()) != value['current_source_path']):
        raise ValueError('原书位置记录与封存身份不一致，请重新核对。')
    try:
        created = datetime.fromisoformat(value['created_at_utc'])
    except (ValueError,TypeError) as exc:
        raise ValueError('原书位置记录的时间无法核对。') from exc
    if created.tzinfo is None or created.utcoffset().total_seconds() != 0:
        raise ValueError('原书位置记录的时间不完整。')
    _safe_state_path(Path(value['current_source_path']))
    return {'record':value, 'raw':raw, 'revision':hashlib.sha256(raw).hexdigest()}


def _candidate(facade, item, image_assets):
    from .desktop_textbook_crop_reading import _reader
    context = _reader(facade,item,image_assets, allow_current_location=True, include_proof=True)
    candidate = context.get('_relocation_candidate')
    if not isinstance(candidate,dict):
        raise ValueError('当前原书位置已经可用，请重新打开原页。')
    value = {'old_path':candidate['descriptor']['original_source_path'],
             'current_path':candidate['current_path'], 'title':context['source']['volume_title'],
             'source_identity':deepcopy(candidate['descriptor']['source_identity']),
             'source_sha256':context['source']['source_sha256']}
    value['preview_id'] = hashlib.sha256(_json({'binding':context['binding'],
        'candidate':candidate, 'old_path':value['old_path'], 'current_path':value['current_path']})).hexdigest()
    return value,context


def preview_textbook_source_relocation(facade,item,image_assets):
    """Verify current registered original and crop pixels without writing."""
    return _candidate(facade,item,image_assets)[0]


class _LocationPublication:
    def __init__(self,facade,item,assets,preview,context):
        self.facade,self.item,self.assets = facade,deepcopy(item),deepcopy(assets)
        self._input_item,self._input_assets = item,assets
        self.preview,self.context = deepcopy(preview),context
        self.published = False

    def publish(self):
        if self.published:
            raise ValueError('这次原书位置关联已完成，请重新打开原页。')
        from .desktop_textbook_crop_reading import _recheck_relocation_proof
        candidate = self.context['_relocation_candidate']
        descriptor = candidate['descriptor']
        root = self.facade.paths.task_root
        path = location_path(root,descriptor)
        check_read_cancelled()
        with _LOCK, publication_guard():
            from .desktop_lesson_composition import validate_resource
            from .desktop_preparation_images import normalize_image_assets
            proof = self.context['_relocation_proof']
            if (validate_resource(self._input_item) != proof['card']
                    or normalize_image_assets(self._input_assets) != proof['assets']):
                raise ValueError('关联时使用的课堂素材在确认期间变化，请重新关联。')
            _recheck_relocation_proof(self.facade,self.item,self.assets,self.context)
            before = location_snapshot(root,descriptor)
            revision = before['revision'] if before else None
            if revision != candidate['previous_location_revision']:
                raise ValueError('原书位置记录在确认期间变化，请重新关联。')
            value = {'schema_version':SCHEMA,'binding_id':_binding_id(descriptor),**deepcopy(descriptor),
                'current_source_path':candidate['current_path'],
                'created_at_utc':datetime.now(timezone.utc).isoformat(),**_FLAGS}
            raw = _json(value)
            _safe_state_path(path)
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary = path.with_name('.'+uuid4().hex+'.tmp')
            try:
                _safe_state_path(temporary)
                with temporary.open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                _safe_state_path(path)
                latest = location_snapshot(root,descriptor)
                if (latest['revision'] if latest else None) != revision:
                    raise ValueError('原书位置记录在确认期间变化，请重新关联。')
                check_read_cancelled()
                os.replace(temporary,path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self.published = True
        return {'linked':True,'binding_id':value['binding_id'],'location_revision':hashlib.sha256(raw).hexdigest(),
                'current_path':candidate['current_path'],'original_unchanged':True}


def stage_textbook_source_relocation(facade,item,image_assets,*,expected_preview):
    """Reverify before confirmation; return an unpublished small-file transaction."""
    preview,context = _candidate(facade,item,image_assets)
    if expected_preview != preview['preview_id']:
        raise ValueError('教材原书、裁片或位置在确认期间变化，请重新关联。')
    return _LocationPublication(facade,item,image_assets,preview,context)
