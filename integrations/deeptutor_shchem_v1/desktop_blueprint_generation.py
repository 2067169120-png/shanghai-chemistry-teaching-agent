"""Generate a personal theme blueprint from the exact local prompt preview.

This is the planning stage, not an exam or a claim of verified chemistry.
Uses the same revision-bound transport as preparation; never owns credentials.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from .desktop_chemistry_prompt_rules import CHEMISTRY_CONSISTENCY_RULES
from .desktop_preparation_provider import _CancellationView
from .intake_imports import PinnedVisualTransport
from .visual_provider_runtime import (
    build_structured_text_request,
    parse_structured_visual_response,
    strict_json_loads,
)


class BlueprintGenerationError(RuntimeError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code, self.message_zh = code, message_zh
        self.response_summary: dict[str, Any] = {}


def _response_summary(response: Any) -> dict[str, Any]:
    """Only status/usage metadata, never response content or error messages."""
    if response is None:
        return {}
    summary: dict[str, Any] = {
        "http_status": int(response.http_status),
        "model_invoked": response.model_invoked is True,
    }
    try:
        value = strict_json_loads(response.body)
        if not isinstance(value, Mapping):
            return summary
        status = value.get("status")
        summary["status"] = (
            status
            if isinstance(status, str)
            and status
            in {
                "completed",
                "incomplete",
                "failed",
                "queued",
                "in_progress",
                "cancelled",
            }
            else "missing_or_unknown"
        )
        details = value.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, Mapping) else None
        summary["incomplete_reason"] = (
            reason
            if isinstance(reason, str)
            and reason in {"max_output_tokens", "content_filter"}
            else "missing_or_unknown"
        )
        summary["error_present"] = value.get("error") is not None
        summary["incomplete_details_present"] = details is not None
        usage = value.get("usage")
        if isinstance(usage, Mapping):
            summary["usage"] = {
                k: usage[k]
                for k in ("input_tokens", "output_tokens", "total_tokens")
                if type(usage.get(k)) is int and 0 <= usage[k] <= 10_000_000
            }
    except (ValueError, TypeError, KeyError):
        pass
    return summary


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def blueprint_schema() -> dict[str, Any]:
    text = {"type": "string"}
    texts = {"type": "array", "items": text}
    material = _object({"material_id": text, "purpose": text, "evidence_refs": texts})
    atomic = _object(
        {
            "printed_question_id": text,
            "atomic_part_id": text,
            "task_plan": text,
            "response_form": text,
            "material_refs": texts,
            "depends_on": texts,
            "answer_outline": text,
            "knowledge_evidence": text,
            "difficulty_evidence": text,
        }
    )
    return _object(
        {
            "theme_center": text,
            "shared_material_plan": {"type": "array", "items": material},
            "question_chain": {"type": "array", "items": atomic},
            "unknowns": texts,
        }
    )


def blueprint_prompt(preview: Mapping[str, Any]) -> str:
    if preview.get("eligibility") != "blueprint_only":
        raise BlueprintGenerationError(
            "blueprint_mode_invalid", "当前提示不属于命题蓝图范围。"
        )
    for key in ("system_prompt", "task_prompt"):
        if not isinstance(preview.get(key), str) or not preview[key].strip():
            raise BlueprintGenerationError(
                "blueprint_prompt_missing", "请先重新编译本地提示。"
            )
    evidence = preview.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise BlueprintGenerationError(
            "blueprint_evidence_missing", "本地资料摘要尚未准备好。"
        )
    indexed = [
        {
            "evidence_id": f"E{index}",
            "scope": item.get("scope", ""),
            "supports": item.get("supports", []),
            "source_type": item.get("source_type", "unknown"),
        }
        for index, item in enumerate(evidence, 1)
    ]
    return (
        preview["system_prompt"]
        + "\n\n"
        + preview["task_prompt"]
        + "\n\n本次只生成一个主题命题蓝图，不生成整卷或声称完整题目已可使用。"
        "严格按给定 JSON Schema 返回。theme_center 是共同语境；"
        "shared_material_plan 规划共同材料；question_chain 按印刷小题和最小作答单元的卷内顺序排列。"
        "同一 printed_question_id 必须连续，atomic_part_id 唯一，depends_on 只能引用前面的作答单元。"
        "material_refs 只能引用本结果的 material_id，evidence_refs 只能引用下面的 E 编号。"
        "作答形态混合嵌在主题中，不另设独立选择题板块。规划 3—12 个作答单元，"
        "task_plan 写任务设计而非冒充来源原题；answer_outline 写解答思路和需核验条件，"
        "不把它称为标准答案。知识和难度说明依据，不猜 K/A/C 编码，缺失写 unknown。"
        "不得发明教材页码、实验数据、官方采分点或审核结论；缺口列入 unknowns。"
        "不要复述这些指令，所有字段用教师可读的简洁中文。每个作答单元的任务与解答思路各用1—3句话，"
        "不要重复整段教材或原题正文，把篇幅用于说明考查关系、必要条件与改写方案。\n"
        "提交前逐项核对材料、任务和解答规划是否一致：材料中已给的体积、状态或条件，"
        "不能在后题声称缺失；仪器选择必须适合物质的物态和实际操作，不能把气体当固体直接称取。"
        "每个操作都要交代必要前提；无法确定时明确留待核验，不把缺前提的操作写成答案。\n"
        "如需写方程式，同时给出物质名称或文字标签，保留条件、物态、电荷和单位。\n"
        + CHEMISTRY_CONSISTENCY_RULES
        + "\n可引用的本地摘要编号：\n"
        + json.dumps(indexed, ensure_ascii=False)
    )


def validate_blueprint(value: Any, evidence_count: int) -> dict[str, Any]:
    errors = list(Draft202012Validator(blueprint_schema()).iter_errors(value))
    if errors:
        raise BlueprintGenerationError(
            "blueprint_output_invalid", "模型返回的蓝图字段不完整，未保存为成功结果。"
        )
    materials, chain = value["shared_material_plan"], value["question_chain"]
    if not 1 <= len(materials) <= 12 or not 1 <= len(chain) <= 30:
        raise BlueprintGenerationError(
            "blueprint_size_invalid", "蓝图缺少共同材料或小题，或超出单主题范围。"
        )
    allowed_evidence = {f"E{i}" for i in range(1, evidence_count + 1)}
    material_ids: set[str] = set()
    for item in materials:
        identity = item["material_id"].strip()
        if (
            not identity
            or identity in material_ids
            or not set(item["evidence_refs"]) <= allowed_evidence
        ):
            raise BlueprintGenerationError(
                "blueprint_material_invalid", "共同材料的编号或资料引用无效。"
            )
        material_ids.add(identity)
    seen: set[str] = set()
    printed: set[str] = set()
    previous = None
    for item in chain:
        identity, parent = (
            item["atomic_part_id"].strip(),
            item["printed_question_id"].strip(),
        )
        if (
            not identity
            or not parent
            or identity in seen
            or not set(item["depends_on"]) <= seen
            or not set(item["material_refs"]) <= material_ids
            or (parent != previous and parent in printed)
        ):
            raise BlueprintGenerationError(
                "blueprint_chain_invalid", "蓝图小题编号、共同材料或前序依赖不闭合。"
            )
        if not item["task_plan"].strip() or not item["response_form"].strip():
            raise BlueprintGenerationError(
                "blueprint_task_missing", "蓝图中存在未说明任务或作答方式的小题。"
            )
        seen.add(identity)
        printed.add(parent)
        previous = parent
    return deepcopy(value)


def generate_blueprint(
    context: Any,
    preview: Mapping[str, Any],
    *,
    transport: Any = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return run_blueprint_request(
        context,
        prompt=blueprint_prompt(preview),
        schema=blueprint_schema(),
        schema_name="shchem_theme_blueprint_v1",
        validator=lambda value: validate_blueprint(value, len(preview["evidence"])),
        transport=transport,
        should_cancel=should_cancel,
    )


def run_blueprint_request(
    context: Any,
    *,
    prompt: str,
    schema: Mapping[str, Any],
    schema_name: str,
    validator: Callable[[Any], dict[str, Any]],
    transport: Any = None,
    should_cancel: Callable[[], bool] | None = None,
    max_output_tokens: int = 24000,
) -> dict[str, Any]:
    """Shared one-shot text transport; caller supplies the result contract."""
    cancelled = should_cancel or (lambda: False)
    if cancelled():
        raise BlueprintGenerationError("blueprint_cancelled", "已停止生成蓝图。")
    started = time.monotonic()
    response = None
    try:
        request = build_structured_text_request(
            context,
            prompt=prompt,
            schema=schema,
            schema_name=schema_name,
            max_output_tokens=max_output_tokens,
        )
        response = (transport or PinnedVisualTransport(total_timeout_seconds=300)).send(
            request,
            cancel_event=_CancellationView(cancelled),
            deadline_monotonic=started + 300,
        )
        if cancelled():
            raise BlueprintGenerationError("blueprint_cancelled", "已停止生成蓝图。")
        if (
            response.model_invoked is not True
            or not 200 <= int(response.http_status) < 300
        ):
            raise BlueprintGenerationError(
                "blueprint_provider_failed", "模型没有完成这次蓝图生成。"
            )
        value, usage = parse_structured_visual_response(
            request.api_style, response.body
        )
        result = validator(value)
    except BlueprintGenerationError as exc:
        if not exc.response_summary:
            exc.response_summary = _response_summary(response)
        raise
    except Exception as exc:
        if cancelled():
            raise BlueprintGenerationError(
                "blueprint_cancelled", "已停止生成蓝图。"
            ) from exc
        raw_code = getattr(exc, "code", "blueprint_provider_failed")
        code = (
            raw_code
            if isinstance(raw_code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", raw_code)
            else "blueprint_provider_failed"
        )
        messages = {
            "timeout": "模型生成超过等待时限，未自动重试；可能已产生费用。",
            "dns_failure": "模型域名解析失败，请检查网络后再试。",
            "invalid_credentials": "模型服务拒绝密钥，请到设置检查。",
            "rate_limited": "模型服务限流，请稍后手动重试。",
            "provider_response_incomplete": "模型响应未完整结束，请检查输出额度或稍后重试。",
            "provider_output_invalid": "模型返回内容不是完整 JSON，未保存为成功蓝图。",
        }
        error = BlueprintGenerationError(
            code, messages.get(code, "蓝图生成未完成，请检查模型连接或稍后重试。")
        )
        error.response_summary = _response_summary(response)
        if (
            code == "provider_response_incomplete"
            and error.response_summary.get("incomplete_reason") == "max_output_tokens"
        ):
            error.message_zh = (
                "模型已连接，但本次生成用尽输出额度，响应不完整。"
                "请缩小本次目标或参考选题后手动重试；未自动重试，可能已产生费用。"
            )
            error.args = (error.message_zh,)
        raise error from exc
    return {
        "candidate": result,
        "usage": usage,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "model_invoked": True,
        "result_kind": "blueprint_only",
        "teacher_review_required": True,
        "official": False,
        "bank_ingest_allowed": False,
        "publication_allowed": False,
    }


def format_blueprint(result: Mapping[str, Any]) -> str:
    candidate = result["candidate"]
    lines = [
        "主题命题蓝图（待教师完善，不是完整试卷）",
        "",
        candidate["theme_center"],
        "",
        "共同材料",
    ]
    for item in candidate["shared_material_plan"]:
        lines.append(
            f"{item['material_id']}　{item['purpose']}（资料：{'、'.join(item['evidence_refs']) or '待补'}）"
        )
    lines.extend(["", "主题内小题推进"])
    for item in candidate["question_chain"]:
        lines.extend(
            [
                "",
                f"{item['printed_question_id']} / {item['atomic_part_id']} · {item['response_form']}",
                item["task_plan"],
                "解答规划：" + item["answer_outline"],
                "知识依据：" + item["knowledge_evidence"],
                "难度依据：" + item["difficulty_evidence"],
                "材料：" + ("、".join(item["material_refs"]) or "无"),
                "前序依赖：" + ("、".join(item["depends_on"]) or "无"),
            ]
        )
    lines.extend(["", "待补齐与核验", *candidate["unknowns"]])
    return "\n".join(lines)
