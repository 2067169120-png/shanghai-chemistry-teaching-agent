"""Real section/Workspace route and isolated modal lifetime boundaries."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton
from test_source_first_word_workspace_ui import sources, qt_app, events, question_boundary
from test_desktop_library_ui import _ManualBridge
from test_section_materials_dialog_ui import app, setup, select
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as ui
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as workspace_ui
from integrations.deeptutor_shchem_v1.desktop_workbench import word_question_dialog as word_ui
from integrations.deeptutor_shchem_v1.desktop_handout_knowledge import handout_knowledge_sources, handout_knowledge_outline
from integrations.deeptutor_shchem_v1.desktop_handout_questions import HandoutQuestionFacade

OPEN = '读取本节的讲义选题范围'
VERIFY = '核对本课范围与所选讲义完整例题'
ALL = '核对本课范围并打开全部题库'


def finish(tasks, label):
    task = tasks.take(label)
    try: value = task.operation()
    except (ValueError, OSError) as exc: task.on_failure(str(exc))
    else: task.on_success(value)
    return task


@pytest.fixture
def section(qt_app, sources, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as backend
    context = {'schema_version': 'OWN-context', 'status': 'ready', 'entry_kind': 'textbook_section',
        'resource_id': None, 'book_key': 'registered:OWN-book', 'book_label': '明确教材',
        'section_filter_key': 'S1', 'section_label': '明确第一节', 'section_choices': [],
        'knowledge_options': [], 'knowledge_selection': None, 'binding_sha256': 'b' * 64,
        'source_binding': {'source_sha256': 'c' * 64, 'pdf_pages': [2, 3]}}
    current = deepcopy(context)
    browser = {'schema_version': 'OWN-browser', 'original_title': context['section_label'],
        'curriculum_context': {'book_key': context['book_key'], 'section_key': 'S1'},
        'search_query': '保留检索范围', 'binding_sha256': 'a' * 64,
        'section_context': deepcopy(context), 'section_binding_sha256': context['binding_sha256']}
    checks = []
    def context_check(facade, value):
        checks.append(('context', deepcopy(value)))
        if value != current: raise ValueError('完整教材节来源已变化')
        return deepcopy(current)
    def browser_check(facade, value, chosen):
        context_check(facade, value)
        checks.append(('browser', deepcopy(chosen)))
        if chosen != browser: raise ValueError('完整选题范围已变化')
        return deepcopy(browser)
    monkeypatch.setattr(backend, 'recheck_textbook_section_material_context', context_check)
    monkeypatch.setattr(ui, 'section_textbook_options', lambda f, c: {'rows': [], 'binding': 'd'*64,
        'notices': [], 'section_binding_sha256': c['binding_sha256'], 'candidate_only': True})
    monkeypatch.setattr(ui, 'section_question_context', lambda f, c: deepcopy(browser))
    monkeypatch.setattr(ui, 'recheck_section_question_context', browser_check)
    tasks = _ManualBridge(); target = {'changed': False}
    def guard():
        if target['changed']: raise ValueError('原备课目标已变化')
    dialog = ui.SectionMaterialsDialog(sources.facade, tasks, context, guard=guard,
        course_resources=[], image_assets=[], batch_mode=True)
    finish(tasks, '读取本节原图与讲义选题范围')
    dialog.show(); events(qt_app)
    yield SimpleNamespace(dialog=dialog, tasks=tasks, source=sources, context=context,
        current=current, browser=browser, checks=checks, target=target, app=qt_app,
        monkeypatch=monkeypatch)
    dialog.reject(); dialog.deleteLater(); events(qt_app)


def material(sources):
    metadata = handout_knowledge_sources(sources.facade)['sources'][0]
    source = handout_knowledge_outline(sources.facade, metadata)['source']
    row = HandoutQuestionFacade(sources.facade, source).word_question_catalog()['items'][0]
    selection = {'key': row['key'], 'revision': row['revision'], 'points': 3,
                 'source_sha256': source['source_sha256'], 'scoped_source_document': deepcopy(source),
                 'title': '完整例题问题标题'}
    return {'schema': workspace_ui.SCHEMA, 'source': source, 'knowledge_requests': [],
        'image_selections': [], 'question_selections': [selection],
        'selection_order': [{'kind': 'question', 'index': 0}]}


def workspace_boundary(case, *, all_library=False, cancel=False, mutate=None, request=None):
    calls = []
    class Workspace(QDialog):
        def __init__(self, facade, tasks, parent, **options):
            super().__init__(parent); calls.append(deepcopy(options))
            self.confirm = QPushButton()
            self.request = deepcopy(request)
            self.all_question_library_requested = all_library
        def exec(self):
            if mutate: mutate()
            return self.DialogCode.Rejected if cancel else self.DialogCode.Accepted
    case.monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', Workspace)
    return calls


def global_boundary(case, *, cancel=False, omit_images=False):
    calls = []
    selection = {'key': 'GLOBAL-Q', 'revision': 'global-rev', 'points': 3}
    reference = {'selections': [selection], 'include_images': not omit_images,
                 'text': '完整题、原图、答案与解析', 'title': '全部题库明确选题'}
    class Global(QDialog):
        def __init__(self, facade, tasks, parent, **options):
            super().__init__(parent); calls.append((facade, deepcopy(options)))
            self.include_images = QCheckBox(); self.import_button = QPushButton()
            self.reference = deepcopy(reference); self.selections = [deepcopy(selection)]
        def exec(self):
            assert self.include_images.isChecked() and not self.include_images.isEnabled()
            return self.DialogCode.Rejected if cancel else self.DialogCode.Accepted
    case.monkeypatch.setattr(word_ui, 'WordQuestionDialog', Global)
    return calls, reference


def test_real_metadata_source_scoped_question_child_enters_same_ordered_section_queue(section):
    case = section; seen = []
    original = workspace_ui.HandoutMaterialWorkspace
    class Workspace(original):
        def exec(self):
            failures = []
            def choose():
                try:
                    assert self._questions_only and self._question_context == case.browser
                    finish(case.tasks, '读取讲义选材资料目录')
                    assert self.source.currentData() is None and not case.source.reads and not case.source.restores
                    self.source.setCurrentIndex(2)  # Explicit second same-named archive, not row zero.
                    finish(case.tasks, '读取当前讲义选材原文')
                    childcase = SimpleNamespace(dialog=self, tasks=case.tasks, app=case.app)
                    opened = question_boundary(childcase, case.monkeypatch)
                    seen.extend(opened)
                    self._child.accept(); events(case.app)
                    self.confirm.click(); finish(case.tasks, '核对本次讲义选材来源')
                except BaseException as exc:
                    failures.append(exc); self.reject()
            QTimer.singleShot(0, choose)
            result = super().exec()
            if failures: raise failures[0]
            return result
    case.monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', Workspace)
    case.dialog.browse_questions(); finish(case.tasks, OPEN)
    assert not case.dialog._queued_requests
    finish(case.tasks, VERIFY)
    assert len(case.dialog._queued_requests) == 1 and len(seen) == 1
    request = case.dialog._queued_requests[0]
    assert request['context'] == case.context and request['selection_scope'] == 'section'
    assert not {'knowledge_selection', 'knowledge_card', 'knowledge_original'} & set(request)
    assert request['kind'] == 'handout_materials' and request['course_handout_source'] is None
    document = request['material_request']['source']
    assert document['source_id'] == 'OWN-archive-1' and len(document) == 8
    assert request['material_request']['question_selections'][0]['scoped_source_document'] == document
    assert all(row == {'OWN-archive-1'} for row in case.source.restores)
    case.dialog.queue_confirm.click()
    assert case.dialog.requests == [request]


@pytest.mark.parametrize('cancel', [False, True])
def test_explicit_all_is_only_global_route_and_keeps_full_browser_context(section, cancel):
    case = section
    source_calls = workspace_boundary(case, all_library=True)
    global_calls, reference = global_boundary(case, cancel=cancel)
    case.dialog.browse_questions(); finish(case.tasks, OPEN)
    assert source_calls == [{'questions_only': True, 'question_context': case.browser}]
    assert not global_calls and not case.source.restores and not case.source.reads
    finish(case.tasks, ALL)
    assert global_calls[0][0] is case.source.facade
    options = global_calls[0][1]
    assert options['initial_curriculum_context'] == case.browser['curriculum_context']
    assert options['initial_search_query'] == case.browser['search_query']
    assert options['persist_selection'] is False and options['allow_knowledge_links'] is False
    if cancel:
        assert not case.tasks.pending and not case.dialog._queued_requests
    else:
        finish(case.tasks, '核对本节与所选完整例题')
        request = case.dialog._queued_requests[0]
        assert request['kind'] == 'questions' and request['reference'] == reference
        assert request['question_context'] == case.browser and request['selection_scope'] == 'section'


def test_workspace_cancel_neither_reads_global_nor_changes_queue(section):
    case = section; calls = workspace_boundary(case, cancel=True)
    def forbidden(*args, **kwargs): raise AssertionError('Cancelled source choice must not open global catalogue')
    case.monkeypatch.setattr(word_ui, 'WordQuestionDialog', forbidden)
    case.dialog.browse_questions(); finish(case.tasks, OPEN)
    assert calls and not case.tasks.pending and not case.dialog._queued_requests
    assert not case.source.reads and not case.source.restores


@pytest.mark.parametrize('change', ['context', 'source', 'parent', 'cancel', 'close', 'aba'])
def test_late_source_handoff_rejects_context_source_parent_cancel_close_and_aba(section, change):
    case = section; chosen = material(case.source)
    workspace_boundary(case, request=chosen)
    case.dialog.browse_questions(); finish(case.tasks, OPEN)
    late = case.tasks.take(VERIFY)
    value = late.operation()
    if change == 'context':
        case.current['source_binding']['pdf_pages'] = [4]
        # Force an honest re-read; sealed success alone cannot mask a source change.
        try: late.operation()
        except ValueError as exc: late.on_failure(str(exc))
        else: pytest.fail('Changed native context must reject')
        return
    if change == 'source':
        case.source.stored['OWN-archive-0'].role = 'answer'
        try: late.operation()
        except ValueError as exc: late.on_failure(str(exc))
        else: pytest.fail('Changed native source must reject')
        assert not case.dialog._queued_requests
        return
    if change == 'parent': case.target['changed'] = True
    elif change == 'cancel': case.dialog.cancel_operation()
    elif change == 'close': case.dialog.reject()
    else:
        case.dialog.context['section_filter_key'] = 'S2'; case.dialog._invalidate()
        case.dialog.context = deepcopy(case.context); case.dialog._show_context()
    late.on_success(value)
    assert not case.dialog._queued_requests and case.dialog.requests == []


def test_child_context_aba_is_rejected_before_all_fallback(section):
    case = section; calls, _ = global_boundary(case)
    def aba():
        case.dialog.context['section_filter_key'] = 'S2'; case.dialog._invalidate()
        case.dialog.context = deepcopy(case.context); case.dialog._show_context()
    workspace_boundary(case, all_library=True, mutate=aba)
    case.dialog.browse_questions(); finish(case.tasks, OPEN)
    assert not calls and not case.dialog._queued_requests
    assert not any(task.label == ALL for task in case.tasks.pending)


def test_explicit_knowledge_scope_preserves_true_card_and_source_only_envelope(setup, sources):
    case = setup; select(case)
    case.dialog.facade = sources.facade
    chosen = material(sources)
    captured = []
    class Workspace(QDialog):
        def __init__(self, facade, tasks, parent, **options):
            super().__init__(parent); captured.append(deepcopy(options))
            self.confirm = QPushButton(); self.request = deepcopy(chosen)
            self.all_question_library_requested = False
        def exec(self): return self.DialogCode.Accepted
    case.monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', Workspace)
    case.dialog.browse_questions(); case.tasks.finish(); case.tasks.finish()
    request = case.dialog.request
    assert request['selection_scope'] == 'knowledge'
    assert request['knowledge_selection'] == {'note_id': '甲'}
    assert request['knowledge_original'] == case.cards['甲']['original']
    assert request['material_request'] == chosen and request['course_handout_source'] is None
    assert captured[0]['question_context'] == case.links.knowledge_context(case.cards['甲'])['browser_context']


def test_all_fallback_discards_unconfirmed_source_specific_selection(section):
    case = section; draft = material(case.source)
    workspace_boundary(case, all_library=True, request=draft)
    calls, reference = global_boundary(case)
    case.dialog.browse_questions(); finish(case.tasks, OPEN); finish(case.tasks, ALL)
    assert not {'required_source_id', 'initial_source_id', 'initial_question_key', 'selection_only'} & set(calls[0][1])
    finish(case.tasks, '核对本节与所选完整例题')
    request = case.dialog._queued_requests[0]
    assert request['kind'] == 'questions' and 'material_request' not in request
    assert request['reference'] == reference and request['question_selections'][0]['key'] == 'GLOBAL-Q'


def test_knowledge_browser_context_change_rejects_before_queue(setup, sources):
    case = setup; select(case); case.dialog.facade = sources.facade
    chosen = material(sources)
    current = deepcopy(case.dialog._knowledge_context['browser_context'])
    def checked(facade, card, browser):
        if browser != current: raise ValueError('当前知识选题范围已变化')
        assert card['original'] == case.cards['甲']['original']
        return deepcopy(current)
    case.monkeypatch.setattr(ui, 'recheck_knowledge_question_context', checked)
    class Workspace(QDialog):
        def __init__(self, facade, tasks, parent, **options):
            super().__init__(parent); self.confirm = QPushButton()
            self.request = deepcopy(chosen); self.all_question_library_requested = False
        def exec(self):
            current['curriculum_context']['section_key'] = 'S2'
            return self.DialogCode.Accepted
    case.monkeypatch.setattr(workspace_ui, 'HandoutMaterialWorkspace', Workspace)
    case.dialog.browse_questions(); case.tasks.finish(); case.tasks.finish()
    assert case.dialog.request is None and not case.dialog._queued_requests
    assert '范围已变化' in case.dialog.status.text()
