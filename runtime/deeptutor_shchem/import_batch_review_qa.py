"""Capture source-bound batch review with synthetic data and no personal store."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QObject, Signal, Qt
    from integrations.deeptutor_shchem_v1.desktop_facade import (
        DesktopNativeImportFile, DesktopVisualImportReceipt, DesktopVisualImportSourceSummary,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.import_batch_dialog import ImportBatchDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    files = (
        DesktopNativeImportFile("synthetic-ok", "化学反应与平衡（合成解析版）.docx", "completed", 1),
        DesktopNativeImportFile("synthetic-failed-1", "物质分离与提纯（合成解析版）.docx", "failed", 1, "docx_parse_failed"),
        DesktopNativeImportFile("synthetic-failed-2", "物质结构与性质（较长文件名的合成资料）.docx", "failed", 2, "import_state_write_failed"),
    )
    receipt = DesktopVisualImportReceipt(
        batch_id="synthetic-private-id", source_type="教师讲义 · 合成验收", status="failed",
        visual_status="awaiting_visual_provider", source_count=4, native_quick_count=3,
        visual_queue_count=1, sources=tuple(DesktopVisualImportSourceSummary(
            "handout", index, item.filename, "native_text_complete"
        ) for index, item in enumerate(files, 1)) + (
            DesktopVisualImportSourceSummary("handout", 4, "实验图（合成资料）.png", "requires_visual_completion"),
        ), message_zh="合成状态",
        native_failed_count=2, native_completed_count=1, native_files=files,
        native_revision="synthetic-revision",
        created_at="2026-09-27T09:30:00Z",
    )

    class Facade:
        def import_batch_details(self, _batch_id):
            return receipt

        def list_import_batches(self):
            return (replace(receipt, batch_id="synthetic-earlier", created_at="2026-09-26T10:00:00Z"), receipt)

        def list_imported_word_batches(self):
            return ()

        def list_provider_profiles(self):
            return ()

    class Tasks(QObject):
        task_finished = Signal(str)
        task_progress = Signal(str, object)
        task_cancelled = Signal(str)

    app = create_application(["import-batch-review-qa"])
    tasks = Tasks()
    captures = []
    for width, height in ((900, 760), (420, 740), (360, 740)):
        dialog = ImportBatchDialog(Facade(), tasks, receipt.batch_id)
        dialog.resize(width, height)
        dialog.show()
        dialog.files.item(1).setCheckState(Qt.CheckState.Checked)
        for _ in range(8):
            app.processEvents()
        assert dialog.width() == width
        assert dialog.files.horizontalScrollBar().maximum() == 0
        assert dialog.retry_button.isEnabled() and dialog.retry_button.isVisible()
        assert dialog.rect().contains(dialog.close_button.geometry())
        path = output / f"import-batch-{width}x{height}.png"
        assert dialog.grab().save(str(path))
        captures.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
    history = ImportDialog(Facade(), tasks)
    history.resize(540, 860)
    history.show()
    for _ in range(8):
        app.processEvents()
    assert history.resume_combo.count() == 2
    assert history.batch_details_button.isVisible()
    path = output / "import-history-540x860.png"
    assert history.grab().save(str(path))
    captures.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    history.reject()
    (output / "manifest.json").write_text(json.dumps({
        "scope": "Synthetic UI only, no personal database/source/provider access",
        "captures": captures, "source_writes": 0, "provider_calls": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
