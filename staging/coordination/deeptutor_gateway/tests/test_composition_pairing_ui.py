"""Teachers preview a real paired page, cancel, commit and undo as one operation."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from PySide6.QtWidgets import QDialog, QVBoxLayout
from test_composition_render import app
from test_lesson_design import sample
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, block, enable_composition, new_composition, resource, set_student_arrangement)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_workbench.composition_pairing_dialog import CompositionPairingDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel


@pytest.fixture
def materials(app, tmp_path):
    image = BytesIO()
    Image.new('RGB', (160, 90), '#467c63').save(image, format='PNG')
    raw = image.getvalue()
    asset = PreparationImageStore(tmp_path/'images').import_bytes(raw, '合成原图', '测试作者', '图文编排测试')
    picture = resource('personal_text', '观察完整原图', [block('image', 'personal', asset['asset_id'])],
        {'source_path': '作者图像', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    notes = resource('personal_text', '联系三个量', [block('text', 'personal', '比例、浓度与总量需要分别描述。')],
        {'source_path': '作者讲解', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    value = new_composition('16:9')
    value['pages'].append('CP-'+uuid4().hex)
    for item, page in zip((picture, notes), value['pages']):
        value = add_resource(value, item, {'page_id': page, 'x': 12, 'y': 12, 'width': 296, 'height': 156})
    return value, [asset], {asset['asset_id']: raw}, picture['id'], notes['id']


def dialog_for(materials):
    value, assets, data, picture, notes = materials
    dialog = CompositionPairingDialog(value, assets, data, selected=notes)
    dialog.first.setCurrentIndex(dialog.first.findData(picture))
    dialog.second.setCurrentIndex(dialog.second.findData(notes))
    dialog.update_preview()
    return dialog


def test_preview_title_edit_and_cancel_do_not_change_source_or_images(materials):
    original = deepcopy(materials)
    dialog = dialog_for(materials)
    assert dialog.apply_button.isEnabled()
    dialog.question_title.setText('从图中能观察到什么？')
    dialog.update_preview()
    assert dialog._candidate['resources'][0]['title'] == '从图中能观察到什么？'
    assert dialog._candidate['resources'][0]['original'] == original[0]['resources'][0]['original']
    dialog.reject()
    assert dialog.result_value is None and materials == original
    dialog.deleteLater()


def test_click_explanation_changes_only_explicit_flag_and_filters_static_preview(materials):
    dialog = dialog_for(materials)
    dialog.click_second.setChecked(True)
    dialog.update_preview()
    assert dialog._candidate['click_reveals'] == [materials[4]]
    assert {f['resource_id'] for f in dialog._layouts['student']['fragments']} == {materials[3]}
    assert {f['resource_id'] for f in dialog._layouts['classroom']['fragments']} == {materials[3], materials[4]}
    assert dialog._candidate['resources'] == materials[0]['resources']
    dialog.apply()
    assert dialog.result_value['click_reveals'] == [materials[4]]
    dialog.deleteLater()


def test_classroom_placement_choice_is_visible_and_explicit(materials):
    value, assets, data, picture, notes = materials
    value = set_student_arrangement(value, 2)
    dialog = CompositionPairingDialog(value, assets, data, selected=notes)
    assert '一并切换' in dialog.arrangement_note.text()
    assert 'student_arrangement' not in dialog._candidate
    dialog.classroom_uses_pair.setChecked(False)
    dialog.update_preview()
    assert dialog._candidate['student_arrangement'] == {'columns': 2}
    assert '仅用于教师' in dialog.arrangement_note.text()
    dialog.deleteLater()


def test_duplicate_selection_blocks_apply_and_clears_invalid_preview(materials):
    dialog = dialog_for(materials)
    dialog.second.setCurrentIndex(dialog.second.findData(materials[3]))
    dialog.update_preview()
    assert not dialog.apply_button.isEnabled() and dialog._candidate is None
    assert '不同的素材' in dialog.status.text()
    dialog.apply()
    assert dialog.result_value is None
    dialog.deleteLater()


@pytest.fixture
def desk(materials, app):
    value, assets, data, picture, notes = materials
    payload = sample()
    payload['lesson_design'] = enable_composition(payload['lesson_design'])
    payload['lesson_design']['composition'] = set_student_arrangement(value, 2)
    payload['image_assets'] = assets
    history = DesignHistory(payload['lesson_design'])
    owner = SimpleNamespace(history=history, facade=SimpleNamespace(preparation_image_bytes=lambda asset: data[asset['asset_id']]))
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    panel = LessonCompositionPanel(owner)
    layout.addWidget(panel)
    def sync():
        panel.set_payload({**payload, 'lesson_design': history.value})
    owner.sync_page = sync
    owner.travel = lambda redo=False: (history.travel(redo), sync())
    sync()
    panel.selected = picture
    panel.refresh()
    yield owner, panel, dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_panel_cancel_does_not_make_an_undo_step(desk, monkeypatch):
    owner, panel, window = desk
    before = deepcopy(owner.history.value)
    monkeypatch.setattr(CompositionPairingDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    panel.pair_button.click()
    assert owner.history.value == before and not owner.history.back


def test_pair_applies_once_then_undo_restores_positions_and_student_policy(desk, monkeypatch):
    owner, panel, window = desk
    before = deepcopy(owner.history.value)
    def accept(self):
        self.click_second.setChecked(True)
        self.apply()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CompositionPairingDialog, 'exec', accept)
    panel.pair_button.click()
    after = owner.history.value
    assert len(owner.history.back) == 1
    assert 'student_arrangement' not in after['composition']
    assert after['composition']['click_reveals']
    assert after['composition']['resources'] == before['composition']['resources']
    assert not panel.student.isChecked()
    owner.travel(False)
    assert owner.history.value == before


def test_changed_lesson_during_modal_preview_is_not_overwritten(desk, monkeypatch):
    owner, panel, window = desk
    expected = None
    def stale(self):
        nonlocal expected
        self.apply()
        changed = deepcopy(owner.history.value)
        changed['nodes'][0]['teacher_action'] = '教师刚刚修改了组织方式'
        owner.history.put(changed)
        owner.sync_page()
        expected = deepcopy(owner.history.value)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CompositionPairingDialog, 'exec', stale)
    panel.pair_button.click()
    assert owner.history.value == expected and len(owner.history.back) == 1
    assert '作品在处理期间变化' in panel.status.text()


def test_narrow_dialog_can_reach_apply_and_enlarge_the_same_preview(materials, app):
    dialog = dialog_for(materials)
    dialog.resize(420, 780)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 420
    assert dialog.apply_button.isVisible()
    assert dialog.apply_button.mapTo(dialog, dialog.apply_button.rect().bottomRight()).y() < dialog.height()
    dialog.focus.click()
    app.processEvents()
    assert not dialog.controls_scroll.isVisible() and dialog.scene.items()
    assert dialog.focus.text() == '返回选材'
    dialog.close()
    dialog.deleteLater()
