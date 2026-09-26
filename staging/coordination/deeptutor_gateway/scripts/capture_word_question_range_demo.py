"""Owned Qt widget captures of synthetic Word range review; no source/store I/O."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
TESTS = Path(__file__).resolve().parents[1] / "tests"
OUTPUT = WORKSPACE / "runtime/deeptutor_shchem/word_question_range_review_20260912"
sys.path[:0] = [str(WORKSPACE), str(TESTS)]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from test_word_question_range_review import item, source

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionRangeDialog,
)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    for font in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(font).is_file():
            QFontDatabase.addApplicationFont(font)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(WORKBENCH_STYLE)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    data = source()
    data["blocks"][1]["text"] = "【变式训练3·变题型（合成题）根据上述材料，判断氧化剂。"
    data["assets"] = [{"asset_id": "synthetic-image-reference", "block_index": 20}]
    paths = []
    for width in (900, 420):
        dialog = WordQuestionRangeDialog(item(), data)
        try:
            dialog.resize(width, 1280)
            dialog.show()
            QTest.qWait(50)
            dialog.fields["context_start"].setValue(10)
            dialog.fields["context_end"].setValue(10)
            dialog.preview_button.click()
            assert dialog.save_button.isEnabled()
            assert all(not box.isChecked() for box in dialog.review_checks.values())
            dialog.review_checks["unmarked_answer"].setChecked(True)
            dialog.review_checks["nonstandard_label"].setChecked(True)
            QTest.qWait(40)
            if width == 900:
                dialog.scroll.verticalScrollBar().setValue(0)
            else:
                dialog.scroll.ensureWidgetVisible(dialog.reading_tabs, 0, 16)
            QTest.qWait(30)
            assert dialog.width() == width
            assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
            assert dialog.original.horizontalScrollBar().maximum() == 0
            assert dialog.range_preview.horizontalScrollBar().maximum() == 0
            assert "合成题" in dialog.range_preview.toPlainText()
            assert "原图引用：1 项" in dialog.range_preview.toPlainText()
            assert (
                "范围页为文字预览，原图请在逐题预览/原Word核对"
                in dialog.range_preview.toPlainText()
            )
            assert (
                dialog.ranges is None and dialog.result() != QDialog.DialogCode.Accepted
            )
            path = OUTPUT / f"synthetic-word-range-review-{width}.png"
            assert dialog.grab().save(str(path))
            paths.append(str(path.resolve()))
        finally:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
    print(
        json.dumps(
            {"synthetic_only": True, "range_saves": 0, "captures": paths},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
