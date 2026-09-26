from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .library_detail import ImageZoomDialog

REVIEW_LABELS = {
    "pending": "未核对",
    "checked": "已记录核对",
    "needs_correction": "待修订",
    "stale": "资料已更新，需重新核对",
}


def label(text: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setMinimumWidth(0)
    return widget


def combo() -> QComboBox:
    widget = QComboBox()
    widget.setMinimumContentsLength(8)
    widget.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return widget


class HandoutCandidateDialog(QDialog):
    """Browse local handout candidates, compare original pages and save personal checks."""

    def __init__(self, facade: Any, tasks: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.setWindowTitle("已整理讲义 · 题面与答案核对")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1020, 820)
        self.setMinimumSize(360, 580)
        self._items: list[dict[str, Any]] = []
        self._practice_selection: dict[str, str] = {}
        self._practice_dialog = None
        self._shown: list[dict[str, Any]] = []
        self._detail: dict[str, Any] | None = None
        self._closed = self._dirty = self._saving = False
        self._generation = 0
        self._previous_row = -1
        self._active_filters = (None, "native_text_complete", "")
        self._total_packages = 0
        self._zoom: ImageZoomDialog | None = None
        root = QVBoxLayout(self)
        root.addWidget(
            label(
                "浏览已整理的 Word 讲义；文字不完整的题保留原页核对入口。记录仅保存在本机，不自动加入正式题库；参考答案非官方。"
            )
        )
        filters = QHBoxLayout()
        self.package = combo()
        self.package.addItem("全部讲义", None)
        self.state_filter = combo()
        for title, value in (
            ("原生文字完整", "native_text_complete"),
            ("全部候选", "all"),
            ("待视觉补全", "incomplete"),
            ("已记录核对", "checked"),
            ("待修订", "needs_correction"),
        ):
            self.state_filter.addItem(title, value)
        filters.addWidget(self.package, 1)
        filters.addWidget(self.state_filter, 1)
        root.addLayout(filters)
        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索题组标题、题面或参考答案")
        self.query.setClearButtonEnabled(True)
        self.search_button = QPushButton("查找")
        self.refresh_button = QPushButton("刷新")
        search.addWidget(self.query, 1)
        search.addWidget(self.search_button)
        search.addWidget(self.refresh_button)
        root.addLayout(search)
        self.summary = label("正在读取已整理讲义…")
        root.addWidget(self.summary)
        self.warning = label()
        self.warning.setVisible(False)
        root.addWidget(self.warning)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.splitter.addWidget(self.results)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(4, 0, 0, 0)
        self.title = label("请选择一题")
        self.title.setObjectName("CardTitle")
        self.source = label()
        self.source.setObjectName("MutedLabel")
        detail_layout.addWidget(self.title)
        detail_layout.addWidget(self.source)
        self.tabs = QTabWidget()
        self.question = QPlainTextEdit()
        self.answer = QPlainTextEdit()
        for title, widget in (("题面", self.question), ("参考答案", self.answer)):
            widget.setReadOnly(True)
            self.tabs.addTab(widget, title)
        page_panel = QWidget()
        pages_layout = QVBoxLayout(page_panel)
        self.page_combo = combo()
        self.page_button = QPushButton("打开原页，可缩放")
        self.page_button.setEnabled(False)
        self.page_hint = label(
            "按来源页码查看整页；题干附近可能含其他题目，请核对题号与跨页内容。"
        )
        pages_layout.addWidget(self.page_combo)
        pages_layout.addWidget(self.page_button)
        pages_layout.addWidget(self.page_hint)
        pages_layout.addStretch(1)
        self.tabs.addTab(page_panel, "原页")
        review_panel = QWidget()
        review_layout = QVBoxLayout(review_panel)
        self.review_status = label("未核对")
        self.question_checked = QCheckBox("已核对题面完整性（含选项、图形和跨页内容）")
        self.answer_checked = QCheckBox("已核对参考答案与本题对应关系")
        self.decision = combo()
        self.decision.addItem("记录本次核对", "checked")
        self.decision.addItem("标记待修订", "needs_correction")
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(
            "记录缺图、错配或需修订之处；点击保存后保留，最多 2000 字。"
        )
        self.save_button = QPushButton("保存核对记录")
        review_layout.addWidget(self.review_status)
        review_layout.addWidget(self.question_checked)
        review_layout.addWidget(self.answer_checked)
        review_layout.addWidget(self.decision)
        review_layout.addWidget(self.note, 1)
        review_layout.addWidget(
            label("以上核对不等于答案化学正确性验收，也不会改变来源的待补图状态。")
        )
        review_layout.addWidget(self.save_button)
        self.tabs.addTab(review_panel, "核对")
        detail_layout.addWidget(self.tabs, 1)
        self.copy_button = QPushButton("复制当前文字（含来源与状态）")
        self.copy_button.setEnabled(False)
        detail_layout.addWidget(self.copy_button)
        self.splitter.addWidget(detail_panel)
        self.splitter.setSizes([300, 650])
        self.splitter.setStretchFactor(1, 2)
        root.addWidget(self.splitter, 1)
        self.status = label()
        root.addWidget(self.status)
        self.close_button = QPushButton("关闭")
        self.practice_button = QPushButton("选题练习 · 草稿与导出")
        root.addWidget(self.practice_button)
        root.addWidget(self.close_button)
        self.practice_button.clicked.connect(self._open_practice)
        self.results.itemChanged.connect(self._practice_checked)
        self.results.currentRowChanged.connect(self._select_row)
        self.package.currentIndexChanged.connect(self._filter)
        self.state_filter.currentIndexChanged.connect(self._filter)
        self.search_button.clicked.connect(self._filter)
        self.query.returnPressed.connect(self._filter)
        self.refresh_button.clicked.connect(self._refresh)
        self.save_button.clicked.connect(self._save)
        self.copy_button.clicked.connect(self._copy)
        self.page_button.clicked.connect(self._open_page)
        self.close_button.clicked.connect(self.close)
        for signal in (
            self.question_checked.toggled,
            self.answer_checked.toggled,
            self.decision.currentIndexChanged,
            self.note.textChanged,
        ):
            signal.connect(self._edited)
        self._refresh()

    def _edited(self, *_args: Any) -> None:
        if self._detail is not None:
            self._dirty = True

    def _discard_ok(self) -> bool:
        if self._saving:
            self.status.setText("正在保存核对记录，请稍候。")
            return False
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "尚未保存核对记录",
            "放弃尚未保存的核对修改？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._dirty = False
        return True

    def _refresh(self) -> None:
        if not self._discard_ok():
            return
        self._generation += 1
        self._detail = None
        self._enable_detail(False)
        for widget in (
            self.package,
            self.state_filter,
            self.query,
            self.search_button,
            self.results,
        ):
            widget.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText("正在核验讲义目录与候选文件…")
        self.tasks.submit(
            "读取已整理讲义",
            self.facade.handout_candidate_catalog,
            on_success=self._loaded,
            on_failure=self._failed,
        )

    def _loaded(self, result: Any) -> None:
        if self._closed:
            return
        self.refresh_button.setEnabled(True)
        for widget in (
            self.package,
            self.state_filter,
            self.query,
            self.search_button,
            self.results,
        ):
            widget.setEnabled(True)
        self._items = result["items"]
        self._total_packages = len(
            {
                package
                for batch in result.get("batches", [])
                for package in batch.get("package_ids", [])
            }
            | {item["package_id"] for item in self._items}
        )
        self.warning.setText("\n".join(result.get("warnings", [])))
        self.warning.setVisible(bool(result.get("warnings")))
        selected = self.package.currentData()
        self.package.blockSignals(True)
        self.package.clear()
        self.package.addItem("全部讲义", None)
        packages = {}
        for item in self._items:
            packages.setdefault(item["package_id"], item["source_name"])
        for key, name in packages.items():
            self.package.addItem(name, key)
        self.package.setCurrentIndex(max(0, self.package.findData(selected)))
        self.package.blockSignals(False)
        self.status.setText("读取完成；可查看原页、记录核对或复制带来源的文字。")
        self._filter()

    def _filter(self, *_args: Any) -> None:
        if not self._discard_ok():
            package, state, query = self._active_filters
            for widget, value in ((self.package, package), (self.state_filter, state)):
                widget.blockSignals(True)
                widget.setCurrentIndex(max(0, widget.findData(value)))
                widget.blockSignals(False)
            self.query.setText(query)
            return
        self._generation += 1
        self._detail = None
        self._enable_detail(False)
        key, state, query = (
            self.package.currentData(),
            self.state_filter.currentData(),
            self.query.text().strip().casefold(),
        )
        self._active_filters = (key, state, self.query.text())
        self._shown = [
            item
            for item in self._items
            if (key is None or item["package_id"] == key)
            and (
                state == "all"
                or item["classification"] == state
                or (
                    state == "incomplete"
                    and item["classification"] != "native_text_complete"
                )
                or (
                    state in ("checked", "needs_correction")
                    and item["review_state"] == state
                )
            )
            and (
                not query
                or query
                in "\n".join(
                    str(item.get(field, ""))
                    for field in (
                        "title",
                        "question_text",
                        "answer_text",
                        "source_name",
                    )
                ).casefold()
            )
        ]
        self.results.blockSignals(True)
        self.results.clear()
        for item in self._shown:
            row = QListWidgetItem(
                f"{item['title']}\n{item['classification_label']} · {REVIEW_LABELS[item['review_state']]}"
            )
            row.setToolTip(item["source_name"])
            row.setData(Qt.ItemDataRole.UserRole, item["key"])
            if item.get("practice_eligible"):
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    Qt.CheckState.Checked
                    if item["key"] in self._practice_selection
                    else Qt.CheckState.Unchecked
                )
            self.results.addItem(row)
        self.results.blockSignals(False)
        complete = sum(
            item["classification"] == "native_text_complete" for item in self._items
        )
        self.summary.setText(
            f"已读 {self._total_packages} 个资料包 · 共 {len(self._items)} 题候选 · 文字完整 {complete} · 可选练习 {sum(bool(item.get('practice_eligible')) for item in self._items)} · 待补全 {len(self._items) - complete}；当前显示 {len(self._shown)} 题"
        )
        self._previous_row = -1
        self.question.clear()
        self.answer.clear()
        self.source.clear()
        self.title.setText(
            "请选择一题" if self._shown else "没有匹配候选，可调整筛选条件"
        )
        if self._shown:
            self.results.setCurrentRow(0)

    def _select_row(self, row: int) -> None:
        if not 0 <= row < len(self._shown):
            return
        if not self._discard_ok():
            self.results.blockSignals(True)
            self.results.setCurrentRow(self._previous_row)
            self.results.blockSignals(False)
            return
        self._previous_row = row
        self._generation += 1
        generation = self._generation
        key = self._shown[row]["key"]
        self._detail = None
        self._enable_detail(False)
        self.status.setText("正在核验本题与配对答案…")
        self.tasks.submit(
            "读取讲义题面与答案",
            lambda: self.facade.handout_candidate_detail(key),
            on_success=lambda value: self._show_detail(generation, value),
            on_failure=lambda message: self._detail_failed(generation, message),
        )

    def _practice_checked(self, row: QListWidgetItem) -> None:
        key = row.data(Qt.ItemDataRole.UserRole)
        item = next((item for item in self._shown if item["key"] == key), None)
        if item is None or not item.get("practice_eligible"):
            return
        if row.checkState() == Qt.CheckState.Checked:
            self._practice_selection[key] = item["revision"]
        else:
            self._practice_selection.pop(key, None)
        self.practice_button.setText(
            f"选题练习 · 已选 {len(self._practice_selection)} 题 · 草稿与导出"
        )

    def _open_practice(self) -> None:
        if not self._discard_ok():
            return
        from .handout_practice_dialog import HandoutPracticeDialog

        if self._practice_dialog is not None:
            self._practice_dialog.raise_()
            return
        self._practice_dialog = HandoutPracticeDialog(
            self.facade,
            self.tasks,
            [
                {"key": key, "revision": revision}
                for key, revision in self._practice_selection.items()
            ],
            self._items,
            self,
        )
        self._practice_dialog.destroyed.connect(
            lambda: setattr(self, "_practice_dialog", None)
        )
        self._practice_dialog.show()

    def _show_detail(self, generation: int, item: Any) -> None:
        if self._closed or generation != self._generation:
            return
        self._detail = item
        self.title.setText(item["title"])
        self.source.setText(
            f"{item['source_name']}\n{item['classification_label']} · {item['atomic_count']} 个作答单元 · 原生题组：{item['parent_title']}"
        )
        notice = (
            ""
            if item["classification"] == "native_text_complete"
            else "【原生文字片段，不能替代完整原题；请查看原页】\n\n"
        )
        self.question.setPlainText(
            notice + (item["question_text"] or "暂无可用原生题面文字，请查看原页。")
        )
        answer = item["answer_text"] or "尚无已对应的原生答案文字。"
        answer_heading = (
            ""
            if answer.startswith("【非官方")
            else "【非官方参考答案，未独立验证正确性】\n\n"
        )
        self.answer.setPlainText(answer_heading + notice + answer)
        self.page_combo.clear()
        for role, heading in (("question", "题目"), ("answer", "答案")):
            for index, page in enumerate(item.get(role + "_pages", [])):
                self.page_combo.addItem(
                    f"{heading}原页 · 第 {page.get('page', '待核验')} 页", (role, index)
                )
        self.page_hint.setText(
            "\n".join(item["blocker_labels"])
            or "原生文字标记为完整；仍可对照原页核验上下标、图形与答案对应关系。"
        )
        review = item.get("review") or {}
        current = item["review_state"] not in ("pending", "stale")
        self.question_checked.setChecked(
            current and review.get("question_checked", False)
        )
        self.answer_checked.setChecked(current and review.get("answer_checked", False))
        self.note.setPlainText(review.get("note", ""))
        self.decision.setCurrentIndex(
            1 if review.get("decision") == "needs_correction" else 0
        )
        self.review_status.setText(REVIEW_LABELS[item["review_state"]])
        self._dirty = False
        self._enable_detail(True)
        self.status.setText("文字显示保留原生内容，不补写缺图、方程式或答案。")

    def _enable_detail(self, enabled: bool) -> None:
        self.copy_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.page_button.setEnabled(enabled and self.page_combo.count() > 0)

    def _save(self) -> None:
        if self._detail is None or self._saving:
            return
        item, generation = self._detail, self._generation
        review = dict(
            decision=self.decision.currentData(),
            question_checked=self.question_checked.isChecked(),
            answer_checked=self.answer_checked.isChecked(),
            note=self.note.toPlainText(),
        )
        self._saving = True
        self._enable_detail(False)
        self.tabs.setEnabled(False)
        self.tasks.submit(
            "保存讲义核对记录",
            lambda: self.facade.record_handout_candidate_review(
                item["key"], item["revision"], **review
            ),
            on_success=lambda result: self._saved(generation, item, result),
            on_failure=self._save_failed,
        )

    def _saved(self, generation: int, item: Any, event: Any) -> None:
        self._saving = False
        if self._closed or generation != self._generation:
            return
        self.tabs.setEnabled(True)
        self._dirty = False
        updated = {**item, "review": event, "review_state": event["decision"]}
        self._items = [
            updated if row["key"] == item["key"] else row for row in self._items
        ]
        self._show_detail(generation, updated)
        row = self.results.item(self._previous_row)
        if row:
            row.setText(
                f"{item['title']}\n{item['classification_label']} · {REVIEW_LABELS[event['decision']]}"
            )
        self.status.setText(
            "核对记录已保存到本机，重开可继续查看；未修改来源与正式题库。"
        )

    def _save_failed(self, message: str) -> None:
        self._saving = False
        if self._closed:
            return
        self.tabs.setEnabled(True)
        self._enable_detail(self._detail is not None)
        self.status.setText(message)

    def _open_page(self) -> None:
        if self._detail is None or self.page_combo.currentData() is None:
            return
        item, generation = self._detail, self._generation
        role, index = self.page_combo.currentData()
        caption = self.page_combo.currentText()
        self.page_button.setEnabled(False)
        self.tasks.submit(
            "核验讲义原页",
            lambda: self.facade.handout_candidate_page(
                item["key"], item["revision"], role, index
            ),
            on_success=lambda data: self._page_ready(generation, caption, data),
            on_failure=lambda message: self._detail_failed(generation, message),
        )

    def _page_ready(self, generation: int, caption: str, data: Any) -> None:
        if self._closed or generation != self._generation:
            return
        self.page_button.setEnabled(True)
        pixmap = QPixmap()
        if not isinstance(data, bytes) or not pixmap.loadFromData(data):
            self.status.setText("原页无法解码，请检查图像文件。")
            return
        if self._zoom is not None:
            self._zoom.close()
        viewer = ImageZoomDialog(pixmap, caption, self)
        self._zoom = viewer
        viewer.destroyed.connect(lambda: self._clear_zoom(viewer))
        viewer.show()

    def _clear_zoom(self, viewer: Any) -> None:
        if self._zoom is viewer:
            self._zoom = None

    def _copy(self) -> None:
        if self._detail is None:
            return
        item, generation = self._detail, self._generation
        self.tasks.submit(
            "准备讲义文字",
            lambda: self.facade.handout_candidate_copy_text(
                item["key"], item["revision"]
            ),
            on_success=lambda text: self._copied(generation, text),
            on_failure=lambda message: self._detail_failed(generation, message),
        )

    def _copied(self, generation: int, text: str) -> None:
        if not self._closed and generation == self._generation:
            QApplication.clipboard().setText(text)
            self.status.setText("已复制题面、配对参考答案、来源和完整性说明。")

    def _detail_failed(self, generation: int, message: str) -> None:
        if not self._closed and generation == self._generation:
            self.status.setText(message)
            self.page_button.setEnabled(
                self._detail is not None and self.page_combo.count() > 0
            )

    def _failed(self, message: str) -> None:
        if not self._closed:
            self.refresh_button.setEnabled(True)
            self.results.clear()
            self._items = []
            self._shown = []
            self.question.clear()
            self.answer.clear()
            self.source.clear()
            self.summary.setText("本次目录读取未完成，可点击刷新重试。")
            self.status.setText(message)

    def resizeEvent(self, event: Any) -> None:
        vertical = self.width() < 720
        orientation = Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([150, 430] if vertical else [300, 650])
        super().resizeEvent(event)

    def closeEvent(self, event: Any) -> None:
        if self._practice_dialog is not None and not self._practice_dialog.close():
            event.ignore()
            return
        if not self._discard_ok():
            event.ignore()
            return
        self._closed = True
        self._generation += 1
        if self._zoom is not None:
            self._zoom.close()
        super().closeEvent(event)

    def reject(self) -> None:
        self.close()
