"""Bounded raster caches and explicit page navigation for local previews."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import hashlib
import math

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem


class PixmapCache:
    """LRU with both a byte budget and an entry limit; oversized pages stay out."""

    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=4):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._items = OrderedDict()
        self.nbytes = 0

    @staticmethod
    def cost(pixmap):
        return pixmap.width() * pixmap.height() * max(4, math.ceil(pixmap.depth() / 8))

    def get(self, key, default=None):
        value = self._items.pop(key, None)
        if value is None:
            return default
        self._items[key] = value
        return value

    def put(self, key, pixmap):
        evicted = []
        old = self._items.pop(key, None)
        if old is not None:
            self.nbytes -= self.cost(old)
        cost = self.cost(pixmap)
        if cost > self.max_bytes or self.max_entries < 1:
            return [key]
        while self._items and (self.nbytes + cost > self.max_bytes or len(self._items) >= self.max_entries):
            expired, value = self._items.popitem(last=False)
            self.nbytes -= self.cost(value)
            evicted.append(expired)
        self._items[key] = pixmap
        self.nbytes += cost
        return evicted

    def clear(self):
        self._items.clear()
        self.nbytes = 0

    def values(self):
        return self._items.values()

    def __contains__(self, key):
        return key in self._items

    def __len__(self):
        return len(self._items)


def checked_page_pixmap(value, page):
    """Check the encoded header before allocating a full-size raster."""
    if not isinstance(value, Mapping):
        raise ValueError("本页图像无法读取。")
    raw = value.get("data", value.get("bytes"))
    mime = value.get("content_type", value.get("mime_type"))
    if (not isinstance(raw, bytes) or not raw or len(raw) > 32 * 1024 * 1024
            or mime != "image/png" or value.get("sha256") != page["sha256"]
            or hashlib.sha256(raw).hexdigest() != page["sha256"]):
        raise ValueError("本页图像与已保存分页不一致。")
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"png")
    size = reader.size()
    if ((size.width(), size.height()) != (page["width"], page["height"])
            or size.width() < 1 or size.height() < 1
            or size.width() * size.height() > 40_000_000):
        raise ValueError("本页图像尺寸与分页目录不一致。")
    image = reader.read()
    if image.isNull():
        raise ValueError("本页图像无法解码。")
    return QPixmap.fromImage(image)


def scaled_page(pixmap, width, pixel_ratio=1.0):
    # One display copy is enough. A large zoom must not allocate an unbounded
    # second raster; the original remains available for another zoom level.
    max_width = math.sqrt(16_000_000 * pixmap.width() / max(1, pixmap.height()))
    pixel_ratio = max(1.0, min(4.0, float(pixel_ratio)))
    width = max(1, min(round(width * pixel_ratio), round(max_width)))
    result = pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    result.setDevicePixelRatio(pixel_ratio)
    return result


class PageNavigator(QListWidget):
    """Horizontal strip: unvisited pages are labelled placeholders, never fake previews."""

    page_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("分页导航，缩略图仅来自已读取页面")
        self.setMinimumWidth(0)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(42, 44))
        self.setGridSize(QSize(106, 100))
        self.setFixedHeight(126)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setStyleSheet("QListWidget::item { padding: 3px; }")
        self.setSpacing(2)
        self._items, self._titles = {}, {}
        self._thumbnails = PixmapCache(4 * 1024 * 1024, 64)
        self.currentItemChanged.connect(self._selected)

    def set_pages(self, pages):
        self.blockSignals(True)
        self.clear_pages()
        for key, title in pages:
            item = QListWidgetItem(title + "\n未查看")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(title + "；选择后读取本页。显示不等于核对。")
            self.addItem(item)
            self._items[key], self._titles[key] = item, title
        self.setCurrentRow(-1)
        self.blockSignals(False)

    def _selected(self, item, _previous):
        if item is not None:
            key = item.data(Qt.ItemDataRole.UserRole)
            self.page_selected.emit(tuple(key) if isinstance(key, list) else key)

    def select_page(self, key):
        item = self._items.get(key)
        if item is not None:
            self.blockSignals(True)
            self.setCurrentItem(item)
            self.scrollToItem(item)
            self.blockSignals(False)

    def set_status(self, key, status):
        if key in self._items:
            self._items[key].setText(self._titles[key] + "\n" + status)
            self._items[key].setToolTip(self._titles[key] + " · " + status)

    def set_thumbnail(self, key, pixmap):
        if key not in self._items:
            return
        ratio = self.devicePixelRatioF()
        size = QSize(round(self.iconSize().width() * ratio), round(self.iconSize().height() * ratio))
        thumbnail = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        thumbnail.setDevicePixelRatio(ratio)
        for expired in self._thumbnails.put(key, thumbnail):
            if expired in self._items:
                self._items[expired].setIcon(QIcon())
        self._items[key].setIcon(QIcon(thumbnail))

    def clear_pages(self):
        self.clear()
        self._items.clear()
        self._titles.clear()
        self._thumbnails.clear()
