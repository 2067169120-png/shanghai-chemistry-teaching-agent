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


PRODUCT_ID = "CAOYANG2-H2-SPRING-FINAL-QUESTION-VISUAL-SCAN-V1-2026-08-25"
PAPER_ID = "MASTER-PAPER-49323b87c2ba13c22b52"
PRODUCT_RELATIVE = Path(
    "kb/classification/question_visual_scan_caoyang2_h2_spring_final_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_SELF_SHA256 = (
    "449a585ea528310daec23a97607d08a66d3a2ead2b255ebc8314f54225603caf"
)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "fc7d0ea0b978707495a7eedec8234db432da33a119ef5f256de237c43f45c4fa"
)
EXPECTED_RECORDS_SHA256 = (
    "72417121f7d0d20815c66154852d5a4fac366f834138dd6c404ce1f34fcaad47"
)
EXPECTED_CROP_MANIFEST_SHA256 = (
    "b3c36b9845b023bf5abb3daa427fb9fe1344a7fd853a7c10425ac614588e35d0"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "511f0dff677c0379f3619e017d40648a45241ba71f26cc6c8279a2be1a587fdb"
)
EXPECTED_OUTPUT_BINDING_COUNT = 100
EXPECTED_SOURCE_BINDING_COUNT = 26
EXPECTED_SOURCE_ID = "WX-CAOYANG2-2025-H2-SECOND-FINAL"
EXPECTED_COUNTS = {
    "expected_themes": 5,
    "expected_printed_questions": 38,
    "expected_atomic_parts": 38,
    "scan_records": 38,
    "visual_scan_completed": 38,
    "blocked_pending_broader_crop": 0,
    "question_whole_pages_actually_viewed": 7,
    "answer_whole_pages_actually_viewed": 3,
    "exact_question_crop_bindings": 38,
    "unique_question_crops_actually_viewed": 38,
    "exact_shared_crop_bindings": 9,
    "unique_shared_crops_actually_viewed": 9,
    "nonofficial_answer_crop_bindings": 38,
    "unique_nonofficial_answer_evidence_sha256": 38,
    "total_exact_crop_bindings": 85,
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
KNOWN_QUALITY_NOTES = {
    "WX-CAOYANG2-2025-H2-SECOND-FINAL-Q07-P01": (
        "Q07_REFERENCE_ANSWER_ARITHMETIC_NOTE"
    ),
    "WX-CAOYANG2-2025-H2-SECOND-FINAL-Q32-P01": (
        "Q32_REFERENCE_ANSWER_ASYMMETRIC_CARBON_NOTE"
    ),
    "WX-CAOYANG2-2025-H2-SECOND-FINAL-Q33-P01": (
        "Q33_REFERENCE_ANSWER_SINGLE_CHOICE_NOTE"
    ),
}
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
_SHARED_ROLES = frozenset(
    {"paper_identity", "theme_stimulus", "printed_question_shared_stem"}
)


class Caoyang2H2DirectVisualScanError(MasterDirectVisualScanError):
    pass


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_by_id: dict[str, dict[str, Any]]
    output_bindings: dict[str, dict[str, Any]]
    output_bytes: dict[str, bytes]
    answer_crop_ids_by_master: dict[str, frozenset[str]]
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
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} must be an object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    if not text or not text.endswith("\n") or any(
        not line.strip() for line in text.splitlines()
    ):
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} JSONL shape drifted"
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is invalid",
            ) from exc
        if not isinstance(row, dict):
            raise Caoyang2H2DirectVisualScanError(
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
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
    ):
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    return value


def _binding_index(
    value: Any, label: str, expected_count: int
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise Caoyang2H2DirectVisualScanError(
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
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} value drifted"
            )
        result[path] = dict(item)
    return result


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
        raise Caoyang2H2DirectVisualScanError(
            "master_direct_scan_gate_elevated", f"{label} gates drifted"
        )


class Caoyang2H2DirectVisualScanReader:
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise Caoyang2H2DirectVisualScanError(
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
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "bound paths may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise Caoyang2H2DirectVisualScanError(
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
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_binding_mismatch",
                    f"{label} hash or byte binding drifted",
                )
            result[relative] = raw
        return result

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
            end = offset + 12 + length
            if end > len(data):
                return False
            chunk = data[offset + 8 : offset + 8 + length]
            crc = int.from_bytes(data[offset + 8 + length : end], "big")
            if zlib.crc32(chunk_type + chunk) & 0xFFFFFFFF != crc:
                return False
            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    return False
                if int.from_bytes(chunk[:4], "big") < 1 or int.from_bytes(
                    chunk[4:8], "big"
                ) < 1:
                    return False
                saw_ihdr = True
            elif chunk_type == b"IHDR":
                return False
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                return length == 0 and saw_ihdr and saw_idat and end == len(data)
            offset = end
        return False

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        value = deepcopy(manifest)
        value["manifest_self_sha256"] = None
        return _sha256(_canonical_bytes(value))

    @staticmethod
    def _evidence_binding(record: dict[str, Any]) -> str:
        return _sha256(
            _canonical_bytes(
                {
                    "atomic_part_id": record["hierarchy"]["atomic_part_id"],
                    "source_atomic_part_id": record["hierarchy"][
                        "source_atomic_part_id"
                    ],
                    "viewed_evidence": record["viewed_evidence"],
                    "answer_evidence": record["answer"]["visual_alignment_evidence"],
                    "identity_boundary": record["identity_boundary"],
                    "visible_summary_zh": record["visible_summary_zh"],
                }
            )
        )

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        if (
            manifest.get("product_id") != PRODUCT_ID
            or manifest.get("paper_id") != PAPER_ID
            or manifest.get("status")
            != "PASS_MODEL_VISUAL_SCAN_CANDIDATE_GATES_CLOSED"
            or manifest.get("fixed_counts") != EXPECTED_COUNTS
            or manifest.get("manifest_self_hash_contract")
            != "sha256(canonical UTF-8 JSON with manifest_self_sha256 set to null)"
            or manifest.get("manifest_self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest identity drifted"
            )
        _false_gates(manifest.get("authority_gates"), "manifest")
        output_bindings = _binding_index(
            manifest.get("output_bindings"), "output", EXPECTED_OUTPUT_BINDING_COUNT
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
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "required output is unbound"
            )
        entries = list(self.product_root.rglob("*"))
        if any(entry.is_symlink() for entry in entries):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_symlink_denied", "product contains a symlink"
            )
        product_files = {
            path.relative_to(self.product_root).as_posix()
            for path in entries
            if path.is_file()
        }
        if product_files != set(output_bindings) | {"manifest.json"}:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "recursive output closure contains missing or unbound files",
            )
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        if (
            output_bindings["scan_records.jsonl"]["sha256"]
            != EXPECTED_RECORDS_SHA256
            or output_bindings["crop_manifest.json"]["sha256"]
            != EXPECTED_CROP_MANIFEST_SHA256
            or output_bindings["source_manifest.json"]["sha256"]
            != EXPECTED_SOURCE_MANIFEST_SHA256
            or manifest.get("source_manifest_binding")
            != output_bindings["source_manifest.json"]
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "key output binding drifted"
            )
        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        if source_manifest.get("crop_manifest_binding") != output_bindings[
            "crop_manifest.json"
        ]:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "crop manifest binding drifted"
            )
        source_bindings = _binding_index(
            source_manifest.get("source_bindings"),
            "source",
            EXPECTED_SOURCE_BINDING_COUNT,
        )
        if list(source_bindings) != sorted(source_bindings) or any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_binding_invalid", "source closure drifted"
            )
        self._verify_bindings(self.shchem_root, source_bindings, "source")
        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "master470/exact169 identity is unavailable",
            ) from exc
        return self._validate(
            manifest,
            output_bindings,
            output_bytes,
            source_manifest,
            source_bindings,
            master_identity,
        )

    def _validate(
        self,
        manifest: dict[str, Any],
        output_bindings: dict[str, dict[str, Any]],
        output_bytes: dict[str, bytes],
        source_manifest: dict[str, Any],
        source_bindings: dict[str, dict[str, Any]],
        master_identity: dict[str, Any],
    ) -> _Snapshot:
        crop_manifest = _json_object(output_bytes["crop_manifest.json"], "crop manifest")
        if (
            set(crop_manifest)
            != {"schema_version", "paper_id", "source_id", "counts", "records", "contracts"}
            or crop_manifest.get("paper_id") != PAPER_ID
            or crop_manifest.get("source_id") != EXPECTED_SOURCE_ID
            or crop_manifest.get("counts")
            != {
                "question_crop_bindings": 38,
                "shared_crop_bindings": 9,
                "answer_crop_bindings": 38,
                "total_crop_bindings": 85,
            }
            or not isinstance(crop_manifest.get("records"), list)
            or len(crop_manifest["records"]) != 85
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop manifest drifted"
            )
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_paths: set[str] = set()
        crop_roles: Counter[str] = Counter()
        for crop in crop_manifest["records"]:
            if not isinstance(crop, dict) or set(crop) != _CROP_ROW_KEYS:
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop row shape drifted"
                )
            crop_id = crop.get("crop_id")
            crop_path = _safe_relative(crop.get("crop_path"))
            source_path = _safe_relative(crop.get("source_path"))
            role = crop.get("role")
            output = output_bindings.get(crop_path)
            source = source_bindings.get(source_path)
            raw = output_bytes.get(crop_path)
            if (
                not isinstance(crop_id, str)
                or crop_id in crop_by_id
                or crop_path in crop_paths
                or role
                not in {
                    "question",
                    "paper_identity",
                    "theme_stimulus",
                    "printed_question_shared_stem",
                    "nonofficial_reference_answer",
                }
                or output is None
                or source is None
                or raw is None
                or output.get("bytes") != crop.get("crop_bytes")
                or output.get("sha256") != crop.get("crop_sha256")
                or source.get("bytes") != crop.get("source_bytes")
                or source.get("sha256") != crop.get("source_sha256")
                or crop.get("visual_inspection_status")
                != "actually_viewed_by_primary_model"
                or crop.get("pixel_reuse_allowed") is not False
                or crop.get("crop_generation")
                != "deterministic_pillow_xyxy_from_bound_source_no_source_overwrite"
                or len(raw) > MAX_CROP_BYTES
                or not self._valid_png(raw)
                or _sha256(raw) != crop.get("crop_sha256")
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_crop_binding_invalid",
                    "crop PNG/source/output binding drifted",
                )
            crop_by_id[crop_id] = crop
            crop_paths.add(crop_path)
            crop_roles[str(role)] += 1
        if (
            crop_paths
            != {path for path in output_bindings if path.startswith("crops/")}
            or crop_roles
            != Counter(
                {
                    "question": 38,
                    "paper_identity": 1,
                    "theme_stimulus": 6,
                    "printed_question_shared_stem": 2,
                    "nonofficial_reference_answer": 38,
                }
            )
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop closure drifted"
            )

        schema = _json_object(output_bytes["scan_record_schema.json"], "scan schema")
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "scan records")
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_schema_invalid", "record schema is invalid"
            ) from exc
        if len(records) != 38:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count drifted"
            )
        for index, record in enumerate(records, 1):
            if next(validator.iter_errors(record), None) is not None:
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} fails its frozen schema",
                )

        master_ids = set(master_identity["master_atomic_ids"])
        exact_ids = set(master_identity["exact_master_ids"])
        by_master_id: dict[str, dict[str, Any]] = {}
        answer_crop_ids: dict[str, frozenset[str]] = {}
        corrected_fields: dict[str, tuple[str, ...]] = {}
        direct_ids: list[str] = []
        prior_ids: set[str] = set()
        questions: set[str] = set()
        identity_crop = crop_by_id.get("CY2H2-2025-SHARED-IDENTITY")
        if identity_crop is None:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "identity crop is missing"
            )
        shared: set[str] = {str(identity_crop["crop_sha256"])}
        answers: set[str] = set()
        printed_by_theme: dict[int, set[str]] = defaultdict(set)
        compare_counts: Counter[str] = Counter()
        compare_by_field: dict[str, Counter[str]] = defaultdict(Counter)
        corrected_report: list[dict[str, Any]] = []
        blocked_report: list[dict[str, Any]] = []
        quality_report: list[dict[str, Any]] = []

        for index, record in enumerate(records, 1):
            _false_gates(record.get("authority_gates"), "record")
            hierarchy = record.get("hierarchy")
            master_id = hierarchy.get("atomic_part_id") if isinstance(hierarchy, dict) else None
            source_id = hierarchy.get("source_atomic_part_id") if isinstance(hierarchy, dict) else None
            if (
                record.get("scan_id") != f"VS-CY2-H2-SPRING-FINAL-{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
                or not isinstance(master_id, str)
                or _MASTER_PART_ID.fullmatch(master_id) is None
                or master_id in by_master_id
                or master_id not in master_ids
                or master_id in exact_ids
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("paper_sequence") != index
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record identity drifted"
                )
            by_master_id[master_id] = record
            direct_ids.append(master_id)
            printed_by_theme[int(hierarchy["theme_sequence"])].add(
                str(hierarchy["printed_question_id"])
            )
            dependency = record.get("dependency")
            prior = dependency.get("prior_atomic_part_ids") if isinstance(dependency, dict) else None
            viewed = record.get("viewed_evidence")
            if (
                not isinstance(prior, list)
                or not set(prior).issubset(prior_ids)
                or not isinstance(viewed, list)
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency drifted"
                )
            question_items = [item for item in viewed if item.get("evidence_role") == "question"]
            shared_items = [item for item in viewed if item.get("evidence_role") == "shared_material"]
            if (
                len(question_items) != 1
                or len(shared_items) not in {1, 2}
                or len(viewed) != len(question_items) + len(shared_items)
                or dependency.get("shared_material_crop_ids")
                != [item.get("crop_id") for item in shared_items]
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "evidence roles drifted"
                )
            for descriptor in viewed:
                crop = crop_by_id.get(descriptor.get("crop_id"))
                role = descriptor.get("evidence_role")
                crop_role = crop.get("role") if crop else None
                binding = output_bindings.get(crop.get("crop_path")) if crop else None
                if (
                    not isinstance(descriptor, dict)
                    or set(descriptor) != _EVIDENCE_KEYS
                    or descriptor.get("visual_inspection_status")
                    != "actually_viewed_by_primary_model"
                    or crop is None
                    or binding is None
                    or crop.get("crop_sha256") != descriptor.get("sha256")
                    or crop.get("crop_bytes") != descriptor.get("bytes")
                    or crop.get("source_page") != descriptor.get("source_page")
                    or crop.get("crop_dimensions")
                    != [descriptor.get("width"), descriptor.get("height")]
                    or (role == "question" and crop_role != "question")
                    or (role == "shared_material" and crop_role not in _SHARED_ROLES)
                ):
                    raise Caoyang2H2DirectVisualScanError(
                        "master_direct_scan_crop_binding_invalid",
                        "record evidence crop binding drifted",
                    )
                (questions if role == "question" else shared).add(
                    str(descriptor["sha256"])
                )

            difficulty = record.get("difficulty")
            factors = difficulty.get("factors") if isinstance(difficulty, dict) else None
            if (
                not isinstance(factors, list)
                or tuple(item.get("dimension_id") for item in factors) != FACTOR_IDS
                or difficulty.get("is_measured") is not False
                or difficulty.get("measured_difficulty") is not None
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "difficulty drifted"
                )
            candidate = record.get("candidate_analysis")
            if (
                not isinstance(candidate, dict)
                or set(candidate)
                != {
                    "candidate_only",
                    "correctness_verified",
                    "known_quality_note_code",
                    "secondary_response_R",
                    "solution_path_zh",
                    "subtasks_zh",
                }
                or candidate.get("candidate_only") is not True
                or candidate.get("correctness_verified") is not False
                or not isinstance(candidate.get("solution_path_zh"), list)
                or not candidate["solution_path_zh"]
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_analysis_invalid", "candidate analysis drifted"
                )
            quality_code = candidate.get("known_quality_note_code")
            if quality_code != KNOWN_QUALITY_NOTES.get(str(source_id)):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_quality_note_invalid", "quality note drifted"
                )
            if quality_code:
                quality_report.append(
                    {
                        "source_atomic_part_id": source_id,
                        "code": quality_code,
                        "blocking": False,
                    }
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
                != "part_aligned_to_nonofficial_appendix_pages_08_09_10_pagination_anomaly_preserved"
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not isinstance(answer.get("visual_alignment_evidence"), list)
                or len(answer["visual_alignment_evidence"]) != 1
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer boundary drifted"
                )
            answer_descriptor = answer["visual_alignment_evidence"][0]
            answer_crop = crop_by_id.get(answer_descriptor.get("crop_id"))
            answer_binding = output_bindings.get(answer_crop.get("crop_path")) if answer_crop else None
            if (
                not isinstance(answer_descriptor, dict)
                or set(answer_descriptor) != _EVIDENCE_KEYS - {"evidence_role"}
                or answer_descriptor.get("source_page") not in {8, 9, 10}
                or answer_descriptor.get("visual_inspection_status")
                != "aligned_via_actually_viewed_nonofficial_answer_crop_and_whole_page"
                or answer_crop is None
                or answer_binding is None
                or answer_crop.get("role") != "nonofficial_reference_answer"
                or answer_crop.get("atomic_part_id") != source_id
                or answer_crop.get("crop_sha256") != answer_descriptor.get("sha256")
                or answer_binding.get("bytes") != answer_descriptor.get("bytes")
            ):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer crop binding drifted"
                )
            answer_crop_ids[master_id] = frozenset({str(answer_descriptor["crop_id"])})
            answers.add(str(answer_descriptor["sha256"]))

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(COMPARISON_FIELDS):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_comparison_invalid", "comparison fields drifted"
                )
            corrected: list[str] = []
            blocked: list[str] = []
            for field in COMPARISON_FIELDS:
                item = comparison[field]
                state = item.get("compare") if isinstance(item, dict) else None
                if state not in {"agree", "corrected", "blocked"}:
                    raise Caoyang2H2DirectVisualScanError(
                        "master_direct_scan_comparison_invalid", "comparison state drifted"
                    )
                compare_counts[state] += 1
                compare_by_field[field][state] += 1
                if state == "corrected":
                    corrected.append(field)
                elif state == "blocked":
                    blocked.append(field)
            corrected_fields[master_id] = tuple(corrected)
            if corrected:
                corrected_report.append(
                    {
                        "atomic_part_id": master_id,
                        "source_atomic_part_id": source_id,
                        "fields": corrected,
                    }
                )
            if blocked:
                blocked_report.append(
                    {
                        "atomic_part_id": master_id,
                        "source_atomic_part_id": source_id,
                        "fields": blocked,
                    }
                )
            if record.get("evidence_binding_sha256") != self._evidence_binding(record):
                raise Caoyang2H2DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "evidence hash drifted"
                )
            prior_ids.add(master_id)

        direct_set = set(direct_ids)
        if (
            len(direct_set) != 38
            or direct_set & exact_ids
            or len(direct_set | exact_ids) != 207
            or len(master_ids - direct_set - exact_ids) != 263
            or [
                sum(record["hierarchy"]["theme_sequence"] == theme for record in records)
                for theme in range(1, 6)
            ]
            != [7, 6, 7, 10, 8]
            or [len(printed_by_theme[theme]) for theme in range(1, 6)]
            != [7, 6, 7, 10, 8]
            or (len(questions), len(shared), len(answers)) != (38, 9, 38)
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_count_mismatch", "derived counts drifted"
            )

        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("paper_id") != PAPER_ID
            or coverage.get("counts") != EXPECTED_COUNTS
            or coverage.get("field_coverage") != {field: 38 for field in FIELD_COVERAGE}
            or coverage.get("unscanned_atomic_part_ids") != []
            or coverage.get("all_authority_gates_false") is not True
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_coverage_invalid", "coverage drifted"
            )
        disagreement = _json_object(
            output_bytes["disagreement_report.json"], "disagreement"
        )
        if (
            disagreement.get("product_id") != PRODUCT_ID
            or disagreement.get("paper_id") != PAPER_ID
            or disagreement.get("overall_compare_counts")
            != dict(sorted(compare_counts.items()))
            or disagreement.get("by_field")
            != {
                field: dict(sorted(counts.items()))
                for field, counts in sorted(compare_by_field.items())
            }
            or disagreement.get("corrected_records") != corrected_report
            or disagreement.get("blocked_records") != blocked_report
            or disagreement.get("known_quality_notes") != quality_report
            or disagreement.get("authority_gates") != EXPECTED_GATES
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_disagreement_invalid", "disagreement drifted"
            )
        identity = source_manifest.get("paper_identity_boundary")
        if (
            source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("paper_id") != PAPER_ID
            or source_manifest.get("source_identity", {}).get("source_id")
            != EXPECTED_SOURCE_ID
            or source_manifest.get("source_identity", {}).get("official_status")
            != "nonofficial"
            or not isinstance(identity, dict)
            or identity.get("school") != "上海市曹杨二中"
            or identity.get("official_status") != "nonofficial"
            or identity.get("district_mock_claim_allowed") is not False
            or identity.get("level_exam_original_claim_allowed") is not False
            or [item.get("page_number") for item in source_manifest.get("question_whole_pages", [])]
            != list(range(1, 8))
            or [item.get("page_number") for item in source_manifest.get("answer_whole_pages", [])]
            != [8, 9, 10]
            or source_manifest.get("answer_boundary", {}).get("known_quality_notes")
            != [
                {"question": 7, "code": KNOWN_QUALITY_NOTES["WX-CAOYANG2-2025-H2-SECOND-FINAL-Q07-P01"], "blocking": False},
                {"question": 32, "code": KNOWN_QUALITY_NOTES["WX-CAOYANG2-2025-H2-SECOND-FINAL-Q32-P01"], "blocking": False},
                {"question": 33, "code": KNOWN_QUALITY_NOTES["WX-CAOYANG2-2025-H2-SECOND-FINAL-Q33-P01"], "blocking": False},
            ]
        ):
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_source_manifest_invalid", "source identity drifted"
            )
        _false_gates(source_manifest.get("authority_gates"), "source manifest")
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids_by_master=answer_crop_ids,
            corrected_fields_by_master=corrected_fields,
            manifest_self_sha256=str(manifest["manifest_self_sha256"]),
            master_crosswalk_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
            paper_identity_boundary=deepcopy(identity),
        )

    @staticmethod
    def _coverage() -> dict[str, int]:
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 38,
            "visual_scanned_master_atomic": 207,
            "remaining_unscanned": 263,
            "direct_exact_overlap": 0,
        }

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "output_binding_count": EXPECTED_OUTPUT_BINDING_COUNT,
            "source_binding_count": EXPECTED_SOURCE_BINDING_COUNT,
            "record_count": 38,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
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
        items: list[dict[str, Any]] = []
        for record in snapshot.records:
            master_id = record["hierarchy"]["atomic_part_id"]
            evidence = record["viewed_evidence"]
            quality_code = record["candidate_analysis"]["known_quality_note_code"]
            items.append(
                {
                    "master_node_id": master_id,
                    "scan_status": "visual_scan_completed",
                    "detail_available": True,
                    "question_evidence_count": 1,
                    "shared_evidence_count": sum(
                        item["evidence_role"] == "shared_material" for item in evidence
                    ),
                    "question_pixels_available": True,
                    "corrected_fields": list(snapshot.corrected_fields_by_master[master_id]),
                    **reference_answer_catalog_metadata(
                        record["answer"], None, quality_code
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
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_node_not_found", "node has no CaoYang direct scan", 404
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
            "reference_answer": project_reference_answer(
                answer,
                None,
                candidate["known_quality_note_code"],
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
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_node_not_found", "node has no CaoYang direct scan", 404
            )
        if crop_id in snapshot.answer_crop_ids_by_master[master_node_id]:
            raise Caoyang2H2DirectVisualScanError(
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
            raise Caoyang2H2DirectVisualScanError(
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        crop = snapshot.crop_by_id.get(crop_id)
        path = crop.get("crop_path") if crop else None
        binding = snapshot.output_bindings.get(path)
        raw = snapshot.output_bytes.get(path)
        role = crop.get("role") if crop else None
        if (
            crop is None
            or binding is None
            or raw is None
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or (descriptor["evidence_role"] == "question" and role != "question")
            or (descriptor["evidence_role"] == "shared_material" and role not in _SHARED_ROLES)
            or len(raw) > MAX_CROP_BYTES
            or not self._valid_png(raw)
            or _sha256(raw) != descriptor["sha256"]
        ):
            raise Caoyang2H2DirectVisualScanError(
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
    "Caoyang2H2DirectVisualScanError",
    "Caoyang2H2DirectVisualScanReader",
]
