import os
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from test_exam_dashboard_ui import desk
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup
from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection, request_revision


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
