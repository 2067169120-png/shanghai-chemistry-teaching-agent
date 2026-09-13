from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox
from test_desktop_preparation import _raw_candidate
from test_desktop_ui import _PreparationFacade
from test_preparation_revision_ui import HeldTasks

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery_dialog import (
    PreparationRecoveryDialog,
    _readable_return,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)

pytest_plugins = ("test_desktop_ui",)


def recovery_source():
    raw = _raw_candidate()
    visual = {
        "kind": "comparison",
        "comparison": {
            "dimension_label": "复习板块",
            "columns": ["概念/对象", "判断依据或条件", "典型例式"],
            "rows": [
                {"label": "类别甲", "values": ["甲的依据", "甲的例式"]},
                {"label": "类别乙", "values": ["乙的依据", "乙的例式"]},
            ],
        },
        "steps": [],
    }
    for slide in raw["slides"][:2]:
        slide["visual"] = deepcopy(visual)
    return {
        "task_id": "failed-original",
        "source_revision": "c" * 64,
        "candidate": raw,
        "error_message": "PPT页面1：比较表第1行有2格内容，但表头有3个数据列。",
    }


@pytest.fixture
def recovery_dialog(qt_app):
    calls, tasks = [], HeldTasks()
    facade = SimpleNamespace(
        repair_returned_preparation=lambda task, revision, edits, *, note="": (
            calls.append((task, revision, edits, note))
            or SimpleNamespace(task_id="child", status="completed")
        )
    )
    source = recovery_source()
    dialog = PreparationRecoveryDialog(facade, tasks, source)
    dialog.show()
    yield dialog, tasks, calls, source
    dialog.pending.clear()
    dialog.busy = False
    dialog.close()


def _fix_first_table(dialog, monkeypatch):
    dialog.table.item(0, 1).setText("判断依据或条件")
    dialog.table.item(0, 2).setText("典型例式")
    dialog.table.setCurrentCell(0, 3)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes
    )
    dialog.remove_column.click()


def test_long_table_text_reflows_after_narrow_resize(recovery_dialog, qt_app):
    dialog, _tasks, calls, _source = recovery_dialog
    text = "电解质：两种状态至少一种能导电的化合物；非电解质：两种状态都不能导电的化合物"
    dialog.table.item(1, 1).setText(text)
    for _ in range(3):
        qt_app.processEvents()
    wide_height = dialog.table.rowHeight(1)
    dialog.resize(420, 780)
    for _ in range(3):
        qt_app.processEvents()
    assert dialog.table.rowHeight(1) > wide_height
    assert dialog.table.rowHeight(1) > 100
    assert dialog.table.item(1, 1).text() == text
    assert not calls


def test_table_navigation_remeasures_new_text_without_column_resize(
    recovery_dialog, qt_app
):
    dialog, _tasks, _calls, _source = recovery_dialog
    dialog.resize(420, 780)
    dialog.table.item(1, 1).setText("每一行都应该完整显示，不因切换表格而沿用旧高度。" * 4)
    for _ in range(3):
        qt_app.processEvents()
    long_height = dialog.table.rowHeight(1)
    dialog.group.setCurrentIndex(1)
    for _ in range(3):
        qt_app.processEvents()
    assert dialog.table.rowHeight(1) < long_height
    dialog.group.setCurrentIndex(0)
    for _ in range(3):
        qt_app.processEvents()
    assert dialog.table.rowHeight(1) == long_height


def test_recovery_is_explicit_preserves_source_and_survives_navigation(
    recovery_dialog, monkeypatch
):
    dialog, tasks, calls, source = recovery_dialog
    before = deepcopy(source)
    assert dialog.table.rowCount() == 3 and dialog.table.columnCount() == 4
    assert not dialog.pending and not dialog.save_button.isEnabled()
    _fix_first_table(dialog, monkeypatch)
    assert dialog.table.columnCount() == 3
    assert dialog.table.item(1, 1).text() == "甲的依据"
    assert dialog.table.item(1, 2).text() == "甲的例式"
    dialog.group.setCurrentIndex(1)
    dialog.group.setCurrentIndex(0)
    assert dialog.table.item(0, 1).text() == "判断依据或条件"
    dialog._refresh_review()
    assert (
        "原表" in dialog.review.toPlainText() and "修订" in dialog.review.toPlainText()
    )
    assert source == before
    dialog._save()
    assert dialog.busy and not calls and not dialog.tabs.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    tasks.success(tasks.operation())
    assert not dialog.isVisible()
    assert calls[0][:2] == ("failed-original", "c" * 64)
    assert calls[0][2] == [
        {
            "slide_number": 1,
            "comparison": {
                "dimension_label": "复习板块",
                "columns": ["判断依据或条件", "典型例式"],
                "rows": [
                    {"label": "类别甲", "values": ["甲的依据", "甲的例式"]},
                    {"label": "类别乙", "values": ["乙的依据", "乙的例式"]},
                ],
            },
        }
    ]
    assert source == before


def test_missing_cells_and_long_text_block_local_export(recovery_dialog, monkeypatch):
    dialog, tasks, calls, _ = recovery_dialog
    dialog.table.item(0, 1).setText("新表头")
    dialog._save()
    assert "尚不完整" in dialog.status.text() and not hasattr(tasks, "operation")
    _fix_first_table(dialog, monkeypatch)
    dialog.table.item(1, 1).setText("字" * 121)
    dialog._save()
    assert "120" in dialog.status.text() and not calls and not dialog.busy


def test_delete_requires_selected_data_column_and_confirmation(
    recovery_dialog, monkeypatch
):
    dialog, _, _, _ = recovery_dialog
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_a, **_k: questions.append(True) or QMessageBox.StandardButton.No,
    )
    dialog.table.setCurrentCell(1, 0)
    dialog.remove_column.click()
    assert not questions and dialog.table.columnCount() == 4
    dialog.table.setCurrentCell(1, 1)
    dialog.remove_column.click()
    assert questions and dialog.table.columnCount() == 4 and not dialog.pending


def test_failure_retains_changes_and_does_not_echo_internal_errors(
    recovery_dialog, monkeypatch
):
    dialog, tasks, _, _ = recovery_dialog
    _fix_first_table(dialog, monkeypatch)
    dialog._save()
    tasks.failure("private credential filesystem detail")
    assert dialog.pending and dialog.save_button.isEnabled() and not dialog.busy
    assert (
        "private" not in dialog.status.text()
        and "未重新请求模型" in dialog.status.text()
    )


def test_unknown_shapes_remain_readable_without_offering_auto_repair(qt_app):
    source = {
        "candidate": {"slides": "未形成页面列表", "unknown": {"detail": "需核对的信息"}}
    }
    dialog = PreparationRecoveryDialog(SimpleNamespace(), HeldTasks(), source)
    assert not dialog.save_button.isEnabled() and not dialog.originals
    assert "需核对的信息" in dialog.original_text.toPlainText()
    assert "不能修复其它结构" in dialog.status.text()
    dialog.close()
    assert "<img src=anything>" in _readable_return({"content": "<img src=anything>"})


def test_page_recovery_entry_only_for_persisted_failed_return(
    qt_app, tmp_path, monkeypatch
):
    facade, bridge = _PreparationFacade(tmp_path), DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    source, calls = recovery_source(), []
    monkeypatch.setattr(
        facade,
        "preparation_returned_source",
        lambda task: calls.append(task) or source,
        raising=False,
    )
    monkeypatch.setattr(
        PreparationRecoveryDialog,
        "exec",
        lambda self: calls.append(self.source["task_id"]),
    )
    page._render_summary(
        SimpleNamespace(
            task_id="failed-original",
            status="failed",
            artifact_ids=(),
            returned_candidate_available=True,
            retryable=True,
        )
    )
    assert page.recover_returned_button.isVisible() and page.result_card.isVisible()
    assert all(button.isHidden() for button in page._result_artifact_buttons)
    page.recover_returned_button.click()
    assert calls == ["failed-original", "failed-original"] and not facade.generate_calls
    page._render_summary(
        SimpleNamespace(task_id="other", status="failed", artifact_ids=())
    )
    assert page.recover_returned_button.isHidden()
    page._render_summary(
        SimpleNamespace(
            task_id="done", status="completed", artifact_ids=("candidate_json",)
        )
    )
    assert page.recover_returned_button.isHidden()
    page.close()
    bridge.shutdown(1000)


def test_retry_confirmation_points_to_no_model_recovery_first(
    qt_app, tmp_path, monkeypatch
):
    facade, bridge = _PreparationFacade(tmp_path), DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _p, title, message, *_a: (
            messages.append(message) or QMessageBox.StandardButton.No
        ),
    )
    page._render_summary(
        SimpleNamespace(
            task_id="failed-original",
            status="failed",
            artifact_ids=(),
            returned_candidate_available=True,
            retryable=True,
        )
    )
    page._activate_current_task()
    assert "本地修复表格" in messages[0] and "费用" in messages[0]
    assert not facade.generate_calls
    page.close()
    bridge.shutdown(1000)
