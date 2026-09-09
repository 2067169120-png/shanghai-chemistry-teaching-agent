from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import MasterDirectVisualScanError
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .pudong2026_first_mock_theme_direct_visual_scan import (
    _Snapshot,
    _binding_index,
    _canonical_bytes,
    _json_object,
    _jsonl_objects,
    _sha256,
    Pudong2026FirstMockThemeDirectVisualScanReader,
)
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier


PRODUCT_ID = "question_visual_scan_jiading_2025_theme1_disinfectants_v1_2026-08-25"
PAPER_ID = "PAPER-2f34ac7602572e37c8be"
THEME_ID = "THEME-516deca72e2d9aca307d"
PRODUCT_RELATIVE = Path(
    "kb/classification/"
    "question_visual_scan_jiading_2025_theme1_disinfectants_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
EXPECTED_MANIFEST_BYTES = 7947
EXPECTED_MANIFEST_FILE_SHA256 = (
    "aa30e9bde3f73fbd3c323d1f50ec8842aa1c7f5a152f7c484f9b400701b10dab"
)
EXPECTED_MANIFEST_SELF_SHA256 = (
    "ad3e7d874ed952efdea3afb48336bc4916eb6aab086806688031f9decd505754"
)
EXPECTED_RECORDS_SHA256 = (
    "9a153f6b43dfd3d64aae032d112e2815f897f0610774b55451e8c07ecf5968d1"
)
EXPECTED_SCHEMA_SHA256 = (
    "e7e3d2c13dac9ece17dc248ccd5f7dc4598665f019ded79dce19ae570dcf97e9"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "d9185f228b83028dab781a7fe4a9b3c53bf6d76a33eafccc53a39861d04dc83f"
)
EXPECTED_CROP_MANIFEST_SHA256 = (
    "1e16f8b627f5de61d63c1fe99b3b6367e8390f4700d67938e8defe1140172542"
)
EXPECTED_THEME_SUMMARY_SHA256 = (
    "73bd7dfeb89a165db47e07750c099e289d3caa41464198ebfe2f13ef7c27fc4c"
)
EXPECTED_OUTPUT_BINDING_COUNT = 37
EXPECTED_SOURCE_BINDING_COUNT = 43
MAX_CROP_BYTES = 1024 * 1024

EXPECTED_PRINTED_IDS = tuple(f"JD2025-EM-S1-Q{index}" for index in range(1, 9))
EXPECTED_ATOMIC_IDS = (
    "JD2025-EM-S1-Q1-P1",
    "JD2025-EM-S1-Q2-P1",
    "JD2025-EM-S1-Q3-P1",
    "JD2025-EM-S1-Q4-P1",
    "JD2025-EM-S1-Q5-P1",
    "JD2025-EM-S1-Q5-P2",
    "JD2025-EM-S1-Q6-P1",
    "JD2025-EM-S1-Q7-P1",
    "JD2025-EM-S1-Q7-P2",
    "JD2025-EM-S1-Q8-P1",
)
EXPECTED_QUALITY_IDS = {
    "JD2025-EM-S1-Q5-P1",
    "JD2025-EM-S1-Q5-P2",
    "JD2025-EM-S1-Q7-P1",
    "JD2025-EM-S1-Q8-P1",
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
DETAIL_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": "2024学年高三年级第二次质量调研 化学试卷",
    "article_title_literal": "【高考二模】2025届上海市嘉定区高三二模化学试卷",
    "district_and_second_mock_basis": (
        "wechat_article_title_only_nonofficial_not_paper_face"
    ),
    "coverage_scope": "theme_1_disinfectants_only_10_atomic_not_complete_paper",
    "covered_theme_title": "消毒剂",
    "official_status": "nonofficial",
    "complete_paper_claim_allowed": False,
    "jiading_district_face_claim_allowed": False,
    "second_mock_face_claim_allowed": False,
    "official_identity_claim_allowed": False,
}
PAPER_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": DETAIL_IDENTITY_BOUNDARY["paper_face_title_literal"],
    "article_title_literal": DETAIL_IDENTITY_BOUNDARY["article_title_literal"],
    "district_and_second_mock_basis": DETAIL_IDENTITY_BOUNDARY[
        "district_and_second_mock_basis"
    ],
    "region_label": "嘉定区（仅公众号标题）",
    "paper_family": "second_mock_article_classified",
    "covered_scope": "主题一（消毒剂）",
    "complete_paper_visual_scan_claim_allowed": False,
    "jiading_district_face_claim_allowed": False,
    "second_mock_face_claim_allowed": False,
    "official_identity_claim_allowed": False,
    "official_status": "nonofficial",
    "source_account": "学教有方",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
}
_PRIVATE_TEXT = re.compile(
    r"(?i:file:/+|(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)


class Jiading2025Theme1DirectVisualScanError(MasterDirectVisualScanError):
    pass


class Jiading2025Theme1DirectVisualScanReader(
    Pudong2026FirstMockThemeDirectVisualScanReader
):
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = (self.shchem_root / PRODUCT_RELATIVE).resolve()
        if not self.product_root.is_relative_to(self.shchem_root):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_path_invalid", "product root leaves sh-chem-db"
            )
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    @staticmethod
    def _false_gates(value: Any, label: str) -> None:
        if value != EXPECTED_GATES or set(value or {}) != EXPECTED_GATE_KEYS:
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_gate_elevated", f"{label} gates drifted"
            )

    @staticmethod
    def _record_binding(record: dict[str, Any]) -> str:
        return _sha256(
            _canonical_bytes(
                {
                    "hierarchy": record["hierarchy"],
                    "viewed_evidence": record["viewed_evidence"],
                    "classification": record["classification"],
                    "dependency": record["dependency"],
                    "answer": record["answer"],
                    "difficulty": record["difficulty"],
                }
            )
        )

    def _snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if (
            len(manifest_raw) != EXPECTED_MANIFEST_BYTES
            or _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest bytes drifted"
            )
        manifest = _json_object(manifest_raw, "manifest")
        if (
            manifest.get("product_id") != PRODUCT_ID
            or manifest.get("status") != "FROZEN"
            or manifest.get("paper_id") != PAPER_ID
            or manifest.get("theme_id") != THEME_ID
            or manifest.get("coverage_scope")
            != "theme_1_disinfectants_only_10_atomic_not_complete_paper"
            or manifest.get("hierarchy_counts")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": 8,
                "atomic_part": 10,
            }
            or manifest.get("output_file_count") != EXPECTED_OUTPUT_BINDING_COUNT
            or manifest.get("total_output_bytes_excluding_manifest") != 1_605_768
            or manifest.get("self_sha256") != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_manifest_invalid", "manifest contract drifted"
            )
        self._false_gates(manifest.get("authority_gates"), "manifest")
        output_bindings = _binding_index(
            manifest.get("outputs"), "output", EXPECTED_OUTPUT_BINDING_COUNT
        )
        self._output_closure(set(output_bindings))
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        if (
            _sha256(output_bytes.get("scan_records.jsonl", b""))
            != EXPECTED_RECORDS_SHA256
            or _sha256(output_bytes.get("scan_record_schema.json", b""))
            != EXPECTED_SCHEMA_SHA256
            or _sha256(output_bytes.get("source_manifest.json", b""))
            != EXPECTED_SOURCE_MANIFEST_SHA256
            or _sha256(output_bytes.get("crop_manifest.json", b""))
            != EXPECTED_CROP_MANIFEST_SHA256
            or _sha256(output_bytes.get("theme_summary.json", b""))
            != EXPECTED_THEME_SUMMARY_SHA256
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_binding_mismatch", "principal output hash drifted"
            )

        source_manifest = _json_object(
            output_bytes["source_manifest.json"], "source manifest"
        )
        if (
            source_manifest.get("product_id") != PRODUCT_ID
            or source_manifest.get("paper_id") != PAPER_ID
            or source_manifest.get("theme_id") != THEME_ID
            or source_manifest.get("source_count") != EXPECTED_SOURCE_BINDING_COUNT
            or source_manifest.get("total_source_bytes") != 16_782_313
            or source_manifest.get("identity_boundary")
            != {
                "paper_face_title_literal": DETAIL_IDENTITY_BOUNDARY[
                    "paper_face_title_literal"
                ],
                "wechat_title_is_nonofficial_only": True,
            }
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_source_invalid", "source manifest drifted"
            )
        source_bindings = _binding_index(
            source_manifest.get("sources"), "source", EXPECTED_SOURCE_BINDING_COUNT
        )
        self._verify_bindings(self.shchem_root, source_bindings, "source")

        schema = _json_object(output_bytes["scan_record_schema.json"], "schema")
        try:
            Draft202012Validator.check_schema(schema)
            schema_validator = Draft202012Validator(schema)
        except Exception as exc:  # jsonschema exposes several validation subclasses
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_schema_invalid", "bound schema is invalid"
            ) from exc
        records = _jsonl_objects(output_bytes["scan_records.jsonl"], "records")
        if len(records) != len(EXPECTED_ATOMIC_IDS):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_count_mismatch", "record count drifted"
            )
        for index, record in enumerate(records, 1):
            if next(schema_validator.iter_errors(record), None) is not None:
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_record_invalid",
                    f"record {index} failed its bound schema",
                )

        crop_manifest = _json_object(
            output_bytes["crop_manifest.json"], "crop manifest"
        )
        crops = crop_manifest.get("crops")
        if (
            crop_manifest.get("product_id") != PRODUCT_ID
            or crop_manifest.get("crop_count") != 25
            or crop_manifest.get("question_crop_count") != 9
            or crop_manifest.get("shared_material_crop_count") != 3
            or crop_manifest.get("answer_or_context_crop_count") != 9
            or crop_manifest.get("boundary_crop_count") != 4
            or not isinstance(crops, list)
            or len(crops) != 25
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop counts drifted"
            )
        crop_by_id: dict[str, dict[str, Any]] = {}
        role_counts: Counter[str] = Counter()
        for crop in crops:
            crop_id = crop.get("crop_id") if isinstance(crop, dict) else None
            relative = crop.get("output_path") if isinstance(crop, dict) else None
            role = crop.get("role") if isinstance(crop, dict) else None
            if (
                not isinstance(crop_id, str)
                or crop_id in crop_by_id
                or not isinstance(relative, str)
                or relative not in output_bindings
                or role
                not in {
                    "question",
                    "shared_material",
                    "answer",
                    "answer_context",
                    "boundary_context",
                }
                or output_bindings[relative]["sha256"] != crop.get("sha256")
                or output_bindings[relative]["bytes"] != crop.get("bytes")
                or self._png_dimensions(output_bytes[relative])
                != (crop.get("width"), crop.get("height"))
                or crop.get("pixel_reuse_allowed") is not False
                or crop.get("publication_allowed") is not False
                or crop.get("human_verified") is not False
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop binding or gate drifted",
                )
            exposable = role in {"question", "shared_material"}
            if (
                crop.get("safe_http_status_if_routed") != (200 if exposable else 403)
                or crop.get("access_policy")
                != (
                    "internal_read_only_200_if_authorized"
                    if exposable
                    else "forbidden_403_no_publication"
                )
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_crop_role_invalid", "crop access role drifted"
                )
            role_counts[role] += 1
            crop_by_id[crop_id] = crop
        if role_counts != Counter(
            {
                "question": 9,
                "shared_material": 3,
                "answer": 8,
                "answer_context": 1,
                "boundary_context": 4,
            }
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_crop_manifest_invalid", "crop role counts drifted"
            )

        try:
            master_snapshot = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise Jiading2025Theme1DirectVisualScanError(
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
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_identity_conflict",
                "Jiading theme rows are absent from master470 or overlap exact169",
            )

        by_master_id: dict[str, dict[str, Any]] = {}
        corrected_fields: dict[str, tuple[str, ...]] = {}
        dependency_counts: Counter[str] = Counter()
        quality_ids: set[str] = set()
        answer_crop_ids: set[str] = set()
        referenced_question_ids: set[str] = set()
        referenced_shared_ids: set[str] = set()
        for expected_id, record in zip(EXPECTED_ATOMIC_IDS, records, strict=True):
            hierarchy = record.get("hierarchy")
            if (
                not isinstance(hierarchy, dict)
                or hierarchy.get("atomic_part_id") != expected_id
                or hierarchy.get("paper_id") != PAPER_ID
                or hierarchy.get("theme_id") != THEME_ID
                or hierarchy.get("theme_sequence") != 1
                or hierarchy.get("theme_title") != "消毒剂"
                or record.get("scan_status") != "visual_scan_completed"
                or record.get("visual_scan_completed") is not True
                or record.get("identity_boundary") != DETAIL_IDENTITY_BOUNDARY
                or expected_id in by_master_id
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_identity_conflict", "record identity drifted"
                )
            atom = master_snapshot.master_nodes.get(("atomic_part", expected_id))
            printed_id = hierarchy.get("printed_question_id")
            if (
                not isinstance(atom, dict)
                or atom.get("parent_paper_id") != PAPER_ID
                or atom.get("parent_theme_big_question_id") != THEME_ID
                or atom.get("parent_printed_question_id") != printed_id
                or printed_id not in EXPECTED_PRINTED_IDS
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_parent_chain_invalid", "parent chain drifted"
                )
            self._false_gates(record.get("authority_gates"), "record")

            viewed = record.get("viewed_evidence")
            if not isinstance(viewed, list) or not viewed:
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "viewed evidence is absent"
                )
            viewed_ids: set[str] = set()
            for descriptor in viewed:
                crop_id = descriptor.get("crop_id") if isinstance(descriptor, dict) else None
                crop = crop_by_id.get(crop_id)
                if (
                    crop is None
                    or crop_id in viewed_ids
                    or crop["role"] not in {"question", "shared_material"}
                    or descriptor.get("evidence_role") != crop["role"]
                    or descriptor.get("source_page") != crop["source_page_number"]
                    or any(
                        descriptor.get(key) != crop.get(key)
                        for key in ("sha256", "bytes", "width", "height")
                    )
                ):
                    raise Jiading2025Theme1DirectVisualScanError(
                        "master_direct_scan_evidence_invalid",
                        "record evidence binding drifted",
                    )
                viewed_ids.add(crop_id)
                if crop["role"] == "question":
                    referenced_question_ids.add(crop_id)
                else:
                    referenced_shared_ids.add(crop_id)

            dependency = record.get("dependency")
            dep_kind = dependency.get("dependency_kind") if isinstance(dependency, dict) else None
            shared_ids = dependency.get("shared_material_crop_ids") if isinstance(dependency, dict) else None
            prior_ids = dependency.get("prior_atomic_part_ids") if isinstance(dependency, dict) else None
            if (
                dep_kind not in {"independent", "shared_theme_context", "one_prior_part"}
                or not isinstance(shared_ids, list)
                or not isinstance(prior_ids, list)
                or not set(shared_ids) <= viewed_ids
                or any(prior not in by_master_id for prior in prior_ids)
                or (dep_kind == "independent" and (shared_ids or prior_ids))
                or (dep_kind == "shared_theme_context" and (not shared_ids or prior_ids))
                or (dep_kind == "one_prior_part" and (shared_ids or len(prior_ids) != 1))
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_dependency_invalid", "dependency edge drifted"
                )
            dependency_counts[dep_kind] += 1

            difficulty = record.get("difficulty")
            factors = difficulty.get("factors") if isinstance(difficulty, dict) else None
            if (
                not isinstance(factors, list)
                or tuple(item.get("dimension_id") for item in factors) != FACTOR_IDS
                or difficulty.get("cognitive_prelabel") not in {"D1", "D2", "D3"}
                or difficulty.get("is_measured") is not False
                or difficulty.get("measured_difficulty") is not None
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_difficulty_invalid", "difficulty contract drifted"
                )

            answer = record.get("answer")
            answer_evidence = (
                answer.get("visual_alignment_evidence")
                if isinstance(answer, dict)
                else None
            )
            if (
                not isinstance(answer, dict)
                or answer.get("availability") != "present_part_aligned"
                or answer.get("authority") != "nonofficial_reference"
                or answer.get("source_authority") != "nonofficial_reference"
                or answer.get("independently_verified") is not False
                or answer.get("answer_verified") is not False
                or answer.get("official_answer_claim_allowed") is not False
                or not isinstance(answer.get("reference_summary_zh"), str)
                or not answer["reference_summary_zh"]
                or not isinstance(answer_evidence, list)
                or len(answer_evidence) != 1
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer boundary drifted"
                )
            answer_crop_id = answer_evidence[0].get("crop_id")
            answer_crop = crop_by_id.get(answer_crop_id)
            if (
                answer_crop is None
                or answer_crop.get("role") != "answer"
                or any(
                    answer_evidence[0].get(key) != answer_crop.get(key)
                    for key in ("sha256", "bytes", "width", "height")
                )
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_answer_invalid", "answer crop binding drifted"
                )
            answer_crop_ids.add(answer_crop_id)

            candidate = record.get("candidate_analysis")
            if (
                not isinstance(candidate, dict)
                or candidate.get("candidate_only") is not True
                or candidate.get("correctness_verified") is not False
                or not isinstance(candidate.get("solution_path_zh"), list)
                or not candidate["solution_path_zh"]
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_candidate_invalid", "candidate analysis drifted"
                )
            has_quality = bool(answer.get("quality_note"))
            if has_quality:
                quality_ids.add(expected_id)
            if has_quality != bool(candidate.get("known_quality_note_code")):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_quality_note_invalid",
                    "quality-note binding drifted",
                )

            comparison = record.get("comparison_with_prior_candidate")
            if not isinstance(comparison, dict) or set(comparison) != set(COMPARISON_FIELDS):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_comparison_invalid", "comparison fields drifted"
                )
            corrected = tuple(
                field
                for field in COMPARISON_FIELDS
                if comparison[field].get("compare") == "corrected"
            )
            if any(
                comparison[field].get("compare") not in {"agree", "corrected", "blocked"}
                for field in COMPARISON_FIELDS
            ):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_comparison_invalid", "comparison state drifted"
                )
            if record.get("evidence_binding_sha256") != self._record_binding(record):
                raise Jiading2025Theme1DirectVisualScanError(
                    "master_direct_scan_evidence_invalid", "record self-binding drifted"
                )
            corrected_fields[expected_id] = corrected
            by_master_id[expected_id] = record

        if (
            dependency_counts
            != Counter({"independent": 7, "shared_theme_context": 2, "one_prior_part": 1})
            or quality_ids != EXPECTED_QUALITY_IDS
            or len(answer_crop_ids) != 8
            or len(referenced_question_ids) != 9
            or referenced_shared_ids
            != {
                "JD2025-T1-SHARED-OPENING",
                "JD2025-T1-SHARED-GRAPH-INTRO",
                "JD2025-T1-SHARED-SPECIATION-GRAPH",
            }
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_count_mismatch", "derived scan closure drifted"
            )

        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        theme_summary = _json_object(
            output_bytes["theme_summary.json"], "theme summary"
        )
        if (
            coverage.get("structure_observed")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": 8,
                "atomic_part": 10,
            }
            or coverage.get("visual_scan_complete_count") != 10
            or coverage.get("answer_aligned_count") != 10
            or coverage.get("dependency_counts")
            != {"independent": 7, "one_prior_part": 1, "shared_theme_context": 2}
            or coverage.get("all_gates_false") is not True
            or theme_summary.get("default_workbench_unit") != "theme_big_question"
            or theme_summary.get("default_workbench_unit_zh") != "主题大题"
            or theme_summary.get("hierarchy_counts")
            != {
                "paper": 1,
                "theme_big_question": 1,
                "printed_question": 8,
                "atomic_part": 10,
            }
            or theme_summary.get("context_bundle", {}).get(
                "must_render_as_one_theme_bundle"
            )
            is not True
            or len(theme_summary.get("quality_notes", ())) != 4
            or theme_summary.get("answer_policy", {}).get("independently_verified")
            is not False
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_report_invalid", "theme report drifted"
            )
        for relative, raw in output_bytes.items():
            # The frozen validator contains the literal detector string
            # ``C:\\Users\\`` as test code.  Runtime privacy projection scans
            # data/document outputs; executable sources are already byte-bound
            # by the trusted manifest and the product validator's own closure.
            if Path(relative).suffix.lower() in {".json", ".jsonl", ".md"}:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise Jiading2025Theme1DirectVisualScanError(
                        "master_direct_scan_privacy_invalid", "text output is not UTF-8"
                    ) from exc
                if _PRIVATE_TEXT.search(text):
                    raise Jiading2025Theme1DirectVisualScanError(
                        "master_direct_scan_privacy_invalid",
                        "product output contains an absolute local locator",
                    )

        forbidden_crop_ids = frozenset(
            crop_id
            for crop_id, crop in crop_by_id.items()
            if crop["role"] not in {"question", "shared_material"}
        )
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_master_id,
            crop_by_id=crop_by_id,
            output_bindings=output_bindings,
            output_bytes=output_bytes,
            answer_crop_ids=frozenset(answer_crop_ids),
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
            "direct_master_visual_scanned": 10,
            "visual_scanned_master_atomic": 179,
            "remaining_unscanned": 291,
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
            "record_count": 10,
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
                "expected_printed_questions": 8,
                "expected_atomic_parts": 10,
                "scan_records": 10,
                "visual_scan_completed": 10,
                "blocked_pending_broader_crop": 0,
                "question_crop_bindings": 9,
                "shared_crop_bindings": 3,
                "nonofficial_answer_crop_bindings": 8,
                "boundary_crop_bindings": 5,
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
                        record["candidate_analysis"].get(
                            "known_quality_note_code"
                        ),
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
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_invalid_node_id", "master node ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Jiading theme-one direct scan",
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
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "master node or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_node_not_found",
                "node has no Jiading theme-one direct scan",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        role = crop.get("role") if isinstance(crop, dict) else None
        if crop_id in snapshot.forbidden_crop_ids or role not in {
            "question",
            "shared_material",
        }:
            raise Jiading2025Theme1DirectVisualScanError(
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
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_crop_not_found", "crop is not node evidence", 404
            )
        internal = str(crop.get("output_path", ""))
        binding = snapshot.output_bindings.get(internal)
        raw = snapshot.output_bytes.get(internal)
        if (
            binding is None
            or raw is None
            or crop.get("safe_http_status_if_routed") != 200
            or binding.get("sha256") != descriptor["sha256"]
            or binding.get("bytes") != descriptor["bytes"]
            or len(raw) > MAX_CROP_BYTES
            or _sha256(raw) != descriptor["sha256"]
            or self._png_dimensions(raw)
            != (descriptor["width"], descriptor["height"])
        ):
            raise Jiading2025Theme1DirectVisualScanError(
                "master_direct_scan_crop_binding_invalid", "served crop binding drifted"
            )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


__all__ = [
    "AUTHORITY",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "Jiading2025Theme1DirectVisualScanError",
    "Jiading2025Theme1DirectVisualScanReader",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "THEME_ID",
]
