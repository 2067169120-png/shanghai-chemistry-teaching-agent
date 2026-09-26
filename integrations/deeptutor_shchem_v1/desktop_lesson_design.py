"""One local teaching design shared by lesson notes, slides and student tasks.

Stored in the existing preparation payload; no parallel question/student store.
A teacher-confirmed link is not a machine claim about learning mastery.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import uuid4

SCHEMA = "shchem.lesson-design.v1"
KINDS = ("课前诊断", "观察与提问", "解释与建模", "例题讲解", "独立练习", "反馈与小结")
TEXT_FIELDS = ("title", "teacher_action", "student_task", "expected_output",
               "criteria", "notes", "material_text", "teacher_answer")
NODE_FIELDS = {"id", "kind", "minutes", "objective_ids", "confirmed", "locked",
               "student_material", "image_ids", "source_digest", *TEXT_FIELDS}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def new_node(kind="独立练习"):
    return {"id": "N-" + uuid4().hex, "kind": kind, "minutes": None,
            "objective_ids": [], "confirmed": False, "locked": False,
            "student_material": False, "image_ids": [], "source_digest": "",
            **{k: (kind if k == "title" else "") for k in TEXT_FIELDS}}


def new_design(payload):
    goals = [s.strip() for s in payload.get("objective", "").splitlines() if s.strip()]
    return {"schema_version": SCHEMA, "id": "LD-" + uuid4().hex,
            "objectives": [{"id": "O-" + uuid4().hex, "text": s} for s in goals],
            "nodes": [], "exports": []}


def validate_design(value):
    """Accept incomplete drafts, but never silently drop an unknown field."""
    if not isinstance(value, dict) or set(value) != {"schema_version", "id", "objectives", "nodes", "exports"}:
        raise ValueError("教学设计字段不完整。")
    if value["schema_version"] != SCHEMA or not re.fullmatch(r"LD-[a-f0-9]{32}", str(value["id"])):
        raise ValueError("教学设计版本或标识不正确。")
    for key, limit in (("objectives", 40), ("nodes", 80), ("exports", 100)):
        if not isinstance(value[key], list) or len(value[key]) > limit:
            raise ValueError("教学设计内容过多，请按课时拆分。")
    ids = set()
    for goal in value["objectives"]:
        if (not isinstance(goal, dict) or set(goal) != {"id", "text"}
                or not re.fullmatch(r"O-[a-f0-9]{32}", str(goal["id"]))
                or goal["id"] in ids or not isinstance(goal["text"], str) or len(goal["text"]) > 2000):
            raise ValueError("教学目标格式不正确。")
        ids.add(goal["id"])
    seen = set()
    for node in value["nodes"]:
        if (not isinstance(node, dict) or set(node) != NODE_FIELDS
                or not re.fullmatch(r"N-[a-f0-9]{32}", str(node["id"])) or node["id"] in seen
                or node["kind"] not in KINDS):
            raise ValueError("教学环节格式不正确。")
        seen.add(node["id"])
        if any(not isinstance(node[k], str) or len(node[k]) > 120000 for k in TEXT_FIELDS):
            raise ValueError("环节文字格式不正确或超长。")
        if node["minutes"] is not None and (type(node["minutes"]) is not int or not 0 <= node["minutes"] <= 180):
            raise ValueError("用时须为0至180分钟，未估时可以留空。")
        for field in ("objective_ids", "image_ids"):
            if (not isinstance(node[field], list) or any(not isinstance(x, str) for x in node[field])
                    or len(set(node[field])) != len(node[field])):
                raise ValueError("环节关联列表有重复或无效标识。")
        if not set(node["objective_ids"]) <= ids:
            raise ValueError("环节引用的教学目标已经不存在。")
        if any(not re.fullmatch(r"IMG-[a-f0-9]{64}", x) for x in node["image_ids"]):
            raise ValueError("环节图片不是本地素材标识。")
        if any(type(node[k]) is not bool for k in ("confirmed", "locked", "student_material")):
            raise ValueError("环节确认状态不正确。")
        if not isinstance(node["source_digest"], str) or (node["source_digest"] and
                not re.fullmatch(r"[a-f0-9]{64}", node["source_digest"])):
            raise ValueError("材料来源摘要不正确。")
    for out in value["exports"]:
        if (not isinstance(out, dict) or set(out) != {"id", "fingerprint", "created_at", "files"}
                or not re.fullmatch(r"OUT-[a-f0-9]{32}", str(out["id"]))
                or not re.fullmatch(r"[a-f0-9]{64}", str(out["fingerprint"]))
                or not isinstance(out["created_at"], str) or not isinstance(out["files"], list)):
            raise ValueError("教学设计成品记录不正确。")
        for f in out["files"]:
            if (set(f) != {"name", "sha256"} or not re.fullmatch(r"[A-Za-z0-9_.-]+", f["name"])
                    or not re.fullmatch(r"[a-f0-9]{64}", f["sha256"])):
                raise ValueError("成品文件记录不正确。")
    return deepcopy(value)


def content_fingerprint(payload, plan=None):
    plan = validate_design(plan if plan is not None else payload["lesson_design"])
    return digest({"design": {k: v for k, v in plan.items() if k != "exports"},
                   "topic": payload.get("topic", ""), "audience": payload.get("audience", ""),
                   "timing": payload.get("lesson_timing", ""),
                   "image_assets": payload.get("image_assets", [])})


def coverage(plan, budget=None):
    plan = validate_design(plan)
    result = []
    for goal in plan["objectives"]:
        linked = [n for n in plan["nodes"] if goal["id"] in n["objective_ids"]]
        verified = [n for n in linked if n["confirmed"] and n["student_task"].strip()
                    and n["expected_output"].strip() and n["criteria"].strip()]
        result.append({"id": goal["id"], "text": goal["text"],
                       "status": "已明确关联" if verified else ("待核对" if linked else "未安排"),
                       "nodes": [n["id"] for n in linked]})
    minutes = sum(n["minutes"] or 0 for n in plan["nodes"])
    return {"objectives": result, "known_minutes": minutes,
            "unestimated": sum(n["minutes"] is None for n in plan["nodes"]),
            "over_budget": budget is not None and minutes > budget,
            "unlinked_nodes": [n["id"] for n in plan["nodes"] if not n["objective_ids"]]}


def update_goal(plan, identity, text):
    updated = validate_design(plan)
    goal = next(g for g in updated["objectives"] if g["id"] == identity)
    if goal["text"] != text:
        goal["text"] = text
        for n in updated["nodes"]:
            if identity in n["objective_ids"]:
                n["confirmed"] = False
    return validate_design(updated)


def update_node(plan, identity, changes, *, expected=None):
    updated = validate_design(plan)
    if expected is not None and digest(updated) != expected:
        raise ValueError("教学环节已修改，请重新核对后再应用。")
    if set(changes) - (NODE_FIELDS - {"id", "source_digest"}):
        raise ValueError("本次修改包含不属于环节编辑的字段。")
    n = next(n for n in updated["nodes"] if n["id"] == identity)
    if n["locked"] and any(k in changes and changes[k] != n[k]
                          for k in ("material_text", "teacher_answer", "image_ids", "student_material")):
        raise ValueError("题目、答案和图片已锁定；请先明确解锁。")
    changed = any(v != n[k] for k, v in changes.items())
    n.update(deepcopy(changes))
    # Confirmation alone is explicit; any substantive later edit needs a new check.
    if changed and set(changes) - {"confirmed", "locked", "minutes", "notes"}:
        n["confirmed"] = False
    return validate_design(updated)


def bind_material(plan, identity, text, images=()):
    plan = validate_design(plan)
    n = next(n for n in plan["nodes"] if n["id"] == identity)
    if n["locked"]:
        raise ValueError("当前材料已锁定，请先解锁再替换。")
    n.update(material_text=text, image_ids=list(images), student_material=False,
             source_digest=digest({"text": text, "images": list(images)}), locked=True, confirmed=False)
    return validate_design(plan)


class DesignHistory:
    """Small local history of design edits. Output files never disappear on undo."""
    def __init__(self, value):
        self.value = validate_design(value)
        self.back, self.forward = [], []

    def put(self, value):
        value = validate_design(value)
        if value != self.value:
            self.back.append(deepcopy(self.value))
            self.back = self.back[-50:]
            self.forward.clear()
            self.value = value

    def travel(self, redo=False):
        source, target = (self.forward, self.back) if redo else (self.back, self.forward)
        if source:
            target.append(deepcopy(self.value))
            old = source.pop()
            known = {x["id"]: x for x in old["exports"] + self.value["exports"]}
            old["exports"] = list(known.values())
            self.value = old
        return deepcopy(self.value)


def candidate_from_design(payload):
    """Project the same ordered nodes to the existing native PPT renderer."""
    plan = validate_design(payload["lesson_design"])
    if not payload.get("topic", "").strip() or not plan["nodes"]:
        raise ValueError("请填写课题并至少安排一个教学环节。")
    from .desktop_preparation import normalize_preparation_payload
    # Local drafts may be incomplete; export doesn't pretend missing fields were assessed.
    normalized = normalize_preparation_payload({**payload, "objective": payload.get("objective") or "待核对",
                                                "materials": payload.get("materials") or "本地环节设计",
                                                "audience": payload.get("audience") or "待填写"})
    c = {"schema_version": "shchem.desktop-preparation-candidate.v1", "candidate_id": plan["id"],
         "title": payload["topic"], "topic": payload["topic"], "audience": payload.get("audience", ""),
         "artifact_mode": "ppt", "lesson_route": normalized["lesson_route"], "timing": normalized["timing"],
         "source_basis": {"mode": "teacher_nodes_local", "evidence_ids": [],
                          "statement_zh": "教师本地教学环节；未调用模型，保留手工材料与待核对目标"},
         "objectives": [{"id": g["id"], "statement": g["text"]} for g in plan["objectives"]],
         "activities": [], "assessments": [], "lesson_stages": [], "slides": [],
         "homework": {"title": "课后安排", "tasks": [], "estimated_minutes": 0},
         "uncertainties": [], "candidate_only": True, "teacher_review_required": True,
         "publication_allowed": False, "official_claim_allowed": False,
         "image_assets": deepcopy(payload.get("image_assets", []))}
    image_ids = {i["asset_id"] for i in c["image_assets"]}
    for n in plan["nodes"]:
        if not n["title"].strip():
            raise ValueError("请为每个教学环节填写标题。")
        if not set(n["image_ids"]) <= image_ids:
            raise ValueError(n["title"] + "引用的图片已从备课中移除，请重新关联。")
        aid = n["id"]
        notes = "\n\n".join(label + "：" + n[k] for k, label in (
            ("teacher_action", "教师活动"), ("expected_output", "预期产出"),
            ("criteria", "评价依据"), ("teacher_answer", "教师答案"), ("notes", "教师备注")) if n[k])
        c["activities"].append({"id": aid, "title": n["title"], "objective_ids": n["objective_ids"],
                                "minutes": n["minutes"] or 0, "teacher_action": n["teacher_action"],
                                "student_action": n["student_task"], "materials": [n["material_text"]]})
        def slide(title, content, image=None):
            s = {"id": "S-" + str(len(c["slides"]) + 1), "order": len(c["slides"]) + 1,
                 "title": title, "slide_type": "content", "purpose": n["kind"],
                 "objective_ids": n["objective_ids"], "activity_ids": [aid], "assessment_ids": [],
                 "content": content, "teacher_notes": notes}
            if image:
                s["image"] = {"asset_id": image, "observation_prompt": n["student_task"][:400] or "观察材料，记录证据。"}
            c["slides"].append(s)
        # Don't silently squeeze long original material into a clipped slide.
        # Full unmodified text remains in DOCX; the teacher must shorten/split for slides.
        if len(n["student_task"]) > 350 or (n["student_material"] and len(n["material_text"]) > 550):
            raise ValueError(n["title"] + "的学生可见文字过长，请拆分环节或另用原资料，不会截断原题。")
        body = ([n["material_text"]] if n["student_material"] and n["material_text"] else [])
        body += [n["student_task"]] if n["student_task"] else []
        slide(n["title"], body or ["本环节学生任务待补充"])
        if n["student_material"]:
            for im in n["image_ids"]:
                slide(n["title"] + " · 材料图", [], im)
        if n["minutes"] is not None:
            c["slides"][-(1 + len(n["image_ids"]) if n["student_material"] else 1)]["minutes"] = n["minutes"]
    return c
