"""Read the exact native Word input, call the chosen API, then select new tags."""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .components import page_scroll, set_status


def _label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def _tags(value):
    lines = ["主考点：" + value["primary_knowledge"]["label"]]
    for row in [value["primary_knowledge"], *value["curriculum_candidates"]]:
        if row.get("section_key"):
            lines.append("教材：" + row["label"] + " · " + row["section_key"])
        lines.extend("依据：" + e["quote"] for e in row["evidence"])
    if not value["curriculum_candidates"]:
        lines.append("教材：待映射")
    return "\n".join(lines)


class WordSemanticTagsDialog(QDialog):
    def __init__(self, facade, tasks, selections, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.selections = deepcopy(selections)
        self.plan, self.analysis_result = None, None
        self._image_labels = []
        self._image_preview_failed = False
        self._job, self._phase = None, ""
        self.saved = []
        self.setWindowTitle("AI补全题目标签 · 先预览再采用")
        self.resize(900, 820)
        self.setMinimumSize(360, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("AI补全缺失标签"))
        layout.addWidget(
            _label(
                "只分析本次选题的题干、公共材料和可读取的原图。原考试出处、已有非空标签和教师修改保留。"
            )
        )
        self.profile = QComboBox()
        self.profile.setMinimumContentsLength(10)
        self.profile.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.profile.setAccessibleName("选择题目标签分析模型")
        layout.addWidget(self.profile)
        self.prepare = QPushButton("预览发送内容")
        self.prepare.setObjectName("QuietButton")
        self.prepare.clicked.connect(self._prepare)
        layout.addWidget(self.prepare)
        self.disclosure = _label(
            "本地预览不调用模型。带图题必须使用允许图片发送的多模态配置；不可读图片不会被悄悄省略。"
        )
        layout.addWidget(self.disclosure)
        self.allow_send = QCheckBox("已核对发送内容，同意调用所选模型（可能计费）")
        self.allow_send.toggled.connect(self._actions)
        layout.addWidget(self.allow_send)
        self.questions = QListWidget()
        self.questions.setAccessibleName("本次分析题目及待采用标签")
        self.questions.setWordWrap(True)
        self.questions.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.questions.setMaximumHeight(120)
        self.questions.currentRowChanged.connect(self._show_question)
        self.questions.itemChanged.connect(self._actions)
        layout.addWidget(self.questions)
        self.tabs = QTabWidget()
        self.paper = QWidget()
        self.paper_layout = QVBoxLayout(self.paper)
        self.paper_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tabs.addTab(page_scroll(self.paper), "原题与发送预览")
        self.tags = QPlainTextEdit()
        self.tags.setReadOnly(True)
        self.tags.setAccessibleName("当前标签与AI建议对照")
        self.tabs.addTab(self.tags, "标签建议与依据")
        layout.addWidget(self.tabs, 1)
        self.status = _label("正在读取可用模型设置…")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton("确认发送并分析")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self._run)
        self.stop = QPushButton("停止分析")
        self.stop.clicked.connect(
            lambda: self.tasks.cancel(self._job) if self._job else None
        )
        actions.addWidget(self.run_button)
        actions.addWidget(self.stop)
        layout.addLayout(actions)
        self.apply_button = QPushButton("保存勾选的标签建议")
        self.apply_button.clicked.connect(self._apply)
        layout.addWidget(self.apply_button)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.profile.currentIndexChanged.connect(self._invalidate)
        self.tasks.task_finished.connect(self._finished)
        self._submit("profiles", self.facade.preparation_profiles, self._profiles)

    def _submit(self, phase, operation, success, *, progress=False):
        self._phase = phase
        self._job = "starting"
        self._actions()
        try:
            if progress:
                self._job = self.tasks.submit_progress(
                    "AI题目标签分析",
                    operation,
                    on_success=success,
                    on_failure=self._failed,
                    on_progress=lambda p: set_status(
                        self.status, "info", p.get("message_zh", "正在分析…")
                    ),
                )
            else:
                self._job = self.tasks.submit(
                    "题目标签预览",
                    operation,
                    on_success=success,
                    on_failure=self._failed,
                )
        except (RuntimeError, TypeError):
            self._job = None
            self._failed("标签任务未能启动，请重试。")

    def _profiles(self, profiles):
        self.profile.blockSignals(True)
        self.profile.clear()
        for profile in profiles:
            self.profile.addItem(
                profile.provider_name + " / " + profile.model_id, profile
            )
        self.profile.blockSignals(False)
        set_status(
            self.status,
            "info" if profiles else "attention",
            "请选择模型并预览本次选题。"
            if profiles
            else "请先在模型设置保存Key，并允许结构化文字输出及题目文字发送。",
        )

    def _invalidate(self):
        if self._job:
            return
        self._discard()
        self.plan, self.analysis_result = None, None
        self.allow_send.setChecked(False)
        self.questions.clear()
        self.tags.clear()
        self._show_question(-1)
        self._actions()

    def _prepare(self):
        profile = self.profile.currentData()
        if not profile or self._job:
            return
        self.allow_send.setChecked(False)
        self._submit(
            "preview",
            lambda: self.facade.word_semantic_tag_preview(
                self.selections, profile.profile_id, profile.revision
            ),
            self._prepared,
        )

    def _prepared(self, value):
        self._discard()
        self.plan, self.analysis_result = value, None
        self._image_preview_failed = False
        ready = [u for u in value["units"] if u["status"] == "ready"]
        images = sum(len(u["images"]) for u in ready)
        self.disclosure.setText(
            f"接收模型：{value['model_label']}\n将发送{len(ready)}题的原生文字、公共材料及{images}张图片像素（包含图内可见内容和文件元数据）。"
            f"另有{len(value['units']) - len(ready)}题不发送，原因见题目列表。\n"
            f"最多调用{value['request_count']}次，每题一次；可能产生费用。失败即停止，重试可能再次计费。"
        )
        self._populate()
        set_status(
            self.status,
            "info",
            "逐题核对发送内容后，再确认调用模型。未调用API、未修改标签。",
        )

    def _populate(self):
        self.questions.blockSignals(True)
        current = self.questions.currentRow()
        self.questions.clear()
        candidates = {
            i["key"]: i for i in (self.analysis_result or {}).get("items", [])
        }
        for number, unit in enumerate(self.plan["units"], 1):
            candidate = candidates.get(unit["key"])
            state = unit["reason"] or (
                "待发送" if not self.analysis_result else "尚未分析"
            )
            if candidate:
                state = (
                    ("新增标签可采用" if candidate.get("changed") else "没有新增标签")
                    if candidate["status"] == "ready"
                    else candidate["note"]
                )
            item = QListWidgetItem(f"{number}. {unit['source_name']} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, unit["key"])
            if candidate and candidate["status"] == "ready" and candidate["changed"]:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            self.questions.addItem(item)
        self.questions.blockSignals(False)
        self.questions.setCurrentRow(max(0, min(current, self.questions.count() - 1)))
        self._show_question(self.questions.currentRow())

    def _show_question(self, index):
        self._image_labels = []
        while self.paper_layout.count():
            item = self.paper_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.plan or not 0 <= index < len(self.plan["units"]):
            self.tags.clear()
            return
        unit = self.plan["units"][index]
        self.paper_layout.addWidget(_label(unit["source_name"]))
        if unit["reason"]:
            self.paper_layout.addWidget(_label("本题不发送：" + unit["reason"]))
        for block in unit["input"]["blocks"]:
            self.paper_layout.addWidget(
                _label(
                    ("公共材料\n" if block["kind"] == "shared_context" else "")
                    + block["text"]
                )
            )
            for sha in block["image_sha256s"]:
                label = _label("原图")
                try:
                    raw = self.facade.word_semantic_tag_image(self.plan["plan_id"], sha)
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(raw):
                        raise ValueError("image invalid")
                    self._image_labels.append((label, pixmap))
                except (ValueError, RuntimeError, KeyError, TypeError, OSError):
                    label.setText("本次原图预览失败，请重新预览后再发送。")
                    self._image_preview_failed = True
                    self.allow_send.setChecked(False)
                self.paper_layout.addWidget(label)
        self._resize_images()
        self.tabs.widget(0).verticalScrollBar().setValue(0)
        text = "当前标签\n" + _tags(unit["attributes"])
        candidate = next(
            (
                i
                for i in (self.analysis_result or {}).get("items", [])
                if i["key"] == unit["key"]
            ),
            None,
        )
        if candidate:
            text += "\n\nAI建议合并后的标签（未作教师审核）\n" + (
                _tags(candidate["proposed"])
                if candidate["status"] == "ready"
                else "本题分析未完成"
            )
            if candidate["status"] == "ready":
                changed = [
                    label
                    for field, label in (
                        ("primary_knowledge", "主考点"),
                        ("curriculum_candidates", "教材映射"),
                    )
                    if candidate["proposed"][field] != unit["attributes"][field]
                ]
                text += "\n本次补全：" + (
                    "、".join(changed) if changed else "无新增字段"
                )
                text += "\n已有非空字段保持原值；题面选项引文不是知识结论。"
                text += "\n\n模型原始说明（可能包含未采用的建议）：\n"
            else:
                text += "\n"
            text += candidate["note"]
        self.tags.setPlainText(text)

    def _run(self):
        if (
            self._job
            or not self.plan
            or self.analysis_result
            or self._image_preview_failed
            or not self.allow_send.isChecked()
        ):
            return
        plan = deepcopy(self.plan)
        self._submit(
            "run",
            lambda progress, cancelled: self.facade.word_semantic_tag_run(
                plan["plan_id"],
                plan["revision"],
                confirmed=True,
                progress=progress,
                cancelled=cancelled,
            ),
            self._result_ready,
            progress=True,
        )

    def _result_ready(self, value):
        self.analysis_result = value
        self._populate()
        self.tabs.setCurrentIndex(1)
        set_status(
            self.status,
            "info",
            "分析已停止或完成。可逐题查看建议与依据，取消不合适的勾选，再保存；尚未写入标签。",
        )

    def _apply(self):
        keys = [
            self.questions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.questions.count())
            if self.questions.item(i).checkState() == Qt.CheckState.Checked
        ]
        if self._job or not keys or not self.plan:
            return
        self._submit(
            "apply",
            lambda: self.facade.word_semantic_tag_apply(self.plan["plan_id"], keys),
            self._saved,
        )

    def _saved(self, values):
        self.saved = values
        set_status(
            self.status,
            "success",
            f"已保存{len(values)}题的标签建议，原题和已有标签保持不变。",
        )

    def _failed(self, message):
        set_status(self.status, "error", message)
        self._actions()

    def _finished(self, task_id):
        if task_id != self._job:
            return
        phase, self._job = self._phase, None
        if phase == "run" and self.plan:
            result = self.facade.word_semantic_tag_result(self.plan["plan_id"])
            if result["finished"]:
                self._result_ready(result)
        self._actions()
        if phase == "apply" and self.saved:
            self.accept()

    def _actions(self, *_):
        busy = bool(self._job)
        self.profile.setEnabled(not busy and not self.analysis_result)
        self.prepare.setEnabled(
            not busy and bool(self.profile.currentData()) and not self.analysis_result
        )
        self.allow_send.setEnabled(
            not busy and bool(self.plan) and not self.analysis_result
        )
        self.run_button.setEnabled(
            not busy
            and not self._image_preview_failed
            and bool(self.plan and self.plan["request_count"])
            and self.allow_send.isChecked()
            and not self.analysis_result
        )
        self.stop.setEnabled(busy and self._phase == "run")
        checked = any(
            self.questions.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.questions.count())
        )
        self.apply_button.setEnabled(
            not busy and bool(self.analysis_result) and checked
        )
        self.close_button.setEnabled(not busy)

    def reject(self):
        if self._job:
            set_status(
                self.status, "attention", "任务执行中，请先停止分析并等待当前请求结束。"
            )
            return
        super().reject()

    def _discard(self):
        if self.plan:
            self.facade.word_semantic_tag_discard(self.plan["plan_id"])

    def done(self, result):
        if self._job:
            return
        if (
            result == QDialog.DialogCode.Rejected
            and not self.saved
            and any(
                i.get("changed") for i in (self.analysis_result or {}).get("items", [])
            )
            and QMessageBox.question(
                self,
                "保留本次标签建议？",
                "还有标签建议没有保存。关闭后将丢弃本次建议，重新分析可能再次计费。确定关闭吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._discard()
        self.plan = None
        super().done(result)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.plan:
            self._resize_images()

    def _resize_images(self):
        for label, pixmap in self._image_labels:
            scaled = pixmap.scaledToWidth(
                min(pixmap.width(), max(200, self.width() - 80)),
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setWordWrap(False)
            label.setPixmap(scaled)
            label.setFixedHeight(scaled.height() + 8)

    def closeEvent(self, event):
        if self._job:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
