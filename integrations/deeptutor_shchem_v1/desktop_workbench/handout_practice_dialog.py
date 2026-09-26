from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .handout_candidate_dialog import combo, label


class PracticeList(QListWidget):
    def fit_rows(self):
        width = max(100, self.viewport().width() - 20)
        for index in range(self.count()):
            item = self.item(index)
            bounds = self.fontMetrics().boundingRect(
                QRect(0, 0, width - 28, 10000), Qt.TextFlag.TextWordWrap, item.text()
            )
            item.setSizeHint(QSize(width, bounds.height() + 24))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_rows()


class HandoutPracticeDialog(QDialog):
    """Arrange a personal handout practice and reopen editable local exports."""

    def __init__(
        self,
        facade: Any,
        tasks: Any,
        selections: list[dict[str, str]],
        catalog: list[dict[str, Any]],
        parent: Any = None,
    ):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self._catalog = {item["key"]: item for item in catalog}
        self._selections = [dict(item) for item in selections]
        self._busy = False
        self._dirty = bool(selections)
        self._closed = False
        self._saved_draft = None
        self._history: list[dict[str, Any]] = []
        self.setWindowTitle("讲义选题练习 · 可编辑 Word")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(780, 750)
        self.setMinimumSize(360, 600)
        root = QVBoxLayout(self)
        root.addWidget(
            label(
                "调整顺序后导出学生版和教师参考答案。保留原生上下标、原题号与题组；不会调用模型。"
            )
        )
        self.editor = QWidget()
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit("上海高中化学讲义练习")
        self.title.setMaxLength(100)
        layout.addWidget(label("练习标题"))
        layout.addWidget(self.title)
        self.count = label()
        layout.addWidget(self.count)
        self.items = PracticeList()
        self.items.setWordWrap(True)
        self.items.setMinimumWidth(0)
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        self.up = QPushButton("上移")
        self.down = QPushButton("下移")
        self.remove = QPushButton("移出")
        for button in (self.up, self.down, self.remove):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label("每题额外留白行数"))
        self.lines = QSpinBox()
        self.lines.setRange(0, 8)
        self.lines.setValue(2)
        row.addWidget(self.lines)
        row.addStretch(1)
        layout.addLayout(row)
        self.restore = QPushButton("恢复上次保存的选题")
        self.restore.setEnabled(False)
        layout.addWidget(self.restore)
        self.save = QPushButton("保存选题草稿")
        self.export = QPushButton("导出学生版与教师参考答案")
        layout.addWidget(self.save)
        layout.addWidget(self.export)
        root.addWidget(self.editor, 1)
        root.addWidget(
            label(
                "参考答案来自原讲义，非官方；个人核对状态保留在导出记录中，不等于化学正确性验收。"
            )
        )
        self.history = combo()
        self.history.addItem("尚无导出记录", None)
        root.addWidget(self.history)
        row = QHBoxLayout()
        self.student = QPushButton("打开学生版")
        self.teacher = QPushButton("打开教师版")
        row.addWidget(self.student)
        row.addWidget(self.teacher)
        root.addLayout(row)
        self.status = label()
        root.addWidget(self.status)
        self.close_button = QPushButton("关闭")
        root.addWidget(self.close_button)
        self.title.textChanged.connect(self._edited)
        self.lines.valueChanged.connect(self._edited)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.remove.clicked.connect(self._remove)
        self.restore.clicked.connect(self._restore)
        self.save.clicked.connect(lambda: self._submit(False))
        self.export.clicked.connect(lambda: self._submit(True))
        self.student.clicked.connect(lambda: self._open("student"))
        self.teacher.clicked.connect(lambda: self._open("teacher"))
        self.history.currentIndexChanged.connect(self._history_changed)
        self.close_button.clicked.connect(self.close)
        self._render_items()
        self._history_changed()
        self.editor.setEnabled(False)
        self.tasks.submit(
            "读取练习草稿与导出记录",
            self.facade.handout_practice_state,
            on_success=self._loaded,
            on_failure=self._failed,
        )

    def _edited(self, *_args):
        self._dirty = True

    def _render_items(self, selected: int = 0):
        self.items.clear()
        for number, selection in enumerate(self._selections, 1):
            item = self._catalog.get(selection["key"])
            text = (
                f"{number}  {item['title']}\n{item['source_name']}"
                if item
                else f"{number}  来源当前不可用，请移出后重新选题"
            )
            if item and item["revision"] != selection["revision"]:
                text += "\n资料已更新，请重新选题"
            self.items.addItem(QListWidgetItem(text))
        self.items.setCurrentRow(min(selected, len(self._selections) - 1))
        self.items.fit_rows()
        self.count.setText(f"已选 {len(self._selections)} 题 · 按下列顺序输出")
        self.export.setEnabled(bool(self._selections))
        self.save.setEnabled(bool(self._selections))

    def _move(self, delta):
        current = self.items.currentRow()
        target = current + delta
        if 0 <= current < len(self._selections) and 0 <= target < len(self._selections):
            self._selections[current], self._selections[target] = (
                self._selections[target],
                self._selections[current],
            )
            self._dirty = True
            self._render_items(target)

    def _remove(self):
        current = self.items.currentRow()
        if 0 <= current < len(self._selections):
            self._selections.pop(current)
            self._dirty = True
            self._render_items(current)

    def _loaded(self, value):
        if self._closed:
            return
        self._saved_draft = value.get("draft")
        self.editor.setEnabled(True)
        self.restore.setEnabled(bool(self._saved_draft))
        self._history = value.get("history", [])
        self._render_history()

    def _restore(self):
        if not self._saved_draft:
            return
        if (
            self._dirty
            and QMessageBox.question(
                self,
                "替换当前选题",
                "用上次保存的草稿替换当前选题和标题？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._selections = [dict(s) for s in self._saved_draft["selections"]]
        self.title.setText(self._saved_draft["title"])
        self.lines.setValue(self._saved_draft["answer_lines"])
        self._render_items()
        self._dirty = False
        self.status.setText("已恢复选题；导出时会重新核验原始 Word 和资料版本。")

    def _render_history(self):
        self.history.clear()
        for record in self._history:
            self.history.addItem(
                f"{record['title']} · {len(record['items'])} 题 · {record['created_at'][:19].replace('T', ' ')} UTC",
                record["export_id"],
            )
        if not self._history:
            self.history.addItem("尚无导出记录", None)
        self._history_changed()

    def _history_changed(self, *_args):
        enabled = self.history.currentData() is not None and not self._busy
        self.student.setEnabled(enabled)
        self.teacher.setEnabled(enabled)

    def _submit(self, export: bool):
        if self._busy:
            return
        self._busy = True
        self.editor.setEnabled(False)
        self._history_changed()
        self.status.setText(
            "正在核验来源并生成两份 Word…" if export else "正在保存选题…"
        )
        title, selections, lines = (
            self.title.text(),
            [dict(s) for s in self._selections],
            self.lines.value(),
        )
        operation = (
            self.facade.export_handout_practice
            if export
            else self.facade.save_handout_practice
        )
        try:
            self.tasks.submit(
                "导出讲义练习" if export else "保存讲义选题",
                lambda: operation(title, selections, lines),
                on_success=lambda result: self._done(result, export),
                on_failure=self._failed,
            )
        except Exception:
            self._failed("任务未能启动，请稍后重试。")

    def _done(self, result, export):
        if self._closed:
            return
        self._busy = False
        self.editor.setEnabled(True)
        self._dirty = False
        self._saved_draft = {
            "title": self.title.text(),
            "selections": [dict(s) for s in self._selections],
            "answer_lines": self.lines.value(),
        }
        self.restore.setEnabled(True)
        if export:
            self._history = [result, *self._history][:20]
            self._render_history()
            self.status.setText(
                "两份可编辑 Word 已生成，选题已保存。可用下方按钮打开；完整来源记录保存在同一文件夹。"
            )
        else:
            self.status.setText("选题草稿已保存，关闭后可恢复继续。")
        self._history_changed()

    def _failed(self, message):
        if not self._closed:
            self._busy = False
            self.editor.setEnabled(True)
            self._history_changed()
            self.status.setText(message)

    def _open(self, role):
        export_id = self.history.currentData()
        if not export_id:
            return
        try:
            path = self.facade.handout_practice_artifact_path(export_id, role)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.status.setText(
                    "未找到可用的 Word 打开程序；文件位置：" + str(path)
                )
            else:
                self.status.setText("已请求打开：" + str(path))
        except Exception as exc:
            self.status.setText(
                getattr(exc, "message_zh", "文件暂时无法打开，请检查导出目录。")
            )

    def closeEvent(self, event):
        if self._busy:
            self.status.setText("正在写入练习或草稿，请完成后再关闭。")
            event.ignore()
            return
        if (
            self._dirty
            and QMessageBox.question(
                self,
                "尚未保存选题",
                "关闭并放弃本次尚未保存的选题调整？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            event.ignore()
            return
        self._closed = True
        super().closeEvent(event)

    def reject(self):
        self.close()
