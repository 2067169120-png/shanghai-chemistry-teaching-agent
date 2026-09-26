"""Synthetic native basket recovery and narrow-layout regression checks."""
from copy import deepcopy
from pathlib import Path
import os
from types import SimpleNamespace
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel, QPushButton
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE


class BasketFacade:
    def __init__(self):
        self.rows = [{"key": key, "title_zh": "合成验收题 " + key,
                      "source_zh": "自编测试材料", "item_kind": "core_theme"}
                     for key in ("a", "b", "c")]
        self.read_error = False
        self.write_error = False
        self.writes = 0

    def basket(self):
        if self.read_error:
            raise OSError("private-path-must-not-appear")
        return deepcopy(self.rows)

    def remove_basket_item(self, key):
        if self.write_error:
            raise OSError("private-path-must-not-appear")
        self.writes += 1
        self.rows = [row for row in self.rows if row["key"] != key]
        return len(self.rows)

    def move_basket_item(self, key, delta):
        if self.write_error:
            raise OSError("private-path-must-not-appear")
        self.writes += 1
        index = next(i for i, row in enumerate(self.rows) if row["key"] == key)
        target = max(0, min(index + delta, len(self.rows) - 1))
        self.rows[index], self.rows[target] = self.rows[target], self.rows[index]
        return len(self.rows)


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    instance.processEvents()


@pytest.fixture
def dialog(app):
    facade = BasketFacade()
    value = ExplorerBasketDialog(facade)
    value.setStyleSheet(WORKBENCH_STYLE)
    value.show()
    app.processEvents()
    yield value, facade
    value.close()
    value.deleteLater()
    app.processEvents()


def test_read_failure_preserves_rows_selection_and_blocks_dependent_actions(dialog):
    value, facade = dialog
    value.list.setCurrentRow(1)
    facade.read_error = True
    assert value.refresh() is False
    assert value.list.count() == 3 and value.list.currentRow() == 1
    assert "0 项" not in value.heading.text() and "待核对" in value.heading.text()
    assert "上次显示" in value.status.text() and "private-path" not in value.status.text()
    for button in (value.up, value.down, value.remove, value.preview, value.edit_button):
        assert not button.isEnabled()
    value.change(0)
    assert facade.writes == 0
    assert value.retry.isVisible()


def test_retry_recovers_selection_and_clears_stale_error(dialog):
    value, facade = dialog
    value.list.setCurrentRow(1)
    facade.read_error = True
    value.refresh()
    facade.read_error = False
    value.retry.click()
    assert value.list.currentRow() == 1
    assert value.status.text() == value.DEFAULT_STATUS
    assert not value.retry.isVisible() and value.preview.isEnabled()


def test_initial_failure_is_unknown_not_zero(app):
    facade = BasketFacade()
    facade.read_error = True
    value = ExplorerBasketDialog(facade)
    try:
        assert "待核对" in value.heading.text()
        assert "0 项" not in value.heading.text()
        assert not value.preview.isEnabled() and not value.edit_button.isEnabled()
    finally:
        value.close()
        value.deleteLater()


def test_empty_read_disables_both_output_actions(dialog):
    value, facade = dialog
    facade.rows = []
    assert value.refresh() is True
    assert value.list.count() == 0 and "0 项" in value.heading.text()
    assert not value.preview.isEnabled() and not value.edit_button.isEnabled()


def test_remove_keeps_nearest_remaining_row(dialog):
    value, facade = dialog
    value.list.setCurrentRow(1)
    value.remove.click()
    assert [row["key"] for row in facade.rows] == ["a", "c"]
    assert value.list.currentItem().data(Qt.ItemDataRole.UserRole) == "c"


def test_write_error_requires_reread_and_does_not_emit_success(dialog):
    value, facade = dialog
    events = []
    value.basket_changed.connect(events.append)
    facade.write_error = True
    value.change(0)
    assert events == [] and len(facade.rows) == 3
    assert not value.remove.isEnabled() and value.retry.isVisible()
    facade.write_error = False
    value.retry.click()
    assert value.remove.isEnabled()


@pytest.mark.parametrize("method", ["preview_paper", "edit"])
def test_action_rechecks_store_before_leaving_dialog(dialog, method):
    value, facade = dialog
    events = []
    value.preview_requested.connect(lambda: events.append("preview"))
    value.edit_requested.connect(lambda: events.append("edit"))
    facade.read_error = True
    getattr(value, method)()
    assert events == [] and value.isVisible()


def test_narrow_actions_stack_and_wide_actions_return(dialog, app):
    value, _ = dialog
    for width, direction in ((420, QBoxLayout.Direction.TopToBottom),
                             (680, QBoxLayout.Direction.LeftToRight)):
        value.resize(width, 700)
        app.processEvents()
        assert value.actions.direction() == direction
        for button in (value.preview, value.edit_button, value.remove, value.up, value.down):
            rect = button.geometry()
            assert rect.left() >= 0 and rect.right() < value.width()
            assert rect.bottom() < value.height()


def test_bad_row_does_not_partially_replace_last_view(dialog):
    value, facade = dialog
    facade.rows.append({"title_zh": "缺少稳定身份"})
    assert value.refresh() is False and value.list.count() == 3
    assert not value.preview.isEnabled()


def page_probe(facade):
    card = SimpleNamespace(ready=True, entry={"key": "new", "lane": "master"}, add=QPushButton())
    page = SimpleNamespace(facade=facade, cards=[card], basket_button=QPushButton(),
                           basket_label=QLabel(), preview_button=QPushButton(),
                           _adding=set(), _active_card=card)
    page._basket_unavailable = lambda: QuestionExplorerPage._basket_unavailable(page)
    page.refresh_basket = lambda: QuestionExplorerPage.refresh_basket(page)
    return page, card


def test_page_failure_blocks_add_and_does_not_claim_zero(app):
    facade = BasketFacade()
    page, card = page_probe(facade)
    assert page.refresh_basket() == 3
    facade.read_error = True
    assert page.refresh_basket() is None
    assert not card.add.isEnabled() and not page.preview_button.isEnabled()
    assert "待核对" in page.basket_button.text()
    # Direct slot invocation must not raise or schedule an add task.
    QuestionExplorerPage.add(page, card)
    assert facade.writes == 0 and not page._adding
    facade.read_error = False
    assert page.refresh_basket() == 3 and card.add.isEnabled()


def test_candidate_screenshots_are_real_qt_windows(dialog, app):
    destination = os.environ.get("SHCHEM_CANDIDATE_SCREENSHOTS")
    if not destination:
        return
    value, facade = dialog
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    for name, width, error in (("basket-wide", 680, False),
                               ("basket-narrow", 420, False),
                               ("basket-recovery", 420, True)):
        facade.read_error = error
        value.refresh()
        value.resize(width, 740)
        app.processEvents()
        assert value.grab().save(str(output / (name + ".png")))
