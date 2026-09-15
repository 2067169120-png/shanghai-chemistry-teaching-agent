"""Exercise the real review entry and append-only decisions in an isolated work area."""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def run_probe(output):
    from PySide6.QtCore import QTimer, QRect
    from PySide6.QtWidgets import QApplication
    from .desktop_version import DESKTOP_VERSION
    from .desktop_review_fixture import seed_review
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.student_review_desk import StudentReviewDesk
    app=create_application(['student-review-verifier'])
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    errors=[];screenshots=[];checks={};geometry=[]
    oldhook=sys.excepthook;sys.excepthook=lambda typ,error,tb:errors.append(typ.__name__+': '+str(error))
    def settle(test=lambda:True):
        end=time.monotonic()+30
        for _ in range(6):app.processEvents();time.sleep(.025)
        while not test() and time.monotonic()<end:app.processEvents();time.sleep(.025)
        assert test(),'Review UI did not complete its operation'
    def capture(widget,name):
        path=output/name;assert widget.grab().save(str(path))
        screenshots.append(dict(file=name,sha256=sha256(path.read_bytes()).hexdigest()))
    with tempfile.TemporaryDirectory(prefix='review196-') as folder:
        root=Path(folder);workspace=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        facade,students,subs,transport,sources=seed_review(workspace,root/'state',root/'sources')
        before=[p.read_bytes() for p in sources];transport_calls=transport.calls
        manager=facade._student_manager_instance()
        candidate_before=json.dumps(manager.get_review(subs[0].student_id,subs[0].submission_id)['analysis'],sort_keys=True)
        win=TeacherWorkbenchWindow(facade);win.resize(1366,820);win.show();win.navigate('student')
        page=win.student_page
        def drive():
            desk=next((w for w in QApplication.topLevelWidgets() if isinstance(w,StudentReviewDesk) and w.isVisible()),None)
            if desk is None:errors.append('Native entry did not open');return
            try:
                settle(lambda:desk.viewer.loaded_identity is not None)
                assert not desk._editors[desk._current].score_edit.text()
                assert facade.student_analysis_profiles()==()
                capture(desk,'student-review-wide.png')
                desk.viewer.location.setCurrentIndex(4)
                settle(lambda:desk.viewer._box_item is not None)
                desk._tabs[desk._current].setCurrentIndex(1)
                # Selection changes the tab style; let Qt finish that layout
                # before checking/capturing the actual visible tab bounds.
                settle()
                bar=desk._tabs[desk._current].tabBar()
                assert bar.rect().contains(bar.tabRect(1)), 'Selected AI tab is clipped'
                capture(desk,'student-review-evidence.png')
                desk._tabs[desk._current].setCurrentIndex(0)
                first=desk._current
                desk._editors[first].score_edit.setText('0.5')
                desk._editors[first].score_reason.setText('合成验收：根据原作答修订，不采纳模型满分建议')
                desk.next.click();settle(lambda:desk.viewer.loaded_identity is not None)
                second=desk._current
                desk._editors[second].score_reason.setText('第二题未记录输入应保留')
                desk.previous.click();settle(lambda:desk.viewer.loaded_identity is not None)
                assert desk._editors[first].score_reason.text().startswith('合成验收')
                desk.score_button.click();settle(lambda:not desk._busy and desk._write_count==1)
                assert desk.review.items[0].latest_teacher_score==.5 and desk.review.items[0].suggested_score==1
                desk.next.click();settle()
                assert desk._editors[second].score_reason.text()=='第二题未记录输入应保留'
                desk._editors[second].score_edit.setText('2')
                desk.score_button.click();settle(lambda:not desk._busy and desk._write_count==2)
                e=desk._editors[second];e.decision.setCurrentIndex(e.decision.findData('edit'))
                e.result.setCurrentIndex(e.result.findData('correct'));e.teacher_note.setText('合成验收：教师已核对')
                desk.diagnosis_button.click();settle(lambda:not desk._busy and desk._write_count==3)
                capture(desk,'student-review-saved.png')
                for width,height in ((1366,768),(800,700)):
                    desk.resize(width,height);settle()
                    for w in (desk.viewer.view,desk.editor_stack,desk.score_button,desk.diagnosis_button,desk.close_button):
                        rect=QRect(w.mapTo(desk,w.rect().topLeft()),w.size())
                        assert desk.rect().contains(rect)
                    assert desk.viewer.view.isVisible() and desk.editor_stack.isVisible()
                    geometry.append({'requested':[width,height],'actual':[desk.width(),desk.height()],
                        'viewer_width':desk.viewer.width(),'editor_width':desk.editor_stack.width(),
                        'actions_visible':True})
                capture(desk,'student-review-compact.png')
                checks.update(native_entry=True,matched_original_page=True,evidence_overlay=True,
                    teacher_score_separate=True,other_inputs_preserved=True,diagnosis_recorded=True,
                    no_model_configuration_needed=True,actions_visible=True)
            except Exception as e:
                errors.append(type(e).__name__+': '+str(e));capture(desk,'student-review-failure.png')
            finally:
                # Synthetic verification teardown only; do not prompt about an
                # incomplete test case. Product close guards are tested separately.
                desk._closed=True;desk.viewer.stop();desk.reject()
        try:
            settle(lambda:page._context_task_id is None and page.student_combo.count()==2)
            index=next(i for i in range(page.student_combo.count()) if page.student_combo.itemData(i).student_id==students[0].student_id)
            page.student_combo.setCurrentIndex(index);settle(lambda:page._recent_task_id is None)
            page._open_recent(subs[0]);settle(lambda:page._current_review is not None and page._review_task_id is None)
            assert page.review_desk_button.isEnabled()
            capture(win,'student-review-entry.png')
            QTimer.singleShot(300,drive);page.review_desk_button.click()
            assert not errors,errors
            fresh=facade.student_analysis_review(student_id=subs[0].student_id,submission_id=subs[0].submission_id)
            assert [i.latest_teacher_score for i in fresh.items]==[.5,2]
            # A separate read/desk instance sees the same saved reasons.
            reopened=StudentReviewDesk(facade,win.tasks,subs[0],fresh,facade.student_curriculum_sections(),parent=win)
            reopened.show();settle(lambda:reopened.viewer.loaded_identity is not None)
            assert '合成验收' in fresh.items[0].latest_score_reason_zh
            reopened.close();reopened.deleteLater()
            other=facade.student_analysis_review(student_id=subs[1].student_id,submission_id=subs[1].submission_id)
            assert all(i.latest_teacher_score is None for i in other.items)
            assert candidate_before==json.dumps(manager.get_review(subs[0].student_id,subs[0].submission_id)['analysis'],sort_keys=True)
            assert [p.read_bytes() for p in sources]==before and transport.calls==transport_calls
            checks.update(reopened_saved_decisions=True,other_student_unchanged=True,
                          original_pages_unchanged=True,model_candidate_unchanged=True,no_review_model_calls=True)
            report={'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
                'frozen':getattr(sys,'frozen',False),'qt_platform':app.platformName(),
                'synthetic_students':2,'reviewed_items':2,'teacher_score_records':2,'teacher_diagnosis_records':1,
                'network_requests':0,'fixture_transport_requests':transport_calls,'review_transport_requests':transport.calls-transport_calls,
                'geometry':geometry,'checks':checks,'screenshots':screenshots,'uncaught_errors':errors,
                'scope':'Synthetic local manager and injected deterministic transport; no real grading or class aggregation.'}
            (output/'student-review-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            win.close();app.processEvents();sys.excepthook=oldhook
            if errors:(output/'review-errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0
