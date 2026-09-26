"""Render native recovery UI with the saved live return; no facade or network."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QMessageBox

from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
    create_application,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery_dialog import (
    PreparationRecoveryDialog,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    raw_path = (
        ROOT
        / "runtime/deeptutor_shchem/qa/word-led-two-periods-v19-live-20260909/returned-candidate.json"
    )
    data = raw_path.read_bytes()
    source = {
        "task_id": "isolated-read-only-ui-example",
        "source_revision": hashlib.sha256(data).hexdigest(),
        "candidate": json.loads(data),
        "error_message": "PPT页面15：比较表第1行有2格内容，但表头有3个数据列。请核对数据列与行标题。",
    }
    app = create_application([])
    install_font_fallbacks()
    view = PreparationRecoveryDialog(SimpleNamespace(), SimpleNamespace(), source)
    view.show()
    assert view.current_number == 15
    images = []
    for width in (980, 420):
        view.resize(width, 780)
        app.processEvents()
        for tab, label in ((0, "table"), (1, "full-return")):
            view.tabs.setCurrentIndex(tab)
            app.processEvents()
            app.processEvents()
            target = output / f"recovery-{width}-{label}.png"
            assert view.grab().save(str(target))
            images.append(str(target))
    view.tabs.setCurrentIndex(0)
    view.resize(980, 780)
    view.table.item(0, 1).setText("判断依据或条件")
    view.table.item(0, 2).setText("典型例式")
    view.table.setCurrentCell(0, 3)
    original_question = QMessageBox.question
    try:
        QMessageBox.question = lambda *_a, **_k: QMessageBox.StandardButton.Yes
        view._remove("column")
    finally:
        QMessageBox.question = original_question
    view.tabs.setCurrentIndex(2)
    app.processEvents()
    target = output / "recovery-explicit-edit-review.png"
    assert view.grab().save(str(target))
    images.append(str(target))
    assert raw_path.read_bytes() == data
    assert view.pending[15]["columns"] == ["判断依据或条件", "典型例式"]
    assert (
        view.pending[15]["rows"]
        == source["candidate"]["slides"][14]["visual"]["comparison"]["rows"]
    )
    report = {
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "selected_error_slide": 15,
        "images": images,
        "network_calls": 0,
        "source_unchanged": True,
        "original_content_cells_preserved_after_explicit_edit": True,
        "export_started": False,
        "visual_review": "pending",
    }
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))
    view.pending.clear()
    view.close()


if __name__ == "__main__":
    main()
