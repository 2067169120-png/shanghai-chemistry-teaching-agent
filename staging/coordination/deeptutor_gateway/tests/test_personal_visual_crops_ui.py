"""Only in-memory synthetic rasters/facades: no API, CAS, or private store."""

from __future__ import annotations

import hashlib
import os
import random
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from test_personal_visual_attributes import (
    attribute_context as attribute_context,  # noqa: PLC0414 - pytest fixture re-export
)
from test_personal_visual_attributes import (
    imported_visual_batch as imported_visual_batch,  # noqa: PLC0414 - pytest fixture re-export
)
from test_personal_visual_questions_ui import _Facade, _ImmediateTasks, _item

from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_crop_dialog import (
    PersonalVisualCropDialog,
    _bbox_pixels,
    normalised_box,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
    PersonalVisualQuestionDialog,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def options(role="question"):
    source = QImage(120, 160, QImage.Format.Format_RGB32)
    source.fill(QColor("#ffffff"))
    for y in range(160):
        for x in range(120):
            source.setPixelColor(x, y, QColor(x * 2, y, (x + y) % 255))
    raw = png(source)
    return {
        "batch_id": "batch-a",
        "key": "q-a",
        "revision": "rev-a",
        "image_id": "q-a-"
        + {"question": "question", "shared_material": "shared", "answer": "answer"}[
            role
        ],
        "evidence_id": "opaque-evidence-123",
        "role": role,
        "source_role": "question",
        "role_label": {
            "question": "题面裁片",
            "shared_material": "公共材料裁片",
            "answer": "答案裁片",
        }[role],
        "source_label": "合成来源第 1 页",
        "theme_title": "合成主题",
        "page_number": 1,
        "page_sha256": hashlib.sha256(raw).hexdigest(),
        "width": 120,
        "height": 160,
        "crop_active": False,
        "original_bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
        "current_bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
        "pixel_bounds": {"left": 12, "top": 16, "right": 72, "bottom": 96},
        "crop_revision": "opaque-crop-revision-123",
        "original_image": {"bytes": raw, "caption": "原始页"},
        "current_image": {
            "bytes": png(source.copy(12, 16, 60, 80)),
            "caption": "旧裁片",
        },
        "affected_questions": [
            {"key": "q-a", "title": "合成第一题", "revision": "rev-a"},
            {"key": "q-c", "title": "合成关联题", "revision": "rev-c"},
        ],
        "affected_question_keys": ["q-a", "q-c"],
        "history": [],
        "warning": "",
    }


class CropFacade(_Facade):
    def __init__(self):
        super().__init__()
        self.options_calls, self.preview_calls, self.save_calls, self.discarded = (
            [],
            [],
            [],
            [],
        )
        self.preview_change = None
        self.preview_error = self.save_error = False
        self.plan = None

    def personal_visual_question_crop_options(self, batch, key, revision, image_id):
        self.options_calls.append((batch, key, revision, image_id))
        role = (
            "answer"
            if image_id.endswith("answer")
            else "shared_material"
            if image_id.endswith("shared")
            else "question"
        )
        self.plan = options(role)
        return deepcopy(self.plan)

    def personal_visual_question_preview_crop(
        self, batch, key, revision, image_id, box, *, expected_crop_revision
    ):
        self.preview_calls.append(
            (batch, key, revision, image_id, deepcopy(box), expected_crop_revision)
        )
        if self.preview_error:
            raise ValueError("来源图已变化，请刷新。")
        value = deepcopy(self.plan or options())
        pixels = _bbox_pixels(box, value["width"], value["height"])
        source = QImage.fromData(value["original_image"]["bytes"])
        cropped = source.copy(
            pixels[0], pixels[1], pixels[2] - pixels[0], pixels[3] - pixels[1]
        )
        value.update(
            preview_id="opaque-preview-123",
            preview_revision="opaque-receipt-123",
            old_bbox=value["current_bbox"],
            new_bbox=deepcopy(box),
            preview_image={"bytes": png(cropped), "caption": "新范围"},
        )
        if self.preview_change:
            self.preview_change(value)
        return value

    def personal_visual_question_save_crop(
        self, preview_id, preview_revision, *, confirmed=False
    ):
        self.save_calls.append((preview_id, preview_revision, confirmed))
        if self.save_error:
            raise ValueError("来源已变化，不能使用旧预览保存。")
        changes, changed_rows = [], []
        for row in self.rows:
            if row["batch_id"] == "batch-a":
                old = row["revision"]
                row["revision"] += "-new"
                changes.append(
                    {
                        "key": row["key"],
                        "old_revision": old,
                        "new_revision": row["revision"],
                    }
                )
                changed_rows.append(deepcopy(row))
        return {
            "batch_id": "batch-a",
            "detail": changed_rows[0],
            "affected_rows": changed_rows,
            "revision_changes": changes,
            "crop_revision": "new-overlay",
            "warnings": [],
        }

    def personal_visual_question_discard_crop(self, preview_id):
        self.discarded.append(preview_id)


class DeferredTasks(_ImmediateTasks):
    def __init__(self):
        super().__init__()
        self.pending = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.pending.append((operation, on_success, on_failure))
        return "deferred"

    def flush(self):
        while self.pending:
            operation, success, failure = self.pending.pop(0)
            try:
                value = operation()
            except Exception as exc:  # noqa: BLE001 - explicit UI error-path fixture
                failure(str(exc))
            else:
                success(value)


@pytest.mark.parametrize(
    "width,height,box",
    [
        (120, 160, (0, 0, 120, 160)),
        (101, 137, (7, 9, 100, 136)),
        (997, 1231, (996, 1230, 997, 1231)),
        (1240, 1753, (0, 1, 1240, 1753)),
        (3, 7, (1, 2, 2, 6)),
        (39999, 39997, (17, 19, 39998, 39996)),
    ],
)
def test_pixel_normalisation_roundtrip(width, height, box):
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
        pixel_bounds,
    )

    result = normalised_box(box, width, height)
    assert _bbox_pixels(result, width, height) == box
    assert tuple(pixel_bounds(result, width, height).values()) == box


def test_thin_and_large_pixel_windows_roundtrip_without_extra_border():
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_crops import (
        pixel_bounds,
    )

    rng = random.Random(491)
    for _ in range(500):
        width, height = rng.randint(2, 40000), rng.randint(2, 40000)
        left, top = rng.randrange(width), rng.randrange(height)
        for right, bottom in ((left + 1, top + 1), (width, height)):
            expected = (left, top, right, bottom)
            box = normalised_box(expected, width, height)
            assert tuple(pixel_bounds(box, width, height).values()) == expected


@pytest.mark.parametrize("role", ["question", "shared_material", "answer"])
def test_real_source_new_pixels_and_explicit_save(qt_app, role):
    facade = CropFacade()
    facade.plan = options(role)
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), facade.plan)
    assert dialog._valid
    assert dialog.canvas.scene().items()
    assert dialog.current_image.has_image
    assert not dialog.new_image.has_image
    assert not dialog.preview_button.isEnabled() and not dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.saved_result is None and not dialog._closed
    dialog._set_bounds((10, 12, 86, 110))
    assert dialog.preview_button.isEnabled()
    dialog.preview_button.click()
    assert dialog.new_image.has_image
    assert dialog.new_image._source.size().toTuple() == (76, 98)
    assert dialog.new_image._source.toImage().pixelColor(0, 0) == QColor(20, 12, 22)
    assert not dialog.save_button.isEnabled()
    assert facade.preview_calls[0][-1] == facade.plan["crop_revision"]
    assert role == facade.plan["role"]
    dialog.impact_confirmed.setChecked(True)
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert facade.save_calls == [("opaque-preview-123", "opaque-receipt-123", True)]


def test_change_bounds_revokes_pixels_receipt_and_consent(qt_app):
    facade = CropFacade()
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), options())
    dialog._set_bounds((10, 12, 86, 110))
    dialog.preview_button.click()
    dialog.impact_confirmed.setChecked(True)
    dialog.edges["right"].setValue(87)
    assert not dialog.new_image.has_image and not dialog.save_button.isEnabled()
    assert not dialog.impact_confirmed.isChecked() and dialog._preview is None
    assert facade.discarded == ["opaque-preview-123"]
    dialog.reset_button.click()
    assert not dialog.preview_button.isEnabled()
    dialog.reject()
    assert not facade.save_calls


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(page_sha256="0" * 64),
        lambda value: value["original_image"].update(bytes=b"broken"),
        lambda value: value.update(width=121),
        lambda value: value.update(affected_questions=[]),
        lambda value: value.update(affected_question_keys=["q-a"]),
        lambda value: value["current_image"].update(bytes=b"broken"),
        lambda value: value.update(role="guessed"),
    ],
)
def test_original_read_and_scope_failures_cannot_preview_or_accept(qt_app, change):
    value = options()
    change(value)
    facade = CropFacade()
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), value)
    assert not dialog._valid
    dialog._set_bounds((10, 12, 86, 110))
    dialog.impact_confirmed.setChecked(True)
    dialog.accept()
    assert not dialog.preview_button.isEnabled() and not dialog.save_button.isEnabled()
    assert not dialog._closed and not facade.save_calls
    dialog.reject()


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(image_id="different-image"),
        lambda value: value.update(role="answer"),
        lambda value: value.update(page_sha256="0" * 64),
        lambda value: value.update(affected_question_keys=["q-a"]),
        lambda value: value.update(new_bbox={"x": 0, "y": 0, "width": 1, "height": 1}),
        lambda value: value["preview_image"].update(bytes=b"broken"),
        lambda value: value.update(preview_id=""),
    ],
)
def test_unbound_or_unreadable_preview_is_discarded(qt_app, change):
    facade = CropFacade()
    facade.preview_change = change
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), options())
    dialog._set_bounds((10, 12, 86, 110))
    dialog.preview_button.click()
    assert not dialog.new_image.has_image and dialog._preview is None
    assert not dialog.save_button.isEnabled() and not facade.save_calls
    dialog.reject()


def test_cancel_pending_preview_discards_late_receipt_without_write(qt_app):
    tasks, facade = DeferredTasks(), CropFacade()
    dialog = PersonalVisualCropDialog(facade, tasks, options())
    dialog._set_bounds((10, 12, 86, 110))
    dialog.preview_button.click()
    assert not dialog.save_button.isEnabled()
    dialog.reject()
    tasks.flush()
    assert tasks.cancelled == ["deferred"]
    assert facade.discarded == ["opaque-preview-123"] and not facade.save_calls
    assert not dialog.new_image.has_image


def test_save_rechecks_source_failure_clears_stale_pixels(qt_app):
    facade = CropFacade()
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), options())
    dialog._set_bounds((10, 12, 86, 110))
    dialog.preview_button.click()
    dialog.impact_confirmed.setChecked(True)
    facade.save_error = True
    dialog.save_button.click()
    assert not dialog._closed and dialog.saved_result is None
    assert not dialog.new_image.has_image and dialog._preview is None
    assert not dialog.save_button.isEnabled() and dialog.preview_button.isEnabled()
    dialog.reject()


def test_original_outside_tolerance_can_be_repaired_not_implicitly_clamped(qt_app):
    value = options()
    value["current_bbox"] = {"x": 0, "y": 0, "width": 1.0000001, "height": 1}
    value["pixel_bounds"] = {"left": 0, "top": 0, "right": 121, "bottom": 160}
    oversized = QImage(121, 160, QImage.Format.Format_RGB32)
    oversized.fill(QColor("white"))
    value["current_image"]["bytes"] = png(oversized)
    facade = CropFacade()
    facade.plan = value
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), value)
    assert dialog._valid and dialog.edges["right"].value() == 121
    assert not dialog.preview_button.isEnabled()
    assert "超出原页" in dialog.scope.text()
    dialog.edges["right"].setValue(120)
    dialog.preview_button.click()
    assert dialog.new_image.has_image
    assert not dialog.save_button.isEnabled()
    dialog.reject()


def test_canvas_drag_maps_zoom_to_original_pixels_and_narrow_layout(qt_app):
    dialog = PersonalVisualCropDialog(CropFacade(), _ImmediateTasks(), options())
    dialog.resize(700, 900)
    dialog.show()
    qt_app.processEvents()
    assert dialog.splitter.orientation() == Qt.Orientation.Vertical
    dialog.canvas.set_zoom(150)
    start = dialog.canvas.mapFromScene(QPointF(20, 30))
    end = dialog.canvas.mapFromScene(QPointF(80, 120))
    QTest.mousePress(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(dialog.canvas.viewport(), end)
    QTest.mouseRelease(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    actual = dialog._pixel_bounds()
    assert all(abs(a - b) <= 1 for a, b in zip(actual, (20, 30, 80, 120), strict=True))
    assert dialog.save_button.geometry().right() <= dialog.width()
    text = (
        "\n".join(label.text() for label in dialog.findChildren(QLabel))
        + dialog.affected.toPlainText()
    )
    for hidden in (
        "opaque-evidence",
        "opaque-crop",
        options()["page_sha256"],
        "q-a-question",
    ):
        assert hidden not in text
    assert "不会自动更新" in dialog.copy_note.text()
    dialog.reject()


@pytest.mark.parametrize("role", ["question", "shared_material", "answer"])
def test_each_image_card_opens_exact_role_and_cancel_keeps_basket(
    qt_app, monkeypatch, role
):
    facade = CropFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    _item(dialog, "q-a").setCheckState(Qt.CheckState.Checked)
    before = dialog.selections
    if role == "answer":
        dialog.tabs.setCurrentIndex(2)
    key = next(key for key in dialog._image_edit_crop_buttons if key[2] == role)
    seen = []

    def cancel(editor):
        seen.append(editor.options["role"])
        assert editor.current_image.has_image
        editor.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PersonalVisualCropDialog, "exec", cancel)
    dialog._image_edit_crop_buttons[key].click()
    assert seen == [role]
    assert facade.options_calls[-1] == (*key[0], key[1])
    assert dialog.selections == before and not facade.save_calls
    assert not dialog._crop_busy and dialog.reload_button.isEnabled()
    dialog.reject()


def test_crop_save_removes_only_affected_selection_and_retains_unsaved_points(
    qt_app, monkeypatch
):
    facade = CropFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    _item(dialog, "q-a").setCheckState(Qt.CheckState.Checked)
    dialog.question_list.setCurrentItem(_item(dialog, "q-b"))
    _item(dialog, "q-b").setCheckState(Qt.CheckState.Checked)
    token_b = next(token for token in dialog._selected if token[1] == "q-b")
    dialog._selected[token_b]["points"] = 7
    dialog.question_list.setCurrentItem(_item(dialog, "q-a"))

    def save(editor):
        editor._set_bounds((10, 12, 86, 110))
        editor.preview_button.click()
        editor.impact_confirmed.setChecked(True)
        editor.save_button.click()
        return editor.result()

    monkeypatch.setattr(PersonalVisualCropDialog, "exec", save)
    image_key = next(
        key for key in dialog._image_edit_crop_buttons if key[2] == "shared_material"
    )
    dialog._image_edit_crop_buttons[image_key].click()
    assert dialog.selections == [
        {"batch_id": "batch-b", "key": "q-b", "revision": "rev-b", "points": 7}
    ]
    assert dialog._current_token == ("batch-a", "q-a", "rev-a-new")
    assert dialog._required_images_ready(dialog._current_token)
    assert "2 道题" in dialog.status.text() and "重新带入" in dialog.status.text()
    assert not facade.saved and not facade.reference_calls
    dialog.reject()


def test_save_pending_keeps_window_open_and_blocks_double_submit(qt_app):
    facade, tasks = CropFacade(), DeferredTasks()
    dialog = PersonalVisualCropDialog(facade, tasks, options())
    dialog.show()
    dialog._set_bounds((10, 12, 86, 110))
    dialog.preview_button.click()
    tasks.flush()
    dialog.impact_confirmed.setChecked(True)
    dialog.save_button.click()
    dialog._save()
    dialog.close()
    qt_app.processEvents()
    assert not dialog._closed and dialog.isVisible()
    assert not dialog.cancel_button.isEnabled() and len(tasks.pending) == 1
    tasks.flush()
    assert dialog.saved_result and len(facade.save_calls) == 1


def test_failed_options_and_failed_refresh_keep_unrelated_unsaved_basket(
    qt_app, monkeypatch
):
    facade = CropFacade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    dialog.question_list.setCurrentItem(_item(dialog, "q-b"))
    _item(dialog, "q-b").setCheckState(Qt.CheckState.Checked)
    before = dialog.selections
    dialog.question_list.setCurrentItem(_item(dialog, "q-a"))

    def fail_options(*_args):
        raise ValueError("opaque-private-evidence")

    monkeypatch.setattr(facade, "personal_visual_question_crop_options", fail_options)
    image_key = next(
        key for key in dialog._image_edit_crop_buttons if key[2] == "question"
    )
    dialog._image_edit_crop_buttons[image_key].click()
    assert dialog.selections == before and dialog.reload_button.isEnabled()
    assert "opaque-private-evidence" not in dialog.status.text()
    result = facade.personal_visual_question_save_crop(
        "synthetic-receipt", "synthetic-hash", confirmed=True
    )
    facade.catalog_failure = True
    dialog._crop_saved(("batch-a", "q-a", "rev-a"), options(), result)
    assert dialog.selections == before and "刷新暂时失败" in dialog.status.text()
    assert dialog.reload_button.isEnabled() and not dialog._crop_busy
    dialog.reject()


@pytest.mark.parametrize("role", ["question", "shared_material", "answer"])
def test_ui_with_real_crop_service_on_temporary_synthetic_import(
    qt_app, attribute_context, role
):
    from test_personal_visual_crops import _target

    service = attribute_context["service"]
    _, row, image, value = _target(attribute_context, role=role)
    facade = SimpleNamespace(
        personal_visual_question_preview_crop=service.preview_crop,
        personal_visual_question_save_crop=service.save_crop,
        personal_visual_question_discard_crop=service.discard_crop,
    )
    dialog = PersonalVisualCropDialog(facade, _ImmediateTasks(), value)
    assert dialog._valid and dialog.current_image.has_image
    dialog._set_bounds((1, 2, value["width"] - 1, value["height"] - 2))
    dialog.preview_button.click()
    assert dialog.new_image.has_image
    dialog.new_image.zoom_button.click()
    assert dialog.new_image._zoom_dialog is not None
    dialog.impact_confirmed.setChecked(True)
    dialog.save_button.click()
    assert (
        dialog.saved_result is not None
        and dialog.result() == QDialog.DialogCode.Accepted
    )
    updated = next(
        item
        for item in dialog.saved_result["affected_rows"]
        if item["key"] == row["key"]
    )
    updated_image = next(
        item
        for item in updated["images"]
        if item["evidence_id"] == image["evidence_id"] and item["role"] == role
    )
    reopened = service.crop_options(
        value["batch_id"], row["key"], updated["revision"], updated_image["image_id"]
    )
    assert reopened["crop_active"] is True
    second = PersonalVisualCropDialog(facade, _ImmediateTasks(), reopened)
    assert second._valid and second.current_image.has_image
    assert not second.preview_button.isEnabled()
    second.reject()
