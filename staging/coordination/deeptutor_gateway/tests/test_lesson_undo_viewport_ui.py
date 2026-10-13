"""Pure in-memory multi-page Qt checks for undo/redo canvas navigation.

No facade, original file, profile, Office renderer or classroom course is read.
These synthetic pages establish viewport geometry only.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QDialog, QLineEdit, QPlainTextEdit, QSpinBox, QWidget

from test_desktop_library_ui import qt_app
from test_desktop_studio_ui import settle

from integrations.deeptutor_shchem_v1.desktop_lesson_design import new_design
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FORMATS, add_resource, block, enable_composition, new_composition, resource,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


def _material(title):
    return resource('personal_text', title, [block('text', 'personal', title)],
        {'source_path': 'synthetic memory only', 'source_sha256': 'unknown', 'source_revision': 'unknown'})


def _append_page(composition, item):
    value = deepcopy(composition)
    page = 'CP-' + uuid4().hex
    value['pages'].append(page)
    width, height = FORMATS[value['format']]
    return add_resource(value, item, {'page_id': page, 'x': 12, 'y': 12,
                                     'width': width - 24, 'height': height - 24}), page


class _MemoryPreparationPage(QWidget):
    def __init__(self, composition):
        super().__init__()
        # No services or stores: any attempted facade I/O fails immediately.
        self.facade = SimpleNamespace()
        self.tasks = SimpleNamespace()
        self.recovery = None
        self.topic = QLineEdit('Synthetic navigation geometry')
        self.materials = QPlainTextEdit('Synthetic in-memory material')
        self.lesson_count = QSpinBox()
        self.lesson_count.setValue(1)
        self.lesson_minutes = QSpinBox()
        self.lesson_minutes.setValue(40)
        self.image_assets_widget = SimpleNamespace(assets=lambda: [])
        self._lesson_design = enable_composition(new_design({'objective': ''}))
        self._lesson_design['composition'] = deepcopy(composition)
        self._form_baseline = self._payload()

    def _payload(self):
        return {'topic': self.topic.text(), 'objective': '',
            'materials': self.materials.toPlainText(), 'image_assets': [],
            'lesson_design': deepcopy(self._lesson_design)}


@pytest.fixture
def memory_editor(qt_app):
    created = []
    def build(format):
        value = new_composition(format)
        width, height = FORMATS[format]
        first = _material('First original page')
        value = add_resource(value, first, {'page_id': value['pages'][0], 'x': 12, 'y': 12,
                                          'width': width - 24, 'height': height - 24})
        second = _material('Second original page')
        value, second_page = _append_page(value, second)
        page = _MemoryPreparationPage(value)
        dialog = LessonDesignDialog(page)
        dialog.resize(1200, 850)
        dialog.tabs.setCurrentWidget(dialog.composition_panel)
        dialog.show()
        settle(qt_app)
        created.append((dialog, page))
        return SimpleNamespace(dialog=dialog, panel=dialog.composition_panel, app=qt_app,
                               first=first, second=second, second_page=second_page)
    yield build
    for dialog, page in created:
        # No save/framework operation is needed when discarding this fixture.
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        page.deleteLater()
    settle(qt_app)


def _assert_view_matches_navigation(case, expected_page, expected_title):
    panel = case.panel
    settle(case.app)
    pages = list(panel.layout_result['pages'])
    assert expected_page in pages
    assert panel.page_navigation.pages.currentData() == expected_page
    assert expected_title in panel.page_navigation.pages.currentText()
    assert panel.visible_page() == expected_page
    width, height = FORMATS[panel.composition()['format']]
    page_y = pages.index(expected_page) * (height + 12)
    page_rect = QRectF(0, page_y, width, height)
    center = panel.view.mapToScene(panel.view.viewport().rect().center())
    assert page_rect.contains(center), {
        'navigation': panel.page_navigation.pages.currentText(),
        'center': (center.x(), center.y()),
        'expected_rect': (0, page_y, width, height),
        'viewport_page_index': int(center.y() // (height + 12)),
    }


@pytest.mark.parametrize('format', ['A4', '16:9'])
def test_undo_removed_selected_page_and_redo_realign_actual_viewport(memory_editor, format):
    case = memory_editor(format)
    dialog, panel = case.dialog, case.panel
    original = deepcopy(dialog.history.value)
    first_page = original['composition']['pages'][0]
    added = _material('Added third page')
    changed = deepcopy(original)
    changed['composition'], added_page = _append_page(changed['composition'], added)
    dialog.history.put(changed)
    dialog.sync_page()
    panel.navigate_to_page(added_page, added['id'], 0)
    _assert_view_matches_navigation(case, added_page, added['title'])

    dialog.travel(False)
    assert dialog.history.value == original
    assert len(panel.layout_result['pages']) == 2
    _assert_view_matches_navigation(case, first_page, case.first['title'])

    dialog.travel(True)
    assert dialog.history.value == changed
    assert len(panel.layout_result['pages']) == 3
    _assert_view_matches_navigation(case, added_page, added['title'])

    dialog.travel(False)
    assert dialog.history.value == original
    _assert_view_matches_navigation(case, first_page, case.first['title'])


@pytest.mark.parametrize('format', ['A4', '16:9'])
def test_teacher_mode_retains_existing_selected_page_through_undo_redo(memory_editor, format):
    case = memory_editor(format)
    dialog, panel = case.dialog, case.panel
    panel.student.setChecked(False)
    panel.navigate_to_page(case.second_page, case.second['id'], 0)
    original = deepcopy(dialog.history.value)
    _assert_view_matches_navigation(case, case.second_page, case.second['title'])
    changed = deepcopy(original)
    changed['composition'], _ = _append_page(changed['composition'], _material('New unselected page'))
    dialog.history.put(changed)
    dialog.sync_page()

    dialog.travel(False)
    assert dialog.history.value == original and not panel.student.isChecked()
    assert panel.selected == case.second['id']
    _assert_view_matches_navigation(case, case.second_page, case.second['title'])

    dialog.travel(True)
    assert dialog.history.value == changed and not panel.student.isChecked()
    assert panel.selected == case.second['id']
    _assert_view_matches_navigation(case, case.second_page, case.second['title'])
