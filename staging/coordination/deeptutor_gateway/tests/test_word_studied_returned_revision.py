"""Regression checks for the explicit source-studied lesson revision."""

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "staging/coordination/deeptutor_gateway/scripts"))
revision = importlib.import_module("revise_word_studied_returned_candidate")


@pytest.fixture
def pair():
    raw = json.loads((revision.LIVE / "returned-candidate.json").read_text("utf-8"))
    return raw, revision.revise(raw)


def test_revision_is_pure_and_keeps_original_response(pair):
    raw, revised = pair
    before = copy.deepcopy(raw)
    revision.revise(raw)
    assert raw == before
    assert (
        revision.digest(revision.LIVE / "returned-candidate.json") == revision.RAW_HASH
    )
    assert len(raw["slides"]) == 40
    revision.validate(revised)


def test_normalization_with_exact_saved_brief(pair):
    payload = json.loads((revision.LIVE / "teacher-brief.json").read_text("utf-8"))
    canonical = revision.normalize_preparation_candidate(
        pair[1], revision.normalize_preparation_payload(payload)
    )
    assert canonical["candidate_only"] is True
    assert canonical["teacher_review_required"] is True
    assert canonical["publication_allowed"] is False


@pytest.mark.parametrize("old_index,new_indices", [(16, [16, 17]), (27, [28, 29])])
def test_split_preserves_every_comparison_row(pair, old_index, new_indices):
    raw, revised = pair
    before = raw["slides"][old_index]["visual"]["comparison"]["rows"]
    after = [
        row
        for n in new_indices
        for row in revised["slides"][n]["visual"]["comparison"]["rows"]
    ]
    assert before == after


def test_complete_visible_knowledge_and_equations(pair):
    slides = pair[1]["slides"]
    assert all(s["visual"]["kind"] == "comparison" for s in slides[-3:])
    text = json.dumps(
        [{k: s[k] for k in ("content", "visual")} for s in slides], ensure_ascii=False
    )
    for equation in (
        "NaHSO₄＝Na⁺＋H⁺＋SO₄²⁻",
        "NaHSO₄＝Na⁺＋HSO₄⁻",
        "H₂S ⇌ H⁺＋HS⁻",
        "HS⁻ ⇌ H⁺＋S²⁻",
        "NaHCO₃＝Na⁺＋HCO₃⁻",
        "HCO₃⁻ ⇌ H⁺＋CO₃²⁻",
    ):
        assert equation in text
    correction = next(s for s in slides if s["title"] == "Q5 核对与完整改正式")
    assert "NH₃·H₂O ⇌ NH₄⁺＋OH⁻" in correction["content"][2]


def test_no_answer_to_optional_question_in_student_material(pair):
    c = pair[1]
    text = json.dumps([a["worksheet"] for a in c["activities"]], ensure_ascii=False)
    assert "H₃O₂⁺＋HO₂⁻" not in text
    assert "H₃O₂⁺＋HO₂⁻" not in json.dumps(c["homework"], ensure_ascii=False)
    assert "H₃O₂⁺＋HO₂⁻" in c["slides"][-1]["teacher_notes"]


def test_worksheet_order_matches_classroom_sequence(pair):
    sections = pair[1]["activities"][4]["worksheet"]["sections"]
    assert [s["heading"].split()[0] for s in sections] == [
        "K4",
        "Q8第1—3题",
        "K5",
        "K6",
        "Q5",
    ]
    assert all(s["response_kind"] == "table" for s in sections[1:4])


def test_stage_activity_links_include_q6_owner(pair):
    c = pair[1]
    stage = next(s for s in c["lesson_stages"] if "Q6辨析" in s["title"])
    assert stage["activity_numbers"] == [6]
    assert [s["minutes"] for s in c["lesson_stages"]] == [
        4,
        10,
        8,
        14,
        4,
        3,
        8,
        10,
        4,
        4,
        7,
        4,
    ]
    for i, activity in enumerate(c["activities"], 1):
        assert (
            sum(s["minutes"] for s in c["slides"] if s["activity_numbers"] == [i])
            == activity["minutes"]
        )
        assert (
            sum(
                s["minutes"] for s in c["lesson_stages"] if s["activity_numbers"] == [i]
            )
            == activity["minutes"]
        )
