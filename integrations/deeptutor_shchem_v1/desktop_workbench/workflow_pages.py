from __future__ import annotations

from copy import deepcopy
from math import isfinite

from PySide6.QtCore import QSignalBlocker, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
    DraftReceipt,
    PreparationAvailability,
)
from ..desktop_preparation_pedagogy import teacher_design_starter
from .assembly_page import PaperPage as _AssemblyPaperPage
from .assembly_page import PaperPreviewDialog
from .components import (
    CardFrame,
    CollapsibleSection,
    page_scroll,
    section_title,
    set_status,
)
from .preparation_design_widget import PreparationDesignEditor
from .preparation_egress_dialog import (
    PreparationEgressDialog,
    needs_scrollable_confirmation,
)
from .preparation_images_widget import PreparationImagesWidget
from .student_page import StudentPage
from .tasks import DesktopTaskBridge


def _stacked_field(label_text: str, editor: QWidget) -> QWidget:
    """Return a label-above-editor row that remains usable in compact panes."""

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(label_text)
    label.setObjectName("MutedLabel")
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    layout.addWidget(label)
    layout.addWidget(editor)
    return container


class PaperPage(QWidget):
    """Responsive shell around the rich native composer.

    The composer owns the paper-specific editing behavior.  Keeping it inside
    the same page scroll used by the other workflows gives the narrow native
    window a real vertical escape path instead of compressing its form fields
    to a one-pixel row.  The small forwarding method keeps the existing
    basket signal contract intact.
    """

    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self._composer = _AssemblyPaperPage(facade, tasks)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(self._composer))

    def update_basket_count(self, count: int | None = None) -> None:
        self._composer.update_basket_count(count)

    def __getattr__(self, name: str):
        # Preserve the practical inspection surface used by existing callers
        # (for example ``paper_page.preview_button``) without copying the
        # composer implementation into this workflow module.
        composer = self.__dict__.get("_composer")
        if composer is not None and hasattr(composer, name):
            return getattr(composer, name)
        raise AttributeError(name)


class PreparationPage(QWidget):
    basket_changed = Signal(int)

    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self._compact = False
        self._lesson_design = None
        self._save_task_id: str | None = None
        self._library_image_task_id: str | None = None
        self._word_import_epoch = 0
        self._word_import_in_flight = False
        self._context_task_id: str | None = None
        self._history_task_id: str | None = None
        self._generation_qt_task_id: str | None = None
        self._active_preparation_task_id: str | None = None
        self._preparation_task_id: str | None = None
        self._current_task_source_kind: str | None = None
        self._current_task_status = ""
        self._current_task_retryable = False
        self._current_returned_available = False
        self._availability: PreparationAvailability | None = None
        self._profiles: tuple[object, ...] = ()
        self._history_row_layouts: list[QBoxLayout] = []
        content = QWidget()
        root = QVBoxLayout(content)
        self.content_layout = root
        root.setContentsMargins(28, 24, 28, 32)
        root.setSpacing(18)
        root.addWidget(
            section_title(
                "备课与课件",
                '填写教学要求，选好资料，再生成教案和课件。生成后可继续修改。',
            )
        )
        output_card = CardFrame()
        output_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, output_card)
        output_layout.setContentsMargins(18, 14, 18, 14)
        output_layout.setSpacing(8)
        self.output_layout = output_layout
        output_label = QLabel("生成内容")
        output_label.setObjectName("MutedLabel")
        output_label.setWordWrap(True)
        output_layout.addWidget(output_label)
        self.output_kind = QComboBox()
        self.output_kind.setAccessibleName("备课输出类型")
        self.output_kind.addItem("PPT 与教案（推荐）", "joint")
        self.output_kind.addItem("仅 PPT", "ppt")
        self.output_kind.addItem("仅教案", "lesson_plan")
        output_layout.addWidget(self.output_kind, 1)
        root.addWidget(output_card)

        self.open_draft_button = QPushButton('打开已有备课…')
        self.open_draft_button.setObjectName("QuietButton")
        self.open_draft_button.setAccessibleName("预览并载入已保存备课草稿，不调用模型")
        self.open_draft_button.clicked.connect(self._open_draft)
        root.addWidget(self.open_draft_button)

        form_card = CardFrame()
        form = QVBoxLayout(form_card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("课题或教材章节")
        self.topic.setAccessibleName("课题或教材章节")
        self.audience = QLineEdit()
        self.audience.setPlaceholderText('例如：高二（3）班')
        self.audience.setAccessibleName("授课对象")
        self.route = QComboBox()
        self.route.setAccessibleName("课程类型")
        for value in ("新授", "复习", "实验", "讲评", "热点"):
            self.route.addItem(value)
        timing = QWidget()
        timing_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, timing)
        timing_layout.setContentsMargins(0, 0, 0, 0)
        timing_layout.setSpacing(6)
        self.timing_layout = timing_layout
        self.lesson_count = QSpinBox()
        self.lesson_count.setRange(1, 12)
        self.lesson_count.setValue(1)
        self.lesson_count.setAccessibleName("课时数")
        self.lesson_minutes = QSpinBox()
        self.lesson_minutes.setRange(1, 180)
        self.lesson_minutes.setValue(40)
        self.lesson_minutes.setAccessibleName("每课时分钟数")
        timing_layout.addWidget(_stacked_field("课时数", self.lesson_count), 1)
        timing_layout.addWidget(_stacked_field("每课时分钟数", self.lesson_minutes), 1)
        self.objective = QPlainTextEdit()
        self.objective.setPlaceholderText('这节课希望学生会解释什么、能完成什么任务？')
        self.objective.setMinimumHeight(76)
        self.objective.setMaximumHeight(120)
        self.objective.setAccessibleName('教学目标')
        self.materials = QPlainTextEdit()
        self.materials.setPlaceholderText(
            "教材页、完整大题、试卷、学生分析或已核验热点；图片发送方式在下方选择"
        )
        self.materials.setMinimumHeight(76)
        self.materials.setMaximumHeight(120)
        self.materials.setAccessibleName("本次资料与补充说明")
        form.addWidget(_stacked_field("1　课题/章节", self.topic))
        form.addWidget(_stacked_field("2　授课对象", self.audience))
        form.addWidget(_stacked_field("3　课程类型", self.route))
        form.addWidget(_stacked_field("4　课时与时长", timing))
        form.addWidget(_stacked_field('5\u3000教学目标', self.objective))
        form.addWidget(_stacked_field('6\u3000备课资料与说明', self.materials))
        self.blueprint_import_button = QPushButton('从命题方案添加参考…')
        self.blueprint_import_button.setObjectName("QuietButton")
        self.blueprint_import_button.setAccessibleName(
            '预览命题方案并追加到备课资料，不调用模型'
        )
        self.blueprint_import_button.clicked.connect(self._import_blueprint)
        form.addWidget(self.blueprint_import_button)
        self.source_import_button = QPushButton('添加Word内容与教材知识点…')
        self.source_import_button.setObjectName("QuietButton")
        self.source_import_button.setAccessibleName(
            "预览 Word 与教材知识点并追加备课参考，不调用模型"
        )
        self.source_import_button.clicked.connect(self._import_sources)
        # Keep a descriptive alias for callers that inspect the preparation
        # form as a small native view-model.
        self.preparation_sources_import_button = self.source_import_button
        form.addWidget(self.source_import_button)
        self.lecture_library_button = QPushButton('从已有教案选取内容…')
        self.lecture_library_button.setObjectName("QuietButton")
        self.lecture_library_button.clicked.connect(self._import_lecture)
        form.addWidget(self.lecture_library_button)
        self.word_questions_button = QPushButton('按本课知识点选题…')
        self.word_questions_button.setObjectName("QuietButton")
        self.word_questions_button.clicked.connect(self._import_word_questions)
        form.addWidget(self.word_questions_button)
        self.image_assets_widget = PreparationImagesWidget(self.facade, form_card)
        # Short alias retained for callers that treat the page as a form model.
        self.image_assets = self.image_assets_widget
        form.addWidget(self.image_assets_widget)
        root.addWidget(form_card)

        self.advanced = CollapsibleSection('更多教学要求')
        self.learning_detail = QLineEdit()
        self.learning_detail.setPlaceholderText("详细学情、分层与实验条件")
        self.learning_detail.setAccessibleName("学情与实验条件")
        design_card = CardFrame()
        design_layout = QVBoxLayout(design_card)
        design_layout.setContentsMargins(18, 16, 18, 16)
        self.template_detail = PreparationDesignEditor()
        self.template_detail.setPlaceholderText(
            "可填写必讲/略讲内容、课堂推进、讲练侧重、学生笔记和版式要求。"
            "留空时按所选课型组织；也可先填入建议结构再修改。"
        )
        self.template_detail.setAccessibleName("授课结构、笔记与呈现要求")
        self.template_detail.setMinimumHeight(160)
        self.template_detail.setMaximumHeight(260)
        design_layout.addWidget(
            _stacked_field("授课结构与学生笔记（可修改）", self.template_detail)
        )
        self.design_starter_button = QPushButton('填入教学结构建议')
        self.design_starter_button.setAccessibleName("填入可编辑的课题与课型建议，不调用模型")
        self.design_starter_button.clicked.connect(self._insert_design_starter)
        design_layout.addWidget(self.design_starter_button)
        design_hint = QLabel(
            "与本次备课草稿一起保存，可载入后继续修改。切换课型不会覆盖这里的自写内容；"
            "匹配到已阅读的平台课例时，会附教学组织参考和出处，可修改或删除；"
            "不自动导入原题或图片。"
        )
        design_hint.setWordWrap(True)
        design_hint.setObjectName("MutedLabel")
        design_layout.addWidget(design_hint)
        root.addWidget(design_card)
        self.strategy_detail = QLineEdit()
        self.strategy_detail.setPlaceholderText('作业安排与教学建议')
        self.strategy_detail.setAccessibleName('作业安排与教学建议')
        advanced_form = QVBoxLayout()
        advanced_form.setSpacing(10)
        advanced_form.addWidget(_stacked_field("学情/实验", self.learning_detail))
        advanced_form.addWidget(_stacked_field('作业安排', self.strategy_detail))
        self.advanced.content_layout.addLayout(advanced_form)
        root.addWidget(self.advanced)

        generation_card = CardFrame()
        generation_layout = QVBoxLayout(generation_card)
        generation_layout.setContentsMargins(18, 16, 18, 16)
        generation_layout.setSpacing(10)
        generation_title = QLabel("生成设置")
        generation_title.setObjectName("CardTitle")
        generation_layout.addWidget(generation_title)
        self.environment_status = QLabel("正在读取模型与 PPT 制作环境…")
        self.environment_status.setObjectName("MutedLabel")
        self.environment_status.setWordWrap(True)
        self.environment_status.setAccessibleName("备课生成环境状态")
        generation_layout.addWidget(self.environment_status)
        self.profile_single = QLabel("")
        self.profile_single.setObjectName("MutedLabel")
        self.profile_single.setWordWrap(True)
        self.profile_single.setAccessibleName("当前备课生成模型")
        self.profile_single.hide()
        generation_layout.addWidget(self.profile_single)
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("选择备课生成模型")
        self.profile_combo.hide()
        generation_layout.addWidget(_stacked_field("生成模型", self.profile_combo))

        actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        actions.setSpacing(8)
        self.actions = actions
        self.save_button = QPushButton("保存草稿")
        self.save_button.setObjectName("QuietButton")
        self.save_button.setAccessibleName("保存备课草稿，不调用模型")
        self.save_button.clicked.connect(self._save)
        self.generate_button = QPushButton('生成初稿')
        self.generate_button.setObjectName("PrimaryAction")
        self.generate_button.setAccessibleName('确认后调用模型生成备课初稿')
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self._generate)
        actions.addWidget(self.save_button)
        actions.addWidget(self.generate_button)
        actions.addStretch(1)
        generation_layout.addLayout(actions)
        self.status = QLabel(
            '填写后可先保存草稿。生成前会显示将发送的资料和模型设置。'
        )
        self.status.setObjectName("MutedLabel")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("备课操作状态")
        generation_layout.addWidget(self.status)
        root.addWidget(generation_card)

        self.progress_card = CardFrame()
        progress_layout = QVBoxLayout(self.progress_card)
        progress_layout.setContentsMargins(18, 16, 18, 16)
        progress_layout.setSpacing(10)
        progress_title = QLabel("任务进度")
        progress_title.setObjectName("CardTitle")
        progress_layout.addWidget(progress_title)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName('备课初稿生成进度')
        progress_layout.addWidget(self.progress)
        self.progress_message = QLabel("任务尚未开始。")
        self.progress_message.setObjectName("MutedLabel")
        self.progress_message.setWordWrap(True)
        self.progress_message.setAccessibleName('备课初稿生成阶段')
        progress_layout.addWidget(self.progress_message)
        self.stop_button = QPushButton("停止任务")
        self.stop_button.setObjectName("QuietButton")
        self.stop_button.setAccessibleName('停止当前备课初稿生成任务')
        self.stop_button.clicked.connect(self._stop_generation)
        progress_layout.addWidget(self.stop_button)
        self.task_action_button = QPushButton("继续生成")
        self.task_action_button.setObjectName("PrimaryAction")
        self.task_action_button.setAccessibleName('继续当前备课初稿生成任务')
        self.task_action_button.clicked.connect(self._activate_current_task)
        self.task_action_button.hide()
        progress_layout.addWidget(self.task_action_button)
        self.progress_card.hide()
        root.addWidget(self.progress_card)

        self.result_card = CardFrame()
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(18, 16, 18, 16)
        result_layout.setSpacing(10)
        result_title = QLabel("生成结果")
        result_title.setObjectName("CardTitle")
        result_layout.addWidget(result_title)
        self.result_summary = QLabel("")
        self.result_summary.setWordWrap(True)
        self.result_summary.setAccessibleName('备课初稿结果摘要')
        result_layout.addWidget(self.result_summary)
        self.result_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.result_actions.setSpacing(8)
        self.open_ppt_button = QPushButton("打开 PPT")
        self.open_ppt_button.setAccessibleName('打开生成的 PPT初稿')
        self.open_ppt_button.clicked.connect(lambda: self._open_artifact("pptx"))
        self.open_lesson_button = QPushButton("打开教案")
        self.open_lesson_button.setAccessibleName('打开生成的教案初稿')
        self.open_lesson_button.clicked.connect(
            lambda: self._open_artifact("lesson_plan_docx")
        )
        self.open_worksheet_button = QPushButton("打开学习单")
        self.open_worksheet_button.setAccessibleName("打开生成的学生学习单")
        self.open_worksheet_button.clicked.connect(
            lambda: self._open_artifact("student_worksheet_docx")
        )
        # A worksheet is optional; only a completed result advertising the
        # artifact should expose this control.
        self.open_worksheet_button.hide()
        self.open_preview_button = QPushButton("查看课件预览")
        self.open_preview_button.setAccessibleName("查看生成的课件预览图")
        self.open_preview_button.clicked.connect(
            lambda: self._open_artifact("preview_montage")
        )
        self._result_artifact_buttons = (
            self.open_ppt_button,
            self.open_lesson_button,
            self.open_worksheet_button,
            self.open_preview_button,
        )
        for button in self._result_artifact_buttons:
            self.result_actions.addWidget(button)
        self.result_actions.addStretch(1)
        result_layout.addLayout(self.result_actions)
        self.review_structure_button = QPushButton('查看教学安排与笔记')
        self.review_structure_button.setAccessibleName("检查课件课堂结构与学生笔记")
        self.review_structure_button.clicked.connect(self._open_classroom_review)
        self.review_structure_button.hide()
        result_layout.addWidget(self.review_structure_button)
        self.revise_content_button = QPushButton('修改课件与教案…')
        self.revise_content_button.setAccessibleName(
            "本地修订课件与教案并另存，不调用模型"
        )
        self.revise_content_button.clicked.connect(self._open_revision)
        self.revise_content_button.hide()
        result_layout.addWidget(self.revise_content_button)
        self.recover_returned_button = QPushButton('查看已返回内容并修复…')
        self.recover_returned_button.setAccessibleName(
            "查看失败备课的已返回内容并本地修复比较表，不调用模型"
        )
        self.recover_returned_button.clicked.connect(self._open_returned_recovery)
        self.recover_returned_button.hide()
        result_layout.addWidget(self.recover_returned_button)
        self.result_card.hide()
        root.addWidget(self.result_card)

        history_card = CardFrame()
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(18, 16, 18, 16)
        history_layout.setSpacing(10)
        history_title = QLabel("最近备课")
        history_title.setObjectName("CardTitle")
        history_layout.addWidget(history_title)
        self.history_items = QVBoxLayout()
        self.history_items.setSpacing(8)
        history_layout.addLayout(self.history_items)
        self.history_empty = QLabel("正在读取最近任务…")
        self.history_empty.setObjectName("MutedLabel")
        self.history_empty.setWordWrap(True)
        self.history_empty.setAccessibleName("最近备课任务状态")
        self.history_items.addWidget(self.history_empty)
        root.addWidget(history_card)
        root.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.editor_scroll = page_scroll(content)
        outer.addWidget(self.editor_scroll, 1)
        self.tasks.task_cancelled.connect(self._qt_task_cancelled)
        self.tasks.task_finished.connect(self._qt_task_finished)
        self._availability_timer = QTimer(self)
        self._availability_timer.setSingleShot(True)
        self._availability_timer.timeout.connect(self._load_context)
        self._availability_timer.start(120)
        self._form_baseline = self._payload()
        self._saving_payload: dict | None = None
        self.image_assets_widget.mode_changed.connect(self._image_input_mode_changed)

    def _image_input_mode_changed(self, _mode: str) -> None:
        self.setWindowModified(self._payload() != self._form_baseline)

    def _open_draft(self) -> None:
        from ..desktop_preparation import normalize_preparation_payload
        from .preparation_draft_dialog import PreparationDraftDialog

        if (
            self._save_task_id
            or self._generation_qt_task_id
            or self._active_preparation_task_id
        ):
            set_status(
                self.status, "attention", "请先等待保存或生成结束，再打开其他草稿。"
            )
            return
        dialog = PreparationDraftDialog(self.facade, self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.selected is None:
            return
        if self._payload() != self._form_baseline:
            decision = QMessageBox.question(
                self,
                "确认载入另一份草稿",
                "当前表单有尚未保存的修改。载入所选草稿会替换当前表单，已保存的草稿不会改变。是否放弃这些未保存修改并载入？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
        payload = dialog.selected["payload"]
        normalized = normalize_preparation_payload(payload)
        from copy import deepcopy
        self._lesson_design = deepcopy(payload.get("lesson_design"))
        self.output_kind.setCurrentIndex(
            self.output_kind.findData(payload["output_kind"])
        )
        self.topic.setText(payload["topic"])
        self.audience.setText(payload["audience"])
        if self.route.findText(payload["lesson_route"]) < 0:
            self.route.addItem(payload["lesson_route"])
        self.route.setCurrentText(payload["lesson_route"])
        self.lesson_count.setValue(normalized["timing"]["periods"])
        self.lesson_minutes.setValue(normalized["timing"]["minutes_per_period"])
        self.objective.setPlainText(payload["objective"])
        self.materials.setPlainText(payload["materials"])
        self.image_assets_widget.set_assets(payload.get("image_assets", ()))
        self.image_assets_widget.set_image_input_mode(payload.get("image_input_mode"))
        for key, widget in (
            ("learning_and_experiment", self.learning_detail),
            ("template_and_delivery", self.template_detail),
            ("homework_and_strategy", self.strategy_detail),
        ):
            widget.setText(payload["advanced"].get(key, ""))
        self._form_baseline = self._payload()
        self.setWindowModified(False)
        self._preparation_task_id = None
        self._current_task_status = ""
        self._current_task_retryable = False
        self.progress_card.hide()
        self._hide_result_artifacts()
        self.result_card.hide()
        set_status(
            self.status,
            "success",
            "备课草稿已载入，可继续修改并另存；生成仍需确认。原草稿未改写，尚未调用模型。",
        )
        self.topic.setFocus()

    def _import_blueprint(self) -> None:
        from ..desktop_blueprint_drafts import BlueprintDraftError
        from ..desktop_blueprint_preparation import append_reference
        from .blueprint_preparation_dialog import BlueprintPreparationDialog

        dialog = BlueprintPreparationDialog(self.facade, self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.reference is None:
            return
        try:
            combined = append_reference(
                self.materials.toPlainText(), dialog.reference["materials"]
            )
        except BlueprintDraftError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        self.materials.setPlainText(combined)
        set_status(
            self.status,
            "success",
            '命题方案参考已追加，其他填写内容未变；请核对课题、对象和目标后保存或生成。尚未调用模型。',
        )

    def _import_lecture(self) -> None:
        from .lecture_library_dialog import LectureLibraryDialog

        dialog = LectureLibraryDialog(self.facade, self.tasks, self, lesson_topic=self.topic.text())
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.reference is not None:
            self.import_word_reference(dialog.reference)

    def _import_word_questions(self) -> None:
        from .word_question_dialog import WordQuestionDialog

        dialog = WordQuestionDialog(self.facade, self.tasks, self, lesson_topic=self.topic.text())
        dialog.basket_changed.connect(self.basket_changed)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.preparation_reference is not None:
            self.import_word_reference(dialog.preparation_reference)

    def _import_sources(self) -> None:
        from ..desktop_blueprint_drafts import BlueprintDraftError
        from ..desktop_blueprint_preparation import append_reference
        from .preparation_sources_dialog import PreparationSourcesDialog

        dialog = PreparationSourcesDialog(self.facade, self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.reference is None:
            return
        try:
            combined = append_reference(
                self.materials.toPlainText(), dialog.reference["materials"]
            )
        except BlueprintDraftError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        self.materials.setPlainText(combined)
        warnings = dialog.reference.get("warnings", ())
        warning_text = ""
        if warnings:
            warning_text = (
                f" 包含 {len(warnings)} 条原文缺口/待核对警告，详情已保留在导入资料中。"
            )
        set_status(
            self.status,
            "success",
            "Word 与教材知识点参考已追加，其他填写内容未变；请核对课题、对象和目标后保存或生成。尚未调用模型。"
            + warning_text,
        )

    def import_word_reference(self, reference: dict) -> bool:
        """Append confirmed Word or personal-image references to the materials."""
        from ..desktop_blueprint_drafts import BlueprintDraftError
        from ..desktop_blueprint_preparation import append_reference

        source_label = "图片题" if isinstance(reference, dict) and reference.get("source_kind") == "personal_visual" else "Word"
        if (
            self._save_task_id
            or self._library_image_task_id
            or self._generation_qt_task_id
            or self._active_preparation_task_id
        ):
            QMessageBox.information(
                self,
                f"暂不能追加 {source_label} 资料",
                f"请先等待当前备课保存、生成或图片导入结束，再重新选择 {source_label} 内容。",
            )
            return False
        if not isinstance(reference, dict):
            return False
        materials = reference.get("materials")
        warnings = reference.get("warnings", ())
        if (
            not isinstance(materials, str)
            or not materials.strip()
            or not isinstance(warnings, (list, tuple))
            or any(not isinstance(warning, str) for warning in warnings)
        ):
            set_status(self.status, "error", f"{source_label} 参考内容不完整，请重新预览后导入。")
            return False
        missing_warnings = [
            warning for warning in warnings if warning and warning not in materials
        ]
        if missing_warnings:
            materials += "\n\n原文缺口 / 待核对提醒：\n" + "\n".join(missing_warnings)
        try:
            combined = append_reference(self.materials.toPlainText(), materials)
        except BlueprintDraftError as exc:
            QMessageBox.information(self, f"未追加 {source_label} 资料", exc.message_zh)
            return False
        if reference.get("source_kind") == "personal_visual" or any(
            key in reference
            for key in ("selections", "source_selection", "include_images", "image_assets", "image_issues")
        ):
            return self._import_word_images(reference, combined)
        self.materials.setPlainText(combined)
        warning_note = (
            f" 包含 {len(warnings)} 条原文缺口/待核对提醒，请逐项核对。"
            if warnings
            else ""
        )
        set_status(
            self.status,
            "success",
            f"所选 {source_label} 内容已追加到备课资料。请核对课题、对象和目标后保存或生成；尚未调用模型。"
            + warning_note,
        )
        self.materials.setFocus()
        return True

    def _import_word_images(self, reference: dict, combined: str) -> bool:
        """Commit a confirmed text/image batch only after all local reads succeed."""
        from shiboken6 import isValid

        from ..desktop_preparation_images import (
            PreparationImageError,
            normalize_image_assets,
        )

        personal_visual = reference.get("source_kind") == "personal_visual"
        source_label = "图片题" if personal_visual else "Word"
        image_label = "图片" if personal_visual else "原图"
        try:
            source_selection = reference.get("source_selection")
            if personal_visual and (
                "source_selection" in reference or reference.get("include_images") is not True
            ):
                raise PreparationImageError("图片题须携带对应题面图片，不能当作 Word 原文区块导入。")
            if "source_selection" in reference:
                if reference.get("reference_issues"):
                    raise PreparationImageError("原教案有待处理事项，请返回原文预览核对。")
                if (
                    "selections" in reference
                    or not isinstance(source_selection, dict)
                    or any(not isinstance(source_selection.get(key), str) or not source_selection[key]
                           for key in ("batch_id", "source_id", "source_sha256", "revision"))
                    or any(type(source_selection.get(key)) is not int for key in ("block_start", "block_end"))
                    or not 1 <= source_selection["block_start"] <= source_selection["block_end"]
                ):
                    raise PreparationImageError("原教案区块定位不完整，请重新预览。")
                import_method = getattr(self.facade, "import_word_source_reference", None)
            else:
                if (
                    not isinstance(reference.get("selections"), list)
                    or not reference["selections"]
                    or any(
                        not isinstance(item, dict)
                        or not isinstance(item.get("key"), str) or not item["key"]
                        or not isinstance(item.get("revision"), str) or not item["revision"]
                        or type(item.get("points")) not in (int, float)
                        or not 0 < item["points"] <= 100 or not isfinite(item["points"])
                        for item in reference["selections"]
                    )
                ):
                    raise PreparationImageError(f"{source_label} 选题定位不完整，请重新预览。")
                if personal_visual and any(
                    not isinstance(item.get("batch_id"), str) or not item["batch_id"]
                    for item in reference["selections"]
                ):
                    raise PreparationImageError("图片题导入批次不完整，请重新预览。")
                import_method = getattr(
                    self.facade,
                    "import_personal_visual_question_reference" if personal_visual else "import_word_question_reference",
                    None,
                )
            if (
                type(reference.get("include_images")) is not bool
                or "warnings" not in reference
                or not isinstance(reference.get("image_issues"), list)
                or any(not isinstance(item, str) for item in reference["image_issues"])
                or "image_assets" not in reference
            ):
                raise PreparationImageError(f"{source_label} 图文参考不完整，请重新预览。")
            incoming = normalize_image_assets(reference["image_assets"])
            if incoming != reference["image_assets"]:
                raise PreparationImageError(f"{source_label} 图片说明已变化，请重新预览。")
            if personal_visual and not incoming:
                raise PreparationImageError("图片题缺少可核对的题面图片，请重新预览。")
            if not reference["include_images"] and incoming:
                raise PreparationImageError("仅文字参考不应携带图片，请重新预览。")
            if reference["include_images"] and reference["image_issues"]:
                raise PreparationImageError(
                    "所选原图存在待处理项，未追加任何内容。请调整选题，或明确选择仅文字后重新预览。"
                )
            existing = normalize_image_assets(self.image_assets_widget.assets())
            seen = {asset["sha256"] for asset in existing}
            merged = deepcopy(existing)
            for asset in incoming:
                if asset["sha256"] not in seen:
                    merged.append(asset)
                    seen.add(asset["sha256"])
            if len(merged) > self.image_assets_widget.MAX_ASSETS:
                raise PreparationImageError(
                    f"已有 {len(existing)} 张，本次待新增 {len(merged) - len(existing)} 张，"
                    f"超过 {self.image_assets_widget.MAX_ASSETS} 张上限。未追加文字或图片；请调整选题或已有图片后重试。"
                )
            merged = normalize_image_assets(merged)
            if not callable(import_method):
                raise PreparationImageError(
                    "当前应用尚不能接收这份图文参考，请更新后重试。"
                )
        except PreparationImageError as exc:
            set_status(self.status, "error", exc.message_zh)
            return False

        frozen_reference = deepcopy(reference)
        before = deepcopy(self._payload())
        expected = {
            "materials": reference["materials"],
            "warnings": list(reference["warnings"]),
            "image_assets": merged,
        }
        self._word_import_epoch += 1
        epoch = self._word_import_epoch
        self._library_image_task_id = "pending-word"
        self._word_import_in_flight = True
        self.setEnabled(False)
        set_status(
            self.status,
            "info",
            f"正在核对所选 {source_label} 内容并保存{image_label}；全部成功后一起追加，不调用模型…",
        )

        def active():
            return isValid(self) and epoch == self._word_import_epoch

        def failed(_message=None):
            if not active():
                return
            self._library_image_task_id = None
            self._word_import_in_flight = False
            self.setEnabled(True)
            set_status(
                self.status,
                "error",
                f"{source_label} 图文未能完整导入，原有备课内容与图片保留。来源可能已变化或图片读取失败，请重新预览后重试。",
            )

        def ready(value):
            if not active():
                return
            try:
                if (
                    not isinstance(value, dict)
                    or any(value.get(key) != item for key, item in expected.items())
                    or self._payload() != before
                ):
                    failed()
                    return
                checked = normalize_image_assets(value["image_assets"])
                # Suppress intermediate change notifications until both fields agree.
                image_blocker = QSignalBlocker(self.image_assets_widget)
                text_blocker = QSignalBlocker(self.materials)
                self.image_assets_widget.set_assets_strict(checked)
                self.materials.setPlainText(combined)
                del image_blocker, text_blocker
                self.image_assets_widget.assets_changed.emit(
                    self.image_assets_widget.assets()
                )
            except (PreparationImageError, RuntimeError, TypeError, ValueError):
                failed()
                return
            self._library_image_task_id = None
            self._word_import_in_flight = False
            self.setEnabled(True)
            set_status(
                self.status,
                "success",
                f"{source_label} 文字与 {len(merged) - len(existing)} 张新增{image_label}已一起追加；原有表单和图片保留。"
                f"{image_label}已保存到本机；是否交给 AI 读图以“图片用法”为准，生成前会确认发送清单。尚未调用模型。"
                + (
                    f" 包含 {len(expected['warnings'])} 条待核对提醒。"
                    if expected["warnings"]
                    else ""
                ),
            )
            self.materials.setFocus()

        try:
            self._library_image_task_id = self.tasks.submit(
                f"导入 {source_label} 选题图文",
                lambda: import_method(
                    frozen_reference, existing
                ),
                on_success=ready,
                on_failure=failed,
            )
        except RuntimeError:
            failed()
            return False
        return True

    def closeEvent(self, event) -> None:
        self._word_import_epoch += 1
        if self._word_import_in_flight:
            cancel = getattr(self.tasks, "cancel", None)
            if callable(cancel) and self._library_image_task_id:
                cancel(self._library_image_task_id)
            self._library_image_task_id = None
            self._word_import_in_flight = False
            self.setEnabled(True)
            set_status(
                self.status, "info", "已取消本次 Word 图文追加，原有表单与图片保留。"
            )
        super().closeEvent(event)

    def import_paper_reference(self, snapshot: dict) -> bool:
        from .paper_preparation_dialog import PaperPreparationDialog

        return self._import_text_reference(
            lambda: PaperPreparationDialog(snapshot, self), "当前组卷参考"
        )

    def import_library_reference(self, detail) -> bool:
        from .library_preparation_dialog import LibraryPreparationDialog

        return self._import_text_reference(
            lambda: LibraryPreparationDialog(detail, self), "题库选题参考"
        )

    def _import_text_reference(self, make_dialog, label: str) -> bool:
        from ..desktop_blueprint_drafts import BlueprintDraftError
        from ..desktop_blueprint_preparation import append_reference

        if (
            self._save_task_id
            or self._library_image_task_id
            or self._generation_qt_task_id
            or self._active_preparation_task_id
        ):
            QMessageBox.information(
                self,
                "暂不能追加备课资料",
                "请先等待当前备课保存或生成结束，已有表单未改变。",
            )
            return False
        dialog = make_dialog()
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.reference is None:
            return False
        try:
            combined = append_reference(
                self.materials.toPlainText(), dialog.reference["materials"]
            )
        except BlueprintDraftError as exc:
            QMessageBox.information(self, "未追加" + label, exc.message_zh)
            return False
        self.materials.setPlainText(combined)
        set_status(
            self.status,
            "success",
            label
            + "已追加；课题、教材资料与目标保留。请核对章节名及授课安排后保存或生成。尚未调用模型。",
        )
        self.materials.setFocus()
        return True

    def import_library_image(self, selection: dict) -> bool:
        """Copy a confirmed library image without replacing the current lesson."""
        from ..desktop_library import LibraryImage

        if (
            self._save_task_id
            or self._generation_qt_task_id
            or self._active_preparation_task_id
            or self._library_image_task_id
        ):
            QMessageBox.information(
                self,
                "暂不能添加题库图片",
                "请先等待当前备课保存、生成或图片导入结束；已有内容未改变。",
            )
            return False
        image = selection.get("image")
        if not isinstance(image, LibraryImage):
            return False
        assets = self.image_assets_widget.assets()
        if any(asset["sha256"] == image.sha256 for asset in assets):
            QMessageBox.information(
                self, "图片已经添加", "这张原图已在当前备课中，无需重复添加。"
            )
            return False
        if len(assets) >= self.image_assets_widget.MAX_ASSETS:
            QMessageBox.information(
                self,
                "图片数量已满",
                f"每次备课最多{self.image_assets_widget.MAX_ASSETS}张图片，请先在备课页移除一张。",
            )
            return False
        self.setEnabled(False)
        self._library_image_task_id = "pending"
        set_status(
            self.status, "info", "正在将所选原图复制到本地备课素材库，不调用模型…"
        )
        try:
            self._library_image_task_id = self.tasks.submit(
                "导入题库备课图片",
                lambda: self.facade.import_library_preparation_image(
                    image,
                    selection["caption"],
                    selection["source"],
                    selection["purpose"],
                ),
                on_success=self._library_image_added,
                on_failure=lambda _message: self._library_image_failed(),
            )
        except RuntimeError:
            self._library_image_failed()
            return False
        return True

    def _library_image_added(self, asset: object) -> None:
        self._library_image_task_id = None
        self.setEnabled(True)
        if self.image_assets_widget.append_asset(asset):
            set_status(
                self.status,
                "success",
                "题库原图已加入本次备课，课题、资料与原有图片保留。请保存草稿或确认生成；尚未调用模型。",
            )
            self.image_assets_widget.asset_list.setFocus()
        else:
            set_status(
                self.status,
                "attention",
                "原有备课内容保留，请查看教学图片区域的导入提示。",
            )

    def _library_image_failed(self) -> None:
        self._library_image_task_id = None
        self.setEnabled(True)
        set_status(
            self.status,
            "error",
            "题库原图未能导入，原有备课内容保留。请重新打开题目核对来源图片后重试。",
        )

    def _insert_design_starter(self) -> None:
        if self.template_detail.text().strip():
            decision = QMessageBox.question(
                self,
                "替换授课结构？",
                "填入建议会替换这里尚未保存的授课结构，其他表单内容与已保存草稿不变。是否替换？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
        self.template_detail.setText(
            teacher_design_starter(self.route.currentText(), self.topic.text())
        )
        self.template_detail.setFocus()

    def _payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "output_kind": str(self.output_kind.currentData()),
            "topic": self.topic.text(),
            "audience": self.audience.text(),
            "lesson_route": self.route.currentText(),
            "lesson_timing": f"{self.lesson_count.value()}课时×{self.lesson_minutes.value()}分钟",
            "objective": self.objective.toPlainText(),
            "materials": self.materials.toPlainText(),
            "advanced": {
                "learning_and_experiment": self.learning_detail.text(),
                "template_and_delivery": self.template_detail.text(),
                "homework_and_strategy": self.strategy_detail.text(),
            },
        }
        if self._lesson_design is not None:
            from copy import deepcopy
            payload["lesson_design"] = deepcopy(self._lesson_design)
        image_assets = self.image_assets_widget.assets()
        if image_assets:
            payload["image_assets"] = image_assets
        if self.image_assets_widget.image_input_mode() == "vision":
            payload["image_input_mode"] = "vision"
        return payload

    def _save(self) -> None:
        if self._save_task_id:
            return
        self.save_button.setEnabled(False)
        # Snapshot Qt fields on the GUI thread; workers never read live widgets.
        payload = self._payload()
        self._saving_payload = payload
        self.image_assets_widget.set_editing_enabled(False)
        set_status(self.status, "info", "正在保存备课草稿…")
        self._save_task_id = self.tasks.submit(
            "保存备课草稿",
            lambda: self.facade.create_preparation_draft(payload),
            on_success=self._saved,
            on_failure=self._save_failed,
        )

    def _saved(self, receipt: DraftReceipt) -> None:
        self._save_task_id = None
        if self._saving_payload is not None:
            self._form_baseline = self._saving_payload
        self.setWindowModified(self._payload() != self._form_baseline)
        self._saving_payload = None
        self.save_button.setEnabled(True)
        self.image_assets_widget.set_editing_enabled(True)
        set_status(self.status, "success", "备课草稿已保存；尚未调用模型。")

    def _save_failed(self, message: str) -> None:
        self._save_task_id = None
        self._saving_payload = None
        self.save_button.setEnabled(True)
        self.image_assets_widget.set_editing_enabled(True)
        set_status(self.status, "error", message)

    def _load_context(self) -> None:
        if self._context_task_id:
            return
        self._context_task_id = self.tasks.submit(
            "读取备课生成设置",
            lambda: (
                self.facade.preparation_availability(),
                self.facade.preparation_profiles(),
                self.facade.list_preparations(limit=3),
            ),
            on_success=self._context_ready,
            on_failure=self._context_failed,
        )

    def _context_ready(self, value: object) -> None:
        self._context_task_id = None
        availability, profiles, summaries = value  # type: ignore[misc]
        self._availability = availability
        self._profiles = tuple(profiles)
        set_status(
            self.environment_status,
            "success"
            if availability.provider_ready and availability.renderer_ready
            else "attention",
            availability.message_zh,
        )
        self._populate_profiles()
        self._populate_history(tuple(summaries))
        self._update_generate_enabled()

    def _context_failed(self, message: str) -> None:
        self._context_task_id = None
        self._availability = None
        self._profiles = ()
        self.profile_combo.parentWidget().hide()
        self.profile_single.setText("模型列表暂时无法读取；草稿仍可离线保存。")
        self.profile_single.show()
        set_status(self.environment_status, "error", message)
        self._update_generate_enabled()
        self._populate_history(())

    def _populate_profiles(self) -> None:
        self.profile_combo.clear()
        for profile in self._profiles:
            provider_name = str(getattr(profile, "provider_name", "模型服务"))
            model_id = str(getattr(profile, "model_id", "模型"))
            self.profile_combo.addItem(f"{provider_name} / {model_id}", profile)
        if len(self._profiles) == 1:
            profile = self._profiles[0]
            self.profile_single.setText(
                "生成模型："
                f"{getattr(profile, 'provider_name', '模型服务')} / "
                f"{getattr(profile, 'model_id', '模型')}"
            )
            self.profile_single.show()
            self.profile_combo.parentWidget().hide()
        elif len(self._profiles) > 1:
            self.profile_single.hide()
            self.profile_combo.parentWidget().show()
            self.profile_combo.show()
        else:
            self.profile_single.setText(
                "尚无支持结构化输出的可用模型；草稿仍可离线保存。"
            )
            self.profile_single.show()
            self.profile_combo.parentWidget().hide()

    def _selected_profile(self) -> object | None:
        if not self._profiles:
            return None
        if len(self._profiles) == 1:
            return self._profiles[0]
        return self.profile_combo.currentData()

    def _update_generate_enabled(self) -> None:
        availability = self._availability
        ready = bool(
            availability
            and availability.provider_ready
            and availability.renderer_ready
            and self._profiles
        )
        self.generate_button.setEnabled(
            ready
            and self._generation_qt_task_id is None
            and self._active_preparation_task_id is None
        )

    def _generate(self) -> None:
        if self._generation_qt_task_id or self._active_preparation_task_id:
            return
        profile = self._selected_profile()
        if profile is None:
            set_status(
                self.status,
                "attention",
                "尚无可用生成模型；请先到设置中保存支持结构化输出的模型配置。",
            )
            return
        payload = self._payload()
        required = (
            self.topic.text(),
            self.audience.text(),
            self.route.currentText(),
            str(payload["lesson_timing"]),
            self.objective.toPlainText(),
            self.materials.toPlainText(),
        )
        if any(not value.strip() for value in required):
            set_status(self.status, "attention", "请先填写六个常用备课字段。")
            self.topic.setFocus()
            return
        profile_id = str(getattr(profile, "profile_id", ""))
        revision = str(getattr(profile, "revision", ""))
        try:
            preview = self.facade.preparation_egress_preview(payload, profile_id, revision)
            confirmation_text = self._egress_confirmation_text(preview)
        except DesktopFacadeError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            set_status(self.status, "error", "备课发送预检未能完成；尚未准备任务或调用模型，请检查输入与设置后重试。")
            return
        if not self._confirm_egress("确认调用模型", confirmation_text, preview):
            set_status(self.status, "info", "已取消生成；没有调用模型。")
            return
        try:
            prepared = self.facade.prepare_preparation(
                payload,
                profile_id,
                revision,
            )
        except DesktopFacadeError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        except (OSError, RuntimeError, TypeError, ValueError):
            set_status(
                self.status, "error", "备课任务未能准备，请检查输入与设置后重试。"
            )
            return
        self._continue_confirmed_preparation(prepared)

    @staticmethod
    def _egress_confirmation_text(preview: object) -> str:
        if not isinstance(preview, dict):
            raise TypeError("发送预检结果不完整。")
        text = preview.get("confirmation_text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("发送预检缺少确认说明。")
        return text

    def _confirm_egress(self, title: str, text: str, preview: dict) -> bool:
        count = preview.get("image_count", 0)
        if needs_scrollable_confirmation(text, count):
            dialog = PreparationEgressDialog(
                title,
                text,
                image_count=count if type(count) is int and count >= 0 else 0,
                image_assets=preview.get("image_assets", []),
                image_loader=getattr(self.facade, "preparation_image_bytes", None),
                local_only=preview.get("local_only_operation") is True,
                parent=self,
            )
            return dialog.exec() == QDialog.DialogCode.Accepted
        return QMessageBox.question(
            self,
            title,
            text + "\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _continue_confirmed_preparation(self, summary: object) -> None:
        """Continue a prepared identity without asking for the same consent twice."""

        status = str(self._field(summary, "status", ""))
        if status == "prepared":
            self._start_generation_task(summary)
            return
        if status == "completed":
            self._render_summary(summary)
            self._refresh_history()
            return

        task_id = str(self._field(summary, "task_id", ""))
        retryable = bool(self._field(summary, "retryable", status == "cancelled"))
        if status in {"failed", "cancelled"} and retryable and task_id:
            try:
                retried = self.facade.retry_preparation(task_id)
            except DesktopFacadeError as exc:
                self._render_summary(summary)
                set_status(self.status, "error", exc.message_zh)
                self._refresh_history()
                return
            except (OSError, RuntimeError, TypeError, ValueError):
                self._render_summary(summary)
                set_status(
                    self.status,
                    "error",
                    "已有同内容任务，但暂时无法重试；本次没有再次调用模型。",
                )
                self._refresh_history()
                return
            retried_status = str(self._field(retried, "status", ""))
            if retried_status == "prepared":
                self._start_generation_task(retried)
                return
            self._render_summary(retried)
            self._update_generate_enabled()
            if retried_status != "completed":
                set_status(
                    self.status,
                    "attention",
                    "重试后的任务尚未进入可生成状态；本次没有调用模型。",
                )
            self._refresh_history()
            return

        self._render_summary(summary)
        self._update_generate_enabled()
        if status in {"failed", "cancelled"}:
            message = str(self._field(summary, "message_zh", "任务未能继续。"))
            set_status(
                self.status,
                "error" if status == "failed" else "attention",
                f"{message} 该任务当前不能直接重试；本次没有调用模型。",
            )
        else:
            set_status(
                self.status,
                "attention",
                "已找到同内容任务，但其当前状态不能启动生成；"
                "本次没有调用模型，请从最近备课查看。",
            )
        self._refresh_history()

    def _start_generation_task(self, summary: object) -> None:
        task_id = str(self._field(summary, "task_id", ""))
        if not task_id:
            set_status(self.status, "error", "备课任务未能准备，请稍后重试。")
            return
        self._preparation_task_id = task_id
        self._active_preparation_task_id = task_id
        self._current_task_status = str(self._field(summary, "status", "prepared"))
        self._current_task_retryable = False
        self.progress_card.show()
        self._hide_result_artifacts()
        self.result_card.hide()
        self.progress.setValue(0)
        self.progress_message.setText('正在开始生成…')
        self.stop_button.setEnabled(True)
        self.stop_button.show()
        self.task_action_button.hide()
        self.save_button.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.image_assets_widget.set_editing_enabled(False)
        set_status(self.status, "info", '正在生成备课初稿，请保持工作台打开。')
        self._generation_qt_task_id = self.tasks.submit_progress(
            '生成初稿',
            lambda progress, cancelled: self.facade.generate_preparation(
                task_id,
                teacher_confirmed=True,
                progress_callback=progress,
                should_cancel=cancelled,
            ),
            on_progress=self._generation_progress,
            on_success=self._generation_succeeded,
            on_failure=self._generation_failed,
        )

    @staticmethod
    def _field(value: object, name: str, default: object = None) -> object:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def _generation_progress(self, value: object) -> None:
        raw_percent = self._field(
            value,
            "percent",
            self._field(value, "progress_percent", self.progress.value()),
        )
        try:
            percent = max(0, min(100, int(raw_percent)))
        except (TypeError, ValueError):
            percent = self.progress.value()
        self.progress.setValue(percent)
        message = str(self._field(value, "message_zh", '正在生成备课初稿…'))
        self.progress_message.setText(message)

    def _activate_current_task(self) -> None:
        task_id = self._preparation_task_id
        if not task_id:
            set_status(self.status, "error", "当前没有可继续的备课任务。")
            return
        is_retry = self._current_task_status in {"failed", "cancelled"}
        if is_retry and not self._current_task_retryable:
            set_status(self.status, "error", "这个备课失败不能直接重试。")
            return
        if not is_retry and self._current_task_status != "prepared":
            set_status(self.status, "attention", "这个备课任务当前不能继续生成。")
            return
        try:
            preview = self.facade.preparation_task_egress_preview(task_id)
            message = self._egress_confirmation_text(preview)
            local_only = preview.get("local_only_operation")
            if type(local_only) is not bool:
                raise ValueError("历史任务发送预检缺少本地操作标记。")
        except DesktopFacadeError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            set_status(self.status, "error", "历史任务发送预检未能完成；本次没有继续生成，请刷新后重试。")
            return
        if local_only:
            title = "确认本地重新导出"
        elif is_retry:
            title = '确认重新生成'
            if self._current_returned_available:
                message = (
                    "这个任务已有本地保存的模型返回稿，但尚未通过结构检查。"
                    "可以先取消并选择“检查返回内容／本地修复表格”，不需要再次请求模型。\n\n"
                    + message
                )
        else:
            title = "确认继续生成"
        if not self._confirm_egress(title, message, preview):
            set_status(self.status, "info", '已取消；没有继续生成备课初稿。')
            return
        try:
            summary = (
                self.facade.retry_preparation(task_id)
                if is_retry
                else self.facade.get_preparation(task_id)
            )
        except DesktopFacadeError as exc:
            set_status(self.status, "error", exc.message_zh)
            self._refresh_history()
            return
        except (OSError, RuntimeError, TypeError, ValueError):
            set_status(self.status, "error", "备课任务暂时无法继续，请刷新后重试。")
            self._refresh_history()
            return
        self._render_summary(summary)
        if str(self._field(summary, "status", "")) != "prepared":
            self._refresh_history()
            return
        self._start_generation_task(summary)

    def _generation_succeeded(self, summary: object) -> None:
        self._render_summary(summary)
        self._refresh_history()

    def _generation_failed(self, message: str) -> None:
        self.stop_button.setEnabled(False)
        self._hide_result_artifacts()
        if self._read_durable_summary() is None:
            set_status(self.status, "error", message)
            self.progress_message.setText("生成未完成，最终任务状态暂时无法确认。")
        self._refresh_history()

    def _read_durable_summary(self) -> object | None:
        task_id = self._active_preparation_task_id or self._preparation_task_id
        if not task_id:
            return None
        try:
            summary = self.facade.get_preparation(task_id)
        except (DesktopFacadeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        self._render_summary(summary)
        return summary

    def _stop_generation(self) -> None:
        if not self._active_preparation_task_id:
            return
        self.stop_button.setEnabled(False)
        self.progress_message.setText("正在停止；当前处理步骤结束后会退出。")
        set_status(self.status, "attention", "停止请求已提交，请稍候。")
        try:
            summary = self.facade.cancel_preparation(self._active_preparation_task_id)
        except (DesktopFacadeError, OSError, RuntimeError, TypeError, ValueError):
            if self._read_durable_summary() is None:
                self.progress_message.setText("最终任务状态暂时无法确认，请稍后再试。")
                set_status(self.status, "error", "停止任务时无法确认最终状态。")
            self._refresh_history()
            return
        self._render_summary(summary)
        durable_status = str(self._field(summary, "status", ""))
        if (
            durable_status in {"cancelled", "cancel_requested"}
            and self._generation_qt_task_id
        ):
            self.tasks.cancel(self._generation_qt_task_id)
        self._refresh_history()

    def _qt_task_cancelled(self, task_id: str, _label: str) -> None:
        if task_id != self._generation_qt_task_id:
            return
        if self._read_durable_summary() is None:
            self.progress_message.setText("正在核对任务最终状态，请从最近备课中刷新。")
            set_status(self.status, "attention", "任务最终状态暂时无法确认。")
        self._refresh_history()

    def _qt_task_finished(self, task_id: str) -> None:
        if task_id != self._generation_qt_task_id:
            return
        self._generation_qt_task_id = None
        self._active_preparation_task_id = None
        self.save_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.image_assets_widget.set_editing_enabled(True)
        self._update_generate_enabled()

    def _render_summary(self, summary: object) -> None:
        self._hide_result_artifacts()
        task_id = str(self._field(summary, "task_id", ""))
        if task_id:
            self._preparation_task_id = task_id
        status = str(self._field(summary, "status", ""))
        raw_percent = self._field(summary, "progress_percent", 0)
        try:
            percent = max(0, min(100, int(raw_percent)))
        except (TypeError, ValueError):
            percent = 0
        self.progress_card.show()
        self.progress.setValue(100 if status == "completed" else percent)
        message = str(self._field(summary, "message_zh", "任务状态已更新。"))
        self.progress_message.setText(message)
        active = status in {"prepared", "queued", "running", "cancel_requested"}
        retryable = bool(self._field(summary, "retryable", status == "cancelled"))
        self._current_task_status = status
        self._current_task_retryable = retryable
        self._current_returned_available = bool(
            self._field(summary, "returned_candidate_available", False)
        )
        self._current_task_source_kind = self._field(summary, "source_kind")
        local_revision = self._current_task_source_kind == "teacher_revision"
        if active and task_id:
            self._active_preparation_task_id = task_id
        elif self._generation_qt_task_id is None:
            self._active_preparation_task_id = None
        self.image_assets_widget.set_editing_enabled(
            not (
                self._save_task_id
                or self._generation_qt_task_id
                or status in {"queued", "running", "cancel_requested"}
            )
        )
        stoppable = status in {"running", "cancel_requested"}
        self.stop_button.setVisible(stoppable)
        self.stop_button.setEnabled(stoppable and status != "cancel_requested")
        self.task_action_button.hide()
        if status == "prepared":
            self.task_action_button.setText(
                '导出修改后的版本' if local_revision else '开始生成'
            )
            self.task_action_button.setAccessibleName('开始生成已准备的备课初稿')
            self.task_action_button.show()
        elif status in {"failed", "cancelled"} and retryable:
            self.task_action_button.setText(
                "重试本地导出" if local_revision else "重试生成"
            )
            self.task_action_button.setAccessibleName('重试当前备课初稿生成任务')
            self.task_action_button.show()
        if status == "completed":
            artifact_ids = {
                str(value) for value in self._field(summary, "artifact_ids", ()) or ()
            }
            slide_count = int(self._field(summary, "slide_count", 0) or 0)
            output_kind = str(self._field(summary, "output_kind", "joint"))
            kind_zh = {
                "ppt": 'PPT初稿',
                "lesson_plan": '教案初稿',
                "joint": 'PPT与教案初稿',
                "linked_bundle": 'PPT与教案初稿',
            }.get(output_kind, '备课初稿')
            page_text = f"，共 {slide_count} 页课件" if slide_count else ""
            self.result_summary.setText(
                ("本地修订版 · " if local_revision else "")
                + f"{kind_zh}已生成{page_text}，待教师检查后再用于教学。"
            )
            self.open_ppt_button.setVisible("pptx" in artifact_ids)
            self.open_lesson_button.setVisible("lesson_plan_docx" in artifact_ids)
            self.open_worksheet_button.setVisible(
                "student_worksheet_docx" in artifact_ids
            )
            self.open_preview_button.setVisible("preview_montage" in artifact_ids)
            self.review_structure_button.setVisible(
                "candidate_json" in artifact_ids and "pptx" in artifact_ids
            )
            self.revise_content_button.setVisible("candidate_json" in artifact_ids)
            self.result_card.show()
            set_status(
                self.status,
                "success",
                '初稿已生成。请检查内容和排版，再用于授课。',
            )
        elif status in {"failed", "blocked"}:
            if status == "failed" and self._current_returned_available:
                self.result_summary.setText(
                    "模型返回内容已保存在本机，但未通过结构检查，尚未生成课件。"
                    "可先查看返回稿、手动修正比较表；本地修复不调用模型。"
                )
                self.recover_returned_button.show()
                self.result_card.show()
            else:
                self.result_card.hide()
            set_status(self.status, "error", message)
        elif status == "cancelled":
            self.result_card.hide()
            set_status(self.status, "attention", '已停止生成。')
        else:
            self.result_card.hide()
            set_status(self.status, "info", message)

    def _hide_result_artifacts(self) -> None:
        """Clear artifact controls before rendering another task state."""

        for button in self._result_artifact_buttons:
            button.hide()
        self.review_structure_button.hide()
        self.revise_content_button.hide()
        self.recover_returned_button.hide()

    def _open_returned_recovery(self) -> None:
        if (
            not self._preparation_task_id
            or self._current_task_status != "failed"
            or not self._current_returned_available
        ):
            set_status(
                self.status, "attention", "请先选择有已保存返回稿的失败备课任务。"
            )
            return
        try:
            source = self.facade.preparation_returned_source(self._preparation_task_id)
            from .preparation_recovery_dialog import PreparationRecoveryDialog

            dialog = PreparationRecoveryDialog(self.facade, self.tasks, source, self)
        except DesktopFacadeError as exc:
            set_status(self.status, "error", exc.message_zh)
            return
        except Exception:  # noqa: BLE001 - no paths or raw exception content in UI
            set_status(self.status, "error", "已返回内容暂时无法读取，原文件未修改。")
            return
        dialog.revision_saved.connect(self._revision_saved)
        dialog.exec()

    def _open_revision(self) -> None:
        if not self._preparation_task_id or self._current_task_status != "completed":
            set_status(self.status, "attention", '请先选择已完成的备课初稿。')
            return
        try:
            source = self.facade.preparation_revision_source(self._preparation_task_id)
            from .preparation_revision_dialog import PreparationRevisionDialog

            dialog = PreparationRevisionDialog(self.facade, self.tasks, source, self)
        except Exception:  # noqa: BLE001 - keep original files and errors private
            set_status(self.status, "error", "备课原稿暂时无法读取，原文件未修改。")
            return
        dialog.revision_saved.connect(self._revision_saved)
        dialog.exec()

    def _revision_saved(self, summary: object) -> None:
        self._render_summary(summary)
        self._refresh_history()

    def _open_classroom_review(self) -> None:
        if not self._preparation_task_id or self._current_task_status != "completed":
            set_status(self.status, "attention", '请先选择已完成的课件初稿。')
            return
        try:
            report = self.facade.preparation_classroom_review(self._preparation_task_id)
        except Exception:  # noqa: BLE001 - never expose local paths or raw errors
            set_status(self.status, "error", "课堂结构暂时无法读取，原课件未修改。")
            return
        from .preparation_review_dialog import PreparationReviewDialog

        PreparationReviewDialog(report, self).exec()

    def _open_artifact(self, artifact_id: str) -> None:
        if not self._preparation_task_id:
            set_status(self.status, "error", '当前没有可打开的备课初稿。')
            return
        try:
            path = self.facade.preparation_artifact_path(
                self._preparation_task_id, artifact_id
            )
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except (OSError, RuntimeError, TypeError, ValueError):
            opened = False
        if not opened:
            set_status(self.status, "error", "文件暂时无法打开，请稍后重试。")

    def _refresh_history(self) -> None:
        if self._history_task_id:
            return
        self._history_task_id = self.tasks.submit(
            "刷新最近备课",
            lambda: self.facade.list_preparations(limit=3),
            on_success=self._history_ready,
            on_failure=self._history_failed,
        )

    def _history_ready(self, summaries: object) -> None:
        self._history_task_id = None
        self._populate_history(tuple(summaries))  # type: ignore[arg-type]

    def _history_failed(self, _message: str) -> None:
        self._history_task_id = None

    @staticmethod
    def _status_zh(status: str) -> str:
        return {
            "prepared": "已准备",
            "needs_confirmation": "等待教师确认",
            "queued": "排队中",
            "running": "生成中",
            "cancel_requested": "正在停止",
            "completed": '初稿已生成',
            "failed": "生成失败",
            "cancelled": "已停止",
            "blocked": "暂不可继续",
        }.get(status, "状态待更新")

    def _populate_history(self, summaries: tuple[object, ...]) -> None:
        while self.history_items.count():
            item = self.history_items.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._history_row_layouts.clear()
        if not summaries:
            self.history_empty = QLabel("暂无最近备课任务。")
            self.history_empty.setObjectName("MutedLabel")
            self.history_empty.setWordWrap(True)
            self.history_empty.setAccessibleName("最近备课任务状态")
            self.history_items.addWidget(self.history_empty)
            return
        for summary in summaries[:3]:
            row = QWidget()
            row_layout = QBoxLayout(
                QBoxLayout.Direction.TopToBottom
                if self._compact
                else QBoxLayout.Direction.LeftToRight,
                row,
            )
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            title = str(self._field(summary, "title_zh", "未命名备课"))
            status = self._status_zh(str(self._field(summary, "status", "")))
            created = str(self._field(summary, "created_at", "")).replace("T", " ")[:16]
            detail = QLabel(f"{title}\n{status}" + (f" · {created}" if created else ""))
            detail.setWordWrap(True)
            detail.setMinimumWidth(0)
            detail.setAccessibleName(f"最近备课：{title}，{status}")
            raw_status = str(self._field(summary, "status", ""))
            retryable = bool(
                self._field(summary, "retryable", raw_status == "cancelled")
            )
            if raw_status == "completed":
                action_label = "查看结果"
            elif raw_status == "prepared":
                action_label = "开始生成"
            elif raw_status in {"failed", "cancelled"} and retryable:
                action_label = "重试生成"
            elif raw_status in {"failed", "cancelled", "blocked"}:
                action_label = "查看原因"
            else:
                action_label = "继续查看"
            button = QPushButton(action_label)
            button.setObjectName("QuietButton")
            button.setAccessibleName(f"{action_label}：{title}")
            button.clicked.connect(
                lambda _checked=False, value=summary: self._activate_history_summary(
                    value
                )
            )
            row_layout.addWidget(detail, 1)
            row_layout.addWidget(button)
            self._history_row_layouts.append(row_layout)
            self.history_items.addWidget(row)

    def _restore_summary(self, summary: object) -> None:
        self._render_summary(summary)
        self._update_generate_enabled()

    def _activate_history_summary(self, summary: object) -> None:
        self._restore_summary(summary)
        status = str(self._field(summary, "status", ""))
        retryable = bool(self._field(summary, "retryable", status == "cancelled"))
        if status == "prepared" or (status in {"failed", "cancelled"} and retryable):
            self._activate_current_task()

    def resizeEvent(self, event: QResizeEvent) -> None:
        compact = event.size().width() < 600
        if compact != self._compact:
            self._compact = compact
            direction = (
                QBoxLayout.Direction.TopToBottom
                if compact
                else QBoxLayout.Direction.LeftToRight
            )
            self.actions.setDirection(direction)
            self.timing_layout.setDirection(direction)
            self.output_layout.setDirection(direction)
            self.result_actions.setDirection(direction)
            for layout in self._history_row_layouts:
                layout.setDirection(direction)
        super().resizeEvent(event)


__all__ = [
    "PaperPage",
    "PaperPreviewDialog",
    "PreparationPage",
    "StudentPage",
]
