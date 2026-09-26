"""Capture native Word selection UI using synthetic data; never start Word."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SyntheticTasks:
    """Keep every completion explicit, including a visible pending state."""

    def __init__(self):
        self.jobs = []
        self.cancelled = []

    def submit(self, _label, operation, *, on_success, on_failure):
        self.jobs.append((operation, on_success, on_failure))
        return len(self.jobs) - 1

    def complete(self, index):
        operation, success, _failure = self.jobs[index]
        success(operation())

    def fail(self, index, message):
        self.jobs[index][2](message)

    def cancel(self, task_id):
        self.cancelled.append(task_id)


def synthetic_payload():
    locations = []
    for index, kind, label, position, text in (
        (1, "image", "合成图片 · 正文首次出现", "正文第 2 段 · 图片 1",
         "原创合成段落：方框沿箭头连接圆形。\n仅检查所选对象、附近文字与原图是否一致，不含真实题目。"),
        (2, "omml", "合成原生公式 · 正文段落", "正文第 3 段 · 公式 1",
         "原创合成段落：甲项加乙项得到总项。\n此处用于检查无独立图片的原生公式定位；表格公式尚不支持自动选中。"),
        (3, "ole_object", "合成旧式对象 · 仅结构定位", "正文第 4 段 · 旧式对象 1",
         "原创合成段落：旧式嵌入对象保留具体位置。\n本例不支持直接在 Word 中选中，可复制附近文字核对。"),
    ):
        locations.append({
            "location_id": f"synthetic-only-{index}", "block_index": index,
            "kind": kind, "label": label, "position_text": position,
            "context_text": text, "source_states": [], "notices": [],
            "xml_locator": f"word/document.xml#/w:document/w:body/w:p[{index}]/*[1]",
            "assets": [{"asset_id": "synthetic-original-image", "label": "原创几何样例",
                        "mime_type": "image/png", "preview_supported": True}]
            if kind == "image" else [],
        })
    return {
        "source_name": "原创合成对象定位示例.docx", "source_sha256": "a" * 64,
        "source_revision": "synthetic-only-revision", "warnings": [],
        "blocks": [{"block_index": loc["block_index"], "locations": [loc]} for loc in locations],
    }


def synthetic_image():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    image = QImage(680, 230, QImage.Format.Format_ARGB32)
    image.fill(QColor("#fffdf4"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#487760"), 4))
    painter.setBrush(QColor("#dce9d5"))
    painter.drawRoundedRect(35, 66, 145, 100, 16, 16)
    painter.drawEllipse(475, 66, 125, 100)
    painter.drawLine(210, 116, 445, 116)
    painter.drawLine(423, 99, 445, 116)
    painter.drawLine(423, 133, 445, 116)
    painter.setPen(QColor("#284b3a"))
    painter.drawText(35, 38, "ORIGINAL SYNTHETIC DRAWING")
    painter.drawText(35, 207, "QA ONLY - NO SOURCE DOCUMENT OR EXAM CONTENT")
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode synthetic image")
    return bytes(data)


def capture(output):
    output = Path(output).resolve()
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton

    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_source_location_dialog import WordSourceLocationDialog

    app = QApplication.instance() or QApplication([])
    install_ui_font(app)
    output.mkdir(parents=True, exist_ok=True)
    raw = synthetic_image()
    screenshots = []
    native_calls = []

    def select_native(location_id, cancelled):
        native_calls.append({"location_id": location_id, "cancelled": cancelled.is_set()})
        return {"status": "selected", "selection_verified": True, "source_unchanged": True}

    def make(width, height):
        tasks = SyntheticTasks()
        dialog = WordSourceLocationDialog(
            lambda: deepcopy(synthetic_payload()), lambda *_args: {"bytes": raw},
            tasks=tasks, select_native=select_native,
        )
        dialog.resize(width, height)
        dialog.show()
        app.processEvents()
        tasks.complete(0)
        tasks.complete(1)
        QTest.qWait(30)
        return dialog, tasks

    def save(dialog, name, expected_size):
        QTest.qWait(30)
        original_scroll = dialog.detail_scroll.verticalScrollBar().value()
        assert (dialog.width(), dialog.height()) == expected_size
        assert dialog.detail_scroll.horizontalScrollBar().maximum() == 0
        assert dialog.location_list.viewport().height() >= 30
        for button in (dialog.copy_button, next(
            b for b in dialog.findChildren(QPushButton) if b.text() == "完成核对"
        )):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
            assert not button.visibleRegion().isEmpty()
        for button in (dialog.native_button, dialog.cancel_native_button):
            if not button.isVisible():
                continue
            dialog.detail_scroll.ensureWidgetVisible(button)
            app.processEvents()
            point = button.mapTo(dialog.detail_scroll.viewport(), QPoint(0, 0))
            assert dialog.detail_scroll.viewport().rect().contains(
                point + QPoint(button.width() // 2, button.height() // 2)
            )
        dialog.detail_scroll.verticalScrollBar().setValue(original_scroll)
        app.processEvents()
        path = output / name
        pixmap = dialog.grab()
        if not pixmap.save(str(path), "PNG"):
            raise RuntimeError("Could not save synthetic UI capture")
        screenshots.append({
            "path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": pixmap.width(), "height": pixmap.height(),
            "native_status": dialog.native_status.text(),
            "native_enabled": dialog.native_button.isEnabled(),
            "cancel_visible": dialog.cancel_native_button.isVisible(),
            "list_enabled": dialog.location_list.isEnabled(), "search_enabled": dialog.search.isEnabled(),
            "vertical_layout": dialog.splitter.orientation() == Qt.Orientation.Vertical,
            "footer_reachable": True, "native_controls_scroll_reachable": True,
        })

    def close(dialog):
        dialog.close()
        dialog.deleteLater()
        app.processEvents()

    dialog, tasks = make(1020, 790)
    save(dialog, "native-word-desktop-ready.png", (1020, 790))
    dialog.native_button.click()
    tasks.complete(len(tasks.jobs) - 1)
    save(dialog, "native-word-desktop-selected.png", (1020, 790))
    close(dialog)

    dialog, tasks = make(420, 790)
    dialog.native_button.click()
    save(dialog, "native-word-narrow-pending.png", (420, 790))
    tasks.fail(len(tasks.jobs) - 1, "合成失败：无法唯一确认对象位置，请在原文中核对。")
    save(dialog, "native-word-narrow-failure.png", (420, 790))
    dialog.location_list.setCurrentRow(2)
    save(dialog, "native-word-narrow-unsupported.png", (420, 790))
    close(dialog)

    dialog, tasks = make(420, 580)
    dialog.native_button.click()
    save(dialog, "native-word-minimum-pending.png", (420, 580))
    dialog.cancel_native_button.click()
    save(dialog, "native-word-minimum-cancelled.png", (420, 580))
    close(dialog)
    report = {
        "synthetic_only": True, "source_documents_read": 0, "word_started": False,
        "network_calls": 0, "native_callback_calls": native_calls, "screenshots": screenshots,
    }
    (output / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"captures": len(screenshots), "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/qa/screenshots/2026-09-27-native-word")
    capture(parser.parse_args().output)
