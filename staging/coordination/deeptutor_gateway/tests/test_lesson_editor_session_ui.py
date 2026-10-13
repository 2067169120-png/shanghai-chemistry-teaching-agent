"""Return to an editor position without adding navigation to saved content."""
from copy import deepcopy
from uuid import uuid4

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QPointF

from test_composition_ui import desk, window, personal
from test_composition_student_pages import question, teacher_note
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import FORMATS
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest, new_design
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


def reopen(dialog, page, app):
    dialog.reject()
    returned = LessonDesignDialog(page)
    returned.show()
    settle(app)
    return returned


def finish(dialog, app):
    dialog.reject()
    dialog.deleteLater()
    settle(app)


def test_return_restores_resource_continuation_page_audience_and_outline_without_edit(desk):
    dialog, panel, app, page = desk
    value = teacher_note(question())
    panel.commit(value)
    panel.student.setChecked(False)
    panel.source_tabs.setCurrentWidget(panel.page_navigation.tree)
    target = panel.layout_result['fragments'][-2]
    assert target['frame_index'] > 0
    panel.navigate_to_page(target['frame']['page_id'], target['resource_id'], target['frame_index'])
    before = deepcopy(page._payload())
    returned = reopen(dialog, page, app)
    try:
        restored = returned.composition_panel
        assert returned.tabs.currentWidget() is restored
        assert not restored.student.isChecked()
        assert restored.source_tabs.currentWidget() is restored.page_navigation.tree
        assert (restored.selected, restored.frame_index, restored._view_page_id) == (
            target['resource_id'], target['frame_index'], target['frame']['page_id'])
        assert restored.page_navigation.pages.currentData() == target['frame']['page_id']
        w, h = FORMATS[value['format']]
        y = list(restored.layout_result['pages']).index(target['frame']['page_id']) * (h + 12)
        assert restored.view.mapToScene(restored.view.viewport().rect()).boundingRect().contains(QPointF(w/2, y+h/2))
        assert page._payload() == before and returned.history.value == before['lesson_design']
        assert '_lesson_editor_session' not in page._payload()
    finally:
        finish(returned, app)


def test_return_restores_focused_intentional_blank_page(desk):
    dialog, panel, app, page = desk
    value = question('A4')
    blank = 'CP-' + uuid4().hex
    value['pages'].append(blank)
    panel.commit(value)
    panel.navigate_to_page(blank)
    panel.page_navigation.focus.setChecked(True)
    before = deepcopy(page._payload())
    returned = reopen(dialog, page, app)
    try:
        restored = returned.composition_panel
        assert restored.selected is None and restored._view_page_id == blank
        assert restored.page_navigation.focus.isChecked()
        assert not restored.sources_panel.isVisible() and not restored.inspector_panel.isVisible()
        assert page._payload() == before
    finally:
        finish(returned, app)


def test_node_and_property_tabs_restore_by_identity_after_node_reorder(desk):
    dialog, _panel, app, page = desk
    dialog.add_node()
    dialog.add_node()
    target = dialog.current
    dialog.properties.setCurrentIndex(2)
    dialog.tabs.setCurrentWidget(dialog.main_splitter)
    dialog.reject()
    page._lesson_design['nodes'].reverse()
    before = deepcopy(page._payload())
    returned = LessonDesignDialog(page)
    returned.show(); settle(app)
    try:
        assert returned.current == target
        assert returned.tabs.currentWidget() is returned.main_splitter
        assert returned.properties.tabText(returned.properties.currentIndex()) == '材料与备注'
        assert page._payload() == before
    finally:
        finish(returned, app)


def test_tab_restore_survives_responsive_preview_tab_index_change(desk):
    dialog, panel, app, page = desk
    dialog.resize(1000, 850)
    settle(app)
    dialog.tabs.setCurrentWidget(dialog.preview_page)
    before = deepcopy(page._payload())
    returned = reopen(dialog, page, app)
    try:
        assert returned.tabs.currentWidget() is returned.preview_page
        assert returned.width() == 1000
        returned.resize(1400, 900); settle(app)
        assert returned.tabs.indexOf(returned.preview_page) == -1
        assert returned.tabs.currentWidget() is returned.main_splitter
        assert page._payload() == before
    finally:
        finish(returned, app)


@pytest.mark.parametrize('change', ['remove', 'hide', 'replace_design'])
def test_stale_deleted_or_hidden_selection_and_other_design_are_not_reused(desk, change):
    dialog, panel, app, page = desk
    value = teacher_note(question())
    panel.commit(value)
    panel.student.setChecked(False)
    target = value['resources'][-1]['id']
    fragment = next(f for f in panel.layout_result['fragments'] if f['resource_id'] == target)
    panel.navigate_to_page(fragment['frame']['page_id'], target, fragment['frame_index'])
    dialog.reject()
    if change == 'remove':
        updated = page._lesson_design['composition']
        updated['resources'] = updated['resources'][:-1]
        del updated['placements'][target]
        updated['pages'].remove(fragment['frame']['page_id'])
    elif change == 'hide':
        page._lesson_editor_session['student'] = True
    else:
        page._lesson_design = new_design(page._payload())
    before = deepcopy(page._payload())
    returned = LessonDesignDialog(page)
    returned.show(); settle(app)
    try:
        restored = returned.composition_panel
        assert restored.selected != target
        assert fragment['frame']['page_id'] != restored._view_page_id
        if change == 'replace_design':
            assert returned.tabs.currentWidget() is returned.main_splitter
            assert restored.student.isChecked()
        assert page._payload() == before
    finally:
        finish(returned, app)


def test_new_external_handoff_takes_focus_before_show_and_is_not_overridden_later(desk):
    dialog, panel, app, page = desk
    panel.commit(question())
    panel.student.setChecked(False)
    panel.navigate_to_page(list(panel.layout_result['pages'])[-1])
    dialog.reject()
    returned = LessonDesignDialog(page)
    try:
        restored = returned.composition_panel
        fresh = personal('新选入的完整教材说明')
        restored.add_result({'resources': [fresh]}, digest(returned.history.value))
        returned.tabs.setCurrentWidget(restored)
        chosen = (restored.selected, restored.frame_index, restored._view_page_id)
        before = deepcopy(page._payload())
        returned.show(); settle(app)
        assert chosen == (restored.selected, restored.frame_index, restored._view_page_id)
        assert restored.selected == fresh['id'] and restored.frame_index == 0
        assert page._payload() == before
    finally:
        finish(returned, app)


def test_failed_recovery_close_keeps_previous_session_until_retry_succeeds(desk, monkeypatch):
    dialog, panel, app, page = desk
    panel.commit(teacher_note(question()))
    panel.student.setChecked(False)
    returned = reopen(dialog, page, app)
    remembered = deepcopy(page._lesson_editor_session)
    try:
        restored = returned.composition_panel
        restored.navigate_to_page(list(restored.layout_result['pages'])[-1])
        assert restored._view_page_id != remembered['page_id']
        before = deepcopy(page._payload())
        with monkeypatch.context() as failed_close:
            failed_close.setattr(page.recovery, 'flush', lambda: False)
            returned.reject()
            assert returned.isVisible()
            assert page._lesson_editor_session == remembered and page._payload() == before
        returned.reject()
        assert not returned.isVisible()
        assert page._lesson_editor_session['page_id'] == restored._view_page_id
        assert page._payload() == before
    finally:
        finish(returned, app)
