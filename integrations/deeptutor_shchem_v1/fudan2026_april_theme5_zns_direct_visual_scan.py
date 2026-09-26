"""Native, source-bound preview of the archived Fudan April ZnS theme.

The eight printed questions remain eight questions. Q41 has two answer units
sharing one question image, with separate 1 + 2 reference points. Source files
and candidate records are never rewritten. Display windows are source-page
rectangles, not a claim that the old overlapping crops were clean.
"""

from __future__ import annotations

import io
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from PIL import Image

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

PRODUCT_ID = "FORMAL-PENDING-V2-FUDAN-2026-APRIL-S5-ZNS-A"
PRODUCT_RELATIVE = Path("kb/formal/candidates/pending_v2/fudan_2026_april_theme5_zns")
PAPER_ID = "PAPER-38b3a716c15a47a47867"
THEME_ID = "THEME-74817cd910c391780050"
SOURCE_ID = "file-63555539d8297136f155940f64eefc964a237675ca5440cc16c4e8a5d1045e61"
EXPECTED_PRINTED_IDS = tuple(f"FD2026-APR-S5-Q{n}" for n in range(40, 48))
EXPECTED_ATOMIC_IDS = tuple(
    f"{question}-P{part}"
    for question in EXPECTED_PRINTED_IDS
    for part in ((1, 2) if question == EXPECTED_PRINTED_IDS[1] else (1,))
)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "619a0e1c5bdab0c88c0b2c5bbb5d2d9b497c9595f59e6a80afc35186f91b39e1"
)
EXPECTED_RECORDS_SHA256 = (
    "a7c1c184aeadb88fcbb9b03985c0bcca079d118f18f9919349c616df5fc58e40"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
SOURCE_BOUNDARY_ZH = (
    "复旦附中2026届高三四月阶段检测 · 主题五ZnS · 公众号转载及非官方参考答案"
)
PRESENTATION_REVISION_ID = "fudan-zns-source-windows-20260912-r1"
MAX_CROP_BYTES = 1024 * 1024
AUTHORITY = {
    **BASE_AUTHORITY,
    "diagnosis_allowed": False,
    "formal_promotion_allowed": False,
    "component_reuse_allowed": False,
    "direct_source_pixel_reuse_allowed": False,
}
PAPER_IDENTITY_BOUNDARY = {
    "region_label": "上海市",
    "year": 2026,
    "grade": "高三",
    "paper_family": "school_exam",
    "paper_type": "2026届高三第二学期化学四月阶段检测（卷面）",
    "covered_scope": "主题五ZnS的制备与应用探究，8个印刷小题/9个作答单元",
    "official_status": "nonofficial",
    "source_account": "化学小助手a",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
    "complete_paper_visual_scan_claim_allowed": False,
    "official_identity_claim_allowed": False,
}
_LOCKED = frozenset(
    {
        "human_checked",
        "human_verified",
        "human_review_complete",
        "human_chemistry_review_complete",
        "formal_promotion_allowed",
        "retrieval_ready",
        "diagnosis_allowed",
        "generation_allowed",
        "publication_allowed",
        "direct_pixel_reuse_allowed",
        "direct_source_pixel_reuse_allowed",
        "component_reuse_allowed",
        "official_claim_allowed",
        "official_answer_claim_allowed",
        "official_scoring_claim_allowed",
    }
)

# Each selector names an explicit source_locator/stimulus-block relation. These
# rectangles were independently inspected on the four complete source pages on
# 2026-09-12; they do not derive roles from filenames or silently recrop archives.
_QUESTION_REF_INDEX = (1, 1, 1, 1, 1, 1, 0, 0)
_CONTEXT_BLOCK = (
    "PREP",
    "PREP",
    "PREP",
    "RECOVERY",
    "RECOVERY",
    "OPTICAL",
    "BATTERY",
    "BATTERY",
)
_DISPLAY_WINDOWS: dict[str, tuple[int, int, int, int]] = {
    "q40": (190, 1395, 1160, 41),
    "q41": (190, 1437, 1160, 82),
    "q42": (190, 1518, 1160, 41),
    "q43": (190, 1722, 1160, 41),
    "q44": (190, 458, 1160, 41),
    "q45": (190, 943, 1160, 205),
    "q46": (190, 1227, 1160, 41),
    "q47": (190, 1269, 1160, 41),
    "preparation": (190, 906, 1160, 493),
    "recovery": (190, 1560, 1160, 163),
    "recovery-graph": (190, 180, 380, 275),
    "optical": (190, 498, 1160, 432),
    "battery": (190, 1186, 1160, 40),
    "battery-crystals": (190, 1310, 810, 245),
    "a40": (200, 1444, 1140, 81),
    "a41": (200, 1528, 1140, 361),
    "a42": (200, 1893, 1140, 36),
    "a43": (200, 1933, 1140, 40),
    "a44": (200, 171, 1140, 80),
    "a45": (200, 255, 1140, 34),
    "a46": (200, 294, 1140, 35),
    "a47": (200, 335, 1140, 38),
}


class Fudan2026AprilTheme5ZnsDirectVisualScanError(MasterDirectVisualScanError):
    pass


def _require(condition: bool, code: str, message: str, status: int = 409) -> None:
    if not condition:
        raise Fudan2026AprilTheme5ZnsDirectVisualScanError(code, message, status)


def _closed(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            _require(
                key not in _LOCKED or nested is False,
                "master_direct_scan_gate_elevated",
                "candidate authority gate drifted",
            )
            _closed(nested)
    elif isinstance(value, list):
        for nested in value:
            _closed(nested)


class Fudan2026AprilTheme5ZnsDirectVisualScanReader(
    Pudong2026FirstMockThemeDirectVisualScanReader
):
    def __init__(self, shchem_root: Path, master_workbench=None):
        self.shchem_root = shchem_root.resolve()
        self.product_root = self.shchem_root / PRODUCT_RELATIVE
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _bound(self, asset, digest):
        _, raw = self._verified_file(self.shchem_root, asset)
        _require(
            _sha256(raw) == digest,
            "master_direct_scan_binding_mismatch",
            "bound source or candidate file changed",
        )
        return raw

    def _inputs(self):
        prefix = PRODUCT_RELATIVE.as_posix() + "/"
        manifest = _json_object(
            self._bound(
                prefix + "candidate_manifest.json", EXPECTED_MANIFEST_FILE_SHA256
            ),
            "manifest",
        )
        _require(
            manifest.get("batch_id") == PRODUCT_ID
            and manifest.get("source_id") == SOURCE_ID
            and manifest.get("package_path") == PRODUCT_RELATIVE.as_posix()
            and manifest.get("candidate_file") == prefix + "question_candidates.jsonl"
            and manifest.get("candidate_file_sha256") == EXPECTED_RECORDS_SHA256
            and manifest.get("candidate_count") == 8
            and manifest.get("part_count") == 9
            and manifest.get("theme_printed_points") == 17,
            "master_direct_scan_manifest_invalid",
            "frozen package identity drifted",
        )
        records = _jsonl_objects(
            self._bound(manifest["candidate_file"], EXPECTED_RECORDS_SHA256), "records"
        )
        _closed([manifest, records])
        _require(
            [row.get("question_id") for row in records] == list(EXPECTED_PRINTED_IDS)
            and all(row.get("source_id") == SOURCE_ID for row in records)
            and [
                part.get("part_id") for row in records for part in row.get("parts", [])
            ]
            == list(EXPECTED_ATOMIC_IDS),
            "master_direct_scan_record_invalid",
            "part order or source identity drifted",
        )
        sources = {}
        for binding in manifest["source_assets"]:
            asset = binding["asset"]
            _require(
                asset not in sources,
                "master_direct_scan_binding_invalid",
                "duplicate source",
            )
            sources[asset] = self._bound(asset, binding["sha256"])
        _require(
            len(sources) == 13,
            "master_direct_scan_count_mismatch",
            "source count drifted",
        )
        crops, outputs = {}, {}
        for binding in manifest["crops"]:
            asset, source = binding["asset"], binding["source_asset"]
            raw = self._bound(asset, binding["sha256"])
            size, box = self._png_dimensions(raw), binding["crop_box_xywh"]
            _require(
                asset.startswith(prefix + "evidence/")
                and asset not in crops
                and source in sources
                and size == tuple(binding["dimensions"])
                and len(box) == 4
                and all(type(v) is int and v >= 0 for v in box)
                and tuple(box[2:]) == size,
                "master_direct_scan_crop_manifest_invalid",
                "crop source or rectangle drifted",
            )
            crops[asset] = {
                **deepcopy(binding),
                "source_sha256": _sha256(sources[source]),
            }
            outputs[asset] = raw
        _require(
            len(crops) == 24, "master_direct_scan_count_mismatch", "crop count drifted"
        )
        return manifest, records, sources, crops, outputs

    @staticmethod
    def _check_reference(ref, crops):
        crop = crops.get(ref.get("crop_asset")) if isinstance(ref, dict) else None
        _require(
            crop is not None
            and ref.get("asset") == crop["source_asset"]
            and ref.get("crop_sha256") == crop["sha256"]
            and ref.get("source_sha256") == crop["source_sha256"]
            and ref.get("crop_box") == crop["crop_box_xywh"]
            and type(ref.get("page_number")) is int
            and ref["page_number"] > 0,
            "master_direct_scan_evidence_invalid",
            "source locator differs from frozen binding",
        )
        return crop

    def _relations(self, records, crops):
        relations = []
        all_refs = {}
        for row in records:
            refs = row["source_locator"]["page_refs"]
            _require(
                len({ref["crop_asset"] for ref in refs}) == len(refs),
                "master_direct_scan_evidence_invalid",
                "duplicate locator",
            )
            for ref in refs:
                self._check_reference(ref, crops)
                _require(
                    ref["role"] in {"question", "answer"},
                    "master_direct_scan_crop_role_invalid",
                    "unsupported source role",
                )
                previous = all_refs.get(ref["crop_asset"])
                _require(
                    previous is None
                    or all(
                        previous[key] == ref[key]
                        for key in (
                            "role",
                            "asset",
                            "page_number",
                            "crop_asset",
                            "source_sha256",
                            "crop_sha256",
                            "crop_box",
                        )
                    ),
                    "master_direct_scan_evidence_invalid",
                    "conflicting shared source reference",
                )
                all_refs[ref["crop_asset"]] = ref
        for sequence, row in enumerate(records):
            refs = row["source_locator"]["page_refs"]
            blocks = {block["block_id"]: block for block in row["stimulus_blocks"]}
            question = row["question_id"]
            stem = blocks.get(question + "-STEM")
            context = blocks.get(question + "-" + _CONTEXT_BLOCK[sequence])
            _require(
                stem is not None
                and context is not None
                and stem.get("raw_text") == row["parts"][0].get("prompt_raw"),
                "master_direct_scan_evidence_invalid",
                "explicit stem or context missing",
            )
            question_ref = refs[_QUESTION_REF_INDEX[sequence]]
            _require(
                question_ref["role"] == "question"
                and question_ref["crop_asset"] in stem["image_refs"],
                "master_direct_scan_crop_role_invalid",
                "question is not its explicit stem evidence",
            )
            context_refs = []
            for asset in context["image_refs"]:
                ref = all_refs.get(asset)
                _require(
                    ref is not None and ref["role"] == "question",
                    "master_direct_scan_evidence_invalid",
                    "shared block has no source-bound question evidence",
                )
                context_refs.append(ref)
            if sequence < 3:
                theme_block = blocks.get(question + "-THEME")
                _require(
                    theme_block is not None
                    and theme_block.get("image_refs") == context.get("image_refs")
                    and bool(theme_block.get("raw_text")),
                    "master_direct_scan_evidence_invalid",
                    "preparation window lacks its explicit theme introduction binding",
                )
            answer_refs = []
            for part in row["parts"]:
                answer = part["answer_evidence"]
                _require(
                    answer.get("authority") == "nonofficial_reference"
                    and answer.get("evidence_status")
                    == "source_page_verified_and_part_aligned"
                    and isinstance(answer.get("answer_text"), str)
                    and bool(answer["answer_text"])
                    and answer.get("points_authority") == "nonofficial_reference"
                    and len(answer.get("source_page_refs", [])) == 1,
                    "master_direct_scan_answer_invalid",
                    "part-aligned answer or points missing",
                )
                ref = answer["source_page_refs"][0]
                self._check_reference(ref, crops)
                _require(
                    ref["role"] == "answer"
                    and any(
                        candidate["crop_asset"] == ref["crop_asset"]
                        and candidate["role"] == "answer"
                        for candidate in refs
                    ),
                    "master_direct_scan_answer_invalid",
                    "answer does not belong to this printed question",
                )
                answer_refs.append(ref)
            relations.append(
                {
                    "question": question_ref,
                    "contexts": context_refs,
                    "context_block": context,
                    "answers": answer_refs,
                }
            )
        points = [
            part["answer_evidence"]["reference_points"]
            for row in records
            for part in row["parts"]
        ]
        _require(
            points == [2, 1, 2, 2, 2, 2, 2, 2, 2] and sum(points) == 17,
            "master_direct_scan_answer_invalid",
            "reference point allocation drifted",
        )
        return relations

    def _master_snapshot(self):
        try:
            master = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise Fudan2026AprilTheme5ZnsDirectVisualScanError(
                "master_direct_scan_master_identity_unavailable",
                "verified Master identity unavailable",
            ) from exc
        ids = {row["atomic_part_id"] for row in master.master_layers["atomic_part"]}
        exact = {
            key
            for key, rows in master.relations_by_master.items()
            if len(rows) == 1
            and rows[0].get("relation_type") == "exact_1_to_1"
            and rows[0].get("identity_mapping_allowed") is True
            and rows[0].get("endpoint_cardinality")
            == {
                "master_anchor_endpoints_for_strong_key": 0,
                "master_atomic_endpoints_for_strong_key": 1,
                "wave_rows_for_strong_key": 1,
            }
        }
        _require(
            len(ids) == 470
            and len(exact) == 169
            and exact <= ids
            and set(EXPECTED_ATOMIC_IDS) <= ids
            and not set(EXPECTED_ATOMIC_IDS) & exact,
            "master_direct_scan_identity_conflict",
            "candidate absent from Master or overlaps exact scan",
        )
        return master

    def _display(self, key, role, ref, sources, crops, displays, outputs):
        archived = self._check_reference(ref, crops)
        _require(
            key in _DISPLAY_WINDOWS,
            "fudan_zns_presentation_binding_invalid",
            "reviewed source-page display window missing",
        )
        box = _DISPLAY_WINDOWS[key]
        crop_id = (
            "FD26ZNS-C-" + _sha256((PRESENTATION_REVISION_ID + ":" + key).encode())[:24]
        )
        if crop_id in displays:
            crop = displays[crop_id]
            _require(
                crop["role"] == role
                and crop["source_asset"] == ref["asset"]
                and crop["source_crop_box"] == list(box),
                "fudan_zns_presentation_binding_invalid",
                "shared display identity conflict",
            )
            return crop
        try:
            with Image.open(io.BytesIO(sources[ref["asset"]])) as source:
                x, y, width, height = box
                _require(
                    x >= 0
                    and y >= 0
                    and width > 0
                    and height > 0
                    and x + width <= source.width
                    and y + height <= source.height,
                    "fudan_zns_presentation_binding_invalid",
                    "display rectangle leaves source page",
                )
                rendered = source.crop((x, y, x + width, y + height)).convert("RGB")
                buffer = io.BytesIO()
                rendered.save(buffer, format="PNG", compress_level=6)
                raw = buffer.getvalue()
        except (OSError, ValueError) as exc:
            raise Fudan2026AprilTheme5ZnsDirectVisualScanError(
                "fudan_zns_presentation_binding_invalid",
                "source page could not be rendered",
            ) from exc
        _require(
            len(raw) <= MAX_CROP_BYTES,
            "fudan_zns_presentation_binding_invalid",
            "display image is too large",
        )
        crop = {
            "crop_id": crop_id,
            "role": role,
            "sha256": _sha256(raw),
            "source_sha256": ref["source_sha256"],
            "source_page": ref["page_number"],
            "source_asset": ref["asset"],
            "source_crop_box": list(box),
            "archived_crop_sha256": archived["sha256"],
            "archived_crop_box": deepcopy(archived["crop_box_xywh"]),
            "presentation_revision_id": PRESENTATION_REVISION_ID,
            "display_key": key,
            "bytes": len(raw),
            "width": box[2],
            "height": box[3],
            "output_path": crop_id,
        }
        outputs[crop_id] = raw
        displays[crop_id] = crop
        return crop

    @staticmethod
    def _descriptor(crop):
        return {
            "evidence_role": crop["role"],
            **{
                key: deepcopy(crop[key])
                for key in (
                    "crop_id",
                    "sha256",
                    "source_sha256",
                    "source_page",
                    "source_asset",
                    "source_crop_box",
                    "archived_crop_sha256",
                    "archived_crop_box",
                    "presentation_revision_id",
                    "bytes",
                    "width",
                    "height",
                )
            },
        }

    def _project(
        self, row, part_index, relation, descriptors, answer_image, master, sequence
    ):
        part = row["parts"][part_index]
        node, question = part["part_id"], row["question_id"]
        atom = master.master_nodes.get(("atomic_part", node), {})
        printed = master.master_nodes.get(("printed_question", question), {})
        theme = master.master_nodes.get(("theme_big_question", THEME_ID), {})
        _require(
            all(
                obj.get("source_id") == SOURCE_ID
                and obj.get("parent_paper_id") == PAPER_ID
                for obj in (atom, printed, theme)
            )
            and atom.get("parent_printed_question_id") == question
            and all(
                obj.get("parent_theme_big_question_id") == THEME_ID
                for obj in (atom, printed)
            )
            and printed.get("printed_question_number_literal")
            == row["source_locator"]["printed_question_number"]
            and atom.get("printed_number_literal") == part["printed_number"]
            and atom.get("atomic_part_order", {}).get("value") == part_index + 1
            and theme.get("theme_order", {}).get("value") == 5,
            "master_direct_scan_parent_chain_invalid",
            "explicit Master/source parent chain differs",
        )
        difficulty = part["difficulty"]
        _require(
            difficulty.get("cognitive_prelabel") is None
            and difficulty.get("is_measured_difficulty") is False
            and difficulty.get("measured_difficulty") is None
            and len(difficulty.get("factors", [])) == 10
            and all(
                factor.get("value") in (None, "unknown")
                for factor in difficulty["factors"]
            ),
            "master_direct_scan_difficulty_invalid",
            "unknown difficulty was promoted",
        )
        cls, source_answer = part["classification"], part["answer_evidence"]
        shared_ids = [
            item["crop_id"]
            for item in descriptors
            if item["evidence_role"] == "shared_material"
        ]
        quality = [
            entry["resolution_note"] for entry in row["source_conflicts_and_boundaries"]
        ]
        return {
            "scan_id": node,
            "scan_status": "visual_scan_completed",
            "visual_scan_completed": True,
            "projection_source": "frozen_candidate_with_source_page_display_windows",
            "source_boundary_zh": SOURCE_BOUNDARY_ZH,
            "source_identity": {"source_id": SOURCE_ID, **deepcopy(row["provenance"])},
            "hierarchy": {
                "paper_id": PAPER_ID,
                "theme_id": THEME_ID,
                "theme_sequence": 5,
                "theme_title": row["source_locator"]["printed_theme_title"],
                "printed_question_id": question,
                "printed_question_number": row["source_locator"][
                    "printed_question_number"
                ],
                "printed_sequence": sequence,
                "atomic_part_id": node,
                "atomic_sequence_in_printed": part_index + 1,
            },
            "visible_summary_zh": part["prompt_raw"],
            "response_requirement_zh": part["prompt_raw"],
            "theme_chain_role": {"role_zh": None, "status": "unknown"},
            "classification": {
                "item_type": atom.get("core_item_type", "unknown"),
                "selection_rule": part["selection_rule"],
                "primary_K": None,
                "supporting_K": [],
                "label_status": "partial_source_candidate",
                "knowledge_candidates": deepcopy(cls["knowledge_K"]),
                "knowledge_candidates_K": [value["id"] for value in cls["knowledge_K"]],
                **{
                    axis: [value["id"] for value in cls[field]]
                    for axis, field in (
                        ("A", "ability_A"),
                        ("C", "context_C"),
                        ("R", "response_R"),
                        ("RP", "representation_RP"),
                    )
                },
            },
            "difficulty": deepcopy(difficulty),
            "dependency": {
                "dependency_kind": "shared_theme_context",
                "status": "source_stimulus_block_binding",
                "prior_atomic_part_ids": [],
                "shared_material_crop_ids": shared_ids,
                "analysis_zh": "保留本小题对应的完整情境和图表；第41题结论与计算过程共用同一印刷题干。",
                "conclusion_use_zh": "不把共享材料或同题作答单元误作前题答案；按完整印刷题选择。",
            },
            "dependency_evidence": {
                "revision_id": PRESENTATION_REVISION_ID,
                "candidate_only": True,
                "human_checked": False,
                "source_stimulus_block_id": relation["context_block"]["block_id"],
                "theme_shared_block_bindings": [
                    {
                        "display_key": key,
                        "source_stimulus_block_id": EXPECTED_PRINTED_IDS[owner]
                        + "-"
                        + _CONTEXT_BLOCK[owner],
                        "image_ref_index": index,
                    }
                    for key, owner, index in _context_plan(39 + sequence)
                ]
                + (
                    [
                        {
                            "display_key": "preparation",
                            "source_stimulus_block_id": EXPECTED_PRINTED_IDS[0]
                            + "-THEME",
                            "image_ref_index": 0,
                        }
                    ]
                    if sequence <= 3
                    else []
                ),
                "required_shared_material_crop_ids": shared_ids,
            },
            "shared_stimulus_text_zh": [],
            "shared_materials": [
                {
                    "block_id": relation["context_block"]["block_id"],
                    "raw_text": relation["context_block"]["raw_text"],
                    "normalized_text": relation["context_block"]["normalized_text"],
                    "crop_ids": shared_ids,
                }
            ],
            "viewed_evidence": deepcopy(descriptors),
            "answer": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "reference_summary_zh": source_answer["answer_text"],
                "source_answer_text": source_answer["answer_text"],
                "answer_text_is_source_transcription": True,
                "independently_verified": False,
                "quality_note": source_answer["note"],
                "reference_points": source_answer["reference_points"],
                "points_authority": "nonofficial_reference",
                "rubric_status": source_answer["rubric_status"],
                "visual_alignment_evidence": [deepcopy(answer_image)],
            },
            "candidate_analysis": {
                "candidate_only": True,
                "correctness_verified": False,
                "solution_path_zh": [],
            },
            "risks_and_limits": {"ambiguity_or_multiple_solutions_zh": quality},
            "quality_notes": [
                "参考答案与分值来自公众号转载，不标作学校官方答案。",
                *quality,
            ],
            "chemistry_observations": [],
            "comparison_with_prior_candidate": {},
            "authority_gates": dict(AUTHORITY),
        }

    def _snapshot(self):
        _, rows, sources, crops, outputs = self._inputs()
        relations = self._relations(rows, crops)
        master = self._master_snapshot()
        records, displays = [], {}
        for sequence, (row, relation) in enumerate(
            zip(rows, relations, strict=True), 1
        ):
            number = 39 + sequence
            question = self._display(
                f"q{number}",
                "question",
                relation["question"],
                sources,
                crops,
                displays,
                outputs,
            )
            # Frozen per-question plans below separate context from the question.
            context_plan = _context_plan(number)
            shared = [
                self._display(
                    key,
                    "shared_material",
                    relations[owner]["contexts"][index],
                    sources,
                    crops,
                    displays,
                    outputs,
                )
                for key, owner, index in context_plan
            ]
            descriptors = [
                self._descriptor(question),
                *(self._descriptor(crop) for crop in shared),
            ]
            for part_index, answer_ref in enumerate(relation["answers"]):
                answer = self._display(
                    f"a{number}",
                    "answer",
                    answer_ref,
                    sources,
                    crops,
                    displays,
                    outputs,
                )
                records.append(
                    self._project(
                        row,
                        part_index,
                        relation,
                        descriptors,
                        self._descriptor(answer),
                        master,
                        sequence,
                    )
                )
        return _Snapshot(
            records=tuple(records),
            by_master_id={row["scan_id"]: row for row in records},
            crop_by_id=displays,
            output_bytes=outputs,
            output_bindings={
                asset: {"path": asset, "sha256": _sha256(raw), "bytes": len(raw)}
                for asset, raw in outputs.items()
            },
            answer_crop_ids=frozenset(
                key for key, crop in displays.items() if crop["role"] == "answer"
            ),
            forbidden_crop_ids=frozenset(
                key for key, crop in displays.items() if crop["role"] == "answer"
            ),
            corrected_fields_by_master={node: () for node in EXPECTED_ATOMIC_IDS},
            manifest_self_sha256="",
            master_crosswalk_manifest_self_sha256=master.manifest_self_sha256,
            paper_identity_boundary=deepcopy(PAPER_IDENTITY_BOUNDARY),
        )

    @staticmethod
    def _integrity(snapshot):
        return {
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "manifest_self_sha256": None,
            "manifest_kind": "upstream_candidate_manifest_without_self_hash",
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "source_binding_count": 13,
            "archived_crop_binding_count": 24,
            "output_binding_count": len(snapshot.output_bindings),
            "display_crop_binding_count": len(snapshot.crop_by_id),
            "record_count": 9,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
            "new_visual_or_human_review_performed": False,
            "presentation_revision_id": PRESENTATION_REVISION_ID,
        }

    @staticmethod
    def _coverage():
        return {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 9,
            "visual_scanned_master_atomic": 178,
            "remaining_unscanned": 292,
            "direct_exact_overlap": 0,
        }

    def catalog(self):
        snapshot = self._snapshot()
        items = [
            {
                "master_node_id": row["scan_id"],
                "scan_status": row["scan_status"],
                "detail_available": True,
                "question_evidence_count": 1,
                "shared_evidence_count": sum(
                    item["evidence_role"] == "shared_material"
                    for item in row["viewed_evidence"]
                ),
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
            "paper_identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def status(self):
        snapshot = self._snapshot()
        roles = Counter(crop["role"] for crop in snapshot.crop_by_id.values())
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "status": "visual_scan_completed",
            "counts": {
                "expected_themes": 1,
                "expected_printed_questions": 8,
                "expected_atomic_parts": 9,
                "scan_records": 9,
                "visual_scan_completed": 9,
                "question_crop_bindings": roles["question"],
                "shared_crop_bindings": roles["shared_material"],
                "nonofficial_answer_crop_bindings": roles["answer"],
                "total_exact_crop_bindings": 24,
                "source_display_windows": len(snapshot.crop_by_id),
            },
            "coverage": self._coverage(),
            "paper_identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    @staticmethod
    def _identifier(value, label):
        try:
            validate_identifier(value, label)
        except SecurityError as exc:
            raise Fudan2026AprilTheme5ZnsDirectVisualScanError(
                "master_direct_scan_invalid_identifier", "identifier is invalid", 400
            ) from exc

    @staticmethod
    def _record(snapshot, node):
        record = snapshot.by_master_id.get(node)
        _require(
            record is not None,
            "master_direct_scan_node_not_found",
            "node has no Fudan ZnS preview",
            404,
        )
        return record

    def detail(self, master_node_id):
        self._identifier(master_node_id, "master_node_id")
        snapshot = self._snapshot()
        record = self._record(snapshot, master_node_id)
        answer = project_reference_answer(record["answer"], record["risks_and_limits"])
        answer.update(
            {
                key: record["answer"][key]
                for key in (
                    "quality_note",
                    "reference_points",
                    "points_authority",
                    "rubric_status",
                    "source_answer_text",
                    "answer_text_is_source_transcription",
                )
            }
        )
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": record["scan_id"],
            "scan_status": record["scan_status"],
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            **{
                key: deepcopy(record[key])
                for key in (
                    "visible_summary_zh",
                    "response_requirement_zh",
                    "theme_chain_role",
                    "dependency",
                    "dependency_evidence",
                    "source_boundary_zh",
                    "quality_notes",
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
                    **deepcopy(item),
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
            "reference_answer_images": [
                {
                    **deepcopy(item),
                    "content_type": "image/png",
                    "access": "teacher_reference_answer_only",
                    "display_mode": "inline_required"
                    if record["hierarchy"]["atomic_part_id"] == "FD2026-APR-S5-Q41-P2"
                    else "preview_only",
                    "caption_zh": "公众号转载 · 非官方参考答案图",
                }
                for item in record["answer"]["visual_alignment_evidence"]
            ],
            "reference_answer": answer,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def _crop(self, node, crop_id, *, answer):
        self._identifier(node, "master_node_id")
        self._identifier(crop_id, "crop_id")
        snapshot = self._snapshot()
        record = self._record(snapshot, node)
        if not answer and crop_id in snapshot.answer_crop_ids:
            _require(
                False,
                "master_direct_scan_crop_role_denied",
                "answer is not a question preview",
                403,
            )
        crop = snapshot.crop_by_id.get(crop_id)
        evidence = (
            record["answer"]["visual_alignment_evidence"]
            if answer
            else record["viewed_evidence"]
        )
        descriptor = next(
            (item for item in evidence if item["crop_id"] == crop_id), None
        )
        _require(
            crop is not None and descriptor is not None,
            "teacher_answer_crop_not_found"
            if answer
            else "master_direct_scan_crop_not_found",
            "crop does not belong to this node",
            404,
        )
        raw = snapshot.output_bytes[crop["output_path"]]
        _require(
            crop["role"] in ({"answer"} if answer else {"question", "shared_material"})
            and descriptor["evidence_role"] == crop["role"]
            and descriptor["source_sha256"] == crop["source_sha256"]
            and descriptor["source_crop_box"] == crop["source_crop_box"]
            and _sha256(raw) == descriptor["sha256"] == crop["sha256"]
            and len(raw) == descriptor["bytes"] == crop["bytes"]
            and self._png_dimensions(raw)
            == (descriptor["width"], descriptor["height"]),
            "master_direct_scan_binding_mismatch",
            "served preview differs from source-bound descriptor",
        )
        return CandidateCropPayload(data=raw, sha256=_sha256(raw))

    def question_crop(self, master_node_id, crop_id):
        return self._crop(master_node_id, crop_id, answer=False)

    def teacher_answer_crop(self, master_node_id, crop_id):
        return self._crop(master_node_id, crop_id, answer=True)


def _context_plan(number):
    # Tuple fields are display key, explicit owner question index, block image
    # reference index. Q43 shares Q44's graph because its prose says "如图";
    # Q47 shares Q46's battery prose, absent from Q47's own cropped page refs.
    if number in (40, 41, 42):
        return [("preparation", 0, 0)]
    if number in (43, 44):
        return [("recovery", 3, 0), ("recovery-graph", 4, 1)]
    if number == 45:
        return [("optical", 5, 0)]
    if number in (46, 47):
        return [("battery", 6, 0), ("battery-crystals", 6, 1)]
    raise Fudan2026AprilTheme5ZnsDirectVisualScanError(
        "master_direct_scan_evidence_invalid", "unknown question context"
    )
