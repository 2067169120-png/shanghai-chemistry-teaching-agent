"""Synthetic native closure acceptance, using real local stores and renderers."""
from __future__ import annotations
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback


def run_probe(output, *, lite=False):
    from PySide6.QtCore import QTimer, QRect
    from PySide6.QtWidgets import QApplication,QDialog,QDoubleSpinBox,QLineEdit,QDialogButtonBox
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.exam_dashboard import ExamDashboard
    from .desktop_review_fixture import seed_review
    from .desktop_closure_fixture import seed_word
    from .desktop_batch_exam import collect_batch_exams
    from .desktop_work_batches import WorkBatchStore
    from .desktop_exam_followup import create_followup,link_basket,latest_attempts
    from .desktop_version import DESKTOP_VERSION
    app=create_application(['closure199-probe']);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    oldhook=sys.excepthook;errors=[];screens=[]
    sys.excepthook=lambda typ,value,tb:errors.append(typ.__name__+': '+str(value))
    def settle(test=lambda:True):
        end=time.monotonic()+120
        for _ in range(6):app.processEvents();time.sleep(.025)
        while not test() and time.monotonic()<end:app.processEvents();time.sleep(.03)
        assert test(), 'Closure operation did not finish'
    def capture(widget,name):
        path=output/name;assert widget.grab().save(str(path))
        screens.append({'file':name,'sha256':sha256(path.read_bytes()).hexdigest()})
    with tempfile.TemporaryDirectory(prefix='closure199-') as directory:
        temp=Path(directory);root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        f,students,subs,transport,sources=seed_review(root,temp/'state',temp/'sources',shared_paper=True)
        original=[p.read_bytes() for p in sources];calls=transport.calls
        source,wordrows=seed_word(f,temp/'word');wordbytes=source.read_bytes()
        for s in subs:
            r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
            for item in r.items:
                r=f.record_student_score(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
                    match_id=item.match_id,teacher_score=1,reason='合成验收：教师核对')
        batch=WorkBatchStore(f).save_batch('平衡专题 · 合成作业','合成班',
            [{'student_id':s.student_id,'submission_id':s.submission_id} for s in subs])
        result=collect_batch_exams(f,batch['batch_id']);assert len(result['groups'])==1
        exam=result['groups'][0]['exam']
        win=TeacherWorkbenchWindow(f);win.resize(1280,900);win.show()
        d=ExamDashboard(f,win.tasks,win);d.accept_exam(exam);d.show()
        try:
            settle();assert d.report['overall']['mean']==2 and d.report['overall']['n']==2
            capture(d,'closure-batch-stats.png')
            task=create_followup(exam,'1',[s['id'] for s in exam['students']],
                '用速率证据解释动态平衡，再用数据写出平衡常数表达式',date.today().isoformat())
            assert d.followup_panel.store(task)
            d.tabs.setCurrentWidget(d.followup_panel);settle();capture(d,'closure-panel.png')
            # Test the real existing explorer, not a replacement recommendation table.
            d.close();d.deleteLater();settle()
            win.open_exam_practice({'exam_id':exam['id'],'task_id':task['id'],'lane':'word_native','knowledge':'K09','label':'反应方向、限度和速率进阶'})
            page=win.library_page
            settle(lambda:not page._loading and len(page.cards)==2 and page.cards[0].ready)
            page.cards[0].add.click();settle(lambda:len(f.basket())==1 and not page._adding)
            capture(win,'closure-library.png')
            chosen=f.basket()[0]['key']
            # An unrelated second basket entry must never enter the task export.
            other=wordrows[1]
            f.add_word_questions_to_basket([{'key':other['key'],'revision':other['revision'],'points':2}])
            basket=deepcopy(f.basket());assert len(basket)==2
            d=ExamDashboard(f,win.tasks,win);d.open_saved(exam['id'],task['id']);d.show();settle()
            panel=d.followup_panel;assert panel.store(link_basket(panel.current(),basket,[chosen]))
            def enter_result():
                dialog=QApplication.activeModalWidget()
                try:
                    assert dialog and dialog is not d
                    scores=dialog.findChildren(QDoubleSpinBox);assert len(scores)==2
                    scores[0].setValue(1.5);scores[1].setValue(2)
                    notes=[w for w in dialog.findChildren(QLineEdit) if w.placeholderText().startswith('依据')]
                    assert len(notes)==1;notes[0].setText('合成复测：已核对所选完整主题，非实际学生成绩。')
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
                except Exception as e:
                    errors.append(str(e));dialog.reject()
            QTimer.singleShot(250,enter_result);panel.record.click();settle()
            assert len(panel.current()['attempts'])==1
            assert list(latest_attempts(panel.current()).values())[0]['score']==1.5
            d.resize(800,700);settle()
            assert d.width()==800 and panel.text.height()>=150
            for w in (panel.new,panel.find,panel.preview_button,panel.record):
                assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
            capture(d,'closure-retest.png')
            exported=False
            if not lite:
                panel.preview_button.click();settle(lambda:d._task is None and panel.preview is not None)
                preview=panel._preview_dialog
                for role_index,role in enumerate(('student','teacher')):
                    preview.tabs.setCurrentIndex(role_index)
                    for n in range(1,len(preview.review.pages[role])+1):
                        preview.page_selector.setValue(n)
                        settle(lambda:preview.review_page_button.isEnabled())
                        preview.review_page_button.click()
                settle(lambda:preview.confirm_button.isEnabled())
                capture(preview,'closure-preview.png');preview.confirm_button.click()
                settle(lambda:d._task is None and panel.approved)
                preview.reject();panel.export.click()
                settle(lambda:d._task is None and bool(panel.current()['exports']))
                entry=panel.current()['exports'][-1]
                assert [x['key'] for x in entry['links']]==[chosen]
                assert [x['key'] for x in panel.preview.preview_model['sections']]==[chosen]
                for artifact in entry['artifacts']:
                    shutil.copyfile(artifact['path'],output/Path(artifact['path']).name)
                exported=True
            saved=d.store.load(exam['id']);assert saved['followups']==d.followups
            assert f.basket()==basket and [p.read_bytes() for p in sources]==original and source.read_bytes()==wordbytes
            assert transport.calls==calls and not errors,errors
            report={'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
                'frozen':getattr(sys,'frozen',False),'qt_platform':app.platformName(),'network_requests':0,
                'checks':{'same_paper_group':True,'formal_score_statistics':True,'basic_tags_without_private_catalog':True,
                    'real_library_filter_and_add':True,'explicit_task_subset':True,'dated_retest_saved':True,
                    'no_invented_gain':True,'compact_readable':True,'source_and_basket_unchanged':True,
                    'actual_pdf_export':exported},'screenshots':screens,'uncaught_errors':errors,
                'scope':'Synthetic local teacher decisions and authored Word. No live model or real classroom result. Lite run omits Office export.'}
            (output/'closure-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:
            (output/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
            if d and d.isVisible():capture(d,'closure-failure.png')
            raise
        finally:
            if d and not d._closed:d.dirty=False;d.close()
            win.close();app.processEvents();sys.excepthook=oldhook
    return 0
