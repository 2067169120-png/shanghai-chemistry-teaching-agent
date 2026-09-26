"""Real Qt task edits, conflicts and deliberately delayed callbacks."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QDialog
from test_exam_dashboard_ui import desk
from test_desktop_studio_ui import settle
from test_exam_practice_set_ui import task_for, capture
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError
from integrations.deeptutor_shchem_v1.desktop_exam_practice import freeze_selection, selection_items, request_revision


def two_items(d):
    return freeze_selection(task_for(d), [
        {'key': 'word:one', 'title_zh': '动态平衡的判断依据', 'source_zh': '合成教师讲义'},
        {'key': 'word:two', 'title_zh': '浓度与反应方向', 'source_zh': '合成教师讲义'},
    ], ['word:one', 'word:two'])


def open_editor(d, app):
    panel = d.followup_panel; d.tabs.setCurrentWidget(panel)
    panel.edit_button.click(); settle(app)
    return panel._selection_dialog


def test_saved_set_edits_without_reading_global_basket_and_reopens(desk):
    d, app, _ = desk; panel = d.followup_panel
    assert panel.store(two_items(d))
    def forbidden():raise AssertionError('Saved editor must not read the global basket')
    d.facade.basket = forbidden
    dialog = open_editor(d, app)
    dialog.view.setCurrentRow(1); dialog.up.click()
    assert dialog.keys == ['word:two', 'word:one']
    dialog.view.setCurrentRow(1); dialog.remove.click()
    dialog.goal.setPlainText('用已知浓度核对反应方向')
    dialog.save_button.click(); settle(app)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert [row['key'] for row in selection_items(panel.current())] == ['word:two']
    identity = panel.current()['id']; d.open_saved(d.exam['id'], identity)
    assert panel.current()['goal'] == '用已知浓度核对反应方向'
    assert [row['key'] for row in selection_items(panel.current())] == ['word:two']


def test_cancel_and_reset_do_not_touch_storage(desk):
    d, app, _ = desk; panel = d.followup_panel; panel.store(two_items(d))
    before = d.store.load(d.exam['id'])
    dialog = open_editor(d, app)
    dialog.down.click(); dialog.remove.click(); dialog.reset.click()
    assert dialog.keys == ['word:one', 'word:two']
    dialog.goal.setPlainText('取消掉的目标'); dialog.reject(); settle(app)
    assert d.store.load(d.exam['id']) == before


def test_save_failure_preserves_editor_input_and_dirty_flag(desk, monkeypatch):
    d, app, _ = desk; panel = d.followup_panel; panel.store(two_items(d))
    before = deepcopy(d.followups); assert not d.dirty
    dialog = open_editor(d, app); dialog.goal.setPlainText('仍在窗口里的修改')
    monkeypatch.setattr(d, 'save_current', lambda: False)
    dialog.save_button.click(); settle(app)
    assert dialog.isVisible() and dialog.goal.toPlainText() == '仍在窗口里的修改'
    assert d.followups == before and not d.dirty and '修改仍保留' in dialog.message.text()
    dialog.reject()


def test_empty_goal_or_last_item_cannot_be_saved_as_empty_set(desk):
    d, app, _ = desk; d.followup_panel.store(task_for(d)); dialog = open_editor(d, app)
    assert not dialog.remove.isEnabled()
    dialog.goal.clear(); assert not dialog.save_button.isEnabled()
    dialog.goal.setPlainText('字' * 2001); assert not dialog.save_button.isEnabled()
    dialog.reject()


def test_edit_invalidates_approval_but_preserves_attempts(desk):
    d, app, _ = desk; panel = d.followup_panel; panel.store(two_items(d))
    original = deepcopy(panel.current())
    panel.preview=SimpleNamespace(); panel.approved=True
    panel.preview_task_id=original['id']; panel.preview_revision=request_revision(original)
    dialog=open_editor(d, app); dialog.goal.setPlainText('新的目标'); dialog.save_button.click(); settle(app)
    assert not panel.approved and panel.preview is None and not panel.export.isEnabled()
    assert panel.current()['attempts'] == original['attempts']


def test_foreign_exam_and_stale_expected_task_cannot_be_saved(desk):
    d, app, _ = desk; panel=d.followup_panel; panel.store(two_items(d))
    before=deepcopy(d.followups); wrong=deepcopy(panel.current()); wrong['exam_id']='other'
    assert not panel.store(wrong) and d.followups==before
    expected=deepcopy(panel.current()); candidate=deepcopy(expected); candidate['goal']='old'
    panel.current()['goal']='new'
    assert not panel.store(candidate, expected=expected) and panel.current()['goal']=='new'


def test_update_keeps_order_and_refresh_keeps_selected_task(desk):
    d, app, _ = desk; panel=d.followup_panel
    first=two_items(d); second=two_items(d); second['id']='second-task'
    panel.store(first); panel.store(second); panel.store(first)
    assert [row['id'] for row in d.followups] == [first['id'],second['id']]
    panel.tasks.setCurrentIndex(panel.tasks.findData(second['id'])); panel.refresh()
    assert panel.current()['id'] == second['id']


def test_external_disk_edit_blocks_editor_save(desk):
    d, app, _ = desk; panel=d.followup_panel; panel.store(two_items(d))
    dialog=open_editor(d, app); dialog.goal.setPlainText('旧窗口修改')
    bundle=d.store.load(d.exam['id']); bundle['notes']='另一个窗口保存'
    d.store.save(bundle, expected_revision=d._saved_revision)
    dialog.save_button.click(); settle(app)
    assert dialog.isVisible() and '另一个窗口' in dialog.message.text()
    assert d.store.load(d.exam['id'])==bundle
    dialog.reject()


def delayed_export(d, monkeypatch):
    panel=d.followup_panel; task=deepcopy(panel.current())
    panel.preview=SimpleNamespace(preview_id='preview-a', preview_hash='hash-a'); panel.approved=True
    panel.preview_task_id=task['id']; panel.preview_revision=request_revision(task)
    pending=[]
    monkeypatch.setattr(d, 'run', lambda label, fn, callback: pending.append(callback))
    panel.export_paper(); assert len(pending)==1
    return pending[0], {'pdf_status':'generated','artifacts':[{'path':'synthetic.pdf'}]}


def test_late_export_preserves_new_attempt_history(desk, monkeypatch):
    d, app, _ = desk; panel=d.followup_panel; panel.store(two_items(d))
    callback, result=delayed_export(d, monkeypatch)
    # Record is added after the export starts; it must not be replaced by its snapshot.
    panel.current()['attempts'].append({'id':'new','student_id':'S0001','date':'2026-09-19',
        'status':'missing','score':None,'maximum':None,'note':'新录入','links':[]})
    callback(result)
    assert panel.current()['attempts'][-1]['id']=='new'
    assert d.store.load(d.exam['id'])['followups'][0]['attempts'][-1]['id']=='new'


def test_late_export_cannot_write_to_switched_task(desk, monkeypatch):
    d, app, _ = desk; panel=d.followup_panel; first=two_items(d); panel.store(first)
    callback, result=delayed_export(d, monkeypatch)
    second=two_items(d); second['id']='second-task'; panel.store(second)
    before=deepcopy(d.followups)
    with pytest.raises(ExamError):callback(result)
    assert d.followups==before


def test_editor_geometry_and_runtime_screenshots(desk):
    d, app, _ = desk; panel=d.followup_panel; panel.store(two_items(d))
    dialog=open_editor(d, app)
    for width,height,name in [(680,660,'practice-editor-wide.png'),(420,560,'practice-editor-compact.png')]:
        dialog.resize(width,height); settle(app)
        assert dialog.width()==width and dialog.view.height()>=120
        for widget in (dialog.up,dialog.down,dialog.remove,dialog.reset,dialog.goal,dialog.save_button):
            assert dialog.rect().contains(QRect(widget.mapTo(dialog, widget.rect().topLeft()),widget.size()))
        capture(dialog,name)
    dialog.reject(); d.resize(800,700); settle(app)
    assert panel.text.height()>=150
    assert d.rect().contains(QRect(panel.edit_button.mapTo(d,panel.edit_button.rect().topLeft()),panel.edit_button.size()))
    capture(d,'practice-panel-compact.png')
