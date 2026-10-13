"""Project a narrowly recognized printed citation out of a fresh work copy.

The source snapshot, block identities, roles and native presentation never
change. Unknown wording or an unprovable rich-text mapping keeps the full text.
This describes classroom wording, not the authority of the printed citation.
"""
from copy import deepcopy
from difflib import SequenceMatcher
import re

from .desktop_composition_inline import MARKER, display_tokens
from .desktop_lesson_composition import validate_resource
from .desktop_word_question_attributes import _REGIONS


_SPACE = re.compile(r'[ \t\u3000]*')
_LEAD = re.compile(
    r'(?:例(?:题)?[ \t]*[0-9]{1,3}[.．、]?|[0-9]{1,3}[.．、]|'
    r'【例(?:题)?[ \t]*[0-9]{1,3}】|（[0-9]{1,3}）|\([0-9]{1,3}\)|'
    r'【变式(?:训练)?[0-9]{0,3}(?:·变[^\r\n【】（）()]{1,12})?】)')
_RESPONSES = frozenset((
    '单选', '双选', '多选', '不定项选择', '单项选择', '双项选择', '多项选择',
    '单项选择题', '双项选择题', '多项选择题', '选择题',
))
_EXAMS = frozenset((
    '一模', '二模', '三模', '等级考', '等级考试', '高考', '高考真题',
    '高考模拟', '模拟考试', '模拟练习', '模拟题', '阶段练习', '阶段测试',
    '阶段考试', '期中', '期末', '期中考试', '期末考试', '月考', '统考', '联考', '校考',
))
_YEAR = re.compile(
    r'(?P<year>(?:19|20)[0-9]{2}(?:-(?:19|20)[0-9]{2})?|[0-9]{2}-[0-9]{2})'
    r'(?:学年|年|届)?(?:高[一二三](?:上学期|下学期|上|下)?)?')
_PLACES = frozenset(_REGIONS)


def _parenthesis(text, start):
    if start >= len(text) or text[start] not in '（(':
        return None
    close = '）' if text[start] == '（' else ')'
    end = text.find(close, start + 1, start + 98)
    if end < 0:
        return None
    inside = text[start + 1:end]
    if any(c in inside for c in '（）()【】[]\r\n'):
        return None
    return inside, end + 1


def _source_payload(value):
    # Whole fields only: a year, zero to two known place literals and an exact
    # exam/worksheet label. A year/keyword somewhere in prose is insufficient.
    # Unknown schools/places and undelimited forms remain available for editing.
    fields = [part.strip(' \t\u3000') for part in value.split('·')]
    if not 2 <= len(fields) <= 4 or fields[-1] not in _EXAMS:
        return False
    match = _YEAR.fullmatch(fields[0])
    if match is None or any(place not in _PLACES for place in fields[1:-1]):
        return False
    years = match['year'].split('-')
    if len(years) == 2:
        left, right = map(int, years)
        if right != (left + 1) % (100 if len(years[0]) == 2 else 10000):
            return False
    return True


def printed_source_span(text):
    """Return one exact leading citation span; do not infer source metadata."""
    position = _SPACE.match(text).end()
    lead = _LEAD.match(text, position)
    if lead is not None:
        position = _SPACE.match(text, lead.end()).end()
    instruction = _parenthesis(text, position)
    if instruction is not None and instruction[0] in _RESPONSES:
        position = _SPACE.match(text, instruction[1]).end()
    candidate = _parenthesis(text, position)
    if candidate is None or not _source_payload(candidate[0]):
        return None
    end = candidate[1]
    if not text[end:].strip():
        return None  # A separate attribution paragraph is not a stem prefix.
    following = _parenthesis(text, _SPACE.match(text, end).end())
    if following is not None and _source_payload(following[0]):
        return None  # Two adjacent attributions are not one unambiguous prefix.
    return position, end


def _deletion_preserves_display(row, by_id, start, end):
    """Prove renderer output against arithmetic offsets, not fuzzy inference."""
    source = row['value']
    value = source[:start] + source[end:]
    presentation = row.get('presentation', {})
    if 'scene' in presentation:
        return False
    protected = [(m.start(), m.end()) for m in MARKER.finditer(source)]
    protected.extend((s['start'], s['end']) for name in ('scripts', 'eqs')
                     for s in presentation.get(name, []))
    if any(left < end and start < right for left, right in protected):
        return False
    changed = deepcopy(row)
    changed['value'] = value
    # This comparison-only block is never saved or used as a new Original.
    # Equal source/work text bypasses the renderer's matching, so it expresses
    # the independent expected projection of the precise deletion interval.
    projection = deepcopy(changed)
    matches = (SequenceMatcher(None, source, value, autojunk=False).get_matching_blocks()
               if max(len(source), len(value)) <= 20000 else [])
    for name in ('scripts', 'eqs'):
        spans = presentation.get(name, [])
        expected = []
        for span in spans:
            shift = end - start if span['start'] >= end else 0
            expected.append({**deepcopy(span), 'start': span['start'] - shift,
                             'end': span['end'] - shift})
        actual = [{**deepcopy(span), 'start': span['start'] + m.b - m.a,
                   'end': span['end'] + m.b - m.a}
                  for span in spans for m in matches
                  if m.a <= span['start'] and span['end'] <= m.a + m.size]
        if actual != expected:
            return False
        if name in presentation:
            projection['presentation'][name] = expected
    return (display_tokens(changed, by_id, source)
            == display_tokens(projection, by_id, value))


def _working_title(item):
    """A printed number can navigate a new card; its stem is never a title.

    This names no chemical concept or question hierarchy. A teacher-authored
    work title is retained by the callers' fresh-card checks.
    """
    for row in item['blocks']:
        if row['kind'] != 'text' or row['role'] != 'question' or not row['value'].strip():
            continue
        text = row['value'].lstrip(' \t\u3000')
        lead = _LEAD.match(text)
        if lead is not None and not lead.group().startswith('【变式'):
            number = re.search(r'[0-9]{1,3}', lead.group())
            if number is not None:
                return '例题' + number.group()
        break
    return '例题'


def classroom_question_copy(item):
    """Apply a deterministic default only to a new, otherwise unedited card.

    Deduplication still uses the sealed question evidence. Existing teacher
    work, including an intentional restoration of source wording, is untouched.
    """
    value = validate_resource(item)
    original = value['original']
    if (original['kind'] != 'complete_question' or value['blocks'] != original['blocks']
            or value['title'] != original['title']):
        return value
    row = next((row for row in value['blocks']
                if row['kind'] == 'text' and row['role'] == 'question'), None)
    if row is None:
        return value
    span = printed_source_span(row['value'])
    if span is None:
        return value
    by_id = {block['id']: block for block in value['blocks']}
    try:
        if not _deletion_preserves_display(row, by_id, *span):
            return value
    except (ValueError, TypeError, KeyError, IndexError):
        return value
    start, end = span
    row['value'] = row['value'][:start] + row['value'][end:]
    value['title'] = _working_title(value)
    return validate_resource(value)
