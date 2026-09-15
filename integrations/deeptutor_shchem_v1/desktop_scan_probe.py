"""Synthetic source/EXE acceptance, deliberately isolated from teacher materials."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


def run_probe(output):
    from PIL import Image, ImageDraw, ImageFont, ImageChops
    from docx import Document
    from docx.shared import Inches
    from .desktop_facade import build_default_facade
    from .desktop_paths import DesktopPaths
    from .desktop_version import DESKTOP_VERSION
    from .desktop_raster_numbers import render_numbered_image
    from .desktop_raster_paper_service import RasterPaperService
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    app=create_application(['scan193-probe'])
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    screenshots=[]
    def capture(widget,name):
        path=output/name;assert widget.grab().save(str(path))
        screenshots.append({'file':name,'sha256':sha256(path.read_bytes()).hexdigest()})
    def settle(predicate=lambda:True):
        end=time.monotonic()+90
        for _ in range(8):app.processEvents();time.sleep(.03)
        while not predicate() and time.monotonic()<end:app.processEvents();time.sleep(.03)
        assert predicate(), 'UI did not finish requested operation'
    with tempfile.TemporaryDirectory(prefix='scan193-') as temp:
        temp=Path(temp)
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        facade=build_default_facade(DesktopPaths.from_workspace(root,state_root=temp/'state'))
        win=TeacherWorkbenchWindow(facade);win.resize(1280,900);win.show()
        try:
            doc=Document();raw_images={};new_numbers={}
            try:font=ImageFont.truetype('arial.ttf',38)
            except OSError:font=ImageFont.load_default(size=38)
            for number,new in ((27,2),(38,1)):
                doc.add_paragraph(f'【例{number}】合成扫描题：看图作答，数据保持12.5。')
                for answer in (False,True):
                    if answer:doc.add_paragraph(f'【答案】{number}. 合成答案。')
                    image=Image.new('RGB',(800,240),'white');draw=ImageDraw.Draw(image)
                    draw.text((12,12),str(number)+'.',font=font,fill='black')
                    draw.text((110,12),'12.5 mol/L' + (' answer' if answer else ' question'),font=font,fill='black')
                    draw.line((100,125,700,125),width=2,fill='black')
                    data=BytesIO();image.save(data,format='PNG');data=data.getvalue()
                    key=sha256(data).hexdigest();raw_images[key]=data;new_numbers[key]=new
                    doc.add_picture(BytesIO(data),width=Inches(5.5))
            source=temp/'合成扫描题.docx';doc.save(source);source_bytes=source.read_bytes()
            facade.save_visual_import_batch(handout_files=(source,),source_type='教师讲义')
            rows=[r for r in facade.word_question_catalog()['items'] if r['source_name']==source.name]
            assert len(rows)==2
            facade.add_word_questions_to_basket([{'key':r['key'],'revision':r['revision']} for r in reversed(rows)])
            original_state=deepcopy(facade.basket())
            win.navigate('paper');settle(lambda:win.paper_page._mixed_panel is not None and not win.paper_page._mixed_panel._busy)
            panel=win.paper_page._mixed_panel
            assert panel.number_button.isEnabled();panel.number_button.click()
            settle(lambda:panel._number_dialog is not None or not panel._busy)
            dialog=panel._number_dialog;assert dialog is not None, panel.status.text()
            assert len(dialog.images)==4
            for index,image in enumerate(dialog.images):
                dialog.picker.setCurrentIndex(index)
                dialog.canvas.selection=[8,8,97,62]
                dialog.number.setValue(new_numbers[image['image_sha256']])
                dialog._add_region()
            assert len(dialog.edits)==4
            edits=deepcopy(dialog.edits)
            dialog.undo.click();assert len(dialog.edits)==3
            last=edits[-1];dialog.picker.setCurrentIndex(next(i for i,r in enumerate(dialog.images) if r['image_sha256']==last['image_sha256']))
            dialog.canvas.selection=list(last['box']);dialog.number.setValue(last['number']);dialog._add_region()
            assert dialog.edits==edits
            capture(dialog,'scan-number-editor.png')
            dialog.original.setChecked(True);capture(dialog,'scan-number-original.png');dialog.original.setChecked(False)
            dialog.apply.click()
            settle(lambda:panel._preview_dialog is not None or not panel._busy)
            assert panel._preview_dialog is not None, panel.status.text()
            preview=panel._preview
            assert preview.preview_model['scan_numbering']['edits']==edits
            service=RasterPaperService(facade)
            for role in ('student','teacher'):
                for page in preview.preview_model['pagination']['documents'][role]['pages']:
                    data=service.image(preview.preview_id,page['image_id'])['data']
                    path=output/f'scan-{role}-page-{page["page_number"]}.png';path.write_bytes(data)
                    screenshots.append({'file':path.name,'sha256':sha256(data).hexdigest()})
            service.approve(preview.preview_id,preview.preview_hash)
            exported=service.export(preview.preview_id,preview.preview_hash)
            for artifact in exported['artifacts']:
                shutil.copyfile(artifact['path'],output/Path(artifact['path']).name)
            assert source.read_bytes()==source_bytes and facade.basket()==original_state
            for edit in edits:
                raw=raw_images[edit['image_sha256']]
                changed=render_numbered_image(raw,[edit])
                a=Image.open(BytesIO(raw)).convert('RGB');b=Image.open(BytesIO(changed)).convert('RGB')
                delta=ImageChops.difference(a,b);assert delta.getbbox()
                delta.paste((0,0,0),tuple(edit['box']));assert delta.getbbox() is None
            report={'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
                    'frozen':getattr(sys,'frozen',False),'qt_platform':app.platformName(),
                    'edits':len(edits),'raw_word_import':True,'native_entry_opened':True,'undo_exercised':True,
                    'two_audiences_exported':True,'outside_regions_unchanged':True,'source_unchanged':True,
                    'pages':{r:preview.preview_model['pagination']['documents'][r]['page_count'] for r in ('student','teacher')},
                    'screenshots':screenshots,'model_calls':0,'scope':'Synthetic Word-hosted raster images; manual pixel selection, no OCR or real-library acceptance.'}
            (output/'scan-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            win.close();app.processEvents()
    return 0
