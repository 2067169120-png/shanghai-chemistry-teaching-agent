"""One searchable view of real saved preparation drafts and generation tasks."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget
from .components import page_scroll, section_title, set_status
from .studio_templates import text_label


def field(value, name, default=""):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class MyWorkPage(QWidget):
    open_requested = Signal(object)
    navigate_requested = Signal(str)

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.records = []
        self._loading = False
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)
        root.addWidget(section_title("我的备课", "找回已保存的草稿，查看生成结果；继续原来的工作，不重复生成。"))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("按课题查找已保存草稿或生成任务")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("搜索我的备课")
        self.kind = QComboBox()
        for text, key in (("全部", "all"), ("离线草稿", "draft"), ("生成任务", "task")):
            self.kind.addItem(text, key)
        self.kind.setAccessibleName("我的备课类型筛选")
        row.addWidget(self.query, 1)
        row.addWidget(self.kind)
        root.addLayout(row)
        self.status = text_label("进入页面后读取本机最近记录。")
        root.addWidget(self.status)
        self.results = QListWidget()
        self.results.setMinimumHeight(300)
        self.results.setWordWrap(True)
        self.results.setAccessibleName("已保存备课和任务列表")
        root.addWidget(self.results, 1)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("QuietButton")
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button = QPushButton("打开选中记录 →")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        new = QPushButton("新建备课")
        new.setObjectName("QuietButton")
        new.clicked.connect(lambda: self.navigate_requested.emit("preparation"))
        for button in (self.refresh_button, new, self.open_button):
            buttons.addWidget(button)
        root.addLayout(buttons)
        root.addWidget(text_label("离线草稿先预览再载入；生成任务只查看已有状态和结果，不在这里自动续跑或收费。"))
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self.query.textChanged.connect(self._filter)
        self.kind.currentIndexChanged.connect(self._filter)
        self.results.currentItemChanged.connect(lambda *_: self.open_button.setEnabled(self.results.currentItem() is not None))
        self.results.itemDoubleClicked.connect(lambda *_: self._open())

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self.refresh_button.setEnabled(False)
        set_status(self.status, "info", "正在读取本机记录…")
        self.tasks.submit("读取我的备课", self._read,
                          on_success=self._loaded, on_failure=self._failed)

    def _read(self):
        records, errors = [], []
        try:
            for option in self.facade.preparation_draft_options():
                records.append({"kind": "draft", "title": option["title"], "date": option["created_at"],
                                "status": "离线草稿", "value": option})
        except Exception:
            errors.append("离线草稿未能读取")
        try:
            labels = {"completed": "候选已生成", "failed": "生成失败", "cancelled": "已停止",
                      "running": "生成中", "prepared": "已准备", "queued": "排队中",
                      "blocked": "需处理", "needs_confirmation": "等待确认"}
            for task in self.facade.list_preparations(limit=50):
                records.append({"kind": "task", "title": field(task, "title_zh", "未命名备课"),
                                "date": field(task, "created_at"), "value": task,
                                "status": labels.get(field(task, "status"), "状态待查看")})
        except Exception:
            errors.append("生成任务未能读取")
        return sorted(records, key=lambda r: r["date"], reverse=True), errors

    def _loaded(self, result):
        self.records, errors = result
        self._loading = False
        self.refresh_button.setEnabled(True)
        set_status(self.status, "attention" if errors else "info",
                   f"已读取{len(self.records)}条最近记录（各来源最多50条）。" +
                   ("；".join(errors) if errors else "") +
                   ("暂无记录时，可先去备课页保存一份草稿。" if not self.records and not errors else ""))
        self._filter()

    def _failed(self, message):
        self._loading = False
        self.refresh_button.setEnabled(True)
        set_status(self.status, "error", message)

    def _filter(self, *_):
        self.results.clear()
        for record in self.records:
            if self.kind.currentData() not in ("all", record["kind"]):
                continue
            if self.query.text().strip().casefold() not in record["title"].casefold():
                continue
            item = QListWidgetItem(f"{record['title']}\n{record['status']}  ·  {record['date'].replace('T', ' ')[:16]}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.results.addItem(item)
        self.open_button.setEnabled(False)

    def _open(self):
        item = self.results.currentItem()
        if item:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
