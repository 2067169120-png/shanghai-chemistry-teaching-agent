"""Native editor for a detached task selection, never the global question bank."""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QGridLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QPlainTextEdit, QDialogButtonBox)
from ..desktop_exam_data import ExamError
from ..desktop_exam_practice import selection_items, revise_selection


class PracticeSetDialog(QDialog):
    def __init__(self, task, save_candidate, parent=None):
        super().__init__(parent)
        self.original = deepcopy(task)
        self.rows = {row['key']: row for row in selection_items(task)}
        self.initial_keys = list(self.rows)
        self.keys = self.initial_keys.copy()
        self.save_candidate = save_candidate
        self.setWindowTitle('整理本任务题集与目标')
        self.resize(680, 660)
        self.setMinimumSize(400, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        title = QLabel('本任务的完整题目'); title.setObjectName('CardTitle')
        box.addWidget(title)
        hint = QLabel('调整顺序或从本任务移除。原题、全局题篮和历史复测不会被删除；新增题目请返回题篮明确勾选。')
        hint.setWordWrap(True); box.addWidget(hint)
        self.counter = QLabel(); self.counter.setAccessibleName('题集数量与当前位置')
        box.addWidget(self.counter)
        self.view = QListWidget(); self.view.setWordWrap(True)
        self.view.setMinimumHeight(120); self.view.setSpacing(4)
        self.view.setAccessibleName('本任务有序题目')
        box.addWidget(self.view, 1)
        actions = QGridLayout(); box.addLayout(actions)
        self.up = QPushButton('上移'); self.down = QPushButton('下移')
        self.remove = QPushButton('从本任务移除'); self.reset = QPushButton('恢复原题目与顺序')
        for i, button in enumerate((self.up, self.down, self.remove, self.reset)):
            button.setObjectName('QuietButton'); button.setAutoDefault(False)
            actions.addWidget(button, i // 2, i % 2)
        box.addWidget(QLabel('本次学习目标（最多2000字）'))
        self.goal = QPlainTextEdit(task['goal']); self.goal.setMaximumHeight(95)
        self.goal.setAccessibleName('本任务学习目标'); box.addWidget(self.goal)
        self.message = QLabel('保存后，题目或目标的实质变化需要重新预览并确认。原题文件仍须保留。')
        self.message.setWordWrap(True); box.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('保存到本任务')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        box.addWidget(self.buttons)
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        self.remove.clicked.connect(self.remove_current)
        self.reset.clicked.connect(self.reset_order)
        self.view.currentRowChanged.connect(self.update_actions)
        self.goal.textChanged.connect(self.update_actions)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.populate(0)

    def populate(self, selected):
        self.view.blockSignals(True); self.view.clear()
        for index, key in enumerate(self.keys):
            row = self.rows[key]
            title = str(row.get('title_zh') or '未命名题目')
            source = str(row.get('source_zh') or '来源待核对')
            item = QListWidgetItem(f'{index + 1}. {title}\n来源：{source}')
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(f'{title}\n来源：{source}')
            self.view.addItem(item)
        self.view.setCurrentRow(min(max(0, selected), len(self.keys) - 1))
        self.view.blockSignals(False); self.update_actions()

    def update_actions(self):
        index = self.view.currentRow(); count = len(self.keys)
        self.counter.setText(f'{count}项完整题目 · 当前第{index + 1 if index >= 0 else 0}项')
        self.up.setEnabled(index > 0)
        self.down.setEnabled(0 <= index < count - 1)
        self.remove.setEnabled(count > 1 and index >= 0)
        self.reset.setEnabled(self.keys != self.initial_keys)
        goal = self.goal.toPlainText()
        self.save_button.setEnabled(bool(count and goal.strip() and len(goal) <= 2000))

    def move(self, delta):
        index = self.view.currentRow(); target = index + delta
        if 0 <= index < len(self.keys) and 0 <= target < len(self.keys):
            self.keys[index], self.keys[target] = self.keys[target], self.keys[index]
            self.populate(target)

    def remove_current(self):
        index = self.view.currentRow()
        if len(self.keys) > 1 and 0 <= index < len(self.keys):
            self.keys.pop(index); self.populate(index)

    def reset_order(self):
        self.keys = self.initial_keys.copy(); self.populate(0)

    def save(self):
        try:
            candidate = revise_selection(self.original, self.keys, self.goal.toPlainText())
            if self.save_candidate(candidate):
                self.accept()
            else:
                self.message.setText('保存未完成，窗口中的修改仍保留。请核对主窗口提示、文件权限或其他窗口的更改，再重试。')
        except ExamError as error:
            self.message.setText(error.message_zh)
