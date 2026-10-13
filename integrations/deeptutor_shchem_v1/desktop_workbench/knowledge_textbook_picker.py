"""Keep an explicit ordered choice of original pages beside their preview."""
from copy import deepcopy
import json

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QDialogButtonBox, QListWidgetItem, QPushButton

from ..desktop_preparation_images import MAX_IMAGES
from .components import page_scroll
from .lesson_composition_panel import ResourcePicker, label


def _identity(row):
    return json.dumps(row['selection'], sort_keys=True, ensure_ascii=False)


class KnowledgeTextbookPicker(ResourcePicker):
    def __init__(self, facade, rows, parent=None, *, intro=None):
        self._checked = {}
        self.chosen_rows = []
        super().__init__(deepcopy(rows), '从知识卡选择教材图',
            lambda r:r['relation']+' · '+r.get('volume_title', r.get('source_name', ''))+' · '+r['label'],
            parent, intro=intro, visual_facade=facade)
        self.list.setWordWrap(True)
        self.list.setMinimumHeight(160)
        self.list.setAccessibleName('知识卡相关教材图与原页，勾选后按选择顺序框选')
        self.setMinimumSize(360, 650)
        preview = self.visual_split.widget(1)
        preview.setParent(None)
        self.preview_scroll = page_scroll(preview)
        self.preview_scroll.setMinimumHeight(140)
        self.visual_split.addWidget(self.preview_scroll)
        self.visual_split.setStretchFactor(0, 1)
        self.visual_split.setStretchFactor(1, 2)
        self.count = label('')
        self.clear_checked = QPushButton('清空勾选')
        self.clear_checked.clicked.connect(self.clear_choices)
        self.layout().insertWidget(2, self.count)
        self.layout().insertWidget(3, self.clear_checked)
        self.confirm = self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        self.list.itemChanged.connect(self.check_changed)
        self._update_choices()

    def filter(self, *_):
        query = self.search.text().strip().casefold()
        with QSignalBlocker(self.list):
            self.list.clear()
            seen = set()
            for row in self.rows:
                key = _identity(row)
                if key in seen or query and query not in json.dumps(row, ensure_ascii=False).casefold():
                    continue
                seen.add(key)
                item = QListWidgetItem(self.row_label(row))
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if key in self._checked else Qt.CheckState.Unchecked)
                self.list.addItem(item)
            self.list.setCurrentRow(0)
        current = self.list.currentItem()
        self.show_row(current)
        if self.page_preview is not None:
            self.page_preview.set_row(current.data(Qt.ItemDataRole.UserRole) if current else None)
        if hasattr(self, 'count'):
            self._update_choices()

    def check_changed(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        key = _identity(row)
        if item.checkState() == Qt.CheckState.Checked:
            if key not in self._checked and len(self._checked) >= MAX_IMAGES:
                with QSignalBlocker(self.list):
                    item.setCheckState(Qt.CheckState.Unchecked)
                self.count.setText(f'每批最多{MAX_IMAGES}张，请先移除部分勾选。')
                return
            self._checked[key] = deepcopy(row)
        else:
            self._checked.pop(key, None)
        self._update_choices()

    def _update_choices(self):
        count = len(self._checked)
        self.count.setText(f'已勾选{count}张 · 搜索后保留勾选，按勾选顺序逐张框选。')
        self.confirm.setText(f'逐张框选所选图（{count}张）')
        self.confirm.setEnabled(bool(count))
        self.clear_checked.setEnabled(bool(count))

    def clear_choices(self):
        self._checked.clear()
        self.filter()

    def choose(self):
        if not self._checked:
            return
        self.chosen_rows = deepcopy(list(self._checked.values()))
        self.page_preview.release()
        self.accept()

    def reject(self):
        self.chosen_rows = []
        self._checked.clear()
        super().reject()
