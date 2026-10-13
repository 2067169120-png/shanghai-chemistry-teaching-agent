"""Synthetic identity release/undo receipts and their evidence boundaries.

The runner supplies E: basetemp and TEMP/TMP. No API, Office, real student
records or new dependencies are needed; existing roster/XLSX fixtures are used.
"""
from copy import deepcopy
from datetime import date

import pytest

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, digest
from integrations.deeptutor_shchem_v1.desktop_exam_followup import (
    create_followup, link_basket, record_attempt,
)
from integrations.deeptutor_shchem_v1.desktop_exam_revision import (
    correct_score, review_task, task_freshness,
)
from integrations.deeptutor_shchem_v1.desktop_student_roster import (
    attach_roster, new_member, new_roster, set_exam_status,
)
from integrations.deeptutor_shchem_v1.desktop_student_roster_identity_actions import (
    ACTION_SCHEMA, bind_exam_identity, unbind_exam_identity,
    undoable_identity_actions, undo_identity_action,
)
from staging.coordination.deeptutor_gateway.tests.test_student_roster import (
    exam, roster_store, row,
)


@pytest.fixture
def identity_roster(roster_store):
    draft = new_roster('合成班A')
    draft['members'] = [new_member('合成身份甲'), new_member('合成身份乙')]
    for member, profile in zip(draft['members'], ('anonymous-A', 'anonymous-B')):
        member['profile_student_id'] = profile
    return roster_store.save(draft)


@pytest.fixture
def attached_exam(exam, identity_roster):
    return attach_roster(exam, identity_roster, reason='合成教师明确冻结本班名册')


@pytest.fixture
def identity_exam(attached_exam, identity_roster, roster_store):
    return bind_exam_identity(
        attached_exam, 'S0001', identity_roster['members'][0]['id'],
        store=roster_store, submission_id='work-A', reason='合成教师核对成绩行与原作答归属',
    )


def release(exam_value, *, identity='S0001', store=None, reason='合成教师明确解除本行身份'):
    return unbind_exam_identity(
        exam_value, identity, expected_binding_sha256=digest(row(exam_value, identity).get('identity_binding')),
        reason=reason, store=store,
    )


def undo(exam_value, action, *, store=None, reason='合成教师逐行复核后撤销原身份动作'):
    return undo_identity_action(
        exam_value, action['id'], expected_action_sha256=action['action_sha256'],
        expected_binding_sha256=digest(row(exam_value, action['exam_student_id']).get('identity_binding')),
        reason=reason, store=store,
    )


def assert_receipt(action, before, after, identity='S0001'):
    assert action['schema'] == ACTION_SCHEMA
    assert action['exam_student_id'] == identity
    assert action['before_row'] == row(before, identity)
    assert action['after_row'] == row(after, identity)
    assert action['before_row_sha256'] == digest(row(before, identity))
    assert action['after_row_sha256'] == digest(row(after, identity))
    assert action['source_basis'] == digest({key: before.get(key) for key in ('id', 'source', 'config', 'questions')})
    assert action['action_sha256'] == digest({key: value for key, value in action.items() if key != 'action_sha256'})
    before_ids = {value['id'] for value in before['students']}
    after_ids = {value['id'] for value in after['students']}
    assert action['added_roster_rows'] == [value for value in after['students'] if value['id'] not in before_ids]
    assert action['removed_roster_rows'] == [value for value in before['students'] if value['id'] not in after_ids]


def test_binding_wrapper_records_complete_receipt_and_detached_input(attached_exam, identity_exam):
    before = deepcopy(attached_exam)
    action = identity_exam['identity_history'][-1]
    assert action['action'] == 'bind_row'
    assert_receipt(action, attached_exam, identity_exam)
    assert len(action['removed_roster_rows']) == 1 and not action['added_roster_rows']
    assert action['removed_roster_rows'][0]['roster_only'] is True
    action['before_row']['scores']['1'] = -1
    assert attached_exam == before


def test_identical_binding_is_noop_without_duplicate_receipt(identity_exam, identity_roster, roster_store):
    before = deepcopy(identity_exam)
    repeated = bind_exam_identity(
        identity_exam, 'S0001', identity_roster['members'][0]['id'], store=roster_store,
        submission_id='work-A', reason='合成复核确认同一身份与原作答',
    )
    assert repeated == before and identity_exam == before
    repeated['students'][0]['scores']['1'] = -1
    assert identity_exam == before


def test_release_preserves_scores_source_file_and_old_retest(identity_exam, roster_store, tmp_path):
    source_path = tmp_path / 'synthetic.xlsx'
    source_bytes = source_path.read_bytes()
    task = create_followup(identity_exam, '1', ['S0001'], '合成检查完整反应条件', date.today().isoformat())
    task = link_basket(task, [{'key': 'synthetic-owned-question', 'title_zh': '合成复测题', 'source_zh': '合成夹具'}],
                       ['synthetic-owned-question'])
    task = record_attempt(task, 'S0001', date.today().isoformat(), 'completed', score=8, maximum=10,
                          note='合成实际作答记录', submission_id='work-A', roster_store=roster_store)
    identity_exam['followups'] = [task]
    before = deepcopy(identity_exam)
    released = release(identity_exam, store=roster_store)
    old, new = row(before, 'S0001'), row(released, 'S0001')
    expected = deepcopy(old)
    expected.pop('identity_binding')
    expected.pop('local_alias')
    expected['roster_only'] = False
    expected['identity_action_revision'] = new['identity_action_revision']
    assert new == expected
    assert new['identity_action_revision']
    assert identity_exam == before and released['followups'] == before['followups']
    assert source_path.read_bytes() == source_bytes
    assert {key: value for key, value in released.items() if key not in {'students', 'identity_history'}} == {
        key: value for key, value in before.items() if key not in {'students', 'identity_history'}
    }
    assert task_freshness(released, task)['state'] == 'blocked'
    action = released['identity_history'][-1]
    assert action['action'] == 'unbind_row'
    assert_receipt(action, before, released)
    assert len(action['added_roster_rows']) == 1 and not action['removed_roster_rows']
    pending = action['added_roster_rows'][0]
    assert pending['identity_binding']['member_id'] == old['identity_binding']['member_id']
    assert pending['roster_only'] and pending['total'] is None
    assert all(value is None for value in pending['scores'].values())


def test_release_graded_roster_only_row_keeps_observation_and_creates_new_pending_row(attached_exam, roster_store):
    pending = next(value for value in attached_exam['students'] if value.get('roster_only'))
    identity = pending['id']
    graded = set_exam_status(attached_exam, identity, 'present', reason='合成教师核对名册成员实到')
    for question in graded['questions']:
        graded = correct_score(graded, identity, question['question'], 12, '合成原作答逐题录分')
    before = deepcopy(graded)
    released = release(graded, identity=identity, store=roster_store)
    assert row(released, identity)['scores'] == row(before, identity)['scores']
    assert row(released, identity)['total'] == 48
    assert row(released, identity)['roster_only'] is False
    assert row(released, identity)['attendance_revision'] == row(before, identity)['attendance_revision']
    assert graded == before and released['score_changes'] == before['score_changes']
    action = released['identity_history'][-1]
    assert_receipt(action, before, released, identity)
    assert action['added_roster_rows'][0]['id'] != identity


def test_undo_binding_restores_unbound_row_and_original_placeholder(attached_exam, identity_exam, roster_store):
    before = deepcopy(identity_exam)
    action = identity_exam['identity_history'][-1]
    restored = undo(identity_exam, action, store=roster_store)
    expected = deepcopy(row(attached_exam, 'S0001'))
    expected['identity_action_revision'] = row(restored, 'S0001')['identity_action_revision']
    assert row(restored, 'S0001') == expected
    assert 'identity_binding' not in row(restored, 'S0001')
    assert row(restored, action['removed_roster_rows'][0]['id']) == action['removed_roster_rows'][0]
    assert identity_exam == before and restored['source'] == attached_exam['source']
    reversal = restored['identity_history'][-1]
    assert reversal['action'] == 'undo_identity_action'
    assert reversal['undone_action_id'] == action['id']
    assert reversal['undone_action_sha256'] == action['action_sha256']
    assert_receipt(reversal, identity_exam, restored)


@pytest.mark.parametrize('operation', ['release', 'rebind'])
def test_undo_identity_roundtrip_never_revives_old_task_or_relabels_retest(
        identity_exam, identity_roster, roster_store, operation):
    task = create_followup(identity_exam, '1', ['S0001'], '合成旧身份复练任务', date.today().isoformat())
    task = record_attempt(task, 'S0001', date.today().isoformat(), 'not_attempted', note='合成尚未作答',
                          submission_id='work-A', roster_store=roster_store)
    identity_exam['followups'] = [task]
    original = deepcopy(identity_exam)
    if operation == 'release':
        changed = release(identity_exam, store=roster_store)
    else:
        changed = bind_exam_identity(identity_exam, 'S0001', identity_roster['members'][1]['id'],
                                     store=roster_store, reason='合成教师核对后改绑另一个成员')
    action = changed['identity_history'][-1]
    restored = undo(changed, action, store=roster_store)
    old, new = row(original, 'S0001'), row(restored, 'S0001')
    assert new['scores'] == old['scores'] and new['total'] == old['total']
    assert {key: value for key, value in new['identity_binding'].items() if key != 'binding_revision'} == {
        key: value for key, value in old['identity_binding'].items() if key != 'binding_revision'
    }
    assert new['identity_binding']['binding_revision'] != old['identity_binding']['binding_revision']
    assert new['identity_action_revision']
    assert task_freshness(restored, task)['state'] == 'stale'
    assert restored['followups'] == original['followups'] and identity_exam == original
    checked = review_task(restored, task, '合成教师明确核对身份往返后保留旧教学安排')
    assert task_freshness(restored, checked)['state'] == 'current'
    assert checked['_baseline_evidence'] == task['_baseline_evidence']
    assert checked['attempts'] == task['attempts']
    assert checked['targets'][0]['baseline_score'] == task['targets'][0]['baseline_score']


def test_action_listing_is_detached_filtered_and_consumed_once(identity_exam, roster_store):
    released = release(identity_exam, store=roster_store)
    actions = undoable_identity_actions(released, 'S0001')
    assert [value['action'] for value in actions] == ['bind_row', 'unbind_row']
    assert undoable_identity_actions(released, 'S0002') == []
    actions[0]['reason'] = '外部改写候选'
    assert released['identity_history'][-2]['reason'] != actions[0]['reason']
    action = released['identity_history'][-1]
    restored = undo(released, action, store=roster_store)
    assert action['id'] not in {value['id'] for value in undoable_identity_actions(restored)}
    before = deepcopy(restored)
    with pytest.raises(ExamError):
        undo(restored, action, store=roster_store)
    assert restored == before


@pytest.mark.parametrize('reason', ['', ' ', None, True, '字' * 2001])
def test_release_and_undo_require_nonempty_reason(identity_exam, roster_store, reason):
    before = deepcopy(identity_exam)
    with pytest.raises(ExamError):
        release(identity_exam, store=roster_store, reason=reason)
    assert identity_exam == before
    released = release(identity_exam, store=roster_store)
    before = deepcopy(released)
    with pytest.raises(ExamError):
        undo(released, released['identity_history'][-1], store=roster_store, reason=reason)
    assert released == before


@pytest.mark.parametrize('damage', ['unbound', 'missing_row', 'duplicate_row', 'stale_binding'])
def test_release_rejects_ambiguous_or_stale_identity_without_partial_change(identity_exam, attached_exam, roster_store, damage):
    candidate = deepcopy(attached_exam if damage == 'unbound' else identity_exam)
    expected = digest(row(candidate, 'S0001').get('identity_binding'))
    if damage == 'missing_row':
        candidate['students'] = [value for value in candidate['students'] if value['id'] != 'S0001']
    elif damage == 'duplicate_row':
        candidate['students'].append(deepcopy(row(candidate, 'S0001')))
    elif damage == 'stale_binding':
        row(candidate, 'S0001')['identity_binding']['binding_revision'] = 'f' * 32
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        unbind_exam_identity(candidate, 'S0001', expected_binding_sha256=expected,
                             reason='合成拒绝不明确或过期身份', store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('damage', ['roster_id', 'roster_revision', 'member_id', 'profile_owner', 'duplicate_snapshot'])
def test_release_verifies_exact_frozen_roster_ownership(identity_exam, roster_store, damage):
    candidate = deepcopy(identity_exam)
    binding = row(candidate, 'S0001')['identity_binding']
    if damage == 'duplicate_snapshot':
        candidate['roster_snapshots'].append(deepcopy(candidate['roster_snapshots'][0]))
    else:
        binding[{'profile_owner': 'profile_student_id'}.get(damage, damage)] = 'f' * 32
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        release(candidate, store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('operation', ['release', 'undo'])
@pytest.mark.parametrize('damage', ['profile_missing', 'profile_reader_error', 'submission_wrong_owner', 'submission_missing', 'submission_reader_error'])
def test_identity_action_rechecks_live_profile_and_submission_ownership(identity_exam, roster_store, operation, damage):
    candidate = identity_exam if operation == 'release' else release(identity_exam, store=roster_store)
    action = candidate['identity_history'][-1]
    if damage == 'profile_missing':
        roster_store.profiles = lambda: [{'student_id': 'anonymous-B'}]
    elif damage == 'profile_reader_error':
        def unavailable(**kwargs):
            raise RuntimeError('PRIVATE_OWNERSHIP_SENTINEL')
        roster_store.profiles = unavailable
    elif damage == 'submission_wrong_owner':
        roster_store.submission = lambda **kwargs: {'student_id': 'anonymous-B', 'submission_id': 'work-A'}
    elif damage == 'submission_missing':
        roster_store.submission = lambda **kwargs: {'student_id': 'anonymous-A', 'submission_id': 'another-work'}
    else:
        def unavailable(**kwargs):
            raise RuntimeError('PRIVATE_OWNERSHIP_SENTINEL')
        roster_store.submission = unavailable
    before = deepcopy(candidate)
    with pytest.raises(ExamError) as rejected:
        release(candidate, store=roster_store) if operation == 'release' else undo(candidate, action, store=roster_store)
    assert candidate == before
    assert 'PRIVATE_OWNERSHIP_SENTINEL' not in str(rejected.value)


@pytest.mark.parametrize('damage', ['score', 'status', 'alias', 'new_binding', 'duplicate_row', 'missing_row'])
def test_undo_rejects_later_changes_to_target_row(identity_exam, roster_store, damage):
    candidate = release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    if damage == 'score':
        candidate = correct_score(candidate, 'S0001', '1', 0, '合成教师后来核对原题得分')
    elif damage == 'status':
        candidate = set_exam_status(candidate, 'S0001', 'exempt', reason='合成后来核对为免考')
    elif damage == 'alias':
        row(candidate, 'S0001')['local_label'] = '合成后来更正的来源标签'
    elif damage == 'new_binding':
        row(candidate, 'S0001')['identity_binding'] = deepcopy(action['before_row']['identity_binding'])
    elif damage == 'duplicate_row':
        candidate['students'].append(deepcopy(row(candidate, 'S0001')))
    else:
        candidate['students'] = [value for value in candidate['students'] if value['id'] != 'S0001']
    before = deepcopy(candidate)
    current = next((value for value in candidate['students'] if value['id'] == 'S0001'), {})
    with pytest.raises(ExamError):
        undo_identity_action(candidate, action['id'], expected_action_sha256=action['action_sha256'],
                             expected_binding_sha256=digest(current.get('identity_binding')),
                             reason='合成不得撤销后续核对', store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('damage', ['score', 'status', 'alias', 'missing', 'duplicate'])
def test_undo_release_rejects_modified_new_placeholder(identity_exam, roster_store, damage):
    candidate = release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    identity = action['added_roster_rows'][0]['id']
    pending = row(candidate, identity)
    if damage == 'score':
        pending['scores']['1'] = 0
    elif damage == 'status':
        candidate = set_exam_status(candidate, identity, 'absent', reason='合成后来核对占位成员缺考')
    elif damage == 'alias':
        pending['local_alias'] = '合成后来核对的占位别名'
    elif damage == 'missing':
        candidate['students'] = [value for value in candidate['students'] if value['id'] != identity]
    else:
        candidate['students'].append(deepcopy(pending))
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, action, store=roster_store)
    assert candidate == before


def test_undo_release_rejects_member_now_bound_to_another_score_row(identity_exam, identity_roster, roster_store):
    released = release(identity_exam, store=roster_store)
    action = deepcopy(released['identity_history'][-1])
    candidate = bind_exam_identity(released, 'S0002', identity_roster['members'][0]['id'],
                                   store=roster_store, reason='合成教师明确把成员绑定另一成绩行')
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, action, store=roster_store)
    assert candidate == before


def test_undo_binding_rejects_reused_original_placeholder_id(identity_exam, roster_store):
    action = deepcopy(identity_exam['identity_history'][-1])
    candidate = deepcopy(identity_exam)
    replacement = deepcopy(row(candidate, 'S0002'))
    replacement['id'] = action['removed_roster_rows'][0]['id']
    candidate['students'].append(replacement)
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, action, store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('field', ['id', 'source', 'config', 'questions'])
def test_undo_rejects_changed_source_or_scoring_mapping(identity_exam, roster_store, field):
    candidate = release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    if field == 'id':
        candidate['id'] = 'f' * 32
    elif field == 'questions':
        candidate['questions'][0]['knowledge'] = '合成后来核对的知识映射'
    else:
        candidate[field]['synthetic_changed_evidence'] = True
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, action, store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('damage', ['action_hash', 'reason', 'before_row', 'after_row', 'row_hash', 'added_row', 'duplicate_action'])
def test_undo_rejects_corrupt_receipt_without_rewriting_history(identity_exam, roster_store, damage):
    candidate = release(identity_exam, store=roster_store)
    expected = deepcopy(candidate['identity_history'][-1])
    action = candidate['identity_history'][-1]
    if damage == 'action_hash':
        action['action_sha256'] = '0' * 64
    elif damage == 'reason':
        action['reason'] = '合成损坏的历史依据'
    elif damage in {'before_row', 'after_row'}:
        action[damage]['scores']['1'] = -1
    elif damage == 'row_hash':
        action['after_row_sha256'] = '0' * 64
    elif damage == 'added_row':
        action['added_roster_rows'][0]['local_alias'] = '合成损坏的占位依据'
    else:
        candidate['identity_history'].append(deepcopy(action))
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, expected, store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('credential', ['action', 'binding', 'missing_action', 'legacy_action'])
def test_undo_requires_current_action_and_binding_credentials(identity_exam, roster_store, credential):
    candidate = release(identity_exam, store=roster_store)
    action = candidate['identity_history'][-1]
    action_id = action['id']
    expected_action = action['action_sha256']
    expected_binding = digest(None)
    if credential == 'action':
        expected_action = '0' * 64
    elif credential == 'binding':
        expected_binding = digest(action['before_row']['identity_binding'])
    elif credential == 'missing_action':
        action_id = 'f' * 32
    else:
        candidate['identity_history'].append({'id': 'legacy-without-receipt', 'action': 'unbind_row',
                                              'exam_student_id': 'S0001'})
        action_id = 'legacy-without-receipt'
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo_identity_action(candidate, action_id, expected_action_sha256=expected_action,
                             expected_binding_sha256=expected_binding,
                             reason='合成拒绝过期或旧版动作凭据', store=roster_store)
    assert candidate == before


def test_undo_preserves_unrelated_later_score_observations(identity_exam, roster_store):
    released = release(identity_exam, store=roster_store)
    action = deepcopy(released['identity_history'][-1])
    later = correct_score(released, 'S0002', '1', 0, '合成另一学生后来核对原题得分')
    before = deepcopy(later)
    restored = undo(later, action, store=roster_store)
    assert row(restored, 'S0002') == row(before, 'S0002')
    assert restored['score_changes'] == before['score_changes']
    assert later == before
    assert [value['id'] for value in restored['students'] if value['id'] != 'S0001'] == [
        value['id'] for value in before['students'] if value['id'] != 'S0001'
        and value['id'] not in {pending['id'] for pending in action['added_roster_rows']}
    ]


@pytest.mark.parametrize('operation', ['bind', 'release', 'undo', 'list'])
@pytest.mark.parametrize('corrupted_history', [None, {}, 'damaged-history', [None], ['damaged-entry']])
def test_corrupt_history_container_is_rejected_as_exam_error_without_replacement(
        identity_exam, identity_roster, roster_store, operation, corrupted_history):
    candidate = identity_exam if operation == 'release' else release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    candidate['identity_history'] = deepcopy(corrupted_history)
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        if operation == 'bind':
            bind_exam_identity(candidate, 'S0001', identity_roster['members'][1]['id'],
                               store=roster_store, reason='合成损坏历史不得继续绑定')
        elif operation == 'release':
            release(candidate, store=roster_store)
        elif operation == 'undo':
            undo(candidate, action, store=roster_store)
        else:
            undoable_identity_actions(candidate)
    assert candidate == before


@pytest.mark.parametrize('operation', ['bind', 'release'])
@pytest.mark.parametrize('damage', ['removed_snapshot', 'revision', 'profile_owner', 'duplicate_snapshot'])
def test_undo_rechecks_frozen_snapshot_before_restoring_any_binding(
        identity_exam, roster_store, operation, damage):
    candidate = deepcopy(identity_exam) if operation == 'bind' else release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    if damage == 'removed_snapshot':
        candidate['roster_snapshots'] = []
    elif damage == 'revision':
        candidate['roster_snapshots'][0]['roster']['revision'] = 'f' * 32
    elif damage == 'profile_owner':
        candidate['roster_snapshots'][0]['roster']['members'][0]['profile_student_id'] = 'anonymous-B'
    else:
        candidate['roster_snapshots'].append(deepcopy(candidate['roster_snapshots'][0]))
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        undo(candidate, action, store=roster_store)
    assert candidate == before


def test_undo_binding_restores_original_placeholder_order(attached_exam, identity_roster, roster_store):
    """Removed pending rows must return to their original exam positions."""
    placeholders = [value for value in attached_exam['students'] if value.get('roster_only')]
    first, second = placeholders
    original = deepcopy(attached_exam)
    rebound = bind_exam_identity(attached_exam, second['id'], identity_roster['members'][0]['id'],
                                 store=roster_store, reason='合成教师核对后修正占位行所属成员')
    restored = undo(rebound, rebound['identity_history'][-1], store=roster_store)
    assert [value['id'] for value in restored['students']] == [value['id'] for value in original['students']]
    assert row(restored, first['id']) == row(original, first['id'])
    assert attached_exam == original


@pytest.mark.parametrize('operation', ['release', 'undo', 'list'])
@pytest.mark.parametrize('damage', [
    'unknown_schema', 'unknown_action', 'missing_order', 'unhashable_order',
    'before_row_type', 'after_row_class', 'row_identity', 'added_row_type',
    'added_row_identity', 'removed_rows_type', 'non_json_extension', 'unversioned_undo',
    'undo_reference', 'before_row_score', 'before_row_total', 'before_row_source_row',
    'resigned_score_rewrite', 'resigned_source_rewrite',
])
def test_receipt_structure_damage_is_exam_error_even_with_recomputed_outer_hash(
        identity_exam, roster_store, operation, damage):
    candidate = release(identity_exam, store=roster_store)
    action = candidate['identity_history'][-1]
    if damage == 'unknown_schema':
        action['schema'] = 'shchem.exam-student-identity-action.future'
    elif damage == 'unknown_action':
        action['action'] = 'unknown_identity_action'
    elif damage == 'missing_order':
        action.pop('before_student_order')
    elif damage == 'unhashable_order':
        action['before_student_order'] = [['S0001']]
    elif damage == 'before_row_type':
        action['before_row'] = None
    elif damage == 'after_row_class':
        action['after_row'].pop('class')
    elif damage == 'row_identity':
        action['before_row']['id'] = 'S0999'
    elif damage == 'before_row_score':
        action['before_row']['scores']['1'] = '12'
    elif damage == 'before_row_total':
        action['before_row']['total'] = [48]
    elif damage == 'before_row_source_row':
        action['before_row']['excel_row'] = {'row': 2}
    elif damage == 'resigned_score_rewrite':
        action['before_row']['scores']['1'] = 0
    elif damage == 'resigned_source_rewrite':
        action['before_row']['excel_row'] += 1
    elif damage == 'added_row_type':
        action['added_roster_rows'] = [None]
    elif damage == 'added_row_identity':
        action['added_roster_rows'][0].pop('id')
    elif damage == 'removed_rows_type':
        action['removed_roster_rows'] = {}
    elif damage == 'non_json_extension':
        action['unknown_extension'] = {'cannot_serialize'}
    elif damage == 'unversioned_undo':
        candidate['identity_history'].append({'action': 'undo_identity_action', 'undone_action_id': action['id']})
    else:
        candidate = undo(candidate, action, store=roster_store)
        action = candidate['identity_history'][-1]
        action['undone_action_id'] = 'missing-original-action'
    if damage != 'non_json_extension':
        for key in ('before_row', 'after_row'):
            action[key + '_sha256'] = digest(action[key])
        action['action_sha256'] = digest({key: value for key, value in action.items() if key != 'action_sha256'})
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        if operation == 'release':
            release(candidate, store=roster_store)
        elif operation == 'undo':
            undo(candidate, action, store=roster_store)
        else:
            undoable_identity_actions(candidate)
    assert candidate == before


def test_valid_unknown_receipt_fields_and_legacy_history_are_preserved(identity_exam, roster_store):
    candidate = deepcopy(identity_exam)
    action = candidate['identity_history'][-1]
    action['future_annotation'] = {'teacher_text': '合成追加字段须按原字节语义保留'}
    action['action_sha256'] = digest({key: value for key, value in action.items() if key != 'action_sha256'})
    original_history = deepcopy(candidate['identity_history'])
    released = release(candidate, store=roster_store)
    restored = undo(released, released['identity_history'][-1], store=roster_store)
    assert released['identity_history'][:len(original_history)] == original_history
    assert restored['identity_history'][:len(original_history)] == original_history
    assert candidate['identity_history'] == original_history


@pytest.mark.parametrize('operation', ['release', 'undo'])
@pytest.mark.parametrize('damage', ['snapshot_container', 'snapshot_entry', 'roster_missing', 'members_container', 'member_entry'])
def test_malformed_frozen_ownership_shape_is_exam_error_without_rewriting_evidence(
        identity_exam, roster_store, operation, damage):
    candidate = deepcopy(identity_exam) if operation == 'release' else release(identity_exam, store=roster_store)
    action = deepcopy(candidate['identity_history'][-1])
    if damage == 'snapshot_container':
        candidate['roster_snapshots'] = None
    elif damage == 'snapshot_entry':
        candidate['roster_snapshots'] = [None]
    elif damage == 'roster_missing':
        candidate['roster_snapshots'][0].pop('roster')
    elif damage == 'members_container':
        candidate['roster_snapshots'][0]['roster']['members'] = None
    else:
        candidate['roster_snapshots'][0]['roster']['members'] = [None]
    before = deepcopy(candidate)
    with pytest.raises(ExamError):
        release(candidate, store=roster_store) if operation == 'release' else undo(candidate, action, store=roster_store)
    assert candidate == before


@pytest.mark.parametrize('operation', ['release', 'rebind'])
def test_repeated_undo_issues_new_identity_revision_and_preserves_all_old_task_evidence(
        identity_exam, identity_roster, roster_store, operation):
    task = create_followup(identity_exam, '1', ['S0001'], '合成连续身份往返复练', date.today().isoformat())
    task = link_basket(task, [{'key': 'synthetic-owned-question', 'title_zh': '合成复测题', 'source_zh': '合成夹具'}],
                       ['synthetic-owned-question'])
    task = record_attempt(task, 'S0001', date.today().isoformat(), 'completed', score=8, maximum=10,
                          note='合成最初实际作答依据', submission_id='work-A', roster_store=roster_store)
    original = deepcopy(task)
    candidate = deepcopy(identity_exam)
    seen_binding_revisions = {row(candidate, 'S0001')['identity_binding']['binding_revision']}
    seen_action_revisions = set()
    for index in range(2):
        candidate['followups'] = [task]
        before = deepcopy(candidate)
        assert task_freshness(candidate, task)['state'] == 'current'
        if operation == 'release':
            changed = release(candidate, store=roster_store)
            assert task_freshness(changed, task)['state'] == 'blocked'
        else:
            changed = bind_exam_identity(candidate, 'S0001', identity_roster['members'][1]['id'],
                                         store=roster_store, reason='合成另一个成员身份核对')
            assert task_freshness(changed, task)['state'] == 'stale'
        restored = undo(changed, changed['identity_history'][-1], store=roster_store)
        current_row = row(restored, 'S0001')
        assert current_row['identity_binding']['binding_revision'] not in seen_binding_revisions
        assert current_row['identity_action_revision'] not in seen_action_revisions
        seen_binding_revisions.add(current_row['identity_binding']['binding_revision'])
        seen_action_revisions.add(current_row['identity_action_revision'])
        assert task_freshness(restored, task)['state'] == 'stale'
        assert restored['followups'] == before['followups'] and candidate == before
        task = review_task(restored, task, '合成明确核对第%d次身份往返' % (index + 1))
        assert task_freshness(restored, task)['state'] == 'current'
        assert task['_baseline_evidence'] == original['_baseline_evidence']
        assert task['attempts'] == original['attempts']
        assert task['targets'][0]['baseline_score'] == original['targets'][0]['baseline_score']
        assert len(task['_evidence_reviews']) == index + 1
        candidate = restored
