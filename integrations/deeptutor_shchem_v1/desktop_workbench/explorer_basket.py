"""Native basket over the existing durable store; read failures never mean empty."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)
from .explorer_reader import text_label


class ExplorerBasketDialog(QDialog):
    basket_changed = Signal(int)
    preview_requested = Signal()
    edit_requested = Signal()
    DEFAULT_STATUS = "排列在题篮中的顺序会保存；已有组卷草稿的编排仍在组卷页核对。"

    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self._readable = False
        self.setWindowTitle("选题篮 · 当前已选")
        self.resize(680, 650)
        self.setMinimumSize(360, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        self.heading = text_label("选题篮", "PageTitle")
        root.addWidget(self.heading)
        root.addWidget(text_label("Word原题与图片/原卷完整主题共用同一题篮；筛选条件不会影响已选内容。", "MutedLabel"))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAccessibleName("选题篮中的完整题目")
        root.addWidget(self.list, 1)
        self.selection_status = text_label("尚未选择题目", "MutedLabel")
        root.addWidget(self.selection_status)
        row = QHBoxLayout()
        for name, label, delta in (("up", "上移", -1), ("down", "下移", 1), ("remove", "移出题篮", 0)):
            button = QPushButton(label)
            button.setObjectName("QuietButton")
            button.clicked.connect(lambda _=False, d=delta: self.change(d))
            row.addWidget(button)
            setattr(self, name, button)
        root.addLayout(row)
        self.status = text_label(self.DEFAULT_STATUS, "MutedLabel")
        self.status.setAccessibleName("题篮读取与保存状态")
        root.addWidget(self.status)
        self.retry = QPushButton("重新读取题篮")
        self.retry.setObjectName("QuietButton")
        self.retry.clicked.connect(lambda _=False: self.refresh())
        self.retry.hide()
        root.addWidget(self.retry)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        back = QPushButton("继续选题")
        back.setObjectName("QuietButton")
        back.clicked.connect(self.accept)
        self.edit_button = QPushButton("进入组卷编辑")
        self.edit_button.setObjectName("QuietButton")
        self.edit_button.clicked.connect(self.edit)
        self.preview = QPushButton("预览试卷排版")
        self.preview.clicked.connect(self.preview_paper)
        for button in (back, self.edit_button, self.preview):
            # Enter must not silently launch preview or close the dialog while
            # the teacher is selecting a row or recovering a failed read.
            button.setAutoDefault(False)
            self.actions.addWidget(button)
        root.addLayout(self.actions)
        self.list.currentRowChanged.connect(self.update_actions)
        self.refresh()

    def refresh(self, selected=None):
        previous_index = self.list.currentRow()
        current = self.list.currentItem()
        if selected is None and current is not None:
            selected = current.data(Qt.ItemDataRole.UserRole)
        try:
            # Prepare the entire new view before touching the last readable one.
            # Do not interpret partial/invalid records as an empty selection.
            rows = tuple(self.facade.basket())
            keys = [row["key"] for row in rows]
            if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
                raise ValueError("Invalid basket identity")
            labels = {"word_question": "Word原题", "personal_visual_theme": "图片完整主题", "core_theme": "原卷完整主题"}
            prepared = [
                (row["key"], f"{index:02d}   {row.get('title_zh', '未命名题目')}\n"
                 f"{labels.get(row.get('item_kind', 'core_theme'), '题目')}  ·  {row.get('source_zh', '')}")
                for index, row in enumerate(rows, 1)
            ]
        except Exception:
            self._readable = False
            self.heading.setText("选题篮 · 状态待核对")
            self.status.setText("题篮暂不能读取；保留上次显示，但不代表当前记录。请重新读取，原数据未被清空。")
            self.retry.show()
            self.update_actions()
            return False
        self._readable = True
        self.list.clear()
        for key, label in prepared:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(item)
            if key == selected:
                self.list.setCurrentItem(item)
        if rows and selected not in keys:
            self.list.setCurrentRow(min(max(previous_index, 0), len(rows) - 1))
        self.heading.setText(f"选题篮  ·  {len(rows)} 项")
        self.status.setText(self.DEFAULT_STATUS)
        self.retry.hide()
        self.update_actions()
        return True

    def update_actions(self, *_):
        index, n = self.list.currentRow(), self.list.count()
        self.up.setEnabled(self._readable and index > 0)
        self.down.setEnabled(self._readable and 0 <= index < n - 1)
        self.remove.setEnabled(self._readable and index >= 0)
        self.preview.setEnabled(self._readable and n > 0)
        self.edit_button.setEnabled(self._readable and n > 0)
        self.selection_status.setText(
            "上次显示仅供核对 · 操作已暂停" if not self._readable else
            f"当前第 {index + 1} 项 / 共 {n} 项" if index >= 0 else
            "题篮为空 · 返回选题中心加入完整题目"
        )

    def change(self, delta):
        item = self.list.currentItem()
        if not self._readable or item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        try:
            count = (self.facade.move_basket_item(key, delta) if delta else self.facade.remove_basket_item(key))
        except Exception:
            self._readable = False
            self.status.setText("修改未能确认保存，请重新读取后核对；不会覆盖题目来源文件。")
            self.retry.show()
            self.update_actions()
            return
        self.refresh(key)
        # The write succeeded even when the subsequent display refresh failed.
        self.basket_changed.emit(count)

    def preview_paper(self):
        if self.refresh() and self.list.count():
            self.accept()
            self.preview_requested.emit()

    def edit(self):
        if self.refresh() and self.list.count():
            self.accept()
            self.edit_requested.emit()

    def resizeEvent(self, event):
        if hasattr(self, "actions"):
            direction = (QBoxLayout.Direction.TopToBottom if event.size().width() < 560
                         else QBoxLayout.Direction.LeftToRight)
            self.actions.setDirection(direction)
        super().resizeEvent(event)
