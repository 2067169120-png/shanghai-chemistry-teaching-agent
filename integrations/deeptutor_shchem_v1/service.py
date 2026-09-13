from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from .adapters import (
    AdapterUnavailable,
    CatalogEvidenceReader,
    CCSwitchClient,
    ControllerRpcAdapter,
    FixtureAdapter,
)
from .candidate_review import (
    CandidateCropPayload,
    CandidateReviewError,
    Wave1CandidateReviewReader,
)
from .config import CONTRACT_VERSION, AppConfig, Principal
from .curriculum_workbench import (
    CurriculumWorkbenchError,
    CurriculumWorkbenchReader,
)
from .deidentification import LocalDeidentifier
from .domain_adapters import GenerationDomainAdapter, GenerationFormalContentProvider
from .full_bank_readiness import FullBankReadinessError, FullBankReadinessReader
from .intake_imports import (
    INTAKE_IMPORT_WRITE_CAPABILITY,
    INTAKE_VISUAL_EXECUTE_CAPABILITY,
    IntakeImportError,
    IntakeImportJobManager,
    default_intake_import_root,
)
from .master_direct_visual_scan import (
    MasterDirectVisualScanError,
    MasterDirectVisualScanReader,
)
from .master_visual_scan_alias import (
    COVERAGE_KIND as MASTER_ALIAS_COVERAGE_KIND,
)
from .master_visual_scan_alias import (
    MasterVisualScanAliasError,
    MasterVisualScanAliasReader,
)
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .material_intake_workbench import (
    MaterialIntakeError,
    MaterialIntakeWorkbenchReader,
)
from .model_provider_probe import (
    MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY,
    ModelProviderModelListProbe,
    ModelProviderProbeError,
    ModelProviderSyntheticProbeManager,
    synthetic_probe_contract,
)
from .model_provider_settings import (
    DEFAULT_PROVIDER_POLICIES,
    MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY,
    ModelProviderSettingsError,
    ModelProviderSettingsStore,
)
from .paper_blueprint_workbench import (
    PaperBlueprintPreviewStore,
    PaperBlueprintWorkbench,
    PaperBlueprintWorkbenchError,
)
from .paper_blueprint_workbench import (
    canonical_sha256 as paper_blueprint_sha256,
)
from .paper_export_workbench import (
    PaperExportJobManager,
    PaperExportWorkbenchError,
    _compact_answer_space_lines,
    _prepare_catalog,
)
from .paper_format_presets import default_shanghai_theme_preset
from .presentation_jobs import PresentationJobManager
from .presentation_theme_adapter import (
    build_presentation_input,
    materialize_theme_assets,
    validate_theme_request,
)
from .presentation_workbench import (
    PresentationToolchain,
    PresentationWorkbenchError,
)
from .public_kb import GenerationRunReader, PublicKBReader, ReadOnlyDataError
from .question_processing_progress import (
    QuestionProcessingProgressError,
    QuestionProcessingProgressReader,
)
from .question_search_workbench import (
    QuestionSearchError,
    QuestionSearchWorkbench,
)
from .question_visual_scan import (
    QuestionVisualScanError,
    QuestionVisualScanReader,
)
from .r18_governance_view import R18GovernanceView
from .retrieval_workbench import (
    KB_RETRIEVAL_READ_CAPABILITY,
    RetrievalWorkbenchError,
    RetrievalWorkbenchGateway,
)
from .security import (
    ALLOWED_UPLOAD_MIME,
    AuditLogger,
    SecurityError,
    atomic_write_json,
    authorize_student,
    canonical_json_sha256,
    safe_join,
    validate_identifier,
)
from .student_bridge import StudentBridgeError, StudentDomainBridge
from .student_recommendation_projection import project_visual_recommendation_payload
from .student_recommendation_workbench import (
    StudentRecommendationWorkbench,
    StudentRecommendationWorkbenchError,
)
from .student_visual_analysis import (
    StudentVisualAnalysisError,
    StudentVisualAnalysisManager,
    default_student_visual_root,
)
from .supplemental_visual_scan import (
    SupplementalVisualScanError,
    SupplementalVisualScanReader,
)
from .supplemental_wechat_tagging_overlay import (
    BASE_REGISTRY_FILE_SHA256 as SUPPLEMENTAL_TAGGING_BASE_REGISTRY_FILE_SHA256,
)
from .supplemental_wechat_tagging_overlay import (
    SCOPE as SUPPLEMENTAL_TAGGING_OVERLAY_SCOPE,
)
from .supplemental_wechat_tagging_overlay import (
    SupplementalWechatTaggingOverlayError,
    SupplementalWechatTaggingOverlayReader,
)
from .tagging_workbench import (
    TAG_PATCH_WRITE_CAPABILITY,
    TagPatchGateway,
    TagPatchGatewayError,
)
from .theme_review_workbench import ThemeReviewGateway, ThemeReviewGatewayError
from .theme_workbench import ThemeWorkbenchError, ThemeWorkbenchReader
from .workbench_product_registry import (
    WorkbenchProductRegistryError,
    WorkbenchProductRegistryReader,
)


class ApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}


_STATUS_DROP = object()
_PRIVATE_STATUS_MARKERS = (
    "private_state",
    "private_profiles",
    "private_runtime",
    "student_data_root",
    "raw_student",
    "06_学生错题档案",
)
_STATUS_DRIVE_ABSOLUTE = re.compile(
    r"(?<![a-z0-9])[a-z]:[\\/]", re.IGNORECASE
)
_STATUS_UNC = re.compile(
    r"(?:(?<![\\/:])\\\\|(?<!:)//)[^\\/\s]+[\\/][^\\/\s]+"
)
_STATUS_FILE_URI = re.compile(
    r"(?<![a-z0-9+.-])file:(?:[\\/]{1,3}|[a-z]:)", re.IGNORECASE
)


def _status_path_key(key: str) -> bool:
    normalized = key.casefold()
    return (
        normalized in {"path", "root", "directory", "workspace", "workspace_root"}
        or normalized.endswith(("_path", "_root", "_directory"))
        or normalized.startswith(("path_", "root_", "workspace_"))
    )


def _status_string_variants(value: str) -> tuple[str, ...]:
    """Return the raw value and bounded URL-decoded forms for path scanning."""
    variants: list[str] = []
    candidate = value
    for _ in range(4):
        if candidate in variants:
            break
        variants.append(candidate)
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return tuple(variants)


def _status_sensitive_string(value: str, workspace_root: Path) -> bool:
    workspace = str(workspace_root.absolute()).casefold().replace("/", "\\")
    for candidate in _status_string_variants(value):
        normalized = candidate.casefold().replace("/", "\\")
        stripped = candidate.lstrip()
        if (
            _STATUS_DRIVE_ABSOLUTE.search(candidate)
            or _STATUS_UNC.search(candidate)
            or _STATUS_FILE_URI.search(candidate)
            or (stripped.startswith("/") and not stripped.startswith("/api/"))
            or workspace in normalized
            or any(
                marker.casefold() in normalized
                for marker in _PRIVATE_STATUS_MARKERS
            )
        ):
            return True
    return False


def _public_status_projection(value: Any, workspace_root: Path) -> Any:
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _status_path_key(name) or _status_sensitive_string(
                name, workspace_root
            ):
                continue
            clean = _public_status_projection(item, workspace_root)
            if clean is not _STATUS_DROP:
                projected[name] = clean
        return projected
    if isinstance(value, list):
        return [
            clean
            for item in value
            if (clean := _public_status_projection(item, workspace_root))
            is not _STATUS_DROP
        ]
    if isinstance(value, tuple):
        return _public_status_projection(list(value), workspace_root)
    if isinstance(value, str) and _status_sensitive_string(value, workspace_root):
        return _STATUS_DROP
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _STATUS_DROP


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _redact_structured(value: Any) -> tuple[Any, list[str]]:
    forbidden_keys = {
        "name",
        "full_name",
        "student_name",
        "school",
        "class",
        "student_number",
        "student_id",
        "phone",
        "mobile",
        "email",
        "identity_document_number",
        "id_card",
        "address",
    }
    findings: list[str] = []
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in forbidden_keys:
                findings.append(f"forbidden_key:{normalized}")
                continue
            clean_item, nested = _redact_structured(item)
            clean[str(key)] = clean_item
            findings.extend(nested)
        return clean, findings
    if isinstance(value, list):
        clean_list = []
        for item in value:
            clean_item, nested = _redact_structured(item)
            clean_list.append(clean_item)
            findings.extend(nested)
        return clean_list, findings
    if isinstance(value, str):
        redacted = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[REDACTED_PHONE]", value)
        redacted = re.sub(r"(?<!\d)\d{17}[0-9Xx](?!\d)", "[REDACTED_ID]", redacted)
        if redacted != value:
            findings.append("direct_identifier_pattern")
        return redacted, findings
    return value, findings


_STUDENT_CSV_HEADERS = {
    "grades": (
        ("earned", "maximum"),
        ("earned", "maximum", "assessment_ref"),
    ),
    "progress": (
        ("module", "completion_percent"),
        ("module", "completion_percent", "semester"),
    ),
}
_STRICT_DECIMAL = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?\Z")
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _strict_csv_number(value: str, field: str) -> int | float:
    if not _STRICT_DECIMAL.fullmatch(value):
        raise ApiError(
            "invalid_student_csv_number",
            f"CSV field {field} must be a plain non-negative decimal number",
        )
    number = float(value) if "." in value else int(value)
    if isinstance(number, float) and not math.isfinite(number):
        raise ApiError(
            "invalid_student_csv_number",
            f"CSV field {field} must be finite",
        )
    return number


def _parse_student_csv(data: bytes, kind: str) -> dict[str, Any]:
    expected_headers = _STUDENT_CSV_HEADERS.get(kind)
    if expected_headers is None:
        raise ApiError(
            "student_csv_kind_not_supported",
            "CSV is supported only for grades and progress",
            415,
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(
            "invalid_student_csv_encoding",
            "student CSV must be strict UTF-8",
        ) from exc
    if "\x00" in text:
        raise ApiError("invalid_student_csv", "student CSV contains a NUL byte")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.removesuffix("\n")
    physical_lines = normalized.split("\n") if normalized else []
    if len(physical_lines) != 2 or any(not line.strip() for line in physical_lines):
        raise ApiError(
            "invalid_student_csv_shape",
            "student CSV must contain exactly one header row and one non-empty data row",
        )
    try:
        rows = list(csv.reader(io.StringIO(normalized, newline=""), strict=True))
    except csv.Error as exc:
        raise ApiError("invalid_student_csv", "student CSV syntax is invalid") from exc
    if len(rows) != 2 or any(len(row) == 0 for row in rows):
        raise ApiError(
            "invalid_student_csv_shape",
            "student CSV must contain exactly one header row and one data row",
        )
    header = tuple(cell.strip() for cell in rows[0])
    if header not in expected_headers or len(set(header)) != len(header):
        allowed = " or ".join(",".join(row) for row in expected_headers)
        raise ApiError(
            "invalid_student_csv_header",
            f"student CSV header must be exactly {allowed}; unknown or reordered columns are rejected",
        )
    values = [cell.strip() for cell in rows[1]]
    if len(values) != len(header) or any(not value for value in values):
        raise ApiError(
            "invalid_student_csv_row",
            "student CSV data row must provide one non-empty value for every column",
        )
    if any(value.lstrip().startswith(_CSV_FORMULA_PREFIXES) for value in values):
        raise ApiError(
            "student_csv_formula_injection_rejected",
            "student CSV cells beginning with =, +, -, or @ are rejected",
        )
    parsed: dict[str, Any] = dict(zip(header, values, strict=True))
    if kind == "grades":
        earned = _strict_csv_number(parsed["earned"], "earned")
        maximum = _strict_csv_number(parsed["maximum"], "maximum")
        if maximum <= 0 or earned > maximum:
            raise ApiError(
                "invalid_student_csv_score_range",
                "CSV grade requires 0 <= earned <= maximum and maximum > 0",
            )
        parsed["earned"] = earned
        parsed["maximum"] = maximum
    else:
        completion = _strict_csv_number(
            parsed["completion_percent"], "completion_percent"
        )
        if completion > 100:
            raise ApiError(
                "invalid_student_csv_progress_range",
                "CSV progress completion_percent must be within 0..100",
            )
        parsed["completion_percent"] = completion
    for field in ("assessment_ref", "module", "semester"):
        value = parsed.get(field)
        if isinstance(value, str) and (
            len(value) > 256 or any(ord(character) < 32 for character in value)
        ):
            raise ApiError(
                "invalid_student_csv_text",
                f"CSV field {field} contains control characters or exceeds 256 characters",
            )
    return parsed


class StateStore:
    def __init__(
        self,
        root: Path,
        max_upload_bytes: int,
        deidentifier: LocalDeidentifier,
        student_bridge: StudentDomainBridge,
        student_data_root: Path | None,
    ):
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes
        self.deidentifier = deidentifier
        self.student_bridge = student_bridge
        self.student_data_root = (
            student_data_root.resolve() if student_data_root else None
        )
        self.root.mkdir(parents=True, exist_ok=True)

    def _student_root(self, student_id: str) -> Path:
        validate_identifier(student_id, "student_id")
        return safe_join(self.root, "students", student_id)

    def save_upload(self, student_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mime_type = str(payload.get("mime_type", ""))
        if mime_type not in ALLOWED_UPLOAD_MIME:
            raise ApiError(
                "unsupported_media_type", "upload MIME type is not allowed", 415
            )
        kind = str(payload.get("kind", ""))
        if kind not in {
            "student_work_image",
            "grades",
            "progress",
            "attempt",
            "timing",
            "other_local_input",
        }:
            raise ApiError("invalid_upload_kind", "upload kind is not allowed")
        encoded = payload.get("content_base64")
        if not isinstance(encoded, str):
            raise ApiError("invalid_upload", "content_base64 is required")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError("invalid_upload", "content_base64 is invalid") from exc
        if len(data) > self.max_upload_bytes:
            raise ApiError("upload_too_large", "upload exceeds configured limit", 413)
        if not data:
            raise ApiError("invalid_upload", "empty uploads are not allowed")

        parsed_csv: dict[str, Any] | None = None
        if mime_type == "text/csv":
            parsed_csv = _parse_student_csv(data, kind)
        elif mime_type == "text/plain" and kind in {
            "grades",
            "progress",
            "attempt",
            "timing",
        }:
            raise ApiError(
                "structured_text_plain_not_supported",
                "structured student inputs must use application/json; grades and progress may also use strict text/csv",
                415,
            )

        upload_id = f"upl-{uuid.uuid4().hex}"
        extension = ALLOWED_UPLOAD_MIME[mime_type]
        student_root = self._student_root(student_id)
        deidentification: dict[str, Any]
        if mime_type.startswith("image/"):
            def local_hold(reason: str) -> dict[str, Any]:
                return {
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
                    "reasons": [reason],
                    "processing_location": "local_private_hold_only",
                    "machine_status": "machine_only_not_human_reviewed",
                    "human_reviewed": False,
                    "claim_scope": "local_private_input_only",
                    "raw_sha256": _sha256_bytes(data),
                    "sanitized_sha256": None,
                }

            bridge_status = self.student_bridge.status()
            if bridge_status["available"]:
                try:
                    record, deidentification = self.student_bridge.import_image(
                        profile_id=student_id,
                        image_bytes=data,
                        mime_type=mime_type,
                        source_ref=upload_id,
                        input_deidentified=payload.get("input_deidentified"),
                        input_contains_face=payload.get("input_contains_face"),
                    )
                    deidentification = dict(deidentification)
                    deidentification["student_domain_media_id"] = record["media_id"]
                    deidentification["storage_owner"] = (
                        "integrations/student_learning_v1"
                    )
                except StudentBridgeError as exc:
                    original_path = safe_join(
                        student_root, "private", "originals", f"{upload_id}{extension}"
                    )
                    _atomic_write_bytes(original_path, data)
                    del exc
                    deidentification = local_hold("student_domain_bridge_failed")
            else:
                original_path = safe_join(
                    student_root, "private", "originals", f"{upload_id}{extension}"
                )
                _atomic_write_bytes(original_path, data)
                deidentification = local_hold("student_visual_provider_required")
        else:
            original_path = safe_join(
                student_root, "private", "originals", f"{upload_id}{extension}"
            )
            _atomic_write_bytes(original_path, data)
            deidentification = self._deidentify_textual(
                data=data,
                mime_type=mime_type,
                student_root=student_root,
                student_id=student_id,
                kind=kind,
                upload_id=upload_id,
                extension=extension,
                parsed_structured=parsed_csv,
            )

        metadata = {
            "contract_version": CONTRACT_VERSION,
            "upload_id": upload_id,
            "student_id": student_id,
            "kind": kind,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "original_sha256": _sha256_bytes(data),
            "original_saved_local_private": True,
            "original_filename_sha256": hashlib.sha256(
                str(payload.get("filename", "")).encode("utf-8")
            ).hexdigest(),
            "deidentification": deidentification,
            "created_at": _utc_now(),
        }
        metadata_path = safe_join(
            student_root, "metadata", "uploads", f"{upload_id}.json"
        )
        atomic_write_json(metadata_path, metadata)
        return self._public_upload(metadata)

    def _deidentify_textual(
        self,
        *,
        data: bytes,
        mime_type: str,
        student_root: Path,
        student_id: str,
        kind: str,
        upload_id: str,
        extension: str,
        parsed_structured: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "contract_version": "shchem.deidentification.v1",
                "status": "blocked",
                "model_egress_allowed": False,
                "blockers": ["text_not_utf8"],
                "review_kind": "machine_only",
            }
        csv_transformed = parsed_structured is not None
        if csv_transformed:
            clean, findings = _redact_structured(parsed_structured)
            clean_bytes = (
                json.dumps(clean, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
        elif mime_type == "application/json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {
                    "contract_version": "shchem.deidentification.v1",
                    "status": "blocked",
                    "model_egress_allowed": False,
                    "blockers": ["invalid_json"],
                    "review_kind": "machine_only",
                }
            clean, findings = _redact_structured(parsed)
            clean_bytes = (
                json.dumps(clean, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
        else:
            clean, findings = _redact_structured(text)
            clean_bytes = str(clean).encode("utf-8")
        derived_extension = ".json" if csv_transformed else extension
        clean_path = safe_join(
            student_root,
            "private",
            "deidentified",
            f"{upload_id}-deidentified{derived_extension}",
        )
        _atomic_write_bytes(clean_path, clean_bytes)
        domain_binding: dict[str, Any] = {
            "recorded": False,
            "interface_version": self.student_bridge.status().get("interface_version"),
        }
        if self.student_bridge.status()["available"]:
            try:
                structured = clean if isinstance(clean, dict) else {"text": clean}
                event = self.student_bridge.append_structured_input(
                    profile_id=student_id, kind=kind, payload=structured
                )
                domain_binding.update(
                    {
                        "recorded": True,
                        "event_id": event.get("event_id"),
                        "event_type": event.get("event_type"),
                    }
                )
            except StudentBridgeError as exc:
                domain_binding["reason"] = str(exc)
        result = {
            "contract_version": "shchem.deidentification.v1",
            "status": "machine_deidentified",
            "derived_copy": {
                "path": str(clean_path),
                "sha256": _sha256_bytes(clean_bytes),
                "transformation": (
                    "strict_single_row_csv_to_structured_json_then_identifier_redaction"
                    if csv_transformed
                    else "structured_key_and_identifier_pattern_redaction"
                ),
            },
            "checks": {
                "direct_identifiers": {
                    "status": "redacted" if findings else "clear",
                    "finding_codes": sorted(set(findings)),
                },
                "qr_barcode": {"status": "not_applicable"},
                "face_or_avatar": {"status": "not_applicable"},
                "file_metadata": {"status": "clear"},
            },
            "egress_allowed": True,
            "model_egress_allowed": True,
            "blockers": [],
            "review_kind": "machine_only",
            "student_domain_binding": domain_binding,
        }
        if (
            self.student_bridge.status()["configured"]
            and not domain_binding["recorded"]
        ):
            result["egress_allowed"] = False
            result["model_egress_allowed"] = False
            result["status"] = "blocked"
            result["blockers"] = ["student_domain_input_record_failed"]
        return result

    @staticmethod
    def _public_upload(metadata: dict[str, Any]) -> dict[str, Any]:
        deid = dict(metadata["deidentification"])
        derived = deid.get("derived_copy")
        if isinstance(derived, dict):
            deid["derived_copy"] = {
                "sha256": derived.get("sha256"),
                "transformation": derived.get("transformation"),
            }
        result = {
            "upload_id": metadata["upload_id"],
            "student_id": metadata["student_id"],
            "kind": metadata["kind"],
            "mime_type": metadata["mime_type"],
            "size_bytes": metadata["size_bytes"],
            "original_sha256": metadata["original_sha256"],
            "original_saved_local_private": True,
            "deidentification": deid,
            "created_at": metadata["created_at"],
        }
        return result

    def get_upload(self, student_id: str, upload_id: str) -> dict[str, Any]:
        validate_identifier(upload_id, "upload_id")
        path = safe_join(
            self._student_root(student_id), "metadata", "uploads", f"{upload_id}.json"
        )
        if not path.is_file():
            raise ApiError("upload_not_found", "upload not found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_job(self, student_id: str, job: dict[str, Any]) -> None:
        validate_identifier(job["job_id"], "job_id")
        path = safe_join(
            self._student_root(student_id), "jobs", f"{job['job_id']}.json"
        )
        atomic_write_json(path, job)

    def get_job(self, student_id: str, job_id: str) -> dict[str, Any]:
        validate_identifier(job_id, "job_id")
        path = safe_join(self._student_root(student_id), "jobs", f"{job_id}.json")
        if not path.is_file():
            raise ApiError("job_not_found", "job not found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_artifact_zip(
        self, student_id: str, job: dict[str, Any]
    ) -> dict[str, Any]:
        artifact_path = safe_join(
            self._student_root(student_id), "artifacts", f"{job['job_id']}.zip"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        members: dict[str, bytes] = {}
        for name, value in {
            "job.json": job,
            "machine_qa.json": job.get("machine_qa", {}),
            "candidate.json": job.get("result", {}),
        }.items():
            members[name] = (
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
        private_paths = job.get("private_paths", {})
        domain_artifact = private_paths.get("domain_artifact")
        if domain_artifact:
            domain_path = Path(str(domain_artifact)).resolve()
            student_root = self._student_root(student_id).resolve()
            allowed_roots = [student_root]
            if self.student_data_root is not None:
                allowed_roots.append(
                    safe_join(
                        self.student_data_root,
                        "private_profiles",
                        student_id[:2],
                        student_id,
                    )
                )
            if not any(root in domain_path.parents for root in allowed_roots) or not (
                domain_path.is_file()
            ):
                raise ApiError(
                    "domain_artifact_path_rejected",
                    "domain artifact is outside the student private root",
                    500,
                )
            members["student_week_bundle.zip"] = domain_path.read_bytes()
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "job_id": job["job_id"],
            "student_id": student_id,
            "claim_scope": job.get("claim_scope"),
            "machine_only": True,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
            "teacher_managed_delivery_candidate": job.get("delivery_gate", {}).get(
                "allowed", False
            ),
            "external_publication_allowed": False,
            "official_claim_allowed": False,
            "raw_uploads_included": False,
            "files": [
                {"path": name, "sha256": _sha256_bytes(data)}
                for name, data in sorted(members.items())
            ],
        }
        members["MANIFEST.json"] = (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(members.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
        zip_bytes = buffer.getvalue()
        _atomic_write_bytes(artifact_path, zip_bytes)
        return {"sha256": _sha256_bytes(zip_bytes), "size_bytes": len(zip_bytes)}

    def domain_work_path(self, student_id: str, job_id: str, name: str) -> Path:
        validate_identifier(job_id, "job_id")
        return safe_join(self._student_root(student_id), "domain_jobs", job_id, name)

    def artifact_path(self, student_id: str, job_id: str) -> Path:
        validate_identifier(job_id, "job_id")
        path = safe_join(self._student_root(student_id), "artifacts", f"{job_id}.zip")
        if not path.is_file():
            raise ApiError("artifact_not_found", "artifact not found", 404)
        return path


class GatewayService:
    def __init__(self, config: AppConfig):
        # Imported here to keep the workbench adapter's ApiError translation
        # independent from module import order.
        from .generation_workbench import GenerationWorkbenchGateway

        self.config = config
        release_runtime = config.workbench_release_runtime
        self.frozen_browse = (
            release_runtime.frozen_reader
            if release_runtime is not None
            and release_runtime.serving.mode == "frozen"
            else None
        )
        self.workbench_release_gateway: Any | None = None
        if release_runtime is not None:
            from .workbench_release_gateway import WorkbenchReleaseGateway

            self.workbench_release_gateway = WorkbenchReleaseGateway(
                config.shchem_root.absolute().parent,
                release_runtime.release_root,
                release_runtime.serving,
            )
        self.deidentifier = LocalDeidentifier()
        self.generation_bridge = GenerationDomainAdapter()
        self.student_bridge = StudentDomainBridge(
            config.student_data_root,
            config.student_capabilities,
            config.shchem_root,
            GenerationFormalContentProvider(self.generation_bridge),
        )
        self.store = StateStore(
            config.state_root,
            config.max_upload_bytes,
            self.deidentifier,
            self.student_bridge,
            config.student_data_root,
        )
        self.generation_workbench = GenerationWorkbenchGateway(config.state_root)
        self.audit = AuditLogger(safe_join(config.state_root, "audit", "gateway.jsonl"))
        self.catalog = CatalogEvidenceReader(config.shchem_root)
        self.public_kb = PublicKBReader(config.shchem_root)
        self.tag_patch_workbench = TagPatchGateway(
            self.public_kb, config.shchem_root.absolute().parent
        )
        self.retrieval_workbench = RetrievalWorkbenchGateway(
            config.shchem_root.absolute().parent
        )
        self.generation_runs = GenerationRunReader(config.shchem_root)
        self.r18_governance = R18GovernanceView(config.shchem_root)
        self.candidate_review = Wave1CandidateReviewReader(config.shchem_root)
        self.full_bank_readiness = FullBankReadinessReader(config.shchem_root)
        self.material_intake = MaterialIntakeWorkbenchReader(config.shchem_root)
        self.curriculum_workbench = CurriculumWorkbenchReader(config.shchem_root)
        self.question_search_workbench = QuestionSearchWorkbench()
        self.student_recommendation_workbench = StudentRecommendationWorkbench()
        self.master_wave1_workbench = MasterWave1WorkbenchReader(config.shchem_root)
        self.master_direct_visual_scans = MasterDirectVisualScanReader(
            config.shchem_root,
            master_workbench=self.master_wave1_workbench,
        )
        self.question_visual_scans = QuestionVisualScanReader(config.shchem_root)
        self.supplemental_visual_scans = SupplementalVisualScanReader(
            config.shchem_root
        )
        self.supplemental_wechat_tagging_overlay = (
            SupplementalWechatTaggingOverlayReader(
                config.shchem_root,
                base_reader=self.supplemental_visual_scans,
            )
        )
        self.theme_workbench = ThemeWorkbenchReader(
            config.shchem_root,
            master_workbench=self.master_wave1_workbench,
            direct_scans=self.master_direct_visual_scans,
            wave_scans=self.question_visual_scans,
        )
        self.question_processing_progress_reader = QuestionProcessingProgressReader(
            config.shchem_root,
            theme_workbench=self.theme_workbench,
            master_workbench=self.master_wave1_workbench,
            wave_review=self.candidate_review,
        )
        # Mutable claim/change/decision records are loaded lazily and remain
        # outside the immutable browse closure.  The task definitions come
        # from the selected frozen release when one is serving.
        self._theme_review_lock = threading.Lock()
        self.theme_review_workbench: ThemeReviewGateway | None = None
        self.workbench_product_registry = WorkbenchProductRegistryReader(
            config.shchem_root,
            config.overlay_root,
            theme_workbench=self.theme_workbench,
            supplemental_visual_scans=self.supplemental_visual_scans,
        )
        self.paper_export_jobs = PaperExportJobManager(config.state_root)
        self.paper_blueprint_workbench = PaperBlueprintWorkbench()
        self.paper_blueprint_previews = PaperBlueprintPreviewStore(
            config.state_root
        )
        # PPT rendering is optional and initialized lazily. A missing bundled
        # presentation toolchain must not prevent offline question browsing.
        self.presentation_jobs: PresentationJobManager | None = None
        self._presentation_lock = threading.Lock()
        self.model_provider_settings: ModelProviderSettingsStore | None = None
        # The manager is created lazily on the first explicitly authorized
        # synthetic probe.  Merely starting or browsing the workbench never
        # opens a provider connection.
        self.model_provider_probe_manager: (
            ModelProviderSyntheticProbeManager | None
        ) = None
        self.model_provider_model_list_client: ModelProviderModelListProbe | None = None
        self._model_provider_probe_lock = threading.Lock()
        self.intake_import_jobs: IntakeImportJobManager | None = None
        self._intake_import_lock = threading.Lock()
        self.model_provider_settings_error: str | None = (
            "model_provider_settings_not_configured"
        )
        if config.model_provider_metadata_root is not None:
            try:
                self.model_provider_settings = ModelProviderSettingsStore(
                    config.model_provider_metadata_root,
                    project_root=config.shchem_root.absolute().parent,
                )
                self.model_provider_settings_error = None
            except (ModelProviderSettingsError, OSError) as exc:
                # A credential-store failure must never take the offline
                # question workbench down with it.
                self.model_provider_settings_error = getattr(
                    exc, "code", "model_provider_settings_unavailable"
                )
        if config.student_data_root is not None:
            student_visual_root = config.student_data_root / "visual-analysis-v1"
        elif config.personal_auto_auth:
            student_visual_root = default_student_visual_root()
        else:
            student_visual_root = config.state_root / "student-visual-v1"
        student_curriculum_catalog: dict[str, Any] | None = None
        try:
            if self.frozen_browse is not None:
                curriculum_operation = getattr(
                    self.frozen_browse, "curriculum_catalog", None
                )
                if curriculum_operation is not None:
                    candidate_catalog = self._frozen_browse_call(
                        curriculum_operation
                    )
                else:
                    candidate_catalog = None
            else:
                candidate_catalog = self.curriculum_workbench.catalog()
            if isinstance(candidate_catalog, dict):
                student_curriculum_catalog = candidate_catalog
        except (ApiError, CurriculumWorkbenchError):
            # Student image review must remain available when an older frozen
            # browse release predates the curriculum catalog.  In that case
            # diagnostic chapter decisions fail closed instead of falling
            # back to a different data snapshot.
            student_curriculum_catalog = None
        self.student_visual_analysis = StudentVisualAnalysisManager(
            student_visual_root,
            project_root=config.shchem_root.absolute().parent,
            provider_store=self.model_provider_settings,
            curriculum_catalog=student_curriculum_catalog,
            max_upload_bytes=config.max_upload_bytes,
            require_project_external=config.personal_auto_auth,
        )
        self.ccswitch = CCSwitchClient(
            config.ccswitch_enabled,
            config.ccswitch_base_url,
            config.ccswitch_timeout_seconds,
        )
        if config.mode == "mock":
            if config.fixture_path is None:
                raise ValueError("fixture_path required")
            self.adapter: FixtureAdapter | ControllerRpcAdapter = FixtureAdapter(
                config.fixture_path
            )
        else:
            self.adapter = ControllerRpcAdapter(
                config.shchem_root,
                config.controller_script,
                config.controller_timeout_seconds,
                (config.state_root,),
            )

    @staticmethod
    def _require_teacher(principal: Principal) -> None:
        if principal.role != "teacher":
            raise SecurityError(
                "teacher_scope_required",
                "this console endpoint requires a teacher principal",
                403,
            )

    @classmethod
    def _require_tag_patch_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if TAG_PATCH_WRITE_CAPABILITY not in principal.capabilities:
            raise SecurityError(
                "tag_patch_write_capability_required",
                "teacher principal lacks candidate tag-patch write capability",
                403,
            )

    @classmethod
    def _require_review_task_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if "review_task_write" not in principal.capabilities:
            raise SecurityError(
                "review_task_write_capability_required",
                "当前教师令牌没有领取或释放整主题复核任务的权限",
                403,
            )

    @classmethod
    def _require_review_candidate_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if "review_candidate_write" not in principal.capabilities:
            raise SecurityError(
                "review_candidate_write_capability_required",
                "当前教师令牌没有提交整主题候选变更的权限",
                403,
            )

    @classmethod
    def _require_review_decision_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if "review_decision_write" not in principal.capabilities:
            raise SecurityError(
                "review_decision_write_capability_required",
                "当前教师令牌没有记录整主题复核决定的权限",
                403,
            )

    @classmethod
    def _require_kb_retrieval_read(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if KB_RETRIEVAL_READ_CAPABILITY not in principal.capabilities:
            raise SecurityError(
                "kb_retrieval_read_capability_required",
                "teacher principal lacks read-only evidence retrieval capability",
                403,
            )

    @classmethod
    def _require_model_provider_settings_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY not in principal.capabilities:
            raise SecurityError(
                "model_provider_settings_write_capability_required",
                "teacher principal lacks model-provider settings capability",
                403,
            )

    @classmethod
    def _require_model_provider_synthetic_probe_execute(
        cls, principal: Principal
    ) -> None:
        cls._require_teacher(principal)
        if (
            MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY
            not in principal.capabilities
        ):
            raise SecurityError(
                "model_provider_synthetic_probe_execute_capability_required",
                "teacher principal lacks fixed synthetic provider probe capability",
                403,
            )

    @classmethod
    def _require_intake_import_write(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if INTAKE_IMPORT_WRITE_CAPABILITY not in principal.capabilities:
            raise SecurityError(
                "intake_import_write_capability_required",
                "teacher principal lacks intake import write capability",
                403,
            )

    @classmethod
    def _require_intake_visual_execute(cls, principal: Principal) -> None:
        cls._require_intake_import_write(principal)
        if INTAKE_VISUAL_EXECUTE_CAPABILITY not in principal.capabilities:
            raise SecurityError(
                "intake_visual_execute_capability_required",
                "teacher principal lacks visual intake execution capability",
                403,
            )

    @classmethod
    def _require_workbench_release_prepare(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if "workbench_release_prepare_write" not in principal.capabilities:
            raise SecurityError(
                "workbench_release_prepare_capability_required",
                "teacher principal lacks candidate release preparation capability",
                403,
            )

    @classmethod
    def _require_workbench_release_activate(cls, principal: Principal) -> None:
        cls._require_teacher(principal)
        if "workbench_release_activate_write" not in principal.capabilities:
            raise SecurityError(
                "workbench_release_activate_capability_required",
                "teacher principal lacks candidate release selection capability",
                403,
            )

    def _release_gateway(self) -> Any:
        if self.workbench_release_gateway is None:
            raise ApiError(
                "workbench_release_control_unavailable",
                "candidate browse release control is unavailable",
                503,
            )
        return self.workbench_release_gateway

    def _theme_review_gateway(self) -> ThemeReviewGateway:
        with self._theme_review_lock:
            gateway = self.theme_review_workbench
            if gateway is not None:
                return gateway
            state_root = self.config.review_workbench_root or safe_join(
                self.config.state_root, "theme-review-workbench"
            )
            serving = (
                self.config.workbench_release_runtime.serving
                if self.config.workbench_release_runtime is not None
                else None
            )
            release_context = {
                "serving_release_id": serving.release_id if serving else None,
                "data_snapshot_id": serving.data_snapshot_id if serving else None,
                "browse_snapshot_id": serving.browse_snapshot_id if serving else None,
            }
            try:
                if self.frozen_browse is not None:
                    gateway = ThemeReviewGateway(
                        catalog=self.frozen_browse.review_task_catalog_bytes(),
                        state_root=state_root,
                        release_context=release_context,
                    )
                else:
                    gateway = ThemeReviewGateway.from_live_readers(
                        self.public_kb,
                        self.theme_workbench,
                        state_root=state_root,
                        release_context=release_context,
                    )
            except ThemeReviewGatewayError as exc:
                raise ApiError(exc.code, str(exc), exc.status) from exc
            self.theme_review_workbench = gateway
            return gateway

    @staticmethod
    def _release_gateway_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            code = getattr(exc, "code", "workbench_release_operation_failed")
            status = getattr(exc, "status", 409)
            if not isinstance(code, str) or not code:
                code = "workbench_release_operation_failed"
            if isinstance(status, bool) or not isinstance(status, int):
                status = 409
            raise ApiError(
                code,
                "candidate browse release operation failed closed",
                status,
            ) from exc

    @staticmethod
    def _readonly_call(operation: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except ReadOnlyDataError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _frozen_browse_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            code = getattr(exc, "code", "browse_snapshot_read_failed")
            status = getattr(exc, "status", 409)
            if not isinstance(code, str) or not code:
                code = "browse_snapshot_read_failed"
            if isinstance(status, bool) or not isinstance(status, int):
                status = 409
            raise ApiError(
                code,
                "frozen workbench browse snapshot failed closed",
                status,
            ) from exc

    @staticmethod
    def _frozen_route_component(value: str) -> str:
        return quote(value, safe="")

    def taxonomy(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.public_kb.taxonomy)

    @staticmethod
    def _candidate_review_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return operation(*args, **kwargs)
        except CandidateReviewError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def candidate_review_status(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/sources/candidate_review_only/wave1/status",
            )
        return self._candidate_review_call(self.candidate_review.status)

    def candidate_review_list(
        self,
        principal: Principal,
        *,
        node_type: str | None,
        query: str | None,
        paper_id: str | None,
        theme_id: str | None,
        printed_question_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            operation = getattr(self.frozen_browse, "candidate_list", None)
            if operation is None:
                raise ApiError(
                    "browse_snapshot_runtime_incompatible",
                    "frozen Wave1 filtering is unavailable",
                    503,
                )
            return self._frozen_browse_call(
                operation,
                node_type=node_type,
                query=query,
                paper_id=paper_id,
                theme_id=theme_id,
                printed_question_id=printed_question_id,
                limit=limit,
                offset=offset,
            )
        return self._candidate_review_call(
            self.candidate_review.list_nodes,
            node_type=node_type,
            query=query,
            paper_id=paper_id,
            theme_id=theme_id,
            printed_question_id=printed_question_id,
            limit=limit,
            offset=offset,
        )

    def candidate_review_node(
        self, principal: Principal, node_type: str, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
                f"{self._frozen_route_component(node_type)}/"
                f"{self._frozen_route_component(node_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        return self._candidate_review_call(
            self.candidate_review.node, node_type, node_id
        )

    def candidate_review_question_crop(
        self, principal: Principal, node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
                f"atomic_part/{self._frozen_route_component(node_id)}/"
                f"question-crops/{self._frozen_route_component(crop_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.crop, route)
        return self._candidate_review_call(
            self.candidate_review.question_crop, node_id, crop_id
        )

    @staticmethod
    def _full_bank_readiness_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except FullBankReadinessError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def full_bank_readiness_status(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._full_bank_readiness_call(self.full_bank_readiness.status)

    def full_bank_readiness_records(
        self,
        principal: Principal,
        *,
        cohort: str | None,
        stage: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._full_bank_readiness_call(
            self.full_bank_readiness.list_records,
            cohort=cohort,
            stage=stage,
            query=query,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _material_intake_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except MaterialIntakeError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def material_intake_status(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json, "/api/v1/intake/status"
            )
        return self._material_intake_call(self.material_intake.status)

    def material_intake_batches(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json, "/api/v1/intake/batches"
            )
        return self._material_intake_call(self.material_intake.list_batches)

    def material_intake_batch_detail(
        self, principal: Principal, batch_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/intake/batches/"
                f"{self._frozen_route_component(batch_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        return self._material_intake_call(
            self.material_intake.batch_detail, batch_id
        )

    def material_intake_records(
        self,
        principal: Principal,
        *,
        kind: str | None,
        stage: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            operation = getattr(
                self.frozen_browse, "material_intake_records", None
            )
            if operation is None:
                raise ApiError(
                    "browse_snapshot_runtime_incompatible",
                    "frozen material intake filtering is unavailable",
                    503,
                )
            return self._frozen_browse_call(
                operation,
                kind=kind,
                stage=stage,
                status=status,
                query=query,
                limit=limit,
                offset=offset,
            )
        return self._material_intake_call(
            self.material_intake.list_records,
            kind=kind,
            stage=stage,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )

    def intake_import_create(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_intake_import_write(principal)
        result = self._intake_import_call(
            self._intake_import_manager().create,
            payload,
            actor_id=principal.principal_id,
        )
        result["_http_status"] = 201
        return result

    def intake_import_upload(
        self,
        principal: Principal,
        import_id: str,
        data: bytes,
        *,
        content_type: str,
    ) -> dict[str, Any]:
        self._require_intake_import_write(principal)
        return self._intake_import_call(
            self._intake_import_manager().upload,
            import_id,
            data,
            content_type=content_type,
            actor_id=principal.principal_id,
        )

    def intake_import_analyze(
        self,
        principal: Principal,
        import_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_intake_visual_execute(principal)
        if set(payload) != {
            "profile_id",
            "expected_revision",
            "teacher_confirmed_egress",
        }:
            raise ApiError(
                "intake_analysis_request_invalid",
                "intake analysis request fields are invalid",
                400,
            )
        result = self._intake_import_call(
            self._intake_import_manager().analyze,
            import_id,
            profile_id=payload["profile_id"],
            expected_revision=payload["expected_revision"],
            teacher_confirmed_egress=payload["teacher_confirmed_egress"],
            actor_id=principal.principal_id,
        )
        if result.get("status") in {"queued_for_analysis", "analyzing"}:
            result["_http_status"] = 202
        return result

    def intake_import_get(
        self, principal: Principal, import_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._intake_import_call(
            self._intake_import_manager().get,
            import_id,
            actor_id=principal.principal_id,
        )

    def intake_import_page_content(
        self, principal: Principal, import_id: str, page: int
    ) -> CandidateCropPayload:
        self._require_teacher(principal)
        payload = self._intake_import_call(
            self._intake_import_manager().page_content,
            import_id,
            page,
            actor_id=principal.principal_id,
        )
        content = payload.get("content")
        content_type = payload.get("mime_type")
        sha256 = payload.get("sha256")
        if (
            not isinstance(content, bytes)
            or not isinstance(content_type, str)
            or not isinstance(sha256, str)
        ):
            raise ApiError(
                "intake_page_payload_invalid",
                "intake page payload is invalid",
                409,
            )
        return CandidateCropPayload(
            data=content,
            sha256=sha256,
            content_type=content_type,
        )

    def intake_import_review(
        self,
        principal: Principal,
        import_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_intake_import_write(principal)
        expected_fields = {
            "expected_review_revision",
            "candidate_sha256",
            "decision",
            "acknowledged_blocker_codes",
            "teacher_note_zh",
        }
        if set(payload) != expected_fields:
            raise ApiError(
                "intake_review_request_invalid",
                "intake review request fields are invalid",
                400,
            )
        return self._intake_import_call(
            self._intake_import_manager().review_decision,
            import_id,
            expected_review_revision=payload["expected_review_revision"],
            candidate_sha256=payload["candidate_sha256"],
            decision=payload["decision"],
            acknowledged_blocker_codes=payload["acknowledged_blocker_codes"],
            teacher_note_zh=payload["teacher_note_zh"],
            actor_id=principal.principal_id,
        )

    def intake_import_personal_library(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._intake_import_call(
            self._intake_import_manager().personal_library,
            actor_id=principal.principal_id,
        )

    def intake_import_cancel(
        self,
        principal: Principal,
        import_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_intake_import_write(principal)
        if payload:
            raise ApiError(
                "intake_cancel_request_invalid",
                "intake cancellation request must be an empty JSON object",
                400,
            )
        return self._intake_import_call(
            self._intake_import_manager().cancel,
            import_id,
            actor_id=principal.principal_id,
        )

    @staticmethod
    def _master_wave1_workbench_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except MasterWave1WorkbenchError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def master_atomic_workbench_status(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/master-atomic/status",
            )
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shchem-master-visible"
        ) as executor:
            master_future = executor.submit(
                self._master_wave1_workbench_call,
                self.master_wave1_workbench.status,
            )
            context_future = executor.submit(
                self._master_visual_scan_coverage_context
            )
            result = master_future.result()
            context = context_future.result()
        if result.get("counts", {}).get("exact_identity") != 169:
            raise ApiError(
                "master_visual_scan_coverage_invalid",
                "Master exact visual-scan coverage failed closed",
                409,
            )
        result["visual_scan_coverage"] = context["coverage"]
        return result

    def master_atomic_workbench_list(
        self, principal: Principal, *, limit: int, offset: int
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.catalog_page,
                "master",
                limit=limit,
                offset=offset,
            )
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shchem-master-visible"
        ) as executor:
            master_future = executor.submit(
                self._master_wave1_workbench_call,
                self.master_wave1_workbench.list_atomic,
                limit=limit,
                offset=offset,
            )
            context_future = executor.submit(
                self._master_visual_scan_coverage_context
            )
            result = master_future.result()
            context = context_future.result()
        result["items"] = [
            self._decorate_master_visual_scan_item(item, context)
            for item in result["items"]
        ]
        result["visual_scan_coverage"] = context["coverage"]
        return result

    def master_atomic_workbench_detail(
        self, principal: Principal, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/master-atomic/"
                f"{self._frozen_route_component(node_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shchem-master-visible"
        ) as executor:
            master_future = executor.submit(
                self._master_wave1_workbench_call,
                self.master_wave1_workbench.atomic_detail,
                node_id,
            )
            context_future = executor.submit(
                self._master_visual_scan_coverage_context
            )
            result = master_future.result()
            context = context_future.result()
        result["node"] = self._decorate_master_visual_scan_item(
            result["node"], context
        )
        if node_id in context["alias_items"]:
            alias_reader = self._master_visual_scan_alias_reader()
            alias_detail = self._master_visual_scan_alias_call(
                alias_reader.detail, node_id
            )
            assert isinstance(alias_detail, dict)
            result["visual_scan_alias"] = alias_detail
        else:
            result["visual_scan_alias"] = None
        result["visual_scan_coverage"] = context["coverage"]
        return result

    @staticmethod
    def _question_visual_scan_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except QuestionVisualScanError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def question_visual_scan_status(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/question-visual-scans/status",
            )
        return self._question_visual_scan_call(self.question_visual_scans.status)

    def question_visual_scan_catalog(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/question-visual-scans/catalog",
            )
        return self._question_visual_scan_call(self.question_visual_scans.catalog)

    def question_visual_scan_detail(
        self, principal: Principal, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/question-visual-scans/"
                f"{self._frozen_route_component(node_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        return self._question_visual_scan_call(
            self.question_visual_scans.detail, node_id
        )

    @staticmethod
    def _theme_workbench_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except ThemeWorkbenchError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def theme_workbench_groups(
        self, principal: Principal, *, scope: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.theme_groups, scope
            )
        if self.workbench_product_registry.activated:
            return self._workbench_product_registry_call(
                self.workbench_product_registry.theme_groups, scope
            )
        if scope == "supplemental":
            return self._supplemental_visual_scan_call(
                self.supplemental_visual_scans.theme_groups
            )
        return self._theme_workbench_call(self.theme_workbench.groups, scope)

    @staticmethod
    def _curriculum_workbench_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except CurriculumWorkbenchError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def textbook_catalog(self, principal: Principal) -> dict[str, Any]:
        """Return the selected release's safe 5/19/60 curriculum tree."""

        self._require_teacher(principal)
        if self.frozen_browse is not None:
            operation = getattr(self.frozen_browse, "curriculum_catalog", None)
            if operation is None:
                raise ApiError(
                    "browse_snapshot_runtime_incompatible",
                    "selected frozen release predates the curriculum catalog",
                    503,
                )
            return self._frozen_browse_call(operation)
        return self._curriculum_workbench_call(self.curriculum_workbench.catalog)

    def _curriculum_search(
        self, selector: dict[str, str]
    ) -> dict[str, Any]:
        if self.frozen_browse is not None:
            operation = getattr(self.frozen_browse, "curriculum_search", None)
            if operation is None:
                raise ApiError(
                    "browse_snapshot_runtime_incompatible",
                    "selected frozen release predates curriculum search",
                    503,
                )
            return self._frozen_browse_call(operation, **selector)
        return self._curriculum_workbench_call(
            self.curriculum_workbench.search, **selector
        )

    @staticmethod
    def _question_processing_progress_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except QuestionProcessingProgressError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def question_processing_progress(
        self,
        principal: Principal,
        *,
        scope: str,
        paper_id: str | None,
        theme_id: str | None,
        gap: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            operation = getattr(
                self.frozen_browse, "question_processing_progress", None
            )
            if operation is None:
                raise ApiError(
                    "browse_snapshot_runtime_incompatible",
                    "frozen question-processing progress is unavailable",
                    503,
                )
            return self._frozen_browse_call(
                operation,
                scope=scope,
                paper_id=paper_id,
                theme_id=theme_id,
                gap=gap,
                limit=limit,
                offset=offset,
            )
        return self._question_processing_progress_call(
            self.question_processing_progress_reader.list_progress,
            scope=scope,
            paper_id=paper_id,
            theme_id=theme_id,
            gap=gap,
            limit=limit,
            offset=offset,
        )

    def question_search(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Search selected theme bytes without ever switching data sources.

        In frozen mode ``theme_workbench_groups`` reads only the selected
        release.  Any missing or incompatible frozen route therefore fails
        closed through the existing frozen-reader adapter instead of falling
        back to the mutable live workspace.
        """

        self._require_teacher(principal)
        snapshot_id: str | None = None
        release_runtime = self.config.workbench_release_runtime
        if self.frozen_browse is not None:
            manifest_operation = getattr(self.frozen_browse, "manifest", None)
            if manifest_operation is not None:
                manifest = self._frozen_browse_call(manifest_operation)
                candidate = manifest.get("browse_snapshot_id")
                if isinstance(candidate, str):
                    snapshot_id = candidate
            elif release_runtime is not None:
                candidate = release_runtime.serving.data_snapshot_id
                if isinstance(candidate, str):
                    snapshot_id = candidate
        try:
            return self.question_search_workbench.search(
                payload,
                theme_loader=lambda scope: self.theme_workbench_groups(
                    principal, scope=scope
                ),
                curriculum_loader=self._curriculum_search,
                snapshot_id=snapshot_id,
            )
        except QuestionSearchError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _workbench_product_registry_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except WorkbenchProductRegistryError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def workbench_readiness(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        result = (
            self._frozen_browse_call(self.frozen_browse.readiness)
            if self.frozen_browse is not None
            else self._workbench_product_registry_call(
                self.workbench_product_registry.readiness
            )
        )
        store = self.model_provider_settings
        provider_connected = False
        if store is not None:
            try:
                profiles = store.list_metadata()
            except ModelProviderSettingsError:
                profiles = []
            provider_connected = any(
                profile.get("model_configured") is True
                and isinstance(profile.get("last_probe"), dict)
                and profile["last_probe"].get("status") == "succeeded"
                and profile["last_probe"].get("connection_state") == "connected"
                for profile in profiles
            )
            if provider_connected and self.frozen_browse is None:
                result["blockers"] = [
                    blocker
                    for blocker in result["blockers"]
                    if blocker.get("code") != "model_provider_not_configured"
                ]
        if self.frozen_browse is not None:
            result["runtime_dynamic"] = {
                "model_provider": {
                    "connected": provider_connected,
                    "frozen_readiness_mutated": False,
                    "offline_browse_available": True,
                }
            }
        return result

    def workbench_registry(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(self.frozen_browse.registry)
        return self._workbench_product_registry_call(
            self.workbench_product_registry.registry
        )

    def workbench_release_status(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        gateway = self._release_gateway()
        return self._release_gateway_call(gateway.status)

    def workbench_release_candidates(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        gateway = self._release_gateway()
        return self._release_gateway_call(gateway.list_candidates)

    def workbench_release_freeze(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_workbench_release_prepare(principal)
        if payload:
            raise ApiError(
                "workbench_release_freeze_request_invalid",
                "candidate freeze accepts no client parameters",
                400,
            )
        gateway = self._release_gateway()
        result = self._release_gateway_call(gateway.freeze_candidate)
        result["_http_status"] = 201
        return result

    def workbench_release_regression_start(
        self,
        principal: Principal,
        release_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_workbench_release_prepare(principal)
        if set(payload) != {"idempotency_key"}:
            raise ApiError(
                "workbench_release_regression_request_invalid",
                "fixed regression accepts an idempotency key only",
                400,
            )
        gateway = self._release_gateway()
        return self._release_gateway_call(
            gateway.start_regression,
            release_id,
            idempotency_key=payload["idempotency_key"],
        )

    def workbench_release_regression_status(
        self, principal: Principal, release_id: str, run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        gateway = self._release_gateway()
        return self._release_gateway_call(
            gateway.regression_status, release_id, run_id
        )

    def workbench_release_regression_cancel(
        self,
        principal: Principal,
        release_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_workbench_release_prepare(principal)
        if payload:
            raise ApiError(
                "workbench_release_regression_cancel_request_invalid",
                "fixed regression cancellation accepts no client parameters",
                400,
            )
        gateway = self._release_gateway()
        return self._release_gateway_call(
            gateway.cancel_regression, release_id, run_id
        )

    @staticmethod
    def _release_selection_payload(payload: dict[str, Any]) -> tuple[str, Any, str]:
        if set(payload) != {"run_id", "expected_revision", "reason_zh"}:
            raise ApiError(
                "workbench_release_selection_request_invalid",
                "release selection fields are invalid",
                400,
            )
        run_id = payload["run_id"]
        expected_revision = payload["expected_revision"]
        reason_zh = payload["reason_zh"]
        if (
            not isinstance(reason_zh, str)
            or not reason_zh.strip()
            or len(reason_zh) > 500
            or re.search(r"[\u3400-\u9fff]", reason_zh) is None
            or any(ord(character) < 32 for character in reason_zh)
        ):
            raise ApiError(
                "workbench_release_selection_request_invalid",
                "a Chinese selection reason is required",
                400,
            )
        return run_id, expected_revision, reason_zh

    def workbench_release_select(
        self,
        principal: Principal,
        release_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_workbench_release_activate(principal)
        run_id, expected_revision, reason_zh = self._release_selection_payload(
            payload
        )
        if expected_revision is not None and not isinstance(expected_revision, str):
            raise ApiError(
                "workbench_release_selection_request_invalid",
                "expected revision must be a string or null",
                400,
            )
        gateway = self._release_gateway()
        return self._release_gateway_call(
            gateway.select_release,
            release_id,
            run_id,
            expected_revision=expected_revision,
            principal_id=principal.principal_id,
            reason_zh=reason_zh,
        )

    def workbench_release_rollback(
        self,
        principal: Principal,
        release_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_workbench_release_activate(principal)
        run_id, expected_revision, reason_zh = self._release_selection_payload(
            payload
        )
        if not isinstance(expected_revision, str):
            raise ApiError(
                "workbench_release_selection_request_invalid",
                "rollback requires the current revision",
                400,
            )
        gateway = self._release_gateway()
        return self._release_gateway_call(
            gateway.rollback_release,
            release_id,
            run_id,
            expected_revision=expected_revision,
            principal_id=principal.principal_id,
            reason_zh=reason_zh,
        )

    @staticmethod
    def _model_provider_settings_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return operation(*args, **kwargs)
        except ModelProviderSettingsError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _model_provider_probe_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return operation(*args, **kwargs)
        except ModelProviderProbeError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def _model_provider_store(self) -> ModelProviderSettingsStore:
        if self.model_provider_settings is None:
            raise ApiError(
                self.model_provider_settings_error
                or "model_provider_settings_unavailable",
                "model-provider settings storage is unavailable; offline browsing remains available",
                503,
            )
        return self.model_provider_settings

    @staticmethod
    def _intake_import_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except IntakeImportError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def _intake_import_manager(self) -> IntakeImportJobManager:
        with self._intake_import_lock:
            manager = self.intake_import_jobs
            if manager is None:
                state_root = self.config.intake_import_root or default_intake_import_root()
                manager = self._intake_import_call(
                    IntakeImportJobManager,
                    state_root,
                    project_root=self.config.shchem_root.absolute().parent,
                    provider_store=self.model_provider_settings,
                    max_upload_bytes=self.config.max_upload_bytes,
                )
                self.intake_import_jobs = manager
            return manager

    def _model_provider_probe_manager(
        self,
    ) -> ModelProviderSyntheticProbeManager:
        with self._model_provider_probe_lock:
            manager = self.model_provider_probe_manager
            if manager is None:
                manager = ModelProviderSyntheticProbeManager(
                    self._model_provider_store()
                )
                self.model_provider_probe_manager = manager
            return manager

    def _model_provider_model_list_client(self) -> ModelProviderModelListProbe:
        with self._model_provider_probe_lock:
            client = self.model_provider_model_list_client
            if client is None:
                client = ModelProviderModelListProbe(self._model_provider_store())
                self.model_provider_model_list_client = client
            return client

    def _cancel_model_provider_probe_for_mutation(
        self, profile_id: str, *, expected_revision: str | None
    ) -> None:
        # Do not instantiate a manager for an ordinary settings write.  If a
        # manager already exists, cancellation is requested before the revision
        # changes; the settings-store CAS independently makes any racing result
        # stale.
        with self._model_provider_probe_lock:
            manager = self.model_provider_probe_manager
        if manager is not None:
            self._model_provider_probe_call(
                manager.cancel_profile,
                profile_id,
                expected_revision=expected_revision,
            )

    def shutdown(self) -> None:
        """Stop local background probe workers without enabling any new work."""

        release_gateway = self.workbench_release_gateway
        self.workbench_release_gateway = None
        if release_gateway is not None:
            release_gateway.shutdown()
        with self._model_provider_probe_lock:
            manager = self.model_provider_probe_manager
            self.model_provider_probe_manager = None
        if manager is not None:
            manager.shutdown(wait=True)
        with self._intake_import_lock:
            intake_manager = self.intake_import_jobs
            self.intake_import_jobs = None
        if intake_manager is not None:
            intake_manager.shutdown(wait=True)
        with self._presentation_lock:
            presentation_manager = self.presentation_jobs
            self.presentation_jobs = None
        if presentation_manager is not None:
            presentation_manager.shutdown(wait=True)
        self.student_visual_analysis.shutdown()
        self.paper_export_jobs.shutdown(wait=True)

    @staticmethod
    def _paper_export_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except PaperExportWorkbenchError as exc:
            raise ApiError(
                exc.code,
                str(exc),
                exc.status,
                exc.details,
            ) from exc

    def _paper_export_scope_snapshot_id(
        self, principal: Principal, scope: str
    ) -> str:
        registry = self.workbench_registry(principal)
        products = registry.get("products")
        if not isinstance(products, list):
            raise ApiError(
                "workbench_registry_invalid",
                "当前活动题库注册表不完整，不能导出。",
                409,
            )
        product = next(
            (
                value
                for value in products
                if isinstance(value, dict) and value.get("scope") == scope
            ),
            None,
        )
        snapshot_id = product.get("data_snapshot_id") if isinstance(product, dict) else None
        if not isinstance(snapshot_id, str) or not _is_sha256(snapshot_id):
            raise ApiError(
                "workbench_product_snapshot_invalid",
                "当前题库范围没有可用的数据快照，不能导出。",
                409,
            )
        return snapshot_id

    @staticmethod
    def _paper_blueprint_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return operation(*args, **kwargs)
        except PaperBlueprintWorkbenchError as exc:
            raise ApiError(
                exc.code,
                exc.message_zh,
                exc.status,
                dict(exc.details),
            ) from exc

    @staticmethod
    def _paper_blueprint_manual_basket(
        payload: dict[str, Any],
        selections: Any,
        *,
        catalog: dict[str, Any],
        scope: str,
        snapshot_id: str,
    ) -> tuple[dict[str, Any], dict[str, set[str]] | None]:
        if selections is None:
            return payload, None
        if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
            raise ApiError(
                "paper_blueprint_basket_invalid",
                "手工题篮须包含 1—100 个条目。",
                400,
            )
        theme_ids = {
            group.get("theme", {}).get("id")
            for paper in catalog.get("papers", [])
            if isinstance(paper, dict)
            for group in paper.get("theme_groups", [])
            if isinstance(group, dict) and isinstance(group.get("theme"), dict)
        }
        theme_ids = {
            value for value in theme_ids if isinstance(value, str) and value
        }
        selected_order: list[str] = []
        full_themes: set[str] = set()
        target_atomics: set[str] = set()
        targets_by_theme: dict[str, set[str]] = {}
        normalized_selections: list[dict[str, Any]] = []
        seen: set[str] = set()
        required_fields = {
            "scope",
            "selection_unit",
            "theme_id",
            "target_atomic_id",
            "expected_data_snapshot_id",
        }
        for value in selections:
            if not isinstance(value, dict) or set(value) != required_fields:
                raise ApiError(
                    "paper_blueprint_basket_invalid",
                    "手工题篮条目字段不完整。",
                    400,
                )
            unit = value.get("selection_unit")
            theme_id = value.get("theme_id")
            target = value.get("target_atomic_id")
            if (
                value.get("scope") != scope
                or value.get("expected_data_snapshot_id") != snapshot_id
                or unit not in {"theme", "dependency", "atomic"}
                or not isinstance(theme_id, str)
                or theme_id not in theme_ids
            ):
                raise ApiError(
                    "paper_blueprint_basket_stale",
                    "手工题篮与当前题库范围、快照或主题不一致。",
                    409,
                )
            normalized_unit = "theme" if unit == "theme" else "dependency"
            if normalized_unit == "theme":
                if target not in {None, ""}:
                    raise ApiError(
                        "paper_blueprint_basket_invalid",
                        "加入整主题时不能同时指定单题。",
                        400,
                    )
                target = None
                full_themes.add(theme_id)
            elif not isinstance(target, str) or not target:
                raise ApiError(
                    "paper_blueprint_basket_invalid",
                    "依赖闭包题篮条目必须指定目标作答单元。",
                    400,
                )
            else:
                target_atomics.add(target)
                targets_by_theme.setdefault(theme_id, set()).add(target)
            identity = paper_blueprint_sha256(
                {
                    "unit": normalized_unit,
                    "theme_id": theme_id,
                    "target_atomic_id": target,
                }
            )
            if identity in seen:
                raise ApiError(
                    "paper_blueprint_basket_invalid",
                    "手工题篮不能含重复条目。",
                    400,
                )
            seen.add(identity)
            if theme_id not in selected_order:
                selected_order.append(theme_id)
            normalized_selections.append(
                {
                    "scope": scope,
                    "selection_unit": normalized_unit,
                    "theme_id": theme_id,
                    "target_atomic_id": target,
                    "expected_data_snapshot_id": snapshot_id,
                }
            )
        result = deepcopy(payload)
        hard = result.get("hard_constraints", {})
        if not isinstance(hard, dict):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "硬约束格式不正确。",
                400,
            )
        hard = deepcopy(hard)

        def merge_ids(field: str, values: set[str]) -> None:
            current = hard.get(field, [])
            if not isinstance(current, list) or any(
                not isinstance(item, str) for item in current
            ):
                raise ApiError(
                    "paper_blueprint_request_invalid",
                    f"{field} 必须是字符串列表。",
                    400,
                )
            hard[field] = list(dict.fromkeys([*current, *sorted(values)]))

        merge_ids("required_theme_ids", full_themes)
        merge_ids("required_atomic_ids", target_atomics)
        merge_ids("excluded_theme_ids", theme_ids - set(selected_order))
        result["hard_constraints"] = hard
        preferences = result.get("preferences", {})
        if not isinstance(preferences, dict):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "组卷偏好格式不正确。",
                400,
            )
        preferences = deepcopy(preferences)
        preferences["selection_unit"] = (
            "dependency" if target_atomics else "theme"
        )
        result["preferences"] = preferences
        ordering = result.get("ordering", {})
        if not isinstance(ordering, dict):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "主题排序规则格式不正确。",
                400,
            )
        ordering = deepcopy(ordering)
        ordering["teacher_theme_order"] = selected_order
        ordering.setdefault("prerequisite_edges", [])
        result["ordering"] = ordering
        result["candidate_count"] = 1
        return result, {
            "full_themes": full_themes,
            "target_atomics": target_atomics,
            "selected_themes": set(selected_order),
            **{
                f"targets:{theme_id}": targets
                for theme_id, targets in targets_by_theme.items()
            },
        }

    @staticmethod
    def _paper_blueprint_project_manual_highlights(
        search_result: dict[str, Any],
        manual: dict[str, set[str]] | None,
    ) -> dict[str, Any]:
        if manual is None:
            return search_result
        projected = deepcopy(search_result)
        for item in projected.get("items", []):
            if not isinstance(item, dict):
                continue
            theme = item.get("theme")
            theme_id = theme.get("id") if isinstance(theme, dict) else None
            targets = manual.get(f"targets:{theme_id}", set())
            if theme_id in manual["full_themes"]:
                continue
            ordered_targets = [
                row.get("atomic_part_id")
                for row in item.get("atomic_chain", [])
                if isinstance(row, dict)
                and row.get("atomic_part_id") in targets
            ]
            item["matched_atomic_ids"] = ordered_targets
            item["match_details"] = [
                {
                    "atomic_part_id": atomic_id,
                    "reason_codes": ["teacher_basket_target"],
                }
                for atomic_id in ordered_targets
            ]
            counts = item.get("counts")
            if isinstance(counts, dict):
                counts["atomic_matched"] = len(ordered_targets)
        return projected

    @staticmethod
    def _paper_blueprint_complete_theme_search(
        loader: Any, initial_payload: dict[str, Any]
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        payload = deepcopy(initial_payload)
        seen_cursors: set[str] = set()
        stable_sha256: str | None = None
        declared_total: int | None = None
        while True:
            page_result = loader(payload)
            if not isinstance(page_result, dict):
                raise ApiError(
                    "question_search_result_invalid",
                    "题目检索分页结果格式不正确。",
                    409,
                )
            page = page_result.get("page")
            items = page_result.get("items")
            if not isinstance(page, dict) or not isinstance(items, list):
                raise ApiError(
                    "question_search_page_invalid",
                    "题目检索分页缺少题卡或分页状态。",
                    409,
                )
            returned = page.get("returned")
            total = page.get("total_theme_cards")
            has_more = page.get("has_more")
            next_cursor = page.get("next_cursor")
            if (
                type(returned) is not int
                or returned != len(items)
                or type(total) is not int
                or total < returned
                or not isinstance(has_more, bool)
                or (has_more and not isinstance(next_cursor, str))
                or (not has_more and next_cursor is not None)
            ):
                raise ApiError(
                    "question_search_page_invalid",
                    "题目检索分页计数或游标不一致。",
                    409,
                )
            stable = {
                key: deepcopy(page_result.get(key))
                for key in (
                    "schema_version",
                    "scope",
                    "q",
                    "filters",
                    "data_snapshot_id",
                    "curriculum",
                    "authority",
                )
            }
            current_sha256 = paper_blueprint_sha256(stable)
            if stable_sha256 is None:
                stable_sha256 = current_sha256
                declared_total = total
            elif current_sha256 != stable_sha256 or total != declared_total:
                raise ApiError(
                    "question_search_page_drift",
                    "题目检索后续页的查询条件、快照或总数发生漂移。",
                    409,
                )
            pages.append(deepcopy(page_result))
            if not has_more:
                break
            assert isinstance(next_cursor, str)
            if next_cursor in seen_cursors or len(pages) >= 1000:
                raise ApiError(
                    "question_search_page_invalid",
                    "题目检索分页游标循环或页数超过安全上限。",
                    409,
                )
            seen_cursors.add(next_cursor)
            payload = deepcopy(initial_payload)
            payload["cursor"] = next_cursor
        all_items = [
            deepcopy(item)
            for page_result in pages
            for item in page_result["items"]
            if isinstance(item, dict)
        ]
        if declared_total is None or len(all_items) != declared_total:
            raise ApiError(
                "question_search_incomplete",
                "题目检索分页结束后仍未收齐声明的全部题卡。",
                409,
            )
        complete_items = [
            item for item in all_items if item.get("group_kind") == "theme"
        ]
        result = deepcopy(pages[0])
        result["items"] = complete_items
        counts = result.get("counts")
        if not isinstance(counts, dict):
            raise ApiError(
                "question_search_result_invalid",
                "题目检索缺少稳定计数。",
                409,
            )
        counts["theme_cards_matched"] = len(complete_items)
        counts["atomic_parts_matched"] = sum(
            int(item.get("counts", {}).get("atomic_matched", 0))
            for item in complete_items
            if isinstance(item.get("counts"), dict)
        )
        counts["returned_theme_cards"] = len(complete_items)
        result["page"] = {
            "limit": initial_payload.get("limit", 50),
            "returned": len(complete_items),
            "total_theme_cards": len(complete_items),
            "has_more": False,
            "next_cursor": None,
            "aggregated_all_pages": True,
            "source_page_count": len(pages),
        }
        integrity = result.get("integrity")
        if not isinstance(integrity, dict):
            raise ApiError(
                "question_search_integrity_blocked",
                "题目检索缺少完整主题与依赖声明。",
                409,
            )
        integrity["complete_theme_chain_returned"] = True
        return result

    @staticmethod
    def _paper_blueprint_align_preview_answer_space(
        response: dict[str, Any], *, requested_lines: int
    ) -> dict[str, Any]:
        """Project the existing exporter's effective answer-line policy.

        The legacy four-file renderer treats the teacher's uniform line count
        as an upper bound and compacts it by atomic response type.  The preview
        must show those effective values before approval, while the export
        bridge still passes the original upper bound to the existing manager.
        """

        projected = deepcopy(response)
        for candidate in projected.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            preview = candidate.get("preview_model")
            if not isinstance(preview, dict):
                continue
            for theme in preview.get("theme_groups", []):
                if not isinstance(theme, dict):
                    continue
                rows = [
                    row
                    for row in theme.get("compact_rows", [])
                    if isinstance(row, dict)
                ]
                rows.extend(
                    row
                    for printed in theme.get("printed_questions", [])
                    if isinstance(printed, dict)
                    for row in printed.get("atomic_rows", [])
                    if isinstance(row, dict)
                )
                for row in rows:
                    response_type = row.get("response_type")
                    item_types = (
                        response_type.get("item_types")
                        if isinstance(response_type, dict)
                        else None
                    )
                    if (
                        not isinstance(item_types, list)
                        or len(item_types) != 1
                        or not isinstance(item_types[0], str)
                        or not isinstance(row.get("answer_space"), dict)
                    ):
                        raise ApiError(
                            "paper_blueprint_export_contract_invalid",
                            "预览中作答单元的题型或答题空间结构不完整。",
                            503,
                        )
                    row["answer_space"]["lines"] = (
                        _compact_answer_space_lines(
                            item_type=item_types[0],
                            requested_lines=requested_lines,
                        )
                    )
            preview_hash = paper_blueprint_sha256(preview)
            candidate["candidate_id"] = f"PBC-{preview_hash[:24]}"
            candidate["preview_snapshot_sha256"] = preview_hash
            preview_freeze = candidate.get("preview_freeze")
            if isinstance(preview_freeze, dict):
                preview_freeze["sha256"] = preview_hash
            export_precondition = candidate.get("export_precondition")
            if isinstance(export_precondition, dict):
                export_precondition[
                    "required_preview_snapshot_sha256"
                ] = preview_hash
        return projected

    def paper_blueprint_preview(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        allowed = {
            "mode",
            "scope",
            "data_snapshot_id",
            "candidate_count",
            "paper",
            "curriculum",
            "hard_constraints",
            "preferences",
            "ordering",
            "teacher_theme_order",
            "search",
            "basket_selections",
        }
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ApiError(
                "paper_blueprint_request_invalid",
                "智能组卷请求含未知字段。",
                400,
            )
        scope = payload.get("scope")
        snapshot_id = payload.get("data_snapshot_id")
        if not isinstance(scope, str) or not isinstance(snapshot_id, str):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "智能组卷必须绑定题库范围和快照。",
                400,
            )
        current_snapshot = self._paper_export_scope_snapshot_id(principal, scope)
        if snapshot_id != current_snapshot:
            raise ApiError(
                "theme_snapshot_stale",
                "智能组卷请求来自旧题库版本，请刷新后重试。",
                409,
            )
        catalog = self.theme_workbench_groups(principal, scope=scope)
        core_payload = deepcopy(payload)
        search = core_payload.pop("search", {})
        basket = core_payload.pop("basket_selections", None)
        if not isinstance(search, dict) or set(search) - {"q", "filters"}:
            raise ApiError(
                "paper_blueprint_search_invalid",
                "智能组卷检索只接受关键词和现有筛选器。",
                400,
            )
        paper = core_payload.get("paper")
        if not isinstance(paper, dict):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "智能组卷必须提交当前模式的试卷信息。",
                400,
            )
        paper = deepcopy(paper)
        paper.setdefault(
            "answer_space_lines",
            3 if core_payload.get("mode") == "mock_exam" else 2,
        )
        if (
            type(paper["answer_space_lines"]) is not int
            or not 0 <= paper["answer_space_lines"] <= 20
        ):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "答题空间须为 0—20 行，以便无损进入现有四文件导出器。",
                400,
            )
        paper.setdefault("score_per_atomic", 1)
        paper.setdefault("time_per_atomic_minutes", 1)
        if core_payload.get("mode") == "daily_practice" and (
            type(paper["time_per_atomic_minutes"]) is not int
            or not 1 <= paper["time_per_atomic_minutes"] <= 300
        ):
            raise ApiError(
                "paper_blueprint_request_invalid",
                "平时练习的统一逐题用时须为 1—300 分钟的整数。",
                400,
            )
        core_payload["paper"] = paper
        core_payload, manual = self._paper_blueprint_manual_basket(
            core_payload,
            basket,
            catalog=catalog,
            scope=scope,
            snapshot_id=snapshot_id,
        )
        search_payload: dict[str, Any] = {"scope": scope, "limit": 50}
        if "q" in search:
            search_payload["q"] = search["q"]
        if "filters" in search:
            search_payload["filters"] = deepcopy(search["filters"])
        curriculum = core_payload.get("curriculum")
        if curriculum is not None:
            search_payload["curriculum"] = deepcopy(curriculum)

        def load_search(value: dict[str, Any]) -> dict[str, Any]:
            try:
                result = self.question_search_workbench.search(
                    value,
                    theme_loader=lambda requested_scope: self.theme_workbench_groups(
                        principal, scope=requested_scope
                    ),
                    curriculum_loader=self._curriculum_search,
                    snapshot_id=snapshot_id,
                )
            except QuestionSearchError as exc:
                raise ApiError(exc.code, str(exc), exc.status) from exc
            return result

        first_search = self._paper_blueprint_complete_theme_search(
            load_search, search_payload
        )
        first_search = self._paper_blueprint_project_manual_highlights(
            first_search, manual
        )
        curriculum_catalog = self.textbook_catalog(principal)
        curriculum_mapping = self._curriculum_search(
            deepcopy(curriculum) if isinstance(curriculum, dict) else {}
        )
        preset = default_shanghai_theme_preset()
        try:
            response = self.paper_blueprint_workbench.optimize(
                core_payload,
                theme_catalog=catalog,
                question_search_result=first_search,
                paper_format_preset=preset,
                curriculum_catalog=curriculum_catalog,
                curriculum_mapping=curriculum_mapping,
                metadata_overlay={},
            )
        except PaperBlueprintWorkbenchError as exc:
            raise ApiError(
                exc.code, exc.message_zh, exc.status, dict(exc.details)
            ) from exc
        response = self._paper_blueprint_align_preview_answer_space(
            response,
            requested_lines=paper["answer_space_lines"],
        )
        fingerprint = paper_blueprint_sha256(
            {
                "contract": "shchem.paper-blueprint-preview-api.v1",
                "submitted_request": deepcopy(payload),
                "effective_request": core_payload,
                "data_snapshot_id": snapshot_id,
                "preset_sha256": paper_blueprint_sha256(preset),
                "theme_catalog_sha256": paper_blueprint_sha256(catalog),
                "question_search_sha256": paper_blueprint_sha256(
                    first_search
                ),
                "curriculum_catalog_sha256": paper_blueprint_sha256(
                    curriculum_catalog
                ),
                "curriculum_mapping_sha256": paper_blueprint_sha256(
                    curriculum_mapping
                ),
            }
        )
        return self._paper_blueprint_call(
            self.paper_blueprint_previews.save_preview,
            principal.principal_id,
            principal.token_sha256,
            response,
            request_fingerprint_sha256=fingerprint,
        )

    def paper_blueprint_approve(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        required = {
            "candidate_id",
            "preview_snapshot_sha256",
            "data_snapshot_id",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ApiError(
                "paper_blueprint_approval_request_invalid",
                "预览确认必须且只能提交候选、预览哈希和题库快照。",
                400,
            )
        candidate_id = payload["candidate_id"]
        scope, stored_snapshot = self._paper_blueprint_call(
            self.paper_blueprint_previews.candidate_scope,
            principal.principal_id,
            principal.token_sha256,
            candidate_id,
        )
        current_snapshot = self._paper_export_scope_snapshot_id(principal, scope)
        if stored_snapshot != current_snapshot:
            raise ApiError(
                "paper_blueprint_approval_stale",
                "候选绑定的题库快照已变化，请重新预览。",
                409,
            )
        return self._paper_blueprint_call(
            self.paper_blueprint_previews.approve,
            principal.principal_id,
            principal.token_sha256,
            payload,
            current_data_snapshot_id=current_snapshot,
        )

    @staticmethod
    def _paper_blueprint_export_request(
        candidate: dict[str, Any], normalized_request: dict[str, Any]
    ) -> dict[str, Any]:
        paper = normalized_request.get("paper")
        preview = candidate.get("preview_model")
        selections = candidate.get("basket_selections")
        if (
            not isinstance(paper, dict)
            or not isinstance(preview, dict)
            or not isinstance(selections, list)
            or not selections
        ):
            raise ApiError(
                "paper_blueprint_export_contract_invalid",
                "已批准候选缺少导出所需的服务端计划。",
                503,
            )
        score = paper.get("score_per_atomic")
        answer_lines = paper.get("answer_space_lines")
        if type(score) is not int or not 1 <= score <= 20:
            raise ApiError(
                "paper_blueprint_export_score_unrepresentable",
                "当前候选分值不能无损映射到既有四文件导出器；请统一逐题分值后重新预览。",
                409,
            )
        if type(answer_lines) is not int or not 0 <= answer_lines <= 20:
            raise ApiError(
                "paper_blueprint_export_answer_space_unrepresentable",
                "当前答题空间超过既有四文件导出器范围，请调整后重新预览。",
                409,
            )
        rows = [
            row
            for group in preview.get("theme_groups", [])
            if isinstance(group, dict)
            for row in group.get("compact_rows", [])
            if isinstance(row, dict)
        ]
        if not rows or any(row.get("score") != score for row in rows):
            raise ApiError(
                "paper_blueprint_export_score_unrepresentable",
                "预览中的逐题分值不是同一个整数，不能交给旧统一分值导出器。",
                409,
            )
        answer_space_mismatch = False
        for row in rows:
            response_type = row.get("response_type")
            item_types = (
                response_type.get("item_types")
                if isinstance(response_type, dict)
                else None
            )
            answer_space = row.get("answer_space")
            if (
                not isinstance(item_types, list)
                or len(item_types) != 1
                or not isinstance(item_types[0], str)
                or not isinstance(answer_space, dict)
                or answer_space.get("lines")
                != _compact_answer_space_lines(
                    item_type=item_types[0],
                    requested_lines=answer_lines,
                )
            ):
                answer_space_mismatch = True
                break
        if answer_space_mismatch:
            raise ApiError(
                "paper_blueprint_export_answer_space_unrepresentable",
                "预览中的有效答题空间与现有导出器策略不一致。",
                409,
            )
        if normalized_request.get("mode") == "mock_exam":
            duration = paper.get("duration_minutes")
        else:
            duration = candidate.get("coverage", {}).get("totals", {}).get(
                "estimated_time_minutes"
            )
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not float(duration).is_integer()
            or not 1 <= int(duration) <= 300
        ):
            raise ApiError(
                "paper_blueprint_export_duration_unrepresentable",
                "当前候选预计用时不能无损映射为 1—300 分钟的整数，请调整后重新预览。",
                409,
            )
        return {
            "title_zh": paper["title_zh"],
            "subtitle_zh": paper.get("subtitle_zh"),
            "duration_minutes": int(duration),
            "numbering_mode": "restart_within_each_theme",
            "score_per_atomic": score,
            "answer_space_lines": answer_lines,
            "selections": deepcopy(selections),
        }

    def paper_blueprint_export(
        self,
        principal: Principal,
        preview_approval_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if payload:
            raise ApiError(
                "paper_blueprint_export_request_invalid",
                "批准后的安全导出不接受客户端题篮或其他参数。",
                400,
            )
        scope, stored_snapshot = self._paper_blueprint_call(
            self.paper_blueprint_previews.approval_scope,
            principal.principal_id,
            principal.token_sha256,
            preview_approval_id,
        )
        current_snapshot = self._paper_export_scope_snapshot_id(principal, scope)
        if stored_snapshot != current_snapshot:
            raise ApiError(
                "paper_blueprint_approval_stale",
                "已批准预览来自旧题库快照，请重新预览。",
                409,
            )
        reservation = self._paper_blueprint_call(
            self.paper_blueprint_previews.begin_export,
            principal.principal_id,
            principal.token_sha256,
            preview_approval_id,
            current_data_snapshot_id=current_snapshot,
        )
        approval = reservation["approval"]
        if reservation["kind"] == "existing":
            job = self.paper_export_get(principal, reservation["job_id"])
            return {
                "schema_version": "shchem.paper-blueprint-approved-export.v1",
                "preview_approval_id": preview_approval_id,
                "approved_revision": approval["approved_revision"],
                "idempotent_replay": True,
                "job": job,
            }
        reservation_id = reservation["reservation_id"]
        try:
            export_payload = self._paper_blueprint_export_request(
                reservation["candidate"], reservation["normalized_request"]
            )
            job = self.paper_export_start(principal, export_payload)
            approval = self._paper_blueprint_call(
                self.paper_blueprint_previews.finish_export,
                principal.principal_id,
                principal.token_sha256,
                preview_approval_id,
                reservation_id,
                job_id=job["job_id"],
            )
        except Exception:
            try:
                self.paper_blueprint_previews.finish_export(
                    principal.principal_id,
                    principal.token_sha256,
                    preview_approval_id,
                    reservation_id,
                    job_id=None,
                )
            except PaperBlueprintWorkbenchError:
                pass
            raise
        return {
            "schema_version": "shchem.paper-blueprint-approved-export.v1",
            "preview_approval_id": preview_approval_id,
            "approved_revision": approval["approved_revision"],
            "idempotent_replay": False,
            "job": job,
        }

    def paper_export_start(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        raw_selections = payload.get("selections")
        if not isinstance(raw_selections, list) or not raw_selections:
            raise ApiError(
                "paper_export_request_invalid", "题篮不能为空。", 400
            )
        scopes = {
            value.get("scope")
            for value in raw_selections
            if isinstance(value, dict) and isinstance(value.get("scope"), str)
        }
        if len(scopes) != 1:
            raise ApiError(
                "multiple_scopes_require_dedup_review",
                "一次导出只能使用同一个题库范围；请分开导出。",
                409,
            )
        scope = next(iter(scopes))
        expected_snapshot = self._paper_export_scope_snapshot_id(principal, scope)
        if any(
            not isinstance(value, dict)
            or value.get("expected_data_snapshot_id") != expected_snapshot
            for value in raw_selections
        ):
            raise ApiError(
                "theme_snapshot_stale", "题篮来自旧题库版本，请刷新后重试。", 409
            )

        # Validate and freeze the exact theme projection before returning 202.
        # This prevents a stale client snapshot from becoming an asynchronous
        # failure after the UI has already shown a queued task.
        frozen_catalog = self.theme_workbench_groups(principal, scope=scope)
        self._paper_export_call(
            _prepare_catalog,
            frozen_catalog,
            scope=scope,
            snapshot_id=expected_snapshot,
        )

        master_crop_bindings: dict[str, str] = {}
        binding_lock = threading.Lock()

        def detail_loader_impl(source_scope: str, node_id: str) -> dict[str, Any]:
            if source_scope == "supplemental":
                return self.supplemental_visual_scan_detail(principal, node_id)
            if source_scope == "wave1":
                return self.question_visual_scan_detail(principal, node_id)
            if source_scope != "master":
                raise ApiError(
                    "paper_export_scope_invalid", "题库范围不支持导出。", 400
                )
            try:
                return self.master_direct_visual_scan_detail(principal, node_id)
            except ApiError as exc:
                if exc.status != 404:
                    raise
            master_detail = self.master_atomic_workbench_detail(principal, node_id)
            node = master_detail.get("node")
            summary = node.get("crosswalk_summary") if isinstance(node, dict) else None
            wave_ids = summary.get("wave1_node_ids") if isinstance(summary, dict) else None
            if (
                not isinstance(summary, dict)
                or summary.get("state") != "exact"
                or not isinstance(wave_ids, list)
                or len(wave_ids) != 1
                or not isinstance(wave_ids[0], str)
            ):
                raise ApiError(
                    "paper_export_visual_scan_unavailable",
                    "这道题尚无可导出的精确题面扫描，请先选择已逐图整理的题目。",
                    409,
                )
            wave_id = wave_ids[0]
            with binding_lock:
                master_crop_bindings[node_id] = wave_id
            return self.question_visual_scan_detail(principal, wave_id)

        def detail_loader(source_scope: str, node_id: str) -> dict[str, Any]:
            try:
                return detail_loader_impl(source_scope, node_id)
            except ApiError as exc:
                raise PaperExportWorkbenchError(
                    exc.code, str(exc), exc.status, details=exc.details
                ) from exc

        def crop_loader_impl(
            source_scope: str, node_id: str, crop_id: str
        ) -> CandidateCropPayload:
            if source_scope == "supplemental":
                return self.supplemental_visual_scan_question_crop(
                    principal, node_id, crop_id
                )
            if source_scope == "wave1":
                return self.candidate_review_question_crop(principal, node_id, crop_id)
            if source_scope != "master":
                raise ApiError(
                    "paper_export_scope_invalid", "题库范围不支持导出。", 400
                )
            with binding_lock:
                wave_id = master_crop_bindings.get(node_id)
            if wave_id is not None:
                return self.candidate_review_question_crop(
                    principal, wave_id, crop_id
                )
            return self.master_direct_visual_scan_question_crop(
                principal, node_id, crop_id
            )

        def crop_loader(
            source_scope: str, node_id: str, crop_id: str
        ) -> CandidateCropPayload:
            try:
                return crop_loader_impl(source_scope, node_id, crop_id)
            except ApiError as exc:
                raise PaperExportWorkbenchError(
                    exc.code, str(exc), exc.status, details=exc.details
                ) from exc

        return self._paper_export_call(
            self.paper_export_jobs.start,
            payload,
            theme_catalog_loader=lambda source_scope: frozen_catalog,
            detail_loader=detail_loader,
            crop_loader=crop_loader,
        )

    def paper_export_get(
        self, principal: Principal, job_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._paper_export_call(self.paper_export_jobs.get, job_id)

    def paper_export_artifact_path(
        self, principal: Principal, job_id: str, artifact_id: str
    ) -> tuple[Path, str]:
        self._require_teacher(principal)
        return self._paper_export_call(
            self.paper_export_jobs.artifact_path, job_id, artifact_id
        )

    def paper_export_artifact_bytes(
        self, principal: Principal, job_id: str, artifact_id: str
    ) -> tuple[bytes, str, str]:
        self._require_teacher(principal)
        return self._paper_export_call(
            self.paper_export_jobs.artifact_bytes, job_id, artifact_id
        )

    @staticmethod
    def _presentation_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except PresentationWorkbenchError as exc:
            raise ApiError(
                exc.code,
                exc.message_zh,
                exc.status,
                dict(exc.details),
            ) from exc

    @staticmethod
    def _presentation_identity(principal: Principal) -> tuple[str, str]:
        # PPT projects are long-lived teacher artifacts. They intentionally do
        # not use the rotating launcher token digest as their session key.
        return principal.principal_id, "local-personal-presentation-v1"

    def _presentation_manager(self) -> PresentationJobManager:
        with self._presentation_lock:
            manager = self.presentation_jobs
            if manager is not None:
                return manager
            try:
                toolchain = PresentationToolchain.from_environment()
                manager = PresentationJobManager(
                    self.config.state_root / "presentation-workbench-v1",
                    toolchain,
                    max_workers=1,
                )
            except PresentationWorkbenchError as exc:
                raise ApiError(
                    exc.code,
                    exc.message_zh,
                    503,
                    dict(exc.details),
                ) from exc
            self.presentation_jobs = manager
            return manager

    @staticmethod
    def _presentation_outline_projection(value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version",
            "project_id",
            "revision",
            "deck_json",
            "candidate_only",
            "candidate_status",
            "teacher_review_status",
            "publication_allowed",
            "created_at",
            "updated_at",
            "idempotent_replay",
        }
        return {key: deepcopy(item) for key, item in value.items() if key in allowed}

    @staticmethod
    def _presentation_version_projection(value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "schema_version",
            "project_id",
            "version_id",
            "source_outline_revision",
            "immutable",
            "candidate_only",
            "candidate_status",
            "teacher_review_status",
            "publication_allowed",
            "created_at",
            "idempotent_replay",
        }
        return {key: deepcopy(item) for key, item in value.items() if key in allowed}

    @staticmethod
    def _presentation_theme_group(
        catalog: dict[str, Any], theme_id: str
    ) -> dict[str, Any]:
        groups = [
            group
            for paper in catalog.get("papers", [])
            if isinstance(paper, dict)
            for group in paper.get("theme_groups", [])
            if isinstance(group, dict)
            and isinstance(group.get("theme"), dict)
            and group["theme"].get("id") == theme_id
        ]
        if len(groups) != 1:
            raise ApiError(
                "presentation_theme_not_found"
                if not groups
                else "presentation_theme_ambiguous",
                "找不到这个完整主题，或当前题库中存在重复主题。",
                404 if not groups else 409,
            )
        return groups[0]

    def _presentation_detail(
        self, principal: Principal, scope: str, atomic_id: str
    ) -> dict[str, Any]:
        if scope == "wave1":
            return self.question_visual_scan_detail(principal, atomic_id)
        if scope == "supplemental":
            return self.supplemental_visual_scan_detail(principal, atomic_id)
        if scope == "master":
            try:
                return self.master_direct_visual_scan_detail(principal, atomic_id)
            except ApiError as exc:
                if exc.status == 404:
                    raise ApiError(
                        "presentation_theme_visual_scan_unavailable",
                        "这个主题仍有题目缺少可用于 PPT 的逐图记录，请先完成题目整理。",
                        409,
                    ) from exc
                raise
        raise ApiError(
            "presentation_theme_scope_invalid", "这个题库范围不能用于课程 PPT。", 400
        )

    def _presentation_crop(
        self,
        principal: Principal,
        scope: str,
        atomic_id: str,
        crop_id: str,
    ) -> CandidateCropPayload:
        if scope == "wave1":
            return self.candidate_review_question_crop(principal, atomic_id, crop_id)
        if scope == "supplemental":
            return self.supplemental_visual_scan_question_crop(
                principal, atomic_id, crop_id
            )
        if scope == "master":
            return self.master_direct_visual_scan_question_crop(
                principal, atomic_id, crop_id
            )
        raise ApiError(
            "presentation_theme_scope_invalid", "这个题库范围不能用于课程 PPT。", 400
        )

    def presentation_projects(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        projects = self._presentation_call(
            self._presentation_manager().list_projects,
            owner_id,
            session_id,
        )
        return {
            "schema_version": "shchem.presentation-project-list.v1",
            "projects": projects,
            "count": len(projects),
        }

    def presentation_create_from_theme(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        # Reject unknown fields, client paths, hashes, authority claims, and
        # malformed lesson settings before reading the question bank or
        # creating any mutable PPT project state.
        normalized_request = self._presentation_call(
            validate_theme_request, payload
        )
        scope = normalized_request["scope"]
        snapshot_id = normalized_request["data_snapshot_id"]
        theme_id = normalized_request["theme_id"]
        current_snapshot = self._paper_export_scope_snapshot_id(principal, scope)
        if snapshot_id != current_snapshot:
            raise ApiError(
                "presentation_theme_snapshot_stale",
                "题库版本已经变化，请刷新主题后再生成 PPT。",
                409,
            )
        catalog = self.theme_workbench_groups(principal, scope=scope)
        observed_catalog_snapshot = canonical_json_sha256(catalog)
        if observed_catalog_snapshot != current_snapshot:
            raise ApiError(
                "presentation_theme_catalog_snapshot_mismatch",
                "当前主题目录与活动题库快照不一致，请刷新后再生成 PPT。",
                409,
                details={
                    "expected_snapshot_id": current_snapshot,
                    "observed_snapshot_id": observed_catalog_snapshot,
                },
            )
        # The theme reader's canonical projection predates the explicit
        # snapshot field.  Bind a private copy only after its canonical bytes
        # have matched the active product snapshot; never mutate the reader's
        # cached value or trust a client-supplied binding.
        catalog = deepcopy(catalog)
        catalog["data_snapshot_id"] = current_snapshot
        group = self._presentation_theme_group(catalog, theme_id)
        chain = group.get("atomic_chain")
        if not isinstance(chain, list) or not chain:
            raise ApiError(
                "presentation_theme_catalog_invalid",
                "这个主题缺少完整题链，不能生成课程 PPT。",
                409,
            )
        atomic_ids = [
            row.get("atomic_part_id") for row in chain if isinstance(row, dict)
        ]
        if (
            len(atomic_ids) != len(chain)
            or any(not isinstance(value, str) or not value for value in atomic_ids)
            or len(set(atomic_ids)) != len(atomic_ids)
        ):
            raise ApiError(
                "presentation_theme_catalog_invalid",
                "这个主题的作答单元父链不完整。",
                409,
            )
        details = {
            atomic_id: self._presentation_detail(principal, scope, atomic_id)
            for atomic_id in atomic_ids
        }

        owner_id, session_id = self._presentation_identity(principal)
        manager = self._presentation_manager()
        project_holder: dict[str, dict[str, Any]] = {}

        def ensure_project() -> dict[str, Any]:
            project = project_holder.get("project")
            if project is None:
                project = manager.create_project(
                    owner_id,
                    session_id,
                    {
                        "title_zh": normalized_request["lesson_title"],
                        "description_zh": (
                            "完整主题："
                            f"{group.get('theme', {}).get('title', theme_id)}"
                        ),
                    },
                )
                project_holder["project"] = project
            return project

        def crop_loader(
            atomic_id: str, descriptor: dict[str, Any]
        ) -> dict[str, Any]:
            crop = self._presentation_crop(
                principal, scope, atomic_id, descriptor["crop_id"]
            )
            return {
                "data": crop.data,
                "content_type": crop.content_type,
                "sha256": crop.sha256,
            }

        def asset_store(filename: str, data: bytes) -> dict[str, Any]:
            project = ensure_project()
            return manager.store_asset(
                owner_id,
                session_id,
                project["project_id"],
                filename=filename,
                data=data,
            )

        asset_bindings = self._presentation_call(
            materialize_theme_assets,
            theme_id=theme_id,
            atomic_chain=chain,
            details_by_atomic=details,
            crop_loader=crop_loader,
            asset_store=asset_store,
        )
        project = self._presentation_call(ensure_project)
        presentation_input = self._presentation_call(
            build_presentation_input,
            normalized_request,
            theme_catalog=catalog,
            details_by_atomic=details,
            active_snapshot_id=current_snapshot,
            asset_bindings=asset_bindings,
        )
        outline = self._presentation_call(
            manager.create_outline,
            owner_id,
            session_id,
            project["project_id"],
            presentation_input,
        )
        return {
            "schema_version": "shchem.presentation-project-create.v1",
            "project": project,
            "outline": self._presentation_outline_projection(outline),
        }

    def presentation_project_get(
        self, principal: Principal, project_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        manager = self._presentation_manager()
        project = self._presentation_call(
            manager.get_project, owner_id, session_id, project_id
        )
        try:
            outline = manager.get_outline(owner_id, session_id, project_id)
        except PresentationWorkbenchError as exc:
            if exc.code != "presentation_outline_not_found":
                raise ApiError(
                    exc.code, exc.message_zh, exc.status, dict(exc.details)
                ) from exc
            outline = None
        versions = self._presentation_call(
            manager.list_versions, owner_id, session_id, project_id
        )
        return {
            "schema_version": "shchem.presentation-project-detail.v1",
            "project": project,
            "outline": (
                self._presentation_outline_projection(outline)
                if isinstance(outline, dict)
                else None
            ),
            "versions": [
                self._presentation_version_projection(value) for value in versions
            ],
        }

    def presentation_outline_get(
        self, principal: Principal, project_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        result = self._presentation_call(
            self._presentation_manager().get_outline,
            owner_id,
            session_id,
            project_id,
        )
        return self._presentation_outline_projection(result)

    def presentation_outline_update(
        self,
        principal: Principal,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if not isinstance(payload, dict) or set(payload) != {
            "expected_revision",
            "deck_json",
        }:
            raise ApiError(
                "presentation_outline_update_invalid",
                "页纲保存请求不完整。",
                400,
            )
        owner_id, session_id = self._presentation_identity(principal)
        result = self._presentation_call(
            self._presentation_manager().update_outline,
            owner_id,
            session_id,
            project_id,
            payload["deck_json"],
            expected_revision=payload["expected_revision"],
        )
        return self._presentation_outline_projection(result)

    def presentation_version_create(
        self,
        principal: Principal,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if not isinstance(payload, dict) or set(payload) != {
            "expected_outline_revision"
        }:
            raise ApiError(
                "presentation_version_request_invalid",
                "冻结 PPT 版本需要当前页纲修订号。",
                400,
            )
        owner_id, session_id = self._presentation_identity(principal)
        result = self._presentation_call(
            self._presentation_manager().create_version,
            owner_id,
            session_id,
            project_id,
            expected_outline_revision=payload["expected_outline_revision"],
        )
        return self._presentation_version_projection(result)

    def presentation_versions(
        self, principal: Principal, project_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        versions = self._presentation_call(
            self._presentation_manager().list_versions,
            owner_id,
            session_id,
            project_id,
        )
        projected = [
            self._presentation_version_projection(value) for value in versions
        ]
        return {
            "schema_version": "shchem.presentation-version-list.v1",
            "versions": projected,
            "count": len(projected),
        }

    def presentation_version_get(
        self,
        principal: Principal,
        project_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        result = self._presentation_call(
            self._presentation_manager().get_version,
            owner_id,
            session_id,
            project_id,
            version_id,
        )
        return self._presentation_version_projection(result)

    def presentation_render_start(
        self,
        principal: Principal,
        project_id: str,
        version_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if payload:
            raise ApiError(
                "presentation_render_request_invalid",
                "PPT 生成不接受客户端路径或额外参数。",
                400,
            )
        owner_id, session_id = self._presentation_identity(principal)
        return self._presentation_call(
            self._presentation_manager().start_render,
            owner_id,
            session_id,
            project_id,
            version_id,
        )

    def presentation_job_get(
        self, principal: Principal, job_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        return self._presentation_call(
            self._presentation_manager().get_job,
            owner_id,
            session_id,
            job_id,
        )

    def presentation_job_cancel(
        self, principal: Principal, job_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if payload:
            raise ApiError(
                "presentation_cancel_request_invalid", "取消请求必须为空对象。", 400
            )
        owner_id, session_id = self._presentation_identity(principal)
        return self._presentation_call(
            self._presentation_manager().cancel_job,
            owner_id,
            session_id,
            job_id,
        )

    def presentation_job_retry(
        self, principal: Principal, job_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if payload:
            raise ApiError(
                "presentation_retry_request_invalid", "重试请求必须为空对象。", 400
            )
        owner_id, session_id = self._presentation_identity(principal)
        return self._presentation_call(
            self._presentation_manager().retry_job,
            owner_id,
            session_id,
            job_id,
        )

    def presentation_artifact_bytes(
        self,
        principal: Principal,
        job_id: str,
        artifact_id: str,
    ) -> tuple[bytes, str, str]:
        self._require_teacher(principal)
        owner_id, session_id = self._presentation_identity(principal)
        return self._presentation_call(
            self._presentation_manager().artifact_bytes,
            owner_id,
            session_id,
            job_id,
            artifact_id,
        )

    @staticmethod
    def _model_provider_catalog() -> list[dict[str, Any]]:
        return [
            {
                "provider_kind": "preset",
                "provider_id": policy.provider_id,
                "display_name": policy.display_name or policy.provider_id,
                "base_url_policy": policy.base_url_policy,
                "base_url": policy.base_url,
                "api_style": policy.api_style,
                "catalog_role": "suggestions_only",
                "custom_model_ids_allowed": True,
                "models": [
                    {
                        "model_id": model.model_id,
                        "capabilities": list(model.capabilities),
                    }
                    for model in policy.models
                ],
                "follow_redirects": False,
            }
            for policy in DEFAULT_PROVIDER_POLICIES.values()
        ]

    def model_provider_settings_list(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        store = self.model_provider_settings
        if store is None:
            profiles: list[dict[str, Any]] = []
            available = False
            blocker = self.model_provider_settings_error
        else:
            profiles = self._model_provider_settings_call(store.list_metadata)
            available = True
            blocker = None
        return {
            "schema_version": "shchem.model-provider-settings-api.v1",
            "available": available,
            "profiles": profiles,
            "count": len(profiles),
            "provider_catalog": self._model_provider_catalog(),
            "credential_storage": {
                "kind": "windows_credential_manager_generic",
                "current_user": True,
                "roaming": False,
                "project_file_storage": False,
                "browser_storage": False,
                "secret_values_exposed": False,
            },
            "synthetic_probe": synthetic_probe_contract(),
            "offline_workbench_available": True,
            "production_model_invocation_enabled": False,
            "blockers": [blocker] if blocker else [],
        }

    def model_provider_settings_upsert(
        self,
        principal: Principal,
        profile_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_model_provider_settings_write(principal)
        legacy_allowed = {
            "provider_id",
            "model_id",
            "base_url_policy",
            "allowed_data_classes",
            "image_egress",
            "expected_revision",
        }
        extended_allowed = legacy_allowed | {
            "provider_kind",
            "display_name",
            "base_url",
            "api_style",
            "local_endpoint_policy",
            "capabilities",
            "max_input_tokens",
            "max_output_tokens",
        }
        provider_kind = payload.get("provider_kind", "preset")
        custom_required = {
            "provider_kind",
            "display_name",
            "base_url",
            "api_style",
            "model_id",
            "capabilities",
            "allowed_data_classes",
            "image_egress",
            "expected_revision",
        }
        required = custom_required if provider_kind == "openai_compatible" else legacy_allowed
        if not set(payload).issubset(extended_allowed) or not required.issubset(payload):
            raise ApiError(
                "model_provider_profile_request_invalid",
                "model-provider profile request fields are invalid",
                400,
            )
        if provider_kind == "preset" and set(payload) & {
            "base_url",
            "api_style",
            "local_endpoint_policy",
        }:
            raise ApiError(
                "model_provider_profile_request_invalid",
                "preset provider endpoint fields cannot be overridden",
                400,
            )
        profile = {
            "profile_id": profile_id,
            "provider_kind": provider_kind,
            "provider_id": payload.get("provider_id"),
            "display_name": payload.get("display_name"),
            "model_id": payload["model_id"],
            "base_url_policy": payload.get("base_url_policy"),
            "base_url": payload.get("base_url"),
            "api_style": payload.get("api_style"),
            "local_endpoint_policy": payload.get("local_endpoint_policy", "deny"),
            "capabilities": payload.get("capabilities"),
            "allowed_data_classes": payload["allowed_data_classes"],
            "image_egress": payload["image_egress"],
            "last_probe": None,
            "max_input_tokens": payload.get("max_input_tokens"),
            "max_output_tokens": payload.get("max_output_tokens"),
        }
        self._cancel_model_provider_probe_for_mutation(
            profile_id, expected_revision=payload["expected_revision"]
        )
        result = self._model_provider_settings_call(
            self._model_provider_store().upsert_metadata,
            profile,
            expected_revision=payload["expected_revision"],
        )
        return {"profile": result, "secret_received": False}

    def model_provider_models_probe(
        self,
        principal: Principal,
        profile_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Explicitly query `/models`; failure never changes the saved profile."""

        self._require_model_provider_synthetic_probe_execute(principal)
        if set(payload) != {"expected_revision"}:
            raise ApiError(
                "model_provider_models_request_invalid",
                "provider model-list request fields are invalid",
                400,
            )
        return self._model_provider_probe_call(
            self._model_provider_model_list_client().probe,
            profile_id,
            expected_revision=payload["expected_revision"],
        )

    def model_provider_credential_put(
        self,
        principal: Principal,
        profile_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_model_provider_settings_write(principal)
        if set(payload) != {"api_key", "expected_revision"}:
            raise ApiError(
                "model_provider_credential_request_invalid",
                "credential request fields are invalid",
                400,
            )
        self._cancel_model_provider_probe_for_mutation(
            profile_id, expected_revision=payload["expected_revision"]
        )
        result = self._model_provider_settings_call(
            self._model_provider_store().put_credential,
            profile_id,
            payload["api_key"],
            expected_revision=payload["expected_revision"],
        )
        return {
            "profile": result,
            "secret_received": True,
            "secret_exposed": False,
        }

    def model_provider_credential_delete(
        self,
        principal: Principal,
        profile_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_model_provider_settings_write(principal)
        if set(payload) != {"expected_revision"}:
            raise ApiError(
                "model_provider_credential_request_invalid",
                "credential deletion fields are invalid",
                400,
            )
        self._cancel_model_provider_probe_for_mutation(
            profile_id, expected_revision=payload["expected_revision"]
        )
        result = self._model_provider_settings_call(
            self._model_provider_store().delete_credential,
            profile_id,
            expected_revision=payload["expected_revision"],
        )
        return {"profile": result, "secret_deleted": True}

    def model_provider_synthetic_probe_start(
        self, principal: Principal, profile_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_model_provider_synthetic_probe_execute(principal)
        if set(payload) != {"expected_revision", "idempotency_key"}:
            raise ApiError(
                "model_provider_probe_request_invalid",
                "synthetic provider probe request fields are invalid",
                400,
            )
        result = self._model_provider_probe_call(
            self._model_provider_probe_manager().start,
            profile_id,
            expected_revision=payload["expected_revision"],
            idempotency_key=payload["idempotency_key"],
        )
        result["_http_status"] = 202
        return result

    def model_provider_synthetic_probe_get(
        self, principal: Principal, profile_id: str, probe_run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        result = self._model_provider_probe_call(
            self._model_provider_probe_manager().get, probe_run_id
        )
        if result.get("profile_id") != profile_id:
            raise ApiError(
                "probe_not_found", "synthetic probe was not found", 404
            )
        return result

    def model_provider_synthetic_probe_cancel(
        self,
        principal: Principal,
        profile_id: str,
        probe_run_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_model_provider_synthetic_probe_execute(principal)
        if set(payload) != {"expected_revision"}:
            raise ApiError(
                "model_provider_probe_request_invalid",
                "synthetic provider probe cancellation fields are invalid",
                400,
            )
        # Prove that the supplied revision is still the current profile
        # revision before accepting cancellation.  The manager then binds the
        # same revision to the selected run.
        self._model_provider_settings_call(
            self._model_provider_store().invocation_policy,
            profile_id,
            expected_revision=payload["expected_revision"],
        )
        result = self._model_provider_probe_call(
            self._model_provider_probe_manager().get, probe_run_id
        )
        if result.get("profile_id") != profile_id:
            raise ApiError(
                "probe_not_found", "synthetic probe was not found", 404
            )
        return self._model_provider_probe_call(
            self._model_provider_probe_manager().cancel,
            probe_run_id,
            expected_revision=payload["expected_revision"],
        )

    @staticmethod
    def _supplemental_visual_scan_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any] | CandidateCropPayload:
        try:
            return operation(*args, **kwargs)
        except SupplementalVisualScanError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _supplemental_wechat_tagging_overlay_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            result = operation(*args, **kwargs)
        except SupplementalWechatTaggingOverlayError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc
        if not isinstance(result, dict):
            raise ApiError(
                "supplemental_tagging_projection_invalid",
                "supplemental WeChat tagging overlay returned a malformed projection",
                409,
            )
        return result

    def _merge_supplemental_wechat_tagging_overlay(
        self, result: dict[str, Any], node_id: str
    ) -> dict[str, Any]:
        node = result.get("node")
        if not isinstance(node, dict) or node.get("node_id") != node_id:
            raise ApiError(
                "supplemental_tagging_base_detail_invalid",
                "supplemental detail cannot be bound to the tagging overlay",
                409,
            )
        source_kind = node.get("source_kind")
        if source_kind == "external_teaching_handout":
            return result
        if source_kind != "shanghai_exam_wechat_archive":
            raise ApiError(
                "supplemental_tagging_base_partition_invalid",
                "supplemental detail has an unsupported source partition",
                409,
            )
        if (
            result.get("data_snapshot_id")
            != SUPPLEMENTAL_TAGGING_BASE_REGISTRY_FILE_SHA256
        ):
            raise ApiError(
                "supplemental_tagging_base_registry_invalid",
                "supplemental detail and tagging overlay bind different base snapshots",
                409,
            )
        overlay = self._supplemental_wechat_tagging_overlay_call(
            self.supplemental_wechat_tagging_overlay.detail,
            node_id,
        )
        source_layer = overlay.get("source_layer")
        if (
            overlay.get("node_id") != node_id
            or overlay.get("scope") != SUPPLEMENTAL_TAGGING_OVERLAY_SCOPE
            or not isinstance(source_layer, dict)
            or not isinstance(source_layer.get("evidence_level"), str)
            or not source_layer["evidence_level"]
        ):
            raise ApiError(
                "supplemental_tagging_projection_invalid",
                "supplemental WeChat tagging overlay identity or source layer drifted",
                409,
            )
        node["source_layer"] = source_layer["evidence_level"]
        node["tagging_overlay"] = overlay
        return result

    def supplemental_visual_scan_status(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/supplemental-scans/status",
            )
        result = self._supplemental_visual_scan_call(
            self.supplemental_visual_scans.status
        )
        assert isinstance(result, dict)
        return result

    def supplemental_visual_scan_list(
        self, principal: Principal, *, limit: int, offset: int
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.catalog_page,
                "supplemental",
                limit=limit,
                offset=offset,
            )
        result = self._supplemental_visual_scan_call(
            self.supplemental_visual_scans.list_atomic,
            limit=limit,
            offset=offset,
        )
        assert isinstance(result, dict)
        return result

    def supplemental_visual_scan_detail(
        self, principal: Principal, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/supplemental-scans/"
                f"{self._frozen_route_component(node_id)}"
            )
            result = self._frozen_browse_call(self.frozen_browse.json, route)
        else:
            result = self._supplemental_visual_scan_call(
                self.supplemental_visual_scans.detail, node_id
            )
        assert isinstance(result, dict)
        return self._merge_supplemental_wechat_tagging_overlay(result, node_id)

    def supplemental_visual_scan_question_crop(
        self, principal: Principal, node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/supplemental-scans/"
                f"{self._frozen_route_component(node_id)}/question-crops/"
                f"{self._frozen_route_component(crop_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.crop, route)
        result = self._supplemental_visual_scan_call(
            self.supplemental_visual_scans.question_crop, node_id, crop_id
        )
        assert isinstance(result, CandidateCropPayload)
        return result

    @staticmethod
    def _master_direct_visual_scan_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any] | CandidateCropPayload:
        try:
            return operation(*args, **kwargs)
        except MasterDirectVisualScanError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def master_direct_visual_scan_status(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/master-direct-scans/status",
            )
        result = self._master_direct_visual_scan_call(
            self.master_direct_visual_scans.status
        )
        assert isinstance(result, dict)
        return result

    def master_direct_visual_scan_catalog(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/master-direct-scans/catalog",
            )
        result = self._master_direct_visual_scan_call(
            self.master_direct_visual_scans.catalog
        )
        assert isinstance(result, dict)
        return result

    def master_direct_visual_scan_detail(
        self, principal: Principal, master_node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/master-direct-scans/"
                f"{self._frozen_route_component(master_node_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        result = self._master_direct_visual_scan_call(
            self.master_direct_visual_scans.detail, master_node_id
        )
        assert isinstance(result, dict)
        return result

    def master_direct_visual_scan_question_crop(
        self, principal: Principal, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/master-direct-scans/"
                f"{self._frozen_route_component(master_node_id)}/question-crops/"
                f"{self._frozen_route_component(crop_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.crop, route)
        result = self._master_direct_visual_scan_call(
            self.master_direct_visual_scans.question_crop,
            master_node_id,
            crop_id,
        )
        assert isinstance(result, CandidateCropPayload)
        return result

    @staticmethod
    def _master_visual_scan_alias_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any] | CandidateCropPayload:
        try:
            return operation(*args, **kwargs)
        except MasterVisualScanAliasError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def _master_visual_scan_alias_reader(self) -> MasterVisualScanAliasReader:
        try:
            return MasterVisualScanAliasReader(
                self.config.shchem_root,
                master_workbench=self.master_wave1_workbench,
            )
        except MasterVisualScanAliasError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _combined_master_visual_scan_coverage(
        *,
        master_atomic_inventory: int,
        wave1_exact_visual_scanned: int,
        direct_master_visual_scanned: int,
        alias_existing_visual_scanned: int,
    ) -> dict[str, int]:
        visual_scanned_master_atomic = (
            wave1_exact_visual_scanned
            + direct_master_visual_scanned
            + alias_existing_visual_scanned
        )
        return {
            "master_atomic_inventory": master_atomic_inventory,
            "wave1_exact_visual_scanned": wave1_exact_visual_scanned,
            "direct_master_visual_scanned": direct_master_visual_scanned,
            "alias_existing_visual_scanned": alias_existing_visual_scanned,
            "visual_scanned_master_atomic": visual_scanned_master_atomic,
            "remaining_unscanned": (
                master_atomic_inventory - visual_scanned_master_atomic
            ),
            "direct_exact_overlap": 0,
            "alias_overlap_with_exact_or_direct": 0,
        }

    def _master_visual_scan_coverage_context(self) -> dict[str, Any]:
        alias_reader = self._master_visual_scan_alias_reader()
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shchem-visible-layer"
        ) as executor:
            direct_future = executor.submit(
                self._master_direct_visual_scan_call,
                self.master_direct_visual_scans.catalog,
            )
            alias_future = executor.submit(
                self._master_visual_scan_alias_call,
                alias_reader.catalog,
            )
            direct = direct_future.result()
            alias = alias_future.result()
        assert isinstance(direct, dict)
        assert isinstance(alias, dict)
        direct_ids = frozenset(direct.get("master_node_ids", ()))
        alias_items = {
            item.get("master_node_id"): item
            for item in alias.get("items", ())
            if isinstance(item, dict)
        }
        alias_ids = frozenset(alias_items)
        direct_count = direct.get("count")
        direct_coverage = direct.get("coverage")
        direct_integrity = direct.get("integrity")
        alias_count = alias.get("count")
        if (
            type(direct_count) is not int
            or not isinstance(direct_coverage, dict)
            or not isinstance(direct_integrity, dict)
            or type(alias_count) is not int
        ):
            raise ApiError(
                "master_visual_scan_coverage_invalid",
                "exact, direct, and alias visual-scan coverage failed closed",
                409,
            )
        master_atomic_inventory = direct_coverage.get("master_atomic_inventory")
        wave1_exact_visual_scanned = direct_coverage.get(
            "wave1_exact_visual_scanned"
        )
        direct_product_count = direct_integrity.get("batch_count")
        if (
            type(master_atomic_inventory) is not int
            or type(wave1_exact_visual_scanned) is not int
            or type(direct_product_count) is not int
        ):
            raise ApiError(
                "master_visual_scan_coverage_invalid",
                "exact, direct, and alias visual-scan coverage failed closed",
                409,
            )
        expected_direct_coverage = {
            "master_atomic_inventory": master_atomic_inventory,
            "wave1_exact_visual_scanned": wave1_exact_visual_scanned,
            "direct_master_visual_scanned": direct_count,
            "visual_scanned_master_atomic": (
                wave1_exact_visual_scanned + direct_count
            ),
            "remaining_unscanned": (
                master_atomic_inventory
                - wave1_exact_visual_scanned
                - direct_count
            ),
            "direct_exact_overlap": 0,
        }
        combined_coverage = self._combined_master_visual_scan_coverage(
            master_atomic_inventory=master_atomic_inventory,
            wave1_exact_visual_scanned=wave1_exact_visual_scanned,
            direct_master_visual_scanned=direct_count,
            alias_existing_visual_scanned=alias_count,
        )
        if (
            direct_count <= 0
            or len(direct_ids) != direct_count
            or direct.get("coverage") != expected_direct_coverage
            or direct_product_count <= 0
            or len(direct.get("products", ())) != direct_product_count
            or alias_count <= 0
            or len(alias_ids) != alias_count
            or alias.get("coverage_kind") != MASTER_ALIAS_COVERAGE_KIND
            or alias.get("new_physical_scans") != 0
            or direct_ids & alias_ids
            or combined_coverage["remaining_unscanned"] < 0
        ):
            raise ApiError(
                "master_visual_scan_coverage_invalid",
                "exact, direct, and alias visual-scan coverage failed closed",
                409,
            )
        return {
            "direct_ids": direct_ids,
            "alias_items": alias_items,
            "coverage": combined_coverage,
        }

    @staticmethod
    def _decorate_master_visual_scan_item(
        item: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        node_id = item.get("node_id")
        alias_item = context["alias_items"].get(node_id)
        if alias_item is not None:
            coverage = {
                "coverage_kind": MASTER_ALIAS_COVERAGE_KIND,
                "detail_available": True,
                "new_physical_scans": 0,
                "target_atomic_count": alias_item["target_atomic_count"],
            }
        elif node_id in context["direct_ids"]:
            coverage = {
                "coverage_kind": "direct_new_visual_scan",
                "detail_available": True,
                "new_physical_scans": 1,
                "target_atomic_count": 1,
            }
        elif item.get("crosswalk_summary", {}).get("state") == "exact":
            coverage = {
                "coverage_kind": "exact_existing_visual_scan",
                "detail_available": True,
                "new_physical_scans": 0,
                "target_atomic_count": 1,
            }
        else:
            coverage = {
                "coverage_kind": "unscanned",
                "detail_available": False,
                "new_physical_scans": 0,
                "target_atomic_count": 0,
            }
        item["visual_scan_coverage"] = coverage
        return item

    def master_visual_scan_alias_status(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/master-visual-scan-aliases/status",
            )
        alias_reader = self._master_visual_scan_alias_reader()
        result = self._master_visual_scan_alias_call(
            alias_reader.status
        )
        assert isinstance(result, dict)
        return result

    def master_visual_scan_alias_catalog(
        self, principal: Principal
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            return self._frozen_browse_call(
                self.frozen_browse.json,
                "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
            )
        alias_reader = self._master_visual_scan_alias_reader()
        result = self._master_visual_scan_alias_call(
            alias_reader.catalog
        )
        assert isinstance(result, dict)
        return result

    def master_visual_scan_alias_detail(
        self, principal: Principal, master_node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/master-visual-scan-aliases/"
                f"{self._frozen_route_component(master_node_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.json, route)
        alias_reader = self._master_visual_scan_alias_reader()
        result = self._master_visual_scan_alias_call(
            alias_reader.detail, master_node_id
        )
        assert isinstance(result, dict)
        return result

    def master_visual_scan_alias_question_crop(
        self, principal: Principal, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        self._require_teacher(principal)
        if self.frozen_browse is not None:
            route = (
                "/api/v1/kb/workbench/master-visual-scan-aliases/"
                f"{self._frozen_route_component(master_node_id)}/question-crops/"
                f"{self._frozen_route_component(crop_id)}"
            )
            return self._frozen_browse_call(self.frozen_browse.crop, route)
        alias_reader = self._master_visual_scan_alias_reader()
        result = self._master_visual_scan_alias_call(
            alias_reader.question_crop,
            master_node_id,
            crop_id,
        )
        assert isinstance(result, CandidateCropPayload)
        return result

    def kb_search(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.public_kb.search, payload)

    def retrieval_status(self, principal: Principal) -> dict[str, Any]:
        self._require_kb_retrieval_read(principal)
        return self.retrieval_workbench.status(capability_granted=True)

    def retrieval_search(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_kb_retrieval_read(principal)
        try:
            return self.retrieval_workbench.search(payload)
        except RetrievalWorkbenchError as exc:
            details = dict(exc.details)
            details["retrieval_boundary"] = {
                "read_only": True,
                "snapshot_verified": False,
                "content_exposed": False,
                "source_file_exposed": False,
                "human_reviewed": False,
            }
            raise ApiError(exc.code, str(exc), exc.status, details) from exc

    def hierarchy_node(
        self, principal: Principal, node_type: str, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        result = self._readonly_call(self.public_kb.node, node_type, node_id)
        if node_type == "atomic_part":
            try:
                result["tag_patch_candidate_context"] = (
                    self.tag_patch_workbench.current_node_context(node_type, node_id)
                )
            except TagPatchGatewayError as exc:
                result["tag_patch_candidate_context"] = {
                    "patchable": False,
                    "blocker": exc.code,
                    "candidate_status": "pending_human_review_not_applied",
                    "candidate_only": True,
                    "human_reviewed": False,
                    "retrieval_ready": False,
                    "generation_allowed": False,
                    "publication_allowed": False,
                    "official": False,
                    "teaching_use_allowed": False,
                }
        return result

    def hierarchy_children(
        self,
        principal: Principal,
        node_type: str,
        node_id: str,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(
            self.public_kb.children, node_type, node_id, limit, offset
        )

    def hierarchy_evidence(
        self, principal: Principal, node_type: str, node_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.public_kb.evidence, node_type, node_id)

    def review_queue(
        self,
        principal: Principal,
        *,
        limit: int,
        offset: int,
        level: str | None,
        state: str | None,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if level is not None and level not in {
            "paper",
            "theme_big_question",
            "printed_question",
            "atomic_part",
            "question_anchor",
        }:
            raise ApiError("invalid_review_level", "unsupported review queue level")
        if state is not None and (
            len(state) > 64 or re.fullmatch(r"[A-Za-z0-9_.-]+", state) is None
        ):
            raise ApiError("invalid_review_state", "invalid review queue state")
        return self._readonly_call(
            self.public_kb.review_queue,
            limit=limit,
            offset=offset,
            level=level,
            state=state,
        )

    def generation_run_list(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.generation_runs.list_runs)

    def generation_run_current(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.generation_runs.current)

    def generation_run_governance(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.r18_governance.current)

    def generation_run_get(self, principal: Principal, run_id: str) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.generation_runs.get, run_id)

    def generation_run_artifacts(
        self, principal: Principal, run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._readonly_call(self.generation_runs.artifacts, run_id)

    def workbench_task_create(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.create_task(payload)

    def workbench_task_list(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.list_tasks()

    def workbench_task_get(
        self, principal: Principal, task_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.get_task(task_id)

    def workbench_task_freeze(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.freeze_task(task_id, payload)

    def workbench_plan_run_create(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.create_plan_run(task_id, payload)

    def workbench_plan_run_get(
        self, principal: Principal, run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.get_run(run_id)

    def workbench_plan_run_events(
        self, principal: Principal, run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.get_events(run_id)

    def workbench_plan_run_artifacts(
        self, principal: Principal, run_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self.generation_workbench.get_artifacts(run_id)

    @staticmethod
    def _tag_patch_call(operation: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except TagPatchGatewayError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def tag_patch_create(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_tag_patch_write(principal)
        return self._tag_patch_call(self.tag_patch_workbench.create, payload)

    def tag_patch_list(
        self,
        principal: Principal,
        *,
        node_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._tag_patch_call(
            self.tag_patch_workbench.list,
            node_id=node_id,
            limit=limit,
            offset=offset,
        )

    def tag_patch_get(
        self, principal: Principal, patch_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._tag_patch_call(self.tag_patch_workbench.get, patch_id)

    @staticmethod
    def _theme_review_call(operation: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return operation(*args, **kwargs)
        except ThemeReviewGatewayError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def theme_review_task_list(
        self,
        principal: Principal,
        *,
        task_kind: str | None,
        paper_id: str | None,
        state: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        gateway = self._theme_review_gateway()
        return self._theme_review_call(
            gateway.list,
            task_kind=task_kind,
            paper_id=paper_id,
            state=state,
            limit=limit,
            offset=offset,
        )

    def theme_review_task_get(
        self, principal: Principal, task_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._theme_review_call(self._theme_review_gateway().get, task_id)

    def theme_review_task_claim(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_review_task_write(principal)
        return self._theme_review_call(
            self._theme_review_gateway().claim,
            task_id,
            payload,
            principal_id=principal.principal_id,
        )

    def theme_review_task_release(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_review_task_write(principal)
        return self._theme_review_call(
            self._theme_review_gateway().release,
            task_id,
            payload,
            principal_id=principal.principal_id,
        )

    def theme_review_change_set_create(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_review_candidate_write(principal)
        return self._theme_review_call(
            self._theme_review_gateway().change_set,
            task_id,
            payload,
            principal_id=principal.principal_id,
        )

    def theme_review_change_set_preview(
        self, principal: Principal, task_id: str, change_set_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        return self._theme_review_call(
            self._theme_review_gateway().preview, task_id, change_set_id
        )

    def theme_review_decision_create(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_review_decision_write(principal)
        return self._theme_review_call(
            self._theme_review_gateway().decision,
            task_id,
            payload,
            principal_id=principal.principal_id,
        )

    def dashboard_status(self, principal: Principal) -> dict[str, Any]:
        self._require_teacher(principal)
        live = self.status()
        try:
            validation = self.validate()
        except ApiError as exc:
            validation = {
                "valid": False,
                "ok": False,
                "errors": [exc.code],
                "live_controller_output": False,
            }
        controller_status = live.get("controller_status", {})
        if not isinstance(controller_status, dict):
            controller_status = {}
        registry = {
            key: controller_status.get(key)
            for key in (
                "candidate_package_registry_records",
                "candidate_registry_actual_content_packages",
                "formal_question_records",
                "formal_human_reviewed_records",
                "machine_governance_chain_count",
                "machine_governance_valid_chain_count",
            )
        }
        publication_blockers = [
            "human_review_pending",
            "formal_freeze_not_authorized",
            "external_release_interface_closed",
        ]
        if controller_status.get("external_publication_allowed") is True:
            publication_blockers.append("controller_publication_overclaim_rejected")
        generation_status = live.get("generation_v2", {})
        student_status = live.get("student_learning", {})
        provider = (
            student_status.get("formal_content_provider", {})
            if isinstance(student_status, dict)
            else {}
        )
        result = {
            "controller": {
                "available": live.get("controller", {}).get("available") is True,
                "live_status": live.get("controller", {}).get("live_status"),
                "contract_version": controller_status.get("contract_version"),
                "ok": controller_status.get("ok") is True,
            },
            "validation": {
                "valid": validation.get("valid") is True,
                "ok": validation.get("ok") is True,
                "warnings": list(validation.get("warnings", [])),
                "errors": list(validation.get("errors", [])),
                "live_controller_output": validation.get("live_controller_output") is True,
            },
            "registry": registry,
            "provider": {
                "generation": generation_status,
                "formal_content": provider,
            },
            "generation_workbench": {
                "scope": "task_card_freeze_preflight_plan_only",
                "teacher_only": True,
                "append_only": True,
                "default_live_tasks_created": False,
                "actual_generation_endpoint_present": False,
                "model_invocation_present": False,
                "download_endpoint_present": False,
                "external_publication_allowed": False,
                "official_claim_allowed": False,
                "human_reviewed": False,
            },
            "tag_patch_workbench": {
                "scope": "atomic_part_candidate_patch_create_list_get_only",
                "teacher_only": True,
                "write_capability_required": TAG_PATCH_WRITE_CAPABILITY,
                "write_capability_granted": TAG_PATCH_WRITE_CAPABILITY
                in principal.capabilities,
                "candidate_only": True,
                "human_reviewed": False,
                "retrieval_ready": False,
                "generation_allowed": False,
                "publication_allowed": False,
                "official": False,
                "teaching_use_allowed": False,
                "apply_endpoint_present": False,
                "human_approve_endpoint_present": False,
            },
            "theme_review_workbench": {
                "scope": "whole_theme_candidate_review_workflow",
                "task_unit_default": "theme_big_question",
                "boundary_review_unit_present": True,
                "teacher_only": True,
                "task_write_capability_granted": "review_task_write"
                in principal.capabilities,
                "candidate_write_capability_granted": "review_candidate_write"
                in principal.capabilities,
                "decision_write_capability_granted": "review_decision_write"
                in principal.capabilities,
                "append_only": True,
                "candidate_overlay_preview_only": True,
                "central_master_mutated": False,
                "human_reviewed": False,
                "retrieval_ready": False,
                "teaching_use_allowed": False,
                "generation_allowed": False,
                "publication_allowed": False,
                "official": False,
            },
            "retrieval_workbench": self.retrieval_workbench.status(
                capability_granted=KB_RETRIEVAL_READ_CAPABILITY
                in principal.capabilities
            ),
            "publication": {
                "status": "blocked",
                "external_publication_allowed": False,
                "official_claim_allowed": False,
                "human_reviewed": False,
                "blockers": publication_blockers,
            },
            "labels": {
                "candidate": "候选",
                "machine_pass": "机器通过（不等于发布）",
                "human_review_pending": "人工复核待完成",
                "release": "发布（当前关闭）",
            },
            "dashboard_response_read_only": True,
            "state_mutating_endpoints_present": True,
            "state_mutating_endpoint_scopes": [
                "generation_task_card_candidate",
                "generation_task_card_freeze",
                "generation_plan_run",
                "tag_patch_candidate",
                "theme_review_task_claim_release",
                "theme_review_candidate_change_set",
                "theme_review_teacher_decision_record",
                "student_upload",
                "candidate_job",
                "student_diagnosis",
                "student_handout_candidate",
            ],
            "privilege_escalation_endpoints_present": False,
            "formal_freeze_endpoints_present": False,
            "register_endpoints_present": False,
            "promote_endpoints_present": False,
            "publish_endpoints_present": False,
            "tag_edit_endpoints_present": False,
            "tag_candidate_patch_endpoints_present": True,
            "kb_retrieval_read_endpoint_present": True,
            "authority_changing_actions": [],
        }
        projected = _public_status_projection(
            result, self.config.shchem_root.absolute().parent
        )
        if not isinstance(projected, dict):
            raise ApiError(
                "public_status_projection_failed",
                "dashboard status could not be safely projected",
                503,
            )
        projected["public_projection"] = {
            "absolute_host_paths_removed": True,
            "private_path_markers_removed": True,
            "path_fields_removed": True,
        }
        return projected

    @staticmethod
    def _student_visual_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except StudentVisualAnalysisError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    @staticmethod
    def _student_recommendation_call(
        operation: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return operation(*args, **kwargs)
        except StudentRecommendationWorkbenchError as exc:
            raise ApiError(exc.code, str(exc), exc.status) from exc

    def create_student_profile(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if "*" not in principal.students:
            raise SecurityError(
                "student_profile_create_scope_required",
                "teacher principal cannot create additional student profiles",
                403,
            )
        return self._student_visual_call(
            self.student_visual_analysis.create_student, payload
        )

    def submission_create(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        if not isinstance(payload, dict) or set(payload) - {
            "student_id",
            "workflow",
            "assignment_id",
        }:
            raise ApiError("submission_invalid", "submission request is invalid")
        student_id = payload.get("student_id")
        if not isinstance(student_id, str):
            raise ApiError("student_id_required", "student_id is required")
        authorize_student(principal, student_id)
        manager_payload = dict(payload)
        manager_payload.pop("student_id", None)
        return self._student_visual_call(
            self.student_visual_analysis.create_submission,
            student_id,
            manager_payload,
        )

    def _submission_student(
        self, principal: Principal, submission_id: str
    ) -> str:
        student_id = self._student_visual_call(
            self.student_visual_analysis.submission_owner, submission_id
        )
        authorize_student(principal, student_id)
        return str(student_id)

    def submission_file_register(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.register_file,
            student_id,
            submission_id,
            payload,
        )

    def submission_file_upload(
        self,
        principal: Principal,
        submission_id: str,
        file_id: str,
        data: bytes,
        *,
        content_type: str,
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.upload_file_content,
            student_id,
            submission_id,
            file_id,
            data,
            content_type=content_type,
        )

    def submission_get(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.get_submission, student_id, submission_id
        )

    def submission_matching_get(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.get_matching, student_id, submission_id
        )

    def submission_matching_update(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.update_matching,
            student_id,
            submission_id,
            payload,
        )

    def submission_privacy_decision(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.record_privacy_decision,
            student_id,
            submission_id,
            payload,
        )

    def submission_analyze(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.analyze,
            student_id,
            submission_id,
            payload,
        )

    def submission_cancel(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.cancel,
            student_id,
            submission_id,
            payload,
        )

    def submission_analysis_get(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.get_analysis, student_id, submission_id
        )

    def submission_review_get(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.get_review, student_id, submission_id
        )

    def submission_scoring_decision(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.append_scoring_decision,
            student_id,
            submission_id,
            payload,
        )

    def submission_diagnostic_decision(
        self,
        principal: Principal,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one teacher-confirmed, textbook-bound diagnostic decision."""

        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.append_diagnostic_decision,
            student_id,
            submission_id,
            payload,
        )

    def submission_diagnostic_review_get(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        """Read the append-only diagnostic chain without creating mastery state."""

        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        return self._student_visual_call(
            self.student_visual_analysis.get_diagnostic_review,
            student_id,
            submission_id,
        )

    def submission_recommendation_preview(
        self, principal: Principal, submission_id: str
    ) -> dict[str, Any]:
        """Derive a read-only, complete-theme recommendation preview.

        Only current-score-bound teacher diagnostic decisions can contribute
        weakness evidence. The preview never writes an attempt,
        mastery record, recommendation record, or question-bank mutation.
        """

        self._require_teacher(principal)
        student_id = self._submission_student(principal, submission_id)
        diagnostic = self._student_visual_call(
            self.student_visual_analysis.get_diagnostic_review,
            student_id,
            submission_id,
        )
        review = self._student_visual_call(
            self.student_visual_analysis.get_review,
            student_id,
            submission_id,
        )
        catalog = self.textbook_catalog(principal)
        release_snapshot_id: str | None = None
        release_runtime = self.config.workbench_release_runtime
        if release_runtime is not None:
            candidate_snapshot = release_runtime.serving.data_snapshot_id
            if isinstance(candidate_snapshot, str):
                release_snapshot_id = candidate_snapshot
        data_snapshot_id = canonical_json_sha256(
            {
                "curriculum_snapshot_id": catalog.get("data_snapshot_id"),
                "release_snapshot_id": release_snapshot_id,
                "curriculum_counts": catalog.get("counts"),
            }
        )

        payload = self._student_recommendation_call(
            project_visual_recommendation_payload,
            submission_id=submission_id,
            review=review,
            diagnostic=diagnostic,
            data_snapshot_id=data_snapshot_id,
            scopes=("master", "wave1", "supplemental"),
            limit_per_section=3,
        )

        search_cache: dict[str, dict[str, Any]] = {}

        def search_current_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
            requested_scope = payload.get("scope")
            curriculum = payload.get("curriculum")
            cache_key = json.dumps(
                curriculum, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            try:
                if cache_key not in search_cache:
                    combined_payload = dict(payload)
                    combined_payload["scope"] = "all"
                    combined_payload["limit"] = 50
                    search_cache[cache_key] = self.question_search_workbench.search(
                        combined_payload,
                        theme_loader=lambda scope: self.theme_workbench_groups(
                            principal, scope=scope
                        ),
                        curriculum_loader=self._curriculum_search,
                        snapshot_id=data_snapshot_id,
                    )
                combined = search_cache[cache_key]
                projected = dict(combined)
                projected["scope"] = requested_scope
                projected["items"] = [
                    item
                    for item in combined.get("items", [])
                    if isinstance(item, dict) and item.get("scope") == requested_scope
                ]
                return projected
            except QuestionSearchError as exc:
                raise StudentRecommendationWorkbenchError(
                    exc.code, str(exc), exc.status
                ) from exc

        preview = self._student_recommendation_call(
            self.student_recommendation_workbench.preview,
            payload,
            curriculum_catalog_loader=lambda: catalog,
            question_search_loader=search_current_snapshot,
        )
        preview["student_id"] = student_id
        preview["submission_revision"] = diagnostic.get("revision")
        preview["diagnostic_chain_head_sha256"] = diagnostic.get(
            "chain_head_sha256"
        )
        decisions = payload["scoring_decisions"]
        preview["diagnostic_review_status"] = (
            "teacher_decisions_recorded"
            if len(decisions) == len(payload["matches"])
            and all(item["decision"] != "pending" for item in decisions)
            else "pending"
        )
        preview["mastery_written"] = False
        preview["recommendation_written"] = False
        return preview

    def list_students(self, principal: Principal) -> dict[str, Any]:
        visual_manager = getattr(self, "student_visual_analysis", None)
        visual_profiles = (
            self._student_visual_call(visual_manager.list_students)
            if visual_manager is not None
            else []
        )
        visual_ids = [str(item["student_id"]) for item in visual_profiles]
        configured = list(dict.fromkeys([*self.config.students, *visual_ids]))
        if principal.role == "teacher" and "*" in principal.students:
            allowed = configured
        else:
            allowed = [
                value for value in configured if value in principal.students
            ]
        bridge_status = self.student_bridge.status()
        blockers: list[str] = []
        if not allowed:
            blockers.append("no_authorized_student_profiles")
        if bridge_status.get("available") is not True and not visual_ids:
            blockers.append("student_domain_bridge_unavailable")
        return {
            "students": allowed,
            "count": len(allowed),
            "workflow": {
                "ready": not blockers,
                "student_bridge_available": bridge_status.get("available") is True,
                "student_visual_analysis_available": visual_manager is not None,
                "anonymous_profile_create_available": (
                    visual_manager is not None
                    and principal.role == "teacher"
                    and "*" in principal.students
                ),
                "authorized_profile_count": len(allowed),
                "student_selection_required": True,
                "blockers": blockers,
                "formal_candidate_practice_items": 0,
                "automatic_scoring_items": 0,
                "candidate_delivery_entry_present": False,
            },
        }

    def _project_learning_view(
        self, value: Any, student_id: str
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("profile_id") != student_id:
            raise ApiError(
                "student_learning_view_contract_mismatch",
                "student learning-view does not match the selected profile",
                409,
            )
        if value.get("human_reviewed") is not False:
            raise ApiError(
                "student_learning_view_review_overclaim",
                "student learning-view must remain machine-only and not human-reviewed",
                409,
            )

        def dictionary_rows(field: str) -> list[dict[str, Any]]:
            rows = value.get(field)
            if not isinstance(rows, list):
                return []
            return [dict(row) for row in rows if isinstance(row, dict)]

        mastery_history = dictionary_rows("mastery_history")
        latest_mastery_snapshot: list[dict[str, Any]] = []
        if mastery_history:
            latest = mastery_history[-1]
            snapshot = latest.get("snapshot")
            if isinstance(snapshot, list):
                latest_mastery_snapshot = [
                    dict(row) for row in snapshot if isinstance(row, dict)
                ]
            elif any(
                key in latest
                for key in ("tag_id", "dimension", "status", "loss_rate")
            ):
                latest_mastery_snapshot = [dict(latest)]

        weekly_source = value.get("weekly_activities")
        weekly_activities = (
            [dict(row) for row in weekly_source if isinstance(row, dict)]
            if isinstance(weekly_source, list)
            else []
        )
        projection = {
            "profile_id": student_id,
            "metadata": value.get("metadata")
            if isinstance(value.get("metadata"), dict)
            else {},
            "input_event_count": value.get("input_event_count", 0),
            "timeline_event_count": value.get("timeline_event_count", 0),
            "mastery_history": mastery_history,
            "latest_mastery_snapshot": latest_mastery_snapshot,
            "focus_history": dictionary_rows("focus_history"),
            "spaced_review_due": dictionary_rows("spaced_review_due"),
            "latest_week_over_week_delta": value.get(
                "latest_week_over_week_delta"
            ),
            "next_week_objectives": value.get("next_week_objectives")
            if isinstance(value.get("next_week_objectives"), list)
            else [],
            "weekly_activities": weekly_activities,
            "weekly_activities_source": (
                "backend_learning_view"
                if isinstance(weekly_source, list)
                else "not_provided"
            ),
            "history_head_sha256": value.get("history_head_sha256"),
            "long_term_teaching_effectiveness_verified": False,
            "machine_only": True,
            "human_reviewed": False,
            "field_availability": {
                "mastery_history": bool(mastery_history),
                "latest_mastery_snapshot": bool(latest_mastery_snapshot),
                "spaced_review_due": bool(dictionary_rows("spaced_review_due")),
                "next_week_objectives": bool(value.get("next_week_objectives")),
                "weekly_activities": bool(weekly_activities),
            },
            "practice_delivery": {
                "formal_candidate_practice_items": 0,
                "automatic_scoring_items": 0,
                "candidate_delivery_entry_present": False,
            },
        }
        projected = _public_status_projection(
            projection, self.config.shchem_root.absolute().parent
        )
        if not isinstance(projected, dict):
            raise ApiError(
                "student_learning_view_projection_failed",
                "student learning-view could not be safely projected",
                503,
            )
        return projected

    def learning_view(
        self, principal: Principal, student_id: str
    ) -> dict[str, Any]:
        authorize_student(principal, student_id)
        if self.config.mode == "mock":
            value = self.adapter.call(
                "student.learning_view", {"student_id": student_id}
            )
        else:
            try:
                value = self.student_bridge.learning_view(profile_id=student_id)
            except StudentBridgeError as exc:
                raise ApiError(
                    "student_learning_view_unavailable", str(exc), 409
                ) from exc
        return self._project_learning_view(value, student_id)

    def status(self) -> dict[str, Any]:
        availability = self.adapter.availability()
        status_data: dict[str, Any] = {}
        if availability.get("available"):
            try:
                status_data = self.adapter.call("status", {})
                if self.config.mode == "controller":
                    availability["live_status"] = "executed"
            except AdapterUnavailable as exc:
                availability["available"] = False
                availability["reason"] = exc.code
        result = {
            "service": "deeptutor-shchem-gateway",
            "service_version": "1.0.0",
            "contract_version": CONTRACT_VERSION,
            "mode": self.config.mode,
            "controller": availability,
            "controller_status": status_data,
            "central_status_claim": "live_only_or_unknown_never_historical_substitution",
            "ccswitch": self.ccswitch.status(),
            "student_learning": self.student_bridge.status(),
            "student_visual_analysis": {
                "available": True,
                "workflow": "quick_single_work",
                "student_private_root": True,
                "project_shared_intake_root_used": False,
                "local_or_cloud_ocr_enabled": False,
                "image_transport": "direct_page_vision_after_exact_hash_confirmation",
                "requires_teacher_review": True,
                "long_term_update_allowed": False,
            },
            "generation_v2": self.generation_bridge.status(),
            "machine_only": True,
            "human_reviewed": False,
            "collection_enabled": False,
            "delivery_policy": {
                "contract_version": "shchem.teacher_managed_delivery.v2",
                "external_publication_allowed": False,
                "official_claim_allowed": False,
                "human_reviewed": False,
            },
        }
        release_runtime = self.config.workbench_release_runtime
        if release_runtime is not None:
            result["serving_release"] = release_runtime.serving.public_dict()
            if self.workbench_release_gateway is not None:
                release_status = self._release_gateway_call(
                    self.workbench_release_gateway.status
                )
                result["selected_release"] = release_status["selected_release"]
                result["release_restart_required"] = release_status[
                    "restart_required"
                ]
        projected = _public_status_projection(
            result, self.config.shchem_root.absolute().parent
        )
        if not isinstance(projected, dict):
            raise ApiError(
                "public_status_projection_failed",
                "gateway status could not be safely projected",
                503,
            )
        projected["public_projection"] = {
            "absolute_host_paths_removed": True,
            "private_path_markers_removed": True,
            "path_fields_removed": True,
        }
        return projected

    def validate(self) -> dict[str, Any]:
        if self.config.mode == "mock":
            return {
                "command": "validate",
                "ok": True,
                "valid": True,
                "mode": "explicit_mock",
                "claim_scope": "synthetic_fixture_only",
                "live_controller_output": False,
                "controller_exit_code": 0,
            }
        try:
            result = self.adapter.call("validate", {})
        except AdapterUnavailable as exc:
            raise ApiError(exc.code, str(exc), 503, exc.details) from exc
        result["machine_only"] = True
        result["human_reviewed"] = False
        return result

    def evidence_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", ""))
        limit = min(max(int(payload.get("limit", 20)), 1), 100)
        if self.config.mode == "mock":
            return self.adapter.call(
                "evidence.search", {"query": query, "limit": limit}
            )
        if self.adapter.availability().get("available"):
            try:
                result = self.adapter.call("query", {"text": query, "limit": limit})
            except AdapterUnavailable:
                result = None
            if isinstance(result, dict):
                result["central_query_live"] = True
                return result
        return self.catalog.search(query, limit)

    def evidence_get(self, evidence_id: str) -> dict[str, Any]:
        if self.config.mode == "mock":
            return self.adapter.call("evidence.get", {"evidence_id": evidence_id})
        return self.catalog.get(evidence_id)

    def preflight(
        self,
        principal: Principal,
        payload: dict[str, Any],
        student_id: str | None = None,
    ) -> dict[str, Any]:
        if student_id:
            authorize_student(principal, student_id)
        upload_blockers = self._upload_blockers(
            student_id, payload.get("upload_ids", [])
        )
        try:
            result = self.adapter.call("preflight", payload)
        except AdapterUnavailable as exc:
            raise ApiError(exc.code, str(exc), 503, exc.details) from exc
        blockers = list(result.get("blockers", [])) + upload_blockers
        if blockers:
            result["ready"] = False
            result["status"] = "blocked"
            result["blockers"] = blockers
        result["machine_only"] = True
        result["human_reviewed"] = False
        return result

    def save_upload(
        self, principal: Principal, student_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        authorize_student(principal, student_id)
        return self.store.save_upload(student_id, payload)

    def create_candidate_job(
        self,
        principal: Principal,
        student_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        authorize_student(principal, student_id)
        if kind not in {
            "student_diagnosis",
            "handout_candidate",
            "generation_candidate",
        }:
            raise ApiError("invalid_job_kind", "unsupported candidate job kind")
        upload_ids = payload.get("upload_ids", [])
        if not isinstance(upload_ids, list) or any(
            not isinstance(item, str) for item in upload_ids
        ):
            raise ApiError("invalid_upload_ids", "upload_ids must be a string array")
        job_id = f"job-{uuid.uuid4().hex}"
        job: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "job_id": job_id,
            "student_id": student_id,
            "kind": kind,
            "status": "running",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "claim_scope": "synthetic_fixture_only"
            if payload.get("synthetic_fixture") is True
            else "real_or_unspecified",
            "machine_only": True,
            "human_reviewed": False,
            "input_binding_sha256": canonical_json_sha256(
                {"student_id": student_id, "kind": kind, "payload": payload}
            ),
            "upload_ids": upload_ids,
        }
        self.store.write_job(student_id, job)
        try:
            blockers = self._upload_blockers(student_id, upload_ids)
            mock_can_ignore_privacy_blockers = (
                self.config.mode == "mock"
                and payload.get("synthetic_fixture") is True
                and blockers
                and all(
                    item.startswith("upload_deidentification_not_clear:")
                    for item in blockers
                )
            )
            if blockers and not mock_can_ignore_privacy_blockers:
                result = {"status": "blocked", "blockers": blockers}
            elif self.config.mode == "controller":
                result, domain_artifact = self._run_domain_job(
                    student_id=student_id,
                    job_id=job_id,
                    kind=kind,
                    payload=payload,
                )
                if domain_artifact is not None:
                    job["private_paths"] = {"domain_artifact": str(domain_artifact)}
            else:
                operation = {
                    "student_diagnosis": "student.diagnosis",
                    "handout_candidate": "handout.candidate",
                    "generation_candidate": "generation.candidate",
                }[kind]
                adapter_payload = dict(payload)
                adapter_payload["student_id"] = student_id
                adapter_payload["uploads"] = self._derived_upload_bindings(
                    student_id, upload_ids
                )
                if mock_can_ignore_privacy_blockers:
                    adapter_payload["mock_ignored_local_only_uploads"] = list(
                        upload_ids
                    )
                result = self.adapter.call(operation, adapter_payload)
            blocked = result.get("status") == "blocked" or result.get("ready") is False
            job["status"] = "failed" if blocked else "completed"
            job["result"] = result
            job["failure_reasons"] = list(
                result.get("blockers", result.get("preflight", {}).get("blockers", []))
            )
            job["machine_qa"] = {
                "status": "fail" if blocked else "pass",
                "machine_only": True,
                "human_reviewed": False,
                "checks": {
                    "student_scope": "pass",
                    "input_binding": "pass",
                    "deidentification_or_mock_boundary": "pass"
                    if not blockers or self.config.mode == "mock"
                    else "fail",
                    "publication_gate": "closed",
                },
                "failure_reasons": job["failure_reasons"],
            }
        except AdapterUnavailable as exc:
            job["status"] = "failed"
            job["failure_reasons"] = [exc.code]
            job["result"] = {"status": "blocked", "details": exc.details}
            job["machine_qa"] = {
                "status": "fail",
                "machine_only": True,
                "failure_reasons": [exc.code],
            }
        job["updated_at"] = _utc_now()
        job["delivery_gate"] = self._delivery_gate(job.get("result", {}))
        job["quality_layers"] = self._quality_layers(
            job.get("result", {}), job["delivery_gate"]
        )
        if job["status"] == "completed":
            artifact_scope: str | None = None
            if self.config.mode == "mock":
                artifact_scope = "synthetic_test_only"
            elif job["delivery_gate"]["allowed"]:
                artifact_scope = "teacher_managed_delivery_candidate"
            if artifact_scope:
                if artifact_scope == "teacher_managed_delivery_candidate":
                    domain_value = job.get("private_paths", {}).get(
                        "domain_artifact"
                    )
                    domain_path = Path(str(domain_value or "")).resolve()
                    bundle = result.get("weekly_bundle", {})
                    bundle_manifest = (
                        bundle.get("manifest", {})
                        if isinstance(bundle, dict)
                        else {}
                    )
                    if (
                        not domain_path.is_file()
                        or _sha256_bytes(domain_path.read_bytes())
                        != bundle_manifest.get("zip_sha256")
                    ):
                        raise ApiError(
                            "domain_artifact_hash_mismatch",
                            "final student ZIP hash is not bound to the domain sidecar",
                            409,
                        )
                    artifact = {
                        "sha256": bundle_manifest["zip_sha256"],
                        "size_bytes": domain_path.stat().st_size,
                        "artifact_count": bundle_manifest.get("artifact_count"),
                        "formal_document_count": 8,
                        "download_is_domain_final_zip": True,
                    }
                else:
                    artifact = self.store.write_artifact_zip(student_id, job)
                job["artifact"] = {
                    **artifact,
                    "access_scope": artifact_scope,
                    "download_path": f"/api/v1/students/{student_id}/jobs/{job_id}/artifact.zip",
                }
        self.store.write_job(student_id, job)
        return self._public_job(job)

    def get_job(
        self, principal: Principal, student_id: str, job_id: str
    ) -> dict[str, Any]:
        authorize_student(principal, student_id)
        return self._public_job(self.store.get_job(student_id, job_id))

    def artifact_path(self, principal: Principal, student_id: str, job_id: str) -> Path:
        authorize_student(principal, student_id)
        job = self.store.get_job(student_id, job_id)
        if job.get("status") != "completed":
            raise ApiError("artifact_not_ready", "job artifact is not ready", 409)
        artifact = job.get("artifact", {})
        allowed_scope = artifact.get("access_scope")
        if allowed_scope == "synthetic_test_only" and self.config.mode != "mock":
            raise ApiError(
                "artifact_scope_rejected", "synthetic test ZIP is unavailable", 403
            )
        if allowed_scope == "teacher_managed_delivery_candidate" and not job.get(
            "delivery_gate", {}
        ).get("allowed"):
            raise ApiError(
                "delivery_gate_closed", "teacher-managed delivery gate is closed", 409
            )
        if allowed_scope not in {
            "synthetic_test_only",
            "teacher_managed_delivery_candidate",
        }:
            raise ApiError(
                "delivery_gate_closed", "no downloadable artifact scope is open", 409
            )
        if allowed_scope == "teacher_managed_delivery_candidate":
            domain_path_value = job.get("private_paths", {}).get("domain_artifact")
            if not isinstance(domain_path_value, str):
                raise ApiError(
                    "delivery_domain_zip_missing",
                    "final student ZIP path is unavailable",
                    409,
                )
            path = Path(domain_path_value).resolve()
            if self.config.student_data_root is None:
                raise ApiError(
                    "delivery_domain_root_missing",
                    "student private domain root is unavailable",
                    409,
                )
            profile_root = safe_join(
                self.config.student_data_root,
                "private_profiles",
                student_id[:2],
                student_id,
            )
            if profile_root not in path.parents or not path.is_file():
                raise ApiError(
                    "delivery_domain_zip_path_rejected",
                    "final student ZIP is outside the authorized profile root",
                    409,
                )
        else:
            path = self.store.artifact_path(student_id, job_id)
        if _sha256_bytes(path.read_bytes()) != artifact.get("sha256"):
            raise ApiError(
                "artifact_hash_mismatch", "download artifact hash changed", 409
            )
        if allowed_scope == "teacher_managed_delivery_candidate":
            result = job.get("result", {})
            bundle = result.get("weekly_bundle", {}) if isinstance(result, dict) else {}
            manifest = bundle.get("manifest", {}) if isinstance(bundle, dict) else {}
            if not (
                isinstance(manifest, dict)
                and manifest.get("manifest_scope") == "sidecar_final_zip_hash_bound"
                and manifest.get("artifact_count") == 23
                and manifest.get("formal_docx_pdf_included") is True
                and manifest.get("provider_production_ready") is True
                and _is_sha256(manifest.get("sidecar_sha256"))
            ):
                raise ApiError(
                    "delivery_sidecar_missing",
                    "final external ZIP sidecar is required at download time",
                    409,
                )
            if _sha256_bytes(path.read_bytes()) != manifest.get("zip_sha256"):
                raise ApiError(
                    "delivery_sidecar_hash_mismatch",
                    "final external ZIP sidecar hash no longer matches",
                    409,
                )
            sidecar_path = path.with_suffix(".manifest.sidecar.json")
            if (
                not sidecar_path.is_file()
                or _sha256_bytes(sidecar_path.read_bytes())
                != manifest.get("sidecar_sha256")
            ):
                raise ApiError(
                    "delivery_sidecar_file_hash_mismatch",
                    "final student ZIP sidecar file is missing or changed",
                    409,
                )
            try:
                persisted_sidecar = json.loads(
                    sidecar_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError(
                    "delivery_sidecar_unreadable",
                    "final student ZIP sidecar cannot be read",
                    409,
                ) from exc
            if (
                persisted_sidecar.get("manifest_scope")
                != "sidecar_final_zip_hash_bound"
                or persisted_sidecar.get("zip_sha256") != manifest.get("zip_sha256")
                or persisted_sidecar.get("artifact_count") != 23
            ):
                raise ApiError(
                    "delivery_sidecar_binding_mismatch",
                    "final student ZIP sidecar binding changed",
                    409,
                )
        return path

    def figure_get(self, figure_id: str) -> dict[str, Any]:
        validate_identifier(figure_id, "figure_id")
        try:
            if self.config.mode == "mock":
                result = self.adapter.call("figure.get", {"figure_id": figure_id})
            else:
                gate = self.adapter.call("preflight", {"mode": "figure"})
                if gate.get("ready") is not True:
                    raise AdapterUnavailable(
                        "controller_preflight_blocked",
                        "controller figure preflight is blocked",
                        {"blockers": gate.get("blockers", [])},
                    )
                result = self.generation_bridge.figure_get(figure_id)
                result["controller_preflight"] = self._gate_receipt(gate)
        except AdapterUnavailable as exc:
            status = 404 if exc.code == "figure_not_found" else 503
            raise ApiError(exc.code, str(exc), status, exc.details) from exc
        result["publication_allowed"] = False
        result["external_publication_allowed"] = False
        result["official_claim_allowed"] = False
        result["machine_only"] = True
        return result

    def publication_preflight(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if self.config.mode == "mock":
                result = self.adapter.call("publication.preflight", payload)
            else:
                controller = self.adapter.call("publication.preflight", payload)
                generation = self.generation_bridge.publication_candidate_status()
                blockers = sorted(
                    set(controller.get("blockers", []))
                    | set(generation.get("blockers", []))
                )
                result = {
                    "ready": False,
                    "status": "blocked",
                    "blockers": blockers,
                    "controller_preflight": self._gate_receipt(controller),
                    "generation_domain": generation,
                }
        except AdapterUnavailable as exc:
            raise ApiError(exc.code, str(exc), 503, exc.details) from exc
        result["publication_allowed"] = False
        result["external_publication_allowed"] = False
        result["official_claim_allowed"] = False
        result["machine_only"] = True
        result["human_reviewed"] = False
        return result

    def _run_domain_job(
        self,
        *,
        student_id: str,
        job_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], Path | None]:
        synthetic = payload.get("synthetic_fixture") is True
        if kind == "generation_candidate":
            profile_path = (
                Path(str(payload["profile_path"]))
                if payload.get("profile_path")
                else self.generation_bridge.observed_profile_path()
            )
            preparation = self.generation_bridge.prepare_candidate(
                payload,
                profile_path,
                self.store.domain_work_path(
                    student_id, job_id, "generation_content_catalog.json"
                ),
            )
            return (
                {
                    "status": preparation.get("status", "blocked"),
                    "blockers": list(preparation.get("blockers", [])),
                    "staged_candidate": preparation.get("candidate"),
                    "diagnostic_content": preparation.get(
                        "diagnostic_content"
                    ),
                    "generation_orchestration": {
                        "contract_version": "shchem.generation_public_v7_orchestration.v1",
                        "sequence": preparation.get("sequence", []),
                        "gateway_duplicate_controller_preflight": False,
                        "content_governance_scope": "content_only_no_delivery_claim",
                        "delivery_attestation_scope": "external_zip_sidecar_required",
                    },
                    "machine_only": True,
                    "human_reviewed": False,
                    "publication_allowed": False,
                },
                None,
            )

        if synthetic:
            gate_payload = {**payload, "mode": "diagnose_synthetic"}
            gate = self.adapter.call("preflight", gate_payload)
            gate_receipt = self._gate_receipt(gate)
            if gate.get("ready") is not True:
                return (
                    {
                        "status": "blocked",
                        "blockers": list(gate.get("blockers", [])),
                        "controller_preflight": gate_receipt,
                    },
                    None,
                )
            controller_orchestration: dict[str, Any] = gate_receipt
            prepare_id: str | None = None
            diagnosis: dict[str, Any] | None = None
        else:
            try:
                preparation = self.student_bridge.prepare_real_diagnosis(
                    profile_id=student_id
                )
            except StudentBridgeError as exc:
                return (
                    {
                        "status": "blocked",
                        "blockers": [str(exc)],
                        "controller_orchestration": {
                            "contract_version": (
                                "shchem.student_diagnosis_orchestration.v1"
                            ),
                            "mode": "diagnose",
                            "sequence": [
                                "student_prepare_private_input",
                                "controller_live_issue_and_preflight",
                                "student_execute_diagnosis",
                            ],
                            "failed_phase": "student_prepare_private_input",
                        },
                    },
                    None,
                )
            prepare_id = str(preparation.get("prepare_id", ""))
            controller_input = preparation.get("controller_input_without_receipt")
            if not prepare_id or not isinstance(controller_input, dict):
                return (
                    {
                        "status": "blocked",
                        "blockers": ["student_preparation_contract_invalid"],
                    },
                    None,
                )
            if "central_receipt" in controller_input:
                return (
                    {
                        "status": "blocked",
                        "blockers": [
                            "caller_supplied_central_receipt_forbidden"
                        ],
                    },
                    None,
                )
            controller_orchestration = {
                "contract_version": "shchem.student_diagnosis_orchestration.v1",
                "mode": "diagnose",
                "sequence": [
                    "student_prepare_private_input",
                    "controller_live_issue_preflight_and_receipt_verify",
                    "student_execute_diagnosis",
                ],
                "prepare_id": prepare_id,
                "prepare_request_sha256": preparation.get(
                    "prepare_request_sha256"
                ),
                "gateway_duplicate_controller_preflight": False,
                "machine_only": True,
                "human_reviewed": False,
            }
            issuance: dict[str, Any] | None = None
            gate_receipt = {}
            try:
                issuance = self.student_bridge.issue_real_diagnosis_receipt(
                    profile_id=student_id, prepare_id=prepare_id
                )
                controller_orchestration["controller_receipt_issue"] = issuance
                gate_receipt = {
                    "contract_version": issuance.get(
                        "controller_contract_version"
                    ),
                    "controller_version": issuance.get("controller_version"),
                    "mode": "diagnose",
                    "ready": True,
                    "ok": True,
                    "receipt_sha256": issuance.get("receipt_sha256"),
                    "live_controller_output": True,
                    "machine_only": True,
                    "human_reviewed": False,
                }
                if kind == "student_diagnosis":
                    diagnosis = self.student_bridge.execute_real_diagnosis(
                        profile_id=student_id,
                        prepare_id=prepare_id,
                    )
            except StudentBridgeError as exc:
                failed_phase = (
                    "student_execute_diagnosis"
                    if issuance is not None
                    else "controller_live_issue_preflight_and_receipt_verify"
                )
                controller_orchestration["failed_phase"] = failed_phase
                return (
                    {
                        "status": "blocked",
                        "blockers": [str(exc)],
                        "controller_preflight": gate_receipt or None,
                        "controller_orchestration": controller_orchestration,
                    },
                    None,
                )
        try:
            if kind == "student_diagnosis":
                if diagnosis is None:
                    diagnosis = self.student_bridge.diagnose(
                        profile_id=student_id,
                        synthetic=True,
                    )
                return (
                    {
                        "status": "candidate",
                        "diagnosis": diagnosis,
                        "controller_preflight": diagnosis.get(
                            "controller_diagnose_preflight", gate_receipt
                        ),
                        "controller_orchestration": controller_orchestration,
                        "machine_only": True,
                        "human_reviewed": False,
                        "teaching_use_allowed": False,
                        "publication_allowed": False,
                    },
                    None,
                )
            if kind == "handout_candidate":
                if not synthetic:
                    bundle, output_path = self.student_bridge.build_week_bundle(
                        profile_id=student_id,
                        synthetic=False,
                        week_number=int(payload.get("week_number", 1)),
                        prepare_id=prepare_id,
                    )
                    return (
                        {
                            "status": "candidate",
                            "weekly_bundle": bundle,
                            "controller_preflight": gate_receipt,
                            "controller_orchestration": controller_orchestration,
                            "machine_only": True,
                            "human_reviewed": False,
                            "teaching_use_allowed": False,
                            "publication_allowed": False,
                        },
                        output_path,
                    )
                bundle, output_path = self.student_bridge.build_week_bundle(
                    profile_id=student_id,
                    synthetic=True,
                    week_number=int(payload.get("week_number", 1)),
                )
                return (
                    {
                        "status": "candidate",
                        "weekly_bundle": bundle,
                        "controller_preflight": gate_receipt,
                        "controller_orchestration": controller_orchestration,
                        "machine_only": True,
                        "human_reviewed": False,
                        "teaching_use_allowed": False,
                        "publication_allowed": False,
                    },
                    output_path,
                )
            return (
                {"status": "blocked", "blockers": ["unknown_domain_job_kind"]},
                None,
            )
        except StudentBridgeError as exc:
            return (
                {
                    "status": "blocked",
                    "blockers": [str(exc)],
                    "controller_preflight": gate_receipt,
                },
                None,
            )

    @staticmethod
    def _delivery_gate(result: dict[str, Any]) -> dict[str, Any]:
        bundle = result.get("weekly_bundle", {})
        manifest = bundle.get("manifest", {}) if isinstance(bundle, dict) else {}
        verification = (
            bundle.get("verification", {}) if isinstance(bundle, dict) else {}
        )
        if manifest.get("schema_version") == "student_week_zip_manifest_v1":
            candidate_hash = manifest.get("zip_sha256")
            verification_hash = (
                verification.get("zip_sha256")
                if isinstance(verification, dict)
                else None
            )
            verification_sidecar_hash = (
                verification.get("sidecar_sha256")
                if isinstance(verification, dict)
                else None
            )
            domain_reasons = manifest.get("teacher_managed_delivery_reasons")
            blockers: list[str] = []
            if manifest.get("claim_scope") != "machine_only_real_student_candidate":
                blockers.append("real_student_machine_candidate_required")
            if manifest.get("teacher_managed_delivery_candidate") is not True:
                blockers.append("teacher_managed_delivery_candidate_true_required")
            if manifest.get("manifest_scope") != "sidecar_final_zip_hash_bound":
                blockers.append("final_zip_hash_sidecar_required")
            if (
                manifest.get("artifact_count") != 23
                or manifest.get("formal_docx_pdf_included") is not True
            ):
                blockers.append("23_artifact_8_document_inventory_required")
            if manifest.get("provider_production_ready") is not True:
                blockers.append("live_generation_provider_required")
            if domain_reasons != []:
                blockers.append("student_bundle_automated_verification_incomplete")
            if not _is_sha256(candidate_hash):
                blockers.append("hash_bound_candidate_required")
            if not (
                isinstance(verification, dict)
                and verification.get("status")
                == "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE"
                and verification_hash == candidate_hash
                and verification.get("artifact_count") == 23
                and verification.get("formal_document_count") == 8
                and _is_sha256(manifest.get("sidecar_sha256"))
                and verification_sidecar_hash == manifest.get("sidecar_sha256")
            ):
                blockers.append("student_bundle_hash_verification_required")
            if manifest.get("human_reviewed") is not False:
                blockers.append("human_review_overclaim_forbidden")
            return {
                "allowed": not blockers,
                "status": "ready" if not blockers else "blocked",
                "scope": "teacher_managed_delivery_candidate",
                "candidate_sha256": (
                    candidate_hash if _is_sha256(candidate_hash) else None
                ),
                "automated_verified_candidate_source": (
                    "student_bundle_internal_content_and_hash_gates"
                    if not blockers
                    else None
                ),
                "machine_review_receipt_hashes": [],
                "blockers": blockers,
                "external_publication_allowed": False,
                "official_claim_allowed": False,
                "human_reviewed": False,
            }
        if isinstance(result.get("external_delivery_attestation"), dict):
            return GenerationDomainAdapter.validate_external_delivery_attestation(
                result
            )
        status = result.get("content_status") or manifest.get("content_status")
        candidate_flag = result.get("teacher_managed_delivery_candidate")
        if candidate_flag is None:
            candidate_flag = manifest.get("teacher_managed_delivery_candidate")
        chain = result.get("machine_review_chain")
        if not isinstance(chain, dict):
            chain = manifest.get("machine_review_chain", {})
        receipt_hashes = (
            chain.get("receipt_hashes", []) if isinstance(chain, dict) else []
        )
        distinct_hashes = (
            isinstance(receipt_hashes, list)
            and len(receipt_hashes) >= 3
            and len(set(receipt_hashes)) == len(receipt_hashes)
            and all(_is_sha256(value) for value in receipt_hashes)
        )
        candidate_hash = result.get("candidate_sha256") or manifest.get("zip_sha256")
        # These legacy content/hash fields are content-QA evidence only. They
        # cannot unlock a real ZIP without the final archive-hash-bound sidecar.
        blockers: list[str] = ["external_final_zip_attestation_required"]
        if status != "automated_verified_candidate":
            blockers.append("automated_verified_candidate_required")
        if candidate_flag is not True:
            blockers.append("teacher_managed_delivery_candidate_true_required")
        if not _is_sha256(candidate_hash):
            blockers.append("hash_bound_candidate_required")
        if not (
            isinstance(chain, dict)
            and chain.get("status") == "pass"
            and distinct_hashes
        ):
            blockers.append("hash_bound_machine_review_chain_required")
        return {
            "allowed": not blockers,
            "status": "ready" if not blockers else "blocked",
            "scope": "teacher_managed_delivery_candidate",
            "candidate_sha256": candidate_hash if _is_sha256(candidate_hash) else None,
            "machine_review_receipt_hashes": receipt_hashes if distinct_hashes else [],
            "blockers": blockers,
            "external_publication_allowed": False,
            "official_claim_allowed": False,
            "human_reviewed": False,
        }

    @staticmethod
    def _quality_layers(
        result: dict[str, Any], delivery_gate: dict[str, Any]
    ) -> dict[str, Any]:
        bundle = result.get("weekly_bundle", {}) if isinstance(result, dict) else {}
        manifest = bundle.get("manifest", {}) if isinstance(bundle, dict) else {}
        verification = bundle.get("verification", {}) if isinstance(bundle, dict) else {}
        content_blockers: list[str] = []
        content_status = "pending"
        if manifest.get("claim_scope") == "synthetic_fixture_only":
            content_status = "structure_fixture_only"
            content_blockers = ["synthetic_fixture_not_content_complete"]
        elif manifest.get("claim_scope") == "machine_only_real_student_candidate":
            if (
                manifest.get("content_completeness_status")
                == "machine_only_real_formal_content_candidate"
                and verification.get("status")
                == "PASS_MACHINE_ONLY_FORMAL_CONTENT_CANDIDATE"
                and manifest.get("artifact_count") == 23
                and manifest.get("formal_docx_pdf_included") is True
                and manifest.get("provider_production_ready") is True
                and verification.get("formal_document_count") == 8
                and verification.get("sidecar_sha256")
                == manifest.get("sidecar_sha256")
            ):
                content_status = "pass"
            else:
                content_status = "blocked"
                content_blockers = ["formal_content_machine_qa_incomplete"]
        elif isinstance(result.get("content_qa"), dict):
            content_status = str(result["content_qa"].get("status", "pending"))
            content_blockers = list(result["content_qa"].get("blockers", []))
        elif result.get("status") == "candidate":
            content_status = "machine_candidate"
        return {
            "content": {
                "status": content_status,
                "blockers": content_blockers,
                "human_reviewed": False,
            },
            "delivery": {
                "status": "pass" if delivery_gate.get("allowed") else "blocked",
                "blockers": list(delivery_gate.get("blockers", [])),
                "external_sidecar_required": True,
                "human_reviewed": False,
            },
            "external_publication_allowed": False,
            "official_claim_allowed": False,
            "human_reviewed": False,
        }

    @staticmethod
    def _gate_receipt(gate: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": gate.get("contract_version"),
            "mode": gate.get("mode"),
            "ready": gate.get("ready") is True,
            "ok": gate.get("ok") is True,
            "blockers": list(gate.get("blockers", [])),
            "controller_exit_code": gate.get("controller_exit_code"),
            "live_controller_output": gate.get("live_controller_output") is True,
            "machine_only": True,
            "human_reviewed": False,
        }

    def _upload_blockers(self, student_id: str | None, upload_ids: Any) -> list[str]:
        if not upload_ids:
            return []
        if not student_id:
            return ["student_id_required_for_upload_binding"]
        if not isinstance(upload_ids, list):
            return ["upload_ids_must_be_array"]
        blockers: list[str] = []
        for upload_id in upload_ids:
            try:
                metadata = self.store.get_upload(student_id, str(upload_id))
            except (ApiError, SecurityError):
                blockers.append(f"upload_not_found_or_wrong_student:{upload_id}")
                continue
            if str(metadata.get("mime_type", "")).startswith("image/"):
                # Old sanitizer receipts remain readable history only.  They
                # can never unlock a new diagnosis/model request; image pixels
                # use the submission-specific visual workflow instead.
                blockers.append(f"student_image_awaiting_visual_analysis:{upload_id}")
                continue
            deidentification = metadata.get("deidentification", {})
            if not (
                deidentification.get("egress_allowed", False)
                or deidentification.get("model_egress_allowed", False)
            ):
                blockers.append(f"upload_deidentification_not_clear:{upload_id}")
        return blockers

    def _derived_upload_bindings(
        self, student_id: str, upload_ids: list[str]
    ) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        for upload_id in upload_ids:
            metadata = self.store.get_upload(student_id, upload_id)
            if str(metadata.get("mime_type", "")).startswith("image/"):
                continue
            deid = metadata.get("deidentification", {})
            derived = deid.get("derived_copy")
            if (deid.get("egress_allowed") or deid.get("model_egress_allowed")) and (
                isinstance(derived, dict) or deid.get("student_domain_media_id")
            ):
                bindings.append(
                    {
                        "upload_id": upload_id,
                        "sha256": derived.get("sha256")
                        if isinstance(derived, dict)
                        else deid.get("sanitized_sha256"),
                        "mime_type": metadata.get("mime_type"),
                        "deidentification_contract": deid.get("contract_version"),
                        "model_egress_allowed": True,
                        "egress_allowed": True,
                        "student_domain_media_id": deid.get("student_domain_media_id"),
                    }
                )
        return bindings

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "private_paths"}
