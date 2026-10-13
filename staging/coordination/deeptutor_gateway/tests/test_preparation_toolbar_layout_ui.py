"""Styled native preparation controls wrap without changing the active lesson."""
from copy import deepcopy
from itertools import combinations
import os
from pathlib import Path

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QPushButton, QStyle,
                              QStyleOptionButton, QStyleOptionToolButton, QToolButton)

from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_preparation import StudioPreparationPage
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def desk(tmp_path):
    app = QApplication.instance() or QApplication([])
    install_ui_font(app)
    facade = build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=tmp_path / 'state'))
    tasks = DesktopTaskBridge()
    page = StudioPreparationPage(facade, tasks)
    # Layout and local selector tests do not load profiles, generate, or save.
    page._availability_timer.stop()
    page.recovery.timer.stop()
    page.setStyleSheet(WORKBENCH_STYLE)
    page.topic.setText('当前课题')
    page.materials.setPlainText('教师已选资料')
    page.resize(800, 800)
    page.show()
    app.processEvents()
    yield page, app
    page.close()
    tasks.shutdown(1000)
    page.deleteLater()
    app.processEvents()


def mapped_rect(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def toolbar_buttons(page):
    return (page.textbook_button, page.handout_button, page.lesson_design_button,
            page.workspace.requirements, page.workspace.materials, page.workspace.results,
            page.recovery.new_button, page.workspace.more, page.personal_template_button)


def assert_readable(button):
    assert button.font().pointSizeF() >= 11
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    if isinstance(button, QPushButton):
        option = QStyleOptionButton()
        button.initStyleOption(option)
        contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert contents.width() >= text_width, (button.text(), contents.width(), text_width)
    else:
        option = QStyleOptionToolButton()
        button.initStyleOption(option)
        size = button.style().sizeFromContents(QStyle.ContentsType.CT_ToolButton, option,
            QSize(text_width, button.fontMetrics().height()), button)
        assert button.width() >= size.width(), (button.text(), button.width(), size.width())


@pytest.mark.parametrize('width', [420, 800, 1200])
@pytest.mark.parametrize('height', [660, 800])
def test_all_real_toolbar_labels_fit_without_overlap_or_horizontal_clip(desk, width, height):
    page, app = desk
    page.resize(width, height)
    app.processEvents()
    app.processEvents()
    assert (page.width(), page.height()) == (width, height)
    buttons = toolbar_buttons(page)
    actual = page.workspace.toolbar.findChildren(QPushButton) + page.workspace.toolbar.findChildren(QToolButton)
    assert len(actual) == len(buttons) == 9
    assert set(actual) == set(buttons)
    rects = [mapped_rect(button, page.workspace.toolbar) for button in buttons]
    assert all(button.isVisible() for button in buttons)
    assert all(page.workspace.toolbar.rect().contains(rect) for rect in rects)
    assert all(not a.intersects(b) for a, b in combinations(rects, 2))
    for button in buttons:
        assert_readable(button)
        assert mapped_rect(button, page).bottom() < page.editor_scroll.geometry().top()
    primary = [mapped_rect(button, page.workspace.toolbar) for button in buttons[:3]]
    secondary = [mapped_rect(button, page.workspace.toolbar) for button in buttons[3:]]
    assert max(rect.bottom() for rect in primary) < min(rect.top() for rect in secondary)
    selectors = (page.material_insertion_position, page.material_insertion_target)
    selector_rects = [mapped_rect(widget, page.material_insertion_bar) for widget in selectors]
    assert all(widget.isVisible() for widget in selectors)
    assert all(page.material_insertion_bar.rect().contains(rect) for rect in selector_rects)
    assert not selector_rects[0].intersects(selector_rects[1])
    assert page.editor_scroll.horizontalScrollBar().maximum() == 0
    assert page.editor_scroll.viewport().height() >= 160
    if width == 420:
        assert selector_rects[1].top() > selector_rects[0].bottom()
    folder = os.getenv('SHCHEM_TOOLBAR_EVIDENCE')
    if folder:
        output = Path(folder)
        output.mkdir(parents=True, exist_ok=True)
        assert page.grab().save(str(output / f'preparation-{width}x{height}.png'))


def test_resizing_keeps_actual_insertion_selection_epoch_and_payload(desk):
    page, app = desk
    page._lesson_design = {'id': 'layout-lesson', 'composition': {'resources': [
        {'id': 'first', 'title': '第一项完整素材'}, {'id': 'second', 'title': '第二项完整素材'}]}}
    page.refresh_material_insertion_targets()
    notices = []
    page.preparation_target_changed.connect(lambda: notices.append(True))
    page.material_insertion_position.setCurrentIndex(page.material_insertion_position.findData('after'))
    page.material_insertion_target.setCurrentIndex(page.material_insertion_target.findData('second'))
    before = deepcopy(page._payload())
    epoch = page._material_position_epoch
    assert len(notices) == 2
    for width in (420, 1200, 800, 420, 800):
        page.resize(width, 660)
        app.processEvents()
        assert page.material_insertion_position.currentData() == 'after'
        assert page.material_insertion_target.currentData() == 'second'
        assert page.material_insertion_target.isEnabled()
        assert page._material_position_epoch == epoch
        assert page._payload() == before
        assert len(notices) == 2
    page.material_insertion_position.setCurrentIndex(page.material_insertion_position.findData('end'))
    assert not page.material_insertion_target.isEnabled()
    assert page.material_insertion_target.currentData() == 'second'
    assert page._material_position_epoch == epoch + 1


def test_wrapped_source_buttons_use_real_single_handlers_and_keep_teacher_form(desk):
    page, app = desk
    page.resize(420, 660)
    app.processEvents()
    before = deepcopy(page._payload())
    textbook, handout = [], []
    page.textbook_requested.connect(lambda: textbook.append(True))
    page.handout_requested.connect(lambda: handout.append(True))
    for width in (420, 1200, 420, 800):
        page.resize(width, 660)
        app.processEvents()
    QTest.mouseClick(page.textbook_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(page.handout_button, Qt.MouseButton.LeftButton)
    assert textbook == [True] and handout == [True]
    assert page._payload() == before
    page._lesson_busy = True
    QTest.mouseClick(page.textbook_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(page.handout_button, Qt.MouseButton.LeftButton)
    assert textbook == [True] and handout == [True]
    assert page._payload() == before
    page._lesson_busy = False
