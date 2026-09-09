"""Strict, dependency-free contracts for task cards and structured inputs."""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Mapping

from .errors import ContractError

TASK_CARD_SCHEMA_VERSION = "generation_task_card_v1"
PROVIDER_PROFILE_ID = "sol_xhigh_generation_v1"

TARGET_KINDS = frozenset({"exercise_set", "full_paper", "weekpack"})
MODES = frozenset({"plan_only", "machine_candidate"})
VALUE_STATUSES = frozenset({"known", "unknown", "blocked_pending_review"})
HIERARCHY = ("paper", "theme_big_question", "printed_question", "atomic_part")
FIGURE_TYPES = frozenset(
    {
        "apparatus",
        "electrochemical_cell",
        "graph",
        "organic_structure",
        "particle_diagram",
        "process_flow",
        "table",
        "other_review_required",
    }
)
EVIDENCE_LEVELS = frozenset(
    {
        "L1_OFFICIAL_COURSE_STANDARD",
        "L1_OFFICIAL_COMMENTARY",
        "L1_OFFICIAL_EXAM_SCHEDULE",
        "L1_LOCAL_TEXTBOOK",
        "L2_PAGE_VERIFIED_SHANGHAI_EXAM",
        "L2_RECALLED_LEVEL_EXAM_NONOFFICIAL",
        "L3_QUALIFIED_TEACHING_EXPERIENCE",
        "L3_USER_PROVIDED_TEACHING_MATERIAL_REVIEWED",
    }
)
CHECK_STATUSES = frozenset({"passed", "failed", "missing"})

# Opaque IDs deliberately exclude ':' so URI schemes and drive-relative paths
# cannot be smuggled through an identifier field. Slash, backslash, whitespace,
# controls, and NUL are outside this explicit alphabet as well.
OPAQUE_RECORD_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_ID_RE = re.compile(OPAQUE_RECORD_ID_PATTERN)
_TASK_CARD_KEYS = frozenset(
    {
        "schema_version",
        "target_kind",
        "grade",
        "teaching_stage",
        "purpose",
        "knowledge_scope",
        "ability_scope",
        "theme_count",
        "duration_minutes",
        "total_score",
        "difficulty_targets",
        "paper_structure",
        "numbering_rules",
        "selection_scoring_rules",
        "prohibited_content",
        "figure_types",
        "evidence_record_ids",
        "mode",
        "provider_profile_id",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "record_id",
        "evidence_level",
        "independence_group_id",
        "formal_question_reviewed",
        "human_chemistry_reviewed",
        "independent_solution_status",
        "independent_machine_review_status",
        "originality_check_status",
        "current_style_eligible",
    }
)
_PROVIDER_STATUS_KEYS = frozenset(
    {"provider_profile_id", "availability", "status_record_id"}
)


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json_object(raw: bytes, *, maximum_bytes: int = 1_000_000) -> dict[str, Any]:
    """Parse strict UTF-8 JSON while rejecting duplicates and non-finite values."""

    if type(raw) is not bytes:
        raise ContractError("raw input must be bytes")
    if not raw or len(raw) > maximum_bytes:
        raise ContractError("raw input byte length is outside the allowed range")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid strict UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        raise ContractError("top-level JSON value must be an object")
    return value


def require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    if type(value) is not dict:
        raise ContractError(f"{name} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ContractError(f"{name} exact-key violation; missing={missing}, extra={extra}")


def validate_identifier(value: Any, name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ContractError(
            f"{name} must be an opaque ID using only ASCII letters, digits, '_', '.', and '-'"
        )
    return value


def _validate_text(value: Any, name: str, *, maximum: int = 256) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ContractError(f"{name} must be a non-empty trimmed string")
    if len(value) > maximum or "\x00" in value:
        raise ContractError(f"{name} exceeds the text contract")
    return value


def _validate_string_list(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum_items: int = 128,
) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= maximum_items:
        raise ContractError(f"{name} must contain {minimum}..{maximum_items} strings")
    result = [_validate_text(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ContractError(f"{name} must not contain duplicates")
    return result


def _validate_status_integer(value: Any, name: str) -> dict[str, Any]:
    require_exact_keys(value, frozenset({"value_status", "value"}), name)
    status = value["value_status"]
    actual = value["value"]
    if status not in VALUE_STATUSES:
        raise ContractError(f"{name}.value_status is invalid")
    if status == "known":
        if type(actual) is not int or actual <= 0:
            raise ContractError(f"{name}.value must be a positive integer when known")
    elif actual is not None:
        raise ContractError(f"{name}.value must be null when status is {status}")
    return {"value_status": status, "value": actual}


def _validate_status_text(value: Any, name: str) -> dict[str, Any]:
    require_exact_keys(value, frozenset({"value_status", "value"}), name)
    status = value["value_status"]
    actual = value["value"]
    if status not in VALUE_STATUSES:
        raise ContractError(f"{name}.value_status is invalid")
    if status == "known":
        actual = _validate_text(actual, f"{name}.value", maximum=512)
    elif actual is not None:
        raise ContractError(f"{name}.value must be null when status is {status}")
    return {"value_status": status, "value": actual}


def _validate_difficulty(value: Any) -> dict[str, float]:
    keys = frozenset({"basic", "intermediate", "advanced"})
    require_exact_keys(value, keys, "difficulty_targets")
    result: dict[str, float] = {}
    for key in sorted(keys):
        item = value[key]
        if type(item) not in (int, float) or not math.isfinite(item):
            raise ContractError(f"difficulty_targets.{key} must be finite")
        if item < 0 or item > 1:
            raise ContractError(f"difficulty_targets.{key} must be between 0 and 1")
        result[key] = float(item)
    if not math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ContractError("difficulty_targets must sum to exactly 1 within tolerance")
    return result


def validate_task_card(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return an isolated generation task-card model."""

    require_exact_keys(value, _TASK_CARD_KEYS, "task_card")
    if value["schema_version"] != TASK_CARD_SCHEMA_VERSION:
        raise ContractError("task_card.schema_version is not supported")
    if value["target_kind"] not in TARGET_KINDS:
        raise ContractError("task_card.target_kind is invalid")
    if value["mode"] not in MODES:
        raise ContractError("task_card.mode is invalid")
    if value["provider_profile_id"] != PROVIDER_PROFILE_ID:
        raise ContractError("only the fixed provider profile is accepted")

    _validate_text(value["grade"], "grade", maximum=64)
    _validate_text(value["teaching_stage"], "teaching_stage", maximum=128)
    _validate_text(value["purpose"], "purpose", maximum=256)
    _validate_string_list(value["knowledge_scope"], "knowledge_scope", minimum=1)
    _validate_string_list(value["ability_scope"], "ability_scope", minimum=1)
    for name in ("theme_count", "duration_minutes", "total_score"):
        _validate_status_integer(value[name], name)
    _validate_difficulty(value["difficulty_targets"])

    structure_keys = frozenset({"hierarchy", "independent_selection_section"})
    structure = value["paper_structure"]
    require_exact_keys(structure, structure_keys, "paper_structure")
    hierarchy = structure["hierarchy"]
    if type(hierarchy) is not list or any(type(item) is not str for item in hierarchy):
        raise ContractError("paper_structure.hierarchy must be a string array")
    if type(structure["independent_selection_section"]) is not bool:
        raise ContractError("independent_selection_section must be boolean")
    if value["target_kind"] == "full_paper":
        if tuple(hierarchy) != HIERARCHY:
            raise ContractError("full_paper requires the four-level Shanghai hierarchy")
        if structure["independent_selection_section"]:
            raise ContractError("full_paper forbids an independent selection section")
    elif structure["independent_selection_section"]:
        raise ContractError("independent selection sections are not supported")

    _validate_status_text(value["numbering_rules"], "numbering_rules")
    _validate_status_text(value["selection_scoring_rules"], "selection_scoring_rules")
    _validate_string_list(value["prohibited_content"], "prohibited_content")

    figures = value["figure_types"]
    if type(figures) is not list or len(figures) > len(FIGURE_TYPES):
        raise ContractError("figure_types must be a bounded array")
    if any(type(item) is not str or item not in FIGURE_TYPES for item in figures):
        raise ContractError("figure_types contains an unsupported controlled value")
    if len(figures) != len(set(figures)):
        raise ContractError("figure_types must not contain duplicates")

    evidence_ids = value["evidence_record_ids"]
    if type(evidence_ids) is not list or len(evidence_ids) > 512:
        raise ContractError("evidence_record_ids must be a bounded ID array")
    for index, item in enumerate(evidence_ids):
        validate_identifier(item, f"evidence_record_ids[{index}]")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ContractError("evidence_record_ids must not contain duplicates")
    return copy.deepcopy(dict(value))


def validate_evidence_summaries(value: Any) -> list[dict[str, Any]]:
    """Accept only compact evidence summaries; no source paths, URLs, or prose."""

    if type(value) is not list or len(value) > 1024:
        raise ContractError("evidence_summaries must be a bounded array")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        name = f"evidence_summaries[{index}]"
        require_exact_keys(item, _EVIDENCE_KEYS, name)
        record_id = validate_identifier(item["record_id"], f"{name}.record_id")
        group_id = validate_identifier(
            item["independence_group_id"], f"{name}.independence_group_id"
        )
        if record_id in seen:
            raise ContractError("evidence_summaries contains duplicate record IDs")
        seen.add(record_id)
        if item["evidence_level"] not in EVIDENCE_LEVELS:
            raise ContractError(f"{name}.evidence_level is invalid")
        for flag in (
            "formal_question_reviewed",
            "human_chemistry_reviewed",
            "current_style_eligible",
        ):
            if type(item[flag]) is not bool:
                raise ContractError(f"{name}.{flag} must be boolean")
        for status_name in (
            "independent_solution_status",
            "independent_machine_review_status",
            "originality_check_status",
        ):
            if item[status_name] not in CHECK_STATUSES:
                raise ContractError(f"{name}.{status_name} is invalid")
        output.append(copy.deepcopy(item))
    return output


def validate_provider_status(value: Any) -> dict[str, Any]:
    require_exact_keys(value, _PROVIDER_STATUS_KEYS, "provider_status")
    if value["provider_profile_id"] != PROVIDER_PROFILE_ID:
        raise ContractError("provider_status profile does not match the fixed profile")
    if value["availability"] not in {"available", "unavailable", "unknown"}:
        raise ContractError("provider_status.availability is invalid")
    validate_identifier(value["status_record_id"], "provider_status.status_record_id")
    return copy.deepcopy(value)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one canonical JSON representation used for every stored record."""

    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError(f"value is not canonical JSON data: {exc}") from exc


def validate_id_sequence(value: Any, name: str) -> list[str]:
    if type(value) is not list or not value or len(value) > 256:
        raise ContractError(f"{name} must be a non-empty bounded ID array")
    result = [validate_identifier(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ContractError(f"{name} must not contain duplicate IDs")
    return result
