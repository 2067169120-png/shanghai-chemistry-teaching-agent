"""Independent relation transfers recheck bytes and addressed CAS before writing."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links as links
from integrations.deeptutor_shchem_v1 import desktop_knowledge_question_links_transfer as transfer
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block, digest
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DraftConflictError
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, read_cancel_scope)

# The two integration cases use existing source-bound endpoint fixtures. Most
# cases below exercise real desktop-state/CAS with small, strictly valid seals.
from test_knowledge_question_links import (handout_section, scoped_lecture,
    desktop_paths, notes, section_fixture, asset_fixture, shelf_fixture, concept,
    supplement, supplement_fixture, saved, combine)
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_knowledge_note


def relation(number=1, kind='reading_note', reason='这个知识解释完整题的条件。'):
    identity = 'SYNTHETIC-NOTE-' + str(number)
    book_sha = sha256(('book-' + str(number)).encode()).hexdigest()
    revision = sha256(('note-' + str(number)).encode()).hexdigest()
    card = resource('knowledge_note', '完整原知识' + str(number),
        [block('text', 'knowledge', '保留全部条件、结论和化学式SO₄²⁻。')],
        {'source_path': 'old-machine/book.pdf', 'source_sha256': book_sha,
         'source_revision': revision, 'record_id': identity, 'pdf_pages': [8], 'printed_pages': [4]})
    original = links._original(card)
    selection = ({'note_id': identity, 'section_key': 'SECTION', 'revision': revision,
                  'directory_revision': 'd' * 64} if kind == 'reading_note' else
                 {'kind': 'personal', 'book': {'source_id': 'BOOK', 'revision': book_sha},
                  'study_revision': 'd' * 64, 'note': {'note_id': identity, 'revision': revision}})
    knowledge = {'kind': kind, 'id': identity, 'source_sha256': book_sha,
        'source_revision': revision, 'selection': selection, 'title': original['title'],
        'original': original, 'content_sha256': digest(original)}
    question = {'key': 'Q-' + str(number), 'revision': 'e' * 64,
        'source_sha256': 'c' * 64, 'source_revision': 'b' * 64, 'preview_revision': 'b' * 64,
        'extraction_revision': 'word-questions-v1', 'batch_id': 'BATCH', 'archive_source_id': 'WORD',
        'source_name': '完整题.docx', 'source_role': 'handout',
        'ranges': {'block_start': 1, 'question_end': 2, 'answer_start': 3, 'block_end': 4,
                   'context_start': None, 'context_end': None, 'origin_block_start': 1},
        'scope_review_sha256': 'f' * 64}
    return {'id': links._link_id(knowledge, question), 'knowledge': knowledge, 'question': question,
        'reason': reason, 'version': 1, 'removed': False,
        'history': [{'version': 1, 'action': 'save', 'reason': reason, 'removed': False}]}


def collection(*records):
    value = links.empty_collection(); value['records'] = deepcopy(list(records))
    return links.validate_collection(value)


def service_at(root):
    words = SimpleNamespace(_lock=RLock())
    facade = SimpleNamespace(state_store=DesktopStateStore(root), _word_questions=lambda: words)
    return links.KnowledgeQuestionLinks(facade)


@pytest.fixture
def service(tmp_path):
    return service_at(tmp_path / 'isolated-profile')


def seed(service, value):
    service.store.save_draft(links.KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY, value)
    return service.snapshot()


def disk(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def packet(value):
    return {'schema_version': transfer.SCHEMA, 'collection': value, 'content_sha256': digest(value)}


def write_json(path, value, *, canonical=True):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=canonical,
        separators=(',', ':') if canonical else None), encoding='utf8')
    return path


def input_file(tmp_path, value):
    return write_json(tmp_path / 'relationships.json', packet(value))


def edited(record, reason='本机补充的关联理由', *, remove=False):
    value = deepcopy(record); links.KnowledgeQuestionLinks._append(value,
        'remove' if remove else 'edit_reason', value['reason'] if remove else reason, remove)
    return value


def test_export_preview_and_restore_only_addressed_collection(service, tmp_path, monkeypatch):
    first = relation(); before = seed(service, collection(first))
    service.store.save_draft('ordinary-preparation', {'kind': 'preparation', 'body': '保留课程'})
    export_before = disk(service.store.root)
    for name in ('knowledge_context', '_question'):
        monkeypatch.setattr(service, name, lambda *_: pytest.fail('transfer read an endpoint'))
    path = tmp_path / 'links.json'
    receipt = transfer.export_knowledge_question_links(service, path, expected_revision=before['revision'])
    assert receipt['written'] and receipt['records_count'] == 1
    assert receipt['file_sha256'] == sha256(path.read_bytes()).hexdigest()
    assert disk(service.store.root) == export_before
    value = json.loads(path.read_text('utf8'))
    assert set(value) == {'schema_version', 'collection', 'content_sha256'}
    assert value['collection'] == before['collection'] and 'source_path' not in path.read_text('utf8')
    fresh = service_at(tmp_path / 'new-profile')
    fresh.store.save_draft('ordinary-preparation', {'kind': 'preparation', 'body': '不能被替换'})
    unrelated = fresh.store.snapshot()['drafts']['ordinary-preparation']
    sentinel = fresh.store.root / 'question-attributes.json'; sentinel.write_bytes(b'attributes-not-restored')
    sentinel_before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
    writes = []; real_write = fresh.store._write_unlocked
    monkeypatch.setattr(fresh.store, '_write_unlocked', lambda *a, **k: (writes.append('write'), real_write(*a, **k))[1])
    plan = transfer.inspect_knowledge_question_links(fresh, path)
    assert plan['counts'] == {'new': 1, 'duplicates': 0, 'conflicts': 0}
    assert plan['new_links'] == [first['id']] and plan['endpoint_status'] == 'pending'
    assert not writes
    result = transfer.restore_knowledge_question_links(fresh, path, plan)
    assert result['result']['collection'] == before['collection'] and result['new_count'] == 1
    assert result['endpoint_status'] == 'pending' and writes == ['write']
    assert fresh.store.snapshot()['drafts']['ordinary-preparation'] == unrelated
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == sentinel_before
    assert set(fresh.store.snapshot()['drafts']) == {'ordinary-preparation', links.KNOWLEDGE_QUESTION_LINKS_DRAFT_KEY}


def test_empty_restore_does_not_create_profile_or_draft(service, tmp_path, monkeypatch):
    path = input_file(tmp_path, collection()); before = service.snapshot()
    monkeypatch.setattr(service.store, '_write_unlocked', lambda *a, **k: pytest.fail('empty restore wrote'))
    plan = transfer.inspect_knowledge_question_links(service, path)
    calls = []
    result = transfer.restore_knowledge_question_links(service, path, plan, guard=lambda: calls.append('guard'))
    assert result['result'] == before and calls == ['guard'] and not service.store.root.exists()


def test_duplicate_restore_and_repeat_export_are_zero_writes(service, tmp_path, monkeypatch):
    seed(service, collection(relation()))
    path = tmp_path / 'same.json'; first = transfer.export_knowledge_question_links(service, path)
    bytes_and_time = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(transfer.os, 'replace', lambda *a: pytest.fail('identical export replaced file'))
    assert transfer.export_knowledge_question_links(service, path)['written'] is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == bytes_and_time
    before = disk(service.store.root)
    monkeypatch.setattr(service.store, '_write_unlocked', lambda *a, **k: pytest.fail('duplicate restore wrote'))
    for _ in range(2):
        plan = transfer.inspect_knowledge_question_links(service, path)
        result = transfer.restore_knowledge_question_links(service, path, plan)
        assert result['new_count'] == 0 and result['duplicate_count'] == 1
    assert disk(service.store.root) == before and first['content_sha256'] == plan['input_digest']


@pytest.mark.parametrize('policy', ['block', 'keep_current'])
def test_mixed_preview_and_explicit_conflict_policy(service, tmp_path, policy):
    base, duplicate, new = relation(1), relation(2), relation(3)
    current = edited(base); seed(service, collection(current, duplicate))
    path = input_file(tmp_path, collection(base, duplicate, new))
    plan = transfer.inspect_knowledge_question_links(service, path); before = disk(service.store.root)
    assert plan['counts'] == {'new': 1, 'duplicates': 1, 'conflicts': 1}
    assert plan['conflicts'] == [base['id']] and plan['new_links'] == [new['id']]
    assert plan['conflict_details'] == [{'id': base['id'], 'current_reason': current['reason'],
        'incoming_reason': base['reason'], 'current_version': 2, 'incoming_version': 1,
        'current_removed': False, 'incoming_removed': False}]
    if policy == 'block':
        with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='冲突'):
            transfer.restore_knowledge_question_links(service, path, plan)
        assert disk(service.store.root) == before
    else:
        result = transfer.restore_knowledge_question_links(service, path, plan, conflict_policy=policy)
        assert result['kept_current_conflicts'] is True
        assert result['result']['collection'] == collection(current, duplicate, new)


def test_tombstone_history_is_never_silently_revived(service, tmp_path):
    base = relation(); removed = edited(edited(base), remove=True)
    seed(service, collection(removed)); path = input_file(tmp_path, collection(base))
    plan = transfer.inspect_knowledge_question_links(service, path); before = disk(service.store.root)
    assert plan['conflict_details'][0]['current_removed'] is True
    transfer.restore_knowledge_question_links(service, path, plan, conflict_policy='keep_current')
    assert disk(service.store.root) == before and service.snapshot()['collection'] == collection(removed)


def test_restored_missing_evidence_is_pending_until_explicit_recheck(service, tmp_path, monkeypatch):
    row = relation(); path = input_file(tmp_path, collection(row))
    def missing(*_): raise FileNotFoundError('missing original')
    monkeypatch.setattr(service, 'knowledge_context', missing); monkeypatch.setattr(service, '_question', missing)
    result = transfer.restore_knowledge_question_links(service, path,
        transfer.inspect_knowledge_question_links(service, path))
    before = disk(service.store.root)
    assert result['endpoint_status'] == 'pending' and service.list()[0]['status'] == 'unknown'
    assert service.evaluate(row['id'])['status'] == 'missing'
    def stale(*_): raise ValueError('原文版本已变化')
    monkeypatch.setattr(service, 'knowledge_context', stale)
    monkeypatch.setattr(service, '_question', lambda *_: None)
    assert service.evaluate(row['id'])['status'] == 'stale'
    monkeypatch.setattr(service, 'knowledge_context', lambda *_: None)
    assert service.evaluate(row['id'])['status'] == 'current'
    assert disk(service.store.root) == before and service.snapshot()['collection'] == collection(row)


def test_moved_json_restores_without_original_machine_paths(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    moved = tmp_path / 'different-name.json'; path.replace(moved)
    result = transfer.restore_knowledge_question_links(service, moved, plan)
    assert result['new_count'] == 1 and 'old-machine' not in moved.read_text('utf8')


@pytest.mark.parametrize('kind', ['reading_note', 'personal_note'])
def test_pure_supported_note_endpoint_roundtrip(service, tmp_path, kind):
    row = relation(kind=kind); path = input_file(tmp_path, collection(row))
    result = transfer.restore_knowledge_question_links(service, path,
        transfer.inspect_knowledge_question_links(service, path))
    assert result['result']['collection']['records'][0]['knowledge']['kind'] == kind


def test_actual_registered_concept_transfer_preserves_fine_identity(scoped_lecture, concept, tmp_path):
    concept.item = import_knowledge_note(concept.facade, concept.option['selection'])
    f = combine(scoped_lecture, concept); snapshot, row = saved(f)
    before = disk(f.facade.paths.state_root); path = tmp_path / 'concept-relations.json'
    transfer.export_knowledge_question_links(f.service, path)
    fresh = service_at(tmp_path / 'restored-concept-profile')
    result = transfer.restore_knowledge_question_links(fresh, path,
        transfer.inspect_knowledge_question_links(fresh, path))
    assert result['result']['collection'] == snapshot['collection']
    assert row['knowledge']['kind'] == 'registered_concept'
    assert disk(f.facade.paths.state_root) == before and 'source_path' not in path.read_text('utf8')


def test_actual_handout_section_transfer_preserves_images_tables_and_roles(handout_section, tmp_path):
    f = handout_section; snapshot, row = saved(f); before = disk(f.facade.paths.state_root)
    path = tmp_path / 'handout-relations.json'; transfer.export_knowledge_question_links(f.service, path)
    fresh = service_at(tmp_path / 'restored-handout-profile')
    result = transfer.restore_knowledge_question_links(fresh, path,
        transfer.inspect_knowledge_question_links(fresh, path))
    assert result['result']['collection'] == snapshot['collection']
    blocks = row['knowledge']['original']['evidence']['section_snapshot']['blocks']
    assert sum(len(b['images']) for b in blocks) == 2 and any('table_rows' in b for b in blocks)
    assert row['knowledge']['kind'] == 'handout_section' and disk(f.facade.paths.state_root) == before


@pytest.mark.parametrize('change', ['schema', 'extra', 'missing', 'digest', 'not_object',
    'duplicate_key', 'nonfinite', 'invalid_utf8', 'truncated', 'nested_duplicate'])
def test_invalid_json_never_mutates_store(service, tmp_path, change):
    value = packet(collection(relation())); raw = json.dumps(value).encode()
    if change == 'schema': value['schema_version'] = 'future'; raw = json.dumps(value).encode()
    elif change == 'extra': value['sources'] = {'word_bytes': 'not allowed'}; raw = json.dumps(value).encode()
    elif change == 'missing': value.pop('content_sha256'); raw = json.dumps(value).encode()
    elif change == 'digest': value['content_sha256'] = 'a' * 64; raw = json.dumps(value).encode()
    elif change == 'not_object': raw = b'[]'
    elif change == 'duplicate_key': raw = b'{"schema_version":"x","schema_version":"y"}'
    elif change == 'nonfinite': raw = b'{"extra":NaN}'
    elif change == 'invalid_utf8': raw = b'\xff'
    elif change == 'truncated': raw = raw[:-7]
    else: raw = b'{"schema_version":"x","collection":{"records":[],"records":[]}}'
    path = tmp_path / 'invalid.json'; path.write_bytes(raw); before = disk(service.store.root)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError):
        transfer.inspect_knowledge_question_links(service, path)
    assert disk(service.store.root) == before


@pytest.mark.parametrize('change', ['duplicate_record', 'record_id', 'history', 'reason',
    'bool_range', 'endpoint_path', 'endpoint_extra', 'original_digest', 'removed', 'oversize_collection'])
def test_recomputed_envelope_digest_cannot_bypass_collection_validation(service, tmp_path, change):
    value = collection(relation()); row = value['records'][0]
    if change == 'duplicate_record': value['records'].append(deepcopy(row))
    elif change == 'record_id': row['id'] = 'KQL-' + '0' * 64
    elif change == 'history': row['history'][0]['version'] = 2
    elif change == 'reason': row['reason'] = 'x' * (links.MAX_REASON + 1)
    elif change == 'bool_range': row['question']['ranges']['block_start'] = True
    elif change == 'endpoint_path': row['knowledge']['selection']['source_path'] = 'old-machine.pdf'
    elif change == 'endpoint_extra': row['question']['attributes'] = {'official': True}
    elif change == 'original_digest': row['knowledge']['original']['blocks'][0]['value'] += 'tampered'
    elif change == 'removed': row['removed'] = True
    else: row['knowledge']['original']['blocks'][0]['value'] = 'x' * (links.MAX_BYTES + 1)
    path = input_file(tmp_path, value); before = disk(service.store.root)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError):
        transfer.inspect_knowledge_question_links(service, path)
    assert disk(service.store.root) == before


def test_file_size_cap_rejects_before_decode(service, tmp_path, monkeypatch):
    path = tmp_path / 'too-large.json'
    with path.open('wb') as stream: stream.truncate(transfer.MAX_TRANSFER_BYTES + 1)
    monkeypatch.setattr(transfer, '_decode', lambda *_: pytest.fail('oversize file decoded'))
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='16MiB'):
        transfer.inspect_knowledge_question_links(service, path)
    assert not service.store.root.exists()


def test_file_change_during_read_is_rejected(service, tmp_path, monkeypatch):
    path = input_file(tmp_path, collection(relation())); real = Path.open
    class ChangedRead:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, *args):
            raw = self.stream.read(*args)
            with real(path, 'ab') as writer: writer.write(b' ')
            return raw
    def open_file(target, mode='r', *args, **kwargs):
        stream = real(target, mode, *args, **kwargs)
        return ChangedRead(stream) if target == path and mode == 'rb' else stream
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='读取期间变化'):
        transfer.inspect_knowledge_question_links(service, path)
    assert not service.store.root.exists()


def test_input_change_during_plan_rejected_before_preview(service, tmp_path, monkeypatch):
    path = input_file(tmp_path, collection(relation())); real = service.plan_merge
    def changed(*a, **k):
        result = real(*a, **k); path.write_bytes(path.read_bytes() + b' '); return result
    monkeypatch.setattr(service, 'plan_merge', changed)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='JSON已变化'):
        transfer.inspect_knowledge_question_links(service, path)
    assert not service.store.root.exists()


def test_caller_cannot_mutate_store_through_preview(service, tmp_path):
    before = seed(service, collection(relation())); path = input_file(tmp_path, before['collection'])
    plan = transfer.inspect_knowledge_question_links(service, path)
    plan['incoming']['records'][0]['knowledge']['original']['blocks'][0]['value'] = 'caller mutation'
    assert service.snapshot() == before


def test_malformed_backend_merge_cannot_drop_local_records(service, tmp_path, monkeypatch):
    seed(service, collection(relation())); path = input_file(tmp_path, collection(relation(2)))
    before = disk(service.store.root); real = service.plan_merge
    def dropped(*a, **k):
        result = real(*a, **k); result['collection'] = collection(relation(2)); return result
    monkeypatch.setattr(service, 'plan_merge', dropped)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='不能覆盖'):
        transfer.inspect_knowledge_question_links(service, path)
    assert disk(service.store.root) == before


@pytest.mark.parametrize('change', ['counts', 'digest', 'incoming_reason', 'conflict_details',
    'revision', 'extra', 'numeric_alias'])
def test_preview_tampering_rejected_even_when_plan_digest_recomputed(service, tmp_path, change):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    if change == 'counts': plan['counts']['new'] = 99
    elif change == 'digest': plan['input_sha256'] = '0' * 64
    elif change == 'incoming_reason': plan['incoming']['records'][0]['reason'] = 'forged'
    elif change == 'conflict_details': plan['conflict_details'].append({'id': 'unverified'})
    elif change == 'revision': plan['current_store_revision'] = 'new-revision'
    elif change == 'extra': plan['unexpected'] = True
    else: plan['counts']['new'] = 1.0
    plan['plan_sha256'] = digest({key: value for key, value in plan.items() if key != 'plan_sha256'})
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='变化'):
        transfer.restore_knowledge_question_links(service, path, plan)
    assert not service.store.root.exists()


@pytest.mark.parametrize('change', ['bytes', 'formatting', 'delete'])
def test_changed_input_after_preview_is_rejected(service, tmp_path, change):
    value = collection(relation()); path = input_file(tmp_path, value)
    plan = transfer.inspect_knowledge_question_links(service, path)
    if change == 'bytes': write_json(path, packet(collection(relation(2))))
    elif change == 'formatting': write_json(path, packet(value), canonical=False)
    else: path.unlink()
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError):
        transfer.restore_knowledge_question_links(service, path, plan)
    assert not service.store.root.exists()


def test_store_change_after_preview_requires_new_plan(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    seed(service, collection(relation(2))); before = disk(service.store.root)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='变化'):
        transfer.restore_knowledge_question_links(service, path, plan)
    assert disk(service.store.root) == before


def test_store_change_between_recheck_and_commit_is_cas_rejected(service, tmp_path, monkeypatch):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    real = service.commit_merge; concurrent = collection(relation(2)); changed = {}
    def concurrent_commit(*args, **kwargs):
        seed(service, concurrent); changed.update(disk(service.store.root))
        return real(*args, **kwargs)
    monkeypatch.setattr(service, 'commit_merge', concurrent_commit)
    with pytest.raises(DraftConflictError): transfer.restore_knowledge_question_links(service, path, plan)
    assert disk(service.store.root) == changed and service.snapshot()['collection'] == concurrent


def test_unrelated_draft_change_does_not_invalidate_addressed_merge(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    service.store.save_draft('another-course', {'kind': 'preparation', 'value': 'new course'})
    result = transfer.restore_knowledge_question_links(service, path, plan)
    assert result['new_count'] == 1 and service.store.snapshot()['drafts']['another-course']['value'] == 'new course'


def test_capacity_failure_cannot_partially_append(service, tmp_path):
    seed(service, collection(*(relation(number) for number in range(1, links.MAX_RECORDS + 1))))
    path = input_file(tmp_path, collection(relation(links.MAX_RECORDS + 1)))
    before = disk(service.store.root)
    with pytest.raises(ValueError): transfer.inspect_knowledge_question_links(service, path)
    assert disk(service.store.root) == before


@pytest.mark.parametrize('duplicate', [False, True])
def test_cas_guard_rechecks_file_even_for_zero_new(service, tmp_path, duplicate):
    value = collection(relation())
    if duplicate: seed(service, value)
    path = input_file(tmp_path, value); plan = transfer.inspect_knowledge_question_links(service, path)
    before = disk(service.store.root); calls = []
    def guard(): calls.append('guard'); path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='提交前变化'):
        transfer.restore_knowledge_question_links(service, path, plan, guard=guard)
    assert calls == ['guard'] and disk(service.store.root) == before


def test_ui_guard_failure_preserves_entire_store(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    def closed(): raise ValueError('课程已关闭')
    with pytest.raises(ValueError, match='已关闭'):
        transfer.restore_knowledge_question_links(service, path, plan, guard=closed)
    assert not service.store.root.exists()


def test_restore_preserves_native_word_publication_lock_order(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    event = TaskCancellationEvent(); lock = service.words._lock; calls = []
    class OrderedLock:
        def __enter__(self):
            assert not event._publication_lock._is_owned(), 'publication lock acquired before native Word lock'
            lock.acquire(); calls.append('word'); return self
        def __exit__(self, *args): lock.release()
    service.words._lock = OrderedLock()
    def guard():
        assert event._publication_lock._is_owned()
        assert lock._is_owned()
        calls.append('guard')
    with read_cancel_scope(event):
        result = transfer.restore_knowledge_question_links(service, path, plan, guard=guard)
    assert result['new_count'] == 1 and calls == ['word', 'guard']
    assert event.request_cancel() is False


@pytest.mark.parametrize('cancelled_event', [0, 1])
def test_nested_task_cancellation_events_each_stop_before_cas_write(service, tmp_path, cancelled_event):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    events = [TaskCancellationEvent(), TaskCancellationEvent()]; calls = []
    def guard():
        assert all(event._publication_lock._is_owned() for event in events)
        assert service.words._lock._is_owned()
        calls.append('guard'); events[cancelled_event].set()
    with pytest.raises(ReadCancelled), read_cancel_scope(events[0]), read_cancel_scope(events[1]):
        transfer.restore_knowledge_question_links(service, path, plan, guard=guard)
    assert calls == ['guard'] and not service.store.root.exists()
    assert not any(event._published for event in events)


def test_nested_successful_publication_marks_both_task_events_completed(service, tmp_path):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    outer, inner = TaskCancellationEvent(), TaskCancellationEvent()
    with read_cancel_scope(outer), read_cancel_scope(inner):
        result = transfer.restore_knowledge_question_links(service, path, plan)
    assert result['new_count'] == 1
    assert outer.request_cancel() is False and inner.request_cancel() is False


@pytest.mark.parametrize('when', ['before', 'inspect', 'commit'])
def test_cancelled_restore_is_zero_write(service, tmp_path, monkeypatch, when):
    path = input_file(tmp_path, collection(relation())); plan = transfer.inspect_knowledge_question_links(service, path)
    event = TaskCancellationEvent(); real = service.plan_merge
    if when == 'before': event.set()
    elif when == 'inspect':
        def cancelled(*a, **k):
            result = real(*a, **k); event.set(); return result
        monkeypatch.setattr(service, 'plan_merge', cancelled)
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        transfer.restore_knowledge_question_links(service, path, plan, guard=event.set if when == 'commit' else None)
    assert not service.store.root.exists()


@pytest.mark.parametrize('policy', [None, [], {}, True, 'overwrite', 'KEEP_CURRENT'])
def test_invalid_conflict_policy_is_rejected_before_any_io(service, policy, monkeypatch):
    monkeypatch.setattr(transfer, '_file_bytes', lambda *_: pytest.fail('invalid policy read file'))
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError):
        transfer.restore_knowledge_question_links(service, 'no-file.json', {}, conflict_policy=policy)
    assert not service.store.root.exists()


def test_noncallable_ui_guard_is_rejected_before_io(service, monkeypatch):
    monkeypatch.setattr(transfer, '_file_bytes', lambda *_: pytest.fail('invalid guard read file'))
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='无法调用'):
        transfer.restore_knowledge_question_links(service, 'not-selected.json', {}, guard=True)
    assert not service.store.root.exists()


@pytest.mark.parametrize('failure', ['replace', 'cancel', 'store_change'])
def test_atomic_export_failures_keep_old_target_and_cleanup(service, tmp_path, monkeypatch, failure):
    seed(service, collection(relation())); path = tmp_path / 'export.json'; path.write_bytes(b'old target')
    before = (path.read_bytes(), path.stat().st_mtime_ns); event = TaskCancellationEvent()
    if failure == 'replace':
        def failed(*a): raise OSError('disk rejected replace')
        monkeypatch.setattr(transfer.os, 'replace', failed)
    else:
        real = transfer.os.fsync; fired = []
        def changed(fd):
            real(fd)
            if fired: return
            fired.append(True)
            if failure == 'cancel': event.set()
            else: seed(service, collection(relation(2)))
        monkeypatch.setattr(transfer.os, 'fsync', changed)
    error = ReadCancelled if failure == 'cancel' else transfer.KnowledgeQuestionLinksTransferError
    with pytest.raises(error), read_cancel_scope(event):
        transfer.export_knowledge_question_links(service, path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not list(tmp_path.glob('.knowledge-links-*.tmp'))


def test_unchanged_export_rechecks_target_before_return(service, tmp_path, monkeypatch):
    seed(service, collection(relation())); path = tmp_path / 'export.json'
    transfer.export_knowledge_question_links(service, path)
    real = service.snapshot; calls = []
    def replaced():
        result = real(); calls.append(1)
        if len(calls) == 2: path.write_bytes(b'external change')
        return result
    monkeypatch.setattr(service, 'snapshot', replaced)
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='导出文件'):
        transfer.export_knowledge_question_links(service, path)
    assert path.read_bytes() == b'external change'


def test_stale_export_revision_keeps_target(service, tmp_path):
    before = service.snapshot(); seed(service, collection(relation()))
    path = tmp_path / 'export.json'; path.write_bytes(b'previous export')
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError, match='已变化'):
        transfer.export_knowledge_question_links(service, path, expected_revision=before['revision'])
    assert path.read_bytes() == b'previous export'


@pytest.mark.parametrize('path_kind', ['directory', 'missing', 'invalid_type'])
def test_invalid_input_path_keeps_profile_absent(service, tmp_path, path_kind):
    path = tmp_path if path_kind == 'directory' else tmp_path/'missing.json' if path_kind == 'missing' else 7
    with pytest.raises(transfer.KnowledgeQuestionLinksTransferError):
        transfer.inspect_knowledge_question_links(service, path)
    assert not service.store.root.exists()
