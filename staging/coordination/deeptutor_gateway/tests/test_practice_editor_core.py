"""Detached selection edits and append-only export records: no Qt or Office."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError
from integrations.deeptutor_shchem_v1.desktop_exam_practice import (
    freeze_selection, selection_items, revise_selection, request_revision, append_export)


@pytest.fixture
def task():
    value = {'id': 'task-a', 'exam_id': 'exam-a', 'question': '1', 'goal': '原目标',
             'links': [], 'exports': [{'note': '旧输出'}], 'attempts': [{'note': '旧复测'}],
             'targets': [{'exam_student_id': 'S0001'}], 'due_date': '2026-09-20'}
    rows = [{'key': key, 'title_zh': '主题' + key, 'source_zh': '合成资料'} for key in ('a', 'b', 'c')]
    return freeze_selection(value, rows, ['a', 'b', 'c'])


def test_reorder_remove_and_goal_keep_history_and_original(task):
    before = deepcopy(task)
    changed = revise_selection(task, ['c', 'a'], '  新目标  ')
    assert [row['key'] for row in selection_items(changed)] == ['c', 'a']
    assert changed['goal'] == '新目标' and request_revision(changed) != request_revision(task)
    assert changed['exports'] == before['exports'] and changed['attempts'] == before['attempts']
    assert task == before and changed['targets'] == before['targets']
    changed['attempts'].append({'note': 'another'})
    assert task == before


@pytest.mark.parametrize('keys', [[], ['a', 'a'], ['unknown'], 'a', None, [None], [['a']]])
def test_invalid_edit_keys_are_rejected_without_mutation(task, keys):
    before = deepcopy(task)
    with pytest.raises(ExamError):revise_selection(task, keys, '目标')
    assert task == before


@pytest.mark.parametrize('goal', ['', '  ', None, True, '字' * 2001])
def test_invalid_goal_is_not_saved(task, goal):
    with pytest.raises(ExamError):revise_selection(task, ['a'], goal)


def test_corruption_cannot_be_repaired_by_silently_rehashing(task):
    task['practice_set']['items'][0]['title_zh'] = '未核对的替换'
    with pytest.raises(ExamError):revise_selection(task, ['a'], '目标')


def test_repeated_unchanged_edit_keeps_content_revision(task):
    assert request_revision(revise_selection(task, ['a', 'b', 'c'], task['goal'])) == request_revision(task)


def result():
    return {'pdf_status': 'generated', 'artifacts': [
        {'artifact_id': role, 'path': role + '.pdf'} for role in ('student', 'teacher')]}


def test_export_merges_fresh_history_and_is_idempotent(task):
    expected = deepcopy(task)
    task['attempts'].append({'note': '操作开始后新录入'})
    task['exports'].append({'note': '其他已完成输出'})
    before = deepcopy(task); files = result()
    preview = SimpleNamespace(preview_id='preview-a', preview_hash='hash-a')
    changed = append_export(task, expected, preview, files)
    assert changed['attempts'] == task['attempts'] and changed['exports'][:-1] == task['exports']
    assert changed['exports'][-1]['goal'] == expected['goal']
    assert append_export(changed, expected, preview, files) == changed
    files['artifacts'][0]['path'] = 'later-change'
    assert changed['exports'][-1]['artifacts'][0]['path'] != 'later-change'
    assert task == before


@pytest.mark.parametrize('field,value', [('goal','改目标'), ('id','other-task'), ('exam_id','other-exam')])
def test_export_from_changed_request_is_rejected(task, field, value):
    expected = deepcopy(task); task[field] = value
    with pytest.raises(ExamError):
        append_export(task, expected, SimpleNamespace(preview_id='p', preview_hash='h'), result())


@pytest.mark.parametrize('payload', [{}, {'pdf_status':'pending'}, {'pdf_status':'generated'},
    {'pdf_status':'generated','artifacts':[]}, {'pdf_status':'generated','artifacts':[{}]}])
def test_incomplete_export_result_is_not_registered(task, payload):
    with pytest.raises(ExamError):
        append_export(task, task, SimpleNamespace(preview_id='p', preview_hash='h'), payload)
