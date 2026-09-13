from __future__ import annotations

import hashlib
import json
import re
import stat
import zlib
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import MasterDirectVisualScanError
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .reader_cancellation import check_read_cancelled
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier

PRODUCT_ID = "SHCHEM-VSCAN-PUDONG-2026-FIRST-MOCK-THEME1-V1"
PAPER_ID = "PAPER-22b0a396c9c1c0a37591"
THEME_ID = "THEME-2c558871ad0b19268d6a"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_pudong_2026_first_mock_theme_v1_2026-08-25"
)
PRODUCT_RELATIVE_POSIX = PRODUCT_RELATIVE.as_posix()
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_FILE_SHA256 = (
    "ea37494740944d7e9ebb631dd58212a19bdbe093650263b9d00d43fde856ca86"
)
EXPECTED_MANIFEST_SELF_SHA256 = (
    "844508eb85929abedca83bfe266779ac1c611571b80bad3742c27b15bbb909f9"
)
EXPECTED_RECORDS_SHA256 = (
    "fcbc0f37121781cfa1aeeb1bbd647d94bd481b7db96f4fe3feff863ae80ac72d"
)
EXPECTED_CROP_MANIFEST_SHA256 = (
    "0f6546147992a82d976507ff93ce4089905a3e9bbde4b638b676e831e513d3ff"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "ef6e679214a5c0999b4f690c8decfe470915406d88c1b315f64fabe6e48dff80"
)
EXPECTED_OUTPUT_BINDING_COUNT = 37
EXPECTED_SOURCE_BINDING_COUNT = 47
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CONTROLLED_VOCAB_RELATIVE = "kb/question_classification_v1/controlled_vocabulary.json"
MASTER_ATOMIC_RELATIVE = (
    "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/"
    "atomic_part_records.jsonl"
)
MASTER_PRINTED_RELATIVE = (
    "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/"
    "printed_question_records.jsonl"
)
MASTER_THEME_RELATIVE = (
    "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/"
    "theme_big_question_records.jsonl"
)
EXPECTED_PRINTED_IDS = tuple(f"PD2026-YM-S1-Q{index}" for index in range(1, 10))
EXPECTED_ATOMIC_IDS = (
    "PD2026-YM-S1-Q1-P1",
    "PD2026-YM-S1-Q2-P1",
    "PD2026-YM-S1-Q3-P1",
    "PD2026-YM-S1-Q4-P1",
    "PD2026-YM-S1-Q4-P2",
    "PD2026-YM-S1-Q5-P1",
    "PD2026-YM-S1-Q5-P2",
    "PD2026-YM-S1-Q6-P1",
    "PD2026-YM-S1-Q7-P1",
    "PD2026-YM-S1-Q8-P1",
    "PD2026-YM-S1-Q9-P1",
)
EXPECTED_SHARED_IDS = frozenset(
    {
        "PD2026-T1-SHARED-OPENING",
        "PD2026-T1-SHARED-PROCESS",
        "PD2026-T1-SHARED-APPARATUS",
        "PD2026-T1-SHARED-TITRATION",
    }
)
KNOWN_QUALITY_NOTES = {
    "PD2026-YM-S1-Q3-P1": "SOURCE_OPTION_EXPRESSION_NOTE",
    "PD2026-YM-S1-Q6-P1": "SOURCE_METHOD_SELECTIVITY_NOTE",
    "PD2026-YM-S1-Q7-P1": "SOURCE_EQUATION_FORMAT_NOTE",
}
EXPECTED_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "generation_allowed",
        "human_chemistry_reviewed",
        "human_reviewed",
        "measured_difficulty_verified",
        "official",
        "pixel_reuse_allowed",
        "publication_allowed",
        "retrieval_allowed",
        "retrieval_ready",
        "rubric_verified",
        "teaching_use_allowed",
        "verified",
    }
)
EXPECTED_GATES = {key: False for key in EXPECTED_GATE_KEYS}
AUTHORITY = {"candidate_only": True, "read_only": True, **EXPECTED_GATES}
COMPARISON_FIELDS = (
    "item_type",
    "selection_rule",
    "primary_K",
    "supporting_K",
    "A",
    "C",
    "R",
    "RP",
    "D",
)
FACTOR_IDS = (
    "information_transformations",
    "reasoning_chain_steps",
    "knowledge_module_span",
    "representation_switches",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_load",
    "dependency_on_prior_parts",
)
EXPECTED_CROP_CATEGORIES = {
    "boundary_context": 2,
    "nonofficial_reference_answer": 10,
    "question": 9,
    "theme_stimulus": 4,
}
EXPECTED_CROP_ROLES = {
    "answer": 9,
    "answer_boundary": 1,
    "answer_context": 1,
    "boundary_context": 1,
    "question": 9,
    "shared_material": 4,
}
PAPER_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": (
        "浦东新区2025学年度第一学期期末教学质量检测 高三化学试卷"
    ),
    "article_cohort_label": "2026届浦东新区高三一模",
    "region": "浦东新区",
    "paper_family": "first_mock",
    "covered_scope": "主题一（水合肼）",
    "complete_paper_local_pages_present": True,
    "complete_paper_visual_scan_claim_allowed": False,
    "pudong_second_mock_claim_allowed": False,
    "putuo_pt_identity_claim_allowed": False,
    "official_status": "nonofficial",
    "source_account": "学教有方",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MASTER_ID = re.compile(r"PD2026-YM-S1-Q[1-9]-P[12]\Z")
_CROP_ID = re.compile(r"PD2026-T1-[A-Z0-9-]{1,80}\Z")
_PRIVATE_TEXT = re.compile(
    r"(?i:file:/+|(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)


class Pudong2026FirstMockThemeDirectVisualScanError(MasterDirectVisualScanError):
    pass


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_by_id: dict[str, dict[str, Any]]
    output_bindings: dict[str, dict[str, Any]]
    output_bytes: dict[str, bytes]
    answer_crop_ids: frozenset[str]
    forbidden_crop_ids: frozenset[str]
    corrected_fields_by_master: dict[str, tuple[str, ...]]
    manifest_self_sha256: str
    master_crosswalk_manifest_self_sha256: str
    paper_identity_boundary: dict[str, Any]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} must be an object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    if not text or not text.endswith("\n") or any(
        not line.strip() for line in text.splitlines()
    ):
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} JSONL shape drifted"
        )
    result: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is invalid",
            ) from exc
        if not isinstance(row, dict):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is not an object",
            )
        result.append(row)
    return result


def _safe_relative(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
    ):
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    return value


def _is_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _binding_index(value: Any, label: str, expected_count: int) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) - {
            "path",
            "bytes",
            "sha256",
            "role",
            "format",
            "width",
            "height",
        }:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} shape drifted"
            )
        path = _safe_relative(item.get("path"))
        if (
            path in result
            or type(item.get("bytes")) is not int
            or item["bytes"] < 1
            or not isinstance(item.get("sha256"), str)
            or _HEX64.fullmatch(item["sha256"]) is None
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} value drifted"
            )
        result[path] = dict(item)
    return result


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
        raise Pudong2026FirstMockThemeDirectVisualScanError(
            "master_direct_scan_gate_elevated", f"{label} gates drifted"
        )


class Pudong2026FirstMockThemeDirectVisualScanReader:
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_path_invalid", "product root leaves sh-chem-db"
            )
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _verified_file(self, root: Path, relative: str) -> tuple[Path, bytes]:
        check_read_cancelled()
        relative = _safe_relative(relative)
        cursor = root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink() or _is_reparse(cursor):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "bound paths may not traverse links or reparse points",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_path_invalid", "a bound file leaves its root"
            )
        try:
            return resolved, resolved.read_bytes()
        except OSError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_file_read_failed", "a bound file is unreadable"
            ) from exc

    def _verify_bindings(
        self, root: Path, bindings: dict[str, dict[str, Any]], label: str
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            _, raw = self._verified_file(root, relative)
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_binding_mismatch",
                    f"{label} hash or byte binding drifted",
                )
            result[relative] = raw
        return result

    @staticmethod
    def _png_dimensions(raw: bytes) -> tuple[int, int] | None:
        if not raw.startswith(PNG_SIGNATURE):
            return None
        offset = len(PNG_SIGNATURE)
        saw_ihdr = False
        saw_idat = False
        dimensions: tuple[int, int] | None = None
        while offset < len(raw):
            if offset + 12 > len(raw):
                return None
            length = int.from_bytes(raw[offset : offset + 4], "big")
            chunk_type = raw[offset + 4 : offset + 8]
            end = offset + 12 + length
            if end > len(raw):
                return None
            payload = raw[offset + 8 : offset + 8 + length]
            expected_crc = int.from_bytes(raw[offset + 8 + length : end], "big")
            if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
                return None
            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    return None
                dimensions = (
                    int.from_bytes(payload[:4], "big"),
                    int.from_bytes(payload[4:8], "big"),
                )
                if dimensions[0] < 1 or dimensions[1] < 1:
                    return None
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                return None
            saw_idat = saw_idat or chunk_type == b"IDAT"
            if chunk_type == b"IEND":
                return (
                    dimensions
                    if length == 0 and saw_ihdr and saw_idat and end == len(raw)
                    else None
                )
            offset = end
        return None

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        value = deepcopy(manifest)
        value["self_sha256"] = None
        return _sha256(_canonical_bytes(value))

    @staticmethod
    def _evidence_binding(record: dict[str, Any], crop_by_id: dict[str, dict[str, Any]]) -> str:
        viewed_ids = [item["crop_id"] for item in record["viewed_evidence"]]
        answer_ids = [
            item["crop_id"] for item in record["answer"]["visual_alignment_evidence"]
        ]
        payload = {
            "atomic_part_id": record["hierarchy"]["atomic_part_id"],
            "evidence": [
                {"crop_id": crop_id, "sha256": crop_by_id[crop_id]["sha256"]}
                for crop_id in viewed_ids
            ],
            "answer_evidence": [
                {"crop_id": crop_id, "sha256": crop_by_id[crop_id]["sha256"]}
                for crop_id in answer_ids
            ],
            "classification": record["classification"],
            "difficulty": record["difficulty"],
            "dependency": record["dependency"],
            "reference_answer_sha256": _sha256(
                record["answer"]["reference_summary_zh"].encode("utf-8")
            ),
        }
        return _sha256(_canonical_bytes(payload))

    def _output_closure(self, expected: set[str]) -> None:
        try:
            entries = list(self.product_root.rglob("*"))
        except OSError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_file_read_failed", "product tree is unreadable"
            ) from exc
        if any(entry.is_symlink() or _is_reparse(entry) for entry in entries):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_symlink_denied", "product contains a link"
            )
        actual = {
            path.relative_to(self.product_root).as_posix()
            for path in entries
            if path.is_file()
            and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts
            and path.name != "manifest.json"
        }
        if actual != expected:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "recursive non-cache output closure drifted",
            )

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if (
            len(manifest_raw) != 9107
            or _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        expected_claims = {
            "candidate_only": True,
            "complete_paper_scanned": False,
            "generation_allowed": False,
            "human_reviewed": False,
            "official": False,
            "pixel_reuse_allowed": False,
            "publication_allowed": False,
            "retrieval_ready": False,
            "teaching_use_allowed": False,
        }
        if (
            manifest.get("product_id") != PRODUCT_ID
            or manifest.get("paper_id") != PAPER_ID
            or manifest.get("theme_id") != THEME_ID
            or manifest.get("theme_title") != "水合肼"
            or manifest.get("coverage_scope")
            != "theme_1_water_hydrazine_only_11_atomic_not_complete_paper"
            or manifest.get("hierarchy_counts")
            != {"paper": 1, "theme": 1, "printed_question": 9, "atomic_part": 11}
            or manifest.get("claims") != expected_claims
            or manifest.get("self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
            or manifest.get("output_file_count") != EXPECTED_OUTPUT_BINDING_COUNT
            or manifest.get("source_file_count") != EXPECTED_SOURCE_BINDING_COUNT
            or manifest.get("crop_count") != 25
            or manifest.get("crop_category_counts") != EXPECTED_CROP_CATEGORIES
            or manifest.get("dependency_kind_counts")
            != {"independent": 2, "shared_theme_context": 9}
            or manifest.get("answer_coverage")
            != {
                "present_part_aligned": 11,
                "source_authority": "nonofficial_reference",
                "independently_verified": False,
                "quality_note_count": 3,
            }
            or manifest.get("max_output_bytes") != MAX_CROP_BYTES
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest contract drifted"
            )
        output_bindings = _binding_index(
            manifest.get("outputs"), "output", EXPECTED_OUTPUT_BINDING_COUNT
        )
        required_outputs = {
            "crop_manifest.json",
            "coverage_report.json",
            "disagreement_report.json",
            "scan_record_schema.json",
            "scan_records.jsonl",
            "source_manifest.json",
            "theme_summary.json",
        }
        if not required_outputs <= set(output_bindings):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", "required output is unbound"
            )
        self._output_closure(set(output_bindings))
        output_bytes = self._verify_bindings(self.product_root, output_bindings, "output")
        if any(len(raw) > MAX_CROP_BYTES for raw in output_bytes.values()):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", "an output exceeds one MiB"
            )
        if (
            _sha256(output_bytes["scan_records.jsonl"]) != EXPECTED_RECORDS_SHA256
            or _sha256(output_bytes["crop_manifest.json"])
            != EXPECTED_CROP_MANIFEST_SHA256
            or _sha256(output_bytes["source_manifest.json"])
            != EXPECTED_SOURCE_MANIFEST_SHA256
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", "key output bytes drifted"
            )

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        source_boundary = source_manifest.get("source_boundary")
        if (
            source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("paper_id") != PAPER_ID
            or source_manifest.get("theme_id") != THEME_ID
            or source_manifest.get("source_file_count") != EXPECTED_SOURCE_BINDING_COUNT
            or source_boundary
            != {
                key: value
                for key, value in PAPER_IDENTITY_BOUNDARY.items()
                if key != "official_status"
            }
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_source_manifest_invalid",
                "source identity or theme-only boundary drifted",
            )
        source_bindings = _binding_index(
            source_manifest.get("sources"), "source", EXPECTED_SOURCE_BINDING_COUNT
        )
        if not {
            CONTROLLED_VOCAB_RELATIVE,
            MASTER_ATOMIC_RELATIVE,
            MASTER_PRINTED_RELATIVE,
            MASTER_THEME_RELATIVE,
        } <= set(source_bindings):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "source bindings omit hierarchy/vocabulary sources",
            )
        if any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_binding_invalid", "dynamic central source is forbidden"
            )
        source_bytes = self._verify_bindings(self.shchem_root, source_bindings, "source")

        crop_manifest = _json_object(output_bytes["crop_manifest.json"], "crop manifest")
        crops = crop_manifest.get("crops")
        if not isinstance(crops, list) or len(crops) != 25:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop count drifted"
            )
        crop_by_id: dict[str, dict[str, Any]] = {}
        output_paths: set[str] = set()
        for crop in crops:
            if not isinstance(crop, dict):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop row is invalid"
                )
            crop_id = crop.get("crop_id")
            output_path = crop.get("output_path")
            if (
                not isinstance(crop_id, str)
                or _CROP_ID.fullmatch(crop_id) is None
                or crop_id in crop_by_id
                or not isinstance(output_path, str)
                or output_path in output_paths
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop identity or output path drifted",
                )
            prefix = PRODUCT_RELATIVE_POSIX + "/"
            if not output_path.startswith(prefix):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop leaves product prefix"
                )
            internal = _safe_relative(output_path[len(prefix) :])
            binding = output_bindings.get(internal)
            raw = output_bytes.get(internal)
            source = source_bindings.get(crop.get("source_path"))
            upstream = source_bindings.get(crop.get("upstream_crop_path"))
            dimensions = self._png_dimensions(raw or b"")
            if (
                binding is None
                or raw is None
                or source is None
                or upstream is None
                or crop.get("source_sha256") != source["sha256"]
                or crop.get("source_bytes") != source["bytes"]
                or crop.get("upstream_crop_sha256") != upstream["sha256"]
                or crop.get("upstream_crop_bytes") != upstream["bytes"]
                or crop.get("sha256") != binding["sha256"]
                or crop.get("bytes") != binding["bytes"]
                or crop.get("sha256") != upstream["sha256"]
                or crop.get("bytes") != upstream["bytes"]
                or dimensions != (crop.get("width"), crop.get("height"))
                or crop.get("visual_inspection_status")
                != "source_page_and_crop_actually_viewed_by_primary_model_2026-08-25"
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_crop_binding_invalid", "crop binding drifted"
                )
            role = crop.get("evidence_role")
            exposable = crop.get("http_exposable")
            if (role in {"question", "shared_material"}) != (exposable is True):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_crop_role_invalid", "crop HTTP boundary drifted"
                )
            crop_by_id[crop_id] = crop
            output_paths.add(output_path)
        categories = dict(sorted(Counter(crop["category"] for crop in crops).items()))
        roles = dict(sorted(Counter(crop["evidence_role"] for crop in crops).items()))
        if (
            crop_manifest.get("product_id") != PRODUCT_ID
            or crop_manifest.get("paper_id") != PAPER_ID
            or crop_manifest.get("theme_id") != THEME_ID
            or crop_manifest.get("crop_count") != 25
            or crop_manifest.get("category_counts") != EXPECTED_CROP_CATEGORIES
            or crop_manifest.get("role_counts") != EXPECTED_CROP_ROLES
            or crop_manifest.get("pixel_verification") is not True
            or crop_manifest.get("max_output_bytes") != MAX_CROP_BYTES
            or categories != EXPECTED_CROP_CATEGORIES
            or roles != EXPECTED_CROP_ROLES
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop contract drifted"
            )

        schema = _json_object(output_bytes["scan_record_schema.json"], "scan schema")
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "scan records")
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_schema_invalid", "scan schema is invalid"
            ) from exc
        if len(records) != 11:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count is not 11"
            )
        for index, record in enumerate(records, 1):
            issue = next(validator.iter_errors(record), None)
            if issue is not None:
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} failed its bound schema",
                )

        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity is unavailable",
            ) from exc
        master_ids = set(master_identity["master_atomic_ids"])
        exact_ids = set(master_identity["exact_master_ids"])
        if (
            len(master_ids) != 470
            or len(exact_ids) != 169
            or not set(EXPECTED_ATOMIC_IDS) <= master_ids
            or set(EXPECTED_ATOMIC_IDS) & exact_ids
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "Pudong theme rows are absent from master470 or overlap exact169",
            )

        master_atoms = {
            row["atomic_part_id"]: row
            for row in _jsonl_objects(source_bytes[MASTER_ATOMIC_RELATIVE], "master atomic")
            if row.get("parent_paper_id") == PAPER_ID
        }
        master_printed = {
            row["printed_question_id"]: row
            for row in _jsonl_objects(
                source_bytes[MASTER_PRINTED_RELATIVE], "master printed"
            )
            if row.get("parent_paper_id") == PAPER_ID
        }
        master_themes = {
            row["theme_big_question_id"]: row
            for row in _jsonl_objects(source_bytes[MASTER_THEME_RELATIVE], "master theme")
            if row.get("parent_paper_id") == PAPER_ID
        }
        if (
            set(master_atoms) != set(EXPECTED_ATOMIC_IDS)
            or set(master_printed) != set(EXPECTED_PRINTED_IDS)
            or set(master_themes) != {THEME_ID}
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_parent_chain_invalid",
                "Pudong Master paper/theme/printed/atomic sets drifted",
            )

        controlled = _json_object(
            source_bytes[CONTROLLED_VOCAB_RELATIVE], "controlled vocabulary"
        )
        try:
            difficulty_contract = controlled["difficulty_factor_contract"]
            required_factor_ids = tuple(difficulty_contract["required_dimension_ids"])
            numeric_ids = set(difficulty_contract["numeric_nonnegative_dimensions"])
            categorical = difficulty_contract["categorical_values"]
        except (KeyError, TypeError) as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_difficulty_invalid",
                "controlled difficulty contract is unavailable",
            ) from exc
        if (
            required_factor_ids != FACTOR_IDS
            or numeric_ids != set(FACTOR_IDS[:4])
            or set(categorical) != set(FACTOR_IDS[4:])
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_difficulty_invalid", "D10 vocabulary drifted"
            )

        by_master_id: dict[str, dict[str, Any]] = {}
        corrected_fields: dict[str, tuple[str, ...]] = {}
        referenced_question: set[str] = set()
        referenced_answer: set[str] = set()
        referenced_shared: set[str] = set()
        comparison_counts: Counter[str] = Counter()
        dependency_counts: Counter[str] = Counter()
        corrected_nodes: set[str] = set()
        quality_ids: list[str] = []
        for index, (expected_id, record) in enumerate(
            zip(EXPECTED_ATOMIC_IDS, records, strict=True), 1
        ):
            hierarchy = record["hierarchy"]
            master_id = hierarchy.get("atomic_part_id")
            if (
                master_id != expected_id
                or _MASTER_ID.fullmatch(master_id or "") is None
                or master_id in by_master_id
                or record.get("scan_id") != f"VS-PD2026-T1-{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record order or identity drifted"
                )
            printed_id = str(master_atoms[master_id].get("parent_printed_question_id"))
            printed_sequence = int(printed_id.rsplit("Q", 1)[-1])
            atomic_sequence = int(master_id.rsplit("P", 1)[-1])
            if (
                hierarchy
                != {
                    "atomic_part_id": master_id,
                    "atomic_sequence_in_printed": atomic_sequence,
                    "paper_id": PAPER_ID,
                    "printed_question_id": printed_id,
                    "printed_sequence": printed_sequence,
                    "sequence_basis": "Master470四层父链与卷面顺序逐项绑定",
                    "theme_id": THEME_ID,
                    "theme_sequence": 1,
                    "theme_title": "水合肼",
                }
                or master_atoms[master_id].get("parent_theme_big_question_id")
                != THEME_ID
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_parent_chain_invalid", "record parent chain drifted"
                )
            _false_gates(record.get("authority_gates"), "record")
            boundary = record.get("identity_boundary")
            if boundary != {
                "article_cohort_label": "2026届浦东新区高三一模",
                "complete_paper_claim_allowed": False,
                "coverage_scope": "theme_1_water_hydrazine_only_11_atomic_not_complete_paper",
                "covered_theme_title": "水合肼",
                "official_status": "nonofficial",
                "paper_face_title_literal": (
                    "浦东新区2025学年度第一学期期末教学质量检测 高三化学试卷"
                ),
                "pudong_second_mock_claim_allowed": False,
                "putuo_pt_identity_claim_allowed": False,
            }:
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_identity_boundary_invalid",
                    "record theme-only identity boundary drifted",
                )

            viewed = record.get("viewed_evidence")
            if not isinstance(viewed, list):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "viewed evidence is invalid"
                )
            viewed_ids = [item.get("crop_id") for item in viewed]
            if len(viewed_ids) != len(set(viewed_ids)) or any(
                crop_id not in crop_by_id for crop_id in viewed_ids
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "evidence identity drifted"
                )
            questions = [
                crop_id
                for crop_id in viewed_ids
                if crop_by_id[crop_id]["evidence_role"] == "question"
            ]
            shared = [
                crop_id
                for crop_id in viewed_ids
                if crop_by_id[crop_id]["evidence_role"] == "shared_material"
            ]
            if questions != [f"PD2026-T1-Q{printed_sequence:02d}-QUESTION"] or not shared:
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_evidence_invalid",
                    "record question/shared evidence contract drifted",
                )
            for descriptor in viewed:
                crop = crop_by_id[descriptor["crop_id"]]
                if (
                    descriptor.get("evidence_role") != crop["evidence_role"]
                    or descriptor.get("visual_inspection_status")
                    != "source_page_and_crop_actually_viewed_by_primary_model_2026-08-25"
                    or any(
                        descriptor.get(key) != crop.get(key)
                        for key in ("sha256", "bytes", "width", "height", "source_page")
                    )
                ):
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_evidence_invalid",
                        "record evidence descriptor drifted",
                    )
            referenced_question.update(questions)
            referenced_shared.update(shared)

            dependency = record.get("dependency")
            if not isinstance(dependency, dict):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency is absent"
                )
            dependency_kind = dependency.get("dependency_kind")
            dependency_counts[dependency_kind] += 1
            dep_shared = dependency.get("shared_material_crop_ids")
            prior = dependency.get("prior_atomic_part_ids")
            if (
                dependency_kind not in {"independent", "shared_theme_context"}
                or not isinstance(dep_shared, list)
                or not isinstance(prior, list)
                or prior
                or not set(dep_shared) <= set(shared)
                or (
                    dependency_kind == "independent" and dep_shared
                )
                or (
                    dependency_kind == "shared_theme_context" and not dep_shared
                )
                or not isinstance(dependency.get("analysis_zh"), str)
                or not dependency["analysis_zh"]
                or dependency.get("conclusion_use_zh")
                != "不使用任何前序最小作答单元的结论。"
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_dependency_invalid",
                    "theme context or prior dependency contract drifted",
                )

            difficulty = record.get("difficulty")
            factors = difficulty.get("factors") if isinstance(difficulty, dict) else None
            if (
                not isinstance(factors, list)
                or tuple(factor.get("dimension_id") for factor in factors) != FACTOR_IDS
                or difficulty.get("is_measured") is not False
                or difficulty.get("measured_difficulty") is not None
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "D10 structure drifted"
                )
            factor_values = {factor["dimension_id"]: factor.get("value") for factor in factors}
            for factor in factors:
                evidence_ids = factor.get("evidence_crop_ids")
                if (
                    not isinstance(evidence_ids, list)
                    or not evidence_ids
                    or not set(evidence_ids) <= set(viewed_ids)
                ):
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_difficulty_invalid",
                        "D10 evidence leaves the viewed crop closure",
                    )
            if any(
                type(factor_values.get(factor_id)) is not int
                or factor_values[factor_id] < 0
                for factor_id in numeric_ids
            ) or any(
                factor_values.get(factor_id) not in allowed
                for factor_id, allowed in categorical.items()
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "D10 value drifted"
                )
            if factor_values["dependency_on_prior_parts"] != "independent":
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid",
                    "D10 prior dependency contradicts the explicit empty edge set",
                )

            answer = record.get("answer")
            if (
                not isinstance(answer, dict)
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("source_authority") != "nonofficial_reference"
                or answer.get("source_authority_zh") != "学教有方转载的非官方参考答案"
                or answer.get("independently_verified") is not False
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or answer.get("alignment_status")
                != "present_part_aligned_to_nonofficial_reference_pixels"
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not answer["reference_summary_zh"]
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_answer_invalid", "reference answer boundary drifted"
                )
            answer_evidence = answer.get("visual_alignment_evidence")
            expected_answer_id = f"PD2026-T1-Q{printed_sequence:02d}-ANSWER"
            if (
                not isinstance(answer_evidence, list)
                or [item.get("crop_id") for item in answer_evidence]
                != [expected_answer_id]
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer alignment drifted"
                )
            for descriptor in answer_evidence:
                crop = crop_by_id[descriptor["crop_id"]]
                if (
                    crop.get("evidence_role") != "answer"
                    or crop.get("category") != "nonofficial_reference_answer"
                    or crop.get("http_exposable") is not False
                    or descriptor.get("visual_inspection_status")
                    != "aligned_via_actually_viewed_nonofficial_reference_answer_page_and_crop"
                    or any(
                        descriptor.get(key) != crop.get(key)
                        for key in ("sha256", "bytes", "width", "height", "source_page")
                    )
                ):
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_answer_invalid", "answer crop binding drifted"
                    )
            referenced_answer.add(expected_answer_id)

            candidate = record.get("candidate_analysis")
            expected_quality = KNOWN_QUALITY_NOTES.get(master_id)
            if (
                not isinstance(candidate, dict)
                or candidate.get("candidate_only") is not True
                or candidate.get("correctness_verified") is not False
                or not isinstance(candidate.get("solution_path_zh"), list)
                or not candidate["solution_path_zh"]
                or candidate.get("known_quality_note_code") != expected_quality
                or bool(answer.get("quality_note")) != bool(expected_quality)
                or answer.get("quality_note") != candidate.get("known_quality_note_zh")
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_quality_note_invalid",
                    "candidate answer quality-note binding drifted",
                )
            if expected_quality:
                quality_ids.append(master_id)

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(COMPARISON_FIELDS):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_comparison_invalid", "comparison fields drifted"
                )
            corrected: list[str] = []
            for field in COMPARISON_FIELDS:
                item = comparison[field]
                state = item.get("compare") if isinstance(item, dict) else None
                if state not in {"agree", "corrected", "blocked"}:
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_comparison_invalid",
                        "comparison state drifted",
                    )
                comparison_counts[state] += 1
                if state == "corrected":
                    corrected.append(field)
            corrected_fields[master_id] = tuple(corrected)
            if corrected:
                corrected_nodes.add(master_id)
            if record.get("evidence_binding_sha256") != self._evidence_binding(
                record, crop_by_id
            ):
                raise Pudong2026FirstMockThemeDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "record evidence hash drifted"
                )
            by_master_id[master_id] = record

        question_ids = {
            crop_id
            for crop_id, crop in crop_by_id.items()
            if crop["evidence_role"] == "question"
        }
        answer_ids = {
            crop_id
            for crop_id, crop in crop_by_id.items()
            if crop["evidence_role"] == "answer"
        }
        shared_ids = {
            crop_id
            for crop_id, crop in crop_by_id.items()
            if crop["evidence_role"] == "shared_material"
        }
        if (
            referenced_question != question_ids
            or len(question_ids) != 9
            or referenced_answer != answer_ids
            or len(answer_ids) != 9
            or referenced_shared != shared_ids
            or shared_ids != EXPECTED_SHARED_IDS
            or dict(sorted(dependency_counts.items()))
            != {"independent": 2, "shared_theme_context": 9}
            or comparison_counts != Counter({"agree": 98, "corrected": 1, "blocked": 0})
            or corrected_nodes != {"PD2026-YM-S1-Q6-P1"}
            or quality_ids != list(KNOWN_QUALITY_NOTES)
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_count_mismatch", "derived record closure drifted"
            )

        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        disagreement = _json_object(
            output_bytes["disagreement_report.json"], "disagreement"
        )
        theme_summary = _json_object(output_bytes["theme_summary.json"], "theme summary")
        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("paper_count") != 1
            or coverage.get("theme_count") != 1
            or coverage.get("printed_question_count") != 9
            or coverage.get("atomic_part_count") != 11
            or coverage.get("visual_scan_completed_count") != 11
            or coverage.get("blocked_count") != 0
            or coverage.get("answer_alignment")
            != {
                "present_part_aligned": 11,
                "independently_verified": 0,
                "source_authority": "nonofficial_reference",
            }
            or coverage.get("dependency_kind_counts")
            != {"independent": 2, "shared_theme_context": 9}
            or coverage.get("comparison_totals")
            != {"agree": 98, "blocked": 0, "corrected": 1}
            or coverage.get("corrected_node_count") != 1
            or disagreement.get("product_id") != PRODUCT_ID
            or disagreement.get("blocked_node_count") != 0
            or disagreement.get("corrected_node_count") != 1
            or disagreement.get("corrected_atomic_part_ids")
            != ["PD2026-YM-S1-Q6-P1"]
            or disagreement.get("quality_note_atomic_part_ids") != quality_ids
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_report_invalid", "coverage/disagreement drifted"
            )
        printed_summary = theme_summary.get("printed_questions_in_order")
        if (
            theme_summary.get("product_id") != PRODUCT_ID
            or theme_summary.get("paper_id") != PAPER_ID
            or theme_summary.get("theme_id") != THEME_ID
            or theme_summary.get("theme_title_literal") != "水合肼"
            or theme_summary.get("theme_order") != 1
            or theme_summary.get("question_page_span") != [1, 2]
            or theme_summary.get("answer_page_span") != [1]
            or theme_summary.get("atomic_part_ids_in_order") != list(EXPECTED_ATOMIC_IDS)
            or not isinstance(printed_summary, list)
            or [row.get("printed_question_id") for row in printed_summary]
            != list(EXPECTED_PRINTED_IDS)
            or theme_summary.get("prior_conclusion_dependency_edges") != []
            or theme_summary.get("context_first_review_completed") is not True
            or theme_summary.get("all_question_and_answer_pages_for_theme_viewed")
            is not True
            or set(theme_summary.get("all_crop_ids", ())) != set(crop_by_id)
            or theme_summary.get("answer_policy")
            != {
                "availability": "present_part_aligned",
                "source_authority": "nonofficial_reference",
                "independently_verified": False,
                "answer_verified": False,
            }
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_theme_summary_invalid", "theme summary drifted"
            )
        for relative, raw in output_bytes.items():
            if Path(relative).suffix.lower() in {".json", ".jsonl", ".md", ".py"}:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_privacy_invalid", "text output is not UTF-8"
                    ) from exc
                if _PRIVATE_TEXT.search(text):
                    raise Pudong2026FirstMockThemeDirectVisualScanError(
                        "master_direct_scan_privacy_invalid",
                        "product output contains an absolute local locator",
                    )

        forbidden_crop_ids = frozenset(
            crop_id
            for crop_id, crop in crop_by_id.items()
            if crop["evidence_role"] not in {"question", "shared_material"}
        )
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids=frozenset(answer_ids),
            forbidden_crop_ids=forbidden_crop_ids,
            corrected_fields_by_master=corrected_fields,
            manifest_self_sha256=EXPECTED_MANIFEST_SELF_SHA256,
            master_crosswalk_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
            paper_identity_boundary=deepcopy(PAPER_IDENTITY_BOUNDARY),
        )

    @staticmethod
    def _coverage() -> dict[str, int]:
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 11,
            "visual_scanned_master_atomic": 180,
            "remaining_unscanned": 290,
            "direct_exact_overlap": 0,
        }

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "master_crosswalk_manifest_self_sha256": (
                snapshot.master_crosswalk_manifest_self_sha256
            ),
            "output_binding_count": EXPECTED_OUTPUT_BINDING_COUNT,
            "source_binding_count": EXPECTED_SOURCE_BINDING_COUNT,
            "record_count": 11,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
        }

    @staticmethod
    def _reference_answer(record: dict[str, Any]) -> dict[str, Any]:
        projected = project_reference_answer(
            record["answer"],
            record["risks_and_limits"],
            record["candidate_analysis"].get("known_quality_note_code"),
        )
        projected["quality_note"] = record["answer"].get("quality_note")
        return projected

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": {
                "expected_themes": 1,
                "expected_printed_questions": 9,
                "expected_atomic_parts": 11,
                "scan_records": 11,
                "visual_scan_completed": 11,
                "blocked_pending_broader_crop": 0,
                "question_crop_bindings": 9,
                "shared_crop_bindings": 4,
                "nonofficial_answer_crop_bindings": 9,
                "boundary_crop_bindings": 3,
                "total_exact_crop_bindings": 25,
            },
            "coverage": self._coverage(),
            "paper_identity_boundary": deepcopy(snapshot.paper_identity_boundary),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def catalog(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        items: list[dict[str, Any]] = []
        for record in snapshot.records:
            master_id = record["hierarchy"]["atomic_part_id"]
            items.append(
                {
                    "master_node_id": master_id,
                    "scan_status": "visual_scan_completed",
                    "detail_available": True,
                    "question_evidence_count": sum(
                        item["evidence_role"] == "question"
                        for item in record["viewed_evidence"]
                    ),
                    "shared_evidence_count": sum(
                        item["evidence_role"] == "shared_material"
                        for item in record["viewed_evidence"]
                    ),
                    "question_pixels_available": True,
                    "corrected_fields": list(
                        snapshot.corrected_fields_by_master[master_id]
                    ),
                    **reference_answer_catalog_metadata(
                        record["answer"],
                        record["risks_and_limits"],
                        record["candidate_analysis"].get("known_quality_note_code"),
                    ),
                }
            )
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_ids": [item["master_node_id"] for item in items],
            "items": items,
            "count": len(items),
            "coverage": self._coverage(),
            "paper_identity_boundary": deepcopy(snapshot.paper_identity_boundary),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def detail(self, master_node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except SecurityError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Pudong first-mock theme-one direct scan",
                404,
            )
        answer = record["answer"]
        candidate = record["candidate_analysis"]
        dependency = record["dependency"]
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": record["scan_id"],
            "scan_status": record["scan_status"],
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(record["identity_boundary"]),
            "visible_summary_zh": record["visible_summary_zh"],
            "response_requirement_zh": record["response_requirement_zh"],
            "theme_chain_role": deepcopy(record["theme_chain_role"]),
            "dependency": {
                "analysis_zh": dependency["analysis_zh"],
                "prior_atomic_part_ids": deepcopy(
                    dependency["prior_atomic_part_ids"]
                ),
                "shared_material_crop_ids": deepcopy(
                    dependency["shared_material_crop_ids"]
                ),
            },
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "chemistry_observations": deepcopy(record["chemistry_observations"]),
            "candidate_analysis": {
                "candidate_only": True,
                "correctness_verified": False,
                "solution_path_zh": deepcopy(candidate["solution_path_zh"]),
            },
            "risks_and_limits": deepcopy(record["risks_and_limits"]),
            "comparison_with_prior_candidate": deepcopy(
                record["comparison_with_prior_candidate"]
            ),
            "evidence_descriptors": [
                {
                    **deepcopy(item),
                    "content_type": "image/png",
                    "access": "teacher_loopback_read_only",
                }
                for item in record["viewed_evidence"]
            ],
            "answer_boundary": {
                "availability": answer["availability"],
                "authority": answer["authority"],
                "verified": False,
            },
            "reference_answer": self._reference_answer(record),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(
        self, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        try:
            validate_identifier(master_node_id, "master_node_id")
            validate_identifier(crop_id, "crop_id")
        except SecurityError as exc:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Pudong first-mock theme-one direct scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        if crop_id in snapshot.forbidden_crop_ids or (
            crop is not None
            and crop.get("evidence_role") not in {"question", "shared_material"}
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_crop_role_denied",
                "answer and boundary crops are forbidden",
                403,
            )
        descriptor = next(
            (
                item
                for item in record["viewed_evidence"]
                if item["crop_id"] == crop_id
                and item["evidence_role"] in {"question", "shared_material"}
            ),
            None,
        )
        if descriptor is None:
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        output_path = str(crop.get("output_path", "")) if crop else ""
        prefix = PRODUCT_RELATIVE_POSIX + "/"
        internal = output_path[len(prefix) :] if output_path.startswith(prefix) else ""
        binding = snapshot.output_bindings.get(internal)
        raw = snapshot.output_bytes.get(internal)
        if (
            crop is None
            or binding is None
            or raw is None
            or crop.get("http_exposable") is not True
            or crop.get("evidence_role") != descriptor["evidence_role"]
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or len(raw) > MAX_CROP_BYTES
            or _sha256(raw) != descriptor["sha256"]
            or self._png_dimensions(raw)
            != (descriptor["width"], descriptor["height"])
        ):
            raise Pudong2026FirstMockThemeDirectVisualScanError(
                "master_direct_scan_crop_binding_invalid", "served crop binding drifted"
            )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


__all__ = [
    "AUTHORITY",
    "EXPECTED_ATOMIC_IDS",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "EXPECTED_OUTPUT_BINDING_COUNT",
    "EXPECTED_SOURCE_BINDING_COUNT",
    "KNOWN_QUALITY_NOTES",
    "MAX_CROP_BYTES",
    "PAPER_ID",
    "PAPER_IDENTITY_BOUNDARY",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "THEME_ID",
    "Pudong2026FirstMockThemeDirectVisualScanError",
    "Pudong2026FirstMockThemeDirectVisualScanReader",
]
