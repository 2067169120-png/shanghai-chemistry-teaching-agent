import copy
import importlib.util
import json
from pathlib import Path


def _module(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "chapter_notes", scripts / "revise_chapter_notes_preparation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chapter_cover_complete_tables_and_exact_verified_quote(monkeypatch):
    module = _module(monkeypatch)
    source = json.loads(module.INPUT.read_text("utf-8"))
    before = copy.deepcopy(source)
    c = module.chapter_notes_candidate(source)
    assert source == before
    assert c["slides"][0]["title"] == c["topic"] == "电解质的电离"
    assert "？" not in "".join(c["slides"][0]["content"])
    assert "为什么" in c["slides"][1]["content"][0]
    for page in (1, 5, 8):
        table = c["slides"][page]["visual"]["comparison"]
        assert all(
            value and "____" not in value
            for row in table["rows"]
            for value in row["values"]
        )
    assert (
        c["slides"][6]["content"][0]
        == "教材原文（第57页）：" + module.ELECTROLYSIS_QUOTE
    )
    for group in ("slides", "activities", "lesson_stages"):
        assert sum(row["minutes"] for row in c[group]) == 40
    assert c["slides"][0]["minutes"] == 1 and c["slides"][1]["minutes"] == 4
    assert len(c["slides"]) == 16
    assert c["teacher_review_required"] and not c["publication_allowed"]


def test_live_chapter_brief_preserves_original_materials_and_does_not_mutate(
    monkeypatch,
):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    from verify_real_source_preparation import chapter_notes_payload

    old = {
        "topic": "原课题",
        "materials": "完整来源参考与警告",
        "advanced": {
            "template_and_delivery": "既有样式要求",
            "learning_and_experiment": "实验限制",
        },
    }
    before = copy.deepcopy(old)
    updated = chapter_notes_payload(old)
    assert old == before
    assert updated["topic"] == "电解质的电离"
    assert updated["materials"].startswith(old["materials"])
    assert "教材原文" in updated["materials"] and "印刷第57页" in updated["materials"]
    assert updated["advanced"]["learning_and_experiment"] == "实验限制"
    assert updated["advanced"]["template_and_delivery"].startswith("既有样式要求")
    assert "不只留任务或空格" in updated["advanced"]["template_and_delivery"]
