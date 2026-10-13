"""Explicit cross-section selection, full preview and atomic same-work handoff."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from test_desktop_library_ui import _ManualBridge,qt_app
from test_knowledge_note_picker import rows
from test_knowledge_batch_preview import card
from test_question_explorer_ui import window
from test_composition_ui import desk,personal
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1 import desktop_knowledge_batch_preview as preview
from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_note_picker import KnowledgeNotePicker
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest


@pytest.fixture
def picker(qt_app,monkeypatch):
    bridge=_ManualBridge();calls=[];changed=[False]
    def imported(_facade,selections):
        calls.append(deepcopy(selections))
        return {'resources':[card(s['note_id'],('新内容' if changed[0] else '完整课堂内容')+'\n第二行') for s in selections]}
    monkeypatch.setattr(preview,'import_knowledge_cards',imported)
    value=KnowledgeNotePicker(SimpleNamespace(),rows(),tasks=bridge,batch=True);value.show();qt_app.processEvents()
    yield value,bridge,qt_app,calls,changed
    value.reject();value.deleteLater();qt_app.processEvents()


def check(value,index):value.list.item(index).setCheckState(Qt.CheckState.Checked)
def ready(value,bridge):
    value.preview_choices();bridge.succeed(bridge.take('预览本批知识卡'),run=True)


def test_reading_and_filtering_do_not_select_cross_section_checks_keep_order(picker):
    value,bridge,app,calls,_=picker
    value.list.setCurrentRow(2);assert not value._checked and not value.confirm.isEnabled() and calls==[]
    check(value,2);check(value,0)
    value.book.setCurrentIndex(value.book.findData('甲'));value.section.setCurrentIndex(value.section.findData('甲二'))
    value.search.setText('无结果');assert value.list.count()==0 and len(value._checked)==2 and value.preview_button.isEnabled()
    ready(value,bridge)
    assert [s['note_id'] for s in calls[0]]==['2','0']
    assert value.confirm.isEnabled() and value.batch_detail.toPlainText().count('第二行')==2
    value.choose();assert value.composition_result is None and not value.confirm.isEnabled()
    bridge.succeed(bridge.take('确认本批知识卡'),run=True)
    assert value.result()==QDialog.DialogCode.Accepted
    assert [r['title'] for r in value.composition_result['resources']]==['2','0']


def test_removing_selection_invalidates_preview_and_restores_filtered_checkboxes(picker):
    value,bridge,_,calls,_=picker;check(value,0);check(value,1);ready(value,bridge)
    value.basket.setCurrentRow(0);value.remove_choice()
    assert len(value._checked)==1 and not value.confirm.isEnabled() and not value.batch_detail.toPlainText()
    assert value.list.item(0).checkState()==Qt.CheckState.Unchecked
    assert value.list.item(1).checkState()==Qt.CheckState.Checked
    value.clear_choices();assert not value._checked and not value.preview_button.isEnabled() and value.basket.count()==0


@pytest.mark.parametrize('action',['change','close'])
def test_pending_preview_callback_cannot_accept_cancelled_or_changed_selection(picker,action):
    value,bridge,_,calls,_=picker;check(value,0);value.preview_choices();pending=bridge.take('预览本批知识卡')
    if action=='change':check(value,1)
    else:value.reject()
    assert pending.task_id in bridge.cancelled
    bridge.succeed(pending,{'result':{'resources':[card()]},'signature':'a'*64})
    assert value._prepared is None and value.composition_result is None and calls==[]


def test_changed_content_after_full_preview_requires_new_preview(picker):
    value,bridge,_,calls,changed=picker;check(value,0);ready(value,bridge);changed[0]=True
    value.choose();pending=bridge.take('确认本批知识卡')
    with pytest.raises(ValueError,match='预览后变化'):pending.operation()
    bridge.fail(pending,'知识卡内容或来源版本在预览后变化，请重新预览。')
    assert value.composition_result is None and not value.confirm.isEnabled() and value.preview_button.isEnabled()
    assert len(value._checked)==1


@pytest.mark.parametrize('width',[420,1100])
def test_batch_preview_and_confirmation_controls_reachable_at_narrow_and_wide_width(picker,width):
    value,bridge,app,_,_=picker;check(value,0);ready(value,bridge);value.resize(width,850);app.processEvents()
    assert value.width()==width
    for widget in (value.book,value.section,value.search,value.preview_button,value.confirm):
        pos=widget.mapTo(value,widget.rect().topLeft())
        assert pos.x()>=0 and pos.y()>=0 and pos.x()+widget.width()<=value.width()
        assert pos.y()+widget.height()<=value.height()


def test_same_work_batch_add_is_one_undo_and_keeps_edited_existing_version(desk,monkeypatch):
    d,panel,app,page=desk;original=card('保留版本');incoming=card('新版本')
    panel.add_result({'resources':[original]},digest(d.history.value))
    before=deepcopy(d.history.value);assets=deepcopy(page.image_assets_widget.assets())
    monkeypatch.setattr(sources,'knowledge_options',lambda *a:{'concepts':[],'reading_notes':rows()})
    def choose(p):
        assert p.batch;p.composition_result={'resources':[deepcopy(original),deepcopy(incoming)]};return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeNotePicker,'exec',choose)
    monkeypatch.setattr(d,'run',lambda _label,operation,done,**kw:done(operation()))
    panel.add_note();settle(app)
    assert len(d.history.value['composition']['resources'])==2 and d.history.value['composition']['resources'][0]==original
    frame=next(f['frame'] for f in panel.layout_result['fragments'] if f['resource_id']==incoming['id'])
    assert panel._view_page_id==frame['page_id'] and page.image_assets_widget.assets()==assets
    d.travel(False);assert d.history.value==before


def test_cancel_or_external_edit_does_not_overwrite_lesson(desk,monkeypatch):
    d,panel,app,page=desk;before=deepcopy(d.history.value)
    monkeypatch.setattr(sources,'knowledge_options',lambda *a:{'concepts':[],'reading_notes':rows()})
    monkeypatch.setattr(d,'run',lambda _label,operation,done,**kw:done(operation()))
    monkeypatch.setattr(KnowledgeNotePicker,'exec',lambda p:QDialog.DialogCode.Rejected)
    panel.add_note();assert d.history.value==before
    changed=[None]
    def choose(p):
        panel.add_result({'resources':[personal('选择期间的新编辑')]},digest(d.history.value))
        changed[0]=deepcopy(d.history.value);p.composition_result={'resources':[card()]};return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeNotePicker,'exec',choose);panel.add_note()
    assert d.history.value==changed[0] and '选择知识卡期间变化' in d.status.text()
