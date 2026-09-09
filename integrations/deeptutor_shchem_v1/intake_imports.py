from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

from .model_provider_probe import (
    ModelProviderProbeError,
    PinnedHttpsProbeTransport,
    ProbeTransportResponse,
    SyntheticProbeRequest,
)
from .model_provider_settings import (
    ModelProviderProbeContext,
    ModelProviderSettingsError,
    ModelProviderSettingsStore,
    _apply_owner_only_permissions,
    _assert_components_not_reparse,
    _assert_existing_path_safe,
    _paths_overlap,
)

INTAKE_IMPORT_WRITE_CAPABILITY = "intake_import_write"
INTAKE_VISUAL_EXECUTE_CAPABILITY = "intake_visual_execute"
INTAKE_IMPORT_SCHEMA_VERSION = "shchem.intake-import-job.v1"
INTAKE_VISUAL_CANDIDATE_SCHEMA_VERSION = "shchem.intake-visual-candidate.v1"

_IMPORT_ID = re.compile(r"^INTIMP-[0-9a-f]{32}$")
_ATTEMPT_ID = re.compile(r"^INTATT-[0-9a-f]{32}$")
_REVIEW_DECISION_ID = re.compile(r"^INTREV-[0-9a-f]{32}$")
_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_REVISION = re.compile(r"^rev_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ROLES = frozenset(
    {"question_paper", "answer", "handout", "textbook", "syllabus"}
)
_MIME_EXTENSIONS: Mapping[str, tuple[str, ...]] = {
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/webp": (".webp",),
    "application/pdf": (".pdf",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx",
    ),
}
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_ACTIVE_STATUSES = frozenset(
    {"preparing_pages", "queued_for_analysis", "analyzing", "cancel_requested"}
)
_TERMINAL_ATTEMPTS = frozenset({"completed", "failed", "blocked", "cancelled", "stale"})
_MAX_FILENAME_CHARS = 180
_MAX_PAGE_COUNT = 40
_MAX_EGRESS_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_EGRESS_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TOTAL_VISUAL_TIMEOUT_SECONDS = 90.0
_MAX_DOCX_ENTRIES = 12_000
_MAX_DOCX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_ATTEMPTS = 64
_MAX_EVENTS = 512
_MAX_TEACHER_NOTE_CHARS = 2000
_SENSITIVE_OUTPUT = re.compile(
    r"(?:[A-Za-z]:[\\/]|file://|\\\\|\b(?:api[_-]?key|authorization|credential[_-]?ref)\b|\bbearer\s+|\bsk-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
_ROOT_LEASES: set[Path] = set()
_ROOT_LEASES_LOCK = threading.Lock()


class IntakeImportError(ValueError):
    """Stable, sanitized error for the import API and persisted attempts."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


@dataclass(frozen=True, slots=True)
class RenderedPage:
    path: Path
    mime_type: str


class PageRenderer(Protocol):
    def render(
        self, source_path: Path, *, mime_type: str, work_root: Path
    ) -> list[RenderedPage]: ...


@dataclass(frozen=True, slots=True, repr=False)
class VisualProviderRequest:
    provider_id: str
    model_id: str
    host: str
    port: int
    path: str
    body: bytes
    api_key: str
    scheme: str
    api_style: str
    base_url_policy: str
    endpoint_scope: str

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider_id={self.provider_id!r}, "
            f"model_id={self.model_id!r}, host={self.host!r}, "
            f"path={self.path!r}, image_request=True, authorization_present=True)"
        )


class VisualTransport(Protocol):
    def send(
        self,
        request: VisualProviderRequest,
        *,
        cancel_event: threading.Event,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse: ...


class PinnedVisualTransport:
    """Reuse the pinned-DNS/TLS/no-redirect transport for one visual request."""

    def __init__(self, *, total_timeout_seconds: float = _TOTAL_VISUAL_TIMEOUT_SECONDS) -> None:
        self._transport = PinnedHttpsProbeTransport(
            total_timeout_seconds=total_timeout_seconds,
            max_response_bytes=_MAX_RESPONSE_BYTES,
            user_agent="ShanghaiChemWorkbench-VisualIntake/1",
        )

    def send(
        self,
        request: VisualProviderRequest,
        *,
        cancel_event: threading.Event,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        # The underlying transport is deliberately one-shot and performs no
        # retries. Its closed request type is reused only for the network
        # boundary; the visual request body was built and validated here.
        outbound = SyntheticProbeRequest(
            provider_id=request.provider_id,
            model_id=request.model_id,
            host=request.host,
            port=request.port,
            path=request.path,
            body=request.body,
            api_key=request.api_key,
            scheme=request.scheme,
            api_style=request.api_style,
            base_url_policy=request.base_url_policy,
            endpoint_scope=request.endpoint_scope,
            method="POST",
            request_kind="synthetic_probe",
        )
        return self._transport.send(
            outbound,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )


def default_intake_import_root() -> Path:
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or tempfile.gettempdir()
    )
    return base / "ShanghaiChem" / "IntakeImports" / "v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record_hash(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "record_sha256"}
    return _sha256_bytes(_canonical_json_bytes(payload))


def _candidate_hash(value: Mapping[str, Any]) -> str:
    """Hash the immutable validated candidate, independent of its job record."""

    return _sha256_bytes(_canonical_json_bytes(value))


def _strict_json_loads(raw: bytes | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(
        text,
        object_pairs_hook=unique,
        parse_constant=reject_constant,
    )


def _validate_filename(value: Any, mime_type: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_FILENAME_CHARS
        or value != value.strip()
        or value.endswith((".", " "))
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise IntakeImportError("filename_invalid", "upload filename is invalid")
    stem = Path(value).stem.casefold()
    if stem in {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }:
        raise IntakeImportError("filename_invalid", "upload filename is invalid")
    allowed = _MIME_EXTENSIONS.get(mime_type)
    if allowed is None or Path(value).suffix.casefold() not in allowed:
        raise IntakeImportError(
            "filename_mime_mismatch", "upload filename does not match its MIME type"
        )
    return value


def _detect_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return None


def _validate_docx_container(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            if (
                not infos
                or len(infos) > _MAX_DOCX_ENTRIES
                or "[Content_Types].xml" not in names
                or "word/document.xml" not in names
            ):
                raise IntakeImportError("docx_invalid", "DOCX container is invalid")
            total = 0
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                parts = normalized.split("/")
                if (
                    normalized.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or any(ord(character) < 0x20 for character in normalized)
                ):
                    raise IntakeImportError("docx_invalid", "DOCX container is invalid")
                total += int(info.file_size)
                if total > _MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise IntakeImportError("docx_too_large", "DOCX expands beyond the safe limit")
                if normalized.casefold().endswith("vbaproject.bin"):
                    raise IntakeImportError("docx_macro_forbidden", "macro-enabled DOCX is not accepted")
                if normalized.casefold().endswith(".rels"):
                    relation = archive.read(info)
                    lowered = relation.lower()
                    if b"<!doctype" in lowered or b"<!entity" in lowered:
                        raise IntakeImportError(
                            "docx_external_relationship_forbidden",
                            "DOCX external relationships are not accepted",
                        )
                    try:
                        root = ET.fromstring(relation)
                    except ET.ParseError:
                        raise IntakeImportError(
                            "docx_invalid", "DOCX relationship XML is invalid"
                        ) from None
                    for element in root.iter():
                        attributes = {
                            key.rsplit("}", 1)[-1].casefold(): str(value).strip()
                            for key, value in element.attrib.items()
                        }
                        target_mode = attributes.get("targetmode", "").casefold()
                        target = attributes.get("target", "")
                        parsed_target = urlsplit(target)
                        if (
                            target_mode == "external"
                            or parsed_target.scheme
                            or target.startswith(("//", "\\\\"))
                        ):
                            raise IntakeImportError(
                                "docx_external_relationship_forbidden",
                                "DOCX external relationships are not accepted",
                            )
    except IntakeImportError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError):
        raise IntakeImportError("docx_invalid", "DOCX container is invalid") from None


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise IntakeImportError("image_invalid", "image file is invalid") from None
    if not 1 <= width <= 20_000 or not 1 <= height <= 20_000 or width * height > 100_000_000:
        raise IntakeImportError("image_dimensions_invalid", "image dimensions are not accepted")
    return int(width), int(height)


class LocalPageRenderer:
    """Convert supported documents to ordered page images without text extraction."""

    def render(
        self, source_path: Path, *, mime_type: str, work_root: Path
    ) -> list[RenderedPage]:
        work_root.mkdir(parents=True, exist_ok=False)
        _apply_owner_only_permissions(work_root, directory=True)
        if mime_type in _IMAGE_MIMES:
            target = work_root / "page-0001.png"
            try:
                with Image.open(source_path) as opened:
                    image = ImageOps.exif_transpose(opened)
                    if image.mode not in {"RGB", "RGBA", "L"}:
                        image = image.convert("RGB")
                    image.save(target, format="PNG", optimize=False)
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                raise IntakeImportError("image_invalid", "image file is invalid") from None
            _apply_owner_only_permissions(target, directory=False)
            return [RenderedPage(target, "image/png")]

        # Reuse the already validated local document rendering toolchain. It
        # produces page bitmaps and never contributes extracted text here.
        from .paper_export_renderer import (
            RendererToolchain,
            _render_pdf_with_poppler,
            _render_with_canonical_docx_tool,
        )
        from .paper_export_workbench import _locate_toolchain

        located = _locate_toolchain()
        toolchain = RendererToolchain(
            python_exe=located.python_exe,
            render_docx_script=located.render_docx_script,
            pdftoppm_exe=located.pdftoppm_exe,
            dpi=160,
            conversion_backend=located.conversion_backend,
        ).validated()
        try:
            if mime_type == "application/pdf":
                paths, _receipt = _render_pdf_with_poppler(
                    source_path, work_root / "pages", toolchain=toolchain
                )
            else:
                _validate_docx_container(source_path)
                paths, _pdf, _receipt = _render_with_canonical_docx_tool(
                    source_path, work_root / "pages", toolchain=toolchain
                )
        except IntakeImportError:
            raise
        except Exception as exc:  # noqa: BLE001 - third-party renderer boundary
            code = getattr(exc, "code", "page_render_failed")
            raise IntakeImportError(code, "document pages could not be rendered", 409) from None
        return [RenderedPage(path, "image/png") for path in paths]


def _anchor_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "page": {"type": "integer", "minimum": 1},
            "bbox": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "x": {"type": "number", "minimum": 0, "maximum": 1},
                    "y": {"type": "number", "minimum": 0, "maximum": 1},
                    "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                },
                "required": ["x", "y", "width", "height"],
            },
        },
        "required": ["page", "bbox"],
    }


def intake_visual_candidate_schema() -> dict[str, Any]:
    evidence = {"type": "array", "items": _anchor_schema(), "minItems": 1, "maxItems": 20}
    confidence = {"type": "number", "minimum": 0, "maximum": 1}
    page_span = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start_page": {"type": "integer", "minimum": 1},
            "end_page": {"type": "integer", "minimum": 1},
        },
        "required": ["start_page", "end_page"],
    }

    def candidate(properties: dict[str, Any], required: Sequence[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {**properties, "confidence": confidence, "evidence": evidence},
            "required": [*required, "confidence", "evidence"],
        }

    nullable_id = {"type": ["string", "null"], "maxLength": 96}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": INTAKE_VISUAL_CANDIDATE_SCHEMA_VERSION},
            "candidate_status": {"type": "string", "const": "candidate_only"},
            "paper_identity_candidates": {
                "type": "array",
                "maxItems": 100,
                "items": candidate(
                    {
                        "field": {
                            "type": "string",
                            "enum": ["title", "year", "region_or_school", "paper_type"],
                        },
                        "value": {"type": "string", "minLength": 1, "maxLength": 240},
                    },
                    ["field", "value"],
                ),
            },
            "theme_boundaries": {
                "type": "array",
                "maxItems": 100,
                "items": candidate(
                    {
                        "theme_candidate_id": {"type": "string", "minLength": 1, "maxLength": 96},
                        "title_zh": {"type": ["string", "null"], "maxLength": 240},
                        "order": {"type": "integer", "minimum": 1},
                        "page_span": page_span,
                    },
                    ["theme_candidate_id", "title_zh", "order", "page_span"],
                ),
            },
            "printed_question_candidates": {
                "type": "array",
                "maxItems": 1000,
                "items": candidate(
                    {
                        "printed_candidate_id": {"type": "string", "minLength": 1, "maxLength": 96},
                        "theme_candidate_id": nullable_id,
                        "question_number": {"type": "string", "minLength": 1, "maxLength": 40},
                        "page_span": page_span,
                        "shared_material_refs": {
                            "type": "array",
                            "maxItems": 100,
                            "items": {"type": "string", "minLength": 1, "maxLength": 96},
                            "uniqueItems": True,
                        },
                    },
                    [
                        "printed_candidate_id",
                        "theme_candidate_id",
                        "question_number",
                        "page_span",
                        "shared_material_refs",
                    ],
                ),
            },
            "atomic_part_candidates": {
                "type": "array",
                "maxItems": 3000,
                "items": candidate(
                    {
                        "atomic_candidate_id": {"type": "string", "minLength": 1, "maxLength": 96},
                        "printed_candidate_id": {"type": "string", "minLength": 1, "maxLength": 96},
                        "part_label": {"type": ["string", "null"], "maxLength": 40},
                        "item_type": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                    ["atomic_candidate_id", "printed_candidate_id", "part_label", "item_type"],
                ),
            },
            "shared_material_candidates": {
                "type": "array",
                "maxItems": 1000,
                "items": candidate(
                    {
                        "shared_material_id": {"type": "string", "minLength": 1, "maxLength": 96},
                        "theme_candidate_id": nullable_id,
                        "page_span": page_span,
                        "summary_zh": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    ["shared_material_id", "theme_candidate_id", "page_span", "summary_zh"],
                ),
            },
            "answer_page_mappings": {
                "type": "array",
                "maxItems": 3000,
                "items": candidate(
                    {
                        "answer_page": {"type": "integer", "minimum": 1},
                        "question_number": {"type": "string", "minLength": 1, "maxLength": 40},
                        "printed_candidate_id": nullable_id,
                        "conflict": {"type": "boolean"},
                    },
                    ["answer_page", "question_number", "printed_candidate_id", "conflict"],
                ),
            },
            "textbook_mapping_candidates": {
                "type": "array",
                "maxItems": 3000,
                "items": candidate(
                    {
                        "atomic_candidate_id": nullable_id,
                        "volume_zh": {"type": ["string", "null"], "maxLength": 120},
                        "chapter_zh": {"type": ["string", "null"], "maxLength": 160},
                        "section_zh": {"type": ["string", "null"], "maxLength": 160},
                    },
                    ["atomic_candidate_id", "volume_zh", "chapter_zh", "section_zh"],
                ),
            },
            "cognitive_difficulty_candidates": {
                "type": "array",
                "maxItems": 3000,
                "items": candidate(
                    {
                        "atomic_candidate_id": nullable_id,
                        "level": {
                            "type": "string",
                            "enum": ["basic", "intermediate", "advanced", "unknown"],
                        },
                        "reason_zh": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    ["atomic_candidate_id", "level", "reason_zh"],
                ),
            },
            "review_blockers": {
                "type": "array",
                "maxItems": 1000,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "code": {
                            "type": "string",
                            "enum": [
                                "low_confidence",
                                "duplicate_question_number",
                                "cross_page_boundary",
                                "answer_conflict",
                                "unreadable_visual",
                                "missing_page_binding",
                                "other",
                            ],
                        },
                        "details_zh": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "evidence": {"type": "array", "items": _anchor_schema(), "maxItems": 20},
                    },
                    "required": ["code", "details_zh", "evidence"],
                },
            },
            "requires_teacher_review": {"type": "boolean", "const": True},
            "central_registry_write": {"type": "boolean", "const": False},
        },
        "required": [
            "schema_version",
            "candidate_status",
            "paper_identity_candidates",
            "theme_boundaries",
            "printed_question_candidates",
            "atomic_part_candidates",
            "shared_material_candidates",
            "answer_page_mappings",
            "textbook_mapping_candidates",
            "cognitive_difficulty_candidates",
            "review_blockers",
            "requires_teacher_review",
            "central_registry_write",
        ],
    }


def _validate_candidate(candidate: Any, page_count: int) -> dict[str, Any]:
    try:
        Draft202012Validator(intake_visual_candidate_schema()).validate(candidate)
    except ValidationError:
        raise IntakeImportError(
            "provider_output_schema_invalid",
            "visual provider returned an invalid candidate structure",
            502,
        ) from None
    assert isinstance(candidate, dict)
    for value in _walk_strings(candidate):
        if _SENSITIVE_OUTPUT.search(value):
            raise IntakeImportError(
                "provider_output_sensitive",
                "visual provider returned content outside the public boundary",
                502,
            )
    for anchor in _walk_anchors(candidate):
        page = anchor["page"]
        bbox = anchor["bbox"]
        if page > page_count or bbox["x"] + bbox["width"] > 1.000001 or bbox["y"] + bbox["height"] > 1.000001:
            raise IntakeImportError(
                "provider_output_evidence_invalid",
                "visual provider returned an invalid page evidence anchor",
                502,
            )
    try:
        themes = candidate["theme_boundaries"]
        printed = candidate["printed_question_candidates"]
        atomics = candidate["atomic_part_candidates"]
        shared = candidate["shared_material_candidates"]
        theme_ids = {item["theme_candidate_id"] for item in themes}
        printed_ids = {item["printed_candidate_id"] for item in printed}
        atomic_ids = {item["atomic_candidate_id"] for item in atomics}
        shared_ids = {item["shared_material_id"] for item in shared}
        if (
            len(theme_ids) != len(themes)
            or len({item["order"] for item in themes}) != len(themes)
            or len(printed_ids) != len(printed)
            or len(atomic_ids) != len(atomics)
            or len(shared_ids) != len(shared)
        ):
            raise ValueError("duplicate candidate ids")
        for item in (*themes, *printed, *shared):
            span = item["page_span"]
            if not 1 <= span["start_page"] <= span["end_page"] <= page_count:
                raise ValueError("page span")
        for item in printed:
            if item["theme_candidate_id"] is not None and item["theme_candidate_id"] not in theme_ids:
                raise ValueError("theme ref")
            if any(reference not in shared_ids for reference in item["shared_material_refs"]):
                raise ValueError("shared ref")
        for item in shared:
            if item["theme_candidate_id"] is not None and item["theme_candidate_id"] not in theme_ids:
                raise ValueError("shared theme ref")
        for item in atomics:
            if item["printed_candidate_id"] not in printed_ids:
                raise ValueError("printed ref")
        for item in candidate["answer_page_mappings"]:
            if not 1 <= item["answer_page"] <= page_count:
                raise ValueError("answer page")
            if item["printed_candidate_id"] is not None and item["printed_candidate_id"] not in printed_ids:
                raise ValueError("answer printed ref")
        for collection in (
            candidate["textbook_mapping_candidates"],
            candidate["cognitive_difficulty_candidates"],
        ):
            for item in collection:
                if item["atomic_candidate_id"] is not None and item["atomic_candidate_id"] not in atomic_ids:
                    raise ValueError("atomic ref")
    except (KeyError, TypeError, ValueError):
        raise IntakeImportError(
            "provider_output_relationship_invalid",
            "visual provider returned inconsistent candidate relationships",
            502,
        ) from None
    result = deepcopy(candidate)
    _append_machine_blockers(result)
    return result


def _walk_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            found.extend(_walk_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item))
    return found


def _walk_anchors(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        if set(value) == {"page", "bbox"} and isinstance(value.get("bbox"), Mapping):
            found.append(dict(value))
        else:
            for item in value.values():
                found.extend(_walk_anchors(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_anchors(item))
    return found


def _append_machine_blockers(candidate: dict[str, Any]) -> None:
    blockers = candidate["review_blockers"]
    existing = {item["code"] for item in blockers}
    collections = (
        "paper_identity_candidates",
        "theme_boundaries",
        "printed_question_candidates",
        "atomic_part_candidates",
        "shared_material_candidates",
        "answer_page_mappings",
        "textbook_mapping_candidates",
        "cognitive_difficulty_candidates",
    )
    if (
        any(
            item["confidence"] < 0.75
            for name in collections
            for item in candidate[name]
        )
        and "low_confidence" not in existing
    ):
        blockers.append(
            {
                "code": "low_confidence",
                "details_zh": "至少一个视觉候选的置信度低于 0.75，需教师逐页核对。",
                "evidence": [],
            }
        )
        existing.add("low_confidence")
    numbers = [item["question_number"] for item in candidate["printed_question_candidates"]]
    if len(numbers) != len(set(numbers)) and "duplicate_question_number" not in existing:
        blockers.append(
            {
                "code": "duplicate_question_number",
                "details_zh": "检测到重复印刷题号，需核对分页、续题或答案页。",
                "evidence": [],
            }
        )
        existing.add("duplicate_question_number")
    if any(
        item["page_span"]["start_page"] != item["page_span"]["end_page"]
        for item in candidate["printed_question_candidates"]
    ) and "cross_page_boundary" not in existing:
        blockers.append(
            {
                "code": "cross_page_boundary",
                "details_zh": "至少一道印刷小题跨页，需连同前后页复核完整边界。",
                "evidence": [],
            }
        )
        existing.add("cross_page_boundary")
    if any(item["conflict"] for item in candidate["answer_page_mappings"]) and "answer_conflict" not in existing:
        blockers.append(
            {
                "code": "answer_conflict",
                "details_zh": "答案页对应存在冲突，禁止据此自动判分或写入题库。",
                "evidence": [],
            }
        )


def _response_text(api_style: str, raw: bytes) -> tuple[str, dict[str, int] | None]:
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise IntakeImportError("provider_response_too_large", "visual provider response is too large", 502)
    try:
        payload = _strict_json_loads(raw)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise IntakeImportError("provider_response_invalid", "visual provider response is invalid", 502) from None
    if not isinstance(payload, Mapping):
        raise IntakeImportError("provider_response_invalid", "visual provider response is invalid", 502)
    text: Any = None
    if api_style == "responses":
        if (
            payload.get("status") != "completed"
            or payload.get("error") is not None
            or payload.get("incomplete_details") is not None
        ):
            raise IntakeImportError(
                "provider_response_incomplete",
                "visual provider did not complete the response",
                502,
            )
        output = payload.get("output")
        if isinstance(output, list):
            if any(
                isinstance(block, Mapping)
                and isinstance(block.get("content"), list)
                and any(
                    isinstance(item, Mapping)
                    and (
                        item.get("type") == "refusal"
                        or item.get("refusal") is not None
                        and item.get("refusal") != ""
                    )
                    for item in block["content"]
                )
                for block in output
            ):
                raise IntakeImportError(
                    "provider_response_refused",
                    "visual provider refused the response",
                    502,
                )
            texts = [
                item.get("text")
                for block in output
                if isinstance(block, Mapping) and isinstance(block.get("content"), list)
                for item in block["content"]
                if isinstance(item, Mapping) and item.get("type") == "output_text"
            ]
            valid = [item for item in texts if isinstance(item, str)]
            if len(valid) == 1:
                text = valid[0]
        if text is None and isinstance(payload.get("output_text"), str):
            text = payload["output_text"]
    else:
        choices = payload.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], Mapping):
            if choices[0].get("finish_reason") != "stop":
                raise IntakeImportError(
                    "provider_response_incomplete",
                    "visual provider did not complete the response",
                    502,
                )
            message = choices[0].get("message")
            if (
                isinstance(message, Mapping)
                and (
                    message.get("refusal") is None
                    or message.get("refusal") == ""
                )
            ):
                text = message.get("content")
            elif isinstance(message, Mapping):
                raise IntakeImportError(
                    "provider_response_refused",
                    "visual provider refused the response",
                    502,
                )
    if not isinstance(text, str) or not text.strip():
        raise IntakeImportError("provider_response_invalid", "visual provider response is invalid", 502)
    usage_value = payload.get("usage")
    usage: dict[str, int] | None = None
    if isinstance(usage_value, Mapping):
        candidate_usage: dict[str, int] = {}
        for key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"):
            value = usage_value.get(key)
            if type(value) is int and 0 <= value <= 10_000_000:
                candidate_usage[key] = value
        usage = candidate_usage or None
    return text, usage


def _egress_image(path: Path, mime_type: str) -> tuple[str, bytes, int, int]:
    raw = path.read_bytes()
    width, height = _image_dimensions(path)
    if len(raw) <= _MAX_EGRESS_IMAGE_BYTES and max(width, height) <= 2400:
        return mime_type, raw, width, height
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=86, optimize=True)
            converted = output.getvalue()
            width, height = image.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise IntakeImportError("image_invalid", "rendered page image is invalid") from None
    if len(converted) > _MAX_EGRESS_IMAGE_BYTES:
        raise IntakeImportError("page_egress_too_large", "a rendered page exceeds the visual request limit", 409)
    return "image/jpeg", converted, int(width), int(height)


def _visual_prompt(job: Mapping[str, Any], page_count: int) -> str:
    identity = job["identity"]
    return (
        "你是上海高中化学教师的页面视觉分析助手。直接查看随后附带的完整页面图，"
        "按 paper→theme_big_question→printed_question→atomic_part 四层提出候选。"
        "选择、填空、简答、计算等只能作为主题大题内部的作答形态。"
        "每个候选必须给出 0—1 置信度，以及页码和 0—1 归一化 bbox 的可见证据。"
        "看不清或跨页时保守保留 review_blockers；不得声称人工复核、官方答案或正式入库。"
        "只返回符合给定 JSON Schema 的 JSON 对象。\n"
        + json.dumps(
            {
                "source_role": job["source_role"],
                "identity_hint": identity,
                "page_count": page_count,
                "candidate_only": True,
                "teacher_review_required": True,
                "central_registry_write": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _build_visual_request(
    context: ModelProviderProbeContext,
    *,
    job: Mapping[str, Any],
    pages: Sequence[tuple[int, str, bytes, int, int]],
) -> VisualProviderRequest:
    api_style = context.api_style or (
        "responses" if context.provider_id == "openai" else "chat_completions"
    )
    if api_style not in {"responses", "chat_completions"}:
        raise IntakeImportError("provider_endpoint_invalid", "visual provider endpoint is invalid", 409)
    parsed = urlsplit(context.base_url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise IntakeImportError("provider_endpoint_invalid", "visual provider endpoint is invalid", 409) from None
    endpoint_scope = (
        "loopback"
        if context.base_url_policy == "openai_compatible_loopback_v1"
        else "public_https"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.scheme == "http" and endpoint_scope != "loopback"
    ):
        raise IntakeImportError("provider_endpoint_invalid", "visual provider endpoint is invalid", 409)
    prompt = _visual_prompt(job, len(pages))
    schema = intake_visual_candidate_schema()
    if api_style == "responses":
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for _number, mime_type, raw, _width, _height in pages:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
                    "detail": "high",
                }
            )
        body_value = {
            "background": False,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": 8000,
            "model": context.model_id,
            "store": False,
            "stream": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "shchem_intake_visual_candidate",
                    "strict": True,
                    "schema": schema,
                }
            },
            "tool_choice": "none",
            "tools": [],
        }
    else:
        schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content = [{"type": "text", "text": prompt + "\nJSON Schema:\n" + schema_text}]
        for _number, mime_type, raw, _width, _height in pages:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
                        "detail": "high",
                    },
                }
            )
        body_value = {
            "max_tokens": 8000,
            "messages": [
                {
                    "role": "system",
                    "content": "只返回一个符合用户消息中 JSON Schema 的 JSON 对象。",
                },
                {"role": "user", "content": content},
            ],
            "model": context.model_id,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
    body = _canonical_json_bytes(body_value)
    if len(body) > 48 * 1024 * 1024:
        raise IntakeImportError("visual_request_too_large", "rendered pages exceed the visual request limit", 409)
    return VisualProviderRequest(
        provider_id=context.provider_id,
        model_id=context.model_id,
        host=parsed.hostname.casefold(),
        port=port,
        path=parsed.path.rstrip("/") + ("/responses" if api_style == "responses" else "/chat/completions"),
        body=body,
        api_key=context.api_key,
        scheme=parsed.scheme,
        api_style=api_style,
        base_url_policy=context.base_url_policy,
        endpoint_scope=endpoint_scope,
    )


class IntakeImportJobManager:
    """Owner-only, project-external candidate import queue."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        project_root: str | Path,
        provider_store: ModelProviderSettingsStore | None,
        renderer: PageRenderer | None = None,
        transport: VisualTransport | None = None,
        max_upload_bytes: int = 6 * 1024 * 1024,
    ) -> None:
        lexical_root = Path(os.path.abspath(Path(state_root).expanduser()))
        lexical_project = Path(os.path.abspath(Path(project_root).expanduser()))
        try:
            _assert_components_not_reparse(lexical_root)
            _assert_components_not_reparse(lexical_project)
        except ModelProviderSettingsError:
            raise IntakeImportError(
                "intake_state_path_unsafe",
                "intake import state path is unsafe",
                503,
            ) from None
        self.root = lexical_root.resolve()
        self.project_root = lexical_project.resolve()
        if _paths_overlap(self.root, self.project_root):
            raise IntakeImportError(
                "intake_state_not_external",
                "intake import state must remain outside the project directory",
                503,
            )
        if not 1 <= max_upload_bytes <= 512 * 1024 * 1024:
            raise IntakeImportError("upload_limit_invalid", "upload limit is invalid", 503)
        self.max_upload_bytes = int(max_upload_bytes)
        self.provider_store = provider_store
        self.renderer = renderer or LocalPageRenderer()
        self.transport = transport or PinnedVisualTransport()
        self._lock = threading.RLock()
        self._activity = threading.Condition(self._lock)
        self._active_uploads = 0
        self._active_operations = 0
        self._cancel_events: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shchem-intake-vision")
        self._closed = False
        self._lease_stream: Any | None = None
        self._lease_registry_owned = False
        self._ensure_store()
        try:
            self._acquire_store_lease()
            self._recover_incomplete_jobs()
        except Exception:
            self._release_store_lease()
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise

    def _ensure_store(self) -> None:
        _assert_components_not_reparse(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _assert_existing_path_safe(self.root, regular_file=False)
        _apply_owner_only_permissions(self.root, directory=True)
        for name in ("objects", "pages", "jobs", ".tmp"):
            path = self.root / name
            path.mkdir(exist_ok=True)
            _assert_existing_path_safe(path, regular_file=False)
            _apply_owner_only_permissions(path, directory=True)

    def _acquire_store_lease(self) -> None:
        with _ROOT_LEASES_LOCK:
            if self.root in _ROOT_LEASES:
                raise IntakeImportError(
                    "intake_state_in_use",
                    "intake import state is already in use",
                    409,
                )
            _ROOT_LEASES.add(self.root)
            self._lease_registry_owned = True
        stream: Any | None = None
        try:
            lock_path = self.root / ".intake-import.v1.lock"
            _assert_existing_path_safe(lock_path, regular_file=True)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            descriptor_stat = os.fstat(descriptor)
            path_stat = lock_path.lstat()
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or descriptor_stat.st_nlink != 1
                or (descriptor_stat.st_dev, descriptor_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                os.close(descriptor)
                raise OSError("unsafe lease file")
            stream = os.fdopen(descriptor, "r+b", closefd=True)
            if descriptor_stat.st_size == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            _apply_owner_only_permissions(lock_path, directory=False)
            _assert_existing_path_safe(lock_path, regular_file=True)
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lease_stream = stream
        except (OSError, ModelProviderSettingsError):
            if stream is not None:
                stream.close()
            with _ROOT_LEASES_LOCK:
                if self._lease_registry_owned:
                    _ROOT_LEASES.discard(self.root)
                    self._lease_registry_owned = False
            raise IntakeImportError(
                "intake_state_in_use",
                "intake import state is already in use",
                409,
            ) from None

    def _release_store_lease(self) -> None:
        stream = self._lease_stream
        self._lease_stream = None
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        with _ROOT_LEASES_LOCK:
            if self._lease_registry_owned:
                _ROOT_LEASES.discard(self.root)
                self._lease_registry_owned = False

    def _job_path(self, import_id: str) -> Path:
        if not isinstance(import_id, str) or not _IMPORT_ID.fullmatch(import_id):
            raise IntakeImportError("import_id_invalid", "intake import id is invalid")
        return self.root / "jobs" / f"{import_id}.json"

    def _load_job(self, import_id: str) -> dict[str, Any]:
        path = self._job_path(import_id)
        if not path.is_file():
            raise IntakeImportError("import_not_found", "intake import was not found", 404)
        try:
            _assert_existing_path_safe(path, regular_file=True)
            raw = path.read_bytes()
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("job record too large")
            value = _strict_json_loads(raw)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError, ModelProviderSettingsError):
            raise IntakeImportError("import_record_corrupt", "intake import record is invalid", 409) from None
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != INTAKE_IMPORT_SCHEMA_VERSION
            or value.get("import_id") != import_id
            or not _SHA256.fullmatch(str(value.get("record_sha256", "")))
            or value["record_sha256"] != _record_hash(value)
            or not isinstance(value.get("events"), list)
            or not isinstance(value.get("attempts"), list)
        ):
            raise IntakeImportError("import_record_corrupt", "intake import record is invalid", 409)
        candidate = value.get("candidate")
        candidate_sha256 = value.get("candidate_sha256")
        if candidate is None:
            if candidate_sha256 is not None:
                raise IntakeImportError(
                    "import_record_corrupt", "intake import record is invalid", 409
                )
        elif not isinstance(candidate, Mapping) or candidate_sha256 is not None and (
            not isinstance(candidate_sha256, str)
            or not _SHA256.fullmatch(candidate_sha256)
            or candidate_sha256 != _candidate_hash(candidate)
        ):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        return value

    def _load_owned_job(self, import_id: str, actor_id: str) -> dict[str, Any]:
        if not isinstance(actor_id, str) or not _SAFE_ACTOR.fullmatch(actor_id):
            raise IntakeImportError("actor_invalid", "intake import actor is invalid")
        job = self._load_job(import_id)
        if job.get("created_by") != actor_id:
            # Do not reveal whether another teacher owns the opaque job ID.
            raise IntakeImportError(
                "import_not_found", "intake import was not found", 404
            )
        return job

    def _save_job(self, job: dict[str, Any]) -> None:
        job["updated_at"] = _utc_now()
        job["record_sha256"] = _record_hash(job)
        raw = _canonical_json_bytes(job)
        if len(raw) > 8 * 1024 * 1024:
            raise IntakeImportError(
                "import_record_limit_reached",
                "intake import record reached its safe size limit",
                409,
            )
        target = self._job_path(job["import_id"])
        temporary = self.root / ".tmp" / f".{job['import_id']}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _apply_owner_only_permissions(temporary, directory=False)
            os.replace(temporary, target)
            _apply_owner_only_permissions(target, directory=False)
        except OSError:
            raise IntakeImportError("import_write_failed", "intake import state could not be saved", 503) from None
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()

    @staticmethod
    def _event(job: dict[str, Any], event_type: str, details: Mapping[str, Any] | None = None) -> None:
        events = job["events"]
        if len(events) >= _MAX_EVENTS:
            raise IntakeImportError(
                "event_limit_reached",
                "intake import event limit reached",
                409,
            )
        events.append(
            {
                "sequence": len(events) + 1,
                "at": _utc_now(),
                "type": event_type,
                "details": dict(details or {}),
            }
        )

    @staticmethod
    def _source_metadata(source: Any) -> dict[str, Any] | None:
        public_source: dict[str, Any] | None = None
        if isinstance(source, Mapping):
            public_source = {
                "filename": source["filename"],
                "mime_type": source["mime_type"],
                "size_bytes": source["size_bytes"],
                "sha256": source["sha256"],
                "deduplicated": source["deduplicated"],
                "immutable": True,
                "page_count": len(source.get("pages", [])),
                "pages": [
                    {
                        "page": page["page"],
                        "mime_type": page["mime_type"],
                        "sha256": page["sha256"],
                        "size_bytes": page["size_bytes"],
                        "width": page["width"],
                        "height": page["height"],
                    }
                    for page in source.get("pages", [])
                ],
            }
        return public_source

    @staticmethod
    def _review_state(job: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize legacy two-field review records without rewriting them."""

        raw = job.get("review")
        if not isinstance(raw, Mapping):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        required = raw.get("required")
        status = raw.get("status")
        revision = raw.get("revision", 0)
        decisions = raw.get("decisions", [])
        personal_library_visible = raw.get("personal_library_visible", False)
        if (
            required is not True
            or status
            not in {"pending", "accepted_personal_library", "rejected"}
            or type(revision) is not int
            or revision < 0
            or not isinstance(decisions, list)
            or type(personal_library_visible) is not bool
            or revision != len(decisions)
            or revision > 1
        ):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        for index, decision in enumerate(decisions):
            if (
                not isinstance(decision, Mapping)
                or set(decision)
                != {
                    "decision_id",
                    "at",
                    "by",
                    "decision",
                    "candidate_sha256",
                    "revision_before",
                    "revision_after",
                    "teacher_note_zh",
                    "acknowledged_blocker_codes",
                }
                or not isinstance(decision.get("decision_id"), str)
                or not _REVIEW_DECISION_ID.fullmatch(decision["decision_id"])
                or not isinstance(decision.get("at"), str)
                or not isinstance(decision.get("by"), str)
                or not _SAFE_ACTOR.fullmatch(decision["by"])
                or decision.get("decision")
                not in {"accept_personal_library", "reject"}
                or not isinstance(decision.get("candidate_sha256"), str)
                or not _SHA256.fullmatch(decision["candidate_sha256"])
                or decision.get("revision_before") != index
                or decision.get("revision_after") != index + 1
                or not isinstance(decision.get("teacher_note_zh"), str)
                or not isinstance(decision.get("acknowledged_blocker_codes"), list)
                or any(
                    not isinstance(code, str)
                    for code in decision["acknowledged_blocker_codes"]
                )
            ):
                raise IntakeImportError(
                    "import_record_corrupt", "intake import record is invalid", 409
                )
        if status == "pending" and (
            revision != 0 or decisions or personal_library_visible
        ):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        if status == "accepted_personal_library" and (
            revision != 1
            or not personal_library_visible
            or decisions[-1]["decision"] != "accept_personal_library"
        ):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        if status == "rejected" and (
            revision != 1
            or personal_library_visible
            or decisions[-1]["decision"] != "reject"
        ):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        return {
            "required": True,
            "status": status,
            "revision": revision,
            "decisions": deepcopy(decisions),
            "personal_library_visible": personal_library_visible,
        }

    @staticmethod
    def _candidate_sha256(job: Mapping[str, Any]) -> str | None:
        candidate = job.get("candidate")
        if candidate is None:
            return None
        if not isinstance(candidate, Mapping):
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        computed = _candidate_hash(candidate)
        stored = job.get("candidate_sha256")
        if stored is not None and stored != computed:
            raise IntakeImportError(
                "import_record_corrupt", "intake import record is invalid", 409
            )
        return computed

    @staticmethod
    def _public(job: Mapping[str, Any]) -> dict[str, Any]:
        public_source = IntakeImportJobManager._source_metadata(job.get("source"))
        candidate_sha256 = IntakeImportJobManager._candidate_sha256(job)
        review = IntakeImportJobManager._review_state(job)
        attempts = []
        for attempt in job["attempts"]:
            attempts.append(
                {
                    key: deepcopy(value)
                    for key, value in attempt.items()
                    if key
                    in {
                        "attempt_id",
                        "status",
                        "provider_profile_id",
                        "credential_revision",
                        "vision_capability_evidence",
                        "visual_api_invocation_allowed",
                        "model_invoked",
                        "queued_at",
                        "started_at",
                        "completed_at",
                        "blocker",
                        "request",
                        "response",
                    }
                }
            )
        return {
            "schema_version": job["schema_version"],
            "import_id": job["import_id"],
            "status": job["status"],
            "source_role": job["source_role"],
            "identity": deepcopy(job["identity"]),
            "upload_spec": deepcopy(job["upload_spec"]),
            "source": public_source,
            "attempts": attempts,
            "candidate": deepcopy(job.get("candidate")),
            "candidate_sha256": candidate_sha256,
            "review": review,
            "egress_consent": deepcopy(job.get("egress_consent")),
            "central_registry_write": False,
            "recognition_mode": "direct_page_vision",
            "invocation_boundary": {
                "one_time_production_model_invocation_enabled": False,
                "specialized_visual_api_path": True,
                "candidate_only": True,
            },
            "events": deepcopy(job["events"]),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }

    def _recover_incomplete_jobs(self) -> None:
        with self._lock:
            for path in sorted((self.root / "jobs").glob("INTIMP-*.json")):
                match = _IMPORT_ID.fullmatch(path.stem)
                if match is None:
                    continue
                try:
                    job = self._load_job(path.stem)
                except IntakeImportError:
                    continue
                if job.get("status") not in _ACTIVE_STATUSES:
                    continue
                was_cancel = job["status"] == "cancel_requested"
                for attempt in reversed(job["attempts"]):
                    if attempt.get("status") not in _TERMINAL_ATTEMPTS:
                        attempt["status"] = "cancelled" if was_cancel else "failed"
                        attempt["completed_at"] = _utc_now()
                        attempt["blocker"] = {
                            "code": "cancelled" if was_cancel else "interrupted_by_restart",
                            "message_zh": "任务已取消。" if was_cancel else "上次处理被服务重启中断，可重新分析。",
                        }
                        break
                job["status"] = "cancelled" if was_cancel else (
                    "ready_for_analysis" if job.get("source", {}).get("pages") else "render_failed"
                )
                job["active_attempt_id"] = None
                self._event(job, "recovered_after_restart", {"status": job["status"]})
                self._save_job(job)

    def _begin_public_operation(self) -> None:
        with self._activity:
            if self._closed:
                raise IntakeImportError(
                    "intake_manager_closed",
                    "intake import manager is closed",
                    503,
                )
            self._active_operations += 1

    def _end_public_operation(self) -> None:
        with self._activity:
            self._active_operations -= 1
            self._activity.notify_all()

    def create(self, payload: Mapping[str, Any], *, actor_id: str) -> dict[str, Any]:
        self._begin_public_operation()
        try:
            return self._create(payload, actor_id=actor_id)
        finally:
            self._end_public_operation()

    def _create(self, payload: Mapping[str, Any], *, actor_id: str) -> dict[str, Any]:
        if not isinstance(actor_id, str) or not _SAFE_ACTOR.fullmatch(actor_id):
            raise IntakeImportError("actor_invalid", "intake import actor is invalid")
        required = {
            "source_role",
            "year",
            "region_or_school",
            "paper_type",
            "filename",
            "mime_type",
            "size_bytes",
        }
        if set(payload) != required:
            raise IntakeImportError("import_request_invalid", "intake import request fields are invalid")
        source_role = payload.get("source_role")
        if source_role not in _SOURCE_ROLES:
            raise IntakeImportError("source_role_invalid", "intake source role is invalid")
        identity: dict[str, str] = {}
        for name in ("year", "region_or_school", "paper_type"):
            value = payload.get(name)
            if not isinstance(value, str) or not 1 <= len(value) <= 120 or value != value.strip():
                raise IntakeImportError("source_identity_invalid", "intake source identity is invalid")
            identity[name] = value
        mime_type = payload.get("mime_type")
        if mime_type not in _MIME_EXTENSIONS:
            raise IntakeImportError("mime_type_unsupported", "upload MIME type is not supported", 415)
        filename = _validate_filename(payload.get("filename"), str(mime_type))
        size_bytes = payload.get("size_bytes")
        if type(size_bytes) is not int or not 1 <= size_bytes <= self.max_upload_bytes:
            raise IntakeImportError("upload_size_invalid", "upload size is outside the configured limit", 413)
        now = _utc_now()
        import_id = "INTIMP-" + secrets.token_hex(16)
        job = {
            "schema_version": INTAKE_IMPORT_SCHEMA_VERSION,
            "import_id": import_id,
            "created_by": actor_id,
            "created_at": now,
            "updated_at": now,
            "status": "awaiting_upload",
            "source_role": source_role,
            "identity": identity,
            "upload_spec": {
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "maximum_bytes": self.max_upload_bytes,
            },
            "source": None,
            "attempts": [],
            "candidate": None,
            "candidate_sha256": None,
            "active_attempt_id": None,
            "review": {
                "required": True,
                "status": "pending",
                "revision": 0,
                "decisions": [],
                "personal_library_visible": False,
            },
            "egress_consent": None,
            "central_registry_write": False,
            "events": [],
        }
        self._event(job, "import_created", {"status": "awaiting_upload"})
        with self._lock:
            self._save_job(job)
        return self._public(job)

    def _write_object(self, digest: str, extension: str, data: bytes) -> tuple[Path, bool]:
        directory = self.root / "objects" / digest[:2]
        directory.mkdir(exist_ok=True)
        _assert_existing_path_safe(directory, regular_file=False)
        _apply_owner_only_permissions(directory, directory=True)
        target = directory / f"{digest}{extension}"
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            _assert_existing_path_safe(target, regular_file=True)
            if target.stat().st_size != len(data) or _sha256_bytes(target.read_bytes()) != digest:
                raise IntakeImportError("object_store_collision", "immutable object store collision", 503)
            return target, True
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            _apply_owner_only_permissions(target, directory=False)
        except OSError:
            with contextlib.suppress(OSError):
                target.unlink()
            raise IntakeImportError("object_write_failed", "upload could not be saved", 503) from None
        return target, False

    def _load_validated_page_manifest(self, digest: str) -> list[dict[str, Any]]:
        if not _SHA256.fullmatch(digest):
            raise IntakeImportError("page_store_corrupt", "rendered page store is invalid", 409)
        final_root = self.root / "pages" / digest
        manifest_path = final_root / "manifest.json"
        try:
            _assert_existing_path_safe(final_root, regular_file=False)
            _assert_existing_path_safe(manifest_path, regular_file=True)
            manifest = _strict_json_loads(manifest_path.read_bytes())
            if (
                not isinstance(manifest, dict)
                or set(manifest) != {"source_sha256", "pages"}
                or manifest["source_sha256"] != digest
                or not isinstance(manifest["pages"], list)
                or not 1 <= len(manifest["pages"]) <= _MAX_PAGE_COUNT
            ):
                raise ValueError("manifest shape")
            validated: list[dict[str, Any]] = []
            for index, raw_page in enumerate(manifest["pages"], start=1):
                if not isinstance(raw_page, dict) or set(raw_page) != {
                    "page",
                    "mime_type",
                    "sha256",
                    "size_bytes",
                    "width",
                    "height",
                    "relative_path",
                }:
                    raise ValueError("page shape")
                mime_type = raw_page["mime_type"]
                if mime_type not in _IMAGE_MIMES:
                    raise ValueError("page mime")
                expected_relative = (
                    f"pages/{digest}/page-{index:04d}{_MIME_EXTENSIONS[mime_type][0]}"
                )
                if (
                    raw_page["page"] != index
                    or not _SHA256.fullmatch(str(raw_page["sha256"]))
                    or type(raw_page["size_bytes"]) is not int
                    or raw_page["size_bytes"] < 1
                    or type(raw_page["width"]) is not int
                    or type(raw_page["height"]) is not int
                    or not 1 <= raw_page["width"] <= 20_000
                    or not 1 <= raw_page["height"] <= 20_000
                    or raw_page["relative_path"] != expected_relative
                ):
                    raise ValueError("page metadata")
                path = (self.root / expected_relative).resolve()
                if self.root not in path.parents or not path.is_file():
                    raise ValueError("page path")
                _assert_existing_path_safe(path, regular_file=True)
                page_bytes = path.read_bytes()
                if (
                    len(page_bytes) != raw_page["size_bytes"]
                    or _sha256_bytes(page_bytes) != raw_page["sha256"]
                    or _detect_mime(page_bytes) != mime_type
                    or _image_dimensions(path)
                    != (raw_page["width"], raw_page["height"])
                ):
                    raise ValueError("page content")
                validated.append(dict(raw_page))
            return validated
        except IntakeImportError:
            raise
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ModelProviderSettingsError,
        ):
            raise IntakeImportError(
                "page_store_corrupt", "rendered page store is invalid", 409
            ) from None

    def _finalize_pages(
        self, digest: str, rendered: Sequence[RenderedPage]
    ) -> list[dict[str, Any]]:
        if not 1 <= len(rendered) <= _MAX_PAGE_COUNT:
            raise IntakeImportError("page_count_unsupported", "document page count is outside the supported range", 409)
        final_root = self.root / "pages" / digest
        manifest_path = final_root / "manifest.json"
        if manifest_path.is_file():
            return self._load_validated_page_manifest(digest)
        stage = self.root / ".tmp" / f"pages-{digest}-{secrets.token_hex(8)}"
        stage.mkdir()
        _apply_owner_only_permissions(stage, directory=True)
        pages: list[dict[str, Any]] = []
        try:
            for index, rendered_page in enumerate(rendered, start=1):
                raw = rendered_page.path.read_bytes()
                detected = _detect_mime(raw)
                if detected not in _IMAGE_MIMES or detected != rendered_page.mime_type:
                    raise IntakeImportError("rendered_page_invalid", "renderer emitted an invalid page image", 409)
                width, height = _image_dimensions(rendered_page.path)
                page_digest = _sha256_bytes(raw)
                suffix = _MIME_EXTENSIONS[detected][0]
                target = stage / f"page-{index:04d}{suffix}"
                target.write_bytes(raw)
                _apply_owner_only_permissions(target, directory=False)
                pages.append(
                    {
                        "page": index,
                        "mime_type": detected,
                        "sha256": page_digest,
                        "size_bytes": len(raw),
                        "width": width,
                        "height": height,
                        "relative_path": f"pages/{digest}/{target.name}",
                    }
                )
            manifest = {"source_sha256": digest, "pages": pages}
            (stage / "manifest.json").write_bytes(_canonical_json_bytes(manifest))
            _apply_owner_only_permissions(stage / "manifest.json", directory=False)
            try:
                os.replace(stage, final_root)
            except OSError:
                if not manifest_path.is_file():
                    raise
            _apply_owner_only_permissions(final_root, directory=True)
            return self._load_validated_page_manifest(digest)
        except IntakeImportError:
            raise
        except OSError:
            raise IntakeImportError("page_write_failed", "rendered pages could not be saved", 503) from None
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(stage)

    def upload(
        self,
        import_id: str,
        data: bytes,
        *,
        content_type: str,
        actor_id: str,
    ) -> dict[str, Any]:
        self._begin_public_operation()
        try:
            return self._upload(
                import_id,
                data,
                content_type=content_type,
                actor_id=actor_id,
            )
        finally:
            self._end_public_operation()

    def _upload(
        self,
        import_id: str,
        data: bytes,
        *,
        content_type: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if not isinstance(data, bytes):
            raise IntakeImportError("upload_invalid", "upload body is invalid")
        with self._lock:
            job = self._load_owned_job(import_id, actor_id)
            if job["status"] != "awaiting_upload":
                source = job.get("source")
                if isinstance(source, Mapping) and _sha256_bytes(data) == source.get("sha256"):
                    return self._public(job)
                raise IntakeImportError("source_already_uploaded", "an immutable source is already attached", 409)
            spec = job["upload_spec"]
            if content_type != spec["mime_type"]:
                raise IntakeImportError("upload_mime_mismatch", "upload Content-Type does not match the import", 415)
            if len(data) != spec["size_bytes"] or len(data) > self.max_upload_bytes:
                raise IntakeImportError("upload_size_mismatch", "upload size does not match the import", 400)
            if _detect_mime(data) != content_type:
                raise IntakeImportError("upload_signature_mismatch", "upload bytes do not match the declared MIME type", 415)
            digest = _sha256_bytes(data)
            extension = Path(spec["filename"]).suffix.casefold()
            object_path, deduplicated = self._write_object(digest, extension, data)
            if content_type in _IMAGE_MIMES:
                _image_dimensions(object_path)
            elif content_type.endswith("wordprocessingml.document"):
                _validate_docx_container(object_path)
            job["source"] = {
                "filename": spec["filename"],
                "mime_type": content_type,
                "size_bytes": len(data),
                "sha256": digest,
                "deduplicated": deduplicated,
                "object_relative_path": object_path.relative_to(self.root).as_posix(),
                "pages": [],
            }
            job["status"] = "preparing_pages"
            self._event(job, "source_saved", {"sha256": digest, "deduplicated": deduplicated})
            self._save_job(job)
            self._active_uploads += 1
        try:
            return self._complete_uploaded_source(
                import_id=import_id,
                object_path=object_path,
                content_type=content_type,
                digest=digest,
            )
        finally:
            with self._activity:
                self._active_uploads -= 1
                self._activity.notify_all()

    def _complete_uploaded_source(
        self,
        *,
        import_id: str,
        object_path: Path,
        content_type: str,
        digest: str,
    ) -> dict[str, Any]:
        work_root = self.root / ".tmp" / f"render-{import_id}-{secrets.token_hex(8)}"
        try:
            rendered = self.renderer.render(object_path, mime_type=content_type, work_root=work_root)
            pages = self._finalize_pages(digest, rendered)
        except IntakeImportError as exc:
            with self._lock:
                job = self._load_job(import_id)
                if job["status"] == "preparing_pages":
                    job["status"] = "render_failed"
                    job["source"]["pages"] = []
                    self._event(job, "page_render_failed", {"code": exc.code})
                self._save_job(job)
                result = self._public(job)
            return result
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(work_root)
        with self._lock:
            job = self._load_job(import_id)
            if job["status"] != "preparing_pages":
                return self._public(job)
            job["source"]["pages"] = pages
            job["status"] = "ready_for_analysis"
            self._event(job, "pages_ready", {"page_count": len(pages)})
            self._save_job(job)
            return self._public(job)

    @staticmethod
    def _provider_blocker(policy: Mapping[str, Any], credential_present: bool) -> tuple[str, str] | None:
        if not credential_present:
            return "awaiting_visual_provider", "所选视觉模型尚未配置 API Key。"
        evidence = policy.get("capability_evidence")
        if not isinstance(evidence, Mapping):
            return "vision_capability_unconfirmed", "所选模型的视觉能力证据不完整。"
        confirmed_vision = "vision" in set(evidence.get("declared", [])) | set(evidence.get("catalog", []))
        if not confirmed_vision or "vision" not in policy.get("effective_capabilities", []):
            return "vision_capability_unconfirmed", "所选模型没有已确认的视觉能力。"
        if "structured_output" not in policy.get("effective_capabilities", []):
            return "structured_output_capability_required", "所选模型没有结构化输出能力。"
        if (
            policy.get("image_egress")
            not in {
                "teacher_confirmed_source_pages",
                "teacher_confirmed_visual_pages",
            }
            or "source_page_image" not in policy.get("allowed_data_classes", [])
        ):
            return "image_egress_not_allowed", "所选 Profile 未允许题目页面图出站。"
        return None

    def analyze(
        self,
        import_id: str,
        *,
        profile_id: str,
        expected_revision: str,
        teacher_confirmed_egress: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        self._begin_public_operation()
        try:
            return self._analyze(
                import_id,
                profile_id=profile_id,
                expected_revision=expected_revision,
                teacher_confirmed_egress=teacher_confirmed_egress,
                actor_id=actor_id,
            )
        finally:
            self._end_public_operation()

    def _analyze(
        self,
        import_id: str,
        *,
        profile_id: str,
        expected_revision: str,
        teacher_confirmed_egress: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise IntakeImportError(
                "provider_profile_id_invalid", "visual provider profile id is invalid"
            )
        if not isinstance(expected_revision, str) or not _REVISION.fullmatch(
            expected_revision
        ):
            raise IntakeImportError(
                "provider_revision_invalid", "visual provider revision is invalid"
            )
        if teacher_confirmed_egress is not True:
            raise IntakeImportError("egress_confirmation_required", "page-image egress requires teacher confirmation", 409)
        with self._lock:
            job = self._load_owned_job(import_id, actor_id)
            if job["status"] in {"queued_for_analysis", "analyzing", "cancel_requested"}:
                return self._public(job)
            if job["status"] == "completed":
                raise IntakeImportError("analysis_already_completed", "intake analysis is already complete", 409)
            if job["status"] == "cancelled":
                raise IntakeImportError("import_cancelled", "cancelled intake import cannot be analyzed", 409)
            if not isinstance(job.get("source"), Mapping) or not job["source"].get("pages"):
                raise IntakeImportError("pages_not_ready", "source pages are not ready for visual analysis", 409)
            if len(job["attempts"]) >= _MAX_ATTEMPTS:
                raise IntakeImportError(
                    "attempt_limit_reached",
                    "intake import analysis attempt limit reached",
                    409,
                )
            consent = job.get("egress_consent")
            if isinstance(consent, Mapping) and consent.get("provider_profile_id") != profile_id:
                raise IntakeImportError(
                    "egress_consent_profile_mismatch",
                    "this import is bound to a different teacher-confirmed visual provider",
                    409,
                )
            attempt = {
                "attempt_id": "INTATT-" + secrets.token_hex(16),
                "status": "preflight",
                "provider_profile_id": profile_id,
                "credential_revision": expected_revision,
                "vision_capability_evidence": None,
                "visual_api_invocation_allowed": False,
                "model_invoked": False,
                "queued_at": _utc_now(),
                "started_at": None,
                "completed_at": None,
                "blocker": None,
                "request": None,
                "response": None,
            }
            job["attempts"].append(attempt)
            blocker: tuple[str, str] | None = None
            policy: Mapping[str, Any] | None = None
            if self.provider_store is None:
                blocker = ("awaiting_visual_provider", "视觉模型配置存储当前不可用。")
            else:
                try:
                    policy = self.provider_store.invocation_policy(
                        profile_id, expected_revision=expected_revision
                    )
                    credential_present = self.provider_store.credential_exists(profile_id)
                    blocker = self._provider_blocker(policy, credential_present)
                except ModelProviderSettingsError as exc:
                    code = (
                        "stale_provider_revision"
                        if exc.code == "revision_conflict"
                        else "awaiting_visual_provider"
                    )
                    blocker = (code, "所选视觉模型配置已变化，请刷新后重试。" if code.startswith("stale") else "当前没有可用的视觉模型配置。")
            if blocker is not None:
                attempt["status"] = "blocked"
                attempt["completed_at"] = _utc_now()
                attempt["blocker"] = {"code": blocker[0], "message_zh": blocker[1]}
                job["status"] = "awaiting_visual_provider"
                self._event(job, "analysis_blocked", {"code": blocker[0]})
                self._save_job(job)
                return self._public(job)
            assert policy is not None
            if consent is None:
                job["egress_consent"] = {
                    "confirmed": True,
                    "scope": "all_source_pages_this_import",
                    "provider_profile_id": profile_id,
                    "source_sha256": job["source"]["sha256"],
                    "confirmed_at": _utc_now(),
                }
                self._event(
                    job,
                    "source_page_egress_confirmed",
                    {"provider_profile_id": profile_id},
                )
            evidence = policy["capability_evidence"]
            attempt["vision_capability_evidence"] = {
                "capability": "vision",
                "sources": [
                    source
                    for source in ("catalog", "declared")
                    if "vision" in evidence.get(source, [])
                ],
                "inferred_from_model_name": False,
            }
            attempt["visual_api_invocation_allowed"] = True
            attempt["status"] = "queued"
            job["status"] = "queued_for_analysis"
            job["active_attempt_id"] = attempt["attempt_id"]
            self._event(job, "analysis_queued", {"attempt_id": attempt["attempt_id"]})
            self._save_job(job)
            cancel_event = threading.Event()
            self._cancel_events[import_id] = cancel_event
            self._executor.submit(
                self._run_analysis,
                import_id,
                attempt["attempt_id"],
                profile_id,
                expected_revision,
                str(policy["image_egress"]),
                cancel_event,
            )
            return self._public(job)

    def _attempt(self, job: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        for attempt in job["attempts"]:
            if attempt.get("attempt_id") == attempt_id:
                return attempt
        raise IntakeImportError("attempt_record_missing", "analysis attempt record is missing", 409)

    def _analysis_pages(self, job: Mapping[str, Any]) -> list[tuple[int, str, bytes, int, int]]:
        pages = job["source"]["pages"]
        if len(pages) > _MAX_PAGE_COUNT:
            raise IntakeImportError("page_count_unsupported", "document page count is outside the supported range", 409)
        result: list[tuple[int, str, bytes, int, int]] = []
        total = 0
        for page in pages:
            path = (self.root / page["relative_path"]).resolve()
            if self.root not in path.parents or not path.is_file():
                raise IntakeImportError("page_store_corrupt", "rendered page store is invalid", 409)
            _assert_existing_path_safe(path, regular_file=True)
            if _sha256_bytes(path.read_bytes()) != page["sha256"]:
                raise IntakeImportError("page_store_corrupt", "rendered page store is invalid", 409)
            mime_type, raw, width, height = _egress_image(path, page["mime_type"])
            total += len(raw)
            if total > _MAX_EGRESS_TOTAL_BYTES:
                raise IntakeImportError("visual_request_too_large", "rendered pages exceed the visual request limit", 409)
            result.append((page["page"], mime_type, raw, width, height))
        return result

    def _terminal_failure(
        self,
        import_id: str,
        attempt_id: str,
        *,
        status: str,
        code: str,
        message_zh: str,
        model_invoked: bool | None = None,
    ) -> None:
        with self._lock:
            job = self._load_job(import_id)
            attempt = self._attempt(job, attempt_id)
            if attempt["status"] in _TERMINAL_ATTEMPTS:
                return
            if job.get("active_attempt_id") != attempt_id:
                return
            attempt["status"] = status
            if model_invoked is not None:
                attempt["model_invoked"] = model_invoked
            attempt["completed_at"] = _utc_now()
            attempt["blocker"] = {"code": code, "message_zh": message_zh}
            job["status"] = (
                "cancelled"
                if status == "cancelled"
                else "awaiting_visual_provider"
                if status in {"blocked", "stale"}
                else "analysis_failed"
            )
            job["active_attempt_id"] = None
            self._event(job, "analysis_terminal", {"status": status, "code": code})
            self._save_job(job)

    def _run_analysis(
        self,
        import_id: str,
        attempt_id: str,
        profile_id: str,
        expected_revision: str,
        egress_policy: str,
        cancel_event: threading.Event,
    ) -> None:
        try:
            with self._lock:
                job = self._load_job(import_id)
                attempt = self._attempt(job, attempt_id)
                if (
                    cancel_event.is_set()
                    or job.get("active_attempt_id") != attempt_id
                    or job["status"] != "queued_for_analysis"
                ):
                    raise IntakeImportError("cancelled", "analysis was cancelled", 409)
                attempt["status"] = "running"
                attempt["started_at"] = _utc_now()
                job["status"] = "analyzing"
                self._event(job, "analysis_started", {"attempt_id": attempt_id})
                self._save_job(job)
                frozen_job = deepcopy(job)
            pages = self._analysis_pages(frozen_job)
            if self.provider_store is None:
                raise IntakeImportError("awaiting_visual_provider", "visual provider settings are unavailable", 409)
            with self.provider_store.borrow_invocation_context(
                profile_id, expected_revision=expected_revision
            ) as context:
                request = _build_visual_request(context, job=frozen_job, pages=pages)
                request_summary = {
                    "body_sha256": _sha256_bytes(request.body),
                    "body_bytes": len(request.body),
                    "image_count": len(pages),
                    "page_sha256": [item["sha256"] for item in frozen_job["source"]["pages"]],
                    "api_style": request.api_style,
                    "direct_page_images": True,
                    "data_class": "source_page_image",
                    "egress_policy": egress_policy,
                    "recognized_text_input_present": False,
                    "fallback_text_input_present": False,
                }
                with self._lock:
                    job = self._load_job(import_id)
                    attempt = self._attempt(job, attempt_id)
                    if (
                        cancel_event.is_set()
                        or job.get("active_attempt_id") != attempt_id
                        or job["status"] != "analyzing"
                    ):
                        raise IntakeImportError(
                            "cancelled", "analysis was cancelled", 409
                        )
                    attempt["request"] = request_summary
                    # The request is about to cross the transport boundary.
                    # Until the transport reports whether bytes reached the
                    # model, crash recovery must preserve an unknown state.
                    attempt["model_invoked"] = None
                    self._save_job(job)
                response = self.transport.send(
                    request,
                    cancel_event=cancel_event,
                    deadline_monotonic=time.monotonic() + _TOTAL_VISUAL_TIMEOUT_SECONDS,
                )
            with self._lock:
                job = self._load_job(import_id)
                attempt = self._attempt(job, attempt_id)
                attempt["model_invoked"] = bool(response.model_invoked)
                self._save_job(job)
            if cancel_event.is_set():
                raise IntakeImportError("cancelled", "analysis was cancelled", 409)
            # A credential rotation or profile edit after egress invalidates the
            # result. The response is discarded rather than silently committed.
            self.provider_store.invocation_policy(
                profile_id, expected_revision=expected_revision
            )
            text, usage = _response_text(request.api_style, response.body)
            try:
                decoded = _strict_json_loads(text)
            except (ValueError, json.JSONDecodeError):
                raise IntakeImportError("provider_output_invalid", "visual provider output is not valid JSON", 502) from None
            candidate = _validate_candidate(decoded, len(pages))
            with self._lock:
                job = self._load_job(import_id)
                attempt = self._attempt(job, attempt_id)
                if (
                    cancel_event.is_set()
                    or job.get("active_attempt_id") != attempt_id
                    or job["status"] != "analyzing"
                ):
                    raise IntakeImportError("cancelled", "analysis was cancelled", 409)
                attempt["status"] = "completed"
                attempt["completed_at"] = _utc_now()
                attempt["response"] = {
                    "body_sha256": _sha256_bytes(response.body),
                    "body_bytes": len(response.body),
                    "latency_ms": response.latency_ms,
                    "usage": usage,
                    "candidate_schema_valid": True,
                }
                job["candidate"] = candidate
                job["candidate_sha256"] = _candidate_hash(candidate)
                job["status"] = "completed"
                job["active_attempt_id"] = None
                job["review"] = {
                    "required": True,
                    "status": "pending",
                    "revision": 0,
                    "decisions": [],
                    "personal_library_visible": False,
                }
                self._event(job, "analysis_completed", {"attempt_id": attempt_id, "teacher_review_required": True})
                self._save_job(job)
        except IntakeImportError as exc:
            status = "cancelled" if exc.code == "cancelled" else "failed"
            self._terminal_failure(
                import_id,
                attempt_id,
                status=status,
                code=exc.code,
                message_zh="任务已取消。" if status == "cancelled" else "视觉分析失败，未生成可冒充成功的候选。",
            )
        except ModelProviderSettingsError as exc:
            status = "stale" if exc.code == "revision_conflict" else "blocked"
            code = "stale_provider_revision" if status == "stale" else "awaiting_visual_provider"
            self._terminal_failure(
                import_id,
                attempt_id,
                status=status,
                code=code,
                message_zh="视觉模型配置已变化，请刷新后重试。" if status == "stale" else "当前没有可用的视觉模型配置。",
            )
        except ModelProviderProbeError as exc:
            self._terminal_failure(
                import_id,
                attempt_id,
                status="cancelled" if exc.code == "cancelled" else "failed",
                code=exc.code,
                message_zh="任务已取消。" if exc.code == "cancelled" else "视觉模型请求失败，未生成候选。",
                model_invoked=bool(exc.model_invoked),
            )
        except Exception:  # noqa: BLE001
            self._terminal_failure(
                import_id,
                attempt_id,
                status="failed",
                code="analysis_internal_failure",
                message_zh="视觉分析失败，未生成候选。",
            )
        finally:
            with self._lock:
                if self._cancel_events.get(import_id) is cancel_event:
                    self._cancel_events.pop(import_id, None)

    def review_decision(
        self,
        import_id: str,
        *,
        expected_review_revision: int,
        candidate_sha256: str,
        decision: str,
        acknowledged_blocker_codes: list[str],
        teacher_note_zh: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Append one hash-bound terminal teacher decision to an import."""

        self._begin_public_operation()
        try:
            return self._review_decision(
                import_id,
                expected_review_revision=expected_review_revision,
                candidate_sha256=candidate_sha256,
                decision=decision,
                acknowledged_blocker_codes=acknowledged_blocker_codes,
                teacher_note_zh=teacher_note_zh,
                actor_id=actor_id,
            )
        finally:
            self._end_public_operation()

    def _review_decision(
        self,
        import_id: str,
        *,
        expected_review_revision: int,
        candidate_sha256: str,
        decision: str,
        acknowledged_blocker_codes: list[str],
        teacher_note_zh: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if type(expected_review_revision) is not int or expected_review_revision < 0:
            raise IntakeImportError(
                "review_revision_invalid", "review revision is invalid"
            )
        if (
            not isinstance(candidate_sha256, str)
            or not _SHA256.fullmatch(candidate_sha256)
        ):
            raise IntakeImportError(
                "candidate_sha256_invalid", "candidate hash is invalid"
            )
        if decision not in {"accept_personal_library", "reject"}:
            raise IntakeImportError(
                "review_decision_invalid", "review decision is invalid"
            )
        if (
            not isinstance(acknowledged_blocker_codes, list)
            or any(
                not isinstance(code, str) or not code
                for code in acknowledged_blocker_codes
            )
            or len(set(acknowledged_blocker_codes))
            != len(acknowledged_blocker_codes)
        ):
            raise IntakeImportError(
                "review_blocker_acknowledgement_invalid",
                "review blocker acknowledgement is invalid",
            )
        if (
            not isinstance(teacher_note_zh, str)
            or len(teacher_note_zh) > _MAX_TEACHER_NOTE_CHARS
            or teacher_note_zh != teacher_note_zh.strip()
            or any(ord(character) < 0x20 and character not in "\n\t" for character in teacher_note_zh)
        ):
            raise IntakeImportError(
                "teacher_note_invalid", "teacher review note is invalid"
            )

        with self._lock:
            job = self._load_owned_job(import_id, actor_id)
            review = self._review_state(job)
            if review["status"] != "pending":
                raise IntakeImportError(
                    "review_already_final",
                    "teacher review already has a terminal decision",
                    409,
                )
            if review["revision"] != expected_review_revision:
                raise IntakeImportError(
                    "review_revision_conflict",
                    "teacher review revision changed",
                    409,
                )
            stored_candidate_sha256 = self._candidate_sha256(job)
            candidate = job.get("candidate")
            if (
                job.get("status") != "completed"
                or stored_candidate_sha256 is None
                or not isinstance(candidate, Mapping)
            ):
                raise IntakeImportError(
                    "candidate_not_ready",
                    "visual candidate is not ready for teacher review",
                    409,
                )
            if candidate_sha256 != stored_candidate_sha256:
                raise IntakeImportError(
                    "candidate_sha256_mismatch",
                    "visual candidate changed; refresh before deciding",
                    409,
                )

            blockers = candidate.get("review_blockers")
            if not isinstance(blockers, list) or any(
                not isinstance(item, Mapping) or not isinstance(item.get("code"), str)
                for item in blockers
            ):
                raise IntakeImportError(
                    "import_record_corrupt", "intake import record is invalid", 409
                )
            blocker_codes = {item["code"] for item in blockers}
            acknowledged = set(acknowledged_blocker_codes)
            if not acknowledged.issubset(blocker_codes):
                raise IntakeImportError(
                    "review_blocker_acknowledgement_invalid",
                    "review blocker acknowledgement is invalid",
                )
            if decision == "accept_personal_library":
                printed = candidate.get("printed_question_candidates")
                atomics = candidate.get("atomic_part_candidates")
                if (
                    not isinstance(printed, list)
                    or not printed
                    or not isinstance(atomics, list)
                    or not atomics
                ):
                    raise IntakeImportError(
                        "candidate_structure_incomplete",
                        "at least one printed question and atomic part are required",
                        409,
                    )
                if acknowledged != blocker_codes:
                    raise IntakeImportError(
                        "review_blocker_acknowledgement_required",
                        "all current review blockers must be acknowledged exactly",
                        409,
                    )

            now = _utc_now()
            revision_after = review["revision"] + 1
            decision_record = {
                "decision_id": "INTREV-" + secrets.token_hex(16),
                "at": now,
                "by": actor_id,
                "decision": decision,
                "candidate_sha256": stored_candidate_sha256,
                "revision_before": review["revision"],
                "revision_after": revision_after,
                "teacher_note_zh": teacher_note_zh,
                "acknowledged_blocker_codes": sorted(acknowledged),
            }
            review["decisions"].append(decision_record)
            review["revision"] = revision_after
            accepted = decision == "accept_personal_library"
            review["status"] = (
                "accepted_personal_library" if accepted else "rejected"
            )
            review["personal_library_visible"] = accepted
            job["review"] = review
            # Preserve a canonical digest for legacy jobs that predate the
            # public candidate_sha256 field. The candidate itself is untouched.
            job["candidate_sha256"] = stored_candidate_sha256
            self._event(
                job,
                "review_decision_recorded",
                {
                    "decision_id": decision_record["decision_id"],
                    "decision": decision,
                    "candidate_sha256": stored_candidate_sha256,
                    "revision": revision_after,
                },
            )
            if _candidate_hash(job["candidate"]) != stored_candidate_sha256:
                raise IntakeImportError(
                    "candidate_sha256_mismatch",
                    "visual candidate changed; refresh before deciding",
                    409,
                )
            self._save_job(job)
            return self._public(job)

    def personal_library(self, *, actor_id: str) -> dict[str, Any]:
        """Project accepted visual candidates into the current teacher's library."""

        self._begin_public_operation()
        try:
            if not isinstance(actor_id, str) or not _SAFE_ACTOR.fullmatch(actor_id):
                raise IntakeImportError(
                    "actor_invalid", "intake import actor is invalid"
                )
            items: list[dict[str, Any]] = []
            with self._lock:
                for path in sorted((self.root / "jobs").glob("INTIMP-*.json")):
                    if not _IMPORT_ID.fullmatch(path.stem):
                        continue
                    job = self._load_job(path.stem)
                    if job.get("created_by") != actor_id:
                        continue
                    review = self._review_state(job)
                    if (
                        review["status"] != "accepted_personal_library"
                        or review["personal_library_visible"] is not True
                    ):
                        continue
                    candidate = job.get("candidate")
                    candidate_sha256 = self._candidate_sha256(job)
                    source = self._source_metadata(job.get("source"))
                    if (
                        not isinstance(candidate, Mapping)
                        or candidate_sha256 is None
                        or source is None
                    ):
                        raise IntakeImportError(
                            "import_record_corrupt",
                            "intake import record is invalid",
                            409,
                        )
                    latest = review["decisions"][-1]
                    items.append(
                        {
                            "import_id": job["import_id"],
                            "candidate_sha256": candidate_sha256,
                            "identity": {
                                "declared": deepcopy(job["identity"]),
                                "visual_candidates": deepcopy(
                                    candidate["paper_identity_candidates"]
                                ),
                            },
                            "source": source,
                            "theme_boundaries": deepcopy(
                                candidate["theme_boundaries"]
                            ),
                            "printed_question_candidates": deepcopy(
                                candidate["printed_question_candidates"]
                            ),
                            "atomic_part_candidates": deepcopy(
                                candidate["atomic_part_candidates"]
                            ),
                            "shared_material_candidates": deepcopy(
                                candidate["shared_material_candidates"]
                            ),
                            "answer_page_mappings": deepcopy(
                                candidate["answer_page_mappings"]
                            ),
                            "textbook_mapping_candidates": deepcopy(
                                candidate["textbook_mapping_candidates"]
                            ),
                            "cognitive_difficulty_candidates": deepcopy(
                                candidate["cognitive_difficulty_candidates"]
                            ),
                            "review": {
                                "status": review["status"],
                                "revision": review["revision"],
                                "decision_id": latest["decision_id"],
                                "decided_at": latest["at"],
                                "decided_by": latest["by"],
                            },
                            "candidate_only": True,
                            "central_registry_write": False,
                        }
                    )
            return {
                "schema_version": "shchem.personal-import-library.v1",
                "items": items,
                "count": len(items),
            }
        finally:
            self._end_public_operation()

    def page_content(
        self, import_id: str, page: int, *, actor_id: str
    ) -> dict[str, Any]:
        """Return the exact immutable rendered page bytes to its owner."""

        self._begin_public_operation()
        try:
            if type(page) is not int or page < 1:
                raise IntakeImportError(
                    "page_number_invalid", "intake page number is invalid"
                )
            with self._lock:
                job = self._load_owned_job(import_id, actor_id)
                source = job.get("source")
                if not isinstance(source, Mapping) or not source.get("pages"):
                    raise IntakeImportError(
                        "pages_not_ready", "source pages are not ready", 409
                    )
                manifest_pages = self._load_validated_page_manifest(
                    str(source["sha256"])
                )
                if manifest_pages != source["pages"]:
                    raise IntakeImportError(
                        "page_store_corrupt", "rendered page store is invalid", 409
                    )
                page_record = next(
                    (item for item in manifest_pages if item["page"] == page),
                    None,
                )
                if page_record is None:
                    raise IntakeImportError(
                        "page_not_found", "intake page was not found", 404
                    )
                path = (self.root / page_record["relative_path"]).resolve()
                if self.root not in path.parents or not path.is_file():
                    raise IntakeImportError(
                        "page_store_corrupt", "rendered page store is invalid", 409
                    )
                _assert_existing_path_safe(path, regular_file=True)
                content = path.read_bytes()
                if (
                    len(content) != page_record["size_bytes"]
                    or _sha256_bytes(content) != page_record["sha256"]
                    or _detect_mime(content) != page_record["mime_type"]
                    or _image_dimensions(path)
                    != (page_record["width"], page_record["height"])
                ):
                    raise IntakeImportError(
                        "page_store_corrupt", "rendered page store is invalid", 409
                    )
                return {
                    "import_id": import_id,
                    "page": page,
                    "mime_type": page_record["mime_type"],
                    "sha256": page_record["sha256"],
                    "size_bytes": page_record["size_bytes"],
                    "width": page_record["width"],
                    "height": page_record["height"],
                    "content": content,
                }
        finally:
            self._end_public_operation()

    def get(self, import_id: str, *, actor_id: str) -> dict[str, Any]:
        self._begin_public_operation()
        try:
            with self._lock:
                return self._public(self._load_owned_job(import_id, actor_id))
        finally:
            self._end_public_operation()

    def cancel(self, import_id: str, *, actor_id: str) -> dict[str, Any]:
        self._begin_public_operation()
        try:
            with self._lock:
                job = self._load_owned_job(import_id, actor_id)
                if job["status"] in {"completed", "analysis_failed", "cancelled"}:
                    return self._public(job)
                cancel_event = self._cancel_events.get(import_id)
                if cancel_event is not None:
                    cancel_event.set()
                    job["status"] = "cancel_requested"
                    self._event(job, "cancel_requested", {})
                else:
                    job["status"] = "cancelled"
                    self._event(job, "cancelled", {})
                self._save_job(job)
                return self._public(job)
        finally:
            self._end_public_operation()

    def shutdown(self, wait: bool = True) -> None:
        with self._activity:
            self._closed = True
            for event in self._cancel_events.values():
                event.set()
            while self._active_operations or self._active_uploads:
                self._activity.wait(timeout=0.5)
            # An analyze operation that was already admitted may have queued its
            # worker after the first cancellation pass.
            for event in self._cancel_events.values():
                event.set()
        # Releasing the cross-process store lease while a worker can still write
        # would permit two managers to mutate the same state. Always drain the
        # executor before the lease is released; wait only controls queued-work
        # cancellation for compatibility with the existing method signature.
        self._executor.shutdown(wait=True, cancel_futures=not wait)
        self._release_store_lease()


__all__ = [
    "INTAKE_IMPORT_SCHEMA_VERSION",
    "INTAKE_IMPORT_WRITE_CAPABILITY",
    "INTAKE_VISUAL_CANDIDATE_SCHEMA_VERSION",
    "INTAKE_VISUAL_EXECUTE_CAPABILITY",
    "IntakeImportError",
    "IntakeImportJobManager",
    "LocalPageRenderer",
    "PageRenderer",
    "PinnedVisualTransport",
    "RenderedPage",
    "VisualProviderRequest",
    "VisualTransport",
    "default_intake_import_root",
    "intake_visual_candidate_schema",
]
