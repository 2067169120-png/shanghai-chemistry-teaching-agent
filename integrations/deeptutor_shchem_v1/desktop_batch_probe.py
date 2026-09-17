"""Actual native batch entry, reference selection and persistent input checks."""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def run_probe(output):
    from PySide6.QtCore import Qt,QTimer,QRect
    from PySide6.QtWidgets import QApplication,QPushButton
    from .desktop_version import DESKTOP_VERSION
    from .desktop_review_fixture import seed_review
    from .desktop_work_batches import WorkBatchStore
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.work_batch_desk import WorkBatchDialog,BatchMemberDialog
    app=create_application(['batch197-verifier']);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    screenshots=[];errors=[];checks={};geometry=[]
    oldhook=sys.excepthook;sys.excepthook=lambda typ,error,tb:errors.append(typ.__name__+': '+str(error))
    def settle(predicate=lambda:True):
        until=time.monotonic()+40
        for _ in range(6):app.processEvents();time.sleep(.025)
        while not predicate() and time.monotonic()<until:app.processEvents();time.sleep(.025)
        assert predicate(),'Batch operation did not complete'
    def capture(widget,name):
        path=output/name;assert widget.grab().save(str(path))
        screenshots.append({'file':name,'sha256':sha256(path.read_bytes()).hexdigest()})
    with tempfile.TemporaryDirectory(prefix='batch197-') as folder:
        root=Path(folder);workspace=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        facade,students,subs,transport,sources=seed_review(workspace,root/'state',root/'sources')
        before=[p.read_bytes() for p in sources];calls=transport.calls
        manager=facade._student_manager_instance()
        before_model=json.dumps(manager.get_review(subs[0].student_id,subs[0].submission_id)['analysis'],sort_keys=True)
        win=TeacherWorkbenchWindow(facade);win.resize(1366,820);win.show();win.navigate('student')
        def drive():
            d=next((w for w in QApplication.topLevelWidgets() if isinstance(w,WorkBatchDialog) and w.isVisible()),None)
            if d is None:errors.append('Batch entry not opened');return
            try:
                settle(lambda:not d._busy)
                def fill():
                    picker=next((w for w in QApplication.topLevelWidgets() if isinstance(w,BatchMemberDialog) and w.isVisible()),None)
                    if picker is None:QTimer.singleShot(100,fill);return
                    picker.title.setText('合成验收 · 第一次练习');picker.class_label.setText('高二测试班')
                    assert picker.rows.count()==2
                    for i in range(picker.rows.count()):picker.rows.item(i).setCheckState(Qt.CheckState.Checked)
                    capture(picker,'work-batch-members.png')
                    next(b for b in picker.findChildren(QPushButton) if b.text()=='保存批次').click()
                QTimer.singleShot(300,fill);d.new.click()
                settle(lambda:d.desk is not None and d.desk.viewer.loaded_identity is not None)
                # The picker is sorted by alias, not by fixture tuple ordering.
                first=d.desk.summary;first_sid=first.student_id;key=d.desk._current
                d.desk._editors[key].score_edit.setText('.5')
                d.desk._editors[key].score_reason.setText('本机暂存：等待核对过程，不是正式得分')
                d.desk.condition.setCurrentIndex(d.desk.condition.findData('missing_page'))
                d.desk.condition_note.setText('教师观察：背面资料待补充')
                d.next.click();settle(lambda:d.desk is not None and d.desk.student_id!=first_sid and d.desk.viewer.loaded_identity is not None)
                second=d.desk.summary
                assert not d.desk._editors[d.desk._current].score_edit.text()
                d.desk._editors[d.desk._current].score_reason.setText('第二名学生的独立暂存')
                d.previous.click();settle(lambda:d.desk is not None and d.desk.student_id==first_sid and d.desk.viewer.loaded_identity is not None)
                assert d.desk._editors[key].score_edit.text()=='.5'
                assert d.desk.condition.currentData()=='missing_page'
                capture(d,'work-batch-restored.png')
                d.desk.score_button.click();settle(lambda:not d.desk._busy and d.desk._write_count==1)
                assert d.desk.review.items[0].latest_teacher_score==.5
                d.desk.condition_save.click();settle()
                condition=d.store.conditions(first)
                assert condition['items'][key]['condition']=='missing_page'
                capture(d,'work-batch-condition.png')
                for size in ((1366,768),(800,700)):
                    d.resize(*size);settle()
                    assert (d.width(),d.height())==size
                    for w in (d.student,d.previous,d.next,d.desk.viewer.view,d.desk.editor_stack,
                              d.desk.score_button,d.desk.stash,d.desk.condition_save,d.back):
                        assert w.isVisible() and d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
                    geometry.append({'size':list(size),'viewer_width':d.desk.viewer.width(),
                                     'actions_visible':True})
                    capture(d,'work-batch-wide.png' if size[0]>800 else 'work-batch-compact.png')
                checks.update(actual_main_entry=True,explicit_members_created=True,student_switch=True,
                    pending_preserved=True,status_input_preserved=True,recorded_score_separate=True,
                    no_missing_as_zero=True,actions_visible=True)
                d.back.click()
                assert d._closed
                # Independent window/state read after returning to main.
                again=WorkBatchDialog(facade,win.tasks,win);again.show()
                try:
                    settle(lambda:again.desk is not None)
                    again.choose_student(1);settle(lambda:again.desk is not None and again.desk.student_id==second.student_id)
                    assert again.desk._editors[again.desk._current].score_reason.text()=='第二名学生的独立暂存'
                    assert again.desk.review.items[0].latest_teacher_score is None
                    checks['pending_reopened']=True
                finally:again.close();again.deleteLater()
            except Exception as error:
                errors.append(type(error).__name__+': '+str(error));capture(d,'work-batch-failure.png')
                d._closed=True
                if d.desk:d.desk._busy=False
                d._busy=False;d._clear_desk();d.reject()
        try:
            settle(lambda:win.student_page._context_task_id is None)
            capture(win,'work-batch-entry.png')
            QTimer.singleShot(300,drive);win.student_page.work_batch_button.click()
            assert not errors,errors
            assert [p.read_bytes() for p in sources]==before and transport.calls==calls
            assert before_model==json.dumps(manager.get_review(subs[0].student_id,subs[0].submission_id)['analysis'],sort_keys=True)
            checks.update(original_pages_unchanged=True,model_candidate_unchanged=True,no_new_network_call=True)
            report={'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
                'frozen':getattr(sys,'frozen',False),'qt_platform':app.platformName(),'checks':checks,'geometry':geometry,
                'screenshots':screenshots,'uncaught_errors':errors,'synthetic_students':2,
                'network_requests':0,'fixture_transport_requests':calls,'review_transport_requests':transport.calls-calls,
                'scope':'Teacher-selected two-submission batch with deterministic local fixture, not a real class or bulk AI grading.'}
            (output/'work-batch-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            win.close();app.processEvents();sys.excepthook=oldhook
            if errors:(output/'batch-errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0
