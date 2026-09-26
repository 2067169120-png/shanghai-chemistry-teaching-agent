from __future__ import annotations

import hashlib
import json
import re
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
from .reference_answer import project_reference_answer
from .security import SecurityError, validate_identifier


PRODUCT_ID = "SHCHEM-VSCAN-2026-LEVEL-RECALL-35-V1"
PAPER_ID = "PAPER-7c4d94620ca1ced2a5ec"
SOURCE_ID = "SHANGHAI-LEVEL-EXAM-2026-DUAL-SOURCE-NONOFFICIAL-RECALL"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_shanghai_exam_2026_recall_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"

# Bound to the final immutable product after its independent build/validation
# completes.  These sentinel values keep an unfinished product fail-closed and
# this module is not registered by the aggregate reader until that freeze.
EXPECTED_MANIFEST_SELF_SHA256 = (
    "643aee54860cadd87cc935de4baa28666eb1780f917f96ab110becbdf4e081a2"
)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "9cfdcf805b298b1899a3a985d793ef410726f94b7f2296679c92045a8c4eb5ff"
)
EXPECTED_RECORDS_SHA256 = (
    "ff1854e1ea252848c03ae04c27d1cf22fec7ca1342d4fba0506d7f78f6ef6c91"
)
EXPECTED_CROP_MANIFEST_SHA256 = (
    "e83383c42d5fd02aaec7ce939df6a8b072b4d7b5877ab744937d364ab32a7f9c"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "56c765f223c95ee2fb14066998d47f6e00451821e0a7f51e5d9149a8b7e10edf"
)
EXPECTED_OUTPUT_BINDING_COUNT = 110
EXPECTED_SOURCE_BINDING_COUNT = 45
EXPECTED_CROP_COUNT = 99
EXPECTED_CROP_CATEGORY_COUNTS = {
    "nonofficial_reference_answer": 56,
    "paper_identity": 1,
    "question": 37,
    "theme_stimulus": 5,
}

EXPECTED_HIERARCHY_COUNTS = {
    "paper": 1,
    "theme": 4,
    "printed_question": 29,
    "atomic_part": 35,
}
EXPECTED_THEME_ATOMIC_COUNTS = {1: 8, 2: 11, 3: 10, 5: 6}
EXPECTED_THEME_IDS = {
    "THEME-fad4040acec82304d3b0": 1,
    "THEME-e9d8031fab60a59aa50a": 2,
    "THEME-58089ed8f4245c11d062": 3,
    "THEME-32e82969583da77cd04d": 5,
}
MASTER_SOURCE_FILES = {
    "paper": "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/paper_records.jsonl",
    "theme": "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/theme_big_question_records.jsonl",
    "printed": "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/printed_question_records.jsonl",
    "atomic": "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/atomic_part_records.jsonl",
}
CONTROLLED_VOCABULARY_PATH = (
    "kb/question_classification_v1/controlled_vocabulary.json"
)
EXPECTED_CONTROLLED_VOCABULARY_SHA256 = (
    "fb362f745dc3c390c025fe9135f4ddd56fd78cbae9875e86c5665659d0df1c99"
)
EXPECTED_MASTER_IDS = frozenset(
    {
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
        "Q7",
        "Q8",
        "Q9",
        "Q10",
        "Q11",
        "Q12",
        "Q13-1",
        "Q13-2",
        "Q14",
        "Q15-1",
        "Q15-2",
        "Q15-3",
        "Q15-4",
        "LE2026-S3-Q16-P1",
        "LE2026-S3-Q16-P2",
        "LE2026-S3-Q17-P1",
        "LE2026-S3-Q18-P1",
        "LE2026-S3-Q19-P1",
        "LE2026-S3-Q19-P2",
        "LE2026-S3-Q20-P1",
        "LE2026-S3-Q21-P1",
        "LE2026-S3-Q22-P1",
        "LE2026-S3-Q23-P1",
        "LE2026-S5-Q25-P1",
        "LE2026-S5-Q26-P1",
        "LE2026-S5-Q27-P1",
        "LE2026-S5-Q28-P1",
        "LE2026-S5-Q29-P1",
        "LE2026-S5-Q30-P1",
    }
)
KNOWN_QUALITY_NOTES = {
    "Q11": "Q11_SOURCE_ANSWER_NINHYDRIN_NOTE",
    "LE2026-S3-Q17-P1": "Q17_RECALL_CONSTANT_TEMPERATURE_NOTE",
    "LE2026-S3-Q23-P1": "Q23_SOURCE_INFORMATION_GAP_NOTE",
    "LE2026-S5-Q26-P1": "Q26_SOURCE_EQUATION_CONDITION_NOTE",
    "LE2026-S5-Q30-P1": "Q30_SOURCE_ROUNDING_NOTE",
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
DIFFICULTY_CATEGORICAL_VALUES = {
    "calculation_load": ("none", "single_step", "multi_step", "unknown"),
    "experiment_load": ("none", "interpret", "design", "evaluate", "unknown"),
    "openness": ("closed", "semi_open", "open", "unknown"),
    "unfamiliarity": (
        "familiar",
        "partly_unfamiliar",
        "unfamiliar",
        "unknown",
    ),
    "language_load": ("low", "medium", "high", "unknown"),
    "dependency_on_prior_parts": (
        "independent",
        "one_prior_part",
        "multiple_prior_parts",
        "unknown",
    ),
}
DIFFICULTY_NUMERIC_DIMENSIONS = FACTOR_IDS[:4]
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_KEYS = frozenset(
    {
        "bytes",
        "crop_id",
        "evidence_role",
        "height",
        "sha256",
        "source_page",
        "visual_inspection_status",
        "width",
    }
)
_ANSWER_EVIDENCE_KEYS = _EVIDENCE_KEYS - {"evidence_role"}
_CROP_KEYS = frozenset(
    {
        "crop_id",
        "evidence_role",
        "category",
        "output_path",
        "source_path",
        "source_sha256",
        "crop_box_xywh_pixels",
        "width",
        "height",
        "bytes",
        "sha256",
        "http_exposable",
        "pixel_authority",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "product_id",
        "paper_id",
        "status",
        "created_date",
        "hierarchy_counts",
        "coverage_scope",
        "source_file_count",
        "crop_count",
        "crop_category_counts",
        "answer_coverage",
        "difficulty_factor_contract",
        "output_file_count",
        "outputs",
        "key_output_hashes",
        "claims",
        "self_hash_algorithm",
        "self_sha256",
    }
)
_IDENTITY_KEYS = frozenset(
    {
        "paper_face_title_literal",
        "paper_face_verified_claims",
        "coverage_scope",
        "covered_theme_orders",
        "excluded_theme_orders",
        "complete_five_theme_claim_allowed",
        "official_status",
        "source_accounts",
        "source_a_exact_pages_local",
        "source_b_exact_pages_local",
        "source_b_boundary_zh",
    }
)


class ShanghaiExam2026RecallDirectVisualScanError(MasterDirectVisualScanError):
    pass


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_by_id: dict[str, dict[str, Any]]
    output_bindings: dict[str, dict[str, Any]]
    output_bytes: dict[str, bytes]
    answer_crop_ids: frozenset[str]
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
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} must be an object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    if not text or not text.endswith("\n") or any(
        not line.strip() for line in text.splitlines()
    ):
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} JSONL shape drifted"
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is invalid",
            ) from exc
        if not isinstance(row, dict):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is not an object",
            )
        rows.append(row)
    return rows


def _safe_relative(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
    ):
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    return value


def _binding_index(
    value: Any, label: str, expected_count: int
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise ShanghaiExam2026RecallDirectVisualScanError(
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
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} value drifted"
            )
        result[path] = dict(item)
    return result


def _source_binding_index(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != EXPECTED_SOURCE_BINDING_COUNT:
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_binding_invalid", "source count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", "source shape drifted"
            )
        path = _safe_relative(item.get("path"))
        media_type = item.get("media_type")
        expected_keys = {"path", "role", "bytes", "sha256", "media_type"}
        if media_type in {"image/jpeg", "image/png"}:
            expected_keys |= {"width", "height"}
        if (
            set(item) != expected_keys
            or path in result
            or item.get("role") not in {"source_page", "candidate_or_rule_binding"}
            or type(item.get("bytes")) is not int
            or item["bytes"] < 1
            or not isinstance(item.get("sha256"), str)
            or _HEX64.fullmatch(item["sha256"]) is None
            or media_type
            not in {"image/jpeg", "image/png", "application/json", "text/plain"}
            or (
                media_type in {"image/jpeg", "image/png"}
                and (
                    type(item.get("width")) is not int
                    or item["width"] < 1
                    or type(item.get("height")) is not int
                    or item["height"] < 1
                )
            )
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", "source value drifted"
            )
        result[path] = deepcopy(item)
    return result


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_gate_elevated", f"{label} gates drifted"
        )


def _validate_difficulty_factor_values(factors: Any) -> None:
    if (
        not isinstance(factors, list)
        or tuple(
            item.get("dimension_id") if isinstance(item, dict) else None
            for item in factors
        )
        != FACTOR_IDS
    ):
        raise ShanghaiExam2026RecallDirectVisualScanError(
            "master_direct_scan_difficulty_invalid", "difficulty factors drifted"
        )
    for factor in factors:
        dimension_id = factor["dimension_id"]
        value = factor.get("value")
        if dimension_id in DIFFICULTY_NUMERIC_DIMENSIONS:
            valid_value = type(value) is int and value >= 0
        else:
            valid_value = value in DIFFICULTY_CATEGORICAL_VALUES[dimension_id]
        if not valid_value:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_difficulty_vocabulary_invalid",
                "difficulty factor violates the bound controlled vocabulary",
            )


class ShanghaiExam2026RecallDirectVisualScanReader:
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        declared_root = self.shchem_root / PRODUCT_RELATIVE
        if declared_root.is_symlink():
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_symlink_denied", "product root may not be a symlink"
            )
        self.product_root = declared_root.resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_path_invalid", "product root leaves sh-chem-db"
            )
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _verified_file(self, root: Path, relative: str) -> tuple[Path, bytes]:
        relative = _safe_relative(relative)
        cursor = root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "bound paths may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_path_invalid", "a bound file leaves its root"
            )
        return resolved, resolved.read_bytes()

    def _verify_bindings(
        self, root: Path, bindings: dict[str, dict[str, Any]], label: str
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            _, raw = self._verified_file(root, relative)
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_binding_mismatch",
                    f"{label} hash or byte binding drifted",
                )
            result[relative] = raw
        return result

    @staticmethod
    def _png_dimensions(data: bytes) -> tuple[int, int] | None:
        if not data.startswith(PNG_SIGNATURE):
            return None
        offset = len(PNG_SIGNATURE)
        dimensions: tuple[int, int] | None = None
        saw_idat = False
        while offset < len(data):
            if offset + 12 > len(data):
                return None
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            end = offset + 12 + length
            if end > len(data):
                return None
            chunk = data[offset + 8 : offset + 8 + length]
            crc = int.from_bytes(data[offset + 8 + length : end], "big")
            if zlib.crc32(chunk_type + chunk) & 0xFFFFFFFF != crc:
                return None
            if dimensions is None:
                if chunk_type != b"IHDR" or length != 13:
                    return None
                width = int.from_bytes(chunk[:4], "big")
                height = int.from_bytes(chunk[4:8], "big")
                if width < 1 or height < 1:
                    return None
                dimensions = (width, height)
            elif chunk_type == b"IHDR":
                return None
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                if length or not saw_idat or end != len(data):
                    return None
                return dimensions
            offset = end
        return None

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        value = deepcopy(manifest)
        value["self_sha256"] = None
        return _sha256(_canonical_bytes(value))

    @staticmethod
    def _evidence_binding(record: dict[str, Any]) -> str:
        answer = record["answer"]
        payload = {
            "atomic_part_id": record["hierarchy"]["atomic_part_id"],
            "evidence": [
                {"crop_id": item["crop_id"], "sha256": item["sha256"]}
                for item in record["viewed_evidence"]
            ],
            "answer_evidence": [
                {"crop_id": item["crop_id"], "sha256": item["sha256"]}
                for item in answer["visual_alignment_evidence"]
            ],
            "classification": record["classification"],
            "difficulty": record["difficulty"]["cognitive_prelabel"],
            "reference_answer_sha256": _sha256(
                answer["reference_summary_zh"].encode("utf-8")
            ),
        }
        return _sha256(_canonical_bytes(payload))

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        if (
            set(manifest) != _MANIFEST_KEYS
            or manifest.get("schema_version")
            != "1.0.0-question-visual-scan-product"
            or manifest.get("product_id") != PRODUCT_ID
            or manifest.get("paper_id") != PAPER_ID
            or manifest.get("status") != "candidate_model_observed_pending_human"
            or manifest.get("created_date") != "2026-08-25"
            or manifest.get("hierarchy_counts") != EXPECTED_HIERARCHY_COUNTS
            or manifest.get("coverage_scope")
            != "themes_1_2_3_5_only_not_complete_five_theme_claim"
            or manifest.get("source_file_count") != EXPECTED_SOURCE_BINDING_COUNT
            or manifest.get("crop_count") != EXPECTED_CROP_COUNT
            or manifest.get("crop_category_counts")
            != EXPECTED_CROP_CATEGORY_COUNTS
            or manifest.get("answer_coverage")
            != {
                "present_part_aligned": 35,
                "source_authority": "nonofficial_recall_institution_analysis",
                "independently_verified": False,
                "quality_note_count": len(KNOWN_QUALITY_NOTES),
            }
            or manifest.get("difficulty_factor_contract")
            != {
                "path": CONTROLLED_VOCABULARY_PATH,
                "sha256": EXPECTED_CONTROLLED_VOCABULARY_SHA256,
                "factor_value_count": 350,
                "invalid_value_count": 0,
                "repaired_illegal_value_count": 54,
            }
            or manifest.get("output_file_count") != EXPECTED_OUTPUT_BINDING_COUNT
            or manifest.get("self_hash_algorithm")
            != "canonical-json-sha256-with-self_sha256-null-v1"
            or manifest.get("self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
            or manifest.get("claims")
            != {
                "candidate_only": True,
                "human_reviewed": False,
                "official": False,
                "retrieval_ready": False,
                "teaching_use_allowed": False,
                "generation_allowed": False,
                "publication_allowed": False,
                "pixel_reuse_allowed": False,
            }
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest identity drifted"
            )
        output_bindings = _binding_index(
            manifest.get("outputs"), "output", EXPECTED_OUTPUT_BINDING_COUNT
        )
        required = {
            "crop_manifest.json",
            "coverage_report.json",
            "disagreement_report.json",
            "scan_record_schema.json",
            "scan_records.jsonl",
            "source_manifest.json",
        }
        if not required <= set(output_bindings):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", "required output is unbound"
            )
        entries = list(self.product_root.rglob("*"))
        if any(entry.is_symlink() for entry in entries):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_symlink_denied", "product contains a symlink"
            )
        product_files = {
            path.relative_to(self.product_root).as_posix()
            for path in entries
            if path.is_file()
            and "__pycache__" not in path.relative_to(self.product_root).parts
            and ".pytest_cache" not in path.relative_to(self.product_root).parts
        }
        if product_files != set(output_bindings) | {"manifest.json"}:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "recursive output closure contains missing or unbound files",
            )
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        key_hashes = manifest.get("key_output_hashes")
        if (
            not isinstance(key_hashes, dict)
            or set(key_hashes) != required
            or any(
                key_hashes[name] != output_bindings[name]["sha256"]
                for name in required
            )
            or key_hashes["scan_records.jsonl"] != EXPECTED_RECORDS_SHA256
            or key_hashes["crop_manifest.json"]
            != EXPECTED_CROP_MANIFEST_SHA256
            or key_hashes["source_manifest.json"]
            != EXPECTED_SOURCE_MANIFEST_SHA256
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", "key output binding drifted"
            )
        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        if (
            set(source_manifest)
            != {
                "schema_version",
                "product_id",
                "source_file_count",
                "sources",
                "dual_source_boundary",
                "coverage_boundary",
            }
            or source_manifest.get("schema_version") != "1.0.0-source-closure"
            or source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("source_file_count")
            != EXPECTED_SOURCE_BINDING_COUNT
            or source_manifest.get("dual_source_boundary")
            != {
                "source_accounts": ["申教在线", "靠谱提分"],
                "source_a_exact_pages_local": True,
                "source_b_exact_pages_local": False,
                "statement": (
                    "本地逐页题卷/解析像素仅能绑定申教在线流通页；靠谱提分"
                    "只在既有README中作为交叉转载与核心一致性边界，不能冒充"
                    "第二套逐页像素。"
                ),
            }
            or source_manifest.get("coverage_boundary")
            != "仅覆盖Master已结构化的主题一、二、三、五及35个atomic；主题四不在本产品记录中。"
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_source_manifest_invalid",
                "source boundary drifted",
            )
        source_bindings = _source_binding_index(source_manifest.get("sources"))
        if any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_binding_invalid", "source closure widened"
            )
        source_bytes = self._verify_bindings(
            self.shchem_root, source_bindings, "source"
        )
        if not set(MASTER_SOURCE_FILES.values()) <= set(source_bytes):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_parent_chain_invalid",
                "the frozen Master470 parent-chain sources are not closed",
            )
        if CONTROLLED_VOCABULARY_PATH not in source_bytes:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_vocabulary_invalid",
                "the frozen controlled vocabulary is not bound",
            )
        if (
            _sha256(source_bytes[CONTROLLED_VOCABULARY_PATH])
            != EXPECTED_CONTROLLED_VOCABULARY_SHA256
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_vocabulary_invalid",
                "controlled vocabulary bytes drifted",
            )
        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "master470/exact169 identity is unavailable",
            ) from exc
        return self._validate(
            manifest,
            output_bindings,
            output_bytes,
            source_manifest,
            source_bindings,
            source_bytes,
            master_identity,
        )

    def _validate(
        self,
        manifest: dict[str, Any],
        output_bindings: dict[str, dict[str, Any]],
        output_bytes: dict[str, bytes],
        source_manifest: dict[str, Any],
        source_bindings: dict[str, dict[str, Any]],
        source_bytes: dict[str, bytes],
        master_identity: dict[str, Any],
    ) -> _Snapshot:
        controlled_vocabulary = _json_object(
            source_bytes[CONTROLLED_VOCABULARY_PATH], "controlled vocabulary"
        )
        difficulty_contract = controlled_vocabulary.get(
            "difficulty_factor_contract"
        )
        expected_categorical_values = {
            key: list(values)
            for key, values in DIFFICULTY_CATEGORICAL_VALUES.items()
        }
        expected_numeric_dimensions = list(DIFFICULTY_NUMERIC_DIMENSIONS)
        if (
            not isinstance(difficulty_contract, dict)
            or difficulty_contract.get("required_dimension_ids")
            != list(FACTOR_IDS)
            or difficulty_contract.get("categorical_values")
            != expected_categorical_values
            or difficulty_contract.get("numeric_nonnegative_dimensions")
            != expected_numeric_dimensions
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_vocabulary_invalid",
                "the difficulty-factor controlled vocabulary drifted",
            )
        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop manifest"
        )
        if (
            set(crop_manifest)
            != {
                "schema_version",
                "product_id",
                "crop_count",
                "category_counts",
                "crops",
                "http_policy",
            }
            or crop_manifest.get("schema_version") != "1.0.0-crop-closure"
            or crop_manifest.get("product_id") != PRODUCT_ID
            or crop_manifest.get("crop_count") != EXPECTED_CROP_COUNT
            or crop_manifest.get("category_counts")
            != EXPECTED_CROP_CATEGORY_COUNTS
            or crop_manifest.get("http_policy")
            != {
                "question_and_shared_may_be_exposed": True,
                "answer_must_remain_internal": True,
            }
            or not isinstance(crop_manifest.get("crops"), list)
            or len(crop_manifest["crops"]) != EXPECTED_CROP_COUNT
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop manifest drifted"
            )
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_relative_paths: set[str] = set()
        crop_categories: Counter[str] = Counter()
        product_prefix = PRODUCT_RELATIVE.as_posix() + "/"
        for crop in crop_manifest["crops"]:
            if not isinstance(crop, dict) or set(crop) != _CROP_KEYS:
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop row shape drifted",
                )
            crop_id = crop.get("crop_id")
            output_path = _safe_relative(crop.get("output_path"))
            source_path = _safe_relative(crop.get("source_path"))
            if not output_path.startswith(product_prefix):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_path_invalid", "crop output leaves product root"
                )
            relative_output = _safe_relative(output_path[len(product_prefix) :])
            output = output_bindings.get(relative_output)
            raw = output_bytes.get(relative_output)
            source = source_bindings.get(source_path)
            box = crop.get("crop_box_xywh_pixels")
            category = crop.get("category")
            role = crop.get("evidence_role")
            role_policy = {
                "paper_identity": ("shared_material", True),
                "theme_stimulus": ("shared_material", True),
                "question": ("question", True),
                "nonofficial_reference_answer": ("answer", False),
            }
            dimensions = self._png_dimensions(raw or b"")
            if (
                not isinstance(crop_id, str)
                or crop_id in crop_by_id
                or relative_output in crop_relative_paths
                or category not in role_policy
                or (role, crop.get("http_exposable")) != role_policy[category]
                or crop.get("pixel_authority") != "source_page_pixels"
                or output is None
                or raw is None
                or source is None
                or output.get("bytes") != crop.get("bytes")
                or output.get("sha256") != crop.get("sha256")
                or source.get("sha256") != crop.get("source_sha256")
                or not isinstance(box, list)
                or len(box) != 4
                or any(type(value) is not int for value in box)
                or box[0] < 0
                or box[1] < 0
                or box[2] < 1
                or box[3] < 1
                or source.get("media_type") not in {"image/jpeg", "image/png"}
                or box[0] + box[2] > source.get("width", 0)
                or box[1] + box[3] > source.get("height", 0)
                or (crop.get("width"), crop.get("height"))
                != (box[2], box[3])
                or dimensions != (crop.get("width"), crop.get("height"))
                or len(raw) > MAX_CROP_BYTES
                or _sha256(raw) != crop.get("sha256")
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_crop_binding_invalid",
                    "crop PNG/source/output binding drifted",
                )
            crop_by_id[crop_id] = deepcopy(crop)
            crop_by_id[crop_id]["relative_output"] = relative_output
            crop_relative_paths.add(relative_output)
            crop_categories[str(category)] += 1
        if (
            crop_categories != Counter(EXPECTED_CROP_CATEGORY_COUNTS)
            or crop_relative_paths
            != {path for path in output_bindings if path.startswith("crops/")}
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop closure drifted"
            )

        schema = _json_object(output_bytes["scan_record_schema.json"], "scan schema")
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "scan records")
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_schema_invalid", "record schema is invalid"
            ) from exc
        if len(records) != 35:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count drifted"
            )
        for index, record in enumerate(records, 1):
            if next(validator.iter_errors(record), None) is not None:
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} fails its frozen schema",
                )

        master_ids = set(master_identity["master_atomic_ids"])
        exact_ids = set(master_identity["exact_master_ids"])
        by_master_id: dict[str, dict[str, Any]] = {}
        answer_crop_ids: set[str] = set()
        corrected_fields: dict[str, tuple[str, ...]] = {}
        seen_ids: set[str] = set()
        theme_counts: Counter[int] = Counter()
        printed_ids: set[str] = set()
        question_crop_ids: set[str] = set()
        shared_crop_ids: set[str] = set()
        answer_sha256s: set[str] = set()
        compare_counts: Counter[str] = Counter(
            {"agree": 0, "corrected": 0, "blocked": 0}
        )
        corrected_counts: Counter[str] = Counter()
        corrected_nodes: list[dict[str, Any]] = []
        identity_boundary: dict[str, Any] | None = None
        master_atoms = {
            row["atomic_part_id"]: row
            for row in _jsonl_objects(
                source_bytes[MASTER_SOURCE_FILES["atomic"]], "master atomic"
            )
            if row.get("parent_paper_id") == PAPER_ID
        }
        master_printed = {
            row["printed_question_id"]: row
            for row in _jsonl_objects(
                source_bytes[MASTER_SOURCE_FILES["printed"]], "master printed"
            )
            if row.get("parent_paper_id") == PAPER_ID
        }
        master_themes = {
            row["theme_big_question_id"]: row
            for row in _jsonl_objects(
                source_bytes[MASTER_SOURCE_FILES["theme"]], "master theme"
            )
            if row.get("parent_paper_id") == PAPER_ID
        }
        master_papers = {
            row["paper_id"]: row
            for row in _jsonl_objects(
                source_bytes[MASTER_SOURCE_FILES["paper"]], "master paper"
            )
        }

        for index, record in enumerate(records, 1):
            _false_gates(record.get("authority_gates"), "record")
            hierarchy = record.get("hierarchy")
            if not isinstance(hierarchy, dict):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_identity_conflict", "hierarchy is invalid"
                )
            master_id = hierarchy.get("atomic_part_id")
            theme_id = hierarchy.get("theme_id")
            printed_id = hierarchy.get("printed_question_id")
            if (
                record.get("scan_id") != f"VS-LE2026-RECALL-{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
                or not isinstance(master_id, str)
                or master_id not in EXPECTED_MASTER_IDS
                or master_id in by_master_id
                or master_id not in master_ids
                or master_id in exact_ids
                or theme_id not in EXPECTED_THEME_IDS
                or hierarchy.get("theme_sequence") != EXPECTED_THEME_IDS[theme_id]
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("paper_sequence") != 1
                or hierarchy.get("source_atomic_part_id") != master_id
                or not isinstance(printed_id, str)
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record identity drifted"
                )
            master_atom = master_atoms.get(master_id)
            master_printed_row = master_printed.get(str(printed_id))
            master_theme = master_themes.get(str(theme_id))
            if (
                PAPER_ID not in master_papers
                or master_atom is None
                or master_atom.get("parent_paper_id") != PAPER_ID
                or master_atom.get("parent_theme_big_question_id") != theme_id
                or master_atom.get("parent_printed_question_id") != printed_id
                or master_printed_row is None
                or master_printed_row.get("parent_paper_id") != PAPER_ID
                or master_printed_row.get("parent_theme_big_question_id") != theme_id
                or master_theme is None
                or master_theme.get("parent_paper_id") != PAPER_ID
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_parent_chain_invalid",
                    "record no longer matches the frozen Master470 parent chain",
                )
            source = record.get("source")
            if source != {
                "source_id": SOURCE_ID,
                "paper_id": PAPER_ID,
                "source_namespace": "master470_direct_visual_scan_candidate",
                "official_status": "nonofficial",
                "evidence_level": "L2_RECALLED_LEVEL_EXAM_NONOFFICIAL",
            }:
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_source_manifest_invalid",
                    "record source authority drifted",
                )
            identity = record.get("identity_boundary")
            if (
                not isinstance(identity, dict)
                or set(identity) != _IDENTITY_KEYS
                or identity.get("coverage_scope")
                != "master_structured_themes_1_2_3_5_only_35_atomic"
                or identity.get("covered_theme_orders") != [1, 2, 3, 5]
                or identity.get("excluded_theme_orders") != [4]
                or identity.get("complete_five_theme_claim_allowed") is not False
                or identity.get("official_status") != "nonofficial"
                or identity.get("source_accounts") != ["申教在线", "靠谱提分"]
                or identity.get("source_a_exact_pages_local") is not True
                or identity.get("source_b_exact_pages_local") is not False
                or not isinstance(identity.get("paper_face_verified_claims"), list)
                or not identity["paper_face_verified_claims"]
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_source_manifest_invalid",
                    "record source identity boundary drifted",
                )
            if identity_boundary is None:
                identity_boundary = deepcopy(identity)
            elif identity != identity_boundary:
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_source_manifest_invalid",
                    "record identity boundaries disagree",
                )

            viewed = record.get("viewed_evidence")
            dependency = record.get("dependency")
            prior = (
                dependency.get("prior_atomic_part_ids")
                if isinstance(dependency, dict)
                else None
            )
            shared_dependencies = (
                dependency.get("shared_material_crop_ids")
                if isinstance(dependency, dict)
                else None
            )
            if (
                not isinstance(viewed, list)
                or not isinstance(prior, list)
                or not set(prior) <= seen_ids
                or not isinstance(shared_dependencies, list)
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency drifted"
                )
            question_items = [
                item for item in viewed if item.get("evidence_role") == "question"
            ]
            shared_items = [
                item
                for item in viewed
                if item.get("evidence_role") == "shared_material"
            ]
            if (
                not question_items
                or len(shared_items) not in {1, 2}
                or len(viewed) != len(question_items) + len(shared_items)
                or shared_dependencies
                != [item.get("crop_id") for item in shared_items]
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "evidence roles drifted"
                )
            for descriptor in viewed:
                crop = crop_by_id.get(descriptor.get("crop_id"))
                role = descriptor.get("evidence_role")
                expected_category = (
                    {"paper_identity", "theme_stimulus"}
                    if role == "shared_material"
                    else {"question"}
                )
                if (
                    not isinstance(descriptor, dict)
                    or set(descriptor) != _EVIDENCE_KEYS
                    or descriptor.get("visual_inspection_status")
                    != "source_page_actually_viewed_and_crop_bounds_checked_by_primary_model"
                    or crop is None
                    or crop.get("category") not in expected_category
                    or crop.get("evidence_role") != role
                    or crop.get("sha256") != descriptor.get("sha256")
                    or crop.get("bytes") != descriptor.get("bytes")
                    or crop.get("width") != descriptor.get("width")
                    or crop.get("height") != descriptor.get("height")
                    or crop.get("http_exposable") is not True
                ):
                    raise ShanghaiExam2026RecallDirectVisualScanError(
                        "master_direct_scan_crop_binding_invalid",
                        "record evidence crop binding drifted",
                    )
                (question_crop_ids if role == "question" else shared_crop_ids).add(
                    str(descriptor["crop_id"])
                )

            difficulty = record.get("difficulty")
            factors = (
                difficulty.get("factors") if isinstance(difficulty, dict) else None
            )
            if (
                not isinstance(factors, list)
                or tuple(item.get("dimension_id") for item in factors) != FACTOR_IDS
                or difficulty.get("is_measured") is not False
                or difficulty.get("measured_difficulty") is not None
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "difficulty drifted"
                )
            _validate_difficulty_factor_values(factors)
            candidate = record.get("candidate_analysis")
            expected_quality_code = KNOWN_QUALITY_NOTES.get(master_id)
            if (
                not isinstance(candidate, dict)
                or candidate.get("candidate_only") is not True
                or candidate.get("correctness_verified") is not False
                or not isinstance(candidate.get("solution_path_zh"), list)
                or not candidate["solution_path_zh"]
                or candidate.get("known_quality_note_code")
                != expected_quality_code
                or (
                    expected_quality_code is None
                    and candidate.get("known_quality_note_zh") is not None
                )
                or (
                    expected_quality_code is not None
                    and (
                        not isinstance(candidate.get("known_quality_note_zh"), str)
                        or not candidate["known_quality_note_zh"]
                    )
                )
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_quality_note_invalid",
                    "candidate quality note drifted",
                )
            answer = record.get("answer")
            if (
                not isinstance(answer, dict)
                or set(answer)
                != {
                    "availability",
                    "authority",
                    "source_authority",
                    "source_authority_zh",
                    "independently_verified",
                    "alignment_status",
                    "answer_verified",
                    "official_answer_claim_allowed",
                    "reference_summary_zh",
                    "quality_note",
                    "visual_alignment_evidence",
                }
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("source_authority")
                != "nonofficial_recall_institution_analysis"
                or answer.get("source_authority_zh")
                != "非官方回忆/重排版机构参考解析"
                or answer.get("independently_verified") is not False
                or answer.get("alignment_status")
                != "present_part_aligned_to_nonofficial_institution_analysis_pixels"
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not answer["reference_summary_zh"]
                or answer.get("quality_note")
                != candidate.get("known_quality_note_zh")
                or not isinstance(answer.get("visual_alignment_evidence"), list)
                or not answer["visual_alignment_evidence"]
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer boundary drifted"
                )
            for descriptor in answer["visual_alignment_evidence"]:
                crop = crop_by_id.get(descriptor.get("crop_id"))
                if (
                    not isinstance(descriptor, dict)
                    or set(descriptor) != _ANSWER_EVIDENCE_KEYS
                    or descriptor.get("visual_inspection_status")
                    != "aligned_via_actually_viewed_nonofficial_answer_page_and_crop_bounds"
                    or crop is None
                    or crop.get("category") != "nonofficial_reference_answer"
                    or crop.get("evidence_role") != "answer"
                    or crop.get("sha256") != descriptor.get("sha256")
                    or crop.get("bytes") != descriptor.get("bytes")
                    or crop.get("width") != descriptor.get("width")
                    or crop.get("height") != descriptor.get("height")
                    or crop.get("http_exposable") is not False
                ):
                    raise ShanghaiExam2026RecallDirectVisualScanError(
                        "master_direct_scan_answer_invalid",
                        "answer crop binding drifted",
                    )
                answer_crop_ids.add(str(descriptor["crop_id"]))
                answer_sha256s.add(str(descriptor["sha256"]))

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(
                COMPARISON_FIELDS
            ):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_comparison_invalid",
                    "comparison fields drifted",
                )
            corrected: list[str] = []
            for field in COMPARISON_FIELDS:
                item = comparison[field]
                state = item.get("compare") if isinstance(item, dict) else None
                if state not in {"agree", "corrected"}:
                    raise ShanghaiExam2026RecallDirectVisualScanError(
                        "master_direct_scan_comparison_invalid",
                        "comparison state drifted",
                    )
                compare_counts[state] += 1
                if state == "corrected":
                    corrected.append(field)
                    corrected_counts[field] += 1
            corrected_fields[master_id] = tuple(corrected)
            if corrected:
                corrected_nodes.append(
                    {"atomic_part_id": master_id, "fields": corrected}
                )
            if record.get("evidence_binding_sha256") != self._evidence_binding(record):
                raise ShanghaiExam2026RecallDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "evidence hash drifted"
                )
            by_master_id[master_id] = record
            seen_ids.add(master_id)
            theme_counts[int(hierarchy["theme_sequence"])] += 1
            printed_ids.add(str(printed_id))

        direct_ids = set(by_master_id)
        if (
            direct_ids != EXPECTED_MASTER_IDS
            or direct_ids & exact_ids
            or len(direct_ids | exact_ids) != 204
            or len(master_ids - direct_ids - exact_ids) != 266
            or dict(theme_counts) != EXPECTED_THEME_ATOMIC_COUNTS
            or len(printed_ids) != 29
            or len(question_crop_ids)
            != EXPECTED_CROP_CATEGORY_COUNTS.get("question")
            or len(shared_crop_ids) != 5
            or len(answer_crop_ids)
            != EXPECTED_CROP_CATEGORY_COUNTS.get("nonofficial_reference_answer")
            or identity_boundary is None
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_count_mismatch", "derived counts drifted"
            )

        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        expected_quality_counts = {
            "with_quality_note": len(KNOWN_QUALITY_NOTES),
            "without_quality_note": 35 - len(KNOWN_QUALITY_NOTES),
        }
        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("paper_count") != 1
            or coverage.get("theme_count") != 4
            or coverage.get("printed_question_count") != 29
            or coverage.get("atomic_part_count") != 35
            or coverage.get("theme_atomic_counts")
            != {str(key): value for key, value in EXPECTED_THEME_ATOMIC_COUNTS.items()}
            or coverage.get("visual_scan_completed_count") != 35
            or coverage.get("answer_alignment")
            != {
                "present_part_aligned": 35,
                "independently_verified": 0,
                "source_authority": "nonofficial_recall_institution_analysis",
            }
            or coverage.get("quality_notes") != expected_quality_counts
            or coverage.get("comparison_totals") != dict(compare_counts)
            or coverage.get("blocked_count") != 0
            or coverage.get("coverage_scope")
            != "themes_1_2_3_5_only_not_complete_five_theme_claim"
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_coverage_invalid", "coverage drifted"
            )
        disagreement = _json_object(
            output_bytes["disagreement_report.json"], "disagreement"
        )
        if (
            disagreement.get("product_id") != PRODUCT_ID
            or disagreement.get("field_corrected_counts")
            != dict(sorted(corrected_counts.items()))
            or disagreement.get("corrected_node_count") != len(corrected_nodes)
            or disagreement.get("corrected_nodes") != corrected_nodes
            or disagreement.get("blocked_node_count") != 0
            or disagreement.get("quality_note_atomic_part_ids")
            != list(KNOWN_QUALITY_NOTES)
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_disagreement_invalid", "disagreement drifted"
            )
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids=frozenset(answer_crop_ids),
            corrected_fields_by_master=corrected_fields,
            manifest_self_sha256=str(manifest["self_sha256"]),
            master_crosswalk_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
            paper_identity_boundary=identity_boundary,
        )

    @staticmethod
    def _coverage() -> dict[str, int]:
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 35,
            "visual_scanned_master_atomic": 204,
            "remaining_unscanned": 266,
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
            "record_count": 35,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
        }

    @staticmethod
    def _reference_answer(record: dict[str, Any]) -> dict[str, Any]:
        answer = record["answer"]
        projected = project_reference_answer(answer)
        projected["quality_note"] = answer["quality_note"]
        return projected

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": {
                "expected_themes": 4,
                "expected_printed_questions": 29,
                "expected_atomic_parts": 35,
                "scan_records": 35,
                "visual_scan_completed": 35,
                "blocked_pending_broader_crop": 0,
                "question_crop_bindings": EXPECTED_CROP_CATEGORY_COUNTS.get(
                    "question", 0
                ),
                "shared_crop_bindings": EXPECTED_CROP_CATEGORY_COUNTS.get(
                    "paper_identity", 0
                )
                + EXPECTED_CROP_CATEGORY_COUNTS.get("theme_stimulus", 0),
                "nonofficial_answer_crop_bindings": (
                    EXPECTED_CROP_CATEGORY_COUNTS.get(
                        "nonofficial_reference_answer", 0
                    )
                ),
                "total_exact_crop_bindings": EXPECTED_CROP_COUNT,
            },
            "coverage": self._coverage(),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def catalog(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        items: list[dict[str, Any]] = []
        for record in snapshot.records:
            master_id = record["hierarchy"]["atomic_part_id"]
            evidence = record["viewed_evidence"]
            answer = record["answer"]
            items.append(
                {
                    "master_node_id": master_id,
                    "scan_status": "visual_scan_completed",
                    "detail_available": True,
                    "question_evidence_count": sum(
                        item["evidence_role"] == "question" for item in evidence
                    ),
                    "shared_evidence_count": sum(
                        item["evidence_role"] == "shared_material"
                        for item in evidence
                    ),
                    "question_pixels_available": True,
                    "corrected_fields": list(
                        snapshot.corrected_fields_by_master[master_id]
                    ),
                    "availability": answer["availability"],
                    "source_authority": answer["authority"],
                    "has_quality_note": answer["quality_note"] is not None,
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
            "paper_identity_boundary": deepcopy(
                snapshot.paper_identity_boundary
            ),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def detail(self, master_node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except SecurityError as exc:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_invalid_node_id",
                "master node ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Shanghai 2026 recall direct scan",
                404,
            )
        answer = record["answer"]
        candidate = record["candidate_analysis"]
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
            "dependency": deepcopy(record["dependency"]),
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
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Shanghai 2026 recall direct scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        if crop_id in snapshot.answer_crop_ids or (
            crop is not None
            and crop.get("category") == "nonofficial_reference_answer"
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_crop_role_denied", "answer crop is forbidden", 403
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
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        relative_output = crop.get("relative_output") if crop else None
        binding = snapshot.output_bindings.get(relative_output)
        raw = snapshot.output_bytes.get(relative_output)
        expected_categories = (
            {"question"}
            if descriptor["evidence_role"] == "question"
            else {"paper_identity", "theme_stimulus"}
        )
        if (
            crop is None
            or binding is None
            or raw is None
            or crop.get("category") not in expected_categories
            or crop.get("http_exposable") is not True
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or len(raw) > MAX_CROP_BYTES
            or self._png_dimensions(raw)
            != (descriptor["width"], descriptor["height"])
            or _sha256(raw) != descriptor["sha256"]
        ):
            raise ShanghaiExam2026RecallDirectVisualScanError(
                "master_direct_scan_crop_binding_invalid", "served crop binding drifted"
            )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


__all__ = [
    "AUTHORITY",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "EXPECTED_OUTPUT_BINDING_COUNT",
    "EXPECTED_SOURCE_BINDING_COUNT",
    "KNOWN_QUALITY_NOTES",
    "MAX_CROP_BYTES",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "ShanghaiExam2026RecallDirectVisualScanError",
    "ShanghaiExam2026RecallDirectVisualScanReader",
]
