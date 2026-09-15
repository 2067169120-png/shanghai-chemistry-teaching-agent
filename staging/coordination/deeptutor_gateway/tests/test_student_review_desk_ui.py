"""Actual Qt review desk against real isolated student storage."""
import threading
from dataclasses import replace
from pathlib import Path
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog
from test_desktop_studio_ui import settle
from test_student_review_desk_core import case
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.student_review_desk import StudentReviewDesk

@pytest.fixture
def desk(case,monkeypatch):
    app=QApplication.instance() or QApplication([])
    f,students,subs,transport,sources=case
    s=subs[0];r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    tasks=DesktopTaskBridge()
    d=StudentReviewDesk(f,tasks,s,r,f.student_curriculum_sections(),student_label=students[0].label_zh)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    d.show();settle(app,lambda:d.viewer.loaded_identity is not None)
    yield d,app,case
    tasks.wait_for_done(5000);settle(app,lambda:not d._busy)
    d.close();d.deleteLater();tasks.shutdown();app.processEvents()


def test_original_image_and_grading_share_the_window_with_empty_teacher_score(desk):
    d,app,case=desk;e=d._editors[d._current]
    assert d.viewer.view.isVisible() and e.score_edit.isVisible()
    assert not e.score_edit.text() and e.decision.currentData()=='pending'
    assert e.result.currentData()=='not_scored'
    assert d.viewer.loaded_identity[1]==d.summary.matches[0].student_work_page_sha256
    assert d.viewer.location.count()==5 and case[0].student_analysis_profiles()==()


def test_navigation_preserves_other_question_inputs_and_viewed_page(desk):
    d,app,_=desk;first=d._current;e=d._editors[first]
    e.score_edit.setText('.5');e.score_reason.setText('尚未记录的评分理由')
    d.viewer.role.setCurrentIndex(1);settle(app,lambda:d.viewer.loaded_identity is not None)
    identity=d.viewer.loaded_identity
    d.next.click();settle(app,lambda:d.viewer.loaded_identity is not None)
    assert d._current!=first and not d._editors[d._current].score_edit.text()
    d.previous.click();settle(app,lambda:d.viewer.loaded_identity==identity)
    assert d._editors[first].score_reason.text()=='尚未记录的评分理由'
    assert d.viewer.role.currentData()=='question_pages'


def test_saving_one_score_does_not_discard_other_match_or_diagnosis_inputs(desk):
    d,app,case=desk;first=d._current
    d._editors[first].teacher_note.setText('后续核对的诊断备注')
    d.next.click();settle(app);second=d._current
    d._editors[second].score_reason.setText('第二题暂未写完')
    d.previous.click();settle(app)
    e=d._editors[first];e.score_edit.setText('0.5');e.score_reason.setText('依据原作答修订')
    calls=case[3].calls;d.score_button.click();settle(app,lambda:not d._busy and d._write_count==1)
    assert d.review.items[0].latest_teacher_score==.5 and d.review.items[0].suggested_score==1
    assert d._editors[first].teacher_note.text()=='后续核对的诊断备注'
    d.next.click();settle(app)
    assert d._editors[second].score_reason.text()=='第二题暂未写完'
    assert case[3].calls==calls


def test_failed_write_keeps_input_and_can_retry_after_refresh(desk,monkeypatch):
    d,app,case=desk;e=d._editors[d._current]
    e.score_edit.setText('1');e.score_reason.setText('保存失败也不能丢')
    original=case[0].record_student_score
    def fail(**kw):raise DesktopFacadeError('test_write','测试写入失败')
    monkeypatch.setattr(case[0],'record_student_score',fail)
    d.score_button.click();settle(app,lambda:not d._busy)
    assert e.score_reason.text()=='保存失败也不能丢' and d._write_count==0
    d.reload.click();settle(app,lambda:not d._busy)
    assert d._editors[d._current].score_reason.text()=='保存失败也不能丢'
    monkeypatch.setattr(case[0],'record_student_score',original)
    d.score_button.click();settle(app,lambda:not d._busy and d._write_count==1)


def test_empty_score_is_not_silently_filled_from_model(desk):
    d,app,_=desk;d.score_button.click();settle(app)
    assert d._write_count==0 and not d._editors[d._current].score_error.isHidden()


def test_cancel_close_keeps_unsaved_inputs(desk,monkeypatch):
    d,app,_=desk;e=d._editors[d._current];e.score_reason.setText('未记录')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    d.reject();settle(app)
    assert d.isVisible() and e.score_reason.text()=='未记录'


def test_small_window_keeps_image_input_and_fixed_actions_visible(desk):
    d,app,_=desk;d.resize(800,700);settle(app)
    assert d.outline.isHidden() and d.viewer.view.isVisible()
    for w in (d.viewer.view,d.score_button,d.diagnosis_button,d.close_button):
        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
    assert d.viewer.width()>=240 and d.editor_stack.width()>=300


def test_slow_old_page_cannot_replace_new_question(desk,monkeypatch):
    d,app,case=desk;f=case[0];original=f.student_submission_page
    entered=threading.Event();release=threading.Event();old=d.viewer.loaded_identity[1]
    def slow(**kw):
        if kw['page_sha256']==old:entered.set();assert release.wait(5)
        return original(**kw)
    monkeypatch.setattr(f,'student_submission_page',slow)
    try:
        d.viewer.load_page();settle(app,entered.is_set)
        d.next.click();wanted=d.summary.matches[1].student_work_page_sha256
        settle(app,lambda:d.viewer.loaded_identity is not None and d.viewer.loaded_identity[1]==wanted)
        release.set();d.tasks.wait_for_done(3000);settle(app)
        assert d.viewer.loaded_identity[1]==wanted
    finally:release.set()


def test_evidence_overlay_does_not_replace_the_full_page(desk):
    d,app,_=desk;d.viewer.location.setCurrentIndex(4)
    settle(app,lambda:d.viewer.loaded_identity is not None and d.viewer._box_item is not None)
    assert d.viewer.scene.sceneRect().width()==880
    d.viewer.location.setCurrentIndex(0);settle(app)
    assert d.viewer._box_item is None and d.viewer._pixmap_item is not None


def test_wrong_submission_is_rejected_before_loading_images(case):
    app=QApplication.instance() or QApplication([]);f,_,subs,_,_=case
    r=f.student_analysis_review(student_id=subs[1].student_id,submission_id=subs[1].submission_id)
    tasks=DesktopTaskBridge()
    with pytest.raises(ValueError):StudentReviewDesk(f,tasks,subs[0],r)
    tasks.shutdown()


def test_missing_page_clears_previous_picture_instead_of_reusing_it(desk,monkeypatch):
    d,app,case=desk
    monkeypatch.setattr(case[0],'student_submission_page',lambda **kw:(_ for _ in ()).throw(OSError('missing')))
    d.viewer.load_page();settle(app,lambda:d.viewer._task is None)
    assert d.viewer.loaded_identity is None and d.viewer._pixmap_item is None
    assert '上一题' in d.viewer.notice.text()
