from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .adapter import bind_observed_profile
from .content import FIGURE_ID, VERSION_ID
from .core import sha256_file, write_json
from .governance_bridge import CHAIN_PATH
from .hierarchy import hierarchy_ids, validate_atomic_registry_binding
from .qa import validate_archive
from .schema_validation import load_schema, validate


def student_learning_content_provider_status() -> dict[str, Any]:
    """Expose the generation provider through the Gateway adapter surface."""

    from .content_provider import student_learning_content_provider_status as provider_status

    return provider_status()


def build_student_learning_content(
    *, diagnosis: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Delegate to the fail-closed generation provider without rewrapping output."""

    from .content_provider import build_student_learning_content as provider_build

    return provider_build(diagnosis=diagnosis, plan=plan)


def student_learning_content_provider_adapter() -> Any:
    from .content_provider import StudentLearningContentProviderAdapter

    return StudentLearningContentProviderAdapter()


API_VERSION = "generation-gateway/1.0.0"
WORKSPACE = Path(__file__).resolve().parents[2]
CONTROLLER = WORKSPACE / "sh-chem-db/scripts/sh_chem_agent.py"
DB_ROOT = WORKSPACE / "sh-chem-db"
STAGING = WORKSPACE / "staging/v1_generation"
REVISION_SLUG = VERSION_ID.rsplit("-", 1)[-1].lower()
CURRENT_CANDIDATE_DIR = STAGING / "candidates" / REVISION_SLUG
CURRENT_REPORT_DIR = STAGING / "reports" / REVISION_SLUG
JOBS = STAGING / "jobs"
EXPORTS = WORKSPACE / "exports/v1_demo"
FIGURES = WORKSPACE / "sh-chem-db/kb/figures/machine_v2"
INTEGRATION_SCHEMA_DIR = WORKSPACE / "integrations/shchem_generation_v2/schemas"
DEFAULT_PROFILE = (
    WORKSPACE
    / "sh-chem-db/kb/shanghai_observed_standard_v1/profiles/OSV1-2026-LEVEL-RECALL-5T-CONTINUOUS.json"
)
DEFAULT_PROJECT_AUTHORIZATION = (
    WORKSPACE / "sh-chem-db/kb/machine_governance_v2/project_generation_authorization.json"
)
DEFAULT_DELIVERY_STATUS = EXPORTS / "delivery_status.json"


ERROR_HTTP = {
    "GEN_V2_E_INVALID_REQUEST": 400,
    "GEN_V2_E_JOB_NOT_FOUND": 404,
    "GEN_V2_E_ARTIFACT_NOT_FOUND": 404,
    "GEN_V2_E_PROFILE_UNBOUND": 409,
    "GEN_V2_E_PROFILE_HASH_MISMATCH": 409,
    "GEN_V2_E_CONTROLLER_UNAVAILABLE": 503,
    "GEN_V2_E_CONTROLLER_PREFLIGHT_DENIED": 423,
    "GEN_V2_E_REVIEW_CHAIN_INCOMPLETE": 409,
    "GEN_V2_E_CHAIN_REGISTRATION_PENDING": 409,
    "GEN_V2_E_NOT_AUTOMATED_VERIFIED": 409,
    "GEN_V2_E_PROMOTION_INVALID": 409,
    "GEN_V2_E_ARTIFACT_HASH_MISMATCH": 409,
    "GEN_V2_E_INTERNAL": 500,
}


def gateway_contract() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "transport": "Python functions or JSON-on-stdout CLI",
        "operations": {
            "generation_job.build": {
                "python": "build_generation_job(request, profile_path, governance_chain)",
                "cli": "--profile PROFILE --governance-chain CHAIN job-build --request REQUEST.json",
                "controller_preflight": [],
                "semantics": "stages the job first; no empty/self-preflight is performed",
                "mutating": True,
            },
            "generation_job.query": {
                "python": "query_generation_job(job_id)",
                "cli": "job-query --job-id JOB_ID",
                "controller_preflight": [],
                "mutating": False,
            },
            "machine_qa.status": {
                "python": "get_machine_qa_status(job_id=None)",
                "cli": "qa-status [--job-id JOB_ID]",
                "controller_preflight": [],
                "mutating": False,
            },
            "figure.get": {
                "python": "get_figure(figure_id, format_name, download, profile_path, governance_chain)",
                "cli": "figure-get --figure-id ID --format svg|png|spec [--download]",
                "controller_preflight": ["figure when download=true"],
                "mutating": False,
            },
            "candidate_zip.build": {
                "python": "build_candidate_zip(profile_path, governance_chain)",
                "cli": "--profile PROFILE --governance-chain CHAIN zip-build",
                "controller_preflight": ["post-generation generate, inside atomic finalizer"],
                "mutating": True,
            },
            "candidate_zip.download": {
                "python": "get_candidate_zip(profile_path, governance_chain)",
                "cli": "--profile PROFILE --governance-chain CHAIN zip-download",
                "controller_preflight": ["live post-generation generate recheck"],
                "mutating": False,
            },
            "student_learning.provider.status": {
                "python": "student_learning_content_provider_status()",
                "cli": None,
                "controller_preflight": [],
                "semantics": "live readiness is false until exact paper+atomic registration, root reconciliation, generation delivery promotion, and the eight-document builder are all ready",
                "mutating": False,
            },
            "student_learning.content.build": {
                "python": "build_student_learning_content(diagnosis=..., plan=...)",
                "cli": None,
                "controller_preflight": ["inherited exact registered content and external delivery gates"],
                "semantics": "returns the student domain contract directly; the student Gateway builds and downloads its final ZIP without a generation-domain wrapper",
                "mutating": True,
            },
        },
        "response_envelope": {
            "api_version": "string",
            "ok": "boolean",
            "operation": "string",
            "http_status": "integer",
            "data": "object|null",
            "error": {"code": "string", "message": "string", "details": "any"},
        },
        "error_codes": [
            {"code": code, "http_status": status}
            for code, status in sorted(ERROR_HTTP.items())
        ],
        "security": {
            "real_action_requires_controller_preflight": True,
            "real_action_requires_exact_profile_hash_binding": True,
            "provisional_profile_allowed": False,
            "human_review_claim_allowed": False,
            "central_registry_write_allowed": False,
            "post_generation_preflight_required_before_promotion": True,
            "qa_and_download_revalidate_promotion_manifest_zip": True,
        },
    }


def _ok(operation: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "ok": True,
        "operation": operation,
        "http_status": 200,
        "data": data,
        "error": None,
    }


def _error(operation: str, code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "ok": False,
        "operation": operation,
        "http_status": ERROR_HTTP[code],
        "data": None,
        "error": {"code": code, "message": message, "details": details},
    }


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_workspace_ref(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("workspace-relative path required")
    raw = Path(value)
    if raw.is_absolute() or raw.drive:
        raise ValueError(f"absolute path prohibited: {value}")
    resolved = (WORKSPACE / raw).resolve()
    try:
        resolved.relative_to(WORKSPACE.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {value}") from exc
    return resolved


def _validate_file_ref(
    ref: object,
    *,
    label: str,
    errors: list[str],
    require_json_pass: bool = False,
) -> Path | None:
    if not isinstance(ref, dict):
        errors.append(f"{label}:invalid_file_ref")
        return None
    try:
        path = _resolve_workspace_ref(ref.get("path"))
    except Exception as exc:
        errors.append(f"{label}:invalid_path:{exc}")
        return None
    if not path.is_file():
        errors.append(f"{label}:missing")
        return path
    if sha256_file(path) != ref.get("sha256"):
        errors.append(f"{label}:hash_mismatch")
    if path.stat().st_size != ref.get("bytes"):
        errors.append(f"{label}:byte_count_mismatch")
    if require_json_pass:
        try:
            if _load(path).get("status") != "pass":
                errors.append(f"{label}:report_not_pass")
        except Exception as exc:
            errors.append(f"{label}:report_json_invalid:{exc}")
    return path


def _atomic_ids_from_paper(paper: dict[str, Any]) -> list[str]:
    return hierarchy_ids(paper)["atomic_part_ids"]


def _profile_binding(profile_path: Path) -> tuple[dict | None, dict | None]:
    operation = "profile_binding"
    paper_path = CURRENT_CANDIDATE_DIR / "frozen_paper.json"
    if not paper_path.is_file():
        return None, _error(operation, "GEN_V2_E_PROFILE_UNBOUND", "frozen paper is missing")
    try:
        bound = bind_observed_profile(WORKSPACE, profile_path=profile_path.resolve())
        paper = _load(paper_path)
        contract = paper.get("observed_profile_contract", {})
        expected = {
            "profile_id": bound.data["profile_id"],
            "profile_version": bound.data["profile_version"],
            "profile_sha256": bound.sha256,
        }
        mismatches = {
            name: {"frozen": contract.get(name), "requested": value}
            for name, value in expected.items()
            if contract.get(name) != value
        }
        evidence = contract.get("evidence_manifest", {})
        if evidence.get("sha256") != bound.evidence_manifest_sha256:
            mismatches["evidence_manifest.sha256"] = {
                "frozen": evidence.get("sha256"),
                "requested": bound.evidence_manifest_sha256,
            }
        if contract.get("provisional_fixture") is not False:
            mismatches["provisional_fixture"] = {
                "frozen": contract.get("provisional_fixture"),
                "required": False,
            }
        if mismatches:
            return None, _error(
                operation,
                "GEN_V2_E_PROFILE_HASH_MISMATCH",
                "requested observed profile does not match the frozen candidate",
                mismatches,
            )
        return {
            "profile_id": bound.data["profile_id"],
            "profile_version": bound.data["profile_version"],
            "profile_path": str(bound.path),
            "profile_sha256": bound.sha256,
            "evidence_manifest_path": str(bound.evidence_manifest_path),
            "evidence_manifest_sha256": bound.evidence_manifest_sha256,
            "field_bindings": contract.get("field_bindings"),
            "exact_source_only": bound.data.get("applicability", {}).get("exact_source_only"),
            "global_template_claim_allowed": False,
            "official_claim_allowed": False,
            "profile_usage": "read_only_structure_observation",
            "profile_generation_authority_claimed": False,
            "original_question_generation": True,
            "source_question_republication": False,
            "profile_gates": {
                "question_retrieval_allowed": bound.data["gates"]["question_retrieval_allowed"],
                "unattended_generation_allowed": bound.data["gates"]["unattended_generation_allowed"],
            },
        }, None
    except Exception as exc:
        return None, _error(operation, "GEN_V2_E_PROFILE_UNBOUND", str(exc))


def controller_preflight(
    mode: str,
    *,
    profile_path: Path,
    governance_chain: Path | None,
    project_authorization: Path = DEFAULT_PROJECT_AUTHORIZATION,
) -> dict[str, Any]:
    if not CONTROLLER.is_file():
        return {
            "available": False,
            "exit_code": None,
            "json": None,
            "stderr": "controller entry point missing",
        }
    configured_python = __import__("os").environ.get("SHCHEM_CONTROLLER_PYTHON")
    if configured_python:
        controller_python = configured_python
    elif importlib.util.find_spec("jsonschema") is not None:
        controller_python = sys.executable
    else:
        controller_python = shutil.which("python") or sys.executable
    command = [
        controller_python,
        str(CONTROLLER),
        "preflight",
        "--root",
        str(DB_ROOT),
        "--mode",
        mode,
        "--profile",
        str(profile_path.resolve()),
        "--project-authorization",
        str(project_authorization.resolve()),
    ]
    if governance_chain is not None:
        command.extend(["--governance-chain", str(governance_chain.resolve())])
    completed = subprocess.run(
        command,
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    return {
        "available": True,
        "exit_code": completed.returncode,
        "json": payload,
        "stderr": completed.stderr.strip(),
    }


def _require_preflight(
    operation: str,
    modes: list[str],
    *,
    profile_path: Path,
    governance_chain: Path | None,
    runner: Callable[..., dict[str, Any]] = controller_preflight,
) -> tuple[list[dict[str, Any]] | None, dict | None]:
    receipts = []
    for mode in modes:
        result = runner(mode, profile_path=profile_path, governance_chain=governance_chain)
        if not result.get("available"):
            return None, _error(
                operation,
                "GEN_V2_E_CONTROLLER_UNAVAILABLE",
                "controller preflight is unavailable",
                result,
            )
        payload = result.get("json") if isinstance(result.get("json"), dict) else {}
        if result.get("exit_code") != 0 or payload.get("ready") is not True or payload.get("ok") is not True:
            return None, _error(
                operation,
                "GEN_V2_E_CONTROLLER_PREFLIGHT_DENIED",
                f"controller denied {mode} preflight",
                {
                    "mode": mode,
                    "exit_code": result.get("exit_code"),
                    "blockers": payload.get("blockers", []),
                    "checks": payload.get("checks", {}),
                    "stderr": result.get("stderr"),
                },
            )
        receipts.append(
            {
                "mode": mode,
                "controller_version": payload.get("controller_version"),
                "contract_version": payload.get("contract_version"),
                "ready": True,
            }
        )
    return receipts, None


def build_generation_job(
    request: dict[str, Any],
    *,
    profile_path: Path = DEFAULT_PROFILE,
    governance_chain: Path | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = controller_preflight,
) -> dict[str, Any]:
    operation = "generation_job.build"
    if not isinstance(request, dict) or request.get("intent") not in {"demo_exam", "demo_weekpack"}:
        return _error(operation, "GEN_V2_E_INVALID_REQUEST", "intent must be demo_exam or demo_weekpack")
    binding, binding_error = _profile_binding(profile_path)
    if binding_error:
        binding_error["operation"] = operation
        return binding_error
    job_id = f"GENJOB-{uuid.uuid4().hex[:16].upper()}"
    record = {
        "schema_version": "1.0.0",
        "job_id": job_id,
        "intent": request["intent"],
        "request": request,
        "status": "staging_candidate_created_awaiting_controller_native_chain",
        "profile_binding": binding,
        "controller_preflights": [],
        "empty_or_pre_generation_preflight_performed": False,
        "next_required": [
            "controller_native_chain",
            "controller_owner_registration",
            "post_generation_generate_preflight",
            "atomic_promotion",
        ],
        "human_reviewed": False,
        "publication_allowed": False,
    }
    JOBS.mkdir(parents=True, exist_ok=True)
    write_json(JOBS / f"{job_id}.json", record)
    return _ok(operation, record)


def query_generation_job(job_id: str) -> dict[str, Any]:
    operation = "generation_job.query"
    path = JOBS / f"{job_id}.json"
    if not path.is_file():
        return _error(operation, "GEN_V2_E_JOB_NOT_FOUND", f"unknown generation job: {job_id}")
    return _ok(operation, _load(path))


def _validated_delivery_bundle(
    *,
    delivery_status_path: Path = DEFAULT_DELIVERY_STATUS,
    preflight_runner: Callable[..., dict[str, Any]] = controller_preflight,
    require_live_preflight: bool = True,
) -> dict[str, Any]:
    """Validate an external delivery attestation without trusting ZIP self-claims.

    The ZIP-internal manifest is content-only and must remain
    ``false/null/false`` for teacher delivery.  Only the external promotion and
    delivery-status sidecars may become a teacher-managed private-delivery
    candidate, after exact archive, manifest, QA, registry and controller checks.
    """

    errors: list[str] = []
    integrity: dict[str, Any] = {"status": "not_run", "errors": []}
    live_preflight: dict[str, Any] = {"available": False, "skipped": True}
    if not delivery_status_path.is_file():
        return {
            "valid": False,
            "teacher_managed_delivery_candidate": False,
            "errors": ["external_delivery_status_missing"],
        }
    try:
        delivery_status = _load(delivery_status_path)
        validate(
            delivery_status,
            load_schema(INTEGRATION_SCHEMA_DIR / "delivery_status.schema.json"),
        )
        promotion_path = _resolve_workspace_ref(delivery_status["promotion_path"])
        promotion = _load(promotion_path)
        validate(
            promotion,
            load_schema(INTEGRATION_SCHEMA_DIR / "automated_promotion.schema.json"),
        )
        manifest_path = _resolve_workspace_ref(promotion["manifest_path"])
        archive_path = _resolve_workspace_ref(promotion["archive_path"])
        manifest = _load(manifest_path)
        validate(
            manifest,
            load_schema(INTEGRATION_SCHEMA_DIR / "weekpack_manifest.schema.json"),
        )
    except Exception as exc:
        return {
            "valid": False,
            "teacher_managed_delivery_candidate": False,
            "errors": [f"schema_or_json:{exc}"],
        }

    def check_path_hash(label: str, value: object, expected_hash: object) -> Path | None:
        try:
            path = _resolve_workspace_ref(value)
        except Exception as exc:
            errors.append(f"{label}:invalid_path:{exc}")
            return None
        if not path.is_file():
            errors.append(f"{label}:missing")
        elif sha256_file(path) != expected_hash:
            errors.append(f"{label}:hash_mismatch")
        return path

    if sha256_file(promotion_path) != delivery_status.get("promotion_sha256"):
        errors.append("delivery_status:promotion_hash_mismatch")
    if promotion_path.stat().st_size != delivery_status.get("promotion_bytes"):
        errors.append("delivery_status:promotion_byte_count_mismatch")
    if delivery_status.get("manifest_path") != promotion.get("manifest_path"):
        errors.append("delivery_status:manifest_path_mismatch")
    if delivery_status.get("manifest_sha256") != promotion.get("manifest_sha256"):
        errors.append("delivery_status:manifest_hash_mismatch")
    if delivery_status.get("archive_path") != promotion.get("archive_path"):
        errors.append("delivery_status:archive_path_mismatch")
    if delivery_status.get("archive_sha256") != promotion.get("archive_sha256"):
        errors.append("delivery_status:archive_hash_mismatch")
    if delivery_status.get("archive_bytes") != promotion.get("archive_bytes"):
        errors.append("delivery_status:archive_byte_count_mismatch")
    if delivery_status.get("candidate_sha256") != promotion.get("candidate_sha256"):
        errors.append("delivery_status:candidate_hash_mismatch")
    if delivery_status.get("task_card_sha256") != promotion.get("task_card_sha256"):
        errors.append("delivery_status:task_hash_mismatch")
    if delivery_status.get("content_governance_sha256") != _canonical_hash(
        promotion.get("content_governance")
    ):
        errors.append("delivery_status:content_governance_hash_mismatch")

    if not manifest_path.is_file() or sha256_file(manifest_path) != promotion.get("manifest_sha256"):
        errors.append("promotion:manifest_missing_or_hash_mismatch")
    if not archive_path.is_file() or sha256_file(archive_path) != promotion.get("archive_sha256"):
        errors.append("promotion:archive_missing_or_hash_mismatch")
    elif archive_path.stat().st_size != promotion.get("archive_bytes"):
        errors.append("promotion:archive_byte_count_mismatch")

    expected_atomic_ids: list[str] = []
    candidate_path = check_path_hash(
        "promotion:candidate", promotion.get("candidate_path"), promotion.get("candidate_sha256")
    )
    task_path: Path | None = None
    if candidate_path is not None:
        try:
            candidate = _load(candidate_path)
            expected_atomic_ids = _atomic_ids_from_paper(candidate)
            raw_task_path = candidate_path.parent / "task_card.json"
            if raw_task_path.is_file() and sha256_file(raw_task_path) == promotion.get("task_card_sha256"):
                task_path = raw_task_path
            else:
                errors.append("promotion:task_card_missing_or_hash_mismatch")
            if candidate.get("task_card", {}).get("version_id") != candidate.get("version_id"):
                errors.append("promotion:candidate_revision_binding_mismatch")
        except Exception as exc:
            errors.append(f"promotion:candidate_json_invalid:{exc}")

    internal_boundaries = {
        "teacher_managed_delivery_candidate": False,
        "delivery_scope": None,
        "teacher_action_required": False,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "publication_allowed": False,
    }
    external_boundaries = {
        "teacher_managed_delivery_candidate": True,
        "delivery_scope": "teacher_managed_private_delivery",
        "teacher_action_required": True,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "publication_allowed": False,
    }
    for key, expected in internal_boundaries.items():
        if manifest.get(key) != expected:
            errors.append(f"manifest_content_only_boundary_mismatch:{key}")
    for label, record in (("promotion", promotion), ("delivery_status", delivery_status)):
        for key, expected in external_boundaries.items():
            if record.get(key) != expected:
                errors.append(f"{label}_external_boundary_mismatch:{key}")

    attestation = manifest.get("external_delivery_attestation", {})
    if (
        attestation.get("path") != delivery_status.get("promotion_path")
        or attestation.get("status") != "pending"
        or attestation.get("included_in_archive") is not False
    ):
        errors.append("manifest:external_attestation_pending_boundary_mismatch")
    if promotion.get("attestation_included_in_bound_archive") is not False:
        errors.append("promotion:self_inclusion_prohibited")

    governance = promotion.get("content_governance", {})
    chain_path = check_path_hash(
        "content_governance:chain",
        governance.get("controller_governance_chain_path"),
        governance.get("controller_governance_chain_sha256"),
    )
    preflight_path = check_path_hash(
        "content_governance:post_generation_preflight",
        governance.get("post_generation_preflight_report_path"),
        governance.get("post_generation_preflight_report_sha256"),
    )
    registry_binding = governance.get("registry_binding", {})
    registry_path = check_path_hash(
        "content_governance:registry",
        registry_binding.get("registry_path"),
        registry_binding.get("registry_sha256"),
    )
    if chain_path is not None and chain_path.is_file():
        try:
            chain = _load(chain_path)
            if chain.get("teacher_managed_delivery") is not None:
                errors.append("content_governance:teacher_delivery_must_be_null")
            if chain.get("human_reviewed") is not False:
                errors.append("content_governance:human_review_overclaim")
        except Exception as exc:
            errors.append(f"content_governance:chain_json_invalid:{exc}")
    if preflight_path is not None and preflight_path.is_file():
        try:
            preflight = _load(preflight_path)
            if preflight.get("status") != "pass":
                errors.append("content_governance:post_generation_preflight_not_pass")
            if chain_path is not None and (
                preflight.get("chain_path") != governance.get("controller_governance_chain_path")
                or preflight.get("chain_sha256") != governance.get("controller_governance_chain_sha256")
            ):
                errors.append("content_governance:preflight_chain_binding_mismatch")
        except Exception as exc:
            errors.append(f"content_governance:preflight_json_invalid:{exc}")

    registry_rows: list[dict[str, Any]] = []
    if registry_path is not None and registry_path.is_file():
        try:
            registry = _load(registry_path)
            registry_rows = registry.get("chains", [])
            if not isinstance(registry_rows, list):
                errors.append("content_governance:registry_rows_invalid")
                registry_rows = []
        except Exception as exc:
            errors.append(f"content_governance:registry_json_invalid:{exc}")
    paper_binding = registry_binding.get("paper", {})
    atomic_bindings = registry_binding.get("atomic", [])
    if registry_binding.get("paper_count") != 1:
        errors.append("content_governance:paper_count_mismatch")
    expected_atomic_count = len(expected_atomic_ids)
    errors.extend(
        f"content_governance:{message}"
        for message in validate_atomic_registry_binding(expected_atomic_ids, registry_binding)
    )
    if registry_binding.get("all_exact_members") is not True:
        errors.append("content_governance:all_exact_members_false")
    all_bindings = [
        ("paper", paper_binding),
        *[(f"atomic:{row.get('artifact_id')}", row) for row in atomic_bindings],
    ]
    for label, binding in all_bindings:
        row = binding.get("registry_row") if isinstance(binding, dict) else None
        if not isinstance(row, dict) or registry_rows.count(row) != 1:
            errors.append(f"content_governance:{label}:registry_membership_mismatch")
            continue
        if binding.get("exact_registry_member") is not True:
            errors.append(f"content_governance:{label}:exact_member_flag_false")
        _validate_file_ref(row, label=f"content_governance:{label}:chain_row", errors=errors)
    if chain_path is not None and isinstance(paper_binding, dict):
        row = paper_binding.get("registry_row", {})
        if row.get("path") != governance.get("controller_governance_chain_path") or row.get(
            "sha256"
        ) != governance.get("controller_governance_chain_sha256"):
            errors.append("content_governance:paper_chain_registry_binding_mismatch")

    manifest_chain = manifest.get("machine_review_chain", {})
    cross_checks = {
        "controller_chain_path": governance.get("controller_governance_chain_path"),
        "controller_chain_sha256": governance.get("controller_governance_chain_sha256"),
        "controller_registry_path": registry_binding.get("registry_path"),
        "controller_registry_sha256": registry_binding.get("registry_sha256"),
        "post_generation_preflight": governance.get("post_generation_preflight_report_path"),
    }
    for key, expected in cross_checks.items():
        if manifest_chain.get(key) != expected:
            errors.append(f"manifest:content_governance_binding_mismatch:{key}")
    if (
        manifest_chain.get("registered") is not True
        or manifest_chain.get("expected_atomic_part_ids") != expected_atomic_ids
        or manifest_chain.get("registered_atomic_chain_count") != expected_atomic_count
        or manifest_chain.get("status") != "pass"
        or manifest_chain.get("teacher_managed_delivery") is not None
    ):
        errors.append("manifest:content_governance_state_mismatch")

    semantic_kinds = [row.get("semantic_kind") for row in promotion.get("semantic_pairs", [])]
    if sorted(semantic_kinds) != sorted(
        ["exam_student", "exam_solutions", "week_student", "week_solutions"]
    ):
        errors.append("promotion:semantic_pair_set_mismatch")
    for row in promotion.get("semantic_pairs", []):
        kind = row.get("semantic_kind")
        _validate_file_ref(row.get("docx"), label=f"semantic_pair:{kind}:docx", errors=errors)
        _validate_file_ref(row.get("pdf"), label=f"semantic_pair:{kind}:pdf", errors=errors)
        _validate_file_ref(
            row.get("parity_report"),
            label=f"semantic_pair:{kind}:parity",
            errors=errors,
            require_json_pass=True,
        )
    for index, report in enumerate(promotion.get("qa_evidence", [])):
        _validate_file_ref(
            report,
            label=f"promotion:qa_evidence:{index}",
            errors=errors,
            require_json_pass=True,
        )
    for index, report in enumerate(manifest.get("qa_reports", [])):
        if report.get("status") != "pass":
            errors.append(f"manifest:qa_report:{index}:status_not_pass")
        _validate_file_ref(
            report,
            label=f"manifest:qa_report:{index}",
            errors=errors,
            require_json_pass=True,
        )
    artifacts = manifest.get("artifacts", [])
    qa_reports = manifest.get("qa_reports", [])
    if manifest.get("artifact_count") != len(artifacts):
        errors.append("manifest:artifact_count_mismatch")
    if manifest.get("qa_report_count") != len(qa_reports):
        errors.append("manifest:qa_report_count_mismatch")
    if manifest.get("demonstration_scope", {}).get("catalog_item_count") != expected_atomic_count:
        errors.append("manifest:catalog_atomic_count_mismatch")
    artifact_source_paths = [row.get("path") for row in artifacts if isinstance(row, dict)]
    required_chain_paths = [
        paper_binding.get("registry_row", {}).get("path"),
        *[
            row.get("registry_row", {}).get("path")
            for row in atomic_bindings
            if isinstance(row, dict)
        ],
    ]
    if any(artifact_source_paths.count(path) != 1 for path in required_chain_paths):
        errors.append("manifest:paper_plus_atomic_chain_artifact_set_mismatch")
    for index, artifact in enumerate(artifacts):
        _validate_file_ref(artifact, label=f"manifest:artifact:{index}", errors=errors)

    zip_report_path = check_path_hash(
        "promotion:zip_integrity_report",
        promotion.get("zip_integrity_report_path"),
        promotion.get("zip_integrity_report_sha256"),
    )
    if zip_report_path is not None and zip_report_path.is_file():
        try:
            if _load(zip_report_path).get("status") != "pass":
                errors.append("promotion:zip_integrity_report_not_pass")
        except Exception as exc:
            errors.append(f"promotion:zip_integrity_report_invalid:{exc}")
    if archive_path.is_file():
        try:
            integrity = validate_archive(archive_path, manifest)
            if integrity.get("status") != "pass":
                errors.extend(f"archive:{error}" for error in integrity.get("errors", []))
            with zipfile.ZipFile(archive_path) as archive:
                forbidden_names = {
                    "automated_promotion.json",
                    "delivery_status.json",
                    Path(delivery_status.get("promotion_path", "")).name,
                    delivery_status_path.name,
                }
                if any(Path(name).name in forbidden_names for name in archive.namelist()):
                    errors.append("archive:external_attestation_or_status_self_included")
        except Exception as exc:
            errors.append(f"archive:validation_exception:{exc}")

    if require_live_preflight and chain_path is not None and chain_path.is_file():
        profile_path: Path | None = None
        observed = manifest.get("observed_profile", {})
        profile_path = check_path_hash(
            "manifest:observed_profile",
            observed.get("profile_path"),
            observed.get("profile_sha256"),
        )
        if profile_path is not None:
            live_preflight = preflight_runner(
                "generate", profile_path=profile_path, governance_chain=chain_path
            )
            live_payload = (
                live_preflight.get("json")
                if isinstance(live_preflight.get("json"), dict)
                else {}
            )
            if (
                live_preflight.get("available") is not True
                or live_preflight.get("exit_code") != 0
                or live_payload.get("ok") is not True
                or live_payload.get("ready") is not True
            ):
                errors.append("live_post_generation_controller_preflight_denied")
    elif require_live_preflight:
        errors.append("live_post_generation_controller_preflight_unavailable")

    return {
        "valid": not errors,
        "teacher_managed_delivery_candidate": not errors,
        "delivery_scope": "teacher_managed_private_delivery" if not errors else None,
        "delivery_status_path": str(delivery_status_path.resolve()),
        "delivery_status_sha256": sha256_file(delivery_status_path),
        "promotion_path": str(promotion_path.resolve()),
        "promotion_sha256": sha256_file(promotion_path),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "archive_path": str(archive_path.resolve()),
        "archive_sha256": sha256_file(archive_path) if archive_path.is_file() else None,
        "archive_integrity": integrity,
        "content_governance_sha256": _canonical_hash(governance),
        "live_post_generation_controller_preflight": live_preflight,
        "errors": errors,
    }


def get_machine_qa_status(job_id: str | None = None) -> dict[str, Any]:
    operation = "machine_qa.status"
    if job_id is not None:
        job = query_generation_job(job_id)
        if not job["ok"]:
            job["operation"] = operation
            return job
    report_dir = CURRENT_REPORT_DIR
    reports = []
    for path in sorted(report_dir.glob("*.json")):
        value = _load(path)
        reports.append(
            {
                "name": path.name,
                "status": value.get("status"),
                "sha256": sha256_file(path),
            }
        )
    bundle = _validated_delivery_bundle()
    state = "automated_verified_candidate" if bundle["valid"] else "machine_pass"
    return _ok(
        operation,
        {
            "job_id": job_id,
            "machine_state": state,
            "teacher_managed_delivery_candidate": bundle["valid"],
            "delivery_scope": "teacher_managed_private_delivery" if bundle["valid"] else None,
            "teaching_use_allowed": False,
            "delivery_bundle_revalidation": bundle,
            "reports": reports,
            "human_reviewed": False,
            "publication_allowed": False,
        },
    )


def get_figure(
    figure_id: str,
    *,
    format_name: str = "svg",
    download: bool = False,
    profile_path: Path = DEFAULT_PROFILE,
    governance_chain: Path | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = controller_preflight,
) -> dict[str, Any]:
    operation = "figure.get"
    if figure_id != FIGURE_ID or format_name not in {"svg", "png", "spec"}:
        return _error(operation, "GEN_V2_E_ARTIFACT_NOT_FOUND", "unknown figure or format")
    binding, binding_error = _profile_binding(profile_path)
    if binding_error:
        binding_error["operation"] = operation
        return binding_error
    path = (
        FIGURES / f"assets/{figure_id}.{format_name}"
        if format_name in {"svg", "png"}
        else FIGURES / f"{figure_id}.spec.json"
    )
    if not path.is_file():
        return _error(operation, "GEN_V2_E_ARTIFACT_NOT_FOUND", str(path))
    preflights = []
    if download:
        preflights, preflight_error = _require_preflight(
            operation,
            ["figure"],
            profile_path=profile_path,
            governance_chain=governance_chain,
            runner=preflight_runner,
        )
        if preflight_error:
            return preflight_error
    return _ok(
        operation,
        {
            "figure_id": figure_id,
            "format": format_name,
            "download_authorized": download,
            "path": str(path.resolve()) if download else None,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "profile_binding": binding,
            "controller_preflights": preflights,
        },
    )


def build_candidate_zip(
    *,
    profile_path: Path = DEFAULT_PROFILE,
    governance_chain: Path | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = controller_preflight,
) -> dict[str, Any]:
    operation = "candidate_zip.build"
    binding, binding_error = _profile_binding(profile_path)
    if binding_error:
        binding_error["operation"] = operation
        return binding_error
    if governance_chain is None:
        return _error(
            operation,
            "GEN_V2_E_REVIEW_CHAIN_INCOMPLETE",
            "a controller-native post-generation governance chain is required",
        )
    if governance_chain.resolve() != CHAIN_PATH.resolve():
        return _error(
            operation,
            "GEN_V2_E_REVIEW_CHAIN_INCOMPLETE",
            "the requested governance chain does not match the current frozen paper chain",
        )
    completed = subprocess.run(
        [sys.executable, "-m", "integrations.shchem_generation_v2.build_demo", "finalize"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if completed.returncode != 0:
        if "RegistrationPending" in completed.stderr or "has not registered" in completed.stderr:
            return _error(
                operation,
                "GEN_V2_E_CHAIN_REGISTRATION_PENDING",
                "controller owner has not registered the frozen chain",
                completed.stderr[-4000:],
            )
        return _error(operation, "GEN_V2_E_INTERNAL", "candidate ZIP build failed", completed.stderr[-4000:])
    bundle = _validated_delivery_bundle()
    if not bundle["valid"]:
        return _error(
            operation,
            "GEN_V2_E_PROMOTION_INVALID",
            "atomic promotion bundle revalidation failed",
            bundle,
        )
    archive = Path(bundle["archive_path"])
    manifest = Path(bundle["manifest_path"])
    if not archive.is_file() or not manifest.is_file():
        return _error(operation, "GEN_V2_E_ARTIFACT_NOT_FOUND", "validated sidecar paths are missing")
    return _ok(
        operation,
        {
            "content_status": "automated_verified_candidate",
            "teacher_managed_delivery_candidate": True,
            "delivery_scope": "teacher_managed_private_delivery",
            "teacher_action_required": True,
            "archive_path": str(archive.resolve()),
            "archive_sha256": sha256_file(archive),
            "manifest_path": str(manifest.resolve()),
            "manifest_sha256": sha256_file(manifest),
            "profile_binding": binding,
            "controller_preflights": [bundle["live_post_generation_controller_preflight"]],
            "delivery_bundle_revalidation": bundle,
            "human_reviewed": False,
            "publication_allowed": False,
        },
    )


def get_candidate_zip(
    *,
    profile_path: Path = DEFAULT_PROFILE,
    governance_chain: Path | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = controller_preflight,
) -> dict[str, Any]:
    operation = "candidate_zip.download"
    binding, binding_error = _profile_binding(profile_path)
    if binding_error:
        binding_error["operation"] = operation
        return binding_error
    if governance_chain is None or governance_chain.resolve() != CHAIN_PATH.resolve():
        return _error(
            operation,
            "GEN_V2_E_REVIEW_CHAIN_INCOMPLETE",
            "the current frozen paper governance chain is required for download",
        )
    preflights, preflight_error = _require_preflight(
        operation,
        ["generate"],
        profile_path=profile_path,
        governance_chain=governance_chain,
        runner=preflight_runner,
    )
    if preflight_error:
        return preflight_error
    bundle = _validated_delivery_bundle()
    if not bundle["valid"]:
        return _error(operation, "GEN_V2_E_PROMOTION_INVALID", "promotion/manifest/ZIP revalidation failed", bundle)
    archive = Path(bundle["archive_path"])
    manifest_path = Path(bundle["manifest_path"])
    if not archive.is_file() or not manifest_path.is_file():
        return _error(operation, "GEN_V2_E_ARTIFACT_NOT_FOUND", "validated candidate ZIP is missing")
    manifest = _load(manifest_path)
    if (
        manifest.get("teacher_managed_delivery_candidate") is not False
        or manifest.get("delivery_scope") is not None
        or manifest.get("teacher_action_required") is not False
    ):
        return _error(operation, "GEN_V2_E_PROMOTION_INVALID", "content-only manifest has a delivery overclaim")
    return _ok(
        operation,
        {
            "download_path": str(archive.resolve()),
            "content_type": "application/zip",
            "bytes": archive.stat().st_size,
            "sha256": sha256_file(archive),
            "profile_binding": binding,
            "controller_preflights": preflights,
            "delivery_bundle_revalidation": bundle,
            "teacher_managed_delivery_candidate": True,
            "delivery_scope": "teacher_managed_private_delivery",
            "teacher_action_required": True,
            "human_reviewed": False,
            "publication_allowed": False,
        },
    )


def _path(value: str | None) -> Path | None:
    return Path(value).resolve() if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed generation/publication v2 Gateway adapter")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--governance-chain")
    sub = parser.add_subparsers(dest="command", required=True)
    job_build = sub.add_parser("job-build")
    job_build.add_argument("--request", required=True)
    job_query = sub.add_parser("job-query")
    job_query.add_argument("--job-id", required=True)
    qa_status = sub.add_parser("qa-status")
    qa_status.add_argument("--job-id")
    figure_get = sub.add_parser("figure-get")
    figure_get.add_argument("--figure-id", required=True)
    figure_get.add_argument("--format", choices=("svg", "png", "spec"), default="svg")
    figure_get.add_argument("--download", action="store_true")
    sub.add_parser("zip-build")
    sub.add_parser("zip-download")
    args = parser.parse_args()
    profile = Path(args.profile).resolve()
    chain = _path(args.governance_chain)
    if args.command == "job-build":
        result = build_generation_job(_load(Path(args.request)), profile_path=profile, governance_chain=chain)
    elif args.command == "job-query":
        result = query_generation_job(args.job_id)
    elif args.command == "qa-status":
        result = get_machine_qa_status(args.job_id)
    elif args.command == "figure-get":
        result = get_figure(
            args.figure_id,
            format_name=args.format,
            download=args.download,
            profile_path=profile,
            governance_chain=chain,
        )
    elif args.command == "zip-build":
        result = build_candidate_zip(profile_path=profile, governance_chain=chain)
    else:
        result = get_candidate_zip(profile_path=profile, governance_chain=chain)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
