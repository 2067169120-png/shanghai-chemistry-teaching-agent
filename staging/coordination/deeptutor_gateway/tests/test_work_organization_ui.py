"""A07 native click paths; fake prompts only, with actual facade and saved records."""
from copy import deepcopy
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QDialog
from test_desktop_studio_ui import window, settle, ROOT
from test_work_organization import save_draft, row, change, terminal_task
from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow


def select(win, app, identity):
    page = win.my_work_page
    settle(app, lambda: not page._loading and not page._mutating)
    index = next(i for i, r in enumerate(page.records) if r["id"] == identity)
    page.results.setCurrentRow(index)
    return page


def scope(page, app, index):
    page.shelves.setCurrentIndex(index)
    settle(app, lambda: not page._loading and not page._mutating)


def test_rename_by_button_preserves_working_form_and_saved_content(window, monkeypatch):
    win, app = window
    identity = save_draft(win.facade)
    before = deepcopy(win.facade.state_store.snapshot()["drafts"])
    win.preparation_page.materials.setPlainText("当前尚未保存的材料")
    payload = win.preparation_page._payload()
    win.navigate("mywork")
    page = select(win, app, identity)
    monkeypatch.setattr(page, "_ask_title", lambda record: "周四高二备课")
    page.rename_button.click()
    settle(app, lambda: not page._mutating and not page._loading and page.records[0]["title"] == "周四高二备课")
    assert "原课题" in page.results.item(0).text()
    assert win.facade.state_store.snapshot()["drafts"] == before
    assert win.preparation_page._payload() == payload


def test_archive_trash_restore_to_archive_and_unarchive_clicks(window, monkeypatch):
    win, app = window
    identity = save_draft(win.facade)
    win.navigate("mywork")
    page = select(win, app, identity)
    page.archive_button.click()
    settle(app, lambda: not page._mutating and not page._loading)
    assert not page.records and page.empty_label.isVisible()
    scope(page, app, 1)
    select(win, app, identity)
    assert page.open_button.isEnabled() and page.archive_button.text() == "移回当前"
    monkeypatch.setattr(page, "_confirm_trash", lambda _: True)
    page.trash_button.click()
    settle(app, lambda: not page._mutating and not page._loading)
    scope(page, app, 2)
    select(win, app, identity)
    assert not page.open_button.isEnabled()
    assert page.restore_button.text() == "还原到已归档"
    assert not page.rename_button.isVisible()
    page.restore_button.click()
    settle(app, lambda: not page._mutating and not page._loading)
    assert not page.records
    scope(page, app, 1)
    select(win, app, identity)
    page.archive_button.click()
    settle(app, lambda: not page._mutating and not page._loading)
    scope(page, app, 0)
    assert page.records[0]["id"] == identity


def test_cancelled_name_or_trash_prompt_never_writes(window, monkeypatch):
    win, app = window
    identity = save_draft(win.facade)
    win.navigate("mywork")
    page = select(win, app, identity)
    before = win.facade.state_store.path.read_bytes()
    monkeypatch.setattr(page, "_ask_title", lambda _: None)
    page.rename_button.click()
    monkeypatch.setattr(page, "_confirm_trash", lambda _: False)
    page.trash_button.click()
    assert win.facade.state_store.path.read_bytes() == before and not page._mutating


def test_invalid_name_retains_selection_and_shows_repair(window, monkeypatch):
    win, app = window
    identity = save_draft(win.facade)
    win.navigate("mywork")
    page = select(win, app, identity)
    monkeypatch.setattr(page, "_ask_title", lambda _: " ")
    page.rename_button.click()
    assert "1—160" in page.status.text() and not page._mutating
    assert page.results.currentItem() is not None


def test_pending_task_cannot_be_hidden_or_renamed(window):
    win, app = window
    identity = terminal_task(win.facade, "prepared")
    win.navigate("mywork")
    page = select(win, app, identity)
    assert page.open_button.isEnabled()
    assert all(not button.isEnabled() for button in page.management_buttons)
    assert "尚未结束" in page.selection_hint.text()


def test_stale_selection_refuses_archive_without_replaying_on_other_item(window):
    win, app = window
    identity = save_draft(win.facade)
    save_draft(win.facade, "other")
    win.navigate("mywork")
    page = select(win, app, identity)
    change(win.facade, row(win.facade, identity), "rename", title="外部新名称")
    before = win.facade.state_store.path.read_bytes()
    page.archive_button.click()
    settle(app, lambda: not page._mutating)
    assert "刷新" in page.status.text() and not page.open_button.isEnabled()
    assert win.facade.state_store.path.read_bytes() == before


def test_stale_open_does_not_switch_page_or_replace_unsaved_material(window):
    win, app = window
    identity = save_draft(win.facade)
    win.preparation_page.materials.setPlainText("不应被旧结果覆盖的当前材料")
    win.navigate("mywork")
    select(win, app, identity)
    selected = row(win.facade, identity)
    change(win.facade, selected, "trash")
    win.open_work_record(selected)
    assert win.stack.currentWidget() is win.my_work_page
    assert win.preparation_page.materials.toPlainText() == "不应被旧结果覆盖的当前材料"


def test_reopening_application_keeps_trash_and_does_not_delete_editor_recovery(window):
    win, app = window
    identity = save_draft(win.facade)
    win.preparation_page.materials.setPlainText("当前材料应独立恢复")
    change(win.facade, row(win.facade, identity), "trash")
    paths = win.facade.paths
    win.close()
    second = TeacherWorkbenchWindow(build_default_facade(paths))
    second.show()
    try:
        assert second.preparation_page.materials.toPlainText() == "当前材料应独立恢复"
        second.navigate("mywork")
        scope(second.my_work_page, app, 2)
        assert second.my_work_page.records[0]["id"] == identity
    finally:
        second.close(); second.deleteLater(); app.processEvents()


def test_narrow_layout_keeps_all_management_actions_inside_page(window):
    win, app = window
    identity = save_draft(win.facade)
    win.navigate("mywork")
    page = select(win, app, identity)
    win.resize(800, 700)
    settle(app)
    assert page.shelves.width() <= page.width()
    for button in (page.rename_button, page.archive_button, page.trash_button):
        assert button.isVisible() and button.geometry().right() <= button.parentWidget().width()


def test_acceptance_probe_handles_an_existing_topic_without_overwriting_old_drafts(window):
    from integrations.deeptutor_shchem_v1.desktop_work_organization_probe import exercise
    win, app = window
    win.preparation_page.apply_studio_template("concept", "已有课题", "软件验收")
    win.preparation_page.materials.setPlainText("已有合成资料，不应被作品整理修改。")
    win.facade.create_preparation_draft(win.preparation_page._payload())
    before = deepcopy(win.facade.state_store.snapshot()["drafts"])
    screenshots = []
    result = exercise(win, lambda condition=lambda: True: settle(app, condition),
                      lambda widget, name: screenshots.append(name))
    after = win.facade.state_store.snapshot()["drafts"]
    assert all(after[key] == record for key, record in before.items())
    assert result["draft_lifecycle"] and result["task_lifecycle"]
    assert result["model_calls"] == 0 and len(screenshots) == 5
