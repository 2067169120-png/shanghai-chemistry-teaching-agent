"""Original-page rectangle editing with an explicit, source-bound preview."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from copy import deepcopy

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_visual_import_v2 import _MAX_IMAGE_EDGE, _MAX_PAGE_BYTES
from .components import set_status
from .preparation_images_widget import _LocalImagePreview


def _label(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return widget


def _bounds(value, width, height):
    if not isinstance(value, Mapping):
        raise TypeError("裁剪范围缺失。")
    result = tuple(value.get(key) for key in ("left", "top", "right", "bottom"))
    if not all(type(number) is int for number in result):
        raise ValueError("裁剪边界必须是原图像素。")
    left, top, right, bottom = result
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("裁剪范围必须在原页内且有实际面积。")
    return result


def _bbox_pixels(box, width, height, *, snap_roundoff=True, original=False):
    if not isinstance(box, Mapping):
        raise TypeError("裁剪范围格式不正确。")
    numbers = [box.get(key) for key in ("x", "y", "width", "height")]
    if any(
        type(value) not in (int, float) or not math.isfinite(value) for value in numbers
    ):
        raise ValueError("裁剪范围格式不正确。")
    x, y, w, h = numbers
    limit = 1.000001 if original else 1.0
    if not (
        0 <= x < 1
        and 0 <= y < 1
        and w > 0
        and h > 0
        and x + w <= limit
        and y + h <= limit
    ):
        raise ValueError("裁剪范围超出原页。")

    def exact(value):
        nearest = round(value)
        return nearest if snap_roundoff and abs(value - nearest) <= 1e-9 else value

    return _bounds(
        {
            "left": math.floor(exact(x * width)),
            "top": math.floor(exact(y * height)),
            "right": math.ceil(exact((x + w) * width)),
            "bottom": math.ceil(exact((y + h) * height)),
        },
        width + int(original),
        height + int(original),
    )


def normalised_box(bounds, width, height):
    """Preserve integer pixel edges through the service's floor/ceil renderer."""
    left, top, right, bottom = _bounds(
        dict(zip(("left", "top", "right", "bottom"), bounds, strict=True)),
        width,
        height,
    )

    def axis(low, high, length):
        start = low / length
        # The backend removes only sub-nanopixel roundoff before floor/ceil.
        # At the page edge, construct the span against one to avoid 1 + ulp.
        span = 1.0 - start if high == length else (high - low) / length
        return start, span

    x, w = axis(left, right, width)
    y, h = axis(top, bottom, height)
    result = {"x": x, "y": y, "width": w, "height": h}
    if _bbox_pixels(result, width, height) != (left, top, right, bottom):
        raise ValueError("裁剪像素换算不一致。")
    return result


class CropCanvas(QGraphicsView):
    """Scene coordinates are original-page pixels, including at any zoom."""

    bounds_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setMinimumSize(0, 150)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAccessibleName(
            "原页框选画布，拖动重新选框，也可用四个像素边界输入框微调"
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.horizontalScrollBar().setStyleSheet(
            "QScrollBar:horizontal { height: 10px; }"
        )
        self._size = None
        self._selection = None
        self._anchor = None
        self._fit_mode = True
        self._rectangle = None

    def set_source(self, pixmap, current):
        self.scene().clear()
        self.scene().addPixmap(pixmap)
        self.scene().setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self._size = (pixmap.width(), pixmap.height())
        old_pen = QPen(QColor("#7b8791"), 1, Qt.PenStyle.DashLine)
        old_pen.setCosmetic(True)
        self.scene().addRect(
            QRectF(
                current[0], current[1], current[2] - current[0], current[3] - current[1]
            ),
            old_pen,
        )
        pen = QPen(QColor("#087c83"), 2)
        pen.setCosmetic(True)
        self._rectangle = self.scene().addRect(QRectF(), pen, QColor(8, 124, 131, 24))
        self.set_bounds(current)
        self.fit_page()

    def set_bounds(self, value):
        self._selection = tuple(value)
        if self._rectangle is not None:
            left, top, right, bottom = value
            self._rectangle.setRect(QRectF(left, top, right - left, bottom - top))

    def fit_page(self):
        self._fit_mode = True
        if self._size:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_zoom(self, percent):
        self._fit_mode = False
        self.resetTransform()
        self.scale(percent / 100, percent / 100)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_page()

    def mousePressEvent(self, event):
        if self._size and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if 0 <= point.x() <= self._size[0] and 0 <= point.y() <= self._size[1]:
                self._anchor = point
                event.accept()
                return
        super().mousePressEvent(event)

    def _drag_to(self, position):
        if self._anchor is None:
            return
        point = self.mapToScene(position)
        x = max(0, min(self._size[0], point.x()))
        y = max(0, min(self._size[1], point.y()))
        proposed = (
            math.floor(min(self._anchor.x(), x)),
            math.floor(min(self._anchor.y(), y)),
            math.ceil(max(self._anchor.x(), x)),
            math.ceil(max(self._anchor.y(), y)),
        )
        if (
            proposed[0] < proposed[2]
            and proposed[1] < proposed[3]
            and proposed != self._selection
        ):
            self.set_bounds(proposed)
            self.bounds_changed.emit(proposed)

    def mouseMoveEvent(self, event):
        if self._anchor is not None:
            self._drag_to(event.position().toPoint())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._anchor is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_to(event.position().toPoint())
            self._anchor = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class PersonalVisualCropDialog(QDialog):
    """Only a successful frozen service save closes this dialog as Accepted."""

    def __init__(self, facade, tasks, options, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.options = deepcopy(options) if isinstance(options, Mapping) else {}
        self.saved_result = None
        self._valid = self._busy = self._saving = self._closed = False
        self._updating_bounds = False
        self._generation = 0
        self._preview = None
        self._task_id = None
        self._size = (1, 1)
        self._initial_bounds = (0, 0, 1, 1)
        self.setWindowTitle("调整这张裁片 · 先预览再保存")
        self.resize(1160, 820)
        self.setMinimumSize(420, 600)
        root = QVBoxLayout(self)
        title = " · ".join(
            str(self.options.get(key) or "")
            for key in ("theme_title", "role_label", "source_label")
        )
        self.heading = _label(title or "图片裁剪返工")
        self.heading.setObjectName("CardTitle")
        root.addWidget(self.heading)
        self.scope = _label("正在核对原页与关联题目…")
        root.addWidget(self.scope)
        self.affected = QPlainTextEdit()
        self.affected.setReadOnly(True)
        self.affected.setAccessibleName("本次裁剪影响的完整题目清单")
        self.affected.setFixedHeight(62)
        root.addWidget(self.affected)
        self.copy_note = _label(
            "已带入备课、教案或课件的副本不会自动更新；保存后请重新带入完整主题。"
        )
        root.addWidget(self.copy_note)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(0)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.addWidget(
            _label("原页：拖动框选；灰色虚线为当前范围，蓝绿色框为新范围。")
        )
        zoom_row = QHBoxLayout()
        self.fit_button = QPushButton("适合整页")
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(25, 200)
        self.zoom.setValue(100)
        self.zoom.setAccessibleName("原页缩放百分比")
        self.zoom.setMinimumWidth(0)
        zoom_row.addWidget(self.fit_button)
        zoom_row.addWidget(_label("缩放"))
        zoom_row.addWidget(self.zoom, 1)
        left_layout.addLayout(zoom_row)
        self.canvas = CropCanvas()
        left_layout.addWidget(self.canvas, 1)
        grid = QGridLayout()
        self.edges = {}
        for index, (key, label) in enumerate(
            (("left", "左边"), ("top", "上边"), ("right", "右边"), ("bottom", "下边"))
        ):
            field = QSpinBox()
            field.setRange(0, _MAX_IMAGE_EDGE + 1)
            field.setSuffix(" px")
            field.setAccessibleName("原图像素" + label)
            field.setMinimumWidth(0)
            self.edges[key] = field
            grid.addWidget(_label(label), index // 2, (index % 2) * 2)
            grid.addWidget(field, index // 2, (index % 2) * 2 + 1)
            field.valueChanged.connect(self._edges_changed)
        left_layout.addLayout(grid)
        self.reset_button = QPushButton("恢复当前范围")
        left_layout.addWidget(self.reset_button)
        self.splitter.addWidget(left)
        self.previews = QTabWidget()
        self.previews.setMinimumWidth(0)
        self.current_image = _LocalImagePreview()
        self.new_image = _LocalImagePreview()
        for preview in (self.current_image, self.new_image):
            preview.image.setMinimumHeight(110)
            preview.image.setMaximumHeight(16777215)
            preview.image.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
            )
            preview.layout().setStretch(0, 1)
        self.previews.addTab(self.new_image, "新裁片预览")
        self.previews.addTab(self.current_image, "当前裁片对照")
        self.splitter.addWidget(self.previews)
        self.splitter.setSizes([650, 450])
        root.addWidget(self.splitter, 1)
        self.impact_confirmed = QCheckBox("已检查新裁片边界及对关联题目的影响")
        self.impact_confirmed.setAccessibleName("明确确认已核对新裁片及影响题目")
        self.impact_confirmed.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        root.addWidget(self.impact_confirmed)
        self.status = _label("未预览、未保存。")
        root.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.preview_button = QPushButton("预览新裁片")
        self.save_button = QPushButton("确认保存裁剪")
        self.save_button.setObjectName("PrimaryButton")
        for button in (self.cancel_button, self.preview_button, self.save_button):
            button.setAutoDefault(False)
            controls.addWidget(button)
        self.cancel_button.setDefault(True)
        root.addLayout(controls)
        self.fit_button.clicked.connect(self.canvas.fit_page)
        self.zoom.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.bounds_changed.connect(self._set_bounds)
        self.reset_button.clicked.connect(
            lambda: self._set_bounds(self._initial_bounds)
        )
        self.preview_button.clicked.connect(self._request_preview)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        self.impact_confirmed.toggled.connect(self._actions)
        try:
            self._load_options()
        except (KeyError, TypeError, ValueError, RuntimeError):
            self._valid = False
            self.current_image.clear("原页或当前裁片无法核对。")
            self.new_image.clear("请取消，重新读取这张图片后再调整。")
            set_status(
                self.status, "error", "原页、当前范围或关联题清单不完整，本次不能保存。"
            )
        self._actions()

    def _load_options(self):
        options = self.options
        width, height = options["width"], options["height"]
        if any(
            type(size) is not int or not 0 < size <= _MAX_IMAGE_EDGE
            for size in (width, height)
        ):
            raise ValueError("Invalid original page dimensions")
        raw = options["original_image"]["bytes"]
        if (
            not isinstance(raw, bytes)
            or not 0 < len(raw) <= _MAX_PAGE_BYTES
            or hashlib.sha256(raw).hexdigest() != options["page_sha256"]
        ):
            raise ValueError("Original page changed")
        pixmap = QPixmap()
        if not pixmap.loadFromData(raw) or (pixmap.width(), pixmap.height()) != (
            width,
            height,
        ):
            raise ValueError("Original page cannot be shown")
        active = options.get("crop_active", False)
        if type(active) is not bool:
            raise TypeError("Invalid crop version state")
        bounds = _bounds(
            options["pixel_bounds"], width + int(not active), height + int(not active)
        )
        if (
            _bbox_pixels(
                options["current_bbox"],
                width,
                height,
                snap_roundoff=active,
                original=not active,
            )
            != bounds
        ):
            raise ValueError("Initial pixel bounds do not match")
        if options["role"] not in {"question", "shared_material", "answer"}:
            raise ValueError("Invalid image role")
        questions = options["affected_questions"]
        if not isinstance(questions, list) or not questions:
            raise ValueError("Missing affected questions")
        keys = [row["key"] for row in questions]
        if (
            len(keys) != len(set(keys))
            or set(keys) != set(options["affected_question_keys"])
            or options["key"] not in keys
        ):
            raise ValueError("Affected question scope changed")
        self._size, self._initial_bounds = (width, height), bounds
        self.canvas.set_source(pixmap, bounds)
        for key, field in self.edges.items():
            field.setMaximum(
                max(width, bounds[2])
                if key in {"left", "right"}
                else max(height, bounds[3])
            )
        self._set_bounds(bounds)
        self.current_image.set_bytes(
            options["current_image"]["bytes"], "当前裁片，仅用于对照"
        )
        if (
            self.current_image._source.width(),
            self.current_image._source.height(),
        ) != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
            raise ValueError("Current crop dimensions changed")
        self.scope.setText(
            f"本次调整影响 {len(keys)} 道题。旧标签保留并待重核，受影响题需重新看图选用；不改原页、文字或答案。"
        )
        self.affected.setPlainText(
            "\n".join(str(row.get("title") or "关联题目") for row in questions)
        )
        self.new_image.clear("在左侧画出新范围，再点击“预览新裁片”。")
        self._valid = True
        if bounds[2] > width or bounds[3] > height:
            self.scope.setText(
                self.scope.text()
                + " 当前旧框超出原页，请把新框收回页内后预览；没有自动修改旧框。"
            )
        set_status(
            self.status,
            "info",
            "已带入备课、教案或课件的副本不会自动更新；保存后请重新带入。",
        )

    def _pixel_bounds(self):
        return tuple(
            self.edges[key].value() for key in ("left", "top", "right", "bottom")
        )

    def _set_bounds(self, bounds):
        previous = self._pixel_bounds()
        self._updating_bounds = True
        for key, value in zip(("left", "top", "right", "bottom"), bounds, strict=True):
            self.edges[key].setValue(value)
        self._updating_bounds = False
        self.canvas.set_bounds(bounds)
        if previous != tuple(bounds):
            self._invalidate_preview()
        self._actions()

    def _edges_changed(self):
        if self._updating_bounds:
            return
        bounds = self._pixel_bounds()
        if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
            self.canvas.set_bounds(bounds)
        self._invalidate_preview()
        self._actions()

    def _discard(self, preview):
        if isinstance(preview, Mapping) and preview.get("preview_id"):
            try:
                self.facade.personal_visual_question_discard_crop(preview["preview_id"])
            except (KeyError, TypeError, ValueError, RuntimeError, OSError):
                pass  # Never reuse the old local token even if cleanup failed.

    def _invalidate_preview(self):
        self._generation += 1
        previous, self._preview = self._preview, None
        self._discard(previous)
        self.impact_confirmed.setChecked(False)
        self.new_image.clear("范围已变化，请重新预览并检查边缘。")

    def _actions(self, *_):
        bounds = self._pixel_bounds()
        valid_box = (
            0 <= bounds[0] < bounds[2] <= self._size[0]
            and 0 <= bounds[1] < bounds[3] <= self._size[1]
        )
        ready = self._valid and valid_box and not self._busy and not self._closed
        self.preview_button.setEnabled(ready and bounds != self._initial_bounds)
        self.save_button.setEnabled(
            ready and self._preview is not None and self.impact_confirmed.isChecked()
        )
        self.impact_confirmed.setEnabled(ready and self._preview is not None)
        self.cancel_button.setEnabled(not self._saving)
        self.canvas.setEnabled(self._valid and not self._busy)
        self.reset_button.setEnabled(self._valid and not self._busy)
        for field in self.edges.values():
            field.setEnabled(self._valid and not self._busy)

    def _submit(self, label, operation, success, failure):
        self._task_id = "starting"

        def ready(value):
            self._task_id = None
            success(value)

        def failed(message):
            self._task_id = None
            failure(message)

        try:
            task_id = self.tasks.submit(
                label, operation, on_success=ready, on_failure=failed
            )
            if self._task_id == "starting":
                self._task_id = task_id
        except (RuntimeError, TypeError):
            failed("本地裁剪任务暂时无法启动，请重试。")

    def _request_preview(self):
        if not self.preview_button.isEnabled():
            return
        try:
            bbox = normalised_box(self._pixel_bounds(), *self._size)
        except (TypeError, ValueError) as exc:
            set_status(self.status, "error", str(exc))
            return
        self._invalidate_preview()
        generation, expected_pixels = self._generation, self._pixel_bounds()
        self._busy = True
        self._actions()
        set_status(self.status, "info", "正在本机核对来源并生成新裁片；尚未保存。")
        options = self.options
        self._submit(
            "预览个人图片题新裁片",
            lambda: self.facade.personal_visual_question_preview_crop(
                options["batch_id"],
                options["key"],
                options["revision"],
                options["image_id"],
                bbox,
                expected_crop_revision=options["crop_revision"],
            ),
            lambda value: self._preview_ready(generation, expected_pixels, value),
            self._preview_failed,
        )

    def _preview_ready(self, generation, expected_pixels, value):
        self._busy = False
        if self._closed or generation != self._generation:
            self._discard(value)
            self._actions()
            return
        try:
            if (
                not isinstance(value, Mapping)
                or not value["preview_id"]
                or not value["preview_revision"]
            ):
                raise ValueError("Missing frozen preview")
            if any(
                value[key] != self.options[key]
                for key in (
                    "batch_id",
                    "key",
                    "revision",
                    "image_id",
                    "page_sha256",
                    "role",
                    "crop_revision",
                )
            ):
                raise ValueError("Preview target changed")
            if set(value["affected_question_keys"]) != set(
                self.options["affected_question_keys"]
            ):
                raise ValueError("Preview affected scope changed")
            if _bbox_pixels(value["new_bbox"], *self._size) != expected_pixels:
                raise ValueError("Preview bounds changed")
            self.new_image.set_bytes(
                value["preview_image"]["bytes"], "新裁片：请检查四周文字和图形是否完整"
            )
            if (self.new_image._source.width(), self.new_image._source.height()) != (
                expected_pixels[2] - expected_pixels[0],
                expected_pixels[3] - expected_pixels[1],
            ):
                raise ValueError("Preview pixel dimensions changed")
        except (KeyError, TypeError, ValueError, RuntimeError):
            self._discard(value)
            self._preview_failed("新裁片与所选范围或关联题不一致，请重新读取图片。")
            return
        self._preview = deepcopy(dict(value))
        self.previews.setCurrentIndex(0)
        set_status(
            self.status,
            "info",
            f"新裁片已显示，影响 {len(value['affected_question_keys'])} 道题。请放大检查边缘，再勾选确认；尚未保存。",
        )
        self._actions()

    def _preview_failed(self, message):
        self._busy = False
        self._invalidate_preview()
        if not self._closed:
            set_status(self.status, "error", str(message) or "新裁片暂时无法核对。")
        self._actions()

    def _save(self):
        if not self.save_button.isEnabled() or self._preview is None:
            return
        plan = self._preview
        self._busy = self._saving = True
        self._actions()
        set_status(self.status, "info", "正在保存已核对的裁剪范围；请稍候。")
        self._submit(
            "保存个人图片题裁剪修订",
            lambda: self.facade.personal_visual_question_save_crop(
                plan["preview_id"], plan["preview_revision"], confirmed=True
            ),
            self._saved,
            self._save_failed,
        )

    def _saved(self, value):
        self._busy = self._saving = False
        if not isinstance(value, Mapping) or not isinstance(
            value.get("revision_changes"), list
        ):
            self._save_failed(
                "裁剪保存结果暂时无法核对，请返回刷新题目；不会自动重试。"
            )
            return
        self.saved_result = deepcopy(dict(value))
        self.done(QDialog.DialogCode.Accepted)

    def _save_failed(self, message):
        self._saving = False
        self._preview_failed(message)

    def done(self, result):
        if result == QDialog.DialogCode.Accepted and self.saved_result is None:
            return
        if self._saving:
            return
        self._closed = True
        self._discard(self._preview)
        self._preview = None
        self.current_image.close_zoom()
        self.new_image.close_zoom()
        super().done(result)

    def reject(self):
        if self._saving:
            set_status(self.status, "attention", "正在保存，请等待结果后再关闭。")
            return
        if self._task_id and callable(getattr(self.tasks, "cancel", None)):
            self.tasks.cancel(self._task_id)
        self.done(QDialog.DialogCode.Rejected)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            orientation = (
                Qt.Orientation.Vertical
                if self.width() < 760
                else Qt.Orientation.Horizontal
            )
            if self.splitter.orientation() != orientation:
                self.splitter.setOrientation(orientation)
                self.splitter.setSizes([400, 260])
