from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import (
    DesktopFacadeError,
    DesktopVisualImportReceipt,
    DesktopWorkbenchFacade,
    ProviderProfileInput,
    ProviderProfileSummary,
)
from ..desktop_provider_probe import ProviderConnectionResult
from ..model_provider_probe import FIXED_SYNTHETIC_PROMPT
from .components import (
    VISUAL_IMPORT_SOURCE_SUFFIXES,
    CardFrame,
    CollapsibleSection,
    FileSelectionPanel,
    page_scroll,
    section_title,
    set_status,
)
from .tasks import DesktopTaskBridge


class ImportDialog(QDialog):
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
        self._corpus_task_id: str | None = None
        # Keep the dialog alive while a worker can still invoke one of its
        # callbacks.  Closing a dialog with an in-flight worker would leave a
        # queued lambda pointing at a deleted QWidget.
        self._active_task_id: str | None = None
        self._active_task_kind: str | None = None
        self._saved_visual_receipt: DesktopVisualImportReceipt | None = None
        self.preparation_reference: dict | None = None
        self._word_receipts: dict[str, DesktopVisualImportReceipt] = {}
        self._import_preview_session: dict | None = None
        self._import_preview_epoch = 0
        self._resumable_receipts: tuple[DesktopVisualImportReceipt, ...] = ()
        self.setWindowTitle("导入资料")
        self.resize(720, 680)
        self.setMinimumSize(420, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)
        root.addWidget(
            section_title(
                "导入资料",
                "先在本机预览并选择文件，确认后再保存。视觉识别另行选择模型并确认。",
            )
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        state_card = CardFrame()
        state_layout = QVBoxLayout(state_card)
        state_layout.setContentsMargins(16, 13, 16, 13)
        state_title = QLabel("资料处理状态")
        state_title.setObjectName("CardTitle")
        state_layout.addWidget(state_title)
        for text in (
            "文字内容完整：Word 文字可直接读取，仍需核对题号与页面。",
            "文字已读，图片待识别：图片、公式或版面关系交给视觉模型核对。",
            "图片资料：扫描页面需要视觉模型识别。",
        ):
            label = QLabel("• " + text)
            label.setWordWrap(True)
            label.setObjectName("MutedLabel")
            state_layout.addWidget(label)
        self.corpus_button = QPushButton("预览指定一轮复习解析版（98 份）")
        self.corpus_button.setAccessibleName("预览指定上好课资料包的 98 份解析版 Word，再选择导入")
        self.corpus_button.clicked.connect(self._run_one_round_corpus)
        state_layout.addWidget(self.corpus_button)
        content_layout.addWidget(state_card)

        self.resume_card = CardFrame()
        resume_layout = QVBoxLayout(self.resume_card)
        resume_layout.setContentsMargins(16, 13, 16, 13)
        resume_title = QLabel("继续上次视觉导入")
        resume_title.setObjectName("CardTitle")
        resume_layout.addWidget(resume_title)
        self.resume_summary = QLabel()
        self.resume_summary.setObjectName("MutedLabel")
        self.resume_summary.setWordWrap(True)
        resume_layout.addWidget(self.resume_summary)
        self.resume_button = QPushButton("继续最近一批")
        self.resume_button.setAccessibleName("继续最近一批待处理的视觉导入")
        self.resume_button.clicked.connect(self._resume_latest)
        resume_layout.addWidget(
            self.resume_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self.resume_card.setVisible(False)
        content_layout.addWidget(self.resume_card)

        type_card = CardFrame()
        form = QFormLayout(type_card)
        form.setContentsMargins(16, 14, 16, 14)
        self.source_type = QComboBox()
        self.source_type.setAccessibleName("资料类型")
        for value in (
            "试卷与答案",
            "教材与课程文件",
            "教师讲义",
            "学生作答",
            "热点资料",
        ):
            self.source_type.addItem(value)
        form.addRow("资料类型", self.source_type)
        format_note = QLabel(
            "支持 PNG、JPEG、WebP、PDF、DOCX；列表顺序就是页面处理顺序。"
        )
        format_note.setObjectName("MutedLabel")
        format_note.setWordWrap(True)
        form.addRow("", format_note)
        content_layout.addWidget(type_card)

        self.question_files = FileSelectionPanel(
            "添加题目页或整份试卷；共享材料与主题内小问请保持原顺序。",
            title="题目页 / 试卷",
            supported_suffixes=VISUAL_IMPORT_SOURCE_SUFFIXES,
            allow_reordering=True,
            accessible_name="题目页或试卷",
        )
        self.answer_files = FileSelectionPanel(
            "只放参考答案页；答案会与题目分开保存，且不会被标成官方答案。",
            title="参考答案页",
            supported_suffixes=VISUAL_IMPORT_SOURCE_SUFFIXES,
            allow_reordering=True,
            accessible_name="参考答案页",
        )
        self.handout_files = FileSelectionPanel(
            "添加教师讲义；可读取的 Word 文字先形成原生文字候选，图片与公式进入视觉队列。",
            title="教师讲义",
            supported_suffixes=VISUAL_IMPORT_SOURCE_SUFFIXES,
            allow_reordering=True,
            accessible_name="教师讲义",
        )
        # Preserve the public attribute used by older shell tests and callers;
        # it now refers to the question/试卷 role rather than a mixed pool.
        self.files = self.question_files
        for panel in self._role_panels():
            content_layout.addWidget(panel)
            panel.files_changed.connect(self._preview_inputs_changed)
        self.source_type.currentTextChanged.connect(self._preview_inputs_changed)

        self.provider_card = CardFrame()
        provider_layout = QVBoxLayout(self.provider_card)
        provider_layout.setContentsMargins(16, 14, 16, 14)
        provider_layout.setSpacing(9)
        provider_title = QLabel("第二步：生成视觉候选")
        provider_title.setObjectName("CardTitle")
        provider_layout.addWidget(provider_title)
        provider_explanation = QLabel(
            "仅处理上一步已保存的待视觉资料及其渲染页面；结果仍是候选，须由教师逐页复核。"
        )
        provider_explanation.setObjectName("MutedLabel")
        provider_explanation.setWordWrap(True)
        provider_layout.addWidget(provider_explanation)
        provider_form = QFormLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.setAccessibleName("用于生成视觉候选的模型")
        provider_form.addRow("视觉模型", self.provider_combo)
        provider_layout.addLayout(provider_form)
        self.provider_note = QLabel()
        self.provider_note.setObjectName("MutedLabel")
        self.provider_note.setWordWrap(True)
        provider_layout.addWidget(self.provider_note)
        self.generate_button = QPushButton("生成视觉候选")
        self.generate_button.setAccessibleName("确认后生成视觉候选")
        self.generate_button.clicked.connect(self._run_visual)
        provider_layout.addWidget(
            self.generate_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self.provider_card.setVisible(False)
        content_layout.addWidget(self.provider_card)
        self.word_history_card = CardFrame()
        word_history_layout = QVBoxLayout(self.word_history_card)
        word_history_layout.setContentsMargins(16, 13, 16, 13)
        word_history_title = QLabel("已导入 Word")
        word_history_title.setObjectName("CardTitle")
        word_history_layout.addWidget(word_history_title)
        self.word_batch_combo = QComboBox()
        self.word_batch_combo.setAccessibleName("选择已保存的 Word 导入批次")
        self.word_batch_combo.setMinimumContentsLength(12)
        self.word_batch_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        word_history_layout.addWidget(self.word_batch_combo)
        self.word_questions_button = QPushButton("逐题预览与挑选…")
        self.word_questions_button.setObjectName("PrimaryButton")
        self.word_questions_button.clicked.connect(self._open_word_questions)
        word_history_layout.addWidget(self.word_questions_button)
        self.word_reference_button = QPushButton("查看 Word 内容并带入备课…")
        self.word_reference_button.setAccessibleName("查看已保存的 Word 内容并带入备课")
        self.word_reference_button.clicked.connect(self._open_word_reference)
        self.word_reference_button.setVisible(False)
        word_history_layout.addWidget(self.word_reference_button)
        self.word_history_card.setVisible(False)
        content_layout.addWidget(self.word_history_card)
        content_layout.addStretch(1)

        self.scroll = page_scroll(content)
        self.scroll.setObjectName("ImportScroll")
        root.addWidget(self.scroll, 1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        self.progress.setAccessibleName("资料导入进度")
        root.addWidget(self.progress)
        self.cancel_button = QPushButton("停止当前任务")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.setAccessibleName("停止当前导入任务")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_active)
        root.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.status = QLabel("请按题目、答案、讲义分别添加资料；参考答案不能单独保存。")
        self.status.setObjectName("StatusInfo")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("导入状态")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.setAccessibleName("关闭导入窗口")
        self.close_button.clicked.connect(self.reject)
        self.save_button = QPushButton("预览并选择导入")
        self.save_button.setAccessibleName("先在本机预览来源，再选择整份文件导入")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.save_button)
        root.addLayout(buttons)
        self.tasks.task_finished.connect(self._task_finished)
        self.tasks.task_cancelled.connect(self._task_cancelled)
        self._load_resumable_batches()
        self._load_word_batches()

    def _role_panels(self) -> tuple[FileSelectionPanel, ...]:
        return (self.question_files, self.answer_files, self.handout_files)

    def _load_resumable_batches(self) -> None:
        try:
            receipts = tuple(self.facade.list_resumable_visual_import_batches())
        except (
            AttributeError,
            DesktopFacadeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            receipts = ()
            set_status(
                self.status,
                "attention",
                "上次保存的视觉导入暂时无法读取；仍可开始新的离线保存。",
            )
        self._resumable_receipts = receipts
        if not receipts:
            self.resume_card.setVisible(False)
            return
        latest = receipts[-1]
        state = "上次生成未完成" if latest.status == "failed" else "已离线保存"
        self.resume_summary.setText(
            f"{state}：{latest.source_type}，{latest.source_count} 份来源，"
            f"{latest.visual_queue_count} 份待视觉资料。无需重新选择本地文件。"
        )
        self.resume_card.setVisible(True)

    def _resume_latest(self) -> None:
        if self._active_task_id or not self._resumable_receipts:
            return
        self._saved_visual_receipt = self._resumable_receipts[-1]
        self.resume_card.setVisible(False)
        self._show_saved_receipt(self._saved_visual_receipt, resumed=True)

    def _save(self) -> None:
        if self._active_task_id:
            return
        arguments = {
            "question_files": tuple(self.question_files.paths()),
            "answer_files": tuple(self.answer_files.paths()),
            "handout_files": tuple(self.handout_files.paths()),
            "source_type": self.source_type.currentText().strip(),
        }
        self._start_import_preview(lambda: self.facade.preview_import_files(**arguments))

    def _preview_inputs_changed(self, *_args) -> None:
        self._import_preview_epoch += 1
        self._discard_import_preview()

    def _discard_import_preview(self) -> None:
        preview, self._import_preview_session = self._import_preview_session, None
        if preview:
            try:
                self.facade.discard_import_preview(preview["preview_id"])
            except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
                pass

    def _start_import_preview(self, loader) -> None:
        self._discard_import_preview()
        self._import_preview_epoch += 1
        epoch = self._import_preview_epoch
        self._begin_task("preview", "正在核对文件清单，随后打开原文预览；尚未保存，不调用模型。")
        def operation(_report, cancelled):
            value = loader()
            if cancelled():
                self.facade.discard_import_preview(value["preview_id"])
            return value
        def ready(value):
            if epoch != self._import_preview_epoch:
                if isinstance(value, dict) and value.get("preview_id"):
                    self.facade.discard_import_preview(value["preview_id"])
                return
            self._open_import_preview(value, epoch)
        try:
            self._active_task_id = self.tasks.submit_progress(
                "准备导入前预览", operation, on_success=ready,
                on_failure=self._import_failed,
            )
        except RuntimeError:
            self._active_task_id = None
            self._active_task_kind = None
            self._set_busy(False)
            set_status(self.status, "error", "预览暂时无法启动，尚未保存任何来源。")

    def _open_import_preview(self, preview, epoch) -> None:
        from .import_preview_dialog import ImportPreviewDialog

        if (
            not isinstance(preview, dict)
            or not isinstance(preview.get("preview_id"), str)
            or not isinstance(preview.get("revision"), str)
            or not isinstance(preview.get("sources"), list)
            or not preview["sources"]
        ):
            set_status(self.status, "error", "没有可预览的来源文件，尚未保存。")
            return
        self._import_preview_session = preview
        self._active_task_id = None
        self._active_task_kind = None
        self.cancel_button.hide()
        self._set_busy(False)
        dialog = ImportPreviewDialog(self.facade, self.tasks, preview, self)
        result = dialog.exec()
        selected = list(dialog.selected_source_ids)
        dialog.deleteLater()
        if result != QDialog.DialogCode.Accepted or epoch != self._import_preview_epoch:
            self._discard_import_preview()
            set_status(self.status, "info", "已取消本次导入预览，未保存到个人题库。")
            return
        if not selected:
            self._discard_import_preview()
            set_status(self.status, "attention", "未选择文件，未保存。")
            return
        self._begin_task("commit", f"正在核对并保存所选 {len(selected)} 份完整文件，请稍候。此阶段不能撤销，不调用模型。")
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        try:
            self._active_task_id = self.tasks.submit_progress(
                "保存已预览的所选文件",
                lambda report, cancelled: self.facade.commit_import_preview(
                    preview["preview_id"], preview["revision"], selected,
                    progress_callback=report, should_cancel=cancelled,
                ),
                on_progress=self._import_progress,
                on_success=self._visual_batch_saved,
                on_failure=self._import_failed,
            )
        except RuntimeError:
            self._discard_import_preview()
            self._active_task_id = None
            self._active_task_kind = None
            self._set_busy(False)
            set_status(self.status, "error", "所选文件暂未保存，请重新预览后重试。")

    def _begin_task(self, kind: str, message: str) -> None:
        self._active_task_kind = kind
        self.progress.setRange(0, 3)
        self.progress.setValue(0)
        self.progress.setFormat("正在准备…")
        self.progress.setVisible(True)
        self.cancel_button.setText(
            "停止批量读取" if kind == "corpus" else "停止当前任务"
        )
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self._set_busy(True)
        set_status(self.status, "info", message)

    def _set_busy(self, busy: bool) -> None:
        self.source_type.setEnabled(not busy)
        for panel in self._role_panels():
            panel.setEnabled(not busy)
        self.corpus_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.resume_button.setEnabled(not busy)
        self.word_batch_combo.setEnabled(not busy)
        self.word_reference_button.setEnabled(
            not busy and self.word_batch_combo.count() > 0
        )
        self.word_questions_button.setEnabled(not busy)
        if busy:
            self.generate_button.setEnabled(False)
        else:
            self._update_generate_enabled()

    def _import_progress(self, value: object) -> None:
        payload = value.as_dict() if hasattr(value, "as_dict") else value
        if not isinstance(payload, dict):
            return
        stage = payload.get("stage")
        kind = self._active_task_kind
        if stage == "planned":
            self.progress.setValue(1)
            if kind == "visual":
                text = "第 2/2 步：已核对保存批次"
            else:
                text = "第 1/2 步：正在核对来源（离线）"
        elif stage == "visual_pages":
            self.progress.setValue(2)
            if kind == "visual":
                text = "第 2/2 步：正在逐页生成视觉候选"
            else:
                text = "第 1/2 步：正在整理待视觉资料（不会发送给模型）"
        elif stage == "completed":
            self.progress.setValue(3)
            text = (
                "第 2/2 步：候选已生成"
                if kind == "visual"
                else "第 1/2 步：已保存并分流"
            )
        else:
            return
        self.progress.setFormat(text)
        set_status(self.status, "info", text + "…")

    @staticmethod
    def _source_role_counts(
        receipt: DesktopVisualImportReceipt,
    ) -> tuple[int, int, int]:
        roles = [getattr(item, "role", "") for item in receipt.sources]
        return roles.count("question"), roles.count("answer"), roles.count("handout")

    def _show_saved_receipt(
        self, receipt: DesktopVisualImportReceipt, *, resumed: bool = False
    ) -> None:
        self._remember_word_receipt(receipt, select=True)
        question_count, answer_count, handout_count = self._source_role_counts(receipt)
        summary = (
            f"来源 {receipt.source_count} 份（题目 {question_count}、答案 {answer_count}、"
            f"讲义 {handout_count}）；可完整读取的 Word 来源 {receipt.native_quick_count} 份；"
            f"待视觉资料 {receipt.visual_queue_count} 份。"
        )
        if receipt.visual_status == "completed" or receipt.visual_queue_count <= 0:
            self.provider_card.setVisible(False)
            set_status(
                self.status,
                "success",
                summary + " 候选已保存，待教师复核；本批无需生成视觉候选。",
            )
            return
        self.provider_card.setVisible(True)
        self._refresh_visual_profiles()
        lead = "已恢复最近一批。" if resumed else "离线保存完成。"
        set_status(
            self.status,
            "success",
            lead + summary + " 可在第二步选择模型生成视觉候选。",
        )

    def _load_word_batches(self) -> None:
        loader = getattr(self.facade, "list_imported_word_batches", None)
        if not callable(loader):
            return
        try:
            for receipt in loader():
                self._remember_word_receipt(receipt, select=True)
        except (DesktopFacadeError, OSError, RuntimeError, TypeError, ValueError):
            set_status(
                self.status,
                "attention",
                "已保存的 Word 列表暂时无法读取；仍可选择新资料并在本机保存。",
            )

    def _remember_word_receipt(
        self, receipt: DesktopVisualImportReceipt, *, select: bool = False
    ) -> None:
        names = [
            item.filename.replace("\\", "/").rsplit("/", 1)[-1]
            for item in receipt.sources
            if item.filename.lower().endswith(".docx")
        ]
        if not receipt.batch_id or not names:
            return
        self._word_receipts[receipt.batch_id] = receipt
        index = self.word_batch_combo.findData(receipt.batch_id)
        label = "、".join(names[:2]) + ("等" if len(names) > 2 else "")
        label += f" · {len(names)} 份 Word"
        if index < 0:
            self.word_batch_combo.addItem(label, receipt.batch_id)
            index = self.word_batch_combo.count() - 1
        else:
            self.word_batch_combo.setItemText(index, label)
        if select:
            self.word_batch_combo.setCurrentIndex(index)
        self.word_history_card.setVisible(True)
        self.word_reference_button.setVisible(True)
        self.word_reference_button.setEnabled(self._active_task_id is None)

    def _open_word_reference(self) -> None:
        from .import_word_dialog import ImportWordDialog

        batch_id = self.word_batch_combo.currentData()
        if self._active_task_id or batch_id not in self._word_receipts:
            return
        dialog = ImportWordDialog(self.facade, batch_id, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.reference is not None:
            self.preparation_reference = dialog.reference
            self.accept()
        dialog.deleteLater()

    def _open_word_questions(self) -> None:
        from .word_question_dialog import WordQuestionDialog

        if self._active_task_id:
            return
        dialog = WordQuestionDialog(self.facade, self.tasks, self, batch_id=self.word_batch_combo.currentData())
        dialog.basket_changed.connect(self.basket_changed)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.preparation_reference is not None:
            self.preparation_reference = dialog.preparation_reference
            self.accept()
        dialog.deleteLater()

    def _refresh_visual_profiles(self) -> None:
        selected = self.provider_combo.currentData()
        self.provider_combo.clear()
        try:
            profiles = tuple(self.facade.list_provider_profiles())
        except (
            AttributeError,
            DesktopFacadeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            profiles = ()
        for profile in profiles:
            capabilities = set(getattr(profile, "capabilities", ()))
            if getattr(profile, "key_saved", False) is not True or not {
                "vision",
                "structured_output",
            }.issubset(capabilities):
                continue
            provider_name = str(getattr(profile, "provider_name", "未命名服务"))
            model_id = str(getattr(profile, "model_id", "未命名模型"))
            data = (
                str(getattr(profile, "profile_id", "")),
                str(getattr(profile, "revision", "")),
            )
            self.provider_combo.addItem(f"{provider_name} / {model_id}", data)
        if selected is not None:
            index = self.provider_combo.findData(selected)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        if self.provider_combo.count() == 0:
            self.provider_note.setText(
                "尚无可用视觉模型。请关闭本窗口后到“设置”保存支持图片与结构化输出的模型；离线候选不受影响。"
            )
        else:
            self.provider_note.setText(
                "只列出已保存 Key 且同时支持图片输入、结构化输出的模型。"
            )
        self._update_generate_enabled()

    def _update_generate_enabled(self) -> None:
        receipt = self._saved_visual_receipt
        self.generate_button.setEnabled(
            self._active_task_id is None
            and receipt is not None
            and receipt.visual_queue_count > 0
            and receipt.visual_status != "completed"
            and self.provider_combo.count() > 0
        )

    def _run_visual(self) -> None:
        if self._active_task_id:
            return
        receipt = self._saved_visual_receipt
        if receipt is None or receipt.visual_queue_count <= 0:
            return
        self._refresh_visual_profiles()
        selected = self.provider_combo.currentData()
        if not (
            isinstance(selected, tuple)
            and len(selected) == 2
            and all(isinstance(value, str) and value for value in selected)
        ):
            set_status(
                self.status,
                "attention",
                "尚无可用视觉模型；请先到“设置”完成模型与 Key 配置。离线候选已保存。",
            )
            return
        answer = QMessageBox.question(
            self,
            "确认发送已保存页面",
            "将把本批待视觉资料渲染后的已确认页面发送给所选视觉模型，以生成结构化候选。"
            "这可能产生模型费用，页面内容也会离开本机。请先确认学校授权、费用与隐私要求。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            set_status(
                self.status,
                "attention",
                "已取消发送；离线候选仍已保存，可稍后再生成视觉候选。",
            )
            return
        profile_id, revision = selected
        self._begin_task(
            "visual",
            "正在生成视觉候选；如停止，将在当前页处理结束后停止。",
        )
        self._active_task_id = self.tasks.submit_progress(
            "生成视觉候选",
            lambda report, cancelled: self.facade.run_saved_visual_import_batch(
                batch_id=receipt.batch_id,
                profile_id=profile_id,
                expected_profile_revision=revision,
                teacher_confirmed=True,
                progress_callback=report,
                should_cancel=cancelled,
            ),
            on_progress=self._import_progress,
            on_success=self._visual_completed,
            on_failure=self._import_failed,
        )

    def _visual_batch_saved(self, receipt: DesktopVisualImportReceipt) -> None:
        self._saved_visual_receipt = receipt
        self.progress.setValue(3)
        self.progress.setFormat("第一步已完成：已保存并分流")
        self._show_saved_receipt(receipt)

    def _visual_completed(self, receipt: DesktopVisualImportReceipt) -> None:
        self._saved_visual_receipt = receipt
        self.progress.setValue(3)
        if receipt.visual_status == "completed":
            self.progress.setFormat("第二步已完成：候选待复核")
            self.provider_card.setVisible(False)
            self.resume_card.setVisible(False)
            set_status(
                self.status,
                "success",
                "候选已生成，待教师逐页复核；本次结果没有写入正式题库。",
            )
            return
        self.progress.setFormat("视觉候选未生成")
        self.provider_card.setVisible(True)
        self._refresh_visual_profiles()
        set_status(
            self.status,
            "error",
            "视觉候选未生成；离线来源仍已保存，可稍后继续最近一批。",
        )

    def _import_failed(self, message: str) -> None:
        self.progress.setFormat("任务未完成")
        if self._active_task_kind == "visual":
            text = f"视觉候选未生成；离线来源仍已保存，可重试。{message}"
        elif self._active_task_kind == "preview":
            text = f"导入预览未完成，尚未保存。{message}"
        else:
            text = f"离线保存未完成。{message}"
        set_status(self.status, "error", text)

    def _run_one_round_corpus(self) -> None:
        if self._active_task_id:
            return
        self._start_import_preview(lambda: self.facade.preview_import_files(
            question_files=(), answer_files=(),
            handout_files=self.facade.teaching_pack_analysis_files(),
            source_type="教师讲义",
        ))

    def _corpus_progress(self, value: object) -> None:
        payload = value.as_dict() if hasattr(value, "as_dict") else value
        if not isinstance(payload, dict):
            return
        total = payload.get("total_documents")
        processed = payload.get("processed_documents")
        failed = payload.get("failed_documents")
        if type(total) is int and total > 0:
            self.progress.setMaximum(total)
        if type(processed) is int:
            self.progress.setValue(processed)
        self.progress.setFormat(f"{processed or 0}/{total or 196} 份")
        self.status.setText(
            f"已处理 {processed or 0} 份，失败 {failed or 0} 份；含图片和公式的题会进入待图片识别列表。"
        )

    def _corpus_saved(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        completed = payload.get("documents_completed", 0)
        quick = payload.get("quick_import_candidates", 0)
        visual = payload.get("visual_completion_candidates", 0)
        paired = payload.get("paired_question_candidates", 0)
        failed = payload.get("documents_failed", 0)
        self.progress.setValue(self.progress.maximum())
        self.progress.setFormat("讲义候选已保存")
        set_status(self.status, "success")
        self.corpus_button.setText("再次导入一轮复习讲义（98 包 / 196 份）")
        self.status.setText(
            f"已处理 {completed} 份 Word：{quick} 项原生文字候选，{visual} 项待视觉处理，"
            f"{paired} 项已匹配参考解析，失败 {failed} 个文件；全部仍待教师复核。"
        )

    def _corpus_failed(self, message: str) -> None:
        self.progress.setFormat("批量导入未完成")
        set_status(self.status, "error")
        self.corpus_button.setText("重试一轮复习讲义")
        self.status.setText(message)

    def _cancel_active(self) -> None:
        if self._active_task_kind == "commit":
            set_status(self.status, "info", "正在完整保存所选文件，请稍候；本阶段不能撤销。")
            return
        if self._active_task_id:
            self.tasks.cancel(self._active_task_id)
            self.cancel_button.setEnabled(False)
            set_status(self.status, "attention")
            if self._active_task_kind == "visual":
                self.status.setText("已请求停止，将在当前页处理结束后停止。")
            elif self._active_task_kind == "corpus":
                self.status.setText("已请求停止批量读取；正在完成当前文件。")
            else:
                self.status.setText("已请求停止；正在完成当前文件的本地处理。")

    def _cancel_corpus(self) -> None:
        # Kept for compatibility with older callers and tests.
        self._cancel_active()

    def _task_cancelled(self, task_id: str, _label: str) -> None:
        if task_id != self._active_task_id:
            return
        self.progress.setFormat("任务已停止")
        set_status(
            self.status,
            "attention",
            "任务已停止；已完成的本地保存不会被改成正式题库记录。",
        )

    def _task_finished(self, task_id: str) -> None:
        if task_id != self._active_task_id:
            return
        if self._active_task_kind == "commit":
            self._discard_import_preview()
        self._active_task_id = None
        if task_id == self._corpus_task_id:
            self._corpus_task_id = None
        self._active_task_kind = None
        self.cancel_button.setVisible(False)
        self._set_busy(False)

    def reject(self) -> None:
        if self._active_task_id:
            set_status(self.status, "attention")
            if self._active_task_kind == "visual":
                self.status.setText(
                    "视觉候选仍在生成；可请求停止，但需等待当前页处理结束后再关闭。"
                )
            elif self._corpus_task_id:
                self.status.setText("批量读取仍在进行；请先停止并等待任务结束。")
            else:
                self.status.setText("导入内容仍在本机保存；请等待任务结束后再关闭。")
            return
        self._import_preview_epoch += 1
        self._discard_import_preview()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._active_task_id:
            set_status(self.status, "attention")
            if self._active_task_kind == "visual":
                self.status.setText(
                    "视觉候选仍在生成；请先请求停止并等待当前页处理结束。"
                )
            elif self._corpus_task_id:
                self.status.setText("批量读取仍在进行；请先停止并等待任务结束。")
            else:
                self.status.setText("导入内容仍在本机保存；请等待任务结束后再关闭。")
            if hasattr(event, "ignore"):
                event.ignore()
            return
        event.accept()
        self._import_preview_epoch += 1
        self._discard_import_preview()


class SettingsDialog(QDialog):
    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self._active_task_id: str | None = None
        self._profile: ProviderProfileSummary | None = None
        self._testing = False
        self._probe_cancel = threading.Event()
        self.setWindowTitle("设置")
        self.resize(720, 650)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)
        root.addWidget(
            section_title(
                "AI 模型设置",
                "可自由填写服务商、接口地址和模型名称；实验版新模型也可直接输入，密钥只显示保存状态。",
            )
        )

        form_card = CardFrame()
        form = QFormLayout(form_card)
        self._settings_form = form
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.provider_name = QLineEdit()
        self.provider_name.setPlaceholderText("例如：DeepSeek 或学校模型服务")
        self.provider_name.setAccessibleName("服务商名称")
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("粘贴服务商提供的接口地址")
        self.base_url.setAccessibleName("模型接口地址")
        self.model_id = QLineEdit()
        self.model_id.setPlaceholderText("直接填写模型名称，包括实验版模型")
        self.model_id.setAccessibleName("模型名称")
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("留空则保持当前保存状态")
        self.key_input.setClearButtonEnabled(True)
        self.key_input.setAccessibleName("API 密钥，输入内容不显示")
        self.key_state = QLabel("密钥状态：正在读取…")
        self.key_state.setObjectName("MutedLabel")
        form.addRow("服务商名称", self.provider_name)
        form.addRow("接口地址（Base URL）", self.base_url)
        form.addRow("模型名称（Model ID）", self.model_id)
        form.addRow("API 密钥（API Key）", self.key_input)
        form.addRow("", self.key_state)
        root.addWidget(form_card)

        advanced = CollapsibleSection("高级设置")
        advanced_form = QFormLayout()
        self.api_style = QComboBox()
        self.api_style.setAccessibleName("接口格式")
        self.api_style.addItem("新式响应接口（Responses）", "responses")
        self.api_style.addItem("聊天补全接口（Chat Completions）", "chat_completions")
        self.vision = QCheckBox("允许确认后发送原始页面")
        self.vision.setAccessibleName("允许确认后发送原始页面")
        self.vision.setChecked(True)
        capability_note = QLabel(
            "要识别题目图片，所选模型需要支持图片输入和结构化结果；不支持时仍可使用本地题库。"
        )
        capability_note.setWordWrap(True)
        capability_note.setObjectName("MutedLabel")
        advanced_form.addRow("接口格式", self.api_style)
        advanced_form.addRow("图片识别", self.vision)
        advanced_form.addRow("", capability_note)
        advanced.content_layout.addLayout(advanced_form)
        root.addWidget(advanced)

        self.status = QLabel("保存设置后可测试连接；测试前会显示出站内容与费用提示。")
        self.status.setObjectName("StatusInfo")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        actions.rejected.connect(self.reject)
        close_action = actions.button(QDialogButtonBox.StandardButton.Close)
        if close_action is not None:
            close_action.setText("关闭")
            close_action.setAccessibleName("关闭设置窗口")
            self.close_button = close_action
        else:
            self.close_button = None
        self.save_button = QPushButton("保存设置")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        actions.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.test_button = QPushButton("测试连接")
        self.test_button.setAccessibleName("测试已保存的模型连接")
        self.test_button.setEnabled(False)
        self.test_button.clicked.connect(self._test_connection)
        actions.addButton(self.test_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.stop_test_button = QPushButton("停止测试")
        self.stop_test_button.setVisible(False)
        self.stop_test_button.clicked.connect(self._stop_test)
        actions.addButton(self.stop_test_button, QDialogButtonBox.ButtonRole.ActionRole)
        root.addWidget(actions)
        for field in (self.provider_name, self.base_url, self.model_id, self.key_input):
            field.textChanged.connect(self._refresh_test_enabled)
        self.api_style.currentIndexChanged.connect(self._refresh_test_enabled)
        self.vision.toggled.connect(self._refresh_test_enabled)
        self.tasks.task_finished.connect(self._task_finished)
        self._load()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_settings_form"):
            self._settings_form.setRowWrapPolicy(
                QFormLayout.RowWrapPolicy.WrapAllRows if self.width() < 580
                else QFormLayout.RowWrapPolicy.WrapLongRows
            )

    def _matches_saved(self) -> bool:
        profile = self._profile
        return bool(
            profile
            and not self.key_input.text()
            and self.provider_name.text().strip() == profile.provider_name
            and self.base_url.text().strip() == profile.base_url
            and self.model_id.text().strip() == profile.model_id
            and self.api_style.currentData() == profile.api_style
            and self.vision.isChecked() == ("vision" in profile.capabilities)
        )

    def _refresh_test_enabled(self) -> None:
        ready = bool(self._profile and self._profile.key_saved and self._matches_saved())
        self.test_button.setEnabled(ready and not self._active_task_id and not self._testing)
        self.test_button.setToolTip(
            "发送固定短文本，检查已保存的模型配置" if ready else "请先保存模型配置和密钥；修改后需重新保存"
        )

    def _set_form_enabled(self, enabled: bool) -> None:
        for field in (self.provider_name, self.base_url, self.model_id, self.key_input, self.api_style, self.vision):
            field.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.test_button.setEnabled(False)
        if self.close_button is not None:
            self.close_button.setEnabled(enabled)

    def _load(self) -> None:
        self._set_form_enabled(False)
        self._active_task_id = self.tasks.submit(
            "读取模型设置",
            self.facade.list_provider_profiles,
            on_success=self._profiles_loaded,
            on_failure=self._load_failed,
        )

    def _profiles_loaded(self, profiles: tuple[ProviderProfileSummary, ...]) -> None:
        profile = next(
            (item for item in profiles if item.profile_id == "desktop-default"),
            profiles[0] if profiles else None,
        )
        self._profile = profile
        if profile is None:
            self.key_state.setText("密钥状态：未保存")
            return
        self.provider_name.setText(profile.provider_name)
        self.base_url.setText(profile.base_url)
        self.model_id.setText(profile.model_id)
        index = self.api_style.findData(profile.api_style)
        if index >= 0:
            self.api_style.setCurrentIndex(index)
        self.vision.setChecked("vision" in profile.capabilities)
        self.key_state.setText(
            "密钥状态：已保存" if profile.key_saved else "密钥状态：未保存"
        )
        if profile.last_connection_test is not None:
            self._show_connection_result(profile.last_connection_test, previous=True)

    def _load_failed(self, message: str) -> None:
        self.save_button.setEnabled(True)
        self.key_state.setText("密钥状态：无法读取")
        set_status(self.status, "error", message)

    def _save(self) -> None:
        if self._active_task_id:
            return
        request = ProviderProfileInput(
            provider_name=self.provider_name.text().strip(),
            base_url=self.base_url.text().strip(),
            model_id=self.model_id.text().strip(),
            api_style=str(self.api_style.currentData()),
            vision_enabled=self.vision.isChecked(),
            key_value=self.key_input.text(),
            profile_id=self._profile.profile_id if self._profile else "desktop-default",
        )
        self.key_input.clear()
        self._set_form_enabled(False)
        set_status(self.status, "info", "正在保存模型设置…")
        self._active_task_id = self.tasks.submit(
            "保存模型设置",
            lambda: self.facade.save_provider_profile(request),
            on_success=self._saved,
            on_failure=self._save_failed,
        )

    def _saved(self, profile: ProviderProfileSummary) -> None:
        self._profiles_loaded((profile,))
        set_status(self.status, "success", "模型设置已保存；可以点击“测试连接”验证。保存不代表连接成功。")

    def _confirm_connection_test(self, profile: ProviderProfileSummary) -> bool:
        message = QMessageBox(self)
        message.setWindowTitle("确认连接测试")
        message.setIcon(QMessageBox.Icon.Question)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(
            f"向 {profile.provider_name} 发送一次固定短文本测试？\n\n"
            f"接口：{profile.base_url}\n模型：{profile.model_id}\n"
            f"格式：{profile.api_style}\n\n"
            "将使用本机已保存的密钥，可能产生少量 API 费用。\n"
            "不发送题目、教材、学生资料或图片；不会自动重试。\n"
            "服务商的数据留存政策仍适用。\n\n"
            f"本次发送的完整测试文本：\n{FIXED_SYNTHETIC_PROMPT}"
        )
        send = message.addButton("发送一次测试", QMessageBox.ButtonRole.AcceptRole)
        cancel = message.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        message.setDefaultButton(cancel)
        message.exec()
        return message.clickedButton() is send

    def _test_connection(self) -> None:
        profile = self._profile
        if self._active_task_id or self._testing or not profile:
            return
        if not profile.key_saved or not self._matches_saved():
            set_status(self.status, "attention", "请先保存当前配置和密钥，再测试连接。")
            return
        if not self._confirm_connection_test(profile):
            return
        self._testing = True
        self._probe_cancel.clear()
        self._set_form_enabled(False)
        self.stop_test_button.setVisible(True)
        self.stop_test_button.setEnabled(True)
        set_status(self.status, "info", "正在测试已保存的模型连接…仅发送固定短文本，通常约 10 秒内返回。")
        self._active_task_id = self.tasks.submit(
            "测试模型连接",
            lambda: self.facade.test_provider_connection(
                profile.profile_id,
                expected_revision=profile.revision,
                confirmed=True,
                is_cancelled=self._probe_cancel.is_set,
            ),
            on_success=self._connection_tested,
            on_failure=self._save_failed,
        )

    def _stop_test(self) -> None:
        if self._testing:
            self._probe_cancel.set()
            self.stop_test_button.setEnabled(False)
            set_status(self.status, "attention", "正在停止测试…已经发出的请求仍可能计费。")

    def _connection_tested(
        self, value: tuple[ProviderConnectionResult, ProviderProfileSummary | None]
    ) -> None:
        result, refreshed = value
        self._profiles_loaded((refreshed,) if refreshed else ())
        self._show_connection_result(result)

    def _show_connection_result(self, result: ProviderConnectionResult, *, previous: bool = False) -> None:
        detail = ("上次测试结果（非实时检测）：" if previous else "") + result.message_zh
        if result.latency_ms is not None:
            detail += f"\n耗时：{result.latency_ms} 毫秒。"
        if result.total_tokens is not None:
            detail += f" 用量：{result.total_tokens} tokens；费用以服务商账单为准。"
        set_status(self.status, "success" if result.status == "succeeded" else "attention", detail)

    def _save_failed(self, message: str) -> None:
        self.save_button.setEnabled(True)
        set_status(self.status, "error", message)

    def _task_finished(self, task_id: str) -> None:
        if task_id != self._active_task_id:
            return
        self._active_task_id = None
        self._testing = False
        self.stop_test_button.setVisible(False)
        self._set_form_enabled(True)
        self._refresh_test_enabled()

    def reject(self) -> None:
        if self._active_task_id:
            set_status(self.status, "attention")
            self.status.setText("设置仍在读取、保存或测试；可先停止测试，等待结束后再关闭。")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._active_task_id:
            set_status(self.status, "attention")
            self.status.setText("设置仍在读取、保存或测试；可先停止测试，等待结束后再关闭。")
            event.ignore()
            return
        event.accept()


__all__ = ["ImportDialog", "SettingsDialog"]
