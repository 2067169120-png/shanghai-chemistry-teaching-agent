"""Measured local inline pictures and script runs shared by canvas and exports."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor,QFont,QFontMetricsF,QImage,QTextDocument,QTextCursor,QTextCharFormat,QTextImageFormat,QTextBlockFormat,QTextOption,QGuiApplication
from .desktop_preparation_images import verify_image_bytes
from .desktop_composition_inline import display_tokens
from .reader_cancellation import check_read_cancelled
PX_MM=96/25.4

def rich_lines(row, by_id, width, assets, image_data, image_height, original_text=None, *, font_px=15, opening_line_budget=0, image_limits=None, tokens=None, bold=False):
    if QGuiApplication.instance() is None:raise ValueError('页面排版需要桌面Qt运行环境。')
    tokens = display_tokens(row, by_id, original_text) if tokens is None else tokens
    if any(token['kind'] == 'source_scene' for token in tokens):
        from .word_static_quote_render import layout_scene
        chunks, pending = [], []
        def flush():
            if pending:
                chunks.extend(rich_lines(row, by_id, width, assets, image_data, image_height,
                    original_text, font_px=font_px, image_limits=image_limits, tokens=list(pending), bold=bold))
                pending.clear()
        for token in tokens:
            if token['kind'] == 'source_scene':
                flush()
                chunks.append(layout_scene(token, assets, image_data, width=width, height=image_height,
                    font_px=font_px, block_id=row['id'], image_limits=image_limits))
            else:pending.append(token)
        flush()
        return chunks
    font=QFont(QGuiApplication.font());font.setPixelSize(font_px);font.setBold(bold)
    doc=QTextDocument();doc.setDefaultFont(font);doc.setDocumentMargin(0);doc.setTextWidth(width*PX_MM)
    option=QTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere);doc.setDefaultTextOption(option)
    cursor=QTextCursor(doc);bf=QTextBlockFormat();bf.setTopMargin(0);bf.setBottomMargin(0);cursor.setBlockFormat(bf)
    objects={};positions={}
    for ordinal,token in enumerate(tokens):
        check_read_cancelled()
        if token['kind']=='text':
            fmt=QTextCharFormat();fmt.setFont(font);fmt.setForeground(QColor('#17242f'))
            fmt.setVerticalAlignment({'baseline':QTextCharFormat.VerticalAlignment.AlignNormal,
                'subscript':QTextCharFormat.VerticalAlignment.AlignSubScript,
                'superscript':QTextCharFormat.VerticalAlignment.AlignSuperScript}[token['script']])
            cursor.insertText(token['text'],fmt)
        elif token['kind']=='eq':
            from .word_static_eq import layout_eq
            picture=layout_eq(token,assets,image_data,font_px=font_px,bold=bold)
            for part in picture['_parts']:
                # Local measurements are implementation details; exported
                # primitives belong to their real source paragraph/answer.
                part['block_id']=row['id']
            if picture['width']>width or picture['height']>image_height:
                raise ValueError('静态EQ条件式超出可用行宽或高度，请扩大画幅。')
            name='shchem-eq-'+str(ordinal);doc.addResource(QTextDocument.ResourceType.ImageResource,QUrl(name),picture['_image'])
            fmt=QTextImageFormat();fmt.setName(name);fmt.setWidth(picture['width']*PX_MM);fmt.setHeight(picture['height']*PX_MM)
            positions[name]=cursor.position();cursor.insertImage(fmt);objects[name]=picture
        else:
            identity=token['asset_id']
            if identity not in assets or identity not in image_data:raise ValueError('行内图片缺失，未省略原图。')
            image=QImage.fromData(verify_image_bytes(assets[identity],image_data[identity]))
            if image.isNull():raise ValueError('行内图片无法显示，未省略原图。')
            if 'crop' in token:
                from .word_static_eq import cropped_image
                image=cropped_image(image,token['crop'])
            if 'transform' in token:
                from .desktop_handout_paint_preview import validate_picture_transform
                validate_picture_transform(token['transform'])
                image=image.mirrored(False, True)
            w=token['width_mm'];h=token['height_mm']
            if w=='unknown' or h=='unknown':w,h=image.width()/PX_MM,image.height()/PX_MM
            # Enlarge authored inline formulas/pictures with projection text;
            # the sealed source dimensions and original image bytes stay intact.
            w*=font_px/15;h*=font_px/15
            factor=min(1,width/w,image_height/h);w*=factor;h*=factor
            from .desktop_composition_image_display import cap_image_size
            w,h=cap_image_size(w,h,(image_limits or {}).get(token['image_block_id']))
            name='shchem-inline-'+str(ordinal);doc.addResource(QTextDocument.ResourceType.ImageResource,QUrl(name),image)
            fmt=QTextImageFormat();fmt.setName(name);fmt.setWidth(w*PX_MM);fmt.setHeight(h*PX_MM)
            positions[name]=cursor.position();cursor.insertImage(fmt)
            objects[name]={'kind':'image','asset_id':identity,'image_block_id':token['image_block_id'],
                           'width':w,'height':h,'_image':image,
                           **({'transform':token['transform']} if 'transform' in token else {}),
                           **({'crop':token['crop']} if 'crop' in token else {})}
    doc.documentLayout().documentSize()
    chunks=_document_chunks(doc,row,objects,width,font_px)
    if opening_line_budget<=0:return chunks
    # A Word paragraph may contain both a short opening stem and its first
    # whole picture. Measure those actual Qt lines before capping this one
    # picture; small inline formulas stay at the original display scale.
    for name,picture in objects.items():
        if picture['kind']!='image':continue
        if picture['height']<=2*font_px/PX_MM+.001:continue
        index=next(i for i,c in enumerate(chunks) if any(
            p['kind']=='image' and p['image_block_id']==picture['image_block_id'] for p in c.get('_parts',[])))
        prefix=chunks[:index];visible=[c for c in prefix if c['kind']!='space']
        short=0<len(visible)<=opening_line_budget and all(
            p['height']<=2*font_px/PX_MM+.001 for c in visible for p in c.get('_parts',[]) if p['kind']=='image')
        available=image_height-sum(c['height'] for c in prefix)
        if short and 0<available<picture['height']:
            factor=available/picture['height'];picture['width']*=factor;picture['height']*=factor
            edit=QTextCursor(doc);edit.setPosition(positions[name]);edit.setPosition(positions[name]+1,QTextCursor.MoveMode.KeepAnchor)
            fmt=edit.charFormat().toImageFormat();fmt.setWidth(picture['width']*PX_MM);fmt.setHeight(picture['height']*PX_MM)
            edit.setCharFormat(fmt);doc.documentLayout().documentSize()
            chunks=_document_chunks(doc,row,objects,width,font_px)
        break
    return chunks


def _document_chunks(doc,row,objects,width,font_px):
    chunks=[];block=doc.firstBlock();font=doc.defaultFont()
    while block.isValid():
        layout=block.layout();iterator=block.begin();fragments=[]
        while not iterator.atEnd():
            fragment=iterator.fragment()
            if fragment.isValid():fragments.append(fragment)
            iterator+=1
        for line_index in range(layout.lineCount()):
            line=layout.lineAt(line_index);start=block.position()+line.textStart();end=start+line.textLength();parts=[]
            for fragment in fragments:
                lo=max(start,fragment.position());hi=min(end,fragment.position()+fragment.length())
                if lo>=hi:continue
                fmt=fragment.charFormat();x=line.cursorToX(lo-block.position())[0]/PX_MM
                if fmt.isImageFormat():
                    picture=objects[fmt.toImageFormat().name()]
                    parts.append({**picture,'offset_x':x,'offset_y':line.ascent()/PX_MM-
                                  picture.get('baseline_mm',picture['height'])})
                else:
                    selection=QTextCursor(doc);selection.setPosition(lo);selection.setPosition(hi,QTextCursor.MoveMode.KeepAnchor)
                    text=selection.selectedText().replace('\u2028','\n').replace('\u2029','\n')
                    glyphs=line.glyphRuns(lo-block.position(),hi-lo)
                    if any(index==0 for run in glyphs for index in run.glyphIndexes()):
                        raise ValueError('行内文字存在无法显示的字形，请载入支持的本地字体。')
                    raw=next((g.rawFont() for g in glyphs if g.rawFont().isValid()),None)
                    run_font_px=raw.pixelSize() if raw else font_px
                    baseline=next((p.y() for g in glyphs for p in g.positions()),line.y()+line.ascent())
                    # glyphRuns exposes unshifted positions. QTextLayout.draw
                    # applies these explicit format baselines when painting.
                    base_height=QFontMetricsF(fmt.font()).height()
                    if fmt.verticalAlignment()==QTextCharFormat.VerticalAlignment.AlignSuperScript:
                        baseline-=base_height*fmt.superScriptBaseline()/100
                    elif fmt.verticalAlignment()==QTextCharFormat.VerticalAlignment.AlignSubScript:
                        baseline+=base_height*fmt.subScriptBaseline()/100
                    y=(baseline-line.y()-(raw.ascent() if raw else line.ascent()))/PX_MM
                    span=abs(line.cursorToX(hi-block.position())[0]-line.cursorToX(lo-block.position())[0])/PX_MM
                    # A tall inline image increases the Qt line height, not
                    # the glyph run. Using that full height for a baseline-
                    # shifted PPT text box would extend it below its table cell.
                    run_height = raw.ascent()+raw.descent() if raw else base_height
                    parts.append({'kind':'text','text':text,'width':max(.1,span+.2),'height':run_height/PX_MM,
                        'font_px':run_font_px,'font_family':raw.familyName() if raw else font.family(),'bold':fmt.font().bold(),
                        'offset_x':x,'offset_y':y,'script':fmt.verticalAlignment().name})
            top=layout.position().y()+line.y()
            measured_height=max(line.height()/PX_MM,max((p['offset_y']+p['height'] for p in parts),default=0))
            chunks.append({'kind':'rich_line','height':measured_height+2/PX_MM,'width':width,'block_id':row['id'],
                '_document':doc,'_line':line,'_doc_top':top,'_parts':parts,
                '_paragraph':(row['id'],block.blockNumber())})
        chunks.append({'kind':'space','height':1.0,'width':width,'block_id':row['id']})
        block=block.next()
    return chunks

def positioned_parts(element):
    """Place direct children while preserving their own native paint objects."""
    inherited = {k:v for k,v in element.items()
        if (not k.startswith('_') or k == '_table_cell') and k not in {'kind','height','width','offset_x','offset_y'}}
    for part in element['_parts']:
        yield {**inherited, **part, 'x':element['x']+part['offset_x'], 'y':element['y']+part['offset_y']}


def flatten_elements(elements):
    """Recursively accumulate cell/line/run positions for editable PPT/audit."""
    for element in elements:
        if element['kind'] not in {'rich_line','diagram'}:yield element;continue
        yield from flatten_elements(positioned_parts(element))
