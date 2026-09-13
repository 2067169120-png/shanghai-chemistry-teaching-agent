from __future__ import annotations

"""Native, read-only theme reader for the teacher question library."""

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..answer_diagrams import answer_diagram_png
from ..desktop_library import LibraryImage, LibraryPartDetail, LibraryThemeDetail
from ..supplemental_answers import ANSWER_LABEL, SOURCE_LABEL
from .components import CardFrame, page_scroll, set_status
from .tasks import DesktopTaskBridge


def _text_label(text: str, object_name: str = "StatusInfo") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class ImageZoomDialog(QDialog):
    """Non-modal local-image viewer with scroll and explicit zoom."""

    def __init__(
        self, pixmap: QPixmap, caption: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LibraryImageZoomDialog")
        self.setWindowTitle(caption or "题图大图")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(960, 760)
        self._source = QPixmap(pixmap)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        toolbar = QVBoxLayout()
        toolbar.addWidget(_text_label(caption or "题图", "CardTitle"))
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(_text_label("缩放", "MutedLabel"))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setObjectName("LibraryImageZoomSlider")
        self.zoom.setRange(25, 250)
        self.zoom.setValue(100)
        self.zoom.setMinimumWidth(0)
        self.zoom.setMaximumWidth(180)
        self.zoom.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.zoom.setAccessibleName("题图缩放")
        self.zoom.valueChanged.connect(self._zoom_changed)
        zoom_row.addWidget(self.zoom, 1)
        self.zoom_value = _text_label("100%", "MutedLabel")
        self.zoom_value.setObjectName("LibraryImageZoomValue")
        self.zoom_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_value.setMinimumWidth(44)
        self.zoom_value.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        zoom_row.addWidget(self.zoom_value)
        reset = QPushButton("重置")
        reset.setObjectName("QuietButton")
        reset.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        reset.setMaximumWidth(88)
        reset.clicked.connect(lambda: self.zoom.setValue(100))
        zoom_row.addWidget(reset)
        zoom_row.addStretch(1)
        toolbar.addLayout(zoom_row)
        root.addLayout(toolbar)
        self.image = QLabel()
        self.image.setObjectName("LibraryZoomImage")
        self.image.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self.image.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("LibraryZoomScroll")
        self.scroll.setWidget(self.image)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The workbench hides horizontal bars on ordinary pages.  A zoomed
        # source image is the one place where horizontal panning is deliberate.
        self.scroll.horizontalScrollBar().setStyleSheet(
            "QScrollBar:horizontal { height: 10px; }"
        )
        root.addWidget(self.scroll, 1)
        self._zoom_changed(100)

    def _zoom_changed(self, percent: int) -> None:
        self.zoom_value.setText(f"{percent}%")
        self._apply_zoom(percent)

    def _apply_zoom(self, percent: int) -> None:
        width = max(1, round(self._source.width() * percent / 100))
        pixmap = self._source.scaledToWidth(
            width, Qt.TransformationMode.SmoothTransformation
        )
        self.image.setPixmap(pixmap)
        self.image.resize(pixmap.size())


class FitWidthImage(QLabel):
    """Loads verified bytes asynchronously and fits the reading column."""

    load_state_changed = Signal()

    def __init__(
        self,
        image: LibraryImage,
        tasks: DesktopTaskBridge,
        image_loader: Callable[[LibraryImage], bytes],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.descriptor = image
        self._tasks = tasks
        self._source = QPixmap()
        self.load_state = "loading"
        self.loaded_bytes: bytes | None = None
        self._task_id: str | None = None
        self._generation = 1
        self._zoom_dialog: ImageZoomDialog | None = None
        self.setObjectName("LibraryFitWidthImage")
        self.setAccessibleName(image.caption_zh)
        self.setAccessibleDescription("按回车键、空格键或点击可查看大图并缩放")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_status(self, "info", f"正在读取{image.caption_zh}…")
        generation = self._generation
        self._task_id = tasks.submit(
            f"读取{image.caption_zh}",
            lambda: image_loader(image),
            on_success=lambda value: self._loaded(generation, value),
            on_failure=lambda message: self._failed(generation, message),
        )

    @property
    def has_image(self) -> bool:
        return not self._source.isNull()

    def _loaded(self, generation: int, value: object) -> None:
        if generation != self._generation:
            return
        self._task_id = None
        pixmap = QPixmap()
        if not isinstance(value, bytes) or not pixmap.loadFromData(value):
            self._failed(generation, "题图文件无法解码")
            return
        self._source = pixmap
        self.loaded_bytes = value
        self.load_state = "ready"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击查看大图并缩放")
        self._fit()
        self.load_state_changed.emit()

    def _failed(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self._task_id = None
        self._source = QPixmap()
        self.loaded_bytes = None
        self.load_state = "failed"
        self.setMinimumHeight(72)
        self.setMaximumHeight(16777215)
        set_status(
            self,
            "attention",
            f"{self.descriptor.caption_zh}暂时无法显示：{message}。请以来源记录为准。",
        )
        self.load_state_changed.emit()

    def _fit(self) -> None:
        if self._source.isNull():
            return
        target = min(max(1, self.contentsRect().width()), self._source.width())
        pixmap = self._source.scaledToWidth(
            target, Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(pixmap)
        self.setMinimumHeight(pixmap.height())
        self.setMaximumHeight(pixmap.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._source.isNull():
            self._open_zoom()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key()
            in {
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Space,
            }
            and not self._source.isNull()
        ):
            self._open_zoom()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_zoom(self) -> None:
        self._zoom_dialog = ImageZoomDialog(
            self._source, self.descriptor.caption_zh, self.window()
        )
        self._zoom_dialog.destroyed.connect(lambda: setattr(self, "_zoom_dialog", None))
        self._zoom_dialog.show()

    def cancel(self) -> None:
        self._generation += 1
        if self._task_id:
            cancel = getattr(self._tasks, "cancel", None)
            if callable(cancel):
                cancel(self._task_id)
        self._task_id = None
        if self.load_state == "loading":
            self.load_state = "cancelled"
            self.load_state_changed.emit()


class LibraryDetailDialog(QDialog):
    """Theme-first reader: question, answer/analysis, and source stay distinct."""

    preparation_image_requested = Signal(dict)
    # This certifies readable question/material bytes, not answer correctness.
    preview_readiness_changed = Signal(bool, str)

    def __init__(
        self,
        detail: LibraryThemeDetail,
        tasks: DesktopTaskBridge,
        image_loader: Callable[[LibraryImage], bytes],
        parent: QWidget | None = None,
        *,
        answer_image_loader: Callable[[LibraryImage], bytes] | None = None,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self.detail = detail
        self._image_widgets: list[FitWidthImage] = []
        self._question_image_widgets: list[FitWidthImage] = []
        self.preview_readiness = (False, "正在读取题面与公共材料。")
        self._preparation_buttons: list[QPushButton] = []
        self.setObjectName("LibraryDetailDialog")
        self.setWindowTitle(f"查看大题 · {detail.title_zh}")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, not embedded)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.resize(940, 780)
        self.setMinimumSize(360, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        heading = _text_label(detail.title_zh, "PageTitle")
        heading.setAccessibleName("大题标题")
        root.addWidget(heading)
        root.addWidget(
            _text_label(
                f"{detail.paper_title_zh} · {detail.page_zh} · "
                f"{len(detail.parts)} 个作答单元",
                "PageSubtitle",
            )
        )
        self.tabs = QTabWidget()
        self.tabs.setObjectName("LibraryDetailTabs")
        self.tabs.setAccessibleName("大题题面答案与来源")
        self.tabs.addTab(self._question_tab(detail, tasks, image_loader), "题面")
        self.tabs.addTab(self._answer_tab(detail, tasks, answer_image_loader), "答案与分析")
        self.tabs.addTab(self._source_tab(detail), "来源")
        root.addWidget(self.tabs, 1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("QuietButton")
        close_button.setAccessibleName("关闭大题详情")
        close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        root.addLayout(buttons)
        if embedded:
            heading.hide()
            close_button.hide()
            self.setMinimumSize(0, 360)
            root.setContentsMargins(0, 0, 0, 0)
        self._update_preview_readiness()

    def _update_preview_readiness(self) -> None:
        if not self.detail.parts:
            state = (False, "当前大题没有可读取的作答单元，不能加入题篮。")
        elif any(
            not part.question_images and not part.question_text_zh.strip()
            for part in self.detail.parts
        ):
            state = (False, "部分题面缺少原图或原文；摘要不能代替原题，暂不能加入题篮。")
        elif any(
            widget.load_state in {"failed", "cancelled"}
            for widget in self._question_image_widgets
        ):
            state = (False, "题面或公共材料图片读取失败，请重新打开预览后再加入题篮。")
        elif any(not widget.has_image for widget in self._question_image_widgets):
            state = (False, "正在读取题面与公共材料图片，加载完成前不能加入题篮。")
        else:
            state = (True, "题面与公共材料已可读取，可加入题篮；不代表参考答案已核验。")
        if state != self.preview_readiness:
            self.preview_readiness = state
            self.preview_readiness_changed.emit(*state)

    def _image(
        self,
        descriptor: LibraryImage,
        tasks: DesktopTaskBridge,
        image_loader: Callable[[LibraryImage], bytes],
        context_label: str = "共同材料",
    ) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        widget = FitWidthImage(descriptor, tasks, image_loader)
        self._image_widgets.append(widget)
        self._question_image_widgets.append(widget)
        widget.load_state_changed.connect(self._update_preview_readiness)
        layout.addWidget(widget)
        button = QPushButton("将这张原图用于备课…")
        button.setObjectName("QuietButton")
        button.setAccessibleName(f"将{context_label}的{descriptor.caption_zh}加入备课")
        button.clicked.connect(lambda: self._prepare_image(widget, context_label))
        layout.addWidget(button)
        self._preparation_buttons.append(button)
        return container

    def _prepare_image(self, widget: FitWidthImage, context_label: str) -> None:
        from .preparation_images_widget import PreparationImageMetadataDialog

        if not widget.has_image:
            QMessageBox.information(
                self,
                "请先核对原图",
                "这张原图尚未成功显示，请等待加载完成或重新打开题目后再添加。",
            )
            return
        image = widget.descriptor
        labels = [self.detail.title_zh]
        if context_label not in image.caption_zh:
            labels.append(context_label)
        labels.append(image.caption_zh)
        caption = " · ".join(labels)
        source = f"{self.detail.paper_title_zh} · {image.caption_zh} · {self.detail.source_zh}"
        dialog = PreparationImageMetadataDialog(
            "",
            self,
            display_name=caption,
            caption=caption,
            source=source,
            purpose="观察原图，记录已知条件、符号与作答要求，再结合教材知识讲解。",
            preview_bytes=widget.loaded_bytes,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.preparation_image_requested.emit(
            {
                "image": image,
                "caption": dialog.caption,
                "source": dialog.source,
                "purpose": dialog.purpose,
            }
        )
        self.close()

    def _question_tab(
        self,
        detail: LibraryThemeDetail,
        tasks: DesktopTaskBridge,
        image_loader: Callable[[LibraryImage], bytes],
    ) -> QScrollArea:
        content = QWidget()
        content.setObjectName("LibraryQuestionContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 8, 4, 16)
        layout.setSpacing(12)
        context = CardFrame()
        context.setAccessibleName("主题共同材料卡片")
        context.setProperty("semanticRole", "LibrarySharedContextCard")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(16, 14, 16, 14)
        context_layout.setSpacing(8)
        context_layout.addWidget(_text_label("主题共同材料", "CardTitle"))
        context_layout.addWidget(_text_label(detail.context_zh))
        if detail.shared_images:
            for image in detail.shared_images:
                context_layout.addWidget(self._image(image, tasks, image_loader))
        else:
            context_layout.addWidget(
                _text_label(
                    "未找到可核验的共同材料题图；请结合下方题面与来源记录阅读。",
                    "StatusAttention",
                )
            )
        layout.addWidget(context)
        if not detail.parts:
            layout.addWidget(
                _text_label(
                    "当前主题没有可展示的最小作答单元；系统不会据此补造题目。",
                    "StatusAttention",
                )
            )
        for part in detail.parts:
            layout.addWidget(self._question_part(part, tasks, image_loader))
        layout.addStretch(1)
        return page_scroll(content)

    def _question_part(
        self,
        part: LibraryPartDetail,
        tasks: DesktopTaskBridge,
        image_loader: Callable[[LibraryImage], bytes],
    ) -> CardFrame:
        card = CardFrame()
        card.setAccessibleName(f"作答单元卡片 {part.label_zh}")
        card.setProperty("semanticRole", "LibraryAtomicPartCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = _text_label(part.label_zh, "CardTitle")
        title.setAccessibleName(f"作答单元 {part.label_zh}")
        layout.addWidget(title)
        if part.question_text_zh.strip():
            original_text = _text_label(part.question_text_zh, "LibraryOriginalQuestionText")
            original_text.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(original_text)
        else:
            layout.addWidget(_text_label(part.summary_zh))
        layout.addWidget(_text_label(f"作答要求：{part.requirement_zh}"))
        layout.addWidget(_text_label(f"材料依赖：{part.dependency_zh}", "MutedLabel"))
        if part.availability_zh:
            layout.addWidget(_text_label(part.availability_zh, "StatusAttention"))
        if part.question_images:
            for image in part.question_images:
                layout.addWidget(self._image(image, tasks, image_loader, part.label_zh))
        elif not part.question_text_zh.strip():
            layout.addWidget(
                _text_label(
                    "本作答单元没有可核验的题面裁图；摘要不是原题替代品。",
                    "StatusAttention",
                )
            )
        if part.classification_zh:
            layout.addWidget(
                _text_label(" · ".join(part.classification_zh), "MutedLabel")
            )
        return card

    def _answer_tab(
        self,
        detail: LibraryThemeDetail,
        tasks: DesktopTaskBridge,
        answer_image_loader: Callable[[LibraryImage], bytes] | None,
    ) -> QScrollArea:
        content = QWidget()
        content.setObjectName("LibraryAnswerContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 8, 4, 16)
        layout.setSpacing(12)
        layout.addWidget(
            _text_label(
                "答案与解路按作答单元分别展示。参考答案、模型候选分析与"
                "官方评分细则不是同一证据层。"
            )
        )
        for part in detail.parts:
            card = CardFrame()
            card.setAccessibleName(f"答案与分析卡片 {part.label_zh}")
            card.setProperty("semanticRole", "LibraryAnswerPartCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(8)
            card_layout.addWidget(_text_label(part.label_zh, "CardTitle"))
            card_layout.addWidget(
                _text_label(part.answer_boundary_zh, "StatusAttention")
            )
            if part.reference_answer_zh:
                card_layout.addWidget(_text_label("来源参考答案", "MutedLabel"))
                answer = _text_label(part.reference_answer_zh)
                answer.setObjectName("LibraryReferenceAnswer")
                card_layout.addWidget(answer)
            elif not part.supplemental_answer_zh:
                card_layout.addWidget(
                    _text_label("本单元不展示参考答案正文。", "MutedLabel")
                )
            if part.answer_images:
                card_layout.addWidget(self._answer_image_preview(part, tasks, answer_image_loader))
            if part.supplemental_answer_zh:
                card_layout.addWidget(_text_label(ANSWER_LABEL, "CardTitle"))
                answer = _text_label(part.supplemental_answer_zh)
                answer.setObjectName("LibrarySupplementalAnswer")
                card_layout.addWidget(answer)
                if part.supplemental_diagram_key:
                    diagram = QLabel()
                    diagram.setObjectName("LibrarySupplementalAnswerDiagram")
                    diagram.setAccessibleName(part.supplemental_answer_zh)
                    pixmap = QPixmap()
                    pixmap.loadFromData(answer_diagram_png(part.supplemental_diagram_key))
                    diagram.setPixmap(pixmap.scaledToWidth(360, Qt.TransformationMode.SmoothTransformation))
                    card_layout.addWidget(diagram)
                card_layout.addWidget(_text_label("解题过程", "MutedLabel"))
                card_layout.addWidget(_text_label(part.supplemental_explanation_zh))
                card_layout.addWidget(_text_label(SOURCE_LABEL, "MutedLabel"))
            elif part.analysis_zh:
                card_layout.addWidget(_text_label("模型候选解路", "MutedLabel"))
                card_layout.addWidget(_text_label("\n".join(part.analysis_zh)))
            else:
                card_layout.addWidget(_text_label("暂无模型候选解路。", "MutedLabel"))
            if part.quality_notes_zh:
                card_layout.addWidget(_text_label("质量与使用边界", "MutedLabel"))
                card_layout.addWidget(_text_label("\n".join(part.quality_notes_zh)))
            layout.addWidget(card)
        if not detail.parts:
            layout.addWidget(
                _text_label("当前主题没有可展示的答案单元。", "StatusAttention")
            )
        layout.addStretch(1)
        return page_scroll(content)

    def _answer_image_preview(
        self,
        part: LibraryPartDetail,
        tasks: DesktopTaskBridge,
        loader: Callable[[LibraryImage], bytes] | None,
    ) -> QWidget:
        container = QWidget()
        container.setObjectName("LibraryAnswerImagePreview")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("查看非官方答案原图")
        button.setObjectName("LibraryShowAnswerImage")
        button.setAccessibleName(f"查看{part.label_zh}的非官方答案原图")
        layout.addWidget(button)
        images = QWidget()
        images.setObjectName("LibraryAnswerImages")
        images_layout = QVBoxLayout(images)
        images_layout.setContentsMargins(0, 0, 0, 0)
        images.setVisible(False)
        layout.addWidget(images)
        loaded = False

        def toggle() -> None:
            nonlocal loaded
            if not loaded:
                if not callable(loader):
                    images_layout.addWidget(_text_label("答案原图读取接口暂不可用，请重新打开主题。", "StatusAttention"))
                else:
                    for descriptor in part.answer_images:
                        widget = FitWidthImage(descriptor, tasks, loader)
                        widget.setObjectName("LibraryReferenceAnswerImage")
                        self._image_widgets.append(widget)
                        images_layout.addWidget(widget)
                loaded = True
            visible = images.isHidden()
            images.setVisible(visible)
            button.setText("收起答案原图" if visible else "查看非官方答案原图")

        button.clicked.connect(toggle)
        return container

    @staticmethod
    def _source_tab(detail: LibraryThemeDetail) -> QScrollArea:
        content = QWidget()
        content.setObjectName("LibrarySourceContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 8, 4, 16)
        layout.setSpacing(12)
        source = CardFrame()
        source.setAccessibleName("来源记录卡片")
        source.setProperty("semanticRole", "LibrarySourceCard")
        source_layout = QVBoxLayout(source)
        source_layout.setContentsMargins(16, 14, 16, 14)
        source_layout.setSpacing(8)
        source_layout.addWidget(_text_label("来源记录", "CardTitle"))
        source_layout.addWidget(_text_label(f"资料范围：{detail.source_zh}"))
        source_layout.addWidget(_text_label(f"原卷位置：{detail.page_zh}"))
        for label, value in detail.source_fields:
            source_layout.addWidget(_text_label(f"{label}：{value}"))
        layout.addWidget(source)
        if detail.notes_zh:
            notes = CardFrame()
            notes.setAccessibleName("证据与使用说明卡片")
            notes.setProperty("semanticRole", "LibraryEvidenceNotesCard")
            notes_layout = QVBoxLayout(notes)
            notes_layout.setContentsMargins(16, 14, 16, 14)
            notes_layout.setSpacing(8)
            notes_layout.addWidget(_text_label("证据与使用说明", "CardTitle"))
            notes_layout.addWidget(_text_label("\n".join(detail.notes_zh)))
            layout.addWidget(notes)
        layout.addStretch(1)
        return page_scroll(content)

    def closeEvent(self, event: QCloseEvent) -> None:
        for widget in self._image_widgets:
            widget.cancel()
        super().closeEvent(event)


__all__ = ["FitWidthImage", "ImageZoomDialog", "LibraryDetailDialog"]
