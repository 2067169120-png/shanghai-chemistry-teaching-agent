"""The real lesson dialog keeps source selection usable at laptop height."""
from copy import deepcopy
from itertools import combinations

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from test_composition_render import app
from test_lesson_design import sample
from test_composition_insertion import fixture_compositions, text_resource
from test_textbook_crop_composition_handoff import (
    crop_fixture, section_fixture, asset_fixture, shelf_fixture, DeferredTasks)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import enable_composition
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_activity_dialogs import ActivityPicker
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_preparation import StudioPreparationPage
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE


@pytest.fixture
def compact_desk(app, crop_fixture):
    old_style = app.styleSheet(); app.setStyleSheet(WORKBENCH_STYLE)
    page = StudioPreparationPage(crop_fixture.facade, DeferredTasks())
    page._availability_timer.stop()
    assert page.recovery is None
    page.topic.setText('短窗口中的素材编排')
    page._lesson_design = enable_composition(sample()['lesson_design'])
    old, _, anchor = fixture_compositions()
    page._lesson_design['composition'] = old
    dialog = LessonDesignDialog(page); dialog.resize(1200, 800)
    panel = dialog.composition_panel; dialog.tabs.setCurrentWidget(panel)
    dialog.show(); app.processEvents()
    yield dialog, panel, app, page, anchor
    dialog.reject(); dialog.deleteLater(); page.close(); page.deleteLater()
    app.processEvents(); app.setStyleSheet(old_style)


def mapped_rect(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def select_anchor(panel, anchor, app):
    item = next(panel.list.item(i) for i in range(panel.list.count())
                if panel.list.item(i).data(Qt.ItemDataRole.UserRole) == anchor)
    panel.list.scrollToItem(item); app.processEvents()
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton,
                    pos=panel.list.visualItemRect(item).center())
    app.processEvents(); assert panel.selected == anchor


def test_short_actual_dialog_keeps_list_target_and_all_actions_reachable(compact_desk):
    dialog, panel, app, _, anchor = compact_desk
    assert (dialog.width(), dialog.height()) == (1200, 800)
    select_anchor(panel, anchor, app)
    # A real styled item is about forty pixels high: allow at least two full
    # choices plus a partial next item without surrendering the action region.
    assert panel.list.viewport().height() >= 2 * panel.list.sizeHintForRow(0)
    controls = (panel.source_tabs, panel.insertion_position, panel.insertion_anchor, panel.source_actions)
    rects = [mapped_rect(w, panel.sources_panel) for w in controls]
    assert all(panel.sources_panel.rect().contains(rect) for rect in rects)
    assert all(not a.intersects(b) for a, b in combinations(rects, 2))
    assert '2 · ' in panel.insertion_anchor.text()
    scroll = panel.source_actions; content = scroll.widget()
    buttons = content.findChildren(QPushButton)
    expected_actions = {
        '教学环节…', '完整题…', '教材原页／切片…', '课外／本机配图…', '知识卡…',
        '通读讲义并选材…', '讲义知识…', '讲义图…', '个人文字…', '分步讲解…',
        '上移', '下移', '教师一栏排满并续页', '教师两栏排满并续页', '移除所选素材',
    }
    assert len(buttons) == len(expected_actions) == 15
    assert {button.text() for button in buttons} == expected_actions
    assert panel.step_flow_button in buttons and panel.step_flow_button.text() == '分步讲解…'
    assert panel.step_flow_button.accessibleName() == '加入完整分步讲解流程'
    button_rects = [mapped_rect(button, content) for button in buttons]
    assert all(not a.intersects(b) for a, b in combinations(button_rects, 2))
    assert all(button.height() >= button.minimumSizeHint().height() for button in buttons)
    assert scroll.verticalScrollBar().maximum() > 0
    for button in buttons:
        scroll.ensureWidgetVisible(button, 0, 0); app.processEvents()
        assert scroll.viewport().rect().contains(mapped_rect(button, scroll.viewport())), button.text()
        assert panel.insertion_anchor.isVisible() and panel.insertion_position.isVisible()
    assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum()


def test_scrolled_actions_keep_batch_insertion_remove_undo_and_stale_guard(compact_desk):
    dialog, panel, app, page, anchor = compact_desk
    select_anchor(panel, anchor, app)
    panel.insertion_position.setCurrentIndex(panel.insertion_position.findData('after'))
    before = deepcopy(dialog.history.value); errors = []
    def finish_picker():
        try:
            picker = app.activeModalWidget(); assert isinstance(picker, ActivityPicker)
            picker.list.item(0).setCheckState(Qt.CheckState.Checked)
            QTest.mouseClick(picker.apply_button, Qt.MouseButton.LeftButton)
        except BaseException as exc:
            errors.append(exc)
            if app.activeModalWidget() is not None: app.activeModalWidget().reject()
    button = next(b for b in panel.source_actions.findChildren(QPushButton) if b.text() == '教学环节…')
    panel.source_actions.ensureWidgetVisible(button, 0, 0); app.processEvents()
    QTimer.singleShot(20, finish_picker)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton); app.processEvents()
    assert not errors, errors
    after = deepcopy(dialog.history.value); old = before['composition']; value = after['composition']
    assert len(value['resources']) == len(old['resources']) + 1
    assert value['resources'][1]['id'] == anchor
    assert value['resources'][2]['original']['kind'] == 'lesson_activity'
    assert [r for r in value['resources'] if r['id'] in old['placements']] == old['resources']
    assert {k: value['placements'][k] for k in old['placements']} == old['placements']
    assert len(dialog.history.back) == 1 and not panel.layout_result['errors']
    inserted = value['resources'][2]['id']; panel.selected = inserted; panel.refresh(); app.processEvents()
    remove = next(b for b in panel.source_actions.findChildren(QPushButton) if b.text() == '移除所选素材')
    panel.source_actions.ensureWidgetVisible(remove, 0, 0); app.processEvents()
    assert panel.source_actions.viewport().rect().contains(mapped_rect(remove, panel.source_actions.viewport()))
    QTest.mouseClick(remove, Qt.MouseButton.LeftButton); app.processEvents()
    assert inserted not in {r['id'] for r in panel.composition()['resources']}
    dialog.travel(False); assert dialog.history.value == after
    dialog.travel(False); assert dialog.history.value == before
    dialog.travel(True); assert dialog.history.value == after
    frozen = panel.freeze_insertion(); expected = digest(after)
    panel.insertion_position.setCurrentIndex(panel.insertion_position.findData('before'))
    with pytest.raises(ValueError, match='加入位置在选材期间变化'):
        panel.add_result({'resources': [text_resource('迟到素材')]}, expected, insertion=frozen)
    assert dialog.history.value == after and page.recovery is None
