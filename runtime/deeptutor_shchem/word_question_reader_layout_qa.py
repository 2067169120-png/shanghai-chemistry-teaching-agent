"""Synthetic reader layout acceptance; memory-only services and generated images."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QLabel


class QueuedTasks(QObject):
    task_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.pending, self.cancelled, self.serial = [], set(), 0

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        identifier = str(self.serial)
        self.pending.append((identifier, label, operation, on_success, on_failure))
        return identifier

    def cancel(self, identifier):
        self.cancelled.add(identifier)

    def finish(self, label=None, *, allow_cancelled=False):
        index = next(i for i, job in enumerate(self.pending) if label is None or job[1] == label)
        identifier, _label, operation, success, failure = self.pending.pop(index)
        if allow_cancelled or identifier not in self.cancelled:
            try:
                value = operation()
            except Exception as exc:
                if failure is None:
                    raise
                failure(str(exc))
            else:
                if success:
                    success(value)
        self.task_finished.emit(identifier)

    def flush(self):
        count = 0
        while self.pending:
            self.finish()
            count += 1
            assert count < 80


def synthetic_question(key, source):
    return {
        "key": key, "revision": "revision-" + key, "source_id": source,
        "source_sha256": ("a" if source == "A" else "b") * 64,
        "batch_id": "synthetic-" + source,
        "source_name": "合成实验讲义.docx" if source == "A" else "另一份合成讲义.docx",
        "source_label": "合成来源甲" if source == "A" else "合成来源乙",
        "title": "合成材料 · 观察实验记录", "chapter": "界面验证材料",
        "question_blocks": [{"index": 2, "text": (
            "【合成界面材料，不是真实试题】\n"
            "比较甲、乙两组操作记录，指出需要保持一致的条件。\n"
            "甲组：完整记录所用材料、操作顺序与观察现象。\n"
            "乙组：逐项核对条件，再比较两次观察结果。\n"
            "作答时引用原记录；信息缺失处应说明待核对。\n"
            "长题面末尾：本句和下方示意图均需可以滚动阅读。"
        ), "warnings": [], "assets": [{"asset_id": key + "-image", "preview_supported": True,
            "mime_type": "image/png", "label": "合成记录示意图"}]}],
        "answer_blocks": [{"index": 3, "text": "合成参考文字：按记录核对条件与现象；本材料不代表化学审核结论。",
                           "warnings": [], "assets": []}],
        "context_blocks": [{"index": 1, "text": "共同材料：两组记录来自同一合成情境，应保留共有条件。",
                            "warnings": [], "assets": []}],
        "warnings": [], "selection_ready": True, "export_ready": True,
        "boundary_status": "verified_candidate", "block_start": 2, "question_end": 2,
        "answer_start": 3, "block_end": 3, "context_start": 1, "context_end": 1,
        "source_block_count": 3,
    }


class ReaderFixture:
    """No personal paths, stores, providers or source-document readers."""

    def __init__(self, *, saved=None, error=None):
        self.catalog = {"revision": "synthetic-r1", "warnings": [],
            "items": [synthetic_question("Q1", "A"), synthetic_question("Q2", "B")],
            "sources": [{"source_id": "A", "source_name": "合成实验讲义.docx"},
                        {"source_id": "B", "source_name": "另一份合成讲义.docx"}]}
        self.saved, self.calls, self.error = deepcopy(saved or []), [], error

    def word_question_catalog(self):
        self.calls.append(("catalog",))
        if self.error:
            raise RuntimeError(self.error)
        return deepcopy(self.catalog)

    def word_question_saved_selection(self):
        return deepcopy(self.saved)

    def word_question_save_selection(self, selections):
        self.calls.append(("save", deepcopy(selections)))
        self.saved = deepcopy(selections)

    def word_question_image(self, key, revision, asset_id):
        self.calls.append(("image", key, revision, asset_id))
        value = QImage(480, 180, QImage.Format.Format_RGB32)
        value.fill(QColor("#eef4ee"))
        painter = QPainter(value)
        painter.setPen(QColor("#24594f"))
        painter.drawRect(24, 24, 190, 112)
        painter.drawRect(266, 24, 190, 112)
        painter.drawText(QRect(24, 24, 190, 112), Qt.AlignmentFlag.AlignCenter, "合成记录甲")
        painter.drawText(QRect(266, 24, 190, 112), Qt.AlignmentFlag.AlignCenter, "合成记录乙")
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert value.save(buffer, "PNG")
        return {"bytes": bytes(data), "mime_type": "image/png", "label": "合成示意图"}

    def word_question_reference(self, selections, *, include_images=True):
        self.calls.append(("reference", deepcopy(selections), include_images))
        return {"materials": "合成完整选题参考：\n" + "\n".join(
            row["question_blocks"][0]["text"] + "\n" + row["answer_blocks"][0]["text"]
            for row in self.catalog["items"] if row["key"] in {s["key"] for s in selections}),
            "selections": deepcopy(selections), "warnings": ["合成界面验证材料"],
            "include_images": include_images, "image_assets": [], "image_issues": []}

    def add_word_questions_to_basket(self, selections):
        self.calls.append(("basket", deepcopy(selections)))
        return len(selections)

    # These entries make real controls visible; they never open a production service.
    def word_question_source(self, *_args):
        raise RuntimeError("合成布局服务不修改题目范围。")

    def word_question_update_range(self, *_args, **_kwargs):
        raise RuntimeError("合成布局服务不修改题目范围。")

    def word_question_attribute_options(self, *_args):
        raise RuntimeError("合成布局服务不修改教学标签。")

    def word_question_save_attributes(self, *_args, **_kwargs):
        raise RuntimeError("合成布局服务不修改教学标签。")

    def word_semantic_tag_preview(self, *_args, **_kwargs):
        raise RuntimeError("合成布局服务不调用模型。")


def settle(app, rounds=25):
    for _ in range(rounds):
        app.processEvents()


def fully_visible(widget, host):
    rect = QRect(widget.mapTo(host, widget.rect().topLeft()), widget.size())
    return host.rect().contains(rect) and widget.visibleRegion().contains(widget.rect())


def footer_evidence(dialog):
    label = dialog.status
    area = label.contentsRect().adjusted(label.margin(), label.margin(), -label.margin(), -label.margin())
    needed = label.fontMetrics().boundingRect(
        QRect(0, 0, area.width(), 10000), int(Qt.TextFlag.TextWordWrap), label.text()
    ).height()
    return {"status_complete": fully_visible(label, dialog), "close_complete": fully_visible(dialog.close_button, dialog),
        "status_text": label.text(), "status_content_height": area.height(), "status_needed_height": needed,
        "status_unclipped": area.height() >= needed, "footer_height": dialog.reader_footer.height(),
        "body_height": dialog.body_scroll.viewport().height(), "tabs_minimum_height": dialog.tabs.minimumHeight(),
        "horizontal_overflow": dialog.body_scroll.horizontalScrollBar().maximum()}


def reveal(dialog, widget, app):
    # Let expansion/layout finish before the next synthetic scroll gesture.
    settle(app)
    dialog.body_scroll.ensureWidgetVisible(widget, 0, 8)
    settle(app)
    assert fully_visible(widget, dialog.body_scroll.viewport()), widget.objectName() or type(widget).__name__


def prepare(mode, width, height, app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
    fixture, tasks = ReaderFixture(), QueuedTasks()
    required = "MISSING" if mode == "source_empty" else "A" if mode == "search_empty" else None
    if mode == "error":
        fixture.error = "来源记录暂时无法读取，请保留现有选题并稍后刷新。若仍失败，请返回导入窗口核对原批次；本次没有保存新的题目或标签。"
    dialog = WordQuestionDialog(fixture, tasks, required_source_id=required, initial_source_id=required)
    dialog.resize(width, height)
    dialog.show()
    if mode != "loading":
        tasks.flush()
    if mode == "search_empty":
        dialog.search.setText("不存在的合成条件")
        dialog._filter_items()
    settle(app)
    if mode == "settings":
        dialog.question_tools_button.click()
    if mode in {"preparation", "basket"}:
        dialog.select_current_button.click()
        dialog._persist_selection()
        tasks.flush()
        if mode == "preparation":
            dialog.preparation_toggle.click()
            dialog.include_images.setChecked(False)
            dialog.preview_button.click()
            tasks.flush()
    if mode == "busy":
        dialog._attributes_busy = True
        dialog._update_actions()
        dialog.close_button.click()
    settle(app)
    return dialog, fixture, tasks


def dispose(dialog, tasks, app):
    dialog._attributes_busy = False  # Only the synthetic busy screenshot sets this flag.
    dialog.close()
    tasks.flush()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    app = create_application(["word-question-reader-layout-qa"])
    scenarios = [(mode, 360, 520) for mode in
                 ("source_empty", "search_empty", "ready", "settings", "preparation", "basket", "loading", "error", "busy")]
    scenarios += [(mode, 420, 520) for mode in ("source_empty", "ready", "settings", "preparation")]
    scenarios += [(mode, 900, 760) for mode in ("source_empty", "ready")]
    if os.environ.get("QT_SCALE_FACTOR") == "1":
        scenarios = [("ready", 1200, 820)]
    captures = []
    for mode, width, height in scenarios:
        dialog, fixture, tasks = prepare(mode, width, height, app)
        assert (dialog.width(), dialog.height()) == (width, height)
        positions = ("top", "bottom") if width == 360 and mode in {"source_empty", "ready", "preparation", "error"} else ("whole",)
        if mode == "ready" and width in {360, 420}:
            positions += ("reading-top", "reading-bottom")
        if mode == "settings":
            positions += ("controls",)
        for position in positions:
            if position in {"top", "bottom"}:
                bar = dialog.body_scroll.verticalScrollBar()
                bar.setValue(bar.maximum() if position == "bottom" else 0)
            elif position.startswith("reading-"):
                reveal(dialog, dialog.tabs, app)
                bar = dialog.tabs.widget(0).verticalScrollBar()
                bar.setValue(bar.maximum() if position == "reading-bottom" else 0)
            elif mode == "settings" and position == "controls":
                reveal(dialog, dialog.question_tools, app)
            elif mode == "settings":
                reveal(dialog, dialog.question_tools_button, app)
            elif mode == "preparation":
                reveal(dialog, dialog.import_button, app)
            elif mode == "basket":
                reveal(dialog, dialog.basket_preview_button, app)
            settle(app)
            evidence = footer_evidence(dialog)
            evidence["visible_actions"] = [button.text() for button in (
                dialog.range_button, dialog.attributes_button, dialog.ai_attributes_button,
                dialog.basket_preview_button, dialog.basket_add_button, dialog.preview_button, dialog.import_button,
            ) if fully_visible(button, dialog.body_scroll.viewport())]
            assert evidence["status_complete"] and evidence["close_complete"] and evidence["status_unclipped"], evidence
            assert evidence["horizontal_overflow"] == 0, evidence
            path = output / f"reader-{mode}-{width}x{height}-{position}.png"
            pixmap = dialog.grab()
            assert pixmap.save(str(path))
            ratio = dialog.devicePixelRatioF()
            assert (pixmap.width(), pixmap.height()) == (round(width * ratio), round(height * ratio))
            captures.append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": mode, "width": width, "height": height, "device_pixel_ratio": dialog.devicePixelRatioF(),
                "pixel_width": pixmap.width(), "pixel_height": pixmap.height(), "evidence": evidence})
        dispose(dialog, tasks, app)
    manifest = {"scope": "memory-only synthetic service, generated image and real application style",
                "personal_state_accessed": False, "provider_calls": 0, "captures": captures}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captures": len(captures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
