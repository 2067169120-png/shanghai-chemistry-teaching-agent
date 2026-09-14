"""Paged search across all existing preparation drafts and generation tasks."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QTabBar, QInputDialog, QMessageBox, QBoxLayout
from .components import page_scroll, section_title, set_status
from .studio_templates import text_label

SHELF_LABELS = {"current": "当前作品", "archived": "已归档", "trash": "回收站"}


class MyWorkPage(QWidget):
    backup_requested = Signal()
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
        self._mutating = False
        self._mutation_task = None
        self._notice = ""
        self._selected_id = None
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)
        root.addWidget(section_title("我的备课", "按作品名称或原课题查找、整理；归档和移入回收站均保留原文件。"))
        self.shelves = QTabBar()
        self.shelves.setAccessibleName("作品范围")
        self.shelves.setExpanding(False)
        self.shelves.setDrawBase(False)
        for key, label in SHELF_LABELS.items():
            index = self.shelves.addTab(label)
            self.shelves.setTabData(index, key)
        root.addWidget(self.shelves)
        self.backup_button = QPushButton("备份与恢复")
        self.backup_button.setObjectName("QuietButton")
        self.backup_button.clicked.connect(self.backup_requested.emit)
        root.addWidget(self.backup_button)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索作品名称或原课题，不限最近50条")
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
        self.empty_label = text_label("当前没有作品。先去备课页保存草稿，或切换范围查找。")
        self.empty_label.hide()
        root.addWidget(self.empty_label)
        self.management_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.rename_button = QPushButton("重命名")
        self.archive_button = QPushButton("归档")
        self.trash_button = QPushButton("移入回收站")
        self.restore_button = QPushButton("还原")
        self.management_buttons = (self.rename_button, self.archive_button, self.trash_button, self.restore_button)
        for button in self.management_buttons:
            button.setObjectName("QuietButton")
            button.setEnabled(False)
            self.management_row.addWidget(button)
        self.restore_button.hide()
        self.rename_button.clicked.connect(lambda: self._organize("rename"))
        self.archive_button.clicked.connect(lambda: self._organize("unarchive" if self.shelf == "archived" else "archive"))
        self.trash_button.clicked.connect(lambda: self._organize("trash"))
        self.restore_button.clicked.connect(lambda: self._organize("restore"))
        root.addLayout(self.management_row)
        self.selection_hint = text_label("选中一份作品即可整理。名称修改不改变原课题或已导出文件。")
        root.addWidget(self.selection_hint)
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
        self.shelves.currentChanged.connect(self._filter)
        self.query.textChanged.connect(self._filter)
        self.kind.currentIndexChanged.connect(self._filter)
        self.order.currentIndexChanged.connect(self._filter)
        self.results.currentItemChanged.connect(self._selection_changed)
        self.results.itemDoubleClicked.connect(lambda *_: self._open())

    @property
    def shelf(self):
        return self.shelves.tabData(self.shelves.currentIndex())

    def _filter(self, *_):
        if self._mutating:
            return
        self._notice = ""
        self._selected_id = None
        self._offset = 0
        self._epoch += 1  # invalidate a reply as soon as its query becomes obsolete
        self.results.clear()
        self.records = []
        self.open_button.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._loading = True
        self._selection_changed()
        set_status(self.status, "info", "正在搜索全部历史作品…")
        self._timer.start()

    def refresh(self):
        if self._mutating:
            return
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
        self._selection_changed()
        set_status(self.status, "info", "正在搜索全部历史作品…")
        request = dict(query=self.query.text(), kind=self.kind.currentData(), order=self.order.currentData(),
                       offset=self._offset, limit=self.PAGE_SIZE, shelf=self.shelf)
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
            original = record.get("original_title", record["title"])
            suffix = ("\n原课题：" + original[:110]) if original != record["title"] else ""
            item = QListWidgetItem(f"{record['title']}\n{record['status']}  ·  {record['date'].replace('T', ' ')[:16]}{suffix}")
            item.setToolTip(record["title"] + "\n原课题：" + original)
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.results.addItem(item)
        self.open_button.setEnabled(False)
        self.previous.setEnabled(self._offset > 0)
        self.next.setEnabled(result["has_more"])
        self.page_label.setText(f"第 {self._offset // self.PAGE_SIZE + 1} / {max(1, (self._total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)} 页 · 每页{self.PAGE_SIZE}条")
        warnings = result["warnings"]
        for index, (key, label) in enumerate(SHELF_LABELS.items()):
            count = result.get("shelf_counts", {}).get(key)
            self.shelves.setTabText(index, label + (f"  {count}" if count is not None else ""))
        self.empty_label.setVisible(not self.records)
        self.empty_label.setText("来源未完整读取，请先检查上方提示后刷新。" if warnings else {
            "current": "没有匹配的当前作品。可修改关键词、切换到归档或回收站，或返回备课保存新稿。",
            "archived": "没有匹配的归档作品。当前作品归档后会在这里保留。",
            "trash": "回收站没有匹配的作品。这里仅作可恢复整理，不清理磁盘文件。",
        }[self.shelf])
        set_status(self.status, "attention" if warnings else "success" if self._notice else "info",
                   self._notice + f"{SHELF_LABELS[self.shelf]}匹配 {self._total} 条，本页 {len(self.records)} 条。" +
                   ("；".join(warnings) if warnings else "页签数量随关键词和类型筛选；先检索全部记录再分页。"))
        if self._selected_id is not None:
            for index, row in enumerate(self.records):
                if (row["kind"], row["id"]) == self._selected_id:
                    self.results.setCurrentRow(index)
                    break
        self._selection_changed()

    def _failed(self, message, epoch=None):
        if epoch is not None and epoch != self._epoch:
            return
        self._loading = False
        self._task_id = None
        self.refresh_button.setEnabled(True)
        self.results.clear()
        self._selection_changed()
        set_status(self.status, "error", message)

    def _page(self, delta):
        if self._loading or self._mutating:
            return
        self._notice = ""
        self._selected_id = None
        self._offset = max(0, self._offset + delta * self.PAGE_SIZE)
        self.refresh()

    def _open(self):
        item = self.results.currentItem()
        if item and not self._loading and not self._mutating and self.shelf != "trash":
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _selection_changed(self, *_):
        item = self.results.currentItem()
        record = item.data(Qt.ItemDataRole.UserRole) if item else None
        ready = bool(record) and not self._loading and not self._mutating
        self.open_button.setEnabled(ready and self.shelf != "trash")
        manageable = ready and record.get("manageable", False)
        self.rename_button.setVisible(self.shelf != "trash")
        self.archive_button.setVisible(self.shelf != "trash")
        self.trash_button.setVisible(self.shelf != "trash")
        self.restore_button.setVisible(self.shelf == "trash")
        for button in self.management_buttons:
            button.setEnabled(manageable)
        self.archive_button.setText("移回当前" if self.shelf == "archived" else "归档")
        target = record.get("restore_shelf", "current") if record else "current"
        self.restore_button.setText("还原到" + SHELF_LABELS.get(target, "当前作品"))
        if ready and not record.get("manageable", False):
            hint = "任务尚未结束，暂不能整理；查看状态不会取消或重试生成。"
        elif self.shelf == "trash":
            hint = "原草稿、图片和导出文件仍保留。先还原后打开；本版不提供永久删除。"
        else:
            hint = "重命名只改作品名称，不改原课题。归档不删文件，仍可打开或移回当前。"
        self.selection_hint.setText(hint)

    def _ask_title(self, record):
        title, accepted = QInputDialog.getText(self, "重命名作品", "作品名称（1—160字；不改原课题与文件名）：",
                                               QLineEdit.EchoMode.Normal, record["title"])
        return title if accepted else None

    def _confirm_trash(self, record):
        return QMessageBox.question(self, "移入回收站", "将“" + record["title"] +
            "”移入回收站？\n原内容与文件不会删除，可从回收站还原。", QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _organize(self, action):
        item = self.results.currentItem()
        if not item or self._loading or self._mutating:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if not record.get("manageable", False):
            return
        title = None
        if action == "rename":
            title = self._ask_title(record)
            if title is None:
                return
            from ..desktop_work_organization import validate_title, WorkOrganizationError
            try:
                title = validate_title(title)
            except WorkOrganizationError as exc:
                set_status(self.status, "attention", str(exc))
                return
            if title == record["title"]:
                return
        elif action == "trash" and not self._confirm_trash(record):
            return
        self._mutating = True
        self._epoch += 1
        self._timer.stop()
        self._selected_id = (record["kind"], record["id"])
        for widget in (self.query, self.kind, self.order, self.shelves, self.refresh_button,
                       self.previous, self.next, self.results):
            widget.setEnabled(False)
        self._selection_changed()
        set_status(self.status, "info", "正在保存作品整理结果…")
        request = dict(expected_source=record["source_revision"],
                       expected_organization=record["organization_revision"], title=title)
        self._mutation_task = self.tasks.submit("整理备课作品",
            lambda: self.facade.organize_preparation_work(record["kind"], record["id"], action, **request),
            on_success=self._organized, on_failure=self._organize_failed)

    def _release_controls(self):
        self._mutating = False
        self._mutation_task = None
        for widget in (self.query, self.kind, self.order, self.shelves, self.refresh_button, self.results):
            widget.setEnabled(True)

    def _organized(self, result):
        self._release_controls()
        self._notice = ("已重命名；原课题与文件未改。" if result["action"] == "rename" else
                        "已移至" + SHELF_LABELS[result["shelf"]] + "；原文件保留。")
        self.refresh()

    def _organize_failed(self, message):
        self._release_controls()
        # Keep the failure visible; do not silently retry against a new identity/version.
        self.results.clear()
        self.records = []
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._selection_changed()
        set_status(self.status, "attention", message + " 请点击刷新后重新选择。")

    def resizeEvent(self, event):
        self.management_row.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 470
                                         else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)
