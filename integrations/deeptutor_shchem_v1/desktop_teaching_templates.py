"""Teacher-owned reusable structures in the existing personal state.

Only explicit teacher organisation text is projected. Original materials,
question blocks, image identities and output/source references are never part
of a template. Applying one is a pure design edit, not a material association.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from uuid import uuid4

from .desktop_lesson_design import KINDS, digest, new_node, validate_design
from .desktop_state import DesktopStateError, _reject_sensitive_fields, utc_now
from .desktop_teaching_framework import FIELDS, framework_from_design, validate_framework

LIBRARY_SCHEMA = "shchem.teaching-template-library.v1"
RECORD_SCHEMA = "shchem.personal-teaching-template.v1"
STRUCTURE_SCHEMA = "shchem.teaching-structure.v1"
TEACHER_FIELDS = ("title", "teacher_action", "student_task", "expected_output", "criteria")
MAX_RECORDS = 400


def _text(value, maximum, message, *, required=False):
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(char) < 32 and char not in "\n\r\t" for char in value)
            or (required and not value.strip())):
        raise ValueError(message)
    return value


def validate_structure(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "framework", "objectives", "nodes"}
            or value["schema_version"] != STRUCTURE_SCHEMA):
        raise ValueError("教学结构模板版本或字段不正确。")
    if value["framework"] is not None:
        validate_framework(value["framework"])
    goals, nodes = value["objectives"], value["nodes"]
    if not isinstance(goals, list) or len(goals) > 40 or not isinstance(nodes, list) or len(nodes) > 80:
        raise ValueError("模板最多保存40个目标和80个教学环节。")
    for goal in goals:
        _text(goal, 2000, "模板目标格式不正确或文字过长。")
    for node in nodes:
        if (not isinstance(node, dict) or set(node) != {*TEACHER_FIELDS, "kind", "minutes", "objective_indices"}
                or node["kind"] not in KINDS):
            raise ValueError("模板环节字段不完整。")
        for key in TEACHER_FIELDS:
            _text(node[key], 12000, "模板教学组织文字过长，请选取需要复用的结构。")
        if node["minutes"] is not None and (type(node["minutes"]) is not int or not 0 <= node["minutes"] <= 180):
            raise ValueError("模板环节用时须为0至180分钟。")
        indices = node["objective_indices"]
        if (not isinstance(indices, list) or len(set(map(str, indices))) != len(indices)
                or any(type(index) is not int or not 0 <= index < len(goals) for index in indices)):
            raise ValueError("模板环节目标关联不正确。")
    _reject_sensitive_fields(value)
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
        raise ValueError("模板结构超过512KB，请按课时拆分。")
    return deepcopy(value)


def capture_structure(plan, *, node_ids=None, include_framework=True, include_objectives=True):
    """Project selected structure without modifying the original design."""
    plan = validate_design(plan)
    if type(include_framework) is not bool or type(include_objectives) is not bool:
        raise ValueError("请明确选择要保存的模板内容。")
    available = {node["id"] for node in plan["nodes"]}
    if node_ids is None:
        node_ids = [node["id"] for node in plan["nodes"]]
    if (not isinstance(node_ids, list) or any(not isinstance(identity, str) for identity in node_ids)
            or len(set(node_ids)) != len(node_ids) or not set(node_ids) <= available):
        raise ValueError("所选教学环节已变化，请重新选择。")
    goals = deepcopy(plan["objectives"]) if include_objectives else []
    indices = {goal["id"]: index for index, goal in enumerate(goals)}
    nodes = [{**{key: node[key] for key in TEACHER_FIELDS}, "kind": node["kind"],
              "minutes": node["minutes"],
              "objective_indices": [indices[key] for key in node["objective_ids"] if key in indices]}
             for node in plan["nodes"] if node["id"] in node_ids]
    return validate_structure({"schema_version": STRUCTURE_SCHEMA,
        "framework": framework_from_design({"lesson_design": plan}) if include_framework else None,
        "objectives": [goal["text"] for goal in goals], "nodes": nodes})


def _time(value):
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError("教学模板时间记录不正确。")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("教学模板时间记录不正确。") from exc


def validate_record(value):
    fields = {"schema_version", "id", "name", "created_at", "updated_at", "deleted_at", "revision", "structure"}
    if isinstance(value, dict) and "name_history" in value:
        fields.add("name_history")
    if (not isinstance(value, dict) or set(value) != fields
            or value["schema_version"] != RECORD_SCHEMA
            or not re.fullmatch(r"TT-[a-f0-9]{32}", str(value["id"]))
            or not re.fullmatch(r"[a-f0-9]{32}", str(value["revision"]))):
        raise ValueError("个人教学模板版本或标识不正确。")
    _text(value["name"], 80, "模板名称须为1至80个字符。", required=True)
    for key in ("created_at", "updated_at"):
        _time(value[key])
    if value["deleted_at"] is not None:
        _time(value["deleted_at"])
    history = value.get("name_history", [])
    if not isinstance(history, list) or len(history) > 30:
        raise ValueError("教学模板名称历史格式不正确。")
    for row in history:
        if not isinstance(row, dict) or set(row) != {"name", "changed_at"}:
            raise ValueError("教学模板名称历史字段不正确。")
        _text(row["name"], 80, "模板历史名称不正确。", required=True)
        _time(row["changed_at"])
    validate_structure(value["structure"])
    return {**deepcopy(value), "name_history": deepcopy(history)}


def empty_library():
    return {"schema_version": LIBRARY_SCHEMA, "records": {}}


def validate_library(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "records"}
            or value["schema_version"] != LIBRARY_SCHEMA or not isinstance(value["records"], dict)
            or len(value["records"]) > MAX_RECORDS):
        raise ValueError("个人教学模板库格式不正确。")
    result = deepcopy(value)
    for identity, record in value["records"].items():
        normalized = validate_record(record)
        if normalized["id"] != identity:
            raise ValueError("个人教学模板身份与记录不一致。")
        result["records"][identity] = normalized
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("个人教学模板库超过8MB，请精简教学组织文字。")
    return result


class TeachingTemplateService:
    def __init__(self, store):
        self.store = store

    def search(self, query="", *, include_deleted=False):
        _text(query, 200, "模板搜索文字过长。")
        library = validate_library(self.store.snapshot().get("teaching_templates", empty_library()))
        query = query.strip().casefold()
        rows = [deepcopy(record) for record in library["records"].values()
                if (include_deleted or record["deleted_at"] is None)
                and (not query or query in (record["name"] + "\n" + json.dumps(
                    record["structure"], ensure_ascii=False)).casefold())]
        return sorted(rows, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def load(self, identity, *, include_deleted=False):
        if not isinstance(identity, str) or not re.fullmatch(r"TT-[a-f0-9]{32}", identity):
            raise ValueError("模板标识不正确。")
        library = validate_library(self.store.snapshot().get("teaching_templates", empty_library()))
        record = library["records"].get(identity)
        if record is None or (record["deleted_at"] is not None and not include_deleted):
            raise ValueError("该模板已删除或不存在，请重新读取。")
        return deepcopy(record)

    def save(self, name, structure):
        _text(name, 80, "模板名称须为1至80个字符。", required=True)
        structure = validate_structure(structure)
        now = utc_now()
        record = validate_record({"schema_version": RECORD_SCHEMA, "id": "TT-" + uuid4().hex,
            "name": name.strip(), "created_at": now, "updated_at": now, "deleted_at": None,
            "revision": uuid4().hex, "name_history": [], "structure": structure})
        def operation(state):
            library = validate_library(state.get("teaching_templates", empty_library()))
            library["records"][record["id"]] = deepcopy(record)
            state["teaching_templates"] = validate_library(library)
        self.store._update(operation)
        return deepcopy(record)

    def _change(self, expected, *, name=None, deleted=None, undo_name=False):
        expected = validate_record(expected)
        if name is not None:
            _text(name, 80, "模板名称须为1至80个字符。", required=True)
        result = {}
        def operation(state):
            library = validate_library(state.get("teaching_templates", empty_library()))
            current = library["records"].get(expected["id"])
            if current != expected:
                raise DesktopStateError("模板已在另一窗口修改，请重新读取后操作。")
            changed = deepcopy(current)
            if undo_name:
                if not changed["name_history"]:
                    raise ValueError("没有可撤销的重命名记录。")
                changed["name"] = changed["name_history"].pop()["name"]
            elif name is not None and name.strip() != current["name"]:
                changed["name_history"].append({"name": current["name"], "changed_at": utc_now()})
                changed["name_history"] = changed["name_history"][-30:]
                changed["name"] = name.strip()
            if deleted is not None:
                changed["deleted_at"] = utc_now() if deleted else None
            if changed == current:
                result.update(current)
                return False
            changed.update(updated_at=utc_now(), revision=uuid4().hex)
            library["records"][changed["id"]] = validate_record(changed)
            state["teaching_templates"] = validate_library(library)
            result.update(changed)
        self.store._update(operation)
        return deepcopy(result)

    def rename(self, expected, name):
        return self._change(expected, name=name)

    def undo_rename(self, expected):
        return self._change(expected, undo_name=True)

    def delete(self, expected):
        return self._change(expected, deleted=True)

    def restore(self, expected):
        return self._change(expected, deleted=False)


def can_replace_empty_structure(plan):
    plan = validate_design(plan)
    identities = {node["id"] for node in plan["nodes"]}
    def references_node(value):
        if isinstance(value, dict): return any(references_node(row) for row in value.values())
        if isinstance(value, (list, tuple)): return any(references_node(row) for row in value)
        return isinstance(value, str) and value in identities
    # A blank-looking node may already be the source of an actual classroom
    # page. Replacing its identity would strand that page's live association.
    if references_node(plan.get("composition", {})):
        return False
    return all(node["title"] in ("", node["kind"]) and node["minutes"] is None
        and not any(node[key] for key in (*TEACHER_FIELDS[1:], "notes", "material_text", "teacher_answer",
                                        "objective_ids", "image_ids", "source_digest"))
        and not any(node[key] for key in ("confirmed", "locked", "student_material"))
        for node in plan["nodes"])


def apply_structure(plan, structure, *, expected_fingerprint, mode="append", fill_framework=True):
    """Pure edit; caller explicitly previews and puts this in DesignHistory."""
    result = validate_design(plan)
    structure = validate_structure(structure)
    if digest(result) != expected_fingerprint:
        raise ValueError("当前教学设计已变化，请重新预览模板。")
    if mode not in ("append", "replace_empty") or type(fill_framework) is not bool:
        raise ValueError("教学模板应用方式不正确。")
    if mode == "replace_empty":
        if not can_replace_empty_structure(result):
            raise ValueError("当前结构已有编辑，请选择新增环节。")
        result["nodes"] = []
    goals = [{"id": "O-" + uuid4().hex, "text": text} for text in structure["objectives"]]
    result["objectives"].extend(goals)
    for row in structure["nodes"]:
        node = new_node(row["kind"])
        node.update({key: row[key] for key in TEACHER_FIELDS}, minutes=row["minutes"],
                    objective_ids=[goals[index]["id"] for index in row["objective_indices"]])
        result["nodes"].append(node)
    if fill_framework and structure["framework"] is not None:
        current = framework_from_design({"lesson_design": result})
        # Existing teacher text wins even when a template uses the same name.
        current.update({key: structure["framework"][key] for key, _ in FIELDS if not current[key].strip()})
        result["teaching_framework"] = validate_framework(current)
    return validate_design(result)


def structure_text(structure):
    structure = validate_structure(structure)
    lines = []
    if structure["framework"] is not None:
        lines.append("教案框架")
        lines.extend(label + "\n" + structure["framework"][key]
                     for key, label in FIELDS if structure["framework"][key])
    if structure["objectives"]:
        lines.append("教学目标\n" + "\n".join(structure["objectives"]))
    labels = {"teacher_action": "教师活动", "student_task": "学生任务",
              "expected_output": "预期产出", "criteria": "评价依据"}
    for index, node in enumerate(structure["nodes"], 1):
        timing = "未估时" if node["minutes"] is None else str(node["minutes"]) + "分钟"
        lines.append(f"{index}. {node['title'] or node['kind']} · {node['kind']} · {timing}")
        lines.extend(label + "：\n" + node[key] for key, label in labels.items() if node[key])
    return "\n\n".join(lines) or "空白教学结构"
