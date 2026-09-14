"""Real Qt editor recovery and all-history pagination using isolated local stores."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from test_desktop_studio_ui import window, settle, ROOT
from test_phase_a_core import saved_state
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_facade import build_default_facade
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import PreparationDraftDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.my_work_page import MyWorkPage


def test_editor_close_reopen_restores_partial_fields_and_images(window, tmp_path):
    from PIL import Image
    win, app = window
    page = win.preparation_page
    image_path = tmp_path / "synthetic.png"
    Image.new("RGB", (64, 40), "white").save(image_path)
    asset = win.facade.import_preparation_image(str(image_path), "合成题图", "测试生成", "公共材料")
    page.topic.setText("")
    page.audience.setText("高二 · 未完成备课")
    page.route.setCurrentText("实验")
    page.lesson_count.setValue(2)
    page.lesson_minutes.setValue(45)
    page.objective.setPlainText("")
    page.materials.setPlainText("  未填完也应恢复\n保留公共材料和SO₄²⁻。  ")
    page.template_detail.setText("教师尚未完成的活动结构")
    page.learning_detail.setText("仍在编辑学情")
    page.strategy_detail.setText("课后任务尚未完成")
    page.image_assets_widget.set_assets([asset])
    page.image_assets_widget.set_image_input_mode("vision")
    page.output_kind.setCurrentIndex(page.output_kind.findData("lesson_plan"))
    expected = deepcopy(page._payload())
    before = win.facade.state_store.snapshot()["drafts"]
    assert win.close()
    second = TeacherWorkbenchWindow(build_default_facade(DesktopPaths.from_workspace(ROOT, state_root=win.facade.paths.state_root)))
    try:
        assert second.preparation_page._payload() == expected
        assert "已恢复上次编辑" in second.preparation_page.recovery.status.text()
        assert second.facade.state_store.snapshot()["drafts"] == before
        assert second.preparation_page.isWindowModified()
    finally:
        second.close()
        second.deleteLater()
        app.processEvents()


def test_autosave_observes_template_and_timing_without_explicit_save(window):
    win, app = window
    page = win.preparation_page
    page.apply_studio_template("concept", "未正式保存的合成课题", "高二")
    page.lesson_minutes.setValue(42)
    page.recovery.tick()
    settle(app, lambda: not page.recovery._inflight)
    record = page.recovery.store.load()
    assert record["payload"] == page._payload()
    assert record["dirty"]
    assert not win.facade.state_store.snapshot()["drafts"]


def test_close_flush_fences_old_worker_even_after_reverting_to_saved_text(window):
    win, _ = window
    recovery = win.preparation_page.recovery
    recovery._inflight = True
    recovery._sequence = 8
    assert recovery.flush()
    assert recovery._sequence == 9
    recovery.store.save({**recovery.default, "topic": "不应最后写入的旧队列"}, dirty=True, sequence=8)
    assert recovery.store.load()["payload"] == recovery.default
    recovery._inflight = False


def test_disk_failure_can_cancel_window_close_without_losing_edit(window, monkeypatch):
    win, _ = window
    page = win.preparation_page
    page.materials.setPlainText("磁盘失败时不能悄悄退出")
    with monkeypatch.context() as patch:
        patch.setattr(page.recovery.store, "save", lambda *args, **kw: (_ for _ in ()).throw(OSError("synthetic failure")))
        patch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel)
        assert not win.close()
        assert win.isVisible()
        assert page.materials.toPlainText() == "磁盘失败时不能悄悄退出"
    assert page.recovery.flush()


def test_new_blank_discards_only_the_current_form_after_confirmation(window, monkeypatch):
    win, _ = window
    page = win.preparation_page
    saved_state(win.facade.paths.state_root, 1)
    page.materials.setPlainText("新建前的未保存修改")
    assert page.recovery.flush()
    before = win.facade.state_store.snapshot()["drafts"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    page.recovery.new_blank()
    assert page.materials.toPlainText() == "新建前的未保存修改"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page.recovery.new_blank()
    assert page._payload() == page.recovery.default
    assert win.facade.state_store.snapshot()["drafts"] == before
    assert page.recovery.store.load()["payload"] == page.recovery.default


def test_all_history_ui_finds_and_opens_oldest_of_501(window, monkeypatch):
    win, app = window
    saved_state(win.facade.paths.state_root)
    win.navigate("mywork")
    page = win.my_work_page
    settle(app, lambda: not page._loading)
    assert page._total == 501 and page.results.count() == 25 and page.next.isEnabled()
    page.query.setText("课题-0000")
    settle(app, lambda: not page._loading)
    assert page._total == 1 and page.results.count() == 1
    selected = page.results.item(0).data(Qt.ItemDataRole.UserRole)
    assert selected["id"] == "prep-0000"
    before = win.facade.state_store.snapshot()["drafts"]
    def accept(dialog):
        assert dialog.selected["draft_id"] == "prep-0000"
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PreparationDraftDialog, "exec", accept)
    win.open_work_record(selected)
    assert win.preparation_page.topic.text() == "合成课题-0000"
    assert win.facade.state_store.snapshot()["drafts"] == before


def test_draft_picker_searches_all_and_rechecks_revision(window):
    win, app = window
    saved_state(win.facade.paths.state_root, 61)
    dialog = PreparationDraftDialog(win.facade, win.preparation_page)
    dialog.show()
    assert dialog.source.count() == 50 and dialog.next.isEnabled()
    dialog.query.setText("课题-0000")
    settle(app, lambda: dialog.selected is not None and dialog.selected["draft_id"] == "prep-0000")
    assert dialog.source.count() == 1
    assert dialog.select_draft_id("prep-0000")
    state = win.facade.state_store
    state._update(lambda value: value["drafts"]["prep-0000"]["core_fields"].update(topic="已在别处修改"))
    dialog.load_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.selected is None
    dialog.close()
    dialog.deleteLater()


def test_stale_search_reply_never_replaces_new_query(window):
    _, _app = window
    calls = []
    class Tasks:
        def submit(self, label, operation, **callbacks):
            calls.append((operation, callbacks))
            return str(len(calls))
        def cancel(self, *args):
            pass
    facade = SimpleNamespace(search_preparation_work=lambda **kw: dict(
        items=[dict(kind="draft", id=kw["query"], title=kw["query"], date="2026-01-01", status="离线草稿", value={})],
        total=1, offset=0, limit=25, has_more=False, warnings=[]))
    page = MyWorkPage(facade, Tasks())
    page.query.setText("旧请求")
    page.refresh()
    page.query.setText("新请求")
    page.refresh()
    calls[1][1]["on_success"](calls[1][0]())
    calls[0][1]["on_success"](calls[0][0]())
    assert page.records[0]["id"] == "新请求"
    page.close()
    page.deleteLater()
