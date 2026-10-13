"""Crop original-page viewing keeps the current lesson and ignores late tasks."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

from test_step_flow_entry_ui import desk as desk
from test_composition_render import app as app
from test_textbook_crop_reading import book_fixture as book_fixture, personal
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_crop_source_dialog import TextbookCropSourceDialog


class FakeDialog:
    opened = []
    action = None

    def __init__(self, facade, item, assets, context, parent, *, current):
        self.value = deepcopy((item, assets, context)); self.current = current
        self.deleted = False; self.__class__.opened.append(self)

    def exec(self):
        if self.__class__.action:self.__class__.action(self)
        return QDialog.DialogCode.Rejected

    def deleteLater(self):self.deleted = True


def setup(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_crop_source_dialog as module
    owner, panel, window, assets = desk
    item = deepcopy(panel.composition()['resources'][0]); panel.selected = item['id']
    panel.frame_index = 0; panel.refresh(); window.show()
    context = {'selection':{'visual_asset_id':'SYNTHETIC-EXACT-PAGE','revision':'b'*64},
               'reading_mode':'asset','binding_sha256':'c'*64}
    calls = []
    owner.facade.preparation_textbook_crop_original = lambda item, assets: (
        calls.append(deepcopy((item,assets))) or deepcopy(context))
    monkeypatch.setattr(owner, 'run', lambda _title, operation, done, **kwargs: done(operation()), raising=False)
    FakeDialog.opened = []; FakeDialog.action = None
    monkeypatch.setattr(module, 'TextbookCropSourceDialog', FakeDialog)
    return owner, panel, window, assets, item, context, calls


def snapshot(owner, panel, assets):
    return deepcopy((owner.history.value, owner.history.back, owner.history.forward,
                     assets[0], panel.payload, panel.selected, panel.frame_index,
                     panel._view_page_id, panel.student.isChecked()))


def test_selected_crop_visible_button_reads_exact_source_and_cancel_changes_nothing(desk, monkeypatch, app):
    owner, panel, window, assets, item, context, calls = setup(desk, monkeypatch)
    app.processEvents(); assert panel.knowledge_read_button.isVisible()
    before = snapshot(owner,panel,assets)
    panel.knowledge_read_button.click();app.processEvents()
    assert calls == [(item,assets[0])]
    assert FakeDialog.opened[0].value == (item,assets[0],context)
    assert FakeDialog.opened[0].deleted and snapshot(owner,panel,assets)==before
    panel.selected = panel.composition()['resources'][1]['id'];panel.refresh()
    assert not panel.knowledge_read_button.isVisible()


@pytest.mark.parametrize('phase',['before_open','after_reading'])
@pytest.mark.parametrize('change',['history','assets','selection','stopping','closing'])
def test_target_changes_prevent_late_open_and_never_rewrite_workpiece(desk,monkeypatch,phase,change):
    owner,panel,window,assets,item,context,calls=setup(desk,monkeypatch)
    pending=[]
    owner.run=lambda _title,operation,done,**kwargs:pending.append((operation,done))
    expected=[]
    def changed():
        if change=='history':
            value=deepcopy(owner.history.value);value['nodes'][0]['teacher_action']='刚改过的教学动作'
            owner.history.put(value);owner.sync_page()
        elif change=='assets':assets[0][0]['caption']='刚修改的图片题名';owner.sync_page()
        elif change=='selection':panel.selected=None;panel.refresh()
        elif change=='stopping':owner._stopping=True
        else:owner._close_when_finished=True
        expected.append(snapshot(owner,panel,assets))
    FakeDialog.action=lambda _dialog:changed() if phase=='after_reading' else None
    panel.read_selected_textbook_original(); result=pending[0][0]()
    if phase=='before_open':changed()
    pending[0][1](result)
    assert len(FakeDialog.opened)==(0 if phase=='before_open' else 1)
    assert snapshot(owner,panel,assets)==expected[0]
    if change in {'stopping','closing'}:
        assert owner.status.text()=='选材窗口已关闭或停止，本批未加入。'
    else:
        assert '阅读期间变化' in owner.status.text()


def test_source_error_preserves_current_selection_and_full_payload(desk,monkeypatch):
    owner,panel,window,assets,item,context,calls=setup(desk,monkeypatch)
    pending=[];owner.run=lambda _title,operation,done,**kwargs:pending.append((operation,done))
    def missing(*_args):raise ValueError('教材原文件内容已变化')
    owner.facade.preparation_textbook_crop_original=missing
    before=snapshot(owner,panel,assets);panel.read_textbook_crop_original()
    with pytest.raises(ValueError,match='原文件'):pending[0][0]()
    assert not FakeDialog.opened and snapshot(owner,panel,assets)==before


def test_native_reader_exact_original_pdf_narrow_controls_and_cancel(app,book_fixture,monkeypatch):
    f=book_fixture;facade,item,assets=personal(f)
    context=facade.preparation_textbook_crop_original(item,assets)
    monkeypatch.setattr(TextbookCropSourceDialog,'_load',lambda _self:None)
    dialog=TextbookCropSourceDialog(facade,item,assets,context)
    dialog._source_ready(context['source']);dialog.resize(960,780);dialog.show();app.processEvents()
    full_scope=dialog.summary.toPlainText()
    assert dialog.summary_tabs.maximumHeight()==85 and dialog.summary_tabs.height()<=85
    assert not dialog.heading.isVisible()
    dialog.resize(360,780);app.processEvents()
    assert dialog.summary_tabs.maximumHeight()==110 and dialog.summary_tabs.height()<=110
    assert dialog.summary.toPlainText()==full_scope
    dialog.resize(960,850);app.processEvents()
    assert dialog.summary_tabs.maximumHeight()==85 and dialog.summary_tabs.height()<=85
    dialog.resize(360,780);app.processEvents()
    assert dialog.summary_tabs.maximumHeight()==110 and dialog.summary_tabs.height()<=110
    assert dialog._loaded_pages and dialog.document.pageCount()==4
    assert dialog.view.pageNavigator().currentPage()==1 and dialog.pages.currentData()==2
    assert bytes(dialog.buffer.data())==f.original
    assert 'PDF第2页' in dialog.summary.toPlainText() and '尚未裁切' not in dialog.summary.toPlainText()
    assert not dialog.excerpt_button.isVisible() and not dialog.current_page_crop_button.isVisible()
    assert dialog.width()==360 and dialog.close_button.isVisible()
    for widget in (dialog.pages,dialog.zoom,dialog.close_button):
        assert dialog.rect().contains(widget.mapTo(dialog,QPoint(0,0)))
        assert dialog.rect().contains(widget.mapTo(dialog,widget.rect().bottomRight()))
    dialog.reject();assert dialog._closed and dialog.buffer.data().isEmpty()
    dialog._source_ready(context['source']);assert not dialog._source and dialog.buffer.data().isEmpty()
    dialog.deleteLater();app.processEvents()


def test_crop_reader_keeps_only_correct_scope_when_legacy_asset_index_is_present(app,book_fixture,monkeypatch):
    f=book_fixture;facade,item,assets=personal(f)
    item['title']='为什么要把这张图与完整教材页对照？'
    context=facade.preparation_textbook_crop_original(item,assets)
    source=deepcopy(context['source'])
    source['source_name']='合成教材册名';source['printed_page']=7
    source['visual_assets']={'assets':[{'label':'旧素材索引锚点','pdf_page':2,'printed_page':7}], 'notices':[]}
    monkeypatch.setattr(TextbookCropSourceDialog,'_load',lambda _self:None)
    dialog=TextbookCropSourceDialog(facade,item,assets,context)
    assert dialog.summary_tabs.count()==1 and dialog.summary_tabs.tabText(0)=='原页定位'
    dialog._source_ready(source);dialog.show();app.processEvents()
    assert dialog._loaded_pages and dialog.asset_list.count()==1
    assert dialog.summary_tabs.count()==1 and dialog.summary_tabs.currentWidget() is dialog.summary
    assert dialog.summary.toPlainText() == item['title']+'\n合成教材册名\nPDF第2页 · 印刷第7页'
    assert not dialog.reading_hints.isVisible() and not dialog.asset_list.isVisible() and not dialog.asset_details.isVisible()
    assert '尚未裁切' not in dialog.summary.toPlainText()
    dialog._document_status(dialog.document.status());app.processEvents()
    assert dialog.summary_tabs.currentWidget() is dialog.summary
    dialog.reject();dialog.deleteLater();app.processEvents()


def test_reader_rechecks_receipt_before_loading_and_rejects_late_changed_target(app,book_fixture,monkeypatch):
    f=book_fixture;facade,item,assets=personal(f)
    context=facade.preparation_textbook_crop_original(item,assets)
    monkeypatch.setattr(TextbookCropSourceDialog,'_load',lambda _self:None)
    alive=[True]
    def current():
        if not alive[0]:raise ValueError('课件在阅读期间变化')
    dialog=TextbookCropSourceDialog(facade,item,assets,context,current=current)
    assert dialog.facade.preparation_textbook_asset_source(**context['selection'])['pdf_bytes']==f.original
    receipt=next((f.paths.task_root/'preparation-v1/textbook-crops').glob('*.json'));receipt.unlink()
    with pytest.raises(ValueError):dialog.facade.preparation_textbook_asset_source(**context['selection'])
    alive[0]=False;dialog._source_ready(context['source'])
    assert dialog._closed and not dialog._source and '阅读期间变化' in dialog.status.text()
    dialog.deleteLater();app.processEvents()
