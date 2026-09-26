"""Reopen a real saved blueprint-backed preparation in isolated native UI."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QScrollArea

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths, default_state_root
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import WORKBENCH_STYLE, install_font_fallbacks
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import PreparationPage

SOURCE = Path("C:/Users/20671/AppData/Local/Temp/shchem-blueprint-prep-qa-77t2chl0")
DRAFT = "prep-6fde5769527149d68cd27e1312ba634d"


def tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    before_source, before_user = tree(SOURCE), tree(default_state_root())
    original = DesktopStateStore(SOURCE).snapshot()["drafts"][DRAFT]
    paths = DesktopPaths.from_workspace(ROOT, state_root=tempfile.mkdtemp(prefix="shchem-prep-reopen-qa-"))
    state = DesktopStateStore(paths.state_root)
    state.save_draft(DRAFT, original)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    output = paths.runtime_root / "qa" / time.strftime("preparation-reopen-%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    captures, errors = [], []

    def capture(widget, name):
        for _ in range(5):
            app.processEvents()
        path = output / f"{name}.png"
        assert widget.grab().save(str(path))
        captures.append(str(path))

    def choose():
        dialog = app.activeModalWidget()
        try:
            assert dialog.selected is not None
            assert "E25" in dialog.preview.toPlainText()
            for width in (920, 420):
                dialog.resize(width, 800)
                capture(dialog, f"saved-brief-{width}")
                assert dialog.width() == width
                assert dialog.preview.horizontalScrollBar().maximum() == 0
            dialog.load_button.click()
        except Exception as exc:
            errors.append(type(exc).__name__ + ":" + str(exc))
            if dialog:
                dialog.reject()

    bridge = DesktopTaskBridge()
    facade = DesktopWorkbenchFacade(paths, state_store=DesktopStateStore(paths.state_root))
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    try:
        page.resize(920, 900)
        page.show()
        QTimer.singleShot(0, choose)
        page.open_draft_button.click()
        assert not errors, errors
        loaded = page._payload()
        assert loaded["materials"] == original["core_fields"]["materials"]
        assert loaded["advanced"] == original["advanced"]
        assert all(loaded[key] == original["core_fields"][key] for key in original["core_fields"])
        for width in (920, 420):
            page.resize(width, 900)
            capture(page, f"reopened-form-{width}")
        page.objective.setPlainText(loaded["objective"] + "\n离线QA：补充课堂追问，未生成内容。")
        expected = page._payload()
        page.save_button.click()
        deadline = time.monotonic() + 10
        while page._save_task_id and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert page._save_task_id is None
        after = state.snapshot()["drafts"]
        assert len(after) == 2 and after[DRAFT] == original
        new_id, = [key for key in after if key != DRAFT]
        scroll = page.findChild(QScrollArea, "PageScroll")
        scroll.ensureWidgetVisible(page.status)
        capture(page, "resaved-new-copy-420")
        page.close()
        bridge.shutdown()
        # Reconstruct both facade and state reader, as a restarted app does.
        facade2 = DesktopWorkbenchFacade(paths, state_store=DesktopStateStore(paths.state_root))
        options = facade2.preparation_draft_options()
        assert options[0]["draft_id"] == new_id
        reread = facade2.load_preparation_draft(new_id, options[0]["revision"])
        assert reread["payload"] == expected
        assert tree(SOURCE) == before_source and tree(default_state_root()) == before_user
        report = {"passed": True, "model_calls": 0, "original_draft_unchanged": True,
                  "user_state_unchanged": True, "source_state_unchanged": True,
                  "restarted_reader_payload_equal": True, "source_draft": DRAFT,
                  "new_draft": new_id, "materials_chars": len(expected["materials"]),
                  "state_root": str(paths.state_root), "captures": captures}
        (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        page.close()
        bridge.shutdown()


if __name__ == "__main__":
    main()
