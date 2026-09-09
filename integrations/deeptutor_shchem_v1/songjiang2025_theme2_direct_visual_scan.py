"""Read-only Master projection of the frozen Songjiang Fe(OH)2 candidate pack.

The upstream package stays untouched. This adapter preserves its explicit
question/answer/context edges and unknown labels; a UI projection is not a new
formal scan manifest, a chemistry approval, or a permission to reuse pixels.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from .candidate_review import CandidateCropPayload
from .master_direct_visual_scan import MasterDirectVisualScanError
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .pudong2026_first_mock_theme_direct_visual_scan import (
    AUTHORITY as _BASE_AUTHORITY,
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

PRODUCT_ID = "QVS-SJ2025-SECOND-MOCK-T2-FEOH2-LOCAL-REFERENCE-V1"
PRODUCT_RELATIVE = Path("kb/formal/candidates/pending_v2/songjiang_2025_theme2_feoh2")
SCOPE = "candidate_only_read_only_master_direct_visual_scan"
PAPER_ID = "PAPER-bd84e1ef58eae4ca12e0"
THEME_ID = "THEME-19fd22b2d56203129dc7"
SOURCE_ID = "file-57f95dd21c8b1acf5012ecd77f1e15cd7f4061d34064edadef97816a17b5abc1"
EXPECTED_BATCH_ID = "FORMAL-PENDING-V2-2025-SJ-EM-S2-FEOH2-A"
EXPECTED_MANIFEST_FILE_SHA256 = (
    "b62a112a1d1bc4f32ed8eb0b7faade10452054cee9a9241d05cc00a9739319b7"
)
EXPECTED_MANIFEST_BYTES = 20536
EXPECTED_PRINTED_IDS = tuple(f"SJ2025-EM-S2-Q{i}" for i in range(1, 10))
EXPECTED_ATOMIC_IDS = tuple(f"{qid}-P1" for qid in EXPECTED_PRINTED_IDS)
MAX_CROP_BYTES = 1024 * 1024
AUTHORITY = {
    **_BASE_AUTHORITY,
    "formal_promotion_allowed": False,
    "diagnosis_allowed": False,
    "component_reuse_allowed": False,
    "direct_source_pixel_reuse_allowed": False,
    "lineage_gate_complete": False,
}
PAPER_IDENTITY_BOUNDARY = {
    "paper_face_title_literal": "松江区2024学年度第二学期等级考质量监控试卷",
    "paper_title_evidence_status": "candidate_source_conflict_record_not_new_cover_page_review",
    "year": 2025,
    "region_label": "松江区",
    "paper_family": "second_mock_repost_classified",
    "paper_type": "二模（目录/公众号分类；卷面称等级考质量监控试卷）",
    "covered_scope": "主题二（Fe(OH)₂的制备）",
    "coverage_scope": "theme_2_only_9_printed_questions_9_atomic_parts",
    "complete_paper_visual_scan_claim_allowed": False,
    "second_mock_face_claim_allowed": False,
    "official_identity_claim_allowed": False,
    "official_status": "nonofficial",
    "source_account": "学教有方",
    "answer_authority": "nonofficial_reference",
    "answer_independently_verified": False,
}


class Songjiang2025Theme2DirectVisualScanError(MasterDirectVisualScanError):
    pass


def _require(condition, code, message):
    if not condition:
        raise Songjiang2025Theme2DirectVisualScanError(code, message)


def _crop_id(asset, digest):
    # An opaque transport handle, never an inferred question ID or evidence role.
    return "SJ25T2-C-" + _sha256((asset + "\n" + digest).encode())[:24]


class Songjiang2025Theme2DirectVisualScanReader(
    Pudong2026FirstMockThemeDirectVisualScanReader
):
    def __init__(self, shchem_root: Path, master_workbench=None):
        self.shchem_root = Path(shchem_root).resolve()
        self.product_root = self.shchem_root / PRODUCT_RELATIVE
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _snapshot(self) -> _Snapshot:
        _, raw = self._verified_file(
            self.shchem_root, (PRODUCT_RELATIVE / "candidate_manifest.json").as_posix()
        )
        _require(
            len(raw) == EXPECTED_MANIFEST_BYTES
            and _sha256(raw) == EXPECTED_MANIFEST_FILE_SHA256,
            "songjiang_manifest_changed",
            "Songjiang candidate manifest bytes changed",
        )
        manifest = _json_object(raw, "Songjiang manifest")
        _require(
            manifest.get("batch_id") == EXPECTED_BATCH_ID
            and manifest.get("source_id") == SOURCE_ID
            and manifest.get("package_path") == PRODUCT_RELATIVE.as_posix()
            and (
                manifest.get("question_count"),
                manifest.get("part_count"),
                manifest.get("local_crop_count"),
            )
            == (9, 9, 20)
            and manifest.get("answer_authority") == "nonofficial_reference_only"
            and manifest.get("rights_mode")
            == "local_research_only_no_direct_pixel_or_component_reuse",
            "songjiang_manifest_invalid",
            "Songjiang candidate contract changed",
        )
        review = manifest.get("review_state", {})
        _require(
            all(
                review.get(key) is False
                for key in (
                    "teacher_or_human",
                    "lineage_gate_complete",
                    "retrieval_ready",
                    "generation_allowed",
                    "diagnosis_allowed",
                    "publication_allowed",
                )
            ),
            "songjiang_gate_elevated",
            "candidate authority gates changed",
        )
        declared = {}

        def bind(path, digest):
            _require(
                isinstance(path, str) and isinstance(digest, str),
                "songjiang_binding_invalid",
                "a source binding is incomplete",
            )
            _require(
                path not in declared or declared[path] == digest,
                "songjiang_binding_invalid",
                "source has conflicting hashes",
            )
            declared[path] = digest

        for path_key, hash_key in (
            ("candidate_file", "candidate_file_sha256"),
            ("evidence_map_file", "evidence_map_sha256"),
            ("shared_schema", "shared_schema_sha256"),
            ("taxonomy_asset", "taxonomy_sha256"),
            ("controlled_vocabulary_asset", "controlled_vocabulary_sha256"),
        ):
            bind(manifest[path_key], manifest[hash_key])
        upstream = manifest["upstream_theme_evidence"]
        bind(upstream["pilot_asset"], upstream["pilot_sha256"])
        bind(upstream["crop_manifest_asset"], upstream["crop_manifest_sha256"])
        all_crops = [*manifest["local_crops"], *upstream["bindings"]]
        _require(
            len(all_crops) == 24,
            "songjiang_count_changed",
            "crop binding count changed",
        )
        for row in [*manifest["source_assets"], *all_crops]:
            bind(row["asset"], row["sha256"])
            if "source_asset" in row:
                bind(row["source_asset"], row["source_sha256"])
        bound_bytes, bindings = {}, {}
        for path, digest in declared.items():
            _, content = self._verified_file(self.shchem_root, path)
            _require(
                _sha256(content) == digest,
                "songjiang_source_changed",
                "a frozen source or crop changed",
            )
            bound_bytes[path] = content
            bindings[path] = {"sha256": digest, "bytes": len(content)}
        candidates = _jsonl_objects(
            bound_bytes[manifest["candidate_file"]], "Songjiang candidates"
        )
        schema = _json_object(
            bound_bytes[manifest["shared_schema"]], "candidate schema"
        )
        validator = Draft202012Validator(schema)
        _require(
            len(candidates) == 9 and all(validator.is_valid(row) for row in candidates),
            "songjiang_candidate_invalid",
            "candidate schema or count changed",
        )
        evidence_map = _json_object(
            bound_bytes[manifest["evidence_map_file"]], "evidence map"
        )
        _require(
            evidence_map.get("batch_id") == EXPECTED_BATCH_ID
            and evidence_map.get("source_id") == SOURCE_ID,
            "songjiang_evidence_invalid",
            "evidence map identity changed",
        )
        types = {
            row["part_id"]: row for row in evidence_map["item_type_contract"]["entries"]
        }
        edges = {row["part_id"]: row for row in evidence_map["question_evidence"]}
        _require(
            set(types) == set(edges) == set(EXPECTED_ATOMIC_IDS)
            and len(evidence_map["question_evidence"])
            == len(evidence_map["item_type_contract"]["entries"])
            == 9,
            "songjiang_evidence_invalid",
            "one-to-one sidecar mapping changed",
        )
        crop_by_path = {}
        for crop in all_crops:
            path = crop["asset"]
            content = bound_bytes[path]
            dims = self._png_dimensions(content)
            box = crop["crop_box"]
            source_dims = self._png_dimensions(bound_bytes[crop["source_asset"]])
            _require(
                path not in crop_by_path
                and dims is not None
                and source_dims is not None
                and len(content) <= MAX_CROP_BYTES
                and len(box) == 4
                and all(type(v) is int for v in box)
                and min(box[:2]) >= 0
                and min(box[2:]) > 0
                and dims == tuple(box[2:])
                and box[0] + box[2] <= source_dims[0]
                and box[1] + box[3] <= source_dims[1],
                "songjiang_crop_invalid",
                "crop dimensions, bounds, or identity changed",
            )
            crop_by_path[path] = {
                **crop,
                "crop_id": _crop_id(path, crop["sha256"]),
                "output_path": path,
                "role": "unknown",
                "width": dims[0],
                "height": dims[1],
                "bytes": len(content),
                "pixel_reuse_allowed": False,
                "publication_allowed": False,
                "safe_http_status_if_routed": 403,
            }
        try:
            master = self.master_workbench._snapshot()
        except MasterWave1WorkbenchError as exc:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_master_unavailable", "verified Master identity unavailable"
            ) from exc
        master_atoms = {
            row["atomic_part_id"]: row for row in master.master_layers["atomic_part"]
        }
        exact = {
            mid
            for mid, relations in master.relations_by_master.items()
            for relation in relations
            if relation.get("relation_type") == "exact_1_to_1"
            and relation.get("identity_mapping_allowed") is True
        }
        _require(
            len(master_atoms) == 470
            and len(exact) == 169
            and not (set(EXPECTED_ATOMIC_IDS) & exact),
            "songjiang_master_overlap",
            "Master or exact169 boundary changed",
        )
        records = []
        for question_id, expected_part, question in zip(
            EXPECTED_PRINTED_IDS, EXPECTED_ATOMIC_IDS, candidates, strict=True
        ):
            part = question["parts"][0]
            atom = master_atoms.get(expected_part, {})
            _require(
                question["question_id"] == question_id
                and question["source_id"] == SOURCE_ID
                and len(question["parts"]) == 1
                and part["part_id"] == expected_part
                and atom.get("source_id") == SOURCE_ID
                and atom.get("parent_paper_id") == PAPER_ID
                and atom.get("parent_theme_big_question_id") == THEME_ID
                and atom.get("parent_printed_question_id") == question_id,
                "songjiang_master_identity_invalid",
                "explicit candidate/Master parent identity changed",
            )
            _require(
                question["gates"]
                and all(value is False for value in question["gates"].values())
                and question["review"]["human_checked"] is False
                and question["rights"]["direct_source_pixel_reuse_allowed"] is False
                and question["rights"]["component_reuse_allowed"] is False
                and all(
                    part[key] is False
                    for key in (
                        "human_checked",
                        "retrieval_ready",
                        "generation_allowed",
                        "publication_allowed",
                    )
                ),
                "songjiang_gate_elevated",
                "candidate or part gates changed",
            )
            locator = question["source_locator"]
            entry, type_entry = edges[expected_part], types[expected_part]
            _require(
                entry["question_id"] == type_entry["question_id"] == question_id
                and entry["ocr_used"] is False
                and type_entry["human_checked"] is False,
                "songjiang_evidence_invalid",
                "explicit item mapping changed",
            )
            descriptors, answers, roles_by_path = [], [], {}
            for edge in locator["page_refs"]:
                path, role = edge["crop_asset"], edge["role"]
                crop = crop_by_path.get(path)
                _require(
                    crop is not None
                    and role in {"question", "context", "answer"}
                    and crop["sha256"] == edge["crop_sha256"]
                    and crop["source_asset"] == edge["asset"]
                    and crop["source_sha256"] == edge["source_sha256"]
                    and crop["crop_box"] == edge["crop_box"]
                    and (
                        "page_number" not in crop
                        or crop["page_number"] == edge["page_number"]
                    ),
                    "songjiang_evidence_invalid",
                    "explicit page/crop/role binding changed",
                )
                mapped_role = "shared_material" if role == "context" else role
                _require(
                    path not in roles_by_path
                    and crop["role"] in {"unknown", mapped_role},
                    "songjiang_evidence_invalid",
                    "duplicate or conflicting evidence role",
                )
                roles_by_path[path] = role
                crop["role"] = mapped_role
                crop["safe_http_status_if_routed"] = 403 if role == "answer" else 200
                descriptor = {
                    "crop_id": crop["crop_id"],
                    "evidence_role": mapped_role,
                    "sha256": crop["sha256"],
                    "bytes": crop["bytes"],
                    "width": crop["width"],
                    "height": crop["height"],
                    "source_page": edge["page_number"],
                    "source_sha256": edge["source_sha256"],
                    "source_label": edge["page_label"],
                    "source_crop_box": deepcopy(edge["crop_box"]),
                    "binding_origin": "explicit_candidate_source_locator_page_refs",
                }
                (answers if role == "answer" else descriptors).append(descriptor)
            _require(
                roles_by_path.get(entry["question_crop"]) == "question"
                and roles_by_path.get(entry["answer_crop"]) == "answer"
                and sum(role == "question" for role in roles_by_path.values()) == 1
                and len(answers) == 1
                and all(
                    roles_by_path.get(path) == "context"
                    for path in entry["shared_stimulus_refs"]
                )
                and all(
                    roles_by_path.get(path) == "context"
                    for block in question["stimulus_blocks"]
                    for path in block["image_refs"]
                ),
                "songjiang_evidence_invalid",
                "question, answer, or shared context link changed",
            )
            answer = part["answer_evidence"]
            expected_answer = [
                edge for edge in locator["page_refs"] if edge["role"] == "answer"
            ]
            _require(
                answer["source_page_refs"] == expected_answer
                and answer["authority"] == "nonofficial_reference"
                and answer["official_answer_claim_allowed"]
                is answer["official_scoring_claim_allowed"]
                is False
                and answer["evidence_status"]
                in {"source_page_verified_and_part_aligned", "source_conflict"},
                "songjiang_answer_invalid",
                "nonofficial answer evidence changed",
            )
            records.append(self._project(question, type_entry, descriptors, answers))
        by_id = {row["hierarchy"]["atomic_part_id"]: row for row in records}
        crop_by_id = {crop["crop_id"]: crop for crop in crop_by_path.values()}
        _require(
            len(by_id) == 9 and len(crop_by_id) == 24,
            "songjiang_count_changed",
            "projection count changed",
        )
        return _Snapshot(
            records=tuple(records),
            by_master_id=by_id,
            crop_by_id=crop_by_id,
            output_bindings=bindings,
            output_bytes=bound_bytes,
            answer_crop_ids=frozenset(
                key for key, value in crop_by_id.items() if value["role"] == "answer"
            ),
            forbidden_crop_ids=frozenset(
                key
                for key, value in crop_by_id.items()
                if value["role"] not in {"question", "shared_material"}
            ),
            corrected_fields_by_master={key: () for key in by_id},
            manifest_self_sha256=None,
            master_crosswalk_manifest_self_sha256=self.master_workbench._integrity(
                master
            )["manifest_self_sha256"],
            paper_identity_boundary=deepcopy(PAPER_IDENTITY_BOUNDARY),
        )

    @staticmethod
    def _project(question, type_entry, descriptors, answer_descriptors):
        part = question["parts"][0]
        locator = question["source_locator"]
        source_answer = part["answer_evidence"]
        conflicts = [
            {
                "field": row["field"],
                "resolution_status": row["resolution_status"],
                "resolution_note": row["resolution_note"],
                "versions": [v["value"] for v in row["versions"]],
            }
            for row in question["source_conflicts_and_boundaries"]
        ]
        quality_notes = [
            row["resolution_note"]
            for row in conflicts
            if row["resolution_status"] == "unresolved_keep_all"
        ]
        classification = part["classification"]
        labels = {
            "label_status": "partial_source_candidate",
            "item_type": type_entry["item_type"],
            "selection_rule": part["selection_rule"],
            "primary_K": None,
            "supporting_K": [],
            "knowledge_candidates_K": [
                row["id"] for row in classification["knowledge_K"]
            ],
            "knowledge_role_status": "unknown_primary_supporting_not_distinguished",
            **{
                axis: [row["id"] for row in classification[field]]
                for axis, field in (
                    ("A", "ability_A"),
                    ("C", "context_C"),
                    ("R", "response_R"),
                    ("RP", "representation_RP"),
                )
            },
            "human_verified": False,
        }
        return {
            "scan_id": "SJ25T2-LOCAL-" + part["part_id"],
            "scan_status": "visual_scan_completed",
            "projection_kind": "read_only_existing_candidate_not_new_visual_review",
            "hierarchy": {
                "paper_id": PAPER_ID,
                "theme_id": THEME_ID,
                "theme_big_question_id": THEME_ID,
                "printed_question_id": question["question_id"],
                "atomic_part_id": part["part_id"],
                "printed_question_number": locator["printed_question_number"],
                "printed_sequence": int(locator["printed_question_number"]),
                "atomic_sequence_in_printed": 1,
                "printed_theme_title": locator["printed_theme_title"],
                "theme_page_span": deepcopy(locator["theme_page_span"]),
            },
            "visible_summary_zh": part["prompt_raw"],
            "response_requirement_zh": part["prompt_raw"],
            "theme_chain_role": {
                "role_zh": None,
                "status": "unknown_not_explicit_in_candidate",
            },
            "classification": labels,
            "difficulty": deepcopy(part["difficulty"]),
            "dependency": {
                "analysis_zh": "保留来源显式共享材料；前题依赖未被候选记录明确标注，不能由题号或共同材料推断不存在依赖。",
                "prior_atomic_part_ids": [],
                "status": "unknown_prior_dependency_not_recorded",
                "shared_material_crop_ids": [
                    row["crop_id"]
                    for row in descriptors
                    if row["evidence_role"] == "shared_material"
                ],
            },
            "shared_materials": [
                {
                    "block_id": row["block_id"],
                    "raw_text": row["raw_text"],
                    "normalized_text": row["normalized_text"],
                    "human_verified": False,
                }
                for row in question["stimulus_blocks"]
            ],
            "answer": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "reference_summary_zh": source_answer["answer_text"],
                "reference_points": source_answer["reference_points"],
                "source_evidence_status": source_answer["evidence_status"],
                "points_authority": source_answer["points_authority"],
                "rubric_status": source_answer["rubric_status"],
                "quality_note": "；".join(quality_notes) if quality_notes else None,
                "verified": False,
                "independently_verified": False,
            },
            "candidate_analysis": {
                "candidate_only": True,
                "correctness_verified": False,
                "solution_path_zh": [],
                "known_quality_note_code": "source_conflict_pending_teacher"
                if quality_notes
                else None,
            },
            "risks_and_limits": {
                "ambiguity_or_multiple_solutions_zh": quality_notes,
                "boundary_zh": [
                    "候选题答与标签未经教师审定；一审精确字节谱系存在缺口。",
                    "原像素仅限本地证据预览，不授权教学成品复用或发布。",
                ],
            },
            "quality_notes": conflicts,
            "symbol_validation": deepcopy(part["symbol_validation"]),
            "viewed_evidence": descriptors,
            "answer_source_evidence": answer_descriptors,
            "source_identity": {
                **deepcopy(PAPER_IDENTITY_BOUNDARY),
                "source_id": question["source_id"],
                "source_url": question["provenance"]["source_url"],
            },
            "authority": dict(AUTHORITY),
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

    @staticmethod
    def _integrity(snapshot):
        local = sum(
            path.startswith(PRODUCT_RELATIVE.as_posix() + "/")
            for path in snapshot.output_bindings
        )
        return {
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "manifest_self_sha256": None,
            "manifest_self_hash_status": "not_present_in_upstream_manifest",
            "master_crosswalk_manifest_self_sha256": snapshot.master_crosswalk_manifest_self_sha256,
            "output_binding_count": local,
            "source_binding_count": len(snapshot.output_bindings) - local,
            "record_count": len(snapshot.records),
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "fail_closed": True,
        }

    def catalog(self):
        snapshot = self._snapshot()
        items = []
        for record in snapshot.records:
            hierarchy = record["hierarchy"]
            items.append(
                {
                    "master_node_id": hierarchy["atomic_part_id"],
                    "scan_status": record["scan_status"],
                    "detail_available": True,
                    "question_pixels_available": True,
                    "question_evidence_count": sum(
                        row["evidence_role"] == "question"
                        for row in record["viewed_evidence"]
                    ),
                    "shared_evidence_count": sum(
                        row["evidence_role"] == "shared_material"
                        for row in record["viewed_evidence"]
                    ),
                    "corrected_fields": [],
                    "printed_question_id": hierarchy["printed_question_id"],
                    "source_year": 2025,
                    "source_region_or_school": "松江区",
                    "source_paper_type": PAPER_IDENTITY_BOUNDARY["paper_type"],
                    "candidate_only": True,
                    **reference_answer_catalog_metadata(
                        record["answer"],
                        record["risks_and_limits"],
                        record["candidate_analysis"]["known_quality_note_code"],
                    ),
                }
            )
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "count": len(items),
            "master_node_ids": list(snapshot.by_master_id),
            "items": items,
            "coverage": self._coverage(),
            "paper_identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def status(self):
        value = self.catalog()
        value["status"] = "visual_scan_completed"
        value["counts"] = {
            "expected_themes": 1,
            "expected_printed_questions": 9,
            "expected_atomic_parts": 9,
            "scan_records": 9,
            "visual_scan_completed": 9,
            "blocked_pending_broader_crop": 0,
            "question_crop_bindings": 9,
            "shared_crop_bindings": 5,
            "nonofficial_answer_crop_bindings": 9,
            "boundary_crop_bindings": 1,
            "total_exact_crop_bindings": 24,
        }
        return value

    @staticmethod
    def _validate_id(value, field):
        try:
            validate_identifier(value, field)
        except SecurityError as exc:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_identifier_invalid",
                "invalid source evidence identifier",
                400,
            ) from exc

    def detail(self, master_node_id):
        self._validate_id(master_node_id, "master_node_id")
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_node_not_found",
                "node has no Songjiang theme-two reference",
                404,
            )
        reference = project_reference_answer(
            record["answer"],
            record["risks_and_limits"],
            record["candidate_analysis"]["known_quality_note_code"],
        )
        reference.update(
            {
                key: record["answer"][key]
                for key in (
                    "reference_points",
                    "points_authority",
                    "rubric_status",
                    "source_evidence_status",
                )
            }
        )
        reference["quality_note"] = record["answer"]["quality_note"]
        return {
            "product_id": PRODUCT_ID,
            "paper_id": PAPER_ID,
            "scope": SCOPE,
            "master_node_id": master_node_id,
            "scan_id": record["scan_id"],
            "scan_status": record["scan_status"],
            "projection_kind": record["projection_kind"],
            "scan_hierarchy": deepcopy(record["hierarchy"]),
            "identity_boundary": deepcopy(PAPER_IDENTITY_BOUNDARY),
            "source_identity": deepcopy(record["source_identity"]),
            **{
                key: deepcopy(record[key])
                for key in (
                    "visible_summary_zh",
                    "response_requirement_zh",
                    "theme_chain_role",
                    "dependency",
                    "shared_materials",
                    "candidate_analysis",
                    "quality_notes",
                    "risks_and_limits",
                    "symbol_validation",
                )
            },
            "scan_classification": deepcopy(record["classification"]),
            "cognitive_difficulty": deepcopy(record["difficulty"]),
            "evidence_descriptors": [
                {
                    **deepcopy(row),
                    "content_type": "image/png",
                    "access": "teacher_loopback_read_only",
                }
                for row in record["viewed_evidence"]
            ],
            "answer_source_evidence": [
                {**deepcopy(row), "access": "source_binding_only_pixel_route_forbidden"}
                for row in record["answer_source_evidence"]
            ],
            "answer_boundary": {
                "availability": "present_part_aligned",
                "authority": "nonofficial_reference",
                "verified": False,
            },
            "reference_answer": reference,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(self, master_node_id, crop_id):
        self._validate_id(master_node_id, "master_node_id")
        self._validate_id(crop_id, "crop_id")
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_node_not_found", "node has no Songjiang reference", 404
            )
        if crop_id in snapshot.forbidden_crop_ids:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_crop_role_denied",
                "answer and unassigned crop pixels are forbidden",
                403,
            )
        descriptor = next(
            (row for row in record["viewed_evidence"] if row["crop_id"] == crop_id),
            None,
        )
        if descriptor is None:
            raise Songjiang2025Theme2DirectVisualScanError(
                "songjiang_crop_not_found",
                "crop is not explicitly linked to this node",
                404,
            )
        crop = snapshot.crop_by_id[crop_id]
        raw = snapshot.output_bytes[crop["output_path"]]
        _require(
            crop["role"] in {"question", "shared_material"}
            and _sha256(raw) == descriptor["sha256"]
            and len(raw) == descriptor["bytes"]
            and len(raw) <= MAX_CROP_BYTES,
            "songjiang_crop_invalid",
            "served crop binding changed",
        )
        return CandidateCropPayload(data=raw, sha256=descriptor["sha256"])


__all__ = [
    "AUTHORITY",
    "EXPECTED_ATOMIC_IDS",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "PAPER_ID",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
    "THEME_ID",
    "Songjiang2025Theme2DirectVisualScanError",
    "Songjiang2025Theme2DirectVisualScanReader",
]
