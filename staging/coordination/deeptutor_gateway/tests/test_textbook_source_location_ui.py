"""Explicit catalog relocation with minimal Qt owners and deferred callbacks.

No MainWindow, Facade, StateStore or Recovery object is constructed. The source
service is a protocol fake; its real PDF/pixel validation has separate tests.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QVBoxLayout, QWidget
from shiboken6 import delete, isValid

from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, block, enable_composition, new_composition, resource)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, new_design
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_location_dialog import TextbookSourceLocationDialog


@pytest.fixture(scope='session')
def app():
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    value = QApplication.instance() or QApplication([])
    value.setStyle('Fusion'); install_ui_font(value)
    return value


class DeferredTasks(QObject):
    task_finished = Signal(str)
    def __init__(self):
        super().__init__()
        self.jobs = []; self.cancelled = []; self.is_shutting_down = False
    def submit(self, label, operation, *, on_success, on_failure):
        identity = 'private-job-' + str(len(self.jobs))
        self.jobs.append(SimpleNamespace(id=identity, label=label, operation=operation,
            on_success=on_success, on_failure=on_failure))
        return identity
    def cancel(self, identity):
        self.cancelled.append(identity); return True
    def calculate(self, index=-1):
        return self.jobs[index].operation()
    def deliver(self, value, index=-1):
        job = self.jobs[index]
        job.on_success(value); self.task_finished.emit(job.id)
    def finish(self, index=-1):
        job = self.jobs[index]
        try: value = job.operation()
        except Exception as exc: job.on_failure(str(exc))
        else: job.on_success(value)
        self.task_finished.emit(job.id)


class Publication:
    def __init__(self, error=None):
        self.calls = 0; self.error = error
        self.receipt = {'linked': True, 'original_unchanged': True,
            'current_path': preview()['current_path']}
    def publish(self):
        self.calls += 1
        if self.error is not None: raise self.error
        return deepcopy(self.receipt)


def preview():
    return {'preview_id': 'p' * 64, 'title': '已登记的合成教材',
        'old_path': 'D:/旧位置/合成教材.pdf', 'current_path': 'E:/当前资料库/合成教材.pdf',
        'source_identity': {'visual_asset_id': 'same-catalog-identity', 'revision': 'r' * 64},
        'source_sha256': 'a' * 64}


@pytest.fixture
def backend():
    from integrations.deeptutor_shchem_v1 import desktop_textbook_source_locations
    return desktop_textbook_source_locations


@pytest.fixture
def link(app, monkeypatch, backend):
    tasks = DeferredTasks(); valid = [True]; checked = []
    item = {'id': 'frozen-resource', 'original': {'evidence': {'source_path': 'D:/原书.pdf'}}}
    assets = [{'asset_id': 'frozen-original-image'}]
    metadata = preview(); publication = Publication(); calls = []
    def current():
        checked.append(valid[0])
        if not valid[0]: raise ValueError('本课或插入位置已变化')
    def stage(facade, selected, images, *, expected_preview):
        calls.append((facade, deepcopy(selected), deepcopy(images), expected_preview))
        return publication
    monkeypatch.setattr(backend, 'stage_textbook_source_relocation', stage)
    facade = SimpleNamespace()
    dialog = TextbookSourceLocationDialog(facade, item, assets, metadata,
        tasks=tasks, current=current)
    yield SimpleNamespace(dialog=dialog, tasks=tasks, valid=valid, checked=checked,
        item=item, assets=assets, metadata=metadata, publication=publication,
        calls=calls, facade=facade, backend=backend)
    if isValid(dialog): dialog.reject(); dialog.deleteLater()
    tasks.deleteLater(); app.processEvents()


def test_confirmation_stages_then_publishes_once_without_changing_source(link):
    s = link; original = deepcopy((s.item, s.assets, s.metadata))
    s.dialog.confirm_button.click(); s.dialog.confirm_link()
    assert len(s.tasks.jobs) == 1 and not s.calls and s.publication.calls == 0
    assert not s.dialog.confirm_button.isEnabled() and s.dialog.cancel_button.isEnabled()
    candidate = s.tasks.calculate()
    assert s.publication.calls == 0
    s.tasks.deliver(candidate)
    assert s.publication.calls == 1 and s.dialog.linked
    assert s.dialog.result() == QDialog.DialogCode.Accepted
    assert s.dialog.status.text() == '已关联当前原书位置，正在打开原页'
    assert s.calls == [(s.facade, original[0], original[1], original[2]['preview_id'])]
    assert (s.item, s.assets, s.metadata) == original
    s.tasks.jobs[0].on_success(candidate)
    assert s.publication.calls == 1  # Repeated/late queued delivery is inert.


def test_preview_inputs_are_detached_before_the_worker_starts(link):
    s = link; original = deepcopy((s.item, s.assets, s.metadata))
    s.item['original']['evidence']['source_path'] = 'unrelated.pdf'
    s.assets.append({'asset_id': 'unrelated'}); s.metadata['preview_id'] = 'changed'
    s.dialog.confirm_link(); s.tasks.finish()
    assert s.calls[0][1:] == (original[0], original[1], original[2]['preview_id'])


def test_cancel_without_confirm_does_not_stage_or_publish(link):
    s = link; s.dialog.cancel_button.click(); s.dialog.confirm_link()
    assert not s.tasks.jobs and not s.calls and not s.dialog.linked
    assert s.publication.calls == 0 and s.dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize('close', ['reject', 'close'])
def test_cancel_pending_stage_ignores_late_success(link, app, close):
    s = link; s.dialog.confirm_link(); candidate = s.tasks.calculate()
    s.dialog.show(); app.processEvents()
    getattr(s.dialog, close)(); s.tasks.deliver(candidate)
    assert s.tasks.cancelled == [s.tasks.jobs[0].id]
    assert s.publication.calls == 0 and not s.dialog.linked


def test_changed_scope_before_confirmation_cannot_start_worker(link):
    s = link; s.valid[0] = False; s.dialog.confirm_button.click()
    assert not s.tasks.jobs and s.publication.calls == 0
    assert s.dialog.result() == QDialog.DialogCode.Rejected


def test_changed_scope_after_stage_cannot_publish(link):
    s = link; s.dialog.confirm_link(); candidate = s.tasks.calculate()
    s.valid[0] = False; s.tasks.deliver(candidate)
    assert s.publication.calls == 0 and not s.dialog.linked
    assert '已变化' in s.dialog.status.text()


def test_guard_timer_cancels_waiting_scope_and_late_callback(link):
    s = link; s.dialog.confirm_link(); candidate = s.tasks.calculate()
    s.valid[0] = False; assert not s.dialog._guard_current()
    s.tasks.deliver(candidate)
    assert s.publication.calls == 0 and s.tasks.cancelled == [s.tasks.jobs[0].id]


@pytest.mark.parametrize('failure', ['stage', 'publish', 'submit'])
def test_failed_check_keeps_dialog_unlinked_and_can_retry(link, monkeypatch, failure):
    s = link
    def fail(*_args, **_kwargs): raise ValueError('原书或位置核对记录已变化')
    if failure == 'stage': monkeypatch.setattr(s.backend, 'stage_textbook_source_relocation', fail)
    elif failure == 'publish': s.publication.error = OSError('原书或位置核对记录已变化')
    else: monkeypatch.setattr(s.tasks, 'submit', fail)
    s.dialog.confirm_link()
    if s.tasks.jobs: s.tasks.finish()
    assert not s.dialog.linked and s.dialog.confirm_button.isEnabled()
    assert '已变化' in s.dialog.status.text()
    assert s.publication.calls == (1 if failure == 'publish' else 0)


def test_finished_without_result_invalidates_late_success_and_allows_retry(link):
    s = link; s.dialog.confirm_link(); candidate = s.tasks.calculate()
    s.tasks.task_finished.emit(s.tasks.jobs[0].id)
    assert s.dialog.confirm_button.isEnabled() and '未完成' in s.dialog.status.text()
    s.tasks.jobs[0].on_success(candidate)
    assert s.publication.calls == 0
    s.dialog.confirm_link(); s.tasks.finish()
    assert s.publication.calls == 1 and s.dialog.linked and len(s.tasks.jobs) == 2


def test_old_failure_and_finish_cannot_end_a_new_attempt(link):
    s = link; s.dialog.confirm_link(); old = s.tasks.jobs[0]
    old.on_failure('可重试核对'); s.dialog.confirm_link()
    old.on_failure('迟到错误'); s.tasks.task_finished.emit(old.id)
    assert not s.dialog.confirm_button.isEnabled()
    s.tasks.finish(); assert s.dialog.linked and s.publication.calls == 1


@pytest.mark.parametrize('receipt', [None, {}, {'linked': False},
    {'linked': 'true', 'original_unchanged': True, 'current_path': preview()['current_path']},
    {'linked': True, 'original_unchanged': False, 'current_path': preview()['current_path']},
    {'linked': True, 'original_unchanged': True, 'current_path': 'E:/另一来源.pdf'}])
def test_incomplete_publication_receipt_does_not_claim_success(link, receipt):
    s = link; s.publication.receipt = receipt
    s.dialog.confirm_link(); s.tasks.finish()
    assert s.publication.calls == 1 and not s.dialog.linked
    assert '结果不完整' in s.dialog.status.text()


def test_deleted_dialog_ignores_queued_publication(link):
    s = link; s.dialog.confirm_link(); candidate = s.tasks.calculate()
    delete(s.dialog); s.tasks.jobs[0].on_success(candidate)
    assert s.publication.calls == 0


def test_narrow_dialog_keeps_both_actions_reachable(link, app):
    s = link; s.dialog.resize(360, 390); s.dialog.show(); app.processEvents()
    assert s.dialog.width() == 360
    for button in (s.dialog.cancel_button, s.dialog.confirm_button):
        assert button.isVisible()
        assert s.dialog.rect().contains(button.mapTo(s.dialog, QPoint(0, 0)))
        assert s.dialog.rect().contains(button.mapTo(s.dialog, button.rect().bottomRight()))
    assert s.dialog.old_path.text() == s.metadata['old_path']
    assert s.dialog.current_path.text() == s.metadata['current_path']


class MinimalPanel(LessonCompositionPanel):
    """Use the actual original-page slot and scope guards without painting pages."""
    def __init__(self, owner, parent):
        QWidget.__init__(self, parent)
        self.owner = owner; self.selected = None
        self._insertion_epoch = 0; self._material_selection_epoch = 0; self._fitted = True
        self._transaction_scopes = set()
        self.insertion_position = QComboBox(self)
        self.insertion_position.addItem('末尾', 'append')
        self.insertion_position.addItem('所选之后', 'after')
        self.insertion_position.currentIndexChanged.connect(self.insertion_position_changed)
    def resizeEvent(self, event): QWidget.resizeEvent(self, event)


@pytest.mark.parametrize('change', ['insertion', 'superseded'])
def test_minimal_panel_invalidates_real_frozen_scope_without_changing_source(desk, change):
    from integrations.deeptutor_shchem_v1.desktop_workbench.composition_image_batch_entry import FrozenCompositionScope
    s = desk
    before = deepcopy((s.crop, s.assets, s.owner.history.value,
                       s.owner.history.back, s.owner.history.forward))
    insertion = s.panel.freeze_insertion()
    scope = FrozenCompositionScope(s.panel, insertion)
    try:
        assert scope in s.panel._transaction_scopes
        scope.check()
        change_scope(s, change)
        assert scope.revoked.is_set()
        with pytest.raises(ValueError, match='读取期间变化'):
            scope.check()
        assert (s.crop, s.assets, s.owner.history.value,
                s.owner.history.back, s.owner.history.forward) == before
    finally:
        scope.close()
    assert scope not in s.panel._transaction_scopes


@pytest.fixture
def desk(app, monkeypatch, backend):
    asset = {'asset_id': 'IMG-' + 'a' * 64, 'sha256': 'a' * 64}
    crop = resource('textbook_image', '合成教材裁片', [block('image', 'material', asset['asset_id'])],
        {'source_path': 'D:/原书/合成教材.pdf', 'source_sha256': 'b' * 64,
         'source_revision': 'c' * 64, 'pdf_pages': [3], 'printed_pages': 'unknown'})
    composition = new_composition('16:9')
    composition = add_resource(composition, crop,
        {'page_id': composition['pages'][0], 'x': 12, 'y': 12, 'width': 296, 'height': 156})
    design = enable_composition(new_design({'objective': '保持来源与课堂位置'}))
    design['composition'] = composition
    assets = [deepcopy(asset)]; tasks = DeferredTasks(); calls = []; previews = []
    context = {'selection': {'visual_asset_id': 'same-source', 'revision': 'c' * 64},
        'binding_sha256': 'd' * 64, 'source': {'source_name': '合成教材'}}
    def read(item, images, **kwargs):
        calls.append((deepcopy(item), deepcopy(images), kwargs))
        if len(calls) == 1: raise backend.TextbookSourceRelocationRequired('请核对新位置')
        return deepcopy(context)
    def make_preview(facade, item, images):
        previews.append((facade, deepcopy(item), deepcopy(images))); return preview()
    monkeypatch.setattr(backend, 'preview_textbook_source_relocation', make_preview)
    owner = SimpleNamespace(history=DesignHistory(design), tasks=tasks, status=QLabel(),
        facade=SimpleNamespace(preparation_textbook_crop_original=read),
        page=SimpleNamespace(image_assets_widget=SimpleNamespace(assets=lambda: deepcopy(assets))))
    window = QDialog(); panel = MinimalPanel(owner, window); panel.selected = crop['id']
    QVBoxLayout(window).addWidget(panel)
    def run(label, operation, success, *, cancellable=False):
        assert cancellable
        owner.status.setText(label)
        return tasks.submit(label, operation, on_success=success, on_failure=owner.status.setText)
    owner.run = run
    yield SimpleNamespace(owner=owner, panel=panel, window=window, assets=assets, crop=crop,
        calls=calls, previews=previews, tasks=tasks, context=context, backend=backend)
    window.close(); window.deleteLater(); owner.status.deleteLater(); tasks.deleteLater(); app.processEvents()


def fake_dialogs(monkeypatch, *, linked=True, mutate=None, section=False):
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_source_location_dialog as location_ui
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_crop_source_dialog as reader_ui
    locations = []; readers = []
    class Location(QDialog):
        def __init__(self, facade, item, assets, metadata, parent=None, *, tasks, current):
            super().__init__(parent)
            self.args = deepcopy((item, assets, metadata)); self.linked = linked
            self.current = current; locations.append(self)
        def exec(self):
            if mutate is not None: mutate()
            return QDialog.DialogCode.Accepted if self.linked else QDialog.DialogCode.Rejected
    class Reader(QDialog):
        def __init__(self, facade, item, assets, context, parent=None, *, current):
            super().__init__(parent)
            self.args = deepcopy((item, assets, context)); self.current = current
            self.section_materials_requested = section; readers.append(self)
        def exec(self): self.current(); return QDialog.DialogCode.Rejected
    monkeypatch.setattr(location_ui, 'TextbookSourceLocationDialog', Location)
    monkeypatch.setattr(reader_ui, 'TextbookCropSourceDialog', Reader)
    return locations, readers


def test_typed_relocation_previews_then_reopens_the_unchanged_reader(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    before = deepcopy((s.owner.history.value, s.assets, s.owner.history.back, s.owner.history.forward))
    s.panel.read_selected_textbook_original()
    assert not locations and not readers and not s.previews
    s.tasks.finish(0)
    assert len(locations) == 1 and not readers and len(s.tasks.jobs) == 2
    assert s.owner.status.text() == '已关联当前原书位置，正在打开原页'
    assert locations[0].args == (s.crop, s.assets, preview())
    s.tasks.finish(1)
    assert len(readers) == 1 and readers[0].args == (s.crop, s.assets, s.context)
    assert s.calls == [(s.crop, s.assets, {}), (s.crop, s.assets, {})]
    assert before == (s.owner.history.value, s.assets, s.owner.history.back, s.owner.history.forward)


def test_cancel_location_preview_does_not_reopen_reader(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch, linked=False)
    before = deepcopy(s.owner.history.value)
    s.panel.read_textbook_crop_original(); s.tasks.finish()
    assert len(locations) == 1 and not readers and len(s.tasks.jobs) == 1
    assert s.owner.history.value == before and not s.owner.history.back


@pytest.mark.parametrize('error', [ValueError('原页回执缺失'), OSError('来源目录未登记')])
def test_untyped_source_failure_uses_existing_error_path(desk, monkeypatch, error):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    def read(*_args): raise error
    s.owner.facade.preparation_textbook_crop_original = read
    s.panel.read_textbook_crop_original(); s.tasks.finish()
    assert not s.previews and not locations and not readers
    assert s.owner.status.text() == str(error)


def change_scope(s, change):
    if change == 'course':
        modified = deepcopy(s.owner.history.value)
        modified['objectives'][0]['text'] = '后来的课程编辑'; s.owner.history.put(modified)
    elif change == 'selection': s.panel.selected = None
    elif change == 'images': s.assets[0]['sha256'] = 'f' * 64
    elif change == 'insertion': s.panel.insertion_position.setCurrentIndex(1)
    elif change == 'superseded': s.panel.invalidate_material_insertion()
    elif change == 'stop': s.owner._stopping = True
    elif change == 'closing': s.owner._close_when_finished = True
    elif change == 'window_closed': s.window.reject()
    else: s.tasks.is_shutting_down = True


@pytest.mark.parametrize('change', ['course', 'selection', 'images', 'insertion', 'superseded',
    'stop', 'closing', 'window_closed', 'shutdown'])
def test_stale_preview_result_cannot_open_relocation_or_reader(desk, monkeypatch, change):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    s.panel.read_textbook_crop_original(); value = s.tasks.calculate()
    change_scope(s, change); before = deepcopy((s.owner.history.value, s.assets))
    s.tasks.deliver(value)
    assert not locations and not readers and len(s.tasks.jobs) == 1
    assert before == (s.owner.history.value, s.assets)


@pytest.mark.parametrize('change', ['course', 'insertion', 'window_closed'])
def test_scope_changed_inside_location_dialog_cannot_queue_reopen(desk, monkeypatch, change):
    s = desk; locations, readers = fake_dialogs(monkeypatch, mutate=lambda: change_scope(s, change))
    s.panel.read_textbook_crop_original(); s.tasks.finish()
    assert len(locations) == 1 and not readers and len(s.tasks.jobs) == 1


def test_changed_scope_after_link_before_reader_callback_cannot_open_reader(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    s.panel.read_textbook_crop_original(); s.tasks.finish(0)
    result = s.tasks.calculate(1); change_scope(s, 'course'); s.tasks.deliver(result, 1)
    assert len(locations) == 1 and not readers


def test_existing_same_section_action_keeps_initial_insertion_scope(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch, section=True)
    chosen = []; s.panel.choose_section_materials = lambda *args: chosen.append(deepcopy(args))
    s.panel.read_textbook_crop_original(); insertion = s.panel._material_selection_epoch
    s.tasks.finish(0); s.tasks.finish(1)
    assert len(locations) == len(readers) == len(chosen) == 1
    assert chosen[0][0:2] == (s.crop, s.assets)
    assert chosen[0][3]['request_epoch'] == insertion
    assert chosen[0][3]['anchor_resource_id'] == s.crop['id']


def test_deleted_panel_ignores_initial_queued_result(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    s.panel.read_textbook_crop_original(); result = s.tasks.calculate()
    delete(s.panel); s.tasks.deliver(result)
    assert not locations and not readers and len(s.tasks.jobs) == 1


@pytest.mark.parametrize('change', ['none', 'course', 'window_closed'])
def test_real_location_dialog_obeys_the_actual_panel_scope(desk, monkeypatch, change):
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_source_location_dialog as location_ui
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    monkeypatch.setattr(location_ui, 'TextbookSourceLocationDialog', TextbookSourceLocationDialog)
    publication = Publication(); dialogs = []
    monkeypatch.setattr(s.backend, 'stage_textbook_source_relocation', lambda *_a, **_kw: publication)
    def execute(dialog):
        dialogs.append(dialog); dialog.confirm_button.click()
        candidate = s.tasks.calculate(1)
        if change != 'none': change_scope(s, change)
        s.tasks.deliver(candidate, 1)
        if not dialog.linked: dialog.reject()
        return dialog.result()
    monkeypatch.setattr(TextbookSourceLocationDialog, 'exec', execute)
    before = deepcopy((s.owner.history.value, s.assets))
    s.panel.read_textbook_crop_original(); s.tasks.finish(0)
    assert len(dialogs) == 1 and not locations
    if change == 'none':
        assert publication.calls == 1 and len(s.tasks.jobs) == 3
        s.tasks.finish(2); assert len(readers) == 1
        assert before == (s.owner.history.value, s.assets)
    else:
        assert publication.calls == 0 and not readers and len(s.tasks.jobs) == 2


def test_post_link_reader_failure_does_not_claim_the_page_opened(desk, monkeypatch):
    s = desk; locations, readers = fake_dialogs(monkeypatch)
    s.panel.read_textbook_crop_original(); s.tasks.finish(0)
    def changed(*_args): raise ValueError('完整原页像素复核未通过')
    s.owner.facade.preparation_textbook_crop_original = changed
    s.tasks.finish(1)
    assert len(locations) == 1 and not readers
    assert s.owner.status.text() == '完整原页像素复核未通过'
