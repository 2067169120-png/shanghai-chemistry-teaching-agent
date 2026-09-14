"""Choose Word blocks and textbook concepts for a local preparation reference.

This dialog is intentionally a small, synchronous native UI boundary.  The
facade owns Word parsing and concept lookup; the dialog only chooses a range,
chooses concepts, asks for a preview, and re-compiles the exact selection
before accepting it.  No provider or network surface is used here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components import set_status


def _message(exc: Exception, fallback: str) -> str:
    value = getattr(exc, "message_zh", None)
    return str(value).strip() if value else fallback


def _text(value: object) -> str:
    return str(value or "").strip()


class PreparationSourcesDialogError(ValueError):
    """Safe, user-facing validation message raised by this dialog."""

    def __init__(self, message: str) -> None:
        self.message_zh = message
        super().__init__(message)


class PreparationSourcesDialog(QDialog):
    """Preview a bounded Word/concepts reference before appending it."""

    def __init__(self, facade: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.facade = facade
        self.reference: dict[str, Any] | None = None
        self._word_path: str | None = None
        self._word_sha256: str | None = None
        self._word_blocks: list[dict[str, Any]] = []
        self._selected_concepts: dict[str, dict[str, Any]] = {}
        self._textbook_excerpts: dict[str, dict[str, Any]] = {}
        self._loading_concepts = False
        self._loading_blocks = False
        self._preview_key: tuple[Any, ...] | None = None
        self._narrow_layout: bool | None = None
        self._splitter: QSplitter | None = None
        self._word_heading: QLabel | None = None
        self._concept_heading: QLabel | None = None

        self.setWindowTitle("从 Word 与教材知识点导入备课参考")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 420)
        # The dialog is also used from a narrow native workbench window.  Keep
        # the requested 420px capture size reachable; the list and preview
        # controls wrap rather than forcing a hidden horizontal surface.
        self.setMinimumSize(360, 320)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        self.intro_label = QLabel(
            "选择对应复习讲义的 Word 区块，并勾选教材知识点；先生成可核对的全文，"
            '确认时会再次核对所选资料。不自动匹配其他内容，也不调用模型。'
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setObjectName("PageSubtitle")
        root.addWidget(self.intro_label)

        self.warning_label = QLabel(
            "提示：候选知识点和部分 Word 对象可能不是完整原文，导入后仍需教师核验；"
            '只有在生成备课初稿时，才会按原有确认流程处理发送文字给模型。'
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setObjectName("StatusAttention")
        self.warning_label.setAccessibleName("备课参考来源警告")
        root.addWidget(self.warning_label)

        source_row = QHBoxLayout()
        source_row.setSpacing(6)
        source_label = QLabel("Word 讲义")
        source_label.setObjectName("MutedLabel")
        source_row.addWidget(source_label)
        self.word_path = QLineEdit()
        self.word_path.setReadOnly(True)
        self.word_path.setPlaceholderText(
            "可选：选择 .docx；不选 Word 时也可只用教材知识点"
        )
        self.word_path.setAccessibleName("已选择的 Word 讲义")
        source_row.addWidget(self.word_path, 1)
        self.word_button = QPushButton("选择 Word…")
        self.word_button.setObjectName("QuietButton")
        self.word_button.setAccessibleName("选择 Word 讲义进行本地预览")
        self.word_button.clicked.connect(self._choose_word)
        source_row.addWidget(self.word_button)
        root.addLayout(source_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._splitter = splitter

        word_panel = QWidget()
        word_layout = QVBoxLayout(word_panel)
        word_layout.setContentsMargins(0, 0, 0, 0)
        word_layout.setSpacing(5)
        self._word_heading = QLabel('Word段落与表格')
        self._word_heading.setObjectName("CardTitle")
        self._word_heading.setWordWrap(True)
        word_layout.addWidget(self._word_heading)
        self.section_picker = QComboBox()
        self.section_picker.setAccessibleName("按Word章节选择完整区块范围")
        self.section_picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.section_picker.setMinimumContentsLength(10)
        self.section_picker.addItem("按章节选范围（可手动调整）", None)
        self.section_picker.setEnabled(False)
        self.section_picker.activated.connect(self._section_selected)
        self.section_picker.setToolTip(
            "依据Word一级、二级标题定位，不使用目录项；缺少标题样式时请手动选区块。范围内浏览不会改变已选范围。"
        )
        word_layout.addWidget(self.section_picker)
        self.block_list = QListWidget()
        self.block_list.setAccessibleName("Word 段落与表格区块列表")
        self.block_list.setWordWrap(False)
        self.block_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.block_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.block_list.setMinimumHeight(45)
        self.block_list.itemSelectionChanged.connect(self._block_list_selected)
        word_layout.addWidget(self.block_list, 1)
        range_row = QHBoxLayout()
        range_row.setSpacing(5)
        range_row.addWidget(QLabel('段落范围'))
        self.block_start = QSpinBox()
        self.block_start.setRange(0, 0)
        self.block_start.setAccessibleName("Word 区块起始序号")
        self.block_end = QSpinBox()
        self.block_end.setRange(0, 0)
        self.block_end.setAccessibleName("Word 区块结束序号")
        range_row.addWidget(self.block_start)
        range_row.addWidget(QLabel("至"))
        range_row.addWidget(self.block_end)
        self.single_block_button = QPushButton("仅选当前块")
        self.single_block_button.setAccessibleName("仅导入当前高亮Word区块")
        self.single_block_button.clicked.connect(self._select_current_block)
        range_row.addWidget(self.single_block_button)
        range_row.addStretch(1)
        word_layout.addLayout(range_row)
        self.block_hint = QLabel("未选择 Word；可以只勾选教材知识点。")
        self.block_hint.setObjectName("MutedLabel")
        self.block_hint.setWordWrap(True)
        self.block_hint.setMaximumHeight(28)
        word_layout.addWidget(self.block_hint)
        splitter.addWidget(word_panel)

        concept_panel = QWidget()
        concept_layout = QVBoxLayout(concept_panel)
        concept_layout.setContentsMargins(0, 0, 0, 0)
        concept_layout.setSpacing(5)
        self._concept_heading = QLabel("教材知识点（可多选）")
        self._concept_heading.setObjectName("CardTitle")
        self._concept_heading.setWordWrap(True)
        concept_layout.addWidget(self._concept_heading)
        self.concept_search = QLineEdit()
        self.concept_search.setPlaceholderText("输入关键词搜索教材概念")
        self.concept_search.setAccessibleName("教材知识点关键词搜索")
        self.concept_search.textChanged.connect(self._load_concepts)
        concept_layout.addWidget(self.concept_search)
        self.concept_list = QListWidget()
        self.concept_list.setAccessibleName("教材知识点多选列表")
        # Highlight selects the item to view; checkboxes still select multiple
        # concepts to import. Viewing a page never changes those checkboxes.
        self.concept_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.concept_list.setWordWrap(False)
        self.concept_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.concept_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.concept_list.setMinimumHeight(45)
        self.concept_list.itemChanged.connect(self._concept_changed)
        self.concept_list.currentRowChanged.connect(self._update_original_button)
        concept_layout.addWidget(self.concept_list, 1)
        self.original_button = QPushButton("查看选中知识点的教材原页…")
        self.original_button.setObjectName("QuietButton")
        self.original_button.setAccessibleName(
            "查看当前高亮知识点对应的教材原页，不改变勾选"
        )
        self.original_button.setEnabled(False)
        self.original_button.clicked.connect(self._show_textbook_source)
        concept_layout.addWidget(self.original_button)
        self.concept_hint = QLabel("正在读取本地教材知识点…")
        self.concept_hint.setObjectName("MutedLabel")
        self.concept_hint.setWordWrap(True)
        self.concept_hint.setMaximumHeight(28)
        concept_layout.addWidget(self.concept_hint)
        splitter.addWidget(concept_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setMinimumHeight(120)

        self.body_scroll = QScrollArea()
        self.body_scroll.setObjectName("PreparationSourcesBodyScroll")
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        body_layout.addWidget(splitter)
        self.body_scroll.setWidget(body)
        root.addWidget(self.body_scroll, 1)

        self.preview_button = QPushButton("生成预览")
        self.preview_button.setObjectName("QuietButton")
        self.preview_button.setAccessibleName("生成 Word 与教材知识点参考全文预览")
        self.preview_button.clicked.connect(self._compile_preview)
        self.preview_button.setEnabled(False)
        root.addWidget(self.preview_button)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("点击“生成预览”后，全文会显示在这里供核对。")
        self.preview.setAccessibleName("备课参考全文预览")
        self.preview.setMinimumHeight(50)
        self.preview.setMaximumHeight(90)
        body_layout.addWidget(self.preview)

        self.status = QLabel("正在读取本地教材知识点…")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("备课参考导入状态")
        root.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("确认导入参考")
        self.import_button.setAccessibleName("确认导入备课参考")
        self.import_button.setEnabled(False)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消，不导入")
        cancel.setAccessibleName("取消导入备课参考")
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.block_start.valueChanged.connect(self._selection_changed)
        self.block_end.valueChanged.connect(self._selection_changed)
        self._load_concepts("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        narrow = self.width() < 600
        self.intro_label.setText(
            "选 Word 区块和教材知识点，先预览，确认前会复核；不会自动匹配或调用模型。"
            if narrow
            else "选择对应复习讲义的 Word 区块，并勾选教材知识点；先生成可核对的全文，"
            '确认时会再次核对所选资料。不自动匹配其他内容，也不调用模型。'
        )
        self.warning_label.setText(
            '候选/部分 Word 对象可能不完整，需教师核验；生成时才处理发送文字给模型。'
            if narrow
            else "提示：候选知识点和部分 Word 对象可能不是完整原文，导入后仍需教师核验；"
            '只有在生成备课初稿时，才会按原有确认流程处理发送文字给模型。'
        )
        # The two small helper lines are redundant in the narrow view.  Their
        # counts remain available in the main status line and their detailed
        # warnings remain in the compiled preview text.
        self.block_hint.setVisible(not narrow)
        self.concept_hint.setVisible(not narrow)
        if self._narrow_layout != narrow and self._splitter is not None:
            self._narrow_layout = narrow
            self._splitter.setOrientation(
                Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
            )
            if narrow:
                # Keep the Word heading, one-line locator, and both range
                # spinners in the first narrow viewport.  The concept panel
                # follows below and is reached with the body scroll bar.
                self._splitter.setMinimumHeight(275)
                self._splitter.setSizes([125, 150])
            else:
                self._splitter.setMinimumHeight(120)
                # QSplitter may apply each child widget's size hint after the
                # dialog resize.  Rebalance once the new geometry is laid out
                # so the Word and textbook columns remain equally usable.
                QTimer.singleShot(0, self._balance_wide_splitter)
        if self._word_heading is not None and self._concept_heading is not None:
            self._word_heading.setText(
                "Word 区块" if narrow else 'Word段落与表格'
            )
            self._concept_heading.setText(
                "教材知识点" if narrow else "教材知识点（可多选）"
            )

    def _balance_wide_splitter(self) -> None:
        if (
            self._splitter is None
            or self._splitter.orientation() != Qt.Orientation.Horizontal
        ):
            return
        available = self._splitter.width()
        if available <= 0:
            return
        left = available // 2
        self._splitter.setSizes([left, available - left])

    @property
    def selected_concepts(self) -> list[dict[str, str]]:
        return [
            {"concept_id": key, "revision": _text(value.get("revision"))}
            for key, value in self._selected_concepts.items()
        ]

    def _choose_word(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择 Word 讲义",
            "",
            "Word 文档 (*.docx)",
        )
        if not path:
            return
        self._load_word(path)

    def _load_word(self, path: str) -> None:
        self._loading_blocks = True
        self._invalidate_preview()
        self.block_list.clear()
        self.section_picker.clear()
        self.section_picker.addItem("按章节选范围（可手动调整）", None)
        self.section_picker.setEnabled(False)
        self._word_blocks = []
        self._word_path = None
        self._word_sha256 = None
        self.word_path.clear()
        try:
            preview = self.facade.preparation_word_preview(path)
            if not isinstance(preview, Mapping):
                raise TypeError("Word 预览返回格式无效")
            blocks = preview.get("blocks", ())
            if not isinstance(blocks, (list, tuple)):
                raise TypeError("Word 区块返回格式无效")
            cleaned: list[dict[str, Any]] = []
            for fallback, value in enumerate(blocks, 1):
                if not isinstance(value, Mapping):
                    continue
                try:
                    index = int(value.get("index", fallback))
                except (TypeError, ValueError):
                    index = fallback
                label = _text(value.get("label")) or f"区块 {index}"
                text = _text(value.get("text"))
                warnings = [
                    _text(item)
                    for item in (value.get("warnings", ()) or ())
                    if _text(item)
                ]
                cleaned.append(
                    {
                        "index": index,
                        "label": label,
                        "text": text,
                        "warnings": warnings,
                    }
                )
            cleaned.sort(key=lambda item: item["index"])
            self._word_blocks = cleaned
            valid_indices = {block["index"] for block in cleaned}
            for section in preview.get("sections", ()):
                if not isinstance(section, Mapping):
                    continue
                start, end = section.get("start"), section.get("end")
                if type(start) is not int or type(end) is not int:
                    continue
                if (
                    start not in valid_indices
                    or end not in valid_indices
                    or start > end
                ):
                    continue
                title = _text(section.get("title"))
                if not title:
                    continue
                label = f"{title} · 区块 {start}—{end}"
                self.section_picker.addItem(label, (start, end))
                self.section_picker.setItemData(
                    self.section_picker.count() - 1, label, Qt.ItemDataRole.ToolTipRole
                )
            self.section_picker.setEnabled(self.section_picker.count() > 1)
            self._word_path = str(Path(path).resolve())
            self._word_sha256 = _text(preview.get("source_sha256")) or None
            self.word_path.setText(Path(path).name)
            self.word_path.setToolTip(self._word_path)
            for block in cleaned:
                # The service label already contains the 1-based block index
                # and a short text excerpt.  Keep it verbatim so the list
                # does not repeat both pieces; the complete extracted text
                # remains available through the tooltip for locating review.
                label = block["label"]
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, block["index"])
                item.setToolTip(block["text"] or block["label"])
                self.block_list.addItem(item)
            if cleaned:
                maximum = max(int(item["index"]) for item in cleaned)
                self.block_start.setRange(1, maximum)
                self.block_end.setRange(1, maximum)
                self.block_start.setValue(int(cleaned[0]["index"]))
                self.block_end.setValue(int(cleaned[0]["index"]))
                self.block_list.setCurrentRow(0)
                self.block_hint.setText(
                    f"已读取 {len(cleaned)} 个区块；默认只选第 {cleaned[0]['index']} 个。"
                )
            else:
                self.block_start.setRange(0, 0)
                self.block_end.setRange(0, 0)
                self.block_hint.setText(
                    '没有找到可选的Word段落，请重新选择文件或改用教材知识点。'
                )
            warnings = [
                _text(item)
                for item in (preview.get("warnings", ()) or ())
                if _text(item)
            ]
            if warnings:
                self.block_hint.setText(
                    self.block_hint.text()
                    + f" 本文件有 {len(warnings)} 条区块警告；详情会保留在预览全文。"
                )
            set_status(
                self.status,
                "success",
                "Word 已读取；请选择区块和教材知识点后生成预览。",
            )
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            self.section_picker.clear()
            self.section_picker.addItem("按章节选范围（可手动调整）", None)
            self.section_picker.setEnabled(False)
            self._word_blocks = []
            self._word_path = None
            self._word_sha256 = None
            self.block_start.setRange(0, 0)
            self.block_end.setRange(0, 0)
            self.block_hint.setText("未选择 Word；可以只勾选教材知识点。")
            set_status(self.status, "error", _message(exc, "Word 预览失败，未导入。"))
        finally:
            self._loading_blocks = False
            self._update_actions()

    def _load_concepts(self, query: str) -> None:
        if self._loading_concepts:
            return
        self._loading_concepts = True
        try:
            options = self.facade.preparation_concept_options(query.strip())
            self.concept_list.blockSignals(True)
            self.concept_list.clear()
            count = 0
            for value in options or ():
                if not isinstance(value, Mapping):
                    continue
                concept_id = _text(value.get("concept_id"))
                if not concept_id:
                    continue
                title = _text(value.get("title")) or concept_id
                label = _text(value.get("label")) or title
                statement = _text(value.get("statement"))
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, concept_id)
                item.setData(Qt.ItemDataRole.UserRole + 1, dict(value))
                item.setToolTip(statement or title)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if concept_id in self._selected_concepts
                    else Qt.CheckState.Unchecked
                )
                self.concept_list.addItem(item)
                count += 1
            self.concept_list.blockSignals(False)
            self.concept_hint.setText(
                f"显示 {count} 项；已选择 {len(self._selected_concepts)} 项。"
                if count
                else "暂无匹配的教材知识点。"
            )
            if not count and not self._selected_concepts:
                set_status(
                    self.status, "attention", "暂无匹配的教材知识点；可更换关键词。"
                )
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            self.concept_list.clear()
            self.concept_hint.setText("教材知识点暂时无法读取。")
            set_status(
                self.status, "error", _message(exc, "教材知识点读取失败，未导入。")
            )
        finally:
            self._loading_concepts = False
            self._update_actions()

    def _concept_changed(self, item: QListWidgetItem) -> None:
        if self._loading_concepts:
            return
        concept_id = _text(item.data(Qt.ItemDataRole.UserRole))
        value = item.data(Qt.ItemDataRole.UserRole + 1)
        if not concept_id or not isinstance(value, Mapping):
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_concepts[concept_id] = dict(value)
        else:
            self._selected_concepts.pop(concept_id, None)
        self.concept_hint.setText(
            f"显示 {self.concept_list.count()} 项；已选择 {len(self._selected_concepts)} 项。"
        )
        self._selection_changed()

    def _update_original_button(self, *_args):
        self.original_button.setEnabled(
            self.concept_list.currentItem() is not None
            and callable(getattr(self.facade, "preparation_textbook_source", None))
        )

    def _show_textbook_source(self):
        item = self.concept_list.currentItem()
        if item is None or not self.original_button.isEnabled():
            return
        from .textbook_source_dialog import TextbookSourceDialog

        concept = item.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(concept, Mapping):
            return
        concept_id = concept["concept_id"]
        kwargs = {}
        if concept_id in self._textbook_excerpts:
            kwargs["excerpt"] = self._textbook_excerpts[concept_id]
        dialog = TextbookSourceDialog(self.facade, concept, self, **kwargs)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.excerpt is not None:
                self._textbook_excerpts[concept_id] = dict(dialog.excerpt)
                self._selected_concepts[concept_id] = dict(concept)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                self._textbook_excerpts.pop(concept_id, None)
            self._selection_changed()
            set_status(
                self.status,
                "attention",
                "摘录已更新，请重新生成预览并确认导入；原书和目录未修改。",
            )
        dialog.deleteLater()

    def _section_selected(self, index: int) -> None:
        bounds = self.section_picker.itemData(index)
        if not bounds or self._loading_blocks:
            return
        start, end = bounds
        self._loading_blocks = True
        try:
            self.block_start.setValue(start)
            self.block_end.setValue(end)
        finally:
            self._loading_blocks = False
        self._selection_changed()
        set_status(
            self.status,
            "success",
            f"已选区块 {start}—{end}；按Word标题层级定位，请预览核对内容与缺失公式。",
        )

    def _block_list_selected(self) -> None:
        if self._loading_blocks:
            return
        if self.block_end.value() > self.block_start.value():
            return
        self._select_current_block()

    def _select_current_block(self) -> None:
        item = self.block_list.currentItem()
        if item is None:
            return
        try:
            index = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        self._loading_blocks = True
        try:
            self.block_start.setValue(index)
            self.block_end.setValue(index)
        finally:
            self._loading_blocks = False
        self._selection_changed()

    def _selection_changed(self, *_args: object) -> None:
        if self._loading_blocks:
            return
        bounds = self.section_picker.currentData()
        if bounds and tuple(bounds) != (
            self.block_start.value(),
            self.block_end.value(),
        ):
            self.section_picker.setCurrentIndex(0)
        if self._word_blocks:
            self.block_hint.setText(
                f"已读取 {len(self._word_blocks)} 个区块；当前选择 {self.block_start.value()}—{self.block_end.value()}。"
            )
        if self.block_list.count():
            target = self.block_start.value()
            for row in range(self.block_list.count()):
                item = self.block_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == target:
                    self.block_list.blockSignals(True)
                    self.block_list.setCurrentRow(row)
                    self.block_list.blockSignals(False)
                    self.block_list.scrollToItem(
                        item, QAbstractItemView.ScrollHint.PositionAtTop
                    )
                    break
        self._invalidate_preview()
        self._update_actions()

    def _invalidate_preview(self) -> None:
        self.reference = None
        self._preview_key = None
        self.preview.clear()
        self.import_button.setEnabled(False)

    def _selection_key(self) -> tuple[Any, ...]:
        concepts = tuple(
            (key, _text(value.get("revision")))
            for key, value in self._selected_concepts.items()
        )
        return (
            self._word_path,
            self._word_sha256,
            self.block_start.value(),
            self.block_end.value(),
            concepts,
            json.dumps(self.selected_excerpts, ensure_ascii=False, sort_keys=True),
        )

    @property
    def selected_excerpts(self) -> list[dict[str, Any]]:
        return [
            dict(self._textbook_excerpts[key])
            for key in self._selected_concepts
            if key in self._textbook_excerpts
        ]

    def _has_word_selection(self) -> bool:
        return bool(
            self._word_path
            and self._word_sha256
            and self._word_blocks
            and self.block_start.value() > 0
            and self.block_end.value() >= self.block_start.value()
            and self.block_end.value()
            <= max(int(item["index"]) for item in self._word_blocks)
        )

    def _word_source_is_selected(self) -> bool:
        return bool(self._word_path or self._word_sha256 or self._word_blocks)

    def _word_range_is_invalid(self) -> bool:
        if not self._word_source_is_selected():
            return False
        if not self._word_path or not self._word_sha256 or not self._word_blocks:
            return True
        start, end = self.block_start.value(), self.block_end.value()
        maximum = max(int(item["index"]) for item in self._word_blocks)
        return not (1 <= start <= end <= maximum)

    def _update_actions(self) -> None:
        self.single_block_button.setEnabled(bool(self._word_blocks))
        self._update_original_button()
        self.preview_button.setEnabled(
            self._has_word_selection()
            or self._word_source_is_selected()
            or bool(self._selected_concepts)
        )
        self.import_button.setEnabled(self.reference is not None)

    def _compile_reference(self) -> dict[str, Any]:
        if self._word_range_is_invalid():
            raise PreparationSourcesDialogError(
                "已选择 Word，但区块范围无效；请修正起止区块后再预览。"
            )
        has_word = self._has_word_selection()
        block_start = self.block_start.value() if has_word else 0
        block_end = self.block_end.value() if has_word else 0
        kwargs = (
            {"textbook_excerpts": self.selected_excerpts}
            if self.selected_excerpts
            else {}
        )
        value = self.facade.preparation_source_reference(
            self._word_path if has_word else None,
            self._word_sha256 if has_word else None,
            block_start,
            block_end,
            self.selected_concepts,
            **kwargs,
        )
        if not isinstance(value, Mapping) or not _text(value.get("materials")):
            raise PreparationSourcesDialogError(
                "没有可导入的参考内容，请选择 Word 区块或教材知识点。"
            )
        return {
            "materials": _text(value.get("materials")),
            "warnings": [
                _text(item) for item in (value.get("warnings", ()) or ()) if _text(item)
            ],
        }

    def _compile_preview(self) -> None:
        if not self.preview_button.isEnabled():
            return
        self._invalidate_preview()
        try:
            reference = self._compile_reference()
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            set_status(self.status, "error", _message(exc, '资料整理失败，尚未添加。'))
            self._update_actions()
            return
        self.reference = reference
        self._preview_key = self._selection_key()
        self.preview.setPlainText(reference["materials"])
        suffix = ""
        if reference["warnings"]:
            suffix = (
                f" 包含 {len(reference['warnings'])} 条原文缺口/待核对警告；"
                "详细内容已保留在预览全文。"
            )
        set_status(
            self.status,
            "success",
            f"预览已生成，共 {len(reference['materials'])} 字。再次确认会重新编译并比较内容。{suffix}",
        )
        self._update_actions()

    def _confirm(self) -> None:
        if self.reference is None or self._preview_key != self._selection_key():
            set_status(self.status, "attention", "选择内容已变化，请先重新生成预览。")
            self._update_actions()
            return
        previous = self.reference
        try:
            current = self._compile_reference()
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            set_status(
                self.status, "error", _message(exc, '确认前重新核对资料失败，尚未添加。')
            )
            return
        if current != previous:
            self.reference = None
            self._preview_key = None
            self.preview.setPlainText(current["materials"])
            set_status(
                self.status,
                "attention",
                "确认前参考内容已变化，请重新核对全文后再次点击确认。",
            )
            self.import_button.setEnabled(False)
            return
        self.accept()


# A short alias keeps tests and future callers readable while retaining the
# explicit class name in the page import.
PreparationSourceDialog = PreparationSourcesDialog
