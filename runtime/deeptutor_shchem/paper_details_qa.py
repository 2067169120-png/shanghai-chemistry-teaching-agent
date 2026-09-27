"""Offline current-paper details acceptance using synthetic sources and real Qt.

Use QT_SCALE_FACTOR=2 and --output <new empty directory>. No real facade,
provider, Office, original files, or personal state are opened.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt, qVersion
from PySide6.QtWidgets import QDialog, QVBoxLayout
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import (
    SyntheticFacade, Tasks, rect_in, settle,
)


from runtime.deeptutor_shchem.paper_details_fixture import rows


class DetailsFacade(SyntheticFacade):
    read_failure = False

    def __init__(self, root):
        super().__init__(root)
        self.rows = rows()
        self.state_store.clear_basket()
        self.state_store.add_many_to_basket(self.rows)

    def paper_basket_projection(self):
        if self.read_failure:
            raise OSError("synthetic source read failure")
        return super().paper_basket_projection()


def make_panel(app, root, width=360, height=520):
    facade, tasks = DetailsFacade(root), Tasks()
    window = QDialog()
    window.setWindowTitle("当前卷细目 · 合成验收")
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = MixedPaperPanel(facade, tasks, PaperComposerModel(), window)
    layout.addWidget(panel)
    window.resize(width, height)
    panel.load()
    tasks.flush()
    window.show()
    settle(app)
    assert window.size().width() == width and window.size().height() == height
    return facade, tasks, window, panel


def geometry_checks(dialog):
    for name in ("status", "refresh_button", "edit_button", "close_button"):
        widget = getattr(dialog, name)
        assert dialog.rect().contains(rect_in(widget, dialog)), (name, widget.geometry(), dialog.size())
    label = dialog.status
    required = label.fontMetrics().boundingRect(QRect(0, 0, label.contentsRect().width(), 10000),
        Qt.TextFlag.TextWordWrap, label.text()).height()
    assert label.contentsRect().height() >= required
    for browser in (dialog.summary, dialog.item_detail, dialog.trace):
        assert browser.horizontalScrollBar().maximum() == 0
    return {"fixed_actions_visible": True, "status_complete": True,
            "horizontal_scroll": False, "status_required_height": required}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be empty; prior evidence is preserved")
    app = create_application(["paper-details-qa"])
    manifest = {"schema": "shchem.paper-details-qa.v1", "synthetic_only": True,
                "personal_state_accessed": False, "provider_calls": 0, "office_calls": 0,
                "qt_version": qVersion(), "images": []}
    cases = [
        ("mixed-overview-360x520", 360, 520, "mixed", "top"),
        ("partial-score-420x520", 420, 520, "mixed", "scores"),
        ("mixed-wide-900x650", 900, 650, "mixed", "top"),
        ("word-detail-360x520", 360, 520, "word", "top"),
        ("word-identity-360x520", 360, 520, "word", "bottom"),
        ("all-known-900x650", 900, 650, "known", "top"),
        ("empty-360x520", 360, 520, "empty", "top"),
        ("removed-420x520", 420, 520, "removed", "top"),
        ("undo-restored-420x520", 420, 520, "undo", "top"),
        ("source-stale-360x520", 360, 520, "stale", "top"),
        ("read-failure-420x520", 420, 520, "failed", "top"),
    ]
    with tempfile.TemporaryDirectory(prefix="paper-details-qa-") as directory:
        for name, width, height, scenario, edge in cases:
            facade, tasks, window, panel = make_panel(app, Path(directory) / name, width, height)
            store = facade.state_store
            basket, other = store.basket_snapshot(), store.draft_snapshot("other-paper")
            initial = store.draft_snapshot(DRAFT_ID).record
            if scenario in {"removed", "undo"}:
                panel.sections.setCurrentRow(1)
                panel.remove_button.click()
                if scenario == "undo":
                    panel.undo_button.click()
                    assert store.draft_snapshot(DRAFT_ID).record == initial
            elif scenario == "empty":
                while panel.model.order:
                    panel.remove_button.click()
            elif scenario == "known":
                facade.rows[2]["content"]["source_scores"][1].update(status="present", max_score=5)
                panel.load()
                tasks.flush()
                for key in ("core-synthetic", "word-synthetic"):
                    panel.sections.setCurrentRow(panel.model.order.index(key))
                    panel.remove_button.click()
            before = store.path.read_bytes()
            panel.details_button.click()
            dialog = panel._details_dialog
            assert dialog is not None and dialog.details is None
            if scenario == "stale":
                facade.rows[0]["content"]["atomic_chain"].pop()
            elif scenario == "failed":
                facade.read_failure = True
            tasks.flush()
            settle(app)
            assert store.path.read_bytes() == before
            if scenario in {"stale", "failed"}:
                assert dialog.details is None and panel._restore_failed
            else:
                assert dialog.details is not None
                if scenario == "word":
                    dialog.selection.setCurrentIndex(1)
                    dialog.tabs.setCurrentIndex(2 if edge == "bottom" else 1)
                if scenario == "known":
                    assert dialog.details["current_score"]["total"] == 8
            dialog.resize(width, height)
            settle(app)
            browser = (dialog.trace if edge == "bottom" else dialog.item_detail) if scenario == "word" else dialog.summary
            bar = browser.verticalScrollBar()
            bar.setValue(bar.maximum() if edge == "bottom" else 0)
            if edge == "scores":
                browser.scrollToAnchor("scores")
            settle(app)
            checks = geometry_checks(dialog)
            assert dialog.width() == width and dialog.height() == height
            assert store.basket_snapshot() == basket and store.draft_snapshot("other-paper") == other
            filename = name + ".png"
            shot = dialog.grab()
            assert shot.save(str(output / filename))
            manifest["images"].append({"file": filename, "sha256": hashlib.sha256((output / filename).read_bytes()).hexdigest(),
                "scenario": scenario, "logical_size": [width, height], "pixel_size": [shot.width(), shot.height()],
                "device_pixel_ratio": shot.devicePixelRatio(), "geometry": checks})
            dialog.close()
            window.close()
            settle(app)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"images": len(manifest["images"]), "geometry_passed": True, "synthetic_only": True}))


if __name__ == "__main__":
    main()
