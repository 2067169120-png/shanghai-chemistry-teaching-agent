"""Deferred source batch delivery is one native design history transaction."""
from copy import deepcopy
from uuid import uuid4

import pytest
from PySide6.QtCore import QObject, Signal
from test_question_explorer_ui import window as window
from test_composition_ui import personal
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as backend
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled


class DeferredTasks(QObject):
    task_finished = Signal(str)
    task_cancelled_detail = Signal(str, str, str)
    task_cancelled = Signal(str, str)
    is_shutting_down = False
    def __init__(self):
        super().__init__(); self.pending = {}; self.stopped = set()
    def submit(self, label, operation, *, on_success=None, on_failure=None):
        key = uuid4().hex
        self.pending[key] = (label, operation, on_success, on_failure)
        return key
    def cancel(self, key):
        self.stopped.add(key); return True
    def calculate(self, key):
        return self.pending[key][1]()
    def deliver(self, key, value):
        label, _, done, _ = self.pending.pop(key)
        if key not in self.stopped: done(value)
        else:
            self.task_cancelled_detail.emit(key, label, '已停止')
            self.task_cancelled.emit(key, label)
        self.task_finished.emit(key)
    def fail(self, key, message):
        _, _, _, failed = self.pending.pop(key); failed(message); self.task_finished.emit(key)


class Publication:
    def __init__(self, rows):
        self.result = {'resources': deepcopy(rows), 'image_assets': []}
        self.accepted = False; self.discarded = False
    def publish(self, guard):
        guard(); return deepcopy(self.result)
    def accept(self): self.accepted = True
    def discard(self): self.discarded = True


def request():
    source = {'schema': 'shchem.handout-knowledge-source.v1', 'batch_id': 'BATCH',
        'source_id': 'SOURCE', 'source_name': '合成讲义.docx', 'source_path': '合成讲义.docx',
        'source_sha256': 'a'*64, 'source_revision': 'b'*64, 'source_role': 'handout'}
    return {'schema': backend.SCHEMA, 'source': source, 'knowledge_requests': [],
        'image_selections': [], 'question_selections': [{'key': 'Q1', 'revision': 'c'*64,
            'points': 2, 'source_sha256': 'a'*64, 'scoped_source_document': source}]}


def setup(window, monkeypatch, rows=None):
    win, app = window; page = win.preparation_page
    page.topic.setText('教师原课题'); page.materials.setPlainText('已有材料全文')
    tasks = DeferredTasks(); page.tasks = tasks
    publication = Publication(rows if rows is not None else [personal('知识段全文'), personal('图文说明'), personal('完整题答')])
    calls = []
    def prepared(facade, selection, assets, resources):
        calls.append((deepcopy(selection), deepcopy(assets), deepcopy(resources)))
        return publication
    monkeypatch.setattr(backend, 'prepare_handout_materials', prepared)
    monkeypatch.setattr(backend, 'prepare_linked_question', prepared)
    return page, tasks, app, publication, calls


def close(page, app):
    if page._material_handoff_dialog is not None: page._material_handoff_dialog.reject()
    app.processEvents()


def test_async_mixed_handoff_preserves_form_and_adds_one_undo(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    before_materials = page.materials.toPlainText(); receipts = []
    page.material_handoff_finished.connect(lambda ok, text: receipts.append((ok, text)))
    assert page.import_handout_materials(request()) and not calls
    dialog = page._material_handoff_dialog
    before = deepcopy(dialog.history.value); back = len(dialog.history.back)
    key = dialog._task; value = tasks.calculate(key); tasks.deliver(key, value); app.processEvents()
    assert publication.accepted and not publication.discarded and len(calls) == 1
    assert dialog.composition_panel.composition()['resources'] == publication.result['resources']
    assert len(dialog.history.back) == back + 1
    assert page.topic.text() == '教师原课题' and page.materials.toPlainText() == before_materials
    after = deepcopy(dialog.history.value)
    assert dialog.status.text() == page.last_material_handoff['message']
    assert not dialog.busy and dialog._task is None
    dialog.travel(False); assert dialog.history.value == before
    dialog.travel(True); assert dialog.history.value == after
    assert receipts[-1][0] and page.last_material_handoff['status'] == 'added'
    close(page, app)


@pytest.mark.parametrize('change', ['topic', 'design', 'closing', 'stopping', 'shutdown'])
def test_late_success_does_not_replace_changed_lesson(window, monkeypatch, change):
    page, tasks, app, publication, _ = setup(window, monkeypatch)
    original_design = deepcopy(page._lesson_design)
    assert page.import_handout_materials(request())
    dialog = page._material_handoff_dialog; key = dialog._task
    status_receipts = []
    page.material_handoff_finished.connect(lambda ok, text: status_receipts.append((ok, text, dialog.status.text())))
    value = tasks.calculate(key)
    if change == 'topic': page.topic.setText('稍后新课题')
    elif change == 'design': page._lesson_design = {**page._lesson_design, 'classroom_notes': True}
    elif change == 'closing': dialog._close_when_finished = True
    elif change == 'stopping': dialog._stopping = True
    else: tasks.is_shutting_down = True
    tasks.deliver(key, value); app.processEvents()
    assert not publication.accepted and publication.discarded
    assert not (page._lesson_design or {}).get('composition', {}).get('resources')
    assert page.last_material_handoff['status'] == 'not_added' and not page.studio_busy()
    assert status_receipts == [(False, page.last_material_handoff['message'], page.last_material_handoff['message'])]
    if change == 'topic': assert page.topic.text() == '稍后新课题'
    if change == 'design': assert page._lesson_design['classroom_notes'] is True
    tasks.is_shutting_down = False


def test_stop_before_worker_has_no_delivery_and_reverts_dialog_initializer(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    before = deepcopy(page._payload())
    assert page.import_handout_materials(request())
    dialog = page._material_handoff_dialog; key = dialog._task
    status_receipts = []
    page.material_handoff_finished.connect(lambda ok, text: status_receipts.append((ok, text, dialog.status.text())))
    dialog.cancel(); tasks.deliver(key, None); app.processEvents()
    assert not calls and not publication.accepted and page._payload() == before
    assert not page.studio_busy() and page.last_material_handoff['status'] == 'not_added'
    assert status_receipts == [(False, page.last_material_handoff['message'], page.last_material_handoff['message'])]


def test_source_failure_and_late_worker_guard_do_not_change_course(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    before = deepcopy(page._payload())
    assert page.import_handout_materials(request())
    dialog = page._material_handoff_dialog; key = dialog._task
    status_receipts = []
    page.material_handoff_finished.connect(lambda ok, text: status_receipts.append((ok, text, dialog.status.text())))
    page.objective.setPlainText('读取中的教师修改')
    with pytest.raises(ReadCancelled): tasks.calculate(key)
    assert not calls
    tasks.fail(key, '原讲义已变化'); app.processEvents()
    assert not publication.accepted and not page.studio_busy()
    assert page.objective.toPlainText() == '读取中的教师修改'
    assert page._lesson_design == before.get('lesson_design')
    assert status_receipts == [(False, '原讲义已变化', '原讲义已变化')]


@pytest.mark.parametrize('entry', ['handout', 'linked'])
def test_duplicate_does_not_add_history_and_keeps_existing_work(window, monkeypatch, entry):
    page, tasks, app, publication, calls = setup(window, monkeypatch, [])
    if entry == 'handout': assert page.import_handout_materials(request())
    else:
        linked = {'link_id': 'LINK', 'knowledge_endpoint': {'kind': 'reading_note', 'id': 'NOTE'},
            'snapshot': {}, 'selection': {}, 'reference': {}, 'scoped_facade': object()}
        assert page.import_linked_question(linked)
    dialog = page._material_handoff_dialog; before = deepcopy(dialog.history.value)
    count = len(dialog.history.back); key = dialog._task
    tasks.deliver(key, tasks.calculate(key)); app.processEvents()
    assert publication.accepted and dialog.history.value == before and len(dialog.history.back) == count
    assert page.last_material_handoff['status'] == 'duplicate'
    assert dialog.status.text() == page.last_material_handoff['message']
    assert not dialog.busy and dialog._task is None
    close(page, app)


def test_add_failure_restores_assets_and_history_without_acknowledging(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    before = deepcopy(page._payload())
    assert page.import_handout_materials(request())
    dialog = page._material_handoff_dialog; key = dialog._task
    status_receipts = []
    page.material_handoff_finished.connect(lambda ok, text: status_receipts.append((ok, text, dialog.status.text())))
    frozen = deepcopy((dialog.history.value, dialog.history.back, dialog.history.forward,
                       page.image_assets_widget.assets(), publication.result))
    attempts = []
    def failed(result, expected, *, expected_assets=None, **kwargs):
        attempts.append((deepcopy(result), expected, deepcopy(expected_assets)))
        raise ValueError('排版失败')
    monkeypatch.setattr(dialog.composition_panel, 'add_result', failed)
    tasks.deliver(key, tasks.calculate(key)); app.processEvents()
    assert publication.discarded and not publication.accepted
    assert page._payload() == before and not page.studio_busy()
    assert len(attempts) == 1 and attempts[0][0] == frozen[4]
    assert attempts[0][2] == frozen[3]
    assert (dialog.history.value, dialog.history.back, dialog.history.forward,
            page.image_assets_widget.assets(), publication.result) == frozen
    assert status_receipts == [(False, '排版失败', '排版失败')]


def test_explicit_linked_question_uses_checked_backend_and_busy_blocks_both(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    linked = {'link_id': 'LINK', 'knowledge_endpoint': {'kind': 'reading_note', 'id': 'NOTE'},
        'snapshot': {}, 'selection': {}, 'reference': {}, 'scoped_facade': object()}
    assert page.import_linked_question(linked)
    assert not page.import_handout_materials(request()) and not page.import_linked_question(linked)
    dialog = page._material_handoff_dialog; key = dialog._task
    tasks.deliver(key, tasks.calculate(key)); app.processEvents()
    assert calls[0][0]['knowledge_endpoint'] == linked['knowledge_endpoint'] and publication.accepted
    assert page.last_material_handoff['status'] == 'added'
    assert dialog.status.text() == page.last_material_handoff['message']
    assert not dialog.busy and dialog._task is None
    close(page, app)


def test_toolbar_has_explicit_lecture_route_and_keeps_current_form(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    received = []; page.handout_requested.connect(lambda: received.append(True))
    before = deepcopy(page._payload())
    page.handout_button.click(); assert received == [True] and page._payload() == before
    page._lesson_busy = True; page.handout_button.click(); assert received == [True]
    page._lesson_busy = False


def test_malformed_link_request_does_not_open_or_mutate_current_form(window, monkeypatch):
    page, tasks, app, publication, calls = setup(window, monkeypatch)
    before = deepcopy(page._payload())
    assert not page.import_linked_question(None)
    assert page._payload() == before and not tasks.pending and not calls
