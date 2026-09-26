"""Source-bound pending work; edits stay in the existing source editors."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QComboBox, QDialog, QFileDialog, QFrame,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy, QTableWidget,
    QTableWidgetItem, QTabBar, QVBoxLayout, QWidget,
)

from ..desktop_library_progress import TODO_LABELS, VISUAL_TODO_LABELS, collect_library_progress
from .components import page_scroll, set_status


def _label(text="", role="MutedLabel"):
    label = QLabel(text)
    label.setObjectName(role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _combo(name):
    combo = QComboBox()
    combo.setAccessibleName(name)
    combo.setMinimumWidth(0)
    combo.setMinimumContentsLength(6)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return combo


class LibraryProgressDialog(QDialog):
    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.report = None
        self.records = []
        self._rows = []
        self._closed = False
        self._loading = False
        self._word_current = False
        self._visual_current = False
        self._bank = "word"
        self._bank_states = {}
        self._bank_rows = {"word": [], "visual": []}
        self._return_anchor = None
        self._opening = False
        self._open_error = ""
        self._epoch = 0
        self._task_id = None
        self._selected_key = None
        self._selected_position = 0
        self._compact = False
        self.setWindowTitle("本地题库进度 · 待整理")
        self.resize(1080, 840)
        self.setMinimumSize(320, 400)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        self.body_scroll = page_scroll(content)
        outer.addWidget(self.body_scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)
        root.addWidget(_label("资料整理 / 本地目录", "ExplorerEyebrow"))
        root.addWidget(_label("本地题库 · 待整理", "PageTitle"))
        root.addWidget(_label("选择资料库，筛选缺项，再打开原题核对。关闭原题后自动刷新并保留位置。"))

        self.metrics = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        for prefix, heading in (("word", "Word 候选 · 待处理"), ("visual", "个人图片题 · 印刷题")):
            card = QFrame()
            card.setObjectName("DeskPanel")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(3)
            layout.addWidget(_label(heading, "MetricTitle"))
            value = _label("读取中…", "MetricValue")
            note = _label("正在读取已保存的本地目录", "DeskMeta")
            layout.addWidget(value)
            layout.addWidget(note)
            setattr(self, prefix + "_count", value)
            setattr(self, prefix + "_summary", note)
            self.metrics.addWidget(card, 1)
        root.addLayout(self.metrics)
        self.summary = _label("标签齐备只表示字段有效，不代表化学审核；两库可能重复，不能相加。", "DeskMeta")
        root.addWidget(self.summary)

        panel = QFrame()
        panel.setObjectName("DeskPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(10)
        self.bank_tabs = QTabBar()
        self.bank_tabs.addTab("Word 文档")
        self.bank_tabs.addTab("图片 / 视觉")
        self.bank_tabs.setAccessibleName("选择待整理题库：Word 文档或个人图片视觉题库")
        self.bank_tabs.setExpanding(True)
        self.bank_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        panel_layout.addWidget(self.bank_tabs)
        self.list_title = _label("Word 待处理清单", "CardTitle")
        panel_layout.addWidget(self.list_title)
        self.scope_note = _label("Word 按提取候选列出；字段有效不代表化学内容已审核。", "DeskMeta")
        panel_layout.addWidget(self.scope_note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索来源、题目或题面摘要")
        self.search.setAccessibleName("搜索待处理 Word 来源、题目和摘要")
        self.search.setClearButtonEnabled(True)
        panel_layout.addWidget(self.search)
        self.filters = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.source_filter = _combo("按来源筛选待处理题目")
        self.source_filter.addItem("全部来源", None)
        self.todo_filter = _combo("按待处理事项筛选")
        self.todo_filter.addItem("全部待办", None)
        for key, label in TODO_LABELS.items():
            self.todo_filter.addItem(label, key)
        self.protection_filter = _combo("按人工保护状态筛选")
        self.protection_filter.addItem("全部保护状态", None)
        self.protection_filter.addItem("教师修改 / 固定标签", True)
        self.protection_filter.addItem("未标记为人工保护", False)
        for combo in (self.source_filter, self.todo_filter, self.protection_filter):
            self.filters.addWidget(combo, 1)
            combo.currentIndexChanged.connect(self._filter_rows)
        panel_layout.addLayout(self.filters)
        self.theme_filter = _combo("按视觉题真实主题父级筛选")
        self.theme_filter.addItem("全部主题", None)
        self.theme_filter.currentIndexChanged.connect(self._filter_rows)
        panel_layout.addWidget(self.theme_filter)
        self.theme_filter.hide()
        self.result_count = _label("正在读取待办…", "DeskMeta")
        panel_layout.addWidget(self.result_count)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("DeskWorkTable")
        self.table.setAccessibleName("Word 待处理列表：来源、题目、待办；双击或 Enter 打开原题")
        self.table.setHorizontalHeaderLabels(["来源", "题目", "待办"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        self.table.setMinimumWidth(0)
        self.table.setFixedHeight(302)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(82)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(55)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self.open_selected)
        self.table.installEventFilter(self)
        panel_layout.addWidget(self.table)
        self.table.hide()
        self.empty_state = _label("正在读取已保存的题目…", "DeskEmptyTitle")
        panel_layout.addWidget(self.empty_state)
        self.selection_detail = _label("", "DeskSelection")
        self.selection_detail.setAccessibleName("所选待办题目的完整来源与缺项")
        panel_layout.addWidget(self.selection_detail)
        self.open_button = QPushButton("打开原题与教学标签")
        self.open_button.setObjectName("PrimaryAction")
        self.open_button.setAccessibleName("打开所选 Word 原题并核对或修改教学标签")
        self.open_button.clicked.connect(self.open_selected)
        self.open_button.setEnabled(False)
        panel_layout.addWidget(self.open_button)
        self.editor_note = _label("原考试类型没有依据时保留待确认。标签修改仍需在原题编辑器中明确保存。", "DeskMeta")
        panel_layout.addWidget(self.editor_note)
        root.addWidget(panel)

        self.status = _label("正在读取本地目录…", "StatusInfo")
        self.status.setAccessibleName("题库进度读取和操作状态")
        root.addWidget(self.status)
        self.warnings = _label("", "StatusAttention")
        self.warnings.hide()
        root.addWidget(self.warnings)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.refresh_button = QPushButton("刷新目录")
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel_button = QPushButton("停止读取")
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.save = QPushButton("导出进度 JSON")
        self.save.clicked.connect(self.export_report)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        for button in (self.refresh_button, self.cancel_button, self.save, self.close_button):
            button.setObjectName("QuietButton")
            button.setAutoDefault(False)
            self.actions.addWidget(button)
        self.open_button.setAutoDefault(False)
        root.addLayout(self.actions)
        root.addStretch(1)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._filter_rows)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.bank_tabs.currentChanged.connect(self._switch_bank)
        self.refresh()

    def _current_available(self):
        return self._visual_current if self._bank == "visual" else self._word_current

    @staticmethod
    def _identity(row):
        return (row["batch_id"], row["key"]) if row.get("batch_id") else row["key"]

    def _remember_bank(self):
        self._bank_states[self._bank] = {
            "search": self.search.text(), "source": self.source_filter.currentData(),
            "source_label": self.source_filter.currentText(), "todo": self.todo_filter.currentData(),
            "protected": self.protection_filter.currentData(), "theme": self.theme_filter.currentData(),
            "theme_label": self.theme_filter.currentText(), "key": self._selected_key,
            "position": self._selected_position, "scroll": self.table.verticalScrollBar().value(),
        }

    def _switch_bank(self, index):
        if self._closed:
            return
        self._search_timer.stop()
        self._remember_bank()
        self._bank = "visual" if index else "word"
        state = self._bank_states.get(self._bank, {})
        self._selected_key, self._selected_position = state.get("key"), state.get("position", 0)
        with QSignalBlocker(self.search):
            self.search.setText(state.get("search", ""))
        with QSignalBlocker(self.protection_filter):
            self.protection_filter.setCurrentIndex(max(0, self.protection_filter.findData(state.get("protected"))))
        self._configure_bank(state)
        self._filter_rows()
        self.table.verticalScrollBar().setValue(state.get("scroll", 0))

    def _configure_bank(self, state=None):
        visual = self._bank == "visual"
        if state is None:
            state = {"source": self.source_filter.currentData(), "todo": self.todo_filter.currentData(),
                     "source_label": self.source_filter.currentText(), "theme": self.theme_filter.currentData(),
                     "theme_label": self.theme_filter.currentText()}
        self._rows = self._bank_rows[self._bank]
        self.list_title.setText("个人图片 / 视觉题库 · 待整理" if visual else "Word 待处理清单")
        self.scope_note.setText("按原主题与主题内顺序列出印刷小题；暂不能选用与标签缺项分别统计。" if visual else
                                "Word 按提取候选列出；字段有效不代表化学内容已审核。")
        self.search.setPlaceholderText("搜索来源、主题、印刷题或题面摘要" if visual else "搜索来源、题目或题面摘要")
        self.search.setAccessibleName("搜索个人视觉题来源、主题与印刷小题" if visual else "搜索待处理 Word 来源、题目和摘要")
        self.theme_filter.setVisible(visual)
        self.table.setHorizontalHeaderLabels(["来源 / 主题" if visual else "来源", "印刷小题" if visual else "题目", "待办"])
        self.table.setAccessibleName("个人视觉待整理列表：来源、主题、印刷小题、待办；双击或 Enter 打开原题" if visual else
                                     "Word 待处理列表：来源、题目、待办；双击或 Enter 打开原题")
        self.open_button.setText("打开原题 / 所属主题" if visual else "打开原题与教学标签")
        self.open_button.setAccessibleName("打开所选视觉原题与所属主题，核对标签和原图" if visual else
                                          "打开所选 Word 原题并核对或修改教学标签")
        self.editor_note.setText("原题窗口可核对题面、共同材料和答案，并编辑个人标签与现有裁片；修改须明确保存。" if visual else
                                 "原考试类型没有依据时保留待确认。标签修改仍需在原题编辑器中明确保存。")
        section = (self.report or {}).get(self._bank)
        all_rows = section.get("rows", []) if section else self._rows
        self._populate_sources(all_rows, state)
        labels = VISUAL_TODO_LABELS if visual else TODO_LABELS
        with QSignalBlocker(self.todo_filter):
            self.todo_filter.clear()
            self.todo_filter.addItem("全部待办", None)
            for key, label in labels.items():
                count = sum(key in row.get("todo_keys", []) for row in self._rows)
                self.todo_filter.addItem(f"{label}（{count:,}）", key)
            self.todo_filter.setCurrentIndex(max(0, self.todo_filter.findData(state.get("todo"))))
        choices = {row["theme_identity"]: row for row in all_rows if row.get("theme_identity")}
        with QSignalBlocker(self.theme_filter):
            self.theme_filter.clear()
            self.theme_filter.addItem("全部主题", None)
            for identity, row in choices.items():
                order = f"主题 {row['theme_sequence']} · " if row.get("theme_sequence") else "顺序待核对 · "
                self.theme_filter.addItem(order + (row.get("theme_title") or "主题标题待核对") + " · " + row["source"], identity)
            selected = state.get("theme")
            if selected is not None and self.theme_filter.findData(selected) < 0:
                self.theme_filter.addItem(state.get("theme_label") or "原主题已不在当前目录", selected)
            self.theme_filter.setCurrentIndex(max(0, self.theme_filter.findData(selected)))

    def done(self, result):
        self._closed = True
        self._epoch += 1
        self._search_timer.stop()
        if self._task_id:
            self.tasks.cancel(self._task_id)
            self._task_id = None
        self._update_actions()
        super().done(result)

    def refresh(self):
        if self._closed:
            return
        self._selection_changed()
        self._epoch += 1
        epoch = self._epoch
        if self._task_id:
            self.tasks.cancel(self._task_id)
        self._loading = True
        self._word_current = False
        self._visual_current = False
        self._update_actions()
        set_status(self.status, "info", "正在重新读取本地目录与当前标签…")
        facade = self.facade
        try:
            task_id = self.tasks.submit(
                "检查题库进度", lambda: collect_library_progress(facade),
                on_success=lambda report: self.apply_report(report, epoch),
                on_failure=lambda message: self.show_failure(message, epoch),
            )
            # Synchronous bridges may already have delivered their callback.
            self._task_id = task_id if self._loading and self._epoch == epoch else None
        except Exception:
            self.show_failure("本地目录任务未能启动，请刷新重试。", epoch)

    def cancel_refresh(self):
        if not self._loading or self._closed:
            return
        self._epoch += 1
        if self._task_id:
            self.tasks.cancel(self._task_id)
        self._task_id = None
        self._loading = False
        self._word_current = False
        self._visual_current = False
        self.word_count.setText("尚未更新")
        self.visual_count.setText("尚未更新")
        self.result_count.setText("已停止读取 · 当前清单尚未更新")
        self.empty_state.setText("已停止读取，请刷新后继续")
        self.save.setText("导出上次快照 JSON")
        set_status(self.status, "attention", "已停止读取；未修改题目或标签。刷新后可继续处理。")
        self._update_actions()

    def show_failure(self, message, epoch=None):
        if self._closed or (epoch is not None and epoch != self._epoch):
            return
        self._loading = False
        self._task_id = None
        self._word_current = False
        self._visual_current = False
        self.result_count.setText("读取失败 · 当前清单尚未更新")
        self.empty_state.setText("目录暂不可读，请刷新重试")
        self.word_count.setText("暂不可读")
        self.visual_count.setText("暂不可读")
        set_status(self.status, "error", message)
        self.save.setText("导出上次快照 JSON")
        self._update_actions()

    def apply_report(self, report, epoch=None):
        if self._closed or (epoch is not None and epoch != self._epoch):
            return
        self._loading = False
        self._task_id = None
        self.report = report
        self.save.setText("导出进度 JSON")
        word, visual = report.get("word"), report.get("visual")
        self._word_current = word is not None
        self._visual_current = visual is not None
        if word is not None:
            counts = word["counts"]
            self._bank_rows["word"] = [row for row in word["rows"] if row["todo"]]
            self.word_count.setText(f"{len(self._bank_rows['word']):,} 条")
            self.word_summary.setText(
                f"{word['source_count']} 份来源 · {counts['candidates']:,} 条候选\n"
                f"四项齐备 {counts['complete_candidates']:,} · 旧标签 {counts['stale_labels']} · 图文缺口 {counts['material_gaps']}")
        else:
            self.word_count.setText("暂不可读")
            self.word_summary.setText("读取失败未记作 0；刷新后继续处理。")
        if visual is not None:
            self._bank_rows["visual"] = [row for row in visual.get("rows", []) if row["todo"]]
            self.visual_count.setText(f"{visual['printed_questions']:,} 道")
            self.visual_summary.setText(f"{visual['themes']} 个主题 · {len(self._bank_rows['visual']):,} 道待整理\n"
                                        f"{visual['not_selectable']} 道暂不能选用 · 与 Word 分开统计")
            if visual.get("unavailable_batches"):
                self.visual_count.setText(f"已读 {visual['printed_questions']:,} 道")
                self.visual_summary.setText(self.visual_summary.text() +
                    f"\n另有 {visual['unavailable_batches']} 批暂不可读，当前数量不是全库总量。")
        else:
            self.visual_count.setText("暂不可读")
            self.visual_summary.setText("个人图片题读取失败未记作 0。")
        self.summary.setText(report["note"])
        warnings = list(report.get("errors", []))
        for section in (word, visual):
            if section:
                warnings.extend(section.get("warnings", []))
        self.warnings.setText("\n".join(warnings[:3]) + (
            f"\n另有 {len(warnings) - 3} 条提醒，可导出完整进度查看。" if len(warnings) > 3 else ""))
        self.warnings.setVisible(bool(warnings))
        set_status(self.status, "attention" if warnings else "info",
                   "已读取当前目录。选中待办后可直接打开原题。" if self._current_available() else
                   "当前题库目录暂不可读；上次清单不可操作，请刷新重试。")
        if self._open_error:
            set_status(self.status, "error", self._open_error)
            self._open_error = ""
        self._configure_bank()
        self._filter_rows()
        if self._return_anchor and self._return_anchor["bank"] == self._bank:
            anchor = self._return_anchor
            self._return_anchor = None
            if self._selected_key == anchor["key"]:
                self.table.verticalScrollBar().setValue(anchor["table_scroll"])
            current_epoch = self._epoch
            def restore_scroll():
                if not self._closed and current_epoch == self._epoch:
                    self.body_scroll.verticalScrollBar().setValue(anchor["body_scroll"])
            QTimer.singleShot(0, restore_scroll)
        self._update_actions()

    def _populate_sources(self, rows, state=None):
        selected = state.get("source") if state else self.source_filter.currentData()
        label = state.get("source_label") if state else self.source_filter.currentText()
        choices = {row.get("source_id", ""): row.get("source") or "来源名称待核对" for row in rows}
        with QSignalBlocker(self.source_filter):
            self.source_filter.clear()
            self.source_filter.addItem("全部来源", None)
            for source, name in sorted(choices.items(), key=lambda value: (value[1], value[0])):
                self.source_filter.addItem(name, source)
            if selected is not None and self.source_filter.findData(selected) < 0:
                self.source_filter.addItem(label, selected)
            self.source_filter.setCurrentIndex(max(0, self.source_filter.findData(selected)))

    def _filter_rows(self, *_args):
        if self._closed:
            return
        query = self.search.text().strip().casefold()
        source, todo, protected = (combo.currentData() for combo in
                                  (self.source_filter, self.todo_filter, self.protection_filter))
        theme = self.theme_filter.currentData() if self._bank == "visual" else None
        selected, position = self._selected_key, self._selected_position
        self.records = [row for row in self._rows
                        if (source is None or row.get("source_id", "") == source)
                        and (todo is None or todo in row.get("todo_keys", []))
                        and (protected is None or row.get("protected", False) == protected)
                        and (theme is None or row.get("theme_identity") == theme)
                        and (not query or query in " ".join(str(row.get(field, "")) for field in
                            ("source", "question_title", "excerpt", "chapter", "theme_title", "question_number")).casefold())]
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self.records))
            for index, row in enumerate(self.records):
                source_name = row.get("source") or "来源名称待核对"
                title = row.get("question_title") or "题目标题待核对"
                question = title + "\n" + (source_name if self._compact else row.get("excerpt", "")[:68])
                if self._bank == "visual":
                    theme_title = row.get("theme_title") or "主题标题待核对"
                    order = f"主题 {row['theme_sequence']}" if row.get("theme_sequence") else "主题顺序待核对"
                    source_name += "\n" + order + " · " + theme_title
                    title = "第 " + (row.get("question_number") or "待核对") + " 题"
                    question = title + "\n" + (order + " · " + theme_title if self._compact else row.get("excerpt", "")[:68])
                for column, value in enumerate((source_name, question, " · ".join(row["todo"]))):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, self._identity(row))
                    item.setToolTip(source_name + "\n" + title + "\n" + "、".join(row["todo"]))
                    self.table.setItem(index, column, item)
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            if self.records:
                index = next((i for i, row in enumerate(self.records) if self._identity(row) == selected),
                             min(position, len(self.records) - 1))
                self.table.selectRow(index)
                self.table.scrollToItem(self.table.item(index, 1))
        self.table.setVisible(bool(self.records))
        self.empty_state.setVisible(not self.records)
        if self._current_available():
            self.result_count.setText(f"显示 {len(self.records):,} / {len(self._rows):,} 条待办 · 双击或 Enter 打开")
            self.empty_state.setText("当前筛选没有匹配的待办" if self._rows else
                                     "当前没有标签缺项或图文缺口待办" if self._bank == "word" else
                                     "当前没有已识别的视觉待整理项；这不代表题目或化学内容已经审核")
            if self._bank == "visual" and (self.report or {}).get("visual", {}).get("unavailable_batches"):
                self.result_count.setText(self.result_count.text() + " · 部分批次未读到")
                if not self._rows:
                    self.empty_state.setText("部分图片批次暂不可读，无法确认全库待整理数量；请核对来源并刷新。")
        else:
            self.result_count.setText(("个人视觉" if self._bank == "visual" else "Word") + "目录暂不可读 · 未显示为 0 条待办")
            self.empty_state.setText("目录暂不可读，请刷新重试")
        self._selection_changed()

    def _selection_changed(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.records) and self.table.selectedItems():
            row = self.records[index]
            self._selected_key, self._selected_position = self._identity(row), index
            self.selection_detail.setText(
                (row.get("source") or "来源名称待核对") + "\n" +
                (row.get("question_title") or "题目标题待核对") +
                (" · " + row["chapter"] if row.get("chapter") else "") + "\n" +
                "待处理：" + "、".join(row["todo"]) + "\n" +
                ("含原题图或公共材料图" if row.get("has_visual") else "未检测到题面或公共材料图片") +
                (" · 教师修改 / 固定标签，需人工核对" if row.get("protected") else ""))
            if self._bank == "visual":
                order = f"主题 {row['theme_sequence']}" if row.get("theme_sequence") else "主题顺序待核对"
                self.selection_detail.setText(
                    (row.get("source") or "来源名称待核对") + "\n" + order + " · " +
                    (row.get("theme_title") or "主题标题待核对") + " → 印刷第 " +
                    (row.get("question_number") or "待核对") + " 题\n待整理：" + "、".join(row["todo"]) +
                    ("\n教师修改 / 固定标签，需人工核对" if row.get("protected") else ""))
            self.selection_detail.setToolTip(row.get("excerpt", ""))
        else:
            self.selection_detail.setText("原题与标签入口会在选中待办后启用。" if self.records else
                                          "字段齐备不等于题目或化学内容已审核。")
        self._update_actions()

    def _update_actions(self):
        index = self.table.currentRow()
        selected = bool(self.table.selectedItems()) and 0 <= index < len(self.records)
        self.open_button.setEnabled(bool(selected and not self._closed and self._current_available() and not self._loading
                                         and not self._opening and self.records[index].get("revision")))
        self.table.setEnabled(not self._closed and self._current_available() and not self._loading and not self._opening)
        self.bank_tabs.setEnabled(not self._closed and not self._opening)
        self.refresh_button.setEnabled(not self._closed and not self._loading and not self._opening)
        self.refresh_button.setText("读取中…" if self._loading else "刷新目录")
        self.cancel_button.setVisible(self._loading)
        self.save.setEnabled(not self._closed and self.report is not None and not self._loading and not self._opening)

    def open_selected(self, *_args):
        if self._closed or not self.open_button.isEnabled():
            return
        from .word_question_dialog import WordQuestionDialog
        row = self.records[self.table.currentRow()]
        self._return_anchor = {"bank": self._bank, "key": self._identity(row),
                               "table_scroll": self.table.verticalScrollBar().value(),
                               "body_scroll": self.body_scroll.verticalScrollBar().value()}
        self._opening = True
        self._update_actions()
        dialog = None
        try:
            if self._bank == "visual":
                from .personal_visual_question_dialog import PersonalVisualQuestionDialog
                dialog = PersonalVisualQuestionDialog(self.facade, self.tasks, self,
                    batch_id=row["batch_id"], initial_question_key=row["key"], initial_revision=row["revision"])
            else:
                dialog = WordQuestionDialog(self.facade, self.tasks, self,
                    initial_source_id=row.get("source_id") or None, initial_question_key=row["key"])
            dialog.exec()
        except Exception:
            self._open_error = "原题窗口未能打开；已重新读取目录，可重试。"
        finally:
            if dialog is not None:
                if not dialog._closed:
                    dialog.reject()
                dialog.deleteLater()
            self._opening = False
            if not self._closed:
                # A cancelled or failed editor must reread, never assume a save.
                self.refresh()

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_selected()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        compact = event.size().width() < 690
        direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        for layout in (self.metrics, self.filters, self.actions):
            layout.setDirection(direction)
        self.table.setColumnHidden(0, compact)
        self.table.verticalHeader().setDefaultSectionSize(100 if compact else 82)
        self.table.setFixedHeight(264 if compact else 302)
        if compact != self._compact:
            self._compact = compact
            self._filter_rows()
        super().resizeEvent(event)

    def export_report(self):
        if self.report is None or not self.save.isEnabled():
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存本机进度", "题库进度.json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            set_status(self.status, "error", "进度文件未保存，请检查目录权限后重试。")
        else:
            set_status(self.status, "info", "已保存本机进度；报告含来源名称与题面摘要，请不要直接上传公开仓库。")
