"""Read a whole lecture and choose an exact original image without importing it."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from ..desktop_handout_images import handout_image_preview
from .tasks import DesktopTaskBridge
from .word_lesson_reader import WordLessonReader


class HandoutImageReader(QDialog):
    def __init__(self, facade, snapshot, parent=None, *, tasks=None):
        super().__init__(parent)
        self.facade = facade
        self.rows = {row['asset_id']: deepcopy(row) for row in snapshot['images']}
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._job = None; self._epoch = 0; self._closed = False
        self.current = None; self.selection = None; self._previewed = None
        self._pixmap = None; self._zoom = None
        self.setWindowTitle('通读讲义与选择原图')
        self.resize(1000, 850); self.setMinimumSize(360, 520)
        root = QVBoxLayout(self)
        hint = QLabel('沿原文阅读知识结构和讲解，点击图名查看原图；选用后返回图题与课堂用途。')
        hint.setWordWrap(True); hint.setMinimumWidth(0); root.addWidget(hint)
        self.reader = WordLessonReader(self, image_selection_only=True)
        self.reader.set_source(snapshot['preview'])
        root.addWidget(self.reader, 1)
        self.status = QLabel('正在读取所选原图…')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setMinimumWidth(0); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm.setText('选用当前原图'); self.confirm.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('返回选图')
        buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self.reader.image_requested.connect(self.show_image)
        self.reader.image_zoom_requested.connect(self.zoom_image)
        self.show_image(snapshot['source']['asset_id'])

    def show_image(self, asset_id):
        if self._closed or asset_id not in self.rows: return
        self._epoch += 1; epoch = self._epoch
        if self._job is not None: self.tasks.cancel(self._job); self._job = None
        self.close_zoom(); self._previewed = None; self._pixmap = None; self.confirm.setEnabled(False)
        self.current = deepcopy(self.rows[asset_id]); row = deepcopy(self.current)
        self.reader.show_image(asset_id, None, '正在读取完整原图…')
        self.reader.scroll_to_block(row['block_index']); self.status.setText('正在读取完整原图…')
        self._job = self.tasks.submit('在讲义原文中读取原图', lambda: handout_image_preview(self.facade, row),
            on_success=lambda value: self.ready(epoch, row, value),
            on_failure=lambda message: self.failed(epoch, row, message))

    def ready(self, epoch, row, value):
        if self._closed or epoch != self._epoch or row != self.current: return
        self._job = None
        if value['source'] != row:
            self.failed(epoch, row, '原图记录已变化，请返回重新选图。'); return
        pixmap = QPixmap()
        if not pixmap.loadFromData(value['bytes']):
            self.failed(epoch, row, '原图暂未显示，请返回重新选图。'); return
        self._pixmap = pixmap; self._previewed = deepcopy(row)
        self.reader.show_image(row['asset_id'], pixmap, '', derived=value.get('derived_preview') is True)
        self.reader.scroll_to_block(row['block_index'])
        display = ' · 本地转换显示' if value.get('derived_preview') is True else ''
        self.status.setText(f"当前原图 · 区块{row['block_index']}{display} · {pixmap.width()} × {pixmap.height()} 像素")
        self.confirm.setEnabled(True)

    def failed(self, epoch, row, message):
        if self._closed or epoch != self._epoch or row != self.current: return
        self._job = None; self._previewed = None; self._pixmap = None
        self.confirm.setEnabled(False); self.reader.show_image(row['asset_id'], None, message)
        self.status.setText(message)

    def choose(self):
        if self._closed or self._previewed is None or not self.confirm.isEnabled(): return
        self.selection = deepcopy(self._previewed); self.accept()

    def zoom_image(self):
        if self._closed or self._pixmap is None: return
        from .library_detail import ImageZoomDialog
        self.close_zoom()
        self._zoom = ImageZoomDialog(self._pixmap, self.current['source_name'], self)
        self._zoom.show()

    def close_zoom(self):
        if self._zoom is not None: self._zoom.close(); self._zoom = None

    def done(self, result):
        if not self._closed:
            self._closed = True; self._epoch += 1
            if self._job is not None: self.tasks.cancel(self._job); self._job = None
            self.close_zoom()
            if self._own_tasks: self.tasks.shutdown(1000)
        super().done(result)
