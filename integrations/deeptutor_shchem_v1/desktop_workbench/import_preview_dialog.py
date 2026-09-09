"""Read-only file selection before personal import; no candidate/state writes."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, section_title, set_status


def _label(text=""):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


class ImportPreviewDialog(QDialog):
    """Preview real source contents and select whole files, never individual items."""

    def __init__(self, facade, tasks, preview, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.preview = deepcopy(preview)
        self.sources = {row["source_id"]: row for row in preview["sources"]}
        self.selected_source_ids = []
        self._viewed = set()
        self._current = None
        self._source_value = None
        self._epoch = 0
        self._closed = False
        self._source_busy = False
        self._jobs = set()
        self._assets = [[], [], []]
        self._image_epochs = [0, 0, 0]
        self._pixmaps = [None, None, None]
        self._pdf_buffer = None
        self._pdf_bytes = None
        self.setWindowTitle("导入前预览与文件选择")
        self.resize(1100, 800)
        self.setMinimumSize(400, 540)
        container = QWidget()
        root = QVBoxLayout(container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(container))
        root.addWidget(
            section_title(
                "先预览，再选择导入",
                "本页只在本机读取原文件，不保存到题库，也不调用模型。",
            )
        )
        root.addWidget(
            _label(
                "这里选择的是整份文件：所选解析版中的知识正文和全部题目候选都会保存。"
                "逐题勾选用于导入后的备课和练习，不表示这里只导入其中几题。"
            )
        )
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(0)
        left_layout = QVBoxLayout(left)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName("待导入来源文件，可勾选整份文件")
        self.file_list.setWordWrap(True)
        self.file_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.file_list.setMinimumSize(0, 120)
        left_layout.addWidget(self.file_list, 1)
        choose = QHBoxLayout()
        self.select_all = QPushButton("全选文件")
        self.select_none = QPushButton("全部取消")
        choose.addWidget(self.select_all)
        choose.addWidget(self.select_none)
        left_layout.addLayout(choose)
        self.splitter.addWidget(left)
        right = QWidget()
        right.setMinimumWidth(0)
        right_layout = QVBoxLayout(right)
        self.source_title = _label("请选择文件查看原文")
        self.source_title.setObjectName("CardTitle")
        right_layout.addWidget(self.source_title)
        self.source_note = _label()
        right_layout.addWidget(self.source_note)
        self.question_combo = QComboBox()
        self.question_combo.setAccessibleName(
            "浏览此 Word 的题目，仅浏览不代表逐题入库选择"
        )
        self.question_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.question_combo.setMinimumContentsLength(12)
        self.question_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        right_layout.addWidget(self.question_combo)
        self.tabs = QTabWidget()
        self.texts, self.asset_combos, self.image_labels = [], [], []
        self.zoom_buttons = []
        for title in ("原文（含知识正文）", "题面与公共材料", "答案与解析"):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setAccessibleName("导入前" + title)
            text.setMinimumHeight(140)
            layout.addWidget(text, 1)
            images = QComboBox()
            images.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            images.setMinimumContentsLength(8)
            images.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            images.setAccessibleName(title + "原图选择")
            layout.addWidget(images)
            picture = _label()
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            picture.setMinimumSize(0, 0)
            picture.setMaximumHeight(230)
            picture.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            picture.hide()
            layout.addWidget(picture)
            zoom = QPushButton("放大查看原图")
            zoom.setObjectName("QuietButton")
            zoom.hide()
            layout.addWidget(zoom)
            self.texts.append(text)
            self.asset_combos.append(images)
            self.image_labels.append(picture)
            index = len(self.texts) - 1
            self.zoom_buttons.append(zoom)
            zoom.clicked.connect(lambda _checked=False, i=index: self._open_image(i))
            images.currentIndexChanged.connect(
                lambda _row, i=index: self._load_asset(i)
            )
            self.tabs.addTab(panel, title)
        self.pdf_panel = QWidget()
        pdf_layout = QVBoxLayout(self.pdf_panel)
        pdf_navigation = QHBoxLayout()
        self.pdf_page = QSpinBox()
        self.pdf_page.setAccessibleName("PDF预览页码")
        self.pdf_count = _label()
        pdf_navigation.addWidget(_label("页码"))
        pdf_navigation.addWidget(self.pdf_page)
        pdf_navigation.addWidget(self.pdf_count)
        pdf_layout.addLayout(pdf_navigation)
        self.pdf_document = QPdfDocument(self)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.pdf_view.setMinimumHeight(200)
        pdf_layout.addWidget(self.pdf_view, 1)
        self.pdf_document.statusChanged.connect(self._pdf_status)
        self.pdf_page.valueChanged.connect(
            lambda page: self.pdf_view.pageNavigator().jump(page - 1, QPointF(), 0)
        )
        self.tabs.addTab(self.pdf_panel, "PDF 原页")
        self.tabs.setTabVisible(3, False)
        right_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(right)
        self.splitter.setSizes([330, 700])
        root.addWidget(self.splitter, 1)
        self.selection_note = _label()
        self.selection_note.setAccessibleName("所选文件与尚未查看数量")
        root.addWidget(self.selection_note)
        self.status = _label("请点击文件查看内容，再勾选需要导入的整份文件。")
        root.addWidget(self.status)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cancel_button = QPushButton("取消，不导入")
        self.confirm_button = QPushButton("确认导入所选 0 份文件")
        self.confirm_button.setObjectName("PrimaryButton")
        self.actions.addWidget(self.cancel_button)
        self.actions.addWidget(self.confirm_button)
        root.addLayout(self.actions)
        self.file_list.itemChanged.connect(self._update_selection)
        self.file_list.currentItemChanged.connect(self._open_source)
        self.select_all.clicked.connect(lambda: self._check_all(True))
        self.select_none.clicked.connect(lambda: self._check_all(False))
        self.question_combo.currentIndexChanged.connect(self._show_question)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self._confirm)
        roles = {"question": "题目", "answer": "答案", "handout": "讲义"}
        for source in preview["sources"]:
            item = QListWidgetItem(
                f"{roles[source['role']]} · {source['source_name']}\n尚未打开查看"
            )
            item.setData(Qt.ItemDataRole.UserRole, source["source_id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.file_list.addItem(item)
        self._update_selection()
        if preview.get("warnings"):
            set_status(self.status, "attention", "；".join(preview["warnings"]))
        if self.file_list.count():
            self.file_list.setCurrentRow(0)

    def _submit(self, label, operation, success, failure):
        holder = []

        def done(value, failed=False):
            if holder:
                self._jobs.discard(holder[0])
            if self._closed:
                return
            (failure if failed else success)(value)

        task = self.tasks.submit(
            label,
            operation,
            on_success=done,
            on_failure=lambda value: done(value, True),
        )
        holder.append(task)
        self._jobs.add(task)

    def _selection(self):
        return [
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
            if self.file_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _check_all(self, selected):
        for index in range(self.file_list.count()):
            self.file_list.item(index).setCheckState(
                Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
            )

    def _update_selection(self, *_args):
        selected = self._selection()
        unseen = sum(key not in self._viewed for key in selected)
        self.selection_note.setText(
            f"已选 {len(selected)} / {len(self.sources)} 份文件；所选中已打开 {len(selected) - unseen} 份，尚未打开 {unseen} 份。"
            "打开查看不等于全部题目已核验；确认后将归档整份文件及其全部候选。"
        )
        valid = bool(selected) and any(
            self.sources[key]["role"] != "answer" for key in selected
        )
        self.confirm_button.setText(f"确认导入所选 {len(selected)} 份文件")
        self.confirm_button.setEnabled(valid and not self._source_busy)
        if selected and not valid:
            set_status(
                self.status,
                "attention",
                "参考答案不能单独导入，请同时选择题目或讲义文件。",
            )

    def _clear_content(self):
        self._source_value = None
        self._pixmaps = [None, None, None]
        self.question_combo.blockSignals(True)
        self.question_combo.clear()
        self.question_combo.blockSignals(False)
        self.question_combo.hide()
        self.pdf_document.close()
        if self._pdf_buffer:
            self._pdf_buffer.close()
        self._pdf_buffer = None
        self._pdf_bytes = None
        for i in range(3):
            self.texts[i].clear()
            self.image_labels[i].clear()
            self.image_labels[i].hide()
            self.zoom_buttons[i].hide()
            self.asset_combos[i].blockSignals(True)
            self.asset_combos[i].clear()
            self.asset_combos[i].blockSignals(False)
            self.asset_combos[i].hide()
            self.tabs.setTabVisible(i, i == 0)
            self._image_epochs[i] += 1
        self.tabs.setTabVisible(3, False)
        self.tabs.setCurrentIndex(0)

    def _open_source(self, item, _previous=None):
        self._epoch += 1
        epoch = self._epoch
        self._clear_content()
        if item is None:
            return
        source_id = item.data(Qt.ItemDataRole.UserRole)
        self._current = source_id
        source = self.sources[source_id]
        self.source_title.setText(source["source_name"])
        self.source_note.setText("正在本机读取原文，尚未保存…")
        self._source_busy = True
        self._update_selection()

        def failed(_message):
            if epoch != self._epoch:
                return
            self._source_busy = False
            self._viewed.discard(source_id)
            item.setText(item.text().split("\n")[0] + "\n预览未完成（请核对原文件）")
            self._clear_content()
            self.source_note.setText(
                "本文件暂不能完整预览，未标为已查看。可取消勾选，或自行核对原文件后明确选择整份保存。"
            )
            self._update_selection()

        def ready(value):
            if epoch != self._epoch:
                return
            if (
                not isinstance(value, dict)
                or value.get("source_sha256") != source["source_sha256"]
                or value.get("kind") != source["kind"]
            ):
                failed(None)
                return
            self._source_busy = False
            self._source_value = value
            try:
                if value["kind"] == "docx":
                    self._show_word(value)
                elif value["kind"] == "image":
                    pixmap = QPixmap()
                    if not isinstance(
                        value.get("bytes"), bytes
                    ) or not pixmap.loadFromData(value["bytes"]):
                        raise ValueError("invalid image")
                    self._display_image(0, pixmap)
                    self._mark_viewed()
                else:
                    self.tabs.setTabVisible(0, False)
                    self.tabs.setTabVisible(3, True)
                    self.tabs.setCurrentIndex(3)
                    self._pdf_bytes = QByteArray(value["bytes"])
                    self._pdf_buffer = QBuffer(self._pdf_bytes, self)
                    self._pdf_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                    self.pdf_document.load(self._pdf_buffer)
                    self._pdf_status(self.pdf_document.status())
            except (KeyError, TypeError, ValueError, RuntimeError):
                failed(None)
                return
            self._update_selection()

        try:
            self._submit(
                "预览导入来源",
                lambda: self.facade.preview_import_source(
                    self.preview["preview_id"], self.preview["revision"], source_id
                ),
                ready,
                failed,
            )
        except RuntimeError:
            failed(None)

    def _mark_viewed(self):
        self._viewed.add(self._current)
        item = self.file_list.currentItem()
        if item:
            item.setText(item.text().split("\n")[0] + "\n已打开内容（非逐题核验）")
        self.source_note.setText("已打开本机内容，尚未导入。请核对后选择整份文件。")
        self._update_selection()

    def _show_word(self, value):
        preview = value["preview"]
        self.texts[0].setPlainText(
            "\n\n".join(
                f"区块 {block['index']}\n{block.get('text', '')}"
                + (
                    "\n待核对：" + "；".join(block["warnings"])
                    if block.get("warnings")
                    else ""
                )
                for block in preview["blocks"]
            )
        )
        self._set_assets(0, preview.get("assets", []))
        self.question_combo.blockSignals(True)
        for index, question in enumerate(value.get("questions", [])):
            self.question_combo.addItem(
                question.get("source_label")
                or question.get("title")
                or f"第 {index + 1} 题",
                index,
            )
        self.question_combo.blockSignals(False)
        self.question_combo.setVisible(self.question_combo.count() > 0)
        self.tabs.setTabVisible(1, self.question_combo.count() > 0)
        self.tabs.setTabVisible(2, self.question_combo.count() > 0)
        self._show_question()
        self._mark_viewed()
        warnings = value.get("warnings", [])
        self.source_note.setText(
            f"已打开知识原文；识别到 {self.question_combo.count()} 道候选。"
            "下拉框只用于浏览，确认会导入整份文件。"
            + ("\n待核对：" + "；".join(warnings) if warnings else "")
        )

    def _show_question(self, *_args):
        if not self._source_value or self.question_combo.currentIndex() < 0:
            return
        question = self._source_value.get("questions", [])[
            self.question_combo.currentIndex()
        ]
        for i, blocks in (
            (
                1,
                question.get("context_blocks", [])
                + question.get("question_blocks", []),
            ),
            (2, question.get("answer_blocks", [])),
        ):
            self.texts[i].setPlainText(
                "\n\n".join(block.get("text", "") for block in blocks)
                or "原文未提供此部分，请核对来源。"
            )
            self._set_assets(
                i, [asset for block in blocks for asset in block.get("assets", [])]
            )

    def _set_assets(self, index, values):
        self._image_epochs[index] += 1
        self._pixmaps[index] = None
        self.image_labels[index].clear()
        self.image_labels[index].hide()
        self.zoom_buttons[index].hide()
        combo = self.asset_combos[index]
        combo.blockSignals(True)
        combo.clear()
        self._assets[index] = values
        for asset in values:
            combo.addItem(asset.get("label") or "原文图片", asset)
        combo.blockSignals(False)
        combo.setVisible(bool(values))
        if self.tabs.currentIndex() == index:
            self._load_asset(index)

    def _tab_changed(self, index):
        if 0 <= index < 3:
            self._load_asset(index)

    def _load_asset(self, index):
        if self._closed:
            return
        self._image_epochs[index] += 1
        image_epoch, epoch = self._image_epochs[index], self._epoch
        self._pixmaps[index] = None
        self.zoom_buttons[index].hide()
        label = self.image_labels[index]
        label.clear()
        asset = self.asset_combos[index].currentData()
        if not asset:
            label.hide()
            return
        label.show()
        if asset.get("preview_supported") is not True:
            label.setText(
                "此原图或对象格式暂不能显示，请核对原 Word；不会默认为已核验。"
            )
            return
        label.setText("正在读取原图…")
        source_id = self._current

        def failed(_message):
            if epoch == self._epoch and image_epoch == self._image_epochs[index]:
                label.clear()
                label.setText("本幅原图未能预览，请核对原文件；未显示其他来源的旧图。")

        def ready(value):
            if epoch != self._epoch or image_epoch != self._image_epochs[index]:
                return
            pixmap = QPixmap()
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("bytes"), bytes)
                or not pixmap.loadFromData(value["bytes"])
            ):
                failed(None)
                return
            self._display_image(index, pixmap)

        try:
            self._submit(
                "预览导入原图",
                lambda: self.facade.preview_import_asset(
                    self.preview["preview_id"],
                    self.preview["revision"],
                    source_id,
                    asset["asset_id"],
                ),
                ready,
                failed,
            )
        except RuntimeError:
            failed(None)

    def _display_image(self, index, pixmap):
        self._pixmaps[index] = pixmap
        label = self.image_labels[index]
        label.clear()
        label.show()
        self.zoom_buttons[index].show()
        width = max(160, self.tabs.width() - 44)
        label.setPixmap(
            pixmap.scaled(
                width,
                230,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _open_image(self, index):
        pixmap = self._pixmaps[index]
        if pixmap is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导入前原图查看")
        dialog.resize(900, 720)
        dialog.setMinimumSize(360, 400)
        layout = QVBoxLayout(dialog)
        mode = QComboBox()
        mode.addItems(["适合窗口宽度", "100% 原始尺寸", "150%", "200%"])
        mode.setAccessibleName("原图缩放")
        layout.addWidget(mode)
        scroll = QScrollArea()
        label = QLabel()
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)

        def scale():
            factor = [
                max(1, scroll.viewport().width() - 8) / pixmap.width(),
                1,
                1.5,
                2,
            ][mode.currentIndex()]
            shown = pixmap.scaled(
                max(1, int(pixmap.width() * factor)),
                max(1, int(pixmap.height() * factor)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setPixmap(shown)
            label.setFixedSize(shown.size())

        mode.currentIndexChanged.connect(scale)
        button = QPushButton("返回文件预览")
        button.clicked.connect(dialog.accept)
        layout.addWidget(button)
        QTimer.singleShot(0, scale)
        dialog.exec()
        dialog.deleteLater()

    def _pdf_status(self, status):
        if (
            self._closed
            or not self._source_value
            or self._source_value.get("kind") != "pdf"
        ):
            return
        if status == QPdfDocument.Status.Ready:
            count = self.pdf_document.pageCount()
            self.pdf_page.setRange(1, max(1, count))
            self.pdf_count.setText(f"/ {count} 页，可滚动查看真实原页")
            self._mark_viewed()
        elif status == QPdfDocument.Status.Error:
            self._viewed.discard(self._current)
            self.source_note.setText(
                "PDF原页暂不能显示，未标记为已查看；请核对原文件。"
            )
            self._update_selection()

    def _confirm(self):
        self._update_selection()
        if not self.confirm_button.isEnabled():
            return
        self.selected_source_ids = self._selection()
        self._finish(QDialog.DialogCode.Accepted)

    def _finish(self, result):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        for task in tuple(self._jobs):
            self.tasks.cancel(task)
        self.pdf_document.close()
        self.done(result)

    def reject(self):
        self.selected_source_ids = []
        self._finish(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.accept()

    def resizeEvent(self, event):
        narrow = event.size().width() < 760
        self.splitter.setOrientation(
            Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        )
        self.actions.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        for index, pixmap in enumerate(self._pixmaps):
            if pixmap is not None:
                self._display_image(index, pixmap)
        super().resizeEvent(event)
