"""Offline question browsing for saved Word imports."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, section_title, set_status
from .tasks import DesktopTaskBridge


def _label(text: str, *, muted: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if muted:
        widget.setObjectName("MutedLabel")
    return widget


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class WordQuestionRangeDialog(QDialog):
    """Choose an explicit range while the complete extracted source stays visible."""

    def __init__(self, item: dict, source: dict, parent=None):
        super().__init__(parent)
        self.ranges: dict | None = None
        self.setWindowTitle("调整本题范围")
        self.resize(800, 780)
        self.setMinimumSize(400, 550)
        outer = QVBoxLayout(self)
        content = QWidget()
        root = QVBoxLayout(content)
        root.addWidget(
            _label(
                "根据完整来源核对题面、答案与共同材料的区块范围。此修改会使旧选题预览失效。"
            )
        )
        root.addWidget(_label(_text(source.get("source_name"))))
        blocks = source.get("blocks", [])
        maximum = max(
            (
                block["index"]
                for block in blocks
                if isinstance(block, dict) and type(block.get("index")) is int
            ),
            default=0,
        )
        self.fields: dict[str, QSpinBox] = {}
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        for key, title in (
            ("block_start", "题面开始"),
            ("question_end", "题面结束"),
            ("answer_start", "答案开始（0 表示无答案）"),
            ("block_end", "本题结束"),
            ("context_start", "共同材料开始（0 表示不选）"),
            ("context_end", "共同材料结束（0 表示不选）"),
        ):
            field = QSpinBox()
            field.setRange(
                0 if key in {"answer_start", "context_start", "context_end"} else 1,
                max(1, maximum),
            )
            field.setValue(
                item.get(key)
                or (0 if key in {"answer_start", "context_start", "context_end"} else 1)
            )
            field.setAccessibleName(title)
            self.fields[key] = field
            form.addRow(title, field)
        root.addLayout(form)
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setAccessibleName("完整 Word 来源区块，用于核对题目范围")
        self.original.setMinimumHeight(230)
        self.original.setPlainText(
            "\n\n".join(
                f"[区块 {block.get('index')}]\n{_text(block.get('text'))}"
                + (
                    "\n待核对：" + "；".join(block.get("warnings", []))
                    if block.get("warnings")
                    else ""
                )
                for block in blocks
                if isinstance(block, dict)
            )
        )
        root.addWidget(self.original)
        self.locate_button = QPushButton("定位题面开始区块")
        self.locate_button.setObjectName("QuietButton")
        self.locate_button.clicked.connect(self._locate)
        root.addWidget(self.locate_button)
        outer.addWidget(page_scroll(content), 1)
        self.status = _label("")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("保存本题范围")
        self.save_button.setEnabled(maximum > 0)
        self.save_button.clicked.connect(self._confirm)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)

    def _locate(self):
        cursor = self.original.document().find(
            f"[区块 {self.fields['block_start'].value()}]"
        )
        if not cursor.isNull():
            cursor.setPosition(cursor.selectionStart())
            self.original.setTextCursor(cursor)
            self.original.verticalScrollBar().setValue(cursor.block().firstLineNumber())

    def _confirm(self):
        ranges = {key: field.value() for key, field in self.fields.items()}
        start, question_end, answer_start, end = (
            ranges[key]
            for key in ("block_start", "question_end", "answer_start", "block_end")
        )
        context_start, context_end = ranges["context_start"], ranges["context_end"]
        if (
            not (1 <= start <= question_end <= end)
            or (answer_start and not question_end < answer_start <= end)
            or (not answer_start and question_end != end)
        ):
            set_status(
                self.status,
                "error",
                "请按先题面、后答案的顺序选择连续范围；无答案时本题结束须等于题面结束。",
            )
            return
        if (bool(context_start) != bool(context_end)) or (
            context_start and context_start > context_end
        ):
            set_status(
                self.status, "error", "共同材料须同时填写有效的开始与结束，或都设为 0。"
            )
            return
        for key in ("answer_start", "context_start", "context_end"):
            ranges[key] = ranges[key] or None
        self.ranges = ranges
        self.accept()


class WordQuestionDialog(QDialog):
    """Browse source questions, persist choices, and confirm an exact reference."""

    def __init__(
        self,
        facade: Any,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
        *,
        initial_source_id: str | None = None,
        batch_id: str | None = None,
    ):
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self.reference: dict | None = None
        self.preparation_reference: dict | None = None
        self._initial_source_id = initial_source_id
        self._initial_batch_id = batch_id
        self._closed = False
        self._jobs: dict[int, str | None] = {}
        self._job_serial = 0
        self._catalog_epoch = 0
        self._detail_epoch = 0
        self._reference_epoch = 0
        self._catalog_busy = False
        self._reference_busy = False
        self._export_busy = False
        self._selection_save_busy = False
        self._selection_to_save: list[dict] | None = None
        self._loaded_once = False
        self._catalog_revision = ""
        self._items: dict[str, dict] = {}
        self._selected: dict[str, str] = {}
        self._points: dict[str, int] = {}
        self._range_busy = False
        self._closing_result: QDialog.DialogCode | None = None
        self._rendering_list = False
        self._current_key: str | None = None
        self._preview_reference: dict | None = None
        self._preview_selection: tuple = ()
        self._image_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._image_targets: list[tuple[QLabel, QPixmap]] = []
        self._image_task_ids: set[str] = set()
        self._loaded_tabs: set[int] = set()
        self._export_paths: dict[str, str] = {}
        self.setWindowTitle("Word 逐题浏览与选题")
        self.resize(1200, 820)
        self.setMinimumSize(400, 600)
        content = QWidget()
        root = QVBoxLayout(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body_scroll = page_scroll(content)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.body_scroll)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(
            section_title(
                "Word 逐题浏览与选题",
                "按题核对已导入的原文与图片；答案单独查看。勾选可跨来源保留，所有读取均在本机完成。",
            )
        )
        self.catalog_note = _label("", muted=True)
        self.catalog_note.setAccessibleName("Word 来源目录读取提醒")
        self.catalog_note.hide()
        root.addWidget(self.catalog_note)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(0)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("按 Word 来源筛选题目")
        self.source_combo.setMinimumContentsLength(10)
        self.source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.source_combo.addItem("全部来源", None)
        left_layout.addWidget(self.source_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题面、题号或章节")
        self.search.setAccessibleName("搜索 Word 题面、题号或章节")
        left_layout.addWidget(self.search)
        self.result_count = _label("正在读取题目…", muted=True)
        left_layout.addWidget(self.result_count)
        self.question_list = QListWidget()
        self.question_list.setAccessibleName("Word 逐题列表，可跨来源勾选")
        self.question_list.setMinimumSize(0, 115)
        self.question_list.setWordWrap(True)
        self.question_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        left_layout.addWidget(self.question_list, 1)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("上一题")
        self.next_button = QPushButton("下一题")
        self.reload_button = QPushButton("刷新")
        for button in (self.previous_button, self.next_button, self.reload_button):
            button.setObjectName("QuietButton")
            navigation.addWidget(button)
        left_layout.addLayout(navigation)
        self.splitter.addWidget(left)
        right = QWidget()
        right.setMinimumWidth(0)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_title = _label("请选择一道题")
        self.detail_title.setObjectName("CardTitle")
        self.detail_note = _label("", muted=True)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_note)
        points_row = QHBoxLayout()
        points_row.addWidget(_label("本次练习分值"))
        self.points = QSpinBox()
        self.points.setRange(1, 100)
        self.points.setValue(2)
        self.points.setAccessibleName("当前题本次练习分值，不是原卷分值")
        points_row.addWidget(self.points)
        points_row.addWidget(_label("非原卷分值", muted=True))
        points_row.addStretch(1)
        right_layout.addLayout(points_row)
        self.range_button = QPushButton("调整本题范围")
        self.range_button.setObjectName("QuietButton")
        self.range_button.setVisible(
            callable(getattr(facade, "word_question_source", None))
            and callable(getattr(facade, "word_question_update_range", None))
        )
        self.range_button.clicked.connect(self._adjust_range)
        right_layout.addWidget(self.range_button)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 130)
        self._panels: list[QWidget] = []
        self._panel_layouts: list[QVBoxLayout] = []
        for title in ("题面", "答案与解析"):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)
            self._panels.append(panel)
            self._panel_layouts.append(layout)
            self.tabs.addTab(page_scroll(panel), title)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("勾选题目将带入备课的完整参考")
        self.preview.setPlaceholderText("勾选题目后点击“预览选题”。")
        self.tabs.addTab(self.preview, "选题预览")
        right_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([380, 770])
        root.addWidget(self.splitter, 1)
        self.selection_count = _label("已选 0 题；筛选或切换来源会保留勾选。")
        root.addWidget(self.selection_count)
        self.action_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.preview_button = QPushButton("预览选题")
        self.import_button = QPushButton("确认带入备课")
        self.clear_button = QPushButton("清空勾选")
        self.clear_button.setObjectName("QuietButton")
        for button in (self.preview_button, self.import_button, self.clear_button):
            self.action_layout.addWidget(button)
        root.addLayout(self.action_layout)
        self.export_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.export_title = QLineEdit("Word 选题练习")
        self.export_title.setAccessibleName("导出练习名称")
        self.export_title.setPlaceholderText("练习名称")
        self.export_button = QPushButton("导出练习")
        self.export_button.setObjectName("QuietButton")
        self.export_layout.addWidget(self.export_title, 1)
        self.export_layout.addWidget(self.export_button)
        root.addLayout(self.export_layout)
        self.show_student_scores = QCheckBox("题面显示分数")
        self.show_student_scores.setChecked(False)
        self.show_student_scores.setToolTip("默认不在学生卷额外添加分数；教师答案始终保留各题分值。")
        self.show_student_scores.toggled.connect(self._invalidate_preview)
        root.addWidget(self.show_student_scores)
        self.export_result_layout = QHBoxLayout()
        self.student_button = QPushButton("打开学生版")
        self.teacher_button = QPushButton("打开教师版")
        for button in (self.student_button, self.teacher_button):
            button.setObjectName("QuietButton")
            button.hide()
            self.export_result_layout.addWidget(button)
        root.addLayout(self.export_result_layout)
        self.export_note = _label("", muted=True)
        self.export_note.setAccessibleName("练习导出提醒")
        self.export_note.hide()
        root.addWidget(self.export_note)
        self.status = _label("正在读取已保存的 Word 题目…")
        self.status.setAccessibleName("Word 选题状态")
        root.addWidget(self.status)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        root.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.source_combo.currentIndexChanged.connect(self._filter_items)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._filter_items)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.question_list.itemChanged.connect(self._checked)
        self.question_list.currentItemChanged.connect(self._current_changed)
        self.points.valueChanged.connect(self._points_changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        self.reload_button.clicked.connect(self._load_catalog)
        self.clear_button.clicked.connect(self._clear_selection)
        self.preview_button.clicked.connect(self._preview_selected)
        self.import_button.clicked.connect(self._confirm)
        self.export_button.clicked.connect(self._export)
        self.student_button.clicked.connect(lambda: self._open_export("student_path"))
        self.teacher_button.clicked.connect(lambda: self._open_export("teacher_path"))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(180)
        self._save_timer.timeout.connect(self._persist_selection)
        self.tasks.task_finished.connect(self._task_finished)
        self._load_catalog()

    @property
    def selections(self) -> list[dict[str, Any]]:
        return [
            {"key": key, "revision": revision, "points": self._points.get(key, 2)}
            for key, revision in self._selected.items()
        ]

    def _selection_key(self) -> tuple:
        return tuple(
            (key, revision, self._points.get(key, 2))
            for key, revision in self._selected.items()
        )

    def _submit(self, label, operation, success, failure=None) -> str | None:
        self._job_serial += 1
        token = self._job_serial
        self._jobs[token] = None

        def completed(value, failed=False):
            self._jobs.pop(token, None)
            if self._closed:
                return
            if failed:
                if failure is not None:
                    failure(value)
                else:
                    set_status(self.status, "error", value)
            else:
                success(value)

        try:
            task_id = self.tasks.submit(
                label,
                operation,
                on_success=completed,
                on_failure=lambda message: completed(message, True),
            )
            if token in self._jobs:
                self._jobs[token] = task_id
            return task_id
        except (RuntimeError, TypeError):
            completed("本地任务暂时无法启动，请重试。", True)
            return None

    def _task_finished(self, task_id: str) -> None:
        self._image_task_ids.discard(task_id)
        for token, pending in tuple(self._jobs.items()):
            if pending == task_id:
                self._jobs.pop(token, None)

    def _load_catalog(self) -> None:
        if self._catalog_busy or self._closed:
            return
        self._catalog_epoch += 1
        epoch = self._catalog_epoch
        restore = not self._loaded_once
        self._catalog_busy = True
        self._invalidate_preview()
        self._update_actions()
        set_status(self.status, "info", "正在本机读取逐题目录…")
        facade = self.facade

        def read():
            catalog = facade.word_question_catalog()
            saved = facade.word_question_saved_selection() if restore else None
            return catalog, saved

        self._submit(
            "读取 Word 逐题目录",
            read,
            lambda result: self._catalog_ready(epoch, result),
            lambda message: self._catalog_failed(epoch, message),
        )

    def _catalog_failed(self, epoch: int, message: str) -> None:
        if epoch != self._catalog_epoch:
            return
        self._catalog_busy = False
        set_status(self.status, "error", message)
        self._update_actions()

    def _catalog_ready(self, epoch: int, result: Any) -> None:
        if epoch != self._catalog_epoch:
            return
        try:
            catalog, saved = result
            if (
                not isinstance(catalog, dict)
                or not isinstance(catalog.get("items"), list)
                or not isinstance(catalog.get("sources"), list)
            ):
                raise TypeError("invalid catalog")
            items = {}
            for item in catalog["items"]:
                if (
                    not isinstance(item, dict)
                    or not _text(item.get("key"))
                    or not _text(item.get("revision"))
                    or item["key"] in items
                ):
                    raise ValueError("invalid question")
                items[item["key"]] = deepcopy(item)
            if not self._loaded_once:
                if not isinstance(saved, list):
                    raise ValueError("invalid saved selection")
                self._selected = {
                    item["key"]: item["revision"]
                    for item in saved
                    if isinstance(item, dict)
                    and isinstance(item.get("key"), str)
                    and isinstance(item.get("revision"), str)
                }
                self._points.update(
                    {
                        item["key"]: item.get("points", 2)
                        for item in saved
                        if isinstance(item, dict)
                        and item.get("key") in self._selected
                        and type(item.get("points", 2)) is int
                        and 1 <= item.get("points", 2) <= 100
                    }
                )
            before = len(self._selected)
            self._selected = {
                key: revision
                for key, revision in self._selected.items()
                if key in items
                and items[key]["revision"] == revision
                and items[key].get("selection_ready") is True
            }
            removed = before - len(self._selected)
            self._items = items
            self._catalog_revision = _text(catalog.get("revision"))
            selected_source = self.source_combo.currentData()
            if not self._loaded_once:
                selected_source = self._initial_source_id or next(
                    (
                        item.get("source_id")
                        for item in items.values()
                        if self._initial_batch_id
                        and item.get("batch_id") == self._initial_batch_id
                    ),
                    selected_source,
                )
            self.source_combo.blockSignals(True)
            self.source_combo.clear()
            self.source_combo.addItem("全部来源", None)
            for source in catalog["sources"]:
                if isinstance(source, dict) and _text(source.get("source_id")):
                    name = (
                        _text(source.get("source_name"))
                        .replace("\\", "/")
                        .rsplit("/", 1)[-1]
                    )
                    self.source_combo.addItem(name or "Word 来源", source["source_id"])
            self.source_combo.setCurrentIndex(
                max(0, self.source_combo.findData(selected_source))
            )
            self.source_combo.blockSignals(False)
            self._loaded_once = True
            self._catalog_busy = False
            self._filter_items()
            warnings = [
                warning
                for warning in catalog.get("warnings", ())
                if isinstance(warning, str) and warning
            ]
            self.catalog_note.setText(
                "来源读取提醒：\n" + "\n".join(warnings) if warnings else ""
            )
            self.catalog_note.setVisible(bool(warnings))
            message = (
                f"已读取 {len(items)} 道题。题目分段和图片仍须逐题核对。"
                if items
                else "尚无可用题目，请先导入 Word 或查看来源读取提醒。"
            )
            if removed:
                message += f" 有 {removed} 道历史选题已变化，已取消勾选，请重新核对。"
                self._queue_selection_save()
            if warnings:
                message += f" 有 {len(warnings)} 条来源读取提醒，请先核对。"
            set_status(
                self.status,
                "attention" if removed or warnings or not items else "success",
                message,
            )
        except (KeyError, TypeError, ValueError):
            self.source_combo.blockSignals(False)
            self._catalog_failed(epoch, "题目目录暂时无法读取，请刷新或重新导入来源。")

    def _filter_items(self, *_args) -> None:
        current = self._current_key
        source = self.source_combo.currentData()
        query = self.search.text().strip().casefold()
        self._rendering_list = True
        self.question_list.blockSignals(True)
        self.question_list.clear()
        row_to_select = 0
        for key, value in self._items.items():
            if source and value.get("source_id") != source:
                continue
            question_text = " ".join(
                _text(block.get("text"))
                for block in value.get("question_blocks", ())
                if isinstance(block, dict)
            )
            searchable = " ".join(
                [
                    _text(value.get("title")),
                    _text(value.get("chapter")),
                    _text(value.get("source_name")),
                    question_text,
                ]
            ).casefold()
            if query and query not in searchable:
                continue
            title = _text(value.get("title")) or "原文题目"
            chapter = _text(value.get("chapter")) or "章节待核对"
            source_label = _text(value.get("source_label")) or _text(
                value.get("source_name")
            )
            state = (
                "可勾选"
                if value.get("selection_ready") is True
                else "分段待核对，暂不能勾选"
            )
            blocks = list(value.get("question_blocks", ())) + list(
                value.get("answer_blocks", ())
            )
            assets = [
                asset
                for block in blocks
                if isinstance(block, dict)
                for asset in block.get("assets", ())
                if isinstance(asset, dict)
            ]
            if assets:
                state += f" · 来源图 {len(assets)} 处"
            if (
                value.get("warnings")
                or any(
                    block.get("warnings") for block in blocks if isinstance(block, dict)
                )
                or any(asset.get("preview_supported") is not True for asset in assets)
            ):
                state += " · 有原文缺口/待核对提示"
            excerpt = " ".join(question_text.split())
            if excerpt.startswith(title):
                excerpt = excerpt[len(title) :].lstrip()
            label = "\n".join(
                part
                for part in (title, excerpt[:90], f"{chapter} · {source_label}", state)
                if part
            )
            widget = QListWidgetItem(label)
            widget.setData(Qt.ItemDataRole.UserRole, key)
            if value.get("selection_ready") is True:
                widget.setFlags(widget.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                widget.setCheckState(
                    Qt.CheckState.Checked
                    if key in self._selected
                    else Qt.CheckState.Unchecked
                )
            self.question_list.addItem(widget)
            if key == current:
                row_to_select = self.question_list.count() - 1
        self.question_list.blockSignals(False)
        self._rendering_list = False
        self.result_count.setText(
            f"显示 {self.question_list.count()} / {len(self._items)} 题"
        )
        if self.question_list.count():
            self.question_list.setCurrentRow(row_to_select)
        else:
            self._show_question(None)
        self._update_actions()

    def _current_changed(self, item: QListWidgetItem | None, *_args) -> None:
        if not self._rendering_list:
            self._show_question(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _clear_panel(self, index: int) -> None:
        layout = self._panel_layouts[index]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _show_question(self, key: str | None) -> None:
        for task_id in self._image_task_ids:
            self.tasks.cancel(task_id)
        self._image_task_ids.clear()
        self._detail_epoch += 1
        self._current_key = key
        self._image_targets = []
        self._loaded_tabs.clear()
        for index in (0, 1):
            self._clear_panel(index)
        value = self._items.get(key, {})
        self.detail_title.setText(_text(value.get("title")) or "没有符合条件的题目")
        source = _text(value.get("source_label")) or _text(value.get("source_name"))
        self.detail_note.setText(
            " · ".join(part for part in (source, _text(value.get("chapter"))) if part)
        )
        self.points.blockSignals(True)
        self.points.setValue(self._points.get(key, 2))
        self.points.blockSignals(False)
        self.points.setEnabled(bool(value))
        self.tabs.setCurrentIndex(0)
        self._render_tab(0)
        self._update_actions()

    def _tab_changed(self, index: int) -> None:
        if index in (0, 1):
            self._render_tab(index)

    def _render_tab(self, index: int) -> None:
        if index in self._loaded_tabs:
            return
        self._loaded_tabs.add(index)
        layout = self._panel_layouts[index]
        value = self._items.get(self._current_key, {})
        if not value:
            layout.addWidget(_label("请调整来源筛选或搜索词。", muted=True))
            layout.addStretch(1)
            return
        blocks = (
            list(value.get("answer_blocks", ()))
            if index
            else list(value.get("context_blocks", ()))
            + list(value.get("question_blocks", ()))
        )
        if not blocks:
            layout.addWidget(
                _label(
                    "本题未提取到参考答案，请核对来源。"
                    if index
                    else "本题暂无可显示的题面。",
                    muted=True,
                )
            )
        for block in blocks:
            if not isinstance(block, dict):
                continue
            layout.addWidget(
                _label(f"来源区块 {block.get('index', '待核对')}", muted=True)
            )
            layout.addWidget(_label(_text(block.get("text"))))
            for asset in block.get("assets", ()):
                if not isinstance(asset, dict):
                    continue
                label = _label(_text(asset.get("label")) or "原文图片", muted=True)
                layout.addWidget(label)
                image_widget = _label("正在读取来源图片…", muted=True)
                image_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image_widget.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
                )
                layout.addWidget(image_widget)
                if asset.get("preview_supported") is not True:
                    image_widget.setText("此对象暂不能直接预览，请核对原始 Word。")
                elif _text(asset.get("asset_id")):
                    self._load_image(value, asset, image_widget, self._detail_epoch)
            for warning in block.get("warnings", ()):
                if isinstance(warning, str) and warning:
                    layout.addWidget(_label("待核对：" + warning, muted=True))
        for warning in value.get("warnings", ()):
            if isinstance(warning, str) and warning:
                layout.addWidget(_label("本题提醒：" + warning, muted=True))
        layout.addStretch(1)

    def _load_image(
        self, question: dict, asset: dict, widget: QLabel, epoch: int
    ) -> None:
        cache_key = (question["key"], question["revision"], asset["asset_id"])
        if cache_key in self._image_cache:
            self._image_cache.move_to_end(cache_key)
            self._display_image(widget, self._image_cache[cache_key])
            return
        facade = self.facade

        def ready(result):
            if epoch != self._detail_epoch or self._current_key != question["key"]:
                return
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("bytes"), bytes)
                or result.get("mime_type")
                not in {
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/gif",
                    "image/bmp",
                    "image/tiff",
                }
            ):
                failed("")
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(result["bytes"]) or pixmap.isNull():
                failed("")
                return
            self._image_cache[cache_key] = pixmap
            while len(self._image_cache) > 32:
                self._image_cache.popitem(last=False)
            self._display_image(widget, pixmap)

        def failed(_message):
            if epoch == self._detail_epoch and self._current_key == question["key"]:
                widget.setText("此来源图片暂时无法读取，请重新选题或核对原始 Word。")

        task_id = self._submit(
            "读取 Word 题目来源图片",
            lambda: facade.word_question_image(*cache_key),
            ready,
            failed,
        )
        if task_id:
            self._image_task_ids.add(task_id)

    def _display_image(self, widget: QLabel, pixmap: QPixmap) -> None:
        self._image_targets.append((widget, pixmap))
        self._scale_images()

    def _scale_images(self) -> None:
        if self._closed:
            return
        for widget, pixmap in self._image_targets:
            available = max(40, widget.parentWidget().width() - 28)
            scaled = pixmap.scaled(
                min(pixmap.width(), available),
                min(640, pixmap.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            widget.setPixmap(scaled)
            widget.setFixedHeight(scaled.height() + 8)

    def _checked(self, item: QListWidgetItem) -> None:
        if self._rendering_list:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        value = self._items.get(key, {})
        if (
            item.checkState() == Qt.CheckState.Checked
            and value.get("selection_ready") is True
        ):
            self._selected[key] = value["revision"]
        else:
            self._selected.pop(key, None)
        self._invalidate_preview()
        self._queue_selection_save()
        self._update_actions()

    def _clear_selection(self) -> None:
        self._selected.clear()
        self._invalidate_preview()
        self._queue_selection_save()
        self._filter_items()

    def _points_changed(self, value: int) -> None:
        if self._current_key not in self._items:
            return
        self._points[self._current_key] = value
        if self._current_key in self._selected:
            self._invalidate_preview()
            self._queue_selection_save()
            self._update_actions()

    def _adjust_range(self) -> None:
        value = self._items.get(self._current_key)
        if not value or self._range_busy:
            return
        question = deepcopy(value)
        key, revision = question["key"], question["revision"]
        facade = self.facade
        self._range_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在读取完整 Word 来源以核对题目范围…")

        def failed(message):
            self._range_busy = False
            set_status(self.status, "error", message)
            self._update_actions()

        def source_ready(source):
            if self._current_key != key:
                self._range_busy = False
                self._update_actions()
                return
            if not isinstance(source, dict) or not isinstance(
                source.get("blocks"), list
            ):
                failed("完整 Word 来源暂时无法读取。")
                return
            editor = WordQuestionRangeDialog(question, source, self)
            accepted = editor.exec() == editor.DialogCode.Accepted
            ranges = editor.ranges
            editor.deleteLater()
            if not accepted or ranges is None:
                self._range_busy = False
                self._update_actions()
                return

            def updated(item):
                self._range_busy = False
                if (
                    not isinstance(item, dict)
                    or not _text(item.get("key"))
                    or not _text(item.get("revision"))
                ):
                    failed("题目范围的保存结果无效，请刷新后重新核对。")
                    return
                self._items.pop(key, None)
                self._items[item["key"]] = deepcopy(item)
                if key in self._selected:
                    self._selected.pop(key)
                    self._selected[item["key"]] = item["revision"]
                    self._points[item["key"]] = self._points.get(key, 2)
                    self._queue_selection_save()
                self._invalidate_preview()
                if self._current_key == key:
                    self._current_key = item["key"]
                self._filter_items()
                set_status(
                    self.status,
                    "success",
                    "本题范围已更新，请重新核对题面与答案后预览选题。",
                )

            self._submit(
                "保存 Word 题目范围",
                lambda: facade.word_question_update_range(key, revision, **ranges),
                updated,
                failed,
            )

        self._submit(
            "读取 Word 完整来源",
            lambda: facade.word_question_source(key, revision),
            source_ready,
            failed,
        )

    def _queue_selection_save(self) -> None:
        self._selection_to_save = deepcopy(self.selections)
        self._save_timer.start()

    def _persist_selection(self) -> None:
        if self._selection_save_busy or self._selection_to_save is None or self._closed:
            return
        selections = self._selection_to_save
        self._selection_to_save = None
        self._selection_save_busy = True
        facade = self.facade

        def finished(_value, failed=False):
            self._selection_save_busy = False
            if failed:
                self._selection_to_save = self.selections
                self._closing_result = None
                set_status(
                    self.status,
                    "attention",
                    "当前勾选仍在本窗口中，但未能保存；关闭前请重试勾选。",
                )
                return
            if self._selection_to_save is not None:
                self._persist_selection()
            elif self._closing_result is not None:
                self._finish_dialog(self._closing_result)

        self._submit(
            "保存 Word 勾选",
            lambda: facade.word_question_save_selection(selections),
            finished,
            lambda message: finished(message, True),
        )

    def _invalidate_preview(self) -> None:
        self._reference_epoch += 1
        self._reference_busy = False
        self.reference = None
        self.preparation_reference = None
        if self._closing_result == QDialog.DialogCode.Accepted:
            self._closing_result = None
        self._preview_reference = None
        self._preview_selection = ()
        self.preview.clear()
        self.import_button.setEnabled(False)

    @staticmethod
    def _valid_reference(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and isinstance(value.get("materials"), str)
            and bool(value["materials"].strip())
            and isinstance(value.get("warnings", []), (list, tuple))
            and all(isinstance(item, str) for item in value.get("warnings", []))
        )

    def _preview_selected(self) -> None:
        if (
            not self._selected
            or self._catalog_busy
            or self._reference_busy
            or self._export_busy
            or self._range_busy
        ):
            return
        self._invalidate_preview()
        self._reference_busy = True
        epoch = self._reference_epoch
        selection_key = self._selection_key()
        selections = self.selections
        facade = self.facade
        set_status(self.status, "info", "正在生成完整选题参考…")
        self._update_actions()

        def ready(value):
            if epoch != self._reference_epoch or selection_key != self._selection_key():
                return
            self._reference_busy = False
            if not self._valid_reference(value):
                self._reference_failed(
                    epoch, "选题参考暂时无法生成，请重新核对所选题目。"
                )
                return
            self._preview_reference = deepcopy(value)
            self._preview_selection = selection_key
            missing = [
                warning
                for warning in value.get("warnings", [])
                if warning and warning not in value["materials"]
            ]
            self.preview.setPlainText(
                value["materials"]
                + ("\n\n待核对提醒：\n" + "\n".join(missing) if missing else "")
            )
            self.tabs.setCurrentIndex(2)
            set_status(
                self.status,
                "success",
                f"已生成 {len(selections)} 道题的完整参考。请核对后确认；确认时会重新读取并比较。",
            )
            self._update_actions()

        self._submit(
            "预览 Word 选题",
            lambda: facade.word_question_reference(selections),
            ready,
            lambda message: self._reference_failed(epoch, message),
        )

    def _reference_failed(self, epoch, message):
        if epoch != self._reference_epoch:
            return
        self._reference_busy = False
        self._preview_reference = None
        self._preview_selection = ()
        set_status(self.status, "error", message)
        self._update_actions()

    def _confirm(self) -> None:
        if (
            self._preview_reference is None
            or self._preview_selection != self._selection_key()
            or self._reference_busy
            or self._export_busy
            or self._range_busy
        ):
            return
        epoch = self._reference_epoch
        previous = deepcopy(self._preview_reference)
        selections = self.selections
        selection_key = self._selection_key()
        facade = self.facade
        self._reference_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在再次核对所选题目和完整参考…")

        def ready(current):
            if epoch != self._reference_epoch or selection_key != self._selection_key():
                return
            self._reference_busy = False
            if not self._valid_reference(current) or current != previous:
                self._invalidate_preview()
                set_status(
                    self.status,
                    "attention",
                    "选题内容已变化，请刷新目录并重新预览后确认。",
                )
                self._update_actions()
                return
            self.reference = deepcopy(current)
            self.preparation_reference = self.reference
            self._finish_dialog(QDialog.DialogCode.Accepted)

        self._submit(
            "确认 Word 选题参考",
            lambda: facade.word_question_reference(selections),
            ready,
            lambda message: self._reference_failed(epoch, message),
        )

    def _export(self) -> None:
        if not self.export_button.isEnabled():
            return
        selections = self.selections
        title = self.export_title.text().strip() or "Word 选题练习"
        show_scores = self.show_student_scores.isChecked()
        facade = self.facade
        self._export_busy = True
        self._export_paths = {}
        self.student_button.hide()
        self.teacher_button.hide()
        self.export_note.hide()
        self.export_note.clear()
        self._update_actions()
        set_status(self.status, "info", "正在本机导出学生版与教师版练习…")

        def ready(value):
            self._export_busy = False
            if not isinstance(value, dict) or not all(
                isinstance(value.get(key), str)
                for key in ("student_path", "teacher_path")
            ):
                failed("练习导出结果不完整，请重试。")
                return
            self._export_paths = dict(value)
            self.student_button.show()
            self.teacher_button.show()
            warnings = [
                warning
                for warning in value.get("warnings", ())
                if isinstance(warning, str) and warning
            ]
            self.export_note.setText(
                "导出提醒：\n" + "\n".join(warnings) if warnings else ""
            )
            self.export_note.setVisible(bool(warnings))
            set_status(
                self.status,
                "attention" if warnings else "success",
                f"已导出 {len(selections)} 题的学生版与教师版练习，请检查图片、题目范围及参考答案。",
            )
            self._update_actions()

        def failed(message):
            self._export_busy = False
            set_status(self.status, "error", message)
            self._update_actions()

        self._submit(
            "导出 Word 选题练习",
            lambda: facade.word_question_export(title, selections, show_student_scores=show_scores),
            ready,
            failed,
        )

    def _open_export(self, key: str) -> None:
        value = self._export_paths.get(key)
        if not value or not Path(value).is_file():
            set_status(self.status, "attention", "导出的文件暂时不可用，请重新导出。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(value).resolve())))

    def _step(self, direction: int) -> None:
        index = self.question_list.currentRow() + direction
        if 0 <= index < self.question_list.count():
            self.question_list.setCurrentRow(index)

    def _update_actions(self) -> None:
        count = len(self._selected)
        self.selection_count.setText(f"已选 {count} 题；筛选或切换来源会保留勾选。")
        self.question_list.setEnabled(not self._catalog_busy)
        self.reload_button.setEnabled(not self._catalog_busy and not self._export_busy)
        self.range_button.setEnabled(
            self._current_key in self._items
            and not self._range_busy
            and not self._catalog_busy
            and not self._export_busy
        )
        self.preview_button.setEnabled(
            count > 0
            and not self._catalog_busy
            and not self._reference_busy
            and not self._export_busy
            and not self._range_busy
        )
        self.import_button.setEnabled(
            self._preview_reference is not None
            and self._preview_selection == self._selection_key()
            and not self._reference_busy
            and not self._catalog_busy
            and not self._export_busy
            and not self._range_busy
        )
        self.clear_button.setEnabled(count > 0)
        self.show_student_scores.setEnabled(not self._export_busy)
        exportable = count > 0 and all(
            self._items.get(key, {}).get("export_ready") is True
            for key in self._selected
        )
        self.export_button.setEnabled(
            exportable
            and not self._export_busy
            and not self._catalog_busy
            and not self._reference_busy
            and not self._range_busy
            and callable(getattr(self.facade, "word_question_export", None))
        )
        self.export_button.setToolTip(
            "所选题目中有暂不能导出的条目，请先核对其分段与缺口。"
            if count and not exportable
            else "在本机生成学生版与教师版练习"
        )
        row = self.question_list.currentRow()
        self.previous_button.setEnabled(row > 0)
        self.next_button.setEnabled(0 <= row < self.question_list.count() - 1)

    def _finish_dialog(self, result: QDialog.DialogCode) -> None:
        if self._closed:
            return
        if self._selection_save_busy or self._selection_to_save is not None:
            self._closing_result = result
            self._save_timer.stop()
            self._persist_selection()
            set_status(self.status, "info", "正在保存本次勾选…")
            return
        self._closed = True
        self._search_timer.stop()
        self._save_timer.stop()
        self._detail_epoch += 1
        for task_id in tuple(self._jobs.values()):
            if task_id:
                self.tasks.cancel(task_id)
        self.done(result)

    def reject(self) -> None:
        if self._export_busy or self._range_busy:
            set_status(
                self.status,
                "attention",
                "本地导出或题目范围任务尚未完成，请稍候再关闭。",
            )
            return
        self._finish_dialog(QDialog.DialogCode.Rejected)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._export_busy or self._range_busy:
            event.ignore()
            set_status(
                self.status,
                "attention",
                "本地导出或题目范围任务尚未完成，请稍候再关闭。",
            )
            return
        self._finish_dialog(QDialog.DialogCode.Rejected)
        event.accept() if self._closed else event.ignore()

    def resizeEvent(self, event: QResizeEvent) -> None:
        narrow = event.size().width() < 760
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([210, 370] if narrow else [380, 770])
        self.action_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        self.export_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        super().resizeEvent(event)
        QTimer.singleShot(0, self._scale_images)


__all__ = ["WordQuestionDialog"]
