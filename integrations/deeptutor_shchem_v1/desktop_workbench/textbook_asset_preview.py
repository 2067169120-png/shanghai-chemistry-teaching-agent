"""Lazy, bounded original-page preview; viewing never selects a teaching image."""
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import io
import json

from PySide6.QtCore import Qt, QSize, Signal, QSignalBlocker
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .tasks import DesktopTaskBridge


def asset_selection(row):
    if not isinstance(row, dict):
        return None
    if isinstance(row.get('selection'), dict):
        return deepcopy(row['selection'])
    if all(row.get(k) for k in ('visual_asset_id', 'revision')):
        return {k: row[k] for k in ('visual_asset_id', 'revision')}
    return None


def _key(selection):
    return json.dumps(selection, ensure_ascii=False, sort_keys=True)


def page_thumbnail(facade, selection):
    """Recheck source on every selection, retaining only a small display raster."""
    from PIL import Image
    page = facade.preparation_textbook_crop_page(selection)
    if any(page.get(k) != selection.get(k) for k in ('visual_asset_id', 'revision')):
        raise ValueError('教材原页版本已变化，请重新选择。')
    data = page['page_data']
    if sha256(data).hexdigest() != page['recipe']['raster_sha256']:
        raise ValueError('教材原页图像无法读取。')
    with Image.open(io.BytesIO(data)) as original:
        if original.size != (page['width'], page['height']):
            raise ValueError('教材原页尺寸不一致。')
        original.thumbnail((640, 960), Image.Resampling.LANCZOS)
        with original.convert('RGB') as raster:
            stream = io.BytesIO()
            raster.save(stream, format='PNG')
    return {'selection': deepcopy(selection), 'data': stream.getvalue(),
            'pdf_page': page['pdf_page'], 'printed_page': page['printed_page'],
            'source_title': page['source_title'], 'source_sha256': page['source_sha256'],
            'page_revision': page['page_revision']}


class _PageCanvas(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.setMinimumSize(0, 120)
        self.setAccessibleName('所选教材完整原页预览，尚未框选')

    def sizeHint(self):
        return QSize(320, 250)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        if self.pixmap.isNull():
            return
        target = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap((self.width()-target.width())//2, (self.height()-target.height())//2,
                           target.width(), target.height(), self.pixmap)


class TextbookAssetPreview(QWidget):
    thumbnail_ready = Signal(object, object)

    def __init__(self, facade, parent=None, *, tasks=None):
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._epoch = 0
        self._task = None
        self._row = None
        self._selection = None
        self.receipt = None
        self._icons = OrderedDict()
        self._list = None
        self.setMinimumWidth(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel('教材原页预览')
        self.heading.setWordWrap(True)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setMinimumWidth(0)
        root.addWidget(self.heading)
        self.canvas = _PageCanvas(self)
        root.addWidget(self.canvas, 1)
        self.status = QLabel('选择图表，查看所在原页。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        root.addWidget(self.status)
        if hasattr(self.tasks, 'task_cancelled'):
            self.tasks.task_cancelled.connect(self._cancelled)

    def bind_list(self, items):
        self._list = items
        items.setIconSize(QSize(80, 108))
        items.currentItemChanged.connect(lambda item, *_: self.set_row(
            item.data(Qt.ItemDataRole.UserRole) if item else None))

    def set_row(self, row):
        self._epoch += 1
        if self._task is not None:
            self.tasks.cancel(self._task)
            self._task = None
        self._row = deepcopy(row)
        self._selection = asset_selection(row)
        if self._selection is not None:
            self._icons.pop(_key(self._selection), None)
            self._apply_icons()
        self.receipt = None
        self.canvas.pixmap = QPixmap()
        self.canvas.update()
        self.heading.setText('教材原页预览' + (' · '+str(row.get('label', '')) if row else ''))
        self.status.setText('选择图表，查看所在原页。' if row is None else
                            '选择PDF页码后可框选原页。' if self._selection is None else '正在读取教材原页…')
        if self.isVisible():
            self._load()

    def _load(self):
        if self._closed or self._selection is None or self._task is not None or self.receipt is not None:
            return
        if not callable(getattr(self.facade, 'preparation_textbook_crop_page', None)):
            self.status.setText('可点击原页入口查看教材。')
            return
        epoch, selection = self._epoch, deepcopy(self._selection)
        def ready(value):
            if self._closed or epoch != self._epoch:
                return
            self._task = None
            try:
                if value['selection'] != self._selection:
                    raise ValueError('教材原页与所选图表不一致。')
                pixmap = QPixmap()
                if not pixmap.loadFromData(value['data']) or pixmap.width() > 640 or pixmap.height() > 960:
                    raise ValueError('教材原页预览无法显示。')
                self.receipt = {k:v for k,v in value.items() if k != 'data'}
                self.canvas.pixmap = pixmap
                self.canvas.update()
                printed = value['printed_page']
                self.status.setText(f"PDF第{value['pdf_page']}页" +
                    (f' · 印刷第{printed}页' if type(printed) is int else '') + ' · 整页预览，框选后加入备课。')
                self._remember_icon(selection, pixmap)
                self.thumbnail_ready.emit(deepcopy(selection), pixmap)
            except (ValueError, TypeError, KeyError) as exc:
                self.status.setText(str(exc))
        def failed(message):
            if not self._closed and epoch == self._epoch:
                self._task = None
                self.status.setText(message)
        try:
            self._task = self.tasks.submit('预览教材图表原页', lambda: page_thumbnail(self.facade, selection),
                                           on_success=ready, on_failure=failed)
        except RuntimeError as exc:
            failed(str(exc))

    def _remember_icon(self, selection, pixmap):
        key = _key(selection)
        self._icons[key] = QIcon(pixmap.scaled(80, 108, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        self._icons.move_to_end(key)
        while len(self._icons) > 8:
            self._icons.popitem(last=False)
        self._apply_icons()

    def _apply_icons(self):
        if self._list is not None:
            # Icon updates are display-only and must not trigger checked-material handlers.
            with QSignalBlocker(self._list):
                for index in range(self._list.count()):
                    item = self._list.item(index)
                    current = asset_selection(item.data(Qt.ItemDataRole.UserRole))
                    item.setIcon(self._icons.get(_key(current), QIcon()))
            current = self._list.currentItem()
            if current is not None:
                self._list.scrollToItem(current)

    def _cancelled(self, task_id, *_):
        if task_id == self._task and not self._closed:
            self._task = None
            self.status.setText('原页预览已取消，重新选择可继续。')

    def showEvent(self, event):
        super().showEvent(event)
        self._load()

    def release(self):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        if self._task is not None:
            self.tasks.cancel(self._task)
            self._task = None
        self._icons.clear()
        self.canvas.pixmap = QPixmap()
        self.receipt = None
        if self._own_tasks:
            self.tasks.shutdown(1000)
