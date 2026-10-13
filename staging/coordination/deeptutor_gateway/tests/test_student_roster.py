"""Stable local identities, explicit ownership and frozen exam membership.

Only synthetic students are used. All temporary roots are supplied by pytest;
the acceptance runner points its basetemp and process TEMP/TMP to E:.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, analyse
from integrations.deeptutor_shchem_v1.desktop_exam_ai import model_payload
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup, followup_html, record_attempt
from integrations.deeptutor_shchem_v1.desktop_exam_report import html_report
from integrations.deeptutor_shchem_v1.desktop_exam_revision import review_task, task_freshness
from integrations.deeptutor_shchem_v1.desktop_student_roster import (
    DesktopStudentRosterStore, attach_roster, bind_exam_row, empty_roster_exam,
    exam_status, new_member, new_roster, set_exam_status,
)


@pytest.fixture
def roster_store(tmp_path):
    return DesktopStudentRosterStore(
        tmp_path / 'private-roster',
        profiles=lambda: [SimpleNamespace(student_id='anonymous-A'), {'student_id': 'anonymous-B'}],
        submission=lambda **kwargs: {
            'student_id': 'anonymous-A', 'submission_id': 'work-A',
        },
    )


def save_roster(store, aliases=('本地甲', '本地乙'), *, label='合成班A'):
    roster = new_roster(label)
    roster['members'] = [new_member(alias) for alias in aliases]
    return store.save(roster)


@pytest.fixture
def exam(tmp_path):
    return example(tmp_path / 'synthetic.xlsx')[2]


def row(exam, identity):
    return next(value for value in exam['students'] if value['id'] == identity)


def bound_exam(exam, store, roster):
    attached = attach_roster(exam, roster, exam_class='合成班A', reason='教师明确选定本班名册')
    return bind_exam_row(attached, 'S0001', roster['members'][0]['id'], store=store,
                         reason='逐行核对本次成绩与本地成员')


def test_same_alias_members_and_rosters_keep_distinct_stable_uuids(roster_store):
    first = save_roster(roster_store, ('同名成员', '同名成员'))
    second = save_roster(roster_store, ('同名成员',), label='合成班A')
    identities = [first['id'], second['id']] + [value['id'] for value in first['members'] + second['members']]
    assert len(set(identities)) == len(identities)
    assert all(re.fullmatch('[0-9a-f]{32}', identity) for identity in identities)
    reopened = DesktopStudentRosterStore(roster_store.root)
    assert reopened.load(first['id']) == first
    assert reopened.load(second['id']) == second
    assert len(reopened.entries()) == 2


def test_save_and_read_are_detached_and_update_retains_identity(roster_store):
    draft = new_roster(' 本地班级 ')
    draft['members'] = [new_member(' 旧别名 ')]
    original = deepcopy(draft)
    saved = roster_store.save(draft)
    assert draft == original and saved['revision'] is not None
    edited = deepcopy(saved)
    edited['members'][0]['local_alias'] = '教师改名'
    updated = roster_store.save(edited, expected_revision=saved['revision'])
    assert updated['id'] == saved['id'] and updated['members'][0]['id'] == saved['members'][0]['id']
    assert updated['created_at'] == saved['created_at'] and updated['revision'] != saved['revision']
    loaded = roster_store.load(saved['id'])
    loaded['members'].clear()
    entries = roster_store.entries()
    entries[0]['label'] = '外部修改'
    assert roster_store.load(saved['id']) == updated


def test_update_requires_current_cas_revision_and_preserves_newer_data(roster_store):
    saved = save_roster(roster_store)
    other = DesktopStudentRosterStore(roster_store.root)
    changed = deepcopy(saved)
    changed['label'] = '另一窗口已保存'
    latest = other.save(changed, expected_revision=saved['revision'])
    before = roster_store.path.read_bytes()
    for expected in (None, saved['revision']):
        with pytest.raises(ExamError):
            roster_store.save(saved, expected_revision=expected)
        assert roster_store.path.read_bytes() == before
    assert roster_store.load(saved['id']) == latest


def test_cas_cannot_change_creation_identity(roster_store):
    saved = save_roster(roster_store)
    changed = deepcopy(saved)
    changed['created_at'] = 'different-creation-record'
    before = roster_store.path.read_bytes()
    with pytest.raises(ExamError):
        roster_store.save(changed, expected_revision=saved['revision'])
    assert roster_store.path.read_bytes() == before


@pytest.mark.parametrize('damage', ['duplicate_member', 'duplicate_profile', 'unknown_profile', 'extra_field', 'invalid_id'])
def test_invalid_member_or_profile_binding_does_not_create_store(roster_store, damage):
    draft = new_roster('合成班')
    draft['members'] = [new_member('甲'), new_member('乙')]
    if damage == 'duplicate_member':
        draft['members'][1]['id'] = draft['members'][0]['id']
    elif damage == 'duplicate_profile':
        for member in draft['members']:
            member['profile_student_id'] = 'anonymous-A'
    elif damage == 'unknown_profile':
        draft['members'][0]['profile_student_id'] = 'not-in-current-private-root'
    elif damage == 'extra_field':
        draft['members'][0]['student_name'] = '不在契约的字段'
    else:
        draft['members'][0]['id'] = '../outside'
    before = deepcopy(draft)
    with pytest.raises(ExamError):
        roster_store.save(draft)
    assert draft == before and not roster_store.path.exists()


def test_corrupt_library_is_preserved_and_cannot_be_replaced(roster_store):
    saved = save_roster(roster_store)
    corrupted = b'{"schema": "shchem.local-student-rosters.v1", "rosters": '
    roster_store.path.write_bytes(corrupted)
    with pytest.raises(ExamError):
        roster_store.save(saved, expected_revision=saved['revision'])
    with pytest.raises(ExamError):
        roster_store.load(saved['id'])
    assert roster_store.path.read_bytes() == corrupted


def test_failed_atomic_save_preserves_disk_revision_and_caller_input(roster_store, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_student_roster as module
    saved = save_roster(roster_store)
    edited = deepcopy(saved)
    edited['label'] = '尚未保存的教师输入'
    input_before, disk_before = deepcopy(edited), roster_store.path.read_bytes()
    calls = []
    def fail_replace(*args):
        calls.append(args)
        raise OSError('synthetic owned replacement failure')
    monkeypatch.setattr(module.os, 'replace', fail_replace)
    with pytest.raises(ExamError):
        roster_store.save(edited, expected_revision=saved['revision'])
    assert len(calls) == 1 and edited == input_before
    assert roster_store.path.read_bytes() == disk_before
    assert roster_store.load(saved['id']) == saved
    assert not list(roster_store.root.glob('.roster-*'))


def test_independent_process_lock_prevents_roster_overwrite(roster_store, tmp_path):
    saved = save_roster(roster_store)
    before = roster_store.path.read_bytes()
    ready = tmp_path / 'roster-lock-ready.txt'
    workspace = Path(__file__).resolve().parents[4]
    script = (
        'import sys\n'
        'from pathlib import Path\n'
        'sys.path.insert(0, sys.argv[3])\n'
        'from integrations.deeptutor_shchem_v1.desktop_state import _state_file_lock\n'
        'with _state_file_lock(Path(sys.argv[1]).resolve()):\n'
        '    Path(sys.argv[2]).write_text("locked", encoding="utf-8")\n'
        '    sys.stdin.readline()\n'
    )
    env = os.environ.copy()
    env['TEMP'] = env['TMP'] = 'E:/临时文件/UserTemp'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    child = subprocess.Popen(
        [sys.executable, '-B', '-c', script, str(roster_store.path), str(ready), str(workspace)],
        cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists() and child.poll() is None, 'synthetic roster lock process did not start'
        writer = DesktopStudentRosterStore(roster_store.root)
        with pytest.raises(ExamError):
            writer.save(saved, expected_revision=saved['revision'])
        assert roster_store.path.read_bytes() == before
    finally:
        try:
            child.communicate(input='release\n', timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=10)
    assert child.returncode == 0
    assert roster_store.load(saved['id']) == saved


def test_profile_and_submission_ownership_are_both_verified(roster_store):
    assert roster_store.check_profile('anonymous-A') is None
    assert roster_store.check_profile('anonymous-B') is None
    assert roster_store.check_submission('anonymous-A', 'work-A') is None
    for student_id, submission_id in (('anonymous-B', 'work-A'), ('anonymous-A', 'other-work'),
                                      ('unknown-profile', 'work-A'), (None, 'work-A')):
        with pytest.raises(ExamError):
            roster_store.check_submission(student_id, submission_id)


def test_submission_reader_error_does_not_leak_private_details(roster_store):
    def unavailable(**kwargs):
        raise RuntimeError('PRIVATE_PATH_AND_TOKEN_SENTINEL')
    roster_store.submission = unavailable
    with pytest.raises(ExamError) as rejected:
        roster_store.check_submission('anonymous-A', 'work-A')
    assert 'PRIVATE_PATH_AND_TOKEN_SENTINEL' not in str(rejected.value)


def test_attaching_matching_alias_never_infers_identity(exam, roster_store):
    roster = save_roster(roster_store, (exam['students'][0]['local_label'],))
    before = deepcopy(exam)
    attached = attach_roster(exam, roster, exam_class='合成班A', reason='明确选择班级名册')
    assert exam == before and 'identity_binding' not in row(attached, 'S0001')
    pending = [value for value in attached['students'] if value.get('roster_only')]
    assert len(pending) == 1 and pending[0]['identity_binding']['member_id'] == roster['members'][0]['id']
    assert pending[0]['attendance_status'] == 'not_entered' and pending[0]['total'] is None


def test_explicit_binding_preserves_source_scores_and_consumes_only_own_pending_row(exam, roster_store):
    roster = save_roster(roster_store)
    original = deepcopy(exam)
    bound = bound_exam(exam, roster_store, roster)
    first = row(bound, 'S0001')
    assert exam == original
    assert first['local_label'] == original['students'][0]['local_label']
    assert first['scores'] == original['students'][0]['scores'] and first['total'] == original['students'][0]['total']
    assert first['excel_row'] == original['students'][0]['excel_row'] and bound['source'] == original['source']
    assert first['local_alias'] == roster['members'][0]['local_alias']
    assert first['identity_binding']['member_id'] == roster['members'][0]['id']
    assert first['identity_binding']['roster_revision'] == roster['revision']
    assert len([value for value in bound['students'] if value.get('roster_only')]) == 1
    audit = bound['identity_history'][-1]
    assert audit['source'] == original['source'] and audit['excel_row'] == first['excel_row']
    assert audit['reason'] == '逐行核对本次成绩与本地成员'


def test_binding_submission_requires_explicit_member_profile_and_exact_owner(exam, roster_store):
    draft = new_roster('合成班A')
    member = new_member('绑定匿名档案成员')
    member['profile_student_id'] = 'anonymous-A'
    draft['members'] = [member]
    roster = roster_store.save(draft)
    attached = attach_roster(exam, roster, reason='明确选定档案成员')
    before = deepcopy(attached)
    with pytest.raises(ExamError):
        bind_exam_row(attached, 'S0001', member['id'], store=roster_store,
                      submission_id='wrong-work', reason='必须核对原作答')
    assert attached == before
    bound = bind_exam_row(attached, 'S0001', member['id'], store=roster_store,
                          submission_id='work-A', reason='核对当前匿名档案的原作答')
    binding = row(bound, 'S0001')['identity_binding']
    assert binding['profile_student_id'] == 'anonymous-A' and binding['submission_id'] == 'work-A'


def test_frozen_member_profile_must_still_exist_before_explicit_row_binding(exam, roster_store):
    draft = new_roster('合成班A')
    member = new_member('已删除匿名档案的本地成员')
    member['profile_student_id'] = 'anonymous-A'
    draft['members'] = [member]
    attached = attach_roster(exam, roster_store.save(draft), reason='先保存并明确选定名册')
    before = deepcopy(attached)
    roster_store.profiles = lambda: []
    with pytest.raises(ExamError):
        bind_exam_row(attached, 'S0001', member['id'], store=roster_store, reason='重新检查当前档案归属')
    assert attached == before


@pytest.mark.parametrize('damage', ['another_class', 'already_bound', 'unknown_member', 'ambiguous_row'])
def test_ambiguous_or_conflicting_row_binding_is_rejected(exam, roster_store, damage):
    roster = save_roster(roster_store)
    attached = attach_roster(exam, roster, reason='核对成员范围')
    student_id, member_id = 'S0001', roster['members'][0]['id']
    if damage == 'another_class':
        student_id = 'S0007'
    elif damage == 'already_bound':
        attached = bind_exam_row(attached, student_id, member_id, store=roster_store, reason='明确首行绑定')
        student_id = 'S0002'
    elif damage == 'unknown_member':
        member_id = new_member('仅有别名')['id']
    else:
        attached['students'].append(deepcopy(row(attached, student_id)))
    before = deepcopy(attached)
    with pytest.raises(ExamError):
        bind_exam_row(attached, student_id, member_id, store=roster_store, reason='拒绝猜测或合并')
    assert attached == before


def test_roster_snapshot_survives_live_roster_edits_and_cannot_be_silently_replaced(exam, roster_store):
    roster = save_roster(roster_store)
    attached = bound_exam(exam, roster_store, roster)
    frozen = deepcopy(attached['roster_snapshots'])
    edited = deepcopy(roster)
    edited['members'][0]['local_alias'] = '之后修改的别名'
    edited['members'].append(new_member('之后加入的成员'))
    latest = roster_store.save(edited, expected_revision=roster['revision'])
    assert attached['roster_snapshots'] == frozen
    assert row(attached, 'S0001')['local_alias'] == roster['members'][0]['local_alias']
    with pytest.raises(ExamError):
        attach_roster(attached, latest, reason='不得自动刷新考试快照')
    other = save_roster(roster_store, ('其他成员',), label='另一名册')
    with pytest.raises(ExamError):
        attach_roster(attached, other, exam_class='合成班A', reason='不得占用已确认班级快照')


@pytest.mark.parametrize('reason', ['', ' ', None, True, '字' * 2001])
def test_identity_or_status_changes_require_recorded_reason(exam, roster_store, reason):
    roster = save_roster(roster_store)
    before = deepcopy(exam)
    with pytest.raises(ExamError):
        attach_roster(exam, roster, reason=reason)
    with pytest.raises(ExamError):
        set_exam_status(exam, 'S0001', 'exempt', reason=reason)
    assert exam == before


def test_status_changes_keep_original_scores_and_require_known_status(exam):
    before = deepcopy(exam)
    changed = set_exam_status(exam, 'S0001', 'exempt', reason='教师核对免考状态')
    assert exam == before and exam_status(row(changed, 'S0001')) == 'exempt'
    assert row(changed, 'S0001')['scores'] == before['students'][0]['scores']
    assert row(changed, 'S0001')['total'] == before['students'][0]['total']
    restored = set_exam_status(changed, 'S0001', 'present', reason='补充实到核对记录')
    assert row(restored, 'S0001')['scores'] == before['students'][0]['scores']
    with pytest.raises(ExamError):
        set_exam_status(exam, 'S0001', 'unknown', reason='不支持该状态')


def test_marking_absent_row_present_does_not_invent_a_zero_or_original_score(exam):
    absent = next(value for value in exam['students'] if value['absent'])
    changed = set_exam_status(exam, absent['id'], 'present', reason='教师确认现已实到，成绩另待录入')
    restored = row(changed, absent['id'])
    assert not restored['absent'] and restored['total'] is None
    assert restored['scores'] == absent['scores'] and all(value is None for value in restored['scores'].values())


def test_explicit_exempt_placeholder_status_survives_binding_to_imported_scores(exam, roster_store):
    roster = save_roster(roster_store, ('明确免考成员',))
    attached = attach_roster(exam, roster, reason='先明确保存应考名册')
    placeholder = next(value for value in attached['students'] if value.get('roster_only'))
    exempt = set_exam_status(attached, placeholder['id'], 'exempt', reason='逐个核对名册成员免考记录')
    status_revision = row(exempt, placeholder['id'])['attendance_revision']
    bound = bind_exam_row(exempt, 'S0001', roster['members'][0]['id'], store=roster_store,
                          reason='随后明确核对原导入成绩归属')
    imported = row(bound, 'S0001')
    assert exam_status(imported) == 'exempt' and imported['attendance_revision'] == status_revision
    assert imported['scores'] == row(exam, 'S0001')['scores'] and imported['total'] == row(exam, 'S0001')['total']
    assert not any(value['id'] == placeholder['id'] for value in bound['students'])
    overall = analyse(bound, '合成班A')['overall']
    assert overall['enrolled'] == overall['exempt'] == 1 and overall['expected'] == overall['n'] == 0
    assert bound['identity_history'][-1]['replaced_roster_rows'][0]['attendance_status'] == 'exempt'


def test_confirmed_roster_cohort_does_not_double_count_unbound_imported_rows(exam, roster_store):
    roster = save_roster(roster_store)
    attached = bound_exam(exam, roster_store, roster)
    before = deepcopy(attached)
    report = analyse(attached, '合成班A')
    overall = report['overall']
    assert overall['enrolled'] == overall['roster_enrolled'] == 2
    assert overall['imported'] == 6 and overall['unbound'] == 5
    assert overall['n'] == overall['present'] == 1 and overall['not_entered'] == 1
    assert overall['expected'] == 2 and overall['unavailable'] == 1
    assert overall['mean'] == row(exam, 'S0001')['total'] and overall['pass_rate'] == 1
    assert len(report['students']) == 7 and sum(value['excluded_unbound'] for value in report['students']) == 5
    assert row(report, 'S0002')['total'] is None and all(value is None for value in row(report, 'S0002')['scores'].values())
    assert all(item['n'] == 1 and item['missing'] == 1 for item in report['items'])
    assert analyse(attached, '合成班B')['overall']['enrolled'] == 6
    assert attached == before and row(attached, 'S0002')['total'] == row(exam, 'S0002')['total']


def test_absent_exempt_and_not_entered_have_distinct_denominators_and_no_zero_projection(exam):
    changed = set_exam_status(exam, 'S0001', 'exempt', reason='明确核对免考')
    changed = set_exam_status(changed, 'S0002', 'not_entered', reason='本次资料尚未录入')
    original_scores = deepcopy(changed['students'])
    report = analyse(changed)
    overall = report['overall']
    assert overall['enrolled'] == 12 and overall['expected'] == 11
    assert overall['absent'] == overall['exempt'] == 1
    assert overall['not_entered'] == 1 and overall['unavailable'] == 2
    assert overall['present'] == 9 and overall['n'] == 8
    assert sum(value['n'] for value in report['distribution']) == 8
    assert all(value['total'] is None for value in report['students'] if value['attendance_status'] != 'present')
    assert changed['students'] == original_scores


def test_empty_roster_exam_and_whole_cohort_absence_have_defined_zero_population(roster_store):
    roster = save_roster(roster_store, ('实到待分', '缺考', '免考', '未录入'))
    empty = empty_roster_exam(roster, title='明确名册空考试', reason='成绩尚未导入，先核对应考名册')
    report = analyse(empty)
    assert report['overall']['enrolled'] == report['overall']['not_entered'] == 4
    assert report['overall']['n'] == 0 and report['items'] == report['knowledge'] == []
    assert report['overall']['mean'] is report['overall']['pass_rate'] is report['overall']['high_rate'] is None
    mixed = deepcopy(empty)
    for student, status in zip(mixed['students'], ('present', 'absent', 'exempt', 'not_entered')):
        mixed = set_exam_status(mixed, student['id'], status, reason='逐个核对实到与免考状态')
    overall = analyse(mixed)['overall']
    assert (overall['enrolled'], overall['expected'], overall['present'], overall['absent'],
            overall['exempt'], overall['not_entered'], overall['unavailable'], overall['n']) == (4, 3, 1, 1, 1, 1, 2, 0)
    whole_absent = deepcopy(empty)
    for student in empty['students']:
        whole_absent = set_exam_status(whole_absent, student['id'], 'absent', reason='明确核对本班全员缺考')
    overall = analyse(whole_absent)['overall']
    assert overall['absent'] == overall['expected'] == overall['enrolled'] == 4
    assert overall['n'] == overall['unavailable'] == 0 and overall['mean'] is None


def test_explicit_zero_is_valid_but_empty_roster_score_is_never_assumed_zero(roster_store):
    roster = save_roster(roster_store, ('明确零分', '尚无成绩'))
    empty = empty_roster_exam(roster, title='只有总分的明确名册考试', reason='教师选定待录入名册')
    entered = set_exam_status(empty, 'S0001', 'present', reason='核对原作答已提交')
    row(entered, 'S0001')['total'] = 0
    report = analyse(entered)
    assert report['overall']['n'] == 1 and report['overall']['mean'] == 0
    assert report['overall']['not_entered'] == report['overall']['unavailable'] == 1
    assert report['overall']['pass_rate'] == 0
    assert row(entered, 'S0002')['total'] is None


def test_unbound_imported_row_cannot_create_a_confirmed_roster_followup(exam, roster_store):
    attached = attach_roster(exam, save_roster(roster_store), reason='本次以明确名册为应考范围')
    with pytest.raises(ExamError):
        create_followup(attached, '1', ['S0001'], '先确认稳定成员身份', date.today().isoformat())


def test_followup_target_and_retest_records_freeze_original_binding_when_exam_is_rebound(exam, roster_store):
    draft = new_roster('合成班A')
    draft['members'] = [new_member('原绑定成员'), new_member('之后改绑成员')]
    draft['members'][0]['profile_student_id'] = 'anonymous-A'
    draft['members'][1]['profile_student_id'] = 'anonymous-B'
    roster = roster_store.save(draft)
    attached = bound_exam(exam, roster_store, roster)
    task = create_followup(attached, '1', ['S0001'], '核对原作答的完整条件', date.today().isoformat())
    frozen = deepcopy(task['targets'][0]['identity_binding'])
    task = record_attempt(task, 'S0001', date.today().isoformat(), 'not_attempted', note='尚未作答',
                          submission_id='work-A', roster_store=roster_store)
    before = deepcopy(task)
    rebound = bind_exam_row(attached, 'S0001', roster['members'][1]['id'], store=roster_store,
                            reason='核对原记录后明确修正所属成员')
    assert task_freshness(rebound, task)['state'] in {'stale', 'blocked'}
    assert task == before and task['targets'][0]['identity_binding'] == frozen
    assert task['attempts'][0]['identity_binding'] == frozen and task['attempts'][0]['submission_id'] == 'work-A'
    assert row(rebound, 'S0001')['identity_binding']['member_id'] != frozen['member_id']
    with pytest.raises(ExamError):
        record_attempt(task, 'S0001', date.today().isoformat(), 'missing',
                       submission_id='not-owned-work', roster_store=roster_store)
    assert task == before


def test_live_alias_edit_does_not_retroactively_expire_frozen_exam_task(exam, roster_store):
    roster = save_roster(roster_store)
    attached = bound_exam(exam, roster_store, roster)
    task = create_followup(attached, '1', ['S0001'], '依据本次明确身份复练', date.today().isoformat())
    changed = deepcopy(roster)
    changed['members'][0]['local_alias'] = '名册之后的新别名'
    roster_store.save(changed, expected_revision=roster['revision'])
    assert task_freshness(attached, task)['state'] == 'current'
    exempt = set_exam_status(attached, 'S0001', 'exempt', reason='之后核对为免考')
    assert task_freshness(exempt, task)['state'] == 'blocked'


def test_identity_and_attendance_round_trips_do_not_silently_revive_old_task(exam, roster_store):
    roster = save_roster(roster_store)
    attached = bound_exam(exam, roster_store, roster)
    task = create_followup(attached, '1', ['S0001'], '原成员明确绑定时创建的任务', date.today().isoformat())
    first_revision = row(attached, 'S0001')['identity_binding']['binding_revision']
    other = bind_exam_row(attached, 'S0001', roster['members'][1]['id'], store=roster_store, reason='教师确认更正所属成员')
    back = bind_exam_row(other, 'S0001', roster['members'][0]['id'], store=roster_store, reason='再次核对后返回原成员')
    assert row(back, 'S0001')['identity_binding']['member_id'] == roster['members'][0]['id']
    assert row(back, 'S0001')['identity_binding']['binding_revision'] != first_revision
    assert task_freshness(back, task)['state'] == 'stale'
    exempt = set_exam_status(attached, 'S0001', 'exempt', reason='核对为免考')
    present_again = set_exam_status(exempt, 'S0001', 'present', reason='再核对为实到')
    assert row(present_again, 'S0001')['total'] == row(attached, 'S0001')['total']
    assert task_freshness(present_again, task)['state'] == 'stale'
    confirmed = review_task(back, task, '明确核对身份往返记录后保留原复练安排')
    assert task_freshness(back, confirmed)['state'] == 'current'
    assert confirmed['_baseline_evidence'] == task['_baseline_evidence']
    assert confirmed['targets'][0]['identity_binding'] == task['targets'][0]['identity_binding']
    assert confirmed['targets'][0]['reviewed_identity_binding'] == row(back, 'S0001')['identity_binding']


def test_anonymous_model_and_html_outputs_exclude_new_identity_fields_and_local_aliases(exam, roster_store):
    draft = new_roster('PRIVATE_CLASS_ALIAS_SENTINEL')
    member = new_member('PRIVATE_MEMBER_ALIAS_SENTINEL')
    member['profile_student_id'] = 'anonymous-A'
    draft['members'] = [member]
    roster = roster_store.save(draft)
    attached = attach_roster(exam, roster, exam_class='合成班A', reason='本地教师核对')
    bound = bind_exam_row(attached, 'S0001', member['id'], store=roster_store,
                          submission_id='work-A', reason='本地明确选择原作答')
    report = analyse(bound)
    task = create_followup(bound, '1', ['S0001'], '检查实验条件', date.today().isoformat())
    task = record_attempt(task, 'S0001', date.today().isoformat(), 'missing', note='当前只记录资料不全')
    payload = json.dumps(model_payload(report, {}, '', True), ensure_ascii=False)
    outputs = [payload, html_report(report), followup_html([task])]
    private_terms = ['PRIVATE_CLASS_ALIAS_SENTINEL', 'PRIVATE_MEMBER_ALIAS_SENTINEL',
                     roster['id'], roster['revision'], member['id'], 'anonymous-A', 'work-A']
    assert all(term not in output for output in outputs for term in private_terms)
    assert all(key not in payload for key in ('identity_binding', 'local_alias', 'roster_snapshots',
                                              'profile_student_id', 'submission_id', 'excel_row'))
    assert 'S0001' in payload and 'S0001' in outputs[2]


@pytest.fixture
def private_service(tmp_path, monkeypatch):
    """Use the actual student manager, redirected away from normal user data."""
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from test_desktop_student_private_paths import facade
    workspace = tmp_path / 'owned-workspace'
    (workspace / 'sh-chem-db').mkdir(parents=True)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'owned-private-userdata'))
    normal = DesktopPaths.from_workspace(workspace)
    restored = replace(DesktopPaths.from_workspace(workspace, state_root=workspace / 'outputs' / 'restore-a'),
                       settings_root=normal.settings_root)
    service = facade(restored)
    yield service, restored, facade
    service.shutdown()


def test_roster_facade_uses_isolated_private_root_and_preserves_anonymous_profile_contract(private_service):
    service, paths, factory = private_service
    profile = service.create_student_profile(grade='高三', retention_days=30, consent_recorded=True)
    anonymous_profiles = service.student_profiles()
    draft = new_roster('PRIVATE_RESTORE_CLASS')
    member = new_member('PRIVATE_RESTORE_LOCAL_ALIAS')
    member['profile_student_id'] = profile.student_id
    draft['members'] = [member]
    saved = service.save_student_roster(draft)
    store = service.student_roster_store()
    assert store.path == paths.student_visual_root / 'student-rosters.v1.json'
    assert not store.path.is_relative_to(paths.workspace_root)
    assert not (paths.state_root / 'student-rosters.v1.json').exists()
    assert service.student_profiles() == anonymous_profiles
    assert service.student_local_identity(profile.student_id) == (('PRIVATE_RESTORE_CLASS', 'PRIVATE_RESTORE_LOCAL_ALIAS', member['id']),)
    reopened = factory(paths)
    other = factory(replace(paths, state_root=paths.state_root.with_name('restore-b')))
    try:
        assert reopened.student_rosters() == [saved]
        assert reopened.student_profiles() == anonymous_profiles
        assert other.student_rosters() == [] and other.student_profiles() == ()
        assert not (paths.state_root / 'student-visual-v1').exists()
    finally:
        reopened.shutdown()
        other.shutdown()
