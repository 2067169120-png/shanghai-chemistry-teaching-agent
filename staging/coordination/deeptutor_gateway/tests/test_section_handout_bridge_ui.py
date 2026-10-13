"""Request-only section/lecture bridge, using standalone Qt dialogs and fake tasks."""
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from test_desktop_library_ui import _ManualBridge
from test_handout_knowledge_picker_ui import _SyntheticLibrary
from test_section_materials_dialog_ui import Links, card
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as workspace_ui
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as section_ui
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


COURSE_SOURCES = '读取本课已用讲义来源'
OPEN = '打开本课讲义选材'
VERIFY = '核对当前知识与讲义选材'
CATALOG = '读取讲义选材资料目录'
OUTLINE = '读取当前讲义选材原文'
CONFIRM = '核对本次讲义选材来源'


class Tasks(_ManualBridge):
    is_shutting_down = False

    def finish(self, label):
        pending = self.take(label)
        try:
            value = pending.operation()
        except (ValueError, OSError) as exc:
            pending.on_failure(str(exc))
        else:
            pending.on_success(value)
        return pending


def events(app):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    install_ui_font(application)
    application.setStyle('Fusion')
    return application


@pytest.fixture
def make_workspace(app, monkeypatch):
    dialogs = []

    def make(*, initial=True, library=None, source=None):
        library = library or _SyntheticLibrary()
        tasks = Tasks()
        document = deepcopy(library.outlines[library.sources[0]['source_id']]['source'])
        calls = []
        def context(_facade, binding):
            calls.append(deepcopy(binding))
            return {'document_source': deepcopy(document)}
        monkeypatch.setattr(workspace_ui, 'handout_knowledge_sources', library.catalog)
        monkeypatch.setattr(workspace_ui, 'handout_knowledge_outline', library.outline)
        monkeypatch.setattr(workspace_ui, 'handout_source_context', context)
        bound = deepcopy(source) if source is not None else document
        dialog = workspace_ui.HandoutMaterialWorkspace(SimpleNamespace(), tasks,
            initial_source=bound if initial else None)
        dialogs.append(dialog)
        return SimpleNamespace(dialog=dialog, tasks=tasks, library=library, source=bound, calls=calls, app=app)
    yield make
    for dialog in dialogs:
        dialog.reject()
        dialog.deleteLater()
    events(app)


def load_workspace(case):
    case.tasks.finish(CATALOG)
    if case.dialog.initial_source is None:
        case.dialog.source.setCurrentIndex(1)
    case.tasks.finish(OUTLINE)


def knowledge(outline, *, teacher_only=False):
    return {'source': deepcopy(outline['source']), 'outline_revision': outline['outline_revision'],
        'section': deepcopy(outline['sections'][0]), 'title': '完整知识栏目与原表格', 'teacher_only': teacher_only}


def mixed_request(source):
    return {'schema': workspace_ui.SCHEMA, 'source': deepcopy(source),
        'knowledge_requests': [{'source': deepcopy(source), 'title': '完整讲义知识', 'teacher_only': True}],
        'image_selections': [{'title': '完整知识结构网', 'teacher_only': False}],
        'question_selections': [{'key': 'Q1', 'revision': 'q1', 'points': 2}],
        'selection_order': [{'kind': 'image', 'index': 0}, {'kind': 'knowledge', 'index': 0},
                            {'kind': 'question', 'index': 0}]}


def test_preset_retains_all_eight_fields_and_never_chooses_same_named_source(make_workspace):
    library = _SyntheticLibrary()
    library.sources[1]['source_name'] = library.sources[0]['source_name']
    case = make_workspace(library=library)
    expected = deepcopy(case.source)
    case.source['source_path'] = 'changed-by-caller.docx'
    load_workspace(case)
    assert case.library.outline_calls == [expected]
    assert case.dialog.initial_source == case.dialog._document == expected
    assert case.dialog.source.count() == 2
    assert case.dialog.source.currentData() == expected
    assert not case.dialog.source.isEnabled() and not case.dialog.source_search.isEnabled()
    case.dialog.source_search.setText('another name')
    assert case.dialog.source.currentData() == expected and len(case.library.outline_calls) == 1
    assert case.dialog.request is None and not case.dialog._selections


@pytest.mark.parametrize('change', ['metadata_only', 'extra_field', 'picture', 'wrong_role', 'empty_path'])
def test_preset_requires_exact_document_binding(app, change):
    library = _SyntheticLibrary()
    source = deepcopy(library.outlines[library.sources[0]['source_id']]['source'])
    if change == 'metadata_only':
        source = deepcopy(library.sources[0])
    elif change == 'extra_field':
        source['extra'] = True
    elif change == 'picture':
        source['schema'] = 'shchem.handout-picture.v1'
    elif change == 'wrong_role':
        source['source_role'] = 'teacher'
    else:
        source['source_path'] = ''
    tasks = Tasks()
    with pytest.raises(ValueError, match='来源'):
        workspace_ui.HandoutMaterialWorkspace(SimpleNamespace(), tasks, initial_source=source)
    assert not tasks.pending


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'role'])
def test_missing_or_ambiguous_preset_has_no_catalog_fallback(make_workspace, change):
    case = make_workspace()
    if change == 'missing':
        case.library.sources.pop(0)
    elif change == 'duplicate':
        case.library.sources.append(deepcopy(case.library.sources[0]))
    else:
        case.library.sources[0]['source_role'] = 'answer'
    case.tasks.finish(CATALOG)
    assert not case.library.outline_calls and not case.tasks.pending
    assert case.dialog._document is None and case.dialog.request is None
    assert case.dialog.source.currentData() is None and not case.dialog.confirm.isEnabled()
    assert '变化' in case.dialog.status.text()


@pytest.mark.parametrize('field', ['source_path', 'source_revision', 'source_role'])
def test_preset_does_not_rebind_changed_outline_field(make_workspace, field):
    case = make_workspace()
    outline = case.library.outlines[case.source['source_id']]
    outline['source'][field] = {'source_path': 'C:/different/lecture.docx',
        'source_revision': 'f' * 64, 'source_role': 'answer'}[field]
    if field == 'source_revision':
        outline['preview']['revision'] = 'f' * 64
    load_workspace(case)
    assert case.library.outline_calls == [case.source]
    assert case.dialog._document is None and case.dialog.request is None
    assert not case.dialog.knowledge_button.isEnabled() and not case.dialog.confirm.isEnabled()
    assert '不一致' in case.dialog.status.text()


def test_unpreset_workspace_keeps_manual_library_choice(make_workspace):
    case = make_workspace(initial=False)
    case.tasks.finish(CATALOG)
    assert case.dialog.source.isEnabled() and case.dialog.source_search.isEnabled()
    assert case.dialog.source.currentData() is None and not case.library.outline_calls
    case.dialog.source.setCurrentIndex(2)
    case.tasks.finish(OUTLINE)
    assert case.dialog._document['source_id'] == case.library.sources[1]['source_id']


def test_cancelled_preset_outline_and_programmatic_foreign_source_cannot_publish(make_workspace):
    case = make_workspace()
    case.tasks.finish(CATALOG)
    pending = case.tasks.take(OUTLINE)
    value = pending.operation()
    case.dialog.stop_reading()
    pending.on_success(value)
    assert case.dialog._document is None and case.dialog.request is None
    case.dialog.source.addItem('同名另一份讲义', deepcopy(case.library.sources[1]))
    case.dialog.source.setCurrentIndex(2)
    assert not case.tasks.pending and case.dialog._document is None
    assert not case.dialog.confirm.isEnabled() and case.dialog.request is None


def test_preset_source_path_mismatch_inside_child_cannot_add_knowledge(make_workspace, monkeypatch):
    case = make_workspace()
    load_workspace(case)
    class Picker(QDialog):
        def __init__(self, facade, parent=None, initial_source=None):
            super().__init__(parent)
            self.requests = [knowledge(case.dialog._outline)]
            self.requests[0]['source']['source_path'] = 'C:/other.docx'
    monkeypatch.setattr(workspace_ui, 'HandoutKnowledgePicker', Picker)
    case.dialog.choose_knowledge()
    case.dialog._child.accept()
    assert not case.dialog._selections and case.dialog.request is None
    assert '不属于当前讲义' in case.dialog.status.text()


@pytest.fixture
def section(app, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as context_backend
    from integrations.deeptutor_shchem_v1 import desktop_section_handout_sources as helper
    cards = {'甲': card('知识甲', 'b'), '乙': card('知识乙', 'c')}
    rows = [{'title': '知识' + key, 'book_key': 'registered:BOOK-1', 'section_filter_key': 'S1',
        'selection': {'note_id': key}} for key in cards]
    context = {'schema_version': 'synthetic', 'status': 'ready', 'resource_id': cards['甲']['id'],
        'book_key': 'registered:BOOK-1', 'book_label': '真实原教材', 'section_filter_key': 'S1',
        'section_label': '原教材真实章节', 'section_choices': [], 'knowledge_options': rows,
        'knowledge_selection': None, 'binding_sha256': 'd' * 64}
    library = _SyntheticLibrary()
    source = deepcopy(library.outlines[library.sources[0]['source_id']]['source'])
    candidate = {'source': source, 'resource_ids': ['course-handout-1', 'course-handout-2'],
        'label': source['source_name'], 'binding_sha256': 'e' * 64}
    guard = {'valid': True}
    calls = []
    def check():
        if not guard['valid']:
            raise ValueError('当前课件已变化，请重新打开。')
    def sources(facade, resources):
        calls.append(('catalog', deepcopy(resources)))
        return {'sources': [deepcopy(candidate)], 'unavailable': [], 'candidate_only': True}
    def recheck(facade, resources, chosen):
        calls.append(('recheck', deepcopy(chosen)))
        if chosen != candidate:
            raise ValueError('本课已用讲义已变化。')
        return True
    monkeypatch.setattr(context_backend, 'recheck_section_material_context', lambda *args: True)
    monkeypatch.setattr(section_ui, 'import_knowledge_note', lambda facade, chosen: deepcopy(cards[chosen['note_id']]))
    monkeypatch.setattr(section_ui, 'knowledge_textbook_options', lambda *args: {'rows': []})
    monkeypatch.setattr(helper, 'course_handout_sources', sources)
    monkeypatch.setattr(helper, 'recheck_course_handout_source', recheck)
    tasks = Tasks()
    dialog = section_ui.SectionMaterialsDialog(SimpleNamespace(), tasks, context, guard=check,
        course_resources=[cards['甲']], image_assets=[], service=Links(cards))
    yield SimpleNamespace(dialog=dialog, tasks=tasks, cards=cards, context=context, library=library,
        candidate=candidate, calls=calls, guard=guard, app=app, monkeypatch=monkeypatch)
    dialog.reject()
    dialog.deleteLater()
    events(app)


def load_section(case, *, choose=True):
    case.dialog.knowledge_list.setCurrentRow(0)
    case.tasks.finish('读取当前知识的讲解与材料')
    case.dialog.tabs.setCurrentWidget(case.dialog.handout_tab)
    case.tasks.finish(COURSE_SOURCES)
    if choose:
        case.dialog.handout_source_list.setCurrentRow(0)


def stub_workspace(case, *, cancel=False, mutation=None, returned=None):
    calls = []
    class Workspace(QDialog):
        def __init__(self, facade, tasks=None, parent=None, *, initial_source=None):
            super().__init__(parent)
            calls.append(deepcopy(initial_source))
            self.confirm = QPushButton(self)
            self.request = deepcopy(returned) if returned is not None else mixed_request(case.candidate['source'])
        def exec(self):
            assert case.dialog._child is self
            assert not case.dialog.knowledge_list.isEnabled() and not case.dialog.section.isEnabled()
            assert case.dialog._selection == {'note_id': '甲'}
            def finish():
                if mutation is not None:
                    mutation(self)
                elif cancel:
                    self.reject()
                else:
                    self.accept()
            QTimer.singleShot(0, finish)
            return super().exec()
    case.monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', Workspace)
    return calls


def test_new_tab_reads_real_course_sources_lazily_without_selecting_a_match(section):
    case = section
    assert case.dialog.tabs.tabText(case.dialog.tabs.indexOf(case.dialog.handout_tab)) == '讲义知识与知识网'
    assert not case.tasks.pending and not case.calls
    load_section(case, choose=False)
    assert case.dialog.handout_source_list.count() == 1
    assert case.dialog.handout_source_list.currentRow() == -1
    assert case.dialog.handout_source_list.item(0).data(Qt.ItemDataRole.UserRole) == case.candidate
    assert not case.dialog.handout_select_button.isEnabled()
    assert case.dialog.browse_handouts_button.isEnabled() and case.dialog.request is None
    assert [call[0] for call in case.calls] == ['catalog']
    case.dialog.tabs.setCurrentIndex(0)
    case.dialog.tabs.setCurrentWidget(case.dialog.handout_tab)
    assert not case.tasks.pending and len(case.calls) == 1


@pytest.mark.parametrize('browse', [False, True])
def test_explicit_workbench_request_keeps_current_knowledge_and_mixed_order(section, browse):
    case = section
    load_section(case)
    calls = stub_workspace(case)
    original = deepcopy(case.dialog._card['original'])
    (case.dialog.browse_handouts if browse else case.dialog.choose_handout_source)()
    case.tasks.finish(OPEN)
    assert case.dialog.request is None
    case.tasks.finish(VERIFY)
    request = case.dialog.request
    assert calls == [None if browse else case.candidate['source']]
    assert request['kind'] == 'handout_materials' and case.dialog.course_result is None
    assert request['course_handout_source'] == (None if browse else case.candidate)
    assert request['material_request'] == mixed_request(case.candidate['source'])
    assert request['knowledge_selection'] == {'note_id': '甲'} and request['context'] == case.context
    assert request['knowledge_original'] == original == request['knowledge_card']['original']
    assert request['material_request']['knowledge_requests'][0]['teacher_only'] is True
    assert case.dialog.result() == QDialog.DialogCode.Accepted


def test_cancel_workbench_produces_no_section_request(section):
    load_section(section)
    stub_workspace(section, cancel=True)
    section.dialog.choose_handout_source()
    section.tasks.finish(OPEN)
    assert not section.tasks.pending and section.dialog.request is None
    assert not section.dialog._closed and section.dialog._child is None
    assert section.dialog.handout_select_button.isEnabled()


@pytest.mark.parametrize('field', ['source_path', 'source_revision', 'source_role'])
def test_workbench_must_return_exact_selected_course_source(section, field):
    load_section(section)
    request = mixed_request(section.candidate['source'])
    request['source'][field] = 'different-source'
    stub_workspace(section, returned=request)
    section.dialog.choose_handout_source()
    section.tasks.finish(OPEN)
    assert not section.tasks.pending and section.dialog.request is None
    assert '不一致' in section.dialog.status.text()


def test_guard_timer_closes_child_and_late_accept_cannot_publish(section):
    load_section(section)
    children = []
    def mutate(child):
        children.append(child)
        section.guard['valid'] = False
        section.dialog._guard_timer.timeout.emit()
        assert child.result() == QDialog.DialogCode.Rejected
        child.accept()  # A queued completion must not revive the rejected parent.
    stub_workspace(section, mutation=mutate)
    section.dialog.choose_handout_source()
    section.tasks.finish(OPEN)
    assert len(children) == 1 and section.dialog._stale
    assert section.dialog.request is None and section.dialog.course_result is None
    assert not section.tasks.pending and not section.dialog.handout_select_button.isEnabled()


def test_guard_failure_before_child_exec_rejects_allocated_dialog(section):
    load_section(section)
    child = QDialog(section.dialog)
    closed = []
    child.rejected.connect(lambda: closed.append(True))
    section.guard['valid'] = False
    with pytest.raises(ValueError, match='课件已变化'):
        section.dialog._exec_child(child)
    assert closed == [True] and section.dialog._child is None
    assert section.dialog.request is None


def test_knowledge_a_b_a_during_child_and_old_callbacks_cannot_publish(section):
    load_section(section)
    def mutate(child):
        section.dialog.knowledge_list.setCurrentRow(1)
        section.dialog.knowledge_list.setCurrentRow(0)
        child.accept()
    stub_workspace(section, mutation=mutate)
    section.dialog.choose_handout_source()
    section.tasks.finish(OPEN)
    old, current = section.tasks.pending[-2:]
    assert old.task_id in section.tasks.cancelled
    old.on_success({'card': section.cards['乙']})
    current.on_success({'card': section.cards['甲'], 'images': {'rows': []},
        'knowledge_context': None, 'snapshot': None, 'links': [], 'image_error': '', 'question_error': ''})
    assert section.dialog.request is None and section.dialog.course_result is None
    assert section.dialog._selection == {'note_id': '甲'}
    assert all(task.label != VERIFY for task in section.tasks.pending)


@pytest.mark.parametrize('change', ['course_source', 'knowledge', 'guard', 'cancel', 'late_callback'])
def test_mutation_or_cancel_after_child_prevents_publish(section, change):
    load_section(section)
    stub_workspace(section)
    section.dialog.choose_handout_source()
    section.tasks.finish(OPEN)
    pending = section.tasks.take(VERIFY)
    if change == 'course_source':
        section.candidate['binding_sha256'] = 'f' * 64
    elif change == 'knowledge':
        section.cards['甲'] = card('改过的知识甲', 'f')
    elif change == 'guard':
        section.guard['valid'] = False
    elif change == 'cancel':
        section.dialog.cancel_operation()
    else:
        pending.operation()
        section.dialog.knowledge_list.setCurrentRow(1)
        section.dialog.knowledge_list.setCurrentRow(0)
        pending.on_success(None)
        assert section.dialog.request is None
        return
    try:
        value = pending.operation()
    except ValueError as exc:
        pending.on_failure(str(exc))
    else:
        pending.on_success(value)
    assert section.dialog.request is None and section.dialog.course_result is None
    assert section.dialog.result() != QDialog.DialogCode.Accepted


def test_stale_catalog_result_does_not_reselect_after_knowledge_switch(section):
    case = section
    case.dialog.knowledge_list.setCurrentRow(0)
    case.tasks.finish('读取当前知识的讲解与材料')
    case.dialog.tabs.setCurrentWidget(case.dialog.handout_tab)
    old = case.tasks.take(COURSE_SOURCES)
    result = old.operation()
    case.dialog.knowledge_list.setCurrentRow(1)
    old.on_success(result)
    assert old.task_id in case.tasks.cancelled
    assert case.dialog.handout_source_list.count() == 0 and case.dialog.request is None


def test_real_workspace_returns_full_sections_original_images_and_question_order(section, monkeypatch):
    case = section
    load_section(case)
    monkeypatch.setattr(workspace_ui, 'handout_knowledge_sources', case.library.catalog)
    monkeypatch.setattr(workspace_ui, 'handout_knowledge_outline', case.library.outline)
    monkeypatch.setattr(workspace_ui, 'handout_source_context',
        lambda facade, bound: {'document_source': deepcopy(bound)})
    real_class = workspace_ui.HandoutMaterialWorkspace
    seen = []
    class RealWorkspace(real_class):
        def exec(self):
            def choose():
                case.tasks.finish(CATALOG)
                case.tasks.finish(OUTLINE)
                assert self._document == case.candidate['source']
                self._add_selections('knowledge', [knowledge(self._outline, teacher_only=True)])
                self._add_selections('image', [{'source': deepcopy(next(iter(self._picture_rows.values()))),
                    'title': '完整知识结构网', 'purpose': '联系全部概念', 'teacher_only': False}])
                self._add_selections('question', [{'key': 'Q1', 'revision': 'q1', 'points': 2,
                    'source_sha256': self._document['source_sha256'],
                    'scoped_source_document': deepcopy(self._document)}])
                seen.append((deepcopy(self.reader._source), deepcopy(self._outline)))
                self.confirm.click()
                case.tasks.finish(CONFIRM)
            QTimer.singleShot(0, choose)
            return super().exec()
    monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', RealWorkspace)
    case.dialog.choose_handout_source()
    case.tasks.finish(OPEN)
    case.tasks.finish(VERIFY)
    request = case.dialog.request['material_request']
    assert request['source'] == case.candidate['source']
    assert request['selection_order'] == [{'kind': 'knowledge', 'index': 0},
        {'kind': 'image', 'index': 0}, {'kind': 'question', 'index': 0}]
    assert request['knowledge_requests'][0]['section'] == seen[0][1]['sections'][0]
    assert request['image_selections'][0]['source']['source_revision'] == request['source']['source_revision']
    assert seen[0][0] == case.library.outlines[request['source']['source_id']]['preview']


@pytest.mark.parametrize('width', [420, 1160])
def test_section_new_tab_and_preset_workspace_fit_actual_font(section, make_workspace, width):
    load_section(section)
    dialog = section.dialog
    dialog.show()
    dialog.resize(width, 820)
    events(section.app)
    assert dialog.width() == width
    assert dialog.tab_choice.isVisible() == (width < 620)
    assert dialog.tabs.tabBar().isVisible() == (width >= 620)
    assert dialog.tab_choice.currentText() == '讲义知识与知识网'
    assert dialog.handout_source_list.width() > 250
    for button in (dialog.handout_select_button, dialog.browse_handouts_button,
                   dialog.reload_handouts_button, dialog.close_button):
        point = button.mapTo(dialog, QPoint(0, 0))
        assert 0 <= point.x() and point.x() + button.width() <= width
        assert 0 <= point.y() and point.y() + button.height() <= dialog.height()
        assert button.width() >= button.sizeHint().width()
    case = make_workspace()
    load_workspace(case)
    case.dialog.show()
    case.dialog.resize(width, 820)
    events(case.app)
    assert case.dialog.width() == width
    assert case.dialog.content_scroll.horizontalScrollBar().maximum() == 0
    for button in (case.dialog.confirm, case.dialog.stop_button, case.dialog.cancel_button):
        point = button.mapTo(case.dialog, QPoint(0, 0))
        assert 0 <= point.x() and point.x() + button.width() <= width
        assert 0 <= point.y() and point.y() + button.height() <= case.dialog.height()
