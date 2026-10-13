"""Detached batch rescue using real widgets/publication and private image files.

No MainWindow, Recovery, StateStore or Facade constructor is used.  A tiny
owner runs the product Studio transaction and native composition/resource UI.
"""
import os
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from threading import RLock, Thread, get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, Signal, QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget, QLabel, QLineEdit, QPlainTextEdit, QTabWidget, QVBoxLayout

from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as handoff
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource, enable_composition
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, new_design, digest
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore, image_info, normalize_image_assets
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_preparation import StudioPreparationPage
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    install_ui_font(app)
    return app


class Assets(QObject):
    assets_changed = Signal()
    mode_changed = Signal()
    def __init__(self, values):
        super().__init__(); self.values = deepcopy(values)
    def assets(self): return deepcopy(self.values)
    def set_assets(self, values):
        self.values = deepcopy(values); self.assets_changed.emit()


class DeferredTasks(QObject):
    task_finished = Signal(str)
    task_cancelled_detail = Signal(str, str, str)
    is_shutting_down = False
    def __init__(self):
        super().__init__(); self.jobs = {}; self.stopped = set()
    def submit(self, label, operation, *, on_success=None, on_failure=None):
        identity = uuid4().hex
        self.jobs[identity] = (operation, on_success, on_failure)
        return identity
    def calculate(self, identity):
        # Backends run away from the GUI, just as DesktopTaskBridge does.
        box = {}
        def run():
            try: box['result'] = self.jobs[identity][0]()
            except BaseException as exc: box['error'] = exc
        thread = Thread(target=run); thread.start(); thread.join(10)
        assert not thread.is_alive()
        if 'error' in box: raise box['error']
        return box['result']
    def deliver(self, identity, value):
        _, success, _ = self.jobs.pop(identity)
        if identity not in self.stopped: success(value)
        self.task_finished.emit(identity)
    def finish(self, identity):
        try: value = self.calculate(identity)
        except Exception as exc:
            _, _, failure = self.jobs.pop(identity)
            failure(str(exc)); self.task_finished.emit(identity)
        else: self.deliver(identity, value)
    def cancel(self, identity): self.stopped.add(identity); return True


class SafeDesignDialog(QDialog):
    """Only the in-memory panel owner, never the app's recovery constructor."""
    run = LessonDesignDialog.run
    def __init__(self, page):
        super().__init__(page)
        self.page, self.facade, self.tasks = page, page.facade, page.tasks
        self.history = DesignHistory(page._lesson_design)
        self.status = QLabel(); self._task = None
        self._stopping = self._close_when_finished = self._cancellable = self.busy = False
        self.tabs = QTabWidget(); self.composition_panel = LessonCompositionPanel(self)
        self.tabs.addTab(self.composition_panel, '编排')
        layout = QVBoxLayout(self); layout.addWidget(self.tabs); layout.addWidget(self.status)
        self.tasks.task_finished.connect(self.task_finished)
        self.sync_page()
    def sync_page(self):
        self.page._lesson_design = deepcopy(self.history.value)
        self.composition_panel.set_payload(self.page._payload())
    def set_busy(self, value): self.busy = value; self.tabs.setEnabled(not value)
    def task_finished(self, identity):
        if identity == self._task:
            self._task = None; self._stopping = False; self.set_busy(False)
    def travel(self, redo=False): self.history.travel(redo); self.sync_page()


class SafeStudioPage(QWidget):
    preparation_target_changed = Signal()
    material_handoff_finished = Signal(bool, str)
    _import_material_batch = StudioPreparationPage._import_material_batch
    def __init__(self, facade, tasks, assets):
        super().__init__(); self.facade, self.tasks = facade, tasks
        for name in ('topic', 'audience', 'output_kind', 'route', 'lesson_count', 'lesson_minutes'):
            setattr(self, name, QLineEdit('原有课堂'))
        for name in ('objective', 'materials', 'learning_detail', 'template_detail', 'strategy_detail'):
            setattr(self, name, QPlainTextEdit('原有完整材料'))
        self.image_assets_widget = Assets(assets); self.status = QLabel()
        self._lesson_design = enable_composition(new_design({'objective': '完整课堂讲解'}))
        self._lesson_busy = False; self._material_handoff_dialog = None
        self._material_handoff_epoch = 0; self._material_position_epoch = 0
        self._editor_draft_id = 'PRIVATE-TEST'; self.last_material_handoff = None
        self._form_baseline = self._payload()
    def _payload(self):
        return {'topic': self.topic.text(), 'objective': self.objective.toPlainText(),
            'materials': self.materials.toPlainText(), 'lesson_design': deepcopy(self._lesson_design),
            'image_assets': self.image_assets_widget.assets()}
    def studio_busy(self): return self._lesson_busy
    def take_material_insertion(self):
        return {'mode': 'end', 'anchor_resource_id': None, 'epoch': self._material_position_epoch}
    def check_material_insertion(self, insertion):
        if insertion['epoch'] != self._material_position_epoch: raise ValueError('插入位置变化')
    def panel_material_insertion(self, insertion):
        return {k: insertion[k] for k in ('mode', 'anchor_resource_id')}
    def refresh_material_insertion_targets(self): pass


def png(color):
    stream = BytesIO(); Image.new('RGB', (80, 40), color).save(stream, 'PNG')
    data = stream.getvalue(); hashed = sha256(data).hexdigest()
    asset = normalize_image_assets([{'asset_id': 'IMG-' + hashed, 'sha256': hashed,
        'caption': '私有测试原图', 'source': '合成测试', 'purpose': '完整范围', **image_info(data)}])[0]
    return asset, data


@pytest.fixture
def desk(app, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_design_dialog as dialog_module
    monkeypatch.setattr(dialog_module, 'LessonDesignDialog', SafeDesignDialog)
    old, old_data = png('blue'); new, new_data = png('green')
    store = PreparationImageStore(tmp_path / 'preparation-v1' / 'images')
    stored = store.import_bytes(old_data, old['caption'], old['source'], old['purpose'])
    assert stored == old
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path),
        _word_questions=lambda: SimpleNamespace(_lock=RLock()),
        preparation_image_bytes=store.load)
    rows = [resource('personal_text', '完整知识栏目', [
        block('text', 'personal', '2.结构\U00100c37以聚乙烯为例\U00100c3b'),
        block('text', 'personal', '全部适用条件和结论原样保留。'),
        block('image', 'personal', new['asset_id'])],
        {'source_path': '合成来源', 'source_sha256': 'unknown', 'source_revision': 'unknown'})]
    result = {'resources': rows, 'image_assets': [old, new]}
    state = {'source_current': True, 'recheck_threads': [], 'prepare_count': 0}
    def recheck():
        state['recheck_threads'].append(get_ident())
        latest = deepcopy(result)
        if not state['source_current']: latest['resources'][0]['title'] = '来源改版'
        return latest, {old['sha256']: old_data, new['sha256']: new_data}
    pub = handoff.MaterialPublication(facade, {}, [old], [], result,
        {old['sha256']: old_data, new['sha256']: new_data}, recheck=recheck)
    def prepare(*_): state['prepare_count'] += 1; return pub
    page = SafeStudioPage(facade, DeferredTasks(), [old])
    new_path = store.root / (new['sha256'] + '.image')
    old_path = store.root / (old['sha256'] + '.image')
    original = deepcopy(result); page_before = page._payload()
    yield SimpleNamespace(app=app, page=page, tasks=page.tasks, pub=pub, prepare=prepare,
        state=state, result=original, before=page_before, old=old, new=new,
        old_path=old_path, new_path=new_path, monkeypatch=monkeypatch)
    if page._material_handoff_dialog is not None: QDialog.reject(page._material_handoff_dialog)
    pub.discard(); page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


def start(s):
    assert s.page._import_material_batch({}, s.prepare, '核对测试完整选材')
    dialog = s.page._material_handoff_dialog
    identity = dialog._task; value = s.tasks.calculate(identity)
    assert s.new_path.exists()
    return dialog, identity, value


def drive_editor(s, dialog, initial_task, *, action='edit', mutate=None):
    errors, visited = [], []
    def drive():
        pending = dialog.composition_panel.pending_material_editor
        try:
            assert pending.objectName() == 'PendingMaterialBatchEditor'
            visited.append(pending)
            assert dialog.history.value == s.before['lesson_design']
            # The old worker may finish while this nested modal event loop runs.
            s.tasks.task_finished.emit(initial_task)
            assert s.page.last_material_handoff['status'] == 'reading'
            if mutate is not None: mutate()
            if action == 'cancel': pending.reject(); return
            if action == 'edit':
                def edit_resource():
                    editor = pending.active_resource_editor
                    try:
                        assert editor.result_value is None
                        editor.locked.setChecked(False)
                        editor.blocks.setCurrentIndex(0)
                        editor.body.set_source_text('2.结构：以聚乙烯为例')
                        editor.apply()
                        assert editor.result_value is not None, editor.status.text()
                    except BaseException as exc:
                        errors.append(exc); editor.reject()
                QTimer.singleShot(0, edit_resource)
                pending.edit_button.click()
            pending.apply()
        except BaseException as exc:
            errors.append(exc); pending.reject()
    QTimer.singleShot(0, drive)
    return errors, visited


def deliver_first(s, *, action='edit', mutate=None):
    dialog, identity, value = start(s)
    errors, visited = drive_editor(s, dialog, identity, action=action, mutate=mutate)
    s.tasks.deliver(identity, value)
    assert not errors, errors
    assert len(visited) == 1
    return dialog


def assert_aborted(s, dialog):
    assert s.page._lesson_design == s.before['lesson_design']
    assert dialog.history.value == s.before['lesson_design'] and not dialog.history.back
    assert s.page.image_assets_widget.assets() == [s.old]
    assert s.pub.result == s.result and not s.pub._accepted
    assert not s.new_path.exists() and s.old_path.exists()
    assert s.page.last_material_handoff['status'] == 'not_added'


def test_native_explicit_edit_rechecks_off_gui_and_commits_once(desk):
    s = desk; dialog = deliver_first(s)
    assert dialog.busy and dialog._task in s.tasks.jobs
    assert not dialog.history.back and s.pub.result == s.result
    assert len(s.state['recheck_threads']) == 2  # One publish, not a second publish.
    s.tasks.finish(dialog._task)
    assert s.pub._accepted and s.new_path.exists() and s.old_path.exists()
    assert s.state['prepare_count'] == 1 and len(s.state['recheck_threads']) == 3
    assert all(t != get_ident() for t in s.state['recheck_threads'])
    assert len(dialog.history.back) == 1 and not dialog.busy
    added = dialog.composition_panel.composition()['resources'][0]
    assert added['blocks'][0]['value'] == '2.结构：以聚乙烯为例'
    assert added['blocks'][1:] == s.result['resources'][0]['blocks'][1:]
    assert added['original'] == s.result['resources'][0]['original']
    assert '\U00100c37' in added['original']['blocks'][0]['value']
    assert s.pub.result == s.result
    dialog.travel(False); assert dialog.history.value == s.before['lesson_design']


def test_native_cancel_discards_private_images_and_no_history(desk):
    s = desk; dialog = deliver_first(s, action='cancel'); assert_aborted(s, dialog)
    assert len(s.state['recheck_threads']) == 2


def test_source_change_after_explicit_edit_aborts_full_batch(desk):
    s = desk; dialog = deliver_first(s)
    s.state['source_current'] = False; s.tasks.finish(dialog._task)
    assert_aborted(s, dialog)
    assert '来源' in s.page.last_material_handoff['message']


def test_second_layout_failure_does_not_reopen_or_drop_glyphs(desk):
    s = desk; dialog = deliver_first(s, action='keep')
    s.tasks.finish(dialog._task); assert_aborted(s, dialog)
    assert '字' in s.page.last_material_handoff['message']


def test_close_before_recheck_worker_prevents_commit_and_cleans_owned_images(desk):
    s = desk; dialog = deliver_first(s); pending = dialog._task
    QDialog.reject(dialog)
    assert not s.page.studio_busy()
    s.tasks.finish(pending)
    assert_aborted(s, dialog)


def test_changed_teacher_form_during_modal_is_retained_without_batch(desk):
    s = desk; dialog = deliver_first(s, action='keep', mutate=lambda: s.page.objective.setPlainText('教师后来修改的目标'))
    assert_aborted(s, dialog)
    assert s.page.objective.toPlainText() == '教师后来修改的目标'


def test_corrupt_picture_is_source_error_without_edit_offer(desk):
    s = desk; dialog, identity, result = start(s)
    s.page.facade.preparation_image_bytes = lambda _: b'not a picture'
    def forbidden(*_, **__): raise AssertionError('Source failures must not offer work edits')
    s.monkeypatch.setattr(dialog.composition_panel, 'edit_pending_materials', forbidden)
    s.tasks.deliver(identity, result)
    assert_aborted(s, dialog)


@pytest.mark.parametrize('foreign', [False, 'geometry', 'undo'], ids=['exact-receipt-rollback', 'later-geometry-preserved', 'later-undo-preserves-redo-image'])
def test_postcommit_refresh_exception_uses_exact_full_history_receipt(desk, foreign):
    s = desk; dialog = deliver_first(s)
    def sync_failure():
        if foreign == 'geometry':
            composition = dialog.history.value['composition']
            composition['placements'][composition['resources'][0]['id']]['x'] += 1
            s.page._lesson_design = deepcopy(dialog.history.value)
        elif foreign == 'undo':
            dialog.history.travel(False)
            s.page._lesson_design = deepcopy(dialog.history.value)
        raise AttributeError('测试提交后刷新异常')
    s.monkeypatch.setattr(dialog, 'sync_page', sync_failure)
    s.tasks.finish(dialog._task)
    if not foreign: assert_aborted(s, dialog)
    else:
        assert s.pub._accepted and s.new_path.exists() and s.old_path.exists()
        assert s.page.image_assets_widget.assets() == s.result['image_assets']
        referenced = dialog.history.value if foreign == 'geometry' else dialog.history.forward[-1]
        assert referenced['composition']['resources'][0]['id'] == s.result['resources'][0]['id']
        assert s.page._lesson_design == dialog.history.value


def test_successful_initial_layout_never_opens_work_copy_editor(desk):
    s = desk
    # Separate safe source publication, with no problem character to repair.
    result = deepcopy(s.result); result['resources'][0] = resource('personal_text', '普通素材',
        [block('text', 'personal', '完整条件与结论。')],
        {'source_path': '合成', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    pub = handoff.MaterialPublication(s.page.facade, {}, [s.old], [], result,
        s.pub._payloads.copy(), recheck=lambda: (deepcopy(result), {}))
    assert s.page._import_material_batch({}, lambda *_: pub, '正常选材')
    dialog = s.page._material_handoff_dialog; identity = dialog._task
    s.monkeypatch.setattr(dialog.composition_panel, 'edit_pending_materials', lambda *_: pytest.fail('unneeded editor'))
    s.tasks.finish(identity)
    assert pub._accepted and len(dialog.history.back) == 1


# This fixture is a native Panel + SimpleNamespace owner with private tmp_path
# images. Its imports contain no application window or persistent state fixture.
from test_section_material_panel_handoff_ui import panel_desk, start as section_start


@pytest.mark.parametrize('outcome', ['accept', 'cancel', 'source_changed'])
def test_section_entry_uses_same_detached_editor_and_worker_contract(panel_desk, outcome):
    s = panel_desk; owner = s.owner; publication = s.publication
    original = publication.result['resources'][0]
    publication.result['resources'][0] = resource('knowledge_note', original['title'],
        [block('text', 'knowledge', '2.结构\U00100c37以聚乙烯为例\U00100c3b')], original['original']['evidence'])
    sealed = deepcopy(publication.result)
    owner.tasks = DeferredTasks(); owner._task = None; owner.busy = False
    owner.finished, owner.destroyed = s.window.finished, s.window.destroyed
    owner.set_busy = lambda value: setattr(owner, 'busy', value)
    calls = []
    def recheck(guard):
        calls.append(get_ident()); guard()
        if not s.state['current']: raise ValueError('讲义来源已变化')
    publication.recheck_published = recheck
    before, _ = section_start(s)
    s.queue.finish(0)  # Source context and accepted picker queue compilation.
    errors = []
    def drive():
        pending = s.panel.pending_material_editor
        try:
            if outcome == 'cancel': pending.reject(); return
            def edit():
                editor = pending.active_resource_editor
                try:
                    editor.locked.setChecked(False)
                    editor.body.set_source_text('2.结构：以聚乙烯为例'); editor.apply()
                    assert editor.result_value is not None
                except BaseException as exc: errors.append(exc); editor.reject()
            QTimer.singleShot(0, edit); pending.edit_selected(); pending.apply()
        except BaseException as exc: errors.append(exc); pending.reject()
    QTimer.singleShot(0, drive); s.queue.finish(1)
    assert not errors, errors
    if outcome != 'cancel':
        assert owner.busy and owner._task in owner.tasks.jobs
        if outcome == 'source_changed': s.state['current'] = False
        owner.tasks.finish(owner._task)
        assert calls and all(identity != get_ident() for identity in calls)
    assert publication.result == sealed
    if outcome == 'accept':
        assert publication.accepted and publication.path.exists()
        assert len(owner.history.back) == 1
        added = next(row for row in owner.history.value['composition']['resources'] if row['id'] == sealed['resources'][0]['id'])
        assert added['original'] == sealed['resources'][0]['original']
        assert added['blocks'][0]['value'] == '2.结构：以聚乙烯为例'
        owner.travel(); assert owner.history.value == before
    else:
        assert not publication.accepted and not publication.path.exists()
        assert owner.history.value == before and not owner.history.back


@pytest.mark.parametrize('outcome', ['accept', 'cancel', 'source_changed'])
def test_normal_knowledge_picker_uses_owned_publication_and_native_editor(desk, outcome):
    from integrations.deeptutor_shchem_v1.desktop_workbench import handout_knowledge_picker as picker_module
    s = desk; dialog = SafeDesignDialog(s.page); s.page._material_handoff_dialog = dialog
    requests = [{'source': {'source_id': 'A'}}, {'source': {'source_id': 'B'}}]
    class Picker(QDialog):
        def __init__(self, *_args, **_kwargs): super().__init__(); self.requests = deepcopy(requests)
        def exec(self): return self.DialogCode.Accepted
    s.monkeypatch.setattr(picker_module, 'HandoutKnowledgePicker', Picker)
    seen = []
    def prepare(facade, selected, assets, resources):
        seen.append(deepcopy(selected)); return s.pub
    s.monkeypatch.setattr(handoff, 'prepare_handout_knowledge_publication', prepare)
    dialog.composition_panel.add_handout_knowledge()
    identity = dialog._task; value = s.tasks.calculate(identity)
    assert seen == [requests] and s.new_path.exists()
    errors = []
    def drive():
        pending = dialog.composition_panel.pending_material_editor
        try:
            s.tasks.task_finished.emit(identity)
            assert s.new_path.exists()  # Initial finished is harmless in the modal phase.
            if outcome == 'cancel': pending.reject(); return
            def edit():
                editor = pending.active_resource_editor
                try:
                    editor.locked.setChecked(False); editor.body.set_source_text('2.结构：以聚乙烯为例')
                    editor.apply(); assert editor.result_value is not None
                except BaseException as exc: errors.append(exc); editor.reject()
            QTimer.singleShot(0, edit); pending.edit_selected(); pending.apply()
        except BaseException as exc: errors.append(exc); pending.reject()
    QTimer.singleShot(0, drive); s.tasks.deliver(identity, value)
    assert not errors, errors
    if outcome != 'cancel':
        if outcome == 'source_changed': s.state['source_current'] = False
        assert dialog.busy; s.tasks.finish(dialog._task)
    assert s.pub.result == s.result
    if outcome == 'accept':
        assert s.pub._accepted and len(dialog.history.back) == 1 and s.new_path.exists()
        assert dialog.history.value['composition']['resources'][0]['blocks'][0]['value'] == '2.结构：以聚乙烯为例'
        dialog.travel(); assert dialog.history.value == s.before['lesson_design']
    else:
        assert not s.pub._accepted and not s.new_path.exists() and s.old_path.exists()
        assert dialog.history.value == s.before['lesson_design'] and not dialog.history.back


def test_knowledge_publication_stages_every_source_under_one_lock_and_freezes_requests(desk, tmp_path):
    from tempfile import TemporaryDirectory
    s = desk; shared_lock = RLock()
    s.page.facade._word_questions = lambda: SimpleNamespace(_lock=shared_lock)
    # All test artifacts stay beneath the private test directory.
    s.monkeypatch.setattr(handoff, 'TemporaryDirectory', lambda **kw: TemporaryDirectory(**{'dir': tmp_path, **kw}))
    requests = [{'source': {'source_id': 'A'}}, {'source': {'source_id': 'B'}}]
    frozen = deepcopy(requests); roots = []; calls = []
    def compiler(staged, selected, assets, resources):
        assert shared_lock._is_owned()
        assert selected == frozen and assets == [s.old] and resources == []
        assert staged.paths.task_root != s.page.facade.paths.task_root
        store = PreparationImageStore(staged.paths.task_root / 'preparation-v1' / 'images')
        assert store.load(s.old) == s.old_path.read_bytes()
        store.import_bytes(s.pub._payloads[s.new['sha256']], s.new['caption'], s.new['source'], s.new['purpose'])
        roots.append(staged.paths.task_root); calls.append(deepcopy(selected))
        return deepcopy(s.result)
    s.monkeypatch.setattr(handoff, 'import_handout_knowledge', compiler)
    pub = handoff.prepare_handout_knowledge_publication(s.page.facade, requests, [s.old], [])
    assert not s.new_path.exists() and not any(path.exists() for path in roots)
    requests[0]['source']['source_id'] = 'caller changed later'
    result = pub.publish(); pub.recheck_published()
    assert result == s.result and pub.request['knowledge_requests'] == frozen and len(calls) == 4
    assert not any(path.exists() for path in roots)
    pub.discard(); assert not s.new_path.exists() and s.old_path.exists()
