from __future__ import annotations

"""Deterministic, self-contained browse snapshot for the teacher workbench.

The active workbench readers deliberately validate their live source products on
every process start.  A release candidate must go one step further: every byte
needed by the read-only question browser is materialized before it enters the
immutable release store.  The resulting mapping is directly consumable by
``WorkbenchReleaseStore.freeze_candidate`` and can later be served without
opening the live workspace.

This module has no HTTP mutation surface and does not read model credentials or
student-private state.  Its authority is permanently candidate-only.
"""

import hashlib
import json
import os
import re
import stat
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload, Wave1CandidateReviewReader
from .config import CONTRACT_VERSION
from .curriculum_workbench import (
    ACTIVE_ATOMIC_COUNT as CURRICULUM_ACTIVE_ATOMIC_COUNT,
)
from .curriculum_workbench import (
    ACTIVE_MASTER_COUNT as CURRICULUM_ACTIVE_MASTER_COUNT,
)
from .curriculum_workbench import (
    ACTIVE_SUPPLEMENTAL_COUNT as CURRICULUM_ACTIVE_SUPPLEMENTAL_COUNT,
)
from .curriculum_workbench import AUTHORITY as CURRICULUM_AUTHORITY
from .curriculum_workbench import (
    EDITION_STATUS_UNKNOWN as CURRICULUM_EDITION_STATUS_UNKNOWN,
)
from .curriculum_workbench import (
    EXPECTED_BLOCKED_ATOMIC_COUNT as CURRICULUM_BLOCKED_ATOMIC_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_BLOCKED_ENTRY_COUNT as CURRICULUM_BLOCKED_ENTRY_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_CHAPTER_COUNT as CURRICULUM_CHAPTER_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_COMPLETE_ATOMIC_COUNT as CURRICULUM_COMPLETE_ATOMIC_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_MAPPING_ENTRY_COUNT as CURRICULUM_MAPPING_ENTRY_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_PARTIAL_ATOMIC_COUNT as CURRICULUM_PARTIAL_ATOMIC_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_SECTION_COUNT as CURRICULUM_SECTION_COUNT,
)
from .curriculum_workbench import (
    EXPECTED_VOLUME_COUNT as CURRICULUM_VOLUME_COUNT,
)
from .curriculum_workbench import (
    SCHEMA_VERSION as CURRICULUM_SCHEMA_VERSION,
)
from .curriculum_workbench import SCOPE as CURRICULUM_SCOPE
from .curriculum_workbench import (
    UNIT_STATUS_UNKNOWN as CURRICULUM_UNIT_STATUS_UNKNOWN,
)
from .curriculum_workbench import CurriculumWorkbenchReader
from .master_direct_visual_scan import MasterDirectVisualScanReader
from .master_visual_scan_alias import COVERAGE_KIND as MASTER_ALIAS_COVERAGE_KIND
from .master_visual_scan_alias import MasterVisualScanAliasReader
from .master_wave1_workbench import AUTHORITY as MASTER_AUTHORITY
from .master_wave1_workbench import MasterWave1WorkbenchReader
from .material_intake_workbench import (
    PIPELINE as MATERIAL_INTAKE_PIPELINE,
)
from .material_intake_workbench import (
    RECORD_SECTIONS as MATERIAL_INTAKE_RECORD_SECTIONS,
)
from .material_intake_workbench import (
    STATUSES as MATERIAL_INTAKE_STATUSES,
)
from .material_intake_workbench import (
    MaterialIntakeWorkbenchReader,
)
from .question_processing_progress import (
    FULL_CAPTURE_LIMIT as QUESTION_PROGRESS_CAPTURE_LIMIT,
)
from .question_processing_progress import (
    QuestionProcessingProgressReader,
    filter_progress_response,
)
from .question_visual_scan import QuestionVisualScanReader
from .reference_answer import ABSENT, NONE
from .supplemental_visual_scan import SupplementalVisualScanReader
from .theme_workbench import ThemeWorkbenchReader
from .workbench_product_registry import (
    PRODUCT_ORDER,
    REGISTRY_ID,
    WorkbenchProductRegistryReader,
)

SNAPSHOT_SCHEMA_VERSION = "shchem.workbench.browse_snapshot.v1"
ROUTE_INDEX_SCHEMA_VERSION = "shchem.workbench.browse_route_index.v1"
SNAPSHOT_KIND = "local_candidate_browse_release_only"
SNAPSHOT_ALGORITHM = (
    "canonical-sha256-of-sorted-materialized-browse-artifact-descriptors-v1"
)
BACKEND_BUILD_ALGORITHM = (
    "canonical-sha256-of-sorted-backend-source-byte-descriptors-v1"
)
MANIFEST_RELATIVE = "snapshot/browse-closure.json"
ROUTE_INDEX_RELATIVE = "snapshot/route-index.json"
BACKEND_IDENTITY_RELATIVE = "snapshot/backend-build.json"
SNAPSHOT_SCHEMA_ARTIFACT = "contracts/browse_snapshot.schema.json"
OPENAPI_ARTIFACT = "contracts/gateway_openapi_v1.yaml"
REVIEW_TASK_CATALOG_ARTIFACT = "review/theme-review-task-catalog.json"

MATERIAL_INTAKE_SOURCE_FILES = {
    "ledger": (
        "material-intake/material_intake_ledger.json",
        (
            "sh-chem-db/kb/workbench/material_intake_ledger_v1/"
            "material_intake_ledger.json"
        ),
    ),
    "ledger_schema": (
        "material-intake/material_intake_ledger.schema.json",
        (
            "sh-chem-db/kb/workbench/material_intake_ledger_v1/"
            "material_intake_ledger.schema.json"
        ),
    ),
    "catalog": (
        "material-intake/inputs/catalog.csv",
        "sh-chem-db/catalog.csv",
    ),
    "full_bank_readiness": (
        "material-intake/inputs/full_bank_readiness_queue_v1.json",
        (
            "sh-chem-db/kb/question_classification_v1/reports/"
            "full_bank_readiness_queue_v1.json"
        ),
    ),
    "paper_inventory": (
        "material-intake/inputs/paper_inventory.jsonl",
        "sh-chem-db/kb/paper_learning_v1/paper_inventory.jsonl",
    ),
    "paper_profiles": (
        "material-intake/inputs/paper_profiles_index.jsonl",
        "sh-chem-db/kb/paper_learning_v1/profiles/index.jsonl",
    ),
    "formalization_queue": (
        "material-intake/inputs/formalization_queue.json",
        "sh-chem-db/kb/paper_learning_v1/reports/formalization_queue.json",
    ),
    "wave1_paper_candidates": (
        "material-intake/inputs/wave1_paper_records.jsonl",
        (
            "sh-chem-db/kb/formal/candidates/wave1_formalization_2026-08-04/"
            "paper_records.jsonl"
        ),
    ),
    "teaching_pack_inventory": (
        "material-intake/inputs/teaching_pack_inventory.jsonl",
        (
            "sh-chem-db/.intake/2026-07-30-user-teaching-pack/analysis/"
            "pack_inventory.jsonl"
        ),
    ),
}

DEFAULT_OVERLAY_RELATIVE = PurePosixPath("runtime/deeptutor_shchem/overlay")
DEFAULT_OPENAPI_RELATIVE = PurePosixPath(
    "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
DEFAULT_SCHEMA_RELATIVE = PurePosixPath(
    "sh-chem-db/kb/workbench/workbench_release_snapshot_v1/"
    "browse_snapshot.schema.json"
)

STATIC_FILES = ("app.js", "index.html", "styles.css")
DEFAULT_BACKEND_SOURCE_RELATIVES = (
    "integrations/deeptutor_shchem_v1/candidate_review.py",
    "integrations/deeptutor_shchem_v1/config.py",
    "integrations/deeptutor_shchem_v1/curriculum_workbench.py",
    "integrations/deeptutor_shchem_v1/http_app.py",
    "integrations/deeptutor_shchem_v1/launcher.py",
    "integrations/deeptutor_shchem_v1/material_intake_workbench.py",
    "integrations/deeptutor_shchem_v1/master_direct_visual_scan.py",
    "integrations/deeptutor_shchem_v1/master_parent_chain_repair_overlay.py",
    "integrations/deeptutor_shchem_v1/huangpu2025_theme4_direct_visual_scan.py",
    "integrations/deeptutor_shchem_v1/master_visual_scan_alias.py",
    "integrations/deeptutor_shchem_v1/master_wave1_workbench.py",
    "integrations/deeptutor_shchem_v1/paper_export_renderer.py",
    "integrations/deeptutor_shchem_v1/paper_export_workbench.py",
    "integrations/deeptutor_shchem_v1/paper_format_presets.py",
    "integrations/deeptutor_shchem_v1/presentation_jobs.py",
    "integrations/deeptutor_shchem_v1/presentation_theme_adapter.py",
    "integrations/deeptutor_shchem_v1/presentation_workbench.py",
    "integrations/deeptutor_shchem_v1/public_kb.py",
    "integrations/deeptutor_shchem_v1/question_processing_progress.py",
    "integrations/deeptutor_shchem_v1/question_search_workbench.py",
    "integrations/deeptutor_shchem_v1/question_visual_scan.py",
    "integrations/deeptutor_shchem_v1/theme_review_workbench.py",
    "integrations/deeptutor_shchem_v1/service.py",
    "integrations/deeptutor_shchem_v1/supplemental_visual_scan.py",
    "integrations/deeptutor_shchem_v1/supplemental_wechat_tagging_overlay.py",
    "integrations/deeptutor_shchem_v1/theme_workbench.py",
    "integrations/deeptutor_shchem_v1/workbench_product_registry.py",
    "integrations/deeptutor_shchem_v1/workbench_release_control.py",
    "integrations/deeptutor_shchem_v1/workbench_release_gateway.py",
    "integrations/deeptutor_shchem_v1/workbench_release_regression.py",
    "integrations/deeptutor_shchem_v1/workbench_release_snapshot.py",
    "integrations/shchem_review_workbench_v1/__init__.py",
    "integrations/shchem_review_workbench_v1/contracts.py",
    "integrations/shchem_review_workbench_v1/errors.py",
    "integrations/shchem_review_workbench_v1/store.py",
)

AUTHORITY = {
    "scope": SNAPSHOT_KIND,
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "external_publication_allowed": False,
}

DYNAMIC_DOMAINS_EXCLUDED = (
    "model_provider_credentials",
    "model_provider_profiles_and_probe_receipts",
    "student_private_profiles",
    "student_uploads_attempts_scores_and_artifacts",
    "mutable_prep_export_jobs_and_artifacts",
    "mutable_presentation_projects_jobs_and_artifacts",
    "mutable_review_and_generation_jobs",
)

DYNAMIC_ROUTE_PREFIXES = (
    "/api/v1/settings/model-providers",
    "/api/v1/students",
    "/api/v1/jobs",
    "/api/v1/tagging",
    "/api/v1/generation/workbench",
    "/api/v1/prep/exports",
    "/api/v1/presentations",
    "/api/v1/review",
)

_REQUIRED_CORE_ROUTES = frozenset(
    {
        "/api/v1/readiness",
        "/api/v1/workbench/product-registry",
        "/api/v1/kb/workbench/theme-groups?scope=wave1",
        "/api/v1/kb/workbench/theme-groups?scope=master",
        "/api/v1/kb/workbench/theme-groups?scope=supplemental",
        "/api/v1/kb/sources/candidate_review_only/wave1/status",
        "/api/v1/kb/workbench/master-atomic/status",
        "/api/v1/kb/workbench/supplemental-scans/status",
        "/api/v1/kb/workbench/question-visual-scans/status",
        "/api/v1/kb/workbench/question-visual-scans/catalog",
        "/api/v1/kb/workbench/master-direct-scans/status",
        "/api/v1/kb/workbench/master-direct-scans/catalog",
        "/api/v1/kb/workbench/master-visual-scan-aliases/status",
        "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
    }
)

CURRICULUM_CATALOG_ROUTE = "/api/v1/textbooks"
CURRICULUM_MAPPING_INDEX_ROUTE = "/api/v1/textbooks/mapping-index"
_REQUIRED_CURRICULUM_ROUTES = frozenset(
    {CURRICULUM_CATALOG_ROUTE, CURRICULUM_MAPPING_INDEX_ROUTE}
)
_CURRICULUM_CATALOG_KEYS = frozenset(
    {
        "schema_version",
        "data_snapshot_id",
        "scope",
        "title_zh",
        "counts",
        "volumes",
        "coverage",
        "authority",
        "integrity",
    }
)
_CURRICULUM_MAPPING_INDEX_KEYS = frozenset(
    {
        "schema_version",
        "data_snapshot_id",
        "scope",
        "counts",
        "records",
        "coverage",
        "authority",
        "integrity",
    }
)
_CURRICULUM_VOLUME_KEYS = frozenset(
    {
        "volume_id",
        "volume_title",
        "display_label_zh",
        "textbook_family",
        "publisher",
        "evidence_level",
        "edition_or_printing",
        "edition_status",
        "chapter_count",
        "section_count",
        "mapping_counts",
        "chapters",
    }
)
_CURRICULUM_CHAPTER_KEYS = frozenset(
    {
        "chapter_id",
        "chapter_title",
        "display_label_zh",
        "section_count",
        "mapping_counts",
        "sections",
    }
)
_CURRICULUM_SECTION_KEYS = frozenset(
    {
        "section_key",
        "section_id",
        "section_number",
        "section_title",
        "display_label_zh",
        "unit_id",
        "unit_title",
        "unit_status",
        "mapping_counts",
    }
)
_CURRICULUM_MAPPING_RECORD_KEYS = frozenset(
    {"atomic_id", "source_layer", "source_batch", "mapping_status", "entries"}
)
_CURRICULUM_MAPPING_ENTRY_KEYS = frozenset(
    {
        "knowledge_tag",
        "knowledge_role",
        "relation",
        "evidence_status",
        "mapping_status",
        "evidence_level",
        "volume_id",
        "volume_title",
        "chapter_id",
        "chapter_title",
        "section_key",
        "section_id",
        "section_number",
        "section_title",
        "unit_id",
        "unit_title",
        "unit_status",
        "edition_or_printing",
        "edition_status",
    }
)
_CURRICULUM_STATS_KEYS = frozenset(
    {
        "mapped_atomic_count",
        "complete_atomic_count",
        "partial_atomic_count",
        "blocked_atomic_count",
        "mapped_entry_count",
        "blocked_entry_count",
    }
)
_CURRICULUM_CATALOG_COUNT_KEYS = frozenset(
    {
        "volumes",
        "chapters",
        "sections",
        "section_ids_known",
        "section_ids_unknown",
        "active_atomic_mappings",
        "mapping_entries",
        *_CURRICULUM_STATS_KEYS,
    }
)
_CURRICULUM_INDEX_COUNT_KEYS = frozenset(
    {"active_atomic_mappings", "mapping_entries", *_CURRICULUM_STATS_KEYS}
)
_CURRICULUM_INTEGRITY = {
    "activated_directory_verified_on_read": True,
    "strict_json_verified": True,
    "directory_identity_unique": True,
    "active_mapping_sources_verified_on_read": True,
    "explicit_directory_mapping_only": True,
    "knowledge_tag_inference_used": False,
    "blocked_section_precision_fabricated": False,
    "theme_parent_chain_projected": False,
    "fail_closed": True,
}

_REQUIRED_MATERIAL_INTAKE_ROUTES = frozenset(
    {
        "/api/v1/intake/status",
        "/api/v1/intake/batches",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_FORBIDDEN_DYNAMIC_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "bearer_token",
        "credential",
        "credential_ref",
        "student_id",
        "student_profile_id",
        "upload_id",
        "attempt_id",
        "consent_record",
        "retention_expiry",
    }
)
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class WorkbenchReleaseSnapshotError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(
    code: str, message: str, status: int = 409
) -> WorkbenchReleaseSnapshotError:
    return WorkbenchReleaseSnapshotError(code, message, status)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error(
            "browse_snapshot_json_invalid",
            "browse snapshot contains a non-finite or non-JSON value",
            400,
        ) from exc


def _json_artifact_bytes(value: Any) -> bytes:
    return _canonical_json_bytes(value) + b"\n"


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical_json_bytes(value))


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _error(
                    "browse_snapshot_json_invalid",
                    f"{label} contains a duplicate JSON key",
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except WorkbenchReleaseSnapshotError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(
            "browse_snapshot_json_invalid",
            f"{label} is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "browse_snapshot_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _parse_canonical_json(raw: bytes, label: str) -> dict[str, Any]:
    value = _strict_json_object(raw, label)
    if _json_artifact_bytes(value) != raw:
        raise _error(
            "browse_snapshot_json_not_canonical",
            f"{label} is not canonical newline-terminated JSON",
        )
    return value


def _self_hash(value: Mapping[str, Any]) -> str:
    projected = deepcopy(dict(value))
    projected.pop("self_sha256", None)
    return _canonical_sha256(projected)


def _with_self_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("self_sha256", None)
    result["self_sha256"] = _canonical_sha256(result)
    return result


def _safe_relative(value: str | PurePosixPath) -> PurePosixPath:
    text = str(value)
    if not text or "\\" in text or "\x00" in text:
        raise _error(
            "browse_snapshot_path_invalid", "snapshot path is not safe", 400
        )
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _error(
            "browse_snapshot_path_invalid", "snapshot path is not relative", 400
        )
    for part in path.parts:
        if part.rstrip(" .") != part or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise _error(
                "browse_snapshot_path_invalid",
                "snapshot path is not portable on Windows",
                400,
            )
    if len(path.as_posix()) > 512:
        raise _error(
            "browse_snapshot_path_invalid", "snapshot path is too long", 400
        )
    return path


def _link_like(details: os.stat_result) -> bool:
    if stat.S_ISLNK(details.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(details, "st_file_attributes", 0) & reparse_flag)


def _file_identity(details: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
        int(details.st_ctime_ns),
        int(getattr(details, "st_nlink", 1)),
    )


def _assert_no_link_components(root: Path, path: Path) -> None:
    try:
        root_resolved = root.resolve(strict=True)
        path_resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _error(
            "browse_snapshot_source_missing", "snapshot source path is missing"
        ) from exc
    try:
        relative = path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise _error(
            "browse_snapshot_source_escape",
            "snapshot source escapes the configured workspace",
            403,
        ) from exc
    cursor = root_resolved
    if _link_like(cursor.lstat()):
        raise _error(
            "browse_snapshot_source_unsafe",
            "workspace root is a symlink or reparse point",
            403,
        )
    for part in relative.parts:
        cursor /= part
        try:
            details = cursor.lstat()
        except OSError as exc:
            raise _error(
                "browse_snapshot_source_missing", "snapshot source path is missing"
            ) from exc
        if _link_like(details):
            raise _error(
                "browse_snapshot_source_unsafe",
                "snapshot source traverses a symlink or reparse point",
                403,
            )


def _read_stable_regular_file(root: Path, relative: str | PurePosixPath) -> bytes:
    safe = _safe_relative(relative)
    path = root.joinpath(*safe.parts)
    _assert_no_link_components(root, path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise _error(
            "browse_snapshot_source_missing", "required snapshot source is missing"
        ) from exc
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_nlink", 1) != 1:
        raise _error(
            "browse_snapshot_source_unsafe",
            "snapshot source is not a single-link regular file",
            403,
        )
    try:
        first = path.read_bytes()
        middle = path.lstat()
        second = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise _error(
            "browse_snapshot_source_unavailable",
            "snapshot source could not be read twice",
        ) from exc
    if (
        _file_identity(before) != _file_identity(middle)
        or _file_identity(middle) != _file_identity(after)
        or first != second
        or len(first) != before.st_size
    ):
        raise _error(
            "browse_snapshot_source_drift",
            "snapshot source changed between two reads",
        )
    if not first:
        raise _error(
            "browse_snapshot_source_empty", "snapshot source must not be empty"
        )
    return first


def _artifact_descriptor(relative_path: str, raw: bytes) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "sha256": _sha256(raw),
        "bytes": len(raw),
    }


def _valid_png(raw: bytes) -> bool:
    signature = b"\x89PNG\r\n\x1a\n"
    if not raw.startswith(signature):
        return False
    offset = len(signature)
    saw_ihdr = False
    saw_idat = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            return False
        length = int.from_bytes(raw[offset : offset + 4], "big")
        chunk_type = raw[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(raw):
            return False
        chunk_data = raw[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(raw[offset + 8 + length : chunk_end], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return False
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(chunk_data[:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            if width < 1 or height < 1:
                return False
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            return False
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            return length == 0 and saw_ihdr and saw_idat and chunk_end == len(raw)
        offset = chunk_end
    return False


def _reject_dynamic_value(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise _error(
                    "browse_snapshot_projection_invalid",
                    "snapshot projection contains a non-string JSON key",
                )
            if key.casefold() in _FORBIDDEN_DYNAMIC_KEYS:
                raise _error(
                    "browse_snapshot_dynamic_domain_forbidden",
                    f"dynamic/private field is forbidden in browse snapshot: {key}",
                    403,
                )
            _reject_dynamic_value(nested, (*path, key))
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_dynamic_value(nested, (*path, str(index)))
        return
    if isinstance(value, str):
        normalized = value.strip().replace("\\", "/")
        if _WINDOWS_ABSOLUTE.match(value) or normalized.casefold().startswith(
            ("/home/", "/users/", "runtime/deeptutor_shchem/private_state")
        ):
            raise _error(
                "browse_snapshot_absolute_path_forbidden",
                "browse projection exposes a private or absolute local path",
                403,
            )
        if normalized.startswith(DYNAMIC_ROUTE_PREFIXES):
            raise _error(
                "browse_snapshot_dynamic_domain_forbidden",
                "browse projection references a dynamic/private API route",
                403,
            )


def _route_allowed(route: str) -> None:
    if (
        not isinstance(route, str)
        or not route.startswith("/api/v1/")
        or "#" in route
        or "\x00" in route
        or "\\" in route
    ):
        raise _error(
            "browse_snapshot_route_invalid", "snapshot route is invalid", 400
        )
    if route.startswith(DYNAMIC_ROUTE_PREFIXES):
        raise _error(
            "browse_snapshot_dynamic_domain_forbidden",
            "dynamic/private route cannot enter the browse snapshot",
            403,
        )
    if "/answer" in route.casefold():
        raise _error(
            "browse_snapshot_answer_image_forbidden",
            "answer-image routes cannot enter the browse snapshot",
            403,
        )


@dataclass(frozen=True)
class BrowseCapture:
    """One immutable observation returned by a projection source."""

    json_routes: Mapping[str, Any]
    binary_routes: Mapping[str, CandidateCropPayload]
    counts: Mapping[str, Any]
    identity: Mapping[str, Any]


class BrowseProjectionSource(Protocol):
    def capture(self) -> BrowseCapture: ...


def _encoded(value: str) -> str:
    return quote(value, safe="")


def _pagination_offsets(total: int, page_size: int = 200) -> Iterable[int]:
    if type(total) is not int or total < 1:
        raise _error(
            "browse_snapshot_count_invalid", "atomic catalog total must be positive"
        )
    return range(0, total, page_size)


def _crop_payload_checked(
    payload: CandidateCropPayload, descriptor: Mapping[str, Any], route: str
) -> CandidateCropPayload:
    if not isinstance(payload, CandidateCropPayload):
        raise _error(
            "browse_snapshot_crop_invalid", "crop reader returned an invalid payload"
        )
    observed = _sha256(payload.data)
    expected_hash = descriptor.get("sha256") or descriptor.get("crop_sha256")
    expected_bytes = descriptor.get("bytes")
    if (
        payload.content_type != "image/png"
        or observed != payload.sha256
        or expected_hash != observed
        or (expected_bytes is not None and expected_bytes != len(payload.data))
        or not _valid_png(payload.data)
    ):
        raise _error(
            "browse_snapshot_crop_binding_mismatch",
            f"crop bytes do not match the detail descriptor: {route}",
        )
    return payload


def _decorate_master_visual_scan_item(
    item: dict[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Mirror the existing read-only service decoration without importing it."""

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


def _master_atomic_detail_projections(
    reader: MasterWave1WorkbenchReader, node_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Project all Master details while computing the 169-answer index once."""

    snapshot = reader._snapshot()
    reference_answers = reader._exact_reference_answer_index(snapshot)
    integrity = reader._integrity(snapshot)
    product_id = reader.status()["product_id"]
    result: dict[str, dict[str, Any]] = {}
    for node_id in node_ids:
        atom = snapshot.master_nodes.get(("atomic_part", node_id))
        if atom is None:
            raise _error(
                "browse_snapshot_catalog_incomplete",
                "Master catalog node disappeared while details were projected",
            )
        rows = snapshot.relations_by_master.get(node_id, [])
        reference_answer = {
            "availability": ABSENT,
            "reference_answer_text": None,
            "source_authority": NONE,
            "independently_verified": False,
            "quality_note": (
                "该主索引节点没有安全的Wave1 exact映射；不继承答案或题面像素。"
            ),
        }
        if len(rows) == 1:
            relation = rows[0]
            if (
                relation.get("relation_type") == "exact_1_to_1"
                and relation.get("identity_mapping_allowed") is True
            ):
                reference_answer = deepcopy(reference_answers[node_id])
        result[node_id] = {
            "product_id": product_id,
            "scope": "candidate_only_read_only_master_atomic_workbench",
            "node": reader._master_item(atom, snapshot),
            "crosswalk_relation": reader._relation_detail(rows),
            "wave1_candidate_overlay": reader._wave_overlay(rows, snapshot),
            "reference_answer": reference_answer,
            "authority": dict(MASTER_AUTHORITY),
            "integrity": deepcopy(integrity),
        }
    return result


def _pin_validated_snapshot(reader: Any) -> Any:
    """Validate once inside one capture and reuse that exact in-memory snapshot."""

    snapshot = reader._snapshot()
    reader._snapshot = lambda snapshot=snapshot: snapshot
    return snapshot


def _theme_atomic_occurrences(value: Any, scope: str) -> dict[str, int]:
    if not isinstance(value, dict) or value.get("scope") != scope:
        raise _error(
            "browse_snapshot_curriculum_join_invalid",
            "curriculum closure received an incompatible theme projection",
        )
    papers = value.get("papers")
    if not isinstance(papers, list):
        raise _error(
            "browse_snapshot_curriculum_join_invalid",
            "curriculum closure theme papers are invalid",
        )
    counts: dict[str, int] = {}

    def add_chain(chain: Any) -> None:
        if not isinstance(chain, list):
            raise _error(
                "browse_snapshot_curriculum_join_invalid",
                "curriculum closure theme chain is invalid",
            )
        for atom in chain:
            atomic_id = atom.get("atomic_part_id") if isinstance(atom, dict) else None
            if not isinstance(atomic_id, str) or _IDENTIFIER.fullmatch(atomic_id) is None:
                raise _error(
                    "browse_snapshot_curriculum_join_invalid",
                    "curriculum closure theme atomic identity is invalid",
                )
            counts[atomic_id] = counts.get(atomic_id, 0) + 1

    for paper in papers:
        groups = paper.get("theme_groups") if isinstance(paper, dict) else None
        if not isinstance(groups, list):
            raise _error(
                "browse_snapshot_curriculum_join_invalid",
                "curriculum closure theme groups are invalid",
            )
        for group in groups:
            add_chain(group.get("atomic_chain") if isinstance(group, dict) else None)
    pending = value.get("unassigned_pending_review")
    if isinstance(pending, dict):
        add_chain(pending.get("atomic_chain", []))
    return counts


def _curriculum_object(
    value: Any, expected_keys: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise _error(
            "browse_snapshot_curriculum_invalid",
            f"frozen curriculum {label} has an incompatible object shape",
        )
    return value


def _curriculum_text(value: Any, label: str, *, identifier: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 320
        or (identifier and _IDENTIFIER.fullmatch(value) is None)
    ):
        raise _error(
            "browse_snapshot_curriculum_invalid",
            f"frozen curriculum {label} is invalid",
        )
    return value


def _curriculum_stats(value: Any, label: str) -> dict[str, int]:
    stats = _curriculum_object(value, _CURRICULUM_STATS_KEYS, label)
    if any(type(count) is not int or count < 0 for count in stats.values()):
        raise _error(
            "browse_snapshot_curriculum_invalid",
            f"frozen curriculum {label} contains an invalid count",
        )
    return stats


def _empty_curriculum_stats() -> dict[str, Any]:
    return {
        "mapped_ids": set(),
        "complete_ids": set(),
        "partial_ids": set(),
        "blocked_ids": set(),
        "mapped_entry_count": 0,
        "blocked_entry_count": 0,
    }


def _public_curriculum_stats(value: Mapping[str, Any]) -> dict[str, int]:
    return {
        "mapped_atomic_count": len(value["mapped_ids"]),
        "complete_atomic_count": len(value["complete_ids"]),
        "partial_atomic_count": len(value["partial_ids"]),
        "blocked_atomic_count": len(value["blocked_ids"]),
        "mapped_entry_count": value["mapped_entry_count"],
        "blocked_entry_count": value["blocked_entry_count"],
    }


def _validate_curriculum_dtos(
    catalog: Any, mapping_index: Any
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    catalog = _curriculum_object(
        catalog, _CURRICULUM_CATALOG_KEYS, "catalog"
    )
    mapping_index = _curriculum_object(
        mapping_index, _CURRICULUM_MAPPING_INDEX_KEYS, "mapping index"
    )
    data_snapshot_id = _curriculum_text(
        catalog.get("data_snapshot_id"), "data snapshot", identifier=True
    )
    if (
        catalog.get("schema_version") != CURRICULUM_SCHEMA_VERSION
        or mapping_index.get("schema_version") != CURRICULUM_SCHEMA_VERSION
        or catalog.get("scope") != CURRICULUM_SCOPE
        or mapping_index.get("scope") != CURRICULUM_SCOPE
        or mapping_index.get("data_snapshot_id") != data_snapshot_id
        or catalog.get("title_zh") != "教材章节工作台"
        or not isinstance(catalog.get("coverage"), dict)
        or catalog.get("coverage") != mapping_index.get("coverage")
        or catalog.get("authority") != dict(CURRICULUM_AUTHORITY)
        or mapping_index.get("authority") != dict(CURRICULUM_AUTHORITY)
        or catalog.get("integrity") != _CURRICULUM_INTEGRITY
        or mapping_index.get("integrity") != _CURRICULUM_INTEGRITY
    ):
        raise _error(
            "browse_snapshot_curriculum_invalid",
            "frozen curriculum catalog and mapping index disagree",
        )

    catalog_counts = _curriculum_object(
        catalog.get("counts"), _CURRICULUM_CATALOG_COUNT_KEYS, "catalog counts"
    )
    index_counts = _curriculum_object(
        mapping_index.get("counts"),
        _CURRICULUM_INDEX_COUNT_KEYS,
        "mapping-index counts",
    )
    if any(
        type(count) is not int or count < 0
        for count in (*catalog_counts.values(), *index_counts.values())
    ):
        raise _error(
            "browse_snapshot_curriculum_invalid",
            "frozen curriculum aggregate counts are invalid",
        )

    volumes_value = catalog.get("volumes")
    records_value = mapping_index.get("records")
    if not isinstance(volumes_value, list) or not isinstance(records_value, list):
        raise _error(
            "browse_snapshot_curriculum_invalid",
            "frozen curriculum catalog or mapping records are invalid",
        )
    volumes: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    volume_by_id: dict[str, dict[str, Any]] = {}
    chapter_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    section_by_key: dict[str, tuple[str, str, dict[str, Any]]] = {}
    section_ids: set[str] = set()
    catalog_node_stats: dict[tuple[str, str], dict[str, int]] = {}

    for raw_volume in volumes_value:
        volume = _curriculum_object(
            raw_volume, _CURRICULUM_VOLUME_KEYS, "volume"
        )
        volume_id = _curriculum_text(
            volume["volume_id"], "volume_id", identifier=True
        )
        volume_title = _curriculum_text(volume["volume_title"], "volume title")
        raw_chapters = volume["chapters"]
        if (
            volume_id in volume_by_id
            or volume["display_label_zh"] != volume_title
            or volume["edition_or_printing"] is not None
            or volume["edition_status"] != CURRICULUM_EDITION_STATUS_UNKNOWN
            or not isinstance(raw_chapters, list)
            or not all(
                isinstance(volume[key], str) and volume[key]
                for key in ("textbook_family", "publisher", "evidence_level")
            )
        ):
            raise _error(
                "browse_snapshot_curriculum_invalid",
                "frozen curriculum volume identity is invalid or duplicated",
            )
        volume_stats = _curriculum_stats(
            volume["mapping_counts"], "volume mapping counts"
        )
        volume_by_id[volume_id] = volume
        catalog_node_stats[("volume", volume_id)] = volume_stats
        volume_sections = 0
        for raw_chapter in raw_chapters:
            chapter = _curriculum_object(
                raw_chapter, _CURRICULUM_CHAPTER_KEYS, "chapter"
            )
            chapter_id = _curriculum_text(
                chapter["chapter_id"], "chapter_id", identifier=True
            )
            chapter_title = _curriculum_text(
                chapter["chapter_title"], "chapter title"
            )
            raw_sections = chapter["sections"]
            if (
                chapter_id in chapter_by_id
                or chapter["display_label_zh"] != chapter_title
                or not isinstance(raw_sections, list)
                or type(chapter["section_count"]) is not int
                or chapter["section_count"] != len(raw_sections)
            ):
                raise _error(
                    "browse_snapshot_curriculum_invalid",
                    "frozen curriculum chapter identity or count is invalid",
                )
            chapter_stats = _curriculum_stats(
                chapter["mapping_counts"], "chapter mapping counts"
            )
            chapter_by_id[chapter_id] = (volume_id, chapter)
            catalog_node_stats[("chapter", chapter_id)] = chapter_stats
            for raw_section in raw_sections:
                section_node = _curriculum_object(
                    raw_section, _CURRICULUM_SECTION_KEYS, "section"
                )
                section_key = _curriculum_text(
                    section_node["section_key"], "section_key", identifier=True
                )
                section_number = _curriculum_text(
                    section_node["section_number"], "section number"
                )
                section_title = _curriculum_text(
                    section_node["section_title"], "section title"
                )
                section_id = section_node["section_id"]
                if section_id is not None:
                    section_id = _curriculum_text(
                        section_id, "section_id", identifier=True
                    )
                if (
                    section_key in section_by_key
                    or (section_id is not None and section_id in section_ids)
                    or re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}", section_number)
                    is None
                    or section_node["display_label_zh"]
                    != f"{section_number} {section_title}"
                    or section_node["unit_id"] is not None
                    or section_node["unit_title"] is not None
                    or section_node["unit_status"] != CURRICULUM_UNIT_STATUS_UNKNOWN
                ):
                    raise _error(
                        "browse_snapshot_curriculum_invalid",
                        "frozen curriculum section identity is invalid or duplicated",
                    )
                section_stats = _curriculum_stats(
                    section_node["mapping_counts"], "section mapping counts"
                )
                section_by_key[section_key] = (
                    volume_id,
                    chapter_id,
                    section_node,
                )
                if section_id is not None:
                    section_ids.add(section_id)
                catalog_node_stats[("section", section_key)] = section_stats
                sections.append(section_node)
            volume_sections += len(raw_sections)
            chapters.append(chapter)
        if (
            type(volume["chapter_count"]) is not int
            or volume["chapter_count"] != len(raw_chapters)
            or type(volume["section_count"]) is not int
            or volume["section_count"] != volume_sections
        ):
            raise _error(
                "browse_snapshot_curriculum_invalid",
                "frozen curriculum volume descendant counts are invalid",
            )
        volumes.append(volume)

    records: list[dict[str, Any]] = []
    atomic_ids: set[str] = set()
    entry_identities: set[tuple[Any, ...]] = set()
    node_stats: defaultdict[tuple[str, str], dict[str, Any]] = defaultdict(
        _empty_curriculum_stats
    )
    global_stats = _empty_curriculum_stats()
    entry_count = 0
    for raw_record in records_value:
        record = _curriculum_object(
            raw_record, _CURRICULUM_MAPPING_RECORD_KEYS, "mapping record"
        )
        atomic_id = _curriculum_text(
            record["atomic_id"], "atomic_id", identifier=True
        )
        source_layer = _curriculum_text(
            record["source_layer"], "source_layer", identifier=True
        )
        _curriculum_text(record["source_batch"], "source_batch", identifier=True)
        raw_entries = record["entries"]
        if (
            atomic_id in atomic_ids
            or source_layer
            not in {"master_direct_active", "supplemental_wechat_active"}
            or record["mapping_status"] not in {"complete", "partial"}
            or not isinstance(raw_entries, list)
            or not raw_entries
        ):
            raise _error(
                "browse_snapshot_curriculum_invalid",
                "frozen curriculum mapping record is invalid or duplicated",
            )
        atomic_ids.add(atomic_id)
        global_stats["mapped_ids"].add(atomic_id)
        global_stats[f"{record['mapping_status']}_ids"].add(atomic_id)
        record_has_blocked = False
        record_has_mapped = False
        for raw_entry in raw_entries:
            entry = _curriculum_object(
                raw_entry, _CURRICULUM_MAPPING_ENTRY_KEYS, "mapping entry"
            )
            knowledge_tag = _curriculum_text(
                entry["knowledge_tag"], "knowledge tag", identifier=True
            )
            volume_id = _curriculum_text(
                entry["volume_id"], "entry volume_id", identifier=True
            )
            chapter_id = _curriculum_text(
                entry["chapter_id"], "entry chapter_id", identifier=True
            )
            volume = volume_by_id.get(volume_id)
            chapter_binding = chapter_by_id.get(chapter_id)
            if (
                volume is None
                or chapter_binding is None
                or chapter_binding[0] != volume_id
                or entry["volume_title"] != volume["volume_title"]
                or entry["chapter_title"]
                != chapter_binding[1]["chapter_title"]
                or entry["knowledge_role"] not in {"primary", "supporting"}
                or entry["evidence_level"] != "L1_LOCAL_TEXTBOOK"
                or entry["unit_id"] is not None
                or entry["unit_title"] is not None
                or entry["unit_status"] != CURRICULUM_UNIT_STATUS_UNKNOWN
                or entry["edition_or_printing"] is not None
                or entry["edition_status"] != CURRICULUM_EDITION_STATUS_UNKNOWN
                or entry["mapping_status"] not in {"mapped", "blocked"}
            ):
                raise _error(
                    "browse_snapshot_curriculum_invalid",
                    "frozen curriculum mapping entry identity is invalid",
                )
            blocked = entry["mapping_status"] == "blocked"
            if blocked:
                if (
                    entry["relation"] != "candidate_knowledge_chapter_only"
                    or entry["evidence_status"] != "blocked_pending_review"
                    or any(
                        entry[key] is not None
                        for key in (
                            "section_key",
                            "section_id",
                            "section_number",
                            "section_title",
                        )
                    )
                ):
                    raise _error(
                        "browse_snapshot_curriculum_invalid",
                        "frozen curriculum blocked entry fabricates section precision",
                    )
                record_has_blocked = True
                node_keys = (("volume", volume_id), ("chapter", chapter_id))
            else:
                section_key = _curriculum_text(
                    entry["section_key"], "entry section_key", identifier=True
                )
                section_binding = section_by_key.get(section_key)
                if (
                    entry["relation"] != "direct_directory_alignment"
                    or entry["evidence_status"]
                    not in {
                        "toc_direct_directory_mapping",
                        "direct_visual_directory_mapping",
                    }
                    or section_binding is None
                    or section_binding[:2] != (volume_id, chapter_id)
                    or (
                        entry["section_id"] is not None
                        and entry["section_id"]
                        != section_binding[2]["section_id"]
                    )
                    or entry["section_number"]
                    != section_binding[2]["section_number"]
                    or entry["section_title"]
                    != section_binding[2]["section_title"]
                ):
                    raise _error(
                        "browse_snapshot_curriculum_invalid",
                        "frozen curriculum mapped entry does not match the catalog",
                    )
                record_has_mapped = True
                node_keys = (
                    ("volume", volume_id),
                    ("chapter", chapter_id),
                    ("section", section_key),
                )
            identity = (
                atomic_id,
                knowledge_tag,
                entry["knowledge_role"],
                entry["mapping_status"],
                volume_id,
                chapter_id,
                entry["section_key"],
            )
            if identity in entry_identities:
                raise _error(
                    "browse_snapshot_curriculum_invalid",
                    "frozen curriculum mapping entry is duplicated",
                )
            entry_identities.add(identity)
            entry_count += 1
            if blocked:
                global_stats["blocked_entry_count"] += 1
                for key in node_keys:
                    node_stats[key]["blocked_ids"].add(atomic_id)
                    node_stats[key]["blocked_entry_count"] += 1
            else:
                global_stats["mapped_entry_count"] += 1
                for key in node_keys:
                    node_stats[key]["mapped_ids"].add(atomic_id)
                    node_stats[key][f"{record['mapping_status']}_ids"].add(
                        atomic_id
                    )
                    node_stats[key]["mapped_entry_count"] += 1
        if record_has_blocked:
            global_stats["blocked_ids"].add(atomic_id)
        expected_status = "partial" if record_has_blocked else "complete"
        if not record_has_mapped or record["mapping_status"] != expected_status:
            raise _error(
                "browse_snapshot_curriculum_invalid",
                "frozen curriculum record status disagrees with its entries",
            )
        records.append(record)

    public_global = _public_curriculum_stats(global_stats)
    expected_index_counts = {
        "active_atomic_mappings": len(records),
        "mapping_entries": entry_count,
        **public_global,
    }
    expected_catalog_counts = {
        "volumes": len(volumes),
        "chapters": len(chapters),
        "sections": len(sections),
        "section_ids_known": len(section_ids),
        "section_ids_unknown": len(sections) - len(section_ids),
        **expected_index_counts,
    }
    expected_global_stats = {
        "mapped_atomic_count": CURRICULUM_ACTIVE_ATOMIC_COUNT,
        "complete_atomic_count": CURRICULUM_COMPLETE_ATOMIC_COUNT,
        "partial_atomic_count": CURRICULUM_PARTIAL_ATOMIC_COUNT,
        "blocked_atomic_count": CURRICULUM_BLOCKED_ATOMIC_COUNT,
        "mapped_entry_count": (
            CURRICULUM_MAPPING_ENTRY_COUNT - CURRICULUM_BLOCKED_ENTRY_COUNT
        ),
        "blocked_entry_count": CURRICULUM_BLOCKED_ENTRY_COUNT,
    }
    if (
        public_global != expected_global_stats
        or index_counts != expected_index_counts
        or catalog_counts != expected_catalog_counts
    ):
        raise _error(
            "browse_snapshot_curriculum_count_mismatch",
            "frozen curriculum aggregate counts disagree with its records",
        )
    for key, actual in catalog_node_stats.items():
        expected = _public_curriculum_stats(
            node_stats.get(key, _empty_curriculum_stats())
        )
        if actual != expected:
            raise _error(
                "browse_snapshot_curriculum_count_mismatch",
                "frozen curriculum node counts disagree with its records",
            )
    return volumes, chapters, sections, records


def _assert_curriculum_closure(
    parsed_routes: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    present = _REQUIRED_CURRICULUM_ROUTES & set(parsed_routes)
    if not present:
        return {}
    if present != _REQUIRED_CURRICULUM_ROUTES:
        raise _error(
            "browse_snapshot_curriculum_incomplete",
            "curriculum catalog and mapping index must be frozen together",
        )
    volumes, chapters, sections, records = _validate_curriculum_dtos(
        parsed_routes[CURRICULUM_CATALOG_ROUTE],
        parsed_routes[CURRICULUM_MAPPING_INDEX_ROUTE],
    )
    if (
        len(volumes) != CURRICULUM_VOLUME_COUNT
        or len(chapters) != CURRICULUM_CHAPTER_COUNT
        or len(sections) != CURRICULUM_SECTION_COUNT
        or len(records) != CURRICULUM_ACTIVE_ATOMIC_COUNT
    ):
        raise _error(
            "browse_snapshot_curriculum_count_mismatch",
            "frozen curriculum counts are not exactly 5/19/60/87/163",
        )

    by_scope: dict[str, set[str]] = {
        "wave1": set(),
        "master": set(),
        "supplemental": set(),
    }
    source_scope = {
        "master_direct_active": "master",
        "supplemental_wechat_active": "supplemental",
    }
    all_ids: set[str] = set()
    entry_count = 0
    for record in records:
        atomic_id = record.get("atomic_id") if isinstance(record, dict) else None
        source_layer = record.get("source_layer") if isinstance(record, dict) else None
        entries = record.get("entries") if isinstance(record, dict) else None
        scope = source_scope.get(source_layer)
        if (
            not isinstance(atomic_id, str)
            or _IDENTIFIER.fullmatch(atomic_id) is None
            or scope is None
            or not isinstance(entries, list)
            or not entries
            or atomic_id in all_ids
        ):
            raise _error(
                "browse_snapshot_curriculum_invalid",
                "frozen curriculum mapping identity is invalid or duplicated",
            )
        all_ids.add(atomic_id)
        by_scope[scope].add(atomic_id)
        entry_count += len(entries)
    if (
        len(all_ids) != CURRICULUM_ACTIVE_ATOMIC_COUNT
        or entry_count != CURRICULUM_MAPPING_ENTRY_COUNT
        or len(by_scope["wave1"]) != 0
        or len(by_scope["master"]) != CURRICULUM_ACTIVE_MASTER_COUNT
        or len(by_scope["supplemental"]) != CURRICULUM_ACTIVE_SUPPLEMENTAL_COUNT
    ):
        raise _error(
            "browse_snapshot_curriculum_count_mismatch",
            "frozen curriculum scope counts are not Wave1 0, Master 30, Supplemental 57",
        )

    theme_occurrences = {
        scope: _theme_atomic_occurrences(
            parsed_routes[f"/api/v1/kb/workbench/theme-groups?scope={scope}"],
            scope,
        )
        for scope in ("wave1", "master", "supplemental")
    }
    for scope, atomic_ids in by_scope.items():
        for atomic_id in atomic_ids:
            if theme_occurrences[scope].get(atomic_id) != 1 or any(
                theme_occurrences[other].get(atomic_id, 0) != 0
                for other in theme_occurrences
                if other != scope
            ):
                raise _error(
                    "browse_snapshot_curriculum_join_invalid",
                    "each curriculum mapping must join exactly one theme in its declared scope",
                )
    return {
        "volumes": CURRICULUM_VOLUME_COUNT,
        "chapters": CURRICULUM_CHAPTER_COUNT,
        "sections": CURRICULUM_SECTION_COUNT,
        "atomic_mappings": CURRICULUM_ACTIVE_ATOMIC_COUNT,
        "mapping_entries": CURRICULUM_MAPPING_ENTRY_COUNT,
        "atomic_mappings_by_scope": {
            scope: len(ids) for scope, ids in by_scope.items()
        },
    }


class LiveBrowseProjectionSource:
    """Capture the currently validated live readers without opening dynamic state."""

    def __init__(self, shchem_root: Path, overlay_root: Path):
        self.shchem_root = shchem_root.absolute()
        self.overlay_root = overlay_root.absolute()

    @staticmethod
    def _add_binary(
        routes: dict[str, CandidateCropPayload],
        route: str,
        payload: CandidateCropPayload,
    ) -> None:
        _route_allowed(route)
        existing = routes.get(route)
        if existing is not None and (
            existing.sha256 != payload.sha256 or existing.data != payload.data
        ):
            raise _error(
                "browse_snapshot_route_collision",
                "one crop route resolved to different bytes",
            )
        routes[route] = payload

    @staticmethod
    def _add_json(routes: dict[str, Any], route: str, value: Any) -> None:
        _route_allowed(route)
        _reject_dynamic_value(value)
        if route in routes and _canonical_json_bytes(routes[route]) != _canonical_json_bytes(value):
            raise _error(
                "browse_snapshot_route_collision",
                "one JSON route resolved to different projections",
            )
        routes[route] = deepcopy(value)

    def capture(self) -> BrowseCapture:
        try:
            master = MasterWave1WorkbenchReader(self.shchem_root)
            direct = MasterDirectVisualScanReader(
                self.shchem_root, master_workbench=master
            )
            alias = MasterVisualScanAliasReader(
                self.shchem_root, master_workbench=master
            )
            wave_scan = QuestionVisualScanReader(self.shchem_root)
            supplemental = SupplementalVisualScanReader(self.shchem_root)
            wave = Wave1CandidateReviewReader(self.shchem_root)
            # Reader endpoints normally revalidate live files per HTTP call.
            # A materialization pass instead validates every independent reader
            # once, pins that exact observation, and then performs a second
            # fresh full pass before returning any closure bytes.
            _pin_validated_snapshot(master)
            for registration in direct._reader_registrations:
                _pin_validated_snapshot(getattr(direct, registration.attribute))
            _pin_validated_snapshot(alias)
            for batch_reader in wave_scan._batch_readers:
                _pin_validated_snapshot(batch_reader)
            _pin_validated_snapshot(wave)
            themes = ThemeWorkbenchReader(
                self.shchem_root,
                master_workbench=master,
                direct_scans=direct,
                wave_scans=wave_scan,
            )
            processing_progress = QuestionProcessingProgressReader(
                self.shchem_root,
                theme_workbench=themes,
                master_workbench=master,
                wave_review=wave,
            )
            registry_reader = WorkbenchProductRegistryReader(
                self.shchem_root,
                self.overlay_root,
                theme_workbench=themes,
                supplemental_visual_scans=supplemental,
            )
            curriculum = CurriculumWorkbenchReader(self.shchem_root)
            _pin_validated_snapshot(curriculum)
            material_intake = MaterialIntakeWorkbenchReader(self.shchem_root)
            # Validate the complete transitive ledger once per capture pass and
            # pin that exact observation while all public projections are made.
            # A second fresh capture pass is still required by ``materialize``.
            intake_snapshot = material_intake._load()
            material_intake._load = lambda snapshot=intake_snapshot: snapshot

            json_routes: dict[str, Any] = {}
            binary_routes: dict[str, CandidateCropPayload] = {}

            registry = registry_reader.registry()
            readiness = registry_reader.readiness()
            self._add_json(
                json_routes, "/api/v1/workbench/product-registry", registry
            )
            self._add_json(json_routes, "/api/v1/readiness", readiness)
            captured_themes: dict[str, dict[str, Any]] = {}
            for scope in PRODUCT_ORDER:
                theme_payload = registry_reader.theme_groups(scope)
                captured_themes[scope] = theme_payload
                self._add_json(
                    json_routes,
                    f"/api/v1/kb/workbench/theme-groups?scope={scope}",
                    theme_payload,
                )
            self._add_json(
                json_routes,
                CURRICULUM_CATALOG_ROUTE,
                curriculum.catalog(),
            )
            self._add_json(
                json_routes,
                CURRICULUM_MAPPING_INDEX_ROUTE,
                curriculum.mapping_index(),
            )
            curriculum_counts = _assert_curriculum_closure(json_routes)
            for scope in ("wave1", "master"):
                progress = processing_progress.list_progress(
                    scope=scope,
                    paper_id=None,
                    theme_id=None,
                    gap=None,
                    limit=QUESTION_PROGRESS_CAPTURE_LIMIT,
                    offset=0,
                    theme_data=captured_themes[scope],
                )
                if progress.get("count") != progress.get("total"):
                    raise _error(
                        "browse_snapshot_question_progress_incomplete",
                        "question-processing progress capture is incomplete",
                    )
                self._add_json(
                    json_routes,
                    (
                        "/api/v1/kb/question-processing-progress"
                        f"?limit={QUESTION_PROGRESS_CAPTURE_LIMIT}&offset=0&scope={scope}"
                    ),
                    progress,
                )

            intake_status = material_intake.status()
            intake_batches = material_intake.list_batches()
            self._add_json(json_routes, "/api/v1/intake/status", intake_status)
            self._add_json(json_routes, "/api/v1/intake/batches", intake_batches)
            batch_items = intake_batches.get("items")
            if (
                not isinstance(batch_items, list)
                or intake_batches.get("total") != 3
                or len(batch_items) != 3
            ):
                raise _error(
                    "browse_snapshot_material_intake_incomplete",
                    "material intake batch inventory is not exactly three batches",
                )
            intake_batch_ids: list[str] = []
            for item in batch_items:
                batch_id = item.get("batch_id") if isinstance(item, dict) else None
                if (
                    not isinstance(batch_id, str)
                    or not batch_id
                    or batch_id in intake_batch_ids
                ):
                    raise _error(
                        "browse_snapshot_material_intake_incomplete",
                        "material intake batch IDs are missing or duplicated",
                    )
                intake_batch_ids.append(batch_id)
                self._add_json(
                    json_routes,
                    f"/api/v1/intake/batches/{_encoded(batch_id)}",
                    material_intake.batch_detail(batch_id),
                )

            intake_total = int(
                intake_status.get("counts", {}).get(
                    "entity_record_count_non_additive", -1
                )
            )
            if intake_total < 1:
                raise _error(
                    "browse_snapshot_material_intake_incomplete",
                    "material intake record total is missing",
                )
            intake_record_ids: list[str] = []
            intake_record_pages = 0
            for offset in _pagination_offsets(intake_total):
                page = material_intake.list_records(
                    kind=None,
                    stage=None,
                    status=None,
                    query=None,
                    limit=200,
                    offset=offset,
                )
                self._add_json(
                    json_routes,
                    f"/api/v1/intake/records?limit=200&offset={offset}",
                    page,
                )
                intake_record_pages += 1
                intake_record_ids.extend(
                    str(item.get("record_id"))
                    for item in page.get("items", ())
                    if isinstance(item, dict)
                )
            if (
                len(intake_record_ids) != intake_total
                or len(set(intake_record_ids)) != intake_total
                or "None" in intake_record_ids
            ):
                raise _error(
                    "browse_snapshot_material_intake_incomplete",
                    "material intake record pagination is incomplete or duplicated",
                )

            wave_status = wave.status()
            wave_total = int(wave_status["counts"]["atomic_parts"])
            self._add_json(
                json_routes,
                "/api/v1/kb/sources/candidate_review_only/wave1/status",
                wave_status,
            )
            wave_items: list[dict[str, Any]] = []
            for offset in _pagination_offsets(wave_total):
                page = wave.list_nodes(
                    node_type="atomic_part",
                    query=None,
                    paper_id=None,
                    theme_id=None,
                    printed_question_id=None,
                    limit=200,
                    offset=offset,
                )
                route = (
                    "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
                    f"?limit=200&offset={offset}&node_type=atomic_part"
                )
                self._add_json(json_routes, route, page)
                wave_items.extend(page["items"])
            wave_ids = [str(item["node_id"]) for item in wave_items]
            if len(wave_ids) != wave_total or len(wave_ids) != len(set(wave_ids)):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "Wave1 atomic pagination is incomplete or duplicated",
                )
            wave_all_total = sum(
                int(wave_status["counts"][key])
                for key in (
                    "papers",
                    "theme_big_questions",
                    "printed_questions",
                    "atomic_parts",
                )
            )
            wave_all_items: list[dict[str, Any]] = []
            for offset in _pagination_offsets(wave_all_total):
                page = wave.list_nodes(
                    node_type=None,
                    query=None,
                    paper_id=None,
                    theme_id=None,
                    printed_question_id=None,
                    limit=200,
                    offset=offset,
                )
                route = (
                    "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
                    f"?limit=200&offset={offset}"
                )
                self._add_json(json_routes, route, page)
                wave_all_items.extend(page["items"])
            wave_all_keys = [
                (str(item["node_type"]), str(item["node_id"]))
                for item in wave_all_items
            ]
            if (
                len(wave_all_keys) != wave_all_total
                or len(wave_all_keys) != len(set(wave_all_keys))
                or {node_id for node_type, node_id in wave_all_keys if node_type == "atomic_part"}
                != set(wave_ids)
            ):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "Wave1 full hierarchy pagination is incomplete or duplicated",
                )

            scan_status = wave_scan.status()
            scan_catalog = wave_scan.catalog()
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/question-visual-scans/status",
                scan_status,
            )
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/question-visual-scans/catalog",
                scan_catalog,
            )
            if set(scan_catalog.get("node_ids", ())) != set(wave_ids):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "Wave1 visual-scan catalog does not close over atomic IDs",
                )
            for node_id in wave_ids:
                base_route = (
                    "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
                    f"atomic_part/{_encoded(node_id)}"
                )
                base = wave.node("atomic_part", node_id)
                visual_route = (
                    "/api/v1/kb/workbench/question-visual-scans/"
                    f"{_encoded(node_id)}"
                )
                visual = wave_scan.detail(node_id)
                self._add_json(json_routes, base_route, base)
                self._add_json(json_routes, visual_route, visual)
                for descriptor in base.get("crop_refs", {}).get("items", []):
                    route = str(descriptor.get("image_endpoint", ""))
                    payload = _crop_payload_checked(
                        wave.question_crop(node_id, str(descriptor["crop_id"])),
                        descriptor,
                        route,
                    )
                    self._add_binary(binary_routes, route, payload)
            for node_type, node_id in wave_all_keys:
                if node_type == "atomic_part":
                    continue
                route = (
                    "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
                    f"{_encoded(node_type)}/{_encoded(node_id)}"
                )
                self._add_json(
                    json_routes, route, wave.node(node_type, node_id)
                )

            direct_status = direct.status()
            direct_catalog = direct.catalog()
            alias_status = alias.status()
            alias_catalog = alias.catalog()
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/master-direct-scans/status",
                direct_status,
            )
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/master-direct-scans/catalog",
                direct_catalog,
            )
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/master-visual-scan-aliases/status",
                alias_status,
            )
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
                alias_catalog,
            )

            master_status = master.status()
            master_total = int(master_status["counts"]["master_atomic_inventory"])
            base_master_pages: list[tuple[int, dict[str, Any]]] = []
            master_items: list[dict[str, Any]] = []
            for offset in _pagination_offsets(master_total):
                page = master.list_atomic(limit=200, offset=offset)
                base_master_pages.append((offset, page))
                master_items.extend(page["items"])
            master_ids = [str(item["node_id"]) for item in master_items]
            if len(master_ids) != master_total or len(master_ids) != len(set(master_ids)):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "Master atomic pagination is incomplete or duplicated",
                )
            exact_ids = {
                str(item["node_id"])
                for item in master_items
                if item.get("crosswalk_summary", {}).get("state") == "exact"
            }
            direct_ids = {
                str(value) for value in direct_catalog.get("master_node_ids", ())
            }
            alias_items = {
                str(item["master_node_id"]): item
                for item in alias_catalog.get("items", ())
                if isinstance(item, dict) and isinstance(item.get("master_node_id"), str)
            }
            alias_ids = set(alias_items)
            master_set = set(master_ids)
            if not direct_ids <= master_set or not alias_ids <= master_set:
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "visual-scan catalog contains an unknown Master node",
                )
            visible_ids = exact_ids | direct_ids | alias_ids
            coverage = {
                "master_atomic_inventory": master_total,
                "wave1_exact_visual_scanned": len(exact_ids),
                "direct_master_visual_scanned": len(direct_ids),
                "alias_existing_visual_scanned": len(alias_ids),
                "visual_scanned_master_atomic": len(visible_ids),
                "remaining_unscanned": master_total - len(visible_ids),
                "direct_exact_overlap": len(direct_ids & exact_ids),
                "alias_overlap_with_exact_or_direct": len(
                    alias_ids & (exact_ids | direct_ids)
                ),
            }
            context = {
                "direct_ids": frozenset(direct_ids),
                "alias_items": alias_items,
                "coverage": coverage,
            }
            master_status["visual_scan_coverage"] = deepcopy(coverage)
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/master-atomic/status",
                master_status,
            )
            for offset, page in base_master_pages:
                page["items"] = [
                    _decorate_master_visual_scan_item(item, context)
                    for item in page["items"]
                ]
                page["visual_scan_coverage"] = deepcopy(coverage)
                self._add_json(
                    json_routes,
                    f"/api/v1/kb/workbench/master-atomic?limit=200&offset={offset}",
                    page,
                )
            master_details = _master_atomic_detail_projections(master, master_ids)
            for node_id in master_ids:
                detail = master_details[node_id]
                detail["node"] = _decorate_master_visual_scan_item(
                    detail["node"], context
                )
                detail["visual_scan_alias"] = (
                    alias.detail(node_id) if node_id in alias_ids else None
                )
                detail["visual_scan_coverage"] = deepcopy(coverage)
                self._add_json(
                    json_routes,
                    f"/api/v1/kb/workbench/master-atomic/{_encoded(node_id)}",
                    detail,
                )

            for node_id in sorted(direct_ids):
                detail = direct.detail(node_id)
                detail_route = (
                    "/api/v1/kb/workbench/master-direct-scans/"
                    f"{_encoded(node_id)}"
                )
                self._add_json(json_routes, detail_route, detail)
                for descriptor in detail.get("evidence_descriptors", []):
                    crop_id = str(descriptor["crop_id"])
                    route = (
                        "/api/v1/kb/workbench/master-direct-scans/"
                        f"{_encoded(node_id)}/question-crops/{_encoded(crop_id)}"
                    )
                    payload = _crop_payload_checked(
                        direct.question_crop(node_id, crop_id), descriptor, route
                    )
                    self._add_binary(binary_routes, route, payload)

            for node_id in sorted(alias_ids):
                detail = alias.detail(node_id)
                detail_route = (
                    "/api/v1/kb/workbench/master-visual-scan-aliases/"
                    f"{_encoded(node_id)}"
                )
                self._add_json(json_routes, detail_route, detail)
                descriptor = detail.get("question_evidence")
                if not isinstance(descriptor, dict):
                    raise _error(
                        "browse_snapshot_catalog_incomplete",
                        "Master alias detail has no question evidence",
                    )
                crop_id = str(descriptor["crop_id"])
                route = (
                    "/api/v1/kb/workbench/master-visual-scan-aliases/"
                    f"{_encoded(node_id)}/question-crops/{_encoded(crop_id)}"
                )
                payload = _crop_payload_checked(
                    alias.question_crop(node_id, crop_id), descriptor, route
                )
                self._add_binary(binary_routes, route, payload)

            supplemental_status = supplemental.status()
            supplemental_total = int(supplemental_status["counts"]["atomic_parts"])
            self._add_json(
                json_routes,
                "/api/v1/kb/workbench/supplemental-scans/status",
                supplemental_status,
            )
            supplemental_items: list[dict[str, Any]] = []
            for offset in _pagination_offsets(supplemental_total):
                page = supplemental.list_atomic(limit=200, offset=offset)
                self._add_json(
                    json_routes,
                    f"/api/v1/kb/workbench/supplemental-scans?limit=200&offset={offset}",
                    page,
                )
                supplemental_items.extend(page["items"])
            supplemental_ids = [str(item["node_id"]) for item in supplemental_items]
            if (
                len(supplemental_ids) != supplemental_total
                or len(supplemental_ids) != len(set(supplemental_ids))
            ):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "supplemental atomic pagination is incomplete or duplicated",
                )
            for node_id in supplemental_ids:
                detail = supplemental.detail(node_id)
                self._add_json(
                    json_routes,
                    f"/api/v1/kb/workbench/supplemental-scans/{_encoded(node_id)}",
                    detail,
                )
                for descriptor in detail.get("node", {}).get("crop_refs", {}).get(
                    "items", []
                ):
                    route = str(descriptor.get("image_endpoint", ""))
                    payload = _crop_payload_checked(
                        supplemental.question_crop(
                            node_id, str(descriptor["crop_id"])
                        ),
                        descriptor,
                        route,
                    )
                    self._add_binary(binary_routes, route, payload)

            counts = {
                "atomic_details_by_scope": {
                    "wave1": len(wave_ids),
                    "master": len(master_ids),
                    "supplemental": len(supplemental_ids),
                },
                "wave1_all_hierarchy_nodes": len(wave_all_keys),
                "source_status_routes": sum(
                    route.endswith("/status") for route in json_routes
                ),
                "catalog_routes": sum(
                    route.endswith("/catalog") for route in json_routes
                ),
                "theme_routes": sum(
                    "/theme-groups?scope=" in route for route in json_routes
                ),
                "master_crosswalk_atomic_records": len(master_ids),
                "direct_visual_details": len(direct_ids),
                "alias_visual_details": len(alias_ids),
                "question_crop_routes": len(binary_routes),
                "material_intake_records": len(intake_record_ids),
                "material_intake_batches": len(intake_batch_ids),
                "material_intake_batch_details": len(intake_batch_ids),
                "material_intake_record_pages": intake_record_pages,
                "curriculum_volumes": curriculum_counts["volumes"],
                "curriculum_chapters": curriculum_counts["chapters"],
                "curriculum_sections": curriculum_counts["sections"],
                "curriculum_atomic_mappings": curriculum_counts[
                    "atomic_mappings"
                ],
                "curriculum_mapping_entries": curriculum_counts[
                    "mapping_entries"
                ],
                "curriculum_atomic_mappings_by_scope": curriculum_counts[
                    "atomic_mappings_by_scope"
                ],
                "json_routes": len(json_routes),
            }
            identity = {
                "registry_id": registry.get("registry_id"),
                "ui_build_id": registry.get("ui_build_id"),
                "data_snapshot_id": registry.get("data_snapshot_id"),
                "registry_manifest_sha256": registry.get("manifest_sha256"),
                "api_contract_version": registry.get("api_contract_version"),
            }
            if (
                identity["registry_id"] != REGISTRY_ID
                or identity["api_contract_version"] != CONTRACT_VERSION
                or any(
                    not isinstance(identity[key], str)
                    or not _SHA256.fullmatch(str(identity[key]))
                    for key in (
                        "ui_build_id",
                        "data_snapshot_id",
                        "registry_manifest_sha256",
                    )
                )
            ):
                raise _error(
                    "browse_snapshot_identity_invalid",
                    "workbench registry returned an invalid release identity",
                )
            return BrowseCapture(
                json_routes=json_routes,
                binary_routes=binary_routes,
                counts=counts,
                identity=identity,
            )
        except WorkbenchReleaseSnapshotError:
            raise
        except Exception as exc:
            raise _error(
                "browse_snapshot_live_capture_failed",
                "validated live browse projections could not be materialized",
            ) from exc


def _resource_kind(route: str, media_type: str) -> str:
    if media_type == "image/png":
        return "question_or_shared_crop"
    if route == CURRICULUM_CATALOG_ROUTE:
        return "curriculum_catalog"
    if route == CURRICULUM_MAPPING_INDEX_ROUTE:
        return "curriculum_mapping_index"
    if route == "/api/v1/readiness":
        return "readiness"
    if route == "/api/v1/workbench/product-registry":
        return "product_registry"
    if route == "/api/v1/intake/status":
        return "material_intake_status"
    if route == "/api/v1/intake/batches":
        return "material_intake_batch_catalog"
    if route.startswith("/api/v1/intake/batches/"):
        return "material_intake_batch_detail"
    if urlsplit(route).path == "/api/v1/intake/records":
        return "material_intake_record_page"
    if "theme-groups" in route:
        return "theme_catalog"
    if route.endswith("/status"):
        return "source_status"
    if route.endswith("/catalog"):
        return "catalog_or_crosswalk_index"
    if "?limit=" in route:
        return "atomic_catalog_page"
    return "atomic_or_visual_detail"


def _object_path(raw: bytes, media_type: str) -> str:
    digest = _sha256(raw)
    if media_type == "application/json":
        return f"objects/json/{digest}.json"
    if media_type == "image/png":
        return f"objects/images/{digest}.png"
    raise _error(
        "browse_snapshot_media_type_invalid", "unsupported snapshot media type"
    )


def _ensure_unique_casefold_paths(paths: Iterable[str]) -> None:
    seen: dict[str, str] = {}
    for value in paths:
        normalized = _safe_relative(value).as_posix()
        folded = normalized.casefold()
        if folded in seen and seen[folded] != normalized:
            raise _error(
                "browse_snapshot_path_collision",
                "snapshot paths collide on a case-insensitive filesystem",
            )
        seen[folded] = normalized


def _collect_image_endpoints(value: Any) -> set[str]:
    endpoints: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "image_endpoint" and isinstance(nested, str):
                endpoints.add(nested)
            else:
                endpoints.update(_collect_image_endpoints(nested))
    elif isinstance(value, list):
        for nested in value:
            endpoints.update(_collect_image_endpoints(nested))
    return endpoints


def current_backend_build_identity(
    workspace_root: Path,
    *,
    openapi_relative: str | PurePosixPath = DEFAULT_OPENAPI_RELATIVE,
    backend_source_relatives: Iterable[str] = DEFAULT_BACKEND_SOURCE_RELATIVES,
) -> dict[str, Any]:
    """Hash the fixed backend allowlist without opening any question data.

    Launchers use this lightweight identity to make sure a frozen snapshot is
    interpreted by exactly the backend source generation that created it.
    """

    try:
        root = workspace_root.resolve(strict=True)
    except OSError as exc:
        raise _error(
            "browse_snapshot_workspace_missing", "workspace root is missing"
        ) from exc
    if not root.is_dir() or _link_like(root.lstat()):
        raise _error(
            "browse_snapshot_workspace_unsafe",
            "workspace root must be a real directory",
            403,
        )
    openapi = _safe_relative(openapi_relative)
    sources = tuple(
        _safe_relative(value).as_posix() for value in backend_source_relatives
    )
    if not sources or len(sources) != len({value.casefold() for value in sources}):
        raise _error(
            "browse_snapshot_backend_sources_invalid",
            "backend source allowlist is empty or duplicated",
            400,
        )
    _ensure_unique_casefold_paths(sources)
    openapi_raw = _read_stable_regular_file(root, openapi)
    source_descriptors = [
        _artifact_descriptor(relative, _read_stable_regular_file(root, relative))
        for relative in sorted(sources)
    ]
    build_id = _canonical_sha256(
        {
            "algorithm": BACKEND_BUILD_ALGORITHM,
            "api_contract_sha256": _sha256(openapi_raw),
            "sources": source_descriptors,
        }
    )
    return {
        "schema_version": "shchem.workbench.backend_build_identity.v1",
        "algorithm": BACKEND_BUILD_ALGORITHM,
        "backend_build_id": build_id,
        "api_contract_version": CONTRACT_VERSION,
        "api_contract_sha256": _sha256(openapi_raw),
        "api_contract_bytes": len(openapi_raw),
        "sources": source_descriptors,
        "dynamic_state_embedded": False,
        "candidate_browse_data_embedded_elsewhere": True,
    }


def current_backend_build_id(
    workspace_root: Path,
    *,
    openapi_relative: str | PurePosixPath = DEFAULT_OPENAPI_RELATIVE,
    backend_source_relatives: Iterable[str] = DEFAULT_BACKEND_SOURCE_RELATIVES,
) -> str:
    return str(
        current_backend_build_identity(
            workspace_root,
            openapi_relative=openapi_relative,
            backend_source_relatives=backend_source_relatives,
        )["backend_build_id"]
    )


class WorkbenchReleaseSnapshotMaterializer:
    """Build the same snapshot twice and return bytes only when both agree."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        capture_factory: Callable[[], BrowseProjectionSource] | None = None,
        overlay_relative: str | PurePosixPath = DEFAULT_OVERLAY_RELATIVE,
        openapi_relative: str | PurePosixPath = DEFAULT_OPENAPI_RELATIVE,
        schema_relative: str | PurePosixPath = DEFAULT_SCHEMA_RELATIVE,
        backend_source_relatives: Iterable[str] = DEFAULT_BACKEND_SOURCE_RELATIVES,
        review_catalog_factory: Callable[[], bytes] | None = None,
        material_intake_source_factory: (
            Callable[[], Mapping[str, bytes]] | None
        ) = None,
    ):
        try:
            self.workspace_root = workspace_root.resolve(strict=True)
        except OSError as exc:
            raise _error(
                "browse_snapshot_workspace_missing", "workspace root is missing"
            ) from exc
        if not self.workspace_root.is_dir() or _link_like(
            self.workspace_root.lstat()
        ):
            raise _error(
                "browse_snapshot_workspace_unsafe",
                "workspace root must be a real directory",
                403,
            )
        self.overlay_relative = _safe_relative(overlay_relative)
        self.openapi_relative = _safe_relative(openapi_relative)
        self.schema_relative = _safe_relative(schema_relative)
        self.backend_source_relatives = tuple(
            _safe_relative(value).as_posix() for value in backend_source_relatives
        )
        if not self.backend_source_relatives or len(
            self.backend_source_relatives
        ) != len({value.casefold() for value in self.backend_source_relatives}):
            raise _error(
                "browse_snapshot_backend_sources_invalid",
                "backend source allowlist is empty or duplicated",
                400,
            )
        _ensure_unique_casefold_paths(self.backend_source_relatives)
        if material_intake_source_factory is None:
            self.material_intake_source_factory = lambda: {
                key: _read_stable_regular_file(self.workspace_root, source_relative)
                for key, (_, source_relative) in MATERIAL_INTAKE_SOURCE_FILES.items()
            }
        else:
            self.material_intake_source_factory = material_intake_source_factory
        overlay_root = self.workspace_root.joinpath(*self.overlay_relative.parts)
        if capture_factory is None:
            shchem_root = self.workspace_root / "sh-chem-db"
            self.capture_factory = lambda: LiveBrowseProjectionSource(
                shchem_root, overlay_root
            )
            if review_catalog_factory is None:
                def live_review_catalog() -> bytes:
                    # Imported lazily so synthetic snapshot fixtures do not
                    # need the full Master review inventory.
                    from .public_kb import PublicKBReader
                    from .theme_review_workbench import ThemeReviewTaskCatalog

                    public_kb = PublicKBReader(shchem_root)
                    theme_reader = ThemeWorkbenchReader(shchem_root)
                    return ThemeReviewTaskCatalog.build_live(
                        public_kb, theme_reader
                    ).canonical_bytes()

                self.review_catalog_factory = live_review_catalog
            else:
                self.review_catalog_factory = review_catalog_factory
        else:
            self.capture_factory = capture_factory
            self.review_catalog_factory = review_catalog_factory or (
                lambda: _json_artifact_bytes(
                    {
                        "schema_version": "shchem.theme_review.task_catalog.fixture.v1",
                        "catalog_available": False,
                        "dynamic_state_embedded": False,
                        "candidate_only": True,
                        "human_reviewed": False,
                    }
                )
            )

    def _base_inputs(self) -> tuple[dict[str, bytes], dict[str, Any]]:
        overlay_fs = self.workspace_root.joinpath(*self.overlay_relative.parts)
        try:
            names = sorted(path.name for path in overlay_fs.iterdir())
        except OSError as exc:
            raise _error(
                "browse_snapshot_static_missing", "WebUI overlay is unavailable"
            ) from exc
        expected_names = sorted((*STATIC_FILES, "overlay.manifest.json"))
        if names != expected_names:
            raise _error(
                "browse_snapshot_static_inventory_invalid",
                "WebUI overlay contains a rogue or missing file",
            )

        artifacts: dict[str, bytes] = {}
        try:
            intake_source_values = dict(self.material_intake_source_factory())
        except WorkbenchReleaseSnapshotError:
            raise
        except Exception as exc:
            raise _error(
                "browse_snapshot_material_intake_source_failed",
                "material intake source closure could not be read",
            ) from exc
        if set(intake_source_values) != set(MATERIAL_INTAKE_SOURCE_FILES) or any(
            not isinstance(raw, bytes) or not raw
            for raw in intake_source_values.values()
        ):
            raise _error(
                "browse_snapshot_material_intake_source_invalid",
                "material intake source closure is missing or malformed",
            )
        material_intake_bindings: dict[str, dict[str, Any]] = {}
        for key, (artifact_relative, _) in MATERIAL_INTAKE_SOURCE_FILES.items():
            raw = intake_source_values[key]
            artifacts[artifact_relative] = raw
            material_intake_bindings[key] = _artifact_descriptor(
                artifact_relative, raw
            )
        static_raw: dict[str, bytes] = {}
        for name in STATIC_FILES:
            raw = _read_stable_regular_file(
                self.workspace_root, self.overlay_relative / name
            )
            static_raw[name] = raw
            artifacts[f"webui/{name}"] = raw
        inner_raw = _read_stable_regular_file(
            self.workspace_root, self.overlay_relative / "overlay.manifest.json"
        )
        outer_relative = self.overlay_relative.parent / "overlay.manifest.json"
        outer_raw = _read_stable_regular_file(self.workspace_root, outer_relative)
        inner = _strict_json_object(inner_raw, "inner overlay manifest")
        outer = _strict_json_object(outer_raw, "outer overlay manifest")
        for manifest in (inner, outer):
            if manifest.get("gateway_contract") != CONTRACT_VERSION:
                raise _error(
                    "browse_snapshot_contract_mismatch",
                    "overlay manifest uses an incompatible API contract",
                )
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != set(STATIC_FILES):
                raise _error(
                    "browse_snapshot_static_inventory_invalid",
                    "overlay manifest does not bind exactly the three UI files",
                )
            for name in STATIC_FILES:
                if files.get(name) != {
                    "sha256": _sha256(static_raw[name]),
                    "bytes": len(static_raw[name]),
                }:
                    raise _error(
                        "browse_snapshot_static_binding_mismatch",
                        "overlay static-file binding drifted",
                    )
        if inner["files"] != outer["files"]:
            raise _error(
                "browse_snapshot_static_binding_mismatch",
                "inner and outer overlay manifests bind different UI bytes",
            )
        artifacts["manifests/overlay.inner.json"] = inner_raw
        artifacts["manifests/overlay.outer.json"] = outer_raw

        openapi_raw = _read_stable_regular_file(
            self.workspace_root, self.openapi_relative
        )
        schema_raw = _read_stable_regular_file(
            self.workspace_root, self.schema_relative
        )
        schema = _strict_json_object(schema_raw, "browse snapshot schema")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise _error(
                "browse_snapshot_schema_invalid",
                "browse snapshot schema is not valid Draft 2020-12",
            ) from exc
        artifacts[OPENAPI_ARTIFACT] = openapi_raw
        artifacts[SNAPSHOT_SCHEMA_ARTIFACT] = schema_raw

        backend_identity = current_backend_build_identity(
            self.workspace_root,
            openapi_relative=self.openapi_relative,
            backend_source_relatives=self.backend_source_relatives,
        )
        if backend_identity["api_contract_sha256"] != _sha256(openapi_raw):
            raise _error(
                "browse_snapshot_source_drift",
                "OpenAPI bytes drifted while the backend identity was observed",
            )
        backend_build_id = str(backend_identity["backend_build_id"])
        artifacts[BACKEND_IDENTITY_RELATIVE] = _json_artifact_bytes(
            backend_identity
        )
        return artifacts, {
            "schema": schema,
            "backend_build_id": backend_build_id,
            "openapi_sha256": _sha256(openapi_raw),
            "static_bindings": {
                name: _artifact_descriptor(f"webui/{name}", static_raw[name])
                for name in STATIC_FILES
            },
            "inner_manifest_sha256": _sha256(inner_raw),
            "outer_manifest_sha256": _sha256(outer_raw),
            "material_intake_bindings": material_intake_bindings,
        }

    @staticmethod
    def _validated_capture(capture: BrowseCapture) -> BrowseCapture:
        if not isinstance(capture, BrowseCapture):
            raise _error(
                "browse_snapshot_capture_invalid",
                "projection source returned an invalid capture",
            )
        json_routes = dict(capture.json_routes)
        binary_routes = dict(capture.binary_routes)
        if not json_routes or not binary_routes:
            raise _error(
                "browse_snapshot_capture_incomplete",
                "browse capture must contain JSON details and visible crop bytes",
            )
        if set(json_routes) & set(binary_routes):
            raise _error(
                "browse_snapshot_route_collision",
                "a route is both JSON and binary",
            )
        for route, value in json_routes.items():
            _route_allowed(route)
            _reject_dynamic_value(value)
            _canonical_json_bytes(value)
        for route, payload in binary_routes.items():
            _route_allowed(route)
            if (
                not isinstance(payload, CandidateCropPayload)
                or payload.content_type != "image/png"
                or payload.sha256 != _sha256(payload.data)
                or not _valid_png(payload.data)
            ):
                raise _error(
                    "browse_snapshot_crop_invalid",
                    "browse capture contains an invalid PNG payload",
                )
        if not (_REQUIRED_CORE_ROUTES | _REQUIRED_CURRICULUM_ROUTES) <= set(
            json_routes
        ):
            raise _error(
                "browse_snapshot_capture_incomplete",
                "browse capture is missing a required status, catalog, curriculum, or theme route",
            )
        if not _REQUIRED_MATERIAL_INTAKE_ROUTES <= set(json_routes):
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "browse capture is missing the material intake baseline",
            )
        identity = dict(capture.identity)
        if (
            identity.get("registry_id") != REGISTRY_ID
            or identity.get("api_contract_version") != CONTRACT_VERSION
            or any(
                not isinstance(identity.get(key), str)
                or not _SHA256.fullmatch(str(identity[key]))
                for key in (
                    "ui_build_id",
                    "data_snapshot_id",
                    "registry_manifest_sha256",
                )
            )
        ):
            raise _error(
                "browse_snapshot_identity_invalid", "capture identity is invalid"
            )
        return BrowseCapture(
            json_routes=json_routes,
            binary_routes=binary_routes,
            counts=deepcopy(dict(capture.counts)),
            identity=identity,
        )

    def _materialize_once(self) -> dict[str, bytes]:
        artifacts, base = self._base_inputs()
        try:
            review_catalog_raw = self.review_catalog_factory()
        except WorkbenchReleaseSnapshotError:
            raise
        except Exception as exc:
            raise _error(
                "browse_snapshot_review_catalog_failed",
                "teacher review task definitions failed closed",
            ) from exc
        if not isinstance(review_catalog_raw, bytes) or not review_catalog_raw:
            raise _error(
                "browse_snapshot_review_catalog_invalid",
                "teacher review task definitions are unavailable",
            )
        review_catalog = _parse_canonical_json(
            review_catalog_raw, "teacher review task catalog"
        )
        _reject_dynamic_value(review_catalog)
        if _json_artifact_bytes(review_catalog) != review_catalog_raw:
            raise _error(
                "browse_snapshot_review_catalog_invalid",
                "teacher review task catalog is not canonical",
            )
        artifacts[REVIEW_TASK_CATALOG_ARTIFACT] = review_catalog_raw
        try:
            source = self.capture_factory()
            capture = self._validated_capture(source.capture())
        except WorkbenchReleaseSnapshotError:
            raise
        except Exception as exc:
            raise _error(
                "browse_snapshot_capture_failed",
                "browse projection source failed closed",
            ) from exc

        route_entries: list[dict[str, Any]] = []
        for route in sorted(capture.json_routes):
            raw = _json_artifact_bytes(capture.json_routes[route])
            relative = _object_path(raw, "application/json")
            previous = artifacts.get(relative)
            if previous is not None and previous != raw:
                raise _error(
                    "browse_snapshot_object_collision",
                    "content-addressed JSON object collision",
                )
            artifacts[relative] = raw
            route_entries.append(
                {
                    "method": "GET",
                    "route": route,
                    "media_type": "application/json",
                    "resource_kind": _resource_kind(route, "application/json"),
                    **_artifact_descriptor(relative, raw),
                }
            )
        for route in sorted(capture.binary_routes):
            payload = capture.binary_routes[route]
            raw = payload.data
            relative = _object_path(raw, "image/png")
            previous = artifacts.get(relative)
            if previous is not None and previous != raw:
                raise _error(
                    "browse_snapshot_object_collision",
                    "content-addressed image object collision",
                )
            artifacts[relative] = raw
            route_entries.append(
                {
                    "method": "GET",
                    "route": route,
                    "media_type": "image/png",
                    "resource_kind": _resource_kind(route, "image/png"),
                    **_artifact_descriptor(relative, raw),
                }
            )
        route_entries.sort(key=lambda item: item["route"])
        route_index = _with_self_hash(
            {
                "schema_version": ROUTE_INDEX_SCHEMA_VERSION,
                "api_contract_version": CONTRACT_VERSION,
                "route_count": len(route_entries),
                "json_route_count": len(capture.json_routes),
                "binary_route_count": len(capture.binary_routes),
                "query_policy": "only_materialized_routes_or_runtime_filter_from_frozen_catalogs",
                "dynamic_route_prefixes_excluded": list(DYNAMIC_ROUTE_PREFIXES),
                "routes": route_entries,
            }
        )
        route_index_raw = _json_artifact_bytes(route_index)
        artifacts[ROUTE_INDEX_RELATIVE] = route_index_raw

        descriptors = [
            _artifact_descriptor(relative, artifacts[relative])
            for relative in sorted(artifacts)
        ]
        browse_snapshot_id = _canonical_sha256(
            {"algorithm": SNAPSHOT_ALGORITHM, "artifacts": descriptors}
        )
        unique_images = {
            entry["sha256"]
            for entry in route_entries
            if entry["media_type"] == "image/png"
        }
        counts = deepcopy(dict(capture.counts))
        counts.update(
            {
                "route_count": len(route_entries),
                "json_route_count": len(capture.json_routes),
                "binary_route_count": len(capture.binary_routes),
                "unique_crop_assets": len(unique_images),
                "materialized_artifacts_excluding_manifest": len(descriptors),
                "materialized_bytes_excluding_manifest": sum(
                    descriptor["bytes"] for descriptor in descriptors
                ),
            }
        )
        manifest = _with_self_hash(
            {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "snapshot_kind": SNAPSHOT_KIND,
                "snapshot_algorithm": SNAPSHOT_ALGORITHM,
                "browse_snapshot_id": browse_snapshot_id,
                "api_contract_version": CONTRACT_VERSION,
                "backend_build_id": base["backend_build_id"],
                "ui_build_id": capture.identity["ui_build_id"],
                "data_snapshot_id": capture.identity["data_snapshot_id"],
                "registry_id": capture.identity["registry_id"],
                "registry_manifest_sha256": capture.identity[
                    "registry_manifest_sha256"
                ],
                "openapi": {
                    **_artifact_descriptor(
                        OPENAPI_ARTIFACT, artifacts[OPENAPI_ARTIFACT]
                    ),
                    "api_contract_version": CONTRACT_VERSION,
                },
                "overlay_manifests": {
                    "inner": _artifact_descriptor(
                        "manifests/overlay.inner.json",
                        artifacts["manifests/overlay.inner.json"],
                    ),
                    "outer": _artifact_descriptor(
                        "manifests/overlay.outer.json",
                        artifacts["manifests/overlay.outer.json"],
                    ),
                },
                "static_files": base["static_bindings"],
                "route_index": _artifact_descriptor(
                    ROUTE_INDEX_RELATIVE, route_index_raw
                ),
                "material_intake_sources": base["material_intake_bindings"],
                "counts": counts,
                "browse_scopes": list(PRODUCT_ORDER),
                "projection_families": [
                    "registry_and_readiness",
                    "curriculum_catalog_and_explicit_mapping_index",
                    "theme_catalogs",
                    "atomic_catalog_pages",
                    "atomic_details",
                    "source_status_and_visual_catalogs",
                    "master_crosswalk_and_visual_aliases",
                    "question_and_shared_crop_pixels",
                    "teacher_review_task_definitions",
                    "material_intake_read_only_baseline",
                ],
                "dynamic_domains_excluded": list(DYNAMIC_DOMAINS_EXCLUDED),
                "dynamic_state_embedded": False,
                "answer_pixels_embedded": False,
                "authority": dict(AUTHORITY),
                "artifacts": descriptors,
            }
        )
        errors = sorted(
            Draft202012Validator(base["schema"]).iter_errors(manifest),
            key=lambda error: tuple(str(value) for value in error.absolute_path),
        )
        if errors:
            raise _error(
                "browse_snapshot_manifest_invalid",
                "materialized browse manifest does not satisfy its schema",
            )
        artifacts[MANIFEST_RELATIVE] = _json_artifact_bytes(manifest)
        verify_materialized_snapshot(artifacts)
        return artifacts

    def materialize(self) -> dict[str, bytes]:
        """Return a release-core mapping only after two independent captures agree."""

        first = self._materialize_once()
        second = self._materialize_once()
        if set(first) != set(second):
            raise _error(
                "browse_snapshot_source_drift",
                "browse closure path inventory drifted between captures",
            )
        for relative in sorted(first):
            if len(first[relative]) != len(second[relative]) or _sha256(
                first[relative]
            ) != _sha256(second[relative]):
                raise _error(
                    "browse_snapshot_source_drift",
                    "browse closure bytes drifted between captures",
                )
        verify_materialized_snapshot(first)
        return first


@dataclass(frozen=True)
class FrozenBrowsePayload:
    """One verified immutable response returned by the frozen runtime reader."""

    data: bytes
    sha256: str
    content_type: str


def _directory_inventory(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    try:
        root_details = root.lstat()
    except OSError as exc:
        raise _error(
            "browse_snapshot_root_missing", "browse snapshot root is missing", 404
        ) from exc
    if not stat.S_ISDIR(root_details.st_mode) or _link_like(root_details):
        raise _error(
            "browse_snapshot_root_unsafe",
            "browse snapshot root is not a real directory",
            403,
        )
    try:
        values = sorted(root.rglob("*"), key=lambda path: path.as_posix())
    except OSError as exc:
        raise _error(
            "browse_snapshot_root_unavailable",
            "browse snapshot root could not be inventoried",
        ) from exc
    for path in values:
        try:
            details = path.lstat()
        except OSError as exc:
            raise _error(
                "browse_snapshot_root_unavailable",
                "browse snapshot entry could not be inspected",
            ) from exc
        if _link_like(details):
            raise _error(
                "browse_snapshot_root_unsafe",
                "browse snapshot tree contains a symlink or reparse point",
                403,
            )
        relative = PurePosixPath(path.relative_to(root).as_posix()).as_posix()
        _safe_relative(relative)
        if stat.S_ISDIR(details.st_mode):
            directories.add(relative)
        elif stat.S_ISREG(details.st_mode) and getattr(details, "st_nlink", 1) == 1:
            files.add(relative)
        else:
            raise _error(
                "browse_snapshot_root_unsafe",
                "browse snapshot tree contains a non-regular or multi-link entry",
                403,
            )
    return files, directories


def _expected_directories(paths: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for relative in paths:
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts)):
            result.add(PurePosixPath(*parts[:index]).as_posix())
    return result


class FrozenWorkbenchBrowseReader:
    """Load a candidate's ``browse-closure`` directory into verified memory.

    Construction is the only filesystem phase.  After all files, route
    bindings, details and crop pixels pass ``verify_materialized_snapshot``,
    responses are served from immutable in-memory byte objects.  The live
    workspace, credential store and student state are never opened.
    """

    def __init__(self, closure_root: Path):
        try:
            root = closure_root.resolve(strict=True)
        except OSError as exc:
            raise _error(
                "browse_snapshot_root_missing", "browse snapshot root is missing", 404
            ) from exc
        files, directories = _directory_inventory(root)
        if MANIFEST_RELATIVE not in files:
            raise _error(
                "browse_snapshot_manifest_missing",
                "browse snapshot root has no closure manifest",
            )
        manifest_raw = _read_stable_regular_file(root, MANIFEST_RELATIVE)
        manifest = _parse_canonical_json(
            manifest_raw, "browse snapshot manifest"
        )
        descriptors = manifest.get("artifacts")
        if not isinstance(descriptors, list):
            raise _error(
                "browse_snapshot_manifest_invalid",
                "browse snapshot artifact descriptors are missing",
            )
        descriptor_paths = {
            item.get("relative_path")
            for item in descriptors
            if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
        }
        expected_files = descriptor_paths | {MANIFEST_RELATIVE}
        if files != expected_files or directories != _expected_directories(
            expected_files
        ):
            raise _error(
                "browse_snapshot_artifact_inventory_mismatch",
                "browse snapshot root contains a rogue or missing entry",
            )
        loaded: dict[str, bytes] = {MANIFEST_RELATIVE: manifest_raw}
        for relative in sorted(descriptor_paths):
            loaded[relative] = _read_stable_regular_file(root, relative)
        verification = verify_materialized_snapshot(loaded)
        self._artifacts = MappingProxyType(dict(loaded))
        self._verification = MappingProxyType(deepcopy(verification))
        self._manifest = MappingProxyType(deepcopy(manifest))
        route_index = _parse_canonical_json(
            loaded[ROUTE_INDEX_RELATIVE], "browse route index"
        )
        self._routes = MappingProxyType(
            {entry["route"]: deepcopy(entry) for entry in route_index["routes"]}
        )
        self._static = MappingProxyType(
            {name: loaded[f"webui/{name}"] for name in STATIC_FILES}
        )
        self._catalogs = MappingProxyType(self._build_catalogs())
        self._material_intake_catalog = MappingProxyType(
            self._build_material_intake_catalog()
        )
        self._curriculum = MappingProxyType(self._build_curriculum())

    @classmethod
    def from_candidate_root(cls, candidate_root: Path) -> FrozenWorkbenchBrowseReader:
        """Load ``<candidate>/browse-closure`` from a release-store candidate."""

        return cls(candidate_root / "browse-closure")

    @classmethod
    def from_artifacts(
        cls, artifacts: Mapping[str, bytes]
    ) -> FrozenWorkbenchBrowseReader:
        """Construct an in-memory reader for tests or a pre-read store adapter."""

        verification = verify_materialized_snapshot(artifacts)
        instance = cls.__new__(cls)
        loaded = dict(artifacts)
        manifest = _parse_canonical_json(
            loaded[MANIFEST_RELATIVE], "browse snapshot manifest"
        )
        route_index = _parse_canonical_json(
            loaded[ROUTE_INDEX_RELATIVE], "browse route index"
        )
        instance._artifacts = MappingProxyType(loaded)
        instance._verification = MappingProxyType(deepcopy(verification))
        instance._manifest = MappingProxyType(deepcopy(manifest))
        instance._routes = MappingProxyType(
            {entry["route"]: deepcopy(entry) for entry in route_index["routes"]}
        )
        instance._static = MappingProxyType(
            {name: loaded[f"webui/{name}"] for name in STATIC_FILES}
        )
        instance._catalogs = MappingProxyType(instance._build_catalogs())
        instance._material_intake_catalog = MappingProxyType(
            instance._build_material_intake_catalog()
        )
        instance._curriculum = MappingProxyType(instance._build_curriculum())
        return instance

    def _build_catalogs(self) -> dict[str, dict[str, Any]]:
        definitions = {
            "wave1": "/api/v1/kb/sources/candidate_review_only/wave1/nodes",
            "wave1_all": "/api/v1/kb/sources/candidate_review_only/wave1/nodes",
            "master": "/api/v1/kb/workbench/master-atomic",
            "supplemental": "/api/v1/kb/workbench/supplemental-scans",
        }
        result: dict[str, dict[str, Any]] = {}
        for scope, base_path in definitions.items():
            pages: list[tuple[int, dict[str, Any]]] = []
            for route in self._routes:
                parsed = urlsplit(route)
                if parsed.path != base_path or not parsed.query:
                    continue
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"limit", "offset", "node_type"}:
                    continue
                if any(len(values) != 1 for values in query.values()):
                    continue
                if scope == "wave1" and query.get("node_type") != ["atomic_part"]:
                    continue
                if scope == "wave1_all" and "node_type" in query:
                    continue
                try:
                    offset = int(query.get("offset", ["0"])[0])
                except ValueError:
                    continue
                value = _parse_canonical_json(
                    self._artifacts[self._routes[route]["relative_path"]],
                    f"frozen catalog page {route}",
                )
                pages.append((offset, value))
            pages.sort(key=lambda item: item[0])
            if not pages or pages[0][0] != 0:
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    f"frozen {scope} catalog has no first page",
                )
            items: list[dict[str, Any]] = []
            expected_offset = 0
            total: int | None = None
            for offset, page in pages:
                page_items = page.get("items")
                page_total = page.get("total")
                if (
                    offset != expected_offset
                    or not isinstance(page_items, list)
                    or type(page_total) is not int
                    or page_total < 1
                    or (total is not None and page_total != total)
                ):
                    raise _error(
                        "browse_snapshot_catalog_incomplete",
                        f"frozen {scope} catalog pages are discontinuous",
                    )
                total = page_total
                items.extend(deepcopy(page_items))
                expected_offset += len(page_items)
            if total is None or len(items) != total:
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    f"frozen {scope} catalog is incomplete",
                )
            result[scope] = {
                "base": deepcopy(pages[0][1]),
                "items": items,
                "total": total,
            }
        return result

    def _build_material_intake_catalog(self) -> dict[str, Any]:
        if "/api/v1/intake/status" not in self._routes:
            return {}
        pages: list[tuple[int, dict[str, Any]]] = []
        for route in self._routes:
            parsed = urlsplit(route)
            if parsed.path != "/api/v1/intake/records" or not parsed.query:
                continue
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                set(query) != {"limit", "offset"}
                or any(len(values) != 1 for values in query.values())
                or query.get("limit") != ["200"]
            ):
                continue
            try:
                offset = int(query["offset"][0])
            except ValueError:
                continue
            value = _parse_canonical_json(
                self._artifacts[self._routes[route]["relative_path"]],
                f"frozen material intake page {route}",
            )
            pages.append((offset, value))
        pages.sort(key=lambda item: item[0])
        if not pages or pages[0][0] != 0:
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "frozen material intake catalog has no first page",
            )
        items: list[dict[str, Any]] = []
        expected_offset = 0
        total: int | None = None
        for offset, page in pages:
            page_items = page.get("items")
            page_total = page.get("total")
            if (
                offset != expected_offset
                or not isinstance(page_items, list)
                or type(page_total) is not int
                or page_total < 1
                or (total is not None and page_total != total)
            ):
                raise _error(
                    "browse_snapshot_material_intake_incomplete",
                    "frozen material intake pages are discontinuous",
                )
            total = page_total
            items.extend(deepcopy(page_items))
            expected_offset += len(page_items)
        if total is None or len(items) != total:
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "frozen material intake catalog is incomplete",
            )
        return {
            "base": deepcopy(pages[0][1]),
            "items": items,
            "total": total,
        }

    def _build_curriculum(self) -> dict[str, Any]:
        present = _REQUIRED_CURRICULUM_ROUTES & set(self._routes)
        if not present:
            return {}
        if present != _REQUIRED_CURRICULUM_ROUTES:
            raise _error(
                "browse_snapshot_curriculum_incomplete",
                "frozen curriculum catalog and mapping index are incomplete",
            )
        catalog = self.json(CURRICULUM_CATALOG_ROUTE)
        mapping_index = self.json(CURRICULUM_MAPPING_INDEX_ROUTE)
        routes = {
            CURRICULUM_CATALOG_ROUTE: catalog,
            CURRICULUM_MAPPING_INDEX_ROUTE: mapping_index,
            **{
                route: self.json(route)
                for route in self._routes
                if route.startswith("/api/v1/kb/workbench/theme-groups?scope=")
            },
        }
        _assert_curriculum_closure(routes)
        return {"catalog": catalog, "mapping_index": mapping_index}

    def curriculum_catalog(self) -> dict[str, Any]:
        if not self._curriculum:
            raise _error(
                "browse_snapshot_runtime_incompatible",
                "selected frozen release predates the curriculum catalog",
                503,
            )
        return deepcopy(self._curriculum["catalog"])

    def curriculum_search(
        self,
        *,
        volume_id: str | None = None,
        chapter_id: str | None = None,
        section: str | None = None,
        mapping_status: str | None = None,
    ) -> dict[str, Any]:
        if not self._curriculum:
            raise _error(
                "browse_snapshot_runtime_incompatible",
                "selected frozen release predates curriculum search",
                503,
            )
        if mapping_status not in {None, "complete", "partial", "blocked"}:
            raise _error(
                "curriculum_mapping_status_invalid",
                "mapping_status 仅支持 complete、partial 或 blocked。",
                400,
            )
        catalog = self._curriculum["catalog"]
        mapping_index = self._curriculum["mapping_index"]
        volumes = {
            item["volume_id"]: item for item in catalog["volumes"]
        }
        chapters: dict[str, tuple[str, dict[str, Any]]] = {}
        sections: dict[str, tuple[str, str, str]] = {}
        for current_volume_id, volume in volumes.items():
            for chapter in volume["chapters"]:
                current_chapter_id = chapter["chapter_id"]
                chapters[current_chapter_id] = (current_volume_id, chapter)
                for node in chapter["sections"]:
                    section_key = node["section_key"]
                    binding = (
                        current_volume_id,
                        current_chapter_id,
                        section_key,
                    )
                    sections[section_key] = binding
                    section_id = node.get("section_id")
                    if isinstance(section_id, str):
                        sections[section_id] = binding

        def checked(value: str | None, label: str) -> str | None:
            if value is not None and (
                not isinstance(value, str)
                or _IDENTIFIER.fullmatch(value) is None
            ):
                raise _error(
                    "curriculum_query_invalid",
                    f"{label} 无效。",
                    400,
                )
            return value

        volume_id = checked(volume_id, "volume_id")
        chapter_id = checked(chapter_id, "chapter_id")
        section = checked(section, "section")
        if volume_id is not None and volume_id not in volumes:
            raise _error("curriculum_node_not_found", "教材册不存在。", 404)
        if chapter_id is not None:
            chapter = chapters.get(chapter_id)
            if chapter is None:
                raise _error("curriculum_node_not_found", "教材章不存在。", 404)
            if volume_id is not None and chapter[0] != volume_id:
                raise _error(
                    "curriculum_query_conflict",
                    "教材册与章不属于同一父链。",
                    400,
                )
            volume_id = chapter[0]
        resolved_section_key: str | None = None
        if section is not None:
            binding = sections.get(section)
            if binding is None:
                raise _error("curriculum_node_not_found", "教材节不存在。", 404)
            bound_volume, bound_chapter, resolved_section_key = binding
            if chapter_id is not None and chapter_id != bound_chapter:
                raise _error(
                    "curriculum_query_conflict",
                    "教材章与节不属于同一父链。",
                    400,
                )
            if volume_id is not None and volume_id != bound_volume:
                raise _error(
                    "curriculum_query_conflict",
                    "教材册与节不属于同一父链。",
                    400,
                )
            volume_id = bound_volume
            chapter_id = bound_chapter

        items: list[dict[str, Any]] = []
        grouped: dict[tuple[str, str], list[str]] = {}
        matched_entry_count = 0
        for record in mapping_index["records"]:
            if mapping_status in {"complete", "partial"} and (
                record["mapping_status"] != mapping_status
            ):
                continue
            wanted_blocked = mapping_status == "blocked"
            matched_entries = [
                entry
                for entry in record["entries"]
                if (entry["mapping_status"] == "blocked") == wanted_blocked
                and (volume_id is None or entry["volume_id"] == volume_id)
                and (chapter_id is None or entry["chapter_id"] == chapter_id)
                and (
                    resolved_section_key is None
                    or entry["section_key"] == resolved_section_key
                )
            ]
            if not matched_entries:
                continue
            atomic_id = record["atomic_id"]
            key = (record["source_layer"], record["source_batch"])
            grouped.setdefault(key, []).append(atomic_id)
            matched_entry_count += len(matched_entries)
            items.append(
                {
                    "atomic_id": atomic_id,
                    "source_layer": record["source_layer"],
                    "source_batch": record["source_batch"],
                    "mapping_status": (
                        "blocked" if wanted_blocked else record["mapping_status"]
                    ),
                    "matched_entry_count": len(matched_entries),
                    "matched_volume_ids": sorted(
                        {entry["volume_id"] for entry in matched_entries}
                    ),
                    "matched_chapter_ids": sorted(
                        {entry["chapter_id"] for entry in matched_entries}
                    ),
                    "matched_section_keys": sorted(
                        {
                            entry["section_key"]
                            for entry in matched_entries
                            if entry["section_key"] is not None
                        }
                    ),
                }
            )
        groups = [
            {
                "group_kind": "mapping_source",
                "source_layer": layer,
                "source_batch": batch,
                "atomic_count": len(ids),
                "atomic_ids": list(ids),
            }
            for (layer, batch), ids in grouped.items()
        ]
        result = {
            "schema_version": mapping_index["schema_version"],
            "data_snapshot_id": mapping_index["data_snapshot_id"],
            "scope": mapping_index["scope"],
            "query": {
                "volume_id": volume_id,
                "chapter_id": chapter_id,
                "section": section,
                "resolved_section_key": resolved_section_key,
                "mapping_status": mapping_status,
            },
            "counts": {
                "active_atomic_total": len(mapping_index["records"]),
                "matched_atomic_count": len(items),
                "matched_entry_count": matched_entry_count,
                "source_group_count": len(groups),
            },
            "atomic_ids": [item["atomic_id"] for item in items],
            "groups": groups,
            "items": items,
            "authority": deepcopy(mapping_index["authority"]),
            "integrity": {
                **deepcopy(mapping_index["integrity"]),
                "blocked_edges_require_explicit_filter": True,
                "complete_theme_chain_returned": False,
                "theme_join_required_downstream": True,
            },
        }
        _reject_dynamic_value(result)
        return result

    @staticmethod
    def _catalog_request(
        route: str,
    ) -> tuple[str, int, int, dict[str, str | None]] | None:
        parsed = urlsplit(route)
        by_path = {
            "/api/v1/kb/sources/candidate_review_only/wave1/nodes": "wave1",
            "/api/v1/kb/workbench/master-atomic": "master",
            "/api/v1/kb/workbench/supplemental-scans": "supplemental",
        }
        scope = by_path.get(parsed.path)
        if scope is None or parsed.fragment:
            return None
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = {"limit", "offset"} | (
            {
                "node_type",
                "q",
                "paper_id",
                "theme_id",
                "printed_question_id",
            }
            if scope == "wave1"
            else set()
        )
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen catalog supports limit/offset and Wave1 atomic node_type only",
                400,
            )
        node_type = query.get("node_type", [None])[0] if scope == "wave1" else None
        if scope == "wave1" and node_type not in {
            None,
            "paper",
            "theme_big_question",
            "printed_question",
            "atomic_part",
        }:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen Wave1 node_type is invalid",
                400,
            )
        try:
            limit = int(query.get("limit", ["200"])[0])
            offset = int(query.get("offset", ["0"])[0])
        except ValueError as exc:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen catalog pagination must use integers",
                400,
            ) from exc
        if not 1 <= limit <= 200 or offset < 0:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen catalog pagination is outside the supported range",
                400,
            )
        filters: dict[str, str | None] = {
            "node_type": node_type,
            "query": None,
            "paper_id": None,
            "theme_id": None,
            "printed_question_id": None,
        }
        if scope == "wave1":
            query_text = query.get("q", [""])[0].strip()
            if len(query_text) > 120 or any(ord(character) < 32 for character in query_text):
                raise _error(
                    "browse_snapshot_query_invalid",
                    "frozen Wave1 query text is invalid",
                    400,
                )
            filters["query"] = query_text
            for key in ("paper_id", "theme_id", "printed_question_id"):
                raw = query.get(key, [""])[0]
                if raw and _IDENTIFIER.fullmatch(raw) is None:
                    raise _error(
                        "browse_snapshot_query_invalid",
                        f"frozen Wave1 {key} is invalid",
                        400,
                    )
                filters[key] = raw or None
        return scope, limit, offset, filters

    @staticmethod
    def _material_intake_request(
        route: str,
    ) -> tuple[
        str | None,
        str | None,
        str | None,
        str | None,
        int,
        int,
    ] | None:
        parsed = urlsplit(route)
        if parsed.path != "/api/v1/intake/records" or parsed.fragment:
            return None
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = {"kind", "stage", "status", "q", "limit", "offset"}
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake query is invalid",
                400,
            )
        try:
            limit = int(query.get("limit", ["200"])[0])
            offset = int(query.get("offset", ["0"])[0])
        except ValueError as exc:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake pagination must use integers",
                400,
            ) from exc
        if not 1 <= limit <= 200 or offset < 0:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake pagination is outside the supported range",
                400,
            )
        kind = query.get("kind", [None])[0]
        stage = query.get("stage", [None])[0]
        status = query.get("status", [None])[0]
        query_text = query.get("q", [None])[0]
        if kind is not None and kind not in MATERIAL_INTAKE_RECORD_SECTIONS:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake kind is invalid",
                400,
            )
        if stage is not None and stage not in MATERIAL_INTAKE_PIPELINE:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake stage is invalid",
                400,
            )
        if status is not None and status not in MATERIAL_INTAKE_STATUSES:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake status is invalid",
                400,
            )
        if query_text is not None and (
            not 1 <= len(query_text) <= 120
            or any(ord(character) < 32 or ord(character) == 127 for character in query_text)
        ):
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake text query is invalid",
                400,
            )
        return kind, stage, status, query_text, limit, offset

    @staticmethod
    def _parent_id(item: Mapping[str, Any], node_type: str) -> str | None:
        chain = item.get("parent_chain")
        if not isinstance(chain, list):
            return None
        for node in chain:
            if (
                isinstance(node, dict)
                and node.get("node_type") == node_type
                and isinstance(node.get("node_id"), str)
            ):
                return str(node["node_id"])
        return None

    def candidate_list(
        self,
        *,
        node_type: str | None = None,
        query: str | None = None,
        paper_id: str | None = None,
        theme_id: str | None = None,
        printed_question_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if (
            node_type
            not in {
                None,
                "paper",
                "theme_big_question",
                "printed_question",
                "atomic_part",
            }
            or
            len(query) > 120
            or any(ord(character) < 32 for character in query)
            or not 1 <= limit <= 200
            or offset < 0
        ):
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen Wave1 filter or pagination is invalid",
                400,
            )
        for label, value in (
            ("paper_id", paper_id),
            ("theme_id", theme_id),
            ("printed_question_id", printed_question_id),
        ):
            if value is not None and _IDENTIFIER.fullmatch(value) is None:
                raise _error(
                    "browse_snapshot_query_invalid",
                    f"frozen Wave1 {label} is invalid",
                    400,
                )
        needle = query.casefold()
        filtered: list[dict[str, Any]] = []
        for item in self._catalogs["wave1_all"]["items"]:
            if node_type is not None and item.get("node_type") != node_type:
                continue
            if paper_id is not None and self._parent_id(item, "paper") != paper_id:
                continue
            if (
                theme_id is not None
                and self._parent_id(item, "theme_big_question") != theme_id
            ):
                continue
            if (
                printed_question_id is not None
                and self._parent_id(item, "printed_question")
                != printed_question_id
            ):
                continue
            if needle and needle not in json.dumps(
                item, ensure_ascii=False, sort_keys=True
            ).casefold():
                continue
            filtered.append(item)
        page = deepcopy(self._catalogs["wave1"]["base"])
        values = deepcopy(filtered[offset : offset + limit])
        page["items"] = values
        page["count"] = len(values)
        page["total"] = len(filtered)
        page["limit"] = limit
        page["offset"] = offset
        page["filters"] = {
            "node_type": node_type,
            "query": query,
            "paper_id": paper_id,
            "theme_id": theme_id,
            "printed_question_id": printed_question_id,
        }
        return page

    def catalog_page(self, scope: str, *, limit: int, offset: int) -> dict[str, Any]:
        if scope not in PRODUCT_ORDER or not 1 <= limit <= 200 or offset < 0:
            raise _error(
                "browse_snapshot_query_invalid", "frozen catalog request is invalid", 400
            )
        catalog = self._catalogs[scope]
        page = deepcopy(catalog["base"])
        items = deepcopy(catalog["items"][offset : offset + limit])
        page["items"] = items
        page["count"] = len(items)
        page["total"] = catalog["total"]
        page["limit"] = limit
        page["offset"] = offset
        if scope == "wave1":
            filters = page.get("filters")
            if isinstance(filters, dict):
                filters["node_type"] = "atomic_part"
                filters["query"] = ""
                filters["paper_id"] = None
                filters["theme_id"] = None
                filters["printed_question_id"] = None
        return page

    def material_intake_records(
        self,
        *,
        kind: str | None,
        stage: str | None,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        if not self._material_intake_catalog:
            raise _error(
                "browse_snapshot_runtime_incompatible",
                "selected frozen release predates the material intake baseline",
                503,
            )
        if kind is not None and kind not in MATERIAL_INTAKE_RECORD_SECTIONS:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake kind is invalid",
                400,
            )
        if stage is not None and stage not in MATERIAL_INTAKE_PIPELINE:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake stage is invalid",
                400,
            )
        if status is not None and status not in MATERIAL_INTAKE_STATUSES:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake status is invalid",
                400,
            )
        if query is not None and (
            not 1 <= len(query) <= 120
            or any(ord(character) < 32 or ord(character) == 127 for character in query)
        ):
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake text query is invalid",
                400,
            )
        if not 1 <= limit <= 200 or offset < 0:
            raise _error(
                "browse_snapshot_query_invalid",
                "frozen material intake pagination is outside the supported range",
                400,
            )
        needle = query.casefold() if query is not None else None
        filtered: list[dict[str, Any]] = []
        for item in self._material_intake_catalog["items"]:
            if kind is not None and item.get("record_kind") != kind:
                continue
            if stage is not None and item.get("ingest_stage") != stage:
                continue
            if status is not None and item.get("status") != status:
                continue
            if needle is not None and needle not in " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("record_kind", "")),
                    str(item.get("source_id", "")),
                    str(item.get("ingest_stage", "")),
                    str(item.get("status", "")),
                    json.dumps(
                        item.get("facts", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ]
            ).casefold():
                continue
            filtered.append(item)
        page = deepcopy(self._material_intake_catalog["base"])
        values = deepcopy(filtered[offset : offset + limit])
        page["items"] = values
        page["count"] = len(values)
        page["total"] = len(filtered)
        page["limit"] = limit
        page["offset"] = offset
        return page

    def catalog_items(self, scope: str) -> tuple[dict[str, Any], ...]:
        """Return defensive copies in frozen source order for service-side facets."""

        if scope not in PRODUCT_ORDER:
            raise _error(
                "browse_snapshot_scope_invalid",
                "frozen catalog scope must be wave1, master, or supplemental",
                400,
            )
        return tuple(deepcopy(self._catalogs[scope]["items"]))

    def _synthesized_catalog_payload(self, route: str) -> FrozenBrowsePayload | None:
        request = self._catalog_request(route)
        if request is None:
            return None
        scope, limit, offset, filters = request
        value = (
            self.candidate_list(
                node_type=filters["node_type"],
                query=filters["query"],
                paper_id=filters["paper_id"],
                theme_id=filters["theme_id"],
                printed_question_id=filters["printed_question_id"],
                limit=limit,
                offset=offset,
            )
            if scope == "wave1"
            else self.catalog_page(scope, limit=limit, offset=offset)
        )
        raw = _json_artifact_bytes(value)
        return FrozenBrowsePayload(
            data=raw,
            sha256=_sha256(raw),
            content_type="application/json; charset=utf-8",
        )

    def _synthesized_material_intake_payload(
        self, route: str
    ) -> FrozenBrowsePayload | None:
        request = self._material_intake_request(route)
        if request is None:
            return None
        kind, stage, status, query, limit, offset = request
        value = self.material_intake_records(
            kind=kind,
            stage=stage,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
        raw = _json_artifact_bytes(value)
        return FrozenBrowsePayload(
            data=raw,
            sha256=_sha256(raw),
            content_type="application/json; charset=utf-8",
        )

    def verification(self) -> dict[str, Any]:
        return deepcopy(dict(self._verification))

    def manifest(self) -> dict[str, Any]:
        return deepcopy(dict(self._manifest))

    def route_keys(self) -> tuple[str, ...]:
        return tuple(self._routes)

    def response(self, route: str) -> FrozenBrowsePayload:
        _route_allowed(route)
        entry = self._routes.get(route)
        if entry is None:
            synthesized = self._synthesized_catalog_payload(route)
            if synthesized is None:
                synthesized = self._synthesized_material_intake_payload(route)
            if synthesized is not None:
                return synthesized
            raise _error(
                "browse_snapshot_route_not_found",
                "route is not present in the frozen browse snapshot",
                404,
            )
        raw = self._artifacts[entry["relative_path"]]
        return FrozenBrowsePayload(
            data=raw,
            sha256=entry["sha256"],
            content_type=(
                "application/json; charset=utf-8"
                if entry["media_type"] == "application/json"
                else entry["media_type"]
            ),
        )

    def json(self, route: str) -> dict[str, Any]:
        payload = self.response(route)
        if payload.content_type != "application/json; charset=utf-8":
            raise _error(
                "browse_snapshot_media_type_mismatch",
                "requested frozen route is not JSON",
                415,
            )
        return _parse_canonical_json(payload.data, f"route {route}")

    def crop(self, route: str) -> CandidateCropPayload:
        payload = self.response(route)
        if payload.content_type != "image/png":
            raise _error(
                "browse_snapshot_media_type_mismatch",
                "requested frozen route is not a PNG crop",
                415,
            )
        return CandidateCropPayload(
            data=payload.data,
            sha256=payload.sha256,
            content_type="image/png",
        )

    def static(self, name: str) -> FrozenBrowsePayload:
        if name not in STATIC_FILES:
            raise _error(
                "browse_snapshot_static_not_found",
                "static file is not one of the frozen UI files",
                404,
            )
        raw = self._static[name]
        content_type = {
            "app.js": "text/javascript; charset=utf-8",
            "index.html": "text/html; charset=utf-8",
            "styles.css": "text/css; charset=utf-8",
        }[name]
        return FrozenBrowsePayload(
            data=raw, sha256=_sha256(raw), content_type=content_type
        )

    def registry(self) -> dict[str, Any]:
        return self.json("/api/v1/workbench/product-registry")

    def readiness(self) -> dict[str, Any]:
        return self.json("/api/v1/readiness")

    def theme_groups(self, scope: str) -> dict[str, Any]:
        if scope not in PRODUCT_ORDER:
            raise _error(
                "browse_snapshot_scope_invalid",
                "frozen theme scope must be wave1, master, or supplemental",
                400,
            )
        return self.json(f"/api/v1/kb/workbench/theme-groups?scope={scope}")

    def question_processing_progress(
        self,
        *,
        scope: str,
        paper_id: str | None,
        theme_id: str | None,
        gap: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        route = (
            "/api/v1/kb/question-processing-progress"
            f"?limit={QUESTION_PROGRESS_CAPTURE_LIMIT}&offset=0&scope={scope}"
        )
        if route not in self._routes:
            raise _error(
                "browse_snapshot_runtime_incompatible",
                "selected frozen release predates question-processing progress",
                503,
            )
        return filter_progress_response(
            self.json(route),
            scope=scope,
            paper_id=paper_id,
            theme_id=theme_id,
            gap=gap,
            limit=limit,
            offset=offset,
        )

    def review_task_catalog_bytes(self) -> bytes:
        """Return the frozen source-derived task definitions, never mutable review state."""

        raw = self._artifacts.get(REVIEW_TASK_CATALOG_ARTIFACT)
        if raw is None:
            raise _error(
                "browse_snapshot_review_catalog_missing",
                "frozen teacher review task definitions are missing",
                409,
            )
        return bytes(raw)

    def assert_current_backend(
        self,
        workspace_root: Path,
        *,
        openapi_relative: str | PurePosixPath = DEFAULT_OPENAPI_RELATIVE,
        backend_source_relatives: Iterable[str] = DEFAULT_BACKEND_SOURCE_RELATIVES,
    ) -> str:
        observed = current_backend_build_id(
            workspace_root,
            openapi_relative=openapi_relative,
            backend_source_relatives=backend_source_relatives,
        )
        expected = str(self._manifest["backend_build_id"])
        if observed != expected:
            raise _error(
                "browse_snapshot_backend_build_mismatch",
                "frozen snapshot and current backend source generation differ",
                409,
            )
        return observed


def _assert_detail_closure(
    parsed_routes: Mapping[str, dict[str, Any]], route_entries: Mapping[str, dict[str, Any]]
) -> dict[str, int]:
    wave_ids: set[str] = set()
    wave_all_keys: set[tuple[str, str]] = set()
    master_ids: set[str] = set()
    supplemental_ids: set[str] = set()
    for route, value in parsed_routes.items():
        items = value.get("items")
        if not isinstance(items, list) or "?limit=" not in route:
            continue
        if "/candidate_review_only/wave1/nodes?" in route:
            query = parse_qs(urlsplit(route).query, keep_blank_values=True)
            if query.get("node_type") == ["atomic_part"]:
                target = wave_ids
            elif "node_type" not in query:
                for item in items:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("node_type"), str)
                        or not isinstance(item.get("node_id"), str)
                    ):
                        raise _error(
                            "browse_snapshot_catalog_incomplete",
                            "Wave1 hierarchy catalog contains an invalid node",
                        )
                    key = (item["node_type"], item["node_id"])
                    if key in wave_all_keys:
                        raise _error(
                            "browse_snapshot_catalog_incomplete",
                            "Wave1 hierarchy catalog contains a duplicate node",
                        )
                    wave_all_keys.add(key)
                continue
            else:
                continue
        elif "/master-atomic?" in route:
            target = master_ids
        elif "/supplemental-scans?" in route:
            target = supplemental_ids
        else:
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("node_id"), str):
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "atomic catalog contains an invalid node",
                )
            if item["node_id"] in target:
                raise _error(
                    "browse_snapshot_catalog_incomplete",
                    "atomic catalog contains a duplicate node",
                )
            target.add(item["node_id"])

    expected_routes = set(route_entries)
    for node_id in wave_ids:
        if (
            "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            f"atomic_part/{_encoded(node_id)}"
            not in expected_routes
            or f"/api/v1/kb/workbench/question-visual-scans/{_encoded(node_id)}"
            not in expected_routes
        ):
            raise _error(
                "browse_snapshot_detail_missing",
                "Wave1 catalog node has no complete base and visual detail",
            )
    if {node_id for node_type, node_id in wave_all_keys if node_type == "atomic_part"} != wave_ids:
        raise _error(
            "browse_snapshot_catalog_incomplete",
            "Wave1 atomic and full hierarchy catalogs disagree",
        )
    for node_type, node_id in wave_all_keys:
        if (
            "/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            f"{_encoded(node_type)}/{_encoded(node_id)}"
            not in expected_routes
        ):
            raise _error(
                "browse_snapshot_detail_missing",
                "Wave1 hierarchy catalog node has no frozen detail",
            )
    for node_id in master_ids:
        if f"/api/v1/kb/workbench/master-atomic/{_encoded(node_id)}" not in expected_routes:
            raise _error(
                "browse_snapshot_detail_missing",
                "Master catalog node has no frozen detail",
            )
    for node_id in supplemental_ids:
        if (
            f"/api/v1/kb/workbench/supplemental-scans/{_encoded(node_id)}"
            not in expected_routes
        ):
            raise _error(
                "browse_snapshot_detail_missing",
                "supplemental catalog node has no frozen detail",
            )

    for catalog_route, detail_prefix, id_key in (
        (
            "/api/v1/kb/workbench/master-direct-scans/catalog",
            "/api/v1/kb/workbench/master-direct-scans/",
            "master_node_ids",
        ),
        (
            "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
            "/api/v1/kb/workbench/master-visual-scan-aliases/",
            "master_node_ids",
        ),
    ):
        catalog = parsed_routes.get(catalog_route)
        if not isinstance(catalog, dict) or not isinstance(catalog.get(id_key), list):
            raise _error(
                "browse_snapshot_catalog_incomplete",
                "visual catalog is missing its node ID inventory",
            )
        for node_id in catalog[id_key]:
            if (
                not isinstance(node_id, str)
                or f"{detail_prefix}{_encoded(node_id)}" not in expected_routes
            ):
                raise _error(
                    "browse_snapshot_detail_missing",
                    "visual catalog node has no frozen detail",
                )
    return {
        "wave1": len(wave_ids),
        "master": len(master_ids),
        "supplemental": len(supplemental_ids),
    }


def _material_projection_has_locator(value: Any) -> bool:
    forbidden = {
        "path",
        "local_path",
        "source_path",
        "absolute_path",
        "url",
        "source_url",
        "manifest_path",
    }
    if isinstance(value, dict):
        return any(
            str(key).casefold() in forbidden
            or _material_projection_has_locator(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_material_projection_has_locator(child) for child in value)
    return False


def _assert_material_intake_closure(
    normalized: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    parsed_routes: Mapping[str, dict[str, Any]],
    route_entries: Mapping[str, dict[str, Any]],
) -> dict[str, int] | None:
    """Verify the source-bound, read-only material intake baseline.

    Legacy releases created before the intake desk existed contain neither the
    source closure nor intake routes and remain readable only for safe upgrade
    or rollback.  Any partial presence fails closed.
    """

    intake_routes = {
        route
        for route in route_entries
        if urlsplit(route).path.startswith("/api/v1/intake")
    }
    bindings = manifest.get("material_intake_sources")
    if bindings is None and not intake_routes:
        return None
    if (
        not isinstance(bindings, dict)
        or set(bindings) != set(MATERIAL_INTAKE_SOURCE_FILES)
        or not _REQUIRED_MATERIAL_INTAKE_ROUTES <= intake_routes
    ):
        raise _error(
            "browse_snapshot_material_intake_incomplete",
            "material intake routes and bound sources are not all present",
        )

    source_raw: dict[str, bytes] = {}
    for key, (artifact_relative, _) in MATERIAL_INTAKE_SOURCE_FILES.items():
        raw = normalized.get(artifact_relative)
        if raw is None or bindings.get(key) != _artifact_descriptor(
            artifact_relative, raw
        ):
            raise _error(
                "browse_snapshot_material_intake_source_binding_mismatch",
                "a material intake source artifact is missing or drifted",
            )
        source_raw[key] = raw

    ledger_schema = _strict_json_object(
        source_raw["ledger_schema"], "material intake ledger schema"
    )
    ledger = _strict_json_object(source_raw["ledger"], "material intake ledger")
    try:
        Draft202012Validator.check_schema(ledger_schema)
        ledger_errors = list(Draft202012Validator(ledger_schema).iter_errors(ledger))
    except Exception as exc:
        raise _error(
            "browse_snapshot_material_intake_schema_invalid",
            "embedded material intake schema is invalid",
        ) from exc
    if ledger_errors:
        raise _error(
            "browse_snapshot_material_intake_ledger_invalid",
            "embedded material intake ledger violates its schema",
        )
    ledger_clone = deepcopy(ledger)
    ledger_clone.pop("self_hash", None)
    if (
        ledger.get("self_hash_algorithm")
        != "sha256_canonical_json_without_self_hash_v1"
        or ledger.get("self_hash") != _canonical_sha256(ledger_clone)
        or ledger.get("input_set_sha256")
        != _canonical_sha256(ledger.get("source_bindings"))
    ):
        raise _error(
            "browse_snapshot_material_intake_ledger_invalid",
            "embedded material intake ledger identity is invalid",
        )

    direct_bindings = {
        "catalog": "catalog",
        "full_bank_readiness": "full_bank_readiness",
    }
    ledger_bindings = ledger.get("source_bindings")
    if not isinstance(ledger_bindings, dict) or set(ledger_bindings) != set(
        direct_bindings
    ):
        raise _error(
            "browse_snapshot_material_intake_source_binding_mismatch",
            "material intake ledger has an unexpected direct input set",
        )
    for ledger_key, source_key in direct_bindings.items():
        descriptor = ledger_bindings.get(ledger_key)
        expected_source_path = MATERIAL_INTAKE_SOURCE_FILES[source_key][1]
        raw = source_raw[source_key]
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("path") != expected_source_path
            or descriptor.get("sha256") != _sha256(raw)
            or descriptor.get("bytes") != len(raw)
        ):
            raise _error(
                "browse_snapshot_material_intake_source_binding_mismatch",
                "material intake ledger does not bind its embedded direct input",
            )

    readiness = _strict_json_object(
        source_raw["full_bank_readiness"], "full-bank readiness source"
    )
    readiness_clone = deepcopy(readiness)
    readiness_clone.pop("self_hash", None)
    readiness_bindings = readiness.get("source_bindings")
    transitive_keys = {
        "paper_inventory",
        "paper_profiles",
        "formalization_queue",
        "wave1_paper_candidates",
        "teaching_pack_inventory",
    }
    if (
        readiness.get("self_hash_algorithm")
        != "sha256_canonical_json_without_self_hash_v1"
        or readiness.get("self_hash") != _canonical_sha256(readiness_clone)
        or not isinstance(readiness_bindings, dict)
        or set(readiness_bindings) != transitive_keys
        or readiness.get("input_set_sha256")
        != _canonical_sha256(readiness_bindings)
    ):
        raise _error(
            "browse_snapshot_material_intake_source_binding_mismatch",
            "embedded readiness source identity is invalid",
        )
    for key in sorted(transitive_keys):
        descriptor = readiness_bindings.get(key)
        raw = source_raw[key]
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("path") != MATERIAL_INTAKE_SOURCE_FILES[key][1]
            or descriptor.get("sha256") != _sha256(raw)
            or descriptor.get("bytes") != len(raw)
        ):
            raise _error(
                "browse_snapshot_material_intake_source_binding_mismatch",
                "readiness does not bind an embedded transitive input",
            )

    status = parsed_routes.get("/api/v1/intake/status")
    batches = parsed_routes.get("/api/v1/intake/batches")
    if not isinstance(status, dict) or not isinstance(batches, dict):
        raise _error(
            "browse_snapshot_material_intake_incomplete",
            "material intake status or batch catalog is missing",
        )
    if _material_projection_has_locator(status) or _material_projection_has_locator(
        batches
    ):
        raise _error(
            "browse_snapshot_material_intake_projection_unsafe",
            "material intake projection exposes a source locator",
            403,
        )
    ledger_counts = ledger.get("counts")
    status_integrity = status.get("integrity")
    if (
        status.get("ledger_id") != ledger.get("ledger_id")
        or status.get("schema_version") != ledger.get("schema_version")
        or status.get("counts") != ledger_counts
        or not isinstance(status_integrity, dict)
        or status_integrity.get("ledger_sha256") != _sha256(source_raw["ledger"])
        or status_integrity.get("self_hash") != ledger.get("self_hash")
        or status_integrity.get("input_set_sha256")
        != ledger.get("input_set_sha256")
        or status.get("endpoints")
        != {
            "read_only_get": True,
            "batch_create": False,
            "worker_execution": False,
            "mutation": False,
        }
    ):
        raise _error(
            "browse_snapshot_material_intake_projection_mismatch",
            "material intake status does not match the bound ledger",
        )

    ledger_batches = ledger.get("batches")
    batch_items = batches.get("items")
    if (
        not isinstance(ledger_batches, list)
        or not isinstance(batch_items, list)
        or len(ledger_batches) != 3
        or batches.get("total") != 3
        or len(batch_items) != 3
    ):
        raise _error(
            "browse_snapshot_material_intake_incomplete",
            "material intake batch catalog is not complete",
        )
    ledger_batch_ids = {
        item.get("batch_id") for item in ledger_batches if isinstance(item, dict)
    }
    projected_batch_ids = {
        item.get("batch_id") for item in batch_items if isinstance(item, dict)
    }
    if (
        None in ledger_batch_ids
        or len(ledger_batch_ids) != 3
        or projected_batch_ids != ledger_batch_ids
    ):
        raise _error(
            "browse_snapshot_material_intake_incomplete",
            "material intake batch identities do not match the ledger",
        )

    detail_routes = {
        f"/api/v1/intake/batches/{_encoded(str(batch_id))}"
        for batch_id in ledger_batch_ids
    }
    for route in detail_routes:
        detail = parsed_routes.get(route)
        if (
            not isinstance(detail, dict)
            or detail.get("batch_id") not in ledger_batch_ids
            or detail.get("ledger_id") != ledger.get("ledger_id")
            or _material_projection_has_locator(detail)
        ):
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "a material intake batch detail is absent or invalid",
            )

    page_rows: list[dict[str, Any]] = []
    page_routes: set[str] = set()
    expected_offset = 0
    observed_total: int | None = None
    pages: list[tuple[int, str, dict[str, Any]]] = []
    for route in intake_routes:
        parsed = urlsplit(route)
        if parsed.path != "/api/v1/intake/records":
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.fragment
            or set(query) != {"limit", "offset"}
            or any(len(values) != 1 for values in query.values())
            or query.get("limit") != ["200"]
        ):
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "material intake record baseline has an invalid page route",
            )
        try:
            offset = int(query["offset"][0])
        except ValueError as exc:
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "material intake record page offset is invalid",
            ) from exc
        page = parsed_routes.get(route)
        if not isinstance(page, dict):
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "material intake record page is missing",
            )
        pages.append((offset, route, page))
    for offset, route, page in sorted(pages):
        items = page.get("items")
        total = page.get("total")
        if (
            offset != expected_offset
            or not isinstance(items, list)
            or type(total) is not int
            or total < 1
            or (observed_total is not None and total != observed_total)
            or page.get("offset") != offset
            or page.get("limit") != 200
            or page.get("count") != len(items)
            or len(items) > 200
            or _material_projection_has_locator(page)
        ):
            raise _error(
                "browse_snapshot_material_intake_incomplete",
                "material intake record pages are discontinuous or unsafe",
            )
        observed_total = total
        expected_offset += len(items)
        page_rows.extend(items)
        page_routes.add(route)

    ledger_rows = [
        row
        for section in MATERIAL_INTAKE_RECORD_SECTIONS.values()
        for row in ledger.get(section, ())
        if isinstance(row, dict)
    ]
    ledger_record_ids = {row.get("record_id") for row in ledger_rows}
    projected_record_ids = {
        row.get("record_id") for row in page_rows if isinstance(row, dict)
    }
    if (
        observed_total is None
        or len(page_rows) != observed_total
        or len(projected_record_ids) != observed_total
        or None in projected_record_ids
        or projected_record_ids != ledger_record_ids
        or not isinstance(ledger_counts, dict)
        or ledger_counts.get("entity_record_count_non_additive") != observed_total
    ):
        raise _error(
            "browse_snapshot_material_intake_incomplete",
            "material intake record pages do not close over the bound ledger",
        )

    allowed_routes = (
        set(_REQUIRED_MATERIAL_INTAKE_ROUTES) | detail_routes | page_routes
    )
    if intake_routes != allowed_routes:
        raise _error(
            "browse_snapshot_dynamic_domain_forbidden",
            "dynamic material intake upload, attempt or mutation routes are forbidden",
            403,
        )
    return {
        "records": observed_total,
        "batches": len(ledger_batch_ids),
        "batch_details": len(detail_routes),
        "record_pages": len(page_routes),
    }


def verify_materialized_snapshot(artifacts: Mapping[str, bytes]) -> dict[str, Any]:
    """Verify a snapshot entirely from its in-memory release artifacts."""

    if not isinstance(artifacts, Mapping) or MANIFEST_RELATIVE not in artifacts:
        raise _error(
            "browse_snapshot_manifest_missing", "browse snapshot manifest is missing"
        )
    normalized: dict[str, bytes] = {}
    for relative, raw in artifacts.items():
        if not isinstance(relative, str) or not isinstance(raw, bytes) or not raw:
            raise _error(
                "browse_snapshot_artifact_invalid",
                "snapshot artifact mapping contains an invalid entry",
            )
        safe = _safe_relative(relative).as_posix()
        if safe != relative:
            raise _error(
                "browse_snapshot_path_invalid",
                "snapshot artifact path is not normalized",
            )
        normalized[relative] = raw
    _ensure_unique_casefold_paths(normalized)

    manifest = _parse_canonical_json(
        normalized[MANIFEST_RELATIVE], "browse snapshot manifest"
    )
    if manifest.get("self_sha256") != _self_hash(manifest):
        raise _error(
            "browse_snapshot_self_hash_mismatch",
            "browse snapshot manifest self hash mismatched",
        )
    schema_raw = normalized.get(SNAPSHOT_SCHEMA_ARTIFACT)
    if schema_raw is None:
        raise _error(
            "browse_snapshot_schema_missing", "embedded snapshot schema is missing"
        )
    schema = _strict_json_object(schema_raw, "embedded browse snapshot schema")
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(manifest),
            key=lambda error: tuple(str(value) for value in error.absolute_path),
        )
    except Exception as exc:
        raise _error(
            "browse_snapshot_schema_invalid",
            "embedded snapshot schema is invalid",
        ) from exc
    if errors:
        raise _error(
            "browse_snapshot_manifest_invalid",
            "browse snapshot manifest violates its embedded schema",
        )
    review_catalog_raw = normalized.get(REVIEW_TASK_CATALOG_ARTIFACT)
    if review_catalog_raw is None:
        raise _error(
            "browse_snapshot_review_catalog_missing",
            "teacher review task definitions are missing",
        )
    review_catalog = _parse_canonical_json(
        review_catalog_raw, "teacher review task catalog"
    )
    _reject_dynamic_value(review_catalog)
    if _json_artifact_bytes(review_catalog) != review_catalog_raw:
        raise _error(
            "browse_snapshot_review_catalog_invalid",
            "teacher review task catalog is not canonical",
        )
    if manifest.get("authority") != AUTHORITY:
        raise _error(
            "browse_snapshot_authority_invalid",
            "browse snapshot widened candidate-only authority",
            403,
        )
    if (
        manifest.get("dynamic_domains_excluded") != list(DYNAMIC_DOMAINS_EXCLUDED)
        or manifest.get("dynamic_state_embedded") is not False
        or manifest.get("answer_pixels_embedded") is not False
    ):
        raise _error(
            "browse_snapshot_dynamic_domain_forbidden",
            "snapshot does not preserve dynamic/private domain exclusion",
            403,
        )

    descriptors = manifest.get("artifacts")
    if not isinstance(descriptors, list):
        raise _error(
            "browse_snapshot_manifest_invalid", "artifact descriptors are missing"
        )
    descriptor_paths = [item.get("relative_path") for item in descriptors if isinstance(item, dict)]
    if descriptor_paths != sorted(descriptor_paths):
        raise _error(
            "browse_snapshot_manifest_invalid", "artifact descriptors are not sorted"
        )
    expected_paths = set(normalized) - {MANIFEST_RELATIVE}
    if set(descriptor_paths) != expected_paths or len(descriptor_paths) != len(
        expected_paths
    ):
        raise _error(
            "browse_snapshot_artifact_inventory_mismatch",
            "snapshot contains a rogue or missing artifact",
        )
    for descriptor in descriptors:
        relative = descriptor["relative_path"]
        raw = normalized[relative]
        if descriptor != _artifact_descriptor(relative, raw):
            raise _error(
                "browse_snapshot_artifact_hash_mismatch",
                "snapshot artifact does not match its descriptor",
            )
    openapi_raw = normalized.get(OPENAPI_ARTIFACT)
    if openapi_raw is None or manifest.get("openapi") != {
        **_artifact_descriptor(OPENAPI_ARTIFACT, openapi_raw),
        "api_contract_version": CONTRACT_VERSION,
    }:
        raise _error(
            "browse_snapshot_openapi_mismatch",
            "embedded OpenAPI bytes do not match the snapshot manifest",
        )
    static_manifest_bindings = manifest.get("static_files")
    if not isinstance(static_manifest_bindings, dict) or set(
        static_manifest_bindings
    ) != set(STATIC_FILES):
        raise _error(
            "browse_snapshot_static_binding_mismatch",
            "snapshot static-file bindings are invalid",
        )
    static_raw: dict[str, bytes] = {}
    for name in STATIC_FILES:
        relative = f"webui/{name}"
        raw = normalized.get(relative)
        if raw is None or static_manifest_bindings.get(name) != _artifact_descriptor(
            relative, raw
        ):
            raise _error(
                "browse_snapshot_static_binding_mismatch",
                "snapshot static file does not match the closure manifest",
            )
        static_raw[name] = raw
    overlay_bindings = manifest.get("overlay_manifests")
    if not isinstance(overlay_bindings, dict) or set(overlay_bindings) != {
        "inner",
        "outer",
    }:
        raise _error(
            "browse_snapshot_static_binding_mismatch",
            "snapshot overlay-manifest bindings are invalid",
        )
    overlay_values: list[dict[str, Any]] = []
    for key, relative in (
        ("inner", "manifests/overlay.inner.json"),
        ("outer", "manifests/overlay.outer.json"),
    ):
        raw = normalized.get(relative)
        if raw is None or overlay_bindings.get(key) != _artifact_descriptor(
            relative, raw
        ):
            raise _error(
                "browse_snapshot_static_binding_mismatch",
                "snapshot overlay manifest does not match its descriptor",
            )
        value = _strict_json_object(raw, f"{key} overlay manifest")
        if value.get("gateway_contract") != CONTRACT_VERSION:
            raise _error(
                "browse_snapshot_contract_mismatch",
                "snapshot overlay manifest has an incompatible API contract",
            )
        files = value.get("files")
        if not isinstance(files, dict) or set(files) != set(STATIC_FILES):
            raise _error(
                "browse_snapshot_static_binding_mismatch",
                "snapshot overlay manifest has an invalid UI inventory",
            )
        for name in STATIC_FILES:
            if files[name] != {
                "sha256": _sha256(static_raw[name]),
                "bytes": len(static_raw[name]),
            }:
                raise _error(
                    "browse_snapshot_static_binding_mismatch",
                    "snapshot overlay manifest does not bind frozen UI bytes",
                )
        overlay_values.append(value)
    if overlay_values[0]["files"] != overlay_values[1]["files"]:
        raise _error(
            "browse_snapshot_static_binding_mismatch",
            "inner and outer frozen overlay manifests disagree",
        )
    observed_snapshot_id = _canonical_sha256(
        {"algorithm": SNAPSHOT_ALGORITHM, "artifacts": descriptors}
    )
    if manifest.get("browse_snapshot_id") != observed_snapshot_id:
        raise _error(
            "browse_snapshot_id_mismatch", "browse snapshot identity mismatched"
        )

    route_binding = manifest.get("route_index")
    if not isinstance(route_binding, dict):
        raise _error(
            "browse_snapshot_route_index_missing", "route index binding is missing"
        )
    route_raw = normalized.get(ROUTE_INDEX_RELATIVE)
    if route_raw is None or route_binding != _artifact_descriptor(
        ROUTE_INDEX_RELATIVE, route_raw
    ):
        raise _error(
            "browse_snapshot_route_index_mismatch",
            "route index bytes do not match the manifest",
        )
    route_index = _parse_canonical_json(route_raw, "browse route index")
    if (
        route_index.get("schema_version") != ROUTE_INDEX_SCHEMA_VERSION
        or route_index.get("api_contract_version") != CONTRACT_VERSION
        or route_index.get("self_sha256") != _self_hash(route_index)
        or route_index.get("dynamic_route_prefixes_excluded")
        != list(DYNAMIC_ROUTE_PREFIXES)
    ):
        raise _error(
            "browse_snapshot_route_index_invalid", "route index identity is invalid"
        )
    entries = route_index.get("routes")
    if not isinstance(entries, list) or not entries:
        raise _error(
            "browse_snapshot_route_index_invalid", "route index is empty"
        )
    routes = [entry.get("route") for entry in entries if isinstance(entry, dict)]
    if routes != sorted(routes) or len(routes) != len(set(routes)):
        raise _error(
            "browse_snapshot_route_index_invalid",
            "route index is unsorted or duplicated",
        )
    by_route: dict[str, dict[str, Any]] = {}
    parsed_json_routes: dict[str, dict[str, Any]] = {}
    explicit_image_endpoints: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "method",
            "route",
            "media_type",
            "resource_kind",
            "relative_path",
            "sha256",
            "bytes",
        }:
            raise _error(
                "browse_snapshot_route_index_invalid",
                "route entry shape is invalid",
            )
        route = entry["route"]
        _route_allowed(route)
        if entry["method"] != "GET":
            raise _error(
                "browse_snapshot_route_index_invalid",
                "browse snapshot may contain GET routes only",
            )
        relative = entry["relative_path"]
        raw = normalized.get(relative)
        if raw is None or {
            "relative_path": relative,
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
        } != _artifact_descriptor(relative, raw):
            raise _error(
                "browse_snapshot_route_binding_mismatch",
                "route entry does not bind its artifact bytes",
            )
        media_type = entry["media_type"]
        if media_type == "application/json":
            if relative != _object_path(raw, media_type):
                raise _error(
                    "browse_snapshot_route_binding_mismatch",
                    "JSON route is not content-addressed",
                )
            value = _parse_canonical_json(raw, f"route {route}")
            _reject_dynamic_value(value)
            parsed_json_routes[route] = value
            explicit_image_endpoints.update(_collect_image_endpoints(value))
        elif media_type == "image/png":
            if (
                relative != _object_path(raw, media_type)
                or not _valid_png(raw)
            ):
                raise _error(
                    "browse_snapshot_crop_invalid",
                    "image route does not bind valid content-addressed PNG bytes",
                )
        else:
            raise _error(
                "browse_snapshot_media_type_invalid",
                "route has an unsupported media type",
            )
        by_route[route] = entry
    if not _REQUIRED_CORE_ROUTES <= set(by_route):
        raise _error(
            "browse_snapshot_capture_incomplete",
            "verified route index is missing a core browse route",
        )
    for endpoint in explicit_image_endpoints:
        entry = by_route.get(endpoint)
        if entry is None or entry.get("media_type") != "image/png":
            raise _error(
                "browse_snapshot_crop_missing",
                "a detail references a crop that is absent from the frozen route index",
            )

    detail_counts = _assert_detail_closure(parsed_json_routes, by_route)
    curriculum_counts = _assert_curriculum_closure(parsed_json_routes)
    material_intake_counts = _assert_material_intake_closure(
        normalized, manifest, parsed_json_routes, by_route
    )
    registry = parsed_json_routes["/api/v1/workbench/product-registry"]
    readiness = parsed_json_routes["/api/v1/readiness"]
    if (
        registry.get("registry_id") != manifest.get("registry_id")
        or registry.get("api_contract_version") != manifest.get("api_contract_version")
        or registry.get("ui_build_id") != manifest.get("ui_build_id")
        or registry.get("data_snapshot_id") != manifest.get("data_snapshot_id")
        or registry.get("manifest_sha256")
        != manifest.get("registry_manifest_sha256")
        or readiness.get("api_contract_version")
        != manifest.get("api_contract_version")
        or readiness.get("ui_build_id") != manifest.get("ui_build_id")
        or readiness.get("data_snapshot_id") != manifest.get("data_snapshot_id")
    ):
        raise _error(
            "browse_snapshot_identity_invalid",
            "frozen registry/readiness and closure manifest identities disagree",
        )
    manifest_counts = manifest.get("counts", {})
    wave1_all_count = sum(
        len(value.get("items", ()))
        for route, value in parsed_json_routes.items()
        if urlsplit(route).path
        == "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
        and "node_type" not in parse_qs(urlsplit(route).query, keep_blank_values=True)
        and isinstance(value.get("items"), list)
    )
    if manifest_counts.get("atomic_details_by_scope") != detail_counts:
        raise _error(
            "browse_snapshot_count_mismatch",
            "manifest atomic detail counts were not dynamically derived",
        )
    if manifest_counts.get("wave1_all_hierarchy_nodes") != wave1_all_count:
        raise _error(
            "browse_snapshot_count_mismatch",
            "Wave1 hierarchy-node count was not dynamically derived",
        )
    curriculum_count_fields = {
        "curriculum_volumes": "volumes",
        "curriculum_chapters": "chapters",
        "curriculum_sections": "sections",
        "curriculum_atomic_mappings": "atomic_mappings",
        "curriculum_mapping_entries": "mapping_entries",
        "curriculum_atomic_mappings_by_scope": "atomic_mappings_by_scope",
    }
    if curriculum_counts:
        if any(
            manifest_counts.get(field) != curriculum_counts[key]
            for field, key in curriculum_count_fields.items()
        ):
            raise _error(
                "browse_snapshot_count_mismatch",
                "curriculum counts were not dynamically derived",
            )
    elif any(field in manifest_counts for field in curriculum_count_fields):
        raise _error(
            "browse_snapshot_count_mismatch",
            "legacy snapshot has curriculum counts without a closure",
        )
    material_count_fields = {
        "material_intake_records": "records",
        "material_intake_batches": "batches",
        "material_intake_batch_details": "batch_details",
        "material_intake_record_pages": "record_pages",
    }
    if material_intake_counts is not None:
        if any(
            manifest_counts.get(field) != material_intake_counts[key]
            for field, key in material_count_fields.items()
        ):
            raise _error(
                "browse_snapshot_count_mismatch",
                "material intake counts were not dynamically derived",
            )
    elif any(field in manifest_counts for field in material_count_fields):
        raise _error(
            "browse_snapshot_count_mismatch",
            "legacy snapshot has material intake counts without a closure",
        )
    json_count = sum(
        entry["media_type"] == "application/json" for entry in entries
    )
    binary_count = len(entries) - json_count
    unique_images = {
        entry["sha256"] for entry in entries if entry["media_type"] == "image/png"
    }
    if (
        route_index.get("route_count") != len(entries)
        or route_index.get("json_route_count") != json_count
        or route_index.get("binary_route_count") != binary_count
        or manifest_counts.get("route_count") != len(entries)
        or manifest_counts.get("json_route_count") != json_count
        or manifest_counts.get("binary_route_count") != binary_count
        or manifest_counts.get("unique_crop_assets") != len(unique_images)
        or manifest_counts.get("materialized_artifacts_excluding_manifest")
        != len(descriptors)
        or manifest_counts.get("materialized_bytes_excluding_manifest")
        != sum(item["bytes"] for item in descriptors)
    ):
        raise _error(
            "browse_snapshot_count_mismatch",
            "snapshot counts do not match the frozen bytes",
        )

    backend_raw = normalized.get(BACKEND_IDENTITY_RELATIVE)
    if backend_raw is None:
        raise _error(
            "browse_snapshot_backend_identity_missing",
            "backend build identity is missing",
        )
    backend = _parse_canonical_json(backend_raw, "backend build identity")
    expected_backend_id = _canonical_sha256(
        {
            "algorithm": BACKEND_BUILD_ALGORITHM,
            "api_contract_sha256": backend.get("api_contract_sha256"),
            "sources": backend.get("sources"),
        }
    )
    if (
        backend.get("backend_build_id") != expected_backend_id
        or manifest.get("backend_build_id") != expected_backend_id
        or backend.get("api_contract_sha256") != _sha256(openapi_raw)
        or backend.get("api_contract_bytes") != len(openapi_raw)
        or backend.get("dynamic_state_embedded") is not False
    ):
        raise _error(
            "browse_snapshot_backend_identity_invalid",
            "backend build identity is invalid",
        )
    result = {
        "valid": True,
        "browse_snapshot_id": manifest["browse_snapshot_id"],
        "backend_build_id": manifest["backend_build_id"],
        "data_snapshot_id": manifest["data_snapshot_id"],
        "artifact_count": len(normalized),
        "route_count": len(entries),
        "json_route_count": json_count,
        "binary_route_count": binary_count,
        "unique_crop_assets": len(unique_images),
        "atomic_details_by_scope": detail_counts,
        "authority": dict(AUTHORITY),
    }
    if material_intake_counts is not None:
        result["material_intake"] = material_intake_counts
    if curriculum_counts:
        result["curriculum"] = curriculum_counts
    return result


__all__ = [
    "AUTHORITY",
    "BACKEND_BUILD_ALGORITHM",
    "BACKEND_IDENTITY_RELATIVE",
    "CURRICULUM_CATALOG_ROUTE",
    "CURRICULUM_MAPPING_INDEX_ROUTE",
    "DEFAULT_BACKEND_SOURCE_RELATIVES",
    "DYNAMIC_DOMAINS_EXCLUDED",
    "DYNAMIC_ROUTE_PREFIXES",
    "MANIFEST_RELATIVE",
    "MATERIAL_INTAKE_SOURCE_FILES",
    "REVIEW_TASK_CATALOG_ARTIFACT",
    "ROUTE_INDEX_RELATIVE",
    "ROUTE_INDEX_SCHEMA_VERSION",
    "SNAPSHOT_ALGORITHM",
    "SNAPSHOT_SCHEMA_VERSION",
    "BrowseCapture",
    "BrowseProjectionSource",
    "FrozenBrowsePayload",
    "FrozenWorkbenchBrowseReader",
    "LiveBrowseProjectionSource",
    "WorkbenchReleaseSnapshotError",
    "WorkbenchReleaseSnapshotMaterializer",
    "current_backend_build_id",
    "current_backend_build_identity",
    "verify_materialized_snapshot",
]
