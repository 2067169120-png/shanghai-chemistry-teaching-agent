"""Read-only UI screenshots of a saved real blueprint; no new provider calls."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
    PromptBlueprintDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge


def main() -> None:
    paths = DesktopPaths.discover()
    facade = DesktopWorkbenchFacade(paths)
    records = facade.prompt_blueprint_history()
    assert records, "No saved real blueprint to inspect"
    record = records[0]
    sections = tuple(
        SimpleNamespace(section_key=k, display_label_zh=v)
        for k, v in zip(
            record["input"]["section_keys"],
            record["preview"]["section_labels"],
            strict=True,
        )
    )
    # The screenshot focuses on the selected saved sections. No compiler/model
    # operations are provided through this read-only presentation adapter.
    view = SimpleNamespace(
        prompt_curriculum_sections=lambda: sections,
        preparation_profiles=facade.preparation_profiles,
        prompt_blueprint_history=lambda: records,
    )
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    bridge = DesktopTaskBridge()
    dialog = PromptBlueprintDialog(view, bridge)
    dialog.show()
    deadline = time.monotonic() + 8
    while (
        dialog.history.count() < 2 or dialog.sections.count() == 0
    ) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.history.count() > 1 and dialog.sections.count()
    dialog.history.setCurrentIndex(1)
    app.processEvents()
    output = (
        paths.runtime_root / "qa" / time.strftime("blueprint-generation-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    captures = []
    for width in (900, 420):
        dialog.resize(width, 900)
        for _ in range(5):
            app.processEvents()
        for name, widget in (
            ("blueprint", dialog.generated_output),
            ("evidence", dialog.evidence_output),
        ):
            dialog.tabs.setCurrentWidget(widget)
            app.processEvents()
            target = output / f"{name}-{width}.png"
            assert dialog.grab().save(str(target))
            captures.append(
                {
                    "path": str(target),
                    "requested_width": width,
                    "actual_width": dialog.width(),
                    "height": dialog.height(),
                    "preview_id": record["preview"]["preview_id"],
                }
            )
    dialog.close()
    bridge.shutdown()
    facade.shutdown()
    print(json.dumps(captures, ensure_ascii=False))


if __name__ == "__main__":
    main()
