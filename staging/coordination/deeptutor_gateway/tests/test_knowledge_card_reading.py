"""Sealed card readers retain actual note identity, pages and read-only state."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_note_page_crops import notes as registered_notes, section_fixture, asset_fixture, shelf_fixture
from test_textbook_concept_cards import concept, supplement, supplement_fixture
from test_personal_textbook_knowledge import native_fixture
from test_personal_textbook_study import chapter, note as personal_note
from test_composition_inline_personal import personal_facade
from test_textbook_bookshelf import disk_bytes
from test_textbook_reading_hints import write_packet

from integrations.deeptutor_shchem_v1.desktop_composition_sources import (
    import_knowledge_note, knowledge_options)
from integrations.deeptutor_shchem_v1 import desktop_knowledge_reading as reading
from integrations.deeptutor_shchem_v1.desktop_knowledge_reading import (
    knowledge_card_reader, recheck_knowledge_card_reader)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest, resource, block
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, read_cancel_scope)


@pytest.fixture
def notes(registered_notes):
    # This existing registered-only facade is allocated via __new__; its real
    # personal state is intentionally absent from the synthetic fixture.
    registered_notes.facade.personal_textbook_study = lambda: SimpleNamespace(books=lambda: [])
    return registered_notes


def test_registered_reader_uses_current_directory_revision_and_literal_first_page(notes, monkeypatch):
    f = notes
    before = disk_bytes(f.root)
    original = deepcopy(f.item['original'])
    f.item['title'] = '教师修改的课程标题'
    f.item['blocks'][0]['value'] = '教师改写的工作副本'
    monkeypatch.setattr(f.facade, 'textbook_question_context',
                        lambda *_: pytest.fail('Reading cannot resolve question filters.'))
    monkeypatch.setattr(f.facade, '_word_questions',
                        lambda *_: pytest.fail('Reading cannot open the question database.'))
    result = knowledge_card_reader(f.facade, f.item)
    assert result['reading_mode'] == 'section'
    assert result['selection'] == {'section_key': f.current['section_key'],
        'revision': f.current['directory_revision'], 'initial_pdf_page': 8}
    assert result['pdf_pages'] == [8, 9] and result['printed_pages'] == [4, 5]
    assert result['source_sha256'] == original['evidence']['source_sha256']
    assert result['source_revision'] == original['evidence']['source_revision']
    assert result['original_title'] == original['title']
    assert result['source_text'] == '\n\n'.join(b['value'] for b in original['blocks'])
    assert '工作副本' not in result['source_text']
    assert recheck_knowledge_card_reader(f.facade, f.item, result) is True
    assert f.item['original'] == original and disk_bytes(f.root) == before
    assert not f.task_root.exists()


@pytest.mark.parametrize('change', ['note', 'deleted_note', 'pdf', 'directory', 'pdf_pages', 'printed_pages'])
def test_registered_change_or_deletion_invalidates_reader(notes, change):
    f = notes
    result = knowledge_card_reader(f.facade, f.item)
    if change == 'note':
        f.note['summary'] += '修改'
        write_packet(f.root, [f.note])
    elif change == 'deleted_note':
        write_packet(f.root, [])
    elif change == 'pdf':
        f.book.write_bytes(f.book.read_bytes() + b'changed')
    elif change == 'directory':
        f.registry['volumes'][0]['volume_title'] += '修改'
        f.activate()
    elif change == 'pdf_pages':
        f.item['original']['evidence']['pdf_pages'] = [8]
        f.item['original_sha256'] = digest(f.item['original'])
    else:
        f.item['original']['evidence']['printed_pages'] = [20, 21]
        f.item['original_sha256'] = digest(f.item['original'])
    with pytest.raises(ValueError):
        recheck_knowledge_card_reader(f.facade, f.item, result)
    assert not f.task_root.exists()


@pytest.mark.parametrize('change', ['title', 'body', 'digest'])
def test_tampered_original_is_rejected_even_with_recomputed_digest(notes, change):
    f = notes
    if change == 'title':
        f.item['original']['title'] = '伪造原卡标题'
    elif change == 'body':
        f.item['original']['blocks'][0]['value'] += '伪造'
    else:
        f.item['original_sha256'] = 'f' * 64
    if change != 'digest':
        f.item['original_sha256'] = digest(f.item['original'])
    with pytest.raises(ValueError):
        knowledge_card_reader(f.facade, f.item)
    assert not f.task_root.exists()


@pytest.mark.parametrize('field', ['reading_mode', 'selection', 'original_title', 'pdf_pages',
                                  'printed_pages', 'source_sha256', 'source_revision',
                                  'source_text', 'binding_sha256', 'extra'])
def test_changed_flow_result_is_rejected(notes, field):
    result = knowledge_card_reader(notes.facade, notes.item)
    result[field] = 'changed'
    with pytest.raises(ValueError):
        recheck_knowledge_card_reader(notes.facade, notes.item, result)


def test_same_source_cannot_replace_selected_resource_identity(notes):
    result = knowledge_card_reader(notes.facade, notes.item)
    notes.item['id'] = 'CR-' + 'c' * 32
    with pytest.raises(ValueError):
        recheck_knowledge_card_reader(notes.facade, notes.item, result)


def test_duplicate_typed_option_does_not_choose_first(notes, monkeypatch):
    real = reading.knowledge_options
    def duplicate(facade):
        options = real(facade)
        options['reading_notes'].append(deepcopy(options['reading_notes'][0]))
        return options
    monkeypatch.setattr(reading, 'knowledge_options', duplicate)
    with pytest.raises(ValueError, match='重复'):
        knowledge_card_reader(notes.facade, notes.item)
    assert not notes.task_root.exists()


def test_late_note_change_after_reader_selection_returns_no_result(notes, monkeypatch):
    f = notes
    real = reading.knowledge_reader_selection
    def changed(*args):
        result = real(*args)
        f.note['summary'] += '读取期间修改'
        write_packet(f.root, [f.note])
        return result
    monkeypatch.setattr(reading, 'knowledge_reader_selection', changed)
    with pytest.raises(ValueError):
        knowledge_card_reader(f.facade, f.item)
    assert not f.task_root.exists()


def test_sealed_card_change_during_final_reader_check_returns_no_result(notes, monkeypatch):
    f = notes
    real = reading.knowledge_reader_selection
    calls = 0
    def changed(*args):
        nonlocal calls
        result = real(*args)
        calls += 1
        if calls == 2:
            f.item['original']['title'] = '读取期间修改原标题'
            f.item['original_sha256'] = digest(f.item['original'])
        return result
    monkeypatch.setattr(reading, 'knowledge_reader_selection', changed)
    with pytest.raises(ValueError, match='读取期间'):
        knowledge_card_reader(f.facade, f.item)
    assert not f.task_root.exists()


def test_registered_concept_reader_retains_exact_noncontiguous_pages(concept):
    f = concept
    card = import_knowledge_note(f.facade, f.option['selection'])
    before = disk_bytes(f.root)
    result = knowledge_card_reader(f.facade, card)
    assert result['reading_mode'] == 'concept_card'
    assert result['selection'] == f.option['selection']
    assert result['pdf_pages'] == f.row['pdf_pages'] == [7, 8, 10]
    assert result['printed_pages'] == [3, 4, 6]
    assert result['source_text'].startswith(f.row['statement'])
    assert recheck_knowledge_card_reader(f.facade, card, result) is True
    assert disk_bytes(f.root) == before and not f.facade.paths.task_root.exists()


def test_supplement_concept_reader_does_not_invent_section_or_contiguous_pages(supplement):
    f = supplement
    card = import_knowledge_note(f.facade, f.option['selection'])
    result = knowledge_card_reader(f.facade, card)
    assert result['reading_mode'] == 'concept_card'
    assert result['selection']['location']['section_key'] is None
    assert result['selection']['location']['supplement_node_key'] == 'declared-project'
    assert result['pdf_pages'] == [43, 45]
    assert result['printed_pages'] == [38, 40]
    assert recheck_knowledge_card_reader(f.facade, card, result) is True


@pytest.mark.parametrize('change', ['record', 'deleted_record', 'directory', 'pdf'])
def test_concept_record_or_source_changes_invalidate_reader(concept, change):
    f = concept
    card = import_knowledge_note(f.facade, f.option['selection'])
    result = knowledge_card_reader(f.facade, card)
    if change == 'record':
        f.row['statement'] += '修改'
        f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    elif change == 'deleted_record':
        f.catalog.write_text('', encoding='utf8')
    elif change == 'directory':
        f.registry['volumes'][0]['volume_title'] += '修改'
        f.activate()
    else:
        f.book.write_bytes(f.book.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        recheck_knowledge_card_reader(f.facade, card, result)
    assert not f.facade.paths.task_root.exists()


def test_unassigned_concept_opens_literal_source_without_guessing_location(concept):
    from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import concept_options
    f = concept
    f.row['section_key'] = None
    f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    option = concept_options(f.root)[0]
    card = import_knowledge_note(f.facade, option['selection'])
    result = knowledge_card_reader(f.facade, card)
    assert result['reading_mode'] == 'concept_card'
    assert result['selection']['location']['section_key'] is None
    assert result['printed_pages'] == 'unknown'
    assert result['pdf_pages'] == f.row['pdf_pages']


def _personal(f):
    view = personal_note(f, chapter(f, title='2.2 个人保存章节'), title='原笔记标题')
    facade = personal_facade(f)
    card = import_knowledge_note(facade, knowledge_options(facade)['reading_notes'][0]['selection'])
    return view, facade, card


def test_personal_reader_retains_saved_section_and_unknown_printed_pages(native_fixture):
    f = native_fixture
    view, facade, card = _personal(f)
    before = f.state.snapshot()
    result = knowledge_card_reader(facade, card)
    assert result['reading_mode'] == 'book'
    assert result['selection'] == {**f.selection, 'section_id': view['notes'][0]['section_id'],
        'study_revision': view['study_revision'], 'initial_pdf_page': 2}
    assert result['pdf_pages'] == [2] and result['printed_pages'] == 'unknown'
    assert result['original_title'] == '原笔记标题'
    assert recheck_knowledge_card_reader(facade, card, result) is True
    assert f.state.snapshot() == before and f.source.read_bytes() == f.original


@pytest.mark.parametrize('change', ['note', 'archived_pdf', 'printed_pages'])
def test_personal_source_or_guessed_printed_page_invalidates_reader(native_fixture, change):
    f = native_fixture
    view, facade, card = _personal(f)
    result = knowledge_card_reader(facade, card)
    if change == 'note':
        f.study.save_note(f.selection, view['study_revision'], section_id=view['notes'][0]['section_id'],
            note_id=view['notes'][0]['note_id'], title=view['notes'][0]['title'], text='修改笔记',
            pdf_page=2, teacher_confirmed=True)
    elif change == 'archived_pdf':
        (f.workspace.root / (f.selection['revision'] + '.pdf')).write_bytes(b'changed')
    else:
        card['original']['evidence']['printed_pages'] = [2]
        card['original_sha256'] = digest(card['original'])
    with pytest.raises(ValueError):
        recheck_knowledge_card_reader(facade, card, result)
    assert f.source.read_bytes() == f.original


def test_other_resource_kind_does_not_open_knowledge_reader(notes):
    card = resource('personal_text', '个人文字', [block('text', 'personal', '课堂补充')],
                    {'source_path': 'unknown', 'source_sha256': 'unknown',
                     'source_revision': 'unknown'})
    with pytest.raises(ValueError, match='知识卡'):
        knowledge_card_reader(notes.facade, card)


def test_cancelled_reader_resolution_has_no_side_effects(notes):
    before = disk_bytes(notes.root)
    event = TaskCancellationEvent()
    event.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        knowledge_card_reader(notes.facade, notes.item)
    assert disk_bytes(notes.root) == before and not notes.task_root.exists()
