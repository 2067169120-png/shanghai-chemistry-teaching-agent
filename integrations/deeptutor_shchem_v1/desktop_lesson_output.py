"""Local, versioned output from one teacher-owned lesson design."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from .desktop_lesson_design import candidate_from_design, content_fingerprint, coverage, validate_design
from .desktop_state import utc_now
from .reader_cancellation import ReadCancelled, check_read_cancelled, publication_guard

FILES = ("lesson_presentation.pptx", "lesson_plan.docx", "student_worksheet.docx")
COMPOSITION_FILES = FILES + ("lesson_canvas.pdf", "student_canvas.pdf", "student_presentation.pptx")


def output_names(record):
    names = tuple(f['name'] for f in record['files'])
    if len(set(names)) != len(names) or set(names) not in (set(FILES), set(COMPOSITION_FILES)):
        raise ValueError('教学成品文件清单不完整。')
    return names


def primary_output_names(record):
    """Resolve the two teacher purposes from a registered legacy/current bundle."""
    names = output_names(record)
    classroom = 'student_presentation.pptx' if 'student_presentation.pptx' in names else 'lesson_presentation.pptx'
    return 'lesson_plan.docx', classroom


def _document(payload, student=False):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Mm(210), Mm(297)
    s.top_margin = s.bottom_margin = Mm(18)
    s.left_margin = s.right_margin = Mm(20)
    for key, size in (("Normal", 11), ("Title", 20), ("Heading 1", 15), ("Heading 2", 12)):
        style = doc.styles[key]
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = key != "Normal"
        style.paragraph_format.line_spacing = 1.15
        # Some installed Word templates add a coloured border to Title.
        # A teaching document should keep its own simple, reproducible style.
        for border in style._element.xpath('./w:pPr/w:pBdr'):
            border.getparent().remove(border)
    notes=payload.get('lesson_design',{}).get('classroom_notes',False)
    if notes and student:doc.core_properties.category='shchem.classroom-notes.v1'
    doc.add_heading(payload["topic"] + ((" · 课堂讲义" if notes else " · 学习单") if student else " · 教案"), 0)
    doc.add_paragraph((payload.get("audience", "") + "　" + payload.get("lesson_timing", "")).strip())
    return doc


def write_documents(payload, directory, image_data):
    from docx.shared import Mm
    from .desktop_preparation_images import verify_image_bytes
    from .desktop_word_picture import add_word_picture, add_word_document_picture
    from .desktop_composition_activities import checked_activity_links
    from .desktop_framework_composition import checked_framework_links
    from .desktop_teaching_framework import (HEADINGS, add_framework_opening,
        classroom_text, framework_from_design)
    plan = validate_design(payload["lesson_design"])
    links = checked_activity_links(plan, payload.get('image_assets', []))
    checked_framework_links(plan)
    on_canvas = {r['node_id'] for r in links if r['mode'] == 'linked' and r['design_id'] == plan['id']}
    images = {x["asset_id"]: x for x in payload.get("image_assets", [])}
    teacher, student = _document(payload), _document(payload, True)
    classroom_notes=plan.get('classroom_notes',False)
    printed_images=set()
    if classroom_notes:
        for node in plan['nodes']:
            if node['student_material'] and (node['material_text'].strip() or node['image_ids']):
                student.add_heading(node['title'], 1)
                for identity in node['image_ids']:
                    if identity in printed_images:continue
                    asset=images[identity]
                    data=verify_image_bytes(asset,image_data[identity])
                    add_word_document_picture(student,data,asset,width=Mm(min(165,65*asset['width']/asset['height'])))
                    student.add_paragraph(asset['caption'])
                    printed_images.add(identity)
                for paragraph in node['material_text'].split('\n\n'):
                    if paragraph.strip():student.add_paragraph(paragraph)
    framework = framework_from_design(payload)
    add_framework_opening(teacher, framework)
    teacher.add_heading(HEADINGS[3], 1)
    teacher.add_heading('1. 导入新课', 2)
    if framework['introduction']: teacher.add_paragraph(classroom_text(framework['introduction']))
    teacher.add_heading('2. 新课讲授', 2)
    for i, n in enumerate(plan["nodes"], 1):
        if n['id'] in on_canvas:
            continue  # Included once below in the reviewed canvas reading order.
        for doc, is_student in ((teacher, False), (student, True)):
            if is_student and classroom_notes:continue
            doc.add_heading(f"{i}. {n['title']}", 2 if not is_student else 1)
            if not is_student:
                goals = [g["text"] for g in plan["objectives"] if g["id"] in n["objective_ids"]]
                timing = "" if n["minutes"] is None else f"{n['minutes']}分钟"
                doc.add_paragraph("目标：" + "；".join(goals) + "　用时：" + timing)
            if n["material_text"] and (not is_student or n["student_material"]):
                doc.add_paragraph(n["material_text"])
            if not is_student or n["student_material"]:
                for identity in n["image_ids"]:
                    a = images[identity]
                    data = verify_image_bytes(a, image_data[identity])
                    # Preserve proportions and original bytes; fit on one A4 page.
                    width = min(165, 200 * a["width"] / a["height"])
                    add_word_document_picture(doc, data, a, width=Mm(width))
                    doc.add_paragraph(a["caption"])
            for key, label in (("teacher_action", "教师活动"), ("student_task", "学生任务"),
                               ("expected_output", "预期产出"), ("criteria", "评价依据"),
                               ("teacher_answer", "教师答案"), ("notes", "教师备注")):
                if n[key] and (not is_student or key == "student_task"):
                    text = n[key] if key == 'teacher_answer' else classroom_text(n[key])
                    if text: doc.add_paragraph(label + "：" + text)
    from .desktop_lesson_composition import visible_blocks
    from .desktop_composition_inline import bound_image_ids,display_tokens
    if plan.get('composition', {}).get('resources'):
        teacher.add_heading('课堂讲解与例题', 1)
        if classroom_notes and any(n['student_material'] and n['material_text'].strip() for n in plan['nodes']):
            student.add_heading('图表、结构与例题', 1)
    for item in plan.get('composition', {}).get('resources', []):
        for doc, is_student in ((teacher, False), (student, True)):
            if is_student and not classroom_notes and item['id'] in plan['composition'].get('click_reveals', []):
                continue
            if not is_student and item['original']['kind'] == 'lesson_framework':
                continue  # Canonical framework headings are already in the teacher document.
            blocks = visible_blocks(item, student=is_student)
            if is_student and not blocks:
                continue
            if (is_student and classroom_notes and all(r['kind']=='image' for r in blocks)
                    and all(r['value'] in printed_images for r in blocks)):
                continue
            resource_start = len(doc.paragraphs)
            doc.add_heading(item['title'], 2 if is_student and classroom_notes else 1)
            if is_student and classroom_notes and item.get('exposition'):
                _word_exposition(doc, item, blocks)
                continue
            body_paragraphs = []
            bound=bound_image_ids(item);by_id={b['id']:b for b in item['blocks']}
            image_limits = item.get('image_display', {}).get('limits', {})
            from .desktop_composition_image_display import cap_image_size
            originals={b['id']:b['value'] for b in item['original']['blocks'] if b['kind']=='text'}
            for row in blocks:
                if row['id'] in bound:continue
                if row['kind'] == 'text':
                    if item['original']['kind']=='handout_knowledge':
                        from .desktop_handout_knowledge_tables import write_word_table
                        from .desktop_handout_table_projection import source_table_projection
                        projection=source_table_projection(item,row)
                        if projection is not None:
                            write_word_table(doc,projection['rows'],cell_tokens=projection['cell_tokens'],
                                assets=images,image_data=image_data,image_limits=image_limits)
                            bound.update(projection['image_block_ids'])
                            continue
                    if 'presentation' not in row:
                        paragraph=doc.add_paragraph(row['value'])
                    else:
                        paragraph=doc.add_paragraph()
                        for token in display_tokens(row,by_id,originals[row['id']]):
                            run=paragraph.add_run()
                            if token['kind']=='text':
                                run.text=token['text'];run.font.subscript=token['script']=='subscript';run.font.superscript=token['script']=='superscript'
                            elif token['kind']=='eq':
                                from .word_static_eq import layout_eq,png_bytes
                                picture=layout_eq(token,images,image_data,font_px=15)
                                run.add_picture(BytesIO(png_bytes(picture['_image'])),
                                    width=Mm(picture['width']),height=Mm(picture['height']))
                                if picture['descent_mm']:
                                    from docx.oxml import OxmlElement
                                    from docx.oxml.ns import qn
                                    position=OxmlElement('w:position')
                                    position.set(qn('w:val'),str(-round(picture['descent_mm']*72/25.4*2)))
                                    run._r.get_or_add_rPr().append(position)
                            elif token['kind'] == 'source_scene':
                                from .word_static_quote_render import layout_scene, scene_picture
                                from .word_static_eq import png_bytes
                                picture = layout_scene(token, images, image_data, width=165, height=220,
                                    block_id=row['id'], image_limits=image_limits)
                                run.add_picture(BytesIO(png_bytes(scene_picture(picture))),
                                    width=Mm(picture['width']), height=Mm(picture['height']))
                            else:
                                identity=token['asset_id'];asset=images[identity]
                                data=verify_image_bytes(asset,image_data[identity]);w,h=token['width_mm'],token['height_mm']
                                if w=='unknown' or h=='unknown':w,h=asset['width']*25.4/96,asset['height']*25.4/96
                                factor=min(1,165/w,220/h)
                                w,h=cap_image_size(w*factor,h*factor,image_limits.get(token['image_block_id']))
                                shape=add_word_picture(run,data,asset,width=Mm(w),height=Mm(h))
                                if 'transform' in token:
                                    from .desktop_handout_paint_preview import validate_picture_transform
                                    from docx.oxml.ns import qn
                                    validate_picture_transform(token['transform'])
                                    shape._inline.find('.//' + qn('pic:spPr') + '/' + qn('a:xfrm')).set('flipV', '1')
                                if 'crop' in token:
                                    from docx.oxml import OxmlElement
                                    from docx.oxml.ns import qn
                                    crop=OxmlElement('a:srcRect')
                                    for key,value in token['crop'].items():crop.set(key,str(value))
                                    fill=shape._inline.find('.//'+qn('pic:blipFill'))
                                    fill.insert(1,crop)
                else:
                    identity = row['value']
                    if identity not in images or identity not in image_data:
                        raise ValueError('完整资源的本地图片缺失，未生成省略图片的文档。')
                    asset = images[identity]
                    data = verify_image_bytes(asset, image_data[identity])
                    if is_student and classroom_notes and item['original']['kind'] != 'handout_knowledge':
                        if identity in printed_images:continue
                        printed_images.add(identity)
                    height=65 if is_student and classroom_notes else 200
                    shape=add_word_document_picture(doc,data,asset,width=Mm(min(165,height*asset['width']/asset['height'])))
                    limits=image_limits.get(row['id'])
                    if limits:
                        # Start from Word's actual fitted geometry, including
                        # the source image's native DPI. With no display setting
                        # the existing width-only picture path is unchanged.
                        original_size=(shape.width.mm,shape.height.mm)
                        w,h=cap_image_size(*original_size,limits)
                        if (w,h)!=original_size:
                            shape.width=Mm(w);shape.height=Mm(h)
                    paragraph = doc.paragraphs[-1]
                body_paragraphs.append((row['role'], paragraph))
            # Original source/role snapshots are retained in the saved lesson;
            # teacher deliverables show the lesson rather than audit citations.
            # Keep short resources together; long text and pictures retain
            # normal complete-text pagination.
            paragraphs = doc.paragraphs[resource_start:]
            if item['original']['kind'] == 'complete_question':
                _keep_question_opening(doc, paragraphs)
                _keep_short_question(doc, paragraphs[0], body_paragraphs)
            if item['original']['kind'] in {'textbook_image', 'preparation_image', 'handout_image'}:
                _keep_short_figure(doc, paragraphs, blocks)
            if (item['original']['kind'] in {'lesson_activity', 'knowledge_note', 'personal_text'}
                    and all(r['kind'] == 'text' and 'presentation' not in r for r in blocks)
                    and sum(max(1, (len(line)+37)//38)+1 for r in blocks for line in r['value'].split('\n')) <= 18
                    and sum(max(1, (len(line)+37)//38)+1 for p in paragraphs for line in p.text.split('\n')) <= 32):
                for index, paragraph in enumerate(paragraphs):
                    paragraph.paragraph_format.keep_together = True
                    paragraph.paragraph_format.keep_with_next = index < len(paragraphs)-1
    teacher.add_heading('3. 巩固小结', 2)
    if framework['consolidation']: teacher.add_paragraph(classroom_text(framework['consolidation']))
    teacher.add_heading('4. 布置作业', 2)
    if framework['homework']: teacher.add_paragraph(classroom_text(framework['homework']))
    teacher.add_heading(HEADINGS[4], 1)
    if framework['board_design']: teacher.add_paragraph(classroom_text(framework['board_design']))
    teacher.save(directory / "lesson_plan.docx")
    student.save(directory / "student_worksheet.docx")


def _word_exposition(doc, item, blocks):
    """Preserve complete diagram text in editable, readable Word tables."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt
    kind=item['exposition']['kind']
    if kind=='notes':
        for block in blocks:
            for text in block['value'].split('\n\n'):
                if not text.strip():continue
                lines=text.split('\n',1)
                if len(lines)==2:
                    heading=doc.add_paragraph();heading.add_run(lines[0]).bold=True
                    heading.paragraph_format.keep_with_next=True
                    doc.add_paragraph(lines[1])
                else:doc.add_paragraph(text)
        return
    vertical=kind=='process_vertical'
    process=kind=='process'
    columns=1 if vertical else len(blocks)*2-1 if process else len(blocks)
    table=doc.add_table(rows=len(blocks) if vertical else 1, cols=columns)
    table.autofit=False
    section=doc.sections[-1]
    width=(section.page_width-section.left_margin-section.right_margin)/36000
    widths=([width] if vertical else
            [7 if i%2 else (width-7*(len(blocks)-1))/len(blocks) for i in range(columns)] if process else
            [width/columns]*columns)
    for column, value in zip(table.columns,widths):column.width=Mm(value)
    for row in table.rows:
        for cell, value in zip(row.cells,widths):cell.width=Mm(value)
    for index, block in enumerate(blocks):
        cell=table.cell(index,0) if vertical else table.cell(0,index*2 if process else index)
        lines=block['value'].split('\n')
        for position,line in enumerate(lines):
            paragraph=cell.paragraphs[0] if position==0 else cell.add_paragraph()
            paragraph.paragraph_format.space_after=Pt(3)
            paragraph.paragraph_format.keep_with_next=position<len(lines)-1
            paragraph.add_run(line).bold=position==0
        properties=cell._tc.get_or_add_tcPr()
        shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'F1F5F9');properties.append(shade)
        margins=OxmlElement('w:tcMar')
        for edge in ('top','left','bottom','right'):
            margin=OxmlElement('w:'+edge);margin.set(qn('w:w'),'90');margin.set(qn('w:type'),'dxa');margins.append(margin)
        properties.append(margins)
        if process and index<len(blocks)-1:
            arrow=table.cell(0,index*2+1).paragraphs[0]
            arrow.alignment=1;arrow.add_run('→')
    for row in table.rows:
        keep=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(keep)
    # A table must be followed by a paragraph. Keep its spacing small while
    # allowing Word to paginate the next teaching section normally.
    paragraph=doc.add_paragraph();paragraph.paragraph_format.space_after=Pt(3)
    paragraph.paragraph_format.space_before=Pt(0);paragraph.paragraph_format.line_spacing=0.5


def _keep_short_question(doc, heading, body_paragraphs):
    """Keep bounded contiguous question material, without chaining answers.

    Student visibility has already been applied. Inline pictures belong to
    their containing paragraph, so neither hidden nor bound picture rows enter
    this group twice. Long questions retain the existing opening/normal flow.
    """
    section = doc.sections[-1]
    usable = (section.page_height-section.top_margin-section.bottom_margin)/36000
    group = [heading]
    has_question = False
    for role, paragraph in [*body_paragraphs, (None, None)]:
        if role in {'shared_material', 'question'}:
            group.append(paragraph)
            has_question |= role == 'question'
            continue
        if has_question and _word_group_height(doc, group) <= usable-12:
            for index, item in enumerate(group):
                item.paragraph_format.keep_together = True
                item.paragraph_format.keep_with_next = index < len(group)-1
        group = []
        has_question = False


def _keep_question_opening(doc, paragraphs):
    """Keep a bounded short stem with its first diagram, in both editions.

    Small inline formula objects and badges do not end the opening group.
    This is a soft pagination rule; long openings and the rest of the question
    retain normal Word flow, with all source text and pictures unchanged.
    """
    group = []
    for paragraph in paragraphs:
        group.append(paragraph)
        if sum(len(p.text) for p in group) > 400:
            return
        if any(int(e.get('cx'))/36000 >= 30 and int(e.get('cy'))/36000 >= 10
               for e in paragraph._p.xpath('.//wp:extent')):
            break
    else:
        return
    section = doc.sections[-1]
    usable = (section.page_height-section.top_margin-section.bottom_margin)/36000
    if _word_group_height(doc, group) > min(usable-12, usable*.55):
        return
    for index, paragraph in enumerate(group):
        paragraph.paragraph_format.keep_together = True
        paragraph.paragraph_format.keep_with_next = index < len(group)-1


def _word_group_height(doc, paragraphs):
    """Conservative millimetre estimate including text, images and spacing."""
    section = doc.sections[-1]
    width = (section.page_width-section.left_margin-section.right_margin)/36000
    height = 0.0
    for paragraph in paragraphs:
        sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
        style_size = paragraph.style.font.size
        size = max(sizes) if sizes else style_size.pt if style_size is not None else 11
        chars = max(1, int(width/(size*25.4/72))-2)
        lines = sum(max(1, (len(line)+chars-1)//chars) for line in paragraph.text.split('\n'))
        pictures = paragraph._p.xpath('.//wp:extent')
        if paragraph.text or not pictures:
            height += lines*size*25.4/72*1.5
        if pictures:
            height += sum(int(p.get('cy'))/36000 for p in pictures) + 4
        for key in ('space_before', 'space_after'):
            spacing = getattr(paragraph.paragraph_format, key)
            if spacing is None: spacing = getattr(paragraph.style.paragraph_format, key)
            height += spacing.mm if spacing is not None else 3
    return height


def _keep_short_figure(doc, paragraphs, blocks):
    """Bounded Word keep group for one crop and its short purpose/source note.

    Word still paginates the complete document itself. The conservative height
    bound includes title, original image height, line breaks and source note;
    longer descriptions retain normal flow rather than an oversized keep chain.
    """
    if (not blocks or blocks[0]['kind'] != 'image'
            or not all(r['kind'] == 'text' and 'presentation' not in r for r in blocks[1:])
            or sum(max(1, (len(line)+37)//38) for r in blocks[1:] for line in r['value'].split('\n')) > 6):
        return
    section = doc.sections[-1]
    usable = (section.page_height-section.top_margin-section.bottom_margin)/36000
    width = (section.page_width-section.left_margin-section.right_margin)/36000
    height = 0.0
    for paragraph in paragraphs:
        sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
        size = max(sizes) if sizes else paragraph.style.font.size.pt or 11
        chars = max(1, int(width/(size*25.4/72))-2)
        lines = sum(max(1, (len(line)+chars-1)//chars) for line in paragraph.text.split('\n'))
        pictures = paragraph._p.xpath('.//wp:extent')
        if pictures:
            height += sum(int(p.get('cy'))/36000 for p in pictures) + 4
        else:
            height += lines*size*25.4/72*1.5
        for key in ('space_before', 'space_after'):
            spacing = getattr(paragraph.paragraph_format, key)
            if spacing is None: spacing = getattr(paragraph.style.paragraph_format, key)
            height += spacing.mm if spacing is not None else 3
    if height > usable-12:
        return
    for index, paragraph in enumerate(paragraphs):
        paragraph.paragraph_format.keep_together = True
        paragraph.paragraph_format.keep_with_next = index < len(paragraphs)-1


def _source_note(doc, text):
    """Keep every source field in a compact teacher-only reading note."""
    from docx.shared import Pt, RGBColor
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.paragraph_format.line_spacing = 1.0
    paragraph.paragraph_format.keep_together = True
    run = paragraph.add_run(text.replace('\n\n', '\n'))
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(75, 85, 95)


def output_root(facade, record=None):
    root = Path(facade.paths.task_root) / "preparation-v1" / "node-exports"
    if record is None:
        return root
    # record has already been validated with the containing design.
    name = record["id"]
    import re
    if not re.fullmatch(r"OUT-[a-f0-9]{32}", name):
        raise ValueError("教学成品标识无效。")
    return root / name


def export_design(facade, payload, *, cancelled=lambda: False):
    from .desktop_preparation_renderer import NativePreparationRenderer, NativePreparationRenderCancelled
    check_read_cancelled()
    plan = validate_design(payload["lesson_design"])
    if plan.get("composition", {}).get("resources"):
        return export_composition(facade, payload, cancelled=cancelled)
    c = candidate_from_design(payload)
    used = {im for n in plan["nodes"] for im in n["image_ids"]}
    assets = {x["asset_id"]: x for x in payload.get("image_assets", [])}
    image_data = {key: facade.preparation_image_bytes(assets[key]) for key in used}
    if cancelled():
        raise ReadCancelled("已取消生成，教学设计与已有成品保留。")
    root = output_root(facade)
    root.mkdir(parents=True, exist_ok=True)
    ident = "OUT-" + uuid4().hex
    target = root / ident
    with tempfile.TemporaryDirectory(prefix=".design-", dir=root) as staging:
        directory = Path(staging)
        try:
            NativePreparationRenderer().render(c, output_kind="ppt", output_dir=directory,
                                               image_data=image_data, is_cancelled=cancelled)
        except NativePreparationRenderCancelled:
            raise ReadCancelled("已取消生成，教学设计与已有成品保留。") from None
        # Do not present known-overflow slide output as ready.
        deck = json.loads((directory / "deck.json").read_text(encoding="utf-8"))
        if any(e.get("overflow") for s in deck["slides"] for e in s["elements"]):
            raise ValueError("有页面内容超出可读范围，请拆分长环节后重试；原成品保留。")
        write_documents(payload, directory, image_data)
        if cancelled():
            raise ReadCancelled("已取消生成，教学设计与已有成品保留。")
        record = {"id": ident, "fingerprint": content_fingerprint(payload), "created_at": utc_now(),
                  "files": [{"name": name, "sha256": sha256((directory / name).read_bytes()).hexdigest()}
                            for name in FILES]}
        (directory / "lesson-design.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "output.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        # Only finished bundles become discoverable. Existing versions are never overwritten.
        with publication_guard():
            if cancelled():
                raise ReadCancelled()
            directory.rename(target)
    return record


def export_composition(facade, payload, *, cancelled=lambda: False):
    """Export the measured canvas plus full lesson notes as one atomic bundle."""
    from .desktop_composition_render import layout_composition, write_pdf, write_pptx
    from .desktop_composition_activities import checked_activity_links
    from .desktop_framework_composition import checked_framework_links
    plan = validate_design(payload['lesson_design'])
    if not payload.get('topic', '').strip(): raise ValueError('请填写课题后导出。')
    links = checked_activity_links(plan, payload.get('image_assets', []))
    framework_links = checked_framework_links(plan)
    assets = {a['asset_id']: a for a in payload.get('image_assets', [])}
    used = {row['value'] for item in plan['composition']['resources'] for row in item['blocks'] if row['kind']=='image'}
    used.update(identity for n in plan['nodes'] for identity in n['image_ids'])
    if not used <= set(assets): raise ValueError('完整资源的本地图片缺失，请重新关联后导出。')
    image_data = {identity: facade.preparation_image_bytes(assets[identity]) for identity in used}
    teacher = layout_composition(plan['composition'], list(assets.values()), image_data, student=False)
    student = layout_composition(plan['composition'], list(assets.values()), image_data, student=True)
    from .desktop_question_answer_reveal import classroom_answer_reveals
    answer_reveals = classroom_answer_reveals(plan['composition'])
    classroom = (layout_composition(plan['composition'], list(assets.values()), image_data, student=True, include_reveals=True)
                 if plan['composition'].get('click_reveals') or answer_reveals else student)
    if teacher['errors'] or student['errors'] or classroom['errors']:
        raise ValueError('\n'.join(dict.fromkeys(teacher['errors']+student['errors']+classroom['errors'])))
    if cancelled(): raise ReadCancelled('已取消，作品与旧成品保留。')
    root = output_root(facade); root.mkdir(parents=True, exist_ok=True)
    ident = 'OUT-' + uuid4().hex; target = root / ident
    with tempfile.TemporaryDirectory(prefix='.composition-', dir=root) as staging:
        directory = Path(staging)
        write_pdf(teacher, directory/'lesson_canvas.pdf')
        write_pdf(student, directory/'student_canvas.pdf')
        write_pptx(teacher, directory/'lesson_presentation.pptx', image_data)
        write_pptx(classroom, directory/'student_presentation.pptx', image_data)
        write_documents(payload, directory, image_data)
        if cancelled(): raise ReadCancelled('已取消，作品与旧成品保留。')
        record = {'id':ident, 'fingerprint':content_fingerprint(payload), 'created_at':utc_now(),
                  'files':[{'name':name,'sha256':sha256((directory/name).read_bytes()).hexdigest()} for name in COMPOSITION_FILES]}
        (directory/'lesson-design.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        (directory/'output.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        # Explicit geometry, ordered block identities and audience coverage make
        # the exported files reviewable without pretending Office was rendered.
        from .desktop_lesson_composition import RENDER_REVISION
        audit = {'schema':'shchem.composition-output.v1', 'fingerprint':record['fingerprint'], 'render_revision':RENDER_REVISION,
                 'format':plan['composition']['format'], 'page_ids':plan['composition']['pages'],
                 'page_ids_by_audience': {'teacher': list(teacher['pages']), 'student': list(student['pages'])},
                 'student_omitted_teacher_page_ids': student['omitted_teacher_page_ids'],
                 'student_arrangement':plan['composition'].get('student_arrangement'),
                 'student_independent_arrangement':student['independent_student_arrangement'],
                 'pptx_physical_render_verified':False,
                 'canvas_scope':'composition resources and explicitly selected lesson activities; remaining activities are included in lesson_plan.docx',
                  'activity_links':links,
                  'framework_links':framework_links,
                 'resources':[{'id':r['id'],'original_sha256':r['original_sha256']} for r in plan['composition']['resources']]}
        for name, layout in (('teacher',teacher),('student',student)):
            from .desktop_composition_rich_render import flatten_elements
            audit[name] = {page:[{k:v for k,v in e.items() if not k.startswith('_')} for e in flatten_elements(elements)]
                           for page,elements in layout['pages'].items()}
        if plan['composition'].get('click_reveals') or answer_reveals:
            audit['classroom_click_reveals'] = plan['composition'].get('click_reveals', [])
            audit['classroom_answer_reveals'] = answer_reveals
            audit['classroom_page_ids'] = list(classroom['pages'])
            audit['classroom'] = {page:[{k:v for k,v in e.items() if not k.startswith('_')} for e in flatten_elements(elements)]
                                  for page,elements in classroom['pages'].items()}
        (directory/'canvas-output.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        with publication_guard():
            if cancelled(): raise ReadCancelled()
            directory.rename(target)
    return record


def checked_file(facade, plan, export_id, name):
    plan = validate_design(plan)
    record = next((r for r in plan["exports"] if r["id"] == export_id), None)
    if record is None:
        raise ValueError("未找到这份成品记录。")
    file = next((f for f in record["files"] if f["name"] == name), None)
    if file is None:
        raise ValueError("该文件不属于已记录的教学成品。")
    path = output_root(facade, record) / name
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != file["sha256"]:
        raise ValueError("原成品缺失或已被外部修改，请重新生成或保留外部修订稿。")
    return path


def actual_ppt_preview(facade, plan, export_id, *, name="lesson_presentation.pptx"):
    """Convert the *actual* PPTX, separately from the renderer's quick PNGs."""
    from .desktop_local_pagination import find_libreoffice
    from .office_conversion import libreoffice_pdf, powerpoint_available, powerpoint_pdf
    check_read_cancelled()
    if name not in ("lesson_presentation.pptx", "student_presentation.pptx"):
        raise ValueError("请选择教师或学生课件。")
    source = checked_file(facade, plan, export_id, name)
    source_sha = sha256(source.read_bytes()).hexdigest()
    office = find_libreoffice()
    if office is None and not powerpoint_available():
        raise ValueError("实际PPTX预览需本机PowerPoint或LibreOffice；原成品保留。")
    target = source.parent / ("actual-pptx-" + uuid4().hex)
    target.mkdir(exist_ok=False)
    if office is not None:
        pdf = libreoffice_pdf(source, target, office, export_filter="impress_pdf_Export")
        engine = "LibreOffice Impress"
    else:
        pdf = target / (source.stem + ".pdf")
        powerpoint_pdf(source, pdf)
        engine = "Microsoft PowerPoint"
    check_read_cancelled()
    if sha256(checked_file(facade, plan, export_id, source.name).read_bytes()).hexdigest() != source_sha:
        raise ValueError("转换期间原PPTX已变化，未发布本次预览。")
    receipt = {"path": str(pdf), "engine": engine,
               "document_kind": "presentation", "document_name": name,
               "export_id": export_id, "presentation_name": name,
               "audience": "student" if name == "student_presentation.pptx" else "teacher",
               "source_sha256": source_sha,
               "pdf_sha256": sha256(pdf.read_bytes()).hexdigest()}
    with publication_guard():
        (target / "preview.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return receipt


def actual_word_preview(facade, plan, export_id):
    """Render the registered Word lesson plan with a local document converter."""
    from .desktop_local_pagination import find_libreoffice
    from .office_conversion import libreoffice_pdf, word_pdf, OfficeConversionError
    check_read_cancelled()
    source = checked_file(facade, plan, export_id, 'lesson_plan.docx')
    source_sha = sha256(source.read_bytes()).hexdigest()
    target = source.parent / ('actual-word-' + uuid4().hex)
    target.mkdir(exist_ok=False)
    office = find_libreoffice()
    try:
        if office is not None:
            pdf = libreoffice_pdf(source, target, office, export_filter='writer_pdf_Export')
            engine = 'LibreOffice Writer'
        else:
            pdf = target / (source.stem + '.pdf')
            word_pdf(source, pdf)
            engine = 'Microsoft Word'
    except OfficeConversionError as exc:
        raise ValueError('教案 Word 预览转换未完成：' + str(exc)) from exc
    check_read_cancelled()
    if sha256(checked_file(facade, plan, export_id, source.name).read_bytes()).hexdigest() != source_sha:
        raise ValueError('转换期间教案文件已变化，请重新生成预览。')
    receipt = {'path': str(pdf), 'engine': engine, 'export_id': export_id,
               'document_kind': 'word', 'document_name': source.name,
               'source_sha256': source_sha, 'pdf_sha256': sha256(pdf.read_bytes()).hexdigest()}
    with publication_guard():
        check_read_cancelled()
        (target / 'preview.json').write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
    return receipt


def _remove_owned_copy(folder, parent):
    # This folder was just created by mkdtemp; never follow it outside its parent.
    if folder.resolve().parent == parent.resolve() and not folder.is_symlink():
        shutil.rmtree(folder, ignore_errors=True)


def copy_primary_bundle(facade, plan, export_id, destination):
    """Export exactly the selected Word plan and classroom PPT as editable copies."""
    plan = validate_design(plan)
    parent = Path(destination).resolve()
    if not parent.is_dir():
        raise ValueError('请选择已经存在的导出文件夹。')
    record = next((row for row in plan['exports'] if row['id'] == export_id), None)
    if record is None:
        raise ValueError('未找到这份成品记录。')
    names = primary_output_names(record)
    from docx import Document
    worksheet=checked_file(facade,plan,export_id,'student_worksheet.docx')
    if Document(worksheet).core_properties.category=='shchem.classroom-notes.v1':names=names+('student_worksheet.docx',)
    sources = [checked_file(facade, plan, export_id, name) for name in names]
    expected = {f['name']: f['sha256'] for f in record['files']}
    check_read_cancelled()
    folder = Path(tempfile.mkdtemp(prefix='教案与课堂PPT-' + export_id[-8:] + '-', dir=parent))
    try:
        for source, alias in zip(sources, ('教案.docx', '课堂演示.pptx','课堂讲义.docx')):
            check_read_cancelled()
            target = folder / alias
            shutil.copyfile(source, target)
            if sha256(target.read_bytes()).hexdigest() != expected[source.name]:
                raise ValueError('复制时成品文件发生变化，请重新导出。')
        for source in sources:
            checked_file(facade, plan, export_id, source.name)
        with publication_guard():
            check_read_cancelled()
    except Exception:
        _remove_owned_copy(folder, parent)
        raise
    return str(folder)


def copy_output_bundle(facade, plan, export_id, destination):
    """Export the selected version to a new teacher-owned folder, never in-place."""
    plan = validate_design(plan)
    parent = Path(destination).resolve()
    if not parent.is_dir():
        raise ValueError("请选择已经存在的导出文件夹。")
    record = next(row for row in plan["exports"] if row["id"] == export_id)
    sources = {name: checked_file(facade, plan, export_id, name) for name in output_names(record)}
    folder = Path(tempfile.mkdtemp(prefix="教学成品-" + export_id[-8:] + "-", dir=parent))
    try:
        for name, source in sources.items():
            check_read_cancelled()
            shutil.copyfile(source, folder / name)
            expected = next(f["sha256"] for f in record["files"] if f["name"] == name)
            if sha256((folder / name).read_bytes()).hexdigest() != expected:
                raise ValueError("复制时成品发生变化，请重新核对后导出。")
        (folder / "OUTPUT-MANIFEST.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "使用说明.txt").write_text(
            "本文件夹是所选教学成品版本的独立副本。\n"
            "lesson_plan.docx：教案，含教师答案及备注。\n"
            "lesson_presentation.pptx：教师课件，编排作品的正文含教师答案；其他设计的讲者备注可能含答案。\n"
            "student_worksheet.docx：学生学习单。\n"
            "编排作品另含lesson_canvas.pdf（教师版）、student_canvas.pdf及student_presentation.pptx（学生版）。\n"
            "所选教学环节与画布素材一起用于PDF、课件和学习单；未加入画布的环节仍保存在教案中。Word与PPTX可在Office中编辑。\n"
            "在Office中修改这些副本不会回写教学环节，也不改变工作台中保留的原版本。\n",
            encoding="utf-8")
        with publication_guard():
            check_read_cancelled()
    except Exception:
        _remove_owned_copy(folder, parent)
        raise
    return str(folder)
