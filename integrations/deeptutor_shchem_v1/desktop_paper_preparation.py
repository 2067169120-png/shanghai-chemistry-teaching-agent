"""Offline, text-only teaching reference from the current editable paper.

This is a teacher working-copy snapshot, not a reconstruction of original pages.
Only known text fields are copied; local image paths and opaque blobs stay local.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from .desktop_blueprint_drafts import BlueprintDraftError
from .desktop_blueprint_preparation import MAX_MATERIALS


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def paper_preparation_reference(
    snapshot: Mapping, selected_indices: Sequence[int], *, include_answers: bool = True
) -> dict:
    themes = snapshot.get("themes", [])
    if not isinstance(themes, list) or not selected_indices:
        raise BlueprintDraftError(
            "paper_reference_empty", "请至少选择一道大题作为备课参考。"
        )
    indices = set(selected_indices)
    if any(type(i) is not int or not 0 <= i < len(themes) for i in indices):
        raise BlueprintDraftError(
            "paper_reference_invalid", "选题范围已无效，请重新打开组卷参考。"
        )
    lines = [
        "【当前组卷备课参考：教师工作副本，不是原卷全文或已核定答案】",
        "原编排名称：" + (_text(snapshot.get("title")) or "未命名组卷"),
        "本段仅作例题、练习与讲评素材；对应Word与教材决定知识结构、定义和笔记，不能把PPT做成题目罗列。",
        "保留共享材料→原编排小问的依赖。先讲知识和完整笔记表，再安排适量作答与逐题反馈；答案只用于反馈，不提前混入题面。",
        "这是导入时的本地文字快照，可能包含教师编辑。原题图片/图形/公式对象未随本段导入，不能凭摘要补造原题、数据或官方评分点。",
    ]
    for key, label in (("subtitle", "编排说明"), ("keywords", "教师填写的范围关键词")):
        value = _text(snapshot.get(key))
        if value:
            lines.append(label + "：" + value)
    warnings = []
    question_count = 0
    for index, theme in enumerate(themes):
        if index not in indices:
            continue
        if not isinstance(theme, Mapping):
            raise BlueprintDraftError(
                "paper_reference_invalid", "当前大题数据无法读取，请重新打开组卷。"
            )
        label = f"原编排第{index + 1}道大题"
        lines.extend(
            [
                "",
                label + "：" + (_text(theme.get("title")) or "未命名大题"),
                "来源标签（待核验）：" + (_text(theme.get("source")) or "来源待确认"),
                "教材关联标签（不是教材原句）："
                + (_text(theme.get("chapter")) or "教材章节待确认"),
            ]
        )
        detail = theme.get("source_detail", {})
        if isinstance(detail, Mapping):
            for key, field_label in (
                ("paper_title", "原始来源卷"),
                ("page", "原卷页码"),
                ("context", "题库情境摘要（不替代原文）"),
            ):
                if _text(detail.get(key)):
                    lines.append(field_label + "：" + _text(detail[key]))
            for pair in detail.get("source_fields", []):
                if isinstance(pair, Mapping):
                    pair = (pair.get("label"), pair.get("value"))
                if (
                    isinstance(pair, (list, tuple))
                    and len(pair) == 2
                    and all(isinstance(v, str) for v in pair)
                ):
                    lines.append(pair[0] + "：" + pair[1])
            for note in detail.get("notes", []):
                if _text(note):
                    warnings.append(label + "：" + _text(note))
        if _text(theme.get("source_detail_warning")):
            warnings.append(label + "：" + _text(theme["source_detail_warning"]))
        shared = _text(theme.get("shared_text"))
        summary = _text(theme.get("shared_summary"))
        if shared:
            lines.append("当前共享材料文字：\n" + shared)
        if summary and summary != shared:
            lines.append("共享材料摘要（不等于原文）：\n" + summary)
        for material in theme.get("shared_materials", []):
            if isinstance(material, Mapping):
                # Only explicit text. Never stringify crop refs, paths or arbitrary dicts.
                value = _text(material.get("text"))
                if value and value not in (shared, summary):
                    lines.append("共享材料补充文字：\n" + value)
        questions = theme.get("questions", [])
        if not isinstance(questions, list):
            raise BlueprintDraftError(
                "paper_reference_invalid", "当前小问数据无法读取，请重新打开组卷。"
            )
        for q_index, question in enumerate(questions, 1):
            if not isinstance(question, Mapping):
                raise BlueprintDraftError(
                    "paper_reference_invalid", "当前小问数据无法读取，请重新打开组卷。"
                )
            question_count += 1
            q_label = f"{label}·当前第{q_index}个作答单元"
            lines.extend(["", q_label])
            number = _text(question.get("source_question_number"))
            if number:
                lines.append("原印刷题号：" + number)
            for key, field_label in (
                ("response_type", "作答形式"),
                ("section", "教材关联标签"),
                ("dependency", "材料/前问依赖"),
            ):
                value = _text(question.get(key))
                if value:
                    lines.append(field_label + "：" + value)
            stem = _text(question.get("stem"))
            lines.append("当前题面文字：\n" + (stem or "未取得题面文字"))
            options = question.get("options", [])
            if isinstance(options, list):
                lines.extend(_text(option) for option in options if _text(option))
            if not stem or "待展开" in stem or "待确认" in stem:
                warnings.append(
                    q_label + "：题面仍有待展开/待确认内容，不能据此还原完整原题。"
                )
            if question.get("crop_available"):
                warnings.append(
                    q_label + "：存在原题图片，本次仅复制文字，图片未导入。"
                )
            if include_answers:
                answer = _text(question.get("answer"))
                analysis = _text(question.get("analysis"))
                lines.append(
                    "参考答案（教师工作副本，非本桥接核验）：\n"
                    + (answer or "未取得答案文字")
                )
                if analysis:
                    lines.append("参考解析（同样待核验）：\n" + analysis)
                source_answer = question.get("source_detail", {})
                has_source_answer = isinstance(source_answer, Mapping) and _text(
                    source_answer.get("reference_answer")
                )
                if not answer and not has_source_answer:
                    warnings.append(
                        q_label
                        + "：未取得答案文字，不能把可打开答案图片当作已读取答案。"
                    )
            else:
                lines.append("本次未选入答案与解析。")
            detail = question.get("source_detail", {})
            if isinstance(detail, Mapping):
                for key, field_label in (
                    ("summary", "题库题意摘要（不替代原图）"),
                    ("requirement", "题库作答要求"),
                ):
                    if _text(detail.get(key)):
                        lines.append(field_label + "：" + _text(detail[key]))
                if include_answers:
                    for key, field_label in (
                        ("reference_answer", "题库补充参考答案（非官方，待核验）"),
                        ("answer_boundary", "答案证据边界"),
                    ):
                        if _text(detail.get(key)):
                            lines.append(field_label + "：\n" + _text(detail[key]))
                    analysis_items = detail.get("analysis", [])
                    if isinstance(analysis_items, (tuple, list)):
                        for value in analysis_items:
                            if _text(value):
                                lines.append(
                                    "模型候选解路（非核定解析）：" + _text(value)
                                )
                for note in detail.get("notes", []):
                    if _text(note):
                        warnings.append(q_label + "：" + _text(note))
            if _text(question.get("source_detail_warning")):
                warnings.append(
                    q_label + "：" + _text(question["source_detail_warning"])
                )
        if theme.get("shared_materials"):
            warnings.append(
                label
                + "：共享材料的图片/附件对象未导入，请核对是否需另行加入教学图片。"
            )
    lines.extend(
        [
            "",
            "导入缺口与提醒：",
            *(
                warnings
                or ["本段仅表示取得了当前文字，完整性与教学适用性仍需对照原页核验。"]
            ),
        ]
    )
    materials = "\n".join(lines)
    digest = hashlib.sha256(materials.encode("utf-8")).hexdigest()
    if len(materials) > MAX_MATERIALS:
        raise BlueprintDraftError(
            "paper_reference_too_large",
            f"所选组卷参考超过{MAX_MATERIALS}字，未截断。请减少大题范围后导入。",
        )
    return {
        "materials": materials,
        "source_revision": digest,
        "theme_count": len(indices),
        "question_count": question_count,
        "warnings": warnings,
    }
