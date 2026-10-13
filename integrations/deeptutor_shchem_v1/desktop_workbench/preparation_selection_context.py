"""A compact, source-independent destination for explicit textbook selection."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class PreparationSelectionContext(QWidget):
    edit_requested = Signal()
    open_requested = Signal()
    new_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('PreparationSelectionContext')
        self.setMinimumWidth(0)
        self._has_context = False
        self._busy = False
        self.body = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(3)
        self.text_panel = QWidget()
        self.text_panel.setMinimumWidth(0)
        layout = QVBoxLayout(self.text_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.body.addWidget(self.text_panel, 1)
        self.topic_label = QLabel()
        self.detail_label = QLabel()
        self.title = self.topic_label
        self.detail = self.detail_label
        for label in (self.topic_label, self.detail_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(label)
        self.topic_label.setAccessibleName('正在接收教材素材的备课课题')
        self.detail_label.setAccessibleName('当前备课课时与保存状态')
        self.detail_label.setObjectName('MutedLabel')
        self.actions_panel = QWidget()
        self.actions = QGridLayout(self.actions_panel)
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(4)
        self._narrow_actions = None
        self._wide_context = None
        self.edit_button = QPushButton('继续编辑')
        self.open_button = QPushButton('打开已有备课')
        self.new_button = QPushButton('新建备课')
        for button, signal in ((self.edit_button, self.edit_requested),
                               (self.open_button, self.open_requested),
                               (self.new_button, self.new_requested)):
            button.setObjectName('QuietButton')
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, event=signal: self._request(event))
        self.body.addWidget(self.actions_panel)
        self._arrange_actions()
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange_actions()

    def _arrange_actions(self):
        buttons = (self.edit_button, self.open_button, self.new_button)
        wide = self.width() >= 1000
        if wide != self._wide_context:
            self._wide_context = wide
            self.body.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            self.body.setSpacing(8 if wide else 3)
            self.actions_panel.setSizePolicy(QSizePolicy.Policy.Minimum if wide else QSizePolicy.Policy.Expanding,
                                            QSizePolicy.Policy.Preferred)
            for button in buttons:
                button.setSizePolicy(QSizePolicy.Policy.Minimum if wide else QSizePolicy.Policy.Ignored,
                                     QSizePolicy.Policy.Fixed)
            self.body.setAlignment(self.actions_panel, Qt.AlignmentFlag.AlignVCenter if wide else Qt.AlignmentFlag(0))
        narrow = self.width() < max(480, sum(button.sizeHint().width() for button in buttons) + 8)
        if narrow == self._narrow_actions:
            return
        self._narrow_actions = narrow
        for button in buttons:
            self.actions.removeWidget(button)
        for column in range(3):
            self.actions.setColumnStretch(column, 0)
        if narrow:
            self.actions.addWidget(self.edit_button, 0, 0)
            self.actions.addWidget(self.open_button, 0, 1)
            self.actions.addWidget(self.new_button, 1, 0, 1, 2)
            self.actions.setColumnStretch(0, 1)
            self.actions.setColumnStretch(1, 1)
        else:
            for column, button in enumerate(buttons):
                self.actions.addWidget(button, 0, column)
                self.actions.setColumnStretch(column, 1)

    def _request(self, signal):
        if self._has_context and not self._busy:
            signal.emit()

    def set_context(self, payload, *, dirty=False, busy=False):
        self._has_context = isinstance(payload, dict)
        self.setVisible(self._has_context)
        if self._has_context:
            topic = payload.get('topic')
            topic = topic.strip() if isinstance(topic, str) else ''
            work_title = payload.get('work_title')
            work_title = work_title.strip() if isinstance(work_title, str) else ''
            timing = payload.get('lesson_timing')
            timing = timing.strip() if isinstance(timing, str) else ''
            self.topic_label.setText('正在接收素材：' + (work_title or topic or '未命名备课'))
            detail = '课时：' + (timing or '未设置') + (' · 未保存修改' if dirty else ' · 无未保存修改')
            if work_title and work_title != topic:
                detail = '课题：' + (topic or '未填写') + ' · ' + detail
            self.detail_label.setText(detail)
        else:
            self.topic_label.clear()
            self.detail_label.clear()
        self.set_busy(busy)

    def set_busy(self, busy):
        self._busy = bool(busy)
        for button in (self.edit_button, self.open_button, self.new_button):
            button.setEnabled(self._has_context and not self._busy)
