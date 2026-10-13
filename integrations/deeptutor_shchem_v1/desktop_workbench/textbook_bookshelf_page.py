"""A book/section-first reading and explicit preparation-reference workspace."""
from __future__ import annotations

from copy import deepcopy
import json

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QDialog, QHeaderView, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .components import section_title, set_status
from .preparation_selection_context import PreparationSelectionContext


def _label(text='', name='MutedLabel'):
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class TextbookBookshelfPage(QWidget):
    preparation_reference_requested = Signal(object)
    preparation_cards_requested = Signal(object)
    preparation_image_requested = Signal(object)
    preparation_images_requested = Signal(object)
    composition_crops_requested = Signal(object)
    question_context_requested = Signal(object)
    knowledge_question_requested = Signal(dict)
    section_materials_requested = Signal(dict)
    preparation_edit_requested = Signal()
    preparation_open_requested = Signal()
    preparation_new_requested = Signal()

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setObjectName('TextbookBookshelfPage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#TextbookBookshelfPage { background-color: #F8F7F2; }')
        self.facade, self.tasks = facade, tasks
        self._snapshot = None
        self._loading = False
        self._closed = False
        self._load_request = 0
        self._reference_request = 0
        self._question_request = 0
        self._question_busy = False
        self._relation_serial = 0
        self._relation_task = None
        self._checked = {}
        self._note_checked = {}
        self._asset_checked = {}
        self._asset_selection_epoch = 0
        self._excerpts = {}
        self._reference = None
        self._prepared_key = None
        self._reference_added_key = self._cards_added_key = None
        self._preparation_busy = False
        self._preparation_target_epoch = 0
        self._selection_dialog = None
        self._rendering = False
        self._reader = None
        self._reader_scope = None
        self._placing_reader = False
        self._parallel_return_tab = None
        self._directory_preference = None
        self._directory_sizes = {}
        self._layout_narrow = None
        positions = getattr(facade, 'textbook_reading_positions', None)
        self._positions = positions() if callable(positions) else None
        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(12, 16, 12, 14)
        root.setSpacing(8)
        self.preparation_context_bar = PreparationSelectionContext(self)
        self.preparation_context_bar.edit_requested.connect(self.preparation_edit_requested)
        self.preparation_context_bar.open_requested.connect(self.preparation_open_requested)
        self.preparation_context_bar.new_requested.connect(self.preparation_new_requested)
        root.addWidget(self.preparation_context_bar)
        self.introduction = section_title('教材', '先选教材和章节，阅读原页；知识摘要和图表就在本节旁边。')
        root.addWidget(self.introduction)
        self.header_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.book = QComboBox()
        self.book.setAccessibleName('选择本地教材册')
        self.book.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.book.setMinimumContentsLength(10)
        self.book.currentIndexChanged.connect(self._populate_sections)
        self.header_row.addWidget(self.book, 1)
        self.import_button = QPushButton('导入教材PDF…')
        self.import_button.setObjectName('QuietButton')
        self.import_button.clicked.connect(self._import_books)
        self.header_row.addWidget(self.import_button)
        self.refresh_button = QPushButton('刷新教材')
        self.refresh_button.setObjectName('QuietButton')
        self.refresh_button.clicked.connect(self.refresh)
        self.header_row.addWidget(self.refresh_button)
        root.addLayout(self.header_row)
        self.resume_bar = QWidget()
        resume_row = QHBoxLayout(self.resume_bar)
        resume_row.setContentsMargins(0, 0, 0, 0)
        self.resume_button = QPushButton('继续上次阅读')
        self.resume_button.setObjectName('QuietButton')
        self.resume_button.setAccessibleName('继续上次阅读同版本教材的册、章节与PDF页')
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self._resume_reading)
        resume_row.addWidget(self.resume_button)
        self.resume_location = _label('阅读后自动记住册、章节和页码。')
        resume_row.addWidget(self.resume_location, 1)
        root.addWidget(self.resume_bar)
        self.resume_bar.setVisible(self._positions is not None)
        self.summary = _label('打开后读取本地教材目录。')
        self.summary.setAccessibleName('教材原书、知识和素材接入情况')
        root.addWidget(self.summary)
        self.query = QLineEdit()
        self.query.setMaxLength(160)
        self.query.setPlaceholderText('查找当前册的章节、知识点或图表')
        self.query.setAccessibleName('在当前教材册查找章节、知识和素材')
        self.query.textChanged.connect(self._populate_sections)
        root.addWidget(self.query)
        self.section_status = _label('选择章节后查看知识、研读提示和图表。')
        self.section_status.setAccessibleName('当前章节的候选内容与研读提示情况')
        self.section_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.section_row.addWidget(self.section_status, 1)
        section_tools = QWidget()
        section_row = QHBoxLayout(section_tools)
        section_row.setContentsMargins(0,0,0,0)
        self.questions_button = QToolButton()
        self.questions_button.setText('本节题目…')
        self.questions_button.setAccessibleName('按当前教材章节的已保存标签查看本地题目并预览加入备课')
        self.questions_button.setToolTip('保留已有选题；本节教材标签中的自动建议仍待核对。')
        self.questions_button.clicked.connect(self._open_questions)
        section_row.addWidget(self.questions_button)
        self.directory_button = QToolButton()
        self.directory_button.setCheckable(True)
        self.directory_button.setAccessibleName('展开或收起教材章节目录')
        self.directory_button.setToolTip('收起目录可留出阅读空间；展开后可查找、切换章节。当前页和选材保留。')
        self.directory_button.toggled.connect(self._toggle_directory)
        section_row.addWidget(self.directory_button)
        self.focus_button = QToolButton()
        self.focus_button.setText('放大阅读')
        self.focus_button.setCheckable(True)
        self.focus_button.setAccessibleName('放大教材原页阅读，返回后保留当前页和选材')
        self.focus_button.setToolTip('暂时收起目录和选材说明，放大当前原页；返回选材时保留原页位置和勾选。')
        self.focus_button.toggled.connect(self._update_reading_layout)
        section_row.addWidget(self.focus_button)
        self.compare_button = QToolButton()
        self.compare_button.setText('原页与选材并排')
        self.compare_button.setCheckable(True)
        self.compare_button.setAccessibleName('宽窗并排核对教材原页与选材，窄窗保留原页并使用页签切换')
        self.compare_button.setToolTip('宽窗并排核对原页与知识、研读提示或图表；窄窗使用页签，拉宽后恢复并排。阅读不会勾选或确认材料。')
        self.compare_button.toggled.connect(self._update_reading_layout)
        section_row.addWidget(self.compare_button)
        self.section_row.addWidget(section_tools)
        root.addLayout(self.section_row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.setAccessibleName('教材章节；复习附录和待归章节内容单列')
        self.tree.setMinimumSize(0, 90)
        self.tree.currentItemChanged.connect(self._section_changed)
        self.tree.itemClicked.connect(lambda *_: self._open_section())
        self.splitter.addWidget(self.tree)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName('当前教材的知识、图表、备课参考和接入说明')
        self.reading = QWidget()
        self.reading_layout = QVBoxLayout(self.reading)
        self.reading_layout.setContentsMargins(0, 0, 0, 0)
        self.reading_prompt = _label('选中左侧章节后，点击“阅读本节原页”。原书在此处打开，章节目录保持可见。')
        self.reading_prompt.setAccessibleName('教材章节原页阅读入口与当前范围')
        self.reading_layout.addWidget(self.reading_prompt, 1)
        self.tabs.addTab(self.reading, '原书阅读')
        self.knowledge_list = QListWidget()
        self.knowledge_list.setAccessibleName('本节知识摘要；勾选后才进入备课参考')
        self.knowledge_list.setMinimumSize(0, 65)
        self.knowledge_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.knowledge_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.knowledge_list.itemChanged.connect(self._check_changed)
        self.knowledge_list.currentItemChanged.connect(self._concept_changed)
        self.knowledge_detail = QPlainTextEdit()
        self.knowledge_detail.setReadOnly(True)
        self.knowledge_detail.setMinimumSize(0, 65)
        self.knowledge_detail.setAccessibleName('所选知识的完整候选摘要和原页位置')
        knowledge = QWidget();box = QVBoxLayout(knowledge)
        box.setContentsMargins(8, 8, 8, 8)
        box.addWidget(_label('勾选要用于本次备课的知识；仅阅读不会勾选。'))
        box.addWidget(self.knowledge_list, 1)
        self.concept_button = QPushButton('阅读知识 / 摘录…')
        self.concept_button.setAccessibleName('查看所选知识的教材原页并手工摘录')
        self.concept_button.setObjectName('QuietButton')
        self.concept_button.clicked.connect(self._open_concept)
        box.addWidget(self.concept_button)
        self.concept_links_button = QPushButton('关联例题…')
        self.concept_links_button.setObjectName('QuietButton')
        self.concept_links_button.setAccessibleName('管理所选教材知识的具体关联例题，明确选题后加入当前备课')
        self.concept_links_button.clicked.connect(lambda: self._open_knowledge_links('concept'))
        box.addWidget(self.concept_links_button)
        box.addWidget(self.knowledge_detail, 1)
        self.knowledge_tab = self.tabs.addTab(knowledge, '本节知识')
        notes = QWidget();box = QVBoxLayout(notes)
        box.setContentsMargins(8, 8, 8, 8)
        box.addWidget(_label('AI整理的研读提示，待教师核对。勾选后可与知识点一起预览、加入当前备课。'))
        self.note_list = QListWidget()
        self.note_list.setMinimumSize(0, 65)
        self.note_list.setAccessibleName('本节研读提示；勾选后才进入备课参考')
        self.note_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.note_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.note_list.currentItemChanged.connect(self._note_changed)
        self.note_list.itemChanged.connect(self._note_check_changed)
        box.addWidget(self.note_list, 1)
        self.note_button = QPushButton('对照提示原页')
        self.note_button.setObjectName('QuietButton')
        self.note_button.clicked.connect(self._open_note)
        box.addWidget(self.note_button)
        self.note_links_button = QPushButton('关联例题…')
        self.note_links_button.setObjectName('QuietButton')
        self.note_links_button.setAccessibleName('管理所选研读提示的具体关联例题，明确选题后加入当前备课')
        self.note_links_button.clicked.connect(lambda: self._open_knowledge_links('readingnote'))
        box.addWidget(self.note_links_button)
        self.note_detail = QPlainTextEdit()
        self.note_detail.setReadOnly(True)
        self.note_detail.setMinimumSize(0, 65)
        self.note_detail.setAccessibleName('研读摘要、课堂核对建议、适用范围与原页位置')
        box.addWidget(self.note_detail, 1)
        self.note_tab = self.tabs.addTab(notes, '研读提示')
        assets = QWidget();box = QVBoxLayout(assets)
        box.setContentsMargins(8, 8, 8, 8)
        box.addWidget(_label('勾选本次需要的图表，可跨章节保留；逐张核对裁片后，一次加入备课。'))
        self.asset_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.asset_splitter.setChildrenCollapsible(False)
        left, right = QWidget(), QWidget()
        left_box, right_box = QVBoxLayout(left), QVBoxLayout(right)
        for layout in (left_box, right_box):layout.setContentsMargins(0,0,0,0)
        self.asset_list = QListWidget()
        self.asset_list.setMinimumSize(0, 160)
        self.asset_list.setAccessibleName('当前章节或补充栏目的图表和实验素材')
        self.asset_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.asset_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.asset_list.currentItemChanged.connect(self._asset_changed)
        self.asset_list.itemChanged.connect(self._asset_check_changed)
        self.asset_list.itemActivated.connect(lambda *_: self._open_asset())
        left_box.addWidget(self.asset_list, 1)
        self.asset_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.check_assets_button = QPushButton('勾选本节图表')
        self.check_assets_button.setObjectName('QuietButton')
        self.check_assets_button.clicked.connect(lambda: self._check_visible_assets(True))
        self.uncheck_assets_button = QPushButton('取消本节勾选')
        self.uncheck_assets_button.setObjectName('QuietButton')
        self.uncheck_assets_button.clicked.connect(lambda: self._check_visible_assets(False))
        self.asset_actions.addWidget(self.check_assets_button)
        self.asset_actions.addWidget(self.uncheck_assets_button)
        left_box.addLayout(self.asset_actions)
        self.batch_crop_button = QPushButton('核对勾选图表（0张）…')
        self.batch_crop_button.setObjectName('PrimaryAction')
        self.batch_crop_button.clicked.connect(self._crop_assets)
        left_box.addWidget(self.batch_crop_button)
        self.asset_button = QPushButton('查看图表原页…')
        self.asset_button.setObjectName('QuietButton')
        self.asset_button.clicked.connect(self._open_asset)
        self.crop_button = QPushButton('框选图表，加入备课…')
        self.crop_button.setObjectName('QuietButton')
        self.crop_button.clicked.connect(self._crop_asset)
        self.asset_detail = QPlainTextEdit()
        self.asset_detail.setReadOnly(True)
        self.asset_detail.setMinimumSize(0, 65)
        self.asset_detail.setAccessibleName('所选素材的中文说明和原页位置')
        from .textbook_asset_preview import TextbookAssetPreview
        self.asset_preview = TextbookAssetPreview(facade, assets, tasks=tasks)
        self.asset_preview.bind_list(self.asset_list)
        self.asset_details_tabs = QTabWidget()
        self.asset_details_tabs.setMinimumSize(0, 0)
        self.asset_details_tabs.addTab(self.asset_preview, '原页预览')
        self.asset_details_tabs.addTab(self.asset_detail, '图表说明')
        right_box.addWidget(self.asset_details_tabs, 1)
        self.asset_open_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.asset_open_actions.addWidget(self.asset_button)
        self.asset_open_actions.addWidget(self.crop_button)
        right_box.addLayout(self.asset_open_actions)
        from .components import page_scroll
        self.asset_splitter.addWidget(page_scroll(left))
        self.asset_splitter.addWidget(page_scroll(right))
        self.asset_splitter.setStretchFactor(0, 2)
        self.asset_splitter.setStretchFactor(1, 3)
        box.addWidget(self.asset_splitter, 1)
        self.asset_tab = self.tabs.addTab(assets, '图表与实验')
        reference = QWidget();box = QVBoxLayout(reference)
        box.setContentsMargins(8, 8, 8, 8)
        self.reference_preview = QPlainTextEdit()
        self.reference_preview.setReadOnly(True)
        self.reference_preview.setAccessibleName('本次勾选的教材备课参考预览')
        self.reference_preview.setPlaceholderText('勾选知识后，点击底部“预览备课参考”。')
        box.addWidget(self.reference_preview, 1)
        self.details_toggle = QToolButton()
        self.details_toggle.setText('查看来源核验详情')
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self._show_reference_details)
        box.addWidget(self.details_toggle)
        self.reference_details = QPlainTextEdit()
        self.reference_details.setReadOnly(True)
        self.reference_details.setAccessibleName('完整编译参考及来源版本核验详情')
        self.reference_details.hide()
        box.addWidget(self.reference_details, 1)
        self.reference_tab = self.tabs.addTab(reference, '备课参考')
        self.intake = QPlainTextEdit()
        self.intake.setReadOnly(True)
        self.intake.setAccessibleName('原书、目录、知识和图表的本地接入说明')
        self.tabs.addTab(self.intake, '接入情况')
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.parallel_reader = QWidget()
        self.parallel_reader.setMinimumSize(0, 0)
        self.parallel_reader.setAccessibleName('与选材并排的只读教材原页')
        self.parallel_layout = QVBoxLayout(self.parallel_reader)
        self.parallel_layout.setContentsMargins(0, 0, 0, 0)
        self.content_splitter.addWidget(self.parallel_reader)
        self.content_splitter.addWidget(self.tabs)
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 2)
        self.parallel_reader.hide()
        self.splitter.addWidget(self.content_splitter)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        root.addWidget(self.splitter, 1)
        from .personal_textbook_panel import PersonalTextbookPanel
        self.personal = PersonalTextbookPanel(facade, tasks, self)
        self._card_sender = None
        self.personal.cards_requested.connect(lambda result: self._deliver_cards(result, personal=True))
        self.personal.reference_requested.connect(self.preparation_reference_requested)
        self.personal.image_requested.connect(self.preparation_image_requested)
        self.personal.images_requested.connect(self.preparation_images_requested)
        self.personal.composition_crops_requested.connect(self.composition_crops_requested)
        self.personal.questions_requested.connect(self.question_context_requested)
        self.personal.knowledge_question_requested.connect(self.knowledge_question_requested)
        self.personal.section_materials_requested.connect(self.section_materials_requested)
        self.personal.changed.connect(self._personal_changed)
        self.personal.note_navigation_requested.connect(self._locate_personal_note)
        self.personal.busy_changed.connect(self._personal_busy)
        self.personal.position_saved.connect(self._update_resume_button)
        self.personal.reading_changed.connect(self._update_reading_layout)
        self.personal.hide()
        root.addWidget(self.personal, 1)
        self.selection_summary = _label('本次备课：尚未勾选知识。')
        selection_row = QHBoxLayout()
        selection_row.addWidget(self.selection_summary, 1)
        self.selection_list_button = QToolButton()
        self.selection_list_button.setText('选材清单…')
        self.selection_list_button.setAccessibleName('查看跨章节已选教材素材，定位或单项移除')
        self.selection_list_button.setToolTip('查看本次知识、研读提示和图表；可定位原项或只移除一项。')
        self.selection_list_button.clicked.connect(self._open_selection_list)
        selection_row.addWidget(self.selection_list_button)
        self.clear_selection_button = QPushButton('清空选材')
        self.clear_selection_button.setObjectName('QuietButton')
        self.clear_selection_button.setToolTip('取消本页勾选和暂存摘录，已加入备课的材料保留。')
        self.clear_selection_button.clicked.connect(self._clear_selection)
        selection_row.addWidget(self.clear_selection_button)
        root.addLayout(selection_row)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.read_button = QPushButton('阅读本节原页…')
        self.read_button.setObjectName('QuietButton')
        self.read_button.clicked.connect(self._open_section)
        self.section_materials_button = QPushButton('本节备课选材…')
        self.section_materials_button.setObjectName('PrimaryAction')
        self.section_materials_button.setAccessibleName('从当前真实教材章节连续选择知识、教材图、讲义知识网和完整例题')
        self.section_materials_button.clicked.connect(self._open_section_materials)
        self.preview_button = QPushButton('预览备课参考')
        self.preview_button.setObjectName('QuietButton')
        self.preview_button.clicked.connect(self._preview_reference)
        self.confirm_button = QPushButton('加入当前备课')
        self.confirm_button.setObjectName('PrimaryAction')
        self.confirm_button.clicked.connect(self._confirm_reference)
        self.cards_button = QPushButton('知识卡加入课件…')
        self.cards_button.setObjectName('QuietButton')
        self.cards_button.setToolTip('把勾选的知识摘要和研读提示加入课件素材；手工摘录通过“加入当前备课”追加。')
        self.cards_button.clicked.connect(lambda:self._compile(confirm=True,cards=True))
        for button in (self.read_button, self.section_materials_button, self.preview_button, self.confirm_button, self.cards_button):
            self.actions.addWidget(button)
        root.addLayout(self.actions)
        self.status = _label('阅读与预览在本机完成；候选内容使用前请对照原页核对。')
        self.status.setAccessibleName('教材阅读和备课参考操作状态')
        root.addWidget(self.status)
        self._set_read_controls(False)
        self.preview_button.setEnabled(False)
        self.confirm_button.setEnabled(False)
        self.cards_button.setEnabled(False)
        self.tabs.currentChanged.connect(self._update_reading_layout)
        finished = getattr(tasks, 'task_finished', None)
        if finished is not None:
            finished.connect(self._relation_finished)
        cancelled = getattr(tasks, 'task_cancelled', None)
        if cancelled is not None:
            cancelled.connect(self._relation_cancelled)

    def set_preparation_context(self, payload, *, dirty=False, busy=False, target_changed=False):
        """Update the destination without changing the currently read source."""
        self._preparation_busy = bool(busy)
        self.preparation_context_bar.set_context(payload, dirty=dirty, busy=busy)
        if target_changed:
            self._preparation_target_epoch += 1
            self._reference_added_key = self._cards_added_key = None
            self.confirm_button.setText('加入当前备课')
            self.cards_button.setText('知识卡加入课件…')
        self.personal.set_preparation_context(payload, dirty=dirty, busy=busy, target_changed=target_changed)
        self._preparation_receipt_controls()
        self._preparation_context_controls()

    def _preparation_context_controls(self):
        self.preparation_context_bar.set_busy(self._preparation_busy or self._loading or self.personal._busy or self._closed)
        self._selection_list_controls()

    def _preparation_receipt_controls(self):
        key = self._selection_key()
        ready = (self._reference is not None and self._prepared_key == key
                 and not self._loading and not self._closed and not self._preparation_busy)
        self.confirm_button.setEnabled(ready and self._reference_added_key != key)
        self.cards_button.setEnabled(ready and self._cards_added_key != key)

    def _set_read_controls(self, enabled):
        self._preparation_context_controls()
        self.questions_button.setEnabled(enabled and not self._question_busy and self._section() is not None)
        self.read_button.setEnabled(enabled and self._scope_node() is not None)
        self.section_materials_button.setEnabled(enabled and not self._preparation_busy and self._scope_node() is not None)
        self.compare_button.setEnabled(enabled and self._scope_node() is not None)
        self.concept_button.setEnabled(enabled and self.knowledge_list.currentItem() is not None)
        self.note_button.setEnabled(enabled and self.note_list.currentItem() is not None)
        self.concept_links_button.setEnabled(enabled and self._relation_task is None
                                             and self.knowledge_list.currentItem() is not None)
        self.note_links_button.setEnabled(enabled and self._relation_task is None
                                          and self.note_list.currentItem() is not None)
        self.asset_button.setEnabled(enabled and self.asset_list.currentItem() is not None)
        self.crop_button.setEnabled(enabled and self.asset_list.currentItem() is not None)
        self.check_assets_button.setEnabled(enabled and self.asset_list.count() > 0)
        self.uncheck_assets_button.setEnabled(enabled and self.asset_list.count() > 0)
        self.batch_crop_button.setEnabled(enabled and bool(self._asset_checked))
        self._update_reading_layout()

    def _update_reading_layout(self, *_):
        if not hasattr(self, 'confirm_button'):
            return
        if any(row['source_id'] == self.book.currentData() for row in (self._snapshot or {}).get('local_books', [])):
            reading = (self.personal._reader is not None
                and self.personal._reader._loaded_pages and self.personal.tabs.currentWidget() == self.personal.reading)
            self.resume_bar.setVisible(self._positions is not None and not reading)
            self.introduction.setVisible(not reading)
            self.summary.setVisible(not reading)
            return
        self.focus_button.setEnabled(self._reader is not None and not self._loading)
        self._place_reader()
        focused = self._reader is not None and self.focus_button.isChecked()
        narrow = self.width() < 720
        reading = self._reader is not None and self.tabs.currentWidget() == self.reading
        compact = focused or narrow and reading
        graphic_focus = narrow and self.tabs.currentIndex() == self.asset_tab
        self.resume_bar.setVisible(self._positions is not None and not compact and not graphic_focus)
        directory = not compact if self._directory_preference is None else self._directory_preference
        directory = bool(directory and not focused)
        was_visible = not self.tree.isHidden()
        if was_visible and not directory:
            self._directory_sizes[self.splitter.orientation()] = self.splitter.sizes()
        self.tree.setVisible(directory)
        self.query.setVisible(directory)
        if directory and not was_visible:
            self.splitter.setSizes(self._directory_sizes.get(self.splitter.orientation(),
                [150, 380] if narrow else [260, 700]))
        with QSignalBlocker(self.directory_button):
            self.directory_button.setChecked(directory)
        self.directory_button.setText('收起目录' if directory else '展开目录')
        self.focus_button.setText('返回选材' if focused else '放大阅读')
        self.introduction.setVisible(not compact and not graphic_focus)
        self.summary.setVisible(not compact and not graphic_focus)
        self.section_status.setVisible(not compact)
        self.compare_button.setVisible(not focused)
        self.compare_button.setText('并排核对' if narrow else '原页与选材并排')
        chosen = bool(self._checked or self._note_checked or self._excerpts or self._asset_checked)
        self.read_button.setVisible(not compact and not graphic_focus)
        self.preview_button.setVisible((not compact or chosen) and not graphic_focus)
        self.confirm_button.setVisible((not compact or chosen) and not graphic_focus)
        self.cards_button.setVisible((not compact or chosen and self.cards_button.isEnabled()) and not graphic_focus)
        self.selection_summary.setVisible((not compact or chosen) and (not graphic_focus or chosen))
        self.clear_selection_button.setVisible((not compact or chosen) and (not graphic_focus or chosen))
        self.selection_list_button.setVisible((not compact or chosen) and (not graphic_focus or chosen))
        self.selection_summary.setText(self._selection_summary_text(compact=compact))
        self.tabs.tabBar().setVisible(not focused)

    def _toggle_directory(self, expanded):
        self._directory_preference = expanded
        if expanded and self.focus_button.isChecked():
            self.focus_button.setChecked(False)
        self._update_reading_layout()

    def _parallel_reading(self):
        return (self._reader is not None and not self.focus_button.isChecked()
                and self.compare_button.isChecked() and self.width() >= 1100)

    def _place_reader(self):
        if self._placing_reader:
            return
        self._placing_reader = True
        try:
            parallel = self._parallel_reading()
            was_parallel = not self.parallel_reader.isHidden()
            current = self.tabs.currentWidget()
            if was_parallel and not parallel and current is not self.reading:
                self._parallel_return_tab = current
            target = self.parallel_reader if parallel else self.reading
            layout = self.parallel_layout if parallel else self.reading_layout
            if self._reader is not None and self._reader.parentWidget() is not target:
                self.parallel_layout.removeWidget(self._reader)
                self.reading_layout.removeWidget(self._reader)
                layout.addWidget(self._reader, 1)
                self._reader.show()
            self.parallel_reader.setVisible(parallel)
            self.tabs.setTabVisible(0, not parallel)
            if parallel and current is self.reading:
                preferred = self._parallel_return_tab
                if preferred is None or self.tabs.indexOf(preferred) < 0:
                    preferred = self.tabs.widget(self.note_tab if self.note_list.count() else self.knowledge_tab)
                self.tabs.setCurrentWidget(preferred)
            elif was_parallel and not parallel and self._reader is not None:
                self.tabs.setCurrentWidget(self.reading)
            if self._reader is not None and self.focus_button.isChecked():
                self.tabs.setCurrentWidget(self.reading)
            self.reading_prompt.setVisible(self._reader is None)
        finally:
            self._placing_reader = False

    def refresh(self):
        if self._closed or self._loading or self.personal._busy:
            return
        loader = getattr(self.facade, 'textbook_bookshelf', None)
        if not callable(loader):
            set_status(self.status, 'attention', '当前工作区暂未接入教材书架。')
            return
        self._loading = True
        self._invalidate_knowledge_links()
        self._update_resume_button()
        self.personal.deactivate()
        self.personal._view = None
        self.import_button.setEnabled(False)
        self._load_request += 1
        request = self._load_request
        self._invalidate_reference()
        self._clear_reader()
        self.preview_button.setEnabled(False)
        self._set_read_controls(False)
        self.tree.setEnabled(False)
        self.book.setEnabled(False)
        self.refresh_button.setEnabled(False)
        set_status(self.status, 'loading', '正在读取本地教材目录、知识和图表…')
        def ready(value):
            if self._closed or request != self._load_request:
                return
            self._loading = False
            self.import_button.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.tree.setEnabled(True)
            self.book.setEnabled(True)
            current_book = self.book.currentData()
            self._snapshot = deepcopy(value)
            by_id = {r['concept_id']: r for r in value['concepts']}
            removed = len(self._checked)
            self._checked = {key:chosen for key,chosen in self._checked.items()
                             if key in by_id and by_id[key]['revision'] == chosen['revision']}
            removed -= len(self._checked)
            by_note = {(r['section_key'], r['note_id']): r for r in value.get('reading_notes', [])}
            removed_notes = len(self._note_checked)
            self._note_checked = {key:chosen for key,chosen in self._note_checked.items()
                if key in by_note and all(chosen[field] == by_note[key][field]
                    for field in ('revision', 'directory_revision'))}
            removed_notes -= len(self._note_checked)
            self._excerpts = {key:entry for key,entry in self._excerpts.items()
                              if key in by_id and entry['revision'] == by_id[key]['revision']}
            by_asset = {row['visual_asset_id']: row for row in value['assets']}
            removed_assets = len(self._asset_checked)
            self._asset_checked = {key: row for key, row in self._asset_checked.items()
                if key in by_asset and row['revision'] == by_asset[key]['revision']}
            removed_assets -= len(self._asset_checked)
            if removed_assets:
                self._asset_selection_epoch += 1
            with QSignalBlocker(self.book):
                self.book.clear()
                for volume in value['volumes']:
                    self.book.addItem(volume['volume_title'], volume['volume_id'])
                for book in value.get('local_books', []):
                    self.book.addItem('已导入 · ' + book['source_name'], book['source_id'])
                current_book = getattr(self, '_imported_book', None) or current_book
                self._imported_book = None
                index = self.book.findData(current_book)
                self.book.setCurrentIndex(max(index, 0))
            self._populate_sections()
            self._update_resume_button()
            self._selection_text()
            message = f"已接入 {len(value['volumes'])} 册教材、{len(value['sections'])} 节；知识与图表仍待核对。"
            if removed:
                message += f' {removed} 条已勾选知识版本变化，已取消旧选择。'
            if removed_assets:
                message += f' {removed_assets} 张已勾选图表版本变化，已取消旧选择。'
            if removed_notes:
                message += f' {removed_notes} 条研读提示版本变化，已取消旧选择。'
            if value['notices']:
                message += '\n' + '\n'.join(value['notices'])
            set_status(self.status, 'attention' if value['notices'] or removed or removed_assets or removed_notes else 'success', message)
        def failed(message):
            if not self._closed and request == self._load_request:
                self._loading = False
                self.import_button.setEnabled(True)
                self._snapshot = None
                self.tree.clear();self.knowledge_list.clear();self.note_list.clear();self.asset_list.clear()
                self.tree.setEnabled(False)
                self.book.setEnabled(False)
                self._checked.clear();self._note_checked.clear();self._excerpts.clear();self._asset_checked.clear()
                self.note_detail.clear()
                self.section_status.setText('教材目录暂不可读，请刷新后重选。')
                self.summary.setText('教材暂不可读，请刷新目录或检查本地资料。')
                self._selection_text()
                self._set_read_controls(False)
                self.refresh_button.setEnabled(True)
                self._update_resume_button()
            set_status(self.status, 'error', message)
        self.tasks.submit('读取教材书架', loader, on_success=ready, on_failure=failed)

    def _populate_sections(self, *_):
        self.personal._capture_note_editor()
        self._invalidate_knowledge_links()
        if self._snapshot is None:
            return
        local = next((row for row in self._snapshot.get('local_books', [])
                      if row['source_id'] == self.book.currentData()), None)
        self._personal_mode(local is not None)
        if local is not None:
            self._clear_reader()
            self.summary.setText(local['source_name'] + f" · {local['page_count']}页 · 已整理章节 {local['section_count']} · 知识笔记 {local['note_count']}")
            if (not self.personal._book or self.personal._book['source_id'] != local['source_id']
                    or self.personal._book['revision'] != local['revision'] or self.personal._view is None):
                self.personal.set_book(local)
            return
        self.personal.deactivate()
        old = self.tree.currentItem()
        old_key = old.data(0, Qt.ItemDataRole.UserRole) if old else None
        volume_id = self.book.currentData()
        volume = next((r for r in self._snapshot['volumes'] if r['volume_id'] == volume_id), None)
        if volume is None:
            return
        state = '原书已在本地；打开时核对版本' if volume['local_file_status'] == 'found' else '原书暂不可读；请检查本地文件'
        self.summary.setText(f"{state} · {volume['knowledge_count']} 条知识 · {volume.get('reading_note_count', 0)} 条可选研读提示 · {volume['asset_count']} 条素材")
        self.intake.setPlainText(volume['volume_title'] + '\n原书：' + volume['source_name'] + '\n' + state
            + '\n\n目录 → 本节原页 → 知识、研读提示与图表 → 预览本次备课参考。'
            + '\n知识摘要是整理候选；素材可框选、预览、确认后加入备课。阅读不自动算作审核。'
            + '\n已登记五册按正式目录阅读。其他PDF可从“导入教材PDF”保存整书，再整理章节范围与知识笔记；不能按页数猜章节。'
            + f"\n另有 {self._snapshot.get('supplement_concept_count', 0)} 条知识按章首页、复习或项目栏目归组；编号节保持独立。"
            + f"\n仍有 {self._snapshot['unmapped_concept_count']} 条知识归属待核对，单列显示。")
        words = self.query.text().casefold().split()
        matches = lambda text: all(w in text.casefold() for w in words)
        def section_match(section):
            texts = [section['title'], section['chapter_title']]
            texts += [r['title']+' '+r['statement'] for r in self._snapshot['concepts'] if r['section_key']==section['section_key']]
            texts += [r['label']+' '+r['description'] for r in self._snapshot['assets'] if r['section_key']==section['section_key']]
            texts += [r['title']+' '+r['summary']+' '+r['classroom_check']
                      for r in self._snapshot.get('reading_notes', []) if r['section_key']==section['section_key']]
            return matches(' '.join(texts))
        with QSignalBlocker(self.tree):
            self.tree.clear()
            chapters = {}
            leaves = []
            for section in self._snapshot['sections']:
                if section['volume_id'] != volume_id or not section_match(section):
                    continue
                parent = chapters.get(section['chapter_id'])
                if parent is None:
                    parent = QTreeWidgetItem([section['chapter_title']])
                    self.tree.addTopLevelItem(parent)
                    parent.setExpanded(True)
                    chapters[section['chapter_id']] = parent
                count = section.get('reading_note_count', 0)
                leaf = QTreeWidgetItem([section['title'], f'研读 {count}条' if count else '暂无研读提示'])
                leaf.setData(0, Qt.ItemDataRole.UserRole, section['section_key'])
                leaf.setData(0, Qt.ItemDataRole.UserRole + 1, section['pdf_pages'][0])
                leaf.setToolTip(0, f"{section['knowledge_count']} 条知识，{count} 条可选研读提示，{section['asset_count']} 条素材；候选待核对。")
                leaf.setToolTip(1, '显示匹配当前教材版本与章节范围的笔记数量，不代表整节已读完或审核。')
                parent.addChild(leaf);leaves.append(leaf)
            book_supplements = None
            for node in self._snapshot.get('supplements', []):
                if node['volume_id'] != volume_id:
                    continue
                node_key = node['supplement_node_key']
                texts = [node['title'], node.get('chapter_title') or '', '补充栏目']
                texts += [r['title']+' '+r['statement'] for r in self._snapshot['concepts']
                          if r.get('resolved_supplement_node_key') == node_key]
                texts += [r['label']+' '+r['description'] for r in self._snapshot['assets']
                          if r.get('resolved_supplement_node_key') == node_key]
                if not matches(' '.join(texts)):
                    continue
                if node['chapter_id'] is None:
                    if book_supplements is None:
                        book_supplements = QTreeWidgetItem(['本册导读与附录'])
                        self.tree.addTopLevelItem(book_supplements)
                        book_supplements.setExpanded(bool(words))
                    parent = book_supplements
                else:
                    parent = chapters.get(node['chapter_id'])
                    if parent is None:
                        parent = QTreeWidgetItem([node['chapter_title']])
                        self.tree.addTopLevelItem(parent)
                        parent.setExpanded(True)
                        chapters[node['chapter_id']] = parent
                leaf = QTreeWidgetItem([node['title']])
                leaf.setData(0, Qt.ItemDataRole.UserRole, node_key)
                leaf.setData(0, Qt.ItemDataRole.UserRole + 1, node['pdf_pages'][0])
                leaf.setToolTip(0, f"补充栏目 · {node['knowledge_count']} 条知识，{node['asset_count']} 条素材")
                parent.addChild(leaf);leaves.append(leaf)
            for parent in list(chapters.values()) + ([book_supplements] if book_supplements else []):
                children = parent.takeChildren()
                parent.addChildren(sorted(children, key=lambda item:item.data(0, Qt.ItemDataRole.UserRole + 1)))
            supplements = [r for r in self._snapshot['assets'] if r['volume_id']==volume_id
                           and r['section_key'] is None and r.get('resolved_supplement_node_key') is None]
            if supplements and matches('复习 探究 附录 '+' '.join(r['label']+' '+r['description'] for r in supplements)):
                leaf = QTreeWidgetItem(['复习、探究与附录'])
                leaf.setData(0, Qt.ItemDataRole.UserRole, '@supplement')
                self.tree.addTopLevelItem(leaf);leaves.append(leaf)
            unknown = [r for r in self._snapshot['concepts'] if r['volume_id']==volume_id
                       and r['section_key'] is None and r.get('resolved_supplement_node_key') is None]
            if unknown and matches('待归章节 '+' '.join(r['title']+' '+r['statement'] for r in unknown)):
                leaf = QTreeWidgetItem(['待归入章节的知识'])
                leaf.setData(0, Qt.ItemDataRole.UserRole, '@unmapped')
                self.tree.addTopLevelItem(leaf);leaves.append(leaf)
            chosen = next((r for r in leaves if r.data(0, Qt.ItemDataRole.UserRole)==old_key), leaves[0] if leaves else None)
            self.tree.setCurrentItem(chosen)
        self._section_changed()

    def _section(self):
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((r for r in (self._snapshot or {}).get('sections', []) if r['section_key']==key), None)

    def _scope_node(self):
        section = self._section()
        if section is not None:
            return section
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((r for r in (self._snapshot or {}).get('supplements', [])
                     if r['supplement_node_key'] == key), None)

    def _section_changed(self, *_):
        self._invalidate_knowledge_links()
        section = self._scope_node()
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        scope = (self.book.currentData(), key, section['revision'] if section else None)
        if self._reader_scope is not None and scope != self._reader_scope:
            self._clear_reader()
        self.read_button.setText('阅读栏目原页' if section and 'supplement_node_key' in section else '阅读本节原页')
        if section and self._reader is None:
            self.reading_prompt.setText(section['title']
                + f'\n\n点击“{self.read_button.text()}”，在这里打开原书；切换知识与素材页签可选取本次备课参考。')
        self._rendering = True
        self.knowledge_list.clear();self.note_list.clear();self.asset_list.clear()
        self.knowledge_detail.clear();self.asset_detail.clear()
        self.note_detail.clear()
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        volume = self.book.currentData()
        if key:
            for row in self._snapshot['concepts']:
                if row['volume_id']==volume and (row['section_key']==key
                        or row.get('resolved_supplement_node_key') == key
                        or key=='@unmapped' and row['section_key'] is None and row.get('resolved_supplement_node_key') is None):
                    entry = QListWidgetItem(row['title'])
                    entry.setData(Qt.ItemDataRole.UserRole, deepcopy(row))
                    entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    entry.setCheckState(Qt.CheckState.Checked if row['concept_id'] in self._checked else Qt.CheckState.Unchecked)
                    entry.setToolTip(row['title'])
                    self.knowledge_list.addItem(entry)
            for row in self._snapshot.get('reading_notes', []):
                if row['volume_id'] == volume and row['section_key'] == key:
                    entry = QListWidgetItem(row['title'])
                    entry.setData(Qt.ItemDataRole.UserRole, deepcopy(row))
                    entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    identity = row['section_key'], row['note_id']
                    entry.setCheckState(Qt.CheckState.Checked if identity in self._note_checked else Qt.CheckState.Unchecked)
                    entry.setToolTip(row['title'] + '；候选待教师核对')
                    self.note_list.addItem(entry)
            for row in self._snapshot['assets']:
                if row['volume_id']==volume and (row['section_key']==key
                        or row.get('resolved_supplement_node_key') == key
                        or key=='@supplement' and row['section_key'] is None and row.get('resolved_supplement_node_key') is None):
                    entry = QListWidgetItem(row['label'])
                    entry.setData(Qt.ItemDataRole.UserRole, deepcopy(row))
                    entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    entry.setCheckState(Qt.CheckState.Checked if row['visual_asset_id'] in self._asset_checked else Qt.CheckState.Unchecked)
                    entry.setToolTip(row['label'])
                    self.asset_list.addItem(entry)
        self._rendering = False
        self.knowledge_list.setCurrentRow(0)
        self.note_list.setCurrentRow(0)
        self.asset_list.setCurrentRow(0)
        self.tabs.setTabText(self.knowledge_tab, f'知识（{self.knowledge_list.count()}）')
        self.tabs.setTabText(self.note_tab, f'研读（{self.note_list.count()}）')
        self.tabs.setTabText(self.asset_tab, f'素材（{self.asset_list.count()}）')
        self.section_status.setText(('本节' if self._section() else '当前栏目')
            + f'：{self.knowledge_list.count()} 条知识 · {self.note_list.count()} 条研读提示 · {self.asset_list.count()} 条图表。候选待教师核对。')
        if key=='@supplement':
            self.tabs.setCurrentIndex(self.asset_tab)
        elif key=='@unmapped':
            self.tabs.setCurrentIndex(self.knowledge_tab)
        self._set_read_controls(not self._loading)

    def _concept_changed(self, item, *_):
        self._invalidate_knowledge_links()
        self.concept_button.setEnabled(item is not None and not self._loading)
        self.concept_links_button.setEnabled(item is not None and not self._loading and not self._closed)
        if item is None:
            self.knowledge_detail.clear();return
        row = item.data(Qt.ItemDataRole.UserRole)
        self.knowledge_detail.setPlainText(row['title']+'\n\n候选知识摘要（请对照原页）：\n'+row['statement']
            +'\n\n原页：PDF第'+ '、'.join(map(str,row['pdf_pages']))+'页。')

    def _asset_changed(self, item, *_):
        self.asset_button.setEnabled(item is not None and not self._loading)
        self.crop_button.setEnabled(item is not None and not self._loading)
        if item is None:
            self.asset_detail.clear();return
        row = item.data(Qt.ItemDataRole.UserRole)
        printed = row['printed_page'] if type(row['printed_page']) is int else '待核对'
        self.asset_detail.setPlainText(row['label']+f"\nPDF第{row['pdf_page']}页 · 书上页码：{printed}\n\n"+row['description']+'\n\n先看原页，再框选、预览并明确加入本次备课。图题与来源保留；查看不会自动选用。')

    def _check_changed(self, item):
        if self._rendering:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked:
            self._checked[row['concept_id']] = {k:row[k] for k in ('concept_id','revision')}
        else:
            self._checked.pop(row['concept_id'], None)
        self._invalidate_reference();self._selection_text()

    def _note_changed(self, item, *_):
        self._invalidate_knowledge_links()
        self.note_button.setEnabled(item is not None and not self._loading)
        self.note_links_button.setEnabled(item is not None and not self._loading and not self._closed)
        if item is None:
            self.note_detail.clear();return
        row = item.data(Qt.ItemDataRole.UserRole)
        self.note_detail.setPlainText(row['title'] + '\n\n研读摘要（AI整理，待教师核对）：\n' + row['summary']
            + '\n\n课堂核对建议：\n' + row['classroom_check']
            + '\n\n适用范围与待核对处：\n' + row['limitation']
            + '\n\nPDF第' + '、'.join(map(str, row['pdf_pages'])) + '页 · 书上第'
            + '、'.join(map(str, row['printed_pages'])) + '页。')

    def _note_check_changed(self, item):
        if self._rendering or self._loading:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        key = row['section_key'], row['note_id']
        if item.checkState() == Qt.CheckState.Checked:
            self._note_checked[key] = {field: row[field] for field in
                ('note_id', 'section_key', 'revision', 'directory_revision')}
        else:
            self._note_checked.pop(key, None)
        self._invalidate_reference();self._selection_text()

    def _open_note(self):
        item = self.note_list.currentItem()
        section = self._section()
        if item is None or section is None or self._loading:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        self.tabs.setCurrentIndex(self.note_tab)
        if self.width() >= 1100:
            self.compare_button.setChecked(True)
        self._show_reader({'section_key': section['section_key'], 'revision': section['revision'],
            'initial_pdf_page': row['pdf_pages'][0]}, 'section')

    def _knowledge_link_identity(self, kind):
        listing = self.knowledge_list if kind == 'concept' else self.note_list
        item = listing.currentItem()
        if item is None or self._snapshot is None:
            return None
        return {'kind': kind, 'row': deepcopy(item.data(Qt.ItemDataRole.UserRole)),
                'book': self.book.currentData(), 'scope': deepcopy(self._scope_node()),
                'volumes': deepcopy(self._snapshot.get('volumes', [])),
                'sections': deepcopy(self._snapshot.get('sections', [])),
                'supplements': deepcopy(self._snapshot.get('supplements', []))}

    def _invalidate_knowledge_links(self):
        self._relation_serial += 1
        task, self._relation_task = self._relation_task, None
        if task is not None and task != 'starting':
            self.tasks.cancel(task)
        if hasattr(self, 'concept_links_button'):
            enabled = not self._loading and not self._closed
            self.concept_links_button.setEnabled(enabled and self.knowledge_list.currentItem() is not None)
            self.note_links_button.setEnabled(enabled and self.note_list.currentItem() is not None)

    def _relation_finished(self, task_id):
        if task_id == self._relation_task:
            self._relation_task = None
            self._relation_serial += 1
            self._set_read_controls(not self._loading and not self._closed)

    def _relation_cancelled(self, task_id, _label):
        if task_id == self._relation_task:
            self._invalidate_knowledge_links()

    def _open_knowledge_links(self, kind):
        if self._closed or self._loading or self._relation_task is not None or self.personal._busy:
            return
        identity = self._knowledge_link_identity(kind)
        if identity is None:
            return
        from ..desktop_knowledge_question_links import KnowledgeQuestionLinks
        service = KnowledgeQuestionLinks(self.facade)
        self._relation_serial += 1
        serial = self._relation_serial
        self._relation_task = 'starting'
        self._set_read_controls(True)
        set_status(self.status, 'loading', '正在读取所选知识的原内容与版本…')

        def current():
            return (not self._closed and not self._loading and serial == self._relation_serial
                    and identity == self._knowledge_link_identity(kind))

        def guard():
            if not current():
                raise ValueError('教材或所选知识已变化，请从当前知识重新打开关联例题。')

        def operation():
            row = identity['row']
            if kind == 'concept':
                from ..desktop_textbook_concept_cards import concept_options
                snapshot = {key: deepcopy(identity[key]) for key in ('volumes', 'sections', 'supplements')}
                snapshot['concepts'] = [deepcopy(row)]
                options = concept_options(self.facade.paths.workspace_root, snapshot)
                selected = next((value['selection'] for value in options
                    if value['concept_id'] == row['concept_id'] and value['revision'] == row['revision']), None)
                if selected is None:
                    raise ValueError('所选教材知识已变化，请刷新教材后重选。')
            else:
                selected = {key: row[key] for key in
                    ('note_id', 'section_key', 'revision', 'directory_revision')}
            return service.knowledge_context(selected)

        def ready(context):
            if not current():
                return
            self._relation_task = None
            self._set_read_controls(True)
            from .knowledge_question_links_dialog import KnowledgeQuestionLinksDialog
            endpoint = deepcopy(context['endpoint'])
            dialog = KnowledgeQuestionLinksDialog(self.facade, self.tasks, self,
                knowledge_resource=endpoint, allow_course_add=True, service=service, guard=guard)
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                if accepted and dialog.course_request is not None and current():
                    # The scoped reader is a live port with native locks;
                    # copy its JSON evidence, while preserving that port.
                    request = {key: value if key == 'scoped_facade' else deepcopy(value)
                               for key, value in dialog.course_request.items()}
                    request['knowledge_endpoint'] = deepcopy(endpoint)
                    self.knowledge_question_requested.emit(request)
            finally:
                dialog.deleteLater()

        def failed(message):
            if not current():
                return
            self._relation_task = None
            self._set_read_controls(True)
            set_status(self.status, 'error', message)
        try:
            task = self.tasks.submit('读取所选知识的关联入口', operation,
                on_success=ready, on_failure=failed)
            if self._relation_task == 'starting' and current():
                self._relation_task = task
        except RuntimeError as exc:
            failed(str(exc))

    def _selection_summary_text(self, *, compact=False):
        count = len(self._checked)
        notes = len(self._note_checked)
        images = len(self._asset_checked)
        if compact:
            return f'已选：知识 {count} · 研读 {notes} · 图表 {images}'
        return f'本次选材：{count} 条知识、{notes} 条研读提示、{images} 张图表（跨章节保留）。文字先预览，图表逐张核对。'

    def _selection_list_busy(self):
        return self._closed or self._loading or self._preparation_busy or self.personal._busy

    def _selection_list_controls(self):
        if not hasattr(self, 'selection_list_button'):
            return
        enabled = not self._selection_list_busy() and bool(self._checked or self._note_checked or self._asset_checked)
        self.selection_list_button.setEnabled(enabled)
        if self._selection_dialog is not None:
            self._selection_dialog.set_busy(self._selection_list_busy())

    def _selection_list_rows(self):
        """Project current identities into a list; titles never determine location."""
        snapshot = self._snapshot or {}
        volumes = {row['volume_id']: row for row in snapshot.get('volumes', [])}
        nodes = {row['section_key']: row for row in snapshot.get('sections', [])}
        nodes.update({row['supplement_node_key']: row for row in snapshot.get('supplements', [])})
        specifications = (
            ('concept', '知识', self._checked, snapshot.get('concepts', []), ('concept_id', 'revision')),
            ('reading_note', '研读提示', self._note_checked, snapshot.get('reading_notes', []),
             ('note_id', 'section_key', 'revision', 'directory_revision')),
            ('asset', '图表', self._asset_checked, snapshot.get('assets', []), ('visual_asset_id', 'revision')),
        )
        entries = []
        for kind, label, checked, available, fields in specifications:
            for chosen in checked.values():
                selection = {key: chosen[key] for key in fields}
                row = next((value for value in available if all(value.get(key) == val for key, val in selection.items())), None)
                row = row or {}
                volume_id = row.get('volume_id')
                volume = volumes.get(volume_id)
                node_key = row.get('section_key') or row.get('resolved_supplement_node_key')
                node = nodes.get(node_key)
                if node is not None and node.get('volume_id') != volume_id:
                    node = None
                # Unmapped groups are explicit UI containers, not invented chapters.
                if not node_key and volume is not None:
                    node_key = '@supplement' if kind == 'asset' else '@unmapped' if kind == 'concept' else None
                can_locate = volume is not None and (node is not None or node_key in ('@supplement', '@unmapped'))
                pages = [row['pdf_page']] if kind == 'asset' and row.get('pdf_page') else row.get('pdf_pages', [])
                printed = [row['printed_page']] if kind == 'asset' and row.get('printed_page') else row.get('printed_pages', [])
                location = ('书：' + (volume['volume_title'] if volume is not None else '待核对')
                    + ' · 节：' + (node['title'] if node is not None else '待核对'))
                location += '\nPDF第' + '、'.join(map(str, pages)) + '页' if pages else '\nPDF页待核对'
                location += ' · 印刷第' + '、'.join(map(str, printed)) + '页' if printed else ' · 印刷页待核对'
                entries.append({'kind': kind, 'selection': selection,
                    'title': label + ' · ' + (row.get('label' if kind == 'asset' else 'title') or '条目版本待核对'),
                    'location': location, 'can_locate': can_locate, 'volume_id': volume_id, 'node_key': node_key})
        return entries

    def _open_selection_list(self):
        personal = any(row['source_id'] == self.book.currentData() for row in (self._snapshot or {}).get('local_books', []))
        if self._selection_list_busy() or not self._selection_list_rows() or personal:
            return
        if self._selection_dialog is not None:
            self._selection_dialog.raise_()
            self._selection_dialog.activateWindow()
            return
        from .textbook_selection_dialog import TextbookSelectionDialog
        dialog = TextbookSelectionDialog(self)
        self._selection_dialog = dialog
        dialog.resize(min(720, max(300, self.window().width()-40)), min(500, max(260, self.window().height()-80)))
        dialog.set_rows(self._selection_list_rows())
        dialog.remove_requested.connect(lambda entry: self._selection_dialog_action(dialog, entry, locate=False))
        dialog.locate_requested.connect(lambda entry: self._selection_dialog_action(dialog, entry, locate=True))
        def finished(_result):
            if self._selection_dialog is dialog:
                self._selection_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def _selection_dialog_action(self, dialog, entry, *, locate):
        if self._selection_dialog is not dialog or not dialog._active:
            return
        changed = self._locate_selection_entry(entry) if locate else self._remove_selection_entry(entry)
        if changed and locate:
            dialog.accept()

    def _current_selection_entry(self, entry):
        if not isinstance(entry, dict):
            return None
        return next((row for row in self._selection_list_rows()
                     if (row['kind'], row['selection']) == (entry.get('kind'), entry.get('selection'))), None)

    def _remove_selection_entry(self, entry):
        if self._selection_list_busy():
            return False
        current = self._current_selection_entry(entry)
        if current is None:
            return False
        selected = current['selection']
        if current['kind'] == 'concept':
            self._checked.pop(selected['concept_id'], None)
        elif current['kind'] == 'reading_note':
            self._note_checked.pop((selected['section_key'], selected['note_id']), None)
        else:
            self._asset_checked.pop(selected['visual_asset_id'], None)
            self._asset_selection_epoch += 1
        # Do not repopulate the chapter: that would discard its active reader.
        for widget, checked, identity in (
                (self.knowledge_list, self._checked, lambda row: row['concept_id']),
                (self.note_list, self._note_checked, lambda row: (row['section_key'], row['note_id'])),
                (self.asset_list, self._asset_checked, lambda row: row['visual_asset_id'])):
            with QSignalBlocker(widget):
                for index in range(widget.count()):
                    item = widget.item(index)
                    item.setCheckState(Qt.CheckState.Checked if identity(item.data(Qt.ItemDataRole.UserRole)) in checked else Qt.CheckState.Unchecked)
        if current['kind'] != 'asset':
            self._invalidate_reference()
        self._selection_text()
        set_status(self.status, 'success', '已移除本次勾选；其他选材、阅读位置和已加入备课的材料保留。')
        return True

    def _locate_selection_entry(self, entry):
        if self._selection_list_busy():
            return False
        current = self._current_selection_entry(entry)
        if current is None or not current['can_locate']:
            return False
        index = self.book.findData(current['volume_id'])
        if index < 0:
            return False
        self.query.clear()
        self.book.setCurrentIndex(index)
        iterator = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        target = None
        while iterator:
            item = iterator.pop(0)
            if item.data(0, Qt.ItemDataRole.UserRole) == current['node_key']:
                target = item
                break
            iterator.extend(item.child(i) for i in range(item.childCount()))
        if target is None:
            return False
        self.tree.setCurrentItem(target)
        self._directory_preference = True
        with QSignalBlocker(self.focus_button):
            self.focus_button.setChecked(False)
        self.tree.scrollToItem(target)
        widget, tab, key = {
            'concept': (self.knowledge_list, self.knowledge_tab, 'concept_id'),
            'reading_note': (self.note_list, self.note_tab, 'note_id'),
            'asset': (self.asset_list, self.asset_tab, 'visual_asset_id'),
        }[current['kind']]
        for index in range(widget.count()):
            item = widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole).get(key) == current['selection'][key]:
                widget.setCurrentItem(item)
                widget.scrollToItem(item)
                self.tabs.setCurrentIndex(tab)
                self._update_reading_layout()
                set_status(self.status, 'success', '已定位原项；可继续核对原页，加入备课仍需明确确认。')
                return True
        return False

    def _open_questions(self):
        section = self._section()
        if self._loading or self._closed or self._question_busy or section is None:
            return
        self._question_request += 1
        epoch = self._question_request
        request = {'mode': 'registered', **{key: section[key] for key in ('section_key', 'revision')}}
        self._question_busy = True
        self._set_read_controls(False)
        set_status(self.status, 'loading', '正在核对本节原书版本与页范围…')

        def current():
            row = self._section()
            return row is not None and all(row[key] == request[key] for key in ('section_key', 'revision'))

        def finished(value=None, message=None):
            if self._closed or epoch != self._question_request:
                return
            self._question_busy = False
            self._set_read_controls(not self._loading)
            if not current():
                return
            if message is not None:
                set_status(self.status, 'error', message)
            else:
                set_status(self.status, 'success', '已核对本节范围；在题目窗口完整预览后加入备课。')
                self.question_context_requested.emit(deepcopy(value))
        try:
            self.tasks.submit('核对本节选题范围', lambda: self.facade.textbook_question_context(request),
                              on_success=finished, on_failure=lambda message: finished(message=message))
        except RuntimeError:
            finished(message='当前窗口已停止接收任务，请重新打开教材页。')

    def _selection_text(self):
        count = len(self._checked)
        notes = len(self._note_checked)
        images = len(self._asset_checked)
        self.selection_summary.setText(self._selection_summary_text())
        self.preview_button.setEnabled(bool(count or notes) and not self._loading)
        self.batch_crop_button.setText(f'核对勾选图表（{images}张）…')
        self.batch_crop_button.setEnabled(bool(images) and not self._loading)
        self.clear_selection_button.setEnabled(bool(count or notes or self._excerpts or images) and not self._loading)
        self._selection_list_controls()
        if self._selection_dialog is not None:
            self._selection_dialog.set_rows(self._selection_list_rows())
        self._update_reading_layout()

    def _asset_check_changed(self, item):
        if self._rendering or self._loading or item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        previous = self._asset_checked.get(row['visual_asset_id'])
        if item.checkState() == Qt.CheckState.Checked:
            self._asset_checked[row['visual_asset_id']] = deepcopy(row)
        else:
            self._asset_checked.pop(row['visual_asset_id'], None)
        if previous != self._asset_checked.get(row['visual_asset_id']):
            self._asset_selection_epoch += 1
        self._selection_text()

    def _check_visible_assets(self, checked):
        if self._loading or self._closed:
            return
        for index in range(self.asset_list.count()):
            self.asset_list.item(index).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _clear_selection(self):
        if self._loading:
            return
        self._checked.clear();self._note_checked.clear();self._excerpts.clear();self._asset_checked.clear()
        self._asset_selection_epoch += 1
        self._invalidate_reference()
        self._section_changed();self._selection_text()
        set_status(self.status, 'success', '本次勾选与暂存摘录已清空；已加入备课的材料保留。')

    def _invalidate_reference(self):
        self._reference_request += 1
        self._reference = self._prepared_key = None
        self._reference_added_key = self._cards_added_key = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setText('加入当前备课')
        self.cards_button.setEnabled(False)
        self.cards_button.setText('知识卡加入课件…')
        self.reference_preview.clear();self.reference_details.clear()

    def _selection_key(self):
        return (tuple(sorted((k,v['revision']) for k,v in self._checked.items())),
                tuple(sorted((key, row['revision'], row['directory_revision']) for key,row in self._note_checked.items())),
                json.dumps([self._excerpts[k] for k in sorted(self._checked) if k in self._excerpts],sort_keys=True,ensure_ascii=False))

    def _compile(self, *, confirm=False, cards=False):
        if not (self._checked or self._note_checked) or self._loading or self._closed:
            return
        if confirm and self._preparation_busy:
            return
        key = self._selection_key()
        if confirm and (self._reference is None or self._prepared_key != key):
            return
        if confirm and (self._cards_added_key if cards else self._reference_added_key) == key:
            return
        target_epoch = self._preparation_target_epoch
        self._reference_request += 1
        request = self._reference_request
        chosen = deepcopy(list(self._checked.values()))
        notes = deepcopy(list(self._note_checked.values()))
        excerpts = deepcopy([self._excerpts[k] for k in self._checked if k in self._excerpts])
        expected = deepcopy(self._reference)
        self.preview_button.setEnabled(False);self.confirm_button.setEnabled(False);self.cards_button.setEnabled(False)
        set_status(self.status, 'loading', '正在核对所选知识与原书版本…')
        def ready(value):
            if self._closed or request != self._reference_request or key != self._selection_key():
                return
            self.preview_button.setEnabled(True)
            if confirm:
                if target_epoch != self._preparation_target_epoch:
                    self._preparation_receipt_controls()
                    set_status(self.status, 'attention', '备课已切换，请再次明确加入当前备课。')
                    return
                reference=value['reference'] if cards else value
                if reference != expected:
                    self._invalidate_reference()
                    set_status(self.status, 'attention', '参考内容在核对期间变化，请重新预览。')
                    return
                if cards:
                    self._preparation_receipt_controls()
                    self._deliver_cards(value['cards'])
                else:
                    self._preparation_receipt_controls()
                    self.preparation_reference_requested.emit(deepcopy(value))
                return
            self._reference, self._prepared_key = deepcopy(value), key
            rows = {r['concept_id']:r for r in self._snapshot['concepts']}
            lines = ['本次将加入的教材备课参考（候选，待教师核对）']
            for selected in chosen:
                row = rows[selected['concept_id']]
                lines.extend(['',row['title'],row['statement'],'原页：PDF第'+'、'.join(map(str,row['pdf_pages']))+'页'])
                if row['concept_id'] in self._excerpts:
                    lines.extend(['本次手工摘录：',self._excerpts[row['concept_id']]['text']])
            by_note = {(r['section_key'], r['note_id']): r for r in self._snapshot.get('reading_notes', [])}
            for selected in notes:
                row = by_note[selected['section_key'], selected['note_id']]
                lines.extend(['', '研读提示 · ' + row['title'], row['summary'],
                    '课堂核对建议：' + row['classroom_check'], '适用范围与待核对处：' + row['limitation'],
                    '原页：PDF第' + '、'.join(map(str, row['pdf_pages'])) + '页；书上第'
                    + '、'.join(map(str, row['printed_pages'])) + '页'])
            self.reference_preview.setPlainText('\n'.join(lines))
            self.reference_details.setPlainText(value['materials'])
            self._preparation_receipt_controls()
            self.tabs.setCurrentIndex(self.reference_tab)
            set_status(self.status, 'success', '已预览所勾选文字。可加入备课参考，或把摘要与提示作为知识卡加入课件；原页图片另行框选。')
        def failed(message):
            if not self._closed and request == self._reference_request:
                if confirm and target_epoch != self._preparation_target_epoch:
                    self.preview_button.setEnabled(True)
                    self._preparation_receipt_controls()
                    set_status(self.status, 'attention', '备课已切换，请再次明确加入当前备课。')
                    return
                self._invalidate_reference();self._selection_text()
                set_status(self.status, 'error', message)
        operation = (lambda: self.facade.textbook_reading_reference(notes, concepts=chosen, textbook_excerpts=excerpts)
                     if notes else self.facade.preparation_source_reference(None,None,1,1,chosen,textbook_excerpts=excerpts))
        if cards:
            reference_operation=operation
            def operation():
                from ..desktop_composition_sources import import_knowledge_cards
                from ..desktop_textbook_concept_cards import concept_options
                options=concept_options(self.facade.paths.workspace_root)
                selections=[]
                for selected in chosen:
                    row=next((r for r in options if all(r.get(k)==v for k,v in selected.items())),None)
                    if row is None:raise ValueError('教材摘要已变化，请重新预览。')
                    selections.append(row['selection'])
                return {'reference':reference_operation(),'cards':import_knowledge_cards(self.facade,selections+notes)}
        self.tasks.submit('核对教材备课参考', operation,
                          on_success=ready,on_failure=failed)

    def _preview_reference(self):
        self.focus_button.setChecked(False)
        self._compile()

    def _confirm_reference(self):
        self._compile(confirm=True)

    def reference_appended(self):
        if self.personal.isVisible():
            self.personal.reference_appended()
            return
        self._reference_added_key = self._selection_key()
        self.confirm_button.setEnabled(False)
        self.confirm_button.setText('本次参考已加入')
        set_status(self.status, 'success', '参考已追加到当前备课；请核对材料并保存草稿。')

    def reference_rejected(self):
        if self.personal.isVisible():
            self.personal.reference_rejected()
            return
        self._preparation_receipt_controls()
        set_status(self.status, 'attention', '当前备课暂不能接收参考，请待当前操作结束后重试。')

    def _deliver_cards(self, result, *, personal=False):
        # Navigation hides the bookshelf before the preparation page replies.
        self._card_sender = self.personal if personal else None
        self.preparation_cards_requested.emit(deepcopy(result))

    def cards_appended(self):
        sender, self._card_sender = self._card_sender, None
        if sender is not None:
            sender.cards_appended()
            return
        self._cards_added_key = self._selection_key()
        self.cards_button.setEnabled(False)
        self.cards_button.setText('本次知识卡已加入')
        set_status(self.status,'success','知识卡已加入当前课件素材；可编排、从知识卡选原图，并保存草稿。')

    def cards_rejected(self):
        sender, self._card_sender = self._card_sender, None
        if sender is not None:
            sender.cards_rejected()
            return
        self._preparation_receipt_controls()
        set_status(self.status,'attention','当前课件暂不能接收知识卡，请查看备课提示后重试。')

    def _show_reference_details(self, checked):
        self.reference_details.setVisible(checked)

    def _open_section(self):
        row = self._scope_node()
        if row is None or self._loading:
            return
        key = 'supplement_node_key' if 'supplement_node_key' in row else 'section_key'
        self._show_reader({k:row[k] for k in (key,'revision')}, 'supplement' if key == 'supplement_node_key' else 'section')

    def _open_section_materials(self):
        row = self._scope_node()
        if row is None or self._loading or self._closed or self._preparation_busy:
            return
        supplement = 'supplement_node_key' in row
        key = 'supplement_node_key' if supplement else 'section_key'
        self.section_materials_requested.emit({
            'book_key': 'registered:' + row['volume_id'],
            'section_filter_key': row[key],
            'selection': {'mode': 'supplement' if supplement else 'registered',
                          key: row[key], 'revision': row['revision']}})

    def _crop_asset(self):
        item = self.asset_list.currentItem()
        if item is None or self._loading or self._closed or self._preparation_busy:
            return
        from .textbook_preparation_crop_dialog import TextbookPreparationCropDialog
        row = item.data(Qt.ItemDataRole.UserRole)
        selection = {key: row[key] for key in ('visual_asset_id', 'revision')}
        book, load_epoch, target_epoch = self.book.currentData(), self._load_request, self._preparation_target_epoch
        dialog = TextbookPreparationCropDialog(self.facade, self.tasks, selection, self,
                                               allow_composition_handoff=True)
        result = dialog.exec()
        current = self.asset_list.currentItem()
        current_row = current.data(Qt.ItemDataRole.UserRole) if current is not None else {}
        if (result == QDialog.DialogCode.Accepted and dialog.selection_request is not None
                and not self._closed and not self._loading and not self._preparation_busy
                and book == self.book.currentData() and load_epoch == self._load_request
                and target_epoch == self._preparation_target_epoch
                and all(current_row.get(key) == value for key, value in selection.items())):
            if getattr(dialog, 'handoff_mode', 'assets') == 'composition':
                self.composition_crops_requested.emit([deepcopy(dialog.selection_request)])
            else:
                self.preparation_image_requested.emit(deepcopy(dialog.selection_request))
        dialog.deleteLater()

    def _open_asset(self):
        item = self.asset_list.currentItem()
        if item is None or self._loading:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        self._show_reader({k:row[k] for k in ('visual_asset_id','revision')}, 'asset')

    def _crop_assets(self):
        if not self._asset_checked or self._loading or self._closed or self._preparation_busy:
            return
        from ..desktop_preparation_images import MAX_IMAGES
        if len(self._asset_checked) > MAX_IMAGES:
            set_status(self.status, 'attention', f'一次最多核对{MAX_IMAGES}张图表，请减少勾选后重试。')
            return
        from .textbook_batch_crop_dialog import TextbookBatchCropDialog
        chosen = deepcopy(self._asset_checked)
        epoch = self._load_request
        selection_epoch = self._asset_selection_epoch
        target_epoch = self._preparation_target_epoch
        choices = [{'selection': {key: row[key] for key in ('visual_asset_id', 'revision')},
                    'label': row['label']} for row in chosen.values()]
        dialog = TextbookBatchCropDialog(self.facade, self.tasks, choices, self,
                                         allow_composition_handoff=True)
        if (dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection_requests is not None
                and not self._closed and not self._preparation_busy and epoch == self._load_request
                and selection_epoch == self._asset_selection_epoch
                and target_epoch == self._preparation_target_epoch and chosen == self._asset_checked):
            if getattr(dialog, 'handoff_mode', 'assets') == 'composition':
                self.composition_crops_requested.emit(deepcopy(dialog.selection_requests))
            else:
                self.preparation_images_requested.emit(deepcopy(dialog.selection_requests))
        dialog.deleteLater()

    def _show_reader(self, identity, mode):
        from .textbook_source_dialog import TextbookSourceDialog
        self._clear_reader()
        section = self._scope_node()
        item = self.tree.currentItem()
        self._reader_scope = (self.book.currentData(), item.data(0, Qt.ItemDataRole.UserRole) if item else None,
                              section['revision'] if section else None)
        self._reader = TextbookSourceDialog(self.facade, identity, self.reading,
            tasks=self.tasks, reading_mode=mode, embedded=True, remember_position=mode in {'section', 'supplement'})
        self._reader.position_saved.connect(self._update_resume_button)
        self.reading_prompt.hide()
        self.reading_layout.addWidget(self._reader, 1)
        self._reader.show()
        if not self._parallel_reading():
            self.tabs.setCurrentWidget(self.reading)
        self._update_reading_layout()

    def _clear_reader(self):
        if self._reader is not None:
            reader, self._reader = self._reader, None
            self.reading_layout.removeWidget(reader)
            self.parallel_layout.removeWidget(reader)
            reader.reject()
            reader.deleteLater()
        self._reader_scope = None
        self._directory_preference = None
        with QSignalBlocker(self.focus_button):
            self.focus_button.setChecked(False)
        self.reading_prompt.setText('选中左侧章节后，点击“阅读本节原页”。原书在此处打开，章节目录保持可见。')
        self.reading_prompt.show()
        self._update_reading_layout()

    def _resume_target(self, record):
        if self._snapshot is None or record is None:
            return None
        selector = record['binding']['selector']
        mode = selector['mode']
        if mode == 'book':
            return next((book for book in self._snapshot.get('local_books', [])
                if book['source_id'] == selector['source_id'] and book['revision'] == selector['revision']
                and (selector['section_id'] is None or book['study_revision'] == selector['study_revision'])), None)
        key = 'section_key' if mode == 'section' else 'supplement_node_key'
        rows = self._snapshot['sections'] if mode == 'section' else self._snapshot.get('supplements', [])
        return next((row for row in rows if row[key] == selector[key] and row['revision'] == selector['revision']
                     and row['volume_id'] == selector['volume_id']), None)

    def _last_reading(self):
        from ..desktop_state import DesktopStateError
        try:
            return self._positions.latest() if self._positions is not None else None
        except (OSError, DesktopStateError):
            return None

    def _update_resume_button(self, *_):
        if self._positions is None:
            return
        record = self._last_reading()
        target = self._resume_target(record)
        available = target is not None and not self._loading and not self._closed and not self.personal._busy
        self.resume_button.setEnabled(available)
        if record is None:
            self.resume_location.setText('阅读后自动记住册、章节和页码。')
        else:
            self.resume_location.setText(record['label'] + f" · PDF第{record['pdf_page']}页"
                + (' · 原册或章节版本已变化，请重新选择。' if target is None and not self._loading else ''))
        self.resume_button.setToolTip('打开时重新核对原文件与阅读范围，继续上次页码。')

    def _resume_reading(self):
        if self._loading or self._closed or self.personal._busy:
            return
        record = self._last_reading()
        target = self._resume_target(record)
        if target is None:
            self._update_resume_button()
            return
        selector = record['binding']['selector']
        if selector['mode'] == 'book':
            self.book.setCurrentIndex(self.book.findData(selector['source_id']))
            self.personal.resume_position(record)
            return
        self.query.clear()
        self.book.setCurrentIndex(self.book.findData(selector['volume_id']))
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        key = 'section_key' if selector['mode'] == 'section' else 'supplement_node_key'
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.data(0, Qt.ItemDataRole.UserRole) == selector[key]:
                self.tree.setCurrentItem(item)
                self._open_section()
                return
            iterator += 1
        self._update_resume_button()

    def _open_concept(self):
        item = self.knowledge_list.currentItem()
        if item is None or self._loading:
            return
        from .textbook_source_dialog import TextbookSourceDialog
        row = item.data(Qt.ItemDataRole.UserRole)
        dialog = TextbookSourceDialog(self.facade,{k:row[k] for k in ('concept_id','revision')},self,
                                      excerpt=self._excerpts.get(row['concept_id']))
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.excerpt is not None:
            self._excerpts[row['concept_id']] = deepcopy(dialog.excerpt)
            self._invalidate_reference();self._selection_text()
            set_status(self.status,'success','本次手工摘录已暂存；勾选此知识后可一并预览。离开本窗口不会改写教材。')
        dialog.deleteLater()

    def resizeEvent(self, event):
        if hasattr(self,'splitter'):
            narrow = event.size().width()<720
            if narrow != self._layout_narrow:
                self._layout_narrow = narrow
                self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
                self.splitter.setSizes(self._directory_sizes.get(self.splitter.orientation(),
                    [150,380] if narrow else [260,700]))
            self.header_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.refresh_button.setText('刷新' if narrow else '刷新教材')
            self.import_button.setText('导入PDF…' if narrow else '导入教材PDF…')
            self.actions.setDirection(QBoxLayout.Direction.TopToBottom if event.size().width()<720 else QBoxLayout.Direction.LeftToRight)
            self.section_row.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self.asset_actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self.asset_splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.asset_open_actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            self._update_reading_layout()
        super().resizeEvent(event)

    def closeEvent(self, event):
        if not self.prepare_close():
            event.ignore()
            return
        self._closed = True
        if self._selection_dialog is not None:
            self._selection_dialog.reject()
        self._invalidate_knowledge_links()
        self.asset_preview.release()
        self.personal.close()
        self._load_request += 1;self._reference_request += 1
        self._question_request += 1
        self._clear_reader()
        super().closeEvent(event)

    def _locate_personal_note(self, key):
        index = self.book.findData(key[0])
        if index >= 0:
            self.book.setCurrentIndex(index)
        self._personal_mode(True)

    def prepare_close(self):
        if self.personal.prepare_close():
            return True
        key = self.personal._pending_note_key or self.personal._note_editor_owner
        if key is not None:
            self._locate_personal_note(key)
        return False

    def _personal_mode(self, enabled):
        self.personal.setVisible(enabled)
        self.introduction.show()
        self.summary.show()
        if enabled and self._selection_dialog is not None:
            self._selection_dialog.reject()
        for widget in (self.query, self.section_status, self.questions_button, self.directory_button, self.focus_button, self.compare_button, self.splitter, self.selection_summary, self.clear_selection_button, self.selection_list_button,
                       self.read_button, self.section_materials_button, self.preview_button, self.confirm_button, self.cards_button, self.status):
            widget.setVisible(not enabled)

    def _personal_busy(self, busy):
        if self._closed:
            return
        for widget in (self.book, self.refresh_button, self.import_button):
            widget.setEnabled(not busy and not self._loading)
        self._preparation_context_controls()
        self._update_resume_button()

    def _personal_changed(self, book):
        if self._snapshot is None:
            return
        for index, row in enumerate(self._snapshot.get('local_books', [])):
            if row['source_id'] == book['source_id']:
                self._snapshot['local_books'][index] = deepcopy(book)
                if self.book.currentData() == book['source_id']:
                    self.summary.setText(book['source_name'] + f" · {book['page_count']}页 · 已整理章节 {book['section_count']} · 知识笔记 {book['note_count']}")
        self._update_resume_button()

    def _import_books(self):
        if self._loading or self._closed or self.personal._busy:
            return
        from .textbook_import_dialog import TextbookImportDialog
        dialog = TextbookImportDialog(self.facade, self.tasks, self)
        def imported():
            self._imported_book = dialog.imported_source_ids[0] if dialog.imported_source_ids else None
            self.refresh()
        dialog.imported.connect(imported)
        dialog.exec();dialog.deleteLater()
        self.refresh()
