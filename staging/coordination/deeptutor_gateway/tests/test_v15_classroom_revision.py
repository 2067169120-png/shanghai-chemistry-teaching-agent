"""Offline regression checks for the source-bound v15 classroom revision."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/revise_v15_classroom_notes.py"


def _module(monkeypatch):
    scripts = SCRIPT.parent
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "revise_v15_classroom_notes_for_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def frozen_source(monkeypatch):
    module = _module(monkeypatch)
    returned = module.SOURCE / "returned-candidate.json"
    brief_path = module.SOURCE / "teacher-brief.json"
    if not returned.is_file() or not brief_path.is_file():
        pytest.skip("冻结的 v15 returned-candidate.json 或 teacher-brief.json 不存在")
    raw_bytes = returned.read_bytes()
    brief_bytes = brief_path.read_bytes()
    return {
        "module": module,
        "returned": returned,
        "brief_path": brief_path,
        "raw_bytes": raw_bytes,
        "brief_bytes": brief_bytes,
        "raw": json.loads(raw_bytes.decode("utf-8")),
        "brief": json.loads(brief_bytes.decode("utf-8")),
    }


def test_revised_raw_is_read_only_and_builds_thirteen_page_classroom_deck(
    frozen_source,
):
    module = frozen_source["module"]
    raw = frozen_source["raw"]
    before = copy.deepcopy(raw)

    revised = module.revised_raw(raw)

    assert raw == before
    assert frozen_source["returned"].read_bytes() == frozen_source["raw_bytes"]
    assert len(revised["slides"]) == 13
    assert revised["slides"][0]["title"] == "电解质的电离"
    assert revised["slides"][0]["content"][:2] == [
        "第2章 海洋中的卤素资源",
        "2.2 氧化还原反应和离子反应",
    ]
    assert "？" not in revised["slides"][0]["title"]
    assert revised["slides"][1]["title"].startswith("为什么NaCl固体不导电")
    assert revised["slides"][6]["content"][0].endswith(module.QUOTE)
    assert module.QUOTE in revised["slides"][6]["content"][0]


def test_observation_classification_and_practice_show_task_before_feedback(
    frozen_source,
):
    module = frozen_source["module"]
    revised = module.revised_raw(frozen_source["raw"])
    slides = revised["slides"]
    titles = [slide["title"] for slide in slides]

    # Observation task -> observation feedback.
    assert titles[2].startswith("观察：")
    assert titles[3].startswith("观察核对：")
    assert "观察后再修正" in slides[1]["content"][2]
    assert "投影完整表供纠错" in slides[3]["teacher_notes"]

    # Definition page does not reveal the six judgments before feedback.
    definition = slides[4]
    definition_rows = definition["visual"]["comparison"]["rows"]
    assert [row["label"] for row in definition_rows] == [
        "研究对象",
        "分类标准",
        "本质区别",
    ]
    definition_text = json.dumps(definition, ensure_ascii=False)
    assert "NaCl、NaOH、CH₃COOH" not in definition_text
    assert "蔗糖、SO₂、CO₂" not in definition_text
    assert titles[5].startswith("分类核对：")
    assert [row["label"] for row in slides[5]["visual"]["comparison"]["rows"]] == [
        "NaCl、NaOH、CH₃COOH",
        "蔗糖",
        "SO₂、CO₂",
    ]

    # Exercise task -> equation feedback, with the same conservation check
    # required by the student worksheet.
    assert titles[9] == "电离方程式书写练习"
    assert titles[10] == "电离方程式书写核对"
    assert "逐式检验原子与电荷守恒" in slides[9]["content"][2]
    worksheet_instructions = revised["activities"][2]["worksheet"]["instructions"]
    assert "每个方程式都要检查原子守恒和电荷守恒。" in worksheet_instructions


def test_final_knowledge_table_and_activity_minutes_are_closed(frozen_source):
    module = frozen_source["module"]
    revised = module.revised_raw(frozen_source["raw"])
    final = revised["slides"][-1]
    comparison = final["visual"]["comparison"]

    assert final["title"] == "课堂笔记：概念、条件与书写规则"
    assert comparison["columns"] == ["应记录的结论", "易错提醒"]
    assert [row["label"] for row in comparison["rows"]] == [
        "电解质分类",
        "电离与导电",
        "强、弱电解质",
        "电离方程式",
    ]
    assert all(
        value.strip() and "____" not in value
        for row in comparison["rows"]
        for value in row["values"]
    )
    assert [activity["minutes"] for activity in revised["activities"]] == [
        8,
        15,
        10,
        7,
    ]
    assert sum(slide["minutes"] for slide in revised["slides"]) == 40
    assert sum(activity["minutes"] for activity in revised["activities"]) == 40
    assert sum(stage["minutes"] for stage in revised["lesson_stages"]) == 40


def test_normalized_candidate_stays_offline_and_has_no_timing_alignment(
    frozen_source,
):
    module = frozen_source["module"]
    raw = frozen_source["raw"]
    brief = frozen_source["brief"]
    raw_before = copy.deepcopy(raw)
    brief_before = copy.deepcopy(brief)
    revised = module.revised_raw(raw)
    revised_before = copy.deepcopy(revised)

    # These are pure local transforms: no facade, provider, renderer, API key,
    # or output directory is involved in this regression path.
    candidate = module.normalize_preparation_candidate(revised, brief)

    assert raw == raw_before
    assert brief == brief_before
    assert revised == revised_before
    assert frozen_source["returned"].read_bytes() == frozen_source["raw_bytes"]
    assert frozen_source["brief_path"].read_bytes() == frozen_source["brief_bytes"]
    assert len(candidate["slides"]) == 13
    assert candidate["slides"][0]["title"] == "电解质的电离"
    assert not any(
        uncertainty.get("field") == "timing_alignment"
        for uncertainty in candidate["uncertainties"]
    )
