"""Self-contained, memory-only navigation and close policy checks.

No MainWindow, Recovery, state store, actual Facade, profile, source PDF or
existing UI fixture is constructed. Main closeEvent is invoked on a fake owner.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_panel import PersonalTextbookPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage


def synthetic_view(source):
    return {
        'book': {'source_id': source, 'revision': source * 64, 'source_name': '合成教材' + source,
                 'page_count': 8, 'section_count': 2, 'note_count': 3},
        'study_revision': source + '-study-1',
        'sections': [dict(section_id=source + '-S1', title='第一节', pdf_start=1, pdf_end=4,
                          revision=source + '-section-1', range_confirmed=True),
                     dict(section_id=source + '-S2', title='第二节', pdf_start=5, pdf_end=8,
                          revision=source + '-section-2', range_confirmed=True)],
        'notes': [dict(note_id=source + '-N1', section_id=source + '-S1', title='同名笔记',
                       text='第一条已保存。', pdf_page=2, revision=source + '-note-1'),
                  dict(note_id=source + '-N2', section_id=source + '-S1', title='同名笔记',
                       text='第二条已保存。', pdf_page=3, revision=source + '-note-2'),
                  dict(note_id=source + '-N3', section_id=source + '-S2', title='同名笔记',
                       text='第二节已保存。', pdf_page=6, revision=source + '-note-3')],
    }


class ManualBridge(QObject):
    task_finished = Signal(str)
    task_cancelled = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.pending, self.cancelled = [], []
        self.serial = 0

    def submit(self, title, operation, *, on_success, on_failure):
        self.serial += 1
        task = SimpleNamespace(id=str(self.serial), title=title, operation=operation,
                               success=on_success, failure=on_failure)
        self.pending.append(task)
        return task.id

    def cancel(self, task_id):
        self.cancelled.append(task_id)

    def take(self, title):
        task = next(row for row in self.pending if row.title == title)
        self.pending.remove(task)
        return task

    def complete(self, title):
        task = self.take(title)
        try:
            value = task.operation()
        except Exception as exc:
            task.failure(str(exc))
            self.task_finished.emit(task.id)
            raise
        task.success(value)
        self.task_finished.emit(task.id)
        return value


class MemoryStudy:
    def __init__(self):
        self.views = {name: synthetic_view(name) for name in ('a', 'b')}
        self.calls, self.serial = [], 0

    def book_study(self, selection):
        view = self.views[selection['source_id']]
        assert selection['revision'] == view['book']['revision']
        return deepcopy(view)

    def _current(self, selection, expected):
        view = self.views[selection['source_id']]
        if selection['revision'] != view['book']['revision'] or expected != view['study_revision']:
            raise ValueError('内存服务的来源或整体版本冲突')
        return view

    def save_note(self, selection, expected, **fields):
        view = self._current(selection, expected)
        assert fields['teacher_confirmed'] is True
        assert fields['title'].strip() and len(fields['title']) <= 160
        assert fields['text'].strip() and len(fields['text']) <= 1200
        section = next(row for row in view['sections'] if row['section_id'] == fields['section_id'])
        assert section['pdf_start'] <= fields['pdf_page'] <= section['pdf_end']
        self.calls.append(('save_note', deepcopy(selection), expected, deepcopy(fields)))
        self.serial += 1
        note = next((row for row in view['notes'] if row['note_id'] == fields['note_id']), None)
        if note is None:
            note = {'note_id': f'{selection["source_id"]}-NEW-{self.serial}'}
            view['notes'].append(note)
        else:
            assert note['section_id'] == fields['section_id']
        note.update({key: fields[key] for key in ('section_id', 'title', 'text', 'pdf_page')})
        note['revision'] = f'note-saved-{self.serial}'
        view['study_revision'] = f'study-saved-{self.serial}'
        return deepcopy(view)

    def save_section(self, selection, expected, **fields):
        view = self._current(selection, expected)
        self.calls.append(('save_section', deepcopy(fields)))
        row = next(row for row in view['sections'] if row['section_id'] == fields['section_id'])
        row.update(fields)
        row['revision'] += '-changed'
        view['study_revision'] += '-section'
        return deepcopy(view)

    def save_outline(self, selection, _preview, _selected):
        view = self.views[selection['source_id']]
        view['sections'].append(dict(section_id='added-outline', title='追加目录', pdf_start=7,
                                     pdf_end=8, revision='outline-1', range_confirmed=False))
        view['study_revision'] += '-outline'
        self.calls.append(('save_outline',))
        return {'view': deepcopy(view), 'added': 1}


class MemoryFacade:
    def __init__(self):
        self.study = MemoryStudy()
        self.references = []

    def personal_textbook_study(self):
        return self.study

    def personal_textbook_reference(self, selection, revision, notes):
        self.study._current(selection, revision)
        self.references.append(deepcopy(notes))
        return {'materials': '合成已保存参考'}

    def textbook_bookshelf(self):
        return {
            'local_books': [deepcopy(value['book']) for value in self.study.views.values()],
            'volumes': [{'volume_id': 'registered', 'volume_title': '合成登记教材',
                         'source_name': '合成登记教材', 'local_file_status': 'found',
                         'knowledge_count': 0, 'asset_count': 0}],
            'sections': [], 'concepts': [], 'assets': [], 'reading_notes': [],
            'supplements': [], 'unmapped_concept_count': 0, 'notices': [],
        }


@pytest.fixture(scope='module')
def app():
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    application = QApplication.instance() or QApplication([])
    install_ui_font(application)
    return application


@pytest.fixture
def panel(app):
    facade, bridge = MemoryFacade(), ManualBridge()
    widget = PersonalTextbookPanel(facade, bridge)
    widget._loaded(facade.study.book_study({'source_id': 'a', 'revision': 'a' * 64}))
    yield widget, facade, bridge
    widget._note_edits.discard_all()
    widget._note_editor_owner = None
    widget.close(); widget.deleteLater(); app.processEvents()


@pytest.fixture
def bookshelf(app):
    facade, bridge = MemoryFacade(), ManualBridge()
    page = TextbookBookshelfPage(facade, bridge)
    page.refresh()
    bridge.complete('读取教材书架')
    page.book.setCurrentIndex(page.book.findData('a'))
    bridge.complete('读取个人教材章节与笔记')
    yield page, facade, bridge
    page.personal._note_edits.discard_all()
    page.personal._note_editor_owner = None
    page.close(); page.deleteLater(); app.processEvents()


def edit(widget, title='未保存名称', text='  尚未保存\n条件、符号与单位。  ', page=4):
    widget.note_title.setText(title)
    widget.note_text.setPlainText(text)
    widget.note_page.setValue(page)
    return title, text, page


def fields(widget):
    return widget.note_title.text(), widget.note_text.toPlainText(), widget.note_page.value()


def choose_section(widget, index):
    widget.tree.setCurrentItem(widget.tree.topLevelItem(index))


def test_saved_note_title_text_page_and_confirmation_survive_navigation(panel):
    widget, facade, _ = panel
    before = deepcopy(widget._view)
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget)
    widget.note_confirmed.setChecked(True)
    first = widget._note_editor_owner
    widget.note_list.setCurrentRow(1)
    widget.note_list.setCurrentRow(0)
    assert fields(widget) == wanted
    assert widget.note_confirmed.isChecked()
    assert widget._note_editor_owner == first
    assert len(widget._note_edits.dirty_keys()) == 1
    assert '未保存修改' in widget.note_edit_status.text()
    assert widget._view == before and facade.study.calls == []


def test_new_slot_repeat_add_and_each_section_remember_position(panel):
    widget, _, _ = panel
    wanted = edit(widget)
    owner = widget._note_editor_owner
    widget.new_note.click(); widget.new_note.click()
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    widget.note_list.setCurrentRow(1)
    wanted_saved = edit(widget, '保存条目修订', '这条也暂存', 3)
    choose_section(widget, 1)
    second = edit(widget, '第二节新稿', '另一条新稿', 8)
    choose_section(widget, 0)
    assert fields(widget) == wanted_saved and widget._note_id == 'a-N2'
    widget.new_note.click()
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    choose_section(widget, 1)
    assert fields(widget) == second
    assert len(widget._note_edits.dirty_keys()) == 3


def test_book_a_b_a_registered_and_refresh_restore_exact_owner(bookshelf):
    page, facade, bridge = bookshelf
    widget = page.personal
    choose_section(widget, 1)
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget, 'A原位置', 'A文字', 7)
    owner = widget._note_editor_owner
    page.book.setCurrentIndex(page.book.findData('b'))
    bridge.complete('读取个人教材章节与笔记')
    edit(widget, 'B新稿', 'B文字', 4)
    page.book.setCurrentIndex(page.book.findData('a'))
    bridge.complete('读取个人教材章节与笔记')
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    page.book.setCurrentIndex(page.book.findData('registered'))
    assert widget.isHidden()
    page.book.setCurrentIndex(page.book.findData('a'))
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    page.refresh(); bridge.complete('读取教材书架'); bridge.complete('读取个人教材章节与笔记')
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    assert facade.study.calls == []


def test_repeated_loaded_never_creates_dirty_and_preserves_raw_qt_normalization(panel):
    widget, facade, _ = panel
    view = deepcopy(widget._view)
    view['notes'][0]['text'] = '原文\r\n第二行\r第三行\u2029第四行\u00a0尾'
    facade.study.views['a'] = deepcopy(view)
    widget._loaded(view)
    widget.note_list.setCurrentRow(0)
    shown = widget.note_text.toPlainText()
    widget.note_confirmed.setChecked(True)
    widget._loaded(view); widget._loaded(view)
    assert not widget._note_edits.dirty_keys()
    assert widget._note_edits.get(widget._note_editor_owner).fields.text == view['notes'][0]['text']
    assert widget.note_text.toPlainText() == shown
    widget.note_title.setText('只改名称')
    assert widget._note_edits.get(widget._note_editor_owner).fields.text == view['notes'][0]['text']


def test_add_chapter_form_never_rebinds_note_owner(panel):
    widget, facade, bridge = panel
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget)
    owner = widget._note_editor_owner
    widget.new_section.click()
    assert widget._section_id is None and widget._note_editor_owner == owner
    widget.note_confirmed.setChecked(True)
    widget.save_note.click(); bridge.complete('保存个人教材笔记')
    assert facade.study.calls[0][3]['section_id'] == 'a-S1'
    assert fields(widget) == wanted and widget._note_id == 'a-N1'


def test_save_section_retains_out_of_range_page_and_blocks_rebinding(panel):
    widget, _, bridge = panel
    wanted = edit(widget, page=4)
    widget.end.setValue(2)
    widget.range_confirmed.setChecked(True)
    widget.save_section.click(); bridge.complete('保存个人教材章节')
    assert fields(widget) == wanted
    assert not widget.save_note.isEnabled()
    assert not widget.note_confirmed.isChecked()
    assert widget._note_edits.get(widget._note_editor_owner).conflict
    assert '4' in widget.note_edit_status.text() or '范围' in widget.note_edit_status.text()


def test_outline_preserves_note_and_unsaved_chapter_form(panel, monkeypatch):
    widget, _, bridge = panel
    wanted = edit(widget)
    widget.section_title.setText('未保存章节名称')
    widget.start.setValue(2); widget.range_confirmed.setChecked(True)
    widget._outline_preview = {'candidate': 'synthetic'}
    monkeypatch.setattr(widget, '_outline_selected', lambda: ['candidate'])
    reads = []
    monkeypatch.setattr(widget, '_read', lambda *args, **kwargs: reads.append((args, kwargs)))
    widget._save_outline(); bridge.complete('保存个人教材自动目录')
    assert fields(widget) == wanted
    assert widget.section_title.text() == '未保存章节名称'
    assert widget.start.value() == 2 and widget.range_confirmed.isChecked()
    assert reads and len(widget._note_edits.dirty_keys()) == 1


@pytest.mark.parametrize('new_chapter', [False, True])
def test_outline_preserves_empty_chapter_title_and_page_only_edits(panel, monkeypatch, new_chapter):
    widget, _, bridge = panel
    wanted = edit(widget)
    if new_chapter:
        widget.new_section.click()
    else:
        widget.section_title.clear()
    widget.start.setValue(2); widget.end.setValue(3)
    section_id = widget._section_id
    widget._outline_preview = {'candidate': 'synthetic'}
    monkeypatch.setattr(widget, '_outline_selected', lambda: ['candidate'])
    monkeypatch.setattr(widget, '_read', lambda *_args, **_kwargs: None)
    widget._save_outline(); bridge.complete('保存个人教材自动目录')
    assert widget._section_id == section_id
    assert (widget.section_title.text(), widget.start.value(), widget.end.value()) == ('', 2, 3)
    assert not widget.range_confirmed.isChecked()
    assert fields(widget) == wanted


def test_adoption_callback_preserves_parent_draft(panel, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_knowledge_dialog
    widget, _, _ = panel
    wanted = edit(widget)
    view = deepcopy(widget._view)
    view['notes'].append(dict(note_id='adopted', section_id='a-S1', title='候选已保存',
                              text='完整核对后保存的知识', pdf_page=2, revision='adopted-v1'))
    view['study_revision'] += '-adopted'

    class MemoryKnowledgeDialog(QObject):
        notes_saved = Signal(object)
        def __init__(self, *_):
            super().__init__()
        def exec(self):
            self.notes_saved.emit({'view': view, 'note_ids': ['adopted'], 'added': 1, 'reused': 0})

    monkeypatch.setattr(textbook_knowledge_dialog, 'TextbookKnowledgeDialog', MemoryKnowledgeDialog)
    widget.knowledge_button.click()
    assert fields(widget) == wanted and 'adopted' in widget._checked
    assert len(widget._note_edits.dirty_keys()) == 1


@pytest.mark.parametrize('change', ['source', 'section', 'note', 'deleted_note', 'deleted_section'])
def test_changed_or_deleted_baseline_retains_draft_visible_and_unconfirmed(panel, change):
    widget, _, _ = panel
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget)
    owner = widget._note_editor_owner
    widget.note_confirmed.setChecked(True)
    view = deepcopy(widget._view)
    view['study_revision'] += '-changed'
    if change == 'source':
        view['book']['revision'] = 'changed-pdf-sha'
    elif change == 'section':
        view['sections'][0]['revision'] += '-changed'
    elif change == 'note':
        view['notes'][0].update(revision='changed-note', text='外部已保存新内容')
    elif change == 'deleted_note':
        view['notes'] = view['notes'][1:]
    else:
        view['sections'] = view['sections'][1:]
        view['notes'] = view['notes'][2:]
    widget._loaded(view)
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    assert not widget.save_note.isEnabled() and not widget.note_confirmed.isChecked()
    assert widget._note_edits.get(owner).dirty
    assert widget._note_edits.get(owner).conflict
    assert widget.locate_note_draft(owner)
    if change == 'note':
        assert '外部已保存新内容' in widget.note_saved_version.toPlainText()


def test_unrelated_study_revision_allows_latest_cas_and_only_current_clears(panel):
    widget, facade, bridge = panel
    wanted_new = edit(widget)
    new_key = widget._note_editor_owner
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget, '修订已保存', '另一条未修改的笔记版本变化不阻断', 3)
    view = deepcopy(widget._view)
    view['notes'][1]['revision'] += '-external'
    view['study_revision'] += '-external'
    facade.study.views['a'] = deepcopy(view)
    widget._loaded(view)
    widget.note_confirmed.setChecked(True)
    widget.save_note.click(); bridge.complete('保存个人教材笔记')
    assert fields(widget) == wanted and widget._note_id == 'a-N1'
    assert widget._note_edits.dirty_keys() == (new_key,)
    assert facade.study.calls[0][2] == view['study_revision']
    widget.new_note.click()
    assert fields(widget) == wanted_new


def test_new_save_selects_returned_id_and_failure_keeps_draft(panel):
    widget, facade, bridge = panel
    wanted = edit(widget)
    widget.note_confirmed.setChecked(True)
    widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    task.failure('合成保存失败'); bridge.task_finished.emit(task.id)
    assert fields(widget) == wanted and len(widget._note_edits.dirty_keys()) == 1
    widget.save_note.click(); bridge.complete('保存个人教材笔记')
    assert fields(widget) == wanted and widget._note_id == 'a-NEW-1'
    assert widget.note_list.currentItem().data(Qt.ItemDataRole.UserRole)['note_id'] == 'a-NEW-1'
    assert not widget._note_edits.dirty_keys() and len(facade.study.calls) == 1


def test_queued_save_is_revoked_on_navigation_and_keeps_both_books(panel):
    widget, facade, bridge = panel
    wanted = edit(widget)
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    widget.set_book(facade.study.views['b']['book'])
    bridge.complete('读取个人教材章节与笔记')
    second = edit(widget, 'B新稿', '不被旧回执覆盖', 4)
    with pytest.raises(ValueError, match='取消'):
        task.operation()
    task.success(deepcopy(facade.study.views['a']))
    assert fields(widget) == second and facade.study.calls == []
    widget.set_book(facade.study.views['a']['book']); bridge.complete('读取个人教材章节与笔记')
    assert fields(widget) == wanted


def test_late_committed_receipt_does_not_replace_new_book_and_reconciles_exact_text(panel):
    widget, facade, bridge = panel
    wanted = edit(widget)
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    committed = task.operation()
    widget.set_book(facade.study.views['b']['book']); bridge.complete('读取个人教材章节与笔记')
    second = edit(widget, 'B新稿', 'B保留', 4)
    task.success(committed)
    assert fields(widget) == second
    widget.set_book(facade.study.views['a']['book']); bridge.complete('读取个人教材章节与笔记')
    assert fields(widget) == wanted and widget._note_id == 'a-NEW-1'
    assert all(key[0] != 'a' for key in widget._note_edits.dirty_keys())


def test_submission_then_new_input_only_clears_submitted_text(panel):
    widget, _, bridge = panel
    edit(widget)
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    # Programmatic input can race disabled widgets; it remains a newer draft.
    widget.note_text.setPlainText('提交之后继续输入')
    task.success(task.operation()); bridge.task_finished.emit(task.id)
    assert widget.note_text.toPlainText() == '提交之后继续输入'
    assert widget._note_id == 'a-NEW-1' and len(widget._note_edits.dirty_keys()) == 1


def test_explicit_task_cancellation_keeps_fields_and_revokes_queued_operation(panel):
    widget, facade, bridge = panel
    wanted = edit(widget)
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    bridge.task_cancelled.emit(task.id, task.title)
    bridge.task_finished.emit(task.id)
    with pytest.raises(ValueError, match='取消'):
        task.operation()
    assert fields(widget) == wanted and len(widget._note_edits.dirty_keys()) == 1
    assert facade.study.calls == [] and not widget._busy


def test_same_title_and_revisions_in_different_sources_do_not_share_fields(panel):
    widget, facade, bridge = panel
    widget.note_list.setCurrentRow(0)
    wanted = edit(widget, '同名笔记', 'A的独立修订', 4)
    key = widget._note_editor_owner
    view_b = facade.study.views['b']
    view_b['notes'][0]['note_id'] = 'a-N1'
    view_b['notes'][0]['revision'] = 'a-note-1'
    widget.set_book(view_b['book']); bridge.complete('读取个人教材章节与笔记')
    widget.note_list.setCurrentRow(0)
    assert widget.note_text.toPlainText() == '第一条已保存。'
    edit(widget, '同名笔记', 'B的独立修订', 4)
    widget.set_book(facade.study.views['a']['book']); bridge.complete('读取个人教材章节与笔记')
    assert fields(widget) == wanted and widget._note_editor_owner == key


@pytest.mark.parametrize('kind', ['page_only', 'whitespace', 'long_text', 'long_title'])
def test_invalid_and_page_only_input_survive_without_auto_save(panel, kind):
    widget, facade, _ = panel
    if kind == 'page_only':
        widget.note_page.setValue(4)
    elif kind == 'whitespace':
        edit(widget, '  ', ' \n ', 4)
    elif kind == 'long_text':
        edit(widget, text='长' * 1201)
    else:
        edit(widget, title='名' * 161)
    wanted = fields(widget)
    widget.note_confirmed.setChecked(True)
    choose_section(widget, 1); choose_section(widget, 0)
    assert fields(widget) == wanted and not widget.save_note.isEnabled()
    assert widget._note_edits.get(widget._note_editor_owner).dirty
    assert facade.study.calls == []


def test_cancel_only_current_and_saved_link_exact_fields_recover(panel):
    widget, _, _ = panel
    original_new = edit(widget)
    new_key = widget._note_editor_owner
    widget.note_list.setCurrentRow(0)
    assert widget._saved_knowledge_selection() is not None
    edit(widget, '当前修订', '需要取消', 3)
    assert widget._saved_knowledge_selection() is None
    widget.cancel_note_edits.click()
    assert fields(widget) == ('同名笔记', '第一条已保存。', 2)
    assert widget._saved_knowledge_selection() is not None
    assert widget._note_edits.dirty_keys() == (new_key,)
    widget.new_note.click()
    assert fields(widget) == original_new
    widget.cancel_note_edits.click()
    assert fields(widget) == ('', '', 1) and not widget._note_edits.dirty_keys()


def test_selected_dirty_note_located_unrelated_new_draft_allows_saved_preview(panel):
    widget, facade, bridge = panel
    new_values = edit(widget)
    widget.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    widget.preview_button.click(); bridge.complete('核对个人教材备课参考')
    assert len(facade.references) == 1 and fields(widget) == new_values
    widget.note_list.setCurrentRow(1)
    dirty_values = edit(widget, '已勾选修订', '不能偷偷拿旧文入课', 4)
    owner = widget._note_editor_owner
    widget.new_note.click()
    widget.preview_button.click()
    assert fields(widget) == dirty_values and widget._note_editor_owner == owner
    assert len(facade.references) == 1 and not bridge.pending
    assert '先保存或取消' in widget.status.text()
    assert widget._reference is None


def test_note_navigation_retains_checked_crop_queue_and_preparation_target(panel):
    widget, _, _ = panel
    widget.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    widget.crop_page.setValue(3); widget.queue_page_button.click()
    widget.set_preparation_context({'topic': '合成目标'}, target_changed=True)
    checked, epoch = deepcopy(widget._checked), widget._preparation_target_epoch
    edit(widget)
    widget.note_list.setCurrentRow(1); widget.new_note.click()
    assert widget._checked == checked and widget._crop_pages == [3]
    assert widget._preparation_target_epoch == epoch


def test_read_and_search_locate_only_replace_memory_reader(panel, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        personal_textbook_search_dialog, textbook_source_dialog)
    widget, facade, _ = panel
    wanted = edit(widget)
    owner = widget._note_editor_owner
    widget.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    widget.crop_page.setValue(3); widget.queue_page_button.click()
    checks = deepcopy(widget._checked)
    requests = []

    class MemoryReader(QWidget):
        current_page_crop_requested = Signal(object)
        position_saved = Signal(object)
        reading_state_changed = Signal()
        def __init__(self, _facade, selection, parent, **_kwargs):
            super().__init__(parent)
            requests.append(deepcopy(selection))
            self._loaded_pages = [1, 2, 3, 4]
        def reject(self):
            self.hide()

    class MemorySearch(QObject):
        page_requested = Signal(object)
        finished = Signal(int)
        def __init__(self, *_args, **kwargs):
            super().__init__()
            self.guard = kwargs['guard']
        def _valid(self):
            self.guard()
            return True
        def show(self): pass
        def raise_(self): pass
        def activateWindow(self): pass
        def reject(self): self.finished.emit(0)

    monkeypatch.setattr(textbook_source_dialog, 'TextbookSourceDialog', MemoryReader)
    monkeypatch.setattr(personal_textbook_search_dialog, 'PersonalTextbookSearchDialog', MemorySearch)
    facade.personal_textbook_search = lambda: None
    widget._read(True, initial_page=3)
    assert requests[-1]['initial_pdf_page'] == 3
    widget._open_book_search()
    dialog = widget._search_dialog
    dialog.page_requested.emit({'selection': widget._selection(),
        'study_revision': widget._view['study_revision'], 'pdf_page': 7})
    dialog.reject()
    assert requests[-1]['initial_pdf_page'] == 7
    assert fields(widget) == wanted and widget._note_editor_owner == owner
    assert widget._checked == checks and widget._crop_pages == [3]


def test_adapted_note_keeps_evidence_page_lock_across_navigation(panel):
    widget, facade, bridge = panel
    view = deepcopy(widget._view)
    view['notes'][0].update(origin='teacher_adapted_candidate', source_evidence={'pdf_pages': [2, 3]})
    facade.study.views['a'] = deepcopy(view)
    widget._loaded(view)
    widget.note_list.setCurrentRow(0)
    assert not widget.note_page.isEnabled()
    widget.note_text.setPlainText('核对后明确修订的知识')
    widget.note_list.setCurrentRow(1); widget.note_list.setCurrentRow(0)
    assert not widget.note_page.isEnabled() and widget.note_page.value() == 2
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    bridge.complete('保存个人教材笔记')
    assert facade.study.views['a']['notes'][0]['source_evidence'] == {'pdf_pages': [2, 3]}


def test_prepare_close_back_locates_first_draft_without_closing(bookshelf, monkeypatch):
    page, facade, bridge = bookshelf
    wanted = edit(page.personal)
    first = page.personal._note_editor_owner
    page.book.setCurrentIndex(page.book.findData('b')); bridge.complete('读取个人教材章节与笔记')
    edit(page.personal, 'B稿', 'B文本', 4)
    seen = []
    monkeypatch.setattr(page.personal, '_confirm_note_close', lambda count: seen.append(count) or False)
    assert page.prepare_close() is False
    bridge.complete('读取个人教材章节与笔记')
    assert page.book.currentData() == 'a' and fields(page.personal) == wanted
    assert page.personal._note_editor_owner == first and seen == [2]
    assert not page._closed and not page.personal._closed and facade.study.calls == []


def test_explicit_discard_revokes_queued_save_and_child_has_no_second_prompt(panel, monkeypatch):
    widget, facade, bridge = panel
    edit(widget)
    widget.note_confirmed.setChecked(True); widget.save_note.click()
    task = bridge.take('保存个人教材笔记')
    prompts = []
    monkeypatch.setattr(widget, '_confirm_note_close', lambda count: prompts.append(count) or True)
    assert widget.prepare_close()
    assert widget.prepare_close()
    widget.close()
    with pytest.raises(ValueError, match='取消'):
        task.operation()
    assert prompts == [1] and facade.study.calls == [] and widget._closed


def test_close_dialog_default_and_escape_are_return_to_edit(panel, monkeypatch):
    widget, _, _ = panel
    seen = []
    def inspect(dialog):
        seen.append((dialog.defaultButton().text(), dialog.escapeButton().text(),
                     [button.text() for button in dialog.buttons()]))
        return 0
    monkeypatch.setattr(QMessageBox, 'exec', inspect)
    monkeypatch.setattr(QMessageBox, 'clickedButton', lambda dialog: dialog.defaultButton())
    assert widget._confirm_note_close(2) is False
    assert seen == [('返回编辑', '返回编辑', ['返回编辑', '放弃未保存修改并关闭'])]


def test_main_close_actual_method_on_fake_owner_preflights_before_any_shutdown():
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    calls = []
    owner = SimpleNamespace(preparation_page=SimpleNamespace(recovery=None),
        textbook_page=SimpleNamespace(prepare_close=lambda: calls.append('textbook-preflight') or False),
        navigate=lambda route: calls.append(('navigate', route)),
        _save_window_state=lambda: pytest.fail('must not persist state after close refusal'))
    event = QCloseEvent()
    TeacherWorkbenchWindow.closeEvent(owner, event)
    assert not event.isAccepted()
    assert calls == ['textbook-preflight', ('navigate', 'textbooks')]


def test_main_close_approved_actual_method_order_on_fake_owner():
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    calls = []
    owner = SimpleNamespace(
        preparation_page=SimpleNamespace(recovery=None, _cancel_textbook_image_import=lambda: calls.append('cancel-image')),
        textbook_page=SimpleNamespace(prepare_close=lambda: calls.append('textbook-preflight') or True,
                                      close=lambda: calls.append('child-close')),
        _save_window_state=lambda: calls.append('save-window'),
        _textbook_context_timer=SimpleNamespace(stop=lambda: calls.append('stop-timer')),
        setEnabled=lambda value: calls.append(('enabled', value)),
        facade=SimpleNamespace(stop_background_readers=lambda: calls.append('stop-readers'),
                               shutdown=lambda: calls.append('facade-shutdown')),
        tasks=SimpleNamespace(shutdown=lambda timeout: calls.append(('shutdown', timeout))))
    event = QCloseEvent()
    TeacherWorkbenchWindow.closeEvent(owner, event)
    assert event.isAccepted() and owner._textbook_context_closed
    assert calls == ['textbook-preflight', 'save-window', 'stop-timer', ('enabled', False),
                     'child-close', 'cancel-image', 'stop-readers', ('shutdown', 5000), 'facade-shutdown']


def test_narrow_note_status_save_and_cancel_reachable(app, panel):
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    widget, _, _ = panel
    widget.setStyleSheet(WORKBENCH_STYLE)
    widget.resize(420, 900); widget.show(); widget.tabs.setCurrentIndex(2)
    edit(widget)
    app.processEvents()
    scroll = widget.tabs.widget(2)
    for control in (widget.note_edit_status, widget.save_note, widget.cancel_note_edits):
        scroll.ensureWidgetVisible(control)
        app.processEvents()
        center = control.mapTo(scroll.viewport(), control.rect().center())
        assert scroll.viewport().rect().contains(center)
        assert control.width() <= scroll.viewport().width()
    assert widget.width() == 420


def test_bookshelf_420_note_actions_reachable_with_actual_styles(app, bookshelf):
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    page, _, _ = bookshelf
    page.setStyleSheet(WORKBENCH_STYLE)
    page.resize(420, 1000); page.show()
    widget = page.personal
    widget.tabs.setCurrentIndex(2); edit(widget)
    app.processEvents()
    scroll = widget.tabs.widget(2)
    for control in (widget.note_edit_status, widget.save_note, widget.cancel_note_edits):
        scroll.ensureWidgetVisible(control)
        app.processEvents()
        center = control.mapTo(scroll.viewport(), control.rect().center())
        assert scroll.viewport().rect().contains(center)
        assert control.width() <= scroll.viewport().width()
    assert page.width() == 420
