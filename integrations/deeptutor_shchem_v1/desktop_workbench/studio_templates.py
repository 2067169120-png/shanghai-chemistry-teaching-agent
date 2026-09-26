"""Native, searchable teaching starters with previews and persistent favorites."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..desktop_studio import TEMPLATES, get_template, search_templates
from .components import CardFrame, page_scroll, section_title, set_status


def text_label(text, role="MutedLabel"):
    label = QLabel(text)
    label.setObjectName(role)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class TemplatePreviewDialog(QDialog):
    def __init__(self, key, parent=None, *, topic=""):
        super().__init__(parent)
        self.template = get_template(key)
        self.setWindowTitle("教学模板 · " + self.template.title)
        self.resize(720, 580)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(section_title(self.template.title, self.template.subtitle))
        self.topic = QLineEdit(topic)
        self.topic.setPlaceholderText("课题，如：化学平衡（已有课题不会被覆盖）")
        self.topic.setAccessibleName("模板课题")
        self.audience = QLineEdit()
        self.audience.setPlaceholderText("授课对象，可留空后在备课页填写")
        self.audience.setAccessibleName("模板授课对象")
        root.addWidget(self.topic)
        root.addWidget(self.audience)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText("建议学习目标\n" + self.template.objective +
                             "\n\n教学推进\n" + self.template.sequence +
                             f"\n\n空白备课默认：{self.template.periods}课时，每课时40分钟，可修改。")
        root.addWidget(preview, 1)
        root.addWidget(text_label("只补充目标与授课结构；不替换已填资料、原图和已有课题，不自动调用AI。"))
        row = QHBoxLayout()
        cancel = QPushButton("返回")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("应用到备课 →")
        apply.setObjectName("PrimaryAction")
        apply.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(apply)
        root.addLayout(row)


class TemplatePage(QWidget):
    template_requested = Signal(str)

    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.facade = facade
        self.cards = []
        self._columns = 0
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)
        root.addWidget(section_title("教学模板", "从一条清晰的学习路径开始，再加入你的教材、原题与课堂判断。"))
        filters = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索模板、场景或能力，例如：实验、错因、两课时")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("搜索教学模板")
        self.category = QComboBox()
        self.category.addItems(["全部", "新授", "实验", "复习", "讲评", "探究", "单元"])
        self.category.setAccessibleName("教学模板分类")
        filters.addWidget(self.query, 1)
        filters.addWidget(self.category)
        root.addLayout(filters)
        self.only_favorites = QCheckBox("只看我的收藏")
        self.only_favorites.setAccessibleName("只看已收藏教学模板")
        root.addWidget(self.only_favorites)
        self.status = text_label("6个原创教学组织模板 · 可以离线预览与选用，生成PPT/教案另走原有AI流程。")
        root.addWidget(self.status)
        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        root.addLayout(self.grid)
        self.empty = text_label("没有匹配的模板。换个关键词或取消“只看我的收藏”。")
        root.addWidget(self.empty)
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self.query.textChanged.connect(self.rebuild)
        self.category.currentTextChanged.connect(self.rebuild)
        self.only_favorites.toggled.connect(self.rebuild)
        self.rebuild()

    def _favorites(self):
        try:
            values = self.facade.state_store.snapshot().get("studio", {}).get("favorites", [])
            return set(values) & {t.key for t in TEMPLATES}
        except Exception:
            set_status(self.status, "attention", "收藏暂时无法读取，模板仍可使用。")
            return set()

    def _favorite(self, key, checked):
        values = self._favorites()
        if checked:
            values.add(key)
        else:
            values.discard(key)
        try:
            self.facade.state_store.save_studio_favorites(sorted(values))
        except Exception:
            set_status(self.status, "error", "收藏未保存，请检查本机数据目录后重试。")
        self.rebuild()

    def rebuild(self, *_):
        for card in self.cards:
            # Layout removal alone leaves the widget visible until deletion.
            card.hide()
            self.grid.removeWidget(card)
            card.deleteLater()
        self.cards = []
        favorites = self._favorites()
        items = search_templates(self.query.text(), self.category.currentText())
        if self.only_favorites.isChecked():
            items = tuple(item for item in items if item.key in favorites)
        self.empty.setVisible(not items)
        for item in items:
            card = CardFrame()
            card.setObjectName("TemplateCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(12)
            row = QHBoxLayout()
            row.addWidget(text_label(item.category + " / " + str(item.periods) + "课时", "Badge"))
            row.addStretch(1)
            favorite = QPushButton("已收藏" if item.key in favorites else "收藏")
            favorite.setObjectName("Chip")
            favorite.setCheckable(True)
            favorite.setChecked(item.key in favorites)
            favorite.setAccessibleName("收藏模板：" + item.title)
            favorite.clicked.connect(lambda checked, key=item.key: self._favorite(key, checked))
            row.addWidget(favorite)
            layout.addLayout(row)
            layout.addWidget(text_label(item.title, "CardTitle"))
            layout.addWidget(text_label(item.subtitle))
            layout.addWidget(text_label(" · ".join(item.tags)))
            button = QPushButton("预览并选用 →")
            button.setObjectName("QuietButton")
            button.setAccessibleName("预览模板：" + item.title)
            button.clicked.connect(lambda _=False, key=item.key: self.template_requested.emit(key))
            layout.addWidget(button)
            self.cards.append(card)
        self._place_cards()

    def _place_cards(self):
        self._columns = 2 if self.width() >= 780 else 1
        for index, card in enumerate(self.cards):
            self.grid.removeWidget(card)
            self.grid.addWidget(card, index // self._columns, index % self._columns)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if self._columns == 2 else 0)

    def resizeEvent(self, event):
        self._place_cards()
        super().resizeEvent(event)
