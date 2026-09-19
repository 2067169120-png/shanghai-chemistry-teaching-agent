"""Local score corrections and evidence freshness; no source-file or model writes.

Fingerprints describe content, not a global revision counter. Task evidence is
scoped to its mapped question and explicitly selected students. Old baselines,
observed attempts and produced files are never relabelled as new observations.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from math import fsum, isfinite
from .desktop_exam_data import ExamError, digest

BASIS_SCHEMA = 'shchem.followup-score-evidence.v1'


def now():
    return datetime.now(timezone.utc).isoformat()


def score_revision(exam):
    return digest({key: exam.get(key) for key in (
        'id', 'title', 'source', 'config', 'maximum', 'pass_score',
        'excellent_score', 'questions', 'students')})


def advice_revision(exam, paper, notes, scope):
    return digest({'exam': score_revision(exam), 'paper': paper,
                   'notes': notes, 'scope': scope,
                   'issues': exam.get('issues', []), 'warnings': exam.get('warnings', [])})


def full_mapping(exam):
    questions = exam['questions']
    return bool(questions and abs(fsum(q['max_score'] for q in questions) - exam['maximum']) <= .001)


def _unique(rows, field, value, label):
    found = [row for row in rows if row.get(field) == value]
    if len(found) != 1:
        raise ExamError(label + '缺失或身份重复，请重新核对；不会按姓名猜测。')
    return found[0]


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ExamError('请填写本次修改或核对的依据（1至2000字）。')
    return value.strip()


def _valid_score(value, maximum):
    return (value is None or (type(value) in (int, float) and isfinite(value)
                             and 0 <= value <= maximum))


def correct_score(exam, student_id, field, value, reason):
    """Return a detached exam. None means explicitly missing, never numeric zero.

    Fully mapped rows are re-totalled from all mapped scores. With a partial
    mapping a changed item clears the now-unverified total; the teacher must
    explicitly correct that independent total. The original XLSX is unchanged.
    """
    reason = _reason(reason)
    student = _unique(exam['students'], 'id', student_id, '学生')
    if student['absent']:
        raise ExamError('缺考记录不能直接改成得分；请先核对缺考状态并重新导入。')
    is_total = field == '__total__'
    if is_total and full_mapping(exam):
        raise ExamError('完整题目映射的总分由各题重算，请修改对应题目，不单独覆盖总分。')
    question = None if is_total else _unique(exam['questions'], 'question', field, '题目映射')
    maximum = exam['maximum'] if is_total else question['max_score']
    if not _valid_score(value, maximum):
        raise ExamError(f'请输入0至{maximum:g}的有限数值，或明确选择缺失；空白不等于0。')
    history = exam.get('score_changes', [])
    if not isinstance(history, list):
        raise ExamError('改分历史格式异常，请保留记录并核对，不能覆盖为空历史。')
    old = student['total'] if is_total else student['scores'].get(field)
    changed = deepcopy(exam)
    if old == value:
        return changed
    row = _unique(changed['students'], 'id', student_id, '学生')
    before = {'score': old, 'total': row['total']}
    if is_total:
        row['total'] = value
        total_policy = 'teacher_confirmed_independent_total'
    else:
        row['scores'][field] = value
        if full_mapping(exam):
            scores = [row['scores'].get(q['question']) for q in exam['questions']]
            if any(not _valid_score(v, q['max_score']) for v, q in zip(scores, exam['questions'])):
                raise ExamError('同一行其他得分存在异常，请先核对；没有部分应用改分。')
            row['total'] = fsum(scores) if all(v is not None for v in scores) else None
            total_policy = 'recomputed_complete_mapping'
        else:
            row['total'] = None
            total_policy = 'partial_mapping_total_requires_confirmation'
    affected_fields = {field, '总分'} if not is_total else {'总分'}
    changed['issues'] = [issue for issue in changed['issues']
                         if not (issue.get('row') == row['excel_row']
                                 and issue.get('field') in affected_fields)]
    if value is None:
        changed['issues'].append({'row': row['excel_row'], 'field': '总分' if is_total else field,
                                  'detail': '教师已标记缺失；不按0分统计'})
    if row['total'] is None and not is_total:
        changed['issues'].append({'row': row['excel_row'], 'field': '总分',
            'detail': '小题修改后总分待核对：映射不完整或仍有缺失，不沿用旧总分'})
    changed.setdefault('score_changes', []).append({
        'at': now(), 'student_id': student_id, 'field': field, 'reason': reason,
        'before': before, 'after': {'score': value, 'total': row['total']},
        'total_policy': total_policy,
        'before_revision': score_revision(exam), 'after_revision': score_revision(changed),
    })
    return changed


def task_basis(exam, task):
    if task.get('exam_id') != exam.get('id'):
        raise ExamError('任务与当前考试身份不一致，不能重新绑定到另一次考试。')
    question = _unique(exam['questions'], 'question', task.get('question'), '原考试题目')
    targets = task.get('targets')
    if not isinstance(targets, list) or not targets:
        raise ExamError('任务缺少已确认学生，请重建任务。')
    seen = set(); rows = []
    for target in targets:
        identity = target.get('exam_student_id')
        if not isinstance(identity, str) or identity in seen:
            raise ExamError('任务学生身份异常，请重新核对。')
        seen.add(identity)
        row = _unique(exam['students'], 'id', identity, '任务学生')
        if target.get('local_label') != row['local_label']:
            raise ExamError('学生标识已变化，不能仅凭本次导入S编号自动迁移；请重建任务。')
        rows.append({key: row[key] for key in ('id', 'local_label', 'class', 'excel_row', 'absent', 'total')}
                    | {'score': row['scores'].get(task['question'])})
    return {'exam_id': exam['id'], 'question': deepcopy(question), 'students': rows,
            'thresholds': [exam['maximum'], exam['pass_score'], exam['excellent_score']]}


def _stamp(basis):
    return {'schema': BASIS_SCHEMA, 'basis': deepcopy(basis), 'sha256': digest(basis)}


def _read_stamp(value):
    if (not isinstance(value, dict) or value.get('schema') != BASIS_SCHEMA
            or not isinstance(value.get('basis'), dict)
            or value.get('sha256') != digest(value['basis'])):
        raise ExamError('任务成绩依据校验异常，请保留原记录并重建任务；不能重新计算哈希掩盖损坏。')
    return value['basis']


def bind_task(exam, task):
    """Used only when a new task is created, never as an implicit legacy upgrade."""
    result = deepcopy(task)
    result['_baseline_evidence'] = _stamp(task_basis(exam, task))
    return result


def recorded_basis(task):
    baseline = _read_stamp(task['_baseline_evidence']) if '_baseline_evidence' in task else None
    reviews = task.get('_evidence_reviews', [])
    if not isinstance(reviews, list):
        raise ExamError('任务核对历史格式异常，请保留原记录。')
    for review in reviews:
        if not isinstance(review, dict):
            raise ExamError('任务核对记录不完整。')
        baseline = _read_stamp(review.get('evidence'))
    return baseline


def task_freshness(exam, task):
    try:
        current = task_basis(exam, task)
        previous = recorded_basis(task)
        if any(row['absent'] or row['score'] is None for row in current['students']):
            return {'state': 'blocked', 'message': '当前任务学生缺考或本题得分缺失；请补充证据，不能确认旧复练依据。'}
        if previous is None:
            return {'state': 'unverified', 'message': '旧任务缺少完整成绩版本依据，须明确核对后继续使用。'}
        if digest(previous) != digest(current):
            return {'state': 'stale', 'message': '成绩或题目映射已变化：复练依据待核对，旧成品和实际复测仍保留。'}
        return {'state': 'current', 'message': '复练成绩依据与当前分析一致；教学安排仍由教师判断。'}
    except (ExamError, KeyError, TypeError, ValueError) as error:
        return {'state': 'blocked', 'message': getattr(error, 'message_zh', '成绩依据不完整，请保留记录并核对。')}


def review_task(exam, task, reason):
    reason = _reason(reason)
    status = task_freshness(exam, task)
    if status['state'] == 'blocked':
        raise ExamError(status['message'])
    result = deepcopy(task)
    if status['state'] == 'current':
        return result
    previous = recorded_basis(task)
    result.setdefault('_evidence_reviews', []).append({
        'at': now(), 'reason': reason, 'previous_evidence': _stamp(previous) if previous else None,
        'evidence': _stamp(task_basis(exam, task)),
        'decision': 'teacher_retains_plan_after_review',
    })
    return result


def task_review_text(exam, task):
    status = task_freshness(exam, task)
    lines = [status['message'], '原目标：' + task.get('goal', ''),
             '原始baseline_score不覆盖；下列核对记录不等于新的学生作答。']
    try:
        current = task_basis(exam, task)
        previous = recorded_basis(task)
        old_rows = {row['id']: row for row in previous['students']} if previous else {}
        lines.append('当前题目：' + str(current['question']['question']) +
                     '；当前主知识点：' + (current['question'].get('knowledge') or '未映射'))
        for row in current['students']:
            old = old_rows.get(row['id'], {})
            old_value = old.get('score', '未记录完整依据')
            new_value = '缺失' if row['score'] is None else row['score']
            lines.append(f"{row['local_label']} ({row['id']})：本题 {old_value} → {new_value}；"
                         f"总分 {old.get('total', '未知')} → {row['total']}")
        if previous and previous['question'] != current['question']:
            lines.append('题目映射/满分/标签亦有变化，请同时核对完整题面。')
    except (ExamError, KeyError, TypeError):
        pass
    lines.append('确认只表示教师决定保留当前任务，不自动改目标、不证明知识掌握或提分。')
    return '\n'.join(lines)
