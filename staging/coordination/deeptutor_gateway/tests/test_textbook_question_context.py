"""Chapter identity, stale-source handling and explicit native question handoff."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1 import desktop_textbook_question_context as module
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import suggest_attributes
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, selected, disk_bytes
from test_textbook_bookshelf_ui import page as page, app as app, leaf
from test_word_question_dialog import _Facade, _Tasks, _cleanup


def registered(f):
    return module.TextbookQuestionContextService(f.root).resolve({'mode': 'registered', **selected(f)})


class Personal:
    def __init__(self, source):
        self.source = source
        self.study_revision = 'b' * 64

    def read(self, selection, *, section_id, study_revision):
        assert selection == {'source_id': 'personal-original', 'revision': self.source['source_sha256']}
        assert section_id == 'saved-chapter' and study_revision == self.study_revision
        return deepcopy(self.source)

    def book_study(self, selection):
        return {'study_revision': self.study_revision}


def personal_service(f, **changes):
    node = next(n for n in f.registry['nodes'] if n['node_key'] == f.row['section_key'])
    source = {'source_sha256': node['source_sha256'], 'pdf_pages': module._pages(node),
              'title': '完全不同的个人章节名称', **changes}
    personal = Personal(source)
    request = {'mode': 'personal', 'selection': {'source_id': 'personal-original', 'revision': source['source_sha256']},
               'section_id': 'saved-chapter', 'study_revision': personal.study_revision}
    return module.TextbookQuestionContextService(f.root, personal), request, personal


def test_registered_context_verifies_original_and_is_read_only(shelf_fixture):
    f = shelf_fixture
    before = disk_bytes(f.root)
    context = registered(f)
    bound = module.question_filter_context(context, f.registry)
    assert bound['mapped'] and bound['selection']['book'] == [f.row['volume_id']]
    assert context['source_sha256'] == f.row['source_sha256']
    assert context['pdf_pages'] == module._pages(next(n for n in f.registry['nodes'] if n['node_key'] == f.row['section_key']))
    assert context['human_reviewed'] is False and context['candidate_only'] is True
    assert 'pdf_bytes' not in context and 'source_path' not in context
    assert disk_bytes(f.root) == before


@pytest.mark.parametrize('change', ['revision', 'missing', 'pdf'])
def test_stale_or_missing_registered_source_is_rejected(shelf_fixture, change):
    f = shelf_fixture
    request = {'mode': 'registered', **selected(f)}
    if change == 'revision': request['revision'] = 'f' * 64
    elif change == 'missing': request['section_key'] = 'nonexistent-section'
    else: f.book.write_bytes(f.book.read_bytes() + b'\nchanged')
    with pytest.raises(PreparationSourceError):
        module.TextbookQuestionContextService(f.root).resolve(request)


def test_directory_change_during_original_read_is_rejected(shelf_fixture, monkeypatch):
    f = shelf_fixture
    original = module.catalog._read_pdf
    def changed(*args):
        data = original(*args)
        f.registry['nodes'][0]['section_title'] += '修改'
        f.activate()
        return data
    monkeypatch.setattr(module.catalog, '_read_pdf', changed)
    with pytest.raises(PreparationSourceError, match='期间已变化'):
        registered(f)


def test_personal_mapping_uses_entire_pages_and_original_sha_not_title(shelf_fixture):
    f = shelf_fixture
    service, request, personal = personal_service(f)
    context = service.resolve(request)
    assert context['mode'] == 'curriculum' and context['section_key'] == f.row['section_key']
    assert context['title'] == personal.source['title']
    assert context['personal_binding']['selection'] == request['selection']
    assert service.validate(context) is True


@pytest.mark.parametrize('change', ['sha', 'partial_pages', 'oversized_pages'])
def test_unmapped_personal_chapter_keeps_explicit_text_search(shelf_fixture, change):
    service, request, personal = personal_service(shelf_fixture, title='2.2 合成分子性质')
    if change == 'sha':
        personal.source['source_sha256'] = request['selection']['revision'] = 'c' * 64
    elif change == 'partial_pages': personal.source['pdf_pages'] = personal.source['pdf_pages'][1:]
    else: personal.source['pdf_pages'].append(personal.source['pdf_pages'][-1] + 1)
    context = service.resolve(request)
    assert context['mode'] == 'text_search' and context['search_query'] == '合成分子性质'
    assert not any(key in context for key in ('volume_id', 'chapter_id', 'section_key'))
    assert module.question_filter_context(context, shelf_fixture.registry)['selection'] == {}


def test_ambiguous_personal_match_does_not_choose_first(shelf_fixture, monkeypatch):
    service, request, _ = personal_service(shelf_fixture)
    registry = deepcopy(shelf_fixture.registry)
    node = next(n for n in registry['nodes'] if n['node_key'] == shelf_fixture.row['section_key'])
    registry['nodes'].append({**node, 'node_key': 'separate-explicit-node'})
    monkeypatch.setattr(module.catalog, '_directory', lambda root: (registry, 'd' * 64, {}))
    assert service.resolve(request)['mode'] == 'text_search'


def test_personal_without_activated_directory_remains_searchable(shelf_fixture, monkeypatch):
    service, request, _ = personal_service(shelf_fixture)
    def unavailable(root): raise module.catalog.TextbookAssetCatalogError('目录不可用')
    monkeypatch.setattr(module.catalog, '_directory', unavailable)
    assert service.resolve(request)['mode'] == 'text_search'


def test_personal_revision_change_during_read_is_rejected(shelf_fixture):
    service, request, personal = personal_service(shelf_fixture)
    read = personal.read
    def changed(*args, **kwargs):
        source = read(*args, **kwargs)
        personal.study_revision = 'e' * 64
        return source
    personal.read = changed
    with pytest.raises(PreparationSourceError, match='期间已变化'):
        service.resolve(request)


@pytest.mark.parametrize('field,value', [('chapter_id', 'wrong-parent'), ('source_sha256', 'c' * 64),
                                         ('printed_pages', [50, 99]), ('section_title', '新标题')])
def test_complete_current_node_must_match_handoff(shelf_fixture, field, value):
    context = registered(shelf_fixture)
    current = deepcopy(shelf_fixture.registry)
    node = next(n for n in current['nodes'] if n['node_key'] == context['section_key'])
    node[field] = value
    with pytest.raises(PreparationSourceError): module.question_filter_context(context, current)


def test_registered_validation_rechecks_original_pdf(shelf_fixture):
    context = registered(shelf_fixture)
    shelf_fixture.book.write_bytes(shelf_fixture.book.read_bytes() + b'changed')
    with pytest.raises(PreparationSourceError): module.TextbookQuestionContextService(shelf_fixture.root).validate(context)


def native_dialog(f):
    context = registered(f)
    facade = _Facade(saved=[{'key': 'Q2', 'revision': 'revision-Q2', 'points': 2}])
    facade.catalog['attribute_catalog'] = deepcopy(f.registry)
    item = facade.catalog['items'][0]
    item['source_sha256'] = 'a' * 64
    item['attributes'] = suggest_attributes(item, {}, f.registry)
    item['attributes']['curriculum_status'] = 'auto_suggested'
    item['attributes']['curriculum_candidates'] = [{
        'volume_id': context['volume_id'], 'chapter_id': context['chapter_id'],
        'section_key': context['section_key'], 'status': 'auto_suggested',
    }]
    tasks = _Tasks()
    dialog = WordQuestionDialog(facade, tasks, initial_curriculum_context=context, handoff_mode='both')
    tasks.flush()
    return dialog, facade, tasks, context


def test_native_chapter_filter_preserves_outside_choices_and_does_not_select(app, shelf_fixture):
    dialog, facade, tasks, context = native_dialog(shelf_fixture)
    assert dialog.question_list.count() == 1
    assert dialog.question_list.item(0).data(256) == 'Q1'
    assert dialog.selections == [{'key': 'Q2', 'revision': 'revision-Q2', 'points': 2}]
    assert not any(call[0] == 'save' for call in facade.calls)
    before = deepcopy(dialog.selections)
    dialog.all_questions_button.click()
    assert dialog.question_list.count() == 3 and dialog.selections == before
    dialog.restore_section_button.click();tasks.flush()
    assert dialog.question_list.count() == 1 and dialog.selections == before
    _cleanup(dialog, tasks)


def test_native_directory_change_requires_explicit_all_browse(app, shelf_fixture):
    dialog, facade, tasks, context = native_dialog(shelf_fixture)
    next(n for n in facade.catalog['attribute_catalog']['nodes'] if n['node_key'] == context['section_key'])['printed_pages'] = [1, 90]
    dialog._load_catalog(); tasks.flush()
    assert dialog.question_list.count() == 0 and dialog._section_context_blocked
    assert '已变化' in dialog.section_context_note.text()
    dialog.all_questions_button.click()
    assert dialog.question_list.count() == 3
    dialog._load_catalog();tasks.flush()
    assert dialog.question_list.count() == 3 and not dialog._section_context_blocked
    _cleanup(dialog, tasks)


def test_native_context_service_error_is_visible_before_browsing(app, shelf_fixture):
    dialog, facade, tasks, _ = native_dialog(shelf_fixture)
    def stale(context): raise PreparationSourceError('原书版本已变化')
    facade.validate_textbook_question_context = stale
    dialog._load_catalog();tasks.flush()
    assert dialog.question_list.count() == 0 and '原书版本已变化' in dialog.section_context_note.text()
    assert not dialog.composition_button.isEnabled()
    _cleanup(dialog, tasks)


def test_native_registered_entry_emits_only_after_source_check(page, app):
    widget, bridge, f = page
    emitted = []
    widget.question_context_requested.connect(emitted.append)
    widget.questions_button.click()
    assert emitted == [] and not widget.questions_button.isEnabled()
    bridge.succeed(bridge.take('核对本节选题范围'), run=True)
    assert len(emitted) == 1 and emitted[0]['section_key'] == f.row['section_key']
    assert widget._reader is None and widget._checked == {}


def test_native_section_switch_ignores_old_async_context(page, app):
    widget, bridge, f = page
    emitted = []
    widget.question_context_requested.connect(emitted.append)
    widget.questions_button.click()
    pending = bridge.take('核对本节选题范围')
    widget.tree.setCurrentItem(leaf(widget, f.row['chapter_id'] + ':1.2'))
    bridge.succeed(pending, run=True)
    assert emitted == [] and widget.questions_button.isEnabled()


@pytest.mark.parametrize('outcome', ['composition', 'reference', 'cancel', 'busy'])
def test_window_routes_only_explicitly_accepted_question_handoff(monkeypatch, outcome):
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    from integrations.deeptutor_shchem_v1.desktop_workbench import word_question_dialog
    from PySide6.QtWidgets import QDialog
    calls = []
    context = {'title': '本节'}
    class Picker:
        def __init__(self, facade, tasks, parent, **kwargs):
            calls.append(('open', kwargs))
            self.basket_changed = SimpleNamespace(connect=lambda callback: None)
            self.composition_result = {'questions': ['chosen']} if outcome == 'composition' else None
            self.preparation_reference = {'materials': 'previewed'} if outcome == 'reference' else None
        def exec(self):
            return QDialog.DialogCode.Rejected if outcome == 'cancel' else QDialog.DialogCode.Accepted
        def deleteLater(self): calls.append(('disposed',))
    monkeypatch.setattr(word_question_dialog, 'WordQuestionDialog', Picker)
    window = SimpleNamespace(facade=object(), tasks=object(),
        preparation_page=SimpleNamespace(studio_busy=lambda: outcome == 'busy', topic=SimpleNamespace(text=lambda: '当前备课')),
        paper_page=SimpleNamespace(update_basket_count=lambda count: None),
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: calls.append(('busy',))),
        _word_cards_to_preparation=lambda result: calls.append(('composition', result)),
        _word_to_preparation=lambda result: calls.append(('reference', result)))
    TeacherWorkbenchWindow._textbook_questions(window, context)
    if outcome == 'busy':
        assert calls == [('busy',)]
    else:
        assert calls[0][1] == {'initial_curriculum_context': context, 'lesson_topic': '当前备课', 'handoff_mode': 'both'}
        assert calls[-1] == ('disposed',)
        assert len(calls) == (2 if outcome == 'cancel' else 3)
        if outcome != 'cancel': assert calls[1][0] == outcome
