"""Pin the existing actions, not copies, around the existing preparation editor."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLayout, QMenu, QPushButton, QSizePolicy,
                              QStyle, QStyleOptionButton, QStyleOptionToolButton,
                              QToolButton, QVBoxLayout, QWidget)


class WrappingControlLayout(QLayout):
    """Wrap the actual controls at their styled, fully readable widths."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    @staticmethod
    def _readable_size(item):
        widget = item.widget()
        size = item.sizeHint().expandedTo(item.minimumSize())
        if isinstance(widget, (QPushButton, QToolButton)):
            # sizeHint alone is insufficient under QSS: the style's label
            # rectangle must still contain the full text after its padding.
            widget.ensurePolished()
            option = QStyleOptionButton() if isinstance(widget, QPushButton) else QStyleOptionToolButton()
            widget.initStyleOption(option)
            option.rect = QRect(QPoint(), size)
            width = widget.fontMetrics().horizontalAdvance(widget.text())
            if isinstance(widget, QPushButton):
                contents = widget.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, widget)
                size.setWidth(size.width() + max(0, width - contents.width()))
            else:
                contents = QSize(width, widget.fontMetrics().height())
                size = size.expandedTo(widget.style().sizeFromContents(
                    QStyle.ContentsType.CT_ToolButton, option, contents, widget))
        return size

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(self._readable_size(item))
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def sizeHint(self):
        return self.minimumSize()

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _arrange(self, rect, *, apply):
        left, top, right, bottom = self.getContentsMargins()
        available = max(0, rect.width() - left - right)
        lines = []
        line = []
        used = 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = self._readable_size(item)
            following = used + (self.spacing() if line else 0) + size.width()
            if line and following > available:
                lines.append(line)
                line, used = [], 0
            line.append((item, size))
            used += (self.spacing() if len(line) > 1 else 0) + size.width()
        if line:
            lines.append(line)
        y = rect.y() + top
        for line in lines:
            height = max(size.height() for _, size in line)
            used = sum(size.width() for _, size in line) + self.spacing() * (len(line) - 1)
            expanding = [item for item, _ in line
                         if item.expandingDirections() & Qt.Orientation.Horizontal]
            extra = max(0, available - used)
            x = rect.x() + left
            for item, size in line:
                width = size.width()
                if item in expanding:
                    share = extra // len(expanding)
                    width += share
                    extra -= share
                    expanding.remove(item)
                if apply:
                    item.setGeometry(QRect(x, y, width, height))
                x += width + self.spacing()
            y += height + self.spacing()
        return y - rect.y() + bottom - (self.spacing() if lines else 0)


class PreparationWorkspace(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.scroll = page.editor_scroll
        self.toolbar = QWidget(page)
        self.toolbar.setObjectName('PreparationToolbar')
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row = QVBoxLayout(self.toolbar)
        row.setContentsMargins(12, 10, 12, 6)
        row.setSpacing(6)
        self.primary_controls = QWidget(self.toolbar)
        self.primary_controls.setAccessibleName('选材与教学设计')
        self.primary_layout = WrappingControlLayout(self.primary_controls)
        self.primary_controls.hide()
        row.addWidget(self.primary_controls)
        self.secondary_controls = QWidget(self.toolbar)
        self.secondary_controls.setAccessibleName('备课定位与管理')
        self.secondary_layout = WrappingControlLayout(self.secondary_controls)
        row.addWidget(self.secondary_controls)
        self.requirements = QPushButton('教学要求')
        self.materials = QPushButton('备课资料')
        self.results = QPushButton('进度与结果')
        for button in (self.requirements, self.materials, self.results):
            button.setObjectName('QuietButton')
            self.secondary_layout.addWidget(button)
        self.requirements.setToolTip('定位当前课题、授课对象和教学目标，保留已填写内容。')
        self.materials.setToolTip('定位当前备课资料与说明，保留已选材料。')
        self.requirements.clicked.connect(lambda: self.locate(page.topic))
        self.materials.clicked.connect(lambda: self.locate(page.materials))
        self.results.clicked.connect(self.show_results)
        page.layout().insertWidget(0, self.toolbar)

        self.footer = QWidget(page)
        self.footer.setObjectName('PreparationActionBar')
        self.footer.setStyleSheet('QWidget#PreparationActionBar {border-top: 1px solid #D9E0DA; background:#FFFFFF;}')
        layout = QVBoxLayout(self.footer)
        layout.setContentsMargins(28, 8, 28, 10)
        layout.setSpacing(5)
        # Reparent the real controls so all existing enabled/disabled updates,
        # confirmation, cancellation and failure behavior remain authoritative.
        owner = page.actions.parent()
        if owner:
            owner.removeItem(page.actions)
        page.actions.setParent(None)
        layout.addLayout(page.actions)
        self._move_widget(page.status, layout)
        page.status.setMinimumWidth(0)
        page.status.setWordWrap(True)
        self.task_controls = QHBoxLayout()
        for button in (page.stop_button, page.task_action_button,
                       page.revise_content_button, page.recover_returned_button):
            self._move_widget(button, self.task_controls)
            button.hide()
        self.task_controls.addStretch(1)
        layout.addLayout(self.task_controls)
        page.layout().addWidget(self.footer)
        for card in (page.progress_card, page.result_card):
            card.installEventFilter(self)
        self.refresh()

        recovery = getattr(page, 'recovery', None)
        if recovery:
            # A single primary Save action remains. Recovery is an automatic
            # status plus an explicitly named secondary action, not a rival Save.
            recovery.save_now.hide()
            self.more = QToolButton()
            self.more.setText('恢复选项')
            self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(self.more)
            action = menu.addAction('立即更新自动恢复副本')
            action.triggered.connect(recovery.flush)
            self.more.setMenu(menu)
            # Move the actual New control to the top navigation.
            self._move_widget(recovery.new_button, self.secondary_layout)
            recovery.new_button.setText('新建备课')
            self.more.setToolTip('立即更新本机自动恢复副本；正式保存仍使用“保存草稿”。')
            self.secondary_layout.addWidget(self.more)

    def register_studio_controls(self, *, textbook, handout, lesson_design, personal_template):
        """Register the existing studio controls once, without forwarding actions."""
        for button in (textbook, handout, lesson_design):
            self.primary_layout.addWidget(button)
        self.secondary_layout.addWidget(personal_template)
        self.primary_controls.show()

    @staticmethod
    def _move_widget(widget, layout):
        parent = widget.parentWidget()
        if parent and parent.layout():
            parent.layout().removeWidget(widget)
        layout.addWidget(widget)

    def locate(self, widget):
        # Only the outer scroll position changes. Inner text cursors, selection
        # and editing scroll positions remain where the teacher left them.
        position = widget.mapTo(self.scroll.widget(), QPoint(0, 0)).y()
        self.scroll.verticalScrollBar().setValue(max(0, position - 16))

    def show_results(self):
        if not self.page.result_card.isHidden():
            self.locate(self.page.result_card)
        elif not self.page.progress_card.isHidden():
            self.locate(self.page.progress_card)

    def refresh(self):
        self.results.setEnabled(not self.page.result_card.isHidden() or not self.page.progress_card.isHidden())
        self.results.setToolTip('定位本次任务进度或已有结果，不会重新生成。')

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.refresh()
        return super().eventFilter(watched, event)
