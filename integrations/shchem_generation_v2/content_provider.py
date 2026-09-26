"""Registry-gated content catalog and deterministic diagnostic selection API."""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .classification import ITEM_TYPE_VOCABULARY
from .content import PAPER_ID, VERSION_ID
from .core import sha256_file, write_json
from .explanations import DETAILED_EXPLANATIONS
from .governance_bridge import (
    ATOMIC_DIR,
    ANSWER_PATH,
    CHAIN_PATH,
    DB_ROOT,
    QUESTION_PATH,
    ROOT_RECONCILIATION_PATH,
    db_file_ref,
    generator_execution_metadata_binding,
    validate_r18_root_reconciliation,
    subject_pair_sha256,
    validate_controller_chain_path,
)
from .hierarchy import hierarchy_ids
from .r17_boundary import atomic_projection_contract
from .schema_validation import load_schema, validate


PROVIDER_VERSION = "shchem-content-provider/2.0.0"
STUDENT_PROVIDER_CONTRACT = "student_learning_generation_content_provider_v1"
STUDENT_PROVIDER_ID = "shchem-generation-v2-r18-production-provider"
WORKSPACE = Path(__file__).resolve().parents[2]
REVISION_SLUG = VERSION_ID.rsplit("-", 1)[-1].lower()
DEFAULT_PAPER = WORKSPACE / "staging/v1_generation/candidates" / REVISION_SLUG / "frozen_paper.json"
DEFAULT_OUTPUT = WORKSPACE / "staging/v1_generation/candidates" / REVISION_SLUG / "weekpack_content.json"
REGISTRY_PATH = DB_ROOT / "kb/machine_governance_v2/chain_registry.json"
STUDENT_PROVIDER_SCHEMA = (
    WORKSPACE / "integrations/student_learning_v1/generation_content_provider.schema.json"
)
GENERATOR_PROVENANCE_RECEIPT_PATH = DEFAULT_PAPER.parent / "generator_provenance_receipt.json"
STUDENT_OUTPUT_ROOT = WORKSPACE / "staging/v1_generation/student_learning" / REVISION_SLUG
_TEST_CONTENT_RENDERER: ContextVar[Callable[..., dict[str, Any]] | None] = ContextVar(
    "shchem_generation_v2_test_content_renderer", default=None
)
SECTION_ORDER = ("training", "day7", "day14")
SECTION_LABELS = {
    "training": "训练组：主攻诊断桶",
    "day7": "第7日：次要桶与跨主题巩固",
    "day14": "第14日：保持桶与延时迁移复测",
}

DEFAULT_SYNTHETIC_STRATEGY = {
    "student_profile_type": "synthetic_student_profile",
    "stage": "consolidation",
    "progress": 0.55,
    "main_buckets": ["K05_stoichiometry", "K11_redox_titration", "K16_experiment_safety"],
    "secondary_buckets": ["K04_electrochemistry", "K09_system_capacity", "K13_energy_wavelength"],
    "maintain_buckets": ["K06_equilibrium", "K14_material_structure_property", "K15_evidence_boundary"],
    "completed_part_ids": [],
    "section_counts": {"training": 5, "day7": 5, "day14": 5},
    "claim_boundary": "合成演示策略，不含真实学生数据，不代表个体诊断或学习效果。",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _part_rows(paper: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for theme_index, theme in enumerate(paper["themes"]):
        for printed_index, printed in enumerate(theme["printed_questions"]):
            for part_index, part in enumerate(printed["atomic_parts"]):
                yield {
                    "theme_index": theme_index,
                    "printed_index": printed_index,
                    "part_index": part_index,
                    "theme_id": theme["theme_id"],
                    "theme_title": theme["title"],
                    "theme_order": theme["order"],
                    "shared_material": theme["shared_material"],
                    "printed_question_id": printed["printed_question_id"],
                    "display_number": printed["display_number"],
                    "part": part,
                }


def _registered_governance(part_id: str, registry: dict[str, Any]) -> dict[str, Any]:
    chain_path = ATOMIC_DIR / part_id / "governance_chain.json"
    validation = validate_controller_chain_path(
        chain_path, require_state="automated_verified_candidate"
    )
    if validation.get("valid") is not True:
        raise RuntimeError(f"content provider rejected locally invalid child {part_id}: {validation}")
    chain = _load(chain_path)
    if chain.get("artifact_id") != part_id or chain.get("teacher_managed_delivery") is not None:
        raise RuntimeError(f"content child chain scope mismatch: {part_id}")
    expected_row = db_file_ref(chain_path)
    rows = registry.get("chains") if isinstance(registry.get("chains"), list) else []
    exact_matches = [row for row in rows if row == expected_row]
    if len(exact_matches) != 1:
        raise RuntimeError(
            f"central registry exact membership required for {part_id}; matches={len(exact_matches)}"
        )
    return {
        "child_chain_path": chain_path.resolve().relative_to(WORKSPACE.resolve()).as_posix(),
        "child_chain_sha256": expected_row["sha256"],
        "child_chain_bytes": expected_row["bytes"],
        "subject_pair_sha256": chain["subject"]["subject_pair_sha256"],
        "state": validation["achieved_state"],
        "controller_valid": True,
        "central_registry_exact_member": True,
        "registry_path": REGISTRY_PATH.resolve().relative_to(WORKSPACE.resolve()).as_posix(),
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "registry_row": exact_matches[0],
        "human_reviewed": False,
    }


def _entry(row: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    part = row["part"]
    part_id = part["part_id"]
    answer = part["answer"]
    explanation = answer.get("detailed_explanation")
    if not explanation or explanation != DETAILED_EXPLANATIONS.get(part_id):
        raise RuntimeError(f"governed detailed explanation missing or drifted: {part_id}")
    return {
        "atomic_part_id": part_id,
        "printed_question_id": row["printed_question_id"],
        "display_number": row["display_number"],
        "theme_id": row["theme_id"],
        "theme_title": row["theme_title"],
        "theme_order": row["theme_order"],
        "shared_material": row["shared_material"],
        "item_type": part["item_type"],
        "score": part["score"],
        "K": {
            "primary": part["primary_knowledge_K"],
            "supporting": part["supporting_knowledge_K"],
        },
        "A": part["ability_A"],
        "C": part["context_C"],
        "R": part["response_R"],
        "RP": part["representation_RP"],
        "cognitive_prelabel": part["difficulty"]["cognitive_prelabel"],
        "measured_difficulty": None,
        "difficulty_claim_boundary": part["difficulty"]["claim_boundary"],
        "prompt": part["prompt"],
        "options": part.get("options", []),
        "suggested_answer": answer["value"],
        "detailed_explanation": explanation,
        "suggested_score_points": answer["score_points"],
        "score_points_label": "suggested_nonofficial",
        "source_refs": part["evidence_refs"],
        "governance": _registered_governance(part_id, registry),
    }


def filter_content(
    bundle: dict[str, Any],
    *,
    theme_ids: Iterable[str] | None = None,
    knowledge_tokens: Iterable[str] | None = None,
    ability_tokens: Iterable[str] | None = None,
    item_types: Iterable[str] | None = None,
    difficulty_labels: Iterable[str] | None = None,
    exclude_part_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    themes = set(theme_ids or ())
    knowledge = set(knowledge_tokens or ())
    abilities = set(ability_tokens or ())
    types = set(item_types or ())
    unknown_types = sorted(types.difference(ITEM_TYPE_VOCABULARY))
    if unknown_types:
        raise ValueError(f"unknown controlled item_types: {unknown_types}")
    difficulties = set(difficulty_labels or ())
    excluded = set(exclude_part_ids)
    result = []
    for item in bundle.get("catalog", []):
        if item.get("item_type") not in ITEM_TYPE_VOCABULARY:
            raise ValueError(
                "content catalog contains an uncontrolled item_type: "
                f"{item.get('item_type')!r}"
            )
        item_knowledge = {item["K"]["primary"], *item["K"]["supporting"]}
        if item["atomic_part_id"] in excluded:
            continue
        if themes and item["theme_id"] not in themes:
            continue
        if knowledge and not knowledge.intersection(item_knowledge):
            continue
        if abilities and not abilities.intersection(item["A"]):
            continue
        if types and item["item_type"] not in types:
            continue
        if difficulties and item["cognitive_prelabel"] not in difficulties:
            continue
        result.append(copy.deepcopy(item))
    return result


def query_content(
    bundle: dict[str, Any],
    *,
    section_id: str | None = None,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query the full catalog or a previously materialized demonstration section."""

    if section_id is not None:
        for section in bundle.get("demonstration_selection", {}).get("sections", []):
            if section.get("section_id") == section_id:
                return copy.deepcopy(section.get("items", []))
        raise KeyError(f"unknown content section: {section_id}")
    return filter_content(bundle, **(filters or {}))


def _tokens(item: dict[str, Any]) -> set[str]:
    return {
        item["atomic_part_id"],
        item["theme_id"],
        item["K"]["primary"],
        *item["K"]["supporting"],
        *item["A"],
        *item["C"],
    }


def _difficulty_score(label: str, stage: str, section: str, progress: float) -> int:
    rank = {"D1": 1, "D2": 2, "D3": 3, "D4": 4}.get(label, 2)
    target = {"foundation": 1.5, "consolidation": 2.5, "transfer": 3.5}.get(stage, 2.5)
    if section == "day14":
        target += 0.5
    elif section == "training" and progress < 0.4:
        target -= 0.5
    return max(0, 50 - int(abs(rank - target) * 20))


def select_content(bundle: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    section = str(request.get("target_section", "training"))
    if section not in SECTION_ORDER:
        raise ValueError(f"target_section must be one of {SECTION_ORDER}")
    count = int(request.get("count", 5))
    available_count = len(bundle.get("catalog", []))
    if count < 1 or count > available_count:
        raise ValueError(f"count must be between 1 and {available_count}")
    stage = str(request.get("stage", "consolidation"))
    progress = float(request.get("progress", 0.5))
    if not 0.0 <= progress <= 1.0:
        raise ValueError("progress must be in [0, 1]")
    buckets = {
        "main": set(request.get("main_buckets", [])),
        "secondary": set(request.get("secondary_buckets", [])),
        "maintain": set(request.get("maintain_buckets", [])),
    }
    completed = set(request.get("completed_part_ids", []))
    excluded = completed | set(request.get("exclude_part_ids", []))
    section_weights = {
        "training": {"main": 400, "secondary": 220, "maintain": 80},
        "day7": {"main": 240, "secondary": 400, "maintain": 140},
        "day14": {"main": 140, "secondary": 260, "maintain": 400},
    }[section]
    ranked: list[tuple[int, str, dict[str, Any], list[str]]] = []
    for item in bundle.get("catalog", []):
        part_id = item["atomic_part_id"]
        if part_id in excluded:
            continue
        tokens = _tokens(item)
        reasons = ["not_previously_completed_or_selected"]
        score = _difficulty_score(item["cognitive_prelabel"], stage, section, progress)
        reasons.append(f"stage={stage}_matches_{item['cognitive_prelabel']}")
        for bucket_name in ("main", "secondary", "maintain"):
            matched = sorted(tokens.intersection(buckets[bucket_name]))
            if matched:
                score += section_weights[bucket_name]
                reasons.append(f"{bucket_name}_bucket_match:{','.join(matched)}")
        score += max(0, 30 - item["theme_order"])
        ranked.append((score, part_id, item, reasons))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    if len(ranked) < count:
        raise RuntimeError(f"only {len(ranked)} non-overlapping items available for {section}")
    selected = []
    theme_counts: dict[str, int] = {}
    remaining = ranked[:]
    while remaining and len(selected) < count:
        remaining.sort(
            key=lambda row: (
                -(row[0] - 35 * theme_counts.get(row[2]["theme_id"], 0)),
                row[1],
            )
        )
        score, _, item, reasons = remaining.pop(0)
        selected_item = copy.deepcopy(item)
        selected_item["selection"] = {
            "target_section": section,
            "deterministic_score": score,
            "reasons": reasons,
            "selection_policy": "bucket_priority_plus_stage_difficulty_plus_theme_diversity_v1",
        }
        selected.append(selected_item)
        theme_counts[item["theme_id"]] = theme_counts.get(item["theme_id"], 0) + 1
    return {
        "section_id": section,
        "label": SECTION_LABELS[section],
        "requested_count": count,
        "selected_part_ids": [item["atomic_part_id"] for item in selected],
        "items": selected,
        "non_overlap_inputs": sorted(excluded),
    }


def select_plan(bundle: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    selected_ids: set[str] = set(strategy.get("completed_part_ids", []))
    sections = []
    for section in SECTION_ORDER:
        request = {
            **strategy,
            "target_section": section,
            "count": int(strategy.get("section_counts", {}).get(section, 5)),
            "exclude_part_ids": sorted(selected_ids),
        }
        result = select_content(bundle, request)
        ids = set(result["selected_part_ids"])
        if ids.intersection(selected_ids):
            raise RuntimeError("selection overlap detected")
        selected_ids.update(ids)
        sections.append(result)
    return {
        "selection_type": "deterministic_synthetic_demonstration",
        "student_profile_type": strategy.get("student_profile_type", "synthetic_student_profile"),
        "strategy": copy.deepcopy(strategy),
        "sections": sections,
        "selected_part_ids": [
            item["atomic_part_id"] for section in sections for item in section["items"]
        ],
        "overlap_count": 0,
        "real_student_data_used": False,
        "personalization_claimed": False,
        "learning_effect_claimed": False,
    }


def export_content_bundle(
    *, paper_path: Path = DEFAULT_PAPER, output_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    paper = _load(paper_path)
    if paper.get("version_id") != VERSION_ID:
        raise RuntimeError("content provider paper version does not match immutable revision")
    registry = _load(REGISTRY_PATH)
    catalog = [_entry(row, registry) for row in _part_rows(paper)]
    expected_ids = hierarchy_ids(paper)["atomic_part_ids"]
    if [row["atomic_part_id"] for row in catalog] != expected_ids:
        raise RuntimeError(
            "content catalog must equal frozen hierarchy atomic membership exactly once in order"
        )
    bundle = {
        "schema_version": "2.0.0",
        "provider_version": PROVIDER_VERSION,
        "record_type": "registered_atomic_content_catalog",
        "version_id": paper["version_id"],
        "paper_id": paper["paper_id"],
        "paper_sha256": sha256_file(paper_path),
        "registry_path": REGISTRY_PATH.resolve().relative_to(WORKSPACE.resolve()).as_posix(),
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "catalog": catalog,
        "item_count": len(catalog),
        "content_status": "automated_verified_content",
        "student_profile": {
            "profile_type": "synthetic_student_profile",
            "profile_id": f"SYNTHETIC-DEMO-STUDENT-{REVISION_SLUG.upper()}",
            "real_student_data_used": False,
            "personal_diagnosis_claimed": False,
            "learning_effect_claimed": False,
        },
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "external_publication_allowed": False,
        "official_claim_allowed": False,
    }
    bundle["demonstration_selection"] = select_plan(bundle, DEFAULT_SYNTHETIC_STRATEGY)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, bundle)
    return bundle


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _exact_registration_status(paper: dict[str, Any]) -> dict[str, Any]:
    expected_atomic_ids = hierarchy_ids(paper)["atomic_part_ids"]
    expected_paths = [CHAIN_PATH, *[ATOMIC_DIR / part_id / "governance_chain.json" for part_id in expected_atomic_ids]]
    if not REGISTRY_PATH.is_file():
        return {
            "ready": False,
            "expected_chain_count": len(expected_paths),
            "exact_registered_count": 0,
            "expected_atomic_part_ids": expected_atomic_ids,
            "errors": ["central_registry_missing"],
        }
    registry = _load(REGISTRY_PATH)
    rows = registry.get("chains") if isinstance(registry.get("chains"), list) else []
    exact_count = 0
    errors: list[str] = []
    for path in expected_paths:
        if not path.is_file():
            errors.append(f"chain_missing:{path.name if path == CHAIN_PATH else path.parent.name}")
            continue
        expected = db_file_ref(path)
        if rows.count(expected) != 1:
            errors.append(f"registry_exact_membership_mismatch:{expected['path']}")
            continue
        validation = validate_controller_chain_path(
            path, require_state="automated_verified_candidate"
        )
        if validation.get("valid") is not True:
            errors.append(f"chain_invalid:{expected['path']}")
            continue
        exact_count += 1
    return {
        "ready": not errors and exact_count == len(expected_paths),
        "expected_chain_count": len(expected_paths),
        "exact_registered_count": exact_count,
        "expected_atomic_part_ids": expected_atomic_ids,
        "errors": errors,
    }


def _root_reconciliation_status() -> dict[str, Any]:
    candidates = sorted(
        (WORKSPACE / "staging/coordination/root/revisions/r18/reconciliation").glob(
            "*/root_provenance_reconciliation_r18.json"
        )
    )
    selected = ROOT_RECONCILIATION_PATH if ROOT_RECONCILIATION_PATH.is_file() else (
        candidates[0] if len(candidates) == 1 else ROOT_RECONCILIATION_PATH
    )
    try:
        attestation_path = selected.resolve().relative_to(WORKSPACE.resolve()).as_posix()
    except ValueError:
        attestation_path = str(selected)
    if not (
        selected.is_file()
        and QUESTION_PATH.is_file()
        and ANSWER_PATH.is_file()
        and GENERATOR_PROVENANCE_RECEIPT_PATH.is_file()
    ):
        return {
            "ready": False,
            "attestation_path": attestation_path,
            "errors": ["root_external_reconciliation_or_bound_generator_receipt_missing"],
        }
    try:
        metadata_binding = generator_execution_metadata_binding()
    except Exception:  # fail closed at the optional readiness boundary
        return {
            "ready": False,
            "attestation_path": attestation_path,
            "errors": ["generator_execution_metadata_invalid"],
        }
    try:
        expected_pair = subject_pair_sha256(
            sha256_file(QUESTION_PATH), sha256_file(ANSWER_PATH)
        )
        result = validate_r18_root_reconciliation(
            selected,
            workspace=WORKSPACE,
            expected_version_id=VERSION_ID,
            expected_paper_id=PAPER_ID,
            expected_execution=metadata_binding["reported_execution"],
            expected_projection=metadata_binding[
                "expected_root_observation_projection"
            ],
        )
    except Exception:  # validator/file races remain a stable readiness failure
        return {
            "ready": False,
            "attestation_path": attestation_path,
            "errors": ["root_reconciliation_validation_exception"],
        }
    if not isinstance(result, dict):
        return {
            "ready": False,
            "attestation_path": attestation_path,
            "errors": ["root_reconciliation_validation_exception"],
        }
    validation_errors = result.get("errors")
    if not isinstance(validation_errors, list):
        validation_errors = ["root_reconciliation_validation_result_invalid"]
    ready = (
        result.get("status") == "pass"
        and not validation_errors
        and result.get("actual_task_metadata") == metadata_binding["reported_execution"]
        and expected_pair
        == subject_pair_sha256(sha256_file(QUESTION_PATH), sha256_file(ANSWER_PATH))
    )
    attestation_ref = result.get("attestation_ref")
    return {
        "ready": ready,
        "attestation_path": attestation_path,
        "attestation_sha256": (
            attestation_ref.get("sha256")
            if isinstance(attestation_ref, dict)
            else None
        ),
        "errors": validation_errors,
    }


def _delivery_publication_status() -> dict[str, Any]:
    delivery_status = WORKSPACE / "exports/v1_demo/delivery_status.json"
    if not delivery_status.is_file():
        return {"ready": False, "errors": ["external_delivery_status_missing"]}
    try:
        from .gateway_api import _validated_delivery_bundle

        result = _validated_delivery_bundle(require_live_preflight=False)
    except Exception as exc:  # fail closed across optional final-stage dependencies
        return {"ready": False, "errors": [f"external_delivery_revalidation_error:{type(exc).__name__}"]}
    return {
        "ready": result.get("valid") is True,
        "archive_path": result.get("archive_path"),
        "archive_sha256": result.get("archive_sha256"),
        "errors": result.get("errors", []),
    }


def _student_document_builder_ready() -> bool:
    try:
        from .student_documents import render_student_learning_documents

        return callable(render_student_learning_documents)
    except Exception:
        return False


def student_learning_content_provider_status() -> dict[str, Any]:
    """Return live production readiness; test injections never affect this result."""

    blockers: list[str] = []
    paper: dict[str, Any] | None = None
    if DEFAULT_PAPER.is_file():
        try:
            paper = _load(DEFAULT_PAPER)
            if paper.get("version_id") != VERSION_ID or paper.get("paper_id") != PAPER_ID:
                raise ValueError("revision identity mismatch")
        except Exception:
            paper = None
    if paper is None:
        blockers.append("r18_frozen_paper_missing_or_invalid")
        registration = {
            "ready": False,
            "expected_chain_count": 37,
            "exact_registered_count": 0,
            "expected_atomic_part_ids": [],
            "errors": ["paper_hierarchy_unavailable"],
        }
    else:
        registration = _exact_registration_status(paper)
        if not registration["ready"]:
            blockers.append(
                "central_registry_exact_paper_plus_atomic_children_required:"
                f"registered={registration['exact_registered_count']}/"
                f"{registration['expected_chain_count']}"
            )
    reconciliation = _root_reconciliation_status()
    if not reconciliation["ready"]:
        blockers.append("root_external_reconciliation_required_for_generator_provenance")
    if not registration["ready"]:
        blockers.append("automated_verified_candidate_atomic_content_unavailable")
    publication = _delivery_publication_status()
    if not publication["ready"]:
        blockers.append("generation_external_delivery_promotion_and_zip_required")
    document_builder_ready = _student_document_builder_ready()
    if not document_builder_ready:
        blockers.append("student_formal_document_builder_unavailable")
    ready = not blockers
    return {
        "contract_version": STUDENT_PROVIDER_CONTRACT,
        "provider_id": STUDENT_PROVIDER_ID,
        "provider_mode": "production",
        "ready": ready,
        "production_ready": ready,
        "fixture_only": False,
        "required_document_count": 8,
        "root_external_reconciliation_ready": reconciliation["ready"],
        "automated_verified_content_ready": registration["ready"],
        "student_document_builder_ready": document_builder_ready,
        "generation_external_delivery_ready": publication["ready"],
        "registry_binding": registration,
        "root_external_reconciliation": reconciliation,
        "generation_external_delivery": publication,
        "blockers": blockers,
        "human_reviewed": False,
        "official": False,
        "publication_allowed": False,
    }


@contextmanager
def _temporary_governed_fixture_renderer(
    renderer: Callable[..., dict[str, Any]],
) -> Iterator[None]:
    """Unit-test-only injection; public readiness deliberately ignores it."""

    token = _TEST_CONTENT_RENDERER.set(renderer)
    try:
        yield
    finally:
        _TEST_CONTENT_RENDERER.reset(token)


def _validate_student_content(
    content: dict[str, Any],
    *,
    diagnosis: dict[str, Any],
    plan: dict[str, Any],
    allow_governed_fixture: bool,
) -> dict[str, Any]:
    validate(content, load_schema(STUDENT_PROVIDER_SCHEMA))
    from integrations.student_learning_v1.domain import validate_formal_content_export

    return validate_formal_content_export(
        content,
        diagnosis=diagnosis,
        plan=plan,
        allow_governed_fixture=allow_governed_fixture,
    )


def _strategy_from_student_inputs(
    diagnosis: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    def tags(value: Any) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "tag_id" and isinstance(child, str) and child:
                    found.append(child)
                else:
                    found.extend(tags(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(tags(child))
        return list(dict.fromkeys(found))

    focus = plan.get("weekly_focus") if isinstance(plan.get("weekly_focus"), dict) else {}
    main = tags(focus.get("main", {}))
    secondary = tags(focus.get("secondary", {}))
    all_diagnosis = tags(diagnosis)
    maintain = [tag for tag in all_diagnosis if tag not in {*main, *secondary}]
    stage = str(plan.get("stage", plan.get("stage_match", {}).get("stage", "consolidation")))
    progress = plan.get("progress", diagnosis.get("progress", 0.55))
    try:
        numeric_progress = min(1.0, max(0.0, float(progress)))
    except (TypeError, ValueError):
        numeric_progress = 0.55
    return {
        "student_profile_type": "anonymous_machine_diagnosis",
        "stage": stage if stage in {"foundation", "consolidation", "transfer"} else "consolidation",
        "progress": numeric_progress,
        "main_buckets": main,
        "secondary_buckets": secondary,
        "maintain_buckets": maintain,
        "completed_part_ids": [],
        "section_counts": {"training": 5, "day7": 5, "day14": 5},
        "claim_boundary": "机器治理内容选择，不声称真实学生学习效果。",
    }


def _production_question_and_answer(
    *,
    selected: dict[str, Any],
    section: str,
    sequence: int,
    parent_row: dict[str, Any],
    parent_question: dict[str, Any],
    parent_answer: dict[str, Any],
    answer_index: int,
    reconciliation_sha256: str,
    paper_source_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    part_id = selected["atomic_part_id"]
    child_dir = ATOMIC_DIR / part_id
    child_question_path = child_dir / "question.json"
    child_answer_path = child_dir / "answer.json"
    child_chain_path = child_dir / "governance_chain.json"
    child_question = _load(child_question_path)
    child_answer = _load(child_answer_path)
    projection = atomic_projection_contract(
        parent_question=parent_question,
        parent_answer=parent_answer,
        child_question=child_question,
        child_answer=child_answer,
        theme_index=parent_row["theme_index"],
        printed_index=parent_row["printed_index"],
        part_index=parent_row["part_index"],
        answer_index=answer_index,
    )
    pointer_rows = projection["components"]
    pointers = [row["parent_json_pointer"] for row in pointer_rows]
    canonical_projection = {
        row["parent_json_pointer"]: {
            "component": row["component"],
            "child_json_pointer": row["child_json_pointer"],
            "canonical_sha256": row["canonical_sha256"],
            "canonical_bytes": row["canonical_bytes"],
        }
        for row in pointer_rows
    }
    parent_chain = {
        "paper_id": PAPER_ID,
        "theme_id": selected["theme_id"],
        "printed_question_id": selected["printed_question_id"],
        "atomic_part_id": part_id,
    }
    task_id = f"{section.upper()}-{sequence:02d}-{part_id}"
    stem = selected["prompt"]
    if selected.get("options"):
        stem += "\n" + "　".join(selected["options"])
    parent_pair = subject_pair_sha256(
        sha256_file(QUESTION_PATH), sha256_file(ANSWER_PATH)
    )
    child_pair = subject_pair_sha256(
        sha256_file(child_question_path), sha256_file(child_answer_path)
    )
    question = {
        "task_id": task_id,
        "question_stem": stem,
        "score": int(selected["score"]),
        "source_ref": f"original-paper:{PAPER_ID}",
        "source_sha256": paper_source_sha256,
        "atomic_part_id": part_id,
        "canonical_parent_chain": parent_chain,
        "canonical_parent_chain_sha256": _canonical_hash(parent_chain),
        "governance_chain_path": str(child_chain_path.resolve()),
        "governance_chain_sha256": sha256_file(child_chain_path),
        "machine_governance_state": "automated_verified_candidate",
        "content_status": "machine_only_real_formal_content_candidate",
        "selection": copy.deepcopy(selected.get("selection", {})),
        "governed_atomic_content": {
            "atomic_part_id": part_id,
            "full_paper_question_ref": (
                f"{QUESTION_PATH.resolve()}#{pointers[0].rsplit('/prompt', 1)[0]}"
            ),
            "full_paper_answer_ref": (
                f"{ANSWER_PATH.resolve()}#/answers/{answer_index}"
            ),
            "parent_subject_pair": [parent_pair, child_pair],
            "component_json_pointers": pointers,
            "canonical_projection": canonical_projection,
            "canonical_projection_sha256": _canonical_hash(canonical_projection),
            "root_external_reconciliation_sha256": reconciliation_sha256,
        },
    }
    scoring = selected["suggested_score_points"]
    points = [
        str(row.get("criterion", row)) if isinstance(row, dict) else str(row)
        for row in scoring
    ]
    answer = {
        "task_id": task_id,
        "suggested_answer": selected["suggested_answer"],
        "detailed_explanation": selected["detailed_explanation"],
        "suggested_scoring_points": points,
        "official_scoring_points": False,
        "content_status": "machine_only_real_formal_content_candidate",
    }
    return question, answer


def _build_live_student_learning_content(
    *, diagnosis: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    from integrations.student_learning_v1.domain import (
        REQUIRED_FORMAL_DOCUMENTS,
        SHANGHAI_EXAM_PRINT_STYLE,
    )
    from .student_documents import render_student_learning_documents

    paper = _load(DEFAULT_PAPER)
    catalog_bundle = export_content_bundle(
        paper_path=DEFAULT_PAPER,
        output_path=STUDENT_OUTPUT_ROOT / "registered_atomic_catalog.json",
    )
    strategy = _strategy_from_student_inputs(diagnosis, plan)
    selection = select_plan(catalog_bundle, strategy)
    selected_by_section = {
        row["section_id"]: row["items"] for row in selection["sections"]
    }
    parent_question = _load(QUESTION_PATH)
    parent_answer = _load(ANSWER_PATH)
    answer_index_by_id = {
        row["atomic_part_id"]: index
        for index, row in enumerate(parent_answer["answers"])
    }
    parent_rows = {
        row["part"]["part_id"]: row for row in _part_rows(parent_question)
    }
    reconciliation_sha256 = sha256_file(ROOT_RECONCILIATION_PATH)
    paper_sha256 = sha256_file(DEFAULT_PAPER)
    question_groups: dict[str, list[dict[str, Any]]] = {}
    answer_groups: dict[str, list[dict[str, Any]]] = {}
    mapping = {
        "training": ("training_tasks", "training_answers"),
        "day7": ("retest_day7", "retest_day7_answers"),
        "day14": ("retest_day14", "retest_day14_answers"),
    }
    for section, (question_key, answer_key) in mapping.items():
        question_groups[question_key] = []
        answer_groups[answer_key] = []
        for sequence, selected in enumerate(selected_by_section[section], 1):
            part_id = selected["atomic_part_id"]
            question, answer = _production_question_and_answer(
                selected=selected,
                section=section,
                sequence=sequence,
                parent_row=parent_rows[part_id],
                parent_question=parent_question,
                parent_answer=parent_answer,
                answer_index=answer_index_by_id[part_id],
                reconciliation_sha256=reconciliation_sha256,
                paper_source_sha256=paper_sha256,
            )
            question_groups[question_key].append(question)
            answer_groups[answer_key].append(answer)
    diagnosis_hash = _canonical_hash(diagnosis)
    plan_hash = _canonical_hash(plan)
    receipt_sha256 = sha256_file(GENERATOR_PROVENANCE_RECEIPT_PATH)
    profile_id = str(diagnosis["profile_id"])
    cycle_index = int(plan["cycle_index"])
    themes = list(
        dict.fromkeys(
            item["theme_title"]
            for section in selection["sections"]
            for item in section["items"]
        )
    )
    content: dict[str, Any] = {
        "contract_version": STUDENT_PROVIDER_CONTRACT,
        "provider_contract": {
            "contract_version": STUDENT_PROVIDER_CONTRACT,
            "provider_id": STUDENT_PROVIDER_ID,
            "provider_mode": "production",
            "production_ready": True,
            "fixture_only": False,
            "generated_from": {
                "diagnosis_sha256": diagnosis_hash,
                "plan_sha256": plan_hash,
                "anonymous_profile_only": True,
            },
            "generator_receipt": {
                "provenance_status": "self_reported",
                "receipt_path": str(GENERATOR_PROVENANCE_RECEIPT_PATH.resolve()),
                "receipt_sha256": receipt_sha256,
            },
            "root_external_reconciliation": {
                "record_type": "root_external_reconciliation_attestation_v1",
                "status": "verified",
                "attestation_path": str(ROOT_RECONCILIATION_PATH.resolve()),
                "attestation_sha256": reconciliation_sha256,
                "root_scoped": True,
                "reconciles_generator_receipt_sha256": receipt_sha256,
            },
            "document_style": dict(SHANGHAI_EXAM_PRINT_STYLE),
            "required_document_names": sorted(REQUIRED_FORMAL_DOCUMENTS),
        },
        "profile_id": profile_id,
        "cycle_index": cycle_index,
        "diagnosis_sha256": diagnosis_hash,
        "plan_sha256": plan_hash,
        "claim_scope": "machine_only_real_formal_content_candidate",
        "content_completeness_status": "machine_only_real_formal_content_candidate",
        "human_reviewed": False,
        "official": False,
        "knowledge_handout": {
            "sections": [
                {
                    "title": f"主题复盘：{title}",
                    "explanation": (
                        "先识别题设对象和证据边界，再按守恒、条件、模型与表达顺序作答；"
                        "答案及采分点均为非官方机器建议。"
                    ),
                }
                for title in themes
            ]
        },
        **question_groups,
        **answer_groups,
        "sources": [
            {
                "source_ref": f"original-paper:{PAPER_ID}",
                "path": str(DEFAULT_PAPER.resolve()),
                "sha256": paper_sha256,
                "authority": "original_machine_governed_r18_candidate_not_official",
            }
        ],
        "rendered_documents": [],
        "document_parity": [],
        "selection_manifest": selection,
    }
    output_root = STUDENT_OUTPUT_ROOT / profile_id / f"cycle-{cycle_index}"
    documents, parity = render_student_learning_documents(content, output_root)
    content["rendered_documents"] = documents
    content["document_parity"] = parity
    return content


def build_student_learning_content(
    *, diagnosis: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Build a schema-valid student export or fail closed before any live render."""

    renderer = _TEST_CONTENT_RENDERER.get()
    if renderer is not None:
        fixture = renderer(diagnosis=diagnosis, plan=plan)
        _validate_student_content(
            fixture,
            diagnosis=diagnosis,
            plan=plan,
            allow_governed_fixture=True,
        )
        return fixture
    status = student_learning_content_provider_status()
    if status["ready"] is not True:
        raise RuntimeError(
            "student_learning_content_provider_not_ready:"
            + "|".join(status["blockers"])
        )
    content = _build_live_student_learning_content(diagnosis=diagnosis, plan=plan)
    _validate_student_content(
        content,
        diagnosis=diagnosis,
        plan=plan,
        allow_governed_fixture=False,
    )
    return content


class StudentLearningContentProviderAdapter:
    """Gateway-discoverable production adapter; it never exposes fixture readiness."""

    def status(self) -> dict[str, Any]:
        return student_learning_content_provider_status()

    def render(
        self, *, diagnosis: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, Any]:
        return build_student_learning_content(diagnosis=diagnosis, plan=plan)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export/query/select registered atomic content")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--paper", type=Path, default=DEFAULT_PAPER)
    export.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    query = sub.add_parser("query")
    query.add_argument("--bundle", type=Path, default=DEFAULT_OUTPUT)
    query.add_argument("--section", choices=SECTION_ORDER)
    select = sub.add_parser("select")
    select.add_argument("--bundle", type=Path, default=DEFAULT_OUTPUT)
    select.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_content_bundle(
            paper_path=args.paper.resolve(), output_path=args.output.resolve()
        )
    elif args.command == "query":
        result = {
            "section_id": args.section,
            "items": query_content(_load(args.bundle.resolve()), section_id=args.section),
        }
    else:
        result = select_content(_load(args.bundle.resolve()), _load(args.request.resolve()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
