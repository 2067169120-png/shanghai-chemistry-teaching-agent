"""Choose and read complete teaching framework cards before adding to slides."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout)

from ..desktop_framework_composition import SECTIONS, framework_resource
from ..desktop_lesson_composition import visible_blocks


class FrameworkSlideDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle('预览教案栏目并同步到课件')
        self.resize(840, 620); self.plan = deepcopy(plan)
        root = QVBoxLayout(self)
        intro = QLabel('勾选本课需要的栏目，阅读完整内容后同步。目标、导入位于开头，小结、板书和课后任务位于结尾。')
        intro.setTextFormat(Qt.TextFormat.PlainText); intro.setWordWrap(True); root.addWidget(intro)
        self.audience = QComboBox(); self.audience.addItems(['学生课件', '教师课件'])
        root.addWidget(self.audience)
        body = QHBoxLayout(); self.sections = QListWidget(); self.sections.setMaximumWidth(220)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        body.addWidget(self.sections); body.addWidget(self.preview, 1); root.addLayout(body, 1)
        existing = {r['framework_link']['section'] for r in plan.get('composition', {}).get('resources', [])
                    if r['original']['kind'] == 'lesson_framework'}
        self.cards = {}
        for key, title, _ in SECTIONS:
            card = framework_resource(plan, key); self.cards[key] = card
            has_content = any(row['value'].strip() for row in card['blocks'])
            row = QListWidgetItem(title + ('' if has_content else '（未填写）'))
            row.setData(Qt.ItemDataRole.UserRole, key)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if has_content or key in existing else Qt.CheckState.Unchecked)
            self.sections.addItem(row)
        self.keep_edits = QCheckBox('保留已经单独编辑的课件文字'); self.keep_edits.setChecked(True)
        root.addWidget(self.keep_edits)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('同步到课件')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.sections.currentRowChanged.connect(self.refresh_preview)
        self.sections.itemChanged.connect(self.refresh_preview)
        self.audience.currentIndexChanged.connect(self.refresh_preview)
        self.sections.setCurrentRow(0); self.refresh_preview()

    def chosen_sections(self):
        return [self.sections.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sections.count())
                if self.sections.item(i).checkState() == Qt.CheckState.Checked]

    def refresh_preview(self, *_):
        item = self.sections.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole); card = self.cards[key]
        rows = visible_blocks(card, student=self.audience.currentIndex() == 0)
        self.preview.setPlainText(card['title'] + '\n\n' + '\n\n'.join(row['value'] for row in rows))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.chosen_sections()))
