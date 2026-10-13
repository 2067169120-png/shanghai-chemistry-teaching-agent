"""Direct real sections stay usable without notes, concepts or invented cards."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import MethodType, SimpleNamespace
import json

import pytest

from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_supplements import supplement_fixture
from test_personal_textbook_study import pdf

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_section_materials import textbook_section_material_context
from integrations.deeptutor_shchem_v1.desktop_section_material_sources import (
    section_textbook_options, section_question_context, recheck_section_question_context)
from integrations.deeptutor_shchem_v1.desktop_section_source_stage import SectionSourceStage
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import TextbookWorkspaceService
from integrations.deeptutor_shchem_v1.desktop_textbook_personal_study import PersonalTextbookStudyService


def facade(root, tasks, study=None, workspace=None):
    value = SimpleNamespace(paths=SimpleNamespace(workspace_root=root, task_root=tasks, state_root=tasks.parent / 'state'))
    value.personal_textbook_study = lambda: study or SimpleNamespace(books=lambda: [])
    value.textbook_workspace = lambda: workspace
    for name in ('textbook_bookshelf', 'textbook_question_context', 'validate_textbook_question_context',
        '_textbook_crop_call', 'preparation_textbook_crop_page', 'preparation_textbook_crop_preview',
        'import_textbook_preparation_crop', 'import_textbook_preparation_crops', 'preparation_textbook_crop_original',
        'preparation_image_bytes'):
        setattr(value, name, MethodType(getattr(DesktopWorkbenchFacade, name), value))
    return value


@pytest.fixture
def empty_registered(shelf_fixture, tmp_path):
    f = shelf_fixture
    f.catalog.write_text('\n', encoding='utf8')
    f.facade = facade(f.root, tmp_path / 'tasks')
    selected = {'mode': 'registered', 'section_key': f.row['section_key'], 'revision': f.shelf._directory()[1]}
    f.context = textbook_section_material_context(f.facade, 'registered:' + f.row['volume_id'], f.row['section_key'], selected)
    assert f.context['knowledge_options'] == [] and f.context['knowledge_selection'] is None
    return f


@pytest.fixture
def empty_supplement(supplement_fixture, tmp_path):
    f = supplement_fixture
    f.catalog.write_text('\n', encoding='utf8')
    f.product['supplement_nodes'][0]['pdf_page_list'] = [43, 45]
    f.product['supplement_nodes'][0]['printed_pages'] = []
    f.write_product(); f.write_assets([])
    f.facade = facade(f.root, tmp_path / 'tasks')
    row = next(row for row in f.shelf.snapshot()['supplements'] if row['supplement_node_key'] == 'declared-project')
    selected = {'mode': 'supplement', 'supplement_node_key': row['supplement_node_key'], 'revision': row['revision']}
    f.context = textbook_section_material_context(f.facade, 'registered:' + row['volume_id'], row['supplement_node_key'], selected)
    assert f.context['knowledge_options'] == []
    return f


class MemoryState:
    def __init__(self): self.value = {}
    def snapshot(self): return deepcopy(self.value)
    def _update(self, operation):
        result = deepcopy(self.value)
        changed = operation(result)
        if changed is not False: self.value = result
        return deepcopy(self.value)


@pytest.fixture
def empty_personal(tmp_path):
    root, state_root = tmp_path / 'workspace', tmp_path / 'owned-state'
    paths = DesktopPaths.from_workspace(root, state_root=state_root)
    state = MemoryState(); workspace = TextbookWorkspaceService(paths, state)
    original = pdf(tmp_path / 'source/synthetic.pdf', pages=4)
    workspace.commit_books(workspace.preview_books([original]))
    study = PersonalTextbookStudyService(workspace)
    book = study.books()[0]; selection = {key: book[key] for key in ('source_id', 'revision')}
    view = study.book_study(selection)
    view = study.save_section(selection, view['study_revision'], title='合成原书完整第二节',
        pdf_start=2, pdf_end=3, range_confirmed=True)
    f = SimpleNamespace(root=root, state=state, workspace=workspace, study=study, book=book)
    f.facade = facade(root, tmp_path / 'tasks', study, workspace)
    request = {'mode': 'personal', 'selection': selection, 'section_id': view['sections'][0]['section_id'],
               'study_revision': view['study_revision']}
    key = 'personal:' + book['source_id']
    f.context = textbook_section_material_context(f.facade, key, key + ':' + request['section_id'], request)
    assert f.context['knowledge_options'] == [] and view['notes'] == []
    return f


def test_empty_registered_offers_catalog_and_every_complete_original_page(empty_registered):
    f = empty_registered; before = f.book.read_bytes()
    result = section_textbook_options(f.facade, f.context)
    pages = [row['pdf_page'] for row in result['rows'] if row['origin'] == 'section_textbook_page']
    assert pages == f.context['source_binding']['pdf_pages'] and len(pages) > 1
    assert any(row['origin'] == 'textbook_catalog_asset' for row in result['rows'])
    assert all(row['selection']['visual_asset_id'].startswith('SECTIONPAGE-') for row in result['rows']
               if row['origin'] == 'section_textbook_page')
    assert len(result['binding']) == 64 and result['section_binding_sha256'] == f.context['binding_sha256']
    assert 'knowledge_note' not in json.dumps(result) and 'NOTEPAGE-' not in json.dumps(result)
    assert before == f.book.read_bytes() and not f.facade.paths.task_root.exists()
    browser = section_question_context(f.facade, f.context)
    assert browser['curriculum_context']['entry_origin'] == 'registered_textbook'
    assert browser['curriculum_context']['validation_request'] == f.context['section_selection']
    assert browser['section_context'] == f.context and recheck_section_question_context(f.facade, f.context, browser)


def test_supplement_keeps_noncontinuous_pages_and_explicit_text_search(empty_supplement):
    f = empty_supplement
    result = section_textbook_options(f.facade, f.context)
    assert [row['pdf_page'] for row in result['rows']] == [43, 45]
    browser = section_question_context(f.facade, f.context)
    assert browser['mode'] == 'text_search' and browser['curriculum_context'] is None
    assert browser['search_query'] == f.context['section_label']
    assert browser['section_context']['source_binding']['pdf_pages'] == [43, 45]
    assert recheck_section_question_context(f.facade, f.context, browser)


def test_personal_bookpage_only_lists_this_section_and_real_text_search(empty_personal):
    f = empty_personal
    result = section_textbook_options(f.facade, f.context)
    assert [row['pdf_page'] for row in result['rows']] == [2, 3]
    assert all(row['selection']['visual_asset_id'].startswith('BOOKPAGE-') for row in result['rows'])
    assert all(row['printed_page'] is None for row in result['rows'])
    browser = section_question_context(f.facade, f.context)
    assert browser['curriculum_context']['entry_origin'] == 'personal_textbook'
    assert browser['curriculum_context']['mode'] == 'text_search'
    assert browser['curriculum_context']['personal_binding']['section_id'] == f.context['section_selection']['section_id']
    assert recheck_section_question_context(f.facade, f.context, browser)


@pytest.mark.parametrize('change', ['book', 'section', 'context', 'pdf', 'directory', 'asset'])
def test_direct_source_changes_reject_without_a_card(empty_registered, change):
    f = empty_registered; context = deepcopy(f.context)
    browser = section_question_context(f.facade, context)
    if change == 'book': context['book_key'] = 'registered:TB-M1'
    elif change == 'section': context['section_selection']['section_key'] = 'unselected'
    elif change == 'context': context['source_binding']['pdf_pages'] = [9]
    elif change == 'pdf': f.book.write_bytes(f.book.read_bytes() + b'\n% changed original\n')
    elif change == 'directory': f.registry['nodes'][0]['section_title'] += 'changed'; f.activate()
    else: f.asset['description'] += 'changed'; f.write_assets([f.asset])
    with pytest.raises(ValueError): section_textbook_options(f.facade, context)
    with pytest.raises(ValueError): recheck_section_question_context(f.facade, context, browser)


@pytest.mark.parametrize('domain', ['options', 'questions'])
def test_independent_helper_memo_copies_and_fresh_native_tail_rejects_change(empty_registered, domain, monkeypatch):
    f = empty_registered; stage = SectionSourceStage(f.facade, excluded_roots=(f.facade.paths.task_root,))
    f.facade._section_source_stage = stage
    reader = section_textbook_options if domain == 'options' else section_question_context
    first = reader(f.facade, f.context)
    if domain == 'options': first['rows'].clear()
    else: first['original_title'] = 'caller work edit'
    second = reader(f.facade, f.context)
    assert second != first
    if domain == 'questions':
        original = f.facade.textbook_question_context
        def changed(request):
            value = original(request); value['statement'] += 'changed native port'; return value
        f.facade.textbook_question_context = changed
        f.facade.validate_textbook_question_context = lambda _value: True
    else:
        from integrations.deeptutor_shchem_v1 import desktop_section_material_sources as ports
        native = ports._source
        def changed_source(*args):
            value = native(*args)
            value['visual_assets']['assets'][0]['label'] += 'changed memory source port'
            return value
        monkeypatch.setattr(ports, '_source', changed_source)
    with pytest.raises(ValueError): stage.finish()
    assert stage.closed and not stage.reads


def test_original_pdf_reparse_metadata_is_rejected_read_only(empty_registered, monkeypatch):
    f = empty_registered
    native = Path.lstat
    def reparse(path, *args, **kwargs):
        value = native(path, *args, **kwargs)
        if path.absolute() == f.book.absolute():
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(ValueError): section_textbook_options(f.facade, f.context)
    assert not f.facade.paths.task_root.exists()
