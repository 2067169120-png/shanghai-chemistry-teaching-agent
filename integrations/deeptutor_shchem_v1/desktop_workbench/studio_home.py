"""Task-first starting panel. It prepares a brief, never silently calls a model."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QBoxLayout, QComboBox, QLineEdit, QPushButton, QVBoxLayout
from ..desktop_studio import TEMPLATES
from .components import CardFrame
from .studio_templates import text_label


class WelcomePanel(CardFrame):
    template_requested = Signal(str, str)
    navigate_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeroCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        root.addWidget(text_label("TEACHING STUDIO  /  上海高中化学", "BrandSub"))
        root.addWidget(text_label("把教学想法，变成一节好课。", "HeroTitle"))
        root.addWidget(text_label("从课题开始，选择组织方式；让教案、课件与练习围绕同一个学习目标。"))
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("今天准备讲什么？例如：化学平衡、原电池、实验方案评价")
        self.prompt.setMinimumHeight(32)
        self.prompt.setAccessibleName("首页备课想法")
        root.addWidget(self.prompt)
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.template = QComboBox()
        for item in TEMPLATES:
            self.template.addItem(item.title, item.key)
        self.template.setAccessibleName("首页教学模板")
        self.start = QPushButton("开始备课 →")
        self.start.setObjectName("PrimaryAction")
        self.start.clicked.connect(self._start)
        self.prompt.returnPressed.connect(self._start)
        self.row.addWidget(self.template, 1)
        self.row.addWidget(self.start)
        root.addLayout(self.row)
        root.addWidget(text_label("先预览结构，再带入材料。此入口不会自动调用AI。", "BrandSub"))
        self.links = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        for title, route in (("浏览教学模板", "templates"), ("打开课堂工具", "classroom"), ("继续我的备课", "mywork")):
            button = QPushButton(title)
            button.setObjectName("LinkButton")
            button.clicked.connect(lambda _=False, key=route: self.navigate_requested.emit(key))
            self.links.addWidget(button)
        root.addLayout(self.links)

    def _start(self):
        self.template_requested.emit(self.template.currentData(), self.prompt.text().strip())

    def resizeEvent(self, event):
        direction = QBoxLayout.Direction.TopToBottom if event.size().width() < 520 else QBoxLayout.Direction.LeftToRight
        self.row.setDirection(direction)
        self.links.setDirection(direction)
        super().resizeEvent(event)
