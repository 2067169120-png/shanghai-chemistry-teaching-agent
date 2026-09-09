"""Offline lesson references from the exact, already-read library theme.

The reader exposes summaries and requirements, not transcribed original pages.
Keep that distinction in the material sent through the ordinary preparation form.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .desktop_blueprint_drafts import BlueprintDraftError
from .desktop_blueprint_preparation import MAX_MATERIALS
from .desktop_library import LibraryThemeDetail


def library_preparation_reference(
    detail: LibraryThemeDetail,
    selected_keys: Sequence[str],
    *,
    include_answers: bool = True,
) -> dict:
    """Project selected units in reader order; never read files or call AI."""
    if not isinstance(detail, LibraryThemeDetail) or detail.scope not in {
        "master",
        "wave1",
        "supplemental",
    }:
        raise BlueprintDraftError(
            "library_reference_invalid", "当前资料没有可用的题库主题详情，请重新查找。"
        )
    keys = [part.key for part in detail.parts]
    if (
        not isinstance(selected_keys, (list, tuple))
        or not selected_keys
        or any(not isinstance(key, str) or not key for key in selected_keys)
        or len(set(keys)) != len(keys)
        or len(set(selected_keys)) != len(selected_keys)
        or not set(selected_keys).issubset(keys)
    ):
        raise BlueprintDraftError(
            "library_reference_selection_invalid",
            "请选择当前主题中至少一个有效作答单元。",
        )
    selected = set(selected_keys)
    lines = [
        "【题库备课参考：当前阅读快照，不是原题全文或已核定答案】",
        "主题：" + detail.title_zh,
        "来源卷：" + detail.paper_title_zh,
        "来源信息：" + detail.source_zh,
        "原卷页码：" + detail.page_zh,
        "仅作为例题、练习与讲评的选材线索；对应Word与教材决定章节结构、定义原句和完整知识表，不把PPT做成题目罗列。",
        "下面的题意摘要和作答要求不等于原题全文。图片、公式对象及未取得的题干未随文字导入；不得据摘要补造原题、数据或官方评分点。",
        "先呈现知识与可抄写的笔记，再安排作答和反馈；参考答案只供讲评，不提前混入学生题面。",
    ]
    for label, value in detail.source_fields:
        if label and value:
            lines.append(f"{label}：{value}")
    lines.extend(["", "共同材料摘要（不替代原页）：", detail.context_zh])
    warnings = list(detail.notes_zh)
    if len(selected) < len(keys):
        warnings.append(
            "本次仅选部分作答单元；共同材料摘要保留。请核对前问依赖，必要时补选前问或另行提供其结论；未自动带入未选小问。"
        )
    for index, part in enumerate(detail.parts, 1):
        if part.key not in selected:
            continue
        lines.extend(
            [
                "",
                f"主题内第{index}个作答单元 · {part.label_zh}",
                "题库题意摘要（不是原题全文）："
                + (part.summary_zh or "未取得题意摘要"),
                "题库作答要求：" + (part.requirement_zh or "未取得作答要求"),
                "材料/前问依赖：" + (part.dependency_zh or "待核对，不推定独立作答"),
            ]
        )
        lines.extend(part.classification_zh)
        if part.availability_zh:
            lines.append("原题文字/图像可用情况：" + part.availability_zh)
        if include_answers:
            lines.extend(
                [
                    "参考答案（沿用题库记录，本次未重新核验）：",
                    part.reference_answer_zh or "未取得对应答案，不补写模型答案。",
                    "答案证据边界：" + part.answer_boundary_zh,
                ]
            )
            if part.analysis_zh:
                lines.append("模型候选解路（非官方解析）：")
                lines.extend(part.analysis_zh)
        else:
            lines.append("本次未选入本单元的参考答案、候选解路和答案证据字段。")
        warnings.extend(f"{part.label_zh}：{note}" for note in part.quality_notes_zh)
        if part.question_images:
            warnings.append(
                f"{part.label_zh}：有原题图片，本次文字参考不含图片，请从题面详情另行选择加入教学图片。"
            )
    if detail.shared_images:
        warnings.append("本主题有共同材料原图，尚未随文字导入。")
    lines.extend(["", "导入缺口与提醒：", *dict.fromkeys(warnings)])
    text = "\n".join(lines)
    if len(text) > MAX_MATERIALS:
        raise BlueprintDraftError(
            "library_reference_too_large",
            "所选题库参考超过20000字，未截断。请减少作答单元或取消答案后再导入。",
        )
    return {
        "materials": text,
        "source_revision": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "theme_count": 1,
        "question_count": len(selected),
        "warnings": list(dict.fromkeys(warnings)),
    }
