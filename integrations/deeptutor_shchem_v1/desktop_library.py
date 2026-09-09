"""Qt-free, read-only teacher projections for native question browsing.

Source identifiers are opaque action bindings, never inferred from filenames.
Question/shared descriptors and teacher answer descriptors have separate routes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .datong_crop_revision import visible_evidence
from .paper_export_alias_projection import project_direct_unit_scan
from .question_search_workbench import VALUE_LABELS_ZH
from .supplemental_answers import validate_supplemental_answer

_RESPONSE_LABELS = {
    "embedded_single_choice": "单项选择",
    "single_choice": "单项选择",
    "embedded_multiple_choice": "多项选择",
    "multiple_choice": "多项选择",
    "embedded_indeterminate_choice": "不定项选择",
    "indefinite_choice": "不定项选择",
    "fill_blank": "填空",
    "short_fill": "填空",
    "data_based_fill_blank": "资料填空",
    "chemical_equation_writing": "化学方程式书写",
    "equation_writing": "方程式书写",
    "ionic_equation_writing": "离子方程式书写",
    "electrode_equation_writing": "电极反应式",
    "calculation": "计算",
    "numeric_calculation": "数值计算",
    "quantitative_calculation": "定量计算",
    "symbolic_calculation": "符号计算",
    "reason_explanation": "原因解释",
    "reasoned_explanation": "原因解释",
    "short_answer": "简答",
    "short_explanation": "简要说明",
    "short_response": "简答",
    "experiment_design": "实验设计",
    "experiment_evaluation": "实验评价",
    "structure_inference": "结构推断",
    "organic_structure_or_route": "有机结构/合成路线",
    "graph_read_draw_complete": "图表读图/作图",
    "graph_based_fill_blank": "图表填空",
    "diagram_completion": "图示补全",
    "synthesis_route": "合成路线",
}


def response_label(value: Any) -> str:
    return (
        VALUE_LABELS_ZH.get(value, _RESPONSE_LABELS.get(value, "作答形式待整理"))
        if isinstance(value, str)
        else "作答形式待整理"
    )


def difficulty_label(value: Any) -> str:
    return VALUE_LABELS_ZH.get(value, "待核对") if isinstance(value, str) else "待核对"


@dataclass(frozen=True)
class LibraryImage:
    scope: str = field(repr=False)
    node_id: str = field(repr=False)
    crop_id: str = field(repr=False)
    sha256: str = field(repr=False)
    role: str
    caption_zh: str
    width: int = 0
    height: int = 0
    view_id: str = field(default="", repr=False)


@dataclass(frozen=True)
class LibraryPartDetail:
    key: str = field(repr=False)
    label_zh: str
    summary_zh: str
    requirement_zh: str
    dependency_zh: str
    question_images: tuple[LibraryImage, ...] = ()
    classification_zh: tuple[str, ...] = ()
    analysis_zh: tuple[str, ...] = ()
    reference_answer_zh: str = ""
    answer_boundary_zh: str = "原资料暂未提供可对齐的参考答案。"
    supplemental_answer_zh: str = ""
    supplemental_explanation_zh: str = ""
    supplemental_diagram_key: str | None = None
    quality_notes_zh: tuple[str, ...] = ()
    availability_zh: str = ""
    answer_images: tuple[LibraryImage, ...] = ()


@dataclass(frozen=True)
class LibraryThemeDetail:
    key: str = field(repr=False)
    scope: str = field(repr=False)
    title_zh: str
    paper_title_zh: str
    source_zh: str
    page_zh: str
    context_zh: str
    shared_images: tuple[LibraryImage, ...] = ()
    parts: tuple[LibraryPartDetail, ...] = ()
    source_fields: tuple[tuple[str, str], ...] = ()
    notes_zh: tuple[str, ...] = ()


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, default: str = "") -> str:
    if (
        isinstance(value, str)
        and value.strip()
        and value not in {"unknown", "none", "pending", "null"}
    ):
        return value
    return default


def _texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if _text(value) else ()
    if isinstance(value, list):
        return tuple(item for item in value if _text(item))
    return ()


def image_descriptors(
    scope: str, node_id: str, scan: Mapping[str, Any], *, view_id: str = ""
) -> tuple[LibraryImage, ...]:
    """Whitelist the existing evidence roles; never follow image endpoints."""
    images = []
    for item in visible_evidence(scan.get("evidence_descriptors", [])):
        if not isinstance(item, Mapping):
            continue
        role = item.get("evidence_role")
        crop_id, digest = item.get("crop_id"), item.get("sha256")
        if (
            role not in {"question", "shared_material"}
            or not isinstance(crop_id, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            continue
        page = item.get("source_page")
        label = "题面" if role == "question" else "共同材料"
        caption = f"{label} · 第 {page} 页" if type(page) is int else label
        if (
            isinstance(item.get("presentation_revision_id"), str)
            and item["presentation_revision_id"]
            and isinstance(item.get("archived_crop_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", item["archived_crop_sha256"])
            and item["archived_crop_sha256"] != digest
        ):
            caption += " · 裁图已修订"
        images.append(
            LibraryImage(
                scope=scope,
                node_id=node_id,
                crop_id=crop_id,
                sha256=digest,
                role=role,
                caption_zh=caption,
                width=item.get("width") if type(item.get("width")) is int else 0,
                height=item.get("height") if type(item.get("height")) is int else 0,
                view_id=view_id,
            )
        )
    return tuple(images)


def answer_image_descriptors(
    scope: str, node_id: str, scan: Mapping[str, Any], *, view_id: str = ""
) -> tuple[LibraryImage, ...]:
    """Keep explicitly aligned answer images out of all question-image lists."""
    answer = _object(scan.get("reference_answer"))
    if (
        answer.get("availability") != "present_part_aligned"
        or answer.get("source_authority") != "nonofficial_reference"
    ):
        return ()
    rows = scan.get("reference_answer_images", [])
    if not isinstance(rows, list):
        return ()
    images = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        digest, crop_id = item.get("sha256"), item.get("crop_id")
        if (
            item.get("evidence_role") != "answer"
            or item.get("access") != "teacher_reference_answer_only"
            or item.get("display_mode") not in {"inline_required", "preview_only"}
            or not isinstance(crop_id, str)
            or not crop_id
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(item.get("source_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"])
        ):
            continue
        images.append(LibraryImage(
            scope=scope, node_id=node_id, crop_id=crop_id, sha256=digest,
            role="answer", caption_zh=_text(item.get("caption_zh"), "非官方参考答案原图"),
            width=item.get("width") if type(item.get("width")) is int else 0,
            height=item.get("height") if type(item.get("height")) is int else 0,
            view_id=view_id,
        ))
    return tuple(images)


def build_theme_detail(
    card: Any,
    paper: Mapping[str, Any],
    group: Mapping[str, Any],
    load_scan: Callable[[str, str], tuple[str, str, Mapping[str, Any]]],
    *,
    view_id: str = "",
) -> LibraryThemeDetail:
    """Preserve theme/printed/atomic order, including explicit alias units.

    Missing scans do not hide the remaining theme. Source answer text is copied
    only from an aligned reference projection, never from model analysis.
    """
    chain = [
        item for item in group.get("atomic_chain", []) if isinstance(item, Mapping)
    ]
    units: list[tuple[str, Mapping[str, Any], str, str]] = []
    for atom in chain:
        aliases = atom.get("alias_units")
        explicit = aliases if isinstance(aliases, list) and aliases else [atom]
        for unit in explicit:
            if not isinstance(unit, Mapping):
                continue
            # Grouped direct scans retain their Master lookup parent while
            # display identities refer to independently answerable units.
            # Historical aliases without a scope continue to bind to Wave1.
            merged = dict(atom)
            merged.update(unit)
            node_id = _text(unit.get("atomic_part_id"))
            scope = (
                _text(unit.get("source_scope"), "wave1")
                if explicit is aliases
                else card.scope
            )
            lookup_node = (
                _text(unit.get("source_parent_atomic_id"))
                if explicit is aliases and scope == "master"
                else node_id
            )
            if node_id:
                units.append((scope, merged, node_id, lookup_node))
    printed_counts: dict[str, int] = {}
    for _, atom, _, _ in units:
        parent = _text(atom.get("printed_question_id"))
        printed_counts[parent] = printed_counts.get(parent, 0) + 1
    labels = {}
    for _, atom, node_id, _ in units:
        number = _text(atom.get("printed_question_number"))
        if not number and type(atom.get("printed_question_number")) is int:
            number = str(atom["printed_question_number"])
        label = f"原卷第 {number} 题" if number else "原卷题号待核对"
        parent = _text(atom.get("printed_question_id"))
        part = atom.get("atomic_sequence_in_printed")
        if printed_counts.get(parent, 0) > 1 and type(part) is int:
            label += f" · 作答单元 {part}"
        labels[node_id] = label
    parts = []
    shared: dict[tuple[str, str, str], LibraryImage] = {}
    for scope, atom, node_id, lookup_node in units:
        scan: Mapping[str, Any] = {}
        availability = ""
        try:
            if not lookup_node:
                raise ValueError("grouped direct unit has no explicit lookup parent")
            image_scope, image_node, scan = load_scan(scope, lookup_node)
            if scope == "master" and atom.get("source_scope") == "master":
                scan = project_direct_unit_scan(scan, node_id)
        except (RuntimeError, ValueError, OSError):
            scan = {}
            image_scope, image_node = scope, node_id
            availability = (
                "此作答单元的逐图详情暂不可读；保留目录摘要，未补写题面或答案。"
            )
        images = image_descriptors(image_scope, image_node, scan, view_id=view_id)
        for image in images:
            if image.role == "shared_material":
                shared.setdefault((image.scope, image.crop_id, image.sha256), image)
        question = tuple(image for image in images if image.role == "question")
        if not question and not availability:
            availability = "该作答单元尚无可读题面裁片；下方摘要不是完整题面。"
        dependency = _object(atom.get("dependency"))
        kind = dependency.get("kind")
        dependency_text = {
            "independent": "无已记录的前题结论依赖",
            "shared_material_only": "使用本主题共同材料",
            "prior_part_only": "依赖前序作答单元",
            "one_prior_part": "依赖一个前序作答单元",
            "multiple_prior_parts": "依赖多个前序作答单元",
            "shared_and_prior": "使用共同材料及前序结论",
            "shared_material_and_prior_part": "使用共同材料及前序结论",
        }.get(kind, "依赖关系待核对")
        prior = dependency.get("prior_atomic_part_ids")
        if isinstance(prior, list) and prior:
            dependency_text += "：" + "、".join(
                labels.get(value, "前序单元待核对") for value in prior
            )
        # A source's explicit explanation supplements, not replaces, the theme
        # dependency edges and native printed labels above.
        explanation = _text(_object(scan.get("dependency")).get("analysis_zh"))
        if explanation:
            dependency_text += "。" + explanation
        classification = _object(scan.get("scan_classification")) or atom
        difficulty = _object(scan.get("cognitive_difficulty")) or _object(
            atom.get("label_summary")
        )
        classification_lines = [
            "作答形式：" + response_label(classification.get("item_type")),
            "认知难度候选："
            + difficulty_label(difficulty.get("cognitive_prelabel"))
            + "（非学生实测）",
        ]
        labels_for_axes = _object(atom.get("label_summary"))
        for axis, label in (
            ("primary_K", "主知识"),
            ("supporting_K", "辅助知识"),
            ("knowledge_candidates_K", "知识点候选（主辅待核对）"),
            ("A", "能力"),
            ("C", "情境"),
            ("R", "作答方式"),
            ("RP", "信息表征"),
        ):
            values = _texts(classification.get(axis)) or _texts(
                labels_for_axes.get(axis)
            )
            if values:
                classification_lines.append(
                    label
                    + "："
                    + "、".join(
                        VALUE_LABELS_ZH.get(value, value + "（名称待补）")
                        for value in values
                    )
                )
        analysis = _object(scan.get("model_candidate_analysis")) or _object(
            scan.get("candidate_analysis")
        )
        answer = _object(scan.get("reference_answer"))
        answer_text = ""
        boundary = "原资料暂未提供可对齐的参考答案。"
        if (
            answer.get("availability") == "present_part_aligned"
            and answer.get("source_authority") == "nonofficial_reference"
        ):
            answer_text = _text(answer.get("reference_answer_text"))
            boundary = (
                "来源参考答案（非官方）；系统未独立核验，不等于官方答案或评分细则。"
            )
        elif answer.get("availability") == "present_unaligned":
            boundary = "来源有参考答案，但尚未对齐本作答单元；不展示错配正文。"
        supplement = (
            validate_supplemental_answer(scan["supplemental_answer"])
            if "supplemental_answer" in scan else {}
        )
        risks = _object(scan.get("risks_and_limits"))
        notes = list(_texts(answer.get("quality_note")))
        notes.extend(_texts(scan.get("quality_notes")))
        if supplement:
            notes.extend(f"易错点：{value}" for value in supplement["pitfalls_zh"])
        for key, prefix in (
            ("common_errors_zh", "易错点"),
            ("ambiguity_or_multiple_solutions_zh", "质量备注"),
            ("information_insufficiency_zh", "资料边界"),
            ("safety_or_apparatus_risks_zh", "安全提醒"),
        ):
            if supplement and key != "safety_or_apparatus_risks_zh":
                continue  # The newly solved answer owns its reasoning/reminders.
            notes.extend(f"{prefix}：{value}" for value in _texts(risks.get(key)))
        parts.append(
            LibraryPartDetail(
                key=node_id,
                label_zh=labels[node_id],
                summary_zh=_text(scan.get("visible_summary_zh"))
                or _text(atom.get("visible_summary_zh"), "题意摘要待整理。"),
                requirement_zh=_text(scan.get("response_requirement_zh"))
                or _text(atom.get("response_requirement_zh"), "作答要求待整理。"),
                dependency_zh=dependency_text,
                question_images=question,
                classification_zh=tuple(classification_lines),
                analysis_zh=_texts(analysis.get("solution_path_zh")),
                reference_answer_zh=answer_text,
                answer_boundary_zh=boundary,
                supplemental_answer_zh=supplement.get("text_zh", ""),
                supplemental_explanation_zh=supplement.get("explanation_zh", ""),
                supplemental_diagram_key=supplement.get("diagram_key"),
                quality_notes_zh=tuple(dict.fromkeys(notes)),
                availability_zh=availability,
                answer_images=answer_image_descriptors(
                    image_scope, image_node, scan, view_id=view_id
                ),
            )
        )
    metadata = _object(paper.get("source_metadata"))
    source_fields = [("来源卷", _text(paper.get("title"), card.paper_title_zh))]
    for key, label in (
        ("year", "年份"),
        ("region", "区域"),
        ("school", "学校"),
        ("paper_type", "资料类型"),
    ):
        value = metadata.get(key)
        if type(value) is int:
            value = str(value)
        if _text(value):
            source_fields.append((label, VALUE_LABELS_ZH.get(value, value)))
    return LibraryThemeDetail(
        key=card.key,
        scope=card.scope,
        title_zh=card.title_zh,
        paper_title_zh=card.paper_title_zh,
        source_zh=card.source_zh,
        page_zh=card.page_zh,
        context_zh=_text(
            _object(group.get("shared_context")).get("context_summary_zh"),
            "共同材料摘要待整理；请以原始题图为准。",
        ),
        shared_images=tuple(shared.values()),
        parts=tuple(parts),
        source_fields=tuple(source_fields),
        notes_zh=(
            "按主题保留原卷小题顺序；题意、分类和解路为模型整理候选。",
            "题图仅供本机来源核对；浏览和加入题篮不改变审核、版权或发布资格。",
        ),
    )
