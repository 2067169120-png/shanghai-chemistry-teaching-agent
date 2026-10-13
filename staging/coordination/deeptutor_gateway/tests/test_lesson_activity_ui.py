"""Real widget handoff, picker scope, source navigation and reviewed refresh."""
from copy import deepcopy
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from test_question_explorer_ui import window
from test_lesson_design_ui import desk
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_activity_dialogs import ActivityPicker, ActivityUpdateDialog
from integrations.deeptutor_shchem_v1.desktop_composition_activities import activity_options, work_copy_changed
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import edit_resource


def test_node_handoff_jump_back_and_stale_status_are_native(desk):
    d, app, page = desk; source = deepcopy(d.history.value); panel = d.composition_panel
    materials_before = page.materials.toPlainText()
    QTest.mouseClick(d.to_canvas, Qt.MouseButton.LeftButton); settle(app)
    assert d.tabs.currentWidget() == panel and len(panel.composition()['resources']) == 1
    item = panel.composition()['resources'][0]
    assert item['original']['evidence']['activity_snapshot']['node'] == source['nodes'][0]
    assert page.materials.toPlainText() == materials_before
    assert '环节已关联' in panel.details.text()
    assert '原环节快照（含教师内容，供备课核对）' in panel.original.toPlainText()
    assert source['nodes'][0]['student_task'] in panel.original.toPlainText()
    assert 'activity_snapshot' not in panel.original.toPlainText()
    panel.open_activity_button.click(); settle(app)
    assert d.tabs.currentWidget() == d.main_splitter and d.current == source['nodes'][0]['id']
    d.fields['student_task'].setPlainText('改后的学生任务，可保留画布旧副本。'); settle(app)
    assert panel.composition()['resources'][0] == item
    assert '待更新关联' in panel.list.item(0).text()
    d.add_current_activity(); settle(app)
    assert len(panel.composition()['resources']) == 1
    assert d.tabs.currentWidget() == panel


def test_multiselect_search_preserves_choices_and_cancel_changes_nothing(desk):
    d, app, page = desk; d.add_node(); d.title_edit.setText('第二个合成环节'); settle(app)
    rows = activity_options(d.history.value, page._payload().get('image_assets', []))
    assert len(rows) > 1
    before = deepcopy(page._payload()); picker = ActivityPicker(rows, d); picker.show(); settle(app)
    first = picker.list.item(0); first.setCheckState(Qt.CheckState.Checked)
    selected = first.data(Qt.ItemDataRole.UserRole)
    picker.search.setText('不存在的搜索内容'); settle(app)
    assert picker.count.text().startswith('已勾选 1') and picker.apply_button.isEnabled()
    picker.search.clear(); settle(app)
    assert picker.list.item(0).checkState() == Qt.CheckState.Checked
    picker.choose(); assert picker.chosen == [selected]
    picker.deleteLater(); assert page._payload() == before


def test_update_requires_review_of_modified_copy_and_keeps_geometry_undo(desk):
    d, app, page = desk; panel = d.composition_panel; d.add_current_activity(); settle(app)
    original = panel.composition(); identity = original['resources'][0]['id']
    value = edit_resource(original, identity, locked=False)
    value = edit_resource(value, identity, title='教师在画布修改的标题')
    panel.commit(value); d.tabs.setCurrentWidget(d.main_splitter)
    d.fields['student_task'].setPlainText('关联环节的新任务'); settle(app)
    before = deepcopy(d.history.value); d.tabs.setCurrentWidget(panel)
    dialog = ActivityUpdateDialog(panel.composition(), identity, d.history.value, page._payload().get('image_assets', []), panel)
    dialog.show(); settle(app)
    assert dialog.allow_replace.isVisible() and not dialog.update_button.isEnabled()
    QTest.mouseClick(dialog.allow_replace, Qt.MouseButton.LeftButton); assert dialog.update_button.isEnabled()
    QTest.mouseClick(dialog.update_button, Qt.MouseButton.LeftButton)
    panel.commit(dialog.result_value); settle(app)
    assert panel.composition()['placements'] == before['composition']['placements']
    assert not work_copy_changed(panel.composition()['resources'][0])
    assert '环节已关联' in panel.details.text()
    d.travel(False); assert d.history.value == before
    dialog.deleteLater()


def test_removed_source_can_be_explicitly_kept_and_exported(desk):
    d, app, page = desk; panel = d.composition_panel; d.add_current_activity(); settle(app)
    identity = panel.composition()['resources'][0]['id']; d.tabs.setCurrentWidget(d.main_splitter)
    d.delete_node(); d.tabs.setCurrentWidget(panel); settle(app)
    assert '原环节已移除' in panel.details.text() and not panel.open_activity_button.isEnabled()
    dialog = ActivityUpdateDialog(panel.composition(), identity, d.history.value, page._payload().get('image_assets', []), panel)
    dialog.show(); settle(app); assert not dialog.update_button.isEnabled()
    QTest.mouseClick(dialog.keep_button, Qt.MouseButton.LeftButton); panel.commit(dialog.result_value); settle(app)
    assert '独立副本' in panel.details.text()
    assert not panel.update_activity_button.isVisible()
    d.generate.click(); settle(app, lambda: not d.busy)
    assert len(d.history.value['exports']) == 1, d.status.text()
    assert len(d.history.value['exports'][-1]['files']) == 6
    dialog.deleteLater()
