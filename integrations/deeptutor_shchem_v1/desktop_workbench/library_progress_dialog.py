"""Read-only teacher progress view, populated on demand off the UI thread."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                              QPlainTextEdit, QPushButton, QVBoxLayout)

from ..desktop_library_progress import collect_library_progress


class LibraryProgressDialog(QDialog):
    def __init__(self, facade, tasks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本地题库进度")
        self.resize(850, 620)
        self.report = None
        self._closed = False
        self.tasks = tasks
        layout = QVBoxLayout(self)
        title = QLabel("从真实目录看进度，不重复导入已有资料")
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        self.summary = QLabel("正在读取已保存的Word和个人图片题目录…")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        actions = QHBoxLayout()
        self.save = QPushButton("导出进度 JSON")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.export_report)
        actions.addWidget(self.save)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._task_id = tasks.submit(
            "检查题库进度", lambda: collect_library_progress(facade),
            on_success=self.apply_report,
            on_failure=self.show_failure,
        )

    def done(self, result):
        self._closed = True
        self.tasks.cancel(self._task_id)
        super().done(result)

    def show_failure(self, message):
        if not self._closed:
            self.summary.setText(message)

    def apply_report(self, report):
        if self._closed:
            return
        self.report = report
        lines = []
        word = report.get("word")
        if word is not None:
            c = word["counts"]
            lines.append(f"Word：{word['source_count']}份来源，{c['candidates']}条候选。")
            lines.append(f"主知识点 {c['primary']} · 教材节 {c['curriculum']} · 适用年级 {c['grade']} · 原考试类型 {c['exam']}。")
            lines.append(f"四项齐备候选 {c['complete_candidates']} · 旧标签待重核 {c['stale_labels']} · 图文缺口 {c['material_gaps']}。")
        visual = report.get("visual")
        if visual is not None:
            lines.append(f"个人图片题：{visual['themes']}个主题，{visual['printed_questions']}道印刷题，{visual['not_selectable']}道暂不能选用。")
        lines.extend(report.get("errors", []))
        lines.append(report["note"])
        self.summary.setText("\n".join(lines))
        details = ["下一步：导入资料 → 导入历史核对；题库 → 我的讲义（Word） → 教学标签补全。",
                   "原考试类型找不到依据时保留待确认，不为追求完成率猜填。", ""]
        if word:
            details.extend(word["warnings"])
            for row in word["rows"]:
                if row["todo"]:
                    details.append(f"{row['source']} | {row['key']} | " + "、".join(row["todo"]))
        if visual:
            details.extend(visual["warnings"])
        self.details.setPlainText("\n".join(details))
        self.save.setEnabled(True)

    def export_report(self):
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存本机进度", "题库进度.json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.summary.setText("进度文件未保存，请检查目录权限后重试。")
        else:
            self.summary.setText("已保存本机进度；报告含来源名称，请不要直接上传公开仓库。")
