"""Teacher-authored scripts bound to a registered, immutable PPTX version."""
from copy import deepcopy
import re

SCHEMA = "shchem.lesson-narration.v1"
PRESENTATIONS = ("student_presentation.pptx", "lesson_presentation.pptx")
MAX_PAGE_TEXT = 20000
MAX_TOTAL_TEXT = 500000
FIELDS = {"schema_version", "export_id", "presentation_name", "source_sha256", "page_count", "scripts"}


def validate_narrations(records, exports):
    if not isinstance(records, list) or len(records) > 200:
        raise ValueError("逐页讲稿过多，请按课时拆分。")
    known = {r["id"]: {f["name"]: f["sha256"] for f in r["files"]} for r in exports}
    seen = set()
    for row in records:
        if (not isinstance(row, dict) or set(row) != FIELDS or row["schema_version"] != SCHEMA
                or row["presentation_name"] not in PRESENTATIONS
                or not isinstance(row["export_id"], str) or row["export_id"] not in known
                or not isinstance(row["source_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", row["source_sha256"]) is None
                or known[row["export_id"]].get(row["presentation_name"]) != row["source_sha256"]
                or type(row["page_count"]) is not int or not 1 <= row["page_count"] <= 2000):
            raise ValueError("讲稿与所选课件版本不匹配，请重新核对。")
        key = (row["export_id"], row["presentation_name"], row["page_count"])
        if key in seen:
            raise ValueError("同一课件版本的讲稿记录重复。")
        seen.add(key)
        scripts = row["scripts"]
        if (not isinstance(scripts, list) or len(scripts) != row["page_count"]
                or any(not isinstance(s, str) or len(s) > MAX_PAGE_TEXT for s in scripts)
                or sum(map(len, scripts)) > MAX_TOTAL_TEXT):
            raise ValueError("逐页讲稿文字无效或过长，请按课时拆分；当前输入保留。")
    return deepcopy(records)


def narration_scripts(plan, export_id, name, source_sha256, count):
    from .desktop_lesson_design import validate_design
    plan = validate_design(plan)
    for row in plan.get("narrations", []):
        if (row["export_id"], row["presentation_name"], row["source_sha256"], row["page_count"]) == (
                export_id, name, source_sha256, count):
            return deepcopy(row["scripts"])
    return [""] * count


def put_narration(plan, export_id, name, source_sha256, scripts):
    from .desktop_lesson_design import validate_design
    if not isinstance(scripts, list):
        raise ValueError("讲稿须按课件页保存。")
    plan = validate_design(plan)
    row = dict(schema_version=SCHEMA, export_id=export_id, presentation_name=name,
               source_sha256=source_sha256, page_count=len(scripts), scripts=deepcopy(scripts))
    records = [r for r in plan.get("narrations", [])
               if (r["export_id"], r["presentation_name"], r["page_count"]) !=
               (export_id, name, len(scripts))]
    records.append(row)
    plan["narrations"] = validate_narrations(records, plan["exports"])
    return validate_design(plan)
