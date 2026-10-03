"""Teacher desk: recent work and the real basket, not a marketing landing page."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QBoxLayout, QHeaderView,
    QLabel, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)
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


def _display_date(value, *, short=False):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return parsed.strftime("%m-%d %H:%M" if short else "%Y-%m-%d %H:%M")
    except ValueError:
        return value.replace("T", " ")


class HomePage(QWidget):
    navigate_requested = Signal(str)
    open_requested = Signal(object)
    new_requested = Signal()
    basket_requested = Signal()
    preview_requested = Signal()
    import_requested = Signal()

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
        self.content_layout = root = QVBoxLayout(content)
        root.setContentsMargins(24, 22, 24, 28)
        root.setSpacing(18)
        self.heading_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.heading_layout.setSpacing(16)
        self.heading_layout.addWidget(section_title("最近备课", "从最近作品继续，或新建一份备课。"), 1)
        self.action_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.refresh_button = button("刷新", self.refresh)
        self.refresh_button.setToolTip("重新读取最近作品和选题篮")
        self.new_button = button("新建备课", self.new_requested.emit, True)
        self.action_row.addWidget(self.refresh_button)
        self.action_row.addWidget(self.new_button)
        self.heading_layout.addLayout(self.action_row)
        root.addLayout(self.heading_layout)
        self.quick_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.quick_actions.addWidget(button("导入本地资料", self.import_requested.emit))
        self.quick_actions.addWidget(button("继续作业批改", lambda: self.navigate_requested.emit("grading")))
        self.quick_actions.addWidget(button("教材研读", lambda: self.navigate_requested.emit("textbooks")))
        self.quick_actions.addWidget(button("查看处理任务", lambda: self.navigate_requested.emit("tasks")))
        root.addLayout(self.quick_actions)
        self.editing_strip = CardFrame()
        self.editing_strip.setObjectName("DeskEditing")
        self.editing_layout = edit = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.editing_strip)
        edit.setContentsMargins(16, 12, 16, 12)
        edit.setSpacing(12)
        self.editing_label = label("", "DeskText")
        edit.addWidget(self.editing_label, 1)
        self.resume_button = button("继续编辑", lambda: self.navigate_requested.emit("preparation"))
        edit.addWidget(self.resume_button)
        self.editing_strip.hide()
        root.addWidget(self.editing_strip)
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(16)
        self.works_panel = works = CardFrame()
        works.setObjectName("DeskPanel")
        lay = QVBoxLayout(works)
        lay.setContentsMargins(0, 0, 0, 16)
        lay.setSpacing(0)
        self.work_heading = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.work_heading.setContentsMargins(18, 16, 18, 14)
        self.work_heading.setSpacing(6)
        self.work_heading.addWidget(label("当前作品", "CardTitle"), 1)
        self.recent = label("正在读取…", "DeskMeta")
        self.work_heading.addWidget(self.recent)
        lay.addLayout(self.work_heading)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("DeskWorkTable")
        self.table.setAccessibleName("最近五份当前备课")
        self.table.setHorizontalHeaderLabels(["作品名称", "状态", "保存 / 更新"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setAccessibleDescription("选择作品查看完整信息，按 Enter 或双击打开")
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(52)
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(190)
        self.table.setMaximumHeight(295)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda *_: self._open())
        self._open_shortcuts = []
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self._open)
            self._open_shortcuts.append(shortcut)
        lay.addWidget(self.table)
        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(24, 26, 24, 28)
        empty_layout.setSpacing(12)
        self.empty_title = label("正在读取最近作品…", "DeskEmptyTitle")
        self.empty = label("保存过的当前作品会显示在这里。")
        self.empty_start_button = button("新建一份备课", self.new_requested.emit)
        for value in (self.empty_title, self.empty):
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(value)
        empty_layout.addWidget(self.empty_start_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_start_button.hide()
        self.table.hide()
        lay.addWidget(self.empty_state)
        self.work_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.work_actions.setContentsMargins(18, 14, 18, 0)
        self.work_actions.setSpacing(12)
        self.selection_detail = label("选择作品后可打开；双击或按 Enter 也可继续。", "DeskSelection")
        self.selection_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.work_actions.addWidget(self.selection_detail, 1)
        self.work_buttons = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.work_buttons.setSpacing(8)
        self.all_button = button("全部备课", lambda: self.navigate_requested.emit("mywork"))
        self.open_button = button("打开选中", self._open, True)
        self.open_button.setEnabled(False)
        self.work_buttons.addWidget(self.all_button)
        self.work_buttons.addWidget(self.open_button)
        self.work_actions.addLayout(self.work_buttons)
        lay.addLayout(self.work_actions)
        self.body.addWidget(works, 3)
        self.basket_panel = CardFrame()
        self.basket_panel.setObjectName("DeskBasket")
        basket = QVBoxLayout(self.basket_panel)
        basket.setContentsMargins(18, 16, 18, 18)
        basket.setSpacing(12)
        basket.addWidget(label("选题篮", "CardTitle"))
        self.basket_count = label("正在读取…", "DeskCount")
        basket.addWidget(self.basket_count)
        self.basket_excerpt = label("正在读取已选题目…", "DeskText")
        self.basket_excerpt.setAlignment(Qt.AlignmentFlag.AlignTop)
        basket.addWidget(self.basket_excerpt, 1)
        self.basket_remainder = label("", "DeskMeta")
        self.basket_remainder.hide()
        basket.addWidget(self.basket_remainder)
        self.basket_button = button("查看 / 调整顺序", self.basket_requested.emit)
        self.preview_button = button("预览试卷排版", self.preview_requested.emit, True)
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
        self.registry_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.registry_actions.addWidget(self.progress_button)
        self.registry_actions.addWidget(self.registry_refresh)
        self.registry_actions.addStretch(1)
        self.diagnostics.content_layout.addLayout(self.registry_actions)
        root.addWidget(self.diagnostics)
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        self.refresh_button.setText("读取中…")
        self.open_button.setEnabled(False)
        self.recent.setText("正在更新…" if self.records else "正在读取…")
        if not self.records:
            self.empty_title.setText("正在读取最近作品…")
            self.empty.setText("保存过的当前作品会显示在这里。")
            self.empty_start_button.hide()
        self.basket_count.setText("正在读取…")
        self.basket_excerpt.setText("正在读取已选题目…")
        self.basket_excerpt.setToolTip("")
        self.basket_remainder.hide()
        self.basket_button.setEnabled(False)
        self.preview_button.setEnabled(False)
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
        self.refresh_button.setText("刷新")
        old_row = self.table.currentRow()
        selected = ((self.records[old_row]["kind"], self.records[old_row]["id"])
                    if 0 <= old_row < len(self.records) else None)
        with QSignalBlocker(self.table):
            self.records = result["works"]
            self.table.setRowCount(len(self.records))
            for index, record in enumerate(self.records):
                values = (record["title"], record["status"], _display_date(record["date"], short=True))
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    item.setToolTip(record["title"] + "\n原课题：" + record.get("original_title", record["title"]) + "\n" + record["date"])
                    self.table.setItem(index, column, item)
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            for index, record in enumerate(self.records):
                if (record["kind"], record["id"]) == selected:
                    self.table.selectRow(index)
        self.table.setVisible(bool(self.records))
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() +
                                  self.table.verticalHeader().defaultSectionSize() * len(self.records) + 2)
        self.empty_state.setVisible(not self.records)
        self.empty_title.setText("还没有当前作品" if result["total"] is not None else "最近作品暂不可读")
        self.empty.setText("先写下课题和目标，再逐步补充教学材料。" if result["total"] is not None else
                           "请刷新重试，或到“全部备课”检查已保存的作品。")
        self.empty_start_button.setVisible(result["total"] is not None)
        warnings = result["warnings"]
        self.recent.setText(f"最近 {len(self.records)} 份 · 仅当前作品" if result["total"] is not None else "最近记录暂不可用")
        rows = result["basket"]
        count = len(rows) if rows is not None else None
        self.basket_count.setText(f"已选 {count} 项" if count is not None else "题篮暂不可读")
        titles = [str(row.get("title_zh", "未命名题目")) for row in (rows or [])[:2]]
        self.basket_excerpt.setText("\n\n".join(
            f"{i + 1:02d}  {title[:90]}{'…' if len(title) > 90 else ''}" for i, title in enumerate(titles)))
        self.basket_excerpt.setToolTip("\n\n".join(titles))
        if count == 0:
            self.basket_excerpt.setText("还没有选题。到题库筛选后加入选题篮。")
        elif count is None:
            self.basket_excerpt.setText("请刷新重试。已选记录未被清空。")
        self.basket_remainder.setText(f"另有 {count - 2} 项 · 打开选题篮查看全部" if count and count > 2 else "")
        self.basket_remainder.setVisible(bool(count and count > 2))
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
        self.refresh_button.setText("刷新")
        self.recent.setText("刷新失败 · 当前显示上次读取内容" if self.records else "最近记录暂不可用")
        if not self.records:
            self.empty_title.setText("最近作品暂不可读")
            self.empty.setText("请刷新重试，或到“全部备课”检查已保存的作品。")
            self.empty_start_button.hide()
        self.basket_count.setText("题篮暂不可读")
        self.basket_excerpt.setText("请刷新重试。已选记录未被清空。")
        self.status.show()
        set_status(self.status, "error", message)

    def _selection(self):
        row = self.table.currentRow()
        selected = bool(self.table.selectedItems()) and 0 <= row < len(self.records)
        self.open_button.setEnabled(not self._loading and selected)
        self.open_button.setVisible(bool(self.records))
        if selected:
            record = self.records[row]
            self.selection_detail.setText(record["title"] + "\n" + record["status"] +
                " · 保存 / 更新：" + _display_date(record["date"]))
            self.selection_detail.setToolTip("记录原时间：" + record["date"])
        else:
            self.selection_detail.setText("选择作品后可打开；双击或按 Enter 也可继续。" if self.records else
                                          "已归档的作品可在“全部备课”中查看。")
            self.selection_detail.setToolTip("")

    def _open(self):
        row = self.table.currentRow()
        if self.open_button.isEnabled() and not self._loading and self.table.selectedItems() and 0 <= row < len(self.records):
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
        width = event.size().width()
        compact = width < 760
        direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        self.body.setDirection(direction)
        narrow = QBoxLayout.Direction.TopToBottom if width < 450 else QBoxLayout.Direction.LeftToRight
        self.heading_layout.setDirection(narrow)
        self.action_row.setDirection(narrow)
        self.quick_actions.setDirection(QBoxLayout.Direction.TopToBottom if width < 720 else QBoxLayout.Direction.LeftToRight)
        self.editing_layout.setDirection(narrow)
        self.work_heading.setDirection(narrow)
        self.recent.setWordWrap(width < 450)
        self.work_actions.setDirection(QBoxLayout.Direction.TopToBottom if width < 1000 else QBoxLayout.Direction.LeftToRight)
        self.work_buttons.setDirection(QBoxLayout.Direction.TopToBottom if width < 360 else QBoxLayout.Direction.LeftToRight)
        self.registry_actions.setDirection(QBoxLayout.Direction.TopToBottom if width < 640 else QBoxLayout.Direction.LeftToRight)
        margin = 14 if width < 450 else 24
        self.content_layout.setContentsMargins(margin, 22, margin, 28)
        self.table.setColumnHidden(2, width < 640)
        self.table.setColumnHidden(1, width < 450)
        super().resizeEvent(event)
