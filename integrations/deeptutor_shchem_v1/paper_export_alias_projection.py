"""Fail-closed, job-local projections for paper export.

The Master catalog retains one Master atomic node as its parent-chain anchor
when visual evidence splits that printed response into multiple Wave1 atomic
units. Export must instead keep every independently answerable unit separate.
This module transforms only a deep copy of the catalog; it never mutates the
frozen readers, central hierarchy, scan records, answers, or review state.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

_CHINESE_THEME_PREFIX = re.compile(r"^\s*[一二三四五六七八九十百]+\s*[、．.]\s*")


class PaperExportAliasProjectionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message_zh: str,
        status: int = 409,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message_zh)
        self.code = code
        self.status = status
        self.details = dict(details or {})


@dataclass(frozen=True)
class AliasContentBinding:
    """Route one projected identity to the immutable content reader."""

    output_atomic_id: str
    source_scope: str
    source_node_id: str
    source_unit_node_id: str | None
    master_parent_atomic_id: str
    master_printed_question_id: str
    master_printed_question_number: str | None


def project_direct_unit_scan(
    scan: Mapping[str, Any], source_atomic_id: str
) -> dict[str, Any]:
    """Select one explicit unit from a grouped Master-direct scan.

    The parent scan owns the crop bytes, while the selected unit owns its
    prompt, taxonomy, dependency, difficulty and aligned reference answer.
    No first-unit or positional fallback is permitted.
    """

    units = scan.get("minimal_atomic_units")
    if not isinstance(units, list):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_units_missing",
            "Master 直扫记录缺少显式最小作答单元。",
        )
    matches = []
    for raw_unit in units:
        if not isinstance(raw_unit, Mapping):
            continue
        hierarchy = raw_unit.get("hierarchy")
        hierarchy = hierarchy if isinstance(hierarchy, Mapping) else {}
        unit_id = raw_unit.get("atomic_part_id") or hierarchy.get("atomic_part_id")
        if unit_id == source_atomic_id:
            matches.append(raw_unit)
    if len(matches) != 1:
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_ambiguous",
            "Master 直扫最小作答单元缺失或不唯一。",
            details={"source_atomic_id": source_atomic_id},
        )
    unit = deepcopy(dict(matches[0]))
    prompt = unit.get("prompt")
    prompt = prompt if isinstance(prompt, Mapping) else {}
    classification = unit.get("classification")
    if not isinstance(classification, Mapping):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_invalid", "Master 直扫分项缺少分类记录。"
        )
    difficulty = unit.get("cognitive_difficulty") or unit.get("difficulty")
    if not isinstance(difficulty, Mapping):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_invalid", "Master 直扫分项缺少难度证据。"
        )
    raw_dependency = unit.get("dependency")
    if not isinstance(raw_dependency, Mapping):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_invalid", "Master 直扫分项缺少依赖记录。"
        )
    prior = raw_dependency.get("prior_atomic_part_ids")
    if not isinstance(prior, list) or any(not isinstance(item, str) for item in prior):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_invalid", "Master 直扫分项依赖格式不正确。"
        )
    shared_crop_ids = [
        row["crop_id"]
        for row in scan.get("evidence_descriptors", [])
        if isinstance(row, Mapping)
        and row.get("evidence_role") == "shared_material"
        and isinstance(row.get("crop_id"), str)
    ]
    raw_answer = unit.get("answer")
    raw_answer = raw_answer if isinstance(raw_answer, Mapping) else {}
    answer_boundary = unit.get("answer_boundary")
    answer_boundary = (
        answer_boundary if isinstance(answer_boundary, Mapping) else raw_answer
    )
    raw_reference = unit.get("reference_answer")
    raw_reference = raw_reference if isinstance(raw_reference, Mapping) else raw_answer
    availability = answer_boundary.get("availability")
    answer_text = raw_reference.get("reference_answer_text")
    if answer_text is None:
        answer_text = raw_reference.get("reference_summary_zh")
    authority = answer_boundary.get("authority") or raw_reference.get("authority")
    if availability == "present_part_aligned" and (
        not isinstance(answer_text, str)
        or not answer_text.strip()
        or not isinstance(authority, str)
        or not authority
    ):
        raise PaperExportAliasProjectionError(
            "paper_export_direct_unit_answer_invalid",
            "Master 直扫分项答案未按作答单元精确对齐。",
            details={"source_atomic_id": source_atomic_id},
        )
    projected = deepcopy(dict(scan))
    projected.pop("minimal_atomic_units", None)
    for parent_only in (
        "candidate_analysis",
        "model_candidate_analysis",
        "risks_and_limits",
    ):
        projected.pop(parent_only, None)
    for unit_owned in (
        "candidate_analysis",
        "model_candidate_analysis",
        "risks_and_limits",
    ):
        if isinstance(unit.get(unit_owned), Mapping):
            projected[unit_owned] = deepcopy(dict(unit[unit_owned]))
    projected.update(
        {
            "source_parent_atomic_id": scan.get("master_node_id"),
            "source_unit_atomic_id": source_atomic_id,
            "visible_summary_zh": unit.get("visible_summary_zh")
            or prompt.get("normalized_text_zh"),
            "response_requirement_zh": unit.get("response_requirement_zh")
            or prompt.get("normalized_text_zh"),
            "scan_hierarchy": deepcopy(unit.get("hierarchy", {})),
            "scan_classification": deepcopy(dict(classification)),
            "cognitive_difficulty": deepcopy(dict(difficulty)),
            "dependency": {
                "kind": (
                    "multiple_prior_parts"
                    if len(prior) > 1
                    else "one_prior_part"
                    if prior
                    else "shared_material_only"
                    if raw_dependency.get("shared_material_id") or shared_crop_ids
                    else "independent"
                ),
                "prior_atomic_part_ids": list(prior),
                "shared_material_crop_ids": shared_crop_ids,
                "explicit_prior_edge_count": len(prior),
                "status": "validated_explicit",
            },
            "reference_answer": {
                "availability": availability or "absent",
                "reference_answer_text": (
                    answer_text.strip() if isinstance(answer_text, str) else None
                ),
                "source_authority": authority or "none",
                "independently_verified": bool(
                    answer_boundary.get("independently_verified", False)
                ),
            },
            "quality_notes": deepcopy(unit.get("quality_notes", [])),
        }
    )
    return projected


@dataclass(frozen=True)
class PaperExportAliasProjection:
    catalog: dict[str, Any]
    selections: tuple[dict[str, Any], ...]
    content_bindings: Mapping[str, AliasContentBinding]
    expanded_parent_count: int
    expanded_unit_count: int


def export_display_title(source_title: Any) -> tuple[Any, str | None]:
    """Remove only an explicit Chinese theme-number prefix.

    Arabic-leading chemistry names such as ``1,2-二氯乙烷`` are deliberately
    outside the accepted prefix grammar and remain byte-for-byte unchanged.
    """

    if not isinstance(source_title, str):
        return source_title, None
    display = _CHINESE_THEME_PREFIX.sub("", source_title, count=1)
    if not display.strip():
        return source_title, source_title
    return display, source_title


def project_export_theme_titles(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve the source title while normalizing the numbered display title."""

    projected = deepcopy(dict(catalog))
    papers = projected.get("papers")
    if not isinstance(papers, list):
        return projected
    for paper_entry in papers:
        if not isinstance(paper_entry, Mapping):
            continue
        groups = paper_entry.get("theme_groups")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            theme = group.get("theme")
            if not isinstance(theme, dict):
                continue
            display, source = export_display_title(theme.get("title"))
            if source is not None:
                theme.setdefault("source_title_zh", source)
                theme["title"] = display
    return projected


def _required_id(value: Any, *, code: str, message_zh: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperExportAliasProjectionError(code, message_zh)
    return value


def _projected_atomic_id(parent_id: str, source_node_id: str) -> str:
    digest = hashlib.sha256(f"{parent_id}\0{source_node_id}".encode()).hexdigest()
    return f"EXPALIAS-{digest[:32]}"


def _validated_dependency(
    value: Any, *, parent_id: str, unit_id: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperExportAliasProjectionError(
            "paper_export_alias_dependency_missing",
            "拆分作答单元缺少显式依赖记录，不能按独立题处理。",
            details={"master_parent_atomic_id": parent_id, "atomic_part_id": unit_id},
        )
    kind = value.get("kind")
    prior = value.get("prior_atomic_part_ids")
    if not isinstance(kind, str) or not kind.strip() or not isinstance(prior, list):
        raise PaperExportAliasProjectionError(
            "paper_export_alias_dependency_invalid",
            "拆分作答单元的依赖记录格式不完整。",
            details={"master_parent_atomic_id": parent_id, "atomic_part_id": unit_id},
        )
    if any(not isinstance(item, str) or not item for item in prior) or len(
        prior
    ) != len(set(prior)):
        raise PaperExportAliasProjectionError(
            "paper_export_alias_dependency_invalid",
            "拆分作答单元的前序依赖标识不正确。",
            details={"master_parent_atomic_id": parent_id, "atomic_part_id": unit_id},
        )
    dependency = deepcopy(dict(value))
    dependency["kind"] = kind
    dependency["prior_atomic_part_ids"] = list(prior)
    dependency["explicit_prior_edge_count"] = len(prior)
    dependency.setdefault("status", "explicit_alias_unit_dependency")
    return dependency


def _project_group(
    group: dict[str, Any],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, AliasContentBinding],
    int,
    int,
]:
    rows = group.get("atomic_chain")
    if not isinstance(rows, list):
        raise PaperExportAliasProjectionError(
            "paper_export_alias_catalog_invalid", "主题作答单元列表格式不正确。"
        )
    original_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_catalog_invalid", "主题作答单元记录格式不正确。"
            )
        node_id = _required_id(
            row.get("atomic_part_id"),
            code="paper_export_alias_catalog_invalid",
            message_zh="主题作答单元缺少标识。",
        )
        if node_id in original_ids:
            raise PaperExportAliasProjectionError(
                "paper_export_alias_identity_conflict", "主题中存在重复作答单元标识。"
            )
        original_ids.add(node_id)

    parent_to_children: dict[str, tuple[str, ...]] = {}
    source_to_output: dict[str, str] = {}
    bindings: dict[str, AliasContentBinding] = {}
    projected_rows: list[dict[str, Any]] = []
    projected_ids: set[str] = set()
    expanded_parent_count = 0
    expanded_unit_count = 0
    for raw_row in rows:
        row = deepcopy(dict(raw_row))
        parent_id = str(row["atomic_part_id"])
        aliases = row.get("alias_units", [])
        if not isinstance(aliases, list):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_catalog_invalid",
                "作答单元的拆分映射格式不正确。",
                details={"master_parent_atomic_id": parent_id},
            )
        if not aliases:
            projected_rows.append(row)
            projected_ids.add(parent_id)
            continue

        printed_id = _required_id(
            row.get("printed_question_id"),
            code="paper_export_alias_parent_missing",
            message_zh="拆分作答单元缺少可核对的 Master 印刷题父节点。",
        )
        children: list[str] = []
        sequences: list[int] = []
        for raw_unit in aliases:
            if not isinstance(raw_unit, Mapping):
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_unit_invalid",
                    "拆分作答单元记录格式不正确。",
                    details={"master_parent_atomic_id": parent_id},
                )
            unit = deepcopy(dict(raw_unit))
            source_node_id = _required_id(
                unit.get("atomic_part_id"),
                code="paper_export_alias_unit_invalid",
                message_zh="拆分作答单元缺少明确标识。",
            )
            sequence = unit.get("atomic_sequence_in_printed")
            if type(sequence) is not int or sequence < 1:
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_order_unknown",
                    "拆分作答单元缺少明确的印刷题内顺序。",
                    details={
                        "master_parent_atomic_id": parent_id,
                        "atomic_part_id": source_node_id,
                    },
                )
            unit_id = _projected_atomic_id(parent_id, source_node_id)
            if (
                unit_id in projected_ids
                or unit_id in original_ids
                or unit_id in children
                or source_node_id in source_to_output
            ):
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_identity_conflict",
                    "拆分作答单元标识与主题中其他节点冲突。",
                    details={"atomic_part_id": source_node_id},
                )
            child = row.copy()
            child.update(unit)
            child.update(
                {
                    "atomic_part_id": unit_id,
                    "printed_question_id": printed_id,
                    "printed_question_number": row.get("printed_question_number"),
                    "printed_sequence": row.get("printed_sequence"),
                    "printed_sequence_status": row.get("printed_sequence_status"),
                    "atomic_sequence_in_printed": sequence,
                    "atomic_sequence_status": unit.get(
                        "atomic_sequence_status", "known_explicit_alias_unit"
                    ),
                    "dependency": _validated_dependency(
                        unit.get("dependency"),
                        parent_id=parent_id,
                        unit_id=source_node_id,
                    ),
                    "visual_coverage_kind": "alias_existing_visual_scan",
                    "alias_units": [],
                }
            )
            projected_rows.append(child)
            projected_ids.add(unit_id)
            children.append(unit_id)
            sequences.append(sequence)
            source_to_output[source_node_id] = unit_id
            source_scope = unit.get("source_scope", "wave1")
            source_parent_id = unit.get("source_parent_atomic_id")
            if source_scope not in {"wave1", "master"}:
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_route_invalid", "拆分作答单元读取范围不正确。"
                )
            if source_scope == "master":
                source_parent_id = _required_id(
                    source_parent_id,
                    code="paper_export_alias_route_invalid",
                    message_zh="Master 直扫分项缺少父节点读取标识。",
                )
            bindings[unit_id] = AliasContentBinding(
                output_atomic_id=unit_id,
                source_scope=source_scope,
                source_node_id=(
                    source_parent_id if source_scope == "master" else source_node_id
                ),
                source_unit_node_id=(
                    source_node_id if source_scope == "master" else None
                ),
                master_parent_atomic_id=parent_id,
                master_printed_question_id=printed_id,
                master_printed_question_number=(
                    str(row["printed_question_number"])
                    if row.get("printed_question_number") is not None
                    else None
                ),
            )
        if len(sequences) != len(set(sequences)) or sequences != sorted(sequences):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_order_invalid",
                "拆分作答单元的显式顺序重复或前后倒置。",
                details={"master_parent_atomic_id": parent_id},
            )
        parent_to_children[parent_id] = tuple(children)
        expanded_parent_count += 1
        expanded_unit_count += len(children)

    available = {str(row["atomic_part_id"]) for row in projected_rows}
    for row in projected_rows:
        dependency = row.get("dependency")
        if not isinstance(dependency, Mapping) or not isinstance(
            dependency.get("prior_atomic_part_ids"), list
        ):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_dependency_missing",
                "作答单元缺少显式依赖记录，不能完成拆分投影。",
                details={"atomic_part_id": row.get("atomic_part_id")},
            )
        rewritten: list[str] = []
        for prior_id in dependency["prior_atomic_part_ids"]:
            explicit_child = source_to_output.get(prior_id)
            children = parent_to_children.get(prior_id)
            if explicit_child is not None:
                target = explicit_child
            elif children is None:
                target = prior_id
            elif len(children) == 1:
                target = children[0]
            else:
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_dependency_ambiguous",
                    "前序依赖指向已拆成多个作答单元的父节点，缺少精确子单元映射。",
                    details={
                        "atomic_part_id": row.get("atomic_part_id"),
                        "ambiguous_parent_atomic_id": prior_id,
                        "candidate_child_ids": list(children),
                    },
                )
            if target not in available:
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_dependency_unresolved",
                    "拆分作答单元的前序依赖不在同一主题的明确节点中。",
                    details={
                        "atomic_part_id": row.get("atomic_part_id"),
                        "prior_atomic_part_id": target,
                    },
                )
            rewritten.append(target)
        if len(rewritten) != len(set(rewritten)):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_dependency_invalid",
                "拆分后前序依赖出现重复目标。",
                details={"atomic_part_id": row.get("atomic_part_id")},
            )
        row["dependency"] = deepcopy(dict(dependency))
        row["dependency"]["prior_atomic_part_ids"] = rewritten
        row["dependency"]["explicit_prior_edge_count"] = len(rewritten)

    # Parent-row position plus explicit child-list order is authoritative.
    # Never sort by identifier or parse an ID suffix.
    group["atomic_chain"] = projected_rows
    return (
        parent_to_children,
        source_to_output,
        bindings,
        expanded_parent_count,
        expanded_unit_count,
    )


def project_explicit_alias_units(
    catalog: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any]],
    *,
    scope: str,
) -> PaperExportAliasProjection:
    """Return an export-only catalog, routed selections, and content bindings.

    Whole-theme selection takes precedence. A partial 1:1 parent target maps to
    its child. A dependency selection of a 1:N parent expands to every explicit
    child. An atomic selection of a 1:N parent is rejected because child answers
    must never be merged into a fabricated single answer.
    """

    projected_catalog = project_export_theme_titles(catalog)
    projected_selections = [deepcopy(dict(item)) for item in selections]
    if scope != "master":
        return PaperExportAliasProjection(
            projected_catalog, tuple(projected_selections), {}, 0, 0
        )
    papers = projected_catalog.get("papers")
    if not isinstance(papers, list):
        raise PaperExportAliasProjectionError(
            "paper_export_alias_catalog_invalid", "Master 主题目录格式不正确。"
        )

    parent_targets: dict[tuple[str, str], tuple[str, ...]] = {}
    child_targets: dict[tuple[str, str], str] = {}
    bindings: dict[str, AliasContentBinding] = {}
    parent_count = unit_count = 0
    for paper_entry in papers:
        if not isinstance(paper_entry, Mapping) or not isinstance(
            paper_entry.get("theme_groups"), list
        ):
            raise PaperExportAliasProjectionError(
                "paper_export_alias_catalog_invalid",
                "Master 试卷或主题记录格式不正确。",
            )
        for group in paper_entry["theme_groups"]:
            if not isinstance(group, dict) or not isinstance(
                group.get("theme"), Mapping
            ):
                raise PaperExportAliasProjectionError(
                    "paper_export_alias_catalog_invalid", "Master 主题记录格式不正确。"
                )
            theme_id = _required_id(
                group["theme"].get("id"),
                code="paper_export_alias_catalog_invalid",
                message_zh="Master 主题缺少标识。",
            )
            (
                targets,
                source_targets,
                group_bindings,
                added_parents,
                added_units,
            ) = _project_group(group)
            for parent_id, children in targets.items():
                parent_targets[(theme_id, parent_id)] = children
            for source_node_id, output_id in source_targets.items():
                key = (theme_id, source_node_id)
                if key in child_targets:
                    raise PaperExportAliasProjectionError(
                        "paper_export_alias_identity_conflict",
                        "同一主题内的拆分来源标识重复。",
                        details={"atomic_part_id": source_node_id},
                    )
                child_targets[key] = output_id
            for child_id, binding in group_bindings.items():
                if child_id in bindings:
                    raise PaperExportAliasProjectionError(
                        "paper_export_alias_identity_conflict",
                        "拆分作答单元内容绑定重复。",
                        details={"atomic_part_id": child_id},
                    )
                bindings[child_id] = binding
            parent_count += added_parents
            unit_count += added_units

    whole_themes = {
        str(item.get("theme_id"))
        for item in projected_selections
        if item.get("selection_unit") == "theme"
    }
    routed: list[dict[str, Any]] = []
    for selection in projected_selections:
        theme_id = selection.get("theme_id")
        if (
            not isinstance(theme_id, str)
            or theme_id in whole_themes
            or selection.get("selection_unit") == "theme"
        ):
            routed.append(selection)
            continue
        target = selection.get("target_atomic_id")
        child_target = child_targets.get((theme_id, str(target)))
        if child_target is not None:
            mapped = deepcopy(selection)
            mapped["target_atomic_id"] = child_target
            routed.append(mapped)
            continue
        children = parent_targets.get((theme_id, str(target)))
        if children is None:
            routed.append(selection)
        elif len(children) == 1:
            mapped = deepcopy(selection)
            mapped["target_atomic_id"] = children[0]
            routed.append(mapped)
        elif selection.get("selection_unit") == "dependency":
            for child_id in children:
                mapped = deepcopy(selection)
                mapped["target_atomic_id"] = child_id
                routed.append(mapped)
        else:
            raise PaperExportAliasProjectionError(
                "paper_export_alias_partial_ambiguous",
                "所选 Master 父节点已拆成多个作答单元，不能作为单一小题加入。",
                details={
                    "theme_id": theme_id,
                    "master_parent_atomic_id": target,
                    "candidate_child_ids": list(children),
                },
            )
    if len(routed) > 100:
        raise PaperExportAliasProjectionError(
            "paper_export_alias_selection_limit_exceeded",
            "拆分作答单元展开后题篮条目超过 100 个，请缩小题篮。",
            status=400,
            details={"expanded_selection_count": len(routed)},
        )
    return PaperExportAliasProjection(
        projected_catalog,
        tuple(routed),
        bindings,
        parent_count,
        unit_count,
    )


__all__ = [
    "AliasContentBinding",
    "PaperExportAliasProjection",
    "PaperExportAliasProjectionError",
    "export_display_title",
    "project_explicit_alias_units",
    "project_direct_unit_scan",
    "project_export_theme_titles",
]
