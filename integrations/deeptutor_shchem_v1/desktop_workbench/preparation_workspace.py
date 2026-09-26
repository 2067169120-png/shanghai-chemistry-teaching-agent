"""Pin the existing actions, not copies, around the existing preparation editor."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget


class PreparationWorkspace(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.scroll = page.editor_scroll
        self.toolbar = QWidget(page)
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(28, 10, 28, 6)
        self.requirements = QPushButton('教学要求')
        self.materials = QPushButton('备课资料')
        self.results = QPushButton('进度与结果')
        for button in (self.requirements, self.materials, self.results):
            button.setObjectName('QuietButton')
            row.addWidget(button)
        row.addStretch(1)
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
            self._move_widget(recovery.new_button, row)
            recovery.new_button.setText('新建备课')
            row.addWidget(self.more)

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
