from copy import deepcopy

from integrations.deeptutor_shchem_v1.desktop_preparation_pedagogy import (
    teacher_design_starter,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import _make_layouts


def test_classroom_reflows_without_changing_source_or_auto_numbering():
    candidate = {
        "topic": "电离",
        "slides": [
            {"id": "S1", "title": "电离", "content": ["教材章节"]},
            {
                "id": "S2",
                "title": "练习：物质分类",
                "content": ["选择正确答案。", "A．氯化钠", "B．乙醇"],
            },
        ],
    }
    original = deepcopy(candidate)
    old = _make_layouts(candidate)
    new = _make_layouts(candidate, classroom_projection=True)
    assert candidate == original
    for before, after in zip(old, new, strict=True):
        refs_before = {
            (p, t)
            for e in before.elements
            for p, t in zip(e.source_paths, e.source_texts, strict=True)
        }
        refs_after = {
            (p, t)
            for e in after.elements
            for p, t in zip(e.source_paths, e.source_texts, strict=True)
        }
        assert refs_before == refs_after
        assert not any(e.overflow for e in after.elements)
    title = next(e for e in new[1].elements if "/slides/1/title" in e.source_paths)
    assert title.align == "center" and title.font_px >= 54
    body = next(e for e in new[1].elements if "/slides/1/content/0" in e.source_paths)
    assert body.font_px >= 40 and "1. 选择" not in body.text and "2. A" not in body.text
    assert not any(
        e.element_type == "rect" and e.height == 900 and e.width < 1600
        for e in new[1].elements
    )


def test_classroom_guidance_reaches_prompt_and_editable_starter():
    assert "约40人" in teacher_design_starter("复习")
    text = _prompt({"lesson_route": "复习"})
    assert "不套组会汇报" in text
    assert "同一页只保留一套必要编号" in text
    assert "不是国家平台规定" in text
    assert "静态导出采用题目页与解答页分开" in text


def test_seven_short_exercise_rows_fit_without_small_type_or_lost_text():
    content = [
        "在水溶液条件下写出下列物质的电离方程式（水用简单离子符号）：",
        "1．Ba(OH)₂",
        "2．Na₂SO₄",
        "3．BaCl₂",
        "4．CH₃COOH",
        "5．NH₃·H₂O",
        "6．H₂O",
    ]
    candidate = {
        "topic": "电离",
        "slides": [
            {"id": "S1", "title": "电离", "content": ["教材章节"]},
            {"id": "S2", "title": "独立书写", "content": content},
        ],
    }
    layout = _make_layouts(candidate, classroom_projection=True)[1]
    body = next(e for e in layout.elements if "/slides/1/content/0" in e.source_paths)
    assert not body.overflow and body.font_px >= 40
    assert list(body.source_texts) == content


def test_live_source_bound_image_and_text_remain_disjoint():
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    fixture = (
        root
        / "runtime/deeptutor_shchem/qa/classroom-live-v21-20260909/live/candidate.json"
    )
    if not fixture.exists():
        import pytest

        pytest.skip(
            "The frozen live QA candidate is a workspace-local regression fixture"
        )
    candidate = json.loads(fixture.read_text("utf-8"))
    assets = root / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料/assets"
    image_data = {
        "IMG-" + hashlib.sha256(p.read_bytes()).hexdigest(): p.read_bytes()
        for p in assets.glob("*.image")
    }
    layouts = _make_layouts(candidate, image_data=image_data, classroom_projection=True)
    for index in (4, 7, 17, 29):
        layout = layouts[index]
        picture = next(e for e in layout.elements if e.element_type == "image")
        body = next(
            e
            for e in layout.elements
            if any("observation_prompt" in p for p in e.source_paths)
        )
        assert not body.overflow and body.font_px >= 40
        assert body.y >= picture.y + picture.height + 20
        assert body.y + body.height <= 810
    # A whole diagram plus long multiple-choice text still needs editorial
    # splitting. Preserve the warning; never declare a dense page readable.
    assert any(e.overflow for e in layouts[13].elements)
