"""Capture the real native StudentPage with synthetic test doubles only.

Run with the desktop development Python. This script neither starts the real
workbench nor reads a student store, provider configuration, source question or
Word file. It moves the actual StudentPage scroll viewport to the recommendation
section; no widgets are hidden, reconstructed, or repainted for the screenshot.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
TESTS = Path(__file__).resolve().parents[1] / "tests"
OUTPUT = WORKSPACE / "runtime/deeptutor_shchem/student_practice_demo_20260912"
sys.path[:0] = [str(WORKSPACE), str(TESTS)]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_desktop_student_practice_ui import Facade, Tasks, detail, preview

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import StudentPage


class DemoFacade(Facade):
    """The existing test facade, with in-memory startup metadata for the page."""

    def __init__(self):
        super().__init__()
        self.preview = preview()
        self.preview["message_zh"] = "已按本次教师确认的教材章节，找到 1 道完整大题。"
        self.preview["notices_zh"] = (
            "合成教学样例 · 不是真实学生或真实试题，仅演示推荐与预览流程。",
        )
        self.preview["recommendations"][0]["reason_zh"] = (
            "本次教师确认的薄弱点是电子得失与氧化剂判断；"
            "本题的氧化还原反应标签与该教材章节相符。"
        )
        original = detail()
        self.detail = replace(
            original,
            context_zh=(
                "【合成题面，仅演示界面】将锌片置于硫酸铜溶液中，"
                "发生反应 Zn + Cu²⁺ → Zn²⁺ + Cu。"
                "请根据上述共同材料，完成两个小题。"
            ),
            parts=(
                replace(
                    original.parts[0],
                    summary_zh="判断氧化剂，并说明电子得失。",
                    reference_answer_zh="Cu²⁺ 得到电子，是氧化剂。",
                    answer_boundary_zh="合成教学样例的演示答案，不是官方评分标准。",
                ),
                replace(
                    original.parts[1],
                    summary_zh="说明锌元素在反应前后的化合价变化。",
                    reference_answer_zh="锌元素由 0 价升高为 +2 价。",
                    answer_boundary_zh="合成教学样例的演示答案，不是官方评分标准。",
                ),
            ),
        )
        self.review.update(
            scoring_confirmed_count=1,
            diagnostic_confirmed_count=1,
        )

    def student_profiles(self):
        return (
            {
                "student_id": "opaque-student-a",
                "label_zh": "匿名学生 · 合成演示",
                "grade": "高一",
            },
        )

    def student_analysis_profiles(self):
        # Do not enumerate any real saved provider metadata.
        return ()

    def student_curriculum_sections(self):
        return (
            {
                "section_key": "synthetic-oxidation",
                "display_label_zh": "必修第一册 · 氧化还原反应",
            },
        )


def _drain(tasks: Tasks) -> None:
    # Every operation comes from the in-memory fake above. A bounded drain
    # exposes unexpected recurring work instead of leaving a background job.
    for _ in range(20):
        if not tasks.pending:
            return
        tasks.resolve(tasks.pending.pop(0))
    raise AssertionError("Unexpected repeated work in synthetic capture")


def _capture_recommendation_view(page, width: int, name: str) -> Path:
    page.resize(width, 1100)
    page.content_layout.activate()
    QTest.qWait(80)
    # Use a real viewport of the page, preserving its existing margins and
    # scrollbar. The window height follows the natural card layout at width.
    panel = page.practice_panel
    natural_height = max(
        panel.height(),
        panel.minimumSizeHint().height(),
        panel.layout().heightForWidth(panel.width()),
    )
    page.resize(width, natural_height + 36)
    QTest.qWait(30)
    origin = panel.mapTo(page.scroll.widget(), QPoint(0, 0)).y()
    page.scroll.verticalScrollBar().setValue(max(0, origin - 18))
    QTest.qWait(30)
    assert page.width() == width
    assert page.scroll.widget().width() <= page.scroll.viewport().width()
    assert page.scroll.horizontalScrollBar().maximum() == 0
    panel_top = panel.mapTo(page.scroll.viewport(), QPoint(0, 0)).y()
    assert panel_top >= 0
    assert panel_top + panel.height() <= page.scroll.viewport().height()
    visible_text = "\n".join(label.text() for label in panel.findChildren(QLabel))
    for expected in (
        "本次学情",
        "合成教学样例",
        "推荐依据",
        "完整大题 2 单元",
        "共同材料 1 项",
    ):
        assert expected in visible_text
    assert "opaque-" not in visible_text
    target = OUTPUT / name
    assert page.grab().save(str(target))
    return target


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for font in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(font).is_file():
            QFontDatabase.addApplicationFont(font)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(WORKBENCH_STYLE)
    app.setQuitOnLastWindowClosed(False)

    facade, tasks = DemoFacade(), Tasks()
    page = StudentPage(facade, tasks)
    files = []
    try:
        page.resize(900, 1100)
        page.show()
        QTest.qWait(30)
        _drain(tasks)
        page._render_submission(
            {
                "student_id": "opaque-student-a",
                "submission_id": "opaque-submission-a",
                "revision": "opaque-revision",
                "status": "awaiting_teacher_review",
                "status_zh": "候选结果可复核",
                "message_zh": "合成演示：教师已记录本次评分和教材章节诊断。",
                "candidate_available": True,
                "matches": (),
                "pages": (),
            }
        )
        _drain(tasks)
        assert not facade.preview_calls
        page.practice_panel.recommend_button.click()
        _drain(tasks)
        card = page.practice_panel.cards["opaque-candidate"]
        assert not card.add_button.isEnabled()
        files.append(
            _capture_recommendation_view(
                page, 900, "student-practice-900-before-preview.png"
            )
        )

        card.view_button.click()
        _drain(tasks)
        dialog = page._practice_dialog
        assert dialog is not None
        assert len(dialog.detail.parts) == 2
        assert "共同材料" in dialog.detail.context_zh
        dialog._check_preview()
        assert card.add_button.isEnabled()
        dialog.close()
        QTest.qWait(20)
        assert not facade.add_calls
        files.append(
            _capture_recommendation_view(
                page, 420, "student-practice-420-after-preview.png"
            )
        )
        assert len(facade.preview_calls) == 1
        assert len(facade.detail_calls) == 1
        assert not facade.add_calls
        print(
            json.dumps(
                {
                    "synthetic_only": True,
                    "real_student_or_provider_reads": False,
                    "basket_writes": 0,
                    "captures": [str(path.resolve()) for path in files],
                    "states": [
                        "before complete-theme preview",
                        "after complete-theme preview; not added",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
