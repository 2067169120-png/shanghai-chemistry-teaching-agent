"""Native crop review to one canvas transaction, without MainWindow/recovery."""
from copy import deepcopy
from uuid import uuid4

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QDialog

from test_composition_render import app
from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture, disk_bytes
from test_textbook_preparation_crops import crop_fixture, request
from test_textbook_preparation_crops_ui import select
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_preparation import DesktopPreparationError
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_preparation import StudioPreparationPage
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_preparation_crop_dialog import TextbookPreparationCropDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_batch_crop_dialog import TextbookBatchCropDialog
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled


class DeferredTasks(QObject):
    task_finished = Signal(str)
    task_cancelled_detail = Signal(str, str, str)
    task_cancelled = Signal(str, str)
    is_shutting_down = False
    def __init__(self):
        super().__init__(); self.pending = {}; self.stopped = set()
    def submit(self, label, operation, *, on_success=None, on_failure=None):
        key = uuid4().hex; self.pending[key] = (label, operation, on_success, on_failure)
        return key
    def cancel(self, key): self.stopped.add(key); return True
    def calculate(self, key): return self.pending[key][1]()
    def deliver(self, key, value):
        label, _, done, _ = self.pending.pop(key)
        if key not in self.stopped: done(value)
        else:
            self.task_cancelled_detail.emit(key, label, '已停止')
            self.task_cancelled.emit(key, label)
        self.task_finished.emit(key)
    def fail(self, key, message):
        _, _, _, failed = self.pending.pop(key); failed(message); self.task_finished.emit(key)


@pytest.fixture
def desk(app, crop_fixture):
    f = crop_fixture; tasks = DeferredTasks()
    page = StudioPreparationPage(f.facade, tasks)
    page._availability_timer.stop()
    assert page.recovery is None
    page.topic.setText('现有教师课题'); page.objective.setPlainText('观察、比较并解释实验现象')
    page.materials.setPlainText('保留教师已有材料全文')
    yield page, tasks, f, app
    if page._material_handoff_dialog is not None:
        page._material_handoff_dialog.reject()
    page.close(); page.deleteLater(); app.processEvents()


def batch_requests(f):
    first = request(f)
    second = deepcopy(first); second['bounds']['right'] += 30
    second['caption'] = '合成第二图'; second['purpose'] = '比較第二图中的实验条件'
    value = f.crops.preview(second['selection'], second['page_revision'], second['bounds'],
                           second['caption'], second['purpose'])
    second['expected_preview_revision'] = value['preview_revision']
    second['expected_image_sha256'] = value['image_asset']['sha256']
    return [first, second]


@pytest.mark.parametrize('mode', ['assets', 'composition'])
def test_single_two_explicit_destinations_keep_strict_crop_contract(app, crop_fixture, mode):
    f = crop_fixture; tasks = _ManualBridge(); before = disk_bytes(f.root)
    dialog = TextbookPreparationCropDialog(f.facade, tasks, f.selection, allow_composition_handoff=True)
    dialog.show(); tasks.succeed(tasks.take('读取教材框选原页'), run=True); select(dialog, tasks)
    assert dialog.confirm.text() == '只入备课图库' and dialog.compose.text() == '加入并编排'
    assert not dialog.compose.isEnabled()
    dialog.checked.setChecked(True)
    (dialog.compose if mode == 'composition' else dialog.confirm).click()
    from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import CROP_REQUEST_FIELDS
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.handoff_mode == mode
    assert set(dialog.selection_request) == CROP_REQUEST_FIELDS
    assert disk_bytes(f.root) == before and not f.task_root.exists()
    dialog.deleteLater(); app.processEvents()


def test_canvas_internal_default_has_no_recursive_composition_action(app, crop_fixture):
    tasks = _ManualBridge(); dialog = TextbookPreparationCropDialog(crop_fixture.facade, tasks, crop_fixture.selection)
    assert dialog.compose.isHidden() and dialog.confirm.text() == '加入当前备课'
    dialog.reject(); dialog.deleteLater(); app.processEvents()


def test_batch_composition_requires_all_confirmations_and_an_explicit_final_click(app, crop_fixture):
    f = crop_fixture; tasks = _ManualBridge()
    dialog = TextbookBatchCropDialog(f.facade, tasks,
        [{'selection': f.selection, 'label': f'合成第{i}图'} for i in (1, 2)], allow_composition_handoff=True)
    dialog.show(); tasks.succeed(tasks.take('读取教材框选原页'), run=True)
    for i in range(2):
        assert not dialog.compose.isEnabled()
        select(dialog, tasks); dialog.caption.setText(f'合成第{i}图')
        dialog.preview_button.click(); tasks.succeed(tasks.take('核对教材裁片'), run=True)
        dialog.checked.setChecked(True); dialog.confirm.click()
        if i == 0: tasks.succeed(tasks.take('读取教材框选原页'), run=True)
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.selection_requests is None
    assert dialog.compose.isEnabled() and dialog.confirm.text() == '只入备课图库（2张）'
    dialog.compose.click()
    assert dialog.handoff_mode == 'composition' and len(dialog.selection_requests) == 2
    assert not f.task_root.exists()
    dialog.deleteLater(); app.processEvents()


@pytest.mark.parametrize('batch', [False, True])
def test_native_direct_canvas_has_no_second_picker_and_one_undo_redo(desk, monkeypatch, batch):
    page, tasks, f, app = desk; rows = batch_requests(f) if batch else [request(f)]
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourcePicker
    monkeypatch.setattr(ResourcePicker, 'exec', lambda *_: pytest.fail('second picker opened'))
    calls = []; actual = f.facade.import_textbook_preparation_crops
    monkeypatch.setattr(f.facade, 'import_textbook_preparation_crops',
        lambda selections, assets: (calls.append(deepcopy(selections)), actual(selections, assets))[1])
    assert page.import_textbook_crops_for_composition(rows)
    dialog = page._material_handoff_dialog; before = deepcopy(dialog.history.value); back = len(dialog.history.back)
    key = dialog._task; tasks.deliver(key, tasks.calculate(key)); app.processEvents()
    after = deepcopy(dialog.history.value)
    assert len(calls) == 1 and calls[0] == rows
    assert dialog.tabs.currentWidget() is dialog.composition_panel
    assert len(after['composition']['resources']) == len(rows) and len(dialog.history.back) == back + 1
    assert dialog.composition_panel.layout_result is not None and not dialog.composition_panel.layout_result['errors']
    assert len(dialog.composition_panel.image_data()) == len(rows)
    assert page.topic.text() == '现有教师课题' and page.materials.toPlainText() == '保留教师已有材料全文'
    assert all(r['original']['kind'] == 'textbook_image' for r in after['composition']['resources'])
    assert [r['title'] for r in after['composition']['resources']] == [r['caption'] for r in rows]
    assert [r['blocks'][1]['value'] for r in after['composition']['resources']] == [r['purpose'] for r in rows]
    dialog.travel(False); assert dialog.history.value == before
    dialog.travel(True); assert dialog.history.value == after
    assert page.last_material_handoff['status'] == 'added' and not dialog.busy


@pytest.mark.parametrize('change', ['payload', 'history', 'assets', 'target-id', 'target-signal', 'close', 'stop', 'shutdown'])
def test_late_success_cannot_replace_changed_target(desk, change):
    page, tasks, f, app = desk; assert page.import_textbook_crops_for_composition([request(f)])
    dialog = page._material_handoff_dialog; key = dialog._task; value = tasks.calculate(key)
    if change == 'payload': page.topic.setText('刚修改的新课题')
    elif change == 'history':
        newer = deepcopy(dialog.history.value); newer['classroom_notes'] = True
        dialog.history.put(newer); dialog.sync_page()
    elif change == 'assets': page.image_assets_widget.set_assets(value['image_assets'])
    elif change == 'target-id': page._editor_draft_id = 'different-same-content-draft'
    elif change == 'target-signal': page.preparation_target_changed.emit()
    elif change == 'close': dialog._close_when_finished = True
    elif change == 'stop': dialog._stopping = True
    else: tasks.is_shutting_down = True
    preserved_design = deepcopy(page._lesson_design); preserved_assets = page.image_assets_widget.assets()
    tasks.deliver(key, value); app.processEvents()
    assert not (page._lesson_design or {}).get('composition', {}).get('resources')
    assert page.image_assets_widget.assets() == preserved_assets
    if change in {'history', 'target-id'}: assert page._lesson_design == preserved_design
    if change == 'payload': assert page.topic.text() == '刚修改的新课题'
    assert page.last_material_handoff['status'] == 'not_added' and not page.studio_busy()


def test_cancel_zero_lesson_changes_and_empty_initializer_restored(desk):
    page, tasks, f, app = desk; before = deepcopy(page._payload())
    assert page.import_textbook_crops_for_composition([request(f)])
    dialog = page._material_handoff_dialog; key = dialog._task
    dialog.reject(); tasks.deliver(key, None); app.processEvents()
    assert page._payload() == before and page.last_material_handoff['status'] == 'not_added'
    assert not f.task_root.exists() and not page.studio_busy()


def test_source_failure_and_worker_revocation_preserve_existing_lesson(desk):
    page, tasks, f, app = desk; before = deepcopy(page._payload())
    assert page.import_textbook_crops_for_composition([request(f)])
    key = page._material_handoff_dialog._task
    original = f.book.read_bytes(); choice = request(f)
    f.book.write_bytes(original + b'\n% synthetic source changed\n')
    with pytest.raises(DesktopPreparationError): tasks.calculate(key)
    tasks.fail(key, '教材原页变化'); app.processEvents()
    assert page._payload() == before and not f.task_root.exists()
    f.book.write_bytes(original)
    assert page.import_textbook_crops_for_composition([choice])
    key = page._material_handoff_dialog._task; page.preparation_target_changed.emit()
    with pytest.raises(ReadCancelled): tasks.calculate(key)
    tasks.fail(key, '当前草稿变化'); app.processEvents()
    assert page._payload() == before


def test_duplicate_preserves_edited_copy_and_different_purpose_remains(desk):
    page, tasks, f, app = desk; choice = request(f)
    assert page.import_textbook_crops_for_composition([choice]); d = page._material_handoff_dialog
    tasks.deliver(d._task, tasks.calculate(d._task)); app.processEvents()
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import edit_resource
    composition = d.composition_panel.composition(); first_id = composition['resources'][0]['id']
    d.composition_panel.commit(edit_resource(composition, first_id, title='教师已改写的课堂问题标题'))
    d.reject(); app.processEvents(); before = deepcopy(page._lesson_design)
    assert page.import_textbook_crops_for_composition([choice]); d = page._material_handoff_dialog
    count = len(d.history.back); tasks.deliver(d._task, tasks.calculate(d._task)); app.processEvents()
    assert d.history.value == before and len(d.history.back) == count
    assert page.last_material_handoff['status'] == 'duplicate'
    d.reject(); app.processEvents()
    other = deepcopy(choice); other['purpose'] = '用同一图讨论新的控制变量'
    preview = f.crops.preview(other['selection'], other['page_revision'], other['bounds'], other['caption'], other['purpose'])
    other['expected_preview_revision'] = preview['preview_revision']
    assert page.import_textbook_crops_for_composition([other]); d = page._material_handoff_dialog
    tasks.deliver(d._task, tasks.calculate(d._task)); app.processEvents()
    assert len(d.history.value['composition']['resources']) == 2
    assert len(page.image_assets_widget.assets()) == 1
    assert d.history.value['composition']['resources'][0] == before['composition']['resources'][0]


def test_original_source_rechecked_after_full_batch_copy(crop_fixture, monkeypatch):
    f = crop_fixture; requests = batch_requests(f); actual = f.facade.import_textbook_preparation_crops
    def copied_then_changed(rows, assets):
        result = actual(rows, assets)
        f.book.write_bytes(f.book.read_bytes() + b'\n% changed after copy\n')
        return result
    monkeypatch.setattr(f.facade, 'import_textbook_preparation_crops', copied_then_changed)
    with pytest.raises(DesktopFacadeError): sources.import_textbook_crops_for_composition(f.facade, requests, [], [])


@pytest.mark.parametrize('extra', ['handoff_mode', 'destination'])
def test_destination_never_enters_sealed_backend_request(desk, extra):
    page, tasks, f, _ = desk; before = deepcopy(page._payload()); choice = request(f); choice[extra] = 'composition'
    assert not page.import_textbook_crops_for_composition([choice])
    assert page._payload() == before and not tasks.pending


@pytest.mark.parametrize('stage', ['constructor', 'submit'])
def test_failed_empty_dialog_initialization_is_restored(desk, monkeypatch, stage):
    page, tasks, f, app = desk; before = deepcopy(page._payload())
    if stage == 'constructor':
        from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_design_dialog as module
        from integrations.deeptutor_shchem_v1.desktop_lesson_design import new_design
        def failed(owner):
            owner._lesson_design = new_design(owner._payload())
            raise ValueError('合成设计窗口初始化失败')
        monkeypatch.setattr(module, 'LessonDesignDialog', failed)
    else:
        monkeypatch.setattr(tasks, 'submit', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('合成任务提交失败')))
    assert not page.import_textbook_crops_for_composition([request(f)])
    app.processEvents()
    assert page._payload() == before and not page.studio_busy() and not f.task_root.exists()


@pytest.mark.parametrize('change', ['target', 'history', 'assets'])
def test_source_bytes_read_rechecks_target_before_commit(desk, monkeypatch, change):
    page, tasks, f, app = desk; assert page.import_textbook_crops_for_composition([request(f)])
    dialog = page._material_handoff_dialog; key = dialog._task; value = tasks.calculate(key)
    actual = f.facade.preparation_image_bytes; preserved = []
    def read_then_changed(asset):
        raw = actual(asset)
        if not preserved:
            if change == 'target':
                page._editor_draft_id = 'another-equal-content-draft'; page.preparation_target_changed.emit()
            elif change == 'history':
                history = deepcopy(dialog.history.value); history['classroom_notes'] = True
                dialog.history.put(history); dialog.sync_page()
            else:
                changed = deepcopy(value['image_assets']); changed[0]['caption'] = '教师刚修改的图题'
                page.image_assets_widget.set_assets(changed)
            preserved.append((deepcopy(page._lesson_design), page.image_assets_widget.assets()))
        return raw
    monkeypatch.setattr(f.facade, 'preparation_image_bytes', read_then_changed)
    tasks.deliver(key, value); app.processEvents()
    assert not (page._lesson_design or {}).get('composition', {}).get('resources')
    if change in {'history', 'target'}: assert page._lesson_design == preserved[0][0]
    if change == 'assets': assert page.image_assets_widget.assets() == preserved[0][1]
    assert page.last_material_handoff['status'] == 'not_added'


def test_partial_canvas_exception_restores_assets_and_all_history(desk, monkeypatch):
    page, tasks, f, app = desk; before = deepcopy(page._payload())
    assert page.import_textbook_crops_for_composition(batch_requests(f))
    dialog = page._material_handoff_dialog; key = dialog._task; value = tasks.calculate(key)
    def failed(result, expected, **kwargs):
        from integrations.deeptutor_shchem_v1.desktop_lesson_composition import add_resource, enable_composition
        page.image_assets_widget.set_assets(result['image_assets'])
        changed = enable_composition(dialog.history.value); composition = changed['composition']
        for row in result['resources']:
            composition = add_resource(composition, row, {'page_id': composition['pages'][0],
                'x': 12, 'y': 12, 'width': 186, 'height': 273})
        changed['composition'] = composition
        dialog.history.put(changed); page._lesson_design = deepcopy(changed)
        raise ValueError('合成排版失败')
    monkeypatch.setattr(dialog.composition_panel, 'add_result', failed)
    tasks.deliver(key, value); app.processEvents()
    assert page._payload() == before and page.last_material_handoff['status'] == 'not_added'
    assert not page.studio_busy()


@pytest.mark.parametrize('batch', [False, True])
def test_narrow_native_both_destination_buttons_reachable(app, crop_fixture, batch):
    f = crop_fixture; tasks = _ManualBridge()
    if batch:
        dialog = TextbookBatchCropDialog(f.facade, tasks, [{'selection': f.selection, 'label': '合成图'}],
                                         allow_composition_handoff=True)
    else: dialog = TextbookPreparationCropDialog(f.facade, tasks, f.selection, allow_composition_handoff=True)
    dialog.resize(420, 900); dialog.show(); tasks.succeed(tasks.take('读取教材框选原页'), run=True)
    select(dialog, tasks); app.processEvents()
    assert dialog.width() == 420 and dialog.splitter.orientation() == Qt.Orientation.Vertical
    for button in (dialog.confirm, dialog.compose, dialog.preview_button, dialog.cancel):
        assert button.isVisible() and dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    dialog.reject(); dialog.deleteLater(); app.processEvents()
