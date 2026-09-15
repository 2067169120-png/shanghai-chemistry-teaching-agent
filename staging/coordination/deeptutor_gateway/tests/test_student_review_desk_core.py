"""Real local student decisions plus exact-page projection; no Qt/network required."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import math
import pytest
from integrations.deeptutor_shchem_v1.desktop_review_evidence import evidence_regions, matched_page, review_counts
from integrations.deeptutor_shchem_v1.desktop_review_fixture import seed_review
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError

@pytest.fixture
def case(tmp_path):
    root=Path(__file__).resolve().parents[4]
    facade,students,submissions,transport,sources=seed_review(root,tmp_path/'state',tmp_path/'sources')
    yield facade,students,submissions,transport,sources
    facade.shutdown()


def test_teacher_reason_is_independent_of_candidate_and_survives_reread(case):
    f,students,subs,t,sources=case;sub=subs[0]
    before=[p.read_bytes() for p in sources]
    review=f.student_analysis_review(student_id=sub.student_id,submission_id=sub.submission_id)
    item=review.items[0];calls=t.calls
    changed=f.record_student_score(student_id=sub.student_id,submission_id=sub.submission_id,
        expected_revision=review.revision,match_id=item.match_id,teacher_score=.5,reason='教师依据可见条件判0.5分')
    assert changed.items[0].suggested_score==item.suggested_score==1
    assert changed.items[0].latest_teacher_score==.5
    assert changed.items[0].latest_score_reason_zh=='教师依据可见条件判0.5分'
    assert f.student_analysis_review(student_id=sub.student_id,submission_id=sub.submission_id)==changed
    assert t.calls==calls and [p.read_bytes() for p in sources]==before
    assert f.student_analysis_review(student_id=subs[1].student_id,submission_id=subs[1].submission_id).items[0].latest_teacher_score is None


def test_score_revision_conflict_does_not_overwrite_latest(case):
    f,_,subs,_,_=case;s=subs[0];r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    kw=dict(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
            match_id=r.items[0].match_id,teacher_score=1,reason='第一份决定')
    f.record_student_score(**kw)
    with pytest.raises(DesktopFacadeError):f.record_student_score(**{**kw,'teacher_score':0,'reason':'过期决定'})
    assert f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id).items[0].latest_teacher_score==1


def test_projection_contains_bounded_evidence_on_original_match_pages(case):
    f,_,subs,_,_=case;s=subs[0];r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    assert len(r.items[0].evidence_regions)==4
    for region in r.items[0].evidence_regions:
        page=matched_page(s,r.items[0].match_id,region.role)
        assert page.sha256==region.page_sha256
        assert region.page_sha256 not in repr(region)


def test_absent_scores_are_not_zero_and_missing_pair_does_not_guess(case):
    f,_,subs,_,_=case;s=subs[0];r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    assert review_counts(r)==dict(items=2,scored=0,unscored=2,recorded_score_sum=0,stale_diagnoses=0)
    assert matched_page(s,'not-a-match','student_work_pages') is None
    assert matched_page(s,r.items[0].match_id,'invalid-role') is None
    other=replace(s,matches=tuple(replace(m,student_work_page_sha256='missing') for m in s.matches))
    assert matched_page(other,r.items[0].match_id,'student_work_pages') is None


def test_diagnosis_note_preserved_and_new_score_invalidates_old_diagnosis(case):
    f,_,subs,_,_=case;s=subs[0];r=f.student_analysis_review(student_id=s.student_id,submission_id=s.submission_id)
    r=f.record_student_score(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
        match_id=r.items[0].match_id,teacher_score=2,reason='教师核对完整')
    r=f.record_student_diagnosis(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
        match_id=r.items[0].match_id,scoring_decision_id=r.items[0].latest_scoring_decision_id,
        decision='edit',result='correct',primary_error_type=None,secondary_error_types=(),curriculum_section_keys=(),teacher_note='以教师复核为准')
    assert r.items[0].latest_diagnostic_note_zh=='以教师复核为准'
    r=f.record_student_score(student_id=s.student_id,submission_id=s.submission_id,expected_revision=r.revision,
        match_id=r.items[0].match_id,teacher_score=1,reason='再次核对后修订')
    assert r.items[0].diagnostic_requires_reconfirmation
    assert not r.items[0].latest_diagnostic_note_zh


@pytest.mark.parametrize('box',[
    {'x':-.1,'y':0,'width':.2,'height':.2}, {'x':0,'y':0,'width':0,'height':.2},
    {'x':.9,'y':0,'width':.2,'height':.2}, {'x':True,'y':0,'width':.2,'height':.2},
    {'x':math.nan,'y':0,'width':.2,'height':.2}, {'x':0,'y':0,'width':math.inf,'height':.2},
    {},None,{'x':'0','y':0,'width':.2,'height':.2}])
def test_invalid_boxes_never_become_an_overlay(box):
    assert evidence_regions({'student_answer_anchor':{'page_sha256':'same','bbox':box}},
                            {'student_work_page_sha256':'same'})==()


def test_wrong_page_and_unbound_reference_are_ignored():
    b={'x':.1,'y':.1,'width':.2,'height':.2}
    assert evidence_regions({'student_answer_anchor':{'page_sha256':'other','bbox':b},
        'reference_answer_anchor':{'page_sha256':'same','bbox':b}}, {'student_work_page_sha256':'same'})==()
