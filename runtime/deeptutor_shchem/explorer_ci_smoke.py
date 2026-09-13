"""End-to-end faceted picking, durable basket and real Office/PDF preview."""
from __future__ import annotations
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')


def main():
    from PySide6.QtCore import Qt,qVersion
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION
    from explorer_demo_data import seed_demo
    output=ROOT/'explorer-qa';output.mkdir(exist_ok=True)
    app=create_application(['explorer-smoke'])
    errors=[];images=[]
    def error(kind,value,tb):
        errors.append(str(value));traceback.print_exception(kind,value,tb)
    sys.excepthook=error
    def settle(predicate=lambda:True,timeout=30):
        end=time.monotonic()+timeout
        for _ in range(8):app.processEvents();time.sleep(.05)
        while not predicate() and time.monotonic()<end:app.processEvents();time.sleep(.05)
        assert predicate(), 'Qt action did not complete'
        assert not errors,errors
    def capture(widget,name):
        settle();image=widget.grab();path=output/(name+'.png');assert image.save(str(path))
        images.append({'file':path.name,'width':image.width(),'height':image.height(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    with tempfile.TemporaryDirectory(prefix='shchem-explorer-') as tmp:
        base=Path(tmp);facade=seed_demo(base/'workspace',base/'state')
        win=TeacherWorkbenchWindow(facade);win.resize(1440,980);win.show();win.navigate('library')
        win.setWindowTitle('沪上化学智研台 · 合成资料演示，非真实考试')
        page=win.library_page
        try:
            page.scope.setCurrentIndex(page.scope.findData('word_native'))
            settle(lambda:not page._loading and len(page.cards)==6 and page.cards[0].ready)
            capture(win,'selection-all')
            # Select real saved knowledge and source-type labels in the UI.
            for group, value in [('knowledge','K09'),('exam','second_mock')]:
                found=False
                for i in range(page.tree.topLevelItemCount()):
                    parent=page.tree.topLevelItem(i)
                    for j in range(parent.childCount()):
                        item=parent.child(j)
                        if item.data(0,Qt.ItemDataRole.UserRole)==(group,value):
                            item.setCheckState(0,Qt.CheckState.Checked);found=True;break
                assert found,(group,value)
                settle(lambda:not page._search_timer.isActive() and not page._loading and bool(page.cards) and page.cards[0].ready)
            assert len(page.cards)==2
            for i in (0,1):
                if page._active_card is not page.cards[i]:page.expand(page.cards[i])
                settle(lambda:page.cards[i].ready)
                page.cards[i].add.click();settle(lambda:len(facade.basket())==i+1)
            page.expand(page.cards[0]);settle(lambda:page.cards[0].ready)
            capture(win,'selection-filtered')
            basket=ExplorerBasketDialog(facade,win);basket.show();settle()
            basket.down.click();assert len(facade.basket())==2
            capture(basket,'selection-basket')
            basket.close();basket.deleteLater()
            # Actual composer writes DOCX -> installed Office -> PDF -> page images.
            page.preview_button.click()
            settle(lambda: win.paper_page._mixed_panel is not None and win.paper_page._mixed_panel._preview_dialog is not None,timeout=240)
            panel=win.paper_page._mixed_panel;dialog=panel._preview_dialog
            settle(lambda:('student',1) in dialog._pixmaps)
            assert panel._preview.preview_model['pagination']['status']=='rendered_pending_review'
            capture(dialog,'selection-pagination-student')
            dialog.tabs.setCurrentIndex(1);settle(lambda:('teacher',1) in dialog._pixmaps)
            capture(dialog,'selection-pagination-teacher')
            versions=panel._preview.preview_model['pagination']['documents']
            counts={k:len(v['pages']) for k,v in versions.items()}
            dialog.close()
            win.navigate('library');win.resize(800,700);settle()
            capture(win,'selection-compact')
            import xml.etree.ElementTree as ET
            junit=ET.parse(output/'pytest.xml').getroot()
            suites=list(junit.iter('testsuite'))
            tests={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
            report={'version':DESKTOP_VERSION,'source_commit':os.environ.get('SHCHEM_SOURCE_SHA',os.environ.get('GITHUB_SHA','local')),
                    'platform':platform.platform(),'python':platform.python_version(),'qt':qVersion(),'tests':tests,
                    'screenshots':images,'uncaught_errors':errors,'real_pagination_pages':counts,
                    'workflow_run':os.environ.get('GITHUB_RUN_ID','local'),
                    'scope':'Actual isolated Word import, saved synthetic tags, native facet checkboxes, durable basket, real LibreOffice/Word DOCX-to-PDF pagination. No private materials, model calls or full chemical/production EXE acceptance.'}
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            win.close();app.processEvents()
    return 0
if __name__=='__main__':raise SystemExit(main())
