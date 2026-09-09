from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .datong_answer_bindings import EXISTING_ANSWER_AREA_NODE_IDS
from .datong_crop_revision import visible_evidence
from .paper_export_alias_projection import (
    AliasContentBinding,
    PaperExportAliasProjection,
    PaperExportAliasProjectionError,
    project_direct_unit_scan,
    project_explicit_alias_units,
    project_export_theme_titles,
)
from .paper_export_renderer import (
    ARTIFACT_FILENAMES,
    RENDER_BUNDLE_KIND,
    RENDERER_SCHEMA_VERSION,
    PaperExportRendererError,
    RendererToolchain,
    render_export_bundle,
)
from .paper_format_presets import (
    PaperFormatContractError,
    build_assembly_blueprint,
    build_document_plans,
    build_render_request,
    default_shanghai_theme_preset,
    run_export_preflight,
)
from .supplemental_answers import validate_supplemental_answer

PAPER_EXPORT_JOB_SCHEMA_VERSION = "shchem.paper-export-workbench-job.v1"
_JOB_ID = re.compile(r"^WBEXP-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_IDS = frozenset(ARTIFACT_FILENAMES)
_SCOPES = frozenset({"wave1", "master", "supplemental"})
_SELECTION_UNITS = frozenset({"theme", "dependency", "atomic"})
_MAX_EXPORT_THEME_COUNT = 20
_MAX_EXPORT_TOTAL_SCORE = 500.0


class PaperExportWorkbenchError(RuntimeError):
    def __init__(
        self,
        code: str,
        message_zh: str,
        status: int = 400,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message_zh)
        self.code = code
        self.status = status
        self.details = dict(details or {})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clean_text(
    value: Any, *, field: str, limit: int, required: bool = True
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", f"{field}格式不正确。"
        )
    return value.strip()


def _validated_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "title_zh",
        "subtitle_zh",
        "duration_minutes",
        "numbering_mode",
        "score_per_atomic",
        "answer_space_lines",
        "show_question_scores",
        "atomic_settings",
        "selections",
    }
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "导出请求含未知字段。"
        )
    title = _clean_text(payload.get("title_zh"), field="试卷标题", limit=120)
    subtitle = _clean_text(
        payload.get("subtitle_zh"), field="副标题", limit=180, required=False
    )
    duration = payload.get("duration_minutes")
    if type(duration) is not int or not 1 <= duration <= 300:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "练习时长须为 1—300 分钟的整数。"
        )
    numbering = payload.get("numbering_mode", "continuous_across_paper")
    if numbering not in {"continuous_across_paper", "restart_within_each_theme"}:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "小题编号方式不正确。"
        )
    score = payload.get("score_per_atomic", 1)
    if type(score) is not int or not 1 <= score <= 20:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "每个作答单元分值须为 1—20 的整数。"
        )
    answer_lines = payload.get("answer_space_lines", 3)
    if type(answer_lines) is not int or not 0 <= answer_lines <= 20:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "默认作答行数须为 0—20 的整数。"
        )
    raw_atomic_settings = payload.get("atomic_settings")
    show_question_scores = payload.get("show_question_scores", False)
    if type(show_question_scores) is not bool:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "题面分数显示选项必须为开启或关闭。"
        )
    atomic_settings: dict[str, dict[str, int]] | None = None
    if raw_atomic_settings is not None:
        if not isinstance(raw_atomic_settings, Mapping):
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "逐题分值与答题空间设置格式不正确。"
            )
        atomic_settings = {}
        for atomic_id, setting in raw_atomic_settings.items():
            if (
                not isinstance(atomic_id, str)
                or not atomic_id.strip()
                or atomic_id != atomic_id.strip()
                or len(atomic_id) > 240
                or not isinstance(setting, Mapping)
                or set(setting) != {"score", "answer_space_lines"}
            ):
                raise PaperExportWorkbenchError(
                    "paper_export_request_invalid", "逐题设置的题目标识或字段不正确。"
                )
            atomic_score = setting.get("score")
            atomic_lines = setting.get("answer_space_lines")
            if type(atomic_score) is not int or not 1 <= atomic_score <= 30:
                raise PaperExportWorkbenchError(
                    "paper_export_request_invalid", "逐题分值须为 1—30 的整数。"
                )
            if type(atomic_lines) is not int or not 0 <= atomic_lines <= 20:
                raise PaperExportWorkbenchError(
                    "paper_export_request_invalid", "逐题答题空间须为 0—20 行的整数。"
                )
            atomic_settings[atomic_id] = {
                "score": atomic_score,
                "answer_space_lines": atomic_lines,
            }
    raw_selections = payload.get("selections")
    if not isinstance(raw_selections, list) or not 1 <= len(raw_selections) <= 100:
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "题篮须包含 1—100 个条目。"
        )
    selections: list[dict[str, Any]] = []
    scopes: set[str] = set()
    snapshots: set[str] = set()
    for value in raw_selections:
        if not isinstance(value, Mapping) or set(value) != {
            "scope",
            "selection_unit",
            "theme_id",
            "target_atomic_id",
            "expected_data_snapshot_id",
        }:
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "题篮条目字段不完整。"
            )
        scope = value.get("scope")
        unit = value.get("selection_unit")
        theme_id = value.get("theme_id")
        target = value.get("target_atomic_id")
        snapshot = value.get("expected_data_snapshot_id")
        if scope not in _SCOPES or unit not in _SELECTION_UNITS:
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "题篮范围或加入方式不正确。"
            )
        if not isinstance(theme_id, str) or not theme_id.strip() or len(theme_id) > 240:
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "题篮主题标识不正确。"
            )
        if unit == "theme":
            if target not in {None, ""}:
                raise PaperExportWorkbenchError(
                    "paper_export_request_invalid", "加入整主题时不能同时指定单题。"
                )
            target = None
        elif not isinstance(target, str) or not target.strip() or len(target) > 240:
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "题篮单题标识不正确。"
            )
        if not isinstance(snapshot, str) or not _SHA256.fullmatch(snapshot):
            raise PaperExportWorkbenchError(
                "paper_export_request_invalid", "题篮数据快照标识不正确。"
            )
        scopes.add(scope)
        snapshots.add(snapshot)
        selections.append(
            {
                "scope": scope,
                "selection_unit": unit,
                "theme_id": theme_id.strip(),
                "target_atomic_id": target.strip() if isinstance(target, str) else None,
                "expected_data_snapshot_id": snapshot,
            }
        )
    if len(scopes) != 1:
        raise PaperExportWorkbenchError(
            "multiple_scopes_require_dedup_review",
            "一次导出只能使用同一个题库范围；请分开导出，避免同题重复。",
            409,
        )
    if len(snapshots) != 1:
        raise PaperExportWorkbenchError(
            "theme_snapshot_stale", "题篮混用了不同题库版本，请刷新后重试。", 409
        )
    return {
        "title_zh": title,
        "subtitle_zh": subtitle,
        "duration_minutes": duration,
        "numbering_mode": numbering,
        "score_per_atomic": float(score),
        "answer_space_lines": answer_lines,
        "show_question_scores": show_question_scores,
        "atomic_settings": atomic_settings,
        "selections": selections,
        "scope": next(iter(scopes)),
        "data_snapshot_id": next(iter(snapshots)),
    }


def _atomic_settings_for_final_ids(
    request: Mapping[str, Any], final_ids: set[str]
) -> Mapping[str, Mapping[str, int]] | None:
    settings = request.get("atomic_settings")
    if settings is None:
        return None
    if not isinstance(settings, Mapping):
        raise PaperExportWorkbenchError(
            "paper_export_request_invalid", "逐题分值与答题空间设置格式不正确。"
        )
    setting_ids = set(settings)
    missing = sorted(final_ids - setting_ids)
    extra = sorted(setting_ids - final_ids)
    if missing or extra:
        raise PaperExportWorkbenchError(
            "paper_export_atomic_settings_mismatch",
            "逐题设置必须与展开依赖后的最终作答单元完全一致。",
            409,
            details={"missing_atomic_ids": missing, "extra_atomic_ids": extra},
        )
    return settings


def _total_score_for_final_ids(
    request: Mapping[str, Any], final_ids: set[str]
) -> float:
    settings = _atomic_settings_for_final_ids(request, final_ids)
    if settings is None:
        return len(final_ids) * float(request["score_per_atomic"])
    return float(sum(int(settings[atomic_id]["score"]) for atomic_id in final_ids))


def _set_template_value(field: dict[str, Any], value: Any, note_zh: str) -> None:
    field["value"] = value
    field["verification"] = {
        "status": "project_template_value",
        "evidence_refs": [],
        "verified_for_paper_id": None,
        "note_zh": note_zh,
    }


def _preset_for_request(
    request: Mapping[str, Any], *, theme_count: int, total_score: float
) -> dict[str, Any]:
    preset = default_shanghai_theme_preset()
    show_scores = request.get("show_question_scores", False)
    preset["student_version"]["show_item_scores"] = show_scores
    preset["student_version"]["show_total_score"] = show_scores
    _set_template_value(
        preset["structure"]["subquestion_numbering"],
        request["numbering_mode"],
        "教师本次导出选择；不是永久官方卷式声明。",
    )
    _set_template_value(
        preset["per_paper"]["theme_count"],
        theme_count,
        "由当前题篮完整主题数自动计算。",
    )
    _set_template_value(
        preset["per_paper"]["total_score"],
        int(total_score) if float(total_score).is_integer() else total_score,
        "由当前题篮作答单元与教师设置的单元分值自动计算。",
    )
    _set_template_value(
        preset["per_paper"]["duration_minutes"],
        request["duration_minutes"],
        "教师本次练习设置；不是来源试卷时长声明。",
    )
    _set_template_value(
        preset["per_paper"]["scoring_rules"],
        {
            "selection_rule_zh": "选择小问嵌入主题大题，按题面要求作答。",
            "partial_credit_rule_zh": (
                "各题分值见题旁标注，多空题按标注的小项计分。"
                if show_scores else "评分分值见教师版答案。"
            ),
            "other_rule_zh": "计算题写出必要步骤，化学方程式注明条件。",
        },
        "本地备课导出规则；不冒充来源卷官方评分细则。",
    )
    preset["student_version"]["answer_space"]["fallback_lines"] = request[
        "answer_space_lines"
    ]
    return preset


def _prepare_catalog(
    value: Mapping[str, Any], *, scope: str, snapshot_id: str
) -> dict[str, Any]:
    catalog = deepcopy(dict(value))
    if catalog.get("scope") != scope or not isinstance(catalog.get("papers"), list):
        raise PaperExportWorkbenchError(
            "theme_snapshot_invalid", "当前题库没有可装配的完整主题目录。", 409
        )
    supplied_snapshot = catalog.pop("data_snapshot_id", None)
    if supplied_snapshot is not None and supplied_snapshot != snapshot_id:
        raise PaperExportWorkbenchError(
            "theme_snapshot_stale", "当前题库版本已经变化，请刷新题篮。", 409
        )
    if supplied_snapshot is None:
        observed_snapshot = _sha256_bytes(_canonical_json_bytes(value))
        if observed_snapshot != snapshot_id:
            raise PaperExportWorkbenchError(
                "theme_snapshot_stale", "当前题库内容已经变化，请刷新题篮。", 409
            )
    integrity = catalog.get("integrity")
    if not isinstance(integrity, Mapping) or any(
        integrity.get(key) is not True
        for key in (
            "hash_verified_on_read",
            "semantic_invariants_verified_on_read",
            "complete_scope_coverage",
            "no_duplicate_atomic_parts",
            "explicit_order_only",
            "dependency_edges_validated",
            "fail_closed",
        )
    ):
        raise PaperExportWorkbenchError(
            "theme_snapshot_integrity_blocked", "当前主题目录完整性检查未通过。", 409
        )

    # The renderer adds its own Chinese theme ordinal. Preserve the exact source
    # title separately and remove only an explicit Chinese-number prefix from
    # the display title. Arabic-leading chemistry names remain untouched.
    catalog = project_export_theme_titles(catalog)

    # Master keeps some single-part sequence fields as unknown even though its
    # atomic_chain is already an explicitly ordered projection.  For export we
    # derive only the within-printed coordinate from that stable list.  This is
    # job-local and never mutates or upgrades the source classification record.
    for paper_entry in catalog["papers"]:
        if not isinstance(paper_entry, dict):
            continue
        for group in paper_entry.get("theme_groups", []):
            if not isinstance(group, dict) or not isinstance(
                group.get("atomic_chain"), list
            ):
                continue
            positions: dict[str, int] = {}
            for row in group["atomic_chain"]:
                if not isinstance(row, dict):
                    continue
                printed_id = row.get("printed_question_id")
                if not isinstance(printed_id, str):
                    continue
                positions[printed_id] = positions.get(printed_id, 0) + 1
                if not isinstance(row.get("atomic_sequence_in_printed"), int):
                    row["atomic_sequence_in_printed"] = positions[printed_id]
                    row["atomic_sequence_status"] = "export_projection_source_order"
    return {
        "data_snapshot_id": snapshot_id,
        "scope": scope,
        "catalog": catalog,
    }


def _alias_projection(
    catalog: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any]],
    *,
    scope: str,
) -> PaperExportAliasProjection:
    try:
        return project_explicit_alias_units(catalog, selections, scope=scope)
    except PaperExportAliasProjectionError as exc:
        raise PaperExportWorkbenchError(
            exc.code, str(exc), exc.status, details=exc.details
        ) from exc


def _theme_rows(
    catalog: Mapping[str, Any],
    theme_ids: set[str],
    *,
    content_bindings: Mapping[str, AliasContentBinding] | None = None,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    found: set[str] = set()
    for paper_entry in catalog.get("papers", []):
        paper = paper_entry.get("paper", {})
        paper_id = paper.get("id") if isinstance(paper, Mapping) else None
        paper_title = paper.get("title") if isinstance(paper, Mapping) else None
        source_metadata = (
            deepcopy(paper.get("source_metadata"))
            if isinstance(paper, Mapping)
            and isinstance(paper.get("source_metadata"), Mapping)
            else {}
        )
        for group in paper_entry.get("theme_groups", []):
            theme = group.get("theme", {})
            theme_id = theme.get("id")
            if theme_id in theme_ids:
                found.add(theme_id)
                for row in group.get("atomic_chain", []):
                    if not isinstance(row, Mapping):
                        continue
                    projected = dict(row)
                    # These fields are private to the export resolver.  The public
                    # theme rows intentionally omit repeated parent IDs, but a crop
                    # name alone is not globally unique across papers/themes.
                    projected["_export_paper_id"] = paper_id
                    projected["_export_paper_title"] = paper_title
                    projected["_export_source_metadata"] = deepcopy(source_metadata)
                    projected["_export_theme_id"] = theme_id
                    binding = (content_bindings or {}).get(
                        str(projected.get("atomic_part_id"))
                    )
                    if binding is not None:
                        projected["_export_content_scope"] = binding.source_scope
                        projected["_export_content_node_id"] = binding.source_node_id
                        if binding.source_unit_node_id is not None:
                            projected["_export_content_unit_node_id"] = (
                                binding.source_unit_node_id
                            )
                        projected["_export_alias_parent_atomic_id"] = (
                            binding.master_parent_atomic_id
                        )
                    rows.append(projected)
    missing = sorted(theme_ids - found)
    if missing:
        raise PaperExportWorkbenchError(
            "theme_not_found",
            "当前题库版本找不到题篮中的完整主题。",
            409,
            details={"missing_theme_ids": missing},
        )
    return rows


def _enforce_prequeue_expanded_limits(
    request: Mapping[str, Any],
    *,
    theme_catalog_loader: Callable[[str], Mapping[str, Any]],
) -> None:
    """Reject oversized valid baskets before they enter the background queue.

    Catalog, snapshot, dependency, and blueprint failures retain the existing
    asynchronous persisted-failure path. This synchronous pass is deliberately
    limited to the two resource limits that require server-side expansion.
    """

    theme_ids = {
        selection["theme_id"]
        for selection in request["selections"]
        if isinstance(selection, Mapping) and isinstance(selection.get("theme_id"), str)
    }
    theme_count = len(theme_ids)
    if theme_count > _MAX_EXPORT_THEME_COUNT:
        raise PaperExportWorkbenchError(
            "paper_export_theme_limit_exceeded",
            f"一次最多导出 {_MAX_EXPORT_THEME_COUNT} 个主题大题，请缩小题篮后重试。",
            400,
            details={
                "observed_theme_count": theme_count,
                "maximum_theme_count": _MAX_EXPORT_THEME_COUNT,
            },
        )

    try:
        raw_catalog = theme_catalog_loader(str(request["scope"]))
        wrapper = _prepare_catalog(
            raw_catalog,
            scope=str(request["scope"]),
            snapshot_id=str(request["data_snapshot_id"]),
        )
        projection = _alias_projection(
            wrapper["catalog"],
            request["selections"],
            scope=str(request["scope"]),
        )
        wrapper["catalog"] = projection.catalog
        preset = _preset_for_request(
            request,
            theme_count=theme_count,
            total_score=max(
                1.0,
                float(
                    sum(
                        setting["score"]
                        for setting in (request.get("atomic_settings") or {}).values()
                    )
                    if request.get("atomic_settings") is not None
                    else request["score_per_atomic"]
                ),
            ),
        )

        def loader(scope: str, expected_data_snapshot_id: str) -> Mapping[str, Any]:
            if (
                scope != request["scope"]
                or expected_data_snapshot_id != request["data_snapshot_id"]
            ):
                raise PaperExportWorkbenchError(
                    "theme_snapshot_stale", "题篮数据快照已变化。", 409
                )
            return deepcopy(wrapper)

        blueprint = build_assembly_blueprint(
            projection.selections,
            theme_loader=loader,
            expected_data_snapshot_id=str(request["data_snapshot_id"]),
            preset=preset,
        )
    except Exception:
        # The worker reruns the authoritative build and persists any non-limit
        # failure. Keeping those failures asynchronous preserves the existing
        # job contract while known oversized work never enters the queue.
        return

    final_ids = {
        atomic_id
        for bundle in blueprint.get("theme_bundles", [])
        if isinstance(bundle, Mapping)
        for atomic_id in bundle.get("final_atomic_ids", [])
        if isinstance(atomic_id, str)
    }
    if not final_ids:
        return
    total_score = _total_score_for_final_ids(request, final_ids)
    if total_score > _MAX_EXPORT_TOTAL_SCORE:
        displayed_score: int | float = (
            int(total_score) if total_score.is_integer() else total_score
        )
        raise PaperExportWorkbenchError(
            "paper_export_total_score_limit_exceeded",
            f"展开前序依赖后总分不能超过 {int(_MAX_EXPORT_TOTAL_SCORE)} 分，请缩小题篮或降低单题分值。",
            400,
            details={
                "observed_atomic_part_count": len(final_ids),
                "score_per_atomic": request["score_per_atomic"],
                "observed_total_score": displayed_score,
                "maximum_total_score": int(_MAX_EXPORT_TOTAL_SCORE),
            },
        )


def _visual_record(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = detail.get("visual_scan")
    return nested if isinstance(nested, Mapping) else detail


def _descriptor_rows(detail: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    record = _visual_record(detail)
    rows = record.get("evidence_descriptors")
    if not isinstance(rows, list):
        rows = detail.get("evidence_descriptors")
    return visible_evidence(rows)


def _bind_blueprint_shared_materials_to_selected_details(
    blueprint: Mapping[str, Any],
    details: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Keep only shared crops referenced by the selected dependency closure.

    Theme catalogs intentionally describe the whole big question.  An atomic-only
    export must not load unrelated, possibly still-unscanned siblings merely to
    discover which common figures the chosen question uses.
    """

    projected = deepcopy(dict(blueprint))
    material_keys: set[str] = set()
    for bundle in projected.get("theme_bundles", []):
        final_ids = {
            value
            for value in bundle.get("final_atomic_ids", [])
            if isinstance(value, str)
        }
        referenced_ids = {
            str(descriptor["crop_id"])
            for node_id in final_ids
            for descriptor in _descriptor_rows(details.get(node_id, {}))
            if descriptor.get("evidence_role") == "shared_material"
            and isinstance(descriptor.get("crop_id"), str)
        }
        materials = bundle.get("shared_materials", [])
        known_ids = {
            str(material.get("material_id"))
            for material in materials
            if isinstance(material, Mapping)
            and isinstance(material.get("material_id"), str)
        }
        unknown = sorted(referenced_ids - known_ids)
        if unknown:
            raise PaperExportWorkbenchError(
                "paper_export_shared_material_unbound",
                "所选题目的共享材料未绑定到当前主题目录。",
                409,
                details={"material_ids": unknown},
            )
        selected_materials = [
            material
            for material in materials
            if isinstance(material, Mapping)
            and material.get("material_id") in referenced_ids
        ]
        # Layout hints come from explicit selected-detail links, never from
        # used_by_atomic_count or a guessed contiguous question range.
        for material in selected_materials:
            material["used_by_atomic_ids"] = [
                node_id
                for node_id in bundle["final_atomic_ids"]
                if any(
                    descriptor.get("evidence_role") == "shared_material"
                    and descriptor.get("crop_id") == material["material_id"]
                    for descriptor in _descriptor_rows(details.get(node_id, {}))
                )
            ]
        bundle["shared_materials"] = selected_materials
        material_keys.update(
            str(material["render_once_key"])
            for material in selected_materials
            if isinstance(material.get("render_once_key"), str)
        )
    counts = projected.get("counts")
    if isinstance(counts, dict):
        counts["shared_material_count"] = len(material_keys)
    projected.pop("blueprint_digest", None)
    status = projected.pop("status", None)
    expected_status = (
        "ready_for_content_resolution" if not projected.get("blockers") else "blocked"
    )
    if status != expected_status:
        raise PaperExportWorkbenchError(
            "paper_export_blueprint_status_invalid", "组卷蓝图状态不正确。", 409
        )
    projected["blueprint_digest"] = _sha256_bytes(_canonical_json_bytes(projected))
    projected["status"] = expected_status
    return projected


class _WorkbenchContentResolver:
    def __init__(
        self,
        *,
        scope: str,
        rows: Sequence[Mapping[str, Any]],
        details: Mapping[str, Mapping[str, Any]],
        crop_loader: Callable[[str, str], Any],
        asset_root: Path,
        score_per_atomic: float,
        default_answer_lines: int,
        atomic_settings: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        self.scope = scope
        self.rows = {str(row.get("atomic_part_id")): row for row in rows}
        self.details = details
        self.crop_loader = crop_loader
        self.asset_root = asset_root
        self.score_per_atomic = score_per_atomic
        self.default_answer_lines = default_answer_lines
        self.atomic_settings = atomic_settings
        self._material_sources: dict[tuple[str, str, str, str], str] = {}
        for node_id, detail in details.items():
            row = self.rows.get(node_id, {})
            paper_id = row.get("_export_paper_id")
            theme_id = row.get("_export_theme_id")
            if not isinstance(paper_id, str) or not isinstance(theme_id, str):
                continue
            for descriptor in _descriptor_rows(detail):
                if descriptor.get("evidence_role") == "shared_material" and isinstance(
                    descriptor.get("crop_id"), str
                ):
                    key = (self.scope, paper_id, theme_id, descriptor["crop_id"])
                    self._material_sources.setdefault(key, node_id)

    def _asset_block_with_sha(
        self,
        *,
        node_id: str,
        crop_id: str,
        block_type: str,
        alt_text_zh: str,
    ) -> tuple[dict[str, Any], str]:
        payload = self.crop_loader(node_id, crop_id)
        data = getattr(payload, "data", None)
        expected_sha = getattr(payload, "sha256", None)
        content_type = getattr(payload, "content_type", None)
        if (
            not isinstance(data, bytes)
            or not isinstance(expected_sha, str)
            or _sha256_bytes(data) != expected_sha
            or content_type != "image/png"
        ):
            raise PaperExportWorkbenchError(
                "paper_export_crop_invalid", "题图读取或校验失败。", 409
            )
        relative = Path("images") / f"{expected_sha}.png"
        target = self.asset_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _sha256_bytes(target.read_bytes()) != expected_sha:
                raise PaperExportWorkbenchError(
                    "paper_export_crop_drift", "导出任务内的题图发生变化。", 409
                )
        else:
            target.write_bytes(data)
        return (
            {
                "block_type": block_type,
                "text_zh": None,
                "asset_ref": relative.as_posix(),
                "alt_text_zh": alt_text_zh[:1000],
            },
            expected_sha,
        )

    def _asset_block(
        self,
        *,
        node_id: str,
        crop_id: str,
        block_type: str,
        alt_text_zh: str,
    ) -> dict[str, Any]:
        block, _ = self._asset_block_with_sha(
            node_id=node_id,
            crop_id=crop_id,
            block_type=block_type,
            alt_text_zh=alt_text_zh,
        )
        return block

    def _source_label(self, node_id: str, detail: Mapping[str, Any]) -> str:
        record = _visual_record(detail)
        boundary = record.get("source_boundary_zh") or detail.get("source_boundary_zh")
        identity = record.get("identity_boundary")
        paper_face = (
            identity.get("paper_face_title_literal")
            if isinstance(identity, Mapping)
            else None
        )
        row = self.rows.get(node_id, {})
        paper_title = row.get("_export_paper_title")
        source_metadata = row.get("_export_source_metadata")
        source_bits: list[str] = []
        if isinstance(source_metadata, Mapping):
            for key in ("year", "region", "paper_type"):
                value = source_metadata.get(key)
                if isinstance(value, str) and value and value != "unknown":
                    source_bits.append(value)
        fallback = (
            f"题库来源卷：{paper_title}"
            if isinstance(paper_title, str) and paper_title.strip()
            else "本地题库来源记录；详情见题库工作台。"
        )
        if source_bits and fallback.startswith("题库来源卷："):
            fallback += "；" + " / ".join(dict.fromkeys(source_bits))
        return str(boundary or paper_face or fallback)[:1000]

    def resolve_shared_material(
        self, reference: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        material_id = reference.get("material_id")
        source_key = (
            str(reference.get("scope")),
            str(reference.get("paper_id")),
            str(reference.get("theme_id")),
            str(material_id),
        )
        node_id = self._material_sources.get(source_key)
        if node_id is None:
            raise PaperExportWorkbenchError(
                "paper_export_shared_material_missing",
                "当前主题共享材料缺少可导出的精确题图。",
                409,
                details={
                    "scope": reference.get("scope"),
                    "paper_id": reference.get("paper_id"),
                    "theme_id": reference.get("theme_id"),
                    "material_id": material_id,
                },
            )
        detail = self.details[node_id]
        asset_block, source_sha256 = self._asset_block_with_sha(
            node_id=node_id,
            crop_id=str(material_id),
            block_type="image",
            alt_text_zh="主题共同材料",
        )
        return {
            "content_blocks": [asset_block],
            "source_label_zh": self._source_label(node_id, detail),
            "material_id": str(material_id),
            "source_crop_id": str(material_id),
            "source_sha256": source_sha256,
            "source_page": reference.get("page"),
        }

    def resolve_atomic_part(self, reference: Mapping[str, Any]) -> Mapping[str, Any]:
        node_id = str(reference.get("atomic_part_id"))
        detail = self.details.get(node_id)
        if detail is None:
            raise PaperExportWorkbenchError(
                "paper_export_question_missing", "题篮中的题目缺少逐图详情。", 409
            )
        record = _visual_record(detail)
        descriptors = [
            row
            for row in _descriptor_rows(detail)
            if row.get("evidence_role") == "question"
            and isinstance(row.get("crop_id"), str)
        ]
        if not descriptors:
            raise PaperExportWorkbenchError(
                "paper_export_question_crop_missing",
                "题目缺少可导出的精确题面裁片。",
                409,
            )
        question_blocks = [
            self._asset_block(
                node_id=node_id,
                crop_id=str(descriptor["crop_id"]),
                block_type="image",
                # Candidate summaries may already contain the solution. Both
                # copies use neutral alternative text; answers stay teacher-only.
                alt_text_zh="原卷题面；按图中题干、条件和小题要求作答。",
            )
            for descriptor in descriptors
        ]
        answer = record.get("reference_answer")
        if not isinstance(answer, Mapping):
            answer = detail.get("reference_answer")
        if not isinstance(answer, Mapping):
            answer = {}
        availability = str(answer.get("availability") or "absent")
        answer_text = answer.get("reference_answer_text")
        if availability in {"present_part_aligned", "aligned"}:
            source_authority = answer.get("source_authority")
            independently_verified = answer.get("independently_verified")
            if (
                not isinstance(answer_text, str)
                or not answer_text.strip()
                or not isinstance(source_authority, str)
                or not source_authority
                or source_authority == "none"
                or type(independently_verified) is not bool
            ):
                raise PaperExportWorkbenchError(
                    "paper_export_answer_contract_invalid",
                    "题库声称参考答案已逐题对齐，但答案正文或来源字段不完整。",
                    409,
                    details={"atomic_part_id": node_id},
                )
            reference_answer = {
                "status": "aligned",
                "text_zh": answer_text.strip(),
                "authority_label": "nonofficial",
                "independently_verified": independently_verified,
                "source_label_zh": "题库已逐题对齐的非官方参考答案；按来源答案直接呈现。",
            }
        elif availability in {"present_unaligned", "unaligned"}:
            if isinstance(answer_text, str) and answer_text.strip():
                raise PaperExportWorkbenchError(
                    "paper_export_answer_contract_invalid",
                    "未逐题对齐的参考答案不能作为本题答案正文导出。",
                    409,
                    details={"atomic_part_id": node_id},
                )
            reference_answer = {
                "status": "unaligned",
                "text_zh": None,
                "authority_label": "none",
                "independently_verified": False,
                "source_label_zh": None,
            }
        elif availability == "absent":
            if isinstance(answer_text, str) and answer_text.strip():
                raise PaperExportWorkbenchError(
                    "paper_export_answer_contract_invalid",
                    "标为无参考答案的题目意外包含答案正文。",
                    409,
                    details={"atomic_part_id": node_id},
                )
            reference_answer = {
                "status": "absent",
                "text_zh": None,
                "authority_label": "none",
                "independently_verified": False,
                "source_label_zh": None,
            }
        else:
            raise PaperExportWorkbenchError(
                "paper_export_answer_contract_invalid",
                "题库参考答案状态不受支持，不能静默降级。",
                409,
                details={"atomic_part_id": node_id, "availability": availability},
            )
        analysis = record.get("candidate_analysis")
        if not isinstance(analysis, Mapping):
            analysis = record.get("model_candidate_analysis")
        solution = (
            analysis.get("solution_path_zh") if isinstance(analysis, Mapping) else None
        )
        explanation = _compact_solution_explanation(solution)
        risks = record.get("risks_and_limits")
        pitfalls = risks.get("common_errors_zh") if isinstance(risks, Mapping) else []
        if not isinstance(pitfalls, list):
            pitfalls = []
        row = self.rows.get(node_id, {})
        item_type = str(
            row.get("item_type")
            or record.get("scan_classification", {}).get("item_type")
            or ""
        )
        setting = (
            self.atomic_settings.get(node_id)
            if self.atomic_settings is not None
            else None
        )
        if setting is None:
            score = self.score_per_atomic
            answer_lines = _source_aware_answer_space_lines(
                node_id=node_id,
                item_type=item_type,
                requested_lines=self.default_answer_lines,
            )
        else:
            score = float(setting["score"])
            answer_lines = int(setting["answer_space_lines"])
        content = {
            "question_blocks": question_blocks,
            "score": score,
            "answer_space_lines": answer_lines,
            "reference_answer": reference_answer,
            "explanation_zh": explanation,
            "explanation_label": "ai_candidate" if explanation else "none",
            "pitfalls_zh": [
                str(item).strip()
                for item in pitfalls
                if isinstance(item, str) and item.strip()
            ],
            "source_label_zh": self._source_label(node_id, detail),
        }
        if "supplemental_answer" in record:
            try:
                supplement = validate_supplemental_answer(record["supplemental_answer"])
            except ValueError as exc:
                raise PaperExportWorkbenchError("paper_export_supplement_invalid", str(exc), 409) from exc
            if availability != "absent":
                raise PaperExportWorkbenchError("paper_export_supplement_invalid", "补充答案不得替代已有来源答案。", 409)
            content["supplemental_answer"] = supplement
            content["explanation_zh"] = supplement["explanation_zh"]
            content["explanation_label"] = "ai_candidate"
            content["pitfalls_zh"] = supplement["pitfalls_zh"]
        if setting is not None:
            content["answer_space_explicit"] = True
        return content


def _source_aware_answer_space_lines(*, node_id: str, item_type: str, requested_lines: int) -> int:
    if node_id in EXISTING_ANSWER_AREA_NODE_IDS:
        return 0  # Preserve source answer areas; explicit teacher overrides still win.
    return _compact_answer_space_lines(item_type=item_type, requested_lines=requested_lines)


def _compact_answer_space_lines(*, item_type: str, requested_lines: int) -> int:
    """Treat the teacher setting as an upper bound and compact it by response type."""

    normalized = item_type.strip().lower()
    if "choice" in normalized:
        return 0
    if normalized in {"short_fill", "chemical_equation_or_notation"}:
        return min(requested_lines, 1)
    if normalized == "reasoned_explanation":
        return min(requested_lines, 2)
    if normalized in {
        "quantitative_calculation",
        "organic_structure_or_route",
        "experiment_operation_apparatus_plan",
        "graph_read_draw_complete",
    }:
        return min(requested_lines, 3)
    return requested_lines


def _compact_solution_explanation(solution: Any) -> str | None:
    """Keep useful reasoning while dropping repeated answer-provenance boilerplate."""

    if not isinstance(solution, list):
        return None
    lines = [
        str(item).strip() for item in solution if isinstance(item, str) and item.strip()
    ]
    meaningful = [
        line
        for line in lines
        if not ("不作独立正确性核验" in line and ("照录" in line or "来源答案" in line))
    ]
    return "\n".join(meaningful) or None


def _locate_toolchain() -> RendererToolchain:
    home = Path.home()
    runtime = (
        home / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
    )
    python_exe = runtime / "python" / "python.exe"
    pdftoppm = runtime / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    documents_root = (
        home / ".codex" / "plugins" / "cache" / "openai-primary-runtime" / "documents"
    )
    candidates = sorted(
        documents_root.glob("*/skills/documents/render_docx.py"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        reverse=True,
    )
    render_script = candidates[0] if candidates else Path("__missing_render_docx__.py")
    return RendererToolchain(
        python_exe=python_exe if python_exe.is_file() else Path(sys.executable),
        render_docx_script=render_script,
        pdftoppm_exe=pdftoppm,
        dpi=300,
        conversion_backend="word_com" if os.name == "nt" else "auto",
    )


class PaperExportJobManager:
    """Single-user, persistent background queue for theme-first paper export."""

    def __init__(self, state_root: Path) -> None:
        self.root = (state_root / "paper-export-workbench").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="shchem-paper-export"
        )
        self._lock = threading.RLock()
        self._closed = False
        self._mark_interrupted_jobs()

    def _job_root(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise PaperExportWorkbenchError(
                "paper_export_job_invalid", "导出任务标识不正确。"
            )
        candidate = (self.root / job_id).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PaperExportWorkbenchError(
                "paper_export_job_invalid", "导出任务路径不正确。"
            ) from exc
        return candidate

    def _job_path(self, job_id: str) -> Path:
        return self._job_root(job_id) / "job.json"

    def _read_job(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(job_id)
        if not path.is_file():
            raise PaperExportWorkbenchError(
                "paper_export_job_not_found", "找不到导出任务。", 404
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaperExportWorkbenchError(
                "paper_export_job_corrupt", "导出任务记录无法读取。", 409
            ) from exc
        if not isinstance(value, dict) or value.get("job_id") != job_id:
            raise PaperExportWorkbenchError(
                "paper_export_job_corrupt", "导出任务记录不一致。", 409
            )
        return value

    def _write_job(self, job: Mapping[str, Any]) -> None:
        # On Windows, replacing job.json while the desktop polling loop has it
        # open can fail transiently and leave an otherwise completed export
        # stuck in its previous state.  Serialize reads and atomic replaces
        # through the manager's re-entrant lock; start() already holds it.
        with self._lock:
            _atomic_write_json(self._job_path(str(job["job_id"])), job)

    def _mark_interrupted_jobs(self) -> None:
        for path in self.root.glob("WBEXP-*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(job, dict) and job.get("status") in {"queued", "running"}:
                job["status"] = "failed"
                job["updated_at"] = _utc_now()
                job["progress"] = {
                    "stage": "failed",
                    "percent": 100,
                    "message_zh": "服务重启中断了上次导出，请重新提交。",
                }
                job["error"] = {
                    "code": "paper_export_interrupted",
                    "message_zh": "服务重启中断了上次导出，请重新提交。",
                }
                _atomic_write_json(path, job)

    @staticmethod
    def _public_job(job: Mapping[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {
                key: value
                for key, value in job.items()
                if key not in {"request", "private_paths"}
            }
        )

    def start(
        self,
        payload: Mapping[str, Any],
        *,
        theme_catalog_loader: Callable[[str], Mapping[str, Any]],
        detail_loader: Callable[[str, str], Mapping[str, Any]],
        crop_loader: Callable[[str, str, str], Any],
    ) -> dict[str, Any]:
        request = _validated_request(payload)
        _enforce_prequeue_expanded_limits(
            request, theme_catalog_loader=theme_catalog_loader
        )
        with self._lock:
            if self._closed:
                raise PaperExportWorkbenchError(
                    "paper_export_unavailable", "导出服务正在关闭。", 503
                )
            job_id = f"WBEXP-{uuid.uuid4().hex}"
            now = _utc_now()
            job = {
                "schema_version": PAPER_EXPORT_JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "scope": request["scope"],
                "data_snapshot_id": request["data_snapshot_id"],
                "title_zh": request["title_zh"],
                "request_digest": _sha256_bytes(_canonical_json_bytes(request)),
                "progress": {
                    "stage": "queued",
                    "percent": 0,
                    "message_zh": "已进入本机导出队列。",
                },
                "counts": {},
                "artifacts": [],
                "error": None,
                "request": request,
                "private_paths": {},
            }
            self._write_job(job)
            self._executor.submit(
                self._run,
                job_id,
                deepcopy(job),
                theme_catalog_loader,
                detail_loader,
                crop_loader,
            )
        return self._public_job(job)

    def _progress(
        self, job: dict[str, Any], stage: str, percent: int, message_zh: str
    ) -> None:
        job["status"] = "running"
        job["updated_at"] = _utc_now()
        job["progress"] = {"stage": stage, "percent": percent, "message_zh": message_zh}
        self._write_job(job)

    def _run(
        self,
        job_id: str,
        queued_job: Mapping[str, Any],
        theme_catalog_loader: Callable[[str], Mapping[str, Any]],
        detail_loader: Callable[[str, str], Mapping[str, Any]],
        crop_loader: Callable[[str, str, str], Any],
    ) -> None:
        try:
            job = self._read_job(job_id)
        except Exception:
            # ``queued_job`` is the exact in-memory envelope atomically written
            # immediately before submission. It lets the worker replace a
            # damaged job.json with a schema-compatible, diagnosable terminal
            # state instead of leaving an unreadable task that appears queued
            # forever in the UI.
            job = deepcopy(dict(queued_job))
            message = "导出任务记录在后台启动时损坏；本次任务已停止，请重新提交。"
            job["status"] = "failed"
            job["updated_at"] = _utc_now()
            job["progress"] = {
                "stage": "failed",
                "percent": 100,
                "message_zh": message,
            }
            job["counts"] = {}
            job["artifacts"] = []
            job["error"] = {
                "code": "paper_export_job_corrupt",
                "message_zh": message,
                "http_status": 409,
                "details": {"recovery_action_zh": "请重新提交导出任务。"},
            }
            job["private_paths"] = {}
            self._write_job(job)
            return
        request = job["request"]
        try:
            self._progress(
                job, "loading_theme_catalog", 8, "正在读取题篮对应的完整主题与依赖。"
            )
            raw_catalog = theme_catalog_loader(request["scope"])
            wrapper = _prepare_catalog(
                raw_catalog,
                scope=request["scope"],
                snapshot_id=request["data_snapshot_id"],
            )
            projection = _alias_projection(
                wrapper["catalog"],
                request["selections"],
                scope=request["scope"],
            )
            wrapper["catalog"] = projection.catalog
            theme_ids = {selection["theme_id"] for selection in request["selections"]}
            all_theme_rows = _theme_rows(
                wrapper["catalog"],
                theme_ids,
                content_bindings=projection.content_bindings,
            )
            initial_preset = _preset_for_request(
                request,
                theme_count=len(theme_ids),
                total_score=max(
                    1.0,
                    float(
                        sum(
                            setting["score"]
                            for setting in (
                                request.get("atomic_settings") or {}
                            ).values()
                        )
                        if request.get("atomic_settings") is not None
                        else request["score_per_atomic"]
                    ),
                ),
            )

            def loader(scope: str, expected_data_snapshot_id: str) -> Mapping[str, Any]:
                if (
                    scope != request["scope"]
                    or expected_data_snapshot_id != request["data_snapshot_id"]
                ):
                    raise PaperExportWorkbenchError(
                        "theme_snapshot_stale", "题篮数据快照已变化。", 409
                    )
                return deepcopy(wrapper)

            blueprint = build_assembly_blueprint(
                projection.selections,
                theme_loader=loader,
                expected_data_snapshot_id=request["data_snapshot_id"],
                preset=initial_preset,
            )
            if blueprint.get("status") != "ready_for_content_resolution":
                blockers = blueprint.get("blockers", [])
                raise PaperExportWorkbenchError(
                    "paper_export_blueprint_blocked",
                    "题篮依赖或分项尚不完整，当前不能导出。",
                    409,
                    details={"blockers": blockers},
                )
            final_ids = {
                atomic_id
                for bundle in blueprint["theme_bundles"]
                for atomic_id in bundle["final_atomic_ids"]
            }
            atomic_settings = _atomic_settings_for_final_ids(request, final_ids)
            total_score = _total_score_for_final_ids(request, final_ids)
            preset = _preset_for_request(
                request,
                theme_count=blueprint["counts"]["theme_count"],
                total_score=total_score,
            )
            self._progress(
                job,
                "loading_question_assets",
                22,
                "正在读取精确题面裁片和逐题参考答案。",
            )
            details: dict[str, Mapping[str, Any]] = {}
            for row in all_theme_rows:
                node_id = row.get("atomic_part_id")
                if isinstance(node_id, str) and node_id in final_ids:
                    source_scope = row.get("_export_content_scope", request["scope"])
                    source_node_id = row.get("_export_content_node_id", node_id)
                    if not isinstance(source_scope, str) or not isinstance(
                        source_node_id, str
                    ):
                        raise PaperExportWorkbenchError(
                            "paper_export_alias_binding_invalid",
                            "拆分作答单元的内容读取绑定不完整。",
                            409,
                            details={"atomic_part_id": node_id},
                        )
                    detail = detail_loader(source_scope, source_node_id)
                    source_unit_node_id = row.get("_export_content_unit_node_id")
                    if source_unit_node_id is not None:
                        if not isinstance(source_unit_node_id, str):
                            raise PaperExportWorkbenchError(
                                "paper_export_alias_binding_invalid",
                                "拆分作答单元的分项读取绑定不完整。",
                                409,
                                details={"atomic_part_id": node_id},
                            )
                        try:
                            detail = project_direct_unit_scan(
                                detail, source_unit_node_id
                            )
                        except PaperExportAliasProjectionError as exc:
                            raise PaperExportWorkbenchError(
                                exc.code,
                                str(exc),
                                exc.status,
                                details=exc.details,
                            ) from exc
                    details[node_id] = detail
            missing = sorted(final_ids - set(details))
            if missing:
                raise PaperExportWorkbenchError(
                    "paper_export_question_missing",
                    "题篮中有题目尚无可导出的逐图详情。",
                    409,
                    details={"missing_atomic_ids": missing},
                )
            blueprint = _bind_blueprint_shared_materials_to_selected_details(
                blueprint, details
            )
            job_root = self._job_root(job_id)
            asset_root = job_root / "assets"
            rows_by_id = {str(row.get("atomic_part_id")): row for row in all_theme_rows}

            def routed_crop_loader(node_id: str, crop_id: str) -> Any:
                row = rows_by_id.get(node_id, {})
                source_scope = row.get("_export_content_scope", request["scope"])
                source_node_id = row.get("_export_content_node_id", node_id)
                if not isinstance(source_scope, str) or not isinstance(
                    source_node_id, str
                ):
                    raise PaperExportWorkbenchError(
                        "paper_export_alias_binding_invalid",
                        "拆分作答单元的题图读取绑定不完整。",
                        409,
                        details={"atomic_part_id": node_id},
                    )
                return crop_loader(source_scope, source_node_id, crop_id)

            resolver = _WorkbenchContentResolver(
                scope=request["scope"],
                rows=all_theme_rows,
                details=details,
                crop_loader=routed_crop_loader,
                asset_root=asset_root,
                score_per_atomic=float(request["score_per_atomic"]),
                default_answer_lines=request["answer_space_lines"],
                atomic_settings=atomic_settings,
            )
            plans = build_document_plans(
                blueprint,
                preset=preset,
                paper_metadata={
                    "title_zh": request["title_zh"],
                    "subtitle_zh": request["subtitle_zh"],
                    "version_label_zh": "本地题篮导出 · 题目来源见教师版 · 不可发布",
                },
                content_resolver=resolver,
            )
            preflight = run_export_preflight(
                preset=preset,
                blueprint=blueprint,
                student_plan=plans["student"],
                teacher_plan=plans["teacher"],
            )
            render_request = build_render_request(
                preset=preset,
                student_plan=plans["student"],
                teacher_plan=plans["teacher"],
                preflight_report=preflight,
            )
            bundle = {
                "renderer_schema_version": RENDERER_SCHEMA_VERSION,
                "contract_kind": RENDER_BUNDLE_KIND,
                "bundle_id": f"paper_export_{job_id}",
                "claim_boundary_zh": (
                    "教师本地备课导出；题目来源见卷内教师版来源标签；"
                    "来源参考答案与 AI 补充解答分别标注；不可发布。"
                ),
                "preset": preset,
                "blueprint": blueprint,
                "student_plan": plans["student"],
                "teacher_plan": plans["teacher"],
                "preflight_report": preflight,
                "render_request": render_request,
                "publication_allowed": False,
            }
            bundle_path = job_root / "render_bundle.json"
            _atomic_write_json(bundle_path, bundle)
            self._progress(job, "rendering", 42, "正在生成学生版与教师版 DOCX/PDF。")
            output_root = job_root / "output"
            report = render_export_bundle(
                bundle,
                output_dir=output_root,
                toolchain=_locate_toolchain(),
                asset_root=asset_root,
            )
            if report.get("machine_blocker_count") != 0:
                raise PaperExportWorkbenchError(
                    "paper_export_machine_qa_failed",
                    "导出文件未通过结构与版面机器检查。",
                    409,
                )
            artifacts: list[dict[str, Any]] = []
            for artifact in report.get("artifacts", []):
                artifact_id = artifact.get("artifact_id")
                if artifact_id not in _ARTIFACT_IDS:
                    continue
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "format": artifact.get("format"),
                        "audience": artifact.get("audience"),
                        "filename": Path(str(artifact.get("path"))).name,
                        "sha256": artifact.get("sha256"),
                        "size_bytes": artifact.get("size_bytes"),
                        "page_count": artifact.get("page_count"),
                        "download_path": f"/api/v1/prep/exports/{job_id}/artifacts/{artifact_id}",
                    }
                )
            if {row["artifact_id"] for row in artifacts} != _ARTIFACT_IDS:
                raise PaperExportWorkbenchError(
                    "paper_export_artifact_incomplete",
                    "导出未生成完整的四个文件。",
                    409,
                )
            job["status"] = "completed"
            job["updated_at"] = _utc_now()
            job["progress"] = {
                "stage": "completed",
                "percent": 100,
                "message_zh": "学生版与教师版 DOCX/PDF 已生成，可直接下载。",
            }
            job["counts"] = deepcopy(blueprint["counts"])
            job["counts"]["total_score"] = total_score
            job["artifacts"] = artifacts
            job["error"] = None
            job["private_paths"] = {"output_root": str(output_root)}
            self._write_job(job)
        except (
            Exception
        ) as exc:  # Every worker failure becomes a bounded persisted state.
            code = getattr(exc, "code", "paper_export_failed")
            status = getattr(exc, "status", 409)
            if not isinstance(code, str) or not code:
                code = "paper_export_failed"
            message = (
                str(exc)
                if isinstance(
                    exc,
                    (
                        PaperFormatContractError,
                        PaperExportRendererError,
                        PaperExportWorkbenchError,
                    ),
                )
                else "导出失败，请检查题篮后重试。"
            )
            details = getattr(exc, "details", {})
            job["status"] = "failed"
            job["updated_at"] = _utc_now()
            job["progress"] = {
                "stage": "failed",
                "percent": 100,
                "message_zh": message,
            }
            job["error"] = {
                "code": code,
                "message_zh": message,
                "http_status": status if isinstance(status, int) else 409,
                "details": dict(details) if isinstance(details, Mapping) else {},
            }
            self._write_job(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_job(self._read_job(job_id))

    def artifact_path(self, job_id: str, artifact_id: str) -> tuple[Path, str]:
        path, content_type, record = self._artifact_file(job_id, artifact_id)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PaperExportWorkbenchError(
                "paper_export_artifact_missing", "导出文件无法读取。", 404
            ) from exc
        if _sha256_bytes(data) != record.get("sha256"):
            raise PaperExportWorkbenchError(
                "paper_export_artifact_drift", "导出文件已变化，请重新生成。", 409
            )
        return path, content_type

    def artifact_bytes(self, job_id: str, artifact_id: str) -> tuple[bytes, str, str]:
        path, content_type, record = self._artifact_file(job_id, artifact_id)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PaperExportWorkbenchError(
                "paper_export_artifact_missing", "导出文件无法读取。", 404
            ) from exc
        if _sha256_bytes(data) != record.get("sha256"):
            raise PaperExportWorkbenchError(
                "paper_export_artifact_drift", "导出文件已变化，请重新生成。", 409
            )
        return data, content_type, path.name

    def _artifact_file(
        self, job_id: str, artifact_id: str
    ) -> tuple[Path, str, Mapping[str, Any]]:
        if artifact_id not in _ARTIFACT_IDS:
            raise PaperExportWorkbenchError(
                "paper_export_artifact_invalid", "导出文件标识不正确。"
            )
        job = self._read_job(job_id)
        if job.get("status") != "completed":
            raise PaperExportWorkbenchError(
                "paper_export_artifact_not_ready", "导出文件尚未生成完成。", 409
            )
        record = next(
            (
                row
                for row in job.get("artifacts", [])
                if row.get("artifact_id") == artifact_id
            ),
            None,
        )
        if not isinstance(record, Mapping):
            raise PaperExportWorkbenchError(
                "paper_export_artifact_missing", "导出文件记录缺失。", 404
            )
        output_root = self._job_root(job_id) / "output"
        path = (output_root / ARTIFACT_FILENAMES[artifact_id]).resolve()
        try:
            path.relative_to(output_root.resolve())
        except ValueError as exc:
            raise PaperExportWorkbenchError(
                "paper_export_artifact_path_invalid", "导出文件路径不正确。", 409
            ) from exc
        if not path.is_file():
            raise PaperExportWorkbenchError(
                "paper_export_artifact_missing", "导出文件不存在。", 404
            )
        content_type = {
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
        }[str(record["format"])]
        return path, content_type, record

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)


__all__ = [
    "PAPER_EXPORT_JOB_SCHEMA_VERSION",
    "PaperExportJobManager",
    "PaperExportWorkbenchError",
]
