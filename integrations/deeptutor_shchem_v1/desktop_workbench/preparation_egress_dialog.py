"""Consent with locally verified pixels from the pending request snapshot."""

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_images import (
    normalize_image_assets,
    verify_image_bytes,
)
from .preparation_images_widget import _LocalImagePreview


def needs_scrollable_confirmation(text: str, image_count: object = 0) -> bool:
    # Even a single sending picture needs actual pixels, not a text-only prompt.
    return len(text) > 1800 or (type(image_count) is int and image_count > 0)


class PreparationEgressDialog(QDialog):
    """Show every sending image; block consent on missing or changed content.

    Keep only small thumbnail icons and one selected full-size raster in memory.
    Validate one image per event-loop turn so cancellation remains available.
    No provider, credential, network or mutable form state is consulted here.
    """

    def __init__(
        self,
        title: str,
        text: str,
        *,
        image_count: int = 0,
        image_assets: object = None,
        image_loader: Callable[[dict], bytes] | None = None,
        local_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("PreparationEgressDialog")
        self.setModal(True)
        self._assets: list[dict] = []
        self._image_loader = image_loader
        self._failed: set[int] = set()
        self._manifest_error = False
        self._cursor = 0
        self._rechecking = False
        self._ready = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._check_next_image)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        heading = QLabel("发送前核对" if not local_only else "本地导出确认")
        heading.setObjectName("PageTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.summary = QLabel(
            f"{image_count} 张图片 · 点击缩略图查看，放大检查公式与裁剪边界"
            if image_count else "完整发送范围与费用说明"
            if not local_only
            else "不调用模型 · 不发送文字或图片"
        )
        self.summary.setObjectName("MutedLabel")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.disclosure = QPlainTextEdit()
        self.disclosure.setAccessibleName("完整发送范围、模型和费用说明")
        self.disclosure.setReadOnly(True)
        self.disclosure.setTabChangesFocus(True)
        self.disclosure.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        # Never render a source caption as HTML or silently abbreviate its text.
        self.disclosure.setPlainText(text)
        self.disclosure.setMinimumHeight(100)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(0)
        self.image_list = QListWidget()
        self.image_list.setAccessibleName("本次实际发送的图片缩略图列表")
        self.image_list.setIconSize(QSize(112, 72))
        self.image_list.setWordWrap(True)
        self.image_list.setMinimumWidth(150)
        self.image_list.currentRowChanged.connect(self._select_image)
        self.image_preview = _LocalImagePreview()
        self.image_preview.image.setMinimumHeight(120)
        self.image_preview.image.setMaximumHeight(16777215)
        self.image_preview.image.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.image_preview.layout().setStretch(0, 1)
        self.image_details = QPlainTextEdit()
        self.image_details.setAccessibleName("当前图片完整图注、来源、用途及尺寸")
        self.image_details.setReadOnly(True)
        self.image_details.setTabChangesFocus(True)
        self.image_details.setFixedHeight(96)
        detail_panel = QWidget()
        details_layout = QVBoxLayout(detail_panel)
        details_layout.setContentsMargins(8, 0, 0, 0)
        details_layout.addWidget(self.image_preview, 1)
        details_layout.addWidget(self.image_details)
        gallery = QSplitter(Qt.Orientation.Horizontal, self)
        self.gallery = gallery
        gallery.setChildrenCollapsible(False)
        gallery.addWidget(self.image_list)
        gallery.addWidget(detail_panel)
        gallery.setStretchFactor(0, 0)
        gallery.setStretchFactor(1, 1)
        gallery.setSizes([240, 580])
        if image_count:
            self.tabs.addTab(gallery, f"图片预览（{image_count}）")
        else:
            gallery.hide()
        self.tabs.addTab(self.disclosure, "发送范围与费用")
        layout.addWidget(self.tabs, 1)
        self.validation_status = QLabel()
        self.validation_status.setWordWrap(True)
        self.validation_status.setTextFormat(Qt.TextFormat.PlainText)
        self.validation_status.setAccessibleName("图片内容核对状态")
        layout.addWidget(self.validation_status)

        reminder = QLabel(
            "调用可能产生费用，重试可能再次计费。确认前不会调用模型；取消不会发送新请求。"
            if not local_only
            else "仅使用已保存的冻结候选继续导出。"
        )
        reminder.setObjectName("MutedLabel")
        reminder.setWordWrap(True)
        layout.addWidget(reminder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText("取消")
        self.cancel_button.setObjectName("QuietButton")
        self.confirm_button = buttons.addButton(
            "确认调用模型" if not local_only else "继续本地导出",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.cancel_button.setFocus()

        available = self.screen().availableGeometry()
        self.resize(min(980, available.width() - 32), min(720, available.height() - 48))
        try:
            if type(image_count) is not int or image_count < 0:
                raise ValueError("Invalid image count")
            self._assets = self._normalize_assets([] if image_assets is None else image_assets)
            if len(self._assets) != image_count or (local_only and image_count):
                raise ValueError("Incomplete sending snapshot")
            if self._assets and not callable(image_loader):
                raise ValueError("No local image reader")
        except (TypeError, ValueError):
            self._manifest_error = True
            self.confirm_button.setEnabled(False)
            self.validation_status.setText("图片发送清单不完整，不能确认。请取消并重新预览。")
            return
        if not self._assets:
            self._ready = True
            self.validation_status.hide()
            return
        self.confirm_button.setEnabled(False)
        for index, asset in enumerate(self._assets, 1):
            item = QListWidgetItem(f"{index:02d}  {asset['caption']}")
            item.setToolTip(asset["caption"])
            item.setSizeHint(QSize(200, 96))
            self.image_list.addItem(item)
        self.validation_status.setText(f"正在读取本次发送的图片：0 / {image_count}；尚未发送。")
        self._timer.start(0)

    def _normalize_assets(self, assets: object) -> list[dict]:
        return normalize_image_assets(assets)

    def _read_image(self, index: int) -> bytes:
        asset = self._assets[index]
        # The reader receives a copy, never the consent snapshot itself.
        data = self._image_loader(dict(asset))
        return verify_image_bytes(asset, data)

    def _fail_image(self, index: int) -> None:
        self._failed.add(index)
        if self.image_list.currentRow() == index:
            self.image_preview.clear("本次图片无法读取或已经变化，请取消后重新选择。")
        item = self.image_list.item(index)
        item.setIcon(QIcon())
        item.setText(f"{index + 1:02d}  无法核对 · {self._assets[index]['caption']}")
        self.confirm_button.setEnabled(False)
        self._ready = False
        self._show_status()

    def _show_status(self) -> None:
        if self._failed:
            numbers = "、".join(str(i + 1) for i in sorted(self._failed))
            self.validation_status.setText(
                f"第 {numbers} 张图片无法读取或内容已变化，不能确认发送。请取消并重新选择。"
            )
        elif self._ready:
            self.validation_status.setText(
                f"已载入 {len(self._assets)} 张原图。请逐张检查内容；确认前会再次核对文件是否变化。"
            )
        else:
            action = "确认文件未变化" if self._rechecking else "读取原图"
            self.validation_status.setText(
                f"正在{action}：{self._cursor} / {len(self._assets)}；尚未发送。"
            )

    def _check_next_image(self) -> None:
        if self._closed or self._manifest_error:
            return
        if self._cursor >= len(self._assets):
            self._ready = not self._failed
            self.confirm_button.setEnabled(self._ready)
            self._show_status()
            if self._rechecking and self._ready:
                self.done(QDialog.DialogCode.Accepted)
            return
        index = self._cursor
        try:
            data = self._read_image(index)
            if not self._rechecking:
                pixmap = QPixmap()
                if not pixmap.loadFromData(data):
                    raise ValueError("Qt cannot display this image")
                thumbnail = pixmap.scaled(
                    self.image_list.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.image_list.item(index).setIcon(QIcon(thumbnail))
        except Exception:  # noqa: BLE001 - untrusted local image readers fail closed
            self._fail_image(index)
        self._cursor += 1
        if not self._rechecking and index == 0:
            self.image_list.setCurrentRow(0)
        self._show_status()
        self._timer.start(0)

    def _select_image(self, index: int) -> None:
        if not 0 <= index < len(self._assets) or self._closed:
            return
        asset = self._assets[index]
        self.image_details.setPlainText(
            f"{index + 1} / {len(self._assets)} · {asset['caption']}\n"
            f"来源：{asset['source']}\n用途：{asset['purpose']}\n"
            f"原图：{asset['width']} × {asset['height']} 像素"
        )
        try:
            self.image_preview.set_bytes(self._read_image(index), asset["caption"])
        except Exception:  # noqa: BLE001 - never substitute a stale/blank preview
            self.image_preview.clear("本次图片无法读取或已经变化，请取消后重新选择。")
            self._fail_image(index)

    def accept(self) -> None:
        if self._manifest_error or not self._ready or self._closed:
            return
        if not self._assets:
            super().accept()
            return
        self._ready = False
        self._rechecking = True
        self._cursor = 0
        self.confirm_button.setEnabled(False)
        self._show_status()
        self._timer.start(0)

    def done(self, result: int) -> None:
        self._closed = True
        self._timer.stop()
        self.image_preview.close_zoom()
        super().done(result)
