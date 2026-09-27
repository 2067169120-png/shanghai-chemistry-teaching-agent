"""Offline draft undo acceptance with a temporary store and real styled Qt.

Run with QT_SCALE_FACTOR=2 and --output <new-empty-directory>. All sources and
content are synthetic. The manifest only contains relative screenshot names.
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

from PySide6.QtCore import QPoint, QRect, Qt, qVersion
from PySide6.QtWidgets import QDialog, QVBoxLayout
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID, MixedPaperDraftSession
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel


class SyntheticStore(DesktopStateStore):
    read_error = False
    write_error = False

    def _read_unlocked(self):
        if self.read_error:
            raise OSError("synthetic private read failure")
        return super()._read_unlocked()

    def _write_unlocked(self, value, **kwargs):
        if self.write_error:
            raise OSError("synthetic private write failure")
        return super()._write_unlocked(value, **kwargs)


class Tasks:
    def __init__(self):
        self.pending = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.pending.append((operation, on_success, on_failure))

    def flush(self):
        while self.pending:
            operation, success, failure = self.pending.pop(0)
            try:
                result = operation()
            except Exception:
                if failure:
                    failure("合成来源读取失败")
            else:
                if success:
                    success(result)


def synthetic_rows():
    return [
        {"key": "core-synthetic", "kind": "core_theme", "title_zh": "合成主题甲 · 共同材料与关联小题整体保留",
         "source_zh": "自编界面验收材料 · 原卷主题", "source_ref": {"theme_id": "core-a", "revision": "core-r1"},
         "content": {}, "settings": {"score_per_atomic": 2, "answer_space_lines": 0, "atomic_settings": {}}},
        {"key": "word-synthetic", "kind": "word_question", "title_zh": "合成完整题乙 · 保留文字、公式与原有答题区",
         "source_zh": "自编界面验收材料 · Word 来源", "source_ref": {"key": "word-a", "revision": "word-r1"},
         "content": {}, "settings": {"points": 4.5}},
        {"key": "visual-synthetic", "kind": "personal_visual_theme", "title_zh": "合成图片主题丙 · 原题图片与答案整体保留",
         "source_zh": "自编界面验收材料 · 图片来源", "source_ref": {"selections": [{"key": "visual-a", "revision": "v1"}]},
         "content": {}, "settings": {"use_source_scores": True}},
    ]


class SyntheticFacade:
    def __init__(self, root):
        self.state_store = SyntheticStore(root)
        self.rows = synthetic_rows()
        self.state_store.add_many_to_basket(self.rows)
        self.state_store.save_draft("other-paper", {"teacher_note": "另一份合成草稿，必须保留"})

    def open_mixed_paper_draft_session(self):
        return MixedPaperDraftSession(self.state_store)

    def paper_basket_projection(self):
        basket = self.state_store.basket_snapshot()
        known = {row["key"]: row for row in self.rows}
        return {"schema_version": "shchem.desktop-mixed-basket.v1", "basket_sha256": basket.content_sha256,
                "items": [deepcopy(known[row["key"]]) for row in basket.rows], "warnings": []}


def settle(app):
    for _ in range(8):
        app.processEvents()


def rect_in(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def footer_checks(panel):
    previous = -1
    for name in ("status", "undo_button", "back_button"):
        widget = getattr(panel, name)
        rect = rect_in(widget, panel)
        assert panel.rect().contains(rect), (name, rect, panel.size())
        assert rect.top() > previous
        previous = rect.bottom()
    label = panel.status
    required = label.fontMetrics().boundingRect(QRect(0, 0, label.contentsRect().width(), 10000),
        Qt.TextFlag.TextWordWrap, label.text()).height()
    assert label.contentsRect().height() >= required, (label.height(), required, label.text())
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert panel.scroll.horizontalScrollBar().maximum() == 0
    return {"status_complete": True, "undo_visible": True, "return_visible": True,
            "status_required_height": required, "status_height": label.height()}


def reachable_controls(panel, app):
    bar = panel.scroll.verticalScrollBar()
    before = bar.value()
    reached = []
    for name in ("up_button", "down_button", "remove_button", "restore_button", "points", "space",
                 "preview_button", "export_button", "reload_button", "restart_button"):
        widget = getattr(panel, name)
        if not widget.isVisible() or not widget.isEnabled():
            continue
        # Spin boxes delegate focus to their line editor. A full-control
        # margin also brings the surrounding arrows/frame into the viewport.
        panel.scroll.ensureWidgetVisible(widget, 0, widget.height())
        settle(app)
        assert panel.scroll.viewport().rect().contains(rect_in(widget, panel.scroll.viewport())), name
        reached.append(name)
    bar.setValue(before)
    settle(app)
    return reached


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be empty; previous evidence is preserved")
    app = create_application(["mixed-paper-draft-undo-qa"])
    manifest = {"schema": "shchem.mixed-paper-draft-undo-qa.v1", "synthetic_only": True,
                "personal_state_accessed": False, "provider_calls": 0, "qt_version": qVersion(), "images": []}
    scenarios = [
        ("ready-top-360x520", 360, 520, "ready", "top"),
        ("removed-list-360x520", 360, 520, "remove", "list"),
        ("undo-restored-list-360x520", 360, 520, "undo", "list"),
        ("empty-bottom-360x520", 360, 520, "empty", "bottom"),
        ("score-layout-bottom-420x520", 420, 520, "settings", "bottom"),
        ("conflict-bottom-360x520", 360, 520, "conflict", "bottom"),
        ("source-restart-bottom-360x520", 360, 520, "recovery", "bottom"),
        ("restarted-bottom-420x520", 420, 520, "restart", "bottom"),
        ("write-failure-bottom-360x520", 360, 520, "write_failure", "bottom"),
        ("ready-wide-900x650", 900, 650, "ready", "top"),
    ]
    with tempfile.TemporaryDirectory(prefix="mixed-draft-undo-qa-") as directory:
        for name, width, height, scenario, edge in scenarios:
            facade, tasks = SyntheticFacade(Path(directory) / name), Tasks()
            store = facade.state_store
            basket = store.basket_snapshot()
            other = store.draft_snapshot("other-paper")
            window = QDialog()
            window.setWindowTitle("当前卷 · 合成验收")
            layout = QVBoxLayout(window)
            layout.setContentsMargins(0, 0, 0, 0)
            panel = MixedPaperPanel(facade, tasks, PaperComposerModel(), window)
            layout.addWidget(panel)
            window.resize(width, height)
            panel.load()
            tasks.flush()
            window.show()
            settle(app)
            baseline = store.draft_snapshot(DRAFT_ID).record
            if scenario in {"remove", "undo", "conflict", "write_failure"}:
                panel.sections.setCurrentRow(1)
                if scenario == "write_failure":
                    store.write_error = True
                panel.remove_button.click()
            if scenario == "undo":
                panel.undo_button.click()
                assert store.draft_snapshot(DRAFT_ID).record == baseline
                assert panel._current_key() == "word-synthetic"
            elif scenario == "empty":
                while panel.model.order:
                    panel.remove_button.click()
                assert panel.undo_button.isEnabled() and not panel.preview_button.isEnabled()
            elif scenario == "settings":
                panel.sections.setCurrentRow(0)
                panel.points.setValue(6)
                panel.space.setValue(3)
                panel.show_scores.setChecked(True)
                assert panel._draft_session.undo_count == 3
            elif scenario == "conflict":
                external = DesktopStateStore(store.root)
                record = external.draft_snapshot(DRAFT_ID).record
                record["payload"]["settings_ui"]["title"] = "另一窗口的合成编辑"
                external.save_draft(DRAFT_ID, record)
                before = store.path.read_bytes()
                panel.undo_button.click()
                assert store.path.read_bytes() == before and panel._restore_failed
            elif scenario in {"recovery", "restart"}:
                facade.rows[0]["source_ref"]["revision"] = "core-r2"
                before = store.path.read_bytes()
                panel.load()
                tasks.flush()
                assert panel.restart_button.isEnabled() and store.path.read_bytes() == before
                if scenario == "restart":
                    # The state transition is real; modal answer is controlled
                    # here so this offline screenshot harness cannot hang CI.
                    panel._confirm_restart = lambda: True
                    panel.restart_button.click()
                    tasks.flush()
                    assert not panel._restore_failed and not panel._draft_session.undo_count
                    assert any(key.startswith("paper-mixed-before-reset-") for key in store.snapshot()["drafts"])
            settle(app)
            assert panel.size().width() == width and panel.size().height() == height
            checks = footer_checks(panel)
            checks["reachable_controls"] = reachable_controls(panel, app)
            if edge == "list":
                panel.scroll.ensureWidgetVisible(panel.remove_button, 0, 0)
            else:
                panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum() if edge == "bottom" else 0)
            settle(app)
            image = window.grab()
            filename = name + ".png"
            assert image.save(str(output / filename), "PNG")
            assert store.basket_snapshot() == basket and store.draft_snapshot("other-paper") == other
            manifest["images"].append({"file": filename, "scenario": scenario, "edge": edge,
                "logical_size": [width, height], "pixel_size": [image.width(), image.height()],
                "device_pixel_ratio": image.devicePixelRatio(), "sha256": hashlib.sha256((output / filename).read_bytes()).hexdigest(),
                "status": panel.status.text(), "checks": checks})
            panel.close()
            window.close()
            window.deleteLater()
            settle(app)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"images": len(manifest["images"]), "all_footer_and_reachability_checks": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
