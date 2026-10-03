"""Explicit local PDF preview, archival and duplicate continuation."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .components import FileSelectionPanel, section_title


class TextbookImportDialog(QDialog):
    imported = Signal()

    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.preview = None
        self.task_id = None
        self._busy = False
        self.setWindowTitle("导入整本教材PDF")
        self.resize(760, 700)
        self.setMinimumSize(360, 500)
        root = QVBoxLayout(self)
        root.addWidget(section_title("导入整本教材", "保留完整PDF供本地阅读；内容相同的文件继续原记录，内容不同的同名文件分别保存。"))
        self.files = FileSelectionPanel("可添加PDF或选择文件夹。每批最多100份、512MB。",
            title="教材原文件", supported_suffixes=frozenset({".pdf"}))
        self.files.files_changed.connect(self._changed)
        root.addWidget(self.files)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["原文件", "PDF页数", "处理方式"])
        self.results.setAccessibleName("整本教材导入预览")
        root.addWidget(self.results, 1)
        self.status = QLabel("先添加教材，预览后选择保存。印刷页码与教材目录需另行核对。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.preview_button = QPushButton("预览教材")
        self.preview_button.setObjectName("QuietButton")
        self.preview_button.clicked.connect(self._preview)
        self.save_button = QPushButton("保存到本机")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("QuietButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.stop_button)
        self.close_button = QPushButton("返回")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        root.addLayout(actions)
        self.tasks.task_finished.connect(self._finished)
        self.tasks.task_cancelled.connect(self._cancelled)

    def _changed(self):
        self.preview = None
        self.results.clear()
        self.save_button.setEnabled(False)

    def _set_busy(self, busy):
        self._busy = busy
        self.files.setEnabled(not busy)
        self.preview_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.preview is not None)
        self.stop_button.setEnabled(busy)
        self.close_button.setEnabled(not busy)

    def _preview(self):
        files = self.files.paths()
        self._set_busy(True)
        self.status.setText("正在核对本地教材文件与页数…")
        self.task_id = self.tasks.submit("预览整本教材", lambda: self.facade.textbook_workspace().preview_books(files),
            on_success=self._preview_ready, on_failure=self.status.setText, origin_route="textbooks")

    def _preview_ready(self, result):
        self.preview = result
        self.results.clear()
        for row in result["sources"]:
            QTreeWidgetItem(self.results, [row["source_name"], str(row["page_count"]),
                "继续已有内容" if row["duplicate"] else "保存完整原文件"])
        self.status.setText(f"已预览{len(result['sources'])}份PDF。保存只完成本机归档，目录与内容仍待核对。")

    def _save(self):
        if self.preview is None:
            return
        preview = self.preview
        self._set_busy(True)
        self.status.setText("正在保存完整原文件…")
        self.task_id = self.tasks.submit_progress("导入整本教材",
            lambda report, cancelled: self.facade.textbook_workspace().commit_books(preview,
                progress_callback=report, should_cancel=cancelled),
            on_progress=lambda p: self.status.setText(f"已处理{p['completed']} / {p['total']}份教材"),
            on_success=self._saved, on_failure=self.status.setText, origin_route="textbooks")

    def _saved(self, result):
        self.preview = None
        self.status.setText(f"已保存{result['added']}份，继续已有内容{result['reused']}份。可返回教材研读打开原文件。")
        self.imported.emit()

    def _stop(self):
        if self.task_id and self.tasks.cancel(self.task_id):
            self.status.setText("正在停止。已保存原文件保留，稍后可再次导入剩余教材。")

    def _cancelled(self, task_id, _label):
        if task_id == self.task_id:
            self.status.setText("已停止。此前保存的教材保留；重新预览可继续剩余文件。")

    def _finished(self, task_id):
        if task_id == self.task_id:
            self.task_id = None
            self._set_busy(False)

    def reject(self):
        if self._busy:
            self._stop()
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            self._stop()
            event.ignore()
        else:
            super().closeEvent(event)
