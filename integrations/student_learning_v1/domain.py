#!/usr/bin/env python3
"""Privacy-first, machine-only student learning domain layer v1.

The implementation intentionally separates private profile storage from public
aggregation.  A bearer capability is required for every profile operation.
Synthetic evidence can exercise the whole loop; real diagnosis is blocked until
the central controller supplies a compatible, hash-bound receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import uuid
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any, Protocol

INTERFACE_VERSION = "student_learning_v1"
DOMAIN_VERSION = "2.2.0"
GENERATION_CONTENT_PROVIDER_CONTRACT = "student_learning_generation_content_provider_v1"
CLAIM_SCOPE_SYNTHETIC = "synthetic_fixture_only"
SUBJECT_KIND_SYNTHETIC = "synthetic_fixture_not_real_student"
MACHINE_STATUS = "machine_only_not_human_reviewed"
WEEKLY_MINUTES = 7 * 60
WEEKLY_SPLIT = {"main_weakness": 252, "secondary_weakness": 105, "maintenance_transfer": 63}
SYNTHETIC_CONTENT_STATUS = "structure_fixture_only"
GOVERNED_CONTRACT_FIXTURE_STATUS = "governed_contract_fixture_only"
REAL_PENDING_CONTENT_STATUS = "machine_only_real_content_descriptions_pending_generation"
REAL_FORMAL_CONTENT_STATUS = "machine_only_real_formal_content_candidate"
REQUIRED_FORMAL_DOCUMENTS = {
    f"{family}_{audience}.{extension}"
    for family in ("training", "retest")
    for audience in ("student", "answers")
    for extension in ("docx", "pdf")
}
FORMAL_DOCUMENT_ROLES = {
    "training_student",
    "training_answers",
    "retest_student",
    "retest_answers",
}
SHANGHAI_EXAM_PRINT_STYLE = {
    "base_preset": "compact_reference_guide",
    "named_override": "shanghai_exam_print",
    "page_size": "A4",
    "orientation": "portrait",
    "body_font_east_asia": "宋体",
    "heading_font_east_asia": "黑体",
    "black_white_readable": True,
    "real_numbering": True,
    "fixed_dxa_tables": True,
    "page_numbers": True,
    "nonofficial_label": "非官方",
    "suggested_scoring_points_label": "建议采分点",
}
ALLOWED_GRADES = {"高一", "高二", "高三", "待核验"}
ALLOWED_RESULTS = {"correct", "partially_correct", "incorrect", "blank", "not_scored"}
ALLOWED_ERRORS = {
    "concept",
    "chemical_language",
    "information_extraction",
    "model_selection",
    "quantitative",
    "experiment_design",
    "evidence_reasoning",
    "organic_route",
    "expression",
    "careless",
    "time_management",
    "source_or_ocr_uncertain",
    "unclassified",
}
DIRECT_IDENTIFIER_KEYS = {
    "name", "student_name", "real_name", "phone", "mobile", "id_card", "id_number",
    "student_id", "student_number", "address", "email", "school", "school_name",
    "class", "class_name", "homeroom", "姓名", "手机号", "身份证号", "学号", "住址",
    "地址", "邮箱", "学校", "班级",
}
IDENTIFIER_TEXT = re.compile(
    r"(?:姓名|学校|班级|学号|住址|地址|手机号|身份证号|student[ _-]?name|"
    r"school|class|address|phone|mobile)[：:]\s*\S+|(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{17}[0-9Xx](?!\d)",
    re.IGNORECASE,
)
class StudentLearningError(ValueError):
    """Base domain error."""


class AccessDenied(StudentLearningError):
    """A profile capability is absent, invalid, or belongs to another profile."""


class PrivacyViolation(StudentLearningError):
    """Input contains a prohibited direct identifier or unsafe media claim."""


class AggregationThresholdError(StudentLearningError):
    """A public aggregate would disclose a cohort smaller than k=5."""


class RealDiagnosisBlocked(StudentLearningError):
    """Real diagnosis evidence has not passed the central fail-closed adapter."""


@dataclass(frozen=True)
class StudentScope:
    profile_id: str
    capability: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", str(key), child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def assert_no_direct_identifiers(value: Any) -> None:
    normalized_keys = {item.casefold() for item in DIRECT_IDENTIFIER_KEYS}
    for path, key, child in _walk(value):
        if key is not None and key.casefold() in normalized_keys:
            raise PrivacyViolation(f"direct identifier key rejected: {path}")
        safe_machine_token = False
        if isinstance(child, str):
            safe_machine_token = re.fullmatch(r"[0-9a-f]{64}", child) is not None
            if not safe_machine_token:
                try:
                    safe_machine_token = str(uuid.UUID(child)) == child
                except (ValueError, AttributeError):
                    safe_machine_token = False
        if isinstance(child, str) and not safe_machine_token and IDENTIFIER_TEXT.search(child):
            raise PrivacyViolation(f"direct identifier text rejected: {path}")


class EvidenceAdapter(Protocol):
    def validate_attempts(
        self, *, profile_id: str, attempts: list[dict[str, Any]], synthetic: bool
    ) -> dict[str, Any]: ...


class FailClosedCentralAdapter:
    """Explicit pending integration point for restored central controller.

    This adapter never substitutes a historical report for live state.  It only
    permits unmistakably synthetic fixture evidence.  A later controller adapter
    must return the contract documented in ``central_adapter_contract.schema.json``.
    """

    def __init__(self, controller_path: Path | None = None) -> None:
        self.controller_path = controller_path

    def validate_attempts(
        self, *, profile_id: str, attempts: list[dict[str, Any]], synthetic: bool
    ) -> dict[str, Any]:
        if not synthetic:
            blocker = "central_controller_missing"
            if self.controller_path is not None and self.controller_path.exists():
                blocker = "central_controller_adapter_not_yet_hash_bound"
            raise RealDiagnosisBlocked(blocker)
        for attempt in attempts:
            evidence = attempt.get("question_evidence", {})
            if evidence.get("fixture_scope") != CLAIM_SCOPE_SYNTHETIC or evidence.get("synthetic") is not True:
                raise RealDiagnosisBlocked("synthetic_attempt_lacks_unmistakable_fixture_evidence")
            required = {
                "canonical_atomic_unit_id", "source_group_id", "question_id", "knowledge_tag",
                "knowledge_name", "ability_tag", "ability_name",
            }
            missing = sorted(required - set(evidence))
            if missing:
                raise StudentLearningError(f"synthetic evidence fields missing: {missing}")
            if evidence["question_id"] != attempt.get("question_id"):
                raise StudentLearningError("question evidence binding mismatch")
        return {
            "contract_version": "synthetic_fixture_adapter_v1",
            "allowed": True,
            "profile_id": profile_id,
            "input_sha256": _sha256_json(attempts),
            "claim_scope": CLAIM_SCOPE_SYNTHETIC,
            "machine_status": MACHINE_STATUS,
            "validated_attempt_count": len(attempts),
            "live_central_state_used": False,
            "limitations": ["synthetic evidence adapter only", "real diagnosis remains fail-closed"],
        }


class ControllerReceiptAdapter:
    """Consume a v2 receipt only after live controller revalidation."""

    def __init__(
        self,
        receipt: dict[str, Any],
        live_verification: dict[str, Any] | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.receipt = receipt
        self.live_verification = live_verification
        self.schema_path = schema_path or Path(__file__).resolve().parent / "central_adapter_contract.schema.json"

    def validate_attempts(
        self, *, profile_id: str, attempts: list[dict[str, Any]], synthetic: bool
    ) -> dict[str, Any]:
        if synthetic:
            raise RealDiagnosisBlocked("central receipt adapter cannot relabel synthetic input")
        try:
            from jsonschema import Draft202012Validator, FormatChecker

            validator = Draft202012Validator(_read_json(self.schema_path), format_checker=FormatChecker())
            errors = sorted(validator.iter_errors(self.receipt), key=lambda error: list(error.path))
        except Exception as exc:
            raise RealDiagnosisBlocked(f"central_receipt_schema_unavailable:{type(exc).__name__}") from exc
        if errors:
            raise RealDiagnosisBlocked(f"central_receipt_schema_invalid:{errors[0].message}")
        unsigned_receipt = dict(self.receipt)
        claimed_self_hash = unsigned_receipt.pop("receipt_sha256", None)
        if claimed_self_hash != _sha256_json(unsigned_receipt):
            raise RealDiagnosisBlocked("central_receipt_self_hash_mismatch")
        if (
            self.receipt.get("contract_version") != "central_diagnosis_adapter_v2"
            or self.receipt.get("receipt_type") != "controller_issued_live_registry_binding"
        ):
            raise RealDiagnosisBlocked("controller_issued_v2_receipt_required")
        verification = self.live_verification
        if (
            not isinstance(verification, dict)
            or verification.get("valid") is not True
            or verification.get("live_registry_revalidated") is not True
            or verification.get("receipt_sha256") != self.receipt.get("receipt_sha256")
            or verification.get("human_reviewed") is not False
        ):
            raise RealDiagnosisBlocked("central_receipt_live_reverification_required")
        if self.receipt.get("allowed") is not True or self.receipt.get("blockers"):
            raise RealDiagnosisBlocked("central_receipt_denied_or_blocked")
        if self.receipt.get("live_central_state_used") is not True:
            raise RealDiagnosisBlocked("central_receipt_did_not_use_live_state")
        if self.receipt.get("profile_id") != profile_id:
            raise RealDiagnosisBlocked("central_receipt_profile_mismatch")
        if self.receipt.get("input_sha256") != _sha256_json(attempts):
            raise RealDiagnosisBlocked("central_receipt_input_hash_mismatch")
        rows = {row["attempt_id"]: row for row in self.receipt.get("validated_attempts", [])}
        if set(rows) != {attempt["attempt_id"] for attempt in attempts}:
            raise RealDiagnosisBlocked("central_receipt_attempt_set_mismatch")
        for attempt in attempts:
            evidence = attempt.get("question_evidence", {})
            if evidence.get("synthetic") is True or evidence.get("fixture_scope") == CLAIM_SCOPE_SYNTHETIC:
                raise RealDiagnosisBlocked("central receipt cannot promote explicit synthetic evidence to real")
            if not isinstance(attempt.get("error_image_media_id"), str) or not isinstance(attempt.get("image_egress_receipt_sha256"), str):
                raise RealDiagnosisBlocked(f"real attempt image binding missing:{attempt.get('attempt_id')}")
            row = rows[attempt["attempt_id"]]
            if row.get("allowed") is not True or row.get("blockers"):
                raise RealDiagnosisBlocked(f"central_receipt_attempt_blocked:{attempt['attempt_id']}")
            if row.get("question_id") != attempt.get("question_id"):
                raise RealDiagnosisBlocked(f"central_receipt_question_mismatch:{attempt['attempt_id']}")
            if row.get("atomic_part_id") != evidence.get("atomic_part_id"):
                raise RealDiagnosisBlocked(f"central_receipt_atomic_part_mismatch:{attempt['attempt_id']}")
            if row.get("evidence_sha256") != _sha256_json(evidence):
                raise RealDiagnosisBlocked(f"central_receipt_evidence_hash_mismatch:{attempt['attempt_id']}")
            if row.get("canonical_parent_chain_sha256") != evidence.get("canonical_parent_chain_sha256"):
                raise RealDiagnosisBlocked(f"central_receipt_parent_hash_mismatch:{attempt['attempt_id']}")
            if row.get("governance_state") != "automated_verified_candidate":
                raise RealDiagnosisBlocked(f"central_receipt_governance_state_insufficient:{attempt['attempt_id']}")
        return self.receipt


class LocalImageSanitizer:
    """Retained only as a fail-closed compatibility symbol.

    Student images are no longer recognized, redacted, or transformed by a
    local text-recognition pipeline.  New uploads are saved unchanged in the
    private profile root and wait for the separately gated page-vision flow.
    """

    @staticmethod
    def _identity_text_boxes(
        _image: Any,
    ) -> tuple[list[tuple[int, int, int, int, str]], list[str], dict[str, Any]]:
        raise RealDiagnosisBlocked("legacy_local_text_recognition_permanently_disabled")

    def sanitize(
        self, _image_bytes: bytes, _media_type: str
    ) -> tuple[bytes | None, dict[str, Any]]:
        raise RealDiagnosisBlocked("legacy_image_sanitizer_permanently_disabled")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_score_payload(payload: dict[str, Any]) -> None:
    earned = payload.get("earned")
    maximum = payload.get("maximum")
    if not _number(earned) or not _number(maximum) or maximum <= 0:
        raise StudentLearningError("score requires numeric earned and positive maximum")
    if not 0 <= float(earned) <= float(maximum):
        raise StudentLearningError("score earned must be within 0..maximum")
    if "assessment_ref" in payload and (
        not isinstance(payload["assessment_ref"], str) or not payload["assessment_ref"].strip()
    ):
        raise StudentLearningError("score assessment_ref must be a non-empty string")


def _validate_progress_payload(payload: dict[str, Any]) -> None:
    module = payload.get("module")
    completion = payload.get("completion_percent")
    if not isinstance(module, str) or not module.strip():
        raise StudentLearningError("grade progress requires a non-empty module")
    if not _number(completion) or not 0 <= float(completion) <= 100:
        raise StudentLearningError("grade progress completion_percent must be within 0..100")
    if "semester" in payload and (
        not isinstance(payload["semester"], str) or not payload["semester"].strip()
    ):
        raise StudentLearningError("grade progress semester must be a non-empty string")


def _validate_declared_weakness_payload(payload: dict[str, Any]) -> None:
    tags = payload.get("tag_ids")
    if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag for tag in tags):
        raise StudentLearningError("declared weakness requires non-empty tag_ids")
    if len(set(tags)) != len(tags):
        raise StudentLearningError("declared weakness tag_ids must be unique")


def _validate_timing_payload(payload: dict[str, Any]) -> None:
    if payload.get("kind") == "error_image_import":
        if not isinstance(payload.get("media"), dict):
            raise StudentLearningError("image-import timing requires media record")
        return
    attempt_id = payload.get("attempt_id")
    elapsed = payload.get("elapsed_seconds")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise StudentLearningError("timing requires attempt_id")
    if not isinstance(elapsed, int) or isinstance(elapsed, bool) or elapsed < 0:
        raise StudentLearningError("timing elapsed_seconds must be a non-negative integer")


def latest_grade_progress(profile: dict[str, Any]) -> dict[str, Any]:
    events = [
        row["payload"]
        for row in profile.get("inputs", [])
        if row.get("event_type") == "grade_progress" and isinstance(row.get("payload"), dict)
    ]
    if not events:
        raise StudentLearningError("latest grade progress input is required")
    _validate_progress_payload(events[-1])
    return dict(events[-1])


class StudentStore:
    """Local per-profile roots protected by unguessable bearer capabilities."""

    def __init__(self, data_root: Path, image_sanitizer: LocalImageSanitizer | None = None) -> None:
        self.data_root = data_root.resolve()
        self.profiles_root = self.data_root / "private_profiles"
        self.tombstones_root = self.data_root / "deletion_receipts"
        self.image_sanitizer = image_sanitizer or LocalImageSanitizer()
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.tombstones_root.mkdir(parents=True, exist_ok=True)

    def create_profile(
        self,
        *,
        grade: str,
        grade_progress: dict[str, Any],
        consent_recorded: bool,
        retention_days: int,
        synthetic: bool = False,
    ) -> StudentScope:
        if grade not in ALLOWED_GRADES:
            raise StudentLearningError("unsupported grade")
        if not consent_recorded:
            raise PrivacyViolation("consent_recorded must be true")
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
            raise StudentLearningError("retention_days must be between 1 and 3650")
        assert_no_direct_identifiers(grade_progress)
        _validate_progress_payload(grade_progress)
        profile_id = str(uuid.uuid4())
        capability = secrets.token_urlsafe(32)
        profile_dir = self._unchecked_profile_dir(profile_id)
        profile_dir.mkdir(parents=True, exist_ok=False)
        metadata = {
            "schema_version": "private_profile_root_v1",
            "profile_id": profile_id,
            "capability_sha256": _sha256_bytes(capability.encode("utf-8")),
            "created_at": _utc_now(),
            "updated_at": None,
            "grade": grade,
            "grade_progress": grade_progress,
            "consent_recorded": True,
            "retention_days": retention_days,
            "storage_scope": "local_private_profile_root",
            "contains_direct_identifiers": False,
            "synthetic": bool(synthetic),
            "claim_scope": CLAIM_SCOPE_SYNTHETIC if synthetic else "private_input_only_no_diagnosis_claim",
            "machine_status": MACHINE_STATUS,
        }
        _write_json(profile_dir / "metadata.json", metadata)
        _append_jsonl(profile_dir / "timeline.jsonl", {
            "event_id": str(uuid.uuid4()), "event_type": "profile_created", "occurred_at": metadata["created_at"],
            "claim_scope": metadata["claim_scope"], "machine_status": MACHINE_STATUS,
        })
        return StudentScope(profile_id, capability)

    def _unchecked_profile_dir(self, profile_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(profile_id))
        except (ValueError, AttributeError) as exc:
            raise AccessDenied("invalid profile id") from exc
        candidate = (self.profiles_root / normalized[:2] / normalized).resolve()
        if self.profiles_root not in candidate.parents:
            raise AccessDenied("profile path escapes private root")
        return candidate

    def _authorize(self, scope: StudentScope) -> tuple[Path, dict[str, Any]]:
        profile_dir = self._unchecked_profile_dir(scope.profile_id)
        metadata_path = profile_dir / "metadata.json"
        if not metadata_path.is_file():
            raise AccessDenied("profile unavailable")
        metadata = _read_json(metadata_path)
        candidate = _sha256_bytes(scope.capability.encode("utf-8"))
        if not hmac.compare_digest(candidate, metadata["capability_sha256"]):
            raise AccessDenied("profile capability rejected")
        return profile_dir, metadata

    def read_profile(self, scope: StudentScope) -> dict[str, Any]:
        profile_dir, metadata = self._authorize(scope)
        return {
            "metadata": {key: value for key, value in metadata.items() if key != "capability_sha256"},
            "inputs": _read_jsonl(profile_dir / "inputs.jsonl"),
            "timeline": _read_jsonl(profile_dir / "timeline.jsonl"),
        }

    def append_input(self, scope: StudentScope, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if event_type not in {"score", "grade_progress", "declared_weakness", "attempt", "timing"}:
            raise StudentLearningError("unsupported input event type")
        assert_no_direct_identifiers(payload)
        if event_type == "score":
            _validate_score_payload(payload)
        elif event_type == "grade_progress":
            _validate_progress_payload(payload)
        elif event_type == "declared_weakness":
            _validate_declared_weakness_payload(payload)
        elif event_type == "attempt":
            LearningEngine._validate_attempt(payload)
        elif event_type == "timing":
            _validate_timing_payload(payload)
        profile_dir, metadata = self._authorize(scope)
        event = {
            "event_id": str(uuid.uuid4()), "event_type": event_type, "occurred_at": _utc_now(),
            "payload": payload, "claim_scope": metadata["claim_scope"], "machine_status": MACHINE_STATUS,
        }
        _append_jsonl(profile_dir / "inputs.jsonl", event)
        self._touch(profile_dir, metadata)
        return event

    def import_error_image(
        self,
        scope: StudentScope,
        *,
        image_bytes: bytes,
        media_type: str,
        source_ref: str,
        deidentified: bool | None = None,
        contains_face: bool | None = None,
    ) -> dict[str, Any]:
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise StudentLearningError("unsupported error-image media type")
        if not image_bytes:
            raise StudentLearningError("empty image rejected")
        assert_no_direct_identifiers({"source_ref": source_ref})
        profile_dir, metadata = self._authorize(scope)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]
        media_id = str(uuid.uuid4())
        media_path = profile_dir / "media" / "raw" / f"{media_id}{extension}"
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(image_bytes)
        # P0 hard boundary: saving a student image must never invoke a local or
        # remote text-recognition provider.  Historical sanitizer artifacts are
        # intentionally left on disk, but no new artifact is produced here.
        sanitization = {
            "contract_version": "shchem.student-image-local-hold.v1",
            "state": "awaiting_visual_provider",
            "egress_allowed": False,
            "model_egress_allowed": False,
            "allowed_consumers": [],
            "raw_local_save_allowed": True,
            "raw_model_access_allowed": False,
            "ocr_invoked": False,
            "transport_attempt_count": 0,
            "legacy_derived_evidence_allowed": False,
            "legacy_sanitized_media_allowed_as_model_input": False,
            "processing_location": "local_private_hold_only",
            "reasons": ["teacher_confirmed_visual_pages_required"],
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
            "media_id": media_id,
            "profile_id": scope.profile_id,
            "raw_sha256": _sha256_bytes(image_bytes),
            "sanitized_sha256": None,
            "input_deidentified_assertion": deidentified,
            "input_contains_face_assertion": contains_face,
            "claim_scope": metadata["claim_scope"],
        }
        receipt_path = profile_dir / "media" / "receipts" / f"{media_id}.json"
        _write_json(receipt_path, sanitization)
        record = {
            "media_id": media_id,
            "media_type": media_type,
            "source_ref": source_ref,
            "sha256": _sha256_bytes(image_bytes),
            "byte_size": len(image_bytes),
            "deidentified_assertion": deidentified,
            "contains_face_assertion": contains_face,
            "privacy_review_status": "awaiting_visual_provider",
            "deeptutor_egress_allowed": False,
            "sanitization_reasons": sanitization["reasons"],
            "sanitized_content_extraction": None,
            "ocr_invoked": False,
            "transport_attempt_count": 0,
            "synthetic": metadata["synthetic"],
        }
        self.append_input(scope, "timing", {"kind": "error_image_import", "media": record})
        return record

    def get_image_sanitization_status(self, scope: StudentScope, media_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}", media_id):
            raise AccessDenied("invalid media id")
        profile_dir, _ = self._authorize(scope)
        receipt_path = profile_dir / "media" / "receipts" / f"{media_id}.json"
        if not receipt_path.is_file():
            raise AccessDenied("media receipt unavailable")
        receipt = _read_json(receipt_path)
        if receipt.get("profile_id") != scope.profile_id:
            raise AccessDenied("media receipt profile mismatch")
        return receipt

    def validate_attempt_image_receipts(
        self, scope: StudentScope, attempts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        validated: list[dict[str, Any]] = []
        for attempt in attempts:
            media_id = attempt.get("error_image_media_id")
            receipt_hash = attempt.get("image_egress_receipt_sha256")
            if not isinstance(media_id, str) or not isinstance(receipt_hash, str):
                raise RealDiagnosisBlocked(f"attempt image receipt binding missing:{attempt.get('attempt_id')}")
            receipt = self.get_image_sanitization_status(scope, media_id)
            evidence_media = attempt.get("question_evidence", {}).get("media_ids")
            if not isinstance(evidence_media, list) or media_id not in evidence_media:
                raise RealDiagnosisBlocked(f"attempt evidence media binding missing:{attempt.get('attempt_id')}")
            if _sha256_json(receipt) != receipt_hash:
                raise RealDiagnosisBlocked(f"attempt image receipt hash mismatch:{attempt.get('attempt_id')}")
            # Neither a new local-hold receipt nor a historical sanitizer
            # receipt can unlock the old real-diagnosis model path.  Student
            # page pixels may leave only through the new submission-specific
            # visual policy and exact-hash privacy decision.
            raise RealDiagnosisBlocked(
                f"legacy_attempt_image_path_disabled:{attempt.get('attempt_id')}"
            )
        return {
            "contract_version": "real_attempt_image_egress_audit_v1",
            "validated_attempt_count": len(validated),
            "validated": validated,
            "all_raw_model_access_blocked": True,
            "all_sanitized_egress_allowed": True,
        }

    def read_sanitized_image_for_model(self, scope: StudentScope, media_id: str) -> bytes:
        self.get_image_sanitization_status(scope, media_id)
        raise AccessDenied("legacy sanitized media is read-only and forbidden as model input")

    def persist_derived(self, scope: StudentScope, name: str, value: dict[str, Any]) -> Path:
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
            raise StudentLearningError("unsafe derived artifact name")
        profile_dir, metadata = self._authorize(scope)
        assert_no_direct_identifiers(value)
        target = profile_dir / "derived" / f"{name}.json"
        _write_json(target, value)
        _append_jsonl(profile_dir / "timeline.jsonl", {
            "event_id": str(uuid.uuid4()), "event_type": "derived_artifact_written", "occurred_at": _utc_now(),
            "artifact": target.name, "artifact_sha256": _sha256_bytes(target.read_bytes()),
            "claim_scope": metadata["claim_scope"], "machine_status": MACHINE_STATUS,
        })
        self._touch(profile_dir, metadata)
        return target

    def read_derived(self, scope: StudentScope, name: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
            raise AccessDenied("unsafe derived artifact name")
        profile_dir, _ = self._authorize(scope)
        target = profile_dir / "derived" / f"{name}.json"
        if not target.is_file():
            raise AccessDenied("derived artifact unavailable")
        value = _read_json(target)
        if not isinstance(value, dict) or value.get("profile_id") not in {None, scope.profile_id}:
            raise AccessDenied("derived artifact profile mismatch")
        return value

    def read_learning_history(self, scope: StudentScope) -> dict[str, Any]:
        profile_dir, _ = self._authorize(scope)
        history_root = profile_dir / "learning_history"
        cycle_root = history_root / "cycles"
        paths = sorted(cycle_root.glob("cycle-*.json")) if cycle_root.is_dir() else []
        records: list[dict[str, Any]] = []
        previous_hash: str | None = None
        for expected_index, path in enumerate(paths, 1):
            if path.name != f"cycle-{expected_index:06d}.json":
                raise AccessDenied("learning history cycle sequence is non-contiguous")
            record = _read_json(path)
            claimed_hash = record.get("record_sha256")
            unsigned = dict(record)
            unsigned.pop("record_sha256", None)
            actual_hash = _sha256_json(unsigned)
            if (
                claimed_hash != actual_hash
                or record.get("profile_id") != scope.profile_id
                or record.get("cycle_index") != expected_index
                or record.get("previous_cycle_record_sha256") != previous_hash
            ):
                raise AccessDenied(f"learning history hash chain invalid at cycle {expected_index}")
            records.append(record)
            previous_hash = actual_hash
        head_path = history_root / "head.json"
        if head_path.is_file():
            head = _read_json(head_path)
            if (
                head.get("profile_id") != scope.profile_id
                or head.get("latest_cycle_index") != len(records)
                or head.get("history_head_sha256") != previous_hash
                or head.get("cycle_record_count") != len(records)
            ):
                raise AccessDenied("learning history head binding invalid")
        elif records:
            raise AccessDenied("learning history head missing")
        next_cycle_index = len(records) + 1
        mastery_history = [
            {"cycle_index": record["cycle_index"], "snapshot": record["mastery_snapshot"]}
            for record in records
        ]
        focus_history = [
            {"cycle_index": record["cycle_index"], **record["focus"]}
            for record in records
        ]
        spaced_review_due = sorted(
            [
                due
                for record in records
                for due in record["spaced_review_scheduled"]
                if due["due_cycle_index"] >= next_cycle_index
            ],
            key=lambda row: (row["due_cycle_index"], row["tag_id"], row["source_cycle_index"]),
        )
        latest = records[-1] if records else None
        return {
            "schema_version": "student_learning_history_state_v1",
            "profile_id": scope.profile_id,
            "cycle_record_count": len(records),
            "latest_cycle_index": len(records),
            "next_cycle_index": next_cycle_index,
            "history_head_sha256": previous_hash,
            "mastery_history": mastery_history,
            "focus_history": focus_history,
            "spaced_review_due": spaced_review_due,
            "latest_week_over_week_delta": latest["week_over_week_delta"] if latest else None,
            "latest_focus": latest["focus"] if latest else None,
            "next_week_objectives": latest["next_week_objectives"] if latest else [],
            "long_term_teaching_effectiveness_verified": False,
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
        }

    def commit_week_history(
        self,
        scope: StudentScope,
        *,
        cycle_index: int,
        diagnosis: dict[str, Any],
        plan: dict[str, Any],
        content: dict[str, Any],
        bundle_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        profile_dir, metadata = self._authorize(scope)
        state = self.read_learning_history(scope)
        if cycle_index != state["next_cycle_index"]:
            raise StudentLearningError(
                f"learning history requires cycle {state['next_cycle_index']}; got {cycle_index}"
            )
        if (
            diagnosis.get("profile_id") != scope.profile_id
            or plan.get("profile_id") != scope.profile_id
            or bundle_manifest.get("profile_id") != scope.profile_id
            or plan.get("cycle_index") != cycle_index
        ):
            raise StudentLearningError("learning history profile/cycle binding mismatch")
        if plan.get("schedule_audit") != audit_weekly_schedule(plan.get("weekly_schedule", []), cycle_index):
            raise StudentLearningError("learning history plan schedule audit mismatch")
        focus = diagnosis.get("weekly_focus", {})
        mastery_snapshot = [
            {
                "dimension": row["dimension"],
                "tag_id": row["tag_id"],
                "status": row["status"],
                "loss_rate": row["loss_rate"],
                "valid_atomic_part_count": row["valid_atomic_part_count"],
                "independent_source_count": row["independent_source_count"],
            }
            for row in diagnosis.get("conclusions", [])
        ]
        spaced_review_scheduled: list[dict[str, Any]] = []
        for mastery in mastery_snapshot:
            offsets = (1, 2, 4) if "weakness" in mastery["status"] else (2, 4, 8)
            for offset in offsets:
                spaced_review_scheduled.append({
                    "tag_id": mastery["tag_id"],
                    "dimension": mastery["dimension"],
                    "source_cycle_index": cycle_index,
                    "due_cycle_index": cycle_index + offset,
                    "basis": mastery["status"],
                })
        record = {
            "schema_version": "student_learning_week_history_record_v1",
            "profile_id": scope.profile_id,
            "cycle_index": cycle_index,
            "claim_scope": diagnosis["claim_scope"],
            "subject_kind": diagnosis["subject_kind"],
            "previous_cycle_record_sha256": state["history_head_sha256"],
            "diagnosis_sha256": _sha256_json(diagnosis),
            "plan_sha256": _sha256_json(plan),
            "content_sha256": _sha256_json(content),
            "retest_hashes": {
                "day7_tasks_sha256": _sha256_json(content.get("retest_day7", [])),
                "day7_answers_sha256": _sha256_json(content.get("retest_day7_answers", [])),
                "day14_tasks_sha256": _sha256_json(content.get("retest_day14", [])),
                "day14_answers_sha256": _sha256_json(content.get("retest_day14_answers", [])),
            },
            "bundle_manifest_sha256": _sha256_json(bundle_manifest),
            "bundle_zip_sha256": bundle_manifest.get("zip_sha256"),
            "mastery_snapshot": mastery_snapshot,
            "week_over_week_delta": diagnosis.get("performance_delta", {}),
            "focus": {
                "main": focus.get("main"),
                "secondary": focus.get("secondary"),
                "evidence_overlap_audit": focus.get("evidence_overlap_audit", {}),
            },
            "spaced_review_scheduled": spaced_review_scheduled,
            "next_week_objectives": plan.get("next_week_objectives", []),
            "content_completeness_status": content.get("content_completeness_status"),
            "complete_week_pack": bundle_manifest.get("complete_week_pack"),
            "teaching_effectiveness_unverified": True,
            "long_term_effect_claimed": False,
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
        }
        record["record_sha256"] = _sha256_json(record)
        history_root = profile_dir / "learning_history"
        target = history_root / "cycles" / f"cycle-{cycle_index:06d}.json"
        if target.exists():
            raise StudentLearningError("learning history cycle is immutable and already exists")
        _write_json(target, record)
        _write_json(history_root / "head.json", {
            "schema_version": "student_learning_history_head_v1",
            "profile_id": scope.profile_id,
            "latest_cycle_index": cycle_index,
            "cycle_record_count": cycle_index,
            "history_head_sha256": record["record_sha256"],
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
        })
        _append_jsonl(profile_dir / "timeline.jsonl", {
            "event_id": str(uuid.uuid4()),
            "event_type": "learning_history_cycle_committed",
            "occurred_at": _utc_now(),
            "cycle_index": cycle_index,
            "record_sha256": record["record_sha256"],
            "claim_scope": metadata["claim_scope"],
            "machine_status": MACHINE_STATUS,
        })
        self._touch(profile_dir, metadata)
        return self.read_learning_history(scope)

    def write_job(self, scope: StudentScope, *, job_type: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        if job_type not in {"prepare_real_diagnosis", "issue_real_diagnosis_receipt", "diagnose_synthetic", "diagnose_real", "build_week_bundle"}:
            raise StudentLearningError("unsupported job type")
        if status not in {"awaiting_controller", "complete", "blocked", "failed"}:
            raise StudentLearningError("unsupported job status")
        profile_dir, metadata = self._authorize(scope)
        job = {
            "api_version": "gateway_student_learning_api_v1",
            "job_id": str(uuid.uuid4()),
            "profile_id": scope.profile_id,
            "job_type": job_type,
            "status": status,
            "updated_at": _utc_now(),
            "result": result,
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
        }
        assert_no_direct_identifiers(job)
        _write_json(profile_dir / "jobs" / f"{job['job_id']}.json", job)
        _append_jsonl(profile_dir / "timeline.jsonl", {
            "event_id": str(uuid.uuid4()), "event_type": "job_status", "occurred_at": job["updated_at"],
            "job_id": job["job_id"], "job_type": job_type, "status": status,
            "claim_scope": metadata["claim_scope"], "machine_status": MACHINE_STATUS,
        })
        return job

    def read_job(self, scope: StudentScope, job_id: str) -> dict[str, Any]:
        try:
            normalized = str(uuid.UUID(job_id))
        except (ValueError, AttributeError) as exc:
            raise AccessDenied("invalid job id") from exc
        profile_dir, _ = self._authorize(scope)
        target = profile_dir / "jobs" / f"{normalized}.json"
        if not target.is_file():
            raise AccessDenied("job unavailable")
        job = _read_json(target)
        if job.get("profile_id") != scope.profile_id:
            raise AccessDenied("job profile mismatch")
        return job

    def private_bundle_path(self, scope: StudentScope, week_number: int) -> Path:
        if not isinstance(week_number, int) or isinstance(week_number, bool) or week_number < 1:
            raise StudentLearningError("invalid week number")
        profile_dir, _ = self._authorize(scope)
        return profile_dir / "bundles" / f"week-{week_number}.zip"

    def delete_profile(self, scope: StudentScope) -> dict[str, Any]:
        profile_dir, metadata = self._authorize(scope)
        if profile_dir.parent.parent != self.profiles_root:
            raise AccessDenied("refusing broad deletion target")
        receipt = {
            "schema_version": "profile_deletion_receipt_v1",
            "receipt_id": str(uuid.uuid4()),
            "deleted_profile_digest": _sha256_bytes(("student-learning-delete-v1:" + scope.profile_id).encode("utf-8")),
            "deleted_at": _utc_now(),
            "deletion_scope": "profile_root_and_all_private_derived_artifacts",
            "recovery_status": "not_recoverable_through_application",
            "physical_media_caveat": "filesystem deletion is not a guarantee of forensic erasure on all storage media",
            "synthetic": metadata["synthetic"],
            "machine_status": MACHINE_STATUS,
        }
        shutil.rmtree(profile_dir)
        _write_json(self.tombstones_root / f"{receipt['receipt_id']}.json", receipt)
        return receipt

    @staticmethod
    def _touch(profile_dir: Path, metadata: dict[str, Any]) -> None:
        metadata["updated_at"] = _utc_now()
        _write_json(profile_dir / "metadata.json", metadata)


def _attempt_loss(attempt: dict[str, Any]) -> float | None:
    result = attempt.get("result", {})
    status = result.get("status")
    if status == "not_scored":
        return None
    earned = result.get("earned_points")
    maximum = result.get("max_points")
    if isinstance(earned, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
        if not 0 <= earned <= maximum:
            raise StudentLearningError("attempt points out of range")
        return 1.0 - (float(earned) / float(maximum))
    return {"correct": 0.0, "partially_correct": 0.5, "incorrect": 1.0, "blank": 1.0}.get(status)


def audit_weekly_schedule(schedule: list[dict[str, Any]], week_number: int) -> dict[str, Any]:
    """Recompute the weekly contract from activities; declarations never unlock it."""
    if not isinstance(week_number, int) or isinstance(week_number, bool) or week_number <= 0:
        raise StudentLearningError("weekly schedule audit requires a positive cycle index")
    if not isinstance(schedule, list) or not schedule:
        raise StudentLearningError("weekly schedule must contain activities")
    bucket_minutes: Counter[str] = Counter()
    daily_minutes: Counter[int] = Counter()
    retest_minutes = 0
    seen_ids: set[str] = set()
    offset = (week_number - 1) * 7
    for index, row in enumerate(schedule):
        if not isinstance(row, dict):
            raise StudentLearningError(f"weekly schedule activity {index} must be an object")
        required = {"schedule_id", "week_day", "cycle_day", "focus_bucket", "minutes", "retest_component"}
        missing = sorted(required - set(row))
        if missing:
            raise StudentLearningError(f"weekly schedule activity fields missing:{missing}")
        schedule_id = row["schedule_id"]
        if not isinstance(schedule_id, str) or not schedule_id or schedule_id in seen_ids:
            raise StudentLearningError("weekly schedule IDs must be unique non-empty strings")
        seen_ids.add(schedule_id)
        week_day = row["week_day"]
        cycle_day = row["cycle_day"]
        minutes = row["minutes"]
        bucket = row["focus_bucket"]
        if not isinstance(week_day, int) or week_day not in range(1, 8):
            raise StudentLearningError(f"weekly schedule invalid week_day:{schedule_id}")
        if cycle_day != offset + week_day:
            raise StudentLearningError(f"weekly schedule cycle_day mismatch:{schedule_id}")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
            raise StudentLearningError(f"weekly schedule minutes must be positive integer:{schedule_id}")
        if bucket not in WEEKLY_SPLIT:
            raise StudentLearningError(f"weekly schedule invalid focus_bucket:{schedule_id}")
        if not isinstance(row["retest_component"], bool):
            raise StudentLearningError(f"weekly schedule retest_component must be boolean:{schedule_id}")
        if row["retest_component"] and week_day != 7:
            raise StudentLearningError(f"weekly retest must be the day-7 checkpoint:{schedule_id}")
        bucket_minutes[bucket] += minutes
        daily_minutes[week_day] += minutes
        if row["retest_component"]:
            retest_minutes += minutes
    if set(daily_minutes) != set(range(1, 8)):
        raise StudentLearningError("every day in the seven-day cycle must have scheduled minutes")
    if any(daily_minutes[day] != 60 for day in range(1, 8)):
        raise StudentLearningError(
            f"each week_day must schedule exactly 60 minutes:{dict(sorted(daily_minutes.items()))}"
        )
    actual_buckets = {key: bucket_minutes[key] for key in WEEKLY_SPLIT}
    if actual_buckets != WEEKLY_SPLIT:
        raise StudentLearningError(f"weekly focus allocation drift:{actual_buckets}")
    total = sum(daily_minutes.values())
    if total != WEEKLY_MINUTES:
        raise StudentLearningError(f"weekly scheduled minutes drift:{total}")
    if retest_minutes <= 0:
        raise StudentLearningError("week-end retest checkpoint has no scheduled minutes")
    return {
        "audit_version": "weekly_schedule_recomputed_v1",
        "week_number": week_number,
        "cycle_day_range": [offset + 1, offset + 7],
        "activity_count": len(schedule),
        "total_minutes": total,
        "focus_bucket_minutes": actual_buckets,
        "daily_minutes": {str(day): daily_minutes[day] for day in range(1, 8)},
        "retest_minutes": retest_minutes,
        "retest_cycle_day": offset + 7,
        "contract_match": True,
    }


class LearningEngine:
    def __init__(self, evidence_adapter: EvidenceAdapter | None = None) -> None:
        self.evidence_adapter = evidence_adapter or FailClosedCentralAdapter()

    def diagnose(self, profile: dict[str, Any]) -> dict[str, Any]:
        metadata = profile["metadata"]
        claim_scope = CLAIM_SCOPE_SYNTHETIC if metadata["synthetic"] else "machine_only_real_student_candidate"
        subject_kind = SUBJECT_KIND_SYNTHETIC if metadata["synthetic"] else "anonymous_real_student_private"
        attempts = [row["payload"] for row in profile["inputs"] if row["event_type"] == "attempt"]
        if not attempts:
            raise StudentLearningError("diagnosis requires attempts")
        for attempt in attempts:
            self._validate_attempt(attempt)
        adapter_receipt = self.evidence_adapter.validate_attempts(
            profile_id=metadata["profile_id"], attempts=attempts, synthetic=metadata["synthetic"]
        )
        score_rows = [row["payload"] for row in profile["inputs"] if row["event_type"] == "score"]
        progress_rows = [row["payload"] for row in profile["inputs"] if row["event_type"] == "grade_progress"]
        declared_rows = [row["payload"] for row in profile["inputs"] if row["event_type"] == "declared_weakness"]
        timing_rows = [
            row["payload"]
            for row in profile["inputs"]
            if row["event_type"] == "timing" and row["payload"].get("kind") != "error_image_import"
        ]
        media_rows = [
            row["payload"]["media"]
            for row in profile["inputs"]
            if row["event_type"] == "timing"
            and row["payload"].get("kind") == "error_image_import"
            and isinstance(row["payload"].get("media"), dict)
        ]
        if not score_rows:
            raise StudentLearningError("diagnosis requires at least one score input")
        if not progress_rows:
            raise StudentLearningError("diagnosis requires at least one grade progress input")
        for row in score_rows:
            _validate_score_payload(row)
        for row in progress_rows:
            _validate_progress_payload(row)
        for row in declared_rows:
            _validate_declared_weakness_payload(row)
        for row in timing_rows:
            _validate_timing_payload(row)
        timing_values: defaultdict[str, list[int]] = defaultdict(list)
        for row in timing_rows:
            timing_values[row["attempt_id"]].append(row["elapsed_seconds"])
        for attempt in attempts:
            values = timing_values.get(attempt["attempt_id"], [])
            if not values:
                raise StudentLearningError(f"attempt timing event missing:{attempt['attempt_id']}")
            if len(set(values)) != 1 or values[-1] != attempt["elapsed_seconds"]:
                raise StudentLearningError(f"attempt/timing elapsed_seconds mismatch:{attempt['attempt_id']}")
        latest: dict[str, dict[str, Any]] = {}
        repeats: defaultdict[str, list[str]] = defaultdict(list)
        for attempt in sorted(attempts, key=lambda row: (row["attempted_at"], row["attempt_id"])):
            unit = attempt["question_evidence"]["canonical_atomic_unit_id"]
            if unit in latest:
                repeats[unit].append(latest[unit]["attempt_id"])
            latest[unit] = attempt
        selected = list(latest.values())
        elapsed_median = float(median(row["elapsed_seconds"] for row in selected))
        speed_anomalies: list[dict[str, Any]] = []
        for row in selected:
            ratio = row["elapsed_seconds"] / elapsed_median if elapsed_median else 1.0
            state = "slow_relative_to_profile" if ratio > 1.35 else "fast_relative_to_profile" if ratio < 0.65 else "within_profile_band"
            if state != "within_profile_band":
                speed_anomalies.append({
                    "attempt_id": row["attempt_id"],
                    "elapsed_seconds": row["elapsed_seconds"],
                    "median_ratio": round(ratio, 4),
                    "state": state,
                })
        buckets: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for attempt in selected:
            evidence = attempt["question_evidence"]
            buckets[("knowledge", evidence["knowledge_tag"])].append(attempt)
            buckets[("ability", evidence["ability_tag"])].append(attempt)
        conclusions: list[dict[str, Any]] = []
        for (dimension, tag), rows in buckets.items():
            losses = [_attempt_loss(row) for row in rows]
            usable = [(row, loss) for row, loss in zip(rows, losses) if loss is not None]
            sources = {row["question_evidence"]["source_group_id"] for row, _ in usable}
            evidence_rows = [row for row, loss in usable if loss > 0]
            counter_rows = [row for row, loss in usable if loss == 0]
            loss_rate = round(sum(loss for _, loss in usable) / len(usable), 4) if usable else None
            if len(usable) >= 3 and len(sources) >= 2 and loss_rate is not None and loss_rate >= 0.34:
                status = "stable_weakness"
                confidence = "high" if counter_rows and len(usable) >= 4 else "medium"
            elif evidence_rows:
                status = "provisional_weakness"
                confidence = "low"
            elif len(usable) >= 3 and len(sources) >= 2:
                status = "stable_strength"
                confidence = "medium"
            else:
                status = "insufficient_evidence"
                confidence = "low"
            first = rows[0]["question_evidence"]
            name_key = "knowledge_name" if dimension == "knowledge" else "ability_name"
            declared_tags = set(declared_rows[-1].get("tag_ids", [])) if declared_rows else set()
            declared_bonus = 0.05 if tag in declared_tags and evidence_rows else 0.0
            base_selection_score = (
                (2.0 if status == "stable_weakness" else 1.0 if status == "provisional_weakness" else 0.0)
                + (loss_rate or 0.0)
                + min(len(usable), 4) * 0.02
            )
            conclusions.append({
                "dimension": dimension,
                "tag_id": tag,
                "display_name": first[name_key],
                "status": status,
                "confidence": confidence,
                "valid_atomic_part_count": len(usable),
                "independent_source_count": len(sources),
                "loss_rate": loss_rate,
                "evidence_attempt_ids": [row["attempt_id"] for row in evidence_rows],
                "counterevidence_attempt_ids": [row["attempt_id"] for row in counter_rows],
                "support_atomic_unit_ids": sorted({
                    row["question_evidence"]["canonical_atomic_unit_id"] for row, _ in usable
                }),
                "field_contributions": {
                    "result_loss_component": round(loss_rate or 0.0, 4),
                    "declared_weakness_low_weight_bonus": declared_bonus,
                    "declared_weakness_can_create_tag_without_attempt_evidence": False,
                    "student_response_present_count": sum(
                        isinstance(row.get("student_response"), str) and bool(row["student_response"].strip())
                        for row, _ in usable
                    ),
                    "sanitized_image_or_ocr_bound_count": sum(
                        isinstance(row.get("error_image_media_id"), str) for row, _ in usable
                    ),
                    "median_elapsed_seconds": round(float(median(row["elapsed_seconds"] for row, _ in usable)), 2)
                    if usable else None,
                },
                "selection_score": round(base_selection_score + declared_bonus, 4),
                "evidence_statement": "machine aggregation over unmistakably synthetic fixture attempts" if metadata["synthetic"] else "machine aggregation over controller-receipted private anonymous attempts",
                "claim_scope": claim_scope,
            })
        weaknesses = [row for row in conclusions if row["status"] in {"stable_weakness", "provisional_weakness"}]
        weaknesses.sort(key=lambda row: (
            row["selection_score"], row["valid_atomic_part_count"], row["tag_id"]
        ), reverse=True)
        main = weaknesses[0] if weaknesses else None
        main_support = set((main or {}).get("evidence_attempt_ids", []))
        secondary = next(
            (
                row for row in weaknesses[1:]
                if set(row.get("evidence_attempt_ids", [])).isdisjoint(main_support)
                and row.get("evidence_attempt_ids")
            ),
            None,
        )
        secondary_support = set((secondary or {}).get("evidence_attempt_ids", []))
        overlap = sorted(main_support & secondary_support)
        if overlap:
            raise StudentLearningError("main/secondary focus evidence overlap is forbidden")

        score_percentages = [round(float(row["earned"]) / float(row["maximum"]) * 100, 4) for row in score_rows]
        score_delta = round(score_percentages[-1] - score_percentages[-2], 4) if len(score_percentages) >= 2 else None
        score_trend = "insufficient_history" if score_delta is None else "improving" if score_delta > 0 else "declining" if score_delta < 0 else "flat"
        media_by_id = {row.get("media_id"): row for row in media_rows if isinstance(row.get("media_id"), str)}
        student_work_rows: list[dict[str, Any]] = []
        for attempt in selected:
            response = attempt.get("student_response")
            media = media_by_id.get(attempt.get("error_image_media_id"), {})
            extraction = media.get("sanitized_content_extraction")
            response_present = isinstance(response, str) and bool(response.strip())
            if not response_present and not isinstance(extraction, dict):
                raise StudentLearningError(f"attempt has neither response nor sanitized image extraction:{attempt['attempt_id']}")
            student_work_rows.append({
                "attempt_id": attempt["attempt_id"],
                "student_response_used": response_present,
                "student_response_sha256": _sha256_bytes(response.encode("utf-8")) if response_present else None,
                "sanitized_image_media_id": attempt.get("error_image_media_id"),
                "sanitized_ocr_or_image_extraction_used": isinstance(extraction, dict),
                "sanitized_content_extraction": extraction,
            })

        performance_delta = self._performance_delta(selected)
        declared_tags = declared_rows[-1].get("tag_ids", []) if declared_rows else []
        supported_tags = {row["tag_id"] for row in conclusions if row["status"] not in {"insufficient_evidence"}}
        input_contributions = {
            "score_and_trend": {
                "record_count": len(score_rows),
                "percentages": score_percentages,
                "latest_percentage": score_percentages[-1],
                "latest_delta_percentage_points": score_delta,
                "trend": score_trend,
                "diagnostic_role": "contextual calibration and week-over-week delta; never creates a weakness tag",
            },
            "latest_grade_progress": {
                "value": dict(progress_rows[-1]),
                "record_count": len(progress_rows),
                "diagnostic_role": "hard stage-match constraint for every plan and content request",
            },
            "declared_weakness": {
                "latest_tag_ids": list(declared_tags),
                "matched_attempt_supported_tag_ids": sorted(set(declared_tags) & supported_tags),
                "unmatched_tag_ids_not_promoted": sorted(set(declared_tags) - supported_tags),
                "selection_bonus_weight": 0.05,
                "can_create_weakness_without_attempt_evidence": False,
            },
            "student_work": {
                "attempt_count": len(student_work_rows),
                "student_response_used_count": sum(row["student_response_used"] for row in student_work_rows),
                "sanitized_ocr_or_image_extraction_used_count": sum(
                    row["sanitized_ocr_or_image_extraction_used"] for row in student_work_rows
                ),
                "attempt_contributions": student_work_rows,
            },
            "elapsed_time": {
                "attempt_and_timing_events_consistent": True,
                "selected_median_seconds": round(elapsed_median, 2),
                "speed_anomalies": speed_anomalies,
                "diagnostic_role": "speed anomaly evidence and week-2 quantity/hint/difficulty policy",
            },
        }
        return {
            "schema_version": "student_diagnosis_v2",
            "domain_version": DOMAIN_VERSION,
            "profile_id": metadata["profile_id"],
            "generated_at": _utc_now(),
            "claim_scope": claim_scope,
            "subject_kind": subject_kind,
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "teaching_effectiveness_unverified": True,
            "adapter_receipt": adapter_receipt,
            "attempt_audit": {
                "input_attempt_count": len(attempts),
                "selected_atomic_part_count": len(selected),
                "selected_attempt_ids": [row["attempt_id"] for row in selected],
                "repeat_history": dict(repeats),
            },
            "input_contributions": input_contributions,
            "performance_delta": performance_delta,
            "conclusions": conclusions,
            "weekly_focus": {
                "main": main,
                "secondary": secondary,
                "selection_rule": "main is highest attempt-supported score; secondary requires a disjoint error-attempt support set so evidence cannot be counted twice across 85 percent of time",
                "evidence_overlap_audit": {
                    "main_evidence_attempt_ids": sorted(main_support),
                    "secondary_evidence_attempt_ids": sorted(secondary_support),
                    "overlap_attempt_ids": overlap,
                    "overlap_count": len(overlap),
                    "independent_secondary_support_required": True,
                },
            },
            "error_type_counts": dict(Counter(error for row in selected for error in row.get("error_types", []))),
            "limitations": [
                "synthetic fixture conclusions only; no real student effect claim" if metadata["synthetic"] else "private machine-only diagnosis; no effect, rank, admission, or teacher-review claim",
                "machine-only aggregation is not teacher or human review",
                "central live diagnosis state was not used and real diagnosis remains blocked" if metadata["synthetic"] else "controller live receipt and machine governance were validated; teaching effectiveness remains unverified",
            ],
        }

    @staticmethod
    def _performance_delta(selected: list[dict[str, Any]]) -> dict[str, Any]:
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            kind = str(row.get("retest_kind", "baseline"))
            group = "day14" if kind.startswith("day14") else "day7" if kind.startswith("day7") else "baseline"
            groups[group].append(row)

        def metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not rows:
                return None
            losses = [loss for loss in (_attempt_loss(row) for row in rows) if loss is not None]
            return {
                "attempt_count": len(rows),
                "mean_loss_rate": round(sum(losses) / len(losses), 4) if losses else None,
                "median_elapsed_seconds": round(float(median(row["elapsed_seconds"] for row in rows)), 2),
                "error_type_counts": dict(sorted(Counter(
                    error for row in rows for error in row.get("error_types", [])
                ).items())),
            }

        group_metrics = {name: metrics(groups.get(name, [])) for name in ("baseline", "day7", "day14")}
        latest_group = "day14" if group_metrics["day14"] else "day7" if group_metrics["day7"] else "baseline"
        baseline = group_metrics["baseline"]
        latest = group_metrics[latest_group]
        loss_delta = None
        elapsed_delta = None
        resolved: list[str] = []
        newly_observed: list[str] = []
        if baseline and latest and latest_group != "baseline":
            if baseline["mean_loss_rate"] is not None and latest["mean_loss_rate"] is not None:
                loss_delta = round(latest["mean_loss_rate"] - baseline["mean_loss_rate"], 4)
            elapsed_delta = round(latest["median_elapsed_seconds"] - baseline["median_elapsed_seconds"], 2)
            baseline_errors = set(baseline["error_type_counts"])
            latest_errors = set(latest["error_type_counts"])
            resolved = sorted(baseline_errors - latest_errors)
            newly_observed = sorted(latest_errors - baseline_errors)
        return {
            "comparison": f"baseline_to_{latest_group}" if latest_group != "baseline" else "baseline_only",
            "latest_group": latest_group,
            "group_metrics": group_metrics,
            "loss_rate_delta": loss_delta,
            "median_elapsed_seconds_delta": elapsed_delta,
            "resolved_error_types": resolved,
            "new_error_types": newly_observed,
            "effect_claim_allowed": False,
        }

    @staticmethod
    def _validate_attempt(attempt: dict[str, Any]) -> None:
        assert_no_direct_identifiers(attempt)
        required = {"attempt_id", "question_id", "attempted_at", "student_response", "result", "error_types", "elapsed_seconds", "question_evidence"}
        missing = sorted(required - set(attempt))
        if missing:
            raise StudentLearningError(f"attempt fields missing: {missing}")
        if attempt["result"].get("status") not in ALLOWED_RESULTS:
            raise StudentLearningError("invalid result status")
        if not set(attempt["error_types"]).issubset(ALLOWED_ERRORS):
            raise StudentLearningError("invalid error type")
        if not isinstance(attempt["elapsed_seconds"], int) or attempt["elapsed_seconds"] < 0:
            raise StudentLearningError("elapsed_seconds must be a non-negative integer")
        _attempt_loss(attempt)

    def build_plan(
        self,
        diagnosis: dict[str, Any],
        grade_progress: dict[str, Any],
        week_number: int,
        previous_diagnosis: dict[str, Any] | None = None,
        history_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(week_number, int) or isinstance(week_number, bool) or week_number <= 0:
            raise StudentLearningError("week_number/cycle_index must be a positive integer")
        if history_state is not None:
            expected_cycle = history_state.get("next_cycle_index")
            if expected_cycle != week_number:
                raise StudentLearningError("learning history next-cycle binding mismatch")
            if week_number > 1 and not re.fullmatch(
                r"[0-9a-f]{64}", str(history_state.get("history_head_sha256", ""))
            ):
                raise StudentLearningError("learning history head hash missing")
            if previous_diagnosis is None and isinstance(history_state.get("latest_focus"), dict):
                previous_diagnosis = {"weekly_focus": history_state["latest_focus"]}
        latest_progress = diagnosis.get("input_contributions", {}).get("latest_grade_progress", {}).get("value")
        if not isinstance(latest_progress, dict):
            raise StudentLearningError("diagnosis is missing latest grade progress contribution")
        _validate_progress_payload(latest_progress)
        if grade_progress != latest_progress:
            raise StudentLearningError("stale grade progress supplied to plan builder")
        focus = diagnosis["weekly_focus"]
        main = focus["main"]
        secondary = focus["secondary"]
        main_name = main["display_name"] if main else "常规保持"
        secondary_name = secondary["display_name"] if secondary else "跨情境迁移"
        feedback = {
            "summary": f"第{week_number}周机器反馈：主任务聚焦“{main_name}”，次任务聚焦“{secondary_name}”。",
            "evidence_boundary": "仅依据合成作答证据；不代表真实学生效果或教师判断。" if diagnosis["claim_scope"] == CLAIM_SCOPE_SYNTHETIC else "仅为私有真实作答的 machine-only candidate；不代表教学效果已验证或教师判断。",
            "observed_change": self._observed_change(diagnosis),
        }
        delta_policy = self._delta_policy(diagnosis, week_number, previous_diagnosis)
        cycle_day_offset = (week_number - 1) * 7
        scheduled_retest_label = "day7" if week_number == 1 else "day14" if week_number == 2 else "week_end"

        def scheduled(
            week_day: int,
            focus_bucket: str,
            minutes: int,
            phase: str,
            focus_name: str,
            task: str,
            *,
            retest_component: bool = False,
        ) -> dict[str, Any]:
            return {
                "schedule_id": f"W{week_number}-D{week_day}-{focus_bucket}",
                "week_day": week_day,
                "cycle_day": cycle_day_offset + week_day,
                "phase": phase,
                "focus_bucket": focus_bucket,
                "minutes": minutes,
                "focus": focus_name,
                "task": task,
                "retest_component": retest_component,
                "retest_label": scheduled_retest_label if retest_component else None,
            }

        daily_allocations = {
            1: {"main_weakness": 42, "secondary_weakness": 12, "maintenance_transfer": 6},
            2: {"main_weakness": 42, "secondary_weakness": 12, "maintenance_transfer": 6},
            3: {"main_weakness": 42, "secondary_weakness": 12, "maintenance_transfer": 6},
            4: {"main_weakness": 36, "secondary_weakness": 15, "maintenance_transfer": 9},
            5: {"main_weakness": 36, "secondary_weakness": 15, "maintenance_transfer": 9},
            6: {"main_weakness": 30, "secondary_weakness": 18, "maintenance_transfer": 12},
            7: {"main_weakness": 24, "secondary_weakness": 21, "maintenance_transfer": 15},
        }
        day_phases = {
            1: "3天修复·基础巩固",
            2: "3天修复·中档综合",
            3: "3天修复·压轴迁移",
            4: "间隔巩固",
            5: "减少提示与跨情境",
            6: "限时应用与错因复盘",
            7: f"{scheduled_retest_label}周末复测",
        }
        focus_names = {
            "main_weakness": main_name,
            "secondary_weakness": secondary_name,
            "maintenance_transfer": "跨情境保持",
        }
        weekly_schedule: list[dict[str, Any]] = []
        for week_day, allocation in daily_allocations.items():
            for bucket in ("main_weakness", "secondary_weakness", "maintenance_transfer"):
                focus_name = focus_names[bucket]
                if week_day == 7:
                    task = (
                        f"使用未参与训练、不同表面情境的{focus_name}复测任务；"
                        f"记录损失率、错误类型和用时并回传cycle {week_number + 1}。"
                    )
                else:
                    task = (
                        f"在{latest_progress['module']}阶段围绕{focus_name}执行{day_phases[week_day]}；"
                        f"{delta_policy['task_directive']}。"
                    )
                weekly_schedule.append(scheduled(
                    week_day,
                    bucket,
                    allocation[bucket],
                    day_phases[week_day],
                    focus_name,
                    task,
                    retest_component=week_day == 7,
                ))
        schedule_audit = audit_weekly_schedule(weekly_schedule, week_number)
        plan = {
            "schema_version": "weekly_learning_plan_v2",
            "domain_version": DOMAIN_VERSION,
            "profile_id": diagnosis["profile_id"],
            "week_number": week_number,
            "cycle_index": week_number,
            "claim_scope": diagnosis["claim_scope"],
            "subject_kind": diagnosis["subject_kind"],
            "machine_status": MACHINE_STATUS,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "teaching_effectiveness_unverified": True,
            "stage_match": {
                "grade_progress": latest_progress,
                "source": "diagnosis.input_contributions.latest_grade_progress.value",
                "rule": "tasks are descriptions constrained to the recorded grade/module progress; formal content provider remains separate",
            },
            "weekly_focus": focus,
            "delta_policy": delta_policy,
            "history_binding": {
                "history_used": history_state is not None,
                "prior_cycle_index": history_state.get("latest_cycle_index") if history_state else None,
                "prior_cycle_record_sha256": history_state.get("history_head_sha256") if history_state else None,
                "next_cycle_binding_verified": history_state is not None and history_state.get("next_cycle_index") == week_number,
                "long_term_effect_claimed": False,
            },
            "time_budget": {
                "total_minutes": WEEKLY_MINUTES,
                "percentages": {"main_weakness": 60, "secondary_weakness": 25, "maintenance_transfer": 15},
                "minutes": dict(schedule_audit["focus_bucket_minutes"]),
                "allocation_source": "recomputed_from_weekly_schedule",
                "allocation_check": schedule_audit["contract_match"],
                "cycle_day_range": [cycle_day_offset + 1, cycle_day_offset + 7],
                "scheduled_retest_label": scheduled_retest_label,
            },
            "learning_feedback": feedback,
            "knowledge_explanation": [
                {
                    "section_id": f"W{week_number}-HANDOUT-MAIN-{(main or {}).get('tag_id', 'MAINTAIN')}",
                    "focus": main_name,
                    "content": f"结构要求：为{latest_progress['module']}阶段的“{main_name}”生成概念边界、正反例与化学用语核对；本字段不是讲义正文。",
                    "content_status": "structure_fixture_only_not_instructional_content" if diagnosis["claim_scope"] == CLAIM_SCOPE_SYNTHETIC else "generation_request_only_not_instructional_content",
                },
                {
                    "section_id": f"W{week_number}-HANDOUT-SECONDARY-{(secondary or {}).get('tag_id', 'TRANSFER')}",
                    "focus": secondary_name,
                    "content": f"结构要求：为“{secondary_name}”生成题设证据到模型与结论的转换链，并落实{delta_policy['hint_policy']}；本字段不是讲义正文。",
                    "content_status": "structure_fixture_only_not_instructional_content" if diagnosis["claim_scope"] == CLAIM_SCOPE_SYNTHETIC else "generation_request_only_not_instructional_content",
                },
            ],
            "weekly_schedule": weekly_schedule,
            "schedule_audit": schedule_audit,
            "three_day_repair": [row for row in weekly_schedule if row["week_day"] <= 3],
            "long_term_plan": [row for row in weekly_schedule if row["week_day"] >= 4],
            "retest_contract": {
                "scheduled_label": scheduled_retest_label,
                "week_day": 7,
                "cycle_day": cycle_day_offset + 7,
                "included_in_weekly_420_minutes": True,
                "retest_minutes": schedule_audit["retest_minutes"],
                "novel_surface_context": True,
                "training_item_overlap_allowed": False,
                "feedback_action": "rediagnose_and_build_week2" if week_number == 1 else "rediagnose_and_recommend_next_cycle",
            },
            "next_week_objectives": self._next_week_objectives(
                current_focus=focus,
                delta_policy=delta_policy,
                cycle_index=week_number,
                history_state=history_state,
            ),
            "spaced_review_due": (history_state or {}).get("spaced_review_due", []),
        }
        return plan

    @staticmethod
    def _next_week_objectives(
        *,
        current_focus: dict[str, Any],
        delta_policy: dict[str, Any],
        cycle_index: int,
        history_state: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        objectives: list[dict[str, Any]] = []
        for role in ("main", "secondary"):
            focus = current_focus.get(role)
            if isinstance(focus, dict):
                objectives.append({
                    "objective_id": f"C{cycle_index + 1}-{role}-{focus['tag_id']}",
                    "target_cycle_index": cycle_index + 1,
                    "focus_role": role,
                    "tag_id": focus["tag_id"],
                    "objective": f"按{delta_policy['policy_id']}复核该标签的新情境损失率、速度和错误类型变化",
                    "source": "current_diagnosis_plus_latest_history" if history_state else "current_diagnosis_only",
                })
        due = [
            row for row in (history_state or {}).get("spaced_review_due", [])
            if row.get("due_cycle_index") == cycle_index + 1
        ]
        for row in due:
            objectives.append({
                "objective_id": f"C{cycle_index + 1}-spaced-{row['tag_id']}",
                "target_cycle_index": cycle_index + 1,
                "focus_role": "spaced_review",
                "tag_id": row["tag_id"],
                "objective": "执行到期的间隔复习并记录独立作答、损失率与用时",
                "source": "immutable_learning_history_due_queue",
            })
        return objectives

    @staticmethod
    def _delta_policy(
        diagnosis: dict[str, Any],
        week_number: int,
        previous_diagnosis: dict[str, Any] | None,
    ) -> dict[str, Any]:
        current_focus = diagnosis.get("weekly_focus", {})
        previous_focus = (previous_diagnosis or {}).get("weekly_focus", {})

        def tag(focus: dict[str, Any], name: str) -> str | None:
            value = focus.get(name)
            return value.get("tag_id") if isinstance(value, dict) else None

        current_pair = [tag(current_focus, "main"), tag(current_focus, "secondary")]
        previous_pair = [tag(previous_focus, "main"), tag(previous_focus, "secondary")]
        performance = diagnosis.get("performance_delta", {})
        loss_delta = performance.get("loss_rate_delta")
        time_delta = performance.get("median_elapsed_seconds_delta")
        resolved = performance.get("resolved_error_types", [])
        new_errors = performance.get("new_error_types", [])
        if week_number == 1:
            decision = {
                "policy_id": "week1_baseline_calibration",
                "quantity_adjustment": "baseline_quantity",
                "hint_policy": "worked_example_then_fade",
                "difficulty_and_transfer_policy": "recorded_stage_matched_three_layer_progression",
                "task_directive": "按基线题量执行，先示范证据链再逐步撤除提示",
                "focus_decision": "establish_baseline_focus",
                "focus_changed_from_previous_week": None,
                "auditable_reason": "week 1 has baseline evidence only; establish stage-matched volume, hint and speed baselines",
            }
        elif (
            (week_number == 2 and performance.get("latest_group") != "day7")
            or (week_number >= 3 and performance.get("latest_group") == "baseline")
            or loss_delta is None
            or time_delta is None
        ):
            decision = {
                "policy_id": "week2_fail_closed_missing_day7_delta",
                "quantity_adjustment": "do_not_increase",
                "hint_policy": "retain_support_until_day7_delta_is_complete",
                "difficulty_and_transfer_policy": "no_promotion_without_day7_loss_and_speed_delta",
                "task_directive": "缺少完整day7损失率/用时变化时不加量、不升难度",
                "focus_decision": "retain_current_attempt_supported_focus",
                "focus_changed_from_previous_week": current_pair != previous_pair if previous_diagnosis else None,
                "auditable_reason": "cycle promotion is blocked because the latest required retest loss-and-speed comparison is unavailable",
            }
        elif loss_delta <= -0.15 and time_delta <= 0:
            decision = {
                "policy_id": "week2_improved_loss_and_speed_transfer_up",
                "quantity_adjustment": "add_one_transfer_unit_per_focus_without_changing_minutes",
                "hint_policy": "fade_to_evidence_prompts_only",
                "difficulty_and_transfer_policy": "increase_surface_novelty_and_cross_module_transfer_one_step",
                "task_directive": "损失率与速度均改善，在固定分钟内增加迁移密度并撤除步骤提示",
                "focus_decision": "retain_or_rerank_by_current_independent_evidence",
                "focus_changed_from_previous_week": current_pair != previous_pair if previous_diagnosis else None,
                "auditable_reason": f"day7 loss delta={loss_delta}, median elapsed delta={time_delta}; resolved errors={resolved}",
            }
        elif loss_delta < 0 and time_delta > 0:
            decision = {
                "policy_id": "week2_accuracy_up_speed_down_efficiency",
                "quantity_adjustment": "reduce_one_repetitive_unit_and_add_timed_comparison",
                "hint_policy": "retain_model_selection_prompt_remove_answer_path",
                "difficulty_and_transfer_policy": "hold_difficulty_prioritize_efficiency",
                "task_directive": "正确性改善但速度下降，保持难度并以限时对照替换重复训练",
                "focus_decision": "retain_current_focus_for_efficiency_repair",
                "focus_changed_from_previous_week": current_pair != previous_pair if previous_diagnosis else None,
                "auditable_reason": f"day7 loss delta={loss_delta}, median elapsed delta={time_delta}; new errors={new_errors}",
            }
        else:
            decision = {
                "policy_id": "week2_loss_not_improved_rebuild",
                "quantity_adjustment": "reduce_transfer_density_keep_total_minutes",
                "hint_policy": "restore_condition_model_evidence_scaffold",
                "difficulty_and_transfer_policy": "step_back_one_layer_before_novel_transfer",
                "task_directive": "损失率未达到改善阈值，恢复条件—模型—证据脚手架后再迁移",
                "focus_decision": "retain_or_rerank_by_current_independent_evidence",
                "focus_changed_from_previous_week": current_pair != previous_pair if previous_diagnosis else None,
                "auditable_reason": f"day7 loss delta={loss_delta}, median elapsed delta={time_delta}; new errors={new_errors}",
            }
        return {
            **decision,
            "source_comparison": performance.get("comparison"),
            "loss_rate_delta": loss_delta,
            "median_elapsed_seconds_delta": time_delta,
            "resolved_error_types": resolved,
            "new_error_types": new_errors,
            "previous_focus_tag_ids": previous_pair if previous_diagnosis else None,
            "current_focus_tag_ids": current_pair,
            "teaching_effectiveness_claimed": False,
        }

    @staticmethod
    def _observed_change(diagnosis: dict[str, Any]) -> str:
        selected = diagnosis["attempt_audit"]["selected_attempt_ids"]
        suffix = "变化仅作合成回归观察。" if diagnosis["claim_scope"] == CLAIM_SCOPE_SYNTHETIC else "变化为机器候选观察，教学有效性未验证。"
        return f"当前截面含{len(selected)}个去重最小作答单元；{suffix}"


class ContentProvider(Protocol):
    def render(self, *, diagnosis: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]: ...


class FixtureContentProvider:
    """Interface-driven synthetic content; never emits formal chemistry questions."""

    def render(self, *, diagnosis: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        main = (plan["weekly_focus"]["main"] or {}).get("tag_id", "MAINTAIN")
        secondary = (plan["weekly_focus"]["secondary"] or {}).get("tag_id", "TRANSFER")
        module = plan["stage_match"]["grade_progress"]["module"]
        delta_policy = plan["delta_policy"]["policy_id"]

        def task(task_id: str, focus: str, layer: str, sequence: int, family: str) -> dict[str, Any]:
            return {
                "task_id": task_id, "focus_tag": focus, "layer": layer,
                "description": (
                    f"STRUCTURE FIXTURE REQUEST {family}-{sequence}: module={module}; "
                    f"focus={focus}; layer={layer}; delta_policy={delta_policy}; no chemistry stem is present."
                ),
                "content_status": SYNTHETIC_CONTENT_STATUS,
                "formal_question": False,
                "claim_scope": CLAIM_SCOPE_SYNTHETIC,
            }

        def answer(task_id: str) -> dict[str, Any]:
            return {
                "task_id": task_id,
                "answer_status": SYNTHETIC_CONTENT_STATUS,
                "generation_interface": {
                    "module": module,
                    "delta_policy_id": delta_policy,
                    "required_fields": ["suggested_answer", "detailed_explanation", "suggested_scoring_points"],
                },
                "suggested_answer_present": False,
                "detailed_explanation_present": False,
                "suggested_scoring_points_present": False,
                "formal_answer": False,
            }

        return {
            "content_completeness_status": SYNTHETIC_CONTENT_STATUS,
            "complete_week_pack": False,
            "formal_chemistry_question_count": 0,
            "training_tasks": [task(f"W{plan['week_number']}-TRAIN-{i}", main if i < 4 else secondary, ("基础巩固", "中档综合", "压轴迁移")[i % 3], i, "TRAIN") for i in range(1, 7)],
            "training_answers": [answer(f"W{plan['week_number']}-TRAIN-{i}") for i in range(1, 7)],
            "retest_day7": [task(f"W{plan['week_number']}-D7-{i}", main if i % 2 else secondary, "复测", i, "D7") for i in range(1, 5)],
            "retest_day7_answers": [answer(f"W{plan['week_number']}-D7-{i}") for i in range(1, 5)],
            "retest_day14": [task(f"W{plan['week_number']}-D14-{i}", main if i % 2 else secondary, "密封复测", i, "D14") for i in range(1, 5)],
            "retest_day14_answers": [answer(f"W{plan['week_number']}-D14-{i}") for i in range(1, 5)],
            "provider_status": SYNTHETIC_CONTENT_STATUS,
            "formal_docx_pdf_rendering": "not_produced_structure_fixture_only",
            "publication_or_delivery_eligible": False,
        }


class FormalContentContractError(StudentLearningError):
    """Generation export is absent or cannot prove a complete private pack."""


def _normalized_rendered_text(value: str) -> str:
    # DOCX and PDF renderers insert different line-break/word-spacing text
    # boundaries.  Semantic parity is therefore whitespace-insensitive while
    # punctuation, symbols, formulas and all non-whitespace glyphs stay exact.
    return re.sub(r"\s+", "", value).strip()


def _extract_rendered_document_text(name: str, document_bytes: bytes) -> str:
    """Extract locally verifiable text from a formal DOCX/PDF or fail closed."""
    if name.endswith(".docx"):
        try:
            from xml.etree import ElementTree

            with zipfile.ZipFile(BytesIO(document_bytes), "r") as docx:
                xml = docx.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            return _normalized_rendered_text(" ".join(root.itertext()))
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise FormalContentContractError(f"rendered DOCX text extraction failed:{name}") from exc
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise FormalContentContractError("rendered PDF text extraction backend unavailable") from exc
    try:
        reader = PdfReader(BytesIO(document_bytes))
        if reader.is_encrypted:
            raise FormalContentContractError(f"rendered PDF must not be encrypted:{name}")
        return _normalized_rendered_text(" ".join((page.extract_text() or "") for page in reader.pages))
    except FormalContentContractError:
        raise
    except Exception as exc:
        raise FormalContentContractError(f"rendered PDF text extraction failed:{name}") from exc


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_provider_contract(
    content: dict[str, Any], *, allow_governed_fixture: bool
) -> tuple[bool, str]:
    provider = content.get("provider_contract")
    if not isinstance(provider, dict):
        raise FormalContentContractError("generation provider attestation missing")
    if provider.get("contract_version") != GENERATION_CONTENT_PROVIDER_CONTRACT:
        raise FormalContentContractError("generation provider attestation contract mismatch")
    provider_id = provider.get("provider_id")
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise FormalContentContractError("generation provider identity missing")
    mode = provider.get("provider_mode")
    fixture_only = provider.get("fixture_only") is True
    if mode == "production":
        if fixture_only or provider.get("production_ready") is not True:
            raise FormalContentContractError("production generation provider is not ready")
        expected_claim = "machine_only_real_student_candidate"
        expected_status = REAL_FORMAL_CONTENT_STATUS
    elif mode == "governed_contract_fixture":
        if not allow_governed_fixture:
            raise FormalContentContractError(
                "governed content fixture cannot unlock a real weekly pack"
            )
        if not fixture_only or provider.get("production_ready") is not False:
            raise FormalContentContractError("governed fixture boundary invalid")
        expected_claim = GOVERNED_CONTRACT_FIXTURE_STATUS
        expected_status = GOVERNED_CONTRACT_FIXTURE_STATUS
    else:
        raise FormalContentContractError("generation provider mode unsupported")
    if content.get("claim_scope") != expected_claim:
        raise FormalContentContractError("generation export claim scope/provider mode mismatch")
    if content.get("content_completeness_status") != expected_status:
        raise FormalContentContractError("generation export completeness/provider mode mismatch")
    generated_from = provider.get("generated_from")
    if not isinstance(generated_from, dict) or (
        generated_from.get("diagnosis_sha256") != content.get("diagnosis_sha256")
        or generated_from.get("plan_sha256") != content.get("plan_sha256")
        or generated_from.get("anonymous_profile_only") is not True
    ):
        raise FormalContentContractError("provider diagnosis/plan generation binding invalid")
    receipt = provider.get("generator_receipt")
    if not isinstance(receipt, dict) or (
        receipt.get("provenance_status") != "self_reported"
        or not _is_sha256(receipt.get("receipt_sha256"))
    ):
        raise FormalContentContractError("generator receipt provenance boundary invalid")
    receipt_path = Path(str(receipt.get("receipt_path", ""))).resolve()
    if not receipt_path.is_file() or _sha256_bytes(receipt_path.read_bytes()) != receipt["receipt_sha256"]:
        raise FormalContentContractError("generator receipt file/hash unavailable")
    reconciliation = provider.get("root_external_reconciliation")
    if not isinstance(reconciliation, dict) or (
        reconciliation.get("record_type")
        != "root_external_reconciliation_attestation_v1"
        or reconciliation.get("status") != "verified"
        or reconciliation.get("root_scoped") is not True
        or not _is_sha256(reconciliation.get("attestation_sha256"))
        or reconciliation.get("reconciles_generator_receipt_sha256")
        != receipt["receipt_sha256"]
    ):
        raise FormalContentContractError(
            "root external reconciliation attestation required"
        )
    attestation_path = Path(str(reconciliation.get("attestation_path", ""))).resolve()
    if not attestation_path.is_file() or _sha256_bytes(attestation_path.read_bytes()) != reconciliation["attestation_sha256"]:
        raise FormalContentContractError("root external reconciliation file/hash unavailable")
    style = provider.get("document_style")
    if not isinstance(style, dict) or any(
        style.get(key) != value for key, value in SHANGHAI_EXAM_PRINT_STYLE.items()
    ):
        raise FormalContentContractError("shanghai_exam_print document style contract mismatch")
    inventory = provider.get("required_document_names")
    if not isinstance(inventory, list) or set(inventory) != REQUIRED_FORMAL_DOCUMENTS:
        raise FormalContentContractError("provider required document inventory mismatch")
    return fixture_only, reconciliation["attestation_sha256"]


def _validate_governed_atomic_record(
    item: dict[str, Any], *, reconciliation_sha256: str, group: str
) -> None:
    governed = item.get("governed_atomic_content")
    if not isinstance(governed, dict):
        raise FormalContentContractError(f"governed atomic projection missing:{group}")
    if governed.get("atomic_part_id") != item.get("atomic_part_id"):
        raise FormalContentContractError(f"governed atomic identity mismatch:{group}")
    for key in ("full_paper_question_ref", "full_paper_answer_ref"):
        if not isinstance(governed.get(key), str) or not governed[key].strip():
            raise FormalContentContractError(f"full-paper Q/A reference missing:{group}:{key}")
    subject_pair = governed.get("parent_subject_pair")
    if not isinstance(subject_pair, list) or len(subject_pair) != 2 or any(
        not isinstance(value, str) or not value.strip() for value in subject_pair
    ):
        raise FormalContentContractError(f"atomic parent subject pair invalid:{group}")
    pointers = governed.get("component_json_pointers")
    if not isinstance(pointers, list) or len(pointers) != 8 or len(set(pointers)) != 8 or any(
        not isinstance(value, str) or not value.startswith("/") for value in pointers
    ):
        raise FormalContentContractError(
            f"atomic eight-component JSON-pointer projection invalid:{group}"
        )
    projection = governed.get("canonical_projection")
    if not isinstance(projection, dict) or set(projection) != set(pointers):
        raise FormalContentContractError(f"atomic canonical projection set mismatch:{group}")
    if governed.get("canonical_projection_sha256") != _sha256_json(projection):
        raise FormalContentContractError(f"atomic canonical projection hash mismatch:{group}")
    if governed.get("root_external_reconciliation_sha256") != reconciliation_sha256:
        raise FormalContentContractError(
            f"atomic external reconciliation binding mismatch:{group}"
        )


def _audit_docx_print_contract(name: str, document_bytes: bytes) -> None:
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(BytesIO(document_bytes), "r") as docx:
            document_xml = docx.read("word/document.xml")
            styles_xml = docx.read("word/styles.xml")
            numbering_xml = docx.read("word/numbering.xml")
            footer_xml = b" ".join(
                docx.read(member)
                for member in docx.namelist()
                if re.fullmatch(r"word/footer\d+\.xml", member)
            )
    except (KeyError, zipfile.BadZipFile) as exc:
        raise FormalContentContractError(f"DOCX print contract parts missing:{name}") from exc
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise FormalContentContractError(f"DOCX document XML invalid:{name}") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    page = root.find(".//w:sectPr/w:pgSz", ns)
    if page is None:
        raise FormalContentContractError(f"DOCX A4 page setup missing:{name}")
    width = int(page.attrib.get(f"{{{ns['w']}}}w", "0"))
    height = int(page.attrib.get(f"{{{ns['w']}}}h", "0"))
    if abs(width - 11906) > 4 or abs(height - 16838) > 4:
        raise FormalContentContractError(f"DOCX page is not A4 portrait:{name}")
    if "宋体".encode() not in styles_xml or "黑体".encode() not in styles_xml:
        raise FormalContentContractError(f"DOCX Chinese font styles missing:{name}")
    tables = root.findall(".//w:tbl", ns)
    if not tables:
        raise FormalContentContractError(f"DOCX fixed-DXA score table missing:{name}")
    for table in tables:
        layout = table.find("./w:tblPr/w:tblLayout", ns)
        table_width = table.find("./w:tblPr/w:tblW", ns)
        grid = table.findall("./w:tblGrid/w:gridCol", ns)
        cell_widths = table.findall(".//w:tcPr/w:tcW", ns)
        if (
            layout is None
            or layout.attrib.get(f"{{{ns['w']}}}type") != "fixed"
            or table_width is None
            or table_width.attrib.get(f"{{{ns['w']}}}type") != "dxa"
            or not grid
            or not cell_widths
            or any(cell.attrib.get(f"{{{ns['w']}}}type") != "dxa" for cell in cell_widths)
        ):
            raise FormalContentContractError(f"DOCX fixed DXA table geometry invalid:{name}")
    if b"<w:numPr" not in document_xml or b"<w:abstractNum" not in numbering_xml:
        raise FormalContentContractError(f"DOCX real numbering definition missing:{name}")
    if b"PAGE" not in footer_xml:
        raise FormalContentContractError(f"DOCX page number field missing:{name}")


def validate_formal_content_export(
    content: dict[str, Any],
    *,
    diagnosis: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    allow_governed_fixture: bool = False,
) -> dict[str, Any]:
    """Validate, hash and return a generation-domain export; all gaps fail closed."""
    if not isinstance(content, dict):
        raise FormalContentContractError("generation content export must be an object")
    if content.get("contract_version") != GENERATION_CONTENT_PROVIDER_CONTRACT:
        raise FormalContentContractError("generation content provider contract mismatch")
    fixture_only, reconciliation_sha256 = _validate_provider_contract(
        content, allow_governed_fixture=allow_governed_fixture
    )
    expected_content_status = (
        GOVERNED_CONTRACT_FIXTURE_STATUS if fixture_only else REAL_FORMAL_CONTENT_STATUS
    )
    if content.get("human_reviewed") is not False or content.get("official") is not False:
        raise FormalContentContractError("generation export review/official boundary invalid")
    privacy_scan = json.loads(json.dumps(content))
    for document in privacy_scan.get("rendered_documents", []):
        if isinstance(document, dict) and "name" in document:
            document["document_filename"] = document.pop("name")
    assert_no_direct_identifiers(privacy_scan)
    for path, key, value in _walk(content):
        if (key == "synthetic" and value is True) or (
            isinstance(value, str)
            and value in {CLAIM_SCOPE_SYNTHETIC, SUBJECT_KIND_SYNTHETIC, "real_path_contract_fixture_not_real_student"}
        ):
            raise FormalContentContractError(f"synthetic/contract fixture marker rejected from real content:{path}")
    try:
        str(uuid.UUID(str(content.get("profile_id"))))
    except (ValueError, AttributeError) as exc:
        raise FormalContentContractError("generation export profile_id invalid") from exc
    if not isinstance(content.get("cycle_index"), int) or isinstance(content.get("cycle_index"), bool) or content["cycle_index"] <= 0:
        raise FormalContentContractError("generation export cycle_index invalid")
    for name in ("diagnosis_sha256", "plan_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(content.get(name, ""))):
            raise FormalContentContractError(f"generation export binding hash invalid:{name}")
    if diagnosis is not None and (
        content["profile_id"] != diagnosis.get("profile_id")
        or content["diagnosis_sha256"] != _sha256_json(diagnosis)
    ):
        raise FormalContentContractError("generation export diagnosis/profile binding mismatch")
    if plan is not None and (
        content["profile_id"] != plan.get("profile_id")
        or content["cycle_index"] != plan.get("cycle_index")
        or content["plan_sha256"] != _sha256_json(plan)
    ):
        raise FormalContentContractError("generation export plan/cycle binding mismatch")
    groups = ("training_tasks", "retest_day7", "retest_day14")
    answer_groups = ("training_answers", "retest_day7_answers", "retest_day14_answers")
    all_ids: set[str] = set()
    question_stem_fingerprints: set[str] = set()
    formal_questions: list[dict[str, Any]] = []
    for group in groups:
        items = content.get(group)
        if not isinstance(items, list) or not items:
            raise FormalContentContractError(f"generation export missing formal question group:{group}")
        for item in items:
            if not isinstance(item, dict):
                raise FormalContentContractError(f"formal question is not an object:{group}")
            required_strings = (
                "task_id", "question_stem", "source_ref", "source_sha256",
                "atomic_part_id", "canonical_parent_chain_sha256", "governance_chain_path",
                "governance_chain_sha256",
            )
            if any(not isinstance(item.get(name), str) or not item[name].strip() for name in required_strings):
                raise FormalContentContractError(f"formal question fields incomplete:{group}")
            for hash_name in ("source_sha256", "canonical_parent_chain_sha256", "governance_chain_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", item[hash_name]):
                    raise FormalContentContractError(f"formal question hash invalid:{group}:{hash_name}")
            governance_path = Path(item["governance_chain_path"]).resolve()
            if not governance_path.is_file() or _sha256_bytes(governance_path.read_bytes()) != item["governance_chain_sha256"]:
                raise FormalContentContractError(f"formal question governance file/hash unavailable:{group}")
            parent = item.get("canonical_parent_chain")
            if not isinstance(parent, dict) or parent.get("atomic_part_id") != item["atomic_part_id"]:
                raise FormalContentContractError(f"formal question parent chain missing:{group}")
            if _sha256_json(parent) != item["canonical_parent_chain_sha256"]:
                raise FormalContentContractError(f"formal question parent chain hash mismatch:{group}")
            if item.get("machine_governance_state") != "automated_verified_candidate":
                raise FormalContentContractError(f"formal question governance state insufficient:{group}")
            if item.get("content_status") != expected_content_status:
                raise FormalContentContractError(f"formal question content status invalid:{group}")
            _validate_governed_atomic_record(
                item,
                reconciliation_sha256=reconciliation_sha256,
                group=group,
            )
            score = item.get("score")
            if not isinstance(score, int) or isinstance(score, bool) or score <= 0:
                raise FormalContentContractError(f"formal question score invalid:{group}")
            if re.search(r"placeholder|pending|fixture|interface[_ -]?only", item["question_stem"], re.IGNORECASE):
                raise FormalContentContractError(f"formal question contains placeholder language:{group}")
            stem_fingerprint = re.sub(r"\s+", " ", item["question_stem"].strip()).casefold()
            if len(stem_fingerprint) < 20:
                raise FormalContentContractError(f"formal question stem is not substantive:{group}")
            if stem_fingerprint in question_stem_fingerprints:
                raise FormalContentContractError("formal question stem duplicated across training/retest")
            question_stem_fingerprints.add(stem_fingerprint)
            formal_questions.append(item)
            if item["task_id"] in all_ids:
                raise FormalContentContractError("training/retest task ID overlap")
            all_ids.add(item["task_id"])
    answers_by_group: dict[str, dict[str, dict[str, Any]]] = {}
    explanation_fingerprints: set[str] = set()
    for group in answer_groups:
        items = content.get(group)
        if not isinstance(items, list) or not items:
            raise FormalContentContractError(f"generation export missing answer group:{group}")
        answers_by_group[group] = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("task_id"), str):
                raise FormalContentContractError(f"formal answer is not bound:{group}")
            if not isinstance(item.get("suggested_answer"), str) or not item["suggested_answer"].strip():
                raise FormalContentContractError(f"formal suggested answer missing:{group}")
            if not isinstance(item.get("detailed_explanation"), str) or not item["detailed_explanation"].strip():
                raise FormalContentContractError(f"formal detailed explanation missing:{group}")
            points = item.get("suggested_scoring_points")
            if not isinstance(points, list) or not points or any(not isinstance(point, str) or not point.strip() for point in points):
                raise FormalContentContractError(f"suggested scoring points missing:{group}")
            if item.get("official_scoring_points") is not False:
                raise FormalContentContractError(f"official scoring point spoof:{group}")
            if item.get("content_status") != expected_content_status:
                raise FormalContentContractError(f"formal answer content status invalid:{group}")
            if any(
                re.search(r"placeholder|pending|fixture|interface[_ -]?only", text, re.IGNORECASE)
                for text in [item["suggested_answer"], item["detailed_explanation"], *points]
            ):
                raise FormalContentContractError(f"formal answer contains placeholder language:{group}")
            explanation_fingerprint = re.sub(
                r"\s+", " ", item["detailed_explanation"].strip()
            ).casefold()
            if len(explanation_fingerprint) < 20:
                raise FormalContentContractError(f"formal detailed explanation is not substantive:{group}")
            if explanation_fingerprint in explanation_fingerprints:
                raise FormalContentContractError("formal detailed explanation duplicated across items")
            explanation_fingerprints.add(explanation_fingerprint)
            answers_by_group[group][item["task_id"]] = item
    pairings = (
        ("training_tasks", "training_answers"),
        ("retest_day7", "retest_day7_answers"),
        ("retest_day14", "retest_day14_answers"),
    )
    for task_group, answer_group in pairings:
        task_ids = {item["task_id"] for item in content[task_group]}
        if task_ids != set(answers_by_group[answer_group]):
            raise FormalContentContractError(f"question/answer binding mismatch:{task_group}")
    sources = content.get("sources")
    if not isinstance(sources, list) or not sources:
        raise FormalContentContractError("formal source inventory missing")
    source_hashes: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise FormalContentContractError("formal source record invalid")
        source_ref = source.get("source_ref")
        source_hash = source.get("sha256")
        if not isinstance(source_ref, str) or not source_ref or not re.fullmatch(r"[0-9a-f]{64}", str(source_hash or "")):
            raise FormalContentContractError("formal source record incomplete")
        source_path = Path(str(source.get("path", ""))).resolve()
        if not source_path.is_file() or _sha256_bytes(source_path.read_bytes()) != source_hash:
            raise FormalContentContractError("formal source file/hash unavailable")
        if source_ref in source_hashes and source_hashes[source_ref] != source_hash:
            raise FormalContentContractError("formal source ref has conflicting hashes")
        source_hashes[source_ref] = source_hash
    for item in formal_questions:
        if source_hashes.get(item["source_ref"]) != item["source_sha256"]:
            raise FormalContentContractError(f"formal question source binding missing:{item['task_id']}")
    handout = content.get("knowledge_handout")
    if not isinstance(handout, dict) or not isinstance(handout.get("sections"), list) or not handout["sections"]:
        raise FormalContentContractError("formal knowledge handout sections missing")
    for section in handout["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("title"), str) or not isinstance(section.get("explanation"), str):
            raise FormalContentContractError("formal knowledge handout section incomplete")
        if not section["title"].strip() or not section["explanation"].strip():
            raise FormalContentContractError("formal knowledge handout section empty")
        if re.search(
            r"placeholder|pending|fixture|interface[_ -]?only",
            section["title"] + " " + section["explanation"],
            re.IGNORECASE,
        ):
            raise FormalContentContractError("formal knowledge handout contains placeholder language")
    documents = content.get("rendered_documents")
    if not isinstance(documents, list):
        raise FormalContentContractError("rendered DOCX/PDF inventory missing")
    document_names: set[str] = set()
    document_records: dict[str, dict[str, Any]] = {}
    extracted_by_name: dict[str, str] = {}
    expected_document_payload_hashes = {
        "training_student": _sha256_json(content["training_tasks"]),
        "training_answers": _sha256_json({
            "questions": content["training_tasks"], "answers": content["training_answers"]
        }),
        "retest_student": _sha256_json({
            "day7": content["retest_day7"], "day14": content["retest_day14"]
        }),
        "retest_answers": _sha256_json({
            "day7_questions": content["retest_day7"],
            "day7_answers": content["retest_day7_answers"],
            "day14_questions": content["retest_day14"],
            "day14_answers": content["retest_day14_answers"],
        }),
    }
    expected_document_fragments = {
        "training_student": [
            value
            for item in content["training_tasks"]
            for value in (
                item["task_id"], item["question_stem"], f"分值：{item['score']}分"
            )
        ],
        "training_answers": [
            value
            for question, answer in zip(content["training_tasks"], content["training_answers"])
            for value in (
                question["task_id"], question["question_stem"], answer["suggested_answer"],
                f"分值：{question['score']}分", answer["detailed_explanation"],
                *answer["suggested_scoring_points"],
            )
        ],
        "retest_student": [
            value
            for item in [*content["retest_day7"], *content["retest_day14"]]
            for value in (
                item["task_id"], item["question_stem"], f"分值：{item['score']}分"
            )
        ],
        "retest_answers": [
            value
            for questions, answers in (
                (content["retest_day7"], content["retest_day7_answers"]),
                (content["retest_day14"], content["retest_day14_answers"]),
            )
            for question, answer in zip(questions, answers)
            for value in (
                question["task_id"], question["question_stem"], answer["suggested_answer"],
                f"分值：{question['score']}分", answer["detailed_explanation"],
                *answer["suggested_scoring_points"],
            )
        ],
    }
    for document in documents:
        if not isinstance(document, dict):
            raise FormalContentContractError("rendered document record invalid")
        name = document.get("name")
        if name not in REQUIRED_FORMAL_DOCUMENTS:
            raise FormalContentContractError(f"unexpected rendered document:{name}")
        semantic_role = name.rsplit(".", 1)[0]
        if (
            document.get("semantic_role") != semantic_role
            or document.get("content_payload_sha256") != expected_document_payload_hashes.get(semantic_role)
        ):
            raise FormalContentContractError(f"rendered document semantic payload binding invalid:{name}")
        if document.get("media_type") not in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/pdf",
        }:
            raise FormalContentContractError(f"rendered document media type invalid:{name}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(document.get("sha256", ""))):
            raise FormalContentContractError(f"rendered document hash invalid:{name}")
        path = Path(str(document.get("path", ""))).resolve()
        if not path.is_file():
            raise FormalContentContractError(f"rendered document file/hash unavailable:{name}")
        document_bytes = path.read_bytes()
        if _sha256_bytes(document_bytes) != document["sha256"]:
            raise FormalContentContractError(f"rendered document file/hash unavailable:{name}")
        if name.endswith(".pdf"):
            if not document_bytes.startswith(b"%PDF-") or b"%%EOF" not in document_bytes[-1024:]:
                raise FormalContentContractError(f"rendered PDF signature invalid:{name}")
            try:
                from pypdf import PdfReader

                pdf_reader = PdfReader(BytesIO(document_bytes))
                if pdf_reader.is_encrypted or not pdf_reader.pages:
                    raise FormalContentContractError(f"rendered PDF pages invalid:{name}")
                for page in pdf_reader.pages:
                    width_pt = float(page.mediabox.width)
                    height_pt = float(page.mediabox.height)
                    if abs(width_pt - 595.28) > 2 or abs(height_pt - 841.89) > 2:
                        raise FormalContentContractError(f"rendered PDF is not A4 portrait:{name}")
                actual_page_count = len(pdf_reader.pages)
            except FormalContentContractError:
                raise
            except Exception as exc:
                raise FormalContentContractError(f"rendered PDF page audit failed:{name}") from exc
        else:
            try:
                with zipfile.ZipFile(BytesIO(document_bytes), "r") as docx:
                    if not {"[Content_Types].xml", "word/document.xml"}.issubset(docx.namelist()):
                        raise FormalContentContractError(f"rendered DOCX structure invalid:{name}")
                    bad = docx.testzip()
                    if bad is not None:
                        raise FormalContentContractError(f"rendered DOCX CRC invalid:{name}:{bad}")
            except zipfile.BadZipFile as exc:
                raise FormalContentContractError(f"rendered DOCX ZIP invalid:{name}") from exc
            _audit_docx_print_contract(name, document_bytes)
            actual_page_count = None
        extracted_text = _extract_rendered_document_text(name, document_bytes)
        if len(extracted_text) < 40:
            raise FormalContentContractError(f"rendered document text is not substantive:{name}")
        if document.get("extracted_text_sha256") != _sha256_bytes(extracted_text.encode("utf-8")):
            raise FormalContentContractError(f"rendered document extracted-text hash mismatch:{name}")
        for fragment in expected_document_fragments[semantic_role]:
            if _normalized_rendered_text(fragment) not in extracted_text:
                raise FormalContentContractError(f"rendered document semantic text missing:{name}")
        if "非官方" not in extracted_text:
            raise FormalContentContractError(f"rendered document nonofficial label missing:{name}")
        if semantic_role.endswith("answers") and "建议采分点" not in extracted_text:
            raise FormalContentContractError(
                f"rendered answer document suggested-scoring label missing:{name}"
            )
        render_qa = document.get("render_qa")
        expected_renderer = (
            "documents_skill_render_docx.py"
            if name.endswith(".docx")
            else "bundled_pdftoppm"
        )
        if not isinstance(render_qa, dict) or (
            render_qa.get("status") != "pass"
            or render_qa.get("renderer") != expected_renderer
            or render_qa.get("all_pages_visual_inspected") is not True
            or not isinstance(render_qa.get("page_count"), int)
            or isinstance(render_qa.get("page_count"), bool)
            or render_qa["page_count"] <= 0
        ):
            raise FormalContentContractError(f"rendered document page QA incomplete:{name}")
        if actual_page_count is not None and render_qa["page_count"] != actual_page_count:
            raise FormalContentContractError(f"rendered PDF page-count QA mismatch:{name}")
        page_pngs = render_qa.get("page_pngs")
        if not isinstance(page_pngs, list) or len(page_pngs) != render_qa["page_count"]:
            raise FormalContentContractError(f"rendered document PNG inventory incomplete:{name}")
        for page_index, page_record in enumerate(page_pngs, start=1):
            if not isinstance(page_record, dict) or page_record.get("page_number") != page_index:
                raise FormalContentContractError(f"rendered page numbering invalid:{name}")
            png_path = Path(str(page_record.get("path", ""))).resolve()
            if not png_path.is_file() or not _is_sha256(page_record.get("sha256")):
                raise FormalContentContractError(f"rendered page PNG/hash missing:{name}:{page_index}")
            png_bytes = png_path.read_bytes()
            if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n") or _sha256_bytes(png_bytes) != page_record["sha256"]:
                raise FormalContentContractError(f"rendered page PNG/hash mismatch:{name}:{page_index}")
        sidecar_path = Path(str(document.get("sidecar_path", ""))).resolve()
        if sidecar_path.parent != path.parent or not sidecar_path.is_file():
            raise FormalContentContractError(f"rendered document sidecar missing:{name}")
        sidecar_bytes = sidecar_path.read_bytes()
        if not _is_sha256(document.get("sidecar_sha256")) or _sha256_bytes(sidecar_bytes) != document["sidecar_sha256"]:
            raise FormalContentContractError(f"rendered document sidecar hash mismatch:{name}")
        try:
            sidecar = json.loads(sidecar_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FormalContentContractError(f"rendered document sidecar invalid:{name}") from exc
        if not isinstance(sidecar, dict) or (
            sidecar.get("schema_version") != "student_formal_document_sidecar_v1"
            or sidecar.get("artifact_name") != name
            or sidecar.get("artifact_sha256") != document["sha256"]
            or sidecar.get("semantic_role") != semantic_role
            or sidecar.get("content_payload_sha256")
            != document["content_payload_sha256"]
            or sidecar.get("render_qa_sha256") != _sha256_json(render_qa)
        ):
            raise FormalContentContractError(f"rendered document sidecar binding mismatch:{name}")
        assert_no_direct_identifiers(extracted_text)
        document_names.add(name)
        document_records[name] = document
        extracted_by_name[name] = extracted_text
    if document_names != REQUIRED_FORMAL_DOCUMENTS:
        raise FormalContentContractError(f"rendered document set incomplete:{sorted(REQUIRED_FORMAL_DOCUMENTS - document_names)}")
    parity = content.get("document_parity")
    if not isinstance(parity, list) or len(parity) != len(FORMAL_DOCUMENT_ROLES):
        raise FormalContentContractError("DOCX/PDF parity inventory incomplete")
    seen_roles: set[str] = set()
    for pair in parity:
        if not isinstance(pair, dict):
            raise FormalContentContractError("DOCX/PDF parity record invalid")
        role = pair.get("semantic_role")
        if role not in FORMAL_DOCUMENT_ROLES or role in seen_roles:
            raise FormalContentContractError("DOCX/PDF parity role invalid")
        docx_name = f"{role}.docx"
        pdf_name = f"{role}.pdf"
        docx_record = document_records[docx_name]
        pdf_record = document_records[pdf_name]
        if (
            pair.get("status") != "pass"
            or pair.get("all_required_fragments_present") is not True
            or pair.get("docx_pdf_page_count_equal") is not True
            or pair.get("docx_sha256") != docx_record["sha256"]
            or pair.get("pdf_sha256") != pdf_record["sha256"]
            or pair.get("content_payload_sha256")
            != expected_document_payload_hashes[role]
            or docx_record["render_qa"]["page_count"]
            != pdf_record["render_qa"]["page_count"]
        ):
            raise FormalContentContractError(f"DOCX/PDF parity binding invalid:{role}")
        for fragment in expected_document_fragments[role]:
            normalized = _normalized_rendered_text(fragment)
            if normalized not in extracted_by_name[docx_name] or normalized not in extracted_by_name[pdf_name]:
                raise FormalContentContractError(f"DOCX/PDF semantic parity failed:{role}")
        seen_roles.add(role)
    if seen_roles != FORMAL_DOCUMENT_ROLES:
        raise FormalContentContractError("DOCX/PDF parity role set incomplete")
    return content


REQUIRED_BUNDLE_ARTIFACTS = {
    "diagnosis.json",
    "learning_feedback.json",
    "knowledge_handout.json",
    "training_tasks.json",
    "training_answers.json",
    "retest_day7.json",
    "retest_day7_answers.json",
    "retest_day14.json",
    "retest_day14_answers.json",
    "three_day_plan.json",
    "long_term_plan.json",
    "teacher_summary.json",
    "sources.json",
    "qa.json",
    "version.json",
}


def _content_questions_automated_verified(content: dict[str, Any]) -> bool:
    if content.get("content_completeness_status") != REAL_FORMAL_CONTENT_STATUS:
        return False
    question_groups = ("training_tasks", "retest_day7", "retest_day14")
    questions = [item for group in question_groups for item in content.get(group, [])]
    return bool(questions) and all(
        isinstance(item, dict)
        and item.get("machine_governance_state") == "automated_verified_candidate"
        and isinstance(item.get("governance_chain_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", item["governance_chain_sha256"])
        and isinstance(item.get("governance_chain_path"), str)
        and bool(item["governance_chain_path"])
        and isinstance(item.get("canonical_parent_chain"), dict)
        and item.get("atomic_part_id") == item["canonical_parent_chain"].get("atomic_part_id")
        and item.get("canonical_parent_chain_sha256") == _sha256_json(item["canonical_parent_chain"])
        for item in questions
    )


def _formal_documents_complete(content: dict[str, Any]) -> bool:
    try:
        validate_formal_content_export(content)
    except FormalContentContractError:
        return False
    return True


def _teacher_delivery_eligibility(
    *,
    diagnosis: dict[str, Any],
    content: dict[str, Any],
    artifact_hash_gate: bool,
    zip_hash_gate: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if diagnosis.get("claim_scope") != "machine_only_real_student_candidate":
        reasons.append("real_student_machine_candidate_required")
    preflight = diagnosis.get("controller_diagnose_preflight", {})
    if preflight.get("ready") is not True or preflight.get("mode") != "diagnose":
        reasons.append("live_controller_diagnose_preflight_required")
    if (
        preflight.get("controller_receipt_registry_bound") is not True
        or preflight.get("receipt_live_revalidated") is not True
        or not isinstance(preflight.get("controller_receipt_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", preflight.get("controller_receipt_sha256", ""))
        or not isinstance(preflight.get("governance_chain_registry_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", preflight.get("governance_chain_registry_sha256", ""))
    ):
        reasons.append("controller_receipt_and_governance_registry_binding_required")
    media = diagnosis.get("image_egress_audit", {})
    if media.get("all_raw_model_access_blocked") is not True or media.get("all_sanitized_egress_allowed") is not True:
        reasons.append("privacy_media_egress_gate_required")
    if not _content_questions_automated_verified(content):
        reasons.append("all_content_questions_must_be_automated_verified_candidate")
    if not _formal_documents_complete(content):
        reasons.append("formal_student_and_answer_docx_pdf_required")
    if not artifact_hash_gate:
        reasons.append("artifact_hash_gate_required")
    if not zip_hash_gate:
        reasons.append("zip_hash_gate_required")
    return not reasons, reasons


def build_zip_bundle(
    output_path: Path,
    *,
    diagnosis: dict[str, Any],
    plan: dict[str, Any],
    content: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    is_synthetic_subject = diagnosis.get("claim_scope") == CLAIM_SCOPE_SYNTHETIC
    is_structure_fixture = (
        is_synthetic_subject
        and content.get("content_completeness_status") == SYNTHETIC_CONTENT_STATUS
    )
    is_governed_contract_fixture = (
        is_synthetic_subject
        and content.get("content_completeness_status")
        == GOVERNED_CONTRACT_FIXTURE_STATUS
    )
    if is_structure_fixture:
        assert_no_direct_identifiers(content)
        content_completeness_status = SYNTHETIC_CONTENT_STATUS
    elif is_governed_contract_fixture:
        validate_formal_content_export(
            content,
            diagnosis=diagnosis,
            plan=plan,
            allow_governed_fixture=True,
        )
        content_completeness_status = GOVERNED_CONTRACT_FIXTURE_STATUS
    elif is_synthetic_subject:
        raise StudentLearningError(
            "synthetic ZIP requires structure or governed-contract-fixture content"
        )
    else:
        validate_formal_content_export(content, diagnosis=diagnosis, plan=plan)
        content_completeness_status = REAL_FORMAL_CONTENT_STATUS
    includes_formal_documents = not is_structure_fixture
    complete_real_week_pack = not is_synthetic_subject
    common = {
        "claim_scope": diagnosis["claim_scope"], "subject_kind": diagnosis["subject_kind"],
        "machine_status": MACHINE_STATUS, "human_reviewed": False,
        "teaching_effectiveness_unverified": True,
        "content_completeness_status": content_completeness_status,
        "complete_week_pack": complete_real_week_pack,
    }
    artifacts: dict[str, Any] = {
        "diagnosis.json": diagnosis,
        "learning_feedback.json": {**common, **plan["learning_feedback"]},
        "knowledge_handout.json": {
            **common,
            "sections": plan["knowledge_explanation"] if is_structure_fixture else content["knowledge_handout"]["sections"],
            "rendering_status": (
                "structure_fixture_only"
                if is_structure_fixture
                else "governed_contract_fixture_only"
                if is_governed_contract_fixture
                else "formal_generation_export"
            ),
        },
        "training_tasks.json": {**common, "items": content["training_tasks"]},
        "training_answers.json": {**common, "answer_authority": "suggested", "items": content["training_answers"]},
        "retest_day7.json": {**common, "items": content["retest_day7"], "return_action": "rediagnose_and_build_week2"},
        "retest_day7_answers.json": {**common, "answer_authority": "suggested", "items": content["retest_day7_answers"]},
        "retest_day14.json": {**common, "items": content["retest_day14"], "return_action": "rediagnose_and_recommend_next_cycle"},
        "retest_day14_answers.json": {**common, "answer_authority": "suggested", "items": content["retest_day14_answers"]},
        "three_day_plan.json": {**common, "stage_match": plan["stage_match"], "time_budget": plan["time_budget"], "days": plan["three_day_repair"]},
        "long_term_plan.json": {**common, "week_number": plan["week_number"], "phases": plan["long_term_plan"], "weekly_schedule": plan["weekly_schedule"], "schedule_audit": plan["schedule_audit"], "retest_contract": plan["retest_contract"], "delta_policy": plan["delta_policy"]},
        "teacher_summary.json": {**common, "audience": "teacher", "review_status": MACHINE_STATUS, "summary": plan["learning_feedback"]["summary"], "weekly_focus": plan["weekly_focus"]},
        "sources.json": {
            **common,
            "source_status": "synthetic_fixture_evidence_only" if diagnosis["claim_scope"] == CLAIM_SCOPE_SYNTHETIC else "controller_receipted_machine_only_private_evidence",
            "adapter_receipt": diagnosis["adapter_receipt"],
            "formal_sources_used": [] if is_structure_fixture else content.get("sources", []),
        },
        "qa.json": {**common, "structural_checks": {"weekly_schedule_recomputed": plan["schedule_audit"]["contract_match"], "weekly_minutes_exact": plan["schedule_audit"]["total_minutes"] == 420, "allocation_60_25_15": plan["schedule_audit"]["focus_bucket_minutes"] == WEEKLY_SPLIT, "week_end_retest_included": plan["retest_contract"]["included_in_weekly_420_minutes"], "training_retest_ids_disjoint": len({item["task_id"] for group in ("training_tasks", "retest_day7", "retest_day14") for item in content[group]}) == sum(len(content[group]) for group in ("training_tasks", "retest_day7", "retest_day14")), "formal_document_count": len(content.get("rendered_documents", [])), "docx_pdf_parity_pairs": len(content.get("document_parity", [])), "provider_production_ready": content.get("provider_contract", {}).get("production_ready") is True}, "chemistry_review": "not_performed_machine_only" if is_structure_fixture else "governed_contract_fixture_not_real_delivery" if is_governed_contract_fixture else "machine_governance_only_not_human_reviewed", "publication_allowed": False},
        "version.json": {**common, "interface_version": INTERFACE_VERSION, "domain_version": DOMAIN_VERSION, "provider_contract_version": content.get("contract_version"), "bundle_schema_version": "student_week_bundle_v1", "timeline_event_count": len(timeline), "formal_docx_pdf_status": "not_produced_structure_fixture_only" if is_structure_fixture else "governed_contract_fixture_render_verified_not_real_delivery" if is_governed_contract_fixture else "generation_export_hash_text_render_parity_verified", "formal_document_inventory": [] if is_structure_fixture else [{key: row[key] for key in ("name", "sha256", "sidecar_sha256", "media_type", "semantic_role", "content_payload_sha256", "extracted_text_sha256")} for row in content["rendered_documents"]]},
    }
    if set(artifacts) != REQUIRED_BUNDLE_ARTIFACTS:
        raise StudentLearningError("bundle artifact set drift")
    artifact_bytes = {name: json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n" for name, value in artifacts.items()}
    artifact_media_types = {name: "application/json" for name in artifact_bytes}
    if includes_formal_documents:
        for document in content["rendered_documents"]:
            archive_path = f"documents/{document['name']}"
            if archive_path in artifact_bytes:
                raise StudentLearningError(f"formal document archive collision:{archive_path}")
            artifact_bytes[archive_path] = Path(document["path"]).resolve().read_bytes()
            artifact_media_types[archive_path] = document["media_type"]
    manifest = {
        "schema_version": "student_week_zip_manifest_v1",
        "bundle_id": str(uuid.uuid4()),
        "profile_id": diagnosis["profile_id"],
        "week_number": plan["week_number"],
        "claim_scope": diagnosis["claim_scope"],
        "subject_kind": diagnosis["subject_kind"],
        "machine_status": MACHINE_STATUS,
        "human_reviewed": False,
        "teaching_effectiveness_unverified": True,
        "content_completeness_status": content_completeness_status,
        "complete_week_pack": complete_real_week_pack,
        "formal_docx_pdf_included": includes_formal_documents,
        "provider_production_ready": content.get("provider_contract", {}).get("production_ready") is True,
        "teacher_managed_delivery_candidate": False,
        "teacher_managed_delivery_reasons": ["final_zip_hash_sidecar_verification_required"],
        "official": False,
        "external_publication_allowed": False,
        "publication_allowed": False,
        "artifacts": [
            {"path": name, "sha256": _sha256_bytes(body), "byte_size": len(body), "media_type": artifact_media_types[name]}
            for name, body in sorted(artifact_bytes.items())
        ],
        "artifact_count": len(artifact_bytes),
        "hash_algorithm": "sha256",
        "manifest_self_hash": "excluded_to_avoid_recursive_hash",
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in artifact_bytes.items():
            archive.writestr(name, body)
        archive.writestr("manifest.json", manifest_bytes)
    sidecar = dict(manifest)
    sidecar["manifest_scope"] = "sidecar_final_zip_hash_bound"
    sidecar["zip_sha256"] = _sha256_bytes(output_path.read_bytes())
    sidecar["zip_byte_size"] = output_path.stat().st_size
    eligible, reasons = _teacher_delivery_eligibility(
        diagnosis=diagnosis, content=content, artifact_hash_gate=True, zip_hash_gate=True
    )
    sidecar["teacher_managed_delivery_candidate"] = eligible
    sidecar["teacher_managed_delivery_reasons"] = reasons
    sidecar_path = output_path.with_suffix(".manifest.sidecar.json")
    _write_json(sidecar_path, sidecar)
    sidecar["sidecar_filename"] = sidecar_path.name
    sidecar["sidecar_sha256"] = _sha256_bytes(sidecar_path.read_bytes())
    return sidecar


def verify_zip_bundle(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        expected = REQUIRED_BUNDLE_ARTIFACTS | {"manifest.json"}
        if manifest.get("formal_docx_pdf_included") is True:
            expected |= {f"documents/{name}" for name in REQUIRED_FORMAL_DOCUMENTS}
        if names != expected:
            raise StudentLearningError(f"ZIP members mismatch: {sorted(names ^ expected)}")
        for row in manifest["artifacts"]:
            body = archive.read(row["path"])
            if _sha256_bytes(body) != row["sha256"] or len(body) != row["byte_size"]:
                raise StudentLearningError(f"ZIP hash mismatch: {row['path']}")
            if row.get("media_type") != "application/json":
                expected_type = (
                    "application/pdf" if row["path"].endswith(".pdf")
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    if row["path"].endswith(".docx") else None
                )
                if row.get("media_type") != expected_type:
                    raise StudentLearningError(f"ZIP binary media type mismatch: {row['path']}")
                continue
            payload = json.loads(body)
            expected_scope = manifest.get("claim_scope")
            expected_subject = manifest.get("subject_kind")
            if payload.get("claim_scope") != expected_scope or payload.get("subject_kind") != expected_subject:
                raise StudentLearningError(f"bundle claim boundary mismatch: {row['path']}")
            if payload.get("human_reviewed") is not False:
                raise StudentLearningError(f"human review spoof detected: {row['path']}")
            if payload.get("teaching_effectiveness_unverified") is not True:
                raise StudentLearningError(f"teaching effectiveness boundary missing: {row['path']}")
        if manifest.get("claim_scope") not in {CLAIM_SCOPE_SYNTHETIC, "machine_only_real_student_candidate"}:
            raise StudentLearningError("unsupported bundle claim scope")
        if manifest.get("claim_scope") == CLAIM_SCOPE_SYNTHETIC and manifest.get("subject_kind") != SUBJECT_KIND_SYNTHETIC:
            raise StudentLearningError("synthetic bundle subject mismatch")
        if manifest.get("claim_scope") == "machine_only_real_student_candidate" and manifest.get("subject_kind") != "anonymous_real_student_private":
            raise StudentLearningError("real bundle subject mismatch")
        if manifest.get("teacher_managed_delivery_candidate") is not False:
            raise StudentLearningError("embedded manifest cannot claim teacher-managed delivery before final ZIP hash")
        if manifest.get("official") is not False or manifest.get("external_publication_allowed") is not False:
            raise StudentLearningError("official/external publication boundary violated")
        if manifest.get("claim_scope") == CLAIM_SCOPE_SYNTHETIC and (
            manifest.get("content_completeness_status") == SYNTHETIC_CONTENT_STATUS
        ):
            if (
                manifest.get("complete_week_pack") is not False
                or manifest.get("formal_docx_pdf_included") is not False
                or manifest.get("provider_production_ready") is not False
            ):
                raise StudentLearningError("synthetic structure fixture overstated content completeness")
            verification_status = "PASS_STRUCTURE_FIXTURE_ONLY_NOT_CONTENT_COMPLETE"
        elif manifest.get("claim_scope") == CLAIM_SCOPE_SYNTHETIC and (
            manifest.get("content_completeness_status")
            == GOVERNED_CONTRACT_FIXTURE_STATUS
        ):
            if (
                manifest.get("complete_week_pack") is not False
                or manifest.get("formal_docx_pdf_included") is not True
                or manifest.get("provider_production_ready") is not False
                or manifest.get("artifact_count") != 23
            ):
                raise StudentLearningError("governed contract fixture boundary invalid")
            verification_status = (
                "PASS_GOVERNED_CONTENT_FIXTURE_ONLY_NOT_REAL_DELIVERY"
            )
        else:
            if (
                manifest.get("content_completeness_status") != REAL_FORMAL_CONTENT_STATUS
                or manifest.get("complete_week_pack") is not True
                or manifest.get("formal_docx_pdf_included") is not True
                or manifest.get("provider_production_ready") is not True
                or manifest.get("artifact_count") != 23
            ):
                raise StudentLearningError("real formal bundle completeness fields invalid")
            verification_status = "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE"
    zip_sha256 = _sha256_bytes(path.read_bytes())
    sidecar_path = path.with_suffix(".manifest.sidecar.json")
    if not sidecar_path.is_file():
        raise StudentLearningError("final ZIP sidecar missing")
    try:
        sidecar = _read_json(sidecar_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudentLearningError("final ZIP sidecar unreadable") from exc
    if not isinstance(sidecar, dict) or (
        sidecar.get("schema_version") != "student_week_zip_manifest_v1"
        or sidecar.get("manifest_scope") != "sidecar_final_zip_hash_bound"
        or sidecar.get("bundle_id") != manifest.get("bundle_id")
        or sidecar.get("zip_sha256") != zip_sha256
        or sidecar.get("zip_byte_size") != path.stat().st_size
        or sidecar.get("artifact_count") != manifest.get("artifact_count")
        or sidecar.get("claim_scope") != manifest.get("claim_scope")
    ):
        raise StudentLearningError("final ZIP sidecar binding mismatch")
    return {
        "status": verification_status,
        "claim_scope": manifest["claim_scope"],
        "content_completeness_status": manifest["content_completeness_status"],
        "complete_week_pack": manifest["complete_week_pack"],
        "artifact_count": manifest["artifact_count"],
        "formal_document_count": 8
        if manifest.get("formal_docx_pdf_included") is True
        else 0,
        "zip_sha256": zip_sha256,
        "sidecar_filename": sidecar_path.name,
        "sidecar_sha256": _sha256_bytes(sidecar_path.read_bytes()),
    }


def build_public_aggregate(diagnoses: list[dict[str, Any]], minimum_k: int = 5) -> dict[str, Any]:
    if minimum_k < 5:
        raise AggregationThresholdError("minimum_k cannot be below 5")
    unique: dict[str, dict[str, Any]] = {}
    for diagnosis in diagnoses:
        if diagnosis.get("claim_scope") != CLAIM_SCOPE_SYNTHETIC:
            raise PrivacyViolation("current public exporter accepts synthetic fixtures only")
        unique[diagnosis["profile_id"]] = diagnosis
    if len(unique) < minimum_k:
        raise AggregationThresholdError(f"public aggregate blocked: cohort {len(unique)} < k={minimum_k}")
    weakness_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    for diagnosis in unique.values():
        for row in diagnosis.get("conclusions", []):
            status_counts[row["status"]] += 1
            if row["status"] in {"stable_weakness", "provisional_weakness"}:
                weakness_counts[row["tag_id"]] += 1
        error_counts.update(diagnosis.get("error_type_counts", {}))
    result = {
        "schema_version": "public_student_learning_aggregate_v1",
        "privacy_model": "k_anonymous_minimum_5",
        "minimum_k": minimum_k,
        "cohort_size": len(unique),
        "claim_scope": "synthetic_aggregate_only",
        "subject_kind": "synthetic_cohort_not_real_students",
        "machine_status": MACHINE_STATUS,
        "human_reviewed": False,
        "contains_profile_ids": False,
        "contains_direct_identifiers": False,
        "contains_exact_timestamps": False,
        "contains_free_text_narratives": False,
        "weakness_tag_counts": dict(sorted(weakness_counts.items())),
        "conclusion_status_counts": dict(sorted(status_counts.items())),
        "error_type_counts": dict(sorted(error_counts.items())),
        "limitations": ["synthetic aggregate only", "not evidence of real student outcomes"],
    }
    assert_public_aggregate_safe(result)
    return result


def assert_public_aggregate_safe(value: dict[str, Any]) -> None:
    assert_no_direct_identifiers(value)
    forbidden_keys = {"profile_id", "student_response", "attempted_at", "occurred_at", "generated_at", "created_at", "updated_at", "note", "narrative"}
    for path, key, child in _walk(value):
        if key in forbidden_keys:
            raise PrivacyViolation(f"public aggregate forbidden field: {path}")
        if isinstance(child, str) and re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", child):
            raise PrivacyViolation(f"public aggregate exact time rejected: {path}")
    if value.get("cohort_size", 0) < value.get("minimum_k", 5):
        raise AggregationThresholdError("public aggregate fell below declared k")
