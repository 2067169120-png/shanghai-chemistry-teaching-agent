"""Real direct book/section helpers and native crop UI with no knowledge card.

No MainWindow, Facade, StateStore, Recovery or Office is constructed. A's
source fixtures create only synthetic books in pytest's owned tmp directory.
Word/lecture modal boundaries are controlled; their production readers retain
their own existing tests. These assertions do not grant source authority.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QPushButton

from test_section_material_sources import (
    empty_registered, empty_supplement, empty_personal,
    section_fixture, asset_fixture, shelf_fixture, supplement_fixture,
)
from test_section_materials_dialog_ui import app
from test_section_handout_bridge_ui import mixed_request
from test_handout_knowledge_picker_ui import _SyntheticLibrary
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as ui
from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_preparation_crop_dialog as crop_ui
from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as handout_ui
from integrations.deeptutor_shchem_v1.desktop_workbench import word_question_dialog as word_ui
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


SECTION_READ = '读取本节原图与讲义选题范围'
COURSE_READ = '读取本课已用讲义来源'
OPEN_HANDOUT = '打开本课讲义选材'
VERIFY_HANDOUT = '核对本节与讲义选材'
OPEN_WORD = '读取本节的讲义选题范围'
OPEN_ALL_WORD = '核对本课范围并打开全部题库'
VERIFY_WORD = '核对本节与所选完整例题'
VERIFY_CROP = '核对教材裁片并准备加入本课'
FORBIDDEN_KNOWLEDGE = {'knowledge_selection', 'knowledge_card', 'knowledge_original'}


class Tasks:
    is_shutting_down = False
    def __init__(self):
        self.pending, self.cancelled, self.serial = [], [], 0
    def submit(self, title, operation, *, on_success, on_failure):
        self.serial += 1
        task = SimpleNamespace(title=title, operation=operation, success=on_success,
                               failure=on_failure, identity=f'own-section-ui-{self.serial}')
        self.pending.append(task)
        return task.identity
    def cancel(self, identity):
        self.cancelled.append(identity)
    def take(self, title):
        for index, task in enumerate(self.pending):
            if task.title == title:
                return self.pending.pop(index)
        raise AssertionError(f'No pending UI operation: {title}')
    def finish(self, title):
        task = self.take(title)
        try:
            value = task.operation()
        except (ValueError, OSError) as exc:
            task.failure(str(exc))
        else:
            task.success(value)
        return task


def events(application):
    for _ in range(5):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()


@pytest.fixture
def make_direct(app, monkeypatch, request):
    dialogs = []
    def make(mode='personal'):
        source = request.getfixturevalue('empty_' + mode)
        guard = {'revoked': False, 'calls': 0}
        def check():
            guard['calls'] += 1
            if guard['revoked']:
                raise ValueError('原备课目标已发生变化。')
        def no_card(*_args):
            raise AssertionError('Direct section actions must not import a knowledge card')
        monkeypatch.setattr(ui, 'import_knowledge_note', no_card)
        tasks = Tasks()
        dialog = ui.SectionMaterialsDialog(source.facade, tasks, source.context, guard=check,
            course_resources=[], image_assets=[], batch_mode=True)
        dialogs.append(dialog)
        return SimpleNamespace(dialog=dialog, tasks=tasks, source=source, guard=guard,
                               app=app, monkeypatch=monkeypatch)
    yield make
    for dialog in dialogs:
        dialog.reject()
        dialog.deleteLater()
    events(app)


def ready(case):
    case.tasks.finish(SECTION_READ)
    assert case.dialog._section_loaded
    assert case.dialog._card is None and case.dialog._selection is None


def assert_section_request(request, context):
    assert request['selection_scope'] == 'section' and request['context'] == context
    assert not FORBIDDEN_KNOWLEDGE.intersection(request)


@pytest.mark.parametrize('mode', ['registered', 'supplement', 'personal'])
def test_real_zero_card_section_initial_read_keeps_scope_and_requires_explicit_material(make_direct, mode):
    case = make_direct(mode)
    before = deepcopy(case.source.context)
    assert case.dialog.knowledge_list.count() == 0 and case.dialog.knowledge_list.currentRow() == -1
    assert not case.dialog.add_knowledge.isEnabled() and not case.dialog.linked_question_button.isEnabled()
    assert case.dialog.request is None and not case.dialog._queued_requests
    ready(case)
    case.dialog.show()
    events(case.app)
    assert case.dialog.knowledge_list.currentRow() == -1 and case.dialog.image_list.currentRow() == -1
    assert not case.dialog.crop_button.isEnabled()
    assert case.dialog.browse_handouts_button.isEnabled() and case.dialog.browse_questions_button.isEnabled()
    assert case.dialog._section_images['section_binding_sha256'] == before['binding_sha256']
    pages = [row['pdf_page'] for row in case.dialog._section_images['rows']
             if row['origin'] in {'section_textbook_page', 'personal_textbook_page'}]
    assert pages == before['source_binding']['pdf_pages']
    browser = case.dialog._section_question_context
    assert browser['section_context'] == before
    if mode == 'supplement':
        assert browser['curriculum_context'] is None and browser['search_query'] == before['section_label']
    assert case.source.context == before and case.dialog.requests == []


def real_crop(case):
    original = crop_ui.TextbookPreparationCropDialog
    observations = []
    class Crop(original):
        def exec(self):
            errors = []
            def choose():
                try:
                    case.tasks.finish('读取教材框选原页')
                    assert self._page and not self.confirm.isEnabled(), self.status.text()
                    self._canvas_changed((3, 3, self._page['width'] - 3, self._page['height'] - 3))
                    self.caption.setText('本节原图：明确选定的图题')
                    self.purpose.setText('观察完整图形和全部条件')
                    self.preview_button.click()
                    case.tasks.finish('核对教材裁片')
                    assert self._preview and not self.confirm.isEnabled(), self.status.text()
                    self.checked.setChecked(True)
                    observations.append(deepcopy(self._current_request()))
                    self.confirm.click()
                except BaseException as exc:
                    errors.append(exc)
                    self.reject()
            QTimer.singleShot(0, choose)
            result = super().exec()
            if errors:
                raise errors[0]
            return result
    case.monkeypatch.setattr(crop_ui, 'TextbookPreparationCropDialog', Crop)
    return observations


@pytest.mark.parametrize('mode', ['registered', 'supplement', 'personal'])
def test_legal_section_original_page_uses_real_crop_preview_and_section_queue(make_direct, mode):
    case = make_direct(mode)
    ready(case)
    row_index = next(index for index in range(case.dialog.image_list.count())
                     if case.dialog.image_list.item(index).data(Qt.ItemDataRole.UserRole)['origin']
                     in {'section_textbook_page', 'personal_textbook_page'})
    selected = deepcopy(case.dialog.image_list.item(row_index).data(Qt.ItemDataRole.UserRole))
    seen = real_crop(case)
    case.dialog.image_list.setCurrentRow(row_index)
    assert case.dialog.crop_button.isEnabled()
    case.dialog.choose_image()
    case.tasks.finish(VERIFY_CROP)
    assert len(seen) == 1 and seen[0]['selection'] == selected['selection']
    assert seen[0]['selection_confirmed'] is True
    assert case.dialog.course_result is None and case.dialog.request is None
    assert len(case.dialog._queued_requests) == 1
    request = case.dialog._queued_requests[0]
    assert_section_request(request, case.source.context)
    assert request['crop_binding'] == case.dialog._section_images['binding']
    assert request['crop_requests'] == seen
    assert '明确选定的图题' in case.dialog.queue_list.item(0).text()
    case.dialog.queue_confirm.click()
    assert case.dialog.result() == QDialog.DialogCode.Accepted
    assert case.dialog.requests == [request]


def word_reader(case, *, cancel=False, mutate=None):
    calls = []
    reference = {'title': '完整原例题', 'source_name': '合成完整讲义.docx',
                 'selections': [{'key': 'SYNTHETIC-Q1', 'revision': 'full-revision', 'points': 2}],
                 'include_images': True, 'text': '完整公共材料、全部小问、原图与教师答案。'}
    class Word(QDialog):
        def __init__(self, facade, tasks, parent, **options):
            super().__init__(parent)
            calls.append(deepcopy(options))
            self.import_button, self.include_images = QPushButton(self), QCheckBox(self)
            self.reference, self.selections = deepcopy(reference), deepcopy(reference['selections'])
        def exec(self):
            assert self.include_images.isChecked() and not self.include_images.isEnabled()
            assert case.dialog._child is self
            def choose():
                if mutate is not None:
                    mutate(self)
                elif cancel:
                    self.reject()
                else:
                    self.accept()
            QTimer.singleShot(0, choose)
            return super().exec()
    class SourceChoice(QDialog):
        def __init__(self, facade, tasks, parent, *, questions_only=False, question_context=None):
            super().__init__(parent)
            assert questions_only is True
            assert question_context == case.dialog._section_question_context
            self.confirm = QPushButton(self)
            self.all_questions_button = QPushButton('浏览全部 Word 题库…', self)
            self.request = None
            self.all_question_library_requested = False
            self.all_questions_button.clicked.connect(self.choose_all)
        def choose_all(self):
            self.all_question_library_requested = True
            self.accept()
        def exec(self):
            assert case.dialog._child is self and self.request is None
            assert calls == []  # No implicit catalogue opens before the explicit choice.
            QTimer.singleShot(0, self.all_questions_button.click)
            result = super().exec()
            assert self.all_question_library_requested and self.request is None
            return result
    case.monkeypatch.setattr(handout_ui, 'HandoutMaterialWorkspace', SourceChoice)
    case.monkeypatch.setattr(word_ui, 'WordQuestionDialog', Word)
    return calls, reference


@pytest.mark.parametrize('mode', ['registered', 'supplement', 'personal'])
def test_no_card_word_browse_keeps_exact_real_section_browser_and_full_reference(make_direct, mode):
    case = make_direct(mode)
    ready(case)
    browser = deepcopy(case.dialog._section_question_context)
    calls, reference = word_reader(case)
    case.dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    case.tasks.finish(VERIFY_WORD)
    assert calls[0]['initial_curriculum_context'] == browser['curriculum_context']
    assert calls[0]['initial_search_query'] == browser['search_query']
    assert calls[0]['persist_selection'] is False and calls[0]['allow_knowledge_links'] is False
    assert calls[0]['handoff_mode'] == 'reference'
    request = case.dialog._queued_requests[0]
    assert_section_request(request, case.source.context)
    assert request['question_context'] == browser and request['reference'] == reference
    assert request['question_selections'] == reference['selections']


def lecture_reader(case, *, cancel=False):
    library = _SyntheticLibrary()
    source = deepcopy(library.outlines[library.sources[0]['source_id']]['source'])
    request = mixed_request(source)
    class Handout(QDialog):
        def __init__(self, facade, tasks, parent, *, initial_source=None):
            super().__init__(parent)
            assert initial_source is None
            self.confirm = QPushButton(self)
            self.request = deepcopy(request)
        def exec(self):
            assert '知识与讲义选材' not in self.confirm.text()
            QTimer.singleShot(0, self.reject if cancel else self.accept)
            return super().exec()
    case.monkeypatch.setattr(handout_ui, 'HandoutMaterialWorkspace', Handout)
    from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as handoff
    case.monkeypatch.setattr(handoff, 'validate_material_request', lambda value: deepcopy(value))
    return request


def test_no_card_image_lecture_network_and_complete_question_share_one_ordered_queue(make_direct):
    case = make_direct()
    ready(case)
    real_crop(case)
    case.dialog.image_list.setCurrentRow(0)
    case.dialog.choose_image()
    case.tasks.finish(VERIFY_CROP)
    material = lecture_reader(case)
    case.dialog.browse_handouts()
    case.tasks.finish(OPEN_HANDOUT)
    case.tasks.finish(VERIFY_HANDOUT)
    word_reader(case)
    case.dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    case.tasks.finish(VERIFY_WORD)
    queued = case.dialog._queued_requests
    assert [row['kind'] for row in queued] == ['textbook_images', 'handout_materials',
        'handout_materials', 'handout_materials', 'questions']
    assert [row['material_request']['selection_order'][0]['kind'] for row in queued[1:4]] == ['image', 'knowledge', 'question']
    assert queued[1]['material_request']['image_selections'][0] == material['image_selections'][0]
    assert queued[2]['material_request']['knowledge_requests'][0]['teacher_only'] is True
    assert queued[3]['material_request']['question_selections'][0] == material['question_selections'][0]
    assert all(row['course_handout_source'] is None for row in queued[1:4])
    for row in queued:
        assert_section_request(row, case.source.context)
    case.dialog.queue_list.setCurrentRow(4)
    case.dialog._move_queue(-1)
    assert case.dialog._queued_requests[3]['kind'] == 'questions'
    case.dialog.queue_confirm.click()
    assert case.dialog.requests == case.dialog._queued_requests and len(case.dialog.requests) == 5
    case.dialog._queued_requests[0]['crop_requests'][0]['caption'] = 'caller mutation'
    assert case.dialog.requests[0]['crop_requests'][0]['caption'] != 'caller mutation'
    assert all(not FORBIDDEN_KNOWLEDGE.intersection(row) for row in case.dialog.requests)


@pytest.mark.parametrize('kind', ['knowledge', 'linked'])
def test_missing_card_cannot_become_knowledge_or_linked_request(make_direct, kind):
    case = make_direct()
    ready(case)
    with pytest.raises(ValueError, match='知识'):
        if kind == 'knowledge':
            case.dialog._base_request('knowledge')
        else:
            case.dialog._finish('questions', None, question_mode='linked', linked_question={})
    assert not case.dialog._queued_requests and case.dialog._card is None


@pytest.mark.parametrize('action', ['cancel', 'close', 'shutdown', 'parent'])
def test_late_no_card_section_read_is_rejected_and_cancel_can_reread(make_direct, action):
    case = make_direct()
    pending = case.tasks.take(SECTION_READ)
    value = pending.operation()
    if action == 'cancel':
        case.dialog.cancel_operation()
    elif action == 'close':
        case.dialog.reject()
    elif action == 'shutdown':
        case.tasks.is_shutting_down = True
    else:
        case.guard['revoked'] = True
        case.dialog._guard_current()
    pending.success(value)
    assert not case.dialog._section_loaded and case.dialog._card is None
    assert not case.dialog._queued_requests and not case.dialog.image_list.count()
    if action == 'cancel':
        assert case.dialog.reload_section_button.isEnabled()
        case.dialog.reload_section_button.click()
        ready(case)
        assert case.dialog.browse_handouts_button.isEnabled() and case.dialog.browse_questions_button.isEnabled()


@pytest.mark.parametrize('change', ['context', 'scope', 'parent', 'section_aba', 'close', 'cancel'])
def test_no_card_modal_child_cannot_accept_after_scope_or_parent_changes(make_direct, change):
    case = make_direct()
    ready(case)
    def mutate(child):
        if change == 'context':
            case.dialog.context['source_binding']['pdf_pages'] = [999]
        elif change == 'scope':
            case.dialog.context['entry_kind'] = 'source_resource'
        elif change == 'parent':
            case.guard['revoked'] = True
            case.dialog._guard_current()
        elif change == 'section_aba':
            original = deepcopy(case.dialog.context)
            case.dialog.context = {**original, 'section_filter_key': 'other'}
            case.dialog._invalidate()
            case.dialog.context = original
            case.dialog._invalidate()
        elif change == 'close':
            case.dialog.reject()
        else:
            case.dialog._invalidate()
        child.accept()
    word_reader(case, mutate=mutate)
    case.dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    assert not case.dialog._queued_requests and not case.dialog.requests and case.dialog.request is None


def test_cancelled_no_card_modal_choice_does_not_change_completed_queue(make_direct):
    case = make_direct()
    ready(case)
    word_reader(case)
    case.dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    case.tasks.finish(VERIFY_WORD)
    before = deepcopy(case.dialog._queued_requests)
    lecture_reader(case, cancel=True)
    case.dialog.browse_handouts()
    case.tasks.finish(OPEN_HANDOUT)
    assert case.dialog._queued_requests == before and case.dialog.requests == []


def test_no_card_430px_source_and_queue_screenshots(make_direct, tmp_path):
    case = make_direct()
    install_ui_font(case.app)
    case.dialog.setStyleSheet(WORKBENCH_STYLE)
    ready(case)
    case.dialog.show()
    case.dialog.resize(430, 860)
    events(case.app)
    assert case.dialog.width() == 430 and case.dialog.knowledge_list.currentRow() == -1
    assert case.dialog.split.orientation() == Qt.Orientation.Vertical
    assert case.dialog.tab_choice.isVisible() and not case.dialog.tabs.tabBar().isVisible()
    folder = Path(os.environ.get('SECTION_UI_OWN', str(tmp_path))) / 'screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    assert case.dialog.grab().save(str(folder / 'no-card-section-430.png'))
    real_crop(case)
    case.dialog.image_list.setCurrentRow(0)
    case.dialog.choose_image()
    case.tasks.finish(VERIFY_CROP)
    word_reader(case)
    case.dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    case.tasks.finish(VERIFY_WORD)
    events(case.app)
    assert case.dialog.width() == 430 and case.dialog.queue_list.count() == 2
    assert case.dialog.grab().save(str(folder / 'no-card-ordered-queue-430.png'))
    (folder / 'screenshots-metadata.json').write_text(json.dumps({
        'synthetic_source': True, 'context': case.source.context,
        'window_width': case.dialog.width(), 'scope': 'section', 'card': None,
        'queue_kinds': [row['kind'] for row in case.dialog._queued_requests],
        'files': ['no-card-section-430.png', 'no-card-ordered-queue-430.png']},
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
