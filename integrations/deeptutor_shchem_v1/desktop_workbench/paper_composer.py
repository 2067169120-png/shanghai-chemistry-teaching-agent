from __future__ import annotations

"""Pure paper-composer projection used by the native Qt workbench.

The readers in :mod:`integrations.deeptutor_shchem_v1` deliberately expose a
candidate/read-only hierarchy.  This module turns that hierarchy into a small,
mutable *teacher draft* without changing the source records.  Keeping the
projection free of Qt makes numbering, preview invalidation and narrow-window
tests deterministic and reusable by other desktop surfaces.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from ..datong_answer_bindings import EXISTING_ANSWER_AREA_NODE_IDS
from ..paper_export_alias_projection import project_explicit_alias_units


class MixedPaperComposerModel:
    """Ordered teacher settings over one source-bound basket, not a second basket."""

    def __init__(self):
        self.basket_sha256 = ""
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self.excluded: set[str] = set()
        self.settings: dict[str, dict] = {}

    def merge(self, projection: Mapping[str, Any]) -> None:
        if projection.get("schema_version") != "shchem.desktop-mixed-basket.v1":
            raise ValueError("题篮版本无法读取，请刷新。")
        checksum, rows = projection.get("basket_sha256"), projection.get("items")
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or not isinstance(rows, list)
        ):
            raise ValueError("题篮快照不完整，请刷新。")
        incoming = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("key"), str)
                or not row["key"]
                or row["key"] in incoming
                or row.get("kind") not in {"core_theme", "word_question", "personal_visual_theme"}
                or not isinstance(row.get("content"), dict)
            ):
                raise ValueError("题篮条目无法对应完整来源，请刷新。")
            incoming[row["key"]] = deepcopy(row)
        old_keys = set(self.items)
        self.order = [key for key in self.order if key in incoming]
        self.order.extend(
            key for key in incoming if key not in old_keys and key not in self.excluded
        )
        self.excluded.intersection_update(incoming)
        self.settings = {
            key: value for key, value in self.settings.items() if key in incoming
        }
        for key, row in incoming.items():
            if row["kind"] == "personal_visual_theme":
                self.settings[key] = {"use_source_scores": True}
                continue
            if key not in self.settings:
                self.settings[key] = deepcopy(
                    row.get("settings")
                    or (
                        {"points": 2}
                        if row["kind"] == "word_question"
                        else {
                            "score_per_atomic": 2,
                            "answer_space_lines": 0,
                            "atomic_settings": {},
                        }
                    )
                )
        self.items = incoming
        self.basket_sha256 = checksum

    def move(self, key: str, direction: int) -> bool:
        if key not in self.order:
            return False
        position = self.order.index(key)
        target = position + direction
        if not 0 <= target < len(self.order):
            return False
        self.order[position], self.order[target] = (
            self.order[target],
            self.order[position],
        )
        return True

    def remove(self, key: str) -> None:
        if key in self.order:
            self.order.remove(key)
            self.excluded.add(key)

    def restore_excluded(self) -> None:
        self.order.extend(key for key in self.items if key in self.excluded)
        self.excluded.clear()

    def draft(self) -> dict:
        return {
            "schema_version": "shchem.desktop-mixed-ui-draft.v1",
            "order": list(self.order),
            "excluded": sorted(self.excluded),
            "settings": deepcopy(self.settings),
        }

    def restore(self, draft: Mapping[str, Any]) -> bool:
        if draft.get("schema_version") != "shchem.desktop-mixed-ui-draft.v1":
            return False
        order, excluded, settings = (
            draft.get("order"),
            draft.get("excluded"),
            draft.get("settings"),
        )
        if (
            not isinstance(order, list)
            or not isinstance(excluded, list)
            or not isinstance(settings, dict)
        ):
            return False
        if any(not isinstance(key, str) for key in order + excluded) or len(
            set(order)
        ) != len(order):
            return False
        if any(not isinstance(value, dict) for value in settings.values()):
            return False
        if any(
            self.items[key]["kind"] == "personal_visual_theme"
            and value != {"use_source_scores": True}
            for key, value in settings.items()
            if key in self.items
        ):
            return False
        self.excluded = set(excluded) & self.items.keys()
        self.order = [
            key for key in order if key in self.items and key not in self.excluded
        ]
        self.order.extend(
            key
            for key in self.items
            if key not in self.order and key not in self.excluded
        )
        for key in self.items:
            if isinstance(settings.get(key), dict):
                self.settings[key] = deepcopy(settings[key])
        return True


class MixedPaperPageReviewModel:
    """Track explicit per-page review of one immutable pagination manifest."""

    def __init__(self, pagination: Mapping[str, Any]):
        def valid_hash(value):
            return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

        if not isinstance(pagination, Mapping) or pagination.get("status") != "rendered_pending_review" or not valid_hash(pagination.get("manifest_sha256")):
            raise ValueError("本次真实分页尚未完成，不能确认。")
        documents = pagination.get("documents")
        if not isinstance(documents, Mapping) or set(documents) != {"student", "teacher"}:
            raise ValueError("学生版或教师版分页缺失，不能确认。")
        self.manifest_sha256 = pagination["manifest_sha256"]
        self.pages = {}
        self.loaded: set[tuple[str, int]] = set()
        self.reviewed: set[tuple[str, int]] = set()
        self.failed = False
        ids = set()
        for audience in ("student", "teacher"):
            document = documents[audience]
            if not isinstance(document, Mapping):
                raise ValueError("分页目录无法读取，不能确认。")
            count, pages = document.get("page_count"), document.get("pages")
            if type(count) is not int or count < 1 or not isinstance(pages, list) or len(pages) != count:
                raise ValueError("分页数量与目录不一致，不能确认。")
            for number, page in enumerate(pages, 1):
                if (
                    not isinstance(page, Mapping)
                    or type(page.get("page_number")) is not int
                    or page["page_number"] != number
                    or not isinstance(page.get("image_id"), str)
                    or not page["image_id"].strip()
                    or page["image_id"] in ids
                    or not valid_hash(page.get("sha256"))
                    or any(type(page.get(key)) is not int or page[key] < 1 for key in ("width", "height"))
                    or page["width"] * page["height"] > 40_000_000
                ):
                    raise ValueError("分页目录有缺页、重复页或无效图片标识，不能确认。")
                ids.add(page["image_id"])
            self.pages[audience] = tuple(deepcopy(dict(page)) for page in pages)

    def page(self, audience: str, number: int) -> dict:
        if audience not in self.pages or type(number) is not int or not 1 <= number <= len(self.pages[audience]):
            raise ValueError("所选分页不在本次预览中。")
        return deepcopy(self.pages[audience][number - 1])

    def mark_loaded(self, audience: str, number: int, sha256: str, width: int, height: int) -> bool:
        page = self.page(audience, number)
        if type(width) is not int or type(height) is not int or (sha256, width, height) != (page["sha256"], page["width"], page["height"]):
            self.failed = True
            return False
        self.loaded.add((audience, number))
        return True

    def mark_reviewed(self, audience: str, number: int) -> bool:
        self.page(audience, number)
        if self.failed or (audience, number) not in self.loaded:
            return False
        self.reviewed.add((audience, number))
        return True

    @property
    def total_pages(self) -> int:
        return sum(len(pages) for pages in self.pages.values())

    @property
    def can_confirm(self) -> bool:
        return not self.failed and len(self.reviewed) == self.total_pages


ANSWER_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "available": ("参考答案可用", "good"),
    "present_part_aligned": ("参考答案可用", "good"),
    "review": ("参考答案待教师核对", "warn"),
    "unaligned": ("参考答案待核对", "warn"),
    "missing": ("暂无参考答案", "none"),
    "absent": ("暂无参考答案", "none"),
    "none": ("暂无参考答案", "none"),
}

RESPONSE_LABELS: dict[str, str] = {
    "embedded_single_choice": "单项选择",
    "single_choice": "单项选择",
    "embedded_multiple_choice": "多项选择",
    "multiple_choice": "多项选择",
    "fill_blank": "填空",
    "fill": "填空",
    "equation": "方程式书写",
    "equation_writing": "方程式书写",
    "electrode_equation": "电极反应式",
    "calculation": "计算",
    "reason_explanation": "原因解释",
    "experiment_evaluation": "实验评价",
    "flow_analysis": "流程分析",
    "structure_inference": "结构推断",
    "isomer": "同分异构体",
    "synthesis_route": "合成路线",
    "graph_read_draw_complete": "图表读图/作图",
    # Common aliases emitted by the direct-scan and handout readers.  Keep the
    # raw machine token out of the teacher-facing compact row whenever the
    # source has already told us what kind of response it expects.
    "choice_single": "单项选择",
    "standalone_choice": "单项选择",
    "choice_multiple": "多项选择",
    "indefinite_choice": "不定项选择",
    "embedded_indeterminate_choice": "不定项选择",
    "single_choice_calculation": "选择/计算",
    "data_based_fill_blank": "资料填空",
    "short_fill": "填空",
    "reasoned_explanation": "原因解释",
    "graph_based_fill_blank": "图表填空",
    "table_reading_fill": "表格填空",
    "chemical_equation_or_notation": "化学用语书写",
    "chemical_equation_writing": "化学方程式书写",
    "ionic_equation_writing": "离子方程式书写",
    "electrode_equation_writing": "电极反应式",
    "electron_formula_writing": "电子式/电子排布书写",
    "numeric_calculation": "数值计算",
    "quantitative_calculation": "定量计算",
    "symbolic_calculation": "符号计算",
    "numeric_symbolic_response": "数值/符号作答",
    "short_answer": "简答",
    "short_explanation": "简要说明",
    "short_response": "简答",
    "concept_explanation": "概念解释",
    "data_explanation": "数据解释",
    "comparison_or_open_response": "比较/开放作答",
    "extended_explanation": "综合说明",
    "experiment_design": "实验设计",
    "experiment_observation": "实验现象记录",
    "experiment_operation_apparatus_plan": "实验操作/装置设计",
    "process_flow_condition_choice": "流程条件选择",
    "diagram_completion": "图示补全",
    "orbital_diagram_writing": "轨道图书写",
    "organic_structure_or_route": "有机结构/合成路线",
}


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _theme_identity_digest(scope: Any, paper_id: Any, theme_id: Any) -> str:
    """Opaque, stable identity used to reopen a basket without exposing IDs."""

    return _stable_hash(
        {
            "scope": _text(scope, "unknown"),
            "paper": _text(paper_id, "unknown"),
            "theme": _text(theme_id, "unknown"),
        }
    )


def _text(value: Any, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _positive_int(value: Any, fallback: int | None) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return fallback


def _display_text(value: Any, *keys: str, fallback: str = "") -> str:
    """Read a human label from either a scalar or a formal mapping."""

    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return fallback
    return _text(value, fallback)


def response_label(value: Any) -> str:
    # ``response_type`` is an internal schema name.  Teachers see the more
    # familiar ``题型`` wording throughout the native workbench.
    raw = _text(value, "题型待确认")
    if raw.casefold() in {"unknown", "none", "null", "pending", "待确认"}:
        return "题型待确认"
    return RESPONSE_LABELS.get(raw, raw if raw else "题型待确认")


def answer_status(value: Any) -> tuple[str, str]:
    raw = _text(value, "review").casefold()
    return ANSWER_STATUS_LABELS.get(raw, ANSWER_STATUS_LABELS["review"])


def difficulty_label(value: Any) -> str:
    raw = _text(value, "")
    return {
        "D1": "基础",
        "D2": "中档",
        "D3": "挑战",
        "easy": "基础",
        "medium": "中档",
        "hard": "挑战",
    }.get(raw, raw or "难度待确认")


def _effective_answer_lines(response_type: str, requested: int) -> int:
    """Keep answer spaces compact while respecting a teacher override."""

    requested = max(0, min(20, int(requested)))
    if response_type in {"单项选择", "多项选择"}:
        return 0
    if response_type in {"填空", "方程式书写", "电极反应式"}:
        return min(requested, 2)
    return requested


@dataclass
class ComposerQuestion:
    key: str
    response_type: str
    score: int | None = 1
    section: str = "教材章节待确认"
    difficulty: str = "难度待确认"
    answer_status: str = "review"
    stem: str = "题面内容待展开。"
    answer: str = ""
    analysis: str = ""
    answer_space: int | None = 2
    dependency: str = "使用共享材料"
    options: list[str] = field(default_factory=list)
    source_question_number: str = ""
    crop_available: bool = False
    expanded: bool = False
    # Trace metadata is kept in the draft/export handoff only; native labels
    # never render these identifiers.
    source_ref: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_atomic(cls, atomic: Mapping[str, Any], index: int) -> "ComposerQuestion":
        labels = atomic.get("label_summary")
        labels = labels if isinstance(labels, Mapping) else {}
        answer = atomic.get("answer")
        answer = answer if isinstance(answer, Mapping) else {}
        raw_type = _display_text(
            atomic.get("item_type"),
            "value",
            "code",
            "id",
            fallback="unknown",
        )
        displayed_type = _display_text(
            atomic.get("response_type"),
            "display_zh",
            "label_zh",
            "value_zh",
            fallback=response_label(raw_type),
        )
        raw_difficulty = labels.get("cognitive_prelabel")
        if raw_difficulty is None:
            raw_difficulty = atomic.get("difficulty")
        raw_status = answer.get("availability")
        if raw_status is None:
            raw_status = "review"
        raw_status = _display_text(
            raw_status,
            "code",
            "value",
            "status",
            fallback="review",
        )
        response_requirement = _text(
            atomic.get("response_requirement_zh"),
            _text(atomic.get("visible_summary_zh"), "题面内容待展开。"),
        )
        full_stem = _text(
            atomic.get("full_stem_zh")
            or atomic.get("stem_zh")
            or atomic.get("question_text_zh")
        )
        # A catalogue summary is not a substitute for the source crop.  Keep
        # it visibly marked as a summary until a detail/crop resolver has
        # supplied the complete question body.
        stem = full_stem or f"题目图片待展开（当前仅显示摘要）：{response_requirement}"
        dependency_value = atomic.get("dependency")
        dependency_kind = (
            dependency_value.get("kind")
            if isinstance(dependency_value, Mapping)
            else None
        )
        dependency = {
            "independent": "独立作答",
            "shared_material_only": "使用共享材料",
            "one_prior_part": "承接前一小问",
            "multiple_prior_parts": "综合前面小问",
        }.get(_text(dependency_kind), "题目关联待确认")
        explicit_score = atomic.get("score")
        score_value = _positive_int(explicit_score, 1)
        score = (
            max(1, int(score_value or 1))
            if explicit_score is not None
            else None
        )
        explicit_lines = atomic.get("answer_space_lines")
        if explicit_lines is None:
            explicit_lines = atomic.get("answer_space")
        lines_value = _positive_int(explicit_lines, 0)
        if explicit_lines is not None:
            lines = int(lines_value or 0)
        elif _text(atomic.get("atomic_part_id"), "") in EXISTING_ANSWER_AREA_NODE_IDS:
            lines = 0
        else:
            lines = None
        crop_available = bool(
            atomic.get("content_loaded") is True
            or atomic.get("evidence_descriptors")
            or atomic.get("crop_bytes")
        )
        options_value = atomic.get("options")
        options = (
            [str(item) for item in options_value if isinstance(item, (str, int, float))]
            if isinstance(options_value, Sequence) and not isinstance(options_value, (str, bytes))
            else []
        )
        key = _text(atomic.get("atomic_part_id"), "")
        if not key:
            key = "question-" + _stable_hash({"index": index, "stem": response_requirement})[:16]
        return cls(
            key=key,
            response_type=displayed_type,
            score=score,
            section=_display_text(
                atomic.get("textbook_section_zh")
                or atomic.get("textbook_section")
                or atomic.get("curriculum_section"),
                "display_zh",
                "section_zh",
                fallback="教材章节待确认",
            ),
            difficulty=difficulty_label(
                _display_text(
                    raw_difficulty,
                    "display_zh",
                    "label_zh",
                    "value",
                    fallback="",
                )
            ),
            answer_status=raw_status,
            stem=stem,
            answer=_text(atomic.get("answer_text"), ""),
            analysis=_text(atomic.get("analysis_zh"), ""),
            answer_space=lines,
            dependency=dependency,
            options=options,
            source_question_number=_text(
                atomic.get("printed_question_number"), ""
            ),
            crop_available=crop_available,
            source_ref={
                key: atomic.get(key)
                for key in (
                    "atomic_part_id",
                    "printed_question_id",
                    "source_atomic_part_id",
                    "source_scope",
                    "master_parent_atomic_id",
                    "expandable_content_ref",
                )
                if atomic.get(key) is not None
            },
        )

    @property
    def answer_label(self) -> str:
        return answer_status(self.answer_status)[0]

    def public_dict(self, number: str | None = None) -> dict[str, Any]:
        value = asdict(self)
        value["display_number"] = number
        value["answer_label"] = self.answer_label
        return value


@dataclass
class ComposerTheme:
    key: str
    title: str
    source: str = "来源待确认"
    chapter: str = "教材章节待确认"
    difficulty: str = "难度待确认"
    time_minutes: int | None = None
    shared_summary: str = "暂无共享材料摘要"
    shared_text: str = "共享材料待展开。"
    shared_materials: list[dict[str, Any]] = field(default_factory=list)
    questions: list[ComposerQuestion] = field(default_factory=list)
    selected: bool = True
    source_identity_sha256: str = ""
    source_ref: dict[str, Any] = field(default_factory=dict, repr=False)
    match_status: str = "matched"

    @property
    def score(self) -> int | None:
        if any(item.score is None for item in self.questions):
            return None
        return sum(max(0, int(item.score or 0)) for item in self.questions)

    @property
    def question_count(self) -> int:
        return len(self.questions)

    def public_dict(self, theme_number: int, numbers: Mapping[str, str]) -> dict[str, Any]:
        return {
            "display_number": f"第{theme_number}题",
            "title": self.title,
            "source": self.source,
            "chapter": self.chapter,
            "difficulty": self.difficulty,
            "time_minutes": self.time_minutes,
            "score": self.score,
            "question_count": self.question_count,
            "shared_materials": self.shared_materials
            or [
                {
                    "summary": self.shared_summary,
                    "text": self.shared_text,
                    "render_once": True,
                }
            ],
            "questions": [
                item.public_dict(numbers.get(item.key)) for item in self.questions
            ],
            "match_status": self.match_status,
        }


def _source_label(paper: Mapping[str, Any]) -> str:
    metadata = paper.get("source_metadata")
    if not isinstance(metadata, Mapping):
        return _text(paper.get("title"), "来源待确认")
    pieces = [
        _text(metadata.get("year")),
        _text(metadata.get("region")),
        _text(metadata.get("paper_type")),
    ]
    pieces = [item for item in pieces if item and item.casefold() != "unknown"]
    title = _text(paper.get("title"))
    if title and pieces:
        return f"{title} · {' · '.join(pieces)}"
    return title or "来源待确认"


def _theme_from_group(
    group: Mapping[str, Any],
    fallback_key: str,
    *,
    scope: str = "",
) -> ComposerTheme:
    # Preview and export must use the same explicitly split answer units.
    # Project only this selected theme; unrelated incomplete catalog themes
    # must not prevent a teacher from composing a usable local draft.
    raw_chain = group.get("atomic_chain")
    if scope == "master" and isinstance(raw_chain, list) and any(
        isinstance(row, Mapping) and row.get("alias_units") for row in raw_chain
    ):
        projection = project_explicit_alias_units(
            {"papers": [{"paper": group.get("paper", {}), "theme_groups": [group]}]},
            [],
            scope=scope,
        )
        group = projection.catalog["papers"][0]["theme_groups"][0]
        for row in group["atomic_chain"]:
            binding = projection.content_bindings.get(row["atomic_part_id"])
            if binding is not None:
                row["source_atomic_part_id"] = (
                    binding.source_unit_node_id or binding.source_node_id
                )
                row["source_scope"] = binding.source_scope
                row["master_parent_atomic_id"] = binding.master_parent_atomic_id
    paper = group.get("paper")
    paper = paper if isinstance(paper, Mapping) else {}
    theme = group.get("theme")
    theme = theme if isinstance(theme, Mapping) else {}
    title = _text(
        group.get("display_title_zh")
        or theme.get("title")
        or theme.get("printed_title"),
        "未命名大题",
    )
    theme_id = _text(theme.get("id"), fallback_key)
    paper_id = _text(paper.get("id"), "")
    source_identity = (
        _theme_identity_digest(scope, paper_id, theme_id)
        if paper_id and theme_id
        else ""
    )
    shared = group.get("shared_context")
    shared = shared if isinstance(shared, Mapping) else {}
    summary = _text(shared.get("context_summary_zh"), "暂无共享材料摘要")
    materials = shared.get("materials")
    material_texts: list[str] = []
    shared_materials: list[dict[str, Any]] = []
    if isinstance(materials, Sequence) and not isinstance(materials, (str, bytes)):
        for material in materials:
            if isinstance(material, Mapping):
                description = _text(material.get("candidate_description_zh"), "")
                material_texts.append(description)
                shared_materials.append(
                    {
                        "summary": description or "共享材料图片待展开。",
                        "text": description or "共享材料图片待展开。",
                        "render_once": True,
                    }
                )
    # Candidate descriptions identify that a material exists but are not the
    # original page pixels.  Do not present those descriptions as source text.
    shared_text = summary
    if material_texts and not summary:
        shared_text = f"共享材料 {len(material_texts)} 项，原始图片待展开。"
    raw_atoms = group.get("atomic_chain")
    raw_atoms = raw_atoms if isinstance(raw_atoms, Sequence) else []
    questions = [
        ComposerQuestion.from_atomic(atom, index)
        for index, atom in enumerate(raw_atoms, start=1)
        if isinstance(atom, Mapping)
    ]
    if not questions:
        count = _positive_int(
            (
                group.get("counts") or {}
            ).get(
                "atomic_total",
                (
                    group.get("counts") or {}
                ).get("atomic"),
            )
            if isinstance(group.get("counts"), Mapping)
            else None,
            0,
        )
        questions = [
            ComposerQuestion(
                key=f"{theme_id}-pending-{index}",
                response_type="题型待确认",
                stem="题目图片待展开；请先在题库完成图片核对。",
                answer_status="review",
                score=None,
                answer_space=None,
            )
            for index in range(1, count + 1)
        ]
    difficulty_values = [
        item.difficulty for item in questions if item.difficulty != "难度待确认"
    ]
    difficulty = (
        "挑战"
        if difficulty_values and all(value == "挑战" for value in difficulty_values)
        else "中档"
        if difficulty_values and any(value == "中档" for value in difficulty_values)
        else difficulty_values[0]
        if difficulty_values
        else "难度待确认"
    )
    # A time estimate is an editable teacher setting, never a source claim.
    explicit_time = group.get("time_minutes")
    if explicit_time is None:
        explicit_time = group.get("estimated_time_minutes")
    time_minutes = (
        max(1, _positive_int(explicit_time, 1))
        if explicit_time is not None
        else None
    )
    chapter = _text(
        group.get("textbook_section_zh")
        or group.get("textbook_section")
        or group.get("curriculum_section"),
        "教材章节待确认",
    )
    return ComposerTheme(
        key=theme_id,
        title=title,
        source=_source_label(paper),
        chapter=chapter,
        difficulty=difficulty,
        time_minutes=time_minutes,
        shared_summary=summary,
        shared_text=shared_text,
        shared_materials=shared_materials,
        questions=questions,
        source_identity_sha256=source_identity,
        source_ref={
            "scope": scope,
            "paper_id": paper_id,
            "theme_id": theme_id,
            "data_snapshot_id": group.get("data_snapshot_id"),
        },
    )


def iter_catalog_groups(catalog: Mapping[str, Any] | None) -> Iterable[Mapping[str, Any]]:
    if not isinstance(catalog, Mapping):
        return ()
    papers = catalog.get("papers")
    if not isinstance(papers, Sequence) or isinstance(papers, (str, bytes)):
        return ()
    groups: list[Mapping[str, Any]] = []
    for paper_entry in papers:
        if not isinstance(paper_entry, Mapping):
            continue
        raw_groups = paper_entry.get("theme_groups")
        if not isinstance(raw_groups, Sequence) or isinstance(raw_groups, (str, bytes)):
            continue
        groups.extend(group for group in raw_groups if isinstance(group, Mapping))
    return groups


def build_themes_from_basket(
    basket: Sequence[Mapping[str, Any]] | None,
    catalog: Mapping[str, Any] | None = None,
) -> list[ComposerTheme]:
    """Resolve persisted basket summaries to complete theme projections.

    Older desktop drafts intentionally contain only teacher-readable summary
    fields.  Matching by title/source/count lets those drafts reopen without
    rewriting the accepted source crops or putting internal identifiers in the
    visible UI.  Newer callers may pass ``theme_group`` directly for a faster
    in-memory path.
    """

    rows = list(iter_catalog_groups(catalog))
    catalog_scope = _text(catalog.get("scope"), "") if isinstance(catalog, Mapping) else ""
    result: list[ComposerTheme] = []
    seen: set[str] = set()
    for index, raw in enumerate(basket or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        group = raw.get("theme_group")
        if not isinstance(group, Mapping):
            title = _text(raw.get("title_zh"), "未命名大题")
            paper_title = _text(raw.get("paper_title_zh"), "")
            atomic_total = _positive_int(raw.get("atomic_total"), 0)
            source_key = _text(
                raw.get("source_identity_sha256") or raw.get("source_key"), ""
            )
            exact_candidates = []
            if source_key:
                for candidate in rows:
                    candidate_paper = candidate.get("paper") if isinstance(candidate.get("paper"), Mapping) else {}
                    candidate_theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else {}
                    candidate_digest = _theme_identity_digest(
                        catalog_scope,
                        candidate_paper.get("id"),
                        candidate_theme.get("id"),
                    )
                    if candidate_digest == source_key:
                        exact_candidates.append(candidate)
            if len(exact_candidates) == 1:
                group = exact_candidates[0]
            elif len(exact_candidates) > 1:
                # A digest collision is exceptionally unlikely, but fail closed
                # if a malformed catalog ever presents one.
                group = None
            if not isinstance(group, Mapping) and not source_key:
                candidates = [
                    candidate
                    for candidate in rows
                    if _text(
                        (candidate.get("theme") or {}).get("title")
                        if isinstance(candidate.get("theme"), Mapping)
                        else None
                    )
                    == title
                    and (
                        not paper_title
                        or _text(
                            (candidate.get("paper") or {}).get("title")
                            if isinstance(candidate.get("paper"), Mapping)
                            else None
                        )
                        == paper_title
                    )
                ]
                if atomic_total and len(candidates) > 1:
                    filtered = [
                        candidate
                        for candidate in candidates
                        if _positive_int(
                            (
                                candidate.get("counts") or {}
                            ).get(
                                "atomic_total",
                                (
                                    candidate.get("counts") or {}
                                ).get("atomic"),
                            )
                            if isinstance(candidate.get("counts"), Mapping)
                            else None,
                            -1,
                        )
                        == atomic_total
                    ]
                    if filtered:
                        candidates = filtered
                # Never pick the first same-title candidate: that would
                # silently change the source paper after a catalog refresh.
                group = candidates[0] if len(candidates) == 1 else None
        if isinstance(group, Mapping):
            theme = _theme_from_group(
                group,
                f"theme-{index}",
                scope=catalog_scope or _text(raw.get("scope"), ""),
            )
        else:
            title = _text(raw.get("title_zh"), "未命名大题")
            count = _positive_int(raw.get("atomic_total"), 0)
            theme = ComposerTheme(
                key=f"theme-{_stable_hash({'title': title, 'index': index})[:16]}",
                title=title,
                source=_text(raw.get("source_zh") or raw.get("paper_title_zh"), "来源待确认"),
                shared_summary=_text(raw.get("shared_context_zh"), "暂无共享材料摘要"),
                shared_text=_text(raw.get("shared_context_zh"), "共享材料待展开。"),
                questions=[
                    ComposerQuestion(
                        key=f"pending-{index}-{n}",
                        response_type="题型待确认",
                        stem="题目图片待展开；请先在题库完成图片核对。",
                        answer_status="review",
                        score=None,
                        answer_space=None,
                    )
                    for n in range(1, max(1, count) + 1)
                ],
                match_status=("ambiguous" if rows and raw.get("title_zh") else "pending"),
                source_identity_sha256=PaperComposerModel.basket_signature([raw])[0],
            )
        # Persisted keys can collide after a source refresh; the displayed
        # order remains stable while internal draft keys stay unique.
        original_key = theme.key
        suffix = 1
        while theme.key in seen:
            suffix += 1
            theme.key = f"{original_key}-{suffix}"
        seen.add(theme.key)
        result.append(theme)
    return result


@dataclass
class PaperComposerModel:
    mode: str = "mock_exam"
    title: str = ""
    subtitle: str = ""
    keywords: str = ""
    hot_topic: bool = False
    show_question_scores: bool = False
    duration_minutes: int = 60
    themes: list[ComposerTheme] = field(default_factory=list)
    revision: int = 0
    approved_revision: int | None = None
    preview_hash: str | None = None
    preview: dict[str, Any] | None = None
    selected_theme_key: str | None = None
    selected_question_key: str | None = None
    expanded_theme_keys: set[str] = field(default_factory=set)
    expanded_question_keys: set[str] = field(default_factory=set)
    basket_signature_value: list[str] = field(default_factory=list, repr=False)

    @classmethod
    def from_basket(
        cls,
        basket: Sequence[Mapping[str, Any]] | None,
        *,
        catalog: Mapping[str, Any] | None = None,
        mode: str = "mock_exam",
        title: str = "",
        keywords: str = "",
        hot_topic: bool = False,
        show_question_scores: bool = False,
    ) -> "PaperComposerModel":
        if type(show_question_scores) is not bool:
            raise ValueError("show_question_scores must be a bool")
        themes = build_themes_from_basket(basket, catalog)
        selected = themes[0].key if themes else None
        return cls(
            mode=mode if mode in {"mock_exam", "daily_practice"} else "mock_exam",
            title=title,
            keywords=keywords,
            hot_topic=hot_topic,
            show_question_scores=show_question_scores,
            themes=themes,
            selected_theme_key=selected,
            basket_signature_value=cls.basket_signature(basket),
        )

    def preserve_into(self, rebuilt: "PaperComposerModel") -> tuple["PaperComposerModel", bool]:
        """Keep exact-source order, edits and omitted themes during a basket refresh."""
        by_identity = {theme.source_identity_sha256: theme for theme in rebuilt.themes if theme.source_identity_sha256}
        previous_ids = set(self.basket_signature_value)
        selected_ids = {theme.source_identity_sha256 for theme in self.themes if theme.source_identity_sha256}
        excluded = previous_ids - selected_ids if all(theme.source_identity_sha256 for theme in self.themes) else set()
        ordered = []
        used = set()
        unmatched = False
        selected_key = None
        for old in self.themes:
            theme = by_identity.get(old.source_identity_sha256)
            if theme is None:
                unmatched = unmatched or bool(old.questions and self.revision)
                continue
            identity = theme.source_identity_sha256
            if identity in used:
                continue
            if theme.questions and all(question.key.startswith("pending-") for question in theme.questions):
                theme = deepcopy(old)
            else:
                previous = {question.key: question for question in old.questions}
                for question in theme.questions:
                    source = previous.get(question.key)
                    if source is not None:
                        if source.score is not None:
                            question.score = source.score
                        if source.answer_space is not None:
                            question.answer_space = source.answer_space
            ordered.append(theme)
            used.add(identity)
            if old.key == self.selected_theme_key:
                selected_key = theme.key
        ordered.extend(theme for theme in rebuilt.themes if theme.source_identity_sha256 not in used and theme.source_identity_sha256 not in excluded)
        rebuilt.themes = ordered
        rebuilt.revision = self.revision
        rebuilt.selected_theme_key = selected_key or (ordered[0].key if ordered else None)
        rebuilt.selected_question_key = self.selected_question_key
        rebuilt.expanded_theme_keys = set(self.expanded_theme_keys)
        rebuilt.expanded_question_keys = set(self.expanded_question_keys)
        return rebuilt, unmatched

    @staticmethod
    def basket_signature(basket: Sequence[Mapping[str, Any]] | None) -> list[str]:
        """Return a stable, non-secret signature for the current basket order."""

        signature: list[str] = []
        for item in basket or ():
            if not isinstance(item, Mapping):
                continue
            value = _text(
                item.get("source_identity_sha256")
                or item.get("source_key")
                or item.get("key"),
                "",
            )
            if not value:
                value = _stable_hash(
                    {
                        "scope": _text(item.get("scope"), ""),
                        "paper": _text(item.get("paper_title_zh"), ""),
                        "title": _text(item.get("title_zh"), ""),
                        "count": _positive_int(item.get("atomic_total"), 0),
                    }
                )
            signature.append(value)
        return signature

    @classmethod
    def from_draft_payload(
        cls,
        payload: Mapping[str, Any],
        basket: Sequence[Mapping[str, Any]] | None,
        *,
        catalog: Mapping[str, Any] | None = None,
    ) -> "PaperComposerModel | None":
        """Restore a draft only when it still belongs to the current basket."""

        if not isinstance(payload, Mapping):
            return None
        current_signature = cls.basket_signature(basket)
        saved_signature = payload.get("basket_signature")
        if isinstance(saved_signature, list) and [str(item) for item in saved_signature] != current_signature:
            return None
        show_question_scores = payload.get("show_question_scores", False)
        if type(show_question_scores) is not bool:
            return None
        mode = payload.get("mode")
        if mode not in {"mock_exam", "daily_practice"}:
            mode = "mock_exam"
        model = cls.from_basket(
            basket,
            catalog=catalog,
            mode=mode,
            title=_text(payload.get("title"), ""),
            keywords=_text(payload.get("keywords"), ""),
            hot_topic=payload.get("hot_topic") is True,
            show_question_scores=show_question_scores,
        )
        model.subtitle = _text(payload.get("subtitle"), "")
        model.basket_signature_value = current_signature
        duration = _positive_int(payload.get("duration_minutes"), 60)
        model.duration_minutes = max(1, min(300, int(duration or 60)))
        raw_themes = payload.get("themes")
        if not isinstance(raw_themes, list):
            return model
        current_by_identity = {
            theme.source_identity_sha256: theme
            for theme in model.themes
            if theme.source_identity_sha256
        }
        title_matches: dict[str, list[ComposerTheme]] = {}
        for candidate in model.themes:
            title_matches.setdefault(candidate.title, []).append(candidate)
        restored: list[ComposerTheme] = []
        used: set[int] = set()
        for raw_theme in raw_themes:
            if not isinstance(raw_theme, Mapping):
                continue
            identity = _text(
                raw_theme.get("source_identity_sha256") or raw_theme.get("source_key"),
                "",
            )
            theme = current_by_identity.get(identity) if identity else None
            if theme is None:
                matches = title_matches.get(_text(raw_theme.get("title"), ""), [])
                theme = matches[0] if len(matches) == 1 else None
            if theme is None or id(theme) in used:
                continue
            used.add(id(theme))
            for field_name in (
                "title",
                "source",
                "chapter",
                "difficulty",
                "time_minutes",
                "shared_summary",
                "shared_text",
            ):
                if field_name not in raw_theme:
                    continue
                value = raw_theme.get(field_name)
                if field_name == "time_minutes":
                    parsed = _positive_int(value, theme.time_minutes)
                    setattr(theme, field_name, parsed)
                elif isinstance(value, str):
                    setattr(theme, field_name, value)
            saved_materials = raw_theme.get("shared_materials")
            if isinstance(saved_materials, list):
                theme.shared_materials = deepcopy(
                    [item for item in saved_materials if isinstance(item, Mapping)]
                )
            if isinstance(raw_theme.get("match_status"), str):
                theme.match_status = str(raw_theme["match_status"])
            raw_questions = raw_theme.get("questions")
            if isinstance(raw_questions, list):
                by_key = {question.key: question for question in theme.questions}
                for q_index, raw_question in enumerate(raw_questions):
                    if not isinstance(raw_question, Mapping):
                        continue
                    question = by_key.get(_text(raw_question.get("key"), ""))
                    if question is None and q_index < len(theme.questions):
                        question = theme.questions[q_index]
                    if question is None:
                        continue
                    for field_name in (
                        "response_type",
                        "section",
                        "difficulty",
                        "answer_status",
                        "stem",
                        "answer",
                        "analysis",
                        "dependency",
                    ):
                        value = raw_question.get(field_name)
                        if isinstance(value, str):
                            setattr(question, field_name, value)
                    for field_name in ("score", "answer_space"):
                        if field_name not in raw_question:
                            continue
                        value = raw_question.get(field_name)
                        if value is None:
                            setattr(question, field_name, None)
                        else:
                            current = getattr(question, field_name)
                            fallback = int(current) if isinstance(current, int) else (1 if field_name == "score" else 0)
                            setattr(question, field_name, _positive_int(value, fallback))
            restored.append(theme)
        restored.extend(theme for theme in model.themes if id(theme) not in used)
        if restored:
            model.themes = restored
        selected = _text(payload.get("selected_theme_key"), "")
        model.selected_theme_key = selected if any(theme.key == selected for theme in model.themes) else (model.themes[0].key if model.themes else None)
        selected_question = _text(payload.get("selected_question_key"), "")
        selected_theme = model.selected_theme()
        model.selected_question_key = selected_question if selected_theme and any(q.key == selected_question for q in selected_theme.questions) else None
        expanded_themes = payload.get("expanded_theme_keys")
        expanded_questions = payload.get("expanded_question_keys")
        model.expanded_theme_keys = {str(key) for key in expanded_themes if isinstance(key, str)} if isinstance(expanded_themes, list) else set()
        model.expanded_question_keys = {str(key) for key in expanded_questions if isinstance(key, str)} if isinstance(expanded_questions, list) else set()
        model.revision = _positive_int(payload.get("revision"), 0) or 0
        # A preview/approval is never trusted across a restart; the teacher
        # must inspect a fresh snapshot before exporting again.
        model.preview_hash = None
        model.preview = None
        model.approved_revision = None
        return model

    @property
    def mode_label(self) -> str:
        return "模拟考试" if self.mode == "mock_exam" else "平时练习"

    @property
    def total_score(self) -> int | None:
        scores = [theme.score for theme in self.themes]
        if any(score is None for score in scores):
            return None
        return sum(int(score or 0) for score in scores)

    @property
    def total_time(self) -> int | None:
        times = [theme.time_minutes for theme in self.themes]
        if any(value is None for value in times):
            return None
        return sum(max(0, int(value or 0)) for value in times)

    @property
    def total_questions(self) -> int:
        return sum(theme.question_count for theme in self.themes)

    def question_numbers(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for theme_index, theme in enumerate(self.themes, start=1):
            for question_index, question in enumerate(theme.questions, start=1):
                # The pair is globally unique while preserving the formal
                # contract that printed subquestions restart within a theme.
                result[question.key] = f"第{theme_index}题·第{question_index}问"
        return result

    def theme_number(self, key: str) -> int | None:
        for index, theme in enumerate(self.themes, start=1):
            if theme.key == key:
                return index
        return None

    def selected_theme(self) -> ComposerTheme | None:
        return next(
            (theme for theme in self.themes if theme.key == self.selected_theme_key),
            None,
        )

    def selected_question(self) -> ComposerQuestion | None:
        theme = self.selected_theme()
        if theme is None:
            return None
        return next(
            (item for item in theme.questions if item.key == self.selected_question_key),
            None,
        )

    def invalidate_preview(self) -> None:
        self.revision += 1
        self.approved_revision = None
        self.preview_hash = None
        self.preview = None

    def set_mode(self, mode: str) -> None:
        if mode not in {"mock_exam", "daily_practice"} or mode == self.mode:
            return
        self.mode = mode
        self.invalidate_preview()

    def set_show_question_scores(self, value: bool) -> None:
        if type(value) is not bool:
            raise TypeError("show_question_scores must be a bool")
        if value == self.show_question_scores:
            return
        self.show_question_scores = value
        self.invalidate_preview()

    def select_theme(self, key: str) -> None:
        if any(theme.key == key for theme in self.themes):
            self.selected_theme_key = key
            self.selected_question_key = None

    def select_question(self, theme_key: str, question_key: str) -> None:
        if any(
            theme.key == theme_key
            and any(item.key == question_key for item in theme.questions)
            for theme in self.themes
        ):
            self.selected_theme_key = theme_key
            self.selected_question_key = question_key

    def move_theme(self, key: str, delta: int) -> bool:
        index = next((i for i, theme in enumerate(self.themes) if theme.key == key), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(self.themes):
            return False
        self.themes[index], self.themes[target] = self.themes[target], self.themes[index]
        self.invalidate_preview()
        return True

    def move_question(self, theme_key: str, question_key: str, delta: int) -> bool:
        theme = next((item for item in self.themes if item.key == theme_key), None)
        if theme is None:
            return False
        index = next((i for i, item in enumerate(theme.questions) if item.key == question_key), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(theme.questions):
            return False
        theme.questions[index], theme.questions[target] = theme.questions[target], theme.questions[index]
        self.invalidate_preview()
        return True

    def remove_theme(self, key: str) -> bool:
        before = len(self.themes)
        self.themes = [theme for theme in self.themes if theme.key != key]
        if len(self.themes) == before:
            return False
        if self.selected_theme_key == key:
            self.selected_theme_key = self.themes[0].key if self.themes else None
            self.selected_question_key = None
        self.invalidate_preview()
        return True

    def remove_question(self, theme_key: str, question_key: str) -> bool:
        theme = next((item for item in self.themes if item.key == theme_key), None)
        if theme is None:
            return False
        before = len(theme.questions)
        theme.questions = [item for item in theme.questions if item.key != question_key]
        if len(theme.questions) == before:
            return False
        if self.selected_question_key == question_key:
            self.selected_question_key = None
        self.invalidate_preview()
        return True

    def update_theme(self, key: str, **changes: Any) -> bool:
        theme = next((item for item in self.themes if item.key == key), None)
        if theme is None:
            return False
        changed = False
        for field_name in ("title", "source", "chapter", "difficulty", "shared_summary", "shared_text"):
            if field_name in changes and isinstance(changes[field_name], str):
                value = changes[field_name].strip()
                if value != getattr(theme, field_name):
                    setattr(theme, field_name, value)
                    changed = True
        if "time_minutes" in changes:
            fallback = theme.time_minutes if theme.time_minutes is not None else 1
            parsed = _positive_int(changes["time_minutes"], fallback)
            value = max(1, min(300, int(parsed or fallback)))
            if value != theme.time_minutes:
                theme.time_minutes = value
                changed = True
        if changed:
            self.invalidate_preview()
        return changed

    def update_question(self, theme_key: str, question_key: str, **changes: Any) -> bool:
        theme = next((item for item in self.themes if item.key == theme_key), None)
        question = next(
            (item for item in (theme.questions if theme else []) if item.key == question_key),
            None,
        )
        if question is None:
            return False
        changed = False
        text_fields = (
            "response_type",
            "section",
            "difficulty",
            "answer_status",
            "stem",
            "answer",
            "analysis",
            "dependency",
        )
        for field_name in text_fields:
            if field_name in changes and isinstance(changes[field_name], str):
                value = changes[field_name].strip()
                if value != getattr(question, field_name):
                    setattr(question, field_name, value)
                    changed = True
        for field_name, low, high in (("score", 1, 30), ("answer_space", 0, 20)):
            if field_name in changes:
                current = getattr(question, field_name)
                fallback = int(current) if isinstance(current, int) else (1 if low else 0)
                parsed = _positive_int(changes[field_name], fallback)
                value = max(low, min(high, int(parsed if parsed is not None else fallback)))
                if value != getattr(question, field_name):
                    setattr(question, field_name, value)
                    changed = True
        if changed:
            self.invalidate_preview()
        return changed

    def preview_projection(self) -> dict[str, Any]:
        numbers = self.question_numbers()
        themes = [
            theme.public_dict(index, numbers)
            for index, theme in enumerate(self.themes, start=1)
        ]
        return {
            "schema_version": "shchem.desktop-paper-preview.v1",
            "mode": self.mode,
            "mode_label": self.mode_label,
            "layout_kind": "exam" if self.mode == "mock_exam" else "compact_practice",
            "title": self.title.strip() or "未命名组卷",
            "subtitle": self.subtitle.strip(),
            "keywords": self.keywords.strip(),
            "hot_topic": self.hot_topic,
            "show_question_scores": self.show_question_scores,
            "stats": {
                "theme_count": len(self.themes),
                "question_count": self.total_questions,
                "total_score": self.total_score,
                "total_time_minutes": self.total_time,
            },
            "cover": {
                "enabled": self.mode == "mock_exam",
                "identity_fields": ["姓名", "班级"] if self.mode == "mock_exam" else [],
            },
            "themes": themes,
            "export_gate": {
                "preview_required": True,
                "preview_frozen_before_export": True,
                "same_revision_required": True,
                "approved": self.approved_revision == self.revision,
            },
        }

    def make_preview(self) -> dict[str, Any]:
        projection = self.preview_projection()
        # The hash excludes the mutable approval bit so the same paper remains
        # the same snapshot before and after the teacher presses confirm.
        hash_basis = dict(projection)
        hash_basis["export_gate"] = {
            "preview_required": True,
            "preview_frozen_before_export": True,
            "same_revision_required": True,
        }
        digest = _stable_hash(hash_basis)
        projection["preview_snapshot_sha256"] = digest
        self.preview_hash = digest
        self.preview = projection
        return projection

    def approve(self, preview_hash: str | None = None) -> bool:
        if self.preview is None:
            return False
        expected = self.preview_hash
        if preview_hash is not None and preview_hash != expected:
            return False
        self.approved_revision = self.revision
        gate = self.preview.setdefault("export_gate", {})
        if isinstance(gate, dict):
            gate["approved"] = True
        return True

    @property
    def can_export(self) -> bool:
        return bool(
            self.themes
            and self.preview_hash
            and self.approved_revision == self.revision
        )

    def draft_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "title": self.title.strip(),
            "subtitle": self.subtitle.strip(),
            "keywords": self.keywords.strip(),
            "hot_topic": self.hot_topic,
            "show_question_scores": self.show_question_scores,
            "duration_minutes": self.duration_minutes,
            "basket_signature": list(self.basket_signature_value)
            or [theme.source_identity_sha256 or theme.key for theme in self.themes],
            "selected_theme_key": self.selected_theme_key,
            "selected_question_key": self.selected_question_key,
            "themes": [
                {
                    "key": theme.key,
                    "source_identity_sha256": theme.source_identity_sha256,
                    "source_ref": dict(theme.source_ref),
                    "title": theme.title,
                    "source": theme.source,
                    "chapter": theme.chapter,
                    "difficulty": theme.difficulty,
                    "time_minutes": theme.time_minutes,
                    "shared_summary": theme.shared_summary,
                    "shared_text": theme.shared_text,
                    "shared_materials": deepcopy(theme.shared_materials),
                    "questions": [asdict(item) for item in theme.questions],
                }
                for theme in self.themes
            ],
            "revision": self.revision,
            "preview_snapshot_sha256": self.preview_hash,
            "approved_revision": self.approved_revision,
            "expanded_theme_keys": sorted(self.expanded_theme_keys),
            "expanded_question_keys": sorted(self.expanded_question_keys),
        }


__all__ = [
    "ANSWER_STATUS_LABELS",
    "ComposerQuestion",
    "ComposerTheme",
    "PaperComposerModel",
    "RESPONSE_LABELS",
    "answer_status",
    "build_themes_from_basket",
    "difficulty_label",
    "iter_catalog_groups",
    "response_label",
]
