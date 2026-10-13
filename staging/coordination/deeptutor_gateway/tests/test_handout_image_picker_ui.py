"""Async preview, filtering, cancellation and same-lesson undo for lecture images."""
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from test_handout_images import lecture
from test_desktop_library_ui import _ManualBridge,qt_app
from test_question_explorer_ui import window
from test_composition_ui import desk,personal
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1 import desktop_handout_images as handout
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_image_picker import HandoutImagePicker
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest


@pytest.fixture
def picker(qt_app,lecture):
    bridge=_ManualBridge(); f=lecture; row=deepcopy(f.row);row['asset_id']='word-b21-image1';row['block_index']=21
    value=HandoutImagePicker(f.facade,{'images':[f.row,row]},[],tasks=bridge);value.show();settle(qt_app)
    yield value,bridge,f,qt_app
    value.reject();value.deleteLater();settle(qt_app)


def test_preview_and_filter_keep_question_and_use_without_importing_on_cancel(picker):
    value,bridge,f,app=picker;bridge.succeed(bridge.take('读取讲义原图'),run=True)
    assert value.preview.has_image and value.confirm.isEnabled()
    value.title.setText('分子结构怎样影响性质？');value.purpose.setPlainText('开场看结构，课末回看性质。')
    value.search.setText('无匹配');assert not value.confirm.isEnabled()
    value.search.clear();bridge.succeed(bridge.take('读取讲义原图'),run=True)
    assert value.title.text()=='分子结构怎样影响性质？' and value.purpose.toPlainText().endswith('回看性质。')
    value.reject();assert value.selection is None and not f.facade.paths.task_root.exists()


@pytest.mark.parametrize('action',['select_other','close'])
def test_late_callback_does_not_show_or_accept_obsolete_picture(picker,action):
    value,bridge,f,app=picker;pending=bridge.take('读取讲义原图')
    if action=='select_other':value.list.setCurrentRow(1)
    else:value.reject()
    bridge.succeed(pending,handout.handout_image_preview(f.facade,f.row))
    assert value._previewed is None and value.selection is None and not value.confirm.isEnabled()
    assert pending.task_id in bridge.cancelled


@pytest.mark.parametrize('width',[420,1080])
def test_picker_controls_reachable_in_narrow_and_wide_window(picker,width):
    value,bridge,f,app=picker;bridge.succeed(bridge.take('读取讲义原图'),run=True)
    value.resize(width,860);settle(app)
    assert value.split.orientation()==(Qt.Orientation.Vertical if width<750 else Qt.Orientation.Horizontal)
    for widget in (value.source,value.search,value.confirm):
        pos=widget.mapTo(value,widget.rect().topLeft())
        assert pos.x()>=0 and pos.y()>=0 and pos.x()+widget.width()<=value.width() and pos.y()+widget.height()<=value.height()


def test_failed_preview_prevents_acceptance(picker):
    value,bridge,f,app=picker;bridge.fail(bridge.take('读取讲义原图'),'来源版本已变化')
    value.choose();assert value.selection is None and not value.confirm.isEnabled()


def test_same_work_acceptance_is_one_undo_and_does_not_replace_existing_text(desk,lecture,monkeypatch):
    d,panel,app,page=desk;f=lecture;panel.add_result({'resources':[personal('已有课堂知识')]},digest(d.history.value))
    before=deepcopy(d.history.value)
    monkeypatch.setattr(handout,'handout_image_options',lambda _: {'images':[f.row]})
    imported=handout.import_handout_image(f.facade,[],[],f.selection)
    monkeypatch.setattr(handout,'import_handout_image',lambda *a:imported)
    monkeypatch.setattr(d.facade,'preparation_image_bytes',lambda _:f.data,raising=False)
    monkeypatch.setattr(d,'run',lambda _label,operation,done,**kw:done(operation()))
    def accept(p):p.selection=deepcopy(f.selection);return QDialog.DialogCode.Accepted
    monkeypatch.setattr(HandoutImagePicker,'exec',accept)
    panel.add_handout_images();settle(app)
    assert d.history.value['composition']['resources'][:-1]==before['composition']['resources']
    assert d.history.value['composition']['resources'][-1]['original']['kind']=='handout_image'
    assert page.image_assets_widget.assets()==imported['image_assets']
    d.travel(False);assert d.history.value==before


def test_cancel_and_selection_during_external_edit_keep_current_work(desk,lecture,monkeypatch):
    d,panel,app,page=desk;f=lecture;before=deepcopy(d.history.value);calls=[]
    monkeypatch.setattr(handout,'handout_image_options',lambda _: {'images':[f.row]})
    monkeypatch.setattr(handout,'import_handout_image',lambda *a:calls.append(a))
    monkeypatch.setattr(d,'run',lambda _label,operation,done,**kw:done(operation()))
    monkeypatch.setattr(HandoutImagePicker,'exec',lambda p:QDialog.DialogCode.Rejected)
    panel.add_handout_images();assert d.history.value==before and not calls
    changed=[]
    def edit(p):
        panel.add_result({'resources':[personal('新编辑')]},digest(d.history.value));changed.append(deepcopy(d.history.value))
        p.selection=f.selection;return QDialog.DialogCode.Accepted
    monkeypatch.setattr(HandoutImagePicker,'exec',edit);panel.add_handout_images()
    assert d.history.value==changed[0] and not calls and '选择讲义图期间变化' in d.status.text()
