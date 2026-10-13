"""Real source readers establish context; no desktop store or window is made."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from test_note_page_crops import notes, request as note_request
from test_textbook_preparation_crops import crop_fixture, request as asset_request
from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture, colored_pdf
from test_textbook_concept_cards import concept, supplement, supplement_fixture
from test_textbook_crop_reading import files
from test_textbook_reading_hints import write_packet

from integrations.deeptutor_shchem_v1.desktop_composition_sources import (
    import_knowledge_note, import_textbook_crop, knowledge_options, knowledge_selection)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FLAGS, bind_knowledge_selection, digest)
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_crops import book_page_selection
from integrations.deeptutor_shchem_v1.desktop_section_materials import (
    section_material_context, recheck_section_material_context)
from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import (
    concept_options, concept_page_selection)
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import (
    PersonalTextbookStudyService, FLAGS as PERSONAL_FLAGS, STUDY_SCHEMA)
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import SCHEMA, TextbookWorkspaceService
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, read_cancel_scope)


def _empty_personal(facade):
    facade.personal_textbook_study = lambda: SimpleNamespace(books=lambda: [])
    return facade


@pytest.fixture
def registered_note(notes):
    _empty_personal(notes.facade)
    return notes


@pytest.fixture
def registered_crop(crop_fixture):
    f = crop_fixture
    _empty_personal(f.facade)
    result = import_textbook_crop(f.facade, asset_request(f), [])
    f.item, f.assets = result['resources'][0], result['image_assets']
    return f


def _crop(facade, selection):
    page = facade.preparation_textbook_crop_page(selection)
    bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    preview = facade.preparation_textbook_crop_preview(
        selection, page['page_revision'], bounds, '合成原图', '核对测试来源')
    request = {'selection': selection, 'page_revision': page['page_revision'], 'bounds': bounds,
        'caption': '合成原图', 'purpose': '核对测试来源',
        'expected_preview_revision': preview['preview_revision'],
        'expected_image_sha256': preview['image_asset']['sha256'], 'selection_confirmed': True}
    result = import_textbook_crop(facade, request, [])
    return result['resources'][0], result['image_assets']


def _assert_closed(value):
    assert all(value[key] is flag for key, flag in FLAGS.items())
    assert not any(isinstance(v, bytes) for v in value.values())
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def test_registered_note_uses_original_despite_work_edits_and_never_writes(registered_note):
    f = registered_note
    f.item['title'] = '氢键（教师改名；不得拿标题查章节）'
    f.item['blocks'][0]['value'] = '可编辑的课堂工作内容'
    before = files(f.root, f.task_root)
    inputs = deepcopy([f.item])
    value = section_material_context(f.facade, f.item, [f.item], [])
    assert value['status'] == 'ready'
    assert value['section_filter_key'] == f.current['section_key']
    assert value['book_key'] == 'registered:' + f.current['volume_id']
    assert value['knowledge_selection'] == f.selected
    assert value['original_title'] == f.item['original']['title']
    assert value['source_binding']['pdf_pages'] == [8, 9]
    assert value['source_binding']['printed_pages'] == [4, 5]
    assert value['knowledge_selection'] in [knowledge_selection(r) for r in value['knowledge_options']]
    _assert_closed(value)
    assert recheck_section_material_context(f.facade, f.item, [f.item], [], value)
    assert [f.item] == inputs and files(f.root, f.task_root) == before
    assert not f.task_root.exists()


@pytest.mark.parametrize('change', ['title', 'text', 'source', 'note', 'directory', 'missing_pdf'])
def test_registered_note_source_changes_fail_closed(registered_note, change):
    f = registered_note
    value = section_material_context(f.facade, f.item, [f.item], [])
    if change == 'title':
        f.item['original']['title'] = '伪造的原标题'
        f.item['original_sha256'] = digest(f.item['original'])
    elif change == 'text':
        f.item['original']['blocks'][0]['value'] = '伪造的原内容'
        f.item['original_sha256'] = digest(f.item['original'])
    elif change == 'source':
        f.book.write_bytes(f.book.read_bytes() + b'% changed')
    elif change == 'note':
        f.note['summary'] += '新来源版本'
        write_packet(f.root, [f.note])
    elif change == 'directory':
        f.registry['volumes'][0]['volume_title'] += '已改变'
        f.activate()
    else:
        f.book.unlink()
    before = files(f.root, f.task_root)
    with pytest.raises(ValueError):
        recheck_section_material_context(f.facade, f.item, [f.item], [], value)
    assert files(f.root, f.task_root) == before and not f.task_root.exists()


def test_registered_asset_crop_uses_real_section_without_guessing_default_knowledge(registered_crop):
    f = registered_crop
    before = files(f.root, f.task_root)
    inputs = deepcopy((f.item, f.assets))
    value = section_material_context(f.facade, f.item, [f.item], f.assets)
    assert value['section_filter_key'] == f.row['section_key']
    assert value['knowledge_selection'] is None and value['knowledge_options']
    assert all(r['section_filter_key'] == value['section_filter_key'] for r in value['knowledge_options'])
    assert value['source_binding']['crop_binding']['source']['pdf_page'] == 9
    assert recheck_section_material_context(f.facade, f.item, [f.item], f.assets, value)
    _assert_closed(value)
    assert (f.item, f.assets) == inputs and files(f.root, f.task_root) == before


def test_unbound_concept_crop_resolves_real_source_without_any_course_knowledge_card(concept):
    f = concept
    item, assets = _crop(f.facade, concept_page_selection(f.option, 8))
    assert 'selected_from_knowledge' not in item['original']
    before = files(f.root, f.facade.paths.task_root)
    value = section_material_context(f.facade, item, [item], assets)
    assert value['knowledge_selection'] == f.option['selection']
    assert value['section_filter_key'] == f.option['section_filter_key']
    assert value['source_binding']['crop_binding']['source']['record_id'] == f.row['concept_id']
    assert recheck_section_material_context(f.facade, item, [item], assets, value)
    assert files(f.root, f.facade.paths.task_root) == before


def test_unbound_registered_note_crop_keeps_exact_note_selection(registered_note):
    f = registered_note
    result = import_textbook_crop(f.facade, note_request(f), [])
    item, assets = result['resources'][0], result['image_assets']
    value = section_material_context(f.facade, item, [item], assets)
    assert value['knowledge_selection'] == f.selected
    assert value['section_kind'] == 'registered'


@pytest.mark.parametrize('case', ['missing', 'duplicate', 'original_changed', 'work_edited'])
def test_bound_crop_requires_unique_real_classroom_knowledge_original(registered_note, case):
    f = registered_note
    result = import_textbook_crop(f.facade, note_request(f), [])
    crop = bind_knowledge_selection(result['resources'][0], f.item)
    assets = result['image_assets']
    resources = [crop, f.item]
    if case == 'missing':
        resources = [crop]
    elif case == 'duplicate':
        resources.append(deepcopy(f.item))
    elif case == 'original_changed':
        f.item['original']['blocks'][0]['value'] += '原件改变'
        f.item['original_sha256'] = digest(f.item['original'])
    else:
        f.item['title'] = '工作标题自由改名'
        f.item['blocks'][0]['value'] += '工作副本改变'
    before = files(f.root, f.task_root)
    inputs = deepcopy((resources, assets))
    if case == 'work_edited':
        value = section_material_context(f.facade, crop, resources, assets)
        assert value['knowledge_selection'] == f.selected
        assert value['source_binding']['knowledge_resource_id'] == f.item['id']
    else:
        with pytest.raises(ValueError):
            section_material_context(f.facade, crop, resources, assets)
    assert (resources, assets) == inputs and files(f.root, f.task_root) == before


@pytest.mark.parametrize('as_crop', [False, True])
def test_supplement_identity_kept_separate_from_registered_numbered_sections(supplement, as_crop):
    f = supplement
    if as_crop:
        item, assets = _crop(f.facade, concept_page_selection(f.option, 43))
    else:
        item, assets = import_knowledge_note(f.facade, f.option['selection']), []
    value = section_material_context(f.facade, item, [item], assets)
    assert value['section_kind'] == 'supplement'
    assert value['section_filter_key'] == 'declared-project'
    assert value['knowledge_selection'] == f.option['selection']
    assert all(row['section_filter_key'] == 'declared-project' for row in value['knowledge_options'])
    other = next(row for row in value['section_choices'] if row['section_kind'] == 'registered')
    with pytest.raises(ValueError):
        section_material_context(f.facade, item, [item], assets,
                                 section_filter_key=other['section_filter_key'])


def test_unmapped_concept_offers_only_its_verified_book_sections_without_claiming_membership(concept):
    f = concept
    f.row['section_key'] = None
    f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    option = concept_options(f.root)[0]
    item, assets = _crop(f.facade, concept_page_selection(option, 8))
    value = section_material_context(f.facade, item, [item], assets)
    assert value['status'] == 'section_selection_required'
    assert value['knowledge_selection'] is None and value['knowledge_options'] == []
    assert all(row['book_key'] == 'registered:' + f.row['volume_id'] for row in value['section_choices'])
    chosen = value['section_choices'][0]['section_filter_key']
    explicit = section_material_context(f.facade, item, [item], assets, section_filter_key=chosen)
    assert explicit['status'] == 'ready' and explicit['knowledge_selection'] is None
    assert recheck_section_material_context(f.facade, item, [item], assets, explicit)
    with pytest.raises(ValueError):
        section_material_context(f.facade, item, [item], assets, section_filter_key='registered:wrong-book')


@pytest.fixture
def personal(tmp_path):
    """Actual personal read services over an immutable snapshot; no StateStore."""
    root, state_root = tmp_path / 'workspace', tmp_path / 'fixture-state'
    root.mkdir()
    source_root = state_root / 'textbook-sources'
    source_root.mkdir(parents=True)
    raw = colored_pdf(4)
    source_hash = sha256(raw).hexdigest()
    source_id = 'BOOK-' + source_hash
    source = source_root / (source_hash + '.pdf')
    source.write_bytes(raw)
    book = {'source_id': source_id, 'source_sha256': source_hash,
            'source_name': '不透露章节的合成整书.pdf', 'page_count': 4,
            'human_reviewed': False, 'review_status': 'local_archived_pending_review'}
    section = {'section_id': 'BSEC-' + '1' * 32, 'source_id': source_id,
        'source_sha256': source_hash, 'title': '教师明确登记的章', 'pdf_start': 2, 'pdf_end': 3,
        'printed_pages': None, 'range_confirmed': True, 'created_at': 'fixture', 'updated_at': 'fixture',
        **PERSONAL_FLAGS}
    note = {'note_id': 'BNOTE-' + '2' * 32, 'section_id': section['section_id'],
        'title': '同节个人知识点', 'text': '仅作来源测试的知识文字', 'pdf_page': 2,
        'teacher_confirmed': True, 'origin': 'teacher_entered',
        'created_at': 'fixture', 'updated_at': 'fixture', **PERSONAL_FLAGS}
    record = {'textbook_workspace': {'schema': SCHEMA, 'books': [book], 'candidate_snapshot': None,
        'personal_study': {'schema': STUDY_SCHEMA, 'sections': [section], 'notes': [note]}}}
    state = SimpleNamespace(snapshot=lambda: deepcopy(record))
    paths = SimpleNamespace(workspace_root=root, state_root=state_root, task_root=state_root / 'tasks')
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths, facade._state = paths, state
    workspace = TextbookWorkspaceService(paths, state)
    study = PersonalTextbookStudyService(workspace)
    return SimpleNamespace(root=root, state_root=state_root, source=source, record=record,
        paths=paths, facade=facade, study=study, workspace=workspace,
        selection={'source_id': source_id, 'revision': source_hash}, section=section)


def test_personal_note_resolves_saved_section_without_writes_or_printed_page_guesses(personal):
    f = personal
    option = knowledge_options(f.facade)['reading_notes'][0]
    item = import_knowledge_note(f.facade, option['selection'])
    before = files(f.root, f.state_root)
    snapshot = deepcopy(f.record)
    value = section_material_context(f.facade, item, [item], [])
    assert value['section_kind'] == 'personal'
    assert value['knowledge_selection'] == option['selection']
    assert value['source_binding']['printed_pages'] == 'unknown'
    assert recheck_section_material_context(f.facade, item, [item], [], value)
    assert files(f.root, f.state_root) == before and f.record == snapshot


@pytest.mark.parametrize('case', ['unique', 'overlap', 'outside', 'none'])
def test_personal_unbound_crop_uses_only_real_book_declared_ranges(personal, case):
    f = personal
    doc = f.record['textbook_workspace']['personal_study']
    if case == 'overlap':
        other = deepcopy(f.section)
        other.update(section_id='BSEC-' + '3' * 32, title='另一已登记范围')
        doc['sections'].append(other)
    if case == 'none':
        doc['sections'], doc['notes'] = [], []
    book = f.study.books()[0]
    page = 1 if case == 'outside' else 2
    item, assets = _crop(f.facade, book_page_selection(book, page))
    before = files(f.root, f.state_root)
    value = section_material_context(f.facade, item, [item], assets)
    assert value['knowledge_selection'] is None
    assert value['book_key'] == 'personal:' + f.selection['source_id']
    if case == 'unique':
        assert value['status'] == 'ready' and len(value['knowledge_options']) == 1
    elif case == 'none':
        assert value['status'] == 'no_section' and value['section_choices'] == []
    else:
        assert value['status'] == 'section_selection_required' and value['knowledge_options'] == []
        choice = value['section_choices'][0]['section_filter_key']
        explicit = section_material_context(f.facade, item, [item], assets, section_filter_key=choice)
        assert explicit['status'] == 'ready' and explicit['knowledge_selection'] is None
        assert recheck_section_material_context(f.facade, item, [item], assets, explicit)
    assert files(f.root, f.state_root) == before


@pytest.mark.parametrize('change', ['pdf_bytes', 'source_sha256', 'pdf_pages', 'identity', 'verification', 'binding'])
def test_independent_crop_adapter_mismatch_is_rejected(registered_crop, monkeypatch, change):
    f = registered_crop
    returned = f.facade.preparation_textbook_crop_original(f.item, f.assets)
    if change == 'pdf_bytes': returned['source']['pdf_bytes'] = b'not a full original'
    elif change == 'source_sha256': returned['source']['source_sha256'] = 'f' * 64
    elif change == 'pdf_pages': returned['source']['pdf_pages'] = [8]
    elif change == 'identity': returned['binding']['resource_id'] = 'CR-' + 'f' * 32
    elif change == 'verification': returned['crop_pixels_verified'] = False
    else: returned['binding_sha256'] = 'f' * 64
    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_original', lambda *args: deepcopy(returned))
    before = files(f.root, f.task_root)
    with pytest.raises(ValueError):
        section_material_context(f.facade, f.item, [f.item], f.assets)
    assert files(f.root, f.task_root) == before


def test_same_section_candidate_must_keep_actual_scope_revision(registered_crop, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as contexts
    f = registered_crop
    real = contexts.knowledge_options
    def stale(facade):
        result = deepcopy(real(facade))
        result['concepts'][0]['selection']['location']['scope_revision'] = 'f' * 64
        return result
    monkeypatch.setattr(contexts, 'knowledge_options', stale)
    with pytest.raises(ValueError, match='版本失配'):
        section_material_context(f.facade, f.item, [f.item], f.assets)


@pytest.mark.parametrize('change', ['source', 'note', 'printed_pages'])
def test_personal_original_and_saved_note_changes_are_rejected(personal, change):
    f = personal
    option = knowledge_options(f.facade)['reading_notes'][0]
    item = import_knowledge_note(f.facade, option['selection'])
    value = section_material_context(f.facade, item, [item], [])
    if change == 'source':
        f.source.write_bytes(b'not the archived PDF')
    elif change == 'note':
        f.record['textbook_workspace']['personal_study']['notes'][0]['text'] += '新笔记版本'
    else:
        item['original']['evidence']['printed_pages'] = [2]
        item['original_sha256'] = digest(item['original'])
    before = files(f.root, f.state_root)
    with pytest.raises(ValueError):
        recheck_section_material_context(f.facade, item, [item], [], value)
    assert files(f.root, f.state_root) == before


@pytest.mark.parametrize('field', ['book_key', 'section_filter_key', 'knowledge_selection', 'knowledge_options',
                                   'source_binding', 'binding_sha256', 'human_reviewed', 'extra'])
def test_recheck_rejects_any_modified_context(registered_note, field):
    f = registered_note
    value = section_material_context(f.facade, f.item, [f.item], [])
    value[field] = 'changed'
    with pytest.raises(ValueError):
        recheck_section_material_context(f.facade, f.item, [f.item], [], value)


def test_late_source_change_and_cancel_return_no_context(registered_crop, monkeypatch):
    f = registered_crop
    real = f.facade.preparation_textbook_crop_original
    calls = []
    def late(*args):
        value = real(*args)
        calls.append(True)
        if len(calls) == 1:
            f.row['statement'] += '并发变更'
            f.catalog.write_text(json.dumps(f.row), encoding='utf8')
        return value
    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_original', late)
    with pytest.raises(ValueError):
        section_material_context(f.facade, f.item, [f.item], f.assets)
    before = files(f.root, f.task_root)
    cancelled = TaskCancellationEvent()
    cancelled.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(cancelled):
        section_material_context(f.facade, f.item, [f.item], f.assets)
    assert files(f.root, f.task_root) == before
