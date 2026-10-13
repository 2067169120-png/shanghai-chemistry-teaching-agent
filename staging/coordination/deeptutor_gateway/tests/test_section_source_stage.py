"""One-compile source proofs retain native checks and reject late changes."""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from threading import Barrier, Event, Thread, get_ident
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from test_forward_section_materials import forward, prepare
from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_crop_reading import files
from test_textbook_reading_hints import write_packet

from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
from integrations.deeptutor_shchem_v1 import desktop_forward_section_materials as batch
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as handout
from integrations.deeptutor_shchem_v1 import desktop_section_material_handoff as handoff
from integrations.deeptutor_shchem_v1 import desktop_section_materials as contexts
from integrations.deeptutor_shchem_v1 import desktop_section_source_stage as stages
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, digest, resource
from integrations.deeptutor_shchem_v1.desktop_textbook_bookshelf import TextbookBookshelfService
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, ReaderThreadPoolExecutor, TaskCancellationEvent, read_cancel_scope)


def _observed_paths(rows):
    return {path for path, _proof in rows.values()}


def _close_source(proof):
    proof.close()
    assert not proof._watches and proof._watch_executor is None


def _memory_card(text, selection=None):
    """A valid native return value whose source service lives only in memory."""
    return resource('knowledge_note', 'Explicit memory source', [block('text', 'knowledge', text)], {
        'source_path': 'memory:explicit-selected-source',
        'source_sha256': sha256(text.encode('utf8')).hexdigest(),
        'source_revision': digest(selection or {'version': text}),
        'record_id': 'EXPLICIT-MEMORY-NOTE', 'pdf_pages': [1], 'printed_pages': 'unknown'})


@pytest.mark.parametrize('constructor_pending', [False, True],
    ids=['armed-before-caller-interruption', 'inflight-constructor-before-caller-interruption'])
def test_interrupted_caller_result_retains_watch_ownership_until_issuer_cleanup(
        tmp_path, monkeypatch, constructor_pending):
    """A safe fake models pending native I/O without allocating Win32 buffers."""
    events, failures = [], []
    entered, completed, release, cleanup_queued, cleanup_done = [Event() for _ in range(5)]
    caller_thread = get_ident()
    class FakeWatch:
        def __init__(self, _path):
            self.armed = True
            events.append(('armed', True, get_ident()))
            entered.set()
            if constructor_pending and not release.wait(timeout=10):
                raise RuntimeError('fake constructor release timed out')
            events.append(('constructed', True, get_ident()))
            completed.set()
        def close(self):
            self.armed = False
            events.append(('closed', False, get_ident()))
        def __del__(self):
            events.append(('destroyed', self.armed, get_ident()))
    monkeypatch.setattr(stages, '_WindowsDirectoryWatch', FakeWatch, raising=False)
    original_result, interrupted = Future.result, []
    def interrupted_result(future, timeout=None):
        if get_ident() == caller_thread and not interrupted:
            interrupted.append(True)
            if constructor_pending:
                assert entered.wait(timeout=5)
                assert not completed.is_set()
            else:
                original_result(future, timeout=5)
                assert completed.is_set()
            raise KeyboardInterrupt('synthetic interrupted watch wait')
        return original_result(future, timeout=timeout)
    monkeypatch.setattr(Future, 'result', interrupted_result)
    proof, closer = stages.SourceFileSet(), None
    key = 'fake-owned-source'
    try:
        with pytest.raises(KeyboardInterrupt, match='synthetic interrupted watch wait'):
            proof._watch(key, tmp_path / 'fake-owned-directory')
        assert proof._watch_executor is not None
        if constructor_pending:
            assert proof._watches == {key: None}  # Ownership is reserved before construction finishes.
            executor = proof._watch_executor
            original_submit = executor.submit
            def submitted_cleanup(function, *args, **kwargs):
                future = original_submit(function, *args, **kwargs)
                if getattr(function, '__name__', '') == '_close_owned_watches':
                    cleanup_queued.set()
                return future
            monkeypatch.setattr(executor, 'submit', submitted_cleanup)
            def close():
                try:
                    proof.close()
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    cleanup_done.set()
            closer = Thread(target=close, name='test-source-proof-close')
            closer.start()
            assert cleanup_queued.wait(timeout=5)
            assert not completed.is_set() and not cleanup_done.is_set()
            assert not any(event[0] == 'closed' for event in events)
            release.set()
            closer.join(timeout=10)
            assert not closer.is_alive() and not failures
            assert completed.is_set() and cleanup_done.is_set()
        else:
            assert len(proof._watches) == 1 and proof._watches[key].armed
            proof.close()
        assert not proof._watches and proof._watch_executor is None
        assert [event[0] for event in events] == ['armed', 'constructed', 'closed', 'destroyed']
        issuer_thread = events[0][2]
        assert issuer_thread != caller_thread
        assert all(event[2] == issuer_thread for event in events)
        assert events[-1][1] is False  # A still-armed buffer can never lose its owner.
    finally:
        release.set()
        if closer is not None:
            closer.join(timeout=10)
        proof.close()


def test_actual_readonly_opens_capture_every_extension_without_guessing_inputs(tmp_path):
    selected = [tmp_path / name for name in ('original.custom-format', 'original.image', 'extensionless')]
    untouched, write_only = tmp_path / 'unselected.pdf', tmp_path / 'only-written.json'
    for path in selected + [untouched]:
        path.write_bytes((path.name.encode('utf8') + b' full source ') * 1500)
    proof = stages.SourceFileSet()
    with proof.capture():
        # A tiny consumer read must still seal the whole source, including its tail.
        with selected[0].open('rb') as stream:
            assert stream.read(1)
        with open(selected[1], 'rb') as stream:
            assert stream.read(1)
        fd = os.open(selected[2], os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        try:
            assert os.read(fd, 1)
        finally:
            os.close(fd)
        write_only.write_bytes(b'caller-owned output, not a source read')
    untouched.read_bytes()  # Reads outside the capture cannot join its source set.
    assert _observed_paths(proof.files) == set(selected)
    assert proof.directories == {}
    for path, fingerprint in proof.files.values():
        assert fingerprint['sha256'] == sha256(path.read_bytes()).hexdigest()
    initial_bytes = proof.statistics['source_bytes_hashed']
    total_bytes = sum(path.stat().st_size for path in selected)
    assert initial_bytes == total_bytes
    assert proof.statistics['readonly_opens'] == len(selected)
    assert proof.statistics['opened_file_size_bytes'] == total_bytes
    assert proof.statistics['sha_passes'] == len(selected)
    proof.verify()
    assert proof.statistics['source_bytes_hashed'] == total_bytes * 2
    assert proof.statistics['sha_passes'] == len(selected) * 2
    assert proof.statistics['readonly_opens'] == len(selected)
    _close_source(proof)


def test_actual_sqlite_readonly_uri_seals_arbitrary_extension_and_ignores_write_connections(tmp_path):
    selected = tmp_path / 'selected native 数据.custom-store'
    writable = tmp_path / 'unrelated-write.custom-store'
    for path in (selected, writable):
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE source (body TEXT NOT NULL)')
            connection.execute('INSERT INTO source VALUES (?)', ('complete original row',))
            connection.commit()
        finally:
            connection.close()
    proof = stages.SourceFileSet()
    with proof.capture():
        connection = sqlite3.connect(selected.as_uri() + '?mode=ro', uri=True)
        try:
            assert connection.execute('SELECT body FROM source').fetchall() == [('complete original row',)]
        finally:
            connection.close()
        ordinary_write = sqlite3.connect(writable)
        try:
            ordinary_write.execute('UPDATE source SET body = ?', ('caller-owned changed row',))
            ordinary_write.commit()
        finally:
            ordinary_write.close()
    assert _observed_paths(proof.files) == {selected}
    assert proof.statistics['readonly_opens'] == 1
    assert next(iter(proof.files.values()))[1]['sha256'] == sha256(selected.read_bytes()).hexdigest()
    proof.verify()  # The unrelated writable connection cannot invalidate the selected source.
    changed = sqlite3.connect(selected)
    try:
        changed.execute('UPDATE source SET body = ?', ('late selected source row',))
        changed.commit()
    finally:
        changed.close()
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_full_digest_rejects_same_size_mtime_content_change_when_metadata_is_equal(tmp_path, monkeypatch):
    source = tmp_path / 'full-source.unknown-format'
    raw = b'A' * (2 * 1024 * 1024 + 19)
    source.write_bytes(raw)
    # Hold the additional change-time/notification signals constant to isolate SHA.
    monkeypatch.setattr(stages, '_change_time', lambda *_args: 7)
    if os.name == 'nt':
        monkeypatch.setattr(stages._WindowsDirectoryWatch, 'check', lambda *_args, **_kwargs: None)
    proof = stages.SourceFileSet()
    with proof.capture():
        with source.open('rb') as stream:
            assert stream.read(1) == b'A'
    before = source.stat()
    with source.open('r+b') as stream:
        stream.seek(-1, os.SEEK_END)
        stream.write(b'B')
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    hashed = proof.statistics['source_bytes_hashed']
    with pytest.raises(ValueError):
        proof.verify()
    assert proof.statistics['source_bytes_hashed'] - hashed >= len(raw)
    _close_source(proof)


@pytest.mark.parametrize('change', ['bytes', 'same_bytes_replacement', 'delete'])
def test_file_proof_rejects_content_missing_or_same_bytes_foreign_identity(tmp_path, change):
    source = tmp_path / 'original.custom'; raw = b'complete original bytes'
    source.write_bytes(raw)
    proof = stages.SourceFileSet()
    with proof.capture():
        assert source.read_bytes() == raw
    before = source.stat()
    if change == 'delete':
        source.unlink()
    elif change == 'same_bytes_replacement':
        replacement = tmp_path / 'replacement.custom'
        replacement.write_bytes(raw)
        assert replacement.stat().st_ino != before.st_ino
        os.replace(replacement, source)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert source.read_bytes() == raw and source.stat().st_mtime_ns == before.st_mtime_ns
        assert source.stat().st_ino != before.st_ino
    else:
        source.write_bytes(b'changed original bytes')
    with pytest.raises((ValueError, OSError)):
        proof.verify()
    _close_source(proof)


@pytest.mark.skipif(os.name != 'nt', reason='Exercises the real Windows FILE_BASIC_INFO ChangeTime gate.')
def test_windows_restored_bytes_mtime_and_identity_still_rejects_aba(tmp_path):
    source = tmp_path / 'original.pdf'; raw = b'complete original bytes'
    source.write_bytes(raw)
    proof = stages.SourceFileSet()
    with proof.capture():
        source.read_bytes()
    before = source.stat()
    source.write_bytes(b'x' * len(raw))
    source.write_bytes(raw)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = source.stat()
    assert source.read_bytes() == raw
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    # Windows can retain the same ChangeTime for edits within one timer tick.
    # The proof must still reject the observed source ABA, without a sleep.
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


@pytest.mark.parametrize('enumeration', ['scandir', 'listdir'])
@pytest.mark.parametrize('change', ['add', 'delete'])
def test_actual_os_directory_discovery_rejects_added_or_deleted_inputs(tmp_path, enumeration, change):
    folder = tmp_path / 'actual-discovery'; folder.mkdir()
    original = folder / 'one.unlisted-format'; original.write_bytes(b'original')
    proof = stages.SourceFileSet()
    with proof.capture():
        if enumeration == 'scandir':
            with os.scandir(folder) as entries:
                assert [entry.name for entry in entries] == [original.name]
        else:
            assert os.listdir(folder) == [original.name]
    assert _observed_paths(proof.directories) == {folder}
    assert proof.files == {}
    if change == 'add':
        (folder / 'new.unlisted-format').write_bytes(b'new input')
    else:
        original.unlink()
    with pytest.raises(ValueError):
        proof.verify()
    watches = list(proof._watches.values())
    _close_source(proof)
    assert all(watch.handle is None and watch.event is None for watch in watches)


@pytest.mark.skipif(os.name != 'nt', reason='Exercises directory Windows ChangeTime after restored names and mtime.')
def test_windows_directory_aba_rejects_restored_listing_and_mtime(tmp_path):
    folder = tmp_path / 'source-discovery'; folder.mkdir()
    original = folder / 'one'; original.write_bytes(b'original')
    proof = stages.SourceFileSet()
    with proof.capture():
        assert os.listdir(folder) == ['one']
    before = folder.stat()
    changed = folder / 'temporary-discovery'; changed.write_bytes(b'new source'); changed.unlink()
    os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.listdir(folder) == ['one'] and folder.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_directory_proof_detects_same_name_foreign_child_identity(tmp_path, monkeypatch):
    folder = tmp_path / 'actual-discovery'; folder.mkdir()
    original = folder / 'one.original'; original.write_bytes(b'original')
    replacement = tmp_path / 'replacement.original'; replacement.write_bytes(b'original')
    old_inode = original.stat().st_ino
    assert replacement.stat().st_ino != old_inode
    monkeypatch.setattr(stages, '_directory_change_time', lambda *_args: 7)
    if os.name == 'nt':
        monkeypatch.setattr(stages._WindowsDirectoryWatch, 'check', lambda *_args, **_kwargs: None)
    proof = stages.SourceFileSet()
    with proof.capture():
        assert os.listdir(folder) == [original.name]
    before = folder.stat()
    os.replace(replacement, original)
    os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.listdir(folder) == [original.name]
    assert original.read_bytes() == b'original' and original.stat().st_ino != old_inode
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_excluded_roots_ignore_only_explicit_compile_output_and_keep_sibling_sources(tmp_path):
    owned = tmp_path / 'shchem-section-stage-owned'; owned.mkdir()
    sibling = tmp_path / 'shchem-section-stage-owned-source'; sibling.mkdir()
    inside, outside = owned / 'copied.image', sibling / 'original.image'
    inside.write_bytes(b'owned temporary output'); outside.write_bytes(b'real selected source')
    proof = stages.SourceFileSet(excluded_roots=(owned,))
    with proof.capture():
        inside.read_bytes(); outside.read_bytes()
        assert os.listdir(owned) == [inside.name]
        with os.scandir(sibling) as entries:
            assert [entry.name for entry in entries] == [outside.name]
    assert _observed_paths(proof.files) == {outside}
    assert _observed_paths(proof.directories) == {sibling}
    inside.write_bytes(b'changed caller-owned output')
    (owned / 'new-receipt.json').write_bytes(b'new caller-owned receipt')
    proof.verify()
    outside.write_bytes(b'changed real source')
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_parent_traversal_cannot_turn_sibling_source_into_excluded_output(tmp_path):
    owned = tmp_path / 'explicit-compile-output'; owned.mkdir()
    source = tmp_path / 'real-sibling.original'; source.write_bytes(b'actual source')
    alias = owned / '..' / source.name
    proof = stages.SourceFileSet(excluded_roots=(owned,))
    with proof.capture():
        assert alias.read_bytes() == b'actual source'
    assert {path.resolve() for path in _observed_paths(proof.files)} == {source.resolve()}
    source.write_bytes(b'changed sibling source')
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_tempfile_created_inside_capture_is_not_implicitly_an_excluded_source(tmp_path):
    proof = stages.SourceFileSet()
    with TemporaryDirectory(prefix='real-reader-selected-', dir=tmp_path) as temporary:
        source = Path(temporary) / 'selected.original'; source.write_bytes(b'actual native source')
        with proof.capture():
            with TemporaryDirectory(prefix='reader-owned-', dir=tmp_path) as second_temporary:
                second = Path(second_temporary) / 'selected.original'
                second.write_bytes(b'another native source')
                source.read_bytes(); second.read_bytes()
                assert _observed_paths(proof.files) == {source, second}
                second.write_bytes(b'changed real source')
                with pytest.raises(ValueError):
                    proof.verify()
    _close_source(proof)


def test_actual_native_staging_facade_registers_only_its_owned_output_root(tmp_path):
    owned = tmp_path / 'native-compile-output'; owned.mkdir()
    source = tmp_path / 'real-native-source.original'; source.write_bytes(b'real native source')
    inside = owned / 'staged.image'; inside.write_bytes(b'compiler output')
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path / 'final-owned-output'))
    proof = stages.SourceFileSet()
    with proof.capture():
        staged = handout._StagingFacade(facade, owned)
        assert staged.paths.task_root == owned
        inside.read_bytes(); source.read_bytes()
        assert os.listdir(owned) == [inside.name]
    observed = _observed_paths(proof.files)
    assert source in observed
    assert not any(path.is_relative_to(owned) for path in observed)
    assert owned not in _observed_paths(proof.directories)
    inside.write_bytes(b'changed compiler output')
    (owned / 'new-receipt.json').write_bytes(b'new compiler receipt')
    proof.verify()
    source.write_bytes(b'changed real native source')
    with pytest.raises(ValueError):
        proof.verify()
    _close_source(proof)


def test_simultaneous_thread_captures_do_not_observe_each_others_sources(tmp_path):
    paths = [tmp_path / name for name in ('first.actual', 'second.actual')]
    for path in paths:
        path.write_bytes(path.name.encode())
    barrier = Barrier(2)
    def read(path):
        proof = stages.SourceFileSet()
        with proof.capture():
            barrier.wait(timeout=10)
            path.read_bytes()
            barrier.wait(timeout=10)
        return proof
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read, path) for path in paths]
        first, second = [future.result(timeout=20) for future in futures]
    assert _observed_paths(first.files) == {paths[0]}
    assert _observed_paths(second.files) == {paths[1]}
    paths[0].write_bytes(b'changed first thread source')
    second.verify()
    with pytest.raises(ValueError):
        first.verify()
    _close_source(first); _close_source(second)


def test_reader_executor_inherits_scope_and_unrelated_executor_does_not(tmp_path):
    own, unrelated_path, after = [tmp_path / name for name in ('own', 'unrelated', 'after')]
    for path in (own, unrelated_path, after):
        path.write_bytes(path.name.encode())
    proof = stages.SourceFileSet()
    with ThreadPoolExecutor(max_workers=1) as unrelated, ReaderThreadPoolExecutor(max_workers=1) as reader:
        with proof.capture():
            unrelated.submit(unrelated_path.read_bytes).result(timeout=10)
            reader.submit(own.read_bytes).result(timeout=10)
    after.read_bytes()
    assert _observed_paths(proof.files) == {own}
    proof.verify()
    _close_source(proof)


def test_nested_capture_and_failed_reader_restore_previous_scope(tmp_path):
    paths = [tmp_path / name for name in ('outer-before', 'inner', 'outer-after', 'outside')]
    for path in paths:
        path.write_bytes(path.name.encode())
    outer, inner = stages.SourceFileSet(), stages.SourceFileSet()
    with outer.capture():
        paths[0].read_bytes()
        with pytest.raises(RuntimeError, match='synthetic reader failure'):
            with inner.capture():
                paths[1].read_bytes()
                raise RuntimeError('synthetic reader failure')
        paths[2].read_bytes()
    paths[3].read_bytes()
    assert _observed_paths(outer.files) == {paths[0], paths[2]}
    assert _observed_paths(inner.files) == {paths[1]}
    outer.verify(); inner.verify()
    _close_source(outer); _close_source(inner)
    assert stages._ACTIVE.get() is None


def test_stage_memoizes_same_typed_selection_but_rechecks_each_distinct_context(monkeypatch):
    calls = {'context': [], 'knowledge': []}
    def context(_facade, _item, _resources, _assets, selected):
        calls['context'].append(deepcopy(selected))
        return True
    def knowledge(_facade, selected):
        calls['knowledge'].append(deepcopy(selected))
        return _memory_card('complete unchanged memory source', selected)
    monkeypatch.setattr(contexts, 'recheck_section_material_context', context)
    monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    stage = stages.SectionSourceStage(SimpleNamespace())
    selection = {'kind': 'explicit_memory', 'note_id': 'one', 'revision': 'a' * 64}
    left, right = {'scope': 'left'}, {'scope': 'right'}
    for chosen in (left, left, right, right):
        stage.context(chosen)
        value = stage.import_knowledge(selection)
        value['original']['blocks'][0]['value'] = 'caller edited returned copy'
    assert calls == {'context': [left, right], 'knowledge': [selection]}
    assert stage.import_knowledge(selection)['original']['blocks'][0]['value'] == 'complete unchanged memory source'
    stage.finish()
    assert calls == {'context': [left, right, left, right], 'knowledge': [selection, selection]}
    assert stage.closed and not stage.contexts and not stage.knowledge


def test_stage_selection_memo_retains_json_type_identity(monkeypatch):
    calls = []
    def knowledge(_facade, selected):
        calls.append(deepcopy(selected))
        return _memory_card(type(selected['record_id']).__name__, selected)
    monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    stage = stages.SectionSourceStage(SimpleNamespace())
    boolean, integer = {'kind': 'explicit_memory', 'record_id': True}, {'kind': 'explicit_memory', 'record_id': 1}
    assert stage.import_knowledge(boolean)['original']['blocks'][0]['value'] == 'bool'
    assert stage.import_knowledge(integer)['original']['blocks'][0]['value'] == 'int'
    assert stage.import_knowledge(boolean)['original']['blocks'][0]['value'] == 'bool'
    assert len(calls) == 2
    stage.finish()
    assert [type(row['record_id']) for row in calls] == [bool, int, bool, int]


def test_context_memo_keeps_entry_identity_when_two_cards_share_one_original(monkeypatch):
    calls = []
    def context(_facade, entry, _resources, _assets, _selected):
        calls.append(entry['id'])
        return True
    monkeypatch.setattr(contexts, 'recheck_section_material_context', context)
    first = _memory_card('same sealed authored content')
    second = deepcopy(first); second['id'] = 'CR-' + 'b' * 32
    assert first['original'] == second['original'] and first['id'] != second['id']
    stage = stages.SectionSourceStage(SimpleNamespace())
    chosen = {'entry_kind': 'memory', 'scope': 'one'}
    stage.context(chosen, item=first)
    stage.context(chosen, item=first)
    stage.context(chosen, item=second)
    assert calls == [first['id'], second['id']]
    stage.finish()
    assert calls == [first['id'], second['id'], first['id'], second['id']]


@pytest.mark.parametrize('port', ['context', 'knowledge'])
def test_stage_tail_rechecks_dynamic_memory_source_and_closes_on_failure(monkeypatch, port):
    version, calls = {'context': 'first', 'knowledge': 'first'}, {'context': 0, 'knowledge': 0}
    def context(_facade, _item, _resources, _assets, selected):
        calls['context'] += 1
        if selected['version'] != version['context']:
            raise ValueError('dynamic memory context changed')
        return True
    def knowledge(_facade, selected):
        calls['knowledge'] += 1
        return _memory_card(version['knowledge'], selected)
    monkeypatch.setattr(contexts, 'recheck_section_material_context', context)
    monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    stage = stages.SectionSourceStage(SimpleNamespace())
    stage.context({'version': 'first'})
    stage.import_knowledge({'kind': 'explicit_memory', 'note_id': 'one'})
    assert not stage.files.files and not stage.files.directories
    version[port] = 'late replacement'
    with pytest.raises(ValueError):
        stage.finish()
    assert calls[port] == 2
    assert stage.closed and not stage.contexts and not stage.knowledge
    with pytest.raises(ValueError):
        stage.import_knowledge({'kind': 'explicit_memory', 'note_id': 'one'})


def test_tail_newly_discovered_source_is_sealed_and_verified_before_success(tmp_path, monkeypatch):
    first, discovered = tmp_path / 'first.original', tmp_path / 'late.original'
    first.write_bytes(b'first source'); discovered.write_bytes(b'late native source')
    calls = []
    def knowledge(_facade, selected):
        calls.append(selected)
        chosen = first if len(calls) == 1 else discovered
        chosen.read_bytes()
        if len(calls) == 2:
            discovered.write_bytes(b'changed after the tail native read')
        return _memory_card('same authored content', selected)
    monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    stage = stages.SectionSourceStage(SimpleNamespace())
    stage.import_knowledge({'kind': 'explicit_memory', 'note_id': 'one'})
    with pytest.raises(ValueError):
        stage.finish()
    assert _observed_paths(stage.files.files) == {first, discovered}
    assert len(calls) == 2 and stage.closed


def test_finish_success_forbids_all_subsequent_stage_operations(monkeypatch):
    monkeypatch.setattr(contexts, 'recheck_section_material_context', lambda *_args: True)
    monkeypatch.setattr(sources, 'import_knowledge_note', lambda _facade, selected: _memory_card('original', selected))
    stage = stages.SectionSourceStage(SimpleNamespace())
    chosen = {'kind': 'explicit_memory', 'note_id': 'one'}
    stage.context({'version': 'one'}); stage.import_knowledge(chosen); stage.finish()
    for operation in (lambda: stage.context({'version': 'one'}), lambda: stage.import_knowledge(chosen), stage.finish):
        with pytest.raises(ValueError):
            operation()
    assert stage.closed and not stage.contexts and not stage.knowledge


def test_phase_reporter_is_scoped_emits_once_and_receives_detached_statistics(monkeypatch):
    monkeypatch.setattr(contexts, 'recheck_section_material_context', lambda *_args: True)
    monkeypatch.setattr(sources, 'import_knowledge_note', lambda _facade, selected: _memory_card('original', selected))
    reports = []
    with stages.source_stage_reporter(reports.append):
        stage = stages.SectionSourceStage(SimpleNamespace())
        chosen = {'kind': 'explicit_memory', 'note_id': 'one'}
        stage.context({'version': 'one'}); stage.context({'version': 'one'})
        stage.import_knowledge(chosen); stage.import_knowledge(chosen)
        stage.finish(); stage.close()
        with pytest.raises(ValueError):
            stage.finish()
    stages.SectionSourceStage(SimpleNamespace()).finish()
    assert len(reports) == 1
    assert reports[0]['context_checks'] == reports[0]['context_hits'] == 1
    assert reports[0]['knowledge_checks'] == reports[0]['knowledge_hits'] == 1
    assert reports[0]['native_context_tail'] == reports[0]['native_knowledge_tail'] == 1
    assert reports[0]['source_files'] == reports[0]['source_directories'] == 0
    original = deepcopy(stage.statistics)
    reports[0]['context_checks'] = 999
    assert stage.statistics == original


def test_stop_before_tail_closes_stage_and_never_marks_durable_publication(monkeypatch):
    monkeypatch.setattr(sources, 'import_knowledge_note', lambda _facade, selected: _memory_card('original', selected))
    stage = stages.SectionSourceStage(SimpleNamespace())
    stage.import_knowledge({'kind': 'explicit_memory', 'note_id': 'one'})
    event = TaskCancellationEvent(); event.set()
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        stage.finish()
    assert event.is_set() and not event._published
    assert stage.closed and not stage.contexts and not stage.knowledge
    assert stages._ACTIVE.get() is None


def test_native_rechecks_do_not_scale_with_repeated_requests(forward, monkeypatch):
    calls = {'context': 0, 'knowledge': 0, 'section_source': 0}
    real_context, real_knowledge = contexts.recheck_section_material_context, sources.import_knowledge_note
    real_source = TextbookBookshelfService.section_source
    def context(*args, **kwargs):
        calls['context'] += 1
        return real_context(*args, **kwargs)
    def knowledge(*args, **kwargs):
        calls['knowledge'] += 1
        return real_knowledge(*args, **kwargs)
    def section_source(*args, **kwargs):
        calls['section_source'] += 1
        return real_source(*args, **kwargs)
    monkeypatch.setattr(contexts, 'recheck_section_material_context', context)
    monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    monkeypatch.setattr(TextbookBookshelfService, 'section_source', section_source)
    counts = []
    for length in (1, 8):
        calls.update({key: 0 for key in calls})
        publication = prepare(forward, [deepcopy(forward.request) for _ in range(length)])
        assert len(publication.result['resources']) == 1
        assert publication.result['resources'][0]['original'] == forward.request['knowledge_original']
        publication.discard()
        counts.append(dict(calls))
    assert counts[0] == counts[1]
    assert counts[0]['context'] == counts[0]['knowledge'] == 2
    assert counts[0]['section_source'] >= 4
    assert not forward.task_root.exists()


def test_prepare_publish_and_detached_check_each_use_fresh_closed_source_stage(forward, monkeypatch):
    seen, real = [], stages.SectionSourceStage
    def stage(*args, **kwargs):
        value = real(*args, **kwargs); seen.append(value)
        return value
    monkeypatch.setattr(stages, 'SectionSourceStage', stage)
    publication = prepare(forward, [forward.request] * 4)
    assert len(seen) == 1
    publication.publish()
    assert len(seen) == 3
    publication.recheck_published()
    assert len(seen) == 4 and len({id(row) for row in seen}) == 4
    assert all(row.closed and not row.contexts and not row.knowledge for row in seen)
    assert all(not row.files._watches for row in seen)
    assert all(row.files._watch_executor is None for row in seen)
    assert all(row.files.files for row in seen)
    assert all(row.files.statistics['source_bytes_hashed'] > 0 for row in seen)
    publication.accept()


@pytest.mark.parametrize('change', ['pdf', 'pdf_aba', 'directory_add', 'directory_delete', 'notes'])
def test_source_change_after_cached_requests_rejects_entire_batch(forward, monkeypatch, change):
    f = forward
    real, real_stage, seen = batch._check_batch_roles, stages.SectionSourceStage, []
    def stage(*args, **kwargs):
        value = real_stage(*args, **kwargs); seen.append(value)
        return value
    def mutate(*args):
        real(*args)
        if change in {'pdf', 'pdf_aba'}:
            raw, before = f.book.read_bytes(), f.book.stat()
            f.book.write_bytes(raw + b'changed source')
            if change == 'pdf_aba':
                f.book.write_bytes(raw); os.utime(f.book, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == 'directory_add':
            write_packet(f.root, [], filename='reading-notes-added.json')
        elif change == 'directory_delete':
            (f.root / 'knowledge/textbook/reading-notes-fixture.json').unlink()
        else:
            f.note['summary'] += ' changed'; write_packet(f.root, [f.note])
    monkeypatch.setattr(batch, '_check_batch_roles', mutate)
    monkeypatch.setattr(stages, 'SectionSourceStage', stage)
    with pytest.raises(ValueError):
        prepare(f, [f.request] * 6)
    assert not f.task_root.exists()
    assert seen and all(row.closed and not row.files._watches for row in seen)
    assert all(row.files._watch_executor is None for row in seen)


@pytest.mark.parametrize('port', ['context', 'knowledge'])
def test_batch_tail_independently_rechecks_dynamic_memory_sources(forward, monkeypatch, port):
    f = forward
    changed = []
    real_roles = batch._check_batch_roles
    def mutate(*args):
        real_roles(*args); changed.append(True)
    monkeypatch.setattr(batch, '_check_batch_roles', mutate)
    if port == 'context':
        real = f.facade.textbook_bookshelf
        def shelf():
            value = real()
            if changed:
                value['reading_notes'][0]['summary'] += ' memory changed'
            return value
        f.facade.textbook_bookshelf = shelf
    else:
        real = sources.import_knowledge_note
        def knowledge(*args, **kwargs):
            value = real(*args, **kwargs)
            if changed:
                value['original']['blocks'][0]['value'] += ' memory changed'
            return value
        monkeypatch.setattr(sources, 'import_knowledge_note', knowledge)
    with pytest.raises(ValueError):
        prepare(f, [f.request] * 6)
    assert not f.task_root.exists()


def test_stop_during_batch_tail_closes_stage_without_publication(forward, monkeypatch):
    event = TaskCancellationEvent()
    real_verify, real_stage, proofs, seen = stages.SourceFileSet.verify, stages.SectionSourceStage, [], []
    def stop(self):
        proofs.append(self); event.set()
        return real_verify(self)
    def stage(*args, **kwargs):
        value = real_stage(*args, **kwargs); seen.append(value)
        return value
    monkeypatch.setattr(stages.SourceFileSet, 'verify', stop)
    monkeypatch.setattr(stages, 'SectionSourceStage', stage)
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        prepare(forward, [forward.request] * 5)
    assert proofs and event.is_set() and not event._published
    assert seen and all(row.closed for row in seen)
    assert all(not row.files._watches for row in seen)
    assert all(row.files._watch_executor is None for row in seen)
    assert stages._ACTIVE.get() is None and not forward.task_root.exists()


@pytest.mark.parametrize('change', ['original', 'flags', 'membership', 'context'])
def test_each_cached_request_still_checks_original_flags_membership_and_context(forward, change):
    second = deepcopy(forward.request)
    if change == 'original':
        second['knowledge_card']['original']['blocks'][0]['value'] += ' forged'
        second['knowledge_card']['original_sha256'] = digest(second['knowledge_card']['original'])
        second['knowledge_original'] = deepcopy(second['knowledge_card']['original'])
    elif change == 'flags':
        second['knowledge_card']['official'] = True
    elif change == 'membership':
        second['knowledge_selection']['revision'] = 'f' * 64
    else:
        second['context']['section_filter_key'] = 'different explicit scope'
    with pytest.raises(ValueError):
        prepare(forward, [forward.request, second])
    assert not forward.task_root.exists()


@pytest.mark.parametrize('late_change', [False, True], ids=['full-handoff', 'late-source-change'])
def test_legacy_request_without_stage_retains_full_native_check_path(forward, monkeypatch, late_change):
    f, calls = forward, {'context': 0, 'knowledge': 0}
    real_context, real_knowledge = handoff.recheck_section_material_context, handoff.import_knowledge_note
    def context(*args, **kwargs):
        calls['context'] += 1
        return real_context(*args, **kwargs)
    def knowledge(*args, **kwargs):
        calls['knowledge'] += 1
        return real_knowledge(*args, **kwargs)
    def forbidden_stage(*_args, **_kwargs):
        pytest.fail('The legacy no-stage path must keep its ordinary native checks.')
    monkeypatch.setattr(handoff, 'recheck_section_material_context', context)
    monkeypatch.setattr(handoff, 'import_knowledge_note', knowledge)
    monkeypatch.setattr(stages, 'SectionSourceStage', forbidden_stage)
    inputs = deepcopy(f.request)
    publication = handoff.prepare_section_material_request(f.facade, None, [], [], f.request)
    assert calls == {'context': 1, 'knowledge': 2}
    if late_change:
        f.note['summary'] += ' late native source'; write_packet(f.root, [f.note])
    before = files(f.root, f.task_root)
    if late_change:
        with pytest.raises(ValueError):
            publication.publish()
        assert calls['context'] == 2
    else:
        result = publication.publish(); publication.accept()
        assert calls == {'context': 3, 'knowledge': 4}
        assert len(result['resources']) == 1 and result['image_assets'] == []
        assert result['resources'][0]['original'] == f.request['knowledge_original']
    assert f.request == inputs and files(f.root, f.task_root) == before
    assert not f.task_root.exists()


def test_source_binding_remains_exact_native_metadata_without_pdf_payload(forward):
    source = forward.shelf.section_source(forward.section_key, forward.selection['revision'])
    metadata = {key: value for key, value in source.items() if key != 'pdf_bytes'}
    assert forward.context['source_binding']['source_snapshot_sha256'] == digest(metadata)
    assert 'pdf_bytes' not in forward.context['source_binding']
    publication = prepare(forward, [forward.request])
    assert publication.requests[0]['context'] == forward.context
    publication.discard()


def _runtime_reader_module():
    import importlib
    module = importlib.import_module('pypdf._reader')
    return module, Path(module.__spec__.origin)


def _runtime_fixture_module(tmp_path, monkeypatch):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    import sys
    folder = tmp_path / 'OWN-DATA-shaped-fixture'; folder.mkdir()
    source = folder / 'material_runtime.py'
    raw = ("def fixture_value():\n    return 'complete own source'\n\n# material tail: "
           + 'full source evidence ' * 1500 + '\n').encode('utf8')
    source.write_bytes(raw)
    name = 'explicit_own_material_runtime_fixture'
    loader = SourceFileLoader(name, str(source))
    spec = importlib.util.spec_from_file_location(name, source, loader=loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    return folder, source, raw, loader, module


def test_runtime_cold_linked_venv_import_and_inspect_are_real_code_reads(tmp_path):
    import json
    import subprocess
    import sys
    script = r'''
from pathlib import Path
import inspect, json, sys
sys.path[:0] = sys.argv[1:3]
import PySide6.QtCore  # Install the real import hook without constructing a QApplication.
from integrations.deeptutor_shchem_v1.desktop_section_source_stage import SourceFileSet
assert not any(name == 'pypdf' or name.startswith('pypdf.') for name in sys.modules)
proof = SourceFileSet()
try:
    with proof.capture():
        from pypdf import PdfReader
        source = inspect.getsource(PdfReader)
    assert 'class PdfReader' in source and len(source) > 1000
    assert proof.statistics['runtime_code_reads'] > 0
    proof.verify()
    print(json.dumps({'runtime_code_reads': proof.statistics['runtime_code_reads'],
        'source_characters': len(source), 'material_files': len(proof.files),
        'interpreter': sys.executable, 'module_origin': inspect.getfile(PdfReader)}))
finally:
    proof.close()
    assert not proof._watches and proof._watch_executor is None
'''
    overlay = Path(stages.__file__).resolve().parents[2]
    repo = Path(__file__).resolve().parents[4]
    result = subprocess.run([sys.executable, '-B', '-u', '-c', script, str(overlay), str(repo)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf8', timeout=60, check=True,
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    report = json.loads(result.stdout)
    (tmp_path / 'cold-runtime.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
    assert report['runtime_code_reads'] > 0 and report['source_characters'] > 1000


def test_runtime_same_path_plain_read_has_no_code_io_exemption():
    _module, source = _runtime_reader_module()
    proof = stages.SourceFileSet()
    try:
        try:
            with proof.capture():
                text = source.read_text(encoding='utf8')
        except ValueError as exc:
            # A literal linked-venv path still receives the raw reparse guard.
            assert '链接' in str(exc)
            assert source.absolute() != source.resolve()
        else:
            assert text and source.resolve() in _observed_paths(proof.files)
            fingerprint = next(value for path, value in proof.files.values() if path == source.resolve())
            assert fingerprint['sha256'] == sha256(source.read_bytes()).hexdigest()
            proof.verify()
        assert proof.statistics['runtime_code_reads'] == 0
    finally:
        _close_source(proof)


def test_runtime_own_module_spec_loader_and_inspect_keep_full_material_proof(tmp_path, monkeypatch):
    import inspect
    import linecache
    _folder, source, raw, loader, module = _runtime_fixture_module(tmp_path, monkeypatch)
    for change in ('changed-content', 'restored-bytes-and-mtime'):
        source.write_bytes(raw)
        linecache.clearcache()
        proof = stages.SourceFileSet()
        try:
            with proof.capture():
                loader.exec_module(module)  # Real SourceLoader.get_code and a live matching ModuleSpec.
                assert 'complete own source' in inspect.getsource(module.fixture_value)
            assert source in _observed_paths(proof.files)
            assert proof.statistics['runtime_code_reads'] == 0
            assert proof.files[os.path.normcase(str(source))][1]['sha256'] == sha256(raw).hexdigest()
            before = source.stat()
            source.write_bytes(raw.replace(b'complete own source', b'changed own source', 1))
            if change == 'restored-bytes-and-mtime':
                source.write_bytes(raw)
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                assert source.read_bytes() == raw and source.stat().st_mtime_ns == before.st_mtime_ns
            with pytest.raises(ValueError):
                proof.verify()
        finally:
            _close_source(proof)


def test_runtime_registered_filefinder_for_own_fixture_still_proves_directory_membership(tmp_path, monkeypatch):
    from importlib.machinery import FileFinder, SOURCE_SUFFIXES, SourceFileLoader
    import sys
    folder, source, _raw, _loader, _module = _runtime_fixture_module(tmp_path, monkeypatch)
    finder = FileFinder(str(folder), (SourceFileLoader, SOURCE_SUFFIXES))
    monkeypatch.setitem(sys.path_importer_cache, finder.path, finder)
    proof = stages.SourceFileSet()
    try:
        with proof.capture():
            spec = finder.find_spec(source.stem)
        assert spec is not None and Path(spec.origin) == source
        assert folder in _observed_paths(proof.directories)
        assert proof.statistics['runtime_code_reads'] == 0
        (folder / 'new-original.unknown-format').write_bytes(b'new explicit fixture input')
        with pytest.raises(ValueError):
            proof.verify()
    finally:
        _close_source(proof)


def test_runtime_explicit_material_binding_precedes_real_inspect_code_exemption(monkeypatch):
    import inspect
    import linecache
    module, source = _runtime_reader_module()
    physical = source.resolve()
    proof, normal_verifier = stages.SourceFileSet(), []
    proof.protect_material(physical)
    def rejected_material(path):
        assert path.resolve() == physical
        normal_verifier.append(path)
        raise ValueError('explicit-bound material verifier rejected this source')
    monkeypatch.setattr(proof, '_fingerprint', rejected_material)
    linecache.clearcache()
    try:
        with pytest.raises(ValueError) as rejected:
            with proof.capture():
                inspect.getsource(module.PdfReader)
        if normal_verifier:
            assert 'explicit-bound material verifier' in str(rejected.value)
        else:
            assert '链接' in str(rejected.value) and source.absolute() != physical
        assert proof.statistics['runtime_code_reads'] == 0
        assert proof.material_path(source) and proof.material_path(physical)
    finally:
        _close_source(proof)


def _runtime_data_version_target():
    import importlib
    module = importlib.import_module('pypdfium2.version')
    actor = module._version_pypdfium2
    return module, actor, Path(actor._FILE)


def test_runtime_data_cold_crop_captures_both_version_json_full_sha(tmp_path):
    import json
    import subprocess
    import sys
    script = r'''
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json, sys
sys.path[:0] = sys.argv[1:3]
import PySide6.QtCore
from pypdf import PdfWriter
from integrations.deeptutor_shchem_v1 import desktop_textbook_preparation_crops as product
from integrations.deeptutor_shchem_v1.desktop_section_source_stage import SourceFileSet
assert not any(name == 'pypdfium2' or name.startswith('pypdfium2.')
    or name == 'pypdfium2_raw' or name.startswith('pypdfium2_raw.') for name in sys.modules)
writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
document = BytesIO(); writer.write(document)
proof = SourceFileSet()
try:
    with proof.capture():
        result = product._render_page(document.getvalue(), 1)
    assert result['page_data'].startswith(b'\x89PNG\r\n\x1a\n')
    versions = [(path, fingerprint) for path, fingerprint in proof.files.values()
        if path.name == 'version.json' and path.parent.name in {'pypdfium2', 'pypdfium2_raw'}]
    assert {path.parent.name for path, _ in versions} == {'pypdfium2', 'pypdfium2_raw'}
    for path, fingerprint in versions:
        assert path == path.resolve()
        assert fingerprint['sha256'] == sha256(path.read_bytes()).hexdigest()
    assert proof.statistics['runtime_data_alias_reads'] == 2
    before = proof.statistics['source_bytes_hashed']
    proof.verify()
    assert proof.statistics['source_bytes_hashed'] - before >= sum(path.stat().st_size for path, _ in versions)
    print(json.dumps({'runtime_data_alias_reads': proof.statistics['runtime_data_alias_reads'],
        'runtime_code_reads': proof.statistics['runtime_code_reads'],
        'version_files': [{'path': str(path), 'sha256': fingerprint['sha256']} for path, fingerprint in versions],
        'width': result['width'], 'height': result['height'], 'image_bytes': len(result['page_data'])}))
finally:
    proof.close()
    assert not proof._watches and proof._watch_executor is None
'''
    overlay = Path(stages.__file__).resolve().parents[2]
    repo = Path(__file__).resolve().parents[4]
    result = subprocess.run([sys.executable, '-B', '-u', '-c', script, str(overlay), str(repo)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf8', timeout=60, check=True,
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    report = json.loads(result.stdout)
    (tmp_path / 'cold-runtime-data.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
    assert report['runtime_data_alias_reads'] == 2 and len(report['version_files']) == 2
    assert report['width'] > 0 and report['height'] > 0 and report['image_bytes'] > 0


def test_runtime_data_plain_json_and_explicit_material_actor_keep_raw_protection(monkeypatch):
    _module, actor, source = _runtime_data_version_target()
    for explicit in (False, True):
        proof, normal_verifier = stages.SourceFileSet(), []
        if explicit:
            proof.protect_material(source.resolve())
            def rejected_material(path):
                assert path.resolve() == source.resolve()
                normal_verifier.append(path)
                raise ValueError('explicit-bound version JSON verifier rejected this source')
            monkeypatch.setattr(proof, '_fingerprint', rejected_material)
        try:
            try:
                with proof.capture():
                    value = actor() if explicit else source.read_text(encoding='utf8')
            except ValueError as exc:
                if normal_verifier:
                    assert 'explicit-bound version JSON verifier' in str(exc)
                else:
                    assert '链接' in str(exc) and source.absolute() != source.resolve()
            else:
                assert explicit is False and value
                assert source.resolve() in _observed_paths(proof.files)
                proof.verify()
            assert proof.statistics['runtime_data_alias_reads'] == 0
        finally:
            _close_source(proof)


def test_runtime_data_own_fake_actor_spec_and_escaped_path_cannot_claim_alias(tmp_path, monkeypatch):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    import sys
    actual_module, _actor, runtime_json = _runtime_data_version_target()
    folder = tmp_path / 'OWN-DATA-shaped-package'; folder.mkdir()
    source, declared = folder / 'version_reader.py', folder / 'version.json'
    raw = b'{"version": "complete owned fixture", "evidence": "all fields remain source bytes"}'
    declared.write_bytes(raw)
    source.write_text("from pathlib import Path\nclass FakeActor:\n"
        "    _FILE = Path(__file__).with_name('version.json')\n"
        "    def read(self, escaped=None):\n"
        "        with open(self._FILE if escaped is None else escaped, 'rb') as stream:\n"
        "            return stream.read()\n", encoding='utf8')
    name = 'own_runtime_data_actor_fixture'
    loader = SourceFileLoader(name, str(source))
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, source, loader=loader))
    monkeypatch.setitem(sys.modules, name, module)
    loader.exec_module(module)
    # A forged live spec cannot turn the OWN method's actual code into package code.
    forged_loader = SourceFileLoader(name, actual_module.__spec__.origin)
    module.__spec__ = importlib.util.spec_from_file_location(name, actual_module.__spec__.origin, loader=forged_loader)
    module.__loader__ = forged_loader
    actor = module.FakeActor()
    proof = stages.SourceFileSet()
    try:
        with proof.capture():
            assert actor.read() == raw
        assert declared in _observed_paths(proof.files)
        assert proof.files[os.path.normcase(str(declared))][1]['sha256'] == sha256(raw).hexdigest()
        assert proof.statistics['runtime_data_alias_reads'] == 0
        declared.write_bytes(b'{"version": "changed owned fixture"}')
        with pytest.raises(ValueError):
            proof.verify()
    finally:
        _close_source(proof)
    escaped = stages.SourceFileSet()
    try:
        try:
            with escaped.capture():
                actor.read(runtime_json)
        except ValueError as exc:
            assert '链接' in str(exc) and runtime_json.absolute() != runtime_json.resolve()
        else:
            assert runtime_json.resolve() in _observed_paths(escaped.files)
            escaped.verify()
        assert escaped.statistics['runtime_data_alias_reads'] == 0
    finally:
        _close_source(escaped)
