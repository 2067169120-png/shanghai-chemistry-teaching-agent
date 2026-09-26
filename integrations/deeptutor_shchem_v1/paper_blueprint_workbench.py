from __future__ import annotations

"""Deterministic, theme-first paper-blueprint optimizer.

This is an integration candidate staged outside the frozen workbench source tree.
It consumes the existing theme catalog, question-search projection, curriculum
projection, paper-format preset, and five-field basket contract.  It never calls
a model and never writes the central question bank.

The optimizer has two explicit modes:

``mock_exam``
    A template-bound examination.  Exam name, template year/version, duration,
    total score, theme count, instructions, scoring rules, identity/seal
    configuration, numbering, and pagination rules are validated explicitly.

``daily_practice``
    A compact practice sheet.  It has no fixed theme-count rule.  Atomic-count
    and duration targets are ranking preferences, while the declared hard
    constraints remain fail-closed.

Atomic search hits are highlights inside a theme.  They never become a
standalone top-level section.  ``daily_practice`` may intentionally request the
existing ``dependency`` basket unit, but its projection remains grouped under
``theme_big_question`` and preserves the theme context.
"""

import hashlib
import itertools
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from integrations.deeptutor_shchem_v1.paper_format_presets import (
    TOP_LEVEL_UNIT,
    PaperFormatContractError,
    build_assembly_blueprint,
    default_shanghai_theme_preset,
    validate_paper_format_preset,
)

SCHEMA_VERSION = "shchem.paper-blueprint-workbench.v1"
PREVIEW_SCHEMA_VERSION = "shchem.paper-blueprint-preview.v1"
PREVIEW_STORE_SCHEMA_VERSION = "shchem.paper-blueprint-preview-store.v1"
PREVIEW_APPROVAL_SCHEMA_VERSION = "shchem.paper-blueprint-preview-approval.v1"
MODE_MOCK_EXAM = "mock_exam"
MODE_DAILY_PRACTICE = "daily_practice"
MODES = frozenset({MODE_MOCK_EXAM, MODE_DAILY_PRACTICE})

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_AXES = ("K", "A", "C", "R", "RP")
_DIFFICULTIES = ("D1", "D2", "D3", "D4", "D5")
_ANSWER_AVAILABILITY = frozenset(
    {"present_part_aligned", "present_unaligned", "absent"}
)
_SELECTION_UNITS = frozenset({"theme", "dependency"})
_MAX_COMBINATION_STATES = 250_000
_MAX_FEASIBLE_POOL = 2_000
_PREVIEW_APPROVAL_ID = re.compile(r"^PBAPP-[0-9a-f]{32}$")
_PREVIEW_REVISION_ID = re.compile(r"^PBREV-[0-9a-f]{32}$")
_APPROVAL_REVISION_ID = re.compile(r"^PBAPR-[0-9a-f]{32}$")

_RESPONSE_TYPE_ORDER = {
    "embedded_single_choice": 10,
    "embedded_multiple_choice": 11,
    "embedded_indeterminate_choice": 12,
    "short_fill": 20,
    "chemical_equation_or_notation": 30,
    "organic_structure_or_route": 35,
    "graph_read_draw_complete": 40,
    "reasoned_explanation": 50,
    "quantitative_calculation": 60,
    "process_flow_condition_choice": 70,
    "experiment_operation_apparatus_plan": 80,
    "comparison_or_open_response": 90,
}

_RESPONSE_TYPE_LABELS = {
    "embedded_single_choice": "主题内单项选择",
    "embedded_multiple_choice": "主题内多项选择",
    "embedded_indeterminate_choice": "主题内不定项选择",
    "short_fill": "填空",
    "chemical_equation_or_notation": "化学方程式或化学用语",
    "organic_structure_or_route": "有机结构或合成路线",
    "quantitative_calculation": "定量计算",
    "reasoned_explanation": "原因解释或证据推理",
    "graph_read_draw_complete": "图表读取、绘制或补全",
    "experiment_operation_apparatus_plan": "实验操作、装置或方案",
    "process_flow_condition_choice": "工艺流程与条件选择",
    "comparison_or_open_response": "比较评价或开放作答",
    "unknown": "作答类型待补",
}


class PaperBlueprintWorkbenchError(ValueError):
    """Sanitized invalid-input or stale-contract error."""

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
        self.message_zh = message_zh
        self.status = status
        self.details = deepcopy(dict(details or {}))


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the repository-standard canonical JSON SHA-256."""

    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}标识不正确。"
        )
    return value


def _text(
    value: Any,
    field: str,
    *,
    required: bool = True,
    limit: int = 500,
) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(character) < 32 for character in value)
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}格式不正确。"
        )
    return value.strip()


def _decimal(value: Any, field: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}必须是非负数。"
        )
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}必须是非负数。"
        ) from exc
    if not result.is_finite() or result < 0:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}必须是非负数。"
        )
    return result


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value.normalize())


def _string_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
    sort_result: bool = True,
) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or (not value and not allow_empty):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}必须是字符串列表。"
        )
    result: list[str] = []
    for item in value:
        normalized = _safe_id(item, field)
        if normalized in result:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", f"{field}不能含重复值。"
            )
        result.append(normalized)
    return sorted(result) if sort_result else result


def _display_text_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or (not value and not allow_empty):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}必须是文本列表。"
        )
    result: list[str] = []
    for item in value:
        normalized = _text(item, field, limit=500)
        assert normalized is not None
        if normalized in result:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", f"{field}不能含重复值。"
            )
        result.append(normalized)
    return result


def _unknown_display(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if not 1 <= value <= 99:
        return str(value)
    if value < 10:
        return digits[value]
    tens, ones = divmod(value, 10)
    prefix = "十" if tens == 1 else f"{digits[tens]}十"
    return prefix if ones == 0 else f"{prefix}{digits[ones]}"


def _conflict(code: str, message_zh: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message_zh": message_zh, "details": details}


def _gap(
    code: str,
    message_zh: str,
    *,
    affected_theme_ids: Sequence[str] = (),
    affected_atomic_ids: Sequence[str] = (),
    missing_fields: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "code": code,
        "message_zh": message_zh,
        "affected_theme_ids": sorted(set(affected_theme_ids)),
        "affected_atomic_ids": sorted(set(affected_atomic_ids)),
        "missing_fields": sorted(set(missing_fields)),
    }


def _dedupe_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        digest = canonical_sha256(record)
        if digest not in seen:
            seen.add(digest)
            result.append(deepcopy(dict(record)))
    return result


@dataclass(frozen=True)
class _AxisRule:
    minimum: int
    maximum: int | None


@dataclass(frozen=True)
class _AtomicInfo:
    atomic_id: str
    printed_id: str
    theme_id: str
    paper_id: str
    row: Mapping[str, Any]
    score: Decimal | None
    time_minutes: Decimal | None
    duplicate_cluster_id: str | None
    axes: Mapping[str, frozenset[str]]
    difficulty: str | None
    item_type: str
    answer: Mapping[str, Any]
    textbook_edges: tuple[Mapping[str, Any], ...]
    textbook_display: Mapping[str, Any]
    curriculum_order: tuple[int, int, int]
    answer_space_lines: int
    expandable_content_ref: str | None
    preview_text_zh: str
    preview_content_available: bool


@dataclass(frozen=True)
class _ThemeUnit:
    scope: str
    paper: Mapping[str, Any]
    group: Mapping[str, Any]
    theme_id: str
    title_zh: str
    final_atomic_ids: tuple[str, ...]
    highlighted_atomic_ids: tuple[str, ...]
    basket_selections: tuple[Mapping[str, Any], ...]
    source_year: str
    natural_sort_key: tuple[Any, ...]


def _normalized_pagination_rules(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not key for key in value
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "分页规则格式不正确。"
        )
    normalized = deepcopy(dict(value))
    boolean_rules = {
        "cover_page",
        "start_each_theme_on_new_page",
        "keep_theme_together",
        "keep_printed_question_together",
        "show_page_numbers",
        "keep_theme_heading_with_first_question",
        "avoid_split_within_printed_question",
        "shared_material_at_theme_level_only",
    }
    unknown_rules = sorted(set(normalized) - boolean_rules)
    if unknown_rules:
        raise PaperBlueprintWorkbenchError(
            "paper_pagination_rule_invalid",
            "分页规则含 v1 尚未定义的字段，不能静默传给渲染器。",
            details={"unknown_rules": unknown_rules},
        )
    if any(not isinstance(item, bool) for item in normalized.values()):
        raise PaperBlueprintWorkbenchError(
            "paper_pagination_rule_invalid", "v1 分页规则值必须是布尔值。"
        )
    protected_true_rules = {
        "keep_printed_question_together",
        "keep_theme_heading_with_first_question",
        "avoid_split_within_printed_question",
        "shared_material_at_theme_level_only",
    }
    if any(normalized.get(key) is False for key in protected_true_rules):
        raise PaperBlueprintWorkbenchError(
            "paper_pagination_rule_invalid",
            "分页规则不能拆散主题标题与首问、卷面小题或主题共享材料。",
        )
    try:
        canonical_sha256(normalized)
    except (TypeError, ValueError) as exc:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "分页规则必须是可序列化的 JSON 数据。"
        ) from exc
    return normalized


def _normalized_mock_paper(value: Any, preset: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "mock_exam_metadata_required", "模拟考试必须填写完整考试信息。"
        )
    allowed = {
        "exam_name_zh",
        "subtitle_zh",
        "template_id",
        "template_version",
        "template_year",
        "duration_minutes",
        "duration_rule",
        "total_score",
        "theme_count",
        "instructions_zh",
        "scoring_rules",
        "identity_fields_zh",
        "sealed_line",
        "numbering_mode",
        "answer_space_lines",
        "score_per_atomic",
        "time_per_atomic_minutes",
        "pagination_rules",
    }
    if set(value) - allowed:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "模拟考试信息含未知字段。"
        )
    exam_name = _text(value.get("exam_name_zh"), "考试名称", limit=200)
    subtitle = _text(
        value.get("subtitle_zh"), "考试副标题", required=False, limit=300
    )
    template_id = _safe_id(value.get("template_id"), "考试模板")
    template_version = value.get("template_version")
    if not isinstance(template_version, str) or _VERSION.fullmatch(template_version) is None:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_template_invalid", "考试模板版本号不正确。"
        )
    if (
        template_id != preset["preset_id"]
        or template_version != preset["preset_version"]
    ):
        raise PaperBlueprintWorkbenchError(
            "mock_exam_template_mismatch",
            "所选年度模板与传入格式预设不一致，不能暗中改用其他模板。",
            409,
        )
    template_year = value.get("template_year")
    if type(template_year) is not int or not 2000 <= template_year <= 2100:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_template_year_required", "模拟考试必须绑定明确的模板年度。"
        )
    duration = value.get("duration_minutes")
    total_score = value.get("total_score")
    theme_count = value.get("theme_count")
    if type(duration) is not int or not 1 <= duration <= 600:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_duration_required", "模拟考试时长须为 1—600 分钟的整数。"
        )
    if type(total_score) is not int or not 1 <= total_score <= 500:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_total_score_required", "模拟考试总分须为 1—500 分的整数。"
        )
    if type(theme_count) is not int or not 1 <= theme_count <= 20:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_theme_count_required", "模拟考试主题数须为 1—20 的整数。"
        )
    duration_rule = value.get("duration_rule", "max")
    if duration_rule not in {"max", "exact"}:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "时长约束只支持 max 或 exact。"
        )
    numbering_mode = value.get("numbering_mode", "restart_within_each_theme")
    if numbering_mode != "restart_within_each_theme":
        raise PaperBlueprintWorkbenchError(
            "paper_numbering_rule_invalid",
            "新卷 printed/atomic 必须在每个完整主题内重新连续编号。",
        )
    instructions = _display_text_list(
        value.get("instructions_zh"), "卷首说明", allow_empty=False
    )
    scoring = value.get("scoring_rules")
    if not isinstance(scoring, Mapping) or set(scoring) != {
        "selection_rule_zh",
        "partial_credit_rule_zh",
        "other_rule_zh",
    }:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_scoring_rules_required",
            "模拟考试必须填写选择、分步得分和其他计分规则。",
        )
    normalized_scoring: dict[str, str | None] = {}
    for key, item in scoring.items():
        normalized_scoring[key] = (
            _text(item, "计分规则", required=False, limit=500)
            if item is not None
            else None
        )
    if not any(normalized_scoring.values()):
        raise PaperBlueprintWorkbenchError(
            "mock_exam_scoring_rules_required", "计分规则不能全部为空。"
        )
    identity = _display_text_list(value.get("identity_fields_zh", []), "姓名栏字段")
    sealed_line = value.get("sealed_line")
    if not isinstance(sealed_line, bool):
        raise PaperBlueprintWorkbenchError(
            "mock_exam_identity_layout_required", "密封线配置必须明确为启用或停用。"
        )
    answer_lines = value.get("answer_space_lines", 3)
    if type(answer_lines) is not int or not 0 <= answer_lines <= 30:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "默认答题空间须为 0—30 行。"
        )
    score_per_atomic = value.get("score_per_atomic")
    if score_per_atomic is not None and (
        type(score_per_atomic) is not int or not 1 <= score_per_atomic <= 20
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            "统一作答单元分值须为 1—20 分的整数。",
        )
    time_per_atomic = _decimal(
        value.get("time_per_atomic_minutes"),
        "统一作答单元预计用时",
        allow_none=True,
    )
    if time_per_atomic is not None and (
        time_per_atomic <= 0 or time_per_atomic > 300
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            "统一作答单元预计用时须大于 0 且不超过 300 分钟。",
        )
    pagination = _normalized_pagination_rules(value.get("pagination_rules"))
    if pagination.get("cover_page", True) is not True:
        raise PaperBlueprintWorkbenchError(
            "mock_exam_cover_required",
            "mock_exam 整卷预览必须保留封面与考试信息。",
        )
    return {
        "title_zh": exam_name,
        "subtitle_zh": subtitle,
        "template_id": template_id,
        "template_version": template_version,
        "template_year": template_year,
        "duration_minutes": duration,
        "duration_rule": duration_rule,
        "total_score": total_score,
        "theme_count": theme_count,
        "instructions_zh": instructions,
        "scoring_rules": normalized_scoring,
        "identity_fields_zh": identity,
        "sealed_line": sealed_line,
        "numbering_mode": numbering_mode,
        "answer_space_lines": answer_lines,
        "score_per_atomic": score_per_atomic,
        "time_per_atomic_minutes": _number(time_per_atomic),
        "pagination_rules": pagination,
    }


def _normalized_daily_paper(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "日常练习信息格式不正确。"
        )
    allowed = {
        "title_zh",
        "subtitle_zh",
        "instructions_zh",
        "identity_fields_zh",
        "sealed_line",
        "numbering_mode",
        "answer_space_lines",
        "score_per_atomic",
        "time_per_atomic_minutes",
        "pagination_rules",
    }
    if set(value) - allowed:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "日常练习信息含未知字段。"
        )
    title = _text(
        value.get("title_zh", "日常练习"), "练习名称", limit=200
    )
    subtitle = _text(
        value.get("subtitle_zh"), "练习副标题", required=False, limit=300
    )
    numbering_mode = value.get("numbering_mode", "restart_within_each_theme")
    if numbering_mode != "restart_within_each_theme":
        raise PaperBlueprintWorkbenchError(
            "paper_numbering_rule_invalid",
            "日常练习也必须在主题内使用新卷连续题号。",
        )
    answer_lines = value.get("answer_space_lines", 2)
    if type(answer_lines) is not int or not 0 <= answer_lines <= 30:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "默认答题空间须为 0—30 行。"
        )
    score_per_atomic = value.get("score_per_atomic")
    if score_per_atomic is not None and (
        type(score_per_atomic) is not int or not 1 <= score_per_atomic <= 20
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            "统一作答单元分值须为 1—20 分的整数。",
        )
    time_per_atomic = _decimal(
        value.get("time_per_atomic_minutes"),
        "统一作答单元预计用时",
        allow_none=True,
    )
    if time_per_atomic is not None and (
        time_per_atomic <= 0 or time_per_atomic > 300
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            "统一作答单元预计用时须大于 0 且不超过 300 分钟。",
        )
    sealed_line = value.get("sealed_line", False)
    if not isinstance(sealed_line, bool):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "密封线配置必须是布尔值。"
        )
    pagination = _normalized_pagination_rules(value.get("pagination_rules"))
    return {
        "title_zh": title,
        "subtitle_zh": subtitle,
        "template_id": None,
        "template_version": None,
        "template_year": None,
        "duration_minutes": None,
        "duration_rule": None,
        "total_score": None,
        "theme_count": None,
        "instructions_zh": _display_text_list(
            value.get("instructions_zh", []), "练习说明"
        ),
        "scoring_rules": None,
        "identity_fields_zh": _display_text_list(
            value.get("identity_fields_zh", []), "姓名栏字段"
        ),
        "sealed_line": sealed_line,
        "numbering_mode": numbering_mode,
        "answer_space_lines": answer_lines,
        "score_per_atomic": score_per_atomic,
        "time_per_atomic_minutes": _number(time_per_atomic),
        "pagination_rules": pagination,
    }


def _normalize_rule(value: Any, field: str) -> _AxisRule:
    if type(value) is int and value >= 0:
        return _AxisRule(value, value)
    if not isinstance(value, Mapping) or set(value) - {"min", "max"}:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            f"{field}须为非负整数或含 min/max 的对象。",
        )
    minimum = value.get("min", 0)
    maximum = value.get("max")
    if type(minimum) is not int or minimum < 0:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}.min 必须是非负整数。"
        )
    if maximum is not None and (
        type(maximum) is not int or maximum < minimum
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", f"{field}.max 不能小于 min。"
        )
    return _AxisRule(minimum, maximum)


def _normalize_coverage(value: Any) -> dict[str, dict[str, _AxisRule]]:
    if value is None:
        return {axis: {} for axis in _AXES}
    if not isinstance(value, Mapping) or set(value) - set(_AXES):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "K/A/C/R/RP 覆盖约束含未知维度。"
        )
    result: dict[str, dict[str, _AxisRule]] = {axis: {} for axis in _AXES}
    for axis in _AXES:
        specification = value.get(axis)
        if specification is None:
            continue
        if isinstance(specification, list):
            for label in _string_list(specification, f"{axis}覆盖"):
                result[axis][label] = _AxisRule(1, None)
            continue
        if not isinstance(specification, Mapping):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", f"{axis}覆盖格式不正确。"
            )
        for label, rule in sorted(specification.items()):
            normalized_label = _safe_id(label, f"{axis}覆盖")
            result[axis][normalized_label] = _normalize_rule(
                rule, f"{axis}.{normalized_label}"
            )
    return result


def _normalize_difficulty(value: Any) -> dict[str, _AxisRule]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or any(
        label not in _DIFFICULTIES for label in value
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "D 分布只支持 D1—D5。"
        )
    return {
        label: _normalize_rule(rule, f"D分布.{label}")
        for label, rule in sorted(value.items())
    }


def _normalize_answer_rule(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {
        "allowed_availability",
        "allowed_authorities",
        "allowed_source_authority",
        "require_all_selected",
    }:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "答案资格约束格式不正确。"
        )
    availability = value.get("allowed_availability")
    if not isinstance(availability, list) or not availability:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "答案资格必须指定允许的可用状态。"
        )
    allowed_availability = sorted(set(availability))
    if len(allowed_availability) != len(availability) or any(
        item not in _ANSWER_AVAILABILITY for item in allowed_availability
    ):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "答案可用状态含未知值或重复值。"
        )
    authorities = value.get(
        "allowed_authorities", value.get("allowed_source_authority")
    )
    if authorities is None:
        allowed_authorities: list[str] = []
    else:
        allowed_authorities = _string_list(authorities, "答案来源资格")
    require_all = value.get("require_all_selected", True)
    if require_all is not True:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid",
            "v1 只支持整卷全部入选作答单元满足答案资格，不能按比例暗放宽。",
        )
    return {
        "allowed_availability": allowed_availability,
        "allowed_authorities": allowed_authorities,
        "require_all_selected": True,
    }


def _normalize_dedup_clusters(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "去重簇必须是簇到题目列表的映射。"
        )
    result: dict[str, tuple[str, ...]] = {}
    seen_atomic: dict[str, str] = {}
    for cluster_id, atomic_ids in sorted(value.items()):
        cluster = _safe_id(cluster_id, "去重簇")
        normalized = tuple(_string_list(atomic_ids, f"去重簇 {cluster}"))
        if not normalized:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "去重簇不能是空列表。"
            )
        for atomic_id in normalized:
            previous = seen_atomic.setdefault(atomic_id, cluster)
            if previous != cluster:
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_request_invalid", "同一道题不能属于两个去重簇。"
                )
        result[cluster] = normalized
    return result


def _normalize_hard_constraints(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "硬约束格式不正确。"
        )
    allowed = {
        "coverage",
        "difficulty_distribution",
        "source_years",
        "answer_eligibility",
        "required_theme_ids",
        "required_atomic_ids",
        "required_item_ids",
        "excluded_theme_ids",
        "excluded_atomic_ids",
        "excluded_item_ids",
        "no_duplicate_clusters",
        "dedup_clusters",
    }
    if set(value) - allowed:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "硬约束含未知字段。"
        )
    source_years_raw = value.get("source_years", [])
    if not isinstance(source_years_raw, list):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "来源年份必须是列表。"
        )
    source_years: list[str] = []
    for year in source_years_raw:
        if type(year) is int and 1900 <= year <= 2100:
            normalized_year = str(year)
        elif isinstance(year, str) and re.fullmatch(r"(?:19|20|21)[0-9]{2}", year):
            normalized_year = year
        else:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "来源年份含未知值。"
            )
        if normalized_year in source_years:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "来源年份不能重复。"
            )
        source_years.append(normalized_year)
    no_duplicate = value.get("no_duplicate_clusters", False)
    if not isinstance(no_duplicate, bool):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "去重簇开关必须是布尔值。"
        )
    return {
        "coverage": _normalize_coverage(value.get("coverage")),
        "difficulty_distribution": _normalize_difficulty(
            value.get("difficulty_distribution")
        ),
        "source_years": sorted(source_years),
        "answer_eligibility": _normalize_answer_rule(
            value.get("answer_eligibility")
        ),
        "required_theme_ids": _string_list(
            value.get("required_theme_ids", []), "必选主题"
        ),
        "required_atomic_ids": _string_list(
            value.get("required_atomic_ids", []), "必选作答单元"
        ),
        "required_item_ids": _string_list(
            value.get("required_item_ids", []), "必选题"
        ),
        "excluded_theme_ids": _string_list(
            value.get("excluded_theme_ids", []), "排除主题"
        ),
        "excluded_atomic_ids": _string_list(
            value.get("excluded_atomic_ids", []), "排除作答单元"
        ),
        "excluded_item_ids": _string_list(
            value.get("excluded_item_ids", []), "排除题"
        ),
        "no_duplicate_clusters": no_duplicate,
        "dedup_clusters": _normalize_dedup_clusters(value.get("dedup_clusters")),
    }


def _normalize_preferences(value: Any, mode: str) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping) or set(value) - {
        "selection_unit",
        "target_atomic_count",
        "target_total_score",
        "target_duration_minutes",
        "target_theme_count",
    }:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "组卷偏好格式不正确。"
        )
    unit = value.get("selection_unit", "theme")
    if unit not in _SELECTION_UNITS:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "加入方式只支持 theme 或 dependency。"
        )
    if mode == MODE_MOCK_EXAM and unit != "theme":
        raise PaperBlueprintWorkbenchError(
            "mock_exam_complete_theme_required",
            "模拟考试只能以完整主题大题作为入卷一级单位。",
        )
    target_atomic = value.get("target_atomic_count")
    target_theme = value.get("target_theme_count")
    for target, label in (
        (target_atomic, "目标题量"),
        (target_theme, "目标主题数"),
    ):
        if target is not None and (type(target) is not int or target < 1):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", f"{label}必须是正整数。"
            )
    target_duration = _decimal(
        value.get("target_duration_minutes"), "目标练习时长", allow_none=True
    )
    target_score = _decimal(
        value.get("target_total_score"), "目标练习总分", allow_none=True
    )
    if target_score == 0:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "目标练习总分必须大于 0。"
        )
    return {
        "selection_unit": unit,
        "target_atomic_count": target_atomic,
        "target_total_score": _number(target_score),
        "target_duration_minutes": _number(target_duration),
        "target_theme_count": target_theme,
    }


def _normalize_ordering(payload: Mapping[str, Any]) -> dict[str, Any]:
    ordering = payload.get("ordering", {})
    if ordering is None:
        ordering = {}
    if not isinstance(ordering, Mapping) or set(ordering) - {
        "teacher_theme_order",
        "prerequisite_edges",
    }:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "主题排序规则格式不正确。"
        )
    teacher_raw = payload.get(
        "teacher_theme_order", ordering.get("teacher_theme_order", [])
    )
    teacher = _string_list(
        teacher_raw, "教师拖动顺序", sort_result=False
    )
    edge_values = ordering.get("prerequisite_edges", [])
    if not isinstance(edge_values, list):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "教材先修边必须是列表。"
        )
    edges: list[tuple[str, str]] = []
    for edge in edge_values:
        if isinstance(edge, Mapping) and set(edge) <= {
            "before",
            "after",
            "before_theme_id",
            "after_theme_id",
        }:
            before = edge.get("before", edge.get("before_theme_id"))
            after = edge.get("after", edge.get("after_theme_id"))
        elif isinstance(edge, list) and len(edge) == 2:
            before, after = edge
        else:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "教材先修边格式不正确。"
            )
        pair = (_safe_id(before, "先修主题"), _safe_id(after, "后续主题"))
        if pair[0] == pair[1] or pair in edges:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "教材先修边自环或重复。"
            )
        edges.append(pair)
    return {
        "teacher_theme_order": teacher,
        "prerequisite_edges": [list(edge) for edge in sorted(edges)],
    }


def _normalize_curriculum_selector(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    allowed = {"volume_id", "chapter_id", "section", "mapping_status"}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "教材册/章/节约束格式不正确。"
        )
    result: dict[str, str] = {}
    for key in allowed:
        item = value.get(key)
        if item is None:
            continue
        result[key] = _safe_id(item, f"教材{key}")
    return result


def _normalize_payload(
    payload: Any, preset: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "组卷请求不是有效对象。"
        )
    allowed = {
        "mode",
        "scope",
        "data_snapshot_id",
        "expected_data_snapshot_id",
        "candidate_count",
        "paper",
        "mock_exam",
        "daily_practice",
        "preview_config",
        "curriculum",
        "hard_constraints",
        "preferences",
        "soft_constraints",
        "ordering",
        "teacher_theme_order",
    }
    if set(payload) - allowed:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "组卷请求含未知字段。"
        )
    mode = payload.get("mode")
    if mode not in MODES:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_mode_invalid",
            "组卷模式只支持 mock_exam 或 daily_practice。",
        )
    scope = _safe_id(payload.get("scope"), "题库范围")
    snapshot_id = payload.get(
        "data_snapshot_id", payload.get("expected_data_snapshot_id")
    )
    if not isinstance(snapshot_id, str) or _SHA256.fullmatch(snapshot_id) is None:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_snapshot_invalid", "题库快照必须是 64 位 SHA-256。"
        )
    candidate_count = payload.get("candidate_count", 3)
    if type(candidate_count) is not int or not 1 <= candidate_count <= 3:
        raise PaperBlueprintWorkbenchError(
            "paper_blueprint_request_invalid", "候选数只能是 1—3。"
        )
    paper_value = payload.get("paper")
    if paper_value is None:
        paper_value = payload.get(mode, payload.get("preview_config"))
    paper = (
        _normalized_mock_paper(paper_value, preset)
        if mode == MODE_MOCK_EXAM
        else _normalized_daily_paper(paper_value)
    )
    preferences_value = payload.get(
        "preferences", payload.get("soft_constraints")
    )
    normalized = {
        "mode": mode,
        "scope": scope,
        "data_snapshot_id": snapshot_id,
        "candidate_count": candidate_count,
        "paper": paper,
        "curriculum": _normalize_curriculum_selector(payload.get("curriculum")),
        "hard_constraints": _normalize_hard_constraints(
            payload.get("hard_constraints")
        ),
        "preferences": _normalize_preferences(preferences_value, mode),
        "ordering": _normalize_ordering(payload),
    }
    if (
        mode == MODE_MOCK_EXAM
        and normalized["hard_constraints"]["answer_eligibility"] is None
    ):
        raise PaperBlueprintWorkbenchError(
            "mock_exam_answer_eligibility_required",
            "模拟考试必须由教师明确选择允许的答案可用状态和来源资格。",
        )
    return normalized


def _coerce_atomic_row(
    value: Any,
    *,
    printed_sequence: int,
    atomic_sequence: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题目录中的作答单元格式不正确。", 409
        )
    row = deepcopy(dict(value))
    atomic_id = row.get("atomic_part_id", row.get("atomic_id"))
    printed_id = row.get("printed_question_id", row.get("printed_id"))
    row["atomic_part_id"] = _safe_id(atomic_id, "作答单元")
    row["printed_question_id"] = _safe_id(printed_id, "卷面小题")
    source_number = row.get(
        "printed_question_number", row.get("source_number")
    )
    row["printed_question_number"] = source_number
    observed_printed_sequence = row.get("printed_sequence")
    if type(observed_printed_sequence) is not int or observed_printed_sequence < 1:
        observed_printed_sequence = None
    row["printed_sequence"] = observed_printed_sequence
    row.setdefault(
        "printed_sequence_status",
        (
            "explicit_optimizer_input"
            if observed_printed_sequence is not None
            else "unknown_pending_review"
        ),
    )
    observed_atomic_sequence = row.get(
        "atomic_sequence_in_printed", row.get("atomic_sequence")
    )
    if type(observed_atomic_sequence) is not int or observed_atomic_sequence < 1:
        observed_atomic_sequence = None
    row["atomic_sequence_in_printed"] = observed_atomic_sequence
    row.setdefault(
        "atomic_sequence_status",
        (
            "explicit_optimizer_input"
            if observed_atomic_sequence is not None
            else "unknown_pending_review"
        ),
    )
    labels = row.get("label_summary", row.get("labels"))
    if not isinstance(labels, Mapping):
        labels = {}
    labels = deepcopy(dict(labels))
    if "primary_K" not in labels:
        knowledge = labels.get("K", row.get("K"))
        if isinstance(knowledge, list):
            labels["primary_K"] = knowledge[0] if knowledge else None
            labels["supporting_K"] = knowledge[1:]
        else:
            labels["primary_K"] = knowledge
            labels["supporting_K"] = []
    labels.setdefault("supporting_K", [])
    for axis in ("A", "C", "R", "RP"):
        item = labels.get(axis, row.get(axis))
        if item is None and axis == "R":
            item = row.get("response_R")
        if item is None and axis == "RP":
            item = row.get("representation_RP")
        labels[axis] = [item] if isinstance(item, str) else list(item or [])
    labels.setdefault(
        "cognitive_prelabel", labels.get("D", row.get("difficulty"))
    )
    labels.setdefault("status", "explicit_optimizer_input")
    labels.setdefault("source", "explicit_optimizer_input")
    row["label_summary"] = labels
    answer = row.get("answer", row.get("answer_metadata"))
    if not isinstance(answer, Mapping):
        answer = {}
    row["answer"] = {
        "availability": answer.get("availability", "absent"),
        "source_authority": answer.get("source_authority", "none"),
        "has_quality_note": answer.get("has_quality_note", False) is True,
        **(
            {"answer_verified": answer.get("answer_verified")}
            if "answer_verified" in answer
            else {}
        ),
    }
    dependency = row.get("dependency")
    if not isinstance(dependency, Mapping):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_dependency_missing",
            "作答单元缺少显式依赖记录，不能默认猜成独立题。",
            409,
        )
    prior = dependency.get("prior_atomic_part_ids", [])
    if not isinstance(prior, list) or any(
        not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None
        for item in prior
    ):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "作答单元依赖列表格式不正确。", 409
        )
    if len(prior) != len(set(prior)):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "作答单元依赖列表不能含重复前序题。", 409
        )
    dependency_kind = dependency.get("kind")
    if not isinstance(dependency_kind, str) or not dependency_kind.strip():
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "作答单元依赖类型格式不正确。", 409
        )
    row["dependency"] = {
        "kind": dependency_kind,
        "prior_atomic_part_ids": list(prior),
        "explicit_prior_edge_count": len(prior),
        "status": dependency.get("status", "explicit_optimizer_input"),
    }
    row.setdefault("item_type", row.get("response_type", "unknown"))
    row.setdefault("visible_summary_zh", row.get("preview_text_zh"))
    row.setdefault("response_requirement_zh", None)
    row.setdefault("theme_chain_role", {"value": None, "status": "unknown"})
    row.setdefault(
        "detail_endpoint", row.get("expandable_content_ref")
    )
    aliases = row.get("alias_units", [])
    row["alias_units"] = deepcopy(aliases if isinstance(aliases, list) else [])
    return row


def _coerce_theme_group(
    value: Any, *, paper: Mapping[str, Any], theme_sequence: int
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题大题记录格式不正确。", 409
        )
    group = deepcopy(dict(value))
    theme_value = group.get("theme")
    if not isinstance(theme_value, Mapping):
        theme_value = {
            "id": group.get("theme_id"),
            "title": group.get("title"),
            "sequence": group.get("theme_order"),
            "sequence_status": "explicit_optimizer_input",
            "page_span": deepcopy(group.get("page_span", {})),
            "parent_chain_status": "complete",
        }
    theme = deepcopy(dict(theme_value))
    theme["id"] = _safe_id(theme.get("id"), "主题")
    theme.setdefault("title", "未命名主题")
    if type(theme.get("sequence")) is not int or theme["sequence"] < 1:
        theme["sequence"] = None
        theme["sequence_status"] = "unknown_pending_review"
    theme.setdefault("sequence_status", "explicit_optimizer_input")
    theme.setdefault("page_span", {})
    theme.setdefault("parent_chain_status", "complete")
    raw_rows = group.get("atomic_chain")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "完整主题缺少作答单元。", 409
        )
    printed_positions: dict[str, int] = {}
    atomic_positions: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for value_index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, Mapping):
            raise PaperBlueprintWorkbenchError(
                "theme_catalog_invalid", "主题作答单元格式不正确。", 409
            )
        printed_id = raw.get("printed_question_id", raw.get("printed_id"))
        if isinstance(printed_id, str) and printed_id not in printed_positions:
            explicit = raw.get("printed_sequence")
            printed_positions[printed_id] = (
                explicit
                if type(explicit) is int and explicit > 0
                else len(printed_positions) + 1
            )
        printed_key = str(printed_id)
        atomic_positions[printed_key] += 1
        rows.append(
            _coerce_atomic_row(
                raw,
                printed_sequence=printed_positions.get(printed_key, value_index),
                atomic_sequence=atomic_positions[printed_key],
            )
        )
    rows_by_printed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_printed[row["printed_question_id"]].append(row)
    derived_sequence_fields: list[dict[str, str]] = []
    for printed_id, printed_rows in rows_by_printed.items():
        known_printed = {
            row["printed_sequence"]
            for row in printed_rows
            if type(row["printed_sequence"]) is int
        }
        if len(known_printed) == 1:
            derived_printed = next(iter(known_printed))
        elif not known_printed and len(rows_by_printed) == 1:
            derived_printed = 1
        else:
            derived_printed = None
        if derived_printed is not None:
            for row in printed_rows:
                if row["printed_sequence"] is None:
                    row["printed_sequence"] = derived_printed
                    row["printed_sequence_status"] = "derived_unambiguous"
                    derived_sequence_fields.append(
                        {
                            "atomic_part_id": row["atomic_part_id"],
                            "field": "printed_sequence",
                        }
                    )
        known_atomic = {
            row["atomic_sequence_in_printed"]
            for row in printed_rows
            if type(row["atomic_sequence_in_printed"]) is int
        }
        missing_atomic_rows = [
            row
            for row in printed_rows
            if row["atomic_sequence_in_printed"] is None
        ]
        derived_atomic_values: list[int] = []
        if len(printed_rows) == 1 and missing_atomic_rows:
            derived_atomic_values = [1]
        elif len(missing_atomic_rows) == 1:
            remaining = set(range(1, len(printed_rows) + 1)) - known_atomic
            if len(remaining) == 1:
                derived_atomic_values = [next(iter(remaining))]
        if derived_atomic_values:
            for row, derived_atomic in zip(
                missing_atomic_rows, derived_atomic_values, strict=True
            ):
                row["atomic_sequence_in_printed"] = derived_atomic
                row["atomic_sequence_status"] = "derived_unambiguous"
                derived_sequence_fields.append(
                    {
                        "atomic_part_id": row["atomic_part_id"],
                        "field": "atomic_sequence_in_printed",
                    }
                )
    printed_sequence_by_id = {
        printed_id: {
            row["printed_sequence"] for row in printed_rows
        }
        for printed_id, printed_rows in rows_by_printed.items()
    }
    unique_printed_sequences = {
        next(iter(values))
        for values in printed_sequence_by_id.values()
        if len(values) == 1 and next(iter(values)) is not None
    }
    order_complete = (
        theme["sequence"] is not None
        and all(
            len(values) == 1 and next(iter(values)) is not None
            for values in printed_sequence_by_id.values()
        )
        and len(unique_printed_sequences) == len(rows_by_printed)
        and all(
            all(row["atomic_sequence_in_printed"] is not None for row in printed_rows)
            and len(
                {
                    row["atomic_sequence_in_printed"]
                    for row in printed_rows
                }
            )
            == len(printed_rows)
            for printed_rows in rows_by_printed.values()
        )
    )
    order_issue_atomic_ids = sorted(
        row["atomic_part_id"]
        for row in rows
        if row["printed_sequence"] is None
        or row["atomic_sequence_in_printed"] is None
    )
    if not order_complete and not order_issue_atomic_ids:
        order_issue_atomic_ids = sorted(row["atomic_part_id"] for row in rows)
    shared = group.get("shared_context", group.get("material_context"))
    if not isinstance(shared, Mapping):
        raise PaperBlueprintWorkbenchError(
            "theme_shared_context_missing",
            "完整主题缺少显式共享上下文记录，不能假定没有共同材料。",
            409,
        )
    shared = deepcopy(dict(shared))
    if "materials" not in shared:
        shared["materials"] = deepcopy(shared.get("shared_materials", []))
    if not isinstance(shared["materials"], list) or any(
        not isinstance(material, Mapping) for material in shared["materials"]
    ):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题共享材料列表格式不正确。", 409
        )
    shared.setdefault("context_summary_zh", None)
    material_count = shared.get("material_count", len(shared["materials"]))
    if type(material_count) is not int or material_count != len(shared["materials"]):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题共享材料数量与材料列表不一致。", 409
        )
    shared["material_count"] = material_count
    shared.setdefault("context_status", "explicit_optimizer_input")
    if any(
        row["dependency"]["kind"] == "shared_material_only" for row in rows
    ) and not shared["materials"]:
        raise PaperBlueprintWorkbenchError(
            "theme_shared_material_missing",
            "作答单元声明依赖主题共享材料，但主题没有可用共同材料。",
            409,
        )
    return {
        **group,
        "paper": deepcopy(dict(paper)),
        "theme": theme,
        "shared_context": shared,
        "atomic_chain": rows,
        "optimizer_order_evidence": {
            "complete": order_complete,
            "issue_atomic_ids": order_issue_atomic_ids,
            "derived_unambiguous": derived_sequence_fields,
        },
    }


def _normalize_catalog(
    value: Any, *, scope: str, snapshot_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题目录不是有效对象。", 409
        )
    wrapper = deepcopy(dict(value))
    if isinstance(wrapper.get("catalog"), Mapping):
        supplied_snapshot = wrapper.get("data_snapshot_id")
        if supplied_snapshot != snapshot_id or wrapper.get("scope") != scope:
            raise PaperBlueprintWorkbenchError(
                "theme_snapshot_stale", "主题目录与题篮快照或范围不一致。", 409
            )
        catalog = deepcopy(dict(wrapper["catalog"]))
    else:
        catalog = wrapper
        supplied_snapshot = catalog.pop("data_snapshot_id", None)
        if supplied_snapshot is not None and supplied_snapshot != snapshot_id:
            raise PaperBlueprintWorkbenchError(
                "theme_snapshot_stale", "主题目录来自另一题库快照。", 409
            )
    if catalog.get("scope") != scope or not isinstance(catalog.get("papers"), list):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_invalid", "主题目录范围或试卷列表不正确。", 409
        )
    integrity = catalog.get("integrity")
    required_integrity = {
        "hash_verified_on_read",
        "semantic_invariants_verified_on_read",
        "complete_scope_coverage",
        "no_duplicate_atomic_parts",
        "explicit_order_only",
        "dependency_edges_validated",
        "fail_closed",
    }
    if not isinstance(integrity, Mapping) or any(
        integrity.get(key) is not True for key in required_integrity
    ):
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_integrity_blocked",
            "主题目录未通过完整性检查，不能用于自动组卷。",
            409,
        )
    papers: list[dict[str, Any]] = []
    for paper_index, paper_entry_value in enumerate(catalog["papers"], start=1):
        if not isinstance(paper_entry_value, Mapping):
            raise PaperBlueprintWorkbenchError(
                "theme_catalog_invalid", "试卷记录格式不正确。", 409
            )
        paper_entry = deepcopy(dict(paper_entry_value))
        paper_value = paper_entry.get("paper")
        if not isinstance(paper_value, Mapping):
            paper_value = {
                "id": paper_entry.get("paper_id"),
                "title": paper_entry.get("title", "来源试卷待补"),
                "source_metadata": deepcopy(paper_entry.get("source_metadata", {})),
            }
        paper = deepcopy(dict(paper_value))
        paper["id"] = _safe_id(paper.get("id"), "来源试卷")
        paper.setdefault("title", "来源试卷待补")
        paper.setdefault("source_metadata", {})
        groups_value = paper_entry.get("theme_groups", paper_entry.get("themes"))
        if not isinstance(groups_value, list):
            raise PaperBlueprintWorkbenchError(
                "theme_catalog_invalid", "试卷缺少主题大题列表。", 409
            )
        groups = [
            _coerce_theme_group(group, paper=paper, theme_sequence=index)
            for index, group in enumerate(groups_value, start=1)
        ]
        papers.append({"paper": paper, "theme_groups": groups})
    normalized_catalog = deepcopy(catalog)
    normalized_catalog["papers"] = papers
    normalized_catalog.pop("candidate_parent_chain_overlay", None)
    return normalized_catalog, {
        "data_snapshot_id": snapshot_id,
        "scope": scope,
        "catalog": normalized_catalog,
    }


def _curriculum_catalog_indexes(value: Any) -> dict[str, Any]:
    indexes = {
        "volumes": {},
        "chapters": {},
        "sections": {},
        "volume_order": {},
        "chapter_order": {},
        "section_order": {},
    }
    if value is None:
        return indexes
    if not isinstance(value, Mapping) or not isinstance(value.get("volumes"), list):
        raise PaperBlueprintWorkbenchError(
            "curriculum_catalog_invalid", "教材目录格式不正确。", 409
        )
    for volume_index, volume in enumerate(value["volumes"], start=1):
        if not isinstance(volume, Mapping):
            raise PaperBlueprintWorkbenchError(
                "curriculum_catalog_invalid", "教材册记录格式不正确。", 409
            )
        volume_id = _safe_id(volume.get("volume_id"), "教材册")
        indexes["volumes"][volume_id] = _unknown_display(
            volume.get("display_label_zh", volume.get("volume_title")), "教材册待补"
        )
        indexes["volume_order"][volume_id] = volume_index
        chapters = volume.get("chapters", [])
        if not isinstance(chapters, list):
            raise PaperBlueprintWorkbenchError(
                "curriculum_catalog_invalid", "教材章列表格式不正确。", 409
            )
        for chapter_index, chapter in enumerate(chapters, start=1):
            if not isinstance(chapter, Mapping):
                raise PaperBlueprintWorkbenchError(
                    "curriculum_catalog_invalid", "教材章记录格式不正确。", 409
                )
            chapter_id = _safe_id(chapter.get("chapter_id"), "教材章")
            indexes["chapters"][chapter_id] = _unknown_display(
                chapter.get("display_label_zh", chapter.get("chapter_title")),
                "教材章待补",
            )
            indexes["chapter_order"][chapter_id] = chapter_index
            sections = chapter.get("sections", [])
            if not isinstance(sections, list):
                raise PaperBlueprintWorkbenchError(
                    "curriculum_catalog_invalid", "教材节列表格式不正确。", 409
                )
            for section_index, section in enumerate(sections, start=1):
                if not isinstance(section, Mapping):
                    raise PaperBlueprintWorkbenchError(
                        "curriculum_catalog_invalid", "教材节记录格式不正确。", 409
                    )
                section_key = _safe_id(
                    section.get("section_key", section.get("section_id")), "教材节"
                )
                indexes["sections"][section_key] = _unknown_display(
                    section.get("display_label_zh", section.get("section_title")),
                    "教材节待补",
                )
                indexes["section_order"][section_key] = section_index
    return indexes


def _normalize_curriculum_mapping(value: Any) -> dict[str, list[dict[str, Any]]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "curriculum_mapping_invalid", "教材题目映射格式不正确。", 409
        )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if isinstance(value.get("records"), list):
        for record in value["records"]:
            if not isinstance(record, Mapping):
                continue
            atomic_id = record.get("atomic_id", record.get("atomic_part_id"))
            entries = record.get("entries", [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, Mapping):
                    result[_safe_id(atomic_id, "教材映射题目")].append(
                        deepcopy(dict(entry))
                    )
    entries_value = value.get("entries")
    if isinstance(entries_value, list):
        for entry in entries_value:
            if not isinstance(entry, Mapping):
                continue
            atomic_id = entry.get("atomic_part_id", entry.get("atomic_id"))
            result[_safe_id(atomic_id, "教材映射题目")].append(deepcopy(dict(entry)))
    items_value = value.get("items")
    if isinstance(items_value, list):
        for item in items_value:
            if not isinstance(item, Mapping):
                continue
            atomic_id = _safe_id(
                item.get("atomic_id", item.get("atomic_part_id")), "教材映射题目"
            )
            result[atomic_id].append(
                {
                    "volume_ids": sorted(item.get("matched_volume_ids", [])),
                    "chapter_ids": sorted(item.get("matched_chapter_ids", [])),
                    "section_keys": sorted(item.get("matched_section_keys", [])),
                    "mapping_status": item.get("mapping_status"),
                }
            )
    return {
        key: sorted(value, key=canonical_sha256)
        for key, value in sorted(result.items())
    }


def _mapping_ids(edge: Mapping[str, Any], singular: str, plural: str) -> list[str]:
    value = edge.get(singular)
    if isinstance(value, str):
        return [value]
    values = edge.get(plural, [])
    return sorted(item for item in values if isinstance(item, str))


def _textbook_projection(
    edges: Sequence[Mapping[str, Any]], indexes: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[int, int, int]]:
    if not edges:
        return (
            {
                "status": "unknown",
                "display_zh": "教材节待核验",
                "mappings": [],
            },
            (10_000, 10_000, 10_000),
        )
    mappings: list[dict[str, Any]] = []
    orders: list[tuple[int, int, int]] = []
    for edge in edges:
        volumes = _mapping_ids(edge, "volume_id", "volume_ids")
        chapters = _mapping_ids(edge, "chapter_id", "chapter_ids")
        sections = _mapping_ids(edge, "section_key", "section_keys")
        volume_labels = [
            indexes["volumes"].get(item, "教材册待补") for item in volumes
        ]
        chapter_labels = [
            indexes["chapters"].get(item, "教材章待补") for item in chapters
        ]
        section_labels = [
            indexes["sections"].get(item, "教材节待补") for item in sections
        ]
        display_parts = [*volume_labels, *chapter_labels, *section_labels]
        mappings.append(
            {
                "volume_ids": volumes,
                "chapter_ids": chapters,
                "section_keys": sections,
                "mapping_status": edge.get("mapping_status", edge.get("status")),
                "display_zh": " / ".join(display_parts) if display_parts else "教材映射待补",
            }
        )
        orders.append(
            (
                min(
                    (indexes["volume_order"].get(item, 10_000) for item in volumes),
                    default=10_000,
                ),
                min(
                    (indexes["chapter_order"].get(item, 10_000) for item in chapters),
                    default=10_000,
                ),
                min(
                    (indexes["section_order"].get(item, 10_000) for item in sections),
                    default=10_000,
                ),
            )
        )
    unique = _dedupe_records(mappings)
    return (
        {
            "status": "explicit_mapping",
            "display_zh": "；".join(item["display_zh"] for item in unique),
            "mappings": unique,
        },
        min(orders),
    )


def _complete_question_search_pages(
    value: Mapping[str, Any] | None,
    *,
    loader: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    initial_payload: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if value is None:
        if loader is None:
            return None
        value = loader(deepcopy(dict(initial_payload)))
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "question_search_result_invalid", "题目检索结果格式不正确。", 409
        )
    first = deepcopy(dict(value))
    page = first.get("page")
    if not isinstance(page, Mapping):
        raise PaperBlueprintWorkbenchError(
            "question_search_page_required",
            "现有 question search 结果必须带完整分页状态，不能假定当前页就是全部命中主题。",
            409,
        )
    items = first.get("items", first.get("cards"))
    if not isinstance(items, list):
        raise PaperBlueprintWorkbenchError(
            "question_search_result_invalid", "题目检索结果缺少主题卡列表。", 409
        )
    returned = page.get("returned")
    total = page.get("total_theme_cards")
    has_more = page.get("has_more")
    next_cursor = page.get("next_cursor")
    if (
        type(returned) is not int
        or returned != len(items)
        or type(total) is not int
        or total < returned
        or not isinstance(has_more, bool)
        or (has_more and not isinstance(next_cursor, str))
        or (not has_more and next_cursor is not None)
    ):
        raise PaperBlueprintWorkbenchError(
            "question_search_page_invalid", "题目检索分页计数或游标不一致。", 409
        )
    stable_contract = {
        "schema_version": first.get("schema_version"),
        "scope": first.get("scope"),
        "q": first.get("q"),
        "filters": first.get("filters", {}),
        "data_snapshot_id": first.get("data_snapshot_id"),
        "curriculum": first.get("curriculum"),
        "integrity": first.get("integrity"),
    }
    combined_items = deepcopy(items)
    seen_cursors: set[str] = set()
    source_page_count = 1
    while has_more:
        if loader is None:
            raise PaperBlueprintWorkbenchError(
                "question_search_incomplete",
                "题目检索结果还有后续页；必须补齐全部主题卡后才能确定组卷是否可行。",
                409,
                details={"next_cursor": next_cursor, "total_theme_cards": total},
            )
        assert isinstance(next_cursor, str)
        if next_cursor in seen_cursors:
            raise PaperBlueprintWorkbenchError(
                "question_search_page_invalid", "题目检索分页游标形成循环。", 409
            )
        seen_cursors.add(next_cursor)
        continuation_payload = deepcopy(dict(initial_payload))
        continuation_payload.update(
            {
                "scope": first.get("scope"),
                "q": first.get("q"),
                "filters": deepcopy(first.get("filters", {})),
                "cursor": next_cursor,
            }
        )
        search_curriculum = first.get("curriculum")
        if isinstance(search_curriculum, Mapping) and isinstance(
            search_curriculum.get("selector"), Mapping
        ):
            continuation_payload["curriculum"] = deepcopy(
                dict(search_curriculum["selector"])
            )
        continuation = loader(continuation_payload)
        if not isinstance(continuation, Mapping):
            raise PaperBlueprintWorkbenchError(
                "question_search_result_invalid", "题目检索后续页格式不正确。", 409
            )
        continuation = deepcopy(dict(continuation))
        continuation_contract = {
            "schema_version": continuation.get("schema_version"),
            "scope": continuation.get("scope"),
            "q": continuation.get("q"),
            "filters": continuation.get("filters", {}),
            "data_snapshot_id": continuation.get("data_snapshot_id"),
            "curriculum": continuation.get("curriculum"),
            "integrity": continuation.get("integrity"),
        }
        if canonical_sha256(continuation_contract) != canonical_sha256(stable_contract):
            raise PaperBlueprintWorkbenchError(
                "question_search_page_drift",
                "题目检索后续页的查询条件、快照或完整性声明发生漂移。",
                409,
            )
        continuation_items = continuation.get(
            "items", continuation.get("cards")
        )
        continuation_page = continuation.get("page")
        if not isinstance(continuation_items, list) or not isinstance(
            continuation_page, Mapping
        ):
            raise PaperBlueprintWorkbenchError(
                "question_search_page_invalid", "题目检索后续页缺少题卡或分页状态。", 409
            )
        continuation_returned = continuation_page.get("returned")
        continuation_total = continuation_page.get("total_theme_cards")
        continuation_has_more = continuation_page.get("has_more")
        continuation_cursor = continuation_page.get("next_cursor")
        if (
            type(continuation_returned) is not int
            or continuation_returned != len(continuation_items)
            or continuation_total != total
            or not isinstance(continuation_has_more, bool)
            or (
                continuation_has_more
                and not isinstance(continuation_cursor, str)
            )
            or (not continuation_has_more and continuation_cursor is not None)
        ):
            raise PaperBlueprintWorkbenchError(
                "question_search_page_invalid", "题目检索后续页计数或游标不一致。", 409
            )
        combined_items.extend(deepcopy(continuation_items))
        has_more = continuation_has_more
        next_cursor = continuation_cursor
        source_page_count += 1
    if len(combined_items) != total:
        raise PaperBlueprintWorkbenchError(
            "question_search_incomplete",
            "题目检索分页结束后仍未收齐声明的全部主题卡。",
            409,
            details={"collected": len(combined_items), "total_theme_cards": total},
        )
    first["items"] = combined_items
    first.pop("cards", None)
    counts = first.get("counts")
    if isinstance(counts, Mapping):
        counts = deepcopy(dict(counts))
        counts["returned_theme_cards"] = len(combined_items)
        first["counts"] = counts
    first["page"] = {
        "limit": page.get("limit"),
        "returned": len(combined_items),
        "total_theme_cards": total,
        "has_more": False,
        "next_cursor": None,
        "aggregated_all_pages": True,
        "source_page_count": source_page_count,
    }
    return first


def _normalize_search_result(
    value: Any,
    *,
    scope: str,
    snapshot_id: str,
    theme_atomic_ids: Mapping[str, tuple[str, ...]],
    theme_order_complete: Mapping[str, bool],
    curriculum_selector: Mapping[str, str] | None,
    curriculum_mapping_present: bool,
) -> tuple[set[str], dict[str, tuple[str, ...]], dict[str, Any] | None]:
    if value is None:
        if curriculum_selector is not None and not curriculum_mapping_present:
            raise PaperBlueprintWorkbenchError(
                "question_search_required",
                "教材册/章/节约束必须由现有 question search 的显式映射结果支持。",
                409,
            )
        return set(theme_atomic_ids), {key: () for key in theme_atomic_ids}, None
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "question_search_result_invalid", "题目检索结果格式不正确。", 409
        )
    if value.get("data_snapshot_id") != snapshot_id:
        raise PaperBlueprintWorkbenchError(
            "question_search_snapshot_stale", "题目检索结果来自另一题库快照。", 409
        )
    search_scope = value.get("scope", scope)
    if search_scope not in {scope, "all"}:
        raise PaperBlueprintWorkbenchError(
            "question_search_scope_mismatch", "题目检索范围与组卷范围不一致。", 409
        )
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping) or any(
        integrity.get(key) is not True
        for key in (
            "theme_first",
            "atomic_matches_are_highlights_only",
            "complete_theme_chain_returned",
            "dependency_context_preserved",
        )
    ):
        raise PaperBlueprintWorkbenchError(
            "question_search_integrity_blocked",
            "检索结果没有声明完整主题题链、依赖保留与 atomic 仅高亮。",
            409,
        )
    cards = value.get("items", value.get("cards"))
    if not isinstance(cards, list):
        raise PaperBlueprintWorkbenchError(
            "question_search_result_invalid", "题目检索结果缺少主题卡列表。", 409
        )
    allowed: set[str] = set()
    highlights: dict[str, tuple[str, ...]] = {}
    for card in cards:
        if not isinstance(card, Mapping):
            raise PaperBlueprintWorkbenchError(
                "question_search_result_invalid", "主题检索卡格式不正确。", 409
            )
        if card.get("scope", scope) != scope:
            continue
        if card.get("group_kind", "theme") != "theme":
            continue
        theme_value = card.get("theme")
        theme_id = (
            theme_value.get("id")
            if isinstance(theme_value, Mapping)
            else card.get("theme_id")
        )
        theme_id = _safe_id(theme_id, "检索主题")
        expected = theme_atomic_ids.get(theme_id)
        if expected is None:
            raise PaperBlueprintWorkbenchError(
                "question_search_join_invalid", "检索主题不存在于当前主题目录。", 409
            )
        chain = card.get("atomic_chain")
        if not isinstance(chain, list):
            raise PaperBlueprintWorkbenchError(
                "question_search_join_invalid",
                "检索卡没有返回当前快照的完整主题题链。",
                409,
            )
        observed = tuple(
            item.get("atomic_part_id", item.get("atomic_id"))
            for item in chain
            if isinstance(item, Mapping)
        )
        exact_order_required = theme_order_complete.get(theme_id) is True
        if (
            exact_order_required
            and observed != expected
            or not exact_order_required
            and (set(observed) != set(expected) or len(observed) != len(expected))
        ):
            raise PaperBlueprintWorkbenchError(
                "question_search_join_invalid",
                "检索卡的完整主题题链与当前目录身份或已核验顺序不一致。",
                409,
            )
        matched = card.get("matched_atomic_ids", [])
        if not isinstance(matched, list) or any(
            not isinstance(item, str) or item not in expected for item in matched
        ):
            raise PaperBlueprintWorkbenchError(
                "question_search_join_invalid", "atomic 高亮不能连接到完整主题。", 409
            )
        ordered = tuple(item for item in expected if item in set(matched))
        if theme_id in allowed:
            raise PaperBlueprintWorkbenchError(
                "question_search_join_invalid", "检索结果重复返回同一完整主题。", 409
            )
        allowed.add(theme_id)
        highlights[theme_id] = ordered
    if curriculum_selector is not None:
        search_curriculum = value.get("curriculum")
        if isinstance(search_curriculum, Mapping):
            selector = search_curriculum.get("selector", {})
            if not isinstance(selector, Mapping) or any(
                selector.get(key) != expected
                for key, expected in curriculum_selector.items()
            ):
                raise PaperBlueprintWorkbenchError(
                    "question_search_curriculum_mismatch",
                    "教材约束与 question search 的显式映射选择器不一致。",
                    409,
                )
            if (
                search_curriculum.get("explicit_mapping_only") is not True
                or search_curriculum.get("knowledge_tag_inference_used") is not False
            ):
                raise PaperBlueprintWorkbenchError(
                    "question_search_curriculum_evidence_blocked",
                    "教材筛选必须声明仅使用显式映射，不能由 K 标签反推教材节。",
                    409,
                )
        elif not curriculum_mapping_present:
            raise PaperBlueprintWorkbenchError(
                "question_search_curriculum_missing",
                "检索结果没有教材显式映射证据，不能按 K 标签反推教材节。",
                409,
            )
    return allowed, highlights, deepcopy(dict(value))


def _overlay_section(value: Any, key: str) -> dict[str, Mapping[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PaperBlueprintWorkbenchError(
            "metadata_overlay_invalid", "组卷元数据覆盖层格式不正确。", 409
        )
    section = value.get(key, {})
    if not isinstance(section, Mapping):
        raise PaperBlueprintWorkbenchError(
            "metadata_overlay_invalid", f"元数据 {key} 覆盖层格式不正确。", 409
        )
    result: dict[str, Mapping[str, Any]] = {}
    for identity, record in section.items():
        normalized_id = _safe_id(identity, f"元数据 {key}")
        if not isinstance(record, Mapping):
            raise PaperBlueprintWorkbenchError(
                "metadata_overlay_invalid", "元数据覆盖记录格式不正确。", 409
            )
        result[normalized_id] = deepcopy(dict(record))
    return result


def _first_explicit(
    row: Mapping[str, Any], overlay: Mapping[str, Any], names: Sequence[str]
) -> Any:
    overlay_values = [overlay[name] for name in names if overlay.get(name) is not None]
    row_values = [row[name] for name in names if row.get(name) is not None]
    if len({canonical_sha256(item) for item in overlay_values}) > 1:
        raise PaperBlueprintWorkbenchError(
            "metadata_overlay_conflict", "同一覆盖记录给出了冲突字段值。", 409
        )
    if overlay_values:
        return overlay_values[0]
    if len({canonical_sha256(item) for item in row_values}) > 1:
        raise PaperBlueprintWorkbenchError(
            "theme_catalog_metadata_conflict", "主题目录中的显式字段值冲突。", 409
        )
    return row_values[0] if row_values else None


def _first_present(
    row: Mapping[str, Any], overlay: Mapping[str, Any], names: Sequence[str]
) -> Any:
    """Choose the first populated fallback field; unlike aliases, values may differ."""

    for source in (overlay, row):
        for name in names:
            if source.get(name) is not None:
                return source[name]
    return None


def _axis_values(
    row: Mapping[str, Any], overlay: Mapping[str, Any]
) -> tuple[dict[str, frozenset[str]], str | None]:
    labels = row.get("label_summary")
    labels = labels if isinstance(labels, Mapping) else {}
    result: dict[str, frozenset[str]] = {}
    knowledge: set[str] = set()
    primary = labels.get("primary_K")
    if isinstance(primary, str) and primary:
        knowledge.add(primary)
    supporting = labels.get("supporting_K", [])
    if isinstance(supporting, list):
        knowledge.update(item for item in supporting if isinstance(item, str) and item)
    overlay_knowledge = overlay.get("K")
    if overlay_knowledge is not None:
        candidate = (
            {overlay_knowledge}
            if isinstance(overlay_knowledge, str)
            else {item for item in overlay_knowledge if isinstance(item, str)}
        )
        if knowledge and candidate != knowledge:
            raise PaperBlueprintWorkbenchError(
                "metadata_overlay_conflict", "元数据覆盖层与题库 K 标签冲突。", 409
            )
        knowledge = candidate
    result["K"] = frozenset(knowledge)
    for axis in ("A", "C", "R", "RP"):
        base = labels.get(axis, [])
        base_values = {base} if isinstance(base, str) else {
            item for item in base if isinstance(item, str)
        }
        overlay_value = overlay.get(axis)
        if overlay_value is not None:
            candidate = (
                {overlay_value}
                if isinstance(overlay_value, str)
                else {item for item in overlay_value if isinstance(item, str)}
            )
            if base_values and candidate != base_values:
                raise PaperBlueprintWorkbenchError(
                    "metadata_overlay_conflict",
                    f"元数据覆盖层与题库 {axis} 标签冲突。",
                    409,
                )
            base_values = candidate
        result[axis] = frozenset(base_values)
    difficulty = _first_explicit(
        labels,
        overlay,
        ("cognitive_prelabel", "D", "difficulty"),
    )
    if difficulty is not None and difficulty not in _DIFFICULTIES:
        difficulty = None
    return result, difficulty


def _metric_from_atomic(
    row: Mapping[str, Any],
    overlay: Mapping[str, Any],
    *,
    kind: str,
) -> Decimal | None:
    if kind == "score":
        value = _first_explicit(row, overlay, ("score", "max_score"))
        return _decimal(value, "题目分值", allow_none=True)
    value = _first_explicit(
        row,
        overlay,
        ("estimated_time_minutes", "time_minutes"),
    )
    if value is not None:
        return _decimal(value, "预计用时", allow_none=True)
    seconds = _first_explicit(
        row,
        overlay,
        ("estimated_time_seconds", "time_seconds"),
    )
    if seconds is None:
        return None
    parsed = _decimal(seconds, "预计用时秒数")
    assert parsed is not None
    return parsed / Decimal(60)


def _curriculum_edge_matches(
    edge: Mapping[str, Any], selector: Mapping[str, str]
) -> bool:
    expected = {
        "volume_id": ("volume_id", "volume_ids"),
        "chapter_id": ("chapter_id", "chapter_ids"),
        "section": ("section_key", "section_keys"),
    }
    for key, (singular, plural) in expected.items():
        if key in selector and selector[key] not in _mapping_ids(edge, singular, plural):
            return False
    status = selector.get("mapping_status")
    return status is None or edge.get("mapping_status", edge.get("status")) == status


def _atomic_row_order_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    printed_sequence = row.get("printed_sequence")
    atomic_sequence = row.get("atomic_sequence_in_printed")
    return (
        printed_sequence if type(printed_sequence) is int else 1_000_000,
        atomic_sequence if type(atomic_sequence) is int else 1_000_000,
        str(row.get("atomic_part_id", "")),
    )


def _build_indexes_and_atomics(
    catalog: Mapping[str, Any],
    *,
    metadata_overlay: Any,
    curriculum_mapping: Mapping[str, list[dict[str, Any]]],
    curriculum_indexes: Mapping[str, Any],
    curriculum_selector: Mapping[str, str] | None,
    highlights: Mapping[str, tuple[str, ...]],
    hard: Mapping[str, Any],
    paper_answer_lines: int,
    paper_score_per_atomic: int | None,
    paper_time_per_atomic_minutes: float | None,
) -> tuple[
    dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    dict[str, _AtomicInfo],
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    atomic_overlay = _overlay_section(metadata_overlay, "atomics")
    theme_index: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    atomic_index: dict[str, _AtomicInfo] = {}
    printed_index_lists: dict[str, list[str]] = defaultdict(list)
    theme_atomic_ids: dict[str, tuple[str, ...]] = {}
    cluster_by_atomic: dict[str, str] = {}
    for cluster_id, atomic_ids in hard["dedup_clusters"].items():
        for atomic_id in atomic_ids:
            cluster_by_atomic[atomic_id] = cluster_id
    for paper_entry in catalog["papers"]:
        paper = paper_entry["paper"]
        paper_id = paper["id"]
        for group in paper_entry["theme_groups"]:
            theme = group["theme"]
            theme_id = theme["id"]
            if theme_id in theme_index:
                raise PaperBlueprintWorkbenchError(
                    "theme_catalog_invalid", "主题目录出现重复主题标识。", 409
                )
            theme_index[theme_id] = (paper, group)
            rows = sorted(group["atomic_chain"], key=_atomic_row_order_key)
            ids: list[str] = []
            for row in rows:
                atomic_id = row["atomic_part_id"]
                if atomic_id in atomic_index:
                    raise PaperBlueprintWorkbenchError(
                        "theme_catalog_invalid", "主题目录重复使用同一作答单元。", 409
                    )
                overlay = atomic_overlay.get(atomic_id, {})
                axes, difficulty = _axis_values(row, overlay)
                score = (
                    Decimal(paper_score_per_atomic)
                    if paper_score_per_atomic is not None
                    else _metric_from_atomic(row, overlay, kind="score")
                )
                time_minutes = (
                    Decimal(str(paper_time_per_atomic_minutes))
                    if paper_time_per_atomic_minutes is not None
                    else _metric_from_atomic(row, overlay, kind="time")
                )
                cluster = _first_explicit(
                    row, overlay, ("duplicate_cluster_id", "dedup_cluster_id")
                )
                declared_cluster = cluster_by_atomic.get(atomic_id)
                if cluster is not None:
                    cluster = _safe_id(cluster, "去重簇")
                if declared_cluster is not None and cluster not in {None, declared_cluster}:
                    raise PaperBlueprintWorkbenchError(
                        "dedup_cluster_conflict", "题目去重簇输入相互冲突。", 409
                    )
                cluster = declared_cluster or cluster
                edges = list(curriculum_mapping.get(atomic_id, []))
                overlay_textbook = overlay.get("textbook_section")
                if not edges and isinstance(overlay_textbook, Mapping):
                    edges = [
                        {
                            "volume_id": overlay_textbook.get("volume_id"),
                            "chapter_id": overlay_textbook.get("chapter_id"),
                            "section_key": overlay_textbook.get("section_key"),
                            "mapping_status": overlay_textbook.get(
                                "mapping_status", "complete"
                            ),
                        }
                    ]
                if (
                    not edges
                    and curriculum_selector is not None
                    and atomic_id in set(highlights.get(theme_id, ()))
                ):
                    edges = [
                        {
                            "volume_id": curriculum_selector.get("volume_id"),
                            "chapter_id": curriculum_selector.get("chapter_id"),
                            "section_key": curriculum_selector.get("section"),
                            "mapping_status": curriculum_selector.get("mapping_status"),
                        }
                    ]
                edges = [
                    edge
                    for edge in edges
                    if curriculum_selector is None
                    or _curriculum_edge_matches(edge, curriculum_selector)
                ]
                textbook, curriculum_order = _textbook_projection(
                    edges, curriculum_indexes
                )
                answer = row.get("answer")
                answer = deepcopy(dict(answer)) if isinstance(answer, Mapping) else {}
                item_type = _first_explicit(
                    row, overlay, ("item_type", "response_type")
                )
                item_type = item_type if isinstance(item_type, str) and item_type else "unknown"
                lines = _first_explicit(
                    row, overlay, ("answer_space_lines",)
                )
                if lines is None:
                    lines = paper_answer_lines
                if type(lines) is not int or not 0 <= lines <= 30:
                    raise PaperBlueprintWorkbenchError(
                        "metadata_overlay_invalid", "题目答题空间须为 0—30 行。", 409
                    )
                content_ref = _first_explicit(
                    row,
                    overlay,
                    ("expandable_content_ref", "detail_endpoint"),
                )
                if content_ref is not None and (
                    not isinstance(content_ref, str)
                    or not content_ref.startswith("/api/")
                ):
                    raise PaperBlueprintWorkbenchError(
                        "metadata_overlay_invalid",
                        "可展开内容引用必须是本机 API 相对路径。",
                        409,
                    )
                preview_text = _first_present(
                    row,
                    overlay,
                    ("preview_text_zh", "visible_summary_zh", "response_requirement_zh"),
                )
                preview_content_available = content_ref is not None
                preview_text = _unknown_display(preview_text, "题面内容待展开")
                info = _AtomicInfo(
                    atomic_id=atomic_id,
                    printed_id=row["printed_question_id"],
                    theme_id=theme_id,
                    paper_id=paper_id,
                    row=row,
                    score=score,
                    time_minutes=time_minutes,
                    duplicate_cluster_id=cluster,
                    axes=axes,
                    difficulty=difficulty,
                    item_type=item_type,
                    answer=answer,
                    textbook_edges=tuple(deepcopy(edges)),
                    textbook_display=textbook,
                    curriculum_order=curriculum_order,
                    answer_space_lines=lines,
                    expandable_content_ref=content_ref,
                    preview_text_zh=preview_text,
                    preview_content_available=preview_content_available,
                )
                atomic_index[atomic_id] = info
                ids.append(atomic_id)
                printed_index_lists[info.printed_id].append(atomic_id)
            theme_atomic_ids[theme_id] = tuple(ids)
    printed_index = {
        key: tuple(value) for key, value in sorted(printed_index_lists.items())
    }
    unknown_cluster_atomics = sorted(set(cluster_by_atomic) - set(atomic_index))
    if unknown_cluster_atomics:
        raise PaperBlueprintWorkbenchError(
            "dedup_cluster_reference_missing",
            "去重簇引用了当前题库快照中不存在的作答单元。",
            409,
            details={"missing_atomic_ids": unknown_cluster_atomics},
        )
    return theme_index, atomic_index, printed_index, theme_atomic_ids


def _answer_is_eligible(
    answer: Mapping[str, Any], rule: Mapping[str, Any] | None
) -> bool:
    if rule is None:
        return True
    availability = answer.get("availability")
    authority = answer.get("source_authority")
    return availability in rule["allowed_availability"] and (
        not rule["allowed_authorities"] or authority in rule["allowed_authorities"]
    )


def _dependency_closure_for_theme(
    atomic_ids: Sequence[str],
    atomic_index: Mapping[str, _AtomicInfo],
    target_ids: Sequence[str],
) -> tuple[str, ...]:
    positions = {atomic_id: index for index, atomic_id in enumerate(atomic_ids)}
    included: set[str] = set()
    visiting: set[str] = set()

    def visit(atomic_id: str) -> None:
        if atomic_id in included:
            return
        if atomic_id in visiting:
            raise PaperBlueprintWorkbenchError(
                "dependency_cycle", "题目依赖形成循环，不能组卷。", 409
            )
        info = atomic_index.get(atomic_id)
        if info is None or atomic_id not in positions:
            raise PaperBlueprintWorkbenchError(
                "dependency_cross_theme_or_missing",
                "依赖闭包缺少同主题前序题，不能组卷。",
                409,
            )
        visiting.add(atomic_id)
        prior = info.row.get("dependency", {}).get("prior_atomic_part_ids", [])
        for prior_id in prior:
            if prior_id not in positions or positions[prior_id] >= positions[atomic_id]:
                raise PaperBlueprintWorkbenchError(
                    "dependency_not_prior",
                    "依赖关系指向其他主题、本题或后问，不能组卷。",
                    409,
                )
            visit(prior_id)
        visiting.remove(atomic_id)
        included.add(atomic_id)

    for target in target_ids:
        visit(target)
    return tuple(atomic_id for atomic_id in atomic_ids if atomic_id in included)


def _resolve_general_item_ids(
    values: Sequence[str],
    *,
    theme_index: Mapping[str, Any],
    printed_index: Mapping[str, tuple[str, ...]],
    atomic_index: Mapping[str, _AtomicInfo],
    label_zh: str,
) -> tuple[set[str], set[str]]:
    themes: set[str] = set()
    atomics: set[str] = set()
    for identity in values:
        matches = sum(
            (
                identity in theme_index,
                identity in printed_index,
                identity in atomic_index,
            )
        )
        if matches == 0:
            raise PaperBlueprintWorkbenchError(
                "constraint_item_not_found",
                f"{label_zh}“{identity}”不在当前题库快照中。",
                409,
            )
        if matches > 1:
            raise PaperBlueprintWorkbenchError(
                "constraint_item_ambiguous",
                f"{label_zh}“{identity}”在多个层级重名，必须指定 theme/atomic 字段。",
                409,
            )
        if identity in theme_index:
            themes.add(identity)
        elif identity in printed_index:
            atomics.update(printed_index[identity])
        else:
            atomics.add(identity)
    return themes, atomics


def _knowledge_order(infos: Sequence[_AtomicInfo]) -> int:
    values: list[int] = []
    for info in infos:
        for label in info.axes["K"]:
            match = re.fullmatch(r"K([0-9]{1,3})", label)
            if match:
                values.append(int(match.group(1)))
    return min(values, default=10_000)


def _difficulty_average(infos: Sequence[_AtomicInfo]) -> float:
    values = [
        int(info.difficulty[1:])
        for info in infos
        if isinstance(info.difficulty, str)
        and re.fullmatch(r"D[1-5]", info.difficulty)
    ]
    return sum(values) / len(values) if values else 10_000.0


def _response_order(infos: Sequence[_AtomicInfo]) -> int:
    return min(
        (_RESPONSE_TYPE_ORDER.get(info.item_type, 1_000) for info in infos),
        default=1_000,
    )


def _theme_source_year(paper: Mapping[str, Any]) -> str:
    metadata = paper.get("source_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    year = metadata.get("year")
    if type(year) is int and 1900 <= year <= 2100:
        return str(year)
    if isinstance(year, str) and re.fullmatch(r"(?:19|20|21)[0-9]{2}", year):
        return year
    return "unknown"


def _build_theme_units(
    *,
    request: Mapping[str, Any],
    theme_index: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    atomic_index: Mapping[str, _AtomicInfo],
    printed_index: Mapping[str, tuple[str, ...]],
    theme_atomic_ids: Mapping[str, tuple[str, ...]],
    allowed_theme_ids: set[str],
    highlights: Mapping[str, tuple[str, ...]],
    metadata_overlay: Any,
) -> tuple[
    list[_ThemeUnit],
    set[str],
    set[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    hard = request["hard_constraints"]
    required_themes = set(hard["required_theme_ids"])
    required_atomics = set(hard["required_atomic_ids"])
    excluded_themes = set(hard["excluded_theme_ids"])
    excluded_atomics = set(hard["excluded_atomic_ids"])
    general_required_themes, general_required_atomics = _resolve_general_item_ids(
        hard["required_item_ids"],
        theme_index=theme_index,
        printed_index=printed_index,
        atomic_index=atomic_index,
        label_zh="必选题",
    )
    general_excluded_themes, general_excluded_atomics = _resolve_general_item_ids(
        hard["excluded_item_ids"],
        theme_index=theme_index,
        printed_index=printed_index,
        atomic_index=atomic_index,
        label_zh="排除题",
    )
    required_themes |= general_required_themes
    required_atomics |= general_required_atomics
    excluded_themes |= general_excluded_themes
    excluded_atomics |= general_excluded_atomics

    for identity in required_themes | excluded_themes:
        if identity not in theme_index:
            raise PaperBlueprintWorkbenchError(
                "constraint_item_not_found", "必选或排除主题不在当前快照中。", 409
            )
    for identity in required_atomics | excluded_atomics:
        if identity not in atomic_index:
            raise PaperBlueprintWorkbenchError(
                "constraint_item_not_found", "必选或排除题不在当前快照中。", 409
            )
    conflicts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if required_themes & excluded_themes or required_atomics & excluded_atomics:
        conflicts.append(
            _conflict(
                "required_excluded_conflict",
                "同一主题或作答单元同时被设为必选和排除。",
                theme_ids=sorted(required_themes & excluded_themes),
                atomic_ids=sorted(required_atomics & excluded_atomics),
            )
        )
    if not required_themes <= allowed_theme_ids:
        conflicts.append(
            _conflict(
                "required_theme_outside_search",
                "部分必选主题不满足当前关键词、教材或筛选结果。",
                theme_ids=sorted(required_themes - allowed_theme_ids),
            )
        )
    required_atomic_themes = {
        atomic_index[atomic_id].theme_id for atomic_id in required_atomics
    }
    if not required_atomic_themes <= allowed_theme_ids:
        conflicts.append(
            _conflict(
                "required_atomic_outside_search",
                "部分必选题不满足当前关键词、教材或筛选结果。",
                atomic_ids=sorted(
                    atomic_id
                    for atomic_id in required_atomics
                    if atomic_index[atomic_id].theme_id not in allowed_theme_ids
                ),
            )
        )
    if conflicts:
        return [], required_themes, required_atomics, conflicts, gaps

    theme_overlay = _overlay_section(metadata_overlay, "themes")
    mode = request["mode"]
    selection_unit = request["preferences"]["selection_unit"]
    source_years = set(hard["source_years"])
    answer_rule = hard["answer_eligibility"]
    curriculum_selector = request["curriculum"]
    need_score = mode == MODE_MOCK_EXAM
    need_time = mode == MODE_MOCK_EXAM
    need_cluster = hard["no_duplicate_clusters"]
    units: list[_ThemeUnit] = []
    for theme_id in sorted(allowed_theme_ids):
        paper, group = theme_index[theme_id]
        all_ids = theme_atomic_ids[theme_id]
        if theme_id in excluded_themes:
            continue
        order_evidence = group.get("optimizer_order_evidence")
        if not isinstance(order_evidence, Mapping) or order_evidence.get(
            "complete"
        ) is not True:
            issue_ids = (
                order_evidence.get("issue_atomic_ids", list(all_ids))
                if isinstance(order_evidence, Mapping)
                else list(all_ids)
            )
            gaps.append(
                _gap(
                    "source_order_metadata_missing",
                    "该完整主题缺少可证明的 printed/atomic 显式顺序，不能按列表位置猜题号。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=issue_ids,
                    missing_fields=["printed_or_atomic_source_order"],
                )
            )
            continue
        targets = list(highlights.get(theme_id, ()))
        targets.extend(
            atomic_id
            for atomic_id in all_ids
            if atomic_id in required_atomics and atomic_id not in targets
        )
        whole_theme = (
            mode == MODE_MOCK_EXAM
            or selection_unit == "theme"
            or theme_id in required_themes
        )
        if whole_theme:
            final_ids = tuple(all_ids)
            selections = (
                {
                    "scope": request["scope"],
                    "selection_unit": "theme",
                    "theme_id": theme_id,
                    "target_atomic_id": None,
                    "expected_data_snapshot_id": request["data_snapshot_id"],
                },
            )
        else:
            if not targets:
                continue
            ordered_targets = tuple(
                atomic_id for atomic_id in all_ids if atomic_id in set(targets)
            )
            final_ids = _dependency_closure_for_theme(
                all_ids, atomic_index, ordered_targets
            )
            selections = tuple(
                {
                    "scope": request["scope"],
                    "selection_unit": "dependency",
                    "theme_id": theme_id,
                    "target_atomic_id": atomic_id,
                    "expected_data_snapshot_id": request["data_snapshot_id"],
                }
                for atomic_id in ordered_targets
            )
        if set(final_ids) & excluded_atomics:
            if theme_id in required_themes or set(final_ids) & required_atomics:
                conflicts.append(
                    _conflict(
                        "required_dependency_excluded",
                        "必选主题或题目的完整依赖闭包包含已排除题。",
                        theme_id=theme_id,
                        atomic_ids=sorted(set(final_ids) & excluded_atomics),
                    )
                )
            continue
        year = _theme_source_year(paper)
        if source_years and year not in source_years:
            continue
        infos = [atomic_index[atomic_id] for atomic_id in final_ids]
        missing_preview_content = [
            info.atomic_id for info in infos if not info.preview_content_available
        ]
        if missing_preview_content:
            gaps.append(
                _gap(
                    "preview_content_missing",
                    "整卷预览缺少可展开的完整题面引用，不能冻结为可导出预览。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=missing_preview_content,
                    missing_fields=["expandable_content_ref"],
                )
            )
            continue
        if curriculum_selector is not None and not any(
            info.textbook_edges for info in infos
        ):
            continue
        ineligible_answers = [
            info.atomic_id
            for info in infos
            if not _answer_is_eligible(info.answer, answer_rule)
        ]
        if ineligible_answers:
            gaps.append(
                _gap(
                    "answer_eligibility_missing",
                    "该主题含不满足教师答案资格的作答单元。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=ineligible_answers,
                    missing_fields=["answer_eligibility"],
                )
            )
            continue
        missing_score = [info.atomic_id for info in infos if info.score is None]
        missing_time = [
            info.atomic_id for info in infos if info.time_minutes is None
        ]
        missing_cluster = [
            info.atomic_id
            for info in infos
            if info.duplicate_cluster_id is None
        ]
        if need_score and missing_score:
            gaps.append(
                _gap(
                    "score_metadata_missing",
                    "精确总分约束缺少逐题分值，不能按题数猜分。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=missing_score,
                    missing_fields=["score"],
                )
            )
            continue
        if need_time and missing_time:
            gaps.append(
                _gap(
                    "time_metadata_missing",
                    "时长约束缺少逐题预计用时，不能按题号或题型猜测。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=missing_time,
                    missing_fields=["estimated_time_minutes"],
                )
            )
            continue
        if need_cluster and missing_cluster:
            gaps.append(
                _gap(
                    "dedup_cluster_metadata_missing",
                    "去重簇硬约束缺少逐题簇标记，不能假定未标题目均唯一。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=missing_cluster,
                    missing_fields=["duplicate_cluster_id"],
                )
            )
            continue
        clusters = [
            info.duplicate_cluster_id
            for info in infos
            if info.duplicate_cluster_id is not None
        ]
        if need_cluster and len(clusters) != len(set(clusters)):
            gaps.append(
                _gap(
                    "dedup_cluster_internal_conflict",
                    "同一候选主题内部已经包含重复簇题目。",
                    affected_theme_ids=[theme_id],
                    affected_atomic_ids=list(final_ids),
                )
            )
            continue
        theme_meta = theme_overlay.get(theme_id, {})
        progression = theme_meta.get("content_progression")
        if not isinstance(progression, (int, float)) or isinstance(progression, bool):
            progression = 10_000
        curriculum_order = min(
            (info.curriculum_order for info in infos),
            default=(10_000, 10_000, 10_000),
        )
        source_sequence = group["theme"].get("sequence")
        if type(source_sequence) is not int:
            source_sequence = 10_000
        natural_key = (
            progression,
            curriculum_order,
            _knowledge_order(infos),
            _response_order(infos),
            _difficulty_average(infos),
            source_sequence,
            theme_id,
        )
        units.append(
            _ThemeUnit(
                scope=request["scope"],
                paper=paper,
                group=group,
                theme_id=theme_id,
                title_zh=_unknown_display(group["theme"].get("title"), "未命名主题"),
                final_atomic_ids=final_ids,
                highlighted_atomic_ids=tuple(
                    atomic_id
                    for atomic_id in all_ids
                    if atomic_id in set(highlights.get(theme_id, ()))
                ),
                basket_selections=selections,
                source_year=year,
                natural_sort_key=natural_key,
            )
        )
    units.sort(key=lambda unit: unit.natural_sort_key)
    available_theme_ids = {unit.theme_id for unit in units}
    available_atomic_ids = {
        atomic_id for unit in units for atomic_id in unit.final_atomic_ids
    }
    missing_required_themes = required_themes - available_theme_ids
    missing_required_atomics = required_atomics - available_atomic_ids
    if missing_required_themes or missing_required_atomics:
        conflicts.append(
            _conflict(
                "required_items_unavailable",
                "部分必选题在来源年份、答案资格、教材映射或元数据硬过滤后不可用。",
                theme_ids=sorted(missing_required_themes),
                atomic_ids=sorted(missing_required_atomics),
            )
        )
    return units, required_themes, required_atomics, conflicts, _dedupe_records(gaps)


def _order_units(
    units: Sequence[_ThemeUnit],
    *,
    ordering: Mapping[str, Any],
    theme_overlay: Mapping[str, Mapping[str, Any]],
) -> tuple[_ThemeUnit, ...]:
    by_id = {unit.theme_id: unit for unit in units}
    selected = set(by_id)
    edges: set[tuple[str, str]] = set()
    teacher = [
        theme_id
        for theme_id in ordering["teacher_theme_order"]
        if theme_id in selected
    ]
    # A full drag order is the teacher's final paper-editing decision.  The
    # curriculum/type/difficulty progression is the deterministic default, not
    # a hidden rule that may undo an explicit full reorder.
    if len(teacher) == len(units) and set(teacher) == selected:
        return tuple(by_id[theme_id] for theme_id in teacher)
    edges.update(itertools.pairwise(teacher))
    edges.update(
        (before, after)
        for before, after in ordering["prerequisite_edges"]
        if before in selected and after in selected
    )
    for theme_id in selected:
        metadata = theme_overlay.get(theme_id, {})
        prerequisites = metadata.get("prerequisite_theme_ids", [])
        if prerequisites is None:
            prerequisites = []
        if not isinstance(prerequisites, list) or any(
            not isinstance(item, str) for item in prerequisites
        ):
            raise PaperBlueprintWorkbenchError(
                "metadata_overlay_invalid", "主题先修列表格式不正确。", 409
            )
        edges.update(
            (prerequisite, theme_id)
            for prerequisite in prerequisites
            if prerequisite in selected
        )
    indegree = {theme_id: 0 for theme_id in selected}
    outgoing: dict[str, set[str]] = {theme_id: set() for theme_id in selected}
    for before, after in edges:
        if after not in outgoing[before]:
            outgoing[before].add(after)
            indegree[after] += 1
    teacher_rank = {theme_id: index for index, theme_id in enumerate(teacher)}

    def ready_key(theme_id: str) -> tuple[Any, ...]:
        return (
            teacher_rank.get(theme_id, 10_000),
            by_id[theme_id].natural_sort_key,
        )

    ready = sorted(
        (theme_id for theme_id, degree in indegree.items() if degree == 0),
        key=ready_key,
    )
    ordered: list[_ThemeUnit] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for following in sorted(outgoing[current], key=ready_key):
            indegree[following] -= 1
            if indegree[following] == 0:
                ready.append(following)
                ready.sort(key=ready_key)
    if len(ordered) != len(units):
        raise PaperBlueprintWorkbenchError(
            "theme_order_cycle",
            "教师拖动顺序与教材先修关系形成循环，请调整顺序或先修边。",
            409,
        )
    return tuple(ordered)


def _coverage_for_units(
    units: Sequence[_ThemeUnit], atomic_index: Mapping[str, _AtomicInfo]
) -> dict[str, Any]:
    atomic_ids: list[str] = []
    for unit in units:
        atomic_ids.extend(unit.final_atomic_ids)
    if len(atomic_ids) != len(set(atomic_ids)):
        raise PaperBlueprintWorkbenchError(
            "duplicate_atomic_part", "候选卷重复装入同一作答单元。", 409
        )
    infos = [atomic_index[atomic_id] for atomic_id in atomic_ids]
    axis_counts = {axis: Counter() for axis in _AXES}
    difficulty = Counter()
    response_types = Counter()
    years = Counter(unit.source_year for unit in units)
    answers = Counter()
    clusters = Counter()
    for info in infos:
        for axis in _AXES:
            axis_counts[axis].update(info.axes[axis])
        difficulty[info.difficulty or "unknown"] += 1
        response_types[info.item_type] += 1
        answers[
            f"{info.answer.get('availability', 'unknown')}|"
            f"{info.answer.get('source_authority', 'unknown')}"
        ] += 1
        if info.duplicate_cluster_id is not None:
            clusters[info.duplicate_cluster_id] += 1
    scores = [info.score for info in infos]
    times = [info.time_minutes for info in infos]
    total_score = sum((item for item in scores if item is not None), Decimal(0))
    total_time = sum((item for item in times if item is not None), Decimal(0))
    return {
        "totals": {
            "theme_count": len(units),
            "atomic_count": len(infos),
            "total_score": _number(total_score) if all(item is not None for item in scores) else None,
            "score_complete": all(item is not None for item in scores),
            "estimated_time_minutes": (
                _number(total_time) if all(item is not None for item in times) else None
            ),
            "time_complete": all(item is not None for item in times),
        },
        "axis_coverage": {
            axis: dict(sorted(counts.items())) for axis, counts in axis_counts.items()
        },
        "difficulty_distribution": dict(sorted(difficulty.items())),
        "difficulty_basis": "atomic_part_count",
        "response_type_distribution": dict(sorted(response_types.items())),
        "source_year_distribution": dict(sorted(years.items())),
        "answer_distribution": dict(sorted(answers.items())),
        "duplicate_cluster_distribution": dict(sorted(clusters.items())),
        "textbook_mapping": {
            "mapped_atomic_count": sum(
                info.textbook_display["status"] == "explicit_mapping" for info in infos
            ),
            "unknown_atomic_count": sum(
                info.textbook_display["status"] != "explicit_mapping" for info in infos
            ),
        },
    }


def _hard_failures(
    coverage: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    selected_theme_ids: set[str],
    selected_atomic_ids: set[str],
) -> list[str]:
    hard = request["hard_constraints"]
    failures: list[str] = []
    if not set(hard["required_theme_ids"]) <= selected_theme_ids:
        failures.append("required_theme_missing")
    if not set(hard["required_atomic_ids"]) <= selected_atomic_ids:
        failures.append("required_atomic_missing")
    totals = coverage["totals"]
    if request["mode"] == MODE_MOCK_EXAM:
        paper = request["paper"]
        if totals["theme_count"] != paper["theme_count"]:
            failures.append("theme_count_mismatch")
        if not totals["score_complete"] or totals["total_score"] != paper["total_score"]:
            failures.append("total_score_mismatch")
        if not totals["time_complete"]:
            failures.append("duration_metadata_missing")
        elif paper["duration_rule"] == "exact" and not math.isclose(
            float(totals["estimated_time_minutes"]),
            float(paper["duration_minutes"]),
            abs_tol=1e-9,
        ):
            failures.append("duration_mismatch")
        elif paper["duration_rule"] == "max" and float(
            totals["estimated_time_minutes"]
        ) > float(paper["duration_minutes"]):
            failures.append("duration_exceeded")
    for axis, rules in hard["coverage"].items():
        counts = coverage["axis_coverage"][axis]
        for label, rule in rules.items():
            observed = counts.get(label, 0)
            if observed < rule.minimum or (
                rule.maximum is not None and observed > rule.maximum
            ):
                failures.append(f"coverage_{axis}_{label}")
    difficulty = coverage["difficulty_distribution"]
    for label, rule in hard["difficulty_distribution"].items():
        observed = difficulty.get(label, 0)
        if observed < rule.minimum or (
            rule.maximum is not None and observed > rule.maximum
        ):
            failures.append(f"difficulty_{label}")
    if hard["no_duplicate_clusters"] and any(
        count > 1 for count in coverage["duplicate_cluster_distribution"].values()
    ):
        failures.append("duplicate_cluster_conflict")
    return failures


def _coverage_surplus(
    coverage: Mapping[str, Any], hard: Mapping[str, Any]
) -> int:
    surplus = 0
    for axis, rules in hard["coverage"].items():
        counts = coverage["axis_coverage"][axis]
        for label, rule in rules.items():
            surplus += max(0, counts.get(label, 0) - rule.minimum)
    return surplus


def _objective(
    ordered_units: Sequence[_ThemeUnit],
    coverage: Mapping[str, Any],
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    preferences = request["preferences"]
    totals = coverage["totals"]
    target_atomic = preferences["target_atomic_count"]
    target_score = preferences["target_total_score"]
    target_time = preferences["target_duration_minutes"]
    target_themes = preferences["target_theme_count"]
    atomic_distance = (
        abs(totals["atomic_count"] - target_atomic) if target_atomic is not None else 0
    )
    if target_time is None:
        time_distance = 0.0
    elif totals["estimated_time_minutes"] is None:
        time_distance = float("inf")
    else:
        time_distance = abs(float(totals["estimated_time_minutes"]) - float(target_time))
    if target_score is None:
        score_distance = 0.0
    elif totals["total_score"] is None:
        score_distance = float("inf")
    else:
        score_distance = abs(float(totals["total_score"]) - float(target_score))
    theme_distance = (
        abs(totals["theme_count"] - target_themes) if target_themes is not None else 0
    )
    source_diversity = len({unit.paper.get("id") for unit in ordered_units})
    surplus = _coverage_surplus(coverage, request["hard_constraints"])
    signature = tuple(unit.theme_id for unit in ordered_units)
    objective = {
        "daily_atomic_target_distance": atomic_distance,
        "daily_score_target_distance": (
            None if math.isinf(score_distance) else score_distance
        ),
        "daily_time_target_distance_minutes": (
            None if math.isinf(time_distance) else time_distance
        ),
        "daily_theme_target_distance": theme_distance,
        "coverage_surplus": surplus,
        "source_diversity": source_diversity,
        "deterministic_theme_signature": list(signature),
    }
    if request["mode"] == MODE_DAILY_PRACTICE:
        key = (
            atomic_distance,
            score_distance,
            time_distance,
            theme_distance,
            totals["theme_count"] if target_themes is None else 0,
            surplus,
            -source_diversity,
            signature,
        )
    else:
        key = (surplus, -source_diversity, signature)
    return objective, key


def _enumerate_feasible_sets(
    units: Sequence[_ThemeUnit],
    *,
    request: Mapping[str, Any],
    atomic_index: Mapping[str, _AtomicInfo],
    required_theme_ids: set[str],
    required_atomic_ids: set[str],
    theme_overlay: Mapping[str, Mapping[str, Any]],
) -> tuple[list[tuple[tuple[_ThemeUnit, ...], dict[str, Any], dict[str, Any], tuple[Any, ...]]], dict[str, Any]]:
    required_indexes = {
        index
        for index, unit in enumerate(units)
        if unit.theme_id in required_theme_ids
        or bool(set(unit.final_atomic_ids) & required_atomic_ids)
    }
    optional_indexes = [
        index for index in range(len(units)) if index not in required_indexes
    ]
    required_count = len(required_indexes)
    if request["mode"] == MODE_MOCK_EXAM:
        target_count = request["paper"]["theme_count"]
        choose_count = target_count - required_count
        sizes = [choose_count] if 0 <= choose_count <= len(optional_indexes) else []
    else:
        sizes = list(range(len(optional_indexes) + 1))
    states_visited = 0
    truncated = False
    failure_counts: Counter[str] = Counter()
    feasible: list[
        tuple[tuple[_ThemeUnit, ...], dict[str, Any], dict[str, Any], tuple[Any, ...]]
    ] = []
    for size in sizes:
        for optional_choice in itertools.combinations(optional_indexes, size):
            states_visited += 1
            if states_visited > _MAX_COMBINATION_STATES:
                truncated = True
                break
            indexes = sorted(required_indexes | set(optional_choice))
            if not indexes:
                continue
            chosen = tuple(units[index] for index in indexes)
            try:
                ordered = _order_units(
                    chosen,
                    ordering=request["ordering"],
                    theme_overlay=theme_overlay,
                )
                coverage = _coverage_for_units(ordered, atomic_index)
            except PaperBlueprintWorkbenchError as exc:
                failure_counts[exc.code] += 1
                continue
            failures = _hard_failures(
                coverage,
                request=request,
                selected_theme_ids={unit.theme_id for unit in ordered},
                selected_atomic_ids={
                    atomic_id for unit in ordered for atomic_id in unit.final_atomic_ids
                },
            )
            if failures:
                failure_counts.update(failures)
                continue
            objective, key = _objective(ordered, coverage, request)
            feasible.append((ordered, coverage, objective, key))
            if len(feasible) > _MAX_FEASIBLE_POOL * 2:
                feasible.sort(key=lambda item: item[3])
                del feasible[_MAX_FEASIBLE_POOL:]
        if truncated:
            break
    feasible.sort(key=lambda item: item[3])
    unique: list[
        tuple[tuple[_ThemeUnit, ...], dict[str, Any], dict[str, Any], tuple[Any, ...]]
    ] = []
    signatures: set[tuple[str, ...]] = set()
    for item in feasible:
        signature = tuple(unit.theme_id for unit in item[0])
        if signature not in signatures:
            signatures.add(signature)
            unique.append(item)
        if len(unique) >= request["candidate_count"]:
            break
    return unique, {
        "states_visited": states_visited,
        "state_limit": _MAX_COMBINATION_STATES,
        "truncated": truncated,
        "failure_counts": dict(sorted(failure_counts.items())),
    }


def _set_preset_field(
    field: dict[str, Any], value: Any, note_zh: str, *, preserve_exact: bool
) -> None:
    verification = field.get("verification")
    verification = verification if isinstance(verification, Mapping) else {}
    if verification.get("status") == "verified_for_exact_paper" and preserve_exact:
        if field.get("value") != value:
            raise PaperBlueprintWorkbenchError(
                "mock_exam_template_value_mismatch",
                "教师本次考试参数与所选精确模板冲突，不能暗中覆盖模板。",
                409,
            )
        return
    field["value"] = value
    field["verification"] = {
        "status": (
            "project_template_value"
            if value is not None
            else "unknown_requires_exact_paper"
        ),
        "evidence_refs": [],
        "verified_for_paper_id": None,
        "note_zh": note_zh,
    }


def _effective_preset(
    base_preset: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    coverage: Mapping[str, Any],
    theme_count: int,
) -> dict[str, Any]:
    preset = deepcopy(dict(base_preset))
    preserve_exact = request["mode"] == MODE_MOCK_EXAM
    if request["mode"] == MODE_DAILY_PRACTICE:
        # A daily sheet is not an exam-format claim.  Keep layout/style but
        # downgrade per-paper values to candidate-local project values.
        preset["template_status"] = "editable_project_template"
    _set_preset_field(
        preset["structure"]["subquestion_numbering"],
        "restart_within_each_theme",
        "本候选新卷在每个主题内连续重编号；来源原题号仅作溯源。",
        preserve_exact=preserve_exact,
    )
    _set_preset_field(
        preset["per_paper"]["theme_count"],
        theme_count,
        (
            "教师本次模拟考试明确设置。"
            if preserve_exact
            else "由本次日常练习候选实际主题数计算，不构成固定主题数限制。"
        ),
        preserve_exact=preserve_exact,
    )
    total_score = coverage["totals"]["total_score"]
    preset_score = total_score if type(total_score) is int else None
    if preserve_exact:
        preset_score = request["paper"]["total_score"]
    _set_preset_field(
        preset["per_paper"]["total_score"],
        preset_score,
        "由本候选逐题显式分值聚合；非整数时保留在预览模型而不伪造预设整数。",
        preserve_exact=preserve_exact,
    )
    estimated_time = coverage["totals"]["estimated_time_minutes"]
    preset_duration = estimated_time if type(estimated_time) is int else None
    if preserve_exact:
        preset_duration = request["paper"]["duration_minutes"]
    _set_preset_field(
        preset["per_paper"]["duration_minutes"],
        preset_duration,
        (
            "教师本次模拟考试明确设置。"
            if preserve_exact
            else "由日常练习逐题显式预计用时聚合；不是考试时长声明。"
        ),
        preserve_exact=preserve_exact,
    )
    scoring = request["paper"]["scoring_rules"]
    if scoring is None:
        scoring = {
            "selection_rule_zh": "选择任务嵌入完整主题中，按题面要求作答。",
            "partial_credit_rule_zh": "日常练习分值仅作教师备课参考。",
            "other_rule_zh": "答案资格仍须在导出前由内容解析与教师复核确认。",
        }
    _set_preset_field(
        preset["per_paper"]["scoring_rules"],
        scoring,
        "本次候选的教师配置；不冒充来源卷官方评分细则。",
        preserve_exact=preserve_exact,
    )
    preset["student_version"]["answer_space"]["fallback_lines"] = request["paper"][
        "answer_space_lines"
    ]
    try:
        return validate_paper_format_preset(preset)
    except PaperFormatContractError as exc:
        raise PaperBlueprintWorkbenchError(
            exc.code, exc.message_zh, exc.status
        ) from exc


def _difficulty_projection(infos: Sequence[_AtomicInfo]) -> dict[str, Any]:
    counts = Counter(info.difficulty or "unknown" for info in infos)
    known = [(label, count) for label, count in counts.items() if label in _DIFFICULTIES]
    dominant = (
        min(known, key=lambda item: (-item[1], int(item[0][1:])))[0]
        if known
        else None
    )
    display = (
        f"{dominant} 为主"
        if dominant is not None and len(counts) > 1
        else dominant or "难度待补"
    )
    return {
        "basis": "atomic_part_count",
        "distribution": dict(sorted(counts.items())),
        "dominant": dominant,
        "display_zh": display,
    }


def _textbook_aggregate(infos: Sequence[_AtomicInfo]) -> dict[str, Any]:
    mapped = [
        deepcopy(dict(info.textbook_display))
        for info in infos
        if info.textbook_display["status"] == "explicit_mapping"
    ]
    unique = _dedupe_records(mapped)
    if not unique:
        return {
            "status": "unknown",
            "display_zh": "教材节待核验",
            "mappings": [],
            "unknown_atomic_count": len(infos),
        }
    labels = sorted({item["display_zh"] for item in unique})
    return {
        "status": "explicit_mapping",
        "display_zh": "；".join(labels),
        "mappings": unique,
        "unknown_atomic_count": sum(
            info.textbook_display["status"] != "explicit_mapping" for info in infos
        ),
    }


def _answer_projection(
    infos: Sequence[_AtomicInfo], rule: Mapping[str, Any] | None
) -> dict[str, Any]:
    if rule is None:
        availability = Counter(
            info.answer.get("availability", "unknown") for info in infos
        )
        authorities = Counter(
            info.answer.get("source_authority", "unknown") for info in infos
        )
        return {
            "eligible": None,
            "catalog_prefilter_only": True,
            "availability_distribution": dict(sorted(availability.items())),
            "authority_distribution": dict(sorted(authorities.items())),
            "display_zh": "教师未设置答案资格硬约束；导出前必须另行确认",
            "downstream_detail_validation_required": True,
        }
    eligible = [
        _answer_is_eligible(info.answer, rule)
        for info in infos
    ]
    availability = Counter(
        info.answer.get("availability", "unknown") for info in infos
    )
    authorities = Counter(
        info.answer.get("source_authority", "unknown") for info in infos
    )
    all_eligible = all(eligible)
    return {
        "eligible": all_eligible,
        "catalog_prefilter_only": True,
        "availability_distribution": dict(sorted(availability.items())),
        "authority_distribution": dict(sorted(authorities.items())),
        "display_zh": (
            "目录层答案资格满足；导出前仍须核验答案正文与来源边界"
            if all_eligible
            else "存在不满足教师答案资格的作答单元"
        ),
        "downstream_detail_validation_required": True,
    }


def _response_projection(infos: Sequence[_AtomicInfo]) -> dict[str, Any]:
    types = sorted({info.item_type for info in infos})
    if len(types) == 1:
        display = _RESPONSE_TYPE_LABELS.get(types[0], types[0])
    elif types:
        display = "混合作答"
    else:
        display = "作答类型待补"
    response_r = sorted(
        {value for info in infos for value in info.axes["R"]}
    )
    return {
        "item_types": types or ["unknown"],
        "response_R": response_r,
        "display_zh": display,
    }


def _source_projection(unit: _ThemeUnit) -> dict[str, Any]:
    metadata = unit.paper.get("source_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    display_parts = [
        str(value)
        for value in (
            metadata.get("year"),
            metadata.get("region"),
            metadata.get("paper_type"),
        )
        if value not in {None, "", "unknown"}
    ]
    display = " · ".join(display_parts)
    if not display:
        display = _unknown_display(unit.paper.get("title"), "来源待核验")
    return {
        "display_zh": display,
        "year": unit.source_year,
        "region": metadata.get("region", "unknown"),
        "paper_type": metadata.get("paper_type", "unknown"),
        "source_tier": metadata.get("source_tier", "unknown"),
        "attribution_status": metadata.get("attribution_status", "unknown"),
        "trace": {
            "paper_id": unit.paper.get("id"),
            "theme_id": unit.theme_id,
            "source_theme_sequence": unit.group["theme"].get("sequence"),
        },
    }


def _shared_material_projection(
    unit: _ThemeUnit,
    bundle: Mapping[str, Any],
    *,
    global_materials: dict[str, tuple[str, str]],
    theme_display_number: str,
) -> list[dict[str, Any]]:
    shared = unit.group.get("shared_context")
    shared = shared if isinstance(shared, Mapping) else {}
    source_materials = {
        material.get("material_id"): material
        for material in shared.get("materials", [])
        if isinstance(material, Mapping)
    }
    result: list[dict[str, Any]] = []
    for index, material in enumerate(bundle.get("shared_materials", []), start=1):
        source = source_materials.get(material.get("material_id"), {})
        explicit_hash = next(
            (
                source.get(key)
                for key in ("content_sha256", "asset_sha256", "sha256")
                if isinstance(source.get(key), str)
                and _SHA256.fullmatch(source.get(key))
            ),
            None,
        )
        identity = (
            f"sha256:{explicit_hash}"
            if explicit_hash is not None
            else f"theme:{material['render_once_key']}"
        )
        previous = global_materials.get(identity)
        if previous is None:
            anchor = f"MAT-{len(global_materials) + 1:03d}"
            global_materials[identity] = (anchor, theme_display_number)
            render = True
            reuse_of = None
            display = _unknown_display(
                source.get("candidate_description_zh"), "共享材料"
            )
        else:
            anchor, first_theme = previous
            render = False
            reuse_of = anchor
            display = f"复用{first_theme}共享材料"
        result.append(
            {
                "material_anchor": anchor,
                "render": render,
                "reuse_of": reuse_of,
                "display_zh": display,
                "material_type": material.get("material_type"),
                "page": material.get("page"),
                "preview_allowed": material.get("preview_allowed") is True,
                "content_sha256": explicit_hash,
                "data_ref": {
                    "material_id": material.get("material_id"),
                    "render_once_key": material.get("render_once_key"),
                },
            }
        )
    return result


def _build_preview_model(
    *,
    ordered_units: Sequence[_ThemeUnit],
    blueprint: Mapping[str, Any],
    effective_preset: Mapping[str, Any],
    request: Mapping[str, Any],
    coverage: Mapping[str, Any],
    atomic_index: Mapping[str, _AtomicInfo],
) -> dict[str, Any]:
    bundle_by_theme = {
        bundle["source"]["theme_id"]: bundle
        for bundle in blueprint["theme_bundles"]
    }
    global_materials: dict[str, tuple[str, str]] = {}
    theme_groups: list[dict[str, Any]] = []
    answer_rule = request["hard_constraints"]["answer_eligibility"]
    for theme_number, unit in enumerate(ordered_units, start=1):
        bundle = bundle_by_theme[unit.theme_id]
        display_number = f"第{_chinese_number(theme_number)}题"
        infos = [atomic_index[atomic_id] for atomic_id in bundle["final_atomic_ids"]]
        atomic_display = {
            info.atomic_id: f"第{index}问" for index, info in enumerate(infos, start=1)
        }
        printed_questions: list[dict[str, Any]] = []
        compact_rows: list[dict[str, Any]] = []
        for printed_number, printed in enumerate(
            bundle["printed_questions"], start=1
        ):
            printed_infos = [
                atomic_index[atomic["atomic_part_id"]]
                for atomic in printed["atomic_parts"]
            ]
            atomic_rows: list[dict[str, Any]] = []
            for info in printed_infos:
                dependency = info.row.get("dependency", {})
                prior_ids = dependency.get("prior_atomic_part_ids", [])
                prior_display = [
                    atomic_display[prior_id]
                    for prior_id in prior_ids
                    if prior_id in atomic_display
                ]
                answer_projection = _answer_projection([info], answer_rule)
                difficulty = _difficulty_projection([info])
                response_type = _response_projection([info])
                row = {
                    "row_kind": "atomic_part",
                    "collapsed": True,
                    "display_number": atomic_display[info.atomic_id],
                    "response_type": response_type,
                    "score": _number(info.score),
                    "time_minutes": _number(info.time_minutes),
                    "textbook_section": deepcopy(dict(info.textbook_display)),
                    "difficulty": difficulty,
                    "answer_eligibility": answer_projection,
                    "dependency": {
                        "kind": dependency.get("kind", "unknown"),
                        "prior_display_numbers": prior_display,
                        "complete_in_candidate": len(prior_display) == len(prior_ids),
                    },
                    "answer_space": {
                        "mode": "ruled_lines",
                        "lines": info.answer_space_lines,
                    },
                    "expandable_content_ref": info.expandable_content_ref,
                    "preview_text_zh": info.preview_text_zh,
                    "highlighted": info.atomic_id in set(unit.highlighted_atomic_ids),
                    "data_ref": {
                        "atomic_part_id": info.atomic_id,
                        "printed_question_id": info.printed_id,
                        "source_printed_number": info.row.get(
                            "printed_question_number"
                        ),
                        "dependency_prior_atomic_ids": list(prior_ids),
                    },
                }
                atomic_rows.append(row)
                compact_rows.append(deepcopy(row))
            printed_score = (
                sum(
                    (info.score for info in printed_infos if info.score is not None),
                    Decimal(0),
                )
                if all(info.score is not None for info in printed_infos)
                else None
            )
            printed_time = (
                sum(
                    (
                        info.time_minutes
                        for info in printed_infos
                        if info.time_minutes is not None
                    ),
                    Decimal(0),
                )
                if all(info.time_minutes is not None for info in printed_infos)
                else None
            )
            printed_questions.append(
                {
                    "row_kind": "printed_question",
                    "collapsed": True,
                    "display_number": str(printed_number),
                    "response_type": _response_projection(printed_infos),
                    "score": _number(printed_score),
                    "time_minutes": _number(printed_time),
                    "textbook_section": _textbook_aggregate(printed_infos),
                    "difficulty": _difficulty_projection(printed_infos),
                    "answer_eligibility": _answer_projection(
                        printed_infos, answer_rule
                    ),
                    "dependency": {
                        "kind": "aggregate",
                        "has_prior_dependency": any(
                            info.row.get("dependency", {}).get(
                                "prior_atomic_part_ids", []
                            )
                            for info in printed_infos
                        ),
                    },
                    "expandable_content_ref": next(
                        (
                            info.expandable_content_ref
                            for info in printed_infos
                            if info.expandable_content_ref is not None
                        ),
                        None,
                    ),
                    "atomic_count": len(printed_infos),
                    "atomic_rows": atomic_rows,
                    "data_ref": {
                        "printed_question_id": printed["printed_question_id"],
                        "source_number": printed.get("source_number"),
                        "source_sequence": printed.get("source_sequence"),
                    },
                }
            )
        theme_score = (
            sum((info.score for info in infos if info.score is not None), Decimal(0))
            if all(info.score is not None for info in infos)
            else None
        )
        theme_time = (
            sum(
                (info.time_minutes for info in infos if info.time_minutes is not None),
                Decimal(0),
            )
            if all(info.time_minutes is not None for info in infos)
            else None
        )
        theme_groups.append(
            {
                "group_kind": TOP_LEVEL_UNIT,
                "collapsed": True,
                "display_number": display_number,
                "title": unit.title_zh,
                "heading_zh": f"{display_number}  {unit.title_zh}",
                "source": _source_projection(unit),
                "textbook": _textbook_aggregate(infos),
                "score": _number(theme_score),
                "time_minutes": _number(theme_time),
                "difficulty": _difficulty_projection(infos),
                "shared_materials": _shared_material_projection(
                    unit,
                    bundle,
                    global_materials=global_materials,
                    theme_display_number=display_number,
                ),
                "atomic_count": len(infos),
                "highlighted_atomic_ids": list(unit.highlighted_atomic_ids),
                "printed_questions": printed_questions,
                "compact_rows": compact_rows,
                "data_ref": {
                    "theme_id": unit.theme_id,
                    "paper_id": unit.paper.get("id"),
                },
            }
        )
    page_layout = effective_preset["page_layout"]
    cover_page = request["paper"]["pagination_rules"].get(
        "cover_page", request["mode"] == MODE_MOCK_EXAM
    )
    preview = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "mode": request["mode"],
        "layout_kind": (
            "exam" if request["mode"] == MODE_MOCK_EXAM else "compact_practice"
        ),
        "projection_unit": TOP_LEVEL_UNIT,
        "data_snapshot_id": request["data_snapshot_id"],
        "preset_projection": {
            "preset_id": effective_preset["preset_id"],
            "preset_version": effective_preset["preset_version"],
            "template_status": effective_preset["template_status"],
            "claim_boundary_zh": effective_preset["claim_boundary_zh"],
            "preset_sha256": canonical_sha256(effective_preset),
        },
        "basket_selections": [
            deepcopy(dict(selection))
            for unit in ordered_units
            for selection in unit.basket_selections
        ],
        "assembly_blueprint_digest": blueprint["blueprint_digest"],
        "cover": {
            "enabled": cover_page is True,
            "title_zh": request["paper"]["title_zh"],
            "subtitle_zh": request["paper"]["subtitle_zh"],
            "instructions_zh": request["paper"]["instructions_zh"],
            "identity_fields_zh": request["paper"]["identity_fields_zh"],
            "sealed_line": {
                "enabled": request["paper"]["sealed_line"],
                "label_zh": "密封线",
            },
        },
        "exam_information": {
            "template_year": request["paper"]["template_year"],
            "scheduled_duration_minutes": request["paper"]["duration_minutes"],
            "estimated_duration_minutes": coverage["totals"][
                "estimated_time_minutes"
            ],
            "total_score": coverage["totals"]["total_score"],
            "theme_count": coverage["totals"]["theme_count"],
            "atomic_count": coverage["totals"]["atomic_count"],
            "numbering_rule": "主题按第一题、第二题……编号；printed/atomic 在主题内连续重编号",
            "source_numbers_are_trace_only": True,
            "scoring_rules": deepcopy(request["paper"]["scoring_rules"]),
        },
        "theme_groups": theme_groups,
        "pagination": {
            "status": "pending_renderer",
            "physical_page_count": None,
            "page_size": page_layout["page_size"],
            "orientation": page_layout["orientation"],
            "margins_mm": deepcopy(page_layout["margins_mm"]),
            "header": deepcopy(effective_preset["student_version"]["header"]),
            "footer": deepcopy(effective_preset["student_version"]["footer"]),
            "rules": {
                "keep_theme_heading_with_first_question": True,
                "avoid_split_within_printed_question": True,
                "shared_material_at_theme_level_only": True,
                **deepcopy(request["paper"]["pagination_rules"]),
            },
            "logical_page_flow": [
                *(
                    [{"block_kind": "cover", "anchor": "COVER"}]
                    if cover_page is True
                    else []
                ),
                *[
                    {
                        "block_kind": TOP_LEVEL_UNIT,
                        "anchor": f"THEME-{index:02d}",
                        "display_number": group["display_number"],
                    }
                    for index, group in enumerate(theme_groups, start=1)
                ],
            ],
            "message_zh": "物理页码和分页缺陷只能在 DOCX/PDF 渲染后逐页确认。",
        },
        "coverage": deepcopy(dict(coverage)),
        "answer_eligibility_summary": _answer_projection(
            [
                atomic_index[atomic_id]
                for unit in ordered_units
                for atomic_id in unit.final_atomic_ids
            ],
            request["hard_constraints"]["answer_eligibility"],
        ),
        "export_gate": {
            "preview_hash_required": True,
            "preview_frozen_before_export": True,
            "export_allowed": False,
            "downstream_requirements": [
                "same_preview_hash_confirmation",
                "answer_detail_qualification",
                "renderer_physical_pagination",
                "rendered_page_visual_qa",
            ],
        },
    }
    return preview


def _public_rule(rule: _AxisRule) -> dict[str, int | None]:
    return {"min": rule.minimum, "max": rule.maximum}


def _public_request(request: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(request))
    hard = value["hard_constraints"]
    hard["coverage"] = {
        axis: {label: _public_rule(rule) for label, rule in sorted(rules.items())}
        for axis, rules in hard["coverage"].items()
    }
    hard["difficulty_distribution"] = {
        label: _public_rule(rule)
        for label, rule in sorted(hard["difficulty_distribution"].items())
    }
    hard["dedup_clusters"] = {
        cluster: list(atomic_ids)
        for cluster, atomic_ids in sorted(hard["dedup_clusters"].items())
    }
    return value


def _catalog_theme_atomic_ids(catalog: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        group["theme"]["id"]: tuple(
            row["atomic_part_id"]
            for row in sorted(group["atomic_chain"], key=_atomic_row_order_key)
        )
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
    }


def _catalog_theme_order_completeness(
    catalog: Mapping[str, Any],
) -> dict[str, bool]:
    return {
        group["theme"]["id"]: group.get("optimizer_order_evidence", {}).get(
            "complete"
        )
        is True
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
    }


def _assert_ordering_references(
    ordering: Mapping[str, Any], theme_ids: set[str]
) -> None:
    referenced = set(ordering["teacher_theme_order"])
    referenced.update(
        identity for edge in ordering["prerequisite_edges"] for identity in edge
    )
    missing = sorted(referenced - theme_ids)
    if missing:
        raise PaperBlueprintWorkbenchError(
            "theme_order_reference_missing",
            "教师拖动顺序或教材先修边引用了当前快照不存在的主题。",
            409,
            details={"missing_theme_ids": missing},
        )


def _assert_theme_overlay_references(
    theme_overlay: Mapping[str, Mapping[str, Any]], theme_ids: set[str]
) -> None:
    unknown_records = sorted(set(theme_overlay) - theme_ids)
    referenced: set[str] = set()
    for metadata in theme_overlay.values():
        prerequisites = metadata.get("prerequisite_theme_ids", [])
        if prerequisites is None:
            prerequisites = []
        if not isinstance(prerequisites, list) or any(
            not isinstance(item, str) or _SAFE_ID.fullmatch(item) is None
            for item in prerequisites
        ):
            raise PaperBlueprintWorkbenchError(
                "metadata_overlay_invalid", "主题先修列表格式不正确。", 409
            )
        if len(prerequisites) != len(set(prerequisites)):
            raise PaperBlueprintWorkbenchError(
                "metadata_overlay_invalid", "主题先修列表不能含重复主题。", 409
            )
        referenced.update(prerequisites)
    missing = sorted(referenced - theme_ids)
    if unknown_records or missing:
        raise PaperBlueprintWorkbenchError(
            "theme_prerequisite_reference_missing",
            "主题元数据或教材先修关系引用了当前快照不存在的主题。",
            409,
            details={
                "unknown_overlay_theme_ids": unknown_records,
                "missing_prerequisite_theme_ids": missing,
            },
        )


def _build_candidate(
    *,
    rank: int,
    ordered_units: Sequence[_ThemeUnit],
    coverage: Mapping[str, Any],
    objective: Mapping[str, Any],
    request: Mapping[str, Any],
    base_preset: Mapping[str, Any],
    catalog_wrapper: Mapping[str, Any],
    atomic_index: Mapping[str, _AtomicInfo],
) -> dict[str, Any]:
    effective_preset = _effective_preset(
        base_preset,
        request=request,
        coverage=coverage,
        theme_count=len(ordered_units),
    )
    selections = [
        deepcopy(dict(selection))
        for unit in ordered_units
        for selection in unit.basket_selections
    ]

    def loader(scope: str, expected_snapshot_id: str) -> Mapping[str, Any]:
        if scope != request["scope"] or expected_snapshot_id != request[
            "data_snapshot_id"
        ]:
            raise PaperBlueprintWorkbenchError(
                "theme_snapshot_stale", "装配器请求了不一致的题库快照。", 409
            )
        return deepcopy(dict(catalog_wrapper))

    try:
        blueprint = build_assembly_blueprint(
            selections,
            theme_loader=loader,
            expected_data_snapshot_id=request["data_snapshot_id"],
            preset=effective_preset,
        )
    except PaperFormatContractError as exc:
        raise PaperBlueprintWorkbenchError(
            exc.code, exc.message_zh, exc.status
        ) from exc
    if blueprint["status"] == "blocked" or blueprint["blockers"]:
        raise PaperBlueprintWorkbenchError(
            "assembly_blueprint_blocked",
            "候选题篮的权威装配蓝图仍有依赖或结构阻断项。",
            409,
            details={"blockers": deepcopy(blueprint["blockers"])},
        )
    observed_order = [
        bundle["source"]["theme_id"] for bundle in blueprint["theme_bundles"]
    ]
    expected_order = [unit.theme_id for unit in ordered_units]
    if observed_order != expected_order:
        raise PaperBlueprintWorkbenchError(
            "assembly_theme_order_mismatch",
            "权威装配蓝图没有保持优化器确定的自然主题顺序。",
            409,
        )
    unit_by_theme = {unit.theme_id: unit for unit in ordered_units}
    for bundle in blueprint["theme_bundles"]:
        theme_id = bundle["source"]["theme_id"]
        if tuple(bundle["final_atomic_ids"]) != unit_by_theme[theme_id].final_atomic_ids:
            raise PaperBlueprintWorkbenchError(
                "dependency_closure_mismatch",
                "优化器预计算与权威装配器的完整依赖闭包不一致。",
                409,
            )
    preview = _build_preview_model(
        ordered_units=ordered_units,
        blueprint=blueprint,
        effective_preset=effective_preset,
        request=request,
        coverage=coverage,
        atomic_index=atomic_index,
    )
    preview_hash = canonical_sha256(preview)
    return {
        "rank": rank,
        "candidate_id": f"PBC-{preview_hash[:24]}",
        "ordered_theme_ids": expected_order,
        "basket_selections": selections,
        "assembly_blueprint": blueprint,
        "coverage": deepcopy(dict(coverage)),
        "objective": deepcopy(dict(objective)),
        "preview_model": preview,
        "preview_snapshot_sha256": preview_hash,
        "preview_freeze": {
            "status": "frozen",
            "algorithm": "sha256_canonical_json_v1",
            "sha256": preview_hash,
        },
        "export_precondition": {
            "status": "blocked_until_same_preview_hash_confirmed",
            "required_preview_snapshot_sha256": preview_hash,
            "optimizer_export_allowed": False,
            "message_zh": "只有确认同一预览哈希后，消费方才能进入答案资格与实体分页导出门禁。",
        },
        "integrity": {
            "theme_first": True,
            "standalone_choice_section_allowed": False,
            "atomic_matches_are_highlights_only": True,
            "dependency_closure_authoritatively_recomputed": True,
            "shared_materials_not_copied_to_compact_rows": True,
            "source_numbers_trace_only": True,
            "hard_constraints_relaxed": False,
        },
    }


def _missing_question_requirements(
    *,
    units: Sequence[_ThemeUnit],
    request: Mapping[str, Any],
    atomic_index: Mapping[str, _AtomicInfo],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    if request["mode"] == MODE_MOCK_EXAM:
        required_themes = request["paper"]["theme_count"]
        if len(units) < required_themes:
            missing.append(
                {
                    "constraint": "theme_count",
                    "required": required_themes,
                    "available": len(units),
                    "shortfall": required_themes - len(units),
                    "message_zh": "满足当前硬过滤的完整主题数量不足。",
                }
            )
    if units:
        union_coverage = _coverage_for_units(units, atomic_index)
    else:
        union_coverage = {
            "axis_coverage": {axis: {} for axis in _AXES},
            "difficulty_distribution": {},
        }
    hard = request["hard_constraints"]
    for axis, rules in hard["coverage"].items():
        counts = union_coverage["axis_coverage"][axis]
        for label, rule in rules.items():
            available = counts.get(label, 0)
            if available < rule.minimum:
                missing.append(
                    {
                        "constraint": axis,
                        "value": label,
                        "required": rule.minimum,
                        "available": available,
                        "shortfall": rule.minimum - available,
                        "message_zh": f"缺少满足 {axis}={label} 的作答单元。",
                    }
                )
    for label, rule in hard["difficulty_distribution"].items():
        available = union_coverage["difficulty_distribution"].get(label, 0)
        if available < rule.minimum:
            missing.append(
                {
                    "constraint": "D",
                    "value": label,
                    "required": rule.minimum,
                    "available": available,
                    "shortfall": rule.minimum - available,
                    "message_zh": f"缺少难度为 {label} 的作答单元。",
                }
            )
    return missing


def _relaxation_options(
    *,
    request: Mapping[str, Any],
    conflicts: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    missing_questions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    codes = {item["code"] for item in conflicts} | {item["code"] for item in gaps}
    options: list[dict[str, Any]] = []
    if request["mode"] == MODE_MOCK_EXAM:
        options.append(
            {
                "code": "review_theme_count_or_score",
                "message_zh": "可由教师明确调整主题数、总分或时长；系统不会自动修改。",
                "changes": ["paper.theme_count", "paper.total_score", "paper.duration_minutes"],
            }
        )
    if "answer_eligibility_missing" in codes:
        options.append(
            {
                "code": "review_answer_eligibility",
                "message_zh": "可由教师明确允许其他答案状态，或先补齐合格参考答案。",
                "changes": ["hard_constraints.answer_eligibility"],
            }
        )
    if any("metadata_missing" in code for code in codes):
        options.append(
            {
                "code": "complete_inventory_metadata",
                "message_zh": "优先补齐逐题分值、预计用时或去重簇；不建议用默认值代替证据。",
                "changes": ["metadata_overlay.atomics"],
            }
        )
    if "preview_content_missing" in codes:
        options.append(
            {
                "code": "complete_preview_content",
                "message_zh": "先补齐可展开的完整题面引用，再重新冻结整卷预览。",
                "changes": [
                    "metadata_overlay.atomics.expandable_content_ref",
                ],
            }
        )
    if "source_order_metadata_missing" in codes:
        options.append(
            {
                "code": "complete_source_order_metadata",
                "message_zh": "先补齐或人工确认主题内 printed/atomic 源顺序，再重新组卷；系统不会按数组位置猜测。",
                "changes": [
                    "theme_catalog.theme_groups.atomic_chain.printed_sequence",
                    "theme_catalog.theme_groups.atomic_chain.atomic_sequence_in_printed",
                ],
            }
        )
    if missing_questions:
        options.append(
            {
                "code": "relax_coverage_explicitly",
                "message_zh": "可由教师选择降低某项 K/A/C/R/RP/D 最低覆盖，或扩大来源年份。",
                "changes": [
                    "hard_constraints.coverage",
                    "hard_constraints.difficulty_distribution",
                    "hard_constraints.source_years",
                ],
            }
        )
    if {"coverage_bound_unsatisfied", "difficulty_bound_unsatisfied"} & codes:
        options.append(
            {
                "code": "relax_coverage_bounds_explicitly",
                "message_zh": "可由教师明确降低最低覆盖，或提高/移除覆盖上限；系统不会自动改动 min/max。",
                "changes": [
                    "hard_constraints.coverage.*.min",
                    "hard_constraints.coverage.*.max",
                    "hard_constraints.difficulty_distribution.*.min",
                    "hard_constraints.difficulty_distribution.*.max",
                ],
            }
        )
    if "theme_order_cycle" in codes:
        options.append(
            {
                "code": "repair_theme_order_cycle",
                "message_zh": "请调整教师拖动顺序或删除冲突的教材先修边；系统不会忽略排序环。",
                "changes": [
                    "ordering.teacher_theme_order",
                    "ordering.prerequisite_edges",
                ],
            }
        )
    if {
        "required_excluded_conflict",
        "required_dependency_excluded",
    } & codes:
        options.append(
            {
                "code": "reconcile_required_and_excluded_items",
                "message_zh": "请从必选或排除列表中明确移除冲突项；系统不会替教师决定保留哪一侧。",
                "changes": [
                    "hard_constraints.required_theme_ids",
                    "hard_constraints.required_atomic_ids",
                    "hard_constraints.required_item_ids",
                    "hard_constraints.excluded_theme_ids",
                    "hard_constraints.excluded_atomic_ids",
                    "hard_constraints.excluded_item_ids",
                ],
            }
        )
    if request["mode"] == MODE_DAILY_PRACTICE:
        options.append(
            {
                "code": "switch_daily_selection_unit",
                "message_zh": "可在完整主题与显式依赖闭包之间切换，但 atomic 仍不能成为一级板块。",
                "changes": ["preferences.selection_unit"],
            }
        )
    return _dedupe_records(options)


def _base_response(
    *,
    request: Mapping[str, Any],
    status: str,
    candidates: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    missing_questions: Sequence[Mapping[str, Any]],
    relaxations: Sequence[Mapping[str, Any]],
    search_diagnostics: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    public_request = _public_request(request)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": request["mode"],
        "data_snapshot_id": request["data_snapshot_id"],
        "request_sha256": canonical_sha256(public_request),
        "normalized_request": public_request,
        "status": status,
        "candidates": [deepcopy(dict(candidate)) for candidate in candidates],
        "conflicts": _dedupe_records(conflicts),
        "inventory_gaps": _dedupe_records(gaps),
        "missing_questions": _dedupe_records(missing_questions),
        "relaxation_options": _dedupe_records(relaxations),
        "warnings": _dedupe_records(warnings),
        "search_diagnostics": deepcopy(dict(search_diagnostics)),
        "integrity": {
            "top_level_unit": TOP_LEVEL_UNIT,
            "standalone_choice_section_allowed": False,
            "atomic_matches_are_highlights_only": True,
            "hard_constraints_relaxed": False,
            "model_invoked": False,
            "central_question_bank_written": False,
            "preview_required_before_export": True,
        },
    }


def _iso_timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _atomic_write_store(path: Path, value: Mapping[str, Any]) -> None:
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


class PaperBlueprintPreviewStore:
    """Owner/session-bound finite preview and approval store.

    The optimizer remains pure.  This store is the authoritative stateful gate
    between a deterministic preview and the existing four-file export job.  A
    The browser never receives the authoritative top-level basket or assembly
    blueprint.  The read-only preview model may include traceable selection
    references, but the approved export route rejects all client selections
    and consumes only the separately stored candidate plan.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        ttl_seconds: int = 30 * 60,
        max_candidates: int = 24,
        max_approvals: int = 24,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if (
            type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= 24 * 60 * 60
            or type(max_candidates) is not int
            or not 1 <= max_candidates <= 100
            or type(max_approvals) is not int
            or not 1 <= max_approvals <= 100
        ):
            raise ValueError("paper blueprint preview-store limits are invalid")
        self.root = (Path(state_root) / "paper-blueprint-preview-v1").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_candidates = max_candidates
        self.max_approvals = max_approvals
        self._clock = clock or time.time
        self._lock = threading.RLock()

    @staticmethod
    def _identity(owner_id: str, session_id: str) -> tuple[str, str, str]:
        if (
            not isinstance(owner_id, str)
            or not owner_id
            or len(owner_id) > 240
            or any(ord(character) < 32 for character in owner_id)
            or not isinstance(session_id, str)
            or _SHA256.fullmatch(session_id) is None
        ):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_owner_invalid",
                "当前教师会话身份不正确，不能保存组卷预览。",
                403,
            )
        owner_sha256 = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        session_sha256 = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        key = hashlib.sha256(
            f"{owner_sha256}:{session_sha256}".encode("ascii")
        ).hexdigest()
        return owner_sha256, session_sha256, key

    def _path(self, owner_id: str, session_id: str) -> tuple[Path, str, str]:
        owner_sha256, session_sha256, key = self._identity(owner_id, session_id)
        return self.root / f"{key}.json", owner_sha256, session_sha256

    @staticmethod
    def _new_document(owner_sha256: str, session_sha256: str) -> dict[str, Any]:
        return {
            "schema_version": PREVIEW_STORE_SCHEMA_VERSION,
            "owner_sha256": owner_sha256,
            "session_sha256": session_sha256,
            "active_request_sha256": None,
            "active_data_snapshot_id": None,
            "active_preview_revision": None,
            "candidates": {},
            "approvals": {},
            "updated_at": None,
        }

    def _read(
        self, path: Path, owner_sha256: str, session_sha256: str
    ) -> dict[str, Any]:
        if not path.is_file():
            return self._new_document(owner_sha256, session_sha256)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_preview_store_corrupt",
                "组卷预览记录损坏，已停止批准与导出；请重新预览。",
                503,
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != PREVIEW_STORE_SCHEMA_VERSION
            or value.get("owner_sha256") != owner_sha256
            or value.get("session_sha256") != session_sha256
            or not isinstance(value.get("candidates"), dict)
            or not isinstance(value.get("approvals"), dict)
        ):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_preview_store_corrupt",
                "组卷预览记录身份或结构不一致，已停止批准与导出。",
                503,
            )
        return value

    @staticmethod
    def _candidate_public(record: Mapping[str, Any]) -> dict[str, Any]:
        stored = record["candidate"]
        candidate = {
            key: deepcopy(stored[key])
            for key in (
                "rank",
                "candidate_id",
                "ordered_theme_ids",
                "coverage",
                "objective",
                "preview_model",
                "preview_snapshot_sha256",
            )
            if key in stored
        }
        candidate["preview_revision"] = record["preview_revision"]
        candidate["expires_at"] = record["expires_at"]
        return candidate

    @staticmethod
    def _approval_public(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": PREVIEW_APPROVAL_SCHEMA_VERSION,
            "preview_approval_id": record["preview_approval_id"],
            "candidate_id": record["candidate_id"],
            "preview_snapshot_sha256": record["preview_snapshot_sha256"],
            "data_snapshot_id": record["data_snapshot_id"],
            "preview_revision": record["preview_revision"],
            "approved_revision": record["approved_revision"],
            "status": record["status"],
            "approved_at": record["approved_at"],
            "expires_at": record["expires_at"],
            "export_job_id": record.get("export_job_id"),
        }

    @staticmethod
    def _expire(document: dict[str, Any], now: float) -> bool:
        changed = False
        candidates = document["candidates"]
        for record in candidates.values():
            if not isinstance(record, dict):
                continue
            if (
                not isinstance(record.get("expires_epoch"), (int, float))
                or float(record["expires_epoch"]) <= now
            ) and record.get("status") != "expired":
                record["status"] = "expired"
                changed = True
        for approval in document["approvals"].values():
            if not isinstance(approval, dict):
                continue
            if (
                approval.get("status") == "approved"
                and isinstance(approval.get("expires_epoch"), (int, float))
                and float(approval["expires_epoch"]) <= now
            ):
                approval["status"] = "expired"
                approval.pop("export_reservation", None)
                changed = True
            reservation = approval.get("export_reservation")
            if (
                isinstance(reservation, dict)
                and isinstance(reservation.get("created_epoch"), (int, float))
                and float(reservation["created_epoch"]) + 120 <= now
            ):
                approval.pop("export_reservation", None)
                changed = True
        return changed

    def save_preview(
        self,
        owner_id: str,
        session_id: str,
        response: Mapping[str, Any],
        *,
        request_fingerprint_sha256: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(response, Mapping)
            or response.get("status") not in {"feasible", "infeasible", "indeterminate"}
            or not isinstance(response.get("candidates"), list)
            or len(response["candidates"]) > 3
            or not isinstance(response.get("data_snapshot_id"), str)
            or _SHA256.fullmatch(response["data_snapshot_id"]) is None
            or _SHA256.fullmatch(request_fingerprint_sha256) is None
        ):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_preview_invalid",
                "组卷器返回的预览结构不完整，不能保存候选。",
                503,
            )
        now = float(self._clock())
        expires_epoch = now + self.ttl_seconds
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            self._expire(document, now)
            changed_request = (
                document.get("active_request_sha256")
                != request_fingerprint_sha256
                or document.get("active_data_snapshot_id")
                != response["data_snapshot_id"]
            )
            revision = document.get("active_preview_revision")
            if changed_request or not isinstance(revision, str) or _PREVIEW_REVISION_ID.fullmatch(revision) is None:
                revision = f"PBREV-{uuid.uuid4().hex}"
                for approval in document["approvals"].values():
                    if isinstance(approval, dict) and approval.get("status") == "approved":
                        approval["status"] = "revoked"
                        approval.pop("export_reservation", None)
            records: dict[str, Any] = {}
            for candidate_value in response["candidates"]:
                if not isinstance(candidate_value, Mapping):
                    raise PaperBlueprintWorkbenchError(
                        "paper_blueprint_preview_invalid",
                        "组卷候选格式不正确，不能保存。",
                        503,
                    )
                candidate = deepcopy(dict(candidate_value))
                candidate_id = candidate.get("candidate_id")
                preview_hash = candidate.get("preview_snapshot_sha256")
                preview_model = candidate.get("preview_model")
                if (
                    not isinstance(candidate_id, str)
                    or _SAFE_ID.fullmatch(candidate_id) is None
                    or not isinstance(preview_hash, str)
                    or _SHA256.fullmatch(preview_hash) is None
                    or not isinstance(preview_model, Mapping)
                    or canonical_sha256(preview_model) != preview_hash
                    or not isinstance(candidate.get("basket_selections"), list)
                    or not candidate["basket_selections"]
                    or candidate_id in records
                ):
                    raise PaperBlueprintWorkbenchError(
                        "paper_blueprint_preview_invalid",
                        "组卷候选身份、预览哈希或服务端题篮不一致。",
                        503,
                    )
                records[candidate_id] = {
                    "candidate_id": candidate_id,
                    "preview_snapshot_sha256": preview_hash,
                    "data_snapshot_id": response["data_snapshot_id"],
                    "preview_revision": revision,
                    "created_at": _iso_timestamp(now),
                    "created_epoch": now,
                    "expires_at": _iso_timestamp(expires_epoch),
                    "expires_epoch": expires_epoch,
                    "status": "active",
                    "candidate": candidate,
                    "normalized_request": deepcopy(response.get("normalized_request")),
                }
            if len(records) > self.max_candidates:
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_preview_limit_exceeded",
                    "本次组卷候选数量超过服务端保存上限。",
                    400,
                )
            document["active_request_sha256"] = request_fingerprint_sha256
            document["active_data_snapshot_id"] = response["data_snapshot_id"]
            document["active_preview_revision"] = revision
            document["candidates"] = records
            approvals = sorted(
                (
                    item
                    for item in document["approvals"].values()
                    if isinstance(item, dict)
                ),
                key=lambda item: float(item.get("approved_epoch", 0)),
                reverse=True,
            )[: self.max_approvals]
            document["approvals"] = {
                item["preview_approval_id"]: item
                for item in approvals
                if isinstance(item.get("preview_approval_id"), str)
            }
            document["updated_at"] = _iso_timestamp(now)
            _atomic_write_store(path, document)
            public = {
                key: deepcopy(response[key])
                for key in (
                    "schema_version",
                    "mode",
                    "data_snapshot_id",
                    "status",
                    "conflicts",
                    "inventory_gaps",
                    "missing_questions",
                    "relaxation_options",
                    "warnings",
                )
                if key in response
            }
            public["request_sha256"] = request_fingerprint_sha256
            public["preview_revision"] = revision
            public["candidate_expires_at"] = _iso_timestamp(expires_epoch)
            public["candidates"] = [
                self._candidate_public(records[candidate["candidate_id"]])
                for candidate in response["candidates"]
            ]
            return public

    def approve(
        self,
        owner_id: str,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        current_data_snapshot_id: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {
            "candidate_id",
            "preview_snapshot_sha256",
            "data_snapshot_id",
        }:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_approval_request_invalid",
                "预览确认必须提交候选、预览哈希和题库快照。",
            )
        candidate_id = _safe_id(payload.get("candidate_id"), "组卷候选")
        preview_hash = payload.get("preview_snapshot_sha256")
        snapshot_id = payload.get("data_snapshot_id")
        if (
            not isinstance(preview_hash, str)
            or _SHA256.fullmatch(preview_hash) is None
            or not isinstance(snapshot_id, str)
            or _SHA256.fullmatch(snapshot_id) is None
            or snapshot_id != current_data_snapshot_id
        ):
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_approval_stale",
                "预览确认使用了旧题库快照，请重新预览。",
                409,
            )
        now = float(self._clock())
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            changed = self._expire(document, now)
            candidate = document["candidates"].get(candidate_id)
            if not isinstance(candidate, dict):
                if changed:
                    document["updated_at"] = _iso_timestamp(now)
                    _atomic_write_store(path, document)
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_candidate_not_found",
                    "当前教师会话中没有这个可确认的组卷候选。",
                    404,
                )
            if candidate.get("status") == "expired":
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_candidate_expired",
                    "这个组卷候选已过期，请重新生成整卷预览。",
                    410,
                )
            if (
                candidate.get("preview_snapshot_sha256") != preview_hash
                or candidate.get("data_snapshot_id") != snapshot_id
                or candidate.get("preview_revision")
                != document.get("active_preview_revision")
            ):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_hash_mismatch",
                    "候选已变化或预览哈希不一致，请重新打开整卷预览。",
                    409,
                )
            for record in document["approvals"].values():
                if (
                    isinstance(record, dict)
                    and record.get("status") == "approved"
                    and record.get("candidate_id") == candidate_id
                    and record.get("preview_snapshot_sha256") == preview_hash
                    and record.get("data_snapshot_id") == snapshot_id
                    and record.get("preview_revision")
                    == candidate.get("preview_revision")
                ):
                    if changed:
                        document["updated_at"] = _iso_timestamp(now)
                        _atomic_write_store(path, document)
                    result = self._approval_public(record)
                    result["idempotent_replay"] = True
                    return result
            for record in document["approvals"].values():
                if isinstance(record, dict) and record.get("status") == "approved":
                    record["status"] = "revoked"
                    record.pop("export_reservation", None)
            approval_id = f"PBAPP-{uuid.uuid4().hex}"
            approved_revision = f"PBAPR-{uuid.uuid4().hex}"
            expires_epoch = min(float(candidate["expires_epoch"]), now + self.ttl_seconds)
            record = {
                "preview_approval_id": approval_id,
                "candidate_id": candidate_id,
                "preview_snapshot_sha256": preview_hash,
                "data_snapshot_id": snapshot_id,
                "preview_revision": candidate["preview_revision"],
                "approved_revision": approved_revision,
                "status": "approved",
                "approved_at": _iso_timestamp(now),
                "approved_epoch": now,
                "expires_at": _iso_timestamp(expires_epoch),
                "expires_epoch": expires_epoch,
                "export_job_id": None,
            }
            document["approvals"][approval_id] = record
            retained_approvals = sorted(
                (
                    item
                    for item in document["approvals"].values()
                    if isinstance(item, dict)
                ),
                key=lambda item: float(item.get("approved_epoch", 0)),
                reverse=True,
            )[: self.max_approvals]
            document["approvals"] = {
                item["preview_approval_id"]: item
                for item in retained_approvals
                if isinstance(item.get("preview_approval_id"), str)
            }
            document["updated_at"] = _iso_timestamp(now)
            _atomic_write_store(path, document)
            result = self._approval_public(record)
            result["idempotent_replay"] = False
            return result

    def approval_scope(
        self, owner_id: str, session_id: str, approval_id: str
    ) -> tuple[str, str]:
        if not isinstance(approval_id, str) or _PREVIEW_APPROVAL_ID.fullmatch(approval_id) is None:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_approval_not_found",
                "当前教师会话中没有这个预览批准。",
                404,
            )
        now = float(self._clock())
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            changed = self._expire(document, now)
            approval = document["approvals"].get(approval_id)
            candidate = (
                document["candidates"].get(approval.get("candidate_id"))
                if isinstance(approval, dict)
                else None
            )
            if changed:
                document["updated_at"] = _iso_timestamp(now)
                _atomic_write_store(path, document)
            if isinstance(approval, dict) and approval.get("status") == "expired":
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_expired",
                    "这次整卷预览确认已过期，请重新预览并确认。",
                    410,
                )
            if not isinstance(approval, dict) or not isinstance(candidate, dict):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_not_found",
                    "当前教师会话中没有这个预览批准。",
                    404,
                )
            if approval.get("status") != "approved":
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_stale",
                    "这次整卷预览确认已经失效，请重新预览。",
                    409,
                )
            normalized = candidate.get("normalized_request")
            scope = normalized.get("scope") if isinstance(normalized, Mapping) else None
            snapshot = candidate.get("data_snapshot_id")
            if (
                not isinstance(scope, str)
                or _SAFE_ID.fullmatch(scope) is None
                or not isinstance(snapshot, str)
                or _SHA256.fullmatch(snapshot) is None
            ):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_preview_store_corrupt",
                    "已批准候选缺少题库范围或快照，不能导出。",
                    503,
                )
            return scope, snapshot

    def candidate_scope(
        self, owner_id: str, session_id: str, candidate_id: str
    ) -> tuple[str, str]:
        candidate_id = _safe_id(candidate_id, "组卷候选")
        now = float(self._clock())
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            changed = self._expire(document, now)
            candidate = document["candidates"].get(candidate_id)
            if changed:
                document["updated_at"] = _iso_timestamp(now)
                _atomic_write_store(path, document)
            if not isinstance(candidate, dict):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_candidate_not_found",
                    "当前教师会话中没有这个可确认的组卷候选。",
                    404,
                )
            if candidate.get("status") == "expired":
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_candidate_expired",
                    "这个组卷候选已过期，请重新生成整卷预览。",
                    410,
                )
            normalized = candidate.get("normalized_request")
            scope = normalized.get("scope") if isinstance(normalized, Mapping) else None
            snapshot = candidate.get("data_snapshot_id")
            if (
                not isinstance(scope, str)
                or _SAFE_ID.fullmatch(scope) is None
                or not isinstance(snapshot, str)
                or _SHA256.fullmatch(snapshot) is None
            ):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_preview_store_corrupt",
                    "组卷候选缺少题库范围或快照，不能确认。",
                    503,
                )
            return scope, snapshot

    def begin_export(
        self,
        owner_id: str,
        session_id: str,
        approval_id: str,
        *,
        current_data_snapshot_id: str,
    ) -> dict[str, Any]:
        now = float(self._clock())
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            self._expire(document, now)
            approval = document["approvals"].get(approval_id)
            candidate = (
                document["candidates"].get(approval.get("candidate_id"))
                if isinstance(approval, dict)
                else None
            )
            if isinstance(approval, dict) and approval.get("status") == "expired":
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_expired",
                    "这次整卷预览确认已过期，请重新预览并确认。",
                    410,
                )
            if not isinstance(approval, dict) or not isinstance(candidate, dict):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_not_found",
                    "当前教师会话中没有这个预览批准。",
                    404,
                )
            if (
                approval.get("status") != "approved"
                or approval.get("data_snapshot_id") != current_data_snapshot_id
                or candidate.get("data_snapshot_id") != current_data_snapshot_id
                or approval.get("preview_revision")
                != document.get("active_preview_revision")
                or approval.get("preview_revision") != candidate.get("preview_revision")
                or approval.get("preview_snapshot_sha256")
                != candidate.get("preview_snapshot_sha256")
            ):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_approval_stale",
                    "预览批准与当前题库或候选修订不一致，请重新预览。",
                    409,
                )
            existing_job = approval.get("export_job_id")
            if isinstance(existing_job, str):
                return {
                    "kind": "existing",
                    "job_id": existing_job,
                    "approval": self._approval_public(approval),
                }
            if isinstance(approval.get("export_reservation"), dict):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_export_in_progress",
                    "同一预览批准正在启动导出，请稍后查看任务。",
                    409,
                )
            reservation_id = f"PBX-{uuid.uuid4().hex}"
            approval["export_reservation"] = {
                "reservation_id": reservation_id,
                "created_epoch": now,
            }
            document["updated_at"] = _iso_timestamp(now)
            _atomic_write_store(path, document)
            return {
                "kind": "reserved",
                "reservation_id": reservation_id,
                "candidate": deepcopy(candidate["candidate"]),
                "normalized_request": deepcopy(candidate["normalized_request"]),
                "approval": self._approval_public(approval),
            }

    def finish_export(
        self,
        owner_id: str,
        session_id: str,
        approval_id: str,
        reservation_id: str,
        *,
        job_id: str | None,
    ) -> dict[str, Any]:
        now = float(self._clock())
        path, owner_sha256, session_sha256 = self._path(owner_id, session_id)
        with self._lock:
            document = self._read(path, owner_sha256, session_sha256)
            approval = document["approvals"].get(approval_id)
            reservation = (
                approval.get("export_reservation")
                if isinstance(approval, dict)
                else None
            )
            if (
                not isinstance(approval, dict)
                or not isinstance(reservation, dict)
                or reservation.get("reservation_id") != reservation_id
            ):
                raise PaperBlueprintWorkbenchError(
                    "paper_blueprint_export_cas_failed",
                    "导出任务与预览批准的并发状态不一致。",
                    409,
                )
            approval.pop("export_reservation", None)
            if job_id is not None:
                if not isinstance(job_id, str) or re.fullmatch(r"WBEXP-[0-9a-f]{32}", job_id) is None:
                    raise PaperBlueprintWorkbenchError(
                        "paper_blueprint_export_job_invalid",
                        "导出器返回的任务标识不正确。",
                        503,
                    )
                approval["export_job_id"] = job_id
            document["updated_at"] = _iso_timestamp(now)
            _atomic_write_store(path, document)
            return self._approval_public(approval)


class PaperBlueprintWorkbench:
    """Pure deterministic optimizer over injected, immutable projections."""

    def optimize(
        self,
        payload: Mapping[str, Any],
        *,
        theme_catalog: Mapping[str, Any] | None = None,
        question_search_result: Mapping[str, Any] | None = None,
        paper_format_preset: Mapping[str, Any] | None = None,
        curriculum_catalog: Mapping[str, Any] | None = None,
        curriculum_mapping: Mapping[str, Any] | None = None,
        metadata_overlay: Mapping[str, Any] | None = None,
        theme_catalog_loader: Callable[[str, str], Mapping[str, Any]] | None = None,
        question_search_loader: Callable[[Mapping[str, Any]], Mapping[str, Any]]
        | None = None,
        preset: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if paper_format_preset is not None and preset is not None:
            raise PaperBlueprintWorkbenchError(
                "paper_blueprint_request_invalid", "preset 只能通过一个参数传入。"
            )
        base_value = paper_format_preset if paper_format_preset is not None else preset
        if base_value is None:
            base_value = default_shanghai_theme_preset()
        try:
            base_preset = validate_paper_format_preset(base_value)
        except PaperFormatContractError as exc:
            raise PaperBlueprintWorkbenchError(
                exc.code, exc.message_zh, exc.status
            ) from exc
        request = _normalize_payload(payload, base_preset)
        if theme_catalog is None:
            if theme_catalog_loader is None:
                raise PaperBlueprintWorkbenchError(
                    "theme_catalog_required", "智能组卷必须注入现有完整主题目录。"
                )
            theme_catalog = theme_catalog_loader(
                request["scope"], request["data_snapshot_id"]
            )
        catalog, catalog_wrapper = _normalize_catalog(
            theme_catalog,
            scope=request["scope"],
            snapshot_id=request["data_snapshot_id"],
        )
        preliminary_theme_atomics = _catalog_theme_atomic_ids(catalog)
        preliminary_theme_order = _catalog_theme_order_completeness(catalog)
        curriculum_indexes = _curriculum_catalog_indexes(curriculum_catalog)
        curriculum_edges = _normalize_curriculum_mapping(curriculum_mapping)
        search_payload: dict[str, Any] = {
            "scope": request["scope"],
            "limit": 50,
        }
        if request["curriculum"] is not None:
            search_payload["curriculum"] = deepcopy(request["curriculum"])
        question_search_result = _complete_question_search_pages(
            question_search_result,
            loader=question_search_loader,
            initial_payload=search_payload,
        )
        allowed_theme_ids, highlights, normalized_search = _normalize_search_result(
            question_search_result,
            scope=request["scope"],
            snapshot_id=request["data_snapshot_id"],
            theme_atomic_ids=preliminary_theme_atomics,
            theme_order_complete=preliminary_theme_order,
            curriculum_selector=request["curriculum"],
            curriculum_mapping_present=bool(curriculum_edges),
        )
        (
            theme_index,
            atomic_index,
            printed_index,
            theme_atomic_ids,
        ) = _build_indexes_and_atomics(
            catalog,
            metadata_overlay=metadata_overlay,
            curriculum_mapping=curriculum_edges,
            curriculum_indexes=curriculum_indexes,
            curriculum_selector=request["curriculum"],
            highlights=highlights,
            hard=request["hard_constraints"],
            paper_answer_lines=request["paper"]["answer_space_lines"],
            paper_score_per_atomic=request["paper"]["score_per_atomic"],
            paper_time_per_atomic_minutes=request["paper"][
                "time_per_atomic_minutes"
            ],
        )
        if theme_atomic_ids != preliminary_theme_atomics:
            raise PaperBlueprintWorkbenchError(
                "theme_catalog_invalid", "主题题链在标准化过程中发生身份漂移。", 409
            )
        _assert_ordering_references(request["ordering"], set(theme_index))
        theme_overlay = _overlay_section(metadata_overlay, "themes")
        _assert_theme_overlay_references(theme_overlay, set(theme_index))
        (
            units,
            required_theme_ids,
            required_atomic_ids,
            pre_conflicts,
            gaps,
        ) = _build_theme_units(
            request=request,
            theme_index=theme_index,
            atomic_index=atomic_index,
            printed_index=printed_index,
            theme_atomic_ids=theme_atomic_ids,
            allowed_theme_ids=allowed_theme_ids,
            highlights=highlights,
            metadata_overlay=metadata_overlay,
        )
        search_summary = {
            "question_search_used": normalized_search is not None,
            "allowed_theme_count": len(allowed_theme_ids),
            "highlighted_atomic_count": sum(len(value) for value in highlights.values()),
            "curriculum_selector": deepcopy(request["curriculum"]),
            "explicit_curriculum_mapping_used": bool(curriculum_edges),
            "knowledge_tag_inference_used": (
                bool(normalized_search.get("curriculum", {}).get(
                    "knowledge_tag_inference_used"
                ))
                if isinstance(normalized_search, Mapping)
                and isinstance(normalized_search.get("curriculum"), Mapping)
                else False
            ),
            "question_search_page": (
                deepcopy(normalized_search.get("page"))
                if isinstance(normalized_search, Mapping)
                else None
            ),
        }
        if (
            request["mode"] == MODE_DAILY_PRACTICE
            and request["preferences"]["selection_unit"] == "dependency"
            and not any(highlights.values())
            and not required_atomic_ids
        ):
            pre_conflicts.append(
                _conflict(
                    "daily_dependency_target_missing",
                    "依赖闭包练习必须由 question search 的 atomic 高亮或教师必选题确定目标；系统不能自行猜题。",
                )
            )
        if pre_conflicts:
            missing = _missing_question_requirements(
                units=units, request=request, atomic_index=atomic_index
            )
            relaxations = _relaxation_options(
                request=request,
                conflicts=pre_conflicts,
                gaps=gaps,
                missing_questions=missing,
            )
            return _base_response(
                request=request,
                status="infeasible",
                candidates=[],
                conflicts=pre_conflicts,
                gaps=gaps,
                missing_questions=missing,
                relaxations=relaxations,
                search_diagnostics=search_summary,
            )
        feasible_sets, enumeration = _enumerate_feasible_sets(
            units,
            request=request,
            atomic_index=atomic_index,
            required_theme_ids=required_theme_ids,
            required_atomic_ids=required_atomic_ids,
            theme_overlay=theme_overlay,
        )
        search_summary["optimizer"] = enumeration
        candidates: list[dict[str, Any]] = []
        build_conflicts: list[dict[str, Any]] = []
        for ordered_units, coverage, objective, _ in feasible_sets:
            try:
                candidates.append(
                    _build_candidate(
                        rank=len(candidates) + 1,
                        ordered_units=ordered_units,
                        coverage=coverage,
                        objective=objective,
                        request=request,
                        base_preset=base_preset,
                        catalog_wrapper=catalog_wrapper,
                        atomic_index=atomic_index,
                    )
                )
            except PaperBlueprintWorkbenchError as exc:
                build_conflicts.append(
                    _conflict(
                        exc.code,
                        exc.message_zh,
                        **deepcopy(exc.details),
                    )
                )
        if candidates:
            warnings: list[dict[str, Any]] = []
            if enumeration["truncated"]:
                warnings.append(
                    {
                        "code": "optimizer_search_truncated",
                        "message_zh": "组合搜索达到确定性预算；已返回的候选全部满足硬约束，但不声称穷尽所有解。",
                    }
                )
            if gaps:
                warnings.append(
                    {
                        "code": "unused_inventory_gaps_present",
                        "message_zh": "部分未入选主题仍缺少分值、用时、答案或去重元数据；不影响当前候选。",
                    }
                )
            return _base_response(
                request=request,
                status="feasible",
                candidates=candidates[: request["candidate_count"]],
                conflicts=[],
                gaps=gaps,
                missing_questions=[],
                relaxations=[],
                search_diagnostics=search_summary,
                warnings=warnings,
            )
        conflicts = list(build_conflicts)
        if not units:
            conflicts.append(
                _conflict(
                    "eligible_theme_inventory_empty",
                    "没有完整主题同时满足当前教材、来源年份、答案资格和元数据硬过滤。",
                )
            )
        failure_counts = enumeration.get("failure_counts", {})
        failure_messages = {
            "theme_count_mismatch": "完整主题数量无法满足模拟考试模板。",
            "total_score_mismatch": "现有完整主题的显式分值无法组成精确总分。",
            "duration_mismatch": "现有题目的显式预计用时无法组成精确时长。",
            "duration_exceeded": "满足其他约束的候选均超过考试时长。",
            "duplicate_cluster_conflict": "满足其他约束的候选均出现重复簇。",
            "theme_order_cycle": "教师拖动顺序与教材先修关系形成循环。",
        }
        for code, message in failure_messages.items():
            if failure_counts.get(code):
                conflicts.append(_conflict(code, message, state_count=failure_counts[code]))
        for axis, rules in request["hard_constraints"]["coverage"].items():
            prefix = f"coverage_{axis}_"
            for label, rule in rules.items():
                failure_code = f"{prefix}{label}"
                if failure_counts.get(failure_code):
                    conflicts.append(
                        _conflict(
                            "coverage_bound_unsatisfied",
                            f"{axis}={label} 的覆盖数量无法落在教师设置的 min/max 范围内。",
                            axis=axis,
                            label=label,
                            minimum=rule.minimum,
                            maximum=rule.maximum,
                            state_count=failure_counts[failure_code],
                        )
                    )
        for label, rule in request["hard_constraints"][
            "difficulty_distribution"
        ].items():
            failure_code = f"difficulty_{label}"
            if failure_counts.get(failure_code):
                conflicts.append(
                    _conflict(
                        "difficulty_bound_unsatisfied",
                        f"难度 {label} 的数量无法落在教师设置的 min/max 范围内。",
                        label=label,
                        minimum=rule.minimum,
                        maximum=rule.maximum,
                        state_count=failure_counts[failure_code],
                    )
                )
        if not conflicts:
            conflicts.append(
                _conflict(
                    "hard_constraints_unsatisfied",
                    "现有题库无法同时满足全部硬约束；系统未暗中放宽任何条件。",
                    failure_counts=failure_counts,
                )
            )
        status = "indeterminate" if enumeration["truncated"] else "infeasible"
        if enumeration["truncated"]:
            conflicts.append(
                _conflict(
                    "optimizer_search_budget_exhausted",
                    "组合搜索达到确定性预算且尚未找到可行解；不能据此宣称题库绝对无解。",
                    states_visited=enumeration["states_visited"],
                )
            )
        missing = _missing_question_requirements(
            units=units, request=request, atomic_index=atomic_index
        )
        relaxations = _relaxation_options(
            request=request,
            conflicts=conflicts,
            gaps=gaps,
            missing_questions=missing,
        )
        return _base_response(
            request=request,
            status=status,
            candidates=[],
            conflicts=conflicts,
            gaps=gaps,
            missing_questions=missing,
            relaxations=relaxations,
            search_diagnostics=search_summary,
        )


def build_paper_blueprint_candidates(
    payload: Mapping[str, Any], **inputs: Any
) -> dict[str, Any]:
    """Convenience wrapper for functional callers and tests."""

    return PaperBlueprintWorkbench().optimize(payload, **inputs)


__all__ = [
    "MODES",
    "MODE_DAILY_PRACTICE",
    "MODE_MOCK_EXAM",
    "PREVIEW_APPROVAL_SCHEMA_VERSION",
    "PREVIEW_SCHEMA_VERSION",
    "PREVIEW_STORE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "PaperBlueprintPreviewStore",
    "PaperBlueprintWorkbench",
    "PaperBlueprintWorkbenchError",
    "build_paper_blueprint_candidates",
    "canonical_sha256",
]
