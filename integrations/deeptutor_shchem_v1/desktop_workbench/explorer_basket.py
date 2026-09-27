"""One durable public basket; undo belongs only to the currently open window."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QDialog, QHBoxLayout, QListView, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from ..desktop_state import BasketConflictError
from .explorer_reader import text_label


class ExplorerBasketDialog(QDialog):
    basket_changed = Signal(int)
    preview_requested = Signal()
    edit_requested = Signal()
    DEFAULT_STATUS = "题篮顺序会保存；组卷草稿仍需在组卷页单独核对。"

    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self._session = None
        self._readable = False
        self._busy = False
        self._last_read_history_reset = False
        self.setWindowTitle("选题篮 · 当前已选")
        self.resize(680, 650)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setAccessibleName("题篮列表与操作，可上下滚动")
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(10)
        self.heading = text_label("选题篮", "PageTitle")
        content.addWidget(self.heading)
        content.addWidget(text_label("Word原题、图片完整主题与原卷完整主题共用此题篮。", "MutedLabel"))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMinimumHeight(170)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.list.setAccessibleName("选题篮中的完整题目")
        content.addWidget(self.list, 1)
        self.selection_status = text_label("尚未选择题目", "MutedLabel")
        content.addWidget(self.selection_status)
        row = QHBoxLayout()
        for name, label, delta in (("up", "上移", -1), ("down", "下移", 1), ("remove", "移出题篮", 0)):
            button = QPushButton(label)
            button.setObjectName("QuietButton")
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, d=delta: self.change(d))
            row.addWidget(button)
            setattr(self, name, button)
        content.addLayout(row)
        self.undo_button = QPushButton("撤销上一步（Ctrl+Z）")
        self.undo_button.setObjectName("QuietButton")
        self.undo_button.setAutoDefault(False)
        self.undo_button.clicked.connect(self.undo)
        content.addWidget(self.undo_button)
        self.history_status = text_label("本窗口暂无可撤销操作", "MutedLabel")
        self.history_status.setAccessibleName("本窗口撤销记录")
        content.addWidget(self.history_status)
        self.history_hint = text_label(
            "仅可撤销本窗口最近30次移出或调序。关闭、读取失败或其他操作更新题篮后，记录失效。", "MutedLabel",
        )
        content.addWidget(self.history_hint)
        self.retry = QPushButton("重新读取题篮")
        self.retry.setObjectName("QuietButton")
        self.retry.setAutoDefault(False)
        self.retry.clicked.connect(lambda _=False: self.refresh())
        self.retry.hide()
        content.addWidget(self.retry)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.edit_button = QPushButton("进入组卷编辑")
        self.edit_button.setObjectName("QuietButton")
        self.edit_button.clicked.connect(self.edit)
        self.preview = QPushButton("预览试卷排版")
        self.preview.clicked.connect(self.preview_paper)
        for button in (self.edit_button, self.preview):
            button.setAutoDefault(False)
            self.actions.addWidget(button)
        content.addLayout(self.actions)
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, 1)
        # Status and exit stay in the same visible footer, even in a short window.
        self.status = text_label(self.DEFAULT_STATUS, "MutedLabel")
        self.status.setAccessibleName("题篮读取与保存状态")
        root.addWidget(self.status)
        self.back = QPushButton("继续选题")
        self.back.setObjectName("QuietButton")
        self.back.setAutoDefault(False)
        self.back.setAccessibleName("关闭题篮，继续选题")
        self.back.clicked.connect(self.accept)
        root.addWidget(self.back)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo)
        self.list.currentRowChanged.connect(self.update_actions)
        self.refresh()

    def _failure(self, message):
        if self._session is not None:
            self._session.invalidate()
        self._readable = False
        self.heading.setText("选题篮 · 状态待核对")
        self.status.setText(message)
        self.retry.show()
        self.update_actions()

    def refresh(self, selected=None):
        previous_index = self.list.currentRow()
        current = self.list.currentItem()
        if selected is None and current is not None:
            selected = current.data(Qt.ItemDataRole.UserRole)
        try:
            if self._session is None:
                self._session = self.facade.open_basket_session()
            snapshot = self._session.read()
            rows = snapshot.rows
            keys = [row["key"] for row in rows]
            labels = {"word_question": "Word原题", "personal_visual_theme": "图片完整主题", "core_theme": "原卷完整主题"}
            prepared = [
                (row["key"], f"{index:02d}   {row.get('title_zh', '未命名题目')}\n"
                 f"{labels.get(row.get('item_kind', 'core_theme'), '题目')}  ·  {row.get('source_zh', '')}")
                for index, row in enumerate(rows, 1)
            ]
        except Exception:
            self._failure("题篮暂不能读取；保留上次显示，当前记录待核对。请重新读取，原数据未清空。")
            return False
        self._readable = True
        self._last_read_history_reset = snapshot.history_reset
        self.list.clear()
        for key, label in prepared:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(item)
            if key == selected:
                self.list.setCurrentItem(item)
        if rows and selected not in keys:
            self.list.setCurrentRow(min(max(previous_index, 0), len(rows) - 1))
        self.list.doItemsLayout()
        if self.list.currentItem() is not None:
            self.list.scrollToItem(self.list.currentItem())
        self.heading.setText(f"选题篮  ·  {len(rows)} 项")
        self.status.setText("题篮已更新；本窗口旧撤销记录已失效。" if snapshot.history_reset else self.DEFAULT_STATUS)
        self.retry.hide()
        self.update_actions()
        return True

    def update_actions(self, *_):
        index, n = self.list.currentRow(), self.list.count()
        ready = self._readable and not self._busy
        self.up.setEnabled(ready and index > 0)
        self.down.setEnabled(ready and 0 <= index < n - 1)
        self.remove.setEnabled(ready and index >= 0)
        self.preview.setEnabled(ready and n > 0)
        self.edit_button.setEnabled(ready and n > 0)
        history = self._session.undo_count if self._session is not None else 0
        action = self._session.undo_action if history else ""
        self.undo_button.setEnabled(ready and history > 0)
        self.undo_shortcut.setEnabled(ready and history > 0)
        self.undo_button.setAccessibleName(f"撤销本窗口上一步{action}，快捷键 Ctrl+Z")
        self.history_status.setText(f"可撤销 {history} 步 · 上一步：{action}" if history else "本窗口暂无可撤销操作")
        self.retry.setEnabled(not self._busy)
        self.back.setEnabled(not self._busy)
        self.selection_status.setText(
            "上次显示仅供核对 · 操作已暂停" if not self._readable else
            f"当前第 {index + 1} 项 / 共 {n} 项" if index >= 0 else
            "题篮为空 · 可撤销本窗口移出，或继续选题"
        )

    def _perform(self, operation, *, undo=False):
        if self._busy or not self._readable:
            return
        self._busy = True
        self.update_actions()
        try:
            result = operation()
        except BasketConflictError:
            self._failure("题篮已被其他操作更新，本次未覆盖。撤销记录已失效，请重新读取。")
        except Exception:
            self._failure("未能确认保存，请重新读取后核对。撤销记录已失效，不会自动重试。")
        else:
            refreshed = self.refresh(result["selected_key"])
            if result["changed"]:
                if refreshed and not self._last_read_history_reset:
                    self.status.setText(
                        f"已撤销{result['action']}，原题身份与顺序已恢复。" if undo else
                        f"已保存{result['action']}，可撤销上一步。"
                    )
                self.basket_changed.emit(self.list.count() if refreshed else result["count"])
        finally:
            self._busy = False
            self.update_actions()

    def change(self, delta):
        item = self.list.currentItem()
        if item is not None and self._session is not None:
            key = item.data(Qt.ItemDataRole.UserRole)
            self._perform(lambda: self._session.change(key, delta))

    def undo(self):
        if self._session is not None and self._session.undo_count:
            self._perform(self._session.undo, undo=True)

    def preview_paper(self):
        if not self._busy and self.refresh() and self.list.count():
            self.accept()
            self.preview_requested.emit()

    def edit(self):
        if not self._busy and self.refresh() and self.list.count():
            self.accept()
            self.edit_requested.emit()

    def done(self, result):
        if self._busy:
            return
        if self._session is not None:
            self._session.close()
        super().done(result)

    def resizeEvent(self, event):
        if hasattr(self, "actions"):
            direction = (QBoxLayout.Direction.TopToBottom if event.size().width() < 560
                         else QBoxLayout.Direction.LeftToRight)
            self.actions.setDirection(direction)
        super().resizeEvent(event)
