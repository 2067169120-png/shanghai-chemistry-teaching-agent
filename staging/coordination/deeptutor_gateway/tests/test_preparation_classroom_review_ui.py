from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from test_desktop_ui import _PreparationFacade
from test_preparation_classroom_review import _candidate

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_review import classroom_review
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_review_dialog import (
    PreparationReviewDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)

pytest_plugins = ("test_desktop_ui",)


def test_facade_reference_whitelist_uses_task_input_not_model_source_summary():
    calls = []
    candidate = _candidate()
    candidate["source_basis"] = "模型自行概括的来源摘要"
    value = {
        "candidate": candidate,
        "payload": {
            "topic": "当时的课题",
            "materials": "当时选定的完整资料，最后一句也保留。",
            "advanced": {"private": "not-for-reference-view"},
        },
        "profile_id": "private-profile-sentinel",
    }
    manager = SimpleNamespace(revision_source=lambda task: calls.append(task) or value)
    facade = SimpleNamespace(_preparation_manager_instance=lambda: manager)
    report = DesktopWorkbenchFacade.preparation_classroom_review(facade, "chosen")
    assert calls == ["chosen"]
    assert report["source_reference"] == {
        "topic": "当时的课题",
        "materials": "当时选定的完整资料，最后一句也保留。",
    }
    assert "模型自行概括" not in str(report["source_reference"])
    assert "private-profile-sentinel" not in str(report)
    assert "not-for-reference-view" not in str(report)


@pytest.fixture
def page(qt_app, tmp_path):
    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    view = PreparationPage(facade, bridge)
    view._availability_timer.stop()
    view.show()
    yield view, facade
    view.close()
    bridge.shutdown(1000)


def _summary(status="completed", artifacts=("pptx", "candidate_json"), task="a"):
    return SimpleNamespace(task_id=task, status=status, artifact_ids=artifacts)


def test_button_requires_completed_ppt_and_candidate_and_clears_on_change(page):
    view, _ = page
    view._render_summary(_summary())
    assert view.review_structure_button.isVisible()
    view._render_summary(_summary(artifacts=("pptx",)))
    assert view.review_structure_button.isHidden()
    view._render_summary(_summary())
    view._render_summary(_summary(status="running", task="b"))
    assert view.review_structure_button.isHidden()


def test_review_opens_current_task_without_generation(page, monkeypatch):
    view, facade = page
    calls = []
    report = classroom_review(_candidate())
    monkeypatch.setattr(
        facade,
        "preparation_classroom_review",
        lambda task: calls.append(task) or report,
        raising=False,
    )
    monkeypatch.setattr(
        PreparationReviewDialog,
        "exec",
        lambda self: calls.append(self.review_text.toPlainText()),
    )
    view._render_summary(_summary(task="selected-task"))
    view.review_structure_button.click()
    assert calls[0] == "selected-task"
    assert "逐页内容与教师备注" in calls[1]
    assert facade.generate_calls == [] and facade.prepare_calls == []


def test_review_failure_is_sanitized(page, monkeypatch):
    view, facade = page

    def fail(_task):
        raise RuntimeError("private-path-and-secret")

    monkeypatch.setattr(facade, "preparation_classroom_review", fail, raising=False)
    view._render_summary(_summary())
    view.review_structure_button.click()
    assert "原课件未修改" in view.status.text()
    assert "private-path" not in view.status.text()


def test_dialog_is_read_only_and_plain_text(qt_app):
    c = _candidate()
    c["slides"][0]["content"] = ["<script>literal source text</script>"]
    dialog = PreparationReviewDialog(classroom_review(c))
    assert dialog.review_text.isReadOnly()
    assert "<script>literal source text</script>" in dialog.review_text.toPlainText()
    dialog.close()


def test_source_compare_separates_notes_and_handles_formula_spelling(qt_app):
    candidate = _candidate()
    candidate["slides"][0]["content"] = ["NH₄Cl = NH₄⁺ + Cl⁻"]
    candidate["slides"][1]["teacher_notes"] = "NH₄Cl例题讲评还要补入正文。"
    report = classroom_review(candidate)
    report["source_reference"] = {
        "topic": "电离",
        "materials": "Word区块96\n氯化铵 NH_{4}Cl 的分类例题。\n<script>原样保留</script>",
    }
    dialog = PreparationReviewDialog(report)
    assert "<script>原样保留</script>" in dialog.source_text.toPlainText()
    dialog.search.setText("NH4Cl")
    dialog._compare()
    assert "NH_{4}Cl" in dialog.source_text.toPlainText()
    assert "NH₄Cl" in dialog.student_text.toPlainText()
    assert "学生正文匹配页：1" in dialog.match_status.text()
    assert "仅教师备注匹配页：2" in dialog.match_status.text()
    assert "还要补入正文" not in dialog.student_text.toPlainText()
    dialog._reset()
    assert dialog.search.text() == ""
    assert "<script>原样保留</script>" in dialog.source_text.toPlainText()
    assert dialog.source_text.isReadOnly() and dialog.student_text.isReadOnly()
    dialog.close()


def test_source_compare_missing_reference_is_not_replaced_by_model_notes(qt_app):
    dialog = PreparationReviewDialog(classroom_review(_candidate()))
    assert "未提供当时选定的资料" in dialog.source_text.toPlainText()
    dialog.search.setText("不在资料中的内容")
    dialog._compare()
    assert "没有资料快照" in dialog.match_status.text()
    assert "未在学生可见文字中找到" in dialog.student_text.toPlainText()
    dialog.close()


def test_source_compare_stacks_panes_in_narrow_window(qt_app):
    dialog = PreparationReviewDialog(classroom_review(_candidate()))
    dialog.show()
    dialog.resize(420, 780)
    qt_app.processEvents()
    assert dialog.splitter.orientation() == Qt.Orientation.Vertical
    assert dialog.width() <= 420
    dialog.resize(1060, 780)
    qt_app.processEvents()
    assert dialog.splitter.orientation() == Qt.Orientation.Horizontal
    dialog.close()
