"""Exact classroom sequence projection, with no semantic role inference."""

from copy import deepcopy


def student_page_text(slide):
    """Keep authored table relationships, without exposing teacher-only fields."""
    lines = [slide["title"], "", *slide.get("content", [])]
    visual = slide.get("visual") or {}
    if visual.get("kind") == "comparison":
        table = visual["comparison"]
        lines.extend(["", "知识表 · " + table["dimension_label"]])
        for row in table["rows"]:
            lines.extend(["", row["label"]])
            lines.extend(
                f"{column}：{value}"
                for column, value in zip(table["columns"], row["values"], strict=True)
            )
    elif visual.get("kind") == "process":
        for number, step in enumerate(visual["steps"], 1):
            lines.extend(["", f"{number}. {step['label']}", step["detail"]])
    if slide.get("image"):
        lines.extend(
            [
                "",
                "读图提示：" + slide["image"]["observation_prompt"],
                "〔本页图片未在文字视图中显示，请打开课件核对图片内容和清晰度。〕",
            ]
        )
    return "\n".join(lines)


def classroom_sequence(candidate):
    """Resolve existing links only; discrepancies remain warnings, not repairs."""
    activities = candidate["activities"]
    period_minutes = candidate["timing"]["minutes_per_period"]
    pages, warnings = [], []
    elapsed = 0
    for number, slide in enumerate(candidate["slides"], 1):
        end = elapsed + slide["minutes"]
        first_period = elapsed // period_minutes + 1
        last_period = (end - 1) // period_minutes + 1
        linked_activities = [a for a in activities if a["id"] in slide["activity_ids"]]
        pages.append(
            {
                "id": slide["id"],
                "page": number,
                "title": slide["title"],
                "minutes": slide["minutes"],
                "start_minute": elapsed,
                "end_minute": end,
                "period_label": (
                    f"第{first_period}课时"
                    if first_period == last_period
                    else f"跨第{first_period}—{last_period}课时"
                ),
                "student_text": student_page_text(slide),
                "purpose": slide.get("purpose", ""),
                "teacher_notes": slide.get("teacher_notes", ""),
                "activities": deepcopy(linked_activities),
                "stages": [
                    deepcopy(stage)
                    for stage in candidate["lesson_stages"]
                    if set(stage["activity_ids"]) & set(slide["activity_ids"])
                ],
                "objectives": [
                    deepcopy(row)
                    for row in candidate["objectives"]
                    if row["id"] in slide["objective_ids"]
                ],
                "assessments": [
                    deepcopy(row)
                    for row in candidate["assessments"]
                    if row["id"] in slide["assessment_ids"]
                ],
            }
        )
        if first_period != last_period:
            warnings.append(f"第{number}页跨课时边界，请核对讲解和学生作答是否被打断。")
        elapsed = end
    unassigned = [
        str(i)
        for i, slide in enumerate(candidate["slides"], 1)
        if len(slide["activity_ids"]) != 1
    ]
    if unassigned:
        warnings.append("第" + "、".join(unassigned) + "页未明确归入单一活动。")
    else:
        actual_order = []
        for slide in candidate["slides"]:
            identity = slide["activity_ids"][0]
            if not actual_order or actual_order[-1] != identity:
                actual_order.append(identity)
        if actual_order != [activity["id"] for activity in activities]:
            warnings.append(
                "PPT活动顺序与教案活动清单不同，或同一活动被其他活动隔开；移动不会自动改写教案。"
            )
    for activity in activities:
        minutes = sum(
            slide["minutes"]
            for slide in candidate["slides"]
            if slide["activity_ids"] == [activity["id"]]
        )
        if minutes != activity["minutes"]:
            warnings.append(
                f"「{activity['title']}」关联PPT {minutes}分钟，活动预算{activity['minutes']}分钟。"
            )
    return {"pages": pages, "warnings": warnings, "total_minutes": elapsed}
