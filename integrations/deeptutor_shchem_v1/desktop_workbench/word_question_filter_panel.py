"""Compact native, multi-select question facets with removable condition chips."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _Flow(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(5)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        return QSize(0, 32 if self.items else 0)

    def _arrange(self, rect, dry):
        x, y, height = rect.x(), rect.y(), 0
        for item in self.items:
            size = item.sizeHint()
            size.setWidth(min(size.width(), max(1, rect.width())))
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, height = rect.x(), y + height + self.spacing(), 0
            if not dry:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y()


class WordQuestionFilterPanel(QWidget):
    selection_changed = Signal(dict)
    GROUPS: ClassVar = {
        "book": "教材册",
        "chapter": "章",
        "section": "节",
        "knowledge": "知识点",
        "grade": "年级",
        "exam": "考试类型",
        "source": "来源",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.options = {}
        self.selection = {group: set() for group in self.GROUPS}
        self.active_group = None
        self.buttons, self.checks, self.chips = {}, {}, {}
        self.setStyleSheet(
            "QPushButton#FilterGroup {padding:5px 10px; border-radius:12px; background:#fff; color:#345b55; border:1px solid #cadfd9;}"
            "QPushButton#FilterGroup:checked {background:#dff1ef; color:#176968; border-color:#8bbab4;}"
            "QPushButton#FilterChip {padding:4px 9px; border-radius:10px; background:#e7f3ef; color:#185e59;}"
            "QCheckBox {spacing:6px; padding:5px 9px; background:#fff; border-radius:8px;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        group_widget = QWidget()
        group_widget.setMinimumWidth(0)
        self.group_layout = _Flow(group_widget)
        for group, label in self.GROUPS.items():
            button = QPushButton(label + " ＋")
            button.setObjectName("FilterGroup")
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, name=group: self.open_group(name)
            )
            self.group_layout.addWidget(button)
            self.buttons[group] = button
        layout.addWidget(group_widget)
        self.option_scroll = QScrollArea()
        self.option_scroll.setWidgetResizable(True)
        self.option_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.option_scroll.setFixedHeight(118)
        self.option_widget = QWidget()
        self.option_widget.setMinimumWidth(0)
        self.option_layout = _Flow(self.option_widget)
        self.option_scroll.setWidget(self.option_widget)
        layout.addWidget(self.option_scroll)
        self.option_scroll.hide()
        self.knowledge_all_check = QCheckBox("需同时包含所选知识点（主辅标签合并）")
        self.knowledge_all_check.hide()
        self.knowledge_all_check.toggled.connect(self._knowledge_mode_changed)
        layout.addWidget(self.knowledge_all_check)
        row = QHBoxLayout()
        self.rule = QLabel("同组任选其一 · 不同组同时满足；点标签展开勾选")
        self.rule.setWordWrap(True)
        self.rule.setTextFormat(Qt.TextFormat.PlainText)
        self.rule.setObjectName("MutedLabel")
        self.rule.setMinimumWidth(0)
        row.addWidget(self.rule, 1)
        self.clear_button = QPushButton("清空条件")
        self.clear_button.setObjectName("QuietButton")
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.chip_widget = QWidget()
        self.chip_widget.setMinimumWidth(0)
        self.chip_layout = _Flow(self.chip_widget)
        self.chip_scroll = QScrollArea()
        self.chip_scroll.setWidgetResizable(True)
        self.chip_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chip_scroll.setWidget(self.chip_widget)
        self.chip_scroll.setFixedHeight(64)
        layout.addWidget(self.chip_scroll)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def set_options(self, groups):
        self.options = groups
        self._prune()
        self._render()

    def set_selection(self, selection, *, emit=True):
        self.selection = {group: set(selection.get(group, ())) for group in self.GROUPS}
        if "knowledge_mode" in selection:
            self.knowledge_all_check.blockSignals(True)
            self.knowledge_all_check.setChecked(selection["knowledge_mode"] == "all")
            self.knowledge_all_check.blockSignals(False)
        self._prune()
        self._render()
        if emit:
            self.selection_changed.emit(self.matching_selection())

    def matching_selection(self):
        return {
            **{key: set(values) for key, values in self.selection.items()},
            "knowledge_mode": "all" if self.knowledge_all_check.isChecked() else "any",
        }

    def _knowledge_mode_changed(self, _checked):
        self._render()
        self.selection_changed.emit(self.matching_selection())

    def _available(self, group):
        rows = self.options.get(group, [])
        if group in {"chapter", "section"} and self.selection["book"]:
            rows = [
                row
                for row in rows
                if row.get("volume_id") in self.selection["book"]
                or (row["id"] == "unknown" and "unknown" in self.selection["book"])
            ]
        if group == "section" and self.selection["chapter"]:
            parents = {
                (row.get("volume_id"), row.get("chapter_id"))
                for row in self.options.get("chapter", [])
                if row["id"] in self.selection["chapter"]
            }
            rows = [
                row
                for row in rows
                if (row.get("volume_id"), row.get("chapter_id")) in parents
            ]
        return rows

    def _prune(self):
        for group in self.GROUPS:
            self.selection[group] &= {row["id"] for row in self._available(group)}

    def open_group(self, group):
        self.active_group = None if self.active_group == group else group
        self._render()

    def clear(self):
        self.set_selection({"knowledge_mode": "any"})

    def remove(self, group, key):
        selected = {name: set(values) for name, values in self.selection.items()}
        selected[group].discard(key)
        self.set_selection(selected)

    def _toggle(self, group, key, checked):
        selected = {name: set(values) for name, values in self.selection.items()}
        if checked:
            selected[group].add(key)
        else:
            selected[group].discard(key)
        self.set_selection(selected)

    def _render(self):
        self.knowledge_all_check.setVisible(
            self.active_group == "knowledge" or self.knowledge_all_check.isChecked()
        )
        for group, button in self.buttons.items():
            count = len(self.selection[group])
            button.setText(self.GROUPS[group] + (f" · {count}" if count else " ＋"))
            button.setChecked(group == self.active_group)
        self._clear_layout(self.option_layout)
        self.checks = {}
        if self.active_group:
            for row in self._available(self.active_group):
                check = QCheckBox(row["label"])
                check.setToolTip(row["label"])
                check.setChecked(row["id"] in self.selection[self.active_group])
                check.toggled.connect(
                    lambda checked, group=self.active_group, key=row["id"]: (
                        self._toggle(group, key, checked)
                    )
                )
                self.option_layout.addWidget(check)
                self.checks[row["id"]] = check
            if not self.checks:
                self.option_layout.addWidget(QLabel("当前条件下没有可用标签"))
        self.option_scroll.setVisible(self.active_group is not None)
        self._clear_layout(self.chip_layout)
        self.chips = {}
        for group in self.GROUPS:
            for row in self.options.get(group, []):
                if row["id"] not in self.selection[group]:
                    continue
                chip = QPushButton(self.GROUPS[group] + "：" + row["label"] + " ×")
                chip.setObjectName("FilterChip")
                chip.setToolTip("移除此条件：" + row["label"])
                chip.clicked.connect(
                    lambda checked=False, name=group, key=row["id"]: self.remove(
                        name, key
                    )
                )
                self.chip_layout.addWidget(chip)
                self.chips[(group, row["id"])] = chip
        self.chip_widget.setVisible(bool(self.chips))
        self.chip_scroll.setVisible(bool(self.chips))
        self.clear_button.setEnabled(bool(self.chips))
        self.updateGeometry()

    def resizeEvent(self, event):
        self.chip_scroll.setFixedHeight(
            min(
                64,
                max(
                    34,
                    self.chip_layout.heightForWidth(max(80, event.size().width() - 8))
                    + 4,
                ),
            )
        )
        super().resizeEvent(event)
