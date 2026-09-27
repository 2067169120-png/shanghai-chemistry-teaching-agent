"""Synthetic repair choices and asynchronous import routing; no personal state."""
from copy import deepcopy

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog

from test_visual_import_egress_dialog import _progress_plan, _dialog_for, _flush, _close
from test_desktop_visual_import_ui import (
    _Facade, _visual_profile, _visual_dialog, _manual_task_bridge, _settle,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog import VisualImportEgressDialog


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def qt_app(app):
    return app


@pytest.fixture(autouse=True)
def dispose_synthetic_windows(app):
    before = set(app.topLevelWidgets())
    yield
    for widget in set(app.topLevelWidgets()) - before:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def repair_plan(*, selected=False, unavailable=False):
    plan, pixels = _progress_plan()
    options = [
        {"option_id": "option-1", "shard_id": "shard-1", "shard_index": 1, "version": 1,
         "summary": "合成记录甲：<b>原文字串不解释为富文本</b>\n旧记录中的题目片段。"},
        {"option_id": "option-2", "shard_id": "shard-1", "shard_index": 1, "version": 2,
         "summary": "合成记录乙：另一种识别内容，需要教师与原页比较。"},
    ]
    if not unavailable:
        options.append({"option_id": "option-3", "shard_id": "shard-2", "shard_index": 2, "version": 1,
                        "summary": "合成记录丙：已有末页识别记录，仍待教师核对。"})
    plan.update(can_confirm=selected, repair={
        "issues": ["当前使用的页组版本记录损坏，不能自动选择旧版本。"],
        "options": options, "selected_option_ids": ["option-2"] if selected else [],
        "selected_shards": int(selected), "unavailable_shards": int(unavailable),
    })
    for page in plan["pages"]:
        page["will_send"] = False
        page["checkpoint_state"] = "repair_selected" if selected and page["page_number"] < 3 else "repair_pending"
    for shard in plan["shards"]:
        shard["unavailable_reason"] = "记录与当前页面不一致；这组不能本机恢复。" if unavailable else ""
    plan["confirmation_text"] = (
        "当前使用的页组版本记录损坏。\n可选记录已独立核对来源、页面、模型及处理条件。\n"
        "同组多个候选需明确选择，不会自动选用。\n"
        + ("本次选择恢复 1 组，其余 1 组保持待处理。\n" if selected else "尚未选择记录。\n")
        + "确认只修复本机目录，保留旧记录与原目录字节。修复后返回普通预览。\n"
        "不会调用模型；尚未恢复的页面须在普通预览另行确认发送。"
    )
    return plan, pixels


def test_choice_is_explicit_exclusive_and_dirty_until_preview(app):
    plan, pixels = repair_plan()
    dialog, _ = _dialog_for(plan, pixels)
    _flush(app, dialog)
    assert not dialog.confirm_button.isEnabled() and dialog.selected_repair_records() == ()
    assert not dialog.revise_button.isEnabled()
    assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "处理进度"
    dialog.repair_list.setCurrentRow(0)
    assert "<b>原文字串不解释为富文本</b>" in dialog.repair_details.toPlainText()
    dialog.repair_list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.repair_list.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_repair_records() == ("option-2",)
    assert dialog.repair_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert not dialog.confirm_button.isEnabled() and dialog.revise_button.isEnabled()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.revise_button.click()
    assert dialog.result() == VisualImportEgressDialog.REVISE_REPAIR and not dialog._timer.isActive()


def test_revised_confirmation_contains_only_selected_group_and_can_be_cancelled(app):
    plan, pixels = repair_plan(selected=True, unavailable=True)
    before = deepcopy(plan)
    dialog, _ = _dialog_for(plan, pixels)
    _flush(app, dialog)
    assert dialog.confirm_button.isEnabled() and dialog.confirm_button.text() == "确认本机修复"
    assert dialog.selected_repair_records() == ("option-2",)
    assert not dialog.revise_button.isEnabled()
    assert "本次选择恢复 1 组" in dialog.disclosure.toPlainText()
    assert not (dialog.repair_list.item(2).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    dialog.repair_list.setCurrentRow(2)
    assert "与当前页面不一致" in dialog.repair_details.toPlainText()
    assert all("本机复用" not in a["caption"] for a in dialog._assets)
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected and not dialog._timer.isActive() and plan == before


@pytest.mark.parametrize("width", [360, 420, 900])
def test_repair_controls_and_wrapped_records_fit_narrow_windows(app, width):
    plan, pixels = repair_plan(selected=True)
    dialog, _ = _dialog_for(plan, pixels)
    dialog.resize(width, 900)
    dialog.show()
    _flush(app, dialog)
    for _ in range(10):
        app.processEvents()
    assert dialog.width() == width
    for widget in (dialog.confirm_button, dialog.cancel_button, dialog.revise_button, dialog.repair_list, dialog.repair_details):
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    assert dialog.repair_list.horizontalScrollBar().maximum() == 0
    assert dialog.repair_details.horizontalScrollBar().maximum() == 0
    _close(dialog)


def test_bad_repair_membership_blocks_confirmation(app):
    plan, pixels = repair_plan(selected=True)
    plan["shards"][1]["page_ids"] = [plan["shards"][0]["page_ids"][0]]
    dialog, _ = _dialog_for(plan, pixels)
    _flush(app, dialog)
    assert not dialog.confirm_button.isEnabled() and dialog._manifest_error
    _close(dialog)


@pytest.mark.parametrize("width", [360, 420])
def test_narrow_select_preview_confirm_and_return_are_real_widget_actions(app, width):
    plan, pixels = repair_plan()
    choose, _ = _dialog_for(plan, pixels)
    choose.resize(width, 900)
    choose.show()
    _flush(app, choose)
    choose.repair_list.item(1).setCheckState(Qt.CheckState.Checked)
    choose.revise_button.click()
    assert choose.result() == VisualImportEgressDialog.REVISE_REPAIR
    plan, pixels = repair_plan(selected=True)
    preview, _ = _dialog_for(plan, pixels)
    preview.resize(width, 900)
    preview.show()
    _flush(app, preview)
    preview.confirm_button.click()
    _flush(app, preview)
    assert preview.result() == QDialog.DialogCode.Accepted
    ordinary, pixels = _progress_plan()
    normal, _ = _dialog_for(ordinary, pixels)
    normal.resize(width, 900)
    normal.show()
    _flush(app, normal)
    assert normal.confirm_button.isEnabled() and "确认" in normal.confirm_button.text()
    normal.cancel_button.click()
    assert normal.result() == QDialog.DialogCode.Rejected and not normal._timer.isActive()


@pytest.mark.parametrize("selected", [False, True])
def test_short_window_scrolls_content_and_keeps_consent_buttons_reachable(app, selected):
    plan, pixels = repair_plan(selected=selected, unavailable=not selected)
    dialog, _ = _dialog_for(plan, pixels)
    dialog.resize(360, 520)
    dialog.show()
    _flush(app, dialog)
    for _ in range(10):
        app.processEvents()
    assert (dialog.width(), dialog.height()) == (360, 520)
    scrollbar = dialog.repair_scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    for position in (0, scrollbar.maximum()):
        scrollbar.setValue(position)
        app.processEvents()
        for widget in (dialog.confirm_button, dialog.cancel_button):
            assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
        assert dialog.repair_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.rect().contains(dialog.revise_button.mapTo(dialog, dialog.revise_button.rect().bottomRight()))
    for label in (dialog.summary, dialog.validation_status, dialog.reminder, dialog.selection_note):
        assert label.height() >= label.heightForWidth(label.width())
    assert dialog.validation_status.geometry().bottom() < dialog.reminder.geometry().top()
    assert dialog.confirm_button.isEnabled() is selected
    if selected:
        dialog.confirm_button.click()
        _flush(app, dialog)
        assert dialog.result() == QDialog.DialogCode.Accepted
    else:
        dialog.cancel_button.click()
        assert dialog.result() == QDialog.DialogCode.Rejected


class RepairRoutingDialog:
    REVISE_PREVIEW = 2
    REVISE_REPAIR = 3
    instances = []
    normal_result = QDialog.DialogCode.Rejected
    def __init__(self, plan, loader, parent):
        self.plan = plan
        self.instances.append(self)
    def exec(self):
        if self.plan.get("repair", {}).get("selected_option_ids"):
            return QDialog.DialogCode.Accepted
        return self.REVISE_REPAIR if "repair" in self.plan else self.normal_result
    def selected_repair_records(self):
        return ("explicit-choice",)
    def deleteLater(self):
        pass


def _routing(monkeypatch, *, failure=False):
    import integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog as module
    RepairRoutingDialog.instances = []
    RepairRoutingDialog.normal_result = QDialog.DialogCode.Rejected
    monkeypatch.setattr(module, "VisualImportEgressDialog", RepairRoutingDialog)
    facade = _Facade(profiles=(_visual_profile(),))
    facade.visual_plan["repair"] = {"selected_option_ids": []}
    calls = []
    def revise(preview_id, revision, choices):
        calls.append(("revise", choices))
        plan = deepcopy(facade.visual_plan)
        plan.update(preview_id="repair-preview", revision="repair-revision", repair={"selected_option_ids": list(choices)})
        return plan
    def repair(**kwargs):
        calls.append(("repair", kwargs))
        if failure:
            raise RuntimeError("合成本机修复失败；旧记录保留")
        plan = deepcopy(facade.visual_plan)
        plan.pop("repair")
        plan.update(preview_id="normal-preview", revision="normal-revision")
        return plan
    facade.revise_visual_import_repair = revise
    facade.repair_visual_import_egress = repair
    tasks = _manual_task_bridge()
    return _visual_dialog(facade, tasks), facade, tasks, calls


def test_repair_commits_only_after_revised_preview_and_returns_normal_preview(qt_app, monkeypatch):
    dialog, facade, tasks, calls = _routing(monkeypatch)
    dialog._run_visual()
    tasks.finish_next()  # Inspect, then request revised preview.
    assert not calls and not facade.run_calls
    tasks.finish_next()  # Preview selected scope, then explicit local confirmation.
    assert calls == [("revise", ("explicit-choice",))]
    assert dialog._active_task_kind == "visual_repair"
    assert not dialog.generate_button.isEnabled() and not dialog.close_button.isEnabled() and not dialog.cancel_button.isEnabled()
    task_id = dialog._active_task_id
    dialog.reject()
    dialog._cancel_active()
    assert dialog._active_task_id == task_id and not tasks.cancelled
    tasks.finish_next()  # Repair, then ordinary preview is separately rejected.
    assert calls[1][0] == "repair" and calls[1][1]["teacher_confirmed"] is True
    assert RepairRoutingDialog.instances[-1].plan["preview_id"] == "normal-preview"
    assert not facade.run_calls and not tasks.pending and dialog._active_task_id is None
    assert dialog.generate_button.isEnabled()
    dialog.close()


def test_failed_repair_keeps_manual_retry_entry_without_reopening_loop(qt_app, monkeypatch):
    dialog, facade, tasks, calls = _routing(monkeypatch, failure=True)
    dialog._run_visual()
    for _ in range(3):
        tasks.finish_next()
    _settle(qt_app)
    assert len(calls) == 2 and len(RepairRoutingDialog.instances) == 2
    assert not facade.run_calls and not tasks.pending and dialog._active_task_id is None
    assert "修复未完成" in dialog.status.text() and "重新打开图片预览" in dialog.status.text()
    assert dialog.generate_button.isEnabled() and dialog.close_button.isEnabled()
    dialog.close()


def test_cancel_repair_repreview_does_not_commit(qt_app, monkeypatch):
    dialog, facade, tasks, calls = _routing(monkeypatch)
    dialog._run_visual()
    tasks.finish_next()
    assert dialog._active_task_kind == "visual_preview"
    dialog._cancel_active()
    tasks.finish_next()
    assert not calls and not facade.run_calls and not tasks.pending
    dialog.close()
