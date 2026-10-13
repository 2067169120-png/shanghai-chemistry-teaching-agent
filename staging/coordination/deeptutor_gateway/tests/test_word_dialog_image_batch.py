"""Native selection stays gated until every current stem image is displayed."""
from copy import deepcopy
import pytest
from test_word_question_dialog import _Facade, _Tasks, _asset, qt_app as qt_app
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog

LABEL="读取 Word 题目来源图片"

class ProgressTasks(_Tasks):
    def submit_progress(self,label,operation,*,on_progress=None,on_success=None,on_failure=None):
        task_id=self.submit(label,lambda:operation(on_progress,lambda:False),
                            on_success=on_success,on_failure=on_failure)
        self.pending[-1]['progress']=on_progress
        return task_id

def open_many(qt_app):
    facade=_Facade();row=facade.catalog['items'][0]
    row['question_blocks'][0]['assets']=[_asset('a'),_asset('b'),_asset('a')]
    tasks=ProgressTasks();dialog=WordQuestionDialog(facade,tasks)
    tasks.finish('读取 Word 逐题目录')
    return facade,tasks,dialog

def job(tasks):return next(p for p in tasks.pending if p['label']==LABEL)

def test_one_visible_batch_deduplicates_reads_and_displays_both_occurrences(qt_app):
    facade,tasks,dialog=open_many(qt_app)
    assert len([p for p in tasks.pending if p['label']==LABEL])==1
    assert not dialog.select_current_button.isEnabled()
    tasks.finish(LABEL)
    reads=[c for c in facade.calls if c[0]=='image']
    assert [c[-1] for c in reads]==['a','b']
    assert len(dialog._image_targets)==3
    assert dialog.select_current_button.isEnabled() and dialog.current_question_readiness()[0]
    dialog.reject()

def test_first_progress_image_cannot_unlock_incomplete_stem(qt_app):
    facade,tasks,dialog=open_many(qt_app);pending=job(tasks)
    result=facade.word_question_image('Q1','revision-Q1','a')
    pending['progress']({'asset_id':'a','result':result})
    assert not dialog.current_question_readiness()[0]
    assert not dialog.select_current_button.isEnabled()
    pending['progress']({'asset_id':'b','result':result})
    assert dialog.select_current_button.isEnabled()
    dialog.reject()

@pytest.mark.parametrize('fault',[{'asset_id':'b','failed':True},
                                {'asset_id':'b','result':{'bytes':b'invalid','mime_type':'image/png'}}])
def test_failed_or_invalid_native_picture_keeps_selection_blocked(qt_app,fault):
    facade,tasks,dialog=open_many(qt_app);pending=job(tasks)
    pending['progress']({'asset_id':'a','result':facade.word_question_image('Q1','revision-Q1','a')})
    pending['progress'](fault);pending['on_success']({'failed':1})
    assert not dialog.select_current_button.isEnabled()
    assert '失败' in dialog.current_question_readiness()[1]
    dialog.reject()

def test_missing_progress_images_become_visible_failure_at_completion(qt_app):
    facade,tasks,dialog=open_many(qt_app);pending=job(tasks)
    pending['progress']({'asset_id':'a','result':facade.word_question_image('Q1','revision-Q1','a')})
    pending['on_success']({'completed':1})
    assert not dialog.select_current_button.isEnabled()
    assert '失败' in dialog.current_question_readiness()[1]
    dialog.reject()

def test_cancelled_previous_question_cannot_poison_new_question(qt_app):
    facade,tasks,dialog=open_many(qt_app);old=job(tasks)
    dialog.question_list.setCurrentRow(1)
    assert old['task_id'] in tasks.cancelled
    before=deepcopy(dialog._current_image_failures)
    old['progress']({'asset_id':'b','failed':True})
    old['on_failure']('late failure')
    assert dialog._current_key=='Q2' and dialog._current_image_failures==before
    tasks.finish(LABEL,allow_cancelled=True)
    assert dialog._current_key=='Q2' and not dialog._current_displayed_images
    tasks.finish(LABEL)
    assert dialog.current_question_readiness()[0]
    dialog.reject()

def test_answer_batch_is_lazy_and_closing_ignores_late_results(qt_app):
    facade,tasks,dialog=open_many(qt_app)
    tasks.finish(LABEL)
    assert all(c[-1]!='Q1-answer' for c in facade.calls if c[0]=='image')
    dialog.tabs.setCurrentIndex(1);pending=job(tasks)
    dialog.reject();before=len(dialog._image_targets)
    pending['progress']({'asset_id':'Q1-answer','result':facade.word_question_image('Q1','revision-Q1','Q1-answer')})
    pending['on_success']({'completed':1})
    assert len(dialog._image_targets)==before
