from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_word_metafile_preview import can_attempt_metafile
from .components import page_scroll, section_title, set_status
from .word_lesson_reader import WordLessonReader


def _safe_message(exc: Exception, fallback: str) -> str:
    # Facade errors have teacher-facing messages. Do not surface raw exception
    # text, which may contain cached filenames, paths, or implementation data.
    message = getattr(exc, "message_zh", None)
    return message if isinstance(message, str) and message.strip() else fallback


def _warnings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError("invalid warnings")
    return [item for item in value if item]


class _WordImageDialog(QDialog):
    """Display the original pixels; zoom affects only the local viewer."""

    def __init__(self, pixmap: QPixmap, title: str, parent: QWidget, *, derived=False):
        super().__init__(parent)
        self._original = QPixmap(pixmap)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 360)
        self.resize(min(1000, max(400, parent.width())), 700)
        layout = QVBoxLayout(self)
        note = QLabel(
            f"{'本地转换预览' if derived else '原图'} {pixmap.width()} × {pixmap.height()} 像素；"
            "100% 按显示像素查看，可滚动查看。缩放不修改原文件。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("原图缩放比例")
        for label, scale in (
            ("适合宽度", None),
            ("50%", 0.5),
            ("100%（原尺寸）", 1.0),
            ("150%", 1.5),
            ("200%", 2.0),
        ):
            self.zoom.addItem(label, scale)
        self.zoom.setCurrentIndex(2)
        layout.addWidget(self.zoom)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.image = QLabel()
        self.image.setAccessibleName("完整原图")
        self.scroll.setWidget(self.image)
        layout.addWidget(self.scroll, 1)
        close_button = QPushButton("关闭原图")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.zoom.currentIndexChanged.connect(self._render)
        self._render()

    def _render(self, *_args: Any) -> None:
        scale = self.zoom.currentData()
        width = (
            max(1, self.scroll.viewport().width() - 4)
            if scale is None
            else max(1, round(self._original.width() * scale))
        )
        shown = self._original.scaledToWidth(
            width, Qt.TransformationMode.SmoothTransformation
        )
        self.image.setPixmap(shown)
        self.image.resize(shown.size())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.zoom.currentData() is None:
            self._render()


class ImportWordDialog(QDialog):
    """Inspect saved Word blocks and hand a freshly checked selection to preparation."""

    def __init__(self, facade: Any, batch_id: str, parent: QWidget | None = None, *, initial_source_id: str | None = None):
        super().__init__(parent)
        self.facade = facade
        self._batch_id = batch_id
        self._initial_source_id = initial_source_id
        self._image_reference_supported = callable(getattr(facade, "imported_word_image_reference", None))
        self.reference: dict[str, Any] | None = None
        self._preview_reference: dict[str, Any] | None = None
        self._preview_key: tuple | None = None
        self._source: dict[str, Any] | None = None
        self._loading = False
        self._image_pixmap: QPixmap | None = None
        self._image_is_derived = False
        self.setWindowTitle("完整教案原文与图片")
        self.resize(900, 820)
        self.setMinimumSize(400, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.addWidget(
            section_title(
                "完整教案原文与图片",
                "先通读原教案，再选择本课需要的段落。文字和原图均来自已保存的 Word，不调用模型。"
                "知识摘要不替代原文。",
            )
        )
        source_form = QFormLayout()
        source_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.source_combo = self._combo("选择已保存的 Word 来源")
        source_form.addRow("Word 来源", self.source_combo)
        root.addLayout(source_form)
        self.reader_tabs = QTabWidget()
        self.reader_tabs.setAccessibleName("原教案阅读与备课选段")
        self.reader = WordLessonReader()
        self.reader_tabs.addTab(self.reader, "通读教案")
        self.reader.image_requested.connect(self._reading_image_requested)
        self.reader.block_requested.connect(self._reading_block_requested)
        self.reader.image_zoom_requested.connect(self._open_image)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        selection_form = QFormLayout()
        selection_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.section_picker = self._combo("按 Word 章节选择区块")
        selection_form.addRow("章节定位", self.section_picker)
        layout.addLayout(selection_form)
        self.whole_document_button = QPushButton("查看整份教案")
        self.whole_document_button.setAccessibleName(
            "查看整份教案原文，不限备课追加字数"
        )
        self.whole_document_button.setEnabled(False)
        self.whole_document_button.clicked.connect(self._view_whole_document)
        layout.addWidget(self.whole_document_button)
        self.open_word_button = QPushButton("用 Word 打开完整原文件")
        self.open_word_button.setObjectName("QuietButton")
        self.open_word_button.setAccessibleName("用默认 Word 文档应用打开已保存原文件")
        self.open_word_button.setEnabled(False)
        self.open_word_button.clicked.connect(self._open_original_word)
        # Keep the original-file escape hatch visible in both reading modes.
        root.addWidget(self.open_word_button)
        self.block_list = QListWidget()
        self.block_list.setAccessibleName("Word 原文区块")
        self.block_list.setMinimumHeight(120)
        self.block_list.setMaximumHeight(170)
        self.block_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.block_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.block_list)
        range_layout = QHBoxLayout()
        self.block_start = QSpinBox()
        self.block_start.setAccessibleName("起始区块")
        self.block_end = QSpinBox()
        self.block_end.setAccessibleName("结束区块")
        for label, widget in (("从区块", self.block_start), ("至区块", self.block_end)):
            widget.setRange(0, 0)
            range_layout.addWidget(QLabel(label))
            range_layout.addWidget(widget, 1)
        layout.addLayout(range_layout)
        self.block_hint = QLabel("正在读取已保存的 Word…")
        self.block_hint.setWordWrap(True)
        layout.addWidget(self.block_hint)
        layout.addWidget(QLabel("教案原文（含表格文字；可滚动阅读全文）"))
        self.source_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True)
        self.source_preview.setAccessibleName("所选 Word 原文与缺失提示")
        self.source_preview.setMinimumHeight(150)
        self.source_preview.setMaximumHeight(220)
        layout.addWidget(self.source_preview)
        self.asset_combo = self._combo("查看 Word 内嵌来源图片")
        layout.addWidget(self.asset_combo)
        self.image_label = QLabel()
        self.image_label.setAccessibleName("Word 内嵌来源图片预览")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(1, 1)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.image_label.setFixedHeight(230)
        self.image_label.hide()
        layout.addWidget(self.image_label)
        self.zoom_image_button = QPushButton("放大查看原图（原尺寸 / 缩放）")
        self.zoom_image_button.setAccessibleName("放大查看当前教案原图")
        self.zoom_image_button.setEnabled(False)
        self.zoom_image_button.clicked.connect(self._open_image)
        layout.addWidget(self.zoom_image_button)
        self.image_note = QLabel(
            "这是原 Word 中的图片，不是重绘图。可放大核对；"
            + ("勾选带入图片后，所选区块的可用图片将与文字一起追加。" if self._image_reference_supported else "下方追加到备课的文字参考不会自动带入这些图片像素。")
        )
        self.image_note.setWordWrap(True)
        self.image_note.hide()
        layout.addWidget(self.image_note)
        self.include_images = QCheckBox("同时带入所选区块的原图（默认；无法处理时会提示）")
        self.include_images.setChecked(self._image_reference_supported)
        self.include_images.setVisible(self._image_reference_supported)
        self.include_images.toggled.connect(self._selection_changed)
        layout.addWidget(self.include_images)
        self.preview_button = QPushButton("预览将带入的内容")
        self.preview_button.clicked.connect(self._compile_preview)
        layout.addWidget(self.preview_button)
        self.preview_body_button = QPushButton("定位到所选正文")
        self.preview_body_button.setAccessibleName("定位到预览中的所选 Word 正文")
        self.preview_body_button.setObjectName("QuietButton")
        self.preview_body_button.setEnabled(False)
        self.preview_body_button.clicked.connect(self._locate_reference_body)
        layout.addWidget(self.preview_body_button)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("将追加到备课的完整参考预览")
        self.preview.setPlaceholderText("生成预览后，在这里核对将追加的完整内容。")
        self.preview.setMinimumHeight(170)
        layout.addWidget(self.preview)
        self.reader_tabs.addTab(page_scroll(content), "选段与备课")
        root.addWidget(self.reader_tabs, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Word 内容导入状态")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        self.import_button = QPushButton("确认追加到备课")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._confirm)
        buttons.addWidget(self.close_button)
        buttons.addStretch(1)
        buttons.addWidget(self.import_button)
        root.addLayout(buttons)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.section_picker.currentIndexChanged.connect(self._section_selected)
        self.block_list.currentItemChanged.connect(self._block_selected)
        self.block_start.valueChanged.connect(self._selection_changed)
        self.block_end.valueChanged.connect(self._selection_changed)
        self.asset_combo.currentIndexChanged.connect(self._asset_selected)
        # Enter in the reading search field means "find next", not opening
        # Word or accepting a preparation reference through QDialog defaults.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._load_sources()

    @staticmethod
    def _combo(accessible_name: str) -> QComboBox:
        combo = QComboBox()
        combo.setAccessibleName(accessible_name)
        combo.setMinimumContentsLength(12)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return combo

    def _load_sources(self) -> None:
        self._loading = True
        try:
            sources = self.facade.imported_word_sources(self._batch_id)
            if not isinstance(sources, (list, tuple)):
                raise TypeError("invalid sources")
            roles = {"question": "题目", "answer": "参考答案", "handout": "讲义"}
            for source in sources:
                if not isinstance(source, dict) or not source.get("source_id"):
                    continue
                name = source.get("source_name")
                if not isinstance(name, str) or not name.strip():
                    continue
                name = name.replace("\\", "/").rsplit("/", 1)[-1]
                role = roles.get(source.get("role"), "资料")
                self.source_combo.addItem(f"{name} · {role}", source["source_id"])
            if self._initial_source_id is not None:
                position = self.source_combo.findData(self._initial_source_id)
                if position < 0:
                    self.source_combo.clear()
                    raise ValueError("selected source disappeared")
                self.source_combo.setCurrentIndex(position)
            if not self.source_combo.count():
                set_status(
                    self.status,
                    "attention",
                    "本批没有可读取的 Word 来源，请重新保存资料后查看。",
                )
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            set_status(
                self.status,
                "error",
                _safe_message(exc, "已保存的 Word 来源暂时无法读取。"),
            )
        finally:
            self._loading = False
        if self.source_combo.count():
            self._source_changed()
        else:
            self._clear_source()
            self.block_hint.setText("没有可选 Word 来源。")

    def _clear_source(self) -> None:
        self._source = None
        self.reader.set_source(None)
        self._invalidate_preview()
        self.block_list.clear()
        self.section_picker.clear()
        self.section_picker.addItem("按章节定位，或手动选择区块", None)
        self.section_picker.setEnabled(False)
        self.block_start.setRange(0, 0)
        self.block_end.setRange(0, 0)
        self.source_preview.clear()
        self.asset_combo.clear()
        self.asset_combo.addItem("选择内嵌来源图片进行核对（可选）", None)
        self.asset_combo.hide()
        self._image_pixmap = None
        self.image_label.clear()
        self.image_label.hide()
        self.image_note.hide()
        self.zoom_image_button.setEnabled(False)
        self.whole_document_button.setEnabled(False)
        self.open_word_button.setEnabled(bool(self.source_combo.currentData()))
        self.preview_button.setEnabled(False)

    def _source_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._loading = True
        self._clear_source()
        try:
            value = self.facade.imported_word_preview(
                self._batch_id, self.source_combo.currentData()
            )
            if not isinstance(value, dict) or not all(
                isinstance(value.get(key), str) and value[key]
                for key in ("source_sha256", "revision")
            ):
                raise ValueError("invalid source preview")
            blocks = value.get("blocks")
            if not isinstance(blocks, (list, tuple)) or any(
                not isinstance(block, dict)
                or type(block.get("index")) is not int
                or block["index"] < 1
                or not isinstance(block.get("text"), str)
                for block in blocks
            ):
                raise ValueError("invalid blocks")
            indices = [block["index"] for block in blocks]
            if indices != sorted(set(indices)):
                raise ValueError("invalid block order")
            _warnings(value.get("warnings", ()))
            for block in blocks:
                _warnings(block.get("warnings", ()))
                item = QListWidgetItem(
                    str(block.get("label") or f"区块 {block['index']}")
                )
                item.setData(Qt.ItemDataRole.UserRole, block["index"])
                item.setToolTip(block["text"])
                self.block_list.addItem(item)
            self._source = deepcopy(value)
            self.reader.set_source(deepcopy(value))
            self.reader_tabs.setCurrentIndex(0)
            for section in value.get("sections", ()):
                if not isinstance(section, dict):
                    continue
                start, end, title = (
                    section.get("start"),
                    section.get("end"),
                    section.get("title"),
                )
                if (
                    type(start) is int
                    and type(end) is int
                    and start in indices
                    and end in indices
                    and start <= end
                    and isinstance(title, str)
                    and title
                ):
                    self.section_picker.addItem(
                        f"{title} · 区块 {start}—{end}", (start, end)
                    )
            self.section_picker.setEnabled(self.section_picker.count() > 1)
            for asset in value.get("assets", ()):
                if not isinstance(asset, dict) or not asset.get("asset_id"):
                    continue
                label = str(asset.get("label") or "内嵌来源图片")
                if can_attempt_metafile(asset):
                    label += "（本地转换预览）"
                elif not asset.get("preview_supported"):
                    label += "（暂不能预览）"
                self.asset_combo.addItem(label, deepcopy(asset))
            self.asset_combo.setVisible(self.asset_combo.count() > 1)
            if blocks:
                self.whole_document_button.setEnabled(True)
                self.block_start.setRange(min(indices), max(indices))
                self.block_end.setRange(min(indices), max(indices))
                self.block_start.setValue(indices[0])
                self.block_end.setValue(indices[0])
                self.block_list.setCurrentRow(0)
                set_status(
                    self.status,
                    "success",
                    "已打开整份教案。可搜索正文、点击本段原图对照；"
                    "点“选此段备课”后再预览并确认追加，不会自动带入全文。",
                )
            else:
                set_status(
                    self.status, "attention", "该 Word 没有可选文字区块，无法带入备课。"
                )
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            self._clear_source()
            set_status(
                self.status,
                "error",
                _safe_message(exc, "Word 内容暂时无法读取，请重新选择来源。"),
            )
        finally:
            self._loading = False
        self._selection_changed()

    def _selection_key(self) -> tuple:
        source = self._source or {}
        return (
            self.source_combo.currentData(),
            source.get("source_sha256"),
            source.get("revision"),
            self.block_start.value(),
            self.block_end.value(),
            self.include_images.isChecked(),
        )

    def _valid_range(self) -> bool:
        indices = {block["index"] for block in (self._source or {}).get("blocks", ())}
        start, end = self.block_start.value(), self.block_end.value()
        return start in indices and end in indices and start <= end

    def _invalidate_preview(self, *, clear: bool = True) -> None:
        self.reference = None
        self._preview_reference = None
        self._preview_key = None
        self.import_button.setEnabled(False)
        self.preview_body_button.setEnabled(False)
        if clear:
            self.preview.clear()

    def _selection_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        self._invalidate_preview()
        valid = self._valid_range()
        self.preview_button.setEnabled(valid)
        start, end = self.block_start.value(), self.block_end.value()
        blocks = (self._source or {}).get("blocks", ())
        self.block_hint.setText(
            f"共 {len(blocks)} 个区块；当前选择 {start}—{end}。"
            if blocks
            else "没有可选区块。"
        )
        bounds = self.section_picker.currentData()
        if bounds and tuple(bounds) != (start, end):
            self.section_picker.setCurrentIndex(0)
        selected = (
            [block for block in blocks if start <= block["index"] <= end]
            if valid
            else []
        )
        notes = list((self._source or {}).get("warnings", ()))
        text = []
        for block in selected:
            text.append(f"区块 {block['index']}\n{block['text']}")
            notes.extend(block.get("warnings", ()))
        if notes:
            text.append("原文缺口 / 待核对提醒：\n" + "\n".join(dict.fromkeys(notes)))
        self.source_preview.setPlainText("\n\n".join(text))

    def _block_selected(self, item: QListWidgetItem | None, *_args: Any) -> None:
        if self._loading or item is None:
            return
        self._set_range(
            item.data(Qt.ItemDataRole.UserRole), item.data(Qt.ItemDataRole.UserRole)
        )

    def _section_selected(self, *_args: Any) -> None:
        bounds = self.section_picker.currentData()
        if not self._loading and bounds:
            self._set_range(*bounds)

    def _set_range(self, start: int, end: int) -> None:
        self._loading = True
        try:
            self.block_start.setValue(start)
            self.block_end.setValue(end)
        finally:
            self._loading = False
        self._selection_changed()

    def _view_whole_document(self) -> None:
        blocks = (self._source or {}).get("blocks", ())
        if not blocks:
            return
        # This is a reader action, not reference compilation: never apply the
        # preparation size limit or shorten the body just to fit that limit.
        self.reader_tabs.setCurrentIndex(0)
        self.reader.scroll_to_block(blocks[0]["index"])
        self.reader.browser.setFocus()
        set_status(
            self.status,
            "success",
            f"正在查看整份教案，共 {len(blocks)} 个区块，正文未按备课字数限制截断。"
            "当前备课选段未改变；需要追加时请在“选段与备课”中调整范围并预览。",
        )

    def _reading_block_requested(self, index: int) -> None:
        if self._loading or type(index) is not int:
            return
        indices = {block["index"] for block in (self._source or {}).get("blocks", ())}
        if index not in indices:
            return
        self._set_range(index, index)
        for row in range(self.block_list.count()):
            if self.block_list.item(row).data(Qt.ItemDataRole.UserRole) == index:
                self.block_list.setCurrentRow(row)
                break
        self.reader_tabs.setCurrentIndex(1)
        self.source_preview.setFocus()
        set_status(
            self.status, "info",
            f"已选择区块 {index}，可调整起止范围，再预览将带入的文字和原图。尚未追加到备课。",
        )

    def _reading_image_requested(self, asset_id: str) -> None:
        if self._loading or not isinstance(asset_id, str) or not self._source:
            return
        for index in range(1, self.asset_combo.count()):
            asset = self.asset_combo.itemData(index)
            if isinstance(asset, dict) and asset.get("asset_id") == asset_id:
                indices = {block["index"] for block in self._source.get("blocks", ())}
                if type(asset.get("block_index")) is not int or asset["block_index"] not in indices:
                    return
                if self.asset_combo.currentIndex() == index:
                    # Revalidate a repeated click instead of trusting old pixels.
                    self._asset_selected()
                else:
                    self.asset_combo.setCurrentIndex(index)
                return

    def _open_original_word(self) -> None:
        source_id = self.source_combo.currentData()
        if not source_id:
            return
        try:
            value = self.facade.imported_word_path(self._batch_id, source_id)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("invalid local source")
            path = Path(value)
            if (
                not path.is_absolute()
                or path.suffix.lower() != ".docx"
                or not path.is_file()
            ):
                raise ValueError("invalid local source")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise ValueError("document application unavailable")
        except Exception as exc:  # noqa: BLE001 - verified local facade boundary
            set_status(
                self.status,
                "error",
                _safe_message(
                    exc,
                    "原文件暂时无法打开，请检查文件仍存在且已安装 Word 或兼容应用。",
                ),
            )
            return
        set_status(
            self.status, "success", "已请求使用默认 Word 文档应用打开完整原文件。"
        )

    def _compile_reference(self) -> dict[str, Any]:
        if not self._source or not self._valid_range():
            raise ValueError("invalid selection")
        source_id, sha256, revision, start, end, include_images = self._selection_key()
        if self._image_reference_supported:
            value = self.facade.imported_word_image_reference(
                self._batch_id, source_id, sha256, start, end,
                expected_revision=revision, include_images=include_images,
            )
        else:
            value = self.facade.imported_word_reference(
                self._batch_id, source_id, sha256, start, end, expected_revision=revision
            )
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("materials"), str)
            or not value["materials"].strip()
        ):
            raise ValueError("invalid reference")
        _warnings(value.get("warnings", ()))
        return deepcopy(value)

    def _compile_preview(self) -> None:
        self._invalidate_preview()
        try:
            reference = self._compile_reference()
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            set_status(
                self.status,
                "error",
                _safe_message(exc, "所选内容暂时无法形成参考，请重新核对来源和范围。"),
            )
            return
        self._preview_reference = reference
        self._preview_key = self._selection_key()
        self._show_reference(reference)
        issues = reference.get("image_issues", [])
        reference_issues = reference.get("reference_issues", [])
        self.import_button.setEnabled(not issues and not reference_issues)
        notes = _warnings(reference.get("warnings", ()))
        message = "请核对完整参考。确认时会再次核对来源及内容，然后追加到备课资料。"
        if notes:
            message += f" 含 {len(notes)} 条原文缺口/待核对提醒。"
        if issues:
            message += " 图片存在待处理项，不能完整追加；请缩小区块范围，或明确取消带图并重新预览。"
        if reference_issues:
            message += " 所选内容有待处理事项，暂不能追加；详见下方预览。"
        elif self._image_reference_supported:
            message += f" 本次带入 {len(reference['image_assets'])} 张图片。"
        set_status(self.status, "attention" if notes else "success", message)

    def _show_reference(self, reference: dict[str, Any]) -> None:
        materials = reference["materials"]
        missing = [
            warning
            for warning in _warnings(reference.get("warnings", ()))
            if warning not in materials
        ]
        if missing:
            materials += "\n\n原文缺口 / 待核对提醒：\n" + "\n".join(missing)
        if reference.get("image_issues"):
            materials += "\n\n图片待处理项：\n" + "\n".join(reference["image_issues"])
        if reference.get("reference_issues"):
            materials += "\n\n参考待处理项：\n" + "\n".join(reference["reference_issues"])
        self.preview.setPlainText(materials)
        self.preview_body_button.setEnabled(
            not self.preview.document().find(self._body_marker()).isNull()
        )

    def _body_marker(self) -> str:
        return f"[Word区块{self.block_start.value()}]"

    def _locate_reference_body(self) -> None:
        cursor = self.preview.document().find(self._body_marker())
        if cursor.isNull():
            self.preview_body_button.setEnabled(False)
            return
        cursor.setPosition(cursor.selectionStart())
        self.preview.setTextCursor(cursor)
        self.preview.verticalScrollBar().setValue(cursor.block().firstLineNumber())
        self.preview.setFocus()

    def _confirm(self) -> None:
        previous = self._preview_reference
        if previous is None or self._preview_key != self._selection_key():
            self._invalidate_preview(clear=False)
            set_status(self.status, "attention", "选择已变化，请重新生成预览后确认。")
            return
        try:
            current = self._compile_reference()
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            self._invalidate_preview(clear=False)
            set_status(
                self.status,
                "error",
                _safe_message(exc, "确认前核对失败，请重新读取来源并预览。"),
            )
            return
        if current != previous:
            self._invalidate_preview(clear=False)
            self._show_reference(current)
            set_status(
                self.status, "attention", "参考内容已变化，请重新生成预览并核对后确认。"
            )
            return
        if current.get("image_issues") or current.get("reference_issues"):
            self._invalidate_preview(clear=False)
            set_status(self.status, "attention", "所选参考尚未就绪，请先处理预览中的待核对项，再重新预览。")
            return
        self.reference = current
        self.accept()

    def _asset_selected(self, *_args: Any) -> None:
        self._image_pixmap = None
        self._image_is_derived = False
        self.image_label.clear()
        self.image_label.hide()
        self.image_note.hide()
        self.zoom_image_button.setEnabled(False)
        self.reader.clear_image()
        asset = self.asset_combo.currentData()
        if self._loading or not asset:
            return
        if not asset.get("preview_supported") and not can_attempt_metafile(asset):
            message = "此图片或旧公式格式暂不能预览，请用 Word 打开完整原文件核对。"
            self.reader.show_image(asset["asset_id"], None, message)
            set_status(
                self.status,
                "attention",
                "此图片或旧公式格式暂不能在窗口内预览，请点“用 Word 打开完整原文件”核对。",
            )
            return
        try:
            result = self.facade.imported_word_asset(
                self._batch_id, self.source_combo.currentData(), asset["asset_id"]
            )
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
                raise ValueError("invalid source image")
            expected_sha = asset.get("sha256")
            if isinstance(expected_sha, str):
                actual_sha = sha256(result["bytes"]).hexdigest()
                if result.get("derived_preview") is True:
                    if (
                        result.get("original_sha256") != expected_sha
                        or result.get("preview_sha256") != actual_sha
                    ):
                        raise ValueError("converted image does not match reading source")
                elif actual_sha != expected_sha:
                    raise ValueError("image does not match reading source")
            pixmap = QPixmap()
            if not pixmap.loadFromData(result["bytes"]) or pixmap.isNull():
                raise ValueError("unreadable source image")
            self._image_pixmap = pixmap
            self._image_is_derived = result.get("derived_preview") is True
            self.image_note.setText(
                ("由原 Word 矢量图在本机转换的预览，原件保留；不是公式文字识别结果。"
                 if self._image_is_derived else "这是原 Word 中的图片，可放大核对。")
                + (" 带图选项仅带入所选区块的图片；预览时会列明具体范围。"
                   if self._image_reference_supported else " 追加文字参考不会自动带入图片像素。")
            )
            self.image_label.show()
            self.image_note.show()
            self.zoom_image_button.setEnabled(True)
            self.reader.show_image(
                asset["asset_id"], self._image_pixmap, self.image_note.text(),
                derived=self._image_is_derived,
            )
            self._resize_image()
        except Exception as exc:  # noqa: BLE001 - local facade boundary
            message = _safe_message(exc, "来源图片暂时无法预览，请核对原始 Word。")
            self._image_pixmap = None
            self.image_label.clear()
            self.image_label.hide()
            self.image_note.hide()
            self.zoom_image_button.setEnabled(False)
            self.reader.show_image(asset["asset_id"], None, message)
            set_status(
                self.status,
                "error",
                message,
            )

    def _open_image(self) -> None:
        if self._image_pixmap is None:
            return
        asset = self.asset_combo.currentData() or {}
        dialog = _WordImageDialog(
            self._image_pixmap, str(asset.get("label") or "教案原图"), self,
            derived=self._image_is_derived,
        )
        dialog.exec()
        dialog.deleteLater()

    def _resize_image(self) -> None:
        if self._image_pixmap is not None:
            self.image_label.setPixmap(
                self._image_pixmap.scaled(
                    max(1, self.image_label.width()),
                    self.image_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_image()
