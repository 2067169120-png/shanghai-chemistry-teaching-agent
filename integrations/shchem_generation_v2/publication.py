"""Two-layer, fail-closed content governance and delivery assembly.

Layer A proves the paper and every atomic content unit only. Its controller chains never
contain a teacher-delivery record.  Layer B builds and verifies delivery files,
then writes an external attestation sidecar that is deliberately excluded from
the ZIP it binds.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .content import COMPONENT_REGISTRY_FILENAME, FIGURE_ID, PAPER_ID, VERSION_ID
from .content_provider import DEFAULT_OUTPUT as CONTENT_BUNDLE_PATH
from .content_provider import export_content_bundle
from .core import sha256_file, write_json
from .documents import build_exam_docx, build_week_docx, scrub_docx_metadata
from .governance_bridge import (
    ADVERSARIAL_PATH,
    ADVERSARIAL_PHASE2_DISPATCH_PATH,
    ANSWER_PATH,
    ATOMIC_BATCH_REGISTRATION_PATH,
    ATOMIC_DIR,
    ATOMIC_RESULTS_PATH,
    CHAIN_PATH,
    QUESTION_PATH,
    REQUEST_PATH,
    REVIEW_A_PATH,
    REVIEW_B_PATH,
    ROOT_RECONCILIATION_PATH,
    assemble_chain,
    build_atomic_child_chains,
    canonical_hash,
    db_file_ref,
    generator_execution_provenance,
    record_output_sha256,
    root_reconciliation_path,
    validate_controller_chain,
    validate_controller_chain_path,
)
from .governance_bridge import (
    REPORT_PATH as DETERMINISTIC_REPORT_PATH,
)
from .hierarchy import hierarchy_ids, validate_atomic_registry_binding
from .qa import (
    R17_QA_REPORT_NAMES,
    freeze_content_addressed_render_tree,
    scan_files_privacy,
    validate_archive,
    validate_archive_bytes,
    validate_docx_pdf_parity,
    validate_parity_report_artifacts,
    validate_r17_manifest_contract,
)
from .r17_boundary import SnapshotGraph
from .schema_validation import load_schema, validate

WORKSPACE = Path(__file__).resolve().parents[2]
INTEGRATION = WORKSPACE / "integrations/shchem_generation_v2"
DB_ROOT = WORKSPACE / "sh-chem-db"
STAGING = WORKSPACE / "staging/v1_generation"
REVISION_SLUG = VERSION_ID.rsplit("-", 1)[-1].casefold()
CANDIDATE_DIR = STAGING / "candidates" / REVISION_SLUG
REPORT_DIR = STAGING / "reports" / REVISION_SLUG
REVIEW_DIR = STAGING / "reviews" / REVISION_SLUG
COORDINATION = WORKSPACE / "staging/coordination/generation_publication"
FIGURES = DB_ROOT / "kb/figures/machine_v2"
ASSET_DIR = FIGURES / "assets"
EXPORTS = WORKSPACE / "exports/v1_demo"
STAGE = STAGING / f"publication_candidate_{REVISION_SLUG}"
CONTENT_FREEZE = STAGE / "content_governance_freeze.json"
ARCHIVE_NAME = f"SHCHEM-GEN-V2-{REVISION_SLUG.upper()}-完整示范周包-automated_verified_candidate.zip"
PROMOTION_PATH = EXPORTS / "automated_promotion.json"
DELIVERY_STATUS_PATH = EXPORTS / "delivery_status.json"
REGISTRY_PATH = DB_ROOT / "kb/machine_governance_v2/chain_registry.json"
SOL_GENERATOR_RECEIPT_PATH = CANDIDATE_DIR / "sol_generator_receipt.json"


class PublicationRegistrationPending(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()


def _file_row(path: Path) -> dict[str, Any]:
    return {"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _artifact(path: Path, archive_path: str, kind: str) -> dict[str, Any]:
    return {**_file_row(path), "archive_path": archive_path, "kind": kind}


def _write_report(path: Path, value: dict[str, Any]) -> None:
    write_json(path, value)
    if value.get("status") != "pass":
        raise RuntimeError(f"validation failed: {path}: {value.get('errors', value)}")


def _assert_report(path: Path, *, paper_sha256: str, task_sha256: str) -> None:
    value = _load(path)
    if value.get("status") != "pass":
        raise RuntimeError(f"required report is not pass: {path}")
    if value.get("paper_sha256") != paper_sha256:
        raise RuntimeError(f"report paper hash is stale: {path}")
    if value.get("task_card_sha256") != task_sha256:
        raise RuntimeError(f"report task hash is stale: {path}")


def _validate_sol_generator_receipt(
    receipt_path: Path = SOL_GENERATOR_RECEIPT_PATH,
    *,
    paper_path: Path | None = None,
    task_path: Path | None = None,
) -> dict[str, Any]:
    """Reject synthetic/stale generator labels; require the live prefreeze receipt."""

    paper_path = paper_path or CANDIDATE_DIR / "frozen_paper.json"
    task_path = task_path or CANDIDATE_DIR / "task_card.json"
    schema_path = INTEGRATION / "schemas" / "sol_generator_receipt.schema.json"
    receipt = _load(receipt_path)
    validate(receipt, load_schema(schema_path))
    if receipt.get("provenance_status") != "self_reported":
        raise RuntimeError("Sol generator provenance must remain explicitly self-reported")
    if receipt.get("output_sha256") != record_output_sha256(receipt):
        raise RuntimeError("Sol generator receipt self-hash mismatch")
    request = _load(REQUEST_PATH)
    expected_exact = {
        "schema_binding": _file_row(schema_path),
        "paper": _file_row(paper_path),
        "task_card": _file_row(task_path),
        "subject": {
            "question": _file_row(QUESTION_PATH),
            "answer": _file_row(ANSWER_PATH),
            "subject_pair_sha256": request["subject"]["subject_pair_sha256"],
        },
        "deterministic_check": {
            "request": _file_row(REQUEST_PATH),
            "report": _file_row(DETERMINISTIC_REPORT_PATH),
        },
    }
    for field, expected in expected_exact.items():
        if receipt.get(field) != expected:
            raise RuntimeError(f"Sol generator receipt live binding mismatch: {field}")
    paper = _load(paper_path)
    if (
        receipt.get("version_id") != VERSION_ID
        or receipt.get("paper_id") != paper.get("paper_id")
        or receipt.get("execution_provenance") != generator_execution_provenance()
    ):
        raise RuntimeError("Sol generator receipt identity/provenance mismatch")

    source_paths = sorted(
        [
            *INTEGRATION.glob("*.py"),
            *INTEGRATION.joinpath("schemas").rglob("*.json"),
            INTEGRATION / "render_svg_png.mjs",
        ],
        key=_relative,
    )
    source_rows = [_file_row(path) for path in source_paths if path.is_file()]
    expected_producer = {
        "producer_version_id": VERSION_ID,
        "algorithm": "canonical-sha256-of-sorted-owned-producer-files-v1",
        "source_file_count": len(source_rows),
        "source_tree_sha256": canonical_hash(source_rows),
        "files": source_rows,
    }
    if receipt.get("producer_fingerprint") != expected_producer:
        raise RuntimeError("Sol generator receipt producer fingerprint is stale")
    expected_regeneration = canonical_hash(
        {
            "producer_source_tree_sha256": expected_producer["source_tree_sha256"],
            "paper_sha256": expected_exact["paper"]["sha256"],
            "task_card_sha256": expected_exact["task_card"]["sha256"],
            "version_id": VERSION_ID,
        }
    )
    if receipt.get("regeneration_id") != f"{VERSION_ID}-REGEN-{expected_regeneration[:20]}":
        raise RuntimeError("Sol generator receipt regeneration binding mismatch")
    return receipt


def _delivery_status_record(
    promotion: dict[str, Any], *, promotion_bytes: bytes | None = None
) -> dict[str, Any]:
    fixed_promotion_bytes = (
        promotion_bytes if promotion_bytes is not None else PROMOTION_PATH.read_bytes()
    )
    governance_bytes = json.dumps(
        promotion["content_governance"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema_version": "2.0.0",
        "record_type": "external_teacher_managed_delivery_status",
        "promotion_path": _relative(PROMOTION_PATH),
        "promotion_sha256": hashlib.sha256(fixed_promotion_bytes).hexdigest(),
        "promotion_bytes": len(fixed_promotion_bytes),
        "candidate_sha256": promotion["candidate_sha256"],
        "task_card_sha256": promotion["task_card_sha256"],
        "manifest_path": promotion["manifest_path"],
        "manifest_sha256": promotion["manifest_sha256"],
        "archive_path": promotion["archive_path"],
        "archive_sha256": promotion["archive_sha256"],
        "archive_bytes": promotion["archive_bytes"],
        "content_governance_sha256": hashlib.sha256(governance_bytes).hexdigest(),
        "human_reviewed": False,
        "teacher_managed_delivery_candidate": True,
        "delivery_scope": "teacher_managed_private_delivery",
        "teacher_action_required": True,
        "teaching_use_allowed": False,
        "official_claim_allowed": False,
        "official_publication_allowed": False,
        "external_publication_allowed": False,
        "publication_allowed": False,
        "release_allowed": False,
    }


def _document_jobs() -> list[tuple[str, str, bool]]:
    label = REVISION_SLUG.upper()
    return [
        (f"2026-主题化化学时事模拟卷-{label}-学生卷", "exam_student", False),
        (f"2026-主题化化学时事模拟卷-{label}-教师解析与建议采分点", "exam_solutions", True),
        (f"完整示范周包-{label}-学生版", "week_student", False),
        (f"完整示范周包-{label}-教师解析版", "week_solutions", True),
    ]


def _positive_paths() -> list[Path]:
    return [PROMOTION_PATH, DELIVERY_STATUS_PATH, EXPORTS / "manifest.json", EXPORTS / ARCHIVE_NAME]


class _PublicationTransaction:
    """Preserve prior files and quarantine every output created by one publish run.

    Targets must be prepared *before* their writer runs.  Existing targets are moved
    to the transaction's ``prior`` area and restored on rollback.  New/overwritten
    run outputs are moved to ``failed`` on rollback rather than being deleted.  An
    unrelated path is never enumerated or removed.
    """

    def __init__(self, *, workspace_root: Path, quarantine_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.transaction_id = uuid.uuid4().hex
        self.root = quarantine_root / self.transaction_id
        self._prepared: dict[Path, Path | None] = {}
        self._committed = False
        self._rolled_back = False

    @staticmethod
    def _absolute(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path)))

    def _inside_workspace(self, path: Path) -> bool:
        try:
            return (
                os.path.commonpath(
                    [
                        os.path.normcase(os.fspath(self.workspace_root)),
                        os.path.normcase(os.fspath(path)),
                    ]
                )
                == os.path.normcase(os.fspath(self.workspace_root))
            )
        except ValueError:
            return False

    def _quarantine_path(self, category: str, path: Path) -> Path:
        try:
            relative = path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            relative = os.fspath(path)
        token = hashlib.sha256(relative.casefold().encode("utf-8")).hexdigest()[:16]
        return self.root / category / f"{token}-{path.name}"

    def prepare(self, path: Path) -> None:
        """Reserve one exact owned target and preserve any previous value."""

        if self._committed or self._rolled_back:
            raise RuntimeError("publication transaction is no longer active")
        target = self._absolute(path)
        if not self._inside_workspace(target):
            raise RuntimeError(f"publication transaction target outside workspace: {target}")
        if target in self._prepared:
            return
        for prepared in self._prepared:
            if target in prepared.parents or prepared in target.parents:
                raise RuntimeError(
                    f"publication transaction targets may not overlap: {prepared} / {target}"
                )
        backup: Path | None = None
        if target.exists():
            backup = self._quarantine_path("prior", target)
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
        self._prepared[target] = backup

    def prepare_all(self, paths: list[Path]) -> None:
        for path in paths:
            self.prepare(path)

    @property
    def quarantined_prior_outputs(self) -> list[str]:
        rows = [backup for backup in self._prepared.values() if backup is not None]
        return sorted(_relative(path) for path in rows)

    def rollback(self) -> list[str]:
        """Quarantine this run's values, then restore every preserved predecessor."""

        if self._committed:
            raise RuntimeError("cannot roll back a committed publication transaction")
        if self._rolled_back:
            return []
        moved: list[Path] = []
        errors: list[str] = []
        for target in sorted(self._prepared, key=lambda value: len(value.parts), reverse=True):
            try:
                if target.exists():
                    failed = self._quarantine_path("failed", target)
                    failed.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, failed)
                    moved.append(failed)
            except OSError as exc:
                errors.append(f"quarantine_failed:{target}:{type(exc).__name__}")
        for target, backup in sorted(
            self._prepared.items(), key=lambda row: len(row[0].parts)
        ):
            if backup is None:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
            except OSError as exc:
                errors.append(f"restore_failed:{target}:{type(exc).__name__}")
        self._rolled_back = True
        if errors:
            raise RuntimeError("publication transaction rollback incomplete: " + ";".join(errors))
        return sorted(_relative(path) for path in moved)

    def commit(self) -> None:
        if self._rolled_back:
            raise RuntimeError("cannot commit a rolled-back publication transaction")
        self._committed = True


def _publication_transaction_targets() -> list[Path]:
    """Return the non-overlapping, known positive targets of one publication run."""

    delivery_stage = STAGE / "delivery"
    final_documents = [
        EXPORTS / f"{stem}.{suffix}"
        for stem, _, _ in _document_jobs()
        for suffix in ("docx", "pdf")
    ]
    reports = [
        REPORT_DIR / "controller_post_generation_preflight.json",
        REPORT_DIR / "privacy_delivery_artifacts.json",
        REPORT_DIR / "zip_manifest.json",
        *(REPORT_DIR / f"parity_{kind}.json" for _, kind, _ in _document_jobs()),
    ]
    exports = [
        *final_documents,
        EXPORTS / "manifest.json",
        EXPORTS / ARCHIVE_NAME,
        PROMOTION_PATH,
        DELIVERY_STATUS_PATH,
        EXPORTS / ".manifest.json.pending",
        EXPORTS / f".{ARCHIVE_NAME}.pending",
        EXPORTS / ".delivery_status.json.pending",
    ]
    coordination = [
        COORDINATION / "CONTROLLER_PREFLIGHT_OBSERVATIONS.json",
        COORDINATION / "HANDOFF.json",
        COORDINATION / "HANDOFF.md",
        COORDINATION / "GATEWAY_CONTRACT.json",
    ]
    return [delivery_stage, *reports, *exports, *coordination]


def _controller_registration_status() -> dict[str, Any]:
    expected_atomic_ids = hierarchy_ids(_load(CANDIDATE_DIR / "frozen_paper.json"))["atomic_part_ids"]
    expected_artifact_ids = [PAPER_ID, *expected_atomic_ids]
    paper_row = db_file_ref(CHAIN_PATH)
    batch = _load(ATOMIC_BATCH_REGISTRATION_PATH) if ATOMIC_BATCH_REGISTRATION_PATH.is_file() else {}
    manifest_entries = [row for row in batch.get("chains", []) if isinstance(row, dict)]
    manifest_artifact_ids = [entry.get("expected_artifact_id") for entry in manifest_entries]
    expected = [entry["chain"] for entry in manifest_entries]
    atomic_entries = [entry for entry in manifest_entries if entry.get("expected_artifact_id") != PAPER_ID]
    atomic_rows = [entry["chain"] for entry in atomic_entries]
    try:
        registry = _load(REGISTRY_PATH)
        rows = registry.get("chains") if isinstance(registry.get("chains"), list) else []
        missing = [row for row in expected if rows.count(row) != 1]
        unexpected_duplicates = [row for row in expected if rows.count(row) > 1]
        results_record = _load(ATOMIC_RESULTS_PATH)
        results_by_row = {
            json.dumps(result["chain"], ensure_ascii=False, sort_keys=True): result
            for result in results_record.get("results", [])
            if isinstance(result, dict) and isinstance(result.get("chain"), dict)
        }
        atomic_bindings = []
        for row in atomic_rows:
            result = results_by_row.get(json.dumps(row, ensure_ascii=False, sort_keys=True), {})
            atomic_bindings.append(
                {
                    "artifact_id": result.get("atomic_part_id"),
                    "subject_pair_sha256": result.get("subject_pair_sha256"),
                    "registry_row": row,
                    "exact_registry_member": rows.count(row) == 1,
                }
            )
        binding = {
            "atomic": atomic_bindings,
            "atomic_count": len(expected_atomic_ids),
        }
        binding_errors = validate_atomic_registry_binding(expected_atomic_ids, binding)
        exact_batch = (
            manifest_artifact_ids == expected_artifact_ids
            and len(manifest_artifact_ids) == len(set(manifest_artifact_ids))
        )
        return {
            "registered": exact_batch and not binding_errors and not missing and not unexpected_duplicates,
            "expected_artifact_ids": expected_artifact_ids,
            "expected": expected,
            "matched": [row for row in expected if rows.count(row) == 1],
            "missing": missing,
            "duplicate_expected_rows": unexpected_duplicates,
            "paper": {
                "artifact_id": PAPER_ID,
                "subject_pair_sha256": next(
                    entry["expected_subject_pair_sha256"]
                    for entry in manifest_entries
                    if entry.get("expected_artifact_id") == PAPER_ID
                ),
                "registry_row": paper_row,
                "exact_registry_member": rows.count(paper_row) == 1,
            },
            "atomic": atomic_bindings,
            "paper_chain_registered": rows.count(paper_row) == 1,
            "atomic_chain_registered_count": sum(row["exact_registry_member"] for row in atomic_bindings),
            "atomic_chain_required_count": len(expected_atomic_ids),
            "binding_errors": binding_errors,
            "registry_path": _relative(REGISTRY_PATH),
            "registry_sha256": sha256_file(REGISTRY_PATH),
            "central_registry_modified_by_generation_task": False,
        }
    except Exception as exc:
        return {"registered": False, "expected": expected, "error": str(exc)}


def _registration_request(registration: dict[str, Any], controller_report: Path) -> None:
    fallback = [
        f'python sh-chem-db/scripts/sh_chem_agent.py governance-register --root "{DB_ROOT}" --chain "{DB_ROOT / row["path"]}"'
        for row in registration.get("expected", [])
    ]
    write_json(
        COORDINATION / "CONTROLLER_CHAIN_REGISTRATION_REQUEST.json",
        {
            "schema_version": "2.0.0",
            "record_type": "controller_chain_batch_registration_request",
            "status": "already_registered" if registration.get("registered") else "pending_controller_owner",
            "expected_chain_count": len(registration.get("expected_artifact_ids", [])),
            "chains": registration.get("expected", []),
            "paper_chain": db_file_ref(CHAIN_PATH),
            "atomic_batch_manifest": db_file_ref(ATOMIC_BATCH_REGISTRATION_PATH),
            "controller_validation": db_file_ref(controller_report),
            "requested_command_for_controller_owner": (
                f'python sh-chem-db/scripts/sh_chem_agent.py governance-register-batch '
                f'--root "{DB_ROOT}" --manifest "{ATOMIC_BATCH_REGISTRATION_PATH}"'
            ),
            "compatibility_commands_if_batch_command_unavailable": fallback,
            "batch_command_observed_available": False,
            "teacher_managed_delivery_candidate": False,
            "delivery_scope": None,
            "executed_by_generation_task": False,
            "central_registry_write_authorized_for_generation_task": False,
            "human_reviewed": False,
        },
    )


def _prepare_content_governance() -> dict[str, Any]:
    STAGE.mkdir(parents=True, exist_ok=True)
    COORDINATION.mkdir(parents=True, exist_ok=True)
    paper_path = CANDIDATE_DIR / "frozen_paper.json"
    task_path = CANDIDATE_DIR / "task_card.json"
    paper = _load(paper_path)
    paper_sha = sha256_file(paper_path)
    task_sha = sha256_file(task_path)
    if paper.get("observed_profile_contract", {}).get("provisional_fixture") is not False:
        raise RuntimeError("finalization forbids a provisional observed profile")

    expected_atomic_ids = hierarchy_ids(paper)["atomic_part_ids"]
    reconciliation_paths = [
        ROOT_RECONCILIATION_PATH,
        *(root_reconciliation_path(part_id) for part_id in expected_atomic_ids),
    ]
    governed = [
        QUESTION_PATH,
        ANSWER_PATH,
        REQUEST_PATH,
        DETERMINISTIC_REPORT_PATH,
        REVIEW_A_PATH,
        REVIEW_B_PATH,
        ADVERSARIAL_PHASE2_DISPATCH_PATH,
        ADVERSARIAL_PATH,
        SOL_GENERATOR_RECEIPT_PATH,
        *reconciliation_paths,
    ]
    missing = [str(path) for path in governed if not path.is_file()]
    if missing:
        raise RuntimeError(f"external controller governance receipts incomplete: {missing}")
    assemble_chain(
        state="automated_verified_candidate",
        include_reviews=True,
        include_adversarial=True,
        include_delivery=False,
    )
    _validate_sol_generator_receipt(
        paper_path=paper_path,
        task_path=task_path,
    )
    validation = validate_controller_chain(require_state="automated_verified_candidate")
    if (
        validation.get("valid") is not True
        or validation.get("teacher_managed_delivery_candidate") is not False
        or _load(CHAIN_PATH).get("teacher_managed_delivery") is not None
    ):
        raise RuntimeError(f"controller rejected content-only chain: {validation}")
    atomic_batch = build_atomic_child_chains()
    if (
        atomic_batch.get("expected_atomic_part_ids") != expected_atomic_ids
        or atomic_batch.get("atomic_chain_count") != len(expected_atomic_ids)
        or atomic_batch.get("all_controller_valid") is not True
    ):
        raise RuntimeError("not all hierarchy-derived controller-native atomic child chains validated")

    prechecks = [
        REPORT_DIR / name
        for name in (
            "schema.json",
            "conservation.json",
            "inverse.json",
            "components.json",
            "figure.json",
            "dedup.json",
            "versioned_schema.json",
            "coverage_matrix.json",
            "coverage_matrix_validation.json",
            "figure_topology_mutations.json",
            "controller_machine_pass.json",
        )
    ]
    for path in prechecks:
        _assert_report(path, paper_sha256=paper_sha, task_sha256=task_sha)
    controller_report = REPORT_DIR / "controller_content_governance.json"
    _write_report(
        controller_report,
        {
            "check": "controller_v2_content_only_governance",
            "status": "pass",
            "paper_sha256": paper_sha,
            "task_card_sha256": task_sha,
            "chain_path": _relative(CHAIN_PATH),
            "chain_sha256": sha256_file(CHAIN_PATH),
            "validation": validation,
            "teacher_managed_delivery_candidate": False,
            "errors": [],
        },
    )
    files = [
        *governed,
        CHAIN_PATH,
        *sorted(ATOMIC_DIR.rglob("*.json")),
        ATOMIC_BATCH_REGISTRATION_PATH,
        ATOMIC_RESULTS_PATH,
        *prechecks,
        controller_report,
    ]
    freeze = {
        "schema_version": "2.0.0",
        "record_type": "content_governance_freeze",
        "version_id": VERSION_ID,
        "paper_id": PAPER_ID,
        "paper_sha256": paper_sha,
        "task_card_sha256": task_sha,
        "governance_chain_sha256": sha256_file(CHAIN_PATH),
        "files": [_file_row(path) for path in files],
        "content_status": "automated_verified_content_pending_central_registration",
        "teacher_managed_delivery_candidate": False,
        "delivery_scope": None,
        "teacher_action_required": False,
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "official_claim_allowed": False,
    }
    write_json(CONTENT_FREEZE, freeze)
    registration = _controller_registration_status()
    _registration_request(registration, controller_report)
    return freeze


def _current_content_freeze_valid() -> bool:
    if not CONTENT_FREEZE.is_file() or not CHAIN_PATH.is_file():
        return False
    try:
        freeze = _load(CONTENT_FREEZE)
        if freeze.get("paper_sha256") != sha256_file(CANDIDATE_DIR / "frozen_paper.json"):
            return False
        if freeze.get("task_card_sha256") != sha256_file(CANDIDATE_DIR / "task_card.json"):
            return False
        if freeze.get("governance_chain_sha256") != sha256_file(CHAIN_PATH):
            return False
        if freeze.get("teacher_managed_delivery_candidate") is not False:
            return False
        return all(
            (WORKSPACE / row["path"]).is_file()
            and sha256_file(WORKSPACE / row["path"]) == row["sha256"]
            for row in freeze.get("files", [])
        )
    except Exception:
        return False


def _load_or_prepare_content_governance() -> dict[str, Any]:
    return _load(CONTENT_FREEZE) if _current_content_freeze_valid() else _prepare_content_governance()


def _controller_preflight_or_fail() -> tuple[dict[str, Any], Path]:
    from .gateway_api import DEFAULT_PROFILE, controller_preflight

    result = controller_preflight("generate", profile_path=DEFAULT_PROFILE, governance_chain=CHAIN_PATH)
    payload = result.get("json") if isinstance(result.get("json"), dict) else {}
    if (
        result.get("available") is not True
        or result.get("exit_code") != 0
        or payload.get("ok") is not True
        or payload.get("ready") is not True
    ):
        raise RuntimeError(f"post-generation controller preflight denied: {result}")
    report = {
        "check": "controller_post_generation_generate_preflight",
        "status": "pass",
        "chain_path": _relative(CHAIN_PATH),
        "chain_sha256": sha256_file(CHAIN_PATH),
        "profile_path": _relative(DEFAULT_PROFILE),
        "profile_sha256": sha256_file(DEFAULT_PROFILE),
        "controller_result": {
            "available": True,
            "exit_code": result.get("exit_code"),
            "json": {
                key: payload.get(key)
                for key in (
                    "command",
                    "contract_version",
                    "controller_version",
                    "mode",
                    "ok",
                    "ready",
                    "gate_phase",
                    "checks",
                    "blockers",
                    "governance_chain_validation",
                    "human_reviewed",
                    "teaching_use_allowed",
                    "external_publication_allowed",
                    "official_claim_allowed",
                )
            },
            "stderr": result.get("stderr", ""),
        },
        "errors": [],
    }
    path = REPORT_DIR / "controller_post_generation_preflight.json"
    _write_report(path, report)
    return result, path


def _render_delivery_stage() -> tuple[list[tuple[Path, Path, str, Path]], list[Path], Path]:
    from .build_demo import _render_docx

    paper_path = CANDIDATE_DIR / "frozen_paper.json"
    task_path = CANDIDATE_DIR / "task_card.json"
    plan_path = CANDIDATE_DIR / "week_plan.json"
    paper = _load(paper_path)
    plan = _load(plan_path)
    content_bundle = export_content_bundle(paper_path=paper_path, output_path=CONTENT_BUNDLE_PATH)
    delivery_stage = STAGE / "delivery"
    delivery_stage.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[Path, Path, str, Path]] = []
    figure_png = ASSET_DIR / f"{FIGURE_ID}.png"
    for stem, kind, solutions in _document_jobs():
        docx = delivery_stage / f"{stem}.docx"
        if kind.startswith("exam"):
            build_exam_docx(paper, figure_png, docx, solutions=solutions)
        else:
            build_week_docx(paper, plan, docx, solutions=solutions, content_bundle=content_bundle)
        scrub_docx_metadata(docx)
        render_dir = delivery_stage / "qa" / stem
        pdf = _render_docx(docx, render_dir)
        generated.append((docx, pdf, kind, render_dir))

    parity_paths = []
    theme_markers = [theme["title"] for theme in paper["themes"]]
    hierarchy = hierarchy_ids(paper)
    first_printed = hierarchy["printed_question_ids"][0]
    last_printed = hierarchy["printed_question_ids"][-1]
    first_atomic = hierarchy["atomic_part_ids"][0]
    last_atomic = hierarchy["atomic_part_ids"][-1]
    for docx, pdf, kind, render_dir in generated:
        if kind.startswith("exam"):
            markers = [
                *theme_markers,
                f"{int(first_printed[1:])}．",
                f"{int(last_printed[1:])}．",
                "建议采分点（非官方）" if kind.endswith("solutions") else "作答说明",
            ]
            figure_id = FIGURE_ID
        else:
            markers = [
                "第1日",
                "第7日",
                "第14日",
                first_atomic,
                last_atomic,
                "synthetic_student_profile",
                "automated_verified_content",
                "建议答案" if kind.endswith("solutions") else "完成记录",
            ]
            figure_id = None
        result = validate_docx_pdf_parity(
            docx_path=docx,
            pdf_path=pdf,
            render_dir=render_dir,
            version_id=paper["version_id"],
            required_markers=markers,
            expected_figure_id=figure_id,
            semantic_kind=kind,
        )
        result = freeze_content_addressed_render_tree(
            result,
            content_root=delivery_stage / "render_page_trees",
        )
        validate(result, load_schema(INTEGRATION / "schemas/parity_report.schema.json"))
        artifact_validation = validate_parity_report_artifacts(result)
        if artifact_validation["status"] != "pass":
            result["status"] = "fail"
            result["errors"].extend(artifact_validation["errors"])
        result["paper_sha256"] = sha256_file(paper_path)
        result["task_card_sha256"] = sha256_file(task_path)
        path = REPORT_DIR / f"parity_{kind}.json"
        _write_report(path, result)
        parity_paths.append(path)

    privacy_inputs = [
        paper_path,
        task_path,
        plan_path,
        CONTENT_BUNDLE_PATH,
        FIGURES / COMPONENT_REGISTRY_FILENAME,
        FIGURES / f"{FIGURE_ID}.spec.json",
        ASSET_DIR / f"{FIGURE_ID}.svg",
        ASSET_DIR / f"{FIGURE_ID}.png",
        QUESTION_PATH,
        ANSWER_PATH,
        REQUEST_PATH,
        DETERMINISTIC_REPORT_PATH,
        REVIEW_A_PATH,
        REVIEW_B_PATH,
        ADVERSARIAL_PATH,
        CHAIN_PATH,
        ATOMIC_BATCH_REGISTRATION_PATH,
        ATOMIC_RESULTS_PATH,
        *sorted(ATOMIC_DIR.rglob("*.json")),
        *[path for row in generated for path in row[:2]],
        *parity_paths,
    ]
    privacy = scan_files_privacy(privacy_inputs)
    privacy["paper_sha256"] = sha256_file(paper_path)
    privacy["task_card_sha256"] = sha256_file(task_path)
    privacy_path = REPORT_DIR / "privacy_delivery_artifacts.json"
    _write_report(privacy_path, privacy)
    return generated, parity_paths, privacy_path


def _semantic_pairs(
    generated: list[tuple[Path, Path, str, Path]], parity_paths: list[Path]
) -> list[dict[str, Any]]:
    by_kind = {path.stem.removeprefix("parity_"): path for path in parity_paths}
    pairs = []
    for docx, pdf, kind, _ in generated:
        parity_path = by_kind[kind]
        parity = _load(parity_path)
        if parity.get("semantic_kind") != kind:
            raise RuntimeError(f"parity semantic kind mismatch: {kind}")
        if parity.get("docx", {}).get("sha256") != sha256_file(docx):
            raise RuntimeError(f"parity/final DOCX content hash mismatch: {kind}")
        if parity.get("pdf", {}).get("sha256") != sha256_file(pdf):
            raise RuntimeError(f"parity/final PDF content hash mismatch: {kind}")
        artifact_check = validate_parity_report_artifacts(parity)
        if artifact_check["status"] != "pass":
            raise RuntimeError(f"parity render-root revalidation failed: {kind}: {artifact_check}")
        pairs.append(
            {
                "semantic_kind": kind,
                "docx": _file_row(docx),
                "pdf": _file_row(pdf),
                "parity_report": _file_row(parity_path),
            }
        )
    return pairs


def _snapshot_db_file_ref(
    graph: SnapshotGraph,
    reference: dict[str, Any],
    *,
    label: str,
) -> dict[str, object]:
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256", "bytes"}:
        raise RuntimeError(f"{label}: controller file ref is not exact")
    path = DB_ROOT / str(reference["path"])
    snapshot = graph.read(path)
    observed = snapshot.ref(DB_ROOT)
    if observed != reference:
        raise RuntimeError(f"{label}: controller file ref changed")
    return observed


def _promotion_registry_binding(
    registration: dict[str, Any],
    *,
    graph: SnapshotGraph | None = None,
    registry_ref: dict[str, object] | None = None,
) -> dict[str, Any]:
    paper = copy.deepcopy(registration["paper"])
    atomic = copy.deepcopy(registration["atomic"])
    if graph is not None:
        paper["registry_row"] = _snapshot_db_file_ref(
            graph,
            paper["registry_row"],
            label="promotion.registry.paper",
        )
        for index, row in enumerate(atomic):
            row["registry_row"] = _snapshot_db_file_ref(
                graph,
                row["registry_row"],
                label=f"promotion.registry.atomic[{index}]",
            )
    return {
        "registry_path": (
            registry_ref["path"] if registry_ref is not None else registration["registry_path"]
        ),
        "registry_sha256": (
            registry_ref["sha256"]
            if registry_ref is not None
            else registration["registry_sha256"]
        ),
        "paper": paper,
        "atomic": atomic,
        "paper_count": 1,
        "atomic_count": len(atomic),
        "all_exact_members": registration["registered"],
    }


def _promotion_prerequisite_snapshot(
    *,
    registration: dict[str, Any],
    paper_path: Path,
    task_path: Path,
    manifest_path: Path,
    archive_path: Path,
    zip_report_path: Path,
    chain_path: Path,
    preflight_path: Path,
    generated: list[tuple[Path, Path, str, Path]],
    qa_paths: list[Path],
    parity_paths: list[Path],
) -> tuple[dict[str, Any], SnapshotGraph, dict[str, Any]]:
    """Revalidate every R17 promotion input from one immutable snapshot graph."""

    errors: list[str] = []
    graph = SnapshotGraph(WORKSPACE)
    promotion_inputs: dict[str, Any] = {}
    try:
        paper_snapshot = graph.read(paper_path)
        task_snapshot = graph.read(task_path)
        manifest_snapshot = graph.read(manifest_path)
        archive_snapshot = graph.read(archive_path)
        zip_report_snapshot = graph.read(zip_report_path)
        chain_snapshot = graph.read(chain_path)
        preflight_snapshot = graph.read(preflight_path)
        promotion_schema_snapshot = graph.read(
            INTEGRATION / "schemas/automated_promotion.schema.json"
        )
        delivery_status_schema_snapshot = graph.read(
            INTEGRATION / "schemas/delivery_status.schema.json"
        )
        manifest_schema_snapshot = graph.read(
            INTEGRATION / "schemas/weekpack_manifest.schema.json"
        )
        manifest = manifest_snapshot.json_value()
        if not isinstance(manifest, dict):
            raise TypeError("promotion manifest is not a JSON object")
        validate(manifest, manifest_schema_snapshot.json_value())
        manifest_contract = validate_r17_manifest_contract(
            manifest,
            expected_qa_paths=[_relative(path) for path in qa_paths],
        )
        if manifest_contract["status"] != "pass":
            errors.extend(manifest_contract["errors"])

        registration_now = _controller_registration_status()
        if (
            registration_now.get("registered") is not True
            or registration_now.get("registry_sha256") != registration.get("registry_sha256")
            or registration_now.get("expected") != registration.get("expected")
            or registration_now.get("matched") != registration.get("matched")
        ):
            errors.append("controller registry changed before promotion")
        registry_snapshot = graph.read(REGISTRY_PATH)
        if registry_snapshot.sha256 != registration.get("registry_sha256"):
            errors.append("controller registry snapshot hash mismatch")
        chain_revalidations = []
        for index, chain_ref in enumerate(registration.get("expected", [])):
            registered_chain_path = DB_ROOT / str(chain_ref.get("path", ""))
            _snapshot_db_file_ref(
                graph,
                chain_ref,
                label=f"promotion.registered_chain[{index}]",
            )
            chain_validation = validate_controller_chain_path(
                registered_chain_path,
                require_state="automated_verified_candidate",
            )
            chain_revalidations.append(chain_validation)
            if (
                chain_validation.get("valid") is not True
                or chain_validation.get("execution_provenance_external_reconciled") is not True
            ):
                errors.append(
                    f"controller chain reconciliation revalidation failed: {index}"
                )

        freeze_snapshot = graph.read(CONTENT_FREEZE)
        freeze = freeze_snapshot.json_value()
        if not isinstance(freeze, dict):
            errors.append("content governance freeze is not an object")
            freeze = {}
        freeze_paths = [
            row.get("path") for row in freeze.get("files", []) if isinstance(row, dict)
        ]
        if len(freeze_paths) != len({str(path).casefold() for path in freeze_paths}):
            errors.append("content governance freeze contains duplicate paths")
        for index, reference in enumerate(freeze.get("files", [])):
            graph.verify_ref(reference, label=f"content_freeze.files[{index}]")
        if freeze.get("governance_chain_sha256") != chain_snapshot.sha256:
            errors.append("content governance freeze chain hash changed")
        if freeze.get("teacher_managed_delivery_candidate") is not False:
            errors.append("content governance freeze overclaims delivery")

        qa_by_path = {
            row.get("path"): row
            for row in manifest.get("qa_reports", [])
            if isinstance(row, dict)
        }
        for path in qa_paths:
            reference = qa_by_path.get(_relative(path))
            snapshot = graph.verify_ref(reference, label=f"qa:{path.name}")
            report = snapshot.json_value()
            if not isinstance(report, dict) or report.get("status") != "pass":
                errors.append(f"QA report is not exact PASS: {path.name}")

        render_tree_hashes: list[str] = []
        for parity_path in parity_paths:
            parity_snapshot = graph.read(parity_path)
            parity = parity_snapshot.json_value()
            if not isinstance(parity, dict):
                errors.append(f"parity report is not an object: {parity_path.name}")
                continue
            tree_sha = parity.get("render_root_tree_sha256")
            if parity.get("render_tree_content_address") != f"sha256:{tree_sha}":
                errors.append(f"parity tree is not content-addressed: {parity_path.name}")
            render_root = WORKSPACE / str(parity.get("render_root", ""))
            expected_names = []
            for index, row in enumerate(parity.get("rendered_pages", [])):
                if not isinstance(row, dict) or row.get("status") != "pass":
                    errors.append(f"render page is not PASS: {parity_path.name}:{index}")
                    continue
                expected_names.append(str(row.get("relative_path")))
                graph.verify_ref(
                    {
                        "path": _relative(render_root / str(row.get("relative_path"))),
                        "sha256": row.get("sha256"),
                        "bytes": row.get("bytes"),
                    },
                    label=f"render:{parity_path.stem}:{index}",
                )
            live_names = sorted(path.name for path in render_root.iterdir())
            if live_names != sorted(expected_names):
                errors.append(f"render page set changed: {parity_path.name}")
            render_tree_hashes.append(str(tree_sha))

        for index, artifact in enumerate(manifest.get("artifacts", [])):
            graph.verify_ref(
                {
                    key: artifact.get(key)
                    for key in ("path", "sha256", "bytes")
                },
                label=f"manifest.artifacts[{index}]",
            )
        archive_validation = validate_archive_bytes(
            archive_snapshot.data,
            manifest,
            archive_label=archive_snapshot.ref(WORKSPACE)["path"],
            manifest_bytes=manifest_snapshot.data,
        )
        if archive_validation.get("status") != "pass":
            errors.extend(archive_validation.get("errors", []))
        if archive_validation.get("archive_sha256") != archive_snapshot.sha256:
            errors.append("ZIP validation and snapshot hashes differ")
        if archive_validation.get("member_count") != len(manifest.get("artifacts", [])) + 1:
            errors.append("ZIP snapshot member count mismatch")
        zip_report = zip_report_snapshot.json_value()
        if not isinstance(zip_report, dict) or zip_report.get("status") != "pass":
            errors.append("ZIP integrity report is not exact PASS")
            zip_report = {}
        if zip_report.get("archive_sha256") != archive_snapshot.sha256:
            errors.append("ZIP report/archive snapshot hash mismatch")
        if zip_report.get("archive_bytes") != archive_snapshot.byte_length:
            errors.append("ZIP report/archive snapshot byte-length mismatch")
        if zip_report.get("manifest_sha256") != manifest_snapshot.sha256:
            errors.append("ZIP report/manifest snapshot hash mismatch")
        if zip_report.get("manifest_bytes") != manifest_snapshot.byte_length:
            errors.append("ZIP report/manifest snapshot byte-length mismatch")
        if zip_report.get("manifest_member_bytes_exact") is not True:
            errors.append("ZIP report does not prove exact manifest member bytes")
        expected_tuple = {
            "staged": {
                "manifest_sha256": manifest_snapshot.sha256,
                "manifest_bytes": manifest_snapshot.byte_length,
                "archive_sha256": archive_snapshot.sha256,
                "archive_bytes": archive_snapshot.byte_length,
            },
            "final": {
                "manifest_sha256": manifest_snapshot.sha256,
                "manifest_bytes": manifest_snapshot.byte_length,
                "archive_sha256": archive_snapshot.sha256,
                "archive_bytes": archive_snapshot.byte_length,
            },
            "exact": True,
        }
        if zip_report.get("staged_final_tuple") != expected_tuple:
            errors.append("ZIP report staged/final byte tuple is not exact")
        if zip_report.get("member_count") != archive_validation.get("member_count"):
            errors.append("ZIP report/archive snapshot member count mismatch")
        if zip_report.get("paper_sha256") != paper_snapshot.sha256:
            errors.append("ZIP report/paper snapshot hash mismatch")
        if zip_report.get("task_card_sha256") != task_snapshot.sha256:
            errors.append("ZIP report/task snapshot hash mismatch")

        preflight = preflight_snapshot.json_value()
        if not isinstance(preflight, dict) or preflight.get("status") != "pass":
            errors.append("post-generation preflight is not exact PASS")
            preflight = {}
        if preflight.get("chain_sha256") != chain_snapshot.sha256:
            errors.append("preflight/governance-chain snapshot hash mismatch")

        parity_by_kind = {
            path.stem.removeprefix("parity_"): path for path in parity_paths
        }
        semantic_pairs: list[dict[str, Any]] = []
        for docx, pdf, kind, _ in generated:
            parity_path = parity_by_kind.get(kind)
            if parity_path is None:
                errors.append(f"semantic pair parity report missing: {kind}")
                continue
            docx_snapshot = graph.read(docx)
            pdf_snapshot = graph.read(pdf)
            parity_snapshot = graph.read(parity_path)
            parity = parity_snapshot.json_value()
            if not isinstance(parity, dict) or parity.get("semantic_kind") != kind:
                errors.append(f"semantic pair kind mismatch: {kind}")
                continue
            if parity.get("docx", {}).get("sha256") != docx_snapshot.sha256:
                errors.append(f"semantic pair DOCX snapshot mismatch: {kind}")
            if parity.get("pdf", {}).get("sha256") != pdf_snapshot.sha256:
                errors.append(f"semantic pair PDF snapshot mismatch: {kind}")
            semantic_pairs.append(
                {
                    "semantic_kind": kind,
                    "docx": docx_snapshot.ref(WORKSPACE),
                    "pdf": pdf_snapshot.ref(WORKSPACE),
                    "parity_report": parity_snapshot.ref(WORKSPACE),
                }
            )
        qa_evidence = [
            graph.read(path).ref(WORKSPACE) for path in [*qa_paths, zip_report_path]
        ]
        registry_ref = registry_snapshot.ref(WORKSPACE)
        promotion_inputs = {
            "paper": paper_snapshot.ref(WORKSPACE),
            "task_card": task_snapshot.ref(WORKSPACE),
            "manifest": manifest_snapshot.ref(WORKSPACE),
            "archive": archive_snapshot.ref(WORKSPACE),
            "zip_integrity_report": zip_report_snapshot.ref(WORKSPACE),
            "governance_chain": chain_snapshot.ref(WORKSPACE),
            "post_generation_preflight": preflight_snapshot.ref(WORKSPACE),
            "semantic_pairs": semantic_pairs,
            "qa_evidence": qa_evidence,
            "registry_binding": _promotion_registry_binding(
                registration,
                graph=graph,
                registry_ref=registry_ref,
            ),
            "promotion_schema": promotion_schema_snapshot.json_value(),
            "delivery_status_schema": delivery_status_schema_snapshot.json_value(),
        }
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        errors.append(f"promotion snapshot failed:{type(exc).__name__}:{exc}")

    snapshot = {
        "schema_version": "3.0.0-r17",
        "check": "promotion_registry_content_qa_render_zip_snapshot",
        "status": "pass" if not errors else "fail",
        "registry": (
            registry_snapshot.ref(WORKSPACE)
            if "registry_snapshot" in locals()
            else None
        ),
        "content_governance_freeze": (
            freeze_snapshot.ref(WORKSPACE) if "freeze_snapshot" in locals() else None
        ),
        "manifest": (
            manifest_snapshot.ref(WORKSPACE) if "manifest_snapshot" in locals() else None
        ),
        "archive": (
            archive_snapshot.ref(WORKSPACE) if "archive_snapshot" in locals() else None
        ),
        "paper": paper_snapshot.ref(WORKSPACE) if "paper_snapshot" in locals() else None,
        "task_card": task_snapshot.ref(WORKSPACE) if "task_snapshot" in locals() else None,
        "zip_integrity_report": (
            zip_report_snapshot.ref(WORKSPACE)
            if "zip_report_snapshot" in locals()
            else None
        ),
        "governance_chain": (
            chain_snapshot.ref(WORKSPACE) if "chain_snapshot" in locals() else None
        ),
        "post_generation_preflight": (
            preflight_snapshot.ref(WORKSPACE)
            if "preflight_snapshot" in locals()
            else None
        ),
        "qa_report_count": len(qa_paths),
        "controller_chain_revalidation_count": (
            len(chain_revalidations) if "chain_revalidations" in locals() else 0
        ),
        "external_reconciliation_revalidated": bool(
            "chain_revalidations" in locals()
            and chain_revalidations
            and all(
                row.get("valid") is True
                and row.get("execution_provenance_external_reconciled") is True
                for row in chain_revalidations
            )
        ),
        "render_tree_sha256s": render_tree_hashes if "render_tree_hashes" in locals() else [],
        "snapshot_graph_sha256": graph.digest(),
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "publication_allowed": False,
        "errors": sorted(dict.fromkeys(errors)),
    }
    return snapshot, graph, promotion_inputs


def _fixed_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _expected_graph_revalidation(graph: SnapshotGraph) -> dict[str, Any]:
    digest = graph.digest()
    return {
        "status": "pass",
        "original_snapshot_graph_sha256": digest,
        "refreshed_snapshot_graph_sha256": digest,
        "errors": [],
    }


def _atomic_write_fixed_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.pending"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _assert_exact_file_bytes(path: Path, expected: bytes, *, label: str) -> dict[str, Any]:
    observed = path.read_bytes()
    if observed != expected:
        raise RuntimeError(f"{label} exact byte readback mismatch")
    return {
        "path": _relative(path),
        "sha256": hashlib.sha256(observed).hexdigest(),
        "bytes": len(observed),
    }


def _commit_fixed_promotion_bytes(
    *,
    output_path: Path,
    promotion_bytes: bytes,
    graph: SnapshotGraph,
    expected_revalidation: dict[str, Any],
    before_revalidation=None,
    after_write=None,
    remove_output_on_failure: bool = True,
) -> dict[str, Any]:
    """Commit fixed bytes with precheck, exact readback, and post-write recheck.

    This primitive is used for both the promotion and delivery-status records.  On
    any exception it removes the just-written positive target by default.  The
    production caller disables that local removal so the enclosing
    :class:`_PublicationTransaction` can quarantine the complete failed run.
    """

    try:
        if before_revalidation is not None:
            before_revalidation()
        observed_before = graph.revalidate()
        if observed_before != expected_revalidation:
            raise RuntimeError(
                "R17 promotion prerequisite changed after byte freeze before output "
                f"write: {observed_before}"
            )
        _atomic_write_fixed_bytes(output_path, promotion_bytes)
        if after_write is not None:
            after_write()
        readback_ref = _assert_exact_file_bytes(
            output_path,
            promotion_bytes,
            label="R17 publication output",
        )
        observed_after = graph.revalidate()
        if observed_after != expected_revalidation:
            raise RuntimeError(
                "R17 promotion prerequisite changed after byte freeze and output "
                f"write: {observed_after}"
            )
        return {
            "status": "pass",
            "pre_write": observed_before,
            "exact_readback": True,
            "output_sha256": readback_ref["sha256"],
            "output_bytes": readback_ref["bytes"],
            "post_write": observed_after,
        }
    except Exception:
        if remove_output_on_failure:
            output_path.unlink(missing_ok=True)
        raise


def finalize_atomic(*, failure_injection: str | None = None) -> None:
    """Build delivery artifacts and write the external sidecar only after all gates pass."""

    from .gateway_api import DEFAULT_PROFILE, controller_preflight, gateway_contract

    transaction: _PublicationTransaction | None = None
    try:
        _load_or_prepare_content_governance()
        registration = _controller_registration_status()
        write_json(COORDINATION / "CONTROLLER_CHAIN_REGISTRATION_OBSERVATION.json", registration)
        if not registration.get("registered"):
            raise PublicationRegistrationPending(
                "content chains are valid but the controller owner has not registered the exact paper plus hierarchy-derived atomic rows"
            )
        transaction = _PublicationTransaction(
            workspace_root=WORKSPACE,
            quarantine_root=STAGING / "publication_transactions",
        )
        transaction.prepare_all(_publication_transaction_targets())
        quarantined = transaction.quarantined_prior_outputs
        preflight_result, preflight_path = _controller_preflight_or_fail()
        generated_stage, parity_paths, _privacy_path = _render_delivery_stage()
        if failure_injection == "copy_failure":
            raise RuntimeError("injected final copy failure")

        EXPORTS.mkdir(parents=True, exist_ok=True)
        generated: list[tuple[Path, Path, str, Path]] = []
        for staged_docx, staged_pdf, kind, render_dir in generated_stage:
            final_docx = EXPORTS / staged_docx.name
            final_pdf = EXPORTS / staged_pdf.name
            shutil.copy2(staged_docx, final_docx)
            shutil.copy2(staged_pdf, final_pdf)
            if sha256_file(staged_docx) != sha256_file(final_docx):
                raise RuntimeError("final DOCX copy hash mismatch")
            if sha256_file(staged_pdf) != sha256_file(final_pdf):
                raise RuntimeError("final PDF copy hash mismatch")
            generated.append((final_docx, final_pdf, kind, render_dir))

        paper_path = CANDIDATE_DIR / "frozen_paper.json"
        task_path = CANDIDATE_DIR / "task_card.json"
        plan_path = CANDIDATE_DIR / "week_plan.json"
        paper = _load(paper_path)
        expected_atomic_ids = hierarchy_ids(paper)["atomic_part_ids"]
        supporting = [
            (paper_path, "data/frozen_paper.json", "frozen_paper"),
            (task_path, "data/task_card.json", "task_card"),
            (plan_path, "data/week_plan.json", "week_plan"),
            (CONTENT_BUNDLE_PATH, "data/weekpack_content.json", "registered_atomic_content_catalog"),
            (FIGURES / COMPONENT_REGISTRY_FILENAME, "figures/component_registry.json", "component_registry"),
            (FIGURES / f"{FIGURE_ID}.spec.json", f"figures/{FIGURE_ID}.spec.json", "figure_spec"),
            (ASSET_DIR / f"{FIGURE_ID}.svg", f"figures/{FIGURE_ID}.svg", "figure_svg"),
            (ASSET_DIR / f"{FIGURE_ID}.png", f"figures/{FIGURE_ID}.png", "figure_png"),
            (SOL_GENERATOR_RECEIPT_PATH, "governance/sol_generator_receipt.json", "sol_generator_receipt"),
            (ROOT_RECONCILIATION_PATH, "governance/root_provenance_reconciliation_paper.json", "root_provenance_reconciliation_not_signature"),
            (QUESTION_PATH, "governance/subject_question.json", "controller_question_subject"),
            (ANSWER_PATH, "governance/subject_answer.json", "controller_answer_subject"),
            (REQUEST_PATH, "governance/deterministic_check_request.json", "controller_deterministic_request"),
            (DETERMINISTIC_REPORT_PATH, "governance/deterministic_check_report.json", "controller_deterministic_report"),
            (REVIEW_A_PATH, "governance/sol_review_a.json", "external_sol_review_a"),
            (REVIEW_B_PATH, "governance/sol_review_b.json", "external_sol_review_b"),
            (ADVERSARIAL_PATH, "governance/adversarial_check.json", "external_adversarial_check"),
            (CHAIN_PATH, "governance/governance_chain.json", "content_governance_chain"),
            (ATOMIC_BATCH_REGISTRATION_PATH, "governance/atomic_batch_registration_request.json", "atomic_batch_registration_manifest"),
            (ATOMIC_RESULTS_PATH, "governance/atomic_chain_results.json", "atomic_chain_results"),
        ]
        supporting.extend(
            (
                root_reconciliation_path(part_id),
                f"governance/root_provenance_reconciliation_atomic/{part_id}.json",
                "root_provenance_reconciliation_not_signature",
            )
            for part_id in expected_atomic_ids
        )
        artifacts = [
            _artifact(path, f"documents/{path.name}", f"{kind}_{suffix}")
            for docx, pdf, kind, _ in generated
            for path, suffix in ((docx, "docx"), (pdf, "pdf"))
        ]
        artifacts.extend(_artifact(*row) for row in supporting)
        for path in sorted(ATOMIC_DIR.rglob("*.json")):
            artifacts.append(
                _artifact(
                    path,
                    f"governance/atomic_parts/{path.relative_to(ATOMIC_DIR).as_posix()}",
                    "controller_atomic_part_evidence",
                )
            )

        qa_paths = [REPORT_DIR / name for name in R17_QA_REPORT_NAMES]
        for path in qa_paths:
            artifacts.append(_artifact(path, f"qa/{path.name}", "qa_evidence"))
        qa_reports = [{**_file_row(path), "status": _load(path)["status"]} for path in qa_paths]
        observed = paper["observed_profile_contract"]
        manifest = {
            "schema_version": "3.0.0-r17",
            "package_id": f"{VERSION_ID}-COMPLETE-DEMO-WEEKPACK",
            "version_id": paper["version_id"],
            "content_status": "automated_verified_candidate",
            "source_scope": {
                "mode": "existing_local_page_verified_only",
                "live_acquisition_performed": False,
                "wechat_search_performed_this_run": False,
                "fact_card_path": paper["evidence_sources"][0]["path"],
                "fact_card_use": paper["evidence_sources"][0]["use"],
                "evidence_sources": [
                    {
                        key: source[key]
                        for key in ("source_kind", "evidence_id", "path", "use")
                        if key in source
                    }
                    for source in paper["evidence_sources"]
                ],
                "source_question_pixels_republished": False,
            },
            "generation_policy": paper["generation_policy"],
            "demonstration_scope": {
                "student_profile_status": "synthetic_student_profile",
                "content_status": "automated_verified_content",
                "real_student_data_used": False,
                "personal_diagnosis_claimed": False,
                "learning_effect_claimed": False,
                "content_provider": "shchem-content-provider/2.0.0",
                "content_bundle_path": _relative(CONTENT_BUNDLE_PATH),
                "content_bundle_sha256": sha256_file(CONTENT_BUNDLE_PATH),
                "catalog_item_count": len(expected_atomic_ids),
            },
            "machine_review_chain": {
                "required": ["generator_execution_provenance", "external_sol_review_a", "external_sol_review_b", "external_adversarial_check"],
                "controller_chain_path": _relative(CHAIN_PATH),
                "controller_chain_sha256": sha256_file(CHAIN_PATH),
                "controller_registry_path": registration["registry_path"],
                "controller_registry_sha256": registration["registry_sha256"],
                "registered": True,
                "expected_atomic_part_ids": expected_atomic_ids,
                "registered_atomic_chain_count": len(expected_atomic_ids),
                "post_generation_preflight": _relative(preflight_path),
                "status": "pass",
                "teacher_managed_delivery": None,
            },
            "observed_profile": {
                "profile_id": observed["profile_id"],
                "profile_version": observed["profile_version"],
                "profile_path": observed["profile_path"],
                "profile_sha256": observed["profile_sha256"],
                "evidence_manifest": observed["evidence_manifest"],
                "field_bindings": observed["field_bindings"],
                "global_template_claim_allowed": False,
                "adversarial_comparison": {
                    "profile_id": "OSV1-2024-PUTUO-ERMO-5T",
                    "role": "district_mock_comparison_only_not_primary_binding",
                    "path": "sh-chem-db/kb/shanghai_observed_standard_v1/profiles/OSV1-2024-PUTUO-ERMO-5T.json",
                },
            },
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "qa_reports": qa_reports,
            "qa_report_count": len(qa_reports),
            "external_delivery_attestation": {
                "path": _relative(PROMOTION_PATH),
                "status": "pending",
                "included_in_archive": False,
                "claim_boundary": "ZIP内manifest不自证其ZIP哈希；仅由ZIP外置sidecar在最终复验后绑定。",
            },
            "open_issues": [
                "观察 profile 为非官方回忆/retypeset exact-source-only 结构证据，不代表上海普遍标准。",
                "热点卡只提供本地逐页核验事实边界，不是正式题目。",
                "无人类审核；答案与采分点均为机器建议，不是官方采分点。",
                "中央 catalog、questions registry 与全库 release manifest 未修改；公开出版门禁关闭。",
            ],
            "human_reviewed": False,
            "teacher_managed_delivery_candidate": False,
            "delivery_scope": None,
            "teacher_action_required": False,
            "teaching_use_allowed": False,
            "official_publication_allowed": False,
            "external_publication_allowed": False,
            "publication_allowed": False,
        }
        validate(manifest, load_schema(INTEGRATION / "schemas/weekpack_manifest.schema.json"))
        manifest_contract = validate_r17_manifest_contract(
            manifest,
            expected_qa_paths=[_relative(path) for path in qa_paths],
        )
        if manifest_contract["status"] != "pass":
            raise RuntimeError(f"R17 manifest contract failed: {manifest_contract}")

        run_dir = STAGE / "package_runs" / uuid.uuid4().hex
        transaction.prepare(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_tmp = run_dir / "manifest.json"
        archive_tmp = run_dir / ARCHIVE_NAME
        write_json(manifest_tmp, manifest)
        with zipfile.ZipFile(archive_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(manifest_tmp, "manifest.json")
            for item in artifacts:
                archive.write(WORKSPACE / item["path"], item["archive_path"])
        if failure_injection == "manifest_tamper":
            tampered = _load(manifest_tmp)
            tampered["package_id"] = f"{tampered['package_id']}-TAMPERED"
            write_json(manifest_tmp, tampered)
        if failure_injection == "zip_failure":
            archive_tmp.write_bytes(b"injected-corrupt-zip")
        manifest_tmp_bytes = manifest_tmp.read_bytes()
        archive_tmp_bytes = archive_tmp.read_bytes()
        staged_zip = validate_archive(
            archive_tmp,
            _load(manifest_tmp),
            manifest_bytes=manifest_tmp_bytes,
        )
        if staged_zip.get("status") != "pass":
            raise RuntimeError(f"staged ZIP failed integrity/privacy validation: {staged_zip}")

        final_manifest = EXPORTS / "manifest.json"
        final_archive = EXPORTS / ARCHIVE_NAME
        manifest_pending = EXPORTS / ".manifest.json.pending"
        archive_pending = EXPORTS / f".{ARCHIVE_NAME}.pending"
        shutil.copy2(manifest_tmp, manifest_pending)
        shutil.copy2(archive_tmp, archive_pending)
        os.replace(manifest_pending, final_manifest)
        _assert_exact_file_bytes(
            final_manifest,
            manifest_tmp_bytes,
            label="staged/final manifest",
        )
        if failure_injection == "manifest_post_write_failure":
            raise RuntimeError("injected manifest post-write failure")
        os.replace(archive_pending, final_archive)
        _assert_exact_file_bytes(
            final_archive,
            archive_tmp_bytes,
            label="staged/final archive",
        )
        if failure_injection == "archive_post_write_failure":
            raise RuntimeError("injected archive post-write failure")
        final_manifest_bytes = final_manifest.read_bytes()
        final_zip = validate_archive(
            final_archive,
            _load(final_manifest),
            manifest_bytes=final_manifest_bytes,
        )
        if final_zip.get("status") != "pass":
            raise RuntimeError(f"final ZIP failed post-copy integrity/privacy validation: {final_zip}")
        final_zip["paper_sha256"] = sha256_file(paper_path)
        final_zip["task_card_sha256"] = sha256_file(task_path)
        final_zip["archive_bytes"] = final_archive.stat().st_size
        final_zip["staged_final_tuple"] = {
            "staged": {
                "manifest_sha256": hashlib.sha256(manifest_tmp_bytes).hexdigest(),
                "manifest_bytes": len(manifest_tmp_bytes),
                "archive_sha256": hashlib.sha256(archive_tmp_bytes).hexdigest(),
                "archive_bytes": len(archive_tmp_bytes),
            },
            "final": {
                "manifest_sha256": hashlib.sha256(final_manifest_bytes).hexdigest(),
                "manifest_bytes": len(final_manifest_bytes),
                "archive_sha256": sha256_file(final_archive),
                "archive_bytes": final_archive.stat().st_size,
            },
            "exact": True,
        }
        zip_report = REPORT_DIR / "zip_manifest.json"
        _write_report(zip_report, final_zip)
        if failure_injection == "zip_report_post_write_failure":
            raise RuntimeError("injected ZIP report post-write failure")

        promotion_snapshot, promotion_graph, promotion_inputs = (
            _promotion_prerequisite_snapshot(
            registration=registration,
            paper_path=paper_path,
            task_path=task_path,
            manifest_path=final_manifest,
            archive_path=final_archive,
            zip_report_path=zip_report,
            chain_path=CHAIN_PATH,
            preflight_path=preflight_path,
            generated=generated,
            qa_paths=qa_paths,
            parity_paths=parity_paths,
        )
        )
        if promotion_snapshot["status"] != "pass":
            raise RuntimeError(
                f"R17 promotion prerequisite snapshot failed: {promotion_snapshot}"
            )
        expected_revalidation = _expected_graph_revalidation(promotion_graph)
        promotion_snapshot["commit_revalidation"] = expected_revalidation
        paper_ref = promotion_inputs["paper"]
        task_ref = promotion_inputs["task_card"]
        manifest_ref = promotion_inputs["manifest"]
        archive_ref = promotion_inputs["archive"]
        zip_report_ref = promotion_inputs["zip_integrity_report"]
        chain_ref = promotion_inputs["governance_chain"]
        preflight_ref = promotion_inputs["post_generation_preflight"]
        promotion = {
            "schema_version": "3.0.0-r17",
            "record_type": "external_teacher_managed_delivery_attestation",
            "promotion_id": f"PROMOTION-{VERSION_ID}",
            "candidate_path": paper_ref["path"],
            "candidate_sha256": paper_ref["sha256"],
            "task_card_sha256": task_ref["sha256"],
            "promotion_chain": [
                "candidate",
                "machine_pass",
                "independent_machine_review",
                "adversarial_check",
                "automated_verified_candidate",
            ],
            "content_status": "automated_verified_candidate",
            "manifest_path": manifest_ref["path"],
            "manifest_sha256": manifest_ref["sha256"],
            "archive_path": archive_ref["path"],
            "archive_sha256": archive_ref["sha256"],
            "archive_bytes": archive_ref["bytes"],
            "zip_integrity_report_path": zip_report_ref["path"],
            "zip_integrity_report_sha256": zip_report_ref["sha256"],
            "semantic_pairs": promotion_inputs["semantic_pairs"],
            "qa_evidence": promotion_inputs["qa_evidence"],
            "promotion_prerequisite_snapshot": promotion_snapshot,
            "content_governance": {
                "controller_governance_chain_path": chain_ref["path"],
                "controller_governance_chain_sha256": chain_ref["sha256"],
                "post_generation_preflight_report_path": preflight_ref["path"],
                "post_generation_preflight_report_sha256": preflight_ref["sha256"],
                "registry_binding": promotion_inputs["registry_binding"],
            },
            "attestation_included_in_bound_archive": False,
            "human_reviewed": False,
            "teacher_managed_delivery_candidate": True,
            "delivery_scope": "teacher_managed_private_delivery",
            "teacher_action_required": True,
            "teaching_use_allowed": False,
            "official_claim_allowed": False,
            "official_publication_allowed": False,
            "external_publication_allowed": False,
            "publication_allowed": False,
            "release_allowed": False,
        }
        validate(promotion, promotion_inputs["promotion_schema"])
        promotion_bytes = _fixed_json_bytes(promotion)
        _commit_fixed_promotion_bytes(
            output_path=PROMOTION_PATH,
            promotion_bytes=promotion_bytes,
            graph=promotion_graph,
            expected_revalidation=expected_revalidation,
            remove_output_on_failure=False,
        )
        if failure_injection == "promotion_post_write_failure":
            raise RuntimeError("injected promotion post-write failure")
        status = _delivery_status_record(promotion, promotion_bytes=promotion_bytes)
        validate(status, promotion_inputs["delivery_status_schema"])
        status_bytes = _fixed_json_bytes(status)
        _commit_fixed_promotion_bytes(
            output_path=DELIVERY_STATUS_PATH,
            promotion_bytes=status_bytes,
            graph=promotion_graph,
            expected_revalidation=expected_revalidation,
            remove_output_on_failure=False,
        )
        if failure_injection == "status_post_write_failure":
            raise RuntimeError("injected status post-write failure")

        controller_observations = {
            "schema_version": "2.0.0",
            "observed_profile": _relative(DEFAULT_PROFILE),
            "governance_chain": _relative(CHAIN_PATH),
            "registration": registration,
            "generate": preflight_result,
            "figure": controller_preflight("figure", profile_path=DEFAULT_PROFILE, governance_chain=CHAIN_PATH),
            "publish_smoke": controller_preflight("publish_smoke", profile_path=DEFAULT_PROFILE, governance_chain=CHAIN_PATH),
            "interpretation": "Actual controller outputs only; content chain has no delivery claim.",
        }
        write_json(COORDINATION / "CONTROLLER_PREFLIGHT_OBSERVATIONS.json", controller_observations)
        handoff = {
            "schema_version": "2.0.0",
            "handoff_id": f"HANDOFF-{VERSION_ID}",
            "status": "automated_verified_candidate",
            "pipeline_order": [
                "staging_candidate",
                "content_only_controller_native_paper_and_atomic_chains",
                "controller_owner_exact_batch_registration",
                "post_generation_generate_preflight",
                "hierarchy_derived_registered_atomic_content_catalog_and_deterministic_selection",
                "four_docx_pdf_pairs_render_parity_privacy",
                "manifest_and_zip_two_pass_integrity",
                "external_delivery_attestation_atomic_write",
            ],
            "artifact_manifest": _relative(final_manifest),
            "archive": _relative(final_archive),
            "archive_sha256": promotion["archive_sha256"],
            "promotion": _relative(PROMOTION_PATH),
            "promotion_sha256": hashlib.sha256(promotion_bytes).hexdigest(),
            "quarantined_prior_positive_outputs": quarantined,
            "human_reviewed": False,
            "teacher_managed_delivery_candidate": True,
            "delivery_scope": "teacher_managed_private_delivery",
            "teacher_action_required": True,
            "teaching_use_allowed": False,
            "external_publication_allowed": False,
            "official_claim_allowed": False,
            "central_catalog_questions_release_manifest_modified": False,
        }
        write_json(COORDINATION / "HANDOFF.json", handoff)
        write_json(COORDINATION / "GATEWAY_CONTRACT.json", gateway_contract())
        (COORDINATION / "HANDOFF.md").write_text(
            "# Generation/publication v2 handoff\n\n"
            "内容链不含 teacher delivery；中央精确注册与 post-generation preflight 只解锁内容。\n\n"
            "四对 DOCX/PDF、全页 parity/privacy、ZIP manifest/hash 二次复验全部通过后，"
            "才在 ZIP 外原子写 delivery attestation。ZIP 内 manifest 的外部证明状态保持 pending，避免自证循环。\n\n"
            "`teaching_use_allowed=false`、`external_publication_allowed=false`、`official_claim_allowed=false`、"
            "`human_reviewed=false` 始终保留；仅外置证明允许教师本人管理的私域交付候选。\n",
            encoding="utf-8",
        )
        transaction.commit()
        print(json.dumps(handoff, ensure_ascii=False, indent=2))
    except Exception as exc:
        if transaction is not None:
            try:
                transaction.rollback()
            except Exception as rollback_exc:  # noqa: BLE001 - preserve original failure
                raise RuntimeError(
                    f"publication failed and rollback was incomplete: {rollback_exc}"
                ) from exc
        raise
