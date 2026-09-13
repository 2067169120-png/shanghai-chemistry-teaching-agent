"""Real native UI selection + save/reopen; source data, isolated personal state."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_candidate_dialog import (
    HandoutCandidateDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.handout_practice_dialog import (
    HandoutPracticeDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)


class LocalTasks:
    def submit(self, label, operation, *, on_success, on_failure):
        on_success(operation())
        return "local-native-practice-qa"


def main():
    paths = DesktopPaths.from_workspace(
        Path.cwd(), state_root=tempfile.mkdtemp(prefix="shchem-practice-ui-qa-")
    )
    facade = DesktopWorkbenchFacade(paths)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    output = (
        paths.runtime_root / "qa" / time.strftime("handout-practice-ui-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    browse = HandoutCandidateDialog(facade, LocalTasks())
    browse.package.setCurrentIndex(browse.package.findData("PKG-033"))
    for index in range(3):
        browse.results.item(index).setCheckState(Qt.CheckState.Checked)
    assert len(browse._practice_selection) == 3
    browse.package.setCurrentIndex(browse.package.findData("PKG-051"))
    index = next(
        i for i, item in enumerate(browse._shown) if item["printed_number"] == "5"
    )
    browse.results.item(index).setCheckState(Qt.CheckState.Checked)
    assert len(browse._practice_selection) == 4
    browse.show()
    captures = []
    for width in (1000, 420):
        browse.resize(width, 900)
        for _ in range(4):
            app.processEvents()
        path = output / f"selection-{width}.png"
        assert browse.grab().save(str(path))
        captures.append(str(path))
    browse.practice_button.click()
    practice = browse._practice_dialog
    assert practice and len(practice._selections) == 4
    practice.title.setText("电解质与物质检验讲义练习")
    practice.items.setCurrentRow(0)
    practice.down.click()
    practice.save.click()
    assert not practice._dirty and "已保存" in practice.status.text()
    selections = [dict(s) for s in practice._selections]
    for width in (780, 420):
        practice.resize(width, 900)
        for _ in range(4):
            app.processEvents()
        assert practice.width() == width
        path = output / f"practice-{width}.png"
        assert practice.grab().save(str(path))
        assert practice.items.horizontalScrollBar().maximum() == 0, (
            width,
            practice.items.horizontalScrollBar().maximum(),
            str(path),
        )
        captures.append(str(path))
    practice.close()
    app.processEvents()
    reopened = HandoutPracticeDialog(facade, LocalTasks(), [], browse._items)
    reopened.restore.click()
    assert reopened._selections == selections
    reopened.close()
    browse.close()
    facade.shutdown()
    report = {
        "status": "native_ui_selection_order_save_reopen_passed",
        "selected": len(selections),
        "state_root": str(paths.state_root),
        "captures": captures,
        "model_invoked": False,
    }
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
