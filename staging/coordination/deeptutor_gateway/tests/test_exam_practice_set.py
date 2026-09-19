"""Independent task references; synthetic renderer tests are not Office acceptance."""
from copy import deepcopy
from datetime import date
from threading import Event
from pathlib import Path
import pytest
from test_desktop_mixed_paper_service import setup, _add_word
from mixed_pagination_test_support import paginate_and_read
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, ExamStore
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup, practice_request
from integrations.deeptutor_shchem_v1.desktop_exam_practice import (
    freeze_selection, selection_items, paper_session, TaskPaperSession, request_revision)
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import _ACTIVE, MixedPaperError


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
