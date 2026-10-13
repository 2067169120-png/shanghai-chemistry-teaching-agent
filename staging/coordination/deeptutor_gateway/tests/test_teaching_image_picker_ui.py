"""Native image preview, staged import and explicit same-work handoff."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from test_question_explorer_ui import window
from test_composition_ui import desk, personal
from test_desktop_studio_ui import settle
from test_composition_teaching_images import picture, source_question
from integrations.deeptutor_shchem_v1.desktop_workbench.teaching_image_picker import TeachingImagePicker
from integrations.deeptutor_shchem_v1.desktop_workbench import teaching_image_picker as ui
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from test_pending_material_edit import app, desk as safe_workspace
from test_composition_classroom_panel_ui import desk as batch_desk, choose_images, finish_images, modal
from integrations.deeptutor_shchem_v1.desktop_workbench.image_batch_workspace import ImageBatchWorkspace

def test_native_picker_previews_pixels_and_keeps_edits_while_filtering_at_narrow_size(desk):
    d,panel,app,page=desk;data,asset=picture();other,second=picture('red')
    facade=SimpleNamespace(preparation_image_bytes=lambda a:data if a['asset_id']==asset['asset_id'] else other)
    picker=TeachingImagePicker(facade,[asset,second],[],panel);picker.show();settle(app)
    assert picker.preview.has_image and picker.confirm_button.isEnabled()
    picker.purpose.setPlainText('先观察，再说明变化');picker.search.setText('无匹配项');settle(app)
    assert not picker.confirm_button.isEnabled()
    picker.search.clear();settle(app);assert picker.purpose.toPlainText()=='先观察，再说明变化'
    picker.resize(420,850);settle(app)
    assert picker.split.orientation()==Qt.Orientation.Vertical
    assert picker.confirm_button.isVisible() and picker.confirm_button.geometry().height()>0
    QTest.mouseClick(picker.confirm_button,Qt.MouseButton.LeftButton);settle(app)
    assert picker.result()==QDialog.DialogCode.Accepted and picker.selection['purpose']=='先观察，再说明变化'
    picker.deleteLater()

def test_native_teacher_answer_picture_scope_cannot_be_unchecked_or_guess_unknown_scope(desk):
    d,panel,app,page=desk;data,asset=picture();question=source_question(asset,'answer')
    picker=TeachingImagePicker(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[question],panel)
    picker.show();settle(app)
    assert picker.teacher_only.isChecked() and not picker.teacher_only.isEnabled()
    picker.accept_choice();assert picker.selection['teacher_only']
    asset['purpose']='完整题原图；按原角色排版。'
    missing=TeachingImagePicker(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[],panel)
    missing.show();settle(app)
    assert missing.preview.has_image and not missing.confirm_button.isEnabled()
    assert '完整题' in missing.status.text()
    picker.deleteLater();missing.reject();missing.deleteLater()

def test_nested_local_import_is_staged_and_parent_cancel_never_calls_import_or_changes_work(desk,monkeypatch,tmp_path):
    d,panel,app,page=desk;data,_=picture();path=tmp_path/'课外情境.png';path.write_bytes(data);calls=[]
    facade=SimpleNamespace(import_preparation_image=lambda *a:calls.append(a))
    picker=TeachingImagePicker(facade,[],[],panel);picker.show();settle(app)
    before=deepcopy(page._payload())
    monkeypatch.setattr(ui.QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
    def metadata(dialog):
        dialog.caption_input.setText('真实课堂情境');dialog.purpose_input.setText('解释现象')
        assert dialog.preview.has_image
        dialog._accept_if_complete();return dialog.result()
    monkeypatch.setattr(ui.PreparationImageMetadataDialog,'exec',metadata)
    picker.choose_local();settle(app)
    assert picker.current['local']['sha256'] and picker.title.text()=='真实课堂情境'
    assert picker.purpose.toPlainText()=='解释现象' and picker.confirm_button.isEnabled()
    picker.reject();assert calls==[] and page._payload()==before and path.read_bytes()==data
    picker.deleteLater()

def test_explicit_handoff_adds_picture_and_undo_removes_use_without_changing_source(batch_desk,monkeypatch,tmp_path):
    # Existing contract migrated from the single-image modal to the actual
    # batch modal; its owner and files are isolated from the real app profile.
    d,panel,app,page=batch_desk
    original_assets=page.image_assets_widget.assets()
    original_pixels={a['asset_id']:d.facade.preparation_image_bytes(a) for a in original_assets}
    panel.add_result({'resources':[personal('既有内容')]},digest(d.history.value))
    before=deepcopy(d.history.value);back_count=len(d.history.back)
    paths=choose_images(batch_desk,monkeypatch,tmp_path)
    source_bytes={str(path):path.read_bytes() for path in paths}
    assert d.history.value==before
    finish_images(d);app.processEvents()
    after=deepcopy(d.history.value);items=after['composition']['resources'][1:]
    assert after['composition']['resources'][0]==before['composition']['resources'][0]
    assert len(items)==2 and len(d.history.back)==back_count+1
    assert items[0]['title']=='为什么第二张图会有这种现象？'
    assert items[1]['title']=='first'
    for item in items:
        assert item['original']['kind']=='preparation_image'
        assert digest(item['original'])==item['original_sha256']
        assert [(b['id'],b['kind'],b['role']) for b in item['blocks']]==[
            (b['id'],b['kind'],b['role']) for b in item['original']['blocks']]
        assert [b for b in item['blocks'] if b['kind']=='image']==[
            b for b in item['original']['blocks'] if b['kind']=='image']
        asset=item['original']['evidence']['image_asset']
        assert asset in page.image_assets_widget.assets()
        assert d.facade.preparation_image_bytes(asset) in source_bytes.values()
    assert items[0]['blocks'][-1]['value']=='观察并比较完整条件。'
    assert items[0]['original']['blocks'][-1]['value']=='观察图片并说明与本节内容的联系。'
    assert all(a in page.image_assets_widget.assets() for a in original_assets)
    assert all(d.facade.preparation_image_bytes(a)==original_pixels[a['asset_id']] for a in original_assets)
    frame=next(f['frame'] for f in panel.layout_result['fragments'] if f['resource_id']==items[0]['id'])
    assert panel._view_page_id==frame['page_id'] and panel._view_page_id!=before['composition']['pages'][0]
    assets_after=page.image_assets_widget.assets()
    d.travel(False);assert d.history.value==before and page.image_assets_widget.assets()==assets_after
    assert all(path.read_bytes()==source_bytes[str(path)] for path in paths)
    d.travel(True);assert d.history.value==after

def test_changed_image_form_during_picker_does_not_overwrite_newer_edit(batch_desk,tmp_path):
    d,panel,app,page=batch_desk;asset=page.image_assets_widget.assets()[0]
    original=d.facade.preparation_image_bytes(asset)
    data,_=picture();path=tmp_path/'待加入原图.png';path.write_bytes(data)
    before=deepcopy(d.history.value);new_assets=[{**asset,'caption':'选择期间的新编辑'}]
    errors=[]
    def change(workspace):
        workspace.add_local_paths([str(path)])
        assert workspace.list.count()==1
        page.image_assets_widget.set_assets(new_assets)
        workspace.accept_batch()
        assert workspace.selection_requests is None
    modal(ImageBatchWorkspace,change,errors)
    panel.add_images();app.processEvents()
    assert not errors,errors
    assert d.history.value==before and page.image_assets_widget.assets()==new_assets
    assert not d.tasks.jobs and not panel._transaction_scopes
    assert path.read_bytes()==data and d.facade.preparation_image_bytes(asset)==original
    assert '变化' in d.status.text()
