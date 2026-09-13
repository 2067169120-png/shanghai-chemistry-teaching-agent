"""Native read-only source-A view of the frozen dual-source fosinopril pack.

Source B remains comparison evidence, never an implicit correction of A.
This adapter does not write a scan product or promote the upstream candidates.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

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

PRODUCT_ID = "FORMAL-PENDING-V2-LEVEL-EXAM-2025-S3-FOSINOPRIL-DUAL-SOURCE-A"
PRODUCT_RELATIVE = Path(
    "kb/formal/candidates/pending_v2/level_exam_2025_theme3_fosinopril_dual_source"
)
PAPER_ID = "PAPER-36de61ec53ac4809efbc"
THEME_ID = "THEME-59d55f8591e42550da0c"
SOURCE_ID = "file-080b81820b91d975f0f63f378d06bd55e34ded4ab81c9596c830f323d1152e6b"
EXPECTED_PRINTED_IDS = tuple(f"LE2025-S3-Q{n:02}" for n in range(1, 10))
EXPECTED_ATOMIC_IDS = tuple(f"{value}-P1" for value in EXPECTED_PRINTED_IDS)
EXPECTED_MANIFEST_FILE_SHA256 = (
    "7021ad2588be0d0aee82382e0f5ed7faa4fa7e16dbea284f88748f54e5117559"
)
EXPECTED_RECORDS_SHA256 = (
    "0e57bbd7a1eb97c1682cd3d1ea02d9d7435db9cb31f480bf658a894f9f7e1ac0"
)
EXPECTED_EVIDENCE_SHA256 = (
    "2f30ddd67445e9a0f550273f79f5528638706a922b2d6e03c133d8a7c59aad14"
)
EXPECTED_CLASSIFICATION_SHA256 = (
    "8376b4b106b7427799916ae9cfe10a5d1503d55de3f5677d5824eece77b87274"
)
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
SOURCE_VARIANT = "source_A"
SOURCE_ACCOUNT = "萌藤mountain"
PRESENTATION_REVISION_ID = "fosinopril-source-a-reference-images-20260910-r1"
DEPENDENCY_REVISION_ID = "fosinopril-source-a-dependencies-20260912-r1"
DEPENDENCY_STATUS = "source_page_backed_candidate_dependency"
SOURCE_BOUNDARY_ZH = (
    "2025年上海化学等级考非官方回忆版 · 来源A（萌藤mountain）· 主题三福辛普利中间体"
)
MAX_CROP_BYTES = 1024 * 1024
VISUAL_ONLY_ATOMIC_IDS = frozenset(EXPECTED_ATOMIC_IDS[n - 1] for n in (5, 7, 9))
VISUAL_ANSWER_NOTICE = "来源A参考答案为结构图，未转录文字；请查看对应原答案图。"
_DEPENDENCY_SOURCE_SHA256 = {
    4: "3529ccf5bb9b306920945ae39b90fefee6abf0ec0b2896a64ea551376547c6ac",
    5: "4e35450c8382b603d2c07a6dea0018acb82ff3a5a318b6a83e3442ab46649b78",
}
# Main-agent visual inspection of the complete source-A question pages established
# these material anchors. This is not human approval or inference from sequence.
_DEPENDENCY_ANCHORS = dict(
    zip(
        EXPECTED_ATOMIC_IDS,
        (
            "使用合成路线中化合物A的结构辨认含氧官能团；不使用前题结论。",
            "使用合成路线中反应1及其反应物、产物结构判断保护作用；不使用第1题答案。",
            "使用合成路线中反应2的反应物、产物判断反应类型；不使用第1、2题答案。",
            "使用合成路线中化合物D的结构判断手性碳原子；不使用前题答案。",
            "使用合成路线中反应4、化合物E及本题给定的异构条件；不依赖前题结论。",
            "使用合成路线中化合物E的结构与本题选项判断性质；不使用第5题作答。",
            "使用合成路线中化合物F和本题跨两页的条件i—iii；各条件完整保留，不引用前题答案。",
            "使用合成路线中反应6两侧物质的结构判断试剂与条件；不使用第7题答案。",
            "使用合成路线以及本题给定的格氏反应、原料与合成要求；不依赖第8题答案。",
        ),
        strict=True,
    )
)

# Explicit part-to-answer decisions from the frozen source-A evidence map.
# Filenames are locators, not a heuristic for discovering roles or ownership.
ANSWER_ASSETS = dict(
    zip(
        EXPECTED_ATOMIC_IDS,
        (
            "a-answer-p04-q01.png",
            "a-answer-p04-q02.png",
            "a-answer-p04-q03.png",
            "a-answer-p04-q04.png",
            "a-answer-p04-q05-visual.png",
            "a-answer-p04-q06.png",
            "a-answer-p04-q07-visual.png",
            "a-answer-p04-q08.png",
            "a-answer-p04-q09-visual.png",
        ),
        strict=True,
    )
)
AUTHORITY = {
    **BASE_AUTHORITY,
    "diagnosis_allowed": False,
    "formal_promotion_allowed": False,
    "component_reuse_allowed": False,
    "direct_source_pixel_reuse_allowed": False,
}
PAPER_IDENTITY_BOUNDARY = {
    "region_label": "上海市",
    "year": 2025,
    "paper_family": "level_exam_nonofficial_recall",
    "paper_type": "2025年上海化学等级考非官方回忆版（来源A）",
    "covered_scope": "主题三福辛普利中间体，9个印刷小题/9个作答单元",
    "official_status": "nonofficial",
    "source_account": SOURCE_ACCOUNT,
    "default_source_variant": SOURCE_VARIANT,
    "comparison_source_account": "昂立中学生",
    "dual_sources_merged": False,
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


class LevelExam2025Theme3FosinoprilDirectVisualScanError(MasterDirectVisualScanError):
    pass


def _require(condition: bool, code: str, message: str, status: int = 409) -> None:
    if not condition:
        raise LevelExam2025Theme3FosinoprilDirectVisualScanError(code, message, status)


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


def _crop_id(asset: str) -> str:
    return "LE25S3-C-" + _sha256(asset.encode("utf-8"))[:24]


def _source_dependency(node, descriptors):
    _require(
        node in _DEPENDENCY_ANCHORS,
        "fosinopril_dependency_evidence_invalid",
        "source-backed material anchor missing",
    )
    shared = [
        item["crop_id"]
        for item in descriptors
        if item["evidence_role"] == "shared_material"
    ]
    pages = {}
    for item in descriptors:
        page, digest = item["source_page"], item["source_sha256"]
        _require(
            _DEPENDENCY_SOURCE_SHA256.get(page) == digest,
            "fosinopril_dependency_evidence_invalid",
            "source page differs from inspected source A",
        )
        pages[page] = digest
    _require(
        len(shared) == 1
        and set(pages) == ({4, 5} if node in EXPECTED_ATOMIC_IDS[6:] else {4}),
        "fosinopril_dependency_evidence_invalid",
        "question or complete shared route is missing",
    )
    return {
        "dependency_kind": "shared_theme_context",
        "status": DEPENDENCY_STATUS,
        "prior_atomic_part_ids": [],
        "shared_material_crop_ids": shared,
        "analysis_zh": _DEPENDENCY_ANCHORS[node],
        "conclusion_use_zh": "不使用前题作答或结论；来源A完整合成路线及本小题全部给定条件必须保留。",
    }, {
        "revision_id": DEPENDENCY_REVISION_ID,
        "status": "source_page_visual_inspection_candidate",
        "candidate_only": True,
        "human_checked": False,
        "source_variant": SOURCE_VARIANT,
        "source_page_bindings": [
            {"page": page, "sha256": digest} for page, digest in sorted(pages.items())
        ],
        "required_shared_material_crop_ids": list(shared),
    }


class LevelExam2025Theme3FosinoprilDirectVisualScanReader(
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

    def _bound(self, asset: str, digest: str, size: int | None = None) -> bytes:
        _, raw = self._verified_file(self.shchem_root, asset)
        _require(
            _sha256(raw) == digest and (size is None or len(raw) == size),
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
            and manifest.get("counts", {}).get("parts") == 9
            and manifest.get("counts", {}).get("exact_crops") == 49,
            "master_direct_scan_manifest_invalid",
            "frozen package identity or counts drifted",
        )
        sources, outputs = {}, {}
        for field, key, target, count in (
            ("source_assets", "asset", sources, 14),
            ("outputs", "path", outputs, 7),
        ):
            for item in manifest[field]:
                asset = item[key]
                _require(
                    asset not in target,
                    "master_direct_scan_binding_invalid",
                    "duplicate binding",
                )
                target[asset] = self._bound(asset, item["sha256"], item.get("bytes"))
            _require(
                len(target) == count,
                "master_direct_scan_count_mismatch",
                "binding count drifted",
            )
        rows = []
        for filename, digest in (
            ("question_candidates.jsonl", EXPECTED_RECORDS_SHA256),
            ("evidence_map.jsonl", EXPECTED_EVIDENCE_SHA256),
            ("classification_evidence.jsonl", EXPECTED_CLASSIFICATION_SHA256),
        ):
            raw = outputs.get(prefix + filename)
            _require(
                raw is not None and _sha256(raw) == digest,
                "master_direct_scan_binding_mismatch",
                "candidate document pin drifted",
            )
            rows.append(_jsonl_objects(raw, filename))
        records, evidence, classification = rows
        _closed([manifest, records, evidence, classification])
        _require(
            all(len(group) == 9 for group in rows)
            and [r.get("question_id") for r in records] == list(EXPECTED_PRINTED_IDS)
            and all(
                len(r.get("parts", [])) == 1 and r.get("source_id") == SOURCE_ID
                for r in records
            )
            and [r["parts"][0].get("part_id") for r in records]
            == list(EXPECTED_ATOMIC_IDS),
            "master_direct_scan_record_invalid",
            "candidate identity, order or cardinality drifted",
        )
        crops = {}
        for item in manifest["crops"]:
            asset = item["asset"]
            _require(
                asset.startswith(prefix + "evidence/") and asset not in outputs,
                "master_direct_scan_crop_manifest_invalid",
                "crop asset duplicated or outside package",
            )
            raw = self._bound(asset, item["sha256"])
            outputs[asset] = raw
            dimensions = self._png_dimensions(raw)
            _require(
                dimensions == tuple(item["dimensions"]),
                "master_direct_scan_crop_manifest_invalid",
                "crop PNG or dimensions drifted",
            )
            if item.get("source_asset") is None:
                continue  # Contact sheets have no single source rectangle and no route.
            box = item.get("crop_box_xywh")
            _require(
                item["source_asset"] in sources
                and isinstance(box, list)
                and len(box) == 4
                and all(type(v) is int and v >= 0 for v in box)
                and tuple(box[2:]) == dimensions
                and len(raw) <= MAX_CROP_BYTES,
                "master_direct_scan_crop_manifest_invalid",
                "crop source or rectangle invalid",
            )
            crops[asset] = {
                **deepcopy(item),
                "crop_id": _crop_id(asset),
                "role": "unknown",
                "output_path": asset,
                "bytes": len(raw),
                "width": dimensions[0],
                "height": dimensions[1],
                "source_sha256": _sha256(sources[item["source_asset"]]),
                "crop_box": list(box),
                "safe_http_status_if_routed": 403,
            }
        _require(
            len(manifest["crops"]) == 51 and len(crops) == 49,
            "master_direct_scan_count_mismatch",
            "crop/contact-sheet coverage drifted",
        )
        return manifest, records, evidence, classification, outputs, crops

    @staticmethod
    def _check_reference(ref, crop):
        _require(
            isinstance(ref, dict)
            and crop is not None
            and ref.get("crop_asset") == crop["asset"]
            and ref.get("crop_sha256") == crop["sha256"]
            and ref.get("asset") == crop["source_asset"]
            and ref.get("source_sha256") == crop["source_sha256"]
            and ref.get("crop_box") == crop["crop_box"]
            and type(ref.get("page_number")) is int
            and ref["page_number"] > 0,
            "master_direct_scan_evidence_invalid",
            "source/crop reference differs from frozen binding",
        )
        if "source_page_number" in crop:
            _require(
                crop["source_page_number"] == ref["page_number"],
                "master_direct_scan_evidence_invalid",
                "crop has conflicting page references",
            )
        crop["source_page_number"] = ref["page_number"]

    def _relations(self, records, evidence, classification, crops):
        relations = []
        prefix = PRODUCT_RELATIVE.as_posix() + "/evidence/"
        for sequence, (row, ev, cls) in enumerate(
            zip(records, evidence, classification, strict=True), 1
        ):
            part = row["parts"][0]
            node = part["part_id"]
            _require(
                ev.get("question_id") == cls.get("question_id") == row["question_id"]
                and ev.get("part_id") == cls.get("part_id") == node
                and ev.get("prompt_transcriptions") == part.get("source_variants")
                and ev.get("answer_alignment") == part.get("answer_evidence")
                and cls.get("classification") == part.get("classification")
                and cls.get("difficulty") == part.get("difficulty"),
                "master_direct_scan_evidence_invalid",
                "part evidence documents disagree",
            )
            refs = row["source_locator"]["page_refs"]
            by_asset = {ref.get("crop_asset"): ref for ref in refs}
            _require(
                len(by_asset) == len(refs),
                "master_direct_scan_evidence_invalid",
                "duplicate source reference",
            )
            for asset, ref in by_asset.items():
                self._check_reference(ref, crops.get(asset))
                _require(
                    ref.get("source_variant") in {"source_A", "source_B"},
                    "master_direct_scan_evidence_invalid",
                    "source variant missing",
                )
            for group in (
                ev["prompt_crop_refs"],
                ev["context_crop_refs"],
                part["answer_evidence"]["source_page_refs"],
            ):
                _require(
                    all(by_asset.get(ref.get("crop_asset")) == ref for ref in group),
                    "master_direct_scan_evidence_invalid",
                    "mapped reference not in part source record",
                )
            _require(
                set(by_asset)
                == {
                    ref["crop_asset"]
                    for group in (
                        ev["prompt_crop_refs"],
                        ev["context_crop_refs"],
                        part["answer_evidence"]["source_page_refs"],
                    )
                    for ref in group
                },
                "master_direct_scan_evidence_invalid",
                "source reference coverage differs",
            )
            for variant, account in (
                ("source_A", SOURCE_ACCOUNT),
                ("source_B", "昂立中学生"),
            ):
                prompt = part["source_variants"][variant]
                question_refs = [
                    ref
                    for ref in ev["prompt_crop_refs"]
                    if ref["source_variant"] == variant
                ]
                answer_refs = part["answer_evidence"]["source_variants"][variant][
                    "evidence_refs"
                ]
                _require(
                    prompt.get("source_variant") == variant
                    and prompt.get("source_account") == account
                    and isinstance(prompt.get("prompt_raw"), str)
                    and bool(prompt["prompt_raw"])
                    and prompt.get("question_crop_refs")
                    == [ref["crop_asset"] for ref in question_refs]
                    and all(
                        ref["role"] == "question" and ref["source_account"] == account
                        for ref in question_refs
                    )
                    and all(
                        asset in by_asset
                        and by_asset[asset]["role"] == "answer_or_analysis"
                        and by_asset[asset]["source_variant"] == variant
                        for asset in answer_refs
                    ),
                    "master_direct_scan_crop_role_invalid",
                    "prompt/answer source ownership differs",
                )
            prompt_a = part["source_variants"][SOURCE_VARIANT]
            shared_refs = [
                ref
                for ref in ev["context_crop_refs"]
                if ref["source_variant"] == SOURCE_VARIANT
            ]
            stimulus_assets = [
                asset
                for block in row["stimulus_blocks"]
                for asset in block["image_refs"]
            ]
            _require(
                prompt_a["printed_subquestion"] == f"3({sequence})"
                and part["printed_number_by_source"][SOURCE_VARIANT]
                == prompt_a["printed_subquestion"]
                and part["selection_rule_by_source"][SOURCE_VARIANT]
                == prompt_a["selection_rule"]
                and len(prompt_a["question_crop_refs"]) == (2 if sequence == 7 else 1)
                and len(shared_refs) == 1
                and shared_refs[0]["role"] == "context"
                and set(stimulus_assets)
                == {ref["crop_asset"] for ref in ev["context_crop_refs"]},
                "master_direct_scan_evidence_invalid",
                "source-A order, question split or shared route drifted",
            )
            answer_asset = prefix + ANSWER_ASSETS[node]
            answer_a = part["answer_evidence"]["source_variants"][SOURCE_VARIANT]
            _require(
                answer_asset in answer_a["evidence_refs"]
                and answer_a["visual_only"] is (node in VISUAL_ONLY_ATOMIC_IDS)
                and (
                    (answer_a["value"] is None)
                    if answer_a["visual_only"]
                    else isinstance(answer_a["value"], str) and bool(answer_a["value"])
                ),
                "master_direct_scan_answer_invalid",
                "source-A answer image/text binding differs",
            )
            selected = {
                "question": list(prompt_a["question_crop_refs"]),
                "shared_material": [ref["crop_asset"] for ref in shared_refs],
                "answer": [answer_asset],
            }
            for role, assets in selected.items():
                for asset in assets:
                    crop = crops[asset]
                    _require(
                        crop["role"] in {"unknown", role}
                        and by_asset[asset]["source_variant"] == SOURCE_VARIANT,
                        "master_direct_scan_crop_role_invalid",
                        "selected source or role conflict",
                    )
                    crop["role"] = role
                    crop["safe_http_status_if_routed"] = (
                        200 if role in {"question", "shared_material"} else 403
                    )
            relations.append(selected)
        _require(
            Counter(crop["role"] for crop in crops.values())
            == {"question": 10, "shared_material": 1, "answer": 9, "unknown": 29},
            "master_direct_scan_count_mismatch",
            "source-A display role coverage drifted",
        )
        return relations

    def _master_snapshot(self):
        try:
            master = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise LevelExam2025Theme3FosinoprilDirectVisualScanError(
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

    @staticmethod
    def _descriptor(crop):
        revision = crop.get("presentation_revision")
        presentation = {}
        if isinstance(revision, dict):
            presentation = {
                "archived_crop_sha256": revision["archived_sha256"],
                "archived_crop_box": deepcopy(revision["archived_crop_box"]),
                "source_crop_box": deepcopy(revision["source_crop_box"]),
                "source_asset": revision["source_asset"],
                "presentation_revision_id": revision["revision_id"],
                "presentation_revision": deepcopy(revision),
            }
        return {
            "crop_id": crop["crop_id"],
            "evidence_role": crop["role"],
            "sha256": crop["sha256"],
            "source_sha256": crop["source_sha256"],
            "source_page": crop["source_page_number"],
            "bytes": crop["bytes"],
            "width": crop["width"],
            "height": crop["height"],
            **{
                key: deepcopy(crop[key])
                for key in (
                    "archived_crop_sha256",
                    "archived_crop_box",
                    "source_crop_box",
                    "source_asset",
                    "presentation_revision_id",
                )
                if key in crop
            },
            **presentation,
        }

    def _project(self, row, relation, crops, master, sequence, theme_title):
        part = row["parts"][0]
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
            == part["printed_number_by_source"]
            and atom.get("atomic_part_order", {}).get("value") == 1
            and theme.get("theme_order", {}).get("value") == 3,
            "master_direct_scan_parent_chain_invalid",
            "explicit Master/source parent chain differs",
        )
        difficulty = part["difficulty"]
        _require(
            difficulty.get("cognitive_prelabel") is None
            and difficulty.get("is_measured_difficulty") is False
            and len(difficulty.get("factors", [])) == 10
            and all(
                factor.get("value") in (None, "unknown")
                for factor in difficulty["factors"]
            ),
            "master_direct_scan_difficulty_invalid",
            "unknown difficulty was promoted",
        )
        source_answer = part["answer_evidence"]
        _require(
            source_answer.get("authority") == "institution_reference_nonofficial"
            and source_answer.get("reference_points") is None
            and source_answer.get("evidence_status")
            == "dual_source_page_crops_part_aligned",
            "master_direct_scan_answer_invalid",
            "source answer authority differs",
        )
        prompt_a = part["source_variants"][SOURCE_VARIANT]
        answer_a = source_answer["source_variants"][SOURCE_VARIANT]
        shared_ids = [crops[asset]["crop_id"] for asset in relation["shared_material"]]
        quality = [
            entry["resolution_note"] for entry in row["source_conflicts_and_boundaries"]
        ]
        classification = part["classification"]
        response_codes = [value["id"] for value in classification["response_R"]]
        response_projection = {}
        if node == "LE2025-S3-Q03-P1":
            prompt_b = part["source_variants"]["source_B"]
            _require(
                len(response_codes) == 2
                and set(response_codes) == {"R01", "R02"}
                and prompt_a["item_type"] == "embedded_single_choice"
                and prompt_a["selection_rule"] == "single"
                and prompt_b["item_type"] == "short_fill"
                and prompt_b["selection_rule"] == "not_applicable",
                "fosinopril_source_response_projection_invalid",
                "Q3 response forms no longer match the inspected source variants",
            )
            response_codes = ["R01"]
            response_projection = {
                "status": "source_page_visual_inspection_candidate",
                "source_variant": SOURCE_VARIANT,
                "source_page": 4,
                "source_sha256": _DEPENDENCY_SOURCE_SHA256[4],
                "question_crop_refs": list(prompt_a["question_crop_refs"]),
                "response_R_by_source": {"source_A": ["R01"], "source_B": ["R02"]},
                "basis_zh": "来源A第3题给出A—D选项并要求选择；来源B对应题为短填空，R02仅保留在来源对照中。",
                "human_checked": False,
            }
        selection_notes = []
        if node == "LE2025-S3-Q06-P1":
            _require(
                prompt_a["selection_rule"] == "unknown",
                "fosinopril_source_selection_rule_invalid",
                "source-A Q6 selection rule was promoted",
            )
            selection_notes = [
                "来源A题面未注明计分及单选/多选规则；相关规则以来源A题面为准，不沿用来源B的“不定项”说明。"
            ]
        descriptors = [
            self._descriptor(crops[asset])
            for role in ("question", "shared_material")
            for asset in relation[role]
        ]
        dependency, dependency_evidence = _source_dependency(node, descriptors)
        return {
            "scan_id": node,
            "scan_status": "visual_scan_completed",
            "visual_scan_completed": True,
            "projection_source": "frozen_dual_source_candidate_source_A_not_new_visual_review",
            "source_variant": SOURCE_VARIANT,
            "source_boundary_zh": SOURCE_BOUNDARY_ZH,
            "source_identity": {
                "source_id": SOURCE_ID,
                "source_variant": SOURCE_VARIANT,
                "year": 2025,
                "region_or_school": "上海市",
                "paper_type": row["provenance"]["paper_type"],
                "source_account": SOURCE_ACCOUNT,
                "source_url": prompt_a["source_url"],
                "official_status": "nonofficial",
            },
            "hierarchy": {
                "paper_id": PAPER_ID,
                "theme_id": THEME_ID,
                "theme_sequence": 3,
                "theme_title": theme_title,
                "printed_question_id": question,
                "printed_question_number": prompt_a["printed_subquestion"],
                "printed_sequence": sequence,
                "printed_sequence_basis": "frozen_source_A_printed_subquestion_literal",
                "atomic_part_id": node,
                "atomic_sequence_in_printed": 1,
            },
            "visible_summary_zh": prompt_a["prompt_raw"],
            "response_requirement_zh": prompt_a["prompt_raw"],
            "theme_chain_role": {"role_zh": None, "status": "unknown"},
            "classification": {
                "item_type": prompt_a["item_type"],
                "selection_rule": prompt_a["selection_rule"],
                "primary_K": None,
                "supporting_K": [],
                "label_status": "partial_source_candidate",
                "knowledge_candidates": deepcopy(classification["knowledge_K"]),
                "knowledge_candidates_K": [
                    value["id"] for value in classification["knowledge_K"]
                ],
                **{
                    axis: [value["id"] for value in classification[field]]
                    for axis, field in (
                        ("A", "ability_A"),
                        ("C", "context_C"),
                        ("RP", "representation_RP"),
                    )
                },
                "R": response_codes,
                **(
                    {"response_projection_evidence": response_projection}
                    if response_projection
                    else {}
                ),
            },
            "difficulty": deepcopy(difficulty),
            "dependency": dependency,
            "dependency_evidence": dependency_evidence,
            "shared_stimulus_text_zh": [],
            "shared_materials": [
                {
                    "block_id": "LE2025-S3-SOURCE-A-ROUTE",
                    "raw_text": None,
                    "normalized_text": "来源A福辛普利中间体合成路线，结构与条件以原图为准。",
                    "crop_ids": shared_ids,
                }
            ],
            "viewed_evidence": descriptors,
            "answer": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "reference_summary_zh": VISUAL_ANSWER_NOTICE
                if answer_a["visual_only"]
                else answer_a["value"],
                "source_answer_text": answer_a["value"],
                "answer_text_is_source_transcription": not answer_a["visual_only"],
                "independently_verified": False,
                "quality_note": source_answer["boundary_note"],
                "reference_points": None,
                "rubric_status": "absent_no_stepwise_rubric",
                "visual_alignment_evidence": [
                    self._descriptor(crops[asset]) for asset in relation["answer"]
                ],
            },
            "source_variant_comparison": {
                "default_source_variant": SOURCE_VARIANT,
                "merged": False,
                "original_dual_source_response_R": deepcopy(
                    classification["response_R"]
                ),
                "prompt_by_source": {
                    variant: {
                        key: deepcopy(value[key])
                        for key in (
                            "source_variant",
                            "source_account",
                            "prompt_raw",
                            "item_type",
                            "selection_rule",
                            "printed_subquestion",
                        )
                    }
                    for variant, value in part["source_variants"].items()
                },
                "answer_by_source": {
                    variant: {
                        "value": value["value"],
                        "visual_only": value["visual_only"],
                    }
                    for variant, value in source_answer["source_variants"].items()
                },
                "differences": deepcopy(row["source_conflicts_and_boundaries"]),
            },
            "candidate_analysis": {
                "candidate_only": True,
                "correctness_verified": False,
                "solution_path_zh": [],
            },
            "risks_and_limits": {"ambiguity_or_multiple_solutions_zh": quality},
            "quality_notes": [
                "当前题面及参考答案统一采用来源A（萌藤mountain）非官方回忆版；来源B（昂立中学生）只作对照，不混入题面或答案。",
                *quality,
                *selection_notes,
            ],
            "chemistry_observations": [],
            "comparison_with_prior_candidate": {},
            "authority_gates": dict(AUTHORITY),
        }

    def _snapshot(self) -> _Snapshot:
        from .fosinopril_source_presentation import (
            FosinoprilPresentationError,
            apply_source_a_presentation,
        )

        manifest, rows, evidence, classification, outputs, crops = self._inputs()
        relations = self._relations(rows, evidence, classification, crops)
        try:
            apply_source_a_presentation(self.shchem_root, outputs, crops)
        except FosinoprilPresentationError as exc:
            raise LevelExam2025Theme3FosinoprilDirectVisualScanError(
                "fosinopril_presentation_binding_invalid",
                "source-A display repair no longer matches its frozen source",
            ) from exc
        master = self._master_snapshot()
        records = tuple(
            self._project(
                row,
                relation,
                crops,
                master,
                sequence,
                manifest["theme_title_by_source"][SOURCE_VARIANT],
            )
            for sequence, (row, relation) in enumerate(
                zip(rows, relations, strict=True), 1
            )
        )
        by_crop = {crop["crop_id"]: crop for crop in crops.values()}
        return _Snapshot(
            records=records,
            by_master_id={row["hierarchy"]["atomic_part_id"]: row for row in records},
            crop_by_id=by_crop,
            output_bytes=outputs,
            output_bindings={
                asset: {"path": asset, "sha256": _sha256(raw), "bytes": len(raw)}
                for asset, raw in outputs.items()
            },
            answer_crop_ids=frozenset(
                key for key, crop in by_crop.items() if crop["role"] == "answer"
            ),
            forbidden_crop_ids=frozenset(
                key
                for key, crop in by_crop.items()
                if crop["role"] not in {"question", "shared_material"}
            ),
            corrected_fields_by_master={key: () for key in EXPECTED_ATOMIC_IDS},
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
            "output_binding_count": len(snapshot.output_bindings),
            "source_binding_count": 14,
            "record_count": 9,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
            "new_visual_or_human_review_performed": False,
            "presentation_revision_id": PRESENTATION_REVISION_ID,
            "default_source_variant": SOURCE_VARIANT,
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
                "master_node_id": row["hierarchy"]["atomic_part_id"],
                "scan_status": row["scan_status"],
                "detail_available": True,
                "question_evidence_count": sum(
                    item["evidence_role"] == "question"
                    for item in row["viewed_evidence"]
                ),
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
                "expected_themes": 1,
                "expected_printed_questions": 9,
                "expected_atomic_parts": 9,
                "scan_records": 9,
                "visual_scan_completed": 9,
                "question_crop_bindings": 10,
                "shared_crop_bindings": 1,
                "nonofficial_answer_crop_bindings": 9,
                "unknown_or_boundary_crop_bindings": 29,
                "total_exact_crop_bindings": 49,
            },
        }

    @staticmethod
    def _identifier(value, label):
        try:
            validate_identifier(value, label)
        except SecurityError as exc:
            raise LevelExam2025Theme3FosinoprilDirectVisualScanError(
                "master_direct_scan_invalid_identifier", "identifier is invalid", 400
            ) from exc

    def _record(self, snapshot, node):
        record = snapshot.by_master_id.get(node)
        _require(
            record is not None,
            "master_direct_scan_node_not_found",
            "node has no source-A preview",
            404,
        )
        return record

    @staticmethod
    def _answer_image(node, descriptor):
        _require(
            node in EXPECTED_ATOMIC_IDS and descriptor.get("evidence_role") == "answer",
            "teacher_answer_crop_binding_mismatch",
            "answer descriptor identity or role invalid",
        )
        return {
            **deepcopy(descriptor),
            "content_type": "image/png",
            "access": "teacher_reference_answer_only",
            "display_mode": "inline_required"
            if node in VISUAL_ONLY_ATOMIC_IDS
            else "preview_only",
            "presentation_revision_id": descriptor.get(
                "presentation_revision_id", PRESENTATION_REVISION_ID
            ),
            "caption_zh": "非官方参考答案图 · 来源A（萌藤mountain）",
        }

    def detail(self, master_node_id: str):
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
            "identity_boundary": deepcopy(snapshot.paper_identity_boundary),
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
                    "source_variant",
                    "source_variant_comparison",
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
                self._answer_image(master_node_id, item)
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
        crop = snapshot.crop_by_id.get(crop_id)
        if not answer and crop_id in snapshot.forbidden_crop_ids:
            _require(
                False,
                "master_direct_scan_crop_role_denied",
                "answer or comparison evidence is not a question preview",
                403,
            )
        descriptors = (
            record["answer"]["visual_alignment_evidence"]
            if answer
            else record["viewed_evidence"]
        )
        descriptor = next(
            (item for item in descriptors if item["crop_id"] == crop_id), None
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
            and _sha256(raw) == descriptor["sha256"] == crop["sha256"]
            and len(raw) == descriptor["bytes"] == crop["bytes"]
            and descriptor["source_sha256"] == crop["source_sha256"]
            and self._png_dimensions(raw)
            == (descriptor["width"], descriptor["height"]),
            "teacher_answer_crop_binding_mismatch"
            if answer
            else "master_direct_scan_binding_mismatch",
            "preview bytes no longer match the source-bound descriptor",
        )
        return CandidateCropPayload(data=raw, sha256=_sha256(raw))

    def question_crop(self, master_node_id: str, crop_id: str) -> CandidateCropPayload:
        return self._crop(master_node_id, crop_id, answer=False)

    def teacher_answer_crop(
        self, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        return self._crop(master_node_id, crop_id, answer=True)
