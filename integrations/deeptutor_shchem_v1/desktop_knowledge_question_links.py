"""Personal, version-bound knowledge cards linked to complete archived Word questions.

Metadata reads never enumerate the Word inventory. Endpoint checks and explicit
course handoffs are read-only; only the addressed relation draft is published.
"""
from copy import deepcopy
from functools import wraps
from hashlib import sha256
import json
import re

from .desktop_composition_sources import import_knowledge_note, knowledge_options, knowledge_selection
from .desktop_knowledge_questions import knowledge_question_context, _selection_and_request
from .desktop_lesson_composition import digest, resource, validate_resource
from .desktop_state import DraftConflictError, _draft_snapshot
from .reader_cancellation import check_read_cancelled, publication_guard

SCHEMA = 'shchem.knowledge-question-links.v1'
PREPARED_SCHEMA = 'shchem.knowledge-question-link-prepared.v1'
KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY = 'knowledge-question-links-v1'
MAX_RECORDS, MAX_HISTORY, MAX_REASON, MAX_BYTES = 512, 64, 4000, 8 * 1024 * 1024
KINDS = {'registered_concept', 'reading_note', 'personal_note', 'handout_section'}
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_RANGES = ('block_start', 'question_end', 'answer_start', 'block_end',
           'context_start', 'context_end', 'origin_block_start')
_KNOWLEDGE = {'kind', 'id', 'source_sha256', 'source_revision', 'selection',
              'title', 'original', 'content_sha256'}
_QUESTION = {'key', 'revision', 'source_sha256', 'source_revision', 'preview_revision',
             'extraction_revision', 'batch_id', 'archive_source_id', 'source_name',
             'source_role', 'ranges', 'scope_review_sha256'}
_RECORD = {'id', 'knowledge', 'question', 'reason', 'version', 'removed', 'history'}


class KnowledgeQuestionLinkError(ValueError):
    """Fail closed while retaining the previously saved evidence and history."""
    def __init__(self, message):
        self.message_zh = str(message)
        super().__init__(message)


def _require(condition, message='知识与完整题的关系记录不完整。'):
    if not condition:
        raise KnowledgeQuestionLinkError(message)


def _text(value, limit=256, empty=False):
    return (isinstance(value, str) and len(value) <= limit
            and (empty or bool(value.strip())) and '\x00' not in value)


def _hash(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _strict(call):
    @wraps(call)
    def checked(*args, **kwargs):
        try:
            return call(*args, **kwargs)
        except (KeyError, TypeError, IndexError, RecursionError, AttributeError) as exc:
            raise KnowledgeQuestionLinkError('知识与完整题的关系记录结构无效。') from exc
    return checked


def _source_errors(call):
    @wraps(call)
    def checked(*args, **kwargs):
        try:
            return call(*args, **kwargs)
        except RuntimeError as exc:
            from .desktop_facade import DesktopFacadeError
            if not isinstance(exc, DesktopFacadeError): raise
            error = KnowledgeQuestionLinkError(getattr(exc, 'message_zh', str(exc)))
            error.code = exc.code
            raise error from exc
    return checked


def _bounded(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf8')
    except (TypeError, ValueError, RecursionError) as exc:
        raise KnowledgeQuestionLinkError('关系记录不是有界的JSON数据。') from exc
    _require(len(encoded) <= MAX_BYTES, '关系集合超出个人草稿容量。')


def _without_paths(value):
    if isinstance(value, dict):
        return {key: _without_paths(item) for key, item in value.items() if key != 'source_path'}
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return deepcopy(value)


def _original(card):
    """Storage paths and freshly allocated classroom block IDs are not identities."""
    value = _without_paths(validate_resource(card)['original'])
    names = {row['id']: 'CB-' + digest({'position': i})[:32]
             for i, row in enumerate(value['blocks'])}
    for row in value['blocks']:
        row['id'] = names[row['id']]
        for picture in row.get('presentation', {}).get('images', []):
            picture['image_block_id'] = names[picture['image_block_id']]
    return value


def _restore_original(value, path='retained-source-provenance'):
    value = deepcopy(value)
    evidence = value['evidence']; evidence['source_path'] = path
    if 'concept_source' in evidence:
        evidence['concept_source']['record']['source_path'] = path
    if 'handout_source' in evidence:
        evidence['handout_source']['source_path'] = path
    return value


def _card(original, path='retained-source-provenance'):
    value = _restore_original(original, path)
    return resource(value['kind'], value['title'], value['blocks'], value['evidence'])


def _validate_selection(kind, selected):
    _require(isinstance(selected, dict))
    if kind == 'registered_concept':
        _require(set(selected) == {'kind', 'concept_id', 'revision', 'directory_revision', 'location'}
                 and selected['kind'] == 'textbook_concept' and _text(selected['concept_id'])
                 and _hash(selected['revision']) and _hash(selected['directory_revision']))
        location = selected['location']
        _require(isinstance(location, dict) and set(location) ==
                 {'volume_id', 'chapter_id', 'section_key', 'supplement_node_key', 'scope_revision'})
        _require(all(item is None or _text(item) for key, item in location.items() if key != 'scope_revision')
                 and (location['scope_revision'] is None or _hash(location['scope_revision'])))
    elif kind == 'reading_note':
        _require(set(selected) == {'note_id', 'section_key', 'revision', 'directory_revision'}
                 and _text(selected['note_id']) and _text(selected['section_key'])
                 and _hash(selected['revision']) and _hash(selected['directory_revision']))
    elif kind == 'personal_note':
        _require(set(selected) == {'kind', 'book', 'study_revision', 'note'} and selected['kind'] == 'personal'
                 and _hash(selected['study_revision']))
        _require(isinstance(selected['book'], dict) and set(selected['book']) == {'source_id', 'revision'}
                 and _text(selected['book']['source_id']) and _hash(selected['book']['revision']))
        _require(isinstance(selected['note'], dict) and set(selected['note']) == {'note_id', 'revision'}
                 and _text(selected['note']['note_id']) and _hash(selected['note']['revision']))
    else:
        from .desktop_handout_knowledge import validate_handout_knowledge_source, _section
        _require(set(selected) == {'source', 'outline_revision', 'section'} and _hash(selected['outline_revision']))
        _require(isinstance(selected['source'], dict) and 'source_path' not in selected['source'])
        validate_handout_knowledge_source({**selected['source'], 'source_path': 'retained-source-provenance'})
        _section(selected['section'])


@_strict
def validate_knowledge_endpoint(value):
    _bounded(value)
    _require(isinstance(value, dict) and set(value) == _KNOWLEDGE
             and value['kind'] in KINDS and _text(value['id'], 300)
             and _hash(value['source_sha256']) and _hash(value['source_revision'])
             and _text(value['title'], 2000) and _hash(value['content_sha256']))
    _require(_without_paths(value) == value, '关系身份不能依赖本机来源路径。')
    _validate_selection(value['kind'], value['selection'])
    original = value['original']; structural = deepcopy(original)
    if value['kind'] == 'registered_concept':
        # The upstream record revision includes its old provenance path. The
        # relation retains that revision, but paths do not enter its identity.
        from .desktop_preparation_sources import _digest
        structural['evidence']['source_revision'] = _digest(
            _restore_original(structural)['evidence']['concept_source']['record'])
    card = _card(structural); normalized = _original(card)
    normalized['evidence']['source_revision'] = original['evidence']['source_revision']
    _require(normalized == original and digest(original) == value['content_sha256']
             and original['title'] == value['title'])
    evidence = original['evidence']
    _require(evidence['source_sha256'] == value['source_sha256']
             and evidence['source_revision'] == value['source_revision'])
    selected, kind = value['selection'], value['kind']
    if kind == 'handout_section':
        snapshot = evidence['section_snapshot']
        _require(original['kind'] == 'handout_knowledge' and selected['source'] == evidence['handout_source']
                 and selected['section'] == snapshot['section']
                 and selected['outline_revision'] == snapshot['outline_revision']
                 and value['id'] == 'HANDOUTSECTION-' + digest({'source_sha256': value['source_sha256'],
                                                             'section': snapshot['section']}))
    else:
        _require(original['kind'] == 'knowledge_note' and evidence['record_id'] == value['id'])
        if kind == 'registered_concept':
            _require('concept_source' in evidence and selected['concept_id'] == value['id']
                     and selected['revision'] == value['source_revision']
                     and selected['directory_revision'] == evidence['concept_source']['directory_revision']
                     and selected['location'] == evidence['concept_source']['location'])
        elif kind == 'reading_note':
            _require('concept_source' not in evidence and selected['note_id'] == value['id']
                     and selected['revision'] == value['source_revision'])
        else:
            _require('concept_source' not in evidence and selected['note']['note_id'] == value['id']
                     and selected['note']['revision'] == value['source_revision']
                     and selected['book']['revision'] == value['source_sha256'])
    return deepcopy(value)


@_strict
def validate_question_endpoint(value):
    _require(isinstance(value, dict) and set(value) == _QUESTION)
    _require(all(_text(value[key], 2000 if key == 'source_name' else 256) for key in
                 ('key', 'extraction_revision', 'batch_id', 'archive_source_id', 'source_name'))
             and value['source_role'] in {'handout', 'question', 'answer'}
             and all(_hash(value[key]) for key in ('revision', 'source_sha256', 'source_revision',
                                                  'preview_revision', 'scope_review_sha256'))
             and value['source_revision'] == value['preview_revision'])
    ranges = value['ranges']
    _require(isinstance(ranges, dict) and set(ranges) == set(_RANGES)
             and all(item is None or type(item) is int and item >= 1 for item in ranges.values())
             and all(type(ranges[key]) is int for key in ('block_start', 'question_end', 'answer_start', 'block_end', 'origin_block_start'))
             and ranges['block_start'] <= ranges['question_end']
             and ranges['answer_start'] == ranges['question_end'] + 1 <= ranges['block_end']
             and ((ranges['context_start'] is None and ranges['context_end'] is None)
                  or type(ranges['context_start']) is int and type(ranges['context_end']) is int
                  and ranges['context_start'] <= ranges['context_end']
                  and (ranges['context_end'] < ranges['block_start'] or ranges['context_start'] > ranges['block_end'])))
    return deepcopy(value)


def _link_id(knowledge, question):
    return 'KQL-' + digest({'knowledge': knowledge, 'question': question})


def empty_collection():
    return {'kind': 'knowledge_question_links', 'schema_version': SCHEMA, 'records': []}


@_strict
def validate_collection(value):
    """Strict, pure transfer/storage validator; this grants no endpoint validity."""
    _bounded(value)
    _require(isinstance(value, dict) and set(value) == {'kind', 'schema_version', 'records'}
             and value['kind'] == 'knowledge_question_links' and value['schema_version'] == SCHEMA
             and isinstance(value['records'], list) and len(value['records']) <= MAX_RECORDS)
    seen = set()
    for row in value['records']:
        _require(isinstance(row, dict) and set(row) ==
                 _RECORD)
        knowledge, question = validate_knowledge_endpoint(row['knowledge']), validate_question_endpoint(row['question'])
        _require(row['id'] == _link_id(knowledge, question) and row['id'] not in seen
                 and _text(row['reason'], MAX_REASON, empty=True) and type(row['removed']) is bool
                 and type(row['version']) is int and isinstance(row['history'], list)
                 and 1 <= row['version'] == len(row['history']) <= MAX_HISTORY)
        seen.add(row['id']); removed = False
        for position, history in enumerate(row['history'], 1):
            _require(isinstance(history, dict) and set(history) == {'version', 'action', 'reason', 'removed'}
                     and type(history['version']) is int and history['version'] == position
                     and history['action'] in {'save', 'edit_reason', 'remove'}
                     and _text(history['reason'], MAX_REASON, empty=True) and type(history['removed']) is bool)
            _require((position != 1 or history['action'] == 'save')
                     and (history['action'] != 'remove' or not removed)
                     and history['removed'] == (True if history['action'] == 'remove' else
                         False if history['action'] == 'save' else removed))
            removed = history['removed']
        _require(row['reason'] == row['history'][-1]['reason'] and row['removed'] == removed)
    return deepcopy(value)


def _snapshot(draft):
    return {'revision': draft.revision, 'content_sha256': draft.content_sha256,
            'collection': empty_collection() if draft.record is None else validate_collection(draft.record)}


def _same_expected(left, right):
    _require(isinstance(right, dict) and set(right) == {'revision', 'content_sha256', 'collection'})
    validate_collection(right['collection'])
    if left != right:
        error = DraftConflictError('关系集合已被其他操作修改，请重新读取。')
        error.message_zh = str(error)
        raise error


def _scope(words, row, source, preview):
    """Exactly the completeness contract used by complete-question course import."""
    from .desktop_word_question_attributes import validate_attributes, suggest_attributes
    from .offline_word_image_label_review import _native_scope
    from .offline_word_label_review import _source_blocks, _digest
    stored = words.attribute_store.get(row['key'], source_sha256=row['source_sha256'],
                                      question_revision=row['revision'])
    attributes = validate_attributes(stored if stored is not None else
                                     suggest_attributes(row, {'source_name': row['source_name']}))
    if any(block.get('assets') for name in ('context_blocks', 'question_blocks', 'answer_blocks')
           for block in row[name]):
        scope, _payloads = _native_scope(words, row, attributes, preview, source)
    else:
        _source_blocks(row, attributes, preview, mode='recheck_automatic')
        _payloads = {}
        scope = {'identity': {key: row[key] for key in ('key', 'revision', 'source_sha256', 'source_revision')},
                 'blocks': {name: deepcopy(row[name]) for name in ('context_blocks', 'question_blocks', 'answer_blocks')}}
    return _digest(scope), scope, _payloads


def _course_blocks(original, scope, payloads, source):
    from .desktop_composition_inline import NativeParagraphDisplay
    from .desktop_lesson_composition import block
    display = NativeParagraphDisplay(source.content); blocks = []
    for group, role in (('context_blocks', 'shared_material'), ('question_blocks', 'question'), ('answer_blocks', 'answer')):
        for native in scope['blocks'][group]:
            text = block('text', role, native['text'], native['index']); blocks.append(text)
            objects = [obj for obj in scope.get('objects', []) if obj['block_index'] == native['index']]
            assets = ([next(asset for asset in native['assets'] if asset['asset_id'] == obj['asset_id']) for obj in objects]
                      if objects else native.get('assets', []))
            pictures = [block('image', role, 'IMG-' + sha256(payloads[asset['asset_id']]).hexdigest(), native['index'])
                        for asset in assets]
            blocks.extend(pictures); display.bind(text, pictures, objects)
    current = resource('complete_question', original['title'], blocks,
                       _restore_original(original)['evidence'])
    retained = resource('complete_question', original['title'], original['blocks'], original['evidence'])
    _require(_original(current)['blocks'] == _original(retained)['blocks'],
             '课程完整题封存块或原图角色与实际原题不一致。')


def verified_handout_section(facade, card):
    """Recompile sealed text/table/picture occurrences without saving image copies."""
    from . import desktop_handout_knowledge as handout
    from .desktop_composition_inline import NativeParagraphDisplay
    from .desktop_preparation_images import verify_image_bytes
    from .desktop_preparation_sources import _local
    from .desktop_word_source_reference import WordSourceReferenceService
    original = validate_resource(card)['original']
    _require(original['kind'] == 'handout_knowledge')
    evidence = original['evidence']; bound = evidence['handout_source']; saved = evidence['section_snapshot']
    outline, source = handout._outline(facade, bound)
    _require(handout._request_matches_outline(saved, outline, source),
             '讲义栏目范围或标题索引已变化。')
    section = saved['section']; handout._check_roles(facade, outline['source'], outline['preview'], section)
    selection = {key: bound[key] for key in ('batch_id', 'source_id', 'source_sha256')}
    selection.update(revision=bound['source_revision'], block_start=section['start'], block_end=section['end'])
    compiled, payloads, held = WordSourceReferenceService(facade)._compile(
        selection, include_images=True, include_guidance=False)
    _require(not held and not compiled['image_issues'] and not compiled['reference_issues'],
             '讲义栏目有来源问题或原图缺口。')
    display = NativeParagraphDisplay(source.content); display.preview_assets = outline['preview']['assets']
    blocks = []; sealed = {row['source_index']: row for row in original['blocks'] if row['kind'] == 'text'}
    for native in outline['preview']['blocks'][section['start'] - 1:section['end']]:
        element = display.elements[native['index'] - 1]
        _require(_local(element) in {'p', 'tbl'}, '讲义栏目正文容器尚不能完整读取。')
        refs = deepcopy([ref for ref in compiled['image_references'] if ref['block_index'] == native['index']])
        objects = handout._occurrence_objects(display, native, refs)
        _require(handout._MARKER.findall(native['text']) == [obj['placeholder'] for obj in objects])
        row = {'index': native['index'], 'text': native['text'], 'images': refs}
        if _local(element) == 'tbl':
            row['table_rows'] = handout._table_snapshot(element, display.doc, native['text'])
        else:
            text = deepcopy(sealed[native['index']])
            pictures = [deepcopy(b) for b in original['blocks'] if b['kind'] == 'image'
                        and b['source_index'] == native['index']]
            display.bind(text, pictures, objects)
            _require(text == sealed[native['index']], '讲义段落原图和行内显示依据已变化。')
        blocks.append(row)
    _require({**saved, 'blocks': blocks} == saved, '讲义封存文字、表格或原图出现位置与原件不一致。')
    _require({asset['asset_id'] for asset in compiled['image_assets']} ==
             {asset['asset_id'] for asset in evidence['image_assets']})
    for asset in evidence['image_assets']:
        verify_image_bytes(asset, payloads[asset['sha256']])
    # A callback/native read must not publish a context after the archive changed.
    current = facade._imported_word_source(bound['batch_id'], bound['source_id'])
    _require(current.effective_source_file_id == bound['source_id'] and current.role == bound['source_role']
             and current.filename == bound['source_name'] and current.source_sha256 == bound['source_sha256']
             and sha256(current.content).hexdigest() == bound['source_sha256'])
    check_read_cancelled()
    # A compatible directory expansion never rewrites a sealed card's identity.
    return {'outline_revision': saved['outline_revision'], 'section': deepcopy(section)}


class _PersonalKnowledgePorts:
    """An unavailable registered shelf cannot hide a separately saved BNOTE.

    Only the registered metadata branch is normalized. The existing personal
    book bytes, study/note versions, content and context checks still run.
    """
    def __init__(self, facade): self.facade = facade
    def __getattr__(self, name): return getattr(self.facade, name)
    def textbook_bookshelf(self):
        snapshot = self.facade.textbook_bookshelf()
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('reading_notes'), list):
            return {'reading_notes': [], 'sections': []}
        return snapshot


class KnowledgeQuestionLinks:
    def __init__(self, facade):
        self.facade = facade; self.words = facade._word_questions(); self.store = facade.state_store

    def snapshot(self):
        check_read_cancelled()
        return _snapshot(self.store.draft_snapshot(KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY))

    def choices(self, resources=None):
        options = knowledge_options(self.facade); rows = []
        for row in [*options['concepts'], *options['reading_notes']]:
            selection = knowledge_selection(row)
            kind = ('registered_concept' if selection.get('kind') == 'textbook_concept' else
                    'personal_note' if selection.get('kind') == 'personal' else 'reading_note')
            rows.append({**deepcopy(row), 'kind': kind, 'selection': selection})
        for card in resources or []:
            value = validate_resource(card)
            if value['original']['kind'] == 'handout_knowledge':
                source = value['original']['evidence']['handout_source']
                section = value['original']['evidence']['section_snapshot']['section']
                rows.append({'kind': 'handout_section', 'origin': 'handout_section', 'title': value['title'],
                    'card': value, 'selection': value, 'book_key': 'handout:' + source['source_sha256'],
                    'section_filter_key': 'handout:' + section['id'], 'volume_title': source['source_name'],
                    'section_title': section['title'], 'summary': '\n'.join(b['value'] for b in value['original']['blocks']
                                                                          if b['kind'] == 'text')})
        check_read_cancelled()
        return {'items': rows, 'notices': []}

    @_source_errors
    def knowledge_context(self, knowledge):
        check_read_cancelled()
        with self.words._lock:
            if isinstance(knowledge, dict) and set(knowledge) == _KNOWLEDGE:
                expected = validate_knowledge_endpoint(knowledge)
                if expected['kind'] == 'handout_section':
                    source = expected['selection']['source']
                    path = self.facade.imported_word_path(source['batch_id'], source['source_id'])
                    card = _card(expected['original'], path)
                else:
                    card = import_knowledge_note(self.facade, expected['selection'])
            else:
                expected = None
                if isinstance(knowledge, dict) and 'original' in knowledge:
                    card = validate_resource(knowledge)
                else:
                    card = import_knowledge_note(self.facade, knowledge)
            original = card['original']; evidence = original['evidence']
            if original['kind'] == 'handout_knowledge':
                browser = verified_handout_section(self.facade, card); kind = 'handout_section'
                selected = {'source': _without_paths(evidence['handout_source']),
                            'outline_revision': browser['outline_revision'], 'section': browser['section']}
                identity = 'HANDOUTSECTION-' + digest({'source_sha256': evidence['source_sha256'],
                                                     'section': browser['section']})
            else:
                ports = (_PersonalKnowledgePorts(self.facade) if str(evidence.get('record_id', '')).startswith('BNOTE-')
                         and 'concept_source' not in evidence else self.facade)
                browser = knowledge_question_context(ports, card)
                selected, _request = _selection_and_request(ports, card)
                kind = ('registered_concept' if selected.get('kind') == 'textbook_concept' else
                        'personal_note' if selected.get('kind') == 'personal' else 'reading_note')
                identity = evidence['record_id']
            authored = _original(card)
            endpoint = validate_knowledge_endpoint({'kind': kind, 'id': identity,
                'source_sha256': evidence['source_sha256'], 'source_revision': evidence['source_revision'],
                'selection': selected, 'title': original['title'], 'original': authored,
                'content_sha256': digest(authored)})
            _require(expected is None or endpoint == expected, '知识内容、选择范围或版本已变化，旧关系不会重绑。')
            check_read_cancelled()
            return {'endpoint': endpoint, 'card': deepcopy(card), 'browser_context': deepcopy(browser)}

    def question_locations(self, course_question):
        """Metadata locators only; every chosen archive is independently rechecked."""
        original = validate_resource(course_question)['original']
        _require(original['kind'] == 'complete_question'); rows = []
        for descriptor in self.store.snapshot()['drafts'].values():
            if not isinstance(descriptor, dict) or descriptor.get('kind') != 'desktop_visual_import_v2': continue
            for source in descriptor.get('sources', []):
                if (isinstance(source, dict) and source.get('source_sha256') == original['evidence']['source_sha256']
                        and str(source.get('filename', '')).casefold().endswith('.docx')):
                    rows.append({'label': str(source['filename']) + ' · ' + str(descriptor['batch_id']),
                                 'batch_id': descriptor['batch_id'], 'archive_source_id': source['source_file_id']})
        check_read_cancelled(); return deepcopy(rows)

    @_source_errors
    def _question(self, chosen, location=None):
        check_read_cancelled()
        if isinstance(chosen, dict) and 'question' in chosen:
            if _RECORD <= set(chosen):
                retained = {key: deepcopy(chosen[key]) for key in _RECORD}
                validate_collection({**empty_collection(), 'records': [retained]})
                current = [row for row in self.snapshot()['collection']['records'] if row['id'] == retained['id']]
                _require(len(current) == 1 and current[0] == retained and not retained['removed'],
                         '这条关系已解除或记录已变化，请刷新；不能据旧关系选用原题。')
            chosen = chosen['question']
        sealed = None
        if isinstance(chosen, dict) and 'original' in chosen:
            sealed = validate_resource(chosen)['original']
            _require(sealed['kind'] == 'complete_question')
            evidence = sealed['evidence']; key = evidence['question_key']; revision = None
            if location is None:
                locations = [{key: row[key] for key in ('batch_id', 'archive_source_id')}
                             for row in self.question_locations(chosen)]
                _require(len(locations) == 1, '课程题目存在缺失或多个同字节归档，请明确选择原批次和文件。')
                location = locations[0]
        else:
            _require(isinstance(chosen, dict) and _text(chosen.get('key')) and _hash(chosen.get('revision')))
            key, revision = chosen['key'], chosen['revision']
            if location is None and all(chosen.get(k) for k in ('batch_id', 'archive_source_id')):
                location = {k: chosen[k] for k in ('batch_id', 'archive_source_id')}
            if location is None:
                pair = self.words._locations.get(key)
                _require(pair is not None, '请从当前原Word题目入口明确选择题目归档。')
                location = dict(zip(('batch_id', 'archive_source_id'), pair))
        _require(isinstance(location, dict) and set(location) == {'batch_id', 'archive_source_id'}
                 and all(_text(value) for value in location.values()))
        source = self.facade._imported_word_source(location['batch_id'], location['archive_source_id'])
        content_sha = sha256(source.content).hexdigest()
        _require(source.effective_source_file_id == location['archive_source_id'] and source.source_sha256 == content_sha)
        catalog, inventory = self.words._catalog({location['batch_id']: {location['archive_source_id']}}, read_only=True)
        found = inventory.get(content_sha)
        _require(found is not None and len(inventory) == 1, '原Word归档已缺失或变化。')
        native, batch, preview, _indexed = found
        _require(batch == location['batch_id'] and native.effective_source_file_id == location['archive_source_id']
                 and native.filename == source.filename and native.role == source.role
                 and native.source_sha256 == content_sha == sha256(native.content).hexdigest()
                 and preview['source_sha256'] == content_sha and preview['source_name'] == source.filename)
        matches = [row for row in catalog['items'] if row['key'] == key]
        _require(len(matches) == 1, '原Word中的完整题已缺失或范围已变化。')
        row = matches[0]
        _require(revision is None or row['revision'] == revision, '完整题内容或范围版本已变化。')
        _require(row['batch_id'] == batch and row['archive_source_id'] == location['archive_source_id']
                 and row['source_sha256'] == content_sha and row['source_id'] == content_sha
                 and row['source_revision'] == preview['revision'])
        scope_sha, scope, payloads = _scope(self.words, row, native, preview)
        endpoint = validate_question_endpoint({key: row[key] for key in
            ('key', 'revision', 'source_sha256', 'source_revision', 'extraction_revision', 'batch_id',
             'archive_source_id', 'source_name')} | {'source_role': source.role,
                'preview_revision': preview['revision'], 'ranges': {key: row.get(key) for key in _RANGES},
                'scope_review_sha256': scope_sha})
        if sealed is not None:
            _require(all(endpoint[key] == sealed['evidence'][key] for key in
                         ('source_sha256', 'source_revision', 'scope_review_sha256')),
                     '课程完整题封存范围与当前原题不一致，不能补猜题目版本。')
            _course_blocks(sealed, scope, payloads, native)
        elif set(chosen) == _QUESTION:
            _require(validate_question_endpoint(chosen) == endpoint, '完整题来源、范围或原图已变化。')
        else:
            _require(all(chosen[key] == row.get(key) for key in (*_RANGES, 'source_sha256', 'source_revision',
                         'batch_id', 'archive_source_id', 'source_name') if key in chosen), '选题范围或来源证据失配。')
        current = self.facade._imported_word_source(batch, location['archive_source_id'])
        _require(current.effective_source_file_id == location['archive_source_id'] and current.role == source.role
                 and current.filename == source.filename and current.source_sha256 == content_sha
                 and sha256(current.content).hexdigest() == content_sha, '原题在核对期间已变化。')
        final_catalog, final_inventory = self.words._catalog({batch: {location['archive_source_id']}}, read_only=True)
        final_rows = [value for value in final_catalog['items'] if value['key'] == key]
        _require(len(final_rows) == 1 and all(final_rows[0].get(name) == row.get(name)
                 for name in ('revision', 'source_revision', 'source_sha256', 'batch_id', 'archive_source_id',
                              'source_name', 'extraction_revision',
                              *_RANGES, 'context_blocks', 'question_blocks', 'answer_blocks'))
                 and content_sha in final_inventory and final_inventory[content_sha][2] == preview,
                 '原题范围在核对期间已变化。')
        final_source, final_batch, _preview, _indexed = final_inventory[content_sha]
        _require(final_batch == batch and final_source.effective_source_file_id == location['archive_source_id']
                 and final_source.filename == source.filename and final_source.role == source.role
                 and final_source.source_sha256 == content_sha == sha256(final_source.content).hexdigest(),
                 '原题归档身份在核对期间已变化。')
        check_read_cancelled()
        return endpoint, deepcopy(row), deepcopy(preview)

    @_source_errors
    def resolve_selection(self, question, *, location=None):
        from .desktop_handout_questions import HandoutQuestionFacade
        with self.words._lock:
            endpoint, row, preview = self._question(question, location)
            bound = {'schema': 'shchem.handout-knowledge-source.v1', 'batch_id': endpoint['batch_id'],
                'source_id': endpoint['archive_source_id'], 'source_sha256': endpoint['source_sha256'],
                'source_name': endpoint['source_name'], 'source_role': endpoint['source_role'],
                'source_revision': endpoint['preview_revision'],
                'source_path': str(self.facade.imported_word_path(endpoint['batch_id'], endpoint['archive_source_id']))}
            self._question(endpoint)
            return {'selection': {key: endpoint[key] for key in ('key', 'revision')}, 'endpoint': endpoint,
                    'row': row, 'preview': preview, 'scoped_facade': HandoutQuestionFacade(self.facade, bound)}

    def prepare(self, knowledge, question, reason='', *, location=None):
        _require(_text(reason, MAX_REASON, empty=True), '关联理由不能超过4000字。')
        with self.words._lock:
            context = self.knowledge_context(knowledge); endpoint, _row, _preview = self._question(question, location)
            self.knowledge_context(context['endpoint']); self._question(endpoint)
            result = {'schema_version': PREPARED_SCHEMA, 'knowledge': context['endpoint'], 'question': endpoint,
                      'reason': reason, 'knowledge_card': context['card'],
                      'selection': {key: endpoint[key] for key in ('key', 'revision')}}
            result['prepared_sha256'] = digest(result)
            check_read_cancelled(); return deepcopy(result)

    def _cas(self, collection, expected, *, guard=None):
        before = self.store.draft_snapshot(KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY)
        _same_expected(_snapshot(before), expected)
        def checked(_state):
            if guard is not None: guard()
            check_read_cancelled()
        # The native word lock is always acquired before the state publication lock.
        with publication_guard():
            collection = validate_collection(collection)
            if before.record is None and collection == empty_collection():
                def absent(state):
                    _same_expected(_snapshot(_draft_snapshot(state, KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY)), expected)
                    checked(state); return False
                state = self.store._update(absent)
                current = _draft_snapshot(state, KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY)
            else:
                try:
                    current = self.store.compare_draft(before, collection, guard=checked)
                except DraftConflictError as exc:
                    exc.message_zh = str(exc); raise
        return _snapshot(current)

    def save(self, prepared, *, expected=None, guard=None):
        _require(isinstance(prepared, dict) and set(prepared) ==
                 {'schema_version', 'knowledge', 'question', 'reason', 'knowledge_card', 'selection', 'prepared_sha256'}
                 and prepared['schema_version'] == PREPARED_SCHEMA
                 and digest({key: value for key, value in prepared.items() if key != 'prepared_sha256'}) == prepared['prepared_sha256'])
        knowledge = validate_knowledge_endpoint(prepared['knowledge']); question = validate_question_endpoint(prepared['question'])
        _require(_text(prepared['reason'], MAX_REASON, empty=True)
                 and _original(prepared['knowledge_card']) == knowledge['original']
                 and prepared['selection'] == {key: question[key] for key in ('key', 'revision')})
        with self.words._lock:
            self.knowledge_context(knowledge); self._question(question)
            expected = self.snapshot() if expected is None else deepcopy(expected)
            collection = validate_collection(expected['collection']); identity = _link_id(knowledge, question)
            rows = [row for row in collection['records'] if row['id'] == identity]
            if rows:
                row = rows[0]
                if row['removed'] or row['reason'] != prepared['reason']:
                    self._append(row, 'save', prepared['reason'], False)
            else:
                row = {'id': identity, 'knowledge': knowledge, 'question': question,
                       'reason': prepared['reason'], 'version': 0, 'removed': False, 'history': []}
                self._append(row, 'save', prepared['reason'], False); collection['records'].append(row)
            def final():
                if guard is not None: guard()
                self.knowledge_context(knowledge); self._question(question); check_read_cancelled()
            return self._cas(collection, expected, guard=final)

    @staticmethod
    def _append(row, action, reason, removed):
        _require(row['version'] < MAX_HISTORY, '该关系的历史已达到容量，请保留后使用新版本端点。')
        row.update(version=row['version'] + 1, reason=reason, removed=removed)
        row['history'].append({'version': row['version'], 'action': action, 'reason': reason, 'removed': removed})

    def _maintain(self, identity, *, reason=None, remove=False, expected=None, guard=None):
        with self.words._lock:
            expected = self.snapshot() if expected is None else deepcopy(expected)
            collection = validate_collection(expected['collection'])
            rows = [row for row in collection['records'] if row['id'] == identity]
            _require(len(rows) == 1, '未找到要维护的关系记录。')
            row = rows[0]
            if remove and not row['removed']:
                self._append(row, 'remove', row['reason'], True)
            elif not remove and row['reason'] != reason:
                self._append(row, 'edit_reason', reason, row['removed'])
            return self._cas(collection, expected, guard=guard)

    def edit_reason(self, link_id, reason, *, expected=None, guard=None):
        _require(_text(reason, MAX_REASON, empty=True), '关联理由不能超过4000字。')
        return self._maintain(link_id, reason=reason, expected=expected, guard=guard)

    def remove(self, link_id, *, expected=None, guard=None):
        return self._maintain(link_id, remove=True, expected=expected, guard=guard)

    def list(self, knowledge=None, *, assess=False):
        rows = self.snapshot()['collection']['records']
        if knowledge is not None:
            if isinstance(knowledge, str):
                rows = [row for row in rows if row['knowledge']['id'] == knowledge]
            else:
                if 'original' in knowledge and 'id' in knowledge and 'source_sha256' not in knowledge:
                    original = _original(knowledge); evidence = original['evidence']
                    identity = (evidence.get('record_id') or 'HANDOUTSECTION-' + digest({
                        'source_sha256': evidence['source_sha256'], 'section': evidence['section_snapshot']['section']}))
                    source_sha = evidence['source_sha256']
                else:
                    endpoint = knowledge.get('endpoint', knowledge); identity = endpoint['id']; source_sha = endpoint['source_sha256']
                rows = [row for row in rows if row['knowledge']['id'] == identity and row['knowledge']['source_sha256'] == source_sha]
        return [self.evaluate(row['id']) if assess else self._unknown(row) for row in rows]

    def reverse(self, question, *, assess=False):
        if isinstance(question, str): key, source_sha = question, None
        elif 'original' in question:
            evidence = validate_resource(question)['original']['evidence']; key, source_sha = evidence['question_key'], evidence['source_sha256']
        else:
            endpoint = question.get('question', question.get('endpoint', question)); key, source_sha = endpoint['key'], endpoint.get('source_sha256')
        rows = [row for row in self.snapshot()['collection']['records'] if row['question']['key'] == key
                and (source_sha is None or row['question']['source_sha256'] == source_sha)]
        return [self.evaluate(row['id']) if assess else self._unknown(row) for row in rows]

    @staticmethod
    def _unknown(row):
        return {**deepcopy(row), 'status': 'unknown', 'knowledge_status': 'unknown', 'question_status': 'unknown'}

    def evaluate(self, link_id):
        rows = [row for row in self.snapshot()['collection']['records'] if row['id'] == link_id]
        _require(len(rows) == 1, '未找到关系记录。'); result = self._unknown(rows[0])
        with self.words._lock:
            for side, call in (('knowledge', lambda: self.knowledge_context(result['knowledge'])),
                               ('question', lambda: self._question(result['question']))):
                try:
                    call(); result[side + '_status'] = 'current'
                except (FileNotFoundError, KeyError, StopIteration): result[side + '_status'] = 'missing'
                except (OSError, AttributeError): result[side + '_status'] = 'unknown'
                except ValueError as exc:
                    code = getattr(exc, 'code', '')
                    message = str(exc)
                    cause = exc; missing_file = False
                    while cause is not None:
                        missing_file |= isinstance(cause, FileNotFoundError)
                        cause = cause.__cause__
                    result[side + '_status'] = ('missing' if missing_file or 'missing' in code else
                        'stale' if any(word in message for word in ('已变化', '不一致', '不符', '版本', '身份重复')) else
                        'missing' if any(word in message for word in ('缺失', '未找到', '已删除')) else 'stale')
            statuses = {result['knowledge_status'], result['question_status']}
            result['status'] = next((status for status in ('missing', 'stale', 'unknown') if status in statuses), 'current')
        return deepcopy(result)

    def plan_merge(self, incoming, *, expected=None):
        """Pure record merge after metadata snapshot; endpoint status stays unknown."""
        incoming = validate_collection(incoming); expected = self.snapshot() if expected is None else deepcopy(expected)
        local = validate_collection(expected['collection']); current = {row['id']: row for row in local['records']}
        new, duplicates, conflicts = [], [], []
        for row in incoming['records']:
            if row['id'] not in current:
                new.append(row['id']); local['records'].append(deepcopy(row))
            elif current[row['id']] == row: duplicates.append(row['id'])
            else: conflicts.append(row['id'])
        return {'expected_revision': expected['revision'], 'expected_content_sha256': expected['content_sha256'],
                'input_sha256': digest(incoming), 'incoming': incoming, 'collection': validate_collection(local),
                'new': new, 'duplicates': duplicates, 'conflicts': conflicts}

    def commit_merge(self, plan, *, expected=None, guard=None, keep_current=False):
        with self.words._lock:
            _require(isinstance(plan, dict) and set(plan) == {'expected_revision', 'expected_content_sha256',
                     'input_sha256', 'incoming', 'collection', 'new', 'duplicates', 'conflicts'})
            expected = self.snapshot() if expected is None else deepcopy(expected)
            _require(plan == self.plan_merge(plan['incoming'], expected=expected), '恢复计划或当前关系集合已变化。')
            _require(not plan['conflicts'] or keep_current is True, '恢复内容与已有关系历史冲突，请明确保留当前记录。')
            return self._cas(plan['collection'], expected, guard=guard)
