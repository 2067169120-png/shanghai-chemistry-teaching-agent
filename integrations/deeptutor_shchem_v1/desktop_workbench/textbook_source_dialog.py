"""Native, local-only viewing of hash-bound textbook PDF pages.

No OCR, hidden-text extraction, model calls or source-file mutations. The PDF
document reads the same in-memory bytes that the source service has verified.
"""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt, QTimer
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .tasks import DesktopTaskBridge


class TextbookSourceDialog(QDialog):
    def __init__(self, facade, concept, parent=None, *, tasks=None, excerpt=None):
        super().__init__(parent)
        self.facade = facade
        self.concept = dict(concept)
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._loaded_pages = False
        self._source = None
        self.excerpt = dict(excerpt) if excerpt is not None else None
        self.excerpt_panel = None
        self.setWindowTitle("查看教材原页 · 本地核对")
        self.resize(960, 850)
        self.setMinimumSize(400, 500)
        root = QVBoxLayout(self)
        self.heading = QLabel("正在读取已绑定的教材原文件…")
        self.heading.setWordWrap(True)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.heading)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(86)
        self.summary.setAccessibleName("待核对的教材蒸馏候选，不是教材原句")
        root.addWidget(self.summary)
        controls = QHBoxLayout()
        self.pages = QComboBox()
        self.pages.setAccessibleName("知识点关联的PDF文件页序")
        self.pages.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.pages.setMinimumContentsLength(10)
        self.pages.currentIndexChanged.connect(self._source_page)
        controls.addWidget(self.pages, 1)
        self.zoom = QComboBox()
        self.zoom.addItems(["适合宽度", "整页", "100%", "150%", "200%"])
        self.zoom.setAccessibleName("教材原页缩放")
        self.zoom.currentIndexChanged.connect(self._zoom_changed)
        controls.addWidget(self.zoom)
        root.addLayout(controls)
        self.view = QPdfView()
        self.view.setAccessibleName("教材原页，只读PDF视图")
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.document = QPdfDocument(self)
        self.buffer = QBuffer(self)
        self.document.statusChanged.connect(self._document_status)
        self.view.setDocument(self.document)
        self.view.pageNavigator().currentPageChanged.connect(self._page_changed)
        root.addWidget(self.view, 1)
        navigation = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.previous.clicked.connect(
            lambda: self._jump(self.view.pageNavigator().currentPage() - 1)
        )
        self.next.clicked.connect(
            lambda: self._jump(self.view.pageNavigator().currentPage() + 1)
        )
        navigation.addWidget(self.previous)
        self.position = QLabel("尚未加载")
        self.position.setWordWrap(True)
        navigation.addWidget(self.position, 1)
        navigation.addWidget(self.next)
        root.addLayout(navigation)
        self.status = QLabel(
            "文件页序不等于纸面页码，请以原页印刷内容为准。查看不等于审核通过，不会自动改写或导入教材原句。"
        )
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.excerpt_button = QPushButton("摘录/修改教材原句…")
        self.excerpt_button.setObjectName("QuietButton")
        self.excerpt_button.clicked.connect(self._edit_excerpt)
        root.addWidget(self.excerpt_button)
        close = QPushButton("关闭，返回选材")
        close.setObjectName("QuietButton")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self._set_controls(False)
        QTimer.singleShot(0, self._load)

    def _set_controls(self, enabled):
        for control in (
            self.pages,
            self.zoom,
            self.previous,
            self.next,
            self.excerpt_button,
        ):
            control.setEnabled(enabled)

    def _edit_excerpt(self):
        if not self._loaded_pages or self._source is None:
            return
        if self.excerpt_panel is None:
            from .textbook_excerpt_widget import TextbookExcerptWidget

            self.excerpt_panel = TextbookExcerptWidget(
                self._source,
                self.concept["revision"],
                self.view.pageNavigator().currentPage() + 1,
                self.excerpt,
                self,
            )
            self.excerpt_panel.pageRequested.connect(lambda page: self._jump(page - 1))
            self.excerpt_panel.dismissed.connect(self._hide_excerpt)
            self.excerpt_panel.applied.connect(self._excerpt_applied)
            self.layout().insertWidget(self.layout().count() - 1, self.excerpt_panel)
        self.excerpt_button.hide()
        self.excerpt_panel.show()
        self._jump(self.excerpt_panel.page.currentData() - 1)
        self.excerpt_panel.text.setFocus()

    def _hide_excerpt(self):
        self.excerpt_panel.hide()
        self.excerpt_button.show()

    def _excerpt_applied(self, value):
        self.excerpt = value
        self._release()
        super().accept()

    def _load(self):
        if self._closed:
            return
        self.tasks.submit(
            "读取教材原页",
            lambda: self.facade.preparation_textbook_source(
                self.concept["concept_id"], self.concept["revision"]
            ),
            on_success=self._source_ready,
            on_failure=self._failed,
        )

    def _source_ready(self, source):
        if self._closed:
            return
        self._source = source
        self.heading.setText(source["title"] + " · " + source["source_name"])
        self.summary.setPlainText(
            "蒸馏候选（请对照原页，不是教材原句）：\n" + source["statement"]
        )
        self.buffer.setData(QByteArray(source["pdf_bytes"]))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.document.load(self.buffer)
        self._document_status(self.document.status())

    def _document_status(self, status):
        if self._closed or self._loaded_pages:
            return
        if status == QPdfDocument.Status.Error:
            self._failed("教材PDF无法打开，可能已加密或文件损坏；请检查原文件。")
            return
        if status != QPdfDocument.Status.Ready or self._source is None:
            return
        if any(page > self.document.pageCount() for page in self._source["pdf_pages"]):
            self.document.close()
            self._failed("知识点关联的文件页序超出教材范围，未跳转到其他页面。")
            return
        self._loaded_pages = True
        for page in self._source["pdf_pages"]:
            self.pages.addItem(f"关联PDF第{page}页", page)
        self._set_controls(True)
        self._source_page()

    def _source_page(self, *_args):
        page = self.pages.currentData()
        if self._loaded_pages and type(page) is int:
            self._jump(page - 1)

    def _jump(self, page):
        if self._loaded_pages and 0 <= page < self.document.pageCount():
            self.view.pageNavigator().jump(page, QPointF())
            self._page_changed(page)

    def _page_changed(self, page):
        self.position.setText(f"PDF文件第{page + 1} / {self.document.pageCount()}页")
        self.previous.setEnabled(self._loaded_pages and page > 0)
        self.next.setEnabled(
            self._loaded_pages and page + 1 < self.document.pageCount()
        )

    def _zoom_changed(self, index):
        if index < 2:
            self.view.setZoomMode(
                QPdfView.ZoomMode.FitToWidth
                if index == 0
                else QPdfView.ZoomMode.FitInView
            )
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor((1.0, 1.5, 2.0)[index - 2])

    def _failed(self, message):
        if not self._closed:
            self.heading.setText("教材原页未能打开")
            self.status.setText(message)
            self._set_controls(False)

    def _release(self):
        self._closed = True
        self.document.close()
        self.buffer.close()
        self.buffer.setData(QByteArray())
        self._source = None
        if self._own_tasks:
            self.tasks.shutdown(1000)

    def reject(self):
        self._release()
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
