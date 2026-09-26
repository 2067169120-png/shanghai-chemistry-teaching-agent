"""Pure, fail-closed evidence for selecting an object in the *original* Word file.

No Office calls, file writes, text-derived offsets or object activation live here.
The caller must obtain all snapshots from one read-only main-story COM session,
check native Start/End/StoryType, and recheck the source hash before selecting.
Flat OPC is a serialization, not a stable XPath: its relationship IDs, run splits
and drawing IDs may change. We first bind the complete ordered semantic tree,
then bind the native range to that tree. The supported subset is deliberately
small. Active parts/relationships and unsupported binary members are rejected
throughout the package before Office; semantic mapping covers the main story.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import posixpath
import re
import zipfile
from urllib.parse import unquote, urlsplit

from lxml import etree

from .desktop_preparation_sources import PreparationSourceError
from .word_handout_import import _validate_container
from .word_native_text import WordNativeTextReader

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
XML = "{http://www.w3.org/XML/1998/namespace}"
_REL_BASE = R[1:-1] + "/"
_MAIN = "word/document.xml"
_SCHEMA = "shchem.native-word-mapping.v1"
MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAX_FLAT_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 80 * 1024 * 1024
MAX_XML_NODES = 150_000
MAX_XML_DEPTH = 160
MAX_EVENTS = 150_000
MAX_OBJECTS = 5_000
MAX_PLAN_BYTES = 8 * 1024 * 1024
_LOCATOR = re.compile(r"word/document\.xml#/w:document/w:body((?:/\*\[[1-9][0-9]{0,6}\])+)\Z")
_RASTER_TYPES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff"}
_XML_TYPES = {"application/xml", "text/xml", "application/vnd.ms-word.styleswitheffects+xml"} | {
    "application/vnd.openxmlformats-" + suffix + "+xml" for suffix in (
        "package.relationships", "package.core-properties", "officedocument.customxmlproperties",
        "officedocument.extended-properties", "officedocument.custom-properties", "officedocument.theme",
        "officedocument.wordprocessingml.document.main", "officedocument.wordprocessingml.styles",
        "officedocument.wordprocessingml.settings", "officedocument.wordprocessingml.websettings",
        "officedocument.wordprocessingml.fonttable", "officedocument.wordprocessingml.numbering",
        "officedocument.wordprocessingml.footnotes", "officedocument.wordprocessingml.endnotes",
        "officedocument.wordprocessingml.header", "officedocument.wordprocessingml.footer",
        "officedocument.wordprocessingml.comments", "officedocument.wordprocessingml.document.glossary",
    )
}
_PASSIVE_RELATIONS = {
    _REL_BASE + name for name in (
        "officeDocument", "image", "styles", "stylesWithEffects", "settings",
        "webSettings", "fontTable", "numbering", "theme", "footnotes", "endnotes",
        "header", "footer", "comments", "customXml", "customXmlProps",
        "extended-properties", "custom-properties", "glossaryDocument",
    )
} | {
    "http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects",
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
}
_FORBIDDEN_W = {
    "object", "control", "pict", "txbxContent", "altChunk", "attachedTemplate", "mailMerge", "fldChar",
    "fldSimple", "instrText", "delInstrText", "del", "ins", "moveFrom", "moveTo",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
    "customXmlInsRangeStart", "customXmlDelRangeStart", "framePr",
}
_BODY_METADATA = {"bookmarkStart", "bookmarkEnd", "proofErr", "lastRenderedPageBreak"}
_FORMAT_PROPERTIES = {
    "pPr", "rPr", "pStyle", "rStyle", "rFonts", "b", "bCs", "i", "iCs",
    "caps", "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss",
    "imprint", "noProof", "snapToGrid", "color", "spacing", "w", "kern",
    "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd",
    "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout",
    "vanish", "webHidden", "specVanish", "keepNext", "keepLines", "pageBreakBefore",
    "widowControl", "suppressLineNumbers", "pBdr", "tabs", "tab", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
    "autoSpaceDN", "bidi", "adjustRightInd", "ind", "contextualSpacing",
    "mirrorIndents", "suppressOverlap", "jc", "textDirection", "textAlignment",
    "textboxTightWrap", "outlineLvl", "divId", "cnfStyle",
    "top", "left", "bottom", "right", "between", "bar", "start", "end",
}
_TABLE_PROPERTIES = set((
    "tblPr tblPrEx tblGrid gridCol trPr tcPr tblStyle tblW jc tblCellSpacing tblInd "
    "tblBorders tblLayout tblCellMar tblLook tblCaption tblDescription bidiVisual "
    "tblOverlap tblStyleRowBandSize tblStyleColBandSize tcW gridSpan hMerge vMerge "
    "tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark cnfStyle "
    "divId gridBefore gridAfter wBefore wAfter cantSplit trHeight tblHeader "
    "top left bottom right start end insideH insideV tl2br tr2bl"
).split())
_SECTION_PROPERTIES = set((
    "sectPr headerReference footerReference type pgSz pgMar paperSrc pgBorders "
    "lnNumType pgNumType cols col formProt vAlign noEndnote titlePg textDirection "
    "bidi rtlGutter docGrid top left bottom right"
).split())
_MATH_NAMES = set((
    "oMath acc accPr bar barPr box boxPr borderBox borderBoxPr d dPr eqArr eqArrPr "
    "f fPr func funcPr groupChr groupChrPr limLow limLowPr limUpp limUppPr m mPr "
    "mr nary naryPr phantom phantomPr rad radPr sPre sPrePr sSub sSubPr sSubSup "
    "sSubSupPr sSup sSupPr e num den fName lim sub sup deg r rPr t ctrlPr "
    "aln alnScr argPr argSz baseJc begChr brk cGp cGpRule chr count cSp degHide "
    "diff endChr grow hideBot hideLeft hideRight hideTop jc limLoc lit maxDist "
    "mc mcPr mcs noBreak nor objDist opEmu plcHide pos rSp rSpRule scr sepChr "
    "show shp smallFrac strikeBLTR strikeH strikeTLBR strikeV sty subHide supHide "
    "transp type vertJc wrapIndent wrapRight zeroAsc zeroDesc zeroWid"
).split())


def _fail(code, detail=""):
    raise PreparationSourceError("无法验证原 Word 对象位置：" + code + ("；" + detail if detail else ""))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _xml(data):
    if not isinstance(data, bytes) or len(data) > MAX_FLAT_BYTES:
        _fail("xml_budget")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        _fail("xml_declaration_forbidden")
    try:
        root = etree.fromstring(data, etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True,
            huge_tree=False, remove_comments=False, remove_pis=False,
        ))
    except (etree.XMLSyntaxError, ValueError) as exc:
        _fail("invalid_xml", str(exc).split("\n", 1)[0][:120])
    if root.getroottree().docinfo.doctype:
        _fail("xml_declaration_forbidden")
    pending = [(root, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_XML_NODES or depth > MAX_XML_DEPTH:
            _fail("xml_structure_budget")
        if not isinstance(node.tag, str):
            # Removing comments/PIs would renumber C04's actual-child XPath.
            _fail("xml_non_element_node")
        pending.extend((child, depth + 1) for child in node)
    return root


def _name(name):
    if not isinstance(name, str) or not name or "\\" in name or "%" in name or ":" in name:
        _fail("invalid_part_name")
    name = name.removeprefix("/")
    if not name or any(part in {"", ".", ".."} for part in name.split("/")):
        _fail("invalid_part_name")
    return name


def _resolve(owner, target):
    try:
        parsed = urlsplit(target)
        decoded = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError):
        _fail("external_or_ambiguous_target")
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in target:
        _fail("external_or_ambiguous_target")
    if "%" in decoded or ":" in decoded or "\\" in decoded:
        _fail("external_or_ambiguous_target")
    joined = decoded.lstrip("/") if decoded.startswith("/") else posixpath.join(posixpath.dirname(owner), decoded)
    return _name(posixpath.normpath(joined))


def _active_check(root):
    # Inspect every XML part, including headers/settings, before COM can open it.
    for node in root.iter():
        tag = node.tag
        if tag.startswith(W):
            local = tag[len(W):]
            if local in _FORBIDDEN_W or local.endswith("Change"):
                _fail("unsupported_active_or_revision_content", local)
            if local in {"vanish", "webHidden", "specVanish"} and node.get(W + "val", "true").casefold() not in {"0", "false", "off"}:
                _fail("hidden_content")
        if tag in {
            "{urn:schemas-microsoft-com:office:office}OLEObject",
            "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent",
            WP + "anchor",
        }:
            _fail("unsupported_object_or_compatibility_branch")
        if tag.startswith("{urn:schemas-microsoft-com:vml}"):
            _fail("unsupported_legacy_drawing")


def _raster_valid(mime, raw):
    return {
        "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
        "image/gif": raw.startswith((b"GIF87a", b"GIF89a")),
        "image/bmp": raw.startswith(b"BM"),
        "image/tiff": raw.startswith((b"II*\x00", b"MM\x00*")),
    }.get(mime, False)


class _Package:
    def __init__(self, parts):
        self.parts = parts
        self.relationships = {}
        for name, part in parts.items():
            if any(token in name.casefold() for token in ("/activex/", "/embeddings/", "vbaproject", "customui/")):
                _fail("active_package_part")
            content_type = part["type"].casefold()
            if any(token in content_type for token in ("macroenabled", "vba", "oleobject", "activex", "octet-stream")):
                _fail("active_package_part")
            root = part.get("root")
            if root is not None:
                if not name.casefold().endswith((".xml", ".rels")):
                    _fail("unknown_package_member")
                if content_type not in _XML_TYPES:
                    _fail("unknown_xml_content_type")
                _active_check(root)
            elif content_type not in _RASTER_TYPES:
                _fail("unsupported_binary_part")
            else:
                extensions = {"image/png": {"png"}, "image/jpeg": {"jpg", "jpeg"},
                              "image/gif": {"gif"}, "image/bmp": {"bmp"}, "image/tiff": {"tif", "tiff"}}
                if name.rsplit(".", 1)[-1].casefold() not in extensions[content_type]:
                    _fail("unknown_package_member")
                if not _raster_valid(content_type, part.get("data", b"")):
                    _fail("image_content_type_mismatch")
            if name.endswith(".rels"):
                if root is None or root.tag != REL + "Relationships":
                    _fail("invalid_relationship_part")
                if name == "_rels/.rels":
                    owner = ""
                else:
                    directory, leaf = posixpath.split(name)
                    if not directory.endswith("/_rels"):
                        _fail("invalid_relationship_part")
                    owner = posixpath.join(directory[:-6], leaf[:-5])
                rels = {}
                for rel in root:
                    rid, rel_type = rel.get("Id"), rel.get("Type")
                    if rel.tag != REL + "Relationship" or not rid or rid in rels:
                        _fail("duplicate_or_invalid_relationship")
                    if rel.get("TargetMode", "Internal") != "Internal" or rel_type not in _PASSIVE_RELATIONS:
                        _fail("external_or_active_relationship")
                    target = _resolve(owner, rel.get("Target", ""))
                    if target not in parts:
                        _fail("missing_relationship_part")
                    rels[rid] = (rel_type, target)
                self.relationships[owner] = rels
        main = [target for typ, target in self.relationships.get("", {}).values() if typ == _REL_BASE + "officeDocument"]
        if main != [_MAIN] or _MAIN not in parts:
            _fail("unsupported_main_part")
        self.document = parts[_MAIN].get("root")
        if self.document is None or self.document.tag != W + "document":
            _fail("invalid_document_part")
        if parts[_MAIN]["type"] != "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
            _fail("unsupported_document_content_type")
        style_targets = [target for typ, target in self.relationships.get(_MAIN, {}).values() if typ == _REL_BASE + "styles"]
        if len(style_targets) > 1:
            _fail("ambiguous_styles")
        styles = parts[style_targets[0]].get("root") if style_targets else None
        if style_targets and (styles is None or styles.tag != W + "styles"):
            _fail("invalid_styles_part")
        self.reader = WordNativeTextReader(styles)

    def image(self, rid):
        relationship = self.relationships.get(_MAIN, {}).get(rid)
        if not relationship or relationship[0] != _REL_BASE + "image":
            _fail("invalid_image_relationship")
        part = self.parts[relationship[1]]
        raw = part.get("data")
        mime = part["type"]
        if not raw or mime not in _RASTER_TYPES:
            _fail("unsupported_image_part")
        if not _raster_valid(mime, raw):
            _fail("image_content_type_mismatch")
        return [mime, len(raw), _sha(raw)]


def _source_package(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_SOURCE_BYTES:
        _fail("source_budget")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _validate_container(archive)
            entries = [info for info in archive.infolist() if not info.is_dir()]
            names = [_name(info.filename) for info in entries]
            if len({name.casefold() for name in names}) != len(names):
                _fail("duplicate_package_part")
            if sum(info.file_size for info in entries) > MAX_PACKAGE_BYTES or any(info.file_size > MAX_PART_BYTES or info.flag_bits & 1 for info in entries):
                _fail("package_budget")
            if "[Content_Types].xml" not in names:
                _fail("missing_content_types")
            types = _xml(archive.read("[Content_Types].xml"))
            if types.tag != CT + "Types":
                _fail("invalid_content_types")
            defaults, overrides = {}, {}
            for node in types:
                if node.tag == CT + "Default":
                    key, mapping = node.get("Extension", "").casefold(), defaults
                elif node.tag == CT + "Override":
                    key, mapping = _name(node.get("PartName", "")), overrides
                else:
                    _fail("invalid_content_types")
                if not key or key in mapping:
                    _fail("duplicate_content_type")
                mapping[key] = node.get("ContentType", "")
            parts = {}
            total_nodes = 0
            for name in names:
                if name == "[Content_Types].xml":
                    continue
                raw = archive.read(name)
                mime = overrides.get(name, defaults.get(name.rsplit(".", 1)[-1].casefold(), ""))
                part = {"type": mime, "data": raw}
                if mime.endswith("+xml") or mime in {"application/xml", "text/xml"}:
                    part["root"] = _xml(raw)
                    total_nodes += sum(1 for _ in part["root"].iter())
                    if total_nodes > MAX_XML_NODES:
                        _fail("package_structure_budget")
                parts[name] = part
            return _Package(parts)
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, PreparationSourceError):
            raise
        _fail("invalid_docx_package")


def _flat_package(flat_xml):
    if not isinstance(flat_xml, str) or len(flat_xml) > MAX_FLAT_BYTES:
        _fail("flat_budget")
    try:
        raw = flat_xml.encode("utf-8")
    except UnicodeError:
        _fail("invalid_flat_xml_encoding")
    root = _xml(raw)
    if root.tag != PKG + "package":
        _fail("expected_flat_opc")
    parts, folded, total = {}, set(), 0
    for node in root:
        if node.tag != PKG + "part" or len(parts) >= 12_000:
            _fail("invalid_flat_part")
        name = _name(node.get(PKG + "name", ""))
        if name.casefold() in folded:
            _fail("duplicate_package_part")
        folded.add(name.casefold())
        part = {"type": node.get(PKG + "contentType", "")}
        if len(node) != 1:
            _fail("invalid_flat_payload")
        payload = node[0]
        if payload.tag == PKG + "xmlData" and len(payload) == 1:
            part["root"] = payload[0]
            size = len(etree.tostring(payload[0]))
        elif payload.tag == PKG + "binaryData" and not len(payload):
            encoded = re.sub(r"[\r\n\t ]", "", payload.text or "")
            if len(encoded) > (MAX_PART_BYTES + 2) // 3 * 4:
                _fail("part_budget")
            try:
                part["data"] = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                _fail("invalid_flat_binary")
            size = len(part["data"])
        else:
            _fail("invalid_flat_payload")
        total += size
        if size > MAX_PART_BYTES or total > MAX_PACKAGE_BYTES:
            _fail("package_budget")
        parts[name] = part
    return _Package(parts), _sha(raw)


def _locator(node, body):
    steps = []
    while node is not body:
        parent = node.getparent()
        if parent is None:
            _fail("locator_outside_main_story")
        steps.append(f"/*[{parent.index(node) + 1}]")
        node = parent
    return "word/document.xml#/w:document/w:body" + "".join(reversed(steps))


def _format_check(node):
    for item in node.iter():
        if not item.tag.startswith(W) or item.tag[len(W):] not in _FORMAT_PROPERTIES or (item.text or "").strip() or (item.tail or "").strip():
            _fail("unsupported_format_property", etree.QName(item).localname)


def _metadata_check(node, allowed):
    for item in node.iter():
        if not item.tag.startswith(W) or item.tag[len(W):] not in allowed or (item.text or "").strip():
            _fail("unsupported_metadata", etree.QName(item).localname)


def _math(node):
    if (node.tail or "").strip():
        _fail("unexpected_math_text")
    if node.tag == W + "rPr":
        _format_check(node)
        children = []
        for child in node:
            # Word 16 adds precisely this default font and splits/rejoins runs.
            if child.tag == W + "rFonts" and dict(child.attrib) == {W + "ascii": "Cambria Math", W + "hAnsi": "Cambria Math"}:
                continue
            if child.tag == W + "noProof" and child.get(W + "val", "true") in {"true", "1"}:
                continue
            children.append([child.tag, sorted(child.attrib.items()), child.text or "", [_math(c) for c in child]])
        return children
    if not node.tag.startswith(M) or node.tag[len(M):] not in _MATH_NAMES:
        _fail("unsupported_math_node", etree.QName(node).localname)
    attrs = sorted((k, v) for k, v in node.attrib.items() if k != XML + "space")
    if any(not k.startswith(M) for k, _ in attrs):
        _fail("unsupported_math_attribute")
    if node.tag == M + "r":
        mprops, wprops, texts = [], [], []
        for child in node:
            if child.tag == M + "rPr" and not mprops:
                mprops = _math(child)
            elif child.tag == W + "rPr" and not wprops:
                wprops = _math(child)
            elif child.tag == M + "t" and not len(child) and not any(k != XML + "space" for k in child.attrib):
                texts.append(child.text or "")
            else:
                _fail("unsupported_math_run")
        if attrs:
            _fail("unsupported_math_run_attribute")
        return ["mr", mprops, wprops, "".join(texts)]
    children = []
    for child in node:
        canonical = _math(child)
        if canonical and canonical[0] == "mr" and children and children[-1][0] == "mr" and children[-1][1:3] == canonical[1:3]:
            children[-1][3] += canonical[3]
        else:
            children.append(canonical)
    return [node.tag, attrs, node.text or "", children]


def _image(node, package):
    if len(node) != 1 or node[0].tag != WP + "inline":
        _fail("unsupported_drawing_host")
    inline = node[0]
    blips = list(inline.iter(A + "blip"))
    docprs = list(inline.iter(WP + "docPr"))
    if len(blips) != 1 or len(docprs) != 1 or not re.fullmatch(r"[0-9]{1,10}", docprs[0].get("id", "")):
        _fail("ambiguous_image_host")
    docpr_id = int(docprs[0].get("id"))
    if docpr_id > 4_294_967_295:
        _fail("invalid_drawing_identity")
    blip = blips[0]
    if set(blip.attrib) - {R + "embed", "cstate"} or not blip.get(R + "embed"):
        _fail("external_or_unsupported_image")
    asset = package.image(blip.get(R + "embed"))
    allowed = {
        WP + x for x in ("inline", "extent", "effectExtent", "docPr", "cNvGraphicFramePr")
    } | {A + x for x in (
        "graphic", "graphicData", "graphicFrameLocks", "blip", "stretch", "fillRect",
        "srcRect", "xfrm", "off", "ext", "prstGeom", "avLst", "picLocks",
    )} | {PIC + x for x in ("pic", "nvPicPr", "cNvPr", "cNvPicPr", "blipFill", "spPr")}

    def canonical(item):
        if item.tag not in allowed:
            _fail("unsupported_drawing_node", etree.QName(item).localname)
        if item.tag == A + "graphicData" and item.get("uri") != PIC[1:-1]:
            _fail("unsupported_graphic_kind")
        attrs = []
        for key, value in sorted(item.attrib.items()):
            if item.tag in {WP + "docPr", PIC + "cNvPr"} and key in {"id", "name"}:
                continue
            if item.tag == WP + "inline" and key in {"distT", "distB", "distL", "distR"} and value == "0":
                continue
            if item.tag == WP + "inline" and key in {
                "{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}anchorId",
                "{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}editId",
            }:
                continue
            if item.tag == A + "blip" and key == R + "embed":
                value = asset
            attrs.append([key, value])
        if item.tag == WP + "effectExtent" and not len(item) and set(item.attrib) <= {"l", "t", "r", "b"} and all(v == "0" for v in item.attrib.values()):
            return None
        if item.tag == A + "avLst" and not len(item) and not item.attrib:
            return None
        if (item.text or "").strip() or (item.tail or "").strip():
            _fail("unexpected_drawing_text")
        children = [value for child in item if (value := canonical(child)) is not None]
        return [item.tag, attrs, children]

    return _sha(_json(canonical(inline))), str(docpr_id), blip


def _projection(package):
    body = package.document.find(W + "body")
    if body is None or len(package.document) != 1:
        _fail("unsupported_document_structure")
    events, objects, tables = [], [], []
    table_ends = {}

    def emit(event):
        if len(events) >= MAX_EVENTS:
            _fail("event_budget")
        events.append(event)

    def host(kind, signature, node, docpr=None):
        if len(objects) >= MAX_OBJECTS:
            _fail("object_budget")
        objects.append({"kind": kind, "signature": signature, "xml_locator": _locator(node, body),
                        "event_index": len(events), "docpr_id": docpr,
                        "table_start": tables[0] if tables else None,
                        "native_type": 3 if kind == "inline" else 0 if any(
                            ancestor.tag == M + "oMathPara" for ancestor in node.iterancestors()
                        ) else 1})
        emit(["object", kind, signature])

    def visit(node, style=None, vertical="baseline"):
        tag = node.tag
        if tag == W + "p":
            pstyle = node.find(W + "pPr/" + W + "pStyle")
            style = pstyle.get(W + "val") if pstyle is not None else package.reader.default_paragraph
        if tag in {W + "r", M + "r", M + "oMath", M + "oMathPara"}:
            for prop in ("vanish", "webHidden", "specVanish"):
                value = package.reader._run_property(node, style, prop)
                if value is not None and value.casefold() not in {"0", "false", "off"}:
                    _fail("hidden_content")
            value = package.reader._run_property(node, style, "vertAlign")
            vertical = value or vertical
            if vertical not in {"baseline", "subscript", "superscript"}:
                _fail("unsupported_vertical_alignment")
        if tag == W + "drawing":
            signature, docpr, blip = _image(node, package)
            host("inline", signature, blip, docpr)
            return
        if tag == M + "oMath":
            host("math", _sha(_json(_math(node))), node)
            return
        if tag == W + "t":
            if len(node) or set(node.attrib) - {XML + "space"}:
                _fail("unsupported_text_node")
            text = node.text or ""
            if text:
                if events and events[-1][:2] == ["text", vertical]:
                    events[-1][2] += text
                else:
                    emit(["text", vertical, text])
            return
        if tag in {W + "tab", W + "br", W + "cr", W + "noBreakHyphen", W + "softHyphen"}:
            if len(node) or (node.text or "").strip():
                _fail("unsupported_character_node")
            emit(["character", tag, [[key, value] for key, value in sorted(node.attrib.items())]])
            return
        if tag in {W + "rPr", W + "pPr"}:
            _format_check(node)
            return
        if tag == W + "sectPr":
            _metadata_check(node, _SECTION_PROPERTIES)
            return
        if tag in {W + name for name in _BODY_METADATA}:
            if len(node) or (node.text or "").strip():
                _fail("unsupported_metadata")
            return
        if tag in {W + "tblPr", W + "tblPrEx", W + "tblGrid", W + "trPr", W + "tcPr"}:
            # Layout is not a character coordinate. Grid cardinality and merge
            # topology are retained on their actual containers below.
            _metadata_check(node, _TABLE_PROPERTIES)
            return
        if tag == M + "oMathParaPr":
            if any(c.tag != M + "jc" for c in node):
                _fail("unsupported_math_paragraph")
            return
        container = tag in {W + "body", W + "p", W + "tbl", W + "tr", W + "tc", M + "oMathPara"}
        if not container and tag != W + "r":
            _fail("unsupported_story_node", etree.QName(node).localname)
        descriptor = []
        if tag == W + "tbl":
            grids = node.findall(W + "tblGrid/" + W + "gridCol")
            if not grids:
                _fail("missing_table_grid")
            descriptor = ["columns", len(grids)]
            tables.append(len(events))
        elif tag == W + "tc":
            for prop in ("gridSpan", "vMerge", "hMerge"):
                items = node.findall(W + "tcPr/" + W + prop)
                if len(items) > 1:
                    _fail("ambiguous_merge_property")
                if items:
                    value = items[0].get(W + "val", "continue" if prop != "gridSpan" else "1")
                    if prop == "gridSpan" and (not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 32767):
                        _fail("invalid_grid_span")
                    if prop != "gridSpan" and value not in {"restart", "continue"}:
                        _fail("invalid_merge")
                    descriptor.append([prop, value])
        elif tag == W + "tr":
            for prop in ("gridBefore", "gridAfter"):
                items = node.findall(W + "trPr/" + W + prop)
                if len(items) > 1:
                    _fail("ambiguous_grid_property")
                if items:
                    value = items[0].get(W + "val", "0")
                    if not re.fullmatch(r"[0-9]{1,5}", value) or int(value) > 32767:
                        _fail("invalid_grid_property")
                    descriptor.append([prop, value])
        if container:
            emit(["open", tag, descriptor])
        if (node.text or "").strip():
            _fail("unexpected_story_text")
        for child in node:
            if (child.tail or "").strip():
                _fail("unexpected_story_tail")
            visit(child, style, vertical)
        if container:
            emit(["close", tag])
        if tag == W + "tbl":
            table_ends[tables.pop()] = len(events)

    visit(body)
    docprs = [item["docpr_id"] for item in objects if item["kind"] == "inline"]
    if len(docprs) != len(set(docprs)):
        _fail("duplicate_drawing_identity")
    for item in objects:
        item["table_end"] = table_ends.get(item["table_start"])
    return {"events": events, "objects": objects}


def build_source_plan(data: bytes, xml_locator: str) -> dict:
    """Preflight the whole DOCX and bind an exact C04 locator to one occurrence.

    ``objects`` is in main-story order and ``target_index`` is zero based. The
    list is a hint for COM enumeration, never evidence by itself. Only a blip
    or oMath locator is accepted; a file/block/paragraph number is insufficient.
    """
    match = _LOCATOR.fullmatch(xml_locator) if isinstance(xml_locator, str) else None
    if match is None or xml_locator.count("/*[") > MAX_XML_DEPTH:
        _fail("invalid_xml_locator")
    package = _source_package(data)
    projection = _projection(package)
    indices = [i for i, item in enumerate(projection["objects"]) if item["xml_locator"] == xml_locator]
    if len(indices) != 1:
        _fail("locator_is_not_supported_object")
    target = projection["objects"][indices[0]]
    if target["kind"] == "math" and target["table_start"] is not None:
        _fail("table_math_has_no_unique_range_identity")
    plan = {"schema": _SCHEMA, "source_sha256": _sha(data), "xml_locator": xml_locator,
            "target_index": indices[0], **projection}
    payload = _json(plan)
    if len(payload) > MAX_PLAN_BYTES:
        _fail("plan_budget")
    plan["plan_sha256"] = _sha(payload)
    return plan


def _check_plan(plan):
    if not isinstance(plan, dict) or plan.get("schema") != _SCHEMA:
        _fail("invalid_plan")
    content = {key: value for key, value in plan.items() if key != "plan_sha256"}
    try:
        payload = _json(content)
        index = plan["target_index"]
        if len(payload) > MAX_PLAN_BYTES or _sha(payload) != plan.get("plan_sha256") or type(index) is not int or not 0 <= index < len(plan["objects"]):
            _fail("invalid_plan")
    except (TypeError, KeyError, ValueError, RecursionError):
        _fail("invalid_plan")


def _verified_document(plan, flat_xml):
    _check_plan(plan)
    package, digest = _flat_package(flat_xml)
    projection = _projection(package)
    if projection["events"] != plan["events"]:
        _fail("document_semantics_changed")
    # Events preserve every occurrence (even byte-identical repeated images).
    if len(projection["objects"]) != len(plan["objects"]):
        _fail("document_object_count_changed")
    return projection, digest


def verify_document(plan: dict, flat_xml: str) -> dict:
    """Verify all main-story text, container boundaries, merges and object bytes."""
    projection, digest = _verified_document(plan, flat_xml)
    return {"verified": True, "document_sha256": digest, "source_sha256": plan["source_sha256"],
            "object_count": len(projection["objects"]), "target_index": plan["target_index"]}


def _prefix(events, cut):
    result = [list(event) for event in events[:cut]]
    stack = []
    for event in result:
        if event[0] == "open":
            stack.append(event[1])
        elif event[0] == "close":
            if not stack or stack.pop() != event[1]:
                _fail("invalid_event_nesting")
    result.extend(["close", tag] for tag in reversed(stack))
    return result


def _check_prefix(actual, expected_events, expected_objects, *, trim_terminal_space=False):
    expected = [list(event) for event in expected_events]
    actual_events = actual["events"]
    if actual_events != expected and trim_terminal_space:
        # Word 16 trims only the final ordinary spaces of a text-only prefix.
        final = next((i for i in range(len(expected) - 1, -1, -1) if expected[i][0] != "close"), None)
        if final is not None and expected[final][0] == "text":
            expected[final][2] = expected[final][2].rstrip(" ")
            if not expected[final][2]:
                expected.pop(final)
    if actual_events != expected:
        _fail("range_prefix_mismatch")
    identities = [(obj["kind"], obj["signature"], obj["docpr_id"]) for obj in actual["objects"]]
    if identities != [(obj["kind"], obj["signature"], obj["docpr_id"]) for obj in expected_objects]:
        _fail("range_prefix_identity_mismatch")


def verify_range(plan: dict, document_xml: str, range_xml: str, before_xml: str,
                 through_xml: str, kind: str, native_type: int) -> dict:
    """Prove the native range is this occurrence, without calculating offsets.

    Word expands table prefixes to the complete outer table. That observed
    mode is accepted only for a unique drawing identity in the verified full
    document, and only when both prefix snapshots equal that exact expansion.
    Table OMML has no such identity and is intentionally unsupported.
    """
    full, document_digest = _verified_document(plan, document_xml)
    index = plan["target_index"]
    target = full["objects"][index]
    if kind != target["kind"] or type(native_type) is not int or native_type != target["native_type"]:
        _fail("native_object_type_mismatch")
    snapshots = []
    digests = []
    for xml in (range_xml, before_xml, through_xml):
        package, digest = _flat_package(xml)
        snapshots.append(_projection(package))
        digests.append(digest)
    selected, before, through = snapshots
    if len(selected["objects"]) != 1:
        _fail("range_is_not_one_object")
    actual = selected["objects"][0]
    if (actual["kind"], actual["signature"]) != (kind, target["signature"]):
        _fail("range_object_mismatch")
    if kind == "inline" and actual["docpr_id"] != target["docpr_id"]:
        _fail("range_occurrence_identity_mismatch")
    expected_range = [["open", W + "body", []], ["open", W + "p", []],
                      ["object", kind, target["signature"]], ["close", W + "p"], ["close", W + "body"]]
    math_wrapped_range = (expected_range[:2] + [["open", M + "oMathPara", []]]
                          + expected_range[2:3] + [["close", M + "oMathPara"]] + expected_range[3:])
    if selected["events"] != expected_range and not (kind == "math" and selected["events"] == math_wrapped_range):
        _fail("range_has_extra_content_or_containers")
    event_index = target["event_index"]
    if target["table_start"] is None:
        _check_prefix(before, _prefix(full["events"], event_index), full["objects"][:index], trim_terminal_space=True)
        _check_prefix(through, _prefix(full["events"], event_index + 1), full["objects"][:index + 1])
        mode = "exact_prefix"
    else:
        if kind != "inline":
            _fail("table_math_has_no_unique_range_identity")
        cut = target["table_end"]
        expected = _prefix(full["events"], cut)
        # Word emits a required empty terminal paragraph after its table copy.
        expected[-1:-1] = [["open", W + "p", []], ["close", W + "p"]]
        objects = [obj for obj in full["objects"] if obj["event_index"] < cut]
        _check_prefix(before, expected, objects)
        _check_prefix(through, expected, objects)
        mode = "expanded_outer_table_unique_drawing"
    return {"verified": True, "source_sha256": plan["source_sha256"],
            "target_index": index, "kind": kind, "native_type": native_type,
            "document_sha256": document_digest, "range_sha256": digests[0],
            "before_sha256": digests[1], "through_sha256": digests[2], "prefix_mode": mode}
