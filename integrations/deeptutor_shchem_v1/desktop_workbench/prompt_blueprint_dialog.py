from __future__ import annotations

from datetime import datetime
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_theme_structure_reference import REFERENCE_KEY, REFERENCE_LABEL


class PromptBlueprintDialog(QDialog):
    """Local preview first; one explicitly confirmed, saved model blueprint."""

    def __init__(self, facade: Any, tasks: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.setWindowTitle("教材命题提示")
        self.resize(800, 760)
        self.setMinimumSize(360, 500)
        self._closed = False
        self._preview: dict[str, Any] | None = None
        self._generated_result: dict[str, Any] | None = None
        self._review_dialog: Any = None
        self._edit_dialog: Any = None
        self._generating = False
        self._preview_completed = False
        self._stop = Event()
        self._history: list[dict[str, Any]] = []
        self.finished.connect(self._finished)
        root = QVBoxLayout(self)
        introduction = QLabel(
            "选择教材章节，填写学习目标，可加入已保存的讲义选题与试题结构参考。"
            "编译不消耗模型额度；确认后可生成并保存主题命题蓝图。"
            "蓝图不是完整试卷，题面、数据与答案仍需完善和核验。"
        )
        introduction.setWordWrap(True)
        root.addWidget(introduction)
        self.inputs_toggle = QPushButton("收起命题要求与教材")
        self.inputs_toggle.clicked.connect(self._toggle_inputs)
        root.addWidget(self.inputs_toggle)
        self.inputs_panel = QWidget()
        inputs_layout = QVBoxLayout(self.inputs_panel)
        inputs_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.form = form
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(80)
        self.title_input.setPlaceholderText("例如：氧化还原反应主题练习")
        self.grade = QComboBox()
        for label, value in (("高一", 10), ("高二", 11), ("高三", 12)):
            self.grade.addItem(label, value)
        self.grade.setCurrentIndex(2)
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText(
            "希望学生学会什么？最多 500 字。请填写自己的目标，不粘贴整题或教材原文。"
        )
        self.goal.setMaximumHeight(90)
        form.addRow("课题", self.title_input)
        form.addRow("年级", self.grade)
        form.addRow("学习目标", self.goal)
        self.theme_reference = QComboBox()
        self.theme_reference.addItem("仅使用教材与通用命题规则", "none")
        self.theme_reference.addItem(REFERENCE_LABEL, REFERENCE_KEY)
        self.theme_reference.setMinimumContentsLength(16)
        self.theme_reference.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.theme_reference.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.theme_reference.setToolTip(
            "可参考已整理试题的材料复用、小题顺序和依赖；不照搬原题、答案或难度标签。"
        )
        form.addRow("试题结构参考", self.theme_reference)
        self.handout_reference = QComboBox()
        self.handout_reference.addItem("不加入讲义选题", None)
        self.handout_reference.setMinimumContentsLength(16)
        self.handout_reference.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.handout_reference.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.handout_reference.setToolTip(
            "先在讲义候选中选题并保存练习。这里读取原始题目内容，不读取你修改后的导出 Word。"
        )
        form.addRow("讲义选题参考", self.handout_reference)
        self.include_handout_answers = QCheckBox("同时加入配对解答（非官方，待核验）")
        self.include_handout_answers.setEnabled(False)
        form.addRow(self.include_handout_answers)
        self.profile = QComboBox()
        self.profile.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.profile.setMinimumContentsLength(16)
        self.profile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.profile.addItem("正在读取模型配置…", None)
        form.addRow("生成模型", self.profile)
        self.history = QComboBox()
        self.history.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.history.setMinimumContentsLength(16)
        self.history.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.history.addItem("最近保存的蓝图（选择后查看）")
        self.history.currentIndexChanged.connect(self._load_history)
        form.addRow("最近蓝图", self.history)
        inputs_layout.addLayout(form)
        inputs_layout.addWidget(QLabel("教材章节　勾选 1—12 节"))
        self.sections = QListWidget()
        self.sections.setMinimumHeight(125)
        self.sections.setMaximumHeight(165)
        self.sections.setWordWrap(True)
        self.sections.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inputs_layout.addWidget(self.sections)
        self.inputs_scroll = QScrollArea()
        self.inputs_scroll.setWidgetResizable(True)
        self.inputs_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.inputs_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.inputs_scroll.setWidget(self.inputs_panel)
        self.inputs_scroll.setMinimumHeight(80)
        self.inputs_scroll.setMaximumHeight(380)
        root.addWidget(self.inputs_scroll, 1)
        actions = QHBoxLayout()
        self.compile_button = QPushButton("编译本地提示")
        self.compile_button.setEnabled(False)
        self.compile_button.clicked.connect(self._compile)
        self.generate_button = QPushButton("生成蓝图")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self._generate)
        self.stop_button = QPushButton("停止")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._request_stop)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        actions.addWidget(self.compile_button)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        root.addLayout(actions)
        self.review_button = QPushButton("审校与修订已生成蓝图")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self._review)
        root.addWidget(self.review_button)
        self.edit_button = QPushButton("教师修订 / 打开本地蓝图草稿")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self._edit)
        root.addWidget(self.edit_button)
        self.status = QLabel("正在读取教材目录…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.evidence_output = QPlainTextEdit()
        self.system_output = QPlainTextEdit()
        self.task_output = QPlainTextEdit()
        self.generated_output = QPlainTextEdit()
        for label, widget in (
            ("资料依据", self.evidence_output),
            ("系统提示", self.system_output),
            ("本次任务提示", self.task_output),
            ("生成蓝图", self.generated_output),
        ):
            widget.setReadOnly(True)
            self.tabs.addTab(widget, label)
        root.addWidget(self.tabs, 1)
        self.profile.currentIndexChanged.connect(self._update_generate_enabled)
        self.title_input.textChanged.connect(self._invalidate_preview)
        self.goal.textChanged.connect(self._invalidate_preview)
        self.grade.currentIndexChanged.connect(self._invalidate_preview)
        self.theme_reference.currentIndexChanged.connect(self._invalidate_preview)
        self.handout_reference.currentIndexChanged.connect(self._handout_changed)
        self.include_handout_answers.toggled.connect(self._invalidate_preview)
        self.sections.itemChanged.connect(self._invalidate_preview)
        self.tasks.submit(
            "读取命题教材目录",
            self.facade.prompt_curriculum_sections,
            on_success=self._loaded,
            on_failure=self._failed,
        )
        self.tasks.submit(
            "读取蓝图模型与历史",
            lambda: (
                getattr(self.facade, "preparation_profiles", lambda: ())(),
                getattr(self.facade, "prompt_blueprint_history", lambda: ())(),
                getattr(self.facade, "prompt_handout_references", lambda: ())(),
            ),
            on_success=self._models_loaded,
            on_failure=self._failed,
        )

    def _finished(self, _result: int) -> None:
        self._closed = True
        self._stop.set()

    def _toggle_inputs(self) -> None:
        self._show_inputs(not self.inputs_panel.isVisible())

    def _show_inputs(self, visible: bool) -> None:
        self.inputs_panel.setVisible(visible)
        self.inputs_scroll.setVisible(visible)
        self.inputs_toggle.setText(
            "收起命题要求与教材" if visible else "展开命题要求与教材"
        )

    def resizeEvent(self, event: Any) -> None:
        if hasattr(self, "form"):
            self.form.setRowWrapPolicy(
                QFormLayout.RowWrapPolicy.WrapAllRows
                if self.width() < 580
                else QFormLayout.RowWrapPolicy.WrapLongRows
            )
        super().resizeEvent(event)

    def closeEvent(self, event: Any) -> None:
        if self._edit_dialog is not None and not self._edit_dialog.close():
            event.ignore()
            return
        if self._review_dialog is not None and not self._review_dialog.close():
            event.ignore()
            return
        if self._generating:
            self._request_stop()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._edit_dialog is not None and not self._edit_dialog.close():
            return
        if self._review_dialog is not None and not self._review_dialog.close():
            return
        if self._generating:
            self._request_stop()
            return
        super().reject()

    def _models_loaded(self, value: Any) -> None:
        if self._closed:
            return
        profiles, history, *references = value
        self.handout_reference.blockSignals(True)
        self.handout_reference.clear()
        self.handout_reference.addItem("不加入讲义选题", None)
        for reference in references[0] if references else ():
            self.handout_reference.addItem(
                reference["label"],
                {
                    "reference_id": reference["reference_id"],
                    "revision": reference["revision"],
                },
            )
        if self.handout_reference.count() == 1:
            self.handout_reference.setToolTip(
                "还没有保存的讲义练习。先在讲义候选中选题并保存，再打开此窗口。"
            )
        self.handout_reference.blockSignals(False)
        self.profile.clear()
        for profile in profiles:
            self.profile.addItem(
                f"{profile.provider_name} / {profile.model_id}", profile
            )
        if not profiles:
            self.profile.addItem("尚无可用的结构化文字模型，请到设置配置", None)
        self._history_loaded(history)
        self._update_generate_enabled()

    def _history_loaded(self, history: Any) -> None:
        if self._closed:
            return
        self._history = list(history)
        self.history.blockSignals(True)
        self.history.clear()
        self.history.addItem("最近保存的蓝图（选择后查看）")
        for item in self._history:
            stamp = item.get("result", {}).get("created_at", "")
            try:
                label = (
                    datetime.fromisoformat(stamp).astimezone().strftime("%m-%d %H:%M")
                )
            except (TypeError, ValueError):
                label = "时间未知"
            self.history.addItem(f"{item['preview']['title']} · {label}")
        self.history.blockSignals(False)

    def _load_history(self, index: int) -> None:
        if index <= 0 or self._generating or index > len(self._history):
            return
        record = self._history[index - 1]
        payload = record.get("input", {})
        self.title_input.setText(payload.get("title", record["preview"]["title"]))
        self.goal.setPlainText(payload.get("learning_goal", ""))
        self.grade.setCurrentIndex(
            max(0, self.grade.findData(payload.get("grade", 12)))
        )
        self.theme_reference.setCurrentIndex(
            max(
                0, self.theme_reference.findData(payload.get("theme_reference", "none"))
            )
        )
        handout = payload.get("handout_reference")
        binding = (
            {key: handout[key] for key in ("reference_id", "revision")}
            if handout
            else None
        )
        index = self.handout_reference.findData(binding)
        if index < 0:
            self.handout_reference.addItem(
                "历史讲义选题（重新编译时核验来源）", binding
            )
            index = self.handout_reference.count() - 1
        self.handout_reference.setCurrentIndex(index)
        self.include_handout_answers.setChecked(
            bool(handout and handout.get("include_answers"))
        )
        for i in range(self.sections.count()):
            item = self.sections.item(i)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole)
                in payload.get("section_keys", [])
                else Qt.CheckState.Unchecked
            )
        self._compiled(record["preview"])
        self._generated(record["result"])

    def _loaded(self, sections: Any) -> None:
        if self._closed:
            return
        for section in sections:
            item = QListWidgetItem(section.display_label_zh)
            item.setData(Qt.ItemDataRole.UserRole, section.section_key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.sections.addItem(item)
        self.compile_button.setEnabled(self.sections.count() > 0)
        self.status.setText(
            "请选择章节并填写课题、学习目标，可选试题结构参考。编译阶段全程本地，不消耗模型额度。"
        )

    def _compile(self) -> None:
        self._show_inputs(True)
        self._preview = None
        self._preview_completed = False
        self._generated_result = None
        self.generated_output.clear()
        self._update_generate_enabled()
        payload = {
            "title": self.title_input.text(),
            "learning_goal": self.goal.toPlainText(),
            "grade": self.grade.currentData(),
            "theme_reference": self.theme_reference.currentData(),
            "handout_reference": (
                {
                    **self.handout_reference.currentData(),
                    "include_answers": self.include_handout_answers.isChecked(),
                }
                if self.handout_reference.currentData()
                else None
            ),
            "section_keys": [
                self.sections.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.sections.count())
                if self.sections.item(index).checkState() == Qt.CheckState.Checked
            ],
        }
        self.compile_button.setEnabled(False)
        self._set_inputs_enabled(False)
        self.status.setText("正在核验本地来源并编译提示…")
        for widget in (self.evidence_output, self.system_output, self.task_output):
            widget.clear()
        self.tasks.submit(
            "编译教材命题提示",
            lambda: self.facade.compile_prompt_blueprint(payload),
            on_success=self._compiled,
            on_failure=self._failed,
        )

    def _compiled(self, result: Any) -> None:
        if self._closed:
            return
        self.compile_button.setEnabled(True)
        self._preview = result
        self._preview_completed = False
        self._set_inputs_enabled(True)
        self.status.setText(result["message_zh"])
        self.system_output.setPlainText(result["system_prompt"])
        self.task_output.setPlainText(result["task_prompt"])
        lines = ["所选章节", *result["section_labels"], "", "本地资料摘要"]
        for item in result["evidence"]:
            lines.extend(["", item["scope"], *item["supports"]])
        self.evidence_output.setPlainText("\n".join(lines))
        self._update_generate_enabled()

    def _update_generate_enabled(self, *_args: Any) -> None:
        self.review_button.setEnabled(
            bool(
                not self._generating
                and self._preview_completed
                and self._generated_result
                and self._preview
                and self._preview.get("preview_id")
            )
        )
        self.edit_button.setEnabled(self.review_button.isEnabled())
        self.generate_button.setEnabled(
            bool(
                not self._generating
                and not self._preview_completed
                and self._preview
                and self._preview.get("preview_id")
                and self.profile.currentData() is not None
            )
        )

    def _generate(self) -> None:
        profile = self.profile.currentData()
        if self._generating or not self._preview or profile is None:
            return
        answer = QMessageBox.question(
            self,
            "确认生成主题蓝图",
            f"模型：{profile.provider_name} / {profile.model_id}\n\n"
            "将发送当前已编译的系统提示、本次任务提示、本地资料的提炼摘要及输出格式要求。"
            + (
                f"其中包含所选 {self._preview['handout_reference']['question_count']} 道讲义的原题文字、公式标记及表格文字，"
                + (
                    "以及配对非官方解答。"
                    if self._preview["handout_reference"]["include_answers"]
                    else "不含配对解答。"
                )
                if self._preview.get("handout_reference")
                else ""
            )
            + "不发送来源文件、图片或学生资料。\n\n"
            "请在提示和资料依据标签页核对内容。此调用可能产生费用，结果仅为待完善的主题蓝图。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("已取消生成，没有调用模型。")
            return
        preview_id = self._preview["preview_id"]
        self._stop.clear()
        self._generating = True
        self._set_inputs_enabled(False)
        self.compile_button.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        self.status.setText(
            "正在生成主题命题蓝图，最长等待 5 分钟，可随时停止；完成后自动保存到本机…"
        )
        self.tasks.submit_progress(
            "生成主题命题蓝图",
            lambda progress, cancelled: self.facade.generate_prompt_blueprint(
                preview_id,
                profile.profile_id,
                profile.revision,
                teacher_confirmed=True,
                should_cancel=lambda: self._stop.is_set() or cancelled(),
            ),
            on_success=self._generated,
            on_failure=self._failed,
        )

    def _request_stop(self) -> None:
        self._stop.set()
        self.stop_button.setEnabled(False)
        self.status.setText("正在停止生成，请等待当前请求结束；可能已产生部分费用。")

    def _generated(self, result: Any) -> None:
        if self._closed:
            return
        from ..desktop_blueprint_generation import format_blueprint

        self._generating = False
        self._preview_completed = True
        self._generated_result = result
        self._show_inputs(False)
        self.stop_button.setVisible(False)
        self._set_inputs_enabled(True)
        self.compile_button.setEnabled(True)
        self.generated_output.setPlainText(format_blueprint(result))
        self.tabs.setCurrentWidget(self.generated_output)
        self.status.setText(
            f"蓝图已保存 · {result['model_id']} · {result['latency_ms'] / 1000:.1f} 秒。"
            "可从最近蓝图重新打开；完善题面和核验答案后再使用。"
        )
        self._update_generate_enabled()

        history_reader = getattr(self.facade, "prompt_blueprint_history", None)
        if callable(history_reader):
            self.tasks.submit(
                "读取已保存蓝图",
                history_reader,
                on_success=self._history_loaded,
                on_failure=self._failed,
            )

    def _edit(self) -> None:
        if not self.edit_button.isEnabled() or not self._preview:
            return
        from .blueprint_draft_dialog import BlueprintDraftDialog

        if self._edit_dialog is not None:
            self._edit_dialog.show()
            self._edit_dialog.raise_()
            return
        self._edit_dialog = BlueprintDraftDialog(
            self.facade,
            self._preview["preview_id"],
            self,
            tasks=self.tasks,
            profile=self.profile.currentData(),
        )
        self._edit_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._edit_dialog.destroyed.connect(lambda: setattr(self, "_edit_dialog", None))
        self._edit_dialog.show()

    def _review(self) -> None:
        if not self.review_button.isEnabled() or not self._generated_result:
            return
        from .blueprint_review_dialog import BlueprintReviewDialog

        if self._review_dialog is not None:
            self._review_dialog.show()
            self._review_dialog.raise_()
            return
        self._review_dialog = BlueprintReviewDialog(
            self.facade,
            self.tasks,
            self._preview["preview_id"],
            self._generated_result,
            self.profile.currentData(),
            self,
        )
        self._review_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._review_dialog.destroyed.connect(
            lambda: setattr(self, "_review_dialog", None)
        )
        self._review_dialog.show()

    def _failed(self, message: str) -> None:
        if self._closed:
            return
        self.compile_button.setEnabled(self.sections.count() > 0)
        self._generating = False
        self.stop_button.setVisible(False)
        self._set_inputs_enabled(True)
        self.status.setText(message)
        self._update_generate_enabled()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.title_input,
            self.grade,
            self.theme_reference,
            self.handout_reference,
            self.goal,
            self.sections,
            self.profile,
            self.history,
        ):
            widget.setEnabled(enabled)
        self.include_handout_answers.setEnabled(
            enabled and self.handout_reference.currentData() is not None
        )

    def _handout_changed(self, *_args: Any) -> None:
        self.include_handout_answers.setEnabled(
            self.handout_reference.currentData() is not None
        )
        self._invalidate_preview()

    def _invalidate_preview(self, *_args: Any) -> None:
        self._preview = None
        self._preview_completed = False
        self._generated_result = None
        self.generated_output.clear()
        self._update_generate_enabled()
        if not self.task_output.toPlainText():
            return
        for widget in (self.evidence_output, self.system_output, self.task_output):
            widget.clear()
        self.status.setText("章节、目标或参考选题已修改，请重新编译本地提示。")
