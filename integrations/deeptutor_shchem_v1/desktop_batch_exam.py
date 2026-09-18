"""Project existing teacher decisions into the existing exam statistics model.

Only exact paper/number/max-score groups are combined. This is a read-only
snapshot of formal decisions, never a second grade book or a model call.
"""
from __future__ import annotations
from copy import deepcopy
from math import isfinite
import re
from .desktop_exam_data import ExamError, build_exam, digest
from .desktop_review_evidence import value
from .desktop_work_batches import WorkBatchStore, CONDITIONS

# These observations mean that a stored score is not a usable measurement for
# this snapshot. A timed but readable completed attempt may still have a score.
EXCLUDED = {'needs_review', 'missing_page', 'not_attempted', 'not_taught', 'unreadable'}


def collect_batch_exams(facade, batch_id, *, cancelled=lambda: False):
    store = WorkBatchStore(facade)
    batch = store.get_batch(batch_id)
    if not batch:
        raise ExamError('作业批次已不存在，请重新打开。')
    groups, unavailable = {}, []
    for member in store.members(batch):
        if cancelled():
            raise ExamError('已停止读取批次统计，原评分未改变。')
        binding = {k: member[k] for k in ('student_id', 'submission_id')}
        summary = facade.student_submission(**binding)
        if not summary.candidate_available:
            unavailable.append({'label': member['label'], 'reason': '暂无已有分析', **binding})
            continue
        review = facade.student_analysis_review(**binding)
        conditions = store.conditions(summary)
        matches = {value(m, 'match_id'): m for m in summary.matches}
        cells = []
        numbers = set()
        for item in review.items:
            match = matches.get(item.match_id)
            maximum = item.maximum_score
            number = str(item.question_number or '').strip()
            page = value(match, 'question_page_sha256')
            if (not match or not page or not number or number in numbers
                    or isinstance(maximum, bool) or not isinstance(maximum, (int, float))
                    or not isfinite(maximum) or maximum <= 0):
                cells = []
                break
            numbers.add(number)
            key = (str(page), number, float(maximum), str(value(match, 'reference_answer_page_sha256') or ''))
            observation = conditions.get('items', {}).get(item.match_id, {})
            condition = observation.get('condition', 'unmarked')
            blocked = conditions.get('source_changed', False) or condition in EXCLUDED
            score = item.latest_teacher_score
            valid = (not blocked and not isinstance(score, bool) and isinstance(score, (int, float)))
            valid = valid and isfinite(score) and 0 <= score <= maximum
            reason = ('原页对应改变，情况记录需核对' if conditions.get('source_changed') else
                      CONDITIONS.get(condition, '待核对') if blocked else
                      '未记录有效教师评分' if not valid else '')
            cells.append({'identity': key, 'score': score if valid else None,
                          'match_id': item.match_id, 'reason': reason, 'condition': condition,
                          'score_record': item.latest_scoring_decision_id})
        if any(a!=b and re.match(re.escape(a)+r'[（(.．]',b) for a in numbers for b in numbers):
            cells=[]  # a parent total and its own subquestion are not additive units
        if not cells:
            unavailable.append({'label': member['label'], 'reason': '题号、原题身份或满分不完整/重复', **binding})
            continue
        # A source/score edit during collection is a reason to refresh, not to
        # silently combine readings taken at different revisions.
        fresh = facade.student_analysis_review(**binding)
        if fresh.revision != review.revision or store.conditions(summary).get('revision') != conditions.get('revision'):
            raise ExamError('读取期间教师评分有更新，请重新生成批次统计。')
        cells.sort(key=lambda c: c['identity'])
        group_key = digest([c['identity'] for c in cells])
        groups.setdefault(group_key, []).append({'member': member, 'revision': review.revision, 'cells': cells})
    if store.get_batch(batch_id)['revision'] != batch['revision']:
        raise ExamError('读取期间批次成员有调整，请重新生成统计。')
    results = []
    for group_key, entries in groups.items():
        layout = entries[0]['cells']
        questions = [{'column': i + 2, 'question': c['identity'][1], 'max_score': c['identity'][2],
                      'knowledge': '', 'chapter': '', 'context': '来自已有作答的原题匹配；知识点未自动推断。'}
                     for i, c in enumerate(layout)]
        maximum = sum(q['max_score'] for q in questions)
        rows = [['学生', '班级'] + [q['question'] for q in questions]]
        for entry in entries:
            # Persist stable IDs locally; the API path still emits S0001 aliases.
            rows.append([entry['member']['student_id'], batch['class_label'] or '本批次'] +
                        [c['score'] for c in entry['cells']])
        book = {'source_name': '作业批次教师正式评分', 'source_sha256': digest(entries),
                'sheets': [{'name': '教师评分', 'rows': rows, 'formula_cells': [], 'hidden_rows': [], 'hidden': False}]}
        cfg = {'title': batch['title'] + ' · 教师评分快照', 'sheet': '教师评分', 'header_row': 1,
               'student_col': 0, 'class_col': 1, 'total_col': None, 'status_col': None,
               'max_score': maximum, 'pass_score': maximum * .6, 'excellent_score': maximum * .85,
               'questions': questions}
        exam = build_exam(book, cfg)
        for student, entry in zip(exam['students'], entries):
            student['local_label'] = entry['member']['label']
        exam['provenance'] = {'kind': 'teacher_work_batch', 'batch_id': batch_id,
                              'batch_revision': batch['revision'], 'paper_group': group_key,
                              'members': [{**{k: e['member'][k] for k in ('student_id', 'submission_id')},
                                           'review_revision': e['revision'], 'cells': deepcopy(e['cells'])}
                                          for e in entries]}
        for i, entry in enumerate(entries, 2):
            for cell in entry['cells']:
                if cell['reason']:
                    exam['issues'].append({'row': i, 'field': cell['identity'][1], 'detail': cell['reason']})
        exam['warnings'] += [
            '本快照只汇总匹配到的评分单元，不保证覆盖整张原卷；仅正式教师评分纳入，暂存及模型建议不计分。',
            '缺页、未作答、未学、难辨及待核对状态对应的分数排除；资料状况与零分不同。',
            '这是读取时快照，后续改分请重新生成；60%/85%仅为默认分析参考线，不是官方等级或教师设置。',
            f'本批共{len(batch["members"])}人，本组{len(entries)}人；原题页/题号/满分/答案来源完全一致才归为同组。']
        results.append({'group': group_key, 'count': len(entries), 'exam': exam})
    return {'batch': batch, 'groups': results, 'unavailable': unavailable}
