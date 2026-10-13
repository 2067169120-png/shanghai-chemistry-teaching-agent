"""Reviewable local cache plan, category selection and per-item receipts."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .components import section_title, set_status


class CacheMaintenanceDialog(QDialog):
    cache_cleared = Signal(object)

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._active = None
        self._plan = None
        self.last_result = None
        self.category_checks = {}
        self.setWindowTitle("本机缓存维护")
        self.resize(860, 620)
        self.setMinimumSize(520, 420)
        root = QVBoxLayout(self)
        root.addWidget(section_title("本机缓存维护", "先查看可重建缓存，再选择类别清理。后续打开资料会从原文件重新生成。"))
        self.note = QLabel("原Word/PDF、教师标签与题目范围、学生资料、考试、费用账本与导出保留。历史观察、未知文件与无法核验的版本保留。")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.note)
        self.category_layout = QVBoxLayout()
        root.addLayout(self.category_layout)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("类别 / 路径", "字节", "索引记录", "状态", "说明"))
        self.table.setAccessibleName("缓存识别与逐项清理结果")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 330)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        root.addWidget(self.table, 1)
        self.status = QLabel("点击“查看缓存”核验本机记录；后台任务进行时请等待完成。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.inspect_button = QPushButton("查看缓存")
        self.inspect_button.setAccessibleName("查看可重建缓存类别、文件数与字节")
        self.inspect_button.clicked.connect(self._inspect)
        self.clear_button = QPushButton("清理所选缓存")
        self.clear_button.setAccessibleName("按当前核验计划清理所选缓存类别")
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self._clear)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        for button in (self.inspect_button, self.clear_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

    def _busy(self):
        workers = getattr(self.tasks, "_workers", {})
        return any(identity != self._active for identity in workers)

    def _controls(self, active):
        self.inspect_button.setEnabled(not active)
        self.close_button.setEnabled(not active)
        self.clear_button.setEnabled(not active and self._plan is not None
            and any(check.isChecked() and check.isEnabled() for check in self.category_checks.values()))
        for check in self.category_checks.values():
            check.setEnabled(not active and bool(check.property("has_entries")))

    def _inspect(self):
        if self._active is not None:
            return
        if self._busy():
            set_status(self.status, "attention", "后台任务正在运行；请等任务完成后再查看缓存。不会停止任务。")
            return
        self._controls(True)
        self._active = self.tasks.submit("核验可重建缓存", self.facade.plan_desktop_cache_cleanup,
            on_success=self._planned, on_failure=self._failed)

    def _planned(self, plan):
        self._active = None
        self._plan = plan
        while self.category_layout.count():
            widget = self.category_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.category_checks.clear()
        for category in plan["categories"]:
            label = f"{category['label']} · {category['files']} 个文件 · {category['bytes']:,} 字节"
            if category["rows"]:
                label += f" · {category['rows']} 条可重建索引记录"
            check = QCheckBox(label)
            check.setAccessibleName(label)
            check.setProperty("has_entries", bool(category["files"]))
            check.setChecked(bool(category["files"]))
            check.toggled.connect(lambda _checked: self._controls(False) if self._active is None else None)
            self.category_checks[category["key"]] = check
            self.category_layout.addWidget(check)
        rows = [[entry["path"], str(entry["bytes"]), str(len(entry["rows"])), "可清理",
            "保留数据库与历史观察" if entry["category"] == "word_catalog" else "已核验可重建协议"] for entry in plan["entries"]]
        rows.extend([entry["path"], "—", "—", "保留", entry["reason"]] for entry in plan["preserved"])
        self._fill(rows)
        set_status(self.status, "info", "已核验当前计划。索引字节为记录大小；SQLite文件可能不会缩小。选择类别后可清理。")
        self._controls(False)

    def _clear(self):
        if self._active is not None or self._plan is None:
            return
        if self._busy():
            set_status(self.status, "attention", "后台任务正在运行，清理尚未执行；请等任务完成后重新查看。")
            return
        categories = [key for key, check in self.category_checks.items() if check.isChecked() and check.isEnabled()]
        if not categories:
            return
        plan = self._plan
        self._controls(True)
        self._active = self.tasks.submit("清理已核验缓存", lambda: self.facade.clear_desktop_caches(
            plan["plan_id"], plan["revision"], categories), on_success=self._cleared, on_failure=self._failed)

    def _cleared(self, result):
        self._active = None
        self.last_result = result
        self._plan = None
        rows = [[item["path"], str(item["bytes"]), str(item["rows"]),
            {"cleared": "已清理", "preserved": "保留", "busy": "正在使用"}.get(item["status"], item["status"]), item["message"]] for item in result["items"]]
        rows.extend([entry["path"], "—", "—", "保留", entry["reason"]] for entry in result["preserved"])
        self._fill(rows)
        set_status(self.status, "attention" if result["status"] != "complete" else "success",
            f"清理 {result['removed_files']} 个文件（{result['removed_bytes']:,} 字节）及 {result['removed_rows']} 条索引记录。"
            "各项结果见列表。Word目录已标为需重新读取；继续检索或刷新时将从原资料重建。")
        self._controls(False)
        self.cache_cleared.emit(result)

    def _failed(self, message):
        self._active = None
        self._plan = None
        set_status(self.status, "attention", message + "；请重新查看缓存。")
        self._controls(False)

    def _fill(self, rows):
        self.table.setRowCount(len(rows))
        for row_number, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row_number, column, item)

    def reject(self):
        if self._active is None:
            super().reject()

    def closeEvent(self, event):
        if self._active is not None:
            event.ignore()
        else:
            super().closeEvent(event)
