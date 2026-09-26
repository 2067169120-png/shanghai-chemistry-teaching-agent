from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest_plugins = ("test_desktop_ui",)

from test_desktop_ui import _PreparationFacade

from integrations.deeptutor_shchem_v1.desktop_workbench import workflow_pages
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


def _completed_summary(task_id: str, *artifact_ids: str) -> object:
    return SimpleNamespace(
        task_id=task_id,
        status="completed",
        title_zh="电解质复习",
        output_kind="joint",
        created_at="2026-09-09T10:00:00Z",
        updated_at="2026-09-09T10:01:00Z",
        progress_percent=100,
        message_zh="候选文件已经生成。",
        artifact_ids=artifact_ids,
        slide_count=4,
        retryable=False,
    )


def _task_summary(task_id: str, status: str) -> object:
    return SimpleNamespace(
        task_id=task_id,
        status=status,
        title_zh="电解质复习",
        output_kind="joint",
        created_at="2026-09-09T10:00:00Z",
        updated_at="2026-09-09T10:01:00Z",
        progress_percent=35,
        message_zh="任务状态已更新。",
        artifact_ids=(),
        slide_count=0,
        retryable=False,
    )


@pytest.fixture
def preparation_page(qt_app, tmp_path):
    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.show()
    yield page, facade, bridge
    page.close()
    bridge.shutdown(1000)


def test_worksheet_button_requires_explicit_artifact(preparation_page):
    page, _facade, _bridge = preparation_page

    page._render_summary(_completed_summary("task-without-worksheet", "pptx"))
    assert page.result_card.isVisible()
    assert page.open_worksheet_button.isHidden()

    page._render_summary(
        _completed_summary("task-with-worksheet", "pptx", "student_worksheet_docx")
    )
    assert page.open_worksheet_button.isVisible()
    assert page.open_worksheet_button.text() == "打开学习单"


def test_worksheet_button_is_cleared_when_task_changes_or_fails(preparation_page):
    page, _facade, _bridge = preparation_page

    page._render_summary(_completed_summary("task-a", "pptx", "student_worksheet_docx"))
    assert page.open_worksheet_button.isVisible()

    page._render_summary(_task_summary("task-b", "running"))
    assert page.open_worksheet_button.isHidden()
    assert not page.result_card.isVisible()

    page._render_summary(_task_summary("task-b", "failed"))
    assert page.open_worksheet_button.isHidden()
    assert not page.result_card.isVisible()

    page._render_summary(_completed_summary("task-b", "pptx"))
    assert page.open_worksheet_button.isHidden()


def test_worksheet_button_opens_student_worksheet_artifact(
    preparation_page, monkeypatch, tmp_path
):
    page, facade, _bridge = preparation_page
    page._render_summary(_completed_summary("task-worksheet", "student_worksheet_docx"))

    artifact_calls: list[tuple[str, str]] = []
    worksheet_path = tmp_path / "student_worksheet.docx"

    def artifact_path(task_id: str, artifact_id: str):
        artifact_calls.append((task_id, artifact_id))
        return worksheet_path

    facade.preparation_artifact_path = artifact_path
    opened: list[str] = []
    monkeypatch.setattr(
        workflow_pages,
        "QDesktopServices",
        SimpleNamespace(
            openUrl=lambda url: opened.append(url.toLocalFile()) or True,
        ),
    )

    page.open_worksheet_button.click()

    assert artifact_calls == [("task-worksheet", "student_worksheet_docx")]
    assert [Path(value) for value in opened] == [worksheet_path]


@pytest.mark.parametrize("width", [420, 900])
def test_all_result_actions_fit_narrow_and_wide_page(
    preparation_page, qt_app, tmp_path, width
):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QBoxLayout, QScrollArea

    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        WORKBENCH_STYLE,
        install_font_fallbacks,
    )

    page, _facade, _bridge = preparation_page
    # Match the real window startup; a bare page omits font registration in
    # Qt's offscreen plugin and can silently render Chinese as tofu.
    page.setFont(QFont(install_font_fallbacks(), 10))
    page.setStyleSheet(WORKBENCH_STYLE)
    page.resize(width, 900)
    page._render_summary(
        _completed_summary(
            "all-artifacts",
            "pptx",
            "lesson_plan_docx",
            "student_worksheet_docx",
            "preview_montage",
            "candidate_json",
        )
    )
    qt_app.processEvents()
    expected = (
        QBoxLayout.Direction.TopToBottom
        if width < 600
        else QBoxLayout.Direction.LeftToRight
    )
    assert page.result_actions.direction() == expected
    bounds = []
    for button in (*page._result_artifact_buttons, page.review_structure_button):
        assert button.isVisible()
        assert all(button.fontMetrics().inFontUcs4(ord(char)) for char in button.text())
        rect = QRect(button.mapTo(page.result_card, QPoint()), button.size())
        assert page.result_card.rect().contains(rect)
        assert rect.height() >= 28
        assert (
            button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 16
        )
        assert all(not rect.intersects(other) for other in bounds)
        bounds.append(rect)
    for scroll in page.findChildren(QScrollArea):
        assert scroll.horizontalScrollBar().maximum() == 0
    assert page.result_card.grab().save(str(tmp_path / f"result-actions-{width}.png"))
