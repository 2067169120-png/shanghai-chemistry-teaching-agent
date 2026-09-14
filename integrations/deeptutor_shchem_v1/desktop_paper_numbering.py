"""Presentation-only numbering for a newly composed paper.

Source identities and source bytes never change. Text replacements are limited
 to explicit leading labels and references of the form 第N题. Raster pixels are
not guessed: those need a separately verified number region.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

_NUMBER = r"[0-9０-９一二三四五六七八九十百]{1,8}(?:[-－—][0-9０-９]{1,3})?"
_KIND = r"(?:即学即练|同步练习|随堂练习|针对训练|典例|例题|例|变式(?:训练|练习)?|练习|题)"
LABEL = re.compile(rf"^\s*(?:第\s*(?P<han>[0-9０-９]{{1,3}})\s*题\s*[:：、.．]?|[【\[]\s*{_KIND}\s*(?P<example>{_NUMBER})\s*[】\]]|{_KIND}\s*(?P<bare>{_NUMBER})(?![0-9０-９一二三四五六七八九十百\-－—])\s*[:：、.．]?|(?P<arabic>[0-9０-９]{{1,3}})\s*(?:[．、]|\.(?![0-9０-９]))|(?P<score>[0-9０-９]{{1,3}})\s+(?=[（(][0-9０-９]+(?:\.[0-9]+)?\s*分))\s*")
ANSWER_INTRO = re.compile(r"^\s*(?:【(?:参考答案|答案|解析|解答)】|(?:参考答案|答案|解析|解答)\s*[:：])\s*")
REFERENCE = re.compile(r"第\s*([0-9０-９]{1,3})\s*题")
RASTER_NOTICE = "图片里的原题号尚未改写，请按图片上方的本卷题号作答；图片内引用其他题号时仍须对照原题核对。"


def leading_label(text):
    match = LABEL.match(text)
    if not match:
        return None
    import unicodedata
    raw = unicodedata.normalize('NFKC', next(v for v in match.groupdict().values() if v is not None)).replace('—', '-').replace('－', '-')
    value = str(int(raw)) if raw.isdigit() else raw
    return (match.end(), value) if value != '0' else None


def renumber_text(text, new_number, *, original_number=None):
    """Replace a supported leading label, not numeric chemistry content."""
    label = leading_label(text)
    if label and (original_number is None or label[1] == str(original_number)):
        return f"{new_number}. " + text[label[0]:]
    return text


def rewrite_references(text, mapping):
    return REFERENCE.sub(lambda m: '第' + str(mapping.get(str(int(m[1])), m[1])) + '题', text)


def _segments(paragraph):
    """Ordinary text nodes plus one boundary token per non-text object.

    Keeping those positions lets explicit references after an OMML formula be
    renumbered without joining text across the formula or editing its nodes.
    """
    wrappers = {qn('w:hyperlink'), qn('w:ins'), qn('w:smartTag'), qn('w:sdt'), qn('w:sdtContent')}
    invisible = {qn('w:pPr'), qn('w:rPr'), qn('w:sdtPr'), qn('w:bookmarkStart'), qn('w:bookmarkEnd'), qn('w:proofErr')}
    for child in paragraph:
        if child.tag in invisible:
            continue
        if child.tag == qn('w:r') or child.tag in wrappers:
            yield from _segments(child)
        elif child.tag == qn('w:t'):
            yield child
        else:
            yield None


def _replace(paragraph, start, end, replacement):
    offset = 0
    inserted = False
    for node in _segments(paragraph):
        text = '\ufffc' if node is None else (node.text or '')
        lo, hi = offset, offset + len(text)
        if node is not None and lo < end and hi > start:
            a, b = max(0, start - lo), min(len(text), end - lo)
            node.text = text[:a] + (replacement if not inserted else '') + text[b:]
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            inserted = True
        offset = hi


def paragraph_text(paragraph):
    return ''.join('\ufffc' if n is None else (n.text or '') for n in _segments(paragraph))


def _paragraph(block):
    if block.tag == qn('w:p'):
        return block
    # A question can start in a table cell; never renumber every numeric cell.
    return next(block.iter(qn('w:p')), None)


@dataclass(frozen=True)
class WordNumbering:
    labels: dict[int, tuple[str | None, int]]
    mapping: dict[str, int]
    first: int
    count: int
    parent_index: int | None = None


def plan_word(item, start):
    labels = {}
    starts = item.nested_starts or item.question
    first_family = None
    previous_number = None
    nested_list = False
    for index in starts:
        paragraph = _paragraph(item.source.blocks[index - 1])
        if paragraph is None:
            continue
        value = paragraph_text(paragraph)
        label = leading_label(value)
        if not label:
            if item.nested_starts:
                # Known printed boundaries still receive a label when the
                # source uses automatic numbering instead of literal text.
                labels[index] = (None, start + len(labels))
            continue
        explicit = not (LABEL.match(value).group('arabic') or LABEL.match(value).group('score'))
        if first_family is None:
            first_family = explicit
        elif not item.nested_starts:
            # Numbered steps inside an example must not become independent
            # questions. A reset/repeat starts a local sub-list, not new IDs.
            if not explicit and (first_family or nested_list or int(label[1]) <= previous_number):
                nested_list = True
                continue
        labels[index] = (label[1], start + len(labels))
        previous_number = int(label[1]) if label[1].isdigit() else -1
    if not labels:
        labels[item.question[0]] = (None, start)
    mapping = {}
    for old, new in labels.values():
        if old is not None and sum(v[0] == old for v in labels.values()) == 1:
            mapping[old] = new
    parent = item.question[0] if item.nested_starts and item.question[0] not in item.nested_starts else None
    return WordNumbering(labels, mapping, start, len(labels), parent)


def apply_word_numbering(clone, index, plan, *, answer=False, references=None):
    """Edit only a fresh OOXML clone; preserve runs, tables and formula nodes."""
    mapping = plan.mapping if references is None else references
    first = _paragraph(clone)
    if first is None:
        return
    target = plan.labels.get(index) if not answer else None
    answer_offset = 0
    if answer:
        body = paragraph_text(first)
        intro = ANSWER_INTRO.match(body)
        answer_offset = intro.end() if intro else 0
        label = leading_label(body[answer_offset:])
        if label and label[1] in plan.mapping:
            target = (label[1], plan.mapping[label[1]])
    # Right-to-left avoids cascading. Object boundaries remain explicit, so
    # text on opposite sides of a formula can never form a synthetic match.
    for p in clone.iter(qn('w:p')):
        text = paragraph_text(p)
        for match in reversed(list(REFERENCE.finditer(text))):
            old = str(int(match[1]))
            if old in mapping and not (p is first and match.start() == 0 and target):
                _replace(p, match.start(), match.end(), f'第{mapping[old]}题')
    if not answer and index == plan.parent_index:
        label = leading_label(paragraph_text(first))
        if label:
            # The composer already supplies a new theme heading. Keep the
            # shared introduction, but not its unrelated original theme number.
            _replace(first, 0, label[0], '')
        first.get_or_add_pPr().get_or_add_numPr().get_or_add_numId().val = 0
    if target:
        label = leading_label(paragraph_text(first)[answer_offset:])
        if label:
            _replace(first, answer_offset, answer_offset + label[0], f'{target[1]}. ')
        else:
            run, node = OxmlElement('w:r'), OxmlElement('w:t')
            node.text = f'{target[1]}. '
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            run.append(node)
            first.insert(1 if first.pPr is not None else 0, run)
        # Override original automatic list numbering, including style-inherited
        # numPr. Only the question-label paragraph is affected, not subquestions.
        props = first.get_or_add_pPr()
        num = props.get_or_add_numPr()
        num.get_or_add_numId().val = 0


def core_sequence(visible, blueprint, start):
    """Mutate rendering copies only. Number printed questions, not answer units."""
    cursor, mapping = start, {}
    bthemes = (blueprint or {}).get('theme_bundles', [])
    for t_index, theme in enumerate(visible['theme_sections']):
        btheme = bthemes[t_index] if t_index < len(bthemes) else {}
        source_rows = btheme.get('printed_questions', [])
        for q_index, question in enumerate(theme['printed_questions']):
            source = source_rows[q_index] if q_index < len(source_rows) else {}
            old = source.get('source_number', question['question_number'])
            if str(old) not in mapping:
                mapping[str(old)] = cursor
            question['question_number'] = cursor
            cursor += 1
    return cursor, mapping


def word_reference_maps(items, plans):
    """Only rewrite uniquely resolved references within the same Word source."""
    groups = {}
    for item, plan in zip(items, plans, strict=True):
        table = groups.setdefault(item.source.digest, {})
        for old, number in plan.labels.values():
            if old is not None:
                table.setdefault(old, set()).add(number)
    return {digest: {old: next(iter(numbers)) for old, numbers in rows.items()
                     if len(numbers) == 1} for digest, rows in groups.items()}


def rewrite_core_text(visible, blueprint, start):
    """Number a rendering copy; resolve text references within a known source.

    Different original papers can both contain a question 27. Never redirect a
    reference in one source to the other's question just because its number is
    unique in the current selection. Missing source identity stays theme-local.
    """
    bthemes = (blueprint or {}).get('theme_bundles', [])
    groups, originals, keys = {}, [], []
    cursor = start
    for t_index, theme in enumerate(visible['theme_sections']):
        btheme = bthemes[t_index] if t_index < len(bthemes) else {}
        source = btheme.get('source', {})
        key = (source.get('scope', ''), source['paper_id']) if source.get('paper_id') else ('theme', t_index)
        keys.append(key)
        sources = btheme.get('printed_questions', [])
        old_numbers = []
        for q_index, question in enumerate(theme['printed_questions']):
            old = str(sources[q_index].get('source_number', question['question_number'])) if q_index < len(sources) else str(question['question_number'])
            old_numbers.append(old)
            groups.setdefault(key, {}).setdefault(old, set()).add(cursor)
            question['question_number'] = cursor
            cursor += 1
        originals.append(old_numbers)
    maps = {key: {old: next(iter(numbers)) for old, numbers in values.items() if len(numbers) == 1}
            for key, values in groups.items()}
    for t_index, theme in enumerate(visible['theme_sections']):
        mapping = maps[keys[t_index]]
        def visit(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in {'text_zh', 'answer_zh', 'explanation_zh'} and isinstance(value, str):
                        node[key] = rewrite_references(value, mapping)
                    elif isinstance(value, (dict, list)):
                        visit(value)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(theme)
        for q_index, question in enumerate(theme['printed_questions']):
            old = originals[t_index][q_index]
            allowed = {old, str(question['question_number'])}
            blocks = question['atomic_parts'][0]['question_blocks']
            if blocks and isinstance(blocks[0].get('text_zh'), str):
                text = blocks[0]['text_zh']
                label = leading_label(text)
                if label and label[1] in allowed:
                    blocks[0]['text_zh'] = text[label[0]:]
            # A separate scoring label already supplies the current number.
            # Remove a matching old answer label, not a numerical answer value.
            for atomic in question['atomic_parts']:
                answer = atomic.get('teacher_notes', {}).get('source_reference_answer', {})
                text = answer.get('text_zh')
                if isinstance(text, str):
                    intro = ANSWER_INTRO.match(text)
                    offset = intro.end() if intro else 0
                    label = leading_label(text[offset:])
                    if label and label[1] == old:
                        answer['text_zh'] = text[:offset] + text[offset + label[0]:]
    return cursor, maps
