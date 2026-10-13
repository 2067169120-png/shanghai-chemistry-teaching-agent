"""Navigate actual audience pages without editing placement, sources or history."""
from copy import deepcopy
from uuid import uuid4

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest

from test_composition_ui import desk, window
from test_composition_student_pages import question, teacher_note
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource, add_resource, FORMATS
from integrations.deeptutor_shchem_v1.desktop_workbench import lesson_composition_panel as ui
from integrations.deeptutor_shchem_v1.desktop_workbench.composition_page_navigator import visible_fragments


def page_ids(panel):
    tree = panel.page_navigation.tree
    return [tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0]
            for i in range(tree.topLevelItemCount())]


def choose_tree(panel, item, app):
    panel.source_tabs.setCurrentWidget(panel.page_navigation.tree)
    panel.page_navigation.tree.scrollToItem(item)
    settle(app)
    rectangle = panel.page_navigation.tree.visualItemRect(item)
    assert not rectangle.isEmpty()
    QTest.mouseClick(panel.page_navigation.tree.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center())
    settle(app)


def test_buttons_combo_and_outline_reach_continuations_without_relayout_or_content_write(desk, monkeypatch):
    dialog, panel, app, page = desk
    panel.commit(question())
    panel.student.setChecked(False); settle(app)
    before = deepcopy(dialog.history.value)
    before_payload = deepcopy(page._payload())
    assert len(page_ids(panel)) > 2
    with monkeypatch.context() as navigation_only:
        navigation_only.setattr(ui, 'layout_composition', lambda *_args, **_kwargs: pytest.fail('Navigation recalculated the full draft'))
        nav = panel.page_navigation
        QTest.mouseClick(nav.next, Qt.MouseButton.LeftButton); settle(app)
        assert nav.pages.currentIndex() == 1
        assert panel.current_frame()['page_id'] == page_ids(panel)[1]
        nav.pages.setCurrentIndex(nav.pages.count()-1); settle(app)
        assert not nav.next.isEnabled() and nav.previous.isEnabled()
        root = nav.tree.topLevelItem(1)
        root.setExpanded(True)
        child = root.child(0)
        choose_tree(panel, child, app)
        page_id, identity, frame = child.data(0, Qt.ItemDataRole.UserRole)
        assert (panel._view_page_id, panel.selected, panel.frame_index) == (page_id, identity, frame)
        assert panel.frames.currentData() == frame
        assert dialog.history.value == before and page._payload() == before_payload


def test_student_directory_omits_answers_and_teacher_titles_even_on_a_shared_page(desk):
    dialog, panel, app, _page = desk
    value = teacher_note(question())
    hidden = value['resources'][-1]
    # Place the teacher-only note beside the student question on the same page.
    value['placements'][hidden['id']]['page_id'] = value['pages'][0]
    value['pages'] = value['pages'][:-1]
    panel.commit(value); panel.student.setChecked(True); settle(app)
    before = deepcopy(dialog.history.value)
    assert page_ids(panel) == list(panel.layout_result['pages']) == [value['pages'][0]]
    assert hidden['id'] not in [r[1] for r in panel.page_navigation._fragment_items]
    assert all('仅教师可见标题' not in panel.page_navigation.pages.itemText(i)
               for i in range(panel.page_navigation.pages.count()))
    panel.student.setChecked(False); settle(app)
    assert hidden['id'] in [r[1] for r in panel.page_navigation._fragment_items]
    assert page_ids(panel) == value['pages'] and dialog.history.value == before


def test_audience_toggle_uses_original_page_ids_and_returns_to_a_visible_page(desk):
    dialog, panel, app, _page = desk
    value = teacher_note(question())
    hidden_resource = value['resources'][-1]
    tail_page = 'CP-'+uuid4().hex
    value['pages'].append(tail_page)
    tail = resource('personal_text', '末尾学生任务', [block('text', 'personal', '第二个学生可见页')],
                    {'source_path': '合成作者输入', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
    value = add_resource(value, tail, {'page_id': tail_page, 'x': 12, 'y': 12, 'width': 200, 'height': 70})
    panel.commit(value); panel.student.setChecked(False); settle(app)
    before = deepcopy(dialog.history.value)
    hidden_index = value['pages'].index(value['placements'][hidden_resource['id']]['page_id'])
    panel.page_navigation.pages.setCurrentIndex(hidden_index); settle(app)
    hidden = panel._view_page_id
    assert panel.selected == hidden_resource['id']
    QTest.mouseClick(panel.page_navigation.focus, Qt.MouseButton.LeftButton); settle(app)
    panel.student.setChecked(True); settle(app)
    assert hidden not in page_ids(panel)
    assert panel._view_page_id == panel.page_navigation.pages.currentData() == value['pages'][0]
    assert panel.page_navigation.pages.count() == 2
    width, height = FORMATS[value['format']]
    assert panel.view.mapToScene(panel.view.viewport().rect()).boundingRect().contains(QPointF(width/2,height/2))
    assert not panel.page_navigation.previous.isEnabled() and panel.page_navigation.next.isEnabled()
    panel.navigate_to_page(hidden)  # A stale page cannot navigate to hidden content.
    assert panel._view_page_id == value['pages'][0] and dialog.history.value == before


def test_intentional_blank_is_navigable_and_clears_the_previous_resource_inspector(desk):
    dialog, panel, app, page = desk
    value = question('A4')
    blank = 'CP-'+uuid4().hex
    value['pages'].append(blank)
    panel.commit(value); panel.student.setChecked(True); settle(app)
    before = deepcopy(page._payload())
    root = panel.page_navigation.tree.topLevelItem(1)
    assert root.childCount() == 0 and '空白画布页' in root.text(0)
    choose_tree(panel, root, app)
    assert panel._view_page_id == blank and panel.selected is None
    assert panel.list.currentRow() == -1 and not panel.edit_button.isEnabled()
    assert panel.original.toPlainText() == '' and page._payload() == before


def test_original_resource_and_frame_controls_keep_the_page_directory_in_sync(desk):
    dialog, panel, app, _page = desk
    panel.commit(teacher_note(question())); panel.student.setChecked(False); settle(app)
    before = deepcopy(dialog.history.value)
    panel.list.setCurrentRow(0); settle(app)
    panel.frames.setCurrentIndex(panel.frames.count()-1); settle(app)
    assert panel.page_navigation.pages.currentData() == panel.current_frame()['page_id']
    selected = panel.page_navigation.tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
    assert selected == (panel.current_frame()['page_id'], panel.selected, panel.frame_index)
    panel.list.setCurrentRow(1); settle(app)
    assert panel.page_navigation.pages.currentData() == panel.current_frame()['page_id']
    assert dialog.history.value == before


def test_focus_keeps_native_page_navigation_and_returns_to_sources_without_history_edit(desk):
    dialog, panel, app, page = desk
    panel.commit(question()); panel.student.setChecked(False); settle(app)
    dialog.resize(1000,900); settle(app)
    before = deepcopy(page._payload()); history = deepcopy(dialog.history.value)
    width = panel.view.viewport().width()
    nav = panel.page_navigation
    QTest.mouseClick(nav.focus, Qt.MouseButton.LeftButton); settle(app)
    assert not panel.sources_panel.isVisible() and not panel.inspector_panel.isVisible()
    assert panel.view.viewport().width() > width and nav.pages.isVisible()
    QTest.mouseClick(nav.next, Qt.MouseButton.LeftButton); settle(app)
    selected = (panel._view_page_id, panel.selected, panel.frame_index)
    QTest.mouseClick(nav.focus, Qt.MouseButton.LeftButton); settle(app)
    assert panel.sources_panel.isVisible() and panel.inspector_panel.isVisible()
    assert selected == (nav.pages.currentData(), panel.selected, panel.frames.currentData())
    assert page._payload() == before and dialog.history.value == history


def test_remove_and_undo_rebuild_the_directory_from_current_content(desk):
    dialog, panel, app, _page = desk
    value = teacher_note(question())
    panel.commit(value); panel.student.setChecked(False); settle(app)
    before = deepcopy(dialog.history.value)
    panel.list.setCurrentRow(1); panel.remove_selected(); settle(app)
    assert value['resources'][-1]['id'] not in [r[1] for r in panel.page_navigation._fragment_items]
    dialog.travel(False); settle(app)
    assert dialog.history.value == before
    assert value['resources'][-1]['id'] in [r[1] for r in panel.page_navigation._fragment_items]
    assert page_ids(panel) == list(panel.layout_result['pages'])


def test_failed_preview_clears_stale_directory_and_disables_all_navigation(desk):
    dialog, panel, app, _page = desk
    value = question(); panel.commit(value); settle(app)
    assert panel.page_navigation.pages.isEnabled()
    image = resource('textbook_image', '合成缺失图', [block('image', 'material', 'IMG-'+'d'*64)],
                     {'source_path': '合成.pdf', 'source_sha256': 'a'*64, 'source_revision': 'b'*64,
                      'pdf_pages': [1], 'printed_pages': 'unknown'})
    value = add_resource(value, image, {'page_id': value['pages'][0], 'x': 12, 'y': 12, 'width': 100, 'height': 60})
    panel.commit(value); settle(app)
    assert panel.layout_result is None
    assert not panel.page_navigation.pages.isEnabled() and panel.page_navigation.tree.topLevelItemCount() == 0
    assert not panel.page_navigation.previous.isEnabled() and not panel.page_navigation.next.isEnabled()


def test_only_teacher_content_has_no_student_directory_and_frame_index_uses_original_positions(desk):
    dialog, panel, app, _page = desk
    value = teacher_note(question())
    value['resources'] = value['resources'][-1:]
    value['placements'] = {value['resources'][0]['id']: value['placements'][value['resources'][0]['id']]}
    value['pages'] = value['pages'][-1:]
    panel.commit(value); panel.student.setChecked(True); settle(app)
    before = deepcopy(dialog.history.value)
    assert not page_ids(panel) and not visible_fragments(panel.layout_result)
    assert not panel.page_navigation.pages.isEnabled()
    panel.student.setChecked(False); settle(app)
    assert page_ids(panel) == value['pages'] and dialog.history.value == before
