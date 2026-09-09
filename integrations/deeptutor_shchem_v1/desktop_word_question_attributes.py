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
RULE_REVISION = "word-attributes-20260910-v1"
UNKNOWN = "unknown"
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
        match = re.search(re.escape(term), text, re.IGNORECASE)
        if match:
            found.append(
                _evidence(
                    kind, text[max(0, match.start() - 25) : match.end() + 35], index
                )
            )
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
    by_id = {row.get("id"): row for row in taxonomy if isinstance(row, Mapping)}
    for identifier, terms in _TERMS.items():
        node = by_id.get(identifier, {})
        phrases = set(terms) | {
            p for p in node.get("subtopics", []) if isinstance(p, str) and len(p) >= 3
        }
        matches = [(term, _find(term, segments)) for term in sorted(phrases)]
        matches = [(term, evidence) for term, evidence in matches if evidence]
        if matches:
            evidence = list(
                {_digest(e): e for _, found in matches for e in found}.values()
            )[:20]
            score = sum(len(term) for term, _ in matches) + 100 * any(
                e["kind"] == "source_chapter" for e in evidence
            )
            candidates.append(
                (
                    score,
                    {
                        "id": identifier,
                        "label": node.get("name") or VALUE_LABELS_ZH[identifier],
                        "status": "auto_suggested",
                        "evidence": evidence,
                    },
                )
            )
    candidates.sort(key=lambda item: (-item[0], item[1]["id"]))
    values = [item[1] for item in candidates]
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

    def save_many(self, rows):
        incoming = [validate_attributes(row) for row in rows]
        if len({row["key"] for row in incoming}) != len(incoming):
            raise WordQuestionAttributeError("同一批题目属性不能重复。")
        result = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for row in incoming:
                existing = self._existing(connection, row["key"])
                if existing and existing["source_sha256"] != row["source_sha256"]:
                    raise WordQuestionAttributeError("题目属性的原文件绑定不能改变。")
                if (
                    existing
                    and existing["question_revision"] == row["question_revision"]
                ):
                    comparable = {**row, "edit_version": existing["edit_version"]}
                    if existing[
                        "annotation_source"
                    ] == "teacher_modified" or existing == _seal(comparable):
                        result.append(existing)
                        continue
                row["edit_version"] = existing["edit_version"] + 1 if existing else 1
                saved = _seal(row)
                self._write(connection, saved)
                result.append(saved)
        return result

    def save_teacher_edit(self, key, updates, *, expected_revision):
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

        def confirmed(value):
            if isinstance(value, dict):
                return {
                    name: "teacher_confirmed" if name == "status" else confirmed(item)
                    for name, item in value.items()
                }
            if isinstance(value, list):
                return [confirmed(item) for item in value]
            return value

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._existing(connection, key)
            if row is None or row["revision"] != expected_revision:
                raise WordQuestionAttributeError("题目标签已有新版本，请刷新后修改。")
            row.update(confirmed(deepcopy(dict(updates))))
            if "original_source" in updates:
                row["original_source"]["display_label"] = _original_label(
                    row["original_source"]
                )
            row.update(
                annotation_source="teacher_modified",
                edit_version=row["edit_version"] + 1,
            )
            row = validate_attributes(_seal(row))
            self._write(connection, row)
        return row

    def history(self, key):
        if not self.path.exists():
            return []
        with self._lock, self._connect(readonly=True) as connection:
            values = connection.execute(
                "SELECT payload FROM history WHERE key=? ORDER BY version", (key,)
            ).fetchall()
        return [validate_attributes(json.loads(row[0])) for row in values]
