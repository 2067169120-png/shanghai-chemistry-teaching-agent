from __future__ import annotations

from copy import deepcopy

import pytest
from test_student_recommendation_workbench import _catalog, _default_search

from integrations.deeptutor_shchem_v1.student_recommendation_projection import (
    project_visual_recommendation_payload,
)
from integrations.deeptutor_shchem_v1.student_recommendation_workbench import (
    StudentRecommendationWorkbench,
    StudentRecommendationWorkbenchError,
)


def _score(sequence: int, match: int = 1, score: float = 0) -> dict:
    return {
        "sequence": sequence,
        "decision_id": f"SVDEC-{sequence}",
        "analysis_id": "analysis-1",
        "match_id": f"match-{match}",
        "atomic_part_id": f"atomic-{match}",
        "teacher_score": score,
        "maximum_score": 1,
        "record_sha256": str(sequence) * 64,
    }


def _diagnosis(sequence: int, scoring: dict, action: str = "accept") -> dict:
    return {
        "sequence": sequence,
        "decision_id": f"SVDIAG-{sequence}",
        "analysis_id": "analysis-1",
        "match_id": scoring["match_id"],
        "atomic_part_id": scoring["atomic_part_id"],
        "scoring_decision_id": scoring["decision_id"],
        "scoring_decision_record_sha256": scoring["record_sha256"],
        "decision": action,
        "curriculum_sections": [
            {
                "section_key": "V1-C1:1.1",
                "volume_id": "V1",
                "volume_title_zh": "第1册",
                "chapter_id": "V1-C1",
                "chapter_title_zh": "第1章",
                "section_number": "1.1",
                "section_title": "第1节",
                "display_label_zh": "1.1 第1节",
            }
        ],
    }


def _inputs(count: int = 1) -> tuple[dict, dict]:
    scores = [_score(index, index) for index in range(1, count + 1)]
    return (
        {
            "submission_id": "SUB-1",
            "analysis": {
                "analysis_id": "analysis-1",
                "candidate": {
                    "matches": [
                        {
                            "match_id": f"match-{index}",
                            "atomic_part_id": f"atomic-{index}",
                        }
                        for index in range(1, count + 1)
                    ]
                },
            },
            "scoring_decisions": scores,
        },
        {
            "submission_id": "SUB-1",
            "diagnostic_decisions": [
                _diagnosis(index, score) for index, score in enumerate(scores, 1)
            ],
        },
    )


def _project(review: dict, diagnostic: dict) -> dict:
    return project_visual_recommendation_payload(
        submission_id="SUB-1",
        review=review,
        diagnostic=diagnostic,
        data_snapshot_id="a" * 64,
        scopes=("master",),
        limit_per_section=3,
    )


def _preview(payload: dict, search=_default_search) -> dict:
    return StudentRecommendationWorkbench().preview(
        payload, curriculum_catalog_loader=_catalog, question_search_loader=search
    )


@pytest.mark.parametrize("new_score", [0, 1])
def test_rescore_invalidates_old_diagnosis_even_if_score_did_not_change(new_score):
    review, diagnostic = _inputs()
    assert _preview(_project(review, diagnostic))["scope_groups"][0]["items"]
    review["scoring_decisions"].append(_score(2, score=new_score))
    payload = _project(review, diagnostic)
    assert payload["scoring_decisions"] == [
        {
            "sequence": 1,
            "match_id": "match-1",
            "atomic_part_id": "atomic-1",
            "teacher_score": new_score,
            "maximum_score": 1,
            "decision": "pending",
            "curriculum_sections": [],
        }
    ]
    calls = []
    preview = _preview(payload, lambda request: calls.append(request))
    assert preview["diagnosis_status"] == "no_weakness_evidence"
    assert preview["diagnoses"] == []
    assert preview["scope_groups"] == [{"scope": "master", "items": []}]
    assert calls == []


def test_new_diagnosis_on_latest_score_restores_only_current_evidence():
    review, diagnostic = _inputs()
    review["scoring_decisions"].append(_score(2, score=0))
    diagnostic["diagnostic_decisions"].append(
        _diagnosis(2, review["scoring_decisions"][-1], "edit")
    )
    payload = _project(review, diagnostic)
    assert len(payload["scoring_decisions"]) == 1
    assert payload["scoring_decisions"][0]["sequence"] == 2
    preview = _preview(payload)
    assert preview["diagnosis_status"] == "provisional_weakness"
    assert preview["metrics"]["supporting_evidence_count"] == 1
    assert preview["scope_groups"][0]["items"]


def test_new_diagnosis_after_full_score_retains_counterevidence_not_old_loss():
    review, diagnostic = _inputs()
    review["scoring_decisions"].append(_score(2, score=1))
    diagnostic["diagnostic_decisions"].append(
        _diagnosis(2, review["scoring_decisions"][-1])
    )
    preview = _preview(_project(review, diagnostic))
    assert preview["diagnosis_status"] == "no_weakness_evidence"
    assert preview["metrics"]["supporting_evidence_count"] == 0
    assert preview["metrics"]["counterevidence_count"] == 1
    assert preview["scope_groups"] == [{"scope": "master", "items": []}]


@pytest.mark.parametrize("action", ["reject", "pending"])
def test_teacher_rejection_or_pending_does_not_contribute_sections(action):
    review, diagnostic = _inputs()
    diagnostic["diagnostic_decisions"][0]["decision"] = action
    payload = _project(review, diagnostic)
    assert payload["scoring_decisions"][0]["decision"] == action
    assert payload["scoring_decisions"][0]["curriculum_sections"] == []
    assert _preview(payload)["diagnoses"] == []


def test_missing_diagnosis_is_not_created_from_scoring_or_candidate_hypotheses():
    review, diagnostic = _inputs()
    diagnostic["diagnostic_decisions"] = []
    payload = _project(review, diagnostic)
    assert payload["scoring_decisions"] == []
    preview = _preview(payload)
    assert preview["scoring_confirmation"] == "pending"
    assert preview["scope_groups"] == [{"scope": "master", "items": []}]


def test_mixed_matches_keep_current_confirmed_evidence_and_complete_theme_context():
    review, diagnostic = _inputs(4)
    review["scoring_decisions"].append(_score(5, match=1, score=1))
    diagnostic["diagnostic_decisions"][2]["decision"] = "reject"
    diagnostic["diagnostic_decisions"][3]["decision"] = "pending"
    payload = _project(review, diagnostic)
    preview = _preview(payload)
    assert preview["metrics"]["supporting_evidence_count"] == 1
    assert preview["diagnoses"][0]["supporting_evidence"][0]["match_id"] == "match-2"
    card = preview["scope_groups"][0]["items"][0]
    assert card["group_kind"] == "theme"
    assert len(card["atomic_chain"]) == 2
    assert card["shared_context"]["context_summary_zh"] == "共同材料"
    assert card["basket_selection"]["unit"] == "theme"
    assert payload["exclusions"]["atomic_part_ids"] == [
        f"atomic-{index}" for index in range(1, 5)
    ]


@pytest.mark.parametrize(
    "field, value",
    [
        ("scoring_decision_id", "SVDEC-missing"),
        ("match_id", "match-2"),
        ("atomic_part_id", "atomic-2"),
        ("analysis_id", "old-analysis"),
        ("scoring_decision_record_sha256", "f" * 64),
        ("sequence", 99),
    ],
)
def test_corrupt_historical_diagnosis_fails_even_when_replaced(field, value):
    review, diagnostic = _inputs(2)
    diagnostic["diagnostic_decisions"].append(
        _diagnosis(3, review["scoring_decisions"][0])
    )
    diagnostic["diagnostic_decisions"][0][field] = value
    with pytest.raises(StudentRecommendationWorkbenchError) as caught:
        _project(review, diagnostic)
    assert caught.value.code == "diagnostic_decision_store_corrupt"
    assert caught.value.status == 503


@pytest.mark.parametrize(
    "field, value",
    [
        ("teacher_score", float("nan")),
        ("sequence", 2),
        ("atomic_part_id", "atomic-other"),
    ],
)
def test_corrupt_scoring_history_fails_closed(field, value):
    review, diagnostic = _inputs()
    review["scoring_decisions"][0][field] = value
    with pytest.raises(StudentRecommendationWorkbenchError) as caught:
        _project(review, diagnostic)
    assert caught.value.code == "scoring_decision_store_corrupt"


def test_projection_and_engine_do_not_mutate_inputs_or_write_files(
    tmp_path, monkeypatch
):
    review, diagnostic = _inputs()
    before = deepcopy((review, diagnostic))
    monkeypatch.chdir(tmp_path)
    payload = _project(review, diagnostic)
    assert set(payload) == {
        "submission_id",
        "data_snapshot_id",
        "matches",
        "scoring_decisions",
        "exclusions",
        "scopes",
        "limit_per_section",
    }
    assert _preview(payload)["read_only"] is True
    assert (review, diagnostic) == before
    assert list(tmp_path.iterdir()) == []
    payload["scoring_decisions"][0]["curriculum_sections"][0]["section_title"] = (
        "修改输出不改变输入"
    )
    assert (review, diagnostic) == before


def test_unready_analysis_keeps_existing_error_contract():
    with pytest.raises(StudentRecommendationWorkbenchError) as caught:
        _project({"analysis": None}, {})
    assert (caught.value.code, caught.value.status) == ("analysis_not_ready", 409)
