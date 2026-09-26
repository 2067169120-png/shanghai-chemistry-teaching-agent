"""Teacher-facing regression: real records, no marketing hero and visible actions."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from integrations.deeptutor_shchem_v1.desktop_teacher_desk import desk_snapshot


def test_desk_reads_work_and_basket_only():
    calls = []
    facade = SimpleNamespace(search_preparation_work=lambda **kw: (calls.append(kw) or
        {"items": [], "total": 0, "warnings": []}), basket=lambda: ())
    result = desk_snapshot(facade)
    assert calls == [{"shelf": "current", "order": "newest", "limit": 5}]
    assert result["basket"] == [] and result["total"] == 0


def test_unavailable_is_not_empty_basket():
    def fail():
        raise OSError("private path")
    value = desk_snapshot(SimpleNamespace(search_preparation_work=lambda **k: fail(), basket=fail))
    assert value["basket"] is None and value["total"] is None
    assert len(value["warnings"]) == 2 and "private path" not in str(value)


pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QMessageBox, QScrollArea
from test_desktop_studio_ui import window, settle


def add_draft(win, topic):
    prep = win.preparation_page
    prep.topic.setText(topic)
    prep.audience.setText("软件验收")
    prep.objective.setPlainText("解释原题条件并保留作答依据；仅软件验收。")
    prep.materials.setPlainText("合成材料，非真实课堂；保留原题公共条件。")
    return win.facade.create_preparation_draft(prep._payload()).draft_id


def test_home_has_no_hero_and_registry_is_lazy(window, monkeypatch):
    win, app = window
    called = []
    original = win.facade.load_desktop_registry
    monkeypatch.setattr(win.facade, "load_desktop_registry", lambda **kw: (called.append(kw) or original(**kw)))
    win.navigate("home"); settle(app, lambda: not win.home_page._loading)
    assert called == []
    assert win.home_page.findChild(QLabel, "HeroTitle") is None
    assert not win.home_page.diagnostics.toggle.isChecked()
    win.home_page.diagnostics.toggle.click()
    settle(app, lambda: not win.home_page._registry_loading)
    assert len(called) == 1
    win.home_page.diagnostics.toggle.click(); win.home_page.diagnostics.toggle.click()
    settle(app)
    assert len(called) == 1


def test_recent_only_current_and_max_five_without_state_changes(window):
    win, app = window
    for i in range(7):
        add_draft(win, f"合成课题{i}")
    row = win.facade.search_preparation_work(limit=100)["items"][0]
    win.facade.organize_preparation_work(row["kind"], row["id"], "archive",
        expected_source=row["source_revision"], expected_organization=row["organization_revision"])
    before = deepcopy(win.facade.state_store.snapshot())
    win.navigate("home"); settle(app, lambda: not win.home_page._loading)
    assert len(win.home_page.records) == 5
    assert all(r["shelf"] == "current" and r["id"] != row["id"] for r in win.home_page.records)
    assert win.facade.state_store.snapshot() == before


def test_recent_opens_selected_identity(window, monkeypatch):
    win, app = window
    wanted = add_draft(win, "真实保存的合成备课")
    win.navigate("home"); settle(app, lambda: not win.home_page._loading)
    chosen = []
    monkeypatch.setattr(win.preparation_page, "_open_draft", lambda value: chosen.append(value))
    win.home_page.table.selectRow(0)
    win.home_page.open_button.click()
    assert chosen == [wanted]
    assert win.stack.currentWidget() is win.preparation_page


def test_continue_preserves_unsaved_form_and_no_model(window):
    win, app = window
    win.preparation_page.topic.setText("尚未保存的课题")
    win.preparation_page.materials.setPlainText("原材料保留")
    before = deepcopy(win.preparation_page._payload())
    win.navigate("home"); settle(app, lambda: not win.home_page._loading)
    assert win.home_page.editing_strip.isVisible()
    assert "尚未保存的课题" in win.home_page.editing_label.text()
    win.home_page.resume_button.click()
    assert win.preparation_page._payload() == before
    assert not win.preparation_page.studio_busy()


def test_new_cancel_keeps_unsaved_and_confirm_clears_only_editor(window, monkeypatch):
    win, app = window
    add_draft(win, "正式保存的课题")
    before = deepcopy(win.preparation_page._payload())
    state = deepcopy(win.facade.state_store.snapshot())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    win.home_page.new_button.click()
    assert win.preparation_page._payload() == before
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.home_page.new_button.click()
    assert win.preparation_page._payload() == win.preparation_page.recovery.default
    assert win.facade.state_store.snapshot() == state


def test_stale_home_reply_does_not_replace_current(window):
    win, app = window
    home = win.home_page
    home.refresh(); settle(app, lambda: not home._loading)
    before = home.basket_count.text()
    home._loaded({"works": [], "total": 500, "basket": [{"title_zh": "STALE"}], "warnings": []}, home._epoch - 1)
    assert home.basket_count.text() == before
    assert "STALE" not in home.basket_excerpt.text()


def test_home_real_preview_entry(window, monkeypatch):
    win, app = window
    calls = []
    monkeypatch.setattr(win.paper_page, "request_layout_preview", lambda: calls.append(True))
    home = win.home_page
    home._loaded({"works": [], "total": 0, "basket": [{"title_zh": "合成题目"}], "warnings": []}, home._epoch)
    home.preview_button.click()
    assert calls == [True] and win.stack.currentWidget() is win.paper_page


def test_empty_basket_cannot_request_preview(window):
    win, app = window
    home = win.home_page
    home.refresh(); settle(app, lambda: not home._loading)
    assert "已选 0 项" == home.basket_count.text()
    assert not home.preview_button.isEnabled()


def test_work_actions_visible_in_800_window_after_list_scroll(window):
    win, app = window
    for i in range(28):
        add_draft(win, "布局验收" + str(i))
    win.resize(800, 700); win.navigate("mywork")
    page = win.my_work_page
    settle(app, lambda: not page._loading)
    page.results.scrollToBottom()
    page.results.setCurrentRow(24)
    settle(app)
    assert win.width() == 800 and win.height() == 700
    assert page.findChild(QScrollArea, "PageScroll") is None
    for control in (page.open_button, page.rename_button, page.archive_button, page.backup_button, page.next):
        assert control.isVisible()
        top = control.mapTo(page, QPoint(0, 0))
        assert 0 <= top.y() and top.y() + control.height() <= page.height()
        assert control.width() >= control.fontMetrics().horizontalAdvance(control.text())
