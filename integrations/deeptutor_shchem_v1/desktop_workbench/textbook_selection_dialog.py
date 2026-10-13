"""Inspect existing textbook choices without importing or changing their source."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtWidgets import (QBoxLayout, QDialog, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout)


class TextbookSelectionDialog(QDialog):
    remove_requested = Signal(object)
    locate_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('本次教材选材')
        self.setMinimumSize(300, 260)
        self._active = True
        self._busy = False
        layout = QVBoxLayout(self)
        self.summary = QLabel('已选 0 项')
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(0)
        layout.addWidget(self.summary)
        self.items = QListWidget()
        self.items.setMinimumSize(0, 0)
        self.items.setWordWrap(True)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setAccessibleName('跨章节勾选的教材知识、研读提示和图表及原页位置')
        self.items.currentItemChanged.connect(self._controls)
        layout.addWidget(self.items, 1)
        note = QLabel('移除只取消本次勾选。定位原项后可继续核对；加入备课仍需明确确认。')
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        note.setMinimumWidth(0)
        layout.addWidget(note)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.locate_button = QPushButton('定位所选素材')
        self.remove_button = QPushButton('移除这一项')
        self.close_button = QPushButton('关闭')
        self.locate_button.clicked.connect(lambda: self._request(locate=True))
        self.remove_button.clicked.connect(lambda: self._request(locate=False))
        self.close_button.clicked.connect(self.reject)
        for button in (self.locate_button, self.remove_button, self.close_button):
            self.actions.addWidget(button)
        layout.addLayout(self.actions)
        self.finished.connect(self._finished)
        self._controls()

    def _finished(self, _result):
        self._active = False
        self._controls()

    def set_rows(self, rows):
        current = self.items.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        old_index = self.items.currentRow()
        with QSignalBlocker(self.items):
            self.items.clear()
            selected_index = None
            for index, entry in enumerate(rows):
                item = QListWidgetItem(entry['title'] + '\n' + entry['location'])
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(entry))
                item.setToolTip(item.text())
                self.items.addItem(item)
                if selected is not None and (entry['kind'], entry['selection']) == (selected['kind'], selected['selection']):
                    selected_index = index
            if rows:
                self.items.setCurrentRow(selected_index if selected_index is not None else min(max(old_index, 0), len(rows)-1))
        self.summary.setText(f'已选 {len(rows)} 项 · 跨章节保留')
        self._controls()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._controls()

    def _controls(self, *_):
        usable = self._active and not self._busy
        current = self.items.currentItem()
        entry = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.items.setEnabled(usable)
        self.remove_button.setEnabled(usable and entry is not None)
        self.locate_button.setEnabled(usable and entry is not None and entry['can_locate'])

    def _request(self, *, locate):
        if not self._active or self._busy or self.items.currentItem() is None:
            return
        entry = self.items.currentItem().data(Qt.ItemDataRole.UserRole)
        if locate and not entry['can_locate']:
            return
        (self.locate_requested if locate else self.remove_requested).emit(deepcopy(entry))

    def resizeEvent(self, event):
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 500
                                  else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)
