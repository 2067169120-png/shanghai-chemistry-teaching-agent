"""Pure structured-evidence preflight for generation task cards."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import (
    validate_evidence_summaries,
    validate_provider_status,
    validate_task_card,
)
from .provider import resolve_provider_request

_GATES = (
    ("l1_direction_source_minimum", 1),
    ("independent_current_shanghai_l2_minimum", 3),
    ("formal_reviewed_question_required", 1),
    ("human_chemistry_review_required", 1),
    ("independent_solution_pass_required", 1),
    ("independent_machine_review_pass_required", 1),
    ("originality_check_pass_required", 1),
    ("provider_available_required", 1),
)

_DIRECTION_L1_LEVELS = frozenset(
    {
        "L1_OFFICIAL_COURSE_STANDARD",
        "L1_OFFICIAL_COMMENTARY",
    }
)


def evidence_preflight(
    task_card: Mapping[str, Any],
    evidence_summaries: Any,
    provider_status: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate generation readiness without opening paths or dereferencing IDs."""

    card = validate_task_card(task_card)
    summaries = validate_evidence_summaries(evidence_summaries)
    status = validate_provider_status(provider_status)
    declared = set(card["evidence_record_ids"])
    supplied = {row["record_id"] for row in summaries}

    l1_count = sum(row["evidence_level"] in _DIRECTION_L1_LEVELS for row in summaries)
    l2_groups = {
        row["independence_group_id"]
        for row in summaries
        if row["evidence_level"].startswith("L2_") and row["current_style_eligible"]
    }
    formal_rows = [
        row
        for row in summaries
        if row["evidence_level"].startswith("L2_") and row["formal_question_reviewed"]
    ]
    observed = {
        "l1_direction_source_minimum": l1_count,
        "independent_current_shanghai_l2_minimum": len(l2_groups),
        "formal_reviewed_question_required": len(formal_rows),
        "human_chemistry_review_required": sum(
            row["human_chemistry_reviewed"] for row in formal_rows
        ),
        "independent_solution_pass_required": sum(
            row["independent_solution_status"] == "passed" for row in formal_rows
        ),
        "independent_machine_review_pass_required": sum(
            row["independent_machine_review_status"] == "passed" for row in formal_rows
        ),
        "originality_check_pass_required": sum(
            row["originality_check_status"] == "passed" for row in formal_rows
        ),
        "provider_available_required": int(status["availability"] == "available"),
    }

    checks: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for code, required in _GATES:
        count = observed[code]
        passed = count >= required
        checks.append(
            {
                "code": code,
                "required": required,
                "observed": count,
                "passed": passed,
            }
        )
        if not passed:
            blockers.append(
                {
                    "code": code,
                    "message": f"required={required}, observed={count}",
                }
            )

    missing_summaries = sorted(declared - supplied)
    unexpected_summaries = sorted(supplied - declared)
    for code, values in (
        ("declared_evidence_summary_missing", missing_summaries),
        ("undeclared_evidence_summary_rejected", unexpected_summaries),
    ):
        passed = not values
        checks.append(
            {
                "code": code,
                "required": len(values) if not passed else 0,
                "observed": 0 if not passed else len(supplied),
                "passed": passed,
            }
        )
        if values:
            blockers.append({"code": code, "message": ",".join(values)})

    ready = not blockers
    if card["mode"] == "plan_only":
        effective_mode = "plan_only"
        result_status = "plan_only"
    elif ready:
        effective_mode = "machine_candidate"
        result_status = "ready"
    else:
        effective_mode = "plan_only_blocked"
        result_status = "blocked"

    return {
        "schema_version": "generation_evidence_preflight_v1",
        "requested_mode": card["mode"],
        "effective_mode": effective_mode,
        "status": result_status,
        "ready_for_machine_candidate": ready,
        "checks": checks,
        "blockers": blockers,
        "evidence_record_ids": sorted(supplied),
        "provider_status_record_id": status["status_record_id"],
        "provider_configuration": resolve_provider_request(
            {"provider_profile_id": card["provider_profile_id"]}
        ),
        "human_reviewed": False,
        "publication_allowed": False,
        "official": False,
    }
