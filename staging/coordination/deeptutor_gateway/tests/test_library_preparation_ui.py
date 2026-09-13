import json
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_desktop_library_ui import (
    _card,
    _detail,
    _ManualBridge,
    _search_result,
)
from test_desktop_library_ui import (
    _Facade as LibraryFacade,
)
from test_desktop_ui import _Facade, _fill_preparation_page

from integrations.deeptutor_shchem_v1.desktop_library_preparation import (
    library_preparation_reference,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_limits import MAX_MATERIALS
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import LibraryPage
from integrations.deeptutor_shchem_v1.desktop_workbench.library_preparation_dialog import (
    LibraryPreparationDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    TeacherWorkbenchWindow,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_dialog_preview_selection_answers_and_empty(app):
    detail = _detail("A", "主题甲")
    dialog = LibraryPreparationDialog(detail)
    assert dialog.reference["question_count"] == 2
    assert dialog.preview.toPlainText() == dialog.reference["materials"]
    assert "来源答案甲" in dialog.preview.toPlainText()
    dialog.include_answers.setChecked(False)
    assert "来源答案甲" not in dialog.preview.toPlainText()
    dialog.units.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.reference["question_count"] == 1
    assert "未自动带入未选小问" in dialog.preview.toPlainText()
    dialog.units.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.reference is None and not dialog.import_button.isEnabled()
    assert not dialog.preview.toPlainText()
    dialog.close()


def test_selection_button_tracks_loaded_identity_and_ignores_stale_callback(app):
    bridge = _ManualBridge()
    facade = LibraryFacade()
    page = LibraryPage(facade, bridge)
    received = []
    page.preparation_reference_requested.connect(received.append)
    page._apply_results(_search_result(_card("A", "甲"), _card("B", "乙")))
    old = bridge.pending.pop(0)
    assert not page.preparation_button.isEnabled()
    page.results.setCurrentRow(1)
    current = bridge.pending.pop(0)
    bridge.succeed(old, facade.details["A"])
    assert not page.preparation_button.isEnabled()
    bridge.succeed(current, facade.details["B"])
    assert page.preparation_button.isEnabled()
    page.preparation_button.click()
    assert received == [facade.details["B"]]
    assert facade.added == []
    page.results.setCurrentRow(-1)
    page._send_preparation_reference()
    assert len(received) == 1 and not page.preparation_button.isEnabled()
    page.close()


@pytest.mark.parametrize("change", [{"key": "B"}, {"scope": "wave1"}, {"parts": ()}])
def test_mismatched_or_empty_detail_cannot_enter_preparation(app, change):
    bridge = _ManualBridge()
    page = LibraryPage(LibraryFacade(), bridge)
    page._apply_results(_search_result(_card("A", "甲")))
    task = bridge.pending.pop(0)
    bridge.succeed(task, replace(_detail("A", "甲"), **change))
    assert not page.preparation_button.isEnabled()
    page.close()


@pytest.mark.parametrize("accept", [False, True])
def test_actual_window_signal_preserves_form_basket_and_paper(app, monkeypatch, accept):
    import integrations.deeptutor_shchem_v1.desktop_workbench.library_preparation_dialog as module

    detail = _detail("A", "主题甲")
    references = []

    class Choice:
        DialogCode = QDialog.DialogCode

        def __init__(self, value, *_args):
            assert value is detail
            self.reference = library_preparation_reference(value, [value.parts[1].key])
            references.append(self.reference)

        def exec(self):
            return self.DialogCode.Accepted if accept else self.DialogCode.Rejected

    monkeypatch.setattr(module, "LibraryPreparationDialog", Choice)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    facade = _Facade()
    window = TeacherWorkbenchWindow(facade)
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    basket = facade.basket()
    paper = window.paper_page._composer.model.draft_payload()
    window.navigate("home")
    window.library_page.preparation_reference_requested.emit(detail)
    after = page._payload()
    assert {k: v for k, v in after.items() if k != "materials"} == {
        k: v for k, v in before.items() if k != "materials"
    }
    if accept:
        assert after["materials"].startswith(before["materials"])
        assert references[0]["materials"] in after["materials"]
        assert json.dumps(references[0]["materials"], ensure_ascii=False)[
            1:-1
        ] in _prompt(normalize_preparation_payload(after))
        assert window.stack.currentWidget() is page
        window.library_page.preparation_reference_requested.emit(detail)
        assert page._payload() == after
    else:
        assert after == before
        assert window.stack.currentWidget() is window.home_page
    assert facade.basket() == basket
    assert window.paper_page._composer.model.draft_payload() == paper
    assert facade.saved_preparation_payloads == []
    window.close()
    window.tasks.shutdown()


@pytest.mark.parametrize(
    "busy_field",
    [
        "_save_task_id",
        "_generation_qt_task_id",
        "_active_preparation_task_id",
        "_library_image_task_id",
    ],
)
def test_busy_lesson_rejects_import_before_opening_dialog(app, monkeypatch, busy_field):
    import integrations.deeptutor_shchem_v1.desktop_workbench.library_preparation_dialog as module

    def forbidden(*_args):
        raise AssertionError("must not create a dialog while busy")

    monkeypatch.setattr(module, "LibraryPreparationDialog", forbidden)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    setattr(page, busy_field, "active")
    assert not page.import_library_reference(_detail("A", "甲"))
    assert page._payload() == before
    setattr(page, busy_field, None)
    window.close()
    window.tasks.shutdown()


def test_total_material_limit_preserves_existing_form(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(
        LibraryPreparationDialog, "exec", lambda _self: QDialog.DialogCode.Accepted
    )
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    existing = "已填教材资料"
    existing *= MAX_MATERIALS // len(existing) + 1
    page.materials.setPlainText(existing)
    before = page._payload()
    assert not page.import_library_reference(_detail("A", "甲"))
    assert page._payload() == before
    window.close()
    window.tasks.shutdown()


def test_word_question_reference_signal_appends_without_resetting_lesson(app):
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    reference = {"materials": "教师选题：写出水的化学式。原文答案：H2O。", "warnings": []}
    window.library_page.word_reference_requested.emit(reference)
    after = page._payload()
    assert after["materials"].startswith(before["materials"])
    assert reference["materials"] in after["materials"]
    assert {k: v for k, v in before.items() if k != "materials"} == {k: v for k, v in after.items() if k != "materials"}
    assert window.stack.currentWidget() is page
    window.close()
    window.tasks.shutdown()
