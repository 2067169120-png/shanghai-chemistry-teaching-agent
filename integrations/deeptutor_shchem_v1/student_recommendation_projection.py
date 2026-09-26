from __future__ import annotations

"""Read-only projection of current visual-review evidence into recommendations.

The visual manager remains responsible for reading and verifying its journals.
This helper performs the cross-journal join without file access or mutations.
In particular, a diagnosis bound to an earlier score cannot confirm a weakness
after the teacher changes that score, even when the numeric score is unchanged.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .student_recommendation_workbench import StudentRecommendationWorkbenchError


def _corrupt(kind: str, message: str) -> StudentRecommendationWorkbenchError:
    return StudentRecommendationWorkbenchError(
        f"{kind}_decision_store_corrupt", message, 503
    )


def _history(value: Any, kind: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise _corrupt(kind, "teacher decision history is invalid")
    records: list[Mapping[str, Any]] = []
    for sequence, item in enumerate(value, 1):
        if (
            not isinstance(item, Mapping)
            or type(item.get("sequence")) is not int
            or item.get("sequence") != sequence
        ):
            raise _corrupt(kind, "teacher decision history sequence is invalid")
        records.append(item)
    return records


def _valid_score(item: Mapping[str, Any]) -> bool:
    score, maximum = item.get("teacher_score"), item.get("maximum_score")
    return (
        type(score) in {int, float}
        and type(maximum) in {int, float}
        and math.isfinite(score)
        and math.isfinite(maximum)
        and 0 <= score <= maximum
        and maximum > 0
    )


def project_visual_recommendation_payload(
    *,
    submission_id: str,
    review: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
    data_snapshot_id: str,
    scopes: Sequence[str] = ("master", "wave1", "supplemental"),
    limit_per_section: int = 3,
) -> dict[str, Any]:
    """Return the existing recommendation payload, using only current decisions.

    Missing diagnoses are not invented. A latest diagnosis attached to an old
    score is represented as pending, with the latest teacher score but no
    curriculum evidence. Historical broken or cross-match bindings fail closed
    before any older records are discarded. Inputs and journals are untouched.
    """
    if not isinstance(review, Mapping) or not isinstance(diagnostic, Mapping):
        raise _corrupt("diagnostic", "visual review projection is invalid")
    for value, kind in ((review, "scoring"), (diagnostic, "diagnostic")):
        if value.get("submission_id", submission_id) != submission_id:
            raise _corrupt(kind, "teacher review belongs to another submission")
    analysis = review.get("analysis")
    candidate = analysis.get("candidate") if isinstance(analysis, Mapping) else None
    raw_matches = candidate.get("matches") if isinstance(candidate, Mapping) else None
    if not isinstance(raw_matches, list) or not raw_matches:
        raise StudentRecommendationWorkbenchError(
            "analysis_not_ready",
            "student recommendation preview requires a visual candidate",
            409,
        )
    matches: list[dict[str, Any]] = []
    matches_by_id: dict[str, Mapping[str, Any]] = {}
    for item in raw_matches:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("match_id"), str)
            or not isinstance(item.get("atomic_part_id"), str)
            or item["match_id"] in matches_by_id
        ):
            raise _corrupt("scoring", "visual candidate match binding is invalid")
        matches_by_id[item["match_id"]] = item
        matches.append(
            {
                "match_id": item["match_id"],
                "atomic_part_id": item["atomic_part_id"],
                "theme_id": None,
                "paper_id": None,
            }
        )

    analysis_id = analysis.get("analysis_id")
    scoring_by_id: dict[str, Mapping[str, Any]] = {}
    latest_scoring: dict[str, Mapping[str, Any]] = {}
    for item in _history(review.get("scoring_decisions", []), "scoring"):
        decision_id = item.get("decision_id")
        match_id = item.get("match_id")
        match = matches_by_id.get(match_id) if isinstance(match_id, str) else None
        if (
            not isinstance(decision_id, str)
            or not decision_id
            or decision_id in scoring_by_id
            or match is None
            or item.get("atomic_part_id") != match.get("atomic_part_id")
            or (analysis_id is not None and item.get("analysis_id") != analysis_id)
            or item.get("submission_id", submission_id) != submission_id
            or not _valid_score(item)
        ):
            raise _corrupt("scoring", "teacher scoring decision binding is invalid")
        scoring_by_id[decision_id] = item
        latest_scoring[match_id] = item

    latest_diagnostic: dict[str, Mapping[str, Any]] = {}
    for item in _history(diagnostic.get("diagnostic_decisions", []), "diagnostic"):
        scoring_id, match_id = item.get("scoring_decision_id"), item.get("match_id")
        scoring = scoring_by_id.get(scoring_id) if isinstance(scoring_id, str) else None
        if (
            scoring is None
            or not isinstance(match_id, str)
            or scoring.get("match_id") != match_id
            or scoring.get("atomic_part_id") != item.get("atomic_part_id")
            or (analysis_id is not None and item.get("analysis_id") != analysis_id)
            or item.get("submission_id", submission_id) != submission_id
            or item.get("decision") not in {"accept", "edit", "reject", "pending"}
            or not isinstance(item.get("curriculum_sections"), list)
            or any(
                not isinstance(section, Mapping)
                for section in item["curriculum_sections"]
            )
            or (
                "scoring_decision_record_sha256" in item
                and item["scoring_decision_record_sha256"]
                != scoring.get("record_sha256")
            )
        ):
            raise _corrupt(
                "diagnostic", "teacher diagnostic decision binding is invalid"
            )
        latest_diagnostic[match_id] = item

    projected_decisions: list[dict[str, Any]] = []
    for item in sorted(latest_diagnostic.values(), key=lambda row: row["sequence"]):
        scoring = latest_scoring[item["match_id"]]
        current = item["scoring_decision_id"] == scoring["decision_id"]
        projected_sections = []
        if current and item["decision"] in {"accept", "edit"}:
            for section in item["curriculum_sections"]:
                projected_sections.append(
                    {
                        "section_key": section.get("section_key"),
                        "volume_id": section.get("volume_id"),
                        "volume_title": section.get(
                            "volume_title", section.get("volume_title_zh")
                        ),
                        "chapter_id": section.get("chapter_id"),
                        "chapter_title": section.get(
                            "chapter_title", section.get("chapter_title_zh")
                        ),
                        "section_number": section.get("section_number"),
                        "section_title": section.get("section_title"),
                        "display_label_zh": section.get("display_label_zh"),
                    }
                )
        projected_decisions.append(
            {
                "sequence": item["sequence"],
                "match_id": item["match_id"],
                "atomic_part_id": item["atomic_part_id"],
                "teacher_score": scoring["teacher_score"],
                "maximum_score": scoring["maximum_score"],
                "decision": item["decision"] if current else "pending",
                "curriculum_sections": projected_sections,
            }
        )
    return {
        "submission_id": submission_id,
        "data_snapshot_id": data_snapshot_id,
        "matches": matches,
        "scoring_decisions": projected_decisions,
        "exclusions": {
            "atomic_part_ids": sorted({item["atomic_part_id"] for item in matches}),
            "theme_ids": [],
            "paper_ids": [],
        },
        "scopes": list(scopes),
        "limit_per_section": limit_per_section,
    }
