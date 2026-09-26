"""Read-only XML occurrence locations, separate from the native Word preview.

These are source-structure coordinates, not rendered pages or native Word
selections. Repeated relationships remain separate occurrences. No content is
inferred from placeholder text, and no image is borrowed from another object.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections import defaultdict
from copy import deepcopy

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from .desktop_preparation_sources import (
    PreparationSourceError,
    PreparationSourcesService,
    _block_text,
    _body_blocks,
    _digest,
    _local,
    _word_images,
)
from .word_handout_import import _validate_container
from .word_native_text import NATIVE_WORD_TEXT_REVISION, WordNativeTextReader, _METADATA

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
O = "{urn:schemas-microsoft-com:office:office}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_LOCATIONS = 5_000
MAX_CONTEXT_CHARS = 8_000_000
_SUPPORTED_IMAGE_RELATIONSHIPS = frozenset({RT.IMAGE})
_METADATA_TAGS = {W + name for name in _METADATA}
_IMAGE_TAGS = {A + "blip", V + "imagedata"}
_VISUAL_TAGS = {W + name for name in ("drawing", "pict", "object")}
_TEXT_TAGS = {W + name for name in (
    "body", "p", "r", "t", "delText", "tab", "br", "cr", "noBreakHyphen",
    "softHyphen", "tbl", "tr", "tc", "sdt", "sdtContent", "customXml",
    "ins", "del", "moveFrom", "moveTo", "hyperlink",
)}
_KINDS = {
    W + "sym": ("symbol", "特殊字体符号"),
    W + "instrText": ("field_code", "复杂域指令节点"),
    W + "delInstrText": ("field_code", "已删除的域指令节点"),
    W + "fldChar": ("field_code", "复杂域边界节点"),
    W + "fldSimple": ("field_code", "简单域节点"),
    W + "altChunk": ("alt_chunk", "外部文档片段引用"),
    W + "txbxContent": ("text_box", "文本框内容"),
    W + "footnoteReference": ("note_reference", "脚注引用"),
    W + "endnoteReference": ("note_reference", "尾注引用"),
    W + "commentReference": ("note_reference", "批注引用"),
}


class _LocationTextReader(WordNativeTextReader):
    """Reuse each unchanged XML paragraph's native projection during one read."""

    def __init__(self, styles_root):
        super().__init__(styles_root)
        self._native_text = {}

    def read(self, element):
        if element not in self._native_text:
            self._native_text[element] = super().read(element)
        return self._native_text[element]


def _nearest(node, tag):
    return next((parent for parent in node.iterancestors() if parent.tag == tag), None)


def _xml_locator(node, body):
    positions = []
    current = node
    while current is not body:
        parent = current.getparent()
        if parent is None:
            raise PreparationSourceError("对象不在原Word正文结构中。")
        positions.append(parent.index(current) + 1)
        current = parent
    return "word/document.xml#/w:document/w:body" + "".join(
        f"/*[{position}]" for position in reversed(positions)
    )


def _branches(node, body):
    return tuple(
        _xml_locator(parent, body)
        for parent in reversed(list(node.iterancestors()))
        if parent.tag in {MC + "Choice", MC + "Fallback"}
    )


def _kind(node):
    tag = node.tag
    if not isinstance(tag, str):
        return None
    if any(parent.tag in _METADATA_TAGS for parent in node.iterancestors()):
        return None
    if tag in _IMAGE_TAGS:
        return "image", "原图引用"
    if tag == O + "OLEObject":
        return "ole_object", "旧式OLE对象"
    if tag == M + "oMath" or (
        tag == M + "oMathPara" and not any(child.tag == M + "oMath" for child in node.iterdescendants())
    ):
        return "omml", "原生OMML公式"
    if tag in _KINDS:
        return _KINDS[tag]
    if tag in _VISUAL_TAGS:
        if any(child.tag in _IMAGE_TAGS or child.tag == O + "OLEObject" for child in node.iterdescendants()):
            return None
        return "drawing_or_object", "无独立原图的图形或嵌入对象"
    if tag == MC + "AlternateContent":
        if any(child.tag in _IMAGE_TAGS | _VISUAL_TAGS | {O + "OLEObject", M + "oMath"} for child in node.iterdescendants()):
            return None
        return "compatibility_content", "兼容显示容器"
    if tag in _TEXT_TAGS or tag in _METADATA_TAGS or tag in {MC + "Choice", MC + "Fallback", M + "oMathPara"}:
        return None
    # A visual/formula/code container already describes its implementation
    # nodes. Do not turn drawing properties or equation runs into fake objects.
    if any(parent.tag in _VISUAL_TAGS | set(_KINDS) | {M + "oMath", M + "oMathPara"} for parent in node.iterancestors()):
        return None
    parent = node.getparent()
    if parent is None or parent.tag not in _TEXT_TAGS | {MC + "Choice", MC + "Fallback"}:
        return None
    return "special_object", "特殊XML对象或容器"


def _visibility(node, reader):
    ancestors = [node, *node.iterancestors()]
    notices, states = [], []
    if any(parent.tag in {W + "del", W + "moveFrom", W + "delInstrText"} for parent in ancestors):
        states.append("deleted_or_moved_from")
        notices.append("修订删除或移出内容；不作为当前可见正文。")
    if any(parent.tag in {W + "ins", W + "moveTo"} for parent in ancestors):
        states.append("inserted_or_moved_to")
        notices.append("包含插入或移入修订；显示状态需核对原Word。")
    for run in ancestors:
        if run.tag not in {W + "r", M + "oMath", M + "oMathPara"}:
            continue
        # A textbox has its own paragraphs. Its normal inner style must not
        # overwrite the style governing an enclosing hidden run/paragraph.
        paragraph = _nearest(run, W + "p")
        style = paragraph.find(f"{W}pPr/{W}pStyle") if paragraph is not None else None
        style_id = style.get(W + "val") if style is not None else None
        hidden = reader._run_property(run, style_id, "vanish")
        if hidden is not None and hidden.casefold() not in {"0", "false", "off"}:
            states.append("hidden")
            notices.append("有效文字样式标记为隐藏；不作为当前可见正文。")
            break
    for branch in reversed(ancestors):
        if branch.tag in {MC + "Choice", MC + "Fallback"}:
            name = _local(branch)
            states.append("compatibility_" + name.lower())
            notices.append(f"兼容显示{name}分支；未判定Word采用哪一分支，不视为已确认可见。")
    return list(dict.fromkeys(states)), list(dict.fromkeys(notices))


def _scoped_nodes(container, tag, scope_tag):
    return [node for node in container.iterdescendants() if node.tag == tag and _nearest(node, scope_tag) is container]


def _positive_property(node, child_path, default):
    value = node.find(child_path)
    result = int(value.get(W + "val", str(default))) if value is not None else default
    if result < 0:
        raise PreparationSourceError("原Word表格网格位置无效。")
    return result


def _cell_position(cell, row, table):
    rows = _scoped_nodes(table, W + "tr", W + "tbl")
    cells = _scoped_nodes(row, W + "tc", W + "tr")
    column = _positive_property(row, f"{W}trPr/{W}gridBefore", 0) + 1
    for sibling in cells:
        span = _positive_property(sibling, f"{W}tcPr/{W}gridSpan", 1)
        if span < 1:
            raise PreparationSourceError("原Word表格合并列数无效。")
        if sibling is cell:
            label = f"源第{rows.index(row) + 1}行·源第{column}列"
            if span > 1:
                label = f"源第{rows.index(row) + 1}行·源第{column}—{column + span - 1}列（横向合并{span}列）"
            for prop, axis in (("vMerge", "纵向"), ("hMerge", "旧式横向")):
                merge = cell.find(f"{W}tcPr/{W}{prop}")
                if merge is not None:
                    value = merge.get(W + "val", "continue")
                    label += f"（{axis}合并" + ({"restart": "起点", "continue": "续接"}.get(value, "标记:" + value)) + "）"
            return label
        column += span
    raise PreparationSourceError("原Word表格单元格位置无法绑定。")


def _position(node, element, block_index, elements, ordinal):
    if element.tag == W + "p":
        number = sum(candidate.tag == W + "p" for candidate in elements[:block_index])
        parts = [f"正文第{number}段（区块{block_index}）"]
    elif element.tag == W + "tbl":
        number = sum(candidate.tag == W + "tbl" for candidate in elements[:block_index])
        parts = [f"正文第{number}张表（区块{block_index}）"]
    else:
        parts = [f"正文区块{block_index}（{_local(element)}容器）"]
    ancestors = list(reversed([node, *node.iterancestors()]))
    tables = [parent for parent in ancestors if parent.tag == W + "tbl"]
    for table in tables:
        outer_cell = _nearest(table, W + "tc")
        if outer_cell is not None:
            inner_tables = _scoped_nodes(outer_cell, W + "tbl", W + "tc")
            parts.append(f"格内第{inner_tables.index(table) + 1}张嵌套表")
        cell = next((parent for parent in ancestors if parent.tag == W + "tc" and _nearest(parent, W + "tbl") is table), None)
        if cell is not None:
            row = _nearest(cell, W + "tr")
            if row is not None:
                parts.append(_cell_position(cell, row, table))
    paragraph = _nearest(node, W + "p")
    cell = _nearest(node, W + "tc")
    box = _nearest(node, W + "txbxContent")
    if paragraph is not None and box is not None:
        paragraphs = _scoped_nodes(box, W + "p", W + "txbxContent")
        parts.append(f"文本框内第{paragraphs.index(paragraph) + 1}段")
    elif paragraph is not None and cell is not None:
        paragraphs = _scoped_nodes(cell, W + "p", W + "tc")
        parts.append(f"格内第{paragraphs.index(paragraph) + 1}段")
    elif paragraph is not None and paragraph is not element:
        paragraphs = [child for child in element.iterdescendants() if child.tag == W + "p"]
        parts.append(f"容器内源第{paragraphs.index(paragraph) + 1}段")
    parts.append(f"源对象{ordinal}")
    return " · ".join(parts)


def _relationship(document, node, rid, body):
    record = {"relationship_id": rid, "xml_locator": _xml_locator(node, body), "status": "missing_relationship"}
    relation = document.part.rels.get(rid) if rid else None
    if relation is None:
        return record
    record.update(relationship_type=relation.reltype, external=relation.is_external)
    if relation.is_external:
        record.update(status="external_not_loaded", target_ref=relation.target_ref)
    else:
        part = relation.target_part
        record.update(status="internal", package_part=str(part.partname), content_type=part.content_type,
                      sha256=hashlib.sha256(part.blob).hexdigest(), bytes_count=len(part.blob))
    return record


def _image_occurrences(element, index, document, catalogue, body):
    seen, bindings = {}, {}
    for node in element.iter():
        if not isinstance(node.tag, str) or _local(node) not in {"blip", "imagedata"}:
            continue
        rid = node.get(R + "embed") or node.get(R + "id")
        if rid and rid not in seen:
            seen[rid] = len(seen) + 1
        if node.tag not in _IMAGE_TAGS:
            continue
        reference = _relationship(document, node, rid or node.get(R + "link"), body)
        asset = catalogue.get(f"word-b{index}-image{seen[rid]}") if rid else None
        if not (
            rid and reference["status"] == "internal"
            and reference.get("relationship_type") in _SUPPORTED_IMAGE_RELATIONSHIPS
            and reference.get("content_type", "").startswith("image/")
            and asset is not None and asset["sha256"] == reference["sha256"]
        ):
            asset = None
        bindings[node] = (deepcopy(asset), reference)
    return bindings


def _ole_assets(node, bindings, body):
    host = _nearest(node, W + "object")
    shape_id = node.get("ShapeID")
    if host is None or not shape_id:
        return []
    owners = [candidate for candidate in host.iterdescendants()
              if candidate.tag == O + "OLEObject" and candidate.get("ShapeID") == shape_id
              and _nearest(candidate, W + "object") is host
              and _branches(candidate, body) == _branches(node, body)]
    if len(owners) != 1:
        return []
    shapes = [shape for shape in host.iterdescendants() if shape.tag == V + "shape"
              and shape.get("id") == shape_id and _nearest(shape, W + "object") is host
              and _branches(shape, body) == _branches(node, body)]
    if len(shapes) != 1:
        return []
    return [deepcopy(bindings[child][0]) for child in shapes[0].iterdescendants()
            if child in bindings and bindings[child][0] is not None
            and _nearest(child, V + "shape") is shapes[0]
            and _nearest(child, W + "object") is host
            and _branches(child, body) == _branches(node, body)]


class WordSourceLocationService:
    """Locate one verified archived source without changing its old preview."""

    def __init__(self, facade):
        self.facade = facade

    def _compile(self, batch_id, source_id, source_sha256, expected_revision, block_indices):
        if any(not isinstance(value, str) or not value.strip() or len(value) > 256
               or any(ord(character) < 32 for character in value) for value in (batch_id, source_id)) or any(
            not isinstance(value, str) or _SHA.fullmatch(value) is None for value in (source_sha256, expected_revision)
        ):
            raise PreparationSourceError("原Word定位选择信息不完整，请重新打开原文。")
        if block_indices is not None and (
            not isinstance(block_indices, list) or not block_indices
            or any(type(index) is not int or index < 1 for index in block_indices)
            or len(set(block_indices)) != len(block_indices)
        ):
            raise PreparationSourceError("原Word定位区块范围不正确。")
        source = self.facade._imported_word_source(batch_id, source_id)
        data = source.content
        if not isinstance(data, bytes) or not 0 < len(data) <= 40 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != source_sha256:
            raise PreparationSourceError("原Word已变化，请重新打开后定位。")
        preview = self.facade.imported_word_preview(batch_id, source_id, read_only=True)
        if (
            not isinstance(preview, dict) or preview.get("source_sha256") != source_sha256
            or preview.get("source_name") != source.filename
            or preview.get("extraction_revision") != NATIVE_WORD_TEXT_REVISION
            or preview.get("revision") != expected_revision
            or _digest({key: value for key, value in preview.items() if key != "revision"}) != expected_revision
        ):
            raise PreparationSourceError("原Word预览已变化，请刷新后再定位。")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                _validate_container(package)
            document = Document(io.BytesIO(data))
            body = document._element.body
            elements = list(_body_blocks(body))
            blocks = preview.get("blocks")
            if not isinstance(blocks, list) or len(elements) != len(blocks) or any(
                not isinstance(block, dict) or type(block.get("index")) is not int
                or block["index"] != index or not isinstance(block.get("text"), str)
                for index, block in enumerate(blocks, 1)
            ):
                raise PreparationSourceError("原Word区块位置已变化，请重新打开原文。")
            chosen = set(block_indices) if block_indices is not None else set(range(1, len(blocks) + 1))
            if not chosen.issubset(set(range(1, len(blocks) + 1))):
                raise PreparationSourceError("原Word定位区块范围不正确。")
            reader = _LocationTextReader(document.styles.element)
            for element, block in zip(elements, blocks):
                if _block_text(element, document, set(), reader) != block["text"]:
                    raise PreparationSourceError("原Word内容与当前原文预览不一致，请刷新后再定位。")
            original_assets = _word_images(elements, document)
            if original_assets != preview.get("assets"):
                raise PreparationSourceError("原Word图片目录与当前原文预览不一致。")
            catalogue = {asset["asset_id"]: asset for asset in original_assets}
            result, notices = [], []
            location_count = context_char_count = 0

            def reserve_context(text):
                nonlocal context_char_count
                context_char_count += len(text)
                if context_char_count > MAX_CONTEXT_CHARS:
                    raise PreparationSourceError(
                        "原Word定位上下文超过输出限制，请限制区块范围或拆分后查看。"
                    )

            for index, (element, block) in enumerate(zip(elements, blocks), 1):
                if index not in chosen:
                    continue
                reserve_context(block["text"])
                # Independent bounds also cover unknown containers whose old
                # text projection intentionally returned just a placeholder.
                nodes = list(element.iter())
                if len(nodes) > 100_000 or any(len(list(node.iterancestors())) > 160 for node in nodes):
                    raise PreparationSourceError("原Word对象结构超过定位限制。")
                bindings = _image_occurrences(element, index, document, catalogue, body)
                counters, locations = defaultdict(int), []
                for node in nodes:
                    kind = _kind(node)
                    if kind is None:
                        continue
                    location_count += 1
                    if location_count > MAX_LOCATIONS:
                        raise PreparationSourceError(
                            "原Word定位对象数量超过限制，请限制区块范围或拆分后查看。"
                        )
                    paragraph = _nearest(node, W + "p")
                    counters[paragraph if paragraph is not None else element] += 1
                    ordinal = counters[paragraph if paragraph is not None else element]
                    states, messages = _visibility(node, reader)
                    context = reader.read(paragraph).text if paragraph is not None else block["text"]
                    if states:
                        context = "【原结构内容；显示状态待核对】" + context
                    reserve_context(context)
                    locator = _xml_locator(node, body)
                    location = {
                        "location_id": "word-location-" + _digest([source_sha256, locator]),
                        "block_index": index, "kind": kind[0], "label": kind[1],
                        "xml_locator": locator, "position_text": _position(node, element, index, elements, ordinal),
                        "context_text": context, "notices": messages, "assets": [],
                        "source_states": states, "current_visibility_confirmed": False,
                    }
                    if node.tag in _IMAGE_TAGS:
                        asset, reference = bindings[node]
                        location["relationship_references"] = [reference]
                        if asset is not None:
                            location["assets"] = [asset]
                        else:
                            messages.append("此图片引用无可绑定的内部原图（缺失、外链或关系类型不符）；未使用其他图片替代。")
                    elif node.tag == O + "OLEObject":
                        host = _nearest(node, W + "object")
                        reference = _relationship(document, node, node.get(R + "id"), body)
                        location["host_xml_locator"] = _xml_locator(host, body) if host is not None else None
                        location["relationship_references"] = [reference]
                        messages.append("二进制对象节点：" + locator)
                        if reference["status"] == "internal" and reference.get("relationship_type", "").rstrip("/").rsplit("/", 1)[-1].lower() in {"oleobject", "package"}:
                            messages.append("原对象包内路径：" + reference["package_part"] + "；未执行或解码对象。")
                            location["assets"] = _ole_assets(node, bindings, body)
                        if not location["assets"]:
                            messages.append("没有通过本对象ShapeID及同一显示分支绑定的替代原图；不能用邻图补足。")
                    elif kind[0] == "field_code":
                        code = node.get(W + "instr") if node.tag == W + "fldSimple" else node.text
                        if code:
                            location["field_instruction"] = code
                        location["field_character_type"] = node.get(W + "fldCharType")
                        location["complete_field_span_verified"] = False
                        messages.append("仅定位此域代码或边界节点；未证明复杂域的完整起止范围。")
                    elif node.tag == W + "sym":
                        location["symbol_font"] = node.get(W + "font")
                        location["symbol_code"] = node.get(W + "char")
                        messages.append("保留字体及字符编码；未猜测实际字形。")
                    elif node.tag == W + "altChunk":
                        location["relationship_references"] = [_relationship(document, node, node.get(R + "id"), body)]
                        messages.append("只记录文档片段引用；未载入、执行或访问其内容。")
                    elif kind[0] == "omml":
                        messages.append("原生公式XML位置；不代表公式版式已经视觉核对。")
                    elif kind[0] == "compatibility_content":
                        location["source_states"].append("compatibility_unresolved")
                        messages.append("容器含兼容显示分支；未判定Choice或Fallback是否被采用。")
                    elif kind[0] in {"drawing_or_object", "special_object"}:
                        messages.append("只记录原XML结构；未补写内容或借用邻图。")
                    locations.append(location)
                    notices.extend(messages)
                result.append({"block_index": index, "context_text": block["text"],
                               "xml_locator": _xml_locator(element, body), "locations": locations})
            return {
                "source_name": source.filename, "source_sha256": source_sha256,
                "source_revision": expected_revision, "blocks": result,
                "warnings": list(dict.fromkeys([
                    "位置依据原Word XML结构，不是页码或Word原生选中；未执行页面视觉复核。",
                    *preview.get("warnings", []), *notices,
                ])),
            }, data
        except PreparationSourceError:
            raise
        except Exception as exc:
            raise PreparationSourceError("原Word结构暂时无法定位；原件未修改，请核对原文件。") from exc

    def preview(self, batch_id, source_id, source_sha256, expected_revision, block_indices=None):
        return self._compile(batch_id, source_id, source_sha256, expected_revision, block_indices)[0]

    def select_native(self, batch_id, source_id, source_sha256, expected_revision,
                      location_id, *, block_indices=None, cancelled=None, revalidate=None):
        from .desktop_native_word_selection import select_original

        if not isinstance(location_id, str) or re.fullmatch(r"word-location-[0-9a-f]{64}", location_id) is None:
            raise PreparationSourceError("原 Word 定位标识不正确，请重新选择对象。")
        arguments = (batch_id, source_id, source_sha256, expected_revision, block_indices)
        projection, data = self._compile(*arguments)
        location = next((entry for block in projection["blocks"] for entry in block["locations"]
                         if entry["location_id"] == location_id), None)
        if location is None or location["kind"] not in {"image", "omml"} or location["source_states"]:
            raise PreparationSourceError("此对象暂不支持 Word 内自动选中，请按原文位置核对。")

        def current():
            if revalidate is not None:
                revalidate()
            latest, current_data = self._compile(*arguments)
            match = next((entry for block in latest["blocks"] for entry in block["locations"]
                          if entry["location_id"] == location_id), None)
            if current_data != data or match != location:
                raise PreparationSourceError("原文或所选范围已变化，请重新定位。")

        result = select_original(
            self.facade.imported_word_path(batch_id, source_id), data, location["xml_locator"],
            cancelled=cancelled, revalidate=current,
        )
        return {**result, "location_id": location_id, "source_revision": expected_revision}

    def image(self, batch_id, source_id, source_sha256, expected_revision, location_id, asset_id):
        asset_match = re.fullmatch(r"word-b([1-9][0-9]*)-image[1-9][0-9]*", asset_id) if isinstance(asset_id, str) and len(asset_id) <= 128 else None
        if (not isinstance(location_id, str) or re.fullmatch(r"word-location-[0-9a-f]{64}", location_id) is None
                or asset_match is None):
            raise PreparationSourceError("原图定位标识不正确，请重新选择原对象。")
        # The parsed block is only a lookup hint. _compile still revalidates
        # the complete source/revision/projection, then this exact occurrence
        # must own the requested asset. Other blocks do not consume the scoped
        # display budget after the user has narrowed a large document.
        projection, data = self._compile(
            batch_id, source_id, source_sha256, expected_revision,
            [int(asset_match.group(1))],
        )
        location = next((entry for block in projection["blocks"] for entry in block["locations"]
                         if entry["location_id"] == location_id), None)
        asset = next((item for item in location["assets"] if item["asset_id"] == asset_id), None) if location else None
        if asset is None:
            raise PreparationSourceError("此原图不属于所选对象位置，请重新选择。")
        result = PreparationSourcesService(self.facade.paths.workspace_root).word_asset_bytes(
            data, asset_id, expected_sha256=asset["sha256"], render_metafiles=True,
        )
        return {**result, "location_id": location_id, "asset_id": asset_id,
                "source_sha256": source_sha256, "source_revision": expected_revision,
                "source_states": list(location["source_states"]), "notices": list(location["notices"])}


__all__ = ["WordSourceLocationService"]
