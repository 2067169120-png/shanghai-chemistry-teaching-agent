#!/usr/bin/env python3
"""Stable Python API and JSON CLI for the read-only Gateway integration.

The Gateway calls this module; it does not modify student-learning files. All
private operations are authorized with a profile UUID plus bearer capability.
CLI capability input comes from an environment variable to keep it out of the
process command line and normal logs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    WORKSPACE = Path(__file__).resolve().parents[2]
    if str(WORKSPACE) not in sys.path:
        sys.path.insert(0, str(WORKSPACE))
    from integrations.student_learning_v1.domain import (
        GENERATION_CONTENT_PROVIDER_CONTRACT,
        AccessDenied,
        AggregationThresholdError,
        ControllerReceiptAdapter,
        FailClosedCentralAdapter,
        FixtureContentProvider,
        LearningEngine,
        PrivacyViolation,
        RealDiagnosisBlocked,
        StudentLearningError,
        StudentScope,
        StudentStore,
        _sha256_json,
        build_zip_bundle,
        latest_grade_progress,
        verify_zip_bundle,
    )
else:
    from .domain import (
        GENERATION_CONTENT_PROVIDER_CONTRACT,
        AccessDenied,
        AggregationThresholdError,
        ControllerReceiptAdapter,
        FailClosedCentralAdapter,
        FixtureContentProvider,
        LearningEngine,
        PrivacyViolation,
        RealDiagnosisBlocked,
        StudentLearningError,
        StudentScope,
        StudentStore,
        _sha256_json,
        build_zip_bundle,
        latest_grade_progress,
        verify_zip_bundle,
    )


API_VERSION = "gateway_student_learning_api_v1"
CAPABILITY_ENV = "STUDENT_LEARNING_CAPABILITY"
REAL_PREPARE_CONTRACT = "student_learning_real_diagnosis_prepare_v1"
CONTROLLER_RECEIPT_CONTRACT = "central_diagnosis_adapter_v2"


class GatewayErrorCode:
    OK = 0
    INVALID_REQUEST = 10
    ACCESS_DENIED = 11
    PRIVACY_VIOLATION = 12
    SANITIZATION_BLOCKED = 13
    AGGREGATION_THRESHOLD = 14
    REAL_DIAGNOSIS_BLOCKED = 15
    DOMAIN_VALIDATION = 16
    CONTENT_PROVIDER_NOT_CONNECTED = 17
    INTERNAL_ERROR = 50


class ContentProviderNotConnected(StudentLearningError):
    pass


class StaticJsonFormalContentProvider:
    """Contract-debug adapter only; static JSON can never unlock real delivery."""

    def __init__(self, export: dict[str, Any]) -> None:
        self.export = export

    def render(self, *, diagnosis: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        del diagnosis, plan
        return json.loads(json.dumps(self.export))

    def status(self) -> dict[str, Any]:
        return {
            "contract_version": GENERATION_CONTENT_PROVIDER_CONTRACT,
            "ready": False,
            "production_ready": False,
            "fixture_only": True,
            "provider_mode": "static_contract_debug_only",
            "required_document_count": 8,
            "root_external_reconciliation_ready": False,
            "blockers": [
                "static_json_export_cannot_unlock_real_week_bundle",
                "live_generation_provider_required",
            ],
        }


def success(data: dict[str, Any]) -> dict[str, Any]:
    return {"api_version": API_VERSION, "ok": True, "code": GatewayErrorCode.OK, "error": None, "data": data}


def failure(code: int, error_type: str, message: str) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "ok": False,
        "code": code,
        "error": {"type": error_type, "message": message},
        "data": None,
    }


def map_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AccessDenied):
        return failure(GatewayErrorCode.ACCESS_DENIED, "access_denied", str(exc))
    if isinstance(exc, PrivacyViolation):
        return failure(GatewayErrorCode.PRIVACY_VIOLATION, "privacy_violation", str(exc))
    if isinstance(exc, AggregationThresholdError):
        return failure(GatewayErrorCode.AGGREGATION_THRESHOLD, "aggregation_threshold", str(exc))
    if isinstance(exc, RealDiagnosisBlocked):
        return failure(GatewayErrorCode.REAL_DIAGNOSIS_BLOCKED, "real_diagnosis_blocked", str(exc))
    if isinstance(exc, ContentProviderNotConnected):
        return failure(GatewayErrorCode.CONTENT_PROVIDER_NOT_CONNECTED, "content_provider_not_connected", str(exc))
    if isinstance(exc, StudentLearningError):
        return failure(GatewayErrorCode.DOMAIN_VALIDATION, "domain_validation", str(exc))
    if isinstance(exc, (json.JSONDecodeError, OSError, ValueError, TypeError)):
        return failure(GatewayErrorCode.INVALID_REQUEST, "invalid_request", str(exc))
    return failure(GatewayErrorCode.INTERNAL_ERROR, "internal_error", type(exc).__name__)


class GatewayStudentLearningAPI:
    def __init__(
        self,
        data_root: Path,
        controller_root: Path | None = None,
        formal_content_provider: Any | None = None,
    ) -> None:
        self.store = StudentStore(data_root)
        self.controller_root = (controller_root or Path(__file__).resolve().parents[2] / "sh-chem-db").resolve()
        self.formal_content_provider = formal_content_provider

    def formal_content_provider_status(self) -> dict[str, Any]:
        if self.formal_content_provider is None:
            return {
                "contract_version": GENERATION_CONTENT_PROVIDER_CONTRACT,
                "ready": False,
                "production_ready": False,
                "fixture_only": False,
                "required_document_count": 8,
                "root_external_reconciliation_ready": False,
                "blockers": ["live_generation_provider_required"],
            }
        status_method = getattr(self.formal_content_provider, "status", None)
        if not callable(status_method):
            return {
                "contract_version": GENERATION_CONTENT_PROVIDER_CONTRACT,
                "ready": False,
                "production_ready": False,
                "fixture_only": False,
                "required_document_count": 8,
                "root_external_reconciliation_ready": False,
                "blockers": ["generation_provider_status_contract_required"],
            }
        try:
            status = status_method()
        except Exception as exc:  # noqa: BLE001 - optional provider boundary
            return {
                "contract_version": GENERATION_CONTENT_PROVIDER_CONTRACT,
                "ready": False,
                "production_ready": False,
                "fixture_only": False,
                "required_document_count": 8,
                "root_external_reconciliation_ready": False,
                "blockers": [f"generation_provider_status_failed:{type(exc).__name__}"],
            }
        if not isinstance(status, dict):
            status = {}
        blockers = list(status.get("blockers", []))
        ready = (
            status.get("contract_version") == GENERATION_CONTENT_PROVIDER_CONTRACT
            and status.get("ready") is True
            and status.get("production_ready") is True
            and status.get("fixture_only") is False
            and status.get("required_document_count") == 8
            and status.get("root_external_reconciliation_ready") is True
        )
        if not ready and not blockers:
            blockers.append("generation_provider_readiness_contract_incomplete")
        return {
            **status,
            "contract_version": GENERATION_CONTENT_PROVIDER_CONTRACT,
            "ready": ready,
            "blockers": sorted({str(value) for value in blockers}),
        }

    @staticmethod
    def scope(profile_id: str, capability: str) -> StudentScope:
        if not capability:
            raise AccessDenied("profile capability missing")
        return StudentScope(profile_id=profile_id, capability=capability)

    def create_student(
        self, *, grade: str, grade_progress: dict[str, Any], consent_recorded: bool,
        retention_days: int, synthetic: bool
    ) -> dict[str, Any]:
        scope = self.store.create_profile(
            grade=grade, grade_progress=grade_progress, consent_recorded=consent_recorded,
            retention_days=retention_days, synthetic=synthetic,
        )
        return {
            "profile_id": scope.profile_id,
            "capability": scope.capability,
            "capability_handling": "return_once_capture_without_logging",
            "synthetic": synthetic,
        }

    def select_student(self, *, profile_id: str, capability: str) -> dict[str, Any]:
        scope = self.scope(profile_id, capability)
        profile = self.store.read_profile(scope)
        return {
            "metadata": profile["metadata"],
            "input_event_count": len(profile["inputs"]),
            "timeline_event_count": len(profile["timeline"]),
            "learning_history": self.store.read_learning_history(scope),
        }

    def save_raw_error_image(
        self,
        *,
        profile_id: str,
        capability: str,
        image_bytes: bytes,
        media_type: str,
        source_ref: str,
    ) -> dict[str, Any]:
        scope = self.scope(profile_id, capability)
        record = self.store.import_error_image(
            scope, image_bytes=image_bytes, media_type=media_type, source_ref=source_ref
        )
        receipt = self.store.get_image_sanitization_status(scope, record["media_id"])
        return {
            "media": record,
            "local_hold_receipt": receipt,
            # Compatibility alias for existing callers.  Its contract_version
            # proves that no sanitizer or text recognizer was invoked.
            "sanitization_receipt": receipt,
        }

    def get_sanitization_receipt(
        self, *, profile_id: str, capability: str, media_id: str
    ) -> dict[str, Any]:
        return self.store.get_image_sanitization_status(self.scope(profile_id, capability), media_id)

    def record_input(
        self, *, profile_id: str, capability: str, input_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self.store.append_input(self.scope(profile_id, capability), input_type, payload)

    def prepare_real_diagnosis(
        self, *, profile_id: str, capability: str
    ) -> dict[str, Any]:
        """Freeze the exact private input that a central issuer must bind.

        Preparation does not diagnose and cannot unlock the real path. It
        returns an anonymized, hash-bound controller payload without a receipt,
        then waits for the controller preflight to issue its v2 receipt.
        """
        scope = self.scope(profile_id, capability)
        profile = self.store.read_profile(scope)
        attempts = [row["payload"] for row in profile["inputs"] if row["event_type"] == "attempt"]
        self._validate_real_private_contract(profile, attempts)
        media_audit = self.store.validate_attempt_image_receipts(scope, attempts)
        controller_input = self._real_controller_input(scope, attempts)
        prepare_id = str(uuid.uuid4())
        record = {
            "contract_version": REAL_PREPARE_CONTRACT,
            "prepare_id": prepare_id,
            "profile_id": scope.profile_id,
            "state": "awaiting_controller_receipt_issuance",
            "controller_input_without_receipt": controller_input,
            "prepare_request_sha256": _sha256_json(controller_input),
            "attempt_input_sha256": _sha256_json(attempts),
            "media_receipt_set_sha256": _sha256_json(controller_input["media_egress_receipts"]),
            "attempt_bindings": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "question_id": attempt["question_id"],
                    "atomic_part_id": attempt["question_evidence"]["atomic_part_id"],
                    "evidence_sha256": _sha256_json(attempt["question_evidence"]),
                }
                for attempt in attempts
            ],
            "required_machine_state": "automated_verified_candidate",
            "required_controller_action": {
                "preflight_command": "preflight",
                "preflight_mode": "diagnose",
                "receipt_output_json_path": "diagnosis_input_gate.controller_receipt",
                "receipt_contract_version": CONTROLLER_RECEIPT_CONTRACT,
                "verify_command": "diagnosis-receipt-verify",
                "caller_supplied_receipt_forbidden": True,
            },
            "image_egress_audit": media_audit,
            "claim_scope": "private_real_diagnosis_input_candidate",
            "machine_status": "machine_only_not_human_reviewed",
            "human_reviewed": False,
        }
        self._validate_local_schema(record, "real_diagnosis_prepare.schema.json")
        self.store.persist_derived(
            scope, f"real_input_{prepare_id.replace('-', '')}", controller_input
        )
        self.store.persist_derived(scope, f"real_prepare_{prepare_id.replace('-', '')}", record)
        return self.store.write_job(
            scope,
            job_type="prepare_real_diagnosis",
            status="awaiting_controller",
            result={"preparation": record},
        )

    def issue_real_diagnosis_receipt(
        self,
        *,
        profile_id: str,
        capability: str,
        prepare_id: str,
    ) -> dict[str, Any]:
        scope = self.scope(profile_id, capability)
        preparation = self._load_real_preparation(scope, prepare_id)
        profile = self.store.read_profile(scope)
        attempts = [row["payload"] for row in profile["inputs"] if row["event_type"] == "attempt"]
        self._validate_real_private_contract(profile, attempts)
        self.store.validate_attempt_image_receipts(scope, attempts)
        current_input = self._real_controller_input(scope, attempts)
        self._assert_preparation_current(preparation, current_input, attempts)
        input_path = self.store.persist_derived(
            scope, f"real_input_{prepare_id.replace('-', '')}", current_input
        )
        receipt, issuance = self._run_controller_receipt_issue(
            scope=scope, prepare_id=prepare_id, input_path=input_path, attempts=attempts
        )
        self.store.persist_derived(
            scope, f"controller_receipt_{prepare_id.replace('-', '')}", receipt
        )
        self.store.persist_derived(
            scope, f"controller_issue_{prepare_id.replace('-', '')}", issuance
        )
        return self.store.write_job(
            scope,
            job_type="issue_real_diagnosis_receipt",
            status="complete",
            result={
                "prepare_id": prepare_id,
                "controller_receipt": receipt,
                "issuance": issuance,
                "next_action": "execute_real_diagnosis",
            },
        )

    def execute_real_diagnosis(
        self,
        *,
        profile_id: str,
        capability: str,
        prepare_id: str,
    ) -> dict[str, Any]:
        scope = self.scope(profile_id, capability)
        preparation = self._load_real_preparation(scope, prepare_id)
        try:
            controller_receipt = self.store.read_derived(
                scope, f"controller_receipt_{prepare_id.replace('-', '')}"
            )
            issuance = self.store.read_derived(
                scope, f"controller_issue_{prepare_id.replace('-', '')}"
            )
        except AccessDenied as exc:
            raise RealDiagnosisBlocked("controller-issued receipt unavailable; run issue step") from exc
        profile = self.store.read_profile(scope)
        attempts = [row["payload"] for row in profile["inputs"] if row["event_type"] == "attempt"]
        current_input = self._real_controller_input(scope, attempts)
        self._assert_preparation_current(preparation, current_input, attempts)
        if (
            issuance.get("authoritative_for_unlock") is not False
            or issuance.get("controller_receipt_sha256") != controller_receipt.get("receipt_sha256")
            or issuance.get("preflight_input_sha256") != preparation.get("prepare_request_sha256")
        ):
            raise RealDiagnosisBlocked("private controller issuance cache binding mismatch")
        input_path = self.store.persist_derived(
            scope, f"real_input_{prepare_id.replace('-', '')}", current_input
        )
        receipt_path = self.store.persist_derived(
            scope, f"controller_receipt_{prepare_id.replace('-', '')}", controller_receipt
        )
        receipt_verification = self._run_controller_receipt_verify(
            scope=scope,
            prepare_id=prepare_id,
            input_path=input_path,
            receipt_path=receipt_path,
        )
        diagnosis = self._diagnose(
            scope,
            profile,
            "real",
            controller_receipt,
            preparation=preparation,
            issuance=issuance,
            receipt_verification=receipt_verification,
        )
        self.store.persist_derived(scope, "latest_diagnose_real", diagnosis)
        return self.store.write_job(
            scope,
            job_type="diagnose_real",
            status="complete",
            result={"prepare_id": prepare_id, "diagnosis": diagnosis},
        )

    def run_diagnosis(
        self,
        *,
        profile_id: str,
        capability: str,
        mode: str,
        controller_receipt: dict[str, Any] | None = None,
        prepare_id: str | None = None,
    ) -> dict[str, Any]:
        if mode == "real":
            if controller_receipt is not None:
                raise RealDiagnosisBlocked("caller-supplied controller receipt is forbidden")
            if prepare_id is None:
                raise RealDiagnosisBlocked("real diagnosis requires prepare_id from the issue flow")
            return self.execute_real_diagnosis(
                profile_id=profile_id,
                capability=capability,
                prepare_id=prepare_id,
            )
        scope = self.scope(profile_id, capability)
        profile = self.store.read_profile(scope)
        diagnosis = self._diagnose(scope, profile, mode, controller_receipt)
        job_type = "diagnose_synthetic" if mode == "synthetic" else "diagnose_real"
        self.store.persist_derived(scope, f"latest_{job_type}", diagnosis)
        return self.store.write_job(
            scope, job_type=job_type, status="complete",
            result={"diagnosis": diagnosis},
        )

    def _diagnose(
        self,
        scope: StudentScope,
        profile: dict[str, Any],
        mode: str,
        controller_receipt: dict[str, Any] | None,
        *,
        preparation: dict[str, Any] | None = None,
        issuance: dict[str, Any] | None = None,
        receipt_verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode == "synthetic":
            engine = LearningEngine(FailClosedCentralAdapter())
        elif mode == "real":
            if controller_receipt is None or preparation is None or issuance is None or receipt_verification is None:
                raise RealDiagnosisBlocked("controller-issued and live-reverified real diagnosis contract required")
            attempts = [row["payload"] for row in profile["inputs"] if row["event_type"] == "attempt"]
            self._validate_real_private_contract(profile, attempts)
            media_audit = self.store.validate_attempt_image_receipts(scope, attempts)
            current_input = self._real_controller_input(scope, attempts)
            self._assert_preparation_current(preparation, current_input, attempts)
            if receipt_verification.get("valid") is not True or receipt_verification.get("live_registry_revalidated") is not True:
                raise RealDiagnosisBlocked("controller receipt was not live-revalidated")
            preflight = {
                "contract_version": controller_receipt.get("controller_contract_version"),
                "controller_version": controller_receipt.get("controller_version"),
                "mode": "diagnose",
                "ready": True,
                "human_reviewed": False,
                "request_sha256": preparation["prepare_request_sha256"],
                "output_sha256": issuance["preflight_output_sha256"],
                "controller_receipt_registry_bound": True,
                "controller_receipt_sha256": controller_receipt["receipt_sha256"],
                "governance_chain_registry_sha256": controller_receipt["registry_binding"]["sha256"],
                "receipt_live_revalidated": True,
                "receipt_verify_output_sha256": receipt_verification["verify_output_sha256"],
                "validated_checks": issuance["validated_checks"],
                "governance_chain_validation": issuance["governance_chain_validation"],
            }
            engine = LearningEngine(
                ControllerReceiptAdapter(controller_receipt, receipt_verification)
            )
        else:
            raise StudentLearningError("diagnosis mode must be synthetic or real")
        diagnosis = engine.diagnose(profile)
        if mode == "real":
            diagnosis["image_egress_audit"] = media_audit
            diagnosis["controller_diagnose_preflight"] = preflight
            diagnosis["claim_scope"] = "machine_only_real_student_candidate"
            diagnosis["human_reviewed"] = False
            diagnosis["teaching_effectiveness_unverified"] = True
            if "synthetic_fixture_only" in json.dumps(diagnosis, ensure_ascii=False):
                raise RealDiagnosisBlocked("real output contains synthetic fixture claim")
            from jsonschema import Draft202012Validator, FormatChecker

            schema_path = Path(__file__).resolve().parents[2] / "sh-chem-db" / "kb" / "student_learning_v2" / "real_machine_candidate.schema.json"
            errors = sorted(
                Draft202012Validator(
                    json.loads(schema_path.read_text(encoding="utf-8")), format_checker=FormatChecker()
                ).iter_errors(diagnosis),
                key=lambda error: list(error.path),
            )
            if errors:
                raise RealDiagnosisBlocked(f"real output schema invalid:{errors[0].message}")
        return diagnosis

    def _load_real_preparation(self, scope: StudentScope, prepare_id: str) -> dict[str, Any]:
        try:
            normalized = str(uuid.UUID(prepare_id))
        except (ValueError, AttributeError) as exc:
            raise RealDiagnosisBlocked("invalid real diagnosis prepare_id") from exc
        preparation = self.store.read_derived(
            scope, f"real_prepare_{normalized.replace('-', '')}"
        )
        self._validate_local_schema(preparation, "real_diagnosis_prepare.schema.json")
        if (
            preparation.get("prepare_id") != normalized
            or preparation.get("profile_id") != scope.profile_id
            or preparation.get("state") != "awaiting_controller_receipt_issuance"
            or preparation.get("human_reviewed") is not False
        ):
            raise RealDiagnosisBlocked("real diagnosis preparation binding invalid")
        return preparation

    def _real_controller_input(
        self, scope: StudentScope, attempts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        media_ids = sorted({
            media_id
            for attempt in attempts
            for media_id in attempt.get("question_evidence", {}).get("media_ids", [])
            if isinstance(media_id, str)
        })
        media_receipts = [
            self.store.get_image_sanitization_status(scope, media_id)
            for media_id in media_ids
        ]
        return {
            "fixture": False,
            "profile_id": scope.profile_id,
            "attempts": attempts,
            "required_machine_state": "automated_verified_candidate",
            "media_egress_receipts": media_receipts,
            "privacy": {
                "egress_allowed": True,
                "deidentified_copy": True,
                "direct_identifiers_present": False,
                "raw_model_access_allowed": False,
                "machine_status": "machine_only_not_human_reviewed",
                "human_reviewed": False,
            },
            "human_reviewed": False,
        }

    @staticmethod
    def _validate_local_schema(value: dict[str, Any], schema_name: str) -> None:
        from jsonschema import Draft202012Validator, FormatChecker

        workspace = Path(__file__).resolve().parents[2]
        candidates = (
            workspace / "sh-chem-db" / "kb" / "student_learning_v2" / schema_name,
            Path(__file__).resolve().parent / schema_name,
        )
        schema_path = next((path for path in candidates if path.is_file()), None)
        if schema_path is None:
            raise RealDiagnosisBlocked(f"local contract schema missing:{schema_name}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda error: list(error.path),
        )
        if errors:
            raise RealDiagnosisBlocked(f"local contract schema invalid:{schema_name}:{errors[0].message}")

    @staticmethod
    def _assert_preparation_current(
        preparation: dict[str, Any],
        current_input: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> None:
        if preparation.get("prepare_request_sha256") != _sha256_json(current_input):
            raise RealDiagnosisBlocked("prepared real diagnosis input is stale or mutated")
        if preparation.get("attempt_input_sha256") != _sha256_json(attempts):
            raise RealDiagnosisBlocked("prepared attempt input hash mismatch")
        if "central_receipt" in current_input:
            raise RealDiagnosisBlocked("caller-supplied central receipt forbidden in controller input")

    def _run_controller_receipt_issue(
        self,
        *,
        scope: StudentScope,
        prepare_id: str,
        input_path: Path,
        attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        controller = self.controller_root / "scripts" / "sh_chem_agent.py"
        if not controller.is_file():
            raise RealDiagnosisBlocked("live controller executable missing")
        process = subprocess.run(
            [
                sys.executable,
                str(controller),
                "preflight",
                "--root",
                str(self.controller_root),
                "--mode",
                "diagnose",
                "--input",
                str(input_path),
            ],
            capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            timeout=180,
            check=False,
        )
        try:
            output = json.loads(process.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RealDiagnosisBlocked("controller receipt issuance output unreadable") from exc
        if not isinstance(output, dict):
            raise RealDiagnosisBlocked("controller receipt issuance output is not an object")
        output_hash = _sha256_json(output)
        self.store.persist_derived(
            scope, f"controller_issue_preflight_{prepare_id.replace('-', '')}", output
        )
        required_checks = {
            "real_diagnosis_input_not_fixture",
            "human_reviewed_false",
            "profile_id_valid",
            "privacy_fields_complete",
            "real_attempts_present",
            "caller_supplied_central_receipt_absent",
            "attempt_ids_unique",
            "live_canonical_governance_registry_valid",
            "diagnosis_machine_state_required",
            "canonical_parent_chains_bound",
            "machine_governance_state_sufficient",
            "student_media_egress_receipts_valid",
        }
        checks = output.get("checks")
        gate = output.get("diagnosis_input_gate")
        receipt = gate.get("controller_receipt") if isinstance(gate, dict) else None
        if (
            process.returncode != 0
            or output.get("command") != "preflight"
            or output.get("mode") != "diagnose"
            or output.get("ready") is not True
            or output.get("ok") is not True
            or output.get("human_reviewed") is not False
            or output.get("blockers") != []
            or not isinstance(checks, dict)
            or any(checks.get(name) is not True for name in required_checks)
            or not isinstance(gate, dict)
            or gate.get("live_central_receipt_valid") is not True
            or not isinstance(receipt, dict)
        ):
            blockers = output.get("blockers") if isinstance(output.get("blockers"), list) else []
            raise RealDiagnosisBlocked(
                "live controller diagnosis receipt issuance blocked:"
                + ",".join(str(item) for item in blockers[:8])
            )
        self._validate_local_schema(receipt, "central_adapter_contract.schema.json")
        unsigned_receipt = dict(receipt)
        claimed_self_hash = unsigned_receipt.pop("receipt_sha256", None)
        if claimed_self_hash != _sha256_json(unsigned_receipt):
            raise RealDiagnosisBlocked("controller-issued receipt self-hash mismatch")
        if (
            receipt.get("profile_id") != scope.profile_id
            or receipt.get("input_sha256") != _sha256_json(attempts)
        ):
            raise RealDiagnosisBlocked("controller-issued receipt input binding mismatch")
        issuance = {
            "contract_version": "student_learning_controller_issue_cache_v1",
            "profile_id": scope.profile_id,
            "prepare_id": prepare_id,
            "state": "controller_issued_receipt_saved_private",
            "preflight_input_sha256": _sha256_json(
                self.store.read_derived(scope, f"real_input_{prepare_id.replace('-', '')}")
            ),
            "preflight_output_sha256": output_hash,
            "controller_receipt_sha256": receipt["receipt_sha256"],
            "validated_checks": sorted(required_checks),
            "governance_chain_validation": gate.get("governance_chain_validation", {}),
            "authoritative_for_unlock": False,
            "authority": "live_controller_receipt_plus_execute_time_reverification",
            "machine_status": "machine_only_not_human_reviewed",
            "human_reviewed": False,
        }
        return receipt, issuance

    def _run_controller_receipt_verify(
        self,
        *,
        scope: StudentScope,
        prepare_id: str,
        input_path: Path,
        receipt_path: Path,
    ) -> dict[str, Any]:
        controller = self.controller_root / "scripts" / "sh_chem_agent.py"
        if not controller.is_file():
            raise RealDiagnosisBlocked("live controller executable missing")
        process = subprocess.run(
            [
                sys.executable,
                str(controller),
                "diagnosis-receipt-verify",
                "--root",
                str(self.controller_root),
                "--input",
                str(input_path),
                "--receipt",
                str(receipt_path),
            ],
            capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            timeout=180,
            check=False,
        )
        try:
            output = json.loads(process.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RealDiagnosisBlocked("controller receipt verification output unreadable") from exc
        if not isinstance(output, dict):
            raise RealDiagnosisBlocked("controller receipt verification output is not an object")
        output_hash = _sha256_json(output)
        self.store.persist_derived(
            scope, f"controller_receipt_verify_{prepare_id.replace('-', '')}", output
        )
        if (
            process.returncode != 0
            or output.get("command") != "diagnosis-receipt-verify"
            or output.get("ok") is not True
            or output.get("valid") is not True
            or output.get("errors") != []
            or output.get("live_registry_revalidated") is not True
            or output.get("human_reviewed") is not False
        ):
            errors = output.get("errors") if isinstance(output.get("errors"), list) else []
            raise RealDiagnosisBlocked(
                "controller diagnosis receipt live verification blocked:"
                + ",".join(str(item) for item in errors[:8])
            )
        return {
            "valid": True,
            "live_registry_revalidated": True,
            "receipt_sha256": output.get("receipt_sha256"),
            "verify_output_sha256": output_hash,
            "human_reviewed": False,
        }

    @staticmethod
    def _validate_real_private_contract(profile: dict[str, Any], attempts: list[dict[str, Any]]) -> None:
        metadata = profile["metadata"]
        if metadata.get("synthetic") is not False or metadata.get("consent_recorded") is not True:
            raise RealDiagnosisBlocked("real private profile and consent required")
        event_types = {row["event_type"] for row in profile["inputs"]}
        missing_types = {"score", "grade_progress", "attempt", "timing"} - event_types
        if missing_types:
            raise RealDiagnosisBlocked("real private input types missing:" + ",".join(sorted(missing_types)))
        if not attempts:
            raise RealDiagnosisBlocked("real attempts missing")
        from jsonschema import Draft202012Validator, FormatChecker

        schema_path = Path(__file__).resolve().parents[2] / "sh-chem-db" / "kb" / "student_learning_v2" / "real_attempt.schema.json"
        validator = Draft202012Validator(
            json.loads(schema_path.read_text(encoding="utf-8")), format_checker=FormatChecker()
        )
        for attempt in attempts:
            errors = sorted(validator.iter_errors(attempt), key=lambda error: list(error.path))
            if errors:
                raise RealDiagnosisBlocked(f"real attempt schema invalid:{attempt.get('attempt_id')}:{errors[0].message}")
            evidence = attempt["question_evidence"]
            parent = evidence["canonical_parent_chain"]
            if attempt["question_id"] != parent["printed_question_id"]:
                raise RealDiagnosisBlocked(f"attempt question/printed parent mismatch:{attempt['attempt_id']}")
            if evidence["atomic_part_id"] != parent["atomic_part_id"]:
                raise RealDiagnosisBlocked(f"attempt atomic parent mismatch:{attempt['attempt_id']}")
            if evidence["canonical_parent_chain_sha256"] != _sha256_json(parent):
                raise RealDiagnosisBlocked(f"canonical parent hash mismatch:{attempt['attempt_id']}")
            if attempt["error_image_media_id"] not in evidence["media_ids"]:
                raise RealDiagnosisBlocked(f"attempt media parent binding mismatch:{attempt['attempt_id']}")
        timing_values: dict[str, list[int]] = {}
        for row in profile["inputs"]:
            if row["event_type"] != "timing" or not isinstance(row.get("payload"), dict):
                continue
            attempt_id = row["payload"].get("attempt_id")
            elapsed = row["payload"].get("elapsed_seconds")
            if isinstance(attempt_id, str) and isinstance(elapsed, int):
                timing_values.setdefault(attempt_id, []).append(elapsed)
        for attempt in attempts:
            values = timing_values.get(attempt.get("attempt_id"), [])
            if not values:
                raise RealDiagnosisBlocked(f"attempt timing event missing:{attempt.get('attempt_id')}")
            if not isinstance(attempt.get("elapsed_seconds"), int):
                raise RealDiagnosisBlocked(f"attempt elapsed_seconds missing:{attempt.get('attempt_id')}")
            if len(set(values)) != 1 or values[-1] != attempt["elapsed_seconds"]:
                raise RealDiagnosisBlocked(f"attempt/timing elapsed_seconds mismatch:{attempt.get('attempt_id')}")

    def build_week_bundle(
        self, *, profile_id: str, capability: str, week_number: int, mode: str,
        prepare_id: str | None = None,
    ) -> dict[str, Any]:
        scope = self.scope(profile_id, capability)
        profile = self.store.read_profile(scope)
        if mode == "real":
            provider_status = self.formal_content_provider_status()
            if provider_status.get("ready") is not True:
                raise ContentProviderNotConnected(
                    "generation ContentProvider v1 is blocked: "
                    + ",".join(provider_status.get("blockers", []))
                )
            if prepare_id is None:
                raise RealDiagnosisBlocked("real bundle requires issued prepare_id")
            diagnosis_job = self.execute_real_diagnosis(
                profile_id=profile_id, capability=capability, prepare_id=prepare_id
            )
            diagnosis = diagnosis_job["result"]["diagnosis"]
        else:
            diagnosis = self._diagnose(scope, profile, mode, None)
        history_state = self.store.read_learning_history(scope)
        plan = LearningEngine().build_plan(
            diagnosis,
            latest_grade_progress(profile),
            week_number,
            history_state=history_state,
        )
        if mode == "synthetic":
            content = FixtureContentProvider().render(diagnosis=diagnosis, plan=plan)
        elif mode == "real":
            content = self.formal_content_provider.render(diagnosis=diagnosis, plan=plan)
        else:
            raise StudentLearningError("bundle mode must be synthetic or real")
        bundle_path = self.store.private_bundle_path(scope, week_number)
        manifest = build_zip_bundle(
            bundle_path, diagnosis=diagnosis, plan=plan, content=content, timeline=profile["timeline"]
        )
        verification = verify_zip_bundle(bundle_path)
        updated_history = self.store.commit_week_history(
            scope,
            cycle_index=week_number,
            diagnosis=diagnosis,
            plan=plan,
            content=content,
            bundle_manifest=manifest,
        )
        return self.store.write_job(
            scope, job_type="build_week_bundle", status="complete",
            result={
                "bundle_path": str(bundle_path),
                "manifest": manifest,
                "verification": verification,
                "learning_history": updated_history,
            },
        )

    def get_job_status(
        self, *, profile_id: str, capability: str, job_id: str
    ) -> dict[str, Any]:
        return self.store.read_job(self.scope(profile_id, capability), job_id)

    def delete_student(self, *, profile_id: str, capability: str) -> dict[str, Any]:
        return self.store.delete_profile(self.scope(profile_id, capability))


def _read_payload(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("JSON payload must be an object")
    return value


def _capability() -> str:
    value = os.environ.get(CAPABILITY_ENV, "")
    if not value:
        raise AccessDenied(f"set {CAPABILITY_ENV} for private profile operations")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-student")
    create.add_argument("--grade", required=True)
    create.add_argument("--progress-json", required=True)
    create.add_argument("--retention-days", type=int, default=365)
    create.add_argument("--consent-recorded", action="store_true")
    create.add_argument("--synthetic", action="store_true")

    for name in ("select-student", "get-sanitization-receipt", "record-score", "record-progress", "record-attempt", "record-timing", "record-weakness", "prepare-real-diagnosis", "issue-real-diagnosis-receipt", "execute-real-diagnosis", "run-diagnosis", "build-week-bundle", "get-job-status", "delete-student", "save-raw-image"):
        command = sub.add_parser(name)
        command.add_argument("--profile-id", required=True)
        if name == "get-sanitization-receipt":
            command.add_argument("--media-id", required=True)
        elif name.startswith("record-"):
            command.add_argument("--payload-json", required=True)
        elif name == "run-diagnosis":
            command.add_argument("--mode", choices=("synthetic", "real"), required=True)
            command.add_argument("--prepare-id")
            command.add_argument("--controller-receipt-json")
        elif name in {"issue-real-diagnosis-receipt", "execute-real-diagnosis"}:
            command.add_argument("--prepare-id", required=True)
        elif name == "build-week-bundle":
            command.add_argument("--mode", choices=("synthetic", "real"), required=True)
            command.add_argument("--week", type=int, required=True)
            command.add_argument("--prepare-id")
            command.add_argument("--content-export-json")
        elif name == "get-job-status":
            command.add_argument("--job-id", required=True)
        elif name == "save-raw-image":
            command.add_argument("--image", required=True)
            command.add_argument("--media-type", choices=("image/png", "image/jpeg", "image/webp"), required=True)
            command.add_argument("--source-ref", required=True)
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    content_export_path = getattr(args, "content_export_json", None)
    content_provider = (
        StaticJsonFormalContentProvider(_read_payload(content_export_path) or {})
        if content_export_path is not None else None
    )
    api = GatewayStudentLearningAPI(args.root, formal_content_provider=content_provider)
    if args.command == "create-student":
        return api.create_student(
            grade=args.grade, grade_progress=_read_payload(args.progress_json) or {},
            consent_recorded=args.consent_recorded, retention_days=args.retention_days,
            synthetic=args.synthetic,
        )
    capability = _capability()
    common = {"profile_id": args.profile_id, "capability": capability}
    if args.command == "select-student":
        return api.select_student(**common)
    if args.command == "save-raw-image":
        return api.save_raw_error_image(
            **common, image_bytes=Path(args.image).read_bytes(), media_type=args.media_type,
            source_ref=args.source_ref,
        )
    if args.command == "get-sanitization-receipt":
        return api.get_sanitization_receipt(**common, media_id=args.media_id)
    event_types = {
        "record-score": "score", "record-progress": "grade_progress", "record-attempt": "attempt",
        "record-timing": "timing", "record-weakness": "declared_weakness",
    }
    if args.command in event_types:
        return api.record_input(
            **common, input_type=event_types[args.command], payload=_read_payload(args.payload_json) or {},
        )
    if args.command == "prepare-real-diagnosis":
        return api.prepare_real_diagnosis(**common)
    if args.command == "issue-real-diagnosis-receipt":
        return api.issue_real_diagnosis_receipt(**common, prepare_id=args.prepare_id)
    if args.command == "execute-real-diagnosis":
        return api.execute_real_diagnosis(**common, prepare_id=args.prepare_id)
    if args.command == "run-diagnosis":
        return api.run_diagnosis(
            **common,
            mode=args.mode,
            prepare_id=args.prepare_id,
            controller_receipt=_read_payload(args.controller_receipt_json),
        )
    if args.command == "build-week-bundle":
        return api.build_week_bundle(
            **common, week_number=args.week, mode=args.mode, prepare_id=args.prepare_id,
        )
    if args.command == "get-job-status":
        return api.get_job_status(**common, job_id=args.job_id)
    if args.command == "delete-student":
        return api.delete_student(**common)
    raise ValueError("unsupported command")


def main() -> int:
    try:
        args = build_parser().parse_args()
        envelope = success(dispatch(args))
    except Exception as exc:  # noqa: BLE001 - CLI maps all failures to safe envelopes
        envelope = map_exception(exc)
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return int(envelope["code"])


if __name__ == "__main__":
    raise SystemExit(main())
