from __future__ import annotations

from datetime import datetime
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
)

from ..desktop_blueprint_generation import format_blueprint
from ..desktop_blueprint_review import (
    candidate_revision,
    format_review,
    format_revision,
)


class BlueprintReviewDialog(QDialog):
    """Inspect original/reasons/revision side by side in separate readable tabs."""

    def __init__(
        self,
        facade: Any,
        tasks: Any,
        preview_id: str,
        original: dict[str, Any],
        profile: Any,
        parent: Any = None,
        *,
        source_draft_id: str | None = None,
        source_label: str = "原始生成蓝图",
    ):
        super().__init__(parent)
        self.facade, self.tasks, self.profile = facade, tasks, profile
        self.preview_id = preview_id
        self.source_draft_id = source_draft_id
        self.source_label = source_label
        self._source_args = (
            {"source_draft_id": source_draft_id} if source_draft_id is not None else {}
        )
        self.source_revision = candidate_revision(original["candidate"])
        self._closed = self._running = False
        self._history: list[dict[str, Any]] = []
        self._result: dict[str, Any] | None = None
        self._resume_review_id: str | None = None
        self._active_review_id: str | None = None
        self._stop = Event()
        self.setWindowTitle("蓝图审校与修订")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 780)
        self.setMinimumSize(360, 540)
        self.finished.connect(self._finished)
        root = QVBoxLayout(self)
        intro = QLabel(
            "核对试剂干扰、样品变化、方程式及条件、材料一致性与证据范围。"
            "先保存诊断，再生成完整修订稿；中断后可只续接修订，不覆盖原稿。AI审校仍可能出错。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        source = QLabel("本次审校来源：" + source_label)
        source.setWordWrap(True)
        root.addWidget(source)
        model = QLabel(
            f"审校模型：{profile.provider_name} / {profile.model_id}"
            if profile is not None
            else "未选择可用模型；仍可离线查看已有审校记录。"
        )
        model.setWordWrap(True)
        root.addWidget(model)
        self.focus = QPlainTextEdit()
        self.focus.setPlaceholderText(
            "可选：补充想重点检查的问题（最多2000字，不填也会执行完整审校）"
        )
        self.focus.setMaximumHeight(85)
        root.addWidget(self.focus)
        self.history = QComboBox()
        self.history.addItem("正在读取此蓝图的审校历史…")
        self.history.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.history.setMinimumContentsLength(16)
        self.history.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.history.currentIndexChanged.connect(self._load_history)
        root.addWidget(self.history)
        actions = QHBoxLayout()
        self.start = QPushButton("审校并修订")
        self.start.setEnabled(False)
        self.start.clicked.connect(self._start)
        self.stop = QPushButton("停止")
        self.stop.hide()
        self.stop.clicked.connect(self.request_stop)
        self.copy = QPushButton("复制修订稿")
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self._copy)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        for button in (self.start, self.stop, self.copy, self.close_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.status = QLabel("待审稿只读；开始审校前会再次确认发送内容。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.original = QPlainTextEdit(format_blueprint(original))
        self.findings = QPlainTextEdit()
        self.revised = QPlainTextEdit()
        for label, widget in (
            ("教师待审稿" if source_draft_id else "原始蓝图", self.original),
            ("问题与理由", self.findings),
            ("修订稿", self.revised),
        ):
            widget.setReadOnly(True)
            self.tabs.addTab(widget, label)
        root.addWidget(self.tabs, 1)
        self.tasks.submit(
            "读取蓝图审校历史",
            self._read_history,
            on_success=self._history_loaded,
            on_failure=self._history_failed,
        )

    def _read_history(self):
        return self.facade.prompt_blueprint_reviews(
            self.preview_id,
            self.source_revision,
            **self._source_args,
        )

    def _finished(self, _result: int):
        self._closed = True
        self._stop.set()

    def closeEvent(self, event: Any):
        if self._running:
            self.request_stop()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self._running:
            self.request_stop()
        else:
            super().reject()

    def _history_loaded(self, records: Any):
        if self._closed:
            return
        self._history = list(records)
        self.history.blockSignals(True)
        self.history.clear()
        self.history.addItem("新建审校 / 选择历史记录（查看不调用模型）")
        for record in self._history:
            try:
                stamp = (
                    datetime.fromisoformat(record["created_at"])
                    .astimezone()
                    .strftime("%m-%d %H:%M")
                )
            except (ValueError, KeyError, TypeError):
                stamp = "时间未知"
            complete = record.get("status") == "completed"
            report = (
                record["result"]["report"]
                if complete
                else record["diagnosis_result"]["candidate"]
            )
            self.history.addItem(
                f"{stamp} · {len(report['issues'])} 项意见 · "
                + ("完整修订" if complete else "诊断已存，可续修")
            )
        self.history.blockSignals(False)
        self.start.setEnabled(not self._running and self.profile is not None)
        if not self._running and self._active_review_id:
            for index, record in enumerate(self._history, 1):
                if record["review_id"] == self._active_review_id:
                    self.history.setCurrentIndex(index)
                    break

    def _load_history(self, index: int):
        if self._closed or self._running:
            return
        self._resume_review_id = None
        self.start.setText("审校并修订")
        self.focus.setEnabled(True)
        if index == 0:
            self._active_review_id = None
            self._result = None
            self.findings.clear()
            self.revised.clear()
            self.copy.setEnabled(False)
            self.status.setText(
                "新建审校将重新运行诊断与修订；可在历史中选择未完成记录只续修。"
            )
            return
        if not 0 < index <= len(self._history):
            return
        record = self._history[index - 1]
        self._active_review_id = record["review_id"]
        self.focus.setPlainText(record.get("focus", ""))
        if record.get("status") == "completed":
            self._show_result(record["result"])
        else:
            self._resume_review_id = record["review_id"]
            self._result = None
            self.findings.setPlainText(
                format_review({"report": record["diagnosis_result"]["candidate"]})
            )
            self.revised.setPlainText(
                "完整修订稿尚未生成。诊断已保存，可点击“继续修订”；不会重新调用诊断。"
            )
            self.tabs.setCurrentWidget(self.findings)
            self.copy.setEnabled(False)
            self.focus.setEnabled(False)
            self.start.setText("继续修订")
            self.status.setText(
                "诊断已保存；续接仅运行修订。如需改变关注点，请选择“新建审校”。"
            )

    def _start(self):
        if self._closed or self._running or self.profile is None:
            return
        focus = self.focus.toPlainText().strip()
        resume_id = self._resume_review_id
        if len(focus) > 2000:
            self.status.setText("审校关注点请控制在2000字以内。")
            return
        answer = QMessageBox.question(
            self,
            "确认审校与修订",
            f"模型：{self.profile.provider_name} / {self.profile.model_id}\n\n"
            f"待审来源：{self.source_label}\n"
            "将发送此份已保存待审稿、其教材/讲义文字依据、原命题任务和你填写的关注点。"
            "不发送图片、来源文件或学生资料。\n\n"
            + (
                "本次只续接修订，使用已保存的诊断；一次调用，最长等待5分钟。"
                if resume_id
                else "本次先诊断再修订，共两次模型调用，每步最长等待5分钟；诊断成功后会先保存。"
            )
            + "可能产生费用，结果仍需教师核验。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("已取消审校，没有发送新请求。")
            return
        self._stop.clear()
        self._running = True
        self._result = None
        if not resume_id:
            self.findings.clear()
        self.revised.setPlainText("完整修订稿正在生成；原稿保留在左侧标签中。")
        self._busy(True)
        self.status.setText(
            "正在继续修订，最长等待5分钟。"
            if resume_id
            else "第1步：正在诊断，最长等待5分钟；可以停止。"
        )
        self.tasks.submit_progress(
            "审校与修订主题蓝图",
            lambda progress, cancelled: self.facade.review_prompt_blueprint(
                self.preview_id,
                self.source_revision,
                self.profile.profile_id,
                self.profile.revision,
                teacher_confirmed=True,
                focus=focus,
                resume_review_id=resume_id,
                on_progress=progress,
                should_cancel=lambda: self._stop.is_set() or cancelled(),
                **self._source_args,
            ),
            on_success=self._completed,
            on_failure=self._failed,
            on_progress=self._progress,
        )

    def _progress(self, value: Any):
        if self._closed or not self._running or not isinstance(value, dict):
            return
        self._active_review_id = value.get("review_id", self._active_review_id)
        if isinstance(value.get("diagnosis_report"), dict):
            self.findings.setPlainText(
                format_review({"report": value["diagnosis_report"]})
            )
            self.tabs.setCurrentWidget(self.findings)
        if not self._stop.is_set():
            self.status.setText(
                "第2步：诊断已保存，正在生成完整修订稿；中断后可续接此步。"
                if value.get("stage") == "revision"
                else "第1步：正在诊断；完成后先保存问题清单。"
            )

    def _busy(self, busy: bool):
        self.start.setEnabled(not busy and self.profile is not None)
        self.focus.setEnabled(not busy and self._resume_review_id is None)
        self.history.setEnabled(not busy)
        self.copy.setEnabled(not busy and self._result is not None)
        self.stop.setVisible(busy)
        self.stop.setEnabled(busy)

    def request_stop(self):
        self._stop.set()
        self.stop.setEnabled(False)
        self.status.setText("正在停止审校，等待请求结束；可能已产生部分费用。")

    def _show_result(self, result: dict[str, Any]):
        self._result = result
        self.findings.setPlainText(format_review(result))
        self.revised.setPlainText(format_revision(result))
        self.tabs.setCurrentWidget(self.findings)
        self.copy.setEnabled(True)
        self.status.setText(
            f"审校记录已保存 · {len(result['report']['issues'])} 项意见 · "
            f"成功阶段耗时合计 {result['latency_ms'] / 1000:.1f}秒。修订稿仍需教师核验，原稿保留。"
        )

    def _completed(self, result: dict[str, Any]):
        if self._closed:
            return
        self._running = False
        self._resume_review_id = None
        self._active_review_id = result["review_id"]
        self._busy(False)
        self._show_result(result)
        self.tasks.submit(
            "更新审校历史",
            self._read_history,
            on_success=self._history_loaded,
            on_failure=self._history_failed,
        )

    def _history_failed(self, message: str):
        if self._closed:
            return
        if not self._running:
            self.start.setEnabled(self.profile is not None)
            self.status.setText(message + " 暂未刷新审校历史；当前原稿和修订稿保留。")

    def _failed(self, message: str):
        if self._closed:
            return
        self._running = False
        self._busy(False)
        self.status.setText(message + " 待审稿与原始蓝图均未修改。")
        self.tasks.submit(
            "读取已保存的诊断",
            self._read_history,
            on_success=self._history_loaded,
            on_failure=self._history_failed,
        )

    def _copy(self):
        if self._result is not None and not self._running:
            QGuiApplication.clipboard().setText(self.revised.toPlainText())
            self.status.setText("已复制AI修订稿及待核验标记。")
