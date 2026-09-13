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
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .reader_cancellation import ReaderThreadPoolExecutor, check_read_cancelled
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier

PRODUCT_ID = "RESEARCH2024-12-QUESTION-VISUAL-SCAN-V1-2026-08-25"
PAPER_ID = "XJYF2024-12-RESEARCH-PAPER"
PRODUCT_RELATIVE = Path(
    "kb/classification/question_visual_scan_research2024_12_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_SELF_SHA256 = (
    "f4257062e554c9bcf769723a5703b6cc8ffaabee6e92a49fecd205871b139a4d"
)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "d85abb55ae5762de044cdcd75361aa5f077b5e064de5ef9fae4281e6fa86ec6d"
)
EXPECTED_OUTPUT_FILES = frozenset(
    {
        "README.md",
        "build_visual_scan.py",
        "coverage_report.json",
        "disagreement_report.json",
        "research_analyses.py",
        "run_mutation_tests.py",
        "scan_record_schema.json",
        "scan_records.jsonl",
        "source_manifest.json",
        "test_visual_scan.py",
        "validate_visual_scan.py",
    }
)
EXPECTED_COUNTS = {
    "expected_themes": 5,
    "expected_printed_questions": 43,
    "expected_atomic_parts": 47,
    "scan_records": 47,
    "visual_scan_completed": 47,
    "blocked_pending_broader_crop": 0,
    "unique_question_crops_actually_viewed": 43,
    "unique_shared_crops_actually_viewed": 14,
    "nonofficial_answer_crops_aligned": 47,
    "unique_nonofficial_answer_evidence_sha256": 46,
    "answer_whole_pages_actually_viewed": 3,
}
EXPECTED_SOURCE_BINDING_COUNT = 128
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
CROP_MANIFEST_RELATIVE = (
    "kb/formal/candidates/intake_round_2026-08-02/"
    "xuejiaoyoufang_2025_first_mock_complete_paper/crop_manifest.jsonl"
)
EXPECTED_SOURCE_ID = "WX-XJYF-2024-12-HIGH3-FIRST-MOCK-CHEM-RESEARCH-PAPER"
MAX_CROP_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MASTER_ID = re.compile(r"XJYF2024-12-T[1-5]-Q0[1-9](?:-[AB])?\Z")
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


class MasterDirectVisualScanError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_manifest_by_id: dict[str, dict[str, Any]]
    source_bindings: dict[str, dict[str, Any]]
    source_bytes: dict[str, bytes]
    answer_crop_ids_by_master: dict[str, frozenset[str]]
    corrected_fields_by_master: dict[str, tuple[str, ...]]
    manifest_self_sha256: str
    manifest_file_sha256: str
    master_crosswalk_manifest_self_sha256: str


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
        raise MasterDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise MasterDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MasterDirectVisualScanError(
            "master_direct_scan_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    if not text or not text.endswith("\n") or any(not line.strip() for line in text.splitlines()):
        raise MasterDirectVisualScanError(
            "master_direct_scan_json_invalid",
            f"{label} must be non-empty JSONL without blank records",
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} is invalid JSON",
            ) from exc
        if not isinstance(value, dict):
            raise MasterDirectVisualScanError(
                "master_direct_scan_json_invalid",
                f"{label} record {index} must be an object",
            )
        rows.append(value)
    return rows


def _safe_relative(value: Any, *, one_component: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise MasterDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
        or (one_component and len(pure.parts) != 1)
    ):
        raise MasterDirectVisualScanError(
            "master_direct_scan_path_invalid", "binding path is unsafe"
        )
    return value


def _binding_index(
    value: Any, label: str, *, expected_count: int, one_component: bool = False
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise MasterDirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} binding count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} binding shape drifted"
            )
        path = _safe_relative(item.get("path"), one_component=one_component)
        if (
            path in result
            or type(item.get("bytes")) is not int
            or item["bytes"] < 1
            or not isinstance(item.get("sha256"), str)
            or _HEX64.fullmatch(item["sha256"]) is None
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} binding value drifted"
            )
        result[path] = dict(item)
    return result


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
        raise MasterDirectVisualScanError(
            "master_direct_scan_gate_elevated", f"{label} authority gates drifted"
        )


class ResearchDirectVisualScanReader:
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise MasterDirectVisualScanError(
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
            if cursor.is_symlink():
                raise MasterDirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "bound files may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise MasterDirectVisualScanError(
                "master_direct_scan_path_invalid", "a bound file leaves its fixed root"
            )
        try:
            return resolved, resolved.read_bytes()
        except OSError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_file_read_failed", "a bound file could not be read"
            ) from exc

    def _verify_bindings(
        self, root: Path, bindings: dict[str, dict[str, Any]], label: str
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            _, raw = self._verified_file(root, relative)
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise MasterDirectVisualScanError(
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

    def _validate_snapshot(
        self,
        *,
        manifest: dict[str, Any],
        output_bytes: dict[str, bytes],
        source_manifest: dict[str, Any],
        source_bindings: dict[str, dict[str, Any]],
        source_bytes: dict[str, bytes],
        master_identity: dict[str, Any],
    ) -> _Snapshot:
        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        disagreement = _json_object(
            output_bytes["disagreement_report.json"], "disagreement"
        )
        schema = _json_object(output_bytes["scan_record_schema.json"], "scan schema")
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "scan records")
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_schema_invalid", "scan record schema is invalid"
            ) from exc
        for index, record in enumerate(records, 1):
            issue = next(validator.iter_errors(record), None)
            if issue is not None:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"scan record {index} failed its bound Draft 2020-12 schema",
                )
        if len(records) != 47:
            raise MasterDirectVisualScanError(
                "master_direct_scan_count_mismatch", "scan record count is not 47"
            )

        crop_rows = _jsonl_objects(
            source_bytes[CROP_MANIFEST_RELATIVE], "source crop manifest"
        )
        crop_manifest_by_id: dict[str, dict[str, Any]] = {}
        for crop in crop_rows:
            crop_id = crop.get("crop_id")
            if not isinstance(crop_id, str) or crop_id in crop_manifest_by_id:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "source crop manifest contains missing or duplicate crop IDs",
                )
            crop_manifest_by_id[crop_id] = crop

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
        shared_hashes: set[str] = {
            "9d5c9f88fc5b47adb9580a13be4e70e0abc8bc035a8149c387b4322b594127dc"
        }
        answer_hashes: set[str] = set()
        prior_ids: set[str] = set()
        printed_by_theme: dict[int, set[str]] = defaultdict(set)

        for index, record in enumerate(records, 1):
            if record.get("authority_gates") != EXPECTED_GATES:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_gate_elevated",
                    "a scan record authority gate drifted",
                )
            if (
                record.get("scan_id") != f"VS-RESEARCH2024-12-{index:02d}"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_record_invalid", "scan order or completion drifted"
                )
            hierarchy = record["hierarchy"]
            master_id = hierarchy.get("atomic_part_id")
            if (
                not isinstance(master_id, str)
                or _MASTER_ID.fullmatch(master_id) is None
                or master_id in by_master_id
                or master_id not in master_ids
                or master_id in exact_master_ids
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("paper_sequence") != index
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_identity_conflict",
                    "direct scan identity is absent from master470, duplicated, or overlaps exact169",
                )
            direct_ids.append(master_id)
            by_master_id[master_id] = record
            printed_by_theme[int(hierarchy["theme_sequence"])].add(
                str(hierarchy["printed_question_id"])
            )

            dependency = record["dependency"]
            prior = dependency.get("prior_atomic_part_ids")
            if not isinstance(prior, list) or not set(prior).issubset(prior_ids):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_dependency_invalid",
                    "direct scan prior dependency is not strictly preceding",
                )
            viewed = record.get("viewed_evidence")
            if not isinstance(viewed, list):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "viewed evidence is not a list"
                )
            questions = [item for item in viewed if item.get("evidence_role") == "question"]
            shared = [
                item for item in viewed if item.get("evidence_role") == "shared_material"
            ]
            if (
                len(questions) != 1
                or len(questions) + len(shared) != len(viewed)
                or dependency.get("shared_material_crop_ids")
                != [item.get("crop_id") for item in shared]
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_evidence_invalid",
                    "question/shared evidence roles or dependency binding drifted",
                )
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
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_evidence_invalid",
                        "viewed evidence descriptor shape drifted",
                    )
                crop_id = descriptor["crop_id"]
                crop = crop_manifest_by_id.get(crop_id)
                expected_role = descriptor["evidence_role"]
                crop_role = str(crop.get("role", "")) if crop else ""
                if (
                    crop is None
                    or crop.get("crop_sha256") != descriptor["sha256"]
                    or crop.get("source_page_number") != descriptor["source_page"]
                    or crop.get("crop_dimensions")
                    != [descriptor["width"], descriptor["height"]]
                    or crop.get("direct_source_pixel_reuse_allowed") is not False
                    or crop.get("publication_allowed") is not False
                    or (expected_role == "question" and crop_role != "printed_question")
                    or (expected_role == "shared_material" and not crop_role.startswith("shared_"))
                ):
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_crop_binding_invalid",
                        "record evidence and source crop manifest drifted",
                    )
                crop_asset = crop.get("crop_asset")
                binding = source_bindings.get(crop_asset)
                if (
                    binding is None
                    or binding["sha256"] != descriptor["sha256"]
                    or binding["bytes"] != descriptor["bytes"]
                ):
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_crop_binding_invalid",
                        "record evidence lacks an exact source binding",
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
                raise MasterDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid",
                    "ten-factor cognitive difficulty contract drifted",
                )
            viewed_crop_ids = {item["crop_id"] for item in viewed}
            for factor in factors:
                if (
                    factor.get("evidence_status")
                    != "actual_visual_evidence_model_inference_not_measured"
                    or not set(factor.get("evidence_crop_ids", [])).issubset(viewed_crop_ids)
                    or not set(factor.get("source_atomic_part_ids", [])).issubset(prior)
                ):
                    raise MasterDirectVisualScanError(
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
                raise MasterDirectVisualScanError(
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
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("visual_alignment_evidence"), list)
                or len(answer["visual_alignment_evidence"]) != 1
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "nonofficial answer boundary drifted",
                )
            answer_descriptor = answer["visual_alignment_evidence"][0]
            if (
                not isinstance(answer_descriptor, dict)
                or set(answer_descriptor) != _EVIDENCE_KEYS - {"evidence_role"}
                or answer_descriptor.get("source_page") not in {9, 10, 11}
                or answer_descriptor.get("visual_inspection_status")
                != "aligned_via_actually_viewed_whole_answer_page"
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "answer alignment evidence drifted",
                )
            answer_binding = next(
                (
                    item
                    for item in source_bindings.values()
                    if item["sha256"] == answer_descriptor.get("sha256")
                    and item["bytes"] == answer_descriptor.get("bytes")
                ),
                None,
            )
            if answer_binding is None:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_answer_invalid",
                    "answer evidence is outside the source binding closure",
                )
            answer_crop_ids_by_master[master_id] = frozenset(
                {str(answer_descriptor["crop_id"])}
            )
            answer_hashes.add(str(answer_descriptor["sha256"]))

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(COMPARISON_FIELDS):
                raise MasterDirectVisualScanError(
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
                    raise MasterDirectVisualScanError(
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
                    {"atomic_part_id": master_id, "fields": corrected}
                )
            if blocked:
                blocked_records.append({"atomic_part_id": master_id, "fields": blocked})
            if record.get("evidence_binding_sha256") != self._record_evidence_binding(
                record
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_evidence_invalid",
                    "record evidence binding hash drifted",
                )
            prior_ids.add(master_id)

        if (
            len(direct_ids) != 47
            or len(set(direct_ids)) != 47
            or set(direct_ids) & exact_master_ids
            or len(set(direct_ids) | exact_master_ids) != 216
            or len(master_ids - set(direct_ids) - exact_master_ids) != 254
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "47 direct and 169 exact master scan identities do not form 216 unique rows",
            )
        if (
            [sum(record["hierarchy"]["theme_sequence"] == theme for record in records) for theme in range(1, 6)]
            != [9, 9, 9, 10, 10]
            or [len(printed_by_theme[theme]) for theme in range(1, 6)]
            != [9, 9, 7, 9, 9]
            or len(question_hashes) != 43
            or len(shared_hashes) != 14
            or len(answer_hashes) != 46
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_count_mismatch",
                "derived hierarchy or evidence counts drifted",
            )

        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("paper_id") != PAPER_ID
            or coverage.get("counts") != EXPECTED_COUNTS
            or coverage.get("field_coverage")
            != {field: 47 for field in FIELD_COVERAGE}
            or coverage.get("unscanned_atomic_part_ids") != []
            or coverage.get("all_authority_gates_false") is not True
        ):
            raise MasterDirectVisualScanError(
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
            or disagreement.get("authority_gates") != EXPECTED_GATES
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_disagreement_invalid",
                "disagreement report drifted",
            )

        expected_source_fields = {
            "product_id",
            "paper_id",
            "source_identity",
            "paper_identity_boundary",
            "legacy_package_boundary",
            "visual_inspection_declaration",
            "authority_gates",
            "answer_whole_pages",
            "paper_identity_visual_evidence",
            "rule_contract_bindings",
            "source_bindings",
            "wechat_search_boundary",
        }
        if (
            set(source_manifest) != expected_source_fields
            or source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("paper_id") != PAPER_ID
            or source_manifest.get("source_identity", {}).get("source_id")
            != EXPECTED_SOURCE_ID
            or source_manifest.get("source_identity", {}).get("official_status")
            != "nonofficial"
            or source_manifest.get("paper_identity_boundary", {}).get("district")
            != "unknown_pending_verification"
            or source_manifest.get("paper_identity_boundary", {}).get("school")
            != "unknown_pending_verification"
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_source_manifest_invalid",
                "source identity or manifest boundary drifted",
            )
        _false_gates(source_manifest.get("authority_gates"), "source manifest")
        if (
            len(source_manifest.get("answer_whole_pages", [])) != 3
            or [item.get("page_number") for item in source_manifest["answer_whole_pages"]]
            != [9, 10, 11]
            or any(
                item.get("role") != "nonofficial_reference_answer_whole_page"
                for item in source_manifest["answer_whole_pages"]
            )
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_answer_invalid", "answer whole-page boundary drifted"
            )
        bound_hashes = {binding["sha256"] for binding in source_bindings.values()}
        if not question_hashes | shared_hashes | answer_hashes <= bound_hashes:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "evidence leaves the source hash closure",
            )

        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_manifest_by_id=crop_manifest_by_id,
            source_bindings=source_bindings,
            source_bytes=source_bytes,
            answer_crop_ids_by_master=answer_crop_ids_by_master,
            corrected_fields_by_master=corrected_fields_by_master,
            manifest_self_sha256=str(manifest["manifest_self_sha256"]),
            manifest_file_sha256=EXPECTED_MANIFEST_FILE_SHA256,
            master_crosswalk_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
        )

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256:
            raise MasterDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest file bytes drifted"
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
            or manifest.get("manifest_self_sha256")
            != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest identity or self hash drifted"
            )
        _false_gates(manifest.get("authority_gates"), "manifest")
        output_bindings = _binding_index(
            manifest.get("output_bindings"),
            "output",
            expected_count=len(EXPECTED_OUTPUT_FILES),
            one_component=True,
        )
        if set(output_bindings) != EXPECTED_OUTPUT_FILES:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid", "output file set drifted"
            )
        try:
            product_files = {
                path.name for path in self.product_root.iterdir() if not path.name.startswith(".")
            }
        except OSError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_file_read_failed", "product directory is unavailable"
            ) from exc
        if product_files != set(EXPECTED_OUTPUT_FILES) | {"manifest.json"}:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid", "product contains unbound files"
            )
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        if manifest.get("source_manifest_binding") != output_bindings[
            "source_manifest.json"
        ]:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "source manifest is not exactly bound by the product manifest",
            )
        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        source_bindings = _binding_index(
            source_manifest.get("source_bindings"),
            "source",
            expected_count=EXPECTED_SOURCE_BINDING_COUNT,
        )
        if list(source_bindings) != sorted(source_bindings) or CROP_MANIFEST_RELATIVE not in source_bindings:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "source bindings are unsorted or omit the crop manifest",
            )
        if any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "dynamic central snapshots are forbidden from the source closure",
            )
        source_bytes = self._verify_bindings(
            self.shchem_root, source_bindings, "source"
        )
        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity index is unavailable",
            ) from exc
        return self._validate_snapshot(
            manifest=manifest,
            output_bytes=output_bytes,
            source_manifest=source_manifest,
            source_bindings=source_bindings,
            source_bytes=source_bytes,
            master_identity=master_identity,
        )

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "manifest_file_sha256": snapshot.manifest_file_sha256,
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "output_binding_count": len(EXPECTED_OUTPUT_FILES),
            "source_binding_count": EXPECTED_SOURCE_BINDING_COUNT,
            "record_count": 47,
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
            "direct_master_visual_scanned": 47,
            "visual_scanned_master_atomic": 216,
            "remaining_unscanned": 254,
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
                    "corrected_fields": list(
                        snapshot.corrected_fields_by_master[master_id]
                    ),
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
            "paper_identity_boundary": {
                "visible_paper_face_title": "化学 调研卷（2024年12月）",
                "district": "unknown_pending_verification",
                "school": "unknown_pending_verification",
                "official_status": "nonofficial",
                "article_title_prelabels_only_not_paper_face_verified": [
                    "上海",
                    "2025届",
                    "高三",
                    "一模",
                ],
            },
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    @staticmethod
    def _evidence_projection(master_id: str, record: dict[str, Any]) -> list[dict[str, Any]]:
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
            raise MasterDirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise MasterDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "master node has no direct visual scan",
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
            # This hierarchy belongs to the intake scan product.  Its paper ID
            # intentionally differs from the frozen master paper parent, so it
            # must never masquerade as the master-native parent chain.
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
            "evidence_descriptors": self._evidence_projection(master_node_id, record),
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
            raise MasterDirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise MasterDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "master node has no direct visual scan",
                404,
            )
        if crop_id in snapshot.answer_crop_ids_by_master[master_node_id]:
            raise MasterDirectVisualScanError(
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
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_not_found",
                "crop is not question/shared evidence for this master node",
                404,
            )
        crop = snapshot.crop_manifest_by_id.get(crop_id)
        crop_asset = crop.get("crop_asset") if crop else None
        binding = snapshot.source_bindings.get(crop_asset)
        data = snapshot.source_bytes.get(crop_asset)
        expected_role = descriptor["evidence_role"]
        crop_role = str(crop.get("role", "")) if crop else ""
        if (
            crop is None
            or binding is None
            or data is None
            or crop.get("crop_sha256") != descriptor["sha256"]
            or binding["sha256"] != descriptor["sha256"]
            or binding["bytes"] != descriptor["bytes"]
            or len(data) != descriptor["bytes"]
            or (expected_role == "question" and crop_role != "printed_question")
            or (expected_role == "shared_material" and not crop_role.startswith("shared_"))
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_binding_invalid",
                "crop no longer matches record, source manifest, and source binding",
            )
        if len(data) > MAX_CROP_BYTES:
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_too_large",
                "crop exceeds the one MiB response limit",
                413,
            )
        if not self._valid_png(data):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_not_png",
                "crop is not a structurally valid PNG",
            )
        if _sha256(data) != descriptor["sha256"]:
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_hash_mismatch", "crop bytes drifted"
            )
        return CandidateCropPayload(data=data, sha256=descriptor["sha256"])


AGGREGATE_PRODUCT_ID = "MASTER-DIRECT-VISUAL-SCAN-AGGREGATE-V1"
AGGREGATE_COUNTS = {
    "direct_scan_products": 10,
    "direct_scan_records": 224,
    "visual_scan_completed": 224,
    "blocked_pending_broader_crop": 0,
}


@dataclass(frozen=True)
class _DirectReaderRegistration:
    attribute: str
    expected_record_count: int


class MasterDirectVisualScanReader:
    """Fail-closed union of all registered immutable master-direct products."""

    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
        *,
        include_archived_candidates: bool = False,
    ):
        self.shchem_root = shchem_root.resolve()
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )
        self.research = ResearchDirectVisualScanReader(
            self.shchem_root, self.master_workbench
        )
        # Import lazily so the SHS reader can share the common gateway error
        # without creating an import-time cycle.
        from .caoyang2_h2_direct_visual_scan import (
            Caoyang2H2DirectVisualScanReader,
        )
        from .fengxian2025_theme2_direct_visual_scan import (
            Fengxian2025Theme2DirectVisualScanReader,
        )
        from .huangpu2025_theme4_direct_visual_scan import (
            Hongkou2026SecondMockTheme4DirectVisualScanReader,
            Huangpu2025Theme4DirectVisualScanReader,
            Qibao2025OpeningTheme4DirectVisualScanReader,
        )
        from .jiading2025_theme1_direct_visual_scan import (
            Jiading2025Theme1DirectVisualScanReader,
        )
        from .pudong2026_first_mock_theme_direct_visual_scan import (
            Pudong2026FirstMockThemeDirectVisualScanReader,
        )
        from .shanghai_exam_2026_recall_direct_visual_scan import (
            ShanghaiExam2026RecallDirectVisualScanReader,
        )
        from .shanghai_school_h2_direct_visual_scan import (
            ShanghaiSchoolH2DirectVisualScanReader,
        )

        self.shanghai_school_h2 = ShanghaiSchoolH2DirectVisualScanReader(
            self.shchem_root, self.master_workbench
        )
        self.caoyang2_h2 = Caoyang2H2DirectVisualScanReader(
            self.shchem_root, self.master_workbench
        )
        self.shanghai_exam_2026_recall = (
            ShanghaiExam2026RecallDirectVisualScanReader(
                self.shchem_root, self.master_workbench
            )
        )
        self.pudong2026_first_mock_theme = (
            Pudong2026FirstMockThemeDirectVisualScanReader(
                self.shchem_root, self.master_workbench
            )
        )
        self.jiading2025_theme1 = Jiading2025Theme1DirectVisualScanReader(
            self.shchem_root, self.master_workbench
        )
        self.huangpu2025_theme4 = Huangpu2025Theme4DirectVisualScanReader(
            self.shchem_root, self.master_workbench
        )
        self.qibao2025_opening_theme4 = (
            Qibao2025OpeningTheme4DirectVisualScanReader(
                self.shchem_root, self.master_workbench
            )
        )
        self.hongkou2026_second_mock_theme4 = (
            Hongkou2026SecondMockTheme4DirectVisualScanReader(
                self.shchem_root, self.master_workbench
            )
        )
        self.fengxian2025_theme2 = Fengxian2025Theme2DirectVisualScanReader(
            self.shchem_root
        )
        self._reader_registrations = (
            _DirectReaderRegistration("research", 47),
            _DirectReaderRegistration("shanghai_school_h2", 43),
            _DirectReaderRegistration("caoyang2_h2", 38),
            _DirectReaderRegistration("shanghai_exam_2026_recall", 35),
            _DirectReaderRegistration("pudong2026_first_mock_theme", 11),
            _DirectReaderRegistration("jiading2025_theme1", 10),
            _DirectReaderRegistration("huangpu2025_theme4", 11),
            _DirectReaderRegistration("qibao2025_opening_theme4", 10),
            _DirectReaderRegistration("hongkou2026_second_mock_theme4", 9),
            _DirectReaderRegistration("fengxian2025_theme2", 10),
        )
        # Native previews can read explicitly bound older candidate packages.
        # The frozen ten-product HTTP projection stays unchanged by default;
        # these adapters do not manufacture new formal scan manifests.
        if include_archived_candidates:
            from .fudan2026_april_theme5_zns_direct_visual_scan import (
                Fudan2026AprilTheme5ZnsDirectVisualScanReader,
            )
            from .level_exam2025_theme3_fosinopril_direct_visual_scan import (
                LevelExam2025Theme3FosinoprilDirectVisualScanReader,
            )
            from .shanghai_high_east2025_theme45_direct_visual_scan import (
                ShanghaiHighEast2025Theme45DirectVisualScanReader,
            )
            from .songjiang2025_theme2_direct_visual_scan import (
                Songjiang2025Theme2DirectVisualScanReader,
            )

            self.songjiang2025_theme2 = Songjiang2025Theme2DirectVisualScanReader(
                self.shchem_root, master_workbench=self.master_workbench
            )
            self.shanghai_high_east2025_theme45 = (
                ShanghaiHighEast2025Theme45DirectVisualScanReader(
                    self.shchem_root, master_workbench=self.master_workbench
                )
            )
            self.level_exam2025_theme3_fosinopril = (
                LevelExam2025Theme3FosinoprilDirectVisualScanReader(
                    self.shchem_root, master_workbench=self.master_workbench
                )
            )
            self.fudan2026_april_theme5_zns = (
                Fudan2026AprilTheme5ZnsDirectVisualScanReader(
                    self.shchem_root, master_workbench=self.master_workbench
                )
            )
            self._reader_registrations += (
                _DirectReaderRegistration("songjiang2025_theme2", 9),
                _DirectReaderRegistration("shanghai_high_east2025_theme45", 11),
                _DirectReaderRegistration("level_exam2025_theme3_fosinopril", 9),
                _DirectReaderRegistration("fudan2026_april_theme5_zns", 9),
            )

    @staticmethod
    def _coverage(direct_record_count: int) -> dict[str, int]:
        visual_scanned = 169 + direct_record_count
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": direct_record_count,
            "visual_scanned_master_atomic": visual_scanned,
            "remaining_unscanned": 470 - visual_scanned,
            "direct_exact_overlap": 0,
        }

    @staticmethod
    def _counts(catalogs: tuple[dict[str, Any], ...]) -> dict[str, int]:
        record_count = sum(int(catalog["count"]) for catalog in catalogs)
        return {
            "direct_scan_products": len(catalogs),
            "direct_scan_records": record_count,
            "visual_scan_completed": record_count,
            "blocked_pending_broader_crop": 0,
        }

    @staticmethod
    def _product_projection(catalog: dict[str, Any]) -> dict[str, Any]:
        return {
            "product_id": catalog["product_id"],
            "paper_id": catalog["paper_id"],
            "count": catalog["count"],
            "paper_identity_boundary": deepcopy(
                catalog["paper_identity_boundary"]
            ),
            "integrity": deepcopy(catalog["integrity"]),
        }

    @staticmethod
    def _aggregate_integrity(
        catalogs: tuple[dict[str, Any], ...]
    ) -> dict[str, Any]:
        integrities = [catalog["integrity"] for catalog in catalogs]
        master_hash = integrities[0]["master_crosswalk_manifest_self_sha256"]
        if any(
            item["master_crosswalk_manifest_self_sha256"] != master_hash
            for item in integrities[1:]
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_master_identity_conflict",
                "direct products were validated against different master snapshots",
            )
        record_count = sum(int(catalog["count"]) for catalog in catalogs)
        return {
            "batch_count": len(catalogs),
            "record_count": record_count,
            "batch_manifest_file_sha256s": [
                item["manifest_file_sha256"] for item in integrities
            ],
            "master_crosswalk_manifest_self_sha256": master_hash,
            "output_binding_count": sum(
                item["output_binding_count"] for item in integrities
            ),
            "source_binding_count": sum(
                item["source_binding_count"] for item in integrities
            ),
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "direct_batch_disjoint_verified_on_read": True,
            "fail_closed": True,
        }

    def _catalogs(self) -> tuple[dict[str, Any], ...]:
        # Every registered product validates for every aggregate read.  A
        # drift in any one product therefore fails the aggregate endpoint as
        # a whole, while the native master/exact endpoints remain independent.
        def validate_registered(
            registration: _DirectReaderRegistration,
        ) -> dict[str, Any]:
            return getattr(self, registration.attribute).catalog()

        # The immutable readers are independent and read-only.  Verify
        # them concurrently while preserving registration order; this keeps
        # the unified teacher workbench inside its existing five-second HTTP
        # budget without weakening any product, source, or semantic check.
        with ReaderThreadPoolExecutor(
            max_workers=len(self._reader_registrations),
            thread_name_prefix="shchem-direct-verify",
        ) as executor:
            catalogs = tuple(
                executor.map(validate_registered, self._reader_registrations)
            )
        product_ids = [str(catalog["product_id"]) for catalog in catalogs]
        id_sets = [set(catalog["master_node_ids"]) for catalog in catalogs]
        if len(set(product_ids)) != len(product_ids):
            raise MasterDirectVisualScanError(
                "master_direct_scan_product_identity_conflict",
                "registered direct products do not have unique product identities",
            )
        for registration, catalog, node_ids in zip(
            self._reader_registrations, catalogs, id_sets, strict=True
        ):
            if (
                type(catalog.get("count")) is not int
                or catalog["count"] != registration.expected_record_count
                or len(node_ids) != registration.expected_record_count
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_product_count_conflict",
                    "a registered direct product does not match its frozen record count",
                )
        for index, left_ids in enumerate(id_sets):
            if any(left_ids & right_ids for right_ids in id_sets[index + 1 :]):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_identity_conflict",
                    "registered direct products overlap",
                )
        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity index is unavailable",
            ) from exc
        master_ids = set(master_identity["master_atomic_ids"])
        exact_ids = set(master_identity["exact_master_ids"])
        direct_ids: set[str] = set().union(*id_sets)
        expected_direct_count = sum(
            registration.expected_record_count
            for registration in self._reader_registrations
        )
        expected_union_count = len(exact_ids) + expected_direct_count
        expected_remaining = len(master_ids) - expected_union_count
        if (
            len(master_ids) != 470
            or len(exact_ids) != 169
            or not exact_ids <= master_ids
            or len(direct_ids) != expected_direct_count
            or not direct_ids <= master_ids
            or direct_ids & exact_ids
            or len(direct_ids | exact_ids) != expected_union_count
            or len(master_ids - direct_ids - exact_ids) != expected_remaining
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "registered direct rows do not form the frozen master470/exact169 union",
            )
        return catalogs

    def status(self) -> dict[str, Any]:
        catalogs = self._catalogs()
        counts = self._counts(catalogs)
        return {
            "product_id": AGGREGATE_PRODUCT_ID,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": counts,
            "coverage": self._coverage(counts["direct_scan_records"]),
            "products": [self._product_projection(catalog) for catalog in catalogs],
            "authority": dict(AUTHORITY),
            "integrity": self._aggregate_integrity(catalogs),
        }

    def catalog(self) -> dict[str, Any]:
        catalogs = self._catalogs()
        items: list[dict[str, Any]] = []
        for catalog in catalogs:
            for item in catalog["items"]:
                items.append(
                    {
                        "product_id": catalog["product_id"],
                        "paper_id": catalog["paper_id"],
                        **deepcopy(item),
                    }
                )
        counts = self._counts(catalogs)
        return {
            "product_id": AGGREGATE_PRODUCT_ID,
            "scope": SCOPE,
            "master_node_ids": [item["master_node_id"] for item in items],
            "items": items,
            "count": len(items),
            "coverage": self._coverage(counts["direct_scan_records"]),
            "products": [self._product_projection(catalog) for catalog in catalogs],
            "authority": dict(AUTHORITY),
            "integrity": self._aggregate_integrity(catalogs),
        }

    @staticmethod
    def _validate_master_node_id(master_node_id: str) -> None:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except SecurityError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_invalid_node_id",
                "master node ID is invalid",
                400,
            ) from exc

    def _reader_for_node(self, master_node_id: str):
        self._validate_master_node_id(master_node_id)
        catalogs = self._catalogs()
        for registration, catalog in zip(
            self._reader_registrations, catalogs, strict=True
        ):
            if master_node_id in set(catalog["master_node_ids"]):
                return getattr(self, registration.attribute)
        raise MasterDirectVisualScanError(
            "master_direct_scan_node_not_found",
            "master node has no direct visual scan",
            404,
        )

    def detail(self, master_node_id: str) -> dict[str, Any]:
        return self._reader_for_node(master_node_id).detail(master_node_id)

    def teacher_answer_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
        reader = self._reader_for_node(master_node_id)
        method = getattr(reader, "teacher_answer_crop", None)
        if not callable(method):
            raise MasterDirectVisualScanError(
                "teacher_answer_image_unavailable", "reader has no teacher answer image route", 404
            )
        return method(master_node_id, crop_id)

    def question_crop(
        self, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        try:
            validate_identifier(crop_id, "crop_id")
        except SecurityError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_invalid_identifier", "crop ID is invalid", 400
            ) from exc
        return self._reader_for_node(master_node_id).question_crop(
            master_node_id, crop_id
        )


__all__ = [
    "AGGREGATE_COUNTS",
    "AGGREGATE_PRODUCT_ID",
    "AUTHORITY",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "MAX_CROP_BYTES",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "MasterDirectVisualScanError",
    "MasterDirectVisualScanReader",
    "ResearchDirectVisualScanReader",
]
