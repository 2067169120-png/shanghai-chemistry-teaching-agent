from __future__ import annotations

"""Read-only, theme-first progress projection for the local question bank.

The projection deliberately reports processing state rather than question
content.  It joins the already validated theme browser with the explicit
Master/Wave hierarchy records, keeps native classification coverage separate
from visual-scan candidate coverage, and never infers source identity from a
title, filename, URL, or identifier.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from .candidate_review import CandidateReviewError, Wave1CandidateReviewReader
from .master_wave1_workbench import (
    PRODUCT_ID as MASTER_PRODUCT_ID,
)
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .master_wave1_workbench import (
    _scalar as _master_scalar,
)
from .theme_workbench import ThemeWorkbenchError, ThemeWorkbenchReader

SCHEMA_VERSION = "1.0.0-question-processing-progress"
ALLOWED_SCOPES = frozenset({"wave1", "master"})
ALLOWED_GAPS = frozenset(
    {
        "parent_chain",
        "visual_scan",
        "textbook_mapping",
        "classification",
        "difficulty",
        "identity",
        "source",
        "answer",
    }
)
FIELD_ORDER = ("item_type", "K", "A", "C", "R", "RP", "D")
MAX_LIMIT = 100
FULL_CAPTURE_LIMIT = MAX_LIMIT
TEN_FACTOR_COUNT = 10

AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "measured_difficulty_verified": False,
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_UNAVAILABLE_TEXT = frozenset(
    {
        "",
        "unknown",
        "blocked_pending_review",
        "unknown_pending_review",
        "待核验",
    }
)
_TEXTBOOK_FIELDS = (
    "textbook_directory_mapping",
    "textbook_mapping",
    "shanghai_textbook_mapping",
    "textbook_catalog_mapping",
)
_FORBIDDEN_KEYS = frozenset(
    {
        "answer_text",
        "reference_answer_text",
        "question_text",
        "stem_text",
        "solution_path_zh",
        "source_url",
        "original_url",
        "local_path",
        "source_path",
        "file_path",
        "crop_path",
        "student_id",
        "student_profile_id",
        "upload_id",
        "attempt_id",
    }
)
_FORBIDDEN_STRING = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/))"
)


class QuestionProcessingProgressError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _known_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, dict):
        if "value" in value:
            return _known_scalar(value.get("value"))
        for key in ("candidate_values", "values", "candidate_labels"):
            candidates = value.get(key)
            if isinstance(candidates, list) and len(candidates) == 1:
                return _known_scalar(candidates[0])
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped.casefold() in _UNAVAILABLE_TEXT else stripped
    if isinstance(value, (int, float, bool)) and not isinstance(value, complex):
        return value
    return None


def _has_values(value: Any) -> bool:
    if isinstance(value, dict):
        if any(
            _known_scalar(value.get(key)) is not None
            for key in ("id", "knowledge_id", "chapter_id", "section_id")
        ):
            return True
        if "value" in value:
            return _has_values(value.get("value"))
        for key in (
            "candidate_values",
            "values",
            "candidate_labels",
            "declared_prelabel",
            "candidate_label",
        ):
            if key in value and _has_values(value.get(key)):
                return True
        return False
    if isinstance(value, list):
        return any(_has_values(item) for item in value)
    return _known_scalar(value) is not None


def _status_for_coverage(covered: int, total: int) -> str:
    if total == 0:
        return "not_applicable"
    if covered == total:
        return "complete"
    if covered == 0:
        return "missing"
    return "partial"


def _coverage(covered: int, total: int) -> dict[str, Any]:
    return {
        "covered_atomic": covered,
        "missing_atomic": total - covered,
        "total_atomic": total,
        "status": _status_for_coverage(covered, total),
    }


def _uncertain_value(value: Any) -> bool:
    return isinstance(value, str) and any(
        marker in value.casefold()
        for marker in ("unknown", "待核验", "未署", "标题归类", "公众号")
    )


def _identity_axis(value: Any, status: str | None) -> dict[str, Any]:
    known = _known_scalar(value)
    if known is None:
        return {"value": None, "status": "unknown_not_in_product"}
    if _uncertain_value(known):
        status = "candidate_uncertain"
    return {"value": known, "status": status or "available_in_product"}


def _source_projection(value: Any, *, fallback_layer: Any = None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = _known_scalar(source.get("official_status"))
    account = _known_scalar(source.get("source_account"))
    evidence_level = _known_scalar(source.get("evidence_level"))
    source_authority = _known_scalar(source.get("source_authority"))
    if source_authority is None:
        source_authority = _known_scalar(fallback_layer)
    return {
        "status": status or "unknown_not_in_product",
        "source_account": account,
        "evidence_level": evidence_level,
        "source_authority": source_authority,
        "human_source_reviewed": False,
        "official": status == "official",
    }


def _master_paper_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    identity = row.get("source_identity")
    source_identity = identity if isinstance(identity, dict) else {}

    year = _known_scalar(row.get("year"))
    year_status = "available_in_master_record" if year is not None else None
    if year is None:
        year = _known_scalar(source_identity.get("year"))
        year_status = "available_in_source_identity" if year is not None else None
    if year is None:
        year = _known_scalar(source_identity.get("academic_year"))
        year_status = "academic_year_only" if year is not None else None

    region = _known_scalar(row.get("region_or_school"))
    region_status = "available_in_master_record" if region is not None else None
    if region is None:
        for key, status in (
            ("district", "available_in_source_identity"),
            ("school", "available_in_source_identity"),
            ("school_article_title_attribution", "article_title_attribution_only"),
        ):
            region = _known_scalar(source_identity.get(key))
            if region is not None:
                region_status = status
                break

    paper_type = _known_scalar(row.get("paper_type"))
    paper_type_status = "available_in_master_record" if paper_type is not None else None
    if paper_type is None:
        for key in ("paper_type", "exam_type"):
            paper_type = _known_scalar(source_identity.get(key))
            if paper_type is not None:
                paper_type_status = "available_in_source_identity"
                break

    return {
        "year": _identity_axis(year, year_status),
        "region_or_school": _identity_axis(region, region_status),
        "paper_type": _identity_axis(paper_type, paper_type_status),
        "source": _source_projection(
            source_identity, fallback_layer=row.get("source_layer")
        ),
    }


def _wave_paper_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    axes = row.get("identity_axes")
    identity_axes = axes if isinstance(axes, dict) else {}

    def axis(name: str) -> dict[str, Any]:
        raw = identity_axes.get(name)
        item = raw if isinstance(raw, dict) else {}
        return _identity_axis(item.get("value"), _known_scalar(item.get("status")))

    district = axis("district")
    school = axis("school")
    region = district if district["value"] is not None else school
    return {
        "year": axis("year"),
        "region_or_school": region,
        "paper_type": axis("paper_type"),
        "source": _source_projection(
            row.get("source_metadata"), fallback_layer=row.get("source_layer")
        ),
    }


def _textbook_field(rows: Iterable[Mapping[str, Any]]) -> str | None:
    rows = tuple(rows)
    for field in _TEXTBOOK_FIELDS:
        if any(field in row for row in rows):
            return field
    return None


def _textbook_projection(
    atomic_ids: Iterable[str], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    ids = tuple(atomic_ids)
    field = metadata.get("textbook_field")
    if not isinstance(field, str):
        return {
            "field_availability": "unavailable",
            "status": "unavailable_not_in_product",
            "mapped_atomic": None,
            "unmapped_atomic": None,
            "total_atomic": len(ids),
            "note_zh": "当前产品没有题级教材目录映射字段；不能把知识标签反推成教材目录映射。",
        }
    mapped = sum(
        bool(metadata["textbook_mapped_by_atomic"].get(node_id)) for node_id in ids
    )
    return {
        "field_availability": "available",
        "status": _status_for_coverage(mapped, len(ids)),
        "mapped_atomic": mapped,
        "unmapped_atomic": len(ids) - mapped,
        "total_atomic": len(ids),
        "note_zh": None,
    }


def _scan_axis_covered(entry: Mapping[str, Any], field: str) -> bool:
    labels = entry.get("label_summary")
    if not isinstance(labels, dict) or labels.get("status") != "complete":
        return False
    if field == "item_type":
        return _known_scalar(entry.get("item_type")) is not None
    if field == "K":
        return _known_scalar(labels.get("primary_K")) is not None or _has_values(
            labels.get("supporting_K")
        )
    if field == "D":
        return _known_scalar(labels.get("cognitive_prelabel")) is not None
    return _has_values(labels.get(field))


def _field_coverage(
    entries: Iterable[Mapping[str, Any]], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    entries = tuple(entries)
    atomic_ids = [str(entry["atomic_part_id"]) for entry in entries]
    flags = metadata["field_flags_by_atomic"]
    alias_unitized = sum(bool(entry.get("alias_units")) for entry in entries)
    blocked_scan = sum(
        entry.get("visual_coverage_kind") != "unscanned"
        and not bool(entry.get("alias_units"))
        and isinstance(entry.get("label_summary"), dict)
        and entry["label_summary"].get("status") != "complete"
        for entry in entries
    )
    result: dict[str, Any] = {}
    for field in FIELD_ORDER:
        native = sum(bool(flags.get(node_id, {}).get(field)) for node_id in atomic_ids)
        scan_candidate = sum(_scan_axis_covered(entry, field) for entry in entries)
        result[field] = {
            "native": _coverage(native, len(entries)),
            "visual_scan_candidate": {
                **_coverage(scan_candidate, len(entries)),
                "unitized_alias_atomic": alias_unitized,
                "blocked_projection_atomic": blocked_scan,
                "candidate_only": True,
            },
        }
    return result


def _visual_scan(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    entries = tuple(entries)
    kinds = Counter(
        str(entry.get("visual_coverage_kind", "unscanned")) for entry in entries
    )
    scanned = sum(count for kind, count in kinds.items() if kind != "unscanned")
    return {
        "status": (
            "complete_candidate_scan"
            if scanned == len(entries) and entries
            else ("not_started" if scanned == 0 else "partial_candidate_scan")
        ),
        "scanned_atomic": scanned,
        "unscanned_atomic": len(entries) - scanned,
        "total_atomic": len(entries),
        "coverage_kinds": dict(sorted(kinds.items())),
        "human_visual_reviewed": False,
    }


def _difficulty_status(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    entries = tuple(entries)
    visual = _visual_scan(entries)
    covered = visual["scanned_atomic"]
    total = len(entries)
    return {
        "status": (
            "complete_candidate_non_measured"
            if covered == total and total
            else ("unavailable" if covered == 0 else "partial_candidate_non_measured")
        ),
        "candidate_evidence_atomic": covered,
        "pending_atomic": total - covered,
        "total_atomic": total,
        "factor_count_per_scanned_unit": TEN_FACTOR_COUNT,
        "measured_difficulty_atomic": 0,
        "measured_difficulty_verified": False,
    }


def _answer_units(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    aliases = entry.get("alias_units")
    if isinstance(aliases, list) and aliases:
        return [
            unit.get("answer", {})
            for unit in aliases
            if isinstance(unit, dict) and isinstance(unit.get("answer"), dict)
        ]
    answer = entry.get("answer")
    return [answer] if isinstance(answer, dict) else []


def _answers(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    units = [answer for entry in entries for answer in _answer_units(entry)]
    availability = Counter(
        str(answer.get("availability", "absent")) for answer in units
    )
    authority = Counter(
        str(answer.get("source_authority", "unknown")) for answer in units
    )
    return {
        "answer_unit_total": len(units),
        "aligned": availability.get("present_part_aligned", 0),
        "unaligned": availability.get("present_unaligned", 0),
        "absent": availability.get("absent", 0),
        "other_explicit_status": sum(
            count
            for key, count in availability.items()
            if key not in {"present_part_aligned", "present_unaligned", "absent"}
        ),
        "source_authority_counts": dict(sorted(authority.items())),
        "official_answer_count": 0,
        "independently_verified_count": 0,
        "authority_boundary": "source_reference_only_unverified_not_official",
    }


def _identity_has_gap(identity: Mapping[str, Any]) -> bool:
    return any(
        not isinstance(identity.get(field), dict)
        or identity[field].get("value") is None
        or str(identity[field].get("status", "")).startswith("unknown")
        for field in ("year", "region_or_school", "paper_type")
    )


def _gap(
    code: str,
    category: str,
    affected_atomic: int | None,
    status: str,
    note_zh: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "affected_atomic": affected_atomic,
        "status": status,
        "note_zh": note_zh,
    }


def _next_gaps(
    *,
    atomic_total: int,
    parent_missing: int,
    visual: Mapping[str, Any],
    fields: Mapping[str, Any],
    textbook: Mapping[str, Any],
    difficulty: Mapping[str, Any],
    identity: Mapping[str, Any] | None,
    answers: Mapping[str, Any],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if parent_missing:
        gaps.append(
            _gap(
                "parent_chain_pending",
                "parent_chain",
                parent_missing,
                "blocked_pending_review",
                "父链不完整，不能按题号、文件名或ID猜测主题归属。",
            )
        )
    if visual["unscanned_atomic"]:
        gaps.append(
            _gap(
                "visual_scan_pending",
                "visual_scan",
                int(visual["unscanned_atomic"]),
                "pending",
                "仍需逐图扫描题面、共享材料和依赖关系。",
            )
        )
    missing_fields = {
        field: int(value["native"]["missing_atomic"])
        for field, value in fields.items()
        if value["native"]["missing_atomic"]
    }
    if missing_fields:
        gaps.append(
            _gap(
                "native_classification_pending",
                "classification",
                max(missing_fields.values()),
                "pending",
                "原生字段仍有缺口："
                + "、".join(
                    f"{field} {count}" for field, count in missing_fields.items()
                ),
            )
        )
    if atomic_total and textbook["field_availability"] == "unavailable":
        gaps.append(
            _gap(
                "textbook_mapping_unavailable",
                "textbook_mapping",
                atomic_total,
                "unavailable_not_in_product",
                "产品未提供题级教材目录映射字段，当前不能报告已映射/未映射数量。",
            )
        )
    elif textbook["unmapped_atomic"]:
        gaps.append(
            _gap(
                "textbook_mapping_pending",
                "textbook_mapping",
                int(textbook["unmapped_atomic"]),
                "pending",
                "题级教材目录映射尚未完成。",
            )
        )
    if difficulty["pending_atomic"]:
        gaps.append(
            _gap(
                "ten_factor_difficulty_pending",
                "difficulty",
                int(difficulty["pending_atomic"]),
                "pending",
                "十因素难度证据尚未覆盖全部最小作答单元；实测难度仍为0。",
            )
        )
    if identity is not None and _identity_has_gap(identity):
        gaps.append(
            _gap(
                "paper_identity_pending",
                "identity",
                atomic_total,
                "unknown_or_candidate_uncertain",
                "年份、地区/学校或卷种至少一项没有可直接投影的显式值。",
            )
        )
    if (
        identity is not None
        and identity.get("source", {}).get("status") == "unknown_not_in_product"
    ):
        gaps.append(
            _gap(
                "source_status_pending",
                "source",
                atomic_total,
                "unknown_not_in_product",
                "产品未提供可直接投影的来源状态。",
            )
        )
    answer_gap = int(answers["unaligned"]) + int(answers["absent"])
    if answer_gap:
        gaps.append(
            _gap(
                "answer_alignment_pending",
                "answer",
                answer_gap,
                "pending_or_absent",
                "参考答案仍有未对齐或缺失；已有答案也只保留来源权威边界且未独立核验。",
            )
        )
    return gaps


def _reject_unsafe_projection(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered in _FORBIDDEN_KEYS or lowered.endswith(
                ("_path", "_url", "_sha256", "_hash", "_bindings")
            ):
                raise QuestionProcessingProgressError(
                    "question_processing_progress_projection_leak",
                    "progress projection contains a forbidden field",
                    409,
                )
            _reject_unsafe_projection(child)
    elif isinstance(value, list):
        for child in value:
            _reject_unsafe_projection(child)
    elif isinstance(value, str) and _FORBIDDEN_STRING.search(value):
        raise QuestionProcessingProgressError(
            "question_processing_progress_projection_leak",
            "progress projection contains a local locator or URL",
            409,
        )


def _validate_request(
    *,
    scope: str,
    paper_id: str | None,
    theme_id: str | None,
    gap: str | None,
    limit: int,
    offset: int,
) -> None:
    if scope not in ALLOWED_SCOPES:
        raise QuestionProcessingProgressError(
            "question_processing_progress_scope_invalid",
            "scope must be exactly wave1 or master",
            400,
        )
    for label, value in (("paper_id", paper_id), ("theme_id", theme_id)):
        if value is not None and _IDENTIFIER.fullmatch(value) is None:
            raise QuestionProcessingProgressError(
                "question_processing_progress_filter_invalid",
                f"{label} is invalid",
                400,
            )
    if gap is not None and gap not in ALLOWED_GAPS:
        raise QuestionProcessingProgressError(
            "question_processing_progress_filter_invalid",
            "gap filter is unsupported",
            400,
        )
    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= MAX_LIMIT
        or offset < 0
    ):
        raise QuestionProcessingProgressError(
            "question_processing_progress_pagination_invalid",
            "progress pagination is invalid",
            400,
        )


def _candidate_parent_chain_overlay_summary(
    theme_data: Mapping[str, Any], scope: str
) -> dict[str, Any] | None:
    overlay = theme_data.get("candidate_parent_chain_overlay")
    if scope == "wave1":
        if overlay is not None:
            raise QuestionProcessingProgressError(
                "question_processing_progress_overlay_scope_invalid",
                "the Master parent-chain candidate overlay appeared in Wave1",
                409,
            )
        return None
    if not isinstance(overlay, Mapping):
        raise QuestionProcessingProgressError(
            "question_processing_progress_overlay_unavailable",
            "the Master parent-chain candidate overlay is unavailable",
            409,
        )
    counts = overlay.get("counts")
    completion = overlay.get("completion")
    central = overlay.get("central_master")
    authority = overlay.get("authority")
    if not all(
        isinstance(value, Mapping)
        for value in (counts, completion, central, authority)
    ):
        raise QuestionProcessingProgressError(
            "question_processing_progress_overlay_invalid",
            "the Master parent-chain candidate overlay summary is invalid",
            409,
        )
    expected = {
        "papers": 1,
        "theme_groups": 5,
        "printed_questions": 41,
        "source_master_atomics": 43,
        "effective_atomics": 56,
        "split_source_master_atomics": 10,
        "dependency_edges": 8,
        "difficulty_factors": 560,
    }
    if (
        any(counts.get(key) != value for key, value in expected.items())
        or completion.get("denominator_unit") != "source_master_atomic"
        or completion.get("candidate_completed") != 43
        or completion.get("candidate_total") != 43
        or completion.get("candidate_remaining") != 0
        or completion.get("human_reviewed") is not False
        or central.get("atomic_total") != 470
        or central.get("complete_parent_chain_atomics") != 427
        or central.get("original_pending_atomics") != 43
        or central.get("applied") is not False
        or central.get("human_confirmed") is not False
        or central.get("denominator_unchanged") is not True
        or authority.get("human_reviewed") is not False
        or authority.get("candidate_only") is not True
        or authority.get("read_only") is not True
    ):
        raise QuestionProcessingProgressError(
            "question_processing_progress_overlay_invalid",
            "the Master parent-chain candidate overlay counts or authority drifted",
            409,
        )
    return {
        "status": completion.get("candidate_status"),
        "candidate_label_zh": "候选整理43/43完成",
        "central_label_zh": "中央原记录43条待确认",
        "primary_unit": "complete_theme_big_question",
        "candidate_completed_source_master_atomics": 43,
        "candidate_total_source_master_atomics": 43,
        "candidate_remaining_source_master_atomics": 0,
        "complete_theme_batches": 5,
        "printed_questions": 41,
        "effective_atomics": 56,
        "split_source_master_atomics": 10,
        "central_master_atomic_total": 470,
        "central_parent_chain_complete_atomics": 427,
        "central_parent_chain_pending_atomics": 43,
        "central_master_modified": False,
        "central_applied": False,
        "machine_candidate": True,
        "human_reviewed": False,
    }


def filter_progress_response(
    base: Mapping[str, Any],
    *,
    scope: str,
    paper_id: str | None,
    theme_id: str | None,
    gap: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Filter one already-safe full-scope response (also used by frozen mode)."""

    _validate_request(
        scope=scope,
        paper_id=paper_id,
        theme_id=theme_id,
        gap=gap,
        limit=limit,
        offset=offset,
    )
    if base.get("scope") != scope or not isinstance(base.get("items"), list):
        raise QuestionProcessingProgressError(
            "question_processing_progress_base_invalid",
            "progress base projection is incompatible",
            409,
        )
    filtered: list[dict[str, Any]] = []
    for item in base["items"]:
        if not isinstance(item, dict):
            raise QuestionProcessingProgressError(
                "question_processing_progress_base_invalid",
                "progress base item is invalid",
                409,
            )
        if paper_id is not None and item.get("paper", {}).get("id") != paper_id:
            continue
        if theme_id is not None and item.get("theme", {}).get("id") != theme_id:
            continue
        if gap is not None and not any(
            candidate.get("category") == gap
            for candidate in item.get("next_gaps", [])
            if isinstance(candidate, dict)
        ):
            continue
        filtered.append(item)
    result = deepcopy(dict(base))
    page = deepcopy(filtered[offset : offset + limit])
    result["items"] = page
    result["count"] = len(page)
    result["total"] = len(filtered)
    result["limit"] = limit
    result["offset"] = offset
    result["filters"] = {
        "scope": scope,
        "paper_id": paper_id,
        "theme_id": theme_id,
        "gap": gap,
    }
    _reject_unsafe_projection(result)
    return result


class QuestionProcessingProgressReader:
    """Project deterministic processing progress from real Master/Wave data."""

    def __init__(
        self,
        shchem_root: Path,
        *,
        theme_workbench: ThemeWorkbenchReader | None = None,
        master_workbench: MasterWave1WorkbenchReader | None = None,
        wave_review: Wave1CandidateReviewReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )
        self.wave_review = wave_review or Wave1CandidateReviewReader(self.shchem_root)
        self.theme_workbench = theme_workbench or ThemeWorkbenchReader(
            self.shchem_root, master_workbench=self.master_workbench
        )

    def _master_metadata(self) -> dict[str, Any]:
        snapshot = self.master_workbench._snapshot()
        rows = snapshot.master_layers["atomic_part"]
        flags: dict[str, dict[str, bool]] = {}
        parents: dict[str, bool] = {}
        textbook_field = _textbook_field(rows)
        textbook: dict[str, bool] = {}
        for row in rows:
            node_id = str(row["atomic_part_id"])
            classification = self.master_workbench._classification(row)
            item_type = _master_scalar(row.get("item_type"))
            flags[node_id] = {
                "item_type": _known_scalar(item_type) is not None,
                **{
                    field: bool(classification[field].get("candidate_values"))
                    for field in ("K", "A", "C", "R", "RP", "D")
                },
            }
            parents[node_id] = bool(
                self.master_workbench._parent_chain(row, snapshot)["complete"]
            )
            textbook[node_id] = bool(
                textbook_field is not None and _has_values(row.get(textbook_field))
            )
        papers = {
            str(row["paper_id"]): _master_paper_metadata(row)
            for row in snapshot.master_layers["paper"]
        }
        return {
            "product_id": MASTER_PRODUCT_ID,
            "display_name_zh": "主索引题库",
            "hierarchy_inventory": {
                "paper": len(snapshot.master_layers["paper"]),
                "theme_big_question": len(snapshot.master_layers["theme_big_question"]),
                "printed_question": len(snapshot.master_layers["printed_question"]),
                "atomic_part": len(rows),
            },
            "field_flags_by_atomic": flags,
            "parent_complete_by_atomic": parents,
            "paper_metadata": papers,
            "textbook_field": textbook_field,
            "textbook_mapped_by_atomic": textbook,
        }

    def _wave_metadata(self) -> dict[str, Any]:
        snapshot = self.wave_review._snapshot()
        rows = snapshot["records"]["atomic_part"]
        flags: dict[str, dict[str, bool]] = {}
        parents: dict[str, bool] = {}
        textbook_field = _textbook_field(rows)
        textbook: dict[str, bool] = {}
        indexes = snapshot["indexes"]
        for row in rows:
            node_id = str(row["atomic_part_id"])
            projected = self.wave_review._project("atomic_part", row, snapshot)
            classification = projected["classification"]
            flags[node_id] = {
                "item_type": _known_scalar(projected["item_type"]["value"]) is not None,
                **{
                    field: _has_values(classification[field].get("values"))
                    for field in ("K", "A", "C", "R", "RP")
                },
                "D": _known_scalar(projected["difficulty"].get("cognitive_prelabel"))
                is not None,
            }
            printed_id = row.get("printed_question_id")
            theme_id = row.get("theme_big_question_id")
            paper_id = row.get("paper_id")
            parents[node_id] = bool(
                isinstance(printed_id, str)
                and isinstance(theme_id, str)
                and isinstance(paper_id, str)
                and printed_id in indexes["printed_question"]
                and theme_id in indexes["theme_big_question"]
                and paper_id in indexes["paper"]
            )
            textbook[node_id] = bool(
                textbook_field is not None and _has_values(row.get(textbook_field))
            )
        papers = {
            str(row["paper_id"]): _wave_paper_metadata(row)
            for row in snapshot["records"]["paper"]
        }
        return {
            "product_id": str(snapshot["manifest"].get("batch_id")),
            "display_name_zh": "Wave1精标题库",
            "hierarchy_inventory": {
                "paper": len(snapshot["records"]["paper"]),
                "theme_big_question": len(snapshot["records"]["theme_big_question"]),
                "printed_question": len(snapshot["records"]["printed_question"]),
                "atomic_part": len(rows),
            },
            "field_flags_by_atomic": flags,
            "parent_complete_by_atomic": parents,
            "paper_metadata": papers,
            "textbook_field": textbook_field,
            "textbook_mapped_by_atomic": textbook,
        }

    def _metadata(self, scope: str) -> dict[str, Any]:
        return self._wave_metadata() if scope == "wave1" else self._master_metadata()

    @staticmethod
    def _flatten(
        theme_data: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assigned = [
            entry
            for paper in theme_data.get("papers", [])
            for group in paper.get("theme_groups", [])
            for entry in group.get("atomic_chain", [])
            if isinstance(entry, dict)
        ]
        unassigned = theme_data.get("unassigned_pending_review", {}).get(
            "atomic_chain", []
        )
        return assigned, [item for item in unassigned if isinstance(item, dict)]

    def _theme_item(
        self, group: Mapping[str, Any], metadata: Mapping[str, Any], scope: str
    ) -> dict[str, Any]:
        entries = tuple(
            item for item in group.get("atomic_chain", []) if isinstance(item, dict)
        )
        atomic_ids = [str(entry["atomic_part_id"]) for entry in entries]
        parent_complete = sum(
            bool(metadata["parent_complete_by_atomic"].get(node_id))
            for node_id in atomic_ids
        )
        visual = _visual_scan(entries)
        fields = _field_coverage(entries, metadata)
        textbook = _textbook_projection(atomic_ids, metadata)
        difficulty = _difficulty_status(entries)
        answers = _answers(entries)
        paper = group.get("paper") if isinstance(group.get("paper"), dict) else {}
        theme = group.get("theme") if isinstance(group.get("theme"), dict) else {}
        paper_id = str(paper.get("id"))
        identity = deepcopy(
            metadata["paper_metadata"].get(
                paper_id,
                {
                    "year": _identity_axis(None, None),
                    "region_or_school": _identity_axis(None, None),
                    "paper_type": _identity_axis(None, None),
                    "source": _source_projection(None),
                },
            )
        )
        item = {
            "scope": scope,
            "product_id": metadata["product_id"],
            "primary_unit": "complete_theme_big_question",
            "standalone_choice_section_created": False,
            "paper": {
                "id": paper_id,
                "title_zh": paper.get("title")
                if isinstance(paper.get("title"), str)
                else None,
                "identity": identity,
            },
            "theme": {
                "id": str(theme.get("id")),
                "title_zh": theme.get("title")
                if isinstance(theme.get("title"), str)
                else None,
                "sequence": theme.get("sequence")
                if type(theme.get("sequence")) is int
                else None,
                "parent_chain_status": theme.get("parent_chain_status")
                if isinstance(theme.get("parent_chain_status"), str)
                else "unknown",
            },
            "atomic_total": len(entries),
            "hierarchy_slicing": {
                "status": (
                    "complete"
                    if parent_complete == len(entries) and entries
                    else "partial"
                ),
                "paper_records": 1,
                "theme_big_question_records": 1,
                "printed_question_records": len(
                    {entry.get("printed_question_id") for entry in entries}
                ),
                "atomic_part_records": len(entries),
                "parent_chain": {
                    "complete_atomic": parent_complete,
                    "pending_atomic": len(entries) - parent_complete,
                    "status": _status_for_coverage(parent_complete, len(entries)),
                },
            },
            "visual_scan": visual,
            "textbook_directory_mapping": textbook,
            "field_coverage": fields,
            "ten_factor_difficulty": difficulty,
            "answers": answers,
        }
        item["next_gaps"] = _next_gaps(
            atomic_total=len(entries),
            parent_missing=len(entries) - parent_complete,
            visual=visual,
            fields=fields,
            textbook=textbook,
            difficulty=difficulty,
            identity=identity,
            answers=answers,
        )
        return item

    def _unassigned(
        self, entries: Iterable[Mapping[str, Any]], metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        entries = tuple(entries)
        ids = [str(entry["atomic_part_id"]) for entry in entries]
        parent_complete = sum(
            bool(metadata["parent_complete_by_atomic"].get(node_id)) for node_id in ids
        )
        visual = _visual_scan(entries)
        fields = _field_coverage(entries, metadata)
        textbook = _textbook_projection(ids, metadata)
        difficulty = _difficulty_status(entries)
        answers = _answers(entries)
        result = {
            "status": "none" if not entries else "missing_parent_pending_review",
            "atomic_total": len(entries),
            "not_assigned_to_theme": bool(entries),
            "reason_zh": (
                None
                if not entries
                else "缺失精确父链的atomic单列；没有按题号、文件名、标题或ID猜测主题。"
            ),
            "parent_chain_complete_atomic": parent_complete,
            "visual_scan": visual,
            "textbook_directory_mapping": textbook,
            "field_coverage": fields,
            "ten_factor_difficulty": difficulty,
            "answers": answers,
        }
        result["next_gaps"] = _next_gaps(
            atomic_total=len(entries),
            parent_missing=len(entries) - parent_complete,
            visual=visual,
            fields=fields,
            textbook=textbook,
            difficulty=difficulty,
            identity=None,
            answers=answers,
        )
        return result

    def _full_scope(
        self, scope: str, theme_data: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if theme_data is None:
            with ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="shchem-processing-progress"
            ) as executor:
                themes_future = executor.submit(self.theme_workbench.groups, scope)
                metadata_future = executor.submit(self._metadata, scope)
                theme_data = themes_future.result()
                metadata = metadata_future.result()
        else:
            if theme_data.get("scope") != scope:
                raise QuestionProcessingProgressError(
                    "question_processing_progress_scope_mismatch",
                    "preloaded theme projection has a different scope",
                    409,
                )
            metadata = self._metadata(scope)

        items = [
            self._theme_item(group, metadata, scope)
            for paper in theme_data.get("papers", [])
            for group in paper.get("theme_groups", [])
            if isinstance(group, dict)
        ]
        assigned, unassigned_entries = self._flatten(theme_data)
        all_entries = [*assigned, *unassigned_entries]
        flags_count = len(metadata["field_flags_by_atomic"])
        if (
            len(all_entries) != flags_count
            or len({str(entry["atomic_part_id"]) for entry in all_entries})
            != flags_count
        ):
            raise QuestionProcessingProgressError(
                "question_processing_progress_scope_mismatch",
                "theme and native atomic inventories do not match",
                409,
            )

        scope_visual = _visual_scan(all_entries)
        scope_fields = _field_coverage(all_entries, metadata)
        scope_textbook = _textbook_projection(
            [str(entry["atomic_part_id"]) for entry in all_entries], metadata
        )
        scope_difficulty = _difficulty_status(all_entries)
        scope_answers = _answers(all_entries)
        complete_parent = sum(metadata["parent_complete_by_atomic"].values())
        paper_ids = {
            item["paper"]["id"]
            for item in items
            if isinstance(item.get("paper", {}).get("id"), str)
        }
        identity_known = sum(
            not _identity_has_gap(metadata["paper_metadata"].get(paper_id, {}))
            for paper_id in paper_ids
        )
        summary = {
            "hierarchy_inventory": deepcopy(metadata["hierarchy_inventory"]),
            "theme_progress_total": len(items),
            "atomic_total": flags_count,
            "parent_chain": {
                "complete_atomic": complete_parent,
                "pending_atomic": flags_count - complete_parent,
                "status": _status_for_coverage(complete_parent, flags_count),
            },
            "visual_scan": scope_visual,
            "textbook_directory_mapping": scope_textbook,
            "field_coverage": scope_fields,
            "ten_factor_difficulty": scope_difficulty,
            "paper_identity": {
                "papers_with_theme_progress": len(paper_ids),
                "all_identity_values_present": identity_known,
                "unknown_or_candidate_uncertain": len(paper_ids) - identity_known,
                "papers_without_theme_progress": max(
                    0, metadata["hierarchy_inventory"]["paper"] - len(paper_ids)
                ),
            },
            "answers": scope_answers,
            "candidate_parent_chain_overlay": (
                _candidate_parent_chain_overlay_summary(theme_data, scope)
            ),
        }
        summary["next_gaps"] = _next_gaps(
            atomic_total=flags_count,
            parent_missing=flags_count - complete_parent,
            visual=scope_visual,
            fields=scope_fields,
            textbook=scope_textbook,
            difficulty=scope_difficulty,
            identity=None,
            answers=scope_answers,
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "scope": scope,
            "product": {
                "product_id": metadata["product_id"],
                "display_name_zh": metadata["display_name_zh"],
                "primary_unit": "complete_theme_big_question",
                "cross_scope_sum_allowed": False,
            },
            "scope_summary": summary,
            "unassigned_parent_chain": self._unassigned(unassigned_entries, metadata),
            "items": items,
            "count": len(items),
            "total": len(items),
            "limit": FULL_CAPTURE_LIMIT,
            "offset": 0,
            "filters": {
                "scope": scope,
                "paper_id": None,
                "theme_id": None,
                "gap": None,
            },
            "authority": dict(AUTHORITY),
            "integrity": {
                "counts_derived_from_current_product_records": True,
                "fixed_total_embedded": False,
                "complete_theme_is_primary_unit": True,
                "standalone_choice_section_created": False,
                "question_text_excluded": True,
                "answer_text_excluded": True,
                "paths_and_urls_excluded": True,
                "student_data_excluded": True,
                "fail_closed": True,
            },
        }
        _reject_unsafe_projection(result)
        return result

    def list_progress(
        self,
        *,
        scope: str,
        paper_id: str | None,
        theme_id: str | None,
        gap: str | None,
        limit: int,
        offset: int,
        theme_data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validate_request(
            scope=scope,
            paper_id=paper_id,
            theme_id=theme_id,
            gap=gap,
            limit=limit,
            offset=offset,
        )
        try:
            base = self._full_scope(scope, theme_data=theme_data)
            return filter_progress_response(
                base,
                scope=scope,
                paper_id=paper_id,
                theme_id=theme_id,
                gap=gap,
                limit=limit,
                offset=offset,
            )
        except QuestionProcessingProgressError:
            raise
        except (
            CandidateReviewError,
            MasterWave1WorkbenchError,
            ThemeWorkbenchError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            raise QuestionProcessingProgressError(
                "question_processing_progress_dependency_unavailable",
                "a required question-processing projection failed closed",
                409,
            ) from exc


__all__ = [
    "ALLOWED_GAPS",
    "ALLOWED_SCOPES",
    "AUTHORITY",
    "FIELD_ORDER",
    "FULL_CAPTURE_LIMIT",
    "MAX_LIMIT",
    "SCHEMA_VERSION",
    "QuestionProcessingProgressError",
    "QuestionProcessingProgressReader",
    "filter_progress_response",
]
