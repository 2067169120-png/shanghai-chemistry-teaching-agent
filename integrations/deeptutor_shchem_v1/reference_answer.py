from __future__ import annotations

import re
from typing import Any


ALIGNED = "present_part_aligned"
UNALIGNED = "present_unaligned"
ABSENT = "absent"
NONOFFICIAL = "nonofficial_reference"
NONE = "none"

_STATUS_NOTES = {
    UNALIGNED: "来源含答案材料但未对齐到该最小作答单元；不展示答案正文。",
    ABSENT: "来源未附可对齐答案；不展示答案正文。",
}
_QUALITY_FIELDS = (
    ("ambiguity_or_multiple_solutions_zh", "歧义或多解"),
)
_FORBIDDEN_TEXT = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/))"
)


class ReferenceAnswerProjectionError(ValueError):
    pass


def _explicit_quality_note(risks_and_limits: Any) -> str | None:
    if not isinstance(risks_and_limits, dict):
        return None
    labels: list[str] = []
    for field, label in _QUALITY_FIELDS:
        values = risks_and_limits.get(field)
        if isinstance(values, list) and any(
            isinstance(item, str) and bool(item.strip()) for item in values
        ):
            labels.append(label)
    if not labels:
        return None
    return f"扫描记录含显式{'、'.join(labels)}提示；该提示不阻断来源答案原文展示。"


def project_reference_answer(
    answer: Any,
    risks_and_limits: Any = None,
    known_quality_note_code: Any = None,
) -> dict[str, Any]:
    """Project only the frozen source answer text and its authority boundary.

    The aligned text is copied byte-for-byte at the Python string level from
    ``reference_summary_zh``.  No extraction, normalization, or correctness
    inference is performed.  Unaligned and absent records always suppress it.
    """

    if not isinstance(answer, dict):
        raise ReferenceAnswerProjectionError("answer record must be an object")
    availability = answer.get("availability")
    authority = answer.get("authority")
    if availability == ALIGNED:
        text = answer.get("reference_summary_zh")
        if authority != NONOFFICIAL or not isinstance(text, str) or not text:
            raise ReferenceAnswerProjectionError(
                "aligned answer lacks exact nonofficial source text"
            )
        if _FORBIDDEN_TEXT.search(text):
            raise ReferenceAnswerProjectionError(
                "aligned answer text contains a forbidden path or URL"
            )
        reference_answer_text: str | None = text
        source_authority = NONOFFICIAL
    elif availability == UNALIGNED:
        if authority != NONOFFICIAL:
            raise ReferenceAnswerProjectionError(
                "unaligned answer authority must remain nonofficial"
            )
        reference_answer_text = None
        source_authority = NONOFFICIAL
    elif availability == ABSENT:
        if authority != NONE:
            raise ReferenceAnswerProjectionError(
                "absent answer authority must remain none"
            )
        reference_answer_text = None
        source_authority = NONE
    else:
        raise ReferenceAnswerProjectionError("answer availability is unsupported")
    explicit_quality_note = _explicit_quality_note(risks_and_limits)
    if isinstance(known_quality_note_code, str) and known_quality_note_code:
        explicit_quality_note = (
            f"扫描记录含显式非阻断质量备注（{known_quality_note_code}）；"
            "该备注不阻断来源答案原文展示。"
        )
    elif known_quality_note_code is not None:
        raise ReferenceAnswerProjectionError(
            "known quality-note code must be a non-empty string or null"
        )
    quality_note = explicit_quality_note or _STATUS_NOTES.get(availability)
    return {
        "availability": availability,
        "reference_answer_text": reference_answer_text,
        "source_authority": source_authority,
        "independently_verified": False,
        "quality_note": quality_note,
    }


def reference_answer_catalog_metadata(
    answer: Any,
    risks_and_limits: Any = None,
    known_quality_note_code: Any = None,
) -> dict[str, Any]:
    projection = project_reference_answer(
        answer, risks_and_limits, known_quality_note_code
    )
    return {
        "availability": projection["availability"],
        "source_authority": projection["source_authority"],
        "has_quality_note": (
            isinstance(known_quality_note_code, str)
            and bool(known_quality_note_code)
        )
        or _explicit_quality_note(risks_and_limits) is not None,
    }


__all__ = [
    "ABSENT",
    "ALIGNED",
    "NONE",
    "NONOFFICIAL",
    "UNALIGNED",
    "ReferenceAnswerProjectionError",
    "project_reference_answer",
    "reference_answer_catalog_metadata",
]
