"""Teacher desk interaction and narrow-layout checks with synthetic local data."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_workbench.home_page import HomePage
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
from test_desktop_studio_ui import settle, window


def snapshot(*, populated=False, basket=None):
    return {
        "works": [
            {"kind": "draft", "id": f"synthetic-{i}", "title": title,
             "status": "草稿", "date": "2026-09-26T09:15:00"}
            for i, title in enumerate((
                "化学平衡：课堂观察与推理（合成界面验收）",
                "实验复习：变量、证据与结论（合成界面验收）"))
        ] if populated else [],
        "total": 2 if populated else 0,
        "basket": basket if basket is not None else [], "warnings": [],
    }


class ManualTasks:
    def __init__(self):
        self.requests = []
        self.cancelled = []

    def submit(self, title, function, *, on_success, on_failure):
        self.requests.append((on_success, on_failure))
        return str(len(self.requests))

    def cancel(self, task_id):
        self.cancelled.append(task_id)


@pytest.fixture
def desk():
    app = QApplication.instance() or QApplication([])
    install_ui_font(app)
    tasks = ManualTasks()
    page = HomePage(SimpleNamespace(), tasks)
    page.setStyleSheet(WORKBENCH_STYLE)
    page.resize(1100, 700)
    page.show()
    settle(app, lambda: page._loading)
    tasks.requests[-1][0](snapshot())
    app.processEvents()
    yield page, app, tasks
    page.close()
    page.deleteLater()
    app.processEvents()


def test_empty_desk_has_action_without_a_blank_table(desk):
    page, app, _ = desk
    created, routes = [], []
    page.new_requested.connect(lambda: created.append(True))
    page.navigate_requested.connect(routes.append)
    assert page.empty_state.isVisible() and not page.table.isVisible()
    assert page.empty_title.text() == "还没有当前作品"
    assert not page.open_button.isVisible()
    page.empty_start_button.click()
    page.all_button.click()
    page.select_button.click()
    assert created == [True] and routes == ["mywork", "library"]
    assert page.basket_count.text() == "已选 0 项"
    assert not page.preview_button.isEnabled()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_selected_work_keeps_full_details_and_opens_once_by_keyboard(desk, key):
    page, app, _ = desk
    value = snapshot(populated=True)
    value["works"][0]["title"] += "：继续核对全部教学目标" * 8
    page._loaded(value, page._epoch)
    page.resize(420, 700)
    app.processEvents()
    page.table.selectRow(0)
    page.table.setFocus()
    opened = []
    page.open_requested.connect(opened.append)
    assert value["works"][0]["title"] in page.selection_detail.text()
    assert "2026-09-26 09:15" in page.selection_detail.text()
    assert value["works"][0]["date"] in page.selection_detail.toolTip()
    assert page.table.isColumnHidden(2)
    QTest.keyClick(page.table, key)
    app.processEvents()
    assert opened == [value["works"][0]]


def test_refresh_keeps_identity_after_reorder_and_ignores_stale_reply(desk):
    page, app, tasks = desk
    value = snapshot(populated=True, basket=[{"title_zh": "合成题目"}])
    page._loaded(value, page._epoch)
    page.table.selectRow(1)
    selected = page.records[1]
    page.refresh()
    stale = tasks.requests[-1]
    page.refresh()
    assert tasks.cancelled and not page.preview_button.isEnabled()
    assert not page.basket_button.isEnabled()
    assert page.refresh_button.text() == "读取中…"
    current = {**value, "works": list(reversed(value["works"]))}
    tasks.requests[-1][0](current)
    assert page.records[page.table.currentRow()]["id"] == selected["id"]
    assert selected["title"] in page.selection_detail.text()
    stale[1]("过期错误")
    stale[0](snapshot())
    assert page.records == current["works"] and page.preview_button.isEnabled()
    assert page.refresh_button.text() == "刷新"


def test_basket_failure_does_not_claim_recent_works_are_unreadable(desk):
    page, app, _ = desk
    value = snapshot()
    value.update(basket=None, warnings=["选题篮未能读取；已选记录未被清空。"])
    page._loaded(value, page._epoch)
    assert page.empty_title.text() == "还没有当前作品"
    assert page.empty_start_button.isVisible()
    assert page.basket_count.text() == "题篮暂不可读"
    assert "未被清空" in page.basket_excerpt.text()
    assert not page.basket_button.isEnabled() and not page.preview_button.isEnabled()
    page.refresh()
    page._failed("读取失败，请稍后重试。", page._epoch)
    assert page.empty_title.text() == "最近作品暂不可读"
    assert not page.empty_start_button.isVisible()
    assert page.status.isVisible() and page.refresh_button.isEnabled()


def test_basket_preview_preserves_order_and_explains_omitted_items(desk):
    page, app, _ = desk
    titles = ["合成题目 " + str(i) + "：核对完整题面" * 16 for i in range(5)]
    page._loaded(snapshot(basket=[{"title_zh": title} for title in titles]), page._epoch)
    assert page.basket_count.text() == "已选 5 项"
    assert page.basket_remainder.isVisible() and "另有 3 项" in page.basket_remainder.text()
    assert page.basket_excerpt.text().startswith("01  " + titles[0][:90])
    assert "…" in page.basket_excerpt.text()
    assert titles[0] in page.basket_excerpt.toolTip()
    requested = []
    page.preview_requested.connect(lambda: requested.append(True))
    page.preview_button.click()
    assert requested == [True]


def test_failed_refresh_keeps_previous_work_visible_without_opening_disabled_action(desk):
    page, app, tasks = desk
    value = snapshot(populated=True)
    page._loaded(value, page._epoch)
    page.table.selectRow(0)
    page.refresh()
    tasks.requests[-1][1]("读取失败，请刷新重试。")
    opened = []
    page.open_requested.connect(opened.append)
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    assert page.records == value["works"] and page.table.isVisible()
    assert "上次读取内容" in page.recent.text()
    assert not opened and not page.open_button.isEnabled()


def test_five_recent_records_fit_without_an_inner_vertical_scrollbar(desk):
    page, app, _ = desk
    value = snapshot(populated=True)
    value["works"] = [{**value["works"][0], "id": f"synthetic-{i}"} for i in range(5)]
    value["total"] = 5
    page._loaded(value, page._epoch)
    app.processEvents()
    assert page.table.rowCount() == 5
    assert page.table.verticalScrollBar().maximum() == 0
    last = page.table.visualItemRect(page.table.item(4, 0))
    assert last.bottom() <= page.table.viewport().height()


def test_directory_is_loaded_only_on_first_expansion(desk):
    page, app, tasks = desk
    assert len(tasks.requests) == 1
    page.diagnostics.toggle.click()
    assert len(tasks.requests) == 2
    tasks.requests[-1][0](SimpleNamespace(products=[], curriculum=SimpleNamespace(
        loaded=False, message_zh="合成目录状态")))
    page.diagnostics.toggle.click()
    page.diagnostics.toggle.click()
    assert len(tasks.requests) == 2 and page._registry_loaded


@pytest.mark.parametrize("width,height", [(1366, 768), (900, 700), (420, 700), (360, 560)])
def test_home_controls_stay_inside_supported_window_width(window, monkeypatch, width, height):
    win, app = window
    page = win.home_page
    win.resize(width, height)
    value = snapshot(populated=True, basket=[{"title_zh": "合成长题目：" + "核对完整材料" * 12}])
    value["works"][0]["title"] += "：核对完整教学目标" * 10
    page._loaded(value, page._epoch)
    page.table.selectRow(0)
    page.update_editor({"topic": "合成当前编辑：" + "保留现有课题与材料" * 10})
    monkeypatch.setattr(win.facade, "load_desktop_registry", lambda **kw:
        SimpleNamespace(products=[], curriculum=SimpleNamespace(loaded=False, message_zh="合成目录状态")))
    page.diagnostics.toggle.click()
    settle(app, lambda: not page._registry_loading)
    assert win.width() == width and win.height() == height
    scroll = page.findChild(QScrollArea, "PageScroll")
    content = scroll.widget()
    assert content.width() <= scroll.viewport().width()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert page.table.horizontalScrollBar().maximum() == 0
    for control in (page.refresh_button, page.new_button, page.resume_button,
                    page.all_button, page.open_button, page.basket_button,
                    page.preview_button, page.select_button, page.progress_button,
                    page.registry_refresh, page.selection_detail):
        left = control.mapTo(content, QPoint(0, 0)).x()
        assert 0 <= left and left + control.width() <= content.width(), control.objectName()
        if hasattr(control, "clicked"):
            assert control.width() >= control.fontMetrics().horizontalAdvance(control.text())
    assert page.selection_detail.height() >= page.selection_detail.heightForWidth(page.selection_detail.width())
