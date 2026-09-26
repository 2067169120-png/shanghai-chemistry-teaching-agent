"""Inline reading of imported question blocks with existing source-image readers."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget, QSizePolicy

from ..desktop_word_metafile_preview import can_attempt_metafile
from .components import page_scroll
from .library_detail import ImageZoomDialog


def text_label(text, name="ExplorerBody"):
    widget = QLabel(str(text))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    widget.setObjectName(name)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class SourceImage(QLabel):
    finished = Signal()

    def __init__(self, tasks, loader=None, parent=None):
        super().__init__("正在读取原图…", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._source = QPixmap()
        self.loaded, self.closed = False, False
        self.task = (tasks.submit("读取选题原图", loader, on_success=self.ready, on_failure=self.failed)
                     if loader is not None else None)
        self.tasks = tasks

    def ready(self, result):
        if self.closed:
            return
        data = result.get("bytes") if isinstance(result, dict) else result
        if not isinstance(data, bytes) or not self._source.loadFromData(data):
            self.failed("")
            return
        self.loaded = True
        self.setToolTip("点击放大原图")
        self.fit()
        self.finished.emit()

    def failed(self, _message):
        if not self.closed:
            self.setText("原图暂不能读取，请在原文与标签入口核对。")
            self.finished.emit()

    def fit(self):
        if not self._source.isNull():
            self.setPixmap(self._source.scaledToWidth(max(1, min(self.width(), self._source.width())),
                                                     Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        self.fit()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if self.loaded:
            dialog = ImageZoomDialog(self._source, "题目来源图", self.window())
            dialog.show()
        super().mousePressEvent(event)

    def cancel(self):
        self.closed = True
        if self.task:
            self.tasks.cancel(self.task)


class PersonalQuestionReader(QWidget):
    readiness_changed = Signal(bool)

    def __init__(self, entry, facade, tasks, parent=None):
        super().__init__(parent)
        self.entry, self.facade, self.tasks = entry, facade, tasks
        self.images, self.required = [], []
        self._batch_tasks = []
        self._closed = False
        self.load_metrics = []
        self.supported = bool(entry["payload"].get("selection_ready"))
        self.tabs = QTabWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.tabs)
        self.loaded_tabs = set()
        for title in ("题面与公共材料", "答案与解析"):
            self.tabs.addTab(QWidget(), title)
        self.tabs.currentChanged.connect(self.render)
        self.render(0)
        self.setMinimumHeight(360)

    @property
    def ready_to_select(self):
        return self.supported and all(image.loaded for image in self.required)

    def render(self, index):
        if index in self.loaded_tabs:
            return
        self.loaded_tabs.add(index)
        row = self.entry["payload"]
        body = QWidget()
        body.setObjectName("ExplorerReadingSheet")
        body.setStyleSheet(
            "QWidget#ExplorerReadingSheet {background: white;}"
            "QLabel#ExplorerBody {color: #243D34; font-size: 16px; background: white;}"
        )
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 12, 8, 16)
        layout.setSpacing(12)
        pending_word = []
        if self.entry["lane"] == "word_native":
            blocks = row.get("answer_blocks", []) if index else row.get("context_blocks", []) + row.get("question_blocks", [])
            for block in blocks:
                if not index and block in row.get("context_blocks", []):
                    layout.addWidget(text_label("公共材料", "CardTitle"))
                layout.addWidget(text_label(block.get("text", "")))
                assets = block.get("assets", [])
                for asset in assets:
                    supported = bool(asset.get("asset_id")) and (asset.get("preview_supported") is True or can_attempt_metafile(asset))
                    if not supported:
                        layout.addWidget(text_label("此公式/图形暂不能显示，请打开原文与标签核对。", "StatusAttention"))
                        if not index:
                            self.supported = False
                        continue
                    image = self.add_image(layout, None, required=not index)
                    pending_word.append((asset["asset_id"], image))
                for warning in block.get("warnings", []):
                    layout.addWidget(text_label(warning, "MutedLabel"))
                    if not index and not assets and any(word in warning for word in ("图片", "对象", "图形", "嵌入")):
                        self.supported = False
            if not blocks:
                layout.addWidget(text_label("未提取到对应答案。" if index else "题面范围待核对。"))
                if not index:
                    self.supported = False
        else:
            if not index:
                layout.addWidget(text_label("识别文字为候选；入篮时保留所属完整主题和公共材料。", "MutedLabel"))
                layout.addWidget(text_label(row.get("shared_text", "")))
            layout.addWidget(text_label(row.get("answer_text" if index else "question_text", "")))
            for image in row.get("images", []):
                is_answer = image.get("role") == "answer"
                if is_answer != bool(index):
                    continue
                loader = lambda im=image: self.facade.personal_visual_question_image(row["batch_id"], row["key"], row["revision"], im["image_id"])
                self.add_image(layout, loader, required=not index)
        layout.addStretch(1)
        container = self.tabs.widget(index)
        child = QVBoxLayout(container)
        child.setContentsMargins(0, 0, 0, 0)
        child.addWidget(page_scroll(body))
        if pending_word:
            self._read_word_images(row, pending_word)
        self.readiness_changed.emit(self.ready_to_select)

    def _read_word_images(self, row, pending):
        from ..desktop_word_image_batch import load_word_image_batch

        def update(value):
            if self._closed:
                return
            for asset_id, image in pending:
                if asset_id == value["asset_id"]:
                    image.failed("") if value.get("failed") else image.ready(value["result"])

        def complete(metrics):
            if not self._closed:
                self.load_metrics.append(metrics)
                self.readiness_changed.emit(self.ready_to_select)

        def failure(message):
            if not self._closed:
                for _, image in pending:
                    if not image.loaded:
                        image.failed(message)

        task = self.tasks.submit_progress("读取本题图片",
            lambda report, cancelled: load_word_image_batch(self.facade, row["key"], row["revision"],
                [asset_id for asset_id, _ in pending], progress=report, cancelled=cancelled),
            on_progress=update, on_success=complete, on_failure=failure)
        self._batch_tasks.append(task)

    def add_image(self, layout, loader, *, required):
        image = SourceImage(self.tasks, loader)
        self.images.append(image)
        if required:
            self.required.append(image)
        image.finished.connect(lambda: self.readiness_changed.emit(self.ready_to_select))
        layout.addWidget(image)
        return image

    def closeEvent(self, event):
        self._closed = True
        for task in self._batch_tasks:
            self.tasks.cancel(task)
        for image in self.images:
            image.cancel()
        super().closeEvent(event)
