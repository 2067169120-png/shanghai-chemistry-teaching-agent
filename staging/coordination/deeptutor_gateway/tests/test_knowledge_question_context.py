"""Card-to-question context keeps the original source identity and stays read-only."""
from copy import deepcopy

import pytest

from test_note_page_crops import notes, section_fixture, asset_fixture, shelf_fixture
from test_textbook_concept_cards import concept, supplement, supplement_fixture
from test_personal_textbook_knowledge import native_fixture
from test_personal_textbook_study import chapter, note as personal_note
from test_composition_inline_personal import personal_facade
from test_textbook_bookshelf import disk_bytes
from test_textbook_reading_hints import write_packet

from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_knowledge_note, knowledge_options
from integrations.deeptutor_shchem_v1.desktop_knowledge_questions import (
    knowledge_question_context, recheck_knowledge_question_context)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest
from integrations.deeptutor_shchem_v1.desktop_textbook_question_context import (
    TextbookQuestionContextService, question_filter_context)
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, read_cancel_scope)


def test_registered_note_uses_directory_revision_and_original_title_read_only(notes):
    f = notes
    before = disk_bytes(f.root)
    original = deepcopy(f.item['original'])
    f.item['title'] = '教师修改的课程标题'
    f.item['blocks'][0]['value'] = '教师补充的工作副本内容'
    value = knowledge_question_context(f.facade, f.item)
    assert value['original_title'] == original['title']
    assert value['search_query'] == ''
    context = value['curriculum_context']
    assert context['validation_request'] == {'mode': 'registered', 'section_key': f.current['section_key'],
                                             'revision': f.current['directory_revision']}
    assert context['source_sha256'] == original['evidence']['source_sha256']
    assert question_filter_context(context, f.registry)['selection']['book'] == [f.row['volume_id']]
    assert recheck_knowledge_question_context(f.facade, f.item, value) is True
    assert f.item['original'] == original and disk_bytes(f.root) == before
    assert not f.task_root.exists()


@pytest.mark.parametrize('change', ['note', 'pdf', 'directory', 'pdf_pages', 'printed_pages'])
def test_registered_source_changes_invalidate_browser_context(notes, change):
    f = notes
    value = knowledge_question_context(f.facade, f.item)
    if change == 'note':
        f.note['summary'] += '变更'
        write_packet(f.root, [f.note])
    elif change == 'pdf':
        f.book.write_bytes(f.book.read_bytes() + b'changed')
    elif change == 'directory':
        f.registry['volumes'][0]['volume_title'] += '变更'
        f.activate()
    elif change == 'pdf_pages':
        f.item['original']['evidence']['pdf_pages'] = [8]
        f.item['original_sha256'] = digest(f.item['original'])
    else:
        f.item['original']['evidence']['printed_pages'] = [20, 21]
        f.item['original_sha256'] = digest(f.item['original'])
    with pytest.raises(ValueError):
        recheck_knowledge_question_context(f.facade, f.item, value)
    assert not f.task_root.exists()


@pytest.mark.parametrize('change', ['title', 'body', 'digest'])
def test_tampered_sealed_note_is_rejected_even_with_recomputed_digest(notes, change):
    f = notes
    if change == 'title':
        f.item['original']['title'] = '伪造原卡标题'
    elif change == 'body':
        f.item['original']['blocks'][0]['value'] += '伪造原内容'
    else:
        f.item['original_sha256'] = 'f' * 64
    if change != 'digest':
        f.item['original_sha256'] = digest(f.item['original'])
    with pytest.raises(ValueError):
        knowledge_question_context(f.facade, f.item)
    assert not f.task_root.exists()


@pytest.mark.parametrize('field', ['original_title', 'search_query', 'binding_sha256', 'extra'])
def test_entire_flow_context_must_match_before_question_handoff(notes, field):
    value = knowledge_question_context(notes.facade, notes.item)
    value[field] = 'changed'
    with pytest.raises(ValueError):
        recheck_knowledge_question_context(notes.facade, notes.item, value)


def test_same_source_card_cannot_replace_selected_card_identity(notes):
    value = knowledge_question_context(notes.facade, notes.item)
    notes.item['id'] = 'CR-' + 'c' * 32
    with pytest.raises(ValueError):
        recheck_knowledge_question_context(notes.facade, notes.item, value)


def test_duplicate_registered_note_identity_does_not_choose_first(notes, monkeypatch):
    snapshot = notes.facade.textbook_bookshelf()
    snapshot['reading_notes'].append(deepcopy(snapshot['reading_notes'][0]))
    monkeypatch.setattr(notes.facade, 'textbook_bookshelf', lambda: deepcopy(snapshot))
    with pytest.raises(ValueError, match='重复'):
        knowledge_question_context(notes.facade, notes.item)
    assert not notes.task_root.exists()


def test_failed_context_validation_does_not_return_browser_context(notes, monkeypatch):
    monkeypatch.setattr(notes.facade, 'validate_textbook_question_context', lambda context: False)
    with pytest.raises(ValueError, match='来源核对'):
        knowledge_question_context(notes.facade, notes.item)
    assert not notes.task_root.exists()


def test_registered_note_change_during_context_read_returns_no_context(notes, monkeypatch):
    f = notes
    real = f.facade.validate_textbook_question_context
    def changed(context):
        result = real(context)
        f.note['summary'] += '核对期间变更'
        write_packet(f.root, [f.note])
        return result
    monkeypatch.setattr(f.facade, 'validate_textbook_question_context', changed)
    with pytest.raises(ValueError):
        knowledge_question_context(f.facade, f.item)
    assert not f.task_root.exists()


def test_registered_concept_uses_declared_section_without_inventing_knowledge_id(concept):
    f = concept
    card = import_knowledge_note(f.facade, f.option['selection'])
    before = disk_bytes(f.root)
    value = knowledge_question_context(f.facade, card)
    assert value['curriculum_context']['section_key'] == f.option['section_key']
    assert value['curriculum_context']['validation_request']['revision'] == f.option['selection']['directory_revision']
    assert value['original_title'] == card['original']['title'] and value['search_query'] == ''
    assert not any('knowledge' in key or 'concept_id' == key for key in value['curriculum_context'])
    assert recheck_knowledge_question_context(f.facade, card, value) is True
    assert disk_bytes(f.root) == before and not f.facade.paths.task_root.exists()


def test_supplement_concept_keeps_literal_title_search_and_no_curriculum_ids(supplement):
    f = supplement
    card = import_knowledge_note(f.facade, f.option['selection'])
    card['title'] = '修改后的工作副本'
    before = disk_bytes(f.root)
    value = knowledge_question_context(f.facade, card)
    assert value['curriculum_context'] is None
    assert value['search_query'] == value['original_title'] == card['original']['title']
    assert not any(key in value for key in ('volume_id', 'chapter_id', 'section_key', 'knowledge_id'))
    assert recheck_knowledge_question_context(f.facade, card, value) is True
    assert disk_bytes(f.root) == before and not f.facade.paths.task_root.exists()


def test_unassigned_concept_uses_title_search_and_stale_record_is_rejected(concept):
    import json
    from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import concept_options
    f = concept
    f.row['section_key'] = None
    f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    option = concept_options(f.root)[0]
    card = import_knowledge_note(f.facade, option['selection'])
    value = knowledge_question_context(f.facade, card)
    assert value['curriculum_context'] is None and value['search_query'] == f.row['title']
    f.row['statement'] += '变更'
    f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    with pytest.raises(ValueError):
        recheck_knowledge_question_context(f.facade, card, value)
    assert not f.facade.paths.task_root.exists()


def _personal(f):
    view = personal_note(f, chapter(f, title='2.2 个人保存章节'), title='原笔记标题')
    facade = personal_facade(f)
    service = TextbookQuestionContextService(f.paths.workspace_root, f.study)
    facade.textbook_question_context = service.resolve
    facade.validate_textbook_question_context = service.validate
    card = import_knowledge_note(facade, knowledge_options(facade)['reading_notes'][0]['selection'])
    return view, facade, card


def test_personal_card_uses_current_saved_section_and_unknown_printed_pages_read_only(native_fixture):
    f = native_fixture
    view, facade, card = _personal(f)
    before = f.state.snapshot()
    value = knowledge_question_context(facade, card)
    context = value['curriculum_context']
    assert context['validation_request'] == {'mode': 'personal', 'selection': f.selection,
        'section_id': view['notes'][0]['section_id'], 'study_revision': view['study_revision']}
    assert context['mode'] == 'text_search' and context['search_query'] == '个人保存章节'
    assert value['original_title'] == '原笔记标题' and value['search_query'] == ''
    assert recheck_knowledge_question_context(facade, card, value) is True
    assert f.state.snapshot() == before and f.source.read_bytes() == f.original


@pytest.mark.parametrize('change', ['note', 'archived_pdf', 'printed_pages'])
def test_personal_source_or_printed_page_guess_invalidates_context(native_fixture, change):
    f = native_fixture
    view, facade, card = _personal(f)
    value = knowledge_question_context(facade, card)
    if change == 'note':
        f.study.save_note(f.selection, view['study_revision'], section_id=view['notes'][0]['section_id'],
            note_id=view['notes'][0]['note_id'], title=view['notes'][0]['title'], text='变更笔记',
            pdf_page=2, teacher_confirmed=True)
    elif change == 'archived_pdf':
        (f.workspace.root / (f.selection['revision'] + '.pdf')).write_bytes(b'changed')
    else:
        card['original']['evidence']['printed_pages'] = [2]
        card['original_sha256'] = digest(card['original'])
    with pytest.raises(ValueError):
        recheck_knowledge_question_context(facade, card, value)
    assert f.source.read_bytes() == f.original


def test_cancelled_context_resolution_is_read_only(notes):
    before = disk_bytes(notes.root)
    event = TaskCancellationEvent()
    event.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        knowledge_question_context(notes.facade, notes.item)
    assert disk_bytes(notes.root) == before and not notes.task_root.exists()
