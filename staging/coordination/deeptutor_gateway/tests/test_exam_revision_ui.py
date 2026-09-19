"""Native correction, stale result and task-review flow; no live model calls."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QFileDialog,QMessageBox,QDialog
from test_exam_dashboard_ui import desk
from test_desktop_studio_ui import settle
from test_exam_practice_set_ui import task_for,capture
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError,digest
from integrations.deeptutor_shchem_v1.desktop_exam_revision import task_freshness


def advice(d):
    return {'candidate':{'summary':'STALE_ADVICE_SENTINEL','class_actions':[], 'groups':[],
        'students':[],'next_lesson':'旧建议','retest':'旧复测安排','limitations':[]},
        '_local_evidence':d.advice_input_stamp()}


def seed(d):
    panel=d.followup_panel; task=task_for(d); assert panel.store(task)
    d.result=advice(d); d.result_scope=None; assert d.save_current()
    return task


def correction(d,app):
    d.tabs.setCurrentIndex(2); d.correct_button.click(); settle(app)
    dialog=d._score_dialog; dialog.student.setCurrentIndex(dialog.student.findData('S0001'))
    dialog.field.setCurrentIndex(dialog.field.findData('1')); dialog.missing.setChecked(False)
    dialog.value.setText('0'); dialog.reason.setPlainText('合成作答核对后更正')
    return dialog


def test_real_score_dialog_recomputes_and_stale_advice_survives_reopen(desk):
    d,app,_=desk; task=seed(d); before=d.report['overall']['mean']; old=deepcopy(d.result)
    dialog=correction(d,app); dialog.save_button.click(); settle(app)
    assert dialog.result()==QDialog.DialogCode.Accepted
    assert d.report['overall']['mean']!=before and d.current_result() is None
    assert d.result==old and '过期' in d.ai_result.toPlainText()
    assert task_freshness(d.exam,d.followup_panel.current())['state']=='stale'
    d.open_saved(d.exam['id'],task['id']); settle(app)
    assert d.current_result() is None and d.result==old
    assert d.followup_panel.evidence_button.isVisible() and not d.followup_panel.preview_button.isEnabled()


def test_cancel_changes_nothing(desk):
    d,app,_=desk; seed(d); before=d.store.load(d.exam['id'])
    dialog=correction(d,app); dialog.reject(); settle(app)
    assert d.store.load(d.exam['id'])==before and d.current_result() is not None


def test_empty_score_is_not_saved_as_zero(desk):
    d,app,_=desk; seed(d); before=deepcopy(d.exam)
    dialog=correction(d,app); dialog.value.clear(); dialog.save_button.click(); settle(app)
    assert dialog.isVisible() and d.exam==before and '空白不按0' in dialog.message.text()
    dialog.reject()


def test_save_failure_keeps_both_data_and_input(desk,monkeypatch):
    d,app,_=desk; seed(d); before=d.bundle(); previous=d._saved_revision
    dialog=correction(d,app)
    def fail(*args,**kwargs):raise ExamError('合成写入失败')
    monkeypatch.setattr(d.store,'save',fail)
    dialog.save_button.click(); settle(app)
    assert dialog.isVisible() and dialog.value.text()=='0' and d.bundle()==before
    assert d._saved_revision==previous and not d.dirty
    dialog.reject()


def test_external_edit_rejected_without_overwriting_file(desk):
    d,app,_=desk; seed(d); before=deepcopy(d.exam); dialog=correction(d,app)
    latest=d.store.load(d.exam['id']); latest['notes']='其他窗口的新内容'
    d.store.save(latest,expected_revision=d._saved_revision)
    dialog.save_button.click(); settle(app)
    assert dialog.isVisible() and '另一个窗口' in dialog.message.text()
    assert d.exam==before and d.store.load(d.exam['id'])==latest
    dialog.reject()


def test_teacher_review_restores_task_but_does_not_revive_ai(desk):
    d,app,_=desk; original=seed(d); d.apply_score_change('S0001','1',0,'核对录入')
    panel=d.followup_panel; d.tabs.setCurrentWidget(panel); panel.evidence_button.click(); settle(app)
    dialog=panel._evidence_dialog; dialog.reason.setText('改分后仍需核对概念，保留任务')
    assert not dialog.commit_button.isEnabled()
    dialog.confirmed.setChecked(True); dialog.commit_button.click(); settle(app)
    assert dialog.result()==QDialog.DialogCode.Accepted
    task=panel.current(); assert task_freshness(d.exam,task)['state']=='current'
    assert task['targets']==original['targets'] and task['attempts']==original['attempts']
    assert panel.preview_button.isEnabled() and not panel.export.isEnabled()
    assert d.current_result() is None


def test_stale_task_cannot_reuse_an_existing_approval(desk):
    d,app,_=desk; task=seed(d); panel=d.followup_panel
    d.apply_score_change('S0001','1',0,'合成变化')
    with pytest.raises(ExamError):panel.current_for(task,require_fresh=True)
    assert panel.current_for(task)==panel.current()  # Actual retest history may still be recorded.


def test_late_ai_result_is_not_relabelled_after_score_change(desk):
    d,app,_=desk; seed(d); old=deepcopy(d.result); stamp=d.advice_input_stamp(); saved=d._saved_revision
    d.apply_score_change('S0001','1',0,'核对录入')
    with pytest.raises(ExamError):d.accept_advice_result(advice(d),None,stamp,saved)
    assert d.result==old and d.current_result() is None


def test_recalculate_alone_does_not_reconfirm_downstream(desk):
    d,app,_=desk; seed(d); d.apply_score_change('S0001','1',0,'核对录入')
    before=deepcopy(d.followups); d.recalculate_button.click(); settle(app)
    assert d.followups==before and d.current_result() is None
    assert task_freshness(d.exam,before[0])['state']=='stale'


def test_stale_ai_not_in_export_or_preparation(desk,monkeypatch,tmp_path):
    d,app,_=desk; seed(d); d.apply_score_change('S0001','1',0,'核对录入')
    target=tmp_path/'report.html'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(target),''))
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    d.export(); text=target.read_text(encoding='utf-8')
    assert 'STALE_ADVICE_SENTINEL' not in text and '依据待核对' in text
    emitted=[]; d.preparation_requested.connect(emitted.append); d.to_preparation()
    assert emitted and 'STALE_ADVICE_SENTINEL' not in emitted[0] and '旧AI建议' in emitted[0]


def test_legacy_advice_remains_readable_but_not_current(desk):
    d,app,_=desk; seed(d); d.result.pop('_local_evidence'); assert d.save_current()
    d.open_saved(d.exam['id']); assert d.current_result() is None
    d.revision_history_button.click(); settle(app)
    assert d._revision_history_dialog.isVisible(); d._revision_history_dialog.accept()


def test_input_change_preserves_old_advice_history(desk):
    d,app,_=desk; seed(d); d.notes.setPlainText('新的教师说明')
    assert d.result is None and d.advice_history[-1]['result']['candidate']['summary']=='STALE_ADVICE_SENTINEL'
    assert d.save_current(); d.open_saved(d.exam['id']); assert len(d.advice_history)==1


def test_native_layout_and_screenshots(desk):
    d,app,_=desk; seed(d); dialog=correction(d,app)
    for width in (600,420):
        dialog.resize(width,540); settle(app); assert dialog.width()==width
        for widget in (dialog.student,dialog.field,dialog.value,dialog.reason,dialog.save_button):
            assert dialog.rect().contains(QRect(widget.mapTo(dialog,widget.rect().topLeft()),widget.size()))
        capture(dialog,f'score-correction-{width}.png')
    dialog.save_button.click(); settle(app)
    d.tabs.setCurrentWidget(d.followup_panel); d.resize(800,700); settle(app)
    assert d.followup_panel.text.height()>=150
    capture(d,'score-stale-followup.png')
    d.followup_panel.evidence_button.click(); settle(app)
    capture(d.followup_panel._evidence_dialog,'score-evidence-review.png')
    d.followup_panel._evidence_dialog.reject()
