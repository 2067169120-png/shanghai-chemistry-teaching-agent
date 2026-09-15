"""Small display projection: original-page bindings, never a grading authority."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


@dataclass(frozen=True)
class ReviewRegion:
    role: str
    label_zh: str
    page_sha256: str = field(repr=False)
    box: tuple[float, float, float, float] = field(repr=False)


def evidence_regions(candidate, match):
    """Accept only finite normalized boxes on this match's actual source pages."""
    bindings = {role: match.get(key) for role, key in (
        ('question_pages', 'question_page_sha256'),
        ('student_work_pages', 'student_work_page_sha256'),
        ('reference_answer_pages', 'reference_answer_page_sha256'))}
    regions = []

    def append(anchor, role, label):
        if not isinstance(anchor, Mapping) or not bindings.get(role):
            return
        box = anchor.get('bbox')
        if anchor.get('page_sha256') != bindings[role] or not isinstance(box, Mapping):
            return
        numbers = tuple(box.get(k) for k in ('x', 'y', 'width', 'height'))
        if any(isinstance(n, bool) or not isinstance(n, (float, int)) or not isfinite(n) for n in numbers):
            return
        x, y, w, h = numbers
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
            return
        regions.append(ReviewRegion(role, label, bindings[role], tuple(float(n) for n in numbers)))

    for key, role, label in (
        ('student_answer_anchor', 'student_work_pages', 'AI定位：学生作答'),
        ('question_anchor', 'question_pages', 'AI定位：题目'),
        ('reference_answer_anchor', 'reference_answer_pages', 'AI定位：参考答案')):
        append(candidate.get(key), role, label)
    for i, point in enumerate(candidate.get('scoring_points') or (), 1):
        if isinstance(point, Mapping):
            for j, anchor in enumerate(point.get('evidence') or (), 1):
                append(anchor, 'student_work_pages', f'评分点{i}：作答证据{j}')
    return tuple(regions)


def matched_page(summary, match_id, role):
    """Return the exact matched page, not an adjacent student or a filename guess."""
    names = {'question_pages': 'question_page_sha256',
             'student_work_pages': 'student_work_page_sha256',
             'reference_answer_pages': 'reference_answer_page_sha256'}
    if role not in names:
        return None
    match = next((m for m in value(summary, 'matches', ()) if value(m, 'match_id') == match_id), None)
    digest = value(match, names[role])
    return next((p for p in value(summary, 'pages', ())
                 if digest and value(p, 'role') == role and value(p, 'sha256') == digest), None)


def review_counts(review):
    """An unrecorded score is missing, not zero. No class/mastery extrapolation."""
    items = value(review, 'items', ())
    scores = [value(i, 'latest_teacher_score') for i in items]
    scored = [s for s in scores if isinstance(s, (float, int)) and not isinstance(s, bool) and isfinite(s)]
    return {'items': len(items), 'scored': len(scored), 'unscored': len(items) - len(scored),
            'recorded_score_sum': sum(scored),
            'stale_diagnoses': sum(bool(value(i, 'diagnostic_requires_reconfirmation', False)) for i in items)}
