"""Closed request and candidate change-set contracts for theme review v1."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .errors import ContractError

SCHEMA_VERSION_V1 = "shchem_theme_review_ledger_v1"
SCHEMA_VERSION_V2 = "shchem_theme_review_ledger_v2"
SCHEMA_VERSION = SCHEMA_VERSION_V2
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION_V1, SCHEMA_VERSION_V2})
SYSTEM_CATALOG_PRINCIPAL = "_system_catalog"

DECISION_VERDICTS = frozenset(
    {
        "accept_candidate_overlay",
        "reject",
        "request_changes",
        "blocked",
    }
)

DEPENDENCY_RELATIONSHIP_KINDS = frozenset(
    {
        "uses_prior_answer",
        "uses_prior_calculated_value",
        "uses_prior_identified_substance",
        "uses_prior_structure",
        "uses_prior_experimental_conclusion",
    }
)

TAG_FIELDS = frozenset(
    {
        "item_type",
        "selection_rule",
        "primary_knowledge_K",
        "supporting_knowledge_K",
        "ability_A",
        "context_C",
        "response_R",
        "representation_RP",
        "cognitive_prelabel",
        "difficulty_factors",
    }
)

LEGACY_CHANGE_ARRAY_FIELDS = (
    "tag_replacements",
    "hierarchy_replacements",
    "atomic_boundary_candidates",
    "dependency_replacements",
)
CHANGE_ARRAY_FIELDS = (*LEGACY_CHANGE_ARRAY_FIELDS, "source_binding_candidates")

SOURCE_BINDING_ACTIONS = frozenset(
    {
        "accept_binding_candidate",
        "reject_binding_candidate",
        "request_source_evidence",
        "block_identity_binding",
    }
)
SOURCE_BINDING_STATES = frozenset(
    {
        "exact_content_set_candidate",
        "blocked_missing_source",
        "blocked_ambiguous",
        "blocked_hash_mismatch",
    }
)

CREATE_TASK_FIELDS_V1 = frozenset(
    {
        "task_id",
        "paper_id",
        "theme_id",
        "title_zh",
        "base_snapshot_sha256",
        "target_atomic_part_ids",
        "target_printed_question_ids",
        "evidence_binding_ids",
        "idempotency_key",
        "expected_revision",
    }
)
CREATE_TASK_FIELDS_V2 = frozenset({*CREATE_TASK_FIELDS_V1, "source_binding_candidates"})
# Compatibility exports used by the existing Gateway four-array path.
CREATE_TASK_FIELDS = CREATE_TASK_FIELDS_V1
CLAIM_FIELDS = frozenset({"idempotency_key", "expected_revision"})
RELEASE_FIELDS = frozenset({"idempotency_key", "expected_revision", "reason"})
CHANGE_SET_FIELDS_V1 = frozenset(
    {
        "idempotency_key",
        "expected_revision",
        "base_snapshot_sha256",
        "reason",
        *LEGACY_CHANGE_ARRAY_FIELDS,
    }
)
CHANGE_SET_FIELDS_V2 = frozenset({*CHANGE_SET_FIELDS_V1, "source_binding_candidates"})
CHANGE_SET_FIELDS = CHANGE_SET_FIELDS_V1
DECISION_FIELDS = frozenset(
    {
        "idempotency_key",
        "expected_revision",
        "change_set_id",
        "verdict",
        "reason",
        "evidence_binding_ids",
    }
)

_ID_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9_][A-Za-z0-9_.@-]{0,191}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{7,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^TRREV-[0-9]{12}-[0-9a-f]{64}$")
_URL_RE = re.compile(r"(?i)(?:https?|ftp|file|data|javascript):(?:/{0,2})")
_DRIVE_PATH_RE = re.compile(r"(?i)(?:^|\s)[a-z]:[\\/]")
_UNC_RE = re.compile(r"(?:^|\s)\\\\[^\\\s]+\\")

_FORBIDDEN_KEYS = frozenset(
    {
        "actor",
        "actor_id",
        "principal",
        "principal_id",
        "assignee",
        "assignee_id",
        "reviewer",
        "reviewer_id",
        "created_at",
        "created_at_utc",
        "updated_at",
        "timestamp",
        "authority",
        "authority_flags",
        "candidate_only",
        "central_master_mutated",
        "human_reviewed",
        "retrieval_ready",
        "retrieval_allowed",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        "official",
        "official_claim_allowed",
        "path",
        "relative_path",
        "absolute_path",
        "source_path",
        "url",
        "uri",
        "source_url",
        "file_url",
    }
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


def parse_json_object(raw: bytes, *, maximum_bytes: int = 8_000_000) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > maximum_bytes:
        raise ContractError("request body must be non-empty bounded immutable bytes")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"request body is not strict UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        raise ContractError("request body must be a JSON object")
    return value


def canonical_json_bytes(value: Any) -> bytes:
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


def request_object_and_bytes(body: Mapping[str, Any] | bytes) -> tuple[dict[str, Any], bytes]:
    """Return a detached object and the sole accepted canonical body bytes."""

    if type(body) is bytes:
        value = parse_json_object(body)
        if canonical_json_bytes(value) != body:
            raise ContractError("raw request body must already be canonical JSON")
        return value, body
    if type(body) is not dict:
        raise ContractError("request body must be an exact object or canonical bytes")
    raw = canonical_json_bytes(body)
    return parse_json_object(raw), raw


def require_exact_keys(value: Any, expected: frozenset[str], name: str) -> None:
    if type(value) is not dict:
        raise ContractError(f"{name} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ContractError(
            f"{name} exact-key violation; missing={missing}, extra={extra}"
        )


def validate_identifier(value: Any, name: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ContractError(
            f"{name} must be an opaque ASCII ID without URL/path/control syntax"
        )
    return value


def validate_principal(value: Any) -> str:
    return validate_identifier(value, "server principal_id")


def validate_idempotency_key(value: Any) -> str:
    if type(value) is not str or not _IDEMPOTENCY_RE.fullmatch(value):
        raise ContractError("idempotency_key must be an opaque 8-128 character token")
    return value


def validate_sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise ContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


def validate_revision(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or not _REVISION_RE.fullmatch(value):
        raise ContractError("expected_revision is not a valid task revision token")
    return value


def validate_text(value: Any, name: str, *, maximum: int = 4000) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ContractError(f"{name} must be a non-empty trimmed string")
    if len(value) > maximum or "\x00" in value:
        raise ContractError(f"{name} exceeds the bounded text contract")
    if (
        _URL_RE.search(value)
        or _DRIVE_PATH_RE.search(value)
        or _UNC_RE.search(value)
        or value.startswith("/")
        or "../" in value
        or "..\\" in value
    ):
        raise ContractError(f"{name} must not smuggle a path or URL")
    return value


def validate_id_list(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int = 4096,
) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ContractError(f"{name} must be a bounded ID array")
    result = [
        validate_identifier(item, f"{name}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        raise ContractError(f"{name} must not contain duplicate IDs")
    return result


def _scan_json(value: Any, path: str = "value", depth: int = 0) -> int:
    if depth > 12:
        raise ContractError(f"{path} exceeds the maximum nesting depth")
    if value is None or type(value) in (bool, int):
        return 1
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError(f"{path} contains a non-finite number")
        return 1
    if type(value) is str:
        if len(value) > 8000 or "\x00" in value:
            raise ContractError(f"{path} contains an unsafe string")
        if (
            _URL_RE.search(value)
            or _DRIVE_PATH_RE.search(value)
            or _UNC_RE.search(value)
            or value.startswith("/")
            or "../" in value
            or "..\\" in value
        ):
            raise ContractError(f"{path} must not smuggle a path or URL")
        return 1
    if type(value) is list:
        if len(value) > 4096:
            raise ContractError(f"{path} array is too large")
        total = 1
        for index, child in enumerate(value):
            total += _scan_json(child, f"{path}[{index}]", depth + 1)
        if total > 20_000:
            raise ContractError(f"{path} JSON tree is too large")
        return total
    if type(value) is dict:
        if len(value) > 256:
            raise ContractError(f"{path} object is too large")
        total = 1
        for key, child in value.items():
            if type(key) is not str:
                raise ContractError(f"{path} contains a non-string key")
            lowered = key.casefold()
            if (
                lowered in _FORBIDDEN_KEYS
                or lowered.endswith(("_path", "_url", "_uri"))
                or "reviewer" in lowered
                or "assignee" in lowered
                or lowered.startswith("authority")
            ):
                raise ContractError(f"{path}.{key} is forbidden")
            total += _scan_json(child, f"{path}.{key}", depth + 1)
        if total > 20_000:
            raise ContractError(f"{path} JSON tree is too large")
        return total
    raise ContractError(f"{path} contains a non-JSON value")


def _validate_evidence_subset(
    value: Any, name: str, task_evidence_ids: frozenset[str]
) -> list[str]:
    result = validate_id_list(value, name, minimum=1, maximum=256)
    if not set(result) <= task_evidence_ids:
        raise ContractError(f"{name} contains evidence outside the task binding")
    return result


def _validate_source_binding_definitions(
    rows: Any, *, task_evidence_ids: frozenset[str]
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 256:
        raise ContractError("source_binding_candidates must be a bounded array")
    keys = frozenset(
        {
            "candidate_id",
            "candidate_sha256",
            "source_id",
            "source_version_id",
            "binding_state",
            "accept_allowed",
            "evidence_binding_ids",
        }
    )
    output: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, row in enumerate(rows):
        require_exact_keys(row, keys, f"source_binding_candidates[{index}]")
        candidate_id = validate_identifier(
            row["candidate_id"], f"source_binding_candidates[{index}].candidate_id"
        )
        candidate_hash = validate_sha256(
            row["candidate_sha256"],
            f"source_binding_candidates[{index}].candidate_sha256",
        )
        source_version_id = validate_identifier(
            row["source_version_id"],
            f"source_binding_candidates[{index}].source_version_id",
        )
        source_id = row["source_id"]
        if source_id is not None:
            source_id = validate_identifier(
                source_id, f"source_binding_candidates[{index}].source_id"
            )
        binding_state = row["binding_state"]
        if type(binding_state) is not str or binding_state not in SOURCE_BINDING_STATES:
            raise ContractError("source binding state is outside the closed vocabulary")
        accept_allowed = row["accept_allowed"]
        if type(accept_allowed) is not bool:
            raise ContractError("source binding accept_allowed must be boolean")
        if accept_allowed != (binding_state == "exact_content_set_candidate"):
            raise ContractError("source binding accept_allowed conflicts with binding_state")
        if accept_allowed and source_id is None:
            raise ContractError("an acceptable source binding candidate requires source_id")
        evidence = _validate_evidence_subset(
            row["evidence_binding_ids"],
            f"source_binding_candidates[{index}].evidence_binding_ids",
            task_evidence_ids,
        )
        hash_subject = {
            "candidate_id": candidate_id,
            "source_id": source_id,
            "source_version_id": source_version_id,
            "binding_state": binding_state,
            "accept_allowed": accept_allowed,
            "evidence_binding_ids": evidence,
        }
        expected_hash = hashlib.sha256(canonical_json_bytes(hash_subject)).hexdigest()
        if candidate_hash != expected_hash:
            raise ContractError("source binding candidate_sha256 does not bind its fields")
        if candidate_id in seen_ids or candidate_hash in seen_hashes:
            raise ContractError("source binding candidate IDs and hashes must be unique")
        seen_ids.add(candidate_id)
        seen_hashes.add(candidate_hash)
        output.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate_hash,
                "source_id": source_id,
                "source_version_id": source_version_id,
                "binding_state": binding_state,
                "accept_allowed": accept_allowed,
                "evidence_binding_ids": evidence,
            }
        )
    return output


def validate_create_task(body: Mapping[str, Any]) -> dict[str, Any]:
    is_v2 = type(body) is dict and set(body) == CREATE_TASK_FIELDS_V2
    require_exact_keys(
        body, CREATE_TASK_FIELDS_V2 if is_v2 else CREATE_TASK_FIELDS_V1, "create_task"
    )
    result = copy.deepcopy(dict(body))
    result["task_id"] = validate_identifier(body["task_id"], "task_id")
    result["paper_id"] = validate_identifier(body["paper_id"], "paper_id")
    result["theme_id"] = validate_identifier(body["theme_id"], "theme_id")
    result["title_zh"] = validate_text(body["title_zh"], "title_zh", maximum=300)
    result["base_snapshot_sha256"] = validate_sha256(
        body["base_snapshot_sha256"], "base_snapshot_sha256"
    )
    result["target_atomic_part_ids"] = validate_id_list(
        body["target_atomic_part_ids"], "target_atomic_part_ids", maximum=4096
    )
    result["target_printed_question_ids"] = validate_id_list(
        body["target_printed_question_ids"],
        "target_printed_question_ids",
        maximum=4096,
    )
    if not result["target_atomic_part_ids"] and not result["target_printed_question_ids"]:
        raise ContractError("a review task must bind at least one target node")
    result["evidence_binding_ids"] = validate_id_list(
        body["evidence_binding_ids"], "evidence_binding_ids", minimum=1, maximum=4096
    )
    result["idempotency_key"] = validate_idempotency_key(body["idempotency_key"])
    result["expected_revision"] = validate_revision(
        body["expected_revision"], allow_none=True
    )
    if result["expected_revision"] is not None:
        raise ContractError("create_task expected_revision must be null")
    if is_v2:
        result["source_binding_candidates"] = _validate_source_binding_definitions(
            body["source_binding_candidates"],
            task_evidence_ids=frozenset(result["evidence_binding_ids"]),
        )
    return result


def validate_claim(body: Mapping[str, Any]) -> dict[str, Any]:
    require_exact_keys(body, CLAIM_FIELDS, "claim_task")
    return {
        "idempotency_key": validate_idempotency_key(body["idempotency_key"]),
        "expected_revision": validate_revision(body["expected_revision"]),
    }


def validate_release(body: Mapping[str, Any]) -> dict[str, Any]:
    require_exact_keys(body, RELEASE_FIELDS, "release_task")
    return {
        "idempotency_key": validate_idempotency_key(body["idempotency_key"]),
        "expected_revision": validate_revision(body["expected_revision"]),
        "reason": validate_text(body["reason"], "release.reason"),
    }


def _validate_tag_rows(
    rows: Any,
    *,
    atomic_ids: frozenset[str],
    evidence_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 4096:
        raise ContractError("tag_replacements must be a bounded array")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    keys = frozenset(
        {"atomic_part_id", "field", "before", "after", "evidence_binding_ids"}
    )
    for index, row in enumerate(rows):
        require_exact_keys(row, keys, f"tag_replacements[{index}]")
        atomic_id = validate_identifier(
            row["atomic_part_id"], f"tag_replacements[{index}].atomic_part_id"
        )
        if atomic_id not in atomic_ids:
            raise ContractError("tag replacement target is outside the task")
        field = row["field"]
        if type(field) is not str or field not in TAG_FIELDS:
            raise ContractError("tag replacement field is outside the closed vocabulary")
        identity = (atomic_id, field)
        if identity in seen:
            raise ContractError("one change-set cannot replace the same tag twice")
        seen.add(identity)
        _scan_json(row["before"], f"tag_replacements[{index}].before")
        _scan_json(row["after"], f"tag_replacements[{index}].after")
        if row["before"] == row["after"]:
            raise ContractError("tag replacement before and after must differ")
        output.append(
            {
                "atomic_part_id": atomic_id,
                "field": field,
                "before": copy.deepcopy(row["before"]),
                "after": copy.deepcopy(row["after"]),
                "evidence_binding_ids": _validate_evidence_subset(
                    row["evidence_binding_ids"],
                    f"tag_replacements[{index}].evidence_binding_ids",
                    evidence_ids,
                ),
            }
        )
    return output


def _validate_hierarchy_rows(
    rows: Any,
    *,
    theme_id: str,
    atomic_ids: frozenset[str],
    printed_ids: frozenset[str],
    evidence_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 4096:
        raise ContractError("hierarchy_replacements must be a bounded array")
    keys = frozenset(
        {
            "node_type",
            "node_id",
            "parent_field",
            "before_parent_id",
            "after_parent_id",
            "evidence_binding_ids",
        }
    )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        require_exact_keys(row, keys, f"hierarchy_replacements[{index}]")
        node_type = row["node_type"]
        if node_type not in {"printed_question", "atomic_part"}:
            raise ContractError("hierarchy node_type is unsupported")
        node_id = validate_identifier(
            row["node_id"], f"hierarchy_replacements[{index}].node_id"
        )
        parent_field = row["parent_field"]
        expected_field = "theme_id" if node_type == "printed_question" else "printed_question_id"
        if parent_field != expected_field:
            raise ContractError("hierarchy parent_field does not match node_type")
        if node_type == "printed_question":
            if node_id not in printed_ids:
                raise ContractError("hierarchy printed target is outside the task")
            expected_parent_ids = frozenset({theme_id})
        else:
            if node_id not in atomic_ids:
                raise ContractError("hierarchy atomic target is outside the task")
            expected_parent_ids = printed_ids
        before = row["before_parent_id"]
        if before is not None:
            before = validate_identifier(
                before, f"hierarchy_replacements[{index}].before_parent_id"
            )
        after = validate_identifier(
            row["after_parent_id"],
            f"hierarchy_replacements[{index}].after_parent_id",
        )
        if after not in expected_parent_ids:
            raise ContractError("hierarchy replacement points outside the task theme")
        if before == after:
            raise ContractError("hierarchy replacement before and after must differ")
        identity = (node_type, node_id)
        if identity in seen:
            raise ContractError("one change-set cannot replace the same parent twice")
        seen.add(identity)
        output.append(
            {
                "node_type": node_type,
                "node_id": node_id,
                "parent_field": parent_field,
                "before_parent_id": before,
                "after_parent_id": after,
                "evidence_binding_ids": _validate_evidence_subset(
                    row["evidence_binding_ids"],
                    f"hierarchy_replacements[{index}].evidence_binding_ids",
                    evidence_ids,
                ),
            }
        )
    return output


def _validate_boundary_rows(
    rows: Any,
    *,
    atomic_ids: frozenset[str],
    printed_ids: frozenset[str],
    evidence_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 4096:
        raise ContractError("atomic_boundary_candidates must be a bounded array")
    row_keys = frozenset(
        {
            "printed_question_id",
            "before_atomic_part_ids",
            "candidate_atomic_parts",
            "evidence_binding_ids",
        }
    )
    candidate_keys = frozenset(
        {"candidate_atomic_part_id", "source_order", "prompt_locator_id"}
    )
    output: list[dict[str, Any]] = []
    seen_printed: set[str] = set()
    for index, row in enumerate(rows):
        require_exact_keys(row, row_keys, f"atomic_boundary_candidates[{index}]")
        printed_id = validate_identifier(
            row["printed_question_id"],
            f"atomic_boundary_candidates[{index}].printed_question_id",
        )
        if printed_id not in printed_ids or printed_id in seen_printed:
            raise ContractError("atomic boundary printed target is outside/duplicated in task")
        seen_printed.add(printed_id)
        before_ids = validate_id_list(
            row["before_atomic_part_ids"],
            f"atomic_boundary_candidates[{index}].before_atomic_part_ids",
            maximum=512,
        )
        if not set(before_ids) <= atomic_ids:
            raise ContractError("atomic boundary before IDs are outside the task")
        candidates = row["candidate_atomic_parts"]
        if type(candidates) is not list or not 1 <= len(candidates) <= 512:
            raise ContractError("candidate_atomic_parts must be a non-empty bounded array")
        candidate_rows: list[dict[str, Any]] = []
        candidate_ids: set[str] = set()
        source_orders: set[int] = set()
        for child_index, child in enumerate(candidates):
            require_exact_keys(
                child,
                candidate_keys,
                f"atomic_boundary_candidates[{index}].candidate_atomic_parts[{child_index}]",
            )
            candidate_id = validate_identifier(
                child["candidate_atomic_part_id"], "candidate_atomic_part_id"
            )
            prompt_id = validate_identifier(child["prompt_locator_id"], "prompt_locator_id")
            source_order = child["source_order"]
            if type(source_order) is not int or type(source_order) is bool or source_order < 1:
                raise ContractError("candidate atomic source_order must be a positive integer")
            if candidate_id in candidate_ids or source_order in source_orders:
                raise ContractError("candidate atomic IDs and source orders must be unique")
            candidate_ids.add(candidate_id)
            source_orders.add(source_order)
            candidate_rows.append(
                {
                    "candidate_atomic_part_id": candidate_id,
                    "source_order": source_order,
                    "prompt_locator_id": prompt_id,
                }
            )
        output.append(
            {
                "printed_question_id": printed_id,
                "before_atomic_part_ids": before_ids,
                "candidate_atomic_parts": candidate_rows,
                "evidence_binding_ids": _validate_evidence_subset(
                    row["evidence_binding_ids"],
                    f"atomic_boundary_candidates[{index}].evidence_binding_ids",
                    evidence_ids,
                ),
            }
        )
    return output


def _validate_dependency_rows(
    rows: Any,
    *,
    atomic_ids: frozenset[str],
    evidence_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 4096:
        raise ContractError("dependency_replacements must be a bounded array")
    keys = frozenset(
        {
            "atomic_part_id",
            "before_dependencies",
            "after_dependencies",
            "evidence_binding_ids",
        }
    )
    edge_keys = frozenset({"atomic_part_id", "relationship_kind"})

    def dependency_edges(value: Any, name: str, owner_id: str) -> list[dict[str, str]]:
        if type(value) is not list or len(value) > 512:
            raise ContractError(f"{name} must be a bounded dependency edge array")
        result: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str]] = set()
        for edge_index, edge in enumerate(value):
            require_exact_keys(edge, edge_keys, f"{name}[{edge_index}]")
            prior_id = validate_identifier(
                edge["atomic_part_id"], f"{name}[{edge_index}].atomic_part_id"
            )
            relationship = edge["relationship_kind"]
            if (
                type(relationship) is not str
                or relationship not in DEPENDENCY_RELATIONSHIP_KINDS
            ):
                raise ContractError(
                    f"{name}[{edge_index}].relationship_kind is outside the closed vocabulary"
                )
            if prior_id not in atomic_ids:
                raise ContractError("dependency replacement points outside the task theme")
            if prior_id == owner_id:
                raise ContractError("an atomic part cannot depend on itself")
            identity = (prior_id, relationship)
            if identity in seen_edges:
                raise ContractError("dependency edge arrays must not contain duplicates")
            seen_edges.add(identity)
            result.append(
                {"atomic_part_id": prior_id, "relationship_kind": relationship}
            )
        return result

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        require_exact_keys(row, keys, f"dependency_replacements[{index}]")
        atomic_id = validate_identifier(row["atomic_part_id"], "atomic_part_id")
        if atomic_id not in atomic_ids or atomic_id in seen:
            raise ContractError("dependency target is outside/duplicated in the task")
        seen.add(atomic_id)
        before_edges = dependency_edges(
            row["before_dependencies"], "before_dependencies", atomic_id
        )
        after_edges = dependency_edges(
            row["after_dependencies"], "after_dependencies", atomic_id
        )
        if before_edges == after_edges:
            raise ContractError("dependency replacement before and after must differ")
        output.append(
            {
                "atomic_part_id": atomic_id,
                "before_dependencies": before_edges,
                "after_dependencies": after_edges,
                "evidence_binding_ids": _validate_evidence_subset(
                    row["evidence_binding_ids"],
                    f"dependency_replacements[{index}].evidence_binding_ids",
                    evidence_ids,
                ),
            }
        )
    return output


def _validate_source_binding_rows(
    rows: Any,
    *,
    task_candidates: list[Mapping[str, Any]],
    task_evidence_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if type(rows) is not list or len(rows) > 256:
        raise ContractError("source_binding_candidates must be a bounded array")
    keys = frozenset(
        {
            "candidate_id",
            "candidate_sha256",
            "source_version_id",
            "action",
            "reason",
            "evidence_binding_ids",
        }
    )
    frozen = {row["candidate_id"]: row for row in task_candidates}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        require_exact_keys(row, keys, f"source_binding_candidates[{index}]")
        candidate_id = validate_identifier(row["candidate_id"], "candidate_id")
        if candidate_id in seen:
            raise ContractError("one change-set cannot decide one source candidate twice")
        seen.add(candidate_id)
        candidate = frozen.get(candidate_id)
        if candidate is None:
            raise ContractError("source binding candidate is unknown to the frozen task")
        candidate_hash = validate_sha256(row["candidate_sha256"], "candidate_sha256")
        source_version_id = validate_identifier(
            row["source_version_id"], "source_version_id"
        )
        if (
            candidate_hash != candidate["candidate_sha256"]
            or source_version_id != candidate["source_version_id"]
        ):
            raise ContractError("source binding candidate hash/version is stale")
        action = row["action"]
        if type(action) is not str or action not in SOURCE_BINDING_ACTIONS:
            raise ContractError("source binding action is outside the closed vocabulary")
        if action == "accept_binding_candidate" and not candidate["accept_allowed"]:
            raise ContractError("blocked source binding candidate cannot be accepted")
        evidence = _validate_evidence_subset(
            row["evidence_binding_ids"],
            f"source_binding_candidates[{index}].evidence_binding_ids",
            task_evidence_ids,
        )
        if not set(evidence) <= set(candidate["evidence_binding_ids"]):
            raise ContractError("source binding evidence is outside the frozen candidate")
        output.append(
            {
                "candidate_id": candidate_id,
                "candidate_sha256": candidate_hash,
                "source_version_id": source_version_id,
                "action": action,
                "reason": validate_text(
                    row["reason"], f"source_binding_candidates[{index}].reason"
                ),
                "evidence_binding_ids": evidence,
            }
        )
    return output


def validate_change_set(body: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    is_v2 = type(body) is dict and set(body) == CHANGE_SET_FIELDS_V2
    require_exact_keys(
        body, CHANGE_SET_FIELDS_V2 if is_v2 else CHANGE_SET_FIELDS_V1, "change_set"
    )
    result: dict[str, Any] = {
        "idempotency_key": validate_idempotency_key(body["idempotency_key"]),
        "expected_revision": validate_revision(body["expected_revision"]),
        "base_snapshot_sha256": validate_sha256(
            body["base_snapshot_sha256"], "base_snapshot_sha256"
        ),
        "reason": validate_text(body["reason"], "change_set.reason"),
    }
    if result["base_snapshot_sha256"] != task["base_snapshot_sha256"]:
        raise ContractError("change-set base snapshot differs from the task binding")
    atomic_ids = frozenset(task["target_atomic_part_ids"])
    printed_ids = frozenset(task["target_printed_question_ids"])
    evidence_ids = frozenset(task["evidence_binding_ids"])
    result["tag_replacements"] = _validate_tag_rows(
        body["tag_replacements"], atomic_ids=atomic_ids, evidence_ids=evidence_ids
    )
    result["hierarchy_replacements"] = _validate_hierarchy_rows(
        body["hierarchy_replacements"],
        theme_id=task["theme_id"],
        atomic_ids=atomic_ids,
        printed_ids=printed_ids,
        evidence_ids=evidence_ids,
    )
    result["atomic_boundary_candidates"] = _validate_boundary_rows(
        body["atomic_boundary_candidates"],
        atomic_ids=atomic_ids,
        printed_ids=printed_ids,
        evidence_ids=evidence_ids,
    )
    result["dependency_replacements"] = _validate_dependency_rows(
        body["dependency_replacements"],
        atomic_ids=atomic_ids,
        evidence_ids=evidence_ids,
    )
    if is_v2:
        result["source_binding_candidates"] = _validate_source_binding_rows(
            body["source_binding_candidates"],
            task_candidates=task.get("source_binding_candidates", []),
            task_evidence_ids=evidence_ids,
        )
    fields = CHANGE_ARRAY_FIELDS if is_v2 else LEGACY_CHANGE_ARRAY_FIELDS
    if not any(result[name] for name in fields):
        raise ContractError("a change-set must contain at least one candidate change")
    return result


def validate_decision(
    body: Mapping[str, Any], task: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    require_exact_keys(body, DECISION_FIELDS, "decision")
    verdict = body["verdict"]
    if type(verdict) is not str or verdict not in DECISION_VERDICTS:
        raise ContractError("decision verdict is outside the closed vocabulary")
    evidence_ids = validate_id_list(
        body["evidence_binding_ids"],
        "decision.evidence_binding_ids",
        minimum=1,
        maximum=256,
    )
    if task is not None and not set(evidence_ids) <= set(task["evidence_binding_ids"]):
        raise ContractError("decision evidence is outside the task binding")
    return {
        "idempotency_key": validate_idempotency_key(body["idempotency_key"]),
        "expected_revision": validate_revision(body["expected_revision"]),
        "change_set_id": validate_identifier(body["change_set_id"], "change_set_id"),
        "verdict": verdict,
        "reason": validate_text(body["reason"], "decision.reason"),
        "evidence_binding_ids": evidence_ids,
    }
