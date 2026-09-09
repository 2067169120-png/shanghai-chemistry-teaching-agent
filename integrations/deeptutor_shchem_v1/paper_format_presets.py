from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Protocol

if __package__:
    from .supplemental_answers import ANSWER_LABEL, validate_supplemental_answer
else:
    from supplemental_answers import ANSWER_LABEL, validate_supplemental_answer

PAPER_FORMAT_SCHEMA_VERSION = "shchem.paper-format-contract.v1"
PRESET_KIND = "paper_format_preset"
BLUEPRINT_KIND = "assembly_blueprint"
DOCUMENT_PLAN_KIND = "document_plan"
PREFLIGHT_KIND = "export_preflight_report"
RENDER_REQUEST_KIND = "render_request"
RENDER_RECEIPT_KIND = "render_receipt"
RENDER_REPORT_KIND = "render_validation_report"

TOP_LEVEL_UNIT = "theme_big_question"
HIERARCHY = ("paper", "theme_big_question", "printed_question", "atomic_part")

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_VERIFICATION_STATES = frozenset(
    {
        "unknown_requires_exact_paper",
        "project_template_value",
        "observed_nonofficial",
        "verified_for_exact_paper",
        "blocked_pending_review",
    }
)
_NUMBERING_MODES = frozenset(
    {"continuous_across_paper", "restart_within_each_theme"}
)
_SHARED_MATERIAL_ORDER_BASES = frozenset(
    {"source_page_order", "dependency_order"}
)
_SELECTION_UNITS = frozenset({"theme", "dependency", "atomic"})
_DEPENDENCY_KINDS = frozenset(
    {
        "independent",
        "shared_material_only",
        "one_prior_part",
        "multiple_prior_parts",
        "per_alias_unit",
        "blocked_pending_review",
    }
)
_BLOCK_TYPES = frozenset(
    {"paragraph", "formula", "image", "table", "structure", "apparatus"}
)
_ANSWER_STATES = frozenset({"aligned", "unaligned", "absent"})
_ANSWER_LABELS = frozenset({"nonofficial", "suggested", "none"})
_EXPLANATION_LABELS = frozenset(
    {"source_analysis", "teacher_analysis", "ai_candidate", "none"}
)
_STANDALONE_CHOICE_TITLES = frozenset(
    {"选择题", "单项选择题", "多项选择题", "不定项选择题"}
)


class PaperFormatContractError(ValueError):
    """Sanitized contract error that can be mapped to a Chinese UI message."""

    def __init__(self, code: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


class ThemeSnapshotLoader(Protocol):
    def __call__(
        self, scope: str, expected_data_snapshot_id: str
    ) -> Mapping[str, Any]: ...


class DocumentContentResolver(Protocol):
    def resolve_shared_material(self, reference: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def resolve_atomic_part(self, reference: Mapping[str, Any]) -> Mapping[str, Any]: ...


class RendererAdapter(Protocol):
    def render(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _require_mapping(value: Any, *, code: str, message_zh: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperFormatContractError(code, message_zh)
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], *, code: str, message_zh: str
) -> None:
    if set(value) != expected:
        raise PaperFormatContractError(code, message_zh)


def _safe_id(value: Any, *, code: str, message_zh: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise PaperFormatContractError(code, message_zh)
    return value


def _text(
    value: Any, *, code: str, message_zh: str, allow_none: bool = False, limit: int = 500
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PaperFormatContractError(code, message_zh)
    return value.strip()


def _verification(
    status: str = "unknown_requires_exact_paper",
    *,
    evidence_refs: Sequence[str] = (),
    verified_for_paper_id: str | None = None,
    note_zh: str = "须在生成或复刻具体试卷前逐卷核验。",
) -> dict[str, Any]:
    return {
        "status": status,
        "evidence_refs": list(evidence_refs),
        "verified_for_paper_id": verified_for_paper_id,
        "note_zh": note_zh,
    }


def _editable_field(
    value: Any = None,
    *,
    status: str = "unknown_requires_exact_paper",
    note_zh: str = "须在生成或复刻具体试卷前逐卷核验。",
) -> dict[str, Any]:
    return {
        "value": value,
        "editable": True,
        "verification": _verification(status, note_zh=note_zh),
    }


def default_shanghai_theme_preset() -> dict[str, Any]:
    """Return an editable Shanghai theme-paper project preset.

    The default deliberately contains no permanent theme count, score, duration,
    scoring rule, or subquestion-numbering claim.  Those values only become
    renderable after the teacher fills them for the target paper.
    """

    return {
        "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
        "contract_kind": PRESET_KIND,
        "preset_id": "shanghai_theme_paper_project_template",
        "preset_version": "1.0.0",
        "display_name_zh": "上海主题式试卷（可编辑项目模板）",
        "template_status": "editable_project_template",
        "claim_boundary_zh": (
            "按上海现行主题大题组织原则制作项目模板；不声称主题数、总分、"
            "时长、计分规则或版式参数是永久官方规定。"
        ),
        "structure": {
            "top_level_unit": TOP_LEVEL_UNIT,
            "hierarchy": list(HIERARCHY),
            "standalone_choice_section_allowed": False,
            "embedded_response_forms": [
                "choice_single",
                "choice_multiple",
                "fill_blank",
                "short_answer",
                "calculation",
                "chemical_equation",
                "structure_formula",
                "experiment_evaluation",
            ],
            "shared_material_policy": "render_once_per_theme_bundle",
            "dependency_policy": "explicit_transitive_closure",
            "theme_numbering": "chinese_sequential",
            "subquestion_numbering": _editable_field(),
        },
        "per_paper": {
            "theme_count": _editable_field(),
            "total_score": _editable_field(),
            "duration_minutes": _editable_field(),
            "scoring_rules": _editable_field(),
        },
        "page_layout": {
            "page_size": "A4",
            "orientation": "portrait",
            "margins_mm": {"top": 18, "bottom": 18, "left": 18, "right": 18},
            "title": {
                "cjk_font": "SimHei",
                "size_pt": 16,
                "bold": True,
                "alignment": "center",
            },
            "body": {
                "cjk_font": "SimSun",
                "latin_font": "Times New Roman",
                "size_pt": 10.5,
                "line_spacing": 1.25,
            },
            "theme_heading": {
                "cjk_font": "SimHei",
                "size_pt": 12,
                "bold": True,
            },
            "minimum_raster_dpi": 300,
        },
        "student_version": {
            "identity_fields_zh": ["姓名", "班级"],
            "show_duration": True,
            "show_total_score": True,
            "show_item_scores": True,
            "answer_space": {
                "mode": "per_atomic_or_teacher_editable_fallback",
                "fallback_lines": 3,
                "editable": True,
            },
            "header": {"enabled": True, "content": "paper_title"},
            "footer": {"enabled": True, "pattern_zh": "第 x 页 / 共 y 页"},
            "forbidden_visible_content": [
                "reference_answer",
                "explanation",
                "pitfalls",
                "source_label",
                "internal_tags",
            ],
        },
        "teacher_version": {
            "uses_same_blueprint": True,
            "include_source_reference_answer": True,
            "include_explanation": True,
            "include_pitfalls": True,
            "include_source_label": True,
            "allowed_answer_labels": ["nonofficial", "suggested", "none"],
        },
        "rendering": {
            "docx_is_source_of_truth": True,
            "artifacts": [
                {"artifact_id": "student_docx", "audience": "student", "format": "docx"},
                {
                    "artifact_id": "student_pdf",
                    "audience": "student",
                    "format": "pdf",
                    "source_artifact_id": "student_docx",
                },
                {"artifact_id": "teacher_docx", "audience": "teacher", "format": "docx"},
                {
                    "artifact_id": "teacher_pdf",
                    "audience": "teacher",
                    "format": "pdf",
                    "source_artifact_id": "teacher_docx",
                },
            ],
            "inspect_every_rendered_page": True,
            "rerender_after_every_change": True,
            "allowed_layout_defects": 0,
            "required_parity": [
                "blueprint_digest",
                "content_version_id",
                "theme_order",
                "question_anchor_digest",
                "figure_count",
                "page_count_within_audience",
            ],
            "secrets_forbidden_in_artifacts": True,
            "publication_allowed": False,
        },
    }


def _normalize_verification(value: Any) -> dict[str, Any]:
    record = _require_mapping(
        value,
        code="preset_verification_invalid",
        message_zh="逐卷核验状态格式不正确。",
    )
    _require_exact_keys(
        record,
        {"status", "evidence_refs", "verified_for_paper_id", "note_zh"},
        code="preset_verification_invalid",
        message_zh="逐卷核验状态字段不完整。",
    )
    status = record.get("status")
    refs = record.get("evidence_refs")
    paper_id = record.get("verified_for_paper_id")
    note = record.get("note_zh")
    if status not in _VERIFICATION_STATES:
        raise PaperFormatContractError(
            "preset_verification_invalid", "逐卷核验状态不在允许范围内。"
        )
    if (
        not isinstance(refs, list)
        or len(refs) != len(set(refs))
        or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in refs)
    ):
        raise PaperFormatContractError(
            "preset_verification_invalid", "逐卷核验依据列表不正确。"
        )
    if paper_id is not None and (
        not isinstance(paper_id, str) or not paper_id.strip() or len(paper_id) > 240
    ):
        raise PaperFormatContractError(
            "preset_verification_invalid", "逐卷核验的试卷标识不正确。"
        )
    if not isinstance(note, str) or not note.strip() or len(note) > 500:
        raise PaperFormatContractError(
            "preset_verification_invalid", "逐卷核验说明不能为空。"
        )
    if status == "verified_for_exact_paper" and (not paper_id or not refs):
        raise PaperFormatContractError(
            "preset_exact_verification_incomplete",
            "标为“已逐卷核验”时必须绑定具体试卷和核验依据。",
        )
    return {
        "status": status,
        "evidence_refs": list(refs),
        "verified_for_paper_id": paper_id,
        "note_zh": note.strip(),
    }


def _normalize_editable_field(
    value: Any,
    *,
    field_name: str,
    validator: Callable[[Any], Any],
) -> dict[str, Any]:
    record = _require_mapping(
        value,
        code="preset_field_invalid",
        message_zh=f"“{field_name}”字段格式不正确。",
    )
    _require_exact_keys(
        record,
        {"value", "editable", "verification"},
        code="preset_field_invalid",
        message_zh=f"“{field_name}”字段不完整。",
    )
    if record.get("editable") is not True:
        raise PaperFormatContractError(
            "preset_field_not_editable", f"“{field_name}”必须保持可编辑。"
        )
    normalized_value = validator(record.get("value"))
    verification = _normalize_verification(record.get("verification"))
    if normalized_value is None and verification["status"] in {
        "project_template_value",
        "observed_nonofficial",
        "verified_for_exact_paper",
    }:
        raise PaperFormatContractError(
            "preset_verification_without_value",
            f"“{field_name}”没有值，不能标成已有模板值或已核验。",
        )
    return {
        "value": normalized_value,
        "editable": True,
        "verification": verification,
    }


def _nullable_int(minimum: int, maximum: int) -> Callable[[Any], int | None]:
    def validate(value: Any) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not minimum <= value <= maximum:
            raise PaperFormatContractError(
                "preset_numeric_value_invalid", "试卷数值超出允许范围。"
            )
        return value

    return validate


def _scoring_rules(value: Any) -> dict[str, str | None] | None:
    if value is None:
        return None
    record = _require_mapping(
        value,
        code="preset_scoring_rules_invalid",
        message_zh="计分规则格式不正确。",
    )
    _require_exact_keys(
        record,
        {"selection_rule_zh", "partial_credit_rule_zh", "other_rule_zh"},
        code="preset_scoring_rules_invalid",
        message_zh="计分规则字段不完整。",
    )
    result: dict[str, str | None] = {}
    for key, item in record.items():
        if item is not None and (
            not isinstance(item, str) or not item.strip() or len(item) > 500
        ):
            raise PaperFormatContractError(
                "preset_scoring_rules_invalid", "计分规则内容不正确。"
            )
        result[key] = item.strip() if isinstance(item, str) else None
    if not any(result.values()):
        raise PaperFormatContractError(
            "preset_scoring_rules_invalid", "已填写的计分规则不能全部为空。"
        )
    return result


def _numbering_mode(value: Any) -> str | None:
    if value is None:
        return None
    if value not in _NUMBERING_MODES:
        raise PaperFormatContractError(
            "preset_numbering_invalid", "题号方式必须逐卷选择一种明确模式。"
        )
    return str(value)


def _positive_number(value: Any, *, label_zh: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise PaperFormatContractError(
            "preset_layout_invalid", f"“{label_zh}”超出允许范围。"
        )
    return float(value)


def _font_name(value: Any, *, label_zh: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise PaperFormatContractError(
            "preset_layout_invalid", f"“{label_zh}”字体名称不正确。"
        )
    return value.strip()


def _normalize_page_layout(value: Any) -> dict[str, Any]:
    layout = _require_mapping(
        value,
        code="preset_layout_invalid",
        message_zh="页面版式预设不正确。",
    )
    _require_exact_keys(
        layout,
        {
            "page_size",
            "orientation",
            "margins_mm",
            "title",
            "body",
            "theme_heading",
            "minimum_raster_dpi",
        },
        code="preset_layout_invalid",
        message_zh="页面版式预设字段不完整。",
    )
    if layout.get("page_size") != "A4" or layout.get("orientation") != "portrait":
        raise PaperFormatContractError(
            "preset_layout_invalid", "第一版渲染合同只支持 A4 纵向。"
        )
    margins = _require_mapping(
        layout.get("margins_mm"),
        code="preset_layout_invalid",
        message_zh="页边距设置不正确。",
    )
    _require_exact_keys(
        margins,
        {"top", "bottom", "left", "right"},
        code="preset_layout_invalid",
        message_zh="页边距字段不完整。",
    )
    normalized_margins = {
        key: _positive_number(value, label_zh=f"{key} 页边距", minimum=5, maximum=50)
        for key, value in margins.items()
    }
    title = _require_mapping(
        layout.get("title"),
        code="preset_layout_invalid",
        message_zh="标题样式不正确。",
    )
    _require_exact_keys(
        title,
        {"cjk_font", "size_pt", "bold", "alignment"},
        code="preset_layout_invalid",
        message_zh="标题样式字段不完整。",
    )
    if title.get("bold") not in {True, False} or title.get("alignment") not in {
        "left",
        "center",
        "right",
    }:
        raise PaperFormatContractError("preset_layout_invalid", "标题样式设置不正确。")
    normalized_title = {
        "cjk_font": _font_name(title.get("cjk_font"), label_zh="标题"),
        "size_pt": _positive_number(
            title.get("size_pt"), label_zh="标题字号", minimum=10, maximum=36
        ),
        "bold": title["bold"],
        "alignment": title["alignment"],
    }
    body = _require_mapping(
        layout.get("body"),
        code="preset_layout_invalid",
        message_zh="正文样式不正确。",
    )
    _require_exact_keys(
        body,
        {"cjk_font", "latin_font", "size_pt", "line_spacing"},
        code="preset_layout_invalid",
        message_zh="正文样式字段不完整。",
    )
    normalized_body = {
        "cjk_font": _font_name(body.get("cjk_font"), label_zh="正文中文"),
        "latin_font": _font_name(body.get("latin_font"), label_zh="正文西文"),
        "size_pt": _positive_number(
            body.get("size_pt"), label_zh="正文字号", minimum=8, maximum=18
        ),
        "line_spacing": _positive_number(
            body.get("line_spacing"), label_zh="正文行距", minimum=1, maximum=2.5
        ),
    }
    theme_heading = _require_mapping(
        layout.get("theme_heading"),
        code="preset_layout_invalid",
        message_zh="主题标题样式不正确。",
    )
    _require_exact_keys(
        theme_heading,
        {"cjk_font", "size_pt", "bold"},
        code="preset_layout_invalid",
        message_zh="主题标题样式字段不完整。",
    )
    if theme_heading.get("bold") not in {True, False}:
        raise PaperFormatContractError("preset_layout_invalid", "主题标题加粗设置不正确。")
    normalized_theme_heading = {
        "cjk_font": _font_name(theme_heading.get("cjk_font"), label_zh="主题标题"),
        "size_pt": _positive_number(
            theme_heading.get("size_pt"),
            label_zh="主题标题字号",
            minimum=9,
            maximum=24,
        ),
        "bold": theme_heading["bold"],
    }
    dpi = layout.get("minimum_raster_dpi")
    if type(dpi) is not int or not 150 <= dpi <= 2400:
        raise PaperFormatContractError("preset_layout_invalid", "位图最低分辨率不正确。")
    return {
        "page_size": "A4",
        "orientation": "portrait",
        "margins_mm": normalized_margins,
        "title": normalized_title,
        "body": normalized_body,
        "theme_heading": normalized_theme_heading,
        "minimum_raster_dpi": dpi,
    }


def _normalize_student_version(value: Any) -> dict[str, Any]:
    student = _require_mapping(
        value,
        code="preset_student_version_invalid",
        message_zh="学生版预设不正确。",
    )
    _require_exact_keys(
        student,
        {
            "identity_fields_zh",
            "show_duration",
            "show_total_score",
            "show_item_scores",
            "answer_space",
            "header",
            "footer",
            "forbidden_visible_content",
        },
        code="preset_student_version_invalid",
        message_zh="学生版预设字段不完整。",
    )
    if student.get("identity_fields_zh") != ["姓名", "班级"] or any(
        student.get(key) is not True
        for key in ("show_duration", "show_total_score", "show_item_scores")
    ):
        raise PaperFormatContractError(
            "preset_student_version_invalid", "学生版必须保留姓名/班级、时间、总分和分题分值。"
        )
    answer_space = _require_mapping(
        student.get("answer_space"),
        code="preset_student_version_invalid",
        message_zh="答题空间预设不正确。",
    )
    _require_exact_keys(
        answer_space,
        {"mode", "fallback_lines", "editable"},
        code="preset_student_version_invalid",
        message_zh="答题空间预设字段不完整。",
    )
    if (
        answer_space.get("mode") != "per_atomic_or_teacher_editable_fallback"
        or answer_space.get("editable") is not True
        or type(answer_space.get("fallback_lines")) is not int
        or not 0 <= answer_space["fallback_lines"] <= 30
    ):
        raise PaperFormatContractError("preset_student_version_invalid", "答题空间预设不正确。")
    header = _require_mapping(
        student.get("header"),
        code="preset_student_version_invalid",
        message_zh="页眉预设不正确。",
    )
    footer = _require_mapping(
        student.get("footer"),
        code="preset_student_version_invalid",
        message_zh="页脚预设不正确。",
    )
    if dict(header) != {"enabled": True, "content": "paper_title"} or dict(
        footer
    ) != {"enabled": True, "pattern_zh": "第 x 页 / 共 y 页"}:
        raise PaperFormatContractError(
            "preset_student_version_invalid", "页眉页脚必须绑定试卷标题和当前页/总页数。"
        )
    forbidden = student.get("forbidden_visible_content")
    required_forbidden = {
        "reference_answer",
        "explanation",
        "pitfalls",
        "source_label",
        "internal_tags",
    }
    if (
        not isinstance(forbidden, list)
        or len(forbidden) != len(set(forbidden))
        or not required_forbidden.issubset(set(forbidden))
        or any(not isinstance(item, str) or not item for item in forbidden)
    ):
        raise PaperFormatContractError(
            "preset_student_version_invalid", "学生版禁止显示内容列表不完整。"
        )
    normalized = deepcopy(dict(student))
    normalized["answer_space"] = dict(answer_space)
    normalized["header"] = dict(header)
    normalized["footer"] = dict(footer)
    normalized["forbidden_visible_content"] = list(forbidden)
    return normalized


def _normalize_teacher_version(value: Any) -> dict[str, Any]:
    teacher = _require_mapping(
        value,
        code="preset_teacher_version_invalid",
        message_zh="教师版预设不正确。",
    )
    _require_exact_keys(
        teacher,
        {
            "uses_same_blueprint",
            "include_source_reference_answer",
            "include_explanation",
            "include_pitfalls",
            "include_source_label",
            "allowed_answer_labels",
        },
        code="preset_teacher_version_invalid",
        message_zh="教师版预设字段不完整。",
    )
    if any(
        teacher.get(key) is not True
        for key in (
            "uses_same_blueprint",
            "include_source_reference_answer",
            "include_explanation",
            "include_pitfalls",
            "include_source_label",
        )
    ) or set(teacher.get("allowed_answer_labels") or []) != _ANSWER_LABELS:
        raise PaperFormatContractError(
            "preset_teacher_version_invalid", "教师版必须使用同一题序并保留答案、解析/易错点和来源标签。"
        )
    normalized = deepcopy(dict(teacher))
    normalized["allowed_answer_labels"] = list(teacher["allowed_answer_labels"])
    return normalized


def _normalize_rendering(value: Any) -> dict[str, Any]:
    rendering = _require_mapping(
        value,
        code="preset_rendering_invalid",
        message_zh="DOCX/PDF 渲染预设不正确。",
    )
    _require_exact_keys(
        rendering,
        {
            "docx_is_source_of_truth",
            "artifacts",
            "inspect_every_rendered_page",
            "rerender_after_every_change",
            "allowed_layout_defects",
            "required_parity",
            "secrets_forbidden_in_artifacts",
            "publication_allowed",
        },
        code="preset_rendering_invalid",
        message_zh="DOCX/PDF 渲染预设字段不完整。",
    )
    if (
        rendering.get("docx_is_source_of_truth") is not True
        or rendering.get("inspect_every_rendered_page") is not True
        or rendering.get("rerender_after_every_change") is not True
        or rendering.get("allowed_layout_defects") != 0
        or rendering.get("secrets_forbidden_in_artifacts") is not True
        or rendering.get("publication_allowed") is not False
    ):
        raise PaperFormatContractError(
            "preset_rendering_invalid", "渲染、逐页检查、密钥排除或发布边界被放宽。"
        )
    artifacts = rendering.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        raise PaperFormatContractError("preset_rendering_invalid", "必须生成四个 DOCX/PDF 文件。")
    normalized_artifacts: dict[str, dict[str, Any]] = {}
    for artifact_value in artifacts:
        artifact = _require_mapping(
            artifact_value,
            code="preset_rendering_invalid",
            message_zh="渲染文件定义不正确。",
        )
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id in normalized_artifacts:
            raise PaperFormatContractError("preset_rendering_invalid", "渲染文件标识重复或无效。")
        normalized_artifacts[artifact_id] = dict(artifact)
    expected_artifacts = {
        "student_docx": {"artifact_id": "student_docx", "audience": "student", "format": "docx"},
        "student_pdf": {
            "artifact_id": "student_pdf",
            "audience": "student",
            "format": "pdf",
            "source_artifact_id": "student_docx",
        },
        "teacher_docx": {"artifact_id": "teacher_docx", "audience": "teacher", "format": "docx"},
        "teacher_pdf": {
            "artifact_id": "teacher_pdf",
            "audience": "teacher",
            "format": "pdf",
            "source_artifact_id": "teacher_docx",
        },
    }
    if normalized_artifacts != expected_artifacts:
        raise PaperFormatContractError(
            "preset_rendering_invalid", "PDF 必须由对应的学生版或教师版 DOCX 生成。"
        )
    expected_parity = {
        "blueprint_digest",
        "content_version_id",
        "theme_order",
        "question_anchor_digest",
        "figure_count",
        "page_count_within_audience",
    }
    parity = rendering.get("required_parity")
    if not isinstance(parity, list) or set(parity) != expected_parity or len(parity) != len(
        expected_parity
    ):
        raise PaperFormatContractError("preset_rendering_invalid", "DOCX/PDF 一致性检查项不完整。")
    normalized = deepcopy(dict(rendering))
    normalized["artifacts"] = [expected_artifacts[item["artifact_id"]] for item in artifacts]
    normalized["required_parity"] = list(parity)
    return normalized


def validate_paper_format_preset(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one editable paper-format preset."""

    record = _require_mapping(
        value,
        code="preset_invalid",
        message_zh="试卷格式预设不是有效对象。",
    )
    _require_exact_keys(
        record,
        {
            "schema_version",
            "contract_kind",
            "preset_id",
            "preset_version",
            "display_name_zh",
            "template_status",
            "claim_boundary_zh",
            "structure",
            "per_paper",
            "page_layout",
            "student_version",
            "teacher_version",
            "rendering",
        },
        code="preset_invalid",
        message_zh="试卷格式预设字段不完整或含未知字段。",
    )
    if record.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION or record.get(
        "contract_kind"
    ) != PRESET_KIND:
        raise PaperFormatContractError("preset_version_invalid", "试卷格式预设版本不兼容。")
    preset_id = _safe_id(
        record.get("preset_id"), code="preset_id_invalid", message_zh="预设标识不正确。"
    )
    preset_version = record.get("preset_version")
    if not isinstance(preset_version, str) or not _VERSION.fullmatch(preset_version):
        raise PaperFormatContractError("preset_version_invalid", "预设版本号不正确。")
    display_name = _text(
        record.get("display_name_zh"),
        code="preset_name_invalid",
        message_zh="预设中文名称不能为空。",
        limit=120,
    )
    claim = _text(
        record.get("claim_boundary_zh"),
        code="preset_claim_invalid",
        message_zh="预设必须写明项目模板边界。",
        limit=800,
    )
    if record.get("template_status") not in {
        "editable_project_template",
        "verified_for_exact_paper",
        "blocked_pending_review",
    }:
        raise PaperFormatContractError("preset_status_invalid", "预设状态不正确。")

    structure = _require_mapping(
        record.get("structure"),
        code="preset_structure_invalid",
        message_zh="试卷层级结构不正确。",
    )
    _require_exact_keys(
        structure,
        {
            "top_level_unit",
            "hierarchy",
            "standalone_choice_section_allowed",
            "embedded_response_forms",
            "shared_material_policy",
            "dependency_policy",
            "theme_numbering",
            "subquestion_numbering",
        },
        code="preset_structure_invalid",
        message_zh="试卷层级结构字段不完整。",
    )
    if (
        structure.get("top_level_unit") != TOP_LEVEL_UNIT
        or tuple(structure.get("hierarchy") or ()) != HIERARCHY
        or structure.get("standalone_choice_section_allowed") is not False
    ):
        raise PaperFormatContractError(
            "standalone_choice_section_forbidden",
            "一级结构只能是完整主题大题，不能生成独立选择题板块。",
        )
    forms = structure.get("embedded_response_forms")
    if (
        not isinstance(forms, list)
        or not forms
        or len(forms) != len(set(forms))
        or any(not isinstance(item, str) or not item for item in forms)
        or any("section" in item.casefold() for item in forms)
    ):
        raise PaperFormatContractError(
            "embedded_response_forms_invalid", "主题内作答形式列表不正确。"
        )
    if structure.get("shared_material_policy") != "render_once_per_theme_bundle":
        raise PaperFormatContractError(
            "shared_material_policy_invalid", "共享材料必须在每个主题包中只呈现一次。"
        )
    if structure.get("dependency_policy") != "explicit_transitive_closure":
        raise PaperFormatContractError(
            "dependency_policy_invalid", "依赖题必须使用显式传递闭包。"
        )
    if structure.get("theme_numbering") != "chinese_sequential":
        raise PaperFormatContractError("theme_numbering_invalid", "主题题号规则不正确。")
    numbering = _normalize_editable_field(
        structure.get("subquestion_numbering"),
        field_name="小题编号方式",
        validator=_numbering_mode,
    )

    per_paper = _require_mapping(
        record.get("per_paper"),
        code="preset_per_paper_invalid",
        message_zh="逐卷参数格式不正确。",
    )
    _require_exact_keys(
        per_paper,
        {"theme_count", "total_score", "duration_minutes", "scoring_rules"},
        code="preset_per_paper_invalid",
        message_zh="逐卷参数字段不完整。",
    )
    normalized_per_paper = {
        "theme_count": _normalize_editable_field(
            per_paper.get("theme_count"),
            field_name="主题数",
            validator=_nullable_int(1, 20),
        ),
        "total_score": _normalize_editable_field(
            per_paper.get("total_score"),
            field_name="总分",
            validator=_nullable_int(1, 500),
        ),
        "duration_minutes": _normalize_editable_field(
            per_paper.get("duration_minutes"),
            field_name="考试时间",
            validator=_nullable_int(1, 600),
        ),
        "scoring_rules": _normalize_editable_field(
            per_paper.get("scoring_rules"),
            field_name="计分规则",
            validator=_scoring_rules,
        ),
    }

    normalized = deepcopy(dict(record))
    normalized["preset_id"] = preset_id
    normalized["preset_version"] = preset_version
    normalized["display_name_zh"] = display_name
    normalized["claim_boundary_zh"] = claim
    normalized["structure"] = deepcopy(dict(structure))
    normalized["structure"]["subquestion_numbering"] = numbering
    normalized["per_paper"] = normalized_per_paper
    normalized["page_layout"] = _normalize_page_layout(record.get("page_layout"))
    normalized["student_version"] = _normalize_student_version(
        record.get("student_version")
    )
    normalized["teacher_version"] = _normalize_teacher_version(
        record.get("teacher_version")
    )
    normalized["rendering"] = _normalize_rendering(record.get("rendering"))

    if normalized["template_status"] == "verified_for_exact_paper":
        exact_ids = {
            field["verification"]["verified_for_paper_id"]
            for field in [numbering, *normalized_per_paper.values()]
            if field["verification"]["status"] == "verified_for_exact_paper"
        }
        if (
            any(
                field["verification"]["status"] != "verified_for_exact_paper"
                for field in [numbering, *normalized_per_paper.values()]
            )
            or len(exact_ids) != 1
        ):
            raise PaperFormatContractError(
                "preset_exact_verification_incomplete",
                "整份预设标为已核验时，主题数、分值、时长、计分和编号方式必须绑定同一试卷。",
            )
    return normalized


def _normalize_snapshot(
    value: Mapping[str, Any], *, scope: str, expected_snapshot_id: str
) -> Mapping[str, Any]:
    wrapper = _require_mapping(
        value,
        code="theme_snapshot_invalid",
        message_zh="主题题库快照格式不正确。",
    )
    if wrapper.get("data_snapshot_id") != expected_snapshot_id:
        raise PaperFormatContractError(
            "theme_snapshot_stale", "题篮所用题库版本已经变化，请刷新后重新组卷。", 409
        )
    if wrapper.get("scope") != scope:
        raise PaperFormatContractError("theme_scope_mismatch", "题库范围与题篮条目不一致。")
    catalog = _require_mapping(
        wrapper.get("catalog"),
        code="theme_snapshot_invalid",
        message_zh="主题题库快照缺少完整主题目录。",
    )
    if catalog.get("scope") != scope or not isinstance(catalog.get("papers"), list):
        raise PaperFormatContractError("theme_snapshot_invalid", "完整主题目录范围不正确。")
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
        raise PaperFormatContractError(
            "theme_snapshot_integrity_blocked", "主题目录未通过完整性检查，不能组卷。", 409
        )
    return catalog


def _row_order(row: Mapping[str, Any]) -> tuple[int, int]:
    printed = row.get("printed_sequence")
    atomic = row.get("atomic_sequence_in_printed")
    if type(printed) is not int or printed < 1 or type(atomic) is not int or atomic < 1:
        raise PaperFormatContractError(
            "source_order_unknown", "题目卷内顺序待补，不能生成确定性试卷。", 409
        )
    return printed, atomic


def _validate_theme_graph(group: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    rows = group.get("atomic_chain")
    if not isinstance(rows, list) or not rows:
        raise PaperFormatContractError("theme_atomic_chain_invalid", "主题题链为空或格式不正确。")
    ids: list[str] = []
    row_by_id: dict[str, Mapping[str, Any]] = {}
    positions: dict[str, tuple[int, int]] = {}
    coordinate_owner: dict[tuple[int, int], str] = {}
    printed_sequence_owner: dict[int, str] = {}
    blockers: list[str] = []
    for index, row_value in enumerate(rows):
        row = _require_mapping(
            row_value,
            code="theme_atomic_chain_invalid",
            message_zh="主题内题目格式不正确。",
        )
        atomic_id = _safe_id(
            row.get("atomic_part_id"),
            code="atomic_id_invalid",
            message_zh="主题内最小作答单元标识不正确。",
        )
        if atomic_id in row_by_id:
            raise PaperFormatContractError("duplicate_atomic_part", "主题内出现重复题目。")
        printed_order, atomic_order = _row_order(row)
        coordinate = (printed_order, atomic_order)
        if coordinate in coordinate_owner:
            raise PaperFormatContractError(
                "source_order_duplicate", "两个作答单元使用了相同卷内顺序，不能确定题序。", 409
            )
        printed_id = _safe_id(
            row.get("printed_question_id"),
            code="printed_question_id_invalid",
            message_zh="卷面小题标识不正确。",
        )
        owner = printed_sequence_owner.setdefault(printed_order, printed_id)
        if owner != printed_id:
            raise PaperFormatContractError(
                "source_order_conflict", "不同卷面小题使用了相同来源顺序，不能确定题序。", 409
            )
        ids.append(atomic_id)
        row_by_id[atomic_id] = row
        positions[atomic_id] = coordinate
        coordinate_owner[coordinate] = atomic_id

    ids.sort(key=positions.__getitem__)

    for atomic_id, row in row_by_id.items():
        dependency = _require_mapping(
            row.get("dependency"),
            code="dependency_invalid",
            message_zh="题目依赖关系格式不正确。",
        )
        kind = dependency.get("kind")
        prior = dependency.get("prior_atomic_part_ids")
        if (
            kind not in _DEPENDENCY_KINDS
            or not isinstance(prior, list)
            or len(prior) != len(set(prior))
            or any(not isinstance(item, str) for item in prior)
        ):
            raise PaperFormatContractError("dependency_invalid", "题目依赖关系格式不正确。")
        aliases = row.get("alias_units")
        if aliases is None:
            aliases = []
        if not isinstance(aliases, list) or any(
            not isinstance(alias, Mapping) for alias in aliases
        ):
            raise PaperFormatContractError("alias_units_invalid", "题目分项信息格式不正确。")
        if kind == "per_alias_unit" or aliases:
            blockers.append(f"alias_dependency:{atomic_id}")
        if kind == "blocked_pending_review" or str(dependency.get("status", "")).startswith(
            "blocked"
        ):
            blockers.append(f"dependency_pending:{atomic_id}")
        for prior_id in prior:
            if prior_id not in row_by_id:
                raise PaperFormatContractError(
                    "dependency_cross_theme_or_missing",
                    "前序依赖不在同一完整主题中，不能自动补题。",
                    409,
                )
            if positions[prior_id] >= positions[atomic_id]:
                raise PaperFormatContractError(
                    "dependency_not_prior",
                    "前序依赖指向本题或后问，不能组卷。",
                    409,
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(atomic_id: str) -> None:
        if atomic_id in visiting:
            raise PaperFormatContractError("dependency_cycle", "题目依赖形成循环，不能组卷。", 409)
        if atomic_id in visited:
            return
        visiting.add(atomic_id)
        for prior_id in row_by_id[atomic_id]["dependency"]["prior_atomic_part_ids"]:
            visit(prior_id)
        visiting.remove(atomic_id)
        visited.add(atomic_id)

    for atomic_id in ids:
        visit(atomic_id)
    return [row_by_id[atomic_id] for atomic_id in ids], sorted(set(blockers))


def _dependency_closure(
    rows: Sequence[Mapping[str, Any]], target_ids: Sequence[str]
) -> tuple[list[str], list[str]]:
    row_by_id = {row["atomic_part_id"]: row for row in rows}
    included: set[str] = set()
    requested = set(target_ids)

    def visit(atomic_id: str) -> None:
        if atomic_id in included:
            return
        row = row_by_id.get(atomic_id)
        if row is None:
            raise PaperFormatContractError(
                "dependency_cross_theme_or_missing", "依赖闭包缺少题目，不能组卷。", 409
            )
        for prior_id in row["dependency"]["prior_atomic_part_ids"]:
            visit(prior_id)
        included.add(atomic_id)

    for target in target_ids:
        visit(target)
    ordered = [row["atomic_part_id"] for row in rows if row["atomic_part_id"] in included]
    auto_added = [atomic_id for atomic_id in ordered if atomic_id not in requested]
    return ordered, auto_added


def _theme_indexes(catalog: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    result: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for paper_entry in catalog["papers"]:
        paper_entry = _require_mapping(
            paper_entry,
            code="theme_snapshot_invalid",
            message_zh="主题目录中的试卷记录不正确。",
        )
        paper = _require_mapping(
            paper_entry.get("paper"),
            code="theme_snapshot_invalid",
            message_zh="主题目录缺少试卷信息。",
        )
        groups = paper_entry.get("theme_groups")
        if not isinstance(groups, list):
            raise PaperFormatContractError("theme_snapshot_invalid", "主题目录缺少主题列表。")
        for group_value in groups:
            group = _require_mapping(
                group_value,
                code="theme_snapshot_invalid",
                message_zh="主题目录中的主题记录不正确。",
            )
            theme = _require_mapping(
                group.get("theme"),
                code="theme_snapshot_invalid",
                message_zh="主题目录缺少主题信息。",
            )
            theme_id = _safe_id(
                theme.get("id"), code="theme_id_invalid", message_zh="主题标识不正确。"
            )
            if theme_id in result:
                raise PaperFormatContractError("duplicate_theme", "主题目录出现重复主题。")
            result[theme_id] = (paper, group)
    return result


def _normalize_selection(value: Any, expected_snapshot_id: str) -> dict[str, Any]:
    record = _require_mapping(
        value,
        code="selection_invalid",
        message_zh="题篮条目格式不正确。",
    )
    allowed = {
        "scope",
        "selection_unit",
        "theme_id",
        "target_atomic_id",
        "expected_data_snapshot_id",
    }
    if set(record) - allowed or not allowed.issuperset(record):
        raise PaperFormatContractError("selection_invalid", "题篮条目含未知字段。")
    scope = _safe_id(
        record.get("scope"), code="selection_scope_invalid", message_zh="题库范围不正确。"
    )
    unit = record.get("selection_unit")
    if unit not in _SELECTION_UNITS:
        raise PaperFormatContractError("selection_unit_invalid", "加入方式不正确。")
    theme_id = _safe_id(
        record.get("theme_id"), code="theme_id_invalid", message_zh="主题标识不正确。"
    )
    target = record.get("target_atomic_id")
    if unit == "theme":
        if target not in {None, ""}:
            raise PaperFormatContractError("selection_invalid", "加入整主题时不能另指定单题。")
        target = None
    else:
        target = _safe_id(
            target, code="atomic_id_invalid", message_zh="所选小题标识不正确。"
        )
    if record.get("expected_data_snapshot_id") != expected_snapshot_id:
        raise PaperFormatContractError(
            "theme_snapshot_stale", "题篮条目来自旧题库版本，请刷新后重试。", 409
        )
    return {
        "scope": scope,
        "selection_unit": unit,
        "theme_id": theme_id,
        "target_atomic_id": target,
        "expected_data_snapshot_id": expected_snapshot_id,
    }


def _normalize_theme_title(value: Any) -> str:
    title = _text(
        value,
        code="theme_title_invalid",
        message_zh="完整主题缺少可用标题，不能生成卷面一级标题。",
        limit=300,
    )
    assert title is not None
    normalized = re.sub(r"[\s：:、，,。．.（）()\-—_]+", "", title)
    choice_heading = any(
        forbidden in normalized for forbidden in _STANDALONE_CHOICE_TITLES
    ) and (
        normalized in _STANDALONE_CHOICE_TITLES
        or any(marker in normalized for marker in ("部分", "板块", "独立"))
        or any(normalized.endswith(forbidden) for forbidden in _STANDALONE_CHOICE_TITLES)
    )
    if choice_heading:
        raise PaperFormatContractError(
            "standalone_choice_heading_forbidden",
            "一级标题不能是选择题板块；选择、填空、简答和计算必须嵌入完整主题。",
            409,
        )
    return title


def _material_rows(
    *, scope: str, paper_id: str, theme_id: str, group: Mapping[str, Any]
) -> list[dict[str, Any]]:
    shared = group.get("shared_context")
    if shared is None:
        return []
    shared = _require_mapping(
        shared,
        code="shared_material_invalid",
        message_zh="主题共享材料格式不正确。",
    )
    materials = shared.get("materials")
    if not isinstance(materials, list):
        raise PaperFormatContractError("shared_material_invalid", "主题共享材料列表不正确。")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for material_value in materials:
        material = _require_mapping(
            material_value,
            code="shared_material_invalid",
            message_zh="共享材料记录不正确。",
        )
        material_id = _safe_id(
            material.get("material_id"),
            code="shared_material_invalid",
            message_zh="共享材料标识不正确。",
        )
        if material_id in seen:
            raise PaperFormatContractError("shared_material_duplicate", "同一主题重复引用共享材料。")
        seen.add(material_id)
        page = material.get("page")
        if type(page) is not int or page < 1:
            raise PaperFormatContractError(
                "shared_material_page_invalid",
                "共享材料必须保留可核验的来源页码。",
            )
        render_once_key = _sha256(
            {
                "scope": scope,
                "paper_id": paper_id,
                "theme_id": theme_id,
                "material_id": material_id,
            }
        )
        result.append(
            {
                "material_id": material_id,
                "material_type": material.get("type"),
                "page": page,
                "preview_allowed": material.get("preview_allowed") is True,
                "used_by_atomic_count": material.get("used_by_atomic_count"),
                "render_once_key": render_once_key,
            }
        )
    # Source-page order is the canonical serialized order.  Python's stable
    # sort preserves the catalog sequence for multiple crops on the same page.
    return sorted(result, key=lambda row: row["page"])


def _project_atomic(row: Mapping[str, Any]) -> dict[str, Any]:
    dependency = row["dependency"]
    aliases = row.get("alias_units")
    if aliases is None:
        aliases = []
    if not isinstance(aliases, list):
        raise PaperFormatContractError("alias_units_invalid", "题目分项信息格式不正确。")
    projected_aliases: list[dict[str, Any]] = []
    for alias_value in aliases:
        alias = _require_mapping(
            alias_value,
            code="alias_units_invalid",
            message_zh="题目分项信息格式不正确。",
        )
        alias_id = _safe_id(
            alias.get("atomic_part_id"),
            code="alias_units_invalid",
            message_zh="题目分项标识不正确。",
        )
        alias_dependency = _require_mapping(
            alias.get("dependency"),
            code="alias_units_invalid",
            message_zh="题目分项依赖格式不正确。",
        )
        alias_prior = alias_dependency.get("prior_atomic_part_ids")
        if (
            alias_dependency.get("kind") not in _DEPENDENCY_KINDS
            or not isinstance(alias_prior, list)
            or len(alias_prior) != len(set(alias_prior))
            or any(not isinstance(item, str) for item in alias_prior)
        ):
            raise PaperFormatContractError(
                "alias_units_invalid", "题目分项依赖格式不正确。"
            )
        projected_aliases.append(
            {
                "atomic_part_id": alias_id,
                "dependency": {
                    "kind": alias_dependency.get("kind"),
                    "prior_atomic_part_ids": list(alias_prior),
                    "status": alias_dependency.get("status"),
                },
            }
        )
    return {
        "atomic_part_id": row["atomic_part_id"],
        "atomic_sequence_in_printed": row["atomic_sequence_in_printed"],
        "item_type": row.get("item_type"),
        "dependency": {
            "kind": dependency.get("kind"),
            "prior_atomic_part_ids": list(dependency.get("prior_atomic_part_ids", [])),
            "status": dependency.get("status"),
        },
        "label_summary": deepcopy(row.get("label_summary")),
        "answer_status": deepcopy(row.get("answer")),
        "detail_endpoint": row.get("detail_endpoint"),
        "alias_units": projected_aliases,
    }


def build_assembly_blueprint(
    selections: Sequence[Mapping[str, Any]],
    *,
    theme_loader: ThemeSnapshotLoader,
    expected_data_snapshot_id: str,
    preset: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, theme-first assembly blueprint from basket selections."""

    normalized_preset = validate_paper_format_preset(preset)
    snapshot_id = _safe_id(
        expected_data_snapshot_id,
        code="snapshot_id_invalid",
        message_zh="题库版本标识不正确。",
    )
    if not isinstance(selections, Sequence) or isinstance(selections, (str, bytes)):
        raise PaperFormatContractError("selection_invalid", "题篮不是有效列表。")
    if not selections or len(selections) > 100:
        raise PaperFormatContractError("selection_invalid", "题篮必须包含 1—100 个条目。")
    normalized_selections = [
        _normalize_selection(selection, snapshot_id) for selection in selections
    ]
    scopes = list(dict.fromkeys(selection["scope"] for selection in normalized_selections))
    if len(scopes) != 1:
        raise PaperFormatContractError(
            "multiple_scopes_require_dedup_review",
            "不同题库范围暂不能混合组卷；须先接入统一题目身份去重。",
            409,
        )
    catalogs: dict[str, Mapping[str, Any]] = {}
    indexes: dict[str, dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for scope in scopes:
        wrapper = theme_loader(scope, snapshot_id)
        catalog = _normalize_snapshot(wrapper, scope=scope, expected_snapshot_id=snapshot_id)
        catalogs[scope] = catalog
        indexes[scope] = _theme_indexes(catalog)

    grouped: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for selection in normalized_selections:
        key = (selection["scope"], selection["theme_id"])
        grouped.setdefault(key, []).append(selection)

    theme_bundles: list[dict[str, Any]] = []
    blueprint_blockers: list[dict[str, str]] = []
    global_atomic_ids: set[tuple[str, str]] = set()
    global_material_keys: set[str] = set()
    for assembly_index, ((scope, theme_id), theme_selections) in enumerate(
        grouped.items(), start=1
    ):
        indexed = indexes[scope].get(theme_id)
        if indexed is None:
            raise PaperFormatContractError(
                "theme_not_found", "当前题库版本中找不到所选完整主题。", 409
            )
        paper, group = indexed
        theme = _require_mapping(
            group.get("theme"), code="theme_invalid", message_zh="主题信息不完整。"
        )
        theme_title = _normalize_theme_title(theme.get("title"))
        rows, graph_blockers = _validate_theme_graph(group)
        row_by_id = {row["atomic_part_id"]: row for row in rows}
        whole_theme = any(item["selection_unit"] == "theme" for item in theme_selections)
        requested: list[str]
        auto_added: list[str] = []
        provenance: list[dict[str, Any]] = []
        if whole_theme:
            requested = [row["atomic_part_id"] for row in rows]
            provenance.append(
                {
                    "selection_unit": "theme",
                    "requested_atomic_ids": list(requested),
                    "target_atomic_id": None,
                }
            )
            final_ids = list(requested)
        else:
            requested = []
            final_id_set: set[str] = set()
            for selection in theme_selections:
                target = selection["target_atomic_id"]
                row = row_by_id.get(target)
                if row is None:
                    raise PaperFormatContractError(
                        "atomic_not_found", "当前主题中找不到所选小题。", 409
                    )
                if selection["selection_unit"] == "atomic" and row["dependency"].get(
                    "prior_atomic_part_ids"
                ):
                    raise PaperFormatContractError(
                        "single_atomic_breaks_dependency",
                        "本题依赖前问，不能仅加入本题；请选择“本题及前序依赖”。",
                        409,
                    )
                target_final, target_auto = _dependency_closure(rows, [target])
                if selection["selection_unit"] == "atomic":
                    target_final = [target]
                    target_auto = []
                requested.append(target)
                final_id_set.update(target_final)
                auto_added.extend(target_auto)
                provenance.append(
                    {
                        "selection_unit": selection["selection_unit"],
                        "requested_atomic_ids": [target],
                        "target_atomic_id": target,
                    }
                )
            final_ids = [
                row["atomic_part_id"]
                for row in rows
                if row["atomic_part_id"] in final_id_set
            ]
        final_id_set = set(final_ids)
        selected_rows = [row for row in rows if row["atomic_part_id"] in final_id_set]
        selected_blockers = [
            blocker
            for blocker in graph_blockers
            if blocker.rsplit(":", 1)[-1] in final_id_set
        ]
        for blocker in selected_blockers:
            code = (
                "alias_dependency_requires_exact_resolution"
                if blocker.startswith("alias_dependency:")
                else "dependency_pending_review"
            )
            blueprint_blockers.append(
                {
                    "code": code,
                    "message_zh": (
                        "题目含按分项保存的依赖，必须保留分项并补齐精确装配规则。"
                        if code == "alias_dependency_requires_exact_resolution"
                        else "题目依赖仍待复核，不能进入正式导出。"
                    ),
                }
            )

        printed: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for row in selected_rows:
            printed_id = _safe_id(
                row.get("printed_question_id"),
                code="printed_question_id_invalid",
                message_zh="卷面小题标识不正确。",
            )
            sequence, _ = _row_order(row)
            current = printed.get(printed_id)
            if current is None:
                current = {
                    "printed_question_id": printed_id,
                    "source_sequence": sequence,
                    "source_number": row.get("printed_question_number"),
                    "atomic_parts": [],
                }
                printed[printed_id] = current
            elif current["source_sequence"] != sequence:
                raise PaperFormatContractError(
                    "printed_question_sequence_conflict", "同一道卷面小题出现冲突顺序。"
                )
            current["atomic_parts"].append(_project_atomic(row))

        paper_id = _safe_id(
            paper.get("id"), code="paper_id_invalid", message_zh="来源试卷标识不正确。"
        )
        materials = _material_rows(
            scope=scope, paper_id=paper_id, theme_id=theme_id, group=group
        )
        for material in materials:
            if material["render_once_key"] in global_material_keys:
                raise PaperFormatContractError(
                    "shared_material_duplicate", "共享材料在装配蓝图中出现重复呈现。"
                )
            global_material_keys.add(material["render_once_key"])
        for atomic_id in final_ids:
            global_key = (scope, atomic_id)
            if global_key in global_atomic_ids:
                raise PaperFormatContractError("duplicate_atomic_part", "题篮重复装入同一道题。")
            global_atomic_ids.add(global_key)

        shared_context = group.get("shared_context")
        context_summary = (
            shared_context.get("context_summary_zh")
            if isinstance(shared_context, Mapping)
            else None
        )
        theme_bundles.append(
            {
                "assembly_theme_number": assembly_index,
                "source": {
                    "scope": scope,
                    "paper_id": paper_id,
                    "paper_title": paper.get("title"),
                    "theme_id": theme_id,
                    "theme_title": theme_title,
                    "source_theme_sequence": theme.get("sequence"),
                },
                "shared_context_summary_zh": context_summary,
                "shared_materials": materials,
                "selection_provenance": provenance,
                "requested_atomic_ids": list(dict.fromkeys(requested)),
                "auto_added_dependency_ids": [
                    item for item in dict.fromkeys(auto_added) if item not in set(requested)
                ],
                "final_atomic_ids": final_ids,
                "printed_questions": list(printed.values()),
                "integrity": {
                    "source_order_preserved": True,
                    "dependency_closure_complete": not any(
                        blocker.startswith("dependency_pending:")
                        for blocker in selected_blockers
                    ),
                    "shared_materials_render_once": True,
                    "shared_material_order_basis": "source_page_order",
                    "alias_units_preserved_not_merged": True,
                },
            }
        )

    configured_theme_count = normalized_preset["per_paper"]["theme_count"]["value"]
    if configured_theme_count is not None and configured_theme_count != len(theme_bundles):
        blueprint_blockers.append(
            {
                "code": "theme_count_mismatch",
                "message_zh": "当前题篮主题数与格式预设中的主题数不一致。",
            }
        )
    core = {
        "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
        "contract_kind": BLUEPRINT_KIND,
        "data_snapshot_id": snapshot_id,
        "preset_ref": {
            "preset_id": normalized_preset["preset_id"],
            "preset_version": normalized_preset["preset_version"],
        },
        "top_level_unit": TOP_LEVEL_UNIT,
        "standalone_choice_section_allowed": False,
        "theme_bundles": theme_bundles,
        "counts": {
            "theme_count": len(theme_bundles),
            "printed_question_count": sum(
                len(theme["printed_questions"]) for theme in theme_bundles
            ),
            "atomic_part_count": len(global_atomic_ids),
            "shared_material_count": len(global_material_keys),
        },
        "blockers": blueprint_blockers,
        "integrity": {
            "theme_first": True,
            "no_standalone_choice_section": True,
            "source_order_preserved": True,
            "no_duplicate_atomic_parts": True,
            "shared_materials_unique": True,
            "dependency_closure_recomputed_server_side": True,
            "client_counts_trusted": False,
        },
    }
    core["blueprint_digest"] = _sha256(core)
    core["status"] = "ready_for_content_resolution" if not blueprint_blockers else "blocked"
    return core


def _normalize_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise PaperFormatContractError("content_blocks_invalid", "题面内容为空或格式不正确。")
    result: list[dict[str, Any]] = []
    for block_value in value:
        block = _require_mapping(
            block_value,
            code="content_blocks_invalid",
            message_zh="题面内容块格式不正确。",
        )
        if set(block) - {"block_type", "text_zh", "asset_ref", "alt_text_zh"}:
            raise PaperFormatContractError("content_blocks_invalid", "题面内容块含未知字段。")
        block_type = block.get("block_type")
        if block_type not in _BLOCK_TYPES:
            raise PaperFormatContractError("content_blocks_invalid", "题面内容块类型不支持。")
        text_zh = block.get("text_zh")
        asset_ref = block.get("asset_ref")
        alt_text = block.get("alt_text_zh")
        if text_zh is not None and (
            not isinstance(text_zh, str) or not text_zh.strip() or len(text_zh) > 20_000
        ):
            raise PaperFormatContractError("content_blocks_invalid", "题面文字内容不正确。")
        if asset_ref is not None and (
            not isinstance(asset_ref, str) or not asset_ref.strip() or len(asset_ref) > 500
        ):
            raise PaperFormatContractError("content_blocks_invalid", "题面资源引用不正确。")
        if alt_text is not None and (
            not isinstance(alt_text, str) or not alt_text.strip() or len(alt_text) > 1_000
        ):
            raise PaperFormatContractError("content_blocks_invalid", "题图说明不正确。")
        if text_zh is None and asset_ref is None:
            raise PaperFormatContractError("content_blocks_invalid", "题面内容块没有可呈现内容。")
        result.append(
            {
                "block_type": block_type,
                "text_zh": text_zh.strip() if isinstance(text_zh, str) else None,
                "asset_ref": asset_ref.strip() if isinstance(asset_ref, str) else None,
                "alt_text_zh": alt_text.strip() if isinstance(alt_text, str) else None,
            }
        )
    return result


def _normalize_reference_answer(value: Any) -> dict[str, Any]:
    answer = _require_mapping(
        value,
        code="reference_answer_invalid",
        message_zh="来源参考答案格式不正确。",
    )
    _require_exact_keys(
        answer,
        {"status", "text_zh", "authority_label", "independently_verified", "source_label_zh"},
        code="reference_answer_invalid",
        message_zh="来源参考答案字段不完整。",
    )
    status = answer.get("status")
    label = answer.get("authority_label")
    text_zh = answer.get("text_zh")
    source = answer.get("source_label_zh")
    if status not in _ANSWER_STATES or label not in _ANSWER_LABELS:
        raise PaperFormatContractError("reference_answer_invalid", "来源参考答案状态不正确。")
    if answer.get("independently_verified") not in {True, False}:
        raise PaperFormatContractError("reference_answer_invalid", "答案核验状态不正确。")
    if status == "aligned":
        if label == "none" or not isinstance(text_zh, str) or not text_zh.strip():
            raise PaperFormatContractError("reference_answer_invalid", "已对齐答案缺少正文或标签。")
        if not isinstance(source, str) or not source.strip():
            raise PaperFormatContractError("reference_answer_invalid", "已对齐答案缺少来源标签。")
    else:
        if text_zh is not None or label != "none":
            raise PaperFormatContractError(
                "reference_answer_invalid", "未对齐或缺失答案不能携带答案正文。"
            )
    return {
        "status": status,
        "text_zh": text_zh.strip() if isinstance(text_zh, str) else None,
        "authority_label": label,
        "independently_verified": answer["independently_verified"],
        "source_label_zh": source.strip() if isinstance(source, str) else None,
    }


def _normalize_atomic_content(value: Any, fallback_lines: int) -> dict[str, Any]:
    record = _require_mapping(
        value,
        code="atomic_content_invalid",
        message_zh="小题内容解析结果不正确。",
    )
    _require_exact_keys(
        record,
        {
            "question_blocks",
            "score",
            "answer_space_lines",
            "reference_answer",
            "explanation_zh",
            "explanation_label",
            "pitfalls_zh",
            "source_label_zh",
        } | ({"answer_space_explicit"} if "answer_space_explicit" in record else set())
        | ({"supplemental_answer"} if "supplemental_answer" in record else set()),
        code="atomic_content_invalid",
        message_zh="小题内容字段不完整。",
    )
    score = record.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 < score <= 100:
        raise PaperFormatContractError("atomic_score_invalid", "小题分值不正确。")
    lines = record.get("answer_space_lines")
    explicit_space = record.get("answer_space_explicit", False)
    if type(explicit_space) is not bool:
        raise PaperFormatContractError("answer_space_invalid", "答题空间设置来源不正确。")
    if lines is None:
        lines = fallback_lines
    if type(lines) is not int or not 0 <= lines <= 30:
        raise PaperFormatContractError("answer_space_invalid", "答题空间行数不正确。")
    explanation = record.get("explanation_zh")
    if explanation is not None and (
        not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 20_000
    ):
        raise PaperFormatContractError("explanation_invalid", "解析内容不正确。")
    explanation_label = record.get("explanation_label")
    if explanation_label not in _EXPLANATION_LABELS:
        raise PaperFormatContractError("explanation_invalid", "解析来源标签不正确。")
    if explanation is None and explanation_label != "none":
        raise PaperFormatContractError("explanation_invalid", "解析标签与解析正文不一致。")
    pitfalls = record.get("pitfalls_zh")
    if (
        not isinstance(pitfalls, list)
        or any(not isinstance(item, str) or not item.strip() or len(item) > 1_000 for item in pitfalls)
    ):
        raise PaperFormatContractError("pitfalls_invalid", "易错点列表不正确。")
    source_label = _text(
        record.get("source_label_zh"),
        code="source_label_invalid",
        message_zh="教师版缺少来源标签。",
        limit=1_000,
    )
    supplement = _normalize_supplement(record)
    return {
        "question_blocks": _normalize_blocks(record.get("question_blocks")),
        "score": float(score),
        "answer_space_lines": lines,
        "answer_space_explicit": explicit_space,
        "reference_answer": _normalize_reference_answer(record.get("reference_answer")),
        "explanation_zh": explanation.strip() if isinstance(explanation, str) else None,
        "explanation_label": explanation_label,
        "pitfalls_zh": [item.strip() for item in pitfalls],
        "source_label_zh": source_label,
        **({"supplemental_answer": supplement} if supplement else {}),
    }


def _normalize_supplement(record: Mapping[str, Any]) -> dict[str, Any] | None:
    if "supplemental_answer" not in record:
        return None
    try:
        supplement = validate_supplemental_answer(record["supplemental_answer"])
    except ValueError as exc:
        raise PaperFormatContractError("supplemental_answer_invalid", str(exc)) from exc
    source = record.get("reference_answer", record.get("source_reference_answer", {}))
    if not isinstance(source, Mapping) or source.get("status") != "absent":
        raise PaperFormatContractError("supplemental_answer_invalid", "补充解答不能替代已有来源答案。")
    return supplement


def _chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if not 1 <= value <= 99:
        return str(value)
    if value < 10:
        return digits[value]
    tens, ones = divmod(value, 10)
    prefix = "十" if tens == 1 else f"{digits[tens]}十"
    return prefix if ones == 0 else f"{prefix}{digits[ones]}"


def _answer_label_zh(answer: Mapping[str, Any]) -> str:
    if answer["status"] == "aligned":
        if answer["authority_label"] == "nonofficial":
            return "参考答案（非官方，未独立核验）" if not answer[
                "independently_verified"
            ] else "参考答案（非官方，已独立核验）"
        return "建议答案"
    if answer["status"] == "unaligned":
        return "答案存在，尚未逐题对齐"
    return "暂无参考答案"


def _content_version_id(
    *,
    preset: Mapping[str, Any],
    blueprint_digest: str,
    student_visible: Mapping[str, Any],
    teacher_visible: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> str:
    return f"content_{_sha256({'blueprint_digest': blueprint_digest, 'preset': preset, 'student_visible': student_visible, 'teacher_visible': teacher_visible, 'bindings': bindings})}"


def _validated_blueprint_digest(blueprint: Mapping[str, Any]) -> str:
    supplied = blueprint.get("blueprint_digest")
    if not isinstance(supplied, str) or not _SHA256.fullmatch(supplied):
        raise PaperFormatContractError("blueprint_digest_invalid", "组卷蓝图校验值不正确。")
    core = deepcopy(dict(blueprint))
    core.pop("blueprint_digest", None)
    status = core.pop("status", None)
    expected_status = (
        "ready_for_content_resolution" if not core.get("blockers") else "blocked"
    )
    if _sha256(core) != supplied or status != expected_status:
        raise PaperFormatContractError(
            "blueprint_digest_mismatch", "组卷蓝图内容已变化，请重新生成。", 409
        )
    return supplied


def build_document_plans(
    blueprint: Mapping[str, Any],
    *,
    preset: Mapping[str, Any],
    paper_metadata: Mapping[str, Any],
    content_resolver: DocumentContentResolver,
) -> dict[str, dict[str, Any]]:
    """Resolve one assembly blueprint into student and teacher document plans."""

    normalized_preset = validate_paper_format_preset(preset)
    if blueprint.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION or blueprint.get(
        "contract_kind"
    ) != BLUEPRINT_KIND:
        raise PaperFormatContractError("blueprint_invalid", "组卷蓝图版本不兼容。")
    blueprint_digest = _validated_blueprint_digest(blueprint)
    if blueprint.get("status") == "blocked" or blueprint.get("blockers"):
        raise PaperFormatContractError("blueprint_blocked", "组卷蓝图仍有阻断项，不能生成文档。", 409)
    if blueprint.get("top_level_unit") != TOP_LEVEL_UNIT or blueprint.get(
        "standalone_choice_section_allowed"
    ) is not False:
        raise PaperFormatContractError(
            "standalone_choice_section_forbidden", "文档计划只能按完整主题大题组织。"
        )
    metadata = _require_mapping(
        paper_metadata,
        code="paper_metadata_invalid",
        message_zh="试卷标题信息不正确。",
    )
    if set(metadata) - {"title_zh", "subtitle_zh", "version_label_zh"}:
        raise PaperFormatContractError("paper_metadata_invalid", "试卷标题信息含未知字段。")
    title = _text(
        metadata.get("title_zh"),
        code="paper_title_invalid",
        message_zh="请填写试卷标题。",
        limit=300,
    )
    subtitle = metadata.get("subtitle_zh")
    version_label = metadata.get("version_label_zh")
    for value, code, message in (
        (subtitle, "paper_subtitle_invalid", "试卷副标题不正确。"),
        (version_label, "paper_version_label_invalid", "试卷版本说明不正确。"),
    ):
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 300
        ):
            raise PaperFormatContractError(code, message)

    numbering_mode = normalized_preset["structure"]["subquestion_numbering"]["value"]
    if numbering_mode is None:
        raise PaperFormatContractError(
            "numbering_not_configured", "请先选择全卷连续题号或主题内重新编号。", 409
        )
    fallback_lines = normalized_preset["student_version"]["answer_space"][
        "fallback_lines"
    ]
    if type(fallback_lines) is not int or not 0 <= fallback_lines <= 30:
        raise PaperFormatContractError("answer_space_invalid", "预设答题空间不正确。")

    common_sections: list[dict[str, Any]] = []
    bindings = {
        "theme_bindings": [],
        "printed_question_bindings": [],
        "atomic_part_bindings": [],
    }
    continuous_number = 0
    question_anchor_ids: list[str] = []
    answer_anchor_ids: list[str] = []
    for bundle in blueprint.get("theme_bundles", []):
        theme_number = bundle["assembly_theme_number"]
        theme_title = bundle["source"].get("theme_title") or "主题标题待补"
        bundle_integrity = (
            bundle.get("integrity")
            if isinstance(bundle.get("integrity"), Mapping)
            else {}
        )
        shared_material_order_basis = bundle_integrity.get(
            "shared_material_order_basis"
        )
        if (
            shared_material_order_basis is not None
            and shared_material_order_basis not in _SHARED_MATERIAL_ORDER_BASES
        ):
            raise PaperFormatContractError(
                "shared_material_order_invalid", "共享材料排序依据不正确。"
            )
        shared_visible: list[dict[str, Any]] = []
        seen_material_keys: set[str] = set()
        for material in bundle.get("shared_materials", []):
            key = material["render_once_key"]
            if key in seen_material_keys:
                raise PaperFormatContractError("shared_material_duplicate", "共享材料重复呈现。")
            seen_material_keys.add(key)
            resolved = _require_mapping(
                content_resolver.resolve_shared_material(
                    {
                        **material,
                        "scope": bundle["source"]["scope"],
                        "paper_id": bundle["source"]["paper_id"],
                        "theme_id": bundle["source"]["theme_id"],
                    }
                ),
                code="shared_material_content_invalid",
                message_zh="共享材料内容解析失败。",
            )
            source_binding_fields = {
                "material_id",
                "source_crop_id",
                "source_sha256",
                "source_page",
            }
            if set(resolved) - {
                "content_blocks",
                "source_label_zh",
                *source_binding_fields,
            }:
                raise PaperFormatContractError(
                    "shared_material_content_invalid", "共享材料内容含未知字段。"
                )
            present_binding_fields = source_binding_fields.intersection(resolved)
            if present_binding_fields and present_binding_fields != source_binding_fields:
                raise PaperFormatContractError(
                    "shared_material_source_binding_invalid",
                    "共享材料来源绑定字段不完整。",
                )
            visible_material = {
                "render_once_key": key,
                "content_blocks": _normalize_blocks(resolved.get("content_blocks")),
                "source_label_zh": resolved.get("source_label_zh"),
            }
            if present_binding_fields:
                resolved_material_id = _safe_id(
                    resolved.get("material_id"),
                    code="shared_material_source_binding_invalid",
                    message_zh="共享材料来源标识不正确。",
                )
                source_crop_id = _safe_id(
                    resolved.get("source_crop_id"),
                    code="shared_material_source_binding_invalid",
                    message_zh="共享材料来源裁片标识不正确。",
                )
                source_sha256 = resolved.get("source_sha256")
                source_page = resolved.get("source_page")
                if (
                    resolved_material_id != material.get("material_id")
                    or not isinstance(source_sha256, str)
                    or not _SHA256.fullmatch(source_sha256)
                    or type(source_page) is not int
                    or source_page < 1
                    or source_page != material.get("page")
                ):
                    raise PaperFormatContractError(
                        "shared_material_source_binding_invalid",
                        "共享材料来源裁片、校验值或页码与组卷蓝图不一致。",
                    )
                visible_material.update(
                    {
                        "material_id": resolved_material_id,
                        "source_crop_id": source_crop_id,
                        "source_sha256": source_sha256,
                        "source_page": source_page,
                    }
                )
            shared_visible.append(visible_material)

        printed_visible: list[dict[str, Any]] = []
        if numbering_mode == "restart_within_each_theme":
            theme_question_number = 0
        for printed in bundle["printed_questions"]:
            if numbering_mode == "continuous_across_paper":
                continuous_number += 1
                question_number = continuous_number
            else:
                theme_question_number += 1
                question_number = theme_question_number
            q_anchor = f"Q{theme_number:02d}-{question_number:02d}"
            question_anchor_ids.append(q_anchor)
            atomic_visible: list[dict[str, Any]] = []
            printed_score = 0.0
            atomic_parts = printed["atomic_parts"]
            for atomic_index, atomic in enumerate(atomic_parts, start=1):
                reference = {
                    "scope": bundle["source"]["scope"],
                    "paper_id": bundle["source"]["paper_id"],
                    "theme_id": bundle["source"]["theme_id"],
                    "printed_question_id": printed["printed_question_id"],
                    "atomic_part_id": atomic["atomic_part_id"],
                    "detail_endpoint": atomic.get("detail_endpoint"),
                }
                content = _normalize_atomic_content(
                    content_resolver.resolve_atomic_part(reference), fallback_lines
                )
                printed_score += content["score"]
                part_label = (
                    None
                    if len(atomic_parts) == 1
                    else f"（{atomic_index}）"
                )
                answer_anchor = (
                    f"A{theme_number:02d}-{question_number:02d}"
                    if len(atomic_parts) == 1
                    else f"A{theme_number:02d}-{question_number:02d}-{atomic_index:02d}"
                )
                answer_anchor_ids.append(answer_anchor)
                atomic_visible.append(
                    {
                        "part_label_zh": part_label,
                        "question_blocks": content["question_blocks"],
                        "score": content["score"],
                        "answer_space": {
                            "mode": "ruled_lines_exact" if content["answer_space_explicit"] else "ruled_lines",
                            "lines": content["answer_space_lines"],
                        },
                        "teacher_notes": {
                            "answer_anchor_id": answer_anchor,
                            "answer_label_zh": _answer_label_zh(content["reference_answer"]),
                            "source_reference_answer": content["reference_answer"],
                            "explanation_zh": content["explanation_zh"],
                            "explanation_label": content["explanation_label"],
                            "pitfalls_zh": content["pitfalls_zh"],
                            "source_label_zh": content["source_label_zh"],
                            **({"supplemental_answer": content["supplemental_answer"],
                                "answer_label_zh": ANSWER_LABEL}
                               if "supplemental_answer" in content else {}),
                        },
                    }
                )
                bindings["atomic_part_bindings"].append(
                    {
                        "question_anchor_id": q_anchor,
                        "answer_anchor_id": answer_anchor,
                        "atomic_part_id": atomic["atomic_part_id"],
                        "printed_question_id": printed["printed_question_id"],
                        "theme_id": bundle["source"]["theme_id"],
                    }
                )
            printed_visible.append(
                {
                    "question_number": question_number,
                    "question_anchor_id": q_anchor,
                    "score": printed_score,
                    "atomic_parts": atomic_visible,
                }
            )
            bindings["printed_question_bindings"].append(
                {
                    "question_anchor_id": q_anchor,
                    "printed_question_id": printed["printed_question_id"],
                    "theme_id": bundle["source"]["theme_id"],
                }
            )
        visible_section = {
            "theme_number": theme_number,
            "heading_zh": f"{_chinese_number(theme_number)}、{theme_title}",
            "context_summary_zh": bundle.get("shared_context_summary_zh"),
            "shared_materials": shared_visible,
            "printed_questions": printed_visible,
            "theme_score": sum(item["score"] for item in printed_visible),
        }
        if shared_material_order_basis is not None:
            visible_section["shared_material_order_basis"] = (
                shared_material_order_basis
            )
        common_sections.append(visible_section)
        bindings["theme_bindings"].append(
            {
                "theme_number": theme_number,
                "theme_id": bundle["source"]["theme_id"],
                "paper_id": bundle["source"]["paper_id"],
                "scope": bundle["source"]["scope"],
            }
        )

    question_anchor_digest = _sha256(question_anchor_ids)
    answer_anchor_digest = _sha256(answer_anchor_ids)
    base_visible = {
        "paper_title_zh": title,
        "subtitle_zh": subtitle.strip() if isinstance(subtitle, str) else None,
        "version_label_zh": version_label.strip() if isinstance(version_label, str) else None,
        "identity_fields_zh": list(normalized_preset["student_version"]["identity_fields_zh"]),
        "exam_info": {
            "duration_minutes": normalized_preset["per_paper"]["duration_minutes"]["value"],
            "total_score": normalized_preset["per_paper"]["total_score"]["value"],
            "scoring_rules": deepcopy(
                normalized_preset["per_paper"]["scoring_rules"]["value"]
            ),
            "template_label_zh": (
                None
                if normalized_preset["template_status"] == "verified_for_exact_paper"
                else "项目模板"
            ),
        },
        "header": {
            "enabled": normalized_preset["student_version"]["header"]["enabled"],
            "text_zh": title,
        },
        "footer": deepcopy(normalized_preset["student_version"]["footer"]),
        "theme_sections": common_sections,
    }

    def project_visible(audience: str) -> dict[str, Any]:
        visible = deepcopy(base_visible)
        if audience == "student":
            for section in visible["theme_sections"]:
                for material in section["shared_materials"]:
                    material.pop("source_label_zh", None)
                for printed in section["printed_questions"]:
                    for atomic in printed["atomic_parts"]:
                        atomic.pop("teacher_notes", None)
        else:
            visible["paper_title_zh"] = f"{visible['paper_title_zh']}（教师版）"
            visible["identity_fields_zh"] = []
        return visible

    student_visible = project_visible("student")
    teacher_visible = project_visible("teacher")
    content_version_id = _content_version_id(
        preset=normalized_preset,
        blueprint_digest=blueprint_digest,
        student_visible=student_visible,
        teacher_visible=teacher_visible,
        bindings=bindings,
    )

    def project(audience: str, visible: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
            "contract_kind": DOCUMENT_PLAN_KIND,
            "audience": audience,
            "blueprint_digest": blueprint_digest,
            "content_version_id": content_version_id,
            "question_anchor_digest": question_anchor_digest,
            "answer_anchor_digest": answer_anchor_digest,
            "visible": deepcopy(dict(visible)),
            "bindings": deepcopy(bindings),
            "render_policy": {
                "internal_bindings_visible": False,
                "shared_materials_render_at_theme_level_only": True,
                "standalone_choice_section_allowed": False,
            },
        }

    return {
        "student": project("student", student_visible),
        "teacher": project("teacher", teacher_visible),
    }


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message_zh: str) -> None:
    checks.append({"check_id": check_id, "status": status, "message_zh": message_zh})


def _visible_atomic_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    visible = plan.get("visible")
    if not isinstance(visible, Mapping) or not isinstance(
        visible.get("theme_sections"), list
    ):
        return []
    result: list[Mapping[str, Any]] = []
    for theme in visible["theme_sections"]:
        if not isinstance(theme, Mapping) or not isinstance(
            theme.get("printed_questions"), list
        ):
            return []
        for printed in theme["printed_questions"]:
            if not isinstance(printed, Mapping) or not isinstance(
                printed.get("atomic_parts"), list
            ):
                return []
            if not all(isinstance(atomic, Mapping) for atomic in printed["atomic_parts"]):
                return []
            result.extend(printed["atomic_parts"])
    return result


def _contains_forbidden_student_keys(value: Any) -> bool:
    forbidden = {
        "teacher_notes",
        "source_reference_answer",
        "reference_answer",
        "explanation_zh",
        "pitfalls_zh",
        "source_label_zh",
        "answer_label_zh",
        "supplemental_answer",
    }
    forbidden_zh = ("答案", "解析", "易错", "来源标签", "采分点")
    if isinstance(value, Mapping):
        return any(
            key in forbidden
            or (isinstance(key, str) and any(token in key for token in forbidden_zh))
            or _contains_forbidden_student_keys(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_student_keys(item) for item in value)
    return False


def _validate_document_plan(
    plan: Mapping[str, Any],
    *,
    audience: str,
    preset: Mapping[str, Any],
) -> dict[str, Any]:
    record = _require_mapping(
        plan, code="document_plan_invalid", message_zh="文档计划格式不正确。"
    )
    _require_exact_keys(
        record,
        {
            "schema_version",
            "contract_kind",
            "audience",
            "blueprint_digest",
            "content_version_id",
            "question_anchor_digest",
            "answer_anchor_digest",
            "visible",
            "bindings",
            "render_policy",
        },
        code="document_plan_invalid",
        message_zh="文档计划字段不完整或含未知字段。",
    )
    if (
        record.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION
        or record.get("contract_kind") != DOCUMENT_PLAN_KIND
        or record.get("audience") != audience
    ):
        raise PaperFormatContractError("document_plan_invalid", "文档计划版本或受众不正确。")
    for key in ("blueprint_digest", "question_anchor_digest", "answer_anchor_digest"):
        if not isinstance(record.get(key), str) or not _SHA256.fullmatch(record[key]):
            raise PaperFormatContractError("document_plan_invalid", "文档计划校验值不正确。")
    if not isinstance(record.get("content_version_id"), str) or not re.fullmatch(
        r"content_[0-9a-f]{64}", record["content_version_id"]
    ):
        raise PaperFormatContractError("document_plan_invalid", "文档内容版本不正确。")
    expected_policy = {
        "internal_bindings_visible": False,
        "shared_materials_render_at_theme_level_only": True,
        "standalone_choice_section_allowed": False,
    }
    if record.get("render_policy") != expected_policy:
        raise PaperFormatContractError("document_plan_invalid", "文档渲染边界被放宽。")

    visible = _require_mapping(
        record.get("visible"),
        code="document_plan_invalid",
        message_zh="文档可见内容格式不正确。",
    )
    _require_exact_keys(
        visible,
        {
            "paper_title_zh",
            "subtitle_zh",
            "version_label_zh",
            "identity_fields_zh",
            "exam_info",
            "header",
            "footer",
            "theme_sections",
        },
        code="document_plan_invalid",
        message_zh="文档可见内容字段不完整或含未知字段。",
    )
    title = visible.get("paper_title_zh")
    if not isinstance(title, str) or not title.strip():
        raise PaperFormatContractError("document_plan_invalid", "试卷标题不能为空。")
    expected_identity = ["姓名", "班级"] if audience == "student" else []
    if visible.get("identity_fields_zh") != expected_identity:
        raise PaperFormatContractError("document_plan_invalid", "姓名/班级栏与版本不匹配。")
    exam_info = _require_mapping(
        visible.get("exam_info"),
        code="document_plan_invalid",
        message_zh="考试信息格式不正确。",
    )
    _require_exact_keys(
        exam_info,
        {"duration_minutes", "total_score", "scoring_rules", "template_label_zh"},
        code="document_plan_invalid",
        message_zh="考试信息字段不完整。",
    )
    header = visible.get("header")
    footer = visible.get("footer")
    if header != {"enabled": True, "text_zh": title.removesuffix("（教师版）")} or footer != {
        "enabled": True,
        "pattern_zh": "第 x 页 / 共 y 页",
    }:
        raise PaperFormatContractError("document_plan_invalid", "页眉页脚与试卷标题不一致。")

    themes = visible.get("theme_sections")
    if not isinstance(themes, list) or not themes:
        raise PaperFormatContractError("document_plan_invalid", "文档没有完整主题大题。")
    numbering_mode = preset["structure"]["subquestion_numbering"]["value"]
    question_anchors: list[str] = []
    expected_answer_anchors: list[str] = []
    material_keys: list[str] = []
    material_bindings: list[dict[str, Any]] = []
    material_order_bases: list[str | None] = []
    atomic_rows: list[Mapping[str, Any]] = []
    continuous_question = 0
    for theme_index, theme_value in enumerate(themes, start=1):
        theme = _require_mapping(
            theme_value,
            code="document_plan_invalid",
            message_zh="主题大题格式不正确。",
        )
        theme_fields = {
            "theme_number",
            "heading_zh",
            "context_summary_zh",
            "shared_materials",
            "printed_questions",
            "theme_score",
        }
        if "shared_material_order_basis" in theme:
            theme_fields.add("shared_material_order_basis")
        _require_exact_keys(
            theme,
            theme_fields,
            code="document_plan_invalid",
            message_zh="主题大题字段不完整。",
        )
        prefix = f"{_chinese_number(theme_index)}、"
        heading = theme.get("heading_zh")
        if theme.get("theme_number") != theme_index or not isinstance(
            heading, str
        ) or not heading.startswith(prefix):
            raise PaperFormatContractError("document_plan_invalid", "主题题号或标题顺序不正确。")
        _normalize_theme_title(heading[len(prefix) :])
        material_order_basis = theme.get("shared_material_order_basis")
        if (
            material_order_basis is not None
            and material_order_basis not in _SHARED_MATERIAL_ORDER_BASES
        ):
            raise PaperFormatContractError(
                "document_plan_invalid", "共享材料排序依据不正确。"
            )
        material_order_bases.append(material_order_basis)
        materials = theme.get("shared_materials")
        if not isinstance(materials, list):
            raise PaperFormatContractError("document_plan_invalid", "共享材料列表不正确。")
        base_material_fields = {"render_once_key", "content_blocks"}
        if audience == "teacher":
            base_material_fields.add("source_label_zh")
        source_binding_fields = {
            "material_id",
            "source_crop_id",
            "source_sha256",
            "source_page",
        }
        section_binding_modes: list[bool] = []
        section_source_pages: list[int] = []
        for material_value in materials:
            material = _require_mapping(
                material_value,
                code="document_plan_invalid",
                message_zh="共享材料格式不正确。",
            )
            has_source_binding = bool(source_binding_fields.intersection(material))
            material_fields = set(base_material_fields)
            if has_source_binding:
                material_fields.update(source_binding_fields)
            _require_exact_keys(
                material,
                material_fields,
                code="document_plan_invalid",
                message_zh="共享材料字段不完整或含越权内容。",
            )
            key = material.get("render_once_key")
            if not isinstance(key, str) or not _SHA256.fullmatch(key):
                raise PaperFormatContractError("document_plan_invalid", "共享材料标识不正确。")
            material_keys.append(key)
            if _normalize_blocks(material.get("content_blocks")) != material.get(
                "content_blocks"
            ):
                raise PaperFormatContractError("document_plan_invalid", "共享材料内容未规范化。")
            source_binding: dict[str, Any] = {
                "render_once_key": key,
                "material_id": None,
                "source_crop_id": None,
                "source_sha256": None,
                "source_page": None,
                "source_binding_complete": False,
            }
            if has_source_binding:
                material_id = material.get("material_id")
                source_crop_id = material.get("source_crop_id")
                source_sha256 = material.get("source_sha256")
                source_page = material.get("source_page")
                if (
                    not isinstance(material_id, str)
                    or not _SAFE_ID.fullmatch(material_id)
                    or not isinstance(source_crop_id, str)
                    or not _SAFE_ID.fullmatch(source_crop_id)
                    or not isinstance(source_sha256, str)
                    or not _SHA256.fullmatch(source_sha256)
                    or type(source_page) is not int
                    or source_page < 1
                ):
                    raise PaperFormatContractError(
                        "document_plan_invalid", "共享材料来源绑定不正确。"
                    )
                source_binding.update(
                    {
                        "material_id": material_id,
                        "source_crop_id": source_crop_id,
                        "source_sha256": source_sha256,
                        "source_page": source_page,
                        "source_binding_complete": True,
                    }
                )
                section_source_pages.append(source_page)
            section_binding_modes.append(has_source_binding)
            material_bindings.append(source_binding)
            if audience == "teacher" and (
                not isinstance(material.get("source_label_zh"), str)
                or not material["source_label_zh"].strip()
            ):
                raise PaperFormatContractError("document_plan_invalid", "教师版共享材料缺少来源标签。")
        if any(section_binding_modes) and not all(section_binding_modes):
            raise PaperFormatContractError(
                "document_plan_invalid", "同一主题的共享材料来源绑定不完整。"
            )
        if (
            material_order_basis == "source_page_order"
            and section_source_pages
            and section_source_pages != sorted(section_source_pages)
        ):
            raise PaperFormatContractError(
                "document_plan_invalid", "共享材料未按声明的来源页顺序排列。"
            )
        printed_questions = theme.get("printed_questions")
        if not isinstance(printed_questions, list) or not printed_questions:
            raise PaperFormatContractError("document_plan_invalid", "主题内没有卷面小题。")
        theme_score = 0.0
        for printed_index, printed_value in enumerate(printed_questions, start=1):
            printed = _require_mapping(
                printed_value,
                code="document_plan_invalid",
                message_zh="卷面小题格式不正确。",
            )
            _require_exact_keys(
                printed,
                {"question_number", "question_anchor_id", "score", "atomic_parts"},
                code="document_plan_invalid",
                message_zh="卷面小题字段不完整。",
            )
            continuous_question += 1
            expected_number = (
                continuous_question
                if numbering_mode == "continuous_across_paper"
                else printed_index
            )
            expected_q_anchor = f"Q{theme_index:02d}-{expected_number:02d}"
            if (
                printed.get("question_number") != expected_number
                or printed.get("question_anchor_id") != expected_q_anchor
            ):
                raise PaperFormatContractError("document_plan_invalid", "卷面题号或题目锚点不连续。")
            question_anchors.append(expected_q_anchor)
            atomics = printed.get("atomic_parts")
            if not isinstance(atomics, list) or not atomics:
                raise PaperFormatContractError("document_plan_invalid", "卷面小题没有作答单元。")
            printed_score = 0.0
            for atomic_index, atomic_value in enumerate(atomics, start=1):
                atomic = _require_mapping(
                    atomic_value,
                    code="document_plan_invalid",
                    message_zh="作答单元格式不正确。",
                )
                atomic_fields = {"part_label_zh", "question_blocks", "score", "answer_space"}
                if audience == "teacher":
                    atomic_fields.add("teacher_notes")
                _require_exact_keys(
                    atomic,
                    atomic_fields,
                    code="document_plan_invalid",
                    message_zh="作答单元字段不完整或含越权内容。",
                )
                if _normalize_blocks(atomic.get("question_blocks")) != atomic.get(
                    "question_blocks"
                ):
                    raise PaperFormatContractError("document_plan_invalid", "题面内容未规范化。")
                score = atomic.get("score")
                if not isinstance(score, (int, float)) or isinstance(score, bool) or score <= 0:
                    raise PaperFormatContractError("document_plan_invalid", "作答单元分值不正确。")
                answer_space = atomic.get("answer_space")
                if (
                    not isinstance(answer_space, Mapping)
                    or set(answer_space) != {"mode", "lines"}
                    or answer_space.get("mode") not in {"ruled_lines", "ruled_lines_exact"}
                    or type(answer_space.get("lines")) is not int
                    or not 0 <= answer_space["lines"] <= 30
                ):
                    raise PaperFormatContractError("document_plan_invalid", "作答空间不正确。")
                expected_answer_anchor = (
                    f"A{theme_index:02d}-{expected_number:02d}"
                    if len(atomics) == 1
                    else f"A{theme_index:02d}-{expected_number:02d}-{atomic_index:02d}"
                )
                expected_answer_anchors.append(expected_answer_anchor)
                if audience == "teacher":
                    notes = _require_mapping(
                        atomic.get("teacher_notes"),
                        code="document_plan_invalid",
                        message_zh="教师版答案与解析格式不正确。",
                    )
                    _require_exact_keys(
                        notes,
                        {
                            "answer_anchor_id",
                            "answer_label_zh",
                            "source_reference_answer",
                            "explanation_zh",
                            "explanation_label",
                            "pitfalls_zh",
                            "source_label_zh",
                        } | ({"supplemental_answer"} if "supplemental_answer" in notes else set()),
                        code="document_plan_invalid",
                        message_zh="教师版答案与解析字段不完整。",
                    )
                    if "supplemental_answer" in notes:
                        _normalize_supplement(notes)
                        if notes.get("answer_label_zh") != ANSWER_LABEL:
                            raise PaperFormatContractError("supplemental_answer_invalid", "补充解答必须保留非官方标签。")
                    if notes.get("answer_anchor_id") != expected_answer_anchor:
                        raise PaperFormatContractError("document_plan_invalid", "教师版答案号与题号不一致。")
                    if _normalize_reference_answer(
                        notes.get("source_reference_answer")
                    ) != notes.get("source_reference_answer"):
                        raise PaperFormatContractError("document_plan_invalid", "教师版来源答案未规范化。")
                printed_score += float(score)
                atomic_rows.append(atomic)
            if not isinstance(printed.get("score"), (int, float)) or abs(
                float(printed["score"]) - printed_score
            ) >= 1e-9:
                raise PaperFormatContractError("document_plan_invalid", "卷面小题分值汇总不一致。")
            theme_score += printed_score
        if not isinstance(theme.get("theme_score"), (int, float)) or abs(
            float(theme["theme_score"]) - theme_score
        ) >= 1e-9:
            raise PaperFormatContractError("document_plan_invalid", "主题分值汇总不一致。")

    if len(material_keys) != len(set(material_keys)):
        raise PaperFormatContractError("document_plan_invalid", "共享材料被重复呈现。")
    if len(question_anchors) != len(set(question_anchors)) or record.get(
        "question_anchor_digest"
    ) != _sha256(question_anchors):
        raise PaperFormatContractError("document_plan_invalid", "题号锚点校验失败。")

    bindings = _require_mapping(
        record.get("bindings"),
        code="document_plan_invalid",
        message_zh="题号与答案号绑定格式不正确。",
    )
    _require_exact_keys(
        bindings,
        {"theme_bindings", "printed_question_bindings", "atomic_part_bindings"},
        code="document_plan_invalid",
        message_zh="题号与答案号绑定字段不完整。",
    )
    theme_bindings = bindings.get("theme_bindings")
    printed_bindings = bindings.get("printed_question_bindings")
    atomic_bindings = bindings.get("atomic_part_bindings")
    if not all(isinstance(items, list) for items in (theme_bindings, printed_bindings, atomic_bindings)):
        raise PaperFormatContractError("document_plan_invalid", "题号与答案号绑定列表不正确。")
    if (
        len(theme_bindings) != len(themes)
        or len(printed_bindings) != len(question_anchors)
        or len(atomic_bindings) != len(atomic_rows)
    ):
        raise PaperFormatContractError("document_plan_invalid", "题号与答案号绑定数量不一致。")
    for index, binding in enumerate(theme_bindings, start=1):
        if not isinstance(binding, Mapping) or set(binding) != {
            "theme_number",
            "theme_id",
            "paper_id",
            "scope",
        } or binding.get("theme_number") != index:
            raise PaperFormatContractError("document_plan_invalid", "主题绑定顺序不正确。")
    for expected_anchor, binding in zip(question_anchors, printed_bindings, strict=True):
        if not isinstance(binding, Mapping) or set(binding) != {
            "question_anchor_id",
            "printed_question_id",
            "theme_id",
        } or binding.get("question_anchor_id") != expected_anchor:
            raise PaperFormatContractError("document_plan_invalid", "卷面小题绑定顺序不正确。")
    bound_answer_anchors: list[str] = []
    for expected_anchor, binding in zip(expected_answer_anchors, atomic_bindings, strict=True):
        if not isinstance(binding, Mapping) or set(binding) != {
            "question_anchor_id",
            "answer_anchor_id",
            "atomic_part_id",
            "printed_question_id",
            "theme_id",
        } or binding.get("answer_anchor_id") != expected_anchor:
            raise PaperFormatContractError("document_plan_invalid", "作答单元绑定顺序不正确。")
        bound_answer_anchors.append(binding["answer_anchor_id"])
    if len(bound_answer_anchors) != len(set(bound_answer_anchors)) or record.get(
        "answer_anchor_digest"
    ) != _sha256(bound_answer_anchors):
        raise PaperFormatContractError("document_plan_invalid", "答案号锚点校验失败。")
    return {
        "atomic_rows": atomic_rows,
        "material_keys": material_keys,
        "material_bindings": material_bindings,
        "material_order_bases": material_order_bases,
        "question_anchors": question_anchors,
        "answer_anchors": bound_answer_anchors,
    }


def _teacher_visible_as_student(value: Mapping[str, Any]) -> dict[str, Any]:
    visible = deepcopy(dict(value))
    title = visible.get("paper_title_zh")
    if isinstance(title, str):
        visible["paper_title_zh"] = title.removesuffix("（教师版）")
    visible["identity_fields_zh"] = ["姓名", "班级"]
    for section in visible.get("theme_sections", []):
        for material in section.get("shared_materials", []):
            material.pop("source_label_zh", None)
        for printed in section.get("printed_questions", []):
            for atomic in printed.get("atomic_parts", []):
                atomic.pop("teacher_notes", None)
    return visible


def run_export_preflight(
    *,
    preset: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    student_plan: Mapping[str, Any],
    teacher_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Check whether two document plans are ready to enter a renderer."""

    normalized_preset = validate_paper_format_preset(preset)
    checks: list[dict[str, Any]] = []
    try:
        validated_blueprint_digest = _validated_blueprint_digest(blueprint)
        blueprint_digest_ok = True
    except PaperFormatContractError:
        validated_blueprint_digest = ""
        blueprint_digest_ok = False
    _check(
        checks,
        "blueprint_digest",
        "pass" if blueprint_digest_ok else "blocked",
        "组卷蓝图与冻结校验值一致。" if blueprint_digest_ok else "组卷蓝图内容已变化。",
    )
    template_status_ok = normalized_preset["template_status"] != "blocked_pending_review"
    _check(
        checks,
        "template_review_status",
        "pass" if template_status_ok else "blocked",
        "试卷格式预设可用于本地导出。" if template_status_ok else "试卷格式预设仍待复核。",
    )
    if (
        blueprint.get("top_level_unit") == TOP_LEVEL_UNIT
        and blueprint.get("standalone_choice_section_allowed") is False
        and blueprint.get("integrity", {}).get("theme_first") is True
    ):
        _check(checks, "theme_first_structure", "pass", "一级结构为完整主题大题。")
    else:
        _check(checks, "theme_first_structure", "blocked", "一级结构不是完整主题大题。")
    if blueprint.get("blockers") or blueprint.get("status") == "blocked":
        _check(checks, "blueprint_blockers", "blocked", "组卷蓝图仍有未解决阻断项。")
    else:
        _check(checks, "blueprint_blockers", "pass", "组卷蓝图无结构阻断项。")

    theme_count = normalized_preset["per_paper"]["theme_count"]["value"]
    theme_verification = normalized_preset["per_paper"]["theme_count"]["verification"][
        "status"
    ]
    if theme_count is None:
        _check(checks, "theme_count_configured", "blocked", "请为本卷填写主题数。")
    elif theme_verification == "blocked_pending_review":
        _check(checks, "theme_count_configured", "blocked", "本卷主题数仍待复核。")
    elif theme_count != blueprint.get("counts", {}).get("theme_count"):
        _check(checks, "theme_count_configured", "blocked", "预设主题数与题篮不一致。")
    else:
        _check(
            checks,
            "theme_count_configured",
            "pass" if theme_verification == "verified_for_exact_paper" else "warning",
            "本卷主题数与题篮一致。",
        )

    for field, label in (
        ("total_score", "总分"),
        ("duration_minutes", "考试时间"),
        ("scoring_rules", "计分规则"),
    ):
        editable = normalized_preset["per_paper"][field]
        verification = editable["verification"]["status"]
        if editable["value"] is None or verification in {
            "unknown_requires_exact_paper",
            "blocked_pending_review",
        }:
            _check(checks, f"{field}_configured", "blocked", f"请为本卷填写{label}。")
        else:
            status = "pass" if verification == "verified_for_exact_paper" else "warning"
            message = (
                f"{label}已逐卷核验。"
                if status == "pass"
                else f"{label}已填写为项目模板值，导出时必须显示“项目模板”。"
            )
            _check(checks, f"{field}_configured", status, message)
    numbering = normalized_preset["structure"]["subquestion_numbering"]
    numbering_verification = numbering["verification"]["status"]
    if numbering["value"] is None or numbering_verification in {
        "unknown_requires_exact_paper",
        "blocked_pending_review",
    }:
        _check(checks, "numbering_configured", "blocked", "请为本卷选择题号方式。")
    else:
        _check(
            checks,
            "numbering_configured",
            "pass" if numbering_verification == "verified_for_exact_paper" else "warning",
            "本卷题号方式已经冻结。",
        )

    try:
        student_summary = _validate_document_plan(
            student_plan, audience="student", preset=normalized_preset
        )
        teacher_summary = _validate_document_plan(
            teacher_plan, audience="teacher", preset=normalized_preset
        )
        plans_valid = True
    except (PaperFormatContractError, KeyError, TypeError, ValueError):
        student_summary = {
            "atomic_rows": [],
            "material_keys": [],
            "material_bindings": [],
            "material_order_bases": [],
        }
        teacher_summary = {
            "atomic_rows": [],
            "material_keys": [],
            "material_bindings": [],
            "material_order_bases": [],
        }
        plans_valid = False
    _check(
        checks,
        "document_plan_integrity",
        "pass" if plans_valid else "blocked",
        "学生版与教师版结构、题号和绑定均已重算。"
        if plans_valid
        else "学生版或教师版结构、题号、材料、分值或绑定不一致。",
    )

    same_source = (
        blueprint_digest_ok
        and student_plan.get("blueprint_digest")
        == teacher_plan.get("blueprint_digest")
        == validated_blueprint_digest
        and student_plan.get("content_version_id") == teacher_plan.get("content_version_id")
        and student_plan.get("question_anchor_digest")
        == teacher_plan.get("question_anchor_digest")
        and student_plan.get("answer_anchor_digest") == teacher_plan.get("answer_anchor_digest")
    )
    _check(
        checks,
        "student_teacher_same_source",
        "pass" if same_source else "blocked",
        "学生版与教师版使用同一冻结内容。" if same_source else "学生版与教师版内容版本不一致。",
    )
    same_bindings = student_plan.get("bindings") == teacher_plan.get("bindings")
    same_visible_questions = False
    if plans_valid:
        teacher_visible = teacher_plan.get("visible")
        student_visible = student_plan.get("visible")
        same_visible_questions = (
            isinstance(teacher_visible, Mapping)
            and isinstance(student_visible, Mapping)
            and _teacher_visible_as_student(teacher_visible) == student_visible
        )
    _check(
        checks,
        "question_answer_numbering",
        "pass" if same_bindings and same_visible_questions else "blocked",
        "学生版题号与教师版答案号映射一致。"
        if same_bindings and same_visible_questions
        else "学生版与教师版的题号、题面、分值或答案号不一致。",
    )
    content_version_ok = False
    if plans_valid and same_bindings and same_source:
        expected_content_version = _content_version_id(
            preset=normalized_preset,
            blueprint_digest=validated_blueprint_digest,
            student_visible=student_plan["visible"],
            teacher_visible=teacher_plan["visible"],
            bindings=student_plan["bindings"],
        )
        content_version_ok = student_plan.get("content_version_id") == expected_content_version
    _check(
        checks,
        "content_version_integrity",
        "pass" if content_version_ok else "blocked",
        "冻结内容版本覆盖双版本题面、共享材料与绑定。"
        if content_version_ok
        else "冻结内容版本与当前可见内容不一致。",
    )
    if _contains_forbidden_student_keys(student_plan.get("visible")):
        _check(checks, "student_answer_leak", "blocked", "学生版可见内容含答案、解析或来源标签。")
    else:
        _check(checks, "student_answer_leak", "pass", "学生版未暴露答案、解析或内部来源标签。")

    student_rows = student_summary["atomic_rows"]
    if student_rows and all(
        isinstance(row.get("answer_space"), Mapping)
        and type(row["answer_space"].get("lines")) is int
        for row in student_rows
    ):
        _check(checks, "answer_space", "pass", "每个作答单元均有答题空间计划。")
    else:
        _check(checks, "answer_space", "blocked", "部分作答单元缺少答题空间。")

    teacher_rows = teacher_summary["atomic_rows"]
    if teacher_rows and all(
        isinstance(row.get("teacher_notes"), Mapping)
        and "source_reference_answer" in row["teacher_notes"]
        and "pitfalls_zh" in row["teacher_notes"]
        and "source_label_zh" in row["teacher_notes"]
        for row in teacher_rows
    ):
        _check(checks, "teacher_annotations", "pass", "教师版保留答案、解析/易错点与来源标签。")
    else:
        _check(checks, "teacher_annotations", "blocked", "教师版附加信息不完整。")

    visible_total = sum(float(row.get("score", 0)) for row in student_rows)
    configured_total = normalized_preset["per_paper"]["total_score"]["value"]
    if configured_total is not None and abs(visible_total - configured_total) < 1e-9:
        _check(checks, "score_total", "pass", "分题分值之和等于本卷总分。")
    else:
        _check(checks, "score_total", "blocked", "分题分值之和与本卷总分不一致。")

    student_material_keys = student_summary["material_keys"]
    teacher_material_keys = teacher_summary["material_keys"]
    student_material_bindings = student_summary["material_bindings"]
    teacher_material_bindings = teacher_summary["material_bindings"]
    blueprint_materials_by_key: dict[str, Mapping[str, Any]] = {}
    blueprint_order_bases: list[str | None] = []
    for bundle in blueprint.get("theme_bundles", []):
        if not isinstance(bundle, Mapping):
            continue
        integrity = bundle.get("integrity")
        blueprint_order_bases.append(
            integrity.get("shared_material_order_basis")
            if isinstance(integrity, Mapping)
            else None
        )
        for material in bundle.get("shared_materials", []):
            if not isinstance(material, Mapping):
                continue
            render_once_key = material.get("render_once_key")
            if isinstance(render_once_key, str):
                blueprint_materials_by_key[render_once_key] = material

    source_bindings_match_blueprint = True
    for binding in student_material_bindings:
        if not binding.get("source_binding_complete"):
            continue
        blueprint_material = blueprint_materials_by_key.get(
            str(binding.get("render_once_key"))
        )
        if (
            blueprint_material is None
            or binding.get("material_id") != blueprint_material.get("material_id")
            or binding.get("source_page") != blueprint_material.get("page")
        ):
            source_bindings_match_blueprint = False
            break
    if (
        plans_valid
        and student_material_keys == teacher_material_keys
        and len(student_material_keys) == len(set(student_material_keys))
        and student_material_bindings == teacher_material_bindings
        and source_bindings_match_blueprint
        and student_summary["material_order_bases"]
        == teacher_summary["material_order_bases"]
        == blueprint_order_bases
    ):
        _check(checks, "shared_material_once", "pass", "共享材料在双版本中均只呈现一次。")
    else:
        _check(checks, "shared_material_once", "blocked", "共享材料被重复呈现或双版本不一致。")

    student_visible = student_plan.get("visible")
    identity = student_visible.get("identity_fields_zh") if isinstance(student_visible, Mapping) else None
    header = student_visible.get("header") if isinstance(student_visible, Mapping) else None
    footer = student_visible.get("footer") if isinstance(student_visible, Mapping) else None
    if (
        identity == ["姓名", "班级"]
        and isinstance(header, Mapping)
        and header.get("enabled") is True
        and isinstance(footer, Mapping)
        and footer.get("enabled") is True
    ):
        _check(checks, "header_footer_identity", "pass", "标题、姓名/班级、页眉页脚计划完整。")
    else:
        _check(checks, "header_footer_identity", "blocked", "学生版卷首或页眉页脚计划不完整。")

    rendering = normalized_preset["rendering"]
    artifacts = rendering.get("artifacts")
    if (
        rendering.get("docx_is_source_of_truth") is True
        and rendering.get("inspect_every_rendered_page") is True
        and isinstance(artifacts, list)
        and {item.get("artifact_id") for item in artifacts}
        == {"student_docx", "student_pdf", "teacher_docx", "teacher_pdf"}
    ):
        _check(checks, "render_contract", "pass", "DOCX/PDF 四文件渲染与逐页检查合同完整。")
    else:
        _check(checks, "render_contract", "blocked", "DOCX/PDF 渲染合同不完整。")

    blockers = [item for item in checks if item["status"] == "blocked"]
    warnings = [item for item in checks if item["status"] == "warning"]
    report = {
        "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
        "contract_kind": PREFLIGHT_KIND,
        "status": "ready_for_renderer" if not blockers else "blocked",
        "blueprint_digest": blueprint.get("blueprint_digest"),
        "content_version_id": student_plan.get("content_version_id"),
        "checks": checks,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "teacher_summary_zh": (
            f"导出前检查通过，含 {len(warnings)} 项模板提示；可以进入 DOCX 渲染。"
            if not blockers
            else f"导出前检查有 {len(blockers)} 项阻断，请先修正。"
        ),
        "publication_allowed": False,
    }
    report["preflight_digest"] = _sha256(report)
    return report


def build_render_request(
    *,
    preset: Mapping[str, Any],
    student_plan: Mapping[str, Any],
    teacher_plan: Mapping[str, Any],
    preflight_report: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_preset = validate_paper_format_preset(preset)
    _validate_document_plan(student_plan, audience="student", preset=normalized_preset)
    _validate_document_plan(teacher_plan, audience="teacher", preset=normalized_preset)
    if (
        student_plan.get("blueprint_digest") != teacher_plan.get("blueprint_digest")
        or student_plan.get("content_version_id") != teacher_plan.get("content_version_id")
        or student_plan.get("question_anchor_digest")
        != teacher_plan.get("question_anchor_digest")
        or student_plan.get("answer_anchor_digest")
        != teacher_plan.get("answer_anchor_digest")
        or student_plan.get("bindings") != teacher_plan.get("bindings")
        or _teacher_visible_as_student(teacher_plan["visible"])
        != student_plan.get("visible")
    ):
        raise PaperFormatContractError(
            "document_plan_pair_mismatch", "学生版与教师版题面、题号或答案号不一致。"
        )
    expected_content_version = _content_version_id(
        preset=normalized_preset,
        blueprint_digest=student_plan["blueprint_digest"],
        student_visible=student_plan["visible"],
        teacher_visible=teacher_plan["visible"],
        bindings=student_plan["bindings"],
    )
    if student_plan.get("content_version_id") != expected_content_version:
        raise PaperFormatContractError(
            "content_version_mismatch", "学生版与教师版冻结内容版本不一致。"
        )
    preflight = _require_mapping(
        preflight_report,
        code="preflight_report_invalid",
        message_zh="导出前检查回执不正确。",
    )
    supplied_preflight_digest = preflight.get("preflight_digest")
    preflight_core = deepcopy(dict(preflight))
    preflight_core.pop("preflight_digest", None)
    if (
        preflight.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION
        or preflight.get("contract_kind") != PREFLIGHT_KIND
        or preflight.get("status") != "ready_for_renderer"
        or preflight.get("blocker_count") != 0
        or preflight.get("publication_allowed") is not False
        or preflight.get("blueprint_digest") != student_plan["blueprint_digest"]
        or preflight.get("content_version_id") != student_plan["content_version_id"]
        or not isinstance(supplied_preflight_digest, str)
        or not _SHA256.fullmatch(supplied_preflight_digest)
        or supplied_preflight_digest != _sha256(preflight_core)
    ):
        raise PaperFormatContractError(
            "preflight_report_invalid", "导出前检查尚未通过或回执已变化。", 409
        )
    artifacts = deepcopy(normalized_preset["rendering"]["artifacts"])
    plan_digests = {
        "student": _sha256(student_plan),
        "teacher": _sha256(teacher_plan),
    }
    request = {
        "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
        "contract_kind": RENDER_REQUEST_KIND,
        "render_job_id": f"render_{_sha256({'student': plan_digests['student'], 'teacher': plan_digests['teacher']})[:32]}",
        "blueprint_digest": student_plan.get("blueprint_digest"),
        "content_version_id": student_plan.get("content_version_id"),
        "question_anchor_digest": student_plan.get("question_anchor_digest"),
        "answer_anchor_digest": student_plan.get("answer_anchor_digest"),
        "preflight_digest": supplied_preflight_digest,
        "plan_digests": plan_digests,
        "artifacts": artifacts,
        "qa_contract": {
            "inspect_every_page_at_100_percent": True,
            "rerender_after_every_change": True,
            "allowed_layout_defects": 0,
            "docx_pdf_page_count_equal_within_audience": True,
            "question_answer_numbering_parity": True,
            "metadata_privacy_required": True,
            "secrets_forbidden": True,
        },
        "publication_allowed": False,
    }
    request["render_request_digest"] = _sha256(request)
    return request


def validate_render_receipts(
    request: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate renderer receipts after all DOCX/PDF pages have been inspected."""

    if request.get("contract_kind") != RENDER_REQUEST_KIND:
        raise PaperFormatContractError("render_request_invalid", "渲染请求版本不正确。")
    supplied_request_digest = request.get("render_request_digest")
    request_core = deepcopy(dict(request))
    request_core.pop("render_request_digest", None)
    if (
        request.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION
        or not isinstance(supplied_request_digest, str)
        or not _SHA256.fullmatch(supplied_request_digest)
        or supplied_request_digest != _sha256(request_core)
        or request.get("publication_allowed") is not False
    ):
        raise PaperFormatContractError("render_request_invalid", "渲染请求已变化或版本不正确。")
    expected = {
        item["artifact_id"]: item for item in request.get("artifacts", [])
    }
    if set(expected) != {
        "student_docx",
        "student_pdf",
        "teacher_docx",
        "teacher_pdf",
    } or len(request.get("artifacts", [])) != 4:
        raise PaperFormatContractError("render_request_invalid", "渲染请求必须包含四个固定文件。")
    receipt_by_id: dict[str, Mapping[str, Any]] = {}
    checks: list[dict[str, Any]] = []
    for receipt_value in receipts:
        receipt = _require_mapping(
            receipt_value,
            code="render_receipt_invalid",
            message_zh="渲染回执格式不正确。",
        )
        required = {
            "schema_version",
            "contract_kind",
            "artifact_id",
            "format",
            "sha256",
            "render_job_id",
            "render_request_digest",
            "plan_digest",
            "preflight_digest",
            "page_count",
            "rendered_page_count",
            "page_review_receipt_sha256",
            "all_pages_visual_pass",
            "metadata_privacy_pass",
            "content_version_id",
            "blueprint_digest",
            "question_anchor_digest",
            "answer_anchor_digest",
            "figure_count",
        }
        if set(receipt) != required:
            raise PaperFormatContractError("render_receipt_invalid", "渲染回执字段不完整。")
        artifact_id = receipt.get("artifact_id")
        if artifact_id not in expected or artifact_id in receipt_by_id:
            raise PaperFormatContractError("render_receipt_invalid", "渲染回执文件标识重复或未知。")
        if receipt.get("format") != expected[artifact_id]["format"]:
            raise PaperFormatContractError("render_receipt_invalid", "渲染回执文件格式不匹配。")
        if (
            receipt.get("schema_version") != PAPER_FORMAT_SCHEMA_VERSION
            or receipt.get("contract_kind") != RENDER_RECEIPT_KIND
            or receipt.get("render_job_id") != request.get("render_job_id")
            or receipt.get("render_request_digest") != supplied_request_digest
            or receipt.get("preflight_digest") != request.get("preflight_digest")
            or receipt.get("answer_anchor_digest") != request.get("answer_anchor_digest")
            or receipt.get("plan_digest")
            != request.get("plan_digests", {}).get(expected[artifact_id]["audience"])
        ):
            raise PaperFormatContractError("render_receipt_invalid", "渲染回执未绑定当前计划和预检。")
        sha = receipt.get("sha256")
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            raise PaperFormatContractError("render_receipt_invalid", "渲染文件校验值不正确。")
        page_review_sha = receipt.get("page_review_receipt_sha256")
        if not isinstance(page_review_sha, str) or not _SHA256.fullmatch(page_review_sha):
            raise PaperFormatContractError("render_receipt_invalid", "逐页检查回执校验值不正确。")
        receipt_by_id[artifact_id] = receipt

    missing = sorted(set(expected) - set(receipt_by_id))
    _check(
        checks,
        "four_artifacts_present",
        "pass" if not missing else "blocked",
        "学生版与教师版 DOCX/PDF 四文件齐全。" if not missing else "DOCX/PDF 四文件不齐全。",
    )
    artifact_hashes = {
        artifact_id: receipt["sha256"] for artifact_id, receipt in receipt_by_id.items()
    }
    hashes_unique = len(artifact_hashes) == len(set(artifact_hashes.values()))
    _check(
        checks,
        "artifact_sha256_unique",
        "pass" if hashes_unique else "blocked",
        "四个导出文件使用不同的内容校验值。"
        if hashes_unique
        else "不同导出文件出现重复校验值，须重新渲染并核验。",
    )
    for artifact_id, receipt in receipt_by_id.items():
        valid = (
            type(receipt["page_count"]) is int
            and receipt["page_count"] > 0
            and receipt["rendered_page_count"] == receipt["page_count"]
            and receipt["all_pages_visual_pass"] is True
            and receipt["metadata_privacy_pass"] is True
            and receipt["content_version_id"] == request["content_version_id"]
            and receipt["blueprint_digest"] == request["blueprint_digest"]
            and receipt["question_anchor_digest"] == request["question_anchor_digest"]
            and type(receipt["figure_count"]) is int
            and receipt["figure_count"] >= 0
        )
        _check(
            checks,
            f"artifact_{artifact_id}",
            "pass" if valid else "blocked",
            f"{artifact_id} 已逐页核验并绑定冻结内容。"
            if valid
            else f"{artifact_id} 未完成逐页核验或版本绑定。",
        )

    if not missing:
        for audience in ("student", "teacher"):
            docx = receipt_by_id[f"{audience}_docx"]
            pdf = receipt_by_id[f"{audience}_pdf"]
            parity = (
                docx["page_count"] == pdf["page_count"]
                and docx["figure_count"] == pdf["figure_count"]
                and docx["question_anchor_digest"] == pdf["question_anchor_digest"]
            )
            _check(
                checks,
                f"{audience}_docx_pdf_parity",
                "pass" if parity else "blocked",
                f"{audience} 版 DOCX/PDF 页数、图数和题号一致。"
                if parity
                else f"{audience} 版 DOCX/PDF 不一致。",
            )
    blockers = [item for item in checks if item["status"] == "blocked"]
    return {
        "schema_version": PAPER_FORMAT_SCHEMA_VERSION,
        "contract_kind": RENDER_REPORT_KIND,
        "render_job_id": request.get("render_job_id"),
        "render_request_digest": supplied_request_digest,
        "status": "local_delivery_candidate" if not blockers else "blocked",
        "checks": checks,
        "blocker_count": len(blockers),
        "artifact_hashes": artifact_hashes,
        "publication_allowed": False,
        "teacher_summary_zh": (
            "四个文件与逐页核验回执一致；仅形成本地教师交付候选，不代表正式发布。"
            if not blockers
            else f"渲染验收有 {len(blockers)} 项阻断。"
        ),
    }


__all__ = [
    "BLUEPRINT_KIND",
    "DOCUMENT_PLAN_KIND",
    "HIERARCHY",
    "PAPER_FORMAT_SCHEMA_VERSION",
    "PREFLIGHT_KIND",
    "PRESET_KIND",
    "RENDER_REPORT_KIND",
    "RENDER_REQUEST_KIND",
    "TOP_LEVEL_UNIT",
    "DocumentContentResolver",
    "PaperFormatContractError",
    "RendererAdapter",
    "ThemeSnapshotLoader",
    "build_assembly_blueprint",
    "build_document_plans",
    "build_render_request",
    "default_shanghai_theme_preset",
    "run_export_preflight",
    "validate_paper_format_preset",
    "validate_render_receipts",
]
