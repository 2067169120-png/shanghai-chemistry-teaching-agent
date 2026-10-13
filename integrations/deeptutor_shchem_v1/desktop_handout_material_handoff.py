"""One explicit, source-bound batch of lecture materials for a lesson.

Existing native compilers run against a temporary image store.  No classroom
image is published until every selected section, occurrence and complete
question has been checked together.  A returned publication remains owned by
the receiver until its single composition transaction is acknowledged.
"""
from copy import copy, deepcopy
from functools import wraps
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from .desktop_composition_sources import import_questions, merge_question_result
from .desktop_handout_images import import_handout_image, validate_source as image_source
from .desktop_handout_knowledge import (import_handout_knowledge,
    validate_handout_knowledge_source)
from .desktop_handout_questions import HandoutQuestionFacade
from .desktop_handout_sources import handout_source_context
from .desktop_lesson_composition import digest, validate_resource
from .desktop_preparation_images import (PreparationImageStore,
    normalize_image_assets, verify_image_bytes)
from .desktop_publication_claims import claim_file, release_file_claims
from .desktop_word_source_reference import _COMMIT_LOCK, _commit_images
from .reader_cancellation import check_read_cancelled, publication_guard

SCHEMA = 'shchem.handout-material-request.v1'
_FIELDS = {'schema', 'source', 'knowledge_requests', 'image_selections', 'question_selections'}
_SOURCE_IDENTITY = ('batch_id', 'source_id', 'source_name', 'source_sha256', 'source_revision')
_SELECTION_ARRAYS = {'knowledge': 'knowledge_requests', 'image': 'image_selections',
                     'question': 'question_selections'}


def _source_errors(operation):
    @wraps(operation)
    def checked(*args, **kwargs):
        from .desktop_facade import DesktopFacadeError
        try: return operation(*args, **kwargs)
        except DesktopFacadeError as exc:
            raise ValueError(exc.message_zh) from exc
    return checked


def _same_document(chosen, source):
    if any(chosen.get(k) != source[k] for k in _SOURCE_IDENTITY):
        raise ValueError('本批选材必须来自同一份已选讲义及其原文版本。')
    if 'source_role' in chosen and chosen['source_role'] != source['source_role']:
        raise ValueError('讲义选材的原文件角色已变化，请重新选择。')
    if Path(chosen['source_path']).resolve() != Path(source['source_path']).resolve():
        raise ValueError('讲义选材的原文件位置不一致，请重新读取。')


def validate_material_request(request):
    if (not isinstance(request, dict) or not _FIELDS <= set(request) <= _FIELDS | {'selection_order'}
            or request['schema'] != SCHEMA):
        raise ValueError('讲义选材记录不完整，请返回资料库重新选择。')
    source = validate_handout_knowledge_source(request['source'])
    for name in ('knowledge_requests', 'image_selections', 'question_selections'):
        if not isinstance(request[name], list) or len(request[name]) > 20:
            raise ValueError('每批知识段、原图和完整题分别最多20项。')
    if not any(request[name] for name in ('knowledge_requests', 'image_selections', 'question_selections')):
        raise ValueError('请先选择至少一项讲义知识、原图或完整题。')
    if 'selection_order' in request:
        order = request['selection_order']
        expected = {(kind, index) for kind, name in _SELECTION_ARRAYS.items()
                    for index in range(len(request[name]))}
        if not isinstance(order, list) or len(order) != len(expected):
            raise ValueError('授课顺序须完整保留本批每一项选择。')
        actual = []
        for entry in order:
            if (not isinstance(entry, dict) or set(entry) != {'kind', 'index'}
                    or not isinstance(entry['kind'], str) or entry['kind'] not in _SELECTION_ARRAYS
                    or type(entry['index']) is not int):
                raise ValueError('授课顺序的素材类型或位置无效，请重新选择。')
            actual.append((entry['kind'], entry['index']))
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError('授课顺序不能遗漏、重复或引用本批之外的选择。')
    for row in request['knowledge_requests']:
        if not isinstance(row, dict) or 'source' not in row:
            raise ValueError('讲义知识段缺少完整原文选择。')
        _same_document(validate_handout_knowledge_source(row['source']), source)
    for row in request['image_selections']:
        if not isinstance(row, dict) or set(row) != {'source', 'title', 'purpose', 'teacher_only'}:
            raise ValueError('讲义原图缺少图题、用途或使用范围。')
        _same_document(image_source(row['source']), source)
    for row in request['question_selections']:
        required = {'key', 'revision', 'points', 'source_sha256', 'scoped_source_document'}
        if not isinstance(row, dict) or not required <= set(row) <= required | {'title'}:
            raise ValueError('完整题须保留本份讲义的确切归档及题目版本。')
        if 'title' in row and (not isinstance(row['title'], str) or not row['title'].strip()
                               or len(row['title']) > 160):
            raise ValueError('完整题的本课引导问题须为1至160字。')
        _same_document(validate_handout_knowledge_source(row['scoped_source_document']), source)
        if row['source_sha256'] != source['source_sha256']:
            raise ValueError('完整题与已选讲义不属于同一原文版本。')
    value = deepcopy(request)
    for row in value['question_selections']:
        if 'title' in row: row['title'] = row['title'].strip()
    return value


class _Paths:
    def __init__(self, original, task_root):
        self._original, self.task_root = original, Path(task_root)
    def __getattr__(self, name):
        return getattr(self._original, name)


class _StagingFacade:
    def __init__(self, facade, task_root):
        self._facade = facade
        # Borrowed native ports can be bound to a different read-only profile.
        # Preserve that exact source root when routing native methods via us.
        saved = getattr(facade, '_saved_visual_import_batch', None)
        self._source_facade = (facade._source_facade if isinstance(facade, _StagingFacade)
                               else getattr(saved, '__self__', facade))
        self.paths = _Paths(facade.paths, task_root)
        from .desktop_section_source_stage import register_staging_root
        register_staging_root(self.paths.task_root)
    def _source_reader(self, name):
        if isinstance(self._facade, _StagingFacade):
            return self._facade._source_reader(name)
        return getattr(self._facade, name)

    def _read_port_identity(self, name):
        reader = self._source_reader(name)
        owner = getattr(reader, '__self__', self._facade)
        return {'owner': id(owner), 'function': id(getattr(reader, '__func__', reader)),
                'archive_root': str(getattr(self._source_facade, '_visual_import_root', ''))}

    def _saved_visual_import_batch(self, batch_id):
        reader = self._source_reader('_saved_visual_import_batch')
        stage = getattr(self._facade, '_section_source_stage', None)
        stage = self.__dict__.get('_section_source_stage', stage)
        if stage is None:
            return reader(batch_id)
        scope = {'source_port': self._read_port_identity('_saved_visual_import_batch'),
                 'batch_id': batch_id}
        def native():
            return {'port': self._read_port_identity('_saved_visual_import_batch'),
                    'descriptor': self._source_reader('_saved_visual_import_batch')(batch_id)}
        return stage.read_memo('saved_batch', scope, native)['descriptor']

    def _read_batch_manifest(self, descriptor):
        reader = self._source_reader('_import_batch_manifest')
        if not callable(reader):
            raise AttributeError('_import_batch_manifest')
        stage = getattr(self, '_section_source_stage', None)
        chosen = deepcopy(descriptor)
        if stage is None:
            return reader(chosen)
        scope = {'source_port': self._read_port_identity('_import_batch_manifest'),
                 'descriptor_sha256': digest(chosen)}
        def native():
            return {'port': self._read_port_identity('_import_batch_manifest'),
                    'manifest': self._source_reader('_import_batch_manifest')(deepcopy(chosen))}
        return stage.read_memo('batch_manifest', scope, native,
            sealed_identity=lambda value: {'port': value['port'],
                                          'manifest_sha256': value['manifest'][1]})['manifest']

    def _verify_batch_manifest(self, descriptor):
        """Private confirmation for native inventory's explicit discard-only gate.

        Full native parsing and validation run before sealing every field. This
        method does not supply a manifest to any API that consumes its contents.
        """
        chosen = deepcopy(descriptor)
        stage = getattr(self, '_section_source_stage', None)
        def native():
            value = self._source_reader('_import_batch_manifest')(deepcopy(chosen))
            return {'verified': True, 'port': self._read_port_identity('_import_batch_manifest'),
                    'raw_revision': value[1], 'semantic_sha256': digest(value)}
        if stage is None:
            return native()
        scope = {'source_port': self._read_port_identity('_import_batch_manifest'),
                 'descriptor_sha256': digest(chosen), 'projection': 'native_inventory_verification'}
        return stage.read_memo('batch_manifest', scope, native)

    def __getattr__(self, name):
        if name == '_visual_import_root':
            return getattr(self._source_facade, name)
        value = getattr(self._facade, name)
        if name == '_import_batch_manifest' and callable(value):
            return self._read_batch_manifest
        # Only these native read methods need self-routing to reach the stage
        # descriptor. Injected ports keep their original validation contract.
        if name in {'_imported_word_source', '_restore_visual_import_sources',
                    'imported_word_path', 'imported_word_sources', 'imported_word_asset'}:
            function = getattr(value, '__func__', None)
            if (function is not None
                    and function.__module__ == 'integrations.deeptutor_shchem_v1.desktop_facade'):
                return function.__get__(self, type(self))
        return value


class _ReadOnlyReferenceWords:
    """Use the native reference compiler without persistent preview writes."""
    def __init__(self, scoped_words, facade=None, *, current_words=None):
        self._words, self._read_facade = scoped_words, facade
        self._current_words = current_words
        self._locations = dict(getattr(scoped_words, '_locations', {}))

    def _checked_words(self):
        check_read_cancelled()
        if self._current_words is not None and self._current_words() is not self._words:
            raise ValueError('当前完整题来源端口在本阶段变化，请重新选择。')
        return self._words

    def _native_catalog(self, locations, *, read_only, progress=None):
        from .desktop_word_questions import WordQuestionService
        self._checked_words()
        if (self._read_facade is None or type(self._words) is not WordQuestionService):
            return self._words._catalog(locations, read_only=read_only, progress=progress)
        native = copy(self._words)
        # Only the exact native inventory ignores its manifest gate's return.
        # Public manifest consumers and custom inventory ports keep the full
        # manifest API; no caller-stack guess changes that contract.
        class CatalogReads:
            def __init__(self, facade):
                self._facade = facade
            def __getattr__(self, name):
                value = getattr(self._facade, name)
                if name == '_import_batch_manifest' and callable(value):
                    return self._facade._verify_batch_manifest
                return value
        exact_inventory = (getattr(self._words._inventory, '__func__', None)
                           is WordQuestionService._inventory)
        can_verify = callable(getattr(self._read_facade, '_verify_batch_manifest', None))
        native.facade = (CatalogReads(self._read_facade)
                         if exact_inventory and can_verify else self._read_facade)
        # A new native extraction at both first read and tail also verifies
        # in-memory readers. No existing process preview grants the new seal.
        native._cache, native._locations = {}, {}
        return native._catalog(locations, read_only=read_only, progress=progress)

    def _catalog(self, locations=None, *, read_only=False, progress=None):
        self._checked_words()
        stage = getattr(self._read_facade, '_section_source_stage', None)
        if stage is None or locations is None or read_only is not True or progress is not None:
            result = self._native_catalog(locations, read_only=read_only, progress=progress)
        else:
            # Let the existing validator reject malformed/unbounded locations.
            if (not isinstance(locations, dict) or any(not isinstance(batch, str) or not batch.strip()
                    or not isinstance(ids, (set, frozenset)) or not ids
                    or any(not isinstance(source_id, str) or not source_id.strip() for source_id in ids)
                    for batch, ids in locations.items())):
                return self._native_catalog(locations, read_only=True)
            chosen = {batch: set(ids) for batch, ids in locations.items()}
            bindings = [{'batch_id': batch, 'archive_source_ids': sorted(ids),
                         'descriptor': self._read_facade._saved_visual_import_batch(batch)}
                        for batch, ids in sorted(chosen.items())]
            def seal(value):
                catalog, inventory = value
                sources = [{'batch_id': batch, 'archive_source_id': source.effective_source_file_id,
                            'source_sha256': source.source_sha256, 'source_name': source.filename,
                            'source_role': source.role, 'source_revision': preview['revision']}
                           for source, batch, preview, _indexed in inventory.values()]
                return {'sources': sources, 'catalog': catalog}
            ports = {'words': id(self._words), 'state': id(getattr(self._words, 'state', None)),
                     'attributes': id(getattr(self._words, 'attribute_store', None)),
                     'reader': id(getattr(self._words, 'reader', None)),
                     'workspace_root': str(self._read_facade.paths.workspace_root),
                     'state_root': str(getattr(self._read_facade.paths, 'state_root', ''))}
            result = stage.read_memo('word_catalog', {'ports': ports, 'bindings': bindings, 'read_only': True},
                lambda: self._native_catalog(chosen, read_only=True), sealed_identity=seal)
        # Locations select archives only. Restore the exact scope on every hit;
        # native selection/range/points validators still execute per request.
        self._locations.update({row['key']: (row['batch_id'], row['archive_source_id'])
                                for row in result[0]['items']})
        return result

    def _resolve(self, selections, **kwargs):
        from .desktop_word_questions import WordQuestionService
        kwargs['read_only'] = True
        if self._read_facade is not None and type(self._words) is WordQuestionService:
            return WordQuestionService._resolve(self, selections, **kwargs)
        return self._words._resolve(selections, **kwargs)
    def __getattr__(self, name):
        return getattr(self._words, name)
    def reference(self, selections):
        from .desktop_word_questions import WordQuestionService
        return WordQuestionService._compile_reference(self, selections, include_images=True)[0]


def _stamp(result):
    """Compare compiled content without fresh composition/block UUIDs."""
    value = deepcopy(result)
    for resource in value['resources']:
        resource.pop('id'); resource.pop('original_sha256')
        mapping = {b['id']: 'block-' + str(i) for i, b in enumerate(resource['original']['blocks'])}
        def eq_references(spans):
            for span in spans:
                base = span['field']['base']
                if base['kind'] == 'image':
                    base['image_block_id'] = mapping[base['image_block_id']]
        for blocks in (resource['original']['blocks'], resource['blocks']):
            for block in blocks:
                block['id'] = mapping[block['id']]
                for reference in block.get('presentation', {}).get('images', []):
                    reference['image_block_id'] = mapping[reference['image_block_id']]
                eq_references(block.get('presentation', {}).get('eqs', []))
        for block in resource['original']['evidence'].get('section_snapshot', {}).get('blocks', []):
            eq_references(block.get('static_eqs', []))
    return digest(value)


def _knowledge_selection_key(source, outline_revision, section):
    return digest({'source': {key: source[key] for key in (*_SOURCE_IDENTITY, 'source_role')},
                   'outline_revision': outline_revision, 'section': section})


def _compiled_groups(rows, selections, resource_key, selection_key):
    """Assign exact source groups once; a deduplicated input owns an empty group."""
    pending = {}
    for row in rows:
        pending.setdefault(resource_key(row), []).append(row)
    groups = [pending.pop(selection_key(selection), []) for selection in selections]
    if pending:
        raise ValueError('编译内容与本批选材来源不一致，请重新选择。')
    return groups


def _ordered_additions(request, knowledge, image_groups, questions):
    """Sort only new resources after all native batch and role checks complete."""
    def knowledge_key(row):
        evidence = row['original']['evidence']
        snapshot = evidence['section_snapshot']
        return _knowledge_selection_key(evidence['handout_source'],
            snapshot['outline_revision'], snapshot['section'])
    def question_key(row):
        evidence = row['original']['evidence']
        return (evidence['question_key'], evidence['source_sha256'], evidence['source_revision'])
    groups = {
        'knowledge': _compiled_groups(knowledge, request['knowledge_requests'], knowledge_key,
            lambda row: _knowledge_selection_key(row['source'], row['outline_revision'], row['section'])),
        'image': image_groups,
        'question': _compiled_groups(questions, request['question_selections'], question_key,
            lambda row: (row['key'], row['source_sha256'], row['scoped_source_document']['source_revision'])),
    }
    return [resource for entry in request['selection_order']
            for resource in groups[entry['kind']][entry['index']]]


def _question_classroom_titles(result, selections):
    """Apply requested work titles only after complete native originals exist."""
    choices = {(row['key'], row['source_sha256'], row['scoped_source_document']['source_revision']): row
               for row in selections}
    if len(choices) != len(selections):
        raise ValueError('同一道完整题不能重复选择。')
    value = deepcopy(result)
    for index, item in enumerate(value['resources']):
        item = validate_resource(item)
        if item['original']['kind'] != 'complete_question':
            raise ValueError('课堂引导问题只能对应已核对的完整题。')
        evidence = item['original']['evidence']
        chosen = choices.pop((evidence['question_key'], evidence['source_sha256'], evidence['source_revision']), None)
        if chosen is None:
            raise ValueError('课堂引导问题与完整题来源不一致，请重新选择。')
        if 'title' in chosen:
            item['title'] = chosen['title']
        # The source snapshot and its digest are never recreated for a title.
        value['resources'][index] = validate_resource(item)
    if choices:
        raise ValueError('课堂引导问题缺少对应的完整题，请重新选择。')
    return value


def _compile(facade, request, existing_assets, existing_resources, *, reference=None):
    request = validate_material_request(request)
    assets = normalize_image_assets(existing_assets)
    resources = [validate_resource(row) for row in existing_resources]
    source = request['source']
    with TemporaryDirectory(prefix='shchem-lecture-materials-') as temporary:
        staged = _StagingFacade(facade, temporary)
        store = PreparationImageStore(staged.paths.task_root / 'preparation-v1' / 'images')
        original_store = PreparationImageStore(facade.paths.task_root / 'preparation-v1' / 'images') if assets else None
        for asset in assets:
            check_read_cancelled()
            data = original_store.load(asset)
            if store.import_bytes(data, asset['caption'], asset['source'], asset['purpose']) != asset:
                raise ValueError('现有备课图片与暂存副本不一致。')
        handout_source_context(facade, source)
        # Compile complete questions first so known answer-image roles also
        # constrain pictures and knowledge occurrences selected in this batch.
        questions = None
        if request['question_selections']:
            scoped = HandoutQuestionFacade(staged, source)
            choices = [{k: row[k] for k in ('key', 'revision', 'points')}
                for row in request['question_selections']]
            actual_reference = scoped.word_question_reference(choices, include_images=True)
            if reference is not None and actual_reference != reference:
                raise ValueError('所选完整题原文已变化，请重新预览。')
            imported = import_questions(scoped, choices, assets, expected_reference=actual_reference)
            imported = _question_classroom_titles(imported, request['question_selections'])
            questions = merge_question_result(imported, resources, assets)
            assets = questions['image_assets']
        companions = resources + (questions['resources'] if questions else [])
        knowledge = {'resources': [], 'image_assets': assets}
        if request['knowledge_requests']:
            knowledge = import_handout_knowledge(staged, request['knowledge_requests'], assets, companions)
            assets = knowledge['image_assets']
        images, image_groups = [], []
        for selection in request['image_selections']:
            selection = deepcopy(selection)
            if source['source_role'] == 'answer': selection['teacher_only'] = True
            item = import_handout_image(staged, assets, companions + knowledge['resources'] + images, selection)
            image_groups.append(item['resources'])
            images.extend(item['resources']); assets = item['image_assets']
        question_resources = questions['resources'] if questions else []
        ordered = (_ordered_additions(request, knowledge['resources'], image_groups, question_resources)
                   if 'selection_order' in request else knowledge['resources'] + images + question_resources)
        result = {'resources': ordered,
            'image_assets': normalize_image_assets(assets)}
        # Each compiler checks its complete scope. This additionally pins the
        # one document after the whole mixed batch, including a picture-only batch.
        handout_source_context(facade, source)
        payloads = {a['sha256']: store.load(a) for a in result['image_assets']}
        check_read_cancelled()
        return result, payloads


class MaterialPublication:
    """Owned classroom image additions awaiting a single UI transaction."""
    def __init__(self, facade, request, existing_assets, existing_resources, result, payloads,
                 *, recheck=None):
        self.facade, self.request = facade, deepcopy(request)
        self.existing_assets, self.existing_resources = deepcopy(existing_assets), deepcopy(existing_resources)
        self.result, self._payloads = deepcopy(result), payloads
        self._stamp = _stamp(result)
        self._recheck = recheck
        self._created, self._accepted, self._published = [], False, False
        self._claimed, self._claim_token = set(), object()
        self._lock = RLock()

    @_source_errors
    def publish(self, guard=None):
        with self._lock:
            if self._published: raise ValueError('本批选材已经交接，请勿重复提交。')
            if _stamp(self.result) != self._stamp:
                raise ValueError('暂存选材在确认期间变化，请重新选择。')
            if guard: guard()
            check_read_cancelled()
            with self.facade._word_questions()._lock:
                latest, _ = self._recheck() if self._recheck else _compile(self.facade,
                    self.request, self.existing_assets, self.existing_resources)
                if _stamp(latest) != self._stamp:
                    raise ValueError('讲义知识、原图或完整题在确认期间变化，未加入本课。')
                if guard: guard()
                root = Path(self.facade.paths.task_root) / 'preparation-v1' / 'images'
                with _COMMIT_LOCK, publication_guard():
                    try:
                        if self.result['image_assets']:
                            store = PreparationImageStore(root)
                            for asset in self.existing_assets: store.load(asset)
                            known = {a['sha256'] for a in self.existing_assets}
                            additions = [a for a in self.result['image_assets'] if a['sha256'] not in known]
                            for asset in additions:
                                target = root / (asset['sha256'] + '.image')
                                if target.is_symlink() or (target.exists() and not target.is_file()):
                                    raise ValueError('本地图片副本不正确，本批未加入。')
                                if target.exists(): store.load(asset)
                            # Reuse byte validation and atomic staging, then
                            # retain only links this transaction itself creates.
                            # A concurrent successful importer can win a link;
                            # its file never becomes ours to roll back.
                            with TemporaryDirectory(prefix='.lecture-material-', dir=root) as temporary:
                                _commit_images(Path(temporary), additions, self._payloads, [])
                                for asset in additions:
                                    target = root / (asset['sha256'] + '.image')
                                    staged = Path(temporary) / target.name
                                    info = staged.stat()
                                    try:
                                        os.link(staged, target)
                                        self._created.append((target, info.st_dev, info.st_ino, asset['sha256']))
                                        self._claimed.add(claim_file(target, self._payloads[asset['sha256']],
                                            self._claim_token, created=True, asset_id=asset['asset_id'],
                                            file_identity=(info.st_dev, info.st_ino)))
                                    except FileExistsError: store.load(asset)
                                for asset in self.result['image_assets']:
                                    raw = store.load(asset)
                                    self._claimed.add(claim_file(root / (asset['sha256'] + '.image'), raw,
                                        self._claim_token, asset_id=asset['asset_id']))
                        if self._recheck:
                            latest, _ = self._recheck()
                            if _stamp(latest) != self._stamp:
                                raise ValueError('知识或题目关联在保存期间变化，未加入本课。')
                        else: handout_source_context(self.facade, self.request['source'])
                        if guard: guard()
                        self._published = True
                    except BaseException:
                        self.discard()
                        raise
            return deepcopy(self.result)

    @_source_errors
    def recheck_published(self, guard=None):
        """Recheck sealed source after detached editing, without publishing twice."""
        with self._lock:
            if not self._published or self._accepted:
                raise ValueError('本批选材不在等待加入的状态，请重新选择。')
            if _stamp(self.result) != self._stamp:
                raise ValueError('已核对的来源选材发生变化，请重新选择。')
            if guard: guard()
            check_read_cancelled()
            with self.facade._word_questions()._lock:
                latest, _ = self._recheck() if self._recheck else _compile(self.facade,
                    self.request, self.existing_assets, self.existing_resources)
                if _stamp(latest) != self._stamp:
                    raise ValueError('编辑期间讲义来源或选材发生变化，本批未加入。')
                if self.result['image_assets']:
                    store = PreparationImageStore(Path(self.facade.paths.task_root) / 'preparation-v1' / 'images')
                    for asset in self.result['image_assets']: store.load(asset)
                check_read_cancelled()
                if guard: guard()

    def accept(self):
        with self._lock, _COMMIT_LOCK:
            if not self._published: raise ValueError('本批选材尚未完成原文核对。')
            release_file_claims(self._claimed, self._claim_token, accepted=True)
            self._claimed.clear()
            self._accepted = True; self._created.clear(); self._payloads.clear()

    def discard(self):
        with self._lock, _COMMIT_LOCK:
            if self._accepted: return
            release_file_claims(self._claimed, self._claim_token)
            self._claimed.clear()
            self._created.clear(); self._payloads.clear()

    def __del__(self):
        try: self.discard()
        except (OSError, RuntimeError, AttributeError): pass


@_source_errors
def prepare_handout_materials(facade, request, existing_assets, existing_resources):
    request = validate_material_request(request)
    with facade._word_questions()._lock:
        result, payloads = _compile(facade, request, existing_assets, existing_resources)
    return MaterialPublication(facade, request, existing_assets, existing_resources, result, payloads)


@_source_errors
def prepare_handout_knowledge_publication(facade, requests, existing_assets, existing_resources):
    """Stage the existing complete, potentially multi-source knowledge batch."""
    frozen = deepcopy(requests)
    assets = normalize_image_assets(existing_assets)
    resources = [validate_resource(row) for row in existing_resources]
    def compile_knowledge():
        with facade._word_questions()._lock:
            with TemporaryDirectory(prefix='shchem-knowledge-materials-') as temporary:
                staged = _StagingFacade(facade, temporary)
                store = PreparationImageStore(staged.paths.task_root / 'preparation-v1' / 'images')
                original = PreparationImageStore(facade.paths.task_root / 'preparation-v1' / 'images') if assets else None
                for asset in assets:
                    check_read_cancelled()
                    if store.import_bytes(original.load(asset), asset['caption'], asset['source'], asset['purpose']) != asset:
                        raise ValueError('现有备课图片与暂存副本不一致。')
                result = import_handout_knowledge(staged, frozen, assets, resources)
                payloads = {a['sha256']: store.load(a) for a in result['image_assets']}
                check_read_cancelled()
                return result, payloads
    result, payloads = compile_knowledge()
    return MaterialPublication(facade, {'knowledge_requests': frozen}, assets, resources,
        result, payloads, recheck=compile_knowledge)


def checked_material_work_copy(published_result, working_resources):
    """Allow existing resource work edits, preserving this exact complete batch."""
    originals = published_result['resources']
    if (not isinstance(working_resources, list) or len(working_resources) != len(originals)
            or [r.get('id') for r in working_resources if isinstance(r, dict)] != [r['id'] for r in originals]):
        raise ValueError('待加入素材的完整范围与顺序不能改变。')
    checked = [validate_resource(row) for row in working_resources]
    if any(row['original'] != source['original'] or row['original_sha256'] != source['original_sha256']
           for row, source in zip(checked, originals)):
        raise ValueError('课堂副本编辑不能替换已核对的原资源快照或来源。')
    result = deepcopy(published_result)
    result['resources'] = checked
    return result


def material_batch_is_referenced(result, *designs, existing_assets=()):
    """Keep owned pictures if a later edit or an undo state still uses them."""
    identities = {r['id'] for r in result.get('resources', [])}
    images = {b['value'] for r in result.get('resources', []) for b in r['blocks'] if b['kind'] == 'image'}
    images -= {a['asset_id'] for a in existing_assets}
    return any(row['id'] in identities or any(b['kind'] == 'image' and b['value'] in images for b in row['blocks'])
        for design in designs if isinstance(design, dict)
        for row in design.get('composition', {}).get('resources', []))


@_source_errors
def prepare_linked_question(facade, request, existing_assets, existing_resources):
    """Check both exact endpoints and the saved association around native import."""
    from .desktop_knowledge_question_links import KnowledgeQuestionLinks
    if not isinstance(request, dict) or set(request) != {
            'link_id', 'snapshot', 'selection', 'reference', 'scoped_facade', 'knowledge_endpoint'}:
        raise ValueError('知识关联题选择不完整，请重新打开关联列表。')
    service = KnowledgeQuestionLinks(facade)
    frozen = {k: deepcopy(v) for k, v in request.items() if k != 'scoped_facade'}
    def checked():
        with service.words._lock:
            def relation():
                if service.snapshot() != frozen['snapshot']:
                    raise ValueError('知识与例题关联已经变化，请重新选择。')
                row = service.evaluate(frozen['link_id'])
                if row.get('status') != 'current' or row.get('removed', False):
                    raise ValueError('关联的原知识或原题已经变化，请重新打开。')
                if (row['knowledge'] != frozen['knowledge_endpoint'] or
                        service.knowledge_context(frozen['knowledge_endpoint'])['endpoint'] != row['knowledge']):
                    raise ValueError('所选知识与关联记录不是同一内容版本。')
                return row
            row = relation(); resolved = service.resolve_selection(row)
            choice = {**resolved['selection'], 'points': frozen['selection']['points']}
            if choice != frozen['selection']:
                raise ValueError('关联例题的完整范围已经变化，请重新选择。')
            with TemporaryDirectory(prefix='shchem-linked-question-') as temporary:
                staged = _StagingFacade(resolved['scoped_facade'], temporary)
                store = PreparationImageStore(staged.paths.task_root / 'preparation-v1' / 'images')
                original = PreparationImageStore(facade.paths.task_root / 'preparation-v1' / 'images') if existing_assets else None
                for asset in existing_assets:
                    data = original.load(asset)
                    store.import_bytes(data, asset['caption'], asset['source'], asset['purpose'])
                result = merge_question_result(import_questions(staged, [choice], existing_assets,
                    expected_reference=frozen['reference']), existing_resources, existing_assets)
                payloads = {a['sha256']: store.load(a) for a in result['image_assets']}
            latest = relation()
            if latest != row: raise ValueError('知识关联在读取期间变化，请重新选择。')
            check_read_cancelled(); return result, payloads
    result, payloads = checked()
    sealed_result = deepcopy(result)
    def recheck():
        # Only the first checked() constructs a card and a temporary image
        # store. Rechecks still read both original endpoints, the exact native
        # question scope and its complete displayed reference from fresh source.
        with service.words._lock:
            snapshot = service.snapshot()
            from .desktop_word_questions import RANGES_DRAFT
            def selected_range():
                return deepcopy(service.words.state.snapshot().get('drafts', {}).get(
                    RANGES_DRAFT, {}).get('ranges', {}).get(frozen['selection']['key']))
            range_before = selected_range()
            if snapshot != frozen['snapshot']:
                raise ValueError('知识与例题关联已经变化，请重新选择。')
            rows = [row for row in snapshot['collection']['records'] if row['id'] == frozen['link_id']]
            if len(rows) != 1 or rows[0]['removed']:
                raise ValueError('关联的原知识或原题已经变化，请重新打开。')
            row = rows[0]
            if (row['knowledge'] != frozen['knowledge_endpoint'] or
                    service.knowledge_context(frozen['knowledge_endpoint'])['endpoint'] != row['knowledge']):
                raise ValueError('所选知识与关联记录不是同一内容版本。')
            resolved = service.resolve_selection(row)
            choice = {**resolved['selection'], 'points': frozen['selection']['points']}
            if choice != frozen['selection']:
                raise ValueError('关联例题的完整范围已经变化，请重新选择。')
            reference = _ReadOnlyReferenceWords(resolved['scoped_facade']._word_questions()).reference([choice])
            if reference != frozen['reference']:
                raise ValueError('关联例题的原文、原图或使用范围已经变化，请重新选择。')
            if service.knowledge_context(frozen['knowledge_endpoint'])['endpoint'] != row['knowledge']:
                raise ValueError('原知识在核对例题期间变化，请重新选择。')
            endpoint = resolved['endpoint']
            current = facade._imported_word_source(endpoint['batch_id'], endpoint['archive_source_id'])
            if (current.effective_source_file_id != endpoint['archive_source_id']
                    or current.filename != endpoint['source_name'] or current.role != endpoint['source_role']
                    or current.source_sha256 != endpoint['source_sha256']
                    or sha256(current.content).hexdigest() != endpoint['source_sha256']):
                raise ValueError('原题归档或角色在核对知识期间变化，请重新选择。')
            if selected_range() != range_before:
                raise ValueError('完整题原文范围在核对期间变化，请重新选择。')
            if service.snapshot() != snapshot:
                raise ValueError('知识或题目关联在读取期间变化，请重新选择。')
            check_read_cancelled()
            return deepcopy(sealed_result), {}
    return MaterialPublication(facade, frozen, existing_assets, existing_resources,
        result, payloads, recheck=recheck)
