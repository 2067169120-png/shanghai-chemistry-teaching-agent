"""Source-bound lesson regression, independent of a paid model invocation."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from build_notebook_complete_ionization import BASE, revised_lesson


def test_source_bound_notebook_pages_preserve_lessons_and_tasks():
    raw, brief, coverage, payload = revised_lesson()
    original = json.loads((BASE / "word-led-raw-candidate.json").read_text("utf-8"))
    assert len(raw["slides"]) == 45
    assert [
        sum(s["minutes"] for s in raw["slides"][:24]),
        sum(s["minutes"] for s in raw["slides"][24:]),
    ] == [40, 40]
    assert len(raw["activities"]) == len(raw["lesson_stages"]) == 8
    assert all(
        sum(a["minutes"] for a in raw[k]) == 80 for k in ("activities", "lesson_stages")
    )
    assert len(payload) == len(brief["image_assets"]) == 8
    assert [c["slide"] for c in coverage] == list(range(1, 46))
    assert raw["slides"][0]["title"] == "电解质的电离"
    for index, old in enumerate(original["slides"]):
        new_index = index if index < 40 else index + 1
        if old["purpose"] == "独立练习":
            assert raw["slides"][new_index]["content"] == old["content"]
    for i in (13, 17):
        assert raw["slides"][i]["image"]["asset_id"] in payload
        assert "教材第" in raw["slides"][i]["content"][0]
        assert raw["slides"][i]["visual"] is None


def test_complete_formula_examples_are_visible_and_in_teacher_plan():
    raw, _, _, _ = revised_lesson()
    examples = [
        "H₂S ⇌ H⁺ + HS⁻",
        "HS⁻ ⇌ H⁺ + S²⁻",
        "NaHCO₃ = Na⁺ + HCO₃⁻",
        "HCO₃⁻ ⇌ H⁺ + CO₃²⁻",
        "NaHSO₄ = Na⁺ + H⁺ + SO₄²⁻",
        "NaHSO₄ = Na⁺ + HSO₄⁻",
    ]
    visible = json.dumps(
        [s["visual"] for s in raw["slides"][39:41]], ensure_ascii=False
    )
    teacher = raw["activities"][7]["teacher_action"]
    for text in examples:
        assert text in visible
        assert text in teacher
    assert "水溶液" in visible and "熔融状态" in visible
    assert "PPT第45页" in json.dumps(
        raw["activities"][7]["worksheet"], ensure_ascii=False
    )
    assert raw["activities"][7]["materials"][0] == "PPT第39—45页"
