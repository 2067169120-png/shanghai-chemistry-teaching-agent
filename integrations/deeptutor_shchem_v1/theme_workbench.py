from __future__ import annotations

"""Strict read-only theme-group projection for the chemistry workbench.

The endpoint built on this reader changes only the browsing unit.  It keeps
the frozen paper/theme/printed/atomic identities and the existing candidate
authority boundaries intact.  In particular, it never promotes a scan label,
merges a one-to-many alias, exposes source-answer text, or returns a local
evidence locator.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .candidate_review import CandidateReviewError, Wave1CandidateReviewReader
from .master_direct_visual_scan import (
    MasterDirectVisualScanError,
    MasterDirectVisualScanReader,
)
from .master_parent_chain_repair_overlay import (
    MasterParentChainRepairOverlayError,
    MasterParentChainRepairOverlayReader,
)
from .master_visual_scan_alias import (
    MasterVisualScanAliasError,
    MasterVisualScanAliasReader,
)
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .master_wave1_workbench import (
    _label as _master_label,
)
from .master_wave1_workbench import (
    _order as _master_order,
)
from .master_wave1_workbench import (
    _scalar as _master_scalar,
)
from .paper_export_alias_projection import project_direct_unit_scan
from .question_visual_scan import (
    QuestionVisualScanError,
    QuestionVisualScanReader,
)
from .reader_cancellation import ReaderThreadPoolExecutor
from .reference_answer import (
    ABSENT,
    ALIGNED,
    NONE,
    UNALIGNED,
    reference_answer_catalog_metadata,
)

SCHEMA_VERSION = "1.0.0-theme-workbench-candidate"
WAVE1_SCOPE = "wave1"
MASTER_SCOPE = "master"
ALLOWED_SCOPES = frozenset({WAVE1_SCOPE, MASTER_SCOPE})

AUTHORITY = {
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "retrieval_allowed": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "rubric_verified": False,
    "measured_difficulty_verified": False,
    "pixel_reuse_allowed": False,
}

WAVE_EXPECTED = {
    "papers": 5,
    "theme_groups": 25,
    "atomic_parts": 252,
    "display_atomic_units": 252,
    "unassigned_atomic_parts": 0,
}
MASTER_EXPECTED = {
    "papers": 20,
    "theme_groups": 48,
    "atomic_parts": 470,
    "display_atomic_units": 480,
    "unassigned_atomic_parts": 43,
}

_FORBIDDEN_KEYS = frozenset(
    {
        "answer_crop",
        "answer_crops",
        "answer_evidence",
        "answer_text",
        "reference_answer_text",
        "solution_path_zh",
        "source_bindings",
        "output_bindings",
        "file_hashes",
        "crop_path",
        "source_path",
        "local_path",
        "url",
        "source_url",
    }
)
_FORBIDDEN_STRING = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_./\\])(?:sh-chem-db/|kb/|staging/|runtime/|integrations/))"
)


class ThemeWorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _evidence_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return _evidence_value(value.get("value"))
    return value


def _known_int(value: Any) -> int | None:
    value = _evidence_value(value)
    if type(value) is int and value >= 0:
        return value
    # Some frozen overlays encode an explicitly observed numeric order as a
    # decimal JSON string.  Parse that literal only; arbitrary labels such as
    # ``whole_printed_item_no_explicit_subnumber`` remain unknown.
    if isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        return int(value)
    return None


def _known_text(value: Any) -> str | None:
    value = _evidence_value(value)
    return value if isinstance(value, str) and value.strip() else None


def _metadata_text(value: Any) -> str | None:
    """Return an explicit metadata literal without treating sentinels as facts."""

    value = _known_text(value)
    if value is None or value.strip().casefold() in {
        "unknown",
        "not_applicable",
        "blocked_pending_review",
        "pending_review",
    }:
        return None
    return value.strip()


def _metadata_year(value: Any) -> str | None:
    """Normalize only a dedicated numeric year field, never a title or ID."""

    value = _evidence_value(value)
    if type(value) is int and 1900 <= value <= 2100:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"(?:19|20|21)[0-9]{2}", value):
        return value
    return None


def _identity_axis(row: dict[str, Any], name: str) -> dict[str, Any]:
    axes = row.get("identity_axes")
    axis = axes.get(name) if isinstance(axes, dict) else None
    return axis if isinstance(axis, dict) else {}


def _wave_source_metadata(row: dict[str, Any]) -> dict[str, str]:
    """Project Wave source facts from structured source/identity fields only."""

    source = row.get("source_metadata")
    source = source if isinstance(source, dict) else {}
    year_axis = _identity_axis(row, "year")
    district_axis = _identity_axis(row, "district")
    school_axis = _identity_axis(row, "school")
    type_axis = _identity_axis(row, "paper_type")

    district = _metadata_text(district_axis.get("value"))
    school = _metadata_text(school_axis.get("value"))
    if district is not None:
        region = district
        attribution = _metadata_text(district_axis.get("status"))
    elif school is not None:
        region = school
        attribution = _metadata_text(school_axis.get("status"))
    else:
        region = None
        attribution = None

    return {
        "source_tier": _metadata_text(source.get("evidence_level")) or "unknown",
        "year": _metadata_year(year_axis.get("value")) or "unknown",
        "region": region or "unknown",
        "paper_type": _metadata_text(type_axis.get("value")) or "unknown",
        "attribution_status": attribution or "unknown",
    }


def _master_source_metadata(row: dict[str, Any]) -> dict[str, str]:
    """Project Master source facts without inspecting titles, filenames, or IDs."""

    identity = row.get("source_identity")
    identity = identity if isinstance(identity, dict) else {}
    nested = identity.get("identity")
    nested = nested if isinstance(nested, dict) else {}

    year = next(
        (
            known
            for value in (
                row.get("year"),
                identity.get("calendar_year"),
                identity.get("year"),
                nested.get("year"),
            )
            if (known := _metadata_year(value)) is not None
        ),
        None,
    )
    region = next(
        (
            known
            for value in (
                row.get("region_or_school"),
                identity.get("district"),
                nested.get("district"),
                identity.get("school"),
                nested.get("school"),
                identity.get("school_attribution"),
                identity.get("school_article_title_attribution"),
            )
            if (known := _metadata_text(value)) is not None
        ),
        None,
    )
    paper_type = next(
        (
            known
            for value in (
                row.get("paper_type"),
                identity.get("paper_type"),
                nested.get("paper_type"),
            )
            if (known := _metadata_text(value)) is not None
        ),
        None,
    )
    attribution = next(
        (
            known
            for value in (
                identity.get("district_attribution_status"),
                identity.get("school_attribution_status"),
                identity.get("identity_basis"),
                nested.get("identity_basis"),
            )
            if (known := _metadata_text(value)) is not None
        ),
        None,
    )
    return {
        "source_tier": (
            _metadata_text(identity.get("evidence_level"))
            or _metadata_text(row.get("source_layer"))
            or "unknown"
        ),
        "year": year or "unknown",
        "region": region or "unknown",
        "paper_type": paper_type or "unknown",
        "attribution_status": attribution or "unknown",
    }


def _tag_values(value: Any) -> list[str]:
    value = _evidence_value(value)
    candidates: Any = value
    if isinstance(value, dict):
        candidates = next(
            (
                value.get(key)
                for key in ("values", "candidate_values", "candidate_labels")
                if isinstance(value.get(key), list)
            ),
            [],
        )
    if isinstance(candidates, str):
        candidates = [candidates]
    if not isinstance(candidates, list):
        return []
    result: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            item = item.get("id")
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def _scan_labels(record: dict[str, Any]) -> dict[str, Any]:
    classification = record.get("classification")
    difficulty = record.get("difficulty")
    if not isinstance(classification, dict) or not isinstance(difficulty, dict):
        raise ThemeWorkbenchError(
            "theme_workbench_label_invalid",
            "a visual scan lacks its controlled label projection",
        )
    primary = classification.get("primary_K")
    if primary is not None and not isinstance(primary, str):
        raise ThemeWorkbenchError(
            "theme_workbench_label_invalid", "primary K label is invalid"
        )
    axes: dict[str, list[str]] = {}
    for axis in ("supporting_K", "A", "C", "R", "RP"):
        values = classification.get(axis)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_label_invalid", f"scan axis {axis} is invalid"
            )
        axes[axis] = list(values)
    item_type = classification.get("item_type")
    prelabel = difficulty.get("cognitive_prelabel")
    if not isinstance(item_type, str) or not isinstance(prelabel, str):
        raise ThemeWorkbenchError(
            "theme_workbench_label_invalid", "scan item type or D label is invalid"
        )
    return {
        "status": "complete",
        "source": "model_visual_scan_candidate",
        "primary_K": primary,
        "supporting_K": axes["supporting_K"],
        "A": axes["A"],
        "C": axes["C"],
        "R": axes["R"],
        "RP": axes["RP"],
        "cognitive_prelabel": prelabel,
    }


def _blocked_labels(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": "none",
        "primary_K": None,
        "supporting_K": [],
        "A": [],
        "C": [],
        "R": [],
        "RP": [],
        "cognitive_prelabel": None,
    }


def _master_candidate_labels(atom: dict[str, Any]) -> dict[str, Any]:
    primary = _known_text(atom.get("primary_knowledge_K"))
    if primary is None:
        candidates = _tag_values(atom.get("primary_knowledge_K"))
        primary = candidates[0] if len(candidates) == 1 else None
    difficulty = atom.get("difficulty")
    prelabel = (
        difficulty.get("declared_prelabel")
        if isinstance(difficulty, dict)
        and isinstance(difficulty.get("declared_prelabel"), str)
        else None
    )
    return {
        "status": "pending",
        "source": "master_candidate",
        "primary_K": primary,
        "supporting_K": _tag_values(atom.get("supporting_knowledge_K")),
        "A": _tag_values(atom.get("ability_A")),
        "C": _tag_values(atom.get("context_C")),
        "R": _tag_values(atom.get("response_R_evidence"))
        or _tag_values(atom.get("response_R")),
        "RP": _tag_values(atom.get("representation_RP_evidence"))
        or _tag_values(atom.get("representation_RP")),
        "cognitive_prelabel": prelabel,
    }


def _answer_metadata(record: dict[str, Any]) -> dict[str, Any]:
    answer = record.get("answer")
    risks = record.get("risks_and_limits")
    candidate = record.get("candidate_analysis")
    known_code = (
        candidate.get("known_quality_note_code")
        if isinstance(candidate, dict)
        else None
    )
    # The 2026 recall product carries an explicit nonblocking note directly on
    # the frozen answer record.  Preserve only its existence, never its text.
    if isinstance(answer, dict) and answer.get("quality_note") is not None:
        known_code = known_code or "source_quality_note"
    return reference_answer_catalog_metadata(answer, risks, known_code)


def _absent_answer() -> dict[str, Any]:
    return {
        "availability": ABSENT,
        "source_authority": NONE,
        "has_quality_note": False,
    }


def _dependency_kind(prior_count: int, shared_count: int) -> str:
    if prior_count == 0:
        return "shared_material_only" if shared_count else "independent"
    if prior_count == 1:
        return "one_prior_part"
    return "multiple_prior_parts"


def _scan_dependency(
    record: dict[str, Any],
    *,
    current_id: str,
    positions: dict[str, tuple[str, int]],
    prior_projection: dict[str, str] | None = None,
) -> dict[str, Any]:
    dependency = record.get("dependency")
    if not isinstance(dependency, dict):
        raise ThemeWorkbenchError(
            "theme_workbench_dependency_invalid", "scan dependency is missing"
        )
    raw_prior = dependency.get("prior_atomic_part_ids")
    shared = dependency.get("shared_material_crop_ids")
    if (
        not isinstance(raw_prior, list)
        or any(not isinstance(value, str) for value in raw_prior)
        or len(raw_prior) != len(set(raw_prior))
        or not isinstance(shared, list)
        or any(not isinstance(value, str) for value in shared)
        or len(shared) != len(set(shared))
    ):
        raise ThemeWorkbenchError(
            "theme_workbench_dependency_invalid", "scan dependency shape drifted"
        )
    projected: list[str] = []
    for prior in raw_prior:
        if prior_projection is not None:
            mapped = prior_projection.get(prior)
            if mapped is None:
                raise ThemeWorkbenchError(
                    "theme_workbench_dependency_blocked",
                    "an exact dependency cannot be projected without guessing",
                )
            prior = mapped
        projected.append(prior)
    current_position = positions.get(current_id)
    if current_position is None:
        raise ThemeWorkbenchError(
            "theme_workbench_dependency_invalid", "current atomic order is unavailable"
        )
    for prior in projected:
        prior_position = positions.get(prior)
        if (
            prior_position is None
            or prior_position[0] != current_position[0]
            or prior_position[1] >= current_position[1]
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_dependency_invalid",
                "a prior atomic dependency is cross-theme or not earlier",
            )
    return {
        "kind": _dependency_kind(len(projected), len(shared)),
        "prior_atomic_part_ids": projected,
        "explicit_prior_edge_count": len(projected),
        "status": "validated_explicit",
    }


def _master_pending_dependency(atom: dict[str, Any]) -> dict[str, Any]:
    value = _known_text(atom.get("inter_part_dependency"))
    if value == "independent":
        return {
            "kind": "independent",
            "prior_atomic_part_ids": [],
            "explicit_prior_edge_count": 0,
            "status": "explicit_master_value",
        }
    if value in {"shared_stimulus_only", "shared_material_only"}:
        return {
            "kind": "shared_material_only",
            "prior_atomic_part_ids": [],
            "explicit_prior_edge_count": 0,
            "status": "explicit_master_value",
        }
    return {
        "kind": "blocked_pending_review",
        "prior_atomic_part_ids": [],
        "explicit_prior_edge_count": 0,
        "status": "blocked_unknown_not_inferred",
    }


def _role(value: Any, status: str) -> dict[str, Any]:
    return {"value": _known_text(value), "status": status}


def _pages_from_record(record: dict[str, Any]) -> set[int]:
    pages: set[int] = set()
    descriptors = [
        *record.get("viewed_evidence", []),
        *record.get("evidence_descriptors", []),
    ]
    for descriptor in descriptors:
        if isinstance(descriptor, dict):
            page = descriptor.get("source_page")
            if type(page) is int and page > 0:
                pages.add(page)
    return pages


def _page_span_from_pages(pages: Iterable[int], status: str) -> dict[str, Any]:
    values = sorted(set(pages))
    return {
        "start_page": values[0] if values else None,
        "end_page": values[-1] if values else None,
        "page_numbers": values,
        "status": status if values else "unknown_pending_review",
    }


def _page_span_from_value(value: Any) -> set[int]:
    value = _evidence_value(value)
    if isinstance(value, dict):
        values = value.get("page_numbers")
        if isinstance(values, list):
            return {page for page in values if type(page) is int and page > 0}
        start = value.get("start_page")
        end = value.get("end_page")
        if type(start) is int and type(end) is int and 0 < start <= end:
            return set(range(start, end + 1))
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(type(page) is int and page > 0 for page in value)
        and value[0] <= value[1]
    ):
        return set(range(value[0], value[1] + 1))
    return set()


def _crop_index(snapshot: Any) -> dict[str, dict[str, Any]]:
    value = getattr(snapshot, "crop_manifest_by_id", None)
    if value is None:
        value = getattr(snapshot, "crop_by_id", None)
    return value if isinstance(value, dict) else {}


def _shared_materials(
    record: dict[str, Any],
    *,
    crop_index: dict[str, dict[str, Any]] | None,
    preview_allowed: bool,
) -> list[dict[str, Any]]:
    dependency = record.get("dependency")
    dependency_ids = (
        dependency.get("shared_material_crop_ids", [])
        if isinstance(dependency, dict)
        else []
    )
    viewed_shared_ids = [
        item.get("crop_id")
        for item in [
            *record.get("viewed_evidence", []),
            *record.get("evidence_descriptors", []),
        ]
        if isinstance(item, dict)
        and item.get("evidence_role") == "shared_material"
        and isinstance(item.get("crop_id"), str)
    ]
    # Shared context is broader than a dependency edge: a theme opening may
    # orient the whole big question even when a particular atomic part can be
    # answered independently.  Preserve both without upgrading the dependency
    # kind or inventing a prior-part edge.
    ids = list(dict.fromkeys([*dependency_ids, *viewed_shared_ids]))
    descriptors = {
        item.get("crop_id"): item
        for item in [
            *record.get("viewed_evidence", []),
            *record.get("evidence_descriptors", []),
        ]
        if isinstance(item, dict) and isinstance(item.get("crop_id"), str)
    }
    result: list[dict[str, Any]] = []
    for material_id in ids:
        if not isinstance(material_id, str):
            raise ThemeWorkbenchError(
                "theme_workbench_shared_context_invalid",
                "shared material identity is invalid",
            )
        metadata = (crop_index or {}).get(material_id, {})
        descriptor = descriptors.get(material_id, {})
        material_type = next(
            (
                value
                for value in (
                    metadata.get("category"),
                    metadata.get("role"),
                    metadata.get("evidence_role"),
                    descriptor.get("evidence_role"),
                )
                if isinstance(value, str) and value
            ),
            "shared_material",
        )
        page = next(
            (
                value
                for value in (
                    metadata.get("source_page"),
                    metadata.get("source_page_number"),
                    descriptor.get("source_page"),
                )
                if type(value) is int and value > 0
            ),
            None,
        )
        result.append(
            {
                "material_id": material_id,
                "type": material_type,
                "page": page,
                "candidate_description_zh": (
                    f"题面显式共享材料（角色：{material_type}），语义仍为模型候选。"
                ),
                "preview_allowed": bool(preview_allowed),
            }
        )
    return result


def _explicit_sort(
    values: list[dict[str, Any]], key: str, *, identity_key: str
) -> list[dict[str, Any]]:
    known = [value[key] for value in values if type(value.get(key)) is int]
    if len(known) != len(set(known)):
        raise ThemeWorkbenchError(
            "theme_workbench_order_invalid",
            f"duplicate explicit order exists for {identity_key}",
        )
    # Python's stable sort preserves immutable source-record order only among
    # unknown ties; such rows remain explicitly marked pending and no ID/number
    # suffix is consulted.
    return sorted(
        values,
        key=lambda value: (
            value.get(key) is None,
            value.get(key) if type(value.get(key)) is int else 0,
        ),
    )


def _answer_bucket(availability: str) -> str:
    if availability == ALIGNED:
        return "answer_aligned"
    if availability == UNALIGNED:
        return "answer_unaligned"
    if availability == ABSENT:
        return "answer_absent"
    raise ThemeWorkbenchError(
        "theme_workbench_answer_invalid", "answer availability is unsupported"
    )


def _counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "printed": len({entry["printed_question_id"] for entry in entries}),
        "atomic": len(entries),
        "display_atomic_units": 0,
        "visual_scanned": 0,
        "unscanned": 0,
        "label_complete": 0,
        "label_pending": 0,
        "answer_aligned": 0,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 0,
    }
    for entry in entries:
        scanned = entry["visual_coverage_kind"] != "unscanned"
        counts["visual_scanned" if scanned else "unscanned"] += 1
        counts[
            "label_complete"
            if entry["label_summary"]["status"] == "complete"
            else "label_pending"
        ] += 1
        units = entry["alias_units"] or [entry]
        counts["display_atomic_units"] += len(units)
        for unit in units:
            answer = unit["answer"]
            counts[_answer_bucket(answer["availability"])] += 1
            counts["quality_notes"] += int(answer["has_quality_note"])
    return counts


def _dependency_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "independent": 0,
        "shared_material_only": 0,
        "one_prior_part": 0,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "explicit_prior_edge_count": 0,
        "blocked": 0,
    }
    for entry in entries:
        dependency = entry["dependency"]
        kind = dependency["kind"]
        if kind not in result:
            if kind != "blocked_pending_review":
                raise ThemeWorkbenchError(
                    "theme_workbench_dependency_invalid",
                    "dependency kind is unsupported",
                )
        else:
            result[kind] += 1
        result["explicit_prior_edge_count"] += dependency["explicit_prior_edge_count"]
        if kind == "blocked_pending_review" or str(
            dependency.get("status", "")
        ).startswith("blocked"):
            result["blocked"] += 1
    return result


def _material_context(entries: list[dict[str, Any]], context: Any) -> dict[str, Any]:
    used_by: dict[str, set[str]] = defaultdict(set)
    material_rows: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for material in entry.pop("_shared_materials"):
            material_id = material["material_id"]
            previous = material_rows.get(material_id)
            if previous is not None and any(
                previous[key] != material[key]
                for key in ("type", "page", "preview_allowed")
            ):
                raise ThemeWorkbenchError(
                    "theme_workbench_shared_context_invalid",
                    "a shared material has conflicting explicit metadata",
                )
            material_rows[material_id] = material
            used_by[material_id].add(entry["atomic_part_id"])
    materials = []
    for material_id, material in material_rows.items():
        row = dict(material)
        row["used_by_atomic_count"] = len(used_by[material_id])
        materials.append(row)
    # Material order follows first appearance in the already explicit atomic
    # order.  It is not sorted by crop/file/identifier suffix.
    summary = None
    status = "unknown_pending_review"
    if isinstance(context, dict):
        summary = _known_text(context.get("summary"))
        status = _known_text(context.get("status")) or status
    elif isinstance(context, str) and context:
        summary = context
        status = "candidate_context"
    return {
        "context_summary_zh": summary,
        "context_status": status,
        "material_count": len(materials),
        "materials": materials,
    }


def _strip_internal(entry: dict[str, Any]) -> dict[str, Any]:
    entry.pop("_pages", None)
    entry.pop("_shared_materials", None)
    return entry


def _reject_unsafe_projection(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if lowered in _FORBIDDEN_KEYS or lowered.endswith(
                ("_path", "_url", "_sha256", "_hash", "_bindings")
            ):
                raise ThemeWorkbenchError(
                    "theme_workbench_projection_leak",
                    "theme projection contains a forbidden evidence or answer field",
                )
            _reject_unsafe_projection(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_unsafe_projection(nested)
    elif isinstance(value, str):
        if value.startswith("/api/"):
            return
        if _FORBIDDEN_STRING.search(value):
            raise ThemeWorkbenchError(
                "theme_workbench_projection_leak",
                "theme projection contains a local locator or URL",
            )


class ThemeWorkbenchReader:
    """Fresh-snapshot theme aggregation across Wave252 and Master470."""

    def __init__(
        self,
        shchem_root: Path,
        *,
        master_workbench: MasterWave1WorkbenchReader | None = None,
        direct_scans: MasterDirectVisualScanReader | None = None,
        wave_scans: QuestionVisualScanReader | None = None,
        parent_chain_overlay: MasterParentChainRepairOverlayReader | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )
        self.direct_scans = direct_scans or MasterDirectVisualScanReader(
            self.shchem_root, master_workbench=self.master_workbench
        )
        self.wave_scans = wave_scans or QuestionVisualScanReader(self.shchem_root)
        self.wave_review = Wave1CandidateReviewReader(self.shchem_root)
        self.parent_chain_overlay = (
            parent_chain_overlay
            or MasterParentChainRepairOverlayReader(self.shchem_root)
        )

    @staticmethod
    def _scan_index(
        validated: tuple[tuple[Any, Any], ...],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for _, snapshot in validated:
            for node_id, record in snapshot.by_node_id.items():
                if node_id in result:
                    raise ThemeWorkbenchError(
                        "theme_workbench_duplicate_atomic",
                        "a Wave scan atomic identity is duplicated",
                    )
                result[node_id] = record
        if len(result) != 252:
            raise ThemeWorkbenchError(
                "theme_workbench_scope_count_mismatch",
                "Wave visual-scan inventory is not exactly 252",
            )
        return result

    @staticmethod
    def _wave_positions(
        records: Iterable[dict[str, Any]],
    ) -> dict[str, tuple[str, int]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            hierarchy = record["hierarchy"]
            grouped[hierarchy["theme_id"]].append(record)
        positions: dict[str, tuple[str, int]] = {}
        for theme_id, items in grouped.items():
            ordered = sorted(
                items,
                key=lambda record: (
                    record["hierarchy"]["printed_sequence"],
                    record["hierarchy"]["atomic_sequence_in_printed"],
                ),
            )
            explicit = [
                (
                    item["hierarchy"]["printed_sequence"],
                    item["hierarchy"]["atomic_sequence_in_printed"],
                )
                for item in ordered
            ]
            if len(explicit) != len(set(explicit)):
                raise ThemeWorkbenchError(
                    "theme_workbench_order_invalid",
                    "Wave atomic explicit order is duplicated",
                )
            for index, item in enumerate(ordered):
                positions[item["hierarchy"]["atomic_part_id"]] = (theme_id, index)
        return positions

    def _wave_entry(
        self,
        record: dict[str, Any],
        *,
        atom: dict[str, Any],
        printed: dict[str, Any],
        positions: dict[str, tuple[str, int]],
        preview_allowed: bool,
    ) -> dict[str, Any]:
        hierarchy = record["hierarchy"]
        node_id = hierarchy["atomic_part_id"]
        labels = _scan_labels(record)
        item_type = record["classification"]["item_type"]
        role_value = atom.get("primary_theme_chain_role")
        role_status = _known_text(atom.get("theme_chain_role_status")) or (
            "model_candidate_pending_human"
        )
        return {
            "atomic_part_id": node_id,
            "printed_question_id": hierarchy["printed_question_id"],
            "printed_question_number": _known_text(
                printed.get("printed_question_number")
            ),
            "printed_sequence": hierarchy["printed_sequence"],
            "printed_sequence_status": "known_explicit",
            "atomic_sequence_in_printed": hierarchy["atomic_sequence_in_printed"],
            "atomic_sequence_status": "known_explicit",
            "item_type": item_type,
            "label_summary": labels,
            "answer": _answer_metadata(record),
            "visual_coverage_kind": "wave1_visual_scan",
            "dependency": _scan_dependency(
                record, current_id=node_id, positions=positions
            ),
            "visible_summary_zh": record.get("visible_summary_zh"),
            "response_requirement_zh": record.get("response_requirement_zh"),
            "theme_chain_role": _role(role_value, role_status),
            "detail_endpoint": (
                f"/api/v1/kb/workbench/question-visual-scans/{quote(node_id, safe='')}"
            ),
            "alias_units": [],
            "_pages": _pages_from_record(record),
            "_shared_materials": _shared_materials(
                record, crop_index=None, preview_allowed=preview_allowed
            ),
        }

    def _wave(self) -> dict[str, Any]:
        with ReaderThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shchem-theme-wave"
        ) as executor:
            review_future = executor.submit(self.wave_review._snapshot)
            scans_future = executor.submit(self.wave_scans._validated_catalog)
            review = review_future.result()
            validated_scans = scans_future.result()
        scans = self._scan_index(validated_scans)
        indexes = review["indexes"]
        atoms = indexes["atomic_part"]
        if set(atoms) != set(scans):
            raise ThemeWorkbenchError(
                "theme_workbench_scope_identity_mismatch",
                "Wave hierarchy and visual-scan identities differ",
            )
        positions = self._wave_positions(scans.values())

        paper_order = {
            row["paper_id"]: index
            for index, row in enumerate(review["records"]["paper"])
        }
        paper_groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for node_id, record in scans.items():
            hierarchy = record.get("hierarchy")
            atom = atoms[node_id]
            if (
                hierarchy.get("paper_id") != atom.get("paper_id")
                or hierarchy.get("theme_id") != atom.get("theme_big_question_id")
                or hierarchy.get("printed_question_id")
                != atom.get("printed_question_id")
            ):
                raise ThemeWorkbenchError(
                    "theme_workbench_parent_chain_invalid",
                    "Wave scan and hierarchy parent chains differ",
                )
            theme = indexes["theme_big_question"].get(hierarchy["theme_id"])
            printed = indexes["printed_question"].get(hierarchy["printed_question_id"])
            if theme is None or printed is None:
                raise ThemeWorkbenchError(
                    "theme_workbench_parent_chain_invalid",
                    "Wave scan parent is unavailable",
                )
            if hierarchy.get("theme_sequence") != _known_int(
                theme.get("sequence_in_paper")
            ) or hierarchy.get("printed_sequence") != _known_int(
                printed.get("sequence_in_theme")
            ):
                raise ThemeWorkbenchError(
                    "theme_workbench_order_invalid",
                    "Wave scan explicit theme/printed order drifted",
                )
            paper_groups[hierarchy["paper_id"]][hierarchy["theme_id"]].append(
                self._wave_entry(
                    record,
                    atom=atom,
                    printed=printed,
                    positions=positions,
                    preview_allowed=True,
                )
            )

        papers: list[dict[str, Any]] = []
        all_entries: list[dict[str, Any]] = []
        for paper_id in sorted(paper_groups, key=lambda value: paper_order[value]):
            paper_row = indexes["paper"][paper_id]
            paper = {
                "id": paper_id,
                "title": self.wave_review._paper_title(paper_row),
                "source_metadata": _wave_source_metadata(paper_row),
                "order": None,
                "order_status": "unknown_source_order_preserved",
                "status": "complete_parent_chain_inventory",
                "observed_theme_count": len(paper_groups[paper_id]),
                "missing_theme_note_zh": None,
            }
            theme_rows: list[dict[str, Any]] = []
            for theme_id, entries in paper_groups[paper_id].items():
                theme_row = indexes["theme_big_question"][theme_id]
                entries.sort(
                    key=lambda entry: (
                        entry["printed_sequence"],
                        entry["atomic_sequence_in_printed"],
                    )
                )
                pages = set().union(*(entry["_pages"] for entry in entries))
                pages |= _page_span_from_value(theme_row.get("page_span"))
                shared_context = _material_context(
                    entries, theme_row.get("theme_context")
                )
                if theme_id == "THEME-516deca72e2d9aca307d":
                    shared_context["context_summary_zh"] = (
                        "生活中常见的消毒剂有含氯消毒剂、含碘消毒剂、双氧水等；"
                        "后半题另共享含氯微粒随 pH 变化的分布图。"
                    )
                    shared_context["context_status"] = "visual_scan_candidate_context"
                theme = {
                    "id": theme_id,
                    "title": _known_text(theme_row.get("printed_title")),
                    "sequence": _known_int(theme_row.get("sequence_in_paper")),
                    "sequence_status": "known_explicit",
                    "page_span": _page_span_from_pages(
                        pages, "explicit_visual_evidence_union"
                    ),
                    "parent_chain_status": "complete",
                }
                theme_rows.append(
                    {
                        "paper": deepcopy(paper),
                        "theme": theme,
                        "counts": _counts(entries),
                        "shared_context": shared_context,
                        "dependencies": _dependency_counts(entries),
                        "atomic_chain": [_strip_internal(entry) for entry in entries],
                    }
                )
                all_entries.extend(entries)
            sequences = [group["theme"]["sequence"] for group in theme_rows]
            if any(type(sequence) is not int for sequence in sequences) or len(
                sequences
            ) != len(set(sequences)):
                raise ThemeWorkbenchError(
                    "theme_workbench_order_invalid",
                    "Wave theme explicit order is missing or duplicated",
                )
            theme_rows.sort(key=lambda group: group["theme"]["sequence"])
            papers.append({"paper": paper, "theme_groups": theme_rows})

        result = self._finalize(
            scope=WAVE1_SCOPE,
            papers=papers,
            unassigned_entries=[],
            expected=WAVE_EXPECTED,
        )
        return result

    def _master_positions(
        self, snapshot: Any
    ) -> tuple[
        dict[str, tuple[str, int]],
        dict[str, tuple[str, str, str]],
        list[dict[str, Any]],
    ]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        chains: dict[str, tuple[str, str, str]] = {}
        incomplete: list[dict[str, Any]] = []
        for atom in snapshot.master_layers["atomic_part"]:
            chain = self.master_workbench._parent_chain(atom, snapshot)
            if not chain["complete"]:
                incomplete.append(atom)
                continue
            ids = chain["hierarchy_ids"]
            node_id = atom["atomic_part_id"]
            chains[node_id] = (
                ids["paper_id"],
                ids["theme_big_question_id"],
                ids["printed_question_id"],
            )
            grouped[ids["theme_big_question_id"]].append(atom)
        if len(chains) != 427 or len(incomplete) != 43:
            raise ThemeWorkbenchError(
                "theme_workbench_parent_chain_invalid",
                "Master complete/incomplete parent-chain split drifted",
            )
        positions: dict[str, tuple[str, int]] = {}
        for theme_id, atoms in grouped.items():
            decorated = []
            for source_index, atom in enumerate(atoms):
                printed = snapshot.master_nodes[
                    ("printed_question", atom["parent_printed_question_id"])
                ]
                decorated.append(
                    {
                        "atom": atom,
                        "printed_order": _known_int(
                            _master_order("printed_question", printed)
                        ),
                        "atomic_order": _known_int(_master_order("atomic_part", atom)),
                        "source_index": source_index,
                    }
                )
            for printed_id in {atom["parent_printed_question_id"] for atom in atoms}:
                explicit = [
                    item["atomic_order"]
                    for item in decorated
                    if item["atom"]["parent_printed_question_id"] == printed_id
                    and type(item["atomic_order"]) is int
                ]
                if len(explicit) != len(set(explicit)):
                    raise ThemeWorkbenchError(
                        "theme_workbench_order_invalid",
                        "Master atomic explicit order is duplicated",
                    )
            ordered = sorted(
                decorated,
                key=lambda item: (
                    item["printed_order"] is None,
                    item["printed_order"] or 0,
                    item["atomic_order"] is None,
                    item["atomic_order"] or 0,
                    item["source_index"],
                ),
            )
            for index, item in enumerate(ordered):
                positions[item["atom"]["atomic_part_id"]] = (theme_id, index)
        return positions, chains, incomplete

    @staticmethod
    def _direct_snapshots(reader: MasterDirectVisualScanReader) -> dict[str, Any]:
        registrations = reader._reader_registrations
        with ReaderThreadPoolExecutor(
            max_workers=len(registrations),
            thread_name_prefix="shchem-theme-direct",
        ) as executor:
            snapshots = tuple(
                executor.map(
                    lambda registration: getattr(
                        reader, registration.attribute
                    )._snapshot(),
                    registrations,
                )
            )
        return {
            registration.attribute: snapshot
            for registration, snapshot in zip(registrations, snapshots, strict=True)
        }

    def _master_base_entry(
        self,
        atom: dict[str, Any],
        *,
        printed: dict[str, Any] | None,
    ) -> dict[str, Any]:
        node_id = atom["atomic_part_id"]
        printed_id = atom.get("parent_printed_question_id")
        pages = (
            _page_span_from_value(printed.get("question_page_span"))
            if isinstance(printed, dict)
            else set()
        )
        item_type = _master_scalar(atom.get("item_type")) or atom.get("core_item_type")
        printed_sequence = (
            _known_int(_master_order("printed_question", printed))
            if isinstance(printed, dict)
            else None
        )
        atomic_sequence = _known_int(_master_order("atomic_part", atom))
        return {
            "atomic_part_id": node_id,
            "printed_question_id": printed_id,
            "printed_question_number": (
                _known_text(printed.get("printed_question_number_literal"))
                if isinstance(printed, dict)
                else None
            ),
            "printed_sequence": printed_sequence,
            "printed_sequence_status": (
                "known_explicit"
                if printed_sequence is not None
                else "unknown_pending_review"
            ),
            "atomic_sequence_in_printed": atomic_sequence,
            "atomic_sequence_status": (
                "known_explicit"
                if atomic_sequence is not None
                else "unknown_pending_review"
            ),
            "item_type": item_type if isinstance(item_type, str) else None,
            "label_summary": _master_candidate_labels(atom),
            "answer": _absent_answer(),
            "visual_coverage_kind": "unscanned",
            "dependency": _master_pending_dependency(atom),
            "visible_summary_zh": None,
            "response_requirement_zh": None,
            "theme_chain_role": _role(
                atom.get("role_in_theme_progression"),
                "master_candidate_pending_human",
            ),
            "detail_endpoint": (
                f"/api/v1/kb/workbench/master-atomic/{quote(node_id, safe='')}"
            ),
            "alias_units": [],
            "_pages": pages,
            "_shared_materials": [],
        }

    def _apply_scan_to_master_entry(
        self,
        entry: dict[str, Any],
        record: dict[str, Any],
        *,
        positions: dict[str, tuple[str, int]],
        coverage_kind: str,
        detail_endpoint: str,
        crop_index: dict[str, dict[str, Any]] | None,
        preview_allowed: bool,
        prior_projection: dict[str, str] | None = None,
        label_allowed: bool = True,
    ) -> None:
        node_id = entry["atomic_part_id"]
        entry["item_type"] = (
            record["classification"]["item_type"] if label_allowed else None
        )
        entry["label_summary"] = (
            _scan_labels(record)
            if label_allowed
            else _blocked_labels("blocked_exact_label_projection")
        )
        entry["answer"] = _answer_metadata(record)
        entry["visual_coverage_kind"] = coverage_kind
        entry["dependency"] = _scan_dependency(
            record,
            current_id=node_id,
            positions=positions,
            prior_projection=prior_projection,
        )
        entry["visible_summary_zh"] = record.get("visible_summary_zh")
        entry["response_requirement_zh"] = record.get("response_requirement_zh")
        chain_role = record.get("theme_chain_role")
        if isinstance(chain_role, dict):
            entry["theme_chain_role"] = _role(
                chain_role.get("role_zh"), "model_candidate_pending_human"
            )
        entry["detail_endpoint"] = detail_endpoint
        entry["_pages"] |= _pages_from_record(record)
        entry["_shared_materials"] = _shared_materials(
            record, crop_index=crop_index, preview_allowed=preview_allowed
        )

    def _alias_units(
        self,
        alias_record: dict[str, Any],
        alias_snapshot: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, int]]]:
        pt_records = alias_snapshot.pt_snapshot.by_node_id
        positions = self._wave_positions(pt_records.values())
        units: list[dict[str, Any]] = []
        for wave_id in alias_record["target"]["wave_scan_atomic_part_ids"]:
            record = pt_records.get(wave_id)
            if record is None:
                raise ThemeWorkbenchError(
                    "theme_workbench_alias_invalid",
                    "an alias target scan unit is unavailable",
                )
            hierarchy = record["hierarchy"]
            units.append(
                {
                    "atomic_part_id": wave_id,
                    "atomic_sequence_in_printed": hierarchy[
                        "atomic_sequence_in_printed"
                    ],
                    "atomic_sequence_status": "known_explicit",
                    "item_type": record["classification"]["item_type"],
                    "label_summary": _scan_labels(record),
                    "answer": _answer_metadata(record),
                    "dependency": _scan_dependency(
                        record, current_id=wave_id, positions=positions
                    ),
                    "visible_summary_zh": record.get("visible_summary_zh"),
                    "response_requirement_zh": record.get("response_requirement_zh"),
                }
            )
        return units, positions

    @staticmethod
    def _direct_alias_units(
        record: dict[str, Any], parent_id: str
    ) -> list[dict[str, Any]]:
        raw_units = record.get("minimal_atomic_units")
        if not isinstance(raw_units, list) or not raw_units:
            return []
        units: list[dict[str, Any]] = []
        for raw_unit in raw_units:
            if not isinstance(raw_unit, dict):
                raise ThemeWorkbenchError(
                    "theme_workbench_direct_unit_invalid",
                    "a grouped direct scan unit is invalid",
                )
            hierarchy = raw_unit.get("hierarchy")
            hierarchy = hierarchy if isinstance(hierarchy, dict) else {}
            unit_id = raw_unit.get("atomic_part_id") or hierarchy.get("atomic_part_id")
            if not isinstance(unit_id, str) or not unit_id:
                raise ThemeWorkbenchError(
                    "theme_workbench_direct_unit_invalid",
                    "a grouped direct scan unit lacks an explicit identity",
                )
            projected = project_direct_unit_scan(record, unit_id)
            classification = projected["scan_classification"]
            difficulty = projected["cognitive_difficulty"]
            reference = projected["reference_answer"]
            dependency = deepcopy(projected["dependency"])
            dependency.pop("shared_material_crop_ids", None)
            sequence = hierarchy.get("atomic_sequence_in_printed")
            if type(sequence) is not int or sequence < 1:
                raise ThemeWorkbenchError(
                    "theme_workbench_direct_unit_invalid",
                    "a grouped direct scan unit lacks explicit order",
                )
            units.append(
                {
                    "atomic_part_id": unit_id,
                    "atomic_sequence_in_printed": sequence,
                    "atomic_sequence_status": "known_explicit",
                    "item_type": classification.get("item_type"),
                    "label_summary": _scan_labels(
                        {
                            "classification": classification,
                            "difficulty": difficulty,
                        }
                    ),
                    "answer": {
                        "availability": reference["availability"],
                        "source_authority": reference["source_authority"],
                        "has_quality_note": bool(projected.get("quality_notes")),
                    },
                    "dependency": dependency,
                    "visible_summary_zh": projected.get("visible_summary_zh"),
                    "response_requirement_zh": projected.get("response_requirement_zh"),
                    "source_scope": "master",
                    "source_parent_atomic_id": parent_id,
                }
            )
        sequences = [unit["atomic_sequence_in_printed"] for unit in units]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ThemeWorkbenchError(
                "theme_workbench_direct_unit_invalid",
                "grouped direct scan unit order is duplicated or reversed",
            )
        return units

    def _master(self) -> dict[str, Any]:
        alias_reader = MasterVisualScanAliasReader(
            self.shchem_root, master_workbench=self.master_workbench
        )
        with ReaderThreadPoolExecutor(
            max_workers=4, thread_name_prefix="shchem-theme-master"
        ) as executor:
            master_future = executor.submit(self.master_workbench._snapshot)
            wave_future = executor.submit(self.wave_scans._validated_catalog)
            direct_future = executor.submit(self._direct_snapshots, self.direct_scans)
            alias_future = executor.submit(alias_reader._snapshot)
            master = master_future.result()
            wave_validated = wave_future.result()
            direct_snapshots = direct_future.result()
            alias_snapshot = alias_future.result()

        positions, chains, incomplete_atoms = self._master_positions(master)
        master_ids = {
            atom["atomic_part_id"] for atom in master.master_layers["atomic_part"]
        }
        wave_scans = self._scan_index(wave_validated)

        exact_to_wave: dict[str, str] = {}
        wave_to_exact: dict[str, str] = {}
        label_allowed: set[str] = set()
        for master_id, rows in master.relations_by_master.items():
            if len(rows) != 1:
                continue
            relation = rows[0]
            if (
                relation.get("relation_type") == "exact_1_to_1"
                and relation.get("identity_mapping_allowed") is True
            ):
                wave_id = relation["wave1"]["endpoint_id"]
                exact_to_wave[master_id] = wave_id
                wave_to_exact[wave_id] = master_id
                if relation.get("label_overlay_allowed") is True:
                    label_allowed.add(master_id)
        if (
            len(exact_to_wave) != 169
            or len(wave_to_exact) != 169
            or len(label_allowed) != 165
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_exact_boundary_invalid",
                "exact169/label165 boundary drifted",
            )
        wave_positions = self._wave_positions(wave_scans.values())
        exact_positions = {
            master_id: wave_positions[wave_id]
            for master_id, wave_id in exact_to_wave.items()
        }

        direct_by_id: dict[
            str,
            tuple[
                dict[str, Any],
                dict[str, dict[str, Any]],
                dict[str, tuple[str, int]],
            ],
        ] = {}
        for snapshot in direct_snapshots.values():
            index = _crop_index(snapshot)
            direct_positions = self._wave_positions(snapshot.records)
            for node_id, record in snapshot.by_master_id.items():
                if node_id in direct_by_id:
                    raise ThemeWorkbenchError(
                        "theme_workbench_duplicate_atomic",
                        "direct products overlap",
                    )
                direct_by_id[node_id] = (record, index, direct_positions)
        alias_by_id = dict(alias_snapshot.by_master_id)
        expected_direct_count = sum(
            registration.expected_record_count
            for registration in self.direct_scans._reader_registrations
        )
        if (
            len(direct_by_id) != expected_direct_count
            or len(alias_by_id) != 14
            or set(direct_by_id) & set(exact_to_wave)
            or set(alias_by_id) & (set(direct_by_id) | set(exact_to_wave))
            or not (set(direct_by_id) | set(alias_by_id) | set(exact_to_wave))
            <= master_ids
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_coverage_boundary_invalid",
                "exact/direct/alias coverage boundaries drifted",
            )

        entries_by_id: dict[str, dict[str, Any]] = {}
        for atom in master.master_layers["atomic_part"]:
            node_id = atom["atomic_part_id"]
            printed = master.master_nodes.get(
                ("printed_question", atom.get("parent_printed_question_id"))
            )
            entry = self._master_base_entry(atom, printed=printed)
            if node_id in direct_by_id:
                record, index, direct_positions = direct_by_id[node_id]
                direct_units = self._direct_alias_units(record, node_id)
                if direct_units:
                    entry["item_type"] = None
                    entry["label_summary"] = _blocked_labels("per_alias_unit_no_merge")
                    entry["answer"] = {
                        "availability": "per_alias_unit",
                        "source_authority": "per_alias_unit",
                        "has_quality_note": any(
                            unit["answer"]["has_quality_note"] for unit in direct_units
                        ),
                    }
                    entry["visual_coverage_kind"] = "direct_new_visual_scan"
                    entry["dependency"] = {
                        "kind": "per_alias_unit",
                        "prior_atomic_part_ids": [],
                        "explicit_prior_edge_count": sum(
                            unit["dependency"]["explicit_prior_edge_count"]
                            for unit in direct_units
                        ),
                        "status": "unitized_alias_no_merge",
                    }
                    entry["detail_endpoint"] = (
                        "/api/v1/kb/workbench/master-direct-scans/"
                        f"{quote(node_id, safe='')}"
                    )
                    entry["alias_units"] = direct_units
                    entry["_pages"] |= _pages_from_record(record)
                    entry["_shared_materials"] = _shared_materials(
                        record,
                        crop_index=index,
                        preview_allowed=True,
                    )
                else:
                    self._apply_scan_to_master_entry(
                        entry,
                        record,
                        # Complete Master chains use Master ordering.  The exact
                        # 43 incomplete parent chains remain unassigned, but their
                        # direct product still has an explicit, hash-bound scan
                        # sequence that can validate dependency direction without
                        # being used to guess a Master theme assignment.
                        positions=(
                            positions if node_id in positions else direct_positions
                        ),
                        coverage_kind="direct_new_visual_scan",
                        detail_endpoint=(
                            "/api/v1/kb/workbench/master-direct-scans/"
                            f"{quote(node_id, safe='')}"
                        ),
                        crop_index=index,
                        preview_allowed=True,
                    )
            elif node_id in exact_to_wave:
                record = wave_scans.get(exact_to_wave[node_id])
                if record is None:
                    raise ThemeWorkbenchError(
                        "theme_workbench_exact_boundary_invalid",
                        "an exact scan record is unavailable",
                    )
                self._apply_scan_to_master_entry(
                    entry,
                    record,
                    positions=(positions if node_id in positions else exact_positions),
                    coverage_kind="exact_existing_visual_scan",
                    detail_endpoint=entry["detail_endpoint"],
                    crop_index=None,
                    preview_allowed=False,
                    prior_projection=wave_to_exact,
                    label_allowed=node_id in label_allowed,
                )
            elif node_id in alias_by_id:
                alias_record = alias_by_id[node_id]
                units, _ = self._alias_units(alias_record, alias_snapshot)
                entry["item_type"] = None
                entry["label_summary"] = _blocked_labels("per_alias_unit_no_merge")
                entry["answer"] = {
                    "availability": "per_alias_unit",
                    "source_authority": "per_alias_unit",
                    "has_quality_note": any(
                        unit["answer"]["has_quality_note"] for unit in units
                    ),
                }
                entry["visual_coverage_kind"] = "alias_existing_visual_scan"
                entry["dependency"] = {
                    "kind": "per_alias_unit",
                    "prior_atomic_part_ids": [],
                    "explicit_prior_edge_count": sum(
                        unit["dependency"]["explicit_prior_edge_count"]
                        for unit in units
                    ),
                    "status": "unitized_alias_no_merge",
                }
                entry["detail_endpoint"] = (
                    f"/api/v1/kb/workbench/master-atomic/{quote(node_id, safe='')}"
                )
                entry["alias_units"] = units
                target_records = [
                    alias_snapshot.pt_snapshot.by_node_id[unit["atomic_part_id"]]
                    for unit in units
                ]
                entry["_pages"] |= set().union(
                    *(_pages_from_record(record) for record in target_records)
                )
                material_by_id: dict[str, dict[str, Any]] = {}
                for record in target_records:
                    for material in _shared_materials(
                        record, crop_index=None, preview_allowed=False
                    ):
                        material_by_id.setdefault(material["material_id"], material)
                entry["_shared_materials"] = list(material_by_id.values())
            entries_by_id[node_id] = entry

        if len(entries_by_id) != 470:
            raise ThemeWorkbenchError(
                "theme_workbench_scope_count_mismatch",
                "Master atomic projection is not exactly 470",
            )

        grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        unassigned_entries: list[dict[str, Any]] = []
        for node_id, entry in entries_by_id.items():
            chain = chains.get(node_id)
            if chain is None:
                unassigned_entries.append(entry)
            else:
                grouped[chain[0]][chain[1]].append(entry)

        paper_source_order = {
            row["paper_id"]: index
            for index, row in enumerate(master.master_layers["paper"])
        }
        recall_snapshot = direct_snapshots.get("shanghai_exam_2026_recall")
        if recall_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_recall_boundary_invalid",
                "the explicitly registered 2026 recall product is unavailable",
            )
        recall_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in recall_snapshot.records
        )
        if recall_counts != Counter({1: 8, 2: 11, 3: 10, 5: 6}):
            raise ThemeWorkbenchError(
                "theme_workbench_recall_boundary_invalid",
                "2026 recall four-theme distribution drifted",
            )
        recall_paper_id = recall_snapshot.records[0]["hierarchy"]["paper_id"]
        pudong_snapshot = direct_snapshots.get("pudong2026_first_mock_theme")
        if pudong_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_pudong_theme_boundary_invalid",
                "the explicitly registered Pudong theme-one product is unavailable",
            )
        pudong_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in pudong_snapshot.records
        )
        pudong_theme_ids = {
            record["hierarchy"]["theme_id"] for record in pudong_snapshot.records
        }
        pudong_paper_id = pudong_snapshot.records[0]["hierarchy"]["paper_id"]
        pudong_boundary = pudong_snapshot.paper_identity_boundary
        if (
            pudong_counts != Counter({1: 11})
            or pudong_theme_ids != {"THEME-2c558871ad0b19268d6a"}
            or pudong_boundary.get("covered_scope") != "主题一（水合肼）"
            or pudong_boundary.get("complete_paper_visual_scan_claim_allowed")
            is not False
            or pudong_boundary.get("pudong_second_mock_claim_allowed") is not False
            or pudong_boundary.get("putuo_pt_identity_claim_allowed") is not False
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_pudong_theme_boundary_invalid",
                "Pudong theme-one scope or incomplete-paper boundary drifted",
            )

        jiading_snapshot = direct_snapshots.get("jiading2025_theme1")
        if jiading_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_jiading_theme_boundary_invalid",
                "the explicitly registered Jiading theme-one product is unavailable",
            )
        jiading_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in jiading_snapshot.records
        )
        jiading_theme_ids = {
            record["hierarchy"]["theme_id"] for record in jiading_snapshot.records
        }
        jiading_paper_id = jiading_snapshot.records[0]["hierarchy"]["paper_id"]
        jiading_boundary = jiading_snapshot.paper_identity_boundary
        if (
            jiading_counts != Counter({1: 10})
            or jiading_theme_ids != {"THEME-516deca72e2d9aca307d"}
            or jiading_boundary.get("covered_scope") != "主题一（消毒剂）"
            or jiading_boundary.get("paper_family") != "second_mock_article_classified"
            or jiading_boundary.get("complete_paper_visual_scan_claim_allowed")
            is not False
            or jiading_boundary.get("jiading_district_face_claim_allowed") is not False
            or jiading_boundary.get("second_mock_face_claim_allowed") is not False
            or jiading_boundary.get("official_identity_claim_allowed") is not False
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_jiading_theme_boundary_invalid",
                "Jiading article-classified theme-one identity boundary drifted",
            )

        huangpu_snapshot = direct_snapshots.get("huangpu2025_theme4")
        if huangpu_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_huangpu_theme_boundary_invalid",
                "the explicitly registered Huangpu theme-four product is unavailable",
            )
        huangpu_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in huangpu_snapshot.records
        )
        huangpu_theme_ids = {
            record["hierarchy"]["theme_id"] for record in huangpu_snapshot.records
        }
        huangpu_paper_id = huangpu_snapshot.records[0]["hierarchy"]["paper_id"]
        huangpu_boundary = huangpu_snapshot.paper_identity_boundary
        if (
            huangpu_counts != Counter({4: 11})
            or huangpu_theme_ids != {"THEME-9dcb346c7fc55289c209"}
            or huangpu_boundary.get("covered_scope") != "主题四（碘酸钙的制备）"
            or huangpu_boundary.get("year") != 2025
            or huangpu_boundary.get("region_label") != "黄浦区（卷面直接身份）"
            or huangpu_boundary.get("paper_type") != "二模"
            or huangpu_boundary.get("complete_paper_visual_scan_claim_allowed")
            is not False
            or huangpu_boundary.get("official_identity_claim_allowed") is not False
            or huangpu_boundary.get("answer_independently_verified") is not False
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_huangpu_theme_boundary_invalid",
                "Huangpu theme-four identity or answer boundary drifted",
            )

        qibao_snapshot = direct_snapshots.get("qibao2025_opening_theme4")
        if qibao_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_qibao_theme_boundary_invalid",
                "the explicitly registered Qibao theme-four product is unavailable",
            )
        qibao_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in qibao_snapshot.records
        )
        qibao_theme_ids = {
            record["hierarchy"]["theme_id"] for record in qibao_snapshot.records
        }
        qibao_paper_id = qibao_snapshot.records[0]["hierarchy"]["paper_id"]
        qibao_boundary = qibao_snapshot.paper_identity_boundary
        if (
            qibao_counts != Counter({4: 10})
            or qibao_theme_ids != {"THEME-14f2350cd3d5dab644a6"}
            or qibao_boundary.get("covered_scope") != "主题四（电解质溶液及废水处理）"
            or qibao_boundary.get("year") != 2025
            or qibao_boundary.get("region_label") != "上海市七宝中学（卷面直接身份）"
            or qibao_boundary.get("paper_type") != "高三年级开学练习"
            or qibao_boundary.get("complete_paper_visual_scan_claim_allowed")
            is not False
            or qibao_boundary.get("official_identity_claim_allowed") is not False
            or qibao_boundary.get("answer_independently_verified") is not False
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_qibao_theme_boundary_invalid",
                "Qibao theme-four identity or answer boundary drifted",
            )

        hongkou_snapshot = direct_snapshots.get("hongkou2026_second_mock_theme4")
        if hongkou_snapshot is None:
            raise ThemeWorkbenchError(
                "theme_workbench_hongkou_theme_boundary_invalid",
                "the explicitly registered Hongkou theme-four product is unavailable",
            )
        hongkou_counts = Counter(
            record["hierarchy"]["theme_sequence"] for record in hongkou_snapshot.records
        )
        hongkou_theme_ids = {
            record["hierarchy"]["theme_id"] for record in hongkou_snapshot.records
        }
        hongkou_paper_id = hongkou_snapshot.records[0]["hierarchy"]["paper_id"]
        hongkou_boundary = hongkou_snapshot.paper_identity_boundary
        if (
            hongkou_counts != Counter({4: 9})
            or hongkou_theme_ids != {"THEME-11c6898df179f3701b15"}
            or hongkou_boundary.get("covered_scope")
            != "主题四（电镀污泥中镍的回收与测定）"
            or hongkou_boundary.get("year") != 2026
            or hongkou_boundary.get("region_label")
            != "虹口区（公众号标题；卷面未署地区）"
            or hongkou_boundary.get("paper_type") != "二模（公众号标题；卷面未署二模）"
            or hongkou_boundary.get("complete_paper_visual_scan_claim_allowed")
            is not False
            or hongkou_boundary.get("district_face_claim_allowed") is not False
            or hongkou_boundary.get("second_mock_face_claim_allowed") is not False
            or hongkou_boundary.get("official_identity_claim_allowed") is not False
            or hongkou_boundary.get("answer_independently_verified") is not False
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_hongkou_theme_boundary_invalid",
                "Hongkou theme-four identity or answer boundary drifted",
            )

        papers: list[dict[str, Any]] = []
        for paper_id in sorted(grouped, key=lambda value: paper_source_order[value]):
            paper_row = master.master_nodes[("paper", paper_id)]
            title = _master_label("paper", paper_row)
            if not isinstance(title, str):
                identity = paper_row.get("source_identity")
                title = (
                    identity.get("article_title")
                    if isinstance(identity, dict)
                    and isinstance(identity.get("article_title"), str)
                    else None
                )
            recall = paper_id == recall_paper_id
            pudong_theme_only = paper_id == pudong_paper_id
            jiading_theme_only = paper_id == jiading_paper_id
            huangpu_theme_only = paper_id == huangpu_paper_id
            qibao_theme_only = paper_id == qibao_paper_id
            hongkou_theme_only = paper_id == hongkou_paper_id
            if recall and title is None:
                boundary = recall_snapshot.paper_identity_boundary
                title = (
                    boundary.get("paper_face_title_literal")
                    if isinstance(boundary, dict)
                    and isinstance(boundary.get("paper_face_title_literal"), str)
                    else None
                )
            if pudong_theme_only:
                paper_face = pudong_boundary.get("paper_face_title_literal")
                cohort = pudong_boundary.get("article_cohort_label")
                if not isinstance(paper_face, str) or not isinstance(cohort, str):
                    raise ThemeWorkbenchError(
                        "theme_workbench_pudong_theme_boundary_invalid",
                        "Pudong paper identity display values are unavailable",
                    )
                title = f"{paper_face} / {cohort}"
            if jiading_theme_only:
                paper_face = jiading_boundary.get("paper_face_title_literal")
                article_title = jiading_boundary.get("article_title_literal")
                if not isinstance(paper_face, str) or not isinstance(
                    article_title, str
                ):
                    raise ThemeWorkbenchError(
                        "theme_workbench_jiading_theme_boundary_invalid",
                        "Jiading paper identity display values are unavailable",
                    )
                title = f"{paper_face} / 公众号标题归类：{article_title}"
            if huangpu_theme_only:
                paper_face = huangpu_boundary.get("paper_face_title_literal")
                if not isinstance(paper_face, str):
                    raise ThemeWorkbenchError(
                        "theme_workbench_huangpu_theme_boundary_invalid",
                        "Huangpu paper-face identity is unavailable",
                    )
                title = paper_face
            if qibao_theme_only:
                paper_face = qibao_boundary.get("paper_face_title_literal")
                if not isinstance(paper_face, str):
                    raise ThemeWorkbenchError(
                        "theme_workbench_qibao_theme_boundary_invalid",
                        "Qibao paper-face identity is unavailable",
                    )
                title = paper_face
            if hongkou_theme_only:
                paper_face = hongkou_boundary.get("paper_face_title_literal")
                article_title = hongkou_boundary.get("article_title_literal")
                if not isinstance(paper_face, str) or not isinstance(
                    article_title, str
                ):
                    raise ThemeWorkbenchError(
                        "theme_workbench_hongkou_theme_boundary_invalid",
                        "Hongkou paper/article identity boundary is unavailable",
                    )
                title = f"{paper_face} / 公众号标题归类：{article_title}"
            source_metadata = _master_source_metadata(paper_row)
            if qibao_theme_only:
                source_metadata = {
                    "source_tier": source_metadata["source_tier"],
                    "year": "2025",
                    "region": "上海市七宝中学（卷面直接身份）",
                    "paper_type": "高三年级开学练习",
                    "attribution_status": "paper_face_pixels",
                }
            if hongkou_theme_only:
                source_metadata = {
                    "source_tier": source_metadata["source_tier"],
                    "year": "2026",
                    "region": "虹口区（公众号标题；卷面未署地区）",
                    "paper_type": "二模（公众号标题；卷面未署二模）",
                    "attribution_status": "article_metadata_not_paper_identity",
                }
            paper = {
                "id": paper_id,
                "title": title,
                "source_metadata": source_metadata,
                "order": _known_int(_master_order("paper", paper_row)),
                "order_status": (
                    "known_explicit"
                    if _known_int(_master_order("paper", paper_row)) is not None
                    else "unknown_source_order_preserved"
                ),
                "status": (
                    "observed_four_theme_recall_incomplete"
                    if recall
                    else (
                        "observed_theme_one_visual_scan_incomplete_paper"
                        if pudong_theme_only
                        else (
                            "observed_theme_one_article_classified_incomplete_paper"
                            if jiading_theme_only
                            else (
                                "observed_theme_four_visual_scan_incomplete_paper"
                                if (
                                    huangpu_theme_only
                                    or qibao_theme_only
                                    or hongkou_theme_only
                                )
                                else "complete_parent_chain_inventory"
                            )
                        )
                    )
                ),
                "observed_theme_count": len(grouped[paper_id]),
                "missing_theme_note_zh": (
                    "主题4题面缺失，不能称为五主题完整卷。"
                    if recall
                    else (
                        "当前只完成主题一“水合肼”11个最小作答单元的逐题扫描；"
                        "本记录不代表浦东一模整卷扫描完成。"
                        if pudong_theme_only
                        else (
                            "当前只完成主题一“消毒剂”10个最小作答单元的逐题扫描；"
                            "“嘉定区、2025届、二模”仅来自非官方公众号标题，"
                            "卷面未署地区或“二模”，本记录也不代表整卷扫描完成。"
                            if jiading_theme_only
                            else (
                                "当前只完成主题四“碘酸钙的制备”11个最小作答单元的逐题扫描；"
                                "年份、黄浦区和二模来自卷面直接身份，"
                                "本记录不代表整卷扫描完成。"
                                if huangpu_theme_only
                                else (
                                    "当前只完成主题四“电解质溶液及废水处理”10个最小作答单元的逐题扫描；"
                                    "学校、学年、学期与开学练习来自卷面直接身份，"
                                    "本记录不代表整卷扫描完成。"
                                    if qibao_theme_only
                                    else (
                                        "当前只完成主题四“电镀污泥中镍的回收与测定”9个最小作答单元的逐题扫描；"
                                        "卷面只确认高三、化学、2026.4、60分钟与100分，"
                                        "虹口区和二模仅来自非官方公众号标题；本记录不代表整卷扫描完成。"
                                        if hongkou_theme_only
                                        else None
                                    )
                                )
                            )
                        )
                    )
                ),
            }
            theme_groups: list[dict[str, Any]] = []
            for theme_id, entries in grouped[paper_id].items():
                theme_row = master.master_nodes[("theme_big_question", theme_id)]
                entries.sort(key=lambda entry: positions[entry["atomic_part_id"]][1])
                pages = set().union(*(entry["_pages"] for entry in entries))
                pages |= _page_span_from_value(theme_row.get("theme_page_span"))
                shared_context = _material_context(
                    entries, theme_row.get("theme_context")
                )
                sequence = _known_int(_master_order("theme_big_question", theme_row))
                theme_groups.append(
                    {
                        "paper": deepcopy(paper),
                        "theme": {
                            "id": theme_id,
                            "title": _master_label("theme_big_question", theme_row),
                            "sequence": sequence,
                            "sequence_status": (
                                "known_explicit"
                                if sequence is not None
                                else "unknown_pending_review"
                            ),
                            "page_span": _page_span_from_pages(
                                pages, "explicit_evidence_union"
                            ),
                            "parent_chain_status": "complete",
                        },
                        "counts": _counts(entries),
                        "shared_context": shared_context,
                        "dependencies": _dependency_counts(entries),
                        "atomic_chain": [_strip_internal(entry) for entry in entries],
                    }
                )
            known_sequences = [
                group["theme"]["sequence"]
                for group in theme_groups
                if type(group["theme"]["sequence"]) is int
            ]
            if len(known_sequences) != len(set(known_sequences)):
                raise ThemeWorkbenchError(
                    "theme_workbench_order_invalid",
                    "Master theme explicit order is duplicated within a paper",
                )
            theme_groups.sort(
                key=lambda group: (
                    group["theme"]["sequence"] is None,
                    group["theme"]["sequence"] or 0,
                )
            )
            papers.append({"paper": paper, "theme_groups": theme_groups})

        # The incomplete rows are never assigned to a guessed paper/theme even
        # when their IDs or source filenames appear suggestive.
        unassigned_ids = {atom["atomic_part_id"] for atom in incomplete_atoms}
        if {entry["atomic_part_id"] for entry in unassigned_entries} != unassigned_ids:
            raise ThemeWorkbenchError(
                "theme_workbench_parent_chain_invalid",
                "Master unassigned rows do not exactly match incomplete chains",
            )
        result = self._finalize(
            scope=MASTER_SCOPE,
            papers=papers,
            unassigned_entries=unassigned_entries,
            expected=MASTER_EXPECTED,
        )
        candidate_overlay = self.parent_chain_overlay.overlay()
        overlay_ids = {
            entry["atomic_part_id"]
            for paper in candidate_overlay["paper_groups"]
            for group in paper["theme_groups"]
            for entry in group["atomic_chain"]
        }
        if (
            overlay_ids != unassigned_ids
            or candidate_overlay["counts"]["source_master_atomics"]
            != MASTER_EXPECTED["unassigned_atomic_parts"]
            or candidate_overlay["central_master"]
            != {
                "atomic_total": 470,
                "complete_parent_chain_atomics": 427,
                "original_pending_atomics": 43,
                "candidate_overlay_atomics": 43,
                "applied": False,
                "human_confirmed": False,
                "denominator_unchanged": True,
            }
        ):
            raise ThemeWorkbenchError(
                "theme_workbench_parent_repair_overlay_mismatch",
                "candidate parent-chain overlay does not exactly cover Master pending rows",
            )
        result["candidate_parent_chain_overlay"] = candidate_overlay
        _reject_unsafe_projection(result)
        return result

    @staticmethod
    def _top_counts(
        papers: list[dict[str, Any]], unassigned: list[dict[str, Any]]
    ) -> dict[str, int]:
        theme_groups = [group for paper in papers for group in paper["theme_groups"]]
        entries = [
            entry for group in theme_groups for entry in group["atomic_chain"]
        ] + unassigned
        projected = _counts(entries)
        return {
            "papers": len(papers),
            "theme_groups": len(theme_groups),
            "atomic_parts": len(entries),
            "display_atomic_units": projected["display_atomic_units"],
            "unassigned_atomic_parts": len(unassigned),
            "visual_scanned": projected["visual_scanned"],
            "unscanned": projected["unscanned"],
            "label_complete": projected["label_complete"],
            "label_pending": projected["label_pending"],
            "answer_aligned": projected["answer_aligned"],
            "answer_unaligned": projected["answer_unaligned"],
            "answer_absent": projected["answer_absent"],
            "quality_notes": projected["quality_notes"],
        }

    def _finalize(
        self,
        *,
        scope: str,
        papers: list[dict[str, Any]],
        unassigned_entries: list[dict[str, Any]],
        expected: dict[str, int],
    ) -> dict[str, Any]:
        for entry in unassigned_entries:
            _strip_internal(entry)
        counts = self._top_counts(papers, unassigned_entries)
        if any(counts[key] != value for key, value in expected.items()):
            raise ThemeWorkbenchError(
                "theme_workbench_scope_count_mismatch",
                "theme workbench fixed scope counts drifted",
            )
        atomic_ids = [
            entry["atomic_part_id"]
            for paper in papers
            for group in paper["theme_groups"]
            for entry in group["atomic_chain"]
        ] + [entry["atomic_part_id"] for entry in unassigned_entries]
        if len(atomic_ids) != len(set(atomic_ids)):
            raise ThemeWorkbenchError(
                "theme_workbench_duplicate_atomic",
                "theme workbench duplicates an atomic identity",
            )
        response = {
            "schema_version": SCHEMA_VERSION,
            "scope": scope,
            "counts": counts,
            "papers": papers,
            "unassigned_pending_review": {
                "status": (
                    "missing_parent_pending_review" if unassigned_entries else "none"
                ),
                "count": len(unassigned_entries),
                "reason_zh": (
                    "缺失精确父链的atomic统一待复核；未按题号、文件名或ID猜测主题。"
                    if unassigned_entries
                    else None
                ),
                "atomic_chain": unassigned_entries,
            },
            "authority": dict(AUTHORITY),
            "integrity": {
                "hash_verified_on_read": True,
                "semantic_invariants_verified_on_read": True,
                "complete_scope_coverage": True,
                "no_duplicate_atomic_parts": True,
                "explicit_order_only": True,
                "dependency_edges_validated": True,
                "answer_text_excluded": True,
                "pixel_reuse_allowed": False,
                "fail_closed": True,
            },
        }
        _reject_unsafe_projection(response)
        return response

    def groups(self, scope: str) -> dict[str, Any]:
        if scope not in ALLOWED_SCOPES:
            raise ThemeWorkbenchError(
                "theme_workbench_scope_invalid",
                "scope must be exactly wave1 or master",
                400,
            )
        try:
            return self._wave() if scope == WAVE1_SCOPE else self._master()
        except ThemeWorkbenchError:
            raise
        except (
            CandidateReviewError,
            MasterWave1WorkbenchError,
            MasterDirectVisualScanError,
            MasterParentChainRepairOverlayError,
            MasterVisualScanAliasError,
            QuestionVisualScanError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
        ) as exc:
            raise ThemeWorkbenchError(
                "theme_workbench_dependency_unavailable",
                "a required frozen theme-workbench dependency failed closed",
                409,
            ) from exc


__all__ = [
    "ALLOWED_SCOPES",
    "AUTHORITY",
    "MASTER_SCOPE",
    "SCHEMA_VERSION",
    "WAVE1_SCOPE",
    "ThemeWorkbenchError",
    "ThemeWorkbenchReader",
]
