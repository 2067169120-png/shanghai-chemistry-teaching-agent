"""Capture a synthetic personal visual-question dialog without private state.

The fixture uses only local Qt-generated PNG bytes.  It never constructs the
real facade, reads a source document or provider setting, or calls a network
service.  Existing screenshots are intentionally never overwritten.
"""

from __future__ import annotations

import argparse
import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def _png_bytes(title: str, subtitle: str, background: str) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    image = QImage(760, 250, QImage.Format.Format_RGB32)
    image.fill(QColor(background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#1e4d55"), 2))
    painter.drawRoundedRect(QRectF(10, 10, 740, 230), 16, 16)
    painter.setPen(QColor("#173e4e"))
    painter.setFont(QFont("Microsoft YaHei UI", 24, QFont.Weight.Bold))
    painter.drawText(QRectF(34, 34, 692, 56), Qt.AlignmentFlag.AlignLeft, title)
    painter.setPen(QColor("#35666f"))
    painter.setFont(QFont("Microsoft YaHei UI", 17))
    painter.drawText(QRectF(34, 98, 692, 46), Qt.AlignmentFlag.AlignLeft, subtitle)
    painter.setPen(QPen(QColor("#8ab7bd"), 2))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(34, 164, 692, 52), 10, 10)
    painter.setPen(QColor("#35666f"))
    painter.setFont(QFont("Microsoft YaHei UI", 15))
    painter.drawText(
        QRectF(52, 175, 656, 30),
        Qt.AlignmentFlag.AlignLeft,
        "合成测试图 · 只用于检查图片读取和界面布局",
    )
    painter.end()
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


class _ImmediateTasks:
    def __init__(self) -> None:
        self.serial = 0

    def submit(self, _label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        task_id = f"synthetic-task-{self.serial}"
        try:
            value = operation()
        except Exception as exc:  # noqa: BLE001 - fixture must surface UI errors
            if on_failure is not None:
                on_failure(str(exc))
        else:
            if on_success is not None:
                on_success(value)
        return task_id

    def cancel(self, _task_id: str) -> None:
        return None


class _SyntheticFacade:
    def __init__(self) -> None:
        self.image_calls: list[tuple[str, bool]] = []
        self._row = {
            "batch_id": "synthetic-batch",
            "key": "synthetic-question",
            "revision": "synthetic-revision",
            "title": "合成图文题 · 图片核对示例",
            "theme_key": "synthetic-theme",
            "theme_title": "合成主题 · 完整题面与公共材料",
            "source_name": "合成测试资料（非真实来源）",
            "question_number": "示例题",
            "question_text": (
                "合成测试题干：请结合右侧公共材料，核对题面信息。\n"
                "(1) 读取题面中的第一个设问并填写观察记录。\n"
                "(2) 根据题面给出的条件整理第二个设问。"
            ),
            "shared_text": (
                "共同材料示例：这里展示一段供教师核对的合成文字，"
                "用于检查题面、公共材料和图片是否分别可见。"
            ),
            "answer_text": "答案默认隐藏（合成测试，不作真实化学答案断言）。",
            "warnings": ["合成测试数据；不代表教材或正式题库。"],
            "images": [
                {
                    "image_id": "synthetic-question-image",
                    "role": "question",
                    "caption": "题面图片 · 合成测试图",
                    "width": 760,
                    "height": 250,
                    "page_number": 1,
                },
                {
                    "image_id": "synthetic-shared-image",
                    "role": "shared_material",
                    "caption": "公共材料图片 · 合成测试图",
                    "width": 760,
                    "height": 250,
                    "page_number": 1,
                },
                {
                    "image_id": "synthetic-answer-image",
                    "role": "answer",
                    "caption": "答案图片 · 默认不加载",
                    "width": 760,
                    "height": 250,
                    "page_number": 2,
                },
            ],
            "facets": {
                "source": ["synthetic-source"],
                "book": ["synthetic-book"],
                "knowledge": ["synthetic-knowledge"],
            },
            "selection_ready": True,
        }
        self._question_png = _png_bytes(
            "题面图片 · 合成测试图",
            "(1)(2) 小问的图片关系示例",
            "#eef7f7",
        )
        self._shared_png = _png_bytes(
            "共同材料图片 · 合成测试图",
            "共享材料示例：本图不代表教材原图",
            "#f1f5fc",
        )
        self._answer_png = _png_bytes(
            "答案图片 · 合成测试图",
            "默认不加载，切换答案页后才读取",
            "#f8effd",
        )

    def personal_visual_questions(self, batch_id=None):
        if batch_id is not None and batch_id != self._row["batch_id"]:
            rows = []
        else:
            rows = [deepcopy(self._row)]
        return {
            "items": rows,
            "warnings": [],
            "filter_options": {
                "book": [{"value": "synthetic-book", "label": "合成教材册"}],
                "knowledge": [{"value": "synthetic-knowledge", "label": "合成知识标签"}],
                "source": [{"value": "synthetic-source", "label": "合成测试来源"}],
            },
            "selection": [],
        }

    def personal_visual_question_detail(self, batch_id, key, revision):
        expected = (self._row["batch_id"], self._row["key"], self._row["revision"])
        if (batch_id, key, revision) != expected:
            raise KeyError("synthetic question not found")
        return deepcopy(self._row)

    def personal_visual_question_image(self, batch_id, key, revision, image_id, *, original=False):
        expected = (self._row["batch_id"], self._row["key"], self._row["revision"])
        if (batch_id, key, revision) != expected:
            raise KeyError("synthetic question not found")
        self.image_calls.append((image_id, original))
        if image_id == "synthetic-question-image":
            raw = self._question_png
        elif image_id == "synthetic-shared-image":
            raw = self._shared_png
        elif image_id == "synthetic-answer-image":
            raw = self._answer_png
        else:
            raise KeyError(image_id)
        return {"bytes": raw, "caption": image_id + (" · 整页" if original else "")}

    def save_personal_visual_selection(self, _selections):
        return None

    def personal_visual_question_reference(self, _selections):
        return {
            "materials": "合成测试备课材料，不代表真实来源。",
            "image_count": 2,
            "warnings": ["合成图片仅用于界面检查。"],
        }


def _capture(dialog, app, path: Path, width: int, height: int) -> None:
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite existing screenshot: {path}")
    dialog.resize(width, height)
    dialog.show()
    app.processEvents()
    pixmap = dialog.grab()
    if pixmap.isNull() or pixmap.width() < width or pixmap.height() < height:
        raise RuntimeError(f"Unexpected offscreen capture size: {pixmap.size()}")
    if dialog.cancel_button.geometry().bottom() > dialog.height():
        raise AssertionError("bottom action row is clipped")
    if not dialog.question_list.count():
        raise AssertionError("synthetic question did not render")
    assert dialog.tabs.currentIndex() in (0, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path)):
        raise RuntimeError(f"Could not save screenshot: {path}")


def capture(output: Path, small_output: Path) -> None:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QPlainTextEdit

    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
        PersonalVisualQuestionDialog,
    )

    app = create_application([])
    font_family = install_font_fallbacks()
    facade = _SyntheticFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks(), batch_id="synthetic-batch")
    token = ("synthetic-batch", "synthetic-question", "synthetic-revision")
    question_preview = dialog._image_previews[(token, "synthetic-question-image", "question")]
    assert question_preview.has_image
    assert dialog._image_previews[(token, "synthetic-shared-image", "shared_material")].has_image
    assert not any(image_id.endswith("answer-image") for image_id, _original in facade.image_calls)
    question_text = next(
        editor.toPlainText()
        for editor in dialog.findChildren(QPlainTextEdit)
        if "合成测试题干" in editor.toPlainText()
    )
    assert "合成测试题干" in question_text and "(1)" in question_text and "(2)" in question_text
    assert dialog.tabs.currentIndex() == 0

    _capture(dialog, app, output, 1200, 850)

    dialog.tabs.setCurrentIndex(1)
    app.processEvents()
    assert any(
        "共同材料示例" in editor.toPlainText()
        for editor in dialog.findChildren(QPlainTextEdit)
    )
    assert not any(image_id.endswith("answer-image") for image_id, _original in facade.image_calls)
    _capture(dialog, app, small_output, 900, 700)

    dialog.reject()
    QCoreApplication.processEvents()
    print(f"Synthetic personal visual-question capture complete; font={font_family}")
    print(output)
    print(small_output)


def main() -> None:
    default = ROOT / "runtime/deeptutor_shchem/qa/personal-visual-20260913/personal-visual-question-ui.png"
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=default)
    parser.add_argument("--small-output", type=Path)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    small_output = args.small_output
    if small_output is None:
        small_output = output.with_name(output.stem + "-900x700" + output.suffix)
    elif not small_output.is_absolute():
        small_output = ROOT / small_output
    capture(output, small_output)


if __name__ == "__main__":
    main()
