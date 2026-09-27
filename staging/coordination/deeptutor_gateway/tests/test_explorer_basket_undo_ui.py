"""Real Qt controls with isolated durable baskets, including 360x520 at 2x."""
import json
import os
from types import SimpleNamespace

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog
from test_desktop_basket_undo import mixed_rows


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.fixture
def app():
    instance = create_application(["basket-undo-ui-test"])
    yield instance
    settle(instance)


@pytest.fixture
def dialog(app, tmp_path):
    store = DesktopStateStore(tmp_path / "synthetic-state")
    rows = mixed_rows()
    for index, row in enumerate(rows, 1):
        row["title_zh"] = f"合成验收材料 {index} · 完整主题及共有材料，不是真实试题"
        row.setdefault("source_zh", "自编界面验收来源，题目身份与原顺序必须保留")
    store.add_many_to_basket(rows)
    facade = SimpleNamespace(_state=store)
    facade.open_basket_session = lambda: DesktopWorkbenchFacade.open_basket_session(facade)
    value = ExplorerBasketDialog(facade)
    value.resize(360, 520)
    value.show()
    value.activateWindow()
    settle(app)
    yield value, store
    value.close()
    value.deleteLater()
    settle(app)


def rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


def assert_footer(dialog):
    for widget in (dialog.status, dialog.back):
        rect = rect_in(widget, dialog)
        assert dialog.rect().contains(rect)
        assert widget.isVisible()
    label = dialog.status
    assert label.textFormat() == Qt.TextFormat.PlainText
    needed = label.fontMetrics().boundingRect(
        QRect(0, 0, label.contentsRect().width(), 10000),
        Qt.TextFlag.TextWordWrap, label.text(),
    ).height()
    assert label.contentsRect().height() >= needed
    assert rect_in(dialog.status, dialog).bottom() < rect_in(dialog.back, dialog).top()


@pytest.mark.parametrize("width", [360, 420, 900])
@pytest.mark.parametrize("state", ["ready", "removed", "empty", "conflict", "read_failure"])
def test_short_window_footer_complete_and_every_legal_action_scrolls_into_view(dialog, app, width, state):
    value, store = dialog
    if state in {"removed", "conflict"}:
        value.remove.click()
    elif state == "empty":
        while value.list.count():
            value.remove.click()
    if state == "conflict":
        DesktopStateStore(store.root).add_to_basket({"key": "another-window", "title_zh": "另一窗口加入"})
        value.undo()
    elif state == "read_failure":
        store.path.write_bytes(b"{unreadable")
        value.refresh()
    value.resize(width, 520)
    settle(app)
    assert value.size().width() == width and value.size().height() == 520
    assert_footer(value)
    assert value.scroll.horizontalScrollBar().maximum() == 0
    for button in (value.up, value.down, value.remove, value.undo_button,
                   value.retry, value.edit_button, value.preview):
        if button.isVisible() and button.isEnabled():
            value.scroll.ensureWidgetVisible(button, 0, 0)
            settle(app)
            assert value.scroll.viewport().rect().contains(rect_in(button, value.scroll.viewport()))
            assert_footer(value)
    image = value.grab()
    assert image.width() == round(width * value.devicePixelRatioF())
    assert image.height() == round(520 * value.devicePixelRatioF())


def test_remove_then_undo_reselects_exact_item_and_mixed_metadata(dialog, app):
    value, store = dialog
    before = store.basket()
    value.list.setCurrentRow(1)
    value.remove.click()
    assert value.list.currentItem().data(Qt.ItemDataRole.UserRole) == before[2]["key"]
    assert value.undo_button.isEnabled() and "移出" in value.history_status.text()
    value.undo_button.click()
    assert store.basket() == before
    assert value.list.currentRow() == 1
    assert value.list.currentItem().data(Qt.ItemDataRole.UserRole) == before[1]["key"]
    assert "已撤销" in value.status.text() and not value.undo_button.isEnabled()


def test_move_keyboard_undo_and_tab_can_reach_hidden_controls(dialog, app):
    value, store = dialog
    before = store.basket()
    value.down.click()
    value.list.setFocus()
    settle(app)
    QTest.keyClick(value.list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settle(app)
    assert store.basket() == before
    assert value.list.currentRow() == 0
    value.down.click()
    value.list.setFocus()
    reached = set()
    for _ in range(20):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        settle(app)
        focused = app.focusWidget()
        for name in ("undo_button", "edit_button", "preview", "back"):
            if focused is getattr(value, name):
                reached.add(name)
                if name != "back":
                    assert value.scroll.viewport().rect().contains(rect_in(focused, value.scroll.viewport()))
    assert reached == {"undo_button", "edit_button", "preview", "back"}


def test_empty_after_remove_can_undo_without_using_output_or_all_library_fallback(dialog):
    value, store = dialog
    before = store.basket()
    while value.list.count():
        value.remove.click()
    assert value.undo_button.isEnabled()
    assert not value.preview.isEnabled() and not value.edit_button.isEnabled()
    for _ in range(3):
        value.undo_button.click()
    assert store.basket() == before and value.list.currentRow() == 0


def test_conflict_never_emits_success_or_overwrites_and_retry_starts_fresh(dialog):
    value, store = dialog
    events = []
    value.basket_changed.connect(events.append)
    value.remove.click()
    external = DesktopStateStore(store.root)
    external.add_to_basket({"key": "external", "title_zh": "新加入"})
    frozen = store.path.read_bytes()
    value.undo_button.click()
    assert store.path.read_bytes() == frozen and events == [2]
    assert "其他操作" in value.status.text() and "失效" in value.status.text()
    assert not value.undo_button.isEnabled() and not value.remove.isEnabled()
    value.retry.click()
    assert value.remove.isEnabled() and not value.undo_button.isEnabled()
    assert value.list.count() == 3


def test_read_error_clears_history_and_raw_error_never_becomes_label_markup(dialog, monkeypatch):
    value, store = dialog
    value.remove.click()
    original = store.basket_snapshot
    def failure():
        raise OSError('<img src="private-path">private-token')
    monkeypatch.setattr(store, "basket_snapshot", failure)
    value.refresh()
    assert not value.undo_button.isEnabled() and value.retry.isVisible()
    assert "private-token" not in value.status.text() and "private-path" not in value.status.text()
    monkeypatch.setattr(store, "basket_snapshot", original)
    value.retry.click()
    assert not value.undo_button.isEnabled()


def test_busy_reentry_cannot_double_submit_close_or_emit_two_successes(dialog, monkeypatch):
    value, store = dialog
    original = value._session.change
    calls, events = [], []
    def reentrant(key, delta):
        calls.append((key, delta))
        assert not value.remove.isEnabled() and not value.back.isEnabled()
        value.change(0)
        value.undo()
        value.accept()
        assert value.isVisible()
        return original(key, delta)
    monkeypatch.setattr(value._session, "change", reentrant)
    value.basket_changed.connect(events.append)
    value.remove.click()
    assert len(calls) == 1 and events == [2] and len(store.basket()) == 2


def test_close_discards_history_and_preview_edit_recheck_without_new_basket_write(dialog, app):
    value, store = dialog
    value.down.click()
    original_session = value._session
    value.back.click()
    assert original_session.undo_count == 0
    second = ExplorerBasketDialog(value.facade)
    second.show()
    try:
        assert not second.undo_button.isEnabled()
        events = []
        second.preview_requested.connect(lambda: events.append("preview"))
        frozen = store.path.read_bytes()
        second.preview.click()
        assert events == ["preview"] and store.path.read_bytes() == frozen
    finally:
        second.close()
        second.deleteLater()
        settle(app)


def test_postcommit_read_failure_keeps_saved_receipt_but_does_not_claim_undo_ready(dialog, monkeypatch):
    value, store = dialog
    events = []
    value.basket_changed.connect(events.append)
    original = value._session.change
    def change_then_unreadable(key, delta):
        result = original(key, delta)
        def failed():
            raise OSError("synthetic read failure")
        monkeypatch.setattr(store, "basket_snapshot", failed)
        return result
    monkeypatch.setattr(value._session, "change", change_then_unreadable)
    value.remove.click()
    assert events == [2]
    assert "暂不能读取" in value.status.text() and not value.undo_button.isEnabled()
    assert value.list.count() == 3  # Last readable view is explicitly marked stale.


def test_saved_undo_does_not_overclaim_when_another_edit_arrives_before_refresh(dialog, monkeypatch):
    value, store = dialog
    value.remove.click()
    events = []
    value.basket_changed.connect(events.append)
    original = value._session.undo
    def undo_then_external():
        result = original()
        DesktopStateStore(store.root).clear_basket()
        return result
    monkeypatch.setattr(value._session, "undo", undo_then_external)
    value.undo()
    assert value.list.count() == 0 and "已更新" in value.status.text()
    assert "已恢复" not in value.status.text()
    assert events == [0]
