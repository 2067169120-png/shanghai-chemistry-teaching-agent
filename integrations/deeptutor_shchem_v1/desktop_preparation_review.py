"""Read-only classroom review aids, not a semantic or teaching approval gate.

Explicit objective/activity references are preserved. Title/purpose keywords
only locate possible note, task and feedback pages; they never prove that an
answer matches a question or that a source supports a chemistry statement.
"""

import re
from collections.abc import Mapping
from typing import Any

_NOTE = re.compile(r"笔记|板书|记录框架|整理记录")
_TASK = re.compile(r"独立|练习|作答|纠错|先判断|先观察|先解释|先预测")
_INDEPENDENT = re.compile(r"独立|作答|先判断|先观察|先解释|先预测")
_FEEDBACK = re.compile(r"反馈|核对|订正|答案|解析|讲评")
_MIXED_TASK_FEEDBACK = re.compile(
    r"(?:练习|题目|任务)\s*(?:及|与|和|及其)\s*(?:答案|解析|讲评)"
)
_EXERCISE = re.compile(
    r"练习\s*([0-9]+|[一二三四五六七八九十]+)(?![0-9一二三四五六七八九十])"
)
_LOCATOR = re.compile(
    r"区块\s*\d|\bC\d{2,}\b|(?:教材|印刷).{0,12}\d.{0,8}页|Word\s*正文子节点\s*\d",
    re.IGNORECASE,
)


def _role_hints(signpost: str) -> list[str]:
    """A numbered exercise's explanation is not automatically another task.

    Explicit independent-work or mixed-page wording keeps the answer-leak hint.
    Content and image pixels are not semantically classified here.
    """
    feedback = bool(_FEEDBACK.search(signpost))
    task = bool(_TASK.search(signpost)) and (
        not feedback
        or bool(_INDEPENDENT.search(signpost))
        or bool(_MIXED_TASK_FEEDBACK.search(signpost))
    )
    return [
        label
        for label, present in (
            ("笔记整理线索", bool(_NOTE.search(signpost))),
            ("学生任务线索", task),
            ("反馈核对线索", feedback),
        )
        if present
    ]


def _related_feedback(task: Mapping[str, Any], feedback: Mapping[str, Any]) -> bool:
    if not set(task["objective_ids"]) & set(feedback["objective_ids"]):
        return False
    task_number = _EXERCISE.search(task["title"])
    feedback_number = _EXERCISE.search(feedback["title"])
    if task_number and feedback_number:
        if task_number.group(1) != feedback_number.group(1):
            return False
        # The same numbered exercise may finish in the next activity. Require
        # an explicit shared assessment too; a title match alone is insufficient.
        if set(task["assessment_ids"]) & set(feedback["assessment_ids"]):
            return True
    return bool(set(task["activity_ids"]) & set(feedback["activity_ids"]))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _visual_text(slide: Mapping[str, Any]) -> list[str]:
    """Only authored student-facing fields, never presenter notes or purpose."""
    texts = _strings(slide.get("content", []))
    visual = slide.get("visual") or {}
    if visual.get("kind") == "comparison":
        texts.extend(_strings(visual.get("comparison")))
    elif visual.get("kind") == "process":
        texts.extend(_strings(visual.get("steps")))
    # Image pixels/captions are not interpreted as verified note content.
    return texts


def classroom_review(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Project a normalized candidate without modifying it or its approvals."""
    pages = []
    findings = []

    def add(code: str, page_numbers: list[int], message: str) -> None:
        findings.append({"code": code, "pages": page_numbers, "message": message})

    for number, slide in enumerate(candidate.get("slides", []), 1):
        signpost = str(slide.get("title", "")) + " " + str(slide.get("purpose", ""))
        visible = _visual_text(slide)
        notes = str(slide.get("teacher_notes", ""))
        hints = _role_hints(signpost)
        pages.append(
            {
                "page": number,
                "title": str(slide.get("title", "")),
                "minutes": slide.get("minutes", 0),
                "objective_ids": list(slide.get("objective_ids", [])),
                "activity_ids": list(slide.get("activity_ids", [])),
                "assessment_ids": list(slide.get("assessment_ids", [])),
                "role_hints": hints,
                "visible_text": visible,
                "teacher_notes": notes,
                "source_locator_mentioned": bool(_LOCATOR.search(notes)),
                "has_image": bool(slide.get("image")),
            }
        )
        if not _LOCATOR.search(notes):
            add(
                "source_locator_not_detected",
                [number],
                "备注未检测到具体 Word 区块/正文子节点、教材知识点编号或教材页码；请核对来源定位。",
            )
        if not notes.strip():
            add(
                "teacher_notes_empty",
                [number],
                "没有教师讲解备注；请补充提问、等待、追问及过渡。",
            )
        if sum(map(len, visible)) > 420 or len(slide.get("content", [])) > 6:
            add(
                "visible_text_dense",
                [number],
                "可见文字较多（超过420字或6条正文）；请检查是否应拆成讲解页和笔记页，不自动删减。",
            )

    objectives = []
    for objective in candidate.get("objectives", []):
        linked = [p for p in pages if objective["id"] in p["objective_ids"]]
        note_pages = [p["page"] for p in linked if "笔记整理线索" in p["role_hints"]]
        objectives.append(
            {
                "id": objective["id"],
                "statement": objective.get("statement", ""),
                "pages": [p["page"] for p in linked],
                "note_hint_pages": note_pages,
            }
        )
        if not linked:
            add(
                "objective_without_slide",
                [],
                f"目标 {objective['id']} 未关联任何课件页。",
            )
        elif not note_pages:
            add(
                "objective_note_not_detected",
                [p["page"] for p in linked],
                f"目标 {objective['id']} 的关联页未检测到笔记整理标记；请检查定义、条件、关系和典型例子是否能供学生记录。",
            )

    task_links = []
    for page in pages:
        if "学生任务线索" not in page["role_hints"]:
            continue
        later = [
            other["page"]
            for other in pages
            if other["page"] > page["page"]
            and "反馈核对线索" in other["role_hints"]
            and _related_feedback(page, other)
        ]
        task_links.append({"task_page": page["page"], "possible_feedback_pages": later})
        if not later:
            add(
                "later_feedback_not_detected",
                [page["page"]],
                "后续未检测到同活动同目标，或同题号同评价同目标的反馈页；请检查练习是否逐项讲解并留出订正时间。",
            )
        if "反馈核对线索" in page["role_hints"]:
            add(
                "task_feedback_same_page",
                [page["page"]],
                "本页同时带有任务和反馈标记；请检查静态投影是否提前露出答案。",
            )

    return {
        "schema_version": "classroom-review-v1",
        "title": str(candidate.get("title", "")),
        "status": "teacher_review_required",
        "method": "explicit_links_and_keyword_hints_only",
        "note": "页码按实际候选顺序；关键词仅定位线索。有关联不等于答案匹配，有来源编号不等于来源已核验。未发现提示也不代表可以直接授课。",
        "objectives": objectives,
        "pages": pages,
        "task_links": task_links,
        "findings": findings,
    }


def format_classroom_review(report: Mapping[str, Any]) -> str:
    """Plain text can be selected/copied without injecting source HTML."""

    def page_list(numbers: list[int]) -> str:
        return "、".join(map(str, numbers)) if numbers else "未定位"

    lines = [report["title"], "课堂结构检查", report["note"], "", "建议核对顺序"]
    lines.extend(
        [
            "1. Word 的本课范围与教材知识点是否一一落实，有无超出范围或遗漏。",
            "2. 问题之后是否建立概念并说明条件，练习之后是否逐项反馈。",
            "3. 笔记页是否有可记录的内容，学习单是否留白，教师是否安排停顿。",
            "4. 图片是否服务于当前问题，末页是否回到开场问题并检查目标达成。",
            "",
            "待核对提示",
        ]
    )
    for row in report["findings"]:
        lines.append(f"第 {page_list(row['pages'])} 页：{row['message']}")
    if not report["findings"]:
        lines.append("未触发规则提示；仍须检查实际题目、答案、来源和投影页面。")
    lines.extend(["", "目标与笔记定位"])
    for row in report["objectives"]:
        lines.extend(
            [
                f"{row['id']} {row['statement']}",
                f"关联页：{page_list(row['pages'])}；笔记线索页：{page_list(row['note_hint_pages'])}",
            ]
        )
    lines.extend(["", "任务与可能的反馈位置（必须逐题核对）"])
    for row in report["task_links"]:
        lines.append(
            f"任务第 {row['task_page']} 页 → 后续相关反馈线索：{page_list(row['possible_feedback_pages'])}"
        )
    lines.extend(["", "逐页内容与教师备注"])
    for page in report["pages"]:
        lines.extend(
            [
                "",
                f"第 {page['page']} 页　{page['title']}　建议 {page['minutes']} 分钟",
                "标记："
                + ("、".join(page["role_hints"]) or "未检测到任务/反馈/笔记标记"),
                "学生可见文字：",
                *page["visible_text"],
            ]
        )
        if page["has_image"]:
            lines.append("本页含图片；图片内容与清晰度需打开 PPT 核对。")
        lines.extend(["教师备注及来源说明：", page["teacher_notes"] or "未填写"])
    return "\n".join(lines)
