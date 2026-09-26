"""Inspect a saved batch and retry explicitly selected failed Word files."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QLabel, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QStyledItemDelegate, QVBoxLayout,
)

from .components import set_status


def _label(text="", role="MutedLabel"):
    value = QLabel(text)
    value.setObjectName(role)
    value.setTextFormat(Qt.TextFormat.PlainText)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    return value


class _FileResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        # Qt's default wrapped size can omit the checkbox width. Reserve it for
        # every row so the status line remains visible after a narrow resize.
        width = max(40, self.parent().viewport().width() - 48)
        bounds = QFontMetrics(option.font).boundingRect(
            QRect(0, 0, width, 100000),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere,
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
        )
        return QSize(width + 48, bounds.height() + 24)


class ImportBatchDialog(QDialog):
    def __init__(self, facade, tasks, batch_id, parent=None):
        super().__init__(parent)
        self.facade, self.tasks, self.batch_id = facade, tasks, batch_id
        self.receipt = None
        self.changed = False
        self._task_id = None
        self._busy = False
        self.setWindowTitle("导入批次 · 文件处理结果")
        self.resize(700, 650)
        self.setMinimumSize(320, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        root.addWidget(_label("本批文件处理结果", "PageTitle"))
        self.summary = _label()
        root.addWidget(self.summary)
        self.visual_status = _label()
        root.addWidget(self.visual_status)
        root.addWidget(_label("仅勾选需要重试的 Word。已成功文件保持，题目标签另外核对。"))
        self.files = QListWidget()
        self.files.setItemDelegate(_FileResultDelegate(self.files))
        self.files.setAccessibleName("本批文件处理结果；仅失败的Word可勾选重试")
        self.files.setWordWrap(True)
        self.files.setResizeMode(QListView.ResizeMode.Adjust)
        self.files.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.itemChanged.connect(self._update_actions)
        root.addWidget(self.files, 1)
        self.status = _label("", "StatusInfo")
        root.addWidget(self.status)
        self.retry_button = QPushButton("重试所选失败文件")
        self.retry_button.setObjectName("PrimaryButton")
        self.retry_button.clicked.connect(self._retry)
        root.addWidget(self.retry_button)
        self.refresh_button = QPushButton("刷新处理结果")
        self.refresh_button.setObjectName("QuietButton")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(self.refresh_button)
        self.close_button = QPushButton("返回导入资料")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.accept)
        root.addWidget(self.close_button)
        self.tasks.task_finished.connect(self._finished)
        self.refresh()

    def refresh(self):
        if self._busy:
            return
        try:
            self._display(self.facade.import_batch_details(self.batch_id))
        except Exception:
            self.receipt = None
            self.files.clear()
            self.summary.setText("这批资料的处理记录暂时无法核对。")
            self.visual_status.clear()
            set_status(self.status, "error", "未修改原件或处理结果。请刷新后再试。")
            self._update_actions()

    def _display(self, receipt):
        self.receipt = receipt
        self.summary.setText(
            f"{receipt.source_type} · {receipt.source_count} 份来源\n"
            f"Word：成功 {receipt.native_completed_count} 份，失败 {receipt.native_failed_count} 份"
        )
        visual = {
            "not_required": "本批无需图片识别。",
            "completed": "图片候选已生成，等待逐页核对；此次Word重试不会替换它。",
            "failed": "图片识别未完成，可返回选择模型后整批续做。",
        }.get(receipt.visual_status, "图片已保存，待发送前预览与确认；返回后可继续。")
        self.visual_status.setText(visual)
        self.files.blockSignals(True)
        self.files.clear()
        for index, row in enumerate(receipt.native_files, 1):
            state = "读取成功" if row.status == "completed" else "读取失败 · 可重试"
            attempts = f"已尝试 {row.attempt_count} 次" if row.attempt_count_known else "历史尝试次数未记录"
            item = QListWidgetItem(f"{index}. {row.filename}\n{state} · {attempts}")
            item.setData(Qt.ItemDataRole.UserRole, row.source_id)
            if row.status == "failed":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.files.addItem(item)
        self.files.blockSignals(False)
        if receipt.native_failed_count and not receipt.native_files:
            set_status(self.status, "attention", "旧批次缺少逐文件失败清单；请先核对原批次记录。")
        elif receipt.native_failed_count:
            set_status(self.status, "attention", "勾选失败文件后重试。只读取本机已保存的同一份原件，不调用模型。")
        else:
            set_status(self.status, "success", "本批没有待重试的Word文件。图片与题目标签仍按各自状态核对。")
        self._update_actions()

    def _selection(self):
        return [self.files.item(index) for index in range(self.files.count())
                if self.files.item(index).checkState() == Qt.CheckState.Checked]

    def _update_actions(self, *_args):
        self.retry_button.setEnabled(not self._busy and self.receipt is not None and bool(self._selection()))
        self.refresh_button.setEnabled(not self._busy)
        self.close_button.setEnabled(not self._busy)
        self.files.setEnabled(not self._busy)

    def _retry(self):
        if self._busy or self.receipt is None:
            return
        selected = self._selection()
        if not selected:
            return
        names = "\n".join(item.text().split("\n")[0] for item in selected)
        if QMessageBox.question(self, "重试选中的文件", f"将重试以下 {len(selected)} 份失败Word：\n{names}\n\n已成功文件及教师标签保持。是否继续？") != QMessageBox.StandardButton.Yes:
            return
        revision = self.receipt.native_revision
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        self._busy = True
        self._update_actions()
        set_status(self.status, "info", f"正在本机重试 {len(ids)} 份文件并保存结果；完成后可关闭。")
        try:
            self._task_id = self.tasks.submit_progress(
                "重试失败Word文件",
                lambda _report, _cancelled: self.facade.retry_failed_word_import_files(
                    self.batch_id, expected_revision=revision, source_ids=ids,
                ),
                on_success=self._saved, on_failure=self._failed,
            )
        except RuntimeError:
            self._busy = False
            self._failed("重试任务暂未启动，请刷新后再试。")
            self._update_actions()

    def _saved(self, receipt):
        self.changed = True
        self._display(receipt)

    def _failed(self, message):
        # Stale selections must never remain eligible for another click.
        self.receipt = None
        self.files.clear()
        set_status(self.status, "error", str(message) + " 请刷新处理结果后重新选择。")

    def _finished(self, task_id):
        if task_id != self._task_id:
            return
        self._task_id = None
        self._busy = False
        self._update_actions()

    def done(self, result):
        if not self._busy:
            super().done(result)

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
