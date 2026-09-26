from __future__ import annotations

import hashlib
import json
import struct
import zlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import MAX_CROP_BYTES, MasterDirectVisualScanError
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .security import SecurityError, validate_identifier
from .source_crop_revision import (
    RECIPE_SHA256,
    REVISION_ID,
    SourceCropRevisionError,
    recrop_fengxian_view,
)

PRODUCT_ID = "QVS-FX2025-SECOND-MOCK-T2-DISINFECTANT-V2"
PAPER_ID = "FX2025-SECOND-MOCK"
THEME_ID = "FX2025-SECOND-MOCK-T2"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_fengxian_2025_second_mock_theme2_"
    "disinfectant_v2_2026-09-08"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_BYTES = 6150
EXPECTED_MANIFEST_FILE_SHA256 = (
    "3986d212aff76ab7a2130b9ecd4a7abca397912de043e7aeb4d369e59ad9e7c6"
)

EXPECTED_MASTER_NODE_IDS = tuple(f"FX2025-EM-S2-Q{index}-P1" for index in range(1, 11))
EXPECTED_GROUPED_ATOMIC_IDS = {
    "FX2025-EM-S2-Q1-P1": ("FX2025-EM-S2-Q1-P1",),
    "FX2025-EM-S2-Q2-P1": ("FX2025-EM-S2-Q2-P1", "FX2025-EM-S2-Q2-P2"),
    "FX2025-EM-S2-Q3-P1": ("FX2025-EM-S2-Q3-P1", "FX2025-EM-S2-Q3-P2"),
    "FX2025-EM-S2-Q4-P1": ("FX2025-EM-S2-Q4-P1",),
    "FX2025-EM-S2-Q5-P1": ("FX2025-EM-S2-Q5-P1",),
    "FX2025-EM-S2-Q6-P1": ("FX2025-EM-S2-Q6-P1",),
    "FX2025-EM-S2-Q7-P1": ("FX2025-EM-S2-Q7-P1", "FX2025-EM-S2-Q7-P2"),
    "FX2025-EM-S2-Q8-P1": ("FX2025-EM-S2-Q8-P1",),
    "FX2025-EM-S2-Q9-P1": ("FX2025-EM-S2-Q9-P1",),
    "FX2025-EM-S2-Q10-P1": ("FX2025-EM-S2-Q10-P1",),
}
EXPECTED_EFFECTIVE_ATOMIC_IDS = tuple(
    atomic_id
    for master_id in EXPECTED_MASTER_NODE_IDS
    for atomic_id in EXPECTED_GROUPED_ATOMIC_IDS[master_id]
)
EXPECTED_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "chemistry_correctness_verified",
        "difficulty_verified",
        "formal_promotion_allowed",
        "generation_allowed",
        "human_chemistry_reviewed",
        "human_reviewed",
        "human_taxonomy_reviewed",
        "measured_difficulty_verified",
        "official",
        "official_answer_claim_allowed",
        "official_scoring_claim_allowed",
        "pixel_reuse_allowed",
        "publication_allowed",
        "retrieval_allowed",
        "retrieval_ready",
        "teaching_use_allowed",
    }
)
AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    **{
        key: False
        for key in (
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
        )
    },
}
PAPER_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": "待核验（当前主题页未显示完整卷首标题）",
    "article_title_literal": "2025届上海市奉贤区高三二模化学试卷和参考答案（转载归档标题）",
    "year": 2025,
    "region_label": "奉贤区（转载归档标题；卷首身份待独立核验）",
    "paper_family": "second_mock_repost_classified",
    "paper_type": "二模（转载归档标题；卷首身份待独立核验）",
    "covered_scope": "主题二（消毒剂的制备）",
    "coverage_scope": "theme_2_disinfectant_only_10_master_projection_13_atomic",
    "complete_paper_visual_scan_claim_allowed": False,
    "district_face_claim_allowed": False,
    "second_mock_face_claim_allowed": False,
    "official_identity_claim_allowed": False,
    "official_status": "nonofficial_wechat_repost",
    "source_account": "上海初高中化学",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
}


class Fengxian2025Theme2DirectVisualScanError(MasterDirectVisualScanError):
    pass


@dataclass(frozen=True)
class _Snapshot:
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    crop_by_id: dict[str, dict[str, Any]]
    crop_bytes: dict[str, bytes]
    forbidden_crop_ids: frozenset[str]
    manifest_file_sha256: str
    manifest_self_sha256: str
    master_crosswalk_manifest_self_sha256: str
    output_binding_count: int
    source_binding_count: int


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
        raise Fengxian2025Theme2DirectVisualScanError(
            "fengxian_theme2_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise Fengxian2025Theme2DirectVisualScanError(
            "fengxian_theme2_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> tuple[dict[str, Any], ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Fengxian2025Theme2DirectVisualScanError(
            "fengxian_theme2_json_invalid", f"{label} is not strict UTF-8 JSONL"
        ) from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_json_invalid",
                f"{label} line {line_number} is invalid JSON",
            ) from exc
        if not isinstance(value, dict):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_json_invalid",
                f"{label} line {line_number} must be an object",
            )
        rows.append(value)
    return tuple(rows)


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise Fengxian2025Theme2DirectVisualScanError(
            "fengxian_theme2_path_invalid", f"{label} must be a relative path"
        )
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise Fengxian2025Theme2DirectVisualScanError(
            "fengxian_theme2_path_invalid", f"{label} escapes the product root"
        )
    return path


def _pick(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return default


class Fengxian2025Theme2DirectVisualScanReader:
    """Strict read-only adapter for the frozen grouped Fengxian visual scan."""

    def __init__(self, shchem_root: Path):
        self.shchem_root = Path(shchem_root).resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_product_root_invalid",
                "product root escaped sh-chem-db",
            )

    def _read_product_file(self, relative: str) -> bytes:
        rel = _safe_relative(relative, "product file")
        path = self.product_root.joinpath(*rel.parts)
        current = self.product_root
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_path_invalid", f"symlink is forbidden: {relative}"
                )
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_product_incomplete",
                f"missing product file: {relative}",
            ) from exc
        if not resolved.is_relative_to(self.product_root) or path.is_symlink():
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_path_invalid", f"unsafe product path: {relative}"
            )
        try:
            return resolved.read_bytes()
        except OSError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_product_incomplete", f"cannot read: {relative}"
            ) from exc

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        return _sha256(_canonical_bytes(manifest))

    @staticmethod
    def _record_master_id(record: dict[str, Any]) -> str:
        projection = record.get("master_projection")
        projection = projection if isinstance(projection, dict) else {}
        hierarchy = record.get("hierarchy")
        hierarchy = hierarchy if isinstance(hierarchy, dict) else {}
        value = _pick(
            record,
            "master_node_id",
            "master_projection_id",
            default=_pick(
                projection,
                "keyed_reader_id",
                "master_projection_id",
                default=_pick(hierarchy, "master_node_id", "master_projection_id"),
            ),
        )
        return value if isinstance(value, str) else ""

    @staticmethod
    def _atomic_units(record: dict[str, Any]) -> list[dict[str, Any]]:
        value = _pick(
            record,
            "minimal_atomic_units",
            "grouped_atomic_units",
            "atomic_units",
            default=[],
        )
        return value if isinstance(value, list) else []

    @staticmethod
    def _atomic_id(unit: dict[str, Any]) -> str:
        hierarchy = unit.get("hierarchy")
        hierarchy = hierarchy if isinstance(hierarchy, dict) else {}
        value = _pick(
            unit,
            "atomic_part_id",
            "effective_atomic_part_id",
            "atomic_id",
            default=hierarchy.get("atomic_part_id"),
        )
        return value if isinstance(value, str) else ""

    @staticmethod
    def _crop_entries(crop_manifest: dict[str, Any]) -> list[dict[str, Any]]:
        value = _pick(crop_manifest, "assets", "crops", "crop_bindings", default=[])
        return value if isinstance(value, list) else []

    @staticmethod
    def _crop_id(crop: dict[str, Any]) -> str:
        explicit = _pick(crop, "crop_id", "id")
        if isinstance(explicit, str) and explicit:
            return explicit
        asset = _pick(crop, "path", "asset", "stored_path", "output_path")
        if isinstance(asset, str) and asset:
            return PurePosixPath(asset.replace("\\", "/")).stem
        return ""

    @staticmethod
    def _crop_path(crop: dict[str, Any]) -> str:
        value = _pick(crop, "path", "asset", "stored_path", "output_path")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _crop_role(crop: dict[str, Any]) -> str:
        value = _pick(crop, "role", "category", "evidence_role")
        if value == "context":
            return "shared_material"
        if isinstance(value, str) and value:
            return value
        return "unknown"

    @staticmethod
    def _png_dimensions(raw: bytes) -> tuple[int, int]:
        if len(raw) < 33 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_png_invalid", "crop is not a valid PNG"
            )
        if raw[12:16] != b"IHDR" or struct.unpack(">I", raw[8:12])[0] != 13:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_png_invalid", "crop has an invalid IHDR"
            )
        if zlib.crc32(raw[12:29]) & 0xFFFFFFFF != struct.unpack(">I", raw[29:33])[0]:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_png_invalid", "crop IHDR checksum is invalid"
            )
        width, height = struct.unpack(">II", raw[16:24])
        if width < 1 or height < 1:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_png_invalid", "crop dimensions are invalid"
            )
        return width, height

    def _snapshot(self) -> _Snapshot:
        manifest_raw = self._read_product_file("manifest.json")
        if (
            len(manifest_raw) != EXPECTED_MANIFEST_BYTES
            or _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256
        ):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_manifest_drift", "frozen manifest binding drifted"
            )
        manifest = _json_object(manifest_raw, "manifest.json")
        actual_self = self._manifest_self_hash(manifest)
        outputs = _pick(manifest, "files", "outputs", default=None)
        if not isinstance(outputs, list) or manifest.get("file_count") != len(outputs):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_manifest_drift", "manifest closure is invalid"
            )
        output_bytes: dict[str, bytes] = {}
        for binding in outputs:
            if not isinstance(binding, dict):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_manifest_drift", "invalid output binding"
                )
            relative = binding.get("path")
            expected_hash = binding.get("sha256")
            raw = self._read_product_file(relative)
            if not isinstance(expected_hash, str) or _sha256(raw) != expected_hash:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_binding_mismatch",
                    f"output hash drifted: {relative}",
                )
            expected_bytes = binding.get("bytes")
            if expected_bytes is not None and expected_bytes != len(raw):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_binding_mismatch",
                    f"output size drifted: {relative}",
                )
            output_bytes[relative] = raw
        required = {"scan_records.jsonl", "crop_manifest.json", "source_manifest.json"}
        if not required.issubset(output_bytes):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_product_incomplete", "required outputs are not bound"
            )

        records = _jsonl_objects(
            output_bytes["scan_records.jsonl"], "scan_records.jsonl"
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        effective_seen: list[str] = []
        for index, record in enumerate(records, start=1):
            master_id = self._record_master_id(record)
            if master_id not in EXPECTED_GROUPED_ATOMIC_IDS:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_membership_drift", "master membership drifted"
                )
            hierarchy = record.get("hierarchy")
            if not isinstance(hierarchy, dict):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_hierarchy_drift", "hierarchy is absent"
                )
            if (
                hierarchy.get("paper_id") != PAPER_ID
                or _pick(hierarchy, "theme_id", "theme_big_question_id") != THEME_ID
                or record.get("record_index") != index
                or record.get("theme_context", {}).get("choice_section_promoted")
                is not False
            ):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_hierarchy_drift",
                    "four-level hierarchy/order drifted",
                )
            atomic_id = hierarchy.get("atomic_part_id")
            if not isinstance(atomic_id, str) or atomic_id in effective_seen:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_atomic_group_drift", "atomic membership overlapped"
                )
            effective_seen.append(atomic_id)
            gates = _pick(record, "authority_gates", "gates", default={})
            if not isinstance(gates, dict) or any(
                value is not False for value in gates.values()
            ):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_gate_drift", "candidate-only gates drifted"
                )
            difficulty = record.get("difficulty")
            if (
                not isinstance(difficulty, dict)
                or difficulty.get("measured") is not False
            ):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_difficulty_gate_drift",
                    "measured difficulty drifted",
                )
            grouped.setdefault(master_id, []).append(record)
        if tuple(effective_seen) != EXPECTED_EFFECTIVE_ATOMIC_IDS:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_count_or_order_drift",
                "10/13 count or source order drifted",
            )
        by_master: dict[str, dict[str, Any]] = {}
        for printed_order, master_id in enumerate(EXPECTED_MASTER_NODE_IDS, start=1):
            units = grouped.get(master_id, [])
            unit_ids = tuple(self._atomic_id(unit) for unit in units)
            if unit_ids != EXPECTED_GROUPED_ATOMIC_IDS[master_id]:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_atomic_group_drift",
                    "grouped atomic mapping drifted",
                )
            if any(
                _pick(
                    unit["hierarchy"],
                    "printed_question_order",
                    "printed_sequence",
                )
                != printed_order
                or unit["hierarchy"].get("atomic_sequence_in_printed") != unit_index
                for unit_index, unit in enumerate(units, start=1)
            ):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_hierarchy_drift", "printed/atomic order drifted"
                )
            first = units[0]
            by_master[master_id] = {
                "hierarchy": {
                    **deepcopy(first["hierarchy"]),
                    "printed_question_order": printed_order,
                    "independent_choice_section": False,
                    "master_projection_id": master_id,
                    "mapped_atomic_ids": list(unit_ids),
                },
                "minimal_atomic_units": units,
                "theme_chain_role": {
                    "printed_question_order": printed_order,
                    "atomic_ids_in_source_order": list(unit_ids),
                    "shared_material_id": first["theme_context"]["shared_material_id"],
                },
                "dependencies": {
                    unit["hierarchy"]["atomic_part_id"]: deepcopy(unit["dependency"])
                    for unit in units
                },
                "classification": {
                    unit["hierarchy"]["atomic_part_id"]: deepcopy(
                        unit["classification"]
                    )
                    for unit in units
                },
                "difficulty": {
                    "measured": False,
                    "candidate_by_atomic": {
                        unit["hierarchy"]["atomic_part_id"]: deepcopy(
                            unit["difficulty"]
                        )
                        for unit in units
                    },
                },
                "source_identity": deepcopy(first["source_identity"]),
                "answer_boundary": {
                    "availability": "present_part_aligned",
                    "authority": "nonofficial_reference",
                },
                "reference_answer": {
                    "atomic_parts": [
                        {
                            "atomic_part_id": unit["hierarchy"]["atomic_part_id"],
                            "reference_answer_text": unit["answer"][
                                "reference_summary_zh"
                            ],
                            "suggested_points": unit["answer"]["suggested_points"],
                            "points_authority": unit["answer"]["points_authority"],
                        }
                        for unit in units
                    ],
                    "authority": "nonofficial_reference",
                    "verified": False,
                },
                "quality_notes": [
                    note for unit in units for note in unit.get("quality_notes", [])
                ],
                "evidence_descriptors": [],
            }

        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop_manifest.json"
        )
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_bytes: dict[str, bytes] = {}
        forbidden: set[str] = set()
        for crop in self._crop_entries(crop_manifest):
            if not isinstance(crop, dict):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_crop_manifest_drift", "invalid crop binding"
                )
            crop_id = self._crop_id(crop)
            if not crop_id or crop_id in crop_by_id:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_crop_manifest_drift", "crop ID overlap drifted"
                )
            path = self._crop_path(crop)
            raw = self._read_product_file(path)
            expected_hash = crop.get("sha256")
            if not isinstance(expected_hash, str) or _sha256(raw) != expected_hash:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_crop_binding_invalid", "crop hash drifted"
                )
            expected_bytes = crop.get("bytes")
            if expected_bytes is not None and expected_bytes != len(raw):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_crop_binding_invalid", "crop size drifted"
                )
            dimensions = self._png_dimensions(raw)
            claimed_dimensions = _pick(crop, "dimensions", default=None)
            if (
                claimed_dimensions is not None
                and list(dimensions) != claimed_dimensions
            ) or len(raw) > MAX_CROP_BYTES:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_crop_binding_invalid", "crop PNG binding drifted"
                )
            crop_by_id[crop_id] = crop
            crop_bytes[crop_id] = raw

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source_manifest.json"
        )
        source_files = source_manifest.get("files")
        if (
            source_manifest.get("authority") != "nonofficial_wechat_repost"
            or source_manifest.get("official") is not False
            or not isinstance(source_files, list)
        ):
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_source_drift", "source identity binding is absent"
            )
        workspace_root = self.shchem_root.parent.resolve()
        candidate_source_raw: bytes | None = None
        for binding in source_files:
            relative = _safe_relative(binding.get("path"), "source file")
            source_path = workspace_root.joinpath(*relative.parts)
            current = workspace_root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise Fengxian2025Theme2DirectVisualScanError(
                        "fengxian_theme2_path_invalid",
                        "source symlink is forbidden",
                    )
            try:
                resolved = source_path.resolve(strict=True)
            except (FileNotFoundError, OSError) as exc:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_source_drift", "bound source file is missing"
                ) from exc
            if not resolved.is_relative_to(workspace_root) or source_path.is_symlink():
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_path_invalid",
                    "source path escaped or is a symlink",
                )
            raw = resolved.read_bytes()
            if _sha256(raw) != binding.get("sha256") or len(raw) != binding.get(
                "bytes"
            ):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_source_drift", "bound source hash drifted"
                )
            if relative.as_posix().endswith(
                "fengxian_2025_theme2_naclo/question_candidates.jsonl"
            ):
                candidate_source_raw = raw

        if candidate_source_raw is None:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_source_drift",
                "bound upstream question candidate source is absent",
            )
        explicit_evidence_by_question: dict[str, list[dict[str, Any]]] = {}
        explicitly_allowed_crop_ids: set[str] = set()
        for candidate in _jsonl_objects(
            candidate_source_raw, "question_candidates.jsonl"
        ):
            question_id = candidate.get("question_id")
            if not isinstance(question_id, str) or question_id in explicit_evidence_by_question:
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_source_drift",
                    "upstream question identity is absent or duplicated",
                )
            locator = candidate.get("source_locator")
            page_refs = locator.get("page_refs") if isinstance(locator, dict) else None
            if not isinstance(page_refs, list):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_source_drift",
                    "upstream page references are absent",
                )
            descriptors: list[dict[str, Any]] = []
            seen_crop_ids: set[str] = set()
            for page_ref in page_refs:
                if not isinstance(page_ref, dict):
                    raise Fengxian2025Theme2DirectVisualScanError(
                        "fengxian_theme2_source_drift",
                        "upstream page reference is invalid",
                    )
                crop_asset = page_ref.get("crop_asset")
                crop_sha256 = page_ref.get("crop_sha256")
                page_number = page_ref.get("page_number")
                upstream_role = page_ref.get("role")
                if (
                    not isinstance(crop_asset, str)
                    or not isinstance(crop_sha256, str)
                    or type(page_number) is not int
                    or page_number < 1
                    or upstream_role not in {"question", "context", "answer"}
                ):
                    raise Fengxian2025Theme2DirectVisualScanError(
                        "fengxian_theme2_source_drift",
                        "upstream page reference fields are invalid",
                    )
                crop_id = PurePosixPath(crop_asset.replace("\\", "/")).stem
                crop = crop_by_id.get(crop_id)
                copied_from = crop.get("copied_from") if isinstance(crop, dict) else None
                if (
                    crop is None
                    or not isinstance(copied_from, str)
                    or copied_from.removeprefix("sh-chem-db/")
                    != crop_asset.removeprefix("sh-chem-db/")
                    or crop.get("sha256") != crop_sha256
                    or crop_id in seen_crop_ids
                ):
                    raise Fengxian2025Theme2DirectVisualScanError(
                        "fengxian_theme2_source_drift",
                        "upstream crop relationship is not bound exactly by path and hash",
                    )
                seen_crop_ids.add(crop_id)
                if upstream_role == "answer":
                    continue
                evidence_role = (
                    "question" if upstream_role == "question" else "shared_material"
                )
                explicitly_allowed_crop_ids.add(crop_id)
                descriptors.append(
                    {
                        "crop_id": crop_id,
                        "evidence_role": evidence_role,
                        "source_page": page_number,
                    }
                )
            if not any(item["evidence_role"] == "question" for item in descriptors):
                raise Fengxian2025Theme2DirectVisualScanError(
                    "fengxian_theme2_source_drift",
                    "upstream question crop relationship is absent",
                )
            explicit_evidence_by_question[question_id] = descriptors

        expected_question_ids = {
            record["hierarchy"]["printed_question_id"] for record in by_master.values()
        }
        if set(explicit_evidence_by_question) != expected_question_ids:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_source_drift",
                "upstream question relationship set drifted",
            )
        # Preserve every archived binding above. Revise only the presentation
        # bytes of views already authorized by explicit source relationships.
        try:
            for crop_id in explicitly_allowed_crop_ids:
                crop_bytes[crop_id] = recrop_fengxian_view(
                    self.shchem_root, crop_id, crop_bytes[crop_id]
                )
        except SourceCropRevisionError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_recrop_source_drift", str(exc)
            ) from exc
        for grouped_record in by_master.values():
            question_id = grouped_record["hierarchy"]["printed_question_id"]
            atomic_crop_ids = {
                PurePosixPath(crop_ref).stem
                for unit in grouped_record["minimal_atomic_units"]
                for crop_ref in unit["evidence"]["crop_refs"]
            }
            descriptors = []
            for relationship in explicit_evidence_by_question[question_id]:
                crop_id = relationship["crop_id"]
                if crop_id not in atomic_crop_ids:
                    raise Fengxian2025Theme2DirectVisualScanError(
                        "fengxian_theme2_source_drift",
                        "upstream evidence is absent from frozen atomic source evidence",
                    )
                crop = crop_by_id[crop_id]
                raw = crop_bytes[crop_id]
                width, height = self._png_dimensions(raw)
                descriptors.append(
                    {
                        **relationship,
                        "sha256": _sha256(raw),
                        "archived_crop_sha256": crop["sha256"],
                        "bytes": len(raw),
                        "width": width,
                        "height": height,
                    }
                )
            grouped_record["evidence_descriptors"] = descriptors
        forbidden.update(set(crop_by_id) - explicitly_allowed_crop_ids)

        try:
            master_identity = MasterWave1WorkbenchReader(
                self.shchem_root
            ).visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_master_identity_unavailable",
                "verified master470/exact169 identity index is unavailable",
            ) from exc
        master_ids = set(master_identity["master_atomic_ids"])
        exact_ids = set(master_identity["exact_master_ids"])
        projected_ids = set(EXPECTED_MASTER_NODE_IDS)
        if not projected_ids <= master_ids or projected_ids & exact_ids:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_master_identity_conflict",
                "Fengxian projection is absent from master470 or overlaps exact169",
            )

        return _Snapshot(
            manifest=manifest,
            records=records,
            by_master_id=by_master,
            crop_by_id=crop_by_id,
            crop_bytes=crop_bytes,
            forbidden_crop_ids=frozenset(forbidden),
            manifest_file_sha256=_sha256(manifest_raw),
            manifest_self_sha256=actual_self,
            master_crosswalk_manifest_self_sha256=master_identity["integrity"][
                "manifest_self_sha256"
            ],
            output_binding_count=len(outputs),
            source_binding_count=len(source_files),
        )

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_file_sha256": snapshot.manifest_file_sha256,
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "master_crosswalk_manifest_self_sha256": (
                snapshot.master_crosswalk_manifest_self_sha256
            ),
            "output_binding_count": snapshot.output_binding_count,
            "source_binding_count": snapshot.source_binding_count,
            "record_count": len(EXPECTED_MASTER_NODE_IDS),
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
            "path_and_symlink_safety_verified_on_read": True,
            "png_structure_verified_on_read": True,
            "four_level_parent_chain_verified_on_read": True,
            "grouped_master_10_to_atomic_13_verified_on_read": True,
            "candidate_gate_closure_verified_on_read": True,
            "presentation_revision_id": REVISION_ID,
            "presentation_recipe_sha256": RECIPE_SHA256,
        }

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        safe = len(snapshot.crop_by_id) - len(snapshot.forbidden_crop_ids)
        return {
            "product_id": PRODUCT_ID,
            "scope": SCOPE,
            "paper_id": PAPER_ID,
            "theme_id": THEME_ID,
            "counts": {
                "expected_themes": 1,
                "expected_printed_questions": 10,
                "master_projection_nodes": 10,
                "minimal_atomic_units": 13,
                "scan_records": len(snapshot.records),
                "question_or_shared_crop_bindings": safe,
                "forbidden_crop_bindings": len(snapshot.forbidden_crop_ids),
                "total_exact_crop_bindings": len(snapshot.crop_by_id),
            },
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def catalog(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        items = []
        for master_id in EXPECTED_MASTER_NODE_IDS:
            record = snapshot.by_master_id[master_id]
            hierarchy = record["hierarchy"]
            units = self._atomic_units(record)
            answer = record.get("answer_boundary") or record.get("answer") or {}
            quality = _pick(record, "quality_notes", "quality_note", default=[])
            evidence = self._safe_evidence(record)
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
                    "corrected_fields": [],
                    "source_year": 2025,
                    "source_region_or_school": "奉贤区（转载归档标题；卷首身份待独立核验）",
                    "source_paper_type": "二模（转载归档标题；卷首身份待独立核验）",
                    "availability": answer.get("availability", "unknown"),
                    "source_authority": "nonofficial_reference",
                    "printed_question_id": hierarchy.get("printed_question_id"),
                    "printed_question_order": hierarchy.get("printed_question_order"),
                    "minimal_atomic_unit_count": len(units),
                    "minimal_atomic_unit_ids": [
                        self._atomic_id(unit) for unit in units
                    ],
                    "answer_availability": answer.get("availability", "unknown"),
                    "answer_authority": answer.get(
                        "authority", "nonofficial_reference"
                    ),
                    "has_quality_note": bool(quality),
                    "candidate_only": True,
                }
            )
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "count": len(items),
            "minimal_atomic_unit_count": sum(
                item["minimal_atomic_unit_count"] for item in items
            ),
            "master_node_ids": list(EXPECTED_MASTER_NODE_IDS),
            "items": items,
            "coverage": {
                "master_atomic_inventory": 470,
                "wave1_exact_visual_scanned": 169,
                "direct_master_visual_scanned": 10,
                "visual_scanned_master_atomic": 179,
                "remaining_unscanned": 291,
                "direct_exact_overlap": 0,
            },
            "paper_identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    @staticmethod
    def _safe_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
        evidence = _pick(record, "evidence_descriptors", "viewed_evidence", default=[])
        result = []
        if isinstance(evidence, list):
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                role = _pick(item, "evidence_role", "role")
                if role not in {"question", "shared_material"}:
                    continue
                result.append(
                    {
                        "crop_id": item.get("crop_id"),
                        "evidence_role": role,
                        "sha256": item.get("sha256"),
                        "bytes": item.get("bytes"),
                        "width": item.get("width"),
                        "height": item.get("height"),
                        "source_page": item.get("source_page"),
                        "archived_crop_sha256": item.get("archived_crop_sha256"),
                        "presentation_revision_id": REVISION_ID,
                        "visual_inspection_status": (
                            "source_page_and_repaired_crop_actually_viewed_by_primary_model_2026-09-09"
                        ),
                        "content_type": "image/png",
                        "access": "teacher_loopback_read_only",
                    }
                )
        return result

    def detail(self, master_node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except SecurityError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_invalid_identifier", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_node_not_found",
                "node has no grouped Fengxian scan",
                404,
            )
        units = []
        for raw_unit in self._atomic_units(record):
            classification = deepcopy(raw_unit.get("classification", {}))
            if isinstance(classification, dict):
                primary = classification.get("primary_K")
                supporting = classification.get("supporting_K", [])
                classification["K"] = [primary, *supporting]
            raw_answer = raw_unit.get("answer", {})
            unit = {
                "atomic_part_id": self._atomic_id(raw_unit),
                "hierarchy": deepcopy(raw_unit.get("hierarchy", {})),
                "visible_summary_zh": raw_unit.get("prompt", {}).get(
                    "normalized_text_zh"
                ),
                "response_requirement_zh": raw_unit.get("prompt", {}).get(
                    "normalized_text_zh"
                ),
                "theme_chain_role": {
                    "shared_material_id": raw_unit.get("theme_context", {}).get(
                        "shared_material_id"
                    ),
                    "atomic_sequence_in_printed": raw_unit.get("hierarchy", {}).get(
                        "atomic_sequence_in_printed"
                    ),
                },
                "dependency": deepcopy(raw_unit.get("dependency", {})),
                "classification": classification,
                "cognitive_difficulty": deepcopy(raw_unit.get("difficulty", {})),
                "answer_boundary": {
                    "availability": raw_answer.get("availability", "unknown"),
                    "authority": raw_answer.get("authority", "nonofficial_reference"),
                    "verified": False,
                    "independently_verified": False,
                    "official_answer_claim_allowed": False,
                    "official_scoring_claim_allowed": False,
                },
                "reference_answer": {
                    "reference_answer_text": raw_answer.get("reference_summary_zh"),
                    "suggested_points": raw_answer.get("suggested_points"),
                    "points_authority": raw_answer.get("points_authority"),
                    "authority": "nonofficial_reference",
                    "verified": False,
                },
                "quality_notes": deepcopy(raw_unit.get("quality_notes", [])),
                "candidate_only": True,
            }
            units.append(unit)
        answer = deepcopy(record.get("answer_boundary") or record.get("answer") or {})
        answer.update(
            {
                "authority": answer.get("authority", "nonofficial_reference"),
                "verified": False,
                "independently_verified": False,
                "official_answer_claim_allowed": False,
                "official_scoring_claim_allowed": False,
            }
        )
        primary_unit = units[0]
        visible_summary = "；".join(
            str(unit["visible_summary_zh"]) for unit in units
        )
        dependency_map = deepcopy(
            _pick(record, "dependencies", "dependency", default={})
        )
        shared_crop_ids = [
            item["crop_id"]
            for item in self._safe_evidence(record)
            if item["evidence_role"] == "shared_material"
        ]
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": f"FX2025-T2-{master_node_id}",
            "scan_status": "visual_scan_completed",
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "visible_summary_zh": visible_summary,
            "response_requirement_zh": visible_summary,
            "minimal_atomic_units": units,
            "theme_chain_role": {
                "role_zh": "按印刷小题投影读取，并保留全部最小作答单元",
                "candidate_only": True,
            },
            "dependency": {
                "analysis_zh": "逐最小作答单元保存依赖；本字段仅为投影首单元兼容摘要。",
                "prior_atomic_part_ids": deepcopy(
                    primary_unit["dependency"].get("prior_atomic_part_ids", [])
                ),
                "shared_material_crop_ids": shared_crop_ids,
            },
            "dependencies": dependency_map,
            "scan_classification": {
                key: deepcopy(primary_unit["classification"][key])
                for key in (
                    "item_type",
                    "selection_rule",
                    "primary_K",
                    "supporting_K",
                    "A",
                    "C",
                    "R",
                    "RP",
                )
            },
            "classification": deepcopy(record.get("classification", {})),
            "cognitive_difficulty": deepcopy(record.get("difficulty", {})),
            "source_identity": deepcopy(record.get("source_identity", {})),
            "answer_boundary": answer,
            "reference_answer": deepcopy(record.get("reference_answer", {})),
            "quality_notes": deepcopy(
                _pick(record, "quality_notes", "quality_note", default=[])
            ),
            "evidence_descriptors": self._safe_evidence(record),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
        try:
            validate_identifier(master_node_id, "master_node_id")
            validate_identifier(crop_id, "crop_id")
        except SecurityError as exc:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_node_not_found",
                "node has no grouped Fengxian scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        if crop is None:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_crop_not_found", "crop is not registered", 404
            )
        if crop_id in snapshot.forbidden_crop_ids:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_crop_role_denied",
                "answer, boundary, identity, and QA crops are forbidden",
                403,
            )
        allowed = {
            item.get("crop_id")
            for item in self._safe_evidence(record)
            if item.get("evidence_role") in {"question", "shared_material"}
        }
        if crop_id not in allowed:
            raise Fengxian2025Theme2DirectVisualScanError(
                "fengxian_theme2_crop_not_found",
                "crop is not evidence for this master node",
                404,
            )
        raw = snapshot.crop_bytes[crop_id]
        return CandidateCropPayload(data=raw, sha256=_sha256(raw))


__all__ = [
    "AUTHORITY",
    "EXPECTED_EFFECTIVE_ATOMIC_IDS",
    "EXPECTED_GROUPED_ATOMIC_IDS",
    "EXPECTED_MANIFEST_BYTES",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MASTER_NODE_IDS",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "THEME_ID",
    "Fengxian2025Theme2DirectVisualScanError",
    "Fengxian2025Theme2DirectVisualScanReader",
]
