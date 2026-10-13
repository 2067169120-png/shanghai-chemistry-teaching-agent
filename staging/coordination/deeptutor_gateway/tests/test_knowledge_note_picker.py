"""Filter intersections and original viewing cannot select or mutate a lesson."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from test_desktop_library_ui import _ManualBridge, qt_app as qt_app
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_bookshelf_ui import facade_for, painted
from test_textbook_reading_hints import note as hint, write_packet
from test_personal_textbook_study import book_fixture as book_fixture, chapter, note
from test_composition_inline_personal import personal_facade

from integrations.deeptutor_shchem_v1.desktop_composition_sources import knowledge_options, knowledge_reader_selection
from integrations.deeptutor_shchem_v1.desktop_workbench import knowledge_note_picker as picker_module
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_note_picker import KnowledgeNotePicker


def rows():
    return [{'origin':'registered','book_key':book,'section_filter_key':section,
        'volume_id':book,'volume_title':book+'册','section_key':section,'section_title':section+'节',
        'source_name':'合成教材.pdf','note_id':str(i),'revision':'a'*64,'directory_revision':'b'*64,
        'title':title,'summary':'合成知识内容 '+title,'pdf_pages':[7+i],'printed_pages':[3+i],
        'limitation':'合成测试内容，待核对。'}
        for i,(book,section,title) in enumerate([('甲','甲一','比例'),('甲','甲二','条件'),('乙','乙一','比例条件')])]


@pytest.fixture
def chooser(qt_app):
    bridge=_ManualBridge();value=KnowledgeNotePicker(SimpleNamespace(),rows(),tasks=bridge)
    value.show();qt_app.processEvents()
    yield value,bridge,qt_app
    value.reject();value.deleteLater();qt_app.processEvents()


def test_book_section_search_intersect_and_empty_result_cannot_confirm(chooser):
    value,bridge,app=chooser
    assert value.list.count()==3 and not bridge.pending and value.chosen is None
    value.book.setCurrentIndex(value.book.findData('甲'));assert value.list.count()==2
    value.section.setCurrentIndex(value.section.findData('甲二'));assert value.list.count()==1
    assert '甲册 · 甲二节' in value.heading.text()
    assert value.detail.toPlainText().index('PDF文件页')<value.detail.toPlainText().index('合成知识内容')
    value.search.setText('比例');assert value.list.count()==0
    assert not value.confirm.isEnabled() and not value.read_button.isEnabled()
    value.choose();assert value.chosen is None and not value._closed
    value.search.clear();value.book.setCurrentIndex(value.book.findData('乙'))
    assert value.section.currentData() is None and value.list.count()==1
    assert not bridge.pending


@pytest.mark.parametrize('width',[600,1100])
def test_desktop_layout_keeps_filter_and_confirmation_inside_window(chooser,width):
    value,_,app=chooser;value.resize(width,800);app.processEvents()
    assert value.width()==width
    assert value.split.orientation()==(Qt.Orientation.Vertical if width<850 else Qt.Orientation.Horizontal)
    for widget in (value.book,value.section,value.search,value.confirm,value.read_button):
        pos=widget.mapTo(value,widget.rect().topLeft())
        assert pos.x()>=0 and pos.y()>=0 and pos.x()+widget.width()<=value.width()
        assert pos.y()+widget.height()<=value.height()


@pytest.mark.parametrize('change',['note','close','accept'])
def test_pending_old_original_callback_is_ignored(chooser,change,monkeypatch):
    value,bridge,app=chooser
    monkeypatch.setattr(picker_module,'TextbookSourceDialog',lambda *a,**k:pytest.fail('late callback opened an original'))
    value.read_original();pending=bridge.take('核对知识卡原页')
    assert not value.read_button.isEnabled() and value.chosen is None
    if change=='note':value.list.setCurrentRow(1)
    elif change=='close':value.reject()
    else:value.choose()
    assert pending.task_id in bridge.cancelled
    bridge.succeed(pending,{'selection':{},'reading_mode':'section'})
    assert value._reader is None
    assert (value.chosen is not None)==(change=='accept')


def test_failed_original_keeps_filter_and_requires_explicit_confirmation(chooser):
    value,bridge,_=chooser;value.book.setCurrentIndex(value.book.findData('甲'))
    value.read_original();bridge.fail(bridge.take('核对知识卡原页'),'来源已变化，请刷新后重选。')
    assert value.read_button.isEnabled() and value._reader is None and value.chosen is None
    assert value.book.currentData()=='甲' and '已变化' in value.status.text()


def test_actual_registered_note_and_pdf_reading_is_read_only(qt_app,shelf_fixture):
    f=shelf_fixture
    write_packet(f.root,[hint(volume_id=f.row['volume_id'],section_key=f.row['section_key'],pdf_pages=[8,9],
        printed_pages=[4,5],textbook_evidence={'source_sha256':f.row['source_sha256'],
        'pdf_pages':[8,9],'printed_pages':[4,5]})])
    facade=facade_for(f);facade.personal_textbook_study=lambda:SimpleNamespace(books=lambda:[])
    before=disk_bytes(f.root);options=knowledge_options(facade)['reading_notes'];assert len(options)==1
    assert options[0]['section_title']=='1.1 合成第1.1节'
    section=next(s for s in f.shelf.snapshot()['sections'] if s['section_key']==f.row['section_key'])
    assert options[0]['volume_title']==section['volume_title']
    bridge=_ManualBridge();value=KnowledgeNotePicker(facade,options,tasks=bridge);value.show()
    value.read_original();bridge.succeed(bridge.take('核对知识卡原页'),run=True)
    reader=value._reader
    assert reader is not None and not reader.isWindow()
    assert painted(qt_app,reader)>10 and reader.pages.currentData()==8
    assert reader.concept['revision']==options[0]['directory_revision']
    assert value.chosen is None and value.list.count()==1
    assert disk_bytes(f.root)==before
    value.reject();assert reader._closed and reader._source is None
    value.deleteLater();qt_app.processEvents();assert disk_bytes(f.root)==before


def test_registered_stale_note_is_blocked_before_original_open(shelf_fixture):
    f=shelf_fixture
    row=hint(volume_id=f.row['volume_id'],section_key=f.row['section_key'],pdf_pages=[8],printed_pages=[4],
        textbook_evidence={'source_sha256':f.row['source_sha256'],'pdf_pages':[8],'printed_pages':[4]})
    write_packet(f.root,[row]);facade=facade_for(f);facade.personal_textbook_study=lambda:SimpleNamespace(books=lambda:[])
    option=knowledge_options(facade)['reading_notes'][0]
    write_packet(f.root,[{**row,'summary':'版本已修改'}])
    with pytest.raises(ValueError):knowledge_reader_selection(facade,option)


def test_personal_book_original_identity_and_stale_study_are_preserved(book_fixture):
    f=book_fixture;view=note(f,chapter(f));facade=personal_facade(f)
    option=knowledge_options(facade)['reading_notes'][0];before=f.state.path.read_bytes()
    # Display fields cannot redirect the version-bound original to another page or section.
    option['section_id']='forged';option['pdf_pages']=[999]
    value=knowledge_reader_selection(facade,option)
    assert value=={'reading_mode':'book','selection':{**f.selection,
        'section_id':view['notes'][0]['section_id'],'study_revision':view['study_revision'],'initial_pdf_page':2}}
    assert f.state.path.read_bytes()==before and f.source.read_bytes()==f.original
    f.study.save_note(f.selection,view['study_revision'],section_id=view['sections'][0]['section_id'],
        title='第二条',text='合成变化',pdf_page=3,teacher_confirmed=True)
    with pytest.raises(ValueError):knowledge_reader_selection(facade,option)


def test_unknown_metadata_is_displayed_without_guessing_a_section():
    row=rows()[0]
    facade=SimpleNamespace(textbook_bookshelf=lambda:{'reading_notes':[row],'sections':[]},
        personal_textbook_study=lambda:SimpleNamespace(books=lambda:[]))
    result=knowledge_options(facade)['reading_notes'][0]
    assert result['volume_title']=='册名待核对' and result['section_title']=='章节待核对'
    assert result['section_key']==row['section_key'] and result['revision']==row['revision']
