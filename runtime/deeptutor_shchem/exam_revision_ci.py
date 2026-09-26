"""Synthetic Windows correction -> review -> real two-audience Office export."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main(destination):
    import hashlib,json,tempfile,time,shutil
    from copy import deepcopy
    from datetime import date
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_dashboard import ExamDashboard
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    from integrations.deeptutor_shchem_v1.desktop_review_fixture import seed_review
    from integrations.deeptutor_shchem_v1.desktop_closure_fixture import seed_word
    from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
    from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup,record_attempt
    from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection
    from integrations.deeptutor_shchem_v1.desktop_exam_revision import task_freshness
    from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import _ACTIVE
    app=create_application(['exam-score-revision-probe'])
    output=Path(destination);output.mkdir(parents=True,exist_ok=True)
    errors=[];hook=sys.excepthook;sys.excepthook=lambda typ,value,tb:errors.append(str(value))
    def settle(test=lambda:True):
        for _ in range(8):app.processEvents();time.sleep(.025)
        limit=time.monotonic()+150
        while not test() and time.monotonic()<limit:app.processEvents();time.sleep(.03)
        assert test(),'UI operation did not finish'
    with tempfile.TemporaryDirectory(prefix='exam-revision-') as directory:
        temp=Path(directory);bridge=DesktopTaskBridge();d=None;f=None
        try:
            f,_,_,transport,_=seed_review(ROOT,temp/'state',temp/'sources',shared_paper=True)
            source,rows=seed_word(f,temp/'word');raw=source.read_bytes();calls=transport.calls
            f.add_word_questions_to_basket([{'key':rows[0]['key'],'revision':rows[0]['revision'],'points':2}])
            basket=deepcopy(f.basket());sentinel={'preview_id':'unrelated','preview_hash':'untouched'}
            f.state_store.save_draft(_ACTIVE,sentinel)
            exam=example(temp/'synthetic.xlsx')[2];d=ExamDashboard(f,bridge);d.accept_exam(exam)
            panel=d.followup_panel
            task=freeze_selection(create_followup(exam,'1',['S0001'],'核对动态平衡的判断依据',date.today().isoformat()),basket,[basket[0]['key']])
            task=record_attempt(task,'S0001',date.today().isoformat(),'missing',note='合成历史记录保留')
            assert panel.store(task)
            d.result={'candidate':{'summary':'SYNTHETIC_OLD_ADVICE','class_actions':[],'groups':[],
                'students':[],'next_lesson':'仅用于状态验收','retest':'人工安排','limitations':['合成软件验收']},
                '_local_evidence':d.advice_input_stamp()}
            d.result_scope=None;assert d.save_current();old_advice=deepcopy(d.result)
            d.show();d.resize(1280,860);d.tabs.setCurrentWidget(panel);settle()
            def export_now():
                panel.preview_button.click();settle(lambda:d._task is None)
                assert panel.preview is not None,d.status.text()
                viewer=panel._preview_dialog
                for index,audience in enumerate(('student','teacher')):
                    viewer.tabs.setCurrentIndex(index)
                    for page in range(1,len(viewer.review.pages[audience])+1):
                        viewer.page_selector.setValue(page);settle(lambda:viewer.review_page_button.isEnabled())
                        viewer.review_page_button.click()
                settle(lambda:viewer.confirm_button.isEnabled())
                viewer.confirm_button.click();settle(lambda:d._task is None and panel.approved)
                viewer.reject();panel.export.click();settle(lambda:d._task is None)
                assert panel.current()['exports'],d.status.text()
                return deepcopy(panel.current()['exports'][-1])
            first=export_now();original_task=deepcopy(panel.current())
            hashes={row['path']:hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() for row in first['artifacts']}
            d.tabs.setCurrentIndex(2);d.correct_button.click();settle()
            editor=d._score_dialog;editor.field.setCurrentIndex(editor.field.findData('1'))
            editor.missing.setChecked(False);editor.value.setText('0');editor.reason.setPlainText('按合成原作答更正漏扣分')
            assert editor.grab().save(str(output/'score-correction.png'))
            editor.save_button.click();settle()
            assert d.exam['students'][0]['scores']['1']==0 and d.current_result() is None
            assert panel.current()['attempts']==original_task['attempts'] and panel.current()['targets']==original_task['targets']
            assert task_freshness(d.exam,panel.current())['state']=='stale' and not panel.approved
            d.open_saved(d.exam['id'],task['id']);settle()
            assert d.result==old_advice and d.current_result() is None
            assert not panel.preview_button.isEnabled() and not panel.export.isEnabled()
            assert d.grab().save(str(output/'score-stale.png'))
            panel.evidence_button.click();settle();review=panel._evidence_dialog
            review.reason.setText('新的成绩仍提示需核对概念，教师决定保留安排')
            review.confirmed.setChecked(True);review.commit_button.click();settle()
            assert task_freshness(d.exam,panel.current())['state']=='current'
            assert d.current_result() is None and panel.preview_button.isEnabled() and not panel.export.isEnabled()
            second=export_now()
            assert second['preview_id']!=first['preview_id'] and len(panel.current()['exports'])==2
            assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest()==value for name,value in hashes.items())
            assert f.basket()==basket and f.state_store.snapshot()['drafts'][_ACTIVE]==sentinel
            assert source.read_bytes()==raw and transport.calls==calls and not errors
            copied=[]
            for row in second['artifacts']:
                role='student' if row['artifact_id'].startswith('student') else 'teacher'
                target=output/(role+Path(row['path']).suffix);shutil.copyfile(row['path'],target)
                copied.append({'file':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
            d.resize(800,700);settle();assert d.grab().save(str(output/'score-reviewed.png'))
            assert d.store.load(d.exam['id'])['followups']==d.followups
            (output/'acceptance.json').write_text(json.dumps({
                'scope':'Windows source; synthetic local Word; actual LibreOffice; no EXE release',
                'checks':{'original_export_retained':True,'explicit_correction_saved':True,
                    'stale_ai_excluded_after_reopen':True,'stale_task_blocks_preview_and_export':True,
                    'teacher_review_required':True,'review_does_not_revive_ai':True,
                    'new_preview_and_real_two_audience_export':True,'original_baseline_and_attempts_preserved':True,
                    'original_word_and_global_basket_unchanged':True,'unrelated_global_preview_unchanged':True,
                    'followup_history_persisted':True},
                'artifacts':copied,'network_model_calls':0,'uncaught_errors':errors,
            },ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            if d is not None:d.dirty=False;d.close();d.deleteLater()
            bridge.wait_for_done(5000);bridge.shutdown()
            if f is not None:f.shutdown()
            app.processEvents();sys.excepthook=hook
    return 0


if __name__=='__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv)>1 else 'revision-qa/office'))
