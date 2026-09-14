"""Native local-check view. No model requests or credentials are inspected."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout,
                              QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from ..desktop_environment import collect_environment_report, report_text
from .components import set_status


class EnvironmentDialog(QDialog):
    def __init__(self, paths, tasks, parent=None):
        super().__init__(parent)
        self.paths, self.tasks = paths, tasks
        self._active = None
        self._closed = False
        self.report = None
        self.setWindowTitle("本机检查 · 版本与依赖")
        self.resize(780, 650)
        self.setMinimumSize(480, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("这台电脑准备好了吗？")
        title.setObjectName("CardTitle")
        root.addWidget(title)
        self.status = QLabel("按功能检查依赖，不发送资料、不调用模型。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName("本机组件检查结果及处理建议")
        root.addWidget(self.output, 1)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("重新检查")
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button = QPushButton("复制报告")
        self.copy_button.clicked.connect(self.copy_report)
        self.save_button = QPushButton("导出 JSON")
        self.save_button.clicked.connect(self.export_report)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        for button in (self.refresh_button, self.copy_button, self.save_button, self.close_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        root.addLayout(actions)
        self.refresh()

    def refresh(self):
        if self._active or self._closed:
            return
        self.refresh_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        set_status(self.status, "info", "正在检查本机组件、资源和个人数据目录…")
        self._active = self.tasks.submit("本机检查", lambda: collect_environment_report(self.paths),
                                         on_success=self._loaded, on_failure=self._failed)

    def _loaded(self, report):
        if self._closed:
            return
        self._active = None
        self.report = report
        self.output.setPlainText(report_text(report))
        self.refresh_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        set_status(self.status, "info", "检查完成。仅需处理影响当前任务的项目；安装发现不等于成品验证。")

    def _failed(self, _message):
        if self._closed:
            return
        self._active = None
        self.refresh_button.setEnabled(True)
        set_status(self.status, "error", "检查未完成，请重试。没有修改原资料或模型设置。")

    def copy_report(self):
        if self.report:
            QApplication.clipboard().setText(report_text(self.report))
            set_status(self.status, "success", "已复制不含本机路径和模型配置的报告。")

    def export_report(self):
        if not self.report:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "保存本机检查报告", "本机检查.json", "JSON (*.json)")
        if not filename:
            return
        try:
            Path(filename).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            set_status(self.status, "error", "报告未保存，请检查所选目录。")
        else:
            set_status(self.status, "success", "报告已保存，不含本机路径、题目正文或模型配置。")

    def done(self, result):
        self._closed = True
        if self._active:
            self.tasks.cancel(self._active)
        super().done(result)
