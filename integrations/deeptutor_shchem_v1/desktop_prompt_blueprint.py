"""Offline native adapter for the evidence-backed prompt compiler.

No provider, credential access, or question-bank writes belong to this adapter.
The compiler's source paths remain in local provenance, not the visible prompt.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .desktop_theme_structure_reference import (
    REFERENCE_KEY,
    compile_structure_reference,
)


class PromptPreviewError(ValueError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _text(payload: Mapping[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise PromptPreviewError(
            "prompt_input_invalid", "请填写课题和学习目标，并控制输入长度。"
        )
    return value.strip()


def build_request(
    payload: Mapping[str, Any], section_labels: Mapping[str, str]
) -> dict[str, Any]:
    allowed = {
        "title",
        "learning_goal",
        "section_keys",
        "grade",
        "context",
        "theme_reference",
        "handout_reference",
    }
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise PromptPreviewError("prompt_input_invalid", "命题提示输入格式不正确。")
    if payload.get("theme_reference", "none") not in ("none", REFERENCE_KEY):
        raise PromptPreviewError(
            "prompt_reference_invalid", "请选择列表中的试题结构参考。"
        )
    handout = payload.get("handout_reference")
    if handout is not None and (
        not isinstance(handout, dict)
        or set(handout) != {"reference_id", "revision", "include_answers"}
        or any(
            not isinstance(handout.get(key), str) or not handout[key]
            for key in ("reference_id", "revision")
        )
        or type(handout.get("include_answers")) is not bool
    ):
        raise PromptPreviewError("prompt_handout_invalid", "请选择已保存的讲义练习。")
    title = _text(payload, "title", 80)
    goal = _text(payload, "learning_goal", 500)
    keys = payload.get("section_keys")
    if (
        not isinstance(keys, list)
        or not 1 <= len(keys) <= 12
        or any(not isinstance(key, str) or key not in section_labels for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise PromptPreviewError(
            "prompt_section_invalid", "请选择当前教材目录中的 1—12 个章节。"
        )
    grade = payload.get("grade", 12)
    if type(grade) is not int or grade not in (10, 11, 12):
        raise PromptPreviewError("prompt_grade_invalid", "请选择高一、高二或高三。")
    context = payload.get("context", "")
    if not isinstance(context, str) or len(context) > 800:
        raise PromptPreviewError(
            "prompt_context_invalid", "情境简述请控制在 800 字以内。"
        )
    today = datetime.now(timezone(timedelta(hours=8))).date()
    return {
        "schema_version": "1.0.0",
        "request_id": "DESKTOP-PROMPT-" + uuid.uuid4().hex,
        "section_keys": list(keys),
        "target_year": today.year,
        "paper_kind": "theme_practice",
        "grade": grade,
        "month": today.month,
        "theme": {
            "title": title,
            "context": context.strip()
            or "围绕所选教材章节与教师学习目标设计主题情境。",
            "shared_material_plan": "依据章节证据规划共同材料，并说明各小题对材料或前序结论的依赖。",
        },
        "learning_targets": [
            {
                "target_id": "TEACHER-GOAL-1",
                "statement": goal,
                "primary_K": "unknown",
                "A": "unknown",
                "C": "unknown",
                "R": "unknown",
                "RP": ["unknown"],
                "priority": "must",
            }
        ],
        "teacher_constraints": {
            "must_include": ["按整卷、主题大题、印刷小题、最小作答单元组织"],
            "must_avoid": [
                "照搬来源题干或答案",
                "把来源标签猜作页码",
                "把教师目标当成已核验知识标签",
            ],
            "language_requirements": "使用规范简洁中文，保留化学表达中的条件、单位和文字标签。",
            "dependency_requirements": "明确共同材料依赖与前序结论依赖；未核验信息保留 unknown。",
        },
        "desired_output": {
            "artifact": "prompt_bundle",
            "include_answer_outline": True,
            "include_scoring_suggestions": True,
            "response_language": "zh-CN",
        },
        "visual_type": "none",
    }


def compile_preview(
    workspace: Path,
    payload: Mapping[str, Any],
    section_labels: Mapping[str, str],
    *,
    compiler: Callable[..., Mapping[str, Any]] | None = None,
    structure_loader: Callable[..., Mapping[str, Any]] | None = None,
    handout_loader: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    request = build_request(payload, section_labels)
    if compiler is None:
        script = workspace / (
            "sh-chem-db/05_命题热点素材/hotspot_theme_pipeline_v1_2026-08-28/"
            "prompt_distillation_workbench_v1/scripts/assemble_prompt_bundle.py"
        )
        if not script.is_file():
            raise PromptPreviewError(
                "prompt_compiler_unavailable", "本地教材提示编译器尚未安装完整。"
            )
        spec = importlib.util.spec_from_file_location(
            "_shchem_desktop_prompt_compiler", script
        )
        if spec is None or spec.loader is None:
            raise PromptPreviewError(
                "prompt_compiler_unavailable", "本地教材提示编译器无法加载。"
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        compiler = module.assemble_prompt_bundle
    try:
        bundle = compiler(request, max_digest_chars=100000)
    except Exception as exc:
        raise PromptPreviewError(
            "prompt_evidence_unavailable",
            "教材证据或提示规则未通过核验，请检查资料完整性及输入内容后重试。",
        ) from exc
    expected_gates = {
        "human_reviewed": False,
        "teaching_use_allowed": False,
        "bank_ingest_allowed": False,
        "official_status_allowed": False,
        "publication_allowed": False,
    }
    if (
        not isinstance(bundle, Mapping)
        or bundle.get("eligibility") != "blueprint_only"
        or bundle.get("gates") != expected_gates
        or not isinstance(bundle.get("system_prompt"), str)
        or not isinstance(bundle.get("task_prompt"), str)
    ):
        raise PromptPreviewError(
            "prompt_preview_invalid", "离线提示结果不符合预览范围，未调用模型。"
        )
    evidence = bundle.get("evidence_digest", [])
    if not isinstance(evidence, list) or not evidence:
        raise PromptPreviewError(
            "prompt_evidence_missing", "没有取得可用的教材证据摘要。"
        )
    result = {
        "title": request["theme"]["title"],
        "section_labels": [section_labels[key] for key in request["section_keys"]],
        "system_prompt": bundle["system_prompt"],
        "task_prompt": bundle["task_prompt"],
        "evidence": [
            {
                "scope": item.get("scope", ""),
                "supports": item.get("supports", []),
                "source_type": item.get("source_type", "unknown"),
            }
            for item in evidence
            if isinstance(item, Mapping)
        ],
        "bundle_id": bundle.get("bundle_id"),
        "eligibility": "blueprint_only",
        "external_call_performed": False,
        "message_zh": "已从本地章节摘要和蒸馏规则编译提示。未调用模型，也未生成题目。",
    }
    if payload.get("theme_reference", "none") == REFERENCE_KEY:
        try:
            reference = compile_structure_reference(workspace, loader=structure_loader)
        except Exception as exc:
            raise PromptPreviewError(
                "prompt_structure_unavailable",
                "所选试题结构参考未通过本地核验。请检查资料完整性，或选“仅使用教材”后重新编译。",
            ) from exc
        result["evidence"].append(reference["evidence"])
        result["structure_reference"] = reference
        result["task_prompt"] += "\n\n试题结构参考（只迁移组织方式）：\n" + "\n".join(
            reference["evidence"]["supports"]
        )
        result["message_zh"] = (
            "已编译教材摘要、命题规则和所选试题的结构参考。"
            "结构参考不含原题答案、不代替化学核验。未调用模型。"
        )
    if payload.get("handout_reference") is not None:
        from .desktop_handout_prompt_reference import (
            TRANSFER_INSTRUCTIONS,
            HandoutReferenceError,
        )

        try:
            if handout_loader is None:
                raise HandoutReferenceError("请从工作台已保存的讲义选题中选择参考。")
            handout = handout_loader(payload["handout_reference"])
        except Exception as exc:
            raise PromptPreviewError(
                "prompt_handout_unavailable",
                exc.message_zh
                if isinstance(exc, HandoutReferenceError)
                else "所选讲义原始文字或公式未通过核验，请回到讲义选题检查来源后重试。",
            ) from exc
        result["evidence"].extend(handout["evidence"])
        result["handout_reference"] = handout
        result["system_prompt"] += "\n\n" + TRANSFER_INSTRUCTIONS
        result["task_prompt"] += (
            f"\n\n本次参考 {handout['question_count']} 道讲义选题，原题内容见资料依据，"
            "生成时随 E 编号摘要一并发送。请提炼考查关系并说明改写方式：\n"
            + "\n".join(item["scope"] for item in handout["evidence"])
        )
        result["message_zh"] = (
            f"已将 {handout['question_count']} 道讲义选题与教材依据编译成提示。"
            + (
                "已加入非官方参考解答。"
                if handout["include_answers"]
                else "未加入参考答案。"
            )
            + "请在资料依据页核对原题内容；尚未调用模型。"
        )
    return result
