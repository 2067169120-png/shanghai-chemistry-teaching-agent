"""Read-only mixed image preparation and one owned publication for a lesson.

The queue describes classroom uses, not unique PDF pages or unique pixel files.
Previewing never constructs a target image store or saves a crop receipt.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from uuid import uuid4

from .desktop_composition_images import image_roles, read_local_image
from .desktop_composition_sources import _checked_textbook_crop_resource, validate_textbook_crop_requests
from .desktop_lesson_composition import block, digest, resource, validate_resource, TEACHER_ROLES
from .desktop_preparation_images import MAX_IMAGES, image_info, normalize_image_assets, verify_image_bytes
from .desktop_publication_claims import _CLAIMS, _OWNED
from .desktop_saved_preparation_images import (validate_saved_preparation_binding,
    resolve_saved_preparation_image, target_image_scope, bind_saved_preparation_selection, _binding_scope)
from .desktop_teaching_framework import classroom_text
from .desktop_textbook_crop_reading import _image_bytes, _read_receipt
from .desktop_textbook_preparation_crops import MAX_BATCH_IMAGE_BYTES, _safe_state_path
from .desktop_word_source_reference import _COMMIT_LOCK
from .reader_cancellation import check_read_cancelled, publication_guard

def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _stamp(result):
    value = deepcopy(result)
    for row in value['resources']:
        row.pop('id'); row.pop('original_sha256')
        for blocks in (row['blocks'], row['original']['blocks']):
            for part in blocks: part.pop('id')
    return digest(value)


def _fields(row, source):
    title = classroom_text(row.get('title', source.get('title', source.get('caption', ''))))
    purpose = classroom_text(row.get('purpose', source.get('purpose', '')))
    teacher_only = row.get('teacher_only', source.get('teacher_only', False))
    if not title or len(title) > 160 or len(purpose) > 500 or type(teacher_only) is not bool:
        raise ValueError('请填写不超过160字的导学问题与不超过500字的课堂用途。')
    return title, purpose, teacher_only


def validate_image_batch_requests(requests):
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IMAGES:
        raise ValueError(f'本批请选择1至{MAX_IMAGES}个图片教学用途。')
    for row in requests:
        if not isinstance(row, dict) or row.get('kind') not in {'image', 'textbook'}:
            raise ValueError('图片批次的来源类型不完整，请重新选图。')
        key = 'request' if row['kind'] == 'textbook' else 'selection'
        if key not in row or set(row) - {'kind', key, 'title', 'purpose', 'teacher_only'}:
            raise ValueError('图片批次的选择字段不完整，请重新选图。')
        if row['kind'] == 'textbook':
            validate_textbook_crop_requests([row[key]])
        elif not isinstance(row[key], dict):
            raise ValueError('教学图片的选择记录不完整。')
        _fields(row, row[key])
    return deepcopy(requests)


def _read_image(facade, asset):
    return _image_bytes(Path(facade.paths.task_root), asset)


def _image_source(facade, choice, existing_assets, resources):
    base = {'title', 'purpose', 'teacher_only'}
    if type(choice.get('teacher_only')) is not bool:
        raise ValueError('请选择图片的课堂可见范围。')
    binding = None
    if 'local' in choice:
        if set(choice) != base | {'local'}:
            raise ValueError('本机图片选择不完整。')
        request = choice['local']
        if not isinstance(request, dict) or set(request) != {'file_path', 'sha256', 'caption', 'source', 'purpose'}:
            raise ValueError('本机图片来源记录不完整。')
        data = read_local_image(request['file_path'])
        if sha256(data).hexdigest() != request['sha256']:
            raise ValueError('所选图片在预览后变化，请重新选择。')
        if not isinstance(request['source'], str): raise ValueError('请保留本机图片来源说明。')
        source = request['source']
        full_path = str(Path(request['file_path']).absolute())
        if full_path not in source: source += '；本机路径：' + full_path
        asset = normalize_image_assets([{'asset_id': 'IMG-' + request['sha256'],
            **{k: request[k] for k in ('sha256', 'caption', 'purpose')}, 'source': source, **image_info(data)}])[0]
        roles = []
    elif 'saved_preparation' in choice:
        if set(choice) != base | {'saved_preparation'}:
            raise ValueError('已有备课图片选择不完整。')
        binding = validate_saved_preparation_binding(choice['saved_preparation'])
        asset = resolve_saved_preparation_image(facade, binding)
        roles, question = _binding_scope(binding)
        if question: raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
        data = _read_image(facade, asset)
    else:
        if set(choice) != base | {'asset', 'source_roles'}:
            raise ValueError('当前备课图片选择不完整。')
        asset = normalize_image_assets([choice['asset']])[0]
        if asset not in existing_assets:
            raise ValueError('所选图片记录已变化，请重新选择。')
        roles = image_roles(resources, asset['asset_id'])
        if roles != choice['source_roles']:
            raise ValueError('图片原素材范围已变化，请重新选择。')
        data = _read_image(facade, asset)
    verify_image_bytes(asset, data)
    return asset, data, roles, binding


def _compile(facade, assets, resources, requests, guard=None):
    def current():
        check_read_cancelled()
        if guard: guard()
    current()
    existing = normalize_image_assets(assets)
    resources = [validate_resource(row) for row in resources]
    # Check existing copies one at a time. Only selected pixels are retained;
    # a large lesson must not consume the batch's temporary memory allowance.
    for asset in existing:
        current(); _read_image(facade, asset)
    payloads = {}
    by_id = {a['asset_id']: a for a in existing}
    candidates, receipts, scopes = [], {}, {}
    for row in requests:
        current()
        title, purpose, teacher_only = _fields(row, row.get('selection', row.get('request')))
        if row['kind'] == 'image':
            asset, data, roles, binding = _image_source(facade, row['selection'], existing, resources)
            source_item = None
        else:
            request = row['request']
            preview = facade.preparation_textbook_crop_preview(request['selection'], request['page_revision'],
                request['bounds'], request['caption'], request['purpose'])
            if (preview['preview_revision'] != request['expected_preview_revision']
                    or preview['image_asset']['sha256'] != request['expected_image_sha256']):
                raise ValueError('教材裁片已变化，请重新核对。')
            class PreviewFacade:
                def __getattr__(self, name): return getattr(facade, name)
                def preparation_textbook_crop_preview(self, *_): return deepcopy(preview)
            source_item = _checked_textbook_crop_resource(PreviewFacade(), request)
            asset, data = preview['image_asset'], preview['image_bytes']
            verify_image_bytes(asset, data)
            receipts[preview['provenance']['crop_id']] = deepcopy(preview['provenance'])
            roles, binding = [], None
        selected_asset = by_id.setdefault(asset['asset_id'], deepcopy(asset))
        target_roles, question = target_image_scope(resources, selected_asset)
        if question: raise ValueError('原题图片请从“完整题…”加入，保留题面与答案范围。')
        scopes.setdefault(asset['asset_id'], set()).update([*roles, *target_roles])
        payloads[asset['asset_id']] = data
        verify_image_bytes(selected_asset, data)
        candidates.append((row, selected_asset, deepcopy(asset), title, purpose, teacher_only, binding, source_item))
        normalize_image_assets(list(by_id.values()))
        if sum(len(data) for data in payloads.values()) > MAX_BATCH_IMAGE_BYTES:
            raise ValueError('本批图片总量过大，请减少图片或缩小裁片。')
    kept = []
    for row, asset, source_asset, title, purpose, teacher_only, binding, source_item in candidates:
        current()
        roles = sorted(scopes[asset['asset_id']])
        role = 'teacher_note' if teacher_only or set(roles) & TEACHER_ROLES else 'material'
        if source_item is None:
            original_purpose = classroom_text(source_asset['purpose'])
            blocks = [block('image', role, asset['asset_id'])]
            # One editable text block also represents an initially empty task.
            blocks.append(block('text', role, original_purpose))
            # The asset registry shares bytes/keeps its first metadata. Each
            # use nevertheless retains the exact source selected for that use.
            evidence = {'source_path': source_asset['source'], 'source_sha256': source_asset['sha256'],
                'source_revision': digest(source_asset), 'image_asset': deepcopy(source_asset), 'source_roles': roles}
            item = resource('preparation_image', source_asset['caption'], blocks, evidence)
            if binding: item = bind_saved_preparation_selection(item, binding)
        else:
            item = deepcopy(source_item)
            if role == 'teacher_note':
                for part in item['original']['blocks']: part['role'] = 'teacher_note'
                item['blocks'] = deepcopy(item['original']['blocks'])
                item['original_sha256'] = digest(item['original'])
        item['title'] = title
        for part in item['blocks']:
            if part['kind'] == 'text': part['value'] = purpose
        if item['blocks'] != item['original']['blocks']: item['locked'] = False
        item = validate_resource(item)
        def origin_key(value):
            original = deepcopy(value['original'])
            # Current classroom text/role already forms the use signature.
            # Source identity excludes transient blocks and the accumulated
            # target roles, but keeps the full chosen asset and saved binding.
            original.pop('blocks')
            original['evidence'].pop('source_roles', None)
            return digest(original)
        signature = (origin_key(item), asset['asset_id'], role, title, purpose)
        def same(old):
            parts = old['blocks']
            pictures = [p['value'] for p in parts if p['kind'] == 'image']
            return (origin_key(old), pictures[0] if len(pictures) == 1 else None,
                next((p['role'] for p in parts if p['kind'] == 'image'), None), old['title'],
                '\n'.join(p['value'] for p in parts if p['kind'] == 'text')) == signature
        if not any(same(old) for old in [*resources, *kept]): kept.append(item)
    used = {p['value'] for row in kept for p in row['blocks'] if p['kind'] == 'image'}
    merged = normalize_image_assets(existing + [a for key, a in by_id.items()
        if key in used and key not in {a['asset_id'] for a in existing}])
    receipts = {key: value for key, value in receipts.items() if value['image_asset']['asset_id'] in used}
    current()
    return {'resources': kept, 'image_assets': merged}, payloads, receipts


class ImageBatchPublication:
    """An in-memory batch whose published files remain owned until UI commit."""
    def __init__(self, facade, assets, resources, requests, result, image_data, receipts, guard=None):
        self.facade, self.existing_assets, self.existing_resources = facade, deepcopy(assets), deepcopy(resources)
        self.requests, self.result = deepcopy(requests), deepcopy(result)
        self.image_data, self._receipts = dict(image_data), deepcopy(receipts)
        self._stamp, self._request_stamp = _stamp(result), digest(requests)
        self._result_stamp = digest(result)
        self._guard, self._lock, self._token = guard, RLock(), uuid4().hex
        self._created, self._claimed, self._published_files = set(), set(), {}
        self._published = self._accepted = self._discarded = False

    def _check(self, guard=None):
        check_read_cancelled()
        if self._guard: self._guard()
        if guard: guard()
        if (self._discarded or self._accepted or digest(self.requests) != self._request_stamp
                or digest(self.result) != self._result_stamp):
            raise ValueError('本批图片已交接或暂存内容发生变化，请重新选图。')
        latest, payloads, receipts = _compile(self.facade, self.existing_assets,
            self.existing_resources, self.requests, guard or self._guard)
        if (_stamp(latest) != self._stamp or receipts != self._receipts
                or self.image_data != payloads):
            raise ValueError('本批图片或来源在预览后变化，请重新核对。')

    def _check_published_files(self) -> None:
        for target, length, expected in self._published_files.values():
            _safe_state_path(target)
            if not target.is_file() or target.stat().st_size != length:
                raise ValueError('已保存的图片或裁片凭据发生变化，本批未加入。')
            with target.open('rb') as stream:
                data = stream.read(length + 1)
            if sha256(data).hexdigest() != expected:
                raise ValueError('已保存的图片或裁片凭据发生变化，本批未加入。')

    def publish(self, guard=None):
        with self._lock:
            if self._published: raise ValueError('本批图片已经交接，请勿重复加入。')
            self._check(guard)
            root = Path(self.facade.paths.task_root) / 'preparation-v1'
            images, crops = root / 'images', root / 'textbook-crops'
            with _COMMIT_LOCK, publication_guard():
                try:
                    for folder in (images, crops): _safe_state_path(folder)
                    images.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix='.image-batch-', dir=images) as folder:
                        stage = Path(folder)
                        files = []
                        for asset in self.result['image_assets']:
                            if asset['asset_id'] not in self.image_data:
                                _read_image(self.facade, asset)
                                continue
                            target = images / (asset['sha256'] + '.image')
                            data = verify_image_bytes(asset, self.image_data[asset['asset_id']])
                            files.append((target, data, asset['asset_id']))
                        for key, record in self._receipts.items():
                            target = crops / (key + '.json'); _safe_state_path(target)
                            if target.exists():
                                previous, data = _read_receipt(target)
                                if previous != record:
                                    raise ValueError('已有教材裁片凭据与本批来源不一致。')
                            else:
                                data = _json({**record, 'created_at_utc': datetime.now(timezone.utc).isoformat()}) + b'\n'
                            files.append((target, data, record['image_asset']['asset_id']))
                        for index, (target, data, asset_id) in enumerate(files):
                            _safe_state_path(target)
                            if target.exists():
                                if not target.is_file() or target.read_bytes() != data:
                                    raise ValueError('已有图片或来源凭据发生变化，本批未加入。')
                                continue
                            staged = stage / str(index); staged.write_bytes(data)
                        for index, (target, data, asset_id) in enumerate(files):
                            target.parent.mkdir(parents=True, exist_ok=True); _safe_state_path(target)
                            key = _path_key(target)
                            if not target.exists():
                                staged = stage / str(index); info = staged.stat()
                                try:
                                    os.link(staged, target)
                                    _OWNED[key] = (target, info.st_dev, info.st_ino, sha256(data).hexdigest(), asset_id)
                                    self._created.add(key)
                                except FileExistsError: pass
                            if target.read_bytes() != data:
                                raise ValueError('本地图片或凭据在保存时变化，本批未加入。')
                            _CLAIMS.setdefault(key, set()).add(self._token)
                            self._claimed.add(key)
                            self._published_files[key] = (target, len(data), sha256(data).hexdigest())
                    self._check(guard)
                    self._check_published_files()
                    self._published = True
                except BaseException:
                    self.discard()
                    raise
            return deepcopy(self.result)

    def recheck_published(self, guard=None):
        with self._lock:
            if not self._published: raise ValueError('本批图片尚未完成交接。')
            self._check(guard)
            for asset in self.result['image_assets']: _read_image(self.facade, asset)
            self._check_published_files()

    def accept(self):
        with self._lock, _COMMIT_LOCK:
            if not self._published or self._discarded: raise ValueError('本批图片尚未完成交接。')
            # A confirmed use makes these files durable even if the creating
            # batch is still live and later cancels. Do not retain tokens forever.
            for key in self._claimed:
                _OWNED.pop(key, None)
                _CLAIMS.get(key, set()).discard(self._token)
                if not _CLAIMS.get(key): _CLAIMS.pop(key, None)
            self._accepted = True; self._created.clear(); self._claimed.clear(); self._published_files.clear()
            self.image_data.clear(); self._receipts.clear()

    def discard(self, *, referenced_assets=()):
        with self._lock, _COMMIT_LOCK:
            if self._accepted or self._discarded: return
            protected = {row['asset_id'] if isinstance(row, dict) else row for row in referenced_assets}
            for key in self._claimed:
                _CLAIMS.get(key, set()).discard(self._token)
                if not _CLAIMS.get(key): _CLAIMS.pop(key, None)
            # Ownership follows a shared unaccepted file, not the first batch
            # object. Both cancellation orders must clean the last unused copy.
            for key in self._created | self._claimed:
                owned = _OWNED.get(key)
                if owned is None: continue
                target, device, inode, expected, asset_id = owned
                if asset_id in protected:
                    _OWNED.pop(key, None); continue
                if _CLAIMS.get(key): continue
                try: _safe_state_path(target)
                except ValueError:
                    _OWNED.pop(key, None); continue
                if target.is_file() and not target.is_symlink():
                    stat = target.stat()
                    if ((stat.st_dev, stat.st_ino) == (device, inode)
                            and sha256(target.read_bytes()).hexdigest() == expected): target.unlink()
                _OWNED.pop(key, None)
            self._created.clear(); self._claimed.clear(); self.image_data.clear(); self._receipts.clear()
            self._published_files.clear()
            self._discarded = True

    def __del__(self):
        try: self.discard()
        except (OSError, RuntimeError, AttributeError): pass


def prepare_image_batch(facade, assets, resources, selection_requests, *, guard=None) -> ImageBatchPublication:
    requests = validate_image_batch_requests(selection_requests)
    result, pixels, receipts = _compile(facade, assets, resources, requests, guard)
    value = ImageBatchPublication(facade, assets, resources, requests, result, pixels, receipts, guard)
    # An early source must still agree after the last item has been read.
    value._check(guard)
    return value


def image_batch_is_referenced(result, *designs, existing_assets=()):
    images = {a['asset_id'] for a in result['image_assets']} - {a['asset_id'] for a in existing_assets}
    identities = {r['id'] for r in result['resources']}
    return any(row['id'] in identities or any(b['kind'] == 'image' and b['value'] in images for b in row['blocks'])
        for design in designs if isinstance(design, dict)
        for row in design.get('composition', {}).get('resources', []))
