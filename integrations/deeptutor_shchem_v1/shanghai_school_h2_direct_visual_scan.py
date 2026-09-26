from __future__ import annotations

import hashlib
import json
import re
import zlib
from collections import Counter, defaultdict
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
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier


PRODUCT_ID = "SHANGHAISCHOOL-H2-SPRING-MID-QUESTION-VISUAL-SCAN-V1-2026-08-25"
PAPER_ID = "MASTER-PAPER-df932ee3e19c621342a9"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_shanghaischool_h2_spring_mid_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_SELF_SHA256 = (
    "7261d068cf78424525e634d73185aacbddd60e7c1fadc65a734b4696bd440c33"
)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "c8f0681126ceb58d312d0b5db0046b1d8598da9392d1bf134d812738fc022467"
)
EXPECTED_OUTPUT_BINDING_COUNT = 112
EXPECTED_SOURCE_BINDING_COUNT = 26
EXPECTED_COUNTS = {
    "expected_themes": 5,
    "expected_printed_questions": 25,
    "expected_atomic_parts": 43,
    "scan_records": 43,
    "visual_scan_completed": 43,
    "blocked_pending_broader_crop": 0,
    "question_whole_pages_actually_viewed": 8,
    "answer_whole_pages_actually_viewed": 2,
    "exact_question_crop_bindings": 43,
    "unique_question_crops_actually_viewed": 43,
    "exact_shared_crop_bindings": 11,
    "unique_shared_crops_actually_viewed": 11,
    "nonofficial_answer_crop_bindings": 43,
    "unique_nonofficial_answer_evidence_sha256": 40,
    "total_exact_crop_bindings": 97,
}
EXPECTED_GATE_KEYS = frozenset(
    {
        "human_reviewed",
        "human_chemistry_reviewed",
        "verified",
        "official",
        "retrieval_ready",
        "retrieval_allowed",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        "answer_verified",
        "rubric_verified",
        "measured_difficulty_verified",
        "pixel_reuse_allowed",
    }
)
EXPECTED_GATES = {key: False for key in EXPECTED_GATE_KEYS}
AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    **EXPECTED_GATES,
}
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
FIELD_COVERAGE = (
    "visible_summary_zh",
    "response_requirement_zh",
    "theme_chain_role",
    "classification",
    "difficulty",
    "chemistry_observations",
    "candidate_analysis",
    "answer",
    "risks_and_limits",
    "comparison_with_prior_candidate",
)
EXPECTED_SOURCE_ID = "WX-SHANGHAI-2025-H2-SECOND-MIDTERM"
Q24_MASTER_NODE_ID = "MASTER-PART-21cf713883c8f26af281"
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MASTER_PART_ID = re.compile(r"MASTER-PART-[0-9a-f]{20}\Z")
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
_CROP_ROW_KEYS = frozenset(
    {
        "atomic_part_id",
        "box_normalized",
        "box_xyxy",
        "crop_bytes",
        "crop_dimensions",
        "crop_generation",
        "crop_id",
        "crop_path",
        "crop_sha256",
        "pixel_reuse_allowed",
        "role",
        "source_bytes",
        "source_dimensions",
        "source_page",
        "source_path",
        "source_sha256",
        "visual_inspection_status",
    }
)
_SHARED_CROP_ROLES = frozenset(
    {"paper_identity", "theme_stimulus", "printed_question_shared_stem"}
)


class ShanghaiSchoolH2DirectVisualScanError(MasterDirectVisualScanError):
    pass


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_manifest_by_id: dict[str, dict[str, Any]]
    output_bindings: dict[str, dict[str, Any]]
    output_bytes: dict[str, bytes]
    answer_crop_ids_by_master: dict[str, frozenset[str]]
    corrected_fields_by_master: dict[str, tuple[str, ...]]
    manifest_self_sha256: str
    manifest_file_sha256: str
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
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    if not value or not value.endswith("\n") or any(
        not line.strip() for line in value.splitlines()
    ):
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_json_invalid",
            f"{label} must be non-empty JSONL without blank records",
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(value.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is invalid JSON",
            ) from exc
        if not isinstance(row, dict):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} must be an object",
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
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
    ):
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    return value


def _binding_index(
    value: Any, label: str, *, expected_count: int
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} binding count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} binding shape drifted"
            )
        path = _safe_relative(item.get("path"))
        if (
            path in result
            or type(item.get("bytes")) is not int
            or item["bytes"] < 1
            or not isinstance(item.get("sha256"), str)
            or _HEX64.fullmatch(item["sha256"]) is None
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} binding value drifted"
            )
        result[path] = dict(item)
    return result


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
        raise ShanghaiSchoolH2DirectVisualScanError(
            "master_direct_scan_gate_elevated", f"{label} authority gates drifted"
        )


class ShanghaiSchoolH2DirectVisualScanReader:
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise ShanghaiSchoolH2DirectVisualScanError(
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
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "bound files may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_path_invalid", "a bound file leaves its fixed root"
            )
        try:
            return resolved, resolved.read_bytes()
        except OSError as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_file_read_failed", "a bound file could not be read"
            ) from exc

    def _verify_bindings(
        self, root: Path, bindings: dict[str, dict[str, Any]], label: str
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            _, raw = self._verified_file(root, relative)
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_binding_mismatch",
                    f"{label} hash or byte binding drifted",
                )
            result[relative] = raw
        return result

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        value = deepcopy(manifest)
        value["manifest_self_sha256"] = None
        return _sha256(_canonical_bytes(value))

    @staticmethod
    def _record_evidence_binding(record: dict[str, Any]) -> str:
        answer = record["answer"]
        payload = {
            "atomic_part_id": record["hierarchy"]["atomic_part_id"],
            "source_atomic_part_id": record["hierarchy"]["source_atomic_part_id"],
            "viewed_evidence": record["viewed_evidence"],
            "answer_evidence": answer["visual_alignment_evidence"],
            "identity_boundary": record["identity_boundary"],
            "visible_summary_zh": record["visible_summary_zh"],
        }
        return _sha256(_canonical_bytes(payload))

    @staticmethod
    def _valid_png(data: bytes) -> bool:
        if not data.startswith(PNG_SIGNATURE):
            return False
        offset = len(PNG_SIGNATURE)
        saw_ihdr = False
        saw_idat = False
        while offset < len(data):
            if offset + 12 > len(data):
                return False
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(data):
                return False
            chunk_data = data[offset + 8 : offset + 8 + length]
            expected_crc = int.from_bytes(
                data[offset + 8 + length : chunk_end], "big"
            )
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                return False
            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    return False
                width = int.from_bytes(chunk_data[0:4], "big")
                height = int.from_bytes(chunk_data[4:8], "big")
                if width < 1 or height < 1:
                    return False
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                return False
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                return length == 0 and saw_ihdr and saw_idat and chunk_end == len(data)
            offset = chunk_end
        return False

    def _validate_crop_manifest(
        self,
        crop_manifest: dict[str, Any],
        output_bindings: dict[str, dict[str, Any]],
        output_bytes: dict[str, bytes],
        source_bindings: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if (
            set(crop_manifest)
            != {"schema_version", "paper_id", "source_id", "counts", "records", "contracts"}
            or crop_manifest.get("paper_id") != PAPER_ID
            or crop_manifest.get("source_id") != EXPECTED_SOURCE_ID
            or crop_manifest.get("counts")
            != {
                "question_crop_bindings": 43,
                "shared_crop_bindings": 11,
                "answer_crop_bindings": 43,
                "total_crop_bindings": 97,
            }
            or not isinstance(crop_manifest.get("records"), list)
            or len(crop_manifest["records"]) != 97
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop manifest drifted"
            )
        result: dict[str, dict[str, Any]] = {}
        role_counts: Counter[str] = Counter()
        crop_paths: set[str] = set()
        for row in crop_manifest["records"]:
            if not isinstance(row, dict) or set(row) != _CROP_ROW_KEYS:
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop row shape drifted"
                )
            crop_id = row.get("crop_id")
            crop_path = _safe_relative(row.get("crop_path"))
            source_path = _safe_relative(row.get("source_path"))
            role = row.get("role")
            if (
                not isinstance(crop_id, str)
                or crop_id in result
                or crop_path in crop_paths
                or role
                not in {
                    "question",
                    "paper_identity",
                    "theme_stimulus",
                    "printed_question_shared_stem",
                    "nonofficial_reference_answer",
                }
                or row.get("visual_inspection_status")
                != "actually_viewed_by_primary_model"
                or row.get("pixel_reuse_allowed") is not False
                or row.get("crop_generation")
                != "deterministic_pillow_xyxy_from_bound_source_no_source_overwrite"
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop row value drifted"
                )
            output_binding = output_bindings.get(crop_path)
            source_binding = source_bindings.get(source_path)
            data = output_bytes.get(crop_path)
            if (
                output_binding is None
                or source_binding is None
                or data is None
                or output_binding.get("bytes") != row.get("crop_bytes")
                or output_binding.get("sha256") != row.get("crop_sha256")
                or source_binding.get("bytes") != row.get("source_bytes")
                or source_binding.get("sha256") != row.get("source_sha256")
                or not isinstance(row.get("crop_dimensions"), list)
                or len(row["crop_dimensions"]) != 2
                or not all(type(value) is int and value > 0 for value in row["crop_dimensions"])
                or len(data) > MAX_CROP_BYTES
                or not self._valid_png(data)
                or _sha256(data) != row.get("crop_sha256")
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_crop_binding_invalid",
                    "crop does not match output, source, PNG, and size bindings",
                )
            result[crop_id] = row
            crop_paths.add(crop_path)
            role_counts[str(role)] += 1
        expected_crop_paths = {
            path for path in output_bindings if path.startswith("crops/")
        }
        if (
            crop_paths != expected_crop_paths
            or role_counts
            != Counter(
                {
                    "question": 43,
                    "paper_identity": 1,
                    "theme_stimulus": 5,
                    "printed_question_shared_stem": 5,
                    "nonofficial_reference_answer": 43,
                }
            )
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid",
                "recursive crop output closure or role counts drifted",
            )
        return result

    def _validate_snapshot(
        self,
        *,
        manifest: dict[str, Any],
        output_bindings: dict[str, dict[str, Any]],
        output_bytes: dict[str, bytes],
        source_manifest: dict[str, Any],
        source_bindings: dict[str, dict[str, Any]],
        master_identity: dict[str, Any],
    ) -> _Snapshot:
        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        disagreement = _json_object(
            output_bytes["disagreement_report.json"], "disagreement"
        )
        schema = _json_object(output_bytes["scan_record_schema.json"], "scan schema")
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "scan records")
        crop_manifest = _json_object(output_bytes["crop_manifest.json"], "crop manifest")
        crop_manifest_by_id = self._validate_crop_manifest(
            crop_manifest, output_bindings, output_bytes, source_bindings
        )
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_schema_invalid", "scan record schema is invalid"
            ) from exc
        for index, record in enumerate(records, 1):
            issue = next(validator.iter_errors(record), None)
            if issue is not None:
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"scan record {index} failed its bound Draft 2020-12 schema",
                )
        if len(records) != 43:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_count_mismatch", "scan record count is not 43"
            )

        master_ids = set(master_identity["master_atomic_ids"])
        exact_master_ids = set(master_identity["exact_master_ids"])
        direct_ids: list[str] = []
        by_master_id: dict[str, dict[str, Any]] = {}
        answer_crop_ids_by_master: dict[str, frozenset[str]] = {}
        corrected_fields_by_master: dict[str, tuple[str, ...]] = {}
        comparison_counts: Counter[str] = Counter()
        comparison_by_field: dict[str, Counter[str]] = defaultdict(Counter)
        corrected_records: list[dict[str, Any]] = []
        blocked_records: list[dict[str, Any]] = []
        question_hashes: set[str] = set()
        identity_crop = crop_manifest_by_id.get("SHS2025-H2-SHARED-IDENTITY")
        if identity_crop is None:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid",
                "paper-identity crop is missing",
            )
        shared_hashes: set[str] = {str(identity_crop["crop_sha256"])}
        answer_hashes: set[str] = set()
        prior_ids: set[str] = set()
        printed_by_theme: dict[int, set[str]] = defaultdict(set)

        for index, record in enumerate(records, 1):
            if record.get("authority_gates") != EXPECTED_GATES:
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_gate_elevated",
                    "a scan record authority gate drifted",
                )
            if (
                record.get("scan_id") != f"VS-SHS-H2-SPRING-MID-{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_record_invalid", "scan order or completion drifted"
                )
            hierarchy = record.get("hierarchy")
            master_id = hierarchy.get("atomic_part_id") if isinstance(hierarchy, dict) else None
            if (
                not isinstance(master_id, str)
                or _MASTER_PART_ID.fullmatch(master_id) is None
                or master_id in by_master_id
                or master_id not in master_ids
                or master_id in exact_master_ids
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("paper_sequence") != index
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_identity_conflict",
                    "SHS direct identity is absent from master470, duplicated, or overlaps exact169",
                )
            direct_ids.append(master_id)
            by_master_id[master_id] = record
            printed_by_theme[int(hierarchy["theme_sequence"])].add(
                str(hierarchy["printed_question_id"])
            )

            dependency = record.get("dependency")
            prior = dependency.get("prior_atomic_part_ids") if isinstance(dependency, dict) else None
            if (
                not isinstance(prior, list)
                or not set(prior).issubset(prior_ids)
                or prior
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_dependency_invalid",
                    "SHS prior dependency is not the frozen independent contract",
                )
            viewed = record.get("viewed_evidence")
            if not isinstance(viewed, list):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "viewed evidence is not a list"
                )
            questions = [item for item in viewed if item.get("evidence_role") == "question"]
            shared = [
                item for item in viewed if item.get("evidence_role") == "shared_material"
            ]
            if (
                len(questions) != 1
                or len(shared) not in {1, 2}
                or len(questions) + len(shared) != len(viewed)
                or dependency.get("shared_material_crop_ids")
                != [item.get("crop_id") for item in shared]
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_evidence_invalid",
                    "question/shared evidence roles or dependency binding drifted",
                )
            source_atomic_part_id = hierarchy.get("source_atomic_part_id")
            for descriptor in viewed:
                if (
                    not isinstance(descriptor, dict)
                    or set(descriptor) != _EVIDENCE_KEYS
                    or descriptor.get("visual_inspection_status")
                    != "actually_viewed_by_primary_model"
                    or not all(
                        type(descriptor.get(key)) is int and descriptor[key] > 0
                        for key in ("bytes", "height", "source_page", "width")
                    )
                    or not isinstance(descriptor.get("sha256"), str)
                    or _HEX64.fullmatch(descriptor["sha256"]) is None
                ):
                    raise ShanghaiSchoolH2DirectVisualScanError(
                        "master_direct_scan_evidence_invalid",
                        "viewed evidence descriptor shape drifted",
                    )
                crop = crop_manifest_by_id.get(descriptor["crop_id"])
                expected_role = descriptor["evidence_role"]
                crop_role = crop.get("role") if crop else None
                output_binding = output_bindings.get(crop.get("crop_path")) if crop else None
                if (
                    crop is None
                    or crop.get("crop_sha256") != descriptor["sha256"]
                    or crop.get("crop_bytes") != descriptor["bytes"]
                    or crop.get("source_page") != descriptor["source_page"]
                    or crop.get("crop_dimensions")
                    != [descriptor["width"], descriptor["height"]]
                    or output_binding is None
                    or output_binding.get("sha256") != descriptor["sha256"]
                    or output_binding.get("bytes") != descriptor["bytes"]
                    or (expected_role == "question" and crop_role != "question")
                    or (expected_role == "shared_material" and crop_role not in _SHARED_CROP_ROLES)
                    or (
                        expected_role == "question"
                        and crop.get("atomic_part_id") != source_atomic_part_id
                    )
                ):
                    raise ShanghaiSchoolH2DirectVisualScanError(
                        "master_direct_scan_crop_binding_invalid",
                        "record evidence and exact crop closure drifted",
                    )
                if expected_role == "question":
                    question_hashes.add(descriptor["sha256"])
                else:
                    shared_hashes.add(descriptor["sha256"])

            difficulty = record.get("difficulty")
            factors = difficulty.get("factors") if isinstance(difficulty, dict) else None
            if (
                not isinstance(factors, list)
                or tuple(item.get("dimension_id") for item in factors) != FACTOR_IDS
                or difficulty.get("is_measured") is not False
                or difficulty.get("measured_difficulty") is not None
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_difficulty_invalid",
                    "ten-factor cognitive difficulty contract drifted",
                )
            viewed_crop_ids = {item["crop_id"] for item in viewed}
            for factor in factors:
                if (
                    factor.get("evidence_status")
                    != "actual_visual_evidence_model_inference_not_measured"
                    or not set(factor.get("evidence_crop_ids", [])).issubset(viewed_crop_ids)
                    or factor.get("source_atomic_part_ids") != []
                ):
                    raise ShanghaiSchoolH2DirectVisualScanError(
                        "master_direct_scan_difficulty_invalid",
                        "difficulty evidence leaves the record evidence boundary",
                    )

            candidate_analysis = record.get("candidate_analysis")
            if (
                not isinstance(candidate_analysis, dict)
                or set(candidate_analysis)
                != {"candidate_only", "correctness_verified", "solution_path_zh"}
                or candidate_analysis.get("candidate_only") is not True
                or candidate_analysis.get("correctness_verified") is not False
                or not isinstance(candidate_analysis.get("solution_path_zh"), list)
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_analysis_invalid",
                    "candidate solution analysis boundary drifted",
                )

            answer = record.get("answer")
            if (
                not isinstance(answer, dict)
                or set(answer)
                != {
                    "alignment_status",
                    "answer_verified",
                    "authority",
                    "availability",
                    "official_answer_claim_allowed",
                    "reference_summary_zh",
                    "visual_alignment_evidence",
                }
                or answer.get("alignment_status")
                != "part_aligned_to_nonofficial_appendix_pages_09_10_pagination_anomaly_preserved"
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("visual_alignment_evidence"), list)
                or len(answer["visual_alignment_evidence"]) != 1
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "nonofficial answer boundary drifted",
                )
            answer_descriptor = answer["visual_alignment_evidence"][0]
            if (
                not isinstance(answer_descriptor, dict)
                or set(answer_descriptor) != _EVIDENCE_KEYS - {"evidence_role"}
                or answer_descriptor.get("source_page") not in {9, 10}
                or answer_descriptor.get("visual_inspection_status")
                != "aligned_via_actually_viewed_nonofficial_answer_crop_and_whole_page"
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "answer alignment evidence drifted",
                )
            answer_crop = crop_manifest_by_id.get(answer_descriptor.get("crop_id"))
            answer_binding = (
                output_bindings.get(answer_crop.get("crop_path")) if answer_crop else None
            )
            if (
                answer_crop is None
                or answer_crop.get("role") != "nonofficial_reference_answer"
                or answer_crop.get("atomic_part_id") != source_atomic_part_id
                or answer_crop.get("source_page") != answer_descriptor.get("source_page")
                or answer_crop.get("crop_sha256") != answer_descriptor.get("sha256")
                or answer_crop.get("crop_bytes") != answer_descriptor.get("bytes")
                or answer_crop.get("crop_dimensions")
                != [answer_descriptor.get("width"), answer_descriptor.get("height")]
                or answer_binding is None
                or answer_binding.get("sha256") != answer_descriptor.get("sha256")
                or answer_binding.get("bytes") != answer_descriptor.get("bytes")
            ):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "answer evidence is outside the exact output crop closure",
                )
            answer_crop_ids_by_master[master_id] = frozenset(
                {str(answer_descriptor["crop_id"])}
            )
            answer_hashes.add(str(answer_descriptor["sha256"]))

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(COMPARISON_FIELDS):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_comparison_invalid",
                    "prior candidate comparison field set drifted",
                )
            corrected: list[str] = []
            blocked: list[str] = []
            for field in COMPARISON_FIELDS:
                item = comparison[field]
                state = item.get("compare") if isinstance(item, dict) else None
                if (
                    state not in {"agree", "corrected", "blocked"}
                    or not isinstance(item.get("old"), list)
                    or not isinstance(item.get("scan"), list)
                    or not isinstance(item.get("reason_zh"), str)
                    or not item["reason_zh"]
                ):
                    raise ShanghaiSchoolH2DirectVisualScanError(
                        "master_direct_scan_comparison_invalid",
                        "prior candidate comparison payload drifted",
                    )
                comparison_counts[state] += 1
                comparison_by_field[field][state] += 1
                if state == "corrected":
                    corrected.append(field)
                elif state == "blocked":
                    blocked.append(field)
            corrected_fields_by_master[master_id] = tuple(corrected)
            if corrected:
                corrected_records.append(
                    {
                        "atomic_part_id": master_id,
                        "source_atomic_part_id": hierarchy["source_atomic_part_id"],
                        "fields": corrected,
                    }
                )
            if blocked:
                blocked_records.append({"atomic_part_id": master_id, "fields": blocked})
            if record.get("evidence_binding_sha256") != self._record_evidence_binding(record):
                raise ShanghaiSchoolH2DirectVisualScanError(
                    "master_direct_scan_evidence_invalid",
                    "record evidence binding hash drifted",
                )
            prior_ids.add(master_id)

        direct_set = set(direct_ids)
        if (
            len(direct_ids) != 43
            or len(direct_set) != 43
            or direct_set & exact_master_ids
            or len(direct_set | exact_master_ids) != 212
            or len(master_ids - direct_set - exact_master_ids) != 258
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "43 SHS direct and 169 exact master identities do not form 212 unique rows",
            )
        if (
            [
                sum(record["hierarchy"]["theme_sequence"] == theme for record in records)
                for theme in range(1, 6)
            ]
            != [7, 7, 9, 10, 10]
            or [len(printed_by_theme[theme]) for theme in range(1, 6)]
            != [5, 5, 5, 5, 5]
            or len(question_hashes) != 43
            or len(shared_hashes) != 11
            or len(answer_hashes) != 40
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_count_mismatch",
                "derived hierarchy or evidence counts drifted",
            )

        q24 = by_master_id.get(Q24_MASTER_NODE_ID)
        if (
            q24 is None
            or q24.get("classification", {}).get("item_type")
            != "embedded_indeterminate_choice"
            or q24.get("classification", {}).get("selection_rule") != "indeterminate"
            or q24.get("candidate_analysis", {}).get("correctness_verified") is not False
            or "BD" not in q24.get("answer", {}).get("reference_summary_zh", "")
            or "AD" not in q24.get("answer", {}).get("reference_summary_zh", "")
            or not any(
                "答案BD" in item and "计算AD" in item
                for item in q24.get("risks_and_limits", {}).get(
                    "ambiguity_or_multiple_solutions_zh", []
                )
            )
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_q24_conflict_invalid",
                "Q24 AD-versus-nonofficial-BD risk boundary drifted",
            )

        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("paper_id") != PAPER_ID
            or coverage.get("counts") != EXPECTED_COUNTS
            or coverage.get("field_coverage") != {field: 43 for field in FIELD_COVERAGE}
            or coverage.get("unscanned_atomic_part_ids") != []
            or coverage.get("all_authority_gates_false") is not True
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_coverage_invalid", "coverage report drifted"
            )
        expected_by_field = {
            field: dict(sorted(counts.items()))
            for field, counts in sorted(comparison_by_field.items())
        }
        if (
            disagreement.get("product_id") != PRODUCT_ID
            or disagreement.get("paper_id") != PAPER_ID
            or disagreement.get("overall_compare_counts")
            != dict(sorted(comparison_counts.items()))
            or disagreement.get("by_field") != expected_by_field
            or disagreement.get("corrected_records") != corrected_records
            or disagreement.get("blocked_records") != blocked_records
            or disagreement.get("answer_conflicts")
            != [
                {
                    "source_atomic_part_id": "WX-SHANGHAI-2025-H2-SECOND-MIDTERM-Q24-P01",
                    "issue": "nonofficial_answer_BD_conflicts_with_independent_hydroxyl_equivalent_calculation_AD",
                    "answer_verified": False,
                }
            ]
            or disagreement.get("authority_gates") != EXPECTED_GATES
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_disagreement_invalid", "disagreement report drifted"
            )

        expected_source_fields = {
            "product_id",
            "paper_id",
            "source_identity",
            "source_bindings",
            "rule_contract_bindings",
            "crop_manifest_binding",
            "paper_identity_visual_evidence",
            "question_whole_pages",
            "answer_whole_pages",
            "paper_identity_boundary",
            "pagination_anomaly",
            "master470_boundary",
            "wechat_search_boundary",
            "visual_inspection_declaration",
            "answer_boundary",
            "authority_gates",
        }
        identity = source_manifest.get("paper_identity_boundary")
        if (
            set(source_manifest) != expected_source_fields
            or source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("paper_id") != PAPER_ID
            or source_manifest.get("source_identity", {}).get("source_id")
            != EXPECTED_SOURCE_ID
            or source_manifest.get("source_identity", {}).get("official_status")
            != "nonofficial"
            or not isinstance(identity, dict)
            or set(identity)
            != {
                "paper_face_verified_claims",
                "school",
                "academic_year",
                "semester",
                "grade",
                "subject",
                "paper_family",
                "district_mock_claim_allowed",
                "level_exam_original_claim_allowed",
                "official_status",
            }
            or identity.get("school") != "上海中学"
            or identity.get("official_status") != "nonofficial"
            or identity.get("district_mock_claim_allowed") is not False
            or identity.get("level_exam_original_claim_allowed") is not False
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_source_manifest_invalid",
                "SHS source identity or paper-face boundary drifted",
            )
        _false_gates(source_manifest.get("authority_gates"), "source manifest")
        if (
            [item.get("page_number") for item in source_manifest.get("question_whole_pages", [])]
            != list(range(1, 9))
            or [item.get("page_number") for item in source_manifest.get("answer_whole_pages", [])]
            != [9, 10]
            or source_manifest.get("pagination_anomaly")
            != {
                "student_question_pages": 8,
                "answer_appendix_pages": 2,
                "answer_printed_page_numbers": [9, 10],
                "answer_footer_total_literal": 8,
                "answer_pages_are_part_of_student_paper": False,
                "quality_flag": "ANSWER_PAGES_APPEND_AFTER_DECLARED_PAPER_TOTAL",
            }
            or source_manifest.get("answer_boundary")
            != {
                "authority": "nonofficial_reference",
                "verified": False,
                "q24_conflict": "page_BD_vs_independent_AD",
                "official_rubric_present": False,
            }
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_answer_invalid",
                "answer appendix or Q24 conflict boundary drifted",
            )

        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_manifest_by_id=crop_manifest_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids_by_master=answer_crop_ids_by_master,
            corrected_fields_by_master=corrected_fields_by_master,
            manifest_self_sha256=str(manifest["manifest_self_sha256"]),
            manifest_file_sha256=EXPECTED_MANIFEST_FILE_SHA256,
            master_crosswalk_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
            paper_identity_boundary=deepcopy(identity),
        )

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "SHS manifest file bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        if (
            set(manifest)
            != {
                "product_id",
                "schema_version",
                "status",
                "paper_id",
                "fixed_counts",
                "source_manifest_binding",
                "output_bindings",
                "authority_gates",
                "manifest_self_hash_contract",
                "manifest_self_sha256",
            }
            or manifest.get("product_id") != PRODUCT_ID
            or manifest.get("paper_id") != PAPER_ID
            or manifest.get("status")
            != "PASS_MODEL_VISUAL_SCAN_CANDIDATE_GATES_CLOSED"
            or manifest.get("fixed_counts") != EXPECTED_COUNTS
            or manifest.get("manifest_self_hash_contract")
            != "sha256(canonical UTF-8 JSON with manifest_self_sha256 set to null)"
            or manifest.get("manifest_self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "SHS manifest identity drifted"
            )
        _false_gates(manifest.get("authority_gates"), "manifest")
        output_bindings = _binding_index(
            manifest.get("output_bindings"),
            "output",
            expected_count=EXPECTED_OUTPUT_BINDING_COUNT,
        )
        required_outputs = {
            "crop_manifest.json",
            "coverage_report.json",
            "disagreement_report.json",
            "scan_record_schema.json",
            "scan_records.jsonl",
            "source_manifest.json",
        }
        if not required_outputs <= set(output_bindings):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "required outputs are unbound"
            )
        try:
            entries = list(self.product_root.rglob("*"))
        except OSError as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_file_read_failed", "product directory is unavailable"
            ) from exc
        if any(entry.is_symlink() for entry in entries):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_symlink_denied",
                "product recursive closure contains a symbolic link",
            )
        product_files = {
            path.relative_to(self.product_root).as_posix()
            for path in entries
            if path.is_file()
        }
        if product_files != set(output_bindings) | {"manifest.json"}:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "product recursive output closure contains missing or unbound files",
            )
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        if manifest.get("source_manifest_binding") != output_bindings["source_manifest.json"]:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "source manifest is not exactly bound by the product manifest",
            )
        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        if source_manifest.get("crop_manifest_binding") != output_bindings["crop_manifest.json"]:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "crop manifest is not exactly bound by the source manifest",
            )
        source_bindings = _binding_index(
            source_manifest.get("source_bindings"),
            "source",
            expected_count=EXPECTED_SOURCE_BINDING_COUNT,
        )
        if list(source_bindings) != sorted(source_bindings):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "source bindings are unsorted"
            )
        if any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "dynamic central snapshots are forbidden from the source closure",
            )
        self._verify_bindings(self.shchem_root, source_bindings, "source")
        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity index is unavailable",
            ) from exc
        return self._validate_snapshot(
            manifest=manifest,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            source_manifest=source_manifest,
            source_bindings=source_bindings,
            master_identity=master_identity,
        )

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "manifest_file_sha256": snapshot.manifest_file_sha256,
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "output_binding_count": EXPECTED_OUTPUT_BINDING_COUNT,
            "source_binding_count": EXPECTED_SOURCE_BINDING_COUNT,
            "record_count": 43,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
        }

    @staticmethod
    def _coverage() -> dict[str, int]:
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 43,
            "visual_scanned_master_atomic": 212,
            "remaining_unscanned": 258,
            "direct_exact_overlap": 0,
        }

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": dict(EXPECTED_COUNTS),
            "coverage": self._coverage(),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def catalog(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        items = []
        for record in snapshot.records:
            master_id = record["hierarchy"]["atomic_part_id"]
            evidence = record["viewed_evidence"]
            items.append(
                {
                    "master_node_id": master_id,
                    "scan_status": "visual_scan_completed",
                    "detail_available": True,
                    "question_evidence_count": sum(
                        item["evidence_role"] == "question" for item in evidence
                    ),
                    "shared_evidence_count": sum(
                        item["evidence_role"] == "shared_material" for item in evidence
                    ),
                    "question_pixels_available": True,
                    "corrected_fields": list(snapshot.corrected_fields_by_master[master_id]),
                    **reference_answer_catalog_metadata(
                        record["answer"], record["risks_and_limits"]
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

    @staticmethod
    def _evidence_projection(record: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "crop_id": item["crop_id"],
                "evidence_role": item["evidence_role"],
                "source_page": item["source_page"],
                "width": item["width"],
                "height": item["height"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "visual_inspection_status": item["visual_inspection_status"],
                "content_type": "image/png",
                "access": "teacher_loopback_read_only",
            }
            for item in record["viewed_evidence"]
        ]

    def detail(self, master_node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except SecurityError as exc:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "master node has no SHS direct visual scan",
                404,
            )
        answer = record["answer"]
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
            "candidate_analysis": deepcopy(record["candidate_analysis"]),
            "risks_and_limits": deepcopy(record["risks_and_limits"]),
            "comparison_with_prior_candidate": deepcopy(
                record["comparison_with_prior_candidate"]
            ),
            "evidence_descriptors": self._evidence_projection(record),
            "answer_boundary": {
                "availability": answer["availability"],
                "authority": answer["authority"],
                "verified": False,
            },
            "reference_answer": project_reference_answer(
                answer, record["risks_and_limits"]
            ),
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
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "master node has no SHS direct visual scan",
                404,
            )
        if crop_id in snapshot.answer_crop_ids_by_master[master_node_id]:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_role_denied",
                "answer evidence cannot be served by the question-crop endpoint",
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
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_not_found",
                "crop is not question/shared evidence for this master node",
                404,
            )
        crop = snapshot.crop_manifest_by_id.get(crop_id)
        crop_path = crop.get("crop_path") if crop else None
        binding = snapshot.output_bindings.get(crop_path)
        data = snapshot.output_bytes.get(crop_path)
        expected_role = descriptor["evidence_role"]
        crop_role = crop.get("role") if crop else None
        if (
            crop is None
            or binding is None
            or data is None
            or crop.get("crop_sha256") != descriptor["sha256"]
            or binding["sha256"] != descriptor["sha256"]
            or binding["bytes"] != descriptor["bytes"]
            or len(data) != descriptor["bytes"]
            or (expected_role == "question" and crop_role != "question")
            or (expected_role == "shared_material" and crop_role not in _SHARED_CROP_ROLES)
        ):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_binding_invalid",
                "crop no longer matches record, crop manifest, and output binding",
            )
        if len(data) > MAX_CROP_BYTES:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_too_large",
                "crop exceeds the one MiB response limit",
                413,
            )
        if not self._valid_png(data):
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_not_png",
                "crop is not a structurally valid PNG",
            )
        if _sha256(data) != descriptor["sha256"]:
            raise ShanghaiSchoolH2DirectVisualScanError(
                "master_direct_scan_crop_hash_mismatch", "crop bytes drifted"
            )
        return CandidateCropPayload(data=data, sha256=descriptor["sha256"])


__all__ = [
    "AUTHORITY",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "EXPECTED_OUTPUT_BINDING_COUNT",
    "EXPECTED_SOURCE_BINDING_COUNT",
    "MAX_CROP_BYTES",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "Q24_MASTER_NODE_ID",
    "SCOPE",
    "ShanghaiSchoolH2DirectVisualScanError",
    "ShanghaiSchoolH2DirectVisualScanReader",
]
