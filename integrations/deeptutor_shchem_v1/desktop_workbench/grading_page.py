"""Batch overview that leads to the existing score writer and saved inputs."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, section_title


class GradingPage(QWidget):
    single_requested = Signal()
    batch_requested = Signal(str)
    roster_requested = Signal()

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        outer.addWidget(page_scroll(content))
        root.addWidget(section_title("作业批改", "按作业批次继续核对，或导入一份学生作答。原页、暂存输入和教师正式评分分别保存。"))
        actions = self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        roster = QPushButton("班级名册与作业收交")
        roster.clicked.connect(self.roster_requested.emit)
        batch = QPushButton("打开作业批次，继续批改")
        batch.clicked.connect(self._request_batch)
        single = QPushButton("导入 / 复核单份作答")
        single.setObjectName("QuietButton")
        single.clicked.connect(self.single_requested.emit)
        refresh = QPushButton("刷新")
        refresh.setObjectName("QuietButton")
        refresh.clicked.connect(self.refresh)
        for button in (roster, batch, single, refresh):
            actions.addWidget(button)
        root.addLayout(actions)
        self.status = QLabel("正在读取已保存作业批次…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.table = QTableWidget(0, 3)
        self.table.setMinimumHeight(200)
        self.table.setAccessibleName("已保存作业批次概览")
        self.table.setHorizontalHeaderLabels(["作业名称", "班级备注", "已关联作答"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.cellDoubleClicked.connect(lambda *_: self._request_batch())
        root.addWidget(self.table, 1)
        note = QLabel("在“班级名册与作业收交”按名单登记已交、未交、缺席和免交，再把已交作答打开为批改批次。空值不记零分。")
        note.setWordWrap(True)
        root.addWidget(note)
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self._loading:
            return
        if not hasattr(self.facade, "paths"):
            self.status.setText("可从单份作答开始，再将已有作答加入作业批次。")
            return
        from ..desktop_work_batches import WorkBatchStore
        self._loading = True
        self.tasks.submit("读取作业批次概览", lambda: WorkBatchStore(self.facade).list_batches(),
            on_success=self._ready, on_failure=self._failed, origin_route="grading")

    def _ready(self, rows):
        self._loading = False
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, text in enumerate((row["title"], row["class_label"] or "未指定", str(len(row["members"])))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, row["batch_id"])
                self.table.setItem(index, column, item)
        self.status.setText(f"{len(rows)}个已保存作业批次。打开批次后继续核对原作答与评分。" if rows else "还没有作业批次。先导入单份作答，再到作业批次选择本次提交。")

    def _failed(self, message):
        self._loading = False
        self.status.setText(message)

    def _request_batch(self):
        item = self.table.item(self.table.currentRow(), 0)
        batch_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.batch_requested.emit(batch_id or "")

    def resizeEvent(self, event):
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 720 else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)
