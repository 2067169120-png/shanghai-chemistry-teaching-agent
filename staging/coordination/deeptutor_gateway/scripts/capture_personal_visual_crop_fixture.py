"""Capture the real crop editor using only a newly drawn, synthetic page."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def _png(image):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(
        buffer, "PNG"
    ):
        raise RuntimeError("Synthetic raster cannot be encoded")
    return bytes(data)


def synthetic_options():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    page = QImage(760, 960, QImage.Format.Format_RGB32)
    page.fill(QColor("#ffffff"))
    painter = QPainter(page)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Microsoft YaHei", 21))
    painter.setPen(QColor("#183b42"))
    painter.drawText(45, 58, "纯合成版面示例 · 非真实试题")
    painter.setFont(QFont("Microsoft YaHei", 14))
    painter.setPen(QColor("#677980"))
    painter.drawText(45, 96, "用于检验裁片是否完整，不作为教学或答案内容")
    painter.setPen(QColor("#1c262b"))
    painter.setFont(QFont("Microsoft YaHei", 19))
    lines = [
        (172, "公共材料：请同时保留文字与示意图。"),
        (222, "材料 A 的观察记录如下，供两道题共同使用。"),
        (272, "检查点一：第一行标题不能被裁掉。"),
        (322, "检查点二：右侧说明与图形边缘应完整。"),
        (548, "检查点三：保留最后一行，不混入下一题。"),
        (594, "本段结束：选择新范围后，对照右侧真实预览。"),
    ]
    for y, line in lines:
        painter.drawText(55, y, line)
    painter.setPen(QPen(QColor("#176c77"), 3))
    painter.drawRect(78, 355, 210, 125)
    painter.drawLine(288, 415, 385, 415)
    painter.drawEllipse(385, 355, 180, 125)
    painter.setFont(QFont("Microsoft YaHei", 18))
    painter.drawText(130, 430, "材料 A")
    painter.drawText(420, 430, "观察点")
    painter.setPen(QPen(QColor("#c3cbd0"), 2, Qt.PenStyle.DashLine))
    painter.drawLine(45, 710, 715, 710)
    painter.setPen(QColor("#71828b"))
    painter.drawText(55, 770, "下一题（不应包含在上方新裁片中）")
    painter.end()
    raw = _png(page)
    old = (45, 178, 640, 556)
    bbox = {
        "x": old[0] / 760,
        "y": old[1] / 960,
        "width": (old[2] - old[0]) / 760,
        "height": (old[3] - old[1]) / 960,
    }
    # This fixture represents an existing teacher overlay, so its integer
    # edges use the backend's roundoff-safe renderer.
    return {
        "batch_id": "synthetic-batch",
        "key": "synthetic-first",
        "revision": "synthetic-content",
        "image_id": "synthetic-image",
        "evidence_id": "synthetic-evidence",
        "role": "shared_material",
        "source_role": "question",
        "role_label": "公共材料裁片",
        "source_label": "合成来源第 1 页",
        "theme_title": "裁片返工 · 公共材料完整性核对",
        "page_number": 1,
        "page_sha256": hashlib.sha256(raw).hexdigest(),
        "width": 760,
        "height": 960,
        "crop_active": True,
        "original_bbox": bbox,
        "current_bbox": bbox,
        "pixel_bounds": dict(zip(("left", "top", "right", "bottom"), old, strict=True)),
        "crop_revision": "synthetic-crop-version",
        "history": [],
        "warning": "",
        "original_image": {"bytes": raw, "caption": "纯合成原页"},
        "current_image": {
            "bytes": _png(page.copy(old[0], old[1], old[2] - old[0], old[3] - old[1])),
            "caption": "旧范围",
        },
        "affected_questions": [
            {
                "key": "synthetic-first",
                "revision": "synthetic-content",
                "title": "第 1 题 · 根据材料 A 读取观察记录",
            },
            {
                "key": "synthetic-second",
                "revision": "synthetic-content-2",
                "title": "第 2 题 · 使用同一公共材料完成说明",
            },
        ],
        "affected_question_keys": ["synthetic-first", "synthetic-second"],
    }


class _Tasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - synthetic preview error delivery
            on_failure(str(exc))
        else:
            on_success(result)
        return "synthetic-task"


class _Facade:
    def __init__(self, options):
        self.options = options

    def personal_visual_question_preview_crop(self, *_args, expected_crop_revision):
        from PySide6.QtGui import QImage

        from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
            pixel_bounds,
        )

        if expected_crop_revision != self.options["crop_revision"]:
            raise ValueError("Synthetic version changed")
        box = _args[-1]
        edges = pixel_bounds(box, self.options["width"], self.options["height"])
        source = QImage.fromData(self.options["original_image"]["bytes"])
        crop = source.copy(
            edges["left"],
            edges["top"],
            edges["right"] - edges["left"],
            edges["bottom"] - edges["top"],
        )
        return {
            **deepcopy(self.options),
            "preview_id": "synthetic-preview",
            "preview_revision": "synthetic-receipt",
            "old_bbox": self.options["current_bbox"],
            "new_bbox": box,
            "preview_image": {"bytes": _png(crop), "caption": "纯合成新裁片"},
        }

    def personal_visual_question_discard_crop(self, _preview_id):
        return True

    def personal_visual_question_save_crop(self, *_args, **_kwargs):
        raise AssertionError("This fixture must never save or access a store")


def capture(output, *, width=1160, height=820):
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing screenshot: {output}")
    if output.suffix.casefold() != ".png" or width < 420 or height < 600:
        raise ValueError("Choose a fresh PNG path and at least 420 by 600 pixels")
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_crop_dialog import (
        PersonalVisualCropDialog,
    )

    app = create_application([])
    font = install_font_fallbacks()
    options = synthetic_options()
    dialog = PersonalVisualCropDialog(_Facade(options), _Tasks(), options)
    try:
        dialog.resize(width, height)
        dialog.show()
        app.processEvents()
        dialog._set_bounds((45, 135, 715, 620))
        dialog.preview_button.click()
        app.processEvents()
        dialog.canvas.fit_page()
        app.processEvents()
        assert dialog.current_image.has_image and dialog.new_image.has_image
        assert (
            not dialog.impact_confirmed.isChecked()
            and not dialog.save_button.isEnabled()
        )
        assert dialog.save_button.isVisible() and dialog.cancel_button.isVisible()
        assert dialog.width() == width and dialog.height() == height
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(_png(dialog.grab()))
    finally:
        dialog.reject()
        app.processEvents()
    print(f"Synthetic crop comparison: {width}x{height}; font={font}")
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1160)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    capture(output, width=args.width, height=args.height)


if __name__ == "__main__":
    main()
