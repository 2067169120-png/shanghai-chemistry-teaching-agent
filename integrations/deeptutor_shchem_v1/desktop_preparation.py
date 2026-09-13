from __future__ import annotations

"""Lean, synchronous preparation contract for the native teacher workbench.

The manager in this module deliberately owns no thread pool and performs no
network calls by itself.  A Qt worker supplies a provider to :meth:`run`, while
the constructor receives a local renderer.  The manager owns only the durable
request/task state, the frozen provider candidate, attempt directories, and
verified artifact receipts.

All output remains a teacher-owned personal preparation candidate.  The
ordinary six-field path is allowed to run without page evidence, but that fact
is explicit in both the normalized request and the canonical candidate.
"""

import hashlib
import inspect
import json
import math
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .desktop_preparation_image_input import (
    PreparationImageInputError,
    image_input_mode,
)
from .desktop_preparation_images import (
    PreparationImageError,
    PreparationImageStore,
    normalize_image_assets,
    normalize_slide_image,
    slide_image_schema,
)
from .desktop_preparation_limits import MAX_MATERIALS
from .desktop_preparation_visual import (
    ComparisonRowCountError,
    normalize_slide_visual,
    slide_visual_schema,
)
from .desktop_preparation_worksheet import normalize_worksheet, worksheet_schema

PREPARATION_REQUEST_SCHEMA_VERSION = "shchem.desktop-preparation-request.v1"
PREPARATION_CANDIDATE_SCHEMA_VERSION = "shchem.desktop-preparation-candidate.v1"
PREPARATION_CANONICAL_SCHEMA_VERSION = "shchem.desktop-preparation-canonical.v1"
PREPARATION_TASK_SCHEMA_VERSION = "shchem.desktop-preparation-task.v1"

_TASK_ID = re.compile(r"^PREP-[0-9a-f]{32}$")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIMING = re.compile(
    r"^\s*(?P<periods>[0-9]+)\s*课时\s*[×xX*]\s*"
    r"(?P<minutes>[0-9]+)\s*分钟\s*$"
)
_MAX_TEXT = 20_000
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_RETURNED_CANDIDATE_BYTES = 16 * 1024 * 1024
_RETURNED_CANDIDATE_FILE = re.compile(
    r"^PREP-[0-9a-f]{32}\.attempt-[0-9]{4}\.candidate\.json$"
)

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "key_value",
        "password",
        "secret",
        "credential",
        "credential_value",
        "authorization",
        "access_token",
        "refresh_token",
        "bear" + "er_token",
    }
)

_OUTPUT_KIND = {
    "ppt": "ppt",
    "lesson_plan": "lesson_plan",
    "joint": "linked_bundle",
    "linked_bundle": "linked_bundle",
}

_LESSON_ROUTE = {
    "新授": "new_lesson",
    "新课": "new_lesson",
    "new_lesson": "new_lesson",
    "复习": "review",
    "review": "review",
    "实验": "experiment",
    "experiment": "experiment",
    "讲评": "exercise_review",
    "习题讲评": "exercise_review",
    "exercise_review": "exercise_review",
    "试卷讲评": "paper_review",
    "paper_review": "paper_review",
    "专题": "special_topic",
    "special_topic": "special_topic",
    "热点": "hotspot",
    "hotspot": "hotspot",
    "其他": "other",
    "other": "other",
}

_REQUEST_FIELDS = frozenset(
    {
        "output_kind",
        "topic",
        "audience",
        "lesson_route",
        "lesson_timing",
        "objective",
        "materials",
        "image_assets",
        "image_input_mode",
        "advanced",
    }
)
_REQUEST_REQUIRED_FIELDS = frozenset(
    _REQUEST_FIELDS - {"advanced", "image_assets", "image_input_mode"}
)
_ADVANCED_FIELDS = (
    "learning_and_experiment",
    "template_and_delivery",
    "homework_and_strategy",
)


class DesktopPreparationError(RuntimeError):
    """Sanitized preparation failure safe to surface in the native UI."""

    def __init__(
        self,
        code: str,
        message_zh: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.retryable = retryable


def _closed_object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": dict(properties),
    }


def preparation_candidate_schema() -> dict[str, Any]:
    """Return the closed schema supplied to a structured-output provider.

    The vocabulary intentionally stays within the conservative subset shared
    by strict-schema provider implementations.  Cardinality, reference, and
    time-closure rules are enforced deterministically after the model call by
    :func:`normalize_preparation_candidate`.
    """

    string_array = {"type": "array", "items": {"type": "string"}}
    integer_array = {"type": "array", "items": {"type": "integer"}}
    definitions: dict[str, Any] = {
        "objective": _closed_object(
            {
                "statement": {"type": "string"},
            }
        ),
        "activity": _closed_object(
            {
                "title": {"type": "string"},
                "objective_numbers": deepcopy(integer_array),
                "minutes": {"type": "integer"},
                "teacher_action": {"type": "string"},
                "student_action": {"type": "string"},
                "materials": deepcopy(string_array),
                "worksheet": worksheet_schema(),
            }
        ),
        "assessment": _closed_object(
            {
                "title": {"type": "string"},
                "objective_numbers": deepcopy(integer_array),
                "activity_numbers": deepcopy(integer_array),
                "evidence_of_learning": {"type": "string"},
                "success_criteria": deepcopy(string_array),
            }
        ),
        "slide": _closed_object(
            {
                "title": {"type": "string"},
                "purpose": {"type": "string"},
                "objective_numbers": deepcopy(integer_array),
                "activity_numbers": deepcopy(integer_array),
                "assessment_numbers": deepcopy(integer_array),
                "minutes": {"type": "integer"},
                "content": deepcopy(string_array),
                "visual": slide_visual_schema(),
                "image": slide_image_schema(),
                "teacher_notes": {"type": "string"},
            }
        ),
        "lesson_stage": _closed_object(
            {
                "title": {"type": "string"},
                "objective_numbers": deepcopy(integer_array),
                "activity_numbers": deepcopy(integer_array),
                "assessment_numbers": deepcopy(integer_array),
                "minutes": {"type": "integer"},
                "teacher_action": {"type": "string"},
                "student_action": {"type": "string"},
                "materials": deepcopy(string_array),
                "assessment": {"type": "string"},
            }
        ),
        "homework_task": _closed_object(
            {
                "instruction": {"type": "string"},
                "objective_numbers": deepcopy(integer_array),
            }
        ),
        "homework": _closed_object(
            {
                "title": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/homework_task"},
                },
                "estimated_minutes": {"type": "integer"},
            }
        ),
        "uncertainty": _closed_object(
            {
                "field": {"type": "string"},
                "description": {"type": "string"},
                "teacher_action": {"type": "string"},
            }
        ),
    }
    schema = _closed_object(
        {
            "title": {"type": "string"},
            "objectives": {
                "type": "array",
                "items": {"$ref": "#/$defs/objective"},
            },
            "activities": {
                "type": "array",
                "items": {"$ref": "#/$defs/activity"},
            },
            "assessments": {
                "type": "array",
                "items": {"$ref": "#/$defs/assessment"},
            },
            "slides": {
                "type": "array",
                "items": {"$ref": "#/$defs/slide"},
            },
            "lesson_stages": {
                "type": "array",
                "items": {"$ref": "#/$defs/lesson_stage"},
            },
            "homework": {"$ref": "#/$defs/homework"},
            "uncertainties": {
                "type": "array",
                "items": {"$ref": "#/$defs/uncertainty"},
            },
        }
    )
    schema["$defs"] = definitions
    return schema


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _strict_json_unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _strict_json_load(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DesktopPreparationError(
            "preparation_state_corrupt", "备课任务记录无法安全读取。"
        ) from exc
    return _strict_json_decode(raw)


def _strict_json_decode(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_unique_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DesktopPreparationError(
            "preparation_state_corrupt", "备课任务记录无法安全读取。"
        ) from exc


def _json_clone(value: Any, *, code: str, message_zh: str) -> Any:
    try:
        return json.loads(_canonical_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DesktopPreparationError(code, message_zh) from exc


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    data = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise DesktopPreparationError(
            "preparation_state_write_failed",
            "备课任务状态无法安全保存。",
            True,
        ) from exc


def _reject_sensitive(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            if normalized_key in _SENSITIVE_FIELD_NAMES or normalized_key.endswith(
                (
                    "_api_key",
                    "_access_token",
                    "_refresh_token",
                    "_bear" + "er_token",
                    "_password",
                    "_credential_value",
                )
            ):
                raise DesktopPreparationError(
                    "preparation_sensitive_field_forbidden",
                    "备课任务中不能包含模型密钥或凭据。",
                )
            _reject_sensitive(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_sensitive(item)


def _text(
    value: Any, field: str, *, allow_empty: bool = False, limit: int = _MAX_TEXT
) -> str:
    if not isinstance(value, str):
        raise DesktopPreparationError(
            "preparation_payload_invalid", f"{field}必须是文字。"
        )
    normalized = value.strip()
    if (not allow_empty and not normalized) or len(normalized) > limit:
        raise DesktopPreparationError(
            "preparation_payload_invalid", f"{field}未填写或超过{limit}字。"
        )
    if "\x00" in normalized:
        raise DesktopPreparationError(
            "preparation_payload_invalid", f"{field}包含不可接受的字符。"
        )
    return normalized


def _timing(value: Any) -> tuple[int, int]:
    if isinstance(value, Mapping):
        if set(value) != {"periods", "minutes_per_period"}:
            raise DesktopPreparationError(
                "preparation_timing_invalid", "课时设置字段不正确。"
            )
        periods = value.get("periods")
        minutes = value.get("minutes_per_period")
    elif isinstance(value, str):
        match = _TIMING.fullmatch(value)
        if match is None:
            raise DesktopPreparationError(
                "preparation_timing_invalid", "课时格式应为“1课时×40分钟”。"
            )
        periods = int(match.group("periods"))
        minutes = int(match.group("minutes"))
    else:
        raise DesktopPreparationError(
            "preparation_timing_invalid", "课时设置格式不正确。"
        )
    if (
        type(periods) is not int
        or type(minutes) is not int
        or not 1 <= periods <= 12
        or not 1 <= minutes <= 180
    ):
        raise DesktopPreparationError(
            "preparation_timing_invalid", "课时数或每课时分钟数超出支持范围。"
        )
    return periods, minutes


def normalize_preparation_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the six teacher fields into one deterministic local request."""

    if not isinstance(payload, Mapping):
        raise DesktopPreparationError(
            "preparation_payload_invalid", "备课请求必须是结构化对象。"
        )
    _reject_sensitive(payload)
    unknown = set(payload) - _REQUEST_FIELDS
    missing = _REQUEST_REQUIRED_FIELDS - set(payload)
    if unknown or missing:
        raise DesktopPreparationError(
            "preparation_payload_invalid", "备课请求字段不完整或包含未知字段。"
        )
    try:
        input_mode = image_input_mode(payload)
    except PreparationImageInputError as exc:
        raise DesktopPreparationError(exc.code, exc.message_zh) from exc
    output_kind = payload.get("output_kind")
    artifact_mode = (
        _OUTPUT_KIND.get(output_kind) if isinstance(output_kind, str) else None
    )
    if artifact_mode is None:
        raise DesktopPreparationError(
            "preparation_kind_invalid", "请选择 PPT、教案或联动输出。"
        )
    route_raw = payload.get("lesson_route")
    route = _LESSON_ROUTE.get(route_raw) if isinstance(route_raw, str) else None
    if route is None:
        raise DesktopPreparationError("preparation_route_invalid", "课程类型不受支持。")
    periods, minutes = _timing(payload.get("lesson_timing"))
    advanced_raw = payload.get("advanced", {})
    if advanced_raw is None:
        advanced_raw = {}
    if not isinstance(advanced_raw, Mapping) or set(advanced_raw) - set(
        _ADVANCED_FIELDS
    ):
        raise DesktopPreparationError(
            "preparation_advanced_invalid", "精细设置字段不正确。"
        )
    advanced = {
        key: _text(advanced_raw.get(key, ""), f"精细设置/{key}", allow_empty=True)
        for key in _ADVANCED_FIELDS
    }
    normalized = {
        "schema_version": PREPARATION_REQUEST_SCHEMA_VERSION,
        "artifact_mode": artifact_mode,
        "topic": _text(payload.get("topic"), "课题/章节"),
        "audience": _text(payload.get("audience"), "授课对象"),
        "lesson_route": route,
        "timing": {
            "periods": periods,
            "minutes_per_period": minutes,
            "total_minutes": periods * minutes,
        },
        "objective": _text(payload.get("objective"), "目标/考试定位"),
        "materials": _text(payload.get("materials"), "本次资料范围", limit=MAX_MATERIALS),
        "advanced": advanced,
        "source_basis": {
            "mode": "teacher_input_only",
            "evidence_ids": [],
            "statement_zh": (
                "本请求由教师六字段输入驱动，未绑定原页证据；仅生成个人备课候选，"
                "其中事实、化学内容与适用性须由教师复核。"
            ),
        },
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
        "official_claim_allowed": False,
    }
    if payload.get("image_assets"):
        try:
            normalized["image_assets"] = normalize_image_assets(payload["image_assets"])
            normalized["source_basis"] = {
                "mode": "teacher_text_with_local_images",
                "evidence_ids": [],
                "statement_zh": (
                    "本请求包含教师输入及按内容摘要绑定的本地图片。模型只接收图片说明，"
                    "不接收图片像素；图片内容、来源权威性及教学适用性尚未经审核。"
                    "仅生成个人备课候选，须由教师复核。"
                ),
            }
        except PreparationImageError as exc:
            raise DesktopPreparationError(exc.code, exc.message_zh) from exc
    elif "image_assets" in payload and payload["image_assets"] != []:
        raise DesktopPreparationError("preparation_image_invalid", "图片列表不正确。")
    # Omit the historical default so old requests retain their exact identity.
    if input_mode == "vision":
        normalized["image_input_mode"] = input_mode
        if normalized.get("image_assets"):
            normalized["source_basis"] = {
                "mode": "teacher_text_with_selected_image_pixels",
                "evidence_ids": [],
                "statement_zh": (
                    "本请求将教师输入、图片说明和所选原图发送给教师确认的模型。"
                    "图片内容、模型识读结果、来源权威性及教学适用性尚未经审核；"
                    "仅生成个人备课候选，须由教师复核。"
                ),
            }
    return _json_clone(
        normalized,
        code="preparation_payload_invalid",
        message_zh="备课请求无法规范化为严格 JSON。",
    )


def _candidate_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    try:
        return _text(value, field, allow_empty=allow_empty)
    except DesktopPreparationError as exc:
        raise DesktopPreparationError(
            "preparation_candidate_invalid", exc.message_zh, True
        ) from exc


def _candidate_string_list(
    value: Any,
    field: str,
    *,
    require_nonempty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (require_nonempty and not value):
        raise DesktopPreparationError(
            "preparation_candidate_invalid", f"{field}必须是完整文字清单。", True
        )
    return [
        _candidate_text(item, f"{field}/{index}") for index, item in enumerate(value, 1)
    ]


def _candidate_minutes(value: Any, field: str, *, allow_zero: bool = True) -> int:
    lower = 0 if allow_zero else 1
    if type(value) is not int or not lower <= value <= 2160:
        raise DesktopPreparationError(
            "preparation_candidate_invalid", f"{field}的时间设置不正确。", True
        )
    return value


def _reference_ids(
    value: Any,
    *,
    count: int,
    prefix: str,
    field: str,
    required: bool,
) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise DesktopPreparationError(
            "preparation_candidate_invalid", f"{field}引用不完整。", True
        )
    result: list[str] = []
    seen: set[int] = set()
    for raw in value:
        if type(raw) is not int or not 1 <= raw <= count or raw in seen:
            raise DesktopPreparationError(
                "preparation_candidate_invalid", f"{field}含无效或重复引用。", True
            )
        seen.add(raw)
        result.append(f"{prefix}{raw:02d}")
    return result


def _allocate_minutes(weights: Sequence[int], total: int, field: str) -> list[int]:
    if not weights or len(weights) > total:
        raise DesktopPreparationError(
            "preparation_candidate_time_invalid",
            f"{field}数量超过可分配的课堂分钟数。",
            True,
        )
    if all(weight >= 1 for weight in weights) and sum(weights) == total:
        return list(weights)
    normalized_weights = [max(1, int(weight)) for weight in weights]
    remaining = total - len(weights)
    if remaining == 0:
        return [1] * len(weights)
    weight_sum = sum(normalized_weights)
    exact = [remaining * weight / weight_sum for weight in normalized_weights]
    floor_values = [math.floor(value) for value in exact]
    result = [1 + value for value in floor_values]
    remainder = remaining - sum(floor_values)
    ranked = sorted(
        range(len(weights)),
        key=lambda index: (-(exact[index] - floor_values[index]), index),
    )
    for index in ranked[:remainder]:
        result[index] += 1
    return result


def _exclusive_activity_groups(
    rows: Sequence[Mapping[str, Any]], activity_ids: Sequence[str]
) -> dict[str, list[int]] | None:
    groups: dict[str, list[int]] = {activity_id: [] for activity_id in activity_ids}
    for index, row in enumerate(rows):
        linked = row["activity_ids"]
        if len(linked) != 1 or linked[0] not in groups:
            return None
        groups[linked[0]].append(index)
    return groups if all(groups.values()) else None


def _activity_sequence(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    sequence: list[str] = []
    for row in rows:
        activity_id = row["activity_ids"][0]
        if not sequence or sequence[-1] != activity_id:
            sequence.append(activity_id)
    return sequence


def _grouped_stage_times_agree(
    activities: Sequence[Mapping[str, Any]],
    slides: Sequence[Mapping[str, Any]],
    stages: Sequence[Mapping[str, Any]],
) -> bool:
    """Check an already timed partition without inferring any allocation.

    A stage may intentionally group several activities. Their existing minute
    budgets and exclusively linked slide budgets must agree, in the same
    uninterrupted order. Overlapping or missing groups remain unresolved.
    """
    budgets = {row["id"]: row["minutes"] for row in activities}
    sequence = [activity_id for row in stages for activity_id in row["activity_ids"]]
    if (
        not all(row["activity_ids"] for row in stages)
        or len(sequence) != len(budgets)
        or set(sequence) != set(budgets)
    ):
        return False
    slide_groups = _exclusive_activity_groups(slides, list(budgets))
    if slide_groups is None or _activity_sequence(slides) != sequence:
        return False
    if any(
        sum(slides[index]["minutes"] for index in indexes) != budgets[activity_id]
        for activity_id, indexes in slide_groups.items()
    ):
        return False
    return all(
        sum(budgets[activity_id] for activity_id in row["activity_ids"])
        == row["minutes"]
        for row in stages
    )


def _align_candidate_timing(
    activities: list[dict[str, Any]],
    slides: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    raw: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Use the executable lesson timeline when explicit links permit it.

    Multiple slides can share one activity budget. This does not invent a
    slide-to-stage mapping or claim that classroom pacing has been validated.
    """
    activity_ids = [row["id"] for row in activities]
    stage_groups = _exclusive_activity_groups(stages, activity_ids)
    unresolved = ""
    if stage_groups is None:
        if not _grouped_stage_times_agree(activities, slides, stages):
            unresolved = (
                "教案的多活动分组、页面归属或已有分钟数未能对应，不能唯一核对活动时间。"
            )
    else:
        budgets = {
            activity_id: sum(stages[index]["minutes"] for index in indexes)
            for activity_id, indexes in stage_groups.items()
        }
        for activity in activities:
            activity["minutes"] = budgets[activity["id"]]
        slide_groups = _exclusive_activity_groups(slides, activity_ids)
        if slide_groups is None:
            unresolved = (
                "PPT 存在未关联页、跨活动页或未覆盖的活动，页面时间不能唯一归组。"
            )
        elif _activity_sequence(slides) != _activity_sequence(stages):
            unresolved = (
                "PPT 与教案的活动出现顺序不同，未自动重排页面或分配活动内时间。"
            )
        elif any(len(indexes) > budgets[key] for key, indexes in slide_groups.items()):
            unresolved = "部分活动分钟数少于关联 PPT 页数，无法按每页至少一分钟分配。"
        else:
            for activity_id, indexes in slide_groups.items():
                minutes = _allocate_minutes(
                    [raw["slides"][index]["minutes"] for index in indexes],
                    budgets[activity_id],
                    "活动内PPT页面",
                )
                for index, value in zip(indexes, minutes, strict=True):
                    slides[index]["minutes"] = value

    notes: list[dict[str, str]] = []
    changes: list[str] = []
    for key, label, rows in (
        ("activities", "活动", activities),
        ("slides", "PPT页面", slides),
        ("lesson_stages", "教案环节", stages),
    ):
        before = [row["minutes"] for row in raw[key]]
        after = [row["minutes"] for row in rows]
        if before != after:
            changes.append(f"{label} {before} → {after} 分钟")
    if changes:
        notes.append(
            {
                "field": "timing_adjustment",
                "description": "程序调整的候选时间：" + "；".join(changes) + "。",
                "teacher_action": "教案流程为时间基准；明确归属时活动与页面按其汇总或分配。授课前检查学生操作和反馈时间是否足够。",
            }
        )
    if unresolved:
        notes.append(
            {
                "field": "timing_alignment",
                "description": unresolved
                + "当前仅保证各类时间各自合计符合课时，不代表跨载体一致。",
                "teacher_action": "核对环节与页面的活动关联、顺序和时间；未自动删除页面或猜测对应关系。",
            }
        )
    return notes


def _normalized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == PREPARATION_REQUEST_SCHEMA_VERSION:
        normalized = _json_clone(
            payload,
            code="preparation_payload_invalid",
            message_zh="备课请求不是严格 JSON。",
        )
        expected_top = {
            "schema_version",
            "artifact_mode",
            "topic",
            "audience",
            "lesson_route",
            "timing",
            "objective",
            "materials",
            "advanced",
            "source_basis",
            "candidate_only",
            "teacher_review_required",
            "publication_allowed",
            "official_claim_allowed",
        }
        if "image_input_mode" in normalized:
            expected_top.add("image_input_mode")
        try:
            image_input_mode(normalized)
        except PreparationImageInputError as exc:
            raise DesktopPreparationError(exc.code, exc.message_zh) from exc
        if "image_assets" in normalized:
            expected_top.add("image_assets")
            try:
                if (
                    normalize_image_assets(normalized["image_assets"])
                    != normalized["image_assets"]
                ):
                    raise PreparationImageError("图片说明尚未规范化。")
            except PreparationImageError as exc:
                raise DesktopPreparationError(exc.code, exc.message_zh) from exc
        if set(normalized) != expected_top:
            raise DesktopPreparationError(
                "preparation_payload_invalid", "已规范化备课请求字段不正确。"
            )
        _reject_sensitive(normalized)
        if (
            normalized.get("candidate_only") is not True
            or normalized.get("teacher_review_required") is not True
            or normalized.get("publication_allowed") is not False
            or normalized.get("official_claim_allowed") is not False
        ):
            raise DesktopPreparationError(
                "preparation_payload_invalid", "备课候选边界不正确。"
            )
        return normalized
    return normalize_preparation_payload(payload)


def normalize_preparation_candidate(
    candidate: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a provider result and produce the renderer's canonical model."""

    if not isinstance(candidate, Mapping):
        raise DesktopPreparationError(
            "preparation_candidate_invalid", "模型未返回结构化备课候选。", True
        )
    _reject_sensitive(candidate)
    raw = _json_clone(
        candidate,
        code="preparation_candidate_invalid",
        message_zh="模型返回的备课候选不是严格 JSON。",
    )
    # Existing raw drafts remain valid. Work on the clone and keep legacy
    # canonical bytes unchanged when no visual structure was supplied.
    if isinstance(raw.get("slides"), list):
        for slide in raw["slides"]:
            if isinstance(slide, dict):
                slide.setdefault("visual", None)
                slide.setdefault("image", None)
    if isinstance(raw.get("activities"), list):
        for activity in raw["activities"]:
            if isinstance(activity, dict):
                activity.setdefault("worksheet", None)
    schema = preparation_candidate_schema()
    errors = sorted(
        Draft202012Validator(schema).iter_errors(raw),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise DesktopPreparationError(
            "preparation_candidate_invalid", "模型返回的备课候选未通过结构校验。", True
        )
    request = _normalized_payload(payload)
    objectives_raw = raw["objectives"]
    activities_raw = raw["activities"]
    assessments_raw = raw["assessments"]
    slides_raw = raw["slides"]
    stages_raw = raw["lesson_stages"]
    if not all(
        isinstance(value, list) and value
        for value in (
            objectives_raw,
            activities_raw,
            assessments_raw,
            slides_raw,
            stages_raw,
        )
    ):
        raise DesktopPreparationError(
            "preparation_candidate_invalid",
            "备课候选必须同时包含目标、活动、评价、PPT 页面和教案环节。",
            True,
        )
    objective_count = len(objectives_raw)
    activity_count = len(activities_raw)
    assessment_count = len(assessments_raw)
    total_minutes = int(request["timing"]["total_minutes"])

    objectives = [
        {
            "id": f"O{index:02d}",
            "statement": _candidate_text(row["statement"], f"目标{index}"),
        }
        for index, row in enumerate(objectives_raw, 1)
    ]

    activity_weights = [
        _candidate_minutes(row["minutes"], f"活动{index}")
        for index, row in enumerate(activities_raw, 1)
    ]
    activity_minutes = _allocate_minutes(activity_weights, total_minutes, "活动")
    activities: list[dict[str, Any]] = []
    for index, (row, minutes) in enumerate(
        zip(activities_raw, activity_minutes, strict=True), 1
    ):
        activities.append(
            {
                "id": f"A{index:02d}",
                "title": _candidate_text(row["title"], f"活动{index}/标题"),
                "objective_ids": _reference_ids(
                    row["objective_numbers"],
                    count=objective_count,
                    prefix="O",
                    field=f"活动{index}/目标",
                    required=True,
                ),
                "minutes": minutes,
                "teacher_action": _candidate_text(
                    row["teacher_action"], f"活动{index}/教师行动"
                ),
                "student_action": _candidate_text(
                    row["student_action"], f"活动{index}/学生活动"
                ),
                "materials": _candidate_string_list(
                    row["materials"], f"活动{index}/材料"
                ),
            }
        )
        try:
            worksheet = normalize_worksheet(row["worksheet"])
        except ValueError:
            raise DesktopPreparationError(
                "preparation_candidate_worksheet_invalid",
                f"活动{index}的学习单任务或填写区域不正确。",
                True,
            ) from None
        if worksheet is not None:
            activities[-1]["worksheet"] = worksheet

    assessments: list[dict[str, Any]] = []
    for index, row in enumerate(assessments_raw, 1):
        assessments.append(
            {
                "id": f"E{index:02d}",
                "title": _candidate_text(row["title"], f"评价{index}/标题"),
                "objective_ids": _reference_ids(
                    row["objective_numbers"],
                    count=objective_count,
                    prefix="O",
                    field=f"评价{index}/目标",
                    required=True,
                ),
                "activity_ids": _reference_ids(
                    row["activity_numbers"],
                    count=activity_count,
                    prefix="A",
                    field=f"评价{index}/活动",
                    required=True,
                ),
                "evidence_of_learning": _candidate_text(
                    row["evidence_of_learning"], f"评价{index}/学习证据"
                ),
                "success_criteria": _candidate_string_list(
                    row["success_criteria"],
                    f"评价{index}/成功标准",
                    require_nonempty=True,
                ),
            }
        )

    activity_objectives = {
        objective_id
        for activity in activities
        for objective_id in activity["objective_ids"]
    }
    assessment_objectives = {
        objective_id
        for assessment in assessments
        for objective_id in assessment["objective_ids"]
    }
    assessed_activities = {
        activity_id
        for assessment in assessments
        for activity_id in assessment["activity_ids"]
    }
    expected_objectives = {row["id"] for row in objectives}
    expected_activities = {row["id"] for row in activities}
    if (
        activity_objectives != expected_objectives
        or assessment_objectives != expected_objectives
        or assessed_activities != expected_activities
    ):
        raise DesktopPreparationError(
            "preparation_candidate_linkage_invalid",
            "每个目标必须有活动和评价，每个活动也必须进入评价。",
            True,
        )

    slide_weights = [
        _candidate_minutes(row["minutes"], f"PPT页面{index}")
        for index, row in enumerate(slides_raw, 1)
    ]
    slide_minutes = _allocate_minutes(slide_weights, total_minutes, "PPT页面")
    slides: list[dict[str, Any]] = []
    for index, (row, minutes) in enumerate(
        zip(slides_raw, slide_minutes, strict=True), 1
    ):
        slides.append(
            {
                "id": f"S{index:02d}",
                "order": index,
                "title": _candidate_text(row["title"], f"PPT页面{index}/标题"),
                "purpose": _candidate_text(row["purpose"], f"PPT页面{index}/作用"),
                "objective_ids": _reference_ids(
                    row["objective_numbers"],
                    count=objective_count,
                    prefix="O",
                    field=f"PPT页面{index}/目标",
                    required=False,
                ),
                "activity_ids": _reference_ids(
                    row["activity_numbers"],
                    count=activity_count,
                    prefix="A",
                    field=f"PPT页面{index}/活动",
                    required=False,
                ),
                "assessment_ids": _reference_ids(
                    row["assessment_numbers"],
                    count=assessment_count,
                    prefix="E",
                    field=f"PPT页面{index}/评价",
                    required=False,
                ),
                "minutes": minutes,
                "content": _candidate_string_list(
                    row["content"], f"PPT页面{index}/内容", require_nonempty=True
                ),
                "teacher_notes": _candidate_text(
                    row["teacher_notes"], f"PPT页面{index}/讲稿", allow_empty=True
                ),
            }
        )
        try:
            visual = normalize_slide_visual(row["visual"])
        except ComparisonRowCountError as exc:
            raise DesktopPreparationError(
                "preparation_candidate_visual_invalid",
                f"PPT页面{index}：{exc.message_zh}",
                True,
            ) from None
        except ValueError:
            raise DesktopPreparationError(
                "preparation_candidate_visual_invalid",
                f"PPT页面{index}的比较维度、流程节点或文字长度不正确，请拆分后重试。",
                True,
            ) from None
        if visual is not None:
            slides[-1]["visual"] = visual
        try:
            slide_image = normalize_slide_image(
                row["image"], request.get("image_assets", [])
            )
            if slide_image is not None and visual is not None:
                raise PreparationImageError("同一页不能同时采用图片和比较或流程版式。")
        except PreparationImageError as exc:
            raise DesktopPreparationError(exc.code, exc.message_zh, True) from exc
        if slide_image is not None:
            slides[-1]["image"] = slide_image

    stage_weights = [
        _candidate_minutes(row["minutes"], f"教案环节{index}")
        for index, row in enumerate(stages_raw, 1)
    ]
    stage_minutes = _allocate_minutes(stage_weights, total_minutes, "教案环节")
    lesson_stages: list[dict[str, Any]] = []
    for index, (row, minutes) in enumerate(
        zip(stages_raw, stage_minutes, strict=True), 1
    ):
        lesson_stages.append(
            {
                "id": f"L{index:02d}",
                "order": index,
                "title": _candidate_text(row["title"], f"教案环节{index}/标题"),
                "objective_ids": _reference_ids(
                    row["objective_numbers"],
                    count=objective_count,
                    prefix="O",
                    field=f"教案环节{index}/目标",
                    required=True,
                ),
                "activity_ids": _reference_ids(
                    row["activity_numbers"],
                    count=activity_count,
                    prefix="A",
                    field=f"教案环节{index}/活动",
                    required=True,
                ),
                "assessment_ids": _reference_ids(
                    row["assessment_numbers"],
                    count=assessment_count,
                    prefix="E",
                    field=f"教案环节{index}/评价",
                    required=False,
                ),
                "minutes": minutes,
                "teacher_action": _candidate_text(
                    row["teacher_action"], f"教案环节{index}/教师行动"
                ),
                "student_action": _candidate_text(
                    row["student_action"], f"教案环节{index}/学生活动"
                ),
                "materials": _candidate_string_list(
                    row["materials"], f"教案环节{index}/材料"
                ),
                "assessment": _candidate_text(
                    row["assessment"], f"教案环节{index}/评价说明", allow_empty=True
                ),
            }
        )

    timing_notes = _align_candidate_timing(activities, slides, lesson_stages, raw)

    homework_raw = raw["homework"]
    homework_tasks_raw = homework_raw["tasks"]
    if not homework_tasks_raw:
        raise DesktopPreparationError(
            "preparation_candidate_invalid", "备课候选必须包含作业任务。", True
        )
    homework = {
        "title": _candidate_text(homework_raw["title"], "作业标题"),
        "tasks": [
            {
                "id": f"H{index:02d}",
                "instruction": _candidate_text(row["instruction"], f"作业{index}"),
                "objective_ids": _reference_ids(
                    row["objective_numbers"],
                    count=objective_count,
                    prefix="O",
                    field=f"作业{index}/目标",
                    required=True,
                ),
            }
            for index, row in enumerate(homework_tasks_raw, 1)
        ],
        "estimated_minutes": _candidate_minutes(
            homework_raw["estimated_minutes"], "作业预计时间"
        ),
    }
    uncertainties = [
        {
            "field": _candidate_text(row["field"], f"不确定项{index}/字段"),
            "description": _candidate_text(row["description"], f"不确定项{index}/说明"),
            "teacher_action": _candidate_text(
                row["teacher_action"], f"不确定项{index}/教师行动"
            ),
        }
        for index, row in enumerate(raw["uncertainties"], 1)
    ]
    uncertainties.extend(timing_notes)
    uncertainties.append(
        {
            "field": "source_basis",
            "description": (
                request["source_basis"]["statement_zh"]
                if request.get("image_assets")
                else "本候选未绑定原页证据，内容仅来自教师六字段与模型草拟。"
            ),
            "teacher_action": "使用前逐项核对化学事实、教材范围、题目来源与课堂适用性。",
        }
    )

    canonical_without_id: dict[str, Any] = {
        "schema_version": PREPARATION_CANONICAL_SCHEMA_VERSION,
        "title": _candidate_text(raw["title"], "备课标题"),
        "topic": request["topic"],
        "audience": request["audience"],
        "artifact_mode": request["artifact_mode"],
        "lesson_route": request["lesson_route"],
        "timing": deepcopy(request["timing"]),
        "source_basis": deepcopy(request["source_basis"]),
        "objectives": objectives,
        "activities": activities,
        "assessments": assessments,
        "slides": slides,
        "lesson_stages": lesson_stages,
        "homework": homework,
        "uncertainties": uncertainties,
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
        "official_claim_allowed": False,
    }
    if request.get("image_assets"):
        canonical_without_id["image_assets"] = deepcopy(request["image_assets"])
    candidate_id = "PREPCAND-" + _sha256_json(canonical_without_id)[:32]
    canonical = {
        "schema_version": canonical_without_id.pop("schema_version"),
        "candidate_id": candidate_id,
        **canonical_without_id,
    }
    return _json_clone(
        canonical,
        code="preparation_candidate_invalid",
        message_zh="备课候选无法规范化为严格 JSON。",
    )


def _canonical_candidate_digest(candidate: Mapping[str, Any]) -> str:
    without_id = dict(candidate)
    without_id.pop("candidate_id", None)
    schema_version = without_id.pop("schema_version", None)
    return _sha256_json({"schema_version": schema_version, **without_id})


def _validate_canonical_candidate(
    candidate: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "candidate_id",
        "title",
        "topic",
        "audience",
        "artifact_mode",
        "lesson_route",
        "timing",
        "source_basis",
        "objectives",
        "activities",
        "assessments",
        "slides",
        "lesson_stages",
        "homework",
        "uncertainties",
        "candidate_only",
        "teacher_review_required",
        "publication_allowed",
        "official_claim_allowed",
    }
    value = _json_clone(
        candidate,
        code="preparation_seed_invalid",
        message_zh="冻结的备课候选无法读取。",
    )
    request = _normalized_payload(payload)
    if request.get("image_assets"):
        expected_fields.add("image_assets")
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or value.get("schema_version") != PREPARATION_CANONICAL_SCHEMA_VERSION
        or value.get("candidate_only") is not True
        or value.get("teacher_review_required") is not True
        or value.get("publication_allowed") is not False
        or value.get("official_claim_allowed") is not False
        or value.get("topic") != request["topic"]
        or value.get("audience") != request["audience"]
        or value.get("artifact_mode") != request["artifact_mode"]
        or value.get("lesson_route") != request["lesson_route"]
        or value.get("timing") != request["timing"]
        or value.get("source_basis") != request["source_basis"]
        or value.get("image_assets") != request.get("image_assets")
    ):
        raise DesktopPreparationError(
            "preparation_seed_invalid", "冻结的备课候选与当前任务不一致。", True
        )
    expected_id = "PREPCAND-" + _canonical_candidate_digest(value)[:32]
    if value.get("candidate_id") != expected_id:
        raise DesktopPreparationError(
            "preparation_seed_drift", "冻结的备课候选摘要已变化。", True
        )
    total = int(request["timing"]["total_minutes"])
    for field in ("activities", "slides", "lesson_stages"):
        rows = value.get(field)
        valid_rows = isinstance(rows, list) and bool(rows)
        if valid_rows:
            valid_rows = all(
                isinstance(row, Mapping)
                and type(row.get("minutes")) is int
                and row["minutes"] >= 1
                for row in rows
            )
        if not valid_rows or sum(row["minutes"] for row in rows) != total:
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结候选的课堂时间未闭合。", True
            )
    for index, slide in enumerate(value["slides"], 1):
        try:
            picture = normalize_slide_image(
                slide.get("image"), request.get("image_assets", [])
            )
            if picture != slide.get("image") or (picture and slide.get("visual")):
                raise PreparationImageError("图片页面绑定不一致。")
        except PreparationImageError as exc:
            raise DesktopPreparationError(
                "preparation_seed_invalid", exc.message_zh, True
            ) from exc
        try:
            visual = normalize_slide_visual(slide.get("visual"))
        except ComparisonRowCountError as exc:
            raise DesktopPreparationError(
                "preparation_seed_invalid",
                f"冻结候选PPT页面{index}：{exc.message_zh}",
                True,
            ) from None
        except ValueError:
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结候选的页面图形结构不正确。", True
            ) from None
        if visual != slide.get("visual"):
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结候选的页面图形尚未规范化。", True
            )
    for activity in value["activities"]:
        try:
            worksheet = normalize_worksheet(activity.get("worksheet"))
        except ValueError:
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结候选的学习单结构不正确。", True
            ) from None
        if worksheet != activity.get("worksheet"):
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结候选的学习单尚未规范化。", True
            )
    _reject_sensitive(value)
    return value


def _profile_text(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 200
        or any(ord(character) < 32 for character in value)
    ):
        raise DesktopPreparationError("preparation_profile_invalid", f"{field}不正确。")
    return value.strip()


def _contained(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.expanduser().resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DesktopPreparationError(
            "preparation_path_invalid", "备课任务路径超出受控目录。"
        ) from exc
    return resolved


def _relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DesktopPreparationError(
            "preparation_artifact_invalid", "渲染产物文件名不正确。", True
        )
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if (
        path.is_absolute()
        or path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(character in value for character in '<>:"|?*')
    ):
        raise DesktopPreparationError(
            "preparation_artifact_invalid", "渲染产物文件名不正确。", True
        )
    return path


def _invoke_provider(
    provider: Any,
    payload: Mapping[str, Any],
    binding: Mapping[str, Any],
    report_progress: Callable[[Mapping[str, Any]], None],
    is_cancelled: Callable[[], bool],
    *,
    image_data: Mapping[str, bytes] | None = None,
) -> Any:
    target = provider if callable(provider) else getattr(provider, "generate", None)
    if not callable(target):
        raise DesktopPreparationError(
            "preparation_provider_invalid", "备课模型调用器不可用。", True
        )
    schema = preparation_candidate_schema()
    calls = (
        (
            (
                deepcopy(dict(payload)),
                schema,
                deepcopy(dict(binding)),
                report_progress,
                is_cancelled,
            ),
            {},
        ),
        (
            (deepcopy(dict(payload)),),
            {
                "candidate_schema": schema,
                "profile_binding": deepcopy(dict(binding)),
                "report_progress": report_progress,
                "is_cancelled": is_cancelled,
            },
        ),
        ((deepcopy(dict(payload)), schema, deepcopy(dict(binding))), {}),
        ((deepcopy(dict(payload)), schema), {}),
        ((deepcopy(dict(payload)),), {}),
    )
    if image_input_mode(payload) == "vision" and payload.get("image_assets"):
        # Every compatible invocation must carry the same complete byte map.
        # A legacy text-only adapter must fail, never silently omit images.
        calls = tuple(
            (args, {**kwargs, "image_data": dict(image_data or {})})
            for args, kwargs in calls
        )
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(*calls[0][0], **calls[0][1])
    for args, kwargs in calls:
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return target(*args, **kwargs)
    raise DesktopPreparationError(
        "preparation_provider_invalid", "备课模型调用器签名不兼容。", True
    )


def _invoke_renderer(
    renderer: Any,
    candidate: Mapping[str, Any],
    *,
    output_kind: str,
    output_dir: Path,
    report_progress: Callable[[Mapping[str, Any]], None],
    is_cancelled: Callable[[], bool],
    image_data: Mapping[str, bytes] | None = None,
) -> Any:
    target = renderer if callable(renderer) else getattr(renderer, "render", None)
    if not callable(target):
        raise DesktopPreparationError(
            "preparation_renderer_invalid", "本机备课渲染器不可用。", True
        )
    calls = (
        (
            (deepcopy(dict(candidate)),),
            {
                "output_kind": output_kind,
                "output_dir": output_dir,
                "report_progress": report_progress,
                "is_cancelled": is_cancelled,
            },
        ),
        (
            (
                deepcopy(dict(candidate)),
                output_dir,
                report_progress,
                is_cancelled,
            ),
            {},
        ),
        ((deepcopy(dict(candidate)), output_dir), {}),
        ((deepcopy(dict(candidate)),), {}),
    )
    if image_data:
        # Do not fall back to a legacy signature that silently drops pictures.
        calls = ((calls[0][0], {**calls[0][1], "image_data": image_data}),)
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(*calls[0][0], **calls[0][1])
    for args, kwargs in calls:
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return target(*args, **kwargs)
    raise DesktopPreparationError(
        "preparation_renderer_invalid", "本机备课渲染器签名不兼容。", True
    )


class DesktopPreparationManager:
    """Persistent synchronous task manager for native desktop preparation."""

    def __init__(self, root: str | Path, renderer: Any) -> None:
        raw_root = Path(root).expanduser()
        if raw_root.exists() and raw_root.is_symlink():
            raise DesktopPreparationError(
                "preparation_root_invalid", "备课状态目录不能是符号链接。"
            )
        self.root = raw_root.resolve()
        if self.root.exists() and not self.root.is_dir():
            raise DesktopPreparationError(
                "preparation_root_invalid", "备课状态目录不可用。"
            )
        if not callable(renderer) and not callable(getattr(renderer, "render", None)):
            raise DesktopPreparationError(
                "preparation_renderer_invalid", "本机备课渲染器不可用。"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_root = self.root / "tasks"
        self.seeds_root = self.root / "seeds"
        self.artifacts_root = self.root / "artifacts"
        self.returned_root = self.root / "returned"
        for path in (
            self.tasks_root,
            self.seeds_root,
            self.artifacts_root,
            self.returned_root,
        ):
            if path.exists() and path.is_symlink():
                raise DesktopPreparationError(
                    "preparation_root_invalid", "备课状态子目录不能是符号链接。"
                )
            path.mkdir(parents=True, exist_ok=True)
        self.renderer = renderer
        self.image_store = PreparationImageStore(self.root / "images")
        self._lock = threading.RLock()
        self._recover_interrupted_tasks()

    def _task_path(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
            raise DesktopPreparationError(
                "preparation_task_not_found", "找不到这个备课任务。"
            )
        return _contained(self.tasks_root, self.tasks_root / f"{task_id}.json")

    def _seed_path(self, task_id: str) -> Path:
        self._task_path(task_id)
        return _contained(
            self.seeds_root, self.seeds_root / f"{task_id}.candidate.json"
        )

    def _returned_path(
        self, task_id: str, *, attempt: int | None = None, filename: str | None = None
    ) -> Path:
        self._task_path(task_id)
        if filename is None:
            if type(attempt) is not int or attempt < 1 or attempt > 9999:
                raise DesktopPreparationError(
                    "preparation_returned_candidate_invalid",
                    "模型返回稿尝试编号不正确。",
                    True,
                )
            filename = f"{task_id}.attempt-{attempt:04d}.candidate.json"
        if (
            not isinstance(filename, str)
            or _RETURNED_CANDIDATE_FILE.fullmatch(filename) is None
            or not filename.startswith(f"{task_id}.")
        ):
            raise DesktopPreparationError(
                "preparation_returned_candidate_invalid",
                "模型返回稿文件绑定不正确。",
                True,
            )
        return _contained(self.returned_root, self.returned_root / filename)

    @staticmethod
    def _record_digest(task: Mapping[str, Any]) -> str:
        value = dict(task)
        value.pop("record_sha256", None)
        return _sha256_json(value)

    def _write_task(self, task: Mapping[str, Any]) -> None:
        _reject_sensitive(task)
        value = _json_clone(
            task,
            code="preparation_state_write_failed",
            message_zh="备课任务状态不是严格 JSON。",
        )
        value["record_sha256"] = self._record_digest(value)
        _atomic_write_json(self._task_path(str(value["task_id"])), value)
        if isinstance(task, dict):
            task["record_sha256"] = value["record_sha256"]

    def _read_task(self, task_id: str) -> dict[str, Any]:
        path = self._task_path(task_id)
        if not path.is_file() or path.is_symlink():
            raise DesktopPreparationError(
                "preparation_task_not_found", "找不到这个备课任务。"
            )
        value = _strict_json_load(path)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != PREPARATION_TASK_SCHEMA_VERSION
            or value.get("task_id") != task_id
            or value.get("status")
            not in {
                "prepared",
                "running",
                "cancel_requested",
                "completed",
                "failed",
                "cancelled",
            }
            or type(value.get("attempt")) is not int
            or value.get("attempt", -1) < 0
            or not isinstance(value.get("payload"), Mapping)
            or value.get("request_sha256") != _sha256_json(value.get("payload"))
            or value.get("record_sha256") != self._record_digest(value)
            or value.get("candidate_only") is not True
            or value.get("teacher_review_required") is not True
            or value.get("publication_allowed") is not False
            or value.get("official_claim_allowed") is not False
        ):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "备课任务记录不一致。"
            )
        _reject_sensitive(value)
        seed_file = value.get("candidate_seed_file")
        seed_sha = value.get("candidate_seed_sha256")
        if (seed_file is None) != (seed_sha is None):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "备课候选种子绑定不完整。"
            )
        if seed_file is not None and (
            seed_file != self._seed_path(task_id).name
            or not isinstance(seed_sha, str)
            or _SHA256.fullmatch(seed_sha) is None
        ):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "备课候选种子绑定不正确。"
            )
        returned_file = value.get("returned_candidate_file")
        returned_sha = value.get("returned_candidate_sha256")
        if (returned_file is None) != (returned_sha is None):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "模型返回稿绑定不完整。"
            )
        if returned_file is not None and (
            not isinstance(returned_file, str)
            or _RETURNED_CANDIDATE_FILE.fullmatch(returned_file) is None
            or not returned_file.startswith(f"{task_id}.")
            or not isinstance(returned_sha, str)
            or _SHA256.fullmatch(returned_sha) is None
        ):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "模型返回稿绑定不正确。"
            )
        if not isinstance(value.get("artifacts"), list):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "备课产物清单格式不正确。"
            )
        if value["status"] != "completed" and value["artifacts"]:
            raise DesktopPreparationError(
                "preparation_state_corrupt", "未完成任务不能登记备课产物。"
            )
        return value

    @staticmethod
    def _public_task(task: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            key: deepcopy(value)
            for key, value in task.items()
            if key
            not in {
                "payload",
                "candidate_seed_file",
                "candidate_seed_sha256",
                "returned_candidate_file",
                "returned_candidate_sha256",
                "attempt_relpath",
                "record_sha256",
            }
        }
        result["candidate_available"] = task.get("candidate_seed_file") is not None
        result["returned_candidate_available"] = (
            task.get("returned_candidate_file") is not None
        )
        result["candidate_only"] = True
        result["teacher_review_required"] = True
        result["publication_allowed"] = False
        result["official_claim_allowed"] = False
        return result

    def _adopt_seed_if_present(self, task: dict[str, Any]) -> bool:
        path = self._seed_path(str(task["task_id"]))
        if not path.is_file() or path.is_symlink():
            return False
        seed = _strict_json_load(path)
        if not isinstance(seed, Mapping):
            return False
        try:
            normalized = _validate_canonical_candidate(seed, task["payload"])
        except DesktopPreparationError:
            return False
        task["candidate_seed_file"] = path.name
        task["candidate_seed_sha256"] = _sha256_json(normalized)
        task["candidate_id"] = normalized["candidate_id"]
        return True

    def _persist_returned_candidate(
        self, task_id: str, attempt: int, candidate: Any
    ) -> bool:
        """Persist a safe, non-canonical provider response for local recovery.

        This is deliberately best-effort: a normalization failure must retain
        its original diagnostic even if the optional recovery copy cannot be
        written.  The returned response is never treated as a frozen seed and
        therefore cannot be rendered or retried without an explicit repair.
        """

        if not isinstance(candidate, Mapping):
            return False
        try:
            _reject_sensitive(candidate)
            value = _json_clone(
                candidate,
                code="preparation_returned_candidate_invalid",
                message_zh="模型返回稿不是可安全保存的 JSON。",
            )
            if not isinstance(value, Mapping):
                return False
            data = _canonical_bytes(value) + b"\n"
            if not data or len(data) > _MAX_RETURNED_CANDIDATE_BYTES:
                return False
            path = self._returned_path(task_id, attempt=attempt)
            with self._lock:
                task = self._read_task(task_id)
                if task.get("attempt") != attempt or task.get("status") != "running":
                    return False
                if path.is_symlink():
                    return False
                if path.exists():
                    if not path.is_file() or path.read_bytes() != data:
                        return False
                else:
                    _atomic_write_json(path, value)
                saved = path.read_bytes()
                task["returned_candidate_file"] = path.name
                task["returned_candidate_sha256"] = _sha256_bytes(saved)
                task["updated_at"] = _utc_now()
                self._write_task(task)
            return True
        except (DesktopPreparationError, OSError, TypeError, ValueError):
            return False

    def _recover_interrupted_tasks(self) -> None:
        for path in sorted(self.tasks_root.glob("PREP-*.json")):
            if path.is_symlink():
                continue
            task_id = path.stem
            if _TASK_ID.fullmatch(task_id) is None:
                continue
            try:
                task = self._read_task(task_id)
            except DesktopPreparationError:
                continue
            if task["status"] not in {"running", "cancel_requested"}:
                continue
            if task.get("candidate_seed_file") is None:
                self._adopt_seed_if_present(task)
            if task["status"] == "cancel_requested":
                status = "cancelled"
                code = "preparation_cancelled"
                message = "上次桌面进程在取消期间结束，任务已按取消处理。"
            else:
                status = "failed"
                code = "preparation_interrupted"
                message = "上次桌面进程未正常结束，可显式重试。"
            task["status"] = status
            task["updated_at"] = _utc_now()
            task["artifacts"] = []
            task["slide_count"] = 0
            task["quality"] = {
                "status": "not_run" if status == "cancelled" else "failed",
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
            }
            task["progress"] = {
                "stage": status,
                "percent": 100,
                "message_zh": message,
            }
            task["error"] = {
                "code": code,
                "message_zh": message,
                "retryable": True,
            }
            self._write_task(task)

    def prepare(
        self,
        payload: Mapping[str, Any],
        profile_id: str,
        profile_revision: str,
    ) -> dict[str, Any]:
        normalized = normalize_preparation_payload(payload)
        try:
            for asset in normalized.get("image_assets", []):
                self.image_store.load(asset)
        except PreparationImageError as exc:
            raise DesktopPreparationError(exc.code, exc.message_zh) from exc
        binding = {
            "profile_id": _profile_text(profile_id, "模型配置标识"),
            "profile_revision": _profile_text(profile_revision, "模型配置版本"),
        }
        digest = _sha256_json({"payload": normalized, "profile_binding": binding})
        task_id = "PREP-" + digest[:32]
        with self._lock:
            path = self._task_path(task_id)
            if path.is_file():
                existing = self._read_task(task_id)
                if existing.get("task_identity_sha256") != digest:
                    raise DesktopPreparationError(
                        "preparation_task_collision", "备课任务标识发生冲突。"
                    )
                return self._public_task(existing)
            now = _utc_now()
            task: dict[str, Any] = {
                "schema_version": PREPARATION_TASK_SCHEMA_VERSION,
                "task_id": task_id,
                "task_identity_sha256": digest,
                "request_sha256": _sha256_json(normalized),
                "status": "prepared",
                "attempt": 0,
                "topic": normalized["topic"],
                "artifact_mode": normalized["artifact_mode"],
                "profile_id": binding["profile_id"],
                "profile_revision": binding["profile_revision"],
                "payload": normalized,
                "candidate_seed_file": None,
                "candidate_seed_sha256": None,
                "returned_candidate_file": None,
                "returned_candidate_sha256": None,
                "candidate_id": None,
                "attempt_relpath": None,
                "artifacts": [],
                "slide_count": 0,
                "quality": {
                    "status": "not_run",
                    "candidate_only": True,
                    "teacher_review_required": True,
                    "publication_allowed": False,
                    "official_claim_allowed": False,
                },
                "progress": {
                    "stage": "prepared",
                    "percent": 0,
                    "message_zh": "备课请求已冻结，可开始生成个人备课候选。",
                },
                "error": None,
                "created_at": now,
                "updated_at": now,
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
            }
            self._write_task(task)
            return self._public_task(task)

    def revision_source(self, task_id: str) -> dict[str, Any]:
        """Read the hash-checked canonical candidate of a completed task.

        The revision hash is the SHA-256 of the registered ``candidate.json``
        bytes, rather than a re-serialized object digest.  This gives the UI a
        concrete optimistic-concurrency token while the canonical validator
        still protects the renderer from hand-edited or drifted JSON.
        """

        with self._lock:
            task = self._read_task(task_id)
            if task.get("status") != "completed":
                raise DesktopPreparationError(
                    "preparation_revision_source_unavailable",
                    "只有已完成的备课候选可以进行本地修订。",
                )
            _path, content_type, data = self._verified_artifact_path(
                task, "candidate_json"
            )
            if content_type != "application/json":
                raise DesktopPreparationError(
                    "preparation_revision_source_invalid",
                    "备课候选文件类型不正确，不能开始本地修订。",
                )
            source_revision = _sha256_bytes(data)
            try:
                raw_candidate = _strict_json_decode(data)
                candidate = _validate_canonical_candidate(
                    raw_candidate, task["payload"]
                )
            except DesktopPreparationError:
                raise
            except Exception as exc:
                raise DesktopPreparationError(
                    "preparation_revision_source_invalid",
                    "备课候选文件不是可修订的规范记录。",
                ) from exc
            return {
                "task_id": task_id,
                "source_revision": source_revision,
                "candidate": candidate,
                # Kept for the facade's pure local edit step; this field is
                # intentionally omitted from the public facade response.
                "payload": deepcopy(task["payload"]),
            }

    def returned_source(self, task_id: str) -> dict[str, Any]:
        """Read one safely persisted, non-canonical provider response.

        Only a failed task with a returned-response binding can expose this
        source.  The response is checked against the byte hash recorded in the
        task before it is returned, and it is never passed to the renderer.
        """

        with self._lock:
            task = self._read_task(task_id)
            if task.get("status") != "failed" or not task.get(
                "returned_candidate_file"
            ):
                raise DesktopPreparationError(
                    "preparation_returned_source_unavailable",
                    "这个备课任务没有可恢复的模型返回稿。",
                )
            path = self._returned_path(
                task_id, filename=str(task["returned_candidate_file"])
            )
            if path.is_symlink() or not path.is_file():
                raise DesktopPreparationError(
                    "preparation_returned_candidate_missing",
                    "模型返回稿文件已丢失，不能开始本地修订。",
                    True,
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise DesktopPreparationError(
                    "preparation_returned_candidate_missing",
                    "模型返回稿文件无法读取。",
                    True,
                ) from exc
            if (
                not data
                or len(data) > _MAX_RETURNED_CANDIDATE_BYTES
                or _sha256_bytes(data) != task.get("returned_candidate_sha256")
            ):
                raise DesktopPreparationError(
                    "preparation_returned_candidate_drift",
                    "模型返回稿已变化，请重新生成或重新打开原始任务。",
                    True,
                )
            try:
                raw = _strict_json_decode(data)
            except DesktopPreparationError as exc:
                raise DesktopPreparationError(
                    "preparation_returned_candidate_invalid",
                    "模型返回稿不是可修订的严格 JSON。",
                    True,
                ) from exc
            if not isinstance(raw, Mapping):
                raise DesktopPreparationError(
                    "preparation_returned_candidate_invalid",
                    "模型返回稿不是可修订的结构化对象。",
                    True,
                )
            _reject_sensitive(raw)
            error = task.get("error")
            error_message = (
                error.get("message_zh")
                if isinstance(error, Mapping)
                else None
            )
            if not isinstance(error_message, str) or not error_message.strip():
                error_message = "模型返回稿未通过本机结构校验。"
            return {
                "task_id": task_id,
                "source_revision": str(task["returned_candidate_sha256"]),
                "candidate": deepcopy(dict(raw)),
                "payload": deepcopy(task["payload"]),
                "error_message": error_message.strip(),
            }

    def create_returned_revision(
        self,
        task_id: str,
        source_revision: str,
        candidate: Mapping[str, Any],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Create a local-only child from an explicitly repaired response."""

        if (
            not isinstance(source_revision, str)
            or _SHA256.fullmatch(source_revision) is None
        ):
            raise DesktopPreparationError(
                "preparation_revision_stale", "修订来源版本不正确，请重新打开返回稿。"
            )
        if not isinstance(note, str) or len(note) > 2000:
            raise DesktopPreparationError(
                "preparation_revision_note_invalid", "修订说明请控制在2000字以内。"
            )

        with self._lock:
            source = self.returned_source(task_id)
            if source["source_revision"] != source_revision:
                raise DesktopPreparationError(
                    "preparation_revision_stale",
                    "模型返回稿已发生变化，请重新打开最新版本。",
                )
            normalized = _validate_canonical_candidate(candidate, source["payload"])
            now = _utc_now()
            child_id = "PREP-" + uuid.uuid4().hex
            child_task_path = self._task_path(child_id)
            if child_task_path.exists() or child_task_path.is_symlink():
                raise DesktopPreparationError(
                    "preparation_revision_collision",
                    "本地修订任务标识发生冲突，请重新提交。",
                    True,
                )
            seed_path = self._seed_path(child_id)
            payload = deepcopy(source["payload"])
            task: dict[str, Any] = {
                "schema_version": PREPARATION_TASK_SCHEMA_VERSION,
                "task_id": child_id,
                "task_identity_sha256": _sha256_json(
                    {
                        "payload": payload,
                        "source_kind": "teacher_revision",
                        "revision_kind": "returned_candidate_repair",
                        "parent_task_id": task_id,
                        "parent_candidate_sha256": source_revision,
                        "candidate_id": normalized["candidate_id"],
                    }
                ),
                "request_sha256": _sha256_json(payload),
                "status": "prepared",
                "attempt": 0,
                "topic": payload["topic"],
                "artifact_mode": payload["artifact_mode"],
                "profile_id": None,
                "profile_revision": None,
                "payload": payload,
                "candidate_seed_file": seed_path.name,
                "candidate_seed_sha256": _sha256_json(normalized),
                "returned_candidate_file": None,
                "returned_candidate_sha256": None,
                "candidate_id": normalized["candidate_id"],
                "attempt_relpath": None,
                "artifacts": [],
                "slide_count": 0,
                "quality": {
                    "status": "not_run",
                    "candidate_only": True,
                    "teacher_review_required": True,
                    "publication_allowed": False,
                    "official_claim_allowed": False,
                },
                "progress": {
                    "stage": "prepared",
                    "percent": 0,
                    "message_zh": "教师已修复模型返回稿，可在本地生成备课产物。",
                },
                "error": None,
                "created_at": now,
                "updated_at": now,
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
                "source_kind": "teacher_revision",
                "revision_kind": "returned_candidate_repair",
                "model_invoked": False,
                "parent_task_id": task_id,
                "parent_candidate_sha256": source_revision,
                "revision_note": note.strip(),
            }
            _atomic_write_json(seed_path, normalized)
            self._write_task(task)
            return self._public_task(task)

    def create_revision(
        self,
        task_id: str,
        source_revision: str,
        candidate: Mapping[str, Any],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Create a prepared, local-only child task from a frozen candidate.

        The source task is never changed.  The child receives its own frozen
        seed so a later :meth:`run` or retry can render locally with
        ``provider=None`` and cannot fall back to a model call.
        """

        if (
            not isinstance(source_revision, str)
            or _SHA256.fullmatch(source_revision) is None
        ):
            raise DesktopPreparationError(
                "preparation_revision_stale", "修订来源版本不正确，请重新打开候选。"
            )
        if not isinstance(note, str) or len(note) > 2000:
            raise DesktopPreparationError(
                "preparation_revision_note_invalid", "修订说明请控制在2000字以内。"
            )

        with self._lock:
            source = self.revision_source(task_id)
            if source["source_revision"] != source_revision:
                raise DesktopPreparationError(
                    "preparation_revision_stale",
                    "备课候选已发生变化，请重新打开最新版本。",
                )
            normalized = _validate_canonical_candidate(candidate, source["payload"])
            if normalized == source["candidate"]:
                raise DesktopPreparationError(
                    "preparation_revision_unchanged",
                    "内容没有变化，无需另存修订版。",
                )

            now = _utc_now()
            child_id = "PREP-" + uuid.uuid4().hex
            child_task_path = self._task_path(child_id)
            if child_task_path.exists() or child_task_path.is_symlink():
                raise DesktopPreparationError(
                    "preparation_revision_collision",
                    "本地修订任务标识发生冲突，请重新提交。",
                    True,
                )
            seed_path = self._seed_path(child_id)
            payload = deepcopy(source["payload"])
            parent_digest = source["source_revision"]
            task: dict[str, Any] = {
                "schema_version": PREPARATION_TASK_SCHEMA_VERSION,
                "task_id": child_id,
                "task_identity_sha256": _sha256_json(
                    {
                        "payload": payload,
                        "source_kind": "teacher_revision",
                        "parent_task_id": task_id,
                        "parent_candidate_sha256": parent_digest,
                        "candidate_id": normalized["candidate_id"],
                    }
                ),
                "request_sha256": _sha256_json(payload),
                "status": "prepared",
                "attempt": 0,
                "topic": payload["topic"],
                "artifact_mode": payload["artifact_mode"],
                "profile_id": None,
                "profile_revision": None,
                "payload": payload,
                "candidate_seed_file": seed_path.name,
                "candidate_seed_sha256": _sha256_json(normalized),
                "candidate_id": normalized["candidate_id"],
                "attempt_relpath": None,
                "artifacts": [],
                "slide_count": 0,
                "quality": {
                    "status": "not_run",
                    "candidate_only": True,
                    "teacher_review_required": True,
                    "publication_allowed": False,
                    "official_claim_allowed": False,
                },
                "progress": {
                    "stage": "prepared",
                    "percent": 0,
                    "message_zh": "教师本地修订已保存，可重新导出备课产物。",
                },
                "error": None,
                "created_at": now,
                "updated_at": now,
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
                "source_kind": "teacher_revision",
                "model_invoked": False,
                "parent_task_id": task_id,
                "parent_candidate_sha256": parent_digest,
                "revision_note": note.strip(),
            }
            _atomic_write_json(seed_path, normalized)
            self._write_task(task)
            return self._public_task(task)

    def _cancelled(
        self, task_id: str, attempt: int, external: Callable[[], bool]
    ) -> bool:
        try:
            externally_cancelled = bool(external())
        except Exception as exc:
            raise DesktopPreparationError(
                "preparation_cancel_callback_failed",
                "备课取消状态无法读取，可显式重试。",
                True,
            ) from exc
        if externally_cancelled:
            return True
        with self._lock:
            task = self._read_task(task_id)
            return (
                task.get("attempt") != attempt
                or task.get("status") == "cancel_requested"
            )

    @staticmethod
    def _notify(
        callback: Callable[[Mapping[str, Any]], None], value: Mapping[str, Any]
    ) -> None:
        try:
            callback(deepcopy(dict(value)))
        except Exception:  # noqa: BLE001 - progress observers cannot fail the task
            return

    def _set_progress(
        self,
        task_id: str,
        attempt: int,
        *,
        stage: str,
        percent: int,
        message_zh: str,
        callback: Callable[[Mapping[str, Any]], None],
    ) -> None:
        progress = {
            "stage": stage,
            "percent": max(0, min(100, int(percent))),
            "message_zh": message_zh,
        }
        with self._lock:
            task = self._read_task(task_id)
            if task.get("attempt") != attempt or task.get("status") != "running":
                if task.get("status") == "cancel_requested":
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    )
                raise DesktopPreparationError(
                    "preparation_state_conflict", "备课任务状态已变化。", True
                )
            task["progress"] = progress
            task["updated_at"] = _utc_now()
            self._write_task(task)
        self._notify(callback, progress)

    def _load_seed(self, task_id: str, attempt: int) -> dict[str, Any] | None:
        with self._lock:
            task = self._read_task(task_id)
            if task.get("attempt") != attempt or task.get("status") != "running":
                if task.get("status") == "cancel_requested":
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    )
                raise DesktopPreparationError(
                    "preparation_state_conflict", "备课任务状态已变化。", True
                )
            if task.get("candidate_seed_file") is None:
                if self._adopt_seed_if_present(task):
                    self._write_task(task)
                else:
                    return None
            payload = deepcopy(task["payload"])
            expected_sha = task.get("candidate_seed_sha256")
        path = self._seed_path(task_id)
        if not path.is_file() or path.is_symlink():
            raise DesktopPreparationError(
                "preparation_seed_missing", "冻结的备课候选已丢失。", True
            )
        raw = _strict_json_load(path)
        if not isinstance(raw, Mapping):
            raise DesktopPreparationError(
                "preparation_seed_invalid", "冻结的备课候选无法读取。", True
            )
        value = _validate_canonical_candidate(raw, payload)
        if _sha256_json(value) != expected_sha:
            raise DesktopPreparationError(
                "preparation_seed_drift", "冻结的备课候选摘要已变化。", True
            )
        return value

    def _freeze_seed(
        self, task_id: str, attempt: int, candidate: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            task = self._read_task(task_id)
            if task.get("attempt") != attempt or task.get("status") != "running":
                if task.get("status") == "cancel_requested":
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    )
                raise DesktopPreparationError(
                    "preparation_state_conflict", "备课任务状态已变化。", True
                )
            value = _validate_canonical_candidate(candidate, task["payload"])
            path = self._seed_path(task_id)
            if path.is_file():
                existing = _strict_json_load(path)
                if _sha256_json(existing) != _sha256_json(value):
                    raise DesktopPreparationError(
                        "preparation_seed_collision", "冻结候选文件已存在且内容不同。"
                    )
            else:
                _atomic_write_json(path, value)
            task["candidate_seed_file"] = path.name
            task["candidate_seed_sha256"] = _sha256_json(value)
            task["candidate_id"] = value["candidate_id"]
            task["updated_at"] = _utc_now()
            self._write_task(task)
            return value

    def _attempt_root(self, task_id: str, attempt: int) -> tuple[Path, str]:
        relative = f"{task_id}/attempt-{attempt:04d}"
        path = _contained(self.artifacts_root, self.artifacts_root / relative)
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise DesktopPreparationError(
                "preparation_attempt_exists",
                "新的备课尝试目录已存在，已停止覆盖。",
                True,
            ) from exc
        return path, relative

    def _materialize_artifacts(
        self,
        rendered: Mapping[str, Any],
        *,
        attempt_root: Path,
        candidate: Mapping[str, Any],
        output_kind: str,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        if not isinstance(rendered, Mapping):
            raise DesktopPreparationError(
                "preparation_renderer_result_invalid",
                "本机渲染器没有返回结构化结果。",
                True,
            )
        _reject_sensitive(rendered)
        rows = rendered.get("artifacts")
        slide_count = rendered.get("slide_count")
        quality_raw = rendered.get("quality")
        if (
            not isinstance(rows, list)
            or not rows
            or type(slide_count) is not int
            or slide_count < 0
            or not isinstance(quality_raw, Mapping)
        ):
            raise DesktopPreparationError(
                "preparation_renderer_result_invalid",
                "本机渲染器返回的产物、页数或质量回执不完整。",
                True,
            )
        if output_kind in {"ppt", "linked_bundle"} and slide_count != len(
            candidate["slides"]
        ):
            raise DesktopPreparationError(
                "preparation_slide_count_mismatch",
                "PPT 实际页数与冻结候选不一致。",
                True,
            )
        if output_kind == "lesson_plan" and slide_count not in {
            0,
            len(candidate["slides"]),
        }:
            raise DesktopPreparationError(
                "preparation_slide_count_mismatch",
                "教案渲染回执中的 PPT 页数不正确。",
                True,
            )
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for index, raw_row in enumerate(rows, 1):
            if not isinstance(raw_row, Mapping):
                raise DesktopPreparationError(
                    "preparation_artifact_invalid", "渲染产物记录格式不正确。", True
                )
            artifact_id = raw_row.get("artifact_id")
            filename = raw_row.get("filename")
            content_type = raw_row.get("content_type")
            if (
                not isinstance(artifact_id, str)
                or _ARTIFACT_ID.fullmatch(artifact_id) is None
                or artifact_id in seen_ids
                or not isinstance(content_type, str)
                or not content_type.strip()
                or len(content_type) > 200
            ):
                raise DesktopPreparationError(
                    "preparation_artifact_invalid", "渲染产物标识或类型不正确。", True
                )
            relative = _relative_path(filename)
            target = _contained(attempt_root, attempt_root / relative)
            raw_data = raw_row.get("data", raw_row.get("bytes"))
            raw_path = raw_row.get("path")
            if raw_data is not None and raw_path is not None:
                raise DesktopPreparationError(
                    "preparation_artifact_invalid",
                    "渲染产物不能同时返回路径与字节。",
                    True,
                )
            if raw_data is not None:
                if not isinstance(raw_data, (bytes, bytearray)) or not raw_data:
                    raise DesktopPreparationError(
                        "preparation_artifact_invalid", "渲染产物字节为空。", True
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise DesktopPreparationError(
                        "preparation_artifact_overwrite_forbidden",
                        "渲染器试图覆盖同一尝试中的产物。",
                        True,
                    )
                try:
                    target.write_bytes(bytes(raw_data))
                except OSError as exc:
                    raise DesktopPreparationError(
                        "preparation_artifact_write_failed", "渲染产物无法保存。", True
                    ) from exc
                path = target
            else:
                if raw_path is None:
                    path = target
                else:
                    try:
                        supplied = Path(raw_path).expanduser()
                    except (TypeError, ValueError) as exc:
                        raise DesktopPreparationError(
                            "preparation_artifact_invalid", "渲染产物路径不正确。", True
                        ) from exc
                    if not supplied.is_absolute():
                        supplied = attempt_root / supplied
                    if supplied.is_symlink():
                        raise DesktopPreparationError(
                            "preparation_artifact_symlink_forbidden",
                            "渲染产物不能通过符号链接提供。",
                            True,
                        )
                    path = _contained(attempt_root, supplied)
                if path.name != relative.name:
                    raise DesktopPreparationError(
                        "preparation_artifact_invalid",
                        "渲染产物文件名绑定不一致。",
                        True,
                    )
            if path.is_symlink() or not path.is_file():
                raise DesktopPreparationError(
                    "preparation_artifact_missing", "渲染产物文件不存在。", True
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise DesktopPreparationError(
                    "preparation_artifact_missing", "渲染产物文件无法读取。", True
                ) from exc
            if not data or len(data) > _MAX_ARTIFACT_BYTES:
                raise DesktopPreparationError(
                    "preparation_artifact_invalid", "渲染产物为空或超过大小限制。", True
                )
            relative_actual = path.relative_to(attempt_root).as_posix()
            if relative_actual in seen_paths:
                raise DesktopPreparationError(
                    "preparation_artifact_invalid", "渲染产物路径重复。", True
                )
            seen_ids.add(artifact_id)
            seen_paths.add(relative_actual)
            records.append(
                {
                    "artifact_id": artifact_id,
                    "filename": path.name,
                    "relative_path": relative_actual,
                    "content_type": content_type.strip(),
                    "size_bytes": len(data),
                    "sha256": _sha256_bytes(data),
                }
            )
        quality = _json_clone(
            quality_raw,
            code="preparation_quality_invalid",
            message_zh="渲染质量回执不是严格 JSON。",
        )
        for key, expected in (
            ("candidate_only", True),
            ("teacher_review_required", True),
            ("publication_allowed", False),
            ("official_claim_allowed", False),
        ):
            if key in quality and quality[key] is not expected:
                raise DesktopPreparationError(
                    "preparation_quality_boundary_invalid",
                    "渲染质量回执越过了教师复核或发布边界。",
                    True,
                )
            quality[key] = expected
        quality.setdefault("status", "candidate_ready_for_teacher_review")
        return records, slide_count, quality

    def _finish_failure(
        self,
        task_id: str,
        attempt: int,
        error: DesktopPreparationError,
    ) -> dict[str, Any]:
        with self._lock:
            task = self._read_task(task_id)
            if task.get("attempt") != attempt:
                return self._public_task(task)
            cancelled = (
                error.code == "preparation_cancelled"
                or task.get("status") == "cancel_requested"
            )
            status = "cancelled" if cancelled else "failed"
            message = (
                "备课任务已取消，未登记任何部分产物。"
                if cancelled
                else error.message_zh
            )
            task["status"] = status
            task["updated_at"] = _utc_now()
            task["artifacts"] = []
            task["slide_count"] = 0
            task["quality"] = {
                "status": "not_run" if cancelled else "failed",
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
            }
            task["progress"] = {
                "stage": status,
                "percent": 100,
                "message_zh": message,
            }
            task["error"] = {
                "code": "preparation_cancelled" if cancelled else error.code,
                "message_zh": message,
                "retryable": True if cancelled else error.retryable,
            }
            self._write_task(task)
            return self._public_task(task)

    def run(
        self,
        task_id: str,
        provider: Any,
        report_progress: Callable[[Mapping[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Synchronously run one prepared attempt in the calling worker."""

        callback = report_progress or (lambda _value: None)
        external_cancelled = is_cancelled or (lambda: False)
        if not callable(callback) or not callable(external_cancelled):
            raise DesktopPreparationError(
                "preparation_callback_invalid", "备课进度或取消回调不可用。"
            )
        with self._lock:
            task = self._read_task(task_id)
            if task["status"] == "completed":
                return self._public_task(task)
            if task["status"] == "running":
                raise DesktopPreparationError(
                    "preparation_task_busy", "这个备课任务正在生成。"
                )
            if task["status"] == "cancel_requested":
                return self._finish_failure(
                    task_id,
                    int(task["attempt"]),
                    DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    ),
                )
            if task["status"] in {"failed", "cancelled"}:
                raise DesktopPreparationError(
                    "preparation_retry_required", "请先显式重试这个备课任务。", True
                )
            if task["status"] != "prepared":
                raise DesktopPreparationError(
                    "preparation_task_blocked", "这个备课任务当前不能生成。"
                )
            attempt = int(task["attempt"]) + 1
            attempt_root, attempt_relpath = self._attempt_root(task_id, attempt)
            task["attempt"] = attempt
            task["attempt_relpath"] = attempt_relpath
            task["status"] = "running"
            task["updated_at"] = _utc_now()
            task["artifacts"] = []
            task["slide_count"] = 0
            task["error"] = None
            task["quality"] = {
                "status": "running",
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
            }
            task["progress"] = {
                "stage": "loading_candidate",
                "percent": 5,
                "message_zh": "正在核对冻结请求与备课候选。",
            }
            self._write_task(task)

        def cancelled() -> bool:
            return self._cancelled(task_id, attempt, external_cancelled)

        try:
            self._notify(callback, task["progress"])
            try:
                image_data = {
                    asset["asset_id"]: self.image_store.load(asset)
                    for asset in task["payload"].get("image_assets", [])
                }
            except PreparationImageError as exc:
                raise DesktopPreparationError(exc.code, exc.message_zh, True) from exc
            if cancelled():
                raise DesktopPreparationError(
                    "preparation_cancelled", "备课任务已取消。", True
                )
            seed = self._load_seed(task_id, attempt)
            if seed is None:
                if provider is None:
                    raise DesktopPreparationError(
                        "preparation_seed_missing",
                        "本地修订候选已丢失，未调用模型；请重新打开原始候选后修订。",
                        True,
                    )
                self._set_progress(
                    task_id,
                    attempt,
                    stage="requesting_candidate",
                    percent=18,
                    message_zh="正在请求结构化备课候选。",
                    callback=callback,
                )
                try:
                    raw_candidate = _invoke_provider(
                        provider,
                        task["payload"],
                        {
                            "profile_id": task["profile_id"],
                            "profile_revision": task["profile_revision"],
                        },
                        callback,
                        cancelled,
                        image_data=image_data,
                    )
                except DesktopPreparationError:
                    raise
                except Exception as exc:
                    raise DesktopPreparationError(
                        "preparation_provider_failed",
                        "备课模型未返回可用结果，可显式重试。",
                        True,
                    ) from exc
                if cancelled():
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    )
                try:
                    seed = normalize_preparation_candidate(
                        raw_candidate, task["payload"]
                    )
                except DesktopPreparationError:
                    # Preserve a safe copy for an explicit teacher repair. It
                    # remains non-canonical and is never used by retry/run
                    # unless the repair endpoint first normalizes it into a
                    # validated local child seed.
                    self._persist_returned_candidate(
                        task_id, attempt, raw_candidate
                    )
                    raise
                seed = self._freeze_seed(task_id, attempt, seed)
            self._set_progress(
                task_id,
                attempt,
                stage="rendering",
                percent=48,
                message_zh="结构化候选已冻结，正在生成本地备课产物。",
                callback=callback,
            )
            if cancelled():
                raise DesktopPreparationError(
                    "preparation_cancelled", "备课任务已取消。", True
                )
            try:
                rendered = _invoke_renderer(
                    self.renderer,
                    seed,
                    output_kind=str(task["payload"]["artifact_mode"]),
                    output_dir=attempt_root,
                    report_progress=callback,
                    is_cancelled=cancelled,
                    image_data=image_data,
                )
            except DesktopPreparationError:
                raise
            except Exception as exc:
                raise DesktopPreparationError(
                    "preparation_renderer_failed",
                    "本机备课渲染失败；候选已冻结，可显式重试。",
                    True,
                ) from exc
            if cancelled():
                raise DesktopPreparationError(
                    "preparation_cancelled", "备课任务已取消。", True
                )
            records, slide_count, quality = self._materialize_artifacts(
                rendered,
                attempt_root=attempt_root,
                candidate=seed,
                output_kind=str(task["payload"]["artifact_mode"]),
            )
            with self._lock:
                current = self._read_task(task_id)
                if (
                    current.get("attempt") != attempt
                    or current.get("status") != "running"
                    or cancelled()
                ):
                    raise DesktopPreparationError(
                        "preparation_cancelled", "备课任务已取消。", True
                    )
                current["status"] = "completed"
                current["updated_at"] = _utc_now()
                current["artifacts"] = records
                current["slide_count"] = slide_count
                current["quality"] = quality
                current["error"] = None
                current["progress"] = {
                    "stage": "teacher_review_pending",
                    "percent": 100,
                    "message_zh": "个人备课候选已生成，使用前仍需教师复核。",
                }
                self._write_task(current)
                result = self._public_task(current)
            self._notify(callback, result["progress"])
            return result
        except DesktopPreparationError as exc:
            return self._finish_failure(task_id, attempt, exc)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_task(self._read_task(task_id))

    def egress_snapshot(self, task_id: str) -> dict[str, Any]:
        """Read the frozen image scope, never the editable form or secrets."""
        with self._lock:
            task = self._read_task(task_id)
            payload = _normalized_payload(task["payload"])
            assets = deepcopy(payload.get("image_assets", []))
            for asset in assets:
                self.image_store.load(asset)
            return {
                "task_id": task_id,
                "request_sha256": task["request_sha256"],
                "profile_id": task["profile_id"],
                "profile_revision": task["profile_revision"],
                "image_input_mode": image_input_mode(payload),
                "image_assets": assets,
                "local_only_operation": (
                    task.get("candidate_seed_file") is not None
                    or task.get("source_kind") == "teacher_revision"
                ),
            }

    def record_egress_confirmation(self, task_id: str) -> None:
        """Called only after the facade's explicit teacher-confirmed boundary."""
        with self._lock:
            snapshot = self.egress_snapshot(task_id)
            task = self._read_task(task_id)
            mode = snapshot["image_input_mode"]
            sending = mode == "vision" and not snapshot["local_only_operation"]
            task["last_egress_confirmation"] = {
                "confirmed_at": _utc_now(),
                "request_sha256": snapshot["request_sha256"],
                "profile_id": snapshot["profile_id"],
                "profile_revision": snapshot["profile_revision"],
                "image_input_mode": mode,
                "local_only_operation": snapshot["local_only_operation"],
                "images": [
                    {"asset_id": asset["asset_id"], "sha256": asset["sha256"]}
                    for asset in snapshot["image_assets"]
                ] if sending else [],
            }
            self._write_task(task)

    def list_tasks(self, limit: int = 50) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise DesktopPreparationError(
                "preparation_limit_invalid", "备课任务列表数量不正确。"
            )
        values: list[dict[str, Any]] = []
        with self._lock:
            for path in self.tasks_root.glob("PREP-*.json"):
                if path.is_symlink() or _TASK_ID.fullmatch(path.stem) is None:
                    continue
                values.append(self._read_task(path.stem))
        values.sort(
            key=lambda item: (str(item.get("updated_at") or ""), item["task_id"]),
            reverse=True,
        )
        return tuple(self._public_task(item) for item in values[:limit])

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._read_task(task_id)
            if task["status"] in {"cancel_requested", "cancelled"}:
                return self._public_task(task)
            if task["status"] == "prepared":
                task["status"] = "cancelled"
                message = "备课任务已取消，未登记任何产物。"
                task["error"] = {
                    "code": "preparation_cancelled",
                    "message_zh": message,
                    "retryable": True,
                }
                task["progress"] = {
                    "stage": "cancelled",
                    "percent": 100,
                    "message_zh": message,
                }
            elif task["status"] == "running":
                task["status"] = "cancel_requested"
                message = "已请求取消，当前安全阶段结束后停止。"
                task["progress"] = {
                    "stage": "cancel_requested",
                    "percent": int(task.get("progress", {}).get("percent", 0)),
                    "message_zh": message,
                }
            else:
                raise DesktopPreparationError(
                    "preparation_task_not_cancellable", "这个备课任务当前不能取消。"
                )
            task["updated_at"] = _utc_now()
            self._write_task(task)
            return self._public_task(task)

    def retry(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._read_task(task_id)
            if task["status"] not in {"failed", "cancelled"}:
                raise DesktopPreparationError(
                    "preparation_task_not_retryable", "这个备课任务当前不能重试。"
                )
            error = task.get("error")
            if (
                task["status"] == "failed"
                and isinstance(error, Mapping)
                and error.get("retryable") is not True
            ):
                raise DesktopPreparationError(
                    "preparation_task_not_retryable", "这个备课失败不能直接重试。"
                )
            task["status"] = "prepared"
            task["updated_at"] = _utc_now()
            task["artifacts"] = []
            task["slide_count"] = 0
            task["quality"] = {
                "status": "not_run",
                "candidate_only": True,
                "teacher_review_required": True,
                "publication_allowed": False,
                "official_claim_allowed": False,
            }
            task["error"] = None
            task["progress"] = {
                "stage": "prepared",
                "percent": 0,
                "message_zh": "已准备显式重试；不会覆盖上一次尝试。",
            }
            self._write_task(task)
            return self._public_task(task)

    def _verified_artifact_path(
        self, task: Mapping[str, Any], artifact_id: str
    ) -> tuple[Path, str, bytes]:
        if task["status"] != "completed":
            raise DesktopPreparationError(
                "preparation_artifact_not_ready", "备课产物尚未生成完成。"
            )
        record = next(
            (
                row
                for row in task["artifacts"]
                if isinstance(row, Mapping) and row.get("artifact_id") == artifact_id
            ),
            None,
        )
        if not isinstance(record, Mapping):
            raise DesktopPreparationError(
                "preparation_artifact_missing", "找不到这个备课产物。"
            )
        attempt_relpath = task.get("attempt_relpath")
        if not isinstance(attempt_relpath, str):
            raise DesktopPreparationError(
                "preparation_state_corrupt", "备课产物目录绑定不正确。"
            )
        attempt_root = _contained(
            self.artifacts_root, self.artifacts_root / attempt_relpath
        )
        relative = _relative_path(record.get("relative_path"))
        path = _contained(attempt_root, attempt_root / relative)
        if path.is_symlink() or not path.is_file():
            raise DesktopPreparationError(
                "preparation_artifact_missing", "备课产物文件不存在。"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DesktopPreparationError(
                "preparation_artifact_missing", "备课产物文件无法读取。", True
            ) from exc
        if len(data) != record.get("size_bytes") or _sha256_bytes(data) != record.get(
            "sha256"
        ):
            raise DesktopPreparationError(
                "preparation_artifact_drift", "备课产物已变化，请重新生成。", True
            )
        return path, str(record.get("content_type")), data

    def artifact_path(self, task_id: str, artifact_id: str) -> tuple[Path, str]:
        if (
            not isinstance(artifact_id, str)
            or _ARTIFACT_ID.fullmatch(artifact_id) is None
        ):
            raise DesktopPreparationError(
                "preparation_artifact_invalid", "备课产物标识不正确。"
            )
        with self._lock:
            task = self._read_task(task_id)
            path, content_type, _data = self._verified_artifact_path(task, artifact_id)
            return path, content_type


__all__ = [
    "PREPARATION_CANDIDATE_SCHEMA_VERSION",
    "PREPARATION_CANONICAL_SCHEMA_VERSION",
    "PREPARATION_REQUEST_SCHEMA_VERSION",
    "PREPARATION_TASK_SCHEMA_VERSION",
    "DesktopPreparationError",
    "DesktopPreparationManager",
    "normalize_preparation_candidate",
    "normalize_preparation_payload",
    "preparation_candidate_schema",
]
