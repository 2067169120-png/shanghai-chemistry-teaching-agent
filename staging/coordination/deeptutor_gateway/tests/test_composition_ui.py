"""Native work-copy commits, independent placement/order and undo in one draft."""
from copy import deepcopy
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourceFrame
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource,block
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest


def personal(text='可编辑个人文字'):
    return resource('personal_text','合成排版验证',[block('text','personal',text)],
                    {'source_path':'合成作者输入','source_sha256':'unknown','source_revision':'unknown'})


@pytest.fixture
def desk(window):
    win,app=window;page=win.preparation_page;page.topic.setText('合成页面编排验证');page.materials.setPlainText('既有备课资料')
    d=LessonDesignDialog(page);d.resize(1400,900);d.show();panel=d.composition_panel;d.tabs.setCurrentWidget(panel);settle(app)
    yield d,panel,app,page
    d.reject();d.deleteLater();settle(app)


def test_two_sequential_handoffs_commit_work_copies_without_mutating_history(desk):
    d,panel,app,page=desk;original=deepcopy(d.history.value)
    assert 'composition' not in original
    panel.add_result({'resources':[personal()]},digest(d.history.value));first=deepcopy(d.history.value)
    returned=panel.composition();returned['resources'].clear();assert d.history.value==first
    panel.add_result({'resources':[personal('第二项全文')]},digest(d.history.value))
    assert len(d.history.value['composition']['resources'])==2
    assert page.materials.toPlainText()=='既有备课资料'
    d.travel(False);assert d.history.value==first
    d.travel(False);assert d.history.value==original


def test_stale_handoff_cannot_change_form_or_images(desk):
    d,panel,app,page=desk;expected=digest(d.history.value)
    panel.add_result({'resources':[personal()]},expected);before=deepcopy(page._payload())
    with pytest.raises(ValueError,match='已变化'):panel.add_result({'resources':[personal('过时素材')]},expected)
    assert page._payload()==before


def test_native_drag_order_remove_and_undo_keep_source_snapshots(desk):
    d,panel,app,page=desk
    panel.add_result({'resources':[personal('第一项'),personal('第二项')]},digest(d.history.value));panel.arrange(1);settle(app)
    before=panel.composition();identity=before['resources'][-1]['id'];panel.selected=identity;panel.refresh();panel.fit_view();settle(app)
    item=next(i for i in panel.scene.items() if isinstance(i,ResourceFrame) and i.fragment['resource_id']==identity)
    point=panel.view.mapFromScene(item.sceneBoundingRect().center());target=point+QPoint(8,8)
    QTest.mousePress(panel.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.mouseMove(panel.view.viewport(),target,delay=50)
    QTest.mouseRelease(panel.view.viewport(),Qt.MouseButton.LeftButton,pos=target);settle(app)
    moved=panel.composition();assert moved['resources']==before['resources']
    assert moved['placements'][identity]!=before['placements'][identity]
    panel.reorder(-1);reordered=panel.composition();assert reordered['placements']==moved['placements']
    assert reordered['resources']==moved['resources'][::-1]
    panel.remove_selected();assert len(panel.composition()['resources'])==1
    d.travel(False);assert panel.composition()==reordered
    d.travel(False);assert panel.composition()==moved
    d.travel(False);assert panel.composition()==before


def test_card_crop_cancel_keeps_source_and_history_and_uses_immutable_snapshot(desk,monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_composition_panel as ui
    d,panel,app,page=desk
    card=resource('knowledge_note','合成知识卡',[block('text','knowledge','有出处的合成知识')],
        {'source_path':'合成教材.pdf','source_sha256':'a'*64,'source_revision':'b'*64,
         'record_id':'SYNTHETIC-NOTE','pdf_pages':[8],'printed_pages':[4]})
    panel.add_result({'resources':[card]},digest(d.history.value));panel.selected=card['id'];panel.refresh();settle(app)
    assert panel.knowledge_textbook_button.isVisible()
    before=deepcopy(d.history.value);payload=deepcopy(page._payload());seen=[]
    def options(_facade,item):
        seen.append(item);return {'rows':[],'notices':[],'binding':'c'*64}
    monkeypatch.setattr(sources,'knowledge_textbook_options',options)
    monkeypatch.setattr(ui.ResourcePicker,'exec',lambda _:ui.QDialog.DialogCode.Rejected)
    monkeypatch.setattr(d,'run',lambda _label,task,done,**_kwargs:done(task()))
    QTest.mouseClick(panel.knowledge_textbook_button,Qt.MouseButton.LeftButton);settle(app)
    assert seen==[card] and d.history.value==before and page._payload()==payload
    panel.add_result({'resources':[personal()]},digest(d.history.value));panel.selected=panel.composition()['resources'][-1]['id'];panel.refresh()
    assert not panel.knowledge_textbook_button.isVisible()


def test_late_card_options_cannot_open_picker_after_draft_edit(desk,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_composition_panel as ui
    d,panel,app,_page=desk
    card=resource('knowledge_note','合成知识卡',[block('text','knowledge','合成内容')],
        {'source_path':'合成教材.pdf','source_sha256':'a'*64,'source_revision':'b'*64,
         'record_id':'SYNTHETIC-NOTE','pdf_pages':[8],'printed_pages':[4]})
    panel.add_result({'resources':[card]},digest(d.history.value));panel.selected=card['id'];panel.refresh()
    pending=[];monkeypatch.setattr(d,'run',lambda _label,_task,done,**_kwargs:pending.append(done))
    monkeypatch.setattr(ui.ResourcePicker,'exec',lambda _:pytest.fail('stale callback opened a picker'))
    panel.add_knowledge_textbook();panel.add_result({'resources':[personal()]},digest(d.history.value))
    before=deepcopy(d.history.value);pending[0]({'rows':[],'notices':[],'binding':'c'*64})
    assert d.history.value==before and '作品在选图期间变化' in panel.status.text()
