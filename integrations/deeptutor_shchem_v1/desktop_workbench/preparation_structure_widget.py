"""Teacher-selected page, timing and blank-note operations with local preview."""

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation import DesktopPreparationError
from ..desktop_preparation_structure import classroom_timeline, preview_structure_edits


class PreparationStructureWidget(QWidget):
    changed = Signal()

    def __init__(self, candidate, parent=None):
        super().__init__(parent)
        self.candidate = deepcopy(candidate)
        self.current_candidate = None
        self.preview_error = ""
        self.operations = []
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        form = QVBoxLayout(panel)
        explanation = QLabel(
            "先确认页面属于哪个活动，再拆开拥挤图文页、补学习单，最后核对时间。"
            "所有操作都先预览，另存后才生效；不调用模型、不覆盖原稿。"
            "图文拆页不会自动区分题干与答案，请先核对该页正文。"
        )
        explanation.setWordWrap(True)
        form.addWidget(explanation)
        fields = QFormLayout()
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.slide = QComboBox()
        for number, slide in enumerate(candidate["slides"], 1):
            self.slide.addItem(f"{number} · {slide['title']}", slide["id"])
        self.slide.setAccessibleName("选择需要调整的PPT页面")
        self.activity = QComboBox()
        self.activity.addItem("请选择活动（不会自动推断）", None)
        for activity in candidate["activities"]:
            self.activity.addItem(activity["title"], activity["id"])
        self.activity.setAccessibleName("确认页面归属的教学活动")
        for combo in (self.slide, self.activity):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(12)
            combo.setMinimumWidth(0)
        fields.addRow("PPT页面", self.slide)
        fields.addRow("归属活动", self.activity)
        form.addLayout(fields)
        row = QHBoxLayout()
        self.link_button = QPushButton("确认本页归属")
        self.link_button.clicked.connect(self._link)
        row.addWidget(self.link_button)
        self.split_button = QPushButton("图文拆成相邻两页")
        self.split_button.clicked.connect(self._split)
        row.addWidget(self.split_button)
        form.addLayout(row)
        self.page_detail = QLabel()
        self.page_detail.setWordWrap(True)
        self.page_detail.setTextFormat(Qt.TextFormat.PlainText)
        form.addWidget(self.page_detail)
        self.slide.currentIndexChanged.connect(self._page_selected)

        note_intro = QLabel("增加学习单留白（归入上方选定活动；已有题目和留白不变）")
        note_intro.setWordWrap(True)
        form.addWidget(note_intro)
        self.heading = QLineEdit()
        self.heading.setMaxLength(80)
        self.heading.setPlaceholderText("小节标题，例如：电解质判断条件")
        self.heading.setAccessibleName("新增学习单小节标题")
        form.addWidget(self.heading)
        self.prompt = QPlainTextEdit()
        self.prompt.setMaximumHeight(80)
        self.prompt.setPlaceholderText(
            "学生应记录什么（最多500字）；不自动从教师答案中抽题。"
        )
        self.prompt.setAccessibleName("新增学习单填写提示")
        form.addWidget(self.prompt)
        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("书写留白行数"))
        self.lines = QSpinBox()
        self.lines.setRange(2, 10)
        self.lines.setValue(4)
        note_row.addWidget(self.lines)
        self.add_notes = QPushButton("添加笔记留白")
        self.add_notes.clicked.connect(self._notes)
        note_row.addWidget(self.add_notes)
        form.addLayout(note_row)
        self.table_notes = QPushButton("从选中页的知识表建立空白笔记表")
        self.table_notes.clicked.connect(self._table_notes)
        form.addWidget(self.table_notes)
        self.align_button = QPushButton("按活动预算重新分配PPT分钟数")
        self.align_button.clicked.connect(lambda: self._queue({"kind": "align_timing"}))
        form.addWidget(self.align_button)
        row = QHBoxLayout()
        self.undo_button = QPushButton("撤销最后一个结构操作")
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.undo_button)
        self.clear_button = QPushButton("清空本次结构操作")
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        form.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        form.addWidget(self.status)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(210)
        self.preview.setAccessibleName("本地结构修订和课时分界预览")
        form.addWidget(self.preview)
        scroll.setWidget(panel)
        root.addWidget(scroll)
        self._refresh()

    def _queue(self, operation):
        try:
            preview_structure_edits(self.candidate, [*self.operations, operation])
        except DesktopPreparationError as error:
            self.status.setText(error.message_zh)
            return False
        self.operations.append(deepcopy(operation))
        self._refresh()
        self.changed.emit()
        return True

    def undo(self):
        if self.operations:
            self.operations.pop()
            self._refresh()
            self.changed.emit()

    def clear(self):
        self.operations.clear()
        self._refresh()
        self.changed.emit()

    def set_candidate(self, candidate):
        """Refresh from original-position text edits, preserving ID-based edits."""
        self.candidate = deepcopy(candidate)
        self._refresh()

    def _page_selected(self, _index=0):
        candidate = self.current_candidate
        if candidate is None:
            self.page_detail.setText("请先修正文字或撤销冲突操作，再继续预览。")
            self.split_button.setEnabled(False)
            self.table_notes.setEnabled(False)
            return
        slide = next(
            row for row in candidate["slides"] if row["id"] == self.slide.currentData()
        )
        linked = slide.get("activity_ids", [])
        self.page_detail.setText(
            f"当前归属：{'、'.join(linked) or '未关联'}；{slide['minutes']}分钟。"
            "拆页后前页1分钟用于读图，后页使用其余分钟并保留全部原正文。"
        )
        self.split_button.setEnabled(
            slide["id"] != candidate["slides"][0]["id"]
            and bool(slide.get("image"))
            and slide["minutes"] >= 2
            and not any(
                op.get("kind") == "split_image" and op["slide_id"] == slide["id"]
                for op in self.operations
            )
        )
        self.table_notes.setEnabled(
            (slide.get("visual") or {}).get("kind") == "comparison"
        )

    def _link(self):
        self._queue(
            {
                "kind": "link_slide_activity",
                "slide_id": self.slide.currentData(),
                "activity_id": self.activity.currentData(),
            }
        )

    def _split(self):
        self._queue({"kind": "split_image", "slide_id": self.slide.currentData()})

    def _append_section(self, section):
        activity_id = self.activity.currentData()
        if not activity_id:
            self.status.setText("请先选择学习单所属的教学活动。")
            return False
        return self._queue(
            {
                "kind": "append_worksheet",
                "activity_id": activity_id,
                "worksheet_title": "课堂笔记与练习",
                "section": section,
            }
        )

    def _notes(self):
        if self._append_section(
            {
                "heading": self.heading.text().strip(),
                "prompt": self.prompt.toPlainText().strip(),
                "response_kind": "lines",
                "response_lines": self.lines.value(),
                "columns": [],
                "row_labels": [],
            }
        ):
            self.heading.clear()
            self.prompt.clear()

    def _table_notes(self):
        candidate = self.current_candidate
        if candidate is None:
            return
        slide = next(
            row for row in candidate["slides"] if row["id"] == self.slide.currentData()
        )
        visual = slide.get("visual") or {}
        if visual.get("kind") != "comparison":
            return
        table = visual["comparison"]
        self._append_section(
            {
                "heading": slide["title"],
                "prompt": f"结合课堂知识表「{slide['title']}」填写概念、条件和区别，再用例题检查。",
                "response_kind": "table",
                "response_lines": 0,
                "columns": [table["dimension_label"], *table["columns"]],
                "row_labels": [row["label"] for row in table["rows"]],
            }
        )

    def _refresh(self):
        self.preview_error = ""
        try:
            candidate, actions = preview_structure_edits(
                self.candidate, self.operations
            )
        except DesktopPreparationError as error:
            candidate, actions = None, []
            self.preview_error = error.message_zh
        self.current_candidate = candidate
        for button in (self.link_button, self.add_notes, self.align_button):
            button.setEnabled(candidate is not None)
        self.undo_button.setEnabled(bool(self.operations))
        self.clear_button.setEnabled(bool(self.operations))
        if candidate is None:
            self.preview.setPlainText(
                "预览暂不可用；编辑内容和操作仍保留。\n" + self.preview_error
            )
            self.status.setText(self.preview_error)
            self._page_selected()
            return
        for combo, rows, placeholder in (
            (self.slide, candidate["slides"], False),
            (self.activity, candidate["activities"], True),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if placeholder:
                combo.addItem("请选择活动（不会自动推断）", None)
            for number, row in enumerate(rows, 1):
                combo.addItem(f"{number} · {row['title']}", row["id"])
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.preview.setPlainText(
            "本次待保存操作\n"
            + ("\n".join(actions) or "无")
            + "\n\n修改前\n"
            + classroom_timeline(self.candidate)
            + "\n\n修改后\n"
            + classroom_timeline(candidate)
        )
        self.status.setText(
            f"{len(self.operations)}个结构操作尚未保存。先核对预览，再另存修订版。"
        )
        self._page_selected()
