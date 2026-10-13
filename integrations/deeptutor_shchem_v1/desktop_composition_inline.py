"""Source-bound paragraph display; original searchable transcription stays intact.

Bindings come only from the already verified native object inventory. No image
is discarded. Plain old cards keep their original display. Compound math not
covered by this display grammar remains visible as its full transcription.
"""
from __future__ import annotations
from copy import deepcopy
from io import BytesIO
from difflib import SequenceMatcher
import re
from docx import Document
from docx.oxml import OxmlElement
from .desktop_preparation_sources import _body_blocks
from .word_native_text import WordNativeTextReader
from . import word_native_math as native_math
from .word_static_eq import paragraph_fields

SCHEMA='shchem.native-word-paragraph.v1'
MARKER=re.compile(r'【待查看原文：[^】]*】')
SCRIPT=re.compile(r'([_^])\{([^{}]*)\}')
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
WP='{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
V='{urn:schemas-microsoft-com:vml}'
M='{http://schemas.openxmlformats.org/officeDocument/2006/math}'


def _math_script_spans(node):
    """Display only scripts explicitly authored in an already validated OMML.

    Literal m:t brace notation, fractions, roots and compound nested scripts
    keep their full transcription. No new math admission is granted here.
    """
    name=node.tag[len(M):];value=native_math._render(node)
    if name in {'oMath','oMathPara'}:
        children=[c for c in node if c.tag!=M+'oMathParaPr'];parts=[];spans=[];offset=0
        for i,child in enumerate(children):
            if i and name=='oMathPara':parts.append('\n');offset+=1
            text,child_spans=_math_script_spans(child)
            spans.extend({**s,'start':s['start']+offset,'end':s['end']+offset} for s in child_spans)
            parts.append(text);offset+=len(text)
        return value,spans if ''.join(parts)==value else []
    if name in {'sSub','sSup','sSubSup'}:
        required={'sSub':('e','sub'),'sSup':('e','sup'),'sSubSup':('e','sub','sup')}[name]
        _,children=native_math._operands(node,name,required)
        values=[native_math._argument(child) for child in children];offset=len(native_math._base_group(values[0]));spans=[]
        for key,text in zip(required[1:],values[1:]):
            end=offset+len(text)+3
            if '{' not in text and '}' not in text:spans.append({'start':offset,'end':end,'script':'subscript' if key=='sub' else 'superscript'})
            offset=end
        return value,spans
    return value,[]

def _dimension(node):
    if node.tag==W+'drawing':
        extent=node.find('.//'+WP+'extent')
        try:
            return float(extent.get('cx'))/36000,float(extent.get('cy'))/36000
        except (AttributeError,TypeError,ValueError):return 'unknown','unknown'
    shape=node.find('.//'+V+'shape')
    style={key.strip():value.strip() for part in (shape.get('style','') if shape is not None else '').split(';')
           if ':' in part for key,value in [part.split(':',1)]}
    sizes=[]
    for name in ('width','height'):
        match=re.fullmatch(r'(\d+(?:\.\d+)?)(pt|mm|cm|in)',style.get(name,''))
        sizes.append(float(match[1])*{'pt':25.4/72,'mm':1,'cm':10,'in':25.4}[match[2]] if match else 'unknown')
    return tuple(sizes)

class NativeParagraphDisplay:
    def __init__(self, source):
        self.doc=Document(BytesIO(source))
        self.elements=list(_body_blocks(self.doc._element.body))
        self.reader=WordNativeTextReader(self.doc.styles.element)

    def source_scene(self, index):
        """Describe supported source geometry without changing its preview."""
        if type(index) is not int or not 1 <= index <= len(self.elements):
            return None
        from .word_static_quote import parse_scene
        return parse_scene(self.elements[index-1], self.doc, self.reader)

    def _script_spans(self, paragraph):
        """Record effective Word run formatting, never literal brace syntax.

        Complex OMML stays as the complete searchable transcription until its
        layout has separate evidence. Unknown wrappers also retain plain text.
        """
        ppr=paragraph.find(W+'pPr');style=ppr.find(W+'pStyle') if ppr is not None else None
        style_id=style.get(W+'val') if style is not None else self.reader.default_paragraph
        def read(node):
            wrapper=OxmlElement('w:p')
            if ppr is not None:wrapper.append(deepcopy(ppr))
            def boundary():
                run=OxmlElement('w:r');prop=OxmlElement('w:rPr')
                vertical=OxmlElement('w:vertAlign');vertical.set(W+'val','baseline');prop.append(vertical)
                visible=OxmlElement('w:vanish');visible.set(W+'val','0');prop.append(visible);run.append(prop)
                text=OxmlElement('w:t');text.text='\ue000';run.append(text);return run
            # The shared reader trims outer whitespace. Boundaries preserve
            # spaces between individual source runs before joining them.
            wrapper.append(boundary());wrapper.append(deepcopy(node));wrapper.append(boundary())
            value=self.reader.read(wrapper).text
            return value[1:-1] if value.startswith('\ue000') and value.endswith('\ue000') else ''
        def visit(node):
            value=read(node)
            if node.tag in {M+'oMath',M+'oMathPara'} and native_math.omml_text(node)==value:
                return _math_script_spans(node)
            if node.tag==W+'r':
                vertical=self.reader._run_property(node,style_id,'vertAlign')
                prefix='_{' if vertical=='subscript' else '^{'
                if vertical in {'subscript','superscript'} and value.startswith(prefix) and value.endswith('}'):
                    return value,[{'start':0,'end':len(value),'script':vertical}]
            if node.tag in {W+t for t in ('r','ins','moveTo','sdt','sdtContent','customXml','hyperlink','fldSimple')}:
                parts=[];spans=[];offset=0
                for child in node:
                    text,child_spans=visit(child)
                    spans.extend({**s,'start':s['start']+offset,'end':s['end']+offset} for s in child_spans)
                    parts.append(text);offset+=len(text)
                if ''.join(parts)==value:return value,spans
            return value,[]
        parts=[];spans=[];offset=0
        fields={f['run_start']:f for f in paragraph_fields(paragraph,lambda run,name:
            self.reader._run_property(run,style_id,name))}
        children=list(paragraph);position=0
        while position<len(children):
            field=fields.get(position)
            if field is not None:
                value,child_spans=field['transcription'],[];position=field['run_end']+1
            else:
                value,child_spans=visit(children[position]);position+=1
            spans.extend({**s,'start':s['start']+offset,'end':s['end']+offset} for s in child_spans)
            parts.append(value);offset+=len(value)
        joined=''.join(parts);leading=len(joined)-len(joined.lstrip())
        return [{**s,'start':s['start']-leading,'end':s['end']-leading} for s in spans
                if leading<=s['start'] and s['end']<=leading+len(joined.strip())] if joined.strip()==self.reader.read(paragraph).text else []

    def bind(self, text_block, images, objects):
        from .offline_word_image_label_review import _xml_sha
        index=text_block['source_index'];element=self.elements[index-1]
        if element.tag!=W+'p':return
        native=self.reader.read(element)
        if native.text!=text_block['value']:
            raise ValueError('行内显示与完整原生文字不一致，请重新核对来源。')
        nodes=[n for n in element.iter() if n.tag in {W+'drawing',W+'pict',W+'object'}]
        if len(nodes)!=len(images) or len(objects)!=len(images):
            raise ValueError('行内图出现次数与已验证原生对象不一致。')
        refs=[]
        for node,image,obj in zip(nodes,images,objects):
            if obj['container_xml_sha256']!=_xml_sha(node):
                raise ValueError('行内图原生对象已变化，未沿用旧位置。')
            width,height=_dimension(node)
            refs.append({'image_block_id':image['id'],'placeholder':obj['placeholder'],
                         'width_mm':width,'height_mm':height,
                         **({'transform':deepcopy(obj['transform'])} if 'transform' in obj else {})})
        if MARKER.findall(text_block['value'])!=[r['placeholder'] for r in refs]:
            raise ValueError('原文占位与原生图形不能逐项对应。')
        text_block['presentation']={'schema':SCHEMA,'images':refs,'scripts':self._script_spans(element)}
        scene = self.source_scene(index)
        if scene is not None:
            from .word_static_quote import validate_scene
            validate_scene(scene, text_block['value'], images=refs)
            text_block['presentation']['scene'] = scene
        if native.static_eqs:
            eqs=deepcopy(list(native.static_eqs))
            for span in eqs:
                base=span['field']['base']
                if base['kind']=='image':
                    matches=[ref for node,ref in zip(nodes,refs)
                             if _xml_sha(node)==base['container_xml_sha256']]
                    if len(matches)!=1:raise ValueError('静态EQ原图与原生出现位置不能唯一绑定。')
                    base['image_block_id']=matches[0]['image_block_id']
            text_block['presentation']['eqs']=eqs

def display_tokens(row, by_id, original_text=None):
    """Return safe local text/scripts/images, with no HTML or external URLs."""
    if 'presentation' not in row:return [{'kind':'text','text':row['value'],'script':'baseline'}]
    value=row['value'];source=value if original_text is None else original_text
    scene = row['presentation'].get('scene')
    if scene is not None:
        from .word_static_quote import validate_scene
        validate_scene(scene, scene['text'], images=row['presentation']['images'])
        # The source paragraph is image-only. Preserve additions on both sides;
        # if its complete marker span was edited, use the ordinary full image
        # projection below rather than hiding a teacher's added text.
        matches = (SequenceMatcher(None, scene['text'], value, autojunk=False).get_matching_blocks()
                   if max(len(scene['text']), len(value)) <= 20000 else [])
        complete = next((m for m in matches if m.a == 0 and m.size == len(scene['text'])), None)
        if complete is not None:
            images = [{'kind': 'image', 'asset_id': by_id[ref['image_block_id']]['value'], **deepcopy(ref)}
                      for ref in row['presentation']['images']]
            result = []
            if complete.b:
                result.append({'kind': 'text', 'text': value[:complete.b], 'script': 'baseline'})
            result.append({'kind': 'source_scene', 'scene': deepcopy(scene), 'images': images})
            if complete.b + complete.size < len(value):
                result.append({'kind': 'text', 'text': value[complete.b + complete.size:], 'script': 'baseline'})
            return result
    spans=row['presentation'].get('scripts',[])
    eqs=row['presentation'].get('eqs',[])
    if value!=source:
        # Retain formatting only for source runs unchanged in the work copy.
        # Large edits fall back to visible raw syntax instead of costly matching.
        matches=SequenceMatcher(None,source,value,autojunk=False).get_matching_blocks() if max(len(source),len(value))<=20000 else []
        spans=[{**s,'start':s['start']+m.b-m.a,'end':s['end']+m.b-m.a}
               for s in spans for m in matches if m.a<=s['start'] and s['end']<=m.a+m.size]
        eqs=[{**s,'start':s['start']+m.b-m.a,'end':s['end']+m.b-m.a}
             for s in eqs for m in matches if m.a<=s['start'] and s['end']<=m.a+m.size]
    events=[(s['start'],s['end'],'eq',s) for s in eqs]
    events.extend((m.start(),m.end(),'image',r) for m,r in zip(MARKER.finditer(value),row['presentation']['images'])
                  if not any(s['start']<=m.start() and m.end()<=s['end'] for s in eqs))
    for span in spans:
        start,end=span['start'],span['end'];raw=value[start:end]
        if SCRIPT.fullmatch(raw) and not any(start<b and a<end for a,b,_,_ in events):
            events.append((start,end,'script',span))
    result=[];cursor=0
    for start,end,kind,ref in sorted(events,key=lambda e:e[0]):
        if start>cursor:result.append({'kind':'text','text':value[cursor:start],'script':'baseline'})
        if kind=='eq':
            token={'kind':'eq','field':deepcopy(ref['field'])}
            base=ref['field']['base']
            if base['kind']=='image':token['asset_id']=by_id[base['image_block_id']]['value']
            result.append(token)
        elif kind=='image':
            token={'kind':'image','asset_id':by_id[ref['image_block_id']]['value'],**deepcopy(ref)}
            base=next((s['field']['base'] for s in row['presentation'].get('eqs',[])
                       if s['field']['base']['kind']=='image' and s['field']['base']['image_block_id']==ref['image_block_id']),None)
            if base is not None:token['crop']=deepcopy(base['crop'])
            result.append(token)
        else:result.append({'kind':'text','text':value[start+2:end-1],'script':ref['script']})
        cursor=end
    if cursor<len(value):result.append({'kind':'text','text':value[cursor:],'script':'baseline'})
    return result

def bound_image_ids(item):
    return {r['image_block_id'] for b in item['blocks'] for r in b.get('presentation',{}).get('images',[])}
