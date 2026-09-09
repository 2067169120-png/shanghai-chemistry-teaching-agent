"""Read-only Master preview of the existing Shanghai High East T4/T5 package.

No scan product is written. The adapter projects the pinned pending_v2 source
records in memory and retains unknown labels, prior dependencies, and closed
authority gates. Crop roles come only from explicit, cross-checked references.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .archived_wechat_crop_revision import (
    project_archived_wechat_descriptor,
    recrop_archived_wechat_view,
)
from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import MasterDirectVisualScanError
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .pudong2026_first_mock_theme_direct_visual_scan import (
    AUTHORITY as BASE_AUTHORITY,
)
from .pudong2026_first_mock_theme_direct_visual_scan import (
    Pudong2026FirstMockThemeDirectVisualScanReader,
    _json_object,
    _jsonl_objects,
    _sha256,
    _Snapshot,
)
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier

PRODUCT_ID = "FORMAL-PENDING-V2-2025-SH-EAST-G2-M05-B-T4-T5-A"
PRODUCT_RELATIVE = Path(
    "kb/formal/candidates/pending_v2/shanghai_high_east_2025_may_theme4_5"
)
PAPER_ID = "PAPER-7e614ffeafa24f280664"
SOURCE_ID = "file-fbe3e524e89098728cd5c1bee9ce5fdc67c0b013989420c1d893402761807305"
THEME_IDS = {4: "THEME-576211e080608c69578d", 5: "THEME-aa5b660ca407ced6230f"}
EXPECTED_PRINTED_IDS = tuple(
    [f"SHEAST2025-M05-B-T4-Q{n}" for n in range(1, 7)]
    + [f"SHEAST2025-M05-B-T5-Q{n}" for n in range(1, 6)]
)
EXPECTED_ATOMIC_IDS = tuple(f"{value}-P1" for value in EXPECTED_PRINTED_IDS)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "7e4ac0e7822b3a38160c0ed8a8331c386a8e721cee33f4fd5e2b7b1d3209bd6d"
)
EXPECTED_RECORDS_SHA256 = (
    "26d768d19c0e2fe4778ca93722c4cfeb113b438c4d494129031ff35a2f910e72"
)
EXPECTED_EVIDENCE_SHA256 = (
    "794f94280552114de40efbfd8fe1e11e906dfe72c7a7de9a81e5552d2642551d"
)
DEPENDENCY_REVISION_ID = "sheast2025-theme5-source-dependencies-20260910-r1"
DEPENDENCY_STATUS = "source_page_backed_candidate_dependency"
_THEME5_SOURCE_SHA256 = (
    "f3ced481f510cf22e9b3936efc1a1de13eb015c2b672fabb2fbabaa56ff996a6"
)
_THEME5_SHARED_SHA256 = (
    "5e6178edcc30dc4026a0442f7830433f492303a5c48cf3b99e3df19a587d064d"
)
# The entire A-J route was inspected on source page 5. E and I are given
# intermediate labels with structures to infer, not absent prior questions.
_THEME5_DEPENDENCY_ANALYSIS = {
    "SHEAST2025-M05-B-T5-Q1-P1": "由完整路线中F、G结构判断氢谱信号与F到G反应类型，不使用前题答案。",
    "SHEAST2025-M05-B-T5-Q2-P1": "比较路线中A与本题自带对位羟基苯甲酸结构；两处结构都保留，不使用第1题答案。",
    "SHEAST2025-M05-B-T5-Q3-P1": "由完整路线中D到E条件推断E，再书写E到F反应；E为源题留待推断的中间体，不依赖第1或2题答案。",
    "SHEAST2025-M05-B-T5-Q4-P1": "I到J需完整保留H经CH3I到I、再经NaOH/C2H5OH和酸化到J的路线；I字母在原图中，结构需推断，不依赖第3题答案。",
    "SHEAST2025-M05-B-T5-Q5-P1": "题面明确参照以上合成路线，并自带起始物和目标结构；保留完整A到J路线及本题结构，不使用前4题答案。",
}
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
MAX_CROP_BYTES = 1024 * 1024
AUTHORITY = {
    **BASE_AUTHORITY,
    "diagnosis_allowed": False,
    "formal_promotion_allowed": False,
    "component_reuse_allowed": False,
    "direct_source_pixel_reuse_allowed": False,
    "reuse_allowed": False,
}
_LOCKED = frozenset(
    {
        "human_checked",
        "human_verified",
        "teacher_or_human",
        "formal_promotion_allowed",
        "retrieval_ready",
        "diagnosis_allowed",
        "generation_allowed",
        "reuse_allowed",
        "publication_allowed",
        "direct_source_pixel_reuse_allowed",
        "source_pixel_reuse_allowed",
        "component_reuse_allowed",
        "official_answer_claim_allowed",
        "official_scoring_claim_allowed",
        "represents_high3_level_exam_overall_format",
    }
)


class ShanghaiHighEast2025Theme45DirectVisualScanError(MasterDirectVisualScanError):
    pass


def _fail(code: str, message: str, status: int = 409) -> None:
    raise ShanghaiHighEast2025Theme45DirectVisualScanError(code, message, status)


def _closed(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _LOCKED and nested is not False:
                _fail(
                    "master_direct_scan_gate_elevated",
                    "candidate authority gate drifted",
                )
            _closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _closed(nested)


def _crop_id(asset: str) -> str:
    # A transport identifier only: role and parentage never come from the name.
    return "SHEAST2025-CROP-" + _sha256(asset.encode("utf-8"))[:24]


def _theme5_source_dependency(master_id, question_crop, shared):
    if (
        master_id not in _THEME5_DEPENDENCY_ANALYSIS
        or shared["sha256"] != _THEME5_SHARED_SHA256
        or any(
            crop["page_number"] != 5 or crop["source_sha256"] != _THEME5_SOURCE_SHA256
            for crop in (question_crop, shared)
        )
    ):
        _fail(
            "master_direct_scan_dependency_evidence_invalid",
            "dependency source differs from the inspected complete route and question",
        )
    return {
        "dependency_kind": "shared_theme_context",
        "status": DEPENDENCY_STATUS,
        "prior_atomic_part_ids": [],
        "shared_material_crop_ids": [shared["crop_id"]],
        "conclusion_use_zh": "不使用前题结论；必须保留完整A到J合成路线及本题自带结构。",
        "analysis_zh": _THEME5_DEPENDENCY_ANALYSIS[master_id],
    }, {
        "revision_id": DEPENDENCY_REVISION_ID,
        "status": "source_page_visual_inspection_candidate",
        "candidate_only": True,
        "human_checked": False,
        "source_page_bindings": [{"page": 5, "sha256": _THEME5_SOURCE_SHA256}],
        "required_shared_material_crop_ids": [shared["crop_id"]],
    }


class ShanghaiHighEast2025Theme45DirectVisualScanReader(
    Pudong2026FirstMockThemeDirectVisualScanReader
):
    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.product_root = self.shchem_root / PRODUCT_RELATIVE
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _bound(self, asset: str, digest: str) -> bytes:
        _, raw = self._verified_file(self.shchem_root, asset)
        if _sha256(raw) != digest:
            _fail(
                "master_direct_scan_binding_mismatch",
                "bound candidate or source file changed",
            )
        return raw

    def _inputs(self):
        prefix = PRODUCT_RELATIVE.as_posix() + "/"
        manifest_raw = self._bound(
            prefix + "candidate_manifest.json", EXPECTED_MANIFEST_FILE_SHA256
        )
        manifest = _json_object(manifest_raw, "candidate manifest")
        if (
            manifest.get("batch_id") != PRODUCT_ID
            or manifest.get("source_id") != SOURCE_ID
            or manifest.get("package_path") != PRODUCT_RELATIVE.as_posix()
            or manifest.get("question_count") != 11
            or manifest.get("part_count") != 11
            or manifest.get("local_crop_count") != 28
            or manifest.get("theme_counts") != {"theme4": 6, "theme5": 5}
            or manifest.get("answer_authority") != "nonofficial_reference_only"
            or manifest.get("points_and_stepwise_rubric_status") != "absent"
        ):
            _fail(
                "master_direct_scan_manifest_invalid",
                "candidate package identity or count drifted",
            )
        outputs = {}
        for field, filename, digest in (
            ("candidate_file", "question_candidates.jsonl", EXPECTED_RECORDS_SHA256),
            ("evidence_map_file", "evidence_map.json", EXPECTED_EVIDENCE_SHA256),
        ):
            hash_field = (
                "candidate_file_sha256"
                if field == "candidate_file"
                else "evidence_map_sha256"
            )
            if (
                manifest.get(field) != prefix + filename
                or manifest.get(hash_field) != digest
            ):
                _fail(
                    "master_direct_scan_manifest_invalid",
                    "candidate document binding drifted",
                )
            outputs[prefix + filename] = self._bound(prefix + filename, digest)
        records = _jsonl_objects(
            outputs[prefix + "question_candidates.jsonl"], "candidates"
        )
        evidence = _json_object(outputs[prefix + "evidence_map.json"], "evidence map")
        _closed([manifest, records, evidence])
        source_bytes = {}
        for item in manifest.get("source_assets", []):
            asset = item["asset"]
            if asset in source_bytes:
                _fail("master_direct_scan_binding_invalid", "duplicate source page")
            source_bytes[asset] = self._bound(asset, item["sha256"])
        if len(source_bytes) != 3:
            _fail("master_direct_scan_count_mismatch", "source page count drifted")
        for asset_field, hash_field in (
            ("shared_schema", "shared_schema_sha256"),
            ("taxonomy_asset", "taxonomy_sha256"),
            ("controlled_vocabulary_asset", "controlled_vocabulary_sha256"),
        ):
            source_bytes[manifest[asset_field]] = self._bound(
                manifest[asset_field], manifest[hash_field]
            )
        schema = _json_object(
            source_bytes[manifest["shared_schema"]], "candidate schema"
        )
        validator = Draft202012Validator(schema)
        if (
            len(records) != 11
            or any(next(validator.iter_errors(row), None) for row in records)
            or [row["question_id"] for row in records] != list(EXPECTED_PRINTED_IDS)
            or [part["part_id"] for row in records for part in row["parts"]]
            != list(EXPECTED_ATOMIC_IDS)
            or any(row["source_id"] != SOURCE_ID for row in records)
            or evidence.get("source_id") != SOURCE_ID
            or evidence.get("batch_id") != PRODUCT_ID
        ):
            _fail(
                "master_direct_scan_record_invalid",
                "candidate schema, order or identity drifted",
            )
        crops = {}
        for crop in manifest.get("local_crops", []):
            asset = crop["asset"]
            if (
                not asset.startswith(prefix + "evidence/")
                or asset in crops
                or crop["source_asset"] not in source_bytes
                or _sha256(source_bytes[crop["source_asset"]]) != crop["source_sha256"]
            ):
                _fail(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop source binding drifted",
                )
            raw = self._bound(asset, crop["sha256"])
            box = crop.get("crop_box")
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(value) is not int or value < 0 for value in box)
                or self._png_dimensions(raw) != tuple(box[2:])
                or len(raw) > MAX_CROP_BYTES
            ):
                _fail(
                    "master_direct_scan_crop_manifest_invalid",
                    "crop dimensions or bytes invalid",
                )
            outputs[asset] = raw
            crops[asset] = {
                **deepcopy(crop),
                "crop_id": _crop_id(asset),
                "role": "unknown",
                "output_path": asset,
                "bytes": len(raw),
                "width": box[2],
                "height": box[3],
                "source_page_number": crop["page_number"],
                "safe_http_status_if_routed": 403,
            }
        if len(crops) != 28:
            _fail("master_direct_scan_count_mismatch", "crop count drifted")
        return manifest, records, evidence, outputs, crops

    @staticmethod
    def _check_reference(ref, crop):
        if (
            not isinstance(ref, dict)
            or ref.get("crop_asset") != crop["asset"]
            or ref.get("crop_sha256") != crop["sha256"]
            or ref.get("asset") != crop["source_asset"]
            or ref.get("source_sha256") != crop["source_sha256"]
            or ref.get("page_number") != crop["page_number"]
            or ref.get("crop_box") != crop["crop_box"]
        ):
            _fail(
                "master_direct_scan_evidence_invalid",
                "explicit source and crop reference disagree",
            )

    def _relations(self, records, evidence, crops):
        relations = evidence.get("question_evidence", [])
        types = evidence.get("item_type_contract", {}).get("entries", [])
        if (
            len(relations) != 11
            or len(types) != 11
            or [value.get("part_id") for value in relations]
            != list(EXPECTED_ATOMIC_IDS)
            or [value.get("part_id") for value in types] != list(EXPECTED_ATOMIC_IDS)
        ):
            _fail(
                "master_direct_scan_evidence_invalid",
                "explicit per-part evidence coverage drifted",
            )
        for row, relation, item_type in zip(records, relations, types, strict=True):
            part = row["parts"][0]
            if (
                relation.get("question_id") != row["question_id"]
                or item_type.get("question_id") != row["question_id"]
                or relation.get("theme") not in THEME_IDS
                or item_type.get("evidence_refs") != [relation.get("question_crop")]
            ):
                _fail(
                    "master_direct_scan_evidence_invalid",
                    "per-part source relationship drifted",
                )
            refs = row["source_locator"]["page_refs"]
            by_asset = {ref.get("crop_asset"): ref for ref in refs}
            if len(by_asset) != len(refs):
                _fail(
                    "master_direct_scan_evidence_invalid",
                    "duplicate per-part crop reference",
                )
            for asset, ref in by_asset.items():
                if asset not in crops:
                    _fail(
                        "master_direct_scan_evidence_invalid",
                        "unbound per-part crop reference",
                    )
                self._check_reference(ref, crops[asset])
            for field, source_role, role in (
                ("question_crop", "question", "question"),
                ("answer_crop", "answer", "answer"),
                ("shared_visual_ref", "context", "shared_material"),
            ):
                asset = relation.get(field)
                ref, crop = by_asset.get(asset), crops.get(asset)
                if (
                    ref is None
                    or crop is None
                    or ref.get("role") != source_role
                    or crop["role"] not in {"unknown", role}
                ):
                    _fail(
                        "master_direct_scan_crop_role_invalid",
                        "explicit question/shared/answer role drifted",
                    )
                crop["role"] = role
                crop["safe_http_status_if_routed"] = 200 if role != "answer" else 403
            shared_refs = [
                asset
                for block in row["stimulus_blocks"]
                for asset in block["image_refs"]
            ]
            if shared_refs != [relation["shared_visual_ref"]]:
                _fail(
                    "master_direct_scan_dependency_invalid",
                    "shared stimulus relationship drifted",
                )
            answer_refs = part["answer_evidence"]["source_page_refs"]
            if (
                len(answer_refs) != 1
                or answer_refs[0] != by_asset[relation["answer_crop"]]
            ):
                _fail(
                    "master_direct_scan_answer_invalid",
                    "answer source is not explicitly part-aligned",
                )
        if Counter(crop["role"] for crop in crops.values()) != {
            "question": 11,
            "shared_material": 2,
            "answer": 11,
            "unknown": 4,
        }:
            _fail(
                "master_direct_scan_count_mismatch",
                "explicit crop role closure drifted",
            )
        return relations, types

    def _master_snapshot(self):
        try:
            snapshot = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise ShanghaiHighEast2025Theme45DirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified Master identity unavailable",
            ) from exc
        master_ids = {
            row["atomic_part_id"] for row in snapshot.master_layers["atomic_part"]
        }
        exact_ids = {
            key
            for key, values in snapshot.relations_by_master.items()
            if len(values) == 1
            and values[0].get("relation_type") == "exact_1_to_1"
            and values[0].get("identity_mapping_allowed") is True
            and values[0].get("endpoint_cardinality")
            == {
                "master_anchor_endpoints_for_strong_key": 0,
                "master_atomic_endpoints_for_strong_key": 1,
                "wave_rows_for_strong_key": 1,
            }
        }
        if (
            len(master_ids) != 470
            or len(exact_ids) != 169
            or not exact_ids <= master_ids
            or not set(EXPECTED_ATOMIC_IDS) <= master_ids
            or set(EXPECTED_ATOMIC_IDS) & exact_ids
        ):
            _fail(
                "master_direct_scan_identity_conflict",
                "candidate IDs absent from Master or overlap exact scan",
            )
        return snapshot

    @staticmethod
    def _descriptor(crop):
        return {
            "crop_id": crop["crop_id"],
            "evidence_role": crop["role"],
            "source_page": crop["page_number"],
            **{key: crop[key] for key in ("sha256", "bytes", "width", "height")},
        }

    def _project(self, row, relation, item_type, crops, master):
        part = row["parts"][0]
        master_id, question_id = part["part_id"], row["question_id"]
        atom = master.master_nodes.get(("atomic_part", master_id), {})
        theme_id = THEME_IDS[relation["theme"]]
        theme = master.master_nodes.get(("theme_big_question", theme_id), {})
        printed = master.master_nodes.get(("printed_question", question_id), {})
        if (
            atom.get("source_id") != SOURCE_ID
            or atom.get("parent_paper_id") != PAPER_ID
            or atom.get("parent_theme_big_question_id") != theme_id
            or atom.get("parent_printed_question_id") != question_id
            or printed.get("parent_paper_id") != PAPER_ID
            or printed.get("parent_theme_big_question_id") != theme_id
            or printed.get("printed_question_number_literal") != part["printed_number"]
            or printed.get("printed_question_order", {}).get("value")
            != int(part["printed_number"])
            or atom.get("atomic_part_order", {}).get("value") != 1
            or theme.get("parent_paper_id") != PAPER_ID
            or theme.get("source_id") != SOURCE_ID
            or theme.get("theme_order", {}).get("value") != relation["theme"]
            or theme.get("theme_title", {}).get("value")
            != row["source_locator"]["printed_theme_title"]
        ):
            _fail(
                "master_direct_scan_parent_chain_invalid",
                "explicit Master/source parent chain drifted",
            )
        classification = part["classification"]
        difficulty = deepcopy(part["difficulty"])
        if (
            difficulty.get("cognitive_prelabel") is not None
            or difficulty.get("is_measured_difficulty") is not False
            or difficulty.get("incomplete_factor_ids") != ["unfamiliarity"]
        ):
            _fail(
                "master_direct_scan_difficulty_invalid",
                "unknown difficulty was promoted",
            )
        answer = part["answer_evidence"]
        if (
            answer["authority"] != "nonofficial_reference"
            or answer["reference_points"] is not None
            or answer["evidence_status"] != "source_page_verified_and_part_aligned"
        ):
            _fail(
                "master_direct_scan_answer_invalid",
                "candidate answer authority drifted",
            )
        quality = list(part["symbol_validation"]["notes"])
        quality.extend(
            conflict["resolution_note"]
            for conflict in row["source_conflicts_and_boundaries"]
            if conflict["resolution_status"] == "unresolved_keep_all"
        )
        shared = crops[relation["shared_visual_ref"]]
        dependency = {
            "dependency_kind": "unknown",
            "status": "unknown_prior_dependency_not_recorded",
            "prior_atomic_part_ids": [],
            "shared_material_crop_ids": [shared["crop_id"]],
            "analysis_zh": "已显式关联本主题公共材料；源候选未记录题间依赖边，前题结论依赖仍待核对，不能据题号或共享图推断独立。",
        }
        dependency_evidence = None
        if relation["theme"] == 5:
            dependency, dependency_evidence = _theme5_source_dependency(
                master_id, crops[relation["question_crop"]], shared
            )
        return {
            "scan_id": master_id,
            "scan_status": "visual_scan_completed",
            "visual_scan_completed": True,
            "projection_source": "frozen_pending_v2_candidate_not_new_visual_review",
            "source_identity": {
                "source_id": SOURCE_ID,
                **{
                    key: deepcopy(row["provenance"][key])
                    for key in (
                        "year",
                        "region_or_school",
                        "paper_type",
                        "source_account",
                        "source_url",
                        "official_status",
                    )
                },
            },
            "hierarchy": {
                "paper_id": PAPER_ID,
                "theme_id": theme_id,
                "theme_sequence": relation["theme"],
                "theme_title": row["source_locator"]["printed_theme_title"],
                "printed_question_id": question_id,
                "printed_question_number": part["printed_number"],
                "printed_sequence": printed["printed_question_order"]["value"],
                "atomic_part_id": master_id,
                "atomic_sequence_in_printed": atom["atomic_part_order"]["value"],
            },
            "visible_summary_zh": part["prompt_raw"],
            "response_requirement_zh": part["prompt_normalized"],
            "theme_chain_role": {"role_zh": None, "status": "unknown"},
            "classification": {
                "item_type": item_type["item_type"],
                "selection_rule": part["selection_rule"],
                "primary_K": None,
                "supporting_K": [],
                "knowledge_candidates": deepcopy(classification["knowledge_K"]),
                "knowledge_candidates_K": [
                    value["id"] for value in classification["knowledge_K"]
                ],
                "label_status": "partial_source_candidate",
                **{
                    axis: [value["id"] for value in classification[field]]
                    for axis, field in (
                        ("A", "ability_A"),
                        ("C", "context_C"),
                        ("R", "response_R"),
                        ("RP", "representation_RP"),
                    )
                },
            },
            "difficulty": difficulty,
            "dependency": dependency,
            **(
                {"dependency_evidence": dependency_evidence}
                if dependency_evidence
                else {}
            ),
            "shared_stimulus_text_zh": [
                block["raw_text"] for block in row["stimulus_blocks"]
            ],
            "shared_materials": [
                {
                    "block_id": block["block_id"],
                    "raw_text": block["raw_text"],
                    "normalized_text": block["normalized_text"],
                    "crop_ids": [
                        crops[asset]["crop_id"] for asset in block["image_refs"]
                    ],
                }
                for block in row["stimulus_blocks"]
            ],
            "viewed_evidence": [
                self._descriptor(crops[relation[field]])
                for field in ("question_crop", "shared_visual_ref")
            ],
            "answer": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "reference_summary_zh": answer["answer_text"],
                "independently_verified": False,
                "quality_note": answer["note"],
                "reference_points": None,
                "rubric_status": "absent_no_stepwise_rubric",
                "visual_alignment_evidence": [
                    self._descriptor(crops[relation["answer_crop"]])
                ],
            },
            "candidate_analysis": {
                "candidate_only": True,
                "correctness_verified": False,
                "solution_path_zh": [],
            },
            "risks_and_limits": {
                "ambiguity_or_multiple_solutions_zh": list(dict.fromkeys(quality))
            },
            "comparison_with_prior_candidate": {},
            "chemistry_observations": [],
            "authority_gates": dict(AUTHORITY),
        }

    def _snapshot(self) -> _Snapshot:
        manifest, source_records, evidence, outputs, crops = self._inputs()
        relations, types = self._relations(source_records, evidence, crops)
        master = self._master_snapshot()
        records = tuple(
            self._project(row, relation, kind, crops, master)
            for row, relation, kind in zip(
                source_records, relations, types, strict=True
            )
        )
        crop_by_id = {crop["crop_id"]: crop for crop in crops.values()}
        return _Snapshot(
            records=records,
            by_master_id={row["hierarchy"]["atomic_part_id"]: row for row in records},
            crop_by_id=crop_by_id,
            output_bytes=outputs,
            output_bindings={
                asset: {"path": asset, "sha256": _sha256(raw), "bytes": len(raw)}
                for asset, raw in outputs.items()
            },
            answer_crop_ids=frozenset(
                key for key, crop in crop_by_id.items() if crop["role"] == "answer"
            ),
            forbidden_crop_ids=frozenset(
                key
                for key, crop in crop_by_id.items()
                if crop["role"] not in {"question", "shared_material"}
            ),
            corrected_fields_by_master={key: () for key in EXPECTED_ATOMIC_IDS},
            manifest_self_sha256="",  # Upstream manifest has no self-hash field; never invent one.
            master_crosswalk_manifest_self_sha256=master.manifest_self_sha256,
            paper_identity_boundary={
                "region_label": "上海中学东校（高二）",
                "paper_family": "school_exam",
                "covered_scope": "2025年5月B卷主题四、五（11个最小作答单元）",
                "official_status": "nonofficial",
                "source_account": "申教在线",
                "grade": manifest["grade_scope"]["grade"],
                "answer_authority": "nonofficial_reference",
                "answer_independently_verified": False,
                "complete_paper_visual_scan_claim_allowed": False,
                "represents_high3_level_exam_overall_format": False,
            },
        )

    @staticmethod
    def _coverage():
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 11,
            "visual_scanned_master_atomic": 180,
            "remaining_unscanned": 290,
            "direct_exact_overlap": 0,
        }

    @staticmethod
    def _integrity(snapshot):
        return {
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "manifest_self_sha256": None,
            "manifest_kind": "upstream_candidate_manifest_without_self_hash",
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "output_binding_count": len(snapshot.output_bindings),
            "source_binding_count": 6,
            "record_count": 11,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
            "new_visual_or_human_review_performed": False,
        }

    def catalog(self):
        snapshot = self._snapshot()
        items = [
            {
                "master_node_id": row["hierarchy"]["atomic_part_id"],
                "scan_status": row["scan_status"],
                "detail_available": True,
                "question_evidence_count": 1,
                "shared_evidence_count": 1,
                "question_pixels_available": True,
                "corrected_fields": [],
                **reference_answer_catalog_metadata(
                    row["answer"], row["risks_and_limits"]
                ),
            }
            for row in snapshot.records
        ]
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "items": items,
            "master_node_ids": [item["master_node_id"] for item in items],
            "count": len(items),
            "coverage": self._coverage(),
            "paper_identity_boundary": deepcopy(snapshot.paper_identity_boundary),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def status(self):
        catalog = self.catalog()
        return {
            **{
                key: value
                for key, value in catalog.items()
                if key not in {"items", "master_node_ids", "count"}
            },
            "status": "visual_scan_completed",
            "counts": {
                "expected_themes": 2,
                "expected_printed_questions": 11,
                "expected_atomic_parts": 11,
                "scan_records": 11,
                "visual_scan_completed": 11,
                "question_crop_bindings": 11,
                "shared_crop_bindings": 2,
                "nonofficial_answer_crop_bindings": 11,
                "unknown_or_boundary_crop_bindings": 4,
                "total_exact_crop_bindings": 28,
            },
        }

    @staticmethod
    def _identifier(value, label):
        try:
            validate_identifier(value, label)
        except SecurityError as exc:
            raise ShanghaiHighEast2025Theme45DirectVisualScanError(
                "master_direct_scan_invalid_identifier",
                "node or crop identifier invalid",
                400,
            ) from exc

    def detail(self, master_node_id: str):
        self._identifier(master_node_id, "master_node_id")
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            _fail(
                "master_direct_scan_node_not_found",
                "node has no Shanghai High East T4/T5 candidate preview",
                404,
            )
        answer = project_reference_answer(record["answer"], record["risks_and_limits"])
        answer["quality_note"] = record["answer"]["quality_note"]
        answer["reference_points"] = record["answer"]["reference_points"]
        answer["rubric_status"] = record["answer"]["rubric_status"]
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": record["scan_id"],
            "scan_status": record["scan_status"],
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(snapshot.paper_identity_boundary),
            **(
                {"dependency_evidence": deepcopy(record["dependency_evidence"])}
                if "dependency_evidence" in record
                else {}
            ),
            **{
                key: deepcopy(record[key])
                for key in (
                    "visible_summary_zh",
                    "response_requirement_zh",
                    "theme_chain_role",
                    "dependency",
                    "shared_stimulus_text_zh",
                    "shared_materials",
                    "source_identity",
                    "candidate_analysis",
                    "risks_and_limits",
                    "chemistry_observations",
                    "comparison_with_prior_candidate",
                    "projection_source",
                )
            },
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "evidence_descriptors": [
                {
                    **project_archived_wechat_descriptor(
                        self.shchem_root, master_node_id, item
                    ),
                    "content_type": "image/png",
                    "access": "teacher_loopback_read_only",
                }
                for item in record["viewed_evidence"]
            ],
            "answer_boundary": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "verified": False,
            },
            "answer_source_evidence": [
                {
                    **deepcopy(item),
                    "access": "source_binding_only_pixel_route_forbidden",
                }
                for item in record["answer"]["visual_alignment_evidence"]
            ],
            "reference_answer": answer,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
        self._identifier(master_node_id, "master_node_id")
        self._identifier(crop_id, "crop_id")
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            _fail(
                "master_direct_scan_node_not_found",
                "node has no Shanghai High East T4/T5 candidate preview",
                404,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        if crop is not None and crop_id in snapshot.forbidden_crop_ids:
            _fail(
                "master_direct_scan_crop_role_denied",
                "answer and unrelated context crops are not question previews",
                403,
            )
        descriptor = next(
            (item for item in record["viewed_evidence"] if item["crop_id"] == crop_id),
            None,
        )
        if crop is None or descriptor is None:
            _fail(
                "master_direct_scan_crop_not_found",
                "crop does not belong to this node",
                404,
            )
        raw = snapshot.output_bytes[crop["output_path"]]
        if (
            crop["role"] not in {"question", "shared_material"}
            or descriptor["evidence_role"] != crop["role"]
            or _sha256(raw) != descriptor["sha256"]
            or descriptor["sha256"] != crop["sha256"]
            or len(raw) != descriptor["bytes"]
            or len(raw) != crop["bytes"]
            or self._png_dimensions(raw) != (descriptor["width"], descriptor["height"])
        ):
            _fail(
                "master_direct_scan_binding_mismatch",
                "question preview no longer matches the verified crop",
            )
        data = recrop_archived_wechat_view(
            self.shchem_root, master_node_id, crop_id, raw
        )
        return CandidateCropPayload(data=data, sha256=_sha256(data))


__all__ = [
    "ShanghaiHighEast2025Theme45DirectVisualScanError",
    "ShanghaiHighEast2025Theme45DirectVisualScanReader",
]
