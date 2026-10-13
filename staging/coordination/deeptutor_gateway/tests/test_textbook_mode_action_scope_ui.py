"""Mode isolation regression: own synthetic memory sources and offscreen Qt."""
from copy import deepcopy
import pytest
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QTreeWidgetItem
from test_personal_textbook_compact_reading_ui import compact as compact, app as app, workspace as workspace
from test_textbook_preparation_context_ui import BOOK, SECTION, TOPIC_A, CONCEPT

pytestmark = pytest.mark.parametrize('workspace', ['personal'], indirect=True)


def test_personal_reader_hides_builtin_action_and_retains_personal_scope(compact, app):
    w = compact
    assert not w.page.section_materials_button.isVisible()
    assert w.owner.section_materials_button.isVisible() and w.owner.section_materials_button.isEnabled()
    received = []
    w.page.section_materials_requested.connect(received.append)
    w.owner.section_materials_button.click()
    assert received == [{
        'book_key': 'personal:' + BOOK['source_id'],
        'section_filter_key': 'personal:' + BOOK['source_id'] + ':' + SECTION['section_id'],
        'selection': {'mode': 'personal',
                      'selection': {key: BOOK[key] for key in ('source_id', 'revision')},
                      'section_id': SECTION['section_id'], 'study_revision': 'e' * 64}}]
    assert w.reader.view.viewport().height() >= 250


def test_return_to_builtin_restores_action_scope_and_busy_no_section_gates(compact, app):
    w, p = compact, compact.page
    assert not p.section_materials_button.isVisible()
    source, page = deepcopy(w.reader._source), w.reader.pages.currentData()
    section = {'volume_id': 'V1', 'section_key': 'S1', 'title': '合成内置节',
               'revision': 'f' * 64, 'pdf_pages': [1, 2, 3]}
    p._snapshot = {'concepts': [deepcopy(CONCEPT)], 'assets': [], 'reading_notes': [],
                   'sections': [section], 'local_books': []}
    with QSignalBlocker(p.book):
        p.book.addItem('合成内置教材', 'V1')
        p.book.setCurrentIndex(p.book.count() - 1)
    with QSignalBlocker(p.tree):
        p.tree.clear()
        leaf = QTreeWidgetItem([section['title']])
        leaf.setData(0, Qt.ItemDataRole.UserRole, 'S1')
        p.tree.addTopLevelItem(leaf)
        p.tree.setCurrentItem(leaf)
    p._personal_mode(False)
    p._section_changed()
    app.processEvents()
    assert p.section_materials_button.isVisible() and p.section_materials_button.isEnabled()
    received = []
    p.section_materials_requested.connect(received.append)
    p.section_materials_button.click()
    assert received == [{'book_key': 'registered:V1', 'section_filter_key': 'S1',
                         'selection': {'mode': 'registered', 'section_key': 'S1', 'revision': 'f' * 64}}]
    p.set_preparation_context(TOPIC_A, busy=True)
    p._set_read_controls(True)
    assert not p.section_materials_button.isEnabled()
    p.section_materials_button.click()
    assert len(received) == 1
    p.set_preparation_context(TOPIC_A, busy=False)
    p._set_read_controls(True)
    assert p.section_materials_button.isEnabled()
    with QSignalBlocker(p.tree):
        p.tree.clear()
    p._section_changed()
    assert p.section_materials_button.isVisible() and not p.section_materials_button.isEnabled()
    p.section_materials_button.click()
    assert len(received) == 1
    assert w.reader._source == source and w.reader.pages.currentData() == page
