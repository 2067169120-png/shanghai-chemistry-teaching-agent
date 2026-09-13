"""Local form for a missing knowledge, worked-example or note page."""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation import DesktopPreparationError
from ..desktop_preparation_structure import preview_structure_edits


class PreparationInsertPageDialog(QDialog):
    def __init__(self, candidate, anchor_id, parent=None):
        super().__init__(parent)
        self.candidate = deepcopy(candidate)
        self.anchor_id = anchor_id
        self.operation = None
        anchor = next(row for row in candidate["slides"] if row["id"] == anchor_id)
        self.setWindowTitle("补充知识或例题页 · 本地预览")
        self.resize(820, 780)
        self.setMinimumSize(420, 520)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        root = QVBoxLayout(panel)
        intro = QLabel(
            "用于补入讲义知识、完整例题、讲评或笔记总结。只使用你填写的内容，不自动补写化学事实。"
            "正文会投影；来源和讲解备注进入教师备注。例题与答案需要分开的，请分别补页。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        labels = [
            name + "：" + ("、".join(anchor[key]) or "未关联")
            for key, name in (
                ("activity_ids", "活动"),
                ("objective_ids", "目标"),
                ("assessment_ids", "评价"),
            )
        ]
        label = QLabel(
            f"所选页「{anchor['title']}」原有{anchor['minutes']}分钟。新增页沿用其关联："
            + "；".join(labels)
            + "。这不代表新内容与评价已匹配，请核对。"
        )
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(label)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title = QLineEdit()
        self.title.setPlaceholderText("学生看到的学科标题，最多80字")
        self.purpose = QLineEdit()
        self.purpose.setPlaceholderText("本页为什么需要放在这里，最多500字")
        self.source_reference = QPlainTextEdit()
        self.source_reference.setMaximumHeight(82)
        self.source_reference.setPlaceholderText(
            "资料名称及页码/区块；自行编写时注明教师自编，非教材原句。最多2000字。"
        )
        for editor, name in (
            (self.title, "页面标题"),
            (self.purpose, "教学作用"),
            (self.source_reference, "来源说明"),
        ):
            editor.setAccessibleName("新增页" + name)
            form.addRow(name, editor)
        self.position = QComboBox()
        self.position.addItem("插在所选页之前", "before")
        self.position.addItem("插在所选页之后", "after")
        self.minutes = QSpinBox()
        self.minutes.setRange(1, max(1, anchor["minutes"] - 1))
        self.minutes.setSuffix(" 分钟")
        self.minutes.setAccessibleName("从所选原页分配给新增页的分钟数")
        form.addRow("补页位置", self.position)
        form.addRow("从所选原页分配", self.minutes)
        self.remaining = QLabel()
        self.remaining.setWordWrap(True)
        self.minutes.valueChanged.connect(
            lambda value: self.remaining.setText(
                f"新增页{value}分钟，原页剩余{anchor['minutes'] - value}分钟；总课时不增加。"
                "若时间不够，请取消并先调整活动安排，不要挤掉学生作答时间。"
            )
        )
        self.minutes.valueChanged.emit(self.minutes.value())
        form.addRow(self.remaining)
        self.kind = QComboBox()
        self.kind.addItem("正文／例题页", "text")
        self.kind.addItem("正文＋知识比较表", "comparison")
        form.addRow("页面内容", self.kind)
        root.addLayout(form)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.content = QPlainTextEdit()
        self.content.setMinimumHeight(170)
        self.content.setAccessibleName("新增页学生可见正文")
        self.content.setPlaceholderText(
            "每段一行，保留完整定义、条件或题干；空行仅作间隔。知识表页也需填写主题或条件说明。"
        )
        self.tabs.addTab(self.content, "学生正文")
        self.table = QTableWidget(5, 4)
        self.table.setAccessibleName("新增知识表，第一行填写表头，随后填写二至四行内容")
        self.table.setHorizontalHeaderLabels(
            ["维度／行标题", "对象1", "对象2", "对象3（可选）"]
        )
        self.table.setVerticalHeaderLabels(["表头", "行1", "行2", "行3", "行4"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setWordWrap(True)
        self.table.setMinimumHeight(210)
        self.tabs.addTab(self.table, "知识表")
        self.teacher_notes = QPlainTextEdit()
        self.teacher_notes.setMinimumHeight(170)
        self.teacher_notes.setAccessibleName("新增页教师讲解备注，不投影")
        self.teacher_notes.setPlaceholderText(
            "可填来源取舍、讲解、等待及过渡；不是学生答案栏。最多10000字。"
        )
        self.tabs.addTab(self.teacher_notes, "讲解备注")
        self.tabs.setTabEnabled(1, False)
        self.kind.currentIndexChanged.connect(
            lambda: self.tabs.setTabEnabled(1, self.kind.currentData() == "comparison")
        )
        root.addWidget(self.tabs)
        help_text = QLabel(
            "知识表第一行填表头；至少2个对象、2个数据行，最多3个对象、4行。标题最多32字、每格最多120字。空行可不填，已填写内容不会自动截掉。"
        )
        help_text.setWordWrap(True)
        root.addWidget(help_text)
        scroll.setWidget(panel)
        outer.addWidget(scroll, 1)
        self.status = QLabel("先补齐内容和来源，再加入本次修订预览；尚不会保存文件。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("加入本次修订预览")
        self.add_button.clicked.connect(self._add)
        buttons.addWidget(self.add_button)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        outer.addLayout(buttons)

    def _cell(self, row, column):
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def _visual(self):
        if self.kind.currentData() != "comparison":
            return None
        columns = 3 if any(self._cell(row, 3) for row in range(5)) else 2
        rows = [
            {
                "label": self._cell(row, 0),
                "values": [self._cell(row, column) for column in range(1, columns + 1)],
            }
            for row in range(1, 5)
            if any(self._cell(row, column) for column in range(4))
        ]
        return {
            "kind": "comparison",
            "steps": [],
            "comparison": {
                "dimension_label": self._cell(0, 0),
                "columns": [self._cell(0, column) for column in range(1, columns + 1)],
                "rows": rows,
            },
        }

    def _add(self):
        operation = {
            "kind": "insert_page",
            "anchor_slide_id": self.anchor_id,
            "position": self.position.currentData(),
            "minutes": self.minutes.value(),
            "page": {
                "title": self.title.text(),
                "purpose": self.purpose.text(),
                "content": [
                    line.strip()
                    for line in self.content.toPlainText().splitlines()
                    if line.strip()
                ],
                "source_reference": self.source_reference.toPlainText(),
                "teacher_notes": self.teacher_notes.toPlainText(),
                "visual": self._visual(),
            },
        }
        try:
            preview_structure_edits(self.candidate, [operation])
        except DesktopPreparationError as error:
            self.status.setText(error.message_zh)
            return
        self.operation = operation
        self.accept()
