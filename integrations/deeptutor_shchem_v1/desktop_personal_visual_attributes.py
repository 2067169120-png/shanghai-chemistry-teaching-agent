"""Local image-question teaching labels; never a Word or source-record rewrite.

The common field schemas and sealing helpers are shared with Word labels, but
the document schema and source binding are visual-specific. There is no text or
filename classifier here. Only explicit CAS fields seed the initial record.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from jsonschema import Draft202012Validator

from .desktop_word_question_attributes import (
    ATTRIBUTE_SCHEMA as WORD_ATTRIBUTE_SCHEMA,
)
from .desktop_word_question_attributes import (
    EXAM_TYPE_LABELS,
    GRADE_LABELS,
    _array,
    _digest,
    _evidence,
    _fact,
    _object,
    _seal,
    _text,
)

SCHEMA_VERSION = "shchem.personal-visual-question-attributes.v1"
RULE_REVISION = "visual-manual-attributes-20260913-v1"
UNKNOWN = "unknown"
TEACHING_USE_LABELS = {
    "new_lesson": "新课",
    "review": "复习",
    "practice": "练习",
    "homework": "作业",
    "assessment": "检测",
    "remediation": "错题巩固",
}
_EDIT_FIELDS = (
    "primary_knowledge",
    "supporting_knowledge",
    "applicable_grades",
    "curriculum_candidates",
    "curriculum_status",
    "teaching_use_tags",
    "teacher_note",
)
_ORIGINAL_FIELDS = ("grade", "exam_type", "year", "region", "school")
_ORIGIN_FIELDS = (
    *_EDIT_FIELDS,
    *("original_source." + field for field in _ORIGINAL_FIELDS),
)
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_BINDING = _object(
    {
        "batch_id": {"type": "string", "pattern": "^DESKTOPBATCH-[a-f0-9]{32}$"},
        "candidate_revision": _text(),
        "candidate_sha256": _HASH,
        "pages": _array(
            _object(
                {
                    "source_file_id": _text(),
                    "source_role": {"enum": ["question", "answer", "handout"]},
                    "source_sha256": _HASH,
                    "page_number": {"type": "integer", "minimum": 1},
                    "page_sha256": _HASH,
                }
            ),
            500,
        ),
    }
)

# Reuse only the pure common data schemas. No Word-schema record is constructed.
ATTRIBUTE_SCHEMA = deepcopy(WORD_ATTRIBUTE_SCHEMA)
_properties = ATTRIBUTE_SCHEMA["properties"]
_properties["schema_version"] = {"const": SCHEMA_VERSION}
for _name in ("index_revision", "extraction_revision"):
    del _properties[_name]
    ATTRIBUTE_SCHEMA["required"].remove(_name)
_properties["original_source"]["properties"]["school"] = deepcopy(
    _properties["original_source"]["properties"]["region"]
)
_properties["original_source"]["required"].append("school")
_properties["annotation_source"] = {
    "enum": ["source_observed", "teacher_modified", "ai_source_review"]
}
_properties.update(
    {
        "source_binding": _BINDING,
        "source_observed": {"type": "object"},
        "teaching_use_tags": _array({"enum": list(TEACHING_USE_LABELS)}, 6),
        "teacher_confirmed": {"type": "boolean"},
        "field_origins": _object(
            {
                field: {"enum": ["source_observed", "teacher", "ai_source_review"]}
                for field in _ORIGIN_FIELDS
            }
        ),
    }
)
ATTRIBUTE_SCHEMA["required"].extend(
    [
        "source_binding",
        "source_observed",
        "teaching_use_tags",
        "teacher_confirmed",
        "field_origins",
    ]
)


def _allow_proposed(schema):
    if isinstance(schema, dict):
        if "enum" in schema and "teacher_confirmed" in schema["enum"]:
            for value in ("teacher_proposed", "ai_source_review"):
                if value not in schema["enum"]:
                    schema["enum"].append(value)
        for value in schema.values():
            _allow_proposed(value)
    elif isinstance(schema, list):
        for value in schema:
            _allow_proposed(value)


_allow_proposed(ATTRIBUTE_SCHEMA)
_VALIDATOR = Draft202012Validator(ATTRIBUTE_SCHEMA)


class PersonalVisualAttributeError(ValueError):
    def __init__(self, message):
        self.code = "personal_visual_attributes_invalid"
        self.message_zh = message
        super().__init__(message)


def validate_attributes(value):
    if not isinstance(value, Mapping) or list(_VALIDATOR.iter_errors(value)):
        raise PersonalVisualAttributeError("图片题教学属性字段不完整或格式不正确。")
    row = deepcopy(dict(value))
    if row != _seal(row) or row["source_sha256"] != _digest(
        row["source_binding"]["pages"]
    ):
        raise PersonalVisualAttributeError("图片题属性内容与来源版本不一致。")
    if row["source_revision"] != row["source_binding"]["candidate_revision"]:
        raise PersonalVisualAttributeError("图片题属性的识别版本不一致。")
    for field in ("teaching_use_tags",):
        if len(row[field]) != len(set(row[field])):
            raise PersonalVisualAttributeError("图片题教学用途不能重复。")
    original = row["original_source"]
    if original["exam_type"]["value"] not in EXAM_TYPE_LABELS:
        raise PersonalVisualAttributeError("原考试类型须从候选目录选择。")
    if original["grade"]["value"] not in {UNKNOWN, *GRADE_LABELS}:
        raise PersonalVisualAttributeError("原题年级须从候选目录选择。")
    year = original["year"]["value"]
    if year != UNKNOWN and not re.fullmatch(r"[12][0-9]{3}", year):
        raise PersonalVisualAttributeError("原题年份须为四位年份，不明确时保留待确认。")
    for field in ("region", "school"):
        text = original[field]["value"]
        if not text.strip() or len(text) > 120:
            raise PersonalVisualAttributeError(
                "原题地区和学校最多120字，不明确时保留待确认。"
            )
    return row


def _original_label(original):
    parts = [
        original[name]["value"]
        for name in ("year", "region", "school")
        if original[name]["value"] != UNKNOWN
    ]
    exam = original["exam_type"]["value"]
    parts.append(EXAM_TYPE_LABELS[exam])
    return "图片收录题·" + "·".join(parts)


def attribute_catalog(catalog):
    result = deepcopy(catalog)
    result["teaching_use_tags"] = [
        {"id": key, "name": name} for key, name in TEACHING_USE_LABELS.items()
    ]
    return result


def initial_attributes(row, snapshot, pages, printed, catalog):
    """Explicit recognition fields only; combined region/school stays unsplit."""
    page_bindings = sorted(
        (
            {
                field: page[field]
                for field in (
                    "source_file_id",
                    "source_role",
                    "source_sha256",
                    "page_number",
                    "page_sha256",
                )
            }
            for page in pages.values()
        ),
        key=lambda page: (
            page["source_file_id"],
            page["page_number"],
            page["page_sha256"],
        ),
    )
    knowledge = {entry["id"]: entry["name"] for entry in catalog["knowledge_points"]}
    atomics = printed["atomic_parts"]
    primary_ids = {
        value
        for atomic in atomics
        for value in atomic["classification"]["primary_knowledge_K"]
        if value in knowledge and re.fullmatch(r"K(?:0[1-9]|1[0-9])", value)
    }
    primary = next(iter(primary_ids)) if len(primary_ids) == 1 else UNKNOWN
    support = sorted(
        {
            value
            for atomic in atomics
            for value in atomic["classification"]["supporting_knowledge_K"]
            if value in knowledge
            and value != primary
            and re.fullmatch(r"K(?:0[1-9]|1[0-9])", value)
        }
    )
    evidence = [
        _evidence(
            "model_image_observation", "本题CAS识别候选原有字段；仍需对照原图核对。"
        )
    ]

    def tag(identifier):
        return {
            "id": identifier,
            "label": knowledge.get(identifier, "知识主题待标记"),
            "status": "source_observed" if identifier != UNKNOWN else UNKNOWN,
            "evidence": deepcopy(evidence) if identifier != UNKNOWN else [],
        }

    sections = {entry["node_key"]: entry for entry in catalog["nodes"]}
    mappings = []
    for path in row["curriculum_paths"]:
        node = sections.get(path["section_key"])
        if node and all(
            node[field] == path[field] for field in ("volume_id", "chapter_id")
        ):
            mappings.append(
                {
                    **path,
                    "label": node.get("section_title", UNKNOWN),
                    "status": "source_observed",
                    "evidence": deepcopy(evidence),
                }
            )
    paper = snapshot["candidate"]["paper"]
    original = {
        name: _fact() for name in ("grade", "exam_type", "year", "region", "school")
    }
    year = str(paper.get("source_year", UNKNOWN))
    if re.fullmatch(r"[12][0-9]{3}", year):
        original["year"] = _fact(year, deepcopy(evidence), "source_observed")
    exam = paper.get("paper_type", UNKNOWN)
    if exam in EXAM_TYPE_LABELS and exam != UNKNOWN:
        original["exam_type"] = _fact(exam, deepcopy(evidence), "source_observed")
    original.update(citation_quotes=[], display_label=_original_label(original))
    source = {name: UNKNOWN for name in _properties["source"]["properties"]}
    source.update(
        source_name=row["source_name"],
        document_role="personal_visual_question",
        package_id=row["batch_id"],
    )
    images = row["images"]
    return validate_attributes(
        _seal(
            {
                "schema_version": SCHEMA_VERSION,
                "rule_revision": RULE_REVISION,
                "key": row["key"],
                "question_revision": row["revision"],
                "source_sha256": _digest(page_bindings),
                "source_revision": row["candidate_revision"],
                "source_binding": {
                    "batch_id": row["batch_id"],
                    "candidate_revision": row["candidate_revision"],
                    "candidate_sha256": row["candidate_sha256"],
                    "pages": page_bindings,
                },
                "source_observed": {
                    "paper": {
                        name: deepcopy(paper.get(name, UNKNOWN))
                        for name in (
                            "title",
                            "source_year",
                            "source_region_or_school",
                            "paper_type",
                            "evidence_refs",
                        )
                    },
                    "atomic_labels": [
                        {
                            "atomic_part_id": atomic["atomic_part_id"],
                            "classification": deepcopy(atomic["classification"]),
                            "curriculum": deepcopy(atomic["curriculum"]),
                        }
                        for atomic in atomics
                    ],
                },
                "source": source,
                "primary_knowledge": tag(primary),
                "supporting_knowledge": [tag(value) for value in support],
                "curriculum_candidates": mappings,
                "curriculum_status": "auto_suggested"
                if mappings
                else "pending_mapping",
                "applicable_grades": {
                    "values": [],
                    "basis": "适用年级待确认，不从文件名推断。",
                    "status": UNKNOWN,
                    "evidence": [],
                },
                "original_source": original,
                "response_forms": [],
                "answer_status": _fact(
                    "present_nonofficial_unverified"
                    if any(image["role"] == "answer" for image in images)
                    else "not_detected_pending_review",
                    deepcopy(evidence),
                    "source_observed",
                ),
                "material_status": {
                    "has_shared_context": bool(
                        row["shared_text"]
                        or any(image["role"] == "shared_material" for image in images)
                    ),
                    "context_block_indices": [],
                    "question_image_count": sum(
                        image["role"] != "answer" for image in images
                    ),
                    "answer_image_count": sum(
                        image["role"] == "answer" for image in images
                    ),
                    "missing_visual": not any(
                        image["role"] != "answer" for image in images
                    ),
                    "missing_context": False,
                    "warnings": deepcopy(row["warnings"]),
                },
                "annotation_source": "source_observed",
                "teacher_note": "",
                "edit_version": 0,
                "teaching_use_tags": [],
                "teacher_confirmed": False,
                "field_origins": {field: "source_observed" for field in _ORIGIN_FIELDS},
            }
        )
    )


def _validated_catalog(catalog):
    if not isinstance(catalog, Mapping):
        raise PersonalVisualAttributeError("图片题标签目录格式不正确。")
    knowledge = {entry["id"]: entry["name"] for entry in catalog["knowledge_points"]}
    sections = {entry["node_key"]: entry for entry in catalog["nodes"]}
    return knowledge, sections


def build_teacher_updates(attributes, selections, curriculum_entries):
    row = validate_attributes(attributes)
    expected = {
        "primary_knowledge_id",
        "supporting_knowledge_ids",
        "applicable_grades",
        "original_grade",
        "original_exam_type",
        "original_year",
        "original_region",
        "original_school",
        "curriculum_section_keys",
        "teaching_use_tags",
        "teacher_note",
    }
    if not isinstance(selections, Mapping) or set(selections) != expected:
        raise PersonalVisualAttributeError("图片题教学标签编辑字段不完整。")
    knowledge, sections = _validated_catalog(curriculum_entries)
    for name in (
        "supporting_knowledge_ids",
        "applicable_grades",
        "curriculum_section_keys",
        "teaching_use_tags",
    ):
        values = selections[name]
        if (
            not isinstance(values, list)
            or not all(isinstance(v, str) for v in values)
            or len(values) != len(set(values))
        ):
            raise PersonalVisualAttributeError("教学标签选项须为不重复的目录选项。")
    primary, support = (
        selections["primary_knowledge_id"],
        selections["supporting_knowledge_ids"],
    )
    if not isinstance(primary, str) or primary not in {UNKNOWN, *knowledge}:
        raise PersonalVisualAttributeError("主知识点须来自现有目录。")
    if any(value not in knowledge for value in support) or primary in support:
        raise PersonalVisualAttributeError("辅助知识点须来自目录且不能重复主知识点。")
    if any(value not in GRADE_LABELS for value in selections["applicable_grades"]):
        raise PersonalVisualAttributeError("适用年级选项不正确。")
    if any(value not in sections for value in selections["curriculum_section_keys"]):
        raise PersonalVisualAttributeError("教材节须来自现有目录。")
    if any(
        value not in TEACHING_USE_LABELS for value in selections["teaching_use_tags"]
    ):
        raise PersonalVisualAttributeError("教学用途须来自固定候选目录。")
    note = selections["teacher_note"]
    if not isinstance(note, str) or len(note) > 2000:
        raise PersonalVisualAttributeError("教师备注最多2000字。")
    evidence = [
        _evidence(
            "teacher_note", note.strip() or "本机个人教学标签修订，非原档案改写。"
        )
    ]

    def tag(identifier):
        return {
            "id": identifier,
            "label": knowledge.get(identifier, "知识主题待标记"),
            "status": "teacher_proposed" if identifier != UNKNOWN else UNKNOWN,
            "evidence": deepcopy(evidence),
        }

    proposed = {
        "primary_knowledge": tag(primary),
        "supporting_knowledge": [tag(value) for value in support],
        "applicable_grades": {
            "values": selections["applicable_grades"],
            "basis": "个人教学用途；不等于原题年级。",
            "status": "teacher_proposed"
            if selections["applicable_grades"]
            else UNKNOWN,
            "evidence": deepcopy(evidence),
        },
        "curriculum_candidates": [
            {
                "section_key": key,
                "chapter_id": sections[key]["chapter_id"],
                "volume_id": sections[key]["volume_id"],
                "label": sections[key]["section_title"],
                "status": "teacher_proposed",
                "evidence": deepcopy(evidence),
            }
            for key in selections["curriculum_section_keys"]
        ],
        "curriculum_status": "teacher_proposed"
        if selections["curriculum_section_keys"]
        else "pending_mapping",
        "teaching_use_tags": selections["teaching_use_tags"],
        "teacher_note": note.strip(),
    }
    original = deepcopy(row["original_source"])
    for field in ("grade", "exam_type", "year", "region", "school"):
        value = selections["original_" + field]
        if not isinstance(value, str):
            raise PersonalVisualAttributeError(
                "原题出处修订须为文字，不明确时保留待确认。"
            )
        value = value.strip() or UNKNOWN
        if value != original[field]["value"]:
            original[field] = _fact(
                value,
                deepcopy(evidence),
                "teacher_proposed" if value != UNKNOWN else UNKNOWN,
            )
    original["display_label"] = (
        _original_label(original)
        if original["exam_type"]["value"] in EXAM_TYPE_LABELS
        else ""
    )
    proposed["original_source"] = original
    unchanged = {
        "primary_knowledge": primary == row["primary_knowledge"]["id"],
        "supporting_knowledge": set(support)
        == {entry["id"] for entry in row["supporting_knowledge"]},
        "applicable_grades": set(selections["applicable_grades"])
        == set(row["applicable_grades"]["values"]),
        "curriculum_candidates": set(selections["curriculum_section_keys"])
        == {entry["section_key"] for entry in row["curriculum_candidates"]},
    }
    unchanged["curriculum_status"] = unchanged["curriculum_candidates"]
    updates = {
        field: value
        for field, value in proposed.items()
        if not unchanged.get(field, value == row[field])
    }
    if updates:
        apply_teacher_edits(row, updates, curriculum_entries=curriculum_entries)
    return updates


def apply_teacher_edits(
    attributes,
    updates,
    *,
    curriculum_entries=None,
    teacher_confirmed=False,
    edit_origin="teacher",
):
    row = validate_attributes(attributes)
    allowed = {
        "primary_knowledge",
        "supporting_knowledge",
        "applicable_grades",
        "original_source",
        "curriculum_candidates",
        "curriculum_status",
        "teaching_use_tags",
        "teacher_note",
    }
    if (
        type(teacher_confirmed) is not bool
        or not isinstance(updates, Mapping)
        or set(updates) - allowed
        or not isinstance(edit_origin, str)
        or edit_origin not in {"teacher", "ai_source_review"}
        or edit_origin == "ai_source_review"
        and teacher_confirmed
    ):
        raise PersonalVisualAttributeError(
            "只能修改个人教学标签，不能改写来源、题面或答案。"
        )
    if list(_VALIDATOR.iter_errors({**row, **deepcopy(dict(updates))})):
        raise PersonalVisualAttributeError("图片题教学属性修改字段不完整或格式不正确。")
    if "original_source" in updates and (
        not isinstance(updates["original_source"], Mapping)
        or updates["original_source"].get("citation_quotes")
        != row["original_source"]["citation_quotes"]
    ):
        raise PersonalVisualAttributeError("原题引文必须保留；教学修订依据请填写备注。")
    if not updates and row["teacher_confirmed"] == teacher_confirmed:
        return row
    changed = {
        field
        for field in _EDIT_FIELDS
        if field in updates and updates[field] != row[field]
    }
    if "original_source" in updates:
        changed.update(
            "original_source." + field
            for field in _ORIGINAL_FIELDS
            if updates["original_source"].get(field) != row["original_source"][field]
        )
    if edit_origin == "ai_source_review" and any(
        row["field_origins"][field] == "teacher" for field in changed
    ):
        raise PersonalVisualAttributeError(
            "已有教师修订的字段不能被AI整理覆盖；请保留教师标签。"
        )

    def labelled(value, old=None):
        if value == old:
            return deepcopy(value)
        if isinstance(value, dict):
            result = {
                key: labelled(item, old.get(key) if isinstance(old, dict) else None)
                for key, item in value.items()
            }
            if "status" in result:
                unknown = (
                    result.get("value") == UNKNOWN
                    or result.get("id") == UNKNOWN
                    or ("values" in result and not result["values"])
                )
                result["status"] = (
                    UNKNOWN
                    if unknown
                    else (
                        "ai_source_review"
                        if edit_origin == "ai_source_review"
                        else "teacher_confirmed"
                        if teacher_confirmed
                        else "teacher_proposed"
                    )
                )
            return result
        if isinstance(value, list):
            return [
                labelled(
                    item,
                    next((previous for previous in old if previous == item), None)
                    if isinstance(old, list)
                    else None,
                )
                for item in value
            ]
        return deepcopy(value)

    for name, value in updates.items():
        row[name] = labelled(value, row.get(name))
    for field in changed:
        row["field_origins"][field] = edit_origin
    if "curriculum_candidates" in updates or "curriculum_status" in updates:
        row["curriculum_status"] = (
            (
                "ai_source_review"
                if edit_origin == "ai_source_review"
                else "teacher_confirmed"
                if teacher_confirmed
                else "teacher_proposed"
            )
            if row["curriculum_candidates"]
            else "pending_mapping"
        )
    original = row["original_source"]
    if original["exam_type"]["value"] in EXAM_TYPE_LABELS:
        original["display_label"] = _original_label(original)
    row.update(
        annotation_source="ai_source_review"
        if edit_origin == "ai_source_review"
        else "teacher_modified",
        teacher_confirmed=teacher_confirmed,
        edit_version=row["edit_version"] + 1,
    )
    row = validate_attributes(_seal(row))
    primary = row["primary_knowledge"]["id"]
    supporting = [item["id"] for item in row["supporting_knowledge"]]
    if (
        primary in supporting
        or len(supporting) != len(set(supporting))
        or UNKNOWN in supporting
    ):
        raise PersonalVisualAttributeError("辅助知识点不能重复主知识点或填写未知项。")
    for values in (
        row["applicable_grades"]["values"],
        [entry["section_key"] for entry in row["curriculum_candidates"]],
    ):
        if len(values) != len(set(values)):
            raise PersonalVisualAttributeError("年级或教材节不能重复。")
    if curriculum_entries is not None:
        knowledge, sections = _validated_catalog(curriculum_entries)
        for entry in [row["primary_knowledge"], *row["supporting_knowledge"]]:
            if entry["id"] != UNKNOWN and knowledge.get(entry["id"]) != entry["label"]:
                raise PersonalVisualAttributeError(
                    "知识标签编号和名称须与现有目录一致。"
                )
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
                raise PersonalVisualAttributeError(
                    "教材册、章、节映射须与现有目录一致。"
                )
    return row


class PersonalVisualAttributeStore:
    """Independent local history with SQLite CAS; reads create no database."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "personal-visual-question-attributes.sqlite3"
        self._lock = RLock()

    def _check_path(self):
        for path in (self.path, self.root, *self.root.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise PersonalVisualAttributeError("图片题属性目录不能使用链接。")

    @contextmanager
    def _connect(self, *, readonly=False):
        self._check_path()
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path,
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
        result = connection.execute(
            "SELECT payload FROM attributes WHERE key=?", (key,)
        ).fetchone()
        row = validate_attributes(json.loads(result[0])) if result else None
        if row and row["key"] != key:
            raise PersonalVisualAttributeError("图片题属性记录与题目定位不一致。")
        return row

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

    def get_many(self, keys):
        self._check_path()
        if not self.path.is_file():
            return {}
        with self._lock, self._connect(readonly=True) as connection:
            return {
                key: row
                for key in dict.fromkeys(keys)
                if (row := self._existing(connection, key))
            }

    def history(self, key):
        self._check_path()
        if not self.path.is_file():
            return []
        with self._lock, self._connect(readonly=True) as connection:
            result = [
                validate_attributes(json.loads(row[0]))
                for row in connection.execute(
                    "SELECT payload FROM history WHERE key=? ORDER BY version", (key,)
                )
            ]
            if any(row["key"] != key for row in result):
                raise PersonalVisualAttributeError("图片题属性历史与题目定位不一致。")
            return result

    def save_teacher_edit(
        self,
        attributes,
        updates,
        *,
        expected_stored_revision,
        curriculum_entries,
        teacher_confirmed=False,
        edit_origin="teacher",
    ):
        initial = validate_attributes(attributes)
        edited = apply_teacher_edits(
            initial,
            updates,
            curriculum_entries=curriculum_entries,
            teacher_confirmed=teacher_confirmed,
            edit_origin=edit_origin,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._existing(connection, initial["key"])
            if (current["revision"] if current else None) != expected_stored_revision:
                raise PersonalVisualAttributeError(
                    "图片题标签已有新版本，请刷新后修改。"
                )
            if current and (
                current["source_sha256"] != initial["source_sha256"]
                or current["source_binding"]["batch_id"]
                != initial["source_binding"]["batch_id"]
            ):
                raise PersonalVisualAttributeError("图片题标签与原始图片来源不一致。")
            if edited == initial:
                return initial
            if (
                current is None
                or current["question_revision"] != initial["question_revision"]
            ):
                initial = _seal(
                    {
                        **initial,
                        "edit_version": current["edit_version"] + 1 if current else 0,
                    }
                )
                self._write(connection, initial)
                edited = _seal({**edited, "edit_version": initial["edit_version"] + 1})
            self._write(connection, edited)
            return edited
