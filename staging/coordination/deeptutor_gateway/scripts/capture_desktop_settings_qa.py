"""Read-only native settings capture; no key reads and no outbound requests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.model_provider_settings import ModelProviderSettingsStore


def main() -> None:
    paths = DesktopPaths.discover()
    # Only public metadata is queried; never borrow a credential for screenshots.
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._providers = ModelProviderSettingsStore(paths.settings_root, project_root=paths.workspace_root)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    bridge = DesktopTaskBridge()
    dialog = SettingsDialog(facade, bridge)
    dialog.show()
    deadline = time.monotonic() + 5
    while dialog._active_task_id and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert dialog._active_task_id is None
    assert dialog.key_input.text() == ""
    output = paths.runtime_root / "qa" / time.strftime("settings-connection-%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    captures = []
    for width in (720, 420):
        dialog.resize(width, 760)
        for _ in range(5):
            app.processEvents()
        target = output / f"settings-{width}.png"
        assert dialog.grab().save(str(target))
        captures.append({"path": str(target), "width": dialog.width(), "height": dialog.height()})
    def capture_confirmation() -> None:
        message = app.activeModalWidget()
        assert message is not None
        target = output / "confirmation.png"
        assert message.grab().save(str(target))
        captures.append({"path": str(target), "width": message.width(), "height": message.height()})
        message.reject()
    QTimer.singleShot(200, capture_confirmation)
    assert dialog._confirm_connection_test(dialog._profile) is False
    dialog.close()
    bridge.shutdown()
    print(json.dumps(captures, ensure_ascii=False))


if __name__ == "__main__":
    main()
