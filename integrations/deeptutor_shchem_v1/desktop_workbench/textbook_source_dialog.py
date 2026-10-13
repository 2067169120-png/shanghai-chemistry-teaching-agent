"""Native, local-only viewing of hash-bound textbook PDF pages.

No OCR, hidden-text extraction, model calls or source-file mutations. The PDF
document reads the same in-memory bytes that the source service has verified.
"""

import re

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QSignalBlocker, Qt, QTimer, Signal
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
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .tasks import DesktopTaskBridge


class TextbookSourceDialog(QDialog):
    position_saved = Signal(object)
    reading_state_changed = Signal()
    current_page_crop_requested = Signal(object)

    def __init__(self, facade, concept, parent=None, *, tasks=None, excerpt=None, reading_mode="concept", embedded=False, remember_position=False, allow_current_page_crop=False):
        super().__init__(parent)
        if reading_mode not in {"concept", "concept_card", "section", "asset", "book", "supplement"} or (reading_mode != "concept" and excerpt is not None):
            raise ValueError("整节或素材阅读不接受知识点摘录或未知阅读范围。")
        self.reading_mode = reading_mode
        if embedded and reading_mode == "concept":
            raise ValueError("知识点摘录请在独立阅读窗口中确认。")
        if allow_current_page_crop and (not embedded or reading_mode != "book"):
            raise ValueError("当前页框选只用于个人教材的嵌入阅读。")
        self._allow_current_page_crop = bool(allow_current_page_crop)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.facade = facade
        self.concept = dict(concept)
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._loaded_pages = False
        self._reading_hint_count = 0
        self._source = None
        loader = getattr(facade, 'textbook_reading_positions', None)
        self._positions = loader() if remember_position and embedded and reading_mode in {'section', 'supplement', 'book'} and callable(loader) else None
        self._position_binding = self._pending_position = None
        self._position_ready = False
        self._position_timer = QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.setInterval(500)
        self._position_timer.timeout.connect(self._save_position)
        self.excerpt = dict(excerpt) if excerpt is not None else None
        self.excerpt_panel = None
        self.setWindowTitle("阅读本节 · 只读教材原页" if reading_mode == "section" else "查看教材原页 · 本地核对")
        if reading_mode == "asset":
            self.setWindowTitle("教材素材 · 只读原页")
        elif reading_mode == 'supplement':
            self.setWindowTitle('教材补充栏目 · 只读原页')
        self.resize(960, 850)
        self.setMinimumSize(0, 0) if embedded else self.setMinimumSize(400, 500)
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
        self.summary_tabs.setMinimumHeight(85 if embedded else 140)
        self.summary_tabs.setMaximumHeight(160 if embedded else 220)
        self.summary_tabs.addTab(self.summary, "知识摘要")
        if reading_mode == "section":
            self.summary_tabs.setTabText(0, "阅读范围")
            self.summary.setAccessibleName("本节阅读范围与原知识点范围")
            self.summary_tabs.setAccessibleName("本节阅读范围与候选研读提示")
        elif reading_mode == "asset":
            self.summary_tabs.setTabText(0, "素材范围")
            self.summary.setAccessibleName("所选教材素材的整页定位与候选说明")
        elif reading_mode == 'supplement':
            self.summary_tabs.setTabText(0, '栏目范围')
            self.summary.setAccessibleName('补充栏目的明确阅读页列表与未知印刷页')
        elif reading_mode == "book":
            self.setWindowTitle("个人教材 · 只读原书")
            self.summary_tabs.setTabText(0, "原书范围")
            self.summary.setAccessibleName("个人教材整书或教师录入章节的阅读范围")
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
        if embedded:
            self.reading_details = QToolButton()
            self.reading_details.setText("阅读详情")
            self.reading_details.setAccessibleName("展开或收起阅读范围、研读提示与素材索引")
            self.reading_details.setToolTip("展开或收起完整阅读范围、研读提示与素材索引。")
            self.reading_details.setCheckable(True)
            self.reading_details.toggled.connect(self.summary_tabs.setVisible)
            self.reading_details.toggled.connect(lambda checked: self.reading_details.setText("收起详情" if checked else "阅读详情"))
            self.summary_tabs.hide()
        root.addWidget(self.summary_tabs)
        controls = QHBoxLayout()
        if embedded:
            controls.addWidget(self.reading_details)
        self.pages = QComboBox()
        self.pages.setAccessibleName("本节PDF文件页序与印刷页码" if reading_mode == "section" else "知识点关联的PDF文件页序")
        self.pages.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.pages.setMinimumContentsLength(4 if embedded else 10)
        self.pages.setToolTip("选择当前阅读范围内的PDF文件页序；完整页码与范围显示在原页下方。")
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
            lambda: self._step_page(-1)
        )
        self.next.clicked.connect(
            lambda: self._step_page(1)
        )
        navigation.addWidget(self.previous)
        self.position = QLabel("尚未加载")
        self.position.setWordWrap(True)
        navigation.addWidget(self.position, 1)
        navigation.addWidget(self.next)
        root.addLayout(navigation)
        self.current_page_crop_button = QPushButton("框选当前页，加入备课…")
        self.current_page_crop_button.setObjectName("PrimaryAction")
        self.current_page_crop_button.setMinimumWidth(0)
        self.current_page_crop_button.setToolTip("框选正在阅读的PDF页，预览后加入当前备课。")
        self.current_page_crop_button.clicked.connect(self._request_current_page_crop)
        self.current_page_crop_button.setVisible(self._allow_current_page_crop)
        root.addWidget(self.current_page_crop_button)
        self.reading_memory = QLabel('打开后记住阅读位置。')
        self.reading_memory.setTextFormat(Qt.TextFormat.PlainText)
        self.reading_memory.setWordWrap(True)
        self.reading_memory.setAccessibleName('当前原书范围内自动保存与恢复的阅读位置')
        self.reading_memory.setVisible(self._positions is not None)
        root.addWidget(self.reading_memory)
        self.status = QLabel(
            "文件页序不等于纸面页码，请以原页印刷内容为准。查看不等于审核通过，不会自动改写或导入教材原句。"
        )
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        if reading_mode == "section":
            self.status.setText("本节阅读依据教材目录。研读提示为候选，待教师核对；阅读不会勾选材料或确认摘录。")
        elif reading_mode == "asset":
            self.status.setText("本窗口只查看所选素材的整页原文。素材尚未裁切、待教师核对；查看不会加入备课材料。")
        elif reading_mode == "book":
            self.status.setText("原书按保存时的内容核对。自动目录与教师章节均可对照原页，印刷页码待核对。")
        root.addWidget(self.status)
        if embedded:
            self.status.hide()
        self.excerpt_button = QPushButton("摘录/修改教材原句…")
        self.excerpt_button.setObjectName("QuietButton")
        self.excerpt_button.clicked.connect(self._edit_excerpt)
        root.addWidget(self.excerpt_button)
        self.excerpt_button.setVisible(reading_mode == "concept")
        close = QPushButton("关闭，返回选材")
        close.setObjectName("QuietButton")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        close.setVisible(not embedded)
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
        self.current_page_crop_button.setEnabled(enabled and self.current_page_crop_request() is not None)

    def current_page_crop_request(self):
        """Return the currently displayed, verified personal-book page only."""
        source = self._source
        if (not self._allow_current_page_crop or self._closed or not self._loaded_pages
                or self.reading_mode != "book" or not isinstance(source, dict)
                or self.document.status() != QPdfDocument.Status.Ready):
            return None
        source_id, revision = source.get("source_id"), source.get("revision")
        pages, page = source.get("pdf_pages"), self.view.pageNavigator().currentPage() + 1
        if (not isinstance(source_id, str) or not re.fullmatch(r"BOOK-[0-9a-f]{64}", source_id)
                or revision != source_id[5:] or source.get("source_sha256") != revision
                or source_id != self.concept.get("source_id") or revision != self.concept.get("revision")
                or source.get("entry_origin") != "personal_textbook" or source.get("reading_mode") != "book"
                or source.get("section_id") != self.concept.get("section_id")
                or source.get("section_id") and source.get("study_revision") != self.concept.get("study_revision")
                or not isinstance(pages, list) or not pages
                or any(type(value) is not int or not 1 <= value <= self.document.pageCount() for value in pages)
                or pages != list(range(pages[0], pages[-1] + 1)) or page not in pages):
            return None
        return {"source_id": source_id, "revision": revision, "pdf_page": page,
                "pdf_pages": list(pages), "section_id": source.get("section_id"),
                "study_revision": source.get("study_revision") if source.get("section_id") else None}

    def _request_current_page_crop(self):
        request = self.current_page_crop_request()
        if request is not None and self.current_page_crop_button.isEnabled():
            self.current_page_crop_requested.emit(request)

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
            if self.reading_mode == 'concept_card':
                from ..desktop_textbook_concept_cards import checked_concept
                row,_raw,source=checked_concept(self.facade.paths.workspace_root,self.concept,read_source=True)
                return {**source,'reading_mode':'concept_card','printed_pages':row['printed_pages'],
                        'card_selection':row['selection']}
            if self.reading_mode == 'supplement':
                return self.facade.textbook_bookshelf_supplement_source(
                    self.concept['supplement_node_key'], self.concept['revision'])
            if self.reading_mode == "book":
                return self.facade.personal_textbook_source(
                    {key: self.concept[key] for key in ("source_id", "revision")},
                    section_id=self.concept.get("section_id"),
                    study_revision=self.concept.get("study_revision"))
            if self.reading_mode == "asset":
                return self.facade.preparation_textbook_asset_source(
                    self.concept["visual_asset_id"], self.concept["revision"]
                )
            if self.reading_mode == "section" and "section_key" in self.concept:
                return self.facade.textbook_bookshelf_section_source(
                    self.concept["section_key"], self.concept["revision"]
                )
            operation = (self.facade.preparation_textbook_section_source if self.reading_mode == "section"
                         else self.facade.preparation_textbook_source)
            return operation(self.concept["concept_id"], self.concept["revision"])

        self.tasks.submit(
            '读取补充栏目原页' if self.reading_mode == 'supplement' else
            "读取教材素材原页" if self.reading_mode == "asset" else ("读取本节教材原页" if self.reading_mode == "section" else "读取教材原页"),
            read,
            on_success=self._source_ready,
            on_failure=self._failed,
        )

    def _source_ready(self, source):
        if self._closed:
            return
        self._source = source
        if self.reading_mode == "book":
            pages = source.get("pdf_pages")
            initial_page = self.concept.get("initial_pdf_page")
            if (source.get("reading_mode") != "book" or source.get("entry_origin") != "personal_textbook"
                    or source.get("source_id") != self.concept.get("source_id")
                    or source.get("revision") != self.concept.get("revision")
                    or source.get("source_sha256") != self.concept.get("revision")
                    or source.get("section_id") != self.concept.get("section_id")
                    or self.concept.get("section_id") and source.get("study_revision") != self.concept.get("study_revision")
                    or not isinstance(pages, list) or not pages
                    or any(type(page) is not int or page < 1 for page in pages)
                    or pages != list(range(pages[0], pages[-1] + 1))
                    or initial_page is not None and (type(initial_page) is not int or initial_page not in pages)):
                self._failed("个人教材来源或页范围未能核对，请返回后重新选择。")
                return
            self.heading.setText(source["title"] + " · 个人教材原书")
            self.summary.setPlainText(source["source_name"]
                + f"\nPDF文件 {pages[0]}—{pages[-1]} 页；印刷页码：待核对\n" + source["statement"])
        elif self.reading_mode == "asset":
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
            if (source.get("reading_mode") != "section"
                    or "section_key" in self.concept and (
                        source.get("entry_origin") != "textbook_bookshelf"
                        or source.get("section_key") != self.concept["section_key"]
                        or source.get("revision") != self.concept["revision"])):
                self._failed("本节阅读范围未能核对，请返回后重新选择。")
                return
            pages, printed = source["pdf_pages"], source["printed_pages"]
            initial_page = self.concept.get("initial_pdf_page")
            if initial_page is not None and (type(initial_page) is not int or initial_page not in pages):
                self._failed("提示原页不在当前教材节内，请刷新后重选。")
                return
            scope = f"PDF文件 {pages[0]}—{pages[-1]} 页 · 印刷 {printed[0]}—{printed[-1]} 页"
            title = source["section_number"] + " " + source["section_title"]
            self.heading.setText("阅读本节 · " + source["volume_title"] + " · " + title + "\n" + scope)
            selected_context = (
                "\n可切换旁边的知识与素材页签；勾选知识后再预览备课参考。"
                if source.get("entry_origin") == "textbook_bookshelf" else
                "\n所选知识点：" + source["title"]
                + "\n原知识点PDF页序：" + "、".join(map(str, source["concept_pdf_pages"]))
                + "\n需要摘录时，请返回“查看选中知识点的教材原页”。"
            )
            self.summary.setPlainText(
                source["volume_title"] + " · " + source["source_name"] + "\n" + title + "\n" + scope
                + "\n本窗口只读本节原页。查看不等于教师审核，不会加入备课材料。"
                + selected_context
            )
        elif self.reading_mode == 'supplement':
            pages, printed = source.get('pdf_pages'), source.get('printed_pages')
            if (source.get('reading_mode') != 'supplement'
                    or source.get('entry_origin') != 'textbook_bookshelf'
                    or source.get('supplement_node_key') != self.concept.get('supplement_node_key')
                    or source.get('revision') != self.concept.get('revision')
                    or not isinstance(pages, list) or not pages
                    or any(type(page) is not int or page < 1 for page in pages)
                    or pages != sorted(set(pages))
                    or not isinstance(printed, list) or printed and len(printed) != len(pages)):
                self._failed('栏目来源或阅读范围未能核对，请刷新后重新选择。')
                return
            printed_text = '、'.join(map(str, printed)) if printed else '待核对'
            self.heading.setText('阅读栏目 · ' + source['volume_title'] + '\n' + source['title'])
            self.summary.setPlainText(source['source_name'] + '\n' + source['title']
                + '\nPDF页：' + '、'.join(map(str, pages)) + '\n印刷页：' + printed_text
                + '\n可切换旁边的知识与图表页签，预览后加入本次备课。')
        elif self.reading_mode == 'concept_card':
            if source.get('reading_mode')!='concept_card' or source.get('card_selection')!=self.concept:
                self._failed('知识摘要的来源版本未能核对，请重新选择。')
                return
            self.heading.setText(source['title']+' · '+source['source_name'])
            self.summary.setPlainText('知识摘要候选（请对照原页，不是教材原句）：\n'+source['statement'])
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
        saved = None
        if self._positions is not None:
            from ..desktop_textbook_reading_positions import bind_source
            from ..desktop_state import DesktopStateError
            try:
                self._position_binding = bind_source(self._source)
                saved = self._positions.lookup(self._position_binding)
            except (ValueError, OSError, DesktopStateError):
                self._position_binding = None
                self.reading_memory.setText('本次阅读位置未能保存，可继续查看原页。')
        for index, page in enumerate(self._source["pdf_pages"]):
            if self.reading_mode == 'concept_card':
                printed=self._source['printed_pages']
                label=f"PDF第{page}页 · 印刷{printed[index] if isinstance(printed,list) else '待核对'}"
            elif self.reading_mode == "asset":
                printed = self._source.get("printed_page")
                label = f"PDF第{page}页 · 印刷{printed if type(printed) is int else '待核对'}"
            else:
                label = (f"PDF第{page}页 · 印刷第{self._source['printed_pages'][index]}页"
                         if self.reading_mode == "section" else (
                             f"PDF第{page}页 · 印刷第{self._source['printed_pages'][index]}页"
                             if self._source['printed_pages'] else f"PDF第{page}页 · 印刷待核对")
                         if self.reading_mode == 'supplement' else f"PDF第{page}页 · 印刷待核对" if self.reading_mode == "book" else f"关联PDF第{page}页")
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
        initial_page = self.concept.get('initial_pdf_page')
        if self.reading_mode in {"book", "section"} and initial_page is not None:
            self.pages.setCurrentIndex(self.pages.findData(initial_page))
        elif saved is not None:
            self.pages.setCurrentIndex(self.pages.findData(saved['pdf_page']))
            self.reading_memory.setText(f"已继续上次阅读 · PDF第{saved['pdf_page']}页")
        self._source_page()
        self._position_ready = self._position_binding is not None
        self._queue_position(self.view.pageNavigator().currentPage())
        if self.reading_mode == "asset":
            self.summary_tabs.setCurrentIndex(2)
        elif self._reading_hint_count:
            self.summary_tabs.setCurrentIndex(1)
        self.reading_state_changed.emit()

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

    def _step_page(self, direction):
        current = self.view.pageNavigator().currentPage()
        if self.reading_mode in {'supplement','concept_card'} and self._source:
            pages = self._source['pdf_pages']
            if current + 1 in pages:
                index = pages.index(current + 1) + direction
                if 0 <= index < len(pages):
                    self._jump(pages[index] - 1)
            return
        self._jump(current + direction)

    def _page_changed(self, page):
        self._queue_position(page)
        if self.reading_mode in {'supplement','concept_card'} and self._source:
            pages = self._source['pdf_pages']
            if page + 1 not in pages:
                self._jump(min(pages, key=lambda chosen:abs(chosen-page-1)) - 1)
                return
            index = pages.index(page + 1)
            printed = self._source['printed_pages']
            label = f'印刷第{printed[index]}页' if isinstance(printed,list) and printed else '印刷待核对'
            scope='知识摘要原页' if self.reading_mode=='concept_card' else '本栏目'
            self.position.setText(f'PDF第{page + 1}页 · {label}\n{scope} {index + 1} / {len(pages)} 页')
            with QSignalBlocker(self.pages):
                self.pages.setCurrentIndex(index)
            self.previous.setEnabled(self._loaded_pages and index > 0)
            self.next.setEnabled(self._loaded_pages and index + 1 < len(pages))
            self._update_reading_hints(page + 1)
            return
        if self.reading_mode == "book" and self._source:
            pages = self._source["pdf_pages"]
            if page + 1 not in pages:
                self._jump(min(max(page + 1, pages[0]), pages[-1]) - 1)
                return
            index = pages.index(page + 1)
            self.position.setText(f"PDF第{page + 1}页 · 印刷待核对\n当前范围 {index + 1} / {len(pages)} 页")
            self.pages.blockSignals(True)
            self.pages.setCurrentIndex(index)
            self.pages.blockSignals(False)
            self.previous.setEnabled(self._loaded_pages and index > 0)
            self.next.setEnabled(self._loaded_pages and index + 1 < len(pages))
            self.current_page_crop_button.setEnabled(self.current_page_crop_request() is not None)
            return
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
            self._position_timer.stop()
            self._pending_position = self._position_binding = None
            self._position_ready = False
            self.heading.setText("教材原页未能打开")
            self.status.setText(message)
            self.status.show()
            self.document.close()
            self.buffer.close()
            self.buffer.setData(QByteArray())
            self._source = None
            self._loaded_pages = False
            self._set_controls(False)
            self.reading_state_changed.emit()

    def _release(self):
        self._save_position()
        self._closed = True
        self._set_controls(False)
        self.document.close()
        self.buffer.close()
        self.buffer.setData(QByteArray())
        self._source = None
        if self._own_tasks:
            self.tasks.shutdown(1000)

    def _queue_position(self, page):
        if (not self._closed and self._position_ready and self._position_binding is not None
                and type(page) is int and page + 1 in self._position_binding['pdf_pages']):
            self._pending_position = page + 1
            self._position_timer.start()

    def _save_position(self):
        self._position_timer.stop()
        page, self._pending_position = self._pending_position, None
        if self._closed or page is None or self._position_binding is None:
            return
        from ..desktop_state import DesktopStateError
        try:
            source = self._source
            title = source.get('title') or source.get('section_title') or source['source_name']
            row = self._positions.remember(self._position_binding, page, title)
        except (ValueError, OSError, DesktopStateError):
            self.reading_memory.setText('阅读位置尚未保存，请检查本地保存位置。')
            return
        self.reading_memory.setText(f'已记住阅读位置 · PDF第{page}页')
        self.position_saved.emit(row)

    def reject(self):
        self._release()
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
