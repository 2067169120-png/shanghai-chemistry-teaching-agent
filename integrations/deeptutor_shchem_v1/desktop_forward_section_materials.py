"""One owned, atomic publication for ordered textbook-section material requests.

Native compilers run only in a temporary task root. The destination receives
the complete image/receipt set after every request passes, and the receiver
acknowledges one classroom transaction. No placeholder knowledge is persisted.
"""
from copy import deepcopy
from contextlib import nullcontext
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from .desktop_handout_material_handoff import _ReadOnlyReferenceWords, _StagingFacade, _stamp
from .desktop_lesson_composition import MAX_COMPOSITION_RESOURCES, digest, validate_resource
from .desktop_preparation_images import (
    MAX_IMAGES, PreparationImageStore, normalize_image_assets, verify_image_bytes)
from .desktop_publication_claims import _CLAIMS as _FILE_CLAIMS, claim_file, release_file_claims
from .desktop_textbook_crop_reading import _image_bytes, _read_receipt
from .desktop_textbook_preparation_crops import (
    CROP_REQUEST_FIELDS, MAX_BATCH_IMAGE_BYTES, TextbookPreparationCropService, _safe_state_path)
from .desktop_word_source_reference import _COMMIT_LOCK
from .reader_cancellation import check_read_cancelled, publication_guard


class _SectionReferenceWords(_ReadOnlyReferenceWords):
    def reference(self, selections, *, include_images=True):
        if include_images is not True:
            raise ValueError('本节选材须保留完整题及全部原图。')
        return super().reference(selections)


class _SectionStagingFacade(_StagingFacade):
    """Keep bound facade write methods away from the real task root."""
    def _word_questions(self):
        if "_section_reference_words" not in self.__dict__:
            self._section_reference_words = _SectionReferenceWords(self._facade._word_questions(), self,
                current_words=lambda: self._facade._word_questions())
        self._section_reference_words._checked_words()
        return self._section_reference_words

    def word_question_reference(self, selections, *, include_images=True):
        return self._word_questions().reference(selections, include_images=include_images)

    def preparation_image_bytes(self, asset):
        return _image_bytes(self.paths.task_root, asset)

    def import_textbook_preparation_crops(self, requests, existing_assets=None):
        if (not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IMAGES
                or any(not isinstance(row, dict) or set(row) != CROP_REQUEST_FIELDS for row in requests)):
            raise ValueError('教材裁片须逐项保留完整的来源与确认记录。')
        assets = normalize_image_assets(existing_assets if existing_assets is not None else [])
        personal = any(str(row['selection'].get('visual_asset_id', '')).startswith('BOOKPAGE-')
                       for row in requests)
        service = TextbookPreparationCropService(self.paths.workspace_root, self.paths.task_root,
            personal_books=self.textbook_workspace() if personal else None)
        previews, size, known = [], 0, {row['asset_id'] for row in assets}
        for request in requests:
            check_read_cancelled()
            preview = service._confirmed_preview(**request)
            size += len(preview['image_bytes'])
            if size > MAX_BATCH_IMAGE_BYTES:
                raise ValueError('本批教材裁片过大，请减少裁片或缩小选框。')
            previews.append(preview)
            asset = preview['image_asset']
            if asset['asset_id'] not in known:
                assets.append(asset); known.add(asset['asset_id'])
        assets = normalize_image_assets(assets)
        for asset in existing_assets or []:
            _image_bytes(self.paths.task_root, asset)
        # Save every distinct provenance receipt even when two uses have the
        # same pixels. This is temporary work, never publication_guard().
        for preview in previews:
            check_read_cancelled()
            if service._save_preview(preview) != preview['image_asset']:
                raise ValueError('教材原图与暂存副本不一致。')
        check_read_cancelled()
        return {'image_assets': assets, 'requested_count': len(requests),
                'candidate_only': True, 'human_reviewed': False}


def _receipt_stamp(receipts):
    records = {}
    for key, raw in receipts.items():
        value = json.loads(raw.decode('utf8'))
        value.pop('created_at_utc')
        records[key] = value
    return digest(records)


def _check_batch_roles(resources, additions):
    """A later complete answer cannot expose an earlier standalone picture."""
    from .desktop_composition_images import image_roles, teacher_image
    from .desktop_handout_images import handout_image_roles
    for item in additions:
        if item['original']['kind'] != 'handout_image':
            continue
        original = item['original']
        source = original['evidence']['handout_source']
        roles = set(handout_image_roles(resources, source))
        for block in original['blocks']:
            if block['kind'] == 'image': roles.update(image_roles(resources, block['value']))
        if teacher_image(roles) and any(block['role'] != 'teacher_note' for block in original['blocks']):
            raise ValueError('本批原图同时用于教师答案，请将该图明确设为教师材料后重新确认。')


def _compile_section_batch(facade, item, existing_resources, existing_assets, requests):
    uses_words = any(row.get('kind') in {'questions', 'handout_materials'} for row in requests)
    with facade._word_questions()._lock if uses_words else nullcontext():
        return _compile_section_batch_locked(facade, item, existing_resources, existing_assets, requests)


def _compile_section_batch_locked(facade, item, existing_resources, existing_assets, requests):
    from .desktop_section_source_stage import SectionSourceStage
    with TemporaryDirectory(prefix='shchem-section-materials-') as temporary:
        staged = _SectionStagingFacade(facade, temporary)
        stage = SectionSourceStage(staged, item, existing_resources, existing_assets,
                                   excluded_roots=(staged.paths.task_root,))
        for original in ([row.get('original') for row in existing_resources]
                + [item.get('original') if isinstance(item, dict) else None]
                + [row.get('knowledge_original') for row in requests]):
            if isinstance(original, dict) and isinstance(original.get('evidence'), dict):
                stage.files.protect_material(original['evidence'].get('source_path'))
        for request in requests:
            material = request.get('material_request')
            if isinstance(material, dict) and isinstance(material.get('source'), dict):
                stage.files.protect_material(material['source'].get('source_path'))
        staged._section_source_stage = stage
        try:
            with stage.files.capture():
                result = _compile_section_batch_staged(
                    facade, staged, item, existing_resources, existing_assets, requests)
            stage.finish()
            return result
        finally:
            stage.close()


def _compile_section_batch_staged(facade, staged, item, existing_resources, existing_assets, requests):
    from .desktop_section_material_handoff import prepare_section_material_request
    resources, assets = deepcopy(existing_resources), deepcopy(existing_assets)
    additions, checks = [], []
    if assets:
        store = PreparationImageStore(staged.paths.task_root / 'preparation-v1' / 'images')
        for asset in assets:
            check_read_cancelled()
            raw = _image_bytes(Path(facade.paths.task_root), asset)
            if store.import_bytes(raw, asset['caption'], asset['source'], asset['purpose']) != asset:
                raise ValueError('现有课件原图与暂存副本不一致。')
    for request in requests:
        check_read_cancelled()
        publication = prepare_section_material_request(staged, item, resources, assets, request)
        try:
            result = publication.stage_result(staged)
            checks.append(publication._check)
            if publication._inner and publication._inner._recheck:
                inner, expected = publication._inner, _stamp(publication._inner.result)
                def verify_inner(inner=inner, expected=expected):
                    current, _ = inner._recheck()
                    if _stamp(current) != expected:
                        raise ValueError('较早选取的知识或完整题来源在整批核对期间变化。')
                checks.append(verify_inner)
            if request['kind'] == 'handout_materials':
                from .desktop_handout_sources import handout_source_context
                source = deepcopy(request['material_request']['source'])
                checks.append(lambda source=source: handout_source_context(staged, source))
        finally:
            publication.discard()
        additions.extend(result['resources']); resources.extend(result['resources'])
        assets = normalize_image_assets(result['image_assets'])
        if len(resources) > MAX_COMPOSITION_RESOURCES:
            raise ValueError(f'每课最多保留{MAX_COMPOSITION_RESOURCES}项资源，请减少本批选择。')
        if len({row['id'] for row in resources}) != len(resources):
            raise ValueError('本批资源身份与现有课件重复，请重新选择。')
    # Roles remain attached to every ordered occurrence, including a
    # complete question selected later than a standalone picture.
    _check_batch_roles(resources, additions)
    payloads = {asset['sha256']: _image_bytes(staged.paths.task_root, asset) for asset in assets}
    receipts = {}
    for row in additions:
        if row['original']['kind'] == 'textbook_image':
            key = row['original']['evidence']['crop_id']
            path = staged.paths.task_root / 'preparation-v1' / 'textbook-crops' / (key + '.json')
            _record, raw = _read_receipt(path)
            receipts[key] = raw
    for check in checks:
        check_read_cancelled(); check()
    check_read_cancelled()
    return {'resources': additions, 'image_assets': assets}, payloads, receipts


# Claims use actual file identity as well as its content hash. An accepted
# overlapping batch protects a shared file from another receiver's discard.
_DIRECTORY_CLAIMS = {}


class SectionMaterialBatchPublication:
    def __init__(self, facade, item, existing_resources, existing_assets, requests, result, payloads, receipts):
        self.facade, self.item = facade, item
        self.existing_resources, self.existing_assets = deepcopy(existing_resources), deepcopy(existing_assets)
        self.requests = deepcopy(requests)
        self.result, self._payloads, self._receipts = deepcopy(result), payloads, receipts
        self._exact = digest(result)
        self._request_stamp = digest(requests)
        self._receipt_bytes = {key: sha256(raw).hexdigest() for key, raw in receipts.items()}
        self._stamp, self._receipt_stamp = _stamp(result), _receipt_stamp(receipts)
        self._accepted = self._published = self._discarded = False
        self._claims, self._directories, self._token, self._lock = [], [], object(), RLock()
        self._published_files = {}

    def _check(self, guard=None):
        if (digest(self.result) != self._exact or digest(self.requests) != self._request_stamp
                or {key: sha256(raw).hexdigest() for key, raw in self._receipts.items()} != self._receipt_bytes):
            raise ValueError('暂存选材发生变化，请重新选择。')
        for asset in self.result['image_assets']:
            verify_image_bytes(asset, self._payloads.get(asset['sha256']))
        if guard: guard()
        check_read_cancelled()
        current, _payloads, receipts = _compile_section_batch(self.facade, self.item,
            self.existing_resources, self.existing_assets, self.requests)
        if _stamp(current) != self._stamp or _receipt_stamp(receipts) != self._receipt_stamp:
            raise ValueError('本批知识、原图或完整题的来源已经变化，未加入本课。')
        check_read_cancelled()
        if guard: guard()

    def _mkdir(self, path):
        _safe_state_path(path)
        pending, cursor = [], path
        while not cursor.exists():
            pending.append(cursor); cursor = cursor.parent
        for folder in reversed(pending):
            try:
                folder.mkdir()
                info = folder.stat()
                key = (os.path.normcase(str(folder.absolute())), info.st_dev, info.st_ino)
                _DIRECTORY_CLAIMS[key] = {'path': folder, 'owners': set(), 'accepted': False}
            except FileExistsError:
                if not folder.is_dir(): raise ValueError('本地选材保存位置不可用。')
        _safe_state_path(path)
        # A later overlapping publication shares cleanup responsibility for
        # directories created by the earlier one, just as it shares files.
        for folder in (path, *path.parents):
            info = folder.stat()
            key = (os.path.normcase(str(folder.absolute())), info.st_dev, info.st_ino)
            claim = _DIRECTORY_CLAIMS.get(key)
            if claim is not None:
                claim['owners'].add(self._token)
                if key not in self._directories: self._directories.append(key)

    def _claim(self, path, raw, created, file_identity):
        asset_id = ('IMG-' + path.stem if path.suffix == '.image' else
                    json.loads(raw.decode('utf8'))['image_asset']['asset_id'])
        key = claim_file(path, raw, self._token, created=created, asset_id=asset_id,
                         file_identity=file_identity)
        if key not in self._claims: self._claims.append(key)

    def _files(self):
        root = Path(self.facade.paths.task_root) / 'preparation-v1'
        return [(root / 'images' / (asset['sha256'] + '.image'), self._payloads[asset['sha256']], asset)
                for asset in self.result['image_assets']] + [
            (root / 'textbook-crops' / (key + '.json'), raw, None) for key, raw in self._receipts.items()]

    def _check_published_files(self):
        for path, (length, expected) in self._published_files.items():
            _safe_state_path(path)
            if not path.is_file() or path.stat().st_size != length:
                raise ValueError('已发布图片或教材来源记录发生变化，本批未加入。')
            with path.open('rb') as stream:
                raw = stream.read(length + 1)
            if sha256(raw).hexdigest() != expected:
                raise ValueError('已发布图片或教材来源记录发生变化，本批未加入。')

    @staticmethod
    def _existing(path, raw, asset):
        _safe_state_path(path)
        if not path.is_file(): raise ValueError('本地选材副本不是可核对的文件。')
        if asset is not None:
            current = path.read_bytes()
            verify_image_bytes(asset, current)
            if current != raw: raise ValueError('本地选材图片与原图不一致。')
        else:
            record, current = _read_receipt(path)
            expected = json.loads(raw.decode('utf8')); expected.pop('created_at_utc')
            if record != expected: raise ValueError('已有教材裁片来源记录已变化，未覆盖。')
        return current

    def publish(self, guard=None):
        with self._lock:
            if self._published or self._discarded:
                raise ValueError('本批选材已经交接或取消，请勿重复提交。')
            try:
                self._check(guard)
                with _COMMIT_LOCK, publication_guard():
                    files = self._files()
                    for path, raw, asset in files:
                        _safe_state_path(path)
                        if path.exists(): self._existing(path, raw, asset)
                    if guard: guard()
                    if files:
                        task_root = Path(self.facade.paths.task_root)
                        self._mkdir(task_root)
                        with TemporaryDirectory(prefix='.section-material-', dir=task_root) as temporary:
                            # Stage every payload before linking any destination.
                            for index, (_path, raw, _asset) in enumerate(files):
                                (Path(temporary) / str(index)).write_bytes(raw)
                            for index, (path, raw, asset) in enumerate(files):
                                self._mkdir(path.parent)
                                created = False
                                info = (Path(temporary) / str(index)).stat()
                                try:
                                    os.link(Path(temporary) / str(index), path)
                                    created = True
                                except FileExistsError:
                                    raw = self._existing(path, raw, asset)
                                self._claim(path, raw, created, (info.st_dev, info.st_ino))
                                self._published_files[path] = (len(raw), sha256(raw).hexdigest())
                    # Re-read all originals after the complete file set exists.
                    self._check(guard)
                    self._check_published_files()
                    self._published = True
                return deepcopy(self.result)
            except BaseException:
                self.discard()
                raise

    def recheck_published(self, guard=None):
        with self._lock:
            if not self._published or self._accepted or self._discarded:
                raise ValueError('本批选材不在等待加入的状态。')
            self._check(guard)
            for path, raw, asset in self._files(): self._existing(path, raw, asset)
            if guard: guard()
            self._check_published_files()

    def _release(self, accepted):
        release_file_claims(self._claims, self._token, accepted=accepted)
        self._claims.clear()
        for key in sorted(self._directories, key=lambda entry: len(Path(entry[0]).parts), reverse=True):
            claim = _DIRECTORY_CLAIMS.get(key)
            if claim is None: continue
            claim['accepted'] |= accepted
            claim['owners'].discard(self._token)
            if claim['owners']: continue
            path = claim['path']
            if not claim['accepted'] and path.is_dir() and not path.is_symlink():
                try:
                    _safe_state_path(path)
                    info = path.stat()
                    if (info.st_dev, info.st_ino) == key[1:3]: path.rmdir()
                except (OSError, ValueError): pass
            _DIRECTORY_CLAIMS.pop(key, None)
        self._directories.clear()

    def accept(self):
        with self._lock, _COMMIT_LOCK:
            if not self._published or self._discarded:
                raise ValueError('本批选材尚未完成交接。')
            self._release(True)
            self._accepted = True
            self._payloads.clear(); self._receipts.clear(); self._directories.clear()
            self._published_files.clear()

    def discard(self):
        with self._lock, _COMMIT_LOCK:
            if self._accepted: return
            self._release(False)
            self._directories.clear(); self._payloads.clear(); self._receipts.clear()
            self._published_files.clear()
            self._discarded = True

    def __del__(self):
        try: self.discard()
        except (OSError, RuntimeError, AttributeError): pass


def prepare_section_material_batch(facade, item, course_resources, image_assets, requests):
    if (not isinstance(requests, list) or not 1 <= len(requests) <= MAX_COMPOSITION_RESOURCES
            or any(not isinstance(row, dict) or not isinstance(row.get('context'), dict) for row in requests)
            or not isinstance(course_resources, list)):
        raise ValueError('请选择有序且完整的一批本节材料。')
    frozen = deepcopy(requests)
    if any(row['context'] != frozen[0]['context'] for row in frozen):
        raise ValueError('同一批选材须保持同一本原书、同一教材节及其来源版本。')
    from .desktop_section_material_handoff import _request_selection_scope
    for request in frozen:
        _request_selection_scope(request, item)
    resources = [deepcopy(validate_resource(row)) for row in course_resources]
    if len(resources) > MAX_COMPOSITION_RESOURCES or len({row['id'] for row in resources}) != len(resources):
        raise ValueError('当前课堂资源数量或身份无法核对。')
    assets = normalize_image_assets(image_assets)
    result, payloads, receipts = _compile_section_batch(facade, item, resources, assets, frozen)
    check_read_cancelled()
    return SectionMaterialBatchPublication(facade, item, resources, assets, frozen, result, payloads, receipts)
