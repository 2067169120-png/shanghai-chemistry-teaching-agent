from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import DesktopRegistry, DesktopWorkbenchFacade
from .components import CardFrame, page_scroll, section_title, set_status
from .tasks import DesktopTaskBridge


class _MetricCell(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("MetricTitle")
        # Keep the four-cell status panel quiet while the single page-level
        # message reports loading; repeated "读取中" labels add visual noise.
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.detail = QLabel("")
        self.detail.setObjectName("MutedLabel")
        self.detail.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def loaded(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)

    def failed(self, message: str) -> None:
        self.value.setText("—")
        self.detail.setText(message)


class HomePage(QWidget):
    navigate_requested = Signal(str)

    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self._loading = False

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 32)
        root.setSpacing(20)

        heading = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        heading.setSpacing(12)
        self.heading_layout = heading
        heading.addWidget(
            section_title(
                "首页",
                "把资料变成一节好课：选题、组卷、备课与学情回看。",
            ),
            1,
        )
        self.refresh_button = QPushButton("重新读取")
        self.refresh_button.setObjectName("QuietButton")
        self.refresh_button.setAccessibleName("重新读取本地资料状态")
        self.refresh_button.clicked.connect(lambda: self.refresh(force_refresh=True))
        heading.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(heading)

        actions = CardFrame()
        actions.setStyleSheet(
            "QFrame#Card {background: #EAF5F4; border: 1px solid #BCDCD8; border-radius: 16px;}"
        )
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(20, 16, 20, 16)
        actions_layout.setSpacing(10)
        action_title = QLabel("今天，要完成哪项教学任务？")
        action_title.setObjectName("CardTitle")
        actions_layout.addWidget(action_title)
        action_hint = QLabel("先选教学目标，再带入完整原题；让教案、课件和课后练习使用同一套材料。")
        action_hint.setObjectName("MutedLabel")
        action_hint.setWordWrap(True)
        action_hint.setMinimumWidth(0)
        actions_layout.addWidget(action_hint)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.action_row = action_row
        for index, (title, route) in enumerate(
            (("按教材找题", "library"), ("开始组卷", "paper"), ("开始备课", "preparation"))
        ):
            button = QPushButton(title)
            button.setObjectName("PrimaryAction" if index == 0 else "QuietButton")
            button.setMinimumHeight(44)
            button.setAccessibleName(title)
            button.clicked.connect(
                lambda _checked=False, value=route: self.navigate_requested.emit(value)
            )
            action_row.addWidget(button, 1)
        actions_layout.addLayout(action_row)
        root.addWidget(actions)

        status_panel = CardFrame()
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_title = QLabel("资料状态")
        status_title.setObjectName("CardTitle")
        status_layout.addWidget(status_title)
        status_hint = QLabel("小问不一定能独立作答；选用时须保留公共材料、题图和必要的前序条件。")
        status_hint.setObjectName("MutedLabel")
        status_hint.setWordWrap(True)
        status_layout.addWidget(status_hint)
        self.progress_button = QPushButton("检查本地题库进度")
        self.progress_button.setObjectName("QuietButton")
        self.progress_button.clicked.connect(self.open_library_progress)
        status_layout.addWidget(self.progress_button)
        paths = getattr(self.facade, "paths", None)
        if paths is not None and getattr(paths, "uses_personal_library", False):
            first_run = QLabel(
                "当前未连接旧版原题库。已有个人Word/图片导入仍保留；新资料从右上角“导入资料”加入。"
                "缺少原题库不影响打开工作台、设置模型和备课。"
            )
            first_run.setObjectName("StatusInfo")
            first_run.setWordWrap(True)
            status_layout.addWidget(first_run)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.metrics = {
            "master": _MetricCell("核心题库"),
            "wave1": _MetricCell("已细分题库"),
            "supplemental": _MetricCell("补充题库"),
            "curriculum": _MetricCell("教材目录"),
        }
        self.metric_grid = grid
        self._metrics_compact = False
        for index, key in enumerate(("master", "wave1", "supplemental", "curriculum")):
            grid.addWidget(self.metrics[key], index // 2, index % 2)
        status_layout.addLayout(grid)
        root.addWidget(status_panel)

        recent_panel = CardFrame()
        recent_layout = QVBoxLayout(recent_panel)
        recent_layout.setContentsMargins(20, 16, 20, 16)
        recent_title = QLabel("继续上次工作")
        recent_title.setObjectName("CardTitle")
        recent_layout.addWidget(recent_title)
        self.recent = QLabel("")
        self.recent.setObjectName("StatusInfo")
        self.recent.setWordWrap(True)
        recent_layout.addWidget(self.recent)
        self.status = QLabel("正在检查本地资料状态…")
        self.status.setObjectName("StatusInfo")
        self.status.setWordWrap(True)
        recent_layout.addWidget(self.status)
        root.addWidget(recent_panel)
        root.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self._refresh_recent()
        QTimer.singleShot(80, self.refresh)

    def open_library_progress(self) -> None:
        from .library_progress_dialog import LibraryProgressDialog

        dialog = LibraryProgressDialog(self.facade, self.tasks, self)
        dialog.exec()
        dialog.deleteLater()

    def resizeEvent(self, event: QResizeEvent) -> None:
        compact = event.size().width() < 600
        self.heading_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        self.action_row.setDirection(
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        if compact != self._metrics_compact:
            self._metrics_compact = compact
            for key, metric in self.metrics.items():
                self.metric_grid.removeWidget(metric)
                column_count = 1 if compact else 2
                index = tuple(self.metrics).index(key)
                self.metric_grid.addWidget(metric, index if compact else index // 2, 0 if compact else index % 2)
        super().resizeEvent(event)

    def _refresh_recent(self) -> None:
        try:
            state = self.facade.state_store.snapshot()
            basket = state.get("basket") if isinstance(state, dict) else []
            drafts = state.get("drafts") if isinstance(state, dict) else {}
            basket_count = len(basket) if isinstance(basket, list) else 0
            draft_count = len(drafts) if isinstance(drafts, dict) else 0
            self.recent.setText(
                f"题篮中有 {basket_count} 道完整大题，个人草稿 {draft_count} 份。"
                " 可从题库或组卷继续。"
            )
        except Exception:
            self.recent.setText("最近工作暂时无法读取；题库浏览不受影响。")

    def refresh(self, *, force_refresh: bool = False) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_button.setEnabled(False)
        set_status(self.status, "info", "正在读取本地题库与教材目录…")
        for metric in self.metrics.values():
            metric.value.setText("—")
            metric.detail.setText("")
        self.tasks.submit(
            "读取本地题库",
            lambda: self.facade.load_desktop_registry(force_refresh=force_refresh),
            on_success=self._apply_registry,
            on_failure=self._show_failure,
        )

    def _apply_registry(self, registry: DesktopRegistry) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        failures = 0
        for product in registry.products:
            metric = self.metrics.get(product.product_id)
            if metric is None:
                continue
            if product.loaded:
                metric.loaded(
                    f"{product.atomic_parts or 0} 个小问",
                    f"{product.papers or 0} 套试卷 · {product.themes or 0} 道大题",
                )
            else:
                failures += 1
                metric.failed(product.message_zh)
        curriculum = registry.curriculum
        if curriculum.loaded:
            self.metrics["curriculum"].loaded(
                f"{curriculum.sections or 0} 个小节",
                f"{curriculum.volumes or 0} 册 · {curriculum.chapters or 0} 章",
            )
        else:
            failures += 1
            self.metrics["curriculum"].failed(curriculum.message_zh)
        self._refresh_recent()
        set_status(
            self.status,
            "success" if failures == 0 else "attention",
            (
            "本地资料已读取，可以开始找题。"
            if failures == 0
            else f"已有 {failures} 项暂时无法读取；其余功能仍可继续，点击“重新读取”重试。"
            ),
        )

    def _show_failure(self, message: str) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        set_status(self.status, "error", message)
        for metric in self.metrics.values():
            metric.failed("本地状态尚未读取，可点击“重新读取”。")


__all__ = ["HomePage"]
