from __future__ import annotations

from base64 import b64decode, b64encode
import sys

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from ..desktop_facade import PRIMARY_NAVIGATION, DesktopWorkbenchFacade
from ..desktop_state import DesktopStateError
from ..desktop_version import DESKTOP_VERSION
from .dialogs import ImportDialog, SettingsDialog
from .home_page import HomePage
from .question_explorer_page import QuestionExplorerPage as LibraryPage
from .tasks import DesktopTaskBridge
from .workflow_pages import StudentPage
from .scan_paper_page import ScanPaperPage as PaperPage
from .studio_preparation import StudioPreparationPage as PreparationPage
from .studio_style import TOKENS, WORKBENCH_STYLE, _build_style as _build_style
from .studio_templates import TemplatePage, TemplatePreviewDialog
from .studio_navigation import CommandPalette, HelpDialog, studio_icon
from .my_work_page import MyWorkPage
from .classroom_page import ClassroomPage
from .textbook_bookshelf_page import TextbookBookshelfPage

ROUTE_ORDER = ("home", "library", "paper", "student", "preparation")
EXTRA_ROUTES = ("textbooks", "templates", "classroom", "mywork")
ALL_ROUTES = ROUTE_ORDER + EXTRA_ROUTES
PAGE_TITLES = {"home": "首页", "library": "题库", "paper": "组卷", "student": "学生分析",
               "preparation": "备课", "textbooks": "教材", "templates": "教学模板", "classroom": "课堂工具", "mywork": "我的备课"}


def install_font_fallbacks() -> str:
    """Compatibility entry for existing source/EXE probes."""
    from .typography import install_ui_font
    return install_ui_font()


class TeacherWorkbenchWindow(QMainWindow):
    def __init__(self, facade: DesktopWorkbenchFacade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = DesktopTaskBridge(self)
        self._font_family = install_font_fallbacks()
        self.setObjectName("TeacherWorkbenchWindow")
        self.setWindowTitle("沪上化学智研台")
        self.setMinimumSize(360, 560)
        self.resize(1360, 920)
        self.setStyleSheet(WORKBENCH_STYLE)
        root_widget = QWidget()
        root_widget.setObjectName("WindowRoot")
        root = QHBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(root_widget)
        self.side_rail = QFrame()
        self.side_rail.setObjectName("SideRail")
        self.side_rail.setFixedWidth(214)
        side = QVBoxLayout(self.side_rail)
        side.setContentsMargins(16, 20, 16, 16)
        side.setSpacing(4)
        self.brand = QLabel("沪上化学智研台")
        self.brand.setObjectName("Brand")
        self.brand_sub = QLabel("上海高中化学 · 教师工作台")
        self.brand_sub.setObjectName("BrandSub")
        side.addWidget(self.brand)
        side.addWidget(self.brand_sub)
        side.addSpacing(16)
        self.rail_sections = []
        section = QLabel("教学工作")
        section.setObjectName("RailSection")
        side.addWidget(section)
        self.rail_sections.append(section)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, label in enumerate(PRIMARY_NAVIGATION):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setIcon(studio_icon(ROUTE_ORDER[index]))
            button.setCheckable(True)
            button.setAccessibleName(label)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, route=ROUTE_ORDER[index]: self.navigate(route))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            side.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        section = QLabel("备课与课堂")
        section.setObjectName("RailSection")
        side.addWidget(section)
        self.rail_sections.append(section)
        self.studio_nav_buttons = {}
        for index, route in enumerate(EXTRA_ROUTES, len(ROUTE_ORDER)):
            button = QPushButton(PAGE_TITLES[route])
            button.setObjectName("NavButton")
            button.setIcon(studio_icon(route))
            button.setCheckable(True)
            button.setAccessibleName(PAGE_TITLES[route])
            button.clicked.connect(lambda _=False, key=route: self.navigate(key))
            self.nav_group.addButton(button, index)
            self.studio_nav_buttons[route] = button
            side.addWidget(button)
        side.addStretch(1)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setIcon(studio_icon("settings"))
        self.settings_button.setAccessibleName("设置")
        self.settings_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.settings_button.clicked.connect(self.open_settings)
        side.addWidget(self.settings_button)
        root.addWidget(self.side_rail)
        work_area = QWidget()
        work = QVBoxLayout(work_area)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(64)
        top = QHBoxLayout(top_bar)
        top.setContentsMargins(24, 10, 24, 10)
        self.top_title = QLabel("首页")
        self.top_title.setObjectName("TopTitle")
        top.addWidget(self.top_title)
        top.addStretch(1)
        self.command_button = QPushButton("搜索功能  Ctrl+K")
        self.command_button.setObjectName("QuietButton")
        self.command_button.clicked.connect(self.open_commands)
        top.addWidget(self.command_button)
        self.help_button = QPushButton("帮助")
        self.help_button.setObjectName("QuietButton")
        self.help_button.clicked.connect(self.open_help)
        top.addWidget(self.help_button)
        self.import_button = QPushButton("导入资料")
        self.import_button.setObjectName("QuietButton")
        self.import_button.setAccessibleName("导入资料")
        self.import_button.setToolTip("导入试卷、教材、讲义或学生作答")
        self.import_button.clicked.connect(self.open_import)
        top.addWidget(self.import_button)
        work.addWidget(top_bar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        self.home_page = HomePage(facade, self.tasks)
        self.library_page = LibraryPage(facade, self.tasks)
        self.paper_page = PaperPage(facade, self.tasks)
        self.student_page = StudentPage(facade, self.tasks)
        self.preparation_page = PreparationPage(facade, self.tasks)
        self.textbook_page = TextbookBookshelfPage(facade, self.tasks)
        self._textbook_context_timer = QTimer(self)
        self._textbook_context_closed = False
        self._textbook_context_timer.setSingleShot(True)
        self._textbook_context_timer.timeout.connect(self._refresh_textbook_preparation_context)
        self.template_page = TemplatePage(facade)
        self.classroom_page = ClassroomPage()
        self.my_work_page = MyWorkPage(facade, self.tasks)
        self.pages = {"home": self.home_page, "library": self.library_page, "paper": self.paper_page,
                      "student": self.student_page, "preparation": self.preparation_page, "textbooks": self.textbook_page,
                      "templates": self.template_page, "classroom": self.classroom_page, "mywork": self.my_work_page}
        for route in ALL_ROUTES:
            self.stack.addWidget(self.pages[route])
        work.addWidget(self.stack, 1)
        root.addWidget(work_area, 1)
        self.home_page.navigate_requested.connect(self.navigate)
        self.home_page.open_requested.connect(self.open_work_record)
        self.home_page.new_requested.connect(self.new_preparation)
        self.home_page.basket_requested.connect(self.open_home_basket)
        self.home_page.preview_requested.connect(self.preview_selected_paper)
        self.template_page.template_requested.connect(self.open_template)
        self.preparation_page.textbook_requested.connect(lambda: self.navigate("textbooks"))
        self.textbook_page.preparation_edit_requested.connect(lambda: self.navigate('preparation'))
        self.textbook_page.preparation_open_requested.connect(self._open_textbook_preparation)
        self.textbook_page.preparation_new_requested.connect(self._new_textbook_preparation)
        self.preparation_page.preparation_target_changed.connect(self._textbook_preparation_changed)
        for widget in (self.preparation_page.topic, self.preparation_page.audience,
                       self.preparation_page.objective, self.preparation_page.materials,
                       self.preparation_page.learning_detail, self.preparation_page.template_detail,
                       self.preparation_page.strategy_detail, self.preparation_page.output_kind,
                       self.preparation_page.route, self.preparation_page.lesson_count,
                       self.preparation_page.lesson_minutes):
            signal = next(getattr(widget, name) for name in
                          ('textChanged', 'currentIndexChanged', 'valueChanged') if hasattr(widget, name))
            signal.connect(self._schedule_textbook_preparation_context)
        handout_requested = getattr(self.preparation_page, "handout_requested", None)
        if handout_requested is not None:
            handout_requested.connect(self._open_handout_materials)
        self.textbook_page.preparation_reference_requested.connect(self._textbook_to_preparation)
        self.textbook_page.preparation_cards_requested.connect(self._textbook_cards_to_preparation)
        self.textbook_page.preparation_image_requested.connect(self._textbook_image_to_preparation)
        self.textbook_page.preparation_images_requested.connect(self._textbook_images_to_preparation)
        self.textbook_page.composition_crops_requested.connect(self._textbook_crops_to_composition)
        self.textbook_page.question_context_requested.connect(self._textbook_questions)
        self.textbook_page.section_materials_requested.connect(self._textbook_section_materials)
        knowledge_question_requested = getattr(self.textbook_page, "knowledge_question_requested", None)
        if knowledge_question_requested is not None:
            knowledge_question_requested.connect(self._linked_question_to_preparation)
        self.my_work_page.navigate_requested.connect(self.navigate)
        self.my_work_page.open_requested.connect(self.open_work_record)
        self.my_work_page.backup_requested.connect(self.open_backup)
        if getattr(self.facade, "paths", None) is not None and (self.facade.paths.state_root / "restored-profile.json").is_file():
            self.setWindowTitle("沪上化学智研台 · 独立恢复副本")
            self.brand_sub.setText("独立恢复副本 · 非默认资料")
        self.classroom_page.reference_requested.connect(self.append_classroom_feedback)
        self.library_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.library_page.preview_requested.connect(self.preview_selected_paper)
        self.library_page.assembly_requested.connect(lambda: self.navigate("paper"))
        self.preparation_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.student_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.library_page.preparation_image_requested.connect(self._library_image_to_preparation)
        self.library_page.preparation_reference_requested.connect(self._library_reference_to_preparation)
        self.library_page.word_reference_requested.connect(self._word_to_preparation)
        self.library_page.word_cards_requested.connect(self._word_cards_to_preparation)
        handout_materials_requested = getattr(self.library_page, "handout_materials_requested", None)
        if handout_materials_requested is not None:
            handout_materials_requested.connect(self._handout_materials_to_preparation)
        self._paper_reference_loading = False
        self.paper_page.preparation_requested.connect(self._paper_to_preparation)
        self.tasks.task_started.connect(self._task_started)
        self.tasks.task_failed.connect(self._task_failed)
        self.tasks.task_cancelled.connect(self._task_cancelled)
        self.tasks.task_finished.connect(self._task_finished)
        self._shortcuts: list[QShortcut] = []
        for index, route in enumerate(ROUTE_ORDER, 1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda value=route: self.navigate(value))
            self._shortcuts.append(shortcut)
        for key, action in (("Ctrl+K", self.open_commands), ("F1", self.open_help)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
            self._shortcuts.append(shortcut)
        status = QStatusBar()
        self.setStatusBar(status)
        self.version_label = QLabel(f"v{DESKTOP_VERSION}")
        self.version_label.setObjectName("DesktopVersion")
        self.version_label.setToolTip("桌面程序版本；题库资料可独立更新")
        self.mode_label = QLabel("打包版" if getattr(sys, "frozen", False) else "源码版")
        self.mode_label.setObjectName("MutedLabel")
        self.environment_button = QPushButton("本机检查")
        self.environment_button.setObjectName("QuietButton")
        self.environment_button.setAccessibleName("检查本机版本、依赖与排版工具")
        self.environment_button.setEnabled(getattr(self.facade, "paths", None) is not None)
        self.environment_button.clicked.connect(self.open_environment)
        status.addPermanentWidget(self.environment_button)
        status.addPermanentWidget(self.mode_label)
        status.addPermanentWidget(self.version_label)
        status.showMessage("本地工作台已就绪")
        self._restore_window_state()
        self.home_page.update_editor(self.preparation_page._payload())

    @property
    def primary_navigation_labels(self) -> tuple[str, ...]:
        return tuple(button.text() for button in self.nav_buttons)

    def new_preparation(self):
        self.navigate("preparation")
        recovery = self.preparation_page.recovery
        if recovery is not None:
            recovery.new_blank()
        self.preparation_page.topic.setFocus()

    def _schedule_textbook_preparation_context(self, *_):
        if not self._textbook_context_closed:
            self._textbook_context_timer.start(0)

    def _refresh_textbook_preparation_context(self, *, target_changed=False):
        if self._textbook_context_closed:
            return
        page = self.preparation_page
        payload = page._payload()
        dirty = payload != page._form_baseline
        payload['work_title'] = page._editor_work_title
        self.textbook_page.set_preparation_context(
            payload, dirty=dirty,
            busy=page.studio_busy(), target_changed=target_changed)

    def _textbook_preparation_changed(self):
        # A late source check may still be running on the hidden bookshelf.
        # Revoke its old destination immediately, without clearing source work.
        self._refresh_textbook_preparation_context(target_changed=True)

    def _open_textbook_preparation(self):
        if self.preparation_page.studio_busy():
            self._refresh_textbook_preparation_context()
            return
        self.preparation_page._open_draft()
        self._refresh_textbook_preparation_context()

    def _new_textbook_preparation(self):
        if self.preparation_page.studio_busy():
            self._refresh_textbook_preparation_context()
            return
        recovery = self.preparation_page.recovery
        if recovery is not None:
            recovery.new_blank()
        self._refresh_textbook_preparation_context()

    def open_home_basket(self):
        from .explorer_basket import ExplorerBasketDialog
        dialog = ExplorerBasketDialog(self.facade, self)
        dialog.basket_changed.connect(self.paper_page.update_basket_count)
        dialog.preview_requested.connect(self.preview_selected_paper)
        dialog.edit_requested.connect(lambda: self.navigate("paper"))
        dialog.exec()
        dialog.deleteLater()
        self.home_page.refresh()

    def open_exam_dialog(self, *, exam=None, identity=None, task_id=None):
        from .exam_dashboard import ExamDashboard
        dialog=ExamDashboard(self.facade,self.tasks,self)
        def append(text):
            if self.preparation_page.import_word_reference({'materials':text,'warnings':[]}):
                dialog.status.setText('已追加到备课材料；未调用模型，请核对并保存。')
        dialog.preparation_requested.connect(append)
        if exam is not None:dialog.accept_exam(exam)
        if identity is not None:dialog.open_saved(identity,task_id)
        dialog.exec()
        handoff=dialog.pending_handoff
        dialog.deleteLater()
        if handoff:self.open_exam_practice(handoff)

    def open_exam_practice(self, request):
        from PySide6.QtCore import QSignalBlocker
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget
        page=self.library_page
        if not hasattr(page,'exam_context'):
            page.exam_context=QWidget();box=QHBoxLayout(page.exam_context)
            page.exam_context_label=QLabel();page.exam_context_label.setWordWrap(True)
            page.exam_context_back=QPushButton('返回复练任务')
            box.addWidget(page.exam_context_label,1);box.addWidget(page.exam_context_back)
            finish=QPushButton('结束选题');finish.clicked.connect(page.exam_context.hide);box.addWidget(finish)
            page.layout().insertWidget(1,page.exam_context)
            page.exam_context_back.clicked.connect(lambda:self.open_exam_dialog(
                identity=page.exam_context_request['exam_id'],task_id=page.exam_context_request['task_id']))
        page.exam_context_request=dict(request)
        page.exam_context_label.setText('考试复练 · '+request['label']+'；核对完整题面后入篮，再返回任务关联题目。')
        page.exam_context.show()
        with QSignalBlocker(page.scope):page.scope.setCurrentIndex(page.scope.findData(request['lane']))
        page._base_facets={}
        page.advanced_button.setText('Word原文与标签管理' if request['lane']=='word_native' else '题图与标签管理')
        page.filters={'knowledge':{request['knowledge']}};page.curriculum={};page.query.clear()
        self.navigate('library')
        page.search()

    def preview_selected_paper(self):
        self.navigate("paper")
        self.paper_page.request_layout_preview()

    def navigate(self, route: str) -> None:
        if route not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[route])
        if route in ROUTE_ORDER:
            self.nav_buttons[ROUTE_ORDER.index(route)].setChecked(True)
        else:
            self.studio_nav_buttons[route].setChecked(True)
        if route == "home":
            self.home_page.update_editor(self.preparation_page._payload())
            self.home_page.refresh()
        if route == "mywork":
            self.my_work_page.refresh()
        if route == "textbooks":
            self._refresh_textbook_preparation_context()
            if self.textbook_page._snapshot is None:
                self.textbook_page.refresh()
        self.top_title.setText(PAGE_TITLES.get(route, "教师工作台"))
        if route == "paper":
            self.paper_page.update_basket_count()
        if route == "library" and not self.library_page.cards and not self.library_page._loading:
            self.library_page.search()

    def open_template(self, key: str, topic: str = "") -> None:
        dialog = TemplatePreviewDialog(key, self, topic=topic)
        if dialog.exec() == dialog.DialogCode.Accepted:
            if self.preparation_page.apply_studio_template(key, dialog.topic.text(), dialog.audience.text()):
                self.navigate("preparation")
        dialog.deleteLater()

    def append_classroom_feedback(self, text: str) -> None:
        if self.preparation_page.append_classroom_feedback(text):
            self.navigate("preparation")

    def open_work_record(self, record: dict) -> None:
        if "organization_revision" in record:
            try:
                record = self.facade.resolve_preparation_work(record)
            except Exception as exc:
                message = getattr(exc, "message_zh", "作品暂时无法打开，请刷新后重选；当前编辑未改变。")
                self.statusBar().showMessage(message, 7000)
                return
        if self.preparation_page.studio_busy():
            self.statusBar().showMessage("请等待当前备课保存或生成结束。", 5000)
            return
        self.navigate("preparation")
        if record["kind"] == "draft":
            self.preparation_page._open_draft(record["value"]["draft_id"])
        else:
            self.preparation_page._restore_summary(record["value"])
            self.statusBar().showMessage("已打开历史状态；此操作没有调用模型。", 5000)

    def open_commands(self) -> None:
        dialog = CommandPalette(self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            command = dialog.command
            if command.startswith("template:"):
                self.open_template(command.split(":", 1)[1])
            elif command == "exam-analysis":
                self.student_page._open_exam_analysis()
            elif command in self.pages:
                self.navigate(command)
            else:
                {"import": self.open_import, "settings": self.open_settings,
                 "progress": self.home_page.open_library_progress, "help": self.open_help,
                 "environment": self.open_environment, "backup": self.open_backup}[command]()
        dialog.deleteLater()

    def open_backup(self) -> None:
        from .backup_dialog import BackupDialog
        recovery = self.preparation_page.recovery
        dialog = BackupDialog(self.facade.paths, self.tasks, self,
                              flush_editor=recovery.flush if recovery else None)
        dialog.exec()
        dialog.deleteLater()

    def open_help(self) -> None:
        dialog = HelpDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def _word_to_preparation(self, reference: dict) -> None:
        if self.preparation_page.import_word_reference(reference):
            self.navigate("preparation")

    def _word_cards_to_preparation(self, result: dict) -> None:
        self.navigate("preparation")
        self.preparation_page.import_word_cards(result)

    def _open_handout_materials(self) -> bool:
        if self.preparation_page.studio_busy():
            self.statusBar().showMessage("当前备课操作结束后，可继续从讲义选材。", 7000)
            return False
        opener = getattr(self.library_page, "open_handout_materials", None)
        if not callable(opener):
            return False
        self.navigate("library")
        opener()
        return True

    def _handout_materials_to_preparation(self, request: dict) -> bool:
        accepted = self.preparation_page.import_handout_materials(request)
        if accepted:
            self.navigate("preparation")
        return bool(accepted)

    def _textbook_section_materials(self, selection: dict) -> bool:
        return bool(self.preparation_page.open_textbook_section_materials(
            selection, on_handoff=lambda: self.navigate('preparation')))

    def _linked_question_to_preparation(self, request: dict) -> bool:
        accepted = self.preparation_page.import_linked_question(request)
        if accepted:
            self.navigate("preparation")
        return bool(accepted)

    def _textbook_questions(self, context: dict) -> None:
        if self.preparation_page.studio_busy():
            self.statusBar().showMessage('当前备课操作结束后，可继续从教材章节选题。', 7000)
            return
        from .word_question_dialog import WordQuestionDialog
        dialog = WordQuestionDialog(self.facade, self.tasks, self,
                                    initial_curriculum_context=context,
                                    lesson_topic=self.preparation_page.topic.text(), handoff_mode='both')
        dialog.basket_changed.connect(self.paper_page.update_basket_count)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if dialog.composition_result is not None:
                    self._word_cards_to_preparation(dialog.composition_result)
                elif dialog.preparation_reference is not None:
                    self._word_to_preparation(dialog.preparation_reference)
        finally:
            dialog.deleteLater()

    def _textbook_to_preparation(self, reference: dict) -> None:
        value = dict(reference, source_kind="textbook")
        if self.preparation_page.import_word_reference(value):
            self.textbook_page.reference_appended()
            self.navigate("preparation")
        else:
            self.textbook_page.reference_rejected()

    def _textbook_image_to_preparation(self, request: dict) -> None:
        if self.preparation_page.import_textbook_image(request):
            self.navigate("preparation")

    def _textbook_cards_to_preparation(self, result: dict) -> None:
        self.navigate('preparation')
        if self.preparation_page.import_textbook_cards(result):
            self.textbook_page.cards_appended()
        else:
            self.textbook_page.cards_rejected()

    def _textbook_images_to_preparation(self, requests: list[dict]) -> None:
        if self.preparation_page.import_textbook_images(requests):
            self.navigate("preparation")

    def _textbook_crops_to_composition(self, requests: list[dict]) -> None:
        if self.preparation_page.import_textbook_crops_for_composition(requests):
            self.navigate('preparation')

    def _library_image_to_preparation(self, selection: dict) -> None:
        if self.preparation_page.import_library_image(selection):
            self.navigate("preparation")

    def _library_reference_to_preparation(self, detail) -> None:
        if self.preparation_page.import_library_reference(detail):
            self.navigate("preparation")

    def _paper_to_preparation(self, snapshot: dict) -> None:
        if self._paper_reference_loading:
            self.statusBar().showMessage("正在读取本地组卷参考，请稍候。")
            return
        loader = getattr(self.facade, "preparation_paper_source_snapshot", None)
        if callable(loader):
            self._paper_reference_loading = True
            self.statusBar().showMessage("正在读取所选原题的来源与参考答案，不调用模型…")
            self.tasks.submit("读取组卷备课参考", lambda: loader(snapshot),
                              on_success=self._paper_reference_ready,
                              on_failure=lambda _message: self._paper_reference_failed())
        else:
            self._paper_reference_ready(snapshot)

    def _paper_reference_failed(self) -> None:
        self._paper_reference_loading = False
        self.statusBar().showMessage("组卷参考读取失败，原组卷与备课表单未改变。请重试。")

    def _paper_reference_ready(self, snapshot: dict) -> None:
        self._paper_reference_loading = False
        if self.preparation_page.import_paper_reference(snapshot):
            self.navigate("preparation")

    def open_import(self) -> None:
        dialog = ImportDialog(self.facade, self.tasks, self)
        dialog.basket_changed.connect(self.paper_page.update_basket_count)
        result = dialog.exec()
        # Saved imports survive cancellation. Invalidate before an explicit
        # library handoff so its first search reads the current local profile.
        self.library_page.invalidate_catalogs()
        preparation_opened = (result == dialog.DialogCode.Accepted
            and dialog.preparation_reference is not None
            and self.preparation_page.import_word_reference(dialog.preparation_reference))
        if preparation_opened:
            self.navigate("preparation")
        if dialog.open_textbooks_requested:
            self.navigate("textbooks")
        elif (not preparation_opened and result == dialog.DialogCode.Accepted
              and getattr(dialog, "open_word_library_requested", False)):
            self.library_page.open_word_library()
            self.navigate("library")
        dialog.deleteLater()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.facade, self.tasks, self)
        dialog.exec()
        dialog.deleteLater()

    def open_environment(self) -> None:
        from .environment_dialog import EnvironmentDialog
        paths = getattr(self.facade, "paths", None)
        if paths is not None:
            dialog = EnvironmentDialog(paths, self.tasks, self)
            dialog.exec()
            dialog.deleteLater()

    def _restore_window_state(self) -> None:
        try:
            value = self.facade.state_store.window_state()
            geometry, layout = value.get("geometry"), value.get("layout")
            if geometry:
                self.restoreGeometry(QByteArray(b64decode(geometry.encode("ascii"))))
            if layout:
                self.restoreState(QByteArray(b64decode(layout.encode("ascii"))))
        except (DesktopStateError, ValueError, OSError):
            self.statusBar().showMessage("上次窗口布局无法恢复，已使用默认布局。", 5000)

    def _save_window_state(self) -> None:
        geometry = b64encode(bytes(self.saveGeometry())).decode("ascii")
        layout = b64encode(bytes(self.saveState())).decode("ascii")
        try:
            self.facade.state_store.save_window_state(geometry=geometry, layout=layout)
        except DesktopStateError:
            self.statusBar().showMessage("窗口布局未能保存。", 4000)

    def resizeEvent(self, event: QResizeEvent) -> None:
        from PySide6.QtGui import QIcon
        compact = event.size().width() < 720
        self.side_rail.setFixedWidth(112 if compact else 214)
        self.brand.setText("沪化" if compact else "沪上化学智研台")
        self.brand_sub.setVisible(not compact)
        self.settings_button.setText("设置")
        self.top_title.setVisible(not compact)
        self.command_button.setText("搜索" if compact else "搜索功能  Ctrl+K")
        self.help_button.setVisible(not compact)
        for section in self.rail_sections:
            section.setVisible(not compact)
        for route, button in zip(ALL_ROUTES, [*self.nav_buttons, *self.studio_nav_buttons.values()]):
            button.setIcon(QIcon() if compact else studio_icon(route))
            button.setStyleSheet("padding: 6px 4px; font-size: 12px; min-height: 22px;" if compact else "")
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.preparation_page.recovery is not None and not self.preparation_page.recovery.prepare_close():
            event.ignore()
            return
        if not self.textbook_page.prepare_close():
            self.navigate('textbooks')
            event.ignore()
            return
        self._save_window_state()
        self._textbook_context_closed = True
        self._textbook_context_timer.stop()
        self.setEnabled(False)
        self.textbook_page.close()
        self.preparation_page._cancel_textbook_image_import()
        stop_readers = getattr(self.facade, "stop_background_readers", None)
        if callable(stop_readers):
            stop_readers()
        self.tasks.shutdown(5000)
        shutdown = getattr(self.facade, "shutdown", None)
        if callable(shutdown):
            shutdown()
        event.accept()

    def _task_started(self, _task_id: str, label: str) -> None:
        self.statusBar().showMessage(f"{label}…")
        self._schedule_textbook_preparation_context()

    def _task_failed(self, _task_id: str, label: str, message: str) -> None:
        self.statusBar().showMessage(f"{label}：{message}", 7000)

    def _task_cancelled(self, _task_id: str, label: str) -> None:
        self.statusBar().showMessage(f"{label}已取消。", 4000)

    def _task_finished(self, _task_id: str) -> None:
        self._schedule_textbook_preparation_context()
        if self.statusBar().currentMessage().endswith("…"):
            self.statusBar().showMessage("本地工作台已就绪", 3000)


__all__ = ["PAGE_TITLES", "PRIMARY_NAVIGATION", "ROUTE_ORDER", "ALL_ROUTES", "TOKENS",
           "WORKBENCH_STYLE", "TeacherWorkbenchWindow", "install_font_fallbacks"]
