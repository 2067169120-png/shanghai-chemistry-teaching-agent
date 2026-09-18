"""Read an existing, hash-checked preparation result into teacher-owned nodes.

No model call and no replacement of existing nodes. The source remains a task;
this is an explicit conversion, not round-trip PowerPoint/Word synchronization.
"""
from __future__ import annotations
from copy import deepcopy
import json
import re

from .desktop_lesson_design import digest, new_node, validate_design
from .desktop_preparation_images import normalize_image_assets
from .desktop_preparation import PREPARATION_CANONICAL_SCHEMA_VERSION


def _joined(values):
    return "\n\n".join(value for value in values if value)


def _strings(values):
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError("原稿文字列表无法转换，请先在原稿中核对。")
    return _joined(values)


def _stable(prefix, source_key, kind, identity):
    return prefix + digest([source_key, kind, identity])[:32]


def prepare_import(plan, source):
    """Create a read-only proposal from the existing facade revision endpoint."""
    plan = validate_design(plan)
    if (not isinstance(source, dict)
            or not re.fullmatch(r"PREP-[a-f0-9]{32}", str(source.get("task_id", "")))
            or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("source_revision", "")))):
        raise ValueError("请从工作台已完成的备课任务读取初稿。")
    candidate = source.get("candidate")
    if (not isinstance(candidate, dict)
            or candidate.get("schema_version") != PREPARATION_CANONICAL_SCHEMA_VERSION):
        raise ValueError("这份初稿尚未整理为可用结构，请先完成原稿修复。")
    for field in ("objectives", "activities", "assessments", "lesson_stages", "slides"):
        rows = candidate.get(field)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("原稿缺少可转换的教学结构。")
        ids = [r.get("id") for r in rows]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("原稿的教学对象标识不完整，请重新核对。")
    assets = normalize_image_assets(candidate.get("image_assets", []))
    source_key = digest([source["task_id"], source["source_revision"]])
    goals, goal_map = [], {}
    by_text = {g["text"]: g["id"] for g in plan["objectives"]}
    for original in candidate["objectives"]:
        text = original["statement"]
        if text not in by_text:
            identity = _stable("O-", source_key, "objective", original["id"])
            by_text[text] = identity
            goals.append({"id": identity, "text": text})
        goal_map[original["id"]] = by_text[text]
    activity_map = {a["id"]: a for a in candidate["activities"]}
    assessments = candidate["assessments"]
    slides = candidate["slides"]
    options, used_activities, used_slides = [], set(), set()
    existing = {n["id"] for n in plan["nodes"]}
    warnings = ["按教学阶段和活动转换，不复刻原PPT版面；原任务及其文件保留。",
                "新增环节均待教师核对。材料、图、答案范围默认锁定且仅教师可见。"]
    uncertainties = candidate.get("uncertainties", [])
    uncertainty_notes = _joined(
        f"待核对：{u.get('description', '')}\n处理建议：{u.get('teacher_action', '')}"
        for u in uncertainties)

    def add(row, group, linked_activities=(), linked_slides=()):
        n = new_node("解释与建模")
        n["id"] = _stable("N-", source_key, group, row["id"])
        n["title"] = row.get("title") or "原稿待命名环节"
        n["teacher_action"] = row.get("teacher_action", "")
        n["student_task"] = row.get("student_action", "")
        # Do not invent a student task from a slide body which may contain answers.
        n["minutes"] = row.get("minutes")
        linked_goals = row.get("objective_ids", [])
        if any(g not in goal_map for g in linked_goals):
            raise ValueError("原稿环节引用了不存在的目标，未自动猜测对应关系。")
        n["objective_ids"] = list(dict.fromkeys(goal_map[g] for g in linked_goals))
        aids = {a["id"] for a in linked_activities}
        if group == "activities":
            aids.add(row["id"])
        specified = set(row.get("assessment_ids", []))
        related = [a for a in assessments if a["id"] in specified or aids.intersection(a.get("activity_ids", []))]
        n["expected_output"] = _joined(a.get("evidence_of_learning", "") for a in related)
        n["criteria"] = _joined([row.get("assessment", ""),
                                 *(_strings(a.get("success_criteria", [])) for a in related)])
        materials = [row.get("materials", [])]
        materials += [a.get("materials", []) for a in linked_activities]
        text = []
        for block in materials:
            value = _strings(block)
            if value and value not in text:
                text.append(value)
        slide_notes, image_ids, complex_pages = [], [], []
        for slide in linked_slides:
            used_slides.add(slide["id"])
            content = _strings(slide.get("content", []))
            if content:
                text.append("原稿页「" + slide["title"] + "」\n" + content)
            if slide.get("teacher_notes"):
                slide_notes.append("原稿页「" + slide["title"] + "」教师讲解\n" + slide["teacher_notes"])
            image = slide.get("image") or {}
            if image.get("asset_id"):
                image_ids.append(image["asset_id"])
            if slide.get("visual"):
                complex_pages.append(slide["title"])
                # Keep the complete structure as a teacher-only reference, not a
                # pretend editable table/diagram. The original task still opens.
                slide_notes.append("原稿结构化图表（需在原PPT核对版式）\n" +
                    json.dumps(slide["visual"], ensure_ascii=False, indent=2))
        if group == "slides":
            text = ["原稿页「" + row["title"] + "」\n" + _strings(row.get("content", []))]
        n["material_text"] = _joined(text)
        n["image_ids"] = list(dict.fromkeys(image_ids))
        n["notes"] = _joined([
            "来自已有初稿，尚未经教师确认。\n来源任务：" + source["task_id"] +
            "\n原稿版本：" + source["source_revision"] + "\n原对象：" + group + "/" + row["id"],
            "用时沿用初稿估计；课堂作用初设为解释与建模，请按实际活动调整。",
            *slide_notes, uncertainty_notes])
        if candidate.get("homework"):
            n["notes"] += "\n\n原稿课后安排（教师参考，未自动加入课堂用时）\n" + json.dumps(
                candidate["homework"], ensure_ascii=False, indent=2)
        n.update(locked=True, student_material=False, confirmed=False,
                 source_digest=digest({"text": n["material_text"], "images": n["image_ids"]}))
        option_warnings = []
        if not n["student_task"]:
            option_warnings.append("学生任务尚未安排，未将原PPT答案或讲解冒充练习。")
        if complex_pages:
            option_warnings.append("结构化图表未复刻版面：" + "、".join(complex_pages))
        if len(n["student_task"]) > 350:
            option_warnings.append("学生任务超过单页建议长度；生成前请拆分，未截断原文。")
        options.append({"id": n["id"], "node": n, "already_present": n["id"] in existing,
                        "warnings": option_warnings})

    for stage in candidate["lesson_stages"]:
        ids = stage.get("activity_ids", [])
        if any(i not in activity_map for i in ids):
            raise ValueError("原稿教学阶段与活动的对应关系不完整。")
        activities = [activity_map[i] for i in ids]
        used_activities.update(ids)
        linked = [s for s in slides if set(ids).intersection(s.get("activity_ids", []))]
        add(stage, "lesson_stages", activities, linked)
    for activity in candidate["activities"]:
        if activity["id"] not in used_activities:
            add(activity, "activities", (), [s for s in slides if activity["id"] in s.get("activity_ids", [])])
    for slide in slides:
        if slide["id"] not in used_slides:
            add(slide, "slides", (), [slide])
    if not options:
        raise ValueError("这份初稿没有可转换的环节或页面。")
    # Validate complete proposal before presenting it; no input is changed.
    validate_design({**plan, "objectives": plan["objectives"] + goals,
                     "nodes": [o["node"] for o in options]})
    known_images = {a["asset_id"] for a in assets}
    if any(set(o["node"]["image_ids"]) - known_images for o in options):
        raise ValueError("原稿引用的图片资料不完整，请先在原任务核对。")
    return {"base_revision": digest(plan), "task_id": source["task_id"],
            "source_revision": source["source_revision"], "title": candidate["title"],
            "objectives": goals, "options": options, "image_assets": assets, "warnings": warnings}


def apply_import(plan, proposal, selected_ids, current_assets=()):
    """Append selected nodes atomically in source order; return new state only."""
    plan = validate_design(plan)
    if digest(plan) != proposal["base_revision"]:
        raise ValueError("教学设计在预览后已变化，请重新读取初稿再确认。")
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("请勾选要加入的环节，不要重复选择。")
    options = {o["id"]: o for o in proposal["options"]}
    if set(selected_ids) - options.keys():
        raise ValueError("勾选环节不属于本次初稿。")
    existing = {n["id"] for n in plan["nodes"]}
    if set(selected_ids) & existing:
        raise ValueError("所选环节已经加入；请编辑已有环节，不重复导入。")
    nodes = [deepcopy(o["node"]) for o in proposal["options"] if o["id"] in selected_ids]
    used_goals = {g for n in nodes for g in n["objective_ids"]}
    plan["objectives"] += [deepcopy(g) for g in proposal["objectives"] if g["id"] in used_goals]
    plan["nodes"].extend(nodes)
    assets = list(normalize_image_assets(list(current_assets)))
    by_id = {a["asset_id"]: a for a in assets}
    used_images = {im for n in nodes for im in n["image_ids"]}
    for image in proposal["image_assets"]:
        if image["asset_id"] in used_images and image["asset_id"] not in by_id:
            assets.append(deepcopy(image)); by_id[image["asset_id"]] = image
    return validate_design(plan), list(normalize_image_assets(assets))
