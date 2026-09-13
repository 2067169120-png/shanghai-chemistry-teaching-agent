from __future__ import annotations

"""Native OOXML intake for the local one-round-review handout corpus.

The importer deliberately reads only editable OOXML structures from ``.docx``
containers.  It never invokes OCR, never reads a PDF text layer and never
pretends that an image, OLE object, equation or layout dependency was recovered
as native text.  Results are candidate records for the personal desktop
workbench; they do not modify the central chemistry catalogue.
"""

import hashlib
import json
import os
import posixpath
import re
import tempfile
import threading
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from .word_native_text import WordNativeTextReader

WORD_HANDOUT_IMPORT_SCHEMA = "shchem.word-handout-native-import.v1"
WORD_HANDOUT_PARSER_VERSION = "1.2.1"
PERSONAL_HANDOUT_INVENTORY_SCHEMA = "shchem.personal-handout-inventory.v1"
IMPORT_STATES = (
    "native_text_complete",
    "hybrid_visual_required",
    "visual_only_required",
)

_MAX_ENTRIES = 12_000
_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_XML_BYTES = 32 * 1024 * 1024
_MAX_ASSET_BYTES = 64 * 1024 * 1024
_PACKAGE_RE = re.compile(r"^PKG-\d{3}$", re.IGNORECASE)
_ROLE_MARK_RE = re.compile(r"[（(](?:原卷版|解析版)[）)]")
_SPACE_RE = re.compile(r"\s+")
_STANDARD_QUESTION_RE = re.compile(
    r"^\s*(?:(?:第\s*)?(?P<number>\d{1,3})\s*[．。.、]|"
    r"(?P<prefix>例题|典例|题)\s*(?P<prefixed>[一二三四五六七八九十百\d]{1,4})\s*[．。.、:：]?)"
    r"\s*(?P<body>.*)$"
)
_PAREN_QUESTION_RE = re.compile(
    r"^\s*[（(]\s*(?P<paren>\d{1,2})\s*[）)]\s*(?P<body>.*)$"
)
_OPTION_RE = re.compile(r"^\s*[A-HＡ-Ｈ]\s*[．。.、]", re.IGNORECASE)
_ANSWER_RE = re.compile(r"(?:【\s*答案\s*】|^\s*答案\s*[:：])", re.MULTILINE)
_ANALYSIS_RE = re.compile(
    r"(?:【\s*(?:解析|解答|详解|点拨|点评)\s*】|"
    r"^\s*(?:解析|解答|详解)\s*[:：])",
    re.MULTILINE,
)
_QUESTION_CUES = re.compile(
    r"下列|正确|错误|不正确|不合理|能达到|不能|可以|填|写出|计算|求|回答|"
    r"解释|原因|选择|判断|鉴别|检验|除去|制备|实验|根据|已知|若|向|关于|"
    r"某|哪|何|如何|为什么|________|____|\?|？"
)
_STRONG_QUESTION_CUES = re.compile(
    r"下列|正确|错误|不正确|不合理|能达到|不能|填|写出|计算|求|回答|解释|"
    r"原因|选择|判断|是否|怎样|如何|为什么|哪|何|________|____|\?|？"
)
_VISUAL_CUES = re.compile(r"如图|下图|图中|装置|曲线|流程图|示意图|结构式|晶胞|谱图")
_EXERCISE_CUES = re.compile(
    r"训练|练习|真题|例题|典例|考向|题型|检测|达标|溯源|突破|作业|测试"
)
_HEADING_CUES = re.compile(
    r"^(?:\d{2}\s*)?(?:考点|知识点|题型|考向|专题|课标达标练|核心突破练|"
    r"真题溯源练|真题溯源|体系构建|考情解码|方法|思维建模)"
)
_THEME_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+\s*[、.．]\s*\S")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = "{" + _W_NS + "}"
_R = "{" + _R_NS + "}"
_REL = "{" + _PKG_REL_NS + "}"
_NS = {"w": _W_NS, "r": _R_NS}

_VISUAL_OR_UNSUPPORTED_FEATURES = {
    "embedded_image_reference",
    "word_drawing_or_shape",
    "vml_drawing_or_shape",
    "ole_object_reference",
    "omml_equation",
    "chart_or_diagram_reference",
    "text_box_layout_dependency",
    "run_vertical_alignment",
    "unsupported_symbol",
    "field_code_dependency",
    "note_or_comment_reference",
    "external_relationship_reference",
    "layout_break_dependency",
    "unsupported_embedded_part",
    "preceding_visual_context_possible",
    "tracked_changes",
    "hidden_text_omitted",
    "automatic_numbering",
}

_FEATURE_BLOCKERS = {
    "embedded_image_reference": "embedded_image_reference",
    "word_drawing_or_shape": "word_drawing_or_shape",
    "vml_drawing_or_shape": "vml_drawing_or_shape",
    "ole_object_reference": "ole_object_reference",
    "omml_equation": "omml_equation_reference",
    "chart_or_diagram_reference": "chart_or_diagram_reference",
    "text_box_layout_dependency": "text_box_layout_dependency",
    "run_vertical_alignment": "run_vertical_alignment",
    "unsupported_symbol": "unsupported_symbol",
    "field_code_dependency": "field_code_dependency",
    "note_or_comment_reference": "note_or_comment_reference",
    "external_relationship_reference": "external_relationship_reference",
    "layout_break_dependency": "layout_break_dependency",
    "unsupported_embedded_part": "unsupported_embedded_part",
    "preceding_visual_context_possible": "preceding_visual_context_possible",
}

_BOUNDARY_BLOCKERS = {
    "answer_analysis_order_ambiguous",
    "answer_boundary_missing_in_solution_document",
    "multiple_question_markers_in_table",
    "native_question_text_missing",
    "question_boundary_uncertain",
}


class WordHandoutImportError(RuntimeError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


@dataclass(frozen=True)
class NativeBlock:
    index: int
    block_kind: str
    text: str
    style_id: str | None
    style_name: str | None
    numbering: Mapping[str, Any] | None
    table: Mapping[str, Any] | None
    relationship_ids: tuple[str, ...]
    features: tuple[str, ...]
    rich_runs: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuestionCandidate:
    candidate_id: str
    package_id: str
    source_document_name: str
    source_document_sha256: str
    document_role: str
    pair_key: str
    ordinal: int
    source_question_label: str
    section_title: str | None
    block_start: int
    block_end: int
    context_block_indexes: tuple[int, ...]
    native_text: str
    question_text: str
    answer_text: str | None
    analysis_text: str | None
    native_blocks: tuple[Mapping[str, Any], ...]
    media_references: tuple[Mapping[str, Any], ...]
    ole_references: tuple[Mapping[str, Any], ...]
    features: tuple[str, ...]
    blockers: tuple[str, ...]
    boundary_status: str
    import_state: str
    question_import_state: str
    answer_import_state: str | None = None
    pairing_status: str = "not_evaluated"
    paired_candidate_id: str | None = None
    paired_source_document_sha256: str | None = None
    reference_answer_text: str | None = None
    reference_analysis_text: str | None = None
    reference_answer_candidate_id: str | None = None
    answer_authority: str | None = None
    answer_verified: bool = False
    quick_import_eligible: bool = False
    record_role: str = "unassigned"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentResult:
    document_key: str
    package_id: str
    source_path: str
    source_name: str
    source_sha256: str
    source_bytes: int
    document_role: str
    pair_key: str
    native_text_characters: int
    native_paragraphs: int
    native_table_cells: int
    referenced_media_count: int
    referenced_ole_count: int
    packaged_media_count: int
    packaged_ole_count: int
    import_state: str
    candidates: tuple[QuestionCandidate, ...]
    warnings: tuple[str, ...] = ()
    cache_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportProgress:
    batch_id: str
    processed_documents: int
    total_documents: int
    cached_documents: int
    failed_documents: int
    current_package_id: str | None
    current_document_name: str | None
    state_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    schema_version: str
    parser_version: str
    documents_total: int
    documents_completed: int
    documents_cached: int
    documents_failed: int
    packages_total: int
    inventory_complete_98x196: bool
    candidates_total: int
    primary_question_candidates: int
    solution_reference_candidates: int
    paired_question_candidates: int
    quick_import_candidates: int
    visual_completion_candidates: int
    boundary_blocked_candidates: int
    state_counts: Mapping[str, int]
    failures: tuple[Mapping[str, Any], ...]
    documents: tuple[DocumentResult, ...]
    candidate_index: tuple[QuestionCandidate, ...]
    manifest_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Relationship:
    relationship_id: str
    kind: str
    target: str
    target_mode: str
    part_name: str | None
    bytes: int | None
    sha256: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WordHandoutImportError("docx_unreadable", "DOCX 文件无法读取。") from exc
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_text(value: str) -> str:
    return _SPACE_RE.sub("", value).casefold()


def _infer_package_id(path: Path) -> str:
    for part in reversed(path.parts):
        if _PACKAGE_RE.fullmatch(part):
            return part.upper()
    return "PKG-UNKNOWN"


def _document_role(name: str) -> str:
    if "原卷版" in name:
        return "question_source"
    if "解析版" in name:
        return "solution_reference"
    return "unknown"


def _pair_key(name: str) -> str:
    stem = Path(name).stem
    stem = _ROLE_MARK_RE.sub("", stem)
    return _SPACE_RE.sub("", stem).casefold()


def _xml_root(package: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    try:
        info = package.getinfo(name)
    except KeyError:
        return None
    if not 0 < info.file_size <= _MAX_XML_BYTES:
        raise WordHandoutImportError("docx_xml_size_invalid", "DOCX 内部 XML 大小异常。")
    try:
        return ElementTree.fromstring(package.read(name))
    except (ElementTree.ParseError, OSError) as exc:
        raise WordHandoutImportError("docx_xml_invalid", "DOCX 内部 XML 无法解析。") from exc


def _validate_container(package: zipfile.ZipFile) -> tuple[set[str], int, int]:
    infos = package.infolist()
    if not infos or len(infos) > _MAX_ENTRIES:
        raise WordHandoutImportError("docx_container_invalid", "DOCX 容器结构异常。")
    total = 0
    media = 0
    ole = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if (
            name.startswith("/")
            or ".." in pure.parts
            or (pure.parts and ":" in pure.parts[0])
        ):
            raise WordHandoutImportError("docx_entry_path_invalid", "DOCX 内部路径不安全。")
        total += max(info.file_size, 0)
        if total > _MAX_UNCOMPRESSED_BYTES:
            raise WordHandoutImportError("docx_too_large", "DOCX 解包后的体积超过限制。")
        names.add(name)
        if name.startswith("word/media/") and not name.endswith("/"):
            media += 1
        if name.startswith("word/embeddings/") and not name.endswith("/"):
            ole += 1
    if "word/document.xml" not in names:
        raise WordHandoutImportError("docx_document_missing", "DOCX 缺少正文 XML。")
    return names, media, ole


def _relationship_kind(type_uri: str) -> str:
    tail = type_uri.rstrip("/").rsplit("/", 1)[-1].casefold()
    if tail == "image":
        return "image"
    if tail in {"oleobject", "package"}:
        return "ole"
    if tail in {"chart", "diagramdata", "diagramlayout", "diagramcolors", "diagramquickstyle"}:
        return "chart_or_diagram"
    if tail == "hyperlink":
        return "hyperlink"
    return tail or "unknown"


def _resolve_internal_part(base_part: str, target: str) -> str | None:
    if not target or "\\" in target:
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(
            posixpath.join(posixpath.dirname(base_part), target)
        )
    if normalized == ".." or normalized.startswith(("../", "/")):
        return None
    return normalized


def _relationships(
    package: zipfile.ZipFile, names: set[str]
) -> dict[str, _Relationship]:
    root = _xml_root(package, "word/_rels/document.xml.rels")
    if root is None:
        return {}
    result: dict[str, _Relationship] = {}
    for node in root:
        if _local_name(node.tag) != "Relationship":
            continue
        relationship_id = str(node.get("Id") or "")
        target = str(node.get("Target") or "")
        target_mode = str(node.get("TargetMode") or "Internal")
        kind = _relationship_kind(str(node.get("Type") or ""))
        part_name: str | None = None
        size: int | None = None
        digest: str | None = None
        if target_mode.casefold() != "external":
            part_name = _resolve_internal_part("word/document.xml", target)
            if part_name is not None and part_name in names:
                info = package.getinfo(part_name)
                size = info.file_size
                if 0 <= size <= _MAX_ASSET_BYTES:
                    digest = hashlib.sha256(package.read(part_name)).hexdigest()
        if relationship_id:
            result[relationship_id] = _Relationship(
                relationship_id=relationship_id,
                kind=kind,
                target=target,
                target_mode=target_mode,
                part_name=part_name,
                bytes=size,
                sha256=digest,
            )
    return result


def _style_catalog(package: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    root = _xml_root(package, "word/styles.xml")
    if root is None:
        return {}
    raw: dict[str, dict[str, Any]] = {}
    for style in root.findall("w:style", _NS):
        style_id = style.get(_W + "styleId")
        if not style_id:
            continue
        name_node = style.find("w:name", _NS)
        based_node = style.find("w:basedOn", _NS)
        num_id_node = style.find("w:pPr/w:numPr/w:numId", _NS)
        level_node = style.find("w:pPr/w:numPr/w:ilvl", _NS)
        raw[style_id] = {
            "style_id": style_id,
            "style_name": name_node.get(_W + "val") if name_node is not None else style_id,
            "based_on": based_node.get(_W + "val") if based_node is not None else None,
            "num_id": num_id_node.get(_W + "val") if num_id_node is not None else None,
            "level": level_node.get(_W + "val") if level_node is not None else None,
        }

    def resolved(style_id: str, trail: set[str] | None = None) -> dict[str, Any]:
        value = dict(raw.get(style_id) or {})
        parent_id = value.get("based_on")
        trail = set(trail or ())
        if parent_id and parent_id not in trail and parent_id in raw:
            trail.add(style_id)
            parent = resolved(parent_id, trail)
            for key in ("num_id", "level"):
                if value.get(key) is None:
                    value[key] = parent.get(key)
        return value

    return {style_id: resolved(style_id) for style_id in raw}


def _numbering_catalog(package: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    root = _xml_root(package, "word/numbering.xml")
    if root is None:
        return {}
    abstracts: dict[str, dict[str, Any]] = {}
    for abstract in root.findall("w:abstractNum", _NS):
        abstract_id = abstract.get(_W + "abstractNumId")
        if abstract_id is None:
            continue
        levels: dict[str, Any] = {}
        for level in abstract.findall("w:lvl", _NS):
            index = level.get(_W + "ilvl") or "0"
            start = level.find("w:start", _NS)
            num_fmt = level.find("w:numFmt", _NS)
            level_text = level.find("w:lvlText", _NS)
            levels[index] = {
                "start": int(start.get(_W + "val") or 1) if start is not None else 1,
                "format": num_fmt.get(_W + "val") if num_fmt is not None else "decimal",
                "level_text": level_text.get(_W + "val") if level_text is not None else "%1.",
            }
        abstracts[abstract_id] = levels
    result: dict[str, dict[str, Any]] = {}
    for num in root.findall("w:num", _NS):
        num_id = num.get(_W + "numId")
        abstract_node = num.find("w:abstractNumId", _NS)
        abstract_id = abstract_node.get(_W + "val") if abstract_node is not None else None
        if num_id is not None:
            result[num_id] = {
                "abstract_id": abstract_id,
                "levels": deepcopy(abstracts.get(str(abstract_id)) or {}),
            }
    return result


def _integer_label(value: int, fmt: str) -> str:
    if fmt in {"lowerLetter", "upperLetter"}:
        result = ""
        current = max(value, 1)
        while current:
            current, remainder = divmod(current - 1, 26)
            result = chr(ord("a") + remainder) + result
        return result.upper() if fmt == "upperLetter" else result
    if fmt in {"lowerRoman", "upperRoman"}:
        numbers = (
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        )
        current = max(value, 1)
        result = ""
        for number, token in numbers:
            while current >= number:
                result += token
                current -= number
        return result if fmt == "upperRoman" else result.lower()
    return str(value)


class _NumberingState:
    def __init__(self, catalog: Mapping[str, Mapping[str, Any]]) -> None:
        self.catalog = catalog
        self.counters: dict[str, dict[int, int]] = defaultdict(dict)

    def next(self, num_id: str, level: int) -> dict[str, Any]:
        definition = dict(self.catalog.get(num_id) or {})
        levels = dict(definition.get("levels") or {})
        current_level = dict(levels.get(str(level)) or {})
        start = int(current_level.get("start") or 1)
        counters = self.counters[num_id]
        counters[level] = counters.get(level, start - 1) + 1
        for deeper in [item for item in counters if item > level]:
            del counters[deeper]
        text = str(current_level.get("level_text") or f"%{level + 1}.")
        for item_level in range(level + 1):
            value = counters.get(item_level)
            item_definition = dict(levels.get(str(item_level)) or {})
            if value is None:
                value = int(item_definition.get("start") or 1)
            text = text.replace(
                f"%{item_level + 1}",
                _integer_label(value, str(item_definition.get("format") or "decimal")),
            )
        return {
            "num_id": num_id,
            "level": level,
            "label": text,
            "format": str(current_level.get("format") or "decimal"),
            "level_text": str(current_level.get("level_text") or ""),
            "start": start,
        }


def _relationship_ids(element: ElementTree.Element) -> tuple[str, ...]:
    result: set[str] = set()
    for node in element.iter():
        for key, value in node.attrib.items():
            if key.startswith(_R) and _local_name(key) in {"id", "embed", "link"} and value:
                result.add(value)
    return tuple(sorted(result))


def _features(
    element: ElementTree.Element,
    relationship_map: Mapping[str, _Relationship],
) -> tuple[str, ...]:
    tags = {_local_name(node.tag) for node in element.iter()}
    result: set[str] = set()
    if "drawing" in tags:
        result.add("word_drawing_or_shape")
    if {"pict", "imagedata"} & tags:
        result.add("vml_drawing_or_shape")
    if {"object", "OLEObject"} & tags:
        result.add("ole_object_reference")
    if {"oMath", "oMathPara"} & tags:
        result.add("omml_equation")
    if "txbxContent" in tags:
        result.add("text_box_layout_dependency")
    if {"fldChar", "instrText", "fldSimple"} & tags:
        result.add("field_code_dependency")
    if "sym" in tags:
        result.add("unsupported_symbol")
    if {"footnoteReference", "endnoteReference", "commentReference"} & tags:
        result.add("note_or_comment_reference")
    for node in element.iter():
        local = _local_name(node.tag)
        if local == "vertAlign" and (node.get(_W + "val") or "") in {
            "subscript",
            "superscript",
        }:
            result.add("run_vertical_alignment")
        if local == "br" and (node.get(_W + "type") or "") in {"page", "column"}:
            result.add("layout_break_dependency")
    for relationship_id in _relationship_ids(element):
        relationship = relationship_map.get(relationship_id)
        if relationship is None:
            result.add("unsupported_embedded_part")
            continue
        if relationship.target_mode.casefold() == "external":
            result.add("external_relationship_reference")
        if relationship.kind == "image":
            result.add("embedded_image_reference")
        elif relationship.kind == "ole":
            result.add("ole_object_reference")
        elif relationship.kind == "chart_or_diagram":
            result.add("chart_or_diagram_reference")
    return tuple(sorted(result))


def _paragraph_numbering(
    paragraph: ElementTree.Element,
    style_id: str | None,
    styles: Mapping[str, Mapping[str, Any]],
    numbering_state: _NumberingState,
) -> Mapping[str, Any] | None:
    num_node = paragraph.find("w:pPr/w:numPr/w:numId", _NS)
    level_node = paragraph.find("w:pPr/w:numPr/w:ilvl", _NS)
    num_id = num_node.get(_W + "val") if num_node is not None else None
    level_value = level_node.get(_W + "val") if level_node is not None else None
    if num_id is None and style_id:
        style = styles.get(style_id) or {}
        num_id = style.get("num_id")
        level_value = style.get("level") if level_value is None else level_value
    if num_id is None:
        return None
    try:
        level = int(level_value or 0)
    except (TypeError, ValueError):
        level = 0
    return numbering_state.next(str(num_id), max(level, 0))


def _table_projection(table: ElementTree.Element, text_reader=None) -> Mapping[str, Any]:
    text_reader = text_reader or WordNativeTextReader()
    rows: list[Mapping[str, Any]] = []
    for row_index, row in enumerate(table.findall("w:tr", _NS)):
        cells: list[Mapping[str, Any]] = []
        for cell_index, cell in enumerate(row.findall("w:tc", _NS)):
            properties = cell.find("w:tcPr", _NS)
            grid_span_node = properties.find("w:gridSpan", _NS) if properties is not None else None
            merge_node = properties.find("w:vMerge", _NS) if properties is not None else None
            cells.append(
                {
                    "row": row_index,
                    "column": cell_index,
                    "text": text_reader.read(cell).text,
                    "grid_span": int(grid_span_node.get(_W + "val") or 1) if grid_span_node is not None else 1,
                    "vertical_merge": merge_node.get(_W + "val") if merge_node is not None else None,
                }
            )
        rows.append({"row": row_index, "cells": cells})
    return {"rows": rows}


def _block_from_element(
    index: int,
    element: ElementTree.Element,
    styles: Mapping[str, Mapping[str, Any]],
    relationships: Mapping[str, _Relationship],
    numbering_state: _NumberingState,
    text_reader: WordNativeTextReader | None = None,
) -> NativeBlock:
    text_reader = text_reader or WordNativeTextReader()
    kind = _local_name(element.tag)
    paragraph = element if kind == "p" else None
    style_id: str | None = None
    style_name: str | None = None
    numbering: Mapping[str, Any] | None = None
    table: Mapping[str, Any] | None = None
    if paragraph is not None:
        style_node = paragraph.find("w:pPr/w:pStyle", _NS)
        style_id = style_node.get(_W + "val") if style_node is not None else None
        style_name = str((styles.get(style_id or "") or {}).get("style_name") or style_id or "") or None
        numbering = _paragraph_numbering(
            paragraph, style_id, styles, numbering_state
        )
    else:
        if kind == "tbl":
            table = _table_projection(element, text_reader)
    native = text_reader.read(element)
    text = native.text
    features = set(_features(element, relationships))
    # These features are blockers only if the shared native reader could not
    # preserve them. Supported editable scripts/math no longer require vision.
    features.difference_update({"omml_equation", "run_vertical_alignment"})
    features.update(native.features)
    if numbering is not None:
        features.discard("automatic_numbering")
    if numbering and numbering.get("label") and text:
        text = f"{numbering['label']}{text}"
    elif numbering and numbering.get("label"):
        text = str(numbering["label"])
    return NativeBlock(
        index=index,
        block_kind="table" if kind == "tbl" else "paragraph",
        text=text,
        style_id=style_id,
        style_name=style_name,
        numbering=numbering,
        table=table,
        relationship_ids=_relationship_ids(element),
        features=tuple(sorted(features)),
        # Keep the legacy field for record compatibility, but never serialize
        # a second text projection that bypasses ancestor visibility or style
        # inheritance. All editable content is retained in the shared-reader
        # text above; there are no consumers of the old raw-run projection.
        rich_runs=(),
    )


def _iter_body_blocks(body: ElementTree.Element) -> Iterable[ElementTree.Element]:
    def descend(parent: ElementTree.Element) -> Iterable[ElementTree.Element]:
        for child in list(parent):
            local = _local_name(child.tag)
            if local in {"p", "tbl"}:
                yield child
            elif local in {"sdt", "sdtContent", "customXml", "ins", "moveTo"}:
                yield from descend(child)

    yield from descend(body)


def _is_toc(block: NativeBlock) -> bool:
    style = (block.style_id or "") + " " + (block.style_name or "")
    return "toc" in style.casefold()


def _is_heading(block: NativeBlock) -> bool:
    style = ((block.style_id or "") + " " + (block.style_name or "")).casefold()
    text = block.text.strip()
    if "heading" in style or "标题" in style:
        return True
    if text and len(text) <= 70 and _THEME_HEADING_RE.match(text):
        return True
    return bool(text and len(text) <= 70 and _HEADING_CUES.search(text))


def _question_match(
    block: NativeBlock, *, allow_parenthesized: bool
) -> re.Match[str] | None:
    if block.block_kind != "paragraph" or _is_toc(block):
        return None
    standard = _STANDARD_QUESTION_RE.match(block.text)
    if standard is not None:
        return standard
    if allow_parenthesized:
        return _PAREN_QUESTION_RE.match(block.text)
    return None


def _question_score(
    blocks: Sequence[NativeBlock],
    index: int,
    section_title: str | None,
    *,
    allow_parenthesized: bool,
) -> int:
    block = blocks[index]
    match = _question_match(block, allow_parenthesized=allow_parenthesized)
    if match is None:
        return 0
    body = (match.group("body") or "").strip()
    score = 1
    exercise_section = bool(section_title and _EXERCISE_CUES.search(section_title))
    knowledge_section = bool(
        section_title
        and re.match(r"^(?:知识点|考点)", section_title.strip())
        and not exercise_section
    )
    if exercise_section:
        score += 2
    has_question_cue = bool(_QUESTION_CUES.search(body))
    if has_question_cue:
        score += 2
    if len(_normalize_text(body)) >= 18:
        score += 1
    if block.features and len(_normalize_text(body)) <= 10:
        score += 2
    for following in blocks[index + 1 : index + 6]:
        if (
            _OPTION_RE.match(following.text)
            or _ANSWER_RE.search(following.text)
            or re.match(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]", following.text)
            or "____" in following.text
        ):
            score += 2
            break
        if _is_heading(following) or _question_match(
            following, allow_parenthesized=allow_parenthesized
        ):
            break
    # Numbered definitions and principles are common in review handouts.  A
    # number alone must not promote a knowledge-list entry into the question
    # bank, even if its sentence happens to be long.
    if knowledge_section and not _STRONG_QUESTION_CUES.search(body):
        return 0
    return score


def _split_answer_boundaries(text: str) -> tuple[str, str | None, str | None, tuple[str, ...]]:
    answer_match = _ANSWER_RE.search(text)
    analysis_match = _ANALYSIS_RE.search(text)
    warnings: list[str] = []
    if analysis_match is not None and answer_match is not None and analysis_match.start() < answer_match.start():
        warnings.append("answer_analysis_order_ambiguous")
    first = min(
        [match.start() for match in (answer_match, analysis_match) if match is not None],
        default=len(text),
    )
    question = text[:first].strip()
    answer: str | None = None
    analysis: str | None = None
    if answer_match is not None:
        answer_end = analysis_match.start() if analysis_match is not None and analysis_match.start() > answer_match.end() else len(text)
        answer = text[answer_match.end() : answer_end].strip() or None
    if analysis_match is not None:
        analysis = text[analysis_match.end() :].strip() or None
    return question, answer, analysis, tuple(warnings)


def _resource_projection(
    relationship_ids: Iterable[str], relationships: Mapping[str, _Relationship]
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    media: list[Mapping[str, Any]] = []
    ole: list[Mapping[str, Any]] = []
    for relationship_id in sorted(set(relationship_ids)):
        value = relationships.get(relationship_id)
        if value is None:
            continue
        projected = value.as_dict()
        if value.kind == "ole":
            ole.append(projected)
        elif value.kind in {"image", "chart_or_diagram"}:
            media.append(projected)
    return tuple(media), tuple(ole)


def _candidate_state(
    question_text: str,
    features: set[str],
    blockers: set[str],
) -> str:
    compact = _normalize_text(re.sub(r"【待查看原文：[^】]*】", "", question_text))
    visual = bool(features & _VISUAL_OR_UNSUPPORTED_FEATURES)
    if visual and len(compact) <= 8:
        return "visual_only_required"
    if visual or blockers:
        return "hybrid_visual_required"
    return "native_text_complete"


def _parse_candidates(
    *,
    blocks: Sequence[NativeBlock],
    package_id: str,
    source_name: str,
    source_sha256: str,
    document_role: str,
    pair_key: str,
    relationships: Mapping[str, _Relationship],
    allow_parenthesized: bool,
) -> tuple[QuestionCandidate, ...]:
    section_for_index: dict[int, str | None] = {}
    current_section: str | None = None
    for block in blocks:
        if _is_heading(block) and not _is_toc(block):
            current_section = block.text.strip() or current_section
        section_for_index[block.index] = current_section

    starts: list[tuple[int, re.Match[str], int]] = []
    for index, block in enumerate(blocks):
        match = _question_match(block, allow_parenthesized=allow_parenthesized)
        if match is None:
            continue
        score = _question_score(
            blocks,
            index,
            section_for_index.get(block.index),
            allow_parenthesized=allow_parenthesized,
        )
        if score >= 2:
            starts.append((index, match, score))

    if document_role == "solution_reference" and starts:
        marker_indexes: dict[str | None, list[int]] = defaultdict(list)
        for index, block in enumerate(blocks):
            if _ANSWER_RE.search(block.text) or _ANALYSIS_RE.search(block.text):
                marker_indexes[section_for_index.get(block.index)].append(index)
        grouped_sections: dict[str | None, int] = {}
        for section, indexes in marker_indexes.items():
            first_marker = min(indexes)
            questions_before = sum(
                start < first_marker
                and section_for_index.get(blocks[start].index) == section
                for start, _match, _score in starts
            )
            # Several numbered questions followed by one grouped answer area is
            # the comprehensive-paper layout.  Parenthesized answer/analysis
            # lines after the marker are not new question starts.
            if questions_before >= 2:
                grouped_sections[section] = first_marker
        starts = [
            item
            for item in starts
            if not (
                section_for_index.get(blocks[item[0]].index) in grouped_sections
                and item[0]
                >= grouped_sections[section_for_index.get(blocks[item[0]].index)]
            )
        ]

    result: list[QuestionCandidate] = []
    for ordinal, (start, match, score) in enumerate(starts, start=1):
        end = starts[ordinal][0] if ordinal < len(starts) else len(blocks)
        for cursor in range(start + 1, end):
            if _is_heading(blocks[cursor]) and not _is_toc(blocks[cursor]):
                end = cursor
                break
        segment = list(blocks[start:end])
        if not segment:
            continue
        context_indexes: list[int] = []
        body = (match.group("body") or "").strip()
        if _VISUAL_CUES.search(body):
            cursor = start - 1
            while cursor >= 0 and not blocks[cursor].text.strip():
                cursor -= 1
            if cursor >= 0 and blocks[cursor].features and not _is_heading(blocks[cursor]):
                context_indexes.append(blocks[cursor].index)
        full_text = "\n".join(block.text for block in segment if block.text.strip()).strip()
        question_text, answer_text, analysis_text, boundary_warnings = _split_answer_boundaries(full_text)
        features = {feature for block in segment for feature in block.features}
        if context_indexes:
            features.add("preceding_visual_context_possible")
        blockers = set(boundary_warnings)
        if score < 3:
            blockers.add("question_boundary_uncertain")
        if not _normalize_text(question_text):
            blockers.add("native_question_text_missing")
        if document_role == "solution_reference" and answer_text is None:
            blockers.add("answer_boundary_missing_in_solution_document")
        table_markers = sum(
            len(_STANDARD_QUESTION_RE.findall(block.text))
            + (
                len(_PAREN_QUESTION_RE.findall(block.text))
                if allow_parenthesized
                else 0
            )
            for block in segment
            if block.block_kind == "table"
        )
        if table_markers > 1:
            blockers.add("multiple_question_markers_in_table")
        for feature in features:
            blocker = _FEATURE_BLOCKERS.get(feature)
            if blocker:
                blockers.add(blocker)
        state = _candidate_state(question_text, features, blockers)
        relationship_ids = {
            relationship_id
            for block in segment
            for relationship_id in block.relationship_ids
        }
        media, ole = _resource_projection(relationship_ids, relationships)
        groups = match.groupdict()
        label = (
            groups.get("number")
            or groups.get("paren")
            or groups.get("prefixed")
            or str(ordinal)
        )
        candidate_id = "WHQ-" + _canonical_digest(
            {
                "schema": WORD_HANDOUT_IMPORT_SCHEMA,
                "source": source_sha256,
                "start": segment[0].index,
                "end": segment[-1].index,
                "question": _normalize_text(question_text),
            }
        )[:24]
        result.append(
            QuestionCandidate(
                candidate_id=candidate_id,
                package_id=package_id,
                source_document_name=source_name,
                source_document_sha256=source_sha256,
                document_role=document_role,
                pair_key=pair_key,
                ordinal=ordinal,
                source_question_label=str(label),
                section_title=section_for_index.get(segment[0].index),
                block_start=segment[0].index,
                block_end=segment[-1].index,
                context_block_indexes=tuple(context_indexes),
                native_text=full_text,
                question_text=question_text,
                answer_text=answer_text,
                analysis_text=analysis_text,
                native_blocks=tuple(block.as_dict() for block in segment),
                media_references=media,
                ole_references=ole,
                features=tuple(sorted(features)),
                blockers=tuple(sorted(blockers)),
                boundary_status=(
                    "blocked" if blockers & _BOUNDARY_BLOCKERS else "complete"
                ),
                import_state=state,
                question_import_state=state,
                record_role=(
                    "question_primary"
                    if document_role == "question_source"
                    else "answer_reference_duplicate"
                    if document_role == "solution_reference"
                    else "unassigned"
                ),
            )
        )
    return tuple(result)


def _document_state(candidates: Sequence[QuestionCandidate], blocks: Sequence[NativeBlock]) -> str:
    if candidates:
        states = {candidate.import_state for candidate in candidates}
        if states == {"native_text_complete"}:
            return "native_text_complete"
        if states == {"visual_only_required"}:
            return "visual_only_required"
        return "hybrid_visual_required"
    text = "".join(block.text for block in blocks).strip()
    features = {feature for block in blocks for feature in block.features}
    if text and not (features & _VISUAL_OR_UNSUPPORTED_FEATURES):
        return "native_text_complete"
    if text:
        return "hybrid_visual_required"
    return "visual_only_required"


def inspect_docx_native_summary(path: str | Path) -> dict[str, Any]:
    """Return a document-level summary used by the desktop file picker.

    This is intentionally only a preflight.  Per-question eligibility comes
    from :class:`WordHandoutImporter`.
    """

    result = WordHandoutImporter().scan_document(Path(path))
    picker_state = result.import_state
    # The file picker is intentionally coarse: an unbound packaged asset still
    # requires the detailed per-question pass before the whole document may be
    # described as native-only.  The batch importer below remains per-question.
    if (
        picker_state == "native_text_complete"
        and result.packaged_media_count + result.packaged_ole_count > 0
    ):
        picker_state = "hybrid_visual_required"
    return {
        "import_state": picker_state,
        "native_text_characters": result.native_text_characters,
        "native_paragraphs": result.native_paragraphs,
        "native_table_cells": result.native_table_cells,
        "visual_asset_count": result.packaged_media_count + result.packaged_ole_count,
        "referenced_media_count": result.referenced_media_count,
        "referenced_ole_count": result.referenced_ole_count,
        "candidate_count": len(result.candidates),
        "native_text_sha256": hashlib.sha256(
            "\n".join(candidate.native_text for candidate in result.candidates).encode("utf-8")
        ).hexdigest()
        if result.candidates
        else None,
        "xml_locator": "word/document.xml",
    }


def _candidate_similarity(left: QuestionCandidate, right: QuestionCandidate) -> float:
    left_text = _normalize_text(left.question_text)[:600]
    right_text = _normalize_text(right.question_text)[:600]
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()


_GROUPED_REFERENCE_RE = re.compile(
    r"(?:^|\n|\t|\s{2,})\s*[（(]\s*(?P<label>\d{1,2})\s*[）)]\s*",
    re.MULTILINE,
)


def _split_grouped_reference(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    matches = list(_GROUPED_REFERENCE_RE.finditer(text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        label = match.group("label")
        if value and label not in result:
            result[label] = value
    return result


def _severity(state: str | None) -> int:
    return {
        None: 0,
        "native_text_complete": 0,
        "hybrid_visual_required": 1,
        "visual_only_required": 2,
    }.get(state, 2)


def _combined_state(*states: str | None) -> str:
    severity = max((_severity(state) for state in states), default=0)
    return IMPORT_STATES[severity]


def _pair_candidates(documents: Sequence[DocumentResult]) -> tuple[DocumentResult, ...]:
    grouped: dict[tuple[str, str], list[DocumentResult]] = defaultdict(list)
    for document in documents:
        grouped[(document.package_id, document.pair_key)].append(document)
    replacements: dict[str, dict[str, QuestionCandidate]] = {}
    for group in grouped.values():
        questions = [
            candidate
            for document in group
            if document.document_role == "question_source"
            for candidate in document.candidates
        ]
        solutions = [
            candidate
            for document in group
            if document.document_role == "solution_reference"
            for candidate in document.candidates
        ]
        grouped_references: dict[
            tuple[str | None, str], tuple[QuestionCandidate, str, str | None]
        ] = {}
        for solution in solutions:
            answers = _split_grouped_reference(solution.answer_text)
            analyses = _split_grouped_reference(solution.analysis_text)
            for label, answer in answers.items():
                grouped_references.setdefault(
                    (solution.section_title, label),
                    (solution, answer, analyses.get(label)),
                )
        used: set[str] = set()
        for question in questions:
            same_label = [
                solution
                for solution in solutions
                if solution.candidate_id not in used
                and solution.source_question_label == question.source_question_label
            ]
            pool = same_label or [
                solution for solution in solutions if solution.candidate_id not in used
            ]
            scored = sorted(
                (( _candidate_similarity(question, solution), solution) for solution in pool),
                key=lambda item: (-item[0], abs(item[1].ordinal - question.ordinal)),
            )
            matched: QuestionCandidate | None = None
            similarity = 0.0
            if scored:
                similarity, candidate = scored[0]
                same_ordinal = candidate.ordinal == question.ordinal
                if similarity >= 0.62 or (same_ordinal and similarity >= 0.42):
                    matched = candidate
            question_blockers = set(question.blockers)
            if matched is None:
                question_blockers.add("answer_reference_unpaired")
                updated = replace(
                    question,
                    blockers=tuple(sorted(question_blockers)),
                    import_state=_combined_state(question.question_import_state, "hybrid_visual_required"),
                    pairing_status="blocked_unpaired",
                    quick_import_eligible=False,
                )
            else:
                used.add(matched.candidate_id)
                grouped_reference = grouped_references.get(
                    (matched.section_title, question.source_question_label)
                )
                reference_provider = (
                    grouped_reference[0] if grouped_reference is not None else matched
                )
                reference_answer = (
                    grouped_reference[1]
                    if grouped_reference is not None
                    else matched.answer_text
                )
                reference_analysis = (
                    grouped_reference[2]
                    if grouped_reference is not None
                    else matched.analysis_text
                )
                answer_state = reference_provider.import_state
                combined = _combined_state(question.question_import_state, answer_state)
                if reference_answer is None:
                    question_blockers.add("paired_reference_answer_missing")
                    combined = _combined_state(combined, "hybrid_visual_required")
                updated = replace(
                    question,
                    blockers=tuple(sorted(question_blockers)),
                    import_state=combined,
                    answer_import_state=answer_state,
                    pairing_status="paired",
                    paired_candidate_id=matched.candidate_id,
                    paired_source_document_sha256=matched.source_document_sha256,
                    reference_answer_text=reference_answer,
                    reference_analysis_text=reference_analysis,
                    reference_answer_candidate_id=reference_provider.candidate_id,
                    answer_authority="user_provided_teaching_material_reference",
                    answer_verified=False,
                    quick_import_eligible=(
                        combined == "native_text_complete"
                        and not question_blockers
                        and reference_answer is not None
                    ),
                )
                matched_updated = replace(
                    matched,
                    pairing_status="paired_as_reference",
                    paired_candidate_id=question.candidate_id,
                    paired_source_document_sha256=question.source_document_sha256,
                    quick_import_eligible=False,
                )
                replacements.setdefault(matched.source_document_sha256, {})[
                    matched.candidate_id
                ] = matched_updated
            replacements.setdefault(question.source_document_sha256, {})[
                question.candidate_id
            ] = updated
        for solution in solutions:
            if solution.candidate_id not in used:
                replacements.setdefault(solution.source_document_sha256, {})[
                    solution.candidate_id
                ] = replace(
                    solution,
                    pairing_status="blocked_unpaired_reference",
                    quick_import_eligible=False,
                )

    result: list[DocumentResult] = []
    for document in documents:
        mapped = replacements.get(document.source_sha256) or {}
        candidates = tuple(mapped.get(item.candidate_id, item) for item in document.candidates)
        result.append(replace(document, candidates=candidates))
    return tuple(result)


class WordHandoutImporter:
    """Scan, pair and locally cache native Word question candidates."""

    def __init__(self, store_root: str | Path | None = None) -> None:
        self.store_root = Path(store_root).resolve() if store_root is not None else None
        self._lock = threading.RLock()

    @staticmethod
    def discover_documents(expanded_root: str | Path) -> tuple[Path, ...]:
        root = Path(expanded_root).resolve()
        if not root.is_dir():
            raise WordHandoutImportError("expanded_root_missing", "讲义展开目录不存在。")
        documents: list[Path] = []
        for package in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not package.is_dir() or not _PACKAGE_RE.fullmatch(package.name):
                continue
            documents.extend(sorted(package.glob("*.docx"), key=lambda item: item.name.casefold()))
        if not documents:
            raise WordHandoutImportError("docx_inventory_empty", "展开目录中没有 DOCX。")
        return tuple(path.resolve() for path in documents)

    def scan_document(self, raw_path: str | Path) -> DocumentResult:
        path = Path(raw_path).resolve()
        if not path.is_file() or path.suffix.casefold() != ".docx":
            raise WordHandoutImportError("docx_invalid", "只接受本地 DOCX 文件。")
        source_sha256 = _sha256_file(path)
        package_id = _infer_package_id(path)
        role = _document_role(path.name)
        pair_key = _pair_key(path.name)
        try:
            with zipfile.ZipFile(path) as package:
                names, packaged_media, packaged_ole = _validate_container(package)
                root = _xml_root(package, "word/document.xml")
                assert root is not None
                body = root.find("w:body", _NS)
                if body is None:
                    raise WordHandoutImportError("docx_body_missing", "DOCX 缺少正文。")
                relationships = _relationships(package, names)
                styles = _style_catalog(package)
                text_reader = WordNativeTextReader(_xml_root(package, "word/styles.xml"))
                numbering = _numbering_catalog(package)
                numbering_state = _NumberingState(numbering)
                blocks = tuple(
                    _block_from_element(
                        index,
                        element,
                        styles,
                        relationships,
                        numbering_state,
                        text_reader,
                    )
                    for index, element in enumerate(_iter_body_blocks(body))
                )
        except zipfile.BadZipFile as exc:
            raise WordHandoutImportError("docx_container_invalid", "DOCX 容器无法打开。") from exc
        native_text_characters = sum(len(block.text) for block in blocks)
        native_paragraphs = sum(block.block_kind == "paragraph" for block in blocks)
        native_table_cells = sum(
            len(row.get("cells") or [])
            for block in blocks
            for row in ((block.table or {}).get("rows") or [])
        )
        candidates = _parse_candidates(
            blocks=blocks,
            package_id=package_id,
            source_name=path.name,
            source_sha256=source_sha256,
            document_role=role,
            pair_key=pair_key,
            relationships=relationships,
            allow_parenthesized=bool(
                re.search(r"检测卷|综合训练", path.name)
            ),
        )
        referenced_media = {
            item.get("relationship_id")
            for candidate in candidates
            for item in candidate.media_references
        }
        referenced_ole = {
            item.get("relationship_id")
            for candidate in candidates
            for item in candidate.ole_references
        }
        document_key = _canonical_digest(
            {
                "schema": WORD_HANDOUT_IMPORT_SCHEMA,
                "parser": WORD_HANDOUT_PARSER_VERSION,
                "source_sha256": source_sha256,
                "package_id": package_id,
                "source_name": path.name,
            }
        )
        warnings: list[str] = []
        if not candidates:
            warnings.append("no_reliable_question_boundary_found")
        if role == "unknown":
            warnings.append("document_role_unknown")
        return DocumentResult(
            document_key=document_key,
            package_id=package_id,
            source_path=str(path),
            source_name=path.name,
            source_sha256=source_sha256,
            source_bytes=path.stat().st_size,
            document_role=role,
            pair_key=pair_key,
            native_text_characters=native_text_characters,
            native_paragraphs=native_paragraphs,
            native_table_cells=native_table_cells,
            referenced_media_count=len(referenced_media),
            referenced_ole_count=len(referenced_ole),
            packaged_media_count=packaged_media,
            packaged_ole_count=packaged_ole,
            import_state=_document_state(candidates, blocks),
            candidates=candidates,
            warnings=tuple(warnings),
        )

    def _document_path(self, document_key: str) -> Path:
        assert self.store_root is not None
        return self.store_root / "documents" / f"{document_key}.json"

    def _batch_path(self, batch_id: str) -> Path:
        assert self.store_root is not None
        return self.store_root / "batches" / f"{batch_id}.json"

    @staticmethod
    def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_canonical_bytes(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise WordHandoutImportError("import_state_write_failed", "导入进度无法保存。") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _candidate_from_dict(value: Mapping[str, Any]) -> QuestionCandidate:
        tuple_fields = {
            "context_block_indexes",
            "native_blocks",
            "media_references",
            "ole_references",
            "features",
            "blockers",
        }
        payload = dict(value)
        for key in tuple_fields:
            payload[key] = tuple(payload.get(key) or ())
        return QuestionCandidate(**payload)

    @classmethod
    def _document_from_dict(cls, value: Mapping[str, Any]) -> DocumentResult:
        payload = dict(value)
        payload["candidates"] = tuple(
            cls._candidate_from_dict(item)
            for item in payload.get("candidates") or ()
            if isinstance(item, Mapping)
        )
        payload["warnings"] = tuple(payload.get("warnings") or ())
        payload["cache_hit"] = True
        return DocumentResult(**payload)

    def _load_cached(self, document_key: str) -> DocumentResult | None:
        if self.store_root is None:
            return None
        path = self._document_path(document_key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, Mapping)
            or value.get("schema_version") != WORD_HANDOUT_IMPORT_SCHEMA
            or value.get("parser_version") != WORD_HANDOUT_PARSER_VERSION
            or value.get("document_key") != document_key
            or not isinstance(value.get("document"), Mapping)
        ):
            return None
        try:
            return self._document_from_dict(value["document"])
        except (TypeError, ValueError, KeyError):
            return None

    def _cache_key_for_path(self, path: Path) -> tuple[str, str]:
        source_sha256 = _sha256_file(path)
        key = _canonical_digest(
            {
                "schema": WORD_HANDOUT_IMPORT_SCHEMA,
                "parser": WORD_HANDOUT_PARSER_VERSION,
                "source_sha256": source_sha256,
                "package_id": _infer_package_id(path),
                "source_name": path.name,
            }
        )
        return key, source_sha256

    def _persist_document(self, result: DocumentResult) -> None:
        if self.store_root is None:
            return
        value = {
            "schema_version": WORD_HANDOUT_IMPORT_SCHEMA,
            "parser_version": WORD_HANDOUT_PARSER_VERSION,
            "document_key": result.document_key,
            "document": replace(result, cache_hit=False).as_dict(),
        }
        self._atomic_write(self._document_path(result.document_key), value)

    def run_batch(
        self,
        raw_documents: Sequence[str | Path],
        *,
        persist: bool = True,
        progress_callback: Callable[[ImportProgress], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> BatchResult:
        documents = tuple(Path(item).resolve() for item in raw_documents)
        if not documents:
            raise WordHandoutImportError("docx_inventory_empty", "没有选择 DOCX 文件。")
        for path in documents:
            if not path.is_file() or path.suffix.casefold() != ".docx":
                raise WordHandoutImportError("docx_invalid", "批次中包含无效 DOCX。")
        if persist and self.store_root is None:
            raise WordHandoutImportError("import_store_missing", "未配置桌面导入存储目录。")
        source_basis: list[Mapping[str, Any]] = []
        keyed: list[tuple[Path, str]] = []
        for path in documents:
            document_key, source_sha256 = self._cache_key_for_path(path)
            keyed.append((path, document_key))
            source_basis.append(
                {
                    "document_key": document_key,
                    "source_sha256": source_sha256,
                    "package_id": _infer_package_id(path),
                    "source_name": path.name,
                }
            )
        batch_id = "WHB-" + _canonical_digest(
            {
                "schema": WORD_HANDOUT_IMPORT_SCHEMA,
                "parser": WORD_HANDOUT_PARSER_VERSION,
                "sources": sorted(source_basis, key=lambda item: str(item["document_key"])),
            }
        )[:24]
        completed: list[DocumentResult] = []
        failures: list[Mapping[str, Any]] = []
        cached_count = 0
        running_states: Counter[str] = Counter()
        for position, (path, document_key) in enumerate(keyed, start=1):
            if should_cancel is not None and should_cancel():
                raise WordHandoutImportError("import_cancelled", "导入已取消；已完成文档可在下次继续。")
            current: DocumentResult | None = None
            if persist:
                current = self._load_cached(document_key)
            if current is not None:
                cached_count += 1
            else:
                try:
                    current = self.scan_document(path)
                    if persist:
                        self._persist_document(current)
                except WordHandoutImportError as exc:
                    failures.append(
                        {
                            "package_id": _infer_package_id(path),
                            "source_name": path.name,
                            "error_code": exc.code,
                            "message_zh": exc.message_zh,
                        }
                    )
            if current is not None:
                completed.append(current)
                running_states[current.import_state] += 1
            if progress_callback is not None:
                progress_callback(
                    ImportProgress(
                        batch_id=batch_id,
                        processed_documents=position,
                        total_documents=len(keyed),
                        cached_documents=cached_count,
                        failed_documents=len(failures),
                        current_package_id=_infer_package_id(path),
                        current_document_name=path.name,
                        state_counts=dict(running_states),
                    )
                )
        paired_documents = _pair_candidates(completed)
        candidate_index = tuple(
            candidate
            for document in paired_documents
            for candidate in document.candidates
        )
        states = Counter(candidate.import_state for candidate in candidate_index)
        packages = {document.package_id for document in paired_documents}
        batch = BatchResult(
            batch_id=batch_id,
            schema_version=WORD_HANDOUT_IMPORT_SCHEMA,
            parser_version=WORD_HANDOUT_PARSER_VERSION,
            documents_total=len(keyed),
            documents_completed=len(paired_documents),
            documents_cached=cached_count,
            documents_failed=len(failures),
            packages_total=len(packages),
            inventory_complete_98x196=(
                len(packages) == 98
                and len(keyed) == 196
                and len(paired_documents) == 196
                and not failures
            ),
            candidates_total=len(candidate_index),
            primary_question_candidates=sum(
                candidate.record_role == "question_primary" for candidate in candidate_index
            ),
            solution_reference_candidates=sum(
                candidate.record_role == "answer_reference_duplicate"
                for candidate in candidate_index
            ),
            paired_question_candidates=sum(
                candidate.record_role == "question_primary"
                and candidate.pairing_status == "paired"
                for candidate in candidate_index
            ),
            quick_import_candidates=sum(
                candidate.quick_import_eligible for candidate in candidate_index
            ),
            visual_completion_candidates=sum(
                candidate.record_role == "question_primary"
                and candidate.import_state != "native_text_complete"
                for candidate in candidate_index
            ),
            boundary_blocked_candidates=sum(
                candidate.record_role == "question_primary"
                and candidate.boundary_status == "blocked"
                for candidate in candidate_index
            ),
            state_counts={state: states.get(state, 0) for state in IMPORT_STATES},
            failures=tuple(failures),
            documents=paired_documents,
            candidate_index=candidate_index,
        )
        if persist:
            assert self.store_root is not None
            manifest_path = self._batch_path(batch_id)
            # Keep the batch manifest light. Complete visible text and tables
            # live once in the content-addressed per-document records;
            # the batch only needs a resumable document index and teacher UI
            # candidate summaries.
            manifest = {
                key: value
                for key, value in batch.as_dict().items()
                if key
                not in {
                    "documents",
                    "candidate_index",
                    "manifest_path",
                    # Cache-hit count describes this invocation, not the
                    # committed source projection.  Omitting it keeps a
                    # resumed/idempotent batch byte-stable.
                    "documents_cached",
                }
            }
            manifest["documents"] = [
                {
                    "document_key": document.document_key,
                    "package_id": document.package_id,
                    "source_name": document.source_name,
                    "source_sha256": document.source_sha256,
                    "document_role": document.document_role,
                    "import_state": document.import_state,
                    "candidate_count": len(document.candidates),
                    "record_path": str(self._document_path(document.document_key)),
                }
                for document in paired_documents
            ]
            manifest["candidate_index"] = [
                {
                    "candidate_id": candidate.candidate_id,
                    "package_id": candidate.package_id,
                    "source_document_name": candidate.source_document_name,
                    "source_document_sha256": candidate.source_document_sha256,
                    "record_role": candidate.record_role,
                    "ordinal": candidate.ordinal,
                    "source_question_label": candidate.source_question_label,
                    "section_title": candidate.section_title,
                    "question_preview": candidate.question_text[:320],
                    "import_state": candidate.import_state,
                    "boundary_status": candidate.boundary_status,
                    "pairing_status": candidate.pairing_status,
                    "paired_candidate_id": candidate.paired_candidate_id,
                    "answer_present": bool(
                        candidate.reference_answer_text or candidate.answer_text
                    ),
                    "answer_verified": False,
                    "quick_import_eligible": candidate.quick_import_eligible,
                    "features": list(candidate.features),
                    "blockers": list(candidate.blockers),
                }
                for candidate in candidate_index
            ]
            manifest["manifest_path"] = str(manifest_path)
            self._atomic_write(manifest_path, manifest)
            batch = replace(batch, manifest_path=str(manifest_path))
        return batch

    def run_expanded_corpus(
        self,
        expanded_root: str | Path,
        *,
        persist: bool = True,
        progress_callback: Callable[[ImportProgress], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> BatchResult:
        return self.run_batch(
            self.discover_documents(expanded_root),
            persist=persist,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    def personal_handout_inventory(
        self,
        *,
        query: str = "",
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List committed personal handout candidates without central promotion."""

        if self.store_root is None:
            raise WordHandoutImportError("import_store_missing", "未配置桌面导入存储目录。")
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise WordHandoutImportError("inventory_limit_invalid", "讲义列表每页数量不正确。")
        if not isinstance(offset, int) or offset < 0:
            raise WordHandoutImportError("inventory_offset_invalid", "讲义列表起始位置不正确。")
        allowed_states = {
            None,
            *IMPORT_STATES,
            "quick_import_ready",
            "visual_completion_queue",
            "unpaired",
        }
        if state not in allowed_states:
            raise WordHandoutImportError("inventory_state_invalid", "讲义候选筛选状态不正确。")
        batch_root = self.store_root / "batches"
        manifests: list[tuple[int, Path, Mapping[str, Any]]] = []
        if batch_root.is_dir():
            for path in batch_root.glob("WHB-*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    modified = path.stat().st_mtime_ns
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(value, Mapping)
                    and value.get("schema_version") == WORD_HANDOUT_IMPORT_SCHEMA
                    and value.get("parser_version") == WORD_HANDOUT_PARSER_VERSION
                    and isinstance(value.get("candidate_index"), list)
                ):
                    manifests.append((modified, path, value))
        # The newest committed projection wins if the same source candidate was
        # imported in more than one batch.  This is an inventory merge, never a
        # central-catalog mutation.
        deduplicated: dict[str, dict[str, Any]] = {}
        source_batches: dict[str, str] = {}
        for _modified, path, manifest in sorted(manifests, key=lambda item: item[0]):
            batch_id = str(manifest.get("batch_id") or path.stem)
            for raw in manifest.get("candidate_index") or []:
                if not isinstance(raw, Mapping) or raw.get("record_role") != "question_primary":
                    continue
                candidate_id = str(raw.get("candidate_id") or "")
                if not candidate_id:
                    continue
                item = dict(raw)
                item["inventory_lane"] = (
                    "native_text_submitted"
                    if item.get("quick_import_eligible") is True
                    else "visual_completion_queue"
                )
                item["source_batch_id"] = batch_id
                deduplicated[candidate_id] = item
                source_batches[candidate_id] = batch_id
        all_items = sorted(
            deduplicated.values(),
            key=lambda item: (
                str(item.get("package_id") or ""),
                str(item.get("source_document_name") or ""),
                int(item.get("ordinal") or 0),
                str(item.get("candidate_id") or ""),
            ),
        )
        counts = {
            "personal_candidate_records": len(all_items),
            "native_text_submitted": sum(
                item.get("quick_import_eligible") is True for item in all_items
            ),
            "visual_completion_queue": sum(
                item.get("quick_import_eligible") is not True for item in all_items
            ),
            "native_text_complete": sum(
                item.get("import_state") == "native_text_complete" for item in all_items
            ),
            "hybrid_visual_required": sum(
                item.get("import_state") == "hybrid_visual_required" for item in all_items
            ),
            "visual_only_required": sum(
                item.get("import_state") == "visual_only_required" for item in all_items
            ),
            "paired": sum(item.get("pairing_status") == "paired" for item in all_items),
            "unpaired": sum(item.get("pairing_status") != "paired" for item in all_items),
            "answer_present": sum(item.get("answer_present") is True for item in all_items),
            "boundary_blocked": sum(
                item.get("boundary_status") == "blocked" for item in all_items
            ),
            "packages": len(
                {str(item.get("package_id")) for item in all_items if item.get("package_id")}
            ),
            "committed_batches": len(manifests),
        }
        needle = _normalize_text(query)

        def state_matches(item: Mapping[str, Any]) -> bool:
            if state is None:
                return True
            if state in IMPORT_STATES:
                return item.get("import_state") == state
            if state == "quick_import_ready":
                return item.get("quick_import_eligible") is True
            if state == "visual_completion_queue":
                return item.get("quick_import_eligible") is not True
            return item.get("pairing_status") != "paired"

        filtered = [
            item
            for item in all_items
            if state_matches(item)
            and (
                not needle
                or needle
                in _normalize_text(
                    " ".join(
                        str(item.get(key) or "")
                        for key in (
                            "package_id",
                            "source_document_name",
                            "section_title",
                            "question_preview",
                            "source_question_label",
                        )
                    )
                )
            )
        ]
        page = filtered[offset : offset + limit]
        return {
            "schema_version": PERSONAL_HANDOUT_INVENTORY_SCHEMA,
            "source_schema_version": WORD_HANDOUT_IMPORT_SCHEMA,
            "parser_version": WORD_HANDOUT_PARSER_VERSION,
            "scope": "personal_handouts",
            "central_catalog_mutated": False,
            "counts": counts,
            "query": query,
            "state": state,
            "page": {
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "filtered_total": len(filtered),
                "has_more": offset + len(page) < len(filtered),
            },
            "items": page,
        }


__all__ = [
    "IMPORT_STATES",
    "PERSONAL_HANDOUT_INVENTORY_SCHEMA",
    "WORD_HANDOUT_IMPORT_SCHEMA",
    "WORD_HANDOUT_PARSER_VERSION",
    "BatchResult",
    "DocumentResult",
    "ImportProgress",
    "QuestionCandidate",
    "WordHandoutImportError",
    "WordHandoutImporter",
    "inspect_docx_native_summary",
]
