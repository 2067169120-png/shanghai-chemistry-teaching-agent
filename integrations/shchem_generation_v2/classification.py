"""Controlled atomic-part classification vocabulary for generation v2.

The values here are publication-candidate metadata, not official Shanghai
taxonomy claims.  Keeping the vocabulary explicit prevents a producer or a
mutated paper from satisfying the schema with arbitrary free-form labels.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable


CLASSIFICATION_VOCABULARY_VERSION = "1.0.0-r18"

ITEM_TYPE_VOCABULARY = (
    "chemical_equation_or_notation",
    "comparison_or_open_response",
    "embedded_single_choice",
    "experiment_operation_apparatus_plan",
    "quantitative_calculation",
    "reasoned_explanation",
    "short_fill",
)

RESPONSE_R_VOCABULARY = (
    "calculation",
    "chemical_equation",
    "fill_blank",
    "short_explanation",
    "single_choice",
)

REPRESENTATION_RP_VOCABULARY = (
    "apparatus",
    "chemical_equation",
    "chemical_symbols",
    "energy_model",
    "equation",
    "experiment_process",
    "organic_structure",
    "particle_model",
    "process_flow",
    "quantitative_data",
    "reaction_route",
    "text",
)

CONTEXT_C_VOCABULARY = ("current_science_and_sustainability",)

ITEM_TYPE_RESPONSE_R_COMPATIBILITY: dict[str, tuple[str, ...]] = {
    "chemical_equation_or_notation": (
        "chemical_equation",
        "short_explanation",
    ),
    "comparison_or_open_response": ("short_explanation",),
    "embedded_single_choice": ("single_choice",),
    "experiment_operation_apparatus_plan": ("short_explanation",),
    "quantitative_calculation": ("calculation",),
    "reasoned_explanation": ("short_explanation",),
    "short_fill": ("fill_blank",),
}


def _controlled_array_errors(
    value: object,
    *,
    field: str,
    vocabulary: Iterable[str],
) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{field}:nonempty_array_required"]
    if any(not isinstance(item, str) for item in value):
        return [f"{field}:string_items_required"]
    if len(value) != len(set(value)):
        return [f"{field}:duplicate_value"]
    allowed = set(vocabulary)
    unknown = sorted(item for item in value if item not in allowed)
    return [f"{field}:unknown_value:{item}" for item in unknown]


def validate_atomic_classification(part: dict[str, Any]) -> dict[str, Any]:
    """Recompute one part's four controlled axes and cross-axis consistency."""

    part_id = str(part.get("part_id") or "unknown")
    errors: list[str] = []
    item_type = part.get("item_type")
    response = part.get("response_R")
    if item_type not in ITEM_TYPE_VOCABULARY:
        errors.append(f"item_type:unknown_value:{item_type}")
    if response not in RESPONSE_R_VOCABULARY:
        errors.append(f"response_R:unknown_value:{response}")
    if (
        item_type in ITEM_TYPE_RESPONSE_R_COMPATIBILITY
        and response in RESPONSE_R_VOCABULARY
        and response not in ITEM_TYPE_RESPONSE_R_COMPATIBILITY[item_type]
    ):
        errors.append(f"item_type_response_R:incompatible:{item_type}:{response}")
    errors.extend(
        _controlled_array_errors(
            part.get("representation_RP"),
            field="representation_RP",
            vocabulary=REPRESENTATION_RP_VOCABULARY,
        )
    )
    errors.extend(
        _controlled_array_errors(
            part.get("context_C"),
            field="context_C",
            vocabulary=CONTEXT_C_VOCABULARY,
        )
    )
    expected_selection = (
        "single" if item_type == "embedded_single_choice" else "not_applicable"
    )
    if item_type in ITEM_TYPE_VOCABULARY and part.get("selection_rule") != expected_selection:
        errors.append(
            "item_type_selection_rule:incompatible:"
            f"{item_type}:{part.get('selection_rule')}"
        )
    return {
        "atomic_part_id": part_id,
        "status": "pass" if not errors else "fail",
        "vocabulary_version": CLASSIFICATION_VOCABULARY_VERSION,
        "item_type": item_type,
        "response_R": response,
        "representation_RP": deepcopy(part.get("representation_RP")),
        "context_C": deepcopy(part.get("context_C")),
        "errors": errors,
    }


def classification_vocabulary_mutation_report(
    paper: dict[str, Any],
) -> dict[str, Any]:
    """Enumerate all atomic parts and prove the four axes fail closed."""

    parts = [
        part
        for theme in paper.get("themes", [])
        for printed in theme.get("printed_questions", [])
        for part in printed.get("atomic_parts", [])
    ]
    baseline = [validate_atomic_classification(part) for part in parts]
    ids = [row["atomic_part_id"] for row in baseline]
    errors: list[str] = []
    if len(ids) != len(set(ids)):
        errors.append("duplicate_atomic_part_id")
    errors.extend(
        f"baseline:{row['atomic_part_id']}:{error}"
        for row in baseline
        for error in row["errors"]
    )

    if not parts:
        errors.append("atomic_parts_missing")
        cases: list[dict[str, Any]] = []
    else:
        first = parts[0]
        mutations = (
            ("unknown_item_type", "item_type", "fabricated_free_form_type"),
            ("unknown_response_R", "response_R", "fabricated_free_form_response"),
            (
                "unknown_representation_RP",
                "representation_RP",
                ["fabricated_free_form_representation"],
            ),
            (
                "unknown_context_C",
                "context_C",
                ["fabricated_free_form_context"],
            ),
            ("valid_but_incompatible_response_R", "response_R", "calculation"),
            (
                "duplicate_representation_RP",
                "representation_RP",
                [first["representation_RP"][0], first["representation_RP"][0]],
            ),
            (
                "item_type_selection_rule_mismatch",
                "selection_rule",
                "not_applicable",
            ),
        )
        cases = []
        for mutation_id, field, value in mutations:
            candidate = deepcopy(first)
            candidate[field] = value
            result = validate_atomic_classification(candidate)
            cases.append(
                {
                    "mutation_id": mutation_id,
                    "atomic_part_id": result["atomic_part_id"],
                    "expected_rejected": True,
                    "observed_rejected": result["status"] == "fail",
                    "errors": result["errors"],
                }
            )
        errors.extend(
            f"mutation_not_rejected:{row['mutation_id']}"
            for row in cases
            if not row["observed_rejected"]
        )

    return {
        "check": "atomic_classification_controlled_vocabulary_and_mutations",
        "status": "pass" if not errors else "fail",
        "vocabulary_version": CLASSIFICATION_VOCABULARY_VERSION,
        "vocabulary": {
            "item_type": list(ITEM_TYPE_VOCABULARY),
            "response_R": list(RESPONSE_R_VOCABULARY),
            "representation_RP": list(REPRESENTATION_RP_VOCABULARY),
            "context_C": list(CONTEXT_C_VOCABULARY),
        },
        "compatibility": {
            key: list(value)
            for key, value in sorted(ITEM_TYPE_RESPONSE_R_COMPATIBILITY.items())
        },
        "atomic_part_count": len(parts),
        "atomic_part_ids": ids,
        "item_type_histogram": dict(
            sorted(Counter(str(part.get("item_type")) for part in parts).items())
        ),
        "response_R_histogram": dict(
            sorted(Counter(str(part.get("response_R")) for part in parts).items())
        ),
        "items": baseline,
        "mutation_cases": cases,
        "errors": errors,
        "human_reviewed": False,
        "official_claim_allowed": False,
    }
