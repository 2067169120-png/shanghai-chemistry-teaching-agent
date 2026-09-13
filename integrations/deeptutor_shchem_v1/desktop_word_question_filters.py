"""Pure multi-select filters for source-bound personal Word questions.

Empty groups are unrestricted. Values within one group are OR; different
groups are AND. A single verified curriculum node must satisfy every selected
curriculum group, so separate mappings cannot manufacture a nonexistent path.
No files, Qt, providers, models or personal state are accessed here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

UNKNOWN_ID = "unknown"
FILTER_GROUPS = ("source", "book", "chapter", "section", "knowledge", "grade", "exam")
RULE_ZH = "同一类勾选多个标签时满足任意一个即可；不同类须同时满足。教材册、章、节须对应同一条有效归属。"
_STATUSES = {
    "auto_suggested",
    "source_observed",
    "usage_positioning",
    "teacher_confirmed",
}
_GRADE_LABELS = {"grade_10": "高一", "grade_11": "高二", "grade_12": "高三"}
_EXAM_LABELS = {
    "first_mock": "一模",
    "second_mock": "二模",
    "grade_exam": "等级考",
    "school_exam": "校考",
    "midterm": "期中",
    "final": "期末",
    "monthly": "月考",
    "gaokao": "高考（不预设地区）",
}
_UNKNOWN_LABELS = {
    "source": "来源待核对",
    "book": "教材未标注 / 目录关联待核对",
    "chapter": "教材章未标注 / 目录关联待核对",
    "section": "教材节未标注 / 目录关联待核对",
    "knowledge": "知识点未标注 / 旧标签待核对",
    "grade": "适用年级待确认",
    "exam": "原考试类型待确认",
}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def chapter_filter_id(volume_id, chapter_id):
    """Opaque, collision-free chapter selection ID; labels are never identity."""
    return "chapter:" + json.dumps(
        [volume_id, chapter_id], ensure_ascii=False, separators=(",", ":")
    )


def section_filter_id(volume_id, chapter_id, section_key):
    """Opaque section selection ID, pinned to both parent IDs."""
    return "section:" + json.dumps(
        [volume_id, chapter_id, section_key], ensure_ascii=False, separators=(",", ":")
    )


def _directory(catalog):
    raw = catalog.get("nodes") if isinstance(catalog, Mapping) else None
    if not isinstance(raw, list) or not raw:
        return {}
    nodes = {}
    for row in raw:
        if not isinstance(row, Mapping):
            return {}
        key = _text(row.get("node_key") or row.get("section_key"))
        volume, chapter = _text(row.get("volume_id")), _text(row.get("chapter_id"))
        if not all((key, volume, chapter)) or UNKNOWN_ID in (key, volume, chapter):
            return {}
        node = {**row, "node_key": key, "volume_id": volume, "chapter_id": chapter}
        previous = nodes.get(key)
        if previous and (previous["volume_id"], previous["chapter_id"]) != (
            volume,
            chapter,
        ):
            return {}
        nodes[key] = node
    return nodes


def _bound_attributes(row):
    attributes = row.get("attributes")
    if not isinstance(attributes, Mapping):
        return {}
    if (
        not _text(row.get("key"))
        or attributes.get("key") != row.get("key")
        or not re.fullmatch(r"[0-9a-f]{64}", _text(row.get("source_sha256")))
        or attributes.get("source_sha256") != row.get("source_sha256")
        or not _text(row.get("revision"))
        or attributes.get("question_revision") != row.get("revision")
    ):
        return {}
    return attributes


def _knowledge(attributes):
    supporting = attributes.get("supporting_knowledge")
    points = [attributes.get("primary_knowledge")] + (
        supporting if isinstance(supporting, list) else []
    )
    return {
        point["id"]: _text(point.get("label")) or point["id"]
        for point in points
        if isinstance(point, Mapping)
        and re.fullmatch(r"K(?:0[1-9]|1[0-9])", _text(point.get("id")))
        and _text(point.get("status")) in _STATUSES
    }


def _grades(attributes):
    grades = attributes.get("applicable_grades")
    if not isinstance(grades, Mapping) or _text(grades.get("status")) not in _STATUSES:
        return set()
    values = grades.get("values")
    return (
        {value for value in values if isinstance(value, str) and value in _GRADE_LABELS}
        if isinstance(values, list)
        else set()
    )


def _exam(attributes):
    original = attributes.get("original_source")
    exam = original.get("exam_type") if isinstance(original, Mapping) else None
    if isinstance(exam, Mapping) and _text(exam.get("status")) in _STATUSES:
        value = exam.get("value")
        if isinstance(value, str) and value in _EXAM_LABELS:
            return value
    return UNKNOWN_ID


def _mappings(attributes, nodes):
    if _text(attributes.get("curriculum_status")) not in {
        "auto_suggested",
        "teacher_confirmed",
    }:
        return []
    candidates = attributes.get("curriculum_candidates")
    result = {}
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, Mapping) or _text(candidate.get("status")) not in {
            "auto_suggested",
            "teacher_confirmed",
        }:
            continue
        node = nodes.get(_text(candidate.get("section_key")))
        if node and all(
            candidate.get(field) == node[field] for field in ("volume_id", "chapter_id")
        ):
            result[node["node_key"]] = node
    return list(result.values())


def _book_label(node):
    return (
        " · ".join(
            dict.fromkeys(
                part
                for part in (
                    _text(node.get("edition_title")),
                    _text(node.get("volume_title")),
                )
                if part
            )
        )
        or node["volume_id"]
    )


def _chapter_label(node):
    return _text(node.get("chapter_title")) or node["chapter_id"]


def _section_label(node):
    return (
        " ".join(
            part
            for part in (
                _text(node.get("section_number")),
                _text(node.get("section_title")),
            )
            if part
        )
        or node["node_key"]
    )


def compile_filter_options(items, attribute_catalog):
    """Return all directory choices and observed valid non-curriculum labels.

    Every group also includes unknown. Unknown means no usable label in that
    group, not an additional invented label on otherwise labelled questions.
    Chapter/section options expose parent IDs for UI-only option narrowing.
    """
    nodes = _directory(attribute_catalog)
    options = {group: {} for group in FILTER_GROUPS}
    book_labels = {node["volume_id"]: _book_label(node) for node in nodes.values()}
    for volume, label in list(book_labels.items()):
        if sum(value == label for value in book_labels.values()) > 1:
            # Stable IDs distinguish options, never infer edition/source facts.
            for key, value in list(book_labels.items()):
                if value == label:
                    book_labels[key] = f"{value}（ID：{key}）"
    for node in nodes.values():
        volume, chapter, key = node["volume_id"], node["chapter_id"], node["node_key"]
        options["book"][volume] = {
            "id": volume,
            "label": book_labels[volume],
            "volume_id": volume,
        }
        chapter_id = chapter_filter_id(volume, chapter)
        options["chapter"][chapter_id] = {
            "id": chapter_id,
            "label": f"{book_labels[volume]} / {_chapter_label(node)}",
            "volume_id": volume,
            "chapter_id": chapter,
        }
        section_id = section_filter_id(volume, chapter, key)
        options["section"][section_id] = {
            "id": section_id,
            "label": f"{book_labels[volume]} / {_chapter_label(node)} / {_section_label(node)}",
            "volume_id": volume,
            "chapter_id": chapter,
            "section_key": key,
        }
    iterable = items.values() if isinstance(items, Mapping) else items or ()
    for row in iterable:
        if not isinstance(row, Mapping):
            continue
        source = _text(row.get("source_id")) or UNKNOWN_ID
        if source != UNKNOWN_ID:
            options["source"][source] = {
                "id": source,
                "label": _text(row.get("source_name")) or source,
            }
        attributes = _bound_attributes(row)
        for key, label in _knowledge(attributes).items():
            options["knowledge"][key] = {"id": key, "label": f"{key} · {label}"}
        for key in _grades(attributes):
            options["grade"][key] = {"id": key, "label": "适用 " + _GRADE_LABELS[key]}
        exam = _exam(attributes)
        if exam != UNKNOWN_ID:
            options["exam"][exam] = {"id": exam, "label": _EXAM_LABELS[exam]}
    return {
        "groups": {
            group: sorted(
                values.values(), key=lambda value: (value["label"], value["id"])
            )
            + [{"id": UNKNOWN_ID, "label": _UNKNOWN_LABELS[group]}]
            for group, values in options.items()
        },
        "catalog_valid": bool(nodes),
        "warnings": []
        if nodes
        else [
            "教材目录为空或无法核对，具体教材筛选不可用；全部原题仍可浏览，也可在教材未标注项中查找。"
        ],
        "rule_zh": RULE_ZH,
    }


def _chosen(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    if not isinstance(value, (list, tuple, set, frozenset)) or any(
        not isinstance(part, str) or not part for part in value
    ):
        return None
    return set(value)


def _search_text(row, attributes, mappings):
    # Deliberately never inspect answer_blocks, answer text or evidence quotes.
    parts = [
        _text(row.get(name))
        for name in ("source_name", "source_label", "chapter", "title", "title_zh")
    ]
    for group in ("question_blocks", "context_blocks"):
        blocks = row.get(group)
        if isinstance(blocks, list):
            parts.extend(
                _text(block.get("text"))
                for block in blocks
                if isinstance(block, Mapping)
            )
    parts.extend(_knowledge(attributes).values())
    parts.extend(_GRADE_LABELS[key] for key in _grades(attributes))
    parts.append(_EXAM_LABELS.get(_exam(attributes), "原考试类型待确认"))
    source = attributes.get("source")
    if isinstance(source, Mapping):
        parts.extend(_text(value) for value in source.values())
    original = attributes.get("original_source")
    if isinstance(original, Mapping):
        parts.append(_text(original.get("display_label")))
        for name in ("year", "region", "grade"):
            fact = original.get(name)
            if isinstance(fact, Mapping) and _text(fact.get("status")) in _STATUSES:
                parts.append(_text(fact.get("value")))
    for node in mappings:
        parts.extend((_book_label(node), _chapter_label(node), _section_label(node)))
    return "\n".join(parts).casefold()


def matches_question(row, selection, catalog):
    """Match checked stable IDs and optional query without mutating any input.

    selection accepts sets/lists/tuples of IDs per FILTER_GROUPS; query is an
    optional case-insensitive substring. knowledge_mode defaults to 'any';
    'all' requires every selected ID in the primary/supporting knowledge union.
    Unknown/malformed IDs never widen a
    selected group. An empty selection keeps all rows, even without a catalog.
    """
    if not isinstance(row, Mapping) or not isinstance(selection, Mapping):
        return False
    chosen = {group: _chosen(selection.get(group)) for group in FILTER_GROUPS}
    if any(value is None for value in chosen.values()):
        return False
    knowledge_mode = selection.get("knowledge_mode", "any")
    if knowledge_mode not in ("any", "all"):
        return False
    attributes = _bound_attributes(row)
    values = {
        "source": {_text(row.get("source_id")) or UNKNOWN_ID},
        "knowledge": set(_knowledge(attributes)) or {UNKNOWN_ID},
        "grade": _grades(attributes) or {UNKNOWN_ID},
        "exam": {_exam(attributes)},
    }
    if any(
        chosen[group] and not chosen[group].intersection(value)
        for group, value in values.items()
    ):
        return False
    if knowledge_mode == "all" and not chosen["knowledge"].issubset(
        values["knowledge"]
    ):
        return False
    mappings = _mappings(attributes, _directory(catalog))
    paths = [
        {
            "book": node["volume_id"],
            "chapter": chapter_filter_id(node["volume_id"], node["chapter_id"]),
            "section": section_filter_id(
                node["volume_id"], node["chapter_id"], node["node_key"]
            ),
        }
        for node in mappings
    ] or [{group: UNKNOWN_ID for group in ("book", "chapter", "section")}]
    if not any(
        all(
            not chosen[group] or path[group] in chosen[group]
            for group in ("book", "chapter", "section")
        )
        for path in paths
    ):
        return False
    query = selection.get("query", "")
    return isinstance(query, str) and (
        not query.strip()
        or query.strip().casefold() in _search_text(row, attributes, mappings)
    )
