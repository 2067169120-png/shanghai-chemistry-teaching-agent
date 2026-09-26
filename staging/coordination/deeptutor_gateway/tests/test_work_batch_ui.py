"""Real Qt navigation against isolated existing student records, no network."""
import threading
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication,QMessageBox
from test_student_review_desk_core import case
from test_desktop_studio_ui import settle
from test_work_batches import references,review
from integrations.deeptutor_shchem_v1.desktop_work_batches import WorkBatchStore
from integrations.deeptutor_shchem_v1.desktop_workbench.work_batch_desk import WorkBatchDialog,BatchMemberDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge

@pytest.fixture
def batch(case):
    app=QApplication.instance() or QApplication([])
    f,_,subs,_,_=case;store=WorkBatchStore(f)
    saved=store.save_batch('合成作业批次','高二测试班',references(subs))
    tasks=DesktopTaskBridge();d=WorkBatchDialog(f,tasks)
    d.show();settle(app,lambda:d.desk is not None and d.desk.viewer.loaded_identity is not None)
    yield d,app,case
    tasks.wait_for_done(5000);settle(app,lambda:not d._busy and (not d.desk or not d.desk._busy))
    d.close();d.deleteLater();tasks.shutdown();app.processEvents()


def test_switch_student_stashes_but_does_not_record_score(batch):
    d,app,(f,_,subs,t,_)=batch;a=d.desk;key=a._current
    a._editors[key].score_edit.setText('.5');a._editors[key].score_reason.setText('甲学生未完成的理由')
    calls=t.calls;d.next.click()
    settle(app,lambda:d.desk is not None and d.desk.student_id==subs[1].student_id and d.desk.viewer.loaded_identity is not None)
    assert not d.desk._editors[d.desk._current].score_edit.text()
    d.previous.click();settle(app,lambda:d.desk is not None and d.desk.student_id==subs[0].student_id)
    assert d.desk._editors[key].score_reason.text()=='甲学生未完成的理由'
    assert review(f,subs[0]).items[0].latest_teacher_score is None and t.calls==calls


def test_batch_reopen_recovers_pending_input_and_status(batch):
    d,app,case=batch;desk=d.desk;e=desk._editors[desk._current]
    e.score_reason.setText('重新打开仍可继续');desk.condition.setCurrentIndex(desk.condition.findData('not_taught'))
    desk.condition_note.setText('只写了一半的状态依据')
    d.reject();assert d._closed
    reopened=WorkBatchDialog(case[0],d.tasks);reopened.show()
    try:
        settle(app,lambda:reopened.desk is not None)
        e=reopened.desk._editors[reopened.desk._current]
        assert e.score_reason.text()=='重新打开仍可继续'
        assert reopened.desk.condition.currentData()=='not_taught'
        assert '一半' in reopened.desk.condition_note.text()
    finally:reopened.close();reopened.deleteLater()


def test_failed_stash_blocks_switch_without_losing_input(batch,monkeypatch):
    d,app,case=batch;desk=d.desk;desk._editors[desk._current].score_reason.setText('不能丢')
    monkeypatch.setattr(d.store,'save_pending',lambda *a,**k:(_ for _ in ()).throw(OSError('disk')))
    d.next.click();settle(app)
    assert d.desk is desk and d._current_index==0 and '不能丢'==desk._editors[desk._current].score_reason.text()
    d.reject();assert d.isVisible() and not d._closed


def test_saved_score_clears_only_its_pending_fields(batch):
    d,app,(f,_,subs,_,_)=batch;desk=d.desk;first=desk._current
    desk._editors[first].score_edit.setText('.5');desk._editors[first].score_reason.setText('已核对甲')
    desk.next.click();settle(app);second=desk._current;desk._editors[second].score_reason.setText('第二题仍未完成')
    desk.previous.click();desk.stash.click();desk.score_button.click()
    settle(app,lambda:desk._write_count==1 and not desk._busy)
    data=d.store.load_pending(subs[0])['changes']
    assert first not in data or 'score_edit' not in data[first]
    assert data[second]['score_reason']=='第二题仍未完成'
    assert review(f,subs[0]).items[0].latest_teacher_score==.5


def test_condition_saved_independent_of_score_and_other_students(batch):
    d,app,(f,_,subs,_,_)=batch;desk=d.desk
    desk.condition.setCurrentIndex(desk.condition.findData('missing_page'))
    desk.condition_note.setText('缺少答题纸背面');desk.condition_save.click();settle(app)
    assert d.store.conditions(subs[0])['items'][desk._current]['condition']=='missing_page'
    assert review(f,subs[0]).items[0].latest_teacher_score is None
    assert not d.store.conditions(subs[1]).get('items')


def test_clearing_pending_does_not_delete_formal_record(batch,monkeypatch):
    d,app,(f,_,subs,_,_)=batch;desk=d.desk;e=desk._editors[desk._current]
    e.score_edit.setText('1');e.score_reason.setText('正式理由');desk.score_button.click()
    settle(app,lambda:desk._write_count==1 and not desk._busy)
    desk._editors[desk._current].score_reason.setText('不要的暂存');desk.stash.click()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    desk.discard.click();settle(app)
    assert d.store.load_pending(subs[0])['changes']=={}
    assert review(f,subs[0]).items[0].latest_teacher_score==1


def test_switch_cannot_move_target_during_slow_score_write(batch,monkeypatch):
    d,app,(f,_,subs,_,_)=batch;desk=d.desk;entered=threading.Event();release=threading.Event()
    original=f.record_student_score
    def slow(**kw):entered.set();assert release.wait(5);return original(**kw)
    monkeypatch.setattr(f,'record_student_score',slow)
    e=desk._editors[desk._current];e.score_edit.setText('1');e.score_reason.setText('等待保存')
    try:
        desk.score_button.click();settle(app,entered.is_set);d.next.click();settle(app)
        assert d.desk is desk and d._current_index==0
        release.set();settle(app,lambda:not desk._busy)
        assert review(f,subs[0]).items[0].latest_teacher_score==1
        assert review(f,subs[1]).items[0].latest_teacher_score is None
    finally:release.set()


def test_loading_failure_clears_previous_student_image(batch,monkeypatch):
    d,app,(f,_,subs,_,_)=batch
    monkeypatch.setattr(f,'student_submission',lambda **k:(_ for _ in ()).throw(ValueError('unreadable')))
    d.next.click();settle(app,lambda:not d._busy)
    assert d.desk is None and d.placeholder.isVisible()
    assert '上一名' in d.placeholder.text()


def test_compact_batch_has_visible_image_status_and_save_actions(batch):
    d,app,_=batch;d.resize(800,700);settle(app)
    assert d.size().width()==800 and d.size().height()==700
    for w in (d.student,d.previous,d.next,d.desk.viewer.view,d.desk.editor_stack,
              d.desk.score_button,d.desk.condition_save,d.desk.stash,d.back):
        assert w.isVisible()
        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))


def test_picker_filter_does_not_clear_checked_members(batch):
    d,app,_=batch;rows=d.store.available_submissions();picker=BatchMemberDialog(rows,d._batch,d)
    try:
        before=picker.selection();picker.search.setText('nothing matches');settle(app)
        assert picker.selection()==before and len(before)==2
        picker.title.clear();picker._accept();assert picker.result()==0
    finally:picker.close();picker.deleteLater()
