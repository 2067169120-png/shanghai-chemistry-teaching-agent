"""Paged search across all existing preparation drafts and generation tasks."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget
from .components import page_scroll, section_title, set_status
from .studio_templates import text_label


class MyWorkPage(QWidget):
    open_requested = Signal(object)
    navigate_requested = Signal(str)
    PAGE_SIZE = 25

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.records = []
        self._loading = False
        self._epoch = 0
        self._task_id = None
        self._offset = 0
        self._total = 0
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)
        root.addWidget(section_title("我的备课", "搜索全部历史作品，再分页；较早的草稿和生成任务也能找回。"))
        self.query = QLineEdit()
        self.query.setPlaceholderText("按课题搜索全部草稿与生成任务，不限最近50条")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("搜索全部历史备课")
        root.addWidget(self.query)
        row = QHBoxLayout()
        self.kind = QComboBox()
        for text, key in (("全部作品", "all"), ("离线草稿", "draft"), ("生成任务", "task")):
            self.kind.addItem(text, key)
        self.kind.setAccessibleName("作品类型")
        self.order = QComboBox()
        for text, key in (("最近保存优先", "newest"), ("最早保存优先", "oldest"), ("按课题排序", "title")):
            self.order.addItem(text, key)
        self.order.setAccessibleName("作品排序")
        row.addWidget(self.kind)
        row.addWidget(self.order)
        row.addStretch(1)
        root.addLayout(row)
        self.status = text_label("进入页面后读取本机作品；查看不会调用模型。")
        root.addWidget(self.status)
        self.results = QListWidget()
        self.results.setMinimumHeight(270)
        self.results.setWordWrap(True)
        self.results.setAccessibleName("当前页备课记录")
        root.addWidget(self.results, 1)
        paging = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.page_label = text_label("第 1 页")
        for button in (self.previous, self.next):
            button.setObjectName("QuietButton")
            button.setEnabled(False)
        self.previous.clicked.connect(lambda: self._page(-1))
        self.next.clicked.connect(lambda: self._page(1))
        paging.addWidget(self.previous)
        paging.addWidget(self.page_label, 1)
        paging.addWidget(self.next)
        root.addLayout(paging)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("QuietButton")
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button = QPushButton("打开选中记录 →")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        self.resume_button = QPushButton("返回备课继续编辑")
        self.resume_button.setObjectName("QuietButton")
        self.resume_button.clicked.connect(lambda: self.navigate_requested.emit("preparation"))
        for button in (self.refresh_button, self.resume_button, self.open_button):
            buttons.addWidget(button)
        root.addLayout(buttons)
        root.addWidget(text_label("离线草稿先预览再载入；任务只查看已有状态和结果，不自动续跑。排序时间：草稿保存时间 / 任务更新时间。"))
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self.query.textChanged.connect(self._filter)
        self.kind.currentIndexChanged.connect(self._filter)
        self.order.currentIndexChanged.connect(self._filter)
        self.results.currentItemChanged.connect(lambda *_: self.open_button.setEnabled(self.results.currentItem() is not None and not self._loading))
        self.results.itemDoubleClicked.connect(lambda *_: self._open())

    def _filter(self, *_):
        self._offset = 0
        self._epoch += 1  # invalidate a reply as soon as its query becomes obsolete
        self.results.clear()
        self.records = []
        self.open_button.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._loading = True
        set_status(self.status, "info", "正在搜索全部历史作品…")
        self._timer.start()

    def refresh(self):
        self._timer.stop()
        self._epoch += 1
        epoch = self._epoch
        if self._task_id:
            self.tasks.cancel(self._task_id)
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        set_status(self.status, "info", "正在搜索全部历史作品…")
        request = dict(query=self.query.text(), kind=self.kind.currentData(), order=self.order.currentData(),
                       offset=self._offset, limit=self.PAGE_SIZE)
        self._task_id = self.tasks.submit(
            "查找历史备课", lambda: self.facade.search_preparation_work(**request),
            on_success=lambda result: self._loaded(result, epoch),
            on_failure=lambda message: self._failed(message, epoch),
        )

    def _loaded(self, result, epoch=None):
        if epoch is not None and epoch != self._epoch:
            return
        self._task_id = None
        self._loading = False
        self.refresh_button.setEnabled(True)
        self.records = result["items"]
        self._offset, self._total = result["offset"], result["total"]
        self.results.clear()
        for record in self.records:
            item = QListWidgetItem(f"{record['title']}\n{record['status']}  ·  {record['date'].replace('T', ' ')[:16]}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.results.addItem(item)
        self.open_button.setEnabled(False)
        self.previous.setEnabled(self._offset > 0)
        self.next.setEnabled(result["has_more"])
        self.page_label.setText(f"第 {self._offset // self.PAGE_SIZE + 1} / {max(1, (self._total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)} 页 · 每页{self.PAGE_SIZE}条")
        warnings = result["warnings"]
        set_status(self.status, "attention" if warnings else "info", f"匹配 {self._total} 条，本页 {len(self.records)} 条。" +
                   ("；".join(warnings) if warnings else "已检索全部历史记录，不限最近50条。"))

    def _failed(self, message, epoch=None):
        if epoch is not None and epoch != self._epoch:
            return
        self._loading = False
        self._task_id = None
        self.refresh_button.setEnabled(True)
        self.results.clear()
        set_status(self.status, "error", message)

    def _page(self, delta):
        if self._loading:
            return
        self._offset = max(0, self._offset + delta * self.PAGE_SIZE)
        self.refresh()

    def _open(self):
        item = self.results.currentItem()
        if item and not self._loading:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
