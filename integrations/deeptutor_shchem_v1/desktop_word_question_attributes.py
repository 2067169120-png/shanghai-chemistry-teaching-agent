"""Evidence-labelled personal Word attributes, independent of bank promotion.

Suggestions use question text and visible headings, never a source filename
as the original exam identity. The SQLite store preserves teacher edits and
past versions; no source document or central question record is modified.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from jsonschema import Draft202012Validator

from .question_search_workbench import VALUE_LABELS_ZH

SCHEMA_VERSION = "shchem.personal-word-question-attributes.v1"
RULE_REVISION = "word-attributes-20260912-v2"
UNKNOWN = "unknown"
EXAM_TYPE_LABELS = {
    "unknown": "原考试待确认",
    "first_mock": "一模",
    "second_mock": "二模",
    "grade_exam": "等级考",
    "school_exam": "校考",
    "midterm": "期中",
    "final": "期末",
    "monthly": "月考",
    "gaokao": "高考（不预设地区）",
}
GRADE_LABELS = {"grade_10": "高一", "grade_11": "高二", "grade_12": "高三"}
_STATUSES = [
    "unknown",
    "auto_suggested",
    "source_observed",
    "usage_positioning",
    "teacher_confirmed",
]
_REGIONS = (
    "上海",
    "北京",
    "天津",
    "重庆",
    "浙江",
    "江苏",
    "山东",
    "湖南",
    "湖北",
    "广东",
    "福建",
    "安徽",
    "江西",
    "河南",
    "河北",
    "山西",
    "陕西",
    "四川",
    "云南",
    "贵州",
    "广西",
    "海南",
    "辽宁",
    "吉林",
    "黑龙江",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "内蒙古",
    "西藏",
    "全国",
    "黄浦区",
    "徐汇区",
    "长宁区",
    "静安区",
    "普陀区",
    "虹口区",
    "杨浦区",
    "闵行区",
    "宝山区",
    "嘉定区",
    "浦东新区",
    "金山区",
    "松江区",
    "青浦区",
    "奉贤区",
    "崇明区",
)
# K identifiers/names reuse the installed 19-node taxonomy. These phrases are
# explanatory suggestions, not an assertion of a canonical textbook mapping.
_TERMS = {
    "K01": (
        "物质的量",
        "物质分类",
        "物质的分类",
        "胶体",
        "分散系",
        "气体摩尔体积",
        "物质的量浓度",
    ),
    "K02": ("氯及其", "氯气", "卤素", "海水资源", "海水中的氯"),
    "K03": ("硫及其", "氮及其", "硫的转化", "氮的循环", "二氧化硫", "硝酸"),
    "K04": ("离子键", "共价键", "核外电子", "原子结构示意图"),
    "K05": ("金属通性", "金属及其", "铁及其", "铝及其", "钠及其", "冶炼"),
    "K06": ("可逆反应", "平衡移动", "速率影响因素"),
    "K07": ("乙醇与乙酸", "常见的有机化合物", "有机反应基础"),
    "K08": ("焓变", "热化学方程式", "盖斯定律", "反应热", "燃烧热"),
    "K09": ("平衡常数", "合成氨", "反应方向", "化学反应速率", "熵变"),
    "K10": (
        "水的电离",
        "弱电解质",
        "电离平衡",
        "盐类水解",
        "盐类的水解",
        "沉淀溶解平衡",
        "中和滴定",
        "溶度积",
    ),
    "K11": (
        "氧化还原",
        "原电池",
        "电解池",
        "电极反应",
        "化学电源",
        "腐蚀与防护",
        "电子转移",
    ),
    "K12": ("电子排布", "原子轨道", "原子结构模型", "元素周期律", "元素周期表"),
    "K13": (
        "分子间作用力",
        "杂化",
        "配位",
        "空间构型",
        "分子极性",
        "分子的极性",
        "VSEPR",
    ),
    "K14": ("晶胞", "晶体", "晶格"),
    "K15": ("同分异构", "同系物", "系统命名", "结构表示", "有机化合物的结构"),
    "K16": ("烷烃", "烯烃", "炔烃", "芳香烃", "卤代烃", "饱和烃", "不饱和烃"),
    "K17": ("醇和酚", "醛和酮", "羧酸", "酯化", "含氧衍生物", "官能团转化"),
    "K18": ("糖类", "蛋白质", "核酸", "油脂", "高分子", "加聚", "缩聚"),
    "K19": ("合成路线", "有机合成", "官能团保护", "波谱", "核磁共振", "红外光谱"),
}
# Explicit subject language only. Generic words such as "实验", "水解" and
# "电子" cannot identify a chapter without the qualifying chemical context.
_MORE_TERMS = {
    "K01": (
        "阿伏加德罗",
        "摩尔质量",
        "分离和提纯",
        "分离与提纯",
        "蒸馏",
        "萃取",
        "重结晶",
        "容量瓶",
        "丁达尔",
        "物质的检验",
    ),
    "K02": ("次氯酸", "漂白粉", "溴和碘", "卤离子"),
    "K03": ("浓硫酸", "硫化氢", "氮氧化物", "铵盐", "氨气"),
    "K04": (
        "同位素",
        "核素",
        "质量数",
        "质子数",
        "中子数",
        "电子式",
        "离子化合物",
        "共价化合物",
    ),
    "K05": (
        "过氧化钠",
        "氢氧化铝",
        "氧化铝",
        "氢氧化铁",
        "氢氧化亚铁",
        "铁离子",
        "亚铁离子",
        "铝热反应",
    ),
    "K06": ("勒夏特列", "勒沙特列", "平衡标志"),
    "K08": ("中和热", "反应的热效应", "反应热效应", "燃烧焓"),
    "K09": ("活化能", "反应机理", "反应历程", "平衡转化率", "反应商", "分压平衡常数"),
    "K10": (
        "强弱电解质",
        "电离常数",
        "电离度",
        "同离子效应",
        "溶液的酸碱性",
        "溶液酸碱性",
        "质子守恒",
        "水解常数",
        "沉淀转化",
        "酸碱滴定",
    ),
    "K11": (
        "氧化性",
        "还原性",
        "电子守恒",
        "放电顺序",
        "氧化剂",
        "还原剂",
        "氧化产物",
        "还原产物",
        "双线桥",
        "单线桥",
        "氧化反应",
        "还原反应",
        "电解反应",
        "电化学腐蚀",
        "牺牲阳极",
    ),
    "K12": (
        "电负性",
        "电离能",
        "泡利",
        "洪特",
        "构造原理",
        "未成对电子",
        "价层电子",
        "价电子排布",
        "能级",
        "电子云",
    ),
    "K13": (
        "氢键",
        "范德华力",
        "手性",
        "σ键",
        "π键",
        "键角",
        "配体",
        "配位数",
        "超分子",
        "价层电子对互斥",
    ),
    "K14": ("晶体密度", "堆积方式"),
    "K15": ("同分异构体", "官能团的名称", "官能团名称"),
    "K16": ("脂肪烃", "苯的同系物", "消去反应"),
    "K17": ("银镜反应", "醛基", "酚羟基", "醇羟基", "酯基", "酰胺", "羧基"),
    "K18": ("多糖", "氨基酸", "肽键", "聚合反应", "合成纤维", "合成橡胶"),
    "K19": ("质谱", "核磁共振氢谱", "元素分析", "有机物的分离"),
}

# A target must exist with this exact title in the supplied directory. These
# aliases are curriculum-navigation suggestions, not inferred source identity.
_SECTION_ALIASES = {
    "物质的分类": ("胶体", "分散系", "丁达尔"),
    "物质的量": ("阿伏加德罗", "摩尔质量", "气体摩尔体积"),
    "化学中常用的实验方法": ("蒸馏", "萃取", "重结晶", "容量瓶"),
    "海水中的氯": ("氯气", "次氯酸", "漂白粉"),
    "氧化还原反应和离子反应": ("离子方程式", "离子共存"),
    "硫及其重要化合物": ("二氧化硫", "浓硫酸", "硫化氢"),
    "氮及其重要化合物": ("氨气", "铵盐", "硝酸", "氮氧化物"),
    "原子结构": ("核素", "同位素", "质量数", "质子数", "中子数"),
    "化学键": ("离子键", "共价键", "电子式"),
    "重要的金属化合物": ("过氧化钠", "氢氧化铝", "氢氧化铁", "氢氧化亚铁"),
    "反应热的测量和计算": ("盖斯定律", "中和热", "燃烧热", "热化学方程式"),
    "化学反应的方向": ("熵变", "自发反应", "反应的自发性"),
    "化学反应的限度": ("平衡常数", "平衡转化率", "反应商"),
    "化学反应的速率": ("活化能", "反应机理", "反应历程"),
    "弱电解质的电离平衡": ("弱电解质", "电离常数", "电离度", "同离子效应"),
    "酸碱中和与盐类水解": (
        "中和滴定",
        "酸碱滴定",
        "盐类水解",
        "盐类的水解",
        "水解常数",
    ),
    "难溶电解质的沉淀溶解平衡": ("溶度积", "沉淀溶解平衡", "沉淀转化"),
    "氧化还原反应": ("氧化剂", "还原剂", "氧化产物", "还原产物", "双线桥", "单线桥"),
    "原电池和化学电源": ("原电池", "燃料电池", "充放电"),
    "电解池": ("电解反应", "电解原理"),
    "金属的电化学腐蚀与防护": ("电化学腐蚀", "牺牲阳极", "外加电流的阴极保护"),
    "多电子原子核外电子的排布": (
        "电子排布式",
        "轨道表示式",
        "泡利",
        "洪特",
        "构造原理",
        "未成对电子",
    ),
    "元素周期律": ("电负性", "电离能"),
    "共价分子的空间结构": ("杂化", "VSEPR", "价层电子对互斥", "键角"),
    "分子结构与物质的性质": ("氢键", "范德华力", "分子极性", "分子的极性", "手性"),
    "配位化合物和超分子": ("配位", "配体", "超分子"),
    "有机化合物的结构": ("同分异构", "官能团名称", "官能团的名称"),
    "有机化合物的命名": ("系统命名",),
    "脂肪烃": ("烷烃", "烯烃", "炔烃"),
    "芳香烃": ("苯的同系物",),
    "醇和酚": ("醇羟基", "酚羟基"),
    "醛和酮": ("醛基", "银镜反应"),
    "羧酸及其衍生物": ("羧基", "酯基", "酯化", "酰胺"),
    "生物大分子": ("氨基酸", "肽键", "蛋白质", "核酸"),
    "合成高分子": ("加聚", "缩聚", "合成纤维", "合成橡胶"),
    "有机合成初步": ("合成路线", "官能团保护"),
    "研究有机化合物的一般方法": ("核磁共振", "红外光谱", "质谱", "元素分析"),
}
_FORMS = (
    (
        "chemical_equation_or_notation",
        "方程式或化学用语",
        r"(?:写出|书写|配平)[^。；\n]{0,45}(?:方程式|电子式|化学式)",
    ),
    (
        "organic_structure_or_route",
        "有机结构或路线",
        r"(?:写出|画出|书写|设计)[^。；\n]{0,45}(?:结构简式|结构式|合成路线)",
    ),
    (
        "quantitative_calculation",
        "定量计算",
        r"计算|求(?:出)?[^。；\n]{0,20}(?:质量|浓度|体积|转化率|产率)",
    ),
    (
        "reasoned_explanation",
        "原因解释与论证",
        r"解释|说明理由|原因是|原因[：:]|说明[^。；\n]{0,20}原因",
    ),
    (
        "experiment_operation_apparatus_plan",
        "实验操作与方案",
        r"(?:设计|改进)[^。；\n]{0,30}(?:实验|方案)|实验操作|检验方法",
    ),
    (
        "graph_read_draw_complete",
        "图表读取与绘制",
        r"(?:画出|绘制|补全)[^。；\n]{0,30}(?:曲线|图像|图表)",
    ),
    ("short_fill", "填空", r"_{2,}|填空|填入"),
)


class WordQuestionAttributeError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message_zh = message
        self.code = "word_question_attributes_invalid"


def _object(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array(items, maximum=100):
    return {"type": "array", "items": items, "maxItems": maximum}


def _text(maximum=500):
    return {"type": "string", "maxLength": maximum}


_EVIDENCE = _object(
    {
        "kind": {
            "enum": [
                "question_text",
                "source_chapter",
                "shared_context",
                "source_metadata",
                "teacher_note",
                "model_image_observation",
            ]
        },
        "quote": _text(),
        "block_index": {"type": ["integer", "null"], "minimum": 1},
    }
)
_FACT = _object(
    {"value": _text(), "status": {"enum": _STATUSES}, "evidence": _array(_EVIDENCE, 20)}
)
_KNOWLEDGE = _object(
    {
        "id": {"type": "string", "pattern": r"^(?:K(?:0[1-9]|1[0-9])|unknown)$"},
        "label": _text(),
        "status": {"enum": _STATUSES},
        "evidence": _array(_EVIDENCE, 20),
    }
)
_SOURCE = _object(
    {
        name: _text()
        for name in (
            "collection_name",
            "package_id",
            "source_name",
            "document_role",
            "lecture_topic",
            "source_chapter",
            "usage_context",
        )
    }
)
_ORIGINAL = _object(
    {
        **{name: _FACT for name in ("grade", "exam_type", "year", "region")},
        "citation_quotes": _array(_EVIDENCE, 20),
        "display_label": _text(),
    }
)
_CURRICULUM = _object(
    {
        **{
            name: _text()
            for name in ("section_key", "chapter_id", "volume_id", "label")
        },
        "status": {"enum": _STATUSES},
        "evidence": _array(_EVIDENCE, 20),
    }
)
ATTRIBUTE_SCHEMA = _object(
    {
        "schema_version": {"const": SCHEMA_VERSION},
        "rule_revision": _text(),
        "key": {"type": "string", "minLength": 1, "maxLength": 160},
        "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        **{
            name: _text()
            for name in (
                "source_revision",
                "question_revision",
                "index_revision",
                "extraction_revision",
            )
        },
        "source": _SOURCE,
        "primary_knowledge": _KNOWLEDGE,
        "supporting_knowledge": _array(_KNOWLEDGE, 19),
        "curriculum_candidates": _array(_CURRICULUM, 60),
        "curriculum_status": {
            "enum": ["pending_mapping", "auto_suggested", "teacher_confirmed"]
        },
        "applicable_grades": _object(
            {
                "values": _array({"enum": ["grade_10", "grade_11", "grade_12"]}, 3),
                "basis": _text(),
                "status": {"enum": _STATUSES},
                "evidence": _array(_EVIDENCE, 20),
            }
        ),
        "original_source": _ORIGINAL,
        "response_forms": _array(
            _object(
                {
                    "id": _text(),
                    "label": _text(),
                    "status": {"enum": _STATUSES},
                    "evidence": _array(_EVIDENCE, 20),
                }
            ),
            12,
        ),
        "answer_status": _FACT,
        "material_status": _object(
            {
                "has_shared_context": {"type": "boolean"},
                "context_block_indices": _array(
                    {"type": "integer", "minimum": 1}, 10000
                ),
                "question_image_count": {"type": "integer", "minimum": 0},
                "answer_image_count": {"type": "integer", "minimum": 0},
                "missing_visual": {"type": "boolean"},
                "missing_context": {"type": "boolean"},
                "warnings": _array(_text(2000), 1000),
            }
        ),
        "annotation_source": {"enum": ["auto_suggested", "teacher_modified"]},
        "teacher_note": _text(2000),
        "edit_version": {"type": "integer", "minimum": 0},
        "revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
)
_VALIDATOR = Draft202012Validator(ATTRIBUTE_SCHEMA)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _seal(value):
    value = deepcopy(value)
    value.pop("revision", None)
    value["revision"] = _digest(value)
    return value


def validate_attributes(value):
    if not isinstance(value, Mapping) or list(_VALIDATOR.iter_errors(value)):
        raise WordQuestionAttributeError("题目属性字段不完整或格式不正确。")
    row = deepcopy(dict(value))
    if row != _seal(row):
        raise WordQuestionAttributeError("题目属性内容与版本不一致。")
    return row


def _evidence(kind, quote, index=None):
    return {"kind": kind, "quote": quote[:500], "block_index": index}


def _fact(value=UNKNOWN, evidence=None, status=None):
    return {
        "value": value,
        "status": status or ("source_observed" if value != UNKNOWN else UNKNOWN),
        "evidence": evidence or [],
    }


def _segments(question):
    result = []
    for field, kind in (
        ("question_blocks", "question_text"),
        ("context_blocks", "shared_context"),
    ):
        for block in question.get(field, []):
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                index = block.get("index")
                result.append(
                    (
                        kind,
                        block["text"],
                        index if type(index) is int and index > 0 else None,
                    )
                )
    chapter = str(question.get("chapter") or "")
    if chapter:
        result.append(("source_chapter", chapter, None))
    return result


def _find(term, segments):
    found = []
    for kind, text, index in segments:
        for match in re.finditer(re.escape(term), text, re.IGNORECASE):
            before = text[max(0, match.start() - 12) : match.start()]
            if term == "饱和烃" and before.endswith("不"):
                continue
            if term == "平衡常数" and before.endswith(
                ("电离", "水解", "溶解", "络合", "配位")
            ):
                continue
            found.append(
                _evidence(
                    kind, text[max(0, match.start() - 25) : match.end() + 35], index
                )
            )
            break
    return found[:20]


def load_attribute_catalog(workspace_root):
    """Read only the established taxonomy and textbook directory, once per caller."""
    root = Path(workspace_root)
    taxonomy = json.loads(
        (root / "sh-chem-db/kb/knowledge_taxonomy.json").read_text(encoding="utf-8")
    )
    directory = json.loads(
        (
            root
            / "sh-chem-db/kb/classification/supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "knowledge_points": taxonomy["dimensions"]["knowledge_points"],
        "nodes": directory["nodes"],
    }


def _catalog_entries(value):
    if value is None:
        return [], []
    if isinstance(value, list):
        return value, []
    if not isinstance(value, Mapping):
        raise WordQuestionAttributeError("教材目录候选格式不正确。")
    nodes = value.get("nodes")
    if nodes is None:
        nodes = [
            {
                **section,
                "chapter_id": chapter.get("chapter_id", ""),
                "volume_id": volume.get("volume_id", ""),
            }
            for volume in value.get("volumes", [])
            for chapter in volume.get("chapters", [])
            for section in chapter.get("sections", [])
        ]
    return nodes, value.get("knowledge_points", [])


def _knowledge(segments, taxonomy):
    candidates = []
    # Options are useful supporting search tags, but an incidental distractor
    # alone is not enough to decide the question's primary knowledge point.
    prompt_segments = []
    for kind, content, index in segments:
        if kind == "question_text":
            content = re.split(
                r"(?:^|\n)\s*[A-D][.．、:：]|\s{2,}[A-D][.．、:：]",
                content,
                maxsplit=1,
            )[0]
        prompt_segments.append((kind, content, index))
    by_id = {row.get("id"): row for row in taxonomy if isinstance(row, Mapping)}
    for identifier, terms in _TERMS.items():
        node = by_id.get(identifier, {})
        phrases = (
            set(terms)
            | set(_MORE_TERMS.get(identifier, ()))
            | {
                p
                for p in node.get("subtopics", [])
                if isinstance(p, str) and len(p) >= 3
            }
        )
        if identifier == "K02":
            phrases.discard("氧化还原")
        if identifier == "K19":
            phrases.discard("分离提纯")
        matches = [(term, _find(term, segments)) for term in sorted(phrases)]
        matches = [(term, evidence) for term, evidence in matches if evidence]
        if matches:
            evidence = list(
                {_digest(e): e for _, found in matches for e in found}.values()
            )[:20]
            priorities = {"question_text": 3, "shared_context": 2, "source_chapter": 1}
            prompt_hits = [
                e for term, _ in matches for e in _find(term, prompt_segments)
            ]
            priority = max((priorities[e["kind"]] for e in prompt_hits), default=0)
            score = sum(
                len(term)
                for term, found in matches
                if not priority or any(priorities[e["kind"]] == priority for e in found)
            )
            candidates.append(
                (
                    priority,
                    score,
                    {
                        "id": identifier,
                        "label": node.get("name") or VALUE_LABELS_ZH[identifier],
                        "status": "auto_suggested",
                        "evidence": evidence,
                    },
                )
            )
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]["id"]))
    values = [item[2] for item in candidates]
    if candidates and candidates[0][0] == 0:
        return {
            "id": UNKNOWN,
            "label": "仅选项含知识线索，主考点待确认",
            "status": UNKNOWN,
            "evidence": [],
        }, values
    if not values:
        # Basic electrolyte language alone does not establish advanced ionic
        # equilibrium or a particular canonical textbook chapter.
        hits = _find("电解质", segments) or _find("电离", segments)
        label = "电解质与电离" if hits else "知识主题待标记"
        values = [
            {
                "id": UNKNOWN,
                "label": label,
                "status": "auto_suggested" if hits else UNKNOWN,
                "evidence": hits,
            }
        ]
    return values[0], values[1:]


def _original_source(segments):
    citations = []
    for kind, text, index in segments:
        if kind != "question_text":
            continue
        # Use source-like, bracketed attributions in the question, not chapter
        # names, package names, solution text, or free numbers in calculations.
        for match in re.finditer(
            r"[（(【\[]([^（）()【】\[\]\n]{2,140})[）)】\]]", text
        ):
            quote = match.group(0)
            if re.search(
                r"高[一二三]|一模|二模|等级考|校考|期中|期末|月考|统考|高考", quote
            ):
                citations.append(_evidence(kind, quote, index))
    citations = list({_digest(e): e for e in citations}.values())[:20]
    fields = {name: [] for name in ("grade", "exam_type", "year", "region")}
    for evidence in citations:
        text = evidence["quote"]
        for grade, value in (
            ("高一", "grade_10"),
            ("高二", "grade_11"),
            ("高三", "grade_12"),
        ):
            if grade in text:
                fields["grade"].append((value, evidence))
        exams = (
            ("一模", "first_mock"),
            ("二模", "second_mock"),
            ("等级考", "grade_exam"),
            ("期中", "midterm"),
            ("期末", "final"),
            ("月考", "monthly"),
            ("校考", "school_exam"),
            ("高考", "gaokao"),
        )
        for token, value in exams:
            if token in text:
                fields["exam_type"].append((value, evidence))
                break
        for match in re.finditer(r"(?<!\d)(20\d{2})(?!\d|届|学年)", text):
            fields["year"].append((match.group(1), evidence))
        places = [name for name in _REGIONS if name in text]
        if places:
            fields["region"].append(("·".join(places), evidence))
    result = {"citation_quotes": citations}
    for name, rows in fields.items():
        values = {value for value, _ in rows}
        result[name] = (
            _fact(next(iter(values)), [e for _, e in rows])
            if len(values) == 1
            else _fact(evidence=[e for _, e in rows])
        )
    result["display_label"] = _original_label(result)
    return result


def _original_label(original):
    labels = {
        "first_mock": "一模",
        "second_mock": "二模",
        "grade_exam": "等级考",
        "school_exam": "校考",
        "midterm": "期中",
        "final": "期末",
        "monthly": "月考",
        "gaokao": "高考",
    }
    exam = original["exam_type"]["value"]
    if exam == UNKNOWN:
        return "讲义收录题·原考试待确认"
    parts = [
        original[name]["value"]
        for name in ("year", "region")
        if original[name]["value"] != UNKNOWN
    ]
    return "讲义收录题·" + "·".join([*parts, labels.get(exam, exam)])


def suggest_attributes(question, source_metadata, curriculum_entries=None):
    """Return deterministic suggestions; unknown facts never block saving."""
    if not isinstance(question, Mapping) or not isinstance(source_metadata, Mapping):
        raise WordQuestionAttributeError("题目与来源元数据格式不正确。")
    segments = _segments(question)
    nodes, taxonomy = _catalog_entries(curriculum_entries)
    primary, supporting = _knowledge(segments, taxonomy)
    source = {
        name: str(source_metadata.get(name) or "") for name in _SOURCE["properties"]
    }
    source["source_name"] = Path(
        source["source_name"]
        or str(
            question.get("source_name") or question.get("source_label") or "Word来源"
        )
    ).name
    source["source_chapter"] = str(question.get("chapter") or "")
    mappings = []
    for node in nodes:
        title = node.get("section_title") if isinstance(node, Mapping) else None
        if not isinstance(title, str) or len(title) < 3:
            continue
        evidence = _find(title, segments)
        # Alias evidence is only allowed in this question/shared material.
        # A broad lecture heading alone cannot attach a more specific section.
        for term in _SECTION_ALIASES.get(title, ()):
            evidence.extend(
                _find(term, [s for s in segments if s[0] != "source_chapter"])
            )
        evidence = list({_digest(e): e for e in evidence}.values())[:20]
        key = node.get("node_key") or node.get("section_key")
        if evidence and key and node.get("chapter_id") and node.get("volume_id"):
            mappings.append(
                {
                    "section_key": str(key),
                    "chapter_id": str(node["chapter_id"]),
                    "volume_id": str(node["volume_id"]),
                    "label": title,
                    "status": "auto_suggested",
                    "evidence": evidence,
                }
            )
    mappings = list({row["section_key"]: row for row in mappings}.values())
    usage = source["usage_context"]
    grades = [
        value
        for token, value in (
            ("高一", "grade_10"),
            ("高二", "grade_11"),
            ("高三", "grade_12"),
        )
        if token in usage
    ]
    forms = []
    for identifier, label, pattern in _FORMS:
        matches = [
            _evidence(kind, m.group(0), index)
            for kind, text, index in segments
            if kind == "question_text"
            for m in [re.search(pattern, text)]
            if m
        ]
        if matches:
            forms.append(
                {
                    "id": identifier,
                    "label": label,
                    "status": "auto_suggested",
                    "evidence": matches[:20],
                }
            )
    question_text = "\n".join(
        text for kind, text, _ in segments if kind == "question_text"
    )
    if all(
        re.search(rf"(?:^|\s){letter}[.．、:：）)]", question_text) for letter in "ABCD"
    ):
        forms.insert(
            0,
            {
                "id": "embedded_choice",
                "label": "选择（单多选待核对）",
                "status": "auto_suggested",
                "evidence": [_evidence("question_text", question_text[:400])],
            },
        )
    qblocks, answers, context = [
        list(question.get(name) or [])
        for name in ("question_blocks", "answer_blocks", "context_blocks")
    ]

    def image_count(blocks):
        return sum(len(block.get("assets") or []) for block in blocks)

    warnings = list(dict.fromkeys(str(w) for w in question.get("warnings", [])))
    missing_visual = bool(
        re.search(r"如图|下图|图中|装置图|结构简式如下", question_text)
        and not image_count(qblocks + context)
    )
    missing_context = not context and bool(
        re.search(r"前题|上一题|上述材料|根据上述", question_text)
        or any("共同材料" in w for w in warnings)
    )
    answer_evidence = [
        _evidence(
            "question_text", str(block.get("text") or "原文答案图"), block.get("index")
        )
        for block in answers[:3]
    ]
    row = {
        "schema_version": SCHEMA_VERSION,
        "rule_revision": RULE_REVISION,
        "key": question.get("key"),
        "source_sha256": question.get("source_sha256"),
        **{
            name: str(question.get(name) or UNKNOWN)
            for name in ("source_revision", "index_revision", "extraction_revision")
        },
        "question_revision": str(question.get("revision") or UNKNOWN),
        "source": source,
        "primary_knowledge": primary,
        "supporting_knowledge": supporting,
        "curriculum_candidates": mappings,
        "curriculum_status": "auto_suggested" if mappings else "pending_mapping",
        "applicable_grades": {
            "values": grades,
            "basis": "资料包使用定位；不等于原题年级" if grades else "适用年级待确认",
            "status": "usage_positioning" if grades else UNKNOWN,
            "evidence": [_evidence("source_metadata", usage)] if grades else [],
        },
        "original_source": _original_source(segments),
        "response_forms": forms,
        "answer_status": _fact(
            "present_nonofficial_unverified"
            if answers
            else "not_detected_pending_review",
            answer_evidence,
            "auto_suggested",
        ),
        "material_status": {
            "has_shared_context": bool(context),
            "context_block_indices": [block["index"] for block in context],
            "question_image_count": image_count(qblocks + context),
            "answer_image_count": image_count(answers),
            "missing_visual": missing_visual,
            "missing_context": missing_context,
            "warnings": warnings,
        },
        "annotation_source": "auto_suggested",
        "teacher_note": "",
        "edit_version": 0,
    }
    return validate_attributes(_seal(row))


def complete_missing_attributes(existing, proposed):
    """Fill only empty knowledge/mapping fields; keep teacher and pinned rows.

    No answers, original exam facts, existing labels or source locators change.
    Callers must revalidate sources and hold their normal transaction boundary.
    """
    old, new = validate_attributes(existing), validate_attributes(proposed)
    if any(
        old[key] != new[key]
        for key in (
            "key",
            "source_sha256",
            "source_revision",
            "question_revision",
            "index_revision",
            "extraction_revision",
        )
    ):
        raise WordQuestionAttributeError("补标签前题目范围或来源已变化，请重新核对。")
    if (
        old["annotation_source"] == "teacher_modified"
        or "pinned" in old["rule_revision"]
    ):
        return old
    result = deepcopy(old)
    if (
        old["primary_knowledge"]["id"] == UNKNOWN
        and new["primary_knowledge"]["id"] != UNKNOWN
    ):
        result["primary_knowledge"] = deepcopy(new["primary_knowledge"])
        result["supporting_knowledge"] = [
            row
            for row in result["supporting_knowledge"]
            if row["id"] != new["primary_knowledge"]["id"]
        ]
    if not old["curriculum_candidates"] and new["curriculum_candidates"]:
        result["curriculum_candidates"] = deepcopy(new["curriculum_candidates"])
        result["curriculum_status"] = new["curriculum_status"]
    if result == old:
        return old
    result["rule_revision"] = new["rule_revision"]
    return validate_attributes(_seal(result))


def build_teacher_updates(attributes, selections, curriculum_entries):
    """Build only changed teaching fields from real catalogue selections.

    This is a local edit proposal, not chemistry verification. No catalogue ID
    or original exam attribution is inferred from user-entered prose.
    """
    row = validate_attributes(attributes)
    expected = {
        "primary_knowledge_id",
        "supporting_knowledge_ids",
        "applicable_grades",
        "original_exam_type",
        "curriculum_section_keys",
        "teacher_note",
    }
    if not isinstance(selections, Mapping) or set(selections) != expected:
        raise WordQuestionAttributeError("教学标签编辑字段不完整。")
    nodes, taxonomy = _catalog_entries(curriculum_entries)
    knowledge = {entry["id"]: entry for entry in taxonomy}
    sections = {entry["node_key"]: entry for entry in nodes}
    primary = selections["primary_knowledge_id"]
    supporting = selections["supporting_knowledge_ids"]
    grades = selections["applicable_grades"]
    section_keys = selections["curriculum_section_keys"]
    exam = selections["original_exam_type"]
    note = selections["teacher_note"]
    for values in (supporting, grades, section_keys):
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise WordQuestionAttributeError("教学标签选项格式不正确。")
        if len(values) != len(set(values)):
            raise WordQuestionAttributeError("教学标签选项不能重复。")
    if not isinstance(primary, str) or primary not in {UNKNOWN, *knowledge}:
        raise WordQuestionAttributeError("请从现有知识目录选择主知识点。")
    if any(value not in knowledge for value in supporting) or primary in supporting:
        raise WordQuestionAttributeError("辅助知识点须来自目录且不能重复主知识点。")
    if any(value not in GRADE_LABELS for value in grades):
        raise WordQuestionAttributeError("适用年级选项不正确。")
    if any(value not in sections for value in section_keys):
        raise WordQuestionAttributeError("请从现有教材目录选择节；不明确时保留待映射。")
    if not isinstance(exam, str) or exam not in EXAM_TYPE_LABELS:
        raise WordQuestionAttributeError("原考试类型选项不正确。")
    if not isinstance(note, str) or len(note) > 2000:
        raise WordQuestionAttributeError("教师备注最多 2000 字。")
    evidence = [_evidence("teacher_note", note.strip() or "教师在本机修订教学标签。")]
    updates = {}

    def knowledge_row(identifier):
        return {
            "id": identifier,
            "label": knowledge[identifier]["name"]
            if identifier != UNKNOWN
            else "知识主题待标记",
            "status": "teacher_confirmed" if identifier != UNKNOWN else UNKNOWN,
            "evidence": deepcopy(evidence),
        }

    if primary != row["primary_knowledge"]["id"]:
        updates["primary_knowledge"] = knowledge_row(primary)
    if set(supporting) != {item["id"] for item in row["supporting_knowledge"]}:
        previous = {item["id"]: item for item in row["supporting_knowledge"]}
        updates["supporting_knowledge"] = [
            deepcopy(previous[value]) if value in previous else knowledge_row(value)
            for value in supporting
        ]
    if set(grades) != set(row["applicable_grades"]["values"]):
        updates["applicable_grades"] = {
            "values": grades,
            "basis": "教师按本次教学用途修订；不等于原题年级。"
            if grades
            else "适用年级待确认。",
            "status": "teacher_confirmed" if grades else UNKNOWN,
            "evidence": deepcopy(evidence),
        }
    if exam != row["original_source"]["exam_type"]["value"]:
        original = deepcopy(row["original_source"])
        original["exam_type"] = _fact(
            exam,
            deepcopy(evidence),
            "teacher_confirmed" if exam != UNKNOWN else UNKNOWN,
        )
        original["display_label"] = _original_label(original)
        updates["original_source"] = original
    if set(section_keys) != {
        item["section_key"] for item in row["curriculum_candidates"]
    }:
        updates["curriculum_candidates"] = [
            {
                "section_key": key,
                "chapter_id": sections[key]["chapter_id"],
                "volume_id": sections[key]["volume_id"],
                "label": sections[key]["section_title"],
                "status": "teacher_confirmed",
                "evidence": deepcopy(evidence),
            }
            for key in section_keys
        ]
        updates["curriculum_status"] = (
            "teacher_confirmed" if section_keys else "pending_mapping"
        )
    if note.strip() != row["teacher_note"]:
        updates["teacher_note"] = note.strip()
    if updates:
        apply_teacher_edits(row, updates, curriculum_entries=curriculum_entries)
    return updates


def apply_teacher_edits(attributes, updates, *, curriculum_entries=None):
    """Pure versioned edit, shared by comparison preview and atomic persistence."""
    row = validate_attributes(attributes)
    allowed = {
        "primary_knowledge",
        "supporting_knowledge",
        "curriculum_candidates",
        "curriculum_status",
        "applicable_grades",
        "original_source",
        "response_forms",
        "teacher_note",
    }
    if not isinstance(updates, Mapping) or not updates or set(updates) - allowed:
        raise WordQuestionAttributeError("只能修改个人教学标签，不能改写题目来源。")
    if "original_source" in updates and (
        not isinstance(updates["original_source"], Mapping)
        or updates["original_source"].get("citation_quotes")
        != row["original_source"]["citation_quotes"]
    ):
        raise WordQuestionAttributeError("原题引文须保留，教师说明请填写备注。")

    def confirmed(value, old=None):
        if value == old:
            return deepcopy(value)
        if isinstance(value, dict):
            result = {
                name: confirmed(item, old.get(name) if isinstance(old, dict) else None)
                for name, item in value.items()
            }
            if "status" in result:
                unknown = result.get("value") == UNKNOWN or (
                    result.get("id") == UNKNOWN and value.get("status") == UNKNOWN
                )
                unknown = unknown or ("values" in result and not result["values"])
                result["status"] = UNKNOWN if unknown else "teacher_confirmed"
            return result
        if isinstance(value, list):
            return [
                confirmed(
                    item,
                    next((v for v in old if v == item), None)
                    if isinstance(old, list)
                    else None,
                )
                for item in value
            ]
        return deepcopy(value)

    for name, value in updates.items():
        row[name] = confirmed(value, row.get(name))
    if "original_source" in updates:
        row["original_source"]["display_label"] = _original_label(
            row["original_source"]
        )
    row.update(
        annotation_source="teacher_modified", edit_version=row["edit_version"] + 1
    )
    row = validate_attributes(_seal(row))
    if curriculum_entries is not None:
        nodes, taxonomy = _catalog_entries(curriculum_entries)
        knowledge = {entry["id"]: entry["name"] for entry in taxonomy}
        sections = {entry["node_key"]: entry for entry in nodes}
        for field in ("primary_knowledge", "supporting_knowledge"):
            if field not in updates:
                continue
            entries = [row[field]] if field == "primary_knowledge" else row[field]
            for entry in entries:
                if (
                    entry["id"] != UNKNOWN
                    and knowledge.get(entry["id"]) != entry["label"]
                ):
                    raise WordQuestionAttributeError(
                        "知识标签必须与现有目录的编号和名称一致。"
                    )
        if "curriculum_candidates" in updates:
            for entry in row["curriculum_candidates"]:
                node = sections.get(entry["section_key"], {})
                if any(
                    entry[field] != node.get(source)
                    for field, source in (
                        ("label", "section_title"),
                        ("chapter_id", "chapter_id"),
                        ("volume_id", "volume_id"),
                    )
                ):
                    raise WordQuestionAttributeError("教材节必须与现有目录一致。")
    return row


class WordQuestionAttributeStore:
    """Atomic local batches with revisioned edits; no source or central writes."""

    def __init__(self, root):
        raw = Path(root)
        if raw.is_symlink():
            raise WordQuestionAttributeError("题目属性目录不能是链接。")
        self.root = raw.resolve()
        self.path = self.root / "word-question-attributes.sqlite3"
        self._lock = RLock()

    @contextmanager
    def _connect(self, *, readonly=False):
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise WordQuestionAttributeError("题目属性文件不能是链接。")
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=ro" if readonly else self.path,
            timeout=10,
            uri=readonly,
        )
        try:
            with connection:
                if readonly:
                    connection.execute("PRAGMA query_only=ON")
                else:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS attributes (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS history (key TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(key, version))"
                    )
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _existing(connection, key):
        row = connection.execute(
            "SELECT payload FROM attributes WHERE key=?", (key,)
        ).fetchone()
        return validate_attributes(json.loads(row[0])) if row else None

    @staticmethod
    def _write(connection, row):
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        connection.execute(
            "INSERT OR REPLACE INTO attributes(key,payload) VALUES (?,?)",
            (row["key"], payload),
        )
        connection.execute(
            "INSERT INTO history(key,version,payload) VALUES (?,?,?)",
            (row["key"], row["edit_version"], payload),
        )

    def get(self, key, *, source_sha256=None, question_revision=None):
        if not self.path.exists():
            return None
        with self._lock, self._connect(readonly=True) as connection:
            row = self._existing(connection, key)
        if row and (
            (source_sha256 is not None and source_sha256 != row["source_sha256"])
            or (
                question_revision is not None
                and question_revision != row["question_revision"]
            )
        ):
            return None
        return row

    def get_many(self, keys):
        """Read current rows through one read-only connection, without creating state.

        Missing keys are omitted. Callers compare source/question revisions with
        their current index. Every returned row is schema- and digest-checked.
        """
        requested = list(dict.fromkeys(keys))
        if not all(isinstance(key, str) and key for key in requested):
            raise WordQuestionAttributeError("题目属性标识格式不正确。")
        if not requested or not self.path.exists():
            return {}
        result = {}
        with self._lock, self._connect(readonly=True) as connection:
            # Stay below SQLite's conservative bind-parameter limit while
            # sharing one connection for the complete catalogue.
            for start in range(0, len(requested), 500):
                batch = requested[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                values = connection.execute(
                    f"SELECT key,payload FROM attributes WHERE key IN ({placeholders})",
                    batch,
                ).fetchall()
                for key, payload in values:
                    row = validate_attributes(json.loads(payload))
                    if row["key"] != key:
                        raise WordQuestionAttributeError("题目属性与保存标识不一致。")
                    result[key] = row
        return result

    def save_many(self, rows, *, expected_revisions=None):
        incoming = [validate_attributes(row) for row in rows]
        if len({row["key"] for row in incoming}) != len(incoming):
            raise WordQuestionAttributeError("同一批题目属性不能重复。")
        if expected_revisions is not None and (
            not isinstance(expected_revisions, dict)
            or set(expected_revisions) != {row["key"] for row in incoming}
        ):
            raise WordQuestionAttributeError("本批标签的旧版本绑定不完整。")
        result = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in incoming:
                existing = self._existing(connection, row["key"])
                if expected_revisions is not None and (
                    (existing["revision"] if existing else None) != expected_revisions[row["key"]]
                ):
                    raise WordQuestionAttributeError("标签已被修改，本批未保存；请刷新后重新核对。")
                if existing and existing["source_sha256"] != row["source_sha256"]:
                    raise WordQuestionAttributeError("题目属性的原文件绑定不能改变。")
                if existing and existing["annotation_source"] == "teacher_modified":
                    # Re-segmentation does not silently rebind teacher labels to
                    # different content. Callers surface the stale binding and
                    # request an explicit edit against a fresh baseline.
                    result.append(existing)
                    continue
                if (
                    existing
                    and existing["question_revision"] == row["question_revision"]
                ):
                    comparable = {**row, "edit_version": existing["edit_version"]}
                    if existing == _seal(comparable):
                        result.append(existing)
                        continue
                row["edit_version"] = existing["edit_version"] + 1 if existing else 1
                saved = _seal(row)
                self._write(connection, saved)
                result.append(saved)
        return result

    def save_teacher_edit(
        self,
        key,
        updates,
        *,
        expected_revision,
        curriculum_entries=None,
        initial_attributes=None,
        replacement_attributes=None,
    ):
        initial = (
            validate_attributes(initial_attributes)
            if initial_attributes is not None
            else None
        )
        if initial is not None and (
            initial["key"] != key or initial["revision"] != expected_revision
        ):
            raise WordQuestionAttributeError("初始题目属性与编辑版本不一致。")
        replacement = (
            validate_attributes(replacement_attributes)
            if replacement_attributes is not None
            else None
        )
        if initial is not None and replacement is not None:
            raise WordQuestionAttributeError("初始标注与重新分题复核不能同时执行。")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._existing(connection, key)
            if row is None and initial is not None:
                if initial["annotation_source"] != "auto_suggested":
                    raise WordQuestionAttributeError(
                        "初始题目属性必须保留自动建议来源。"
                    )
                row = _seal({**initial, "edit_version": 1})
                # Validate the entire proposal before either history row is written.
                edited = apply_teacher_edits(
                    row, updates, curriculum_entries=curriculum_entries
                )
                self._write(connection, row)
            elif row is None or row["revision"] != expected_revision:
                raise WordQuestionAttributeError("题目标签已有新版本，请刷新后修改。")
            elif replacement is not None:
                if (
                    replacement["key"] != row["key"]
                    or replacement["source_sha256"] != row["source_sha256"]
                    or replacement["question_revision"] == row["question_revision"]
                    or replacement["annotation_source"] != "auto_suggested"
                ):
                    raise WordQuestionAttributeError(
                        "重新分题复核必须绑定同一来源的新题目版本与自动建议。"
                    )
                baseline = _seal(
                    {**replacement, "edit_version": row["edit_version"] + 1}
                )
                edited = apply_teacher_edits(
                    baseline, updates, curriculum_entries=curriculum_entries
                )
                self._write(connection, baseline)
            else:
                edited = apply_teacher_edits(
                    row, updates, curriculum_entries=curriculum_entries
                )
            self._write(connection, edited)
        return edited

    def history(self, key):
        if not self.path.exists():
            return []
        with self._lock, self._connect(readonly=True) as connection:
            values = connection.execute(
                "SELECT payload FROM history WHERE key=? ORDER BY version", (key,)
            ).fetchall()
        return [validate_attributes(json.loads(row[0])) for row in values]
