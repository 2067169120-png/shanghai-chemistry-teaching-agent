"""Capture synthetic-only unified composition UI; never read real app state/sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
TESTS = ROOT / "staging/coordination/deeptutor_gateway/tests"
sys.path[:0] = [str(ROOT), str(TESTS)]

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_personal_visual_theme_basket_ui import BasketFacade, _ImmediateTasks, select
from test_visual_mixed_paper_ui import Tasks, VisualFacade, review_every_page

from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
    PersonalVisualQuestionDialog,
)

OUTPUT = ROOT / "runtime/deeptutor_shchem/qa/visual-mixed-pagination-ui-20260914-r2"


def synthetic_page(audience: str, number: int) -> bytes:
    image = QImage(900, 1273, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#183847"))
    painter.setFont(QFont("Microsoft YaHei", 23, QFont.Weight.Bold))
    painter.drawText(QRect(60, 45, 780, 55), Qt.AlignmentFlag.AlignCenter, "统一组卷 · 纯合成分页演示")
    painter.setFont(QFont("Microsoft YaHei", 12))
    edition = "学生版 · 题面与公共材料" if audience == "student" else "教师版 · 逐题分值与参考内容"
    painter.drawText(QRect(60, 105, 780, 55), Qt.AlignmentFlag.AlignCenter, f"{edition}  /  第 {number} 页")
    painter.setPen(QColor("#9a352c"))
    painter.drawText(QRect(65, 167, 770, 60), Qt.AlignmentFlag.AlignCenter, "SYNTHETIC ONLY — 不含真实试卷、学生资料或化学答案")
    painter.setPen(QColor("#294453"))
    for index, top in enumerate((250, 535, 820), 1):
        painter.setFont(QFont("Microsoft YaHei", 15, QFont.Weight.Bold))
        title = f"{(number - 1) * 3 + index}. 合成来源内容区"
        if audience == "teacher":
            title += f"  （来源分值：{index + 1} 分）"
        painter.drawText(QRect(70, top, 760, 45), Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(QFont("Microsoft YaHei", 12))
        painter.drawText(QRect(70, top + 50, 750, 45), Qt.AlignmentFlag.AlignLeft, "该区域仅验证完整页图显示、导航、缩放及逐页核对行为。")
        painter.setPen(QColor("#7796a1"))
        painter.drawRect(75, top + 105, 740, 105)
        painter.drawText(QRect(90, top + 125, 700, 60), Qt.AlignmentFlag.AlignCenter, "参考内容占位 · 非真实答案" if audience == "teacher" else "来源题图占位 · 非真实题目")
        painter.setPen(QColor("#294453"))
    painter.drawText(QRect(60, 1190, 780, 35), Qt.AlignmentFlag.AlignCenter, f"纯合成文档页  {number}")
    painter.end()
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(raw)


class ScreenshotFacade(VisualFacade):
    def __init__(self):
        super().__init__()
        self.pages = {}

    def prepare_mixed_paper_pagination(self, preview_id, preview_hash):
        result = super().prepare_mixed_paper_pagination(preview_id, preview_hash)
        model = deepcopy(result.preview_model)
        for audience, document in model["pagination"]["documents"].items():
            for page in document["pages"]:
                raw = synthetic_page(audience, page["page_number"])
                checksum = hashlib.sha256(raw).hexdigest()
                page.update(sha256=checksum, width=900, height=1273)
                self.pages[page["image_id"]] = {"data": raw, "content_type": "image/png", "sha256": checksum}
        return replace(result, preview_model=model)

    def paper_preview_image(self, preview_id, image_id):
        self.calls.append(("image", preview_id, image_id))
        return self.pages[image_id]


def main():
    if OUTPUT.exists():
        raise SystemExit("Output already exists; choose a fresh QA round instead of overwriting.")
    app = QApplication.instance() or QApplication([])
    chosen_font = install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    app.setFont(QFont(chosen_font, 10))
    OUTPUT.mkdir(parents=True)
    captures = []

    def capture(widget, name, scenario):
        app.processEvents()
        QTest.qWait(80)
        path = OUTPUT / name
        assert not path.exists()
        assert widget.grab().save(str(path), "PNG")
        captures.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "width": widget.width(), "height": widget.height(), "scenario": scenario})

    facade, tasks = ScreenshotFacade(), Tasks()
    panel = MixedPaperPanel(facade, tasks, PaperComposerModel(title="纯合成 · 三种来源统一组卷"))
    panel.load()
    tasks.flush()
    panel.resize(900, 980)
    panel.show()
    panel.sections.setCurrentRow(2)
    capture(panel, "mixed-composer-900.png", "Three source kinds; visual theme keeps source scores and hides added-answer-line controls.")
    panel.preview_button.click()
    tasks.flush()
    dialog = panel._preview_dialog
    assert dialog.review.total_pages == 5
    assert dialog.review.loaded == {("student", 1)} and not dialog.review.reviewed
    dialog.resize(900, 950)
    dialog.zoom.setCurrentIndex(dialog.zoom.findData(50))
    capture(dialog, "pagination-student-first-900.png", "Student first page only loaded; no pages marked reviewed, confirmation disabled.")
    dialog.review_page_button.click()
    dialog.tabs.setCurrentIndex(1)
    dialog.page_selector.setValue(2)
    tasks.flush()
    capture(dialog, "pagination-teacher-second-900.png", "Teacher page 2 selected; only student page 1 explicitly reviewed, not all pages reviewed.")
    review_every_page(dialog, tasks)
    assert dialog.review.can_confirm and dialog.confirm_button.isEnabled()
    capture(dialog, "pagination-all-reviewed-900.png", "Explicitly reviewed all 2 student and 3 teacher pages; confirmation is now enabled.")
    dialog.tabs.setCurrentIndex(0)
    dialog.page_selector.setValue(1)
    dialog.zoom.setCurrentIndex(0)
    dialog.resize(420, 850)
    capture(dialog, "pagination-fit-width-420.png", "Narrow-window real-page image view using fit-to-width zoom.")
    assert dialog.tabs.widget(0).horizontalScrollBar().maximum() == 0
    dialog.reject()
    panel.close()

    personal_facade = BasketFacade()
    personal = PersonalVisualQuestionDialog(personal_facade, _ImmediateTasks())
    personal.resize(1160, 900)
    personal.show()
    select(personal, "q-a")
    assert personal.add_basket_button.isEnabled() and personal_facade.basket_calls == []
    capture(personal, "personal-visual-whole-theme-basket-1160.png", "Synthetic personal image source selected; explicit whole-theme basket button enabled, not clicked.")
    personal.reject()
    with (OUTPUT / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"synthetic_only": True, "real_sources_read": False, "real_state_read_or_written": False,
                   "font_family": chosen_font, "supersedes": "visual-mixed-pagination-ui-20260914-r1 (offscreen font glyphs unavailable)",
                   "API_calls": 0, "chemical_answers_generated": False, "teacher_reviewed": False,
                   "fixtures": ["test_visual_mixed_paper_ui.VisualFacade", "test_personal_visual_theme_basket_ui.BasketFacade"],
                   "captures": captures}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(OUTPUT), "screenshots": len(captures), "synthetic_only": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
