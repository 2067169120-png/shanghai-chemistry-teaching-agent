from __future__ import annotations

from base64 import b64decode, b64encode
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout, QWidget,
)

from ..desktop_facade import PRIMARY_NAVIGATION, DesktopWorkbenchFacade
from ..desktop_state import DesktopStateError
from ..desktop_version import DESKTOP_VERSION
from .dialogs import ImportDialog, SettingsDialog
from .home_page import HomePage
from .library_page import LibraryPage
from .tasks import DesktopTaskBridge
from .workflow_pages import PaperPage, StudentPage
from .studio_preparation import StudioPreparationPage as PreparationPage
from .studio_style import TOKENS, WORKBENCH_STYLE, _build_style
from .studio_templates import TemplatePage, TemplatePreviewDialog
from .studio_navigation import CommandPalette, HelpDialog, studio_icon
from .my_work_page import MyWorkPage
from .classroom_page import ClassroomPage

ROUTE_ORDER = ("home", "library", "paper", "student", "preparation")
EXTRA_ROUTES = ("templates", "classroom", "mywork")
ALL_ROUTES = ROUTE_ORDER + EXTRA_ROUTES
PAGE_TITLES = {"home": "首页", "library": "题库", "paper": "组卷", "student": "学生分析",
               "preparation": "备课", "templates": "教学模板", "classroom": "课堂工具", "mywork": "我的备课"}


def install_font_fallbacks() -> str:
    """Load Windows Chinese faces for packaged and offscreen environments."""
    database = QFontDatabase()
    preferred = "Microsoft YaHei UI"
    if preferred not in database.families():
        for filename in ("msyh.ttc", "seguisym.ttf", "simhei.ttf", "simsun.ttc"):
            path = Path("C:/Windows/Fonts") / filename
            if path.is_file():
                database.addApplicationFont(str(path))
    families = database.families()
    chosen = next((value for value in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun")
                   if value in families), preferred)
    application = QApplication.instance()
    if application is not None:
        font = QFont(chosen)
        font.setPointSize(10)
        application.setFont(font)
    return chosen


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
        self.brand_sub = QLabel("CHEMISTRY TEACHING STUDIO")
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
        self.template_page = TemplatePage(facade)
        self.classroom_page = ClassroomPage()
        self.my_work_page = MyWorkPage(facade, self.tasks)
        self.pages = {"home": self.home_page, "library": self.library_page, "paper": self.paper_page,
                      "student": self.student_page, "preparation": self.preparation_page,
                      "templates": self.template_page, "classroom": self.classroom_page, "mywork": self.my_work_page}
        for route in ALL_ROUTES:
            self.stack.addWidget(self.pages[route])
        work.addWidget(self.stack, 1)
        root.addWidget(work_area, 1)
        self.home_page.navigate_requested.connect(self.navigate)
        self.home_page.template_requested.connect(self.open_template)
        self.template_page.template_requested.connect(self.open_template)
        self.my_work_page.navigate_requested.connect(self.navigate)
        self.my_work_page.open_requested.connect(self.open_work_record)
        self.classroom_page.reference_requested.connect(self.append_classroom_feedback)
        self.library_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.preparation_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.student_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.library_page.preparation_image_requested.connect(self._library_image_to_preparation)
        self.library_page.preparation_reference_requested.connect(self._library_reference_to_preparation)
        self.library_page.word_reference_requested.connect(self._word_to_preparation)
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
        status.addPermanentWidget(self.version_label)
        status.showMessage("本地工作台已就绪")
        self._restore_window_state()

    @property
    def primary_navigation_labels(self) -> tuple[str, ...]:
        return tuple(button.text() for button in self.nav_buttons)

    def navigate(self, route: str) -> None:
        if route not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[route])
        if route in ROUTE_ORDER:
            self.nav_buttons[ROUTE_ORDER.index(route)].setChecked(True)
        else:
            self.studio_nav_buttons[route].setChecked(True)
        if route == "mywork":
            self.my_work_page.refresh()
        self.top_title.setText(PAGE_TITLES.get(route, "教师工作台"))
        if route == "paper":
            self.paper_page.update_basket_count()
        if route == "library" and self.library_page.results.count() == 0:
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
            elif command in self.pages:
                self.navigate(command)
            else:
                {"import": self.open_import, "settings": self.open_settings,
                 "progress": self.home_page.open_library_progress, "help": self.open_help}[command]()
        dialog.deleteLater()

    def open_help(self) -> None:
        dialog = HelpDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def _word_to_preparation(self, reference: dict) -> None:
        if self.preparation_page.import_word_reference(reference):
            self.navigate("preparation")

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
        if (dialog.exec() == dialog.DialogCode.Accepted
            and dialog.preparation_reference is not None
            and self.preparation_page.import_word_reference(dialog.preparation_reference)):
            self.navigate("preparation")
        dialog.deleteLater()

    def open_settings(self) -> None:
        SettingsDialog(self.facade, self.tasks, self).exec()

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
        self._save_window_state()
        self.setEnabled(False)
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

    def _task_failed(self, _task_id: str, label: str, message: str) -> None:
        self.statusBar().showMessage(f"{label}：{message}", 7000)

    def _task_cancelled(self, _task_id: str, label: str) -> None:
        self.statusBar().showMessage(f"{label}已取消。", 4000)

    def _task_finished(self, _task_id: str) -> None:
        if self.statusBar().currentMessage().endswith("…"):
            self.statusBar().showMessage("本地工作台已就绪", 3000)


__all__ = ["PAGE_TITLES", "PRIMARY_NAVIGATION", "ROUTE_ORDER", "ALL_ROUTES", "TOKENS",
           "WORKBENCH_STYLE", "TeacherWorkbenchWindow", "install_font_fallbacks"]
