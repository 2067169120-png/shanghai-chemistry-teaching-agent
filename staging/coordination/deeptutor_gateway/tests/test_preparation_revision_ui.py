from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox
from test_desktop_ui import _PreparationFacade
from test_preparation_text_revision import revision_candidate

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_revision_dialog import (
    PreparationRevisionDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)

pytest_plugins = ("test_desktop_ui",)


class HeldTasks:
    def submit(self, label, operation, *, on_success, on_failure):
        self.operation, self.success, self.failure = operation, on_success, on_failure
        return "local-test"


@pytest.fixture
def dialog(qt_app):
    tasks, calls = HeldTasks(), []
    facade = SimpleNamespace(
        revise_preparation=lambda task, revision, edits, *, note="": (
            calls.append((task, revision, edits, note))
            or SimpleNamespace(status="completed", task_id="new")
        )
    )
    source = {
        "task_id": "original",
        "source_revision": "a" * 64,
        "candidate": revision_candidate(),
    }
    view = PreparationRevisionDialog(facade, tasks, source)
    view.show()
    yield view, tasks, calls
    view.pending.clear()
    view.busy = False
    view.close()


def test_text_edits_survive_navigation_and_are_explicitly_submitted(dialog):
    view, tasks, calls = dialog
    path = ("slides", 1, "visual", "comparison", "rows", 0, "values", 0)
    field = next(f for f in view.fields if tuple(f["path"]) == path)
    view.group.setCurrentText(field["group"])
    view.editors[path].setPlainText("经核对的完整定义")
    view.group.setCurrentIndex(0)
    view.group.setCurrentText(field["group"])
    assert view.editors[path].toPlainText() == "经核对的完整定义"
    view._refresh_review()
    assert "原文：甲的定义" in view.review.toPlainText()
    assert "修订：经核对的完整定义" in view.review.toPlainText()
    view.save_button.click()
    assert view.busy and not calls and not view.tabs.isEnabled()
    view.reject()
    assert view.isVisible()
    tasks.success(tasks.operation())
    assert calls[0] == (
        "original",
        "a" * 64,
        [{"path": list(path), "text": "经核对的完整定义"}],
        "",
    )
    assert not view.isVisible()


def test_failure_retains_changes_and_sanitizes_errors(dialog):
    view, tasks, _ = dialog
    key = next(iter(view.editors))
    view.editors[key].setPlainText("新的教学说明")
    view._save()
    tasks.failure("private path and credentials")
    assert view.pending and view.save_button.isEnabled()
    assert "原稿未修改" in view.status.text() and "private" not in view.status.text()


def test_long_table_text_cannot_start_export(dialog):
    view, tasks, calls = dialog
    field = next(f for f in view.fields if f["limit"] == 120)
    view.group.setCurrentText(field["group"])
    view.editors[tuple(field["path"])].setPlainText("字" * 121)
    view._save()
    assert not view.busy and not calls and not hasattr(tasks, "operation")
    assert "120" in view.status.text()


def test_page_entry_only_exposes_completed_registered_candidate(
    qt_app, tmp_path, monkeypatch
):
    facade, bridge = _PreparationFacade(tmp_path), DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    source = {
        "task_id": "old",
        "source_revision": "b" * 64,
        "candidate": revision_candidate(),
    }
    calls = []
    monkeypatch.setattr(
        facade,
        "preparation_revision_source",
        lambda task: calls.append(task) or source,
        raising=False,
    )
    monkeypatch.setattr(
        PreparationRevisionDialog,
        "exec",
        lambda self: calls.append(self.source["task_id"]),
    )
    page._render_summary(
        SimpleNamespace(
            task_id="old",
            status="completed",
            artifact_ids=("candidate_json", "lesson_plan_docx"),
        )
    )
    assert page.revise_content_button.isVisible()
    page.revise_content_button.click()
    assert calls == ["old", "old"] and not facade.generate_calls
    page._render_summary(
        SimpleNamespace(task_id="new", status="running", artifact_ids=())
    )
    assert page.revise_content_button.isHidden()
    page.close()
    bridge.shutdown(1000)


def test_local_retry_confirmation_does_not_claim_model_or_charge(
    qt_app, tmp_path, monkeypatch
):
    facade, bridge = _PreparationFacade(tmp_path), DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    captured = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _p, title, message, *_a: (
            captured.append((title, message)) or QMessageBox.StandardButton.No
        ),
    )
    page._render_summary(
        SimpleNamespace(
            task_id="local",
            status="failed",
            artifact_ids=(),
            source_kind="teacher_revision",
            retryable=True,
        )
    )
    assert page.task_action_button.text() == "重试本地导出"
    page._activate_current_task()
    assert captured[0][0] == "确认本地重新导出"
    assert "不读取模型配置" in captured[0][1] and "费用" not in captured[0][1]
    assert not facade.generate_calls
    page.close()
    bridge.shutdown(1000)
