"""Textbook handoffs preserve the chosen scope and unfinished preparation."""
import copy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QTreeWidgetItemIterator

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    TeacherWorkbenchWindow,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_study_page import (
    TextbookStudyPage,
)


def settle(app, predicate):
    deadline = time.monotonic() + 15
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    assert predicate()


def test_changed_selection_discards_late_handoff_and_unbound_candidate_is_disabled():
    app = QApplication.instance() or QApplication([])
    calls = []
    tasks = SimpleNamespace(submit=lambda *args, **kwargs: calls.append((args, kwargs)))
    facade = SimpleNamespace(textbook_study_catalog=lambda: None,
                             textbook_question_selection=lambda *_args: None)
    page = TextbookStudyPage(facade, tasks)
    app.processEvents()
    calls.clear()
    rows = [{"concept_id": key, "title": key, "summary": "合成候选",
             "source": {"sha256": "a" * 64}, "curriculum": {}}
            for key in ("bound", "unbound")]
    page._ready({"rows": rows, "books": {}, "count": 2, "imported": False,
                 "native_options": {"bound": {"revision": "native"}}})
    items = {}
    it = QTreeWidgetItemIterator(page.tree)
    while it.value():
        item = it.value()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            items[data['concept_id']] = item
        it += 1
    received = []
    page.questions_requested.connect(received.append)
    try:
        page.tree.setCurrentItem(items['bound'])
        assert page.question_button.isEnabled()
        page.question_button.click()
        assert page._link_pending and not page.preparation_button.isEnabled()
        page.tree.setCurrentItem(items['unbound'])
        calls[0][1]['on_success']({'filters': {'section': ['old']}})
        assert not received
        assert not page.question_button.isEnabled()
        assert not page.preparation_button.isEnabled()
        assert page.lecture_button.isEnabled()
        page.tree.setCurrentItem(items['bound'])
        page.question_button.click()
        calls[-1][1]['on_success']({'filters': {'section': ['current']}})
        assert received == [{'filters': {'section': ['current']}}]
    finally:
        page.deleteLater()
        app.processEvents()


def test_main_window_handoff_keeps_empty_scope_basket_and_teacher_draft(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / 'runtime/deeptutor_shchem'))
    from explorer_demo_data import seed_demo

    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        preparation_sources_dialog,
    )
    facade = seed_demo(tmp_path / 'workspace', tmp_path / 'state')
    window = TeacherWorkbenchWindow(facade)
    window.show()
    try:
        page = window.library_page
        before = copy.deepcopy(facade.basket())
        request = {'filters': {'book': ['MISSING'], 'chapter': ['missing-chapter'], 'section': ['missing-section']},
                   'label': '精确但无题的合成教材单元', 'statement': '空结果保留范围'}
        window._textbook_to_questions(request)
        settle(app, lambda: not page._loading)
        assert window.stack.currentWidget() is page
        assert page.scope.currentData() == 'word_native'
        assert page.filters == {key: set(values) for key, values in request['filters'].items()}
        assert not page.cards
        assert facade.basket() == before
        assert page.textbook_context.isVisible()
        page.textbook_requested.emit()
        assert window.stack.currentWidget() is window.textbook_page

        selected = []
        class ConfirmedDialog:
            def __init__(self, *_args):
                self.reference = {'materials': '教师确认的教材候选参考', 'warnings': []}
            def preselect_concepts(self, value):
                selected.extend(value)
                return True
            def exec(self):
                return QDialog.DialogCode.Accepted
            def deleteLater(self):
                pass
        monkeypatch.setattr(preparation_sources_dialog, 'PreparationSourcesDialog', ConfirmedDialog)
        preparation = window.preparation_page
        preparation.topic.setText('现有课题')
        preparation.materials.setPlainText('教师原材料')
        window._textbook_to_preparation({'concept_id': 'C1', 'revision': 'r1'})
        assert selected == [{'concept_id': 'C1', 'revision': 'r1'}]
        assert preparation.topic.text() == '现有课题'
        assert preparation.materials.toPlainText().startswith('教师原材料')
        assert '教师确认的教材候选参考' in preparation.materials.toPlainText()
        assert window.stack.currentWidget() is preparation
    finally:
        window.preparation_page.topic.clear()
        window.preparation_page.materials.clear()
        window.close()
        window.deleteLater()
        app.processEvents()
