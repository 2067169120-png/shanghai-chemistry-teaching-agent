from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from collections import Counter, defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image

from .classification import validate_atomic_classification
from .content_metadata import validate_content_metadata_contract


MAJOR_SUBSTANCE_CHINESE_NAME_VOCABULARY: dict[str, list[str]] = {
    "Cu2+": ["铜离子", "二价铜离子"],
    "Cu": ["铜", "铜单质"],
    "H2O": ["水蒸气", "水"],
    "O2": ["氧气", "氧"],
    "H+": ["氢离子"],
    "CO2": ["二氧化碳"],
    "H2": ["氢气", "氢"],
    "CH3OH": ["甲醇"],
    "ClO-": ["次氯酸根", "次氯酸根离子"],
    "I-": ["碘离子"],
    "I2": ["碘", "碘单质"],
    "Cl-": ["氯离子"],
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_paper(paper: dict) -> dict:
    errors: list[str] = []
    content_metadata = validate_content_metadata_contract(
        paper, Path(__file__).resolve().parents[2]
    )
    errors.extend(
        f"content_metadata:{error}" for error in content_metadata.get("errors", [])
    )
    task = paper.get("task_card", {})
    if task.get("version_id") != paper.get("version_id"):
        errors.append("task_card version_id must bind the paper revision")
    if task.get("paper_id") != paper.get("paper_id"):
        errors.append("task_card paper_id must bind the paper artifact")
    themes = paper.get("themes", [])
    if task.get("hierarchy") != "paper -> theme_big_question -> printed_question -> atomic_part":
        errors.append("hierarchy contract mismatch")
    if task.get("standalone_choice_section") is not False:
        errors.append("standalone choice section must be false")
    theme_count = task.get("theme_count", {}).get("value") if isinstance(task.get("theme_count"), dict) else task.get("theme_count")
    total_score = task.get("total_score", {}).get("value") if isinstance(task.get("total_score"), dict) else task.get("total_score")
    if theme_count != len(themes):
        errors.append(f"theme count mismatch: {theme_count} != {len(themes)}")
    seen_themes: set[str] = set()
    seen_questions: set[str] = set()
    seen_parts: set[str] = set()
    score = 0
    numbers: list[int] = []
    part_scores: list[int] = []
    multi_part_questions: list[str] = []
    dependency_edges: list[tuple[str, str]] = []
    organic_parts: list[str] = []
    for theme in themes:
        tid = theme.get("theme_id")
        if not tid or tid in seen_themes:
            errors.append(f"invalid or duplicate theme id: {tid}")
        seen_themes.add(tid)
        if theme.get("parent_paper_id") != paper.get("paper_id"):
            errors.append(f"theme parent mismatch: {tid}")
        theme_score = 0
        for question in theme.get("printed_questions", []):
            qid = question.get("printed_question_id")
            if not qid or qid in seen_questions:
                errors.append(f"invalid or duplicate printed question id: {qid}")
            seen_questions.add(qid)
            if question.get("parent_theme_id") != tid:
                errors.append(f"printed question parent mismatch: {qid}")
            numbers.append(int(question.get("display_number", -1)))
            atomic_parts = question.get("atomic_parts", [])
            if len(atomic_parts) > 1:
                multi_part_questions.append(qid)
            for part in atomic_parts:
                pid = part.get("part_id")
                if not pid or pid in seen_parts:
                    errors.append(f"invalid or duplicate part id: {pid}")
                seen_parts.add(pid)
                if part.get("parent_printed_question_id") != qid:
                    errors.append(f"atomic part parent mismatch: {pid}")
                classification = validate_atomic_classification(part)
                errors.extend(
                    f"classification:{pid}:{error}"
                    for error in classification["errors"]
                )
                response_type = part.get("response_R")
                representations = part.get("representation_RP", [])
                if response_type == "single_choice" and len(part.get("options", [])) < 2:
                    errors.append(f"classification:{pid}:single_choice_options_missing")
                if response_type == "calculation" and (
                    "quantitative_data" not in representations
                    or not isinstance(part.get("solver"), dict)
                ):
                    errors.append(
                        f"classification:{pid}:calculation_requires_quantitative_data_and_solver"
                    )
                if response_type == "chemical_equation" and (
                    "chemical_symbols" not in representations
                    or not isinstance(part.get("equation_balance"), dict)
                ):
                    errors.append(
                        f"classification:{pid}:chemical_equation_requires_symbols_and_balance"
                    )
                answer = part.get("answer", {})
                if answer.get("label") != "suggested":
                    errors.append(f"answer authority is not suggested: {pid}")
                if not answer.get("value"):
                    errors.append(f"answer missing: {pid}")
                scoring = answer.get("suggested_scoring", {})
                weights = scoring.get("criterion_weights", [])
                weighted_criteria = [row.get("criterion") for row in weights if isinstance(row, dict)]
                weighted_total = sum(
                    float(row.get("points", 0)) for row in weights if isinstance(row, dict)
                )
                if scoring.get("authority") != "suggested_nonofficial":
                    errors.append(f"suggested scoring authority mismatch: {pid}")
                if scoring.get("max_score") != part.get("score"):
                    errors.append(f"suggested scoring max score mismatch: {pid}")
                if weighted_criteria != answer.get("score_points", []):
                    errors.append(f"suggested scoring criteria mismatch: {pid}")
                if not math.isclose(weighted_total, float(part.get("score", 0)), abs_tol=1e-12):
                    errors.append(
                        f"suggested scoring weights do not sum to item score: {pid} "
                        f"{weighted_total} != {part.get('score')}"
                    )
                if not scoring.get("equivalent_response_policy"):
                    errors.append(f"equivalent-response policy missing: {pid}")
                if part.get("item_type") == "embedded_single_choice":
                    if (
                        scoring.get("aggregation") != "all_or_nothing_single_criterion"
                        or len(weights) != 1
                        or not math.isclose(float(weights[0].get("points", 0)), float(part.get("score", 0)), abs_tol=1e-12)
                    ):
                        errors.append(f"single-choice rubric must be one all-or-nothing criterion: {pid}")
                elif scoring.get("aggregation") != "sum_satisfied_criteria_capped_at_max_score":
                    errors.append(f"non-choice rubric aggregation mismatch: {pid}")
                if pid in {"P02", "P03", "P10", "P25"}:
                    contract = answer.get("major_substance_name_contract", {})
                    expected_rows = contract.get("required_species_names", [])
                    expected_by_id = {
                        row.get("species_id"): row.get("accepted_chinese_names")
                        for row in expected_rows
                        if isinstance(row, dict)
                    }
                    equation_species = {
                        row.get("name"): row.get("chinese_names")
                        for side in ("reactants", "products")
                        for row in (part.get("equation_balance") or {}).get(side, [])
                        if row.get("name") != "e-" and row.get("chinese_names")
                    }
                    name_rows = [
                        row for row in weights
                        if isinstance(row, dict)
                        and row.get("criterion_type") == "required_major_substance_names"
                    ]
                    if "名称" not in str(part.get("prompt", "")):
                        errors.append(f"major-substance name requirement missing from prompt: {pid}")
                    if (
                        contract.get("required") is not True
                        or contract.get("source") != "equation_balance_species_bidirectional_binding"
                        or contract.get("full_score_requires_all_species_names") is not True
                    ):
                        errors.append(f"major-substance structured contract missing: {pid}")
                    if expected_by_id != equation_species:
                        errors.append(f"major-substance species/name contract differs from equation: {pid}")
                    canonical_species = {
                        species_id: MAJOR_SUBSTANCE_CHINESE_NAME_VOCABULARY.get(species_id)
                        for species_id in expected_by_id
                    }
                    if any(names is None for names in canonical_species.values()):
                        errors.append(f"major-substance species missing from controlled vocabulary: {pid}")
                    elif any(
                        not accepted
                        or not set(accepted).issubset(set(canonical_species[species_id] or []))
                        for species_id, accepted in expected_by_id.items()
                    ):
                        errors.append(f"major-substance Chinese names exceed controlled vocabulary: {pid}")
                    if not name_rows or sum(float(row.get("points", 0)) for row in name_rows) <= 0:
                        errors.append(f"major-substance name criterion must carry positive points: {pid}")
                    elif set(name_rows[0].get("required_species_ids", [])) != set(expected_by_id):
                        errors.append(f"major-substance rubric species IDs mismatch: {pid}")
                    answer_value = str(answer.get("value", ""))
                    missing_answer_names = [
                        species_id
                        for species_id, accepted in expected_by_id.items()
                        if not any(name in answer_value for name in accepted or [])
                    ]
                    if missing_answer_names:
                        errors.append(
                            f"major-substance suggested answer missing names: {pid}:{missing_answer_names}"
                        )
                    max_without_names = weighted_total - sum(
                        float(row.get("points", 0)) for row in name_rows
                    )
                    if max_without_names >= float(part.get("score", 0)):
                        errors.append(f"response omitting major-substance names could still earn full score: {pid}")
                if len(part.get("originality", {}).get("changed_dimensions", [])) < 2:
                    errors.append(f"insufficient substantive change dimensions: {pid}")
                part_score = int(part.get("score", 0))
                part_scores.append(part_score)
                theme_score += part_score
                for dependency in part.get("dependencies", []):
                    dependency_edges.append((pid, dependency))
                if (
                    part.get("primary_knowledge_K") == "K16"
                    or "organic_structure" in part.get("representation_RP", [])
                    or "reaction_route" in part.get("representation_RP", [])
                ):
                    organic_parts.append(pid)
        if theme_score != theme.get("score"):
            errors.append(f"theme score mismatch: {tid} {theme_score} != {theme.get('score')}")
        score += theme_score
    if numbers != list(range(1, len(numbers) + 1)):
        errors.append(f"numbering is not continuous: {numbers}")
    hierarchy_counts = task.get("hierarchy_counts", {})
    if hierarchy_counts.get("printed_question_count") != len(seen_questions):
        errors.append("task hierarchy printed_question_count does not match frozen hierarchy")
    if hierarchy_counts.get("atomic_part_count") != len(seen_parts):
        errors.append("task hierarchy atomic_part_count does not match frozen hierarchy")
    if len(seen_parts) <= len(seen_questions):
        errors.append("atomic_part count must be materially greater than printed_question count")
    if not multi_part_questions:
        errors.append("at least one printed process/experiment question must have multiple atomic children")
    if not dependency_edges:
        errors.append("at least one atomic dependency edge is required")
    if len(set(part_scores)) < 3:
        errors.append("atomic score distribution must be mixed, with at least three score values")
    if not {"P18", "P19"} <= set(organic_parts):
        errors.append("complete organic theme must include structure classification and reaction route")
    p18 = next((part for theme in themes for question in theme.get("printed_questions", []) for part in question.get("atomic_parts", []) if part.get("part_id") == "P18"), {})
    if "官能团" not in p18.get("prompt", "") or "官能团" not in p18.get("answer", {}).get("value", ""):
        errors.append("organic theme must include an explicit functional-group judgment")
    mch_parts = {
        part.get("part_id"): part
        for theme in themes
        for question in theme.get("printed_questions", [])
        for part in question.get("atomic_parts", [])
        if part.get("part_id") in {"P18", "P19", "P20", "P21", "P22", "P23"}
    }
    for part_id, part in mch_parts.items():
        refs = set(part.get("evidence_refs", []))
        if "SRCEX-MCH-SEKINE-HIGO-2021-P2-V1" not in refs:
            errors.append(f"MCH part lacks exact source extract binding: {part_id}")
        if "HOT-W1-HYD-004" in refs:
            errors.append(f"MCH part improperly reuses fact-card boundary: {part_id}")
    p19_value = str(mch_parts.get("P19", {}).get("answer", {}).get("value", ""))
    if p19_value.count("—催化剂→") < 2:
        errors.append("P19 answer must place catalyst on both reaction arrows, not in a note")
    if score != total_score:
        errors.append(f"paper score mismatch: {score} != {total_score}")
    for flag in ("human_reviewed", "teaching_use_allowed", "publication_allowed"):
        if paper.get(flag) is not False:
            errors.append(f"{flag} must remain false")
    if paper.get("answer_authority") != "suggested" or paper.get("rubric_authority") != "suggested":
        errors.append("answer and rubric authority must remain suggested")
    if not paper.get("evidence_sources"):
        errors.append("at least one evidence source is required")
    observed = paper.get("observed_profile_contract", {})
    if observed.get("provisional_fixture") is not False:
        errors.append("final paper must not use a provisional observed profile")
    if not observed.get("profile_id") or not observed.get("profile_version"):
        errors.append("versioned observed profile identity is missing")
    if observed.get("profile_id") != "OSV1-2026-LEVEL-RECALL-5T-CONTINUOUS":
        errors.append("final level-exam demo must bind the 2026 level-exam recall profile")
    if observed.get("global_template_claim_allowed") is not False:
        errors.append("global template claim must remain false")
    if observed.get("official_claim_allowed") is not False:
        errors.append("official claim must remain false")
    gates = observed.get("profile_gates", {})
    if gates.get("question_retrieval_allowed") is not False or gates.get("unattended_generation_allowed") is not False:
        errors.append("observed profile gates must retain question-retrieval and unattended-generation denial")
    policy = paper.get("generation_policy", {})
    if policy != task.get("generation_policy"):
        errors.append("paper/task generation policy binding mismatch")
    if policy.get("profile_usage") != "read_only_structure_observation":
        errors.append("profile use must remain read-only structure observation")
    if policy.get("profile_generation_authority_claimed") is not False:
        errors.append("profile generation authority must not be claimed")
    if policy.get("original_question_generation") is not True:
        errors.append("original-question generation marker is required")
    if policy.get("source_question_republication") is not False:
        errors.append("source-question republication must remain false")
    authorization = policy.get("project_generation_authorization", {})
    if authorization.get("version") != "1.0.0" or not authorization.get("sha256"):
        errors.append("project generation authorization path/hash/version binding is missing")
    bindings = observed.get("field_bindings", {})
    for name in ("theme_count", "numbering_mode", "total_score", "duration_minutes"):
        if bindings.get(name) != task.get(name):
            errors.append(f"observed profile/task binding mismatch: {name}")
    def _overclaims(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized in {
                    "human_reviewed",
                    "teacher_reviewed",
                    "expert_reviewed",
                    "official_claim_allowed",
                    "release_allowed",
                    "publication_allowed",
                    "teaching_use_allowed",
                } and child is not False:
                    return True
                if _overclaims(child):
                    return True
        elif isinstance(value, list):
            return any(_overclaims(child) for child in value)
        return False
    if _overclaims(paper):
        errors.append("paper contains an official, human, teaching, publication or release overclaim")
    return {
        "check": "paper_schema_and_hierarchy",
        "status": "pass" if not errors else "fail",
        "theme_count": len(themes),
        "printed_question_count": len(seen_questions),
        "atomic_part_count": len(seen_parts),
        "total_score": score,
        "errors": errors,
    }


def _balance_side(species: list[dict]) -> tuple[Counter[str], int]:
    atoms: Counter[str] = Counter()
    charge = 0
    for item in species:
        coefficient = int(item["coefficient"])
        for element, count in item.get("atoms", {}).items():
            atoms[element] += coefficient * int(count)
        charge += coefficient * int(item.get("charge", 0))
    return atoms, charge


def validate_equations(paper: dict) -> dict:
    checks = []
    for part in iter_parts(paper):
        equation = part.get("equation_balance")
        if not equation:
            continue
        left_atoms, left_charge = _balance_side(equation["reactants"])
        right_atoms, right_charge = _balance_side(equation["products"])
        passed = left_atoms == right_atoms and left_charge == right_charge
        checks.append(
            {
                "part_id": part["part_id"],
                "status": "pass" if passed else "fail",
                "left_atoms": dict(left_atoms),
                "right_atoms": dict(right_atoms),
                "left_charge": left_charge,
                "right_charge": right_charge,
            }
        )
    return {
        "check": "deterministic_conservation",
        "status": "pass" if checks and all(c["status"] == "pass" for c in checks) else "fail",
        "equation_count": len(checks),
        "results": checks,
    }


def solve(solver: dict) -> Any:
    kind = solver["type"]
    p = solver["params"]
    if kind == "faraday_mass":
        return p["current_A"] * p["time_s"] * p["efficiency"] / (p["electron_number"] * p["faraday_C_mol"]) * p["molar_mass_g_mol"]
    if kind == "sequential_yield_mass":
        return p["initial_mol"] * p["conversion"] * p["selectivity"] * p["stoich_ratio"] * p["molar_mass_g_mol"]
    if kind == "ratio_division":
        return p["numerator"] / p["ratio"]
    if kind == "ratio_product":
        return p["numerator"] * p["ratio"]
    if kind == "iodometric_chlorine":
        thio_mol = p["thiosulfate_mol_L"] * p["thiosulfate_mL"] / 1000
        chlorine_mol = thio_mol / 2
        chlorine_mg = chlorine_mol * p["chlorine_molar_mass_g_mol"] * 1000
        return chlorine_mg / (p["sample_mL"] / 1000)
    if kind == "photon_wavelength_pair":
        return [p["constant_eV_nm"] / energy for energy in p["energies_eV"]]
    if kind == "angstrom_range_to_nm":
        return [value / 10 for value in p["values_A"]]
    raise ValueError(f"unknown solver type: {kind}")


def _numeric_close(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            math.isclose(float(a), float(e), abs_tol=tolerance, rel_tol=0)
            for a, e in zip(actual, expected)
        )
    return math.isclose(float(actual), float(expected), abs_tol=tolerance, rel_tol=0)


_SUPERSCRIPT_TRANSLATION = str.maketrans(
    {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "⁺": "+"}
)


def _display_numbers(value: str) -> list[float]:
    normalized = value.translate(_SUPERSCRIPT_TRANSLATION).replace("−", "-")
    normalized = re.sub(r"(?i)([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*e\s*([+-]?\d+)", r"\1×10\2", normalized)
    normalized = re.sub(r"10\s*\^\s*([+-]?\d+)", r"10\1", normalized)
    scientific = re.compile(
        r"(?<![A-Za-z0-9])([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[×xX*]\s*10\s*([+-]?\d+)"
    )
    spans: list[tuple[int, int]] = []
    values: list[tuple[int, float]] = []
    for match in scientific.finditer(normalized):
        spans.append(match.span())
        values.append((match.start(), float(match.group(1)) * (10 ** int(match.group(2)))))
    plain = re.compile(r"(?<![A-Za-z0-9])([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
    for match in plain.finditer(normalized):
        if any(left <= match.start() < right for left, right in spans):
            continue
        values.append((match.start(), float(match.group(1))))
    return [number for _, number in sorted(values)]


def _round_significant(value: float, digits: int) -> float:
    if digits < 1:
        raise ValueError("significant_figures must be positive")
    if value == 0:
        return 0.0
    decimal_value = Decimal(str(value))
    exponent = decimal_value.adjusted() - digits + 1
    quantum = Decimal("1").scaleb(exponent)
    return float(decimal_value.quantize(quantum, rounding=ROUND_HALF_UP))


def _display_status(part: dict, solver: dict, actual: Any, tolerance: float) -> tuple[bool, dict]:
    answer_value = str(part.get("answer", {}).get("value", ""))
    parsed = _display_numbers(answer_value)
    expected_values = actual if isinstance(actual, list) else [actual]
    declared_sig = solver.get("significant_figures")
    declared_sig_by_value = solver.get("significant_figures_by_value")
    accepted_strings = solver.get("accepted_display_strings", [])
    accepted_string_found = not accepted_strings or any(token in answer_value for token in accepted_strings)
    if len(parsed) < len(expected_values):
        return False, {
            "answer_value": answer_value,
            "parsed_display_values": parsed,
            "expected_display_values": expected_values,
            "significant_figures": declared_sig,
            "significant_figures_by_value": declared_sig_by_value,
            "accepted_display_strings": accepted_strings,
            "accepted_display_string_found": accepted_string_found,
            "reason": "too_few_display_numbers",
        }
    displayed = parsed[: len(expected_values)]
    if declared_sig_by_value is not None:
        if (
            not isinstance(declared_sig_by_value, list)
            or len(declared_sig_by_value) != len(expected_values)
            or any(not isinstance(digits, int) or digits < 1 for digits in declared_sig_by_value)
        ):
            return False, {
                "answer_value": answer_value,
                "parsed_display_values": displayed,
                "expected_display_values": expected_values,
                "significant_figures": declared_sig,
                "significant_figures_by_value": declared_sig_by_value,
                "accepted_display_strings": accepted_strings,
                "accepted_display_string_found": accepted_string_found,
                "reason": "invalid_significant_figures_by_value_contract",
            }
        rounded = [
            _round_significant(float(value), digits)
            for value, digits in zip(expected_values, declared_sig_by_value)
        ]
        numeric_ok = all(
            math.isclose(float(observed), float(expected), abs_tol=10 ** -12, rel_tol=10 ** -12)
            for observed, expected in zip(displayed, rounded)
        )
    elif declared_sig is not None:
        rounded = [_round_significant(float(value), int(declared_sig)) for value in expected_values]
        numeric_ok = all(
            math.isclose(float(observed), float(expected), abs_tol=10 ** -12, rel_tol=10 ** -12)
            for observed, expected in zip(displayed, rounded)
        )
    else:
        rounded = expected_values
        numeric_ok = all(
            math.isclose(float(observed), float(expected), abs_tol=tolerance, rel_tol=0)
            for observed, expected in zip(displayed, expected_values)
        )
    normalized_unit_count = len(re.findall(r"(?i)nm", answer_value))
    unit_ok = solver.get("unit") != "nm" or normalized_unit_count >= 1
    return numeric_ok and accepted_string_found and unit_ok, {
        "answer_value": answer_value,
        "parsed_display_values": displayed,
        "expected_display_values": rounded,
        "significant_figures": declared_sig,
        "significant_figures_by_value": declared_sig_by_value,
        "accepted_display_strings": accepted_strings,
        "accepted_display_string_found": accepted_string_found,
        "normalized_unit_count": normalized_unit_count,
        "reason": "matched" if numeric_ok and accepted_string_found and unit_ok else "display_numeric_unit_or_declared_string_mismatch",
    }


def validate_display_answer(part: dict, response_value: str) -> dict:
    """Validate a student-visible numeric answer against the declared solver contract."""
    solver = part.get("solver")
    if not isinstance(solver, dict):
        return {
            "check": "normalized_numeric_display_answer",
            "status": "not_applicable",
            "part_id": part.get("part_id"),
            "errors": ["solver_missing"],
        }
    candidate = json.loads(json.dumps(part, ensure_ascii=False))
    candidate.setdefault("answer", {})["value"] = response_value
    actual = solve(solver)
    passed, detail = _display_status(
        candidate, solver, actual, float(solver.get("tolerance", 1e-9))
    )
    return {
        "check": "normalized_numeric_display_answer",
        "status": "pass" if passed else "fail",
        "part_id": part.get("part_id"),
        "response_value": response_value,
        "detail": detail,
        "errors": [] if passed else [detail.get("reason", "display_mismatch")],
    }


def score_major_substance_name_response(part: dict, response_value: str) -> dict:
    """Enforce the structured name criterion as a real full-score ceiling.

    This deliberately does not pretend to grade every open-response criterion.
    It computes the maximum score that any downstream scorer may award after
    checking the equation-bound required Chinese substance names.
    """
    answer = part.get("answer", {})
    contract = answer.get("major_substance_name_contract", {})
    scoring = answer.get("suggested_scoring", {})
    required_rows = contract.get("required_species_names", [])
    name_rows = [
        row
        for row in scoring.get("criterion_weights", [])
        if isinstance(row, dict)
        and row.get("criterion_type") == "required_major_substance_names"
    ]
    if not required_rows or not name_rows:
        return {
            "check": "major_substance_name_response_full_score_gate",
            "status": "not_applicable",
            "part_id": part.get("part_id"),
            "full_score_allowed": True,
            "maximum_awardable_score": float(part.get("score", 0)),
            "errors": [],
        }
    normalized = re.sub(r"\s+", "", str(response_value)).casefold()
    matched: dict[str, str | None] = {
        str(row.get("species_id", "")): None for row in required_rows
    }
    occupied: list[tuple[int, int]] = []
    candidates: list[tuple[int, str, str, int, int]] = []
    for row in required_rows:
        species_id = str(row.get("species_id", ""))
        for alias in row.get("accepted_chinese_names", []):
            normalized_alias = re.sub(r"\s+", "", str(alias)).casefold()
            for occurrence in re.finditer(re.escape(normalized_alias), normalized):
                candidates.append(
                    (-len(normalized_alias), species_id, str(alias), occurrence.start(), occurrence.end())
                )
    for _, species_id, alias, start, end in sorted(candidates):
        if matched[species_id] is not None:
            continue
        if any(not (end <= left or start >= right) for left, right in occupied):
            continue
        matched[species_id] = alias
        occupied.append((start, end))
    missing = [species_id for species_id, name in matched.items() if name is None]
    name_points = sum(float(row.get("points", 0)) for row in name_rows)
    maximum = float(part.get("score", 0)) if not missing else float(part.get("score", 0)) - name_points
    return {
        "check": "major_substance_name_response_full_score_gate",
        "status": "pass" if not missing else "fail",
        "part_id": part.get("part_id"),
        "matched_names_by_species_id": matched,
        "missing_species_ids": missing,
        "name_criterion_points": name_points,
        "full_score_allowed": not missing,
        "maximum_awardable_score": maximum,
        "errors": [] if not missing else [f"required_species_names_missing:{missing}"],
    }


def validate_solvers(paper: dict) -> dict:
    results = []
    for part in iter_parts(paper):
        solver = part.get("solver")
        if not solver:
            continue
        actual = solve(solver)
        tolerance = float(solver.get("tolerance", 1e-9))
        raw_passed = _numeric_close(actual, solver["expected"], tolerance)
        display_passed, display_detail = _display_status(part, solver, actual, tolerance)
        answer_text = part["answer"]["value"].lower().replace("·", "").replace("⁻¹", "")
        unit = str(solver.get("unit", "")).lower()
        if unit == "kg system":
            unit_ok = "kg" in answer_text and ("system" in answer_text or "系统" in answer_text)
        elif unit == "l system":
            unit_ok = ("l" in answer_text or "升" in answer_text) and ("system" in answer_text or "系统" in answer_text)
        elif unit == "mg/l as cl2":
            unit_ok = "mg" in answer_text and ("l" in answer_text or "升" in answer_text) and ("cl₂" in answer_text or "cl2" in answer_text)
        elif unit == "g h2":
            unit_ok = "g" in answer_text and ("h₂" in answer_text or "h2" in answer_text or "氢" in answer_text)
        else:
            unit_ok = bool(unit) and unit in answer_text
        results.append(
            {
                "part_id": part["part_id"],
                "solver_type": solver["type"],
                "actual": actual,
                "expected": solver["expected"],
                "unit": solver["unit"],
                "raw_calculation_status": "pass" if raw_passed else "fail",
                "rounding_display_status": "pass" if display_passed else "fail",
                "rounding_display": display_detail,
                "unit_status": "pass" if unit_ok else "fail",
            }
        )
    return {
        "check": "independent_inverse_solve_and_units",
        "status": "pass"
        if results
        and all(
            r["raw_calculation_status"]
            == r["rounding_display_status"]
            == r["unit_status"]
            == "pass"
            for r in results
        )
        else "fail",
        "solver_count": len(results),
        "results": results,
    }


def normalize_stem(value: str) -> str:
    value = re.sub(r"\s+", "", value.lower())
    value = re.sub(r"\d+(?:\.\d+)?", "#", value)
    return re.sub(r"[^0-9a-z#\u4e00-\u9fff]", "", value)


def _trigrams(value: str) -> set[str]:
    return {value[i : i + 3] for i in range(max(0, len(value) - 2))}


def validate_dedup(
    paper: dict,
    source_hashes: set[str],
    figure_png: Path,
    *,
    formal_registry_path: Path | None = None,
) -> dict:
    parts = list(iter_parts(paper))
    normalized = {p["part_id"]: normalize_stem(p["prompt"]) for p in parts}
    exact_duplicates = []
    near_duplicates = []
    ids = list(normalized)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            a, b = normalized[left], normalized[right]
            if a == b:
                exact_duplicates.append([left, right])
            ta, tb = _trigrams(a), _trigrams(b)
            union = ta | tb
            score = len(ta & tb) / len(union) if union else 0.0
            if score >= 0.72:
                near_duplicates.append({"left": left, "right": right, "score": round(score, 4)})
    semantic = [p["originality"]["semantic_signature"] for p in parts]
    semantic_duplicates = [key for key, count in Counter(semantic).items() if count > 1]
    figure_hash = sha256_file(figure_png)
    level2_exact_source_reuse = figure_hash in source_hashes
    workspace = Path(__file__).resolve().parents[2]
    formal_registry = (
        formal_registry_path
        if formal_registry_path is not None
        else figure_png.parents[3] / "formal" / "questions.jsonl"
    ).resolve()
    formal_registry_portable = formal_registry.resolve().relative_to(workspace).as_posix()
    corpus_prompts: list[tuple[str, str]] = []
    if formal_registry.is_file():
        for line in formal_registry.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            for source_part in record.get("parts", []):
                prompt = source_part.get("prompt_normalized") or source_part.get("prompt_raw")
                if prompt:
                    corpus_prompts.append((source_part.get("part_id", record.get("question_id", "unknown")), normalize_stem(prompt)))
    corpus_matches = []
    threshold = 0.72
    for part_id, candidate in normalized.items():
        candidate_trigrams = _trigrams(candidate)
        for source_id, source in corpus_prompts:
            if candidate == source:
                corpus_matches.append({"part_id": part_id, "source_id": source_id, "score": 1.0})
                continue
            source_trigrams = _trigrams(source)
            union = candidate_trigrams | source_trigrams
            score = len(candidate_trigrams & source_trigrams) / len(union) if union else 0.0
            if score >= threshold:
                corpus_matches.append({"part_id": part_id, "source_id": source_id, "score": round(score, 4)})
    candidate_fingerprints = {
        part_id: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for part_id, text in normalized.items()
    }
    report = {
        "check": "four_level_deduplication",
        "algorithm_version": "shchem-four-level-dedup/2.0.0",
        "thresholds": {"normalized_trigram_jaccard": threshold},
        "corpus": {
            "formal_registry_path": formal_registry_portable,
            "formal_registry_sha256": sha256_file(formal_registry) if formal_registry.is_file() else None,
            "formal_prompt_count": len(corpus_prompts),
            "source_crop_hash_count": len(source_hashes),
        },
        "candidate_fingerprints": candidate_fingerprints,
        "levels": {
            "L1_file_url": {"status": "pass", "source_artifacts_embedded": False, "candidate_source_url": None},
            "L2_page_perceptual_or_exact": {"status": "fail" if level2_exact_source_reuse else "pass", "figure_exact_source_hash_match": level2_exact_source_reuse},
            "L3_normalized_stem": {"status": "fail" if exact_duplicates or near_duplicates or corpus_matches else "pass", "internal_exact": exact_duplicates, "internal_near": near_duplicates, "formal_corpus_matches": corpus_matches},
            "L4_semantic_route": {"status": "fail" if semantic_duplicates else "pass", "duplicates": semantic_duplicates},
        },
    }
    report["status"] = "pass" if all(v["status"] == "pass" for v in report["levels"].values()) else "fail"
    return report


def iter_parts(paper: dict):
    for theme in paper.get("themes", []):
        for question in theme.get("printed_questions", []):
            yield from question.get("atomic_parts", [])


def build_component_registry(
    workspace: Path, *, figure_id: str = "FIG-GEN-V2-EWASTE-ELECTROLYSIS-008"
) -> dict:
    source_registry = workspace / "sh-chem-db/kb/figures/apparatus_source_crop_batches/batch_04_school_exam_round05_2026-08-02/crop_registry.jsonl"
    rows = [json.loads(line) for line in source_registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_component: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for component in row.get("component_candidates", []):
            by_component[component].append(row)
    eligible = {
        component: [
            row
            for row in component_rows
            if row.get("paper_page_number") is not None or row.get("contained_physical_pages")
        ]
        for component, component_rows in by_component.items()
    }
    eligible = {component: rows for component, rows in eligible.items() if rows}
    selected = sorted(eligible)[:30]
    components = []
    for component in selected:
        row = eligible[component][0]
        evidence = {
            "crop_id": row["crop_id"],
            "crop_path": "sh-chem-db/" + row["crop_path_db_relative"],
            "crop_sha256": row["crop_sha256"],
            "source_path": "sh-chem-db/" + row["source_path_db_relative"],
            "source_sha256": row["source_sha256"],
            "paper_page_number": row.get("paper_page_number"),
            "contained_physical_pages": row.get("contained_physical_pages"),
            "bbox_coordinate_system": row["bbox_coordinate_system"],
            "bbox_xyxy": row["bbox_xyxy"],
            "authority_level": row["authority_level"],
            "source_article_url": row.get("source_article_url"),
        }
        components.append(
            {
                "component_id": "M2-" + component.removesuffix("_candidate").upper().replace("_", "-"),
                "name": component.removesuffix("_candidate"),
                "aliases": component.removesuffix("_candidate").split("_"),
                "search_keywords": [component, *component.removesuffix("_candidate").split("_")],
                "status": "searchable_internal_evidence_only",
                "evidence_refs": [evidence],
                "direct_pixel_reuse_allowed": False,
                "independent_redraw_required": True,
                "human_reviewed": False,
                "publication_allowed": False,
            }
        )
    authored_components = [
        "low_voltage_dc_supply",
        "graphite_anode",
        "stainless_steel_cathode",
        "open_electrolytic_cell",
        "porous_diaphragm",
        "gas_hood",
        "gas_delivery_tube",
        "flexible_collection_bag",
    ]
    for component in authored_components:
        components.append(
            {
                "component_id": "M2-AUTHORED-" + component.upper().replace("_", "-"),
                "name": component,
                "aliases": component.split("_"),
                "search_keywords": [component, *component.split("_")],
                "status": "independent_svg_component_machine_checked",
                "evidence_refs": [],
                "authored_asset_ref": f"assets/{figure_id}.svg",
                "direct_pixel_reuse_allowed": False,
                "independent_redraw_required": False,
                "human_reviewed": False,
                "publication_allowed": False,
            }
        )
    return {
        "schema_version": "2.0.0",
        "registry_id": "SHCHEM-MACHINE-V2-COMPONENTS-20260813",
        "source_registry": str(source_registry.relative_to(workspace)).replace("\\", "/"),
        "source_registry_sha256": sha256_file(source_registry),
        "component_count": len(components),
        "searchable": True,
        "claim_boundary": "内部证据组件索引；名称为机器候选，不能替代人工拓扑/化学确认，也不授权来源像素复用。",
        "components": components,
        "human_reviewed": False,
        "publication_allowed": False,
    }


def validate_component_registry(workspace: Path, registry: dict) -> dict:
    errors: list[str] = []
    components = registry.get("components", [])
    component_ids = [item.get("component_id") for item in components]
    if registry.get("component_count") != len(components) or len(components) < 25:
        errors.append("component registry must contain at least 25 counted entries")
    if len(component_ids) != len(set(component_ids)):
        errors.append("component IDs are not unique")
    for component in components:
        if component.get("direct_pixel_reuse_allowed") is not False:
            errors.append(f"pixel reuse must be false: {component.get('component_id')}")
        if component.get("publication_allowed") is not False:
            errors.append(f"source component publication must remain false: {component.get('component_id')}")
        if not component.get("search_keywords"):
            errors.append(f"component is not searchable: {component.get('component_id')}")
        for evidence in component.get("evidence_refs", []):
            source = workspace / evidence["source_path"]
            crop = workspace / evidence["crop_path"]
            if not source.is_file() or sha256_file(source) != evidence["source_sha256"]:
                errors.append(f"source hash/path mismatch: {component.get('component_id')}")
            if not crop.is_file() or sha256_file(crop) != evidence["crop_sha256"]:
                errors.append(f"crop hash/path mismatch: {component.get('component_id')}")
            bbox = evidence.get("bbox_xyxy")
            if not isinstance(bbox, list) or len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                errors.append(f"invalid crop coordinates: {component.get('component_id')}")
            if evidence.get("paper_page_number") is None and not evidence.get("contained_physical_pages"):
                errors.append(f"page provenance missing: {component.get('component_id')}")
    return {
        "check": "component_registry_provenance_and_searchability",
        "status": "pass" if not errors else "fail",
        "component_count": len(components),
        "verified_evidence_ref_count": sum(len(item.get("evidence_refs", [])) for item in components),
        "errors": errors,
    }


def figure_svg() -> str:
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="165mm" height="76mm" viewBox="0 0 990 456" role="img" aria-labelledby="title desc">
<title id="title">处理模拟含硫酸铜废液的低压直流电解装置</title>
<desc id="desc">开放式双电极电解槽，透明倒置集气罩的开放下缘浸入液面并覆盖石墨阳极，气体由罩顶导气管进入柔性收集袋；正负极导线连续、相互绝缘，收集袋满前必须停止。</desc>
<rect id="frame" x="4" y="4" width="982" height="448" fill="white" stroke="none" data-z-role="background"/>
<rect id="power" x="65" y="54" width="190" height="92" rx="8" fill="white" stroke="black" stroke-width="4" data-z-role="front_structure"/>
<text x="160" y="87" font-family="Arial, SimSun" font-size="24" text-anchor="middle">低压直流电源</text>
<text x="105" y="129" font-family="Arial" font-size="28" text-anchor="middle">+</text>
<text x="215" y="129" font-family="Arial" font-size="28" text-anchor="middle">−</text>
<path id="wire-positive" d="M105 140 L105 180 L365 180 L365 230" fill="none" stroke="black" stroke-width="5" data-network="electrical_positive" data-z-role="front_structure"/>
<circle id="positive-anode-junction" cx="365" cy="230" r="7" fill="white" stroke="black" stroke-width="4" data-network="electrical_positive" data-z-role="front_structure"/>
<path id="wire-negative-segments" d="M215 140 L215 165 L400 165 M434 165 L650 165 L650 226" fill="none" stroke="black" stroke-width="5" data-network="electrical_negative" data-z-role="front_structure"/>
<path id="cell-body" d="M290 214 L315 406 L700 406 L725 214" fill="none" stroke="black" stroke-width="5" data-z-role="transparent_body"/>
<path id="solution" d="M304 285 L711 285 L696 396 L319 396 Z" fill="#dddddd" stroke="black" stroke-width="2" data-z-role="liquid_fill"/>
<line id="solution-surface-left" x1="304" y1="285" x2="326" y2="285" stroke="black" stroke-width="3" data-z-role="front_structure"/>
<line id="solution-surface-center" x1="326" y1="285" x2="425" y2="285" stroke="black" stroke-width="3" stroke-dasharray="8 6" data-z-role="behind_transparent_hood"/>
<line id="solution-surface-right" x1="425" y1="285" x2="711" y2="285" stroke="black" stroke-width="3" data-z-role="front_structure"/>
<rect id="anode" x="350" y="230" width="30" height="150" fill="white" stroke="black" stroke-width="5" data-network="electrical_positive" data-z-role="front_structure"/>
<rect id="cathode" x="635" y="220" width="30" height="150" fill="white" stroke="black" stroke-width="5" data-z-role="front_structure"/>
<line id="diaphragm" x1="507" y1="245" x2="507" y2="396" stroke="black" stroke-width="3" stroke-dasharray="10 8" data-z-role="front_structure"/>
<path id="gas-hood" d="M326 320 L326 255 Q360 215 400 235" fill="none" stroke="black" stroke-width="4" data-network="gas_path" data-open-bottom="true" data-rim-submerged="true" data-covers-anode="true" data-z-role="transparent_front_structure"/>
<path id="gas-hood-right" d="M400 235 Q420 245 425 255 L425 320" fill="none" stroke="black" stroke-width="4" data-network="gas_path" data-z-role="transparent_front_structure"/>
<line id="gas-hood-open-rim-left" x1="318" y1="320" x2="337" y2="320" stroke="black" stroke-width="4" data-z-role="front_structure"/>
<line id="gas-hood-open-rim-right" x1="414" y1="320" x2="433" y2="320" stroke="black" stroke-width="4" data-z-role="front_structure"/>
<path id="gas-tube" d="M400 235 L417 235 L417 95 L730 95" fill="none" stroke="black" stroke-width="5" data-network="gas_path" data-z-role="front_structure"/>
<path id="wire-negative-bridge-underlay" d="M400 165 C400 138 434 138 434 165" fill="none" stroke="white" stroke-width="15" data-network="visual-separation" data-z-role="crossing_underlay"/>
<path id="wire-negative-bridge" d="M400 165 C400 138 434 138 434 165" fill="none" stroke="black" stroke-width="5" data-network="electrical_negative" data-crossing="bridge-over-gas-path" data-z-role="front_structure"/>
<path id="flex-bag" d="M730 95 Q755 50 805 76 Q840 112 807 148 Q760 179 730 145 L730 95 Z" fill="white" stroke="black" stroke-width="4" data-network="gas_path" data-z-role="front_structure"/>
<circle cx="393" cy="302" r="6" fill="white" stroke="black" stroke-width="2"/><circle cx="402" cy="279" r="5" fill="white" stroke="black" stroke-width="2"/><circle cx="409" cy="255" r="4" fill="white" stroke="black" stroke-width="2"/>
<text x="340" y="435" font-family="Arial, SimSun" font-size="22" text-anchor="middle">石墨阳极（+）</text>
<text x="665" y="435" font-family="Arial, SimSun" font-size="22" text-anchor="middle">不锈钢阴极（−）</text>
<text x="505" y="302" font-family="Arial, SimSun" font-size="20" text-anchor="middle">多孔隔膜</text>
<text x="507" y="386" font-family="Arial, SimSun" font-size="20" text-anchor="middle">模拟CuSO₄废液</text>
<text x="270" y="248" font-family="Arial, SimSun" font-size="18" text-anchor="middle">倒置集气罩</text>
<text x="270" y="271" font-family="Arial, SimSun" font-size="16" text-anchor="middle">（下端开放且浸液）</text>
<text x="844" y="103" font-family="Arial, SimSun" font-size="20">柔性收集袋</text>
<text x="844" y="128" font-family="Arial, SimSun" font-size="18">（满袋前停止）</text>
</svg>'''


def figure_spec(figure_id: str) -> dict:
    return {
        "schema_version": "2.0.0",
        "figure_id": figure_id,
        "kind": "apparatus",
        "title": "处理模拟含硫酸铜废液的低压直流电解装置",
        "coordinate_system": "svg_viewbox_990x456",
        "physical_size_mm": [165, 76],
        "source_pixel_reuse": False,
        "semantic_components": [
            "low_voltage_dc_supply",
            "graphite_anode",
            "stainless_steel_cathode",
            "open_electrolytic_cell",
            "porous_diaphragm",
            "gas_hood",
            "gas_delivery_tube",
            "flexible_collection_bag",
        ],
        "topology": {
            "nodes": ["power", "anode", "open_cell", "cathode", "gas_hood", "gas_tube", "flex_bag", "atmosphere"],
            "edges": [
                ["power", "anode", "electrical_positive"],
                ["power", "cathode", "electrical_negative"],
                ["anode", "open_cell", "immersed"],
                ["cathode", "open_cell", "immersed"],
                ["anode", "gas_hood", "oxygen_generation"],
                ["gas_hood", "gas_tube", "gas_path"],
                ["gas_tube", "flex_bag", "gas_path"],
                ["open_cell", "atmosphere", "open_pressure_boundary"],
            ],
        },
        "network_contract": {
            "networks": {
                "electrical_positive": {
                    "svg_members": ["wire-positive", "positive-anode-junction", "anode"],
                    "logical_nodes": ["power_positive", "anode"],
                },
                "electrical_negative": {
                    "svg_members": [
                        "wire-negative-segments",
                        "wire-negative-bridge",
                        "cathode",
                    ],
                    "logical_nodes": ["power_negative", "cathode"],
                },
                "gas_path": {
                    "svg_members": ["gas-hood", "gas-hood-right", "gas-tube", "flex-bag"],
                    "logical_nodes": ["gas_hood", "gas_tube", "flex_bag"],
                },
            },
            "verified_geometric_junctions": [
                {
                    "junction_id": "gas_hood_to_tube",
                    "member_a": "gas-hood",
                    "member_b": "gas-tube",
                    "coordinate": [400, 235],
                    "max_centerline_gap_viewbox": 4.5,
                    "pixel_probes_viewbox": [[390, 230], [410, 235]],
                },
                {
                    "junction_id": "gas_tube_to_flexible_bag",
                    "member_a": "gas-tube",
                    "member_b": "flex-bag",
                    "coordinate": [730, 95],
                    "max_centerline_gap_viewbox": 4.5,
                    "pixel_probes_viewbox": [[718, 95], [736, 84]],
                },
            ],
            "declared_crossovers": [
                {
                    "network_a": "electrical_negative",
                    "network_b": "gas_path",
                    "coordinate": [417, 165],
                    "rendering": "white_underlay_bridge_no_junction",
                    "bridge_svg_id": "wire-negative-bridge",
                    "underlay_svg_id": "wire-negative-bridge-underlay",
                    "junction": False,
                }
            ],
            "unapproved_solid_crossings": [],
            "positive_negative_shared_nodes": [],
            "electrical_short_circuit": False,
            "gas_path_continuous": True,
            "positive_path_continuous": True,
            "negative_path_continuous": True,
            "gas_hood_open_bottom": True,
            "gas_hood_rim_submerged": True,
            "gas_hood_covers_immersed_anode": True,
            "positive_wire_visibly_terminates_at_anode": True,
        },
        "safety_contract": {
            "heated": False,
            "sealed_rigid_system": False,
            "low_voltage_dc": True,
            "flammable_gas_present": False,
            "oxygen_collection_path": ["anode", "gas_hood", "gas_tube", "flex_bag"],
            "continuous_vent_to_atmosphere_claimed": False,
            "open_cell_pressure_path": ["open_cell", "atmosphere"],
            "flexible_pressure_boundary_required": True,
            "stop_before_bag_full_required": True,
            "open_cell_required": True,
            "gas_hood_geometry_required": "open_bottom_rim_below_solution_surface_covering_immersed_anode",
        },
        "evidence_use": {
            "visual_grammar_refs": ["VIS-2026-XUHUI-YIMO-APPARATUS", "VIS-TB-M1-APPARATUS-CHLORINE"],
            "allowed": "topology grammar, monochrome line language and label clearance only",
            "prohibited": "copying, tracing or collaging source pixels",
        },
        "review": {"mode": "machine_only", "human_reviewed": False},
        "publication_allowed": False,
    }


def _svg_path_endpoints(svg_root: ET.Element, member_id: str) -> tuple[tuple[float, float], tuple[float, float]]:
    node = next(
        (item for item in svg_root.iter() if item.attrib.get("id") == member_id),
        None,
    )
    if node is None or not node.tag.endswith("path"):
        raise ValueError(f"SVG member is not a path: {member_id}")
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", node.attrib.get("d", ""))]
    if len(numbers) < 4 or len(numbers) % 2:
        raise ValueError(f"SVG path coordinates cannot be parsed: {member_id}")
    return (numbers[0], numbers[1]), (numbers[-2], numbers[-1])


def _nearest_dark_pixel(
    image: Image.Image, point: tuple[int, int], radius: int = 10
) -> tuple[int, int] | None:
    width, height = image.size
    x0, y0 = point
    candidates: list[tuple[int, int, int]] = []
    for y in range(max(0, y0 - radius), min(height, y0 + radius + 1)):
        for x in range(max(0, x0 - radius), min(width, x0 + radius + 1)):
            if image.getpixel((x, y)) < 190:
                candidates.append(((x - x0) ** 2 + (y - y0) ** 2, x, y))
    if not candidates:
        return None
    _, x, y = min(candidates)
    return x, y


def _pixel_path_connected(
    image: Image.Image,
    probe_a: list[float],
    probe_b: list[float],
    junction: list[float],
    *,
    viewbox: tuple[float, float] = (990.0, 456.0),
) -> bool:
    sx = image.width / viewbox[0]
    sy = image.height / viewbox[1]

    def scale(point: list[float]) -> tuple[int, int]:
        return round(point[0] * sx), round(point[1] * sy)

    start = _nearest_dark_pixel(image, scale(probe_a))
    target = _nearest_dark_pixel(image, scale(probe_b))
    if start is None or target is None:
        return False
    center = scale(junction)
    margin_x = max(24, round(38 * sx))
    margin_y = max(24, round(38 * sy))
    bounds = (
        max(0, center[0] - margin_x),
        max(0, center[1] - margin_y),
        min(image.width - 1, center[0] + margin_x),
        min(image.height - 1, center[1] + margin_y),
    )
    queue = deque([start])
    visited = {start}
    while queue:
        x, y = queue.popleft()
        if abs(x - target[0]) <= 2 and abs(y - target[1]) <= 2:
            return True
        for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            candidate = (x + dx, y + dy)
            if candidate in visited:
                continue
            if not (bounds[0] <= candidate[0] <= bounds[2] and bounds[1] <= candidate[1] <= bounds[3]):
                continue
            if image.getpixel(candidate) >= 210:
                continue
            visited.add(candidate)
            queue.append(candidate)
    return False


def validate_figure(spec: dict, svg_path: Path, png_path: Path, component_registry: dict | None = None) -> dict:
    errors: list[str] = []
    svg_text = svg_path.read_text(encoding="utf-8")
    for prohibited in ("transform=", "<image", "mask=", "clip-path", "<style", "animation", "@keyframes"):
        if prohibited.lower() in svg_text.lower():
            errors.append(f"prohibited SVG construct: {prohibited}")
    try:
        svg_root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        errors.append(f"SVG parse error: {exc}")
        svg_root = None
    ids = [] if svg_root is None else [node.attrib["id"] for node in svg_root.iter() if "id" in node.attrib]
    if len(ids) != len(set(ids)):
        errors.append("SVG element IDs must be unique")
    safety = spec["safety_contract"]
    if safety.get("heated") or safety.get("sealed_rigid_system"):
        errors.append("heated or sealed electrolysis apparatus is prohibited")
    if not safety.get("low_voltage_dc"):
        errors.append("low voltage DC requirement missing")
    nodes = set(spec["topology"]["nodes"])
    graph: dict[str, set[str]] = {node: set() for node in nodes}
    for left, right, _ in spec["topology"]["edges"]:
        graph[left].add(right)
        graph[right].add(left)
    visited = set()
    queue = deque(["open_cell"])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(graph[node] - visited)
    if "atmosphere" not in visited:
        errors.append("open cell has no path to atmospheric pressure")
    collection_path = safety.get("oxygen_collection_path", [])
    if collection_path != ["anode", "gas_hood", "gas_tube", "flex_bag"]:
        errors.append("oxygen collection path must terminate in the flexible bag")
    if safety.get("continuous_vent_to_atmosphere_claimed") is not False:
        errors.append("sealed collection bag must not be claimed as continuous atmospheric vent")
    if safety.get("stop_before_bag_full_required") is not True:
        errors.append("stop-before-full control is required")
    networks = spec.get("network_contract", {})
    if networks.get("unapproved_solid_crossings"):
        errors.append("different networks have an unapproved solid-line crossing")
    if networks.get("positive_negative_shared_nodes") or networks.get("electrical_short_circuit") is not False:
        errors.append("positive and negative electrical networks are shorted")
    if not all(
        networks.get(name) is True
        for name in ("gas_path_continuous", "positive_path_continuous", "negative_path_continuous")
    ):
        errors.append("one or more apparatus networks are discontinuous")
    if not all(
        networks.get(name) is True
        for name in (
            "gas_hood_open_bottom",
            "gas_hood_rim_submerged",
            "gas_hood_covers_immersed_anode",
            "positive_wire_visibly_terminates_at_anode",
        )
    ):
        errors.append("gas hood collection geometry or visible positive-anode connection is invalid")
    required_svg_geometry_markers = (
        'id="gas-hood"',
        'data-open-bottom="true"',
        'data-rim-submerged="true"',
        'data-covers-anode="true"',
        'id="positive-anode-junction"',
    )
    for marker in required_svg_geometry_markers:
        if marker not in svg_text:
            errors.append(f"apparatus SVG geometry marker missing: {marker}")
    network_map = networks.get("networks", {})
    positive_nodes = set(network_map.get("electrical_positive", {}).get("logical_nodes", []))
    negative_nodes = set(network_map.get("electrical_negative", {}).get("logical_nodes", []))
    if positive_nodes.intersection(negative_nodes):
        errors.append("positive and negative networks share a logical node")
    member_network: dict[str, str] = {}
    for network_name, network in network_map.items():
        for member in network.get("svg_members", []):
            if member in member_network:
                errors.append(f"SVG member belongs to multiple networks: {member}")
            member_network[member] = network_name
            if member not in ids:
                errors.append(f"network SVG member missing: {member}")
    for crossing in networks.get("declared_crossovers", []):
        if (
            crossing.get("network_a") == crossing.get("network_b")
            or crossing.get("junction") is not False
            or crossing.get("rendering") != "white_underlay_bridge_no_junction"
            or crossing.get("bridge_svg_id") not in ids
            or crossing.get("underlay_svg_id") not in ids
        ):
            errors.append(f"invalid network crossover declaration: {crossing}")
        bridge_token = f'id="{crossing.get("bridge_svg_id")}"'
        underlay_token = f'id="{crossing.get("underlay_svg_id")}"'
        if bridge_token not in svg_text or 'data-crossing="bridge-over-gas-path"' not in svg_text:
            errors.append("crossover bridge lacks explicit no-junction rendering marker")
        if underlay_token not in svg_text or 'stroke="white"' not in svg_text:
            errors.append("crossover bridge lacks a white separation underlay")
    geometric_junction_checks = []
    image = Image.open(png_path).convert("L")
    if svg_root is not None:
        for junction in networks.get("verified_geometric_junctions", []):
            try:
                endpoints_a = _svg_path_endpoints(svg_root, junction["member_a"])
                endpoints_b = _svg_path_endpoints(svg_root, junction["member_b"])
                distance = min(
                    math.dist(left, right)
                    for left in endpoints_a
                    for right in endpoints_b
                )
                declared = [float(value) for value in junction["coordinate"]]
                a_to_declared = min(math.dist(point, declared) for point in endpoints_a)
                b_to_declared = min(math.dist(point, declared) for point in endpoints_b)
                allowed = float(junction["max_centerline_gap_viewbox"])
                geometry_passed = distance <= allowed and a_to_declared <= allowed and b_to_declared <= allowed
                probes = junction["pixel_probes_viewbox"]
                pixel_passed = _pixel_path_connected(image, probes[0], probes[1], declared)
            except (KeyError, TypeError, ValueError) as exc:
                distance = None
                geometry_passed = False
                pixel_passed = False
                junction_error = str(exc)
            else:
                junction_error = None
            if not geometry_passed:
                errors.append(f"actual SVG gas-path junction is disconnected: {junction.get('junction_id')}")
            if not pixel_passed:
                errors.append(f"rendered PNG gas-path junction is disconnected: {junction.get('junction_id')}")
            geometric_junction_checks.append(
                {
                    "junction_id": junction.get("junction_id"),
                    "member_a": junction.get("member_a"),
                    "member_b": junction.get("member_b"),
                    "centerline_gap_viewbox": distance,
                    "allowed_gap_viewbox": junction.get("max_centerline_gap_viewbox"),
                    "actual_svg_geometry_status": "pass" if geometry_passed else "fail",
                    "rendered_pixel_connectivity_status": "pass" if pixel_passed else "fail",
                    "error": junction_error,
                }
            )
    if len(geometric_junction_checks) != 2:
        errors.append("exactly two actual gas-path geometric junction checks are required")
    if component_registry is not None:
        names = {item.get("name") for item in component_registry.get("components", [])}
        missing_components = sorted(set(spec.get("semantic_components", [])) - names)
        if missing_components:
            errors.append(f"semantic components missing from registry: {missing_components}")
    width, height = image.size
    if width < 1900 or height < 850:
        errors.append(f"PNG resolution too low: {width}x{height}")
    pixels = list(image.getdata())
    dark = sum(1 for value in pixels if value < 96)
    if dark < 10000:
        errors.append("insufficient dark structure for monochrome print")
    onebit = image.point(lambda x: 0 if x < 180 else 255, mode="1")
    bbox = onebit.convert("L").point(lambda x: 255 - x).getbbox()
    if not bbox or bbox[0] <= 1 or bbox[1] <= 1 or bbox[2] >= width - 1 or bbox[3] >= height - 1:
        errors.append("1-bit drawing touches the raster boundary or is empty")
    small = image.resize((max(1, width // 4), max(1, height // 4)))
    small_pixels = list(small.getdata())
    small_dark = sum(1 for value in small_pixels if value < 128)
    small_light = sum(1 for value in small_pixels if value > 224)
    if small_dark < 600 or small_light < len(small_pixels) // 2:
        errors.append("grayscale quarter-size print loses apparatus contrast")
    return {
        "check": "figure_topology_safety_and_print",
        "status": "pass" if not errors else "fail",
        "figure_id": spec["figure_id"],
        "svg_sha256": sha256_file(svg_path),
        "png_sha256": sha256_file(png_path),
        "figure_spec_sha256": canonical_hash(spec),
        "component_registry_sha256": canonical_hash(component_registry) if component_registry is not None else None,
        "png_size": [width, height],
        "dark_pixel_count": dark,
        "quarter_size_grayscale": {
            "size": list(small.size),
            "dark_pixel_count": small_dark,
            "light_pixel_count": small_light,
            "status": "pass" if small_dark >= 600 and small_light >= len(small_pixels) // 2 else "fail",
        },
        "network_contract": networks,
        "actual_geometry_and_pixel_connectivity": geometric_junction_checks,
        "onebit_bbox": list(bbox) if bbox else None,
        "human_reviewed": False,
        "publication_allowed": False,
        "errors": errors,
    }


def validate_cross_question_leakage(paper: dict, svg_path: Path) -> dict:
    def normalized(value: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())

    def contains_cluster(value: str, clusters: tuple[tuple[str, ...], ...]) -> bool:
        text = normalized(value)
        return any(all(normalized(token) in text for token in cluster) for cluster in clusters)

    copper_product_clusters = (
        ("阴极", "铜", "沉积"),
        ("阴极", "铜", "析出"),
        ("阴极", "铜", "生成"),
        ("阴极", "铜", "得到"),
        ("阴极", "产物", "铜"),
        ("阴极", "Cu", "析出"),
        ("阴极", "Cu", "生成"),
    )
    measurement_operation_clusters = (
        ("水", "附着液", "质量不再变化"),
        ("蒸馏水", "残液", "质量稳定"),
        ("洗涤", "干燥", "恒重"),
        ("冲洗", "烘", "恒重"),
    )

    def evaluate(candidate: dict, candidate_svg: str) -> list[dict]:
        parts = {part["part_id"]: part for part in iter_parts(candidate)}
        return [
            {
                "case_id": "P05_must_not_reveal_P02_cathode_product",
                "passed": (
                    not any(token in parts["P05"]["prompt"] for token in ("Cu", "铜"))
                    and "P02" not in parts["P05"].get("dependencies", [])
                    and "copper-deposit" not in candidate_svg
                    and "回收铜装置" not in candidate_svg
                ),
                "evidence": "P05 is an independent two-electron calibration; SVG has no product deposit marker or product-bearing title.",
            },
            {
                "case_id": "P08_and_P02_must_not_reveal_each_others_answer",
                "passed": (
                    not contains_cluster(parts["P08"]["prompt"], copper_product_clusters)
                    and not contains_cluster(parts["P02"]["prompt"], measurement_operation_clusters)
                    and "P02" not in parts["P08"].get("dependencies", [])
                    and "P08" not in parts["P02"].get("dependencies", [])
                    and parts["P08"].get("dependencies", []) == []
                ),
                "evidence": "P08 neutrally asks how to measure cathode mass change and has no dependency; neither P02 nor P08 states the other's answer.",
            },
            {
                "case_id": "P22_must_not_reveal_P19_dehydrogenation_direction_or_product",
                "passed": (
                    not any(token in parts["P22"]["prompt"] for token in ("脱氢", "生成的H₂", "H₂产物", "移出H₂"))
                    and "P19" not in parts["P22"].get("dependencies", [])
                ),
                "evidence": "P22 asks only about carrier enthalpy versus system energy and does not state the P19 route direction or product.",
            },
            {
                "case_id": "P32_formula_must_not_reverse_P31_choice",
                "passed": (
                    "比较方案" in parts["P31"]["prompt"]
                    and "P31" not in parts["P32"].get("dependencies", [])
                    and "控制" not in parts["P32"]["prompt"]
                    and "粒径" not in parts["P32"]["prompt"]
                ),
                "evidence": "P31 assesses controlled-comparison design; P32 independently calculates an energy-wavelength pair.",
            },
        ]

    svg_text = svg_path.read_text(encoding="utf-8")
    cases = evaluate(paper, svg_text)
    mutations = []
    for mutation_id, expected_case in (
        (
            "inject_P02_product_into_P05",
            "P05_must_not_reveal_P02_cathode_product",
        ),
        (
            "inject_P02_product_into_P08",
            "P08_and_P02_must_not_reveal_each_others_answer",
        ),
        (
            "inject_P08_operations_into_P02",
            "P08_and_P02_must_not_reveal_each_others_answer",
        ),
        (
            "inject_P02_product_synonym_into_P08",
            "P08_and_P02_must_not_reveal_each_others_answer",
        ),
        (
            "inject_P08_operations_synonym_into_P02",
            "P08_and_P02_must_not_reveal_each_others_answer",
        ),
        (
            "inject_P19_direction_into_P22",
            "P22_must_not_reveal_P19_dehydrogenation_direction_or_product",
        ),
        (
            "make_P32_reverse_P31_by_dependency_and_size_wording",
            "P32_formula_must_not_reverse_P31_choice",
        ),
    ):
        candidate = json.loads(json.dumps(paper, ensure_ascii=False))
        target_by_id = {part["part_id"]: part for part in iter_parts(candidate)}
        if mutation_id == "inject_P02_product_into_P05":
            target_by_id["P05"]["prompt"] += " 该沉积物为铜。"
        elif mutation_id == "inject_P02_product_into_P08":
            target_by_id["P08"]["prompt"] += " 阴极质量变化来自铜沉积。"
            target_by_id["P08"]["dependencies"] = ["P02"]
        elif mutation_id == "inject_P08_operations_into_P02":
            target_by_id["P02"]["prompt"] += " 反应后洗涤、干燥并称量至恒重。"
            target_by_id["P02"]["dependencies"] = ["P08"]
        elif mutation_id == "inject_P02_product_synonym_into_P08":
            target_by_id["P08"]["prompt"] += " 阴极上生成Cu单质后再称量。"
        elif mutation_id == "inject_P08_operations_synonym_into_P02":
            target_by_id["P02"]["prompt"] += " 蒸馏水冲去残液，烘至质量稳定。"
        elif mutation_id == "inject_P19_direction_into_P22":
            target_by_id["P22"]["prompt"] += " MCH脱氢生成H₂。"
            target_by_id["P22"]["dependencies"] = ["P19"]
        else:
            target_by_id["P32"]["prompt"] += " 据此判断粒径变化趋势。"
            target_by_id["P32"]["dependencies"] = ["P31"]
        result_by_id = {row["case_id"]: row for row in evaluate(candidate, svg_text)}
        mutations.append(
            {
                "mutation_id": mutation_id,
                "expected_rejected": True,
                "observed_rejected": result_by_id[expected_case]["passed"] is False,
            }
        )
    return {
        "check": "targeted_cross_question_answer_leakage",
        "status": "pass"
        if all(case["passed"] for case in cases)
        and all(case["observed_rejected"] for case in mutations)
        else "fail",
        "cases": cases,
        "mutation_cases": mutations,
        "errors": [case["case_id"] for case in cases if not case["passed"]]
        + [case["mutation_id"] for case in mutations if not case["observed_rejected"]],
    }


def validate_review_receipts(receipts: list[dict], paper_sha256: str, task_sha256: str) -> dict:
    errors = []
    required_roles = {"generator", "independent_machine_review", "adversarial_check"}
    roles = [receipt.get("role") for receipt in receipts]
    if set(roles) != required_roles or len(roles) != 3:
        errors.append(f"roles must be exactly {sorted(required_roles)}, got {roles}")
    run_ids = [receipt.get("run_id") for receipt in receipts]
    context_ids = [receipt.get("context_id") for receipt in receipts]
    if len(set(run_ids)) != 3 or len(set(context_ids)) != 3:
        errors.append("run_id and context_id must each be distinct")
    for receipt in receipts:
        if receipt.get("model") != "gpt-5.6-sol" or receipt.get("reasoning_effort") != "xhigh":
            errors.append(f"wrong model contract: {receipt.get('run_id')}")
        if receipt.get("paper_sha256") != paper_sha256 or receipt.get("task_card_sha256") != task_sha256:
            errors.append(f"hash binding mismatch: {receipt.get('run_id')}")
        if receipt.get("disposition") != "pass":
            errors.append(f"non-pass disposition: {receipt.get('run_id')}")
        if receipt.get("human_review_claimed") is not False:
            errors.append(f"human review overclaim: {receipt.get('run_id')}")
    for receipt in receipts:
        if receipt.get("role") == "generator":
            continue
        visible = set(receipt.get("visible_context_kinds", []))
        forbidden = {"generator_transcript", "other_reviewer_output"}
        if visible & forbidden:
            errors.append(f"review context contamination: {receipt.get('run_id')}")
        if not {"task_card", "evidence_contract", "frozen_candidate"} <= visible:
            errors.append(f"review context incomplete: {receipt.get('run_id')}")
    return {
        "check": "sol_generation_and_isolated_dual_review",
        "status": "pass" if not errors else "fail",
        "receipt_hashes": [canonical_hash(receipt) for receipt in receipts],
        "run_ids": run_ids,
        "context_ids": context_ids,
        "errors": errors,
    }


def docx_media_hashes(path: Path) -> dict[str, str]:
    result = {}
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.startswith("word/media/"):
                result[name] = hashlib.sha256(archive.read(name)).hexdigest()
    return result


def source_crop_hashes(workspace: Path) -> set[str]:
    registry = workspace / "sh-chem-db/kb/figures/apparatus_source_crop_batches/batch_04_school_exam_round05_2026-08-02/crop_registry.jsonl"
    return {
        json.loads(line)["crop_sha256"]
        for line in registry.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
