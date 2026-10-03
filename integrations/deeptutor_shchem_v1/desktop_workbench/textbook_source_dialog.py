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
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .tasks import DesktopTaskBridge


class TextbookSourceDialog(QDialog):
    def __init__(self, facade, concept, parent=None, *, tasks=None, excerpt=None, reading_mode="concept"):
        super().__init__(parent)
        if reading_mode not in {"concept", "section", "book", "asset"} or (reading_mode != "concept" and excerpt is not None):
            raise ValueError("整节、整书或素材阅读不接受知识点摘录或未知阅读范围。")
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
        if reading_mode == "book":
            self.setWindowTitle("阅读整本教材 · 本地原文件")
        if reading_mode == "asset":
            self.setWindowTitle("教材素材 · 只读原页")
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
        elif reading_mode == "asset":
            self.summary_tabs.setTabText(0, "素材范围")
            self.summary.setAccessibleName("所选教材素材的整页定位与候选说明")
        self.summary_tabs.addTab(self.reading_hints, "研读提示")
        self.asset_list = QListWidget()
        self.asset_list.setAccessibleName("当前阅读范围的教材素材候选，双击或按回车查看原页")
        self.asset_list.itemActivated.connect(self._asset_page)
        self.asset_details = QPlainTextEdit()
        self.asset_details.setReadOnly(True)
        self.asset_details.setAccessibleName("当前教材页的素材索引，整页锚点未裁切，待教师核对")
        asset_panel = QWidget()
        asset_layout = QHBoxLayout(asset_panel)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.addWidget(self.asset_list, 1)
        asset_layout.addWidget(self.asset_details, 2)
        self.summary_tabs.addTab(asset_panel, "素材索引（候选）")
        self.summary_tabs.setAccessibleName("教材阅读范围、研读提示与素材索引")
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
        elif reading_mode == "asset":
            self.status.setText("本窗口只查看所选素材的整页原文。素材尚未裁切、待教师核对；查看不会加入备课材料。")
        root.addWidget(self.status)
        self.excerpt_button = QPushButton("摘录/修改教材原句…")
        self.excerpt_button.setObjectName("QuietButton")
        self.excerpt_button.clicked.connect(self._edit_excerpt)
        root.addWidget(self.excerpt_button)
        self.excerpt_button.setVisible(reading_mode == "concept")
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
            self.asset_list,
        ):
            control.setEnabled(enabled)
        if self.reading_mode != "concept":
            self.excerpt_button.setEnabled(False)

    def _edit_excerpt(self):
        if self.reading_mode != "concept" or not self._loaded_pages or self._source is None:
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
        if self.reading_mode != "concept":
            return
        self.excerpt = value
        self._release()
        super().accept()

    def _load(self):
        if self._closed:
            return
        def read():
            if self.reading_mode == "asset":
                return self.facade.preparation_textbook_asset_source(
                    self.concept["visual_asset_id"], self.concept["revision"]
                )
            operation = (self.facade.preparation_textbook_book_source if self.reading_mode == "book"
                         else self.facade.preparation_textbook_section_source if self.reading_mode == "section"
                         else self.facade.preparation_textbook_source)
            return operation(self.concept["concept_id"], self.concept["revision"])

        self.tasks.submit(
            {"book": "读取整本教材", "section": "读取本节教材原页", "asset": "读取教材素材原页"}.get(self.reading_mode, "读取教材原页"),
            read,
            on_success=self._source_ready,
            on_failure=self._failed,
        )

    def _source_ready(self, source):
        if self._closed:
            return
        self._source = source
        if self.reading_mode == "asset":
            if (source.get("reading_mode") != "asset"
                    or source.get("visual_asset_id") != self.concept.get("visual_asset_id")
                    or source.get("revision") != self.concept.get("revision")
                    or len(source.get("pdf_pages", [])) != 1
                    or type(source["pdf_pages"][0]) is not int
                    or source["pdf_pages"][0] < 1):
                self._failed("素材原页范围未能核对，请返回后重新选择。")
                return
            page = source["pdf_pages"][0]
            printed = source.get("printed_page")
            printed_label = str(printed) if type(printed) is int else "待核对"
            self.heading.setText("教材素材 · " + source["volume_title"] + "\n" + source["title"])
            self.summary.setPlainText(
                source["source_name"] + f"\nPDF文件第{page}页；印刷页码：{printed_label}"
                + "\n候选说明：" + source["statement"]
                + "\n整页定位，尚未裁切。当前窗口不提供摘录或选材。"
            )
        elif self.reading_mode == "section":
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
                ('原文件阅读范围：\n' if self.reading_mode == "book" else '整理出的知识摘要（请对照原页，不是教材原句）：\n') + source["statement"]
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
            if self.reading_mode == "asset":
                printed = self._source.get("printed_page")
                label = f"PDF第{page}页 · 印刷{printed if type(printed) is int else '待核对'}"
            else:
                label = (f"PDF第{page}页 · 印刷第{self._source['printed_pages'][index]}页"
                         if self.reading_mode == "section" else f"PDF文件第{page}页" if self.reading_mode == "book" else f"关联PDF第{page}页")
            self.pages.addItem(label, page)
        self.asset_list.clear()
        for asset in self._source.get("visual_assets", {}).get("assets", []):
            if asset["pdf_page"] not in self._source["pdf_pages"]:
                continue
            printed_page = str(asset["printed_page"]) if type(asset.get("printed_page")) is int else "待核对"
            item = QListWidgetItem(
                asset["label"] + f" · PDF {asset['pdf_page']} / 印刷 {printed_page}"
            )
            item.setData(Qt.ItemDataRole.UserRole, asset["pdf_page"])
            item.setToolTip("双击或按回车查看整页原文；素材候选尚未裁切、未通过教师核对。")
            self.asset_list.addItem(item)
        count = self.asset_list.count()
        self.summary_tabs.setTabText(2, f"素材索引（{count}项候选）" if count else "素材索引（候选）")
        self._set_controls(True)
        self._source_page()
        if self.reading_mode == "asset":
            self.summary_tabs.setCurrentIndex(2)
        elif self._reading_hint_count:
            self.summary_tabs.setCurrentIndex(1)

    def _source_page(self, *_args):
        page = self.pages.currentData()
        if self._loaded_pages and type(page) is int:
            self._jump(page - 1)

    def _jump(self, page):
        if self.reading_mode != "concept" and (not self._source or page + 1 not in self._source["pdf_pages"]):
            return
        if self._loaded_pages and 0 <= page < self.document.pageCount():
            self.view.pageNavigator().jump(page, QPointF())
            self._page_changed(page)

    def _page_changed(self, page):
        if self.reading_mode == "asset" and self._source:
            anchor = self._source["pdf_pages"][0]
            if page + 1 != anchor:
                self._jump(anchor - 1)
                return
            printed = self._source.get("printed_page")
            self.position.setText(f"PDF第{anchor}页 · 印刷{printed if type(printed) is int else '待核对'}")
            self._update_reading_hints(anchor)
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            return
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
        from ..desktop_textbook_assets import textbook_assets_for_page

        source = self._source or {}
        text, count = reading_hints_for_page(
            source.get("reading_hints", {}), page, source.get("pdf_pages", [])
        )
        self._reading_hint_count = count
        self.reading_hints.setPlainText(text)
        self.summary_tabs.setTabText(1, f"研读提示（{count}）" if count else "研读提示")
        text, _count = textbook_assets_for_page(
            source.get("visual_assets", {}), page, source.get("pdf_pages", [])
        )
        self.asset_details.setPlainText(text)

    def _asset_page(self, item):
        page = item.data(Qt.ItemDataRole.UserRole)
        if self._loaded_pages and self._source and type(page) is int and page in self._source["pdf_pages"]:
            self._jump(page - 1)

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
