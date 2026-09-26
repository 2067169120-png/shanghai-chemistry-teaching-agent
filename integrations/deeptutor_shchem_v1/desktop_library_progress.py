"""On-demand progress from current catalogues, never historical README totals.

This module reads no credentials, sends no requests and writes no labels.
Word candidates and image printed questions are deliberately not added together.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from .desktop_word_question_attributes import automatic_tags_protected
from .desktop_word_question_filters import (
    _bound_attributes, _directory, _exam, _grades, _knowledge, _mappings,
)


TODO_LABELS = {
    "primary": "主知识点", "curriculum": "教材节", "grade": "适用年级",
    "exam": "原考试类型", "stale": "重核旧标签", "material": "补齐原图或公共材料",
}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _protected(attributes):
    """Use the write service's protection rule, also for incomplete legacy rows."""
    value = {name: _text(attributes.get(name)) for name in (
        "annotation_source", "rule_revision", "curriculum_status")}
    primary = attributes.get("primary_knowledge")
    value["primary_knowledge"] = {"status": _text(
        primary.get("status")) if isinstance(primary, Mapping) else ""}
    for name in ("supporting_knowledge", "curriculum_candidates"):
        candidates = attributes.get(name)
        value[name] = [{"status": _text(row.get("status"))}
                       for row in candidates if isinstance(row, Mapping)] if isinstance(candidates, list) else []
    return automatic_tags_protected(value)


def summarize_word_catalog(catalog: Mapping) -> dict:
    rows, seen = [], set()
    warnings = list(catalog.get("warnings", []))
    nodes = _directory(catalog.get("attribute_catalog"))
    if not nodes:
        warnings.append("教材目录暂不可核对，教材节不计为有效；可打开原题核对标签。")
    counts = dict(candidates=0, primary=0, curriculum=0, grade=0, exam=0,
                  complete_candidates=0, stale_labels=0, material_gaps=0,
                  pending_candidates=0, protected_pending=0)
    for item in catalog.get("items", []):
        if not isinstance(item, Mapping):
            warnings.append("发现无法读取的Word条目，未计入候选。")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key or key in seen:
            warnings.append("发现无身份或重复的Word条目，未重复计数。")
            continue
        seen.add(key)
        attrs = _bound_attributes(item)
        stale = bool(item.get("attributes")) and not attrs
        original_attrs = item.get("attributes")
        protected = _protected(original_attrs if isinstance(original_attrs, Mapping) else {}) or bool(item.get("attribute_warning"))
        present = {
            "primary": bool(_knowledge({"primary_knowledge": attrs.get("primary_knowledge")})),
            "curriculum": bool(_mappings(attrs, nodes)),
            "grade": bool(_grades(attrs)),
            "exam": _exam(attrs) != "unknown",
        }
        material = attrs.get("material_status") or {}
        material = material if isinstance(material, Mapping) else {}
        gap = bool(material.get("missing_context") or material.get("missing_visual"))
        counts["candidates"] += 1
        for field, value in present.items():
            counts[field] += int(value)
        complete = all(present.values())
        counts["complete_candidates"] += int(complete)
        counts["stale_labels"] += int(stale or bool(item.get("attribute_warning")))
        counts["material_gaps"] += int(gap)
        todo_keys = [field for field in present if not present[field]]
        if stale or item.get("attribute_warning"):
            todo_keys.insert(0, "stale")
        if gap:
            todo_keys.insert(0, "material")
        blocks = [block for name in ("context_blocks", "question_blocks")
                  for block in (item.get(name) or []) if isinstance(block, Mapping)]
        question_text = " ".join(_text(block.get("text")) for block in blocks)
        counts["pending_candidates"] += bool(todo_keys)
        counts["protected_pending"] += bool(todo_keys) and protected
        rows.append({
            "key": key, "source": _text(item.get("source_name")),
            "source_id": _text(item.get("source_id")),
            "source_sha256": _text(item.get("source_sha256")),
            "revision": _text(item.get("revision")),
            "question_title": _text(item.get("title")) or _text(item.get("title_zh")),
            "chapter": _text(item.get("chapter")),
            "excerpt": " ".join(question_text.split())[:240],
            "has_visual": any(bool(block.get("assets")) for block in blocks),
            "protected": protected, "todo_keys": todo_keys,
            "todo": [TODO_LABELS[field] for field in todo_keys],
            "labels_complete_candidate": complete,
        })
    return {"counts": counts, "source_count": len(catalog.get("sources", [])),
            "rows": rows, "warnings": list(dict.fromkeys(warnings))}


def collect_library_progress(facade) -> dict:
    """A user-triggered snapshot; unavailable sources are not reported as zero."""
    report = {"schema_version": "desktop-library-progress-v1",
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "word": None, "visual": None, "errors": [],
              "note": "标签齐备只表示当前绑定字段有效，不代表化学审核；两库可能重复，不能相加。"}
    try:
        report["word"] = summarize_word_catalog(facade.word_question_catalog())
    except Exception:  # independent source failures keep the other panel usable
        report["errors"].append("Word目录读取失败，请到导入历史核对；未记作0题。")
    try:
        catalog = facade.personal_visual_questions()
        rows = {row["key"]: row for row in catalog["items"]}
        report["visual"] = {
            "printed_questions": len(rows),
            "themes": len({(row["batch_id"], row["theme_key"]) for row in rows.values()}),
            "not_selectable": sum(not row.get("selection_ready", False) for row in rows.values()),
            "warnings": list(catalog.get("warnings", [])),
        }
    except Exception:
        report["errors"].append("个人图片题目录读取失败，请核对来源或教材标签目录；未记作0题。")
    return report
