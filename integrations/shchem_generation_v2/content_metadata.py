from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


TAXONOMY_RELATIVE_PATH = "sh-chem-db/kb/knowledge_taxonomy.json"
PAGE_REASON_CODE = "publication_layout_not_generated_at_prefreeze"
DIFFICULTY_FACTOR_KEYS = (
    "information_conversion",
    "reasoning_steps",
    "knowledge_span",
    "representation_switch",
    "calculation_load",
    "experiment_load",
    "openness",
    "unfamiliarity",
    "language_load",
    "prior_part_dependency",
)
REQUIRED_PROHIBITED_CONTENT = (
    ("standalone_choice_section", "禁止把选择题或不定项选择题建立为独立卷面板块。"),
    ("other_province_as_shanghai", "禁止把全国卷或外省题冒充上海本地试题或当前上海风格证据。"),
    ("legacy_textbook_drives_current_style", "禁止用旧教材或2023年及以前材料主导现行卷式、语言或难度。"),
    ("unverified_facts_or_data", "禁止使用未逐项核验且无可追溯边界的化学事实或定量数据。"),
    ("student_visible_answer_or_structure_leakage", "禁止在学生题面、图形、选项或元数据中泄漏答案、结构或解题路线。"),
    ("review_authority_misrepresentation", "禁止冒称human、teacher、expert或official审定及官方采分点。"),
    ("publication_or_delivery_authority_overreach", "禁止在完整治理、渲染、parity、ZIP和外置证明前声称发布或交付授权。"),
    ("recall_material_as_official", "禁止把等级考非官方回忆材料包装为官方原卷、答案或评分细则。"),
    ("source_screenshot_collage", "禁止直接拼贴或轻改教材、试卷、讲义或网页来源截图作为发布图。"),
    ("unsupported_mechanism_arrows", "禁止在合格证据样本为零时生成电子流弯箭或共振箭任务。"),
    ("student_private_data_egress", "禁止将学生身份、原始作答或未脱敏私有资料送入外发路径。"),
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _taxonomy_source(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = (workspace / TAXONOMY_RELATIVE_PATH).resolve()
    workspace_root = workspace.resolve()
    try:
        relative = path.relative_to(workspace_root).as_posix()
    except ValueError as exc:
        raise ValueError("knowledge taxonomy must remain inside the workspace") from exc
    data = json.loads(path.read_text(encoding="utf-8"))
    source = {
        "path": relative,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "schema_version": data.get("schema_version"),
    }
    return data, source


def blocked_page_span() -> dict[str, Any]:
    return {
        "status": "blocked_pending_review",
        "start_page": None,
        "end_page": None,
        "reason_code": PAGE_REASON_CODE,
    }


def blocked_page_anchor() -> dict[str, Any]:
    return {
        "status": "blocked_pending_review",
        "page_number": None,
        "reason_code": PAGE_REASON_CODE,
    }


def _parts(paper: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part
        for theme in paper.get("themes", [])
        for printed in theme.get("printed_questions", [])
        for part in printed.get("atomic_parts", [])
    ]


def _indexes(taxonomy: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    dimensions = taxonomy.get("dimensions", {})
    knowledge = {
        str(row.get("id")): row
        for row in dimensions.get("knowledge_points", [])
        if isinstance(row, dict)
    }
    abilities = {
        str(row.get("id")): row
        for row in dimensions.get("abilities", [])
        if isinstance(row, dict)
    }
    return knowledge, abilities


def _part_textbook_mapping(
    part: dict[str, Any], knowledge_index: dict[str, dict], source: dict[str, Any]
) -> dict[str, Any]:
    knowledge_ids = [part.get("primary_knowledge_K"), *part.get("supporting_knowledge_K", [])]
    if any(not isinstance(value, str) for value in knowledge_ids):
        raise ValueError(f"{part.get('part_id')}: knowledge identifiers must be strings")
    if len(knowledge_ids) != len(set(knowledge_ids)):
        raise ValueError(f"{part.get('part_id')}: duplicate primary/supporting knowledge identifiers")
    mappings: list[dict[str, Any]] = []
    merged: set[str] = set()
    for knowledge_id in knowledge_ids:
        row = knowledge_index.get(knowledge_id)
        if not row:
            raise ValueError(f"{part.get('part_id')}: taxonomy knowledge missing: {knowledge_id}")
        chapter_refs = row.get("textbook_chapter_refs")
        if not isinstance(chapter_refs, list) or not chapter_refs or any(
            not isinstance(value, str) or not value for value in chapter_refs
        ):
            raise ValueError(
                f"{part.get('part_id')}: textbook chapter mapping missing: {knowledge_id}"
            )
        chapter_refs = sorted(set(chapter_refs))
        merged.update(chapter_refs)
        mappings.append(
            {
                "knowledge_id": knowledge_id,
                "knowledge_name": row.get("name"),
                "textbook_chapter_refs": chapter_refs,
                "mapping_source": deepcopy(source),
            }
        )
    return {
        "status": "mapped",
        "knowledge_mappings": mappings,
        "merged_textbook_chapter_refs": sorted(merged),
    }


def _task_metadata(
    paper: dict[str, Any], taxonomy: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    parts = _parts(paper)
    knowledge_index, ability_index = _indexes(taxonomy)
    knowledge_parts: dict[str, list[str]] = defaultdict(list)
    ability_parts: dict[str, list[str]] = defaultdict(list)
    label_counts: Counter[str] = Counter()
    factor_counts: dict[str, Counter[str]] = {
        key: Counter() for key in DIFFICULTY_FACTOR_KEYS
    }
    for part in parts:
        part_id = str(part.get("part_id"))
        knowledge_ids = [part.get("primary_knowledge_K"), *part.get("supporting_knowledge_K", [])]
        for knowledge_id in knowledge_ids:
            if knowledge_id not in knowledge_index:
                raise ValueError(f"{part_id}: unknown taxonomy knowledge id: {knowledge_id}")
            knowledge_parts[str(knowledge_id)].append(part_id)
        for ability_id in part.get("ability_A", []):
            if ability_id not in ability_index:
                raise ValueError(f"{part_id}: unknown taxonomy ability id: {ability_id}")
            ability_parts[str(ability_id)].append(part_id)
        difficulty = part.get("difficulty", {})
        label = difficulty.get("cognitive_prelabel")
        if not isinstance(label, str):
            raise ValueError(f"{part_id}: cognitive prelabel missing")
        evidence = difficulty.get("evidence", {})
        if set(evidence) != set(DIFFICULTY_FACTOR_KEYS):
            raise ValueError(f"{part_id}: exact ten-factor difficulty evidence required")
        label_counts[label] += 1
        for key in DIFFICULTY_FACTOR_KEYS:
            factor_counts[key][str(evidence[key])] += 1

    knowledge_points = []
    for knowledge_id in sorted(knowledge_parts):
        row = knowledge_index[knowledge_id]
        chapter_refs = row.get("textbook_chapter_refs")
        if not isinstance(chapter_refs, list) or not chapter_refs:
            raise ValueError(f"task knowledge scope lacks chapter mapping: {knowledge_id}")
        knowledge_points.append(
            {
                "id": knowledge_id,
                "name": row.get("name"),
                "textbook_chapter_refs": sorted(set(chapter_refs)),
                "atomic_part_ids": sorted(set(knowledge_parts[knowledge_id])),
            }
        )
    abilities = [
        {
            "id": ability_id,
            "name": ability_index[ability_id].get("name"),
            "atomic_part_ids": sorted(set(ability_parts[ability_id])),
        }
        for ability_id in sorted(ability_parts)
    ]
    difficulty_ids = [
        str(row.get("id"))
        for row in taxonomy.get("dimensions", {}).get("difficulty", [])
        if isinstance(row, dict)
    ]
    return {
        "grade": "高三",
        "teaching_stage": "高三上海化学等级考综合模拟（项目模板、非官方）",
        "purpose": "machine_only_private_teacher_managed_practice_mock",
        "knowledge_scope": {
            "aggregation_source_fields": [
                "primary_knowledge_K",
                "supporting_knowledge_K",
            ],
            "taxonomy_source": deepcopy(source),
            "knowledge_points": knowledge_points,
        },
        "ability_targets": {
            "aggregation_source_field": "ability_A",
            "taxonomy_source": deepcopy(source),
            "abilities": abilities,
        },
        "difficulty_distribution": {
            "cognitive_prelabel": {
                "status": "project_template_cognitive_prelabel_aggregate",
                "source": "atomic_part.difficulty.cognitive_prelabel_and_ten_factor_evidence",
                "atomic_part_count": len(parts),
                "label_counts": [
                    {"id": difficulty_id, "count": label_counts.get(difficulty_id, 0)}
                    for difficulty_id in difficulty_ids
                ],
                "ten_factor_keys": list(DIFFICULTY_FACTOR_KEYS),
                "ten_factor_value_counts": {
                    key: [
                        {"value": value, "count": count}
                        for value, count in sorted(factor_counts[key].items())
                    ]
                    for key in DIFFICULTY_FACTOR_KEYS
                },
            },
            "measured_difficulty": {
                "status": "unknown",
                "sample_size": 0,
                "distribution": None,
                "reason_code": "no_real_student_response_data",
            },
        },
        "prohibited_content": [
            {"code": code, "rule": rule} for code, rule in REQUIRED_PROHIBITED_CONTENT
        ],
    }


def apply_content_metadata_contract(paper: dict[str, Any], workspace: Path) -> None:
    taxonomy, source = _taxonomy_source(workspace)
    knowledge_index, _ = _indexes(taxonomy)
    paper["page_span"] = blocked_page_span()
    for theme in paper.get("themes", []):
        theme["page_span"] = blocked_page_span()
        for printed in theme.get("printed_questions", []):
            printed["page_anchor"] = blocked_page_anchor()
            for part in printed.get("atomic_parts", []):
                part["page_anchor"] = blocked_page_anchor()
                part["textbook_chapter_mapping"] = _part_textbook_mapping(
                    part, knowledge_index, source
                )
    paper["task_card"].update(_task_metadata(paper, taxonomy, source))


def validate_content_metadata_contract(
    paper: dict[str, Any], workspace: Path
) -> dict[str, Any]:
    errors: list[str] = []
    taxonomy, source = _taxonomy_source(workspace)
    knowledge_index, _ = _indexes(taxonomy)
    expected_span = blocked_page_span()
    expected_anchor = blocked_page_anchor()
    coverage = {
        "paper_page_span": 0,
        "theme_page_span": 0,
        "printed_page_anchor": 0,
        "atomic_page_anchor": 0,
        "atomic_textbook_mapping": 0,
    }
    if paper.get("page_span") == expected_span:
        coverage["paper_page_span"] = 1
    else:
        errors.append("paper_page_span_must_be_blocked_pending_publication_layout")
    for theme in paper.get("themes", []):
        theme_id = theme.get("theme_id")
        if theme.get("page_span") == expected_span:
            coverage["theme_page_span"] += 1
        else:
            errors.append(f"{theme_id}:page_span_invalid")
        for printed in theme.get("printed_questions", []):
            question_id = printed.get("printed_question_id")
            if printed.get("page_anchor") == expected_anchor:
                coverage["printed_page_anchor"] += 1
            else:
                errors.append(f"{question_id}:page_anchor_invalid")
            for part in printed.get("atomic_parts", []):
                part_id = part.get("part_id")
                if part.get("page_anchor") == expected_anchor:
                    coverage["atomic_page_anchor"] += 1
                else:
                    errors.append(f"{part_id}:page_anchor_invalid")
                try:
                    expected_mapping = _part_textbook_mapping(
                        part, knowledge_index, source
                    )
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if part.get("textbook_chapter_mapping") == expected_mapping:
                    coverage["atomic_textbook_mapping"] += 1
                else:
                    errors.append(f"{part_id}:textbook_chapter_mapping_mismatch")

    expected_task = _task_metadata(paper, taxonomy, source)
    task = paper.get("task_card", {})
    for key, expected in expected_task.items():
        if task.get(key) != expected:
            errors.append(f"task_card:{key}:aggregate_or_contract_mismatch")
    required_counts = {
        "paper_page_span": 1,
        "theme_page_span": len(paper.get("themes", [])),
        "printed_page_anchor": sum(
            len(theme.get("printed_questions", [])) for theme in paper.get("themes", [])
        ),
        "atomic_page_anchor": len(_parts(paper)),
        "atomic_textbook_mapping": len(_parts(paper)),
    }
    if coverage != required_counts:
        errors.append("content_metadata_coverage_counts_mismatch")
    return {
        "check": "task_page_taxonomy_content_metadata_contract",
        "status": "pass" if not errors else "fail",
        "coverage": coverage,
        "required_counts": required_counts,
        "taxonomy_source": source,
        "errors": errors,
    }


def content_metadata_mutation_report(
    paper: dict[str, Any], workspace: Path
) -> dict[str, Any]:
    def mutate(path: tuple[Any, ...], value: Any = None, delete: bool = False) -> dict:
        candidate = deepcopy(paper)
        target: Any = candidate
        for component in path[:-1]:
            target = target[component]
        if delete:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return candidate

    mutations: list[tuple[str, dict[str, Any]]] = []
    mutations.append(("delete_task_teaching_stage", mutate(("task_card", "teaching_stage"), delete=True)))
    wrong_knowledge = deepcopy(paper)
    wrong_knowledge["task_card"]["knowledge_scope"]["knowledge_points"][0]["name"] = "错误聚合"
    mutations.append(("wrong_task_knowledge_aggregate", wrong_knowledge))
    extra_ability = deepcopy(paper)
    extra_ability["task_card"]["ability_targets"]["abilities"].append(
        {"id": "A10", "name": "综合问题链组织", "atomic_part_ids": ["P01"]}
    )
    mutations.append(("extra_task_ability_aggregate", extra_ability))
    wrong_chapter = deepcopy(paper)
    wrong_chapter["themes"][0]["printed_questions"][0]["atomic_parts"][0][
        "textbook_chapter_mapping"
    ]["merged_textbook_chapter_refs"] = ["TB-E3-C5"]
    mutations.append(("wrong_knowledge_to_chapter_mapping", wrong_chapter))
    mutations.append(("missing_theme_page_span", mutate(("themes", 0, "page_span"), delete=True)))
    mutations.append(("missing_printed_page_anchor", mutate(("themes", 0, "printed_questions", 0, "page_anchor"), delete=True)))
    mutations.append(("missing_atomic_page_anchor", mutate(("themes", 0, "printed_questions", 0, "atomic_parts", 0, "page_anchor"), delete=True)))
    mutations.append(("missing_atomic_textbook_mapping", mutate(("themes", 0, "printed_questions", 0, "atomic_parts", 0, "textbook_chapter_mapping"), delete=True)))
    forged_page = blocked_page_anchor()
    forged_page.update({"status": "verified", "page_number": 1})
    mutations.append(("forged_numeric_page_anchor", mutate(("themes", 0, "printed_questions", 0, "page_anchor"), forged_page)))
    measured = deepcopy(paper)
    measured["task_card"]["difficulty_distribution"]["measured_difficulty"].update(
        {"status": "measured", "sample_size": 36, "distribution": {"D2": 1.0}}
    )
    mutations.append(("measured_difficulty_without_student_data", measured))
    missing_rule = deepcopy(paper)
    missing_rule["task_card"]["prohibited_content"].pop()
    mutations.append(("required_prohibited_content_missing", missing_rule))
    cases = []
    for mutation_id, candidate in mutations:
        result = validate_content_metadata_contract(candidate, workspace)
        cases.append(
            {
                "mutation_id": mutation_id,
                "expected_rejected": True,
                "observed_rejected": result["status"] == "fail",
                "errors": result["errors"],
            }
        )
    baseline = validate_content_metadata_contract(paper, workspace)
    failures = [row["mutation_id"] for row in cases if not row["observed_rejected"]]
    if baseline["status"] != "pass":
        failures.append("baseline_content_metadata_contract_failed")
    return {
        "check": "content_metadata_fail_closed_mutations",
        "status": "pass" if not failures else "fail",
        "baseline": baseline,
        "cases": cases,
        "errors": failures,
    }
