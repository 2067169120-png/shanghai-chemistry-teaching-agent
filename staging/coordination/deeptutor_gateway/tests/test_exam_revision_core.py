"""Content freshness and local corrections; only synthetic exam records."""
from copy import deepcopy
from datetime import date
import pytest
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, analyse
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup
from integrations.deeptutor_shchem_v1.desktop_exam_revision import (correct_score,
    full_mapping, score_revision, advice_revision, bind_task, task_freshness, review_task)


@pytest.fixture
def exam(tmp_path):
    return example(tmp_path/'synthetic.xlsx')[2]


def task_for(exam):
    return create_followup(exam,'1',['S0001'],'核对本题条件',date.today().isoformat())


def changed(exam, student='S0001', value=0):
    return correct_score(exam,student,'1',value,'按合成原作答核对录入')


def test_new_task_is_bound_and_changed_score_requires_review(exam):
    task=task_for(exam); before=deepcopy(task)
    assert task_freshness(exam,task)['state']=='current'
    revised=changed(exam)
    assert task_freshness(revised,task)['state']=='stale'
    checked=review_task(revised,task,'核对后继续保留该复练任务')
    assert task_freshness(revised,checked)['state']=='current'
    assert task==before and checked['targets']==task['targets']
    assert checked['_baseline_evidence']==task['_baseline_evidence']
    assert len(checked['_evidence_reviews'])==1


def test_full_mapping_recomputes_total_and_preserves_original_source(exam):
    assert full_mapping(exam)
    before=deepcopy(exam); report=analyse(exam)
    revised=changed(exam); row=revised['students'][0]
    assert row['total']==sum(row['scores'].values())
    assert analyse(revised)['overall']['mean']!=report['overall']['mean']
    assert revised['source']==exam['source'] and exam==before
    assert revised['score_changes'][0]['before']['score']==exam['students'][0]['scores']['1']
    assert revised['score_changes'][0]['after_revision']==score_revision(revised)


def test_partial_mapping_never_guesses_total_from_delta(exam):
    exam['questions']=exam['questions'][:1]
    revised=changed(exam)
    assert revised['students'][0]['total'] is None
    assert any(i['field']=='总分' for i in revised['issues'] if i['row']==revised['students'][0]['excel_row'])
    confirmed=correct_score(revised,'S0001','__total__',73,'已核对独立总分')
    assert confirmed['students'][0]['total']==73 and len(confirmed['score_changes'])==2


def test_missing_is_not_zero_and_blocks_reconfirmation(exam):
    task=task_for(exam); revised=changed(exam,value=None)
    assert revised['students'][0]['scores']['1'] is None and revised['students'][0]['total'] is None
    assert analyse(revised)['items'][0]['n']==analyse(exam)['items'][0]['n']-1
    assert task_freshness(revised,task)['state']=='blocked'
    with pytest.raises(ExamError):review_task(revised,task,'不能把缺失当作确认')


@pytest.mark.parametrize('value',[True,False,float('nan'),float('inf'),-1,10001,'3',[],{}])
def test_invalid_score_rejected_without_mutation(exam,value):
    before=deepcopy(exam)
    with pytest.raises(ExamError):changed(exam,value=value)
    assert exam==before


@pytest.mark.parametrize('reason',['',' ',None,True,'字'*2001])
def test_modifications_need_explicit_reason(exam,reason):
    with pytest.raises(ExamError):correct_score(exam,'S0001','1',0,reason)


def test_noop_does_not_create_fake_revision(exam):
    result=correct_score(exam,'S0001','1',exam['students'][0]['scores']['1'],'核对未变')
    assert result==exam and result is not exam and 'score_changes' not in result


def test_full_mapping_cannot_overwrite_total_independently(exam):
    with pytest.raises(ExamError):correct_score(exam,'S0001','__total__',50,'拒绝不一致总分')


def test_absent_student_cannot_be_turned_into_zero(exam):
    row=next(row for row in exam['students'] if row['absent'])
    with pytest.raises(ExamError):correct_score(exam,row['id'],'1',0,'仍须核对缺考状态')


def test_unrelated_student_does_not_invalidate_task(exam):
    task=task_for(exam)
    assert task_freshness(changed(exam,student='S0002'),task)['state']=='current'


def test_total_changes_also_invalidate_target_context(exam):
    exam['questions']=exam['questions'][:1]; task=task_for(exam)
    revised=correct_score(exam,'S0001','__total__',10,'核对总分')
    assert task_freshness(revised,task)['state']=='stale'


def test_mapping_change_requires_review_not_automatic_target_change(exam):
    task=task_for(exam); revised=deepcopy(exam); revised['questions'][0]['knowledge']='人工修订标签'
    assert task_freshness(revised,task)['state']=='stale'
    checked=review_task(revised,task,'已看原题，保留原目标')
    assert checked['goal']==task['goal'] and checked['knowledge']==task['knowledge']
    assert task_freshness(revised,checked)['state']=='current'


def test_legacy_without_stamp_is_unverified_not_assumed_fresh(exam):
    task=task_for(exam); task.pop('_baseline_evidence')
    assert task_freshness(exam,task)['state']=='unverified'
    checked=review_task(exam,task,'明确补核旧任务依据')
    assert '_baseline_evidence' not in checked and task_freshness(exam,checked)['state']=='current'


@pytest.mark.parametrize('damage',['hash','reviews','id','label','duplicate','question'])
def test_corrupt_or_ambiguous_evidence_cannot_be_silently_rebound(exam,damage):
    task=task_for(exam)
    if damage=='hash':task['_baseline_evidence']['sha256']='wrong'
    elif damage=='reviews':task['_evidence_reviews']={}
    elif damage=='id':task['exam_id']='another-exam'
    elif damage=='label':exam['students'][0]['local_label']='另一个学生'
    elif damage=='duplicate':exam['students'].append(deepcopy(exam['students'][0]))
    else:exam['questions']=[]
    assert task_freshness(exam,task)['state']=='blocked'
    with pytest.raises(ExamError):review_task(exam,task,'不允许静默修复')


def test_history_and_attempts_are_never_rewritten_on_review(exam):
    task=task_for(exam); task['exports']=[{'old':'file'}]; task['attempts']=[{'old':'observation'}]
    result=review_task(changed(exam),task,'仍保留安排')
    assert result['exports']==task['exports'] and result['attempts']==task['attempts']
    again=review_task(changed(exam),result,'无需重复记录')
    assert again==result


def test_advice_versions_cover_data_paper_notes_and_scope_but_not_audit_time(exam):
    paper={'text':'原题','pages':[]}; stamp=advice_revision(exam,paper,'备注',None)
    variants=[(changed(exam),paper,'备注',None),(exam,{'text':'改题','pages':[]},'备注',None),
              (exam,paper,'改备注',None),(exam,paper,'备注','合成班A')]
    assert all(advice_revision(*values)!=stamp for values in variants)
    copy=deepcopy(exam); copy['score_changes']=[{'at':'later'}]
    assert advice_revision(copy,paper,'备注',None)==stamp


def test_corrupt_score_history_does_not_get_replaced(exam):
    exam['score_changes']={'broken':True}
    with pytest.raises(ExamError):changed(exam)


def test_advice_versions_include_diagnostics(exam):
    original=advice_revision(exam,{},'',None)
    copy=deepcopy(exam);copy['warnings'].append('新核对提醒')
    assert advice_revision(copy,{},'',None)!=original
    copy=deepcopy(exam);copy['issues'].append({'row':2,'field':'总分','detail':'新异常'})
    assert advice_revision(copy,{},'',None)!=original
