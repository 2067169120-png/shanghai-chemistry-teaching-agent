"""Capture owned offscreen widgets with synthetic data only; no real app/store."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
TESTS = Path(__file__).resolve().parents[1] / "tests"
OUTPUT = WORKSPACE / "runtime/deeptutor_shchem/qa/mixed-paper-20260912/ui"
sys.path[:0] = [str(WORKSPACE), str(TESTS)]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_desktop_mixed_paper_ui import Facade, Tasks

from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import PaperPage
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
)


def main():
    app = QApplication.instance() or QApplication([])
    for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(path).is_file():
            QFontDatabase.addApplicationFont(path)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(WORKBENCH_STYLE)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    files = []
    for width in (900, 420):
        facade, tasks = Facade(), Tasks()
        page = PaperPage(facade, tasks)
        page.resize(width, 1280)
        page.show()
        tasks.flush()
        panel = page._mixed_panel
        panel.title.setText("合成演示 · 同一题篮混合练习")
        panel.sections.setCurrentRow(1)
        panel.up_button.click()
        panel.points.setValue(4)
        QTest.qWait(60)
        assert page.width() == width
        assert panel.scroll.horizontalScrollBar().maximum() == 0
        target = OUTPUT / f"synthetic-mixed-composer-{width}.png"
        assert page.grab().save(str(target))
        files.append(str(target))
        panel.preview_button.click()
        tasks.flush()
        dialog = panel._preview_dialog
        dialog.resize(width, 1000)
        dialog.tabs.setCurrentIndex(1)
        QTest.qWait(60)
        assert dialog.width() == width
        assert dialog.tabs.currentWidget().horizontalScrollBar().maximum() == 0
        target = OUTPUT / f"synthetic-mixed-teacher-preview-{width}.png"
        assert dialog.grab().save(str(target))
        files.append(str(target))
        assert not any(call[0] in {"approve", "export"} for call in facade.calls)
        dialog.reject()
        page.close()
        page.deleteLater()
        app.processEvents()
    print(
        json.dumps(
            {"synthetic_only": True, "actual_state_writes": 0, "captures": files},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
