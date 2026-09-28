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
    QTabWidget,
    QVBoxLayout,
)

from .tasks import DesktopTaskBridge


class TextbookSourceDialog(QDialog):
    def __init__(self, facade, concept, parent=None, *, tasks=None, excerpt=None, reading_mode="concept"):
        super().__init__(parent)
        if reading_mode not in {"concept", "section"} or (reading_mode == "section" and excerpt is not None):
            raise ValueError("整节阅读不接受知识点摘录或未知阅读范围。")
        self.reading_mode = reading_mode
        self.facade = facade
        self.concept = dict(concept)
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._loaded_pages = False
        self._reading_hint_count = 0
        self._source = None
        self.excerpt = dict(excerpt) if excerpt is not None else None
        self.excerpt_panel = None
        self.setWindowTitle("阅读本节 · 只读教材原页" if reading_mode == "section" else "查看教材原页 · 本地核对")
        self.resize(960, 850)
        self.setMinimumSize(400, 500)
        root = QVBoxLayout(self)
        self.heading = QLabel("正在读取已绑定的教材原文件…")
        self.heading.setWordWrap(True)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.heading)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName('待核对的教材整理出的知识摘要，不是教材原句')
        self.reading_hints = QPlainTextEdit()
        self.reading_hints.setReadOnly(True)
        self.reading_hints.setAccessibleName("当前教材页面的研读提示、适用范围与双页码，候选待教师核对")
        self.summary_tabs = QTabWidget()
        self.summary_tabs.setAccessibleName("教材知识摘要与研读提示")
        self.summary_tabs.setMinimumHeight(140)
        self.summary_tabs.setMaximumHeight(220)
        self.summary_tabs.addTab(self.summary, "知识摘要")
        if reading_mode == "section":
            self.summary_tabs.setTabText(0, "阅读范围")
            self.summary.setAccessibleName("本节阅读范围与原知识点范围")
            self.summary_tabs.setAccessibleName("本节阅读范围与候选研读提示")
        self.summary_tabs.addTab(self.reading_hints, "研读提示")
        root.addWidget(self.summary_tabs)
        controls = QHBoxLayout()
        self.pages = QComboBox()
        self.pages.setAccessibleName("本节PDF文件页序与印刷页码" if reading_mode == "section" else "知识点关联的PDF文件页序")
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
        if reading_mode == "section":
            self.status.setText("本节阅读依据教材目录。研读提示为候选，待教师核对；阅读不会勾选材料或确认摘录。")
        root.addWidget(self.status)
        self.excerpt_button = QPushButton("摘录/修改教材原句…")
        self.excerpt_button.setObjectName("QuietButton")
        self.excerpt_button.clicked.connect(self._edit_excerpt)
        root.addWidget(self.excerpt_button)
        self.excerpt_button.setVisible(reading_mode != "section")
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
        if self.reading_mode == "section":
            self.excerpt_button.setEnabled(False)

    def _edit_excerpt(self):
        if self.reading_mode == "section" or not self._loaded_pages or self._source is None:
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
        if self.reading_mode == "section":
            return
        self.excerpt = value
        self._release()
        super().accept()

    def _load(self):
        if self._closed:
            return
        def read():
            operation = (self.facade.preparation_textbook_section_source if self.reading_mode == "section"
                         else self.facade.preparation_textbook_source)
            return operation(self.concept["concept_id"], self.concept["revision"])

        self.tasks.submit(
            "读取本节教材原页" if self.reading_mode == "section" else "读取教材原页",
            read,
            on_success=self._source_ready,
            on_failure=self._failed,
        )

    def _source_ready(self, source):
        if self._closed:
            return
        self._source = source
        if self.reading_mode == "section":
            if source.get("reading_mode") != "section":
                self._failed("本节阅读范围未能核对，请返回后重新选择。")
                return
            pages, printed = source["pdf_pages"], source["printed_pages"]
            scope = f"PDF文件 {pages[0]}—{pages[-1]} 页 · 印刷 {printed[0]}—{printed[-1]} 页"
            title = source["section_number"] + " " + source["section_title"]
            self.heading.setText("阅读本节 · " + source["volume_title"] + " · " + title + "\n" + scope)
            self.summary.setPlainText(
                source["volume_title"] + " · " + source["source_name"] + "\n" + title + "\n" + scope
                + "\n本窗口只读本节原页。查看不等于教师审核，不会加入备课材料。"
                + "\n所选知识点：" + source["title"]
                + "\n原知识点PDF页序：" + "、".join(map(str, source["concept_pdf_pages"]))
                + "\n需要摘录时，请返回“查看选中知识点的教材原页”。"
            )
        else:
            self.heading.setText(source["title"] + " · " + source["source_name"])
            self.summary.setPlainText(
                '整理出的知识摘要（请对照原页，不是教材原句）：\n' + source["statement"]
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
            self._failed("本节目录页序超出教材范围，未打开本节。" if self.reading_mode == "section"
                         else "知识点关联的文件页序超出教材范围，未跳转到其他页面。")
            return
        self._loaded_pages = True
        for index, page in enumerate(self._source["pdf_pages"]):
            label = (f"PDF第{page}页 · 印刷第{self._source['printed_pages'][index]}页"
                     if self.reading_mode == "section" else f"关联PDF第{page}页")
            self.pages.addItem(label, page)
        self._set_controls(True)
        self._source_page()
        if self._reading_hint_count:
            self.summary_tabs.setCurrentIndex(1)

    def _source_page(self, *_args):
        page = self.pages.currentData()
        if self._loaded_pages and type(page) is int:
            self._jump(page - 1)

    def _jump(self, page):
        if self.reading_mode == "section" and (not self._source or page + 1 not in self._source["pdf_pages"]):
            return
        if self._loaded_pages and 0 <= page < self.document.pageCount():
            self.view.pageNavigator().jump(page, QPointF())
            self._page_changed(page)

    def _page_changed(self, page):
        if self.reading_mode == "section" and self._source:
            pages = self._source["pdf_pages"]
            if page + 1 not in pages:
                self._jump(min(max(page + 1, pages[0]), pages[-1]) - 1)
                return
            index = pages.index(page + 1)
            printed = self._source["printed_pages"][index]
            self.position.setText(f"PDF第{page + 1}页 · 印刷第{printed}页\n本节 {index + 1} / {len(pages)} 页")
            self.pages.blockSignals(True)
            self.pages.setCurrentIndex(index)
            self.pages.blockSignals(False)
            self._update_reading_hints(page + 1)
            self.previous.setEnabled(self._loaded_pages and index > 0)
            self.next.setEnabled(self._loaded_pages and index + 1 < len(pages))
            return
        self.position.setText(f"PDF文件第{page + 1} / {self.document.pageCount()}页")
        self._update_reading_hints(page + 1)
        self.previous.setEnabled(self._loaded_pages and page > 0)
        self.next.setEnabled(
            self._loaded_pages and page + 1 < self.document.pageCount()
        )

    def _update_reading_hints(self, page):
        from ..desktop_textbook_reading_hints import reading_hints_for_page

        source = self._source or {}
        text, count = reading_hints_for_page(
            source.get("reading_hints", {}), page, source.get("pdf_pages", [])
        )
        self._reading_hint_count = count
        self.reading_hints.setPlainText(text)
        self.summary_tabs.setTabText(1, f"研读提示（{count}）" if count else "研读提示")

    def resizeEvent(self, event):
        # Keep long notes scrollable without taking the PDF/navigation space on
        # short screens. Leave several readable lines below the tab controls.
        self.summary_tabs.setMaximumHeight(160 if self.height() < 650 else 220)
        super().resizeEvent(event)

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
