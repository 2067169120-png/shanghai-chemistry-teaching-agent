"""Strict contracts for atomic-part candidate tag patches.

This module deliberately models field replacements, not RFC 6902 / arbitrary
JSON Patch operations.  It has no knowledge-base apply or promotion operation.
"""

from __future__ import annotations

import copy
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ContractError, StoreConflictError

PATCH_SCHEMA_VERSION = "shchem_tag_patch_candidate_v1"
TAXONOMY_SNAPSHOT_SCHEMA_VERSION = "shchem_tag_taxonomy_snapshot_v1"
BASE_INDEX_MANIFEST_SCHEMA_VERSION = "shchem_atomic_part_index_manifest_v1"

CHANGE_FIELDS = frozenset(
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

REQUEST_FIELDS = frozenset(
    {
        "node_type",
        "node_id",
        "paper_id",
        "base_index_manifest_sha256",
        "target_record_sha256",
        "taxonomy_sha256",
        "changes",
        "reason",
        "evidence_binding_ids",
    }
)

ITEM_TYPES = frozenset(
    {
        "embedded_single_choice",
        "embedded_multiple_choice",
        "embedded_indeterminate_choice",
        "short_fill",
        "chemical_equation_or_notation",
        "organic_structure_or_route",
        "quantitative_calculation",
        "reasoned_explanation",
        "graph_read_draw_complete",
        "experiment_operation_apparatus_plan",
        "process_flow_condition_choice",
        "comparison_or_open_response",
    }
)
SELECTION_RULES = frozenset(
    {"single", "multiple", "indeterminate", "not_applicable", "unknown"}
)
DIFFICULTY_FACTOR_FIELDS = frozenset(
    {
        "information_transformations",
        "reasoning_chain_steps",
        "knowledge_module_span",
        "representation_switches",
        "calculation_load",
        "experiment_load",
        "openness",
        "unfamiliarity",
        "language_load",
        "dependency_on_prior_parts",
    }
)

# This public value is documentation/introspection only.  Store writes and
# validators deliberately use their own private fixed literals so rebinding or
# attempted mutation of this public alias cannot elevate persisted authority.
AUTHORITY_FLAGS = MappingProxyType({
    "candidate_only": True,
    "human_reviewed": False,
    "retrieval_ready": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "official": False,
})

_BASE_MANIFEST_FIELDS = frozenset(
    {"schema_version", "manifest_id", "paper_id", "node_kind", "records"}
)
_BASE_MANIFEST_RECORD_FIELDS = frozenset(
    {"paper_id", "node_kind", "record_id", "record_sha256", "record_size_bytes"}
)

OPAQUE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_ID_RE = re.compile(OPAQUE_ID_PATTERN)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_AXIS_PATTERNS = {
    "K": re.compile(r"^K[0-9][A-Za-z0-9_.-]*$"),
    "A": re.compile(r"^A[0-9][A-Za-z0-9_.-]*$"),
    "C": re.compile(r"^C[0-9][A-Za-z0-9_.-]*$"),
    "R": re.compile(r"^R(?!P)[0-9][A-Za-z0-9_.-]*$"),
    "RP": re.compile(r"^RP[0-9][A-Za-z0-9_.-]*$"),
    "D": re.compile(r"^D[1-5]$"),
}

# These names are forbidden at every depth below changes.  The exact top-level
# allowlist already rejects them directly; the recursive rule prevents a
# difficulty-factor object from becoming a smuggling envelope.
_FORBIDDEN_NESTED_CHANGE_KEYS = frozenset(
    {
        "op",
        "operations",
        "json_patch",
        "patch",
        "path",
        "from",
        "node_id",
        "part_id",
        "parent_id",
        "parent_node_id",
        "parent_printed_question_id",
        "source_order",
        "order",
        "position",
        "gate",
        "gates",
        "authority",
        "candidate_only",
        "human_reviewed",
        "retrieval_ready",
        "retrieval_allowed",
        "generation_allowed",
        "publication_allowed",
        "official",
        "measured_difficulty",
        "measured_difficulty_value",
        "source_path",
        "source_url",
        "url",
        "raw",
        "raw_text",
        "raw_input",
    }
)

_DIMENSION_ALIASES = {
    "K": ("K", "knowledge_points", "primary_knowledge_K"),
    "A": ("A", "abilities", "ability_A"),
    "C": ("C", "contexts", "context_C"),
    "R": ("R", "response_types", "response_R"),
    "RP": (
        "RP",
        "representation_types",
        "representations",
        "representation_RP",
    ),
    "D": ("D", "difficulty", "cognitive_prelabel"),
}


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
    """Parse strict UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""

    if type(raw) is not bytes:
        raise ContractError("raw snapshot must be immutable bytes")
    if not raw or len(raw) > maximum_bytes:
        raise ContractError("raw snapshot byte length is outside the allowed range")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
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


def canonical_json_bytes(value: Any) -> bytes:
    """Return the sole canonical JSON representation used by this workbench."""

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


def require_canonical_object(raw: bytes, name: str) -> dict[str, Any]:
    value = parse_json_object(raw)
    if canonical_json_bytes(value) != raw:
        raise ContractError(f"{name} raw bytes must already be canonical JSON")
    return value


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
    """Reject URI, drive, UNC, slash, whitespace, control, and NUL IDs."""

    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ContractError(
            f"{name} must be an opaque ASCII ID without URI/drive/UNC/slash/NUL syntax"
        )
    return value


def validate_sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise ContractError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _validate_text(value: Any, name: str, *, maximum: int) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ContractError(f"{name} must be a non-empty trimmed string")
    if len(value) > maximum or "\x00" in value:
        raise ContractError(f"{name} exceeds the bounded text contract")
    return value


def _validate_id_list(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int = 128,
) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ContractError(f"{name} must be a bounded ID array")
    output = [validate_identifier(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(output) != len(set(output)):
        raise ContractError(f"{name} must not contain duplicate IDs")
    return output


def _axis_entries(container: Mapping[str, Any], axis: str) -> Any:
    for name in _DIMENSION_ALIASES[axis]:
        if name in container:
            return container[name]
    raise ContractError(f"taxonomy snapshot is missing the {axis} axis")


def extract_taxonomy_axes(taxonomy: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Extract six independently validated ID sets from a caller-bound snapshot.

    Preferred snapshots use ``{"axes":{"K":[...], ...}}``.  The local
    ``dimensions`` vocabulary shape is also accepted so callers may freeze an
    existing controlled taxonomy without translating its IDs.
    """

    if type(taxonomy) is not dict:
        raise ContractError("taxonomy snapshot must be an object")
    if "axes" in taxonomy:
        container = taxonomy["axes"]
        require_exact_keys(container, frozenset(_AXIS_PATTERNS), "taxonomy.axes")
    elif "dimensions" in taxonomy:
        container = taxonomy["dimensions"]
        if type(container) is not dict:
            raise ContractError("taxonomy.dimensions must be an object")
    else:
        container = taxonomy

    axes: dict[str, frozenset[str]] = {}
    for axis in _AXIS_PATTERNS:
        entries = _axis_entries(container, axis)
        if type(entries) is not list or not entries or len(entries) > 4096:
            raise ContractError(f"taxonomy axis {axis} must be a non-empty bounded array")
        ids: list[str] = []
        for index, entry in enumerate(entries):
            candidate = entry.get("id") if type(entry) is dict else entry
            item_id = validate_identifier(candidate, f"taxonomy.{axis}[{index}].id")
            if not _AXIS_PATTERNS[axis].fullmatch(item_id):
                raise ContractError(f"taxonomy ID {item_id!r} is invalid for axis {axis}")
            ids.append(item_id)
        if len(ids) != len(set(ids)):
            raise ContractError(f"taxonomy axis {axis} contains duplicate IDs")
        axes[axis] = frozenset(ids)
    return axes


def _require_axis_member(value: Any, axis: str, axes: Mapping[str, frozenset[str]], name: str) -> str:
    item_id = validate_identifier(value, name)
    if not _AXIS_PATTERNS[axis].fullmatch(item_id):
        raise ContractError(f"{name} is not syntactically an {axis}-axis ID")
    if item_id not in axes[axis]:
        raise ContractError(f"{name} is absent from the bound taxonomy {axis} axis")
    return item_id


def _axis_list(
    value: Any,
    axis: str,
    axes: Mapping[str, frozenset[str]],
    name: str,
    *,
    minimum: int,
) -> list[str]:
    ids = _validate_id_list(value, name, minimum=minimum)
    return [
        _require_axis_member(item, axis, axes, f"{name}[{index}]")
        for index, item in enumerate(ids)
    ]


def _scan_forbidden_change_keys(value: Any, path: str = "changes") -> None:
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ContractError(f"{path} contains a non-string object key")
            if key in _FORBIDDEN_NESTED_CHANGE_KEYS or key.startswith("parent_"):
                raise ContractError(f"{path}.{key} is forbidden in a candidate tag patch")
            _scan_forbidden_change_keys(child, f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _scan_forbidden_change_keys(child, f"{path}[{index}]")
    elif value is None or type(value) in (str, int, float, bool):
        if type(value) is float and not math.isfinite(value):
            raise ContractError(f"{path} contains a non-finite number")
    else:
        raise ContractError(f"{path} contains a non-JSON value")


def _validate_factor_value(value: Any, name: str) -> None:
    if value is None or value in ("unknown", "blocked_pending_review"):
        raise ContractError(f"{name} must contain non-unknown factor evidence")
    if type(value) is bool:
        raise ContractError(f"{name} must not be a boolean placeholder")
    if type(value) is int:
        if value < 0:
            raise ContractError(f"{name} integer value must be non-negative")
        return
    if type(value) is float:
        if not math.isfinite(value) or value < 0:
            raise ContractError(f"{name} numeric value must be finite and non-negative")
        return
    if type(value) is str:
        _validate_text(value, name, maximum=128)
        return
    if type(value) is dict:
        require_exact_keys(value, frozenset({"value"}), name)
        _validate_factor_value(value["value"], f"{name}.value")
        return
    raise ContractError(f"{name} has an unsupported factor evidence value")


def validate_changes(
    value: Any,
    axes: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    if type(value) is not dict or not value:
        raise ContractError("changes must be a non-empty object")
    extra = sorted(set(value) - CHANGE_FIELDS)
    if extra:
        raise ContractError(f"changes contains forbidden fields: {extra}")
    _scan_forbidden_change_keys(value)

    output = copy.deepcopy(value)
    if "item_type" in value and (
        type(value["item_type"]) is not str or value["item_type"] not in ITEM_TYPES
    ):
        raise ContractError("changes.item_type is outside the closed vocabulary")
    if "selection_rule" in value and (
        type(value["selection_rule"]) is not str
        or value["selection_rule"] not in SELECTION_RULES
    ):
        raise ContractError("changes.selection_rule is outside the closed vocabulary")
    if "primary_knowledge_K" in value:
        _require_axis_member(value["primary_knowledge_K"], "K", axes, "changes.primary_knowledge_K")
    if "supporting_knowledge_K" in value:
        _axis_list(value["supporting_knowledge_K"], "K", axes, "changes.supporting_knowledge_K", minimum=0)
    if "ability_A" in value:
        _axis_list(value["ability_A"], "A", axes, "changes.ability_A", minimum=1)
    if "context_C" in value:
        _axis_list(value["context_C"], "C", axes, "changes.context_C", minimum=1)
    if "response_R" in value:
        _require_axis_member(value["response_R"], "R", axes, "changes.response_R")
    if "representation_RP" in value:
        _axis_list(value["representation_RP"], "RP", axes, "changes.representation_RP", minimum=1)

    cognitive_present = "cognitive_prelabel" in value
    cognitive = value.get("cognitive_prelabel")
    if cognitive_present and cognitive is not None:
        _require_axis_member(cognitive, "D", axes, "changes.cognitive_prelabel")
        if "difficulty_factors" not in value:
            raise ContractError(
                "non-null cognitive_prelabel requires all ten difficulty_factors"
            )
    if cognitive_present and cognitive == "":
        raise ContractError("changes.cognitive_prelabel must be D1-D5 or null")

    if "difficulty_factors" in value:
        factors = value["difficulty_factors"]
        require_exact_keys(factors, DIFFICULTY_FACTOR_FIELDS, "changes.difficulty_factors")
        for name in sorted(DIFFICULTY_FACTOR_FIELDS):
            _validate_factor_value(factors[name], f"changes.difficulty_factors.{name}")
    return output


def _target_identity(target: Mapping[str, Any]) -> tuple[str, str, str]:
    node_types = [target[key] for key in ("node_type", "kind") if key in target]
    if not node_types or any(item != "atomic_part" for item in node_types):
        raise ContractError("target record must identify node_type=atomic_part")
    node_ids = [target[key] for key in ("node_id", "part_id") if key in target]
    if not node_ids:
        raise ContractError("target record is missing its atomic-part ID")
    first = validate_identifier(node_ids[0], "target atomic-part ID")
    if any(item != first for item in node_ids):
        raise ContractError("target record contains inconsistent atomic-part IDs")
    paper_ids = [target[key] for key in ("paper_id",) if key in target]
    if not paper_ids:
        raise ContractError("target record is missing paper_id")
    paper_id = validate_identifier(paper_ids[0], "target paper_id")
    return "atomic_part", first, paper_id


def _validate_base_manifest_membership(
    base: Mapping[str, Any],
    *,
    target_node_type: str,
    target_node_id: str,
    target_paper_id: str,
    target_record_raw: bytes,
) -> dict[str, Any]:
    """Bind one target's paper, kind, ID, exact hash, and byte count to a manifest."""

    require_exact_keys(base, _BASE_MANIFEST_FIELDS, "base_index_manifest")
    if base["schema_version"] != BASE_INDEX_MANIFEST_SCHEMA_VERSION:
        raise ContractError("base_index_manifest schema_version is unsupported")
    manifest_id = validate_identifier(base["manifest_id"], "base_index_manifest.manifest_id")
    paper_id = validate_identifier(base["paper_id"], "base_index_manifest.paper_id")
    if base["node_kind"] != "atomic_part":
        raise ContractError("base_index_manifest.node_kind must be atomic_part")
    records = base["records"]
    if type(records) is not list or not records or len(records) > 1_000_000:
        raise ContractError("base_index_manifest.records must be a non-empty bounded array")

    members: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(records):
        require_exact_keys(
            row,
            _BASE_MANIFEST_RECORD_FIELDS,
            f"base_index_manifest.records[{index}]",
        )
        row_paper_id = validate_identifier(
            row["paper_id"], f"base_index_manifest.records[{index}].paper_id"
        )
        if row_paper_id != paper_id:
            raise ContractError("base manifest record paper_id differs from manifest paper_id")
        if row["node_kind"] != "atomic_part":
            raise ContractError("base manifest record node_kind must be atomic_part")
        record_id = validate_identifier(
            row["record_id"], f"base_index_manifest.records[{index}].record_id"
        )
        if record_id in members:
            raise ContractError("base_index_manifest contains duplicate record IDs")
        record_sha256 = validate_sha256(
            row["record_sha256"],
            f"base_index_manifest.records[{index}].record_sha256",
        )
        record_size_bytes = row["record_size_bytes"]
        if type(record_size_bytes) is not int or record_size_bytes < 1:
            raise ContractError("base manifest record_size_bytes must be a positive integer")
        members[record_id] = {
            "paper_id": row_paper_id,
            "node_kind": row["node_kind"],
            "record_id": record_id,
            "record_sha256": record_sha256,
            "record_size_bytes": record_size_bytes,
        }

    if paper_id != target_paper_id or base["node_kind"] != target_node_type:
        raise StoreConflictError("base manifest and target paper/node-kind identity conflict")
    member = members.get(target_node_id)
    if member is None:
        raise StoreConflictError("target record is not a member of the bound base manifest")

    import hashlib

    if (
        member["paper_id"] != target_paper_id
        or member["node_kind"] != target_node_type
        or member["record_id"] != target_node_id
        or member["record_sha256"] != hashlib.sha256(target_record_raw).hexdigest()
        or member["record_size_bytes"] != len(target_record_raw)
    ):
        raise StoreConflictError("target record bytes do not match base manifest membership")
    return {
        "base_index_manifest_id": manifest_id,
        "paper_id": paper_id,
        "base_node_kind": base["node_kind"],
        "target_record_size_bytes": len(target_record_raw),
    }


def validate_patch_request(
    request: Mapping[str, Any],
    *,
    base_index_manifest_raw: bytes,
    target_record_raw: bytes,
    taxonomy_raw: bytes,
) -> dict[str, Any]:
    """Validate request fields and all three optimistic-concurrency snapshots."""

    require_exact_keys(request, REQUEST_FIELDS, "patch_request")
    if request["node_type"] != "atomic_part":
        raise ContractError("only node_type=atomic_part is patchable")
    node_id = validate_identifier(request["node_id"], "patch_request.node_id")
    paper_id = validate_identifier(request["paper_id"], "patch_request.paper_id")
    for name in (
        "base_index_manifest_sha256",
        "target_record_sha256",
        "taxonomy_sha256",
    ):
        validate_sha256(request[name], f"patch_request.{name}")

    # Canonical raw bytes are the concurrency unit; reserialization of an
    # equivalent object is intentionally not accepted as the same observation.
    import hashlib

    base = require_canonical_object(base_index_manifest_raw, "base_index_manifest")
    target = require_canonical_object(target_record_raw, "target_record")
    taxonomy = require_canonical_object(taxonomy_raw, "taxonomy")
    actual_hashes = {
        "base_index_manifest_sha256": hashlib.sha256(base_index_manifest_raw).hexdigest(),
        "target_record_sha256": hashlib.sha256(target_record_raw).hexdigest(),
        "taxonomy_sha256": hashlib.sha256(taxonomy_raw).hexdigest(),
    }
    for name, actual in actual_hashes.items():
        if request[name] != actual:
            raise StoreConflictError(f"stale {name}; optimistic concurrency conflict")

    node_type, target_id, target_paper_id = _target_identity(target)
    if (
        node_type != request["node_type"]
        or target_id != node_id
        or target_paper_id != paper_id
    ):
        raise StoreConflictError("stale or mismatched target atomic-part identity")
    base_binding = _validate_base_manifest_membership(
        base,
        target_node_type=node_type,
        target_node_id=target_id,
        target_paper_id=target_paper_id,
        target_record_raw=target_record_raw,
    )

    axes = extract_taxonomy_axes(taxonomy)
    changes = validate_changes(request["changes"], axes)
    reason = _validate_text(request["reason"], "patch_request.reason", maximum=4000)
    evidence_ids = _validate_id_list(
        request["evidence_binding_ids"],
        "patch_request.evidence_binding_ids",
        minimum=1,
        maximum=256,
    )
    return {
        "node_type": "atomic_part",
        "node_id": node_id,
        "paper_id": paper_id,
        **base_binding,
        "base_index_manifest_size_bytes": len(base_index_manifest_raw),
        **actual_hashes,
        "changes": changes,
        "reason": reason,
        "evidence_binding_ids": evidence_ids,
    }
