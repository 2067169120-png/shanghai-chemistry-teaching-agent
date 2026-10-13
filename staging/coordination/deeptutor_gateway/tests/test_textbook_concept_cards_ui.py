"""Native selection, bounded original reading and explicit canvas delivery."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_textbook_concept_cards import concept as concept,section_fixture as section_fixture,asset_fixture as asset_fixture,shelf_fixture as shelf_fixture
from test_textbook_bookshelf_ui import page as shelf_page,app as app,painted,select_and_preview
from test_question_explorer_ui import window as window
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_note_picker import KnowledgeNotePicker
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_knowledge_note


def test_picker_content_filters_and_bounded_reading_are_read_only(app,concept):
    f=concept;row=deepcopy(f.option)
    note={**row,'origin':'registered','title':'合成研读提示','note_id':'separate-note',
          'section_key':row['section_key'],'revision':'a'*64,'directory_revision':'b'*64}
    note.pop('selection')
    bridge=_ManualBridge();picker=KnowledgeNotePicker(f.facade,[row,note],tasks=bridge)
    picker.resize(1100,850);picker.show();app.processEvents()
    assert picker.list.count()==2 and picker.chosen is None
    picker.content_type.setCurrentIndex(picker.content_type.findData('textbook_concept'))
    assert picker.list.count()==1 and '知识摘要' in picker.list.item(0).text()
    picker.read_original();bridge.succeed(bridge.take('核对知识卡原页'),run=True)
    reader=picker._reader
    assert painted(app,reader)>10 and reader.reading_mode=='concept_card'
    assert [reader.pages.itemData(i) for i in range(reader.pages.count())]==[7,8,10]
    assert not reader.excerpt_button.isVisible() and reader.isWindow() is False
    reader.pages.setCurrentIndex(reader.pages.findData(8));app.processEvents()
    reader._step_page(1);app.processEvents()
    assert reader.view.pageNavigator().currentPage()+1==10 and not reader.next.isEnabled()
    reader._jump(44);assert reader.view.pageNavigator().currentPage()+1==10
    assert picker.chosen is None and not f.facade.paths.task_root.exists()
    picker.content_type.setCurrentIndex(picker.content_type.findData('registered'))
    assert picker.list.count()==1 and '研读提示' in picker.list.item(0).text()
    picker.reject();picker.deleteLater();app.processEvents()


def test_bookshelf_cards_require_preview_and_explicit_click(shelf_page):
    page,bridge,f=shelf_page
    page.facade.personal_textbook_study=lambda:SimpleNamespace(books=lambda:[])
    cards=[];references=[]
    page.preparation_cards_requested.connect(cards.append)
    page.preparation_reference_requested.connect(references.append)
    assert not page.cards_button.isEnabled()
    select_and_preview(page,bridge)
    assert not cards and not references and page.cards_button.isEnabled()
    QTest.mouseClick(page.cards_button,Qt.MouseButton.LeftButton)
    bridge.succeed(bridge.take('核对教材备课参考'),run=True)
    assert len(cards)==1 and len(cards[0]['resources'])==1 and references==[]
    assert cards[0]['resources'][0]['original']['evidence']['record_id']==f.row['concept_id']
    page.cards_appended();assert not page.cards_button.isEnabled()
    page.knowledge_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not page.cards_button.isEnabled() and not page.confirm_button.isEnabled()


def test_changed_source_after_preview_cannot_deliver_cards(shelf_page):
    page,bridge,f=shelf_page
    page.facade.personal_textbook_study=lambda:SimpleNamespace(books=lambda:[])
    cards=[];page.preparation_cards_requested.connect(cards.append)
    select_and_preview(page,bridge)
    f.row['statement']+='变化';f.catalog.write_text(__import__('json').dumps(f.row),encoding='utf8')
    page.cards_button.click();pending=bridge.take('核对教材备课参考')
    try:pending.operation()
    except ValueError as exc:bridge.fail(pending,str(exc))
    else:pytest.fail('stale source delivered cards')
    assert not cards and not page.cards_button.isEnabled() and page._reference is None


def test_main_window_canvas_handoff_preserves_existing_materials_and_deduplicates(window,concept,monkeypatch):
    win,app=window;page=win.preparation_page
    page.topic.setText('合成课题');page.materials.setPlainText('既有备课资料')
    card=import_knowledge_note(concept.facade,concept.option['selection'])
    opened=[]
    def inspect(dialog):
        opened.append(deepcopy(dialog.composition_panel.composition()))
        assert dialog.tabs.currentWidget() is dialog.composition_panel
        dialog.reject();return 0
    monkeypatch.setattr(LessonDesignDialog,'exec',inspect)
    win.textbook_page.preparation_cards_requested.emit({'resources':[card]})
    assert len(opened)==1 and page._lesson_design['composition']['resources']==[card]
    assert page.materials.toPlainText()=='既有备课资料' and page.topic.text()=='合成课题'
    win.textbook_page.preparation_cards_requested.emit({'resources':[card]})
    assert len(opened)==1 and len(page._lesson_design['composition']['resources'])==1
    before=deepcopy(page._payload());page._lesson_busy=True
    assert page.import_textbook_cards({'resources':[card]}) is False and page._payload()==before
    page._lesson_busy=False
