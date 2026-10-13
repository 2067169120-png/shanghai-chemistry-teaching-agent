"""Open current textbook pages from a sealed classroom knowledge card."""

from copy import deepcopy

from .desktop_composition_sources import (
    import_knowledge_note, knowledge_options, knowledge_reader_selection,
    knowledge_selection, knowledge_textbook_options)
from .desktop_knowledge_questions import _selection_and_request, _source_content
from .desktop_lesson_composition import digest, validate_resource
from .reader_cancellation import check_read_cancelled


def _current_reader(facade, selection):
    """Use the existing typed source selection, never a classroom title."""
    options = knowledge_options(facade)
    rows = [row for row in options['reading_notes'] + options['concepts']
            if knowledge_selection(row) == selection]
    if len(rows) != 1:
        raise ValueError('知识卡来源已缺失、变化或身份重复，请重新加入知识卡。')
    return knowledge_reader_selection(facade, rows[0])


def _knowledge_card_reader(facade, item):
    check_read_cancelled()
    card = deepcopy(validate_resource(item))
    if card['original']['kind'] != 'knowledge_note':
        raise ValueError('请先选择一张知识卡。')

    # These helpers validate the sealed evidence and rebuild the actual note.
    # The unused request is metadata only; no question context or catalog is read.
    options = knowledge_textbook_options(facade, card)
    selection, _request = _selection_and_request(facade, card)
    current = import_knowledge_note(facade, selection)
    if _source_content(current['original']) != _source_content(card['original']):
        raise ValueError('知识卡原内容与当前来源不一致，请重新加入知识卡后阅读。')
    reader = _current_reader(facade, selection)

    evidence = card['original']['evidence']
    result = {**deepcopy(reader), 'original_title': card['original']['title'],
              'pdf_pages': deepcopy(evidence['pdf_pages']),
              'printed_pages': deepcopy(evidence['printed_pages']),
              'source_sha256': evidence['source_sha256'],
              'source_revision': evidence['source_revision'],
              'source_text': '\n\n'.join(row['value'] for row in card['original']['blocks']
                                         if row['kind'] == 'text'),
              'binding_sha256': digest({'resource_id': card['id'],
                                        'original_sha256': card['original_sha256'],
                                        'knowledge_binding': options['binding'],
                                        'reader': reader})}

    # Original PDF verification can be slow. Check the card and the current
    # metadata/contents again before any reader selection is handed to the UI.
    check_read_cancelled()
    latest = validate_resource(item)
    if latest['id'] != card['id'] or latest['original'] != card['original']:
        raise ValueError('知识卡原内容在读取期间已变化，请重新选择。')
    if (knowledge_textbook_options(facade, latest)['binding'] != options['binding']
            or _selection_and_request(facade, latest)[0] != selection
            or _source_content(import_knowledge_note(facade, selection)['original'])
            != _source_content(card['original'])
            or _current_reader(facade, selection) != reader):
        raise ValueError('知识卡或教材原页在读取期间已变化，请重新选择。')
    check_read_cancelled()
    latest = validate_resource(item)
    if latest['id'] != card['id'] or latest['original'] != card['original']:
        raise ValueError('知识卡原内容在读取期间已变化，请重新选择。')
    return result


def knowledge_card_reader(facade, item):
    """Return a source-checked read-only TextbookSourceDialog selection.

    ``reading_mode`` and ``selection`` are the existing viewer arguments.
    ``pdf_pages`` describes the card's literal cited pages; section/book readers
    may expose a wider declared scope and start at the card's first page.
    ``source_text`` retains the sealed text, including teacher notes. No work
    copy, source, selection basket or review/publication qualification is saved.
    """
    try:
        return _knowledge_card_reader(facade, item)
    except (KeyError, TypeError, StopIteration, OSError) as exc:
        raise ValueError('知识卡来源或教材原页无法核对，请刷新后重新选择。') from exc


def recheck_knowledge_card_reader(facade, item, result):
    """Re-resolve the original card/source before continuing from its reader."""
    if not isinstance(result, dict) or knowledge_card_reader(facade, item) != result:
        raise ValueError('知识卡或教材阅读范围已变化，请重新从知识卡打开原页。')
    return True
