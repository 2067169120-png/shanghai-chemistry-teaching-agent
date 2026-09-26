"""An explicit list of disjoint Word segments, separate from the reading cursor."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_word_ranges import (
    MAX_WORD_RANGES,
    normalize_word_ranges,
    word_range_label,
)


class WordRangeSelection(QWidget):
    changed = Signal()
    range_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ranges = []
        self._current = None
        self._blocks = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("合并多个分散段落")
        self.enabled.setAccessibleName("启用多个不连续原文选段")
        layout.addWidget(self.enabled)
        self.panel = QWidget()
        body = QVBoxLayout(self.panel)
        body.setContentsMargins(0, 4, 0, 4)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        body.addWidget(self.hint)
        self.add_button = QPushButton("加入当前段")
        self.add_button.setAccessibleName("把当前查看范围加入本次选段")
        body.addWidget(self.add_button)
        self.summary = QLabel("本次尚未加入段落")
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.ranges_list = QListWidget()
        self.ranges_list.setAccessibleName("本次将带入备课的原文段落")
        self.ranges_list.setMinimumHeight(100)
        self.ranges_list.setMaximumHeight(160)
        self.ranges_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.ranges_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.ranges_list.setSpacing(3)
        body.addWidget(self.ranges_list)
        actions = QHBoxLayout()
        self.remove_button = QPushButton("移除选中段")
        self.clear_button = QPushButton("清空选段")
        for button in (self.remove_button, self.clear_button):
            button.setObjectName("QuietButton")
            actions.addWidget(button)
        body.addLayout(actions)
        note = QLabel(
            f"只带入清单中的段落，按原文顺序合并；重复或相邻范围自动合并。最多{MAX_WORD_RANGES}段。请把完整题干、共同材料和对应答案一起选入。"
        )
        note.setWordWrap(True)
        body.addWidget(note)
        layout.addWidget(self.panel)
        self.panel.hide()
        self.enabled.toggled.connect(self._toggle)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.clear_button.clicked.connect(self._clear)
        self.ranges_list.currentRowChanged.connect(self._row_selected)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._render()

    def ranges(self):
        return deepcopy(self._ranges)

    def reset(self):
        self._ranges = []
        self._current = None
        self._blocks = []
        self.enabled.setChecked(False)
        self._render()

    def set_current(self, start, end, valid, blocks):
        self._current = {"start": start, "end": end} if valid else None
        self._blocks = blocks
        self.add_button.setEnabled(valid)
        included = valid and any(
            r["start"] <= start <= end <= r["end"] for r in self._ranges
        )
        self.hint.setText(
            f"当前查看：区块 {start}—{end}。"
            + (
                "已在本次清单内。"
                if included
                else "尚未加入；点击“加入当前段”后才会带入备课。"
            )
            if valid
            else "请先在上方选择有效的起止区块。"
        )

    def _toggle(self, checked):
        self.panel.setVisible(checked)
        self.changed.emit()

    def _add(self):
        if self._current is None:
            return
        try:
            merged = normalize_word_ranges([*self._ranges, self._current])
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        if merged != self._ranges:
            self._ranges = merged
            self._render()
            self.changed.emit()

    def _remove(self):
        row = self.ranges_list.currentRow()
        if row < 0:
            return
        del self._ranges[row]
        self._render()
        self.changed.emit()

    def _clear(self):
        if not self._ranges:
            return
        self._ranges = []
        self._render()
        self.changed.emit()

    def _row_selected(self, row):
        self.remove_button.setEnabled(row >= 0)
        if row >= 0:
            bounds = self._ranges[row]
            self.range_requested.emit(bounds["start"], bounds["end"])

    def _render(self):
        self.ranges_list.blockSignals(True)
        self.ranges_list.clear()
        for bounds in self._ranges:
            snippets = [
                b["text"].strip()
                for b in self._blocks
                if bounds["start"] <= b["index"] <= bounds["end"] and b["text"].strip()
            ]
            first = " ".join(snippets[0].split()) if snippets else "（空白段落）"
            label = f"区块 {word_range_label([bounds])}\n{first[:60]}"
            item = QListWidgetItem(label)
            item.setToolTip("\n\n".join(snippets))
            self.ranges_list.addItem(item)
        self.ranges_list.blockSignals(False)
        count = sum(r["end"] - r["start"] + 1 for r in self._ranges)
        self.summary.setText(
            f"本次已选 {len(self._ranges)} 段，共 {count} 个区块"
            if self._ranges
            else "本次尚未加入段落"
        )
        self.remove_button.setEnabled(False)
        self.clear_button.setEnabled(bool(self._ranges))
