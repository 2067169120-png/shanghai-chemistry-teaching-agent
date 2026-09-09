"""Inspect synthetic failure messages in the actual native preparation page."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "staging/coordination/deeptutor_gateway/tests"))

from PySide6.QtWidgets import QApplication, QScrollArea
from test_desktop_preparation_provider import _context
from test_desktop_ui import _PreparationFacade

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    DesktopPreparationProviderError,
    StructuredPreparationProvider,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


def main():
    output = ROOT / "runtime/deeptutor_shchem/qa/preparation-failure-ui-0121"
    output.mkdir(parents=True, exist_ok=True)
    assert not list(output.glob("*.png")), "Refuse to overwrite existing captures"
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    bridge = DesktopTaskBridge()
    page = PreparationPage(
        _PreparationFacade(Path(tempfile.mkdtemp(prefix="shchem-prep-error-ui-"))),
        bridge,
    )
    page._availability_timer.stop()
    page.show()
    captures = []
    for code in ("timeout", "dns_failure", "invalid_credentials"):

        class FailedTransport:
            def send(self, *args, error_code=code, **kwargs):
                error = RuntimeError("synthetic offline failure")
                error.code = error_code
                raise error

        try:
            StructuredPreparationProvider(
                _context(), transport=FailedTransport()
            ).generate({"topic": "synthetic UI check"})
        except DesktopPreparationProviderError as exc:
            summary = {
                "task_id": "PREP-synthetic-offline",
                "status": "failed",
                "message_zh": exc.message_zh,
                "progress_percent": 100,
                "retryable": exc.retryable,
                "artifact_ids": [],
            }
        page._render_summary(summary)
        for width in (920, 420):
            page.resize(width, 850)
            for _ in range(5):
                app.processEvents()
            scroll = page.findChild(QScrollArea, "PageScroll")
            scroll.ensureWidgetVisible(page.progress_card)
            app.processEvents()
            assert page.width() == width
            assert scroll.horizontalScrollBar().maximum() == 0
            path = output / f"{code}-{width}.png"
            assert page.grab().save(str(path))
            captures.append(str(path))
    page.close()
    receipt = {
        "synthetic_only": True,
        "model_calls": 0,
        "captures": captures,
        "horizontal_overflow": False,
    }
    (output / "verification.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
