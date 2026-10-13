"""Responsive, bounded reading of the verified actual PPTX conversion."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..desktop_preview_pdf import FrozenPdfPages
from .preview_navigation import PageNavigator, PixmapCache, checked_page_pixmap, scaled_page
from .tasks import DesktopTaskBridge


class ActualPptPreview(QDialog):
    def __init__(self, report, parent=None, *, tasks=None, script_load=None,
                 script_save=None, verify_presentation=None, speech_factory=None):
        super().__init__(parent)
        self.report = dict(report)
        self.verify_presentation = verify_presentation
        self.script_load = script_load
        self.narration = None
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._source = self._task = self._active = None
        if hasattr(self.tasks, "task_cancelled"):
            self.tasks.task_cancelled.connect(self._read_cancelled)
        self._cache = PixmapCache()
        self._bitmap = None
        self._shown = None
        self._failed_pages = set()
        self._navigation_explicit = False
        self.index, self.count = 0, 0
        self.document_title = '教案 Word' if self.report.get('document_kind') == 'word' else '课堂演示 PPT'
        engine = self.report.get("engine") or "本地转换器"
        self.setWindowTitle(self.document_title + "预览 · " + engine)
        self.resize(1080, 760)
        self.setMinimumSize(360, 520)
        root = QVBoxLayout(self)
        heading = QLabel(self.document_title + "预览 · " + engine)
        heading.setWordWrap(True)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(heading)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.reader_panel = QWidget()
        reader_layout = QVBoxLayout(self.reader_panel)
        reader_layout.setContentsMargins(0, 0, 0, 0)
        self.content_splitter.addWidget(self.reader_panel)
        self.content_splitter.splitterMoved.connect(lambda *_: self._fit_page())
        root.addWidget(self.content_splitter, 1)
        self.picture = QLabel("正在读取页面…")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setTextFormat(Qt.TextFormat.PlainText)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.picture)
        self.scroll.setAccessibleName(self.document_title + "页面，可放大并滚动")
        reader_layout.addWidget(self.scroll, 1)
        self.navigator = PageNavigator()
        self.navigator.page_selected.connect(self._jump)
        reader_layout.addWidget(self.navigator)
        row = QHBoxLayout()
        self.prev, self.next = QPushButton("上一页"), QPushButton("下一页")
        self.prev.setAccessibleName("上一页")
        self.next.setAccessibleName("下一页")
        self.page_selector = QSpinBox()
        self.page_selector.setAccessibleName(self.document_title + "页码")
        self.zoom = QComboBox()
        self.zoom.addItem("适合宽度", 0)
        self.zoom.addItem("整页", -1)
        for value in (100, 150, 200):
            self.zoom.addItem(f"{value}%", value)
        self.zoom.setAccessibleName(self.document_title + "缩放")
        for widget in (self.prev, self.page_selector, self.next, self.zoom):
            widget.setMinimumWidth(0)
            row.addWidget(widget)
        row.setStretch(1, 1)
        row.setStretch(3, 1)
        self.zoom.setMinimumWidth(self.zoom.fontMetrics().horizontalAdvance("适合宽度") + 38)
        reader_layout.addLayout(row)
        self.navigation_toggle = QPushButton("页码导航")
        self.navigation_toggle.setObjectName("QuietButton")
        self.navigation_toggle.setCheckable(True)
        self.navigation_toggle.setChecked(True)
        self.navigation_toggle.setAccessibleName("展开或收起页面缩略导航")
        self.navigation_toggle.clicked.connect(self._toggle_navigation)
        reader_layout.addWidget(self.navigation_toggle)
        self.caption = QLabel("正在读取文件…")
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        reader_layout.addWidget(self.caption)
        if script_save is not None:
            from .ppt_narration_panel import PptNarrationPanel
            self.narration = PptNarrationPanel(self, script_save, speech_factory=speech_factory)
            self.content_splitter.addWidget(self.narration);self.narration.hide()
            self.narration_toggle = QPushButton("本页讲解稿与本机朗读")
            self.narration_toggle.setCheckable(True)
            self.narration_toggle.toggled.connect(self._toggle_narration)
            root.addWidget(self.narration_toggle)
        footer = QHBoxLayout()
        self.open_pdf = QPushButton("打开完整PDF")
        self.back = QPushButton("返回")
        footer.addWidget(self.open_pdf)
        footer.addWidget(self.back)
        root.addLayout(footer)
        self.prev.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        self.page_selector.valueChanged.connect(self._jump)
        self.zoom.currentIndexChanged.connect(self._fit_page)
        self.open_pdf.clicked.connect(self._open_pdf)
        self.back.clicked.connect(self.reject)
        self._controls()
        self._active = "opening"
        task = self.tasks.submit("读取" + self.document_title + "分页", lambda: FrozenPdfPages(self.report),
                                 on_success=self._source_ready, on_failure=self._source_failed)
        if self._active is not None:
            self._task = task

    def _source_ready(self, source):
        if self._closed:
            return
        self._source, self.count = source, source.count
        if self.narration:
            try:
                self.narration.bind(source, self.script_load(self.count) if self.script_load else [""]*self.count)
            except (ValueError, TypeError):
                self._source = None
                self._source_failed("讲稿版本未能核对")
                return
        self._active = self._task = None
        self.page_selector.blockSignals(True)
        self.page_selector.setRange(1, self.count)
        self.page_selector.setSuffix(f" / {self.count} 页")
        self.page_selector.setMinimumWidth(self.page_selector.fontMetrics().horizontalAdvance(f"{self.count} / {self.count} 页") + 30)
        self.page_selector.blockSignals(False)
        self.navigator.set_pages([(n, f"第 {n} 页") for n in range(1, self.count + 1)])
        self.move(0)

    def _source_failed(self, _message):
        if self._closed:
            return
        self._active = self._task = None
        self.picture.setText("预览文件缺失、已变化或无法读取。")
        self.caption.setText("返回后可重新生成预览。")
        self._controls()

    def _read_cancelled(self, task_id, *_args):
        if self._closed or task_id != self._task:
            return
        if self._active == "opening":
            self._source_failed("读取已停止")
        elif isinstance(self._active, int):
            self._page_failed(self._active)

    def _jump(self, number):
        if self._closed or not self.count:
            return
        self.index = max(0, min(self.count - 1, number - 1))
        if self.narration:self.narration.select_page(self.index+1)
        self._show_current()

    def _toggle_navigation(self, checked):
        self._navigation_explicit = True
        self.navigator.setVisible(checked)
        QTimer.singleShot(0, self._fit_page)

    def _toggle_narration(self, checked):
        self.narration.setVisible(checked)
        self._arrange_reader()

    def _arrange_reader(self):
        wide = self.width() >= 1000
        self.content_splitter.setOrientation(Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical)
        if self.narration:
            self.narration.setMaximumHeight(16777215 if wide else 330)
            if self.narration.isVisible():
                self.content_splitter.setSizes([max(400, self.width()-500), 460] if wide else [480, 300])
        if not self._navigation_explicit:
            show = self.width() >= 600 and not (self.narration and self.narration.isVisible() and not wide)
            self.navigator.setVisible(show)
            self.navigation_toggle.setChecked(show)
        QTimer.singleShot(0, self._fit_page)

    def move(self, delta):
        self._jump(self.index + delta + 1)

    def _show_current(self):
        if self._closed or self._source is None:
            return
        number = self.index + 1
        self.page_selector.blockSignals(True)
        self.page_selector.setValue(number)
        self.page_selector.blockSignals(False)
        self.navigator.select_page(number)
        bitmap = self._cache.get(number)
        if bitmap is None and self._shown == number:
            bitmap = self._bitmap
        self.picture.clear()
        self.picture.setMinimumSize(0, 0)
        self.picture.setMaximumSize(16777215, 16777215)
        self._shown = self._bitmap = None
        self.caption.setText(f"第 {number} / {self.count} 页")
        if number in self._failed_pages:
            self.picture.setText("本页未能显示，请返回重新生成预览。")
            self.picture.adjustSize()
            self.caption.setText(f"第 {number} / {self.count} 页读取失败；其余页仍可查看。")
        elif bitmap is not None:
            self._shown, self._bitmap = number, bitmap
            self._fit_page()
        else:
            self.picture.setText("正在读取本页…")
            self.picture.adjustSize()
            if self._active is None:
                self._active = number
                self.navigator.set_status(number, "读取中")
                source = self._source
                ratio = max(1.0, min(4.0, self.devicePixelRatioF()))
                try:
                    task = self.tasks.submit("读取" + self.document_title + "页面", lambda: source.render(number, pixel_ratio=ratio),
                        on_success=lambda value: self._page_ready(number, value),
                        on_failure=lambda _message: self._page_failed(number))
                    if self._active is not None:
                        self._task = task
                except Exception:
                    self._page_failed(number)
        self._controls()

    def _page_ready(self, number, value):
        if self._closed:
            return
        self._active = self._task = None
        try:
            bitmap = checked_page_pixmap(value, value)
        except (ValueError, KeyError, TypeError):
            self._page_failed(number)
            return
        self._cache.put(number, bitmap)
        self.navigator.set_thumbnail(number, bitmap)
        self.navigator.set_status(number, "已读取")
        if number == self.index + 1:
            self._shown, self._bitmap = number, bitmap
        self._show_current()

    def _page_failed(self, number):
        if self._closed:
            return
        self._active = self._task = None
        self._failed_pages.add(number)
        self.navigator.set_status(number, "读取失败")
        self._show_current()

    def _fit_page(self, *_args):
        pixmap = self._bitmap
        if pixmap is None or self._shown != self.index + 1:
            return
        mode = self.zoom.currentData()
        viewport = self.scroll.viewport().size()
        if mode == -1:
            factor = min(max(40, viewport.width() - 12) / pixmap.width(),
                         max(40, viewport.height() - 12) / pixmap.height())
            width = pixmap.width() * factor
        else:
            width = max(40, viewport.width() - 12) if mode == 0 else pixmap.width() * mode / 100
        image = scaled_page(pixmap, width, self.devicePixelRatioF())
        self.picture.setPixmap(image)
        self.picture.setFixedSize(image.deviceIndependentSize().toSize())

    def _controls(self):
        ready = self._source is not None and not self._closed
        self.navigator.setEnabled(ready)
        self.page_selector.setEnabled(ready)
        self.prev.setEnabled(ready and self.index > 0)
        self.next.setEnabled(ready and self.index + 1 < self.count)
        self.zoom.setEnabled(ready and self._shown == self.index + 1)
        self.open_pdf.setEnabled(ready)
        if self.narration:self.narration.set_ready(ready and self._shown == self.index+1)

    def _open_pdf(self):
        if self._source is None or self._closed:
            return
        try:
            self._source.verify_file()
        except (OSError, ValueError):
            self.caption.setText("原PDF已变化，未打开外部文件。当前仍是打开时快照，请重新生成预览。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._source.path)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "zoom"):
            narrow = self.width() < 600
            self.prev.setText("‹" if narrow else "上一页")
            self.next.setText("›" if narrow else "下一页")
            self.prev.setFixedWidth(34 if narrow else 84)
            self.next.setFixedWidth(34 if narrow else 84)
            if not self._navigation_explicit:
                self.navigator.setVisible(not narrow)
                self.navigation_toggle.setChecked(not narrow)
            self._arrange_reader()

    def reject(self):
        if self._closed:
            return
        if self.narration and not self.narration.finish():
            return
        self._closed = True
        if self._task:
            self.tasks.cancel(self._task)
        self._task = self._active = self._source = self._shown = self._bitmap = None
        self._cache.clear()
        self.navigator.clear_pages()
        self.picture.clear()
        done = not self._own_tasks or self.tasks.shutdown(1000)
        super().reject()
        if done:
            self.deleteLater()
        else:
            # A native PDF call cannot be interrupted mid-render. Keep only
            # this hidden owner until its own worker has actually finished.
            self.setParent(None)
            QTimer.singleShot(100, self._dispose_when_idle)

    def _dispose_when_idle(self):
        if self.tasks.wait_for_done(0):
            self.tasks.shutdown(0)
            self.deleteLater()
        else:
            QTimer.singleShot(100, self._dispose_when_idle)

    def closeEvent(self, event):
        self.reject()
        if self._closed:event.accept()
        else:event.ignore()
