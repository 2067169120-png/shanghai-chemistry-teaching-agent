"""Receiving-course choices keep the source reader and require explicit delivery."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from test_desktop_studio_ui import window as window, settle
from test_personal_textbook_study import pdf
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import PreparationDraftDialog


def reading_selection(win, app, tmp_path):
    source = pdf(tmp_path / '合成教材.pdf')
    workspace = win.facade.textbook_workspace()
    workspace.commit_books(workspace.preview_books([source]))
    study = win.facade.personal_textbook_study()
    book = study.books()[0]
    selection = {key: book[key] for key in ('source_id', 'revision')}
    view = study.save_section(selection, study.book_study(selection)['study_revision'],
        title='合成章节', pdf_start=2, pdf_end=3, range_confirmed=True)
    study.save_note(selection, view['study_revision'], section_id=view['sections'][0]['section_id'],
        title='合成笔记', text='合成原笔记；定量关系与单位保留。', pdf_page=2, teacher_confirmed=True)
    win.navigate('textbooks')
    settle(app, lambda: not win.textbook_page._loading)
    win.textbook_page.book.setCurrentIndex(win.textbook_page.book.findData(book['source_id']))
    personal = win.textbook_page.personal
    settle(app, lambda: not personal._busy)
    personal.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    personal.preview_button.click()
    settle(app, lambda: not personal._busy)
    personal._read(True)
    settle(app, lambda: personal._reader is not None and bool(personal._reader._loaded_pages))
    personal._reader.next.click()
    settle(app)
    return personal


def source_state(win):
    shelf, personal = win.textbook_page, win.textbook_page.personal
    return (shelf.book.currentData(), shelf.tree.currentItem(), personal._section_id,
            personal._reader, personal._reader.pages.currentData(), deepcopy(personal._checked),
            deepcopy(personal._reference), deepcopy(personal._reference_key))


def test_direct_book_entry_shows_actual_editor_and_updates_without_import(window):
    win, app = window
    prep = win.preparation_page
    prep.topic.setText('原课题 <酸与碱>')
    prep.materials.setPlainText('教师未保存材料')
    before = deepcopy(prep._payload())
    win.navigate('textbooks')
    settle(app, lambda: not win.textbook_page._loading)
    bar = win.textbook_page.preparation_context_bar
    assert bar.isVisible()
    assert '原课题 <酸与碱>' in bar.title.text()
    assert '未保存' in bar.detail.text()
    assert prep._payload() == before
    prep.topic.setText('更新后的当前课题')
    settle(app)
    assert '更新后的当前课题' in bar.title.text()
    assert prep.materials.toPlainText() == '教师未保存材料'
    bar.edit_button.click()
    assert win.stack.currentWidget() is prep and prep._payload()['topic'] == '更新后的当前课题'


def test_cancelled_destination_choices_preserve_editor_and_source(window, tmp_path, monkeypatch):
    win, app = window
    win.preparation_page.topic.setText('当前课A')
    personal = reading_selection(win, app, tmp_path)
    before_source, before_payload = source_state(win), deepcopy(win.preparation_page._payload())
    monkeypatch.setattr(PreparationDraftDialog, 'exec', lambda _: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    bar = win.textbook_page.preparation_context_bar
    bar.open_button.click()
    bar.new_button.click()
    settle(app)
    assert win.stack.currentWidget() is win.textbook_page
    assert source_state(win) == before_source
    assert win.preparation_page._payload() == before_payload and personal.confirm_button.isEnabled()


def test_open_saved_course_keeps_reader_and_delivers_only_after_confirm(window, tmp_path, monkeypatch):
    win, app = window
    prep = win.preparation_page
    prep.topic.setText('已保存课B')
    prep.audience.setText('合成高二班')
    prep.objective.setPlainText('合成目标')
    prep.materials.setPlainText('B原资料')
    saved = win.facade.create_preparation_draft(prep._payload())
    option = win.facade.preparation_draft_options()[0]
    original_record = deepcopy(win.facade.load_preparation_draft(option['draft_id'], option['revision']))
    prep.topic.setText('正在选材的课A')
    prep.materials.setPlainText('A未保存资料')
    personal = reading_selection(win, app, tmp_path)
    before_source = source_state(win)
    def choose(dialog):
        assert dialog.select_draft_id(option['draft_id'])
        dialog.load_button.click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PreparationDraftDialog, 'exec', choose)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    win.textbook_page.preparation_context_bar.open_button.click()
    settle(app)
    assert source_state(win) == before_source
    assert win.stack.currentWidget() is win.textbook_page
    assert prep.topic.text() == '已保存课B' and prep.materials.toPlainText() == 'B原资料'
    assert '已保存课B' in win.textbook_page.preparation_context_bar.title.text()
    assert '合成原笔记' not in prep.materials.toPlainText()
    personal.confirm_button.click()
    settle(app, lambda: not personal._busy)
    assert prep.materials.toPlainText().startswith('B原资料') and '合成原笔记' in prep.materials.toPlainText()
    assert win.facade.load_preparation_draft(option['draft_id'], option['revision']) == original_record
    assert saved is not None


def test_new_course_keeps_selection_and_can_receive_same_preview_again(window, tmp_path, monkeypatch):
    win, app = window
    prep = win.preparation_page
    prep.topic.setText('当前课A')
    personal = reading_selection(win, app, tmp_path)
    personal.confirm_button.click()
    settle(app, lambda: not personal._busy)
    assert '合成原笔记' in prep.materials.toPlainText() and not personal.confirm_button.isEnabled()
    win.navigate('textbooks')
    before_source = source_state(win)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    win.textbook_page.preparation_context_bar.new_button.click()
    settle(app)
    assert source_state(win) == before_source
    assert prep._payload() == prep.recovery.default
    assert personal.confirm_button.isEnabled()
    assert win.stack.currentWidget() is win.textbook_page
    personal.confirm_button.click()
    settle(app, lambda: not personal._busy)
    assert '合成原笔记' in prep.materials.toPlainText()


def test_busy_destination_actions_do_not_open_or_clear(window, monkeypatch):
    win, app = window
    prep = win.preparation_page
    prep.topic.setText('忙碌的当前课')
    before = deepcopy(prep._payload())
    opened = []
    monkeypatch.setattr(prep, '_open_draft', lambda: opened.append('open'))
    monkeypatch.setattr(prep.recovery, 'new_blank', lambda: opened.append('new'))
    prep._lesson_busy = True
    win._open_textbook_preparation()
    win._new_textbook_preparation()
    settle(app)
    assert not opened and prep._payload() == before
    prep._lesson_busy = False
