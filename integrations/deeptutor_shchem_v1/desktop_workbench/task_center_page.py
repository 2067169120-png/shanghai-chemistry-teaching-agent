"""Visible worker receipts; returning to a page never reruns an operation."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, section_title

STATUS_LABELS = {"queued": "等待处理", "running": "正在处理", "cancel_requested": "正在停止",
    "completed": "已完成", "failed": "失败，待处理", "cancelled": "已停止", "interrupted": "中断，待核对"}


class TaskCenterPage(QWidget):
    navigate_requested = Signal(str)

    def __init__(self, tasks, parent=None):
        super().__init__(parent)
        self.tasks = tasks
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        outer.addWidget(page_scroll(content))
        root.addWidget(section_title("任务中心", "查看处理状态和原教学上下文；中断后先回原页面核对已保存结果。"))
        self.filter = QComboBox()
        self.filter.addItem("全部任务", "all")
        self.filter.addItem("正在处理", "active")
        self.filter.addItem("需要处理", "attention")
        self.filter.setAccessibleName("按任务状态筛选")
        self.filter.currentIndexChanged.connect(self.refresh)
        root.addWidget(self.filter)
        self.table = QTableWidget(0, 4)
        self.table.setMinimumHeight(200)
        self.table.setHorizontalHeaderLabels(["任务", "状态", "教学上下文", "更新时间"])
        self.table.setAccessibleName("当前与历史任务")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda *_: self._return())
        root.addWidget(self.table, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setAccessibleName("所选任务状态说明")
        root.addWidget(self.detail)
        actions = self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cancel_button = QPushButton("停止所选任务")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.clicked.connect(self._cancel)
        self.return_button = QPushButton("回原页面核对")
        self.return_button.clicked.connect(self._return)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.return_button)
        actions.addStretch()
        root.addLayout(actions)
        self.tasks.history_changed.connect(self.refresh)
        self.refresh()

    def selected(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def refresh(self):
        selected = self.selected()
        selected_id = selected["task_id"] if selected else None
        mode = self.filter.currentData()
        from ..desktop_teacher_workspace import ACTIVE
        rows = [row for row in self.tasks.records() if mode == "all"
                or (mode == "active" and row["status"] in ACTIVE)
                or (mode == "attention" and row["status"] in {"failed", "interrupted"})]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            context = row.get("context", {})
            values = [row["label"], STATUS_LABELS[row["status"]],
                " · ".join(context.get(key, "") for key in ("term", "class_label", "work_label") if context.get(key)) or "未指定",
                row["updated_at"][:16].replace("T", " ") + " UTC"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(index, column, item)
            if row["task_id"] == selected_id:
                self.table.selectRow(index)
        self._selection()
        if not rows:
            self.detail.setText(self.tasks.history_error or "当前筛选没有任务。导入、读取或导出后，这里会显示处理状态。")
        elif self.selected() is None:
            self.detail.setText(self.tasks.history_error or "选择任务查看处理状态，或返回原页面核对已保存结果。")

    def _selection(self):
        row = self.selected()
        self.cancel_button.setEnabled(bool(row and row["status"] in {"queued", "running"}))
        self.return_button.setEnabled(row is not None)
        if row:
            self.detail.setText((self.tasks.history_error + "\n" if self.tasks.history_error else "") + row.get("message", ""))

    def _cancel(self):
        row = self.selected()
        if row and not self.tasks.cancel(row["task_id"]):
            self.detail.setText("当前步骤已经完成或正在保存，请稍后核对状态。")

    def _return(self):
        row = self.selected()
        if row:
            self.navigate_requested.emit(row.get("route", "home"))

    def resizeEvent(self, event):
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 720 else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)
