"""Teacher-controlled page ordering and linked classroom content inspection."""

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_sequence import classroom_sequence


class PreparationSequenceWidget(QWidget):
    operation_requested = Signal(object)
    undo_requested = Signal()
    page_edit_requested = Signal(str)
    page_insert_requested = Signal(str)

    def __init__(self, candidate, parent=None):
        super().__init__(parent)
        self.pages = []
        self.editable_ids = {slide["id"] for slide in candidate["slides"]}
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        panel = QWidget()
        root = QVBoxLayout(panel)
        self.scroll.setWidget(panel)
        outer.addWidget(self.scroll)
        intro = QLabel(
            "按实际页序检查：讲解是否先于练习，反馈是否回应题目，学生最后能记下什么。"
            "按住 Shift 选择相邻多页，可将例题与解析一起移动。章节首页固定。"
            "这里呈现原稿已有的关联，不自动判断题目与答案是否匹配。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setMinimumHeight(340)
        root.addWidget(self.splitter, 1)
        left = QWidget()
        left.setMinimumWidth(0)
        left.setMinimumHeight(330)
        listing = QVBoxLayout(left)
        listing.setContentsMargins(0, 0, 0, 0)
        self.pages_list = QListWidget()
        self.pages_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.pages_list.setWordWrap(False)
        self.pages_list.setMinimumHeight(130)
        self.pages_list.setAccessibleName("实际授课页序，可选择相邻多页一起移动")
        self.pages_list.itemSelectionChanged.connect(self._selection_changed)
        listing.addWidget(self.pages_list, 1)
        buttons = QHBoxLayout()
        self.up_button = QPushButton("上移一页")
        self.down_button = QPushButton("下移一页")
        self.up_button.clicked.connect(lambda: self._step(-1))
        self.down_button.clicked.connect(lambda: self._step(1))
        buttons.addWidget(self.up_button)
        buttons.addWidget(self.down_button)
        listing.addLayout(buttons)
        self.destination = QComboBox()
        self.destination.setMinimumContentsLength(12)
        self.destination.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.destination.setMinimumWidth(0)
        self.destination.setAccessibleName("选择整组页面移入的位置")
        listing.addWidget(self.destination)
        self.move_button = QPushButton("移动到选定位置")
        self.move_button.clicked.connect(self._move)
        listing.addWidget(self.move_button)
        self.undo_button = QPushButton("撤销最后一个结构操作")
        self.undo_button.clicked.connect(self.undo_requested.emit)
        listing.addWidget(self.undo_button)
        self.splitter.addWidget(left)
        right = QWidget()
        right.setMinimumWidth(0)
        right.setMinimumHeight(220)
        details = QVBoxLayout(right)
        details.setContentsMargins(0, 0, 0, 0)
        self.selection_label = QLabel()
        self.selection_label.setWordWrap(True)
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.selection_label)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setUsesScrollButtons(True)
        self.detail_tabs.setMinimumHeight(140)
        self.student_text = self._text_tab(
            "学生看到的文字", "本页学生可见文字，不含教师备注"
        )
        self.teacher_text = self._text_tab(
            "讲解与衔接", "本页教学意图、教师备注和原有教案活动"
        )
        self.links_text = self._text_tab(
            "目标与学习单", "本页明确关联的目标、评价和活动学习单"
        )
        self.timing_text = self._text_tab(
            "课时检查", "按当前页序和原有活动预算检查课时"
        )
        details.addWidget(self.detail_tabs, 1)
        self.edit_button = QPushButton("编辑本页文字")
        self.edit_button.clicked.connect(self._edit)
        edit_actions = QHBoxLayout()
        edit_actions.addWidget(self.edit_button)
        self.insert_button = QPushButton("补充知识或例题页")
        self.insert_button.setToolTip("选中非首页、唯一活动归属且至少2分钟的页面，新增页从该页分配时间。")
        self.insert_button.clicked.connect(self._insert)
        edit_actions.addWidget(self.insert_button)
        details.addLayout(edit_actions)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([320, 520])
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.set_candidate(candidate)

    def _text_tab(self, title, accessible_name):
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setMinimumWidth(0)
        editor.setAccessibleName(accessible_name)
        self.detail_tabs.addTab(editor, title)
        return editor

    def set_candidate(self, candidate, *, has_operations=False, error=""):
        selected = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.pages_list.selectedItems()
        }
        self.pages_list.blockSignals(True)
        self.pages_list.clear()
        report = classroom_sequence(candidate) if candidate is not None else None
        self.pages = report["pages"] if report else []
        self.pages_by_id = {page["id"]: page for page in self.pages}
        for page in self.pages:
            activities = (
                " / ".join(a["title"] for a in page["activities"]) or "活动未关联"
            )
            item = QListWidgetItem(
                f"{page['page']:02d}　{page['title']}\n"
                f"{page['period_label']} · {page['minutes']}分钟 · {activities}"
            )
            item.setData(Qt.ItemDataRole.UserRole, page["id"])
            item.setToolTip(item.text())
            item.setSizeHint(QSize(0, 60))
            self.pages_list.addItem(item)
            item.setSelected(page["id"] in selected)
        if not self.pages_list.selectedItems() and self.pages:
            self.pages_list.item(0).setSelected(True)
        if self.pages_list.selectedItems():
            current = self.pages_list.selectedItems()[0]
            self.pages_list.setCurrentItem(
                current, QItemSelectionModel.SelectionFlag.NoUpdate
            )
            self.pages_list.scrollToItem(current)
        self.pages_list.blockSignals(False)
        self.undo_button.setEnabled(has_operations)
        self.timing_text.setPlainText(
            (
                f"当前课件共{len(self.pages)}页、{report['total_minutes']}分钟。\n\n"
                + "\n".join(report["warnings"] or ["未发现页序与活动分钟数差异。"])
                + "\n\n这不是教学审核；仍需核对练习顺序、等待时间、过渡及实际投影。"
            )
            if report
            else "预览暂不可用：" + error
        )
        self._selection_changed()
        if error:
            self.status.setText("预览暂不可用，未显示旧稿：" + error)
        elif report and report["warnings"]:
            self.status.setText(
                f"课时检查有{len(report['warnings'])}项提示，移动不会自动改写教案或分钟数。"
            )

    def _selected_indexes(self):
        return sorted(
            self.pages_list.row(item) for item in self.pages_list.selectedItems()
        )

    def _selection_changed(self):
        indexes = self._selected_indexes()
        contiguous = bool(indexes) and indexes == list(
            range(indexes[0], indexes[-1] + 1)
        )
        movable = contiguous and indexes[0] != 0
        self.up_button.setEnabled(movable and indexes[0] > 1)
        self.down_button.setEnabled(movable and indexes[-1] < len(self.pages) - 1)
        self.move_button.setEnabled(movable)
        self.destination.setEnabled(movable)
        self.edit_button.setEnabled(
            len(indexes) == 1 and self.pages[indexes[0]]["id"] in self.editable_ids
        )
        self.insert_button.setEnabled(
            len(indexes) == 1 and indexes[0] > 0
            and len(self.pages[indexes[0]]["activities"]) == 1
            and self.pages[indexes[0]]["minutes"] >= 2
        )
        previous_target = self.destination.currentData()
        self.destination.clear()
        if movable:
            for i, page in enumerate(self.pages):
                if i > 0 and i not in indexes and i != indexes[-1] + 1:
                    self.destination.addItem(
                        f"第{page['page']}页「{page['title']}」之前", page["id"]
                    )
            if indexes[-1] != len(self.pages) - 1:
                self.destination.addItem("课件末尾", None)
            match = self.destination.findData(previous_target)
            if match >= 0:
                self.destination.setCurrentIndex(match)
        self.move_button.setEnabled(movable and self.destination.count() > 0)
        self.status.setText(
            "可移动所选页组；组内顺序、正文、图片与分钟数均保留，另存后生效。"
            if movable
            else "章节首页固定，不能移动。"
            if indexes and indexes[0] == 0
            else "请选择相邻的多页，或只选择一页。"
        )
        if not indexes:
            self.selection_label.setText("尚未选择页面")
            for editor in (self.student_text, self.teacher_text, self.links_text):
                editor.clear()
            return
        page = self.pages[indexes[0]]
        prefix = (
            f"已选第{indexes[0] + 1}—{indexes[-1] + 1}页；下方显示首个选中页。\n"
            if len(indexes) > 1
            else ""
        )
        self.selection_label.setText(
            prefix
            + f"第{page['page']}页 · {page['title']} · {page['start_minute']}—{page['end_minute']}分钟"
        )
        self.student_text.setPlainText(page["student_text"])
        teacher = [
            "本页教学意图",
            page["purpose"],
            "",
            "本页教师备注（不投影）",
            page["teacher_notes"] or "未填写",
        ]
        for activity in page["activities"]:
            teacher.extend(
                [
                    "",
                    "关联活动 · " + activity["title"],
                    "教师：" + activity["teacher_action"],
                    "学生：" + activity["student_action"],
                ]
            )
        for stage in page["stages"]:
            teacher.extend(
                [
                    "",
                    "教案环节 · " + stage["title"],
                    "教师：" + stage["teacher_action"],
                    "学生：" + stage["student_action"],
                    "检查：" + stage["assessment"],
                ]
            )
        self.teacher_text.setPlainText("\n".join(teacher))
        links = ["学习目标（原稿明确关联，不代表已经达成）"]
        links.extend(f"{row['id']} {row['statement']}" for row in page["objectives"])
        if not page["objectives"]:
            links.append("未关联")
        links.extend(["", "学习证据与达标标准"])
        for assessment in page["assessments"]:
            links.extend(
                [
                    assessment["title"],
                    "证据：" + assessment["evidence_of_learning"],
                    *assessment["success_criteria"],
                ]
            )
        if not page["assessments"]:
            links.append("未关联")
        links.extend(["", "活动学习单（同活动共享，并非自动匹配本页题目）"])
        for activity in page["activities"]:
            worksheet = activity.get("worksheet")
            links.extend(["", activity["title"]])
            if not worksheet:
                links.append("此活动没有学习单。")
                continue
            links.extend([worksheet["title"], *worksheet["instructions"]])
            for section in worksheet["sections"]:
                links.extend(["", section["heading"], section["prompt"]])
                if section["response_kind"] == "table":
                    links.extend(
                        [
                            "表头：" + " / ".join(section["columns"]),
                            "行项：" + " / ".join(section["row_labels"]),
                        ]
                    )
                else:
                    links.append(f"书写留白：{section['response_lines']}行")
        self.links_text.setPlainText("\n".join(links))
        if page["id"] not in self.editable_ids:
            self.status.setText(
                "这是本次新增页；请先另存修订版，再打开新稿编辑该页文字。"
            )

    def _request_move(self, target):
        indexes = self._selected_indexes()
        if not self.move_button.isEnabled() or not indexes:
            return
        self.operation_requested.emit(
            {
                "kind": "move_slides",
                "slide_ids": [self.pages[i]["id"] for i in indexes],
                "before_slide_id": target,
            }
        )

    def _step(self, direction):
        indexes = self._selected_indexes()
        button = self.up_button if direction < 0 else self.down_button
        if not button.isEnabled() or not indexes:
            return
        target_index = indexes[0] - 1 if direction < 0 else indexes[-1] + 2
        target = (
            self.pages[target_index]["id"] if target_index < len(self.pages) else None
        )
        self._request_move(target)

    def _move(self):
        self._request_move(self.destination.currentData())

    def _edit(self):
        indexes = self._selected_indexes()
        if self.edit_button.isEnabled() and len(indexes) == 1:
            self.page_edit_requested.emit(self.pages[indexes[0]]["id"])

    def _insert(self):
        indexes = self._selected_indexes()
        if self.insert_button.isEnabled() and len(indexes) == 1:
            self.page_insert_requested.emit(self.pages[indexes[0]]["id"])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = (
            Qt.Orientation.Vertical if self.width() < 720 else Qt.Orientation.Horizontal
        )
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setMinimumHeight(580 if self.width() < 720 else 340)
            self.splitter.setSizes([250, 330] if self.width() < 720 else [320, 520])
        QTimer.singleShot(0, self._reveal_selection)

    def _reveal_selection(self):
        selected = self.pages_list.selectedItems()
        if selected:
            self.pages_list.scrollToItem(selected[0])
