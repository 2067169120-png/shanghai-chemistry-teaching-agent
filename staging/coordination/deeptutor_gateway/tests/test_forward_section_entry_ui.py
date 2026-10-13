"""Section-led reading and ordered selection without application constructors."""
import os
from copy import deepcopy
from threading import Event, Thread
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QWidget

from test_section_materials_dialog_ui import app, setup, select, Tasks
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as ui
from integrations.deeptutor_shchem_v1.desktop_workbench import forward_section_entry as entry


def forward_dialog(s, *, guard=lambda: None):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as backend
    context = {**deepcopy(s.context), 'entry_kind': 'textbook_section', 'resource_id': None}
    context['section_selection'] = {'mode': 'registered', 'section_key': 'S1', 'revision': 'synthetic-source-revision'}
    s.monkeypatch.setattr(backend, 'recheck_textbook_section_material_context', lambda *args: True)
    s.monkeypatch.setattr(ui, 'section_textbook_options', lambda facade, current: {
        'binding': 'e' * 64, 'section_binding_sha256': current['binding_sha256'],
        'rows': [{'selection': {'visual_asset_id': 'S1-PAGE', 'revision': 'synthetic-page-revision'},
                  'label': '真实第一节完整原页', 'caption': '真实第一节完整原页'}],
        'notices': [], 'candidate_only': True})
    s.monkeypatch.setattr(ui, 'section_question_context', lambda facade, current: {
        'entry_kind': 'textbook_section', 'original_title': current['section_label'],
        'curriculum_context': {'section_key': 'S1'}, 'search_query': '',
        'section_context': deepcopy(current), 'section_binding_sha256': current['binding_sha256'],
        'binding_sha256': 'f' * 64})
    return ui.SectionMaterialsDialog(s.dialog.facade, s.tasks, context,
        guard=guard, course_resources=[], image_assets=[], service=s.links, batch_mode=True)


def add_knowledge(s, dialog, index):
    dialog.knowledge_list.setCurrentRow(index)
    s.tasks.finish()
    dialog.choose_knowledge()
    s.tasks.finish()


def test_empty_course_direct_context_does_not_choose_or_insert_first_knowledge(setup):
    dialog = forward_dialog(setup)
    try:
        assert dialog.source_item is None and dialog.course_resources == []
        dialog.show()
        setup.app.processEvents()
        assert dialog.knowledge_list.count() == 2
        assert dialog.knowledge_list.currentRow() == -1
        assert dialog.requests == [] and dialog._queued_requests == []
        assert not dialog.queue_confirm.isEnabled()
    finally:
        dialog.reject()


def test_continuous_selection_keeps_full_sources_and_order_until_single_confirm(setup):
    s = setup
    dialog = forward_dialog(s)
    try:
        add_knowledge(s, dialog, 0)
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.requests == [] and len(dialog._queued_requests) == 1
        first = deepcopy(dialog._queued_requests[0])
        assert first['knowledge_original'] == s.cards['甲']['original']
        assert first['knowledge_card']['blocks'][0]['value'].endswith('最后一段知识解释')
        assert first['knowledge_card']['blocks'][1]['value'] == ''
        add_knowledge(s, dialog, 1)
        dialog._move_queue(-1)
        assert [x['knowledge_original']['title'] for x in dialog._queued_requests] == ['知识乙', '知识甲']
        dialog._confirm_queue()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert [x['knowledge_original']['title'] for x in dialog.requests] == ['知识乙', '知识甲']
        dialog._queued_requests[0]['knowledge_card']['title'] = 'late mutation'
        assert dialog.requests[0]['knowledge_card']['title'] != 'late mutation'
        assert s.cards['甲']['original'] == first['knowledge_original']
    finally:
        dialog.reject()


def test_queue_remove_and_return_never_return_a_partial_batch(setup):
    dialog = forward_dialog(setup)
    add_knowledge(setup, dialog, 0)
    add_knowledge(setup, dialog, 1)
    dialog._remove_queue()
    assert len(dialog._queued_requests) == 1
    dialog.reject()
    assert dialog.requests == [] and dialog._queued_requests == []


def test_revocation_disables_confirm_even_with_completed_selection(setup):
    revoked = Event()
    def guard():
        if revoked.is_set():
            raise ValueError('目标变化')
    dialog = forward_dialog(setup, guard=guard)
    add_knowledge(setup, dialog, 0)
    revoked.set()
    dialog._guard_current()
    assert not dialog.queue_confirm.isEnabled()
    dialog._confirm_queue()
    assert dialog.requests == []
    dialog.reject()


def test_browsed_complete_questions_can_be_arranged_individually(setup):
    dialog = forward_dialog(setup)
    dialog.knowledge_list.setCurrentRow(0)
    setup.tasks.finish()
    selections = [{'key': 'Q1', 'revision': 'one'}, {'key': 'Q2', 'revision': 'two'}]
    references = [{'selections': [chosen], 'include_images': True, 'text': chosen['key'] + '完整原题'}
                  for chosen in selections]
    dialog._finish('questions', None, question_mode='browse', question_context={},
        question_selections=selections, reference={'selections': selections, 'include_images': True},
        _single_references=references)
    assert len(dialog._queued_requests) == 2
    dialog._move_queue(-1)
    assert dialog._queued_requests[0]['reference'] == references[1]
    assert dialog._queued_requests[0]['question_selections'] == [selections[1]]
    assert '_single_references' not in dialog._queued_requests[0]
    dialog.reject()


class Page(QWidget):
    preparation_target_changed = Signal()

    def __init__(self):
        super().__init__()
        self.tasks = Tasks()
        self.status = QLabel(self)
        self.topic = QLineEdit('原课', self)
        self.facade = SimpleNamespace()
        self._lesson_design = None
        self._editor_draft_id = 'original-draft'
        self._source_material_insertion = None
        self.image_assets_widget = SimpleNamespace(assets=lambda: [])
        self.position = 0
        self.imports = []

    def studio_busy(self):
        return bool(getattr(self, '_section_materials_job', None))

    def _payload(self):
        return {'topic': self.topic.text()}

    def freeze_material_insertion(self):
        return {'epoch': self.position}

    def check_material_insertion(self, value, **kwargs):
        if value['epoch'] != self.position:
            raise ValueError('加入位置变化')

    def _import_material_batch(self, request, prepare, label):
        self.imports.append((deepcopy(request), prepare))
        return True


@pytest.fixture
def direct(app, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as backend
    page = Page()
    selection = {'book_key': 'registered:BOOK', 'section_filter_key': 'S1',
                 'selection': {'mode': 'registered', 'section_key': 'S1', 'revision': 'rev'}}
    context = {**deepcopy(selection), 'entry_kind': 'textbook_section', 'resource_id': None}
    calls = []
    def resolve(facade, *args):
        calls.append(args)
        return deepcopy(context)
    monkeypatch.setattr(backend, 'textbook_section_material_context', resolve)
    class Picker:
        requests = []
        outcome = QDialog.DialogCode.Rejected
        def __init__(self, *args, **kwargs):
            assert kwargs['course_resources'] == [] and kwargs['batch_mode'] is True
        def exec(self): return self.outcome
        def deleteLater(self): pass
    monkeypatch.setattr(ui, 'SectionMaterialsDialog', Picker)
    yield SimpleNamespace(page=page, selection=selection, context=context, picker=Picker, calls=calls)
    page.deleteLater()


def test_form_aba_revokes_source_read_before_any_initializer_or_import(direct):
    s = direct
    assert entry.open_textbook_section_materials(s.page, s.selection)
    s.page.topic.setText('另一课')
    s.page.topic.setText('原课')
    s.page.tasks.finish()
    assert s.page.imports == [] and s.page._lesson_design is None
    assert not s.page.studio_busy() and s.calls == []


def test_worker_resolve_has_no_gui_reads_and_cancel_keeps_empty_course(direct):
    s = direct
    assert entry.open_textbook_section_materials(s.page, s.selection)
    operation, success, failure, _ = s.page.tasks.pending[-1]
    results = []
    def work(): results.append(operation())
    thread = Thread(target=work)
    thread.start(); thread.join()
    assert results == [s.context]
    success(results[0])
    assert s.page.imports == [] and s.page._lesson_design is None
    assert not s.page.studio_busy()


def test_selected_batch_uses_existing_transaction_once_and_original_position(direct):
    s = direct
    handoffs = []
    s.picker.outcome = QDialog.DialogCode.Accepted
    s.picker.requests = [{'kind': 'knowledge', 'context': deepcopy(s.context)}]
    assert entry.open_textbook_section_materials(s.page, s.selection, on_handoff=lambda: handoffs.append(True))
    s.page.tasks.finish()
    assert len(s.page.imports) == 1 and handoffs == [True]
    request, _ = s.page.imports[0]
    assert request['requests'] == s.picker.requests
    assert s.page._source_material_insertion == {'epoch': 0}
    assert s.page._lesson_design is None and not s.page.studio_busy()


def test_position_change_after_source_read_prevents_dialog_handoff(direct):
    s = direct
    assert entry.open_textbook_section_materials(s.page, s.selection)
    operation, success, _, _ = s.page.tasks.pending[-1]
    value = operation()
    s.page.position += 1
    success(value)
    assert s.page.imports == [] and s.page._lesson_design is None
    assert not s.page.studio_busy()
