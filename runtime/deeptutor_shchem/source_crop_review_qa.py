"""C05 native review screenshots from synthetic pixels and in-memory receipts."""

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


def png(image):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Cannot encode synthetic raster")
    return bytes(data)


def synthetic_review_options():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_crop_dialog import normalised_box
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import ui_font

    def page_for(role):
        shared = role == "shared_material"
        width, height = (920, 680) if role == "question" else (760, 960)
        page = QImage(width, height, QImage.Format.Format_RGB32)
        page.fill(QColor("#ffffff"))
        painter = QPainter(page)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(ui_font(21))
        painter.setPen(QColor("#183b42"))
        painter.drawText(45, 58, "纯合成版面示例 · 非真实试题")
        painter.setFont(ui_font(13))
        painter.setPen(QColor("#677980"))
        painter.drawText(45, 96, "仅用于核对裁片与原页，不含学生数据或实际教学答案")
        painter.setFont(ui_font(18))
        painter.setPen(QColor("#1c262b"))
        if shared:
            lines = [
                (172, "共同材料：请同时保留文字与示意图。"),
                (222, "材料 A 的观察记录，供两道题共同使用。"),
                (272, "检查点一：第一行标题不能被裁掉。"),
                (322, "检查点二：右侧说明与图形边缘应完整。"),
                (548, "检查点三：保留末行，不混入下一题。"),
                (594, "本段结束：新范围应包含这一行。"),
            ]
            for y, text in lines:
                painter.drawText(55, y, text)
            painter.setPen(QPen(QColor("#176c77"), 3))
            painter.drawRect(78, 355, 210, 125)
            painter.drawLine(288, 415, 385, 415)
            painter.drawEllipse(385, 355, 180, 125)
            painter.drawText(126, 430, "材料 A")
            painter.drawText(417, 430, "观察点")
            separator = 710
        elif role == "question":
            painter.drawText(55, 172, "第 1 题 · 使用前一页的共同材料")
            painter.drawText(55, 228, "（1）读取材料 A 的两项观察记录。")
            painter.drawText(55, 284, "（2）根据表格中的条件，说明比较依据。")
            painter.setPen(QPen(QColor("#176c77"), 2))
            painter.drawRect(65, 325, 700, 120)
            painter.drawLine(65, 380, 765, 380)
            painter.drawLine(365, 325, 365, 445)
            painter.drawText(100, 363, "观察条件")
            painter.drawText(410, 363, "记录栏（合成）")
            painter.drawText(100, 424, "条件一")
            painter.drawText(410, 424, "由教师检查文字与边框")
            painter.drawText(55, 504, "末行：与共同材料一起核对。")
            separator = 555
        else:
            painter.drawText(55, 172, "合成参考答案页 · 显式选择后显示")
            painter.drawText(55, 234, "此处只用于检验答案页不会错绑题面。")
            painter.drawText(55, 296, "不构成真实答案或评分规则。")
            painter.setPen(QPen(QColor("#b67b23"), 3))
            painter.drawRect(65, 340, 625, 130)
            painter.drawText(92, 416, "答案页独立来源、独立裁剪范围")
            separator = 610
        painter.setPen(QPen(QColor("#c3cbd0"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(45, separator, width - 45, separator)
        painter.setPen(QColor("#71828b"))
        painter.drawText(55, separator + 55, "下一段（不应混入上方裁片）")
        painter.end()
        return page

    plans = {}
    for role, page_number, bounds in (
        ("question", 2, (45, 140, 800, 520)),
        ("shared_material", 1, (45, 178, 640, 556)),
        ("answer", 1, (45, 140, 715, 510)),
    ):
        page = page_for(role)
        raw = png(page)
        bbox = normalised_box(bounds, page.width(), page.height())
        questions = [{"key": "synthetic-first", "revision": "synthetic-content", "title": "第 1 题 · 读取观察记录"}]
        if role == "shared_material":
            questions.append({"key": "synthetic-second", "revision": "synthetic-content-2", "title": "第 2 题 · 使用同一共同材料"})
        image_id = "synthetic-" + role
        plans[image_id] = {
            "batch_id": "synthetic-batch", "key": "synthetic-first", "revision": "synthetic-content",
            "image_id": image_id, "evidence_id": "synthetic-evidence-" + role,
            "role": role, "source_role": "answer" if role == "answer" else "question",
            "role_label": {"question": "题面裁片", "shared_material": "共同材料裁片", "answer": "答案裁片"}[role],
            "source_label": "合成参考答案" if role == "answer" else "合成课堂材料",
            "theme_title": "观察记录 · 原页与裁片完整性审核",
            "page_number": page_number, "page_sha256": hashlib.sha256(raw).hexdigest(),
            "width": page.width(), "height": page.height(), "crop_active": True,
            "original_bbox": bbox, "current_bbox": bbox,
            "pixel_bounds": dict(zip(("left", "top", "right", "bottom"), bounds, strict=True)),
            "crop_revision": "synthetic-crop-" + role, "history": [{"synthetic": True}], "warning": "",
            "original_image": {"bytes": raw, "caption": "纯合成原页"},
            "current_image": {"bytes": png(page.copy(bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1])), "caption": "当前裁片"},
            "affected_questions": questions, "affected_question_keys": [row["key"] for row in questions],
        }
    choices = [{key: item[key] for key in ("image_id", "role", "page_number", "page_sha256", "source_label")} for item in plans.values()]
    for item in plans.values():
        item["review_images"] = deepcopy(choices)
    return plans


class ImmediateTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - synthetic task error delivery
            on_failure(str(exc))
        else:
            on_success(result)
        return "synthetic-task"


class SyntheticReviewFacade:
    """No file/state/network API; save only records a synthetic receipt in memory."""

    def __init__(self, plans=None):
        self.plans = plans if plans is not None else synthetic_review_options()
        self.options_calls, self.preview_calls, self.save_calls, self.discarded = [], [], [], []
        self.previews = {}
        self.change_options = None

    def personal_visual_question_crop_options(self, batch, key, revision, image_id):
        self.options_calls.append((batch, key, revision, image_id))
        value = deepcopy(self.plans[image_id])
        assert tuple(value[field] for field in ("batch_id", "key", "revision")) == (batch, key, revision)
        if self.change_options:
            self.change_options(value)
        return value

    def personal_visual_question_preview_crop(self, batch, key, revision, image_id, bbox, *, expected_crop_revision):
        from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import crop_bytes

        value = deepcopy(self.plans[image_id])
        assert tuple(value[field] for field in ("batch_id", "key", "revision")) == (batch, key, revision)
        assert expected_crop_revision == value["crop_revision"]
        self.preview_calls.append((image_id, deepcopy(bbox)))
        value.update(preview_id=f"synthetic-preview-{len(self.preview_calls)}", preview_revision="synthetic-receipt", new_bbox=deepcopy(bbox))
        value["preview_image"] = {"bytes": crop_bytes(value["original_image"]["bytes"], bbox, value["width"], value["height"])}
        self.previews[value["preview_id"]] = value
        return deepcopy(value)

    def personal_visual_question_save_crop(self, preview_id, preview_revision, *, confirmed=False):
        value = self.previews[preview_id]
        assert confirmed and preview_revision == value["preview_revision"]
        self.save_calls.append((preview_id, preview_revision, confirmed, value["image_id"]))
        return {"batch_id": value["batch_id"], "revision_changes": [
            {"key": item["key"], "old_revision": item["revision"], "new_revision": item["revision"] + "-new"}
            for item in value["affected_questions"]]}

    def personal_visual_question_discard_crop(self, preview_id):
        self.discarded.append(preview_id)
        self.previews.pop(preview_id, None)


def capture(output):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionFrame

    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_crop_dialog import PersonalVisualCropDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import typography_report

    output.mkdir(parents=True, exist_ok=True)
    app = create_application([])
    facade = SyntheticReviewFacade()
    original = facade.plans["synthetic-shared_material"]
    files, layout_checks = [], []

    def save(name, raw):
        path = output / name
        path.write_bytes(raw)
        files.append({"name": name, "sha256": hashlib.sha256(raw).hexdigest()})

    for item in facade.plans.values():
        save(f"original-{item['role']}.png", item["original_image"]["bytes"])
    dialog = PersonalVisualCropDialog(facade, ImmediateTasks(), original)
    dialog.resize(1160, 860)
    dialog.show()

    def screen(name):
        app.processEvents()
        QTest.qWait(15)
        fields = {}
        for key, field in dialog.edges.items():
            edit = field.lineEdit()
            option = QStyleOptionFrame()
            edit.initStyleOption(option)
            contents = edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, edit)
            margins = edit.textMargins()
            available = contents.width() - margins.left() - margins.right() - 4
            required = edit.fontMetrics().horizontalAdvance("9999 px")
            label = dialog.edge_labels[key]
            assert label.geometry().right() < field.geometry().left()
            assert available >= required
            fields[key] = {"text": field.text(), "available_text_width": available,
                           "four_digit_unit_width": required, "label_separate": True}
        crop = dialog.previews.currentWidget()
        fit = None
        if crop.has_image and crop._fit_mode:
            view = crop.image
            fit = {"horizontal_scroll_max": view.horizontalScrollBar().maximum(),
                   "vertical_scroll_max": view.verticalScrollBar().maximum(),
                   "entire_scene_visible": view.mapToScene(view.viewport().rect()).boundingRect().contains(view.sceneRect())}
            assert not fit["horizontal_scroll_max"] and not fit["vertical_scroll_max"]
            assert fit["entire_scene_visible"]
        layout_checks.append({"screenshot": name, "width": dialog.width(), "fields": fields,
                              "fitted_crop": fit, "crop_zoom": crop.zoom_value.text()})
        save(name, png(dialog.grab()))

    screen("01-wide-current.png")
    dialog._set_bounds((45, 135, 715, 620))
    QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
    assert dialog.new_image.has_image and dialog.previews.currentIndex() == 0
    screen("02-wide-new-crop.png")
    dialog.new_image.zoom.setValue(125)
    screen("03-wide-inline-zoom.png")
    dialog.image_combo.setCurrentIndex(dialog.image_combo.findData("synthetic-question"))
    assert dialog.options["image_id"] == "synthetic-question"
    screen("04-wide-question-page.png")
    dialog.image_combo.setCurrentIndex(dialog.image_combo.findData("synthetic-shared_material"))
    assert dialog._pixel_bounds() == (45, 135, 715, 620)
    dialog.resize(420, 860)
    dialog.scroll.verticalScrollBar().setValue(0)
    screen("05-narrow-420-original.png")
    QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
    dialog.scroll.ensureWidgetVisible(dialog.new_image, 0, 10)
    screen("06-narrow-420-crop.png")
    dialog.resize(360, 800)
    app.processEvents()
    dialog.scroll.ensureWidgetVisible(dialog.impact_confirmed, 0, 10)
    QTest.mouseClick(dialog.impact_confirmed, Qt.MouseButton.LeftButton)
    assert dialog.save_button.isEnabled()
    screen("07-narrow-360-confirm.png")
    report = {"synthetic_only": True, "provider_calls": 0, "private_store_calls": 0,
        "screenshots": files, "layout_checks": layout_checks, "typography": typography_report(dialog),
        "option_reads": len(facade.options_calls), "previews": len(facade.preview_calls),
        "save_calls": len(facade.save_calls), "restored_shared_draft": list(dialog._pixel_bounds())}
    assert report["typography"]["han"]["missing_glyphs"] == 0
    dialog.reject()
    assert not facade.save_calls and not facade.previews
    report["cancel_discarded_all_receipts"] = True
    (output / "capture-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "screenshots": 7, "original_pages": 3, "provider_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/source-review-qa-20260926")
    capture(parser.parse_args().output)
