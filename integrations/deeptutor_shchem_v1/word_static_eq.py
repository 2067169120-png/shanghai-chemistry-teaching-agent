"""Bounded, source-preserving projection of closed static Word EQ overlays.

This is deliberately not a field evaluator. It follows no links, updates no
fields and uses no cached result. Unknown field grammar remains a dependency.
"""
from copy import deepcopy
from hashlib import sha256
import math
import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REVISION = '20261008-static-eq-overlay-v1'
MARKER = '【待查看原文：图片或图形】'
_IMAGE = '\ufffc'
_SCRIPT = {'baseline', 'subscript', 'superscript'}


def _xml_sha(node):
    from lxml import etree
    try:
        raw=etree.tostring(node, method='c14n')
    except TypeError:
        from xml.etree import ElementTree
        raw=ElementTree.canonicalize(ElementTree.tostring(node,encoding='unicode')).encode()
    return sha256(raw).hexdigest()


def _drawing(node):
    pictures = node.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/picture}pic')
    blips = node.findall('.//' + A + 'blip')
    extents = node.findall('.//' + WP + 'extent')
    if (len(pictures) != 1 or len(blips) != 1 or len(extents) != 1
            or len(node.findall('.//' + WP + 'inline')) != 1
            or node.findall('.//' + WP + 'anchor') or blips[0].get(R+'link')
            or not blips[0].get(R+'embed') or len(blips[0])):
        raise ValueError('unsupported EQ drawing')
    if any(node.tag in {A+name for name in ('effectLst','effectDag','scene3d','sp3d','duotone','clrChange','lum','alphaModFix')}
           for node in node.iter()):
        raise ValueError('unsupported EQ image effect')
    sizes = [int(extents[0].get(k, '0')) / 36000 for k in ('cx','cy')]
    if any(not 0 < value <= 10000 for value in sizes):
        raise ValueError('invalid EQ drawing size')
    crops = node.findall('.//' + A + 'srcRect')
    if len(crops) > 1:
        raise ValueError('ambiguous EQ crop')
    crop = {key: int(crops[0].get(key, '0')) if crops else 0 for key in ('l','t','r','b')}
    if (any(not 0 <= value < 100000 for value in crop.values())
            or crop['l']+crop['r'] >= 100000 or crop['t']+crop['b'] >= 100000):
        raise ValueError('invalid EQ crop')
    transforms = node.findall('.//' + A + 'xfrm')
    if len(transforms) != 1 or any(transforms[0].get(key) not in (None,'0','false')
                                  for key in ('rot','flipH','flipV')):
        raise ValueError('unsupported EQ transform')
    return {'kind':'image', 'container_xml_sha256':_xml_sha(node),
            'relationship_id':blips[0].get(R+'embed'), 'width_mm':sizes[0],
            'height_mm':sizes[1], 'crop':crop}


def _runs(text, scripts, start, end):
    values = []
    for char, script in zip(text[start:end], scripts[start:end]):
        if values and values[-1]['script'] == script:
            values[-1]['text'] += char
        else:
            values.append({'text':char,'script':script})
    return values


def _transcribe(runs):
    return ''.join(r['text'] if r['script']=='baseline' else
                   ('_{' if r['script']=='subscript' else '^{')+r['text']+'}' for r in runs)


def _parse(code, scripts, drawing):
    # Grammar intentionally has no recursive expression, quoting, separators
    # inside an operand, or executable/dynamic field instructions.
    match = re.fullmatch(r'\s*[Ee][Qq]\s+\\o\(([^(),\\]+),(.+)\)\s*', code)
    if (not match or len(code) > 8192 or any(ord(c)<32 or c in '\u2028\u2029\ue100\ue101\ue102' for c in code)):
        raise ValueError('unsupported EQ expression')
    base_start, base_end = match.span(1)
    base_raw = match[1]
    if base_raw == _IMAGE and drawing is not None:
        base = deepcopy(drawing)
    elif drawing is None and _IMAGE not in base_raw and base_raw.strip() and len(base_raw) <= 256:
        base = {'kind':'text','runs':_runs(code,scripts,base_start,base_end)}
    else:
        raise ValueError('unsupported EQ base')
    overlays = []
    cursor = match.start(2)
    while cursor < match.end(2):
        condition = re.match(r'\\s\\(up|do)(\d{1,2}(?:\.\d{1,2})?)\(([^(),\\\ufffc]+)\)', code[cursor:match.end(2)])
        if not condition:
            raise ValueError('unsupported EQ condition')
        offset = float(condition[2])
        if not 0 < offset <= 72:
            raise ValueError('invalid EQ offset')
        lo, hi = (cursor+position for position in condition.span(3))
        if not code[lo:hi].strip() or hi-lo > 256:
            raise ValueError('invalid EQ label')
        overlays.append({'position':'up' if condition[1]=='up' else 'down',
                         'offset_pt':offset,'runs':_runs(code,scripts,lo,hi)})
        cursor += condition.end()
        if cursor < match.end(2):
            if code[cursor] != ',':
                raise ValueError('unsupported EQ operand separator')
            cursor += 1
    if not 1 <= len(overlays) <= 2 or len({o['position'] for o in overlays}) != len(overlays):
        raise ValueError('ambiguous EQ overlays')
    # Formatting of command syntax must never be mistaken for label formatting.
    allowed = set(range(base_start,base_end)) if base['kind']=='text' else set()
    for condition in re.finditer(r'\\s\\(?:up|do)\d{1,2}(?:\.\d{1,2})?\(([^(),\\]+)\)',code):
        allowed.update(range(*condition.span(1)))
    if any(script!='baseline' and index not in allowed for index,script in enumerate(scripts)):
        raise ValueError('formatted EQ command syntax')
    text = MARKER if base['kind']=='image' else _transcribe(base['runs'])
    text += '〔'+ '；'.join(('上移' if o['position']=='up' else '下移')+
        format(o['offset_pt'],'g')+'pt：'+_transcribe(o['runs']) for o in overlays)+'〕'
    return base, overlays, text


def paragraph_fields(paragraph, property_reader=None):
    """Return only fully closed direct-run static fields, without changing XML.

    Whole paragraphs with malformed/nested/cross-wrapper field boundaries are
    left to the existing dependency guard. Other unrelated fields can coexist.
    """
    if paragraph.tag != W+'p' or len(paragraph) > 10000:
        return []
    children = list(paragraph)
    parents={child:parent for parent in paragraph.iter() for child in parent}
    if any(n.tag in {W+'fldChar',W+'instrText'} and
           (parents[n].tag != W+'r' or parents.get(parents[n]) is not paragraph)
           for n in paragraph.iter()):
        return []
    fields, active = [], None
    for index, run in enumerate(children):
        boundaries = run.findall(W+'fldChar') if run.tag == W+'r' else []
        if len(boundaries) > 1:
            return []
        if boundaries:
            kind = boundaries[0].get(W+'fldCharType')
            if kind == 'begin':
                if active is not None:
                    return []
                active = {'start':index,'separate':None,'end':None}
            elif kind == 'separate':
                if active is None or active['separate'] is not None:
                    return []
                active['separate'] = index
            elif kind == 'end':
                if active is None or active['separate'] is None:
                    return []
                active['end'] = index
                fields.append(active); active = None
            else:
                return []
    if active is not None:
        return []
    result = []
    for bounds in fields:
        try:
            start, separate, end = (bounds[k] for k in ('start','separate','end'))
            span = children[start:end+1]
            if any(n.tag != W+'r' for n in span):
                continue
            if any(any(n.tag not in {W+'rPr',W+'fldChar'} for n in children[position])
                   for position in (start,separate,end)):
                continue
            if any(any(n.tag != W+'rPr' for n in run) for run in children[separate+1:end]):
                continue  # Cached pixels/text are not substituted for the code.
            code, scripts, instruction_runs, drawing, font_pt = '', [], [], None, None
            for run in children[start+1:separate]:
                def prop(name):
                    value=property_reader(run,name) if property_reader else None
                    if value is None:
                        node=run.find(W+'rPr/'+W+name)
                        value=node.get(W+'val','true') if node is not None else None
                    return value
                vertical = prop('vertAlign') or 'baseline'
                if vertical not in _SCRIPT:
                    raise ValueError('unsupported EQ script')
                hidden = prop('vanish')
                if hidden not in (None,'0','false','off'):
                    raise ValueError('hidden EQ instruction')
                if prop('position') not in (None,'0'):
                    raise ValueError('unsupported EQ run position')
                size = prop('sz')
                if size is not None:
                    size = float(size)/2
                    if not 1 <= size <= 100:
                        raise ValueError('invalid EQ font size')
                    if font_pt is not None and font_pt != size:
                        raise ValueError('mixed EQ font size')
                    font_pt = size
                for node in run:
                    if node.tag == W+'rPr':
                        continue
                    if node.tag == W+'instrText':
                        value = node.text or ''
                        code += value; scripts.extend([vertical]*len(value))
                        instruction_runs.append({'text':value,'script':vertical})
                    elif node.tag == W+'drawing' and drawing is None and vertical=='baseline':
                        drawing = _drawing(node); code += _IMAGE; scripts.append('baseline')
                    else:
                        raise ValueError('unsupported EQ instruction operand')
            base, overlays, text = _parse(code,scripts,drawing)
            result.append({'run_start':start,'run_end':end,'transcription':text,
                'base':base,'overlays':overlays,'instruction':code,
                'instruction_runs':instruction_runs,'source_font_pt':font_pt,
                'field_sha256':sha256(''.join(_xml_sha(n) for n in span).encode()).hexdigest()})
        except (ValueError,TypeError,OverflowError):
            continue
    return result


def validate_spans(spans, value, *, images=(), original=False):
    """Validate stored source positions and the bounded presentation grammar."""
    if not isinstance(spans,list) or len(spans)>256:
        raise ValueError('静态EQ原文显示依据无效。')
    end = 0
    for span in spans:
        if (not isinstance(span,dict) or set(span)!={'start','end','field'}
                or type(span['start']) is not int or type(span['end']) is not int
                or not end <= span['start'] < span['end'] <= 120000):
            raise ValueError('静态EQ原文范围无效。')
        field = span['field']
        keys = {'run_start','run_end','transcription','base','overlays','instruction',
                'instruction_runs','source_font_pt','field_sha256'}
        if (not isinstance(field,dict) or set(field)!=keys
                or not re.fullmatch('[a-f0-9]{64}',str(field['field_sha256']))
                or type(field['run_start']) is not int or type(field['run_end']) is not int
                or not 0<=field['run_start']<field['run_end']<=10000
                or not isinstance(field['instruction'],str) or len(field['instruction'])>8192
                or not isinstance(field['transcription'],str)
                or not isinstance(field['instruction_runs'],list)):
            raise ValueError('静态EQ来源证据无效。')
        base = field['base']; raw = ''; scripts = []
        if not isinstance(base,dict):raise ValueError('静态EQ基底无效。')
        for run in field['instruction_runs']:
            if (not isinstance(run,dict) or set(run)!={'text','script'}
                    or not isinstance(run['text'],str) or run['script'] not in _SCRIPT):
                raise ValueError('静态EQ指令字符格式无效。')
            raw += run['text']; scripts.extend([run['script']]*len(run['text']))
        drawing = None
        if base.get('kind')=='image':
            refs = [r for r in images if r['image_block_id']==base.get('image_block_id')]
            required = {'kind','container_xml_sha256','relationship_id','width_mm','height_mm','crop','image_block_id'}
            if set(base)!=required or len(refs)!=1 or base['width_mm']!=refs[0]['width_mm'] or base['height_mm']!=refs[0]['height_mm']:
                raise ValueError('静态EQ原图绑定无效。')
            if not re.fullmatch('[a-f0-9]{64}',str(base['container_xml_sha256'])) or not isinstance(base['relationship_id'],str):
                raise ValueError('静态EQ原图证据无效。')
            crop=base['crop']
            if (not isinstance(crop,dict) or set(crop)!={'l','t','r','b'}
                    or any(type(v) is not int or not 0<=v<100000 for v in crop.values())
                    or crop['l']+crop['r']>=100000 or crop['t']+crop['b']>=100000):
                raise ValueError('静态EQ原图裁切无效。')
            drawing={k:v for k,v in base.items() if k!='image_block_id'}
            # The image occupies the sole non-text operand position in code.
            pos=field['instruction'].find(_IMAGE)
            if pos<0 or field['instruction'].replace(_IMAGE,'')!=raw:
                raise ValueError('静态EQ原图指令位置无效。')
            raw=raw[:pos]+_IMAGE+raw[pos:];scripts=scripts[:pos]+['baseline']+scripts[pos:]
        if raw!=field['instruction']:
            raise ValueError('静态EQ指令来源失配。')
        parsed_base,overlays,text=_parse(raw,scripts,drawing)
        if (parsed_base!=({k:v for k,v in base.items() if k!='image_block_id'})
                or overlays!=field['overlays'] or text!=field['transcription']):
            raise ValueError('静态EQ显示与原指令失配。')
        size=field['source_font_pt']
        if size is not None and (type(size) not in (int,float) or not math.isfinite(size) or not 1<=size<=100):
            raise ValueError('静态EQ字体尺寸无效。')
        if original and value[span['start']:span['end']]!=text:
            raise ValueError('静态EQ原文转录失配。')
        end=span['end']


def cropped_image(image,crop):
    from PySide6.QtCore import QRectF,Qt
    from PySide6.QtGui import QImage,QPainter
    rectangle=QRectF(image.width()*crop['l']/100000,image.height()*crop['t']/100000,
        image.width()*(100000-crop['l']-crop['r'])/100000,image.height()*(100000-crop['t']-crop['b'])/100000)
    cropped=QImage(image.size(),QImage.Format.Format_ARGB32_Premultiplied);cropped.fill(Qt.GlobalColor.transparent)
    painter=QPainter(cropped);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(cropped.rect()),image,rectangle);painter.end();return cropped


def layout_eq(token, assets, image_data, *, font_px=15, bold=False):
    """Measured native image plus editable text/image primitives at same coords."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage,QPainter
    from .desktop_composition_rich_render import rich_lines,flatten_elements,PX_MM
    from .desktop_preparation_images import verify_image_bytes
    field=token['field']; base=field['base']; parts=[]
    def measure(runs):
        lines=rich_lines({'id':'EQ-local','value':''},{},1000,{}, {},1000,
            font_px=font_px,tokens=[{'kind':'text',**r} for r in runs],bold=bold)
        line=next(c for c in lines if c['kind']=='rich_line')
        leaves=list(flatten_elements([{**line,'x':0,'y':0}]))
        width=max((p['x']+p['width'] for p in leaves),default=0)
        top=min((p['y'] for p in leaves),default=0)
        bottom=max((p['y']+p['height'] for p in leaves),default=0)
        return [{**line,'width':width,'x':0,'y':0}],width,top,bottom
    scale=font_px/15
    if base['kind']=='image':
        identity=token['asset_id']; image=QImage.fromData(verify_image_bytes(assets[identity],image_data[identity]))
        if image.isNull():raise ValueError('静态EQ原图无法显示。')
        crop=base['crop']
        # Smoothly sample exact source fractions; no rounding away original crop.
        cropped=cropped_image(image,crop)
        bw,bh=base['width_mm']*scale,base['height_mm']*scale
        leaves=[{'kind':'image','asset_id':identity,'image_block_id':base['image_block_id'],
            'width':bw,'height':bh,'x':0,'y':-bh,'crop':deepcopy(crop),'_image':cropped}]
        top,bottom=-bh,0
    else:
        leaves,bw,top,bottom=measure(base['runs'])
        # Text fragments expose ink top rather than a baseline; use measured
        # line ascent as the common base baseline for both labels and glyphs.
        baseline=leaves[0]['_line'].ascent()/PX_MM
        leaves=[{**p,'y':p['y']-baseline} for p in leaves];top-=baseline;bottom-=baseline
    groups=[(leaves,bw)]
    # up/do is an actual point displacement, not a smaller generic superscript.
    # Source-size evidence determines scaling; without it preserve literal pt.
    source_px=(field['source_font_pt']*96/72) if field['source_font_pt'] else font_px
    for overlay in field['overlays']:
        leaves,ow,ot,ob=measure(overlay['runs'])
        baseline=leaves[0]['_line'].ascent()/PX_MM
        shift=overlay['offset_pt']*25.4/72*(font_px/source_px)
        dy=-baseline + (-shift if overlay['position']=='up' else shift)
        leaves=[{**p,'y':p['y']+dy} for p in leaves];ot+=dy;ob+=dy
        top=min(top,ot);bottom=max(bottom,ob);groups.append((leaves,ow))
    width=max(width for _,width in groups); height=max(.1,bottom-top)
    for leaves,group_width in groups:
        for leaf in leaves:
            parts.append({**leaf,'offset_x':leaf['x']+(width-group_width)/2,'offset_y':leaf['y']-top})
    image=QImage(max(1,math.ceil(width*PX_MM*3)),max(1,math.ceil(height*PX_MM*3)),QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent); painter=QPainter(image);painter.scale(PX_MM*3,PX_MM*3)
    from .desktop_composition_render import paint_elements
    paint_elements(painter,[{**p,'x':p['offset_x'],'y':p['offset_y']} for p in parts]);painter.end()
    return {'kind':'diagram','width':width,'height':height,'_parts':parts,'_image':image,
            'eq_field_sha256':field['field_sha256'],'baseline_mm':-top,'descent_mm':max(0,bottom)}


def png_bytes(image):
    from PySide6.QtCore import QBuffer,QByteArray,QIODevice
    data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer,'PNG'):raise ValueError('静态EQ显示图无法保存。')
    return bytes(data)
