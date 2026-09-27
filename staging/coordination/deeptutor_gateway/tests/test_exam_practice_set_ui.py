import os
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from threading import Event
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from test_exam_dashboard_ui import desk
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup
from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection, request_revision
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from test_desktop_mixed_paper_service import setup as paper_setup, _add_word
from mixed_pagination_test_support import synthetic_pagination


def task_for(d):
    task = create_followup(d.exam, '1', ['S0001'], '完整题目与学习目标逐项核对', date.today().isoformat())
    return freeze_selection(task, [{'key': 'word:one', 'title_zh': '完整主题一', 'source_zh': '合成讲义'}], ['word:one'])


def capture(widget, filename):
    destination = os.environ.get('SHCHEM_CANDIDATE_SCREENSHOTS')
    if destination:
        root = Path(destination); root.mkdir(parents=True, exist_ok=True)
        assert widget.grab().save(str(root / filename))


def test_explicit_selection_saves_an_independent_set(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as module
    d, app, _ = desk; task = task_for(d); task.pop('practice_set')
    panel = d.followup_panel; panel.store(task)
    d.facade.basket = lambda: [{'key': 'one', 'title_zh': '本任务'}, {'key': 'two', 'title_zh': '其他备课'}]
    monkeypatch.setattr(module, 'checked_rows', lambda *a: ['one'])
    panel.link.click(); settle(app)
    assert [r['key'] for r in panel.current()['practice_set']['items']] == ['one']
    assert d.store.load(d.exam['id'])['followups'][0]['practice_set'] == panel.current()['practice_set']
    d.facade.basket = lambda: []
    panel.show_task()
    assert '独立题集' in panel.selection_status.text() and panel.preview_button.isEnabled()


def test_changed_goal_disables_export_without_calling_service(desk):
    d, app, _ = desk; panel = d.followup_panel; panel.store(task_for(d))
    panel.preview = SimpleNamespace(); panel.approved = True
    panel.preview_task_id = panel.current()['id']; panel.preview_revision = request_revision(panel.current())
    panel.current()['goal'] = '目标已改变'
    panel.export_paper()
    assert not panel.approved and not panel.export.isEnabled()
    assert '重新预览' in d.status.text()


def test_corrupt_saved_set_is_visible_and_not_exportable(desk):
    d, app, _ = desk; panel = d.followup_panel; task = task_for(d)
    task['practice_set']['sha256'] = 'wrong'; panel.store(task)
    assert not panel.preview_button.isEnabled() and not panel.export.isEnabled()
    assert '校验不一致' in panel.selection_status.text()


def test_failed_save_does_not_replace_old_task(desk, monkeypatch):
    d, app, _ = desk; panel = d.followup_panel; panel.store(task_for(d))
    before = deepcopy(d.followups); changed = deepcopy(panel.current()); changed['goal'] = '未能保存'
    monkeypatch.setattr(d, 'save_current', lambda: False)
    assert not panel.store(changed) and d.followups == before


def test_task_set_layout_and_real_screenshots(desk):
    d, app, _ = desk; panel = d.followup_panel; panel.store(task_for(d))
    d.tabs.setCurrentWidget(panel)
    for width, height, name in [(1320, 860, 'task-set-wide.png'), (800, 700, 'task-set-compact.png')]:
        d.resize(width, height); settle(app)
        assert panel.text.height() >= 150
        for widget in (panel.new, panel.link, panel.preview_button, panel.export, panel.record):
            assert d.rect().contains(QRect(widget.mapTo(d, widget.rect().topLeft()), widget.size()))
        assert '原题文件须保留' in panel.selection_status.text()
        capture(d, name)


def test_unowned_old_preview_failure_keeps_fresh_preview_entry_available(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as module
    from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError
    d, app, _ = desk
    panel = d.followup_panel
    panel.store(task_for(d))
    original = deepcopy(d.followups)
    panel.preview = SimpleNamespace(preview_id='mixed-preview-' + '1' * 32, preview_hash='a' * 64)
    panel.approved = True
    panel.preview_task_id = panel.current()['id']
    panel.preview_revision = request_revision(panel.current())
    calls = []
    class MissingOldPreview:
        def export_paper_preview(self, *_args):
            calls.append('old-export')
            raise MixedPaperError('统一预览已缺失，请重新生成。')
    monkeypatch.setattr(module, 'paper_session', lambda *_args: MissingOldPreview())
    panel.export_paper()
    settle(app, lambda: d._task is None)
    assert calls == ['old-export'] and d.followups == original
    assert '重新生成' in d.status.text() and panel.preview_button.isEnabled()
    queued = []
    monkeypatch.setattr(d, 'run', lambda label, operation, done: queued.append(operation))
    panel.preview_button.click()
    assert len(queued) == 1 and panel.preview is None and not panel.approved


def test_legacy_link_with_public_draft_explicitly_saves_set_then_previews_independently(desk, paper_setup, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import exam_followup_panel as module
    from integrations.deeptutor_shchem_v1.desktop_exam_practice import TaskPaperSession
    from test_mixed_paper_draft_undo import initial_payload
    d, app, _ = desk
    engine, state, words, _, _ = paper_setup
    key = _add_word(engine, words)
    engine.facade._reader_stop_event = Event()
    engine.facade.basket = state.basket
    d.facade = engine.facade
    global_session = engine.open_draft_session()
    projection = engine.projection()
    global_session.read(projection)
    global_session.save(initial_payload(projection), remember=False)
    public = state.draft_snapshot(DRAFT_ID)
    task = create_followup(d.exam, '1', ['S0001'], '合成任务目标', date.today().isoformat())
    task = freeze_selection(task, state.basket(), [key]); task.pop('practice_set')
    panel = d.followup_panel
    assert panel.store(task)
    before = deepcopy(d.followups)
    factory, calls = module.paper_session, []
    def session_factory(facade, selected):
        calls.append(deepcopy(selected))
        result = factory(facade, selected)
        assert isinstance(result, TaskPaperSession)
        for name in ('_core_bundle_builder', '_docx_builder', '_word_validator', '_core_context'):
            setattr(result.service, name, getattr(engine, name))
        result.service._pagination_builder = synthetic_pagination
        return result
    monkeypatch.setattr(module, 'paper_session', session_factory)
    panel.preview_button.click()
    assert not calls and d._task is None and d.followups == before
    assert '保存本任务题集' in d.status.text() and panel.link.isEnabled()
    assert state.draft_snapshot(DRAFT_ID) == public
    # The hint never accepts the selection for the teacher. Cancelling the
    # existing chooser preserves the old links and the public draft.
    monkeypatch.setattr(module, 'checked_rows', lambda *_args: None)
    panel.link.click()
    assert d.followups == before
    chosen = []
    def choose(_title, rows, _parent):
        chosen.append(rows)
        return [key]
    monkeypatch.setattr(module, 'checked_rows', choose)
    panel.link.click()
    assert chosen and 'practice_set' in panel.current()
    assert d.store.load(d.exam['id'])['followups'][0]['practice_set'] == panel.current()['practice_set']
    assert state.draft_snapshot(DRAFT_ID) == public
    panel.preview_button.click()
    settle(app, lambda: d._task is None)
    assert calls and panel.preview is not None and panel._preview_dialog.isVisible()
    assert panel.preview.preview_model['sections'][0]['key'] == key
    assert state.draft_snapshot(DRAFT_ID) == public
    panel._preview_dialog.reject()


def test_legacy_link_without_public_draft_retains_existing_preview_flow(desk, tmp_path, monkeypatch):
    d, _, _ = desk
    d.facade.state_store = DesktopStateStore(tmp_path / 'no-public-draft')
    task = task_for(d); task.pop('practice_set')
    panel = d.followup_panel
    panel.store(task)
    queued = []
    monkeypatch.setattr(d, 'run', lambda label, operation, done: queued.append(operation))
    panel.preview_button.click()
    assert len(queued) == 1 and 'practice_set' not in panel.current()
    assert not d.facade.state_store.path.exists()


def test_legacy_link_state_read_failure_never_means_no_public_draft(desk, tmp_path, monkeypatch):
    d, _, _ = desk
    state = DesktopStateStore(tmp_path / 'unreadable-state')
    d.facade.state_store = state
    state.save_draft(DRAFT_ID, {'teacher_note': 'must remain'})
    frozen = state.path.read_bytes()
    task = task_for(d); task.pop('practice_set')
    panel = d.followup_panel
    panel.store(task)
    queued = []
    monkeypatch.setattr(d, 'run', lambda *_args: queued.append(True))
    panel.preview = SimpleNamespace(preview_id='old-preview', preview_hash='old-hash')
    panel.approved = True
    panel.preview_task_id = panel.current()['id']
    panel.preview_revision = request_revision(panel.current())
    panel.show_task()
    assert panel.export.isEnabled()
    def fail():
        raise OSError('synthetic private read failure')
    monkeypatch.setattr(state, 'snapshot', fail)
    panel.preview_button.click()
    assert not queued and '暂不能读取' in d.status.text()
    assert panel.preview is None and not panel.approved and not panel.export.isEnabled()
    assert state.path.read_bytes() == frozen and 'practice_set' not in panel.current()
