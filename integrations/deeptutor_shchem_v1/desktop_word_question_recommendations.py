"""Transparent, read-only lesson-title matches against bound Word labels.

This is lexical navigation, not model inference or a change to any question.
It deliberately ignores stems, answers, source filenames and long materials.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

_LABEL_SEPARATORS = re.compile(r"[、,，/／;；]|与|和|及")
_KNOWLEDGE_ID = re.compile(r"K(?:0[1-9]|1[0-9])\Z")


def _normalized(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _matching_terms(topic: str, label: str) -> list[str]:
    """Use whole names or whole explicit name components, never fuzzy tokens."""

    def matches(name: str) -> bool:
        normalized = _normalized(name)
        if normalized == topic:
            return True
        if len(normalized) < 2:
            return False
        # A Latin label such as pH must not match the middle of another word.
        if normalized.isascii() and normalized.isalnum():
            return bool(
                re.search(
                    r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])", topic
                )
            )
        return normalized in topic

    names = [label, *_LABEL_SEPARATORS.split(label)]
    matched = {name.strip() for name in names if name.strip() and matches(name)}
    return sorted(matched, key=lambda name: (-len(_normalized(name)), name))


def lesson_knowledge_suggestions(
    lesson_topic: str, items: Iterable[Mapping]
) -> list[dict]:
    """Return deterministic visible filter suggestions without selecting items.

    Input is the current Word catalog, whose attributes are already source and
    revision bound. If explicit binding fields are present, stale bindings are
    rejected here too. Counts describe catalog question/theme units, not their
    embedded subparts, and do not assert that any unit is ready for teaching.
    """
    if not isinstance(lesson_topic, str) or not lesson_topic.strip():
        return []
    topic = _normalized(lesson_topic)
    groups = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        attributes = item.get("attributes")
        if not isinstance(key, str) or not key or not isinstance(attributes, Mapping):
            continue
        if any(
            name in attributes and attributes[name] != item.get(item_name)
            for name, item_name in (
                ("question_revision", "revision"),
                ("source_sha256", "source_sha256"),
            )
        ):
            continue
        primary = attributes.get("primary_knowledge")
        supporting = attributes.get("supporting_knowledge")
        points = [("primary", primary)]
        if isinstance(supporting, list):
            points.extend(("supporting", point) for point in supporting)
        for role, point in points:
            if not isinstance(point, Mapping):
                continue
            knowledge_id, label = point.get("id"), point.get("label")
            if (
                not isinstance(knowledge_id, str)
                or not _KNOWLEDGE_ID.fullmatch(knowledge_id)
                or not isinstance(label, str)
                or not label.strip()
            ):
                continue
            group = groups.setdefault(
                knowledge_id,
                {
                    "labels": set(),
                    "terms": set(),
                    "primary": set(),
                    "supporting": set(),
                    "statuses": {},
                    "ready": set(),
                },
            )
            group["labels"].add(label)
            group["terms"].update(_matching_terms(topic, label))
            group[role].add(key)
            if item.get("selection_ready") is True:
                group["ready"].add(key)
            status = point.get("status")
            status = (
                status
                if status in {"auto_suggested", "teacher_confirmed"}
                else "unknown"
            )
            group["statuses"].setdefault(status, set()).add(key)
    result = []
    for knowledge_id, group in groups.items():
        if not group["terms"]:
            continue
        terms = sorted(group["terms"], key=lambda name: (-len(_normalized(name)), name))
        result.append(
            {
                "knowledge_id": knowledge_id,
                "label": min(
                    group["labels"],
                    key=lambda label: (not bool(_matching_terms(topic, label)), label),
                ),
                "matched_terms": terms,
                "primary_count": len(group["primary"]),
                "supporting_count": len(group["supporting"]),
                "question_count": len(group["primary"] | group["supporting"]),
                "selectable_count": len(group["ready"]),
                "status_counts": {
                    name: len(keys) for name, keys in sorted(group["statuses"].items())
                },
                "exact_label_match": any(
                    _normalized(label) == topic for label in group["labels"]
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            not row["exact_label_match"],
            -len(_normalized(row["matched_terms"][0])),
            -row["primary_count"],
            -row["question_count"],
            row["knowledge_id"],
        ),
    )


__all__ = ["lesson_knowledge_suggestions"]
