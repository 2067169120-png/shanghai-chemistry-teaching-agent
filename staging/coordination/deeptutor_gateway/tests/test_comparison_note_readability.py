"""Regression checks for readable source notes above comparison tables."""

from __future__ import annotations

from typing import Any

from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    SLIDE_HEIGHT_PX,
    SLIDE_WIDTH_PX,
    _make_layouts,
)

QUOTE = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"


def _comparison_slide(
    content: list[str],
    *,
    columns: list[str] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    columns = columns or ["电离", "电解质导电"]
    rows = rows or [
        {"label": "微观变化", "values": ["形成自由移动离子", "离子定向移动"]},
        {"label": "条件", "values": ["水溶液或熔融状态", "外接电源并构成闭合回路"]},
        {
            "label": "联系与区别",
            "values": ["提供自由移动离子", "电离本身不等于产生电流"],
        },
    ]
    return {
        "title": "知识点：电离与导电",
        "content": content,
        "visual": {
            "kind": "comparison",
            "comparison": {
                "dimension_label": "记录项目",
                "columns": columns,
                "rows": rows,
            },
            "steps": [],
        },
    }


def _layout(content: list[str], **kwargs: Any) -> Any:
    return _make_layouts(
        {
            "title": "测试课题",
            "topic": "测试课题",
            "slides": [_comparison_slide(content, **kwargs)],
        }
    )[0]


def _text_element(layout: Any, source_paths: list[str]) -> Any:
    return next(
        element
        for element in layout.elements
        if list(element.source_paths) == source_paths
    )


def _table_rects(layout: Any) -> list[Any]:
    return [
        element
        for element in layout.elements
        if element.element_type == "rect" and element.y >= 300 and element.width > 200
    ]


def test_short_comparison_note_keeps_original_geometry() -> None:
    layout = _layout(["简短引导"])
    note = _text_element(layout, ["/slides/0/content/0"])

    assert (note.x, note.y, note.width, note.height) == (104, 190, 1380, 98)
    assert note.font_px == 34
    assert note.source_texts == ("简短引导",)
    assert note.source_paths == ("/slides/0/content/0",)
    assert next(rect for rect in _table_rects(layout) if rect.y == 320)


def test_textbook_sentence_gets_preferred_size_and_table_moves_by_measured_height() -> (
    None
):
    layout = _layout(
        [
            f"教材原文（印刷57页／PDF62页）：{QUOTE}",
            "以下仅比较电解质：电离形成自由移动离子；离子定向移动才形成电流。",
        ]
    )
    note = _text_element(layout, ["/slides/0/content/0", "/slides/0/content/1"])

    assert note.font_px >= 34
    assert 98 < note.height <= 148
    assert note.overflow is False
    assert note.source_texts[0].endswith(QUOTE)
    table_header = next(rect for rect in _table_rects(layout) if rect.fill == "138A86")
    assert table_header.y == 320 + note.height - 98
    assert table_header.y > 320


def test_explicit_multiline_note_uses_same_wrapping_metrics_and_preserves_sources() -> (
    None
):
    layout = _layout(["第一行引导\n第二行引导\n第三行引导", "补充说明"])
    note = _text_element(layout, ["/slides/0/content/0", "/slides/0/content/1"])

    assert 26 <= note.font_px < 34
    assert note.height == 148
    assert note.text.splitlines()[:3] == ["第一行引导", "第二行引导", "第三行引导"]
    assert note.source_texts == ("第一行引导\n第二行引导\n第三行引导", "补充说明")
    assert note.source_paths == ("/slides/0/content/0", "/slides/0/content/1")
    assert note.overflow is False


def test_three_column_comparison_keeps_complete_table_inside_page() -> None:
    columns = ["状态一", "状态二", "状态三"]
    rows = [
        {
            "label": "定义",
            "values": [
                "水溶液中形成可以自由移动离子的过程",
                "熔融状态下形成可以自由移动离子的过程",
                "离子在电场作用下定向移动形成电流",
            ],
        },
        {"label": "条件", "values": ["条件甲", "条件乙", "条件丙"]},
        {"label": "结论", "values": ["结论甲", "结论乙", "结论丙"]},
    ]
    layout = _layout(["三列记录页"], columns=columns, rows=rows)

    headers = [
        element
        for element in layout.elements
        if element.element_type == "text"
        and element.source_paths
        and "/visual/comparison/columns/" in element.source_paths[0]
    ]
    assert [element.width for element in headers] == [360, 360, 360]
    assert [element.x for element in headers] == [340, 732, 1124]
    assert all(
        0 <= element.x <= SLIDE_WIDTH_PX - element.width
        and 0 <= element.y <= SLIDE_HEIGHT_PX - element.height
        for element in layout.elements
    )
    assert all(
        element.overflow is False
        for element in layout.elements
        if element.element_type == "text"
    )


def test_very_long_note_is_capped_without_hiding_overflow_or_pushing_table_off_page() -> (
    None
):
    layout = _layout(["长正文" * 400, "仍需保留的第二段说明"])
    note = _text_element(layout, ["/slides/0/content/0", "/slides/0/content/1"])

    assert note.height == 148
    assert note.overflow is True
    table_rects = _table_rects(layout)
    assert max(element.y + element.height for element in table_rects) <= 788
    assert all(
        0 <= element.x <= SLIDE_WIDTH_PX - element.width
        and 0 <= element.y <= SLIDE_HEIGHT_PX - element.height
        for element in layout.elements
    )
