"""Compose frozen core themes and native Word selections into two DOCX bytes.

No provider, state mutation, Office process or PDF conversion occurs here.
Source packages are read, never rewritten. The caller owns source/asset freeze
validation and approval; both native outputs are returned only after success.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path

from .desktop_word_question_export import (
    WordQuestionExportError,
    _new_document,
    _prepare,
    _Writer,
)
from .paper_export_renderer import (
    _add_theme_sections,
    _configure_section,
    _content_width_dxa,
    _display_theme_heading,
    _shared_question_visual_dedup,
    _style_document,
    default_shanghai_theme_preset,
    validate_render_bundle,
)


def build_mixed_paper_docx(
    title: str,
    sections: list[dict],
    *,
    duration_minutes: int = 40,
    show_student_scores: bool = False,
    subtitle: str = "",
) -> dict:
    """Keep source question/answer boundaries and interleaved selection order.

    Each core bundle is one complete dependency-closed theme, not an extracted
    isolated answer unit. Word selections retain OOXML tables, equations,
    source styles and embedded image bytes. Scores are this exercise's teacher
    allocation; the switch never erases a source's printed score or blanks.
    """
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise WordQuestionExportError("请填写 1 至 160 字的练习名称。")
    if not isinstance(subtitle, str) or len(subtitle) > 300:
        raise WordQuestionExportError("练习说明须为不超过 300 字的文字。")
    if type(duration_minutes) is not int or not 1 <= duration_minutes <= 600:
        raise WordQuestionExportError("练习时长须为 1 至 600 分钟的整数。")
    if type(show_student_scores) is not bool:
        raise WordQuestionExportError("题面分值开关无效，请重新选择。")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 100:
        raise WordQuestionExportError("请选择 1 至 100 个完整题目或大题。")

    prepared = []
    word_rows = []
    for section in sections:
        if not isinstance(section, dict):
            raise WordQuestionExportError("题篮条目格式不正确，请重新预览。")
        if section.get("kind") == "word_question":
            word_rows.append(section.get("question"))
            prepared.append(("word_question", None, None))
        elif section.get("kind") == "core_plan":
            bundle = validate_render_bundle(section.get("bundle"))
            if any(
                len(bundle[role + "_plan"]["visible"]["theme_sections"]) != 1
                for role in ("student", "teacher")
            ):
                raise WordQuestionExportError(
                    "每个原卷条目必须保留一个完整主题及公共材料。"
                )
            root = section.get("asset_root")
            if root is not None and not isinstance(root, Path):
                raise WordQuestionExportError("冻结题图目录格式不正确。")
            prepared.append(("core_plan", bundle, root))
        elif section.get("kind") == "personal_visual_theme":
            from .desktop_personal_visual_theme_writer import build_theme_blocks

            visual_item, assets = section.get("item"), section.get("assets")
            for audience in ("student", "teacher"):
                build_theme_blocks(visual_item, assets, audience, show_scores=show_student_scores)
            prepared.append(("personal_visual_theme", visual_item, assets))
        else:
            raise WordQuestionExportError("题篮条目类型不支持，请重新选择。")

    # Validate Word rows together: an answer for one selection cannot leak
    # into another selection's question/shared context, even across core items.
    word_items = iter(_prepare(word_rows) if word_rows else [])
    prepared = [
        (kind, next(word_items) if kind == "word_question" else item, root)
        for kind, item, root in prepared
    ]
    total = sum(
        item.points
        if kind == "word_question"
        else sum(row["max_score"] or 0 for row in item["content"]["source_scores"])
        if kind == "personal_visual_theme"
        else item["student_plan"]["visible"]["theme_sections"][0]["theme_score"]
        for kind, item, _root in prepared
    )
    has_visual = any(kind == "personal_visual_theme" for kind, _item, _root in prepared)
    scores_complete = all(
        row["max_score"] is not None
        for kind, item, _root in prepared
        if kind == "personal_visual_theme"
        for row in item["content"]["source_scores"]
    )
    # Use the application's established A4 exercise layout consistently across
    # sources; source formatting is namespaced by the native Word writer.
    preset = next(
        (item["preset"] for kind, item, _root in prepared if kind == "core_plan"),
        default_shanghai_theme_preset(),
    )
    outputs, warnings = {}, []
    for audience in ("student", "teacher"):
        teacher = audience == "teacher"
        scores = teacher or show_student_scores
        document = _new_document(title.strip(), teacher)
        if teacher and has_visual:
            document.paragraphs[2].text = (
                "图片主题保留来源参考分值；Word及其他题组按本次设置。"
                "答案供备课和讲评使用，未核定分值明确标为待核对。"
            )
        _style_document(document, preset)
        _configure_section(document, preset, {"header": {"text_zh": title.strip()}})
        if subtitle.strip():
            document.add_paragraph(subtitle.strip())
        meta = f"{duration_minutes} 分钟 · {len(prepared)} 个题组"
        if scores:
            label = "合计已知分值" if has_visual else "本次分值"
            meta += f" · {label} {total:g} 分"
            if not scores_complete:
                meta += "（部分分值待核对，非总分）"
        document.add_paragraph(meta, "ShChemPaperMeta")
        if not teacher:
            document.add_paragraph(
                "姓名：____________　班级：____________", "ShChemPaperMeta"
            )
        writer = _Writer(document)
        for ordinal, (kind, item, asset_root) in enumerate(prepared, 1):
            if kind == "personal_visual_theme":
                from .desktop_personal_visual_theme_writer import (
                    append_personal_visual_theme,
                )

                append_personal_visual_theme(document, item, asset_root, audience,
                                             ordinal=ordinal, show_scores=show_student_scores)
                continue
            if kind == "word_question":
                score = f"（{item.points:g} 分）" if scores else ""
                document.add_paragraph(f"题组 {ordinal}{score}", "ShChemThemeHeading")
                # Each selected question remains self-contained when reordered.
                # Do not globally suppress context that was many groups ago.
                if item.context:
                    document.add_paragraph("公共材料", "ShChemQuestion")
                    writer.append(item.source, item.context)
                writer.append(item.source, item.question, keep_question=True)
                if teacher:
                    document.add_paragraph(f"来源：{item.source_name}", "ShChemQuiet")
                    document.add_paragraph(
                        f"参考答案 · 本题 {item.points:g} 分", "ShChemQuestion"
                    )
                    if item.answer:
                        writer.append(item.source, item.answer)
                    else:
                        document.add_paragraph(
                            "当前选定范围未识别到本题答案，请核对原教案与题答边界；这不表示原文没有答案。"
                        )
                continue

            plan = item[audience + "_plan"]
            visible = deepcopy(plan["visible"])
            theme = visible["theme_sections"][0]
            blueprint_theme = item["blueprint"]["theme_bundles"][0]
            source_heading = _display_theme_heading(theme, blueprint_theme)
            theme["heading_zh"] = f"题组 {ordinal} · {source_heading}"
            suppressed = set(
                _shared_question_visual_dedup(plan, asset_root)[
                    "suppressed_shared_keys"
                ]
            )
            _add_theme_sections(
                document,
                visible,
                audience=audience,
                asset_root=asset_root,
                content_width_dxa=_content_width_dxa(preset),
                body_size_pt=float(preset["page_layout"]["body"]["size_pt"]),
                blueprint=item["blueprint"],
                suppressed_shared_keys=suppressed,
                show_question_scores=scores,
            )
        document.core_properties.subject = (
            "本地混合选编练习；题目来源见教师版；不可发布"
        )
        document.core_properties.comments = (
            "保留来源题面；图片主题采用来源参考分值，其他题组采用本次设置。"
            "须在软件中核对实际分页；排版确认不等于化学或教学审核。"
        )
        stream = BytesIO()
        document.save(stream)
        outputs[audience + "_bytes"] = stream.getvalue()
        warnings.extend(writer.warnings)
    outputs["warnings"] = list(dict.fromkeys(warnings))
    return outputs
