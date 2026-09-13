"""A small native basket drawer over the existing durable basket, not a second cart."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout
from .explorer_reader import text_label


class ExplorerBasketDialog(QDialog):
    basket_changed = Signal(int)
    preview_requested = Signal()
    edit_requested = Signal()

    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
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
        row = QHBoxLayout()
        for name, label, delta in (("up", "上移", -1), ("down", "下移", 1), ("remove", "移出题篮", 0)):
            button = QPushButton(label)
            button.setObjectName("QuietButton")
            button.clicked.connect(lambda _=False, d=delta: self.change(d))
            row.addWidget(button)
            setattr(self, name, button)
        root.addLayout(row)
        self.status = text_label("排列在题篮中的顺序会保存；已有组卷草稿的编排仍在组卷页核对。", "MutedLabel")
        root.addWidget(self.status)
        actions = QHBoxLayout()
        back = QPushButton("继续选题")
        back.setObjectName("QuietButton")
        back.clicked.connect(self.accept)
        edit = QPushButton("进入组卷编辑")
        edit.setObjectName("QuietButton")
        edit.clicked.connect(self.edit)
        self.preview = QPushButton("预览试卷排版")
        self.preview.clicked.connect(self.preview_paper)
        for button in (back, edit, self.preview):
            actions.addWidget(button)
        root.addLayout(actions)
        self.list.currentRowChanged.connect(self.update_actions)
        self.refresh()

    def refresh(self, selected=None):
        self.list.clear()
        try:
            rows = self.facade.basket()
        except Exception:
            rows = []
            self.status.setText("题篮暂不能读取，请关闭后重试；未清空原有记录。")
        labels = {"word_question": "Word原题", "personal_visual_theme": "图片完整主题", "core_theme": "原卷完整主题"}
        for index, row in enumerate(rows, 1):
            item = QListWidgetItem(f"{index:02d}   {row.get('title_zh', '未命名题目')}\n"
                                   f"{labels.get(row.get('item_kind', 'core_theme'), '题目')}  ·  {row.get('source_zh', '')}")
            item.setData(Qt.ItemDataRole.UserRole, row["key"])
            self.list.addItem(item)
            if row["key"] == selected:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and rows:
            self.list.setCurrentRow(0)
        self.heading.setText(f"选题篮  ·  {len(rows)} 项")
        self.preview.setEnabled(bool(rows))
        self.update_actions()

    def update_actions(self, *_):
        index, n = self.list.currentRow(), self.list.count()
        self.up.setEnabled(index > 0)
        self.down.setEnabled(0 <= index < n - 1)
        self.remove.setEnabled(index >= 0)

    def change(self, delta):
        item = self.list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        try:
            count = (self.facade.move_basket_item(key, delta) if delta else self.facade.remove_basket_item(key))
        except Exception:
            self.status.setText("修改未保存，请重试；不会覆盖题目来源文件。")
            return
        self.refresh(key)
        self.basket_changed.emit(count)

    def preview_paper(self):
        self.accept()
        self.preview_requested.emit()

    def edit(self):
        self.accept()
        self.edit_requested.emit()
