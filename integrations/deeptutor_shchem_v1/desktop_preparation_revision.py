"""Teacher-authored text edits on a frozen lesson, without model calls.

The form and backend share the same explicit field inventory. IDs, ordering,
timing, original evidence and review status are not editable by this operation.
No fuzzy replacement or cross-carrier semantic synchronization is performed.
"""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .desktop_preparation import (
    DesktopPreparationError,
    _canonical_candidate_digest,
    _validate_canonical_candidate,
)

_LABELS = {
    "title": "标题",
    "statement": "目标内容",
    "purpose": "本页教学作用",
    "content": "学生可见正文",
    "teacher_notes": "教师讲解与来源备注",
    "teacher_action": "教师活动",
    "student_action": "学生活动",
    "materials": "所用材料",
    "assessment": "本环节检查标准",
    "evidence_of_learning": "学生学习证据",
    "success_criteria": "达标标准",
    "instruction": "任务要求",
    "instructions": "填写说明",
    "heading": "小节标题",
    "prompt": "任务提示",
    "columns": "列标题",
    "row_labels": "行标题",
    "dimension_label": "比较维度标题",
    "label": "名称",
    "values": "知识表内容",
    "detail": "关系或过程说明",
}


def preparation_text_fields(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Enumerate existing editable strings with plain-language form labels."""
    result = []

    def add(path, value, group, label, limit=20000):
        if isinstance(value, str):
            result.append(
                {
                    "path": path,
                    "text": value,
                    "group": group,
                    "label": label,
                    "limit": limit,
                    "allow_empty": not value,
                }
            )

    def walk(value, path, group, label, limit=20000):
        if isinstance(value, str):
            add(path, value, group, label, limit)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, [*path, index], group, f"{label} {index + 1}", limit)

    for section, caption, keys in (
        ("slides", "PPT", ("title", "purpose", "content", "teacher_notes")),
        (
            "lesson_stages",
            "教案环节",
            ("title", "teacher_action", "student_action", "materials", "assessment"),
        ),
        (
            "activities",
            "活动",
            ("title", "teacher_action", "student_action", "materials"),
        ),
        ("objectives", "教学目标", ("statement",)),
        (
            "assessments",
            "学习评价",
            ("title", "evidence_of_learning", "success_criteria"),
        ),
    ):
        for index, row in enumerate(candidate.get(section, [])):
            group = f"{caption} {index + 1} · {row.get('title', row.get('id', ''))}"
            base = [section, index]
            for key in keys:
                # The renderer takes the cover heading from the frozen topic.
                # Do not offer an apparent title edit that cannot affect it.
                if section == "slides" and index == 0 and key == "title":
                    continue
                walk(row.get(key), [*base, key], group, _LABELS[key])
            visual = row.get("visual") or {}
            if section == "slides" and visual.get("kind") == "comparison":
                table = visual["comparison"]
                tb = [*base, "visual", "comparison"]
                add(
                    [*tb, "dimension_label"],
                    table["dimension_label"],
                    group,
                    "知识表 / 比较维度标题",
                    32,
                )
                walk(table["columns"], [*tb, "columns"], group, "知识表 / 列标题", 32)
                for ri, table_row in enumerate(table["rows"]):
                    rb = [*tb, "rows", ri]
                    add(
                        [*rb, "label"],
                        table_row["label"],
                        group,
                        f"知识表 / 第{ri + 1}行标题",
                        32,
                    )
                    for ci, value in enumerate(table_row["values"]):
                        add(
                            [*rb, "values", ci],
                            value,
                            group,
                            f"知识表 / {table_row['label']} / {table['columns'][ci]}",
                            120,
                        )
            if section == "slides" and visual.get("kind") == "process":
                for si, step in enumerate(visual["steps"]):
                    for key, limit in (("label", 32), ("detail", 120)):
                        add(
                            [*base, "visual", "steps", si, key],
                            step[key],
                            group,
                            f"过程 {si + 1} / {_LABELS[key]}",
                            limit,
                        )
            worksheet = row.get("worksheet")
            if section == "activities" and worksheet:
                wg = f"学习单 {index + 1} · {worksheet['title']}"
                wb = [*base, "worksheet"]
                for key in ("title", "instructions"):
                    walk(
                        worksheet[key],
                        [*wb, key],
                        wg,
                        _LABELS[key],
                        80 if key == "title" else 300,
                    )
                for si, part in enumerate(worksheet["sections"]):
                    for key in ("heading", "prompt", "columns", "row_labels"):
                        walk(
                            part.get(key),
                            [*wb, "sections", si, key],
                            wg,
                            f"小节 {si + 1} / {_LABELS[key]}",
                            {
                                "heading": 80,
                                "prompt": 500,
                                "columns": 40,
                                "row_labels": 60,
                            }[key],
                        )
    homework = candidate.get("homework", {})
    add(["homework", "title"], homework.get("title"), "课后任务", "标题")
    for index, task in enumerate(homework.get("tasks", [])):
        add(
            ["homework", "tasks", index, "instruction"],
            task["instruction"],
            "课后任务",
            f"任务 {index + 1}",
        )
    return result


def apply_preparation_text_edits(candidate, edits, payload):
    """Apply explicit strings only, then verify canonical invariants again."""
    value = _validate_canonical_candidate(candidate, payload)
    fields = {tuple(f["path"]): f for f in preparation_text_fields(value)}
    if not isinstance(edits, list) or not edits or len(edits) > len(fields):
        raise DesktopPreparationError(
            "preparation_revision_invalid", "请先修改需要订正的内容。"
        )
    seen = set()
    updated = deepcopy(value)
    for edit in edits:
        if not isinstance(edit, Mapping) or set(edit) != {"path", "text"}:
            raise DesktopPreparationError(
                "preparation_revision_invalid", "修订字段格式不正确。"
            )
        path = edit["path"]
        if not isinstance(path, list) or any(type(p) not in (str, int) for p in path):
            raise DesktopPreparationError(
                "preparation_revision_invalid", "修订位置不正确。"
            )
        key = tuple(path)
        if key not in fields or key in seen:
            raise DesktopPreparationError(
                "preparation_revision_invalid",
                "只能修订已列出的文字，不能修改编号、时间、来源或审核状态。",
            )
        seen.add(key)
        field, text = fields[key], edit["text"]
        if (
            not isinstance(text, str)
            or len(text.strip()) > field["limit"]
            or (not text.strip() and not field["allow_empty"])
            or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text)
        ):
            raise DesktopPreparationError(
                "preparation_revision_invalid",
                f"{field['label']}为空、过长或含不可用字符。",
            )
        target = updated
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = text.strip()
    if updated == value:
        raise DesktopPreparationError(
            "preparation_revision_unchanged", "内容没有变化，无需另存修订版。"
        )
    updated["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(updated)[:32]
    return _validate_canonical_candidate(updated, payload)
