"""Read-only, source-bound question browsing from a sealed knowledge card."""

from copy import deepcopy

from .desktop_composition_sources import import_knowledge_note, knowledge_textbook_options
from .desktop_lesson_composition import digest, validate_resource
from .reader_cancellation import check_read_cancelled


def _unique(rows, message):
    if len(rows) != 1:
        raise ValueError(message)
    return rows[0]


def _source_content(original):
    """Compare authored contents; new imports create fresh local block IDs."""
    value = deepcopy(original)
    value['evidence'].pop('source_path', None)
    for row in value['blocks']:
        row.pop('id', None)
    return value


def _selection_and_request(facade, card):
    evidence = card['original']['evidence']
    if 'concept_source' in evidence:
        source = evidence['concept_source']
        selection = {'kind': 'textbook_concept', 'concept_id': evidence['record_id'],
                     'revision': evidence['source_revision'],
                     'directory_revision': source['directory_revision'],
                     'location': deepcopy(source['location'])}
        # A concept identity is never a K01--K19 teaching label. Only its
        # current declared section can establish curriculum filter identities.
        section_key = source['location']['section_key']
        request = ({'mode': 'registered', 'section_key': section_key,
                    'revision': source['directory_revision']} if section_key is not None else None)
        return selection, request

    snapshot = facade.textbook_bookshelf()
    notes = [row for row in snapshot['reading_notes'] if row['note_id'] == evidence['record_id']]
    if notes:
        note = _unique(notes, '知识卡身份重复，请刷新后重新选择。')
        selection = {key: note[key] for key in ('note_id', 'section_key', 'revision', 'directory_revision')}
        return selection, {'mode': 'registered', 'section_key': note['section_key'],
                           'revision': note['directory_revision']}

    study = facade.personal_textbook_study()
    book = _unique([row for row in study.books() if row['source_sha256'] == evidence['source_sha256']],
                   '知识卡对应的个人教材已缺失或身份重复，请重新加入知识卡。')
    book_selection = {key: book[key] for key in ('source_id', 'revision')}
    view = study.book_study(book_selection)
    note = _unique([row for row in view['notes'] if row['note_id'] == evidence['record_id']
                    and row['revision'] == evidence['source_revision']],
                   '个人教材知识卡已变化，请重新加入知识卡。')
    if evidence['printed_pages'] != 'unknown':
        raise ValueError('个人教材知识卡不能补猜印刷页码，请重新加入知识卡。')
    selection = {'kind': 'personal', 'book': book_selection, 'study_revision': view['study_revision'],
                 'note': {key: note[key] for key in ('note_id', 'revision')}}
    return selection, {'mode': 'personal', 'selection': book_selection, 'section_id': note['section_id'],
                       'study_revision': view['study_revision']}


def _knowledge_question_context(facade, item):
    """Resolve current chapter filters or explicit original-title text search.

    The returned binding is transient browser-flow evidence. This function
    changes no question attributes, selected questions, source files or review
    flags, and does not add knowledge-selection fields to complete questions.
    Working-copy titles and text do not change the sealed source identity.
    """
    check_read_cancelled()
    card = deepcopy(validate_resource(item))
    if card['original']['kind'] != 'knowledge_note':
        raise ValueError('请先选择一张知识卡。')
    options = knowledge_textbook_options(facade, card)
    selection, request = _selection_and_request(facade, card)
    current_card = import_knowledge_note(facade, selection)
    if _source_content(current_card['original']) != _source_content(card['original']):
        raise ValueError('知识卡原内容与当前来源不一致，请重新加入知识卡后选例题。')

    context = facade.textbook_question_context(request) if request is not None else None
    if context is not None:
        if facade.validate_textbook_question_context(context) is not True:
            raise ValueError('教材选题范围尚未完成来源核对，请刷新后重选。')
        if context['source_sha256'] != card['original']['evidence']['source_sha256']:
            raise ValueError('知识卡与教材章节的原书版本不一致，请刷新后重选。')
    title = card['original']['title']
    result = {'original_title': title, 'curriculum_context': deepcopy(context),
              'search_query': title if context is None else '',
              'binding_sha256': digest({'resource_id': card['id'],
                                        'original_sha256': card['original_sha256'],
                                        'knowledge_binding': options['binding']})}

    # Source reads and context validation can take time. Recheck the sealed
    # input, source selection, original contents and knowledge binding before
    # returning any usable browser context.
    check_read_cancelled()
    latest = validate_resource(item)
    if latest['id'] != card['id'] or latest['original'] != card['original']:
        raise ValueError('知识卡原内容在核对期间已变化，请重新选例题。')
    if (knowledge_textbook_options(facade, latest)['binding'] != options['binding']
            or _selection_and_request(facade, latest) != (selection, request)
            or _source_content(import_knowledge_note(facade, selection)['original'])
            != _source_content(card['original'])):
        raise ValueError('知识卡或教材章节在核对期间已变化，请重新选例题。')
    return result


def knowledge_question_context(facade, item):
    """Return a checked, read-only card/title/chapter browser-flow binding."""
    try:
        return _knowledge_question_context(facade, item)
    except (KeyError, TypeError, StopIteration, OSError) as exc:
        # A facade can expose an unavailable-catalog result after a directory
        # change. Never turn missing metadata into a permissive all-book search.
        raise ValueError('知识卡来源或教材目录无法核对，请刷新后重新选例题。') from exc


def recheck_knowledge_question_context(facade, item, context):
    """Fail closed after browsing if any original card/source context changed."""
    if not isinstance(context, dict) or knowledge_question_context(facade, item) != context:
        raise ValueError('知识卡或教材选题范围已变化，请重新从知识卡选例题。')
    return True
