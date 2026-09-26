from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import (
    MAX_CROP_BYTES,
    MasterDirectVisualScanError,
)
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .pudong2026_first_mock_theme_direct_visual_scan import (
    Pudong2026FirstMockThemeDirectVisualScanReader,
    _is_reparse,
    _json_object,
    _jsonl_objects,
    _safe_relative,
    _sha256,
    _Snapshot,
)
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier

PRODUCT_ID = "QVS-HP2025-SECOND-MOCK-T4-CALCIUM-IODATE-V1"
BATCH_ID = "BATCH-HP2025-SECOND-MOCK-T4-CALCIUM-IODATE-20260826"
PAPER_ID = "PAPER-6187e12d404b0d5dc54c"
THEME_ID = "THEME-9dcb346c7fc55289c209"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_huangpu_2025_second_mock_theme4_"
    "calcium_iodate_v1_2026-08-26"
)
PRODUCT_RELATIVE_POSIX = PRODUCT_RELATIVE.as_posix()
SCOPE = "candidate_only_read_only_master_direct_visual_scan"

EXPECTED_MANIFEST_BYTES = 2_947
EXPECTED_MANIFEST_FILE_SHA256 = (
    "9858f3b7d4abf451a6040389e22fe1e9c27cb0ffdf1f5b2b513e37e7693d1721"
)
EXPECTED_MANIFEST_SELF_SHA256 = (
    "b228d06f55dd0fdc72c199c9f1025456fed45a8218dfae66a96714be5fd41482"
)
EXPECTED_RECORDS_SHA256 = (
    "31a496b9c0324a4df65f418cef9fd46a7f5cb86b1891477e62a58c6a22156fbe"
)
EXPECTED_SCHEMA_SHA256 = (
    "14514f2b736260baed0ad315926407cd3b51b19d36fe7c8dae00fae5c6d78789"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "6820c838efa88eba7468c5c7116ac73b663cfa305623500e4810ece81222e41e"
)
EXPECTED_CROP_MANIFEST_SHA256 = (
    "562e799f7858dcace64ffdcecc918cf07e3cdd9d90b6b48009ee71e4b84d7b35"
)
EXPECTED_THEME_SUMMARY_SHA256 = (
    "ca88c5fcda6f7ae8c9b5b54b37ff2d66abb90e8a9de0d347ed5a6eb244dc36e9"
)
EXPECTED_VALIDATION_REPORT_SHA256 = (
    "06fc381a758b1b2de14885dba71f15c7b4855f49f067a7268e72b65f21af0b51"
)
EXPECTED_MUTATION_REPORT_SHA256 = (
    "8cac9adac404a0c3d5ffb1630dd9b2ff554c6695d954d51990bd1021d3ae8d44"
)
EXPECTED_OUTPUT_BINDING_COUNT = 47
EXPECTED_SOURCE_BINDING_COUNT = 28

EXPECTED_PRINTED_IDS = tuple(f"HP2025-EM-S4-Q{index}" for index in range(1, 10))
EXPECTED_ATOMIC_IDS = (
    "HP2025-EM-S4-Q1-P1",
    "HP2025-EM-S4-Q1-P2",
    "HP2025-EM-S4-Q2-P1",
    "HP2025-EM-S4-Q3-P1",
    "HP2025-EM-S4-Q4-P1",
    "HP2025-EM-S4-Q5-P1",
    "HP2025-EM-S4-Q6-P1",
    "HP2025-EM-S4-Q7-P1",
    "HP2025-EM-S4-Q7-P2",
    "HP2025-EM-S4-Q8-P1",
    "HP2025-EM-S4-Q9-P1",
)
EXPECTED_DEPENDENCIES = {
    "HP2025-EM-S4-Q1-P2": ["HP2025-EM-S4-Q1-P1"],
    "HP2025-EM-S4-Q7-P2": ["HP2025-EM-S4-Q7-P1"],
}
EXPECTED_QUALITY_IDS = {
    "HP2025-EM-S4-Q1-P2",
    "HP2025-EM-S4-Q2-P1",
    "HP2025-EM-S4-Q6-P1",
    "HP2025-EM-S4-Q7-P2",
}
EXPECTED_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "chemistry_correctness_verified",
        "difficulty_verified",
        "generation_allowed",
        "human_chemistry_reviewed",
        "human_reviewed",
        "human_taxonomy_reviewed",
        "measured_difficulty_verified",
        "official",
        "official_answer_claim_allowed",
        "pixel_reuse_allowed",
        "publication_allowed",
        "retrieval_allowed",
        "retrieval_ready",
        "teaching_use_allowed",
    }
)
EXPECTED_GATES = {key: False for key in EXPECTED_GATE_KEYS}
AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "retrieval_allowed": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "rubric_verified": False,
    "measured_difficulty_verified": False,
    "pixel_reuse_allowed": False,
}
PAPER_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": "2025年上海市黄浦区高三化学二模试卷",
    "paper_face_date_literal": "2025年4月",
    "year": 2025,
    "region_label": "黄浦区（卷面直接身份）",
    "paper_family": "second_mock_paper_face_verified",
    "paper_type": "二模",
    "covered_scope": "主题四（碘酸钙的制备）",
    "coverage_scope": "theme_4_calcium_iodate_only_11_atomic_not_complete_paper",
    "complete_paper_visual_scan_claim_allowed": False,
    "official_identity_claim_allowed": False,
    "official_status": "nonofficial_source_bundle",
    "source_account": "上海初高中化学",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
}
_EXPECTED_MANIFEST_GATES = {
    "human_reviewed": False,
    "answer_verified": False,
    "retrieval_ready": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "formal_promotion_allowed": False,
}
_FACTOR_IDS = {
    "information_transformations",
    "reasoning_chain_steps",
    "knowledge_module_span",
    "representation_switches",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_density",
    "prior_dependency",
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PAGE_NUMBER = re.compile(r"(?:试卷|答案)-page-(\d+)\.png\Z")


class Huangpu2025Theme4DirectVisualScanError(MasterDirectVisualScanError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _manifest_self_hash(manifest: dict[str, Any]) -> str:
    value = deepcopy(manifest)
    value.pop("self_sha256", None)
    return _sha256(_canonical_bytes(value))


def _record_self_hash(record: dict[str, Any]) -> str:
    value = deepcopy(record)
    value.pop("evidence_binding_sha256", None)
    return _sha256(_canonical_bytes(value))


def _manifest_bindings(
    value: Any, label: str, expected_count: int
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise Huangpu2025Theme4DirectVisualScanError(
            "master_direct_scan_binding_invalid", f"{label} count drifted"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} shape drifted"
            )
        relative = _safe_relative(item.get("path"))
        digest = item.get("sha256")
        if (
            relative in result
            or not isinstance(digest, str)
            or _HEX64.fullmatch(digest) is None
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_binding_invalid", f"{label} value drifted"
            )
        result[relative] = dict(item)
    return result


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Huangpu2025Theme4DirectVisualScanReader(
    Pudong2026FirstMockThemeDirectVisualScanReader
):
    """Strict read-only adapter for the frozen Huangpu theme-four product."""

    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_path_invalid", "product root leaves sh-chem-db"
            )
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    @staticmethod
    def _false_gates(value: Any, label: str) -> None:
        if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_gate_elevated", f"{label} gates drifted"
            )

    @staticmethod
    def _source_page(asset: dict[str, Any]) -> int:
        source_asset = asset.get("source_asset")
        match = _PAGE_NUMBER.search(source_asset or "")
        if match is None:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid",
                "an exposable crop lacks a source page",
            )
        return int(match.group(1))

    def _verified_source_path(self, relative: str) -> Path:
        relative = _safe_relative(relative)
        root = (
            self.shchem_root.parent
            if relative.startswith("课本/")
            else self.shchem_root
        )
        cursor = root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink() or _is_reparse(cursor):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_symlink_denied",
                    "source bindings may not traverse links or reparse points",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_file_missing", "a source binding is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_path_invalid", "a source binding leaves its root"
            )
        return resolved

    def _verify_source_binding(self, item: Any) -> None:
        if not isinstance(item, dict) or set(item) - {
            "path",
            "sha256",
            "dimensions",
            "original_preserved",
        }:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_source_invalid", "source binding shape drifted"
            )
        digest = item.get("sha256")
        if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_source_invalid", "source hash binding drifted"
            )
        try:
            actual = _stream_sha256(self._verified_source_path(item.get("path")))
        except OSError as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_file_read_failed", "a source binding is unreadable"
            ) from exc
        if actual != digest:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_binding_mismatch", "source hash binding drifted"
            )

    @staticmethod
    def _safe_source_identity(record: dict[str, Any]) -> dict[str, Any]:
        source = record["source_identity"]
        return {
            key: deepcopy(source.get(key))
            for key in (
                "year",
                "region_or_school",
                "paper_type",
                "paper_face_title_literal",
                "paper_face_date_literal",
                "duration_minutes",
                "total_score",
                "source_layer",
                "official_status",
                "article_title_literal",
                "article_account",
                "article_published_at",
                "identity_separation_note",
            )
        }

    @staticmethod
    def _textbook_projection(record: dict[str, Any]) -> dict[str, Any]:
        mapping = record["textbook_mapping"]
        entries = []
        for item in mapping["entries"]:
            entries.append(
                {
                    key: deepcopy(item.get(key))
                    for key in (
                        "knowledge_tag",
                        "knowledge_role",
                        "status",
                        "relation",
                        "textbook_family",
                        "publisher",
                        "volume_id",
                        "volume_title",
                        "chapter_id",
                        "chapter_title",
                        "section_id",
                        "section_number",
                        "section_title",
                        "unit_id",
                        "unit_title",
                        "unit_status",
                        "edition_status",
                        "evidence_level",
                        "blocker_or_note",
                    )
                }
            )
        return {
            "schema_version": "1.0.0-reader-safe-directory-projection",
            "mapping_status": mapping["mapping_status"],
            "entries": entries,
            "authority_boundary": {
                "directory_mapping_candidate_only": True,
                "edition_or_printing_verified": False,
                "unit_verified": False,
                "human_taxonomy_reviewed": False,
                "local_paths_or_hashes_exposed": False,
            },
        }

    @staticmethod
    def _reference_answer(record: dict[str, Any]) -> dict[str, Any]:
        known_code = (
            "source_quality_note" if record["answer"].get("quality_note") else None
        )
        return project_reference_answer(record["answer"], None, known_code)

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if (
            len(manifest_raw) != EXPECTED_MANIFEST_BYTES
            or _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        if (
            manifest.get("schema_version") != "1.0.0"
            or manifest.get("product_id") != PRODUCT_ID
            or manifest.get("batch_id") != BATCH_ID
            or manifest.get("counts")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": 9,
                "atomic_part": 11,
                "crop_assets": 33,
            }
            or manifest.get("ownership")
            != {
                "scope": PRODUCT_RELATIVE_POSIX,
                "central_master_mutated": False,
                "forbidden_parallel_paths_touched": False,
            }
            or manifest.get("gates") != _EXPECTED_MANIFEST_GATES
            or manifest.get("self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or _manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest contract drifted"
            )
        outputs = _manifest_bindings(manifest.get("outputs"), "output", 10)
        scripts = _manifest_bindings(manifest.get("scripts"), "script", 4)
        expected_principal = {
            "scan_record_schema.json": EXPECTED_SCHEMA_SHA256,
            "scan_records.jsonl": EXPECTED_RECORDS_SHA256,
            "crop_manifest.json": EXPECTED_CROP_MANIFEST_SHA256,
            "source_manifest.json": EXPECTED_SOURCE_MANIFEST_SHA256,
            "theme_summary.json": EXPECTED_THEME_SUMMARY_SHA256,
        }
        if any(
            outputs.get(path, {}).get("sha256") != digest
            for path, digest in expected_principal.items()
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_binding_mismatch",
                "principal output binding drifted",
            )
        output_bytes: dict[str, bytes] = {}
        for label, bindings in (("output", outputs), ("script", scripts)):
            for relative, binding in bindings.items():
                _, raw = self._verified_file(self.product_root, relative)
                if _sha256(raw) != binding["sha256"]:
                    raise Huangpu2025Theme4DirectVisualScanError(
                        "master_direct_scan_binding_mismatch",
                        f"{label} hash binding drifted",
                    )
                output_bytes[relative] = raw
        for relative, digest in (
            ("validation_report.json", EXPECTED_VALIDATION_REPORT_SHA256),
            ("mutation_test_report.json", EXPECTED_MUTATION_REPORT_SHA256),
        ):
            _, raw = self._verified_file(self.product_root, relative)
            if _sha256(raw) != digest:
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_binding_mismatch", "QA report binding drifted"
                )
            output_bytes[relative] = raw

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        if (
            source_manifest.get("batch_id") != BATCH_ID
            or source_manifest.get("paper_face_identity")
            != {
                "title": PAPER_IDENTITY_BOUNDARY["paper_face_title_literal"],
                "date_literal": "2025年4月",
                "duration_minutes": 60,
                "total_score": 100,
                "authority": "paper_face_pixels",
            }
            or source_manifest.get("answer_authority")
            != "nonofficial_reposted_reference; direct_adoption_without_independent_solution_verification"
            or source_manifest.get("article_identity", {}).get("authority")
            != "article_metadata_not_paper_identity"
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_source_invalid", "source authority boundary drifted"
            )
        source_groups = (
            source_manifest.get("source_assets"),
            source_manifest.get("protected_inputs"),
            source_manifest.get("textbook_sources"),
        )
        if (
            any(not isinstance(group, list) for group in source_groups)
            or sum(len(group) for group in source_groups)
            != EXPECTED_SOURCE_BINDING_COUNT
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_source_invalid", "source binding count drifted"
            )
        for group in source_groups:
            for binding in group:
                self._verify_source_binding(binding)

        schema = _json_object(output_bytes["scan_record_schema.json"], "schema")
        try:
            Draft202012Validator.check_schema(schema)
            schema_validator = Draft202012Validator(schema)
        except Exception as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_schema_invalid", "bound schema is invalid"
            ) from exc
        raw_records = _jsonl_objects(output_bytes["scan_records.jsonl"], "records")
        if len(raw_records) != 11:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count drifted"
            )
        for index, record in enumerate(raw_records, 1):
            if next(schema_validator.iter_errors(record), None) is not None:
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} failed its bound schema",
                )

        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop manifest"
        )
        crop_assets = crop_manifest.get("assets")
        if (
            crop_manifest.get("batch_id") != BATCH_ID
            or crop_manifest.get("crop_count") != 33
            or not isinstance(crop_assets, list)
            or len(crop_assets) != 33
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop count drifted"
            )
        crop_by_path: dict[str, dict[str, Any]] = {}
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_bytes_by_path: dict[str, bytes] = {}
        crop_bindings: dict[str, dict[str, Any]] = {}
        crop_id_by_path: dict[str, str] = {}
        for index, asset in enumerate(crop_assets, 1):
            if not isinstance(asset, dict) or set(asset) != {
                "asset",
                "sha256",
                "dimensions",
                "role",
                "source_asset",
                "crop_box_xywh",
                "derivation",
                "copyright_use",
            }:
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop shape drifted"
                )
            stored = _safe_relative(asset.get("asset"))
            prefix = PRODUCT_RELATIVE_POSIX + "/"
            if not stored.startswith(prefix) or stored in crop_by_path:
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop path drifted"
                )
            relative = stored[len(prefix) :]
            _, raw = self._verified_file(self.product_root, relative)
            dimensions = asset.get("dimensions")
            if (
                not isinstance(asset.get("sha256"), str)
                or _HEX64.fullmatch(asset["sha256"]) is None
                or _sha256(raw) != asset["sha256"]
                or not isinstance(dimensions, list)
                or len(dimensions) != 2
                or self._png_dimensions(raw) != tuple(dimensions)
                or len(raw) > MAX_CROP_BYTES
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop hash, dimensions, or size drifted",
                )
            crop_id = f"HP2025-T4-CROP-{index:02d}"
            crop_id_by_path[stored] = crop_id
            crop_by_path[stored] = asset
            crop_bytes_by_path[stored] = raw
            crop_by_id[crop_id] = {
                "crop_id": crop_id,
                "stored_path": stored,
                "role": asset["role"],
                "sha256": asset["sha256"],
                "bytes": len(raw),
                "width": dimensions[0],
                "height": dimensions[1],
            }
            crop_bindings[stored] = {
                "sha256": asset["sha256"],
                "bytes": len(raw),
            }

        expected_tree = (
            set(outputs)
            | set(scripts)
            | {
                "validation_report.json",
                "mutation_test_report.json",
                *(f"crops/{Path(path).name}" for path in crop_by_path),
            }
        )
        try:
            actual_tree = {
                path.relative_to(self.product_root).as_posix()
                for path in self.product_root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and ".pytest_cache" not in path.parts
                and path.name != "manifest.json"
            }
        except OSError as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_file_read_failed", "product tree is unreadable"
            ) from exc
        if actual_tree != expected_tree:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_binding_invalid", "product file closure drifted"
            )

        try:
            master_snapshot = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity is unavailable",
            ) from exc
        master_ids = {
            str(row["atomic_part_id"])
            for row in master_snapshot.master_layers["atomic_part"]
        }
        exact_ids: set[str] = set()
        for master_id, relations in master_snapshot.relations_by_master.items():
            if len(relations) != 1:
                continue
            relation = relations[0]
            if (
                relation.get("relation_type") == "exact_1_to_1"
                and relation.get("identity_mapping_allowed") is True
                and relation.get("endpoint_cardinality")
                == {
                    "master_anchor_endpoints_for_strong_key": 0,
                    "master_atomic_endpoints_for_strong_key": 1,
                    "wave_rows_for_strong_key": 1,
                }
            ):
                exact_ids.add(master_id)
        if (
            len(master_ids) != 470
            or len(exact_ids) != 169
            or not set(EXPECTED_ATOMIC_IDS) <= master_ids
            or set(EXPECTED_ATOMIC_IDS) & exact_ids
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "Huangpu theme rows are absent from master470 or overlap exact169",
            )

        safe_question_paths: set[str] = set()
        shared_paths: set[str] = set()
        answer_paths: set[str] = set()
        for record in raw_records:
            evidence = record.get("evidence", {})
            safe_question_paths.update(evidence.get("question_crop_refs", []))
            shared_paths.update(evidence.get("shared_material_refs", []))
            answer_paths.update(evidence.get("answer_crop_refs", []))
        if (
            safe_question_paths & answer_paths
            or not safe_question_paths <= set(crop_by_path)
            or not shared_paths <= safe_question_paths
            or not answer_paths <= set(crop_by_path)
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_role_invalid", "crop role boundary drifted"
            )
        for stored, crop in crop_by_path.items():
            crop_id = crop_id_by_path[stored]
            crop_by_id[crop_id]["category"] = (
                "shared_material"
                if stored in shared_paths
                else "question"
                if stored in safe_question_paths
                else "forbidden"
            )
            if stored in safe_question_paths:
                crop_by_id[crop_id]["source_page"] = self._source_page(crop)

        records: list[dict[str, Any]] = []
        by_master_id: dict[str, dict[str, Any]] = {}
        corrected_fields: dict[str, tuple[str, ...]] = {}
        quality_ids: set[str] = set()
        seen_ids: set[str] = set()
        printed_ids: list[str] = []
        dependency_counts: Counter[str] = Counter()
        for expected_id, raw in zip(EXPECTED_ATOMIC_IDS, raw_records, strict=True):
            hierarchy = raw.get("hierarchy")
            source = raw.get("source_identity")
            answer = raw.get("answer")
            classification = raw.get("classification")
            difficulty = raw.get("difficulty")
            dependency = raw.get("dependency")
            evidence = raw.get("evidence")
            textbook = raw.get("textbook_mapping")
            if (
                not isinstance(hierarchy, dict)
                or hierarchy.get("atomic_part_id") != expected_id
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("theme_big_question_id") != THEME_ID
                or hierarchy.get("theme_order") != 4
                or hierarchy.get("theme_title") != "四、碘酸钙的制备"
                or hierarchy.get("independent_choice_section") is not False
                or expected_id in seen_ids
                or raw.get("record_index") != len(records) + 1
                or raw.get("evidence_binding_sha256") != _record_self_hash(raw)
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record identity drifted"
                )
            printed_id = hierarchy.get("printed_question_id")
            printed_order = hierarchy.get("printed_question_order")
            atomic_order = hierarchy.get("atomic_part_order")
            expected_printed = f"HP2025-EM-S4-Q{printed_order}"
            if (
                printed_id != expected_printed
                or printed_id not in EXPECTED_PRINTED_IDS
                or type(printed_order) is not int
                or type(atomic_order) is not int
                or atomic_order not in {1, 2}
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_identity_conflict", "printed hierarchy drifted"
                )
            if printed_id not in printed_ids:
                printed_ids.append(printed_id)
            atom = master_snapshot.master_nodes.get(("atomic_part", expected_id))
            if (
                not isinstance(atom, dict)
                or atom.get("parent_paper_id") != PAPER_ID
                or atom.get("parent_theme_big_question_id") != THEME_ID
                or atom.get("parent_printed_question_id") != printed_id
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_master_identity_conflict",
                    "record parent chain disagrees with Master",
                )
            if (
                not isinstance(source, dict)
                or source.get("year") != 2025
                or source.get("region_or_school") != "黄浦区"
                or source.get("paper_type") != "二模"
                or source.get("paper_face_title_literal")
                != PAPER_IDENTITY_BOUNDARY["paper_face_title_literal"]
                or source.get("official_status") != "nonofficial_source_bundle"
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_source_invalid",
                    "record source identity drifted",
                )
            self._false_gates(raw.get("authority_gates"), expected_id)
            if (
                not isinstance(answer, dict)
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("source_authority") != "nonofficial_reposted_reference"
                or answer.get("independently_verified") is not False
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not answer["reference_summary_zh"]
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer boundary drifted"
                )
            has_quality = bool(answer.get("quality_note"))
            if has_quality:
                quality_ids.add(expected_id)
            if (
                not isinstance(classification, dict)
                or not isinstance(classification.get("item_type"), str)
                or classification.get("selection_rule") != "not_applicable"
                or not isinstance(classification.get("primary_K"), str)
                or any(
                    not isinstance(classification.get(axis), list)
                    or any(
                        not isinstance(value, str) or not value
                        for value in classification[axis]
                    )
                    for axis in ("supporting_K", "A", "C", "R", "RP")
                )
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_classification_invalid",
                    "classification shape drifted",
                )
            factors = (
                difficulty.get("factors") if isinstance(difficulty, dict) else None
            )
            if (
                not isinstance(factors, list)
                or len(factors) != 10
                or {factor.get("dimension_id") for factor in factors} != _FACTOR_IDS
                or difficulty.get("candidate_label") not in {"D1", "D2", "D3"}
                or difficulty.get("human_verified") is not False
                or difficulty.get("is_measured_difficulty") is not False
                or difficulty.get("measured") is not None
                or difficulty.get("student_data_used") is not False
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_difficulty_invalid",
                    "difficulty boundary drifted",
                )
            prior = (
                dependency.get("depends_on_atomic_parts")
                if isinstance(dependency, dict)
                else None
            )
            if prior != EXPECTED_DEPENDENCIES.get(expected_id, []):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency edge drifted"
                )
            if any(value not in seen_ids for value in prior):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_dependency_invalid",
                    "dependency is not an earlier theme atomic",
                )
            dependency_counts[
                "one_prior_part" if prior else "shared_theme_context_only"
            ] += 1
            if (
                not isinstance(evidence, dict)
                or not evidence.get("question_crop_refs")
                or not evidence.get("answer_crop_refs")
                or evidence.get("question_crop_hashes")
                != [
                    crop_by_path[path]["sha256"]
                    for path in evidence["question_crop_refs"]
                ]
                or evidence.get("answer_crop_hashes")
                != [
                    crop_by_path[path]["sha256"]
                    for path in evidence["answer_crop_refs"]
                ]
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "record crop binding drifted"
                )
            if (
                not isinstance(textbook, dict)
                or textbook.get("mapping_status")
                not in {"complete_directory_level_unit_unknown", "partial_blocked"}
                or not isinstance(textbook.get("entries"), list)
                or not textbook["entries"]
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_textbook_invalid", "textbook mapping drifted"
                )
            knowledge_pairs = [
                (classification["primary_K"], "primary"),
                *[(value, "supporting") for value in classification["supporting_K"]],
            ]
            if [
                (entry.get("knowledge_tag"), entry.get("knowledge_role"))
                for entry in textbook["entries"]
            ] != knowledge_pairs or any(
                entry.get("edition_status") != "unknown_not_externally_verified"
                or entry.get("unit_id") is not None
                or entry.get("unit_title") is not None
                or entry.get("unit_status")
                != "unknown_no_verified_atomic_unit_registry"
                for entry in textbook["entries"]
            ):
                raise Huangpu2025Theme4DirectVisualScanError(
                    "master_direct_scan_textbook_invalid",
                    "textbook authority or K closure drifted",
                )

            viewed_evidence: list[dict[str, Any]] = []
            shared = set(evidence.get("shared_material_refs", []))
            for stored in evidence["question_crop_refs"]:
                asset = crop_by_path[stored]
                crop_id = crop_id_by_path[stored]
                viewed_evidence.append(
                    {
                        "crop_id": crop_id,
                        "evidence_role": (
                            "shared_material" if stored in shared else "question"
                        ),
                        "source_page": self._source_page(asset),
                        "sha256": asset["sha256"],
                        "bytes": len(crop_bytes_by_path[stored]),
                        "width": asset["dimensions"][0],
                        "height": asset["dimensions"][1],
                    }
                )
            normalized_factors = []
            for factor in factors:
                refs = factor.get("evidence_refs")
                if not isinstance(refs, list) or any(
                    path not in evidence["question_crop_refs"] for path in refs
                ):
                    raise Huangpu2025Theme4DirectVisualScanError(
                        "master_direct_scan_difficulty_invalid",
                        "difficulty evidence leaves question pixels",
                    )
                normalized_factors.append(
                    {
                        "dimension_id": factor["dimension_id"],
                        "value": deepcopy(factor.get("value")),
                        "basis": factor.get("basis"),
                        "evidence_crop_ids": [crop_id_by_path[path] for path in refs],
                    }
                )
            normalized = {
                "scan_id": f"HP2025-T4-{expected_id}",
                "scan_status": "visual_scan_completed",
                "visual_scan_completed": True,
                "hierarchy": {
                    "paper_id": PAPER_ID,
                    "theme_id": THEME_ID,
                    "theme_sequence": 4,
                    "theme_title": "四、碘酸钙的制备",
                    "printed_question_id": printed_id,
                    "printed_sequence": printed_order,
                    "atomic_part_id": expected_id,
                    "atomic_sequence_in_printed": atomic_order,
                    "independent_choice_section": False,
                },
                "source_identity": self._safe_source_identity(raw),
                "identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
                "visible_summary_zh": raw["prompt"]["normalized_task_zh"],
                "response_requirement_zh": "、".join(
                    classification["response_form_zh"]
                ),
                "theme_chain_role": {
                    "role_zh": (
                        "承接前序作答单元" if prior else "使用主题共享材料独立作答"
                    ),
                    "candidate_only": True,
                },
                "viewed_evidence": viewed_evidence,
                "classification": deepcopy(classification),
                "difficulty": {
                    "cognitive_prelabel": difficulty["candidate_label"],
                    "candidate_label": difficulty["candidate_label"],
                    "status": difficulty["status"],
                    "human_verified": False,
                    "is_measured_difficulty": False,
                    "measured": None,
                    "student_data_used": False,
                    "factors": normalized_factors,
                },
                "dependency": {
                    "analysis_zh": dependency["analysis_zh"],
                    "prior_atomic_part_ids": list(prior),
                    "shared_material_crop_ids": [
                        crop_id_by_path[path]
                        for path in evidence.get("shared_material_refs", [])
                    ],
                },
                "answer": deepcopy(answer),
                "comparison_with_master": deepcopy(raw["comparison_with_master"]),
                "textbook_mapping": deepcopy(textbook),
                "authority_gates": deepcopy(raw["authority_gates"]),
            }
            corrected_fields[expected_id] = (
                ("item_type",)
                if raw["comparison_with_master"].get("item_type_change")
                == "unknown_to_reasoned_explanation"
                else ()
            )
            seen_ids.add(expected_id)
            records.append(normalized)
            by_master_id[expected_id] = normalized

        if (
            tuple(printed_ids) != EXPECTED_PRINTED_IDS
            or quality_ids != EXPECTED_QUALITY_IDS
            or dependency_counts
            != Counter({"shared_theme_context_only": 9, "one_prior_part": 2})
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_count_mismatch", "derived theme closure drifted"
            )
        theme_summary = _json_object(
            output_bytes["theme_summary.json"], "theme summary"
        )
        if (
            theme_summary.get("counts")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": 9,
                "atomic_part": 11,
            }
            or theme_summary.get("hierarchy", {}).get("paper_id") != PAPER_ID
            or theme_summary.get("hierarchy", {}).get("theme_big_question_id")
            != THEME_ID
            or theme_summary.get("hierarchy", {}).get("atomic_part_ids_in_source_order")
            != list(EXPECTED_ATOMIC_IDS)
            or theme_summary.get("independent_choice_section") is not False
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_report_invalid", "theme summary drifted"
            )

        forbidden_crop_ids = frozenset(
            crop_id_by_path[path]
            for path in crop_by_path
            if path not in safe_question_paths
        )
        answer_crop_ids = frozenset(crop_id_by_path[path] for path in answer_paths)
        output_bindings = {**outputs, **scripts, **crop_bindings}
        output_bytes.update(crop_bytes_by_path)
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids=answer_crop_ids,
            forbidden_crop_ids=forbidden_crop_ids,
            corrected_fields_by_master=corrected_fields,
            manifest_self_sha256=EXPECTED_MANIFEST_SELF_SHA256,
            master_crosswalk_manifest_self_sha256=str(
                self.master_workbench._integrity(master_snapshot)[
                    "manifest_self_sha256"
                ]
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
            "answer_pixels_forbidden": True,
            "fail_closed": True,
        }

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
                "question_or_shared_crop_bindings": len(
                    set(snapshot.crop_by_id) - set(snapshot.forbidden_crop_ids)
                ),
                "shared_crop_bindings": len(
                    {
                        crop_id
                        for record in snapshot.records
                        for crop_id in record["dependency"]["shared_material_crop_ids"]
                    }
                ),
                "nonofficial_answer_crop_bindings": len(snapshot.answer_crop_ids),
                "total_exact_crop_bindings": 33,
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
            known_code = (
                "source_quality_note" if record["answer"].get("quality_note") else None
            )
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
                    "source_year": 2025,
                    "source_region_or_school": "黄浦区",
                    "source_paper_type": "二模",
                    "textbook_mapping_status": record["textbook_mapping"][
                        "mapping_status"
                    ],
                    **reference_answer_catalog_metadata(
                        record["answer"], None, known_code
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
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Huangpu theme-four direct scan",
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
            "source_identity": deepcopy(record["source_identity"]),
            "visible_summary_zh": record["visible_summary_zh"],
            "response_requirement_zh": record["response_requirement_zh"],
            "theme_chain_role": deepcopy(record["theme_chain_role"]),
            "dependency": deepcopy(record["dependency"]),
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "comparison_with_master": deepcopy(record["comparison_with_master"]),
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
                "independently_verified": False,
                "official_answer_claim_allowed": False,
            },
            "reference_answer": self._reference_answer(record),
            "textbook_directory_mapping": self._textbook_projection(record),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
        try:
            validate_identifier(master_node_id, "master_node_id")
            validate_identifier(crop_id, "crop_id")
        except SecurityError as exc:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Huangpu theme-four direct scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        category = crop.get("category") if isinstance(crop, dict) else None
        if crop_id in snapshot.forbidden_crop_ids or category not in {
            "question",
            "shared_material",
        }:
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_role_denied",
                "answer, boundary, identity, and QA crops are forbidden",
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
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        stored = crop["stored_path"]
        raw = snapshot.output_bytes.get(stored)
        binding = snapshot.output_bindings.get(stored)
        if (
            raw is None
            or binding is None
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or len(raw) > MAX_CROP_BYTES
            or _sha256(raw) != descriptor["sha256"]
            or self._png_dimensions(raw) != (descriptor["width"], descriptor["height"])
        ):
            raise Huangpu2025Theme4DirectVisualScanError(
                "master_direct_scan_crop_binding_invalid", "served crop binding drifted"
            )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


_RECENT_EXPECTED_GATE_KEYS = frozenset(
    {
        "answer_verified",
        "chemistry_correctness_verified",
        "difficulty_verified",
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
_RECENT_EXPECTED_GATES = {key: False for key in _RECENT_EXPECTED_GATE_KEYS}
_RECENT_SOURCE_PAGE = re.compile(
    r"(?:question|answer)-page-(\d+)\.(?:jpe?g|png)\Z", re.IGNORECASE
)


@dataclass(frozen=True)
class _RecentThemeProductConfig:
    product_id: str
    batch_id: str
    paper_id: str
    theme_id: str
    product_relative: Path
    manifest_bytes: int
    manifest_file_sha256: str
    manifest_self_sha256: str
    printed_ids: tuple[str, ...]
    atomic_ids: tuple[str, ...]
    dependencies: dict[str, tuple[str, ...]]
    quality_ids: frozenset[str]
    crop_count: int
    source_binding_count: int
    theme_order: int
    theme_title: str
    page_span: tuple[int, int]
    source_year: int
    source_region_or_school: str
    source_paper_type: str
    source_official_status: str
    source_id: str
    paper_face_identity: dict[str, Any]
    paper_identity_boundary: dict[str, Any]
    crop_id_prefix: str

    @property
    def product_relative_posix(self) -> str:
        return self.product_relative.as_posix()

    @property
    def output_binding_count(self) -> int:
        return 11 + 5 + self.crop_count


QIBAO2025_OPENING_THEME4_CONFIG = _RecentThemeProductConfig(
    product_id="QVS-QB2025-OPENING-T4-ELECTROLYTE-WASTEWATER-V1",
    batch_id="BATCH-QB2025-OPENING-T4-ELECTROLYTE-WASTEWATER-20260827",
    paper_id="PAPER-10301ae251d89b7bb14d",
    theme_id="THEME-14f2350cd3d5dab644a6",
    product_relative=Path(
        "kb/classification/"
        "question_visual_scan_qibao_2025_opening_theme4_"
        "electrolyte_wastewater_v1_2026-08-27"
    ),
    manifest_bytes=3_229,
    manifest_file_sha256=(
        "fec00f736a0c4d7d1aeba3bd0c89c03eec5ecd37f85e82de146503034efdde88"
    ),
    manifest_self_sha256=(
        "091101f07ddb2408a51eaa0d50dfa82cc8c5c1bf7e599421d928f8c327d49e07"
    ),
    printed_ids=tuple(f"QB2025-OPEN-S4-Q{index}" for index in range(1, 9)),
    atomic_ids=(
        "QB2025-OPEN-S4-Q1-P1",
        "QB2025-OPEN-S4-Q2-P1",
        "QB2025-OPEN-S4-Q3-P1",
        "QB2025-OPEN-S4-Q4-P1",
        "QB2025-OPEN-S4-Q4-P2",
        "QB2025-OPEN-S4-Q5-P1",
        "QB2025-OPEN-S4-Q5-P2",
        "QB2025-OPEN-S4-Q6-P1",
        "QB2025-OPEN-S4-Q7-P1",
        "QB2025-OPEN-S4-Q8-P1",
    ),
    dependencies={
        "QB2025-OPEN-S4-Q4-P2": ("QB2025-OPEN-S4-Q4-P1",),
        "QB2025-OPEN-S4-Q5-P2": ("QB2025-OPEN-S4-Q5-P1",),
    },
    quality_ids=frozenset({"QB2025-OPEN-S4-Q7-P1"}),
    crop_count=31,
    source_binding_count=21,
    theme_order=4,
    theme_title="四、电解质溶液及废水处理",
    page_span=(4, 5),
    source_year=2025,
    source_region_or_school="上海市七宝中学",
    source_paper_type="高三年级开学练习",
    source_official_status="nonofficial_wechat_repost",
    source_id="file-c17b879d67f73e8833fd73e21f9ce4f56ea5b2a598bd9848a94d52596dde350a",
    paper_face_identity={
        "title": "上海市七宝中学2025学年第一学期高三年级开学练习 化学试卷",
        "academic_year": "2025学年",
        "semester": "第一学期",
        "grade": "高三",
        "school": "上海市七宝中学",
        "exam_month": None,
        "duration_minutes": None,
        "total_score": None,
        "authority": "paper_face_pixels",
    },
    paper_identity_boundary={
        "paper_face_title_literal": (
            "上海市七宝中学2025学年第一学期高三年级开学练习 化学试卷"
        ),
        "year": 2025,
        "region_label": "上海市七宝中学（卷面直接身份）",
        "paper_family": "opening_practice_paper_face_verified",
        "paper_type": "高三年级开学练习",
        "covered_scope": "主题四（电解质溶液及废水处理）",
        "coverage_scope": (
            "theme_4_electrolyte_wastewater_only_10_atomic_not_complete_paper"
        ),
        "complete_paper_visual_scan_claim_allowed": False,
        "official_identity_claim_allowed": False,
        "official_status": "nonofficial_wechat_repost",
        "source_account": "申教在线",
        "answer_authority": "nonofficial_reference",
        "answer_independently_verified": False,
    },
    crop_id_prefix="QB2025-T4",
)

HONGKOU2026_SECOND_MOCK_THEME4_CONFIG = _RecentThemeProductConfig(
    product_id="QVS-HK2026-SECOND-MOCK-T4-NICKEL-RECOVERY-V1",
    batch_id="BATCH-HK2026-SECOND-MOCK-T4-NICKEL-RECOVERY-20260827",
    paper_id="PAPER-8da880c69c0bb53bdbb4",
    theme_id="THEME-11c6898df179f3701b15",
    product_relative=Path(
        "kb/classification/"
        "question_visual_scan_hongkou_2026_second_mock_theme4_"
        "nickel_recovery_v1_2026-08-27"
    ),
    manifest_bytes=3_221,
    manifest_file_sha256=(
        "0a8859df6bf1cc7c9133c6c06160932fe19fb8c197f7ebbb8e2cfaedab688113"
    ),
    manifest_self_sha256=(
        "bb5320024e811b45a55c417fb899a3a3075257a7aed83b9dfcc7b89c5eeac438"
    ),
    printed_ids=tuple(f"HK2026-EM-S4-Q{index}" for index in range(1, 8)),
    atomic_ids=(
        "HK2026-EM-S4-Q1-P1",
        "HK2026-EM-S4-Q2-P1",
        "HK2026-EM-S4-Q2-P2",
        "HK2026-EM-S4-Q3-P1",
        "HK2026-EM-S4-Q4-P1",
        "HK2026-EM-S4-Q5-P1",
        "HK2026-EM-S4-Q5-P2",
        "HK2026-EM-S4-Q6-P1",
        "HK2026-EM-S4-Q7-P1",
    ),
    dependencies={
        "HK2026-EM-S4-Q5-P2": ("HK2026-EM-S4-Q5-P1",),
    },
    quality_ids=frozenset(
        {"HK2026-EM-S4-Q3-P1", "HK2026-EM-S4-Q6-P1"}
    ),
    crop_count=28,
    source_binding_count=27,
    theme_order=4,
    theme_title="四、电镀污泥中镍的回收与测定",
    page_span=(5, 6),
    source_year=2026,
    source_region_or_school="虹口区（公众号文章标题身份）",
    source_paper_type="二模（公众号文章标题身份）",
    source_official_status="nonofficial_wechat_repost",
    source_id="file-4a9d4d23b28f3b5df5b3ab44e641b3530a7555702546259523bb88dae916ad83",
    paper_face_identity={
        "title": "高三 化学 2026.4",
        "calendar_year": 2026,
        "calendar_month": 4,
        "date_literal": "2026.4",
        "grade": "高三",
        "school": None,
        "district": None,
        "paper_type": None,
        "duration_minutes": 60,
        "total_score": 100,
        "authority": "paper_face_pixels",
    },
    paper_identity_boundary={
        "paper_face_title_literal": "高三 化学 2026.4",
        "article_title_literal": (
            "【高三二模】2026届上海市虹口区高三二模化学试卷和答案"
        ),
        "year": 2026,
        "region_label": "虹口区（公众号标题；卷面未署地区）",
        "paper_family": "second_mock_article_classified",
        "paper_type": "二模（公众号标题；卷面未署二模）",
        "covered_scope": "主题四（电镀污泥中镍的回收与测定）",
        "coverage_scope": (
            "theme_4_nickel_recovery_only_9_atomic_not_complete_paper"
        ),
        "complete_paper_visual_scan_claim_allowed": False,
        "district_face_claim_allowed": False,
        "second_mock_face_claim_allowed": False,
        "official_identity_claim_allowed": False,
        "official_status": "nonofficial_wechat_repost",
        "source_account": "申教在线",
        "answer_authority": "nonofficial_reference",
        "answer_independently_verified": False,
    },
    crop_id_prefix="HK2026-T4",
)


class _RecentThemeDirectVisualScanReader(Huangpu2025Theme4DirectVisualScanReader):
    """Configuration-driven strict adapter for the 2026-08-27 theme products."""

    CONFIG: _RecentThemeProductConfig

    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.config = self.CONFIG
        self.shchem_root = shchem_root.resolve()
        self.product_root = (
            self.shchem_root / self.config.product_relative
        ).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise MasterDirectVisualScanError(
                "master_direct_scan_path_invalid", "product root leaves sh-chem-db"
            )
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    @staticmethod
    def _recent_source_page(asset: dict[str, Any]) -> int:
        source_asset = asset.get("source_asset")
        match = _RECENT_SOURCE_PAGE.search(source_asset or "")
        if match is None:
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid",
                "an exposable crop lacks a source-page binding",
            )
        return int(match.group(1))

    @staticmethod
    def _recent_false_gates(value: Any, label: str) -> None:
        if value != _RECENT_EXPECTED_GATES or set(value or {}) != set(
            _RECENT_EXPECTED_GATE_KEYS
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_gate_elevated", f"{label} gates drifted"
            )

    @staticmethod
    def _knowledge_pairs(record: dict[str, Any]) -> list[tuple[str, str]]:
        classification = record["classification"]
        return [
            (classification["primary_K"], "primary"),
            *[
                (knowledge, "supporting")
                for knowledge in classification["supporting_K"]
            ],
        ]

    def _snapshot(self) -> _Snapshot:
        config = self.config
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if (
            len(manifest_raw) != config.manifest_bytes
            or _sha256(manifest_raw) != config.manifest_file_sha256
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        expected_counts = {
            "paper": 1,
            "theme_big_question": 1,
            "printed_question": len(config.printed_ids),
            "atomic_part": len(config.atomic_ids),
            "crop_assets": config.crop_count,
        }
        if (
            manifest.get("schema_version") != "1.0.0"
            or manifest.get("product_id") != config.product_id
            or manifest.get("batch_id") != config.batch_id
            or manifest.get("counts") != expected_counts
            or manifest.get("ownership")
            != {
                "scope": config.product_relative_posix,
                "central_master_mutated": False,
                "forbidden_parallel_paths_touched": False,
            }
            or manifest.get("gates") != _EXPECTED_MANIFEST_GATES
            or manifest.get("self_sha256") != config.manifest_self_sha256
            or _manifest_self_hash(manifest) != config.manifest_self_sha256
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest contract drifted"
            )
        outputs = _manifest_bindings(manifest.get("outputs"), "output", 11)
        scripts = _manifest_bindings(manifest.get("scripts"), "script", 5)
        required_outputs = {
            "README.md",
            "coverage_report.json",
            "crop_manifest.json",
            "crop_manifest_seed.json",
            "disagreement_report.json",
            "master_gap_projection.json",
            "scan_record_schema.json",
            "scan_records.jsonl",
            "source_manifest.json",
            "textbook_mapping_summary.json",
            "theme_summary.json",
        }
        required_scripts = {
            "build_product.py",
            "make_crops.py",
            "run_mutation_tests.py",
            "test_visual_scan.py",
            "validate_visual_scan.py",
        }
        if set(outputs) != required_outputs or set(scripts) != required_scripts:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid",
                "manifest output or script closure drifted",
            )
        output_bytes: dict[str, bytes] = {}
        for label, bindings in (("output", outputs), ("script", scripts)):
            for relative, binding in bindings.items():
                _, raw = self._verified_file(self.product_root, relative)
                if _sha256(raw) != binding["sha256"]:
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_binding_mismatch",
                        f"{label} hash binding drifted",
                    )
                output_bytes[relative] = raw

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        source_groups = (
            source_manifest.get("source_assets"),
            source_manifest.get("protected_inputs"),
            source_manifest.get("textbook_sources"),
        )
        if (
            source_manifest.get("schema_version") != "1.0.0"
            or source_manifest.get("batch_id") != config.batch_id
            or source_manifest.get("source_id") != config.source_id
            or source_manifest.get("paper_face_identity")
            != config.paper_face_identity
            or source_manifest.get("answer_authority")
            != "nonofficial_reposted_reference; direct_adoption_without_independent_solution_verification"
            or source_manifest.get("article_identity", {}).get("authority")
            != "article_metadata_not_paper_identity"
            or any(not isinstance(group, list) for group in source_groups)
            or sum(len(group) for group in source_groups)
            != config.source_binding_count
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_source_invalid",
                "source identity, authority, or binding count drifted",
            )
        source_by_path: dict[str, dict[str, Any]] = {}
        for group in source_groups:
            for binding in group:
                path = binding.get("path") if isinstance(binding, dict) else None
                if not isinstance(path, str) or path in source_by_path:
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_source_invalid",
                        "source path is missing or duplicated",
                    )
                self._verify_source_binding(binding)
                source_by_path[path] = binding

        schema = _json_object(output_bytes["scan_record_schema.json"], "schema")
        try:
            Draft202012Validator.check_schema(schema)
            schema_validator = Draft202012Validator(schema)
        except Exception as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_schema_invalid", "bound schema is invalid"
            ) from exc
        raw_records = _jsonl_objects(output_bytes["scan_records.jsonl"], "records")
        if len(raw_records) != len(config.atomic_ids):
            raise MasterDirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count drifted"
            )
        for index, record in enumerate(raw_records, 1):
            if next(schema_validator.iter_errors(record), None) is not None:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} failed its bound schema",
                )

        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop manifest"
        )
        crop_assets = crop_manifest.get("assets")
        if (
            crop_manifest.get("schema_version") != "1.0.0"
            or crop_manifest.get("batch_id") != config.batch_id
            or crop_manifest.get("crop_count") != config.crop_count
            or not isinstance(crop_assets, list)
            or len(crop_assets) != config.crop_count
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop count drifted"
            )
        crop_by_path: dict[str, dict[str, Any]] = {}
        crop_by_id: dict[str, dict[str, Any]] = {}
        crop_id_by_path: dict[str, str] = {}
        crop_bytes_by_path: dict[str, bytes] = {}
        crop_bindings: dict[str, dict[str, Any]] = {}
        prefix = config.product_relative_posix + "/"
        for index, asset in enumerate(crop_assets, 1):
            if not isinstance(asset, dict):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid", "crop shape drifted"
                )
            stored = _safe_relative(asset.get("asset"))
            dimensions = asset.get("dimensions")
            digest = asset.get("sha256")
            if (
                not stored.startswith(prefix)
                or stored in crop_by_path
                or not isinstance(digest, str)
                or _HEX64.fullmatch(digest) is None
                or not isinstance(dimensions, list)
                or len(dimensions) != 2
                or any(type(value) is not int or value < 1 for value in dimensions)
                or asset.get("direct_source_pixel_reuse_allowed", False) is not False
                or asset.get("direct_pixel_reuse_allowed", False) is not False
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop path, hash, dimensions, or rights boundary drifted",
                )
            relative = stored[len(prefix) :]
            _, raw = self._verified_file(self.product_root, relative)
            if _sha256(raw) != digest or self._png_dimensions(raw) != tuple(dimensions):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop hash or dimensions drifted",
                )
            explicit_crop_id = asset.get("crop_id")
            crop_id = (
                explicit_crop_id
                if isinstance(explicit_crop_id, str) and explicit_crop_id
                else f"{config.crop_id_prefix}-CROP-{index:02d}"
            )
            if crop_id in crop_by_id:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop identity is duplicated",
                )
            source_asset = asset.get("source_asset")
            if isinstance(source_asset, str) and not source_asset.startswith("derived_"):
                source_binding = source_by_path.get(source_asset)
                if (
                    source_binding is None
                    or asset.get("source_asset_sha256")
                    != source_binding.get("sha256")
                    or (
                        source_binding.get("dimensions") is not None
                        and asset.get("source_asset_dimensions")
                        != source_binding.get("dimensions")
                    )
                ):
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_crop_manifest_invalid",
                        "crop-to-source binding drifted",
                    )
            crop_id_by_path[stored] = crop_id
            crop_by_path[stored] = asset
            crop_bytes_by_path[stored] = raw
            crop_bindings[stored] = {"sha256": digest, "bytes": len(raw)}
            crop_by_id[crop_id] = {
                "crop_id": crop_id,
                "stored_path": stored,
                "sha256": digest,
                "bytes": len(raw),
                "width": dimensions[0],
                "height": dimensions[1],
                "role": asset.get("role"),
                "category": "forbidden",
            }

        expected_tree = set(outputs) | set(scripts) | {
            str(Path(path).relative_to(config.product_relative)).replace("\\", "/")
            for path in crop_by_path
        }
        allowed_unbound = {"validation_report.json", "mutation_test_report.json"}
        try:
            actual_tree = {
                path.relative_to(self.product_root).as_posix()
                for path in self.product_root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and ".pytest_cache" not in path.parts
                and path.name != "manifest.json"
            }
        except OSError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_file_read_failed", "product tree is unreadable"
            ) from exc
        if not expected_tree <= actual_tree or actual_tree - expected_tree - allowed_unbound:
            raise MasterDirectVisualScanError(
                "master_direct_scan_binding_invalid", "product file closure drifted"
            )

        try:
            master_snapshot = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise MasterDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified master470/exact169 identity is unavailable",
            ) from exc
        master_ids = {
            str(row["atomic_part_id"])
            for row in master_snapshot.master_layers["atomic_part"]
        }
        exact_ids: set[str] = set()
        for master_id, relations in master_snapshot.relations_by_master.items():
            if len(relations) != 1:
                continue
            relation = relations[0]
            if (
                relation.get("relation_type") == "exact_1_to_1"
                and relation.get("identity_mapping_allowed") is True
            ):
                exact_ids.add(master_id)
        if (
            len(master_ids) != 470
            or len(exact_ids) != 169
            or not set(config.atomic_ids) <= master_ids
            or set(config.atomic_ids) & exact_ids
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "theme rows are absent from master470 or overlap exact169",
            )

        safe_question_paths: set[str] = set()
        shared_paths: set[str] = set()
        answer_paths: set[str] = set()
        for record in raw_records:
            evidence = record.get("evidence")
            if not isinstance(evidence, dict):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "record evidence is absent"
                )
            safe_question_paths.update(evidence.get("question_crop_refs", []))
            shared_paths.update(evidence.get("shared_material_refs", []))
            answer_paths.update(evidence.get("answer_crop_refs", []))
        if (
            (safe_question_paths | shared_paths) & answer_paths
            or not (safe_question_paths | shared_paths | answer_paths)
            <= set(crop_by_path)
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_role_invalid", "crop role boundary drifted"
            )
        for stored in safe_question_paths | shared_paths:
            asset = crop_by_path[stored]
            role = asset.get("role")
            expected_category = (
                "shared_material" if stored in shared_paths else "question"
            )
            if (
                expected_category == "question"
                and role
                not in {"question_evidence", "question_boundary_current_theme_end"}
            ) or (
                expected_category == "shared_material"
                and not str(role).startswith("shared_material")
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_role_invalid",
                    "question/shared crop role drifted",
                )
            crop_id = crop_id_by_path[stored]
            crop_by_id[crop_id]["category"] = expected_category
            crop_by_id[crop_id]["source_page"] = self._recent_source_page(asset)
            if len(crop_bytes_by_path[stored]) > MAX_CROP_BYTES:
                raise MasterDirectVisualScanError(
                    "master_direct_scan_crop_too_large",
                    "an exposable crop exceeds one MiB",
                    413,
                )
        if any(
            crop_by_path[path].get("role")
            not in {"answer_evidence", "answer_context", "answer_context_complete_theme"}
            for path in answer_paths
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_role_invalid", "answer crop role drifted"
            )

        records: list[dict[str, Any]] = []
        by_master_id: dict[str, dict[str, Any]] = {}
        corrected_fields: dict[str, tuple[str, ...]] = {}
        seen_ids: set[str] = set()
        seen_printed: list[str] = []
        quality_ids: set[str] = set()
        referenced_questions: set[str] = set()
        referenced_shared: set[str] = set()
        referenced_answers: set[str] = set()
        source_order = {node_id: index for index, node_id in enumerate(config.atomic_ids)}
        for index, (expected_id, raw) in enumerate(
            zip(config.atomic_ids, raw_records, strict=True), 1
        ):
            hierarchy = raw.get("hierarchy")
            source = raw.get("source_identity")
            answer = raw.get("answer")
            classification = raw.get("classification")
            difficulty = raw.get("difficulty")
            dependency = raw.get("dependency")
            evidence = raw.get("evidence")
            textbook = raw.get("textbook_mapping")
            if (
                not isinstance(hierarchy, dict)
                or hierarchy.get("atomic_part_id") != expected_id
                or hierarchy.get("paper_id") != config.paper_id
                or hierarchy.get("theme_big_question_id") != config.theme_id
                or hierarchy.get("theme_order") != config.theme_order
                or hierarchy.get("theme_title") != config.theme_title
                or hierarchy.get("independent_choice_section") is not False
                or expected_id in seen_ids
                or raw.get("record_index") != index
                or raw.get("batch_id") != config.batch_id
                or raw.get("evidence_binding_sha256") != _record_self_hash(raw)
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record identity drifted"
                )
            printed_id = hierarchy.get("printed_question_id")
            printed_order = hierarchy.get("printed_question_order")
            atomic_order = hierarchy.get("atomic_part_order")
            if (
                printed_id not in config.printed_ids
                or printed_order != config.printed_ids.index(printed_id) + 1
                or type(atomic_order) is not int
                or atomic_order < 1
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_identity_conflict", "printed hierarchy drifted"
                )
            if printed_id not in seen_printed:
                seen_printed.append(printed_id)
            atom = master_snapshot.master_nodes.get(("atomic_part", expected_id))
            printed = master_snapshot.master_nodes.get(
                ("printed_question", printed_id)
            )
            theme = master_snapshot.master_nodes.get(
                ("theme_big_question", config.theme_id)
            )
            paper = master_snapshot.master_nodes.get(("paper", config.paper_id))
            if (
                not all(isinstance(value, dict) for value in (atom, printed, theme, paper))
                or atom.get("parent_paper_id") != config.paper_id
                or atom.get("parent_theme_big_question_id") != config.theme_id
                or atom.get("parent_printed_question_id") != printed_id
                or printed.get("parent_paper_id") != config.paper_id
                or printed.get("parent_theme_big_question_id") != config.theme_id
                or theme.get("parent_paper_id") != config.paper_id
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_master_identity_conflict",
                    "paper/theme/printed/atomic chain disagrees with Master",
                )
            if (
                not isinstance(source, dict)
                or source.get("source_id") != config.source_id
                or source.get("year") != config.source_year
                or source.get("region_or_school")
                != config.source_region_or_school
                or source.get("paper_type") != config.source_paper_type
                or source.get("paper_face_title_literal")
                != config.paper_identity_boundary["paper_face_title_literal"]
                or source.get("official_status") != config.source_official_status
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_source_invalid", "record source identity drifted"
                )
            self._recent_false_gates(raw.get("authority_gates"), expected_id)
            if (
                not isinstance(answer, dict)
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("source_authority")
                != "nonofficial_wechat_reposted_reference"
                or answer.get("independently_verified") is not False
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or answer.get("official_scoring_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not answer["reference_summary_zh"]
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer boundary drifted"
                )
            if bool(answer.get("quality_note")) != (expected_id in config.quality_ids):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_quality_note_invalid", "quality note drifted"
                )
            if answer.get("quality_note"):
                quality_ids.add(expected_id)
            if (
                not isinstance(classification, dict)
                or not isinstance(classification.get("item_type"), str)
                or classification.get("selection_rule")
                not in {"not_applicable", "single", "indeterminate"}
                or not isinstance(classification.get("primary_K"), str)
                or any(
                    not isinstance(classification.get(axis), list)
                    or any(
                        not isinstance(value, str) or not value
                        for value in classification[axis]
                    )
                    for axis in ("supporting_K", "A", "C", "R", "RP")
                )
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_classification_invalid",
                    "answer-shape classification drifted",
                )
            factors = (
                difficulty.get("factors") if isinstance(difficulty, dict) else None
            )
            if (
                not isinstance(factors, list)
                or len(factors) != 10
                or {factor.get("dimension_id") for factor in factors} != _FACTOR_IDS
                or difficulty.get("candidate_label")
                not in {"D1", "D2", "D3", "D4", "D5"}
                or difficulty.get("human_verified") is not False
                or difficulty.get("is_measured_difficulty") is not False
                or difficulty.get("measured") is not None
                or difficulty.get("student_data_used") is not False
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "difficulty boundary drifted"
                )
            prior = (
                dependency.get("depends_on_atomic_parts")
                if isinstance(dependency, dict)
                else None
            )
            if prior != list(config.dependencies.get(expected_id, ())):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency edge drifted"
                )
            if any(
                value not in source_order or source_order[value] >= source_order[expected_id]
                for value in prior
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_dependency_invalid",
                    "dependency points forward or outside the complete theme",
                )
            qrefs = evidence.get("question_crop_refs")
            srefs = evidence.get("shared_material_refs")
            arefs = evidence.get("answer_crop_refs")
            if (
                not isinstance(qrefs, list)
                or not qrefs
                or not isinstance(srefs, list)
                or not isinstance(arefs, list)
                or not arefs
                or any(path not in crop_by_path for path in [*qrefs, *srefs, *arefs])
                or evidence.get("question_crop_hashes")
                != [crop_by_path[path]["sha256"] for path in qrefs]
                or evidence.get("answer_crop_hashes")
                != [crop_by_path[path]["sha256"] for path in arefs]
                or sum(
                    crop_by_path[path].get("role") == "question_evidence"
                    for path in qrefs
                )
                != 1
                or any(
                    crop_by_path[path].get("role")
                    not in {
                        "question_evidence",
                        "question_boundary_current_theme_end",
                    }
                    for path in qrefs
                )
                or any(
                    not str(crop_by_path[path].get("role")).startswith("shared_material")
                    for path in srefs
                )
                or any(
                    crop_by_path[path].get("role")
                    not in {"answer_evidence", "answer_context", "answer_context_complete_theme"}
                    for path in arefs
                )
                or answer.get("visual_alignment_evidence") != arefs
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "record crop binding drifted"
                )
            referenced_questions.update(qrefs)
            referenced_shared.update(srefs)
            referenced_answers.update(arefs)
            if (
                not isinstance(textbook, dict)
                or textbook.get("mapping_status")
                not in {"complete_directory_level_unit_unknown", "partial_blocked"}
                or not isinstance(textbook.get("entries"), list)
                or not textbook["entries"]
                or [
                    (entry.get("knowledge_tag"), entry.get("knowledge_role"))
                    for entry in textbook["entries"]
                ]
                != self._knowledge_pairs(raw)
            ):
                raise MasterDirectVisualScanError(
                    "master_direct_scan_textbook_invalid", "textbook K closure drifted"
                )
            for entry in textbook["entries"]:
                source_binding = source_by_path.get(entry.get("source_path"))
                if (
                    entry.get("status")
                    not in {
                        "toc_direct_directory_mapping",
                        "direct_visual_directory_mapping",
                        "blocked_pending_review",
                    }
                    or entry.get("edition_status")
                    != "unknown_not_externally_verified"
                    or entry.get("unit_id") is not None
                    or entry.get("unit_title") is not None
                    or entry.get("unit_status")
                    != "unknown_no_verified_atomic_unit_registry"
                    or source_binding is None
                    or entry.get("source_sha256") != source_binding.get("sha256")
                ):
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_textbook_invalid",
                        "textbook authority or source binding drifted",
                    )
                visual_evidence = entry.get("visual_evidence")
                if not isinstance(visual_evidence, list) or not visual_evidence:
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_textbook_invalid",
                        "textbook directory evidence is absent",
                    )
                for descriptor in visual_evidence:
                    binding = source_by_path.get(descriptor.get("path"))
                    if binding is None or descriptor.get("sha256") != binding.get(
                        "sha256"
                    ):
                        raise MasterDirectVisualScanError(
                            "master_direct_scan_textbook_invalid",
                            "textbook visual evidence binding drifted",
                        )
                registry_path = entry.get("mapping_registry_path")
                if registry_path is not None:
                    binding = source_by_path.get(registry_path)
                    if binding is None or entry.get(
                        "mapping_registry_sha256"
                    ) != binding.get("sha256"):
                        raise MasterDirectVisualScanError(
                            "master_direct_scan_textbook_invalid",
                            "textbook registry binding drifted",
                        )
            allowed_factor_refs = set(qrefs) | set(srefs)
            normalized_factors: list[dict[str, Any]] = []
            for factor in factors:
                refs = factor.get("evidence_refs")
                if (
                    not isinstance(refs, list)
                    or not refs
                    or not set(refs) <= allowed_factor_refs
                ):
                    raise MasterDirectVisualScanError(
                        "master_direct_scan_difficulty_invalid",
                        "difficulty evidence leaves question/shared pixels",
                    )
                normalized_factors.append(
                    {
                        "dimension_id": factor["dimension_id"],
                        "value": deepcopy(factor.get("value")),
                        "basis": factor.get("basis"),
                        "evidence_crop_ids": [crop_id_by_path[path] for path in refs],
                    }
                )
            viewed_evidence: list[dict[str, Any]] = []
            for stored, evidence_role in [
                *((path, "question") for path in qrefs),
                *((path, "shared_material") for path in srefs),
            ]:
                asset = crop_by_path[stored]
                viewed_evidence.append(
                    {
                        "crop_id": crop_id_by_path[stored],
                        "evidence_role": evidence_role,
                        "source_page": self._recent_source_page(asset),
                        "sha256": asset["sha256"],
                        "bytes": len(crop_bytes_by_path[stored]),
                        "width": asset["dimensions"][0],
                        "height": asset["dimensions"][1],
                    }
                )
            normalized = {
                "scan_id": f"{config.crop_id_prefix}-{expected_id}",
                "scan_status": "visual_scan_completed",
                "visual_scan_completed": True,
                "hierarchy": {
                    "paper_id": config.paper_id,
                    "theme_id": config.theme_id,
                    "theme_sequence": config.theme_order,
                    "theme_title": config.theme_title,
                    "printed_question_id": printed_id,
                    "printed_sequence": printed_order,
                    "atomic_part_id": expected_id,
                    "atomic_sequence_in_printed": atomic_order,
                    "independent_choice_section": False,
                },
                "source_identity": self._safe_source_identity(raw),
                "identity_boundary": deepcopy(config.paper_identity_boundary),
                "visible_summary_zh": raw["prompt"]["normalized_task_zh"],
                "response_requirement_zh": "、".join(
                    classification["response_form_zh"]
                ),
                "theme_chain_role": {
                    "role_zh": (
                        "承接前序作答单元" if prior else "使用主题共享材料独立作答"
                    ),
                    "candidate_only": True,
                },
                "viewed_evidence": viewed_evidence,
                "classification": deepcopy(classification),
                "difficulty": {
                    "cognitive_prelabel": difficulty["candidate_label"],
                    "candidate_label": difficulty["candidate_label"],
                    "status": difficulty["status"],
                    "human_verified": False,
                    "is_measured_difficulty": False,
                    "measured": None,
                    "student_data_used": False,
                    "factors": normalized_factors,
                },
                "dependency": {
                    "analysis_zh": dependency["analysis_zh"],
                    "prior_atomic_part_ids": list(prior),
                    "shared_material_crop_ids": [
                        crop_id_by_path[path] for path in srefs
                    ],
                },
                "answer": deepcopy(answer),
                "comparison_with_master": deepcopy(raw["comparison_with_master"]),
                "textbook_mapping": deepcopy(textbook),
                "authority_gates": deepcopy(raw["authority_gates"]),
            }
            corrected_fields[expected_id] = (
                ("item_type",)
                if raw["comparison_with_master"].get("item_type_change")
                == "unknown_to_direct_visual_candidate"
                else ()
            )
            records.append(normalized)
            by_master_id[expected_id] = normalized
            seen_ids.add(expected_id)

        if (
            tuple(seen_printed) != config.printed_ids
            or quality_ids != set(config.quality_ids)
            or referenced_questions != safe_question_paths
            or referenced_shared != shared_paths
            or referenced_answers != answer_paths
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_count_mismatch",
                "record, evidence, or quality-note closure drifted",
            )
        theme_summary = _json_object(
            output_bytes["theme_summary.json"], "theme summary"
        )
        expected_dependency_edges = [
            {
                "from": prior,
                "to": target,
                "kind": "explicit_previous_part_conclusion",
            }
            for target in config.atomic_ids
            for prior in config.dependencies.get(target, ())
        ]
        if (
            theme_summary.get("counts")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": len(config.printed_ids),
                "atomic_part": len(config.atomic_ids),
            }
            or theme_summary.get("hierarchy", {}).get("paper_id")
            != config.paper_id
            or theme_summary.get("hierarchy", {}).get("theme_big_question_id")
            != config.theme_id
            or theme_summary.get("hierarchy", {}).get("theme_order")
            != config.theme_order
            or theme_summary.get("hierarchy", {}).get("theme_title")
            != config.theme_title
            or theme_summary.get("hierarchy", {}).get(
                "printed_question_ids_in_source_order"
            )
            != list(config.printed_ids)
            or theme_summary.get("hierarchy", {}).get(
                "atomic_part_ids_in_source_order"
            )
            != list(config.atomic_ids)
            or theme_summary.get("page_span") != list(config.page_span)
            or theme_summary.get("dependency_edges") != expected_dependency_edges
            or theme_summary.get("independent_choice_section") is not False
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_report_invalid", "complete theme summary drifted"
            )
        coverage = _json_object(
            output_bytes["coverage_report.json"], "coverage report"
        )
        if any(
            coverage.get(key) != expected
            for key, expected in {
                "theme_atomic_count": len(config.atomic_ids),
                "question_visual_covered": len(config.atomic_ids),
                "answer_part_aligned": len(config.atomic_ids),
                "item_type_covered": len(config.atomic_ids),
                "A_covered": len(config.atomic_ids),
                "C_covered": len(config.atomic_ids),
                "R_covered": len(config.atomic_ids),
                "RP_covered": len(config.atomic_ids),
                "difficulty_factor_supported": len(config.atomic_ids),
                "difficulty_measured": 0,
                "textbook_K_entry_closed": len(config.atomic_ids),
                "source_identity_closed": len(config.atomic_ids),
                "human_reviewed": 0,
                "official_answer": 0,
                "conflict_or_quality_note_count": len(config.quality_ids),
            }.items()
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_report_invalid", "coverage report drifted"
            )
        textbook_summary = _json_object(
            output_bytes["textbook_mapping_summary.json"], "textbook summary"
        )
        if (
            textbook_summary.get("atomic_count") != len(config.atomic_ids)
            or textbook_summary.get("unit_verified_count") != 0
            or textbook_summary.get("edition_verified_count") != 0
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_textbook_invalid", "textbook summary drifted"
            )

        forbidden_crop_ids = frozenset(
            crop_id_by_path[path]
            for path in crop_by_path
            if path not in safe_question_paths | shared_paths
        )
        answer_crop_ids = frozenset(crop_id_by_path[path] for path in answer_paths)
        output_bindings = {**outputs, **scripts, **crop_bindings}
        output_bytes.update(crop_bytes_by_path)
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids=answer_crop_ids,
            forbidden_crop_ids=forbidden_crop_ids,
            corrected_fields_by_master=corrected_fields,
            manifest_self_sha256=config.manifest_self_sha256,
            master_crosswalk_manifest_self_sha256=str(
                self.master_workbench._integrity(master_snapshot)[
                    "manifest_self_sha256"
                ]
            ),
            paper_identity_boundary=deepcopy(config.paper_identity_boundary),
        )

    def _coverage(self) -> dict[str, int]:
        direct = len(self.config.atomic_ids)
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": direct,
            "visual_scanned_master_atomic": 169 + direct,
            "remaining_unscanned": 470 - 169 - direct,
            "direct_exact_overlap": 0,
        }

    def _integrity(self, snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": snapshot.manifest_self_sha256,
            "manifest_file_sha256": self.config.manifest_file_sha256,
            "master_crosswalk_manifest_self_sha256": (
                snapshot.master_crosswalk_manifest_self_sha256
            ),
            "output_binding_count": self.config.output_binding_count,
            "source_binding_count": self.config.source_binding_count,
            "record_count": len(self.config.atomic_ids),
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "four_level_parent_chain_verified_on_read": True,
            "answer_and_textbook_fields_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "answer_pixels_forbidden": True,
            "fail_closed": True,
        }

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        shared_ids = {
            crop_id
            for record in snapshot.records
            for crop_id in record["dependency"]["shared_material_crop_ids"]
        }
        return {
            "product_id": self.config.product_id,
            "paper_id": self.config.paper_id,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": {
                "expected_themes": 1,
                "expected_printed_questions": len(self.config.printed_ids),
                "expected_atomic_parts": len(self.config.atomic_ids),
                "scan_records": len(self.config.atomic_ids),
                "visual_scan_completed": len(self.config.atomic_ids),
                "blocked_pending_broader_crop": 0,
                "question_or_shared_crop_bindings": len(
                    set(snapshot.crop_by_id) - set(snapshot.forbidden_crop_ids)
                ),
                "shared_crop_bindings": len(shared_ids),
                "nonofficial_answer_crop_bindings": len(snapshot.answer_crop_ids),
                "total_exact_crop_bindings": self.config.crop_count,
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
            known_code = (
                "source_quality_note" if record["answer"].get("quality_note") else None
            )
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
                    "source_year": self.config.source_year,
                    "source_region_or_school": self.config.paper_identity_boundary[
                        "region_label"
                    ],
                    "source_paper_type": self.config.paper_identity_boundary[
                        "paper_type"
                    ],
                    "textbook_mapping_status": record["textbook_mapping"][
                        "mapping_status"
                    ],
                    **reference_answer_catalog_metadata(
                        record["answer"], None, known_code
                    ),
                }
            )
        return {
            "product_id": self.config.product_id,
            "paper_id": self.config.paper_id,
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
            raise MasterDirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise MasterDirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no registered theme direct scan",
                404,
            )
        answer = record["answer"]
        return {
            "product_id": self.config.product_id,
            "paper_id": self.config.paper_id,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": record["scan_id"],
            "scan_status": record["scan_status"],
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(record["identity_boundary"]),
            "source_identity": deepcopy(record["source_identity"]),
            "visible_summary_zh": record["visible_summary_zh"],
            "response_requirement_zh": record["response_requirement_zh"],
            "theme_chain_role": deepcopy(record["theme_chain_role"]),
            "dependency": deepcopy(record["dependency"]),
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "comparison_with_master": deepcopy(record["comparison_with_master"]),
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
                "source_authority": answer["source_authority"],
                "verified": False,
                "independently_verified": False,
                "official_answer_claim_allowed": False,
                "official_scoring_claim_allowed": False,
            },
            "reference_answer": self._reference_answer(record),
            "textbook_directory_mapping": self._textbook_projection(record),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
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
                "node has no registered theme direct scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        category = crop.get("category") if isinstance(crop, dict) else None
        if crop_id in snapshot.forbidden_crop_ids or category not in {
            "question",
            "shared_material",
        }:
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_role_denied",
                "answer, boundary, identity, and QA crops are forbidden",
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
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        stored = crop["stored_path"]
        raw = snapshot.output_bytes.get(stored)
        binding = snapshot.output_bindings.get(stored)
        if (
            raw is None
            or binding is None
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or len(raw) > MAX_CROP_BYTES
            or _sha256(raw) != descriptor["sha256"]
            or self._png_dimensions(raw)
            != (descriptor["width"], descriptor["height"])
        ):
            raise MasterDirectVisualScanError(
                "master_direct_scan_crop_binding_invalid", "served crop binding drifted"
            )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


class Qibao2025OpeningTheme4DirectVisualScanReader(
    _RecentThemeDirectVisualScanReader
):
    CONFIG = QIBAO2025_OPENING_THEME4_CONFIG


class Hongkou2026SecondMockTheme4DirectVisualScanReader(
    _RecentThemeDirectVisualScanReader
):
    CONFIG = HONGKOU2026_SECOND_MOCK_THEME4_CONFIG


__all__ = [
    "AUTHORITY",
    "EXPECTED_ATOMIC_IDS",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "HONGKOU2026_SECOND_MOCK_THEME4_CONFIG",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "QIBAO2025_OPENING_THEME4_CONFIG",
    "SCOPE",
    "THEME_ID",
    "Hongkou2026SecondMockTheme4DirectVisualScanReader",
    "Huangpu2025Theme4DirectVisualScanError",
    "Huangpu2025Theme4DirectVisualScanReader",
    "Qibao2025OpeningTheme4DirectVisualScanReader",
]
