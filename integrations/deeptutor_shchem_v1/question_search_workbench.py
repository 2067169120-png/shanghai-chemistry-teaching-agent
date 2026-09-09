from __future__ import annotations

"""Fast, read-only search over the existing theme-workbench projections.

Search results are deliberately grouped by theme.  Atomic parts are the
matching unit, but a match only highlights an item inside its complete theme
chain; it never turns embedded choices or fills into a synthetic top-level
section.  The reader consumes either the selected frozen release or the live
bootstrap theme projection supplied by :class:`GatewayService`.
"""

import base64
import hashlib
import json
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0.0-question-search-theme-cards"
SCOPE_ORDER = ("master", "wave1", "supplemental")
ALLOWED_SCOPES = frozenset((*SCOPE_ORDER, "all"))
FILTER_KEYS = (
    "K",
    "A",
    "C",
    "R",
    "RP",
    "D",
    "item_type",
    "answer_status",
    "source_tier",
    "year",
    "region",
)
CURRICULUM_SELECTOR_KEYS = (
    "volume_id",
    "chapter_id",
    "section",
    "mapping_status",
)
CURRICULUM_MAPPING_STATUSES = frozenset({"complete", "partial", "blocked"})
CURRICULUM_REASON_CODE = "curriculum_explicit_mapping_match"
CURRICULUM_SOURCE_SCOPES = {
    "master_direct_active": "master",
    "supplemental_wechat_active": "supplemental",
}
FACET_LABELS_ZH = {
    "K": "知识点",
    "A": "能力",
    "C": "情境",
    "R": "作答方式",
    "RP": "信息表征",
    "D": "认知难度",
    "item_type": "题目类型",
    "answer_status": "参考答案状态",
    "source_tier": "来源层级",
    "year": "年份",
    "region": "区域或学校",
}
VALUE_LABELS_ZH = {
    "K01": "化学研究方法、物质分类与计量",
    "K02": "卤素与海洋资源",
    "K03": "硫、氮及其循环",
    "K04": "原子结构和化学键基础",
    "K05": "金属及其化合物",
    "K06": "反应速率与化学平衡基础",
    "K07": "常见有机化合物基础",
    "K08": "化学反应热效应",
    "K09": "反应方向、限度和速率进阶",
    "K10": "水溶液中的离子反应与平衡",
    "K11": "氧化还原与电化学（原电池、电解池、腐蚀与防护）",
    "K12": "原子结构与元素性质进阶",
    "K13": "分子结构、配位与物质性质",
    "K14": "晶体结构与性质",
    "K15": "有机结构、同分异构与命名",
    "K16": "烃和卤代烃",
    "K17": "烃的含氧衍生物",
    "K18": "生物大分子与合成高分子",
    "K19": "有机合成与结构研究",
    "A01": "信息提取与表征转换",
    "A02": "化学用语与规范表达",
    "A03": "结构—性质—用途推理",
    "A04": "定量计算与守恒",
    "A05": "反应原理建模与迁移",
    "A06": "实验探究与证据推理",
    "A07": "工艺流程分析与优化",
    "A08": "有机路线设计与结构推断",
    "A09": "比较评价与开放论证",
    "A10": "综合问题链组织",
    "C01": "实验室研究与测定",
    "C02": "化工生产与流程",
    "C03": "资源开发与冶金",
    "C04": "能源与电化学",
    "C05": "材料与物质结构",
    "C06": "绿色化学与循环经济",
    "C07": "环境与生态",
    "C08": "医药与有机合成",
    "C09": "生命、食品与日常生活",
    "C10": "化学史与社会议题",
    "R01": "客观选择",
    "R02": "短填空",
    "R03": "化学方程式或化学用语",
    "R04": "结构式、同分异构或路线图",
    "R05": "定量计算",
    "R06": "原因解释与论证",
    "R07": "图表读取、绘制或补全",
    "R08": "实验操作、装置与方案设计",
    "R09": "工艺流程补全与条件选择",
    "R10": "比较评价或开放作答",
    "RP01": "连续文字与任务语言",
    "RP02": "化学符号与方程式",
    "RP03": "表格",
    "RP04": "坐标曲线",
    "RP05": "能量图",
    "RP06": "实验装置",
    "RP07": "工艺流程",
    "RP08": "有机结构与路线",
    "RP09": "晶体或微粒模型",
    "RP10": "光谱",
    "RP11": "电化学装置",
    "RP12": "多表征转换",
    "D1": "基础识记与辨认",
    "D2": "直接应用",
    "D3": "模块内综合",
    "D4": "跨模块陌生迁移",
    "D5": "复杂开放与评价",
    "embedded_single_choice": "主题内单项选择",
    "embedded_multiple_choice": "主题内多项选择",
    "embedded_indeterminate_choice": "主题内不定项选择",
    "short_fill": "短填空",
    "chemical_equation_or_notation": "化学方程式或化学用语",
    "organic_structure_or_route": "有机结构或合成路线",
    "quantitative_calculation": "定量计算",
    "reasoned_explanation": "原因解释或证据推理",
    "graph_read_draw_complete": "图表读取、绘制或补全",
    "experiment_operation_apparatus_plan": "实验操作、装置与方案设计",
    "process_flow_condition_choice": "工艺流程与条件选择",
    "comparison_or_open_response": "比较评价或开放作答",
    "present_part_aligned": "有参考答案且已对齐",
    "present_unaligned": "有参考答案但未逐题对齐",
    "absent": "暂无参考答案",
    "per_alias_unit": "按拆分单元分别记录",
    "external_teaching_handout": "外部教学讲义候选层",
    "external_handout": "外部教学讲义",
    "shanghai_exam_wechat_archive": "上海试卷公众号存档",
    "second_mock_nonofficial_attribution": "二模（非官方归类）",
    "unknown": "待补",
}
AUTHORITY = {
    "read_only": True,
    "candidate_only": True,
    "answer_text_excluded": True,
    "local_path_excluded": True,
    "url_excluded": True,
    "student_data_excluded": True,
    "cross_scope_sum_allowed": False,
}

_TOP_LEVEL_KEYS = frozenset(
    {"scope", "q", "filters", "curriculum", "cursor", "limit"}
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_SELECTOR_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_FORBIDDEN_KEYS = frozenset(
    {
        "answer_text",
        "reference_answer_text",
        "question_text",
        "solution_path_zh",
        "source_path",
        "local_path",
        "absolute_path",
        "crop_path",
        "url",
        "source_url",
        "student_id",
        "profile_id",
    }
)
_FORBIDDEN_TEXT = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/])"
)


class QuestionSearchError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _SearchRequest:
    scope: str
    query: str | None
    filters: dict[str, tuple[str, ...]]
    curriculum: dict[str, str] | None
    cursor: str | None
    limit: int


@dataclass(frozen=True)
class _PreparedAtomic:
    source: dict[str, Any]
    values: dict[str, frozenset[str]]
    normalized_text: str


@dataclass(frozen=True)
class _PreparedCard:
    scope: str
    group_kind: str
    paper: dict[str, Any] | None
    theme: dict[str, Any] | None
    shared_context: dict[str, Any] | None
    dependencies: dict[str, Any]
    source_metadata: dict[str, str]
    display_title_zh: str
    normalized_theme_text: str
    atomic_chain: tuple[_PreparedAtomic, ...]


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def _known_year(value: Any) -> str:
    if type(value) is int and 1900 <= value <= 2100:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"(?:19|20|21)[0-9]{2}", value):
        return value
    return "unknown"


def _source_metadata(scope: str, paper: Mapping[str, Any] | None) -> dict[str, str]:
    paper = paper or {}
    metadata = paper.get("source_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}

    # Theme readers own the evidence-bound projection.  Search deliberately
    # does not fall back to paper titles, IDs, filenames, status strings, or
    # loosely related top-level values; an absent structured field stays
    # unknown.
    return {
        "question_bank_layer": scope,
        "source_tier": _safe_string(metadata.get("source_tier")) or "unknown",
        "year": _known_year(metadata.get("year")),
        "region": _safe_string(metadata.get("region")) or "unknown",
        "paper_type": _safe_string(metadata.get("paper_type")) or "unknown",
        "attribution_status": (
            _safe_string(metadata.get("attribution_status")) or "unknown"
        ),
    }


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str) and item}
    return set()


def _atomic_values(
    atom: Mapping[str, Any], source_metadata: Mapping[str, str]
) -> dict[str, frozenset[str]]:
    units: list[Mapping[str, Any]] = [atom]
    aliases = atom.get("alias_units")
    if isinstance(aliases, list):
        units.extend(item for item in aliases if isinstance(item, Mapping))

    values: dict[str, set[str]] = {key: set() for key in FILTER_KEYS}
    for unit in units:
        label = unit.get("label_summary")
        label = label if isinstance(label, Mapping) else {}
        primary = label.get("primary_K")
        if isinstance(primary, str) and primary:
            values["K"].add(primary)
        values["K"].update(_string_values(label.get("supporting_K")))
        for axis in ("A", "C", "R", "RP"):
            values[axis].update(_string_values(label.get(axis)))
        difficulty = label.get("cognitive_prelabel")
        if isinstance(difficulty, str) and difficulty:
            values["D"].add(difficulty)
        item_type = unit.get("item_type")
        if isinstance(item_type, str) and item_type:
            values["item_type"].add(item_type)
        answer = unit.get("answer")
        availability = answer.get("availability") if isinstance(answer, Mapping) else None
        if isinstance(availability, str) and availability:
            values["answer_status"].add(availability)

    for key in ("K", "A", "C", "R", "RP", "D", "item_type", "answer_status"):
        if not values[key]:
            values[key].add("unknown")
    for key in ("source_tier", "year", "region"):
        values[key].add(source_metadata[key])
    return {key: frozenset(item) for key, item in values.items()}


def _atomic_search_text(atom: Mapping[str, Any], values: Mapping[str, frozenset[str]]) -> str:
    fields: list[Any] = [
        atom.get("atomic_part_id"),
        atom.get("printed_question_id"),
        atom.get("printed_question_number"),
        atom.get("visible_summary_zh"),
        atom.get("response_requirement_zh"),
        atom.get("item_type"),
    ]
    aliases = atom.get("alias_units")
    if isinstance(aliases, list):
        for unit in aliases:
            if isinstance(unit, Mapping):
                fields.extend(
                    [
                        unit.get("atomic_part_id"),
                        unit.get("visible_summary_zh"),
                        unit.get("response_requirement_zh"),
                        unit.get("item_type"),
                    ]
                )
    for key in ("K", "A", "C", "R", "RP", "D", "item_type"):
        fields.extend(sorted(values[key]))
        fields.extend(VALUE_LABELS_ZH.get(value, value) for value in values[key])
    return _normalized_text(" ".join(str(value) for value in fields if value is not None))


def _theme_search_text(
    paper: Mapping[str, Any] | None,
    theme: Mapping[str, Any] | None,
    shared_context: Mapping[str, Any] | None,
    source_metadata: Mapping[str, str],
) -> str:
    fields: list[Any] = []
    if paper is not None:
        fields.extend((paper.get("id"), paper.get("title"), paper.get("status")))
    if theme is not None:
        fields.extend((theme.get("id"), theme.get("title")))
    if shared_context is not None:
        fields.append(shared_context.get("context_summary_zh"))
    fields.extend(source_metadata.values())
    return _normalized_text(" ".join(str(value) for value in fields if value is not None))


def _validate_filter_values(key: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        raise QuestionSearchError(
            "question_search_filter_invalid",
            f"filter {key} must be a non-empty array with at most 30 values",
        )
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= 80
            or _CONTROL_CHARACTERS.search(item)
            or item.strip() != item
            or item in result
        ):
            raise QuestionSearchError(
                "question_search_filter_invalid",
                f"filter {key} contains an invalid or duplicate value",
            )
        result.append(item)
    return tuple(result)


def _validate_curriculum_selector(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) - set(CURRICULUM_SELECTOR_KEYS)
    ):
        raise QuestionSearchError(
            "question_search_curriculum_invalid",
            "curriculum must be a strict selector object",
        )
    result: dict[str, str] = {}
    for key in CURRICULUM_SELECTOR_KEYS:
        if key not in value:
            continue
        item = value[key]
        if (
            not isinstance(item, str)
            or not item
            or item.strip() != item
            or _CONTROL_CHARACTERS.search(item)
        ):
            raise QuestionSearchError(
                "question_search_curriculum_invalid",
                f"curriculum {key} is invalid",
            )
        if key == "mapping_status":
            if item not in CURRICULUM_MAPPING_STATUSES:
                raise QuestionSearchError(
                    "question_search_curriculum_invalid",
                    "curriculum mapping_status must be complete, partial, or blocked",
                )
        elif _SAFE_SELECTOR_ID.fullmatch(item) is None:
            raise QuestionSearchError(
                "question_search_curriculum_invalid",
                f"curriculum {key} is invalid",
            )
        result[key] = item
    return result


def _parse_request(payload: Any) -> _SearchRequest:
    if not isinstance(payload, dict) or set(payload) - _TOP_LEVEL_KEYS:
        raise QuestionSearchError(
            "question_search_request_invalid",
            "question search request contains an unsupported field",
        )
    scope = payload.get("scope", "master")
    if scope not in ALLOWED_SCOPES:
        raise QuestionSearchError(
            "question_search_scope_invalid",
            "scope must be master, wave1, supplemental, or all",
        )
    query = payload.get("q")
    if query is not None and (
        not isinstance(query, str)
        or not 1 <= len(query) <= 120
        or query.strip() != query
        or _CONTROL_CHARACTERS.search(query)
    ):
        raise QuestionSearchError(
            "question_search_query_invalid",
            "q must be a trimmed non-empty string with at most 120 characters",
        )
    filters_value = payload.get("filters", {})
    if not isinstance(filters_value, dict) or set(filters_value) - set(FILTER_KEYS):
        raise QuestionSearchError(
            "question_search_filter_invalid",
            "question search filters contain an unsupported axis",
        )
    filters = {
        key: _validate_filter_values(key, filters_value[key])
        for key in FILTER_KEYS
        if key in filters_value
    }
    curriculum = _validate_curriculum_selector(payload.get("curriculum"))
    cursor = payload.get("cursor")
    if cursor is not None and (
        not isinstance(cursor, str)
        or not 1 <= len(cursor) <= 256
        or _CONTROL_CHARACTERS.search(cursor)
    ):
        raise QuestionSearchError(
            "question_search_cursor_invalid", "cursor is invalid"
        )
    limit = payload.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise QuestionSearchError(
            "question_search_limit_invalid", "limit must be an integer from 1 to 50"
        )
    return _SearchRequest(scope, query, filters, curriculum, cursor, limit)


def _curriculum_allowed_atomic_ids(
    value: Any,
) -> tuple[dict[str, frozenset[str]], dict[str, Any], str]:
    if not isinstance(value, dict):
        raise QuestionSearchError(
            "question_search_curriculum_source_invalid",
            "curriculum mapping source is incompatible with question search",
            409,
        )
    items = value.get("items")
    atomic_ids = value.get("atomic_ids")
    resolved_query = value.get("query")
    if (
        not isinstance(items, list)
        or not isinstance(atomic_ids, list)
        or not isinstance(resolved_query, dict)
    ):
        raise QuestionSearchError(
            "question_search_curriculum_source_invalid",
            "curriculum mapping source shape is invalid",
            409,
        )
    allowed: dict[str, set[str]] = {scope: set() for scope in SCOPE_ORDER}
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise QuestionSearchError(
                "question_search_curriculum_source_invalid",
                "curriculum mapping item is invalid",
                409,
            )
        atomic_id = item.get("atomic_id")
        source_layer = item.get("source_layer")
        scope = CURRICULUM_SOURCE_SCOPES.get(source_layer)
        if (
            not isinstance(atomic_id, str)
            or _SAFE_SELECTOR_ID.fullmatch(atomic_id) is None
            or scope is None
            or atomic_id in seen
        ):
            raise QuestionSearchError(
                "question_search_curriculum_source_invalid",
                "curriculum mapping identity or source layer is invalid",
                409,
            )
        seen.add(atomic_id)
        allowed[scope].add(atomic_id)
    if atomic_ids != [item.get("atomic_id") for item in items] or set(atomic_ids) != seen:
        raise QuestionSearchError(
            "question_search_curriculum_source_invalid",
            "curriculum mapping atomic index is inconsistent",
            409,
        )
    frozen = {scope: frozenset(values) for scope, values in allowed.items()}
    return frozen, deepcopy(resolved_query), _canonical_sha256(value)


def _reject_unsafe_projection(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in _FORBIDDEN_KEYS:
                raise QuestionSearchError(
                    "question_search_projection_leak",
                    "search result contains a forbidden field",
                    409,
                )
            _reject_unsafe_projection(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_unsafe_projection(nested)
    elif isinstance(value, str):
        if value.startswith("/api/"):
            return
        if _FORBIDDEN_TEXT.search(value):
            raise QuestionSearchError(
                "question_search_projection_leak",
                "search result contains a local path or URL",
                409,
            )


def _dependency_counts(atoms: tuple[_PreparedAtomic, ...]) -> dict[str, int]:
    result = {
        "independent": 0,
        "shared_material_only": 0,
        "one_prior_part": 0,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "blocked": 0,
        "explicit_prior_edge_count": 0,
    }
    for prepared in atoms:
        dependency = prepared.source.get("dependency")
        dependency = dependency if isinstance(dependency, Mapping) else {}
        kind = dependency.get("kind")
        if kind in result and kind != "explicit_prior_edge_count":
            result[kind] += 1
        else:
            result["blocked"] += 1
        edge_count = dependency.get("explicit_prior_edge_count")
        if type(edge_count) is int and edge_count >= 0:
            result["explicit_prior_edge_count"] += edge_count
    return result


class QuestionSearchWorkbench:
    """Build and cache a compact searchable index per immutable data snapshot."""

    def __init__(self, *, max_cached_scopes: int = 9):
        self.max_cached_scopes = max_cached_scopes
        self._cache: OrderedDict[tuple[str, str], tuple[_PreparedCard, ...]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def _prepare_scope(
        self, scope: str, value: dict[str, Any], snapshot_id: str
    ) -> tuple[_PreparedCard, ...]:
        key = (snapshot_id, scope)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

        if value.get("scope") != scope or not isinstance(value.get("papers"), list):
            raise QuestionSearchError(
                "question_search_source_invalid",
                "theme source is incompatible with question search",
                409,
            )
        cards: list[_PreparedCard] = []
        for paper_group in value["papers"]:
            if not isinstance(paper_group, dict):
                raise QuestionSearchError(
                    "question_search_source_invalid", "theme paper group is invalid", 409
                )
            paper = paper_group.get("paper")
            groups = paper_group.get("theme_groups")
            if not isinstance(paper, dict) or not isinstance(groups, list):
                raise QuestionSearchError(
                    "question_search_source_invalid", "theme paper group is invalid", 409
                )
            metadata = _source_metadata(scope, paper)
            paper_projection = deepcopy(paper)
            paper_projection["source_metadata"] = {
                key: metadata[key]
                for key in (
                    "source_tier",
                    "year",
                    "region",
                    "paper_type",
                    "attribution_status",
                )
            }
            for group in groups:
                if not isinstance(group, dict):
                    raise QuestionSearchError(
                        "question_search_source_invalid", "theme group is invalid", 409
                    )
                theme = group.get("theme")
                shared = group.get("shared_context")
                dependencies = group.get("dependencies")
                chain = group.get("atomic_chain")
                if (
                    not isinstance(theme, dict)
                    or not isinstance(shared, dict)
                    or not isinstance(dependencies, dict)
                    or not isinstance(chain, list)
                    or not chain
                ):
                    raise QuestionSearchError(
                        "question_search_source_invalid", "theme group shape is invalid", 409
                    )
                atoms: list[_PreparedAtomic] = []
                for atom in chain:
                    if not isinstance(atom, dict) or not isinstance(
                        atom.get("atomic_part_id"), str
                    ):
                        raise QuestionSearchError(
                            "question_search_source_invalid", "theme atomic item is invalid", 409
                        )
                    values = _atomic_values(atom, metadata)
                    atoms.append(
                        _PreparedAtomic(
                            source=deepcopy(atom),
                            values=values,
                            normalized_text=_atomic_search_text(atom, values),
                        )
                    )
                title = _safe_string(theme.get("title")) or (
                    f"主题 {theme.get('sequence')}"
                    if type(theme.get("sequence")) is int
                    else "未命名主题"
                )
                cards.append(
                    _PreparedCard(
                        scope=scope,
                        group_kind="theme",
                        paper=paper_projection,
                        theme=deepcopy(theme),
                        shared_context=deepcopy(shared),
                        dependencies=deepcopy(dependencies),
                        source_metadata=metadata,
                        display_title_zh=title,
                        normalized_theme_text=_theme_search_text(
                            paper, theme, shared, metadata
                        ),
                        atomic_chain=tuple(atoms),
                    )
                )

        unassigned = value.get("unassigned_pending_review")
        if isinstance(unassigned, dict) and unassigned.get("atomic_chain"):
            chain = unassigned.get("atomic_chain")
            if not isinstance(chain, list):
                raise QuestionSearchError(
                    "question_search_source_invalid", "unassigned chain is invalid", 409
                )
            metadata = _source_metadata(scope, None)
            atoms = []
            for atom in chain:
                if not isinstance(atom, dict) or not isinstance(
                    atom.get("atomic_part_id"), str
                ):
                    raise QuestionSearchError(
                        "question_search_source_invalid", "unassigned item is invalid", 409
                    )
                values = _atomic_values(atom, metadata)
                atoms.append(
                    _PreparedAtomic(
                        source=deepcopy(atom),
                        values=values,
                        normalized_text=_atomic_search_text(atom, values),
                    )
                )
            # Missing parents are not evidence that these atomics share one
            # theme.  Keep every pending atomic isolated so a hit can never
            # manufacture a 43-part pseudo-theme chain.
            for prepared_atom in atoms:
                prepared_atoms = (prepared_atom,)
                atomic_id = prepared_atom.source["atomic_part_id"]
                cards.append(
                    _PreparedCard(
                        scope=scope,
                        group_kind="unassigned_pending_review",
                        paper=None,
                        theme=None,
                        shared_context=None,
                        dependencies=_dependency_counts(prepared_atoms),
                        source_metadata=metadata,
                        display_title_zh=f"主题待补（父链缺失）·{atomic_id}",
                        normalized_theme_text=_normalized_text(
                            f"主题待补 父链缺失 pending review {atomic_id}"
                        ),
                        atomic_chain=prepared_atoms,
                    )
                )

        prepared = tuple(cards)
        with self._lock:
            self._cache[key] = prepared
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cached_scopes:
                self._cache.popitem(last=False)
        return prepared

    @staticmethod
    def _cursor(offset: int, signature: str) -> str:
        raw = json.dumps(
            {"offset": offset, "signature": signature, "version": 1},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _cursor_offset(cursor: str | None, signature: str) -> int:
        if cursor is None:
            return 0
        try:
            padding = "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise QuestionSearchError(
                "question_search_cursor_invalid", "cursor is malformed"
            ) from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"offset", "signature", "version"}
            or value.get("version") != 1
            or value.get("signature") != signature
            or type(value.get("offset")) is not int
            or not 0 <= value["offset"] <= 1_000_000
        ):
            raise QuestionSearchError(
                "question_search_cursor_stale", "cursor does not match this search"
            )
        return value["offset"]

    def search(
        self,
        payload: dict[str, Any],
        *,
        theme_loader: Callable[[str], dict[str, Any]],
        curriculum_loader: Callable[[dict[str, str]], dict[str, Any]] | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        request = _parse_request(payload)
        curriculum_allowed: dict[str, frozenset[str]] | None = None
        curriculum_resolved_query: dict[str, Any] | None = None
        curriculum_projection_sha256: str | None = None
        if request.curriculum is not None:
            if curriculum_loader is None:
                raise QuestionSearchError(
                    "question_search_curriculum_unavailable",
                    "curriculum selector is unavailable for this search source",
                    503,
                )
            curriculum_value = curriculum_loader(dict(request.curriculum))
            (
                curriculum_allowed,
                curriculum_resolved_query,
                curriculum_projection_sha256,
            ) = _curriculum_allowed_atomic_ids(curriculum_value)
        scopes = SCOPE_ORDER if request.scope == "all" else (request.scope,)
        loaded: dict[str, dict[str, Any]] = {
            scope: theme_loader(scope) for scope in scopes
        }
        effective_snapshot_id = snapshot_id
        if not isinstance(effective_snapshot_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", effective_snapshot_id
        ):
            effective_snapshot_id = _canonical_sha256(
                {
                    "themes": loaded,
                    "curriculum_projection_sha256": curriculum_projection_sha256,
                }
            )
        cards = [
            card
            for scope in scopes
            for card in self._prepare_scope(
                scope, loaded[scope], effective_snapshot_id
            )
        ]
        if curriculum_allowed is not None:
            for scope in scopes:
                occurrences = Counter(
                    prepared.source["atomic_part_id"]
                    for card in cards
                    if card.scope == scope
                    for prepared in card.atomic_chain
                    if prepared.source["atomic_part_id"]
                    in curriculum_allowed[scope]
                )
                if set(occurrences) != set(curriculum_allowed[scope]) or any(
                    count != 1 for count in occurrences.values()
                ):
                    raise QuestionSearchError(
                        "question_search_curriculum_join_invalid",
                        "curriculum mappings do not join exactly once to the selected theme source",
                        409,
                    )

        normalized_query = _normalized_text(request.query)
        terms = tuple(normalized_query.split()) if normalized_query else ()
        requested = {key: frozenset(values) for key, values in request.filters.items()}
        matched_cards: list[dict[str, Any]] = []
        facet_counts = {key: Counter() for key in FILTER_KEYS}
        scope_counts = {
            scope: {
                "theme_cards_scanned": 0,
                "theme_cards_matched": 0,
                "atomic_parts_scanned": 0,
                "atomic_parts_matched": 0,
            }
            for scope in scopes
        }

        for card in cards:
            scope_count = scope_counts[card.scope]
            scope_count["theme_cards_scanned"] += 1
            scope_count["atomic_parts_scanned"] += len(card.atomic_chain)
            details: list[dict[str, Any]] = []
            for prepared in card.atomic_chain:
                atomic_id = prepared.source["atomic_part_id"]
                if (
                    curriculum_allowed is not None
                    and atomic_id not in curriculum_allowed[card.scope]
                ):
                    continue
                if any(
                    not (selected & prepared.values[key])
                    for key, selected in requested.items()
                ):
                    continue
                combined = f"{card.normalized_theme_text} {prepared.normalized_text}"
                if terms and not all(term in combined for term in terms):
                    continue
                reasons: list[str] = []
                if terms:
                    theme_match = all(
                        term in card.normalized_theme_text for term in terms
                    )
                    atomic_match = all(term in prepared.normalized_text for term in terms)
                    reasons.append(
                        "keyword_theme"
                        if theme_match
                        else "keyword_atomic" if atomic_match else "keyword_combined"
                    )
                reasons.extend(f"filter_{key}" for key in requested)
                if curriculum_allowed is not None:
                    reasons.append(CURRICULUM_REASON_CODE)
                if not reasons:
                    reasons.append("scope_default")
                details.append(
                    {"atomic_part_id": atomic_id, "reason_codes": reasons}
                )
                for key in FILTER_KEYS:
                    facet_counts[key].update(prepared.values[key])

            if not details:
                continue
            scope_count["theme_cards_matched"] += 1
            scope_count["atomic_parts_matched"] += len(details)
            matched_cards.append(
                {
                    "scope": card.scope,
                    "group_kind": card.group_kind,
                    "display_title_zh": card.display_title_zh,
                    "paper": deepcopy(card.paper),
                    "theme": deepcopy(card.theme),
                    "source_metadata": dict(card.source_metadata),
                    "counts": {
                        "atomic_total": len(card.atomic_chain),
                        "atomic_matched": len(details),
                        "display_atomic_units": sum(
                            max(1, len(prepared.source.get("alias_units", [])))
                            for prepared in card.atomic_chain
                        ),
                    },
                    "shared_context": deepcopy(card.shared_context),
                    "dependencies": deepcopy(card.dependencies),
                    "matched_atomic_ids": [
                        detail["atomic_part_id"] for detail in details
                    ],
                    "match_details": details,
                    "atomic_chain": [
                        deepcopy(prepared.source) for prepared in card.atomic_chain
                    ],
                }
            )

        signature = _canonical_sha256(
            {
                "scope": request.scope,
                "q": normalized_query or None,
                "filters": {key: list(value) for key, value in request.filters.items()},
                "curriculum": request.curriculum,
                "curriculum_projection_sha256": curriculum_projection_sha256,
                "snapshot_id": effective_snapshot_id,
            }
        )
        offset = self._cursor_offset(request.cursor, signature)
        page_items = matched_cards[offset : offset + request.limit]
        next_offset = offset + len(page_items)
        has_more = next_offset < len(matched_cards)
        facets = {
            key: {
                "label_zh": FACET_LABELS_ZH[key],
                "values": [
                    {
                        "value": value,
                        "label_zh": VALUE_LABELS_ZH.get(value, value),
                        "atomic_count": count,
                    }
                    for value, count in sorted(
                        facet_counts[key].items(), key=lambda item: (-item[1], item[0])
                    )
                ],
            }
            for key in FILTER_KEYS
        }
        response = {
            "schema_version": SCHEMA_VERSION,
            "scope": request.scope,
            "q": request.query,
            "filters": {key: list(value) for key, value in request.filters.items()},
            "data_snapshot_id": effective_snapshot_id,
            "counts": {
                "theme_cards_scanned": sum(
                    item["theme_cards_scanned"] for item in scope_counts.values()
                ),
                "theme_cards_matched": len(matched_cards),
                "atomic_parts_scanned": sum(
                    item["atomic_parts_scanned"] for item in scope_counts.values()
                ),
                "atomic_parts_matched": sum(
                    item["atomic_parts_matched"] for item in scope_counts.values()
                ),
                "returned_theme_cards": len(page_items),
            },
            "scope_counts": scope_counts,
            "page": {
                "limit": request.limit,
                "returned": len(page_items),
                "total_theme_cards": len(matched_cards),
                "has_more": has_more,
                "next_cursor": (
                    self._cursor(next_offset, signature) if has_more else None
                ),
            },
            "facets": facets,
            "items": page_items,
            "authority": dict(AUTHORITY),
            "integrity": {
                "theme_first": True,
                "atomic_matches_are_highlights_only": True,
                "complete_theme_chain_returned": all(
                    item["group_kind"] == "theme" for item in page_items
                ),
                "dependency_context_preserved": True,
                "unassigned_parent_not_guessed": True,
                "answer_text_excluded": True,
                "frozen_release_live_fallback_allowed": False,
            },
        }
        if request.curriculum is not None:
            assert curriculum_allowed is not None
            assert curriculum_resolved_query is not None
            response["curriculum"] = {
                "selector": dict(request.curriculum),
                "resolved_query": curriculum_resolved_query,
                "allowed_atomic_counts_by_scope": {
                    scope: len(curriculum_allowed[scope]) for scope in SCOPE_ORDER
                },
                "matched_atomic_count": response["counts"]["atomic_parts_matched"],
                "reason_code": CURRICULUM_REASON_CODE,
                "explicit_mapping_only": True,
                "knowledge_tag_inference_used": False,
            }
        _reject_unsafe_projection(response)
        return response


__all__ = [
    "ALLOWED_SCOPES",
    "AUTHORITY",
    "CURRICULUM_MAPPING_STATUSES",
    "CURRICULUM_REASON_CODE",
    "CURRICULUM_SELECTOR_KEYS",
    "FACET_LABELS_ZH",
    "FILTER_KEYS",
    "SCHEMA_VERSION",
    "SCOPE_ORDER",
    "VALUE_LABELS_ZH",
    "QuestionSearchError",
    "QuestionSearchWorkbench",
]
