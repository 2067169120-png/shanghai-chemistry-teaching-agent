"""On-demand progress from current catalogues, never historical README totals.

This module reads no credentials, sends no requests and writes no labels.
Word candidates and image printed questions are deliberately not added together.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json

from .desktop_personal_visual_attributes import (
    EXAM_TYPE_LABELS, GRADE_LABELS, PersonalVisualAttributeError, validate_attributes,
)

from .desktop_word_question_attributes import automatic_tags_protected
from .desktop_word_question_filters import (
    _bound_attributes, _directory, _exam, _grades, _knowledge, _mappings,
)


TODO_LABELS = {
    "primary": "主知识点", "curriculum": "教材节", "grade": "适用年级",
    "exam": "原考试类型", "stale": "重核旧标签", "material": "补齐原图或公共材料",
}
VISUAL_TODO_LABELS = {
    "not_selectable": "暂不能选用", "primary": "主知识点", "curriculum": "教材节",
    "grade": "适用年级", "exam": "原考试类型", "provenance": "出处年份 / 地区或学校",
    "stale": "重核旧标签", "material": "原图 / 共同材料待核对",
    "hierarchy": "主题层级 / 顺序待核对",
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
        stale = bool(item.get("attribute_stale") or (bool(item.get("attributes")) and not attrs))
        original_attrs = item.get("attributes")
        protected = (_protected(original_attrs if isinstance(original_attrs, Mapping) else {})
                     or bool(item.get("attribute_warning")) or bool(item.get("attribute_protected")))
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


def _known(value):
    return isinstance(value, str) and value.strip() not in ("", "unknown", "blocked_pending_review")


def _visual_attributes(item):
    """Reuse the visual store validator, then require the current source binding."""
    try:
        attrs = validate_attributes(item.get("attributes"))
    except PersonalVisualAttributeError:
        return {}
    binding = attrs["source_binding"]
    if (attrs["key"] != item.get("key") or attrs["question_revision"] != item.get("revision")
            or binding["batch_id"] != item.get("batch_id")
            or binding["candidate_revision"] != item.get("candidate_revision")
            or binding["candidate_sha256"] != item.get("candidate_sha256")):
        return {}
    return attrs


def summarize_visual_catalog(catalog: Mapping) -> dict:
    """Count source-bound printed questions without inventing themes or scores.

    These are label-coverage candidates, including saved teacher proposals and
    source-observed candidates, exactly as in the existing visual editor. They
    are not teacher chemical approval or a successful image-read check.
    """
    if not isinstance(catalog, Mapping) or not isinstance(catalog.get("items"), list):
        raise ValueError("invalid visual catalog")
    warnings = [value for value in catalog.get("warnings", []) if isinstance(value, str)]
    directory = catalog.get("attribute_catalog")
    nodes = _directory(directory)
    knowledge = {entry.get("id"): entry.get("name") for entry in
                 (directory.get("knowledge_points", []) if isinstance(directory, Mapping) else [])
                 if isinstance(entry, Mapping)}
    if catalog["items"] and (not nodes or not knowledge):
        warnings.append("视觉题的教材或知识目录暂不可核对；相应标签保留为待整理，不从文件名补足。")
    rows, seen, themes, batches = [], set(), set(), {}
    counts = {field: 0 for field in ("primary", "curriculum", "grade", "exam", "provenance",
                                    "pending_questions", "protected_pending", "stale_labels", "material_gaps")}
    for index, item in enumerate(catalog["items"]):
        if not isinstance(item, Mapping):
            warnings.append("发现无法读取的视觉题条目，未计入题数。")
            continue
        key, batch = _text(item.get("key")), _text(item.get("batch_id"))
        if not key or not batch or (batch, key) in seen:
            warnings.append("发现无身份或重复的视觉题条目，未重复计数。")
            continue
        seen.add((batch, key))
        batches.setdefault(batch, len(batches))
        attrs = _visual_attributes(item)
        raw_attrs = item.get("attributes")
        raw_attrs = raw_attrs if isinstance(raw_attrs, Mapping) else {}
        origins = raw_attrs.get("field_origins")
        protected = (raw_attrs.get("annotation_source") == "teacher_modified"
                     or raw_attrs.get("teacher_confirmed") is True
                     or (isinstance(origins, Mapping) and "teacher" in origins.values())
                     or bool(item.get("attribute_warning")))
        primary = attrs.get("primary_knowledge", {})
        mappings = attrs.get("curriculum_candidates", [])
        grades = attrs.get("applicable_grades", {})
        original = attrs.get("original_source", {})
        def fact(name):
            value = original.get(name, {})
            return value.get("value") if _known(value.get("status")) else None
        observed = attrs.get("source_observed", {}).get("paper", {})
        observed = observed if isinstance(observed, Mapping) else {}
        # A combined source field is evidence of place, but never split into
        # guessed region/school labels. Missing source facts stay explicit.
        provenance = bool(_known(fact("year")) and (
            _known(fact("region")) or _known(fact("school"))
            or _known(observed.get("source_region_or_school"))))
        present = {
            "primary": bool(primary.get("id") in knowledge and primary.get("id") != "unknown"
                            and primary.get("label") == knowledge.get(primary.get("id"))
                            and _known(primary.get("status"))),
            "curriculum": bool(attrs.get("curriculum_status") in (
                "auto_suggested", "teacher_proposed", "teacher_confirmed", "ai_source_review") and any(
                _known(entry.get("status")) and entry.get("section_key") in nodes
                and all(entry.get(field) == nodes[entry["section_key"]][field]
                        for field in ("volume_id", "chapter_id")) for entry in mappings)),
            "grade": bool(_known(grades.get("status")) and any(
                value in GRADE_LABELS for value in grades.get("values", []))),
            "exam": fact("exam_type") in EXAM_TYPE_LABELS and fact("exam_type") != "unknown",
            "provenance": provenance,
        }
        for field, valid in present.items():
            counts[field] += int(valid)
        todo_keys = [field for field, valid in present.items() if not valid]
        selectable = item.get("selection_ready") is True
        if not selectable:
            todo_keys.insert(0, "not_selectable")
        stale = bool(item.get("attribute_warning") or (raw_attrs and not attrs))
        if stale:
            todo_keys.append("stale")
        material = attrs.get("material_status", {})
        gap = bool(material.get("missing_visual") or material.get("missing_context")
                   or item.get("crop_warning") or item.get("material_review_required"))
        if gap:
            todo_keys.append("material")
        theme_key = _text(item.get("theme_key")) if _known(item.get("theme_key")) else ""
        theme_sequence, printed_sequence = item.get("theme_sequence"), item.get("printed_sequence")
        ordered = item.get("sequence_source") == "candidate_cas" and all(
            type(value) is int and value > 0 for value in (theme_sequence, printed_sequence))
        if not theme_key or not ordered:
            todo_keys.append("hierarchy")
        if theme_key:
            themes.add((batch, theme_key))
        counts["pending_questions"] += bool(todo_keys)
        counts["protected_pending"] += bool(todo_keys) and protected
        counts["stale_labels"] += stale
        counts["material_gaps"] += gap
        rows.append({
            "key": key, "batch_id": batch, "source_id": batch,
            "revision": _text(item.get("revision")), "source": _text(item.get("source_name")),
            "question_title": _text(item.get("title")), "question_number": _text(item.get("question_number")),
            "theme_key": theme_key, "theme_title": _text(item.get("theme_title")),
            "theme_identity": json.dumps([batch, theme_key], ensure_ascii=False) if theme_key else "",
            "theme_sequence": theme_sequence if ordered else None,
            "printed_sequence": printed_sequence if ordered else None,
            "sequence_source": "candidate_cas" if ordered else "unknown",
            "excerpt": " ".join((_text(item.get("shared_text")) + " " + _text(item.get("question_text"))).split())[:240],
            "has_visual": any(isinstance(image, Mapping) and image.get("role") in ("question", "shared_material")
                              for image in item.get("images", [])),
            "protected": protected, "selection_ready": selectable, "todo_keys": todo_keys,
            "todo": [VISUAL_TODO_LABELS[field] for field in todo_keys],
            "labels_complete_candidate": all(present.values()),
            "catalog_position": index,
        })
    # Genuine CAS order is distinct from printed question numbers (which can
    # be non-numeric). Unknown order retains its observed directory position.
    rows.sort(key=lambda row: (batches[row["batch_id"]],
              row["theme_sequence"] if row["theme_sequence"] is not None else float("inf"),
              row["printed_sequence"] if row["printed_sequence"] is not None else row["catalog_position"]))
    return {"printed_questions": len(rows), "themes": len(themes),
            "not_selectable": sum(not row["selection_ready"] for row in rows),
            "unavailable_batches": catalog.get("unavailable_batches", 0),
            "source_count": len(batches), "counts": counts, "rows": rows,
            "warnings": list(dict.fromkeys(warnings))}


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
        report["visual"] = summarize_visual_catalog(facade.personal_visual_questions())
    except Exception:
        report["errors"].append("个人图片题目录读取失败，请核对来源或教材标签目录；未记作0题。")
    return report
