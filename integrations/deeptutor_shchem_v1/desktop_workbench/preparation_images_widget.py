"""Native teacher-selected image assets for the preparation workflow.

The preparation model receives only the teacher-authored metadata for these
assets.  The image bytes stay in the local application asset store managed by
the facade.  This module deliberately has no network, browser, preview, or
image-decoding surface: the first UI milestone only needs a clear, reviewable
asset list and a reliable hand-off to ``DesktopWorkbenchFacade``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_images import MAX_IMAGES, normalize_image_assets
from .components import set_status

_ASSET_KEYS = (
    "asset_id",
    "sha256",
    "caption",
    "source",
    "purpose",
    "width",
    "height",
    "content_type",
)
_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
    }
)


def _normalise_asset(value: object) -> dict[str, Any] | None:
    """Keep only the public metadata contract used by the preparation payload.

    In particular, a facade implementation or an old draft may contain an
    internal path, a temporary file handle, or other private values.  Those
    are intentionally discarded before the object reaches ``_payload``.
    """

    if not isinstance(value, Mapping):
        return None
    asset_id = str(value.get("asset_id", "")).strip()
    sha256 = str(value.get("sha256", "")).strip()
    caption = str(value.get("caption", "")).strip()
    source = str(value.get("source", "")).strip()
    purpose = str(value.get("purpose", "")).strip()
    content_type = str(value.get("content_type", "")).strip().lower()
    if (
        not asset_id.startswith("IMG-")
        or not sha256
        or not caption
        or not source
        or not purpose
        or content_type not in _IMAGE_CONTENT_TYPES
    ):
        return None
    try:
        width = int(value.get("width", 0))
        height = int(value.get("height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "asset_id": asset_id,
        "sha256": sha256,
        "caption": caption,
        "source": source,
        "purpose": purpose,
        "width": width,
        "height": height,
        "content_type": content_type,
    }


class PreparationImageMetadataDialog(QDialog):
    """Collect the three teacher-authored fields required for one image."""

    def __init__(
        self,
        file_path: str,
        parent: QWidget | None = None,
        *,
        caption: str = "",
        source: str = "",
        purpose: str = "",
        display_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加教学图片说明")
        self.setModal(True)
        self.resize(460, 260)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        file_name = display_name or Path(file_path).name or "已选择图片"
        selected = QLabel(f"已选择：{file_name}")
        selected.setObjectName("CardTitle")
        selected.setWordWrap(True)
        selected.setAccessibleName(f"已选择图片：{file_name}")
        root.addWidget(selected)

        hint = QLabel(
            "请填写给模型和课堂使用的文字说明。图片像素仍只保留在本机；"
            "这些字段会随确认后的备课文字发送。"
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(9)
        self.caption_input = QLineEdit()
        self.caption_input.setPlaceholderText("例如：教材图 2.14 氯化钠电离过程示意图")
        self.caption_input.setAccessibleName("图片图注")
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText("例如：沪科技化学必修第一册，第 57 页")
        self.source_input.setAccessibleName("图片来源")
        self.purpose_input = QLineEdit()
        self.purpose_input.setPlaceholderText("例如：解释溶于水与熔融时的自由移动离子")
        self.purpose_input.setAccessibleName("图片教学用途")
        self.caption_input.setText(caption)
        self.source_input.setText(source)
        self.purpose_input.setText(purpose)
        for editor in (self.caption_input, self.source_input, self.purpose_input):
            editor.setCursorPosition(0)
            editor.setToolTip(editor.text())
        form.addRow("图注", self.caption_input)
        form.addRow("来源", self.source_input)
        form.addRow("教学用途", self.purpose_input)
        root.addLayout(form)

        self.status = QLabel("")
        self.status.setObjectName("MutedLabel")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("图片说明填写状态")
        root.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm_button.setText("导入图片")
        self.confirm_button.setAccessibleName("确认导入教学图片")
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        cancel_button.setAccessibleName("取消导入教学图片")
        buttons.accepted.connect(self._accept_if_complete)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def caption(self) -> str:
        return self.caption_input.text().strip()

    @property
    def source(self) -> str:
        return self.source_input.text().strip()

    @property
    def purpose(self) -> str:
        return self.purpose_input.text().strip()

    def _accept_if_complete(self) -> None:
        if len(self.caption) > 160 or len(self.source) > 500 or len(self.purpose) > 500:
            set_status(
                self.status,
                "attention",
                "图注最多160字，来源和教学用途各最多500字。请精简后确认，原文未被截断。",
            )
            return
        missing: list[str] = []
        if not self.caption:
            missing.append("图注")
        if not self.source:
            missing.append("来源")
        if not self.purpose:
            missing.append("教学用途")
        if missing:
            set_status(
                self.status,
                "attention",
                "请补充：" + "、".join(missing) + "。图片尚未导入。",
            )
            for editor in (
                self.caption_input,
                self.source_input,
                self.purpose_input,
            ):
                if not editor.text().strip():
                    editor.setFocus()
                    break
            return
        self.accept()


class PreparationImagesWidget(QWidget):
    """Teacher-controlled list of local image metadata for one preparation."""

    MAX_ASSETS = MAX_IMAGES
    privacy_notice_text = (
        "本次不发送图片像素：图片像素只保留在本机素材库；"
        "模型仅收到你填写的图注、来源和用途，不会直接看到图片。"
    )
    assets_changed = Signal(object)

    def __init__(self, facade: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.facade = facade
        self._assets: list[dict[str, Any]] = []
        self._editing_enabled = True
        self.setObjectName("PreparationImagesWidget")
        self.setAccessibleName("备课教学图片")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(8)

        title = QLabel(f"7　本次课使用的教学图片（可选，最多 {self.MAX_ASSETS} 张）")
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        title.setAccessibleName("本次课使用的教学图片")
        root.addWidget(title)

        self.privacy_label = QLabel(self.privacy_notice_text)
        self.privacy_label.setObjectName("MutedLabel")
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setAccessibleName("图片本地保存与模型发送说明")
        root.addWidget(self.privacy_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.add_button = QPushButton("选择图片…")
        self.add_button.setObjectName("QuietButton")
        self.add_button.setAccessibleName("选择本地教学图片")
        self.add_button.clicked.connect(self._choose_image)
        self.add_image_button = self.add_button
        self.remove_button = QPushButton("移除选中图片")
        self.remove_button.setObjectName("QuietButton")
        self.remove_button.setAccessibleName("移除选中的教学图片")
        self.remove_button.clicked.connect(self._remove_selected)
        self.remove_image_button = self.remove_button
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        root.addLayout(actions)

        self.asset_list = QListWidget()
        self.assets_list = self.asset_list
        self.asset_list.setAccessibleName("已添加教学图片列表")
        self.asset_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.asset_list.setMinimumHeight(72)
        self.asset_list.setMaximumHeight(190)
        self.asset_list.setWordWrap(True)
        self.asset_list.itemSelectionChanged.connect(self._update_controls)
        root.addWidget(self.asset_list)

        self.status = QLabel("尚未添加教学图片。")
        self.status.setObjectName("MutedLabel")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("教学图片列表状态")
        root.addWidget(self.status)
        self._update_controls()

    def assets(self) -> list[dict[str, Any]]:
        """Return a payload-safe copy with no local path or image bytes."""

        return [dict(asset) for asset in self._assets]

    def set_assets(self, values: Iterable[object] | None) -> None:
        """Replace the list when loading a draft, including clearing stale data."""

        incoming = [] if values is None else values
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in incoming:
            asset = _normalise_asset(value)
            if asset is None or asset["asset_id"] in seen:
                continue
            seen.add(asset["asset_id"])
            cleaned.append(asset)
            if len(cleaned) >= self.MAX_ASSETS:
                break
        self._assets = cleaned
        self._render_assets()
        self.assets_changed.emit(self.assets())

    def clear_assets(self) -> None:
        self.set_assets(())

    def set_assets_strict(self, values: object) -> None:
        """Validate a complete batch before replacing anything; never drop rows."""
        cleaned = normalize_image_assets(values)
        self._assets = cleaned
        self._render_assets()
        self.assets_changed.emit(self.assets())

    def append_asset(self, value: object) -> bool:
        """Add one confirmed asset without replacing the teacher's existing list."""
        asset = _normalise_asset(value)
        if asset is None:
            set_status(self.status, "error", "图片导入结果不完整，原有图片保留。")
            return False
        if any(a["sha256"] == asset["sha256"] for a in self._assets):
            set_status(self.status, "attention", "这张图片已经添加，无需重复导入。")
            return False
        if len(self._assets) >= self.MAX_ASSETS:
            set_status(
                self.status,
                "attention",
                f"最多添加{self.MAX_ASSETS}张教学图片，请先移除一张。",
            )
            return False
        self._assets.append(asset)
        self._render_assets()
        self.assets_changed.emit(self.assets())
        set_status(
            self.status,
            "success",
            f"已添加“{asset['caption']}”；图片像素仍保留在本机。",
        )
        return True

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = bool(enabled)
        self.asset_list.setEnabled(self._editing_enabled)
        self._update_controls()

    def is_editing_enabled(self) -> bool:
        return self._editing_enabled

    def confirmation_text(self) -> str:
        return self.privacy_notice_text

    def _choose_image(self) -> None:
        if not self._editing_enabled:
            return
        if len(self._assets) >= self.MAX_ASSETS:
            set_status(
                self.status,
                "attention",
                f"最多添加 {self.MAX_ASSETS} 张教学图片，请先移除一张。",
            )
            return
        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择本地教学图片",
            "",
            "图像文件 (*.png *.jpg *.jpeg *.webp);;PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp)",
        )
        if not file_path:
            return
        dialog = PreparationImageMetadataDialog(file_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            imported = self.facade.import_preparation_image(
                file_path,
                dialog.caption,
                dialog.source,
                dialog.purpose,
            )
            asset = _normalise_asset(imported)
        except Exception:  # noqa: BLE001 - do not leak local/provider diagnostics
            set_status(
                self.status,
                "error",
                "图片导入失败，原有图片仍保留。请检查文件后重试。",
            )
            return
        if asset is None:
            set_status(
                self.status,
                "error",
                "图片导入结果不完整，原有图片仍保留。请稍后重试。",
            )
            return
        if any(
            asset["asset_id"] == existing["asset_id"]
            or asset["sha256"] == existing["sha256"]
            for existing in self._assets
        ):
            set_status(self.status, "attention", "这张图片已经添加，无需重复导入。")
            return
        self._assets.append(asset)
        self._render_assets()
        self.assets_changed.emit(self.assets())
        set_status(
            self.status,
            "success",
            f"已添加“{asset['caption']}”；图片像素仍保留在本机。",
        )

    def _remove_selected(self) -> None:
        if not self._editing_enabled:
            return
        row = self.asset_list.currentRow()
        if row < 0 or row >= len(self._assets):
            return
        removed = self._assets.pop(row)
        self._render_assets()
        self.assets_changed.emit(self.assets())
        set_status(
            self.status, "success", f"已移除“{removed['caption']}”；可重新选择图片。"
        )

    def _render_assets(self) -> None:
        self.asset_list.blockSignals(True)
        try:
            self.asset_list.clear()
            for asset in self._assets:
                item = QListWidgetItem(
                    f"{asset['caption']}\n"
                    f"来源：{asset['source']}\n用途：{asset['purpose']}"
                )
                item.setData(Qt.ItemDataRole.UserRole, asset["asset_id"])
                item.setToolTip(
                    f"{asset['asset_id']}\n"
                    f"{asset['caption']}\n"
                    f"来源：{asset['source']}\n"
                    f"用途：{asset['purpose']}"
                )
                self.asset_list.addItem(item)
        finally:
            self.asset_list.blockSignals(False)
        if self._assets:
            self.asset_list.setCurrentRow(0)
            self.status.setText(f"已添加 {len(self._assets)} 张教学图片。")
        else:
            self.status.setText("尚未添加教学图片。")
        self._update_controls()

    def _update_controls(self) -> None:
        can_edit = self._editing_enabled
        self.add_button.setEnabled(can_edit and len(self._assets) < self.MAX_ASSETS)
        self.remove_button.setEnabled(can_edit and self.asset_list.currentRow() >= 0)


__all__ = [
    "PreparationImageMetadataDialog",
    "PreparationImagesWidget",
]
