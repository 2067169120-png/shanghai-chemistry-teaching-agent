"""Complete, local-only reading of the native Word preview's text blocks."""

from __future__ import annotations

from .typography import ui_font

from copy import deepcopy
from html import escape
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPixmap,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..desktop_word_metafile_preview import can_attempt_metafile
from .word_table_layout import word_table_html


class _LocalDocument(QTextDocument):
    """Never fall back to Qt's filesystem/network resource resolution."""

    def __init__(self, parent: QWidget, images: dict[str, QPixmap]):
        super().__init__(parent)
        self._images = images

    def loadResource(self, resource_type: int, url: QUrl) -> Any:
        if resource_type == QTextDocument.ResourceType.ImageResource:
            return self._images.get(url.toString())
        return None


class _LocalBrowser(QTextBrowser):
    def loadResource(self, resource_type: int, url: QUrl) -> Any:
        # QTextBrowser and QTextDocument both have resource entry points.
        document = self.document()
        if isinstance(document, _LocalDocument):
            return document.loadResource(resource_type, url)
        return None


class WordLessonReader(QWidget):
    """Reading never changes the caller's preparation selection.

    Text is the native block text, not a reconstruction of Word layout. Asset
    actions use the explicit block_index relationship; images are supplied by
    the caller only after a teacher requests one.
    """

    image_requested = Signal(str)
    block_requested = Signal(int)
    image_zoom_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WordLessonReader")
        self.setMinimumWidth(0)
        self._source: dict[str, Any] | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._assets: dict[str, dict[str, Any]] = {}
        self._block_anchors: dict[int, str] = {}
        self._image_anchors: dict[str, str] = {}
        self._actions: dict[str, tuple[str, str | int | None]] = {}
        self._generation = 0
        self._action_serial = 0
        self._image_asset_id: str | None = None
        self._image_pixmap: QPixmap | None = None
        self._image_note = ""
        self._image_derived = False
        self._image_url: str | None = None
        self._matches: list[tuple[int, int]] = []
        self._match_index = -1
        self._table_grids: dict[int, str] = {}
        self._table_fallbacks: set[int] = set()
        self._show_table_grids = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.section_picker = QComboBox()
        self.section_picker.setAccessibleName("通读教案章节定位，不改变备课选区")
        self.section_picker.setMinimumContentsLength(10)
        self.section_picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.section_picker.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        root.addWidget(self.section_picker)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("在整份教案中查找…")
        self.search_edit.setAccessibleName("查找完整教案原文")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(0)
        search_row.addWidget(self.search_edit, 1)
        self.previous_button = QPushButton("上一处")
        self.next_button = QPushButton("下一处")
        for button in (self.previous_button, self.next_button):
            button.setAutoDefault(False)
            button.setMinimumWidth(0)
            search_row.addWidget(button)
        self.previous_button.setAccessibleName("查找上一处匹配")
        self.next_button.setAccessibleName("查找下一处匹配")
        root.addLayout(search_row)
        self.search_status = QLabel("阅读全文；查找不会缩短正文。")
        self.search_status.setTextFormat(Qt.TextFormat.PlainText)
        self.search_status.setWordWrap(True)
        self.search_status.setAccessibleName("全文查找匹配状态")
        root.addWidget(self.search_status)

        self.table_mode_button = QPushButton("表格：网格预览 · 切换逐格原文")
        self.table_mode_button.setAccessibleName("切换表格网格预览与逐格原文")
        self.table_mode_button.setAutoDefault(False)
        self.table_mode_button.setMinimumWidth(0)
        self.table_mode_button.clicked.connect(self._toggle_tables)
        root.addWidget(self.table_mode_button)

        self.browser = _LocalBrowser()
        self.browser.setAccessibleName("完整教案原文阅读区")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setReadOnly(True)
        self.browser.setMinimumSize(0, 180)
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.browser.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
        self._fit_document_width()
        self.browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._wrap_timer = QTimer(self)
        self._wrap_timer.setSingleShot(True)
        self._wrap_timer.timeout.connect(self._fit_document_width)
        self.browser.viewport().installEventFilter(self)
        root.addWidget(self.browser, 1)
        self.setStyleSheet(
            "QWidget#WordLessonReader { background: #f5f3ed; color: #263d34; }"
            "QWidget#WordLessonReader QTextBrowser { background: #fffdf6;"
            " border: 1px solid #d9dfd3; border-radius: 10px; padding: 4px;"
            " selection-background-color: #d8e7c9; selection-color: #183d2f; }"
            "QWidget#WordLessonReader QLineEdit,"
            " QWidget#WordLessonReader QComboBox { min-height: 28px;"
            " border: 1px solid #cbd7c9; border-radius: 6px; padding: 3px 8px;"
            " background: #fffef9; color: #263d34; }"
            "QWidget#WordLessonReader QPushButton { min-height: 28px;"
            " border: 1px solid #cad8cc; border-radius: 6px; padding: 3px 9px;"
            " color: #244e3d; background: #edf3e9; }"
            "QWidget#WordLessonReader QPushButton:disabled { color: #839086; }"
            "QWidget#WordLessonReader QLabel { color: #627066; font-size: 12px; }"
        )
        self.browser.anchorClicked.connect(self._activate_link)
        self.section_picker.currentIndexChanged.connect(self._section_selected)
        self.search_edit.textChanged.connect(self._search_changed)
        self.search_edit.returnPressed.connect(self.find_next)
        self.next_button.clicked.connect(self.find_next)
        self.previous_button.clicked.connect(self.find_previous)
        self.set_source(None)

    def set_source(self, value: dict | None) -> None:
        """Replace all reading state. Invalid block data fails closed."""
        self._generation += 1
        self._source = None
        self._blocks.clear()
        self._assets.clear()
        self._block_anchors.clear()
        self._image_anchors.clear()
        self._actions.clear()
        self._table_grids.clear()
        self._table_fallbacks.clear()
        self._show_table_grids = True
        self.table_mode_button.hide()
        self.table_mode_button.setText("表格：网格预览 · 切换逐格原文")
        self._reset_image()
        self.search_edit.clear()
        self.section_picker.blockSignals(True)
        self.section_picker.clear()
        self.section_picker.addItem("章节定位 · 保持当前备课选区", None)
        self.section_picker.blockSignals(False)
        invalid = False
        if value is not None:
            blocks = value.get("blocks") if isinstance(value, dict) else None
            invalid = not isinstance(blocks, (list, tuple))
            if not invalid:
                indices = []
                for block in blocks:
                    if (
                        not isinstance(block, dict)
                        or type(block.get("index")) is not int
                        or block["index"] < 1
                        or not isinstance(block.get("text"), str)
                    ):
                        invalid = True
                        break
                    indices.append(block["index"])
                invalid = invalid or indices != sorted(set(indices))
            if not invalid:
                self._source = deepcopy(value)
                self._blocks = {b["index"]: b for b in self._source["blocks"]}
                self._collect_assets()
                self._collect_sections()
        self.section_picker.setEnabled(self.section_picker.count() > 1)
        self._render()
        self.browser.verticalScrollBar().setValue(0)
        if invalid:
            raise ValueError("invalid native Word blocks")

    def set_table_previews(self, value: object) -> bool:
        """Attach verified source geometry without changing text or selection."""
        if (
            self._source is None or not isinstance(value, dict)
            or not isinstance(self._source.get("source_sha256"), str)
            or not self._source.get("source_sha256")
            or value.get("source_sha256") != self._source.get("source_sha256")
            or not isinstance(self._source.get("revision"), str)
            or not self._source.get("revision")
            or value.get("source_revision") != self._source.get("revision")
            or not isinstance(value.get("tables"), dict)
        ):
            return False
        grids, fallbacks = {}, set()
        for index, rows in value["tables"].items():
            if type(index) is not int or index not in self._blocks:
                return False
            grid = word_table_html(rows, self._blocks[index]["text"])
            if grid is None:
                fallbacks.add(index)
            else:
                grids[index] = grid
        self._table_grids = grids
        self._table_fallbacks = fallbacks
        self.table_mode_button.setVisible(bool(grids))
        self._render()
        return True

    def _toggle_tables(self) -> None:
        self._show_table_grids = not self._show_table_grids
        self.table_mode_button.setText(
            "表格：网格预览 · 切换逐格原文" if self._show_table_grids
            else "表格：逐格原文 · 切换网格预览"
        )
        self._render()

    def _collect_assets(self) -> None:
        values = self._source.get("assets", ())
        if not isinstance(values, (list, tuple)):
            return
        duplicate_ids = set()
        seen_ids = set()
        for asset in values:
            if not isinstance(asset, dict):
                continue
            asset_id, block_index = asset.get("asset_id"), asset.get("block_index")
            if not isinstance(asset_id, str) or not asset_id:
                continue
            if asset_id in seen_ids:
                duplicate_ids.add(asset_id)
            seen_ids.add(asset_id)
            if type(block_index) is int and block_index in self._blocks:
                self._assets[asset_id] = asset
        for asset_id in duplicate_ids:
            self._assets.pop(asset_id, None)

    def _collect_sections(self) -> None:
        sections = self._source.get("sections", ())
        if not isinstance(sections, (list, tuple)):
            return
        for section in sections:
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
                and start in self._blocks
                and end in self._blocks
                and start <= end
                and isinstance(title, str)
                and title
            ):
                self.section_picker.addItem(title, start)

    @staticmethod
    def _label(value: Any, fallback: str) -> str:
        return value if isinstance(value, str) and value else fallback

    @staticmethod
    def _warning_html(values: Any) -> str:
        if not isinstance(values, (list, tuple)):
            return ""
        return "".join(
            '<p class="warning">待核对 · ' + escape(value) + "</p>"
            for value in values
            if isinstance(value, str) and value
        )

    def _link(self, label: str, action: str, value: str | int | None) -> str:
        self._action_serial += 1
        url = f"lesson-action:/{self._generation}/{self._action_serial}"
        self._actions[url] = (action, value)
        return f'<a href="{url}">{escape(label)}</a>'

    def _render(self) -> None:
        scroll = self.browser.verticalScrollBar().value()
        previous_match = self._match_index
        self._actions.clear()
        self._block_anchors.clear()
        self._image_anchors.clear()
        images = {}
        self._image_url = None
        if self._image_pixmap is not None and self._image_asset_id in self._assets:
            self._image_url = f"lesson-memory:/{self._generation}/{self._action_serial}"
            images[self._image_url] = self._image_pixmap
        html = [
            (
                "<html><head><style>"
                "body { color:#26362d; font-size:12pt; }"
                "h1 { font-size:20pt; font-weight:600; color:#214b3b; margin:8px 0 18px; }"
                ".eyebrow { color:#557263; font-size:10pt; margin-top:12px; }"
                ".block-label { color:#526c5b; font-size:10pt; margin-top:26px; }"
                ".body { white-space:pre-wrap; line-height:155%; margin:10px 0 14px; }"
                ".warning { color:#805b34; font-size:10pt; margin:6px 0; }"
                ".asset { color:#5a6e60; font-size:10pt; margin:12px 0 6px; }"
                ".note { color:#607065; font-size:10pt; margin:7px 0 12px; }"
                "a { color:#236345; text-decoration:underline; }"
                "</style></head><body>"
            )
        ]
        if not self._blocks:
            html.append(
                '<p class="eyebrow">原文阅读</p><h1>在这里通读教案</h1>'
                '<p class="body">选择一份已保存的 Word 后，完整正文会出现在这里。</p>'
                '<p class="note">若原文尚未读出，可用 Word 打开原文件核对。</p>'
            )
        else:
            title = self._label(self._source.get("source_name"), "完整教案")
            warnings = self._source.get("warnings", ())
            source_warnings = (
                [item for item in warnings if isinstance(item, str) and item]
                if isinstance(warnings, (list, tuple))
                else []
            )
            warnings_at_end = len(source_warnings) > 4
            warning_summary = (
                '<p class="warning">来源提醒 '
                + str(len(source_warnings))
                + " 条 · "
                + self._link("查看完整提醒（文末）", "warnings", None)
                + "</p>"
                if warnings_at_end
                else self._warning_html(source_warnings)
            )
            html.extend(
                [
                    '<p class="eyebrow">原文阅读 · 完整正文</p>',
                    f"<h1>{escape(title)}</h1>",
                    (
                        '<p class="note">按原文区块顺序通读。选中“选此段备课”后，'
                        "再核对将要追加的内容。公式、表格版式和图片位置以原 Word 为准。</p>"
                    ),
                    warning_summary,
                ]
            )
            for index, block in self._blocks.items():
                anchor = f"lesson-block-{self._generation}-{index}"
                self._block_anchors[index] = anchor
                label = self._label(block.get("label"), f"区块 {index}")
                if index in self._table_grids:
                    label = f"{index} · 原文表格"
                body = '<p class="body">' + escape(block["text"]) + "</p>"
                if self._show_table_grids and index in self._table_grids:
                    body = (
                        '<p class="note">原表行列预览 · 保留明确的合并关系；'
                        "字体、列宽及表内图片位置请对照原 Word。</p>"
                        + self._table_grids[index]
                    )
                elif index in self._table_fallbacks:
                    body = (
                        '<p class="note">此表较宽或含复杂结构，保留逐格原文；'
                        "请打开原 Word 核对表格版面。</p>" + body
                    )
                html.extend(
                    [
                        f'<p class="block-label"><a name="{anchor}"></a>{escape(label)}</p>',
                        body,
                        self._warning_html(block.get("warnings")),
                        '<p class="note">'
                        + self._link("选此段备课", "block", index)
                        + "</p>",
                    ]
                )
                for asset_id, asset in self._assets.items():
                    if asset["block_index"] != index:
                        continue
                    label = self._label(asset.get("label"), "来源图片")
                    mime = self._label(asset.get("mime_type"), "")
                    if can_attempt_metafile(asset):
                        status = (
                            " · WMF，核验字体后尝试本地预览"
                            if mime.lower().strip() in {"image/wmf", "image/x-wmf"}
                            else " · EMF，可尝试本地转换预览"
                        )
                    elif mime.lower().strip() in {"image/wmf", "image/x-wmf"}:
                        status = " · WMF 旧式图形，需用 Word 核对"
                    elif not asset.get("preview_supported"):
                        status = " · 暂不支持预览，请用 Word 核对"
                    else:
                        status = " · 查看来源图片"
                    image_anchor = (
                        f"lesson-asset-{self._generation}-{len(self._image_anchors)}"
                    )
                    self._image_anchors[asset_id] = image_anchor
                    html.append(
                        f'<p class="asset"><a name="{image_anchor}"></a>'
                        + self._link(label, "image", asset_id)
                        + escape(status)
                        + "</p>"
                    )
                    if asset_id == self._image_asset_id:
                        if self._image_url is not None:
                            width, height = self._image_size()
                            html.append(
                                f'<p><img src="{self._image_url}" width="{width}" height="{height}" /></p>'
                            )
                            kind = "本地转换预览" if self._image_derived else "来源原图"
                            html.append(
                                '<p class="note">'
                                + escape(kind)
                                + " · "
                                + self._link("放大查看", "zoom", asset_id)
                                + "</p>"
                            )
                        if self._image_note:
                            html.append(
                                '<p class="note">' + escape(self._image_note) + "</p>"
                            )
            if warnings_at_end:
                html.append(
                    f'<p class="block-label"><a name="lesson-warnings-{self._generation}"></a>'
                    "来源提醒 · 完整记录</p>" + self._warning_html(source_warnings)
                )
        html.append("</body></html>")
        document = _LocalDocument(self.browser, images)
        document.setDefaultFont(ui_font(12))
        document.setDocumentMargin(20)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        self.browser.setDocument(document)
        self.browser.setHtml("".join(html))
        self._fit_document_width()
        self._search_changed(preserve_index=previous_match, reveal=False)
        self.browser.verticalScrollBar().setValue(scroll)

    def _activate_link(self, url: QUrl) -> None:
        action = self._actions.get(url.toString())
        if action is None:
            return
        kind, value = action
        if kind == "block" and value in self._blocks:
            self.block_requested.emit(value)
        elif kind == "image" and value in self._assets:
            self.image_requested.emit(value)
        elif kind == "warnings":
            self.browser.scrollToAnchor(f"lesson-warnings-{self._generation}")
        elif (
            kind == "zoom"
            and value == self._image_asset_id
            and self._image_pixmap is not None
        ):
            self.image_zoom_requested.emit()

    def _section_selected(self, *_args: Any) -> None:
        self.scroll_to_block(self.section_picker.currentData())

    def scroll_to_block(self, index: int) -> bool:
        if type(index) is not int or index not in self._block_anchors:
            return False
        self.browser.scrollToAnchor(self._block_anchors[index])
        return True

    def show_image(
        self, asset_id: str, pixmap: QPixmap | None, note: str, derived: bool = False
    ) -> bool:
        """Replace the sole image preview, including on a failed image load."""
        self._reset_image()
        if not isinstance(asset_id, str) or asset_id not in self._assets:
            self._render()
            return False
        self._image_asset_id = asset_id
        self._image_note = note if isinstance(note, str) else ""
        self._image_derived = bool(derived)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._image_pixmap = QPixmap(pixmap)
        self._render()
        self.browser.scrollToAnchor(self._image_anchors[asset_id])
        return True

    def _reset_image(self) -> None:
        self._image_asset_id = None
        self._image_pixmap = None
        self._image_note = ""
        self._image_derived = False
        self._image_url = None

    def clear_image(self) -> None:
        if self._image_asset_id is None:
            return
        self._reset_image()
        self._render()

    def _image_size(self) -> tuple[int, int]:
        width = min(
            self._image_pixmap.width(), max(1, self.browser.viewport().width() - 52)
        )
        height = max(
            1, round(self._image_pixmap.height() * width / self._image_pixmap.width())
        )
        return width, height

    def _fit_document_width(self) -> None:
        # Qt's hinted glyph bounds can exceed the line advance by one pixel
        # at a CJK/Latin boundary. Leave real drawing space outside the wrap
        # width, so the document itself fits without clipping or changing text.
        width = max(1, self.browser.viewport().width() - 2)
        if self.browser.lineWrapColumnOrWidth() != width:
            self.browser.setLineWrapColumnOrWidth(width)

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        viewport_resized = (
            watched is self.browser.viewport() and event.type() == QEvent.Type.Resize
        )
        if viewport_resized:
            self._fit_document_width()
            # Scrollbar appearance can reenter resize before Qt has finished
            # applying the outer width. Reconcile once the event completes.
            self._wrap_timer.start(0)
        if (
            viewport_resized
            and self._image_pixmap is not None
            and self._image_url is not None
        ):
            width, height = self._image_size()
            block = self.browser.document().begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        form = fragment.charFormat().toImageFormat()
                        if form.name() == self._image_url and (
                            form.width(),
                            form.height(),
                        ) != (width, height):
                            form.setWidth(width)
                            form.setHeight(height)
                            cursor = QTextCursor(self.browser.document())
                            cursor.setPosition(fragment.position())
                            cursor.setPosition(
                                fragment.position() + fragment.length(),
                                QTextCursor.MoveMode.KeepAnchor,
                            )
                            cursor.setCharFormat(form)
                    iterator += 1
                block = block.next()
        return super().eventFilter(watched, event)

    def _search_changed(
        self, *_args: Any, preserve_index: int = 0, reveal: bool = True
    ) -> None:
        self._matches = []
        query = self.search_edit.text()
        if query:
            cursor = QTextCursor(self.browser.document())
            while True:
                cursor = self.browser.document().find(query, cursor)
                if cursor.isNull():
                    break
                self._matches.append((cursor.selectionStart(), cursor.selectionEnd()))
        self._match_index = min(max(preserve_index, 0), len(self._matches) - 1)
        self._show_match(reveal=reveal)

    def _show_match(self, *, reveal: bool = True) -> None:
        count = len(self._matches)
        self.previous_button.setEnabled(count > 0)
        self.next_button.setEnabled(count > 0)
        if not self.search_edit.text():
            self.search_status.setText("阅读全文；查找不会缩短正文。")
        elif not count:
            self.search_status.setText("未找到匹配；完整正文仍在下方。")
        else:
            self.search_status.setText(
                f"第 {self._match_index + 1} / {count} 处匹配 · 搜索整份教案"
            )
        selections = []
        if count:
            start, end = self._matches[self._match_index]
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor("#f1df9a"))
            selection.format.setForeground(QColor("#273c30"))
            selections.append(selection)
            if reveal:
                self.browser.setTextCursor(cursor)
                self.browser.ensureCursorVisible()
        self.browser.setExtraSelections(selections)

    def find_next(self) -> None:
        if self._matches:
            self._match_index = (self._match_index + 1) % len(self._matches)
            self._show_match()

    def find_previous(self) -> None:
        if self._matches:
            self._match_index = (self._match_index - 1) % len(self._matches)
            self._show_match()
