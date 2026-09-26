from __future__ import annotations

"""Fixed, local-only regression runner for immutable workbench candidates.

The public run surface accepts only a release id and an optional cancellation
event.  Command lines, executable paths, working directories and environment
values are selected inside this module from a byte-pinned recipe; callers
cannot supply argv, paths or shell text.  Every attempted run burns an
owner-only reservation for that immutable release, even when a check fails.

The runner-issued receipt intentionally still satisfies the release-control v1
receipt schema.  Its trusted verifier additionally requires the immutable
runner receipt, reservation and detailed per-check evidence stored outside the
project.  A structurally valid, self-hashed receipt without those records is
therefore not trusted for activation.
"""

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1 import (
    workbench_release_control as release_control,
)

RUNNER_ID = "SHCHEM-WORKBENCH-FIXED-REGRESSION-RUNNER-V1"
RUNNER_VERSION = "1.0.0"
RUNNER_RECIPE_SCHEMA_VERSION = "shchem.workbench.release_runner_recipe.v1"
RUN_EVIDENCE_SCHEMA_VERSION = "shchem.workbench.regression_run_evidence.v1"
CHECK_EVIDENCE_SCHEMA_VERSION = "shchem.workbench.regression_check_evidence.v1"
RESERVATION_SCHEMA_VERSION = "shchem.workbench.regression_run_reservation.v1"
ENVIRONMENT_POLICY = "minimal_allowlist_no_credentials_no_student_capabilities"
NETWORK_POLICY = "fixed_checks_have_no_external_network_step_loopback_smoke_only"
WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM = (
    "canonical-sha256-of-sorted-fixed-regression-input-descriptors-v1"
)
WORKSPACE_SOURCE_ROOTS = (
    "integrations/deeptutor_shchem_v1/**/*.py",
    "runtime/deeptutor_shchem/*.{py,json}",
    "runtime/deeptutor_shchem/overlay/*.{html,js,css,json}",
    "staging/coordination/deeptutor_gateway/tests/**/*.py",
    "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml",
    "sh-chem-db/kb/workbench/workbench_release_control_v1/*.json",
)

CONTRACT_RELATIVE_ROOT = Path("sh-chem-db/kb/workbench/workbench_release_control_v1")
RUNNER_RECIPE_FILENAME = "release_runner_recipe.json"
RUN_EVIDENCE_SCHEMA_FILENAME = "regression_run_evidence.schema.json"
CANDIDATE_ROOT_ENV = "SHCHEM_WORKBENCH_RELEASE_CANDIDATE_ROOT"

# Filled after the two contracts are finalized.  Both bytes are read and
# checked before any reservation or subprocess is created.
RUNNER_CONTRACT_FILE_SHA256 = {
    RUNNER_RECIPE_FILENAME: "49e3d5b09d34564860d78da81b2a2039559de4ab9056e853c53b659e6140350f",
    RUN_EVIDENCE_SCHEMA_FILENAME: "0817c1f35a15c6a9e60fef0aba9e0375284cdf821d9dcc3aacb8a4c40a1a2eb7",
}
RUNNER_RECIPE_SELF_SHA256 = (
    "362f27dd755d0f8cdf1b9f6f8aeeaf1cda610155cd228139206166b9a2f0ba6e"
)

# A runner recipe may change for a narrowly reviewed operational reason (for
# example, increasing the full-suite timeout after the suite grows).  The
# immediately superseded recipe remains acceptable only when validating the
# old active pointer as the compare-and-swap subject of a replacement.  It is
# never accepted for current serving, rollback, or activation of that old
# target.  Every field still has to agree across the immutable reservation and
# evidence records before this tuple is consulted.
HISTORICAL_REPLACEMENT_RUNNER_RECIPE_BINDINGS = frozenset(
    {
        (
            "64cf56a4acf9a4b51cc01916b9ccee3bbaaeaff5acfdab716b9a44c5b6e4e2eb",
            2994,
            "691df435981022961cf6bc110b6fd9beffa5296ffe37f8e676d2c9ef7099afd5",
        ),
        (
            "626b9722e030eec1897db86747d76d67ed667455ffc70c99fe78590b5ab1dd77",
            2994,
            "d9409f3e23048f842b4a9aaab045d838ef686a5fc0f3ebb500e5fd7fde3993d7",
        )
    }
)

MAX_CONTRACT_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 1024 * 1024 * 1024
SUMMARY_CHAR_LIMIT = 1200
POLL_SECONDS = 0.02
TERMINATION_SECONDS = 10.0

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^WBREL-[0-9a-f]{64}$")
_RECEIPT_ID = re.compile(r"^WBRR-[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^WBRUN-[0-9a-f]{32}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,99}$")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


@dataclass(frozen=True)
class _StepSpec:
    check_id: str
    implementation_id: str
    kind: str
    timeout_seconds: int
    output_limit_bytes_per_stream: int


class _FingerprintTrustMode(Enum):
    CURRENT_RUNTIME = "current_runtime"
    HISTORICAL_FOR_REPLACEMENT_ONLY = "historical_for_replacement_only"


FIXED_STEPS = (
    _StepSpec(
        "gateway_full_pytest",
        "current_python_gateway_full_pytest_v1",
        "subprocess",
        5400,
        2 * 1024 * 1024,
    ),
    _StepSpec(
        "central_status",
        "current_python_central_status_v1",
        "subprocess",
        180,
        2 * 1024 * 1024,
    ),
    _StepSpec(
        "central_validate",
        "current_python_central_validate_v1",
        "subprocess",
        600,
        2 * 1024 * 1024,
    ),
    _StepSpec(
        "node_check_overlay",
        "current_node_overlay_syntax_check_v1",
        "subprocess",
        120,
        1024 * 1024,
    ),
    _StepSpec(
        "openapi_contract",
        "current_python_openapi_contract_pytest_v1",
        "subprocess",
        180,
        1024 * 1024,
    ),
    _StepSpec(
        "release_closure",
        "immutable_candidate_full_readback_v1",
        "internal",
        120,
        1024 * 1024,
    ),
    _StepSpec(
        "launcher_smoke",
        "current_python_clean_launcher_and_candidate_smoke_pytest_v2",
        "subprocess",
        300,
        2 * 1024 * 1024,
    ),
)
FIXED_CHECK_IDS = tuple(step.check_id for step in FIXED_STEPS)

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


class WorkbenchReleaseRegressionError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(
    code: str, message: str, status: int = 409
) -> WorkbenchReleaseRegressionError:
    return WorkbenchReleaseRegressionError(code, message, status)


def _trusted_runner_recipe_binding(
    *,
    claimed: tuple[Any, Any, Any],
    evidence: tuple[Any, Any, Any],
    current: tuple[str, int, str],
    fingerprint_mode: _FingerprintTrustMode,
) -> tuple[str, int, str]:
    """Select a recipe binding without widening current-runtime trust."""

    if (
        claimed != evidence
        or not _SHA256.fullmatch(str(claimed[0]))
        or isinstance(claimed[1], bool)
        or not isinstance(claimed[1], int)
        or claimed[1] <= 0
        or not _SHA256.fullmatch(str(claimed[2]))
    ):
        raise _error(
            "regression_runner_recipe_binding_mismatch",
            "runner recipe binding is inconsistent",
        )
    normalized = (str(claimed[0]), claimed[1], str(claimed[2]))
    if fingerprint_mode is _FingerprintTrustMode.CURRENT_RUNTIME:
        if normalized != current:
            raise _error(
                "regression_runner_recipe_binding_mismatch",
                "current runtime requires the current runner recipe",
            )
        return current
    if fingerprint_mode is _FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY:
        if normalized == current or (
            normalized in HISTORICAL_REPLACEMENT_RUNNER_RECIPE_BINDINGS
        ):
            return normalized
        raise _error(
            "regression_historical_recipe_untrusted",
            "historical runner recipe is not allowlisted for replacement",
        )
    raise _error(
        "regression_fingerprint_mode_invalid",
        "regression fingerprint trust mode is invalid",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp is not UTC")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_bytes(value: Mapping[str, Any]) -> bytes:
    return release_control.canonical_json_bytes(dict(value)) + b"\n"


def _with_self_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("self_sha256", None)
    result["self_sha256"] = release_control.canonical_json_sha256(result)
    return result


def _self_hash(value: Mapping[str, Any]) -> str:
    projected = dict(value)
    projected.pop("self_sha256", None)
    return release_control.canonical_json_sha256(projected)


def _strict_record(raw: bytes, label: str) -> dict[str, Any]:
    value = release_control._parse_canonical_record(raw, label)
    if _record_bytes(value) != raw:
        raise _error("regression_evidence_not_canonical", f"{label} is not canonical")
    return value


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.absolute()))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _assert_external_root(release_root: Path, project_root: Path) -> None:
    actual_project_root = Path(__file__).resolve().parents[2]
    try:
        supplied = project_root.resolve(strict=True)
    except OSError as exc:
        raise _error(
            "regression_project_root_invalid",
            "project root is unavailable",
            400,
        ) from exc
    if os.path.normcase(str(supplied)) != os.path.normcase(str(actual_project_root)):
        raise _error(
            "regression_project_root_identity_mismatch",
            "project root must be the workspace that owns the fixed runner",
            403,
        )
    release_control._assert_components_not_reparse(release_root)
    release_control._assert_components_not_reparse(actual_project_root)
    if release_control._paths_overlap(release_root, actual_project_root):
        raise _error(
            "regression_store_not_external",
            "regression evidence must remain outside the project",
            403,
        )


class _RunnerStorage:
    def __init__(self, release_root: str | Path, *, project_root: str | Path) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.release_root = Path(release_root).absolute()
        _assert_external_root(self.release_root, Path(project_root))
        self.contract_root = self.project_root / CONTRACT_RELATIVE_ROOT
        self.root = self.release_root / "fixed-regression-runner-v1"
        self.reservations_root = self.root / "reservations"
        self.receipts_root = self.root / "issued-receipts"
        self.evidence_root = self.root / "run-evidence"
        self.lock_path = self.root / ".single-inflight.lock"
        self._ensure()
        (
            self.runner_recipe,
            self.runner_recipe_binding,
            self.evidence_validator,
        ) = self._load_contracts()
        self.receipt_validator = self._load_release_control_schema(
            "regression_receipt.schema.json"
        )
        self.candidate_validator = self._load_release_control_schema(
            "release_candidate.schema.json"
        )

    def _ensure(self) -> None:
        release_control._mkdir_secure(self.release_root, parents=True, exist_ok=True)
        release_control._mkdir_secure(self.root, parents=False, exist_ok=True)
        for path in (
            self.reservations_root,
            self.receipts_root,
            self.evidence_root,
        ):
            release_control._mkdir_secure(path, parents=False, exist_ok=True)
        for path in (
            self.release_root,
            self.root,
            self.reservations_root,
            self.receipts_root,
            self.evidence_root,
        ):
            release_control._assert_owner_only(path, directory=True)

    def _load_contracts(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], Draft202012Validator]:
        recipe_path = self.contract_root / RUNNER_RECIPE_FILENAME
        recipe_raw = release_control._read_file_stable(
            recipe_path,
            maximum_bytes=MAX_CONTRACT_BYTES,
            require_owner_only=False,
        )
        if _sha256(recipe_raw) != RUNNER_CONTRACT_FILE_SHA256[RUNNER_RECIPE_FILENAME]:
            raise _error(
                "regression_runner_recipe_hash_mismatch",
                "fixed runner recipe drifted",
            )
        recipe = release_control._strict_json_object(recipe_raw, "fixed runner recipe")
        expected_steps = [
            {
                "check_id": step.check_id,
                "implementation_id": step.implementation_id,
                "kind": step.kind,
                "timeout_seconds": step.timeout_seconds,
                "output_limit_bytes_per_stream": step.output_limit_bytes_per_stream,
            }
            for step in FIXED_STEPS
        ]
        expected_keys = {
            "schema_version",
            "runner_id",
            "runner_version",
            "execution_policy",
            "execution_implemented",
            "steps",
            "environment_policy",
            "network_policy",
            "workspace_source_fingerprint_algorithm",
            "workspace_source_roots",
            "single_inflight",
            "same_release_rerun_allowed",
            "automatic_activation",
            "client_supplied_commands_allowed",
            "model_invocation_required",
            "api_key_access_allowed",
            "student_private_domain_access_allowed",
            "human_review_claim_allowed",
            "teaching_use_allowed",
            "publication_allowed",
            "self_sha256",
        }
        if (
            set(recipe) != expected_keys
            or recipe.get("schema_version") != RUNNER_RECIPE_SCHEMA_VERSION
            or recipe.get("runner_id") != RUNNER_ID
            or recipe.get("runner_version") != RUNNER_VERSION
            or recipe.get("execution_policy")
            != "fixed_allowlist_no_client_argv_path_or_shell"
            or recipe.get("execution_implemented") is not True
            or recipe.get("steps") != expected_steps
            or recipe.get("environment_policy") != ENVIRONMENT_POLICY
            or recipe.get("network_policy") != NETWORK_POLICY
            or recipe.get("workspace_source_fingerprint_algorithm")
            != WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
            or recipe.get("workspace_source_roots") != list(WORKSPACE_SOURCE_ROOTS)
            or recipe.get("single_inflight") is not True
            or recipe.get("same_release_rerun_allowed") is not False
            or recipe.get("automatic_activation") is not False
            or recipe.get("client_supplied_commands_allowed") is not False
            or recipe.get("model_invocation_required") is not False
            or recipe.get("api_key_access_allowed") is not False
            or recipe.get("student_private_domain_access_allowed") is not False
            or recipe.get("human_review_claim_allowed") is not False
            or recipe.get("teaching_use_allowed") is not False
            or recipe.get("publication_allowed") is not False
            or recipe.get("self_sha256") != RUNNER_RECIPE_SELF_SHA256
            or _self_hash(recipe) != RUNNER_RECIPE_SELF_SHA256
        ):
            raise _error(
                "regression_runner_recipe_invalid", "fixed runner recipe is invalid"
            )

        schema_path = self.contract_root / RUN_EVIDENCE_SCHEMA_FILENAME
        schema_raw = release_control._read_file_stable(
            schema_path,
            maximum_bytes=MAX_CONTRACT_BYTES,
            require_owner_only=False,
        )
        if (
            _sha256(schema_raw)
            != RUNNER_CONTRACT_FILE_SHA256[RUN_EVIDENCE_SCHEMA_FILENAME]
        ):
            raise _error(
                "regression_evidence_schema_hash_mismatch",
                "regression evidence schema drifted",
            )
        schema = release_control._strict_json_object(schema_raw, "run evidence schema")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise _error(
                "regression_evidence_schema_invalid",
                "regression evidence schema is invalid",
            ) from exc
        binding = {
            "file_sha256": _sha256(recipe_raw),
            "file_bytes": len(recipe_raw),
            "self_sha256": RUNNER_RECIPE_SELF_SHA256,
        }
        return recipe, binding, Draft202012Validator(schema)

    def _load_release_control_schema(
        self, filename: str
    ) -> Draft202012Validator:
        expected = release_control.CONTRACT_FILE_SHA256.get(filename)
        if expected is None:
            raise _error(
                "regression_release_schema_unknown",
                "fixed release schema is not registered",
            )
        raw = release_control._read_file_stable(
            self.contract_root / filename,
            maximum_bytes=MAX_CONTRACT_BYTES,
            require_owner_only=False,
        )
        if _sha256(raw) != expected:
            raise _error(
                "regression_release_schema_hash_mismatch",
                "fixed release schema drifted",
            )
        schema = release_control._strict_json_object(raw, filename)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise _error(
                "regression_release_schema_invalid",
                "fixed release schema is invalid",
            ) from exc
        return Draft202012Validator(schema)

    def read_result(self, receipt_id: str) -> dict[str, Any]:
        """Read one completed immutable result without exposing raw output bytes."""

        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error("regression_receipt_id_invalid", "receipt id is invalid", 400)
        receipt_raw = release_control._read_file_stable(
            self.receipts_root / f"{receipt_id}.json",
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        receipt = _strict_record(receipt_raw, "issued runner receipt")
        evidence_raw = release_control._read_file_stable(
            self.evidence_root / f"{receipt_id}.json",
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        evidence = _strict_record(evidence_raw, "fixed regression run evidence")
        release_id = receipt.get("release_id")
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error(
                "regression_result_binding_mismatch",
                "completed regression release id is invalid",
            )
        reservation_raw = release_control._read_file_stable(
            self.reservations_root / f"{release_id}.json",
            maximum_bytes=MAX_CONTRACT_BYTES,
        )
        reservation = _strict_record(reservation_raw, "fixed regression reservation")
        errors = sorted(
            self.evidence_validator.iter_errors(evidence),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if (
            errors
            or evidence.get("receipt_id") != receipt_id
            or evidence.get("receipt_sha256") != _sha256(receipt_raw)
            or evidence.get("receipt_bytes") != len(receipt_raw)
            or evidence.get("release_id") != receipt.get("release_id")
            or evidence.get("verdict") != receipt.get("verdict")
            or evidence.get("reservation_sha256") != _sha256(reservation_raw)
            or evidence.get("reservation_bytes") != len(reservation_raw)
            or reservation.get("schema_version") != RESERVATION_SCHEMA_VERSION
            or reservation.get("release_id") != release_id
            or reservation.get("receipt_id") != receipt_id
            or reservation.get("run_id") != evidence.get("run_id")
            or reservation.get("candidate_manifest_sha256")
            != receipt.get("candidate_manifest_sha256")
            or reservation.get("candidate_manifest_bytes")
            != receipt.get("candidate_manifest_bytes")
        ):
            raise _error(
                "regression_result_binding_mismatch",
                "completed regression result binding mismatched",
            )
        trusted_pass_current = False
        if receipt.get("verdict") == "PASS":
            verifier = WorkbenchReleaseRegressionVerifier(
                self.release_root, project_root=self.project_root
            )
            trusted_pass_current = verifier(receipt)
        return {
            "run_id": evidence["run_id"],
            "receipt_id": receipt_id,
            "release_id": evidence["release_id"],
            "state": "completed",
            "verdict": evidence["verdict"],
            "started_at": evidence["started_at"],
            "completed_at": evidence["completed_at"],
            "checks": [
                {
                    "check_id": check["check_id"],
                    "status": check["status"],
                    "exit_code": check["exit_code"],
                    "duration_ms": check["duration_ms"],
                    "failure_code": check["failure_code"],
                    "stdout_safe_summary": check["stdout"]["safe_summary"],
                    "stderr_safe_summary": check["stderr"]["safe_summary"],
                    "stdout_sha256": check["stdout"]["sha256"],
                    "stdout_bytes": check["stdout"]["bytes_observed"],
                    "stderr_sha256": check["stderr"]["sha256"],
                    "stderr_bytes": check["stderr"]["bytes_observed"],
                }
                for check in evidence["checks"]
            ],
            "receipt": receipt,
            "trusted_pass_current": trusted_pass_current,
            "cancellable": False,
            "automatic_activation": False,
            "model_invoked": False,
            "api_key_accessed": False,
            "student_private_domain_accessed": False,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }

    def status_for_release(self, release_id: str) -> dict[str, Any]:
        """Read the cross-restart reservation/result ledger for one release."""

        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("regression_release_id_invalid", "release id is invalid", 400)
        reservation_path = self.reservations_root / f"{release_id}.json"
        if not os.path.lexists(reservation_path):
            return {
                "release_id": release_id,
                "state": "not_started",
                "cancellable": False,
                "automatic_activation": False,
            }
        reservation_raw = release_control._read_file_stable(
            reservation_path, maximum_bytes=MAX_CONTRACT_BYTES
        )
        reservation = _strict_record(reservation_raw, "fixed regression reservation")
        receipt_id = reservation.get("receipt_id")
        run_id = reservation.get("run_id")
        if (
            reservation.get("schema_version") != RESERVATION_SCHEMA_VERSION
            or reservation.get("release_id") != release_id
            or not isinstance(receipt_id, str)
            or not _RECEIPT_ID.fullmatch(receipt_id)
            or not isinstance(run_id, str)
            or not _RUN_ID.fullmatch(run_id)
            or reservation.get("rerun_allowed") is not False
        ):
            raise _error(
                "regression_reservation_untrusted",
                "cross-restart reservation is invalid",
            )
        receipt_exists = os.path.lexists(self.receipts_root / f"{receipt_id}.json")
        evidence_exists = os.path.lexists(self.evidence_root / f"{receipt_id}.json")
        if receipt_exists and evidence_exists:
            result = self.read_result(receipt_id)
            if result["run_id"] != run_id or result["release_id"] != release_id:
                raise _error(
                    "regression_result_binding_mismatch",
                    "cross-restart result identity mismatched",
                )
            return result
        return {
            "run_id": run_id,
            "receipt_id": receipt_id,
            "release_id": release_id,
            "state": (
                "reserved_incomplete_or_running"
                if not receipt_exists and not evidence_exists
                else "failed_closed_incomplete_evidence"
            ),
            "reserved_at": reservation.get("reserved_at"),
            "receipt_committed": receipt_exists,
            "evidence_committed": evidence_exists,
            "cancellable": False,
            "rerun_allowed": False,
            "automatic_activation": False,
        }

    def retrieve_receipt(self, run_id: str) -> dict[str, Any]:
        """Resolve a persisted receipt by runner id; no caller receipt is used."""

        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise _error("regression_run_id_invalid", "run id is invalid", 400)
        files, directories = release_control._tree_inventory(self.reservations_root)
        if directories or any(
            not _RELEASE_ID.fullmatch(Path(relative).stem)
            or Path(relative).suffix != ".json"
            or "/" in relative
            for relative in files
        ):
            raise _error(
                "regression_reservation_inventory_unsafe",
                "reservation ledger contains an unexpected entry",
                403,
            )
        matches: list[dict[str, Any]] = []
        for relative in sorted(files):
            raw = release_control._read_file_stable(
                self.reservations_root / relative,
                maximum_bytes=MAX_CONTRACT_BYTES,
            )
            reservation = _strict_record(raw, "fixed regression reservation")
            if reservation.get("run_id") == run_id:
                matches.append(reservation)
        if not matches:
            raise _error(
                "regression_run_not_found",
                "persisted regression run was not found",
                404,
            )
        if len(matches) != 1:
            raise _error(
                "regression_run_identity_collision",
                "persisted regression run id collided",
            )
        reservation = matches[0]
        receipt_id = reservation.get("receipt_id")
        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error(
                "regression_reservation_untrusted",
                "persisted regression receipt id is invalid",
            )
        result = self.read_result(receipt_id)
        if result["run_id"] != run_id:
            raise _error(
                "regression_result_binding_mismatch",
                "persisted regression run binding mismatched",
            )
        return result["receipt"]

    @contextlib.contextmanager
    def single_inflight(self) -> Iterator[None]:
        self._ensure()
        thread_lock = _thread_lock(self.lock_path)
        if not thread_lock.acquire(blocking=False):
            raise _error(
                "regression_run_in_progress",
                "another fixed regression run is already in progress",
                409,
            )
        descriptor: int | None = None
        acquired = False
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
            descriptor = os.open(self.lock_path, flags, 0o600)
            details = os.fstat(descriptor)
            path_details = self.lock_path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or getattr(details, "st_nlink", 1) != 1
                or release_control._is_reparse_or_symlink(path_details)
                or not os.path.samestat(details, path_details)
            ):
                raise _error(
                    "regression_runner_lock_unsafe",
                    "fixed runner lock is unsafe",
                    403,
                )
            if details.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            release_control._apply_owner_only_permissions(
                self.lock_path, directory=False
            )
            release_control._assert_owner_only(self.lock_path, directory=False)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise _error(
                    "regression_run_in_progress",
                    "another fixed regression run is already in progress",
                    409,
                ) from exc
            yield
        finally:
            if descriptor is not None:
                if acquired:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            thread_lock.release()


def _minimal_environment() -> dict[str, str]:
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "LANG",
        "LC_ALL",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        }
    )
    for key in tuple(result):
        upper = key.upper()
        if (
            "KEY" in upper
            or "TOKEN" in upper
            or "SECRET" in upper
            or "PASSWORD" in upper
            or "STUDENT" in upper
            or upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
        ):
            result.pop(key, None)
    return result


def _safe_summary(raw: bytes, project_root: Path) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = "".join(
        character if character in "\n\r\t" or ord(character) >= 32 else "�"
        for character in text
    )
    replacements = (
        (str(project_root), "<WORKSPACE>"),
        (str(Path.home()), "<HOME>"),
        (os.environ.get("TEMP", ""), "<TEMP>"),
        (os.environ.get("TMP", ""), "<TEMP>"),
    )
    for source, target in replacements:
        if source:
            text = text.replace(source, target)
            text = text.replace(source.replace("\\", "/"), target)
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: match.group(1) + "<REDACTED>", text)
        else:
            text = pattern.sub("<REDACTED>", text)
    if len(text) <= SUMMARY_CHAR_LIMIT:
        return text
    half = (SUMMARY_CHAR_LIMIT - len("\n…<TRUNCATED>…\n")) // 2
    return text[:half] + "\n…<TRUNCATED>…\n" + text[-half:]


class _StreamCapture:
    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self.hasher = hashlib.sha256()
        self.observed = 0
        self.captured = bytearray()
        self.exceeded = threading.Event()
        self.error: BaseException | None = None

    def consume(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                self.hasher.update(chunk)
                self.observed += len(chunk)
                remaining = self.maximum_bytes - len(self.captured)
                if remaining > 0:
                    self.captured.extend(chunk[:remaining])
                if self.observed > self.maximum_bytes:
                    self.exceeded.set()
        except (
            OSError,
            ValueError,
        ) as exc:  # pragma: no cover - defensive pipe failure
            self.error = exc
            self.exceeded.set()
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def record(self, project_root: Path) -> dict[str, Any]:
        raw = bytes(self.captured)
        return {
            "sha256": self.hasher.hexdigest(),
            "bytes_observed": self.observed,
            "bytes_captured": len(raw),
            "truncated": self.observed > len(raw),
            "safe_summary": _safe_summary(raw, project_root),
        }


def _bytes_stream(raw: bytes, project_root: Path) -> dict[str, Any]:
    return {
        "sha256": _sha256(raw),
        "bytes_observed": len(raw),
        "bytes_captured": len(raw),
        "truncated": False,
        "safe_summary": _safe_summary(raw, project_root),
    }


def _empty_stream(project_root: Path) -> dict[str, Any]:
    return _bytes_stream(b"", project_root)


def _hash_stable_executable(path: Path) -> tuple[str, int, str]:
    resolved = path.resolve(strict=True)
    before = resolved.lstat()
    if (
        release_control._is_reparse_or_symlink(before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > MAX_EXECUTABLE_BYTES
    ):
        raise _error(
            "regression_executable_unsafe", "fixed executable is unavailable or unsafe"
        )
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if not os.path.samestat(before, opened):
            raise _error(
                "regression_executable_drift", "fixed executable identity drifted"
            )
        hasher = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EXECUTABLE_BYTES:
                raise _error(
                    "regression_executable_unsafe", "fixed executable is too large"
                )
            hasher.update(chunk)
        after_descriptor = os.fstat(descriptor)
        after_path = resolved.lstat()
        if (
            not os.path.samestat(after_descriptor, after_path)
            or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            != (
                after_descriptor.st_size,
                after_descriptor.st_mtime_ns,
                after_descriptor.st_ctime_ns,
            )
            or total != opened.st_size
        ):
            raise _error(
                "regression_executable_drift", "fixed executable drifted during read"
            )
        return (
            hasher.hexdigest(),
            total,
            _sha256(str(resolved).encode("utf-8")),
        )
    finally:
        os.close(descriptor)


def _fixed_command(
    step: _StepSpec, project_root: Path
) -> tuple[list[str], list[str], Path]:
    python = Path(sys.executable).resolve(strict=True)
    pytest_prefix = [
        str(python),
        "-B",
        "-X",
        "utf8",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    display_pytest_prefix = [
        "<PYTHON_CURRENT>",
        "-B",
        "-X",
        "utf8",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if step.check_id == "gateway_full_pytest":
        suffix = ["staging/coordination/deeptutor_gateway/tests"]
        return pytest_prefix + suffix, display_pytest_prefix + suffix, python
    if step.check_id in {"central_status", "central_validate"}:
        action = "status" if step.check_id == "central_status" else "validate"
        suffix = [
            "-B",
            "-X",
            "utf8",
            "sh-chem-db/scripts/sh_chem_agent.py",
            action,
            "--root",
            "sh-chem-db",
        ]
        return [str(python), *suffix], ["<PYTHON_CURRENT>", *suffix], python
    if step.check_id == "node_check_overlay":
        node_value = shutil.which("node")
        if not node_value:
            raise _error(
                "regression_node_unavailable",
                "the fixed Node syntax checker is unavailable",
            )
        node = Path(node_value).resolve(strict=True)
        suffix = ["--check", "runtime/deeptutor_shchem/overlay/app.js"]
        return [str(node), *suffix], ["<NODE_CURRENT>", *suffix], node
    if step.check_id == "openapi_contract":
        suffix = [
            "staging/coordination/deeptutor_gateway/tests/test_gateway.py::ConfigAndContractTests::test_openapi_contract_contains_required_operations"
        ]
        return pytest_prefix + suffix, display_pytest_prefix + suffix, python
    if step.check_id == "launcher_smoke":
        suffix = [
            "staging/coordination/deeptutor_gateway/tests/test_launcher.py::test_detached_launcher_home_status_drift_and_clean_stop",
            "staging/coordination/deeptutor_gateway/tests/test_workbench_release_gateway_api.py::test_fixed_runner_candidate_frozen_http_smoke",
        ]
        return pytest_prefix + suffix, display_pytest_prefix + suffix, python
    raise _error(
        "regression_fixed_step_unknown", "fixed step has no internal command binding"
    )


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=TERMINATION_SECONDS,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(process.pid, 15)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=TERMINATION_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(process.pid, 9)
            else:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=TERMINATION_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _execute_subprocess_step(
    step: _StepSpec,
    sequence: int,
    project_root: Path,
    cancel_event: threading.Event,
    *,
    candidate_root: Path | None = None,
) -> dict[str, Any]:
    if cancel_event.is_set():
        return _make_unexecuted_check(
            step, sequence, project_root, "cancelled", f"{step.check_id}_cancelled"
        )
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    stdout_capture = _StreamCapture(step.output_limit_bytes_per_stream)
    stderr_capture = _StreamCapture(step.output_limit_bytes_per_stream)
    command_record: dict[str, Any] | None = None
    process: subprocess.Popen[bytes] | None = None
    stdout_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    status = "spawn_error"
    failure_code: str | None = f"{step.check_id}_spawn_error"
    exit_code: int | None = None
    try:
        argv, display_argv, executable = _fixed_command(step, project_root)
        executable_sha, executable_bytes, executable_path_sha = _hash_stable_executable(
            executable
        )
        environment = _minimal_environment()
        if step.check_id == "launcher_smoke":
            if candidate_root is None:
                raise _error(
                    "regression_candidate_smoke_binding_missing",
                    "fixed launcher smoke has no immutable candidate binding",
                )
            resolved_candidate = candidate_root.resolve(strict=True)
            if (
                not resolved_candidate.is_dir()
                or not (resolved_candidate / "browse-closure").is_dir()
            ):
                raise _error(
                    "regression_candidate_smoke_binding_invalid",
                    "fixed launcher smoke candidate binding is invalid",
                )
            environment[CANDIDATE_ROOT_ENV] = str(resolved_candidate)
        command_record = {
            "argv": display_argv,
            "cwd": "<WORKSPACE>",
            "shell": False,
            "executable_sha256": executable_sha,
            "executable_bytes": executable_bytes,
            "executable_path_sha256": executable_path_sha,
            "environment_policy": ENVIRONMENT_POLICY,
            "environment_names": sorted(key.upper() for key in environment),
        }
        creationflags = 0
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            argv,
            cwd=project_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
            **popen_kwargs,
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_thread = threading.Thread(
            target=stdout_capture.consume,
            args=(process.stdout,),
            name=f"{step.check_id}-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.consume,
            args=(process.stderr,),
            name=f"{step.check_id}-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = started_monotonic + step.timeout_seconds
        forced_status: str | None = None
        while process.poll() is None:
            if cancel_event.is_set():
                forced_status = "cancelled"
                break
            if stdout_capture.exceeded.is_set() or stderr_capture.exceeded.is_set():
                forced_status = "output_limit"
                break
            if time.monotonic() >= deadline:
                forced_status = "timeout"
                break
            time.sleep(POLL_SECONDS)
        if forced_status is not None:
            _terminate_process_tree(process)
        exit_code = process.poll()
        if exit_code is None:
            _terminate_process_tree(process)
            exit_code = process.poll()
        if stdout_thread is not None:
            stdout_thread.join(timeout=TERMINATION_SECONDS)
        if stderr_thread is not None:
            stderr_thread.join(timeout=TERMINATION_SECONDS)
        if (
            stdout_capture.exceeded.is_set()
            or stderr_capture.exceeded.is_set()
            or stdout_capture.error is not None
            or stderr_capture.error is not None
        ):
            forced_status = forced_status or "output_limit"
        if forced_status is not None:
            status = forced_status
            failure_code = f"{step.check_id}_{forced_status}"
        elif exit_code == 0:
            status = "pass"
            failure_code = None
        else:
            status = "fail"
            failure_code = f"{step.check_id}_failed"
    except (OSError, ValueError, WorkbenchReleaseRegressionError) as exc:
        if process is not None:
            _terminate_process_tree(process)
            exit_code = process.poll()
        message = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
        stderr_capture.hasher.update(message)
        stderr_capture.observed += len(message)
        stderr_capture.captured.extend(message[: step.output_limit_bytes_per_stream])
        if isinstance(exc, WorkbenchReleaseRegressionError):
            failure_code = exc.code
        status = "spawn_error"
    completed_at = _utc_now()
    duration_ms = min(
        int(max(0.0, time.monotonic() - started_monotonic) * 1000),
        86_400_000,
    )
    return _with_self_hash(
        {
            "schema_version": CHECK_EVIDENCE_SCHEMA_VERSION,
            "check_id": step.check_id,
            "sequence": sequence,
            "implementation_id": step.implementation_id,
            "kind": step.kind,
            "actually_executed": process is not None,
            "command": command_record,
            "status": status,
            "exit_code": exit_code,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "stdout": stdout_capture.record(project_root),
            "stderr": stderr_capture.record(project_root),
            "failure_code": failure_code,
        }
    )


def _make_unexecuted_check(
    step: _StepSpec,
    sequence: int,
    project_root: Path,
    status: str,
    failure_code: str,
) -> dict[str, Any]:
    return _with_self_hash(
        {
            "schema_version": CHECK_EVIDENCE_SCHEMA_VERSION,
            "check_id": step.check_id,
            "sequence": sequence,
            "implementation_id": step.implementation_id,
            "kind": step.kind,
            "actually_executed": False,
            "command": None,
            "status": status,
            "exit_code": None,
            "started_at": None,
            "completed_at": None,
            "duration_ms": 0,
            "stdout": _empty_stream(project_root),
            "stderr": _empty_stream(project_root),
            "failure_code": failure_code,
        }
    )


def _execute_internal_closure_step(
    step: _StepSpec,
    sequence: int,
    project_root: Path,
    store: release_control.WorkbenchReleaseStore,
    expected_candidate: Mapping[str, Any],
    cancel_event: threading.Event,
) -> dict[str, Any]:
    if cancel_event.is_set():
        return _make_unexecuted_check(
            step, sequence, project_root, "cancelled", f"{step.check_id}_cancelled"
        )
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    status = "pass"
    failure_code: str | None = None
    exit_code: int | None = 0
    stdout_raw = b""
    stderr_raw = b""
    try:
        observed = store.read_candidate(str(expected_candidate["release_id"]))
        if (
            observed.get("manifest_sha256") != expected_candidate.get("manifest_sha256")
            or observed.get("manifest_bytes")
            != expected_candidate.get("manifest_bytes")
            or observed.get("manifest") != expected_candidate.get("manifest")
        ):
            raise _error(
                "release_closure_binding_mismatch",
                "candidate changed during fixed regression",
            )
        stdout_raw = release_control.canonical_json_bytes(
            {
                "release_id": observed["release_id"],
                "manifest_sha256": observed["manifest_sha256"],
                "manifest_bytes": observed["manifest_bytes"],
                "artifact_count": observed["manifest"]["browse_closure"][
                    "artifact_count"
                ],
                "closure_sha256": observed["manifest"]["browse_closure"][
                    "closure_sha256"
                ],
                "full_readback": True,
            }
        )
    except (
        WorkbenchReleaseRegressionError,
        release_control.WorkbenchReleaseControlError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
    ) as exc:
        status = "fail"
        exit_code = 1
        failure_code = (
            exc.code
            if isinstance(
                exc,
                (
                    WorkbenchReleaseRegressionError,
                    release_control.WorkbenchReleaseControlError,
                ),
            )
            else "release_closure_failed"
        )
        stderr_raw = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
    completed_at = _utc_now()
    return _with_self_hash(
        {
            "schema_version": CHECK_EVIDENCE_SCHEMA_VERSION,
            "check_id": step.check_id,
            "sequence": sequence,
            "implementation_id": step.implementation_id,
            "kind": step.kind,
            "actually_executed": True,
            "command": None,
            "status": status,
            "exit_code": exit_code,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": min(
                int(max(0.0, time.monotonic() - started_monotonic) * 1000),
                86_400_000,
            ),
            "stdout": _bytes_stream(stdout_raw, project_root),
            "stderr": _bytes_stream(stderr_raw, project_root),
            "failure_code": failure_code,
        }
    )


def _runner_source_sha256() -> str:
    raw = release_control._read_file_stable(
        Path(__file__).resolve(),
        maximum_bytes=4 * 1024 * 1024,
        require_owner_only=False,
    )
    return _sha256(raw)


def _workspace_source_fingerprint(project_root: Path) -> dict[str, Any]:
    """Hash the closed, code-owned inputs exercised by the fixed recipe."""

    candidates: set[Path] = set()
    integration_root = project_root / "integrations/deeptutor_shchem_v1"
    candidates.update(integration_root.rglob("*.py"))
    runtime_root = project_root / "runtime/deeptutor_shchem"
    runtime_suffixes = {".py", ".html", ".js", ".css", ".json"}
    candidates.update(
        path
        for path in runtime_root.glob("*")
        if path.suffix.lower() in runtime_suffixes
    )
    overlay_root = runtime_root / "overlay"
    candidates.update(
        path
        for path in overlay_root.glob("*")
        if path.suffix.lower() in runtime_suffixes
    )
    tests_root = project_root / "staging/coordination/deeptutor_gateway/tests"
    candidates.update(tests_root.rglob("*.py"))
    candidates.add(
        project_root
        / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
    )
    candidates.update((project_root / CONTRACT_RELATIVE_ROOT).glob("*.json"))
    descriptors: list[dict[str, Any]] = []
    for path in sorted(
        candidates,
        key=lambda value: value.relative_to(project_root).as_posix(),
    ):
        relative = path.relative_to(project_root).as_posix()
        raw = release_control._read_file_stable(
            path,
            maximum_bytes=64 * 1024 * 1024,
            require_owner_only=False,
        )
        descriptors.append(
            {"path": relative, "sha256": _sha256(raw), "bytes": len(raw)}
        )
    if not descriptors or len(descriptors) > 10_000:
        raise _error(
            "regression_workspace_source_inventory_invalid",
            "fixed regression source inventory is empty or excessive",
        )
    subject = {
        "algorithm": WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM,
        "files": descriptors,
    }
    return {
        "algorithm": WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM,
        "sha256": release_control.canonical_json_sha256(subject),
        "file_count": len(descriptors),
    }


class WorkbenchReleaseRegressionVerifier(_RunnerStorage):
    """Callable trusted verifier suitable for ``WorkbenchReleaseStore``."""

    def __call__(self, receipt: Mapping[str, Any]) -> bool:
        try:
            self.verify_or_raise(receipt)
        except Exception:  # noqa: BLE001 - trust boundary must return false
            return False
        return True

    def verify_or_raise(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Verify a PASS receipt against the currently executing code bytes."""

        return self._verify_issued_receipt_or_raise(
            receipt, _FingerprintTrustMode.CURRENT_RUNTIME
        )

    def verify_historical_for_replacement_or_raise(
        self, receipt: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Verify an old head solely as the CAS subject of a safe replacement.

        This projection never authorizes serving, rollback, activation of the
        historical target, or any teaching use.  The issued receipt, detailed
        evidence, reservation, candidate manifest and fixed seven-step safety
        contract remain byte- and hash-bound.  Only the code-generation
        fingerprints may be historical, and even then the reservation and
        evidence must agree exactly with one another.
        """

        return self._verify_issued_receipt_or_raise(
            receipt, _FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY
        )

    def _verify_issued_receipt_or_raise(
        self,
        receipt: Mapping[str, Any],
        fingerprint_mode: _FingerprintTrustMode,
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping):
            raise _error("regression_receipt_untrusted", "receipt must be an object")
        normalized = dict(receipt)
        receipt_raw = _record_bytes(normalized)
        receipt_errors = sorted(
            self.receipt_validator.iter_errors(normalized),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if receipt_errors:
            raise _error(
                "regression_receipt_contract_failed",
                "issued receipt does not satisfy its fixed contract",
            )
        if normalized.get("self_sha256") != _self_hash(normalized):
            raise _error("regression_receipt_untrusted", "receipt self hash mismatched")
        receipt_id = normalized.get("receipt_id")
        release_id = normalized.get("release_id")
        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error("regression_receipt_untrusted", "receipt id is invalid")
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("regression_receipt_untrusted", "release id is invalid")
        candidate_manifest_path = (
            self.release_root
            / "candidates"
            / release_id
            / "release.manifest.json"
        )
        candidate_manifest_raw = release_control._read_file_stable(
            candidate_manifest_path,
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        candidate_manifest = _strict_record(
            candidate_manifest_raw, "immutable release candidate manifest"
        )
        candidate_errors = sorted(
            self.candidate_validator.iter_errors(candidate_manifest),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if (
            candidate_errors
            or candidate_manifest.get("self_sha256") != _self_hash(candidate_manifest)
            or candidate_manifest.get("release_id") != release_id
            or normalized.get("candidate_manifest_sha256")
            != _sha256(candidate_manifest_raw)
            or normalized.get("candidate_manifest_bytes")
            != len(candidate_manifest_raw)
        ):
            raise _error(
                "regression_candidate_manifest_untrusted",
                "issued receipt candidate manifest binding mismatched",
            )
        stored_receipt_raw = release_control._read_file_stable(
            self.receipts_root / f"{receipt_id}.json",
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        if stored_receipt_raw != receipt_raw:
            raise _error(
                "regression_receipt_untrusted", "issued receipt bytes mismatched"
            )
        stored_receipt = _strict_record(stored_receipt_raw, "issued runner receipt")
        if stored_receipt != normalized:
            raise _error("regression_receipt_untrusted", "issued receipt drifted")

        evidence_raw = release_control._read_file_stable(
            self.evidence_root / f"{receipt_id}.json",
            maximum_bytes=MAX_EVIDENCE_BYTES,
        )
        evidence = _strict_record(evidence_raw, "fixed regression run evidence")
        errors = sorted(
            self.evidence_validator.iter_errors(evidence),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise _error(
                "regression_evidence_contract_failed",
                "fixed regression evidence does not satisfy its contract",
            )
        if evidence.get("self_sha256") != _self_hash(evidence):
            raise _error(
                "regression_evidence_untrusted", "run evidence self hash mismatched"
            )
        reservation_raw = release_control._read_file_stable(
            self.reservations_root / f"{release_id}.json",
            maximum_bytes=MAX_CONTRACT_BYTES,
        )
        reservation = _strict_record(reservation_raw, "fixed regression reservation")
        reservation_keys = {
            "schema_version",
            "run_id",
            "receipt_id",
            "release_id",
            "candidate_manifest_sha256",
            "candidate_manifest_bytes",
            "runner_id",
            "runner_version",
            "runner_source_sha256",
            "runner_recipe_sha256",
            "runner_recipe_self_sha256",
            "workspace_source_fingerprint_algorithm",
            "workspace_source_fingerprint_sha256",
            "workspace_source_file_count",
            "reserved_at",
            "rerun_allowed",
            "self_sha256",
        }
        try:
            _parse_utc(reservation["reserved_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _error(
                "regression_reservation_untrusted",
                "run reservation timestamp is invalid",
            ) from exc
        if (
            set(reservation) != reservation_keys
            or reservation.get("self_sha256") != _self_hash(reservation)
            or not _RUN_ID.fullmatch(str(reservation.get("run_id", "")))
            or not _RECEIPT_ID.fullmatch(str(reservation.get("receipt_id", "")))
            or not _RELEASE_ID.fullmatch(str(reservation.get("release_id", "")))
            or not _SHA256.fullmatch(
                str(reservation.get("candidate_manifest_sha256", ""))
            )
            or not isinstance(reservation.get("candidate_manifest_bytes"), int)
            or isinstance(reservation.get("candidate_manifest_bytes"), bool)
            or reservation["candidate_manifest_bytes"] <= 0
            or reservation.get("runner_id") != RUNNER_ID
            or reservation.get("runner_version") != RUNNER_VERSION
            or not _SHA256.fullmatch(
                str(reservation.get("runner_source_sha256", ""))
            )
            or reservation.get("workspace_source_fingerprint_algorithm")
            != WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
            or not _SHA256.fullmatch(
                str(reservation.get("workspace_source_fingerprint_sha256", ""))
            )
            or not isinstance(reservation.get("workspace_source_file_count"), int)
            or isinstance(reservation.get("workspace_source_file_count"), bool)
            or reservation["workspace_source_file_count"] <= 0
            or reservation.get("rerun_allowed") is not False
        ):
            raise _error(
                "regression_reservation_untrusted",
                "run reservation does not satisfy its fixed schema",
            )

        if fingerprint_mode is _FingerprintTrustMode.CURRENT_RUNTIME:
            expected_workspace_source = _workspace_source_fingerprint(
                self.project_root
            )
            expected_runner_source = _runner_source_sha256()
        elif (
            fingerprint_mode
            is _FingerprintTrustMode.HISTORICAL_FOR_REPLACEMENT_ONLY
        ):
            expected_workspace_source = {
                "algorithm": evidence.get(
                    "workspace_source_fingerprint_algorithm"
                ),
                "sha256": evidence.get("workspace_source_fingerprint_sha256"),
                "file_count": evidence.get("workspace_source_file_count"),
            }
            expected_runner_source = evidence.get("runner_source_sha256")
            if (
                expected_workspace_source["algorithm"]
                != WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
                or not _SHA256.fullmatch(str(expected_workspace_source["sha256"]))
                or not isinstance(expected_workspace_source["file_count"], int)
                or isinstance(expected_workspace_source["file_count"], bool)
                or expected_workspace_source["file_count"] <= 0
                or not _SHA256.fullmatch(str(expected_runner_source))
            ):
                raise _error(
                    "regression_historical_fingerprint_untrusted",
                    "historical runner fingerprints are invalid",
                )
        else:  # pragma: no cover - private enum closes this surface
            raise _error(
                "regression_fingerprint_mode_invalid",
                "regression fingerprint trust mode is invalid",
            )
        claimed_runner_recipe = (
            reservation.get("runner_recipe_sha256"),
            evidence.get("runner_recipe_bytes"),
            reservation.get("runner_recipe_self_sha256"),
        )
        evidence_runner_recipe = (
            evidence.get("runner_recipe_sha256"),
            evidence.get("runner_recipe_bytes"),
            evidence.get("runner_recipe_self_sha256"),
        )
        current_runner_recipe = (
            self.runner_recipe_binding["file_sha256"],
            self.runner_recipe_binding["file_bytes"],
            RUNNER_RECIPE_SELF_SHA256,
        )
        expected_runner_recipe = _trusted_runner_recipe_binding(
            claimed=claimed_runner_recipe,
            evidence=evidence_runner_recipe,
            current=current_runner_recipe,
            fingerprint_mode=fingerprint_mode,
        )
        if (
            reservation.get("schema_version") != RESERVATION_SCHEMA_VERSION
            or reservation.get("release_id") != release_id
            or reservation.get("receipt_id") != receipt_id
            or reservation.get("run_id") != evidence.get("run_id")
            or reservation.get("candidate_manifest_sha256")
            != normalized.get("candidate_manifest_sha256")
            or reservation.get("candidate_manifest_bytes")
            != normalized.get("candidate_manifest_bytes")
            or reservation.get("runner_recipe_sha256")
            != expected_runner_recipe[0]
            or reservation.get("runner_recipe_self_sha256")
            != expected_runner_recipe[2]
            or reservation.get("runner_source_sha256") != expected_runner_source
            or reservation.get("workspace_source_fingerprint_algorithm")
            != WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
            or reservation.get("workspace_source_fingerprint_sha256")
            != expected_workspace_source["sha256"]
            or reservation.get("workspace_source_file_count")
            != expected_workspace_source["file_count"]
            or evidence.get("reservation_sha256") != _sha256(reservation_raw)
            or evidence.get("reservation_bytes") != len(reservation_raw)
        ):
            raise _error(
                "regression_reservation_untrusted", "run reservation binding mismatched"
            )

        expected_recipe = release_control.CONTRACT_FILE_SHA256["release_recipe.json"]
        checks = normalized.get("checks")
        evidence_checks = evidence.get("checks")
        if (
            normalized.get("verdict") != "PASS"
            or normalized.get("failures") != []
            or normalized.get("model_invoked") is not False
            or normalized.get("human_reviewed") is not False
            or normalized.get("teaching_use_allowed") is not False
            or normalized.get("publication_allowed") is not False
            or normalized.get("recipe_id") != release_control.RECIPE_ID
            or normalized.get("recipe_sha256") != expected_recipe
            or evidence.get("runner_id") != RUNNER_ID
            or evidence.get("runner_version") != RUNNER_VERSION
            or evidence.get("runner_source_sha256") != expected_runner_source
            or evidence.get("workspace_source_fingerprint_algorithm")
            != WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
            or evidence.get("workspace_source_fingerprint_sha256")
            != expected_workspace_source["sha256"]
            or evidence.get("workspace_source_file_count")
            != expected_workspace_source["file_count"]
            or evidence.get("receipt_id") != receipt_id
            or evidence.get("receipt_sha256") != _sha256(receipt_raw)
            or evidence.get("receipt_bytes") != len(receipt_raw)
            or evidence.get("release_id") != release_id
            or evidence.get("candidate_manifest_sha256")
            != normalized.get("candidate_manifest_sha256")
            or evidence.get("candidate_manifest_bytes")
            != normalized.get("candidate_manifest_bytes")
            or evidence.get("recipe_id") != normalized.get("recipe_id")
            or evidence.get("recipe_sha256") != normalized.get("recipe_sha256")
            or evidence.get("recipe_bytes") != normalized.get("recipe_bytes")
            or evidence.get("recipe_self_sha256") != release_control.RECIPE_SELF_SHA256
            or evidence.get("runner_recipe_sha256") != expected_runner_recipe[0]
            or evidence.get("runner_recipe_bytes") != expected_runner_recipe[1]
            or evidence.get("runner_recipe_self_sha256")
            != expected_runner_recipe[2]
            or evidence.get("verdict") != "PASS"
            or evidence.get("check_order") != list(FIXED_CHECK_IDS)
            or evidence.get("environment_policy") != ENVIRONMENT_POLICY
            or evidence.get("network_policy") != NETWORK_POLICY
            or evidence.get("model_invoked") is not False
            or evidence.get("api_key_accessed") is not False
            or evidence.get("student_private_domain_accessed") is not False
            or evidence.get("human_reviewed") is not False
            or evidence.get("teaching_use_allowed") is not False
            or evidence.get("publication_allowed") is not False
            or not isinstance(checks, list)
            or not isinstance(evidence_checks, list)
            or [item.get("check_id") for item in checks] != list(FIXED_CHECK_IDS)
            or [item.get("check_id") for item in evidence_checks]
            != list(FIXED_CHECK_IDS)
            or normalized.get("completed_at") != evidence.get("completed_at")
        ):
            raise _error(
                "regression_receipt_untrusted", "fixed runner binding is incomplete"
            )
        try:
            run_started = _parse_utc(evidence["started_at"])
            run_completed = _parse_utc(evidence["completed_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _error(
                "regression_evidence_untrusted", "run timestamps are invalid"
            ) from exc
        if run_completed < run_started:
            raise _error(
                "regression_evidence_untrusted", "run timestamp order is invalid"
            )

        previous_completed: datetime | None = None
        base_environment_names = {
            key.upper() for key in _minimal_environment()
        }
        for index, (step, receipt_check, evidence_check) in enumerate(
            zip(FIXED_STEPS, checks, evidence_checks, strict=True), start=1
        ):
            evidence_check_raw = _record_bytes(evidence_check)
            if (
                evidence_check.get("self_sha256") != _self_hash(evidence_check)
                or receipt_check.get("check_id") != step.check_id
                or receipt_check.get("status") != "pass"
                or receipt_check.get("evidence_sha256") != _sha256(evidence_check_raw)
                or receipt_check.get("evidence_bytes") != len(evidence_check_raw)
                or evidence_check.get("sequence") != index
                or evidence_check.get("implementation_id") != step.implementation_id
                or evidence_check.get("kind") != step.kind
                or evidence_check.get("actually_executed") is not True
                or evidence_check.get("status") != "pass"
                or evidence_check.get("exit_code") != 0
                or evidence_check.get("failure_code") is not None
            ):
                raise _error(
                    "regression_check_untrusted",
                    f"fixed check evidence is invalid: {step.check_id}",
                )
            try:
                check_started = _parse_utc(evidence_check["started_at"])
                check_completed = _parse_utc(evidence_check["completed_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise _error(
                    "regression_check_untrusted",
                    f"fixed check timestamps are invalid: {step.check_id}",
                ) from exc
            if (
                check_started < run_started
                or check_completed < check_started
                or check_completed > run_completed
                or (
                    previous_completed is not None
                    and check_started < previous_completed
                )
            ):
                raise _error(
                    "regression_check_untrusted",
                    f"fixed check order is invalid: {step.check_id}",
                )
            previous_completed = check_completed
            for stream_name in ("stdout", "stderr"):
                stream = evidence_check[stream_name]
                if (
                    stream.get("truncated") is not False
                    or stream.get("bytes_observed") != stream.get("bytes_captured")
                    or stream.get("bytes_observed") > step.output_limit_bytes_per_stream
                ):
                    raise _error(
                        "regression_check_untrusted",
                        f"fixed check output is incomplete: {step.check_id}",
                    )
            command = evidence_check.get("command")
            if step.kind == "internal":
                if command is not None:
                    raise _error(
                        "regression_check_untrusted",
                        "internal release-closure check cannot have argv",
                    )
            else:
                if not isinstance(command, dict):
                    raise _error(
                        "regression_check_untrusted",
                        f"fixed check command is missing: {step.check_id}",
                    )
                _, expected_display, _ = _fixed_command(step, self.project_root)
                environment_names = command.get("environment_names")
                expected_environment_names = sorted(
                    base_environment_names
                    | (
                        {CANDIDATE_ROOT_ENV}
                        if step.check_id == "launcher_smoke"
                        else set()
                    )
                )
                if (
                    command.get("argv") != expected_display
                    or command.get("cwd") != "<WORKSPACE>"
                    or command.get("shell") is not False
                    or command.get("environment_policy") != ENVIRONMENT_POLICY
                    or environment_names != expected_environment_names
                    or not _SHA256.fullmatch(str(command.get("executable_sha256", "")))
                    or not _SHA256.fullmatch(
                        str(command.get("executable_path_sha256", ""))
                    )
                    or not isinstance(command.get("executable_bytes"), int)
                    or command["executable_bytes"] <= 0
                ):
                    raise _error(
                        "regression_check_untrusted",
                        f"fixed check argv/environment drifted: {step.check_id}",
                    )

        # Re-observe every immutable trust record after semantic validation.
        # This rejects same-length replacement or mutation between the first
        # read and the final decision.
        if (
            release_control._read_file_stable(
                candidate_manifest_path, maximum_bytes=MAX_EVIDENCE_BYTES
            )
            != candidate_manifest_raw
            or release_control._read_file_stable(
                self.receipts_root / f"{receipt_id}.json",
                maximum_bytes=MAX_EVIDENCE_BYTES,
            )
            != stored_receipt_raw
            or release_control._read_file_stable(
                self.evidence_root / f"{receipt_id}.json",
                maximum_bytes=MAX_EVIDENCE_BYTES,
            )
            != evidence_raw
            or release_control._read_file_stable(
                self.reservations_root / f"{release_id}.json",
                maximum_bytes=MAX_CONTRACT_BYTES,
            )
            != reservation_raw
        ):
            raise _error(
                "regression_record_drift",
                "fixed regression records changed during verification",
            )
        if fingerprint_mode is _FingerprintTrustMode.CURRENT_RUNTIME and (
            _runner_source_sha256() != expected_runner_source
            or _workspace_source_fingerprint(self.project_root)
            != expected_workspace_source
        ):
            raise _error(
                "regression_workspace_source_drift",
                "fixed regression source changed during verification",
            )

        common = {
            "receipt_id": receipt_id,
            "release_id": release_id,
            "run_id": evidence["run_id"],
            "evidence_sha256": _sha256(evidence_raw),
            "evidence_bytes": len(evidence_raw),
        }
        if fingerprint_mode is _FingerprintTrustMode.CURRENT_RUNTIME:
            return {
                **common,
                "trusted": True,
                "current_trusted": True,
                "serving_eligible": True,
            }
        return {
            **common,
            "historical_for_replacement_only": True,
            "current_trusted": False,
            "serving_eligible": False,
            "rollback_eligible": False,
        }


class WorkbenchReleaseRegressionRunner(_RunnerStorage):
    """Execute the fixed seven-step recipe once for one immutable release."""

    def __init__(
        self,
        store: release_control.WorkbenchReleaseStore,
    ) -> None:
        if not isinstance(store, release_control.WorkbenchReleaseStore):
            raise _error(
                "regression_release_store_invalid",
                "fixed runner requires the immutable release store",
                400,
            )
        self.store = store
        super().__init__(store.release_root, project_root=store.project_root)

    def run(
        self,
        release_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("regression_release_id_invalid", "release id is invalid", 400)
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise _error(
                "regression_cancel_token_invalid",
                "cancellation token is invalid",
                400,
            )
        cancellation = cancel_event or threading.Event()
        with self.single_inflight():
            candidate = self.store.read_candidate(release_id)
            if tuple(self.store.recipe_check_ids) != FIXED_CHECK_IDS:
                raise _error(
                    "regression_release_recipe_mismatch",
                    "release store recipe and fixed runner order differ",
                )
            runner_source = _runner_source_sha256()
            workspace_source = _workspace_source_fingerprint(self.project_root)
            run_id = "WBRUN-" + secrets.token_hex(16)
            receipt_id = "WBRR-" + secrets.token_hex(16)
            if not _RUN_ID.fullmatch(run_id) or not _RECEIPT_ID.fullmatch(receipt_id):
                raise _error(
                    "regression_identity_exhausted", "runner identity generation failed"
                )
            started_at = _utc_now()
            reservation = _with_self_hash(
                {
                    "schema_version": RESERVATION_SCHEMA_VERSION,
                    "run_id": run_id,
                    "receipt_id": receipt_id,
                    "release_id": release_id,
                    "candidate_manifest_sha256": candidate["manifest_sha256"],
                    "candidate_manifest_bytes": candidate["manifest_bytes"],
                    "runner_id": RUNNER_ID,
                    "runner_version": RUNNER_VERSION,
                    "runner_source_sha256": runner_source,
                    "runner_recipe_sha256": self.runner_recipe_binding["file_sha256"],
                    "runner_recipe_self_sha256": RUNNER_RECIPE_SELF_SHA256,
                    "workspace_source_fingerprint_algorithm": (
                        WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
                    ),
                    "workspace_source_fingerprint_sha256": workspace_source["sha256"],
                    "workspace_source_file_count": workspace_source["file_count"],
                    "reserved_at": started_at,
                    "rerun_allowed": False,
                }
            )
            reservation_raw = _record_bytes(reservation)
            reservation_path = self.reservations_root / f"{release_id}.json"
            try:
                release_control._write_exclusive(reservation_path, reservation_raw)
            except release_control.WorkbenchReleaseControlError as exc:
                if exc.code == "release_store_append_conflict":
                    raise _error(
                        "regression_release_already_attempted",
                        "this immutable release already has a burned regression attempt",
                    ) from exc
                raise

            checks: list[dict[str, Any]] = []
            prior_failure = False
            for sequence, step in enumerate(FIXED_STEPS, start=1):
                if prior_failure:
                    check = _make_unexecuted_check(
                        step,
                        sequence,
                        self.project_root,
                        "not_run",
                        f"{step.check_id}_not_run_due_to_prior_failure",
                    )
                elif step.kind == "internal":
                    check = _execute_internal_closure_step(
                        step,
                        sequence,
                        self.project_root,
                        self.store,
                        candidate,
                        cancellation,
                    )
                else:
                    check = _execute_subprocess_step(
                        step,
                        sequence,
                        self.project_root,
                        cancellation,
                        candidate_root=(
                            self.store.candidates_root / release_id
                            if step.check_id == "launcher_smoke"
                            else None
                        ),
                    )
                checks.append(check)
                if check["status"] != "pass":
                    prior_failure = True

            # A successful sequence receives one final immutable readback.  If
            # bytes changed after the explicit closure check, the closure check
            # is replaced by a failure record and PASS becomes impossible.
            if not prior_failure:
                try:
                    if _runner_source_sha256() != runner_source:
                        raise _error(
                            "regression_runner_source_drift",
                            "fixed runner source changed during execution",
                        )
                    if (
                        _workspace_source_fingerprint(self.project_root)
                        != workspace_source
                    ):
                        raise _error(
                            "regression_workspace_source_drift",
                            "fixed regression source changed during execution",
                        )
                    final_candidate = self.store.read_candidate(release_id)
                    if (
                        final_candidate.get("manifest_sha256")
                        != candidate.get("manifest_sha256")
                        or final_candidate.get("manifest_bytes")
                        != candidate.get("manifest_bytes")
                        or final_candidate.get("manifest") != candidate.get("manifest")
                    ):
                        raise _error(
                            "release_closure_postflight_mismatch",
                            "candidate changed after fixed checks",
                        )
                except (
                    WorkbenchReleaseRegressionError,
                    release_control.WorkbenchReleaseControlError,
                    KeyError,
                    TypeError,
                    ValueError,
                    OSError,
                ) as exc:
                    index = FIXED_CHECK_IDS.index("release_closure")
                    step = FIXED_STEPS[index]
                    checks[index] = _with_self_hash(
                        {
                            **checks[index],
                            "status": "fail",
                            "exit_code": 1,
                            "failure_code": (
                                exc.code
                                if isinstance(
                                    exc,
                                    (
                                        WorkbenchReleaseRegressionError,
                                        release_control.WorkbenchReleaseControlError,
                                    ),
                                )
                                else "release_closure_postflight_failed"
                            ),
                            "stderr": _bytes_stream(
                                f"{type(exc).__name__}: {exc}".encode(
                                    "utf-8", errors="replace"
                                ),
                                self.project_root,
                            ),
                        }
                    )
                    prior_failure = True

            completed_at = _utc_now()
            verdict = "FAIL" if prior_failure else "PASS"
            receipt_checks = []
            failures = []
            for check in checks:
                check_raw = _record_bytes(check)
                passed = check["status"] == "pass"
                receipt_checks.append(
                    {
                        "check_id": check["check_id"],
                        "status": "pass" if passed else "fail",
                        "evidence_sha256": _sha256(check_raw),
                        "evidence_bytes": len(check_raw),
                    }
                )
                if not passed:
                    failure = check.get("failure_code") or f"{check['check_id']}_failed"
                    if not isinstance(failure, str) or not _FAILURE_CODE.fullmatch(
                        failure
                    ):
                        failure = f"{check['check_id']}_failed"
                    if failure not in failures:
                        failures.append(failure)
            receipt = _with_self_hash(
                {
                    "schema_version": release_control.REGRESSION_RECEIPT_SCHEMA_VERSION,
                    "receipt_id": receipt_id,
                    "release_id": release_id,
                    "candidate_manifest_sha256": candidate["manifest_sha256"],
                    "candidate_manifest_bytes": candidate["manifest_bytes"],
                    "recipe_id": self.store.recipe_binding["recipe_id"],
                    "recipe_sha256": self.store.recipe_binding["file_sha256"],
                    "recipe_bytes": self.store.recipe_binding["file_bytes"],
                    "verdict": verdict,
                    "checks": receipt_checks,
                    "failures": failures,
                    "completed_at": completed_at,
                    "model_invoked": False,
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            )
            receipt_raw = _record_bytes(receipt)
            receipt_path = self.receipts_root / f"{receipt_id}.json"
            release_control._write_exclusive(receipt_path, receipt_raw)

            evidence = _with_self_hash(
                {
                    "schema_version": RUN_EVIDENCE_SCHEMA_VERSION,
                    "run_id": run_id,
                    "runner_id": RUNNER_ID,
                    "runner_version": RUNNER_VERSION,
                    "runner_source_sha256": runner_source,
                    "workspace_source_fingerprint_algorithm": (
                        WORKSPACE_SOURCE_FINGERPRINT_ALGORITHM
                    ),
                    "workspace_source_fingerprint_sha256": workspace_source["sha256"],
                    "workspace_source_file_count": workspace_source["file_count"],
                    "reservation_sha256": _sha256(reservation_raw),
                    "reservation_bytes": len(reservation_raw),
                    "receipt_id": receipt_id,
                    "receipt_sha256": _sha256(receipt_raw),
                    "receipt_bytes": len(receipt_raw),
                    "release_id": release_id,
                    "candidate_manifest_sha256": candidate["manifest_sha256"],
                    "candidate_manifest_bytes": candidate["manifest_bytes"],
                    "recipe_id": self.store.recipe_binding["recipe_id"],
                    "recipe_sha256": self.store.recipe_binding["file_sha256"],
                    "recipe_bytes": self.store.recipe_binding["file_bytes"],
                    "recipe_self_sha256": self.store.recipe_binding["self_sha256"],
                    "runner_recipe_sha256": self.runner_recipe_binding["file_sha256"],
                    "runner_recipe_bytes": self.runner_recipe_binding["file_bytes"],
                    "runner_recipe_self_sha256": RUNNER_RECIPE_SELF_SHA256,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "verdict": verdict,
                    "check_order": list(FIXED_CHECK_IDS),
                    "checks": checks,
                    "environment_policy": ENVIRONMENT_POLICY,
                    "network_policy": NETWORK_POLICY,
                    "model_invoked": False,
                    "api_key_accessed": False,
                    "student_private_domain_accessed": False,
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            )
            errors = sorted(
                self.evidence_validator.iter_errors(evidence),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
            if errors:
                location = "/".join(str(part) for part in errors[0].absolute_path)
                raise _error(
                    "regression_evidence_contract_failed",
                    f"run evidence failed before commit at {location or '<root>'}",
                    500,
                )
            evidence_raw = _record_bytes(evidence)
            release_control._write_exclusive(
                self.evidence_root / f"{receipt_id}.json", evidence_raw
            )
            persisted_receipt = release_control._read_file_stable(
                receipt_path, maximum_bytes=MAX_EVIDENCE_BYTES
            )
            persisted_evidence = release_control._read_file_stable(
                self.evidence_root / f"{receipt_id}.json",
                maximum_bytes=MAX_EVIDENCE_BYTES,
            )
            if persisted_receipt != receipt_raw or persisted_evidence != evidence_raw:
                raise _error(
                    "regression_evidence_postcommit_mismatch",
                    "runner evidence did not round-trip",
                    500,
                )
            return {
                "receipt": receipt,
                "run": {
                    "run_id": run_id,
                    "receipt_id": receipt_id,
                    "release_id": release_id,
                    "verdict": verdict,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "evidence_sha256": _sha256(evidence_raw),
                    "evidence_bytes": len(evidence_raw),
                    "automatic_activation": False,
                    "model_invoked": False,
                    "api_key_accessed": False,
                    "student_private_domain_accessed": False,
                    "human_reviewed": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                },
            }


__all__ = [
    "ENVIRONMENT_POLICY",
    "FIXED_CHECK_IDS",
    "NETWORK_POLICY",
    "RUNNER_ID",
    "RUNNER_VERSION",
    "WorkbenchReleaseRegressionError",
    "WorkbenchReleaseRegressionRunner",
    "WorkbenchReleaseRegressionVerifier",
]
