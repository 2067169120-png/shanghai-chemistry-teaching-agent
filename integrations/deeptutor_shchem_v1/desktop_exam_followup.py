"""Exam-scoped practice plans and observed retest records; no new student bank.

References are to the existing basket. A planned task or a model recommendation
never counts as a completed attempt; different papers are not treated as an
interval scale on which a score increase proves learning.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timezone
from math import isfinite
from uuid import uuid4
from .desktop_exam_data import ExamError, digest


def _date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ExamError('请填写有效日期（年-月-日）。') from None


def create_followup(exam, question_number, student_ids, goal, due_date):
    question = next((q for q in exam['questions'] if q['question'] == question_number), None)
    if not question:
        raise ExamError('请从本次考试选择一个已映射的题目。只有总分时不能创建知识点复练。')
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
        raise ExamError('请填写本次要检查的学习目标（不超过2000字）。')
    _date(due_date)
    if not student_ids or len(student_ids) != len(set(student_ids)):
        raise ExamError('请明确选择学生，且不要重复。')
    students = {s['id']: s for s in exam['students']}
    targets = []
    for sid in student_ids:
        s = students.get(sid)
        score = s['scores'].get(question_number) if s else None
        if not s or s['absent'] or score is None:
            raise ExamError('缺考或本题缺少有效成绩的学生，请先补充证据，不作为本次失分复练对象。')
        targets.append({'exam_student_id': sid, 'local_label': s['local_label'],
                        'baseline_score': score, 'baseline_maximum': question['max_score']})
    from .desktop_exam_revision import bind_task
    return bind_task(exam, {'schema': 'shchem.exam-followup.v1', 'id': uuid4().hex, 'exam_id': exam['id'],
            'created_at': datetime.now(timezone.utc).isoformat(), 'question': question_number,
            'knowledge': question['knowledge'], 'goal': goal.strip(), 'due_date': due_date,
            'targets': targets, 'library_filter': None, 'links': [], 'exports': [], 'attempts': []})


def link_basket(task, basket, keys):
    if not keys or len(set(keys)) != len(keys):
        raise ExamError('请勾选不重复的真实题篮项目。')
    rows = {r['key']: r for r in basket}
    if any(k not in rows for k in keys):
        raise ExamError('题篮已变化，请重新读取再勾选。')
    changed = deepcopy(task)
    changed['links'] = [{'key': key, 'basket_fingerprint': digest(rows[key]),
                         'title': rows[key].get('title_zh', '未命名题目'),
                         'source': rows[key].get('source_zh', '')} for key in keys]
    # Old outputs stay as history; each export records its own ordered links.
    return changed


def practice_request(task, basket, projection):
    linked = task.get('links', [])
    if not linked:
        raise ExamError('请先从真实题库选题，再关联本任务的题篮项目。')
    current = {r['key']: r for r in basket}
    for link in linked:
        if link['key'] not in current or digest(current[link['key']]) != link['basket_fingerprint']:
            raise ExamError('关联题目已移出题篮或内容发生变化，请重新核对并关联；不会悄悄换题。')
    return {'schema_version': 'shchem.desktop-mixed-paper-request.v1',
            'title': '复练 · ' + task['goal'][:80], 'subtitle': '根据考试第' + task['question'] + '题安排',
            'mode': 'daily_practice', 'duration_minutes': 20, 'show_question_scores': True,
            'basket_sha256': projection['basket_sha256'],
            'section_order': [r['key'] for r in linked], 'settings_by_key': {}}


def record_attempt(task, student_id, attempt_date, status, score=None, maximum=None, note=''):
    if student_id not in {r['exam_student_id'] for r in task['targets']}:
        raise ExamError('该学生不在本任务范围内。')
    when = _date(attempt_date)
    if when > date.today():
        raise ExamError('实际复测日期不能在未来；计划日期请填在任务中。')
    if status not in {'completed', 'not_attempted', 'missing'}:
        raise ExamError('请选择已作答、未作答或资料不全。')
    if not isinstance(note, str) or len(note) > 4000:
        raise ExamError('复测说明不能超过4000字。')
    if status == 'completed':
        if not task.get('links'):
            raise ExamError('请先关联本次使用的真实练习题，再记录完成结果。')
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n) for n in (score, maximum)):
            raise ExamError('请填写本次实得分和实际满分。')
        if not 0 <= score <= maximum or maximum <= 0:
            raise ExamError('本次分数应在0与实际满分之间。')
        if not note.strip():
            raise ExamError('请简述本次作答依据或仍需核对的地方。')
    else:
        score = maximum = None
    changed = deepcopy(task)
    changed['attempts'].append({'id': uuid4().hex, 'student_id': student_id,
        'date': when.isoformat(), 'status': status, 'score': score, 'maximum': maximum,
        'note': note.strip(), 'links': deepcopy(task['links']),
        'recorded_at': datetime.now(timezone.utc).isoformat()})
    return changed


def latest_attempts(task):
    # Append-only corrections: most recent entered record is the current teacher
    # observation, while prior records remain visible in the saved task.
    latest = {}
    for row in task.get('attempts', []):
        latest[row['student_id']] = row
    return latest


def task_summary(task):
    latest = latest_attempts(task)
    complete = sum(r['status'] == 'completed' for r in latest.values())
    return f"{complete}/{len(task['targets'])}名已记录实际作答 · {len(task['links'])}项真实题目 · 计划{task['due_date']}"


def followup_text(tasks):
    text = ['复练与复测记录（本地教师记录）', '不同题目的分数不直接作提升量；未记录不算零分或已进步。']
    for task in tasks:
        text.extend(['', '目标：' + task['goal'], '原考试题号：' + task['question'], task_summary(task)])
        for row in task['links']:
            text.append('练习：' + row['title'] + ' / ' + row['source'])
        for sid, row in latest_attempts(task).items():
            label = next(t['local_label'] for t in task['targets'] if t['exam_student_id'] == sid)
            result = (f"{row['score']}/{row['maximum']}" if row['status'] == 'completed' else
                      '未作答' if row['status'] == 'not_attempted' else '资料不全')
            text.append(f"{label}：{row['date']} {result}；{row['note']}")
    return '\n'.join(text)


def followup_html(tasks, *, include_names=False):
    from html import escape
    export = deepcopy(tasks)
    if not include_names:
        for task in export:
            for target in task['targets']:
                target['local_label'] = target['exam_student_id']
    return '<section><h2>复练与实际复测</h2><p>目标、备注和题目标题可能含教师自由文字，请自行核对个人信息。</p><pre style="white-space:pre-wrap">' + escape(followup_text(export)) + '</pre></section>'
