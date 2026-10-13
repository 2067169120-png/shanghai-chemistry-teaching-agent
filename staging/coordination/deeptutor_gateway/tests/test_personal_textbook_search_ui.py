"""Search/locate without changing teacher selection, edited notes or target."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QDialog
from PySide6.QtCore import Qt
from test_personal_textbook_ui import app, panel, book_fixture, Bridge, complete, enter_chapter, enter_note, facade_for
from test_desktop_studio_ui import settle
from test_personal_textbook_search import make_book, frozen_files
from test_personal_textbook_knowledge import native_fixture,draft_for,choose
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_panel import PersonalTextbookPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_search_dialog import PersonalTextbookSearchDialog


@pytest.mark.parametrize('batch', [False, True])
def test_personal_crop_result_cannot_cross_a_changed_preparation_target(panel, monkeypatch, batch):
    value, bridge, f = panel; emitted = []
    value.image_requested.connect(emitted.append); value.images_requested.connect(emitted.append)
    before = deepcopy(value._view)
    def dialog(*args, **kw):
        def execute():
            value.set_preparation_context({},target_changed=True)
            return QDialog.DialogCode.Accepted
        return SimpleNamespace(exec=execute, selection_request={'qa':'old-target'},
            selection_requests=[{'qa':'old-target'}], deleteLater=lambda:None)
    if batch:
        from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as crop
        monkeypatch.setattr(crop,'TextbookBatchCropDialog',dialog)
        value.crop_page.setValue(2); value._queue_crop_page(); value._crop_batch()
    else:
        from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_preparation_crop_dialog as crop
        monkeypatch.setattr(crop,'TextbookPreparationCropDialog',dialog); value._crop_page()
    assert emitted == [] and value._view == before


def run_search(dialog,bridge,text):
    dialog.query.setText(text);dialog.search()
    return complete(bridge,'查找个人教材原文与已保存整理')


def test_search_entry_locates_original_preserving_checks_crop_queue_reference_and_target(panel,app):
    value,bridge,f=panel;enter_chapter(value,bridge);enter_note(value,bridge)
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    value.preview_button.click();complete(bridge,'核对个人教材备课参考')
    value.crop_page.setValue(3);value._queue_crop_page()
    before=dict(checked=deepcopy(value._checked),queue=list(value._crop_pages),reference=deepcopy(value._reference),
        key=value._reference_key,target=value._preparation_target_epoch,section=value._section_id,note=value._note_id,
        crop_page=value.crop_page.value(),note_text=value.note_text.toPlainText())
    files=frozen_files(f);emitted=[];value.image_requested.connect(emitted.append);value.reference_requested.connect(emitted.append)
    value.search_book_button.click();dialog=value._search_dialog;assert dialog is not None and dialog.isVisible()
    assert dialog.scope.count()==2 and dialog.scope.currentData() is None
    run_search(dialog,bridge,'合成')
    assert dialog.result_page['counts']==dict(original_text=0,section=1,knowledge_note=1)
    assert '没有可检索的原生文字' in dialog.status.text()
    dialog.list.setCurrentRow(1);assert 'A → B' in dialog.details.toPlainText()
    assert dialog.pages.currentData()==2
    dialog.locate();complete(bridge,'重新核对教材查找定位');app.processEvents()
    complete(bridge,'读取教材原页');app.processEvents()
    assert value._reader.current_page_crop_request()['pdf_page']==2
    assert dict(checked=deepcopy(value._checked),queue=list(value._crop_pages),reference=deepcopy(value._reference),
        key=value._reference_key,target=value._preparation_target_epoch,section=value._section_id,note=value._note_id,
        crop_page=value.crop_page.value(),note_text=value.note_text.toPlainText())==before
    assert not emitted and frozen_files(f)==files
    dialog.reject();assert value._search_dialog is None


def test_all_book_and_current_chapter_search_native_pages_beyond_eight(app,tmp_path):
    f=make_book(tmp_path,['' for _ in range(8)]+['Scope page 9','Scope page 10','EDGE at page eleven','EDGE at last page'])
    view=f.study.save_section(f.selection,f.study.book_study(f.selection)['study_revision'],title='当前章',
        pdf_start=9,pdf_end=10,range_confirmed=True)
    bridge=Bridge();value=PersonalTextbookPanel(f.facade,bridge);value.show();value.set_book(f.study.books()[0])
    complete(bridge,'读取个人教材章节与笔记');value._open_book_search();dialog=value._search_dialog
    run_search(dialog,bridge,'EDGE');assert [r['pdf_pages'] for r in dialog.result_page['items']]==[[11],[12]]
    assert '共12页' in dialog.summary.text() and '2项匹配' in dialog.summary.text()
    dialog.scope.setCurrentIndex(1);dialog.search();complete(bridge,'查找个人教材原文与已保存整理')
    assert dialog.result_page['pdf_pages']==[9,10] and dialog.result_page['total']==0
    assert '没有找到匹配' in dialog.status.text()
    value.close();value.deleteLater();app.processEvents()


def test_search_and_cancel_keep_unsaved_teacher_note_edits(panel):
    value,bridge,f=panel;enter_chapter(value,bridge);enter_note(value,bridge)
    value.note_title.setText('尚未保存的新讲解');value.note_text.setPlainText('教师正在补充的条件与例证。')
    edited=(value.note_title.text(),value.note_text.toPlainText(),value.note_page.value(),value.note_confirmed.isChecked())
    value._open_book_search();dialog=value._search_dialog;run_search(dialog,bridge,'合成')
    dialog.reject()
    assert (value.note_title.text(),value.note_text.toPlainText(),value.note_page.value(),value.note_confirmed.isChecked())==edited
    assert f.study.book_study(f.selection)['notes'][0]['title']=='合成笔记'


def test_query_stop_close_and_busy_changes_drop_late_results(panel):
    value,bridge,f=panel;enter_chapter(value,bridge);value._open_book_search();dialog=value._search_dialog
    dialog.query.setText('合成');dialog.search();old=bridge.take('查找个人教材原文与已保存整理')
    dialog.query.setText('不存在');dialog.search();new=bridge.take('查找个人教材原文与已保存整理')
    bridge.succeed(old,run=True);assert dialog.result_page is None and old.task_id in bridge.cancelled
    bridge.succeed(new,run=True);assert dialog.result_page['query']=='不存在'
    dialog.search();pending=bridge.take('查找个人教材原文与已保存整理');dialog.stop();bridge.succeed(pending,run=True)
    assert dialog._task is None and '已停止' in dialog.status.text()
    dialog.search();pending=bridge.take('查找个人教材原文与已保存整理');value._busy=True
    bridge.succeed(pending,run=True);assert dialog.result_page is None and dialog._task is None and '状态已变化' in dialog.status.text()
    value._busy=False;dialog.search();pending=bridge.take('查找个人教材原文与已保存整理');dialog.reject();bridge.succeed(pending,run=True)
    assert value._search_dialog is None and dialog.result_page is None


def test_book_switch_rejects_search_and_late_location_from_old_source(panel,app,tmp_path):
    value,bridge,f=panel;enter_chapter(value,bridge);value._open_book_search();dialog=value._search_dialog
    run_search(dialog,bridge,'合成');dialog.locate();pending=bridge.take('重新核对教材查找定位')
    from test_personal_textbook_search import text_pdf
    other=text_pdf(tmp_path/'other.pdf',['Different source'])
    f.workspace.commit_books(f.workspace.preview_books([other]))
    book=next(b for b in f.study.books() if b['source_id']!=f.selection['source_id'])
    value.set_book(book);complete(bridge,'读取个人教材章节与笔记');bridge.succeed(pending,run=True)
    assert value._search_dialog is None and value._book['source_id']==book['source_id'] and value._reader is None


def test_location_rechecks_archive_and_discards_invalid_result_without_parent_change(panel):
    value,bridge,f=panel;enter_chapter(value,bridge);value._open_book_search();dialog=value._search_dialog
    run_search(dialog,bridge,'合成');before=deepcopy(value._view)
    archive=f.workspace.root/(f.selection['revision']+'.pdf');archive.write_bytes(b'changed archive')
    dialog.locate();task=bridge.take('重新核对教材查找定位')
    with pytest.raises(ValueError) as failure:task.operation()
    bridge.fail(task,str(failure.value))
    assert dialog.result_page is None and not dialog.locate_button.isEnabled() and value._view==before and value._reader is None
    assert f.source.read_bytes()==f.original


@pytest.mark.parametrize('text',['','x'*161])
def test_empty_and_long_queries_have_plain_search_status_without_reading(panel,text):
    value,bridge,f=panel;value._open_book_search();dialog=value._search_dialog
    dialog.query.setText(text);dialog.search();task=bridge.take('查找个人教材原文与已保存整理')
    with pytest.raises(ValueError) as failure:task.operation()
    bridge.fail(task,str(failure.value))
    assert '1—160' in dialog.status.text() and not dialog.list.count() and dialog._task is None


def test_native_result_pagination_never_silently_hides_more_than_twenty_five(app,tmp_path):
    f=make_book(tmp_path,[f'EDGE page {i}' for i in range(31)]);bridge=Bridge()
    view=f.study.book_study(f.selection)
    dialog=PersonalTextbookSearchDialog(f.facade,bridge,f.selection,view);dialog.show()
    run_search(dialog,bridge,'EDGE');assert dialog.list.count()==25 and dialog.next.isEnabled() and '31项' in dialog.page_label.text()
    dialog.change_page(1);complete(bridge,'查找个人教材原文与已保存整理')
    assert dialog.list.count()==6 and dialog.previous.isEnabled() and not dialog.next.isEnabled()
    dialog.list.setCurrentRow(5);requests=[];dialog.page_requested.connect(requests.append)
    dialog.locate();complete(bridge,'重新核对教材查找定位')
    assert requests[0]['pdf_page']==31 and requests[0]['result']['kind']=='original_text'
    dialog.reject();dialog.deleteLater();app.processEvents()


def test_multi_page_saved_note_can_locate_each_original_page_with_no_auto_selection(app,native_fixture):
    f=native_fixture;service,view,draft=draft_for(f)
    view=service.adopt(f.selection,view['study_revision'],draft['draft_id'],draft['revision'],choose(draft))['view']
    facade=facade_for(f);bridge=Bridge();value=PersonalTextbookPanel(facade,bridge);value.show()
    value.set_book(f.study.books()[0]);complete(bridge,'读取个人教材章节与笔记')
    value._open_book_search();dialog=value._search_dialog;dialog.content.setCurrentIndex(2)
    run_search(dialog,bridge,'条件Y');assert dialog.list.count()==1 and dialog.pages.count()==2
    assert '条件Y' in dialog.details.toPlainText() and '已保存知识笔记' in dialog.details.toPlainText()
    dialog.pages.setCurrentIndex(1);dialog.locate();complete(bridge,'重新核对教材查找定位');app.processEvents()
    complete(bridge,'读取教材原页');app.processEvents()
    assert value._reader.current_page_crop_request()['pdf_page']==3 and not value._checked and not value._crop_pages
    value.close();value.deleteLater();app.processEvents()


@pytest.mark.parametrize('width',[420,700])
def test_new_search_action_and_dialog_fit_narrow_width_without_hiding_native_page(panel,app,width):
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    value,bridge,f=panel;install_ui_font(app);value.setStyleSheet(WORKBENCH_STYLE)
    value.resize(width,960);value.read_book_button.click();app.processEvents();complete(bridge,'读取教材原页');app.processEvents()
    assert value.search_book_button.isVisible() and value.search_book_button.isEnabled()
    assert value.search_book_button.mapTo(value,value.search_book_button.rect().bottomRight()).x()<value.width()
    value._open_book_search();dialog=value._search_dialog;dialog.resize(width,780);run_search(dialog,bridge,'no-match')
    settle(app);assert dialog.width()==width and dialog.locate_button.isVisible() and not dialog.list.horizontalScrollBar().isVisible()
    assert value._reader._loaded_pages and value._reader.current_page_crop_request()['pdf_page']==1
    dialog.reject()


def test_search_target_can_change_but_late_old_crop_cannot_enter_it(panel,monkeypatch):
    value,bridge,f=panel;enter_chapter(value,bridge);value._open_book_search();dialog=value._search_dialog
    run_search(dialog,bridge,'合成');value.set_preparation_context({},target_changed=True)
    dialog.locate();complete(bridge,'重新核对教材查找定位')
    assert value._reader is not None and dialog.result_page is not None


def test_batch_remove_and_readd_same_page_rejects_old_pending_crop_result(panel,monkeypatch):
    value,bridge,f=panel;value.crop_page.setValue(2);value._queue_crop_page();emitted=[]
    value.images_requested.connect(emitted.append)
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as crop
    def factory(*args,**kw):
        def execute():value._clear_crop_pages();value._queue_crop_page();return QDialog.DialogCode.Accepted
        return SimpleNamespace(exec=execute,selection_requests=[{'qa':'old-queue'}],deleteLater=lambda:None)
    monkeypatch.setattr(crop,'TextbookBatchCropDialog',factory);value._crop_batch()
    assert emitted==[] and value._crop_pages==[2]
