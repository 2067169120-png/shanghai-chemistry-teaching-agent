"""Standalone section materials UI; no application, recovery or state store."""
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton

from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class Tasks:
    is_shutting_down = False

    def __init__(self):
        self.pending = []
        self.cancelled = []

    def submit(self, title, operation, *, on_success, on_failure):
        identity = 'task-' + str(len(self.pending))
        self.pending.append((operation, on_success, on_failure, identity))
        return identity

    def cancel(self, identity):
        self.cancelled.append(identity)

    def finish(self, index=-1):
        operation, success, failure, _ = self.pending[index]
        try:
            result = operation()
        except (ValueError, OSError) as exc:
            failure(str(exc))
            return
        success(result)


def card(title, identity):
    return resource('knowledge_note', title,
        [block('text', 'knowledge', title + '完整定义\n条件与比较\n最后一段知识解释'),
         block('text', 'knowledge', '适用范围与待核对处：来源提醒'),
         block('text', 'teacher_note', '课堂核对建议：来源记录')],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a' * 64,
         'source_revision': identity * 64, 'record_id': 'SYNTHETIC-' + identity,
         'pdf_pages': [12], 'printed_pages': [8]})


class Links:
    def __init__(self, cards, *, linked=False):
        self.cards = cards
        self.linked = linked
        self.version = 1
        self.calls = []
        self.reference = {'selections': [{'key': 'Q1', 'revision': 'q-rev', 'points': 2}],
                          'include_images': True, 'text': '全部原题、原图与答案'}
        self.scoped = SimpleNamespace(word_question_reference=lambda *a, **kw: deepcopy(self.reference))

    def knowledge_context(self, selected):
        self.calls.append(('knowledge_context', deepcopy(selected)))
        title = selected['original']['title']
        return {'endpoint': {'title': title, 'selection': {'note_id': title}}, 'card': deepcopy(selected),
                'browser_context': {'original_title': title, 'curriculum_context': {'section_key': 'S1'},
                                    'search_query': '', 'binding_sha256': 'b' * 64}}

    def snapshot(self):
        return {'version': self.version}

    def list(self, selected, *, assess=False):
        self.calls.append(('list', selected['original']['title'], assess))
        if not self.linked or selected['original']['title'] != '知识甲':
            return []
        return [self.evaluate('KQL-1')]

    def evaluate(self, identity):
        return {'id': identity, 'status': 'current', 'removed': False,
                'knowledge': self.knowledge_context(self.cards['甲'])['endpoint'],
                'question': {'source_name': '完整原讲义.docx', 'key': 'Q1', 'revision': 'q-rev'},
                'reason': '本题用知识甲完整定义解释实验现象'}

    def resolve_selection(self, row):
        self.calls.append(('resolve', deepcopy(row)))
        return {'selection': {'key': 'Q1', 'revision': 'q-rev'},
                'row': {'source_id': 'DOC-1'}, 'scoped_facade': self.scoped}


@pytest.fixture
def setup(app, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as context_backend
    cards = {'甲': card('知识甲', 'b'), '乙': card('知识乙', 'c')}
    rows = [{'title': '知识' + k, 'book_key': 'registered:BOOK-1', 'section_filter_key': 'S1',
             'selection': {'note_id': k}} for k in cards]
    context = {'schema_version': 'synthetic', 'status': 'ready', 'resource_id': cards['甲']['id'],
        'book_key': 'registered:BOOK-1', 'book_label': '实际原教材', 'section_filter_key': 'S1',
        'section_label': '真实第一节', 'section_choices': [], 'knowledge_options': rows,
        'knowledge_selection': None, 'binding_sha256': 'd' * 64}
    calls, guards = [], []
    monkeypatch.setattr(context_backend, 'recheck_section_material_context',
        lambda *args: calls.append(('context_recheck', deepcopy(args[-1]))) or True)
    monkeypatch.setattr(ui, 'import_knowledge_note', lambda facade, selected: deepcopy(cards[selected['note_id']]))
    def images(facade, selected):
        title = selected['original']['title']
        return {'rows': [{'selection': {'visual_asset_id': title + '-PAGE', 'revision': 'rev'},
                          'label': title + '完整原页', 'relation': '知识卡原页'}],
                'binding': title + '-binding', 'notices': [], 'candidate_only': True}
    monkeypatch.setattr(ui, 'knowledge_textbook_options', images)
    monkeypatch.setattr(ui, 'recheck_knowledge_question_context',
        lambda facade, selected, context: calls.append(('question_recheck', deepcopy(selected), deepcopy(context))) or True)
    tasks = Tasks()
    links = Links(cards)
    dialog = ui.SectionMaterialsDialog(SimpleNamespace(), tasks, context, guard=lambda: guards.append(True),
        course_resources=[cards['甲']], image_assets=[], service=links)
    yield SimpleNamespace(dialog=dialog, tasks=tasks, cards=cards, context=context, links=links,
                          calls=calls, guards=guards, app=app, monkeypatch=monkeypatch)
    dialog.reject()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def select(s, index=0):
    s.dialog.knowledge_list.setCurrentRow(index)
    s.tasks.finish()


def test_actual_section_list_and_complete_knowledge_with_sealed_source(setup):
    s = setup
    assert s.dialog.knowledge_list.count() == 2
    assert s.dialog.knowledge_list.currentRow() == -1
    assert s.tasks.pending == []
    select(s)
    assert s.dialog.knowledge_heading.text() == '知识甲'
    assert s.dialog.knowledge_text.toPlainText() == '知识甲完整定义\n条件与比较\n最后一段知识解释'
    assert s.dialog._card['original'] == s.cards['甲']['original']
    assert '候选' in s.dialog.image_list.item(0).text()
    assert s.links.calls[-1] == ('list', '知识甲', True)
    assert s.dialog.question_list.count() == 0
    assert '还没有已保存' in s.dialog.question_hint.text()
    assert s.dialog.browse_questions_button.isEnabled()


def test_knowledge_handoff_preserves_original_but_excludes_source_reminders(setup):
    s = setup
    select(s)
    original = deepcopy(s.dialog._card['original'])
    s.dialog.choose_knowledge()
    s.tasks.finish()
    assert s.dialog.result() == QDialog.DialogCode.Accepted
    chosen = s.dialog.course_result['resources'][0]
    assert chosen['original'] == original
    assert chosen['original_sha256'] == s.cards['甲']['original_sha256']
    assert [b['value'] for b in chosen['blocks']] == ['知识甲完整定义\n条件与比较\n最后一段知识解释', '', '']
    assert s.dialog.request['knowledge_card']['original'] == original
    assert s.dialog.request['knowledge_selection'] == {'note_id': '甲'}
    assert s.dialog.request['context'] == s.context
    assert s.guards


def test_switch_clears_prior_candidates_submission_and_ignores_late_callback(setup):
    s = setup
    select(s)
    s.dialog.course_result = {'old': True}
    s.dialog.request = {'old': True}
    s.dialog.choose_knowledge()
    operation, late_success, _, identity = s.tasks.pending[-1]
    old = operation()
    s.dialog.knowledge_list.setCurrentRow(1)
    assert identity in s.tasks.cancelled
    assert s.dialog.course_result is None and s.dialog.request is None
    assert not s.dialog.knowledge_text.toPlainText()
    assert not s.dialog.image_list.count() and not s.dialog.question_list.count()
    late_success(old)
    assert s.dialog.course_result is None and not s.dialog._closed
    s.tasks.finish()
    assert s.dialog.knowledge_heading.text() == '知识乙'
    assert '知识乙' in s.dialog.knowledge_text.toPlainText()
    assert s.dialog.page_preview._selection['visual_asset_id'] == '知识乙-PAGE'


@pytest.mark.parametrize('action', ['cancel', 'close', 'guard'])
def test_cancel_close_or_changed_frozen_parent_discards_late_read(setup, action):
    s = setup
    s.dialog.knowledge_list.setCurrentRow(0)
    operation, late_success, _, identity = s.tasks.pending[-1]
    value = operation()
    if action == 'cancel':
        s.dialog.cancel_operation()
    elif action == 'close':
        s.dialog.reject()
    else:
        def changed():
            raise ValueError('冻结插入位置已变化')
        s.dialog.guard = changed
        s.dialog._guard_current()
    late_success(value)
    assert not s.dialog.knowledge_text.toPlainText()
    assert s.dialog.request is None and s.dialog.course_result is None
    assert identity in s.tasks.cancelled
    assert not s.dialog.add_knowledge.isEnabled()


def test_ready_callback_checks_parent_again_before_exposing_material(setup):
    s = setup
    s.dialog.knowledge_list.setCurrentRow(0)
    operation, ready, _, _ = s.tasks.pending[-1]
    value = operation()
    def changed():
        raise ValueError('课件已变化')
    s.dialog.guard = changed
    ready(value)
    assert not s.dialog.knowledge_text.toPlainText()
    assert s.dialog.request is None and '课件已变化' in s.dialog.status.text()


def test_cancelled_only_knowledge_can_be_retried_by_clicking_same_row(setup):
    s = setup
    s.dialog.knowledge_list.takeItem(1)
    s.dialog.knowledge_list.setCurrentRow(0)
    s.dialog.cancel_operation()
    s.dialog.knowledge_list.itemClicked.emit(s.dialog.knowledge_list.currentItem())
    s.tasks.finish()
    assert '最后一段知识解释' in s.dialog.knowledge_text.toPlainText()
    assert s.dialog.add_knowledge.isEnabled()


def test_only_same_book_explicit_sections_and_no_empty_section_guess(setup):
    s = setup
    from integrations.deeptutor_shchem_v1 import desktop_section_materials as backend
    s.dialog.context.update(status='section_selection_required', section_filter_key=None,
        section_label=None, knowledge_options=[], section_choices=[
            {'book_key': 'registered:BOOK-1', 'section_filter_key': 'S1', 'section_label': '真实第一节'},
            {'book_key': 'registered:OTHER', 'section_filter_key': 'BAD', 'section_label': '其他书章节'}])
    initial = deepcopy(s.dialog.context)
    s.dialog._show_context()
    assert s.dialog.section.count() == 2
    assert s.dialog.section.findData('BAD') == -1
    assert not s.dialog.knowledge_list.count() and not s.tasks.pending
    def resolve(facade, item, resources, assets, *, section_filter_key):
        assert section_filter_key == 'S1'
        return {**deepcopy(initial), 'status': 'ready', 'section_filter_key': 'S1',
                'section_label': '真实第一节', 'knowledge_options': []}
    s.monkeypatch.setattr(backend, 'section_material_context', resolve)
    s.dialog.section.setCurrentIndex(1)
    s.tasks.finish()
    assert not s.dialog.knowledge_list.count()
    assert '本节尚无' in s.dialog.status.text()
    assert not s.dialog.add_knowledge.isEnabled() and not s.dialog.browse_questions_button.isEnabled()


def test_crop_opens_actual_selected_candidate_and_only_returns_request(setup):
    s = setup
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_preparation_crop_dialog as crop_ui
    calls = []
    class Crop(QDialog):
        def __init__(self, facade, tasks, selection, parent):
            super().__init__(parent)
            self.confirm = QPushButton()
            calls.append(deepcopy(selection))
            self.selection_request = {'selection': deepcopy(selection), 'caption': '完整图题',
                                      'purpose': '说明这条知识', 'selection_confirmed': True}
        def exec(self):
            assert '知识甲' in self.windowTitle()
            assert '知识与所选教材图' in self.confirm.text()
            return self.DialogCode.Accepted
    s.monkeypatch.setattr(crop_ui, 'TextbookPreparationCropDialog', Crop)
    select(s)
    s.dialog.choose_image()
    s.tasks.finish()
    assert calls == [{'visual_asset_id': '知识甲-PAGE', 'revision': 'rev'}]
    assert s.dialog.course_result is None
    assert s.dialog.request['kind'] == 'textbook_images'
    assert s.dialog.request['crop_binding'] == '知识甲-binding'
    assert s.dialog.request['crop_requests'][0]['selection'] == calls[0]


def word_stub(s, *, linked=False, cancel=False, wrong_question=False):
    from integrations.deeptutor_shchem_v1.desktop_workbench import word_question_dialog as question_ui
    calls = []
    class Word(QDialog):
        def __init__(self, facade, tasks, parent, **kwargs):
            super().__init__(parent)
            calls.append((facade, deepcopy(kwargs)))
            self.import_button = QPushButton()
            self.include_images = QCheckBox()
            self.reference = deepcopy(s.links.reference)
            if wrong_question:
                self.reference['selections'][0]['key'] = 'Q2'
            self.selections = deepcopy(self.reference['selections'])
        def exec(self):
            assert '知识甲' in self.windowTitle()
            assert '加入本课' in self.import_button.text()
            assert self.include_images.isChecked() and not self.include_images.isEnabled()
            return self.DialogCode.Rejected if cancel else self.DialogCode.Accepted
    s.monkeypatch.setattr(question_ui, 'WordQuestionDialog', Word)
    return calls


def test_empty_links_browse_uses_real_knowledge_context_and_explicit_request(setup):
    s = setup
    calls = word_stub(s)
    select(s)
    from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as handout_ui
    expected_browser = deepcopy(s.dialog._knowledge_context['browser_context'])
    class SourceChoice(QDialog):
        def __init__(self, facade, tasks, parent, *, questions_only=False, question_context=None):
            super().__init__(parent)
            assert questions_only is True and question_context == expected_browser
            self.confirm = QPushButton(self)
            self.all_questions_button = QPushButton('浏览全部 Word 题库…', self)
            self.all_question_library_requested = False
            self.request = None
            self.all_questions_button.clicked.connect(self.choose_all)
        def choose_all(self):
            self.all_question_library_requested = True
            self.accept()
        def exec(self):
            assert calls == [] and s.dialog._child is self
            self.all_questions_button.click()
            assert self.all_question_library_requested and self.request is None
            return self.DialogCode.Accepted
    s.monkeypatch.setattr(handout_ui, 'HandoutMaterialWorkspace', SourceChoice)
    s.dialog.browse_questions()
    s.tasks.finish()
    assert calls == [] and s.dialog.request is None
    s.tasks.finish()  # Explicit all-library choice rechecks the current scope first.
    s.tasks.finish()
    _, options = calls[0]
    assert options['persist_selection'] is False and options['allow_knowledge_links'] is False
    assert options['handoff_mode'] == 'reference'
    assert options['initial_curriculum_context'] == {'section_key': 'S1'}
    assert options['lesson_topic'] == '知识甲'
    assert s.dialog.course_result is None
    assert s.dialog.request['question_mode'] == 'browse'
    assert s.dialog.request['question_selections'] == s.links.reference['selections']
    assert s.dialog.request['reference'] == s.links.reference
    assert s.dialog.request['question_context'] == expected_browser
    assert s.dialog.request['selection_scope'] == 'knowledge'
    assert s.dialog.request['knowledge_selection'] == {'note_id': '甲'}
    assert s.dialog.request['knowledge_original'] == s.cards['甲']['original']


def test_linked_full_question_reader_is_scoped_and_requires_explicit_selection(setup):
    s = setup
    s.links.linked = True
    calls = word_stub(s, linked=True)
    select(s)
    assert s.dialog.question_list.count() == 1
    assert '本题用知识甲' in s.dialog.question_detail.toPlainText()
    s.dialog.choose_linked_question()
    s.tasks.finish()
    s.tasks.finish()
    facade, options = calls[0]
    assert facade is s.links.scoped
    assert options['initial_source_id'] == options['required_source_id'] == 'DOC-1'
    assert options['initial_question_key'] == 'Q1'
    assert options['persist_selection'] is False
    assert s.dialog.request['question_mode'] == 'linked'
    request = s.dialog.request['linked_question']
    assert request['link_id'] == 'KQL-1' and request['snapshot'] == {'version': 1}
    assert request['reference'] == s.links.reference
    assert request['knowledge_endpoint'] == s.links.knowledge_context(s.cards['甲'])['endpoint']


@pytest.mark.parametrize('case', ['cancel', 'wrong_question', 'changed_link'])
def test_linked_preview_cancel_other_question_or_changed_relation_never_handoffs(setup, case):
    s = setup
    s.links.linked = True
    word_stub(s, linked=True, cancel=case == 'cancel', wrong_question=case == 'wrong_question')
    select(s)
    s.dialog.choose_linked_question()
    s.tasks.finish()
    if case == 'changed_link':
        s.links.version = 2
        s.tasks.finish()
    assert s.dialog.request is None and s.dialog.course_result is None
    assert not s.dialog._closed


def test_wide_and_narrow_keep_body_readable_and_preview_height(setup):
    s = setup
    select(s)
    s.dialog.show()
    s.dialog.resize(1160, 820)
    s.app.processEvents()
    assert s.dialog.split.orientation() == Qt.Orientation.Horizontal
    assert s.dialog.knowledge_text.height() >= 250
    s.dialog.resize(420, 780)
    s.app.processEvents()
    assert s.dialog.split.orientation() == Qt.Orientation.Vertical
    assert s.dialog.knowledge_text.height() >= 170
    s.dialog.tabs.setCurrentIndex(1)
    s.app.processEvents()
    assert s.dialog.image_list.height() >= 70
    assert s.dialog.page_preview.canvas.height() >= 120
    assert s.dialog.width() == 420
