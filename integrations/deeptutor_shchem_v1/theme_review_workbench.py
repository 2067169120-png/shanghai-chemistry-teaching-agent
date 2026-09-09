"""Candidate-only gateway for whole-theme teacher review.

This module deliberately separates three concerns:

* :class:`ThemeReviewTaskCatalog` is an immutable, canonical snapshot built
  only from the hash-verified public hierarchy and theme readers.  It never
  opens or writes the mutable review ledger and can therefore be placed in a
  frozen browse release.
* :class:`ThemeReviewGateway` merges that frozen catalog with an independent
  append-only local ledger.  The ledger is never part of the browse snapshot.
* every proposed edit remains a candidate overlay.  There is no central-KB
  apply, authority promotion, teaching, scoring, generation, or publication
  operation in this adapter.

The current source inventory has one important fail-closed boundary.  The 43
Datong atomic parts are unassigned in Master470.  Five exact-crosswalk parent
groups may be reviewed together, but they remain
``paper_theme_boundary_review`` tasks; they are not relabelled as completed
``theme_big_question`` records.  The other 94 missing-theme printed questions
have no atomic children and consequently accept atomic-boundary candidates
only.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from integrations.shchem_review_workbench_v1 import (
    CHANGE_SET_FIELDS_V2,
    DEPENDENCY_RELATIONSHIP_KINDS,
    SOURCE_BINDING_ACTIONS,
    SYSTEM_CATALOG_PRINCIPAL,
    AppendOnlyThemeReviewStore,
    ThemeReviewError,
    canonical_json_bytes,
)
from integrations.shchem_review_workbench_v1.contracts import (
    CHANGE_SET_FIELDS,
    CLAIM_FIELDS,
    DECISION_FIELDS,
    RELEASE_FIELDS,
    parse_json_object,
    validate_identifier,
)
from integrations.shchem_tagging_workbench_v1 import (
    DIFFICULTY_FACTOR_FIELDS,
    ITEM_TYPES,
    SELECTION_RULES,
)
from integrations.shchem_tagging_workbench_v1 import (
    ContractError as TagContractError,
)
from integrations.shchem_tagging_workbench_v1.contracts import (
    extract_taxonomy_axes,
    validate_changes,
)

from .public_kb import PublicKBReader, ReadOnlyDataError
from .theme_workbench import MASTER_SCOPE, ThemeWorkbenchError, ThemeWorkbenchReader

CATALOG_SCHEMA_VERSION_V1 = "shchem_gateway_theme_review_task_catalog_v1"
CATALOG_SCHEMA_VERSION_V2 = "shchem_gateway_theme_review_task_catalog_v2"
CATALOG_SCHEMA_VERSION = CATALOG_SCHEMA_VERSION_V2
GATEWAY_SCHEMA_VERSION = "shchem_gateway_theme_review_v2"
REVIEW_TASK_WRITE_CAPABILITY = "review_task_write"
REVIEW_CANDIDATE_WRITE_CAPABILITY = "review_candidate_write"
REVIEW_DECISION_WRITE_CAPABILITY = "review_decision_write"

THEME_TASK = "theme_big_question"
BOUNDARY_TASK = "paper_theme_boundary_review"
TASK_KINDS = frozenset({THEME_TASK, BOUNDARY_TASK})

BOUNDARY_MODE_THEME = "whole_theme_candidate_review"
BOUNDARY_MODE_CANDIDATE_THEME = "candidate_theme_parent_review"
BOUNDARY_MODE_ATOMIC_ONLY = "atomic_boundary_only"
BOUNDARY_MODES = frozenset(
    {
        BOUNDARY_MODE_THEME,
        BOUNDARY_MODE_CANDIDATE_THEME,
        BOUNDARY_MODE_ATOMIC_ONLY,
    }
)

# This alias is intentionally fixed in the gateway as well as in the isolated
# core contract.  Shared material is context, never a dependency edge.
DEPENDENCY_RELATIONSHIP_KINDS = frozenset(
    {
        "uses_prior_answer",
        "uses_prior_calculated_value",
        "uses_prior_identified_substance",
        "uses_prior_structure",
        "uses_prior_experimental_conclusion",
    }
)

ALLOWED_ACTION_KEYS = (
    "claim",
    "release",
    "submit_change_set",
    "record_decision",
)

_AUTHORITY = {
    "candidate_only": True,
    "central_master_mutated": False,
    "human_reviewed": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
}

_EXPECTED_COUNTS = {
    "tasks": 147,
    "theme_big_question_tasks": 48,
    "paper_theme_boundary_review_tasks": 99,
    "candidate_theme_boundary_tasks": 5,
    "atomic_boundary_only_tasks": 94,
    "target_printed_questions": 501,
    "target_atomic_parts": 470,
    "missing_theme_printed_questions": 135,
    "candidate_group_printed_questions": 41,
    "candidate_group_atomic_parts": 43,
    "no_atomic_boundary_printed_questions": 94,
}

_LAYER_COUNTS = {
    "paper": 24,
    "theme_big_question": 68,
    "printed_question": 501,
    "atomic_part": 470,
}

_CATALOG_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "counts",
        "source_snapshot",
        "taxonomy_axes",
        "tasks",
        "authority",
        "integrity",
    }
)

_TASK_KEYS_V1 = frozenset(
    {
        "task_id",
        "task_kind",
        "priority",
        "paper_id",
        "theme_id",
        "candidate_theme_id",
        "title_zh",
        "boundary_mode",
        "candidate_basis_zh",
        "base_snapshot_sha256",
        "task_input_sha256",
        "target_atomic_part_ids",
        "target_printed_question_ids",
        "allowed_theme_ids",
        "current_tags",
        "current_hierarchy",
        "current_dependencies",
        "source_order",
        "evidence_bindings",
        "node_evidence_ids",
        "shared_materials",
        "candidate_only",
        "human_reviewed",
        "central_master_mutated",
    }
)
_TASK_KEYS_V2 = frozenset(
    {
        *_TASK_KEYS_V1,
        "source_binding",
        "source_binding_candidates",
    }
)

_TASK_DERIVED_FIELDS = frozenset(
    {
        "task_id",
        "base_snapshot_sha256",
        "task_input_sha256",
        "candidate_only",
        "human_reviewed",
        "central_master_mutated",
    }
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^TRREV-[0-9]{12}-[0-9a-f]{64}$")
_HOST_OR_URL_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\[^\\\s]+\\|(?:https?|ftp|file|data|javascript):/{0,2})"
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "path",
        "relative_path",
        "absolute_path",
        "source_path",
        "url",
        "uri",
        "source_url",
        "file_url",
        "event_relative_path",
        "commit_relative_path",
    }
)

_SOURCE_CROSSWALK_SCHEMA_VERSION = "1.0.0-source-version-crosswalk"
_SOURCE_CROSSWALK_ARTIFACT_ID = (
    "SHCHEM-SOURCE-VERSION-CROSSWALK-V1-2026-08-26"
)
_SOURCE_CROSSWALK_MANIFEST_SCHEMA_VERSION = (
    "1.0.0-source-version-crosswalk-manifest"
)
_SOURCE_BINDING_SCHEMA_VERSION = "shchem_gateway_source_binding_projection_v1"
_SOURCE_CROSSWALK_PAPERS = frozenset(
    {
        "MASTER-PAPER-21b2686786eef90582ca",
        "MASTER-PAPER-4a39a5ecb376c90f8ed7",
        "MASTER-PAPER-eca872096473cd6ec91a",
    }
)
_HUAER_PAPER_ID = "MASTER-PAPER-21b2686786eef90582ca"
_FUDAN_PAPER_ID = "MASTER-PAPER-4a39a5ecb376c90f8ed7"
_DATONG_PAPER_ID = "MASTER-PAPER-eca872096473cd6ec91a"

_SOURCE_ARTIFACT_AUTHORITY = {
    "state": "machine_candidate_only",
    "human_reviewed": False,
    "human_source_reviewed": False,
    "official": False,
    "official_status": "nonofficial",
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
}
_SOURCE_ARTIFACT_MANIFEST_AUTHORITY = {
    "state": "machine_candidate_only",
    "human_reviewed": False,
    "official": False,
    "teaching_use_allowed": False,
    "publication_allowed": False,
}

_SOURCE_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "paper_id",
        "master_source_id",
        "source_version_id",
        "source_version_sha256",
        "source_layer",
        "package_id",
        "paper_face",
        "article_attribution",
        "catalog_binding",
        "authority",
        "blockers",
        "evidence_ids",
    }
)
_SOURCE_PAPER_FACE_KEYS = frozenset(
    {
        "identity_status",
        "school_zh",
        "academic_year_zh",
        "semester_zh",
        "grade_zh",
        "exam_type_zh",
        "boundary_zh",
    }
)
_SOURCE_ARTICLE_ATTRIBUTION_KEYS = frozenset(
    {
        "status",
        "school_zh",
        "article_title_zh",
        "source_account_zh",
        "boundary_zh",
    }
)
_SOURCE_CATALOG_BINDING_KEYS = frozenset(
    {"status", "source_id", "match_basis", "accept_allowed"}
)
_SOURCE_AUTHORITY_KEYS = frozenset(
    {
        "official_status",
        "answer_authority",
        "human_source_reviewed",
        "rights_cleared",
        "no_authority_elevation",
    }
)
_SOURCE_BLOCKER_KEYS = frozenset({"code", "message_zh"})
_CORE_SOURCE_CANDIDATE_KEYS = frozenset(
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
_SOURCE_BINDING_STATES = frozenset(
    {
        "exact_content_set_candidate",
        "blocked_missing_source",
        "blocked_ambiguous",
        "blocked_hash_mismatch",
    }
)
_SOURCE_BLOCKER_MESSAGES_ZH = {
    "central_catalog_source_missing": "中央 catalog 尚无该来源版本记录。",
    "central_corpus_source_missing": "中央 corpus 尚无该来源版本记录。",
    "human_source_review_pending": "来源版本仍待真人教师复核。",
    "school_name_article_title_only": "学校名仅来自公众号标题，卷面未直接显示。",
    "authority_nonofficial": "当前来源为非官方材料，不能提升为官方。",
    "rights_clearance_pending": "材料使用权边界仍待确认。",
    "answer_material_absent": "当前来源版本没有答案材料。",
    "source_version_crosswalk_row_missing": "该卷尚无冻结来源版本交叉记录。",
    "source_binding_action_delegated_to_canonical_task": (
        "本卷来源动作仅允许在唯一的卷级主复核任务中提交；本任务只读展示。"
    ),
}

_BROWSER_RELEASE_FIELDS = frozenset(
    {"idempotency_key", "expected_revision", "reason_zh"}
)
_BROWSER_CHANGE_SET_FIELDS_V1 = frozenset(
    {
        "expected_revision",
        "idempotency_key",
        "base_task_input_sha256",
        "tag_replacements",
        "hierarchy_replacements",
        "atomic_boundary_candidates",
        "dependency_replacements",
    }
)
_BROWSER_CHANGE_SET_FIELDS_V2 = frozenset(
    {*_BROWSER_CHANGE_SET_FIELDS_V1, "source_binding_candidates"}
)
_BROWSER_TAG_FIELDS = frozenset(
    {"atomic_part_id", "changes", "reason_zh", "evidence_ids"}
)
_BROWSER_HIERARCHY_FIELDS = frozenset(
    {"printed_question_id", "proposed_theme_id", "reason_zh", "evidence_ids"}
)
_BROWSER_BOUNDARY_FIELDS = frozenset(
    {
        "printed_question_id",
        "operation",
        "candidate_atomic_parts",
        "reason_zh",
        "evidence_ids",
    }
)
_BROWSER_BOUNDARY_CHILD_FIELDS = frozenset(
    {"client_key", "source_order", "response_requirement_zh"}
)
_BROWSER_DEPENDENCY_FIELDS = frozenset(
    {
        "dependent_atomic_part_id",
        "prior_atomic_part_ids",
        "relationship_kinds",
        "evidence_ids",
        "reason_zh",
    }
)
_BROWSER_SOURCE_BINDING_FIELDS = frozenset(
    {
        "candidate_id",
        "candidate_sha256",
        "source_version_id",
        "action",
        "reason_zh",
        "evidence_ids",
    }
)
_BROWSER_DECISION_FIELDS = frozenset(
    {
        "expected_revision",
        "idempotency_key",
        "change_set_id",
        "verdict",
        "reason_zh",
        "evidence_ids",
    }
)


class ThemeReviewGatewayError(RuntimeError):
    """Stable API-facing failure for the candidate theme-review slice."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact_keys(value: Any, expected: frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ThemeReviewGatewayError(
            "theme_review_contract_invalid", f"{name} 必须是对象", 400
        )
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise ThemeReviewGatewayError(
            "theme_review_contract_invalid",
            f"{name} 字段不完整；missing={missing}, extra={extra}",
            400,
        )
    return value


def _assert_sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise ThemeReviewGatewayError(
            "theme_review_catalog_invalid", f"{name} 不是有效 SHA-256", 409
        )
    return value


def _assert_projection_safe(value: Any, path: str = "value") -> None:
    """Reject rather than redact any path/URL or hidden authority envelope."""

    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ThemeReviewGatewayError(
                    "theme_review_projection_unsafe",
                    f"{path} 包含非字符串键",
                    409,
                )
            lowered = key.casefold()
            if (
                lowered in _FORBIDDEN_OUTPUT_KEYS
                or lowered.endswith(("_path", "_url", "_uri"))
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_projection_unsafe",
                    f"{path}.{key} 不得包含路径或 URL",
                    409,
                )
            _assert_projection_safe(child, f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _assert_projection_safe(child, f"{path}[{index}]")
    elif type(value) is str:
        if _HOST_OR_URL_RE.search(value) or value.startswith(("/", "\\")):
            raise ThemeReviewGatewayError(
                "theme_review_projection_unsafe",
                f"{path} 不得包含主机路径或 URL",
                409,
            )
    elif value is None or type(value) in (bool, int, float):
        return
    else:
        raise ThemeReviewGatewayError(
            "theme_review_projection_unsafe", f"{path} 含非 JSON 值", 409
        )


def _jsonl_rows(raw: bytes, label: str) -> list[tuple[dict[str, Any], bytes]]:
    rows: list[tuple[dict[str, Any], bytes]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = parse_json_object(line)
        except ThemeReviewError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_source_invalid",
                f"{label} 第 {line_number} 行不是严格 JSON 对象",
                409,
            ) from exc
        rows.append((row, line))
    return rows


def _record_index(
    rows: list[tuple[dict[str, Any], bytes]], id_field: str, label: str
) -> dict[str, tuple[dict[str, Any], bytes]]:
    result: dict[str, tuple[dict[str, Any], bytes]] = {}
    for row, raw in rows:
        node_id = row.get(id_field)
        try:
            node_id = validate_identifier(node_id, f"{label} ID")
        except ThemeReviewError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_source_invalid", f"{label} 含无效 ID", 409
            ) from exc
        if node_id in result:
            raise ThemeReviewGatewayError(
                "theme_review_source_invalid", f"{label} 含重复 ID", 409
            )
        result[node_id] = (row, raw)
    return result


def _evidence_value(value: Any) -> Any:
    if type(value) is dict:
        explicit = value.get("value")
        if explicit is not None:
            return explicit
        for key in ("values", "candidate_values", "candidate_labels"):
            candidates = value.get(key)
            if type(candidates) is list and candidates:
                return candidates
        if "value" in value:
            return None
    return value


def _candidate_values(value: Any) -> list[str]:
    value = _evidence_value(value)
    if type(value) is str:
        return [value]
    if type(value) is list:
        return [item for item in value if type(item) is str]
    if type(value) is dict:
        declared = value.get("declared_prelabel")
        if type(declared) is str:
            return [declared]
    return []


def _current_tag_values(row: Mapping[str, Any]) -> dict[str, Any]:
    item_types = [
        item
        for item in _candidate_values(row.get("item_type"))
        if item in ITEM_TYPES
    ]
    primary = _candidate_values(row.get("primary_knowledge_K"))
    response = _candidate_values(row.get("response_R_evidence"))
    if not response:
        response = _candidate_values(row.get("response_R"))
    representation = _candidate_values(row.get("representation_RP_evidence"))
    if not representation:
        representation = _candidate_values(row.get("representation_RP"))
    difficulty = _candidate_values(row.get("difficulty"))
    return {
        "item_type": item_types[0] if item_types else None,
        "selection_rule": (
            row.get("selection_rule")
            if type(row.get("selection_rule")) is str
            else None
        ),
        "primary_knowledge_K": primary[0] if primary else None,
        "supporting_knowledge_K": _candidate_values(
            row.get("supporting_knowledge_K")
        ),
        "ability_A": _candidate_values(row.get("ability_A")),
        "context_C": _candidate_values(row.get("context_C")),
        "response_R": response[0] if len(response) == 1 else response,
        "representation_RP": representation,
        "cognitive_prelabel": (
            difficulty[0]
            if difficulty and re.fullmatch(r"D[1-5]", difficulty[0])
            else None
        ),
        "difficulty_factors": None,
    }


def _known_int(value: Any) -> int | None:
    value = _evidence_value(value)
    return value if type(value) is int and type(value) is not bool else None


def _known_text(value: Any) -> str | None:
    value = _evidence_value(value)
    return value.strip() if type(value) is str and value.strip() else None


def _source_hashes(value: Any) -> set[str]:
    """Collect only declared content hashes; paths and URLs are never copied."""

    output: set[str] = set()

    def visit(node: Any, key: str | None = None) -> None:
        if type(node) is dict:
            for child_key, child in node.items():
                visit(child, str(child_key))
        elif type(node) is list:
            for child in node:
                visit(child, key)
        elif (
            type(node) is str
            and _SHA_RE.fullmatch(node)
            and type(key) is str
            and (
                key in {
                    "source_sha256",
                    "original_source_sha256",
                    "crop_sha256",
                    "page_sha256",
                    "render_sha256",
                    "evidence_binding_sha256",
                }
                or key.endswith("_source_sha256")
            )
        ):
            output.add(node)

    visit(value)
    return output


def _record_binding(
    role: str,
    node_type: str,
    node_id: str,
    raw: bytes,
) -> dict[str, Any]:
    digest = _sha256(raw)
    return {
        "evidence_binding_id": f"EVREC-{digest}",
        "role": role,
        "node_type": node_type,
        "node_id": node_id,
        "sha256": digest,
        "size_bytes": len(raw),
    }


def _hash_binding(digest: str) -> dict[str, Any]:
    return {
        "evidence_binding_id": f"EVSOURCE-{digest}",
        "role": "source_content_hash",
        "node_type": None,
        "node_id": None,
        "sha256": digest,
        "size_bytes": None,
    }


def _control_binding(role: str, raw: bytes) -> dict[str, Any]:
    digest = _sha256(raw)
    return {
        "evidence_binding_id": f"EVCTRL-{digest}",
        "role": role,
        "node_type": None,
        "node_id": None,
        "sha256": digest,
        "size_bytes": len(raw),
    }


def _crosswalk_binding(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json_bytes(dict(row))
    digest = _sha256(raw)
    return {
        "evidence_binding_id": f"EVCROSS-{digest}",
        "role": "exact_crosswalk_parent_candidate",
        "node_type": "atomic_part",
        "node_id": row["master"]["endpoint_id"],
        "sha256": digest,
        "size_bytes": len(raw),
    }


def _deduplicate_bindings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["evidence_binding_id"]
        if key in indexed and indexed[key] != row:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid",
                "证据绑定 ID 发生内容冲突",
                409,
            )
        indexed[key] = row
    return [indexed[key] for key in sorted(indexed)]


def _source_artifact_error(message: str) -> ThemeReviewGatewayError:
    return ThemeReviewGatewayError(
        "theme_review_source_binding_artifact_invalid", message, 409
    )


def _source_canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _source_artifact_error("来源 artifact 含非规范 JSON 值") from exc


def _source_exact_keys(
    value: Any, expected: frozenset[str], label: str
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise _source_artifact_error(f"{label} 字段或类型漂移")
    return value


def _source_identifier(value: Any, label: str) -> str:
    try:
        return validate_identifier(value, label)
    except ThemeReviewError as exc:
        raise _source_artifact_error(f"{label} 无效") from exc


def _source_sha(value: Any, label: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise _source_artifact_error(f"{label} 不是有效 SHA-256")
    return value


def _source_artifact_binding(
    role: str,
    raw: bytes,
    *,
    paper_id: str | None = None,
) -> dict[str, Any]:
    digest = _sha256(raw)
    return {
        "evidence_binding_id": f"EVSOURCEBIND-{digest}",
        "role": role,
        "node_type": "paper" if paper_id is not None else None,
        "node_id": paper_id,
        "sha256": digest,
        "size_bytes": len(raw),
    }


def _validate_source_crosswalk_record(
    record: Any,
    *,
    paper: Mapping[str, Any],
    paper_raw: bytes,
) -> dict[str, Any]:
    record_keys = frozenset(
        {
            "binding_id",
            "master_paper_id",
            "master_source_id",
            "master_package_id",
            "master_paper_record_sha256",
            "source_id",
            "source_version_id",
            "manifest_path",
            "manifest_sha256",
            "manifest_bytes",
            "candidate_manifest_path",
            "candidate_manifest_sha256",
            "candidate_manifest_bytes",
            "central_manifest_path",
            "central_manifest_sha256",
            "central_manifest_bytes",
            "catalog_row_sha256",
            "corpus_row_sha256",
            "page_hash_set_sha256",
            "page_count",
            "content_set_anchor",
            "candidate_content_set_anchor",
            "matching_method",
            "binding_state",
            "accept_allowed",
            "accept_scope",
            "identity_basis",
            "unresolved_reasons",
            "authority",
        }
    )
    row = _source_exact_keys(record, record_keys, "source crosswalk record")
    paper_id = _source_identifier(row["master_paper_id"], "master_paper_id")
    if paper_id != paper.get("paper_id"):
        raise _source_artifact_error("来源记录与 Master paper 不一致")
    if (
        row["master_source_id"] != paper.get("source_id")
        or row["master_package_id"] != paper.get("package_id")
        or row["master_paper_record_sha256"]
        != _sha256(_source_canonical_json_bytes(dict(paper)))
    ):
        raise _source_artifact_error("来源记录未绑定当前 Master paper 规范行")
    # The public JSONL line itself is frozen independently as task evidence.
    if parse_json_object(paper_raw) != dict(paper):
        raise _source_artifact_error("Master paper 原始行与解析结果不一致")

    if row["authority"] != _SOURCE_ARTIFACT_AUTHORITY:
        raise _source_artifact_error("来源记录尝试提升机器候选权限")
    binding_state = row["binding_state"]
    if binding_state not in {"exact_content_set_candidate", "blocked_missing_source"}:
        raise _source_artifact_error("来源记录 binding_state 漂移")
    if type(row["accept_allowed"]) is not bool or row["accept_allowed"] != (
        binding_state == "exact_content_set_candidate"
    ):
        raise _source_artifact_error("来源记录 accept_allowed 与状态冲突")
    if row["accept_scope"] != (
        "source_version_candidate_binding_only_no_authority_elevation"
    ):
        raise _source_artifact_error("来源候选接受范围漂移")

    for key in (
        "manifest_sha256",
        "candidate_manifest_sha256",
        "page_hash_set_sha256",
    ):
        _source_sha(row[key], key)
    for key in ("manifest_bytes", "candidate_manifest_bytes", "page_count"):
        if type(row[key]) is not int or type(row[key]) is bool or row[key] < 1:
            raise _source_artifact_error(f"{key} 无效")
    for sha_key, bytes_key in (
        ("central_manifest_sha256", "central_manifest_bytes"),
        ("catalog_row_sha256", None),
        ("corpus_row_sha256", None),
    ):
        value = row[sha_key]
        if value is not None:
            _source_sha(value, sha_key)
        if bytes_key is not None:
            byte_value = row[bytes_key]
            if byte_value is not None and (
                type(byte_value) is not int
                or type(byte_value) is bool
                or byte_value < 1
            ):
                raise _source_artifact_error(f"{bytes_key} 无效")

    anchor_keys = frozenset(
        {
            "path",
            "format",
            "collection_pointer",
            "hash_field",
            "role_field",
            "included_roles",
            "page_count",
            "page_hash_set_sha256",
        }
    )
    anchors: list[dict[str, Any]] = []
    for name in ("content_set_anchor", "candidate_content_set_anchor"):
        anchor = _source_exact_keys(row[name], anchor_keys, name)
        if (
            type(anchor["path"]) is not str
            or not anchor["path"]
            or _HOST_OR_URL_RE.search(anchor["path"])
            or anchor["path"].startswith(("/", "\\"))
            or ".." in Path(anchor["path"]).parts
            or anchor["format"] not in {"json", "jsonl"}
            or anchor["collection_pointer"] not in {"/", "/files", "/source_pages"}
            or anchor["hash_field"] not in {"sha256", "slice_sha256"}
            or anchor["role_field"] not in {"role", None}
            or type(anchor["included_roles"]) is not list
            or len(anchor["included_roles"]) != len(set(anchor["included_roles"]))
            or type(anchor["page_count"]) is not int
            or type(anchor["page_count"]) is bool
            or anchor["page_count"] < 1
        ):
            raise _source_artifact_error(f"{name} 无效")
        _source_sha(anchor["page_hash_set_sha256"], f"{name}.page_hash_set_sha256")
        anchors.append(anchor)
    if any(
        anchor["page_count"] != row["page_count"]
        or anchor["page_hash_set_sha256"] != row["page_hash_set_sha256"]
        for anchor in anchors
    ):
        raise _source_artifact_error("来源版本页面哈希集合锚不一致")

    identity_keys = frozenset(
        {"academic_year", "semester", "grade", "exam_type", "school", "boundary_zh"}
    )
    identity_field_keys = frozenset({"value", "basis"})
    identity = _source_exact_keys(row["identity_basis"], identity_keys, "identity_basis")
    master_identity = paper.get("source_identity")
    if type(master_identity) is not dict:
        raise _source_artifact_error("Master paper 缺少来源身份")
    for field in ("academic_year", "semester", "grade", "exam_type", "school"):
        claim = _source_exact_keys(
            identity[field], identity_field_keys, f"identity_basis.{field}"
        )
        if claim["basis"] not in {
            "paper_face_direct",
            "article_title_only_not_visible_on_paper_face",
            "unknown",
        }:
            raise _source_artifact_error(f"identity_basis.{field}.basis 无效")
        if field == "exam_type":
            observed = master_identity.get("exam_type") or master_identity.get(
                "paper_face_exam_type"
            )
        elif field == "school" and claim["basis"] == (
            "article_title_only_not_visible_on_paper_face"
        ):
            observed = master_identity.get(
                "school_article_title_attribution",
                master_identity.get("school_attribution"),
            )
        else:
            observed = master_identity.get(field)
        if claim["value"] != observed:
            raise _source_artifact_error(f"identity_basis.{field} 与 Master 不一致")
    if type(identity["boundary_zh"]) is not str or len(identity["boundary_zh"].strip()) < 10:
        raise _source_artifact_error("identity_basis.boundary_zh 无效")

    unresolved = row["unresolved_reasons"]
    if (
        type(unresolved) is not list
        or not unresolved
        or len(unresolved) != len(set(unresolved))
        or any(item not in _SOURCE_BLOCKER_MESSAGES_ZH for item in unresolved)
    ):
        raise _source_artifact_error("unresolved_reasons 无效")

    version_subject = {
        "schema_version": "1.0.0-source-version-subject",
        "manifest_sha256": row["manifest_sha256"],
        "manifest_bytes": row["manifest_bytes"],
        "page_hash_set_sha256": row["page_hash_set_sha256"],
        "page_count": row["page_count"],
        "source_id": row["source_id"],
    }
    expected_version = f"SHCHEM-SV-{_sha256(_source_canonical_json_bytes(version_subject))}"
    if row["source_version_id"] != expected_version:
        raise _source_artifact_error("source_version_id 未绑定规范来源版本主题")
    binding_subject = {
        "schema_version": "1.0.0-source-version-binding-subject",
        "master_paper_id": paper_id,
        "master_source_id": row["master_source_id"],
        "source_id": row["source_id"],
        "source_version_id": row["source_version_id"],
        "binding_state": binding_state,
    }
    expected_binding = f"SHCHEM-SVB-{_sha256(_source_canonical_json_bytes(binding_subject))}"
    if row["binding_id"] != expected_binding:
        raise _source_artifact_error("binding_id 未绑定规范来源关联主题")

    exact = binding_state == "exact_content_set_candidate"
    if exact:
        if (
            paper_id != _HUAER_PAPER_ID
            or type(row["source_id"]) is not str
            or not row["source_id"].startswith("file-")
            or row["central_manifest_path"] is None
            or row["central_manifest_sha256"] is None
            or row["central_manifest_bytes"] is None
            or row["catalog_row_sha256"] is None
            or row["corpus_row_sha256"] is None
            or row["matching_method"]
            != "exact_page_sha256_set_and_exact_wechat_identity"
            or row["page_count"] != 9
        ):
            raise _source_artifact_error("精确内容集候选不满足华二九页闭集")
    else:
        if (
            paper_id not in {_FUDAN_PAPER_ID, _DATONG_PAPER_ID}
            or row["source_id"] is not None
            or row["central_manifest_path"] is not None
            or row["central_manifest_sha256"] is not None
            or row["central_manifest_bytes"] is not None
            or row["catalog_row_sha256"] is not None
            or row["corpus_row_sha256"] is not None
            or row["matching_method"]
            != "candidate_manifest_only_no_central_source_record"
            or not {
                "central_catalog_source_missing",
                "central_corpus_source_missing",
            }
            <= set(unresolved)
        ):
            raise _source_artifact_error("缺失来源候选未保持阻断边界")
    return copy.deepcopy(row)


def _wechat_identity(value: Any) -> tuple[str, str, str, str] | None:
    if type(value) is not str or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if (parsed.hostname or "").casefold() not in {
        "mp.weixin.qq.com",
        "www.mp.weixin.qq.com",
    }:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    parts: list[str] = []
    for key in ("__biz", "mid", "idx", "sn"):
        candidates = query.get(key)
        if not candidates or len(candidates) != 1 or not candidates[0]:
            return None
        parts.append(candidates[0])
    return tuple(parts)  # type: ignore[return-value]


def _source_catalog_rows(raw: bytes) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _source_artifact_error("catalog.csv 不是严格 UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected_header = [
        "record_id",
        "year",
        "region_or_school",
        "paper_type",
        "content_type",
        "title",
        "source_account",
        "source_url",
        "published_at",
        "collected_at",
        "local_path",
        "completeness",
        "answer_status",
        "rubric_status",
        "sha256",
        "verification_status",
        "notes",
    ]
    if reader.fieldnames != expected_header or len(set(reader.fieldnames or [])) != len(
        expected_header
    ):
        raise _source_artifact_error("catalog.csv 表头漂移")
    rows: list[dict[str, str]] = []
    for row in reader:
        if set(row) != set(expected_header) or any(
            key is None or value is None
            for key, value in row.items()
        ):
            raise _source_artifact_error("catalog.csv 行字段漂移")
        rows.append(dict(row))
    if len(rows) != 108:
        raise _source_artifact_error("catalog.csv 计数漂移")
    return rows


def _source_page_hashes(
    raw: bytes,
    anchor: Mapping[str, Any],
) -> list[str]:
    if anchor["format"] == "jsonl":
        rows: Any = [row for row, _ in _jsonl_rows(raw, "source page anchor")]
    else:
        try:
            rows = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _source_artifact_error("来源页面锚不是严格 UTF-8 JSON") from exc
    pointer = anchor["collection_pointer"]
    if pointer != "/":
        current = rows
        for token in pointer.lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if type(current) is not dict or token not in current:
                raise _source_artifact_error("来源页面锚 JSON pointer 缺失")
            current = current[token]
        rows = current
    if type(rows) is not list:
        raise _source_artifact_error("来源页面锚没有解析为数组")
    included_roles = set(anchor["included_roles"])
    hashes: list[str] = []
    for row in rows:
        if type(row) is not dict:
            raise _source_artifact_error("来源页面锚包含非对象行")
        if included_roles and row.get(anchor["role_field"]) not in included_roles:
            continue
        digest = row.get(anchor["hash_field"])
        hashes.append(_source_sha(digest, "source page sha256"))
    if len(hashes) != len(set(hashes)):
        raise _source_artifact_error("来源页面哈希集合含重复项")
    return sorted(hashes)


def _verify_source_crosswalk_upstream(
    public_kb: PublicKBReader,
    records: Mapping[str, Mapping[str, Any]],
    paper_index: Mapping[str, tuple[dict[str, Any], bytes]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    cache: dict[str, tuple[bytes, str]] = {}

    def read(relative: str) -> tuple[bytes, str]:
        if relative not in public_kb.SOURCE_VERSION_UPSTREAM_FILES:
            raise _source_artifact_error("来源交叉表引用了未列入闭集的上游文件")
        if relative not in cache:
            try:
                cache[relative] = public_kb._read_exact(relative)
            except ReadOnlyDataError as exc:
                raise _source_artifact_error("来源交叉表上游文件不可用") from exc
        return cache[relative]

    catalog_raw, catalog_sha = read("catalog.csv")
    corpus_raw, corpus_sha = read("kb/corpus_manifest.jsonl")
    catalog_rows = _source_catalog_rows(catalog_raw)
    corpus_rows = [
        row for row, _ in _jsonl_rows(corpus_raw, "source corpus manifest")
    ]
    if len(corpus_rows) != 108:
        raise _source_artifact_error("corpus manifest 计数漂移")

    checks: list[dict[str, Any]] = []
    for paper_id in sorted(records):
        record = records[paper_id]
        paper = paper_index[paper_id][0]
        for path_key, sha_key, bytes_key in (
            ("manifest_path", "manifest_sha256", "manifest_bytes"),
            (
                "candidate_manifest_path",
                "candidate_manifest_sha256",
                "candidate_manifest_bytes",
            ),
        ):
            raw, digest = read(record[path_key])
            if digest != record[sha_key] or len(raw) != record[bytes_key]:
                raise _source_artifact_error("三校来源 manifest 哈希或字节漂移")
        if record["central_manifest_path"] is not None:
            central_raw, central_sha = read(record["central_manifest_path"])
            if (
                central_sha != record["central_manifest_sha256"]
                or len(central_raw) != record["central_manifest_bytes"]
            ):
                raise _source_artifact_error("中央来源 manifest 哈希或字节漂移")

        page_sets: list[list[str]] = []
        for anchor_key in ("content_set_anchor", "candidate_content_set_anchor"):
            anchor = record[anchor_key]
            anchor_raw, _ = read(anchor["path"])
            page_hashes = _source_page_hashes(anchor_raw, anchor)
            if (
                len(page_hashes) != anchor["page_count"]
                or _sha256(_source_canonical_json_bytes(page_hashes))
                != anchor["page_hash_set_sha256"]
            ):
                raise _source_artifact_error("三校页面哈希集合漂移")
            page_sets.append(page_hashes)
        if (
            page_sets[0] != page_sets[1]
            or len(page_sets[0]) != record["page_count"]
            or _sha256(_source_canonical_json_bytes(page_sets[0]))
            != record["page_hash_set_sha256"]
        ):
            raise _source_artifact_error("三校主页面集合与候选页面集合不一致")

        master_identity = _wechat_identity(
            paper.get("source_identity", {}).get("source_url")
            if type(paper.get("source_identity")) is dict
            else None
        )
        if master_identity is None:
            raise _source_artifact_error("Master paper 缺少严格公众号来源身份")
        catalog_matches = [
            row
            for row in catalog_rows
            if _wechat_identity(row.get("source_url")) == master_identity
        ]
        corpus_matches = [
            row
            for row in corpus_rows
            if _wechat_identity(row.get("source_url")) == master_identity
        ]
        if record["binding_state"] == "exact_content_set_candidate":
            if len(catalog_matches) != 1 or len(corpus_matches) != 1:
                raise _source_artifact_error("中央来源公众号身份匹配不唯一")
            catalog_row = catalog_matches[0]
            corpus_row = corpus_matches[0]
            if (
                catalog_row.get("record_id") != record["source_id"]
                or corpus_row.get("source_id") != record["source_id"]
                or _sha256(_source_canonical_json_bytes(catalog_row))
                != record["catalog_row_sha256"]
                or _sha256(_source_canonical_json_bytes(corpus_row))
                != record["corpus_row_sha256"]
            ):
                raise _source_artifact_error("中央 catalog/corpus 来源行漂移")
            central_manifest_raw, _ = read(record["central_manifest_path"])
            try:
                central_manifest = parse_json_object(central_manifest_raw)
            except ThemeReviewError as exc:
                raise _source_artifact_error("中央来源 manifest 不是严格 JSON") from exc
            catalog_local = Path(
                catalog_row["local_path"].replace("\\", "/")
            ).as_posix()
            if catalog_local.startswith("sh-chem-db/"):
                catalog_local = catalog_local[len("sh-chem-db/") :]
            expected_manifest = (
                Path(catalog_local) / "manifest.json"
            ).as_posix()
            if (
                central_manifest.get("record_id") != record["source_id"]
                or expected_manifest
                != Path(record["central_manifest_path"]).as_posix()
            ):
                raise _source_artifact_error("中央来源 manifest 与 catalog 路径绑定漂移")
        elif catalog_matches or corpus_matches:
            raise _source_artifact_error("阻断来源意外出现中央同身份记录")

        checks.append(
            {
                "paper_id": paper_id,
                "source_version_id": record["source_version_id"],
                "binding_state": record["binding_state"],
                "accept_allowed": record["accept_allowed"],
                "page_count": record["page_count"],
                "page_hash_set_sha256": record["page_hash_set_sha256"],
                "manifest_sha256": record["manifest_sha256"],
                "candidate_manifest_sha256": record[
                    "candidate_manifest_sha256"
                ],
                "central_manifest_sha256": record["central_manifest_sha256"],
                "catalog_match_count": len(catalog_matches),
                "corpus_match_count": len(corpus_matches),
                "verified": True,
            }
        )

    file_bindings = [
        {
            "file_id": f"SRCUP-{index:02d}",
            "sha256": digest,
            "size_bytes": len(raw),
        }
        for index, (_, (raw, digest)) in enumerate(sorted(cache.items()), 1)
    ]
    receipt: dict[str, Any] = {
        "schema_version": "shchem_gateway_source_upstream_verification_v1",
        "verified": True,
        "catalog_sha256": catalog_sha,
        "catalog_size_bytes": len(catalog_raw),
        "catalog_row_count": len(catalog_rows),
        "corpus_sha256": corpus_sha,
        "corpus_size_bytes": len(corpus_raw),
        "corpus_row_count": len(corpus_rows),
        "paper_check_count": len(checks),
        "paper_checks": checks,
        "upstream_file_count": len(file_bindings),
        "upstream_file_bindings": file_bindings,
        "receipt_sha256": None,
    }
    receipt["receipt_sha256"] = _sha256(_source_canonical_json_bytes(receipt))
    return receipt, {relative: raw for relative, (raw, _) in cache.items()}


def _load_source_crosswalk(
    public_kb: PublicKBReader,
    paper_index: Mapping[str, tuple[dict[str, Any], bytes]],
) -> dict[str, Any]:
    try:
        manifest_raw, manifest_sha = public_kb._read_exact(
            public_kb.SOURCE_VERSION_CROSSWALK_MANIFEST
        )
        manifest = parse_json_object(manifest_raw)
    except (ReadOnlyDataError, ThemeReviewError) as exc:
        raise _source_artifact_error("来源版本 artifact manifest 不可用") from exc
    manifest_keys = frozenset(
        {
            "schema_version",
            "manifest_id",
            "artifact_id",
            "file_hash_algorithm",
            "self_hash_algorithm",
            "files",
            "authority",
            "self_sha256",
        }
    )
    _source_exact_keys(manifest, manifest_keys, "source crosswalk manifest")
    if (
        manifest["schema_version"] != _SOURCE_CROSSWALK_MANIFEST_SCHEMA_VERSION
        or manifest["artifact_id"] != _SOURCE_CROSSWALK_ARTIFACT_ID
        or manifest["authority"] != _SOURCE_ARTIFACT_MANIFEST_AUTHORITY
        or manifest["file_hash_algorithm"] != "sha256-of-raw-bytes-v1"
        or manifest["self_hash_algorithm"]
        != "canonical-json-sha256-with-self-sha256-null-v1"
    ):
        raise _source_artifact_error("来源版本 artifact manifest 头部漂移")
    manifest_subject = copy.deepcopy(manifest)
    manifest_subject["self_sha256"] = None
    if manifest["self_sha256"] != _sha256(
        _source_canonical_json_bytes(manifest_subject)
    ):
        raise _source_artifact_error("来源版本 artifact manifest 自哈希失败")
    files = manifest["files"]
    if type(files) is not list:
        raise _source_artifact_error("来源版本 artifact files 无效")
    file_keys = frozenset({"path", "role", "sha256", "bytes"})
    prefix = f"{public_kb.SOURCE_VERSION_CROSSWALK_ROOT}/"
    expected_paths = {
        relative.removeprefix(prefix)
        for relative in public_kb.SOURCE_VERSION_CROSSWALK_FILES
    }
    by_path: dict[str, dict[str, Any]] = {}
    raw_by_path: dict[str, bytes] = {}
    sha_by_path: dict[str, str] = {}
    for item in files:
        row = _source_exact_keys(item, file_keys, "source artifact file")
        relative_name = row["path"]
        if (
            type(relative_name) is not str
            or relative_name not in expected_paths
            or relative_name in by_path
            or type(row["role"]) is not str
            or not row["role"]
            or type(row["bytes"]) is not int
            or type(row["bytes"]) is bool
            or row["bytes"] < 0
        ):
            raise _source_artifact_error("来源版本 artifact 文件闭集漂移")
        _source_sha(row["sha256"], f"artifact file {relative_name}")
        full_relative = f"{prefix}{relative_name}"
        try:
            raw, digest = public_kb._read_exact(full_relative)
        except ReadOnlyDataError as exc:
            raise _source_artifact_error("来源版本 artifact 文件不可用") from exc
        if digest != row["sha256"] or len(raw) != row["bytes"]:
            raise _source_artifact_error("来源版本 artifact 文件哈希或字节漂移")
        by_path[relative_name] = row
        raw_by_path[relative_name] = raw
        sha_by_path[relative_name] = digest
    if set(by_path) != expected_paths:
        raise _source_artifact_error("来源版本 artifact 文件闭集不完整")

    data_name = public_kb.SOURCE_VERSION_CROSSWALK_DATA.removeprefix(prefix)
    schema_name = public_kb.SOURCE_VERSION_CROSSWALK_SCHEMA.removeprefix(prefix)
    try:
        payload = parse_json_object(raw_by_path[data_name])
        schema = parse_json_object(raw_by_path[schema_name])
    except ThemeReviewError as exc:
        raise _source_artifact_error("来源版本交叉表或 schema 不是严格 JSON") from exc
    payload_keys = frozenset(
        {
            "schema_version",
            "artifact_id",
            "artifact_state",
            "created_date",
            "record_count",
            "hash_algorithms",
            "authority",
            "records",
        }
    )
    _source_exact_keys(payload, payload_keys, "source crosswalk")
    if (
        payload["schema_version"] != _SOURCE_CROSSWALK_SCHEMA_VERSION
        or payload["artifact_id"] != _SOURCE_CROSSWALK_ARTIFACT_ID
        or payload["artifact_state"] != "machine_candidate_only"
        or payload["record_count"] != 3
        or payload["authority"] != _SOURCE_ARTIFACT_AUTHORITY
        or payload["hash_algorithms"]
        != {
            "file": "sha256-of-raw-bytes-v1",
            "canonical_row": "sha256-of-canonical-json-sorted-keys-utf8-v1",
            "page_hash_set": "sha256-of-canonical-json-sorted-unique-page-sha256-list-v1",
            "source_version": "canonical-source-version-subject-sha256-v1",
            "binding": "canonical-source-version-binding-subject-sha256-v1",
        }
        or type(payload["records"]) is not list
        or len(payload["records"]) != 3
    ):
        raise _source_artifact_error("来源版本交叉表头部、权限或计数漂移")
    if (
        schema.get("$id")
        != "https://sh-chem.local/schemas/source-version-crosswalk-v1.json"
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
    ):
        raise _source_artifact_error("来源版本交叉表 schema 身份漂移")

    records: dict[str, dict[str, Any]] = {}
    record_raw: dict[str, bytes] = {}
    for unvalidated in payload["records"]:
        paper_id = unvalidated.get("master_paper_id") if type(unvalidated) is dict else None
        pair = paper_index.get(paper_id)
        if pair is None or paper_id in records:
            raise _source_artifact_error("来源版本交叉表 paper 范围重复或越界")
        validated = _validate_source_crosswalk_record(
            unvalidated,
            paper=pair[0],
            paper_raw=pair[1],
        )
        records[paper_id] = validated
        record_raw[paper_id] = _source_canonical_json_bytes(validated)
    if set(records) != _SOURCE_CROSSWALK_PAPERS:
        raise _source_artifact_error("来源版本交叉表三校范围漂移")
    exact = [row for row in records.values() if row["accept_allowed"]]
    if len(exact) != 1 or exact[0]["master_paper_id"] != _HUAER_PAPER_ID:
        raise _source_artifact_error("只有华二九页内容集可形成精确候选")
    upstream_receipt, _ = _verify_source_crosswalk_upstream(
        public_kb,
        records,
        paper_index,
    )
    upstream_receipt_raw = _source_canonical_json_bytes(upstream_receipt)

    controls = [
        _source_artifact_binding("source_version_crosswalk_manifest", manifest_raw),
        _source_artifact_binding("source_version_crosswalk_data", raw_by_path[data_name]),
        _source_artifact_binding("source_version_crosswalk_schema", raw_by_path[schema_name]),
        _source_artifact_binding(
            "source_version_upstream_verification_receipt",
            upstream_receipt_raw,
        ),
    ]
    return {
        "records": records,
        "record_raw": record_raw,
        "controls": controls,
        "snapshot": {
            "manifest_sha256": manifest_sha,
            "manifest_size_bytes": len(manifest_raw),
            "manifest_self_sha256": manifest["self_sha256"],
            "data_sha256": sha_by_path[data_name],
            "data_size_bytes": len(raw_by_path[data_name]),
            "schema_sha256": sha_by_path[schema_name],
            "schema_size_bytes": len(raw_by_path[schema_name]),
            "artifact_file_sha256": dict(sorted(sha_by_path.items())),
            "record_count": len(records),
            "upstream_verification_receipt": upstream_receipt,
        },
    }


def _paper_source_identity(paper: Mapping[str, Any]) -> dict[str, Any]:
    value = paper.get("source_identity")
    return copy.deepcopy(value) if type(value) is dict else {}


def _safe_source_text(value: Any) -> str | None:
    if type(value) is not str or not value.strip():
        return None
    text = value.strip()
    if _HOST_OR_URL_RE.search(text) or text.startswith(("/", "\\")):
        return None
    return text


def _build_source_binding_projection(
    *,
    paper: Mapping[str, Any],
    crosswalk_record: Mapping[str, Any] | None,
    evidence_ids: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paper_id = paper["paper_id"]
    identity = _paper_source_identity(paper)
    school_status = str(identity.get("school_attribution_status", "")).upper()
    school_direct = school_status == "PAPER_FACE_DIRECT"
    school_title_only = "TITLE" in school_status or school_status == (
        "TITLE_ATTRIBUTION_ONLY"
    )

    if crosswalk_record is not None:
        identity_basis = crosswalk_record["identity_basis"]
        field_values = {
            key: identity_basis[key]["value"]
            for key in ("academic_year", "semester", "grade", "exam_type")
        }
        school_claim = identity_basis["school"]
        paper_face_school = (
            school_claim["value"]
            if school_claim["basis"] == "paper_face_direct"
            else None
        )
        identity_status = (
            "paper_face_direct"
            if school_claim["basis"] == "paper_face_direct"
            else "paper_face_direct_except_school"
        )
        identity_boundary = identity_basis["boundary_zh"]
    else:
        field_values = {
            "academic_year": _safe_source_text(identity.get("academic_year")),
            "semester": _safe_source_text(identity.get("semester")),
            "grade": _safe_source_text(identity.get("grade") or paper.get("grade")),
            "exam_type": _safe_source_text(
                identity.get("exam_type") or identity.get("paper_face_exam_type")
            ),
        }
        paper_face_school = (
            _safe_source_text(identity.get("school")) if school_direct else None
        )
        identity_status = "master_candidate_without_frozen_crosswalk"
        identity_boundary = (
            "这些字段仅来自 Master 机器候选记录；当前没有冻结来源版本交叉记录，"
            "不能据此确认来源版本或提升权威。"
        )

    article_school = None
    article_status = "article_metadata_only"
    if school_title_only:
        article_school = _safe_source_text(
            identity.get(
                "school_article_title_attribution",
                identity.get("school_attribution"),
            )
        )
        article_status = "title_attribution_only"
        article_boundary = "学校名仅来自公众号标题归属，不等同于卷面直接身份。"
    elif not identity.get("article_title") and not identity.get("source_account"):
        article_status = "unknown"
        article_boundary = "当前 Master 记录没有可展示的公众号标题归属信息。"
    else:
        article_boundary = "公众号标题和账号仅作来源归属，不能替代卷面身份。"

    blockers = (
        list(crosswalk_record["unresolved_reasons"])
        if crosswalk_record is not None
        else [
            "source_version_crosswalk_row_missing",
            "human_source_review_pending",
            "authority_nonofficial",
            "rights_clearance_pending",
        ]
    )
    if school_title_only and "school_name_article_title_only" not in blockers:
        blockers.append("school_name_article_title_only")
    blockers = sorted(set(blockers))

    if crosswalk_record is None:
        source_version_id = None
        source_version_sha256 = None
        catalog_status = "blocked"
        catalog_source_id = None
        match_basis = "no_frozen_crosswalk_candidate"
        accept_allowed = False
        source_candidates: list[dict[str, Any]] = []
    else:
        source_version_id = crosswalk_record["source_version_id"]
        source_version_sha256 = source_version_id.removeprefix("SHCHEM-SV-")
        catalog_source_id = crosswalk_record["source_id"]
        match_basis = crosswalk_record["matching_method"]
        accept_allowed = crosswalk_record["accept_allowed"]
        catalog_status = "partial" if accept_allowed else "blocked"
        candidate_subject = {
            "candidate_id": crosswalk_record["binding_id"],
            "source_id": catalog_source_id,
            "source_version_id": source_version_id,
            "binding_state": crosswalk_record["binding_state"],
            "accept_allowed": accept_allowed,
            "evidence_binding_ids": list(evidence_ids),
        }
        source_candidates = [
            {
                **candidate_subject,
                "candidate_sha256": _sha256(
                    canonical_json_bytes(candidate_subject)
                ),
            }
        ]

    official_status = _safe_source_text(identity.get("official_status"))
    if official_status not in {"nonofficial", "unknown"}:
        official_status = "unverified_source_status"
    answer_authority = _safe_source_text(identity.get("answer_authority"))
    if answer_authority is None or "official" in answer_authority.casefold():
        answer_authority = "unknown_or_unverified"
    gates = paper.get("gates") if type(paper.get("gates")) is dict else {}
    source_binding = {
        "schema_version": _SOURCE_BINDING_SCHEMA_VERSION,
        "paper_id": paper_id,
        "master_source_id": _safe_source_text(paper.get("source_id")),
        "source_version_id": source_version_id,
        "source_version_sha256": source_version_sha256,
        "source_layer": _safe_source_text(paper.get("source_layer")),
        "package_id": _safe_source_text(paper.get("package_id")),
        "paper_face": {
            "identity_status": identity_status,
            "school_zh": paper_face_school,
            "academic_year_zh": field_values["academic_year"],
            "semester_zh": field_values["semester"],
            "grade_zh": field_values["grade"],
            "exam_type_zh": field_values["exam_type"],
            "boundary_zh": identity_boundary,
        },
        "article_attribution": {
            "status": article_status,
            "school_zh": article_school,
            "article_title_zh": _safe_source_text(identity.get("article_title")),
            "source_account_zh": _safe_source_text(identity.get("source_account")),
            "boundary_zh": article_boundary,
        },
        "catalog_binding": {
            "status": catalog_status,
            "source_id": catalog_source_id,
            "match_basis": match_basis,
            "accept_allowed": accept_allowed,
        },
        "authority": {
            "official_status": official_status or "unknown",
            "answer_authority": answer_authority,
            "human_source_reviewed": False,
            "rights_cleared": gates.get("rights_cleared") is True,
            "no_authority_elevation": True,
        },
        "blockers": [
            {"code": code, "message_zh": _SOURCE_BLOCKER_MESSAGES_ZH[code]}
            for code in blockers
        ],
        "evidence_ids": list(evidence_ids),
    }
    return source_binding, source_candidates


def _validate_upstream_verification_receipt(value: Any) -> None:
    keys = frozenset(
        {
            "schema_version",
            "verified",
            "catalog_sha256",
            "catalog_size_bytes",
            "catalog_row_count",
            "corpus_sha256",
            "corpus_size_bytes",
            "corpus_row_count",
            "paper_check_count",
            "paper_checks",
            "upstream_file_count",
            "upstream_file_bindings",
            "receipt_sha256",
        }
    )
    receipt = _source_exact_keys(value, keys, "upstream verification receipt")
    if (
        receipt["schema_version"]
        != "shchem_gateway_source_upstream_verification_v1"
        or receipt["verified"] is not True
        or receipt["catalog_row_count"] != 108
        or receipt["corpus_row_count"] != 108
        or receipt["paper_check_count"] != 3
        or receipt["upstream_file_count"] != len(
            PublicKBReader.SOURCE_VERSION_UPSTREAM_FILES
        )
    ):
        raise _source_artifact_error("上游复算回执状态或计数漂移")
    for key in ("catalog_sha256", "corpus_sha256", "receipt_sha256"):
        _source_sha(receipt[key], f"upstream receipt {key}")
    for key in ("catalog_size_bytes", "corpus_size_bytes"):
        if type(receipt[key]) is not int or type(receipt[key]) is bool or receipt[key] < 1:
            raise _source_artifact_error("上游复算回执字节数无效")
    file_rows = receipt["upstream_file_bindings"]
    file_keys = frozenset({"file_id", "sha256", "size_bytes"})
    if (
        type(file_rows) is not list
        or len(file_rows) != receipt["upstream_file_count"]
        or len({row.get("file_id") for row in file_rows if type(row) is dict})
        != len(file_rows)
    ):
        raise _source_artifact_error("上游复算文件绑定闭集漂移")
    for index, row in enumerate(file_rows, 1):
        row = _source_exact_keys(row, file_keys, "upstream file binding")
        if (
            row["file_id"] != f"SRCUP-{index:02d}"
            or type(row["size_bytes"]) is not int
            or type(row["size_bytes"]) is bool
            or row["size_bytes"] < 1
        ):
            raise _source_artifact_error("上游复算文件绑定身份漂移")
        _source_sha(row["sha256"], "upstream file binding sha256")
    check_rows = receipt["paper_checks"]
    check_keys = frozenset(
        {
            "paper_id",
            "source_version_id",
            "binding_state",
            "accept_allowed",
            "page_count",
            "page_hash_set_sha256",
            "manifest_sha256",
            "candidate_manifest_sha256",
            "central_manifest_sha256",
            "catalog_match_count",
            "corpus_match_count",
            "verified",
        }
    )
    if type(check_rows) is not list or len(check_rows) != 3:
        raise _source_artifact_error("上游复算三校检查计数漂移")
    papers: set[str] = set()
    accepted = 0
    for row in check_rows:
        row = _source_exact_keys(row, check_keys, "upstream paper check")
        paper_id = _source_identifier(row["paper_id"], "upstream paper_id")
        if (
            paper_id in papers
            or paper_id not in _SOURCE_CROSSWALK_PAPERS
            or row["binding_state"]
            not in {"exact_content_set_candidate", "blocked_missing_source"}
            or type(row["accept_allowed"]) is not bool
            or row["verified"] is not True
            or type(row["page_count"]) is not int
            or type(row["page_count"]) is bool
            or row["page_count"] < 1
            or type(row["catalog_match_count"]) is not int
            or type(row["corpus_match_count"]) is not int
        ):
            raise _source_artifact_error("上游复算三校检查字段漂移")
        papers.add(paper_id)
        accepted += int(row["accept_allowed"])
        for key in (
            "page_hash_set_sha256",
            "manifest_sha256",
            "candidate_manifest_sha256",
        ):
            _source_sha(row[key], f"upstream paper {key}")
        if row["central_manifest_sha256"] is not None:
            _source_sha(
                row["central_manifest_sha256"],
                "upstream central_manifest_sha256",
            )
    if papers != _SOURCE_CROSSWALK_PAPERS or accepted != 1:
        raise _source_artifact_error("上游复算三校范围或接受门漂移")
    subject = copy.deepcopy(receipt)
    subject["receipt_sha256"] = None
    if receipt["receipt_sha256"] != _sha256(
        _source_canonical_json_bytes(subject)
    ):
        raise _source_artifact_error("上游复算回执自哈希失败")
    _assert_projection_safe(receipt, "upstream_verification_receipt")


def _validate_task_source_binding(task: Mapping[str, Any]) -> None:
    binding = _source_exact_keys(
        task.get("source_binding"), _SOURCE_BINDING_KEYS, "task.source_binding"
    )
    if (
        binding["schema_version"] != _SOURCE_BINDING_SCHEMA_VERSION
        or binding["paper_id"] != task["paper_id"]
    ):
        raise _source_artifact_error("任务来源绑定身份漂移")
    _source_exact_keys(binding["paper_face"], _SOURCE_PAPER_FACE_KEYS, "paper_face")
    _source_exact_keys(
        binding["article_attribution"],
        _SOURCE_ARTICLE_ATTRIBUTION_KEYS,
        "article_attribution",
    )
    catalog = _source_exact_keys(
        binding["catalog_binding"],
        _SOURCE_CATALOG_BINDING_KEYS,
        "catalog_binding",
    )
    authority = _source_exact_keys(
        binding["authority"], _SOURCE_AUTHORITY_KEYS, "source authority"
    )
    if (
        catalog["status"] not in {"bound", "partial", "blocked"}
        or type(catalog["accept_allowed"]) is not bool
        or authority["human_source_reviewed"] is not False
        or authority["no_authority_elevation"] is not True
        or authority["rights_cleared"] is not False
    ):
        raise _source_artifact_error("任务来源资格或权限边界漂移")
    task_evidence = {
        row["evidence_binding_id"] for row in task["evidence_bindings"]
    }
    evidence = binding["evidence_ids"]
    if (
        type(evidence) is not list
        or not evidence
        or len(evidence) != len(set(evidence))
        or not set(evidence) <= task_evidence
    ):
        raise _source_artifact_error("任务来源证据未绑定冻结任务")
    blockers = binding["blockers"]
    if type(blockers) is not list or not blockers:
        raise _source_artifact_error("任务来源阻断清单为空")
    blocker_codes: set[str] = set()
    for row in blockers:
        row = _source_exact_keys(row, _SOURCE_BLOCKER_KEYS, "source blocker")
        if (
            row["code"] not in _SOURCE_BLOCKER_MESSAGES_ZH
            or row["message_zh"] != _SOURCE_BLOCKER_MESSAGES_ZH[row["code"]]
            or row["code"] in blocker_codes
        ):
            raise _source_artifact_error("任务来源阻断项漂移或重复")
        blocker_codes.add(row["code"])

    candidates = task.get("source_binding_candidates")
    if type(candidates) is not list or len(candidates) > 1:
        raise _source_artifact_error("任务来源候选数组无效")
    if not candidates:
        version_id = binding["source_version_id"]
        if version_id is None:
            if (
                binding["source_version_sha256"] is not None
                or catalog
                != {
                    "status": "blocked",
                    "source_id": None,
                    "match_basis": "no_frozen_crosswalk_candidate",
                    "accept_allowed": False,
                }
                or "source_version_crosswalk_row_missing" not in blocker_codes
            ):
                raise _source_artifact_error("无交叉记录任务伪造了来源版本")
        elif (
            type(version_id) is not str
            or not version_id.startswith("SHCHEM-SV-")
            or binding["source_version_sha256"]
            != version_id.removeprefix("SHCHEM-SV-")
            or catalog["accept_allowed"] is not False
            or catalog["status"] not in {"partial", "blocked"}
            or catalog["match_basis"] == "no_frozen_crosswalk_candidate"
            or "source_binding_action_delegated_to_canonical_task"
            not in blocker_codes
        ):
            raise _source_artifact_error("同卷非主任务未保持只读来源候选边界")
        return

    candidate = _source_exact_keys(
        candidates[0], _CORE_SOURCE_CANDIDATE_KEYS, "source binding candidate"
    )
    version_id = binding["source_version_id"]
    if (
        type(version_id) is not str
        or not version_id.startswith("SHCHEM-SV-")
        or binding["source_version_sha256"] != version_id.removeprefix("SHCHEM-SV-")
        or candidate["source_version_id"] != version_id
        or candidate["source_id"] != catalog["source_id"]
        or candidate["accept_allowed"] != catalog["accept_allowed"]
        or candidate["binding_state"] not in _SOURCE_BINDING_STATES
        or candidate["evidence_binding_ids"] != evidence
    ):
        raise _source_artifact_error("任务来源候选与来源投影不一致")
    candidate_subject = {
        key: copy.deepcopy(candidate[key])
        for key in (
            "candidate_id",
            "source_id",
            "source_version_id",
            "binding_state",
            "accept_allowed",
            "evidence_binding_ids",
        )
    }
    if candidate["candidate_sha256"] != _sha256(
        canonical_json_bytes(candidate_subject)
    ):
        raise _source_artifact_error("任务来源候选自哈希失败")
    if candidate["accept_allowed"] != (
        candidate["binding_state"] == "exact_content_set_candidate"
    ):
        raise _source_artifact_error("任务来源候选接受门与状态冲突")
    if candidate["accept_allowed"] and catalog["status"] != "partial":
        raise _source_artifact_error("待接受精确来源候选必须保持 partial")
    if not candidate["accept_allowed"] and catalog["status"] != "blocked":
        raise _source_artifact_error("证据不足来源候选必须保持 blocked")
    if "source_binding_action_delegated_to_canonical_task" in blocker_codes:
        raise _source_artifact_error("卷级主来源任务不得携带委派阻断项")


def _node_evidence_rows(
    bindings: list[dict[str, Any]], node_ids: set[str]
) -> list[dict[str, Any]]:
    control = [
        row["evidence_binding_id"]
        for row in bindings
        if row["role"] in {"public_manifest", "public_layer"}
    ]
    result: list[dict[str, Any]] = []
    for node_id in sorted(node_ids):
        ids = list(control)
        ids.extend(
            row["evidence_binding_id"]
            for row in bindings
            if row.get("node_id") == node_id
        )
        ids = sorted(set(ids))
        if not ids:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid",
                "任务节点没有可绑定证据",
                409,
            )
        result.append({"node_id": node_id, "evidence_binding_ids": ids})
    return result


def _task_seed(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(task[key])
        for key in sorted(set(task) - _TASK_DERIVED_FIELDS)
    }


def _finalize_task(seed: dict[str, Any]) -> dict[str, Any]:
    digest = _sha256(canonical_json_bytes(seed))
    task = {
        "task_id": f"TRTASK-{digest}",
        **copy.deepcopy(seed),
        "base_snapshot_sha256": digest,
        "task_input_sha256": digest,
        "candidate_only": True,
        "human_reviewed": False,
        "central_master_mutated": False,
    }
    expected_keys = (
        _TASK_KEYS_V2
        if "source_binding" in seed and "source_binding_candidates" in seed
        else _TASK_KEYS_V1
    )
    if set(task) != expected_keys:
        raise ThemeReviewGatewayError(
            "theme_review_catalog_invalid", "任务目录字段合同不完整", 409
        )
    return task


def _source_candidate_task_sort_key(
    task: Mapping[str, Any],
) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    return (
        str(task["theme_id"]),
        tuple(task["target_printed_question_ids"]),
        tuple(task["target_atomic_part_ids"]),
        str(task["task_id"]),
    )


def _retain_one_source_candidate_task_per_paper(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if task.get("source_binding_candidates"):
            by_paper[task["paper_id"]].append(task)
    if set(by_paper) != _SOURCE_CROSSWALK_PAPERS:
        raise _source_artifact_error("三校来源候选任务范围漂移")

    canonical_ids = {
        min(rows, key=_source_candidate_task_sort_key)["task_id"]
        for rows in by_paper.values()
    }
    output: list[dict[str, Any]] = []
    for task in tasks:
        if (
            not task.get("source_binding_candidates")
            or task["task_id"] in canonical_ids
        ):
            output.append(task)
            continue
        seed = _task_seed(task)
        seed["source_binding_candidates"] = []
        seed["source_binding"]["catalog_binding"]["accept_allowed"] = False
        blocker = {
            "code": "source_binding_action_delegated_to_canonical_task",
            "message_zh": _SOURCE_BLOCKER_MESSAGES_ZH[
                "source_binding_action_delegated_to_canonical_task"
            ],
        }
        blockers = [
            row
            for row in seed["source_binding"]["blockers"]
            if row["code"] != blocker["code"]
        ]
        blockers.append(blocker)
        seed["source_binding"]["blockers"] = sorted(
            blockers, key=lambda row: row["code"]
        )
        output.append(_finalize_task(seed))
    if (
        sum(bool(task["source_binding_candidates"]) for task in output) != 3
        or sum(
            bool(task["source_binding_candidates"])
            and task["source_binding_candidates"][0]["accept_allowed"]
            for task in output
        )
        != 1
    ):
        raise _source_artifact_error("卷级唯一来源候选任务约束失败")
    return output


def _task_title(
    *, prefix: str, sequence: int | None, title: str | None
) -> str:
    sequence_text = f"第{sequence}主题" if sequence is not None else "主题序号待复核"
    title_text = f"「{title}」" if title else "（题面标题待补）"
    return f"{prefix}：{sequence_text}{title_text}"


def _atomic_sort_key(
    atom: Mapping[str, Any],
    printed: Mapping[str, Any],
    fallback: int,
) -> tuple[int, int, int]:
    printed_order = _known_int(printed.get("printed_question_order"))
    atomic_order = _known_int(atom.get("atomic_part_order"))
    return (
        printed_order if printed_order is not None else 10**9,
        atomic_order if atomic_order is not None else 10**9,
        fallback,
    )


class ThemeReviewTaskCatalog:
    """Validated immutable task-definition catalog, independent of the ledger."""

    def __init__(self, value: Mapping[str, Any]):
        self._value = self._validate(copy.deepcopy(dict(value)))
        self._raw = canonical_json_bytes(self._value)
        self.sha256 = _sha256(self._raw)
        self._by_id = {row["task_id"]: row for row in self._value["tasks"]}

    @classmethod
    def from_bytes(
        cls, raw: bytes, *, expected_sha256: str | None = None
    ) -> "ThemeReviewTaskCatalog":
        try:
            value = parse_json_object(raw)
        except ThemeReviewError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录不是严格 UTF-8 JSON", 409
            ) from exc
        if canonical_json_bytes(value) != raw:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录不是规范 JSON 字节", 409
            )
        if expected_sha256 is not None and _sha256(raw) != expected_sha256:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_hash_mismatch", "任务目录哈希不匹配", 409
            )
        catalog = cls(value)
        if catalog.canonical_bytes() != raw:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录规范化后发生漂移", 409
            )
        return catalog

    @classmethod
    def build_live(
        cls,
        public_kb: PublicKBReader,
        theme_workbench: ThemeWorkbenchReader,
    ) -> "ThemeReviewTaskCatalog":
        """Build one pure catalog snapshot without touching mutable state."""

        try:
            manifest_raw, manifest_sha = public_kb._read_exact(public_kb.MANIFEST)
            taxonomy_raw, taxonomy_sha = public_kb._read_exact(public_kb.TAXONOMY)
            layer_raw: dict[str, bytes] = {}
            layer_sha: dict[str, str] = {}
            layer_rows: dict[str, list[tuple[dict[str, Any], bytes]]] = {}
            for kind, relative in public_kb.LAYER_FILES.items():
                raw, digest = public_kb._read_exact(relative, verify_manifest=True)
                layer_raw[kind] = raw
                layer_sha[kind] = digest
                layer_rows[kind] = _jsonl_rows(raw, f"public {kind}")
            projection = theme_workbench.groups(MASTER_SCOPE)
            cross_snapshot = theme_workbench.master_workbench._snapshot()
        except (ReadOnlyDataError, ThemeWorkbenchError) as exc:
            code = getattr(exc, "code", "theme_review_source_unavailable")
            status = getattr(exc, "status", 409)
            raise ThemeReviewGatewayError(code, str(exc), status) from exc

        for kind, expected in _LAYER_COUNTS.items():
            if len(layer_rows[kind]) != expected:
                raise ThemeReviewGatewayError(
                    "theme_review_source_count_mismatch",
                    f"public {kind} 计数漂移",
                    409,
                )

        indexes = {
            "paper": _record_index(layer_rows["paper"], "paper_id", "paper"),
            "theme_big_question": _record_index(
                layer_rows["theme_big_question"],
                "theme_big_question_id",
                "theme_big_question",
            ),
            "printed_question": _record_index(
                layer_rows["printed_question"],
                "printed_question_id",
                "printed_question",
            ),
            "atomic_part": _record_index(
                layer_rows["atomic_part"], "atomic_part_id", "atomic_part"
            ),
        }
        source_crosswalk = _load_source_crosswalk(public_kb, indexes["paper"])

        # The crosswalk reader must be bound to the same parsed Master records.
        for kind in _LAYER_COUNTS:
            cross_rows = cross_snapshot.master_layers[kind]
            if [row for row, _ in layer_rows[kind]] != cross_rows:
                raise ThemeReviewGatewayError(
                    "theme_review_source_identity_mismatch",
                    "PublicKB 与 crosswalk 的 Master 快照不一致",
                    409,
                )

        try:
            taxonomy = parse_json_object(taxonomy_raw)
            axes = extract_taxonomy_axes(
                {
                    "axes": {
                        "K": [
                            row["id"]
                            for row in taxonomy["dimensions"]["knowledge_points"]
                        ],
                        "A": [
                            row["id"]
                            for row in taxonomy["dimensions"]["abilities"]
                        ],
                        "C": [
                            row["id"]
                            for row in taxonomy["dimensions"]["contexts"]
                        ],
                        "R": [
                            row["id"]
                            for row in taxonomy["dimensions"]["response_types"]
                        ],
                        "RP": [f"RP{index:02d}" for index in range(1, 13)],
                        "D": [
                            row["id"]
                            for row in taxonomy["dimensions"]["difficulty"]
                        ],
                    }
                }
            )
        except (KeyError, TypeError, ThemeReviewError, TagContractError) as exc:
            raise ThemeReviewGatewayError(
                "theme_review_taxonomy_invalid", "受控标签词表不可用", 409
            ) from exc
        taxonomy_axes = {key: sorted(value) for key, value in axes.items()}

        manifest_binding = _control_binding("public_manifest", manifest_raw)
        taxonomy_binding = _control_binding("public_taxonomy", taxonomy_raw)
        layer_bindings = {
            kind: _control_binding("public_layer", raw)
            for kind, raw in layer_raw.items()
        }

        paper_themes: dict[str, list[str]] = defaultdict(list)
        for theme_id, (theme, _) in indexes["theme_big_question"].items():
            paper_id = theme.get("parent_paper_id")
            if type(paper_id) is str and paper_id in indexes["paper"]:
                paper_themes[paper_id].append(theme_id)
        for theme_ids in paper_themes.values():
            theme_ids.sort()

        atoms_by_printed: dict[str, list[str]] = defaultdict(list)
        for atom_id, (atom, _) in indexes["atomic_part"].items():
            printed_id = atom.get("parent_printed_question_id")
            if type(printed_id) is str:
                atoms_by_printed[printed_id].append(atom_id)

        projection_entries: dict[str, dict[str, Any]] = {}
        for paper in projection["papers"]:
            for group in paper["theme_groups"]:
                for entry in group["atomic_chain"]:
                    atomic_id = entry["atomic_part_id"]
                    if atomic_id in projection_entries:
                        raise ThemeReviewGatewayError(
                            "theme_review_source_invalid",
                            "主题投影包含重复 atomic",
                            409,
                        )
                    projection_entries[atomic_id] = entry
        for entry in projection["unassigned_pending_review"]["atomic_chain"]:
            atomic_id = entry["atomic_part_id"]
            if atomic_id in projection_entries:
                raise ThemeReviewGatewayError(
                    "theme_review_source_invalid",
                    "未归组 atomic 与已归组主题重叠",
                    409,
                )
            projection_entries[atomic_id] = entry
        if set(projection_entries) != set(indexes["atomic_part"]):
            raise ThemeReviewGatewayError(
                "theme_review_source_identity_mismatch",
                "主题投影未精确覆盖 Master470",
                409,
            )

        tasks: list[dict[str, Any]] = []
        assigned_printed: set[str] = set()
        assigned_atomic: set[str] = set()

        def build_task(
            *,
            task_kind: str,
            priority: int,
            paper_id: str,
            theme_id: str,
            candidate_theme_id: str | None,
            title_zh: str,
            boundary_mode: str,
            candidate_basis_zh: str,
            printed_ids: list[str],
            atomic_ids: list[str],
            extra_bindings: list[dict[str, Any]] | None = None,
            source_position_override: Mapping[str, int] | None = None,
            shared_materials: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            paper, paper_raw = indexes["paper"][paper_id]
            theme_pair = indexes["theme_big_question"].get(theme_id)
            paper_binding = _record_binding(
                "paper_record", "paper", paper_id, paper_raw
            )
            source_control_bindings = copy.deepcopy(source_crosswalk["controls"])
            crosswalk_record = source_crosswalk["records"].get(paper_id)
            source_record_binding = (
                _source_artifact_binding(
                    "source_version_crosswalk_record",
                    source_crosswalk["record_raw"][paper_id],
                    paper_id=paper_id,
                )
                if crosswalk_record is not None
                else None
            )
            bindings = [
                manifest_binding,
                taxonomy_binding,
                *(layer_bindings.values()),
                paper_binding,
                *source_control_bindings,
            ]
            if source_record_binding is not None:
                bindings.append(source_record_binding)
            if theme_pair is not None:
                bindings.append(
                    _record_binding(
                        "theme_record",
                        "theme_big_question",
                        theme_id,
                        theme_pair[1],
                    )
                )
            current_tags: list[dict[str, Any]] = []
            current_hierarchy_printed: list[dict[str, Any]] = []
            current_hierarchy_atomic: list[dict[str, Any]] = []
            current_dependencies: list[dict[str, Any]] = []
            source_order: list[dict[str, Any]] = []
            all_source_hashes = _source_hashes(paper)

            for printed_id in printed_ids:
                printed, printed_raw = indexes["printed_question"][printed_id]
                bindings.append(
                    _record_binding(
                        "printed_record",
                        "printed_question",
                        printed_id,
                        printed_raw,
                    )
                )
                all_source_hashes |= _source_hashes(printed)
                current_hierarchy_printed.append(
                    {
                        "printed_question_id": printed_id,
                        "parent_theme_big_question_id": printed.get(
                            "parent_theme_big_question_id"
                        ),
                    }
                )

            for position, atomic_id in enumerate(atomic_ids):
                atom, atom_raw = indexes["atomic_part"][atomic_id]
                printed_id = atom.get("parent_printed_question_id")
                bindings.append(
                    _record_binding(
                        "atomic_record", "atomic_part", atomic_id, atom_raw
                    )
                )
                all_source_hashes |= _source_hashes(atom)
                current_tags.append(
                    {"atomic_part_id": atomic_id, "values": _current_tag_values(atom)}
                )
                current_hierarchy_atomic.append(
                    {
                        "atomic_part_id": atomic_id,
                        "parent_printed_question_id": printed_id,
                    }
                )
                entry = projection_entries[atomic_id]
                dependency = entry.get("dependency", {})
                prior_ids = dependency.get("prior_atomic_part_ids")
                if type(prior_ids) is not list:
                    prior_ids = []
                if prior_ids:
                    dependency_status = (
                        "blocked_existing_edge_relationship_kind_not_recorded"
                    )
                    typed_dependencies = None
                else:
                    dependency_status = dependency.get("status") or "unknown"
                    typed_dependencies = []
                current_dependencies.append(
                    {
                        "atomic_part_id": atomic_id,
                        "dependencies": typed_dependencies,
                        "untyped_prior_atomic_part_ids": list(prior_ids),
                        "status": dependency_status,
                    }
                )
                source_order.append(
                    {
                        "atomic_part_id": atomic_id,
                        "printed_question_id": printed_id,
                        "position": (
                            source_position_override[atomic_id]
                            if source_position_override is not None
                            else position
                        ),
                        "explicit": (
                            source_position_override is not None
                            or (
                                entry.get("printed_sequence_status")
                                == "known_explicit"
                                and entry.get("atomic_sequence_status")
                                == "known_explicit"
                            )
                        ),
                    }
                )

            for digest in sorted(all_source_hashes):
                bindings.append(_hash_binding(digest))
            if extra_bindings:
                bindings.extend(extra_bindings)
            bindings = _deduplicate_bindings(bindings)
            source_evidence_ids = sorted(
                {
                    paper_binding["evidence_binding_id"],
                    *(
                        row["evidence_binding_id"]
                        for row in source_control_bindings
                    ),
                    *(
                        [source_record_binding["evidence_binding_id"]]
                        if source_record_binding is not None
                        else []
                    ),
                }
            )
            source_binding, source_binding_candidates = (
                _build_source_binding_projection(
                    paper=paper,
                    crosswalk_record=crosswalk_record,
                    evidence_ids=source_evidence_ids,
                )
            )
            node_ids = set(printed_ids) | set(atomic_ids)
            node_evidence = _node_evidence_rows(bindings, node_ids)
            seed = {
                "task_kind": task_kind,
                "priority": priority,
                "paper_id": paper_id,
                "theme_id": theme_id,
                "candidate_theme_id": candidate_theme_id,
                "title_zh": title_zh,
                "boundary_mode": boundary_mode,
                "candidate_basis_zh": candidate_basis_zh,
                "target_atomic_part_ids": list(atomic_ids),
                "target_printed_question_ids": list(printed_ids),
                "allowed_theme_ids": list(paper_themes[paper_id]),
                "current_tags": current_tags,
                "current_hierarchy": {
                    "printed_questions": current_hierarchy_printed,
                    "atomic_parts": current_hierarchy_atomic,
                },
                "current_dependencies": current_dependencies,
                "source_order": source_order,
                "evidence_bindings": bindings,
                "node_evidence_ids": node_evidence,
                "shared_materials": copy.deepcopy(shared_materials or []),
                "source_binding": source_binding,
                "source_binding_candidates": source_binding_candidates,
            }
            return _finalize_task(seed)

        # 48 themes already have explicit, complete Master parent chains.
        for paper in projection["papers"]:
            for group in paper["theme_groups"]:
                paper_id = group["paper"]["id"]
                theme_id = group["theme"]["id"]
                theme_record = indexes["theme_big_question"].get(theme_id)
                if theme_record is None or theme_record[0].get("parent_paper_id") != paper_id:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_invalid",
                        "显式主题的 paper 绑定不一致",
                        409,
                    )
                atomic_ids = [row["atomic_part_id"] for row in group["atomic_chain"]]
                printed_ids: list[str] = []
                for atomic_id in atomic_ids:
                    atom = indexes["atomic_part"][atomic_id][0]
                    printed_id = atom.get("parent_printed_question_id")
                    printed_pair = indexes["printed_question"].get(printed_id)
                    if (
                        printed_pair is None
                        or printed_pair[0].get("parent_theme_big_question_id") != theme_id
                    ):
                        raise ThemeReviewGatewayError(
                            "theme_review_source_invalid",
                            "显式主题成员父链不一致",
                            409,
                        )
                    if printed_id not in printed_ids:
                        printed_ids.append(printed_id)
                if assigned_atomic.intersection(atomic_ids) or assigned_printed.intersection(
                    printed_ids
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_source_invalid", "显式主题任务发生成员重叠", 409
                    )
                assigned_atomic.update(atomic_ids)
                assigned_printed.update(printed_ids)
                tasks.append(
                    build_task(
                        task_kind=THEME_TASK,
                        priority=100,
                        paper_id=paper_id,
                        theme_id=theme_id,
                        candidate_theme_id=None,
                        title_zh=_task_title(
                            prefix="整主题复核",
                            sequence=group["theme"].get("sequence"),
                            title=group["theme"].get("title"),
                        ),
                        boundary_mode=BOUNDARY_MODE_THEME,
                        candidate_basis_zh=(
                            "Master 公共索引已声明完整 paper→theme→printed→atomic 父链；"
                            "本任务仍只生成候选复核记录。"
                        ),
                        printed_ids=printed_ids,
                        atomic_ids=atomic_ids,
                        shared_materials=[
                            {
                                "material_id": material.get("material_id"),
                                "type": material.get("type"),
                                "page_numbers": (
                                    [material["page"]]
                                    if type(material.get("page")) is int
                                    else []
                                ),
                                "candidate_description_zh": material.get(
                                    "candidate_description_zh"
                                ),
                                "evidence_ids": [],
                            }
                            for material in group.get("shared_context", {}).get(
                                "materials", []
                            )
                            if type(material) is dict
                        ],
                    )
                )

        unassigned_ids = {
            row["atomic_part_id"]
            for row in projection["unassigned_pending_review"]["atomic_chain"]
        }
        candidate_groups: dict[
            tuple[str, str], dict[str, Any]
        ] = defaultdict(
            lambda: {
                "atomic": set(),
                "printed": set(),
                "bindings": [],
                "source_position": {},
            }
        )
        wave_source_position = {
            wave_id: position
            for position, wave_id in enumerate(cross_snapshot.wave_atomic)
        }
        for atomic_id in sorted(unassigned_ids):
            atom = indexes["atomic_part"][atomic_id][0]
            printed_id = atom.get("parent_printed_question_id")
            printed_pair = indexes["printed_question"].get(printed_id)
            rows = cross_snapshot.relations_by_master.get(atomic_id, [])
            if printed_pair is None or not rows:
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_evidence_missing",
                    "未归组 atomic 缺少精确 crosswalk 父链证据",
                    409,
                )
            parent_candidates: set[tuple[str, str, str]] = set()
            for row in rows:
                if row.get("relation_type") not in {
                    "exact_1_to_1",
                    "wave_refines_master",
                }:
                    raise ThemeReviewGatewayError(
                        "theme_review_boundary_evidence_invalid",
                        "未归组 atomic 只有精确/split crosswalk 可作候选证据",
                        409,
                    )
                master = row.get("master")
                chain = master.get("parent_chain") if type(master) is dict else None
                if type(chain) is not dict:
                    raise ThemeReviewGatewayError(
                        "theme_review_boundary_evidence_invalid",
                        "crosswalk 缺少候选父链",
                        409,
                    )
                parent_candidates.add(
                    (
                        chain.get("paper_id"),
                        chain.get("theme_id"),
                        chain.get("printed_question_id"),
                    )
                )
            if len(parent_candidates) != 1:
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_evidence_conflict",
                    "同一未归组 atomic 的 crosswalk 候选父链冲突",
                    409,
                )
            paper_id, theme_id, cross_printed_id = next(iter(parent_candidates))
            if (
                paper_id != atom.get("parent_paper_id")
                or cross_printed_id != printed_id
                or printed_pair[0].get("parent_paper_id") != paper_id
                or printed_pair[0].get("parent_theme_big_question_id") is not None
                or theme_id not in paper_themes.get(paper_id, [])
                or indexes["theme_big_question"][theme_id][0].get("parent_paper_id")
                != paper_id
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_evidence_invalid",
                    "crosswalk 候选父链未通过同 paper 与空父节点校验",
                    409,
                )
            group = candidate_groups[(paper_id, theme_id)]
            group["atomic"].add(atomic_id)
            group["printed"].add(printed_id)
            group["bindings"].extend(_crosswalk_binding(row) for row in rows)
            positions = [
                wave_source_position[row["wave1"]["endpoint_id"]] for row in rows
            ]
            if not positions:
                raise ThemeReviewGatewayError(
                    "theme_review_candidate_order_unknown",
                    "crosswalk 未绑定 Wave1 源顺序",
                    409,
                )
            group["source_position"][atomic_id] = min(positions)

        # The first actionable parent-gap batch is exactly five candidate
        # groups, 41 printed questions and 43 Master atomic parts.  They remain
        # boundary tasks even though the exact crosswalk supplies a candidate
        # theme ID.
        if (
            len(candidate_groups) != 5
            or sum(len(group["printed"]) for group in candidate_groups.values())
            != 41
            or sum(len(group["atomic"]) for group in candidate_groups.values())
            != 43
            or len({paper_id for paper_id, _ in candidate_groups}) != 1
        ):
            raise ThemeReviewGatewayError(
                "theme_review_candidate_group_count_mismatch",
                "首批五主题候选边界不再精确覆盖 41 printed / 43 atomic",
                409,
            )

        for (paper_id, theme_id), group in candidate_groups.items():
            decorated = sorted(
                (
                    group["source_position"][atomic_id],
                    atomic_id,
                )
                for atomic_id in group["atomic"]
            )
            atomic_ids = [atomic_id for _, atomic_id in decorated]
            if len({position for position, _ in decorated}) != len(decorated):
                raise ThemeReviewGatewayError(
                    "theme_review_candidate_order_unknown",
                    "首批候选主题的 Wave1 源顺序重复",
                    409,
                )
            printed_ids = []
            for atomic_id in atomic_ids:
                printed_id = indexes["atomic_part"][atomic_id][0][
                    "parent_printed_question_id"
                ]
                if printed_id not in printed_ids:
                    printed_ids.append(printed_id)
            theme = indexes["theme_big_question"][theme_id][0]
            if assigned_atomic.intersection(atomic_ids) or assigned_printed.intersection(
                printed_ids
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_source_invalid", "候选主题任务发生成员重叠", 409
                )
            assigned_atomic.update(atomic_ids)
            assigned_printed.update(printed_ids)
            tasks.append(
                build_task(
                    task_kind=BOUNDARY_TASK,
                    priority=0,
                    paper_id=paper_id,
                    theme_id=theme_id,
                    candidate_theme_id=theme_id,
                    title_zh=_task_title(
                        prefix="候选主题边界复核",
                        sequence=_known_int(theme.get("theme_order")),
                        title=_known_text(theme.get("theme_title")),
                    ),
                    boundary_mode=BOUNDARY_MODE_CANDIDATE_THEME,
                    candidate_basis_zh=(
                        "精确 strong-key crosswalk 给出同 paper 的候选 theme 父链；"
                        "Master 当前仍为 missing_parent_pending_review，未自动应用。"
                    ),
                    printed_ids=printed_ids,
                    atomic_ids=atomic_ids,
                    extra_bindings=group["bindings"],
                    source_position_override={
                        atomic_id: position for position, atomic_id in decorated
                    },
                )
            )

        # The remaining 94 missing-theme printed questions have zero atomic
        # children.  They stay one-question boundary tasks and may not carry
        # tag, hierarchy, or dependency changes.
        missing_theme_printed = [
            printed_id
            for printed_id, (printed, _) in indexes["printed_question"].items()
            if printed.get("parent_theme_big_question_id") is None
        ]
        no_atomic_printed = [
            printed_id
            for printed_id in missing_theme_printed
            if not atoms_by_printed.get(printed_id)
        ]
        if len(missing_theme_printed) != 135 or len(no_atomic_printed) != 94:
            raise ThemeReviewGatewayError(
                "theme_review_boundary_count_mismatch",
                "缺主题/无 atomic 边界计数漂移",
                409,
            )
        for printed_id in no_atomic_printed:
            printed = indexes["printed_question"][printed_id][0]
            paper_id = printed.get("parent_paper_id")
            if paper_id not in indexes["paper"] or not paper_themes.get(paper_id):
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_evidence_missing",
                    "无 atomic 印刷题缺少同 paper 主题 allowlist",
                    409,
                )
            pseudo_theme = f"PAPERBOUNDARY-{_sha256(canonical_json_bytes({'paper_id': paper_id, 'printed_question_id': printed_id}))}"
            literal = printed.get("printed_question_number_literal")
            literal_text = (
                str(literal)
                if type(literal) in (str, int, float) and type(literal) is not bool
                else "题号待复核"
            )
            if printed_id in assigned_printed:
                raise ThemeReviewGatewayError(
                    "theme_review_source_invalid", "边界任务发生 printed 重叠", 409
                )
            assigned_printed.add(printed_id)
            tasks.append(
                build_task(
                    task_kind=BOUNDARY_TASK,
                    priority=200,
                    paper_id=paper_id,
                    theme_id=pseudo_theme,
                    candidate_theme_id=None,
                    title_zh=f"印刷小题拆题边界复核（题号 {literal_text}）",
                    boundary_mode=BOUNDARY_MODE_ATOMIC_ONLY,
                    candidate_basis_zh=(
                        "Master 已明确该 printed 缺 theme 且当前无 atomic；"
                        "只允许提交拆题边界候选，不猜测主题或标签。"
                    ),
                    printed_ids=[printed_id],
                    atomic_ids=[],
                )
            )

        if assigned_printed != set(indexes["printed_question"]) or assigned_atomic != set(
            indexes["atomic_part"]
        ):
            raise ThemeReviewGatewayError(
                "theme_review_catalog_coverage_mismatch",
                "任务目录未无重叠覆盖全部 501 printed / 470 atomic",
                409,
            )

        tasks = _retain_one_source_candidate_task_per_paper(tasks)
        tasks.sort(
            key=lambda row: (
                row["priority"],
                row["paper_id"],
                row["theme_id"],
                row["task_id"],
            )
        )
        counts = {
            "tasks": len(tasks),
            "theme_big_question_tasks": sum(
                row["task_kind"] == THEME_TASK for row in tasks
            ),
            "paper_theme_boundary_review_tasks": sum(
                row["task_kind"] == BOUNDARY_TASK for row in tasks
            ),
            "candidate_theme_boundary_tasks": sum(
                row["boundary_mode"] == BOUNDARY_MODE_CANDIDATE_THEME
                for row in tasks
            ),
            "atomic_boundary_only_tasks": sum(
                row["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY for row in tasks
            ),
            "target_printed_questions": len(assigned_printed),
            "target_atomic_parts": len(assigned_atomic),
            "missing_theme_printed_questions": len(missing_theme_printed),
            "candidate_group_printed_questions": sum(
                len(group["printed"]) for group in candidate_groups.values()
            ),
            "candidate_group_atomic_parts": sum(
                len(group["atomic"]) for group in candidate_groups.values()
            ),
            "no_atomic_boundary_printed_questions": len(no_atomic_printed),
        }
        value = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "scope": "master470_candidate_review_no_apply",
            "counts": counts,
            "source_snapshot": {
                "public_manifest_sha256": manifest_sha,
                "public_manifest_size_bytes": len(manifest_raw),
                "taxonomy_sha256": taxonomy_sha,
                "taxonomy_size_bytes": len(taxonomy_raw),
                "layer_sha256": dict(sorted(layer_sha.items())),
                "layer_size_bytes": {
                    kind: len(layer_raw[kind]) for kind in sorted(layer_raw)
                },
                "theme_projection_sha256": _sha256(
                    canonical_json_bytes(projection)
                ),
                "crosswalk_manifest_self_sha256": cross_snapshot.manifest_self_sha256,
                "source_version_crosswalk": copy.deepcopy(
                    source_crosswalk["snapshot"]
                ),
            },
            "taxonomy_axes": taxonomy_axes,
            "tasks": tasks,
            "authority": dict(_AUTHORITY),
            "integrity": {
                "canonical_task_inputs": True,
                "hash_verified_public_sources": True,
                "all_printed_questions_exactly_once": True,
                "all_atomic_parts_exactly_once": True,
                "candidate_parent_not_promoted": True,
                "no_id_or_question_number_parent_inference": True,
                "source_paths_and_urls_excluded": True,
                "mutable_store_excluded": True,
                "source_version_crosswalk_hash_verified": True,
                "source_version_candidate_authority_closed": True,
            },
        }
        return cls(value)

    @staticmethod
    def _validate(value: dict[str, Any]) -> dict[str, Any]:
        _exact_keys(value, _CATALOG_KEYS, "theme_review_catalog")
        if (
            value["schema_version"]
            not in {CATALOG_SCHEMA_VERSION_V1, CATALOG_SCHEMA_VERSION_V2}
            or value["scope"] != "master470_candidate_review_no_apply"
            or value["counts"] != _EXPECTED_COUNTS
            or value["authority"] != _AUTHORITY
        ):
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录头部或固定计数漂移", 409
            )
        if type(value["tasks"]) is not list or len(value["tasks"]) != 147:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录 tasks 无效", 409
            )
        axes = value["taxonomy_axes"]
        if type(axes) is not dict or set(axes) != {"K", "A", "C", "R", "RP", "D"}:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录标签轴无效", 409
            )
        try:
            normalized_axes = extract_taxonomy_axes({"axes": axes})
        except TagContractError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录受控标签无效", 409
            ) from exc
        if any(sorted(normalized_axes[key]) != axes[key] for key in axes):
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录标签轴未规范排序", 409
            )

        if value["schema_version"] == CATALOG_SCHEMA_VERSION_V2:
            source_snapshot = (
                value["source_snapshot"].get("source_version_crosswalk")
                if type(value["source_snapshot"]) is dict
                else None
            )
            source_snapshot_keys = frozenset(
                {
                    "manifest_sha256",
                    "manifest_size_bytes",
                    "manifest_self_sha256",
                    "data_sha256",
                    "data_size_bytes",
                    "schema_sha256",
                    "schema_size_bytes",
                    "artifact_file_sha256",
                    "record_count",
                    "upstream_verification_receipt",
                }
            )
            source_snapshot = _source_exact_keys(
                source_snapshot,
                source_snapshot_keys,
                "source_version_crosswalk snapshot",
            )
            for key in (
                "manifest_sha256",
                "manifest_self_sha256",
                "data_sha256",
                "schema_sha256",
            ):
                _source_sha(source_snapshot[key], f"source_snapshot.{key}")
            expected_artifact_files = {
                relative.removeprefix(
                    f"{PublicKBReader.SOURCE_VERSION_CROSSWALK_ROOT}/"
                )
                for relative in PublicKBReader.SOURCE_VERSION_CROSSWALK_FILES
            }
            if (
                source_snapshot["record_count"] != 3
                or any(
                    type(source_snapshot[key]) is not int
                    or type(source_snapshot[key]) is bool
                    or source_snapshot[key] < 1
                    for key in (
                        "manifest_size_bytes",
                        "data_size_bytes",
                        "schema_size_bytes",
                    )
                )
                or type(source_snapshot["artifact_file_sha256"]) is not dict
                or set(source_snapshot["artifact_file_sha256"])
                != expected_artifact_files
            ):
                raise _source_artifact_error("来源版本 snapshot 计数或文件闭集漂移")
            for name, digest in source_snapshot["artifact_file_sha256"].items():
                _source_sha(digest, f"artifact_file_sha256.{name}")
            _validate_upstream_verification_receipt(
                source_snapshot["upstream_verification_receipt"]
            )

        task_ids: set[str] = set()
        printed_ids: set[str] = set()
        atomic_ids: set[str] = set()
        task_kinds: Counter[str] = Counter()
        modes: Counter[str] = Counter()
        source_candidate_tasks: Counter[str] = Counter()
        source_candidate_ids: set[str] = set()
        source_accept_allowed_tasks = 0
        task_keys = (
            _TASK_KEYS_V2
            if value["schema_version"] == CATALOG_SCHEMA_VERSION_V2
            else _TASK_KEYS_V1
        )
        for task in value["tasks"]:
            _exact_keys(task, task_keys, "theme_review_task")
            try:
                validate_identifier(task["task_id"], "task_id")
                validate_identifier(task["paper_id"], "paper_id")
                validate_identifier(task["theme_id"], "theme_id")
            except ThemeReviewError as exc:
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务 ID 无效", 409
                ) from exc
            if (
                task["task_id"] in task_ids
                or task["task_kind"] not in TASK_KINDS
                or task["boundary_mode"] not in BOUNDARY_MODES
                or task["candidate_only"] is not True
                or task["human_reviewed"] is not False
                or task["central_master_mutated"] is not False
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务身份或门禁无效", 409
                )
            task_ids.add(task["task_id"])
            digest = _sha256(canonical_json_bytes(_task_seed(task)))
            if (
                task["task_id"] != f"TRTASK-{digest}"
                or task["base_snapshot_sha256"] != digest
                or task["task_input_sha256"] != digest
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务输入哈希绑定失败", 409
                )
            if type(task["target_printed_question_ids"]) is not list or type(
                task["target_atomic_part_ids"]
            ) is not list:
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务成员数组无效", 409
                )
            if printed_ids.intersection(task["target_printed_question_ids"]) or atomic_ids.intersection(
                task["target_atomic_part_ids"]
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务成员重复覆盖", 409
                )
            printed_ids.update(task["target_printed_question_ids"])
            atomic_ids.update(task["target_atomic_part_ids"])
            task_kinds[task["task_kind"]] += 1
            modes[task["boundary_mode"]] += 1
            if task["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY and task[
                "target_atomic_part_ids"
            ]:
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "纯边界任务不得预存 atomic", 409
                )
            if task["boundary_mode"] == BOUNDARY_MODE_CANDIDATE_THEME and (
                task["task_kind"] != BOUNDARY_TASK
                or task["candidate_theme_id"] != task["theme_id"]
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "候选主题被错误提升", 409
                )
            binding_ids = [
                row.get("evidence_binding_id") for row in task["evidence_bindings"]
            ]
            if len(binding_ids) != len(set(binding_ids)) or not binding_ids:
                raise ThemeReviewGatewayError(
                    "theme_review_catalog_invalid", "任务证据绑定重复或为空", 409
                )
            if value["schema_version"] == CATALOG_SCHEMA_VERSION_V2:
                _validate_task_source_binding(task)
                if task["source_binding_candidates"]:
                    source_candidate_tasks[task["paper_id"]] += 1
                    candidate = task["source_binding_candidates"][0]
                    if candidate["candidate_id"] in source_candidate_ids:
                        raise _source_artifact_error(
                            "同一卷来源候选被复制到多个可写任务"
                        )
                    source_candidate_ids.add(candidate["candidate_id"])
                    source_accept_allowed_tasks += int(
                        candidate["accept_allowed"]
                    )
        if (
            len(printed_ids) != 501
            or len(atomic_ids) != 470
            or task_kinds != Counter({THEME_TASK: 48, BOUNDARY_TASK: 99})
            or modes[BOUNDARY_MODE_CANDIDATE_THEME] != 5
            or modes[BOUNDARY_MODE_ATOMIC_ONLY] != 94
            or (
                value["schema_version"] == CATALOG_SCHEMA_VERSION_V2
                and source_candidate_tasks
                != Counter(
                    {
                        _HUAER_PAPER_ID: 1,
                        _FUDAN_PAPER_ID: 1,
                        _DATONG_PAPER_ID: 1,
                    }
                )
            )
            or (
                value["schema_version"] == CATALOG_SCHEMA_VERSION_V2
                and source_accept_allowed_tasks != 1
            )
        ):
            raise ThemeReviewGatewayError(
                "theme_review_catalog_invalid", "任务目录派生覆盖计数不一致", 409
            )
        _assert_projection_safe(value)
        return value

    def canonical_bytes(self) -> bytes:
        return bytes(self._raw)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._value)

    def task(self, task_id: str) -> dict[str, Any]:
        try:
            validate_identifier(task_id, "task_id")
        except ThemeReviewError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", str(exc), 400
            ) from exc
        task = self._by_id.get(task_id)
        if task is None:
            raise ThemeReviewGatewayError(
                "theme_review_task_not_found", "主题复核任务不存在", 404
            )
        return copy.deepcopy(task)

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._value["tasks"])

    @property
    def taxonomy_axes(self) -> dict[str, list[str]]:
        return copy.deepcopy(self._value["taxonomy_axes"])


class ThemeReviewGateway:
    """Thin safe adapter around a frozen task catalog and mutable local ledger."""

    def __init__(
        self,
        *,
        catalog: ThemeReviewTaskCatalog | bytes,
        store: AppendOnlyThemeReviewStore | None = None,
        state_root: Path | None = None,
        store_factory: Callable[[Path], AppendOnlyThemeReviewStore] | None = None,
        release_context: Mapping[str, Any] | None = None,
    ) -> None:
        self.catalog = (
            catalog
            if isinstance(catalog, ThemeReviewTaskCatalog)
            else ThemeReviewTaskCatalog.from_bytes(catalog)
        )
        self._store = store
        self._store_factory = store_factory or (
            lambda root: AppendOnlyThemeReviewStore(root)
        )
        self._state_root = state_root.absolute() if state_root is not None else None
        source_snapshot = self.catalog.as_dict()["source_snapshot"]
        supplied_context = dict(release_context or {})
        unknown_context = set(supplied_context) - {
            "serving_release_id",
            "data_snapshot_id",
            "browse_snapshot_id",
        }
        if unknown_context:
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid",
                f"release_context 含未知字段：{sorted(unknown_context)}",
                400,
            )
        self._release_context = {
            "serving_release_id": supplied_context.get("serving_release_id"),
            "data_snapshot_id": supplied_context.get("data_snapshot_id")
            or f"TRDATA-{source_snapshot['public_manifest_sha256']}",
            "browse_snapshot_id": supplied_context.get("browse_snapshot_id")
            or f"TRBROWSE-{source_snapshot['theme_projection_sha256']}",
            "source_manifest_sha256": source_snapshot[
                "public_manifest_sha256"
            ],
        }
        _assert_projection_safe(self._release_context, "release_context")
        self._all_atomic_ids = {
            atomic_id for task in self.catalog.tasks for atomic_id in task["target_atomic_part_ids"]
        }

    @classmethod
    def from_live_readers(
        cls,
        public_kb: PublicKBReader,
        theme_workbench: ThemeWorkbenchReader,
        **kwargs: Any,
    ) -> "ThemeReviewGateway":
        return cls(
            catalog=ThemeReviewTaskCatalog.build_live(public_kb, theme_workbench),
            **kwargs,
        )

    @staticmethod
    def _default_state_root() -> Path:
        local = os.environ.get("LOCALAPPDATA")
        if type(local) is not str or not local.strip():
            raise ThemeReviewGatewayError(
                "theme_review_state_unavailable",
                "未找到当前 Windows 用户的 LOCALAPPDATA，复核账本保持关闭",
                503,
            )
        root = Path(local).absolute()
        if not root.is_absolute():
            raise ThemeReviewGatewayError(
                "theme_review_state_unavailable", "LOCALAPPDATA 无效", 503
            )
        return root / "Codex" / "ShanghaiChemistry" / "ThemeReviewWorkbenchV1"

    def _store_for_use(self) -> AppendOnlyThemeReviewStore:
        if self._store is None:
            root = self._state_root or self._default_state_root()
            try:
                self._store = self._store_factory(root)
            except ThemeReviewError as exc:
                raise self._map_core_error(exc) from exc
        return self._store

    @staticmethod
    def _map_core_error(exc: ThemeReviewError) -> ThemeReviewGatewayError:
        return ThemeReviewGatewayError(exc.code, str(exc), exc.status_code)

    @staticmethod
    def _core_task_body(task: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "task_id": task["task_id"],
            "paper_id": task["paper_id"],
            "theme_id": task["theme_id"],
            "title_zh": task["title_zh"],
            "base_snapshot_sha256": task["base_snapshot_sha256"],
            "target_atomic_part_ids": copy.deepcopy(task["target_atomic_part_ids"]),
            "target_printed_question_ids": copy.deepcopy(
                task["target_printed_question_ids"]
            ),
            "evidence_binding_ids": [
                row["evidence_binding_id"] for row in task["evidence_bindings"]
            ],
            "idempotency_key": f"catalog-{task['task_input_sha256']}",
            "expected_revision": None,
        }
        if "source_binding_candidates" in task:
            body["source_binding_candidates"] = copy.deepcopy(
                task["source_binding_candidates"]
            )
        return body

    @classmethod
    def _assert_state_matches_task(
        cls, state: Mapping[str, Any], task: Mapping[str, Any]
    ) -> None:
        expected = cls._core_task_body(task)
        expected.pop("idempotency_key")
        expected.pop("expected_revision")
        if type(state) is not dict or state.get("task") != expected:
            raise ThemeReviewGatewayError(
                "theme_review_state_catalog_mismatch",
                "可变复核账本与冻结任务目录不一致",
                409,
            )
        for key, required in _AUTHORITY.items():
            if state.get(key) is not required:
                raise ThemeReviewGatewayError(
                    "theme_review_authority_escalation",
                    "复核账本尝试提升候选权限",
                    409,
                )

    def _states(self) -> dict[str, dict[str, Any]]:
        try:
            rows = self._store_for_use().list_tasks()
        except TagContractError as exc:
            raise self._map_core_error(exc) from exc
        output: dict[str, dict[str, Any]] = {}
        for state in rows:
            task_id = state.get("task", {}).get("task_id")
            try:
                task = self.catalog.task(task_id)
            except ThemeReviewGatewayError as exc:
                if exc.status == 404:
                    # Older content-addressed tasks may coexist in the same
                    # append-only store; they are never projected into the
                    # selected frozen catalog.
                    continue
                raise
            self._assert_state_matches_task(state, task)
            output[task_id] = state
        return output

    def _state(self, task: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            state = self._store_for_use().get_task(task["task_id"])
        except ThemeReviewError as exc:
            if exc.code == "theme_review_not_found":
                return None
            raise self._map_core_error(exc) from exc
        self._assert_state_matches_task(state, task)
        return state

    def _ensure_registered(self, task: Mapping[str, Any]) -> dict[str, Any]:
        state = self._state(task)
        if state is not None:
            return state
        try:
            result = self._store_for_use().create_task(
                self._core_task_body(task), principal_id=SYSTEM_CATALOG_PRINCIPAL
            )
        except ThemeReviewError as exc:
            # Another process may have registered the same content-addressed
            # task between the read and the exclusive create.
            if exc.code == "theme_review_conflict":
                state = self._state(task)
                if state is not None:
                    return state
            raise self._map_core_error(exc) from exc
        state = result["task_state"]
        self._assert_state_matches_task(state, task)
        return state

    @staticmethod
    def _allowed_actions(state: Mapping[str, Any] | None) -> dict[str, bool]:
        if state is None:
            values = {
                "claim": True,
                "release": False,
                "submit_change_set": False,
                "record_decision": False,
            }
        else:
            claimed = state.get("claimed") is True
            pending = any(
                row.get("decision_id") is None for row in state.get("change_sets", [])
            )
            values = {
                "claim": not claimed,
                "release": claimed,
                "submit_change_set": claimed,
                "record_decision": claimed and pending,
            }
        if tuple(values) != ALLOWED_ACTION_KEYS:
            raise AssertionError("allowed action key order drifted")
        return values

    @staticmethod
    def _state_summary(state: Mapping[str, Any] | None) -> dict[str, Any]:
        if state is None:
            return {
                "registered": False,
                "revision": None,
                "sequence": 0,
                "claimed": False,
                "claimed_by_principal_id": None,
                "change_set_count": 0,
                "decision_count": 0,
            }
        return {
            "registered": True,
            "revision": state["revision"],
            "sequence": state["sequence"],
            "claimed": state["claimed"],
            "claimed_by_principal_id": state["claimed_by_principal_id"],
            "change_set_count": len(state["change_sets"]),
            "decision_count": len(state["decisions"]),
        }

    @staticmethod
    def _status(state: Mapping[str, Any] | None) -> str:
        if state is None:
            return "available"
        if state.get("decisions"):
            return "decision_recorded_candidate_only"
        if state.get("change_sets"):
            return "change_set_submitted_pending_decision"
        if state.get("claimed") is True:
            return "claimed_in_review"
        return "available"

    @staticmethod
    def _latest(
        state: Mapping[str, Any] | None, collection: str
    ) -> Mapping[str, Any] | None:
        if state is None or not state.get(collection):
            return None
        return max(state[collection], key=lambda row: row["sequence"])

    @staticmethod
    def _gap_counts(task: Mapping[str, Any]) -> dict[str, int]:
        tags = {row["atomic_part_id"]: row["values"] for row in task["current_tags"]}
        return {
            "missing_theme_parent_printed": sum(
                row["parent_theme_big_question_id"] is None
                for row in task["current_hierarchy"]["printed_questions"]
            ),
            "printed_without_atomic": sum(
                not any(
                    atom["parent_printed_question_id"] == row["printed_question_id"]
                    for atom in task["current_hierarchy"]["atomic_parts"]
                )
                for row in task["current_hierarchy"]["printed_questions"]
            ),
            "item_type": sum(not values.get("item_type") for values in tags.values()),
            "context_C": sum(not values.get("context_C") for values in tags.values()),
            "representation_RP": sum(
                not values.get("representation_RP") for values in tags.values()
            ),
            "cognitive_prelabel": sum(
                not values.get("cognitive_prelabel") for values in tags.values()
            ),
            "dependency_relationship_kind_untyped": sum(
                row["dependencies"] is None
                for row in task["current_dependencies"]
            ),
        }

    @staticmethod
    def _printed_projection(task: Mapping[str, Any]) -> list[dict[str, Any]]:
        atomic_positions: dict[str, list[int]] = defaultdict(list)
        for row in task["source_order"]:
            atomic_positions[row["printed_question_id"]].append(row["position"])
        result: list[dict[str, Any]] = []
        for fallback, row in enumerate(task["current_hierarchy"]["printed_questions"]):
            printed_id = row["printed_question_id"]
            positions = atomic_positions.get(printed_id, [])
            evidence = next(
                item["evidence_binding_ids"]
                for item in task["node_evidence_ids"]
                if item["node_id"] == printed_id
            )
            result.append(
                {
                    "printed_question_id": printed_id,
                    "title_zh": None,
                    "question_number": None,
                    "source_order": min(positions) if positions else fallback,
                    "current_parent_theme_id": row[
                        "parent_theme_big_question_id"
                    ],
                    "candidate_parent_theme_id": (
                        task["candidate_theme_id"]
                        if row["parent_theme_big_question_id"] is None
                        else None
                    ),
                    "evidence_ids": copy.deepcopy(evidence),
                }
            )
        result.sort(key=lambda row: (row["source_order"], row["printed_question_id"]))
        return result

    @staticmethod
    def _atomic_projection(
        task: Mapping[str, Any], state: Mapping[str, Any] | None
    ) -> list[dict[str, Any]]:
        tag_map = {row["atomic_part_id"]: row["values"] for row in task["current_tags"]}
        parent_map = {
            row["atomic_part_id"]: row["parent_printed_question_id"]
            for row in task["current_hierarchy"]["atomic_parts"]
        }
        dependency_map = {
            row["atomic_part_id"]: row for row in task["current_dependencies"]
        }
        evidence_map = {
            row["node_id"]: row["evidence_binding_ids"]
            for row in task["node_evidence_ids"]
        }
        latest = ThemeReviewGateway._latest(state, "change_sets")
        candidate_tags: dict[str, dict[str, Any]] = defaultdict(dict)
        candidate_dependencies: dict[str, list[dict[str, str]]] = {}
        if latest is not None:
            for row in latest["request"]["tag_replacements"]:
                candidate_tags[row["atomic_part_id"]][row["field"]] = copy.deepcopy(
                    row["after"]
                )
            for row in latest["request"]["dependency_replacements"]:
                candidate_dependencies[row["atomic_part_id"]] = copy.deepcopy(
                    row["after_dependencies"]
                )
        result: list[dict[str, Any]] = []
        for order in sorted(task["source_order"], key=lambda row: row["position"]):
            atomic_id = order["atomic_part_id"]
            current = tag_map[atomic_id]
            gaps = [
                field
                for field, value in current.items()
                if value is None or value == []
            ]
            dependency = copy.deepcopy(dependency_map[atomic_id])
            if atomic_id in candidate_dependencies:
                dependency["candidate_dependencies"] = candidate_dependencies[atomic_id]
            result.append(
                {
                    "atomic_part_id": atomic_id,
                    "printed_question_id": parent_map[atomic_id],
                    "source_order": order["position"],
                    "current_tags": copy.deepcopy(current),
                    "candidate_tags": copy.deepcopy(candidate_tags.get(atomic_id, {})),
                    "dependency": dependency,
                    "evidence_ids": copy.deepcopy(evidence_map[atomic_id]),
                    "gaps": gaps,
                    "answer_boundary": {
                        "status": "reference_answer_outside_review_task",
                        "official": False,
                        "independently_verified": False,
                    },
                }
            )
        return result

    def _project_task(
        self,
        task: Mapping[str, Any],
        state: Mapping[str, Any] | None,
        *,
        detail: bool,
    ) -> dict[str, Any]:
        output: dict[str, Any] = {
            "schema_version": GATEWAY_SCHEMA_VERSION,
            "task_id": task["task_id"],
            "task_kind": task["task_kind"],
            "unit_kind": task["task_kind"],
            "priority": task["priority"],
            "paper_id": task["paper_id"],
            "theme_id": task["theme_id"],
            "candidate_theme_id": task["candidate_theme_id"],
            "title_zh": task["title_zh"],
            "status": self._status(state),
            "revision": state["revision"] if state is not None else None,
            "assignee": (
                state["claimed_by_principal_id"] if state is not None else None
            ),
            "boundary_mode": task["boundary_mode"],
            "candidate_basis_zh": task["candidate_basis_zh"],
            "source_binding": copy.deepcopy(task.get("source_binding")),
            "source_binding_candidates": copy.deepcopy(
                task.get("source_binding_candidates", [])
            ),
            "base_snapshot_sha256": task["base_snapshot_sha256"],
            "task_input_sha256": task["task_input_sha256"],
            "target_counts": {
                "printed_questions": len(task["target_printed_question_ids"]),
                "atomic_parts": len(task["target_atomic_part_ids"]),
            },
            "state": self._state_summary(state),
            "allowed_actions": self._allowed_actions(state),
            "base": {
                **copy.deepcopy(self._release_context),
                "task_input_sha256": task["task_input_sha256"],
            },
            "gap_counts": self._gap_counts(task),
            "write_capabilities": {
                "claim": REVIEW_TASK_WRITE_CAPABILITY,
                "release": REVIEW_TASK_WRITE_CAPABILITY,
                "submit_change_set": REVIEW_CANDIDATE_WRITE_CAPABILITY,
                "record_decision": REVIEW_DECISION_WRITE_CAPABILITY,
            },
            "authority": dict(_AUTHORITY),
        }
        if detail:
            latest_change_set = self._latest(state, "change_sets")
            latest_decision = self._latest(state, "decisions")
            output.update(
                {
                    "target_atomic_part_ids": copy.deepcopy(
                        task["target_atomic_part_ids"]
                    ),
                    "target_printed_question_ids": copy.deepcopy(
                        task["target_printed_question_ids"]
                    ),
                    "allowed_theme_ids": copy.deepcopy(task["allowed_theme_ids"]),
                    "current_tags": copy.deepcopy(task["current_tags"]),
                    "current_hierarchy": copy.deepcopy(task["current_hierarchy"]),
                    "current_dependencies": copy.deepcopy(
                        task["current_dependencies"]
                    ),
                    "source_order": copy.deepcopy(task["source_order"]),
                    "evidence_bindings": copy.deepcopy(task["evidence_bindings"]),
                    "node_evidence_ids": copy.deepcopy(task["node_evidence_ids"]),
                    "printed_questions": self._printed_projection(task),
                    "atomic_parts": self._atomic_projection(task, state),
                    "shared_materials": copy.deepcopy(task["shared_materials"]),
                    "change_sets": self._change_set_summaries(state),
                    "decisions": self._decision_summaries(state),
                    "latest_change_set": (
                        None
                        if latest_change_set is None
                        else self._change_set_summaries(
                            {"change_sets": [latest_change_set]}
                        )[0]
                    ),
                    "latest_decision": (
                        None
                        if latest_decision is None
                        else self._decision_summaries(
                            {"decisions": [latest_decision]}
                        )[0]
                    ),
                }
            )
        _assert_projection_safe(output)
        return output

    @staticmethod
    def _change_set_summaries(
        state: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if state is None:
            return []
        return [
            {
                "change_set_id": row["change_set_id"],
                "submitted_by_principal_id": row["submitted_by_principal_id"],
                "sequence": row["sequence"],
                "created_at_utc": row["created_at_utc"],
                "decision_id": row["decision_id"],
                "verdict": row["verdict"],
                "change_counts": {
                    name: len(row["request"][name])
                    for name in (
                        "tag_replacements",
                        "hierarchy_replacements",
                        "atomic_boundary_candidates",
                        "dependency_replacements",
                        "source_binding_candidates",
                    )
                    if name in row["request"]
                },
            }
            for row in state["change_sets"]
        ]

    @staticmethod
    def _decision_summaries(
        state: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if state is None:
            return []
        return [
            {
                "decision_id": row["decision_id"],
                "change_set_id": row["change_set_id"],
                "decided_by_principal_id": row["decided_by_principal_id"],
                "verdict": row["verdict"],
                "reason": row["reason"],
                "evidence_ids": copy.deepcopy(
                    row.get("evidence_binding_ids", [])
                ),
                "sequence": row["sequence"],
                "created_at_utc": row["created_at_utc"],
                "central_master_mutated": False,
                "human_reviewed": False,
            }
            for row in state["decisions"]
        ]

    def catalog_snapshot(self) -> dict[str, Any]:
        value = self.catalog.as_dict()
        output = {
            "schema_version": value["schema_version"],
            "catalog_sha256": self.catalog.sha256,
            "counts": value["counts"],
            "tasks": [
                self._project_task(task, None, detail=True)
                for task in value["tasks"]
            ],
            "authority": dict(_AUTHORITY),
            "integrity": value["integrity"],
        }
        _assert_projection_safe(output)
        return output

    def list(
        self,
        *,
        task_kind: str | None = None,
        paper_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if task_kind is not None and task_kind not in TASK_KINDS:
            raise ThemeReviewGatewayError(
                "theme_review_filter_invalid", "task_kind 无效", 400
            )
        if state not in {None, "available", "claimed", "has_changes", "decided"}:
            raise ThemeReviewGatewayError(
                "theme_review_filter_invalid", "state 无效", 400
            )
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ThemeReviewGatewayError(
                "theme_review_pagination_invalid", "limit/offset 无效", 400
            )
        if paper_id is not None:
            try:
                paper_id = validate_identifier(paper_id, "paper_id")
            except ThemeReviewError as exc:
                raise ThemeReviewGatewayError(exc.code, str(exc), exc.status_code) from exc
        states = self._states()
        rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for task in self.catalog.tasks:
            task_state = states.get(task["task_id"])
            if task_kind is not None and task["task_kind"] != task_kind:
                continue
            if paper_id is not None and task["paper_id"] != paper_id:
                continue
            if state == "available" and task_state is not None and task_state["claimed"]:
                continue
            if state == "claimed" and not (task_state and task_state["claimed"]):
                continue
            if state == "has_changes" and not (task_state and task_state["change_sets"]):
                continue
            if state == "decided" and not (task_state and task_state["decisions"]):
                continue
            rows.append((task, task_state))
        page = rows[offset : offset + limit]
        output = {
            "schema_version": GATEWAY_SCHEMA_VERSION,
            "catalog_sha256": self.catalog.sha256,
            "items": [
                self._project_task(task, task_state, detail=False)
                for task, task_state in page
            ],
            "count": len(page),
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "filters": {
                "task_kind": task_kind,
                "paper_id": paper_id,
                "state": state,
            },
            "authority": dict(_AUTHORITY),
        }
        _assert_projection_safe(output)
        return output

    list_tasks = list

    def get(self, task_id: str) -> dict[str, Any]:
        task = self.catalog.task(task_id)
        return self._project_task(task, self._state(task), detail=True)

    get_task = get

    @staticmethod
    def _validate_principal(principal_id: Any) -> str:
        try:
            principal = validate_identifier(principal_id, "principal_id")
        except ThemeReviewError as exc:
            raise ThemeReviewGatewayError(exc.code, str(exc), exc.status_code) from exc
        if principal == SYSTEM_CATALOG_PRINCIPAL:
            raise ThemeReviewGatewayError(
                "theme_review_principal_forbidden",
                "系统目录身份不能冒充教师复核者",
                403,
            )
        return principal

    @staticmethod
    def _mutation_output(
        task: Mapping[str, Any], result: Mapping[str, Any], operation: str
    ) -> dict[str, Any]:
        state = result["task_state"]
        output = {
            "schema_version": GATEWAY_SCHEMA_VERSION,
            "operation": operation,
            "task_id": task["task_id"],
            "event_id": result["event"]["event_id"],
            "event_sha256": result["event_sha256"],
            "commit_sha256": result["commit_sha256"],
            "idempotent_replay": result["idempotent_replay"],
            "state": ThemeReviewGateway._state_summary(state),
            "allowed_actions": ThemeReviewGateway._allowed_actions(state),
            "authority": dict(_AUTHORITY),
        }
        _assert_projection_safe(output)
        return output

    def claim(
        self, task_id: str, payload: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        request = _exact_keys(payload, CLAIM_FIELDS, "claim")
        principal = self._validate_principal(principal_id)
        task = self.catalog.task(task_id)
        state = self._state(task)
        expected = request["expected_revision"]
        if state is None:
            if expected is not None:
                raise ThemeReviewGatewayError(
                    "theme_review_revision_conflict",
                    "尚未注册的任务 expected_revision 必须为 null",
                    409,
                )
            state = self._ensure_registered(task)
            expected = state["revision"]
        elif expected is None:
            # A retry after task registration but before its first claim may
            # still use the catalog's null revision.  Any later event requires
            # the exact ledger revision.
            if state["sequence"] == 1 and not state["claimed"]:
                expected = state["revision"]
            else:
                raise ThemeReviewGatewayError(
                    "theme_review_revision_conflict",
                    "任务已进入可变账本，请使用当前 revision",
                    409,
                )
        core_body = {
            "idempotency_key": request["idempotency_key"],
            "expected_revision": expected,
        }
        try:
            result = self._store_for_use().claim_task(
                task_id, core_body, principal_id=principal
            )
        except ThemeReviewError as exc:
            raise self._map_core_error(exc) from exc
        self._assert_state_matches_task(result["task_state"], task)
        return self._mutation_output(task, result, "claim")

    claim_task = claim

    def release(
        self, task_id: str, payload: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        request = _exact_keys(payload, _BROWSER_RELEASE_FIELDS, "release")
        principal = self._validate_principal(principal_id)
        task = self.catalog.task(task_id)
        state = self._state(task)
        if state is None:
            raise ThemeReviewGatewayError(
                "theme_review_not_found", "任务尚未进入复核账本", 404
            )
        reason = self._browser_text(request["reason_zh"], "release.reason_zh")
        core_request = {
            "idempotency_key": request["idempotency_key"],
            "expected_revision": request["expected_revision"],
            "reason": reason,
        }
        try:
            result = self._store_for_use().release_task(
                task_id, core_request, principal_id=principal
            )
        except ThemeReviewError as exc:
            raise self._map_core_error(exc) from exc
        self._assert_state_matches_task(result["task_state"], task)
        return self._mutation_output(task, result, "release")

    release_task = release

    @staticmethod
    def _current_maps(task: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "tags": {
                row["atomic_part_id"]: row["values"] for row in task["current_tags"]
            },
            "printed_parent": {
                row["printed_question_id"]: row["parent_theme_big_question_id"]
                for row in task["current_hierarchy"]["printed_questions"]
            },
            "atomic_parent": {
                row["atomic_part_id"]: row["parent_printed_question_id"]
                for row in task["current_hierarchy"]["atomic_parts"]
            },
            "dependencies": {
                row["atomic_part_id"]: row for row in task["current_dependencies"]
            },
            "positions": {
                row["atomic_part_id"]: row for row in task["source_order"]
            },
            "node_evidence": {
                row["node_id"]: set(row["evidence_binding_ids"])
                for row in task["node_evidence_ids"]
            },
        }

    @staticmethod
    def _require_node_evidence(
        row: Mapping[str, Any], node_id: str, maps: Mapping[str, Any]
    ) -> None:
        evidence = row.get("evidence_binding_ids")
        if type(evidence) is not list or not evidence:
            raise ThemeReviewGatewayError(
                "theme_review_evidence_required", "每项变更必须绑定证据", 400
            )
        if not set(evidence).intersection(maps["node_evidence"].get(node_id, set())):
            raise ThemeReviewGatewayError(
                "theme_review_evidence_outside_node",
                "变更未绑定目标节点的冻结证据",
                400,
            )

    @staticmethod
    def _browser_text(value: Any, name: str, *, maximum: int = 2000) -> str:
        if type(value) is not str or not value.strip() or value != value.strip():
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", f"{name} 必须是非空中文说明", 400
            )
        if len(value) > maximum or "\x00" in value or _HOST_OR_URL_RE.search(value):
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", f"{name} 含路径、URL 或超长文本", 400
            )
        return value

    @staticmethod
    def _browser_evidence(
        value: Any,
        *,
        node_id: str,
        maps: Mapping[str, Any],
        name: str,
    ) -> list[str]:
        if type(value) is not list or not value or any(type(item) is not str for item in value):
            raise ThemeReviewGatewayError(
                "theme_review_evidence_required", f"{name} 必须是非空证据 ID 数组", 400
            )
        if len(value) != len(set(value)):
            raise ThemeReviewGatewayError(
                "theme_review_evidence_required", f"{name} 不得重复", 400
            )
        ThemeReviewGateway._require_node_evidence(
            {"evidence_binding_ids": value}, node_id, maps
        )
        return list(value)

    def _browser_change_set_to_core(
        self, task: Mapping[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Derive every before-value server-side, then validate as one batch."""

        is_v2 = "source_binding_candidates" in task
        request = _exact_keys(
            payload,
            (
                _BROWSER_CHANGE_SET_FIELDS_V2
                if is_v2
                else _BROWSER_CHANGE_SET_FIELDS_V1
            ),
            "browser_change_set",
        )
        if request["base_task_input_sha256"] != task["task_input_sha256"]:
            raise ThemeReviewGatewayError(
                "theme_review_base_snapshot_stale", "任务输入哈希已过期", 409
            )
        array_names = [
            "tag_replacements",
            "hierarchy_replacements",
            "atomic_boundary_candidates",
            "dependency_replacements",
        ]
        if is_v2:
            array_names.append("source_binding_candidates")
        for name in array_names:
            if type(request[name]) is not list:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", f"{name} 必须是数组", 400
                )
        maps = self._current_maps(task)
        reasons: list[str] = []

        tag_rows: list[dict[str, Any]] = []
        for index, row in enumerate(request["tag_replacements"]):
            row = _exact_keys(row, _BROWSER_TAG_FIELDS, f"tag_replacements[{index}]")
            atomic_id = row["atomic_part_id"]
            current = maps["tags"].get(atomic_id)
            if current is None or type(row["changes"]) is not dict or not row["changes"]:
                raise ThemeReviewGatewayError(
                    "theme_review_tag_target_invalid", "标签目标或 changes 无效", 400
                )
            reason = self._browser_text(row["reason_zh"], "tag.reason_zh")
            evidence = self._browser_evidence(
                row["evidence_ids"],
                node_id=atomic_id,
                maps=maps,
                name="tag.evidence_ids",
            )
            reasons.append(reason)
            for field, after in row["changes"].items():
                if field not in current:
                    raise ThemeReviewGatewayError(
                        "theme_review_tag_target_invalid", "标签字段不在闭集", 400
                    )
                tag_rows.append(
                    {
                        "atomic_part_id": atomic_id,
                        "field": field,
                        "before": copy.deepcopy(current[field]),
                        "after": copy.deepcopy(after),
                        "evidence_binding_ids": copy.deepcopy(evidence),
                    }
                )

        hierarchy_rows: list[dict[str, Any]] = []
        for index, row in enumerate(request["hierarchy_replacements"]):
            row = _exact_keys(
                row,
                _BROWSER_HIERARCHY_FIELDS,
                f"hierarchy_replacements[{index}]",
            )
            printed_id = row["printed_question_id"]
            if printed_id not in maps["printed_parent"]:
                raise ThemeReviewGatewayError(
                    "theme_review_hierarchy_target_invalid", "printed 不在任务中", 400
                )
            reason = self._browser_text(row["reason_zh"], "hierarchy.reason_zh")
            evidence = self._browser_evidence(
                row["evidence_ids"],
                node_id=printed_id,
                maps=maps,
                name="hierarchy.evidence_ids",
            )
            reasons.append(reason)
            hierarchy_rows.append(
                {
                    "node_type": "printed_question",
                    "node_id": printed_id,
                    "parent_field": "theme_id",
                    "before_parent_id": maps["printed_parent"][printed_id],
                    "after_parent_id": row["proposed_theme_id"],
                    "evidence_binding_ids": evidence,
                }
            )

        boundary_rows: list[dict[str, Any]] = []
        for index, row in enumerate(request["atomic_boundary_candidates"]):
            row = _exact_keys(
                row,
                _BROWSER_BOUNDARY_FIELDS,
                f"atomic_boundary_candidates[{index}]",
            )
            printed_id = row["printed_question_id"]
            if printed_id not in maps["printed_parent"] or row["operation"] != "split_into_atomic_parts":
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_candidate_invalid",
                    "边界候选只支持任务内 printed 的 split_into_atomic_parts",
                    400,
                )
            reason = self._browser_text(row["reason_zh"], "boundary.reason_zh")
            evidence = self._browser_evidence(
                row["evidence_ids"],
                node_id=printed_id,
                maps=maps,
                name="boundary.evidence_ids",
            )
            reasons.append(reason)
            candidates = row["candidate_atomic_parts"]
            if type(candidates) is not list or not candidates:
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_candidate_invalid", "拆题候选不能为空", 400
                )
            core_candidates: list[dict[str, Any]] = []
            client_keys: set[str] = set()
            for child_index, child in enumerate(candidates):
                child = _exact_keys(
                    child,
                    _BROWSER_BOUNDARY_CHILD_FIELDS,
                    f"candidate_atomic_parts[{child_index}]",
                )
                try:
                    client_key = validate_identifier(child["client_key"], "client_key")
                except ThemeReviewError as exc:
                    raise ThemeReviewGatewayError(exc.code, str(exc), exc.status_code) from exc
                if client_key in client_keys:
                    raise ThemeReviewGatewayError(
                        "theme_review_boundary_candidate_invalid", "client_key 重复", 400
                    )
                client_keys.add(client_key)
                source_order = child["source_order"]
                if type(source_order) is not int or type(source_order) is bool or source_order < 1:
                    raise ThemeReviewGatewayError(
                        "theme_review_boundary_candidate_invalid", "source_order 必须为正整数", 400
                    )
                response_requirement = self._browser_text(
                    child["response_requirement_zh"],
                    "candidate_atomic_part.response_requirement_zh",
                )
                reasons.append(response_requirement)
                identity = {
                    "task_id": task["task_id"],
                    "printed_question_id": printed_id,
                    "client_key": client_key,
                    "source_order": source_order,
                }
                core_candidates.append(
                    {
                        "candidate_atomic_part_id": (
                            f"TRATOM-{_sha256(canonical_json_bytes(identity))}"
                        ),
                        "source_order": source_order,
                        "prompt_locator_id": evidence[0],
                    }
                )
            before_ids = [
                atomic_id
                for atomic_id, parent_id in maps["atomic_parent"].items()
                if parent_id == printed_id
            ]
            boundary_rows.append(
                {
                    "printed_question_id": printed_id,
                    "before_atomic_part_ids": before_ids,
                    "candidate_atomic_parts": core_candidates,
                    "evidence_binding_ids": evidence,
                }
            )

        dependency_rows: list[dict[str, Any]] = []
        for index, row in enumerate(request["dependency_replacements"]):
            row = _exact_keys(
                row,
                _BROWSER_DEPENDENCY_FIELDS,
                f"dependency_replacements[{index}]",
            )
            atomic_id = row["dependent_atomic_part_id"]
            current = maps["dependencies"].get(atomic_id)
            if current is None:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_target_invalid", "依赖目标不在任务中", 400
                )
            prior_ids = row["prior_atomic_part_ids"]
            kinds = row["relationship_kinds"]
            if (
                type(prior_ids) is not list
                or type(kinds) is not list
                or len(prior_ids) != len(kinds)
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_invalid",
                    "prior_atomic_part_ids 与 relationship_kinds 必须等长",
                    400,
                )
            reason = self._browser_text(row["reason_zh"], "dependency.reason_zh")
            evidence = self._browser_evidence(
                row["evidence_ids"],
                node_id=atomic_id,
                maps=maps,
                name="dependency.evidence_ids",
            )
            reasons.append(reason)
            dependency_rows.append(
                {
                    "atomic_part_id": atomic_id,
                    "before_dependencies": copy.deepcopy(current["dependencies"]),
                    "after_dependencies": [
                        {
                            "atomic_part_id": prior_id,
                            "relationship_kind": relationship,
                        }
                        for prior_id, relationship in zip(prior_ids, kinds, strict=True)
                    ],
                    "evidence_binding_ids": evidence,
                }
            )

        source_binding_rows: list[dict[str, Any]] = []
        if is_v2:
            frozen_candidates = {
                row["candidate_id"]: row
                for row in task["source_binding_candidates"]
            }
            seen_candidate_ids: set[str] = set()
            for index, row in enumerate(request["source_binding_candidates"]):
                row = _exact_keys(
                    row,
                    _BROWSER_SOURCE_BINDING_FIELDS,
                    f"source_binding_candidates[{index}]",
                )
                candidate_id = row["candidate_id"]
                candidate = frozen_candidates.get(candidate_id)
                if candidate is None or candidate_id in seen_candidate_ids:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_candidate_invalid",
                        "来源绑定候选不在冻结任务中或被重复提交",
                        409,
                    )
                seen_candidate_ids.add(candidate_id)
                if (
                    row["candidate_sha256"] != candidate["candidate_sha256"]
                    or row["source_version_id"] != candidate["source_version_id"]
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_stale",
                        "来源绑定候选哈希或版本已过期",
                        409,
                    )
                action = row["action"]
                if type(action) is not str or action not in SOURCE_BINDING_ACTIONS:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_action_invalid",
                        "来源绑定动作不在受控词表中",
                        400,
                    )
                if action == "accept_binding_candidate" and not candidate[
                    "accept_allowed"
                ]:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_blocked",
                        "当前来源证据不足，阻断候选不得接受",
                        409,
                    )
                evidence = row["evidence_ids"]
                if (
                    type(evidence) is not list
                    or not evidence
                    or any(type(item) is not str for item in evidence)
                    or len(evidence) != len(set(evidence))
                    or not set(evidence)
                    <= set(candidate["evidence_binding_ids"])
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_evidence_required",
                        "来源绑定动作必须引用该冻结候选的非空证据",
                        400,
                    )
                reason = self._browser_text(
                    row["reason_zh"], "source_binding.reason_zh"
                )
                reasons.append(reason)
                source_binding_rows.append(
                    {
                        "candidate_id": candidate_id,
                        "candidate_sha256": candidate["candidate_sha256"],
                        "source_version_id": candidate["source_version_id"],
                        "action": action,
                        "reason": reason,
                        "evidence_binding_ids": list(evidence),
                    }
                )

        if not reasons:
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", "change-set 不能为空", 400
            )
        unique_reasons = list(dict.fromkeys(reasons))
        combined_reason = "；".join(unique_reasons)
        if len(combined_reason) > 4000:
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", "change-set 中文理由合计过长", 400
            )
        core = {
            "idempotency_key": request["idempotency_key"],
            "expected_revision": request["expected_revision"],
            "base_snapshot_sha256": task["base_snapshot_sha256"],
            "reason": combined_reason,
            "tag_replacements": tag_rows,
            "hierarchy_replacements": hierarchy_rows,
            "atomic_boundary_candidates": boundary_rows,
            "dependency_replacements": dependency_rows,
        }
        if is_v2:
            core["source_binding_candidates"] = source_binding_rows
        return self._validate_core_change_set(task, core)

    def _validate_core_change_set(
        self, task: Mapping[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        is_v2 = "source_binding_candidates" in task
        request = _exact_keys(
            payload,
            CHANGE_SET_FIELDS_V2 if is_v2 else CHANGE_SET_FIELDS,
            "change_set",
        )
        if request["base_snapshot_sha256"] != task["base_snapshot_sha256"]:
            raise ThemeReviewGatewayError(
                "theme_review_base_snapshot_stale", "change-set 基线哈希已过期", 409
            )
        arrays = [
            "tag_replacements",
            "hierarchy_replacements",
            "atomic_boundary_candidates",
            "dependency_replacements",
        ]
        if is_v2:
            arrays.append("source_binding_candidates")
        if any(type(request[name]) is not list for name in arrays):
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", "候选变更必须是数组", 400
            )
        if not any(request[name] for name in arrays):
            raise ThemeReviewGatewayError(
                "theme_review_contract_invalid", "change-set 不能为空", 400
            )
        if task["boundary_mode"] == BOUNDARY_MODE_ATOMIC_ONLY and (
            request["tag_replacements"]
            or request["hierarchy_replacements"]
            or request["dependency_replacements"]
        ):
            raise ThemeReviewGatewayError(
                "theme_review_boundary_only",
                "无 atomic 的印刷题只允许提交拆题边界候选",
                409,
            )

        maps = self._current_maps(task)
        axes = {
            key: frozenset(values) for key, values in self.catalog.taxonomy_axes.items()
        }
        tag_changes_by_atomic: dict[str, dict[str, Any]] = defaultdict(dict)
        for row in request["tag_replacements"]:
            if type(row) is not dict:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", "标签变更项必须是对象", 400
                )
            atomic_id = row.get("atomic_part_id")
            field = row.get("field")
            if atomic_id not in maps["tags"] or field not in maps["tags"][atomic_id]:
                raise ThemeReviewGatewayError(
                    "theme_review_tag_target_invalid", "标签变更超出任务或字段词表", 400
                )
            self._require_node_evidence(row, atomic_id, maps)
            if row.get("before") != maps["tags"][atomic_id][field]:
                raise ThemeReviewGatewayError(
                    "theme_review_before_value_stale", "标签 before 与冻结基线不一致", 409
                )
            if field in tag_changes_by_atomic[atomic_id]:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", "同一标签字段不得重复替换", 400
                )
            tag_changes_by_atomic[atomic_id][field] = copy.deepcopy(row.get("after"))
        try:
            for changes in tag_changes_by_atomic.values():
                validate_changes(changes, axes)
        except TagContractError as exc:
            raise ThemeReviewGatewayError(
                "theme_review_tag_vocabulary_invalid", str(exc), 400
            ) from exc

        allowed_themes = set(task["allowed_theme_ids"])
        if task["theme_id"] not in allowed_themes and task["boundary_mode"] != BOUNDARY_MODE_ATOMIC_ONLY:
            raise ThemeReviewGatewayError(
                "theme_review_same_paper_allowlist_invalid",
                "任务候选 theme 不在同 paper allowlist",
                409,
            )
        for row in request["hierarchy_replacements"]:
            if type(row) is not dict:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", "父链变更项必须是对象", 400
                )
            node_type = row.get("node_type")
            node_id = row.get("node_id")
            self._require_node_evidence(row, node_id, maps)
            if node_type == "printed_question":
                sentinel = object()
                current = maps["printed_parent"].get(node_id, sentinel)
                if current is sentinel or row.get("parent_field") != "theme_id":
                    raise ThemeReviewGatewayError(
                        "theme_review_hierarchy_target_invalid", "printed 父链目标无效", 400
                    )
                if (
                    row.get("before_parent_id") != current
                    or row.get("after_parent_id") != task["theme_id"]
                    or row.get("after_parent_id") not in allowed_themes
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_hierarchy_same_paper_violation",
                        "printed 父链必须从冻结值变更到同 paper 的任务候选 theme",
                        409,
                    )
            elif node_type == "atomic_part":
                current = maps["atomic_parent"].get(node_id)
                if (
                    current is None
                    or row.get("parent_field") != "printed_question_id"
                    or row.get("before_parent_id") != current
                    or row.get("after_parent_id") not in maps["printed_parent"]
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_hierarchy_target_invalid", "atomic 父链目标无效", 409
                    )
            else:
                raise ThemeReviewGatewayError(
                    "theme_review_hierarchy_target_invalid", "父链 node_type 无效", 400
                )

        for row in request["atomic_boundary_candidates"]:
            if type(row) is not dict:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", "拆题边界项必须是对象", 400
                )
            printed_id = row.get("printed_question_id")
            if printed_id not in maps["printed_parent"]:
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_target_invalid", "拆题目标超出任务", 400
                )
            self._require_node_evidence(row, printed_id, maps)
            current_children = [
                atomic_id
                for atomic_id, parent in maps["atomic_parent"].items()
                if parent == printed_id
            ]
            if row.get("before_atomic_part_ids") != current_children:
                raise ThemeReviewGatewayError(
                    "theme_review_before_value_stale", "拆题 before 与冻结基线不一致", 409
                )
            candidates = row.get("candidate_atomic_parts")
            if type(candidates) is not list or not candidates:
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_candidate_invalid", "拆题候选不能为空", 400
                )
            candidate_ids = [child.get("candidate_atomic_part_id") for child in candidates if type(child) is dict]
            if (
                len(candidate_ids) != len(candidates)
                or len(candidate_ids) != len(set(candidate_ids))
                or set(candidate_ids).intersection(self._all_atomic_ids)
            ):
                raise ThemeReviewGatewayError(
                    "theme_review_boundary_candidate_collision",
                    "候选 atomic ID 重复或与 Master470 冲突",
                    409,
                )

        graph: dict[str, list[dict[str, str]]] = {}
        for atomic_id, row in maps["dependencies"].items():
            if row["dependencies"] is None:
                graph[atomic_id] = []
            else:
                graph[atomic_id] = copy.deepcopy(row["dependencies"])
        seen_dependency_targets: set[str] = set()
        for row in request["dependency_replacements"]:
            if type(row) is not dict:
                raise ThemeReviewGatewayError(
                    "theme_review_contract_invalid", "依赖变更项必须是对象", 400
                )
            atomic_id = row.get("atomic_part_id")
            current = maps["dependencies"].get(atomic_id)
            if current is None or atomic_id in seen_dependency_targets:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_target_invalid", "依赖目标超出任务或重复", 400
                )
            seen_dependency_targets.add(atomic_id)
            self._require_node_evidence(row, atomic_id, maps)
            if current["dependencies"] is None:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_kind_missing",
                    "冻结基线仅记录了未定类型的前序边，需先补证据后再替换",
                    409,
                )
            if row.get("before_dependencies") != current["dependencies"]:
                raise ThemeReviewGatewayError(
                    "theme_review_before_value_stale", "依赖 before 与冻结基线不一致", 409
                )
            after = row.get("after_dependencies")
            if type(after) is not list:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_invalid", "after_dependencies 必须是数组", 400
                )
            target_position = maps["positions"].get(atomic_id)
            if not target_position or not target_position["explicit"]:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_order_unknown",
                    "目标 atomic 缺少显式题序，不能建立依赖边",
                    409,
                )
            pairs: set[tuple[str, str]] = set()
            for edge in after:
                if type(edge) is not dict or set(edge) != {
                    "atomic_part_id",
                    "relationship_kind",
                }:
                    raise ThemeReviewGatewayError(
                        "theme_review_dependency_invalid", "依赖边字段无效", 400
                    )
                prior_id = edge["atomic_part_id"]
                relationship = edge["relationship_kind"]
                prior_position = maps["positions"].get(prior_id)
                pair = (prior_id, relationship)
                if (
                    relationship not in DEPENDENCY_RELATIONSHIP_KINDS
                    or prior_position is None
                    or not prior_position["explicit"]
                    or prior_id == atomic_id
                    or prior_position["position"] >= target_position["position"]
                    or pair in pairs
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_dependency_not_strictly_forward",
                        "依赖必须在同一候选主题内严格指向前序 atomic，且类型受控、无 self/重复",
                        409,
                    )
                pairs.add(pair)
            graph[atomic_id] = copy.deepcopy(after)

        # A strict-forward graph is already acyclic, but keep an independent
        # cycle check so future ordering changes cannot silently weaken it.
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ThemeReviewGatewayError(
                    "theme_review_dependency_cycle", "依赖图存在环", 409
                )
            if node_id in visited:
                return
            visiting.add(node_id)
            for edge in graph.get(node_id, []):
                visit(edge["atomic_part_id"])
            visiting.remove(node_id)
            visited.add(node_id)

        for atomic_id in graph:
            visit(atomic_id)

        if is_v2:
            frozen_candidates = {
                row["candidate_id"]: row
                for row in task["source_binding_candidates"]
            }
            seen_source_candidates: set[str] = set()
            for row in request["source_binding_candidates"]:
                if type(row) is not dict or set(row) != {
                    "candidate_id",
                    "candidate_sha256",
                    "source_version_id",
                    "action",
                    "reason",
                    "evidence_binding_ids",
                }:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_candidate_invalid",
                        "来源绑定候选字段无效",
                        400,
                    )
                candidate_id = row["candidate_id"]
                candidate = frozen_candidates.get(candidate_id)
                if candidate is None or candidate_id in seen_source_candidates:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_candidate_invalid",
                        "来源绑定候选不在冻结任务中或被重复提交",
                        409,
                    )
                seen_source_candidates.add(candidate_id)
                if (
                    row["candidate_sha256"] != candidate["candidate_sha256"]
                    or row["source_version_id"] != candidate["source_version_id"]
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_stale",
                        "来源绑定候选哈希或版本已过期",
                        409,
                    )
                if row["action"] not in SOURCE_BINDING_ACTIONS:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_action_invalid",
                        "来源绑定动作不在受控词表中",
                        400,
                    )
                if row["action"] == "accept_binding_candidate" and not candidate[
                    "accept_allowed"
                ]:
                    raise ThemeReviewGatewayError(
                        "theme_review_source_binding_blocked",
                        "当前来源证据不足，阻断候选不得接受",
                        409,
                    )
                evidence = row["evidence_binding_ids"]
                if (
                    type(evidence) is not list
                    or not evidence
                    or any(type(item) is not str for item in evidence)
                    or len(evidence) != len(set(evidence))
                    or not set(evidence)
                    <= set(candidate["evidence_binding_ids"])
                ):
                    raise ThemeReviewGatewayError(
                        "theme_review_evidence_required",
                        "来源绑定动作必须引用该冻结候选的非空证据",
                        400,
                    )
                self._browser_text(row["reason"], "source_binding.reason")

        _assert_projection_safe(request)
        return copy.deepcopy(request)

    def change_set(
        self, task_id: str, payload: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        principal = self._validate_principal(principal_id)
        task = self.catalog.task(task_id)
        state = self._state(task)
        if state is None:
            raise ThemeReviewGatewayError(
                "theme_review_not_found", "任务尚未进入复核账本", 404
            )
        request = self._browser_change_set_to_core(task, payload)
        try:
            result = self._store_for_use().submit_change_set(
                task_id, request, principal_id=principal
            )
        except ThemeReviewError as exc:
            raise self._map_core_error(exc) from exc
        self._assert_state_matches_task(result["task_state"], task)
        output = self._mutation_output(task, result, "submit_change_set")
        output["change_set_id"] = result["event"]["payload"]["change_set_id"]
        output["candidate_overlay_only"] = True
        _assert_projection_safe(output)
        return output

    submit_change_set = change_set

    def preview(self, task_id: str, change_set_id: str) -> dict[str, Any]:
        task = self.catalog.task(task_id)
        state = self._state(task)
        if state is None:
            raise ThemeReviewGatewayError(
                "theme_review_not_found", "任务尚未进入复核账本", 404
            )
        try:
            preview = self._store_for_use().preview_candidate_overlay(
                task_id, change_set_id
            )
        except ThemeReviewError as exc:
            raise self._map_core_error(exc) from exc
        revalidation = {
            "idempotency_key": "preview-validation-only",
            "expected_revision": state["revision"],
            "base_snapshot_sha256": preview["base_snapshot_sha256"],
            "reason": preview["reason"],
            "tag_replacements": preview["tag_replacements"],
            "hierarchy_replacements": preview["hierarchy_replacements"],
            "atomic_boundary_candidates": preview["atomic_boundary_candidates"],
            "dependency_replacements": preview["dependency_replacements"],
        }
        if "source_binding_candidates" in task:
            revalidation["source_binding_candidates"] = copy.deepcopy(
                preview.get("source_binding_candidates", [])
            )
        self._validate_core_change_set(task, revalidation)
        candidate_overlay_preview = {
            "schema_version": GATEWAY_SCHEMA_VERSION,
            "task_id": task_id,
            "task_kind": task["task_kind"],
            "paper_id": task["paper_id"],
            "theme_id": task["theme_id"],
            "candidate_theme_id": task["candidate_theme_id"],
            "change_set_id": preview["change_set_id"],
            "base_snapshot_sha256": preview["base_snapshot_sha256"],
            "reason": preview["reason"],
            "tag_replacements": preview["tag_replacements"],
            "hierarchy_replacements": preview["hierarchy_replacements"],
            "atomic_boundary_candidates": preview["atomic_boundary_candidates"],
            "dependency_replacements": preview["dependency_replacements"],
            "source_binding_candidates": copy.deepcopy(
                preview.get("source_binding_candidates", [])
            ),
            "decision_id": preview["decision_id"],
            "decision_evidence_ids": copy.deepcopy(
                preview.get("decision_evidence_binding_ids", [])
            ),
            "verdict": preview["verdict"],
            "overlay_status": preview["overlay_status"],
            "eligible_for_central_apply": False,
        }
        output = {
            "schema_version": GATEWAY_SCHEMA_VERSION,
            "candidate_overlay_preview": candidate_overlay_preview,
            "validation": {
                "valid": True,
                "status": "pass",
                "task_catalog_binding_current": True,
                "same_paper_hierarchy_validated": True,
                "controlled_tag_vocabulary_validated": True,
                "strict_forward_dependency_graph_validated": True,
                "frozen_source_binding_candidate_validated": (
                    "source_binding_candidates" in task
                ),
                "central_apply_available": False,
            },
            "authority": dict(_AUTHORITY),
        }
        _assert_projection_safe(output)
        return output

    preview_candidate_overlay = preview

    def decision(
        self, task_id: str, payload: dict[str, Any], *, principal_id: str
    ) -> dict[str, Any]:
        request = _exact_keys(payload, _BROWSER_DECISION_FIELDS, "decision")
        principal = self._validate_principal(principal_id)
        task = self.catalog.task(task_id)
        state = self._state(task)
        if state is None:
            raise ThemeReviewGatewayError(
                "theme_review_not_found", "任务尚未进入复核账本", 404
            )
        evidence = request["evidence_ids"]
        all_evidence = {
            row["evidence_binding_id"] for row in task["evidence_bindings"]
        }
        if (
            type(evidence) is not list
            or not evidence
            or any(type(item) is not str for item in evidence)
            or len(evidence) != len(set(evidence))
            or not set(evidence) <= all_evidence
        ):
            raise ThemeReviewGatewayError(
                "theme_review_evidence_required",
                "教师决定必须绑定当前任务中的非空证据 ID",
                400,
            )
        change_set = next(
            (
                row
                for row in state["change_sets"]
                if row["change_set_id"] == request["change_set_id"]
            ),
            None,
        )
        if change_set is None:
            raise ThemeReviewGatewayError(
                "theme_review_not_found", "教师决定引用的 change-set 不存在", 404
            )
        change_evidence = {
            evidence_id
            for name in (
                "tag_replacements",
                "hierarchy_replacements",
                "atomic_boundary_candidates",
                "dependency_replacements",
                "source_binding_candidates",
            )
            for row in change_set["request"].get(name, [])
            for evidence_id in row["evidence_binding_ids"]
        }
        if not set(evidence).intersection(change_evidence):
            raise ThemeReviewGatewayError(
                "theme_review_evidence_required",
                "教师决定证据必须与所选 change-set 的冻结证据相交",
                400,
            )
        reason = self._browser_text(request["reason_zh"], "decision.reason_zh")
        core_request = {
            "idempotency_key": request["idempotency_key"],
            "expected_revision": request["expected_revision"],
            "change_set_id": request["change_set_id"],
            "verdict": request["verdict"],
            "reason": reason,
            "evidence_binding_ids": copy.deepcopy(evidence),
        }
        try:
            result = self._store_for_use().create_decision(
                task_id, core_request, principal_id=principal
            )
        except ThemeReviewError as exc:
            raise self._map_core_error(exc) from exc
        self._assert_state_matches_task(result["task_state"], task)
        output = self._mutation_output(task, result, "record_decision")
        output["decision_id"] = result["event"]["payload"]["decision_id"]
        output["change_set_id"] = result["event"]["payload"]["change_set_id"]
        output["verdict"] = result["event"]["payload"]["verdict"]
        output["evidence_ids"] = copy.deepcopy(evidence)
        output["candidate_overlay_only"] = True
        _assert_projection_safe(output)
        return output

    record_decision = decision


__all__ = [
    "ALLOWED_ACTION_KEYS",
    "BOUNDARY_MODE_ATOMIC_ONLY",
    "BOUNDARY_MODE_CANDIDATE_THEME",
    "BOUNDARY_MODE_THEME",
    "BOUNDARY_TASK",
    "CATALOG_SCHEMA_VERSION",
    "DEPENDENCY_RELATIONSHIP_KINDS",
    "GATEWAY_SCHEMA_VERSION",
    "REVIEW_CANDIDATE_WRITE_CAPABILITY",
    "REVIEW_DECISION_WRITE_CAPABILITY",
    "REVIEW_TASK_WRITE_CAPABILITY",
    "TASK_KINDS",
    "THEME_TASK",
    "ThemeReviewGateway",
    "ThemeReviewGatewayError",
    "ThemeReviewTaskCatalog",
]
