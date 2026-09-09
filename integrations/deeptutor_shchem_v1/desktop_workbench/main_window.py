from __future__ import annotations

from base64 import b64decode, b64encode
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import PRIMARY_NAVIGATION, DesktopWorkbenchFacade
from ..desktop_state import DesktopStateError
from ..desktop_version import DESKTOP_VERSION
from .dialogs import ImportDialog, SettingsDialog
from .home_page import HomePage
from .library_page import LibraryPage
from .tasks import DesktopTaskBridge
from .workflow_pages import PaperPage, PreparationPage, StudentPage

ROUTE_ORDER = ("home", "library", "paper", "student", "preparation")
PAGE_TITLES = {
    "home": "首页",
    "library": "题库",
    "paper": "组卷",
    "student": "学生分析",
    "preparation": "备课",
}

# Semantic tokens for the native shell. Pages use object names so a theme
# change remains a one-place operation rather than scattered color literals.
TOKENS = {
    "surface_app": "#F4F6F8",
    "surface_panel": "#FFFFFF",
    "surface_subtle": "#EEF2F3",
    "surface_rail": "#E7EFF0",
    "ink": "#1F2933",
    "ink_muted": "#5B6870",
    "brand": "#167C80",
    "brand_dark": "#0E6064",
    "brand_soft": "#DDF1F0",
    # Deep amber keeps the attention state above WCAG AA on white while
    # remaining distinct from the success green and error red.
    "attention": "#8A5A00",
    "danger": "#B42318",
    "success": "#237A57",
    "line": "#D9E1E4",
    "line_focus": "#167C80",
}


def _build_style(tokens: dict[str, str]) -> str:
    return f"""
QMainWindow, QDialog, QWidget#WindowRoot, QStackedWidget,
QScrollArea#PageScroll, QWidget#PageViewport, QWidget#PageContent {{
    background: {tokens["surface_app"]}; color: {tokens["ink"]};
}}
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Symbol", "SimHei", "SimSun", "Segoe UI";
    font-size: 13px; background: {tokens["surface_app"]}; color: {tokens["ink"]};
}}
QLabel {{ color: {tokens["ink"]}; background: transparent; }}
QFrame#SideRail {{ background: {tokens["surface_rail"]}; border: 0; border-right: 1px solid {tokens["line"]}; }}
QLabel#Brand {{ color: {tokens["ink"]}; font-size: 18px; font-weight: 700; }}
QLabel#BrandSub {{ color: {tokens["ink_muted"]}; font-size: 12px; }}
QPushButton#NavButton, QPushButton#SettingsButton {{
    border: 1px solid {tokens["line"]}; border-left: 3px solid {tokens["line"]};
    border-radius: 7px; color: {tokens["ink_muted"]}; text-align: left;
    padding: 9px 12px; background: {tokens["surface_panel"]}; min-height: 34px;
}}
QPushButton#NavButton:hover, QPushButton#SettingsButton:hover {{
    background: {tokens["surface_subtle"]}; color: {tokens["ink"]};
    border-color: {tokens["brand"]};
}}
QPushButton#NavButton:checked {{
    background: {tokens["brand_soft"]}; color: {tokens["brand_dark"]};
    border-color: {tokens["brand"]}; border-left-color: {tokens["brand"]};
    font-weight: 700;
}}
QPushButton#NavButton:focus, QPushButton#SettingsButton:focus {{
    border-color: {tokens["line_focus"]}; border-left-color: {tokens["brand"]};
}}
QFrame#TopBar {{ background: {tokens["surface_panel"]}; border-bottom: 1px solid {tokens["line"]}; }}
QLabel#TopTitle {{ font-size: 16px; font-weight: 700; color: {tokens["ink"]}; }}
QLabel#PageTitle {{ font-size: 24px; font-weight: 700; color: {tokens["ink"]}; }}
QLabel#PageSubtitle, QLabel#MutedLabel {{ color: {tokens["ink_muted"]}; }}
QLabel#CardTitle {{ font-size: 16px; font-weight: 700; color: {tokens["ink"]}; }}
QLabel#MetricTitle {{ color: {tokens["ink_muted"]}; font-weight: 600; }}
QLabel#MetricValue {{ color: {tokens["brand_dark"]}; font-size: 22px; font-weight: 700; }}
QFrame#Card {{ background: {tokens["surface_panel"]}; border: 1px solid {tokens["line"]}; border-radius: 10px; }}
QFrame#CollapsibleContent {{ background: {tokens["surface_subtle"]}; border: 1px solid {tokens["line"]}; border-radius: 8px; }}
QFrame#ThemeCard {{ background: {tokens["surface_panel"]}; border: 1px solid {tokens["line"]}; border-radius: 8px; }}
QFrame#QuestionRow {{ background: {tokens["surface_panel"]}; border: 0; border-bottom: 1px solid {tokens["line"]}; border-radius: 0; }}
QFrame#QuestionDetails, QFrame#SharedMaterial {{ background: {tokens["surface_subtle"]}; border: 0; border-radius: 6px; }}
QLabel#QuestionResponse, QLabel#QuestionMeta, QLabel#ThemeMeta, QLabel#SharedSummary {{ background: transparent; color: {tokens["ink_muted"]}; }}
QPushButton {{
    background: {tokens["brand"]}; color: white; border: 1px solid {tokens["brand"]};
    border-radius: 6px; padding: 8px 14px; min-height: 20px;
}}
QPushButton:hover {{ background: {tokens["brand_dark"]}; border-color: {tokens["brand_dark"]}; }}
QPushButton:pressed {{ background: {tokens["brand_dark"]}; }}
QPushButton:disabled {{ background: {tokens["surface_subtle"]}; color: {tokens["ink_muted"]}; border-color: {tokens["line"]}; }}
QPushButton#PrimaryAction {{ background: {tokens["brand"]}; color: white; border-color: {tokens["brand"]}; font-weight: 700; }}
QPushButton#PrimaryAction:hover {{ background: {tokens["brand_dark"]}; border-color: {tokens["brand_dark"]}; }}
QPushButton:focus, QToolButton:focus, QRadioButton:focus, QCheckBox:focus {{ border: 2px solid {tokens["line_focus"]}; }}
QPushButton#QuietButton {{ background: {tokens["surface_subtle"]}; color: {tokens["ink"]}; border-color: {tokens["line"]}; }}
QPushButton#QuietButton:hover {{ background: {tokens["brand_soft"]}; border-color: {tokens["brand"]}; }}
QPushButton#LinkButton {{ background: transparent; color: {tokens["brand_dark"]}; border: 0; padding: 3px 0; }}
QPushButton#LinkButton:hover {{ color: {tokens["brand"]}; text-decoration: underline; }}
QPushButton#ThemeTitleButton {{ background: transparent; color: {tokens["ink"]}; border: 0; }}
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QListWidget {{
    background: {tokens["surface_panel"]}; color: {tokens["ink"]}; border: 1px solid {tokens["line"]};
    border-radius: 6px; padding: 7px;
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus, QListWidget:focus {{
    border: 2px solid {tokens["line_focus"]}; padding: 6px;
}}
QListWidget::item {{ padding: 9px 8px; border-bottom: 1px solid {tokens["surface_subtle"]}; }}
QListWidget::item:selected {{ background: {tokens["brand_soft"]}; color: {tokens["brand_dark"]}; }}
QListWidget#DropFileList {{ border: 1px dashed #9AAEB2; background: {tokens["surface_panel"]}; }}
QListWidget#DropFileList::item {{ padding: 8px; }}
QToolButton {{ border: 1px solid transparent; color: {tokens["ink"]}; padding: 5px; }}
QToolButton:hover {{ background: {tokens["surface_subtle"]}; }}
QStatusBar {{ background: {tokens["surface_panel"]}; border-top: 1px solid {tokens["line"]}; color: {tokens["ink_muted"]}; }}
QScrollArea {{ background: {tokens["surface_app"]}; border: 0; }}
QScrollBar:horizontal {{ height: 0px; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #B9C7CA; border-radius: 4px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QSplitter::handle {{ background: {tokens["line"]}; }}
QProgressBar {{ border: 1px solid {tokens["line"]}; border-radius: 5px; background: {tokens["surface_subtle"]}; text-align: center; color: {tokens["ink"]}; min-height: 16px; }}
QProgressBar::chunk {{ background: {tokens["brand"]}; border-radius: 4px; }}
QLabel#StatusSuccess {{ color: {tokens["success"]}; }}
QLabel#StatusAttention {{ color: {tokens["attention"]}; }}
QLabel#StatusError {{ color: {tokens["danger"]}; }}
QLabel#StatusInfo {{ color: {tokens["ink_muted"]}; }}
"""


WORKBENCH_STYLE = _build_style(TOKENS)


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
    chosen = next(
        (
            value
            for value in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun")
            if value in families
        ),
        preferred,
    )
    application = QApplication.instance()
    if application is not None:
        font = QFont(chosen)
        font.setPointSize(10)
        application.setFont(font)
    return chosen


class TeacherWorkbenchWindow(QMainWindow):
    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = DesktopTaskBridge(self)
        self._font_family = install_font_fallbacks()
        self.setObjectName("TeacherWorkbenchWindow")
        self.setWindowTitle("沪上化学智研台")
        self.setMinimumSize(360, 560)
        self.resize(1180, 780)
        self.setStyleSheet(WORKBENCH_STYLE)

        root_widget = QWidget()
        root_widget.setObjectName("WindowRoot")
        root = QHBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(root_widget)

        self.side_rail = QFrame()
        self.side_rail.setObjectName("SideRail")
        self.side_rail.setFixedWidth(196)
        side = QVBoxLayout(self.side_rail)
        side.setContentsMargins(16, 20, 16, 16)
        side.setSpacing(4)
        self.brand = QLabel("沪上化学智研台")
        self.brand.setObjectName("Brand")
        self.brand_sub = QLabel("教师个人工作台")
        self.brand_sub.setObjectName("BrandSub")
        side.addWidget(self.brand)
        side.addWidget(self.brand_sub)
        side.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, label in enumerate(PRIMARY_NAVIGATION):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setAccessibleName(label)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda _checked=False, route=ROUTE_ORDER[index]: self.navigate(route)
            )
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            side.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        side.addStretch(1)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("SettingsButton")
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
        self.pages = {
            "home": self.home_page,
            "library": self.library_page,
            "paper": self.paper_page,
            "student": self.student_page,
            "preparation": self.preparation_page,
        }
        for route in ROUTE_ORDER:
            self.stack.addWidget(self.pages[route])
        work.addWidget(self.stack, 1)
        root.addWidget(work_area, 1)

        self.home_page.navigate_requested.connect(self.navigate)
        self.library_page.basket_changed.connect(self.paper_page.update_basket_count)
        self.library_page.preparation_image_requested.connect(
            self._library_image_to_preparation
        )
        self.library_page.preparation_reference_requested.connect(
            self._library_reference_to_preparation
        )
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
        index = ROUTE_ORDER.index(route)
        self.stack.setCurrentWidget(self.pages[route])
        self.nav_buttons[index].setChecked(True)
        self.top_title.setText(PAGE_TITLES.get(route, "教师工作台"))
        if route == "paper":
            self.paper_page.update_basket_count()
        if route == "library" and self.library_page.results.count() == 0:
            self.library_page.search()

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
            self.statusBar().showMessage(
                "正在读取所选原题的来源与参考答案，不调用模型…"
            )
            self.tasks.submit(
                "读取组卷备课参考",
                lambda: loader(snapshot),
                on_success=self._paper_reference_ready,
                on_failure=lambda _message: self._paper_reference_failed(),
            )
        else:
            self._paper_reference_ready(snapshot)

    def _paper_reference_failed(self) -> None:
        self._paper_reference_loading = False
        self.statusBar().showMessage(
            "组卷参考读取失败，原组卷与备课表单未改变。请重试。"
        )

    def _paper_reference_ready(self, snapshot: dict) -> None:
        self._paper_reference_loading = False
        if self.preparation_page.import_paper_reference(snapshot):
            self.navigate("preparation")

    def open_import(self) -> None:
        dialog = ImportDialog(self.facade, self.tasks, self)
        if (
            dialog.exec() == dialog.DialogCode.Accepted
            and dialog.preparation_reference is not None
            and self.preparation_page.import_word_reference(
                dialog.preparation_reference
            )
        ):
            self.navigate("preparation")
        dialog.deleteLater()

    def open_settings(self) -> None:
        SettingsDialog(self.facade, self.tasks, self).exec()

    def _restore_window_state(self) -> None:
        try:
            value = self.facade.state_store.window_state()
            geometry = value.get("geometry")
            layout = value.get("layout")
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
        compact = event.size().width() < 720
        self.side_rail.setFixedWidth(112 if compact else 196)
        self.brand.setText("沪化" if compact else "沪上化学智研台")
        self.brand_sub.setVisible(not compact)
        self.settings_button.setText("设置")
        self.top_title.setVisible(not compact)
        for button in self.nav_buttons:
            button.setStyleSheet(
                "padding-left: 8px; padding-right: 6px; font-size: 12px;"
                if compact
                else ""
            )
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_window_state()
        # Stop background readers before Qt starts deleting page widgets.  A
        # bounded shutdown keeps the close action responsive; workers that
        # take longer are detached from GUI callbacks by the bridge and will
        # finish quietly without a terminal/console error.
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


__all__ = [
    "PAGE_TITLES",
    "PRIMARY_NAVIGATION",
    "ROUTE_ORDER",
    "TOKENS",
    "WORKBENCH_STYLE",
    "TeacherWorkbenchWindow",
    "install_font_fallbacks",
]
