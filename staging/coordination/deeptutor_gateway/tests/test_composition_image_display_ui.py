"""Real Qt work-copy controls and one history commit, using memory-only images.

No profile, source document, asset store or Office output is used by this file.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pytest
pytest.importorskip('PySide6')
from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QGraphicsPixmapItem, QVBoxLayout

from test_composition_render import app
from integrations.deeptutor_shchem_v1.desktop_composition_render import arrange_composition
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, block, enable_composition, new_composition, resource,
    set_click_reveal, set_student_arrangement)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, new_design
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info
from integrations.deeptutor_shchem_v1.desktop_workbench.image_display_dialog import ImageDisplayDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel


def _image():
    stream = BytesIO()
    Image.new('RGB', (600, 300), '#467c63').save(stream, format='PNG')
    data = stream.getvalue()
    fingerprint = sha256(data).hexdigest()
    return data, {'asset_id': 'IMG-'+fingerprint, 'sha256': fingerprint,
        'caption': 'Synthetic original image', 'source': 'Synthetic memory author',
        'purpose': 'Image display geometry test', **image_info(data)}


def _question(asset, *, teacher_only=False, long=False):
    if teacher_only:
        return resource('personal_text', '教师原图备注', [
            block('text', 'teacher_note', '教师完整解析。', 1),
            block('image', 'teacher_note', asset['asset_id'], 2)],
            {'source_path': 'Synthetic memory author', 'source_sha256': 'unknown',
             'source_revision': 'unknown'})
    lines = [block('text', 'answer' if teacher_only else 'question',
        '\n'.join(f'第{i}行完整讲解，最后内容也必须保留。' for i in range(1, 46)) if long else '观察原图中的变化。', 1),
        block('image', 'answer' if teacher_only else 'question', asset['asset_id'], 2)]
    if not teacher_only:
        # Two uses of the same pixels must remain separate original block IDs.
        lines.extend([block('text', 'answer', '教师完整解析。', 3),
                      block('image', 'answer', asset['asset_id'], 4)])
    return resource('complete_question', '怎样从图中解释变化？', lines,
        {'source_path': 'Synthetic memory question', 'source_sha256': 'a'*64,
         'source_revision': 'b'*64, 'question_key': 'SYNTHETIC-IMAGE-DISPLAY',
         'scope_review_sha256': 'c'*64, 'scope_status': 'complete_native_scope',
         'hierarchy': dict.fromkeys(('paper_id', 'theme_big_question_id',
                                    'printed_question_id', 'atomic_part_id'), 'unknown')})


def _materials(*, teacher_only=False, long=False, overflow=False):
    data, asset = _image()
    item = _question(asset, teacher_only=teacher_only, long=long)
    value = new_composition('16:9')
    value = add_resource(value, item, {'page_id': value['pages'][0], 'x': 12, 'y': 12,
        'width': 296, 'height': 20 if overflow else 156})
    if not overflow:
        value = arrange_composition(value, [asset], {asset['asset_id']: data})
    return value, [asset], {asset['asset_id']: data}, item['id']


@pytest.fixture
def dialogs(app):
    opened = []
    def build(**kwargs):
        values = _materials(**kwargs)
        dialog = ImageDisplayDialog(values[0], values[3], values[1], values[2])
        opened.append(dialog)
        return dialog, values
    yield build
    for dialog in opened:
        dialog.reject()
        dialog.deleteLater()
    app.processEvents()


def _elements(dialog, scope):
    return [e for fragment in dialog._layouts[scope]['fragments'] for e in fragment['elements']]


def test_controls_are_block_bound_and_cancel_preserves_every_input(dialogs):
    dialog, values = dialogs()
    before = deepcopy(values)
    dialog.max_width.setValue(45)
    dialog.max_height.setValue(22)
    dialog.update_preview()
    assert dialog.apply_button.isEnabled()
    chosen = dialog.rows[0]
    assert chosen['image_block_id'] in dialog.binding.toPlainText()
    assert chosen['asset_id'] in dialog.binding.toPlainText()
    modified = next(r for r in dialog._candidate['resources'] if r['id'] == values[3])
    assert modified['image_display']['limits'] == {
        chosen['image_block_id']: {'max_width_mm': 45, 'max_height_mm': 22}}
    assert modified['blocks'] == before[0]['resources'][0]['blocks']
    assert modified['original'] == before[0]['resources'][0]['original']
    assert modified['locked'] == before[0]['resources'][0]['locked']
    QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    assert dialog.result_value is None and values == before


def test_repeated_asset_uses_keep_independent_limits_and_reset_omits_auto(dialogs):
    dialog, values = dialogs()
    first, second = [row['image_block_id'] for row in dialog.rows]
    assert dialog.rows[0]['asset_id'] == dialog.rows[1]['asset_id']
    dialog.max_width.setValue(41)
    dialog.images.setCurrentIndex(1)
    assert dialog.max_width.value() == 0
    dialog.max_height.setValue(18)
    dialog.images.setCurrentIndex(0)
    assert dialog.max_width.value() == 41 and dialog.max_height.value() == 0
    dialog.update_preview()
    assert dialog._candidate['resources'][0]['image_display']['limits'] == {
        first: {'max_width_mm': 41}, second: {'max_height_mm': 18}}
    dialog.reset.click()
    dialog.update_preview()
    assert dialog._candidate['resources'][0]['image_display']['limits'] == {second: {'max_height_mm': 18}}
    dialog.images.setCurrentIndex(1)
    dialog.reset.click()
    dialog.update_preview()
    assert 'image_display' not in dialog._candidate['resources'][0]
    assert 'image_display' not in values[0]['resources'][0]
    assert dialog.max_width.specialValueText() == '自动'


def test_complete_teacher_preview_contains_answers_student_scopes_hide_them(dialogs):
    dialog, _ = dialogs()
    assert len([e for e in _elements(dialog, 'teacher') if e['kind'] == 'image']) == 2
    for scope in ('classroom', 'student'):
        assert len([e for e in _elements(dialog, scope) if e['kind'] == 'image']) == 1
        assert not any('教师完整解析' in e.get('text', '') for e in _elements(dialog, scope))
    dialog.images.setCurrentIndex(1)
    dialog.audience.setCurrentIndex(dialog.audience.findData('student'))
    assert dialog.thumbnail.isHidden()
    dialog.audience.setCurrentIndex(dialog.audience.findData('teacher'))
    assert not dialog.thumbnail.isHidden()


def test_teacher_only_picture_can_apply_with_legitimate_empty_student_scope(dialogs):
    dialog, values = dialogs(teacher_only=True)
    dialog.max_height.setValue(18)
    dialog.update_preview()
    assert dialog.apply_button.isEnabled()
    assert not dialog._layouts['student']['pages'] and not dialog._layouts['classroom']['pages']
    dialog.audience.setCurrentIndex(dialog.audience.findData('student'))
    assert not any(isinstance(item, QGraphicsPixmapItem) for item in dialog.scene.items())
    assert dialog.thumbnail.isHidden()
    dialog.apply_button.click()
    assert dialog.result_value['resources'][0]['blocks'] == values[0]['resources'][0]['blocks']


@pytest.mark.parametrize('columns', [None, 1, 2])
def test_preview_preserves_click_and_student_pagination_policy_without_revealing_answers(app, columns):
    value, assets, data, identity = _materials()
    value = set_click_reveal(value, identity, True)
    value = set_student_arrangement(value, columns)
    dialog = ImageDisplayDialog(value, identity, assets, data)
    dialog.max_height.setValue(18)
    dialog.update_preview()
    assert dialog.apply_button.isEnabled()
    assert dialog._candidate['click_reveals'] == [identity]
    assert dialog._candidate.get('student_arrangement') == value.get('student_arrangement')
    assert not dialog._layouts['student']['pages']
    assert len([e for e in _elements(dialog, 'classroom') if e['kind'] == 'image']) == 1
    assert not any('教师完整解析' in e.get('text', '') for e in _elements(dialog, 'classroom'))
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def test_unchecked_reflow_preserves_frames_but_overflow_blocks_application(dialogs):
    dialog, values = dialogs()
    dialog.rearrange.setChecked(False)
    dialog.max_width.setValue(30)
    dialog.update_preview()
    assert dialog.apply_button.isEnabled()
    assert dialog._candidate['placements'] == values[0]['placements']
    assert dialog._candidate['pages'] == values[0]['pages']
    broken, _ = dialogs(overflow=True)
    assert broken.apply_button.isEnabled()
    broken.rearrange.setChecked(False)
    broken.update_preview()
    assert not broken.apply_button.isEnabled() and broken._candidate is None
    assert not broken.scene.items() and '重新排这项素材' in broken.status.text()
    broken.apply()
    assert broken.result_value is None
    broken.rearrange.setChecked(True)
    broken.update_preview()
    assert broken.apply_button.isEnabled()


def test_missing_original_pixels_clear_preview_and_block_application(app):
    value, assets, data, identity = _materials()
    dialog = ImageDisplayDialog(value, identity, assets, {})
    assert not dialog.apply_button.isEnabled() and dialog._candidate is None
    assert not dialog.scene.items()
    dialog.apply()
    assert dialog.result_value is None
    assert data and assets and value
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def test_complete_continuations_narrow_layout_and_focus_keep_edits(dialogs, app):
    dialog, _ = dialogs(long=True)
    dialog.resize(420, 760)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 420
    assert dialog.controls_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.controls_scroll.widget().width() <= dialog.controls_scroll.viewport().width()
    assert dialog.apply_button.mapTo(dialog, dialog.apply_button.rect().bottomRight()).y() < dialog.height()
    dialog.max_height.setValue(20)
    dialog.update_preview()
    before = deepcopy(dialog._limits)
    assert dialog.pages.count() > 1
    dialog.pages.setCurrentIndex(dialog.pages.count()-1)
    last_page = dialog.pages.currentData()
    assert any('第45行完整讲解' in e.get('text', '') for e in _elements(dialog, 'teacher'))
    dialog.focus.click()
    app.processEvents()
    assert dialog.controls_scroll.isHidden() and dialog.focus.text() == '返回设置'
    assert dialog.pages.currentData() == last_page and dialog.scene.items()
    dialog.focus.click()
    app.processEvents()
    assert not dialog.controls_scroll.isHidden() and dialog._limits == before
    assert dialog.max_height.value() == 20
    dialog.audience.setCurrentIndex(dialog.audience.findData('student'))
    dialog.next.click()
    assert dialog._limits == before


def test_narrow_long_layout_error_keeps_cancel_and_apply_reachable(app):
    value, assets, data, identity = _materials(overflow=True)
    value['resources'][0]['title'] = '很长的合成素材标题' * 100
    dialog = ImageDisplayDialog(value, identity, assets, data)
    dialog.rearrange.setChecked(False)
    dialog.update_preview()
    dialog.resize(420, 760)
    dialog.show()
    app.processEvents()
    assert not dialog.apply_button.isEnabled() and dialog.width() <= 420
    for button in (dialog.apply_button, dialog.cancel_button):
        assert button.mapTo(dialog, button.rect().bottomRight()).y() < dialog.height()
    assert dialog.status_scroll.verticalScrollBar().maximum() > 0
    assert dialog.status_scroll.horizontalScrollBar().maximum() == 0
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


@pytest.fixture
def desk(app):
    composition, assets, data, identity = _materials()
    design = enable_composition(new_design({'objective': ''}))
    design['composition'] = composition
    history = DesignHistory(design)
    payload = {'image_assets': assets, 'lesson_design': history.value, 'topic': 'Memory-only geometry'}
    owner = SimpleNamespace(history=history,
        facade=SimpleNamespace(preparation_image_bytes=lambda asset: data[asset['asset_id']]))
    window = QDialog()
    layout = QVBoxLayout(window)
    panel = LessonCompositionPanel(owner)
    layout.addWidget(panel)
    def sync():
        panel.set_payload({**payload, 'lesson_design': history.value})
    owner.sync_page = sync
    owner.travel = lambda redo=False: (history.travel(redo), sync())
    sync()
    panel.selected = identity
    panel.refresh()
    window.resize(1300, 900)
    window.show()
    app.processEvents()
    yield owner, panel, window
    window.close()
    window.deleteLater()
    app.processEvents()


def _run_modal(app, panel, action):
    failures = []
    seen = []
    def operate():
        modal = app.activeModalWidget()
        try:
            assert isinstance(modal, ImageDisplayDialog)
            seen.append(modal)
            action(modal)
        except BaseException as exc:
            failures.append(exc)
            if modal is not None:
                modal.reject()
    QTimer.singleShot(20, operate)
    QTest.mouseClick(panel.image_display_button, Qt.MouseButton.LeftButton)
    if failures:
        raise failures[0]
    assert seen
    return seen[0]


def test_panel_real_modal_cancel_makes_no_history_step(desk, app):
    owner, panel, _ = desk
    before = deepcopy(owner.history.value)
    def cancel(dialog):
        dialog.max_width.setValue(40)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    _run_modal(app, panel, cancel)
    assert owner.history.value == before and not owner.history.back


def test_panel_real_modal_applies_one_commit_and_one_undo_restores_all(desk, app):
    owner, panel, _ = desk
    before = deepcopy(owner.history.value)
    def apply(dialog):
        dialog.max_width.setValue(40)
        dialog.update_preview()
        assert dialog.apply_button.isEnabled()
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    _run_modal(app, panel, apply)
    after = deepcopy(owner.history.value)
    assert len(owner.history.back) == 1
    assert after['composition']['resources'][0]['original'] == before['composition']['resources'][0]['original']
    assert after['composition']['resources'][0]['blocks'] == before['composition']['resources'][0]['blocks']
    owner.travel(False)
    assert owner.history.value == before
    owner.travel(True)
    assert owner.history.value == after


def test_panel_real_modal_stale_confirmation_preserves_new_history(desk, app):
    owner, panel, _ = desk
    expected = []
    def stale(dialog):
        dialog.max_width.setValue(40)
        dialog.update_preview()
        assert dialog.apply_button.isEnabled()
        changed = deepcopy(owner.history.value)
        changed['classroom_notes'] = True
        owner.history.put(changed)
        owner.sync_page()
        expected.append(deepcopy(owner.history.value))
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    _run_modal(app, panel, stale)
    assert owner.history.value == expected[0] and len(owner.history.back) == 1
    assert '作品在处理期间变化' in panel.status.text()


def test_panel_entry_hidden_for_resource_without_images(desk):
    owner, panel, _ = desk
    value = deepcopy(owner.history.value)
    text = resource('personal_text', 'Memory text', [block('text', 'personal', 'No images')],
        {'source_path': 'Memory author', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    value['composition']['resources'].append(text)
    value['composition']['placements'][text['id']] = deepcopy(value['composition']['placements'][panel.selected])
    owner.history.put(value)
    owner.sync_page()
    panel.selected = text['id']
    panel.refresh_inspector()
    assert panel.image_display_button.isHidden()


def test_panel_reads_only_selected_images_and_preserves_unrelated_missing_image(desk, app):
    owner, panel, _ = desk
    value = deepcopy(owner.history.value)
    other = resource('personal_text', 'Other original picture',
        [block('image', 'personal', 'IMG-'+'f'*64)],
        {'source_path': 'Memory author', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    value['composition']['resources'].append(other)
    original_frame = deepcopy(value['composition']['placements'][panel.selected])
    value['composition']['placements'][other['id']] = original_frame
    owner.history.put(value)
    owner.sync_page()
    before = deepcopy(owner.history.value)
    def apply(dialog):
        dialog.max_width.setValue(40)
        dialog.update_preview()
        assert dialog.apply_button.isEnabled()
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    _run_modal(app, panel, apply)
    assert owner.history.value['composition']['resources'][-1] == other
    assert owner.history.value['composition']['placements'][other['id']] == original_frame
    assert len(owner.history.back) == 2
    owner.travel(False)
    assert owner.history.value == before
