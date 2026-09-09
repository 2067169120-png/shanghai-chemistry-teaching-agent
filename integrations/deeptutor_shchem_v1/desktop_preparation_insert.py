"""Explicit teacher-authored page insertion; no generated chemistry or source claims."""

from collections.abc import Mapping
from copy import deepcopy

from .desktop_preparation import DesktopPreparationError
from .desktop_preparation_visual import normalize_slide_visual


def _invalid(message):
    raise DesktopPreparationError("preparation_structure_invalid", message)


def _text(value, label, limit, *, allow_empty=False):
    if (
        not isinstance(value, str)
        or len(value.strip()) > limit
        or (not allow_empty and not value.strip())
        or any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    ):
        _invalid(f"{label}为空、含无效字符或超过{limit}字，请核对。")
    return value.strip()


def next_local_slide_id(slides):
    used = {slide["id"] for slide in slides}
    number = 1
    while f"SLOCAL{number:03d}" in used:
        number += 1
    return f"SLOCAL{number:03d}"


def insert_teacher_page(candidate, operation):
    """Mutate only the caller's preview copy, after all request validation."""
    slides = candidate["slides"]
    matches = [slide for slide in slides if slide["id"] == operation["anchor_slide_id"]]
    if len(matches) != 1 or matches[0] is slides[0]:
        _invalid("请选中首页以外的现有页面，作为补页位置及时间来源。")
    anchor = matches[0]
    if len(anchor.get("activity_ids", [])) != 1:
        _invalid("请先明确所选页的单一活动归属，再补充页面。")
    if operation["position"] not in ("before", "after"):
        _invalid("请选择插入在所选页之前或之后。")
    minutes = operation["minutes"]
    if type(minutes) is not int or not 1 <= minutes < anchor["minutes"]:
        _invalid("新页至少1分钟，所选原页也须保留至少1分钟；请先核对活动时间。")
    page = operation["page"]
    expected = {
        "title",
        "purpose",
        "content",
        "teacher_notes",
        "source_reference",
        "visual",
    }
    if not isinstance(page, Mapping) or set(page) != expected:
        _invalid("新增页只接受标题、教学作用、正文、讲解备注、来源说明与知识表。")
    title = _text(page["title"], "新增页标题", 80)
    purpose = _text(page["purpose"], "教学作用", 500)
    source = _text(page["source_reference"], "来源说明", 2000)
    notes = _text(page["teacher_notes"], "讲解备注", 10000, allow_empty=True)
    if not isinstance(page["content"], list) or not 1 <= len(page["content"]) <= 12:
        _invalid("正文需有1至12段；请保留完整定义或题干，不要只写知识点标签。")
    content = [_text(text, "正文段落", 2000) for text in page["content"]]
    try:
        visual = normalize_slide_visual(page["visual"])
    except ValueError:
        _invalid(
            "知识表需2至3个对象列、2至4行；标题最多32字，单元格最多120字。不会截断或补空。"
        )
    if visual is not None and visual["kind"] != "comparison":
        _invalid("新增页目前支持正文或知识比较表，不自动生成示意图。")
    if visual is not None:
        table = visual["comparison"]
        cells = [table["dimension_label"], *table["columns"]]
        cells.extend(
            text for row in table["rows"] for text in [row["label"], *row["values"]]
        )
        for text in cells:
            _text(text, "知识表内容", 120)
    new = {
        "id": next_local_slide_id(slides),
        "order": 0,
        "title": title,
        "purpose": purpose,
        "content": content,
        "teacher_notes": "来源说明（本地修订者提供，未自动核验）："
        + source
        + ("\n讲解备注：" + notes if notes else ""),
        "minutes": minutes,
        **{
            key: deepcopy(anchor[key])
            for key in ("activity_ids", "objective_ids", "assessment_ids")
        },
    }
    if visual is not None:
        new["visual"] = visual
    index = slides.index(anchor) + (operation["position"] == "after")
    anchor["minutes"] -= minutes
    slides.insert(index, new)
    for number, slide in enumerate(slides, 1):
        slide["order"] = number
    return new
