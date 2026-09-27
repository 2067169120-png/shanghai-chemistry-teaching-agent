"""Independent task references; synthetic renderer tests are not Office acceptance."""
from copy import deepcopy
from datetime import date
from threading import Event
from pathlib import Path
import hashlib
import pytest
from test_desktop_mixed_paper_service import setup, _add_word
from mixed_pagination_test_support import paginate_and_read
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, ExamStore
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup, practice_request
from integrations.deeptutor_shchem_v1.desktop_exam_practice import (
    freeze_selection, selection_items, paper_session, TaskPaperSession, request_revision)
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import _ACTIVE, MixedPaperError
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID, DRAFT_SCHEMA
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore, DesktopStateError


@pytest.fixture
def selected(setup, tmp_path):
    engine, state, words, _, _ = setup
    engine.facade._reader_stop_event = Event()
    key = _add_word(engine, words)
    exam = example(tmp_path / 'exam.xlsx')[2]
    task = create_followup(exam, '1', ['S0001'], '核对完整题目条件', date.today().isoformat())
    task = freeze_selection(task, state.basket(), [key])
    return engine, state, words, exam, task


def session_for(engine, task):
    session = TaskPaperSession(engine.facade, task)
    for name in ('_core_bundle_builder', '_docx_builder', '_word_validator', '_core_context'):
        setattr(session.service, name, getattr(engine, name))
    return session


def preview_for(session, task):
    request = practice_request(task, session.basket(), session.paper_basket_projection())
    return session.create_paper_preview(request)


def test_saved_set_reopens_after_basket_is_cleared(selected, tmp_path):
    engine, state, _, exam, task = selected
    store = ExamStore(tmp_path / 'exams'); store.save({'exam': exam, 'followups': [task]})
    state.remove_basket_item(task['links'][0]['key'])
    reopened = store.load(exam['id'])['followups'][0]
    session = session_for(engine, reopened)
    assert session.basket() and state.basket() == []
    before = state.basket()
    preview = preview_for(session, reopened)
    assert preview.preview_model['sections'][0]['key'] == task['links'][0]['key']
    assert state.basket() == before


def test_task_output_does_not_replace_global_active_preview(selected):
    engine, state, _, _, task = selected
    state.save_draft(_ACTIVE, {'preview_id': 'unrelated-global', 'preview_hash': 'unchanged'})
    state.save_draft('paper-current', {'title': '教师原组卷草稿'})
    before = deepcopy(state.snapshot())
    session = session_for(engine, task)
    preview = paginate_and_read(session.service, preview_for(session, task))
    session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    state.remove_basket_item(task['links'][0]['key'])
    state.add_to_basket({'key': 'another-teachers-question', 'title_zh': '不属于本任务'})
    exported = session.export_paper_preview(preview.preview_id, preview.preview_hash)
    assert exported['pdf_status'] == 'generated' and len(exported['artifacts']) == 4
    assert all(Path(row['path']).is_file() for row in exported['artifacts'])
    assert state.snapshot()['drafts'][_ACTIVE] == before['drafts'][_ACTIVE]
    assert state.snapshot()['drafts']['paper-current'] == before['drafts']['paper-current']
    assert [row['key'] for row in state.basket()] == ['another-teachers-question']


def test_two_tasks_have_independent_active_preview_pointers(selected):
    engine, _, _, _, task = selected
    other = deepcopy(task); other['id'] = 'b' * 32
    a = session_for(engine, task); b = session_for(engine, other)
    p = preview_for(a, task); q = preview_for(b, other)
    a.service._load(p.preview_id, require_current=True)
    b.service._load(q.preview_id, require_current=True)
    with pytest.raises(MixedPaperError):
        b.service._load(p.preview_id, require_current=True)


def test_original_source_revision_change_still_blocks_generation(selected):
    engine, state, words, _, task = selected
    before = deepcopy(state.snapshot())
    words.row['revision'] = 'changed-on-disk'
    with pytest.raises(MixedPaperError):
        preview_for(session_for(engine, task), task)
    assert state.snapshot() == before


@pytest.mark.parametrize('damage', ['checksum', 'row', 'link', 'empty', 'schema', 'duplicate'])
def test_corrupt_set_never_falls_back_to_current_basket(selected, damage):
    engine, _, _, _, task = selected
    bad = deepcopy(task)
    if damage == 'checksum': bad['practice_set']['sha256'] = '0' * 64
    elif damage == 'row': bad['practice_set']['items'][0]['title_zh'] = '悄悄换题'
    elif damage == 'link': bad['links'][0]['key'] = 'other'
    elif damage == 'empty': bad['practice_set']['items'] = []
    elif damage == 'schema': bad['practice_set']['schema'] = 'unknown'
    else:
        bad['links'] *= 2; bad['practice_set']['items'] *= 2
    with pytest.raises(ExamError):
        paper_session(engine.facade, bad)


def test_freeze_is_detached_and_legacy_requires_explicit_selection(selected):
    engine, state, _, _, task = selected
    old = deepcopy(task); old.pop('practice_set')
    assert paper_session(engine.facade, old) is engine.facade
    frozen = freeze_selection(old, state.basket(), [task['links'][0]['key']])
    detached = selection_items(frozen); detached[0]['title_zh'] = 'edited'
    assert selection_items(frozen) != detached and 'practice_set' not in old


def test_only_request_changes_expire_approval(selected):
    _, _, _, _, task = selected
    original = request_revision(task)
    updated = deepcopy(task); updated['attempts'].append({'note': '实际复测记录'})
    assert request_revision(updated) == original
    updated['goal'] = '新目标'
    assert request_revision(updated) != original


def test_invalid_basket_identity_is_not_guessed(selected):
    _, _, _, _, task = selected
    with pytest.raises(ExamError): freeze_selection(task, [{'key': 'x'}, {'key': 'x'}], ['x'])
    with pytest.raises(ExamError): freeze_selection(task, [{'title_zh': '只有标题'}], ['x'])


def test_task_output_with_existing_global_draft_keeps_global_undo_and_versions(selected):
    engine, state, _, _, task = selected
    projection = engine.projection()
    public = engine.open_draft_session()
    public.read(projection)
    payload = {'schema_version': DRAFT_SCHEMA, 'order': [row['key'] for row in projection['items']],
               'excluded': [], 'settings': {row['key']: row['settings'] for row in projection['items']},
               'settings_ui': {'title': '公共草稿', 'subtitle': '', 'mode': 'daily_practice',
                               'duration_minutes': 40, 'show_question_scores': False}}
    public.save(payload, remember=False)
    changed = deepcopy(payload); changed['settings_ui']['title'] = '教师正在编辑的公共草稿'
    public.save(changed)
    state.save_draft(_ACTIVE, {'preview_id': 'unrelated-global', 'preview_hash': 'unchanged'})
    before = state.snapshot()
    draft_version = state.draft_snapshot(DRAFT_ID)
    session = session_for(engine, task)
    assert DRAFT_ID not in session.state.snapshot()['drafts']
    assert DRAFT_ID not in session.state.snapshot()['draft_revisions']
    preview = paginate_and_read(session.service, preview_for(session, task))
    session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    assert len(session.export_paper_preview(preview.preview_id, preview.preview_hash)['artifacts']) == 4
    assert state.draft_snapshot(DRAFT_ID) == draft_version
    assert state.snapshot()['drafts'][_ACTIVE] == before['drafts'][_ACTIVE]
    assert state.snapshot()['basket'] == before['basket']
    assert public.undo_count == 1
    public.undo()
    assert state.draft_snapshot(DRAFT_ID).record['payload'] == payload
    # Unrelated public undo cannot invalidate the task's approval either.
    assert session.export_paper_preview(preview.preview_id, preview.preview_hash)['status'] == 'completed'


@pytest.mark.parametrize('identity', ['task', 'exam'])
def test_task_namespace_isolates_full_pagination_approval_and_export(selected, identity):
    engine, state, _, _, task = selected
    other = deepcopy(task)
    other['id' if identity == 'task' else 'exam_id'] = 'c' * 32
    a, b = session_for(engine, task), session_for(engine, other)
    p = paginate_and_read(a.service, preview_for(a, task))
    q = paginate_and_read(b.service, preview_for(b, other))
    a.approve_paper_preview(p.preview_id, p.preview_hash)
    b.approve_paper_preview(q.preview_id, q.preview_hash)
    state.clear_basket()
    for session, own, foreign in ((a, p, q), (b, q, p)):
        assert len(session.export_paper_preview(own.preview_id, own.preview_hash)['artifacts']) == 4
        with pytest.raises(MixedPaperError):
            session.service._load(foreign.preview_id)
    assert state.basket() == []


def test_task_request_version_blocks_old_preview_but_attempt_history_does_not(selected):
    engine, _, _, _, task = selected
    a = session_for(engine, task)
    preview = paginate_and_read(a.service, preview_for(a, task))
    a.approve_paper_preview(preview.preview_id, preview.preview_hash)
    observed = deepcopy(task); observed['attempts'].append({'note': '合成复测观察'})
    compatible = session_for(engine, observed)
    assert compatible.export_paper_preview(preview.preview_id, preview.preview_hash)['status'] == 'completed'
    changed = deepcopy(task); changed['goal'] = '教师修改后的目标'
    current = session_for(engine, changed)
    with pytest.raises(MixedPaperError):
        current.service._load(preview.preview_id)
    fresh = preview_for(current, changed)
    with pytest.raises(MixedPaperError):
        a.service._load(preview.preview_id, require_current=True)
    assert current.service._load(fresh.preview_id, require_current=True)


@pytest.mark.parametrize('target', ['basket', 'window', 'public_draft', 'other_active', 'revision', 'delete_preview'])
def test_task_transaction_cannot_mutate_any_unowned_state(selected, target):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    preview = preview_for(session, task)
    frozen = state.path.read_bytes()
    def illegal(value):
        if target == 'basket':
            value['basket'].clear()
        elif target == 'window':
            value['window'] = {'geometry': 'hijacked'}
        elif target == 'public_draft':
            value['drafts'][DRAFT_ID] = {'teacher': 'overwrite'}
        elif target == 'other_active':
            value['drafts']['exam-practice-active-foreign'] = {'preview_id': preview.preview_id}
        elif target == 'revision':
            value['draft_revisions'].clear()
        else:
            del value['drafts'][preview.preview_id]
    with pytest.raises(ExamError):
        session.state._update(illegal)
    assert state.path.read_bytes() == frozen


@pytest.mark.parametrize('owner', ['global', 'other_task', 'old_task_revision'])
def test_arbitrary_preview_key_cannot_overwrite_another_owner(selected, owner):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    if owner == 'global':
        projection = engine.projection()
        preview = engine.create_preview(practice_request(task, state.basket(), projection))
        record = state.snapshot()['drafts'][preview.preview_id]
    else:
        other = deepcopy(task)
        if owner == 'other_task':
            other['id'] = 'd' * 32
        else:
            other['goal'] = '同任务其他版本'
        foreign = session_for(engine, other)
        preview = preview_for(foreign, other)
        record = foreign.state.snapshot()['drafts'][preview.preview_id]
    frozen = state.path.read_bytes()
    def forge(value):
        value['drafts'][preview.preview_id] = deepcopy(record)
        value['drafts'][_ACTIVE] = {'preview_id': preview.preview_id, 'preview_hash': preview.preview_hash}
    with pytest.raises(ExamError):
        session.state._update(forge)
    assert state.path.read_bytes() == frozen
    with pytest.raises(MixedPaperError):
        session.service._load(preview.preview_id)


@pytest.mark.parametrize('change', ['active', 'record', 'owner_revision'])
def test_task_approval_checks_fresh_active_record_and_owner_inside_parent_transaction(selected, monkeypatch, change):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    preview = paginate_and_read(session.service, preview_for(session, task))
    original = state._update
    other = DesktopStateStore(state.root)
    writes = []
    def interleaved(operation, **kwargs):
        if change == 'active':
            other.save_draft(session.state._active, {'preview_id': 'mixed-preview-' + 'f' * 32, 'preview_hash': 'a' * 64})
        else:
            key = session.state._prefix + preview.preview_id
            record = other.snapshot()['drafts'][key]
            if change == 'record':
                record['record']['pages_read'] = []
            else:
                record['request_revision'] = 'new-task-version'
            other.save_draft(key, record)
        writes.append(other.path.read_bytes())
        return original(operation, **kwargs)
    monkeypatch.setattr(state, '_update', interleaved)
    with pytest.raises(MixedPaperError):
        session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    assert len(writes) == 1 and state.path.read_bytes() == writes[0]
    assert state.snapshot()['drafts'][session.state._prefix + preview.preview_id]['record']['approval'] is None


def test_task_final_source_hash_gate_runs_on_task_view_inside_transaction(selected, monkeypatch):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    original = session.service._word_validator
    def source_changed(rows):
        original(rows)
        session.state._items[0]['title_zh'] = '合成并发来源变更'
    monkeypatch.setattr(session.service, '_word_validator', source_changed)
    frozen = state.path.read_bytes()
    with pytest.raises(MixedPaperError, match='题篮已变化'):
        preview_for(session, task)
    assert state.path.read_bytes() == frozen


@pytest.mark.parametrize('phase', ['approve', 'export'])
def test_task_original_source_change_cannot_approve_or_export_stale_preview(selected, phase):
    engine, state, words, _, task = selected
    session = session_for(engine, task)
    preview = paginate_and_read(session.service, preview_for(session, task))
    if phase == 'export':
        session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    words.row['revision'] = 'changed-source-after-preview'
    frozen = state.path.read_bytes()
    with pytest.raises(MixedPaperError):
        getattr(session, phase + '_paper_preview')(preview.preview_id, preview.preview_hash)
    assert state.path.read_bytes() == frozen


@pytest.mark.parametrize('when', ['before', 'after'])
def test_task_transaction_save_failure_is_not_success_or_retried(selected, monkeypatch, when):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    original, calls = state._write_unlocked, []
    def failed(value, **kwargs):
        calls.append(True)
        if when == 'after':
            original(value, **kwargs)
        raise OSError('synthetic uncertain write')
    monkeypatch.setattr(state, '_write_unlocked', failed)
    with pytest.raises(DesktopStateError):
        preview_for(session, task)
    assert len(calls) == 1
    monkeypatch.setattr(state, '_write_unlocked', original)
    owned = [key for key in state.snapshot()['drafts'] if key.startswith(session.state._prefix)]
    assert len(owned) == (1 if when == 'after' else 0)


def test_task_noop_and_raised_callback_never_write_parent_state(selected):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    preview = preview_for(session, task)
    frozen = state.path.read_bytes()
    session.state._update(lambda value: None)
    session.state.save_draft(preview.preview_id, session.state.snapshot()['drafts'][preview.preview_id])
    assert state.path.read_bytes() == frozen
    def fail(value):
        value['drafts'][preview.preview_id]['approval'] = {'status': 'not-committed'}
        raise RuntimeError('synthetic callback failed')
    with pytest.raises(RuntimeError):
        session.state._update(fail)
    assert state.path.read_bytes() == frozen


def test_legacy_unowned_preview_and_finished_files_are_preserved_for_fresh_task_preview(selected):
    engine, state, _, _, task = selected
    old = paginate_and_read(engine, engine.create_preview(practice_request(task, state.basket(), engine.projection())))
    engine.approve(old.preview_id, old.preview_hash)
    exported = engine.export(old.preview_id, old.preview_hash)
    old_files = {row['path']: hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() for row in exported['artifacts']}
    old_record = state.draft_snapshot(old.preview_id)
    session = session_for(engine, task)
    # This is the pre-fix task layout: the active pointer was scoped but the
    # preview record had no task/version ownership. Do not infer ownership.
    state.save_draft(session.state._active, {'preview_id': old.preview_id, 'preview_hash': old.preview_hash})
    with pytest.raises(MixedPaperError, match='重新生成'):
        session.service._load(old.preview_id, require_current=True)
    fresh = preview_for(session, task)
    assert fresh.preview_id != old.preview_id
    assert state.draft_snapshot(old.preview_id) == old_record
    assert {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in old_files} == old_files


def test_task_transaction_read_failure_never_uses_global_or_cached_state(selected, monkeypatch):
    engine, state, _, _, task = selected
    session = session_for(engine, task)
    preview_for(session, task)
    frozen = state.path.read_bytes()
    def unavailable():
        raise OSError('synthetic read failure')
    monkeypatch.setattr(state, '_read_unlocked', unavailable)
    called = []
    with pytest.raises(DesktopStateError):
        session.state._update(lambda value: called.append(True))
    assert not called and state.path.read_bytes() == frozen
