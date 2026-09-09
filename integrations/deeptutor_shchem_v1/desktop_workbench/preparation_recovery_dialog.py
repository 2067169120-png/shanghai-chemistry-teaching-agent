"""Inspect a failed model return and explicitly repair comparison tables offline."""

from collections.abc import Mapping
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import DesktopFacadeError
from ..desktop_preparation import DesktopPreparationError
from ..desktop_preparation_recovery import apply_returned_comparison_edits
from ..desktop_preparation_visual import normalize_slide_visual


def _readable_return(candidate):
    """Keep every returned field visible as plain text, including source notes."""
    labels = {
        "slides": "PPT页面",
        "content": "学生正文",
        "teacher_notes": "教师讲解与来源",
        "objectives": "目标",
        "activities": "课堂活动",
        "assessments": "评价",
        "lesson_stages": "教案环节",
        "homework": "作业",
        "uncertainties": "待核验",
        "title": "标题",
        "minutes": "分钟",
        "purpose": "作用",
        "visual": "图表",
        "comparison": "比较表",
        "dimension_label": "行标题列名称",
        "columns": "数据列表头",
        "rows": "表格行",
        "label": "名称",
        "values": "内容",
        "statement": "目标表述",
        "objective_numbers": "关联目标序号",
        "activity_numbers": "关联活动序号",
        "assessment_numbers": "关联评价序号",
        "slide_numbers": "关联页面序号",
        "teacher_action": "教师活动",
        "student_action": "学生活动",
        "materials": "材料",
        "evidence_of_learning": "学习证据",
        "success_criteria": "达标标准",
        "worksheet": "学习单",
        "instructions": "填写说明",
        "instruction": "任务要求",
        "sections": "部分",
        "heading": "小节",
        "prompt": "题目或观察提示",
        "response_kind": "作答区域类型",
        "response_lines": "预留行数",
        "row_labels": "行标题",
        "tasks": "任务",
        "description": "说明",
        "kind": "类型",
        "steps": "步骤",
        "detail": "详细内容",
        "image": "图片引用",
        "asset_id": "本地图片标识",
        "observation_prompt": "观察问题",
        "caption": "图注",
        "assessment": "检查标准",
    }
    lines = []

    def walk(value, level=0):
        if isinstance(value, Mapping):
            for key, item in value.items():
                heading = "  " * level + labels.get(key, key) + "："
                if isinstance(item, (Mapping, list)):
                    lines.append(heading)
                    walk(item, level + 1)
                else:
                    lines.append(heading + ("无" if item is None else str(item)))
        elif isinstance(value, list):
            for number, item in enumerate(value, 1):
                lines.append("  " * level + f"[{number}]")
                walk(item, level + 1)
        else:
            lines.append("  " * level + ("无" if value is None else str(value)))

    walk(candidate)
    return "\n".join(lines)


def _tables(candidate):
    """Bound the grid UI; other malformed returns remain inspectable, not repaired."""
    result = {}
    slides = candidate.get("slides", []) if isinstance(candidate, Mapping) else []
    if not isinstance(slides, list):
        return result
    for number, slide in enumerate(slides, 1):
        if not isinstance(slide, Mapping):
            continue
        visual = slide.get("visual")
        if not isinstance(visual, Mapping) or visual.get("kind") != "comparison":
            continue
        table = visual.get("comparison")
        if not isinstance(table, Mapping) or set(table) != {
            "dimension_label",
            "columns",
            "rows",
        }:
            continue
        columns, rows = table["columns"], table["rows"]
        if not isinstance(table["dimension_label"], str):
            continue
        if (
            not isinstance(columns, list)
            or not 1 <= len(columns) <= 8
            or not all(isinstance(c, str) for c in columns)
        ):
            continue
        if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
            continue
        if not all(
            isinstance(r, Mapping)
            and set(r) == {"label", "values"}
            and isinstance(r["label"], str)
            and isinstance(r["values"], list)
            and len(r["values"]) <= 8
            and all(isinstance(v, str) for v in r["values"])
            for r in rows
        ):
            continue
        result[number] = deepcopy(table)
    return result


class PreparationRecoveryDialog(QDialog):
    revision_saved = Signal(object)

    def __init__(self, facade, tasks, source, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.source = deepcopy(source)
        self.originals = _tables(source["candidate"])
        self.pending = {}
        self.splits = {}
        self.current_number = None
        self.busy = False
        self.setWindowTitle("检查已返回内容 · 本地修复表格")
        self.resize(980, 780)
        self.setMinimumSize(420, 560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "模型已返回，但未生成课件。这里不重新请求模型：可查看完整返回稿，"
            "手动修正比较表后另存导出。原返回稿保留；结构修复不等于知识、来源或课堂质量已经通过。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        error = QLabel(source.get("error_message", "请先核对返回内容与表格结构。"))
        error.setTextFormat(Qt.TextFormat.PlainText)
        error.setWordWrap(True)
        layout.addWidget(error)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        self.group = QComboBox()
        self.group.setMinimumWidth(0)
        self.group.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.group.setMinimumContentsLength(12)
        self.group.setAccessibleName("选择返回稿中的比较表")
        for number in self.originals:
            title = source["candidate"]["slides"][number - 1].get("title", "")
            self.group.addItem(f"第{number}页 · {title}", number)
        editor_layout.addWidget(self.group)
        help_text = QLabel(
            "首行编辑表头，首列编辑行标题；其余格子是逐列对应的内容。"
            "空白格表示原返回中缺少内容，不会自动猜补。每页数据列2—3列，内容行2—4行；长表可保留全部行拆页。"
        )
        help_text.setWordWrap(True)
        editor_layout.addWidget(help_text)
        self.table = QTableWidget()
        self.table.setAccessibleName("比较表原位编辑，首行为表头，首列为行标题")
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.verticalHeader().setMinimumSectionSize(44)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        # Qt does not always recompute wrapped row hints when stretched columns
        # shrink. Re-measure after width changes so narrow windows keep all text.
        self.row_resize_timer = QTimer(self)
        self.row_resize_timer.setSingleShot(True)
        self.row_resize_timer.timeout.connect(self._resize_table_rows)
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_args: self.row_resize_timer.start(0)
        )
        self.table.itemChanged.connect(self._changed)
        self.table.itemChanged.connect(lambda *_args: self.row_resize_timer.start(0))
        editor_layout.addWidget(self.table, 1)
        self.split_enabled = QCheckBox("拆为两页（保留整张表的全部行）")
        self.split_enabled.setAccessibleName("将当前比较表拆成两页")
        editor_layout.addWidget(self.split_enabled)
        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("在第几行后拆分"))
        self.split_after = QSpinBox()
        self.split_after.setRange(2, 4)
        self.split_after.setAccessibleName("第一页保留的内容行数，不含表头")
        split_row.addWidget(self.split_after)
        split_row.addWidget(QLabel("第一页分钟"))
        self.first_minutes = QSpinBox()
        self.first_minutes.setAccessibleName("拆页后的第一页分钟数")
        split_row.addWidget(self.first_minutes)
        editor_layout.addLayout(split_row)
        self.split_hint = QLabel()
        self.split_hint.setWordWrap(True)
        editor_layout.addWidget(self.split_hint)
        self.split_enabled.toggled.connect(self._split_changed)
        self.split_after.valueChanged.connect(self._split_changed)
        self.first_minutes.valueChanged.connect(self._split_changed)
        self.add_column = QPushButton("增加数据列")
        self.remove_column = QPushButton("删除当前列…")
        self.add_row = QPushButton("增加内容行")
        self.remove_row = QPushButton("删除当前行…")
        for buttons in (
            (self.add_column, self.remove_column),
            (self.add_row, self.remove_row),
        ):
            row = QHBoxLayout()
            for button in buttons:
                button.setObjectName("QuietButton")
                row.addWidget(button)
            editor_layout.addLayout(row)
        self.add_column.clicked.connect(lambda: self._add("column"))
        self.add_row.clicked.connect(lambda: self._add("row"))
        self.remove_column.clicked.connect(lambda: self._remove("column"))
        self.remove_row.clicked.connect(lambda: self._remove("row"))
        self.tabs.addTab(editor_page, "修正比较表")
        self.original_text = QPlainTextEdit()
        self.original_text.setReadOnly(True)
        self.original_text.setAccessibleName(
            "模型完整原始返回内容，只读且未通过内容审核"
        )
        self.original_text.setPlainText(_readable_return(source["candidate"]))
        read_page = QWidget()
        read_layout = QVBoxLayout(read_page)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("查找知识点、例题或页标题")
        self.search.setAccessibleName("在完整返回稿中查找")
        search_next = QPushButton("查找下一处")
        search_next.setObjectName("QuietButton")
        search_next.clicked.connect(self._find)
        self.search.returnPressed.connect(self._find)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(search_next)
        read_layout.addLayout(search_row)
        read_layout.addWidget(self.original_text, 1)
        self.tabs.addTab(read_page, "完整返回稿（只读）")
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setAccessibleName("比较表修改前后核对")
        self.tabs.addTab(self.review, "修改前后")
        self.tabs.currentChanged.connect(self._refresh_review)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("修订说明（可选，最多2000字）")
        self.note.setAccessibleName("表格修复说明")
        self.note.setMaximumHeight(64)
        layout.addWidget(self.note)
        self.status = QLabel(
            "选择表格并修改。知识内容仍需逐项检查；导出后可继续使用“修订课件与教案”。"
        )
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.save_button = QPushButton("本地另存并导出")
        self.save_button.setObjectName("PrimaryAction")
        self.save_button.setAccessibleName("保存表格修复并本地导出，不调用模型")
        self.save_button.clicked.connect(self._save)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.save_button)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.group.currentIndexChanged.connect(self._select)
        for index, table in enumerate(self.originals.values()):
            try:
                normalize_slide_visual(
                    {"kind": "comparison", "comparison": table, "steps": []}
                )
            except ValueError:
                self.group.setCurrentIndex(index)
                break
        self._select()
        if not self.originals:
            self.tabs.setCurrentIndex(1)
            self.status.setText(
                "当前返回稿没有可在此编辑的比较表；可查看完整内容，但本窗口不能修复其它结构错误。"
            )

    def _resize_table_rows(self):
        # The default delegate underestimates some wrapped CJK/fallback-font
        # cells. Measure the complete plain text with the actual column width.
        for row in range(self.table.rowCount()):
            height = 44
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is None:
                    continue
                document = QTextDocument()
                document.setDefaultFont(item.font())
                document.setPlainText(item.text())
                document.setTextWidth(max(24, self.table.columnWidth(column) - 16))
                height = max(height, int(document.size().height()) + 16)
            self.table.setRowHeight(row, height)

    def _select(self, _index=0):
        self.current_number = self.group.currentData()
        table = self.pending.get(
            self.current_number, self.originals.get(self.current_number)
        )
        self.table.blockSignals(True)
        self.table.clear()
        if table:
            width = (
                max(len(table["columns"]), *(len(r["values"]) for r in table["rows"]))
                + 1
            )
            self.table.setRowCount(len(table["rows"]) + 1)
            self.table.setColumnCount(width)
            self.table.setHorizontalHeaderLabels(
                ["行标题"] + [f"数据列{i}" for i in range(1, width)]
            )
            rows = [[table["dimension_label"], *table["columns"]]] + [
                [r["label"], *r["values"]] for r in table["rows"]
            ]
            for ri, row in enumerate(rows):
                for ci, text in enumerate(row):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    if ri == 0 or ci == 0:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    self.table.setItem(ri, ci, item)
        else:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
        self.table.blockSignals(False)
        for control in (self.split_enabled, self.split_after, self.first_minutes):
            control.blockSignals(True)
        config = self.splits.get(self.current_number, {})
        self.split_enabled.setChecked(bool(config))
        self.split_after.setValue(config.get("split_after_row", 2))
        total = self._original_minutes()
        self.first_minutes.setRange(1, max(1, total - 1))
        self.first_minutes.setValue(config.get("first_page_minutes", 1))
        for control in (self.split_enabled, self.split_after, self.first_minutes):
            control.blockSignals(False)
        self.row_resize_timer.start(0)
        self._controls()

    def _original_minutes(self):
        if self.current_number is None:
            return 0
        value = self.source["candidate"]["slides"][self.current_number - 1].get(
            "minutes"
        )
        return value if type(value) is int else 0

    def _split_changed(self, _value=None):
        if self.current_number is None or self.busy:
            return
        if self.split_enabled.isChecked():
            self.splits[self.current_number] = {
                "split_after_row": self.split_after.value(),
                "first_page_minutes": self.first_minutes.value(),
            }
        else:
            self.splits.pop(self.current_number, None)
        self._controls()
        self.status.setText(
            "拆页设置尚未保存；请在“修改前后”核对两页内容、用时和关联说明。"
        )

    def _edits(self):
        return [
            {
                "slide_number": number,
                "comparison": deepcopy(
                    self.pending.get(number, self.originals[number])
                ),
                **self.splits.get(number, {}),
            }
            for number in sorted(self.pending.keys() | self.splits.keys())
        ]

    def _find(self):
        text = self.search.text().strip()
        if not text:
            return
        if not self.original_text.find(text):
            cursor = self.original_text.textCursor()
            cursor.setPosition(0)
            self.original_text.setTextCursor(cursor)
            if not self.original_text.find(text):
                self.status.setText("完整返回稿中没有找到这段文字。")
                return
        self.status.setText("已定位；可继续查找下一处。原始返回稿只读，不会被修改。")

    def _grid(self):
        def text(row, column):
            item = self.table.item(row, column)
            return item.text().strip() if item else ""

        return {
            "dimension_label": text(0, 0),
            "columns": [text(0, c) for c in range(1, self.table.columnCount())],
            "rows": [
                {
                    "label": text(r, 0),
                    "values": [text(r, c) for c in range(1, self.table.columnCount())],
                }
                for r in range(1, self.table.rowCount())
            ],
        }

    def _changed(self, _item=None):
        if self.current_number is None:
            return
        table = self._grid()
        if table == self.originals[self.current_number]:
            self.pending.pop(self.current_number, None)
        else:
            self.pending[self.current_number] = table
        self._controls()
        self.status.setText(
            f"已修改{len(self.pending.keys() | self.splits.keys())}张比较表，尚未另存。请核对列标题与每一格内容。"
        )

    def _controls(self):
        available = self.current_number is not None and not self.busy
        self.add_column.setEnabled(available and self.table.columnCount() < 4)
        self.add_row.setEnabled(available and self.table.rowCount() < 9)
        self.remove_column.setEnabled(available and self.table.columnCount() > 3)
        self.remove_row.setEnabled(available and self.table.rowCount() > 3)
        self.save_button.setEnabled(bool(self.pending or self.splits) and not self.busy)
        rows, total = self.table.rowCount() - 1, self._original_minutes()
        possible = 4 <= rows <= 8 and total >= 2
        # Keep an existing option removable even after a row edit makes it invalid.
        self.split_enabled.setEnabled(
            available and (possible or self.split_enabled.isChecked())
        )
        enabled = available and self.split_enabled.isChecked()
        self.split_after.setEnabled(enabled)
        self.first_minutes.setEnabled(enabled)
        if enabled:
            cut, first = self.split_after.value(), self.first_minutes.value()
            self.split_hint.setText(
                f"拆为{cut}行＋{rows - cut}行；用时{first}＋{total - first}＝{total}分钟。"
                "每页须2—4行。标题加（1/2）（2/2），两页沿用原正文、备注与教学关联；"
                "以上为返回稿用时，最终仍按整课时长核对。文字中的旧页码需在导出后核对。"
            )
        else:
            self.split_hint.setText(
                "4—8行且原页至少2分钟时可拆为两页；每页沿用完整表头，总用时不增加。"
            )

    def _add(self, axis):
        if self.busy or self.current_number is None:
            return
        if axis == "column" and self.table.columnCount() < 4:
            self.table.insertColumn(self.table.columnCount())
        elif axis == "row" and self.table.rowCount() < 9:
            self.table.insertRow(self.table.rowCount())
        else:
            return
        self._changed()

    def _remove(self, axis):
        if self.busy or self.current_number is None:
            return
        index = (
            self.table.currentColumn() if axis == "column" else self.table.currentRow()
        )
        count = self.table.columnCount() if axis == "column" else self.table.rowCount()
        label = "列" if axis == "column" else "行"
        if index <= 0 or count <= 3:
            self.status.setText(
                f"请先选中要删除的数据{label}；不能删除表头行或行标题列，至少保留2行、2个数据列。"
            )
            return
        if (
            QMessageBox.question(
                self,
                f"删除当前{label}",
                f"将从本次修订中删除选中{label}及其全部内容。请确认没有需要保留的文字。原始返回稿仍保留。是否删除？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.table.blockSignals(True)
        if axis == "column":
            self.table.removeColumn(index)
        else:
            self.table.removeRow(index)
        self.table.blockSignals(False)
        self._changed()

    def _refresh_review(self, _index=0):
        lines = []
        for edit in self._edits():
            number, table = edit["slide_number"], edit["comparison"]
            lines.extend(
                [
                    f"第{number}页\n原表：",
                    _readable_return(self.originals[number]),
                    "修订：",
                    _readable_return(table),
                    "",
                ]
            )
            if "split_after_row" in edit:
                cut, first = edit["split_after_row"], edit["first_page_minutes"]
                total = self.source["candidate"]["slides"][number - 1]["minutes"]
                lines.extend(
                    [
                        f"拆页：第1页{first}分钟，第2页{total - first}分钟；原页合计{total}分钟不变。",
                        "第一页完整表：",
                        _readable_return({**table, "rows": table["rows"][:cut]}),
                        "第二页完整表：",
                        _readable_return({**table, "rows": table["rows"][cut:]}),
                        "正文、备注与目标/活动/评价关联在两页保留。后续页码顺延；文字中的旧页码请核对。",
                        "",
                    ]
                )
        self.review.setPlainText("\n".join(lines) or "还没有修改。")

    def _busy(self, value):
        self.busy = value
        self.tabs.setEnabled(not value)
        self.note.setEnabled(not value)
        self.close_button.setEnabled(not value)
        self._controls()

    def _save(self):
        if self.busy or not (self.pending or self.splits):
            return
        note = self.note.toPlainText().strip()
        if len(note) > 2000:
            self.status.setText("修订说明超过2000字。")
            return
        edits = self._edits()
        try:
            apply_returned_comparison_edits(self.source["candidate"], edits)
        except DesktopPreparationError as exc:
            self.status.setText("修订尚不完整：" + exc.message_zh)
            return

        def export():
            try:
                return self.facade.repair_returned_preparation(
                    self.source["task_id"],
                    self.source["source_revision"],
                    edits,
                    note=note,
                )
            except DesktopFacadeError as exc:
                return {"recovery_error": exc.message_zh}

        self._busy(True)
        self.status.setText("正在验证全部返回内容并本地另存导出，不调用模型…")
        try:
            self.tasks.submit(
                "本地修复备课表格",
                export,
                on_success=self._saved,
                on_failure=self._failed,
            )
        except Exception:  # noqa: BLE001 - keep edits, do not expose arbitrary errors
            self._failed("")

    def _saved(self, summary):
        self._busy(False)
        if isinstance(summary, dict) and "recovery_error" in summary:
            self.status.setText(summary["recovery_error"])
            return
        self.revision_saved.emit(summary)
        status = (
            summary.get("status")
            if isinstance(summary, dict)
            else getattr(summary, "status", "")
        )
        if status == "completed":
            self.pending.clear()
            self.splits.clear()
            self.accept()
        else:
            self.status.setText(
                "本地修订任务已保存，但导出未完成。可在最近备课中重试本地导出；原返回稿不变。"
            )

    def _failed(self, _message):
        self._busy(False)
        self.status.setText(
            "本地处理未完成，编辑内容仍保留。原返回稿未修改，也未重新请求模型。"
        )

    def reject(self):
        if self.busy:
            return
        if (self.pending or self.splits) and QMessageBox.question(
            self,
            "未保存的表格修订",
            "关闭会放弃本次未保存的修改，原始返回稿仍保留。是否关闭？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
