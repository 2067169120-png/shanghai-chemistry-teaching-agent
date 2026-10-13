"""Mixed lecture requests stay inside a minimal in-memory composition owner.

Only the native Panel, pending editor and owned MaterialPublication are built.
The frozen source port below emits synthetic validated resources; no application
window, Facade, StateStore or Recovery constructor, Office, Normal or QA is used.
"""
from copy import deepcopy
from threading import RLock, get_ident
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QEvent, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QPlainTextEdit, QTabWidget, QVBoxLayout

from test_pending_material_edit import app, Assets, DeferredTasks, png
from test_composition_insertion import fixture_compositions
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as backend
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    block, resource, enable_composition, edit_resource)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import DesignHistory, new_design
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import LessonCompositionPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


class Page(QObject):
    preparation_target_changed = Signal()

    def __init__(self, design, assets):
        super().__init__()
        self._lesson_design = deepcopy(design)
        self._editor_draft_id = 'PRIVATE-MIXED-COURSE'
        self.image_assets_widget = Assets(assets)
        self.topic = QLineEdit('本课知识与原图')
        self.materials = QPlainTextEdit('教师已有完整解释')

    def _payload(self):
        return {'topic': self.topic.text(), 'materials': self.materials.toPlainText(),
                'lesson_design': deepcopy(self._lesson_design),
                'image_assets': self.image_assets_widget.assets()}


class Owner(QDialog):
    run = LessonDesignDialog.run
    task_finished = LessonDesignDialog.task_finished

    def __init__(self, page, facade):
        super().__init__()
        self.page, self.facade, self.tasks = page, facade, DeferredTasks()
        self.history = DesignHistory(page._lesson_design)
        self.status = QLabel()
        self._task = None
        self._stopping = self._close_when_finished = self._cancellable = self.busy = False
        self.tabs = QTabWidget()
        self.panel = LessonCompositionPanel(self)
        self.tabs.addTab(self.panel, '素材与页面编排')
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs); layout.addWidget(self.status)
        self.tasks.task_finished.connect(self.task_finished)
        self.sync_page()

    def sync_page(self):
        self.page._lesson_design = deepcopy(self.history.value)
        self.panel.set_payload(self.page._payload())

    def set_busy(self, busy):
        self.busy = busy; self.tabs.setEnabled(not busy)

    def travel(self, redo=False):
        self.history.travel(redo); self.sync_page()


def personal(title, value, image=None):
    blocks = [block('text', 'personal', value)]
    if image is not None: blocks.insert(0, block('image', 'personal', image['asset_id']))
    return resource('personal_text', title, blocks,
        {'source_path': '合成的封存源端点', 'source_sha256': 'unknown', 'source_revision': 'unknown'})


@pytest.fixture
def case(app, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as workspace_api
    old_asset, old_data = png('blue'); new_asset, new_data = png('green')
    store = PreparationImageStore(tmp_path / 'preparation-v1' / 'images')
    assert store.import_bytes(old_data, old_asset['caption'], old_asset['source'], old_asset['purpose']) == old_asset
    facade = SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path),
        _word_questions=lambda: SimpleNamespace(_lock=RLock()), preparation_image_bytes=store.load)
    composition, _, anchor = fixture_compositions()
    design = enable_composition(new_design({'objective': '完整知识与图像讲解'}))
    design['composition'] = composition
    page = Page(design, [old_asset]); owner = Owner(page, facade)
    owner.resize(1350, 880); owner.show(); app.processEvents()
    panel = owner.panel
    panel.selected = anchor; panel.refresh(); panel.insertion_position.setCurrentIndex(panel.insertion_position.findData('after'))
    source = {'schema': 'shchem.handout-knowledge-source.v1', 'batch_id': 'PRIVATE-BATCH',
        'source_id': 'PRIVATE-SOURCE', 'source_name': '合成讲義.docx', 'source_role': 'handout',
        'source_sha256': 'a' * 64, 'source_path': str(tmp_path / '合成讲義.docx'), 'source_revision': 'b' * 64}
    request = {'schema': backend.SCHEMA, 'source': deepcopy(source),
        'knowledge_requests': [{'title': '完整栏目'}], 'image_selections': [{'title': '原图'}],
        'question_selections': [{'title': '完整题'}],
        'selection_order': [{'kind': 'image', 'index': 0}, {'kind': 'knowledge', 'index': 0},
                            {'kind': 'question', 'index': 0}]}
    incoming = [personal('原图先观察', '完整图注与条件。', new_asset),
                personal('再完整讲解', '定义、适用条件与全部解释。'),
                personal('最后一道题', '完整题面、条件与课堂反馈。')]
    state = {'request': request, 'source': source, 'outcome': QDialog.DialogCode.Accepted,
        'mutation': None, 'source_current': True, 'incoming': incoming, 'new_image': True,
        'made': [], 'calls': [], 'publications': [], 'threads': [], 'prepare_error': None}

    class Workspace(QDialog):
        def __init__(self, port, tasks, parent, **options):
            super().__init__(parent)
            self.options = deepcopy(options)
            self.request = deepcopy(state['request'])
            self.initial_selections = []  # A new dialog, never the previous batch.
            self.port = port
            state['made'].append(self)

        def exec(self):
            if state['mutation'] is not None: state['mutation']()
            # Deliberately permit a late Accepted result: the Panel must guard it.
            return state['outcome']

        def continuation_source(self): return deepcopy(state['source'])

    monkeypatch.setattr(workspace_api, 'HandoutMaterialWorkspace', Workspace)

    def prepared(port, submitted, assets, resources):
        state['threads'].append(get_ident())
        state['calls'].append((deepcopy(submitted), deepcopy(assets), deepcopy(resources)))
        if state['prepare_error'] is not None: raise ValueError(state['prepare_error'])
        result = {'resources': deepcopy(state['incoming']),
                  'image_assets': deepcopy(assets) + ([new_asset] if state['new_image'] else [])}
        payloads = {old_asset['sha256']: old_data, new_asset['sha256']: new_data}
        def recheck():
            state['threads'].append(get_ident())
            fresh = deepcopy(result)
            if not state['source_current'] and fresh['resources']:
                fresh['resources'][0]['title'] += '来源改变'
            return fresh, payloads
        publication = backend.MaterialPublication(port, submitted, assets, resources, result, payloads, recheck=recheck)
        state['publications'].append(publication)
        return publication

    monkeypatch.setattr(backend, 'prepare_handout_materials', prepared)
    before = deepcopy(owner.history.value)
    yield SimpleNamespace(app=app, owner=owner, panel=panel, page=page, tasks=owner.tasks,
        state=state, before=before, anchor=anchor, old=old_asset, new=new_asset,
        old_path=store.root / (old_asset['sha256'] + '.image'),
        new_path=store.root / (new_asset['sha256'] + '.image'), monkeypatch=monkeypatch)
    for publication in state['publications']: publication.discard()
    owner.reject(); owner.deleteLater(); page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()


def start(c):
    c.panel.handout_materials_button.click()
    assert c.owner._task in c.tasks.jobs
    return c.owner._task


def unchanged(c, before=None, assets=None):
    assert c.owner.history.value == (c.before if before is None else before)
    assert c.page.image_assets_widget.assets() == ([c.old] if assets is None else assets)
    assert c.old_path.is_file() and not c.new_path.exists()


@pytest.mark.parametrize('mode,resource_index,page_index', [('before', 1, 1), ('after', 2, 3), ('end', 5, 5)])
def test_toolbar_mixed_batch_keeps_frozen_page_order_and_one_undo(case, mode, resource_index, page_index):
    c = case; c.panel.insertion_position.setCurrentIndex(c.panel.insertion_position.findData(mode))
    assert c.panel.handout_materials_button.text() == '通读讲义并选材…'
    task = start(c)
    assert c.state['made'][0].options == {}  # clicked(bool) never becomes a source.
    assert not c.state['calls'] and c.owner.history.value == c.before
    c.tasks.finish(task)
    assert len(c.state['calls']) == len(c.state['publications']) == 1
    assert c.state['calls'][0][0] == c.state['request']
    value = c.panel.composition(); old = c.before['composition']
    assert value['resources'] == old['resources'][:resource_index] + c.state['incoming'] + old['resources'][resource_index:]
    assert {key: value['placements'][key] for key in old['placements']} == old['placements']
    new_pages = [p for p in value['pages'] if p not in old['pages']]
    assert value['pages'] == old['pages'][:page_index] + new_pages + old['pages'][page_index:]
    assert value['student_arrangement'] == old['student_arrangement']
    assert value['click_reveals'] == old['click_reveals']
    assert len(c.owner.history.back) == 1 and c.state['publications'][0]._accepted
    assert c.new_path.is_file() and set(c.state['threads']) != {get_ident()}
    assert '讲义知识、原图与完整题' in c.owner.status.text()
    after = deepcopy(c.owner.history.value)
    c.owner.travel(); assert c.owner.history.value == c.before
    c.owner.travel(True); assert c.owner.history.value == after


def test_returned_request_is_detached_before_worker_reads_it(case):
    c = case; original = deepcopy(c.state['request']); task = start(c)
    c.state['made'][0].request['selection_order'].reverse()
    c.state['made'][0].request['source']['source_id'] = 'LATE-WORKSPACE-MUTATION'
    c.tasks.finish(task)
    assert c.state['calls'][0][0] == original


def test_cancel_can_resume_verified_source_but_next_selection_is_empty(case):
    c = case; c.state['outcome'] = QDialog.DialogCode.Rejected
    c.panel.add_handout_materials()
    assert not c.state['calls'] and not c.tasks.jobs and not c.owner.history.back
    assert c.panel._handout_materials_source == c.state['source']; unchanged(c)
    c.panel.add_handout_materials()
    assert c.state['made'][1].options == {'initial_source': c.state['source'], 'resume_source': True}
    assert c.state['made'][1].initial_selections == []
    assert c.state['made'][1] is not c.state['made'][0]


def test_no_verified_resume_binding_clears_preference_without_guessing(case):
    c = case; c.state['outcome'] = QDialog.DialogCode.Rejected
    c.panel.add_handout_materials(); c.state['source'] = None
    c.panel.add_handout_materials()
    assert c.state['made'][1].options['resume_source'] is True
    assert c.panel._handout_materials_source is None
    c.panel.add_handout_materials()
    assert c.state['made'][2].options == {}; unchanged(c)


def mutate(c, change):
    if change == 'history_undo':
        value = deepcopy(c.owner.history.value)
        value['composition'] = edit_resource(value['composition'], c.anchor, title='临时改过')
        c.owner.history.put(value); c.owner.travel()
        assert c.owner.history.value == c.before and c.owner.history.forward
    elif change == 'insertion_aba':
        old = c.panel.insertion_position.currentIndex()
        c.panel.insertion_position.setCurrentIndex((old + 1) % 3)
        c.panel.insertion_position.setCurrentIndex(old)
    elif change == 'selection_aba':
        old = c.panel.selected
        c.panel.list.setCurrentRow(0)
        c.panel.list.setCurrentRow(next(i for i in range(c.panel.list.count())
            if c.panel.list.item(i).data(256) == old))
    elif change == 'assets_aba':
        c.page.image_assets_widget.set_assets([c.old, c.new])
        c.page.image_assets_widget.set_assets([c.old])
    elif change == 'form_aba':
        old = c.page.materials.toPlainText()
        c.page.materials.setPlainText('临时修改'); c.page.materials.setPlainText(old)
    elif change == 'target_aba':
        old = c.page._editor_draft_id; c.page._editor_draft_id = 'OTHER'
        c.page.preparation_target_changed.emit(); c.page._editor_draft_id = old
        c.page.preparation_target_changed.emit()
    elif change == 'stopping': c.owner._stopping = True
    elif change == 'closing': c.owner.reject()
    else: raise AssertionError(change)


CHANGES = ['history_undo', 'insertion_aba', 'selection_aba', 'assets_aba', 'form_aba', 'target_aba', 'stopping', 'closing']


@pytest.mark.parametrize('change', CHANGES)
def test_changed_then_restored_scope_while_picker_open_never_queues(case, change):
    c = case; c.state['mutation'] = lambda: mutate(c, change)
    c.panel.add_handout_materials()
    assert not c.state['calls'] and not c.tasks.jobs
    assert c.panel._handout_materials_source is None
    unchanged(c)
    if change == 'history_undo': assert c.owner.history.forward


@pytest.mark.parametrize('change', CHANGES)
def test_late_published_result_cannot_erase_current_undo_redo_or_scope(case, change):
    c = case; task = start(c); result = c.tasks.calculate(task)
    mutate(c, change)
    history = deepcopy((c.owner.history.value, c.owner.history.back, c.owner.history.forward))
    c.tasks.deliver(task, result)
    assert (c.owner.history.value, c.owner.history.back, c.owner.history.forward) == history
    assert not c.state['publications'][0]._accepted
    unchanged(c)


def test_sticky_asset_change_before_worker_prevents_prepare_and_image_write(case):
    c = case; task = start(c); mutate(c, 'assets_aba'); c.tasks.finish(task)
    assert not c.state['calls'] and not c.state['publications']; unchanged(c)


@pytest.mark.parametrize('outcome', ['prepare_failure', 'source_changed', 'duplicate'])
def test_source_failure_or_duplicate_does_not_add_history(case, outcome):
    c = case
    if outcome == 'prepare_failure': c.state['prepare_error'] = '原讲义版本改变'
    elif outcome == 'source_changed': c.state['source_current'] = False
    else: c.state['incoming'] = []; c.state['new_image'] = False
    c.tasks.finish(start(c)); unchanged(c)
    assert not c.owner.history.back
    if outcome == 'duplicate':
        assert c.state['publications'][0]._accepted and '已有编辑保持' in c.owner.status.text()


def test_close_during_workspace_does_not_update_continuation_or_status(case):
    c = case; c.owner.status.setText('原状态')
    c.state['mutation'] = c.owner.reject
    c.panel.add_handout_materials()
    assert c.panel._handout_materials_source is None and c.owner.status.text() == '原状态'
    assert not c.tasks.jobs; unchanged(c)


def test_busy_owner_does_not_open_another_workspace(case):
    c = case; c.owner.busy = True
    c.panel.add_handout_materials()
    assert not c.state['made'] and not c.tasks.jobs; unchanged(c)


def test_pending_native_edit_rechecks_off_gui_and_ignores_old_task_finished(case):
    c = case
    c.state['incoming'][0] = personal('完整知识栏目', '2.结构\U00100c37以聚乙烯为例\U00100c3b')
    task = start(c); result = c.tasks.calculate(task)
    original = deepcopy(result); errors = []; seen = []
    def edit_pending():
        pending = c.panel.pending_material_editor
        try:
            seen.append(pending)
            c.tasks.task_finished.emit(task)
            assert c.new_path.exists() and not c.state['publications'][0]._accepted
            def edit_resource():
                editor = pending.active_resource_editor
                try:
                    editor.locked.setChecked(False); editor.blocks.setCurrentIndex(0)
                    editor.body.set_source_text('2.结构：以聚乙烯为例'); editor.apply()
                except BaseException as exc: errors.append(exc); editor.reject()
            QTimer.singleShot(0, edit_resource)
            pending.edit_button.click(); pending.apply()
        except BaseException as exc: errors.append(exc); pending.reject()
    QTimer.singleShot(0, edit_pending)
    c.tasks.deliver(task, result)
    assert not errors and len(seen) == 1
    assert c.owner.history.value == c.before and c.new_path.exists()
    assert c.owner._task != task
    c.tasks.finish(c.owner._task)
    assert len(c.owner.history.back) == 1 and c.state['publications'][0]._accepted
    assert c.state['publications'][0].result == original
    inserted = c.panel.composition()['resources'][2]
    assert inserted['blocks'][0]['value'] == '2.结构：以聚乙烯为例'
    assert inserted['original'] == original['resources'][0]['original']
    assert all(thread != get_ident() for thread in c.state['threads'])


def test_postcommit_refresh_failure_keeps_referenced_owned_image(case, monkeypatch):
    c = case; task = start(c)
    def fail_sync(): raise AttributeError('同步刷新失败')
    monkeypatch.setattr(c.owner, 'sync_page', fail_sync)
    c.tasks.finish(task)
    assert len(c.owner.history.back) == 1
    assert c.state['publications'][0]._accepted and c.new_path.is_file()
    assert all(row in c.panel.composition()['resources'] for row in c.state['incoming'])


def test_precommit_failure_releases_only_this_batch_images(case, monkeypatch):
    c = case; task = start(c)
    def fail_add(*args, **kwargs): raise ValueError('尚未提交')
    monkeypatch.setattr(c.panel, 'add_result', fail_add)
    c.tasks.finish(task)
    assert not c.state['publications'][0]._accepted and not c.owner.history.back; unchanged(c)
