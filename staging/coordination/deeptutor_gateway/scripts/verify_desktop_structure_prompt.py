"""Offline real-source desktop compile + native UI QA in isolated personal state."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_blueprint_generation import (
    blueprint_prompt,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_theme_structure_reference import (
    REFERENCE_KEY,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
    PromptBlueprintDialog,
)


class LocalTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        on_success(operation())

    def submit_progress(self, *args, **kwargs):
        raise AssertionError("Offline QA must not invoke model generation")


def main() -> None:
    workspace = Path.cwd()
    paths = DesktopPaths.from_workspace(
        workspace, state_root=tempfile.mkdtemp(prefix="shchem-structure-qa-")
    )
    facade = DesktopWorkbenchFacade(paths)
    app = QApplication.instance() or QApplication([])
    install_font_fallbacks()
    app.setStyleSheet(WORKBENCH_STYLE)
    dialog = None
    try:
        dialog = PromptBlueprintDialog(facade, LocalTasks())
        dialog.title_input.setText("物质的量：配制溶液的主题设计")
        dialog.goal.setPlainText(
            "在所选章节范围内规划信息提取、定量关系和操作理由的递进任务，共同材料和前题结论须有明确用途。"
        )
        dialog.grade.setCurrentIndex(dialog.grade.findData(10))
        dialog.theme_reference.setCurrentIndex(
            dialog.theme_reference.findData(REFERENCE_KEY)
        )
        for i in range(dialog.sections.count()):
            item = dialog.sections.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "TB-M1-C1:1.2":
                item.setCheckState(Qt.CheckState.Checked)
        dialog.compile_button.click()
        preview = dialog._preview
        assert (
            preview and preview["structure_reference"]["counts"]["atomic_parts"] == 13
        )
        request_text = blueprint_prompt(preview)
        assert "P10 / A13" in request_text and "前序作答依赖" in request_text
        # The existing compiler legitimately includes textbook anchor hashes.
        # The new question product's paths/hashes must remain local.
        assert (
            "local_provenance" not in request_text
            and "scan_records.jsonl" not in request_text
        )
        for source in preview["structure_reference"]["local_provenance"]["source"][
            "source_refs"
        ]:
            assert source["relative_path"] not in request_text
            assert source["sha256"] not in request_text
        saved = facade.state_store.snapshot()["drafts"][preview["preview_id"]]
        assert saved["input"]["theme_reference"] == REFERENCE_KEY
        assert saved["status"] == "previewed"
        assert (
            len(
                saved["preview"]["structure_reference"]["local_provenance"]["source"][
                    "source_refs"
                ]
            )
            == 4
        )
        output = (
            paths.runtime_root / "qa" / time.strftime("structure-prompt-%Y%m%d-%H%M%S")
        )
        output.mkdir(parents=True, exist_ok=False)
        captures = []
        dialog.show()
        for width in (900, 420):
            dialog.resize(width, 900)
            dialog._show_inputs(True)
            dialog.tabs.setCurrentWidget(dialog.evidence_output)
            for _ in range(5):
                app.processEvents()
            path = output / f"inputs-{width}.png"
            assert dialog.grab().save(str(path))
            captures.append(
                {"path": str(path), "width": dialog.width(), "height": dialog.height()}
            )
            dialog._show_inputs(False)
            dialog.evidence_output.moveCursor(
                dialog.evidence_output.textCursor().MoveOperation.Start
            )
            assert dialog.evidence_output.find("2025 奉贤二模")
            for _ in range(5):
                app.processEvents()
            path = output / f"reference-{width}.png"
            assert dialog.grab().save(str(path))
            captures.append(
                {"path": str(path), "width": dialog.width(), "height": dialog.height()}
            )
        result = {
            "status": "offline_real_source_compile_passed",
            "curriculum_sections": dialog.sections.count(),
            "evidence_count": len(preview["evidence"]),
            "reference_counts": preview["structure_reference"]["counts"],
            "prompt_characters": len(request_text),
            "model_calls": 0,
            "isolated_state": str(paths.state_root),
            "preview_id": preview["preview_id"],
            "captures": captures,
        }
        (output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if dialog is not None:
            dialog.close()
        facade.shutdown()


if __name__ == "__main__":
    main()
