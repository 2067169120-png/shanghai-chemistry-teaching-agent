"""Browse whole-page textbook candidates; this dialog never selects materials."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .tasks import DesktopTaskBridge


class TextbookAssetCatalogDialog(QDialog):
    def __init__(self, facade, parent=None, *, tasks=None):
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks or DesktopTaskBridge(self)
        self._own_tasks = tasks is None
        self._closed = False
        self._request = 0
        self.setWindowTitle("浏览教材素材 · 候选原页索引")
        self.resize(900, 700)
        self.setMinimumSize(400, 500)
        root = QVBoxLayout(self)
        intro = QLabel("按册名、图表或实验关键词查找，也可查看复习、探究与附录素材。条目仅定位原页，尚未裁切，须对照原文核对。")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("输入册名、关键词或PDF页码")
        self.search.setAccessibleName("教材素材候选关键词搜索")
        root.addWidget(self.search)
        self.results = QListWidget()
        self.results.setAccessibleName("教材素材候选列表，双击或按回车查看对应原页")
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.currentItemChanged.connect(self._selected)
        self.results.itemActivated.connect(lambda *_: self._open())
        root.addWidget(self.results, 2)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("所选教材素材的候选说明、册名和原页定位")
        root.addWidget(self.details, 1)
        self.status = QLabel("正在读取本地教材素材目录…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.open_button = QPushButton("查看所选教材原页…")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        root.addWidget(self.open_button)
        close = QPushButton("关闭，返回选材")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._load)
        self.search.textChanged.connect(self._queue)
        QTimer.singleShot(0, self._load)

    def _queue(self, *_):
        self._request += 1
        self.results.clear()
        self.details.clear()
        self.open_button.setEnabled(False)
        self.status.setText("正在更新搜索结果…")
        self._timer.start()

    def _load(self):
        if self._closed:
            return
        self._request += 1
        request, query = self._request, self.search.text()
        self.open_button.setEnabled(False)
        def ready(result):
            if self._closed or request != self._request:
                return
            self.results.clear()
            self.details.clear()
            for row in result["assets"]:
                printed = row.get("printed_page")
                printed_label = str(printed) if type(printed) is int else "待核对"
                item = QListWidgetItem(row["volume_title"] + " · " + row["label"] + f" · PDF {row['pdf_page']} / 印刷 {printed_label}")
                item.setData(Qt.ItemDataRole.UserRole, dict(row))
                item.setToolTip(item.text())
                self.results.addItem(item)
            self.status.setText(f"显示 {self.results.count()} 项候选。查看不会勾选知识点或加入备课参考。" + ("\n" + "\n".join(result["notices"]) if result["notices"] else ""))
        def failed(message):
            if not self._closed and request == self._request:
                self.results.clear()
                self.details.clear()
                self.open_button.setEnabled(False)
                self.status.setText(message)
        self.tasks.submit("读取教材素材目录", lambda: self.facade.preparation_textbook_asset_options(query), on_success=ready, on_failure=failed)

    def _selected(self, item, *_):
        row = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self.open_button.setEnabled(isinstance(row, dict))
        if row is None:
            self.details.clear()
            return
        printed = row.get("printed_page")
        lines = [row["label"], "教材：" + row["volume_title"],
                 f"PDF文件第{row['pdf_page']}页；印刷页码：{printed if type(printed) is int else '待核对'}",
                 "候选说明：" + row["description"], "整页定位，尚未裁切；请对照原页核对。"]
        self.details.setPlainText("\n".join(lines))

    def _open(self):
        item = self.results.currentItem()
        if self._closed or item is None or not self.open_button.isEnabled():
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        from .textbook_source_dialog import TextbookSourceDialog
        selection = {key: row[key] for key in ("visual_asset_id", "revision")}
        dialog = TextbookSourceDialog(self.facade, selection, self, reading_mode="asset")
        dialog.exec()
        dialog.deleteLater()

    def reject(self):
        self._closed = True
        self._timer.stop()
        if self._own_tasks:
            self.tasks.shutdown(1000)
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
