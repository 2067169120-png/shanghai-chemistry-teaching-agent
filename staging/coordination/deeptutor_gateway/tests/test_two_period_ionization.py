"""Content and timing regression checks, not teacher approval."""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "two_period_lesson", SCRIPTS / "build_two_period_ionization.py"
)
lesson_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lesson_module)


def test_two_period_timing_is_40_plus_40_without_reallocation():
    raw, _ = lesson_module.lesson()
    candidate = lesson_module.normalize_preparation_candidate(
        raw, lesson_module.build_brief()
    )
    assert len(candidate["slides"]) == 38
    assert sum(s["minutes"] for s in candidate["slides"][:18]) == 40
    assert sum(s["minutes"] for s in candidate["slides"][18:]) == 40
    for key in ("slides", "activities", "lesson_stages"):
        assert sum(s["minutes"] for s in candidate[key]) == 80
    assert not any(u["field"] == "timing_alignment" for u in candidate["uncertainties"])
    assert candidate["candidate_only"] and candidate["teacher_review_required"]
    assert not candidate["publication_allowed"]


def test_chapter_title_sources_and_examples_are_present():
    raw, sources = lesson_module.lesson()
    assert raw["slides"][0]["title"] == "电解质的电离"
    assert sum(s["title"].startswith("例") for s in raw["slides"]) == 6
    assert len(sources) == 38
    assert all(s["source"] for s in sources)
    assert lesson_module.QUOTE in "".join(raw["slides"][6]["content"])
    assert "全部电离" in "".join(raw["slides"][19]["content"])
    assert "部分分子" in "".join(raw["slides"][19]["content"])
    assert raw["slides"][5]["image"]["asset_id"].endswith(
        lesson_module.EXPECTED[lesson_module.FIGURE]
    )


def test_all_numbered_practice_and_exit_groups_have_later_feedback():
    raw, _ = lesson_module.lesson()
    pairs = [
        (12, 13),
        (14, 15),
        (23, 24),
        (28, 29),
        (32, 33),
        (34, 35),
        (16, 17),
        (36, 37),
    ]
    for question, feedback in pairs:
        assert question < feedback
        assert raw["slides"][question - 1]["purpose"] in ("独立练习", "独立检测")
        title = raw["slides"][feedback - 1]["title"]
        assert "解析" in title or "答案" in title
    assert "先写后核对" not in "".join(raw["slides"][29]["content"])


def test_workbench_review_finds_feedback_without_guessing_missing_links():
    from integrations.deeptutor_shchem_v1.desktop_preparation_review import (
        classroom_review,
    )

    raw, _ = lesson_module.lesson()
    candidate = lesson_module.normalize_preparation_candidate(
        raw, lesson_module.build_brief()
    )
    report = classroom_review(candidate)
    links = {
        row["task_page"]: row["possible_feedback_pages"] for row in report["task_links"]
    }
    pairs = {12: 13, 14: 15, 23: 24, 28: 29, 32: 33, 34: 35, 16: 17, 36: 37}
    assert set(links) == set(pairs)
    assert all(answer in links[task] for task, answer in pairs.items() if task != 14)
    # The authored lesson crosses both activity and assessment boundaries here.
    # Its actual answer was manually checked, but the structural aid must not
    # invent an explicit link or propagate the preceding page's source locator.
    assert links[14] == []
    assert {(row["code"], tuple(row["pages"])) for row in report["findings"]} == {
        ("later_feedback_not_detected", (14,)),
        ("source_locator_not_detected", (15,)),
    }
    assert report["status"] == "teacher_review_required"


def test_six_equations_agree_with_worksheet_and_textbook_scopes():
    raw, _ = lesson_module.lesson()
    answer = "\n".join(raw["slides"][28]["content"])
    for equation in (
        "Ba(OH)₂ = Ba²⁺ + 2OH⁻",
        "Na₂SO₄ = 2Na⁺ + SO₄²⁻",
        "BaCl₂ = Ba²⁺ + 2Cl⁻",
        "CH₃COOH ⇌ H⁺ + CH₃COO⁻",
        "NH₃·H₂O ⇌ NH₄⁺ + OH⁻",
        "H₂O ⇌ H⁺ + OH⁻",
    ):
        assert equation in answer
    sheet = raw["activities"][5]["worksheet"]
    assert len(sheet["sections"][0]["row_labels"]) == 6
    assert "⑥H₂O" in "".join(raw["slides"][27]["content"])
    assert "例式复写" in "".join(raw["slides"][27]["content"])


def test_six_worksheets_and_word_question_have_explicit_content():
    raw, _ = lesson_module.lesson()
    assert sum(a["worksheet"] is not None for a in raw["activities"]) == 6
    question = "".join(raw["slides"][13]["content"])
    for option in ("黄酒", "冰醋酸", "漂白粉", "乙醇"):
        assert option in question
        assert option in raw["activities"][2]["worksheet"]["sections"][1]["prompt"]
    assert "答案B" in "".join(raw["slides"][14]["content"])
    assert "未独立核验" in raw["slides"][13]["teacher_notes"]


def test_authored_question_numbers_do_not_receive_template_numbers():
    raw, _ = lesson_module.lesson()
    for number in (16, 17, 23, 24, 29, 32, 33, 34, 35, 36, 37):
        assert len(raw["slides"][number - 1]["content"]) == 1
    assert 1 in raw["slides"][17]["objective_numbers"]
    assert raw["slides"][37]["objective_numbers"] == [1, 2, 3, 4]
