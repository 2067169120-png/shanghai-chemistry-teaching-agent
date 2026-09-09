"""Find a whole imported lesson plan; summaries only lead to original content."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from shiboken6 import isValid

from ..desktop_lecture_library import lecture_catalog, search_lectures
from .components import section_title, set_status
from .import_word_dialog import ImportWordDialog


class LectureLibraryDialog(QDialog):
    def __init__(self, facade, tasks, parent=None, *, lesson_topic=""):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.reference = None
        self._items = []
        self._closed = False
        self._job = None
        self.setWindowTitle("按知识点查找原教案")
        self.resize(820, 800)
        self.setMinimumSize(400, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(
            section_title(
                "查找原教案",
                "先用知识线索查找，再打开完整原文与原图。摘要不直接带入备课，不调用模型。",
            )
        )
        self.search = QLineEdit()
        self.search.setAccessibleName("搜索原教案名称、知识与解题方法")
        self.search.setPlaceholderText("输入知识点或原教案名称；多个词用空格分隔")
        self.search.setText(lesson_topic)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setAccessibleName("已导入原教案搜索结果")
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setMinimumHeight(100)
        layout.addWidget(self.results, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("原教案知识线索与区块位置")
        layout.addWidget(self.preview, 2)
        self.status = QLabel("正在读取已导入的原教案目录…")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        layout.addWidget(self.status)
        self.open_button = QPushButton("打开这份教案的完整原文与图片…")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        layout.addWidget(self.open_button)
        self.close_button = QPushButton("取消")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.search.textChanged.connect(self._filter)
        self.results.currentItemChanged.connect(self._selected)
        self.results.itemDoubleClicked.connect(lambda *_: self._open())
        try:
            self._job = tasks.submit(
                "读取原教案知识索引",
                lambda: lecture_catalog(facade),
                on_success=self._ready,
                on_failure=self._failed,
            )
        except RuntimeError:
            self._failed()

    def _ready(self, value):
        if self._closed or not isValid(self):
            return
        self._job = None
        self._items = value["items"]
        self._warnings = value.get("warnings", [])
        self._filter()

    def _failed(self, *_):
        if self._closed or not isValid(self):
            return
        self._job = None
        set_status(
            self.status,
            "error",
            "原教案目录暂不可读取，请从导入历史核对文件后重新打开。已有备课内容未改动。",
        )

    def _filter(self, *_):
        self.results.clear()
        self.preview.clear()
        self.open_button.setEnabled(False)
        rows = search_lectures(self._items, self.search.text())
        for row in rows:
            label = row["source_name"] + (
                " · 有知识线索" if row["indexed"] else " · 完整原文"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setToolTip(row["source_name"])
            self.results.addItem(item)
        warning = " ".join(getattr(self, "_warnings", []))
        note = f"找到 {len(rows)} 份 / 已导入 {len(self._items)} 份。按文字匹配，可修改或清空关键词查看全部。"
        if not rows and self._items:
            note += " 没有命中不表示没有原文；可用更短的知识点名称查找。"
        set_status(
            self.status, "attention" if warning else "info", note + " " + warning
        )
        if rows:
            self.results.setCurrentRow(0)

    def _selected(self, item, *_):
        self.open_button.setEnabled(item is not None)
        self.preview.setPlainText(
            item.data(Qt.ItemDataRole.UserRole)["preview"] if item else ""
        )

    def _open(self):
        item = self.results.currentItem()
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        dialog = ImportWordDialog(
            self.facade, row["batch_id"], self, initial_source_id=row["source_id"]
        )
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.reference is not None:
            self.reference = dialog.reference
            self.accept()

    def done(self, result):
        self._closed = True
        if self._job:
            self.tasks.cancel(self._job)
            self._job = None
        super().done(result)
