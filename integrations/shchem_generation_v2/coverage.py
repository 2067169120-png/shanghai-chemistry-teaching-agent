"""Evidence-bounded structural and curriculum coverage matrix for prefreeze."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .content import PAPER_ID, VERSION_ID
from .content_metadata import validate_content_metadata_contract
from .core import sha256_file


WORKSPACE = Path(__file__).resolve().parents[2]
PROFILE_PATH = WORKSPACE / "sh-chem-db/kb/shanghai_observed_standard_v1/profiles/OSV1-2026-LEVEL-RECALL-5T-CONTINUOUS.json"
PROFILE_EVIDENCE_PATH = WORKSPACE / "sh-chem-db/kb/shanghai_observed_standard_v1/evidence/OSV1-2026-LEVEL-RECALL-5T-CONTINUOUS.evidence.json"
TAXONOMY_PATH = WORKSPACE / "sh-chem-db/kb/knowledge_taxonomy.json"
ORGANIC_REGISTRY_PATH = WORKSPACE / "sh-chem-db/kb/figures/textbook_organic_expression_v1/source_registry.json"
COMPARATOR_PATHS = (
    WORKSPACE / "sh-chem-db/kb/paper_learning_v1/curated/deep_read_mvpplus_2026-08-09/worker_a/papers/PL-ebcab8c652c66d1e_minhang_2026_second_mock.json",
    WORKSPACE / "sh-chem-db/kb/paper_learning_v1/curated/deep_read_mvpplus_2026-08-09/worker_a/papers/PL-b9d5b7e994765825_jiading_2026_second_mock.json",
    WORKSPACE / "sh-chem-db/kb/paper_learning_v1/curated/deep_read_mvpplus_2026-08-09/worker_a/papers/PL-2241f75a91108235_songjiang_2026_second_mock.json",
)


MODULE_PARTS: dict[str, list[str]] = {
    "atomic_structure_and_periodicity": ["P06"],
    "chemical_equilibrium_and_rate": ["P09", "P10", "P12", "P13", "P14", "P15", "P22"],
    "electrolyte_and_aqueous_chemistry": ["P07", "P24", "P25", "P26", "P27", "P28", "P29", "P30"],
    "experiment_and_evidence": ["P04", "P08", "P15", "P23", "P24", "P27", "P28", "P29", "P30", "P34", "P36"],
    "inorganic_process_and_environment": ["P01", "P02", "P03", "P04", "P05", "P07", "P08", "P24", "P25", "P26", "P27", "P28", "P29", "P30", "P36"],
    "quantitative_reasoning": ["P01", "P05", "P11", "P16", "P17", "P21", "P27", "P32", "P33"],
    "organic_structure_function_and_route": ["P18", "P19", "P20", "P21", "P22", "P23"],
    "materials_structure_property_risk": ["P18", "P19", "P20", "P21", "P22", "P23", "P31", "P32", "P33", "P34", "P35", "P36"],
    "redox_and_electrochemistry": ["P02", "P03", "P05", "P25", "P26", "P27"],
}


KEYWORDS: dict[str, tuple[str, ...]] = {
    "atomic_structure_and_periodicity": ("原子", "核外", "周期", "轨道", "电子排布"),
    "chemical_equilibrium_and_rate": ("平衡", "速率", "焓", "温度", "压强", "活化能"),
    "electrolyte_and_aqueous_chemistry": ("电解质", "离子", "pH", "滴定", "水解", "酸碱"),
    "experiment_and_evidence": ("实验", "装置", "操作", "滴定", "误差", "洗涤", "分离", "取样"),
    "inorganic_process_and_environment": ("流程", "浸出", "沉淀", "矿", "回收", "焙烧", "过滤", "环境"),
    "quantitative_reasoning": (),
    "organic_structure_function_and_route": ("有机", "合成", "结构式", "官能团", "同分异构", "加成", "取代", "消去"),
    "materials_structure_property_risk": ("材料", "晶体", "纳米", "量子", "半导体", "金属", "结构性质"),
    "redox_and_electrochemistry": ("氧化", "还原", "电解", "原电池", "电极", "电子转移"),
}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_parts(paper: dict[str, Any]):
    for theme in paper["themes"]:
        for printed in theme["printed_questions"]:
            for part in printed["atomic_parts"]:
                yield theme, printed, part


def _source_ref(path: Path) -> dict[str, Any]:
    return {"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _comparator(path: Path) -> dict[str, Any]:
    source = _load(path)
    paper = source["paper"]
    parts: list[dict[str, Any]] = []
    multi_part = 0
    task_texts: list[str] = []
    for theme in paper["themes"]:
        for printed in theme["printed_questions"]:
            atomic = printed["atomic_parts"]
            parts.extend(atomic)
            if len(atomic) > 1:
                multi_part += 1
            task_texts.append(str(printed.get("task_summary") or ""))
            task_texts.extend(str(part.get("task_summary") or "") for part in atomic)
    joined = "\n".join(task_texts)
    module_counts: dict[str, int] = {}
    for module, words in KEYWORDS.items():
        if module == "quantitative_reasoning":
            module_counts[module] = sum(part.get("item_type") == "quantitative_calculation" for part in parts)
        elif module == "organic_structure_function_and_route":
            module_counts[module] = sum(part.get("item_type") == "organic_structure_or_route" for part in parts)
        else:
            module_counts[module] = sum(joined.count(word) for word in words)
    return {
        "source": _source_ref(path),
        "profile_id": source["profile_id"],
        "qualification_status": source["qualification_status"],
        "identity": source["identity"],
        "inspection_method": source["inspection_method"],
        "page_inspection_ledger_count": len(source["page_inspection_ledger"]),
        "counts": source["counts"],
        "theme_titles": [theme["printed_title_literal"] for theme in paper["themes"]],
        "theme_scores": paper["theme_scores"],
        "numbering_mode": paper["numbering_mode"],
        "standalone_choice_section_observed": paper["standalone_choice_section_observed"],
        "multi_atomic_printed_question_count": multi_part,
        "item_type_histogram": dict(sorted(Counter(part["item_type"] for part in parts).items())),
        "machine_keyword_module_hits": module_counts,
        "classification_boundary": "Counts use existing page-inspected task summaries only; they are structural comparison signals, not official coverage requirements or question-retrieval permission.",
    }


def build_coverage_matrix(
    paper: dict[str, Any], *, paper_sha256: str, task_card_sha256: str
) -> dict[str, Any]:
    parts = {part["part_id"]: part for _, _, part in _iter_parts(paper)}
    printed = [question for theme in paper["themes"] for question in theme["printed_questions"]]
    score_histogram = Counter(part["score"] for part in parts.values())
    item_histogram = Counter(part["item_type"] for part in parts.values())
    module_rows = []
    for module, ids in MODULE_PARTS.items():
        missing = [part_id for part_id in ids if part_id not in parts]
        module_rows.append(
            {
                "module": module,
                "atomic_part_ids": ids,
                "atomic_part_count": len(ids),
                "score": sum(parts[part_id]["score"] for part_id in ids if part_id in parts),
                "status": "covered" if not missing else "missing",
                "missing_part_ids": missing,
            }
        )
    profile = _load(PROFILE_PATH)
    profile_evidence = _load(PROFILE_EVIDENCE_PATH)
    organic_registry = _load(ORGANIC_REGISTRY_PATH)
    actual_hierarchy_counts = {
        "printed_question_count": len(printed),
        "atomic_part_count": len(parts),
    }
    declared_hierarchy_counts = paper.get("task_card", {}).get("hierarchy_counts")
    if declared_hierarchy_counts != actual_hierarchy_counts:
        raise RuntimeError(
            "coverage producer refuses hierarchy drift: "
            f"declared={declared_hierarchy_counts}, actual={actual_hierarchy_counts}"
        )
    content_metadata = validate_content_metadata_contract(paper, WORKSPACE)
    if content_metadata["status"] != "pass":
        raise RuntimeError(
            "coverage producer refuses incomplete content metadata: "
            f"{content_metadata['errors']}"
        )
    return {
        "schema_version": "1.0.0",
        "record_type": "generation_coverage_matrix",
        "version_id": VERSION_ID,
        "paper_id": PAPER_ID,
        "paper_sha256": paper_sha256,
        "task_card_sha256": task_card_sha256,
        "status": "pass" if all(row["status"] == "covered" for row in module_rows) else "fail",
        "generated_candidate": {
            "theme_count": len(paper["themes"]),
            "printed_question_count": len(printed),
            "atomic_part_count": len(parts),
            "atomic_to_printed_ratio": round(len(parts) / len(printed), 3),
            "numbering": [question["display_number"] for question in printed],
            "theme_printed_ranges": [
                {
                    "theme_id": theme["theme_id"],
                    "printed_question_ids": [q["printed_question_id"] for q in theme["printed_questions"]],
                    "atomic_part_ids": [p["part_id"] for q in theme["printed_questions"] for p in q["atomic_parts"]],
                    "score": theme["score"],
                }
                for theme in paper["themes"]
            ],
            "shared_multi_atomic_questions": [
                {
                    "printed_question_id": question["printed_question_id"],
                    "atomic_part_ids": [part["part_id"] for part in question["atomic_parts"]],
                    "dependency_edges": [
                        {"from": part["part_id"], "to": dependency}
                        for part in question["atomic_parts"]
                        for dependency in part.get("dependencies", [])
                    ],
                }
                for question in printed
                if len(question["atomic_parts"]) > 1
            ],
            "atomic_score_histogram": {str(key): score_histogram[key] for key in sorted(score_histogram)},
            "item_type_histogram": dict(sorted(item_histogram.items())),
            "module_coverage": module_rows,
            "content_metadata_contract": {
                "status": content_metadata["status"],
                "coverage": content_metadata["coverage"],
                "required_counts": content_metadata["required_counts"],
                "taxonomy_source": content_metadata["taxonomy_source"],
                "task_card_required_fields_complete": all(
                    field in paper.get("task_card", {})
                    for field in (
                        "grade",
                        "teaching_stage",
                        "purpose",
                        "knowledge_scope",
                        "ability_targets",
                        "difficulty_distribution",
                        "prohibited_content",
                    )
                ),
                "page_status_reason_code": "publication_layout_not_generated_at_prefreeze",
            },
            "score_authority": "project_template_mixed_atomic_scores_not_official",
            "declared_hierarchy_counts": dict(declared_hierarchy_counts),
            "deterministic_scope_boundary": "Coverage membership and counts are machine-checkable; chemistry correctness, ambiguity, answer uniqueness and Shanghai-style generalization still require two isolated Sol reviews and an independent adversarial check.",
        },
        "osv1_exact_source_baseline": {
            "profile": _source_ref(PROFILE_PATH),
            "evidence_manifest": _source_ref(PROFILE_EVIDENCE_PATH),
            "profile_id": profile["profile_id"],
            "profile_version": profile["profile_version"],
            "material_family": profile["material_family"],
            "exact_source_only": profile["applicability"]["exact_source_only"],
            "global_template_claim_allowed": profile["applicability"]["global_template_claim_allowed"],
            "question_page_count": profile_evidence["completeness"]["question_pages_observed"],
            "question_page_refs": [
                {"page_id": row["page_id"], "path": row["local_path"], "sha256": row["sha256"]}
                for row in profile_evidence["pages"]
                if row["role"] == "question"
            ],
            "root_exact_page_audit_observation": {
                "printed_numbering": "visible continuous 1-30",
                "theme_ranges": ["1-8", "9-15", "16-23", "24(shared process with at least 9 visible sub-prompts in source)", "25-30"],
                "calibration_decision": f"{VERSION_ID} uses {len(printed)} printed questions and {len(parts)} atomic parts derived from its frozen hierarchy; its shared process question has newly authored atomic children and does not copy source sub-prompts.",
                "authority_boundary": "Task-supplied root visual/OCR audit bound to the seven exact manifest page hashes; nonofficial recall, no source-question republication, no official or global-template claim.",
            },
            "profile_theme_titles": [row["title"] for row in profile["organization"]["themes"]],
            "profile_theme_scores_status": [row["score"]["value_status"] for row in profile["organization"]["themes"]],
            "profile_gates": profile["gates"],
        },
        "page_inspected_shanghai_comparators": [_comparator(path) for path in COMPARATOR_PATHS],
        "textbook_coverage_basis": {
            "taxonomy": _source_ref(TAXONOMY_PATH),
            "organic_expression_registry": _source_ref(ORGANIC_REGISTRY_PATH),
            "organic_source_pdf": organic_registry["source_pdf"],
            "relevant_chapters": [
                "TB-M1-C4 原子结构和化学键",
                "TB-M2-C5 金属及其化合物",
                "TB-M2-C6 化学反应速率和化学平衡",
                "TB-E1-C3 水溶液中的离子反应与平衡",
                "TB-E1-C4 氧化还原反应和电化学",
                "TB-E2-C1 原子结构与性质",
                "TB-E3-C1 认识有机化学",
                "TB-E3-C2 烃和卤代烃",
                "TB-E3-C5 有机化合物的合成与研究",
            ],
            "authority_boundary": "Local textbook/taxonomy coverage basis only; not a claim of the current official exam scope or scoring rule.",
        },
        "calibration_findings": [
            "OSV1 exact-source fields support five themes, continuous numbering, 100 points and 60 minutes, but theme and atomic scores remain unknown.",
            f"The exact page audit supports approximately thirty printed numbers and a printed/atomic distinction; {VERSION_ID} records {len(printed)} printed questions and {len(parts)} atomic parts from its own hierarchy.",
            "All three page-inspected district mocks have more atomic parts than printed questions and contain multi-atomic printed questions.",
            f"All three comparators contain organic structure/route tasks inside theme chains; {VERSION_ID} includes a full MCH-toluene structure, functional-group judgment, class, reversible route and reaction-type theme.",
            f"{VERSION_ID} atomic scores 2/3/4/5 are an explicit project template; comparator theme-score variation is a calibration signal, not proof of exact-profile atomic scoring.",
            "MCH C7H14/Mr98.2/6.16 wt%/59.4 kJ mol-H2 and the reversible structure route bind SRCEX-MCH-SEKINE-HIGO-2021-P2-V1, a local page-verified review extract; they do not inherit HOT-W1-HYD-004's narrower fact-card boundary and do not prove complete-system performance.",
        ],
        "review_requirements": {
            "sol_review_a_and_b_must_check": [
                f"{len(printed)} continuous printed questions and {len(parts)} atomic parts exactly as declared by the frozen hierarchy",
                "Q24 shared-material dependency chain",
                "mixed project-template score distribution totals 100",
                "organic structure/function/reaction/route completeness",
                "all nine module coverage rows",
                "OSV1 nonofficial exact-source-only and generation-authority boundaries",
            ],
            "human_reviewed": False,
            "official_claim_allowed": False,
            "source_question_republication": False,
        },
        "human_reviewed": False,
        "publication_allowed": False,
    }


def validate_coverage_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    generated = matrix.get("generated_candidate", {})
    declared = generated.get("declared_hierarchy_counts", {})
    if generated.get("printed_question_count") != declared.get("printed_question_count"):
        errors.append("printed_question_count must equal the frozen hierarchy declaration")
    if generated.get("atomic_part_count") != declared.get("atomic_part_count"):
        errors.append("atomic_part_count must equal the frozen hierarchy declaration")
    if generated.get("atomic_to_printed_ratio", 0) <= 1:
        errors.append("atomic_to_printed_ratio must be greater than 1")
    if not generated.get("shared_multi_atomic_questions"):
        errors.append("shared multi-atomic question missing")
    if len(generated.get("atomic_score_histogram", {})) < 3:
        errors.append("mixed atomic scores missing")
    modules = generated.get("module_coverage", [])
    if len(modules) != len(MODULE_PARTS) or any(row.get("status") != "covered" for row in modules):
        errors.append("required module coverage is incomplete")
    metadata = generated.get("content_metadata_contract", {})
    if metadata.get("status") != "pass":
        errors.append("content metadata contract did not pass")
    if metadata.get("coverage") != {
        "paper_page_span": 1,
        "theme_page_span": 5,
        "printed_page_anchor": 30,
        "atomic_page_anchor": 36,
        "atomic_textbook_mapping": 36,
    }:
        errors.append("content metadata coverage counts mismatch")
    if metadata.get("task_card_required_fields_complete") is not True:
        errors.append("task card required metadata fields incomplete")
    comparators = matrix.get("page_inspected_shanghai_comparators", [])
    if len(comparators) != 3:
        errors.append("exactly three page-inspected Shanghai comparators are required")
    if any(row.get("qualification_status") != "qualified_content_deep_read" for row in comparators):
        errors.append("comparator qualification mismatch")
    if matrix.get("status") != "pass":
        errors.append("matrix status is not pass")
    return {
        "check": "osv1_textbook_three_paper_coverage_matrix",
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }
