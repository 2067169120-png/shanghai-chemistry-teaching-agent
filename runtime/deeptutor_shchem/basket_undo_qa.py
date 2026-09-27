"""Offline basket undo acceptance: synthetic temporary store and real Qt render.

Run with QT_SCALE_FACTOR=2 and --output <new-directory>. The manifest contains
relative image names and synthetic UI evidence, never personal file paths.
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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, qVersion
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_basket import ExplorerBasketDialog


class SyntheticStore(DesktopStateStore):
    read_error = False
    write_error = False

    def _read_unlocked(self):
        if self.read_error:
            raise OSError("synthetic-private-read-error")
        return super()._read_unlocked()

    def _write_unlocked(self, value, **kwargs):
        if self.write_error:
            raise OSError("synthetic-private-write-error")
        return super()._write_unlocked(value, **kwargs)


def synthetic_rows():
    return [
        {"key": "word:synthetic-energy", "item_kind": "word_question",
         "title_zh": "合成材料甲 · 比较两组实验记录，保留原题身份与来源",
         "source_zh": "自编界面验收材料 · Word来源",
         "source_ref": {"key": "word-part", "revision": "synthetic-r1", "source_sha256": "a" * 64},
         "teacher_metadata": {"points": 4.5, "note": "原教师设置保留"}},
        {"key": "visual:synthetic-energy", "item_kind": "personal_visual_theme",
         "title_zh": "合成材料乙 · 含共有材料和两道小题的完整图片主题",
         "source_zh": "自编界面验收材料 · 图片来源",
         "source_ref": {"scope": "personal", "selections": [{"key": "visual-a", "revision": "synthetic-v1"}]},
         "visual_selections": [{"key": "visual-a", "revision": "synthetic-v1"}]},
        {"key": "core:synthetic-energy", "item_kind": "core_theme", "scope": "supplemental",
         "title_zh": "合成材料丙 · 完整原卷主题及关联小题",
         "source_zh": "自编界面验收材料 · 原卷来源",
         "source_identity_sha256": "b" * 64, "data_snapshot_id": "c" * 64, "atomic_total": 2},
    ]


def settle(app):
    for _ in range(8):
        app.processEvents()


def rect_in(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def footer_visible(dialog):
    assert dialog.rect().contains(rect_in(dialog.status, dialog))
    assert dialog.rect().contains(rect_in(dialog.back, dialog))
    assert rect_in(dialog.status, dialog).bottom() < rect_in(dialog.back, dialog).top()
    label = dialog.status
    required = label.fontMetrics().boundingRect(
        QRect(0, 0, label.contentsRect().width(), 10000),
        Qt.TextFlag.TextWordWrap, label.text(),
    ).height()
    assert label.contentsRect().height() >= required
    assert label.textFormat() == Qt.TextFormat.PlainText
    return {"status_complete": True, "continue_visible": True,
            "status_height": label.height(), "status_text_required_height": required}


def reachable_controls(dialog, app):
    position = dialog.scroll.verticalScrollBar().value()
    names = []
    for name in ("up", "down", "remove", "undo_button", "retry", "edit_button", "preview"):
        button = getattr(dialog, name)
        if button.isVisible() and button.isEnabled():
            dialog.scroll.ensureWidgetVisible(button, 0, 0)
            settle(app)
            assert dialog.scroll.viewport().rect().contains(rect_in(button, dialog.scroll.viewport()))
            names.append(name)
    dialog.scroll.verticalScrollBar().setValue(position)
    settle(app)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be a new empty directory; previous receipts are preserved")
    app = create_application(["basket-undo-qa"])
    manifest = {"schema": "shchem.basket-undo-qa.v1", "synthetic_only": True,
                "personal_state_accessed": False, "provider_calls": 0,
                "qt_version": qVersion(), "images": [], "checks": {}}
    scenarios = [
        ("mixed-top-360x520", 360, 520, "ready", "top"),
        ("removed-bottom-360x520", 360, 520, "remove", "bottom"),
        ("restored-top-360x520", 360, 520, "restore", "top"),
        ("empty-undo-bottom-360x520", 360, 520, "empty", "bottom"),
        ("conflict-bottom-360x520", 360, 520, "conflict", "bottom"),
        ("read-failure-bottom-360x520", 360, 520, "read_failure", "bottom"),
        ("write-failure-bottom-360x520", 360, 520, "write_failure", "bottom"),
        ("moved-bottom-420x520", 420, 520, "move", "bottom"),
        ("restored-bottom-420x520", 420, 520, "restore", "bottom"),
        ("mixed-wide-900x650", 900, 650, "ready", "top"),
    ]
    with tempfile.TemporaryDirectory(prefix="shchem-basket-undo-qa-") as directory:
        for name, width, height, scenario, edge in scenarios:
            store = SyntheticStore(Path(directory) / name)
            original = synthetic_rows()
            store.add_many_to_basket(original)
            store.save_draft("independent-paper", {"section_order": ["draft-own-order"], "teacher_note": "独立草稿"})
            original_drafts = deepcopy(store.snapshot()["drafts"])
            facade = SimpleNamespace(_state=store)
            facade.open_basket_session = lambda: DesktopWorkbenchFacade.open_basket_session(facade)
            dialog = ExplorerBasketDialog(facade)
            dialog.resize(width, height)
            dialog.show()
            settle(app)
            events = []
            dialog.basket_changed.connect(events.append)
            if scenario in {"remove", "restore", "conflict"}:
                dialog.list.setCurrentRow(1)
                dialog.remove.click()
                assert len(store.basket()) == 2
            if scenario == "restore":
                dialog.undo_button.click()
                assert store.basket() == original and dialog.list.currentRow() == 1
                assert events == [2, 3]
            elif scenario == "empty":
                while dialog.list.count():
                    dialog.remove.click()
                assert store.basket() == [] and dialog.undo_button.isEnabled()
            elif scenario == "move":
                dialog.down.click()
                assert store.basket()[1]["key"] == original[0]["key"]
            elif scenario == "conflict":
                other = DesktopStateStore(store.root)
                other.add_to_basket({"key": "external:synthetic", "title_zh": "另一窗口加入的合成材料", "source_zh": "合成来源"})
                frozen = store.path.read_bytes()
                dialog.undo_button.click()
                assert store.path.read_bytes() == frozen and events == [2]
                assert not dialog.undo_button.isEnabled()
            elif scenario == "read_failure":
                store.read_error = True
                dialog.refresh()
                assert dialog.list.count() == 3 and not dialog.remove.isEnabled()
            elif scenario == "write_failure":
                frozen = store.path.read_bytes()
                store.write_error = True
                dialog.remove.click()
                assert store.path.read_bytes() == frozen and not events
            settle(app)
            assert dialog.width() == width and dialog.height() == height
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
            controls = reachable_controls(dialog, app)
            scrollbar = dialog.scroll.verticalScrollBar()
            scrollbar.setValue(0 if edge == "top" else scrollbar.maximum())
            settle(app)
            checks = footer_visible(dialog)
            image = dialog.grab()
            ratio = dialog.devicePixelRatioF()
            assert image.width() == round(width * ratio) and image.height() == round(height * ratio)
            destination = output / f"{name}.png"
            assert image.save(str(destination))
            manifest["images"].append({
                "file": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "window": [width, height], "pixels": [image.width(), image.height()],
                "device_pixel_ratio": ratio, "scenario": scenario, "scroll": edge,
                "status": dialog.status.text(), "history": dialog.history_status.text(),
                "reachable_controls": controls, "checks": checks,
            })
            store.read_error = False
            assert store.snapshot()["drafts"] == original_drafts
            dialog.close()
            assert dialog._session.undo_count == 0
            dialog.deleteLater()
            settle(app)
    manifest["checks"] = {"exact_restoration": True, "selection_restored": True,
        "conflict_not_overwritten": True, "read_error_not_empty": True,
        "write_error_not_success": True, "drafts_untouched": True,
        "history_discarded_on_close": True, "short_windows_actual_size": True,
        "footer_and_controls_verified": True}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"images": len(manifest["images"]), "checks": manifest["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
