"""Personal-book reading, explicit chapter entry and lesson-note selection."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .components import set_status
from .personal_note_edit_session import NEW_NOTE, NoteEditConflict, NoteEditSession
from .studio_style import TOKENS


def label(text):
    value = QLabel(text)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


class PersonalTextbookPanel(QWidget):
    reference_requested = Signal(object)
    cards_requested = Signal(object)
    image_requested = Signal(object)
    images_requested = Signal(object)
    composition_crops_requested = Signal(object)
    questions_requested = Signal(object)
    knowledge_question_requested = Signal(dict)
    section_materials_requested = Signal(dict)
    changed = Signal(object)
    busy_changed = Signal(bool)
    position_saved = Signal(object)
    reading_changed = Signal()
    note_navigation_requested = Signal(object)

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._book = self._view = self._reader = None
        self._epoch = 0
        self._closed = self._busy = self._rendering = False
        self._task_id = None
        self._relation_task = None
        self._relation_serial = 0
        self._section_id = self._note_id = None
        self._note_edits = NoteEditSession()
        self._note_editor_owner = None
        self._note_rendered_fields = None
        self._note_save_token = None
        self._note_views = {}
        self._note_last_sections = {}
        self._pending_note_key = None
        self._checked = {}
        self._crop_pages = []
        self._crop_queue_epoch = 0
        self._search_dialog = None
        self._search_serial = 0
        self._reference = None
        self._reference_key = None
        self._reference_added_key = None
        self._cards_added_key = None
        self._preparation_busy = False
        self._preparation_target_epoch = 0
        self._outline_preview = None
        self._pending_resume = None
        self._directory_preference = None
        root = QVBoxLayout(self)
        # Let the requested width arrive before responsive buttons/splitter
        # change direction; their initial horizontal minimum must not widen it.
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        self.introduction = label("阅读原书，可先识别目录，再按章节整理笔记。勾选笔记并预览后，可加入备课参考或课件知识卡。")
        root.addWidget(self.introduction)
        self.directory_button = QToolButton()
        self.directory_button.setCheckable(True)
        self.directory_button.setAccessibleName('展开或收起个人教材章节，保持当前原页与选材')
        self.directory_button.toggled.connect(self._toggle_directory)
        chapter_actions = QHBoxLayout()
        chapter_actions.addWidget(self.directory_button)
        chapter_actions.addStretch(1)
        self.search_book_button = QToolButton()
        self.search_book_button.setText('在书内查找…')
        self.search_book_button.setAccessibleName('查找个人教材原文、已保存目录与知识笔记，并定位原页')
        self.search_book_button.clicked.connect(self._open_book_search)
        chapter_actions.addWidget(self.search_book_button)
        self.questions_button = QToolButton()
        self.questions_button.setText('本节题目…')
        self.questions_button.setAccessibleName('核对个人教材当前章节与本地题目标签的对应后选题')
        self.questions_button.setToolTip('原书与完整页范围唯一对应时按教材标签筛选；其他章节可按文字检索。')
        self.questions_button.clicked.connect(self._open_questions)
        chapter_actions.addWidget(self.questions_button)
        self.section_materials_button = QToolButton()
        self.section_materials_button.setText('本节备课选材…')
        self.section_materials_button.setAccessibleName('从个人教材当前已保存章节连续选知识、图和讲义例题')
        self.section_materials_button.clicked.connect(self._open_section_materials)
        chapter_actions.addWidget(self.section_materials_button)
        root.addLayout(chapter_actions)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumSize(0, 80)
        self.tree.setAccessibleName("个人教材章节与自动目录候选及PDF页范围")
        self.tree.currentItemChanged.connect(self._section_changed)
        self.splitter.addWidget(self.tree)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 150)
        self.reading = QWidget()
        self.reading_box = QVBoxLayout(self.reading)
        self.reading_box.setContentsMargins(0, 0, 0, 0)
        self.reading_prompt = label("点击底部“阅读整书”，核对书上目录和PDF页序。已整理的章节可从左侧选择。")
        self.reading_box.addWidget(self.reading_prompt, 1)
        self.tabs.addTab(self.reading, "原书阅读")
        chapter = QWidget(); box = QVBoxLayout(chapter)
        self.detect_outline_button = QPushButton("识别并整理目录")
        self.detect_outline_button.clicked.connect(self._detect_outline)
        box.addWidget(self.detect_outline_button)
        box.addWidget(label("从同版本登记目录、PDF书签或正文标题整理候选。保存后即可按章节阅读，范围仍可核对修改。"))
        box.addWidget(label("填写自己核对的章节名称与PDF文件页序。印刷页码保留为待核对。"))
        self.new_section = QPushButton("添加章节")
        self.new_section.clicked.connect(self._new_section)
        box.addWidget(self.new_section)
        form = QFormLayout()
        self.section_title = QLineEdit(); self.section_title.setMaxLength(160)
        self.section_title.setAccessibleName("教师填写的章节名称")
        self.start, self.end = QSpinBox(), QSpinBox()
        self.start.setAccessibleName("章节起始PDF文件页序")
        self.end.setAccessibleName("章节结束PDF文件页序")
        for value in (self.start, self.end):
            value.setRange(1, 3000)
            value.setMinimumWidth(90)
            value.valueChanged.connect(self._chapter_edited)
        self.section_title.textChanged.connect(self._chapter_edited)
        form.addRow("章节名称", self.section_title)
        form.addRow("起始PDF页", self.start)
        form.addRow("结束PDF页", self.end)
        box.addLayout(form)
        self.range_confirmed = QCheckBox("我已对照原书确认章节名称与起止PDF页")
        self.range_confirmed.toggled.connect(self._controls)
        box.addWidget(self.range_confirmed)
        self.save_section = QPushButton("保存章节范围")
        self.save_section.clicked.connect(self._save_section)
        box.addWidget(self.save_section)
        self.backup_button = QPushButton("备份全部个人教材与笔记…")
        self.backup_button.setObjectName("QuietButton")
        self.backup_button.clicked.connect(self._backup)
        box.addWidget(self.backup_button);box.addStretch(1)
        self.tabs.addTab(self._scroll(chapter), "章节整理")
        notes = QWidget(); box = QVBoxLayout(notes)
        self.knowledge_button = QPushButton('从所选章节批量整理知识…')
        self.knowledge_button.clicked.connect(self._knowledge)
        box.addWidget(self.knowledge_button)
        box.addWidget(label("可从所选章节整理知识候选，也可手工添加笔记。核对原页、条件、符号与单位后，勾选本次备课需要的内容。"))
        self.note_list = QListWidget()
        self.note_list.setMinimumSize(0, 65)
        self.note_list.setMaximumHeight(110)
        self.note_list.setAccessibleName("当前章节知识笔记，勾选用于本次备课")
        self.note_list.itemChanged.connect(self._check_changed)
        self.note_list.currentItemChanged.connect(self._note_changed)
        box.addWidget(self.note_list, 1)
        self.knowledge_links_button = QPushButton('关联例题…')
        self.knowledge_links_button.setObjectName('QuietButton')
        self.knowledge_links_button.setAccessibleName('管理当前已保存教材知识笔记的具体关联例题，明确选题后加入当前备课')
        self.knowledge_links_button.setToolTip('选中已保存的笔记后使用；编辑内容请先保存，再关联具体例题。')
        self.knowledge_links_button.clicked.connect(self._open_knowledge_links)
        box.addWidget(self.knowledge_links_button)
        self.new_note = QPushButton("添加笔记")
        self.new_note.clicked.connect(self._new_note)
        box.addWidget(self.new_note)
        form = QFormLayout()
        self.note_title = QLineEdit()
        self.note_title.setAccessibleName("手工笔记名称")
        self.note_page = QSpinBox();self.note_page.setRange(1, 3000)
        self.note_page.setAccessibleName("手工笔记对应的PDF文件页序")
        form.addRow("笔记名称", self.note_title);form.addRow("原页位置", self.note_page)
        box.addLayout(form)
        self.note_text = QPlainTextEdit()
        self.note_text.setMinimumSize(0, 90)
        self.note_text.setMaximumHeight(135)
        self.note_text.setAccessibleName("教师手工笔记正文，最多1200字，保留条件符号与单位")
        self.note_text.setPlaceholderText("记录概念、条件、例证或易错点；最多1200字。")
        box.addWidget(self.note_text, 1)
        for signal in (self.note_title.textChanged, self.note_page.valueChanged, self.note_text.textChanged):
            signal.connect(self._note_edited)
        self.note_confirmed = QCheckBox("我已对照原页核对这条手工笔记")
        self.note_confirmed.toggled.connect(self._note_confirmation_changed)
        box.addWidget(self.note_confirmed)
        self.note_edit_status = label('选择已保存笔记，或添加本节笔记。')
        self.note_edit_status.setAccessibleName('当前笔记未保存修改与版本状态')
        box.addWidget(self.note_edit_status)
        self.note_saved_version = QPlainTextEdit()
        self.note_saved_version.setReadOnly(True)
        self.note_saved_version.setMinimumHeight(75)
        self.note_saved_version.setMaximumHeight(115)
        self.note_saved_version.setAccessibleName('版本变化后的已保存笔记，供与当前未保存修改核对')
        self.note_saved_version.hide()
        box.addWidget(self.note_saved_version)
        self.save_note = QPushButton("保存手工笔记")
        self.save_note.clicked.connect(self._save_note)
        box.addWidget(self.save_note)
        self.cancel_note_edits = QPushButton('取消本次修改')
        self.cancel_note_edits.setObjectName('QuietButton')
        self.cancel_note_edits.setAccessibleName('仅取消当前笔记的未保存修改，其他笔记保留')
        self.cancel_note_edits.clicked.connect(self._cancel_note_edits)
        box.addWidget(self.cancel_note_edits)
        self._notes_tab = self.tabs.addTab(self._scroll(notes), "知识笔记")
        self.reference_preview = QPlainTextEdit()
        self.reference_preview.setReadOnly(True)
        self.reference_preview.setAccessibleName("所选个人教材知识笔记与原页位置的备课预览")
        reference = QWidget();box = QVBoxLayout(reference)
        box.addWidget(self.reference_preview, 1)
        self.details_toggle = QToolButton();self.details_toggle.setText("查看来源核验详情")
        self.details_toggle.setCheckable(True)
        box.addWidget(self.details_toggle)
        self.reference_details = QPlainTextEdit();self.reference_details.setReadOnly(True)
        self.reference_details.setAccessibleName("个人教材完整来源、笔记版本与备课编译详情")
        self.reference_details.hide();box.addWidget(self.reference_details, 1)
        self.details_toggle.toggled.connect(self.reference_details.setVisible)
        self.tabs.addTab(reference, "备课参考")
        outline = QWidget(); outline_box = QVBoxLayout(outline)
        self.outline_summary = label("先在章节整理中点击“识别并整理目录”。识别和预览不保存章节。")
        outline_box.addWidget(self.outline_summary)
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderLabels(["目录候选", "PDF页", "识别依据"])
        self.outline_tree.setMinimumSize(0, 130)
        self.outline_tree.setWordWrap(True)
        self.outline_tree.setAccessibleName("自动目录候选及实际PDF页，勾选保存，双击定位原页")
        self.outline_tree.itemChanged.connect(self._controls)
        self.outline_tree.currentItemChanged.connect(self._controls)
        self.outline_tree.itemActivated.connect(lambda *_: self._read_outline_page())
        outline_box.addWidget(self.outline_tree, 1)
        self.outline_read_button = QPushButton("定位所选原页")
        self.outline_read_button.clicked.connect(self._read_outline_page)
        outline_box.addWidget(self.outline_read_button)
        self.outline_save_button = QPushButton("保存勾选的目录候选")
        self.outline_save_button.clicked.connect(self._save_outline)
        outline_box.addWidget(self.outline_save_button)
        outline_box.addWidget(label("结束页可能按下一标题推定。自动目录保持待核对，不会覆盖已有章节和笔记。"))
        self._outline_tab = self.tabs.addTab(outline, "目录识别")
        graphics = QWidget(); graphics_box = QVBoxLayout(graphics)
        graphics_box.addWidget(label("从整书任意PDF页框选图表。先看原页和裁片，再填写图题与本次用途，加入当前备课。"))
        self.crop_origin = label("请选择已导入的教材。印刷页码保持待核对。")
        graphics_box.addWidget(self.crop_origin)
        self.crop_page = QSpinBox(); self.crop_page.setRange(1, 3000)
        self.crop_page.setAccessibleName("个人教材图表所在的PDF文件页序")
        graphics_form = QFormLayout(); graphics_form.addRow("图表所在PDF页", self.crop_page)
        graphics_box.addLayout(graphics_form)
        self.crop_read_button = QPushButton("定位图表原页")
        self.crop_read_button.clicked.connect(lambda: self._read(False, initial_page=self.crop_page.value()))
        graphics_box.addWidget(self.crop_read_button)
        self.crop_button = QPushButton("框选本页图表，加入备课…")
        self.crop_button.setObjectName("PrimaryAction")
        self.crop_button.clicked.connect(self._crop_page)
        graphics_box.addWidget(self.crop_button)
        self.queue_page_button = QPushButton('加入本批待选页')
        self.queue_page_button.setObjectName('QuietButton')
        self.queue_page_button.clicked.connect(self._queue_crop_page)
        graphics_box.addWidget(self.queue_page_button)
        self.crop_queue = QListWidget()
        self.crop_queue.setMinimumHeight(75)
        self.crop_queue.setMaximumHeight(145)
        self.crop_queue.setAccessibleName('本批待核对的个人教材PDF页，按添加顺序；跨章节保留')
        graphics_box.addWidget(self.crop_queue)
        self.queue_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.remove_page_button = QPushButton('移除所选页')
        self.remove_page_button.setObjectName('QuietButton')
        self.remove_page_button.clicked.connect(self._remove_crop_page)
        self.clear_pages_button = QPushButton('清空本批')
        self.clear_pages_button.setObjectName('QuietButton')
        self.clear_pages_button.clicked.connect(self._clear_crop_pages)
        self.queue_actions.addWidget(self.remove_page_button)
        self.queue_actions.addWidget(self.clear_pages_button)
        graphics_box.addLayout(self.queue_actions)
        self.batch_crop_button = QPushButton('逐张核对本批图表（0页）…')
        self.batch_crop_button.setObjectName('PrimaryAction')
        self.batch_crop_button.clicked.connect(self._crop_batch)
        graphics_box.addWidget(self.batch_crop_button)
        graphics_box.addWidget(label('先将需要的PDF页加入本批，再逐页框选。每页本批选一张图；同页的其他图可另开一批。'))
        graphics_box.addWidget(label("加入后在备课的教学环节关联此图，核对学生可见范围后可用于PPT与学习单。原书、PDF页位置和裁选记录一并保留。"))
        graphics_box.addStretch(1)
        self._graphics_tab = self.tabs.addTab(self._scroll(graphics), "图表选用")
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 1);self.splitter.setStretchFactor(1, 3)
        root.addWidget(self.splitter, 1)
        self.selection = label("尚未勾选知识笔记。")
        root.addWidget(self.selection)
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.read_book_button = QPushButton("阅读整书")
        self.read_book_button.clicked.connect(lambda: self._read(False))
        self.read_section_button = QPushButton("阅读所选章节")
        self.read_section_button.clicked.connect(lambda: self._read(True))
        self.preview_button = QPushButton("预览备课参考")
        self.preview_button.clicked.connect(lambda: self._compile(False))
        self.confirm_button = QPushButton("加入当前备课")
        self.confirm_button.setObjectName("PrimaryAction")
        self.confirm_button.clicked.connect(lambda: self._compile(True))
        self.cards_button = QPushButton("知识卡加入课件…")
        self.cards_button.setAccessibleName("预览所选个人教材笔记后，作为知识卡加入当前课件编排")
        self.cards_button.clicked.connect(lambda: self._compile(True, cards=True))
        self.clear_button = QPushButton("清空勾选")
        self.clear_button.clicked.connect(self._clear_selection)
        for button in (self.read_book_button, self.read_section_button, self.preview_button, self.confirm_button, self.cards_button, self.clear_button):
            self.actions.addWidget(button)
        self.reading_actions_button = QToolButton()
        self.reading_actions_button.setText('选材操作…')
        self.reading_actions_button.setObjectName('QuietButton')
        self.reading_actions_button.setAccessibleName('预览备课参考、知识卡加入课件或清空勾选')
        self.reading_actions_button.setToolTip('预览备课参考、将知识卡加入课件，或清空本次勾选。')
        self.reading_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.reading_actions_menu = QMenu(self.reading_actions_button)
        self.reading_actions_button.setMenu(self.reading_actions_menu)
        for name, button in (('preview', self.preview_button), ('cards', self.cards_button), ('clear', self.clear_button)):
            action = self.reading_actions_menu.addAction(button.text())
            action.triggered.connect(lambda _checked=False, target=button: target.click())
            setattr(self, f'reading_{name}_action', action)
        self.reading_actions_menu.aboutToShow.connect(self._controls)
        self.actions.addWidget(self.reading_actions_button)
        self.note_actions_button = QToolButton()
        self.note_actions_button.setText('阅读与选材…')
        self.note_actions_button.setObjectName('QuietButton')
        self.note_actions_button.setAccessibleName('阅读整书或所选章节，预览备课参考、加入当前备课、知识卡加入课件或清空勾选')
        self.note_actions_button.setToolTip('阅读原书、预览或加入所选笔记；保存和取消修改仍在笔记正文下方。')
        self.note_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.note_actions_menu = QMenu(self.note_actions_button)
        self.note_actions_menu.setObjectName('NoteActionsMenu')
        self.note_actions_menu.setStyleSheet(
            f"QMenu#NoteActionsMenu {{ background: {TOKENS['surface_panel']}; border: 1px solid {TOKENS['line']}; padding: 4px; }}"
            "QMenu#NoteActionsMenu::item { padding: 8px 12px; }"
            f"QMenu#NoteActionsMenu::item:selected {{ background: {TOKENS['brand_soft']}; }}"
            f"QMenu#NoteActionsMenu::item:disabled {{ color: {TOKENS['ink_muted']}; }}")
        self.note_actions_button.setMenu(self.note_actions_menu)
        self._note_action_buttons = []
        for name, button in (('read_book', self.read_book_button), ('read_section', self.read_section_button),
                             ('preview', self.preview_button), ('confirm', self.confirm_button),
                             ('cards', self.cards_button), ('clear', self.clear_button)):
            action = self.note_actions_menu.addAction(button.text())
            action.triggered.connect(lambda _checked=False, target=button: target.click())
            setattr(self, f'note_{name}_action', action)
            self._note_action_buttons.append((action, button))
        self.note_actions_menu.aboutToShow.connect(self._controls)
        self.actions.addWidget(self.note_actions_button)
        root.addLayout(self.actions)
        self.status = label("教师笔记只作本次备课参考；原书和正式教材目录保留。")
        root.addWidget(self.status)
        if hasattr(self.tasks, 'task_finished'):
            self.tasks.task_finished.connect(self._finished)
        if hasattr(self.tasks, 'task_cancelled'):
            self.tasks.task_cancelled.connect(self._cancelled)
        self.tabs.currentChanged.connect(self._update_reading_layout)
        self._controls()

    @staticmethod
    def _scroll(widget):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame); scroll.setWidget(widget)
        return scroll

    def _selection(self):
        return {key: self._book[key] for key in ("source_id", "revision")}

    def set_book(self, book):
        self.deactivate()
        self._note_editor_owner = None
        self._book = deepcopy(book)
        self._view = None
        self._section_id = self._note_last_sections.get(book['source_id'])
        self._checked.clear()
        self._clear_crop_pages()
        with QSignalBlocker(self.tree), QSignalBlocker(self.note_list):
            self.tree.clear();self.note_list.clear()
        self._invalidate()
        self._clear_outline()
        selection = self._selection()
        self._run("读取个人教材章节与笔记",
            lambda: self.facade.personal_textbook_study().book_study(selection), self._loaded)

    def _run(self, title, operation, ready, *, stale_failure=None):
        if self._busy or self._closed:
            return
        epoch = self._epoch
        self._busy = True;self._controls();self.busy_changed.emit(True)
        def done(value):
            if not self._closed and epoch == self._epoch:
                self._task_id = None;self._busy = False
                ready(value)
                self._controls();self.busy_changed.emit(False)
                if self._pending_resume is not None:
                    self._apply_resume()
        def failed(message):
            if not self._closed and epoch == self._epoch:
                self._task_id = None;self._busy = False
                if title == '保存个人教材笔记' and self._note_save_token is not None:
                    self._note_edits.cancel_save(self._note_save_token)
                    self._note_save_token = None
                if stale_failure is not None and stale_failure():
                    self._controls();self.busy_changed.emit(False)
                    return
                if "目录" in title:
                    self._clear_outline()
                self._invalidate();self._controls();self.busy_changed.emit(False)
                set_status(self.status, "error", message)
        try:
            self._task_id = self.tasks.submit(title, operation, on_success=done, on_failure=failed)
        except RuntimeError:
            self._busy = False;self._controls();self.busy_changed.emit(False)
            set_status(self.status, "error", "当前窗口已停止接收任务，请重新打开教材页。")

    def _finished(self, task_id):
        if task_id == self._relation_task:
            self._invalidate_knowledge_links()
            self._controls()
            return
        if task_id == self._task_id:
            self._task_id = None;self._busy = False
            self._controls();self.busy_changed.emit(False)

    def _cancelled(self, task_id, _label):
        if task_id == self._relation_task:
            self._invalidate_knowledge_links()
            self._controls()
            return
        if task_id == self._task_id and not self._closed:
            if self._note_save_token is not None:
                self._note_edits.cancel_save(self._note_save_token)
                self._note_save_token = None
            self._epoch += 1
            self._invalidate()
            self._clear_outline()
            set_status(self.status, "attention", "已停止；此前保存的章节和笔记保留。")

    def _loaded(self, view):
        self._capture_note_editor()
        previous = self._note_edits.get(self._note_editor_owner) if self._note_editor_owner else None
        if (previous is not None and previous.dirty and previous.key[0] == view['book']['source_id']
                and self._pending_note_key is None):
            self._pending_note_key = previous.key
        self._note_editor_owner = None
        self._note_edits.reconcile(view)
        self._note_views[view['book']['source_id']] = deepcopy(view)
        self._view = deepcopy(view)
        self._book = deepcopy(view["book"])
        available = {row["note_id"]: row["revision"] for row in view["notes"]}
        self._checked = {key: row for key, row in self._checked.items() if available.get(key) == row["revision"]}
        self._invalidate();self._clear_reader()
        self._clear_outline()
        pending = self._pending_note_key
        old = (pending[2] if pending and pending[0] == self._book['source_id'] else
               self._note_last_sections.get(self._book['source_id'], self._section_id))
        self._rendering = True;self.tree.clear()
        selected = None
        groups = {}
        for row in view["sections"]:
            text = row["title"] + f" · PDF {row['pdf_start']}—{row['pdf_end']}"
            if row.get("origin") == "auto_outline" and not row["range_confirmed"]:
                text += " · 待核对"
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.ItemDataRole.UserRole, deepcopy(row))
            path = tuple(row.get("outline_evidence", {}).get("group_path", []))
            if path:
                if path not in groups:
                    group = QTreeWidgetItem([" / ".join(path)])
                    group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.tree.addTopLevelItem(group);group.setExpanded(True)
                    groups[path] = group
                groups[path].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            if row["section_id"] == old:
                selected = item
        first = self.tree.topLevelItem(0) if self.tree.topLevelItemCount() else None
        if first and first.childCount():
            first = first.child(0)
        self.tree.setCurrentItem(selected or (None if pending else first))
        self._rendering = False
        self._section_changed()
        if not view["sections"] and self._note_editor_owner is None:
            self._new_section()
        self.changed.emit({**self._book, "study_revision": view["study_revision"],
                           "section_count": len(view["sections"]), "note_count": len(view["notes"])})
        set_status(self.status, "success", "原书已保存。章节和知识笔记供你核对、选取本次备课参考。")

    def _current_section(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _open_section_materials(self):
        section = self._current_section()
        if (self._busy or self._closed or self._preparation_busy or self._book is None
                or self._view is None or section is None):
            return
        book_key = 'personal:' + self._book['source_id']
        self.section_materials_requested.emit({
            'book_key': book_key,
            'section_filter_key': book_key + ':' + section['section_id'],
            'selection': {'mode': 'personal', 'selection': self._selection(),
                          'section_id': section['section_id'],
                          'study_revision': self._view['study_revision']}})

    def _clear_outline(self):
        self._outline_preview = None
        self.outline_tree.clear()
        self.outline_summary.setText("识别目录后可逐项定位原页；勾选保存将追加目录候选。")

    def _detect_outline(self):
        if self._busy or not self._view:
            return
        selection = self._selection()
        self._clear_outline()
        def ready(preview):
            from ..desktop_textbook_auto_outline import METHOD_LABELS
            self._outline_preview = deepcopy(preview)
            self._rendering = True
            groups = {}
            for row in preview["rows"]:
                path = tuple(row["evidence"]["group_path"])
                item = QTreeWidgetItem([row["title"], f"{row['pdf_start']}—{row['pdf_end']}",
                    "已保存" if row["already_saved"] else METHOD_LABELS[row["evidence"]["method"]]])
                item.setData(0, Qt.ItemDataRole.UserRole, deepcopy(row))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked if row["already_saved"] else Qt.CheckState.Checked)
                if row["already_saved"]:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                if path:
                    if path not in groups:
                        group = QTreeWidgetItem([" / ".join(path)])
                        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                        self.outline_tree.addTopLevelItem(group);group.setExpanded(True)
                        groups[path] = group
                    groups[path].addChild(item)
                else:
                    self.outline_tree.addTopLevelItem(item)
            self.outline_tree.resizeColumnToContents(0)
            self.outline_tree.resizeColumnToContents(1)
            self._rendering = False
            self.outline_summary.setText(f"识别出 {len(preview['rows'])} 个目录候选。已有整理保留。\n"
                                         + "\n".join(preview["notices"]))
            self.tabs.setCurrentIndex(self._outline_tab)
            first = self.outline_tree.topLevelItem(0)
            if first and first.childCount():
                first = first.child(0)
            self.outline_tree.setCurrentItem(first)
            set_status(self.status, "success" if preview["rows"] else "attention",
                       "目录识别完成，尚未保存。可定位原页后保存所选候选。" if preview["rows"] else
                       "没有可定位的目录；原书仍可阅读，可在章节整理中手工添加。")
        self._run("识别个人教材目录", lambda: self.facade.personal_textbook_study().preview_outline(selection), ready)

    def _outline_selected(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator = QTreeWidgetItemIterator(self.outline_tree)
        chosen = []
        while iterator.value():
            item = iterator.value()
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row and item.checkState(0) == Qt.CheckState.Checked and not row["already_saved"]:
                chosen.append(row["candidate_id"])
            iterator += 1
        return chosen

    def _save_outline(self):
        selected = self._outline_selected()
        if self._busy or self._outline_preview is None or not selected:
            return
        selection, preview = self._selection(), deepcopy(self._outline_preview)
        current = self._current_section()
        chapter_values = (self.section_title.text(), self.start.value(), self.end.value())
        chapter_pending = (self._section_id is None or current is None
            or chapter_values != (current["title"], current["pdf_start"], current["pdf_end"]))
        chapter_pending = chapter_pending or self.range_confirmed.isChecked()
        draft = {"selected_id": current["section_id"] if current else None,
                 "chapter_pending": chapter_pending, "section_id": self._section_id,
                 "chapter_values": chapter_values, "range_confirmed": self.range_confirmed.isChecked()}
        self._capture_note_editor()
        def saved(value):
            if draft["selected_id"]:
                self._section_id = draft["selected_id"]
            self._loaded(value["view"])
            if draft["chapter_pending"]:
                self._section_id = draft["section_id"]
                title, start, end = draft["chapter_values"]
                self.section_title.setText(title);self.start.setValue(start);self.end.setValue(end)
                self.range_confirmed.setChecked(draft["range_confirmed"])
            self._read(True)
            set_status(self.status, "success", f"已追加 {value['added']} 个目录候选；范围待核对，已有章节和笔记保留。")
        self._run("保存个人教材自动目录",
                  lambda: self.facade.personal_textbook_study().save_outline(selection, preview, selected), saved)

    def _read_outline_page(self):
        item = self.outline_tree.currentItem()
        row = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if row and not self._busy:
            self._read(False, initial_page=row["pdf_start"])

    def _section_changed(self, *_):
        if self._rendering:
            return
        self._capture_note_editor()
        self._invalidate_knowledge_links()
        row = self._current_section()
        self._clear_reader()
        self._section_id = row["section_id"] if row else None
        if row and self._book:
            self._note_last_sections[self._book['source_id']] = row['section_id']
        self._rendering = True
        self.section_title.setText(row["title"] if row else "")
        maximum = self._book["page_count"] if self._book else 3000
        self.crop_page.setMaximum(maximum)
        self.crop_page.setValue(row['pdf_start'] if row else 1)
        self.crop_origin.setText((self._book['source_name'] if self._book else '请选择已导入的教材。')
            + f" · 共{maximum}个PDF页；印刷页码待核对。")
        for spin in (self.start, self.end):
            spin.setMaximum(maximum)
        self.start.setValue(row["pdf_start"] if row else 1)
        self.end.setValue(row["pdf_end"] if row else 1)
        self.range_confirmed.setChecked(False)
        self.note_list.clear()
        for note in (self._view or {}).get("notes", []):
            if row and note["section_id"] == row["section_id"]:
                item = QListWidgetItem(note["title"] + f" · PDF {note['pdf_page']}")
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(note))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if note["note_id"] in self._checked else Qt.CheckState.Unchecked)
                self.note_list.addItem(item)
        key, self._pending_note_key = self._pending_note_key, None
        if key is None and row and self._view:
            key = (self._note_edits.last_key(self._view, row['section_id'])
                   or self._note_edits.last_key_for_source(self._book['source_id'], row['section_id']))
        self._rendering = False
        if key and self._note_edits.get(key) is not None:
            draft = self._note_edits.get(key)
            if (key[:2] == (self._book['source_id'], self._book['revision']) and row
                    and (key[3] == NEW_NOTE or any(note['note_id'] == key[3] for note in self._view['notes']))):
                draft = self._note_edits.open(self._view, row['section_id'], None if key[3] == NEW_NOTE else key[3])
            self._render_note_draft(draft)
        elif row and self._view:
            self._render_note_draft(self._note_edits.open(self._view, row['section_id']))
        else:
            self._render_note_draft(None)
        self._controls()

    def _chapter_edited(self, *_):
        if not self._rendering:
            self.range_confirmed.setChecked(False)
        self._controls()

    def _new_section(self):
        self._section_id = None
        self.section_title.clear();self.start.setValue(1);self.end.setValue(1)
        self.range_confirmed.setChecked(False)
        self.tabs.setCurrentIndex(1);self.section_title.setFocus();self._controls()

    def _save_section(self):
        if not self._view or not self.range_confirmed.isChecked() or self._busy:
            return
        selection, revision = self._selection(), self._view["study_revision"]
        fields = dict(title=self.section_title.text(), pdf_start=self.start.value(), pdf_end=self.end.value(),
                      range_confirmed=True, section_id=self._section_id)
        old_ids = {row['section_id'] for row in self._view['sections']}
        def saved(view):
            added = [row['section_id'] for row in view['sections'] if row['section_id'] not in old_ids]
            if added:
                self._section_id = added[0]
            self._loaded(view)
        self._run("保存个人教材章节", lambda: self.facade.personal_textbook_study().save_section(selection, revision, **fields), saved)

    def _new_note(self, *, switch=True):
        if self._rendering:
            return
        self._capture_note_editor()
        self._invalidate_knowledge_links()
        row = self._current_section()
        if not row or not self._view:
            return
        self._render_note_draft(self._note_edits.open(self._view, row['section_id']))
        if switch:
            self.tabs.setCurrentIndex(2);self.note_title.setFocus()
        self._controls()

    def _note_changed(self, item, *_):
        if self._rendering or item is None:
            return
        self._capture_note_editor()
        self._invalidate_knowledge_links()
        row = item.data(Qt.ItemDataRole.UserRole)
        self._render_note_draft(self._note_edits.open(self._view, row['section_id'], row['note_id']))
        self._controls()

    def _capture_note_editor(self):
        """Capture the frozen editor owner, never the newly selected tree item."""
        if self._rendering or self._note_editor_owner is None:
            return None
        draft = self._note_edits.get(self._note_editor_owner)
        if draft is None:
            return None
        shown = (self.note_title.text(), self.note_text.toPlainText(), self.note_page.value())
        original = (draft.fields.title, draft.fields.text, draft.fields.pdf_page)
        # Qt normalizes paragraph separators when rendering. Retain the exact
        # stored field until that particular control actually changes.
        fields = tuple(value if self._note_rendered_fields is None or value != self._note_rendered_fields[index]
                       else original[index] for index, value in enumerate(shown))
        draft = self._note_edits.update(draft.key, title=fields[0], text=fields[1], pdf_page=fields[2],
                                        confirmed=self.note_confirmed.isChecked())
        self._note_rendered_fields = shown
        return draft

    def _render_note_draft(self, draft):
        self._rendering = True
        try:
            self._note_editor_owner = draft.key if draft else None
            self._note_id = draft.key[3] if draft and draft.key[3] != NEW_NOTE else None
            self.note_list.setCurrentRow(-1)
            for index in range(self.note_list.count()):
                if self.note_list.item(index).data(Qt.ItemDataRole.UserRole)['note_id'] == self._note_id:
                    self.note_list.setCurrentRow(index)
                    break
            self.note_title.setText(draft.fields.title if draft else '')
            self.note_text.setPlainText(draft.fields.text if draft else '')
            page = draft.fields.pdf_page if draft else 1
            low, high = (draft.pdf_start, draft.pdf_end) if draft else (1, 3000)
            # A changed chapter must never silently clamp a teacher's old page.
            self.note_page.setRange(min(low, page), max(high, page))
            self.note_page.setValue(page)
            self.note_confirmed.setChecked(bool(draft and draft.confirmed))
            self._note_rendered_fields = (self.note_title.text(), self.note_text.toPlainText(), self.note_page.value())
        finally:
            self._rendering = False

    def _note_problem(self, draft):
        if draft is None:
            return '请选择已保存章节后添加笔记。'
        if draft.conflict:
            return draft.conflict
        if self._book is None or draft.key[:2] != (self._book['source_id'], self._book['revision']):
            return '原书版本已变化，旧稿已保留。请核对后取消本次修改，再编辑当前版本。'
        if not draft.pdf_start <= draft.fields.pdf_page <= draft.pdf_end:
            return f'原页 PDF {draft.fields.pdf_page} 已超出当前章节 {draft.pdf_start}—{draft.pdf_end} 页；输入已保留，请先核对。'
        return None

    def _note_confirmation_changed(self, *_):
        if self._rendering:
            return
        self._capture_note_editor()
        self._controls()

    def _note_edited(self, *_):
        if self._rendering:
            return
        draft = self._capture_note_editor()
        self._invalidate_knowledge_links()
        with QSignalBlocker(self.note_confirmed):
            self.note_confirmed.setChecked(bool(draft and draft.confirmed))
        self._invalidate();self._controls()

    def _cancel_note_edits(self):
        if self._busy or self._note_editor_owner is None:
            return
        self._capture_note_editor()
        draft = self._note_edits.discard(self._note_editor_owner, self._view)
        self._render_note_draft(draft)
        self._invalidate_knowledge_links();self._invalidate();self._controls()
        set_status(self.status, 'success', '已取消当前笔记的本次修改；其他未保存笔记保留。')

    def _save_note(self):
        draft = self._capture_note_editor()
        if (self._busy or not self._view or draft is None or not draft.confirmed
                or self._note_problem(draft) or not self.save_note.isEnabled()):
            return
        try:
            token = self._note_edits.freeze_save(draft.key, self._view)
        except NoteEditConflict as exc:
            set_status(self.status, 'attention', str(exc));self._controls()
            return
        self._note_save_token = token
        epoch = self._epoch
        fields = dict(section_id=token.section_id, title=token.fields.title, text=token.fields.text,
                      pdf_page=token.fields.pdf_page, teacher_confirmed=True, note_id=token.note_id)
        old_ids = {row['note_id'] for row in self._view['notes']}
        def saved(view):
            added = [row['note_id'] for row in view['notes'] if row['note_id'] not in old_ids
                     and all(row[name] == fields[name] for name in ('section_id', 'title', 'text', 'pdf_page'))]
            desired = fields['note_id'] or (added[0] if len(added) == 1 else None)
            self._capture_note_editor()
            current = self._note_edits.acknowledge_save(token, view, note_id=desired)
            self._note_save_token = None
            self._note_editor_owner = None
            self._pending_note_key = current.key if current else None
            self._loaded(view)
        def operation():
            if (self._closed or epoch != self._epoch or self._book is None
                    or self._selection() != token.selection or not self._note_edits.is_save_active(token)
                    or not self._note_edits.mark_save_started(token)):
                raise ValueError('本次笔记保存已取消；未保存输入仍可返回核对。')
            return self.facade.personal_textbook_study().save_note(token.selection, token.study_revision, **fields)
        self._run("保存个人教材笔记", operation, saved)

    def _check_changed(self, item):
        if self._rendering:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked[row["note_id"]] = {key: row[key] for key in ("note_id", "revision")}
        else:
            self._checked.pop(row["note_id"], None)
        self._invalidate();self._controls()

    def _knowledge(self):
        section = self._current_section()
        if not self._view or not section or self._busy: return
        from .textbook_knowledge_dialog import TextbookKnowledgeDialog
        dialog = TextbookKnowledgeDialog(self.facade, self.tasks, self._selection(), self._view, section, self)
        def saved(value):
            self._loaded(value['view'])
            rows = {r['note_id']: r for r in self._view['notes']}
            for note_id in value['note_ids']:
                self._checked[note_id] = {key: rows[note_id][key] for key in ('note_id', 'revision')}
            self._section_changed(); self.tabs.setCurrentIndex(2)
            set_status(self.status, 'success', f"新增{value['added']}条知识笔记，复用{value['reused']}条；已勾选，可预览后加入当前备课。")
        dialog.notes_saved.connect(saved); dialog.exec(); dialog.deleteLater()

    def _clear_selection(self):
        self._checked.clear();self._invalidate()
        self._rendering = True
        for index in range(self.note_list.count()):
            self.note_list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self._rendering = False;self._controls()

    def _key(self):
        return ((self._view or {}).get("study_revision"), tuple(sorted((key, value["revision"]) for key, value in self._checked.items())))

    def set_preparation_context(self, payload, *, dirty=False, busy=False, target_changed=False):
        """The enclosing bookshelf owns the common bar; keep only receipt state."""
        self._preparation_busy = bool(busy)
        if target_changed:
            self._preparation_target_epoch += 1
            self._reference_added_key = self._cards_added_key = None
            self.confirm_button.setText("加入当前备课")
            self.cards_button.setText("知识卡加入课件…")
        self._controls()

    def _invalidate(self):
        self._reference = self._reference_key = None
        self._reference_added_key = None
        self._cards_added_key = None
        self.reference_preview.clear()
        self.reference_details.clear()
        self.confirm_button.setEnabled(False);self.confirm_button.setText("加入当前备课")
        self.cards_button.setEnabled(False);self.cards_button.setText("知识卡加入课件…")
        self._sync_reading_actions()

    def _compile(self, confirm, *, cards=False):
        if self._busy or not self._checked or not self._view:
            return
        self._capture_note_editor()
        dirty = next((key for key in self._note_edits.dirty_keys()
                      if key[0] == self._book['source_id'] and key[3] in self._checked), None)
        if dirty is not None:
            self.locate_note_draft(dirty)
            self._invalidate()
            set_status(self.status, 'attention', '所选笔记有未保存修改。已返回这条笔记，请先保存或取消本次修改，再预览和加入备课。')
            return
        if confirm and self._preparation_busy:
            return
        key = self._key()
        expected = deepcopy(self._reference)
        if confirm and (expected is None or key != self._reference_key):
            return
        if cards and (not confirm or self._cards_added_key == key):
            return
        if confirm and not cards and self._reference_added_key == key:
            return
        target_epoch = self._preparation_target_epoch
        selection, revision, notes = self._selection(), self._view["study_revision"], deepcopy(list(self._checked.values()))
        def ready(value):
            if self._key() != key:
                return
            if confirm:
                if target_epoch != self._preparation_target_epoch:
                    set_status(self.status, "attention", "备课已切换，请再次明确加入当前备课。")
                    return
                reference = value['reference'] if cards else value
                if reference != expected:
                    self._invalidate();set_status(self.status, "attention", "参考内容已变化，请重新预览。")
                    return
                if cards:
                    self.cards_requested.emit(deepcopy(value['cards']))
                else:
                    self.reference_requested.emit(deepcopy(value))
            else:
                self._reference, self._reference_key = deepcopy(value), key
                rows = {row['note_id']: row for row in self._view['notes']}
                candidates = any(rows[chosen['note_id']].get('origin') == 'teacher_adapted_candidate' for chosen in notes)
                lines = ["本次将加入的知识笔记（使用前请对照全部依据原页）" if candidates else "本次将加入的手工笔记（使用前请对照原页）", self._book['source_name'],
                         "以下位置为PDF文件页序；印刷页码待核对。"]
                for chosen in notes:
                    row = rows[chosen['note_id']]
                    pages = row.get('source_evidence', {}).get('pdf_pages', [row['pdf_page']])
                    lines.extend(['', row['title'], row['text'], '原页：PDF ' + '、'.join(map(str, pages)) + '页'])
                self.reference_preview.setPlainText('\n'.join(lines))
                self.reference_details.setPlainText(value["materials"])
                self.tabs.setCurrentIndex(3)
                set_status(self.status, "success", "已预览所选笔记；核对后可加入备课参考，或作为知识卡加入课件编排。")
        def operation():
            reference = self.facade.personal_textbook_reference(selection, revision, notes)
            if not cards:
                return reference
            if reference != expected:
                raise ValueError("参考内容已变化，请重新预览。")
            from ..desktop_composition_sources import import_knowledge_cards
            selections = [{'kind': 'personal', 'book': selection, 'study_revision': revision, 'note': note}
                          for note in notes]
            return {'reference': reference, 'cards': import_knowledge_cards(self.facade, selections)}
        def stale_failure():
            if confirm and target_epoch != self._preparation_target_epoch:
                set_status(self.status, "attention", "备课已切换，请再次明确加入当前备课。")
                return True
            return False
        self._run("核对个人教材备课参考", operation, ready, stale_failure=stale_failure)

    def cards_appended(self):
        self._cards_added_key = self._key()
        self.cards_button.setText("本次知识卡已加入")
        self.cards_button.setEnabled(False)
        self._sync_reading_actions()
        set_status(self.status, "success", "所选知识卡已加入当前课件；可编排内容，并保存或导出教案、PPT和学习单。")

    def cards_rejected(self):
        set_status(self.status, "attention", "当前课件暂不能接收知识卡；待操作结束后可以重试。")

    def reference_appended(self):
        self._reference_added_key = self._key()
        self.confirm_button.setText("本次参考已加入")
        self.confirm_button.setEnabled(False)
        self._sync_reading_actions()
        set_status(self.status, "success", "所选笔记已追加到备课；请保存草稿或继续教学环节设计。")

    def reference_rejected(self):
        set_status(self.status, "attention", "当前备课暂不能接收参考；待操作结束后可以重试。")

    def _open_questions(self):
        section = self._current_section()
        if self._busy or self._closed or self._book is None or self._view is None or section is None:
            return
        request = {'mode': 'personal', 'selection': self._selection(),
                   'section_id': section['section_id'], 'study_revision': self._view['study_revision']}
        def ready(value):
            current = self._current_section()
            if (current is not None and current['section_id'] == request['section_id']
                    and self._view['study_revision'] == request['study_revision']):
                self.questions_requested.emit(deepcopy(value))
        self._run('核对本节选题范围', lambda: self.facade.textbook_question_context(request), ready)

    def _saved_knowledge_selection(self):
        if self._book is None or self._view is None or self._note_id is None:
            return None
        item, section = self.note_list.currentItem(), self._current_section()
        row = next((value for value in self._view['notes'] if value['note_id'] == self._note_id), None)
        if (item is None or section is None or row is None
                or item.data(Qt.ItemDataRole.UserRole) != row or row['section_id'] != section['section_id']
                or self.note_title.text() != row['title'] or self.note_text.toPlainText() != row['text']
                or self.note_page.value() != row['pdf_page']):
            return None
        return {'kind': 'personal', 'book': self._selection(), 'study_revision': self._view['study_revision'],
                'note': {key: row[key] for key in ('note_id', 'revision')}}

    def _knowledge_link_identity(self):
        selection = self._saved_knowledge_selection()
        if selection is None:
            return None
        return {'selection': selection, 'book': deepcopy(self._book),
                'section': deepcopy(self._current_section()),
                'row': deepcopy(self.note_list.currentItem().data(Qt.ItemDataRole.UserRole))}

    def _invalidate_knowledge_links(self):
        self._relation_serial += 1
        task, self._relation_task = self._relation_task, None
        if task is not None:
            if task != 'starting':
                self.tasks.cancel(task)
            self._busy = False
            self.busy_changed.emit(False)

    def _open_knowledge_links(self):
        if self._closed or self._busy:
            return
        identity = self._knowledge_link_identity()
        if identity is None:
            return
        from ..desktop_knowledge_question_links import KnowledgeQuestionLinks
        service = KnowledgeQuestionLinks(self.facade)
        self._relation_serial += 1
        serial, epoch = self._relation_serial, self._epoch
        self._relation_task = 'starting'
        self._busy = True
        self._controls();self.busy_changed.emit(True)
        set_status(self.status, 'loading', '正在读取已保存知识笔记的原内容与版本…')

        def current():
            return (not self._closed and epoch == self._epoch and serial == self._relation_serial
                    and identity == self._knowledge_link_identity())

        def guard():
            if not current():
                raise ValueError('教材、已保存笔记或编辑内容已变化，请从当前笔记重新打开关联例题。')

        def release():
            self._relation_task = None
            self._busy = False
            self._controls();self.busy_changed.emit(False)

        def ready(context):
            if not current():
                return
            self._relation_task = None
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
                if not self._closed and epoch == self._epoch:
                    release()

        def failed(message):
            if not current():
                return
            release()
            set_status(self.status, 'error', message)
        try:
            task = self.tasks.submit('读取已保存笔记的关联入口',
                lambda: service.knowledge_context(identity['selection']), on_success=ready, on_failure=failed)
            if self._relation_task == 'starting' and current():
                self._relation_task = task
        except RuntimeError as exc:
            failed(str(exc))

    def _controls(self, *_):
        usable = self._book is not None and self._view is not None and not self._busy and not self._closed
        self.tree.setEnabled(usable)
        self.tabs.setEnabled(not self._busy and not self._closed)
        self.read_book_button.setEnabled(usable)
        self.search_book_button.setEnabled(usable and callable(getattr(self.facade, 'personal_textbook_search', None)))
        self.read_section_button.setEnabled(usable and self._current_section() is not None)
        self.questions_button.setEnabled(usable and self._current_section() is not None)
        self.section_materials_button.setEnabled(usable and not self._preparation_busy and self._current_section() is not None)
        self.save_section.setEnabled(usable and self.range_confirmed.isChecked() and bool(self.section_title.text().strip()) and self.start.value() <= self.end.value())
        self.new_note.setEnabled(usable and self._current_section() is not None)
        self.knowledge_button.setEnabled(usable and self._current_section() is not None)
        self.knowledge_links_button.setEnabled(usable and self._saved_knowledge_selection() is not None)
        draft = self._note_edits.get(self._note_editor_owner) if self._note_editor_owner else None
        problem = self._note_problem(draft)
        self.save_note.setEnabled(bool(usable and draft is not None and not problem and draft.confirmed
            and 0 < len(draft.fields.title) <= 160 and draft.fields.title.strip()
            and 0 < len(draft.fields.text) <= 1200 and draft.fields.text.strip()))
        self.cancel_note_edits.setEnabled(bool(usable and draft is not None and (draft.dirty or problem)))
        message = '未保存修改 · 已暂存在本次教材会话，切换后可继续编辑。' if draft and draft.dirty else '当前笔记没有未保存修改。'
        if problem:
            message += '\n' + problem
        self.note_edit_status.setText(message)
        saved = draft.saved_fields if draft and problem else None
        self.note_saved_version.setVisible(saved is not None)
        if saved is not None:
            self.note_saved_version.setPlainText(f'当前已保存版本：{saved.title} · PDF {saved.pdf_page}\n{saved.text}')
        selected_note = next((r for r in (self._view or {}).get('notes', []) if r['note_id'] == self._note_id), None)
        adapted = selected_note is not None and selected_note.get('origin') == 'teacher_adapted_candidate'
        self.save_note.setText('保存知识笔记修订' if adapted else '保存手工笔记')
        self.note_page.setEnabled(usable and not adapted)
        self.note_confirmed.setText('我已对照全部依据原页核对这条知识笔记' if adapted else '我已对照原页核对这条手工笔记')
        self.preview_button.setEnabled(usable and bool(self._checked))
        self.confirm_button.setEnabled(usable and not self._preparation_busy and self._reference is not None
                                       and self._reference_key == self._key() and self._reference_added_key != self._key())
        self.cards_button.setEnabled(usable and self._reference is not None and self._reference_key == self._key()
                                     and not self._preparation_busy and self._cards_added_key != self._key())
        self.clear_button.setEnabled(usable and bool(self._checked))
        self.backup_button.setEnabled(usable)
        self.detect_outline_button.setEnabled(usable)
        self.crop_page.setEnabled(usable)
        self.crop_read_button.setEnabled(usable)
        self.crop_button.setEnabled(usable and not self._preparation_busy)
        self.queue_page_button.setEnabled(usable)
        self.crop_queue.setEnabled(usable)
        self.remove_page_button.setEnabled(usable and bool(self._crop_pages))
        self.clear_pages_button.setEnabled(usable and bool(self._crop_pages))
        self.batch_crop_button.setEnabled(usable and not self._preparation_busy and bool(self._crop_pages))
        self.batch_crop_button.setText(f'逐张核对本批图表（{len(self._crop_pages)}页）…')
        self.outline_save_button.setEnabled(usable and self._outline_preview is not None and bool(self._outline_selected()))
        item = self.outline_tree.currentItem()
        self.outline_read_button.setEnabled(usable and item is not None and item.data(0, Qt.ItemDataRole.UserRole) is not None)
        self.selection.setText(f"本次勾选 {len(self._checked)} 条知识笔记（跨章节保留）。")
        self._update_reading_layout()

    def _current_reader_crop_matches(self, reader, epoch, request):
        """Keep a reader action tied to its current source, scope and page."""
        if (self._busy or self._preparation_busy or self._closed or self._view is None or self._book is None
                or reader is None or reader is not self._reader or epoch != self._epoch
                or not isinstance(request, dict) or type(request.get("pdf_page")) is not int
                or not isinstance(request.get("pdf_pages"), list)
                or any(type(page) is not int for page in request["pdf_pages"])
                or reader.current_page_crop_request() != request
                or self._selection() != {key: request.get(key) for key in ("source_id", "revision")}
                or {key: self._view["book"].get(key) for key in ("source_id", "revision", "page_count")}
                != {key: self._book.get(key) for key in ("source_id", "revision", "page_count")}):
            return False
        section_id = request.get("section_id")
        if section_id is None:
            pages = list(range(1, self._book["page_count"] + 1))
        else:
            section = self._current_section()
            if (section is None or section["section_id"] != section_id
                    or request.get("study_revision") != self._view["study_revision"]):
                return False
            saved = next((row for row in self._view["sections"] if row["section_id"] == section_id), None)
            if saved is None or saved != section:
                return False
            pages = list(range(section["pdf_start"], section["pdf_end"] + 1))
        return request.get("pdf_pages") == pages and request.get("pdf_page") in pages

    def _crop_current_reader_page(self, reader, epoch, request):
        if not self._current_reader_crop_matches(reader, epoch, request):
            return
        # Reading alone does not move the separate crop selector or select a
        # material. Synchronize it only after this explicit reader action.
        self.crop_page.setValue(request["pdf_page"])
        self._crop_page(reader_context=(reader, epoch, deepcopy(request)))

    def _crop_page(self, *, reader_context=None):
        if self._busy or self._preparation_busy or self._closed or self._view is None or self._book is None:
            return
        if reader_context is not None and not self._current_reader_crop_matches(*reader_context):
            return
        from ..desktop_personal_textbook_crops import book_page_selection
        from .textbook_preparation_crop_dialog import TextbookPreparationCropDialog
        selection = book_page_selection(self._book, self.crop_page.value())
        epoch, target_epoch = self._epoch, self._preparation_target_epoch
        dialog = TextbookPreparationCropDialog(self.facade, self.tasks, selection, self,
                                               allow_composition_handoff=True)
        if (dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection_request is not None
                and not self._closed and epoch == self._epoch
                and not self._preparation_busy and target_epoch == self._preparation_target_epoch
                and (reader_context is None or self._current_reader_crop_matches(*reader_context))
                and selection == book_page_selection(self._book, self.crop_page.value())):
            if getattr(dialog, 'handoff_mode', 'assets') == 'composition':
                self.composition_crops_requested.emit([deepcopy(dialog.selection_request)])
            else:
                self.image_requested.emit(deepcopy(dialog.selection_request))
        dialog.deleteLater()

    def _queue_crop_page(self):
        if self._busy or self._closed or self._view is None or self._book is None:
            return
        from ..desktop_preparation_images import MAX_IMAGES
        page = self.crop_page.value()
        if page in self._crop_pages:
            set_status(self.status, 'attention', f'PDF第{page}页已在本批待选页中。')
            return
        if len(self._crop_pages) >= MAX_IMAGES:
            set_status(self.status, 'attention', f'一次最多核对{MAX_IMAGES}页，请分批选用。')
            return
        self._crop_pages.append(page)
        self._crop_queue_epoch += 1
        self._render_crop_pages()

    def _render_crop_pages(self):
        self.crop_queue.clear()
        for page in self._crop_pages:
            item = QListWidgetItem(f'PDF第{page}页 · 尚未框选与核对')
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.crop_queue.addItem(item)
        self.crop_queue.setCurrentRow(0)
        self._controls()

    def _remove_crop_page(self):
        if self._busy or self._closed:
            return
        item = self.crop_queue.currentItem()
        if item is not None:
            self._crop_pages.remove(item.data(Qt.ItemDataRole.UserRole))
            self._crop_queue_epoch += 1
            self._render_crop_pages()

    def _clear_crop_pages(self):
        if self._busy or self._closed:
            return
        if self._crop_pages: self._crop_queue_epoch += 1
        self._crop_pages.clear()
        self._render_crop_pages()

    def _crop_batch(self):
        if self._busy or self._preparation_busy or self._closed or not self._crop_pages or self._view is None or self._book is None:
            return
        from ..desktop_personal_textbook_crops import book_page_selection
        from .textbook_batch_crop_dialog import TextbookBatchCropDialog
        pages, book, epoch = list(self._crop_pages), deepcopy(self._book), self._epoch
        target_epoch, queue_epoch = self._preparation_target_epoch, self._crop_queue_epoch
        choices = [{'selection': book_page_selection(book, page),
                    'label': f"{book['source_name']} · PDF第{page}页"} for page in pages]
        dialog = TextbookBatchCropDialog(self.facade, self.tasks, choices, self,
                                         allow_composition_handoff=True)
        if (dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection_requests is not None
                and not self._closed and epoch == self._epoch and book == self._book and pages == self._crop_pages):
            if (not self._preparation_busy and target_epoch == self._preparation_target_epoch
                    and queue_epoch == self._crop_queue_epoch):
                if getattr(dialog, 'handoff_mode', 'assets') == 'composition':
                    self.composition_crops_requested.emit(deepcopy(dialog.selection_requests))
                else:
                    self.images_requested.emit(deepcopy(dialog.selection_requests))
        dialog.deleteLater()

    def _backup(self):
        if self._busy or self._book is None:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "新建个人教材与笔记备份", "个人教材与笔记.zip", "教材备份 (*.zip)")
        if not destination:
            return
        self._run("备份个人教材与笔记", lambda: self.facade.export_textbook_bundle(destination),
            lambda result: set_status(self.status, "success", f"已备份全部 {result['books']} 本个人教材、章节和笔记到所选文件；原件保留。"))

    def _read(self, section, *, initial_page=None):
        if self._busy or self._book is None:
            return
        from .textbook_source_dialog import TextbookSourceDialog
        selection = self._selection()
        if initial_page is not None:
            selection["initial_pdf_page"] = initial_page
        if section:
            row = self._current_section()
            if row is None:
                return
            selection.update(section_id=row["section_id"], study_revision=self._view["study_revision"])
        self._clear_reader()
        self._reader = TextbookSourceDialog(self.facade, selection, self.reading,
                                            tasks=self.tasks, reading_mode="book", embedded=True, remember_position=True,
                                            allow_current_page_crop=True)
        reader, epoch = self._reader, self._epoch
        reader.current_page_crop_requested.connect(
            lambda request: self._crop_current_reader_page(reader, epoch, request))
        self._reader.position_saved.connect(self.position_saved)
        self._reader.reading_state_changed.connect(self._update_reading_layout)
        self.reading_prompt.hide();self.reading_box.addWidget(self._reader, 1)
        self._reader.show();self.tabs.setCurrentIndex(0)
        self._update_reading_layout()

    def _open_book_search(self):
        if self._busy or self._closed or self._book is None or self._view is None:
            return
        if self._search_dialog is not None:
            if self._search_dialog._valid():
                self._search_dialog.show(); self._search_dialog.raise_(); self._search_dialog.activateWindow(); return
            self._search_dialog.reject()
        from .personal_textbook_search_dialog import PersonalTextbookSearchDialog
        self._search_serial += 1
        serial, epoch = self._search_serial, self._epoch
        selection, study_revision, section_id = self._selection(), self._view['study_revision'], self._section_id
        def guard():
            if (self._closed or self._busy or epoch != self._epoch or serial != self._search_serial
                    or self._book is None or self._view is None or self._selection() != selection
                    or self._view['study_revision'] != study_revision or self._section_id != section_id):
                raise ValueError('当前教材或阅读范围已变化，请重新打开查找。')
        dialog = PersonalTextbookSearchDialog(self.facade,self.tasks,selection,self._view,self,
            section_id=section_id,guard=guard)
        self._search_dialog = dialog
        def locate(request):
            try: guard()
            except ValueError: return
            if (request.get('selection') != selection or request.get('study_revision') != study_revision
                    or type(request.get('pdf_page')) is not int
                    or not 1 <= request['pdf_page'] <= self._book['page_count']): return
            # Only replace the reader. Teacher note edits, checked notes, crop
            # queue, reference preview and the lesson target remain in place.
            self._read(False,initial_page=request['pdf_page'])
        def closed(*_):
            if self._search_dialog is dialog: self._search_dialog = None
            dialog.deleteLater()
        dialog.page_requested.connect(locate); dialog.finished.connect(closed)
        dialog.show(); dialog.raise_(); dialog.activateWindow()

    def resume_position(self, record):
        selector = record['binding']['selector']
        if (self._closed or self._book is None or selector['mode'] != 'book'
                or self._selection() != {key: selector[key] for key in ('source_id', 'revision')}):
            return
        self._pending_resume = deepcopy(record)
        if not self._busy:
            self._apply_resume()

    def _apply_resume(self):
        record, self._pending_resume = self._pending_resume, None
        if record is None or self._busy or self._view is None:
            return
        selector = record['binding']['selector']
        if self._selection() != {key: selector[key] for key in ('source_id', 'revision')}:
            return
        if selector['section_id'] is None:
            self._read(False)
            return
        if self._view['study_revision'] != selector['study_revision']:
            set_status(self.status, 'attention', '章节整理版本已变化，请重新选择阅读范围。')
            return
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row and row['section_id'] == selector['section_id']:
                self.tree.setCurrentItem(item)
                self._read(True)
                return
            iterator += 1
        set_status(self.status, 'attention', '上次章节已变化，请重新选择阅读范围。')

    def _clear_reader(self):
        if self._reader is not None:
            reader, self._reader = self._reader, None
            self.reading_box.removeWidget(reader);reader.reject();reader.deleteLater()
        self.reading_prompt.show()
        self._directory_preference = None
        self._update_reading_layout()

    def _toggle_directory(self, expanded):
        self._directory_preference = expanded
        self._update_reading_layout()

    def _update_reading_layout(self, *_):
        if not hasattr(self, 'status'):
            return
        reading = self._reader is not None and self._reader._loaded_pages and self.tabs.currentWidget() == self.reading
        compact = self.width() < 720 and reading
        note_editing = self.width() < 720 and self.tabs.currentIndex() == self._notes_tab
        directory = not compact or self._directory_preference is True
        self.tree.setVisible(directory)
        self.introduction.setVisible(not reading)
        self.directory_button.setVisible(compact)
        with QSignalBlocker(self.directory_button):
            self.directory_button.setChecked(directory)
        self.directory_button.setText('收起章节' if directory else '展开章节')
        selected = bool(self._checked)
        compact_actions = compact and selected
        for button in (self.read_book_button, self.read_section_button):
            button.setVisible(not compact and not note_editing)
        for button in (self.preview_button, self.clear_button):
            button.setVisible((not compact or selected) and not compact_actions and not note_editing)
        self.confirm_button.setVisible((not compact or selected) and not note_editing)
        self.cards_button.setVisible(not compact_actions and not note_editing)
        self.reading_actions_button.setVisible(compact_actions)
        self.note_actions_button.setVisible(note_editing)
        # Give the reclaimed action rows to the editing page and its body field.
        # The scroll area keeps status, confirmation, save and cancel reachable.
        self.note_text.setMinimumHeight(180 if note_editing else 90)
        self.note_text.setMaximumHeight(360 if note_editing else 135)
        self.actions.setDirection(QBoxLayout.Direction.LeftToRight if compact_actions or note_editing or self.width() >= 720
                                  else QBoxLayout.Direction.TopToBottom)
        self.selection.setVisible(not compact or selected)
        self._sync_reading_actions()
        self.reading_changed.emit()

    def _sync_reading_actions(self):
        for action, button in ((self.reading_preview_action, self.preview_button),
                               (self.reading_cards_action, self.cards_button),
                               (self.reading_clear_action, self.clear_button)):
            action.setText(button.text())
            action.setEnabled(button.isEnabled())
        self.reading_actions_button.setEnabled(any(action.isEnabled() for action in self.reading_actions_menu.actions()))
        for action, button in self._note_action_buttons:
            action.setText(button.text())
            action.setEnabled(button.isEnabled())
        self.note_actions_button.setEnabled(any(action.isEnabled() for action, _button in self._note_action_buttons))

    def deactivate(self):
        self._capture_note_editor()
        if self._note_save_token is not None:
            self._note_edits.cancel_save(self._note_save_token)
            self._note_save_token = None
        self._search_serial += 1
        if self._search_dialog is not None: self._search_dialog.reject()
        self._invalidate_knowledge_links()
        self._pending_resume = None
        self._epoch += 1
        if self._task_id:
            self.tasks.cancel(self._task_id)
        self._task_id = None;self._busy = False;self.busy_changed.emit(False)
        self._clear_reader();self._invalidate()
        self._clear_outline()

    def resizeEvent(self, event):
        narrow = event.size().width() < 720
        self.splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.splitter.setSizes([100, 400] if narrow else [250, 700])
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.queue_actions.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self._update_reading_layout()
        super().resizeEvent(event)

    def closeEvent(self, event):
        if not self.prepare_close():
            event.ignore()
            return
        self._closed = True;self.deactivate()
        super().closeEvent(event)

    def locate_note_draft(self, key):
        """Return to an exact owner, including drafts whose chapter was removed."""
        self._capture_note_editor()
        if self._note_edits.get(key) is None:
            return False
        self._pending_note_key = key
        if self._view is None or self._view['book']['source_id'] != key[0]:
            self.note_navigation_requested.emit(key)
            if self._book is not None and self._book['source_id'] == key[0] and self._busy:
                self.tabs.setCurrentIndex(2)
                return True
            if self._view is None or self._view['book']['source_id'] != key[0]:
                view = self._note_views.get(key[0])
                if view is None:
                    return False
                self.deactivate()
                self._note_editor_owner = None
                self._loaded(view)
        else:
            from PySide6.QtWidgets import QTreeWidgetItemIterator
            iterator, selected = QTreeWidgetItemIterator(self.tree), None
            while iterator.value():
                item = iterator.value()
                row = item.data(0, Qt.ItemDataRole.UserRole)
                if row and row['section_id'] == key[2]:
                    selected = item
                    break
                iterator += 1
            with QSignalBlocker(self.tree):
                self.tree.setCurrentItem(selected)
            self._section_changed()
        self.tabs.setCurrentIndex(2)
        self.note_text.setFocus()
        return True

    def _confirm_note_close(self, count):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('还有未保存的教材笔记')
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f'有 {count} 条教材笔记尚未保存。关闭后，本次会话暂存的修改将丢失。')
        back = dialog.addButton('返回编辑', QMessageBox.ButtonRole.RejectRole)
        discard = dialog.addButton('放弃未保存修改并关闭', QMessageBox.ButtonRole.DestructiveRole)
        dialog.setDefaultButton(back)
        dialog.setEscapeButton(back)
        dialog.exec()
        return dialog.clickedButton() is discard

    def prepare_close(self):
        self._capture_note_editor()
        dirty = self._note_edits.dirty_keys()
        if not dirty:
            return True
        if not self._confirm_note_close(len(dirty)):
            self.locate_note_draft(dirty[0])
            return False
        # Revoke queued writes before allowing the enclosing window to close.
        self.deactivate()
        self._note_edits.discard_all()
        self._note_editor_owner = None
        return True
