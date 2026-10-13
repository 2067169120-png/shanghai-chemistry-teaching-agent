"""Read a lesson's complete knowledge text beside its bound textbook pages."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QLabel, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from ..desktop_lesson_composition import TEACHER_ROLES
from .textbook_source_dialog import TextbookSourceDialog


class KnowledgeCardReader(QDialog):
    def __init__(self, facade, card, context, parent=None, *, current=None):
        super().__init__(parent)
        self._current = current or (lambda: None)
        self.action = 'pick'
        self.section_materials_requested = False
        self.setWindowTitle('知识卡与教材原页')
        self.resize(1160, 820)
        self.setMinimumSize(360, 550)
        self._released = False
        root = QVBoxLayout(self)
        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        root.addWidget(self.split, 1)
        lesson = QWidget()
        left = QVBoxLayout(lesson)
        left.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel('本课讲解：' + card['title'])
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        left.addWidget(self.heading)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setAccessibleName('本课知识卡的完整讲解文字')
        self.body.setPlainText('\n\n'.join(
            block['value'] for block in card['blocks']
            if block['kind'] == 'text' and block['role'] not in TEACHER_ROLES
        ))
        left.addWidget(self.body, 1)
        self.split.addWidget(lesson)
        self.reader = TextbookSourceDialog(
            facade, context['selection'], self,
            reading_mode=context['reading_mode'], embedded=True,
        )
        self.split.addWidget(self.reader)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 2)
        actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.actions = actions
        self.section_materials_button = QPushButton('本节按知识点选材…')
        self.section_materials_button.setObjectName('PrimaryAction')
        self.section_materials_button.setEnabled(False)
        # Other read-only hosts (for example relationship inspection) have no
        # course insertion owner. Only the guarded course flow handles this intent.
        self.section_materials_button.setVisible(current is not None)
        self.section_materials_button.clicked.connect(self._choose_section_materials)
        actions.addWidget(self.section_materials_button, 1)
        self.pick = QPushButton('从这张知识卡选教材图…')
        self.pick.setObjectName('QuietButton')
        self.pick.setEnabled(False)
        self.pick.clicked.connect(self.accept)
        actions.addWidget(self.pick, 1)
        self.close_button = QPushButton('返回课件')
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        root.addLayout(actions)
        self.reader.reading_state_changed.connect(self._reading_ready)
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(100)
        self._stale_timer.timeout.connect(self._reading_ready)
        self._stale_timer.start()
        self._reading_ready()

    def _reading_ready(self):
        ready = not self._released and self.reader._loaded_pages and not self.reader._closed
        if ready:
            try:
                self._current()
            except (ValueError, OSError):
                ready = False
                self.section_materials_requested = False
                self.reject()
        self.pick.setEnabled(ready)
        self.section_materials_button.setEnabled(ready)

    def _choose_section_materials(self):
        self._reading_ready()
        if not self.section_materials_button.isEnabled():
            return
        self.action = 'section_materials'
        self.section_materials_requested = True
        self.accept()

    def resizeEvent(self, event):
        if hasattr(self, 'split'):
            narrow = event.size().width() < 800
            self.split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.split.setSizes([180, max(300, event.size().height() - 260)] if narrow else [350, 700])
            self.actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)

    def done(self, result):
        if not self._released:
            self._released = True
            self._stale_timer.stop()
            self.reader.reject()
        if result != QDialog.DialogCode.Accepted:
            self.section_materials_requested = False
            self.action = None
        super().done(result)

    def closeEvent(self, event):
        self.reject()
        event.accept()
