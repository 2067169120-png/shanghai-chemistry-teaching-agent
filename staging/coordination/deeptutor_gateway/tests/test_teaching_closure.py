"""Real teacher records and exam-scoped retests, without API or UI substitutes."""
from copy import deepcopy
from datetime import date, timedelta
from dataclasses import replace
import pytest
from test_student_review_desk_core import case
from integrations.deeptutor_shchem_v1.desktop_batch_exam import collect_batch_exams
from integrations.deeptutor_shchem_v1.desktop_work_batches import WorkBatchStore
from integrations.deeptutor_shchem_v1.desktop_exam_data import analyse, ExamError, ExamStore
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_followup import (
    create_followup, link_basket, practice_request, record_attempt, latest_attempts, task_summary)


def make_batch(case):
    f,_,subs,_,_=case
    b=WorkBatchStore(f).save_batch('合成作业','合成班',[{'student_id':s.student_id,'submission_id':s.submission_id} for s in subs])
    return f,b,subs


def test_batch_groups_different_original_papers_and_does_not_use_model_scores(case):
    f,b,subs=make_batch(case)
    before=[p.read_bytes() for p in case[4]];calls=case[3].calls
    result=collect_batch_exams(f,b['batch_id'])
    assert len(result['groups'])==2  # fixture originals really have different bytes
    assert all(analyse(g['exam'])['overall']['n']==0 for g in result['groups'])
    assert all(s['total'] is None for g in result['groups'] for s in g['exam']['students'])
    assert [p.read_bytes() for p in case[4]]==before and case[3].calls==calls


def test_formal_scores_count_but_partial_and_observed_missing_are_not_zero(case):
    f,b,subs=make_batch(case);s=subs[0]
    r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    for item in r.items:
        r=f.record_student_score(student_id=s.student_id,submission_id=s.submission_id,
            expected_revision=r.revision,match_id=item.match_id,teacher_score=1,reason='核对原作答')
    result=collect_batch_exams(f,b['batch_id'])
    e=next(g['exam'] for g in result['groups'] if g['exam']['provenance']['members'][0]['student_id']==s.student_id)
    assert analyse(e)['overall']['mean']==2 and analyse(e)['overall']['n']==1
    store=WorkBatchStore(f)
    store.save_condition(s,r.items[0].match_id,'missing_page','缺少答题纸',expected_revision=None)
    e=next(g['exam'] for g in collect_batch_exams(f,b['batch_id'])['groups'] if g['exam']['provenance']['members'][0]['student_id']==s.student_id)
    assert e['students'][0]['total'] is None
    assert sum(i['n'] for i in analyse(e)['items'])==1
    assert any('资料缺页'==i['detail'] for i in e['issues'])


def test_cancelled_collection_does_not_write(case):
    f,b,_=make_batch(case)
    with pytest.raises(ExamError,match='停止'):collect_batch_exams(f,b['batch_id'],cancelled=lambda:True)
    assert WorkBatchStore(f).get_batch(b['batch_id'])==b


@pytest.fixture
def exam(tmp_path):return example(tmp_path/'synthetic.xlsx')[2]


def task(exam):
    target=next(s['id'] for s in exam['students'] if not s['absent'] and s['scores'].get('1') is not None)
    return create_followup(exam,'1',[target],'用题目中的数据解释条件变化',date.today().isoformat())


def test_task_requires_evidence_and_explicit_targets(exam):
    t=task(exam)
    assert t['exam_id']==exam['id'] and t['attempts']==[] and t['links']==[]
    assert t['targets'][0]['baseline_score'] is not None
    absent=next(s['id'] for s in exam['students'] if s['absent'])
    with pytest.raises(ExamError):create_followup(exam,'1',[absent],'目标',date.today().isoformat())
    with pytest.raises(ExamError):create_followup(exam,'99',['S0001'],'目标',date.today().isoformat())
    with pytest.raises(ExamError):create_followup(exam,'1',[],'目标',date.today().isoformat())


def test_link_and_preview_only_explicit_basket_subset(exam):
    t=task(exam);basket=[{'key':f'word:{i}','title_zh':f'真实完整题{i}','word_selection':{'key':str(i),'revision':'v1'}} for i in range(3)]
    before=deepcopy(basket)
    linked=link_basket(t,basket,['word:2','word:0'])
    req=practice_request(linked,basket,{'basket_sha256':'hash'})
    assert req['section_order']==['word:2','word:0'] and req['basket_sha256']=='hash'
    assert basket==before and t['links']==[]
    with pytest.raises(ExamError):link_basket(t,basket,['imaginary'])
    with pytest.raises(ExamError):practice_request(linked,basket[:2],{'basket_sha256':'hash'})
    basket[2]['word_selection']['revision']='changed'
    with pytest.raises(ExamError):practice_request(linked,basket,{'basket_sha256':'hash'})


def test_actual_retest_record_retains_history_and_missing_not_zero(exam,tmp_path):
    t=link_basket(task(exam),[{'key':'real','title_zh':'完整主题'}],['real']);sid=t['targets'][0]['exam_student_id']
    t1=record_attempt(t,sid,date.today().isoformat(),'not_attempted',0,10,'未完成')
    assert latest_attempts(t1)[sid]['score'] is None and '0/1名' in task_summary(t1)
    t2=record_attempt(t1,sid,date.today().isoformat(),'completed',6,8,'检查了所选两问，分值不同于原考试')
    assert latest_attempts(t2)[sid]['score']==6 and len(t2['attempts'])==2
    assert 'improvement' not in t2 and '1/1名' in task_summary(t2)
    store=ExamStore(tmp_path);store.save({'exam':exam,'followups':[t2]})
    assert store.load(exam['id'])['followups']==[t2]
    assert t['attempts']==[]


@pytest.mark.parametrize('score,maximum',[(None,8),(True,8),(-1,8),(9,8),(float('nan'),8),(1,0)])
def test_invalid_retest_never_counted(exam,score,maximum):
    t=link_basket(task(exam),[{'key':'real'}],['real']);sid=t['targets'][0]['exam_student_id']
    with pytest.raises(ExamError):record_attempt(t,sid,date.today().isoformat(),'completed',score,maximum,'依据')
    assert t['attempts']==[]


def test_future_or_unlinked_retest_rejected(exam):
    t=task(exam);sid=t['targets'][0]['exam_student_id']
    with pytest.raises(ExamError):record_attempt(t,sid,date.today().isoformat(),'completed',1,2,'依据')
    with pytest.raises(ExamError):record_attempt(t,sid,(date.today()+timedelta(days=1)).isoformat(),'not_attempted')


def test_exam_revision_check_preserves_newer_followups(exam,tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_exam_data import digest
    store=ExamStore(tmp_path);old={'exam':exam,'followups':[]};store.save(old)
    changed={**old,'followups':[task(exam)]}
    store.save(changed,expected_revision=digest(old))
    with pytest.raises(ExamError):store.save(old,expected_revision=digest(old))
    assert store.load(exam['id'])==changed


def test_followup_html_escapes_names_and_does_not_fake_gain(exam):
    from integrations.deeptutor_shchem_v1.desktop_exam_followup import followup_html
    t=task(exam);t['goal']='<script>alert(1)</script>'
    html=followup_html([t]);assert '<script>' not in html and '&lt;script&gt;' in html
    assert t['targets'][0]['local_label'] not in html
    assert '不同题目' in html


def test_shared_originals_form_one_group_and_existing_word_tags_remain_real(tmp_path):
    from pathlib import Path
    from integrations.deeptutor_shchem_v1.desktop_review_fixture import seed_review
    from integrations.deeptutor_shchem_v1.desktop_closure_fixture import seed_word
    from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results
    root=Path(__file__).resolve().parents[4]
    f,students,subs,transport,sources=seed_review(root,tmp_path/'state',tmp_path/'sources',shared_paper=True)
    try:
        b=WorkBatchStore(f).save_batch('同卷','合成班',[{'student_id':s.student_id,'submission_id':s.submission_id} for s in subs])
        assert len(collect_batch_exams(f,b['batch_id'])['groups'])==1
        path,rows=seed_word(f,tmp_path/'word')
        matched=personal_results(f.word_question_catalog(),'word_native',{'knowledge':['K09']},'')
        assert {r['key'] for r in matched['entries']}=={r['key'] for r in rows}
        assert len(rows)==2
    finally:f.shutdown()


def test_clean_install_keeps_known_k_ids_but_does_not_invent_textbook(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import load_attribute_catalog
    from integrations.deeptutor_shchem_v1.question_search_workbench import VALUE_LABELS_ZH
    c=load_attribute_catalog(tmp_path,allow_builtin=True)
    assert len(c['knowledge_points'])==19 and c['nodes']==[] and len(c['warnings'])==2
    assert c['knowledge_points'][8]=={'id':'K09','name':VALUE_LABELS_ZH['K09']}
    assert list(tmp_path.iterdir())==[]


def test_invalid_installed_taxonomy_is_not_silently_replaced(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import load_attribute_catalog
    p=tmp_path/'sh-chem-db/kb/knowledge_taxonomy.json';p.parent.mkdir(parents=True);p.write_text('{invalid')
    with pytest.raises(ValueError):load_attribute_catalog(tmp_path,allow_builtin=True)
    assert p.read_text()=='{invalid'
