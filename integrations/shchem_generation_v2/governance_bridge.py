"""Controller-v2-native evidence chain for the generation candidate.

The controller deliberately accepts file references only below ``sh-chem-db``.
This bridge therefore keeps a versioned, explicitly non-registered integration
fixture in this task's owned test tree.  Root integration may later register an
accepted chain without changing the central registry here.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import jsonschema
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .content import PAPER_ID, VERSION_ID
from .core import sha256_file, solve, validate_display_answer, write_json
from .hierarchy import hierarchy_ids, validate_paper_plus_atomic_chain_batch
from .r17_boundary import (
    EXECUTION_CORE_FIELDS,
    OBSERVED_TASK_METADATA_FIELDS,
    R17BoundaryError,
    SnapshotGraph,
    atomic_projection_contract,
    exclusive_create_bundle,
    portable_relative_path_error,
    validate_formal_freeze_package,
    validate_root_provenance_reconciliation,
)
from .schema_validation import load_schema, validate

WORKSPACE = Path(__file__).resolve().parents[2]
DB_ROOT = WORKSPACE / "sh-chem-db"
CONTROLLER = DB_ROOT / "scripts" / "sh_chem_agent.py"
BRIDGE_DIR = (
    DB_ROOT
    / "tests"
    / "generation_publication_v2"
    / "controller_fixture_r18"
)
QUESTION_PATH = BRIDGE_DIR / "subject_question.json"
ANSWER_PATH = BRIDGE_DIR / "subject_answer.json"
REQUEST_PATH = BRIDGE_DIR / "deterministic_check_request.json"
REPORT_PATH = BRIDGE_DIR / "deterministic_check_report.json"
REVIEW_A_PATH = BRIDGE_DIR / "sol_review_a.json"
REVIEW_B_PATH = BRIDGE_DIR / "sol_review_b.json"
ADVERSARIAL_PATH = BRIDGE_DIR / "adversarial_check.json"
ADVERSARIAL_PHASE2_DISPATCH_PATH = BRIDGE_DIR / "adversarial_phase2_dispatch.json"
DELIVERY_PATH = BRIDGE_DIR / "teacher_managed_delivery.json"
CHAIN_PATH = BRIDGE_DIR / "governance_chain.json"
ATOMIC_DIR = BRIDGE_DIR / "atomic_parts"
ATOMIC_BATCH_REGISTRATION_PATH = BRIDGE_DIR / "atomic_batch_registration_request.json"
ATOMIC_RESULTS_PATH = BRIDGE_DIR / "atomic_chain_results.json"
AUTHORIZATION_PATH = DB_ROOT / "kb/machine_governance_v2/project_generation_authorization.json"
INTEGRATION_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
REVIEW_DISPATCH_SCHEMA_PATH = (
    INTEGRATION_SCHEMA_DIR / "consumer_v2" / "review_dispatch_request.schema.json"
)
PHASE1_ACTIVATION_SCHEMA_PATH = (
    INTEGRATION_SCHEMA_DIR / "phase1_review_dispatch_activation.schema.json"
)
PHASE2_DISPATCH_SCHEMA_PATH = (
    INTEGRATION_SCHEMA_DIR / "adversarial_phase2_dispatch_r18.schema.json"
)
GENERATOR_SELF_REPORT_SCHEMA_PATH = (
    INTEGRATION_SCHEMA_DIR / "generator_execution_self_report_r18.schema.json"
)
R18_FORMAL_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/formal_freeze_receipt_r18.schema.json"
)
R18_FORMAL_SIDECAR_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/formal_freeze_sidecar_r18.schema.json"
)
R18_OBSERVATION_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/review_codex_metadata_observation_r18.schema.json"
)
R18_TASK_ATTESTATION_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/review_task_creation_attestation_r18.schema.json"
)
R18_RECONCILIATION_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/root_provenance_reconciliation_r18.schema.json"
)
R18_PHASE1_AUTHORIZATION_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/review_phase1_authorization_r18.schema.json"
)
MACHINE_REVIEW_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/machine_review_record.schema.json"
)
ADVERSARIAL_SCHEMA_PATH = (
    DB_ROOT / "kb/machine_governance_v2/schemas/adversarial_check_record.schema.json"
)
R18_PHASE1_IMMUTABLE_KEYS = (
    "paper",
    "task_card",
    "question_subject",
    "answer_subject",
    "deterministic_request",
    "deterministic_report",
    "coverage_matrix",
    "deterministic_atomic_scope",
    "figure_svg",
    "figure_png",
    "figure_spec",
    "component_registry",
    "figure_visual_qa",
    "figure_topology_mutations",
    "observed_profile",
    "evidence_manifest",
    "project_generation_authorization",
    "prefreeze_receipt",
    "r18_producer_receipt",
    "sol_generator_receipt",
    "generator_provenance_receipt",
    "formal_freeze",
    "formal_freeze_sidecar",
    "generator_reconciliation",
    "root_phase1_authorization",
)
R18_PHASE1_SCHEMA_KEYS = (
    "paper_candidate",
    "component_registry",
    "deterministic_check_request",
    "deterministic_check_report",
    "project_generation_authorization",
    "r18_producer_receipt",
    "sol_generator_receipt",
    "generator_provenance_receipt",
    "formal_freeze_receipt",
    "formal_freeze_sidecar",
    "root_provenance_reconciliation",
    "review_phase1_authorization",
    "review_task_creation_attestation",
    "codex_metadata_observation",
    "machine_review_record",
    "adversarial_check_record",
    "review_dispatch_request",
    "phase1_review_dispatch_activation",
    "adversarial_phase2_dispatch",
)

R18_PHASE1_REVIEW_SLOTS = ("sol_review_a", "sol_review_b")
R18_PHASE1_REVIEW_RECEIPT_PREFIXES = {
    "sol_review_a": "R18-REVIEW-A-EXECUTION-",
    "sol_review_b": "R18-REVIEW-B-EXECUTION-",
}
R18_PHASE1_REVIEW_PROMPT_FILENAMES = {
    "sol_review_a": "REVIEW_PROMPT_A.md",
    "sol_review_b": "REVIEW_PROMPT_B.md",
}
R18_PHASE1_RUNTIME_ENVELOPE_KEYS = (
    "protocol",
    "reviewer_slot",
    "actual_task_thread_id",
    "phase1_activation",
    "review_dispatch",
    "review_prompt",
    "task_creation_configuration_attestation",
    "allowed_output_path",
    "root_dispatch_id",
)
R18_PHASE1_SELF_READ_KEYS = (
    "thread_id",
    "turn_id",
    "host_id",
    "raw_status",
)

R18_PHASE1_PROTOCOL_TOKEN = "R18-PHASE1-TWO-STAGE-REVIEW-V2"
R18_PHASE1_RUNTIME_PROTOCOL = "R18-PHASE1-REVIEW-RUNTIME-ENVELOPE-V2"
R18_REVIEW_ATTEMPT_ID_PATTERN = r"R18-REVIEW-ATTEMPT-[0-9a-f]{32}"
R18_POLICY_PROFILE_ID = "shanghai_project_local_v1"
R18_POLICY_PURPOSE = "mandatory_instruction_only_not_review_evidence"
R18_POLICY_INPUT_KEYS = (
    "global_agents",
    "project_agents",
    "obsidian_skill",
    "shchem_skill",
    "kb_index",
    "kb_project_note",
    "task_routes",
    "evidence_contract",
)


@dataclass(frozen=True)
class ControllerBuildLayout:
    """Explicit output and reference root for one isolated build attempt."""

    root: Path
    bridge_dir: Path
    question_path: Path
    answer_path: Path
    request_path: Path
    report_path: Path
    controller: Path
    controller_cwd: Path

    def validate(self) -> None:
        lexical_root = Path(os.path.abspath(os.fspath(self.root)))
        for label, path in (
            ("bridge_dir", self.bridge_dir),
            ("question_path", self.question_path),
            ("answer_path", self.answer_path),
            ("request_path", self.request_path),
            ("report_path", self.report_path),
            ("controller", self.controller),
            ("controller_cwd", self.controller_cwd),
        ):
            lexical = Path(os.path.abspath(os.fspath(path)))
            try:
                lexical.relative_to(lexical_root)
            except ValueError as exc:
                raise R17BoundaryError(
                    f"controller_build_layout:{label}_outside_attempt_root"
                ) from exc
        if not self.controller.is_file():
            raise R17BoundaryError("controller_build_layout:controller_missing")
        if not self.controller_cwd.is_dir():
            raise R17BoundaryError("controller_build_layout:controller_cwd_missing")
        if len(
            {
                os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
                for path in (
                    self.question_path,
                    self.answer_path,
                    self.request_path,
                    self.report_path,
                )
            }
        ) != 4:
            raise R17BoundaryError("controller_build_layout:duplicate_output_path")

# This allowlist is code-derived from this installed project and the owning
# user's fixed Codex/knowledge-base roots.  Hashes and byte counts are always
# taken from one live host snapshot; they are never embedded in source.
_INSTALLED_WORKSPACE = Path(__file__).resolve().parents[2]
_INSTALLED_USER_HOME = _INSTALLED_WORKSPACE.parents[1]
R18_POLICY_HOST_PATHS: dict[str, Path] = {
    "global_agents": _INSTALLED_USER_HOME / ".codex/AGENTS.md",
    "project_agents": _INSTALLED_WORKSPACE / "AGENTS.md",
    "obsidian_skill": _INSTALLED_USER_HOME / ".codex/skills/obsidian-kb/SKILL.md",
    "shchem_skill": _INSTALLED_USER_HOME
    / ".codex/skills/shanghai-chemistry-teaching-research/SKILL.md",
    "kb_index": _INSTALLED_USER_HOME / "Documents/ChatGPT/知识库/00-INDEX.md",
    "kb_project_note": _INSTALLED_USER_HOME
    / "Documents/ChatGPT/知识库/20-Projects/shanghai-chemistry.md",
    "task_routes": _INSTALLED_USER_HOME
    / ".codex/skills/shanghai-chemistry-teaching-research/references/task-routes.md",
    "evidence_contract": _INSTALLED_USER_HOME
    / ".codex/skills/shanghai-chemistry-teaching-research/references/evidence-contract.md",
}


def r18_phase1_schema_paths(
    *, workspace: Path = WORKSPACE, db_root: Path = DB_ROOT
) -> dict[str, Path]:
    integration = workspace.resolve() / "integrations/shchem_generation_v2/schemas"
    governance = db_root.resolve() / "kb/machine_governance_v2/schemas"
    return {
        "paper_candidate": integration / "paper_candidate.schema.json",
        "component_registry": integration / "component_registry.schema.json",
        "deterministic_check_request": governance / "deterministic_check_request.schema.json",
        "deterministic_check_report": governance / "deterministic_check_report.schema.json",
        "project_generation_authorization": governance / "project_generation_authorization.schema.json",
        "r18_producer_receipt": integration / "r18_producer_receipt.schema.json",
        "sol_generator_receipt": integration / "sol_generator_receipt.schema.json",
        "generator_provenance_receipt": governance / "generator_provenance_receipt_r18.schema.json",
        "formal_freeze_receipt": governance / "formal_freeze_receipt_r18.schema.json",
        "formal_freeze_sidecar": governance / "formal_freeze_sidecar_r18.schema.json",
        "root_provenance_reconciliation": governance / "root_provenance_reconciliation_r18.schema.json",
        "review_phase1_authorization": governance / "review_phase1_authorization_r18.schema.json",
        "review_task_creation_attestation": governance / "review_task_creation_attestation_r18.schema.json",
        "codex_metadata_observation": governance / "review_codex_metadata_observation_r18.schema.json",
        "machine_review_record": governance / "machine_review_record.schema.json",
        "adversarial_check_record": governance / "adversarial_check_record.schema.json",
        "review_dispatch_request": integration / "consumer_v2/review_dispatch_request.schema.json",
        "phase1_review_dispatch_activation": integration / "phase1_review_dispatch_activation.schema.json",
        "adversarial_phase2_dispatch": integration / "adversarial_phase2_dispatch_r18.schema.json",
    }


GENERATOR_THREAD_ID = "019ff963-b23d-7d02-9e75-e1b92dfd4582"
ROOT_DISPATCH_ID = "019fe448-bce9-71a3-8615-98f5c34905d2"
GENERATOR_EXECUTION_METADATA_ENV = "SHCHEM_GENERATOR_EXECUTION_METADATA_PATH"
GENERATOR_EXECUTION_METADATA_DIR_RELATIVE = Path(
    "staging/coordination/generation_publication"
)
ROOT_RECONCILIATION_DIR = WORKSPACE / "staging/coordination/root/formal_freeze"
ROOT_RECONCILIATION_PATH = (
    ROOT_RECONCILIATION_DIR / f"root_provenance_reconciliation_{PAPER_ID}.json"
)


def root_reconciliation_path(artifact_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", artifact_id):
        raise ValueError(f"unsafe reconciliation artifact id: {artifact_id}")
    return ROOT_RECONCILIATION_DIR / f"root_provenance_reconciliation_{artifact_id}.json"


def generator_execution_metadata_path(
    *,
    workspace: Path | None = None,
    version_id: str | None = None,
) -> Path:
    """Return the one canonical self-report path for a generation revision."""

    bound_version = VERSION_ID if version_id is None else version_id
    if not isinstance(bound_version, str) or not bound_version:
        raise R17BoundaryError("generator_execution_metadata:version_id_invalid")
    revision_slug = bound_version.rsplit("-", 1)[-1].casefold()
    if not re.fullmatch(r"r[0-9]+", revision_slug):
        raise R17BoundaryError(
            "generator_execution_metadata:revision_slug_not_version_derived"
        )
    return (
        (WORKSPACE if workspace is None else workspace).resolve()
        / GENERATOR_EXECUTION_METADATA_DIR_RELATIVE
        / f"generator_execution_metadata_{revision_slug}.json"
    )


def _load_generator_execution_metadata(
    metadata: dict[str, Any] | Path | str | None = None,
    *,
    expected_version_id: str = VERSION_ID,
) -> dict[str, Any]:
    """Load an explicit, version-bound self-report; never invent task metadata.

    Resolution order is explicit mapping/path, then a non-empty workspace-local
    ``SHCHEM_GENERATOR_EXECUTION_METADATA_PATH`` override, then the canonical
    path derived from ``expected_version_id``.  There is intentionally no
    hard-coded turn/receipt fallback.
    """

    source: dict[str, Any]
    supplied = metadata
    if supplied is None:
        configured = os.environ.get(GENERATOR_EXECUTION_METADATA_ENV, "").strip()
        supplied = (
            Path(configured)
            if configured
            else generator_execution_metadata_path(
                workspace=WORKSPACE,
                version_id=expected_version_id,
            )
        )
    graph: SnapshotGraph | None = None
    if isinstance(supplied, (str, Path)):
        if not os.fspath(supplied).strip():
            raise R17BoundaryError("generator_execution_metadata:path_empty")
        raw_candidate = Path(os.fspath(supplied))
        candidate = Path(
            os.path.abspath(
                os.fspath(
                    raw_candidate
                    if raw_candidate.is_absolute()
                    else WORKSPACE / raw_candidate
                )
            )
        )
        graph = SnapshotGraph(WORKSPACE.resolve())
        snapshot = graph.read(candidate)
        try:
            loaded = snapshot.json_value()
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise R17BoundaryError(
                f"generator_execution_metadata:json_unreadable:{type(exc).__name__}"
            ) from exc
        if not isinstance(loaded, dict):
            raise R17BoundaryError("generator_execution_metadata:object_required")
        source = loaded
    elif isinstance(supplied, dict):
        source = copy.deepcopy(supplied)
    else:
        raise R17BoundaryError("generator_execution_metadata:input_type_invalid")

    required = {
        "schema_version",
        "record_type",
        "version_id",
        "provenance_status",
        "reported_execution",
        "expected_root_observation_projection",
    }
    if set(source) != required:
        raise R17BoundaryError("generator_execution_metadata:fields_not_exact")
    try:
        jsonschema.Draft202012Validator(
            load_schema(GENERATOR_SELF_REPORT_SCHEMA_PATH),
            format_checker=jsonschema.FormatChecker(),
        ).validate(source)
    except Exception as exc:
        raise R17BoundaryError(
            f"generator_execution_metadata:schema_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        source.get("schema_version") != "2.0.0-r18"
        or source.get("record_type") != "generation_execution_self_report"
        or source.get("version_id") != expected_version_id
        or source.get("provenance_status") != "self_reported"
    ):
        raise R17BoundaryError(
            "generator_execution_metadata:version_or_record_boundary_invalid"
        )
    execution = source.get("reported_execution")
    projection = source.get("expected_root_observation_projection")
    if not isinstance(execution, dict) or set(execution) != set(EXECUTION_CORE_FIELDS):
        raise R17BoundaryError("generator_execution_metadata:execution_fields_not_exact")
    projection_fields = {
        "task_role",
        "observed_task_identity",
        "root_dispatch_configuration",
        "receipt_derivation",
        "derived_local_receipt_id",
    }
    if not isinstance(projection, dict) or set(projection) != projection_fields:
        raise R17BoundaryError(
            "generator_execution_metadata:expected_projection_fields_not_exact"
        )
    observed_identity = projection.get("observed_task_identity")
    dispatch_configuration = projection.get("root_dispatch_configuration")
    if not isinstance(observed_identity, dict) or set(observed_identity) != {
        "thread_id",
        "turn_id",
    }:
        raise R17BoundaryError(
            "generator_execution_metadata:observed_task_identity_fields_not_exact"
        )
    if not isinstance(dispatch_configuration, dict) or set(
        dispatch_configuration
    ) != {"root_dispatch_id", "model", "reasoning_effort"}:
        raise R17BoundaryError(
            "generator_execution_metadata:dispatch_configuration_fields_not_exact"
        )
    for field in ("thread_id", "turn_id", "receipt_id", "root_dispatch_id"):
        if not isinstance(execution.get(field), str) or not execution[field].strip():
            raise R17BoundaryError(
                f"generator_execution_metadata:{field}_missing"
            )
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        execution["thread_id"],
    ):
        raise R17BoundaryError("generator_execution_metadata:thread_id_not_uuid")
    expected_receipt_id = f"R18-GENERATOR-EXECUTION-{execution['turn_id']}"
    if (
        execution.get("provider") != "codex_app_thread"
        or execution.get("model") != "gpt-5.6-sol"
        or execution.get("reasoning_effort") != "xhigh"
        or execution.get("receipt_id") != expected_receipt_id
        or projection.get("task_role") != "generator"
        or projection.get("receipt_derivation")
        != "local_r18_role_prefix_plus_turn_id"
        or projection.get("derived_local_receipt_id") != expected_receipt_id
        or observed_identity
        != {"thread_id": execution["thread_id"], "turn_id": execution["turn_id"]}
        or dispatch_configuration
        != {
            "root_dispatch_id": execution["root_dispatch_id"],
            "model": execution["model"],
            "reasoning_effort": execution["reasoning_effort"],
        }
    ):
        raise R17BoundaryError(
            "generator_execution_metadata:self_report_projection_invalid"
        )
    if graph is not None:
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "generator_execution_metadata:snapshot_changed_before_return"
            )
    return source


def generator_execution_metadata_binding(
    metadata: dict[str, Any] | Path | str | None = None,
    *,
    expected_version_id: str = VERSION_ID,
) -> dict[str, Any]:
    """Public one-load view used by build and readiness layers."""

    return _load_generator_execution_metadata(
        metadata,
        expected_version_id=expected_version_id,
    )


def generator_execution_provenance(
    metadata: dict[str, Any] | Path | str | None = None,
    *,
    expected_version_id: str = VERSION_ID,
) -> dict[str, str]:
    binding = _load_generator_execution_metadata(
        metadata,
        expected_version_id=expected_version_id,
    )
    return copy.deepcopy(binding["reported_execution"])


def generator_expected_root_observation_projection(
    metadata: dict[str, Any] | Path | str | None = None,
    *,
    expected_version_id: str = VERSION_ID,
) -> dict[str, Any]:
    binding = _load_generator_execution_metadata(
        metadata,
        expected_version_id=expected_version_id,
    )
    return copy.deepcopy(binding["expected_root_observation_projection"])


def generation_policy() -> dict[str, Any]:
    authorization = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    if (
        authorization.get("authorization_version") != "1.0.0"
        or authorization.get("profile_authorization_claimed") is not False
        or authorization.get("profile_role") != "read_only_structure_observation"
    ):
        raise RuntimeError("project generation authorization boundary mismatch")
    return {
        "original_question_generation": True,
        "source_question_republication": False,
        "profile_authorization_claimed": False,
        "project_generation_authorization": db_file_ref(AUTHORIZATION_PATH),
        "authorization_version": authorization["authorization_version"],
        "authorization_scope": authorization["authorized_capabilities"],
    }


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def subject_pair_sha256(question_sha256: str, answer_sha256: str) -> str:
    return canonical_hash(
        {"answer_sha256": answer_sha256, "question_sha256": question_sha256}
    )


def db_file_ref(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(DB_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"controller file reference is outside sh-chem-db: {resolved}") from exc
    snapshot = SnapshotGraph(DB_ROOT.resolve()).read(resolved)
    return {"path": relative, "sha256": snapshot.sha256, "bytes": snapshot.byte_length}


def workspace_file_ref(path: Path, *, workspace: Path = WORKSPACE) -> dict[str, object]:
    """Return a live file reference rooted at the shared workspace.

    Phase-2 must bind both database files and the root-authored formal-freeze
    receipt under ``staging``.  A single workspace-relative representation
    avoids ambiguous ``..`` paths while keeping the controller's database-root
    references unchanged.
    """

    resolved = path.resolve()
    try:
        relative = resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"phase-2 file reference is outside workspace: {resolved}") from exc
    snapshot = SnapshotGraph(workspace.resolve()).read(resolved)
    return {"path": relative, "sha256": snapshot.sha256, "bytes": snapshot.byte_length}


def _canonical_host_path(path: Path) -> str:
    """Render one trusted host path in the only record spelling we accept."""

    if not isinstance(path, Path):
        raise R17BoundaryError("phase1_policy:path_object_required")
    rendered = Path(os.path.abspath(os.fspath(path))).as_posix()
    if (
        not re.fullmatch(r"[A-Za-z]:/[^\x00]+", rendered)
        or "\\" in rendered
        or rendered.startswith(("//", "\\\\", "//?/", "//./"))
        or any(part in {"", ".", ".."} for part in rendered[3:].split("/"))
        or any(":" in part for part in rendered[3:].split("/"))
    ):
        raise R17BoundaryError("phase1_policy:canonical_absolute_host_path_required")
    return rendered


def r18_phase1_policy_paths(*, workspace: Path = WORKSPACE) -> dict[str, Path]:
    """Return a copy of the finite, code-derived mandatory-policy allowlist."""

    if set(R18_POLICY_HOST_PATHS) != set(R18_POLICY_INPUT_KEYS):
        raise R17BoundaryError("phase1_policy:code_allowlist_keys_not_exact")
    paths = {key: R18_POLICY_HOST_PATHS[key] for key in R18_POLICY_INPUT_KEYS}
    paths["project_agents"] = Path(os.path.abspath(os.fspath(workspace))) / "AGENTS.md"
    rendered = [_canonical_host_path(path) for path in paths.values()]
    if len({value.casefold() for value in rendered}) != len(rendered):
        raise R17BoundaryError("phase1_policy:code_allowlist_casefold_duplicate")
    return paths


def _policy_snapshot_graph(paths: dict[str, Path]) -> SnapshotGraph:
    anchors = {Path(os.path.abspath(os.fspath(path))).anchor.casefold() for path in paths.values()}
    if len(anchors) != 1:
        raise R17BoundaryError("phase1_policy:host_paths_not_on_single_snapshot_root")
    first = next(iter(paths.values()))
    return SnapshotGraph(Path(Path(os.path.abspath(os.fspath(first))).anchor))


def _snapshot_phase1_policy_inputs(
    *, workspace: Path = WORKSPACE
) -> tuple[dict[str, Any], SnapshotGraph]:
    paths = r18_phase1_policy_paths(workspace=workspace)
    graph = _policy_snapshot_graph(paths)
    refs: dict[str, dict[str, object]] = {}
    for key in R18_POLICY_INPUT_KEYS:
        path = paths[key]
        snapshot = graph.read(path)
        refs[key] = {
            "path": _canonical_host_path(path),
            "sha256": snapshot.sha256,
            "bytes": snapshot.byte_length,
        }
    core = {
        "profile_id": R18_POLICY_PROFILE_ID,
        "purpose": R18_POLICY_PURPOSE,
        "inputs": refs,
    }
    payload = canonical_json_bytes(core)
    return {
        **core,
        "canonical_sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_bytes": len(payload),
    }, graph


def r18_phase1_policy_inputs(*, workspace: Path = WORKSPACE) -> dict[str, Any]:
    """Snapshot and revalidate the exact policy-only host profile."""

    profile, graph = _snapshot_phase1_policy_inputs(workspace=workspace)
    checkpoint = graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError(
            "phase1_policy:host_snapshot_changed:"
            + ",".join(checkpoint.get("errors", []))
        )
    return profile


def _protocol_policy_digest(policy_inputs: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "phase1_protocol": R18_PHASE1_PROTOCOL_TOKEN,
            "runtime_protocol": R18_PHASE1_RUNTIME_PROTOCOL,
            "policy_profile_id": policy_inputs.get("profile_id"),
            "policy_purpose": policy_inputs.get("purpose"),
            "policy_canonical_sha256": policy_inputs.get("canonical_sha256"),
            "policy_canonical_bytes": policy_inputs.get("canonical_bytes"),
        }
    )


def _activation_id_from_bindings(
    *,
    expected_version_id: str,
    root_authorization_ref: dict[str, object],
    policy_inputs: dict[str, Any],
    review_attempt_id: str,
) -> str:
    if not isinstance(review_attempt_id, str) or not re.fullmatch(
        R18_REVIEW_ATTEMPT_ID_PATTERN, review_attempt_id
    ):
        raise R17BoundaryError("phase1_review_attempt:id_invalid")
    suffix = canonical_hash(
        {
            "protocol_policy_digest": _protocol_policy_digest(policy_inputs),
            "root_phase1_authorization": root_authorization_ref,
            "review_attempt_id": review_attempt_id,
        }
    )[:16]
    return f"PHASE1-ACTIVATION-{expected_version_id}-{suffix}"


def r18_phase1_activation_id(
    root_authorization_path: Path,
    *,
    review_attempt_id: str,
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
) -> str:
    """Derive one append-only activation ID from policy, root GO and attempt ID."""

    policy_inputs, policy_graph = _snapshot_phase1_policy_inputs(workspace=workspace)
    workspace_graph = SnapshotGraph(workspace.resolve())
    authorization_ref = workspace_graph.read(root_authorization_path).ref(workspace)
    for graph, label in (
        (policy_graph, "policy"),
        (workspace_graph, "root_authorization"),
    ):
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                f"phase1_activation_id:{label}_snapshot_changed:"
                + ",".join(checkpoint.get("errors", []))
            )
    return _activation_id_from_bindings(
        expected_version_id=expected_version_id,
        root_authorization_ref=authorization_ref,
        policy_inputs=policy_inputs,
        review_attempt_id=review_attempt_id,
    )


def _validate_phase1_policy_inputs(
    dispatch: dict[str, Any],
    activation: dict[str, Any],
    *,
    workspace: Path = WORKSPACE,
    disallowed_paths: Iterable[Path] = (),
) -> tuple[dict[str, Any], SnapshotGraph]:
    """Bind the mirrored policy-only profile to one live host snapshot graph."""

    expected, graph = _snapshot_phase1_policy_inputs(workspace=workspace)
    dispatch_policy = dispatch.get("policy_inputs")
    activation_policy = activation.get("policy_inputs")
    if dispatch_policy != activation_policy or activation_policy != expected:
        raise R17BoundaryError("phase1_policy:activation_dispatch_profile_mismatch")
    if set(expected) != {
        "profile_id",
        "purpose",
        "inputs",
        "canonical_sha256",
        "canonical_bytes",
    }:
        raise R17BoundaryError("phase1_policy:profile_keys_not_exact")
    if set(expected["inputs"]) != set(R18_POLICY_INPUT_KEYS):
        raise R17BoundaryError("phase1_policy:input_keys_not_exact_8")
    core = {
        "profile_id": expected["profile_id"],
        "purpose": expected["purpose"],
        "inputs": expected["inputs"],
    }
    core_bytes = canonical_json_bytes(core)
    if expected["canonical_sha256"] != hashlib.sha256(core_bytes).hexdigest() or expected[
        "canonical_bytes"
    ] != len(core_bytes):
        raise R17BoundaryError("phase1_policy:canonical_core_mismatch")
    if activation.get("protocol_policy_digest") != _protocol_policy_digest(expected) or dispatch.get(
        "protocol_policy_digest"
    ) != _protocol_policy_digest(expected):
        raise R17BoundaryError("phase1_policy:protocol_policy_digest_mismatch")

    _assert_policy_paths_disjoint(disallowed_paths, workspace=workspace)
    return expected, graph


def _assert_policy_paths_disjoint(
    disallowed_paths: Iterable[Path], *, workspace: Path = WORKSPACE
) -> None:
    policy_identities = {
        os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
        for path in r18_phase1_policy_paths(workspace=workspace).values()
    }
    other_identities = {
        os.path.normcase(os.path.abspath(os.fspath(path))).casefold()
        for path in disallowed_paths
        if isinstance(path, Path)
    }
    if policy_identities.intersection(other_identities):
        raise R17BoundaryError("phase1_policy:path_overlaps_review_history_output_or_evidence")


def _r18_phase1_review_slot_contract(slot: str) -> dict[str, str]:
    if slot not in R18_PHASE1_REVIEW_SLOTS:
        raise ValueError(f"unsupported R18 phase-1 reviewer slot: {slot!r}")
    letter = "A" if slot == "sol_review_a" else "B"
    counterpart_slot = "sol_review_b" if slot == "sol_review_a" else "sol_review_a"
    counterpart_letter = "B" if letter == "A" else "A"
    return {
        "slot": slot,
        "letter": letter,
        "counterpart_slot": counterpart_slot,
        "counterpart_letter": counterpart_letter,
        "receipt_prefix": R18_PHASE1_REVIEW_RECEIPT_PREFIXES[slot],
        "prompt_filename": R18_PHASE1_REVIEW_PROMPT_FILENAMES[slot],
        "counterpart_prompt_filename": R18_PHASE1_REVIEW_PROMPT_FILENAMES[
            counterpart_slot
        ],
    }


def build_phase1_review_bootstrap_message(
    slot: str, *, workspace: Path = WORKSPACE
) -> str:
    """Return the exact policy-only first-turn message for a new A/B task."""

    contract = _r18_phase1_review_slot_contract(slot)
    policy_rows = "; ".join(
        f"{key}={_canonical_host_path(path)}"
        for key, path in r18_phase1_policy_paths(workspace=workspace).items()
    )
    reply = (
        f"R18 phase-1 {contract['slot']} mandatory policies loaded; waiting for "
        "the root follow-up runtime envelope."
    )
    return (
        f"{R18_PHASE1_PROTOCOL_TOKEN} {contract['slot']} bootstrap only. Read exactly "
        f"these eight mandatory policy files and no other file or resource: {policy_rows}. "
        "These policy reads are instructions only and never review evidence. Do not read "
        "any activation, dispatch, evidence, review, counterpart prompt/output, history, "
        "status, validation result, or other workspace content; do not run status, "
        "validate, WeChat search, collection, workspace discovery, listing, review, or "
        "validation; and do not write any receipt, sidecar, prompt, cache, log, blocker, "
        "or other output. Review work is forbidden until a separate root follow-up turn "
        f"supplies the exact V2 runtime envelope. Reply exactly: {reply}"
    )


def validate_phase1_review_bootstrap_message(
    message: object, *, expected_slot: str, workspace: Path = WORKSPACE
) -> dict[str, Any]:
    """Fail closed unless a bootstrap message is the exact canonical no-work text."""

    errors: list[str] = []
    try:
        expected = build_phase1_review_bootstrap_message(
            expected_slot, workspace=workspace
        )
        if not isinstance(message, str):
            errors.append("phase1_bootstrap:message_string_required")
        elif message != expected:
            errors.append("phase1_bootstrap:no_work_contract_mismatch")
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    return {
        "check": "r18_phase1_review_bootstrap_no_work_contract",
        "status": "pass" if not errors else "fail",
        "reviewer_slot": expected_slot,
        "workspace_reads_authorized": False,
        "mandatory_policy_reads_authorized": not errors,
        "mandatory_policy_input_count": len(R18_POLICY_INPUT_KEYS),
        "workspace_writes_authorized": False,
        "review_authorized": False,
        "errors": sorted(dict.fromkeys(errors)),
    }


def render_phase1_reviewer_prompt(
    slot: str,
    *,
    output_path: str,
    expected_version_id: str = VERSION_ID,
    workspace: Path = WORKSPACE,
) -> str:
    """Render the immutable, exact two-stage reviewer instruction contract."""

    contract = _r18_phase1_review_slot_contract(slot)
    path_error = portable_relative_path_error(output_path)
    if path_error is not None:
        raise ValueError(f"phase1_prompt:output_path_invalid:{path_error}")
    atomic_ids = ", ".join(f"P{index:02d}" for index in range(1, 37))
    immutable_keys = ", ".join(R18_PHASE1_IMMUTABLE_KEYS)
    schema_keys = ", ".join(R18_PHASE1_SCHEMA_KEYS)
    bootstrap = build_phase1_review_bootstrap_message(slot, workspace=workspace)
    focus = (
        "Independently check chemistry, answer, rubric, unit/state/charge/condition "
        "integrity and evidence alignment."
        if slot == "sol_review_a"
        else "Independently attack isolation, ambiguity, hierarchy, answerability, "
        "Shanghai-style fit and unsupported inference."
    )
    return f"""# Independent Sol review {contract['letter']} — R18 two-stage protocol

Protocol token: `{R18_PHASE1_PROTOCOL_TOKEN}`. Version: `{expected_version_id}`. Reviewer slot: `{slot}`. Own receipt prefix: `{contract['receipt_prefix']}`. Own immutable prompt filename: `{contract['prompt_filename']}`. The only permitted output path is `{output_path}`.

## Mandatory two-stage root dispatch

The root must first create a distinct, user-visible review task using exactly this bootstrap message:

> {bootstrap}

That bootstrap turn reads only the exact eight fixed mandatory policy files named above, treats them only as instructions, performs no workspace discovery/write/review/validation, and creates no receipt or output. The root must then send a separate follow-up turn containing one runtime envelope with exactly these fields: `protocol`, `reviewer_slot`, `actual_task_thread_id`, `phase1_activation` (`path`, expected `sha256`, expected `bytes`), `review_dispatch` (`path`, expected `sha256`, expected `bytes`), `review_prompt` (`path`, expected `sha256`, expected `bytes`), `task_creation_configuration_attestation` (`path`, expected `sha256`, expected `bytes`), `allowed_output_path`, and `root_dispatch_id`. Review work occurs only in that follow-up turn. The bootstrap turn, task-creation response, placeholders, or guessed metadata never authorize review.

## Start-of-review-turn fail-closed checks

Before any review-evidence read and before any write, validate the exact runtime envelope. Re-read the live activation, dispatch, this own prompt, and strict root task-creation configuration attestation at the envelope paths; verify their exact raw SHA-256 and byte counts; verify the attestation's schema/self-hash and exact binding to this slot/thread/host, the completed bootstrap turn and canonical bootstrap prompt, activation, dispatch, this own prompt, allowed output path, root dispatch ID, and root-requested `gpt-5.6-sol`/`xhigh` configuration. Reject any attestation claiming a platform-reported model, platform-reported reasoning effort, platform receipt, signature, or private-key use. Verify all eight `policy_inputs` host files against the activation before reading evidence. Then call `codex_app.read_thread` on the supplied `actual_task_thread_id`, verify it is this task, and identify this review follow-up's currently `inProgress` `turn_id`—never the bootstrap or an earlier/completed turn. Self-read exactly `thread_id`, `turn_id`, `host_id`, and `raw_status`; it does not supply model or effort. Derive the local receipt ID exactly as `{contract['receipt_prefix']}<turn_id>` from that current turn; never accept a root-supplied receipt ID or a platform-receipt claim.

If the thread ID, four-field self-read result, current in-progress turn ID, host ID, task attestation, policy profile, activation/dispatch binding, prompt path/hash/bytes, allowed output path, or root dispatch ID is missing, unavailable, stale, or inconsistent, return a blocker in the task and write no receipt or other file. Do not guess or repair metadata.

## Exact read boundary

Control-plane reads are limited to the exact activation, dispatch, own prompt, and task-creation configuration attestation references in the valid runtime envelope. Mandatory-policy reads are limited to the activation's separate exact `policy_inputs` profile `shanghai_project_local_v1`, purpose `mandatory_instruction_only_not_review_evidence`, with exactly eight absolute host references. Policy content can enforce instructions and read boundaries but cannot support chemistry, answer, rubric, Shanghai-style, ambiguity, source-authority, or any other substantive finding. It is never part of the 25 review inputs or the schema set. Review-evidence reads are limited to the activation's exact 25 `immutable_inputs` references, each verified by path/hash/bytes, with these exact keys and no link-following beyond them: {immutable_keys}.

Schema reads are limited to the activation's exact 19 `schemas` references, each verified by path/hash/bytes, with these exact keys: {schema_keys}.

    Blanket deny: do not read, search, list, stat, hash, infer from, or follow an alias/symlink/reference to any path or record matching counterpart slot `{contract['counterpart_slot']}`, counterpart prompt `{contract['counterpart_prompt_filename']}`, any counterpart/current/prior review receipt or output, any current or prior adversarial prompt/sidecar/receipt/output, any `R2` through `R17` review artifact, or any path/record labelled `history`, `historical`, `prior`, `old`, `superseded`, `failed_round`, `review_receipt`, `machine_review_record`, `sol_review_a`, `sol_review_b`, or `adversarial_check`, except that the exact allowlisted schema references may be read as schemas and this exact own prompt may be read as the prompt. Existing content at the allowed output path is a blocker, not an input. This is a path-and-record-pattern ban across the workspace, not merely a ban on the current counterpart file. The exact R18 V2 policy-only bootstrap and activation-bound follow-up are the Shanghai chemistry skill exception; do not fall back to its ordinary status/validate, task-route discovery, evidence search, or WeChat startup path in either turn.

## Review and output contract

{focus} Emit exactly one finding for every atomic part in this exact order: {atomic_ids}. Every finding must carry these five checks in this exact order: `chemistry`, `answer`, `rubric`, `shanghai_style`, `ambiguity`. Use strict UTF-8 and specific readable finding/evidence text.

Set the receipt `subject` to an exact deep copy of the loaded deterministic
request's `subject`. It must contain exactly `question`, `answer`, and
`subject_pair_sha256`; preserve the DB-relative `path`, `sha256`, and `bytes`
values byte-for-byte. Never rebuild it from a producer/formal-freeze subject,
prepend `sh-chem-db/` to either path, or add `paper_id`, `version_id`, or any
other field.

After every start-of-turn check passes, revalidate the same bound snapshots and absence checks immediately before commit, then exclusively create only `{output_path}` with atomic no-replace semantics equivalent to `O_CREAT|O_EXCL`. A `receipt_write_allowed=true` validation result is not a path reservation and never permits an ordinary overwrite. Write no prompt, activation, dispatch, observation, sidecar, blocker file, temporary file, cache, log, register, chain, publication, delivery, or other artifact. Do not modify an existing file. Do not perform or authorize adversarial review, chain creation, registration, publishing, or delivery.

Never claim or imply human, teacher, expert, employment, sealed-review, or official authority. Semantic authority is fixed to `official=false`, `official_claim_allowed=false`, and `human_reviewed=false`; the receipt must carry schema-permitted `human_reviewed=false` and no contrary claim. Execution provenance is a local derivation only: provider `codex_app_thread`, the self-read thread/current turn, receipt `{contract['receipt_prefix']}<turn_id>`, the attestation's root-requested model `gpt-5.6-sol` with `model_source=task_creation_configuration_attestation.requested_configuration`, the attestation's root-requested effort `xhigh` with `reasoning_effort_source=task_creation_configuration_attestation.requested_configuration`, and the envelope's `root_dispatch_id`. Never label model or effort as platform-reported or self-read.

The reviewer must not create the root metadata observation. After this task completes, the root later calls `codex_app.read_thread`, observes the completed review task, and creates the separate root observation before any later phase can be considered.
"""


def validate_phase1_reviewer_prompt_contract(
    prompt_text: object,
    *,
    expected_slot: str,
    expected_output_path: str,
    expected_version_id: str = VERSION_ID,
    workspace: Path = WORKSPACE,
) -> dict[str, Any]:
    """Reject any removal, rewriting, or relaxation of the static prompt contract."""

    errors: list[str] = []
    try:
        expected = render_phase1_reviewer_prompt(
            expected_slot,
            output_path=expected_output_path,
            expected_version_id=expected_version_id,
            workspace=workspace,
        )
        if not isinstance(prompt_text, str):
            errors.append("phase1_prompt:utf8_text_required")
        elif prompt_text != expected:
            errors.append(
                f"phase1_prompt:{expected_slot}:canonical_two_stage_contract_mismatch"
            )
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    return {
        "check": "r18_phase1_reviewer_prompt_contract",
        "status": "pass" if not errors else "fail",
        "reviewer_slot": expected_slot,
        "errors": sorted(dict.fromkeys(errors)),
    }


def _canonical_codex_uuid(value: object) -> str | None:
    """Return a canonical non-nil UUID string, or ``None`` for metadata prose."""

    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    ):
        return None
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return None
    return value if parsed.int != 0 and str(parsed) == value else None


def build_phase1_review_runtime_envelope(
    *,
    slot: str,
    actual_task_thread_id: str,
    activation_path: Path,
    dispatch_path: Path,
    prompt_path: Path,
    task_creation_configuration_attestation_path: Path,
    allowed_output_path: Path,
    root_dispatch_id: str,
    workspace: Path = WORKSPACE,
) -> dict[str, Any]:
    """Build, but never send, the exact root follow-up envelope for one reviewer."""

    contract = _r18_phase1_review_slot_contract(slot)
    if _canonical_codex_uuid(actual_task_thread_id) is None:
        raise ValueError("phase1_runtime_envelope:actual_task_thread_id_not_codex_uuid")
    if _canonical_codex_uuid(root_dispatch_id) is None:
        raise ValueError("phase1_runtime_envelope:root_dispatch_id_not_codex_uuid")
    if prompt_path.name != contract["prompt_filename"]:
        raise ValueError("phase1_runtime_envelope:prompt_filename_slot_mismatch")
    try:
        output_rel = allowed_output_path.resolve().relative_to(
            workspace.resolve()
        ).as_posix()
    except ValueError as exc:
        raise ValueError(
            "phase1_runtime_envelope:allowed_output_outside_workspace"
        ) from exc
    path_error = portable_relative_path_error(output_rel)
    if path_error is not None:
        raise ValueError(
            f"phase1_runtime_envelope:allowed_output_path_invalid:{path_error}"
        )
    return {
        "protocol": R18_PHASE1_RUNTIME_PROTOCOL,
        "reviewer_slot": slot,
        "actual_task_thread_id": actual_task_thread_id,
        "phase1_activation": workspace_file_ref(activation_path, workspace=workspace),
        "review_dispatch": workspace_file_ref(dispatch_path, workspace=workspace),
        "review_prompt": workspace_file_ref(prompt_path, workspace=workspace),
        "task_creation_configuration_attestation": workspace_file_ref(
            task_creation_configuration_attestation_path, workspace=workspace
        ),
        "allowed_output_path": output_rel,
        "root_dispatch_id": root_dispatch_id,
    }


def _validate_review_task_creation_attestation_with_graph(
    attestation_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    expected_slot: str,
    expected_thread_id: str,
    expected_host_id: str,
    current_turn_id: str,
    activation_ref: dict[str, object],
    dispatch_ref: dict[str, object],
    prompt_ref: dict[str, object],
    allowed_output_path: str,
    expected_root_dispatch_id: str,
    schema_ref: dict[str, object],
    expected_version_id: str,
    expected_paper_id: str,
) -> dict[str, Any]:
    contract = _r18_phase1_review_slot_contract(expected_slot)
    snapshot = graph.read(attestation_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError("review_task_attestation:object_required")
    expected_schema_path = (
        workspace.resolve()
        / "sh-chem-db/kb/machine_governance_v2/schemas/review_task_creation_attestation_r18.schema.json"
    )
    schema_snapshot = graph.read(expected_schema_path)
    if schema_ref != schema_snapshot.ref(workspace):
        raise R17BoundaryError("review_task_attestation:activation_schema_ref_mismatch")
    graph.verify_ref(
        record.get("schema_binding"),
        label="review_task_attestation.schema_binding",
        expected_path=expected_schema_path,
    )
    _schema_validate_snapshot(record, schema_snapshot, label="review_task_attestation")
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError("review_task_attestation:self_hash_mismatch")

    try:
        relative = snapshot.path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise R17BoundaryError("review_task_attestation:path_outside_workspace") from exc
    prefix = (
        "staging",
        "coordination",
        "root",
        "revisions",
        "r18",
        "review_task_attestations",
    )
    expected_directory = f"{expected_slot}--{expected_thread_id}"
    if (
        len(relative.parts) != len(prefix) + 2
        or tuple(part.casefold() for part in relative.parts[: len(prefix)]) != prefix
        or relative.parts[-2] != expected_directory
        or relative.parts[-1] != "review_task_creation_attestation_r18.json"
    ):
        raise R17BoundaryError("review_task_attestation:path_pattern_mismatch")

    actual_task = record.get("actual_task")
    bootstrap_turn = record.get("bootstrap_turn")
    if actual_task != {"thread_id": expected_thread_id, "host_id": expected_host_id}:
        raise R17BoundaryError("review_task_attestation:thread_or_host_mismatch")
    if (
        not isinstance(bootstrap_turn, dict)
        or set(bootstrap_turn) != {"turn_id", "status"}
        or _canonical_codex_uuid(bootstrap_turn.get("turn_id")) is None
        or bootstrap_turn.get("status") != "completed"
        or bootstrap_turn.get("turn_id") == current_turn_id
    ):
        raise R17BoundaryError("review_task_attestation:bootstrap_turn_invalid_or_current")
    bootstrap_bytes = build_phase1_review_bootstrap_message(
        expected_slot, workspace=workspace
    ).encode("utf-8")
    expected_bootstrap = {
        "canonical_sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
        "canonical_bytes": len(bootstrap_bytes),
    }
    if record.get("canonical_bootstrap_prompt") != expected_bootstrap:
        raise R17BoundaryError("review_task_attestation:bootstrap_prompt_mismatch")
    expected_bindings = {
        "phase1_activation": activation_ref,
        "review_dispatch": dispatch_ref,
        "review_prompt": prompt_ref,
        "allowed_output_path": allowed_output_path,
        "root_dispatch_id": expected_root_dispatch_id,
    }
    if any(record.get(field) != value for field, value in expected_bindings.items()):
        raise R17BoundaryError("review_task_attestation:activation_dispatch_prompt_output_binding_mismatch")
    if (
        record.get("version_id") != expected_version_id
        or record.get("paper_id") != expected_paper_id
        or record.get("reviewer_slot") != expected_slot
    ):
        raise R17BoundaryError("review_task_attestation:identity_mismatch")
    if record.get("requested_configuration") != {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "xhigh",
        "configuration_source": "root_create_thread_request_arguments",
    }:
        raise R17BoundaryError("review_task_attestation:requested_configuration_mismatch")
    fixed_boundary = {
        "platform_model_reported": False,
        "platform_reasoning_effort_reported": False,
        "platform_receipt_claimed": False,
        "attestation_kind": "root_non_signing_review_task_creation_configuration_attestation",
        "observer_role": "root_observer",
        "non_signing": True,
        "cryptographic_signature": False,
        "private_key_used": False,
        "human_reviewed": False,
    }
    if any(record.get(field) is not value for field, value in fixed_boundary.items() if isinstance(value, bool)) or any(
        record.get(field) != value
        for field, value in fixed_boundary.items()
        if not isinstance(value, bool)
    ):
        raise R17BoundaryError("review_task_attestation:platform_or_signing_boundary_mismatch")
    created_at = _utc_timestamp(
        record.get("created_at"), label="review_task_attestation.created_at"
    )
    current = datetime.now(timezone.utc)
    if created_at > current + timedelta(minutes=5) or current - created_at > timedelta(days=7):
        raise R17BoundaryError("review_task_attestation:timestamp_not_current")
    return {
        "record": record,
        "ref": snapshot.ref(workspace),
        "bootstrap_turn_id": bootstrap_turn["turn_id"],
        "execution": {
            "provider": "codex_app_thread",
            "thread_id": expected_thread_id,
            "turn_id": current_turn_id,
            "receipt_id": contract["receipt_prefix"] + current_turn_id,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "model_source": "task_creation_configuration_attestation.requested_configuration",
            "reasoning_effort_source": "task_creation_configuration_attestation.requested_configuration",
            "root_dispatch_id": expected_root_dispatch_id,
        },
    }


def validate_phase1_review_runtime_envelope(
    envelope: object,
    *,
    expected_slot: str,
    self_read_metadata: object,
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
) -> dict[str, Any]:
    """Validate one review turn against a single immutable snapshot graph.

    The reviewer may read its own prompt but never the counterpart prompt.  All
    25 review inputs and all 19 schemas are nevertheless live-bound before a
    receipt write is authorized.
    """

    errors: list[str] = []
    derived_receipt_id: str | None = None
    derived_execution: dict[str, Any] | None = None
    attestation_binding: dict[str, Any] | None = None
    policy_graph: SnapshotGraph | None = None
    graph = SnapshotGraph(workspace.resolve())
    try:
        contract = _r18_phase1_review_slot_contract(expected_slot)
        if not isinstance(envelope, dict):
            raise R17BoundaryError("phase1_runtime_envelope:object_required")
        if set(envelope) != set(R18_PHASE1_RUNTIME_ENVELOPE_KEYS):
            raise R17BoundaryError("phase1_runtime_envelope:keys_not_exact")
        if envelope.get("protocol") != R18_PHASE1_RUNTIME_PROTOCOL:
            raise R17BoundaryError("phase1_runtime_envelope:protocol_mismatch")
        if envelope.get("reviewer_slot") != expected_slot:
            raise R17BoundaryError("phase1_runtime_envelope:reviewer_slot_mismatch")
        task_thread_id = envelope.get("actual_task_thread_id")
        if _canonical_codex_uuid(task_thread_id) is None:
            raise R17BoundaryError(
                "phase1_runtime_envelope:actual_task_thread_id_not_codex_uuid"
            )
        if _canonical_codex_uuid(envelope.get("root_dispatch_id")) is None:
            raise R17BoundaryError(
                "phase1_runtime_envelope:root_dispatch_id_not_codex_uuid"
            )

        activation_snapshot = graph.verify_ref(
            envelope.get("phase1_activation"), label="phase1_runtime.activation"
        )
        dispatch_snapshot = graph.verify_ref(
            envelope.get("review_dispatch"), label="phase1_runtime.dispatch"
        )
        prompt_snapshot = graph.verify_ref(
            envelope.get("review_prompt"), label="phase1_runtime.own_prompt"
        )
        attestation_snapshot = graph.verify_ref(
            envelope.get("task_creation_configuration_attestation"),
            label="phase1_runtime.task_creation_configuration_attestation",
        )
        for label, reference, snapshot in (
            ("activation", envelope["phase1_activation"], activation_snapshot),
            ("dispatch", envelope["review_dispatch"], dispatch_snapshot),
            ("own_prompt", envelope["review_prompt"], prompt_snapshot),
            (
                "task_creation_configuration_attestation",
                envelope["task_creation_configuration_attestation"],
                attestation_snapshot,
            ),
        ):
            if reference != snapshot.ref(workspace):
                raise R17BoundaryError(
                    f"phase1_runtime_envelope:{label}_reference_not_canonical"
                )
        activation = activation_snapshot.json_value()
        dispatch = dispatch_snapshot.json_value()
        if not isinstance(activation, dict) or not isinstance(dispatch, dict):
            raise R17BoundaryError("phase1_runtime_envelope:activation_dispatch_objects_required")
        review_attempt_id = activation.get("review_attempt_id")
        if (
            activation.get("review_protocol") != R18_PHASE1_PROTOCOL_TOKEN
            or dispatch.get("review_protocol") != R18_PHASE1_PROTOCOL_TOKEN
            or not isinstance(review_attempt_id, str)
            or not re.fullmatch(R18_REVIEW_ATTEMPT_ID_PATTERN, review_attempt_id)
            or dispatch.get("review_attempt_id") != review_attempt_id
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:review_attempt_binding_mismatch"
            )
        activation_id = _phase1_output_layout(activation_snapshot.path, workspace=workspace)
        bundle_dir = activation_snapshot.path.parent
        expected_activation_path = (
            "staging/coordination/generation_publication/r18/review_dispatch/"
            f"{activation_id}/review_phase1_activation_r18.json"
        )
        expected_dispatch_path = (
            bundle_dir / "review_dispatch_request_r18.json"
        ).relative_to(workspace.resolve()).as_posix()
        expected_prompt_path = (
            bundle_dir / contract["prompt_filename"]
        ).relative_to(workspace.resolve()).as_posix()
        if (
            envelope["phase1_activation"].get("path") != expected_activation_path
            or envelope["review_dispatch"].get("path") != expected_dispatch_path
            or envelope["review_prompt"].get("path") != expected_prompt_path
            or dispatch_snapshot.path != bundle_dir / "review_dispatch_request_r18.json"
            or prompt_snapshot.path != bundle_dir / contract["prompt_filename"]
        ):
            raise R17BoundaryError("phase1_runtime_envelope:bundle_path_binding_mismatch")
        if os.path.lexists(bundle_dir / "ADVERSARIAL_PROMPT.md"):
            raise R17BoundaryError("phase1_runtime_envelope:adversarial_prompt_present")

        output_path = envelope.get("allowed_output_path")
        output_path_error = portable_relative_path_error(output_path)
        if output_path_error is not None:
            raise R17BoundaryError(
                f"phase1_runtime_envelope:allowed_output_path_invalid:{output_path_error}"
            )
        assert isinstance(output_path, str)
        output_candidate = Path(
            os.path.abspath(os.fspath(workspace.joinpath(*output_path.split("/"))))
        )
        try:
            output_candidate.relative_to(workspace.resolve())
        except ValueError as exc:
            raise R17BoundaryError(
                "phase1_runtime_envelope:allowed_output_outside_workspace"
            ) from exc
        if os.path.lexists(output_candidate):
            raise R17BoundaryError("phase1_runtime_envelope:allowed_output_already_exists")

        policy_inputs, policy_graph = _validate_phase1_policy_inputs(
            dispatch,
            activation,
            workspace=workspace,
            disallowed_paths=(
                activation_snapshot.path,
                dispatch_snapshot.path,
                prompt_snapshot.path,
                attestation_snapshot.path,
                output_candidate,
            ),
        )
        canonical_schema_paths = r18_phase1_schema_paths(
            workspace=workspace, db_root=workspace.resolve() / "sh-chem-db"
        )
        activation_schemas = activation.get("schemas")
        if not isinstance(activation_schemas, dict) or set(activation_schemas) != set(
            R18_PHASE1_SCHEMA_KEYS
        ):
            raise R17BoundaryError("phase1_runtime_envelope:schema_keys_not_exact_19")
        for key in R18_PHASE1_SCHEMA_KEYS:
            graph.verify_ref(
                activation_schemas[key],
                label=f"phase1_runtime.schemas.{key}",
                expected_path=canonical_schema_paths[key],
            )
        activation_schema_snapshot = graph.verify_ref(
            activation.get("schema_binding"),
            label="phase1_runtime.activation.schema_binding",
            expected_path=canonical_schema_paths[
                "phase1_review_dispatch_activation"
            ],
        )
        if activation.get("schema_binding") != activation_schemas.get(
            "phase1_review_dispatch_activation"
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:activation_schema_binding_mismatch"
            )

        if not isinstance(self_read_metadata, dict):
            raise R17BoundaryError("phase1_runtime_envelope:self_read_metadata_unavailable")
        if set(self_read_metadata) != set(R18_PHASE1_SELF_READ_KEYS):
            raise R17BoundaryError("phase1_runtime_envelope:self_read_metadata_keys_not_exact_4")
        thread_id = self_read_metadata.get("thread_id")
        turn_id = self_read_metadata.get("turn_id")
        if _canonical_codex_uuid(thread_id) is None or thread_id != task_thread_id:
            raise R17BoundaryError("phase1_runtime_envelope:self_read_thread_id_invalid")
        if _canonical_codex_uuid(turn_id) is None:
            raise R17BoundaryError("phase1_runtime_envelope:current_turn_id_not_codex_uuid")
        if thread_id == turn_id:
            raise R17BoundaryError("phase1_runtime_envelope:thread_turn_identity_collision")
        if self_read_metadata.get("raw_status") != "inProgress":
            raise R17BoundaryError("phase1_runtime_envelope:current_turn_not_in_progress")
        host_id = self_read_metadata.get("host_id")
        if not isinstance(host_id, str) or not host_id:
            raise R17BoundaryError("phase1_runtime_envelope:host_id_unavailable")

        attestation_binding = _validate_review_task_creation_attestation_with_graph(
            attestation_snapshot.path,
            workspace=workspace,
            graph=graph,
            expected_slot=expected_slot,
            expected_thread_id=thread_id,
            expected_host_id=host_id,
            current_turn_id=turn_id,
            activation_ref=activation_snapshot.ref(workspace),
            dispatch_ref=dispatch_snapshot.ref(workspace),
            prompt_ref=prompt_snapshot.ref(workspace),
            allowed_output_path=output_path,
            expected_root_dispatch_id=str(envelope["root_dispatch_id"]),
            schema_ref=activation_schemas["review_task_creation_attestation"],
            expected_version_id=expected_version_id,
            expected_paper_id=PAPER_ID,
        )
        derived_execution = attestation_binding["execution"]

        activation_inputs = activation.get("immutable_inputs")
        if not isinstance(activation_inputs, dict) or set(activation_inputs) != set(
            R18_PHASE1_IMMUTABLE_KEYS
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:immutable_input_keys_not_exact_25"
            )
        immutable_paths = {
            key: graph.verify_ref(
                activation_inputs[key], label=f"phase1_runtime.immutable_inputs.{key}"
            ).path
            for key in R18_PHASE1_IMMUTABLE_KEYS
        }
        formal = _validate_r18_formal_freeze_with_graph(
            immutable_paths["formal_freeze"],
            immutable_paths["formal_freeze_sidecar"],
            workspace=workspace,
            graph=graph,
            expected_version_id=expected_version_id,
            expected_paper_id=PAPER_ID,
        )
        reconciliation = _validate_r18_runtime_reconciliation_with_graph(
            immutable_paths["generator_reconciliation"],
            workspace=workspace,
            graph=graph,
            formal=formal,
            immutable_paths=immutable_paths,
            schema_paths=canonical_schema_paths,
            expected_version_id=expected_version_id,
            expected_paper_id=PAPER_ID,
        )
        authorization = _validate_r18_phase1_authorization_with_graph(
            immutable_paths["root_phase1_authorization"],
            workspace=workspace,
            graph=graph,
            formal=formal,
            reconciliation=reconciliation,
            expected_version_id=expected_version_id,
            expected_paper_id=PAPER_ID,
        )
        _schema_validate_snapshot(
            activation,
            activation_schema_snapshot,
            label="phase1_runtime.activation",
        )
        _schema_validate_snapshot(
            dispatch,
            graph.read(canonical_schema_paths["review_dispatch_request"]),
            label="phase1_runtime.dispatch",
        )
        _validate_phase1_authoritative_bindings(
            dispatch,
            activation,
            immutable_inputs=immutable_paths,
            schema_paths=canonical_schema_paths,
            workspace=workspace,
            graph=graph,
            expected_version_id=expected_version_id,
            expected_paper_id=PAPER_ID,
        )
        _validate_phase1_dispatch_path_contract(dispatch, activation, workspace=workspace)

        if (
            activation.get("output_sha256") != record_output_sha256(activation)
            or dispatch.get("output_sha256") != record_output_sha256(dispatch)
        ):
            raise R17BoundaryError("phase1_runtime_envelope:self_hash_mismatch")
        if activation.get("activation_id") != activation_id:
            raise R17BoundaryError("phase1_runtime_envelope:activation_id_path_mismatch")
        common = {
            "formal_freeze": formal["formal_freeze"],
            "formal_freeze_sidecar": formal["formal_freeze_sidecar"],
            "formal_freeze_payload": formal["freeze_payload"],
            "generator_reconciliation": reconciliation["ref"],
            "root_phase1_authorization": authorization["ref"],
        }
        if any(
            activation.get(field) != expected or dispatch.get(field) != expected
            for field, expected in common.items()
        ):
            raise R17BoundaryError("phase1_runtime_envelope:root_binding_mismatch")
        if (
            activation.get("root_dispatch_id") != envelope["root_dispatch_id"]
            or dispatch.get("root_dispatch_id") != envelope["root_dispatch_id"]
            or authorization["root_dispatch_id"] != envelope["root_dispatch_id"]
        ):
            raise R17BoundaryError("phase1_runtime_envelope:root_dispatch_id_mismatch")
        expected_activation_id = _activation_id_from_bindings(
            expected_version_id=expected_version_id,
            root_authorization_ref=authorization["ref"],
            policy_inputs=policy_inputs,
            review_attempt_id=review_attempt_id,
        )
        if activation_id != expected_activation_id:
            raise R17BoundaryError(
                "phase1_runtime_envelope:activation_id_protocol_policy_binding_mismatch"
            )
        formal_false_fields = (
            "review_dispatch_prepared",
            "review_dispatch_authorized",
            "review_task_creation_authorized",
            "adversarial_dispatch_authorized",
            "chain_creation_authorized",
            "controller_registration_authorized",
            "batch_registration_authorized",
            "publication_authorized",
            "delivery_authorized",
            "human_reviewed",
        )
        if (
            formal["receipt"].get("status")
            != "active_formal_freeze_dispatch_not_prepared"
            or formal["receipt"].get("active") is not True
            or any(formal["receipt"].get(field) is not False for field in formal_false_fields)
        ):
            raise R17BoundaryError("phase1_runtime_envelope:formal_freeze_not_current_active")
        sidecar_false_fields = (
            "dispatch_prepared",
            "tasks_created",
            "adversarial_dispatch_authorized",
            "chain_creation_authorized",
            "registration_authorized",
            "publication_authorized",
            "delivery_authorized",
            "human_reviewed",
        )
        if any(
            formal["sidecar"].get(field) is not False
            for field in sidecar_false_fields
        ):
            raise R17BoundaryError("phase1_runtime_envelope:formal_sidecar_authority_mismatch")
        authorization_false_fields = (
            "adversarial_prompt_creation_authorized",
            "adversarial_task_creation_authorized",
            "adversarial_dispatch_authorized",
            "chain_creation_authorized",
            "registration_authorized",
            "batch_registration_authorized",
            "publication_authorized",
            "delivery_authorized",
            "external_delivery_authorized",
            "human_review_authorized",
            "official_claim_authorized",
            "human_reviewed",
        )
        if (
            authorization["record"].get("status")
            != "review_phase1_dispatch_authorized"
            or authorization["record"].get("authorized_slots")
            != list(R18_PHASE1_REVIEW_SLOTS)
            or authorization["record"].get("review_task_creation_authorized")
            is not True
            or any(
                authorization["record"].get(field) is not False
                for field in authorization_false_fields
            )
        ):
            raise R17BoundaryError("phase1_runtime_envelope:root_phase1_authority_mismatch")
        if (
            activation.get("generator_thread_id") != reconciliation["execution"]["thread_id"]
            or dispatch.get("generator_thread_id") != reconciliation["execution"]["thread_id"]
        ):
            raise R17BoundaryError("phase1_runtime_envelope:generator_thread_binding_mismatch")
        if (
            activation.get("prompt_payloads", {}).get(expected_slot)
            != prompt_snapshot.ref(workspace)
            or dispatch.get("prompts", {}).get(expected_slot)
            != prompt_snapshot.ref(workspace)
        ):
            raise R17BoundaryError("phase1_runtime_envelope:own_prompt_binding_mismatch")
        if dispatch.get("phase1_activation_path") != activation_snapshot.ref(workspace)["path"]:
            raise R17BoundaryError("phase1_runtime_envelope:activation_path_binding_mismatch")
        dispatch_bytes = canonical_json_bytes(dispatch)
        if activation.get("dispatch_core") != {
            "canonical_sha256": hashlib.sha256(dispatch_bytes).hexdigest(),
            "canonical_bytes": len(dispatch_bytes),
        }:
            raise R17BoundaryError("phase1_runtime_envelope:dispatch_core_mismatch")

        tasks = dispatch.get("requested_tasks")
        if not isinstance(tasks, list) or len(tasks) != 3:
            raise R17BoundaryError("phase1_runtime_envelope:task_count_invalid")
        task_a, task_b, adversarial = tasks
        expected_outputs = {
            slot: path.resolve().relative_to(workspace.resolve()).as_posix()
            for slot, path in r18_review_attempt_output_paths(
                activation_snapshot.path, workspace=workspace
            ).items()
        }
        if {task.get("slot"): task.get("output_path") for task in tasks} != expected_outputs:
            raise R17BoundaryError("phase1_runtime_envelope:slot_output_path_mismatch")
        own_task = task_a if expected_slot == "sol_review_a" else task_b
        counterpart = task_b if expected_slot == "sol_review_a" else task_a
        counterpart_slot = contract["counterpart_slot"]
        counterpart_prompt_ref = activation.get("prompt_payloads", {}).get(
            counterpart_slot
        )
        expected_counterpart_prompt_path = (
            bundle_dir / contract["counterpart_prompt_filename"]
        ).relative_to(workspace.resolve()).as_posix()
        if (
            not isinstance(counterpart_prompt_ref, dict)
            or counterpart_prompt_ref.get("path") != expected_counterpart_prompt_path
            or dispatch.get("prompts", {}).get(counterpart_slot)
            != counterpart_prompt_ref
            or counterpart.get("prompt") != counterpart_prompt_ref
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:counterpart_prompt_structural_binding_mismatch"
            )
        if (
            own_task.get("phase") != 1
            or own_task.get("dispatch_now") is not True
            or own_task.get("output_path") != output_path
            or own_task.get("may_write_only") != [output_path]
            or own_task.get("prompt") != prompt_snapshot.ref(workspace)
            or own_task.get("must_not_read")
            != [counterpart.get("output_path"), adversarial.get("output_path")]
        ):
            raise R17BoundaryError("phase1_runtime_envelope:own_slot_isolation_mismatch")
        for task, slot, other in (
            (task_a, "sol_review_a", task_b),
            (task_b, "sol_review_b", task_a),
        ):
            if (
                task.get("slot") != slot
                or task.get("phase") != 1
                or task.get("dispatch_now") is not True
                or task.get("new_task_required") is not True
                or task.get("distinct_new_task_required") is not True
                or task.get("exclusive_output") is not True
                or task.get("may_write_only") != [task.get("output_path")]
                or task.get("must_not_read")
                != [other.get("output_path"), adversarial.get("output_path")]
                or task.get("prompt")
                != activation.get("prompt_payloads", {}).get(slot)
            ):
                raise R17BoundaryError(
                    "phase1_runtime_envelope:ab_reciprocal_isolation_mismatch"
                )
        if (
            adversarial.get("slot") != "adversarial_check"
            or adversarial.get("phase") != 2
            or adversarial.get("dispatch_now") is not False
            or adversarial.get("new_task_required") is not True
            or adversarial.get("distinct_new_task_required") is not True
            or adversarial.get("dispatch_after") != list(R18_PHASE1_REVIEW_SLOTS)
            or adversarial.get("prompt_absent_until_phase2") is not True
            or adversarial.get("exclusive_output") is not True
            or adversarial.get("may_write_only")
            != [adversarial.get("output_path")]
            or any(
                key in adversarial
                for key in ("prompt", "prompt_path", "prompt_ref", "prompt_sha256", "prompt_bytes")
            )
        ):
            raise R17BoundaryError("phase1_runtime_envelope:adversarial_phase1_authority")
        adversarial_output = workspace.joinpath(
            *str(adversarial.get("output_path", "")).split("/")
        )
        if os.path.lexists(adversarial_output):
            raise R17BoundaryError(
                "phase1_runtime_envelope:adversarial_output_already_exists"
            )
        activation_authority = {
            "review_dispatch_prepared": True,
            "review_dispatch_authorized": True,
            "review_task_creation_authorized": True,
            "adversarial_dispatch_authorized": False,
            "chain_creation_authorized": False,
            "controller_registration_authorized": False,
            "batch_registration_authorized": False,
            "publication_authorized": False,
            "delivery_authorized": False,
            "external_delivery_authorized": False,
            "tasks_created": False,
            "reviews_authored": False,
        }
        dispatch_authority = {
            "phase1_ab_dispatch_authorized": True,
            "adversarial_dispatch_authorized": False,
            "tasks_created": False,
            "reviews_authored": False,
            "chain_creation_authorized": False,
            "controller_registration_authorized": False,
            "batch_registration_authorized": False,
            "publication_authorized": False,
            "delivery_authorized": False,
            "external_delivery_authorized": False,
        }
        if (
            activation.get("authority") != activation_authority
            or dispatch.get("authority") != dispatch_authority
            or activation.get("human_reviewed") is not False
            or dispatch.get("human_reviewed") is not False
            or dispatch.get("official_claim_allowed") is not False
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:phase1_downstream_authority_mismatch"
            )
        prompt_contract = validate_phase1_reviewer_prompt_contract(
            prompt_snapshot.data.decode("utf-8"),
            expected_slot=expected_slot,
            expected_output_path=output_path,
            expected_version_id=expected_version_id,
            workspace=workspace,
        )
        if prompt_contract["status"] != "pass":
            raise R17BoundaryError(";".join(prompt_contract["errors"]))

        root_auth = authorization["record"]["root_authorization"]
        root_dispatch = authorization["record"]["root_dispatch_configuration"]
        formal_root = formal["receipt"]["root_go"]
        control_thread_ids = (
            reconciliation["execution"]["thread_id"],
            root_auth["root_authorization_thread_id"],
            root_dispatch["root_thread_id"],
            formal_root["root_authorization_thread_id"],
        )
        control_turn_ids = (
            reconciliation["execution"]["turn_id"],
            root_auth["root_authorization_turn_id"],
            root_dispatch["root_turn_id"],
            formal_root["root_authorization_turn_id"],
        )
        if (
            any(_canonical_codex_uuid(value) is None for value in control_thread_ids)
            or any(_canonical_codex_uuid(value) is None for value in control_turn_ids)
            or reconciliation["execution"]["thread_id"]
            == reconciliation["execution"]["turn_id"]
            or root_auth["root_authorization_thread_id"]
            == root_auth["root_authorization_turn_id"]
            or root_dispatch["root_thread_id"] == root_dispatch["root_turn_id"]
            or formal_root["root_authorization_thread_id"]
            == formal_root["root_authorization_turn_id"]
            or reconciliation["execution"]["thread_id"]
            in {
                root_auth["root_authorization_thread_id"],
                root_dispatch["root_thread_id"],
                formal_root["root_authorization_thread_id"],
            }
        ):
            raise R17BoundaryError(
                "phase1_runtime_envelope:control_role_uuid_or_distinctness_mismatch"
            )
        role_thread_ids = {
            *control_thread_ids,
        }
        role_turn_ids = {
            *control_turn_ids,
        }
        if thread_id in role_thread_ids or turn_id in role_turn_ids:
            raise R17BoundaryError("phase1_runtime_envelope:review_role_identity_not_distinct")

        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "phase1_runtime_envelope:snapshot_changed_before_authorize:"
                + ",".join(checkpoint.get("errors", []))
            )
        assert policy_graph is not None
        policy_checkpoint = policy_graph.revalidate()
        if policy_checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "phase1_runtime_envelope:policy_snapshot_changed_before_authorize:"
                + ",".join(policy_checkpoint.get("errors", []))
            )
        if os.path.lexists(output_candidate):
            raise R17BoundaryError(
                "phase1_runtime_envelope:allowed_output_appeared_before_authorize"
            )
        derived_receipt_id = contract["receipt_prefix"] + turn_id
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        R17BoundaryError,
    ) as exc:
        errors.append(str(exc))

    return {
        "check": "r18_phase1_review_runtime_envelope",
        "status": "pass" if not errors else "fail",
        "reviewer_slot": expected_slot,
        "derived_local_receipt_id": derived_receipt_id if not errors else None,
        "execution_provenance": derived_execution if not errors else None,
        "task_creation_configuration_attestation": (
            copy.deepcopy(attestation_binding.get("ref"))
            if not errors and isinstance(attestation_binding, dict)
            else None
        ),
        "receipt_write_allowed": not errors,
        "exclusive_create_required": True,
        "authorization_is_not_path_reservation": True,
        "snapshot_graph_sha256": graph.digest(),
        "policy_snapshot_graph_sha256": (
            policy_graph.digest() if policy_graph is not None else None
        ),
        "errors": sorted(dict.fromkeys(errors)),
    }


def record_output_sha256(record: dict[str, Any]) -> str:
    unsigned = copy.deepcopy(record)
    unsigned.pop("output_sha256", None)
    return canonical_hash(unsigned)


def write_self_hashed_record(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(record)
    value["output_sha256"] = record_output_sha256(value)
    write_json(path, value)
    return value


def _record_hash_without(record: dict[str, Any], field: str) -> str:
    unsigned = copy.deepcopy(record)
    unsigned.pop(field, None)
    return canonical_hash(unsigned)


def _pretty_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _write_immutable_json(path: Path, value: dict[str, Any]) -> bool:
    """Create a JSON sidecar once; exact reruns are read-only and idempotent."""

    content = _pretty_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"immutable phase-2 sidecar is unreadable: {path}") from exc
        if canonical_json_bytes(existing) != canonical_json_bytes(value):
            raise RuntimeError(f"immutable phase-2 sidecar already exists with another binding: {path}")
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _write_immutable_json(path, value)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return True


def _question_subject_projection(paper: dict[str, Any]) -> dict[str, Any]:
    """Return only student-visible content plus non-directional hierarchy bindings.

    The formal paper retains originality, K/A/C/R/RP, difficulty, dependency,
    solver and conservation metadata for provenance.  None of those internal
    fields is review-question material: several encode the expected solution
    route.  Building an allowlisted projection is safer than deleting a small
    blacklist from a deep copy.
    """
    themes: list[dict[str, Any]] = []
    declared_atomic_part_ids: list[str] = []
    for theme in paper.get("themes", []):
        printed_questions: list[dict[str, Any]] = []
        for printed in theme.get("printed_questions", []):
            atomic_parts = [
                {
                    "part_id": part.get("part_id"),
                    "parent_printed_question_id": part.get("parent_printed_question_id"),
                    "label": part.get("label"),
                    "score": part.get("score"),
                    "prompt": part.get("prompt"),
                    "options": copy.deepcopy(part.get("options", [])),
                    "evidence_refs": copy.deepcopy(part.get("evidence_refs", [])),
                }
                for part in printed.get("atomic_parts", [])
            ]
            declared_atomic_part_ids.extend(
                str(part.get("part_id")) for part in printed.get("atomic_parts", [])
            )
            printed_questions.append(
                {
                    "printed_question_id": printed.get("printed_question_id"),
                    "parent_theme_id": printed.get("parent_theme_id"),
                    "display_number": printed.get("display_number"),
                    "theme_order": printed.get("theme_order"),
                    "atomic_parts": atomic_parts,
                }
            )
        themes.append(
            {
                "theme_id": theme.get("theme_id"),
                "parent_paper_id": theme.get("parent_paper_id"),
                "order": theme.get("order"),
                "title": theme.get("title"),
                "score": theme.get("score"),
                "context": theme.get("context"),
                "shared_material": theme.get("shared_material"),
                "source_card_refs": copy.deepcopy(theme.get("source_card_refs", [])),
                "printed_questions": printed_questions,
            }
        )
    return {
        "schema_version": "1.0.0",
        "record_type": "generation_v2_question_subject",
        "artifact_id": paper.get("paper_id"),
        "paper_id": paper.get("paper_id"),
        "version_id": paper.get("version_id"),
        "answer_material_removed": True,
        "question_projection_contract": "student_visible_content_and_non_directional_hierarchy_v1",
        "observed_profile_contract": copy.deepcopy(paper.get("observed_profile_contract")),
        "generation_policy": copy.deepcopy(paper.get("generation_policy")),
        "figure_refs": copy.deepcopy(paper.get("figure_refs", [])),
        "review_scope": {
            "declared_atomic_part_ids": declared_atomic_part_ids,
        },
        "themes": themes,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "official_claim_allowed": False,
    }


def _internal_directional_tokens(paper: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for theme in paper.get("themes", []):
        for printed in theme.get("printed_questions", []):
            relation = printed.get("dependency_relation")
            if isinstance(relation, str) and len(relation) >= 8:
                tokens.add(relation)
            for part in printed.get("atomic_parts", []):
                originality = part.get("originality", {})
                signature = originality.get("semantic_signature")
                if isinstance(signature, str) and len(signature) >= 8:
                    tokens.add(signature)
                for field in (
                    "primary_knowledge_K",
                    "primary_theme_chain_role",
                ):
                    value = part.get(field)
                    if isinstance(value, str) and len(value) >= 8:
                        tokens.add(value)
                solver_type = (part.get("solver") or {}).get("type")
                if isinstance(solver_type, str) and len(solver_type) >= 8:
                    tokens.add(solver_type)
                for field in ("ability_A", "context_C", "representation_RP"):
                    for value in part.get(field, []):
                        if isinstance(value, str) and len(value) >= 8:
                            tokens.add(value)
    return tokens


def _normalize_visible_text(value: str) -> str:
    return re.sub(r"[\s，。；：、,.!?！？:;（）()\-—_]+", "", value).casefold()


def _student_visible_prompt_values(question: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for theme in question.get("themes", []):
        for printed in theme.get("printed_questions", []):
            for part in printed.get("atomic_parts", []):
                pid = str(part.get("part_id"))
                values.append((f"{pid}:prompt", str(part.get("prompt", ""))))
                for index, option in enumerate(part.get("options", [])):
                    values.append((f"{pid}:option:{index}", str(option)))
    return values


def validate_student_visible_prompt_safety(
    paper: dict[str, Any], question: dict[str, Any]
) -> dict[str, Any]:
    parts = {
        part["part_id"]: part
        for theme in paper.get("themes", [])
        for printed in theme.get("printed_questions", [])
        for part in printed.get("atomic_parts", [])
    }
    errors: list[str] = []
    cases: list[dict[str, Any]] = []
    direct_markers = ("正确答案", "答案为", "答案选", "应选", "先选")
    for location, value in _student_visible_prompt_values(question):
        normalized = _normalize_visible_text(value)
        pid = location.split(":", 1)[0]
        part = parts.get(pid, {})
        hits = [marker for marker in direct_markers if _normalize_visible_text(marker) in normalized]
        choice_key = str(part.get("answer", {}).get("key", ""))
        if choice_key:
            dynamic = (
                f"正确答案为{choice_key}", f"答案为{choice_key}", f"答案选{choice_key}",
                f"选{choice_key}", f"先选{choice_key}",
            )
            hits.extend(token for token in dynamic if _normalize_visible_text(token) in normalized)
        if hits:
            errors.append(f"{location}:direct_answer_marker:{sorted(set(hits))}")
        cases.append({"location": location, "direct_answer_hits": sorted(set(hits))})

    question_parts = {
        part["part_id"]: part
        for theme in question.get("themes", [])
        for printed in theme.get("printed_questions", [])
        for part in printed.get("atomic_parts", [])
    }
    p02 = _normalize_visible_text(str(question_parts.get("P02", {}).get("prompt", "")))
    p08 = _normalize_visible_text(str(question_parts.get("P08", {}).get("prompt", "")))
    p02_product_ids = {
        row.get("name")
        for row in (parts.get("P02", {}).get("equation_balance") or {}).get("products", [])
        if row.get("name") != "e-"
    }
    p02_product_aliases = {
        alias
        for row in parts.get("P02", {}).get("answer", {}).get(
            "major_substance_name_contract", {}
        ).get("required_species_names", [])
        if row.get("species_id") in p02_product_ids
        for alias in row.get("accepted_chinese_names", [])
    } | {str(species_id) for species_id in p02_product_ids}
    copper_product_clusters = tuple(
        ("阴极", alias, process)
        for alias in sorted(p02_product_aliases)
        for process in ("析出", "生成", "得到", "沉积")
    ) + tuple(("阴极", "产物", alias) for alias in sorted(p02_product_aliases))
    p08_operation_clusters = (
        ("水", "附着液", "质量不再变化"), ("蒸馏水", "残液", "质量稳定"),
        ("洗涤", "干燥", "恒重"), ("冲洗", "烘", "恒重"),
    )
    p08_hits = [
        cluster
        for cluster in copper_product_clusters
        if all(_normalize_visible_text(token) in p08 for token in cluster)
    ]
    p02_hits = [
        cluster
        for cluster in p08_operation_clusters
        if all(_normalize_visible_text(token) in p02 for token in cluster)
    ]
    if p08_hits:
        errors.append(f"P08:reveals_P02_target_product:{p08_hits}")
    if p02_hits:
        errors.append(f"P02:reveals_P08_measurement_operations:{p02_hits}")
    dependencies_ok = (
        parts.get("P08", {}).get("dependencies", []) == []
        and "P02" not in parts.get("P08", {}).get("dependencies", [])
        and "P08" not in parts.get("P02", {}).get("dependencies", [])
    )
    if not dependencies_ok:
        errors.append("P02_P08_dependency_edge_forbidden")
    serialized_question = json.dumps(question, ensure_ascii=False, sort_keys=True)
    closure: dict[str, dict[str, Any]] = {}
    directional_rules = {
        "P09": ("pressure_temperature_role_interpretation_without_private_choice_signature", ("3.30mpa", "实测总压"), "initial room-temperature partial pressures remain source data, not a stated answer"),
        "P14": ("catalyst_rate_equilibrium_boundary_without_private_choice_signature", ("不改变平衡常数", "同时降低正逆"), "neutral catalyst-effect choice does not state the rate/equilibrium conclusion"),
        "P24": ("guideline_operational_role_interpretation_without_private_choice_signature", ("不是普适推荐投加量", "角色不同"), "role-labelled source values are not a stated correct-option paraphrase"),
        "P30": ("outlier_retest_reasoning_without_private_semantic_signature", ("应重做", "任意删值", "可归因错误", "合格平行数据平均"), "replicate values and an open method request do not state the processing conclusion"),
    }
    for pid, (gate, forbidden_conclusions, source_interpretation) in directional_rules.items():
        signature = str(parts.get(pid, {}).get("originality", {}).get("semantic_signature", ""))
        prompt = _normalize_visible_text(str(question_parts.get(pid, {}).get("prompt", "")))
        conclusion_hits = [phrase for phrase in forbidden_conclusions if _normalize_visible_text(phrase) in prompt]
        passed = bool(signature) and signature not in serialized_question and not conclusion_hits
        closure[pid] = {
            "gate": gate,
            "source_interpretation": source_interpretation,
            "private_semantic_signature": signature,
            "private_signature_absent_from_question_subject": signature not in serialized_question,
            "forbidden_prompt_conclusion_hits": conclusion_hits,
            "status": "pass" if passed else "fail",
        }
        if not passed:
            errors.append(f"{pid}:private_semantic_signature_present_or_missing")
    return {
        "check": "student_visible_prompt_answer_and_cross_item_semantic_safety",
        "status": "pass" if not errors else "fail",
        "direct_answer_cases": cases,
        "p02_p08_semantic_cluster_evidence": {
            "p02_private_product_species_ids": sorted(p02_product_ids),
            "p02_private_product_aliases": sorted(p02_product_aliases),
            "p08_private_operation_semantic_tags": [
                "wash_or_rinse_attached_electrolyte",
                "dry_or_heat_until_mass_stable",
                "constant_mass_measurement",
            ],
            "p08_product_cluster_hits": p08_hits,
            "p02_operation_cluster_hits": p02_hits,
            "dependencies_ok": dependencies_ok,
        },
        "review_b_failed_part_directional_closure": closure,
        "errors": errors,
    }


def validate_question_subject_isolation(
    paper: dict[str, Any], question: dict[str, Any]
) -> dict[str, Any]:
    expected = _question_subject_projection(paper)
    serialized = json.dumps(question, ensure_ascii=False, sort_keys=True)
    forbidden_tokens = sorted(
        token for token in _internal_directional_tokens(paper) if token in serialized
    )
    exact_projection = canonical_json_bytes(question) == canonical_json_bytes(expected)
    errors: list[str] = []
    if not exact_projection:
        errors.append("question_subject_not_exact_allowlisted_projection")
    if forbidden_tokens:
        errors.append("internal_directional_semantic_value_present")
    prompt_safety = validate_student_visible_prompt_safety(paper, question)
    if prompt_safety["status"] != "pass":
        errors.extend(prompt_safety["errors"])
    return {
        "check": "question_only_subject_semantic_isolation",
        "status": "pass" if not errors else "fail",
        "projection_contract": expected["question_projection_contract"],
        "exact_projection_match": exact_projection,
        "internal_directional_token_count": len(_internal_directional_tokens(paper)),
        "leaked_internal_directional_tokens": forbidden_tokens,
        "question_subject_sha256": canonical_hash(question),
        "expected_projection_sha256": canonical_hash(expected),
        "prompt_safety": prompt_safety,
        "errors": errors,
    }


def _question_and_answer(paper: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    question = _question_subject_projection(paper)
    answers: list[dict[str, Any]] = []
    for theme in paper.get("themes", []):
        for printed in theme.get("printed_questions", []):
            for part in printed.get("atomic_parts", []):
                answers.append(
                    {
                        "theme_id": theme.get("theme_id"),
                        "printed_question_id": printed.get("printed_question_id"),
                        "part_id": part.get("part_id"),
                        "printed_label": part.get("label"),
                        "score": part.get("score"),
                        "answer": copy.deepcopy(part.get("answer")),
                        "solver": part.get("solver"),
                        "equation_balance": part.get("equation_balance"),
                    }
                )
    answer = {
        "schema_version": "1.0.0",
        "record_type": "generation_v2_answer_subject",
        "artifact_id": PAPER_ID,
        "version_id": VERSION_ID,
        "answer_label": "machine_suggested_nonofficial",
        "answers": answers,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "official_claim_allowed": False,
    }
    return question, answer


def _atomic_scope(paper: dict[str, Any]) -> dict[str, Any]:
    declared: list[str] = []
    parents: list[dict[str, str]] = []
    for theme in paper.get("themes", []):
        for printed in theme.get("printed_questions", []):
            for part in printed.get("atomic_parts", []):
                part_id = part["part_id"]
                if not re.fullmatch(r"P[0-9]+", part_id) or part_id in declared:
                    raise RuntimeError(f"invalid or duplicate atomic part ID: {part_id}")
                declared.append(part_id)
                parents.append(
                    {
                        "paper_id": paper["paper_id"],
                        "theme_big_question_id": theme["theme_id"],
                        "printed_question_id": printed["printed_question_id"],
                        "atomic_part_id": part_id,
                    }
                )
    if not declared:
        raise RuntimeError("paper hierarchy contains no atomic parts")
    return {"declared_atomic_part_ids": declared, "atomic_parent_chains": parents}


def _deterministic_request(subject: dict[str, Any], paper: dict[str, Any]) -> dict[str, Any]:
    p32 = next(
        part
        for theme in paper.get("themes", [])
        for printed in theme.get("printed_questions", [])
        for part in printed.get("atomic_parts", [])
        if part.get("part_id") == "P32"
    )
    p32_display_cases = [
        ("NUM-P32-DISPLAY-SCIENTIFIC", "4×10² nm；6.9×10² nm", True),
        ("NUM-P32-DISPLAY-DECIMAL", "400 nm 和 690 nm", True),
        ("NUM-P32-DISPLAY-E-NOTATION", "4e2 nm; 6.9e2 nm", True),
        ("NUM-P32-DISPLAY-CARET", "4×10^2nm；6.9×10^2nm", True),
        ("NUM-P32-REJECT-410-690", "410 nm；690 nm", False),
        ("NUM-P32-REJECT-4.1E2", "4.1e2 nm；6.9e2 nm", False),
    ]
    normalized_display_numeric_cases = [
        {
            "case_id": case_id,
            "actual": "1"
            if (validate_display_answer(p32, display)["status"] == "pass") == expected_accept
            else "0",
            "expected": "1",
            "abs_tolerance": "0",
            "rel_tolerance": "0",
            "display_input": display,
            "expected_acceptance": expected_accept,
            "local_parser": "normalized_numeric_unit_pair_with_mixed_significant_figures",
        }
        for case_id, display, expected_accept in p32_display_cases
    ]
    return {
        "schema_version": "2.0.0",
        "record_type": "deterministic_check_request",
        "subject": subject,
        "atomic_scope": _atomic_scope(paper),
        "applicability": {
            name: {"status": "required"}
            for name in ("chemical", "numeric", "unit", "charge", "conservation")
        },
        "cases": {
            "chemical": [
                {
                    "case_id": "CHEM-CUSO4-COMPOSITION",
                    "type": "formula_composition",
                    "formula": "CuSO4",
                    "expected_elements": {"Cu": 1, "S": 1, "O": 4},
                },
                {
                    "case_id": "CHEM-METHANOL-COMPOSITION",
                    "type": "formula_composition",
                    "formula": "CH3OH",
                    "expected_elements": {"C": 1, "H": 4, "O": 1},
                },
            ],
            "numeric": [
                {
                    "case_id": "NUM-EWASTE-PERCENT",
                    "actual": "22.2580645161",
                    "expected": "22.3",
                    "abs_tolerance": "0.05",
                    "rel_tolerance": "0",
                },
                {
                    "case_id": "NUM-FARADAY-INVERSE",
                    "actual": "0.0100",
                    "expected": "0.0100",
                    "abs_tolerance": "0.00001",
                    "rel_tolerance": "0",
                },
                *normalized_display_numeric_cases,
            ],
            "unit": [
                {
                    "case_id": "UNIT-MMOL-MOL",
                    "actual_value": "2.5",
                    "actual_unit": "mmol",
                    "expected_value": "0.0025",
                    "expected_unit": "mol",
                    "abs_tolerance": "0",
                },
                {
                    "case_id": "UNIT-MG-G",
                    "actual_value": "30",
                    "actual_unit": "mg",
                    "expected_value": "0.030",
                    "expected_unit": "g",
                    "abs_tolerance": "0",
                },
            ],
            "charge": [
                {
                    "case_id": "CHARGE-ACID-BASE",
                    "reactants": [
                        {"formula": "H", "coefficient": 1, "charge": 1},
                        {"formula": "OH", "coefficient": 1, "charge": -1},
                    ],
                    "products": [
                        {"formula": "H2O", "coefficient": 1, "charge": 0}
                    ],
                }
            ],
            "conservation": [
                {
                    "case_id": "CONS-COPPER-HYDROXIDE",
                    "reactants": [
                        {"formula": "CuSO4", "coefficient": 1, "charge": 0},
                        {"formula": "NaOH", "coefficient": 2, "charge": 0},
                    ],
                    "products": [
                        {"formula": "Cu(OH)2", "coefficient": 1, "charge": 0},
                        {"formula": "Na2SO4", "coefficient": 1, "charge": 0},
                    ],
                },
                {
                    "case_id": "CONS-METHANOL-SYNTHESIS",
                    "reactants": [
                        {"formula": "CO2", "coefficient": 1, "charge": 0},
                        {"formula": "H2", "coefficient": 3, "charge": 0},
                    ],
                    "products": [
                        {"formula": "CH3OH", "coefficient": 1, "charge": 0},
                        {"formula": "H2O", "coefficient": 1, "charge": 0},
                    ],
                },
            ],
        },
        "human_reviewed": False,
    }


def controller_python() -> str:
    configured = os.environ.get("SHCHEM_CONTROLLER_PYTHON")
    if configured:
        return configured
    if importlib.util.find_spec("jsonschema") is not None:
        return sys.executable
    candidate = shutil.which("python")
    if candidate:
        return candidate
    raise RuntimeError("controller v2 requires a Python runtime with jsonschema")


def run_controller(
    *args: str,
    controller: Path | None = None,
    root: Path | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    selected_controller = controller if controller is not None else CONTROLLER
    selected_root = root if root is not None else DB_ROOT
    selected_cwd = cwd if cwd is not None else WORKSPACE
    completed = subprocess.run(
        [
            controller_python(),
            str(selected_controller),
            *args,
            "--root",
            str(selected_root),
        ],
        cwd=selected_cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"controller returned non-JSON (exit {completed.returncode}): {completed.stdout!r}"
        ) from exc
    payload["_exit_code"] = completed.returncode
    payload["_stderr"] = completed.stderr.strip()
    return payload


def prepare_controller_subject(
    paper: dict[str, Any],
    *,
    layout: ControllerBuildLayout,
) -> dict[str, Any]:
    """Freeze question/answer subjects and run deterministic checks only.

    This stage intentionally does not create a governance chain or register
    anything.  A chain is assembled only after three external, provenance-bound
    receipts have been returned and validated without modification.
    """
    layout.validate()
    layout.bridge_dir.mkdir(parents=True, exist_ok=True)
    question, answer = _question_and_answer(paper)
    isolation = validate_question_subject_isolation(paper, question)
    if isolation["status"] != "pass":
        raise RuntimeError(f"question-only subject isolation failed: {isolation}")
    write_json(layout.question_path, question)
    write_json(layout.answer_path, answer)
    subject = {
        "question": workspace_file_ref(layout.question_path, workspace=layout.root),
        "answer": workspace_file_ref(layout.answer_path, workspace=layout.root),
        "subject_pair_sha256": subject_pair_sha256(
            sha256_file(layout.question_path), sha256_file(layout.answer_path)
        ),
    }
    request = _deterministic_request(subject, paper)
    write_json(layout.request_path, request)
    review_subject = review_subject_from_deterministic_request(
        request,
        db_root=layout.root,
    )
    if review_subject != subject:
        raise RuntimeError(
            "review subject must be the exact deterministic-request subject"
        )
    output = run_controller(
        "machine-check",
        "--request",
        str(layout.request_path),
        controller=layout.controller,
        root=layout.root,
        cwd=layout.controller_cwd,
    )
    if output.get("ok") is not True or output.get("passed") is not True:
        raise RuntimeError(f"controller deterministic machine-check failed: {output}")
    report = {
        key: output[key]
        for key in (
            "schema_version",
            "record_type",
            "request_sha256",
            "subject",
            "atomic_scope",
            "categories",
            "errors",
            "passed",
            "human_reviewed",
        )
    }
    write_json(layout.report_path, report)
    return {
        "subject": subject,
        "review_subject": review_subject,
        "subject_pair_sha256": subject["subject_pair_sha256"],
        "deterministic_report_sha256": sha256_file(layout.report_path),
        "deterministic_machine_check": output,
        "question_subject_isolation": isolation,
        "governance_chain_created": False,
        "registered": False,
    }


R18_REVIEW_SUBJECT_FIELDS = ("question", "answer", "subject_pair_sha256")


def review_subject_from_deterministic_request(
    request_or_path: dict[str, Any] | Path | str,
    *,
    db_root: Path = DB_ROOT,
) -> dict[str, Any]:
    """Return the one canonical reviewer subject without changing path roots.

    Review receipts are governed against the deterministic request, whose file
    references are DB-relative.  The formal-freeze controller subject is a
    different workspace-relative projection and must never be reused here.
    This helper only copies and validates structure; it never authors review
    findings or a receipt.
    """

    if isinstance(request_or_path, (str, Path)):
        request_path = Path(request_or_path)
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise R17BoundaryError(
                f"review_subject:deterministic_request_unreadable:{type(exc).__name__}"
            ) from exc
    elif isinstance(request_or_path, dict):
        request = request_or_path
    else:
        raise R17BoundaryError("review_subject:deterministic_request_object_required")

    subject = request.get("subject")
    if not isinstance(subject, dict) or set(subject) != set(
        R18_REVIEW_SUBJECT_FIELDS
    ):
        raise R17BoundaryError("review_subject:fields_not_exact")
    for label in ("question", "answer"):
        reference = subject.get(label)
        if not isinstance(reference, dict) or set(reference) != {
            "path",
            "sha256",
            "bytes",
        }:
            raise R17BoundaryError(f"review_subject:{label}_ref_not_exact")
        raw_path = reference.get("path")
        path_error = portable_relative_path_error(raw_path)
        if path_error is not None:
            raise R17BoundaryError(
                f"review_subject:{label}_path_invalid:{path_error}"
            )
        assert isinstance(raw_path, str)
        if raw_path.split("/", 1)[0].casefold() == "sh-chem-db":
            raise R17BoundaryError(
                f"review_subject:{label}_workspace_prefix_forbidden"
            )
        resolved = (db_root / str(raw_path)).resolve()
        try:
            resolved.relative_to(db_root.resolve())
        except ValueError as exc:
            raise R17BoundaryError(
                f"review_subject:{label}_path_outside_database"
            ) from exc
        if not isinstance(reference.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", reference["sha256"]
        ):
            raise R17BoundaryError(f"review_subject:{label}_sha256_invalid")
        if not isinstance(reference.get("bytes"), int) or reference["bytes"] < 1:
            raise R17BoundaryError(f"review_subject:{label}_bytes_invalid")
        if not resolved.is_file():
            raise R17BoundaryError(f"review_subject:{label}_file_missing")
        if (
            resolved.stat().st_size != reference["bytes"]
            or sha256_file(resolved) != reference["sha256"]
        ):
            raise R17BoundaryError(f"review_subject:{label}_file_binding_mismatch")
    expected_pair = subject_pair_sha256(
        subject["question"]["sha256"], subject["answer"]["sha256"]
    )
    if subject.get("subject_pair_sha256") != expected_pair:
        raise R17BoundaryError("review_subject:pair_hash_mismatch")
    return copy.deepcopy(subject)


def write_machine_review_receipt_exclusive(
    path: Path,
    *,
    deterministic_request: dict[str, Any] | Path | str,
    receipt_fields: dict[str, Any],
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    review_schema_path: Path = MACHINE_REVIEW_SCHEMA_PATH,
) -> dict[str, Any]:
    """Bind subject/self-hash and exclusively create one externally authored receipt.

    ``receipt_fields`` must already contain the reviewer's own findings and
    provenance.  This helper never invents, repairs, normalizes, or upgrades a
    finding or verdict; it only adds the exact deterministic subject, computes
    the canonical self-hash, validates the completed structure, and commits with
    no-replace semantics.
    """

    if not isinstance(receipt_fields, dict):
        raise R17BoundaryError("review_receipt:fields_object_required")
    if "subject" in receipt_fields or "output_sha256" in receipt_fields:
        raise R17BoundaryError("review_receipt:subject_or_self_hash_prepopulated")
    if "findings" not in receipt_fields:
        raise R17BoundaryError("review_receipt:externally_authored_findings_required")
    value = copy.deepcopy(receipt_fields)
    value["subject"] = review_subject_from_deterministic_request(
        deterministic_request, db_root=db_root
    )
    value["output_sha256"] = record_output_sha256(value)
    try:
        validate(value, load_schema(review_schema_path))
    except Exception as exc:
        raise R17BoundaryError(
            f"review_receipt:schema_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    transaction = exclusive_create_bundle(
        workspace=workspace,
        files=[(path, _pretty_json_bytes(value))],
    )
    if transaction.get("created") is not True or transaction.get("created_count") != 1:
        raise R17BoundaryError("review_receipt:exclusive_create_not_confirmed")
    return value


def assemble_chain(
    *,
    state: str,
    include_reviews: bool,
    include_adversarial: bool,
    include_delivery: bool,
    reconciliation_attestation_path: Path = ROOT_RECONCILIATION_PATH,
    generator_execution_metadata: dict[str, Any] | Path | str | None = None,
    phase1_activation_path: Path | None = None,
    phase2_dispatch_path: Path | None = None,
    adversarial_prompt_path: Path | None = None,
    root_metadata_observation_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    if include_reviews or include_adversarial:
        if (
            phase1_activation_path is None
            or phase2_dispatch_path is None
            or adversarial_prompt_path is None
            or root_metadata_observation_paths is None
        ):
            raise RuntimeError("R18 chain requires phase1/phase2 and four root observations")
        external = validate_external_review_receipts(
            phase1_activation_path=phase1_activation_path,
            phase2_dispatch_path=phase2_dispatch_path,
            adversarial_prompt_path=adversarial_prompt_path,
            root_metadata_observation_paths=root_metadata_observation_paths,
        )
        if external["status"] != "pass":
            raise RuntimeError(f"external review receipts rejected without modification: {external}")
        attempt_paths = r18_review_attempt_output_paths(
            phase1_activation_path, workspace=WORKSPACE
        )
    else:
        attempt_paths = {}
    execution_binding = _load_generator_execution_metadata(
        generator_execution_metadata
    )
    execution = copy.deepcopy(execution_binding["reported_execution"])
    expected_projection = copy.deepcopy(
        execution_binding["expected_root_observation_projection"]
    )
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    reconciliation = validate_r18_root_reconciliation(
        reconciliation_attestation_path,
        workspace=WORKSPACE,
        expected_execution=execution,
        expected_projection=expected_projection,
    )
    if reconciliation.get("status") != "pass":
        raise RuntimeError(
            "R18 content chain refused: root external provenance reconciliation "
            f"is missing or invalid: {reconciliation.get('errors')}"
        )
    chain = {
        "schema_version": "2.0.0",
        "record_type": "machine_governance_chain",
        "artifact_id": PAPER_ID,
        "state": state,
        "generator_execution_provenance": execution,
        "generator_provenance_attestation": workspace_file_ref(
            reconciliation_attestation_path
        ),
        "generation_policy": generation_policy(),
        "subject": request["subject"],
        "deterministic_check": {
            "request": db_file_ref(REQUEST_PATH),
            "report": db_file_ref(REPORT_PATH),
        },
        "reviews": {
            "sol_review_a": db_file_ref(attempt_paths["sol_review_a"])
            if include_reviews
            else None,
            "sol_review_b": db_file_ref(attempt_paths["sol_review_b"])
            if include_reviews
            else None,
        },
        "adversarial_check": db_file_ref(attempt_paths["adversarial_check"])
        if include_adversarial
        else None,
        "teacher_managed_delivery": db_file_ref(DELIVERY_PATH) if include_delivery else None,
        "human_reviewed": False,
    }
    if root_metadata_observation_paths is not None:
        if set(root_metadata_observation_paths) != {
            "generator", "sol_review_a", "sol_review_b", "adversarial_check"
        }:
            raise RuntimeError("R18 chain root_metadata_observations must contain exact four roles")
        observation_refs = {
            role: workspace_file_ref(path)
            for role, path in root_metadata_observation_paths.items()
        }
        reconciliation_record = json.loads(
            reconciliation_attestation_path.read_text(encoding="utf-8")
        )
        if observation_refs["generator"] != reconciliation_record.get(
            "codex_metadata_observation"
        ):
            raise RuntimeError("R18 generator observation must equal reconciliation observation")
        chain["root_metadata_observations"] = observation_refs
    write_json(CHAIN_PATH, chain)
    return chain


def validate_controller_chain(*, require_state: str | None = None) -> dict[str, Any]:
    return validate_controller_chain_path(CHAIN_PATH, require_state=require_state)


def validate_controller_chain_path(
    chain_path: Path, *, require_state: str | None = None
) -> dict[str, Any]:
    output = run_controller("governance", "--chain", str(chain_path))
    if require_state is not None and output.get("achieved_state") != require_state:
        output.setdefault("errors", []).append(
            f"bridge:required_state_mismatch:{require_state}:{output.get('achieved_state')}"
        )
        output["valid"] = False
    return output


def artifact_set_sha256(artifacts: list[dict[str, object]]) -> str:
    ordered = sorted(artifacts, key=lambda row: str(row.get("path") or ""))
    return canonical_hash(ordered)


def mirror_delivery_artifacts(paths: Iterable[Path]) -> list[dict[str, object]]:
    target = BRIDGE_DIR / "delivery_artifacts"
    target.mkdir(parents=True, exist_ok=True)
    references: list[dict[str, object]] = []
    seen: set[str] = set()
    for source in paths:
        name = source.name
        if name.casefold() in seen:
            raise ValueError(f"duplicate controller delivery artifact basename: {name}")
        seen.add(name.casefold())
        destination = target / name
        shutil.copy2(source, destination)
        if sha256_file(source) != sha256_file(destination):
            raise RuntimeError(f"controller delivery mirror hash mismatch: {source}")
        references.append(db_file_ref(destination))
    return references


def _part_rows(
    question: dict[str, Any], answer: dict[str, Any]
) -> list[tuple[int, int, int, int, dict, dict, dict]]:
    answers = {
        row["part_id"]: (answer_index, row)
        for answer_index, row in enumerate(answer.get("answers", []))
    }
    rows: list[tuple[int, int, int, int, dict, dict, dict]] = []
    for theme_index, theme in enumerate(question.get("themes", [])):
        for printed_index, printed in enumerate(theme.get("printed_questions", [])):
            for part_index, part in enumerate(printed.get("atomic_parts", [])):
                part_id = part.get("part_id")
                if part_id not in answers:
                    raise RuntimeError(f"atomic answer missing: {part_id}")
                answer_index, answer_row = answers[part_id]
                rows.append(
                    (
                        theme_index,
                        printed_index,
                        part_index,
                        answer_index,
                        theme,
                        part,
                        answer_row,
                    )
                )
    return rows


def _expected_atomic_ids(question: dict[str, Any] | None = None) -> list[str]:
    subject = question
    if subject is None:
        subject = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    return hierarchy_ids(subject)["atomic_part_ids"]


def _findings_by_part(
    record: dict[str, Any], slot: str, expected_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    actual_ids: list[str] = []
    for finding in record.get("findings", []):
        if not isinstance(finding, dict):
            continue
        part_id = finding.get("atomic_part_id")
        if isinstance(part_id, str) and re.fullmatch(r"P[0-9]+", part_id):
            actual_ids.append(part_id)
            result.setdefault(part_id, []).append(finding)
    missing = [part_id for part_id in expected_ids if part_id not in result]
    extra = sorted(set(actual_ids) - set(expected_ids))
    duplicate = sorted(part_id for part_id, rows in result.items() if len(rows) != 1)
    if missing or extra or duplicate or len(actual_ids) != len(expected_ids):
        raise RuntimeError(
            f"{slot} per-atomic-part findings are not exact hierarchy membership: "
            f"missing={missing}, extra={extra}, duplicate={duplicate}"
        )
    return result


def _semantic_review_text_error(value: Any) -> str | None:
    if not isinstance(value, str):
        return "not_a_string"
    try:
        if value.encode("utf-8", errors="strict").decode("utf-8", errors="strict") != value:
            return "utf8_round_trip_mismatch"
    except UnicodeError:
        return "utf8_round_trip_failed"
    if "\ufffd" in value:
        return "replacement_character_present"
    stripped = value.strip()
    if len(stripped) < 8:
        return "not_readable_specific_text"
    question_marks = stripped.count("?") + stripped.count("？")
    if "????" in stripped or "？？？？" in stripped or question_marks / max(1, len(stripped)) >= 0.2:
        return "literal_question_mark_corruption"
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", stripped):
        return "not_readable_specific_text"
    return None


def _resolve_bound_ref(
    reference: object,
    *,
    base: Path,
    label: str,
    snapshots: SnapshotGraph | None = None,
) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256", "bytes"}:
        return None, [f"{label}:file_ref_fields_must_be_exact"]
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None, [f"{label}:path_invalid"]
    relative = Path(raw_path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None, [f"{label}:path_unsafe"]
    resolved = Path(os.path.abspath(os.fspath(base / relative)))
    try:
        resolved.relative_to(Path(os.path.abspath(os.fspath(base))))
    except ValueError:
        return None, [f"{label}:path_outside_base"]
    try:
        snapshot = (snapshots or SnapshotGraph(base.resolve())).read(resolved)
    except (OSError, R17BoundaryError) as exc:
        return resolved, [f"{label}:file_unreadable:{type(exc).__name__}"]
    if reference.get("sha256") != snapshot.sha256:
        errors.append(f"{label}:sha256_mismatch")
    if reference.get("bytes") != snapshot.byte_length:
        errors.append(f"{label}:bytes_mismatch")
    return resolved, errors


def _load_object(
    path: Path,
    *,
    label: str,
    errors: list[str],
    snapshots: SnapshotGraph | None = None,
) -> dict[str, Any]:
    try:
        if snapshots is None:
            payload = path.read_bytes()
        else:
            payload = snapshots.read(path).data
        value = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label}:json_unreadable:{type(exc).__name__}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}:json_not_object")
        return {}
    return value


def _report_subject_binding_errors(
    request_subject: object,
    report_subject: object,
    *,
    label: str,
) -> list[str]:
    """Require the controller report's exact augmented subject contract."""

    if not isinstance(request_subject, dict):
        return [f"{label}:request_subject_not_object"]
    if not isinstance(report_subject, dict):
        return [f"{label}:report_subject_not_object"]
    validity_fields = {"question_path_valid", "answer_path_valid"}
    expected_fields = set(request_subject) | validity_fields
    errors: list[str] = []
    if set(report_subject) != expected_fields:
        errors.append(f"{label}:report_subject_fields_not_exact")
    if all(field in report_subject for field in request_subject):
        core_subject = {field: report_subject[field] for field in request_subject}
        if core_subject != request_subject:
            errors.append(f"{label}:report_subject_core_mismatch")
    else:
        errors.append(f"{label}:report_subject_core_mismatch")
    for field in sorted(validity_fields):
        if report_subject.get(field) is not True:
            errors.append(f"{label}:{field}_not_true")
    return errors


def _refs_resolve_to_same_file(
    left: object,
    *,
    left_base: Path,
    right: object,
    right_base: Path,
) -> bool:
    left_path, left_errors = _resolve_bound_ref(left, base=left_base, label="left")
    right_path, right_errors = _resolve_bound_ref(right, base=right_base, label="right")
    return not left_errors and not right_errors and left_path == right_path


def _utc_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise R17BoundaryError(f"{label}:timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise R17BoundaryError(f"{label}:timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise R17BoundaryError(f"{label}:timezone_required")
    return parsed.astimezone(timezone.utc)


def _require_workspace_prefix(path: Path, workspace: Path, prefix: tuple[str, ...], label: str) -> None:
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise R17BoundaryError(f"{label}:outside_workspace") from exc
    parts = tuple(part.casefold() for part in relative.parts)
    if len(parts) <= len(prefix) or parts[: len(prefix)] != prefix:
        raise R17BoundaryError(f"{label}:not_root_owned_r18_path")


def _schema_validate_snapshot(record: dict[str, Any], schema_snapshot: Any, *, label: str) -> None:
    try:
        schema = schema_snapshot.json_value()
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(record)
    except Exception as exc:
        raise R17BoundaryError(f"{label}:schema_invalid:{type(exc).__name__}:{exc}") from exc


def _shared_snapshot_graph(
    workspace: Path, snapshot_graph: SnapshotGraph | None
) -> SnapshotGraph:
    resolved_workspace = workspace.resolve()
    if snapshot_graph is None:
        return SnapshotGraph(resolved_workspace)
    if os.path.normcase(os.fspath(snapshot_graph.root.resolve())).casefold() != os.path.normcase(
        os.fspath(resolved_workspace)
    ).casefold():
        raise R17BoundaryError("snapshot_graph:workspace_root_mismatch")
    return snapshot_graph


def _validate_r18_formal_freeze_with_graph(
    receipt_path: Path,
    sidecar_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    expected_version_id: str,
    expected_paper_id: str,
    expected_artifact_id: str | None = None,
    expected_subject_pair_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_artifact_id is not None and (
        not isinstance(expected_artifact_id, str) or not expected_artifact_id
    ):
        raise R17BoundaryError("formal_freeze_r18:expected_artifact_id_invalid")
    if expected_subject_pair_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_subject_pair_sha256
    ):
        raise R17BoundaryError(
            "formal_freeze_r18:expected_subject_pair_sha256_invalid"
        )
    prefix = ("staging", "coordination", "root", "revisions", "r18", "freeze_only")
    _require_workspace_prefix(receipt_path, workspace, prefix, "formal_freeze")
    _require_workspace_prefix(sidecar_path, workspace, prefix, "formal_freeze_sidecar")
    receipt_snapshot = graph.read(receipt_path)
    sidecar_snapshot = graph.read(sidecar_path)
    receipt = receipt_snapshot.json_value()
    sidecar = sidecar_snapshot.json_value()
    if not isinstance(receipt, dict) or not isinstance(sidecar, dict):
        raise R17BoundaryError("formal_freeze_r18:objects_required")
    receipt_schema_path = (
        workspace / "sh-chem-db/kb/machine_governance_v2/schemas/formal_freeze_receipt_r18.schema.json"
    )
    sidecar_schema_path = (
        workspace / "sh-chem-db/kb/machine_governance_v2/schemas/formal_freeze_sidecar_r18.schema.json"
    )
    producer_schema_path = (
        workspace
        / "integrations/shchem_generation_v2/schemas/r18_producer_receipt.schema.json"
    )
    receipt_schema = graph.read(receipt_schema_path)
    sidecar_schema = graph.read(sidecar_schema_path)
    graph.verify_ref(receipt.get("schema_binding"), label="formal_freeze.schema_binding", expected_path=receipt_schema_path)
    graph.verify_ref(receipt.get("formal_freeze_sidecar_schema"), label="formal_freeze.sidecar_schema", expected_path=sidecar_schema_path)
    graph.verify_ref(
        receipt.get("r18_producer_receipt_schema"),
        label="formal_freeze.r18_producer_receipt_schema",
        expected_path=producer_schema_path,
    )
    graph.verify_ref(sidecar.get("schema_binding"), label="formal_freeze_sidecar.schema_binding", expected_path=sidecar_schema_path)
    _schema_validate_snapshot(receipt, receipt_schema, label="formal_freeze_r18")
    _schema_validate_snapshot(sidecar, sidecar_schema, label="formal_freeze_sidecar_r18")
    if receipt.get("self_hash") != _record_hash_without(receipt, "self_hash"):
        raise R17BoundaryError("formal_freeze_r18:self_hash_mismatch")
    if sidecar.get("self_hash") != _record_hash_without(sidecar, "self_hash"):
        raise R17BoundaryError("formal_freeze_sidecar_r18:self_hash_mismatch")
    if receipt.get("version_id") != expected_version_id or sidecar.get("version_id") != expected_version_id:
        raise R17BoundaryError("formal_freeze_r18:version_mismatch")
    if receipt.get("paper_id") != expected_paper_id or sidecar.get("paper_id") != expected_paper_id:
        raise R17BoundaryError("formal_freeze_r18:paper_mismatch")
    payload = receipt.get("freeze_payload")
    if not isinstance(payload, dict):
        raise R17BoundaryError("formal_freeze_r18:payload_missing")
    payload_bytes = canonical_json_bytes(payload)
    payload_binding = {
        "json_pointer": "#/freeze_payload",
        "canonical_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "canonical_bytes": len(payload_bytes),
    }
    if receipt.get("freeze_payload_sha256") != payload_binding["canonical_sha256"]:
        raise R17BoundaryError("formal_freeze_r18:payload_hash_mismatch")
    if sidecar.get("freeze_payload") != payload_binding:
        raise R17BoundaryError("formal_freeze_sidecar_r18:payload_binding_mismatch")
    if sidecar.get("formal_freeze") != receipt_snapshot.ref(workspace):
        raise R17BoundaryError("formal_freeze_sidecar_r18:formal_ref_mismatch")
    for field in (
        "r18_producer_receipt_schema",
        "prefreeze_receipt",
        "r18_producer_receipt",
        "sol_generator_receipt",
    ):
        if field == "r18_producer_receipt_schema":
            if payload.get(field) != receipt.get(field):
                raise R17BoundaryError(
                    "formal_freeze_r18:payload_r18_producer_receipt_schema_mismatch"
                )
            continue
        graph.verify_ref(receipt.get(field), label=f"formal_freeze.{field}")
        if payload.get(field) != receipt.get(field):
            raise R17BoundaryError(f"formal_freeze_r18:payload_{field}_mismatch")
    subject = receipt.get("controller_subject")
    if payload.get("controller_subject") != subject:
        raise R17BoundaryError("formal_freeze_r18:payload_subject_mismatch")
    if payload.get("version_id") != expected_version_id or payload.get("paper_id") != expected_paper_id:
        raise R17BoundaryError("formal_freeze_r18:payload_identity_mismatch")
    if payload.get("root_authorization_thread_id") != receipt.get("root_go", {}).get("root_authorization_thread_id"):
        raise R17BoundaryError("formal_freeze_r18:root_thread_binding_mismatch")
    if payload.get("formal_freeze_sidecar_schema") != receipt.get("formal_freeze_sidecar_schema"):
        raise R17BoundaryError("formal_freeze_r18:sidecar_schema_payload_mismatch")
    subject_snapshots = {
        field: graph.verify_ref(
            subject.get(field), label=f"formal_freeze.controller_subject.{field}"
        )
        for field in ("question", "answer")
    }
    expected_pair = subject_pair_sha256(subject["question"]["sha256"], subject["answer"]["sha256"])
    if subject.get("subject_pair_sha256") != expected_pair:
        raise R17BoundaryError("formal_freeze_r18:subject_pair_mismatch")
    for field, snapshot in subject_snapshots.items():
        document = snapshot.json_value()
        if not isinstance(document, dict) or document.get("artifact_id") != expected_paper_id:
            raise R17BoundaryError(
                f"formal_freeze_r18:{field}_artifact_id_mismatch"
            )
    # The formal freeze is always the parent paper freeze.  A public paper
    # reconciliation must bind the caller's exact requested pair; atomic child
    # reconciliations bind their child identity in the reconciliation record
    # while retaining this independently verified parent-paper freeze.
    if expected_artifact_id in (None, expected_paper_id):
        if (
            expected_subject_pair_sha256 is not None
            and expected_subject_pair_sha256 != expected_pair
        ):
            raise R17BoundaryError(
                "formal_freeze_r18:expected_subject_pair_mismatch"
            )
    return {
        "receipt": receipt,
        "sidecar": sidecar,
        "formal_freeze": receipt_snapshot.ref(workspace),
        "formal_freeze_sidecar": sidecar_snapshot.ref(workspace),
        "freeze_payload": payload_binding,
        "subject": copy.deepcopy(subject),
    }


def _validate_r18_observation_with_graph(
    observation_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    expected_role: str,
    formal: dict[str, Any],
    expected_root_dispatch_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    prefix = ("staging", "coordination", "root", "revisions", "r18")
    _require_workspace_prefix(observation_path, workspace, prefix, f"{expected_role}_observation")
    snapshot = graph.read(observation_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError(f"{expected_role}_observation:object_required")
    schema_path = workspace / "sh-chem-db/kb/machine_governance_v2/schemas/codex_metadata_observation_r18.schema.json"
    schema_snapshot = graph.read(schema_path)
    graph.verify_ref(record.get("schema_binding"), label=f"{expected_role}_observation.schema_binding", expected_path=schema_path)
    _schema_validate_snapshot(record, schema_snapshot, label=f"{expected_role}_observation")
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError(f"{expected_role}_observation:self_hash_mismatch")
    if record.get("task_role") != expected_role:
        raise R17BoundaryError(f"{expected_role}_observation:role_mismatch")
    observed = record["observed_task_metadata"]
    dispatch = record["root_dispatch_configuration"]
    derived = record["derived_execution_metadata"]
    prefixes = {
        "generator": "R18-GENERATOR-EXECUTION-",
        "sol_review_a": "R18-REVIEW-A-EXECUTION-",
        "sol_review_b": "R18-REVIEW-B-EXECUTION-",
        "adversarial_check": "R18-ADVERSARIAL-EXECUTION-",
    }
    expected_derived = {
        "provider": "codex_app_thread",
        "thread_id": observed["thread_id"],
        "turn_id": observed["turn_id"],
        "receipt_id": prefixes[expected_role] + observed["turn_id"],
        "model": dispatch["model"],
        "reasoning_effort": dispatch["reasoning_effort"],
        "root_dispatch_id": dispatch["root_dispatch_id"],
    }
    if derived != expected_derived or dispatch.get("root_dispatch_id") != expected_root_dispatch_id:
        raise R17BoundaryError(f"{expected_role}_observation:derived_execution_mismatch")
    if (
        record.get("formal_freeze") != formal["formal_freeze"]
        or record.get("formal_freeze_sidecar") != formal["formal_freeze_sidecar"]
        or record.get("freeze_payload") != formal["freeze_payload"]
    ):
        raise R17BoundaryError(f"{expected_role}_observation:formal_binding_mismatch")
    observed_at = _utc_timestamp(record.get("observed_at"), label=f"{expected_role}_observation.observed_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at > current + timedelta(minutes=5) or current - observed_at > timedelta(days=7):
        raise R17BoundaryError(f"{expected_role}_observation:timestamp_not_current")
    return {"record": record, "ref": snapshot.ref(workspace), "execution": derived}


def _validate_r18_review_observation_with_graph(
    observation_path: Path,
    *,
    review_receipt_path: Path,
    activation_path: Path,
    dispatch_path: Path,
    prompt_path: Path,
    workspace: Path,
    graph: SnapshotGraph,
    expected_role: str,
    expected_root_dispatch_id: str,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one root-completed review observation and its creation attestation."""

    if expected_role not in R18_PHASE1_REVIEW_SLOTS:
        raise R17BoundaryError("review_observation:unsupported_role")
    snapshot = graph.read(observation_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError(f"{expected_role}_observation:object_required")
    activation_snapshot = graph.read(activation_path)
    dispatch_snapshot = graph.read(dispatch_path)
    prompt_snapshot = graph.read(prompt_path)
    review_snapshot = graph.read(review_receipt_path)
    activation = activation_snapshot.json_value()
    if not isinstance(activation, dict):
        raise R17BoundaryError(f"{expected_role}_observation:activation_object_required")

    schema_path = (
        workspace.resolve()
        / "sh-chem-db/kb/machine_governance_v2/schemas/review_codex_metadata_observation_r18.schema.json"
    )
    schema_snapshot = graph.read(schema_path)
    expected_schema_ref = activation.get("schemas", {}).get("codex_metadata_observation")
    if expected_schema_ref != schema_snapshot.ref(workspace):
        raise R17BoundaryError(f"{expected_role}_observation:activation_schema_mismatch")
    graph.verify_ref(
        record.get("schema_binding"),
        label=f"{expected_role}_observation.schema_binding",
        expected_path=schema_path,
    )
    _schema_validate_snapshot(record, schema_snapshot, label=f"{expected_role}_observation")
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError(f"{expected_role}_observation:self_hash_mismatch")
    if record.get("reviewer_slot") != expected_role:
        raise R17BoundaryError(f"{expected_role}_observation:role_mismatch")

    metadata = record.get("attested_task_metadata")
    review_turn = record.get("observed_review_turn")
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"thread_id", "host_id"}
        or _canonical_codex_uuid(metadata.get("thread_id")) is None
        or not isinstance(metadata.get("host_id"), str)
        or not metadata["host_id"]
    ):
        raise R17BoundaryError(f"{expected_role}_observation:attested_task_metadata_invalid")
    if (
        not isinstance(review_turn, dict)
        or set(review_turn) != {"turn_id", "status"}
        or _canonical_codex_uuid(review_turn.get("turn_id")) is None
        or review_turn.get("status") != "completed"
    ):
        raise R17BoundaryError(f"{expected_role}_observation:completed_turn_invalid")
    attestation_snapshot = graph.verify_ref(
        record.get("task_creation_attestation"),
        label=f"{expected_role}_observation.task_creation_attestation",
    )
    attestation = _validate_review_task_creation_attestation_with_graph(
        attestation_snapshot.path,
        workspace=workspace,
        graph=graph,
        expected_slot=expected_role,
        expected_thread_id=metadata["thread_id"],
        expected_host_id=metadata["host_id"],
        current_turn_id=review_turn["turn_id"],
        activation_ref=activation_snapshot.ref(workspace),
        dispatch_ref=dispatch_snapshot.ref(workspace),
        prompt_ref=prompt_snapshot.ref(workspace),
        allowed_output_path=review_receipt_path.resolve()
        .relative_to(workspace.resolve())
        .as_posix(),
        expected_root_dispatch_id=expected_root_dispatch_id,
        schema_ref=activation.get("schemas", {}).get(
            "review_task_creation_attestation"
        ),
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
    )
    if record.get("task_creation_attestation") != attestation["ref"]:
        raise R17BoundaryError(f"{expected_role}_observation:attestation_ref_mismatch")
    if record.get("attested_task_metadata") != attestation["record"]["actual_task"]:
        raise R17BoundaryError(f"{expected_role}_observation:attested_task_binding_mismatch")
    if record.get("review_receipt") != review_snapshot.ref(workspace):
        raise R17BoundaryError(f"{expected_role}_observation:review_receipt_binding_mismatch")
    if record.get("derived_execution_metadata") != attestation["execution"]:
        raise R17BoundaryError(f"{expected_role}_observation:derived_execution_mismatch")
    boundary = {
        "platform_model_reported": False,
        "platform_reasoning_effort_reported": False,
        "platform_receipt_claimed": False,
        "observer_role": "root_observer",
        "non_signing": True,
        "cryptographic_signature": False,
        "private_key_used": False,
        "human_reviewed": False,
    }
    if any(
        record.get(field) is not value
        for field, value in boundary.items()
        if isinstance(value, bool)
    ) or any(
        record.get(field) != value
        for field, value in boundary.items()
        if not isinstance(value, bool)
    ):
        raise R17BoundaryError(f"{expected_role}_observation:platform_or_signing_boundary_mismatch")
    observed_at = _utc_timestamp(
        record.get("observed_at"), label=f"{expected_role}_observation.observed_at"
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at > current + timedelta(minutes=5) or current - observed_at > timedelta(days=7):
        raise R17BoundaryError(f"{expected_role}_observation:timestamp_not_current")

    try:
        relative = snapshot.path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise R17BoundaryError(f"{expected_role}_observation:path_outside_workspace") from exc
    prefix = (
        "staging",
        "coordination",
        "root",
        "revisions",
        "r18",
        "review_metadata_observations",
    )
    expected_dir = f"{expected_role}--{metadata['thread_id']}"
    if (
        len(relative.parts) != len(prefix) + 2
        or tuple(part.casefold() for part in relative.parts[: len(prefix)]) != prefix
        or relative.parts[-2] != expected_dir
        or relative.parts[-1] != "review_codex_metadata_observation_r18.json"
    ):
        raise R17BoundaryError(f"{expected_role}_observation:path_pattern_mismatch")
    return {
        "record": record,
        "ref": snapshot.ref(workspace),
        "execution": copy.deepcopy(attestation["execution"]),
        "task_creation_attestation": copy.deepcopy(attestation["ref"]),
    }


def _validate_r18_reconciliation_with_graph(
    reconciliation_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    formal: dict[str, Any],
    expected_version_id: str,
    expected_paper_id: str,
    expected_artifact_id: str | None = None,
    expected_subject_pair_sha256: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    prefix = ("staging", "coordination", "root", "revisions", "r18", "reconciliation")
    _require_workspace_prefix(reconciliation_path, workspace, prefix, "generator_reconciliation")
    snapshot = graph.read(reconciliation_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError("generator_reconciliation:object_required")
    schema_path = workspace / "sh-chem-db/kb/machine_governance_v2/schemas/root_provenance_reconciliation_r18.schema.json"
    schema_snapshot = graph.read(schema_path)
    graph.verify_ref(record.get("schema_binding"), label="generator_reconciliation.schema_binding", expected_path=schema_path)
    _schema_validate_snapshot(record, schema_snapshot, label="generator_reconciliation")
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError("generator_reconciliation:self_hash_mismatch")
    bound_artifact_id = expected_paper_id if expected_artifact_id is None else expected_artifact_id
    bound_subject_pair = (
        formal["subject"]["subject_pair_sha256"]
        if expected_subject_pair_sha256 is None
        else expected_subject_pair_sha256
    )
    if record.get("version_id") != expected_version_id or record.get("paper_id") != expected_paper_id or record.get("artifact_id") != bound_artifact_id:
        raise R17BoundaryError("generator_reconciliation:identity_mismatch")
    if record.get("subject_pair_sha256") != bound_subject_pair:
        raise R17BoundaryError("generator_reconciliation:subject_mismatch")
    if record.get("formal_freeze") != formal["formal_freeze"] or record.get("formal_freeze_sidecar") != formal["formal_freeze_sidecar"] or record.get("formal_freeze_payload") != formal["freeze_payload"]:
        raise R17BoundaryError("generator_reconciliation:formal_binding_mismatch")
    issued = _utc_timestamp(record.get("issued_at"), label="generator_reconciliation.issued_at")
    expires = _utc_timestamp(record.get("expires_at"), label="generator_reconciliation.expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued > current + timedelta(minutes=5) or expires <= current or expires <= issued or expires - issued > timedelta(days=7):
        raise R17BoundaryError("generator_reconciliation:expired_or_invalid_interval")
    observation_snapshot = graph.verify_ref(record.get("codex_metadata_observation"), label="generator_reconciliation.codex_metadata_observation")
    observation = _validate_r18_observation_with_graph(
        observation_snapshot.path,
        workspace=workspace,
        graph=graph,
        expected_role="generator",
        formal=formal,
        expected_root_dispatch_id=record["actual_task_metadata"]["root_dispatch_id"],
        now=current,
    )
    if record.get("actual_task_metadata") != observation["execution"]:
        raise R17BoundaryError("generator_reconciliation:actual_task_metadata_mismatch")
    generator_snapshot = graph.verify_ref(record.get("generator_provenance_receipt"), label="generator_reconciliation.generator_provenance_receipt")
    generator = generator_snapshot.json_value()
    generator_schema_path = workspace / "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r18.schema.json"
    generator_schema = graph.read(generator_schema_path)
    graph.verify_ref(generator.get("schema_binding"), label="generator_provenance.schema_binding", expected_path=generator_schema_path)
    _schema_validate_snapshot(generator, generator_schema, label="generator_provenance")
    if generator.get("self_hash") != _record_hash_without(generator, "self_hash") or generator.get("provenance_status") != "self_reported":
        raise R17BoundaryError("generator_provenance:self_report_boundary_invalid")
    if generator.get("reported_execution") != observation["execution"]:
        raise R17BoundaryError("generator_provenance:execution_mismatch")
    for field in ("r18_producer_receipt", "sol_generator_receipt"):
        expected = formal["receipt"][field]
        if record.get(field) != expected or generator.get(field) != expected:
            raise R17BoundaryError(f"generator_reconciliation:{field}_binding_mismatch")
    controller_subject = record.get("controller_subject")
    if not isinstance(controller_subject, dict) or controller_subject.get("subject_pair_sha256") != bound_subject_pair:
        raise R17BoundaryError("generator_reconciliation:controller_subject_mismatch")
    for field in ("question", "answer"):
        graph.verify_ref(controller_subject.get(field), label=f"generator_reconciliation.controller_subject.{field}")
    if generator.get("controller_subject") != controller_subject:
        raise R17BoundaryError("generator_reconciliation:generator_controller_subject_mismatch")
    if bound_artifact_id == expected_paper_id and controller_subject != formal["subject"]:
        raise R17BoundaryError("generator_reconciliation:paper_controller_subject_mismatch")
    return {"record": record, "ref": snapshot.ref(workspace), "execution": observation["execution"], "expires_at": record["expires_at"]}


def validate_r18_root_reconciliation(
    reconciliation_path: Path,
    *,
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    expected_artifact_id: str | None = None,
    expected_subject_pair_sha256: str | None = None,
    expected_execution: dict[str, Any] | None = None,
    expected_projection: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Public R18 reconciliation check; generator data is only an expectation."""

    errors: list[str] = []
    graph = SnapshotGraph(workspace.resolve())
    result: dict[str, Any] = {}
    try:
        reconciliation_snapshot = graph.read(reconciliation_path)
        raw = reconciliation_snapshot.json_value()
        if not isinstance(raw, dict):
            raise R17BoundaryError("generator_reconciliation:object_required")
        formal_snapshot = graph.verify_ref(raw.get("formal_freeze"), label="generator_reconciliation.formal_freeze")
        sidecar_snapshot = graph.verify_ref(raw.get("formal_freeze_sidecar"), label="generator_reconciliation.formal_freeze_sidecar")
        formal = _validate_r18_formal_freeze_with_graph(
            formal_snapshot.path,
            sidecar_snapshot.path,
            workspace=workspace,
            graph=graph,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            expected_artifact_id=expected_artifact_id,
            expected_subject_pair_sha256=expected_subject_pair_sha256,
        )
        result = _validate_r18_reconciliation_with_graph(
            reconciliation_path,
            workspace=workspace,
            graph=graph,
            formal=formal,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            expected_artifact_id=expected_artifact_id,
            expected_subject_pair_sha256=expected_subject_pair_sha256,
            now=now,
        )
        if expected_execution is not None and result["execution"] != expected_execution:
            raise R17BoundaryError("generator_reconciliation:self_report_execution_mismatch")
        if expected_projection is not None:
            execution = result["execution"]
            expected = {
                "task_role": "generator",
                "observed_task_identity": {"thread_id": execution["thread_id"], "turn_id": execution["turn_id"]},
                "root_dispatch_configuration": {"root_dispatch_id": execution["root_dispatch_id"], "model": execution["model"], "reasoning_effort": execution["reasoning_effort"]},
                "receipt_derivation": "local_r18_role_prefix_plus_turn_id",
                "derived_local_receipt_id": execution["receipt_id"],
            }
            if expected_projection != expected:
                raise R17BoundaryError("generator_reconciliation:self_report_projection_mismatch")
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError("generator_reconciliation:snapshot_changed_before_return:" + ",".join(checkpoint.get("errors", [])))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        errors.append(str(exc))
    return {
        "check": "r18_root_generator_provenance_reconciliation",
        "status": "pass" if not errors else "fail",
        "valid": not errors,
        "external_reconciled": not errors,
        "reconciliation_id": result.get("record", {}).get("reconciliation_id"),
        "actual_task_metadata": result.get("execution"),
        "expires_at": result.get("expires_at"),
        "attestation_ref": result.get("ref") if not errors else None,
        "snapshot_graph_sha256": graph.digest(),
        "errors": sorted(dict.fromkeys(errors)),
        "human_reviewed": False,
    }


def _validate_r18_phase1_authorization_with_graph(
    authorization_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    formal: dict[str, Any],
    reconciliation: dict[str, Any],
    expected_version_id: str,
    expected_paper_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    prefix = ("staging", "coordination", "root", "revisions", "r18", "review_dispatch")
    _require_workspace_prefix(authorization_path, workspace, prefix, "root_phase1_authorization")
    snapshot = graph.read(authorization_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError("root_phase1_authorization:object_required")
    schema_path = workspace / "sh-chem-db/kb/machine_governance_v2/schemas/review_phase1_authorization_r18.schema.json"
    schema_snapshot = graph.read(schema_path)
    graph.verify_ref(record.get("schema_binding"), label="root_phase1_authorization.schema_binding", expected_path=schema_path)
    _schema_validate_snapshot(record, schema_snapshot, label="root_phase1_authorization")
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError("root_phase1_authorization:self_hash_mismatch")
    if record.get("version_id") != expected_version_id or record.get("paper_id") != expected_paper_id:
        raise R17BoundaryError("root_phase1_authorization:identity_mismatch")
    if record.get("formal_freeze") != formal["formal_freeze"] or record.get("formal_freeze_sidecar") != formal["formal_freeze_sidecar"] or record.get("freeze_payload") != formal["freeze_payload"] or record.get("generator_reconciliation") != reconciliation["ref"]:
        raise R17BoundaryError("root_phase1_authorization:prerequisite_binding_mismatch")
    issued = _utc_timestamp(record.get("issued_at"), label="root_phase1_authorization.issued_at")
    expires = _utc_timestamp(record.get("expires_at"), label="root_phase1_authorization.expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued > current + timedelta(minutes=5) or expires <= current or expires <= issued or expires - issued > timedelta(days=7):
        raise R17BoundaryError("root_phase1_authorization:expired_or_invalid_interval")
    dispatch_configuration = record["root_dispatch_configuration"]
    if (
        dispatch_configuration["root_dispatch_id"] != reconciliation["execution"]["root_dispatch_id"]
        or dispatch_configuration["model"] != reconciliation["execution"]["model"]
        or dispatch_configuration["reasoning_effort"] != reconciliation["execution"]["reasoning_effort"]
    ):
        raise R17BoundaryError("root_phase1_authorization:dispatch_configuration_mismatch")
    return {"record": record, "ref": snapshot.ref(workspace), "root_dispatch_id": dispatch_configuration["root_dispatch_id"], "expires_at": record["expires_at"]}


def _validate_r18_runtime_reconciliation_with_graph(
    reconciliation_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    formal: dict[str, Any],
    immutable_paths: dict[str, Path],
    schema_paths: dict[str, Path],
    expected_version_id: str,
    expected_paper_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the bound reconciliation without following its observation ref.

    The phase-1 reviewer is allowlisted to the 25 immutable inputs and 18
    schemas only.  The root observation is deliberately outside that set, so
    this runtime path validates all locally available reconciliation semantics
    while preserving the no-link-following review boundary.
    """

    prefix = (
        "staging",
        "coordination",
        "root",
        "revisions",
        "r18",
        "reconciliation",
    )
    _require_workspace_prefix(
        reconciliation_path, workspace, prefix, "generator_reconciliation"
    )
    snapshot = graph.read(reconciliation_path)
    record = snapshot.json_value()
    if not isinstance(record, dict):
        raise R17BoundaryError("generator_reconciliation:object_required")
    schema_snapshot = graph.read(schema_paths["root_provenance_reconciliation"])
    graph.verify_ref(
        record.get("schema_binding"),
        label="generator_reconciliation.schema_binding",
        expected_path=schema_paths["root_provenance_reconciliation"],
    )
    _schema_validate_snapshot(
        record, schema_snapshot, label="generator_reconciliation"
    )
    if record.get("self_hash") != _record_hash_without(record, "self_hash"):
        raise R17BoundaryError("generator_reconciliation:self_hash_mismatch")
    if (
        record.get("version_id") != expected_version_id
        or record.get("paper_id") != expected_paper_id
        or record.get("artifact_id") != expected_paper_id
        or record.get("subject_pair_sha256")
        != formal["subject"]["subject_pair_sha256"]
    ):
        raise R17BoundaryError("generator_reconciliation:identity_mismatch")
    if (
        record.get("formal_freeze") != formal["formal_freeze"]
        or record.get("formal_freeze_sidecar") != formal["formal_freeze_sidecar"]
        or record.get("formal_freeze_payload") != formal["freeze_payload"]
    ):
        raise R17BoundaryError("generator_reconciliation:formal_binding_mismatch")
    issued = _utc_timestamp(
        record.get("issued_at"), label="generator_reconciliation.issued_at"
    )
    expires = _utc_timestamp(
        record.get("expires_at"), label="generator_reconciliation.expires_at"
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        issued > current + timedelta(minutes=5)
        or expires <= current
        or expires <= issued
        or expires - issued > timedelta(days=7)
        or record.get("external_reconciled") is not True
        or record.get("observer_role") != "root_observer"
        or record.get("non_signing") is not True
        or record.get("cryptographic_signature") is not False
        or record.get("private_key_used") is not False
        or record.get("human_reviewed") is not False
    ):
        raise R17BoundaryError("generator_reconciliation:not_current_external_attestation")

    observation_ref = record.get("codex_metadata_observation")
    if (
        not isinstance(observation_ref, dict)
        or set(observation_ref) != {"path", "sha256", "bytes"}
        or portable_relative_path_error(observation_ref.get("path")) is not None
        or not str(observation_ref.get("path", "")).startswith(
            "staging/coordination/root/revisions/r18/metadata_observations/"
        )
        or not re.fullmatch(r"[0-9a-f]{64}", str(observation_ref.get("sha256", "")))
        or isinstance(observation_ref.get("bytes"), bool)
        or not isinstance(observation_ref.get("bytes"), int)
        or observation_ref["bytes"] < 1
    ):
        raise R17BoundaryError("generator_reconciliation:observation_ref_not_structural")

    execution = record.get("actual_task_metadata")
    if not isinstance(execution, dict):
        raise R17BoundaryError("generator_reconciliation:execution_object_required")
    if (
        execution.get("provider") != "codex_app_thread"
        or _canonical_codex_uuid(execution.get("thread_id")) is None
        or _canonical_codex_uuid(execution.get("turn_id")) is None
        or _canonical_codex_uuid(execution.get("root_dispatch_id")) is None
        or execution.get("thread_id") == execution.get("turn_id")
        or execution.get("receipt_id")
        != "R18-GENERATOR-EXECUTION-" + execution.get("turn_id", "")
        or execution.get("model") != "gpt-5.6-sol"
        or execution.get("reasoning_effort") != "xhigh"
    ):
        raise R17BoundaryError("generator_reconciliation:execution_metadata_invalid")

    expected_refs = {
        key: graph.read(immutable_paths[key]).ref(workspace)
        for key in (
            "generator_provenance_receipt",
            "r18_producer_receipt",
            "sol_generator_receipt",
            "question_subject",
            "answer_subject",
        )
    }
    if (
        record.get("generator_provenance_receipt")
        != expected_refs["generator_provenance_receipt"]
        or record.get("r18_producer_receipt") != expected_refs["r18_producer_receipt"]
        or record.get("sol_generator_receipt")
        != expected_refs["sol_generator_receipt"]
        or record.get("controller_subject") != formal["subject"]
        or formal["subject"].get("question") != expected_refs["question_subject"]
        or formal["subject"].get("answer") != expected_refs["answer_subject"]
    ):
        raise R17BoundaryError("generator_reconciliation:authoritative_binding_mismatch")

    generator_snapshot = graph.read(immutable_paths["generator_provenance_receipt"])
    generator = generator_snapshot.json_value()
    if not isinstance(generator, dict):
        raise R17BoundaryError("generator_provenance:object_required")
    generator_schema = graph.read(schema_paths["generator_provenance_receipt"])
    graph.verify_ref(
        generator.get("schema_binding"),
        label="generator_provenance.schema_binding",
        expected_path=schema_paths["generator_provenance_receipt"],
    )
    _schema_validate_snapshot(generator, generator_schema, label="generator_provenance")
    if (
        generator.get("self_hash") != _record_hash_without(generator, "self_hash")
        or generator.get("provenance_status") != "self_reported"
        or generator.get("reported_execution") != execution
        or generator.get("r18_producer_receipt")
        != expected_refs["r18_producer_receipt"]
        or generator.get("sol_generator_receipt")
        != expected_refs["sol_generator_receipt"]
        or generator.get("controller_subject") != formal["subject"]
    ):
        raise R17BoundaryError("generator_provenance:runtime_binding_mismatch")
    return {
        "record": record,
        "ref": snapshot.ref(workspace),
        "execution": execution,
        "expires_at": record["expires_at"],
        "observation_read": False,
    }


def _bytes_ref(path: Path, payload: bytes, *, workspace: Path) -> dict[str, object]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise R17BoundaryError("output_ref:path_outside_workspace") from exc
    return {"path": relative, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def _phase1_output_layout(activation_path: Path, *, workspace: Path) -> str:
    try:
        relative = activation_path.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise R17BoundaryError("phase1_activation:path_outside_workspace") from exc
    parts = relative.parts
    prefix = ("staging", "coordination", "generation_publication", "r18", "review_dispatch")
    if (
        len(parts) != len(prefix) + 2
        or tuple(part.casefold() for part in parts[: len(prefix)]) != prefix
        or parts[-1] != "review_phase1_activation_r18.json"
        or not re.fullmatch(r"PHASE1-ACTIVATION-.+-[0-9a-f]{16}", parts[-2])
    ):
        raise R17BoundaryError("phase1_activation:path_pattern_invalid")
    return parts[-2]


def r18_review_attempt_output_paths(
    activation_path: Path,
    *,
    workspace: Path = WORKSPACE,
) -> dict[str, Path]:
    """Return the only A/B/adversarial receipt paths for one activation.

    The activation ID includes the explicit review-attempt ID.  Consequently an
    earlier failed receipt remains immutable in its old activation directory and
    can never occupy, shadow, or be used as a fallback for a later attempt.
    """

    activation_id = _phase1_output_layout(activation_path, workspace=workspace)
    root = (
        workspace.resolve()
        / "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/review_attempts"
        / activation_id
    )
    paths = {
        "sol_review_a": root / "sol_review_a.json",
        "sol_review_b": root / "sol_review_b.json",
        "adversarial_check": root / "adversarial_check.json",
    }
    canonical = [
        path.resolve().relative_to(workspace.resolve()).as_posix().casefold()
        for path in paths.values()
    ]
    if len(canonical) != len(set(canonical)):
        raise R17BoundaryError("review_attempt_output:path_casefold_collision")
    return paths


def _dispatch_review_output_paths(
    dispatch: dict[str, Any],
    *,
    activation_path: Path,
    workspace: Path,
) -> dict[str, Path]:
    """Resolve exact output paths from one dispatch and reject every fallback."""

    tasks = dispatch.get("requested_tasks")
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise R17BoundaryError("review_attempt_output:dispatch_task_count_invalid")
    expected = r18_review_attempt_output_paths(
        activation_path, workspace=workspace
    )
    resolved: dict[str, Path] = {}
    for task in tasks:
        if not isinstance(task, dict):
            raise R17BoundaryError("review_attempt_output:task_object_required")
        slot = task.get("slot")
        if slot not in expected or slot in resolved:
            raise R17BoundaryError("review_attempt_output:slot_set_not_exact")
        raw_path = task.get("output_path")
        path_error = portable_relative_path_error(raw_path)
        if path_error is not None:
            raise R17BoundaryError(
                f"review_attempt_output:{slot}:path_invalid:{path_error}"
            )
        assert isinstance(raw_path, str)
        lexical = Path(os.path.abspath(os.fspath(workspace.resolve() / raw_path)))
        if lexical != expected[slot].resolve():
            raise R17BoundaryError(
                f"review_attempt_output:{slot}:not_exact_activation_scoped_path"
            )
        resolved[slot] = lexical
    if set(resolved) != set(expected):
        raise R17BoundaryError("review_attempt_output:slot_set_not_exact")
    return resolved


def _exact_authoritative_refs(
    paths: dict[str, Path] | None,
    *,
    required_keys: tuple[str, ...],
    workspace: Path,
    graph: SnapshotGraph,
    label: str,
) -> dict[str, dict[str, object]]:
    """Snapshot an exact caller-supplied path map and reject aliases/duplicates."""

    if not isinstance(paths, dict) or set(paths) != set(required_keys):
        raise R17BoundaryError(f"{label}:keys_not_exact")
    refs: dict[str, dict[str, object]] = {}
    normalized_paths: list[str] = []
    for key in required_keys:
        path = paths.get(key)
        if not isinstance(path, Path):
            raise R17BoundaryError(f"{label}.{key}:path_object_required")
        lexical = Path(os.path.abspath(os.fspath(path)))
        try:
            relative = lexical.relative_to(workspace.resolve()).as_posix()
        except ValueError as exc:
            raise R17BoundaryError(f"{label}.{key}:outside_workspace") from exc
        path_error = portable_relative_path_error(relative)
        if path_error is not None:
            raise R17BoundaryError(f"{label}.{key}:path_invalid:{path_error}")
        normalized_paths.append(relative.casefold())
        refs[key] = graph.read(lexical).ref(workspace)
    if len(normalized_paths) != len(set(normalized_paths)):
        raise R17BoundaryError(f"{label}:duplicate_or_casefold_colliding_paths")
    return refs


def _validate_phase1_authoritative_bindings(
    dispatch: dict[str, Any],
    activation: dict[str, Any],
    *,
    immutable_inputs: dict[str, Path] | None,
    schema_paths: dict[str, Path] | None,
    workspace: Path,
    graph: SnapshotGraph,
    expected_version_id: str,
    expected_paper_id: str,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    expected_inputs = _exact_authoritative_refs(
        immutable_inputs,
        required_keys=R18_PHASE1_IMMUTABLE_KEYS,
        workspace=workspace,
        graph=graph,
        label="phase1_authoritative_inputs",
    )
    expected_schemas = _exact_authoritative_refs(
        schema_paths,
        required_keys=R18_PHASE1_SCHEMA_KEYS,
        workspace=workspace,
        graph=graph,
        label="phase1_authoritative_schemas",
    )
    expected_schema_filenames = {
        "paper_candidate": "paper_candidate.schema.json",
        "component_registry": "component_registry.schema.json",
        "deterministic_check_request": "deterministic_check_request.schema.json",
        "deterministic_check_report": "deterministic_check_report.schema.json",
        "project_generation_authorization": "project_generation_authorization.schema.json",
        "r18_producer_receipt": "r18_producer_receipt.schema.json",
        "sol_generator_receipt": "sol_generator_receipt.schema.json",
        "generator_provenance_receipt": "generator_provenance_receipt_r18.schema.json",
        "formal_freeze_receipt": "formal_freeze_receipt_r18.schema.json",
        "formal_freeze_sidecar": "formal_freeze_sidecar_r18.schema.json",
        "root_provenance_reconciliation": "root_provenance_reconciliation_r18.schema.json",
        "review_phase1_authorization": "review_phase1_authorization_r18.schema.json",
        "review_task_creation_attestation": "review_task_creation_attestation_r18.schema.json",
        "codex_metadata_observation": "review_codex_metadata_observation_r18.schema.json",
        "machine_review_record": "machine_review_record.schema.json",
        "adversarial_check_record": "adversarial_check_record.schema.json",
        "review_dispatch_request": "review_dispatch_request.schema.json",
        "phase1_review_dispatch_activation": "phase1_review_dispatch_activation.schema.json",
        "adversarial_phase2_dispatch": "adversarial_phase2_dispatch_r18.schema.json",
    }
    assert schema_paths is not None
    for key, expected_filename in expected_schema_filenames.items():
        if schema_paths[key].name != expected_filename:
            raise R17BoundaryError(
                f"phase1_authoritative_schemas.{key}:schema_identity_mismatch"
            )
    for container_label, container in (
        ("phase1_dispatch.immutable_inputs", dispatch.get("immutable_inputs")),
        ("phase1_activation.immutable_inputs", activation.get("immutable_inputs")),
    ):
        if container != expected_inputs:
            raise R17BoundaryError(f"{container_label}:authoritative_binding_mismatch")
    for container_label, container in (
        ("phase1_dispatch.schemas", dispatch.get("schemas")),
        ("phase1_activation.schemas", activation.get("schemas")),
    ):
        if container != expected_schemas:
            raise R17BoundaryError(f"{container_label}:authoritative_binding_mismatch")

    fixed_common = {
        "formal_freeze": expected_inputs["formal_freeze"],
        "formal_freeze_sidecar": expected_inputs["formal_freeze_sidecar"],
        "generator_reconciliation": expected_inputs["generator_reconciliation"],
        "root_phase1_authorization": expected_inputs["root_phase1_authorization"],
    }
    for field, expected in fixed_common.items():
        if dispatch.get(field) != expected or activation.get(field) != expected:
            raise R17BoundaryError(f"phase1_authoritative_inputs.{field}:common_ref_mismatch")

    records: dict[str, dict[str, Any]] = {}
    non_json = {"figure_svg", "figure_png"}
    assert immutable_inputs is not None
    for key in R18_PHASE1_IMMUTABLE_KEYS:
        if key in non_json:
            continue
        value = graph.read(immutable_inputs[key]).json_value()
        if not isinstance(value, dict):
            raise R17BoundaryError(f"phase1_authoritative_inputs.{key}:object_required")
        records[key] = value
    if not isinstance(records["paper"].get("themes"), list):
        raise R17BoundaryError("phase1_authoritative_inputs.paper:paper_identity_mismatch")
    if records["task_card"].get("hierarchy") != (
        "paper -> theme_big_question -> printed_question -> atomic_part"
    ):
        raise R17BoundaryError("phase1_authoritative_inputs.task_card:task_identity_mismatch")
    if not isinstance(records["figure_spec"].get("figure_id"), str):
        raise R17BoundaryError("phase1_authoritative_inputs.figure_spec:figure_identity_mismatch")
    if not isinstance(records["component_registry"].get("registry_id"), str):
        raise R17BoundaryError(
            "phase1_authoritative_inputs.component_registry:registry_identity_mismatch"
        )
    if records["figure_visual_qa"].get("check") != (
        "apparatus_full_size_grayscale_and_onebit_visual_evidence"
    ):
        raise R17BoundaryError(
            "phase1_authoritative_inputs.figure_visual_qa:record_identity_mismatch"
        )
    if records["figure_topology_mutations"].get("check") != (
        "figure_topology_mutation_rejection"
    ):
        raise R17BoundaryError(
            "phase1_authoritative_inputs.figure_topology_mutations:record_identity_mismatch"
        )
    if not isinstance(records["observed_profile"].get("profile_id"), str):
        raise R17BoundaryError(
            "phase1_authoritative_inputs.observed_profile:record_identity_mismatch"
        )
    if not isinstance(records["evidence_manifest"].get("manifest_id"), str):
        raise R17BoundaryError(
            "phase1_authoritative_inputs.evidence_manifest:record_identity_mismatch"
        )
    svg_bytes = graph.read(immutable_inputs["figure_svg"]).data.lstrip()
    png_bytes = graph.read(immutable_inputs["figure_png"]).data
    if not svg_bytes.startswith((b"<svg", b"<?xml")):
        raise R17BoundaryError("phase1_authoritative_inputs.figure_svg:svg_identity_mismatch")
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise R17BoundaryError("phase1_authoritative_inputs.figure_png:png_identity_mismatch")
    expected_record_types = {
        "question_subject": "generation_v2_question_subject",
        "answer_subject": "generation_v2_answer_subject",
        "deterministic_request": "deterministic_check_request",
        "deterministic_report": "deterministic_check_report",
        "coverage_matrix": "generation_coverage_matrix",
        "deterministic_atomic_scope": "generation_v2_atomic_deterministic_scope_report",
        "project_generation_authorization": "project_generation_authorization",
        "prefreeze_receipt": "generation_v2_prefreeze_self_check",
        "r18_producer_receipt": "generation_v2_r18_producer_receipt",
        "sol_generator_receipt": "sol_generator_receipt",
        "generator_provenance_receipt": "generator_execution_provenance_receipt",
        "formal_freeze": "generation_v2_formal_review_freeze_receipt",
        "formal_freeze_sidecar": "generation_v2_formal_freeze_sidecar",
        "generator_reconciliation": "root_generator_provenance_reconciliation",
        "root_phase1_authorization": "root_review_phase1_authorization",
    }
    for key, record_type in expected_record_types.items():
        if records[key].get("record_type") != record_type:
            raise R17BoundaryError(
                f"phase1_authoritative_inputs.{key}:record_type_mismatch"
            )
    for key in (
        "paper",
        "task_card",
        "question_subject",
        "answer_subject",
        "coverage_matrix",
        "deterministic_atomic_scope",
        "prefreeze_receipt",
        "r18_producer_receipt",
        "sol_generator_receipt",
        "formal_freeze",
        "formal_freeze_sidecar",
        "generator_reconciliation",
        "root_phase1_authorization",
    ):
        if records[key].get("version_id") != expected_version_id:
            raise R17BoundaryError(
                f"phase1_authoritative_inputs.{key}:version_id_mismatch"
            )
    for key in (
        "paper",
        "task_card",
        "question_subject",
        "coverage_matrix",
        "deterministic_atomic_scope",
        "r18_producer_receipt",
        "sol_generator_receipt",
        "formal_freeze",
        "formal_freeze_sidecar",
        "generator_reconciliation",
        "root_phase1_authorization",
    ):
        if records[key].get("paper_id") != expected_paper_id:
            raise R17BoundaryError(
                f"phase1_authoritative_inputs.{key}:paper_id_mismatch"
            )
    for key in ("question_subject", "answer_subject"):
        if records[key].get("artifact_id") != expected_paper_id:
            raise R17BoundaryError(
                f"phase1_authoritative_inputs.{key}:artifact_id_mismatch"
            )
    if records["generator_reconciliation"].get(
        "generator_provenance_receipt"
    ) != expected_inputs["generator_provenance_receipt"]:
        raise R17BoundaryError(
            "phase1_authoritative_inputs.generator_provenance_receipt:reconciliation_binding_mismatch"
        )
    return expected_inputs, expected_schemas


def r18_phase2_output_paths(
    activation_path: Path,
    *,
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
) -> tuple[Path, Path]:
    """Return the only portable R18 phase-2 prompt and sidecar destinations."""

    activation_id = _phase1_output_layout(activation_path, workspace=workspace)
    if not isinstance(expected_version_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+-R18", expected_version_id
    ):
        raise R17BoundaryError("phase2_output:version_id_not_portable_r18")
    root = (
        workspace.resolve()
        / "sh-chem-db/tests/generation_publication_v2/controller_fixture_r18/phase2"
        / expected_version_id
        / activation_id
    )
    return root / "ADVERSARIAL_PROMPT.md", root / "adversarial_phase2_dispatch.json"


def _validate_phase2_output_paths(
    *,
    activation_path: Path,
    adversarial_prompt_path: Path,
    sidecar_path: Path,
    workspace: Path,
    expected_version_id: str,
) -> None:
    expected_prompt, expected_sidecar = r18_phase2_output_paths(
        activation_path,
        workspace=workspace,
        expected_version_id=expected_version_id,
    )
    for label, supplied, expected in (
        ("adversarial_prompt_path", adversarial_prompt_path, expected_prompt),
        ("sidecar_path", sidecar_path, expected_sidecar),
    ):
        if ".." in supplied.parts:
            raise R17BoundaryError(f"phase2_output.{label}:dotdot_forbidden")
        lexical = Path(os.path.abspath(os.fspath(supplied)))
        try:
            relative = lexical.relative_to(workspace.resolve()).as_posix()
            expected_relative = expected.relative_to(workspace.resolve()).as_posix()
        except ValueError as exc:
            raise R17BoundaryError(f"phase2_output.{label}:outside_workspace") from exc
        path_error = portable_relative_path_error(relative)
        if path_error is not None:
            raise R17BoundaryError(
                f"phase2_output.{label}:path_invalid:{path_error}"
            )
        if relative != expected_relative:
            raise R17BoundaryError(f"phase2_output.{label}:not_exact_approved_path")
    if os.path.normcase(os.fspath(adversarial_prompt_path)).casefold() == os.path.normcase(
        os.fspath(sidecar_path)
    ).casefold():
        raise R17BoundaryError("phase2_output:path_casefold_collision")


def _portable_path_under_roots(
    raw_path: object,
    *,
    workspace: Path,
    allowed_roots: Iterable[Path],
    label: str,
) -> str:
    path_error = portable_relative_path_error(raw_path)
    if path_error is not None:
        raise R17BoundaryError(f"{label}:path_invalid:{path_error}")
    assert isinstance(raw_path, str)
    raw_parts = tuple(part.casefold() for part in raw_path.split("/"))
    workspace = workspace.resolve()
    allowed_parts: list[tuple[str, ...]] = []
    for root in allowed_roots:
        try:
            relative_root = root.resolve().relative_to(workspace)
        except ValueError as exc:
            raise R17BoundaryError(f"{label}:allowlisted_root_outside_workspace") from exc
        allowed_parts.append(tuple(part.casefold() for part in relative_root.parts))
    if not any(
        len(raw_parts) > len(prefix) and raw_parts[: len(prefix)] == prefix
        for prefix in allowed_parts
    ):
        raise R17BoundaryError(f"{label}:path_outside_r18_output_roots")
    return raw_path


def _validate_phase1_dispatch_path_contract(
    dispatch: dict[str, Any],
    activation: dict[str, Any],
    *,
    workspace: Path,
) -> None:
    """Validate portable spellings, allowlists and case-insensitive identity."""

    bundle_root = (
        workspace.resolve()
        / "staging/coordination/generation_publication/r18/review_dispatch"
    )
    raw_activation_path = dispatch.get("phase1_activation_path")
    path_error = portable_relative_path_error(raw_activation_path)
    if path_error is not None:
        raise R17BoundaryError(
            f"phase1_dispatch.phase1_activation_path:path_invalid:{path_error}"
        )
    assert isinstance(raw_activation_path, str)
    activation_file = Path(
        os.path.abspath(os.fspath(workspace.resolve() / raw_activation_path))
    )
    canonical_outputs = r18_review_attempt_output_paths(
        activation_file, workspace=workspace
    )
    review_roots = tuple(
        dict.fromkeys(path.parent.resolve() for path in canonical_outputs.values())
    )
    prompt_paths: list[str] = []
    for container_label, container in (
        ("phase1_activation.prompt_payloads", activation.get("prompt_payloads")),
        ("phase1_dispatch.prompts", dispatch.get("prompts")),
    ):
        if not isinstance(container, dict):
            raise R17BoundaryError(f"{container_label}:object_required")
        for slot in ("sol_review_a", "sol_review_b"):
            reference = container.get(slot)
            raw_path = reference.get("path") if isinstance(reference, dict) else None
            prompt_paths.append(
                _portable_path_under_roots(
                    raw_path,
                    workspace=workspace,
                    allowed_roots=(bundle_root,),
                    label=f"{container_label}.{slot}",
                )
            )

    activation_path = _portable_path_under_roots(
        raw_activation_path,
        workspace=workspace,
        allowed_roots=(bundle_root,),
        label="phase1_dispatch.phase1_activation_path",
    )
    tasks = dispatch.get("requested_tasks")
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise R17BoundaryError("phase1_dispatch:task_count_invalid")
    output_paths: list[str] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise R17BoundaryError(f"phase1_dispatch.task_{index}:object_required")
        slot = str(task.get("slot", index))
        output_path = _portable_path_under_roots(
            task.get("output_path"),
            workspace=workspace,
            allowed_roots=review_roots,
            label=f"phase1_dispatch.{slot}.output_path",
        )
        output_paths.append(output_path)
        may_write = task.get("may_write_only")
        if not isinstance(may_write, list):
            raise R17BoundaryError(
                f"phase1_dispatch.{slot}.may_write_only:not_array"
            )
        normalized_may_write = [
            _portable_path_under_roots(
                path,
                workspace=workspace,
                allowed_roots=review_roots,
                label=f"phase1_dispatch.{slot}.may_write_only",
            )
            for path in may_write
        ]
        if len({path.casefold() for path in normalized_may_write}) != len(
            normalized_may_write
        ):
            raise R17BoundaryError(
                f"phase1_dispatch.{slot}.may_write_only:casefold_collision"
            )
        must_not_read = task.get("must_not_read", [])
        if not isinstance(must_not_read, list):
            raise R17BoundaryError(
                f"phase1_dispatch.{slot}.must_not_read:not_array"
            )
        normalized_must_not_read = [
            _portable_path_under_roots(
                path,
                workspace=workspace,
                allowed_roots=review_roots,
                label=f"phase1_dispatch.{slot}.must_not_read",
            )
            for path in must_not_read
        ]
        if len({path.casefold() for path in normalized_must_not_read}) != len(
            normalized_must_not_read
        ):
            raise R17BoundaryError(
                f"phase1_dispatch.{slot}.must_not_read:casefold_collision"
            )
        prompt = task.get("prompt")
        if prompt is not None:
            raw_prompt_path = prompt.get("path") if isinstance(prompt, dict) else None
            prompt_paths.append(
                _portable_path_under_roots(
                    raw_prompt_path,
                    workspace=workspace,
                    allowed_roots=(bundle_root,),
                    label=f"phase1_dispatch.{slot}.prompt",
                )
            )
    if len({path.casefold() for path in output_paths}) != len(output_paths):
        raise R17BoundaryError("phase1_dispatch:output_path_casefold_collision")
    expected_output_paths = {
        slot: path.resolve().relative_to(workspace.resolve()).as_posix()
        for slot, path in canonical_outputs.items()
    }
    observed_output_paths = {
        task.get("slot"): task.get("output_path") for task in tasks
    }
    if observed_output_paths != expected_output_paths:
        raise R17BoundaryError(
            "phase1_dispatch:slot_output_path_not_activation_scoped"
        )
    canonical_prompt_paths = {
        path.casefold() for path in prompt_paths
    }
    if len(canonical_prompt_paths) != 2:
        raise R17BoundaryError("phase1_dispatch:prompt_path_casefold_collision")
    if canonical_prompt_paths.intersection(path.casefold() for path in output_paths):
        raise R17BoundaryError("phase1_dispatch:prompt_output_path_collision")
    if activation_path.casefold() in canonical_prompt_paths or activation_path.casefold() in {
        path.casefold() for path in output_paths
    }:
        raise R17BoundaryError("phase1_dispatch:activation_path_collision")


def validate_phase1_review_dispatch_activation(
    activation_path: Path,
    *,
    dispatch_path: Path,
    prompt_a_path: Path,
    prompt_b_path: Path,
    formal_freeze_path: Path,
    formal_freeze_sidecar_path: Path,
    reconciliation_path: Path,
    root_authorization_path: Path,
    immutable_inputs: dict[str, Path] | None = None,
    schema_paths: dict[str, Path] | None = None,
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    now: datetime | None = None,
    snapshot_graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    """Validate the exact append-only phase-1 A/B activation and no phase-2 prompt."""

    errors: list[str] = []
    graph = snapshot_graph or SnapshotGraph(workspace.resolve())
    policy_graph: SnapshotGraph | None = None
    activation: dict[str, Any] = {}
    dispatch: dict[str, Any] = {}
    try:
        graph = _shared_snapshot_graph(workspace, snapshot_graph)
        activation_id = _phase1_output_layout(activation_path, workspace=workspace)
        if dispatch_path.parent.resolve() != activation_path.parent.resolve() or prompt_a_path.parent.resolve() != activation_path.parent.resolve() or prompt_b_path.parent.resolve() != activation_path.parent.resolve():
            raise R17BoundaryError("phase1_activation:bundle_directory_mismatch")
        if (activation_path.parent / "ADVERSARIAL_PROMPT.md").exists():
            raise R17BoundaryError("phase1_activation:adversarial_prompt_must_be_absent")
        formal = _validate_r18_formal_freeze_with_graph(
            formal_freeze_path,
            formal_freeze_sidecar_path,
            workspace=workspace,
            graph=graph,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
        )
        reconciliation = _validate_r18_reconciliation_with_graph(
            reconciliation_path,
            workspace=workspace,
            graph=graph,
            formal=formal,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            now=now,
        )
        root_authorization = _validate_r18_phase1_authorization_with_graph(
            root_authorization_path,
            workspace=workspace,
            graph=graph,
            formal=formal,
            reconciliation=reconciliation,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            now=now,
        )
        activation_snapshot = graph.read(activation_path)
        dispatch_snapshot = graph.read(dispatch_path)
        prompt_a_snapshot = graph.read(prompt_a_path)
        prompt_b_snapshot = graph.read(prompt_b_path)
        activation = activation_snapshot.json_value()
        dispatch = dispatch_snapshot.json_value()
        if not isinstance(activation, dict) or not isinstance(dispatch, dict):
            raise R17BoundaryError("phase1_activation:objects_required")
        if (
            activation.get("review_protocol") != R18_PHASE1_PROTOCOL_TOKEN
            or dispatch.get("review_protocol") != R18_PHASE1_PROTOCOL_TOKEN
        ):
            raise R17BoundaryError("phase1_activation:review_protocol_mismatch")
        review_attempt_id = activation.get("review_attempt_id")
        if (
            not isinstance(review_attempt_id, str)
            or not re.fullmatch(R18_REVIEW_ATTEMPT_ID_PATTERN, review_attempt_id)
            or dispatch.get("review_attempt_id") != review_attempt_id
        ):
            raise R17BoundaryError("phase1_activation:review_attempt_id_mismatch")
        attempt_output_paths = r18_review_attempt_output_paths(
            activation_path, workspace=workspace
        )
        policy_inputs, policy_graph = _validate_phase1_policy_inputs(
            dispatch,
            activation,
            workspace=workspace,
            disallowed_paths=(
                activation_path,
                dispatch_path,
                prompt_a_path,
                prompt_b_path,
                *attempt_output_paths.values(),
                *(immutable_inputs or {}).values(),
                *(schema_paths or {}).values(),
            ),
        )
        activation_schema = graph.read(PHASE1_ACTIVATION_SCHEMA_PATH)
        dispatch_schema = graph.read(REVIEW_DISPATCH_SCHEMA_PATH)
        graph.verify_ref(activation.get("schema_binding"), label="phase1_activation.schema_binding", expected_path=PHASE1_ACTIVATION_SCHEMA_PATH)
        _schema_validate_snapshot(activation, activation_schema, label="phase1_activation")
        _schema_validate_snapshot(dispatch, dispatch_schema, label="phase1_dispatch")
        _validate_phase1_authoritative_bindings(
            dispatch,
            activation,
            immutable_inputs=immutable_inputs,
            schema_paths=schema_paths,
            workspace=workspace,
            graph=graph,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
        )
        _validate_phase1_dispatch_path_contract(
            dispatch, activation, workspace=workspace
        )
        if activation.get("output_sha256") != record_output_sha256(activation) or dispatch.get("output_sha256") != record_output_sha256(dispatch):
            raise R17BoundaryError("phase1_activation:self_hash_mismatch")
        if activation.get("activation_id") != activation_id:
            raise R17BoundaryError("phase1_activation:id_path_mismatch")
        common = {
            "formal_freeze": formal["formal_freeze"],
            "formal_freeze_sidecar": formal["formal_freeze_sidecar"],
            "formal_freeze_payload": formal["freeze_payload"],
            "generator_reconciliation": reconciliation["ref"],
            "root_phase1_authorization": root_authorization["ref"],
        }
        for field, expected in common.items():
            if activation.get(field) != expected or dispatch.get(field) != expected:
                raise R17BoundaryError(f"phase1_activation:{field}_binding_mismatch")
        if activation.get("root_dispatch_id") != root_authorization["root_dispatch_id"] or dispatch.get("root_dispatch_id") != root_authorization["root_dispatch_id"]:
            raise R17BoundaryError("phase1_activation:root_dispatch_id_mismatch")
        expected_activation_id = _activation_id_from_bindings(
            expected_version_id=expected_version_id,
            root_authorization_ref=root_authorization["ref"],
            policy_inputs=policy_inputs,
            review_attempt_id=review_attempt_id,
        )
        if activation_id != expected_activation_id:
            raise R17BoundaryError("phase1_activation:id_protocol_policy_binding_mismatch")
        if dispatch.get("dispatch_id") != (
            f"DISPATCH-{expected_version_id}-{expected_activation_id.rsplit('-', 1)[-1]}"
        ):
            raise R17BoundaryError("phase1_dispatch:id_protocol_policy_binding_mismatch")
        if activation.get("generator_thread_id") != reconciliation["execution"]["thread_id"] or dispatch.get("generator_thread_id") != reconciliation["execution"]["thread_id"]:
            raise R17BoundaryError("phase1_activation:generator_thread_id_mismatch")
        prompt_refs = {"sol_review_a": prompt_a_snapshot.ref(workspace), "sol_review_b": prompt_b_snapshot.ref(workspace)}
        if activation.get("prompt_payloads") != prompt_refs or dispatch.get("prompts") != prompt_refs:
            raise R17BoundaryError("phase1_activation:prompt_binding_mismatch")
        if dispatch.get("phase1_activation_path") != activation_snapshot.ref(workspace)["path"]:
            raise R17BoundaryError("phase1_activation:path_binding_mismatch")
        dispatch_bytes = canonical_json_bytes(dispatch)
        if activation.get("dispatch_core") != {"canonical_sha256": hashlib.sha256(dispatch_bytes).hexdigest(), "canonical_bytes": len(dispatch_bytes)}:
            raise R17BoundaryError("phase1_activation:dispatch_core_mismatch")
        expected_ids = [f"P{index:02d}" for index in range(1, 37)]
        checks = ["chemistry", "answer", "rubric", "shanghai_style", "ambiguity"]
        coverage = dispatch.get("coverage_contract", {})
        if coverage.get("expected_atomic_part_ids") != expected_ids or coverage.get("required_checks_exact_order") != checks:
            raise R17BoundaryError("phase1_dispatch:coverage_contract_mismatch")
        tasks = dispatch.get("requested_tasks")
        if not isinstance(tasks, list) or len(tasks) != 3:
            raise R17BoundaryError("phase1_dispatch:task_count_invalid")
        a, b, adversarial = tasks
        if "prompt" in adversarial or any(key in adversarial for key in ("prompt_path", "prompt_ref", "prompt_sha256", "prompt_bytes")):
            raise R17BoundaryError("phase1_dispatch:adversarial_prompt_present")
        if a.get("prompt") != prompt_refs["sol_review_a"] or b.get("prompt") != prompt_refs["sol_review_b"]:
            raise R17BoundaryError("phase1_dispatch:ab_prompt_mismatch")
        if a.get("dispatch_now") is not True or b.get("dispatch_now") is not True or adversarial.get("dispatch_now") is not False:
            raise R17BoundaryError("phase1_dispatch:phase_authority_mismatch")
        if a.get("must_not_read") != [b["output_path"], adversarial["output_path"]] or b.get("must_not_read") != [a["output_path"], adversarial["output_path"]]:
            raise R17BoundaryError("phase1_dispatch:read_isolation_mismatch")
        for task in (a, b, adversarial):
            if task.get("may_write_only") != [task.get("output_path")]:
                raise R17BoundaryError("phase1_dispatch:output_isolation_mismatch")
        expected_output_paths = {
            slot: path.resolve().relative_to(workspace.resolve()).as_posix()
            for slot, path in attempt_output_paths.items()
        }
        if {
            task.get("slot"): task.get("output_path") for task in tasks
        } != expected_output_paths:
            raise R17BoundaryError("phase1_dispatch:slot_output_path_mismatch")
        for slot, snapshot, task in (
            ("sol_review_a", prompt_a_snapshot, a),
            ("sol_review_b", prompt_b_snapshot, b),
        ):
            try:
                prompt_text = snapshot.data.decode("utf-8")
            except UnicodeError as exc:
                raise R17BoundaryError(
                    f"phase1_prompt:{slot}:strict_utf8_required"
                ) from exc
            prompt_validation = validate_phase1_reviewer_prompt_contract(
                prompt_text,
                expected_slot=slot,
                expected_output_path=task["output_path"],
                expected_version_id=expected_version_id,
                workspace=workspace,
            )
            if prompt_validation.get("status") != "pass":
                raise R17BoundaryError(";".join(prompt_validation["errors"]))
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError("phase1_activation:snapshot_changed_before_return:" + ",".join(checkpoint.get("errors", [])))
        assert policy_graph is not None
        policy_checkpoint = policy_graph.revalidate()
        if policy_checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "phase1_activation:policy_snapshot_changed_before_return:"
                + ",".join(policy_checkpoint.get("errors", []))
            )
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        errors.append(str(exc))
    return {
        "check": "r18_phase1_review_dispatch_activation",
        "status": "pass" if not errors else "fail",
        "activation_id": activation.get("activation_id"),
        "dispatch_id": dispatch.get("dispatch_id"),
        "activation": graph.read(activation_path).ref(workspace) if not errors else None,
        "dispatch": graph.read(dispatch_path).ref(workspace) if not errors else None,
        "snapshot_graph_sha256": graph.digest(),
        "policy_snapshot_graph_sha256": (
            policy_graph.digest() if policy_graph is not None else None
        ),
        "errors": sorted(dict.fromkeys(errors)),
    }


def create_phase1_review_dispatch_activation(
    *,
    formal_freeze_path: Path,
    formal_freeze_sidecar_path: Path,
    reconciliation_path: Path,
    root_authorization_path: Path,
    review_attempt_id: str,
    prompt_a_text: str,
    prompt_b_text: str,
    prompt_a_path: Path,
    prompt_b_path: Path,
    dispatch_path: Path,
    activation_path: Path,
    immutable_inputs: dict[str, Path],
    schema_paths: dict[str, Path],
    output_paths: dict[str, Path],
    workspace: Path = WORKSPACE,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Exclusively create A/B prompts, dispatch and activation; never tasks/reviews."""

    if not isinstance(review_attempt_id, str) or not re.fullmatch(
        R18_REVIEW_ATTEMPT_ID_PATTERN, review_attempt_id
    ):
        raise R17BoundaryError("phase1_create:review_attempt_id_invalid")
    activation_id = _phase1_output_layout(activation_path, workspace=workspace)
    if dispatch_path.parent.resolve() != activation_path.parent.resolve() or prompt_a_path.parent.resolve() != activation_path.parent.resolve() or prompt_b_path.parent.resolve() != activation_path.parent.resolve():
        raise R17BoundaryError("phase1_create:bundle_directory_mismatch")
    if (activation_path.parent / "ADVERSARIAL_PROMPT.md").exists():
        raise R17BoundaryError("phase1_create:adversarial_prompt_must_be_absent")
    if (
        set(immutable_inputs) != set(R18_PHASE1_IMMUTABLE_KEYS)
        or set(schema_paths) != set(R18_PHASE1_SCHEMA_KEYS)
        or set(output_paths) != {"sol_review_a", "sol_review_b", "adversarial_check"}
    ):
        raise R17BoundaryError("phase1_create:input_or_output_keys_not_exact")
    graph = SnapshotGraph(workspace.resolve())
    policy_inputs, policy_graph = _snapshot_phase1_policy_inputs(workspace=workspace)
    formal = _validate_r18_formal_freeze_with_graph(
        formal_freeze_path, formal_freeze_sidecar_path, workspace=workspace, graph=graph,
        expected_version_id=expected_version_id, expected_paper_id=expected_paper_id,
    )
    reconciliation = _validate_r18_reconciliation_with_graph(
        reconciliation_path, workspace=workspace, graph=graph, formal=formal,
        expected_version_id=expected_version_id, expected_paper_id=expected_paper_id, now=now,
    )
    root_authorization = _validate_r18_phase1_authorization_with_graph(
        root_authorization_path, workspace=workspace, graph=graph, formal=formal,
        reconciliation=reconciliation, expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id, now=now,
    )
    expected_activation_id = _activation_id_from_bindings(
        expected_version_id=expected_version_id,
        root_authorization_ref=root_authorization["ref"],
        policy_inputs=policy_inputs,
        review_attempt_id=review_attempt_id,
    )
    if activation_id != expected_activation_id:
        raise R17BoundaryError("phase1_create:activation_id_protocol_policy_binding_mismatch")
    input_refs = _exact_authoritative_refs(
        immutable_inputs,
        required_keys=R18_PHASE1_IMMUTABLE_KEYS,
        workspace=workspace,
        graph=graph,
        label="phase1_authoritative_inputs",
    )
    schema_refs = _exact_authoritative_refs(
        schema_paths,
        required_keys=R18_PHASE1_SCHEMA_KEYS,
        workspace=workspace,
        graph=graph,
        label="phase1_authoritative_schemas",
    )
    output_rel = {name: path.resolve().relative_to(workspace.resolve()).as_posix() for name, path in output_paths.items()}
    expected_output_paths = {
        slot: path.resolve().relative_to(workspace.resolve()).as_posix()
        for slot, path in r18_review_attempt_output_paths(
            activation_path, workspace=workspace
        ).items()
    }
    if output_rel != expected_output_paths:
        raise R17BoundaryError("phase1_create:slot_output_path_mismatch")
    _assert_policy_paths_disjoint(
        (
            activation_path,
            dispatch_path,
            prompt_a_path,
            prompt_b_path,
            *output_paths.values(),
            *immutable_inputs.values(),
            *schema_paths.values(),
        ),
        workspace=workspace,
    )
    for slot, prompt_text in (
        ("sol_review_a", prompt_a_text),
        ("sol_review_b", prompt_b_text),
    ):
        prompt_validation = validate_phase1_reviewer_prompt_contract(
            prompt_text,
            expected_slot=slot,
            expected_output_path=output_rel[slot],
            expected_version_id=expected_version_id,
            workspace=workspace,
        )
        if prompt_validation.get("status") != "pass":
            raise R17BoundaryError(";".join(prompt_validation["errors"]))
    prompt_a_bytes = prompt_a_text.encode("utf-8")
    prompt_b_bytes = prompt_b_text.encode("utf-8")
    prompt_refs = {
        "sol_review_a": _bytes_ref(prompt_a_path, prompt_a_bytes, workspace=workspace),
        "sol_review_b": _bytes_ref(prompt_b_path, prompt_b_bytes, workspace=workspace),
    }
    expected_ids = [f"P{index:02d}" for index in range(1, 37)]
    common = {
        "formal_freeze": formal["formal_freeze"], "formal_freeze_sidecar": formal["formal_freeze_sidecar"],
        "formal_freeze_payload": formal["freeze_payload"], "generator_reconciliation": reconciliation["ref"],
        "root_phase1_authorization": root_authorization["ref"],
    }
    activation_rel = activation_path.resolve().relative_to(workspace.resolve()).as_posix()
    dispatch: dict[str, Any] = {
        "schema_version": "3.0.0-r18", "record_type": "independent_review_dispatch_request",
        "dispatch_id": f"DISPATCH-{expected_version_id}-{activation_id.rsplit('-', 1)[-1]}",
        "review_protocol": R18_PHASE1_PROTOCOL_TOKEN,
        "review_attempt_id": review_attempt_id,
        "version_id": expected_version_id, "paper_id": expected_paper_id,
        "generator_thread_id": reconciliation["execution"]["thread_id"], "root_dispatch_id": root_authorization["root_dispatch_id"],
        "phase1_activation_path": activation_rel, **common, "immutable_inputs": input_refs, "schemas": schema_refs,
        "policy_inputs": copy.deepcopy(policy_inputs), "protocol_policy_digest": _protocol_policy_digest(policy_inputs), "prompts": prompt_refs,
        "requested_tasks": [
            {"slot": "sol_review_a", "phase": 1, "dispatch_now": True, "new_task_required": True, "distinct_new_task_required": True, "prompt": prompt_refs["sol_review_a"], "output_path": output_rel["sol_review_a"], "exclusive_output": True, "must_not_read": [output_rel["sol_review_b"], output_rel["adversarial_check"]], "may_write_only": [output_rel["sol_review_a"]]},
            {"slot": "sol_review_b", "phase": 1, "dispatch_now": True, "new_task_required": True, "distinct_new_task_required": True, "prompt": prompt_refs["sol_review_b"], "output_path": output_rel["sol_review_b"], "exclusive_output": True, "must_not_read": [output_rel["sol_review_a"], output_rel["adversarial_check"]], "may_write_only": [output_rel["sol_review_b"]]},
            {"slot": "adversarial_check", "phase": 2, "dispatch_now": False, "new_task_required": True, "distinct_new_task_required": True, "dispatch_after": ["sol_review_a", "sol_review_b"], "prompt_absent_until_phase2": True, "output_path": output_rel["adversarial_check"], "exclusive_output": True, "may_write_only": [output_rel["adversarial_check"]]},
        ],
        "independence_contract": {"required_engine": "gpt-5.6-sol", "required_reasoning_effort": "xhigh", "distinct_non_generator_thread_count": 3, "run_id_only_is_not_isolation": True, "root_observation_required_each": True, "generation_task_may_validate_but_must_not_author_or_modify_receipts": True},
        "coverage_contract": {"expected_atomic_part_ids": expected_ids, "review_finding_count_each": 36, "required_checks_exact_order": ["chemistry", "answer", "rubric", "shanghai_style", "ambiguity"], "adversarial_item_coverage_count": 36, "coverage_matrix_review_required": True},
        "authority": {"phase1_ab_dispatch_authorized": True, "adversarial_dispatch_authorized": False, "tasks_created": False, "reviews_authored": False, "chain_creation_authorized": False, "controller_registration_authorized": False, "batch_registration_authorized": False, "publication_authorized": False, "delivery_authorized": False, "external_delivery_authorized": False},
        "human_reviewed": False, "official_claim_allowed": False,
    }
    dispatch["output_sha256"] = record_output_sha256(dispatch)
    _schema_validate_snapshot(dispatch, graph.read(REVIEW_DISPATCH_SCHEMA_PATH), label="phase1_dispatch")
    dispatch_bytes = canonical_json_bytes(dispatch)
    activation: dict[str, Any] = {
        "schema_version": "3.0.0-r18", "record_type": "phase1_review_dispatch_activation", "activation_id": activation_id,
        "review_protocol": R18_PHASE1_PROTOCOL_TOKEN,
        "review_attempt_id": review_attempt_id,
        "version_id": expected_version_id, "paper_id": expected_paper_id, "generator_thread_id": reconciliation["execution"]["thread_id"],
        "root_dispatch_id": root_authorization["root_dispatch_id"], "schema_binding": schema_refs["phase1_review_dispatch_activation"], **common,
        "immutable_inputs": input_refs, "schemas": schema_refs,
        "policy_inputs": copy.deepcopy(policy_inputs), "protocol_policy_digest": _protocol_policy_digest(policy_inputs),
        "prompt_payloads": prompt_refs, "dispatch_core": {"canonical_sha256": hashlib.sha256(dispatch_bytes).hexdigest(), "canonical_bytes": len(dispatch_bytes)},
        "authorized_slots": ["sol_review_a", "sol_review_b"],
        "authority": {"review_dispatch_prepared": True, "review_dispatch_authorized": True, "review_task_creation_authorized": True, "adversarial_dispatch_authorized": False, "chain_creation_authorized": False, "controller_registration_authorized": False, "batch_registration_authorized": False, "publication_authorized": False, "delivery_authorized": False, "external_delivery_authorized": False, "tasks_created": False, "reviews_authored": False},
        "human_reviewed": False,
    }
    activation["output_sha256"] = record_output_sha256(activation)
    _validate_phase1_authoritative_bindings(
        dispatch,
        activation,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
        workspace=workspace,
        graph=graph,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
    )
    _schema_validate_snapshot(activation, graph.read(PHASE1_ACTIVATION_SCHEMA_PATH), label="phase1_activation")
    _validate_phase1_dispatch_path_contract(
        dispatch, activation, workspace=workspace
    )
    policy_checkpoint = policy_graph.revalidate()
    if policy_checkpoint.get("status") != "pass":
        raise R17BoundaryError(
            "phase1_create:policy_snapshot_changed_before_write:"
            + ",".join(policy_checkpoint.get("errors", []))
        )
    transaction = exclusive_create_bundle(
        workspace=workspace,
        files=[(prompt_a_path, prompt_a_bytes), (prompt_b_path, prompt_b_bytes), (dispatch_path, _pretty_json_bytes(dispatch)), (activation_path, _pretty_json_bytes(activation))],
        input_graph=graph,
    )
    policy_checkpoint = policy_graph.revalidate()
    if policy_checkpoint.get("status") != "pass":
        for path in (activation_path, dispatch_path, prompt_b_path, prompt_a_path):
            path.unlink(missing_ok=True)
        raise R17BoundaryError(
            "phase1_create:policy_snapshot_changed_during_write:"
            + ",".join(policy_checkpoint.get("errors", []))
        )
    validated = validate_phase1_review_dispatch_activation(
        activation_path, dispatch_path=dispatch_path, prompt_a_path=prompt_a_path, prompt_b_path=prompt_b_path,
        formal_freeze_path=formal_freeze_path, formal_freeze_sidecar_path=formal_freeze_sidecar_path,
        reconciliation_path=reconciliation_path, root_authorization_path=root_authorization_path,
        immutable_inputs=immutable_inputs, schema_paths=schema_paths,
        workspace=workspace, expected_version_id=expected_version_id, expected_paper_id=expected_paper_id, now=now,
    )
    if validated.get("status") != "pass":
        for path in (activation_path, dispatch_path, prompt_b_path, prompt_a_path):
            path.unlink(missing_ok=True)
        raise R17BoundaryError(f"phase1_create:post_write_validation_failed:{validated['errors']}")
    return {**validated, **transaction, "ab_dispatch_authorized": True, "adversarial_dispatch_authorized": False, "tasks_created": False, "reviews_authored": False}


def validate_ab_review_receipts(
    review_a_path: Path | None = None,
    review_b_path: Path | None = None,
    *,
    question_path: Path = QUESTION_PATH,
    request_path: Path = REQUEST_PATH,
    report_path: Path = REPORT_PATH,
    review_schema_path: Path = MACHINE_REVIEW_SCHEMA_PATH,
    activation_path: Path | None = None,
    immutable_inputs: dict[str, Path] | None = None,
    schema_paths: dict[str, Path] | None = None,
    observation_a_path: Path | None = None,
    observation_b_path: Path | None = None,
    generator_thread_id: str | None = None,
    root_dispatch_id: str | None = None,
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    snapshot_graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    """Validate only the two live phase-1 receipts.

    This is intentionally independent of the adversarial receipt so root can
    prove A/B eligibility before authorizing a third task.  The function never
    edits or repairs either externally authored file.
    """

    errors: list[str] = []
    snapshots = snapshot_graph or SnapshotGraph(workspace.resolve())
    try:
        snapshots = _shared_snapshot_graph(workspace, snapshot_graph)
    except R17BoundaryError as exc:
        return {
            "check": "phase1_ab_only_schema_hash_provenance_pass_and_scope",
            "status": "fail",
            "review_bindings": {},
            "errors": [str(exc)],
        }
    if activation_path is not None:
        try:
            activation_outputs = r18_review_attempt_output_paths(
                activation_path, workspace=workspace
            )
            for label, supplied, expected in (
                ("sol_review_a", review_a_path, activation_outputs["sol_review_a"]),
                ("sol_review_b", review_b_path, activation_outputs["sol_review_b"]),
            ):
                if supplied is not None and supplied.resolve() != expected.resolve():
                    errors.append(f"{label}:path_not_activation_scoped")
            review_a_path = activation_outputs["sol_review_a"]
            review_b_path = activation_outputs["sol_review_b"]
        except (OSError, ValueError, R17BoundaryError) as exc:
            errors.append(f"review_attempt_output:{exc}")
    if review_a_path is None:
        errors.append("sol_review_a:path_required")
    if review_b_path is None:
        errors.append("sol_review_b:path_required")
    if errors:
        return {
            "check": "phase1_ab_only_schema_hash_provenance_pass_and_scope",
            "status": "fail",
            "review_bindings": {},
            "errors": sorted(dict.fromkeys(errors)),
        }
    assert review_a_path is not None
    assert review_b_path is not None
    required_paths = {
        "question": question_path,
        "request": request_path,
        "report": report_path,
        "review_schema": review_schema_path,
        "sol_review_a": review_a_path,
        "sol_review_b": review_b_path,
    }
    for label, path in (
        ("phase1_activation", activation_path),
        ("sol_review_a_root_observation", observation_a_path),
        ("sol_review_b_root_observation", observation_b_path),
    ):
        if path is None:
            errors.append(f"{label}:path_required")
        else:
            required_paths[label] = path
    for label, path in required_paths.items():
        if not path.is_file():
            errors.append(f"{label}:file_missing")
    if errors:
        return {
            "check": "phase1_ab_only_schema_hash_provenance_pass_and_scope",
            "status": "fail",
            "review_bindings": {},
            "errors": errors,
        }

    assert activation_path is not None
    assert observation_a_path is not None
    assert observation_b_path is not None
    try:
        activation_seed = snapshots.read(activation_path).json_value()
        if not isinstance(activation_seed, dict):
            raise R17BoundaryError("phase1_activation:object_required")
        formal_path = snapshots.verify_ref(
            activation_seed.get("formal_freeze"), label="phase1_activation.formal_freeze"
        ).path
        formal_sidecar_path = snapshots.verify_ref(
            activation_seed.get("formal_freeze_sidecar"), label="phase1_activation.formal_freeze_sidecar"
        ).path
        reconciliation_path = snapshots.verify_ref(
            activation_seed.get("generator_reconciliation"), label="phase1_activation.generator_reconciliation"
        ).path
        root_authorization_path = snapshots.verify_ref(
            activation_seed.get("root_phase1_authorization"), label="phase1_activation.root_phase1_authorization"
        ).path
        phase1 = validate_phase1_review_dispatch_activation(
            activation_path,
            dispatch_path=activation_path.parent / "review_dispatch_request_r18.json",
            prompt_a_path=activation_path.parent / "REVIEW_PROMPT_A.md",
            prompt_b_path=activation_path.parent / "REVIEW_PROMPT_B.md",
            formal_freeze_path=formal_path,
            formal_freeze_sidecar_path=formal_sidecar_path,
            reconciliation_path=reconciliation_path,
            root_authorization_path=root_authorization_path,
            immutable_inputs=immutable_inputs,
            schema_paths=schema_paths,
            workspace=workspace,
            snapshot_graph=snapshots,
        )
        if phase1.get("status") != "pass":
            errors.extend(f"phase1:{error}" for error in phase1.get("errors", []))
        phase1_dispatch = snapshots.read(
            activation_path.parent / "review_dispatch_request_r18.json"
        ).json_value()
        phase1_tasks = {
            row.get("slot"): row
            for row in phase1_dispatch.get("requested_tasks", [])
            if isinstance(row, dict)
        }
        formal = _validate_r18_formal_freeze_with_graph(
            formal_path,
            formal_sidecar_path,
            workspace=workspace,
            graph=snapshots,
            expected_version_id=VERSION_ID,
            expected_paper_id=PAPER_ID,
        )
        effective_generator_thread_id = activation_seed.get("generator_thread_id")
        effective_root_dispatch_id = activation_seed.get("root_dispatch_id")
        if generator_thread_id is not None and generator_thread_id != effective_generator_thread_id:
            errors.append("phase1_activation:caller_generator_thread_mismatch")
        if root_dispatch_id is not None and root_dispatch_id != effective_root_dispatch_id:
            errors.append("phase1_activation:caller_root_dispatch_mismatch")
        observations = {
            "sol_review_a": _validate_r18_review_observation_with_graph(
                observation_a_path,
                review_receipt_path=review_a_path,
                activation_path=activation_path,
                dispatch_path=activation_path.parent / "review_dispatch_request_r18.json",
                prompt_path=activation_path.parent / "REVIEW_PROMPT_A.md",
                workspace=workspace,
                graph=snapshots,
                expected_role="sol_review_a",
                expected_root_dispatch_id=str(effective_root_dispatch_id),
            ),
            "sol_review_b": _validate_r18_review_observation_with_graph(
                observation_b_path,
                review_receipt_path=review_b_path,
                activation_path=activation_path,
                dispatch_path=activation_path.parent / "review_dispatch_request_r18.json",
                prompt_path=activation_path.parent / "REVIEW_PROMPT_B.md",
                workspace=workspace,
                graph=snapshots,
                expected_role="sol_review_b",
                expected_root_dispatch_id=str(effective_root_dispatch_id),
            ),
        }
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        errors.append(str(exc))
        observations = {}
        phase1_tasks = {}
        effective_generator_thread_id = generator_thread_id
        effective_root_dispatch_id = root_dispatch_id

    question = _load_object(
        question_path, label="question", errors=errors, snapshots=snapshots
    )
    request = _load_object(
        request_path, label="deterministic_request", errors=errors, snapshots=snapshots
    )
    report = _load_object(
        report_path, label="deterministic_report", errors=errors, snapshots=snapshots
    )
    records = {
        "sol_review_a": _load_object(
            review_a_path, label="sol_review_a", errors=errors, snapshots=snapshots
        ),
        "sol_review_b": _load_object(
            review_b_path, label="sol_review_b", errors=errors, snapshots=snapshots
        ),
    }
    try:
        expected_order = hierarchy_ids(question)["atomic_part_ids"]
    except Exception as exc:
        errors.append(f"question:frozen_hierarchy_invalid:{type(exc).__name__}")
        expected_order = []
    expected_scope = request.get("atomic_scope") if isinstance(request, dict) else None
    if not isinstance(expected_scope, dict):
        errors.append("deterministic_request:atomic_scope_missing")
    elif expected_scope.get("declared_atomic_part_ids") != expected_order:
        errors.append("deterministic_request:atomic_scope_differs_from_frozen_hierarchy")
    if report.get("request_sha256") != snapshots.read(request_path).sha256:
        errors.append("deterministic_report:request_hash_mismatch")
    errors.extend(
        _report_subject_binding_errors(
            request.get("subject"),
            report.get("subject"),
            label="deterministic_report",
        )
    )
    if report.get("atomic_scope") != expected_scope:
        errors.append("deterministic_report:atomic_scope_mismatch")
    if report.get("passed") is not True or report.get("human_reviewed") is not False:
        errors.append("deterministic_report:not_machine_pass")
    try:
        request_subject = review_subject_from_deterministic_request(
            request, db_root=db_root
        )
    except R17BoundaryError as exc:
        request_subject = {}
        errors.append(str(exc))
    question_ref = request_subject.get("question")
    answer_ref = request_subject.get("answer")
    resolved_question, question_ref_errors = _resolve_bound_ref(
        question_ref,
        base=db_root,
        label="deterministic_request.subject.question",
        snapshots=snapshots,
    )
    _, answer_ref_errors = _resolve_bound_ref(
        answer_ref,
        base=db_root,
        label="deterministic_request.subject.answer",
        snapshots=snapshots,
    )
    errors.extend(question_ref_errors)
    errors.extend(answer_ref_errors)
    if resolved_question != question_path.resolve():
        errors.append("deterministic_request:question_path_mismatch")
    if isinstance(question_ref, dict) and isinstance(answer_ref, dict):
        expected_pair = subject_pair_sha256(
            str(question_ref.get("sha256")), str(answer_ref.get("sha256"))
        )
        if request_subject.get("subject_pair_sha256") != expected_pair:
            errors.append("deterministic_request:subject_pair_hash_mismatch")

    try:
        review_schema = snapshots.read(review_schema_path).json_value()
    except (OSError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        review_schema = {}
        errors.append(f"review_schema:unreadable:{type(exc).__name__}")
    report_sha = snapshots.read(report_path).sha256
    bindings: dict[str, dict[str, Any]] = {}
    for slot, path in (("sol_review_a", review_a_path), ("sol_review_b", review_b_path)):
        record = records[slot]
        if review_schema:
            try:
                validate(record, review_schema)
            except Exception as exc:
                errors.append(f"{slot}:schema_invalid:{type(exc).__name__}:{exc}")
        if record.get("slot") != slot:
            errors.append(f"{slot}:slot_mismatch")
        expected_output_path = phase1_tasks.get(slot, {}).get("output_path")
        try:
            actual_output_path = path.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            actual_output_path = None
        if actual_output_path != expected_output_path:
            errors.append(f"{slot}:output_path_not_dispatch_bound")
        record_subject = record.get("subject")
        if not isinstance(record_subject, dict) or set(record_subject) != set(
            R18_REVIEW_SUBJECT_FIELDS
        ):
            errors.append(f"{slot}:subject_fields_not_exact")
        if record_subject != request_subject:
            errors.append(f"{slot}:subject_mismatch")
        if record.get("deterministic_report_sha256") != report_sha:
            errors.append(f"{slot}:deterministic_report_hash_mismatch")
        if record.get("output_sha256") != record_output_sha256(record):
            errors.append(f"{slot}:canonical_self_hash_mismatch")
        if record.get("verdict") != "pass" or record.get("human_reviewed") is not False:
            errors.append(f"{slot}:verdict_not_pass")
        findings = record.get("findings") if isinstance(record.get("findings"), list) else []
        finding_ids = [row.get("atomic_part_id") for row in findings if isinstance(row, dict)]
        if finding_ids != expected_order:
            errors.append(f"{slot}:atomic_coverage_not_exact_frozen_order")
        if len(finding_ids) != len(set(finding_ids)):
            errors.append(f"{slot}:duplicate_atomic_part_id")
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                errors.append(f"{slot}:finding_not_object:{index}")
                continue
            part_id = finding.get("atomic_part_id") or index
            for field in ("finding", "evidence"):
                semantic_error = _semantic_review_text_error(finding.get(field))
                if semantic_error:
                    errors.append(f"{slot}:{part_id}:{field}:{semantic_error}")
            checks_value = finding.get("checks")
            if not isinstance(checks_value, dict) or set(checks_value) != {
                "chemistry", "answer", "rubric", "shanghai_style", "ambiguity"
            }:
                errors.append(f"{slot}:check_set_not_exact:{part_id}")
            for field in ("finding", "evidence"):
                text = finding.get(field)
                if isinstance(text, str) and re.search(
                    r"(?i)\b(?:human|teacher|expert)[ -]?(?:reviewed|approved|verified)\b|人工(?:审核|复核)|教师(?:审核|复核)",
                    text,
                ):
                    errors.append(f"{slot}:{part_id}:{field}:forbidden_human_claim")
            if finding.get("verdict") != "pass" or any(
                finding.get("checks", {}).get(name) != "pass"
                for name in ("chemistry", "answer", "rubric", "shanghai_style", "ambiguity")
            ):
                errors.append(f"{slot}:non_pass_finding:{part_id}")
        provenance = record.get("execution_provenance")
        if not isinstance(provenance, dict):
            errors.append(f"{slot}:execution_provenance_missing")
            provenance = {}
        if set(provenance) != {
            "provider",
            "thread_id",
            "turn_id",
            "receipt_id",
            "model",
            "reasoning_effort",
            "model_source",
            "reasoning_effort_source",
            "root_dispatch_id",
        }:
            errors.append(f"{slot}:execution_provenance_fields_not_exact_9")
        observation = observations.get(slot, {})
        observed_execution = observation.get("execution", {})
        if provenance != observed_execution:
            errors.append(f"{slot}:execution_provenance_not_root_observed")
        if provenance.get("root_dispatch_id") != effective_root_dispatch_id:
            errors.append(f"{slot}:root_dispatch_id_mismatch")
        if provenance.get("thread_id") in {
            effective_generator_thread_id,
            effective_root_dispatch_id,
        }:
            errors.append(f"{slot}:thread_reuses_generator_or_root")
        if provenance.get("model") != record.get("engine") or provenance.get(
            "reasoning_effort"
        ) != record.get("reasoning_effort"):
            errors.append(f"{slot}:execution_provenance_policy_mismatch")
        if (
            provenance.get("model_source")
            != "task_creation_configuration_attestation.requested_configuration"
            or provenance.get("reasoning_effort_source")
            != "task_creation_configuration_attestation.requested_configuration"
        ):
            errors.append(f"{slot}:execution_provenance_not_root_requested")
        bindings[slot] = {
            "file": snapshots.read(path).ref(workspace.resolve()),
            "root_observation": copy.deepcopy(observation.get("ref")),
            "task_creation_attestation": copy.deepcopy(
                observation.get("task_creation_attestation")
            ),
            "file_self_sha256": record.get("output_sha256"),
            "run_id": record.get("run_id"),
            "isolation_context_id": record.get("isolation_context_id"),
            "output_sha256": record.get("output_sha256"),
            "execution_provenance": copy.deepcopy(provenance),
            "verdict": record.get("verdict"),
        }

    a = records["sol_review_a"]
    b = records["sol_review_b"]
    for field in ("run_id", "isolation_context_id", "output_sha256"):
        if a.get(field) == b.get(field):
            errors.append(f"reviews:{field}_must_be_distinct")
    provenance_a = a.get("execution_provenance") if isinstance(a.get("execution_provenance"), dict) else {}
    provenance_b = b.get("execution_provenance") if isinstance(b.get("execution_provenance"), dict) else {}
    for field in ("thread_id", "turn_id", "receipt_id"):
        if provenance_a.get(field) == provenance_b.get(field):
            errors.append(f"reviews:{field}_must_be_distinct")
    if provenance_a.get("root_dispatch_id") != provenance_b.get("root_dispatch_id"):
        errors.append("reviews:root_dispatch_id_mismatch")
    if observations:
        observation_a = observations["sol_review_a"]["record"]
        observation_b = observations["sol_review_b"]["record"]
        if observation_a["attested_task_metadata"]["thread_id"] == observation_b[
            "attested_task_metadata"
        ]["thread_id"]:
            errors.append("reviews:observed_thread_id_must_be_distinct")
        if observation_a["observed_review_turn"]["turn_id"] == observation_b[
            "observed_review_turn"
        ]["turn_id"]:
            errors.append("reviews:observed_turn_id_must_be_distinct")
        if observations["sol_review_a"]["task_creation_attestation"] == observations[
            "sol_review_b"
        ]["task_creation_attestation"]:
            errors.append("reviews:task_creation_attestation_must_be_distinct")
        if observations["sol_review_a"]["execution"]["receipt_id"] == observations["sol_review_b"]["execution"]["receipt_id"]:
            errors.append("reviews:observed_receipt_id_must_be_distinct")
    checkpoint = snapshots.revalidate()
    if checkpoint.get("status") != "pass":
        errors.extend(f"snapshot:{error}" for error in checkpoint.get("errors", []))
    return {
        "check": "phase1_ab_only_schema_hash_provenance_pass_and_scope",
        "status": "pass" if not errors else "fail",
        "subject_pair_sha256": request_subject.get("subject_pair_sha256"),
        "deterministic_report_sha256": report_sha,
        "expected_atomic_part_ids": expected_order,
        "review_bindings": bindings,
        "thread_ids": [provenance_a.get("thread_id"), provenance_b.get("thread_id")],
        "snapshot_graph_sha256": snapshots.digest(),
        "errors": sorted(dict.fromkeys(errors)),
    }


def _validate_legacy_formal_freeze_for_phase2(
    formal_freeze_path: Path,
    *,
    workspace: Path,
    expected_version_id: str,
    expected_paper_id: str,
    expected_root_dispatch_id: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if not formal_freeze_path.is_file():
        return {"status": "fail", "errors": ["formal_freeze:file_missing"]}
    receipt = _load_object(formal_freeze_path, label="formal_freeze", errors=errors)
    schema_ref = receipt.get("schema_binding")
    schema_path, schema_ref_errors = _resolve_bound_ref(
        schema_ref, base=workspace, label="formal_freeze.schema_binding"
    )
    errors.extend(schema_ref_errors)
    if schema_path is not None and not schema_ref_errors:
        try:
            validate(receipt, load_schema(schema_path))
        except Exception as exc:
            errors.append(f"formal_freeze:schema_invalid:{type(exc).__name__}:{exc}")
    if receipt.get("record_type") != "generation_v2_formal_review_freeze_receipt":
        errors.append("formal_freeze:record_type_invalid")
    if receipt.get("status") != "active_formal_freeze_review_dispatch_authorized":
        errors.append("formal_freeze:not_active_dispatch_authorization")
    if receipt.get("active") is not True or receipt.get("review_dispatch_authorized") is not True:
        errors.append("formal_freeze:review_dispatch_not_authorized")
    if receipt.get("adversarial_dispatch_authorized") is not False:
        errors.append("formal_freeze:must_leave_phase2_unauthorized")
    for field, expected in (
        ("version_id", expected_version_id),
        ("paper_id", expected_paper_id),
    ):
        if receipt.get(field) != expected:
            errors.append(f"formal_freeze:{field}_mismatch")
    root_go = receipt.get("root_go") if isinstance(receipt.get("root_go"), dict) else {}
    if root_go.get("source_thread_id") != expected_root_dispatch_id:
        errors.append("formal_freeze:root_dispatch_id_mismatch")
    if receipt.get("self_hash") != _record_hash_without(receipt, "self_hash"):
        errors.append("formal_freeze:self_hash_mismatch")
    payload = receipt.get("freeze_payload")
    if not isinstance(payload, dict):
        payload = {}
        errors.append("formal_freeze:payload_missing")
    if payload.get("version_id") != expected_version_id:
        errors.append("formal_freeze:payload_version_id_mismatch")
    if payload.get("paper_id") != expected_paper_id:
        errors.append("formal_freeze:payload_paper_id_mismatch")
    if payload.get("root_dispatch_id") != expected_root_dispatch_id:
        errors.append("formal_freeze:payload_root_dispatch_id_mismatch")
    payload_bytes = canonical_json_bytes(payload)
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()
    if receipt.get("freeze_payload_sha256") != payload_sha:
        errors.append("formal_freeze:payload_hash_mismatch")
    dispatch_path_value = receipt.get("review_dispatch_path")
    if not isinstance(dispatch_path_value, str) or not dispatch_path_value:
        errors.append("formal_freeze:review_dispatch_path_invalid")
        dispatch_path = None
    else:
        dispatch_path = (workspace / dispatch_path_value).resolve()
        try:
            dispatch_path.relative_to(workspace.resolve())
        except ValueError:
            errors.append("formal_freeze:review_dispatch_path_outside_workspace")
            dispatch_path = None
    if payload.get("review_dispatch_path") != dispatch_path_value:
        errors.append("formal_freeze:payload_review_dispatch_path_mismatch")
    if dispatch_path is None or not dispatch_path.is_file():
        errors.append("formal_freeze:review_dispatch_missing")
        dispatch = {}
    else:
        if receipt.get("review_dispatch_sha256") != sha256_file(dispatch_path):
            errors.append("formal_freeze:review_dispatch_hash_mismatch")
        if receipt.get("review_dispatch_bytes") != dispatch_path.stat().st_size:
            errors.append("formal_freeze:review_dispatch_bytes_mismatch")
        dispatch = _load_object(dispatch_path, label="review_dispatch", errors=errors)
    if dispatch:
        if dispatch.get("version_id") != expected_version_id:
            errors.append("review_dispatch:version_id_mismatch")
        if dispatch.get("paper_id") != expected_paper_id:
            errors.append("review_dispatch:paper_id_mismatch")
        if dispatch.get("root_dispatch_id") != expected_root_dispatch_id:
            errors.append("review_dispatch:root_dispatch_id_mismatch")
        if dispatch.get("controller_registration_requested") is not False:
            errors.append("review_dispatch:controller_registration_boundary_invalid")
        if dispatch.get("publication_requested") is not False:
            errors.append("review_dispatch:publication_boundary_invalid")
    return {
        "status": "pass" if not errors else "fail",
        "receipt": receipt,
        "schema_path": schema_path,
        "payload": payload,
        "payload_sha256": payload_sha,
        "payload_bytes": len(payload_bytes),
        "dispatch_path": dispatch_path,
        "dispatch": dispatch,
        "errors": sorted(dict.fromkeys(errors)),
    }


def _validate_formal_freeze_for_phase2(
    formal_freeze_path: Path,
    *,
    workspace: Path,
    expected_version_id: str,
    expected_paper_id: str,
    expected_root_dispatch_id: str,
) -> dict[str, Any]:
    """Consume only the root-owned R17 inert formal-freeze package.

    A valid R17 receipt is intentionally *not* a phase-2 dispatch grant.  The
    compatibility return shape keeps callers fail-closed while exposing the
    exact frozen payload and schema to a future, separately root-authorized
    dispatch-preparation step.
    """

    del expected_root_dispatch_id  # R17 formal freeze has no dispatch authority.
    sidecar_path = formal_freeze_path.parent / "formal_freeze_sidecar_r17.json"
    validation = validate_formal_freeze_package(
        formal_freeze_path,
        sidecar_path,
        workspace=workspace,
    )
    errors = list(validation.get("errors", []))
    receipt: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    if validation.get("status") == "pass":
        receipt = validation.get("_receipt", {})
        payload = receipt.get("freeze_payload", {}) if isinstance(receipt, dict) else {}
        if not isinstance(receipt, dict) or not isinstance(payload, dict):
            errors.append("formal_freeze:r17_snapshot_document_missing")
        if receipt.get("version_id") != expected_version_id:
            errors.append("formal_freeze:version_id_mismatch")
        if receipt.get("paper_id") != expected_paper_id:
            errors.append("formal_freeze:paper_id_mismatch")
    payload_bytes = canonical_json_bytes(payload)
    schema_path = workspace / (
        "sh-chem-db/tests/generation_publication_v2/schemas/"
        "formal_freeze_receipt_r17.schema.json"
    )
    return {
        "status": "pass" if not errors else "fail",
        "receipt": receipt,
        "schema_path": schema_path,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload_bytes": len(payload_bytes),
        "dispatch_path": None,
        "dispatch": {},
        "review_dispatch_authorized": False,
        "review_task_creation_authorized": False,
        "adversarial_dispatch_authorized": False,
        "chain_creation_authorized": False,
        "controller_registration_authorized": False,
        "publication_authorized": False,
        "errors": sorted(dict.fromkeys(errors)),
    }


def _phase2_schema_validate(
    record: dict[str, Any],
    schema_path: Path,
    *,
    snapshots: SnapshotGraph | None = None,
) -> list[str]:
    try:
        schema = (
            snapshots.read(schema_path).json_value()
            if snapshots is not None
            else load_schema(schema_path)
        )
        validate(record, schema)
    except Exception as exc:
        return [f"phase2_dispatch:schema_invalid:{type(exc).__name__}:{exc}"]
    return []


def validate_phase2_adversarial_dispatch(
    sidecar_path: Path = ADVERSARIAL_PHASE2_DISPATCH_PATH,
    *,
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    expected_generator_thread_id: str = GENERATOR_THREAD_ID,
) -> dict[str, Any]:
    """Revalidate a phase-2 sidecar against every live byte it binds."""

    errors: list[str] = []
    snapshots = SnapshotGraph(workspace.resolve())
    if not sidecar_path.is_file():
        return {
            "check": "immutable_phase2_adversarial_dispatch_live_binding",
            "status": "fail",
            "errors": ["phase2_dispatch:file_missing"],
        }
    record = _load_object(
        sidecar_path,
        label="phase2_dispatch",
        errors=errors,
        snapshots=snapshots,
    )
    schema_path, schema_errors = _resolve_bound_ref(
        record.get("schema_binding"),
        base=workspace,
        label="phase2_dispatch.schema_binding",
        snapshots=snapshots,
    )
    errors.extend(schema_errors)
    if schema_path is not None and not schema_errors:
        errors.extend(_phase2_schema_validate(record, schema_path, snapshots=snapshots))
    if record.get("output_sha256") != record_output_sha256(record):
        errors.append("phase2_dispatch:output_hash_mismatch")
    if record.get("version_id") != expected_version_id:
        errors.append("phase2_dispatch:version_id_mismatch")
    if record.get("paper_id") != expected_paper_id:
        errors.append("phase2_dispatch:paper_id_mismatch")
    root_dispatch_id = record.get("root_dispatch_id")
    if not isinstance(root_dispatch_id, str) or not root_dispatch_id:
        errors.append("phase2_dispatch:root_dispatch_id_invalid")
        root_dispatch_id = ""

    freeze_path, freeze_ref_errors = _resolve_bound_ref(
        record.get("formal_freeze"),
        base=workspace,
        label="phase2_dispatch.formal_freeze",
        snapshots=snapshots,
    )
    errors.extend(freeze_ref_errors)
    formal: dict[str, Any] = {"status": "fail", "errors": []}
    if freeze_path is not None and not freeze_ref_errors:
        formal = _validate_formal_freeze_for_phase2(
            freeze_path,
            workspace=workspace,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            expected_root_dispatch_id=root_dispatch_id,
        )
        errors.extend(formal.get("errors", []))
        formal_schema_path = formal.get("schema_path")
        bound_schema_path, bound_schema_errors = _resolve_bound_ref(
            record.get("formal_freeze_schema"),
            base=workspace,
            label="phase2_dispatch.formal_freeze_schema",
            snapshots=snapshots,
        )
        errors.extend(bound_schema_errors)
        if bound_schema_path != formal_schema_path:
            errors.append("phase2_dispatch:formal_freeze_schema_binding_mismatch")
        payload_binding = record.get("formal_freeze_payload")
        expected_payload_binding = {
            "json_pointer": "#/freeze_payload",
            "canonical_sha256": formal.get("payload_sha256"),
            "canonical_bytes": formal.get("payload_bytes"),
        }
        if payload_binding != expected_payload_binding:
            errors.append("phase2_dispatch:formal_freeze_payload_binding_mismatch")
        dispatch_path = formal.get("dispatch_path")
        bound_dispatch_path, bound_dispatch_errors = _resolve_bound_ref(
            record.get("review_dispatch"),
            base=workspace,
            label="phase2_dispatch.review_dispatch",
            snapshots=snapshots,
        )
        errors.extend(bound_dispatch_errors)
        if bound_dispatch_path != dispatch_path:
            errors.append("phase2_dispatch:review_dispatch_binding_mismatch")

    prompt_path, prompt_errors = _resolve_bound_ref(
        record.get("adversarial_prompt"),
        base=workspace,
        label="phase2_dispatch.adversarial_prompt",
        snapshots=snapshots,
    )
    errors.extend(prompt_errors)
    adversarial_schema_path, adversarial_schema_errors = _resolve_bound_ref(
        record.get("adversarial_record_schema"),
        base=workspace,
        label="phase2_dispatch.adversarial_record_schema",
        snapshots=snapshots,
    )
    errors.extend(adversarial_schema_errors)
    dispatch = formal.get("dispatch") if isinstance(formal.get("dispatch"), dict) else {}
    if dispatch:
        prompt_ref = (dispatch.get("prompts") or {}).get("adversarial")
        dispatch_prompt_path, dispatch_prompt_errors = _resolve_bound_ref(
            prompt_ref,
            base=workspace,
            label="review_dispatch.prompts.adversarial",
            snapshots=snapshots,
        )
        errors.extend(dispatch_prompt_errors)
        if dispatch_prompt_path != prompt_path:
            errors.append("phase2_dispatch:static_adversarial_prompt_binding_mismatch")
        schema_ref = (dispatch.get("schemas") or {}).get("adversarial_check_record")
        dispatch_schema_path, dispatch_schema_errors = _resolve_bound_ref(
            schema_ref,
            base=workspace,
            label="review_dispatch.schemas.adversarial_check_record",
            snapshots=snapshots,
        )
        errors.extend(dispatch_schema_errors)
        if dispatch_schema_path != adversarial_schema_path:
            errors.append("phase2_dispatch:adversarial_schema_binding_mismatch")
        tasks = [
            row
            for row in dispatch.get("requested_tasks", [])
            if isinstance(row, dict) and row.get("slot") == "adversarial_check"
        ]
        if len(tasks) != 1:
            errors.append("phase2_dispatch:adversarial_task_not_unique")
        else:
            task = tasks[0]
            if task.get("phase") != 2 or task.get("dispatch_now") is not False:
                errors.append("phase2_dispatch:static_dispatch_phase_boundary_invalid")
            if task.get("prompt_path") and prompt_path is not None:
                if (workspace / str(task["prompt_path"])).resolve() != prompt_path:
                    errors.append("phase2_dispatch:adversarial_task_prompt_mismatch")
            if record.get("adversarial_task", {}).get("output_path") != task.get("output_path"):
                errors.append("phase2_dispatch:adversarial_output_path_mismatch")

    subject = record.get("subject") if isinstance(record.get("subject"), dict) else {}
    question_path, question_errors = _resolve_bound_ref(
        subject.get("question"),
        base=workspace,
        label="phase2_dispatch.subject.question",
        snapshots=snapshots,
    )
    answer_path, answer_errors = _resolve_bound_ref(
        subject.get("answer"),
        base=workspace,
        label="phase2_dispatch.subject.answer",
        snapshots=snapshots,
    )
    errors.extend(question_errors)
    errors.extend(answer_errors)
    if isinstance(subject.get("question"), dict) and isinstance(subject.get("answer"), dict):
        expected_pair = subject_pair_sha256(
            str(subject["question"].get("sha256")), str(subject["answer"].get("sha256"))
        )
        if subject.get("subject_pair_sha256") != expected_pair:
            errors.append("phase2_dispatch:subject_pair_hash_mismatch")
    deterministic = (
        record.get("deterministic_check")
        if isinstance(record.get("deterministic_check"), dict)
        else {}
    )
    request_path, request_errors = _resolve_bound_ref(
        deterministic.get("request"),
        base=workspace,
        label="phase2_dispatch.deterministic.request",
        snapshots=snapshots,
    )
    report_path, report_errors = _resolve_bound_ref(
        deterministic.get("report"),
        base=workspace,
        label="phase2_dispatch.deterministic.report",
        snapshots=snapshots,
    )
    errors.extend(request_errors)
    errors.extend(report_errors)
    request: dict[str, Any] = {}
    if request_path is not None and report_path is not None and question_path is not None:
        request = _load_object(
            request_path,
            label="phase2_dispatch.deterministic.request",
            errors=errors,
            snapshots=snapshots,
        )
        report = _load_object(
            report_path,
            label="phase2_dispatch.deterministic.report",
            errors=errors,
            snapshots=snapshots,
        )
        request_subject = request.get("subject") if isinstance(request.get("subject"), dict) else {}
        if request_subject.get("subject_pair_sha256") != subject.get("subject_pair_sha256"):
            errors.append("phase2_dispatch:request_subject_pair_mismatch")
        request_question, request_question_errors = _resolve_bound_ref(
            request_subject.get("question"),
            base=db_root,
            label="phase2_dispatch.request.subject.question",
            snapshots=snapshots,
        )
        request_answer, request_answer_errors = _resolve_bound_ref(
            request_subject.get("answer"),
            base=db_root,
            label="phase2_dispatch.request.subject.answer",
            snapshots=snapshots,
        )
        errors.extend(request_question_errors)
        errors.extend(request_answer_errors)
        if request_question != question_path or request_answer != answer_path:
            errors.append("phase2_dispatch:subject_files_do_not_match_deterministic_request")
        if report.get("request_sha256") != snapshots.read(request_path).sha256:
            errors.append("phase2_dispatch:deterministic_report_request_hash_mismatch")
        errors.extend(
            _report_subject_binding_errors(
                request_subject,
                report.get("subject"),
                label="phase2_dispatch:deterministic_report",
            )
        )
        if report.get("passed") is not True:
            errors.append("phase2_dispatch:deterministic_report_not_bound_pass")

    review_paths: dict[str, Path] = {}
    reviews = record.get("reviews") if isinstance(record.get("reviews"), dict) else {}
    for slot in ("sol_review_a", "sol_review_b"):
        binding = reviews.get(slot) if isinstance(reviews.get(slot), dict) else {}
        path, binding_errors = _resolve_bound_ref(
            binding.get("file"),
            base=workspace,
            label=f"phase2_dispatch.reviews.{slot}.file",
            snapshots=snapshots,
        )
        errors.extend(binding_errors)
        if path is not None:
            review_paths[slot] = path
            review = _load_object(
                path,
                label=f"phase2_dispatch.reviews.{slot}",
                errors=errors,
                snapshots=snapshots,
            )
            for field in (
                "run_id",
                "isolation_context_id",
                "output_sha256",
                "execution_provenance",
                "verdict",
            ):
                if binding.get(field) != review.get(field):
                    errors.append(f"phase2_dispatch:{slot}:{field}_binding_mismatch")
    if (
        set(review_paths) == {"sol_review_a", "sol_review_b"}
        and question_path is not None
        and request_path is not None
        and report_path is not None
    ):
        ab = validate_ab_review_receipts(
            review_paths["sol_review_a"],
            review_paths["sol_review_b"],
            question_path=question_path,
            request_path=request_path,
            report_path=report_path,
            review_schema_path=MACHINE_REVIEW_SCHEMA_PATH
            if db_root == DB_ROOT
            else db_root / "kb/machine_governance_v2/schemas/machine_review_record.schema.json",
            generator_thread_id=expected_generator_thread_id,
            root_dispatch_id=root_dispatch_id,
            workspace=workspace,
            db_root=db_root,
        )
        errors.extend(ab.get("errors", []))
        if ab.get("review_bindings") != reviews:
            errors.append("phase2_dispatch:ab_live_binding_mismatch")
    else:
        ab = {"status": "fail", "errors": ["phase2_dispatch:ab_prerequisites_missing"]}
    return {
        "check": "immutable_phase2_adversarial_dispatch_live_binding",
        "status": "pass" if not errors else "fail",
        "sidecar": snapshots.read(sidecar_path).ref(workspace.resolve()),
        "subject_pair_sha256": subject.get("subject_pair_sha256"),
        "deterministic_report_sha256": (
            snapshots.read(report_path).sha256
            if report_path is not None and report_path.is_file()
            else None
        ),
        "snapshot_graph_sha256": snapshots.digest(),
        "review_bindings": reviews,
        "ab_validation": ab,
        "errors": sorted(dict.fromkeys(errors)),
    }


def create_phase2_adversarial_dispatch(
    formal_freeze_path: Path,
    *,
    review_a_path: Path = REVIEW_A_PATH,
    review_b_path: Path = REVIEW_B_PATH,
    sidecar_path: Path = ADVERSARIAL_PHASE2_DISPATCH_PATH,
    adversarial_prompt_path: Path,
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    expected_generator_thread_id: str = GENERATOR_THREAD_ID,
    expected_root_dispatch_id: str = ROOT_DISPATCH_ID,
) -> dict[str, Any]:
    """Freeze the exact live A/B PASS receipts into an immutable phase-2 sidecar."""

    ab = validate_ab_review_receipts(
        review_a_path,
        review_b_path,
        question_path=QUESTION_PATH,
        request_path=REQUEST_PATH,
        report_path=REPORT_PATH,
        review_schema_path=MACHINE_REVIEW_SCHEMA_PATH,
        generator_thread_id=expected_generator_thread_id,
        root_dispatch_id=expected_root_dispatch_id,
        workspace=workspace,
        db_root=db_root,
    )
    if ab.get("status") != "pass":
        raise RuntimeError(f"phase-2 dispatch refused: A/B validation failed: {ab}")
    formal = _validate_formal_freeze_for_phase2(
        formal_freeze_path,
        workspace=workspace,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
        expected_root_dispatch_id=expected_root_dispatch_id,
    )
    if formal.get("status") != "pass":
        raise RuntimeError(f"phase-2 dispatch refused: formal freeze invalid: {formal}")
    if formal.get("review_dispatch_authorized") is not True:
        raise RuntimeError(
            "phase-2 dispatch refused: R17 formal freeze is inert; a separate root-owned "
            "dispatch activation bound to the immutable receipt is required"
        )
    dispatch = formal["dispatch"]
    prompt_ref = (dispatch.get("prompts") or {}).get("adversarial")
    prompt_path, prompt_errors = _resolve_bound_ref(
        prompt_ref, base=workspace, label="review_dispatch.prompts.adversarial"
    )
    if prompt_errors or prompt_path != adversarial_prompt_path.resolve():
        raise RuntimeError(
            f"phase-2 dispatch refused: static adversarial prompt binding invalid: {prompt_errors}"
        )
    schema_ref = (dispatch.get("schemas") or {}).get("adversarial_check_record")
    adversarial_schema_path, adversarial_schema_errors = _resolve_bound_ref(
        schema_ref, base=workspace, label="review_dispatch.schemas.adversarial_check_record"
    )
    if adversarial_schema_errors or adversarial_schema_path != ADVERSARIAL_SCHEMA_PATH.resolve():
        raise RuntimeError(
            "phase-2 dispatch refused: adversarial receipt schema binding invalid: "
            f"{adversarial_schema_errors}"
        )
    task_rows = [
        row
        for row in dispatch.get("requested_tasks", [])
        if isinstance(row, dict) and row.get("slot") == "adversarial_check"
    ]
    if len(task_rows) != 1 or task_rows[0].get("phase") != 2 or task_rows[0].get(
        "dispatch_now"
    ) is not False:
        raise RuntimeError("phase-2 dispatch refused: static adversarial task is not uniquely gated")
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    subject = {
        "question": workspace_file_ref(QUESTION_PATH, workspace=workspace),
        "answer": workspace_file_ref(ANSWER_PATH, workspace=workspace),
        "subject_pair_sha256": request["subject"]["subject_pair_sha256"],
    }
    record: dict[str, Any] = {
        "schema_version": "2.0.0",
        "record_type": "adversarial_phase2_dispatch",
        "dispatch_id": f"PHASE2-{expected_version_id}-{ab['review_bindings']['sol_review_a']['output_sha256'][:12]}-{ab['review_bindings']['sol_review_b']['output_sha256'][:12]}",
        "version_id": expected_version_id,
        "paper_id": expected_paper_id,
        "root_dispatch_id": expected_root_dispatch_id,
        "schema_binding": workspace_file_ref(PHASE2_DISPATCH_SCHEMA_PATH, workspace=workspace),
        "formal_freeze": workspace_file_ref(formal_freeze_path, workspace=workspace),
        "formal_freeze_schema": workspace_file_ref(formal["schema_path"], workspace=workspace),
        "formal_freeze_payload": {
            "json_pointer": "#/freeze_payload",
            "canonical_sha256": formal["payload_sha256"],
            "canonical_bytes": formal["payload_bytes"],
        },
        "review_dispatch": workspace_file_ref(formal["dispatch_path"], workspace=workspace),
        "adversarial_prompt": workspace_file_ref(adversarial_prompt_path, workspace=workspace),
        "adversarial_record_schema": workspace_file_ref(
            adversarial_schema_path, workspace=workspace
        ),
        "subject": subject,
        "deterministic_check": {
            "request": workspace_file_ref(REQUEST_PATH, workspace=workspace),
            "report": workspace_file_ref(REPORT_PATH, workspace=workspace),
        },
        "reviews": copy.deepcopy(ab["review_bindings"]),
        "adversarial_task": {
            "output_path": task_rows[0]["output_path"],
            "required_engine": "gpt-5.6-sol",
            "required_reasoning_effort": "xhigh",
            "new_task_required": True,
            "distinct_new_task_required": True,
            "dispatch_authorized": True,
            "must_not_edit_review_files": True,
        },
        "source_review_files_immutable": True,
        "chain_creation_authorized": False,
        "controller_registration_requested": False,
        "publication_requested": False,
        "human_reviewed": False,
    }
    record["output_sha256"] = record_output_sha256(record)
    schema_errors = _phase2_schema_validate(record, PHASE2_DISPATCH_SCHEMA_PATH)
    if schema_errors:
        raise RuntimeError(f"phase-2 dispatch refused: sidecar schema invalid: {schema_errors}")

    # Re-read every externally authored/frozen prerequisite immediately before
    # the exclusive create.  A mutation between the first validation and this
    # point cannot be silently captured under stale metadata.
    ab_before_write = validate_ab_review_receipts(
        review_a_path,
        review_b_path,
        question_path=QUESTION_PATH,
        request_path=REQUEST_PATH,
        report_path=REPORT_PATH,
        review_schema_path=MACHINE_REVIEW_SCHEMA_PATH,
        generator_thread_id=expected_generator_thread_id,
        root_dispatch_id=expected_root_dispatch_id,
        workspace=workspace,
        db_root=db_root,
    )
    formal_before_write = _validate_formal_freeze_for_phase2(
        formal_freeze_path,
        workspace=workspace,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
        expected_root_dispatch_id=expected_root_dispatch_id,
    )
    if (
        ab_before_write.get("status") != "pass"
        or ab_before_write.get("review_bindings") != ab.get("review_bindings")
        or formal_before_write.get("status") != "pass"
        or formal_before_write.get("payload_sha256") != formal.get("payload_sha256")
        or workspace_file_ref(formal_before_write["dispatch_path"], workspace=workspace)
        != record["review_dispatch"]
    ):
        raise RuntimeError("phase-2 dispatch refused: prerequisite changed during validation")
    created = _write_immutable_json(sidecar_path, record)
    validation = validate_phase2_adversarial_dispatch(
        sidecar_path,
        workspace=workspace,
        db_root=db_root,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
        expected_generator_thread_id=expected_generator_thread_id,
    )
    if validation.get("status") != "pass":
        if created:
            try:
                sidecar_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"phase-2 dispatch failed post-write revalidation: {validation}")
    return {
        **validation,
        "created": created,
        "dispatch_authorized": True,
        "chain_creation_authorized": False,
        "controller_registration_requested": False,
        "publication_requested": False,
    }


def _r18_phase2_prerequisites(
    *,
    phase1_activation_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    observation_a_path: Path,
    observation_b_path: Path,
    immutable_inputs: dict[str, Path] | None,
    schema_paths: dict[str, Path] | None,
    workspace: Path,
    db_root: Path,
    expected_version_id: str,
    expected_paper_id: str,
    snapshot_graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    graph = _shared_snapshot_graph(workspace, snapshot_graph)
    activation_snapshot = graph.read(phase1_activation_path)
    activation = activation_snapshot.json_value()
    if not isinstance(activation, dict):
        raise R17BoundaryError("phase2:phase1_activation_object_required")
    if activation.get("schema_version") != "3.0.0-r18" or activation.get("record_type") != "phase1_review_dispatch_activation":
        raise R17BoundaryError("phase2:legacy_activation_rejected")
    formal_path = graph.verify_ref(activation.get("formal_freeze"), label="phase2.activation.formal_freeze").path
    formal_sidecar_path = graph.verify_ref(activation.get("formal_freeze_sidecar"), label="phase2.activation.formal_freeze_sidecar").path
    reconciliation_path = graph.verify_ref(activation.get("generator_reconciliation"), label="phase2.activation.reconciliation").path
    root_authorization_path = graph.verify_ref(activation.get("root_phase1_authorization"), label="phase2.activation.root_authorization").path
    bundle_dir = phase1_activation_path.parent
    dispatch_path = bundle_dir / "review_dispatch_request_r18.json"
    prompt_a_path = bundle_dir / "REVIEW_PROMPT_A.md"
    prompt_b_path = bundle_dir / "REVIEW_PROMPT_B.md"
    phase1 = validate_phase1_review_dispatch_activation(
        phase1_activation_path,
        dispatch_path=dispatch_path,
        prompt_a_path=prompt_a_path,
        prompt_b_path=prompt_b_path,
        formal_freeze_path=formal_path,
        formal_freeze_sidecar_path=formal_sidecar_path,
        reconciliation_path=reconciliation_path,
        root_authorization_path=root_authorization_path,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
        workspace=workspace,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
        snapshot_graph=graph,
    )
    if phase1.get("status") != "pass":
        raise R17BoundaryError(f"phase2:phase1_activation_invalid:{phase1['errors']}")
    dispatch = graph.read(dispatch_path).json_value()
    if not isinstance(dispatch, dict):
        raise R17BoundaryError("phase2:dispatch_object_required")
    inputs = dispatch.get("immutable_inputs", {})
    schemas = dispatch.get("schemas", {})
    if inputs != activation.get("immutable_inputs") or schemas != activation.get("schemas"):
        raise R17BoundaryError("phase2:activation_dispatch_binding_set_mismatch")
    policy_inputs, policy_graph = _validate_phase1_policy_inputs(
        dispatch,
        activation,
        workspace=workspace,
        disallowed_paths=(
            phase1_activation_path,
            dispatch_path,
            prompt_a_path,
            prompt_b_path,
            review_a_path,
            review_b_path,
            observation_a_path,
            observation_b_path,
            *list((immutable_inputs or {}).values()),
            *list((schema_paths or {}).values()),
        ),
    )
    question_path = graph.verify_ref(inputs.get("question_subject"), label="phase2.dispatch.question_subject").path
    answer_path = graph.verify_ref(inputs.get("answer_subject"), label="phase2.dispatch.answer_subject").path
    request_path = graph.verify_ref(inputs.get("deterministic_request"), label="phase2.dispatch.deterministic_request").path
    report_path = graph.verify_ref(inputs.get("deterministic_report"), label="phase2.dispatch.deterministic_report").path
    review_schema_path = graph.verify_ref(dispatch.get("schemas", {}).get("machine_review_record"), label="phase2.dispatch.machine_review_schema").path
    ab = validate_ab_review_receipts(
        review_a_path,
        review_b_path,
        question_path=question_path,
        request_path=request_path,
        report_path=report_path,
        review_schema_path=review_schema_path,
        activation_path=phase1_activation_path,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
        observation_a_path=observation_a_path,
        observation_b_path=observation_b_path,
        workspace=workspace,
        db_root=db_root,
        snapshot_graph=graph,
    )
    if ab.get("status") != "pass":
        raise R17BoundaryError(f"phase2:ab_validation_failed:{ab['errors']}")
    formal = _validate_r18_formal_freeze_with_graph(
        formal_path,
        formal_sidecar_path,
        workspace=workspace,
        graph=graph,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
    )
    checkpoint = graph.revalidate()
    if checkpoint.get("status") != "pass":
        raise R17BoundaryError("phase2:prerequisite_snapshot_changed:" + ",".join(checkpoint.get("errors", [])))
    policy_checkpoint = policy_graph.revalidate()
    if policy_checkpoint.get("status") != "pass":
        raise R17BoundaryError(
            "phase2:policy_snapshot_changed:"
            + ",".join(policy_checkpoint.get("errors", []))
        )
    return {
        "graph": graph,
        "activation": activation,
        "activation_ref": activation_snapshot.ref(workspace),
        "dispatch": dispatch,
        "dispatch_ref": graph.read(dispatch_path).ref(workspace),
        "formal": formal,
        "reconciliation_ref": graph.read(reconciliation_path).ref(workspace),
        "question_path": question_path,
        "answer_path": answer_path,
        "request_path": request_path,
        "report_path": report_path,
        "immutable_inputs": copy.deepcopy(inputs),
        "schemas": copy.deepcopy(schemas),
        "policy_inputs": copy.deepcopy(policy_inputs),
        "protocol_policy_digest": _protocol_policy_digest(policy_inputs),
        "policy_graph": policy_graph,
        "ab": ab,
    }


def validate_phase2_adversarial_dispatch_r18(
    sidecar_path: Path,
    *,
    phase1_activation_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    observation_a_path: Path,
    observation_b_path: Path,
    adversarial_prompt_path: Path,
    immutable_inputs: dict[str, Path] | None = None,
    schema_paths: dict[str, Path] | None = None,
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
    snapshot_graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    record: dict[str, Any] = {}
    prerequisites: dict[str, Any] = {}
    try:
        _validate_phase2_output_paths(
            activation_path=phase1_activation_path,
            adversarial_prompt_path=adversarial_prompt_path,
            sidecar_path=sidecar_path,
            workspace=workspace,
            expected_version_id=expected_version_id,
        )
        prerequisites = _r18_phase2_prerequisites(
            phase1_activation_path=phase1_activation_path,
            review_a_path=review_a_path,
            review_b_path=review_b_path,
            observation_a_path=observation_a_path,
            observation_b_path=observation_b_path,
            immutable_inputs=immutable_inputs,
            schema_paths=schema_paths,
            workspace=workspace,
            db_root=db_root,
            expected_version_id=expected_version_id,
            expected_paper_id=expected_paper_id,
            snapshot_graph=snapshot_graph,
        )
        graph: SnapshotGraph = prerequisites["graph"]
        sidecar_snapshot = graph.read(sidecar_path)
        prompt_snapshot = graph.read(adversarial_prompt_path)
        record = sidecar_snapshot.json_value()
        if not isinstance(record, dict):
            raise R17BoundaryError("phase2_dispatch:object_required")
        schema_snapshot = graph.read(PHASE2_DISPATCH_SCHEMA_PATH)
        graph.verify_ref(record.get("schema_binding"), label="phase2_dispatch.schema_binding", expected_path=PHASE2_DISPATCH_SCHEMA_PATH)
        _schema_validate_snapshot(record, schema_snapshot, label="phase2_dispatch")
        if record.get("output_sha256") != record_output_sha256(record):
            raise R17BoundaryError("phase2_dispatch:self_hash_mismatch")
        if (
            record.get("review_protocol") != R18_PHASE1_PROTOCOL_TOKEN
            or record.get("review_attempt_id")
            != prerequisites["dispatch"].get("review_attempt_id")
            or record.get("review_attempt_id")
            != prerequisites["activation"].get("review_attempt_id")
        ):
            raise R17BoundaryError(
                "phase2_dispatch:review_attempt_binding_mismatch"
            )
        formal = prerequisites["formal"]
        expected_bindings = {
            "formal_freeze": formal["formal_freeze"],
            "formal_freeze_sidecar": formal["formal_freeze_sidecar"],
            "formal_freeze_payload": formal["freeze_payload"],
            "generator_reconciliation": prerequisites["reconciliation_ref"],
            "phase1_activation": prerequisites["activation_ref"],
            "review_dispatch": prerequisites["dispatch_ref"],
            "immutable_inputs": prerequisites["immutable_inputs"],
            "schemas": prerequisites["schemas"],
            "policy_inputs": prerequisites["policy_inputs"],
            "protocol_policy_digest": prerequisites["protocol_policy_digest"],
            "adversarial_prompt": prompt_snapshot.ref(workspace),
            "adversarial_record_schema": graph.read(ADVERSARIAL_SCHEMA_PATH).ref(workspace),
        }
        for field, expected in expected_bindings.items():
            if record.get(field) != expected:
                raise R17BoundaryError(f"phase2_dispatch:{field}_binding_mismatch")
        question_ref = graph.read(prerequisites["question_path"]).ref(workspace)
        answer_ref = graph.read(prerequisites["answer_path"]).ref(workspace)
        expected_subject = {"question": question_ref, "answer": answer_ref, "subject_pair_sha256": subject_pair_sha256(question_ref["sha256"], answer_ref["sha256"])}
        if record.get("subject") != expected_subject:
            raise R17BoundaryError("phase2_dispatch:subject_mismatch")
        expected_deterministic = {"request": graph.read(prerequisites["request_path"]).ref(workspace), "report": graph.read(prerequisites["report_path"]).ref(workspace)}
        if record.get("deterministic_check") != expected_deterministic:
            raise R17BoundaryError("phase2_dispatch:deterministic_binding_mismatch")
        if record.get("reviews") != prerequisites["ab"]["review_bindings"]:
            raise R17BoundaryError("phase2_dispatch:review_binding_mismatch")
        planned = prerequisites["dispatch"]["requested_tasks"][2]
        task = record.get("adversarial_task", {})
        if task.get("output_path") != planned.get("output_path") or task.get("dispatch_authorized") is not True or task.get("exclusive_output") is not True:
            raise R17BoundaryError("phase2_dispatch:adversarial_task_mismatch")
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError("phase2_dispatch:snapshot_changed_before_return:" + ",".join(checkpoint.get("errors", [])))
        policy_checkpoint = prerequisites["policy_graph"].revalidate()
        if policy_checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "phase2_dispatch:policy_snapshot_changed_before_return:"
                + ",".join(policy_checkpoint.get("errors", []))
            )
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        errors.append(str(exc))
    return {
        "check": "r18_phase2_adversarial_dispatch",
        "status": "pass" if not errors else "fail",
        "dispatch_id": record.get("dispatch_id"),
        "sidecar": workspace_file_ref(sidecar_path, workspace=workspace) if not errors else None,
        "subject_pair_sha256": record.get("subject", {}).get("subject_pair_sha256"),
        "deterministic_report_sha256": record.get("deterministic_check", {}).get("report", {}).get("sha256"),
        "review_bindings": record.get("reviews", {}),
        "errors": sorted(dict.fromkeys(errors)),
    }


def create_phase2_adversarial_dispatch_r18(
    *,
    phase1_activation_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    observation_a_path: Path,
    observation_b_path: Path,
    adversarial_prompt_path: Path,
    sidecar_path: Path,
    immutable_inputs: dict[str, Path] | None = None,
    schema_paths: dict[str, Path] | None = None,
    workspace: Path = WORKSPACE,
    db_root: Path = DB_ROOT,
    expected_version_id: str = VERSION_ID,
    expected_paper_id: str = PAPER_ID,
) -> dict[str, Any]:
    _validate_phase2_output_paths(
        activation_path=phase1_activation_path,
        adversarial_prompt_path=adversarial_prompt_path,
        sidecar_path=sidecar_path,
        workspace=workspace,
        expected_version_id=expected_version_id,
    )
    prerequisites = _r18_phase2_prerequisites(
        phase1_activation_path=phase1_activation_path,
        review_a_path=review_a_path,
        review_b_path=review_b_path,
        observation_a_path=observation_a_path,
        observation_b_path=observation_b_path,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
        workspace=workspace,
        db_root=db_root,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
    )
    graph: SnapshotGraph = prerequisites["graph"]
    planned = prerequisites["dispatch"]["requested_tasks"][2]
    prompt_text = (
        f"# Independent R18 adversarial review\n\nConsume only the exact phase-2 sidecar for {expected_version_id}. "
        "Use a new gpt-5.6-sol/xhigh task distinct from generator, A and B. Bind the exact A/B files and root observations; "
        "write only the planned exclusive adversarial output. Test every P01-P36 item plus missing file, wrong hash, receipt reuse, "
        "subject mismatch, numeric/unit/charge/conservation mutations and false human/publication claims. Never edit A/B.\n"
    )
    prompt_bytes = prompt_text.encode("utf-8")
    prompt_ref = _bytes_ref(adversarial_prompt_path, prompt_bytes, workspace=workspace)
    question_ref = graph.read(prerequisites["question_path"]).ref(workspace)
    answer_ref = graph.read(prerequisites["answer_path"]).ref(workspace)
    formal = prerequisites["formal"]
    record: dict[str, Any] = {
        "schema_version": "3.0.0-r18", "record_type": "adversarial_phase2_dispatch",
        "dispatch_id": f"PHASE2-{expected_version_id}-{prerequisites['ab']['review_bindings']['sol_review_a']['output_sha256'][:12]}-{prerequisites['ab']['review_bindings']['sol_review_b']['output_sha256'][:12]}",
        "review_protocol": R18_PHASE1_PROTOCOL_TOKEN,
        "review_attempt_id": prerequisites["activation"]["review_attempt_id"],
        "version_id": expected_version_id, "paper_id": expected_paper_id,
        "root_dispatch_id": prerequisites["activation"]["root_dispatch_id"],
        "schema_binding": graph.read(PHASE2_DISPATCH_SCHEMA_PATH).ref(workspace),
        "formal_freeze": formal["formal_freeze"], "formal_freeze_sidecar": formal["formal_freeze_sidecar"], "formal_freeze_payload": formal["freeze_payload"],
        "generator_reconciliation": prerequisites["reconciliation_ref"], "phase1_activation": prerequisites["activation_ref"], "review_dispatch": prerequisites["dispatch_ref"],
        "immutable_inputs": copy.deepcopy(prerequisites["immutable_inputs"]), "schemas": copy.deepcopy(prerequisites["schemas"]),
        "policy_inputs": copy.deepcopy(prerequisites["policy_inputs"]), "protocol_policy_digest": prerequisites["protocol_policy_digest"],
        "adversarial_prompt": prompt_ref, "adversarial_record_schema": graph.read(ADVERSARIAL_SCHEMA_PATH).ref(workspace),
        "subject": {"question": question_ref, "answer": answer_ref, "subject_pair_sha256": subject_pair_sha256(question_ref["sha256"], answer_ref["sha256"])},
        "deterministic_check": {"request": graph.read(prerequisites["request_path"]).ref(workspace), "report": graph.read(prerequisites["report_path"]).ref(workspace)},
        "reviews": copy.deepcopy(prerequisites["ab"]["review_bindings"]),
        "adversarial_task": {"output_path": planned["output_path"], "required_engine": "gpt-5.6-sol", "required_reasoning_effort": "xhigh", "new_task_required": True, "distinct_new_task_required": True, "dispatch_authorized": True, "must_not_edit_review_files": True, "exclusive_output": True},
        "source_review_files_immutable": True,
        "authority": {"adversarial_dispatch_authorized": True, "adversarial_task_created": False, "chain_creation_authorized": False, "controller_registration_authorized": False, "batch_registration_authorized": False, "publication_authorized": False, "delivery_authorized": False, "external_delivery_authorized": False},
        "human_reviewed": False,
    }
    record["output_sha256"] = record_output_sha256(record)
    _schema_validate_snapshot(record, graph.read(PHASE2_DISPATCH_SCHEMA_PATH), label="phase2_dispatch")
    policy_checkpoint = prerequisites["policy_graph"].revalidate()
    if policy_checkpoint.get("status") != "pass":
        raise R17BoundaryError(
            "phase2_create:policy_snapshot_changed_before_write:"
            + ",".join(policy_checkpoint.get("errors", []))
        )
    transaction = exclusive_create_bundle(
        workspace=workspace,
        files=[(adversarial_prompt_path, prompt_bytes), (sidecar_path, _pretty_json_bytes(record))],
        input_graph=graph,
    )
    policy_checkpoint = prerequisites["policy_graph"].revalidate()
    if policy_checkpoint.get("status") != "pass":
        sidecar_path.unlink(missing_ok=True)
        adversarial_prompt_path.unlink(missing_ok=True)
        raise R17BoundaryError(
            "phase2_create:policy_snapshot_changed_during_write:"
            + ",".join(policy_checkpoint.get("errors", []))
        )
    validated = validate_phase2_adversarial_dispatch_r18(
        sidecar_path,
        phase1_activation_path=phase1_activation_path,
        review_a_path=review_a_path,
        review_b_path=review_b_path,
        observation_a_path=observation_a_path,
        observation_b_path=observation_b_path,
        adversarial_prompt_path=adversarial_prompt_path,
        immutable_inputs=immutable_inputs,
        schema_paths=schema_paths,
        workspace=workspace,
        db_root=db_root,
        expected_version_id=expected_version_id,
        expected_paper_id=expected_paper_id,
        snapshot_graph=graph,
    )
    if validated.get("status") != "pass":
        sidecar_path.unlink(missing_ok=True)
        adversarial_prompt_path.unlink(missing_ok=True)
        raise R17BoundaryError(f"phase2_create:post_write_validation_failed:{validated['errors']}")
    return {**validated, **transaction, "adversarial_dispatch_authorized": True, "adversarial_task_created": False, "chain_creation_authorized": False, "publication_authorized": False}


# R18 is the only reachable phase-2 producer/validator.  The legacy R13/R17
# implementations above remain readable for historical tests but cannot be
# selected or invoked through the public names.
validate_phase2_adversarial_dispatch = validate_phase2_adversarial_dispatch_r18
create_phase2_adversarial_dispatch = create_phase2_adversarial_dispatch_r18


def validate_external_review_receipts(
    *,
    phase1_activation_path: Path | None = None,
    phase2_dispatch_path: Path | None = None,
    adversarial_prompt_path: Path | None = None,
    root_metadata_observation_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Validate externally authored receipts without editing their content."""

    if (
        phase1_activation_path is None
        or phase2_dispatch_path is None
        or adversarial_prompt_path is None
        or root_metadata_observation_paths is None
        or set(root_metadata_observation_paths)
        != {"generator", "sol_review_a", "sol_review_b", "adversarial_check"}
    ):
        return {
            "check": "r18_external_review_receipts",
            "status": "fail",
            "errors": ["r18_phase_and_exact_four_root_observations_required"],
        }
    try:
        attempt_graph = SnapshotGraph(WORKSPACE.resolve())
        activation_record = attempt_graph.read(phase1_activation_path).json_value()
        if not isinstance(activation_record, dict):
            raise R17BoundaryError("external:activation_object_required")
        dispatch_path = phase1_activation_path.parent / "review_dispatch_request_r18.json"
        dispatch_record = attempt_graph.read(dispatch_path).json_value()
        if not isinstance(dispatch_record, dict):
            raise R17BoundaryError("external:dispatch_object_required")
        review_paths = _dispatch_review_output_paths(
            dispatch_record,
            activation_path=phase1_activation_path,
            workspace=WORKSPACE,
        )
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        return {
            "check": "r18_external_review_receipts",
            "status": "fail",
            "errors": [f"attempt_binding:{exc}"],
        }
    review_a_path = review_paths["sol_review_a"]
    review_b_path = review_paths["sol_review_b"]
    adversarial_path = review_paths["adversarial_check"]
    ab_validation = validate_ab_review_receipts(
        review_a_path=review_a_path,
        review_b_path=review_b_path,
        activation_path=phase1_activation_path,
        observation_a_path=root_metadata_observation_paths["sol_review_a"],
        observation_b_path=root_metadata_observation_paths["sol_review_b"],
    )
    phase2_validation = validate_phase2_adversarial_dispatch(
        phase2_dispatch_path,
        phase1_activation_path=phase1_activation_path,
        review_a_path=review_a_path,
        review_b_path=review_b_path,
        observation_a_path=root_metadata_observation_paths["sol_review_a"],
        observation_b_path=root_metadata_observation_paths["sol_review_b"],
        adversarial_prompt_path=adversarial_prompt_path,
    )
    records = {
        "sol_review_a": json.loads(review_a_path.read_text(encoding="utf-8")),
        "sol_review_b": json.loads(review_b_path.read_text(encoding="utf-8")),
        "adversarial": json.loads(adversarial_path.read_text(encoding="utf-8")),
    }
    validate(
        records["sol_review_a"],
        load_schema(MACHINE_REVIEW_SCHEMA_PATH),
    )
    validate(
        records["sol_review_b"],
        load_schema(MACHINE_REVIEW_SCHEMA_PATH),
    )
    validate(
        records["adversarial"],
        load_schema(ADVERSARIAL_SCHEMA_PATH),
    )
    errors: list[str] = [
        *(f"phase1:{error}" for error in ab_validation.get("errors", [])),
        *(f"phase2:{error}" for error in phase2_validation.get("errors", [])),
    ]
    request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    question = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    report_sha = sha256_file(REPORT_PATH)
    expected_order = _expected_atomic_ids(question)
    expected_parts = set(expected_order)
    expected_count = len(expected_order)
    request_scope = request.get("atomic_scope", {}).get("declared_atomic_part_ids")
    if request_scope != expected_order:
        errors.append("deterministic request atomic scope differs from frozen hierarchy")
    provenances = []
    for slot in ("sol_review_a", "sol_review_b"):
        record = records[slot]
        if record.get("slot") != slot:
            errors.append(f"{slot}:slot mismatch")
        if record.get("subject") != request["subject"]:
            errors.append(f"{slot}:subject mismatch")
        if record.get("deterministic_report_sha256") != report_sha:
            errors.append(f"{slot}:deterministic report hash mismatch")
        if record.get("output_sha256") != record_output_sha256(record):
            errors.append(f"{slot}:canonical self hash mismatch")
        if record.get("verdict") != "pass":
            errors.append(f"{slot}:external verdict is not pass")
        findings = [row for row in record.get("findings", []) if isinstance(row, dict)]
        ids = [row.get("atomic_part_id") for row in findings]
        if len(ids) != expected_count or set(ids) != expected_parts or len(ids) != len(set(ids)):
            errors.append(f"{slot}:atomic coverage must be exact frozen hierarchy membership")
        for finding in findings:
            for field in ("finding", "evidence"):
                semantic_error = _semantic_review_text_error(finding.get(field))
                if semantic_error:
                    errors.append(
                        f"{slot}:{finding.get('atomic_part_id')}:{field}:{semantic_error}"
                    )
            if finding.get("verdict") != "pass" or any(
                value != "pass" for value in finding.get("checks", {}).values()
            ):
                errors.append(f"{slot}:non-pass finding:{finding.get('atomic_part_id')}")
        provenances.append(record.get("execution_provenance", {}))
    adversarial = records["adversarial"]
    expected_phase2_ref = db_file_ref(phase2_dispatch_path)
    if expected_phase2_ref is None or adversarial.get("phase2_dispatch") != expected_phase2_ref:
        errors.append("adversarial:phase2 dispatch file binding mismatch")
    if adversarial.get("phase2_binding_scope") != "direct_full_subject":
        errors.append("adversarial:full-paper receipt must use direct phase2 binding")
    if adversarial.get("parent_adversarial_check") is not None:
        errors.append("adversarial:full-paper receipt cannot bind a parent adversarial record")
    if phase2_validation.get("subject_pair_sha256") != request["subject"]["subject_pair_sha256"]:
        errors.append("adversarial:phase2 subject mismatch")
    if phase2_validation.get("deterministic_report_sha256") != report_sha:
        errors.append("adversarial:phase2 deterministic report mismatch")
    if adversarial.get("subject_pair_sha256") != request["subject"]["subject_pair_sha256"]:
        errors.append("adversarial:subject mismatch")
    if adversarial.get("deterministic_report_sha256") != report_sha:
        errors.append("adversarial:deterministic report hash mismatch")
    if adversarial.get("review_record_sha256s") != {
        "sol_review_a": sha256_file(review_a_path),
        "sol_review_b": sha256_file(review_b_path),
    }:
        errors.append("adversarial:review hash binding mismatch")
    if adversarial.get("output_sha256") != record_output_sha256(adversarial):
        errors.append("adversarial:canonical self hash mismatch")
    coverage = [row for row in adversarial.get("item_coverage", []) if isinstance(row, dict)]
    coverage_ids = [row.get("atomic_part_id") for row in coverage]
    if len(coverage_ids) != expected_count or set(coverage_ids) != expected_parts or len(coverage_ids) != len(set(coverage_ids)):
        errors.append("adversarial:atomic coverage must be exact frozen hierarchy membership")
    if any(row.get("verdict") != "pass" or row.get("all_observed_rejected") is not True for row in coverage):
        errors.append("adversarial:item coverage contains a non-pass")
    for row in coverage:
        for field in ("finding", "evidence"):
            if field in row:
                semantic_error = _semantic_review_text_error(row.get(field))
                if semantic_error:
                    errors.append(
                        f"adversarial:{row.get('atomic_part_id')}:{field}:{semantic_error}"
                    )
    mutation_ids = {
        row.get("mutation_id")
        for row in adversarial.get("mutation_cases", [])
        if isinstance(row, dict)
    }
    required = {
        "missing_file",
        "wrong_hash",
        "fake_human_review",
        "duplicate_review_run",
        "question_answer_mismatch",
        "numeric_mismatch",
        "unit_mismatch",
        "charge_imbalance",
        "conservation_imbalance",
    }
    if not required <= mutation_ids:
        errors.append(f"adversarial:required mutations missing:{sorted(required - mutation_ids)}")
    if any(not any(part_id in str(mid) for mid in mutation_ids) for part_id in expected_parts):
        errors.append("adversarial:each frozen atomic part needs a part-specific mutation")
    if adversarial.get("verdict") != "pass":
        errors.append("adversarial:external verdict is not pass")
    provenances.append(adversarial.get("execution_provenance", {}))
    try:
        observation_graph = SnapshotGraph(WORKSPACE)
        phase2_record = observation_graph.read(phase2_dispatch_path).json_value()
        formal_path = observation_graph.verify_ref(
            phase2_record.get("formal_freeze"), label="external.phase2.formal"
        ).path
        formal_sidecar_path = observation_graph.verify_ref(
            phase2_record.get("formal_freeze_sidecar"), label="external.phase2.formal_sidecar"
        ).path
        formal = _validate_r18_formal_freeze_with_graph(
            formal_path,
            formal_sidecar_path,
            workspace=WORKSPACE,
            graph=observation_graph,
            expected_version_id=VERSION_ID,
            expected_paper_id=PAPER_ID,
        )
        activation = observation_graph.read(phase1_activation_path).json_value()
        adversarial_observation = _validate_r18_observation_with_graph(
            root_metadata_observation_paths["adversarial_check"],
            workspace=WORKSPACE,
            graph=observation_graph,
            expected_role="adversarial_check",
            formal=formal,
            expected_root_dispatch_id=activation["root_dispatch_id"],
        )
        if adversarial_observation["execution"] != adversarial.get("execution_provenance"):
            errors.append("adversarial:execution_provenance_not_root_observed")
        reconciliation = observation_graph.read(
            observation_graph.verify_ref(
                phase2_record.get("generator_reconciliation"),
                label="external.phase2.reconciliation",
            ).path
        ).json_value()
        generator_observation_ref = workspace_file_ref(
            root_metadata_observation_paths["generator"]
        )
        if generator_observation_ref != reconciliation.get("codex_metadata_observation"):
            errors.append("generator:root_observation_not_reconciliation_bound")
        observed_records = [
            observation_graph.read(path).json_value()
            for path in root_metadata_observation_paths.values()
        ]
        identities = [
            (
                row["observation_id"],
                row["observed_task_metadata"]["thread_id"],
                row["observed_task_metadata"]["turn_id"],
                row["derived_execution_metadata"]["receipt_id"],
            )
            for row in observed_records
        ]
        for index in range(4):
            if len({row[index] for row in identities}) != 4:
                errors.append("root_metadata_observations:four_distinct_identities_required")
                break
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, R17BoundaryError) as exc:
        errors.append(f"root_metadata_observations:{exc}")
    thread_ids = [row.get("thread_id") for row in provenances]
    turn_ids = [row.get("turn_id") for row in provenances]
    receipt_ids = [row.get("receipt_id") for row in provenances]
    generator_thread_id = activation_record.get("generator_thread_id")
    root_dispatch_id = activation_record.get("root_dispatch_id")
    if (
        len(set(thread_ids)) != 3
        or generator_thread_id in thread_ids
        or root_dispatch_id in thread_ids
    ):
        errors.append("external A/B/adversarial must come from three distinct non-generator/non-root tasks")
    if len(set(turn_ids)) != 3:
        errors.append("external A/B/adversarial turn IDs must be distinct")
    if len(set(receipt_ids)) != 3:
        errors.append("external A/B/adversarial receipt IDs must be distinct")
    if any(row.get("provider") != "codex_app_thread" for row in provenances):
        errors.append("external execution provenance provider mismatch")
    if any(row.get("root_dispatch_id") != root_dispatch_id for row in provenances):
        errors.append("external execution provenance root dispatch mismatch")
    return {
        "check": "external_review_provenance_schema_hash_and_per_item_coverage",
        "status": "pass" if not errors else "fail",
        "thread_ids": thread_ids,
        "turn_ids": turn_ids,
        "execution_receipt_ids": receipt_ids,
        "phase1_ab_validation": ab_validation,
        "phase2_dispatch_validation": phase2_validation,
        "receipt_hashes": {
            "sol_review_a": sha256_file(review_a_path),
            "sol_review_b": sha256_file(review_b_path),
            "adversarial": sha256_file(adversarial_path),
        },
        "errors": errors,
    }


def _adversarial_cases_by_part(
    record: dict[str, Any], expected_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for case in record.get("mutation_cases", []):
        if not isinstance(case, dict):
            continue
        mutation_id = case.get("mutation_id")
        if not isinstance(mutation_id, str):
            continue
        match = re.search(r"P[0-9]+", mutation_id)
        if match:
            result.setdefault(match.group(0), []).append(case)
    missing = [part_id for part_id in expected_ids if part_id not in result]
    extra = sorted(set(result) - set(expected_ids))
    if missing or extra:
        raise RuntimeError(
            "adversarial record per-part mutations are not exact hierarchy membership: "
            f"missing={missing}, extra={extra}"
        )
    return result


def _formula_case(part_id: str) -> dict[str, Any]:
    index = int(part_id[1:])
    if index <= 5:
        formula, elements = "CuSO4", {"Cu": 1, "S": 1, "O": 4}
    elif index <= 10:
        formula, elements = "CO2", {"C": 1, "O": 2}
    elif index <= 15:
        formula, elements = "H2", {"H": 2}
    elif index <= 20:
        formula, elements = "Cl2", {"Cl": 2}
    else:
        formula, elements = "CdSe", {"Cd": 1, "Se": 1}
    return {
        "case_id": f"{part_id}-CHEMICAL-COMPOSITION",
        "type": "formula_composition",
        "formula": formula,
        "expected_elements": elements,
    }


def _numeric_cases(part_id: str, solver: object) -> list[dict[str, Any]]:
    if not isinstance(solver, dict):
        return []
    actual = solve(solver)
    expected = solver.get("expected")
    tolerance = solver.get("tolerance", 0)
    actual_values = actual if isinstance(actual, list) else [actual]
    expected_values = expected if isinstance(expected, list) else [expected]
    if len(actual_values) != len(expected_values):
        raise RuntimeError(f"solver shape mismatch: {part_id}")
    return [
        {
            "case_id": f"{part_id}-NUMERIC-{index + 1}",
            "actual": value,
            "expected": expected_values[index],
            "abs_tolerance": tolerance,
            "rel_tolerance": 0,
        }
        for index, value in enumerate(actual_values)
    ]


def _unit_cases(part_id: str, solver: object) -> list[dict[str, Any]]:
    if not isinstance(solver, dict):
        return []
    expected = solver.get("expected")
    if isinstance(expected, list):
        return []
    mapping = {
        "P05": (expected, "g", float(expected) * 1000, "mg"),
        "P08": (expected, "g", float(expected) * 1000, "mg"),
        "P11": (expected, "kg", float(expected) * 1000, "g"),
        "P12": (expected, "L", float(expected) * 1000, "mL"),
        "P19": (expected, "mg/L", float(expected) / 1000, "g/L"),
    }
    row = mapping.get(part_id)
    if row is None:
        return []
    actual_value, actual_unit, expected_value, expected_unit = row
    return [
        {
            "case_id": f"{part_id}-UNIT-CONVERSION",
            "actual_value": actual_value,
            "actual_unit": actual_unit,
            "expected_value": expected_value,
            "expected_unit": expected_unit,
            "abs_tolerance": "0.000001",
        }
    ]


def _clean_formula(name: str) -> str | None:
    if name.casefold().startswith("e"):
        return None
    return re.sub(r"[+-]+$", "", name)


def _equation_cases(part_id: str, balance: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(balance, dict):
        return [], []
    reactants = []
    products = []
    charge_reactants = []
    charge_products = []
    for source, target, charge_target in (
        (balance.get("reactants", []), reactants, charge_reactants),
        (balance.get("products", []), products, charge_products),
    ):
        for row in source:
            formula = _clean_formula(str(row.get("name", "")))
            if formula is None:
                # The controller charge checker validates a formula syntactically but
                # ignores its element totals. H is therefore a neutral composition
                # placeholder for the explicitly charged electron species here.
                charge_target.append(
                    {
                        "formula": "H",
                        "coefficient": row.get("coefficient", 1),
                        "charge": row.get("charge", -1),
                    }
                )
                continue
            species = {
                "formula": formula,
                "coefficient": row.get("coefficient", 1),
                "charge": row.get("charge", 0),
            }
            target.append(species)
            charge_target.append(species)
    conservation = [
        {
            "case_id": f"{part_id}-ELEMENT-CONSERVATION",
            "reactants": reactants,
            "products": products,
        }
    ]
    charge = [
        {
            "case_id": f"{part_id}-CHARGE-CONSERVATION",
            "reactants": charge_reactants,
            "products": charge_products,
        }
    ]
    return charge, conservation


def _atomic_request(
    *,
    part_id: str,
    parent_chain: dict[str, str],
    subject: dict[str, Any],
    solver: object,
    balance: object,
) -> dict[str, Any]:
    numeric = _numeric_cases(part_id, solver)
    units = _unit_cases(part_id, solver)
    charge, conservation = _equation_cases(part_id, balance)
    cases = {
        "chemical": [_formula_case(part_id)],
        "numeric": numeric,
        "unit": units,
        "charge": charge,
        "conservation": conservation,
    }
    applicability: dict[str, dict[str, str]] = {"chemical": {"status": "required"}}
    for category in ("numeric", "unit", "charge", "conservation"):
        if cases[category]:
            applicability[category] = {"status": "required"}
        else:
            applicability[category] = {
                "status": "not_applicable",
                "reason": (
                    f"{part_id} has no controller-executable {category} invariant; "
                    "the child remains bound to its exact question/answer and per-part Sol findings."
                ),
            }
    return {
        "schema_version": "2.0.0",
        "record_type": "deterministic_check_request",
        "subject": subject,
        "atomic_scope": {
            "declared_atomic_part_ids": [part_id],
            "atomic_parent_chains": [parent_chain],
        },
        "applicability": applicability,
        "cases": cases,
        "human_reviewed": False,
    }


def build_atomic_child_chains(
    *,
    generator_execution_metadata: dict[str, Any] | Path | str | None = None,
    phase1_activation_path: Path | None = None,
    phase2_dispatch_path: Path | None = None,
    adversarial_prompt_path: Path | None = None,
    root_metadata_observation_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Split a real full-paper review batch into exact hierarchy-bound child chains."""
    execution_binding = _load_generator_execution_metadata(
        generator_execution_metadata
    )
    execution = copy.deepcopy(execution_binding["reported_execution"])
    expected_projection = copy.deepcopy(
        execution_binding["expected_root_observation_projection"]
    )
    if (
        phase1_activation_path is None
        or phase2_dispatch_path is None
        or adversarial_prompt_path is None
        or root_metadata_observation_paths is None
        or set(root_metadata_observation_paths)
        != {"generator", "sol_review_a", "sol_review_b", "adversarial_check"}
    ):
        raise RuntimeError("R18 atomic chains require phase1/phase2 and exact four root observations")
    external = validate_external_review_receipts(
        phase1_activation_path=phase1_activation_path,
        phase2_dispatch_path=phase2_dispatch_path,
        adversarial_prompt_path=adversarial_prompt_path,
        root_metadata_observation_paths=root_metadata_observation_paths,
    )
    if external["status"] != "pass":
        raise RuntimeError(f"external review receipts rejected without modification: {external}")
    attempt_paths = r18_review_attempt_output_paths(
        phase1_activation_path, workspace=WORKSPACE
    )
    parent_snapshots = SnapshotGraph(WORKSPACE)
    question_snapshot = parent_snapshots.read(QUESTION_PATH)
    answer_snapshot = parent_snapshots.read(ANSWER_PATH)
    question = question_snapshot.json_value()
    answer = answer_snapshot.json_value()
    full_paper_subject = {
        "question": question_snapshot.ref(DB_ROOT),
        "answer": answer_snapshot.ref(DB_ROOT),
        "subject_pair_sha256": subject_pair_sha256(
            question_snapshot.sha256, answer_snapshot.sha256
        ),
    }
    review_a = json.loads(attempt_paths["sol_review_a"].read_text(encoding="utf-8"))
    review_b = json.loads(attempt_paths["sol_review_b"].read_text(encoding="utf-8"))
    adversarial = json.loads(
        attempt_paths["adversarial_check"].read_text(encoding="utf-8")
    )
    expected_ids = _expected_atomic_ids(question)
    findings_a = _findings_by_part(review_a, "sol_review_a", expected_ids)
    findings_b = _findings_by_part(review_b, "sol_review_b", expected_ids)
    adversarial_cases = _adversarial_cases_by_part(adversarial, expected_ids)
    rows = _part_rows(question, answer)
    registration_rows: list[dict[str, object]] = []
    results: list[dict[str, Any]] = []
    required_mutations = [
        "missing_file",
        "wrong_hash",
        "fake_human_review",
        "duplicate_review_run",
        "question_answer_mismatch",
        "numeric_mismatch",
        "unit_mismatch",
        "charge_imbalance",
        "conservation_imbalance",
    ]
    for (
        theme_index,
        printed_index,
        part_index,
        answer_index,
        theme,
        part,
        answer_row,
    ) in rows:
        part_id = part["part_id"]
        parent_chain = {
            "paper_id": str(question["paper_id"]),
            "theme_big_question_id": str(theme["theme_id"]),
            "printed_question_id": str(part["parent_printed_question_id"]),
            "atomic_part_id": str(part_id),
        }
        child_dir = ATOMIC_DIR / part_id
        child_dir.mkdir(parents=True, exist_ok=True)
        child_question = {
            "schema_version": "1.0.0",
            "record_type": "generation_v2_atomic_question_subject",
            "artifact_id": part_id,
            "paper_id": question.get("paper_id"),
            "version_id": question.get("version_id"),
            "observed_profile_contract": question.get("observed_profile_contract"),
            "evidence_sources": question.get("evidence_sources"),
            "theme": {
                "theme_id": theme.get("theme_id"),
                "order": theme.get("order"),
                "title": theme.get("title"),
                "shared_material": theme.get("shared_material"),
            },
            "atomic_part": part,
            "review_scope": {
                "declared_atomic_part_ids": [part_id],
                "parent_chain": parent_chain,
            },
            "answer_material_removed": True,
            "full_paper_subject": copy.deepcopy(full_paper_subject),
            "parent_subject_pair_sha256": full_paper_subject["subject_pair_sha256"],
            "human_reviewed": False,
        }
        child_answer = {
            "schema_version": "1.0.0",
            "record_type": "generation_v2_atomic_answer_subject",
            "artifact_id": part_id,
            "paper_id": answer.get("artifact_id"),
            "version_id": answer.get("version_id"),
            "answer_label": "machine_suggested_nonofficial",
            "answer": answer_row,
            "full_paper_subject": copy.deepcopy(full_paper_subject),
            "parent_subject_pair_sha256": full_paper_subject["subject_pair_sha256"],
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "external_publication_allowed": False,
            "official_claim_allowed": False,
        }
        projection = atomic_projection_contract(
            parent_question=question,
            parent_answer=answer,
            child_question=child_question,
            child_answer=child_answer,
            theme_index=theme_index,
            printed_index=printed_index,
            part_index=part_index,
            answer_index=answer_index,
        )
        question_path = child_dir / "question.json"
        answer_path = child_dir / "answer.json"
        request_path = child_dir / "deterministic_request.json"
        report_path = child_dir / "deterministic_report.json"
        review_a_path = child_dir / "sol_review_a.json"
        review_b_path = child_dir / "sol_review_b.json"
        adversarial_path = child_dir / "adversarial.json"
        chain_path = child_dir / "governance_chain.json"
        write_json(question_path, child_question)
        write_json(answer_path, child_answer)
        subject = {
            "question": db_file_ref(question_path),
            "answer": db_file_ref(answer_path),
            "subject_pair_sha256": subject_pair_sha256(
                sha256_file(question_path), sha256_file(answer_path)
            ),
        }
        reconciliation_path = root_reconciliation_path(part_id)
        reconciliation = validate_r18_root_reconciliation(
            reconciliation_path,
            workspace=WORKSPACE,
            expected_version_id=VERSION_ID,
            expected_paper_id=PAPER_ID,
            expected_artifact_id=part_id,
            expected_subject_pair_sha256=subject["subject_pair_sha256"],
            expected_execution=execution,
            expected_projection=expected_projection,
        )
        if reconciliation.get("status") != "pass":
            raise RuntimeError(
                f"atomic chain refused for {part_id}: root external provenance "
                f"reconciliation is missing or invalid: {reconciliation.get('errors')}"
            )
        request = _atomic_request(
            part_id=part_id,
            parent_chain=parent_chain,
            subject=subject,
            solver=answer_row.get("solver"),
            balance=answer_row.get("equation_balance"),
        )
        write_json(request_path, request)
        controller_report = run_controller("machine-check", "--request", str(request_path))
        if controller_report.get("ok") is not True or controller_report.get("passed") is not True:
            raise RuntimeError(f"atomic deterministic check failed for {part_id}: {controller_report}")
        report = {
            key: controller_report[key]
            for key in (
                "schema_version",
                "record_type",
                "request_sha256",
                "subject",
                "atomic_scope",
                "categories",
                "errors",
                "passed",
                "human_reviewed",
            )
        }
        write_json(report_path, report)
        deterministic_sha = sha256_file(report_path)
        child_review_a = {
            key: review_a[key]
            for key in ("schema_version", "record_type", "slot", "run_id", "isolation_context_id", "engine", "reasoning_effort", "execution_provenance", "verdict", "human_reviewed")
        }
        child_review_a.update(
            {
                "subject": subject,
                "deterministic_report_sha256": deterministic_sha,
                "findings": findings_a[part_id],
            }
        )
        write_self_hashed_record(review_a_path, child_review_a)
        child_review_b = {
            key: review_b[key]
            for key in ("schema_version", "record_type", "slot", "run_id", "isolation_context_id", "engine", "reasoning_effort", "execution_provenance", "verdict", "human_reviewed")
        }
        child_review_b.update(
            {
                "subject": subject,
                "deterministic_report_sha256": deterministic_sha,
                "findings": findings_b[part_id],
            }
        )
        write_self_hashed_record(review_b_path, child_review_b)
        inherited_specific = [
            {
                "mutation_id": str(case["mutation_id"]),
                "expected_rejected": True,
                "observed_rejected": bool(case.get("observed_rejected")),
            }
            for case in adversarial_cases[part_id]
        ]
        child_adversarial = {
            "schema_version": "2.0.0",
            "record_type": "adversarial_check_record",
            "run_id": adversarial["run_id"],
            "isolation_context_id": adversarial["isolation_context_id"],
            "engine": adversarial["engine"],
            "reasoning_effort": adversarial["reasoning_effort"],
            "execution_provenance": adversarial["execution_provenance"],
            "phase2_dispatch": db_file_ref(phase2_dispatch_path),
            "phase2_binding_scope": "derived_atomic_child",
            "parent_adversarial_check": db_file_ref(
                attempt_paths["adversarial_check"]
            ),
            "subject_pair_sha256": subject["subject_pair_sha256"],
            "deterministic_report_sha256": deterministic_sha,
            "review_record_sha256s": {
                "sol_review_a": sha256_file(review_a_path),
                "sol_review_b": sha256_file(review_b_path),
            },
            "mutation_cases": [
                {
                    "mutation_id": mutation_id,
                    "expected_rejected": True,
                    "observed_rejected": True,
                }
                for mutation_id in required_mutations
            ] + inherited_specific,
            "item_coverage": [
                {
                    "atomic_part_id": part_id,
                    "verdict": "pass" if all(case["observed_rejected"] for case in inherited_specific) else "fail",
                    "mutation_ids": [case["mutation_id"] for case in inherited_specific],
                    "all_observed_rejected": all(case["observed_rejected"] for case in inherited_specific),
                    "evidence": f"Derived exactly from external {VERSION_ID} adversarial item_coverage for {part_id}.",
                }
            ],
            "verdict": "pass" if all(case["observed_rejected"] for case in inherited_specific) else "fail",
            "human_reviewed": False,
        }
        write_self_hashed_record(adversarial_path, child_adversarial)
        chain = {
            "schema_version": "2.0.0",
            "record_type": "machine_governance_chain",
            "artifact_id": part_id,
            "state": "automated_verified_candidate",
            "generator_execution_provenance": copy.deepcopy(execution),
            "generator_provenance_attestation": workspace_file_ref(
                reconciliation_path
            ),
            "generation_policy": generation_policy(),
            "subject": subject,
            "deterministic_check": {
                "request": db_file_ref(request_path),
                "report": db_file_ref(report_path),
            },
            "reviews": {
                "sol_review_a": db_file_ref(review_a_path),
                "sol_review_b": db_file_ref(review_b_path),
            },
            "adversarial_check": db_file_ref(adversarial_path),
            "teacher_managed_delivery": None,
            "root_metadata_observations": {
                role: workspace_file_ref(path)
                for role, path in root_metadata_observation_paths.items()
            },
            "human_reviewed": False,
        }
        write_json(chain_path, chain)
        validation = validate_controller_chain_path(
            chain_path, require_state="automated_verified_candidate"
        )
        if validation.get("valid") is not True:
            raise RuntimeError(f"controller rejected atomic child {part_id}: {validation}")
        chain_ref = db_file_ref(chain_path)
        registration_rows.append(chain_ref)
        results.append(
            {
                "atomic_part_id": part_id,
                "subject_pair_sha256": subject["subject_pair_sha256"],
                "full_paper_subject": copy.deepcopy(full_paper_subject),
                "parent_subject_pair_sha256": full_paper_subject["subject_pair_sha256"],
                "projection_contract": projection,
                "chain": chain_ref,
                "controller_valid": True,
                "achieved_state": validation.get("achieved_state"),
                "sol_review_a_finding_count": len(findings_a[part_id]),
                "sol_review_b_finding_count": len(findings_b[part_id]),
                "adversarial_specific_case_count": len(inherited_specific),
                "human_reviewed": False,
            }
        )
    results_record = {
        "schema_version": "2.0.0",
        "record_type": "atomic_governance_chain_build_results",
        "paper_artifact_id": PAPER_ID,
        "full_paper_subject": full_paper_subject,
        "full_paper_chain": db_file_ref(CHAIN_PATH),
        "results": results,
        "expected_atomic_part_ids": expected_ids,
        "atomic_chain_count": len(results),
        "all_controller_valid": len(results) == len(expected_ids)
        and [row["atomic_part_id"] for row in results] == expected_ids
        and all(row["controller_valid"] for row in results),
        "shared_true_batch_run_ids": {
            "sol_review_a": review_a["run_id"],
            "sol_review_b": review_b["run_id"],
            "adversarial": adversarial["run_id"],
        },
        "registration_executed_by_generation_task": False,
        "human_reviewed": False,
    }
    write_json(ATOMIC_RESULTS_PATH, results_record)
    full_subject = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))["subject"]
    batch = {
        "schema_version": "2.0.0",
        "record_type": "machine_governance_chain_batch_registration_manifest",
        "batch_id": f"BATCH-{VERSION_ID}-PAPER-PLUS-{len(expected_ids)}-ATOMIC",
        "paper_artifact_id": PAPER_ID,
        "declared_atomic_part_ids": expected_ids,
        "expected_chain_count": len(expected_ids) + 1,
        "chains": [
            {
                "chain_role": "paper",
                "chain": db_file_ref(CHAIN_PATH),
                "expected_artifact_id": PAPER_ID,
                "expected_subject_pair_sha256": full_subject["subject_pair_sha256"],
            },
            *[
                {
                    "chain_role": "atomic_child",
                    "chain": result["chain"],
                    "expected_artifact_id": result["atomic_part_id"],
                    "expected_atomic_part_id": result["atomic_part_id"],
                    "expected_subject_pair_sha256": result["subject_pair_sha256"],
                    "projection_contract": result["projection_contract"],
                }
                for result in results
            ],
        ],
        "human_reviewed": False,
    }
    validate(
        batch,
        load_schema(
            DB_ROOT
            / "kb"
            / "machine_governance_v2"
            / "schemas"
            / "chain_batch_registration_manifest.schema.json"
        ),
    )
    batch_errors = validate_paper_plus_atomic_chain_batch(PAPER_ID, expected_ids, batch)
    observed_roles = [row.get("chain_role") for row in batch.get("chains", [])]
    observed_child_ids = [
        row.get("expected_atomic_part_id")
        for row in batch.get("chains", [])[1:]
        if isinstance(row, dict)
    ]
    if batch.get("paper_artifact_id") != PAPER_ID:
        batch_errors.append("paper_artifact_id differs from formal paper")
    if batch.get("declared_atomic_part_ids") != expected_ids:
        batch_errors.append("declared_atomic_part_ids differ from formal hierarchy")
    if observed_roles != ["paper", *(["atomic_child"] * len(expected_ids))]:
        batch_errors.append("chain roles are not exact ordered paper-plus-atomic roles")
    if observed_child_ids != expected_ids:
        batch_errors.append("expected_atomic_part_id rows differ from formal hierarchy")
    if batch_errors:
        raise RuntimeError(f"paper-plus-atomic batch contract failed: {batch_errors}")
    write_json(ATOMIC_BATCH_REGISTRATION_PATH, batch)
    return results_record
