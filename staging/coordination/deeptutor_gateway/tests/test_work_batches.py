"""Batch references, pending inputs and observations with actual student storage."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from test_student_review_desk_core import case
from integrations.deeptutor_shchem_v1.desktop_work_batches import WorkBatchStore, WorkBatchError, CONDITIONS


def references(subs):return [dict(student_id=s.student_id,submission_id=s.submission_id) for s in subs]

def review(f,s):return f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)


def test_batch_roundtrip_uses_existing_students_without_changing_sources(case):
    f,students,subs,t,files=case;store=WorkBatchStore(f);before=[p.read_bytes() for p in files];calls=t.calls
    batch=store.save_batch('周练第1次','高二测试班',references(subs))
    reopened=WorkBatchStore(f)
    assert reopened.get_batch(batch['batch_id'])==batch
    assert [m['label'] for m in reopened.members(batch)]==[s.label_zh for s in students]
    assert [p.read_bytes() for p in files]==before and t.calls==calls
    assert len(f.student_profiles())==2


def test_duplicate_student_and_wrong_owner_are_rejected(case):
    f,_,subs,_,_=case;store=WorkBatchStore(f)
    with pytest.raises(WorkBatchError):store.save_batch('a','',references([subs[0],subs[0]]))
    with pytest.raises(ValueError):store.save_batch('a','',[dict(student_id=subs[0].student_id,submission_id=subs[1].submission_id)])
    assert store.list_batches()==[]


def test_edit_batch_optimistic_revision_leaves_latest_members(case):
    f,_,subs,_,_=case;store=WorkBatchStore(f)
    b=store.save_batch('a','',references(subs))
    revised=store.save_batch('b','',references(subs[:1]),batch_id=b['batch_id'],expected_revision=b['revision'])
    with pytest.raises(WorkBatchError):store.save_batch('old','',references(subs),batch_id=b['batch_id'],expected_revision=b['revision'])
    assert store.get_batch(b['batch_id'])==revised


def test_history_picker_does_not_truncate_at_fifty(case):
    f,_,subs,_,_=case;store=WorkBatchStore(f)
    original=store.manager.list_submissions(subs[0].student_id)[0]
    old=store.manager.list_submissions
    store.manager.list_submissions=lambda sid:[dict(original,submission_id=f'sub_{i:032x}') for i in range(61)] if sid==subs[0].student_id else old(sid)
    rows=store.available_submissions()
    assert sum(r['student_id']==subs[0].student_id for r in rows)==61


def test_pending_is_not_a_score_and_never_crosses_students(case):
    f,_,subs,t,_=case;s=subs[0];r=review(f,s);store=WorkBatchStore(f);key=r.items[0].match_id
    changes={key:{'score_edit':'.5','score_reason':'未完成的理由'}}
    observed={key:{'condition':'missing_page','note':'待补第2页'}}
    calls=t.calls
    saved=store.save_pending(s,r,changes,observations=observed,expected_revision=None)
    loaded=WorkBatchStore(f).load_pending(s)
    assert loaded['changes']==changes and loaded['observations']==observed and not loaded['source_changed']
    assert review(f,s).items[0].latest_teacher_score is None
    assert store.load_pending(subs[1]).get('changes',{})=={}
    assert t.calls==calls and saved['revision']


def test_clear_pending_keeps_formal_score(case):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s);key=r.items[0].match_id
    p=store.save_pending(s,r,{key:{'score_edit':'1'}},expected_revision=None)
    r=f.record_student_score(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
        match_id=key,teacher_score=.5,reason='正式教师评分')
    store.save_pending(s,r,{},expected_revision=p['revision'])
    assert store.load_pending(s)['changes']=={} and review(f,s).items[0].latest_teacher_score==.5


def test_stale_pending_does_not_overwrite_new_input(case):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s);key=r.items[0].match_id
    p=store.save_pending(s,r,{key:{'score_reason':'new'}},expected_revision=None)
    with pytest.raises(WorkBatchError):store.save_pending(s,r,{key:{'score_reason':'old'}},expected_revision=None)
    assert store.load_pending(s)['changes'][key]['score_reason']=='new'


def test_write_failure_leaves_previous_pending(case,monkeypatch):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s);key=r.items[0].match_id
    p=store.save_pending(s,r,{key:{'score_reason':'original'}},expected_revision=None)
    original=store.manager._write_private_json
    monkeypatch.setattr(store.manager,'_write_private_json',lambda *a,**k:(_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(WorkBatchError):store.save_pending(s,r,{key:{'score_reason':'new'}},expected_revision=p['revision'])
    assert store.load_pending(s)['changes'][key]['score_reason']=='original'


@pytest.mark.parametrize('condition',list(CONDITIONS))
def test_status_never_silently_changes_grade_or_model(case,condition):
    f,_,subs,t,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s)
    calls=t.calls;before=review(f,s)
    data=store.save_condition(s,r.items[0].match_id,condition,'教师记录的事实',expected_revision=None)
    assert data['items'][r.items[0].match_id]['condition']==condition
    assert review(f,s)==before and t.calls==calls


def test_missing_status_reason_is_not_inferred(case):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s)
    with pytest.raises(WorkBatchError):store.save_condition(s,r.items[0].match_id,'not_attempted','',expected_revision=None)
    assert store.conditions(s).get('items',{})=={}


def test_edited_status_has_history_without_overwriting_score(case):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s);key=r.items[0].match_id
    a=store.save_condition(s,key,'missing_page','少一页',expected_revision=None)
    b=store.save_condition(s,key,'needs_review','已补齐，待复核',expected_revision=a['revision'])
    assert len(b['history'])==2 and b['history'][1]['previous']['condition']=='missing_page'
    with pytest.raises(WorkBatchError):store.save_condition(s,key,'unmarked','',expected_revision=a['revision'])


def test_wrong_review_and_unknown_fields_rejected(case):
    f,_,subs,_,_=case;store=WorkBatchStore(f)
    with pytest.raises(WorkBatchError):store.save_pending(subs[0],review(f,subs[1]),{},expected_revision=None)
    r=review(f,subs[0]);key=r.items[0].match_id
    with pytest.raises(WorkBatchError):store.save_pending(subs[0],r,{key:{'api_key':'no'}},expected_revision=None)
    with pytest.raises(WorkBatchError):store.save_pending(subs[0],r,{'unknown':{'score_edit':'1'}},expected_revision=None)


def test_stale_match_context_is_rejected_before_writing(case):
    f,_,subs,_,_=case;s=subs[0];store=WorkBatchStore(f);r=review(f,s)
    changed=replace(s,matches=tuple(replace(m,maximum_score=m.maximum_score+1) for m in s.matches))
    with pytest.raises(WorkBatchError):store.save_pending(changed,r,{},expected_revision=None)


def test_observation_input_survives_without_confirmation(case):
    f,_,subs,_,_=case;s=subs[0];r=review(f,s);store=WorkBatchStore(f);key=r.items[0].match_id
    store.save_pending(s,r,{},observations={key:{'condition':'unreadable','note':''}},expected_revision=None)
    assert store.load_pending(s)['observations'][key]['condition']=='unreadable'
    assert store.conditions(s).get('items',{})=={}
