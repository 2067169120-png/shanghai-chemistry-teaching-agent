from __future__ import annotations

"""Native, in-process student visual-analysis workflow.

Opaque identifiers and exact page hashes live only in widget item data and facade
calls.  Teacher-visible labels deliberately stay at the anonymous-profile,
page-label, status, and candidate-review layers.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDoubleValidator, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import DesktopFacadeError, DesktopWorkbenchFacade
from .components import (
    VISUAL_IMPORT_SOURCE_SUFFIXES,
    CardFrame,
    FileSelectionPanel,
    page_scroll,
    section_title,
    set_status,
)
from .student_egress_dialog import AnalysisConfirmationDialog
from .student_practice_panel import StudentPracticeDetailDialog, StudentPracticePanel
from .tasks import DesktopTaskBridge

_FILE_ROLES = (
    (
        "question_pages",
        "题目页面（必需）",
        '按题目页面顺序添加；题面模糊、裁切或缺页会保留为需要补充的信息。',
        "题目页面",
    ),
    (
        "reference_answer_pages",
        "参考答案页面（可选）",
        "仅作为教师或用户提供的参考材料；除非另有独立核验，不代表官方答案。没有可靠答案时可以留空。",
        "参考答案页面",
    ),
    (
        "student_work_pages",
        "学生作答页面（必需）",
        "只添加已匿名化的原始作答页，顺序应与实际装订顺序一致。",
        "学生作答页面",
    ),
)

_ACTIVE_STATUSES = frozenset({"queued_for_analysis", "analyzing", "cancel_requested"})
_LOSS_RESULTS = frozenset({"partial", "incorrect", "blank"})
_CONFIRMING_DECISIONS = frozenset({"accept", "edit"})

_ERROR_TYPES = (
    ("", "请选择错误类型"),
    ("concept", "概念理解"),
    ("chemical_language", "化学用语"),
    ("information_extraction", "信息提取"),
    ("model_selection", "模型选择"),
    ("quantitative", "定量计算"),
    ("experiment_design", "实验设计"),
    ("evidence_reasoning", "证据推理"),
    ("organic_route", "有机路线"),
    ("expression", "表述不完整"),
    ("careless", "审题或笔误"),
    ("time_management", "时间管理"),
    ("unclassified", "暂未分类"),
)


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _stacked_field(label_text: str, editor: QWidget) -> QWidget:
    container = QWidget()
    container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(label_text)
    label.setObjectName("MutedLabel")
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    layout.addWidget(label)
    editor.setMinimumWidth(0)
    layout.addWidget(editor)
    return container


def _clear_layout(layout: QVBoxLayout | QBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)  # type: ignore[arg-type]


def _capability_names(profile: object | None) -> set[str]:
    if profile is None:
        return set()
    raw = _field(profile, "capabilities", ())
    if isinstance(raw, Mapping):
        return {str(key).casefold() for key, enabled in raw.items() if enabled}
    if isinstance(raw, str):
        return {raw.casefold()}
    if isinstance(raw, Sequence):
        return {str(value).casefold() for value in raw}
    return set()


def _profile_is_ready(profile: object | None) -> bool:
    capabilities = _capability_names(profile)
    return bool(
        profile is not None
        and _field(profile, "key_saved", False)
        and {"vision", "structured_output"}.issubset(capabilities)
    )


class NewStudentDialog(QDialog):
    """Collect only anonymous metadata and explicit local-retention consent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建匿名学生")
        self.setModal(True)
        self.setMinimumWidth(320)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("新建匿名学生")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        warning = QLabel(
            "系统会自动生成匿名代号。不要填写或上传姓名、学号、班级名单等直接身份信息。"
        )
        warning.setWordWrap(True)
        warning.setAccessibleName("匿名学生隐私提醒")
        set_status(warning, "attention")
        root.addWidget(warning)

        self.grade = QComboBox()
        self.grade.setAccessibleName("匿名学生年级")
        for value in ("待核验", "高一", "高二", "高三"):
            self.grade.addItem(value, value)
        root.addWidget(_stacked_field("年级", self.grade))

        self.retention_days = QSpinBox()
        self.retention_days.setRange(1, 3650)
        self.retention_days.setValue(180)
        self.retention_days.setSuffix(" 天")
        self.retention_days.setAccessibleName("学生材料本机保留天数")
        root.addWidget(
            _stacked_field(
                "保留期限记录（本版本不会自动删除文件）", self.retention_days
            )
        )
        retention_help = QLabel(
            "该天数只记录教师的保留计划；本版本不会到期自动删除，请由教师按学校要求管理本机文件。"
        )
        retention_help.setWordWrap(True)
        retention_help.setObjectName("MutedLabel")
        root.addWidget(retention_help)

        self.consent = QCheckBox(
            "我已取得处理这份匿名化学生材料所需的同意，\n并确认只按教学诊断用途使用。"
        )
        self.consent.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.consent.setAccessibleName("确认学生材料处理同意")
        root.addWidget(self.consent)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.create_button.setText("创建匿名学生")
        self.create_button.setAccessibleName("确认创建匿名学生")
        self.create_button.setEnabled(False)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setAccessibleName("取消创建匿名学生")
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        self.create_button.setDefault(False)
        self.consent.toggled.connect(self.create_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)




class PagePreviewDialog(QDialog):
    def __init__(
        self, image_bytes: bytes, mime_type: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("本机页面预览")
        self.resize(780, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setAccessibleName("所选学生分析页面本机预览")
        pixmap = QPixmap()
        if mime_type.casefold().startswith("image/") and pixmap.loadFromData(
            image_bytes
        ):
            label.setPixmap(
                pixmap.scaled(
                    740,
                    650,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            label.setText("本机页面预览暂不可显示；请返回并检查源文件。")
            label.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(label)
        root.addWidget(scroll, 1)
        close_button = QPushButton("关闭预览")
        close_button.setAccessibleName("关闭学生页面预览")
        close_button.clicked.connect(self.accept)
        root.addWidget(close_button)


class MatchEditor(CardFrame):
    preview_requested = Signal(object)
    changed = Signal()

    def __init__(
        self,
        match: object,
        pages: Sequence[object],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.match = match
        self._compact = False
        self._preview_buttons: list[QPushButton] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel(str(_field(match, "label_zh", "页面匹配")))
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        question_pages = [
            page for page in pages if _field(page, "role") == "question_pages"
        ]
        reference_pages = [
            page for page in pages if _field(page, "role") == "reference_answer_pages"
        ]
        work_pages = [
            page for page in pages if _field(page, "role") == "student_work_pages"
        ]

        self.page_fields = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.page_fields.setSpacing(8)
        self.question_combo, question_control = self._page_control(
            "题目页",
            "选择对应题目页面",
            question_pages,
            _field(match, "question_page_sha256"),
            allow_empty=False,
        )
        self.work_combo, work_control = self._page_control(
            "学生作答页",
            "选择对应学生作答页面",
            work_pages,
            _field(match, "student_work_page_sha256"),
            allow_empty=False,
        )
        self.reference_combo, reference_control = self._page_control(
            "参考答案页",
            "选择对应参考答案页面",
            reference_pages,
            _field(match, "reference_answer_page_sha256"),
            allow_empty=True,
        )
        self.page_fields.addWidget(question_control, 1)
        self.page_fields.addWidget(work_control, 1)
        self.page_fields.addWidget(reference_control, 1)
        root.addLayout(self.page_fields)

        self.metadata_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.metadata_row.setSpacing(8)
        self.question_number = QLineEdit(str(_field(match, "question_number_hint", "")))
        self.question_number.setPlaceholderText("例如：12(2)")
        self.question_number.setAccessibleName("题号或小问编号")
        self.maximum_score = QDoubleSpinBox()
        self.maximum_score.setRange(0.5, 100.0)
        self.maximum_score.setDecimals(1)
        self.maximum_score.setSingleStep(0.5)
        self.maximum_score.setValue(float(_field(match, "maximum_score", 1.0) or 1.0))
        self.maximum_score.setAccessibleName("本小题最高分")
        self.maximum_score.setSuffix(" 分")
        self.metadata_row.addWidget(
            _stacked_field("题号（可修改）", self.question_number), 1
        )
        self.metadata_row.addWidget(_stacked_field("最高分", self.maximum_score), 1)
        root.addLayout(self.metadata_row)

        for editor in (
            self.question_combo,
            self.work_combo,
            self.reference_combo,
            self.question_number,
        ):
            if isinstance(editor, QComboBox):
                editor.currentIndexChanged.connect(self.changed)
            else:
                editor.textChanged.connect(self.changed)
        self.maximum_score.valueChanged.connect(self.changed)

    def _page_control(
        self,
        visible_label: str,
        accessible_name: str,
        pages: Sequence[object],
        selected_sha: str | None,
        *,
        allow_empty: bool,
    ) -> tuple[QComboBox, QWidget]:
        combo = QComboBox()
        combo.setAccessibleName(accessible_name)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if allow_empty:
            combo.addItem("不使用参考答案页", None)
        for page in pages:
            combo.addItem(str(_field(page, "label_zh", "页面")), page)
        selected_index = 0 if combo.count() else -1
        for index in range(combo.count()):
            page = combo.itemData(index)
            if page is not None and _field(page, "sha256") == selected_sha:
                selected_index = index
                break
        if selected_index >= 0:
            combo.setCurrentIndex(selected_index)

        preview = QPushButton("预览")
        preview.setObjectName("QuietButton")
        preview.setAccessibleName(f"预览{visible_label}")
        preview.clicked.connect(lambda: self._preview_combo(combo))
        self._preview_buttons.append(preview)
        control = QWidget()
        outer = QVBoxLayout(control)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        label = QLabel(visible_label)
        label.setObjectName("MutedLabel")
        label.setWordWrap(True)
        outer.addWidget(label)
        row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        row.setSpacing(6)
        row.addWidget(combo, 1)
        row.addWidget(preview)
        outer.addLayout(row)
        control._responsive_row = row  # type: ignore[attr-defined]
        return combo, control

    def _preview_combo(self, combo: QComboBox) -> None:
        page = combo.currentData()
        if page is not None:
            self.preview_requested.emit(page)

    def edit_payload(self) -> dict[str, object]:
        reference = self.reference_combo.currentData()
        question = self.question_combo.currentData()
        work = self.work_combo.currentData()
        return {
            "match_id": _field(self.match, "match_id"),
            "question_number_hint": self.question_number.text().strip(),
            "maximum_score": float(self.maximum_score.value()),
            "question_page_sha256": _field(question, "sha256"),
            "student_work_page_sha256": _field(work, "sha256"),
            "reference_answer_page_sha256": (
                _field(reference, "sha256") if reference is not None else None
            ),
        }

    def is_complete(self) -> bool:
        return bool(
            self.question_combo.currentData() is not None
            and self.work_combo.currentData() is not None
            and self.question_number.text().strip()
            and self.maximum_score.value() > 0
        )

    def set_editable(self, editable: bool) -> None:
        for editor in (
            self.question_combo,
            self.work_combo,
            self.reference_combo,
            self.question_number,
            self.maximum_score,
        ):
            editor.setEnabled(editable)
        # A confirmed match is immutable, but its source pages remain safely
        # previewable while the teacher reviews the resulting candidate.
        for button in self._preview_buttons:
            button.setEnabled(button.parentWidget() is not None)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        direction = (
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        self.page_fields.setDirection(direction)
        self.metadata_row.setDirection(direction)
        for combo in (self.question_combo, self.work_combo, self.reference_combo):
            control = combo.parentWidget()
            while control is not None and not hasattr(control, "_responsive_row"):
                control = control.parentWidget()
            if control is not None:
                control._responsive_row.setDirection(direction)  # type: ignore[attr-defined]


class ReviewItemCard(CardFrame):
    score_requested = Signal(object)
    diagnosis_requested = Signal(object)

    def __init__(
        self,
        item: object,
        sections: Sequence[object],
        *,
        diagnosis_available: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._compact = False
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title_text = str(_field(item, "label_zh", "待复核小题"))
        title = QLabel(title_text)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        maximum = float(_field(item, "maximum_score", 0.0) or 0.0)
        confidence = _field(item, "confidence")
        confidence_text = (
            '模型自报置信度未提供'
            if confidence is None
            else f"模型自报置信度 {max(0.0, min(1.0, float(confidence))) * 100:.0f}%"
        )
        metadata = QLabel(
            f"题号：{_field(item, 'question_number', '待核对')}　·　"
            f"最高 {maximum:g} 分　·　{confidence_text}（不是评分依据）"
        )
        metadata.setWordWrap(True)
        metadata.setObjectName("MutedLabel")
        root.addWidget(metadata)
        observation = str(_field(item, "observation_zh", "")).strip()
        if observation:
            observation_label = QLabel(observation)
            observation_label.setWordWrap(True)
            root.addWidget(observation_label)

        for heading, values in (
            ("化学观察", _field(item, "chemistry_observations_zh", ())),
            ("建议评分点", _field(item, "scoring_points_zh", ())),
            ("错误假设（不是结论）", _field(item, "error_hypotheses_zh", ())),
            ("页面或证据缺口", _field(item, "blockers_zh", ())),
        ):
            lines = tuple(str(value) for value in (values or ()) if str(value).strip())
            if lines:
                label = QLabel(
                    heading + "：\n" + "\n".join(f"• {line}" for line in lines)
                )
                label.setWordWrap(True)
                label.setMinimumWidth(0)
                if heading == "页面或证据缺口":
                    set_status(label, "attention")
                else:
                    label.setObjectName("MutedLabel")
                root.addWidget(label)

        suggested = _field(item, "suggested_score")
        withheld = bool(_field(item, "suggested_score_withheld", False))
        if withheld:
            suggestion_text = "模型建议分未显示：页面或证据不足，不能据此判错。"
        elif suggested is None:
            suggestion_text = "模型未提供建议分；请教师根据可见证据独立判断。"
        else:
            suggestion_text = (
                f"模型建议 {float(suggested):g} / {maximum:g} 分，仅供参考。"
            )
        self.suggestion_label = QLabel(suggestion_text)
        self.suggestion_label.setWordWrap(True)
        set_status(self.suggestion_label, "attention")
        root.addWidget(self.suggestion_label)

        latest_score = _field(item, "latest_teacher_score")
        latest_text = (
            "尚未记录教师评分。"
            if latest_score is None
            else f"最近一次教师评分：{float(latest_score):g} / {maximum:g} 分。"
        )
        self.latest_score_label = QLabel(latest_text)
        self.latest_score_label.setWordWrap(True)
        self.latest_score_label.setObjectName("MutedLabel")
        root.addWidget(self.latest_score_label)

        self.score_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.score_row.setSpacing(8)
        self.score_edit = QLineEdit()
        self.score_edit.setPlaceholderText(f"请教师输入 0–{maximum:g}")
        self.score_edit.setAccessibleName(f"{title_text}教师评分")
        validator = QDoubleValidator(0.0, maximum, 2, self.score_edit)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.score_edit.setValidator(validator)
        self.score_reason = QLineEdit()
        self.score_reason.setPlaceholderText("必填：依据可见作答说明评分理由")
        self.score_reason.setAccessibleName(f"{title_text}教师评分理由")
        self.score_row.addWidget(_stacked_field("教师评分", self.score_edit), 1)
        self.score_row.addWidget(
            _stacked_field("评分理由（必填）", self.score_reason), 2
        )
        root.addLayout(self.score_row)
        self.score_error = QLabel("")
        self.score_error.setWordWrap(True)
        self.score_error.setAccessibleName(f"{title_text}评分校验信息")
        self.score_error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.score_error.hide()
        root.addWidget(self.score_error)
        self.record_score_button = QPushButton("记录本题教师评分")
        self.record_score_button.setAccessibleName(f"记录{title_text}教师评分决定")
        self.record_score_button.clicked.connect(self._emit_score)
        root.addWidget(self.record_score_button)

        diagnosis_title = QLabel("诊断确认")
        diagnosis_title.setObjectName("CardTitle")
        root.addWidget(diagnosis_title)
        self.diagnosis_help = QLabel(
            "先记录教师评分，再确认或驳回错误诊断。图片模糊、裁切或证据不足不能直接记为学生错误。"
        )
        self.diagnosis_help.setWordWrap(True)
        self.diagnosis_help.setObjectName("MutedLabel")
        root.addWidget(self.diagnosis_help)

        self.decision = QComboBox()
        self.decision.setAccessibleName(f"{title_text}诊断处理")
        for text, value in (
            ('采纳分析建议', "accept"),
            ("教师修订", "edit"),
            ('不采纳分析建议', "reject"),
            ("暂不决定", "pending"),
        ):
            self.decision.addItem(text, value)
        self.result = QComboBox()
        self.result.setAccessibleName(f"{title_text}作答结果")
        for text, value in (
            ("正确", "correct"),
            ("部分得分", "partial"),
            ("错误", "incorrect"),
            ("空白", "blank"),
            ("不计分", "not_scored"),
        ):
            self.result.addItem(text, value)
        self.diagnosis_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.diagnosis_row.setSpacing(8)
        self.diagnosis_row.addWidget(_stacked_field('如何处理分析建议', self.decision), 1)
        self.diagnosis_row.addWidget(_stacked_field("教师确认结果", self.result), 1)
        root.addLayout(self.diagnosis_row)

        self.primary_error = QComboBox()
        self.primary_error.setAccessibleName(f"{title_text}主要错误类型")
        self.secondary_error = QComboBox()
        self.secondary_error.setAccessibleName(f"{title_text}辅助错误类型")
        for value, text in _ERROR_TYPES:
            self.primary_error.addItem(text, value or None)
            secondary_text = "不添加辅助错误类型" if not value else text
            self.secondary_error.addItem(secondary_text, value or None)
        self.section = QComboBox()
        self.section.setAccessibleName(f"{title_text}教材章节")
        self.section.addItem("请选择教材章节", None)
        for section in sections:
            self.section.addItem(
                str(_field(section, "display_label_zh", "教材章节")),
                _field(section, "section_key"),
            )
        self.error_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.error_row.setSpacing(8)
        self.error_row.addWidget(_stacked_field("主要错误类型", self.primary_error), 1)
        self.error_row.addWidget(
            _stacked_field("辅助错误类型（可选）", self.secondary_error), 1
        )
        self.error_row.addWidget(_stacked_field("教材章节", self.section), 2)
        root.addLayout(self.error_row)

        self.teacher_note = QLineEdit()
        self.teacher_note.setPlaceholderText("可选：记录反证、待观察点或后续核查")
        self.teacher_note.setAccessibleName(f"{title_text}诊断备注")
        root.addWidget(_stacked_field("教师诊断备注", self.teacher_note))
        self.diagnosis_error = QLabel("")
        self.diagnosis_error.setWordWrap(True)
        self.diagnosis_error.setAccessibleName(f"{title_text}诊断校验信息")
        self.diagnosis_error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.diagnosis_error.hide()
        root.addWidget(self.diagnosis_error)
        self.record_diagnosis_button = QPushButton("记录本题诊断决定")
        self.record_diagnosis_button.setAccessibleName(f"记录{title_text}诊断决定")
        self.record_diagnosis_button.clicked.connect(self._emit_diagnosis)
        root.addWidget(self.record_diagnosis_button)

        self.decision.currentIndexChanged.connect(self._update_diagnosis_fields)
        self.result.currentIndexChanged.connect(self._update_diagnosis_fields)
        scoring_decision = _field(item, "latest_scoring_decision_id")
        latest_diagnostic = _field(item, "latest_diagnostic_decision")
        if latest_diagnostic:
            latest_diagnostic_label = QLabel(
                f"最近一次教师诊断决定：{latest_diagnostic}。可继续追加修订决定。"
            )
            latest_diagnostic_label.setWordWrap(True)
            latest_diagnostic_label.setObjectName("MutedLabel")
            root.insertWidget(root.count() - 1, latest_diagnostic_label)
        self.record_diagnosis_button.setEnabled(bool(scoring_decision))
        if not scoring_decision:
            self.diagnosis_help.setText(
                "请先记录本题教师评分；在此之前不会写入任何错误诊断。"
            )
        if not diagnosis_available:
            self.diagnosis_help.setText(
                "当前教材目录不可用，诊断记录已安全关闭；仍可独立记录教师评分。"
            )
            for editor in (
                self.decision,
                self.result,
                self.primary_error,
                self.secondary_error,
                self.section,
                self.teacher_note,
                self.record_diagnosis_button,
            ):
                editor.setEnabled(False)
        self._update_diagnosis_fields()
        if not diagnosis_available:
            self.primary_error.setEnabled(False)
            self.secondary_error.setEnabled(False)
            self.section.setEnabled(False)

    def _show_error(self, label: QLabel, message: str) -> None:
        label.show()
        set_status(label, "error", message)
        label.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_score(self) -> None:
        raw = self.score_edit.text().strip()
        reason = self.score_reason.text().strip()
        if not raw:
            self._show_error(
                self.score_error, "请输入教师评分；不会自动采用模型建议分。"
            )
            self.score_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        try:
            score = float(raw)
        except ValueError:
            self._show_error(self.score_error, "教师评分必须是数字。")
            self.score_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        maximum = float(_field(self.item, "maximum_score", 0.0) or 0.0)
        if not 0 <= score <= maximum:
            self._show_error(
                self.score_error, f"教师评分须在 0 到 {maximum:g} 分之间。"
            )
            self.score_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if not reason:
            self._show_error(self.score_error, "请填写基于可见作答的评分理由。")
            self.score_reason.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.score_error.hide()
        self.score_requested.emit(
            {
                "match_id": _field(self.item, "match_id"),
                "teacher_score": score,
                "reason": reason,
            }
        )

    def _update_diagnosis_fields(self) -> None:
        needs_error = bool(
            self.decision.currentData() in _CONFIRMING_DECISIONS
            and self.result.currentData() in _LOSS_RESULTS
        )
        for editor in (self.primary_error, self.secondary_error, self.section):
            editor.setEnabled(needs_error)
        if not needs_error:
            self.primary_error.setCurrentIndex(0)
            self.secondary_error.setCurrentIndex(0)
            self.section.setCurrentIndex(0)

    def _emit_diagnosis(self) -> None:
        scoring_decision = _field(self.item, "latest_scoring_decision_id")
        if not scoring_decision:
            self._show_error(self.diagnosis_error, "请先记录教师评分。")
            return
        decision = str(self.decision.currentData())
        result = str(self.result.currentData())
        needs_error = decision in _CONFIRMING_DECISIONS and result in _LOSS_RESULTS
        primary = self.primary_error.currentData() if needs_error else None
        section = self.section.currentData() if needs_error else None
        secondary = self.secondary_error.currentData() if needs_error else None
        if needs_error and not primary:
            self._show_error(
                self.diagnosis_error, "部分得分、错误或空白需选择主要错误类型。"
            )
            self.primary_error.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if needs_error and not section:
            self._show_error(self.diagnosis_error, "确认失分诊断时需选择教材章节。")
            self.section.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if secondary == primary:
            secondary = None
        self.diagnosis_error.hide()
        self.diagnosis_requested.emit(
            {
                "match_id": _field(self.item, "match_id"),
                "scoring_decision_id": scoring_decision,
                "decision": decision,
                "result": result,
                "primary_error_type": primary,
                "secondary_error_types": (() if secondary is None else (secondary,)),
                "curriculum_section_keys": (() if section is None else (section,)),
                "teacher_note": self.teacher_note.text().strip(),
            }
        )

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        direction = (
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        self.score_row.setDirection(direction)
        self.diagnosis_row.setDirection(direction)
        self.error_row.setDirection(direction)


class StudentPage(QWidget):
    """Teacher-simple native workflow for one anonymous work submission."""

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
        self._view_generation = 0
        self._context_task_id: str | None = None
        self._recent_task_id: str | None = None
        self._local_task_id: str | None = None
        self._matching_task_id: str | None = None
        self._confirmation_task_id: str | None = None
        self._analysis_task_id: str | None = None
        self._cancel_task_id: str | None = None
        self._poll_task_id: str | None = None
        self._review_task_id: str | None = None
        self._review_action_task_id: str | None = None
        self._open_task_id: str | None = None
        self._practice_task_id: str | None = None
        self._practice_detail_task_id: str | None = None
        self._practice_add_task_id: str | None = None
        self._practice_preview: object | None = None
        self._practice_viewed_keys: set[str] = set()
        self._practice_dialog: StudentPracticeDetailDialog | None = None
        self._practice_review_dirty = False
        self._current_summary: object | None = None
        self._current_review: object | None = None
        self._student_profiles: tuple[object, ...] = ()
        self._provider_profiles: tuple[object, ...] = ()
        self._sections: tuple[object, ...] = ()
        self.match_editors: list[MatchEditor] = []
        self.review_editors: list[ReviewItemCard] = []
        self._recent_rows: list[QBoxLayout] = []

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(28, 24, 28, 32)
        self.content_layout.setSpacing(18)
        self.content_layout.addWidget(
            section_title(
                "学生分析",
                "先在本机保存匿名化题目与作答页面，再由教师确认匹配、模型发送范围和候选诊断。看不清不会直接判错。",
            )
        )

        self.status = QLabel("正在读取本机学生与最近任务…")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("学生分析操作状态")
        set_status(self.status, "info")
        self.content_layout.addWidget(self.status)
        self.curriculum_status = QLabel("")
        self.curriculum_status.setWordWrap(True)
        self.curriculum_status.setAccessibleName("学生诊断教材目录状态")
        self.curriculum_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.curriculum_status.hide()
        self.content_layout.addWidget(self.curriculum_status)

        self.identity_card = CardFrame()
        identity_layout = QVBoxLayout(self.identity_card)
        identity_layout.setContentsMargins(18, 16, 18, 16)
        identity_layout.setSpacing(10)
        identity_title = QLabel("1　选择匿名学生")
        identity_title.setObjectName("CardTitle")
        identity_layout.addWidget(identity_title)
        privacy = QLabel(
            "只使用系统生成的匿名代号；不要输入姓名、学号、手机号或班级名单。"
        )
        privacy.setWordWrap(True)
        privacy.setAccessibleName("学生身份匿名化提醒")
        set_status(privacy, "attention")
        identity_layout.addWidget(privacy)
        self.identity_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.identity_row.setSpacing(8)
        self.student_combo = QComboBox()
        self.student_combo.setAccessibleName("选择匿名学生")
        self.student_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.student_combo.currentIndexChanged.connect(self._student_changed)
        self.new_student_button = QPushButton("新建匿名学生")
        self.new_student_button.setAccessibleName("新建匿名学生资料")
        self.new_student_button.clicked.connect(self._new_student)
        self.identity_row.addWidget(_stacked_field("匿名学生", self.student_combo), 1)
        self.identity_row.addWidget(self.new_student_button)
        identity_layout.addLayout(self.identity_row)
        self.content_layout.addWidget(self.identity_card)

        self.files_heading = QLabel("2　本机材料")
        self.files_heading.setObjectName("CardTitle")
        self.content_layout.addWidget(self.files_heading)
        self.file_panels: dict[str, FileSelectionPanel] = {}
        for role, title, hint, accessible_name in _FILE_ROLES:
            panel = FileSelectionPanel(
                hint,
                title=title,
                supported_suffixes=VISUAL_IMPORT_SOURCE_SUFFIXES,
                allow_reordering=True,
                accessible_name=accessible_name,
            )
            panel.files_changed.connect(lambda role=role: self._files_changed(role))
            self.file_panels[role] = panel
            self.content_layout.addWidget(panel)
        self.question_files = self.file_panels["question_pages"]
        self.reference_files = self.file_panels["reference_answer_pages"]
        self.student_files = self.file_panels["student_work_pages"]

        self.local_card = CardFrame()
        local_layout = QVBoxLayout(self.local_card)
        local_layout.setContentsMargins(18, 16, 18, 16)
        local_layout.setSpacing(10)
        self.materials_status = QLabel(
            "题目页面和学生作答页面为必需；参考答案可以留空。此步不调用模型。"
        )
        self.materials_status.setWordWrap(True)
        self.materials_status.setAccessibleName("学生材料本机保存状态")
        self.materials_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.materials_status.setObjectName("MutedLabel")
        local_layout.addWidget(self.materials_status)
        self.local_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.local_actions.setSpacing(8)
        self.prepare_button = QPushButton("仅保存到本机并准备页面")
        self.prepare_button.setObjectName("PrimaryAction")
        self.prepare_button.setAccessibleName("仅在本机保存学生材料并准备页面")
        self.prepare_button.clicked.connect(self._prepare_locally)
        self.new_submission_button = QPushButton("新建一次分析")
        self.new_submission_button.setObjectName("QuietButton")
        self.new_submission_button.setAccessibleName("新建一次独立学生分析")
        self.new_submission_button.clicked.connect(lambda: self._reset_submission())
        self.new_submission_button.hide()
        self.local_actions.addWidget(self.prepare_button)
        self.local_actions.addWidget(self.new_submission_button)
        self.local_actions.addStretch(1)
        local_layout.addLayout(self.local_actions)
        self.content_layout.addWidget(self.local_card)

        self.matching_card = CardFrame()
        matching_layout = QVBoxLayout(self.matching_card)
        matching_layout.setContentsMargins(18, 16, 18, 16)
        matching_layout.setSpacing(10)
        matching_title = QLabel("3　确认页面与临时作答单元配对")
        matching_title.setObjectName("CardTitle")
        matching_layout.addWidget(matching_title)
        matching_help = QLabel(
            "系统初始按每张学生作答页生成一个临时匹配，不代表正式小题切分。"
            "请逐项核对题目页、作答页、题号和最高分；低置信或缺页必须由教师修正。"
        )
        matching_help.setWordWrap(True)
        matching_help.setObjectName("MutedLabel")
        matching_layout.addWidget(matching_help)
        self.matching_status = QLabel("等待本机页面准备完成。")
        self.matching_status.setWordWrap(True)
        self.matching_status.setAccessibleName("学生页面匹配状态")
        self.matching_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.matching_status.setObjectName("MutedLabel")
        matching_layout.addWidget(self.matching_status)
        self.match_items = QVBoxLayout()
        self.match_items.setSpacing(10)
        matching_layout.addLayout(self.match_items)
        self.confirm_matching_button = QPushButton("确认页面匹配")
        self.confirm_matching_button.setAccessibleName("确认所有学生分析页面匹配")
        self.confirm_matching_button.clicked.connect(self._confirm_matching)
        matching_layout.addWidget(self.confirm_matching_button)
        self.matching_card.hide()
        self.content_layout.addWidget(self.matching_card)

        self.model_card = CardFrame()
        model_layout = QVBoxLayout(self.model_card)
        model_layout.setContentsMargins(18, 16, 18, 16)
        model_layout.setSpacing(10)
        model_title = QLabel("4　选择模型并确认发送")
        model_title.setObjectName("CardTitle")
        model_layout.addWidget(model_title)
        self.model_status = QLabel("正在读取视觉模型设置…")
        self.model_status.setWordWrap(True)
        self.model_status.setAccessibleName("学生视觉分析模型就绪状态")
        self.model_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.model_status.setObjectName("MutedLabel")
        model_layout.addWidget(self.model_status)
        self.model_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.model_row.setSpacing(8)
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("选择学生视觉分析模型")
        self.profile_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.analyze_button = QPushButton("确认后开始视觉分析")
        self.analyze_button.setObjectName("PrimaryAction")
        self.analyze_button.setAccessibleName("确认隐私与费用后开始学生视觉分析")
        self.analyze_button.clicked.connect(self._prepare_analysis_confirmation)
        self.model_row.addWidget(_stacked_field("视觉模型", self.profile_combo), 1)
        self.model_row.addWidget(self.analyze_button)
        model_layout.addLayout(self.model_row)
        self.model_card.hide()
        self.content_layout.addWidget(self.model_card)

        self.progress_card = CardFrame()
        progress_layout = QVBoxLayout(self.progress_card)
        progress_layout.setContentsMargins(18, 16, 18, 16)
        progress_layout.setSpacing(10)
        progress_title = QLabel("5　分析任务")
        progress_title.setObjectName("CardTitle")
        progress_layout.addWidget(progress_title)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName("学生视觉分析任务进度")
        progress_layout.addWidget(self.progress)
        self.progress_message = QLabel("任务尚未开始。")
        self.progress_message.setWordWrap(True)
        self.progress_message.setAccessibleName("学生视觉分析当前阶段")
        self.progress_message.setObjectName("MutedLabel")
        progress_layout.addWidget(self.progress_message)
        self.progress_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.progress_actions.setSpacing(8)
        self.stop_button = QPushButton("停止分析")
        self.stop_button.setObjectName("QuietButton")
        self.stop_button.setAccessibleName("停止当前学生视觉分析")
        self.stop_button.clicked.connect(self._cancel_analysis)
        self.progress_actions.addWidget(self.stop_button)
        self.progress_actions.addStretch(1)
        progress_layout.addLayout(self.progress_actions)
        self.progress_card.hide()
        self.content_layout.addWidget(self.progress_card)

        self.review_card = CardFrame()
        review_layout = QVBoxLayout(self.review_card)
        review_layout.setContentsMargins(18, 16, 18, 16)
        review_layout.setSpacing(10)
        review_title = QLabel('6\u3000分析结果与教师确认')
        review_title.setObjectName("CardTitle")
        review_layout.addWidget(review_title)
        self.candidate_boundary = QLabel(
            "AI 结果只是候选，必须由教师按当前临时匹配项复核；不会自动写入长期学情、掌握度或推荐。"
        )
        self.candidate_boundary.setWordWrap(True)
        self.candidate_boundary.setAccessibleName("学生分析候选边界")
        set_status(self.candidate_boundary, "attention")
        review_layout.addWidget(self.candidate_boundary)
        self.review_status = QLabel('正在读取教师确认记录…')
        self.review_status.setWordWrap(True)
        self.review_status.setAccessibleName("学生分析教师复核状态")
        self.review_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.review_status.setObjectName("MutedLabel")
        review_layout.addWidget(self.review_status)
        self.reload_review_button = QPushButton("重新读取复核状态")
        self.reload_review_button.setObjectName("QuietButton")
        self.reload_review_button.setAccessibleName("重新读取学生分析教师复核状态")
        self.reload_review_button.clicked.connect(self._load_review)
        review_layout.addWidget(self.reload_review_button)
        self.review_items = QVBoxLayout()
        self.review_items.setSpacing(10)
        review_layout.addLayout(self.review_items)
        self.review_card.hide()
        self.content_layout.addWidget(self.review_card)

        self.practice_panel = StudentPracticePanel()
        self.practice_panel.recommend_requested.connect(self._recommend_practice)
        self.practice_panel.view_requested.connect(self._view_practice_theme)
        self.practice_panel.add_requested.connect(self._add_practice_theme)
        self.practice_panel.hide()
        self.content_layout.addWidget(self.practice_panel)

        self.recent_card = CardFrame()
        recent_layout = QVBoxLayout(self.recent_card)
        recent_layout.setContentsMargins(18, 16, 18, 16)
        recent_layout.setSpacing(10)
        recent_title = QLabel("最近任务")
        recent_title.setObjectName("CardTitle")
        recent_layout.addWidget(recent_title)
        self.recent_status = QLabel("选择匿名学生后读取最近任务。")
        self.recent_status.setWordWrap(True)
        self.recent_status.setAccessibleName("学生分析最近任务状态")
        self.recent_status.setObjectName("MutedLabel")
        recent_layout.addWidget(self.recent_status)
        self.recent_items = QVBoxLayout()
        self.recent_items.setSpacing(8)
        recent_layout.addLayout(self.recent_items)
        self.content_layout.addWidget(self.recent_card)
        self.content_layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.review_desk_button = QPushButton("打开作答与评分（同屏批改）")
        self.review_desk_button.setAccessibleName("打开原作答与教师评分同屏工作区")
        self.review_desk_button.setToolTip("先打开一份已有分析结果；同屏查看原页、评分点和教师修正，不调用模型。")
        self.review_desk_button.setEnabled(False)
        self.review_desk_button.clicked.connect(self._open_review_desk)
        review_actions = QHBoxLayout()
        review_actions.addWidget(self.review_desk_button, 1)
        self.work_batch_button = QPushButton("作业批次")
        self.work_batch_button.setAccessibleName("管理作业批次并逐人复核")
        self.work_batch_button.clicked.connect(self._open_work_batch)
        review_actions.addWidget(self.work_batch_button)
        outer.addLayout(review_actions)
        self.scroll = page_scroll(content)
        outer.addWidget(self.scroll)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(900)
        self.poll_timer.timeout.connect(self._poll_submission)
        self._update_prepare_enabled()
        QTimer.singleShot(0, self._load_context)

    def _load_context(self) -> None:
        if self._context_task_id is not None:
            return

        def operation() -> dict[str, object]:
            students = tuple(self.facade.student_profiles())
            profiles = tuple(self.facade.student_analysis_profiles())
            try:
                sections = tuple(self.facade.student_curriculum_sections())
                sections_error = ""
            except DesktopFacadeError as exc:
                candidate = getattr(exc, "message_zh", None)
                sections_error = (
                    candidate.strip()
                    if isinstance(candidate, str) and candidate.strip()
                    else "当前教材目录暂时不可用。"
                )
                sections = ()
            return {
                "students": students,
                "profiles": profiles,
                "sections": sections,
                "sections_error": sections_error,
            }

        self._context_task_id = self.tasks.submit(
            "读取学生分析本机状态",
            operation,
            on_success=self._context_loaded,
            on_failure=self._context_failed,
        )

    def _context_loaded(self, payload: Mapping[str, object]) -> None:
        self._context_task_id = None
        self._student_profiles = tuple(payload.get("students", ()))
        self._provider_profiles = tuple(payload.get("profiles", ()))
        self._sections = tuple(payload.get("sections", ()))
        sections_error = str(payload.get("sections_error", "")).strip()
        if sections_error:
            self.curriculum_status.show()
            set_status(
                self.curriculum_status,
                "attention",
                sections_error
                + " 学生材料仍可保存和分析，但教材章节诊断记录已安全关闭。",
            )
        else:
            self.curriculum_status.hide()
        self.student_combo.blockSignals(True)
        self.student_combo.clear()
        for profile in self._student_profiles:
            label = str(_field(profile, "label_zh", "匿名学生"))
            grade = str(_field(profile, "grade", "待核验"))
            self.student_combo.addItem(f"{label} · {grade}", profile)
        self.student_combo.blockSignals(False)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._provider_profiles:
            provider = str(_field(profile, "provider_name", "视觉模型"))
            model = str(_field(profile, "model_id", ""))
            self.profile_combo.addItem(
                f"{provider} · {model}" if model else provider,
                profile,
            )
        self.profile_combo.blockSignals(False)
        self._profile_changed()
        if self.student_combo.count():
            set_status(self.status, "success", "已读取匿名学生；可开始准备一份作答。")
            self._student_changed()
        else:
            set_status(
                self.status,
                "attention",
                "尚无匿名学生。请先新建匿名资料，且不要录入姓名或学号。",
            )
            self.recent_status.setText("尚无匿名学生。")
        self._update_prepare_enabled()

    def _context_failed(self, message: str) -> None:
        self._context_task_id = None
        set_status(self.status, "error", message)
        self._update_prepare_enabled()

    def _new_student(self) -> None:
        dialog = NewStudentDialog(self)
        if (
            dialog.exec() != QDialog.DialogCode.Accepted
            or not dialog.consent.isChecked()
        ):
            set_status(self.status, "attention", "未创建匿名学生；没有保存任何新资料。")
            return
        self.new_student_button.setEnabled(False)
        set_status(self.status, "info", "正在本机创建匿名学生资料…")
        self.tasks.submit(
            "创建匿名学生",
            lambda: self.facade.create_student_profile(
                grade=str(dialog.grade.currentData()),
                retention_days=int(dialog.retention_days.value()),
                consent_recorded=True,
            ),
            on_success=self._student_created,
            on_failure=self._student_create_failed,
        )

    def _student_created(self, profile: object) -> None:
        self.new_student_button.setEnabled(True)
        self._student_profiles = (*self._student_profiles, profile)
        self.student_combo.addItem(
            f"{_field(profile, 'label_zh', '匿名学生')} · {_field(profile, 'grade', '待核验')}",
            profile,
        )
        self.student_combo.setCurrentIndex(self.student_combo.count() - 1)
        set_status(self.status, "success", "匿名学生已在本机创建。")
        self._update_prepare_enabled()

    def _student_create_failed(self, message: str) -> None:
        self.new_student_button.setEnabled(True)
        set_status(self.status, "error", message)

    def _student_changed(self) -> None:
        # File selections are scoped to the selected anonymous student.  Never
        # carry prior paths across an identity change, even before local copy.
        self._reset_submission(clear_files=True)
        self._render_recent(())
        self._load_recent()
        self._update_prepare_enabled()

    def _selected_student(self) -> object | None:
        return self.student_combo.currentData()

    def _selected_profile(self) -> object | None:
        return self.profile_combo.currentData()

    def _advance_view_generation(self) -> int:
        """Invalidate callbacks belonging to the previously displayed task."""

        self._view_generation += 1
        return self._view_generation

    def _view_matches(
        self,
        generation: int,
        *,
        student_id: str | None = None,
        submission_id: str | None = None,
    ) -> bool:
        if generation != self._view_generation:
            return False
        if student_id is not None:
            student = self._selected_student()
            if student is None or str(_field(student, "student_id")) != student_id:
                return False
        if submission_id is not None:
            summary = self._current_summary
            if (
                summary is None
                or str(_field(summary, "submission_id")) != submission_id
            ):
                return False
        return True

    def _submit_view_task(
        self,
        label: str,
        operation: Callable[[], object],
        *,
        task_attribute: str,
        generation: int,
        on_success: Callable[[object], None],
        on_failure: Callable[[str], None],
        student_id: str | None = None,
        submission_id: str | None = None,
    ) -> str:
        """Submit a task whose result may mutate only its originating view.

        The task id check matters when a student switch immediately starts a
        replacement request using the same task slot.  A late callback from
        the former request must neither clear nor repaint the replacement.
        """

        task_ref: dict[str, Any] = {}

        def is_current_task() -> bool:
            task_id = task_ref.get("task_id")
            return bool(
                task_id
                and getattr(self, task_attribute) == task_id
                and self._view_matches(
                    generation,
                    student_id=student_id,
                    submission_id=submission_id,
                )
            )

        def succeeded(value: object) -> None:
            if "task_id" not in task_ref:
                task_ref["immediate_result"] = (True, value)
                return
            if not is_current_task():
                return
            if (
                student_id is not None
                and str(_field(value, "student_id", student_id)) != student_id
            ):
                setattr(self, task_attribute, None)
                return
            if (
                submission_id is not None
                and str(_field(value, "submission_id", submission_id)) != submission_id
            ):
                setattr(self, task_attribute, None)
                return
            on_success(value)

        def failed(message: str) -> None:
            if "task_id" not in task_ref:
                task_ref["immediate_result"] = (False, message)
                return
            if is_current_task():
                on_failure(message)

        task_id = self.tasks.submit(
            label,
            operation,
            on_success=succeeded,
            on_failure=failed,
        )
        task_ref["task_id"] = task_id
        setattr(self, task_attribute, task_id)
        # Small local/injected bridges may finish inside submit(). Register
        # the same task/view guards before delivering that synchronous result.
        immediate = task_ref.pop("immediate_result", None)
        if immediate is not None:
            if immediate[0]:
                succeeded(immediate[1])
            else:
                failed(immediate[1])
        return task_id

    def _profile_changed(self) -> None:
        profile = self._selected_profile()
        if _profile_is_ready(profile):
            set_status(
                self.model_status,
                "success",
                "所选模型已保存 Key，并声明支持图片理解与结构化输出。发送前仍需再次确认隐私和费用。",
            )
        elif profile is None:
            set_status(
                self.model_status,
                "attention",
                "尚无可选视觉模型。请先到设置保存 Key，并配置图片理解与结构化输出能力。",
            )
        else:
            set_status(
                self.model_status,
                "attention",
                "所选模型尚未同时满足已保存 Key、图片理解和结构化输出；请到设置修复。",
            )
        self._update_analysis_button()

    def _files_changed(self, role: str) -> None:
        panel = self.file_panels[role]
        set_status(panel.selection_label, "info", panel.selection_label.text())
        self._update_prepare_enabled()

    def _update_prepare_enabled(self) -> None:
        self.prepare_button.setEnabled(
            self._selected_student() is not None
            and self._current_summary is None
            and self._local_task_id is None
        )

    def _prepare_locally(self) -> None:
        student = self._selected_student()
        missing: list[str] = []
        if not self.question_files.paths():
            missing.append("题目页面")
            set_status(
                self.question_files.selection_label, "error", "请至少添加一个题目文件。"
            )
        if not self.student_files.paths():
            missing.append("学生作答页面")
            set_status(
                self.student_files.selection_label,
                "error",
                "请至少添加一个已匿名化的学生作答文件。",
            )
        if student is None:
            missing.append("匿名学生")
        if missing:
            set_status(
                self.materials_status,
                "error",
                "尚不能准备：缺少" + "、".join(missing) + "。",
            )
            self.materials_status.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        student_id = str(_field(student, "student_id"))
        role_files = tuple(
            (role, tuple(self.file_panels[role].paths())) for role, *_ in _FILE_ROLES
        )
        self._set_intake_enabled(False)
        self.prepare_button.setEnabled(False)
        set_status(
            self.materials_status,
            "info",
            "正在复制并准备本机页面；此步不会调用模型…",
        )

        def operation() -> object:
            summary = self.facade.create_student_submission(student_id)
            for role, paths in role_files:
                if not paths:
                    continue
                summary = self.facade.add_student_submission_files(
                    student_id=student_id,
                    submission_id=str(_field(summary, "submission_id")),
                    role=role,
                    files=paths,
                    expected_revision=str(_field(summary, "revision")),
                )
            return summary

        generation = self._view_generation
        self._submit_view_task(
            "在本机准备学生页面",
            operation,
            task_attribute="_local_task_id",
            generation=generation,
            student_id=student_id,
            on_success=self._local_prepared,
            on_failure=self._local_prepare_failed,
        )

    def _local_prepared(self, summary: object) -> None:
        self._local_task_id = None
        set_status(
            self.materials_status,
            "success",
            "材料已仅保存到本机。请逐项核对页面匹配。",
        )
        self._render_submission(summary)
        self._load_recent()

    def _local_prepare_failed(self, message: str) -> None:
        self._local_task_id = None
        self._set_intake_enabled(True)
        self.files_heading.show()
        self.prepare_button.show()
        for panel in self.file_panels.values():
            panel.show()
        set_status(self.materials_status, "error", message)
        self.materials_status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_prepare_enabled()

    def _set_intake_enabled(self, enabled: bool) -> None:
        self.student_combo.setEnabled(enabled)
        self.new_student_button.setEnabled(enabled)
        for panel in self.file_panels.values():
            panel.setEnabled(enabled)

    def _reset_submission(self, *, clear_files: bool = True) -> None:
        self._advance_view_generation()
        self._invalidate_practice()
        self._practice_review_dirty = False
        self.practice_panel.hide()
        self.poll_timer.stop()
        # The workers may still finish cooperatively.  Release their UI task
        # slots now; task-id plus generation checks prevent those late results
        # from clearing or repainting any replacement request.
        for task_attribute in (
            "_local_task_id",
            "_matching_task_id",
            "_confirmation_task_id",
            "_analysis_task_id",
            "_poll_task_id",
            "_cancel_task_id",
            "_recent_task_id",
            "_open_task_id",
            "_review_task_id",
            "_review_action_task_id",
        ):
            setattr(self, task_attribute, None)
        self._current_summary = None
        self._current_review = None
        self.review_desk_button.setEnabled(False)
        self._set_intake_enabled(True)
        self.files_heading.show()
        self.prepare_button.show()
        for panel in self.file_panels.values():
            panel.show()
        if clear_files:
            for panel in self.file_panels.values():
                panel.file_list.clear_paths()
        self.matching_card.hide()
        self.model_card.hide()
        self.progress_card.hide()
        self.review_card.hide()
        self.new_submission_button.hide()
        _clear_layout(self.match_items)
        _clear_layout(self.review_items)
        self.match_editors.clear()
        self.review_editors.clear()
        set_status(
            self.materials_status,
            "info",
            "题目页面和学生作答页面为必需；参考答案可以留空。此步不调用模型。",
        )
        self._update_prepare_enabled()

    def _render_submission(self, summary: object) -> None:
        if self._current_summary is not None and (
            _field(self._current_summary, "submission_id") != _field(summary, "submission_id")
            or _field(self._current_summary, "revision") != _field(summary, "revision")
        ):
            self._invalidate_practice("分析记录已更新，请按当前复核结果重新推荐。")
        self._current_summary = summary
        self._set_intake_enabled(False)
        # Once copied into the private submission store, collapse the large
        # source baskets.  Confirmed page previews remain available below.
        self.files_heading.hide()
        self.prepare_button.hide()
        for panel in self.file_panels.values():
            panel.hide()
        self.new_submission_button.show()
        status = str(_field(summary, "status", ""))
        status_zh = str(_field(summary, "status_zh", "学生分析任务"))
        message = str(_field(summary, "message_zh", ""))
        semantic = (
            "error"
            if status in {"analysis_failed", "upload_processing_failed"}
            else "attention"
            if status in {"awaiting_visual_provider", "cancel_requested", "cancelled"}
            else "success"
            if bool(_field(summary, "candidate_available", False))
            else "info"
        )
        set_status(self.status, semantic, f"{status_zh}。{message}".strip("。"))
        self._render_matching(summary)
        self.model_card.setVisible(
            bool(_field(summary, "matches", ()))
            and not bool(_field(summary, "candidate_available", False))
        )
        self._update_analysis_button()

        active = status in _ACTIVE_STATUSES
        show_progress = active or status in {
            "analysis_failed",
            "awaiting_visual_provider",
            "cancelled",
            "awaiting_teacher_review",
        }
        self.progress_card.setVisible(show_progress)
        if active:
            self.progress.setRange(0, 0)
            self.progress_message.setText(f"{status_zh}。{message}".strip("。"))
            self.stop_button.setEnabled(bool(_field(summary, "can_cancel", False)))
            if status == "cancel_requested":
                self.stop_button.setEnabled(False)
            self.poll_timer.start()
        else:
            self.poll_timer.stop()
            self.progress.setRange(0, 100)
            self.progress.setValue(
                100 if bool(_field(summary, "candidate_available", False)) else 0
            )
            progress_text = f"{status_zh}。{message}".strip("。")
            if status == "analysis_failed":
                progress_text += " 系统不会自动重试。"
            self.progress_message.setText(progress_text)
            self.stop_button.setEnabled(False)
        if status == "cancelled":
            self.analyze_button.setEnabled(False)
            self.analyze_button.setText("此任务已取消")
            self.new_submission_button.setText("新建一次分析")
        else:
            self.new_submission_button.setText("新建一次分析")
        if bool(_field(summary, "candidate_available", False)):
            self.review_card.show()
            self._load_review()
        else:
            self.review_card.hide()
            self.practice_panel.hide()

    def _render_matching(self, summary: object) -> None:
        matches = tuple(_field(summary, "matches", ()) or ())
        pages = tuple(_field(summary, "pages", ()) or ())
        _clear_layout(self.match_items)
        self.match_editors.clear()
        self.matching_card.setVisible(bool(matches))
        if not matches:
            return
        editable = bool(_field(summary, "can_confirm_matching", False))
        for match in matches:
            editor = MatchEditor(match, pages)
            editor.set_editable(editable)
            editor.set_compact(self._compact)
            editor.preview_requested.connect(self._preview_page)
            editor.changed.connect(self._update_matching_enabled)
            self.match_items.addWidget(editor)
            self.match_editors.append(editor)
        if editable:
            set_status(
                self.matching_status,
                "attention",
                "请核对每项匹配；确认后再选择模型。",
            )
            self.confirm_matching_button.setText("确认页面匹配")
            self.confirm_matching_button.show()
        else:
            set_status(
                self.matching_status,
                "success",
                "页面匹配已确认，或当前任务已不允许修改。",
            )
            self.confirm_matching_button.hide()
        self._update_matching_enabled()

    def _update_matching_enabled(self) -> None:
        can_confirm = bool(
            self._current_summary is not None
            and _field(self._current_summary, "can_confirm_matching", False)
        )
        self.confirm_matching_button.setEnabled(
            can_confirm
            and self._matching_task_id is None
            and bool(self.match_editors)
            and all(editor.is_complete() for editor in self.match_editors)
        )

    def _confirm_matching(self) -> None:
        summary = self._current_summary
        student = self._selected_student()
        if (
            summary is None
            or student is None
            or not all(editor.is_complete() for editor in self.match_editors)
        ):
            set_status(
                self.matching_status, "error", "请先补全每项页面、题号和最高分。"
            )
            self.matching_status.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.confirm_matching_button.setEnabled(False)
        edits = tuple(editor.edit_payload() for editor in self.match_editors)
        set_status(self.matching_status, "info", "正在本机保存页面匹配…")
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        self._submit_view_task(
            "确认学生页面匹配",
            lambda: self.facade.confirm_student_matching(
                student_id=student_id,
                submission_id=submission_id,
                expected_revision=str(_field(summary, "revision")),
                edits=edits,
            ),
            task_attribute="_matching_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._matching_confirmed,
            on_failure=self._matching_failed,
        )

    def _matching_confirmed(self, summary: object) -> None:
        self._matching_task_id = None
        self._render_submission(summary)
        set_status(
            self.matching_status,
            "success",
            "页面匹配已由教师确认。下一步选择模型并确认发送范围。",
        )
        self._load_recent()

    def _matching_failed(self, message: str) -> None:
        self._matching_task_id = None
        set_status(self.matching_status, "error", message)
        self.matching_status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_matching_enabled()

    def _preview_page(self, page: object) -> None:
        summary = self._current_summary
        student = self._selected_student()
        if summary is None or student is None:
            return
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        set_status(self.matching_status, "info", "正在读取本机页面预览…")

        def loaded(payload: object) -> None:
            if not self._view_matches(
                generation,
                student_id=student_id,
                submission_id=submission_id,
            ):
                return
            if not isinstance(payload, tuple) or len(payload) != 2:
                set_status(self.matching_status, "error", "本机页面预览返回格式无效。")
                return
            image_bytes, mime_type = payload
            dialog = PagePreviewDialog(bytes(image_bytes), str(mime_type), self)
            dialog.exec()
            set_status(
                self.matching_status, "success", "页面预览已关闭，可继续核对匹配。"
            )

        self.tasks.submit(
            "读取学生页面预览",
            lambda: self.facade.student_submission_page(
                student_id=student_id,
                submission_id=submission_id,
                file_id=str(_field(page, "file_id")),
                page_sha256=str(_field(page, "sha256")),
            ),
            on_success=loaded,
            on_failure=lambda message: (
                set_status(self.matching_status, "error", message)
                if self._view_matches(
                    generation,
                    student_id=student_id,
                    submission_id=submission_id,
                )
                else None
            ),
        )

    def _update_analysis_button(self) -> None:
        summary = self._current_summary
        status = str(_field(summary, "status", "")) if summary is not None else ""
        retryable = bool(_field(summary, "can_retry", False)) if summary else False
        can_start = bool(_field(summary, "can_analyze", False)) if summary else False
        if retryable and status != "cancelled":
            self.analyze_button.setText("重新确认并重试分析")
            self.analyze_button.setAccessibleName("重新确认隐私与费用后重试学生分析")
        else:
            self.analyze_button.setText("确认后开始视觉分析")
            self.analyze_button.setAccessibleName("确认隐私与费用后开始学生视觉分析")
        self.analyze_button.setEnabled(
            status != "cancelled"
            and (can_start or retryable)
            and _profile_is_ready(self._selected_profile())
            and self._confirmation_task_id is None
            and self._analysis_task_id is None
        )

    def _prepare_analysis_confirmation(self) -> None:
        summary = self._current_summary
        student = self._selected_student()
        profile = self._selected_profile()
        if summary is None or student is None or not _profile_is_ready(profile):
            set_status(
                self.model_status,
                "error",
                "尚不能分析：请先确认页面匹配并选择已就绪的视觉模型。",
            )
            self.model_status.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.analyze_button.setEnabled(False)
        set_status(self.model_status, "info", '正在整理本次要发送的页面…')
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        self._submit_view_task(
            "准备学生页面发送确认",
            lambda: self.facade.prepare_student_analysis_confirmation(
                student_id=student_id,
                submission_id=submission_id,
                profile_id=str(_field(profile, "profile_id")),
            ),
            task_attribute="_confirmation_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._confirmation_prepared,
            on_failure=self._confirmation_failed,
        )

    def _confirmation_prepared(self, confirmation: object) -> None:
        self._confirmation_task_id = None
        pages = tuple(_field(confirmation, "pages", ()))
        page_map = {_field(page, "sha256"): page for page in pages}

        def load_page(page_sha256):
            page = page_map[page_sha256]
            return self.facade.student_submission_page(
                student_id=str(_field(confirmation, "student_id")),
                submission_id=str(_field(confirmation, "submission_id")),
                file_id=str(_field(page, "file_id")),
                page_sha256=page_sha256,
            )

        dialog = AnalysisConfirmationDialog(confirmation, self, image_loader=load_page)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        identifiers_clear = dialog.identifiers_clear.isChecked()
        egress_confirmed = dialog.egress_confirmed.isChecked()
        if not accepted or not dialog._ready or not (identifiers_clear and egress_confirmed):
            set_status(
                self.model_status,
                "attention",
                "未发送任何页面。图片须完整显示，并勾选两项确认后才会调用模型。",
            )
            self._update_analysis_button()
            return
        self._start_analysis(
            confirmation,
            identifiers_clear=identifiers_clear,
            egress_confirmed=egress_confirmed,
        )

    def _confirmation_failed(self, message: str) -> None:
        self._confirmation_task_id = None
        set_status(self.model_status, "error", message)
        self.model_status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_analysis_button()

    def _start_analysis(
        self,
        confirmation: object,
        *,
        identifiers_clear: bool,
        egress_confirmed: bool,
    ) -> None:
        student = self._selected_student()
        if student is None or not (identifiers_clear and egress_confirmed):
            return
        self.progress_card.show()
        self.progress.setRange(0, 0)
        self.progress_message.setText("正在提交视觉分析；不会自动重试。")
        self.stop_button.setEnabled(False)
        set_status(self.model_status, "info", "已确认发送范围，正在启动分析…")
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(confirmation, "submission_id"))
        self._submit_view_task(
            "启动学生视觉分析",
            lambda: self.facade.start_student_analysis(
                student_id=student_id,
                confirmation=confirmation,
                identifiers_clear=True,
                student_page_egress_confirmed=True,
            ),
            task_attribute="_analysis_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._analysis_started,
            on_failure=self._analysis_failed,
        )

    def _analysis_started(self, summary: object) -> None:
        self._analysis_task_id = None
        self._render_submission(summary)
        self._load_recent()

    def _analysis_failed(self, message: str) -> None:
        self._analysis_task_id = None
        self.poll_timer.stop()
        self.progress_card.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        set_status(self.progress_message, "error", message + " 系统不会自动重试。")
        self._update_analysis_button()

    def _poll_submission(self) -> None:
        summary = self._current_summary
        student = self._selected_student()
        if (
            summary is None
            or student is None
            or str(_field(summary, "status", "")) not in _ACTIVE_STATUSES
            or self._poll_task_id is not None
        ):
            if (
                summary is None
                or str(_field(summary, "status", "")) not in _ACTIVE_STATUSES
            ):
                self.poll_timer.stop()
            return
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        self._submit_view_task(
            "刷新学生分析状态",
            lambda: self.facade.student_submission(
                student_id=student_id,
                submission_id=submission_id,
            ),
            task_attribute="_poll_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._poll_succeeded,
            on_failure=self._poll_failed,
        )

    def _poll_succeeded(self, summary: object) -> None:
        self._poll_task_id = None
        self._render_submission(summary)
        if str(_field(summary, "status", "")) not in _ACTIVE_STATUSES:
            self._load_recent()

    def _poll_failed(self, message: str) -> None:
        self._poll_task_id = None
        self.poll_timer.stop()
        set_status(
            self.progress_message,
            "error",
            message + " 已停止自动刷新；系统不会自动重试分析。",
        )

    def _cancel_analysis(self) -> None:
        summary = self._current_summary
        student = self._selected_student()
        if (
            summary is None
            or student is None
            or not _field(summary, "can_cancel", False)
        ):
            return
        # Cancellation and polling can finish in either order.  Move to a new
        # view generation so an older poll result cannot repaint a cancelled
        # submission as active after the durable cancel read completes.
        generation = self._advance_view_generation()
        self.poll_timer.stop()
        self.stop_button.setEnabled(False)
        self.progress_message.setText("正在请求停止；当前模型调用可能需要先结束。")
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        self._submit_view_task(
            "停止学生视觉分析",
            lambda: self.facade.cancel_student_analysis(
                student_id=student_id,
                submission_id=submission_id,
                expected_revision=str(_field(summary, "revision")),
            ),
            task_attribute="_cancel_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._cancel_requested,
            on_failure=self._cancel_failed,
        )

    def _cancel_requested(self, summary: object) -> None:
        self._cancel_task_id = None
        self._render_submission(summary)
        if str(_field(summary, "status", "")) in _ACTIVE_STATUSES:
            self.poll_timer.start()

    def _cancel_failed(self, message: str) -> None:
        self._cancel_task_id = None
        set_status(self.progress_message, "error", message)
        if self._current_summary is not None:
            self.stop_button.setEnabled(
                bool(_field(self._current_summary, "can_cancel", False))
            )
            if str(_field(self._current_summary, "status", "")) in _ACTIVE_STATUSES:
                self.poll_timer.start()

    def _load_recent(self) -> None:
        student = self._selected_student()
        if student is None or self._recent_task_id is not None:
            return
        student_id = str(_field(student, "student_id"))
        generation = self._view_generation
        self.recent_status.setText("正在读取本机最近任务…")
        task_ref: dict[str, str] = {}

        def loaded(values: object) -> None:
            if self._recent_task_id != task_ref.get("task_id"):
                return
            self._recent_task_id = None
            current = self._selected_student()
            if (
                generation != self._view_generation
                or current is None
                or str(_field(current, "student_id")) != student_id
            ):
                self._load_recent()
                return
            self._render_recent(tuple(values or ()))

        def failed(message: str) -> None:
            if self._recent_task_id != task_ref.get("task_id"):
                return
            self._recent_task_id = None
            current = self._selected_student()
            if (
                generation != self._view_generation
                or current is None
                or str(_field(current, "student_id")) != student_id
            ):
                self._load_recent()
                return
            self._recent_failed(message)

        task_id = self.tasks.submit(
            "读取学生最近任务",
            lambda: self.facade.student_submissions(student_id=student_id, limit=10),
            on_success=loaded,
            on_failure=failed,
        )
        task_ref["task_id"] = task_id
        self._recent_task_id = task_id

    def _recent_failed(self, message: str) -> None:
        self._recent_task_id = None
        set_status(self.recent_status, "error", message)

    def _render_recent(self, summaries: Sequence[object]) -> None:
        _clear_layout(self.recent_items)
        self._recent_rows.clear()
        if not summaries:
            self.recent_status.setText("这个匿名学生尚无分析任务。")
            return
        self.recent_status.setText(f"本机保留最近 {len(summaries)} 个任务。")
        for summary in summaries:
            row = QWidget()
            row_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            counts = _field(summary, "page_counts_by_role", {})
            if not isinstance(counts, Mapping):
                counts = {}
            detail = QLabel(
                " · ".join(
                    (
                        str(_field(summary, "status_zh", "本机任务")),
                        str(_field(summary, "updated_at", "")),
                        f"题目 {int(counts.get('question_pages', 0))} 页",
                        f"作答 {int(counts.get('student_work_pages', 0))} 页",
                    )
                )
            )
            detail.setWordWrap(True)
            detail.setMinimumWidth(0)
            button = QPushButton("继续查看")
            button.setObjectName("QuietButton")
            button.setAccessibleName(
                f"继续查看{_field(summary, 'status_zh', '学生分析')}任务"
            )
            button.clicked.connect(
                lambda _checked=False, value=summary: self._open_recent(value)
            )
            row_layout.addWidget(detail, 1)
            row_layout.addWidget(button)
            self._recent_rows.append(row_layout)
            self.recent_items.addWidget(row)
        self._apply_responsive_layouts()

    def _open_recent(self, summary: object) -> None:
        student = self._selected_student()
        if student is None or self._open_task_id is not None:
            return
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        # Opening a different durable task is a view transition.  Clear any
        # unsubmitted file paths and invalidate callbacks from the old task.
        self._reset_submission(clear_files=True)
        generation = self._view_generation
        self._set_intake_enabled(False)
        self.prepare_button.setEnabled(False)
        set_status(self.status, "info", "正在读取本机任务最新状态…")

        def opened(value: object) -> None:
            self._open_task_id = None
            if str(_field(value, "submission_id")) != submission_id:
                self._set_intake_enabled(True)
                self._update_prepare_enabled()
                set_status(
                    self.status, "error", "本机任务返回与所选分析不一致，请重试。"
                )
                return
            self._render_submission(value)

        def failed(message: str) -> None:
            self._open_task_id = None
            self._set_intake_enabled(True)
            self._update_prepare_enabled()
            set_status(self.status, "error", message)

        self._submit_view_task(
            "打开学生分析任务",
            lambda: self.facade.student_submission(
                student_id=student_id,
                submission_id=submission_id,
            ),
            task_attribute="_open_task_id",
            generation=generation,
            student_id=student_id,
            on_success=opened,
            on_failure=failed,
        )

    def _load_review(self) -> None:
        summary = self._current_summary
        student = self._selected_student()
        if summary is None or student is None or self._review_task_id is not None:
            return
        self._invalidate_practice("正在重新读取复核记录；此前推荐已清除。")
        self.practice_panel.recommend_button.setEnabled(False)
        self.review_card.show()
        self.reload_review_button.setEnabled(False)
        set_status(self.review_status, "info", "正在读取候选与教师决定…")
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))
        self._submit_view_task(
            "读取学生分析候选",
            lambda: self.facade.student_analysis_review(
                student_id=student_id,
                submission_id=submission_id,
            ),
            task_attribute="_review_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._review_loaded,
            on_failure=self._review_failed,
        )

    def _review_loaded(self, review: object) -> None:
        self._review_task_id = None
        self.reload_review_button.setEnabled(True)
        self._current_review = review
        self.review_desk_button.setEnabled(bool(_field(review, "items", ())))
        self._invalidate_practice()
        self._practice_review_dirty = False
        self.practice_panel.show()
        _clear_layout(self.review_items)
        self.review_editors.clear()
        blockers = tuple(_field(review, "candidate_blockers_zh", ()) or ())
        counts = (
            f"教师评分 {_field(review, 'scoring_confirmed_count', 0)} 项；"
            f"诊断决定 {_field(review, 'diagnostic_confirmed_count', 0)} 项。"
        )
        if bool(_field(review, "review_complete", False)):
            message = (
                "当前临时匹配项的教师决定已记录；结果仍是候选，不写入长期学情。"
                + counts
            )
            set_status(self.review_status, "success", message)
        else:
            message = str(
                _field(review, "message_zh", "等待教师按当前临时匹配项复核。")
            )
            set_status(self.review_status, "attention", message + counts)
        if blockers:
            blocker_label = QLabel(
                '全局需要补充的信息：\n' + "\n".join(f"• {value}" for value in blockers)
            )
            blocker_label.setWordWrap(True)
            set_status(blocker_label, "attention")
            self.review_items.addWidget(blocker_label)
        for item in tuple(_field(review, "items", ()) or ()):
            editor = ReviewItemCard(
                item,
                self._sections,
                diagnosis_available=bool(self._sections),
            )
            editor.set_compact(self._compact)
            editor.score_requested.connect(self._record_score)
            editor.diagnosis_requested.connect(self._record_diagnosis)
            for field in (editor.score_edit, editor.score_reason, editor.teacher_note):
                field.textEdited.connect(self._practice_review_edited)
            for field in (editor.decision, editor.result, editor.primary_error, editor.secondary_error, editor.section):
                field.currentIndexChanged.connect(self._practice_review_edited)
            self.review_items.addWidget(editor)
            self.review_editors.append(editor)
        self._update_practice_enabled()

    def _open_work_batch(self) -> None:
        from .work_batch_desk import WorkBatchDialog
        if self._review_action_task_id:
            set_status(self.status, "attention", "当前评分正在保存，完成后再打开作业批次。")
            return
        dialog = WorkBatchDialog(self.facade, self.tasks, self.window())
        dialog.exec()
        dialog.deleteLater()
        # Do not replace the parent page's in-progress teacher inputs on return.
        set_status(self.status, "info", "批次窗口已返回。本页已有输入保持；同一作答的保存结果可重新读取后核对。")

    def _open_review_desk(self) -> None:
        from .student_review_desk import StudentReviewDesk, editor_values, restore_values
        if (self._current_summary is None or self._current_review is None
                or self._review_task_id or self._review_action_task_id):
            set_status(self.status, "attention", "请先打开已有分析结果，或等待当前记录保存结束。")
            return
        initial = {}
        for editor in self.review_editors:
            values = editor_values(editor)
            if (any(values[k] for k in ("score_edit", "score_reason", "teacher_note"))
                    or any(values[k] is not None for k in ("primary_error", "secondary_error", "section"))
                    or values["decision"] != "accept" or values["result"] != "correct"):
                if (values["decision"] == "accept" and values["result"] == "correct"
                        and not values["teacher_note"] and not any(values[k] for k in ("primary_error", "secondary_error", "section"))):
                    values["decision"], values["result"] = "pending", "not_scored"
                initial[_field(editor.item, "match_id")] = values
        dialog = StudentReviewDesk(self.facade, self.tasks, self._current_summary,
            self._current_review, self._sections,
            student_label=str(_field(self._selected_student(), "label_zh", "匿名学生")),
            initial_edits=initial, parent=self.window())
        dialog.exec()
        dialog.viewer.stop()
        if (_field(self._current_summary, "submission_id") == dialog.submission_id
                and _field(self._selected_student(), "student_id") == dialog.student_id):
            self._review_loaded(dialog.review)
            if not dialog._discarded:
                edits = dialog.edit_values()
                dirty = set(dialog.dirty_keys())
                for editor in self.review_editors:
                    key = _field(editor.item, "match_id")
                    if key in dirty:
                        restore_values(editor, edits[key])
                        self._practice_review_edited()
        dialog.deleteLater()

    def _review_failed(self, message: str) -> None:
        self._review_task_id = None
        self.reload_review_button.setEnabled(True)
        set_status(self.review_status, "error", message)
        self.review_status.setFocus(Qt.FocusReason.OtherFocusReason)
        self.practice_panel.recommend_button.setEnabled(False)

    def _record_score(self, payload: Mapping[str, object]) -> None:
        review = self._current_review
        student = self._selected_student()
        if review is None or student is None or self._review_action_task_id is not None:
            return
        self._invalidate_practice("评分正在更新；完成诊断确认后请重新推荐练习。")
        self.practice_panel.recommend_button.setEnabled(False)
        self.reload_review_button.setEnabled(False)
        set_status(self.review_status, "info", "正在追加教师评分决定…")
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(review, "submission_id"))
        self._submit_view_task(
            "记录学生教师评分",
            lambda: self.facade.record_student_score(
                student_id=student_id,
                submission_id=submission_id,
                expected_revision=str(_field(review, "revision")),
                match_id=str(payload["match_id"]),
                teacher_score=float(payload["teacher_score"]),
                reason=str(payload["reason"]),
            ),
            task_attribute="_review_action_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._review_action_succeeded,
            on_failure=self._review_action_failed,
        )

    def _record_diagnosis(self, payload: Mapping[str, object]) -> None:
        review = self._current_review
        student = self._selected_student()
        if review is None or student is None or self._review_action_task_id is not None:
            return
        self._invalidate_practice("诊断正在更新；记录完成后请重新推荐练习。")
        self.practice_panel.recommend_button.setEnabled(False)
        self.reload_review_button.setEnabled(False)
        set_status(self.review_status, "info", "正在追加教师诊断决定…")
        generation = self._view_generation
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(review, "submission_id"))
        self._submit_view_task(
            "记录学生教师诊断",
            lambda: self.facade.record_student_diagnosis(
                student_id=student_id,
                submission_id=submission_id,
                expected_revision=str(_field(review, "revision")),
                match_id=str(payload["match_id"]),
                scoring_decision_id=str(payload["scoring_decision_id"]),
                decision=str(payload["decision"]),
                result=str(payload["result"]),
                primary_error_type=payload["primary_error_type"],
                secondary_error_types=tuple(payload["secondary_error_types"]),
                curriculum_section_keys=tuple(payload["curriculum_section_keys"]),
                teacher_note=str(payload["teacher_note"]),
            ),
            task_attribute="_review_action_task_id",
            generation=generation,
            student_id=student_id,
            submission_id=submission_id,
            on_success=self._review_action_succeeded,
            on_failure=self._review_action_failed,
        )

    def _review_action_succeeded(self, review: object) -> None:
        self._review_action_task_id = None
        self._review_loaded(review)

    def _review_action_failed(self, message: str) -> None:
        self._review_action_task_id = None
        self.reload_review_button.setEnabled(True)
        set_status(
            self.review_status,
            "error",
            message + " 请重新读取复核状态后再提交；旧决定没有被覆盖。",
        )
        self.review_status.setFocus(Qt.FocusReason.OtherFocusReason)

    def _invalidate_practice(self, message: str = "先记录教师评分并确认诊断对应的教材章节，再按本次学情推荐。") -> None:
        """Drop only recommendation work; score/review workers keep their slots."""

        for attribute in ("_practice_task_id", "_practice_detail_task_id", "_practice_add_task_id"):
            task_id = getattr(self, attribute)
            setattr(self, attribute, None)
            cancel = getattr(self.tasks, "cancel", None)
            if task_id and callable(cancel):
                cancel(task_id)
        self._practice_preview = None
        self._practice_viewed_keys.clear()
        dialog = self._practice_dialog
        self._practice_dialog = None
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass
        self.practice_panel.clear(message)

    def _practice_review_edited(self, *_args: object) -> None:
        self._practice_review_dirty = True
        self._invalidate_practice("评分或诊断尚有未记录的修改。请先记录决定，或重新读取复核状态后再推荐。")
        self.practice_panel.recommend_button.setEnabled(False)

    def _update_practice_enabled(self) -> None:
        supported = all(callable(getattr(self.facade, method, None)) for method in (
            "student_practice_preview", "student_practice_theme_detail", "add_student_practice_to_basket",
        ))
        self.practice_panel.recommend_button.setEnabled(bool(
            supported and self._current_review is not None and self._current_summary is not None
            and self._selected_student() is not None and not self._practice_review_dirty
            and self._review_task_id is None and self._review_action_task_id is None
            and self._practice_task_id is None and self._practice_detail_task_id is None
            and self._practice_add_task_id is None
        ))
        if not supported:
            set_status(self.practice_panel.status, "attention", "当前版本暂未提供本次学情推荐接口；可到题库按章节查找完整大题。")
        busy = self._practice_detail_task_id is not None or self._practice_add_task_id is not None
        for key, card in self.practice_panel.cards.items():
            card.view_button.setEnabled(not busy)
            card.add_button.setEnabled(not busy and key in self._practice_viewed_keys)

    def _recommend_practice(self) -> None:
        self._update_practice_enabled()
        if not self.practice_panel.recommend_button.isEnabled():
            return
        summary, student = self._current_summary, self._selected_student()
        if summary is None or student is None:
            return
        self._invalidate_practice("正在按本次已记录的评分、诊断与教材章节查找本机题库…")
        self.practice_panel.recommend_button.setEnabled(False)
        student_id = str(_field(student, "student_id"))
        submission_id = str(_field(summary, "submission_id"))

        def loaded(preview: object) -> None:
            self._practice_task_id = None
            self._practice_preview = preview
            self.practice_panel.show_preview(preview)
            self._update_practice_enabled()

        def failed(message: str) -> None:
            self._practice_task_id = None
            set_status(self.practice_panel.status, "error", message + " 未加入任何题目，可检查复核记录后重试。")
            self._update_practice_enabled()

        self._submit_view_task(
            "按本次学情推荐练习",
            lambda: self.facade.student_practice_preview(student_id=student_id, submission_id=submission_id),
            task_attribute="_practice_task_id", generation=self._view_generation,
            student_id=student_id, submission_id=submission_id,
            on_success=loaded, on_failure=failed,
        )

    def _view_practice_theme(self, candidate_key: str) -> None:
        preview = self._practice_preview
        card = self.practice_panel.cards.get(candidate_key)
        if preview is None or card is None or self._practice_detail_task_id or self._practice_add_task_id:
            return
        self._practice_viewed_keys.discard(candidate_key)
        old_dialog = self._practice_dialog
        self._practice_dialog = None
        if old_dialog is not None:
            try:
                old_dialog.close()
            except RuntimeError:
                pass
        set_status(card.status, "info", "正在读取完整大题、题图及共同材料…")
        card.add_button.setEnabled(False)

        def failed(message: str) -> None:
            self._practice_detail_task_id = None
            self._practice_viewed_keys.discard(candidate_key)
            set_status(card.status, "error", message + ' 尚未完成预览，暂不能加入题篮。')
            self._update_practice_enabled()

        def loaded(detail: object) -> None:
            self._practice_detail_task_id = None
            image_loader = getattr(self.facade, "library_image", None)
            if not callable(image_loader):
                failed("本地题图读取接口暂不可用。")
                return
            try:
                dialog = StudentPracticeDetailDialog(
                    detail, self.tasks, image_loader, parent=self.window(),
                    answer_image_loader=getattr(self.facade, "library_answer_image", None),
                )
                self._practice_dialog = dialog

                def is_current() -> bool:
                    return self._practice_preview is preview and self._practice_dialog is dialog

                def ready() -> None:
                    if is_current():
                        self._practice_viewed_keys.add(candidate_key)
                        set_status(card.status, "success", "完整题面与共同材料已显示。核对后可将完整大题加入题篮。")
                        self._update_practice_enabled()

                def display_failed(message: str) -> None:
                    if is_current():
                        self._practice_viewed_keys.discard(candidate_key)
                        set_status(card.status, "error", message)
                        self._update_practice_enabled()

                def closed(*_args: object) -> None:
                    if is_current():
                        self._practice_dialog = None
                        if candidate_key not in self._practice_viewed_keys:
                            set_status(card.status, "attention", "尚未完成完整题面预览，请重新查看后再加入题篮。")
                        self._update_practice_enabled()

                dialog.preview_ready.connect(ready)
                dialog.preview_failed.connect(display_failed)
                dialog.finished.connect(closed)
                dialog.show()
            except (DesktopFacadeError, AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                failed(str(getattr(exc, "message_zh", "完整大题预览暂时无法打开。")))
                return
            self._update_practice_enabled()

        self._submit_view_task(
            "查看推荐完整大题",
            lambda: self.facade.student_practice_theme_detail(preview, candidate_key),
            task_attribute="_practice_detail_task_id", generation=self._view_generation,
            student_id=str(_field(preview, "student_id")), submission_id=str(_field(preview, "submission_id")),
            on_success=loaded, on_failure=failed,
        )
        self._update_practice_enabled()

    def _add_practice_theme(self, candidate_key: str) -> None:
        preview = self._practice_preview
        card = self.practice_panel.cards.get(candidate_key)
        if (preview is None or card is None or candidate_key not in self._practice_viewed_keys
                or self._practice_add_task_id or self._practice_detail_task_id):
            return
        set_status(card.status, "info", "正在核对本次推荐版本并将完整大题加入题篮…")

        def added(value: object) -> None:
            self._practice_add_task_id = None
            count = int(value)
            set_status(card.status, "success", f"已加入完整大题，题篮共 {count} 道大题。可前往“组卷”继续编排。")
            self._update_practice_enabled()
            self.basket_changed.emit(count)

        def failed(message: str) -> None:
            self._invalidate_practice("推荐需要重新核对。")
            set_status(self.practice_panel.status, "error", message + " 请重新读取复核状态，再推荐与预览。")
            self.practice_panel.recommend_button.setEnabled(False)

        self._submit_view_task(
            "加入推荐完整大题",
            lambda: self.facade.add_student_practice_to_basket(preview, candidate_key),
            task_attribute="_practice_add_task_id", generation=self._view_generation,
            student_id=str(_field(preview, "student_id")), submission_id=str(_field(preview, "submission_id")),
            on_success=added, on_failure=failed,
        )
        self._update_practice_enabled()

    def _apply_responsive_layouts(self) -> None:
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self._compact
            else QBoxLayout.Direction.LeftToRight
        )
        for layout in (
            self.identity_row,
            self.local_actions,
            self.model_row,
            self.progress_actions,
            *self._recent_rows,
        ):
            layout.setDirection(direction)
        for editor in self.match_editors:
            editor.set_compact(self._compact)
        for editor in self.review_editors:
            editor.set_compact(self._compact)

    def resizeEvent(self, event: QResizeEvent) -> None:
        compact = event.size().width() < 600
        if compact != self._compact:
            self._compact = compact
            margin = 12 if compact else 28
            top = 16 if compact else 24
            bottom = 20 if compact else 32
            self.content_layout.setContentsMargins(margin, top, margin, bottom)
            self._apply_responsive_layouts()
        super().resizeEvent(event)

    def closeEvent(self, event: object) -> None:
        self.poll_timer.stop()
        self._advance_view_generation()
        self._invalidate_practice()
        super().closeEvent(event)  # type: ignore[arg-type]


__all__ = [
    "AnalysisConfirmationDialog",
    "MatchEditor",
    "NewStudentDialog",
    "PagePreviewDialog",
    "ReviewItemCard",
    "StudentPage",
]
