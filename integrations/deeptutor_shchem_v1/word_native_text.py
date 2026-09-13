"""Read editable Word content without flattening chemical scripts or objects.

This small shared reader is used by desktop routing and personal Word intake.
It reads no files, follows no links and never treats embedded/OLE pixels as text.
"""

from __future__ import annotations

from dataclasses import dataclass

from .word_native_math import omml_text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
NATIVE_WORD_TEXT_REVISION = "20260909-editable-chemistry-v2"
_TRANSPARENT_CONTAINERS = {W + tag for tag in ("sdt", "sdtContent", "customXml")}
_MAX_NODES = 100_000
_MAX_DEPTH = 160
_MAX_TEXT = 2_000_000
_OBJECTS = {
    "drawing": ("word_drawing_or_shape", "图片或图形"),
    "pict": ("vml_drawing_or_shape", "图片或旧式图形"),
    "object": ("ole_object_reference", "嵌入对象或旧公式"),
    "txbxContent": ("text_box_layout_dependency", "文本框"),
    "altChunk": ("unsupported_embedded_part", "外部文档片段"),
}
_METADATA = {
    "pPr",
    "rPr",
    "tblPr",
    "tblPrEx",
    "tblGrid",
    "trPr",
    "tcPr",
    "sectPr",
    "sdtPr",
    "sdtEndPr",
    "customXmlPr",
    "bookmarkStart",
    "bookmarkEnd",
    "proofErr",
    "permStart",
    "permEnd",
    "lastRenderedPageBreak",
}


@dataclass(frozen=True)
class NativeText:
    text: str
    features: tuple[str, ...]
    native_math_count: int


def _check_tree_budget(element):
    """Count wrappers, metadata and skipped content before rendering anything."""
    pending = [(element, 0)]
    seen = {}
    text_size = 0
    while pending:
        node, depth = pending.pop()
        if depth > _MAX_DEPTH or id(node) in seen:
            raise ValueError("Word block exceeds structural limits")
        # Retain lxml proxies so their Python ids cannot be reused mid-walk.
        seen[id(node)] = node
        if len(seen) > _MAX_NODES or len(node) + len(pending) > _MAX_NODES - len(seen):
            raise ValueError("Word block exceeds structural limits")
        text_size += len(node.text or "") + len(node.tail or "")
        text_size += sum(len(key) + len(value) for key, value in node.attrib.items())
        if text_size > _MAX_TEXT:
            raise ValueError("Word block exceeds text limit")
        pending.extend((child, depth + 1) for child in reversed(node))


def native_body_blocks(body, locator="word/document.xml#/w:body"):
    """Keep actual XML child positions, including wrapped source blocks."""
    if locator.count("/*[") > 128:
        raise ValueError("Word body exceeds structural limits")
    for position, child in enumerate(body, 1):
        path = f"{locator}/*[{position}]"
        if child.tag in _TRANSPARENT_CONTAINERS:
            yield from native_body_blocks(child, path)
        elif child.tag not in {W + tag for tag in _METADATA}:
            yield child, path


class WordNativeTextReader:
    """Preserve source order, table cells, supported OMML and effective scripts."""

    def __init__(self, styles_root=None):
        self.styles = {}
        self.defaults = None
        self.default_paragraph = None
        if styles_root is not None:
            self.defaults = styles_root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
            for style in styles_root.findall(W + "style"):
                key = style.get(W + "styleId")
                if key:
                    self.styles[key] = style
                    if (
                        style.get(W + "default") in {"1", "true"}
                        and style.get(W + "type") == "paragraph"
                    ):
                        self.default_paragraph = key

    def _style_properties(self, style_id):
        seen = set()
        while style_id and style_id not in seen:
            seen.add(style_id)
            style = self.styles.get(style_id)
            if style is None:
                break
            yield style.find(W + "rPr")
            based = style.find(W + "basedOn")
            style_id = based.get(W + "val") if based is not None else None

    def _run_property(self, run, paragraph_style, name):
        direct = run.find(W + "rPr")
        rstyle = run.find(f"{W}rPr/{W}rStyle")
        rstyle_id = rstyle.get(W + "val") if rstyle is not None else None
        props = [
            direct,
            *self._style_properties(rstyle_id),
            *self._style_properties(paragraph_style or self.default_paragraph),
            self.defaults,
        ]
        for prop in props:
            if prop is not None:
                item = prop.find(W + name)
                if item is not None:
                    return item.get(W + "val", "true")
        return None

    def read(self, element) -> NativeText:
        _check_tree_budget(element)
        features = set()
        math_count = 0

        def gap(feature, label):
            features.add(feature)
            return "【待查看原文：" + label + "】"

        def expanded_children(node, depth):
            """Unwrap known containers while preserving original block nodes.

            Revision containers are handled here as well as in ``visit`` so
            table row/cell expansion cannot expose deleted content or discard
            the tracked-change feature. Unknown wrappers remain intact for an
            explicit gap. The preflight covers every node, including skipped
            revisions and metadata, against the same structural budget.
            """
            for child in node:
                tag = child.tag
                if tag in {W + "del", W + "moveFrom"}:
                    features.add("tracked_changes")
                elif tag in {W + "ins", W + "moveTo"}:
                    features.add("tracked_changes")
                    yield from expanded_children(child, depth + 1)
                elif tag in _TRANSPARENT_CONTAINERS:
                    yield from expanded_children(child, depth + 1)
                else:
                    yield child, depth + 1

        def visit(node, paragraph_style=None, depth=0):
            nonlocal math_count
            if depth > _MAX_DEPTH:
                raise ValueError("Word block exceeds structural limits")
            tag = node.tag
            if tag in {M + "oMath", M + "oMathPara"}:
                hidden = self._run_property(node, paragraph_style, "vanish")
                if hidden is not None and hidden.casefold() not in {
                    "0",
                    "false",
                    "off",
                }:
                    features.add("hidden_text_omitted")
                    return ""
                text = omml_text(node)
                if text is None:
                    return gap("omml_equation", "数学公式")
                math_count += 1
                return text
            if not isinstance(tag, str) or not tag.startswith(W):
                return gap("unsupported_embedded_part", "特殊对象")
            local = tag[len(W) :]
            if local in _METADATA:
                return ""
            if local in {"del", "moveFrom"}:
                features.add("tracked_changes")
                return ""
            if local in {"ins", "moveTo"}:
                features.add("tracked_changes")
            if local in _OBJECTS:
                return gap(*_OBJECTS[local])
            if local == "sym":
                return gap("unsupported_symbol", "特殊字体符号")
            if local in {"footnoteReference", "endnoteReference", "commentReference"}:
                return gap("note_or_comment_reference", "脚注、尾注或批注")
            if local in {"instrText", "fldChar"}:
                features.add("field_code_dependency")
                return ""
            if local == "fldSimple":
                features.add("field_code_dependency")
            if local == "hyperlink":
                features.add("external_relationship_reference")
            if local == "t":
                return node.text or ""
            if local == "tab":
                return "\t"
            if local in {"br", "cr"}:
                if node.get(W + "type") in {"page", "column"}:
                    features.add("layout_break_dependency")
                return "\n"
            if local == "noBreakHyphen":
                return "‑"
            if local == "softHyphen":
                return "\u00ad"
            if local == "p":
                style = node.find(f"{W}pPr/{W}pStyle")
                paragraph_style = (
                    style.get(W + "val")
                    if style is not None
                    else self.default_paragraph
                )
                if node.find(f"{W}pPr/{W}numPr") is not None:
                    features.add("automatic_numbering")
            if local == "r":
                hidden = self._run_property(node, paragraph_style, "vanish")
                if hidden is not None and hidden.casefold() not in {
                    "0",
                    "false",
                    "off",
                }:
                    features.add("hidden_text_omitted")
                    return ""
                text = "".join(
                    visit(child, paragraph_style, depth + 1) for child in node
                )
                vertical = self._run_property(node, paragraph_style, "vertAlign")
                if text and vertical in {"subscript", "superscript"}:
                    return ("_{" if vertical == "subscript" else "^{") + text + "}"
                if vertical not in {None, "baseline", "subscript", "superscript"}:
                    features.add("run_vertical_alignment")
                return text
            if local == "tbl":
                rows = ["【表格开始：按原行列顺序】"]
                row_index = 0
                for row, row_depth in expanded_children(node, depth):
                    if row.tag in {W + name for name in _METADATA}:
                        continue
                    if row.tag != W + "tr":
                        rows.append(gap("unsupported_embedded_part", "特殊表格行容器"))
                        continue
                    row_index += 1
                    before = row.find(f"{W}trPr/{W}gridBefore")
                    column = (
                        int(before.get(W + "val", "0")) + 1 if before is not None else 1
                    )
                    for cell, cell_depth in expanded_children(row, row_depth):
                        if cell.tag in {W + name for name in _METADATA}:
                            continue
                        if cell.tag != W + "tc":
                            rows.append(
                                gap("unsupported_embedded_part", "特殊表格单元格容器")
                            )
                            continue
                        span_node = cell.find(f"{W}tcPr/{W}gridSpan")
                        span = (
                            int(span_node.get(W + "val", "1"))
                            if span_node is not None
                            else 1
                        )
                        if span < 1 or column < 1:
                            raise ValueError("Invalid Word table grid")
                        merge = cell.find(f"{W}tcPr/{W}vMerge")
                        note = f"第{row_index}行·第{column}列"
                        if span > 1:
                            note += f"；横向合并{span}列"
                        if merge is not None:
                            note += (
                                "；纵向合并起点"
                                if merge.get(W + "val") == "restart"
                                else "；纵向合并续接上方"
                            )
                        rows.extend(
                            [
                                "〔" + note + "〕",
                                visit(cell, paragraph_style, cell_depth),
                            ]
                        )
                        column += span
                rows.append("【表格结束】")
                return "\n".join(rows)
            if local not in {
                "p",
                "tc",
                "sdt",
                "sdtContent",
                "customXml",
                "ins",
                "moveTo",
                "hyperlink",
                "fldSimple",
                "body",
            }:
                return gap("unsupported_embedded_part", "特殊正文容器")
            parts = []
            previous_block = False
            for child, child_depth in expanded_children(node, depth):
                text = visit(child, paragraph_style, child_depth)
                is_block = child.tag in {W + "p", W + "tbl"}
                if text:
                    if parts and (is_block or previous_block):
                        parts.append("\n")
                    parts.append(text)
                    previous_block = is_block
            return "".join(parts)

        text = visit(element).strip()
        if len(text) > _MAX_TEXT:
            raise ValueError("Word block exceeds text limit")
        return NativeText(text, tuple(sorted(features)), math_count)
