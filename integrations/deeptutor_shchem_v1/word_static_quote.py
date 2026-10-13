"""Closed, source-only image QUOTE scenes; never change the Word preview.

The instruction picture and its cached occurrence are separate evidence.  This
module does not evaluate fields or infer Word's page/paragraph coordinate origin.
Only an image-only paragraph with one preceding simple anchor and one closed
image QUOTE is described.  Its complete source XML is re-parsed on validation.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

from lxml import etree

SCHEMA = 'shchem.word-source-scene.v1'
REVISION = '20261009-static-image-quote-v2'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
PIC = '{http://schemas.openxmlformats.org/drawingml/2006/picture}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A14 = '{http://schemas.microsoft.com/office/drawing/2010/main}'
XML = '{http://www.w3.org/XML/1998/namespace}'
MARKER = '【待查看原文：图片或图形】'
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_MAX_XML = 512 * 1024
_MAX_EMU = 36000 * 10000
_DPI = '{28A0092B-C50C-407E-A947-70E740481C1C}'


def _require(value):
    if not value:
        raise ValueError('原Word图片域场景不在已验证的完整来源范围内。')


def _xml(node):
    return etree.tostring(node, method='c14n').decode('utf-8')


def _sha(node):
    return sha256(_xml(node).encode('utf-8')).hexdigest()


def _integer(value, low=0, high=_MAX_EMU):
    _require(isinstance(value, str) and re.fullmatch(r'-?\d{1,12}', value))
    number = int(value)
    _require(low <= number <= high)
    return number


def _attrs(node, required=(), optional=()):
    _require(set(required) <= set(node.attrib) <= set(required) | set(optional))


def _children(node, names):
    _require([c.tag for c in node] == list(names))
    _require(not (node.text or '').strip() and not any((c.tail or '').strip() for c in node))


def _empty(node, required=(), optional=()):
    _attrs(node, required, optional)
    _children(node, ())


def _size(node, names, *, positive=False, signed=False):
    _empty(node, names)
    return {k: _integer(node.get(k), -_MAX_EMU if signed else (1 if positive else 0)) for k in names}


def _properties(run):
    props = run.find(W + 'rPr')
    if props is None:
        return None
    _attrs(props)
    allowed = {'rFonts', 'sz', 'szCs', 'position', 'vertAlign', 'vanish', 'b', 'bCs', 'i', 'iCs', 'color', 'lang', 'noProof'}
    seen = set()
    for child in props:
        _require(child.tag.startswith(W))
        name = child.tag[len(W):]
        _require(name in allowed and name not in seen)
        seen.add(name)
        _require(not len(child) and not (child.text or '').strip())
        _require(all(k.startswith(W) for k in child.attrib))
        attribute_names = {'rFonts': ('ascii', 'hAnsi', 'eastAsia', 'cs', 'hint', 'asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme'),
                           'lang': ('val', 'eastAsia', 'bidi'), 'color': ('val', 'themeColor', 'themeTint', 'themeShade')}.get(name, ('val',))
        _attrs(child, (), tuple(W + key for key in attribute_names))
        if name == 'vanish':
            _require(child.get(W + 'val', 'true') in {'0', 'false', 'off'})
        elif name == 'vertAlign':
            _require(child.get(W + 'val') == 'baseline')
        elif name in {'sz', 'szCs'}:
            _integer(child.get(W + 'val'), 1, 400)
        elif name == 'position':
            _integer(child.get(W + 'val'), -31680, 31680)
    position = props.find(W + 'position')
    return None if position is None else _integer(position.get(W + 'val'), -31680, 31680)


def _paragraph_properties(node):
    _attrs(node)
    allowed = {'pStyle', 'autoSpaceDE', 'autoSpaceDN', 'adjustRightInd', 'snapToGrid', 'spacing', 'ind', 'jc', 'rPr'}
    seen = set()
    for child in node:
        _require(child.tag.startswith(W))
        name = child.tag[len(W):]
        _require(name in allowed and name not in seen)
        seen.add(name)
        _require(all(k.startswith(W) for k in child.attrib))
        if name == 'rPr':
            # This image-only paragraph has no paragraph-mark text to paint.
            # Preserve authored highlight/textFill metadata in the full XML;
            # effective visibility of actual image runs is checked by reader.
            _require(not any(n.tag in {W + 'drawing', W + 'object', W + 'pict', W + 'fldChar', W + 'instrText', W + 't'} for n in child.iter()))
        else:
            _require(not len(child) and not (child.text or '').strip())
        if name == 'jc':
            _require(child.get(W + 'val') in {'left', 'center', 'right'})


def _read_xml(raw):
    _require(isinstance(raw, str) and len(raw.encode('utf-8')) <= _MAX_XML)
    _require('<!DOCTYPE' not in raw and '<!ENTITY' not in raw)
    return etree.fromstring(raw.encode('utf-8'), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))


def _frame(paragraph, section, style_xmls, defaults_xml):
    """Derive the supported one-column viewport from preserved source XML."""
    _require(section.tag == W + 'sectPr')
    _require(isinstance(style_xmls, list) and len(style_xmls) <= 16)
    styles = [_read_xml(raw) for raw in style_xmls]
    _require(all(node.tag == W + 'style' for node in styles))
    _require(all(len(node.findall(W + 'pPr')) <= 1 and len(node.findall(W + 'rPr')) <= 1 for node in styles))
    defaults = _read_xml(defaults_xml) if defaults_xml is not None else None
    _require(defaults is None or defaults.tag == W + 'docDefaults')
    ppr = paragraph.find(W + 'pPr')
    _require(ppr is not None)
    style_ref = ppr.find(W + 'pStyle')
    wanted = style_ref.get(W + 'val') if style_ref is not None else None
    for style in styles:
        _require(style.get(W + 'styleId') == wanted and style.get(W + 'type') == 'paragraph')
        based = style.find(W + 'basedOn')
        wanted = based.get(W + 'val') if based is not None else None
    _require(wanted is None)
    properties = [ppr, *[node.find(W + 'pPr') for node in styles]]
    if defaults is not None:
        properties.append(defaults.find(W + 'pPrDefault/' + W + 'pPr'))
    properties = [node for node in properties if node is not None]
    _require(all(len(node) == len({child.tag for child in node}) for node in properties))
    _require(not any(node.find(W + name) is not None for node in properties
                     for name in ('framePr', 'bidi', 'mirrorIndents', 'textDirection', 'contextualSpacing')))

    def effective(name, attribute='val', default=None):
        for node in properties:
            found = node.find(W + name)
            if found is not None and W + attribute in found.attrib:
                return found.get(W + attribute)
        return default

    _require(effective('jc') == 'center' and effective('snapToGrid') in {'0', 'false', 'off'})
    _require(effective('spacing', 'lineRule') in {None, 'auto'})
    _require(effective('ind', 'hanging', '0') == '0' and effective('ind', 'hangingChars', '0') == '0')
    _require(effective('ind', 'firstLine', '0') == '0' and effective('ind', 'firstLineChars', '0') == '0')
    for node in properties:
        indent = node.find(W + 'ind')
        if indent is not None:
            _attrs(indent, (), tuple(W + n for n in ('left', 'right', 'firstLine', 'hanging', 'leftChars', 'rightChars', 'firstLineChars', 'hangingChars')))
            for key, value in indent.attrib.items():
                _integer(value, -200000, 200000)
            # Word gives character units precedence even when it also stores
            # twips. Require both here and check their consistency below.
            for name in ('left', 'right'):
                _require(W + name + 'Chars' not in indent.attrib or W + name in indent.attrib)
    left = _integer(effective('ind', 'left', '0'), -200000, 200000)
    right = _integer(effective('ind', 'right', '0'), -200000, 200000)
    character_indents = {name: effective('ind', name + 'Chars') for name in ('left', 'right')}
    unit = None
    if any(value is not None for value in character_indents.values()):
        # Native Word measurements establish that the document's default
        # Normal size supplies the character unit, not the paragraph mark or
        # picture runs. Only the two measured sizes/font basis are supported.
        normal = [node for node in styles if node.get(W + 'default') in {'1', 'true'}]
        _require(len(normal) == 1 and normal[0].get(W + 'styleId') == 'Normal'
                 and normal[0].find(W + 'basedOn') is None)
        normal_properties = normal[0].find(W + 'rPr')
        _require(normal_properties is not None)
        _require(len(normal_properties.findall(W + 'sz')) == 1
                 and len(normal_properties.findall(W + 'rFonts')) == 1)
        size = normal_properties.find(W + 'sz')
        fonts = normal_properties.find(W + 'rFonts')
        _require(size is not None and fonts is not None)
        _empty(size, (W + 'val',))
        _require(size.get(W + 'val') in {'21', '24'})
        _empty(fonts, tuple(W + name for name in ('ascii', 'hAnsi', 'eastAsia', 'cs')))
        _require(dict(fonts.attrib) == {W + 'ascii': 'Times New Roman', W + 'hAnsi': 'Times New Roman',
                                       W + 'eastAsia': '宋体', W + 'cs': 'Times New Roman'})
        _require(not any(normal_properties.find(W + name) is not None
                         for name in ('spacing', 'w', 'fitText', 'position', 'rtl', 'cs')))
        unit = int(size.get(W + 'val')) * 10
        for name, value in character_indents.items():
            if value is not None:
                chars = _integer(value, -200000, 200000)
                twips = left if name == 'left' else right
                _require(chars * unit == twips * 100)
    seen = set()
    allowed = {'headerReference', 'footerReference', 'type', 'pgSz', 'pgMar', 'pgBorders', 'pgNumType', 'cols', 'rtlGutter', 'docGrid'}
    for child in section:
        _require(child.tag.startswith(W) and child.tag[len(W):] in allowed)
        name = child.tag[len(W):]
        if name not in {'headerReference', 'footerReference'}:
            _require(name not in seen)
        seen.add(name)
        if name == 'rtlGutter':
            _require(child.get(W + 'val') in {'0', 'false', 'off'})
        if name == 'docGrid':
            _require(child.get(W + 'type', 'default') in {'default', 'lines'}
                     and child.get(W + 'charSpace', '0') == '0')
    pg = section.find(W + 'pgSz'); margins = section.find(W + 'pgMar'); cols = section.find(W + 'cols')
    _require(pg is not None and margins is not None and cols is not None)
    _empty(pg, (W + 'w', W + 'h'), (W + 'orient', W + 'code'))
    page_width = _integer(pg.get(W + 'w'), 1, 200000)
    _integer(pg.get(W + 'h'), 1, 200000)
    _empty(margins, (W + 'top', W + 'right', W + 'bottom', W + 'left', W + 'header', W + 'footer', W + 'gutter'))
    margin_values = {k[len(W):]: _integer(v, 0, 200000) for k, v in margins.attrib.items()}
    _require(margin_values['gutter'] == 0)
    _empty(cols, (), (W + 'num', W + 'space', W + 'equalWidth', W + 'sep'))
    _require(cols.get(W + 'num', '1') == '1' and cols.get(W + 'equalWidth', '1') in {'1', 'true'}
             and cols.get(W + 'sep', '0') in {'0', 'false'})
    column = page_width - margin_values['left'] - margin_values['right']
    _require(0 < column <= 200000 and 0 < column - left - right <= 200000)
    return {'section_xml': _xml(section), 'section_xml_sha256': _sha(section),
            'column_width_twips': column, 'paragraph_left_twips': left, 'paragraph_right_twips': right,
            'alignment': 'center', 'paragraph_style_chain_xml': style_xmls, 'document_defaults_xml': defaults_xml,
            **({'character_indent_unit_twips': unit} if unit is not None else {})}


def _source_frame(paragraph, document):
    body = document.element.body
    _require(paragraph.getparent() is body)
    _require(not any(child.find(W + 'pPr/' + W + 'sectPr') is not None for child in body))
    following = paragraph
    section = None
    while following is not None:
        candidate = following.find(W + 'pPr/' + W + 'sectPr')
        if candidate is not None:
            section = candidate
            break
        following = following.getnext()
    if section is None:
        section = body.find(W + 'sectPr')
    _require(section is not None)
    ppr = paragraph.find(W + 'pPr')
    _require(ppr is not None)
    styles = document.styles.element
    table = {node.get(W + 'styleId'): node for node in styles if node.tag == W + 'style'}
    named = ppr.find(W + 'pStyle')
    wanted = named.get(W + 'val') if named is not None else None
    if wanted is None:
        default_style = [n for n in table.values() if n.get(W + 'type') == 'paragraph' and n.get(W + 'default') in {'1', 'true'}]
        # A missing explicit pStyle would require a different inherited-style
        # identity. Keep this first viewport grammar explicit and closed.
        _require(not default_style)
    chain = []; seen = set()
    while wanted is not None:
        _require(wanted in table and wanted not in seen and len(chain) < 16)
        seen.add(wanted); node = table[wanted]; chain.append(_xml(node))
        based = node.find(W + 'basedOn')
        wanted = based.get(W + 'val') if based is not None else None
    defaults = styles.find(W + 'docDefaults')
    frame = _frame(paragraph, section, chain, _xml(defaults) if defaults is not None else None)
    if 'character_indent_unit_twips' in frame:
        default_style = [n for n in table.values() if n.get(W + 'type') == 'paragraph'
                         and n.get(W + 'default') in {'1', 'true'}]
        _require(len(default_style) == 1 and default_style[0].get(W + 'styleId') == 'Normal'
                 and 'Normal' in seen)
    return frame


def _drawing(node, phase, position, run_position, relations):
    _attrs(node)
    _require(len(node) == 1)
    container = node[0]
    anchored = phase == 'outside_field'
    _require(container.tag == WP + ('anchor' if anchored else 'inline'))
    anchor = None
    if anchored:
        required = {'distT', 'distB', 'distL', 'distR', 'simplePos', 'relativeHeight', 'behindDoc', 'locked', 'layoutInCell', 'allowOverlap'}
        _attrs(container, required)
        for key in ('distT', 'distB', 'distL', 'distR', 'relativeHeight'):
            _integer(container.get(key), 0, 4294967295 if key == 'relativeHeight' else _MAX_EMU)
        _require(all(container.get(k) == v for k, v in {'simplePos': '0', 'behindDoc': '0', 'locked': '0', 'layoutInCell': '1', 'allowOverlap': '1'}.items()))
        prefix = [WP + n for n in ('simplePos', 'positionH', 'positionV')]
        simple = _size(container[0], ('x', 'y'))
        _require(simple == {'x': 0, 'y': 0})
        positions = []
        for child, relative in zip(list(container)[1:3], ('column', 'paragraph')):
            _attrs(child, ('relativeFrom',))
            _require(child.get('relativeFrom') == relative)
            _children(child, (WP + 'posOffset',))
            _attrs(child[0])
            _require(not len(child[0]))
            positions.append({'relative_from': relative, 'offset_emu': _integer(child[0].text, -_MAX_EMU)})
        anchor = {'attributes': dict(container.attrib), 'simple_position': simple,
                  'position_h': positions[0], 'position_v': positions[1], 'wrap': 'none'}
    else:
        _attrs(container, (), ('distT', 'distB', 'distL', 'distR'))
        _require(all(v == '0' for v in container.attrib.values()))
        prefix = []
    expected = prefix + [WP + 'extent', WP + 'effectExtent']
    if anchored:
        expected.append(WP + 'wrapNone')
    expected += [WP + 'docPr', WP + 'cNvGraphicFramePr', A + 'graphic']
    _children(container, expected)
    extent = _size(container.find(WP + 'extent'), ('cx', 'cy'), positive=True)
    effect = _size(container.find(WP + 'effectExtent'), ('l', 't', 'r', 'b'))
    # Shadows/glows/effect geometry are not supported; the known small bottom
    # extent is retained as source geometry rather than silently discarded.
    _require(effect['l'] == effect['t'] == effect['r'] == 0 and (anchored or effect['b'] == 0))
    if anchored:
        _empty(container.find(WP + 'wrapNone'))
    docpr = container.find(WP + 'docPr')
    _empty(docpr, ('id', 'name'), ('descr', 'title'))
    _integer(docpr.get('id'), 0, 4294967295)
    frame = container.find(WP + 'cNvGraphicFramePr')
    _attrs(frame)
    _children(frame, (A + 'graphicFrameLocks',))
    _empty(frame[0], ('noChangeAspect',))
    _require(frame[0].get('noChangeAspect') == '1')
    graphic = container.find(A + 'graphic')
    _attrs(graphic)
    _children(graphic, (A + 'graphicData',))
    _attrs(graphic[0], ('uri',))
    _require(graphic[0].get('uri') == PIC[1:-1])
    _children(graphic[0], (PIC + 'pic',))
    picture = graphic[0][0]
    _attrs(picture)
    _children(picture, (PIC + 'nvPicPr', PIC + 'blipFill', PIC + 'spPr'))
    nv, fill, shape = picture
    _attrs(nv)
    _children(nv, (PIC + 'cNvPr', PIC + 'cNvPicPr'))
    _empty(nv[0], ('id', 'name'), ('descr', 'title'))
    _integer(nv[0].get('id'), 0, 4294967295)
    _attrs(nv[1])
    _require([n.tag for n in nv[1]] in ([], [A + 'picLocks']))
    if len(nv[1]):
        _empty(nv[1][0], (), ('noChangeAspect', 'noChangeArrowheads'))
        _require(all(v == '1' for v in nv[1][0].attrib.values()))
    _attrs(fill)
    _children(fill, (A + 'blip', A + 'stretch'))
    blip = fill[0]
    _attrs(blip, (R + 'embed',), ('cstate',))
    _require(blip.get('cstate', 'print') == 'print')
    rid = blip.get(R + 'embed')
    _require(isinstance(rid, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', rid))
    children = list(blip)
    transparency = None
    if children and children[0].tag == A + 'clrChange':
        change = children.pop(0)
        _attrs(change)
        _children(change, (A + 'clrFrom', A + 'clrTo'))
        for color in change:
            _attrs(color)
            _children(color, (A + 'srgbClr',))
            _attrs(color[0], ('val',))
            _require(color[0].get('val') == 'FFFFFF')
        _children(change[0][0], ())
        _children(change[1][0], (A + 'alpha',))
        _empty(change[1][0][0], ('val',))
        _require(change[1][0][0].get('val') == '0')
        _require(not anchored)
        transparency = {'from': 'FFFFFF', 'to': 'FFFFFF', 'alpha': 0}
    _require([c.tag for c in children] in ([], [A + 'extLst']))
    if children:
        _attrs(children[0])
        _children(children[0], (A + 'ext',))
        extension = children[0][0]
        _attrs(extension, ('uri',))
        _require(extension.get('uri') == _DPI)
        _children(extension, (A14 + 'useLocalDpi',))
        _empty(extension[0], ('val',))
        _require(extension[0].get('val') in {'0', '1', 'false', 'true'})
    _attrs(fill[1])
    _children(fill[1], (A + 'fillRect',))
    _empty(fill[1][0])
    _attrs(shape)
    _require([c.tag for c in shape] in ([A + 'xfrm', A + 'prstGeom'], [A + 'xfrm', A + 'prstGeom', A + 'noFill', A + 'ln']))
    transform = shape[0]
    _attrs(transform)
    _children(transform, (A + 'off', A + 'ext'))
    offset = _size(transform[0], ('x', 'y'))
    _require(offset == {'x': 0, 'y': 0})
    xextent = _size(transform[1], ('cx', 'cy'), positive=True)
    if anchored:
        _require(xextent == extent)
    _attrs(shape[1], ('prst',))
    _require(shape[1].get('prst') == 'rect')
    _children(shape[1], (A + 'avLst',))
    _empty(shape[1][0])
    if len(shape) == 4:
        _empty(shape[2])
        _attrs(shape[3])
        _children(shape[3], (A + 'noFill',))
        _empty(shape[3][0])
    identity = relations[rid]
    _require(set(identity) == {'original_sha256', 'original_mime_type'})
    _require(isinstance(identity['original_sha256'], str) and _SHA.fullmatch(identity['original_sha256']))
    _require(identity['original_mime_type'] in {'image/png', 'image/jpeg'})
    return {'position': position, 'phase': phase, 'relationship_id': rid, **identity,
            'container_xml_sha256': _sha(node), 'width_mm': extent['cx'] / 36000,
            'height_mm': extent['cy'] / 36000, 'wp_extent': extent, 'effect_extent': effect,
            'xfrm': {'off': offset, 'ext': xextent}, 'run_position_half_points': run_position,
            'transparency': transparency, 'anchor': anchor}


def _parse(paragraph, text, relations, frame):
    _require(paragraph.tag == W + 'p' and isinstance(text, str) and text == MARKER * 3)
    _require(len(list(paragraph.iter())) <= 512)
    _require(all(isinstance(n.tag, str) for n in paragraph.iter()))
    _require(not (paragraph.text or '').strip() and not any((n.tail or '').strip() for n in paragraph.iter()))
    children = list(paragraph)
    if children and children[0].tag == W + 'pPr':
        _paragraph_properties(children.pop(0))
    _require(6 <= len(children) <= 64 and all(n.tag == W + 'r' for n in children))
    state = 'outside_field'; occurrences = []; instruction = []; boundaries = []
    for ordinal, run in enumerate(children, 1):
        _attrs(run, (), (W + 'rsidR', W + 'rsidRPr', W + 'rsidDel'))
        position = _properties(run)
        content = [c for c in run if c.tag != W + 'rPr']
        _require(len(run.findall(W + 'rPr')) <= 1 and len(content) >= 1)
        for child in content:
            if child.tag == W + 'fldChar':
                _require(len(content) == 1)
                _empty(child, (W + 'fldCharType',))
                kind = child.get(W + 'fldCharType')
                wanted = {'outside_field': 'begin', 'instruction': 'separate', 'cache': 'end'}.get(state)
                _require(kind == wanted)
                boundaries.append((kind, ordinal))
                if kind == 'begin':
                    _require(len(occurrences) == 1)
                    state = 'instruction'
                elif kind == 'separate':
                    _require(len(occurrences) == 2)
                    state = 'cache'
                else:
                    _require(len(occurrences) == 3)
                    state = 'done'
            elif child.tag == W + 'instrText':
                _require(state == 'instruction' and position in (None, 0))
                _attrs(child, (), (XML + 'space',))
                _require(not len(child) and child.get(XML + 'space', 'preserve') in {'preserve', 'default'})
                value = child.text or ''
                _require(len(value) <= 8192 and not any(ord(c) < 32 for c in value))
                instruction.append({'kind': 'text', 'text': value})
            elif child.tag == W + 'drawing':
                _require(state in {'outside_field', 'instruction', 'cache'} and len(content) == 1)
                _require(len(occurrences) == {'outside_field': 0, 'instruction': 1, 'cache': 2}[state])
                occurrences.append(_drawing(child, state, len(occurrences) + 1, position, relations))
                if state == 'instruction':
                    instruction.append({'kind': 'image', 'position': 2})
            else:
                _require(False)
    _require(state == 'done' and len(boundaries) == 3 and len(occurrences) == 3)
    command = ''.join(part['text'] if part['kind'] == 'text' else '\ufffc' for part in instruction)
    _require(re.fullmatch(r' *QUOTE +\ufffc *', command, re.ASCII | re.IGNORECASE))
    first, cached = occurrences[1:]
    _require(first['relationship_id'] == cached['relationship_id']
             and first['original_sha256'] == cached['original_sha256']
             and first['original_mime_type'] == cached['original_mime_type']
             and first['transparency'] == cached['transparency']
             and first['run_position_half_points'] == cached['run_position_half_points'])
    down = cached['run_position_half_points']
    _require(type(down) is int and down < 0 and -down * 25.4 / 144 < cached['height_mm'])
    _require(occurrences[0]['run_position_half_points'] in (None, 0))
    raw = _xml(paragraph)
    _require(len(raw.encode('utf-8')) <= _MAX_XML)
    start, end = boundaries[0][1], boundaries[-1][1]
    return {'schema': SCHEMA, 'parser_revision': REVISION, 'paragraph_xml': raw,
            'paragraph_xml_sha256': sha256(raw.encode('utf-8')).hexdigest(), 'text': text,
            'span': {'start': 0, 'end': len(text)},
            'quote': {'run_start': start, 'run_end': end, 'instruction_parts': instruction,
                      'instruction_image_position': 2, 'cache_image_position': 3,
                      'field_xml_sha256': sha256(''.join(_sha(run) for run in children[start - 1:end]).encode()).hexdigest()},
            'occurrences': occurrences, 'frame': frame,
            'projection': {'kind': 'image_only_quote_with_anchor', 'geometry': 'cache_authored',
                           'visible_positions': [1, 3], 'retained_instruction_position': 2,
                           'coordinate_policy': 'centered_column_cache_top_with_paragraph_anchor.v1'}}


def parse_scene(paragraph, document, reader):
    """Return a source-derived scene, or None for every unsupported structure."""
    try:
        native = reader.read(paragraph)
        _require(set(native.features) == {'field_code_dependency', 'word_drawing_or_shape'})
        style = paragraph.find(W + 'pPr/' + W + 'pStyle')
        style_id = style.get(W + 'val') if style is not None else reader.default_paragraph
        for run in paragraph.findall(W + 'r'):
            direct = _properties(run)
            effective = reader._run_property(run, style_id, 'position')
            _require((None if effective is None else _integer(effective, -31680, 31680)) == direct)
        relations = {}
        for node in paragraph.iter(A + 'blip'):
            rid = node.get(R + 'embed')
            relation = document.part.rels.get(rid)
            _require(relation is not None and not relation.is_external and not node.get(R + 'link'))
            part = relation.target_part
            relations[rid] = {'original_sha256': sha256(part.blob).hexdigest(),
                              'original_mime_type': part.content_type}
        return _parse(paragraph, native.text, relations, _source_frame(paragraph, document))
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError):
        return None


def validate_scene(scene, text, refs=None, *, images=None):
    """Re-parse the sealed XML and optionally bind every source occurrence.

    ``refs`` means complete section-snapshot image references; ``images`` means
    paragraph presentation references. Neither may drop the instruction image.
    """
    try:
        _require(isinstance(scene, dict) and isinstance(scene.get('paragraph_xml'), str))
        raw = scene['paragraph_xml'].encode('utf-8')
        _require(len(raw) <= _MAX_XML and b'<!DOCTYPE' not in raw and b'<!ENTITY' not in raw)
        paragraph = _read_xml(scene['paragraph_xml'])
        rows = scene.get('occurrences')
        _require(isinstance(rows, list) and len(rows) == 3 and all(isinstance(r, dict) for r in rows))
        relations = {}
        for row in rows:
            identity = {k: row[k] for k in ('original_sha256', 'original_mime_type')}
            rid = row['relationship_id']
            _require(isinstance(rid, str) and (rid not in relations or relations[rid] == identity))
            relations[rid] = identity
        frame = scene['frame']
        _require(isinstance(frame, dict))
        rebuilt_frame = _frame(paragraph, _read_xml(frame['section_xml']),
                               frame['paragraph_style_chain_xml'], frame['document_defaults_xml'])
        expected = _parse(paragraph, text, relations, rebuilt_frame)
        _require(json.dumps(scene, sort_keys=True, ensure_ascii=False, allow_nan=False)
                 == json.dumps(expected, sort_keys=True, ensure_ascii=False, allow_nan=False))
        if refs is not None:
            _require(isinstance(refs, list) and len(refs) == 3)
            for row, ref in zip(rows, refs):
                _require(isinstance(ref, dict) and all(ref.get(k) == row[k] for k in
                    ('position', 'container_xml_sha256', 'original_sha256', 'original_mime_type', 'width_mm', 'height_mm')))
                _require(ref.get('source_branch') == 'direct' and ref.get('status') == 'included')
        if images is not None:
            _require(isinstance(images, list) and len(images) == 3)
            for row, image in zip(rows, images):
                _require(isinstance(image, dict) and image.get('placeholder') == MARKER
                         and image.get('width_mm') == row['width_mm'] and image.get('height_mm') == row['height_mm']
                         and 'transform' not in image)
        return scene
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError, etree.XMLSyntaxError) as exc:
        raise ValueError('原Word图片域场景与完整来源证据不一致。') from exc
