"""Frozen example checks, not a general chemistry or classroom approval gate."""

import copy
import importlib.util
import json
from pathlib import Path


def _revision(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "classroom_revision", scripts / "revise_source_preparation_v11.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = json.loads(module.INPUT.read_text(encoding="utf-8"))
    before = copy.deepcopy(original)
    result = module.revised_candidate(original)
    assert original == before
    return result


def test_offline_revision_keeps_candidate_limits_and_timing(monkeypatch):
    candidate = _revision(monkeypatch)
    assert len(candidate["slides"]) == 16
    for view in ("activities", "slides", "lesson_stages"):
        assert sum(row["minutes"] for row in candidate[view]) == 40
    assert candidate["candidate_only"] and candidate["teacher_review_required"]
    assert not candidate["publication_allowed"]
    assert not candidate["official_claim_allowed"]
    assert all("依据：" in row["teacher_notes"] for row in candidate["slides"])


def test_questions_precede_matching_feedback_with_complete_notation(monkeypatch):
    slides = _revision(monkeypatch)["slides"]
    for left, right in ((2, 3), (10, 11), (14, 15)):
        assert slides[left]["order"] < slides[right]["order"]
    answers = slides[11]["content"]
    assert len(answers) == 4
    for equation in (
        "Ba(OH)₂ = Ba²⁺ + 2OH⁻",
        "Na₂SO₄ = 2Na⁺ + SO₄²⁻",
        "BaCl₂ = Ba²⁺ + 2Cl⁻",
        "CH₃COOH ⇌ H⁺ + CH₃COO⁻",
    ):
        assert any(equation in line for line in answers)
    visible = json.dumps(
        [{k: s[k] for k in ("content", "visual") if k in s} for s in slides],
        ensure_ascii=False,
    )
    assert "强极性键" not in visible and "弱极性键" not in visible
    assert "===" not in visible
    assert "外接电源" in visible and "闭合通路" in visible


def test_notes_are_segmented_and_worksheet_responses_remain_blank(monkeypatch):
    c = _revision(monkeypatch)
    worksheets = [a["worksheet"] for a in c["activities"] if a.get("worksheet")]
    assert len(worksheets) == 2
    assert [len(w["sections"]) for w in worksheets] == [4, 2]
    assert worksheets[1]["sections"][1]["response_lines"] == 8
    text = json.dumps(worksheets, ensure_ascii=False)
    assert "Ba²⁺ + 2OH⁻" not in text
    assert "第3栏" not in c["slides"][12]["content"][-1]
    assert "订正行" in c["slides"][12]["content"][-1]
    assert c["slides"][4]["image"]["asset_id"] == c["image_assets"][0]["asset_id"]
