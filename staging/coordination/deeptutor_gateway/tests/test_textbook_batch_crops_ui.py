"""Native ordered review, source-page baskets and one complete lesson handoff."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_bookshelf_ui import app as app, page as page, leaf
from test_textbook_preparation_crops import crop_fixture as crop_fixture
from test_textbook_preparation_crops_ui import select
from test_textbook_batch_crops import batch
from test_desktop_library_ui import _ManualBridge
from test_desktop_studio_ui import window as window
from test_personal_textbook_study import book_fixture as book_fixture
from test_personal_textbook_ui import panel as panel, enter_chapter
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_batch_crop_dialog import TextbookBatchCropDialog


@pytest.fixture
def batch_dialog(app, crop_fixture):
    f = crop_fixture; bridge = _ManualBridge()
    choices = [{'selection': f.selection, 'label': f'合成图表{i}'} for i in (1, 2)]
    value = TextbookBatchCropDialog(f.facade, bridge, choices)
    value.show(); bridge.succeed(bridge.take('读取教材框选原页'), run=True); app.processEvents()
    yield value, bridge, f
    value.close(); value.deleteLater(); app.processEvents()


def confirm_current(value, bridge, index):
    value._canvas_changed((100 + index * 10, 100, 610 + index * 20, 630))
    value.caption.setText(f'本批合成图{index + 1}')
    value.purpose.setText(f'观察第{index + 1}个选区')
    value.preview_button.click(); bridge.succeed(bridge.take('核对教材裁片'), run=True)
    assert not value.confirm.isEnabled()
    value.checked.setChecked(True); value.confirm.click()


def test_all_images_require_individual_review_and_separate_final_click(batch_dialog):
    value, bridge, f = batch_dialog; before = disk_bytes(f.root)
    assert not value._confirmed and not value.confirm.isEnabled()
    confirm_current(value, bridge, 0)
    assert value._index == 1 and len(value._confirmed) == 1 and value.selection_requests is None
    bridge.succeed(bridge.take('读取教材框选原页'), run=True)
    assert not value.checked.isChecked() and value._chosen_bounds is None
    confirm_current(value, bridge, 1)
    assert value.result() != QDialog.DialogCode.Accepted and value.selection_requests is None
    assert value.confirm.text() == '加入当前备课（2张）'
    value.confirm.click()
    assert [r['caption'] for r in value.selection_requests] == ['本批合成图1', '本批合成图2']
    assert value.result() == QDialog.DialogCode.Accepted and not f.task_root.exists()
    assert disk_bytes(f.root) == before


def test_returning_to_confirmed_image_restores_fields_but_requires_new_review(batch_dialog):
    value, bridge, _ = batch_dialog; confirm_current(value, bridge, 0)
    bridge.succeed(bridge.take('读取教材框选原页'), run=True)
    value.previous.click(); bridge.succeed(bridge.take('读取教材框选原页'), run=True)
    assert value.caption.text() == '本批合成图1' and value.purpose.text() == '观察第1个选区'
    assert value._chosen_bounds == {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    assert not value._confirmed and not value._preview and not value.checked.isChecked()
    assert value.preview_button.isEnabled() and not value.confirm.isEnabled()


def test_unconfirmed_navigation_preserves_draft_fields_and_bounds(batch_dialog):
    value, bridge, _ = batch_dialog
    value._canvas_changed((120, 140, 640, 670)); value.caption.setText('尚未预览的图题'); value.purpose.setText('未完成用途')
    value.next.click(); bridge.succeed(bridge.take('读取教材框选原页'), run=True)
    value.previous.click(); bridge.succeed(bridge.take('读取教材框选原页'), run=True)
    assert value.caption.text() == '尚未预览的图题' and value.purpose.text() == '未完成用途'
    assert value._chosen_bounds == {'left': 120, 'top': 140, 'right': 640, 'bottom': 670}
    assert not value.checked.isChecked() and not value.confirm.isEnabled()


def test_cancel_with_pending_next_page_discards_whole_batch_and_late_callback(batch_dialog):
    value, bridge, f = batch_dialog; confirm_current(value, bridge, 0)
    task = bridge.take('读取教材框选原页'); value.reject(); bridge.succeed(task, run=True)
    assert task.task_id in bridge.cancelled and value.selection_requests is None
    assert not value._confirmed and value._page is None and not f.task_root.exists()


def test_last_image_edit_revokes_final_batch_submission(batch_dialog):
    value, bridge, _ = batch_dialog; confirm_current(value, bridge, 0)
    bridge.succeed(bridge.take('读取教材框选原页'), run=True); confirm_current(value, bridge, 1)
    value.purpose.setText('修改用途')
    assert len(value._confirmed) == 1 and not value.confirm.isEnabled() and not value.checked.isChecked()
    assert value.selection_requests is None


def test_narrow_batch_native_controls_and_actual_crop_remain_reachable(batch_dialog, app):
    value, bridge, _ = batch_dialog; value.resize(420, 900); app.processEvents(); select(value, bridge); app.processEvents()
    assert value.width() == 420 and value.splitter.orientation() == Qt.Orientation.Vertical
    for widget in (value.steps, value.choice, value.previous, value.next, value.preview_button, value.confirm, value.cancel):
        top = widget.mapTo(value, widget.rect().topLeft()); bottom = widget.mapTo(value, widget.rect().bottomRight())
        assert 0 <= top.x() <= bottom.x() < value.width() and 0 <= top.y() <= bottom.y() < value.height()
    assert value.crop_preview.has_image


def test_bookshelf_basket_survives_chapter_change_and_refresh_clears_stale_revision(page):
    value, bridge, f = page; value.asset_list.item(0).setCheckState(Qt.CheckState.Checked)
    picked = deepcopy(value._asset_checked); value.query.setText('不存在的章'); value.query.clear()
    value.tree.setCurrentItem(leaf(value, f.row['section_key']))
    assert value._asset_checked == picked and value.asset_list.item(0).checkState() == Qt.CheckState.Checked
    assert value.batch_crop_button.isEnabled() and not value.preview_button.isEnabled()
    f.asset['description'] = 'new source revision'; f.write_assets([f.asset]); value.refresh()
    bridge.succeed(bridge.take('读取教材书架'), run=True)
    assert not value._asset_checked and not value.batch_crop_button.isEnabled()
    assert '图表版本变化' in value.status.text()


@pytest.mark.parametrize('result', ['accepted', 'cancelled', 'changed-selection', 'refreshed', 'closed'])
def test_bookshelf_batch_handoff_checks_selection_and_live_context(page, monkeypatch, result):
    value, _, _ = page; value._check_visible_assets(True); seen = []
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as module
    class TestDialog:
        selection_requests = [{'synthetic': True}]
        def __init__(self, facade, tasks, choices, parent, *, allow_composition_handoff=False):
            assert allow_composition_handoff; seen.append(choices)
        def exec(self):
            if result == 'changed-selection': value._clear_selection()
            elif result == 'refreshed': value._load_request += 1
            elif result == 'closed': value.close()
            return QDialog.DialogCode.Rejected if result == 'cancelled' else QDialog.DialogCode.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'TextbookBatchCropDialog', TestDialog)
    value.preparation_images_requested.connect(lambda x: seen.append(x)); value.batch_crop_button.click()
    assert len(seen) == (2 if result == 'accepted' else 1)
    assert set(seen[0][0]['selection']) == {'visual_asset_id', 'revision'}


def test_personal_book_queue_survives_chapter_edit_and_has_remove_clear_controls(panel, app):
    value, bridge, _ = panel; value.crop_page.setValue(4); value.queue_page_button.click()
    value.crop_page.setValue(2); value.queue_page_button.click(); value.queue_page_button.click()
    assert value._crop_pages == [4, 2]
    enter_chapter(value, bridge); assert value._crop_pages == [4, 2]
    value.tabs.setCurrentIndex(value._graphics_tab); value.resize(420, 900); app.processEvents()
    scroll = value.tabs.widget(value._graphics_tab); scroll.ensureWidgetVisible(value.batch_crop_button); app.processEvents()
    bottom = value.batch_crop_button.mapTo(value, value.batch_crop_button.rect().bottomRight())
    assert 0 <= bottom.x() < value.width() and 0 <= bottom.y() < value.height()
    value.crop_queue.setCurrentRow(1); value.remove_page_button.click(); assert value._crop_pages == [4]
    value.clear_pages_button.click(); assert not value._crop_pages and not value.batch_crop_button.isEnabled()


@pytest.mark.parametrize('result', ['accepted', 'cancelled', 'changed-book', 'changed-queue', 'closed'])
def test_personal_batch_handoff_checks_book_revision_and_queue(panel, monkeypatch, result):
    value, _, _ = panel; value.queue_page_button.click(); value.crop_page.setValue(4); value.queue_page_button.click(); seen = []
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as module
    class TestDialog:
        selection_requests = [{'synthetic': True}]
        def __init__(self, facade, tasks, choices, parent, *, allow_composition_handoff=False):
            assert allow_composition_handoff; seen.append(choices)
        def exec(self):
            if result == 'changed-book': value.deactivate()
            elif result == 'changed-queue': value._clear_crop_pages()
            elif result == 'closed': value.close()
            return QDialog.DialogCode.Rejected if result == 'cancelled' else QDialog.DialogCode.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'TextbookBatchCropDialog', TestDialog)
    value.images_requested.connect(lambda x: seen.append(x)); value.batch_crop_button.click()
    assert len(seen) == (2 if result == 'accepted' else 1)
    assert [c['selection']['visual_asset_id'][-1] for c in seen[0]] == ['1', '4']


def bind_batch(win, f, monkeypatch):
    bridge = _ManualBridge(); prep = win.preparation_page; prep.tasks = bridge
    # This manually scheduled import test controls its own queue. Recovery is
    # exercised separately; its wall-clock timer must not enqueue unrelated work.
    prep.recovery.timer.stop()
    service = TextbookPreparationCropService(f.root, win.facade.paths.task_root)
    monkeypatch.setattr(win.facade, 'import_textbook_preparation_crops', lambda choices, assets: service.confirm_batch(choices, assets))
    return prep, bridge


def test_main_window_complete_batch_adds_once_and_preserves_the_rest_of_lesson(window, crop_fixture, monkeypatch):
    win, app = window; f = crop_fixture; prep, bridge = bind_batch(win, f, monkeypatch)
    prep.topic.setText('原有课题'); prep.materials.setPlainText('原有材料'); prep.objective.setPlainText('原有目标')
    prep.image_assets_widget.set_image_input_mode('local_only'); before = deepcopy(prep._payload()); choices = batch(f)
    win.textbook_page.preparation_images_requested.emit(choices)
    assert win.stack.currentWidget() is prep and not prep.isEnabled() and prep.studio_busy()
    win.new_preparation(); assert prep._payload() == before
    bridge.succeed(bridge.take('批量添加教材备课图表'), run=True); app.processEvents()
    after = prep._payload(); assets = after.pop('image_assets')
    assert after == before and len(assets) == 2 and prep.isEnabled() and not prep.studio_busy()
    assert prep.image_assets_widget.image_input_mode() == 'local_only'
    assert not prep.import_textbook_images(choices) and not bridge.pending


@pytest.mark.parametrize('result', ['changed-form', 'failure', 'cancelled', 'partial-result', 'closed-page'])
def test_batch_failure_or_old_context_never_adds_a_partial_lesson(window, crop_fixture, monkeypatch, result):
    win, _ = window; f = crop_fixture; prep, bridge = bind_batch(win, f, monkeypatch)
    prep.topic.setText('原课题'); before = deepcopy(prep._payload()); assert prep.import_textbook_images(batch(f))
    task = bridge.take('批量添加教材备课图表')
    if result == 'changed-form': prep.topic.setText('另一次备课'); bridge.succeed(task, run=True)
    elif result == 'failure': bridge.fail(task, '后一张原页已变化')
    elif result == 'cancelled': prep._qt_task_cancelled(task.task_id, task.label); bridge.succeed(task, run=True)
    elif result == 'closed-page': prep.close(); bridge.succeed(task, run=True)
    else:
        payload = task.operation(); payload['image_assets'].pop(); bridge.succeed(task, payload)
    assert not prep.image_assets_widget.assets() and not prep.studio_busy() and prep.isEnabled()
    if result == 'changed-form': assert prep.topic.text() == '另一次备课'
    else: assert prep._payload() == before
