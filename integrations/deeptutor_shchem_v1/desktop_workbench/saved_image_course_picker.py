"""Paged, read-only course search before selecting a saved teaching picture."""
from copy import deepcopy
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout)
from ..desktop_saved_preparation_images import saved_preparation_image_page
from .tasks import DesktopTaskBridge


def _saved_time(row):
    value = row.get('created_at', '')
    try: return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, AttributeError): return str(value)


class SavedImageCoursePicker(QDialog):
    PAGE_SIZE = 25

    def __init__(self, facade, parent=None, *, tasks=None):
        super().__init__(parent)
        self.facade = facade; self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None; self._closed = False; self._task = None; self._epoch = 0
        self.offset = 0; self.total = 0; self.chosen = None
        self.setWindowTitle('从已有备课选图片'); self.resize(650, 660); self.setMinimumSize(360, 440)
        root = QVBoxLayout(self)
        intro = QLabel('搜索作品名称或原课题，选择一份备课，再预览其中的图片。')
        intro.setWordWrap(True); root.addWidget(intro)
        self.search = QLineEdit(); self.search.setMaxLength(300)
        self.search.setPlaceholderText('搜索作品名称或原课题'); self.search.setAccessibleName('搜索全部当前已有备课')
        root.addWidget(self.search)
        self.list = QListWidget(); self.list.setWordWrap(True); self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAccessibleName('已有备课搜索结果'); root.addWidget(self.list, 1)
        nav = QHBoxLayout(); self.previous = QPushButton('上一页'); self.next = QPushButton('下一页')
        self.page_label = QLabel(''); self.page_label.setWordWrap(True)
        nav.addWidget(self.previous); nav.addWidget(self.page_label, 1); nav.addWidget(self.next); root.addLayout(nav)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(150)
        self.detail.setAccessibleName('所选备课的图片数量与课题'); root.addWidget(self.detail)
        self.status = QLabel(''); self.status.setWordWrap(True); root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok); self.confirm_button.setText('预览这份备课的图片')
        self.confirm_button.setEnabled(False); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.choose); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self._debounce = QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(self.search_changed); self.list.currentItemChanged.connect(self.show_row)
        self.previous.clicked.connect(lambda: self.change_page(-1)); self.next.clicked.connect(lambda: self.change_page(1))
        self.previous.setEnabled(False); self.next.setEnabled(False)
        QTimer.singleShot(0, self.refresh)

    def _cancel_read(self):
        self._epoch += 1
        if self._task is not None: self.tasks.cancel(self._task); self._task = None

    def search_changed(self, *_):
        if self._closed: return
        self._cancel_read(); self.offset = 0; self.list.clear(); self.detail.clear()
        self.confirm_button.setEnabled(False); self.previous.setEnabled(False); self.next.setEnabled(False)
        self.status.setText('正在搜索…'); self._debounce.start()

    def change_page(self, step):
        if self._closed or self._task is not None: return
        offset = max(0, self.offset + step*self.PAGE_SIZE)
        if offset == self.offset or (step > 0 and offset >= self.total): return
        self.offset = offset; self.refresh()

    def refresh(self):
        if self._closed: return
        self._debounce.stop(); self._cancel_read(); epoch = self._epoch
        query = self.search.text().strip(); offset = self.offset
        self.list.clear(); self.detail.clear(); self.confirm_button.setEnabled(False)
        self.previous.setEnabled(False); self.next.setEnabled(False); self.status.setText('正在读取备课列表…')
        def failed(message):
            if self._closed or epoch != self._epoch: return
            self._task = None; self.status.setText(message)
            self.previous.setEnabled(self.offset > 0)
        def ready(page):
            if self._closed or epoch != self._epoch: return
            self._task = None; self.total = page['total']
            # A course can disappear between two pages. Return to a valid page.
            if self.offset and self.offset >= self.total:
                self.offset = max(0, ((self.total-1)//self.PAGE_SIZE)*self.PAGE_SIZE); self.refresh(); return
            for row in page['items']:
                item = QListWidgetItem(row['title']+'\n'+_saved_time(row)+f" · {row['image_count']}图")
                item.setData(Qt.ItemDataRole.UserRole, row); item.setToolTip(row['title'])
                self.list.addItem(item)
            first = min(self.offset+1, self.total); last = min(self.offset+self.PAGE_SIZE, self.total)
            self.page_label.setText(f'{first}–{last} / {self.total}份备课')
            self.previous.setEnabled(self.offset > 0); self.next.setEnabled(page['has_more'])
            self.status.setText('本页有备课在读取时变化，可重新搜索。' if page['unavailable_count'] else
                                ('暂无符合条件的当前备课。' if not self.total else '无图片的备课可查看课题，选择含图片的备课继续。'))
            if self.list.count(): self.list.setCurrentRow(0)
        self._task = self.tasks.submit('搜索已有备课图片',
            lambda: saved_preparation_image_page(self.facade, query=query, offset=offset, limit=self.PAGE_SIZE),
            on_success=ready, on_failure=failed)

    def show_row(self, item, *_):
        if item is None: self.detail.clear(); self.confirm_button.setEnabled(False); return
        row = item.data(Qt.ItemDataRole.UserRole)
        lines = [row['title'], '保存时间：'+_saved_time(row)]
        if row.get('original_title') and row['original_title'] != row['title']:
            lines.append('原课题：'+row['original_title'])
        lines.append(f"图片：{row['image_count']}张")
        self.detail.setPlainText('\n\n'.join(lines)); self.confirm_button.setEnabled(row['image_count'] > 0)

    def choose(self):
        item = self.list.currentItem()
        if item is None or not self.confirm_button.isEnabled() or self._task is not None: return
        self.chosen = deepcopy(item.data(Qt.ItemDataRole.UserRole)); self.accept()

    def done(self, result):
        if not self._closed:
            self._closed = True; self._debounce.stop(); self._cancel_read()
            if self._own_tasks: self.tasks.shutdown(1000)
        super().done(result)
