"""On-demand progress from current catalogues, never historical README totals.

This module reads no credentials, sends no requests and writes no labels.
Word candidates and image printed questions are deliberately not added together.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone


def summarize_word_catalog(catalog: Mapping) -> dict:
    rows, seen = [], set()
    warnings = list(catalog.get("warnings", []))
    counts = dict(candidates=0, primary=0, curriculum=0, grade=0, exam=0,
                  complete_candidates=0, stale_labels=0, material_gaps=0)
    for item in catalog.get("items", []):
        key = item.get("key")
        if not isinstance(key, str) or not key or key in seen:
            warnings.append("发现无身份或重复的Word条目，未重复计数。")
            continue
        seen.add(key)
        attrs = item.get("attributes")
        bound = isinstance(attrs, Mapping) and all(
            attrs.get(field) == item.get(target) and item.get(target)
            for field, target in (("source_sha256", "source_sha256"),
                                  ("question_revision", "revision"))
        )
        stale = bool(attrs) and not bound
        if not bound:
            attrs = {}
        def known(value):
            return isinstance(value, str) and value not in ("", "unknown")
        present = {
            "primary": known(attrs.get("primary_knowledge", {}).get("id")),
            "curriculum": any(known(row.get("section_key"))
                              for row in attrs.get("curriculum_candidates", [])),
            "grade": bool(attrs.get("applicable_grades", {}).get("values")),
            "exam": known(attrs.get("original_source", {}).get("exam_type", {}).get("value")),
        }
        material = attrs.get("material_status", {})
        gap = bool(material.get("missing_context") or material.get("missing_visual"))
        counts["candidates"] += 1
        for field, value in present.items():
            counts[field] += int(value)
        complete = all(present.values())
        counts["complete_candidates"] += int(complete)
        counts["stale_labels"] += int(stale or bool(item.get("attribute_warning")))
        counts["material_gaps"] += int(gap)
        todo = [label for field, label in (("primary", "主知识点"),
                ("curriculum", "教材节"), ("grade", "适用年级"),
                ("exam", "原考试类型")) if not present[field]]
        if stale or item.get("attribute_warning"):
            todo.insert(0, "重核旧标签")
        if gap:
            todo.insert(0, "补齐原图或公共材料")
        rows.append({"key": key, "source": item.get("source_name", ""),
                     "todo": todo, "labels_complete_candidate": complete})
    return {"counts": counts, "source_count": len(catalog.get("sources", [])),
            "rows": rows, "warnings": list(dict.fromkeys(warnings))}


def collect_library_progress(facade) -> dict:
    """A user-triggered snapshot; unavailable sources are not reported as zero."""
    report = {"schema_version": "desktop-library-progress-v1",
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "word": None, "visual": None, "errors": [],
              "note": "标签齐备只表示字段存在，不代表化学审核；两库可能重复，不能相加。"}
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
