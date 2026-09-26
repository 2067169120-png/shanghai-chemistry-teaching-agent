"""Teacher desk: recent work and the real basket, not a marketing landing page."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QBoxLayout, QHeaderView,
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from ..desktop_teacher_desk import desk_snapshot
from .components import CardFrame, CollapsibleSection, page_scroll, section_title, set_status


def label(text="", role="MutedLabel"):
    value = QLabel(text)
    value.setObjectName(role)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


def button(text, slot, primary=False):
    value = QPushButton(text)
    value.setObjectName("PrimaryAction" if primary else "QuietButton")
    value.setAccessibleName(text)
    value.clicked.connect(slot)
    return value


class HomePage(QWidget):
    navigate_requested = Signal(str)
    open_requested = Signal(object)
    new_requested = Signal()
    basket_requested = Signal()
    preview_requested = Signal()

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._loading = False
        self._epoch = 0
        self._task_id = None
        self._registry_loading = False
        self._registry_loaded = False
        self.records = []
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 22, 24, 28)
        root.setSpacing(16)
        self.heading_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.heading_layout.addWidget(section_title("最近备课"), 1)
        self.action_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.refresh_button = button("刷新", self.refresh)
        self.new_button = button("新建备课", self.new_requested.emit, True)
        self.action_row.addWidget(self.refresh_button)
        self.action_row.addWidget(self.new_button)
        self.heading_layout.addLayout(self.action_row)
        root.addLayout(self.heading_layout)
        self.editing_strip = CardFrame()
        self.editing_strip.setObjectName("DeskEditing")
        edit = QHBoxLayout(self.editing_strip)
        edit.setContentsMargins(12, 8, 12, 8)
        self.editing_label = label("", "DeskText")
        edit.addWidget(self.editing_label, 1)
        self.resume_button = button("继续编辑", lambda: self.navigate_requested.emit("preparation"))
        edit.addWidget(self.resume_button)
        self.editing_strip.hide()
        root.addWidget(self.editing_strip)
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(16)
        works = CardFrame()
        works.setObjectName("DeskPanel")
        lay = QVBoxLayout(works)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(10)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("DeskWorkTable")
        self.table.setAccessibleName("最近五份当前备课")
        self.table.setHorizontalHeaderLabels(["作品名称", "状态", "保存 / 更新"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(48)
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(190)
        self.table.setMaximumHeight(295)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda *_: self._open())
        lay.addWidget(self.table)
        self.empty = label("暂无备课。新建一份，或从“我的备课”查找归档作品。")
        self.empty.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(self.empty)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 0, 12, 0)
        self.recent = label("正在读取最近作品…")
        bottom.addWidget(self.recent, 1)
        self.all_button = button("全部备课", lambda: self.navigate_requested.emit("mywork"))
        self.open_button = button("打开选中", self._open)
        self.open_button.setEnabled(False)
        bottom.addWidget(self.all_button)
        bottom.addWidget(self.open_button)
        lay.addLayout(bottom)
        self.body.addWidget(works, 3)
        self.basket_panel = CardFrame()
        self.basket_panel.setObjectName("DeskPanel")
        basket = QVBoxLayout(self.basket_panel)
        basket.setContentsMargins(16, 14, 16, 16)
        basket.setSpacing(10)
        basket.addWidget(label("选题篮", "CardTitle"))
        self.basket_count = label("正在读取…", "DeskText")
        basket.addWidget(self.basket_count)
        self.basket_excerpt = label()
        basket.addWidget(self.basket_excerpt, 1)
        self.basket_button = button("查看 / 调整顺序", self.basket_requested.emit)
        self.preview_button = button("预览试卷排版", self.preview_requested.emit)
        self.select_button = button("继续选题", lambda: self.navigate_requested.emit("library"))
        self.basket_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        for value in (self.basket_button, self.preview_button, self.select_button):
            basket.addWidget(value)
        self.body.addWidget(self.basket_panel, 1)
        root.addLayout(self.body)
        self.status = label()
        self.status.hide()
        root.addWidget(self.status)
        self.diagnostics = CollapsibleSection("题库与教材状态")
        self.diagnostics.toggle.setAccessibleDescription("按需读取题库目录状态，不影响最近备课")
        self.diagnostics.toggle.toggled.connect(self._diagnostics_toggled)
        self.registry_text = label("展开后读取目录状态。")
        self.diagnostics.content_layout.addWidget(self.registry_text)
        self.progress_button = button("检查本地题库进度", self.open_library_progress)
        self.registry_refresh = button("重新读取目录", lambda: self._load_registry(True))
        row = QHBoxLayout()
        row.addWidget(self.progress_button)
        row.addWidget(self.registry_refresh)
        self.diagnostics.content_layout.addLayout(row)
        root.addWidget(self.diagnostics)
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        QTimer.singleShot(80, self.refresh)

    def update_editor(self, payload):
        """Called on the UI thread; show the current form, not an old saved draft."""
        present = any(payload.get(key) for key in ("topic", "materials", "objective", "image_assets"))
        present = present or any(payload.get("advanced", {}).values())
        title = str(payload.get("topic", "")).strip() or "未命名备课"
        self.editing_label.setText("正在编辑：" + (title[:85] + "…" if len(title) > 85 else title))
        self.editing_label.setToolTip(title)
        self.editing_strip.setVisible(bool(present))

    def refresh(self, *, force_refresh=False):
        self._epoch += 1
        epoch = self._epoch
        if self._task_id:
            self.tasks.cancel(self._task_id)
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self._task_id = self.tasks.submit("读取最近备课", lambda: desk_snapshot(self.facade),
            on_success=lambda result: self._loaded(result, epoch),
            on_failure=lambda message: self._failed(message, epoch))
        if force_refresh and self.diagnostics.toggle.isChecked():
            self._load_registry(True)

    def _loaded(self, result, epoch):
        if epoch != self._epoch:
            return
        self._loading = False
        self._task_id = None
        self.refresh_button.setEnabled(True)
        old_row = self.table.currentRow()
        selected = ((self.records[old_row]["kind"], self.records[old_row]["id"])
                    if 0 <= old_row < len(self.records) else None)
        self.records = result["works"]
        self.table.setRowCount(len(self.records))
        for index, record in enumerate(self.records):
            values = (record["title"], record["status"], record["date"].replace("T", " ")[5:16])
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(record["title"] + "\n原课题：" + record.get("original_title", record["title"]) + "\n" + record["date"])
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        for index, record in enumerate(self.records):
            if (record["kind"], record["id"]) == selected:
                self.table.selectRow(index)
        self.empty.setVisible(not self.records)
        warnings = result["warnings"]
        self.empty.setText("暂无可显示的记录，请检查下方读取提示。" if warnings else
                           "暂无当前备课。新建一份，或从“全部备课”查找归档作品。")
        self.recent.setText(f"最近 {len(self.records)} 份 · 仅当前作品" if result["total"] is not None else "最近记录暂不可用")
        rows = result["basket"]
        count = len(rows) if rows is not None else None
        self.basket_count.setText(f"已选 {count} 项" if count is not None else "题篮暂不可读")
        self.basket_excerpt.setText("\n\n".join(f"{i + 1}. {str(row.get('title_zh', '未命名题目'))[:90]}" for i, row in enumerate((rows or [])[:2])))
        if count == 0:
            self.basket_excerpt.setText("还没有选题。到题库筛选后加入选题篮。")
        self.basket_button.setEnabled(rows is not None)
        self.preview_button.setEnabled(bool(rows))
        self.status.setVisible(bool(warnings))
        set_status(self.status, "attention", "\n".join(warnings))
        self._selection()

    def _failed(self, message, epoch):
        if epoch != self._epoch:
            return
        self._loading = False
        self._task_id = None
        self.refresh_button.setEnabled(True)
        self.status.show()
        set_status(self.status, "error", message)

    def _selection(self):
        self.open_button.setEnabled(not self._loading and bool(self.table.selectedItems()))

    def _open(self):
        row = self.table.currentRow()
        if not self._loading and self.table.selectedItems() and 0 <= row < len(self.records):
            self.open_requested.emit(self.records[row])

    def _diagnostics_toggled(self, expanded):
        if expanded and not self._registry_loaded:
            self._load_registry()

    def _load_registry(self, force=False):
        if self._registry_loading:
            return
        self._registry_loading = True
        self.registry_refresh.setEnabled(False)
        self.registry_text.setText("正在读取题库与教材目录…")
        self.tasks.submit("读取资料状态", lambda: self.facade.load_desktop_registry(force_refresh=force),
                          on_success=self._registry_ready, on_failure=self._registry_failed)

    def _registry_ready(self, registry):
        self._registry_loading = False
        self._registry_loaded = True
        self.registry_refresh.setEnabled(True)
        lines = []
        names = {"master": "核心题库", "wave1": "细分题库", "supplemental": "补充题库"}
        for product in registry.products:
            name = names.get(product.product_id, "原题库")
            lines.append(f"{name}：{product.papers or 0}套 · {product.themes or 0}主题 · {product.atomic_parts or 0}作答单元"
                         if product.loaded else name + "：" + product.message_zh)
        c = registry.curriculum
        lines.append(f"教材目录：{c.volumes or 0}册 · {c.chapters or 0}章 · {c.sections or 0}节" if c.loaded else "教材目录：" + c.message_zh)
        lines.append("未连接原库不影响个人备课；个人导入及标签缺项可在“题库进度”中检查。")
        self.registry_text.setText("\n".join(lines))

    def _registry_failed(self, message):
        self._registry_loading = False
        self.registry_refresh.setEnabled(True)
        self.registry_text.setText(message)

    def open_library_progress(self):
        from .library_progress_dialog import LibraryProgressDialog
        dialog = LibraryProgressDialog(self.facade, self.tasks, self)
        dialog.exec()
        dialog.deleteLater()

    def resizeEvent(self, event):
        compact = event.size().width() < 760
        direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        self.body.setDirection(direction)
        self.heading_layout.setDirection(direction if event.size().width() < 450 else QBoxLayout.Direction.LeftToRight)
        self.action_row.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width() < 450 else QBoxLayout.Direction.LeftToRight)
        self.table.setColumnHidden(2, event.size().width() < 640)
        self.table.setColumnHidden(1, event.size().width() < 450)
        super().resizeEvent(event)
