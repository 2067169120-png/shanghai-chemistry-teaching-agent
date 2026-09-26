"""Pagination preferences on answer clones, not edits to source material.

Bind a short opening label and up to three empty spacer paragraphs to the first
answer block. Do not keep the complete answer or its table on one page.
"""
from docx.oxml.ns import qn


def _has_break(block):
    if any(n.get(qn('w:type')) == 'page' for n in block.iter(qn('w:br'))):
        return True
    return any(n.get(qn('w:val'), '1') not in {'0', 'false', 'off'}
               for n in block.iter(qn('w:pageBreakBefore')))


def _short_text(block):
    if block.tag != qn('w:p') or _has_break(block):
        return None
    # Formula/graphic content is a real answer block, not an empty spacer.
    if any(list(block.iter(qn(tag))) for tag in ('w:drawing', 'w:pict', 'w:object', 'm:oMath', 'm:oMathPara')):
        return None
    text = ''.join(n.text or '' for n in block.iter(qn('w:t'))).strip()
    return text if len(text) <= 160 else None


def keep_answer_opening(blocks):
    """Keep generated header -> short source label -> first body block.

    Blank lines are allowed but explicit source page breaks are respected. The
    first picture/table/long paragraph is not glued to the following content.
    """
    pending, seen_text, blanks = [], False, 0
    for block in blocks:
        text = _short_text(block)
        if text is None:
            break
        if text:
            if seen_text:
                break
            seen_text = True
        else:
            blanks += 1
            if blanks > 3:
                break
        pending.append(block)
    # No following block means there is nothing to bind to. Never join the
    # terminal paragraph of an answer to the next question or its source note.
    for index, block in enumerate(pending):
        if index + 1 >= len(blocks) or _has_break(blocks[index + 1]):
            break
        ppr = block.get_or_add_pPr()
        ppr.get_or_add_keepNext().val = True
        ppr.get_or_add_keepLines().val = True
