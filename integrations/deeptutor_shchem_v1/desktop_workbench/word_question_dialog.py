"""Offline question browsing for saved Word imports."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_images import MAX_IMAGES
from ..desktop_word_metafile_preview import can_attempt_metafile
from ..desktop_word_question_attributes import EXAM_TYPE_LABELS, validate_attributes
from ..desktop_word_question_recommendations import lesson_knowledge_suggestions
from .components import page_scroll, section_title, set_status
from .tasks import DesktopTaskBridge
from .word_question_attributes_dialog import WordQuestionAttributesDialog


def _label(text: str, *, muted: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if muted:
        widget.setObjectName("MutedLabel")
    return widget


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


_GRADE_LABELS = {
    "grade_10": "高一",
    "grade_11": "高二",
    "grade_12": "高三",
    "unknown": "待确认",
}
_STATUS_LABELS = {
    "auto_suggested": "自动建议，待教师确认",
    "source_observed": "原文可见，未核验原始出处",
    "usage_positioning": "资料包使用定位",
    "teacher_confirmed": "教师确认",
    "unknown": "待确认",
}


def _attribute_text(value: dict, *, details: bool = False) -> str:
    """Readable personal labels, separate from the untouched question text."""
    attributes = value.get("attributes")
    if not isinstance(attributes, dict):
        if _text(value.get("attribute_warning")):
            return _text(value["attribute_warning"])
        return (
            "本题尚未保存教学属性。原题出处、知识点和适用年级待标记。"
            if details
            else ""
        )
    source = attributes.get("source", {})
    original = attributes.get("original_source", {})
    primary = attributes.get("primary_knowledge", {})
    lines = [
        _text(original.get("display_label")) or "讲义收录题·原考试待确认",
        "主知识点："
        + (_text(primary.get("label")) or "待标记")
        + "（"
        + _STATUS_LABELS.get(primary.get("status"), "待确认")
        + "）",
    ]
    if not details:
        return "\n".join(lines)

    def evidence_lines(evidence):
        return [
            "  依据"
            + (
                f"〔来源区块 {entry['block_index']}〕"
                if entry.get("block_index")
                else ""
            )
            + "："
            + _text(entry.get("quote"))
            for entry in evidence
            if isinstance(entry, dict)
        ]

    lines += evidence_lines(primary.get("evidence", []))
    for label, name in (
        ("资料包", "collection_name"),
        ("包内编号", "package_id"),
        ("讲义专题", "lecture_topic"),
        ("原文章节", "source_chapter"),
    ):
        if source.get(name):
            lines.append(label + "：" + _text(source[name]))
    grades = attributes.get("applicable_grades", {})
    lines.append(
        "适用年级："
        + "、".join(
            _GRADE_LABELS.get(grade, "待确认") for grade in grades.get("values", [])
        )
        if grades.get("values")
        else "适用年级：待确认"
    )
    lines.append("适用依据：" + _text(grades.get("basis")))
    for label, name in (
        ("原题年级", "grade"),
        ("原题年份", "year"),
        ("原题地区", "region"),
    ):
        fact = original.get(name, {})
        content = _text(fact.get("value"))
        content = (
            _GRADE_LABELS.get(content, content)
            if name == "grade"
            else ("待确认" if content == "unknown" else content)
        )
        lines.append(label + "：" + (content or "待确认"))
    lines += evidence_lines(original.get("citation_quotes", []))
    for candidate in attributes.get("supporting_knowledge", []):
        lines.append(
            "辅助知识点："
            + _text(candidate.get("label"))
            + "（"
            + _STATUS_LABELS.get(candidate.get("status"), "待确认")
            + "）"
        )
        lines += evidence_lines(candidate.get("evidence", []))
    mappings = attributes.get("curriculum_candidates", [])
    if not mappings:
        lines.append("教材章节点：待映射；知识主题建议不等于已确认教材归属。")
    for candidate in mappings:
        lines.append(
            "教材章节点候选："
            + _text(candidate.get("label"))
            + "〔"
            + _text(candidate.get("section_key"))
            + "〕（"
            + _STATUS_LABELS.get(candidate.get("status"), "待确认")
            + "）"
        )
        lines += evidence_lines(candidate.get("evidence", []))
    forms = attributes.get("response_forms", [])
    lines.append(
        "作答形态建议："
        + ("、".join(_text(form.get("label")) for form in forms) or "待确认")
    )
    answer = attributes.get("answer_status", {}).get("value")
    lines.append(
        "答案状态："
        + (
            "已提取讲义参考答案；不等于官方答案或化学内容已审核"
            if answer == "present_nonofficial_unverified"
            else "尚未检测到完整参考答案，待核对"
        )
    )
    material = attributes.get("material_status", {})
    lines.append(
        "公共材料："
        + (
            "已关联，选题时随题保留"
            if material.get("has_shared_context")
            else "未检测到单独公共材料"
        )
    )
    if material.get("missing_context"):
        lines.append("待核对：题目可能引用未关联的前文或公共材料。")
    if material.get("missing_visual"):
        lines.append("待核对：题面有图示引用，但未检测到对应图片。")
    lines.append(
        "属性版本："
        + str(attributes.get("edit_version", 0))
        + "；"
        + (
            "含教师修改"
            if attributes.get("annotation_source") == "teacher_modified"
            else "自动标记，尚未教师确认"
        )
    )
    if attributes.get("teacher_note"):
        lines.append("教师备注：" + _text(attributes["teacher_note"]))
    return "\n".join(lines)


class WordQuestionRangeDialog(QDialog):
    """Preview explicit source-block ranges before recording any teacher review."""

    _REVIEWABLE_WARNINGS = (
        ("unmarked_answer", "原文未使用答案标记", "已核对无答案标签的原文分界"),
        ("nonstandard_label", "原文变式题标签缺少闭括号", "已核对非标准题目标记的边界"),
        ("self_contained_reference", "题目引用前文或前题", "题面本身已包含所引用的全部材料"),
    )

    def __init__(self, item: dict, source: dict, parent=None):
        super().__init__(parent)
        self.ranges: dict | None = None
        self._preview_key: tuple | None = None
        self._preview_ranges: dict | None = None
        blocks = deepcopy(source.get("blocks", []))
        self._source_valid = isinstance(blocks, list) and bool(blocks)
        self._blocks = blocks if isinstance(blocks, list) else []
        self._positions: dict[int, int] = {}
        previous = 0
        for position, block in enumerate(self._blocks):
            if (
                not isinstance(block, dict)
                or type(block.get("index")) is not int
                or block["index"] <= previous
                or block["index"] in self._positions
                or not isinstance(block.get("text"), str)
            ):
                self._source_valid = False
                continue
            self._positions[block["index"]] = position
            previous = block["index"]
        self._source_assets: dict[int, list] = {}
        assets = source.get("assets", [])
        if isinstance(assets, (list, tuple)):
            for asset in deepcopy(assets):
                if isinstance(asset, dict) and type(asset.get("block_index")) is int:
                    self._source_assets.setdefault(asset["block_index"], []).append(asset)
        self.setWindowTitle("调整本题范围")
        self.resize(800, 780)
        self.setMinimumSize(400, 550)
        outer = QVBoxLayout(self)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setSpacing(10)
        heading = _label("核对原文范围，再保存")
        heading.setObjectName("CardTitle")
        root.addWidget(heading)
        root.addWidget(
            _label(
                "先选择题面、答案与共同材料的区块，再查看本次范围预览。"
                "任何范围变动都需要重新预览；原 Word 不会被修改。"
            )
        )
        root.addWidget(_label(_text(source.get("source_name"))))
        warnings = tuple(
            warning for warning in item.get("warnings", ())
            if isinstance(warning, str) and warning.strip()
        ) if isinstance(item.get("warnings", ()), (list, tuple)) else ()
        self.warning_label = _label(
            "当前题目的待核对提示（保存时还会重新检查）：\n"
            + "\n".join("• " + warning for warning in warnings)
            if warnings else "当前题目没有已记录的范围提醒；仍请先核对本次预览。",
            muted=True,
        )
        self.warning_label.setAccessibleName("当前 Word 题目范围的待核对原因")
        root.addWidget(self.warning_label)
        maximum = max(self._positions, default=0)
        first = next(iter(self._positions), 1)
        self.fields: dict[str, QSpinBox] = {}
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        for key, title in (
            ("block_start", "题面开始"),
            ("question_end", "题面结束"),
            ("answer_start", "答案开始（0 表示无答案）"),
            ("block_end", "本题结束"),
            ("context_start", "共同材料开始（0 表示不选）"),
            ("context_end", "共同材料结束（0 表示不选）"),
        ):
            field = QSpinBox()
            field.setRange(
                0 if key in {"answer_start", "context_start", "context_end"} else 1,
                max(1, maximum),
            )
            field.setValue(
                item.get(key)
                or (0 if key in {"answer_start", "context_start", "context_end"} else first)
            )
            field.setAccessibleName(title)
            self.fields[key] = field
            form.addRow(title, field)
        root.addLayout(form)
        self.preview_button = QPushButton("查看本次范围预览")
        self.preview_button.setObjectName("PrimaryAction")
        self.preview_button.setAccessibleName("查看本次待保存的完整题面答案与共同材料")
        self.preview_button.setEnabled(self._source_valid)
        self.preview_button.clicked.connect(self._preview_range)
        root.addWidget(self.preview_button)
        self.reading_tabs = QTabWidget()
        self.reading_tabs.setMinimumWidth(0)
        self.reading_tabs.setAccessibleName("完整来源与本次范围预览")
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setAccessibleName("完整 Word 来源区块，用于核对题目范围")
        self.original.setMinimumWidth(0)
        self.original.setMinimumHeight(230)
        self.original.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.original.setPlainText(self._block_text(self._blocks))
        self.reading_tabs.addTab(self.original, "完整来源")
        self.range_preview = QPlainTextEdit()
        self.range_preview.setReadOnly(True)
        self.range_preview.setAccessibleName("本次待保存的完整范围预览")
        self.range_preview.setMinimumSize(0, 230)
        self.range_preview.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.range_preview.setPlaceholderText("点击“查看本次范围预览”后，在这里核对完整内容。")
        self.reading_tabs.addTab(self.range_preview, "本次范围预览")
        self.reading_tabs.setTabEnabled(1, False)
        root.addWidget(self.reading_tabs)
        self.locate_button = QPushButton("定位题面开始区块")
        self.locate_button.setObjectName("QuietButton")
        self.locate_button.clicked.connect(self._locate)
        root.addWidget(self.locate_button)
        self.review_checks: dict[str, QCheckBox] = {}
        for key, marker, title in self._REVIEWABLE_WARNINGS:
            if not any(marker in warning for warning in warnings):
                continue
            checkbox = QCheckBox(title)
            checkbox.setAccessibleName(title)
            checkbox.setMinimumWidth(0)
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
            self.review_checks[key] = checkbox
            root.addWidget(checkbox)
        self.review_note = _label(
            "预览后，仅勾选你已核对的事项；未勾选也可保存范围，但对应提醒不会因此解除。"
            "这些确认只针对所选原文的分界和材料位置，不代表化学正确性或官方答案审核。",
            muted=True,
        )
        self.review_note.setVisible(bool(self.review_checks))
        root.addWidget(self.review_note)
        self.scroll = page_scroll(content)
        outer.addWidget(self.scroll, 1)
        self.status = _label("请先查看本次范围预览，再保存。")
        self.status.setAccessibleName("范围预览与保存状态")
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("保存本题范围")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._confirm)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)
        for field in self.fields.values():
            field.valueChanged.connect(self._invalidate_range_preview)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        if not self._source_valid:
            set_status(self.status, "error", "完整来源区块无效或缺失，请返回并重新读取原文。")

    def _block_text(self, blocks: list[dict]) -> str:
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            warnings = block.get("warnings", ())
            warnings = [w for w in warnings if isinstance(w, str)] if isinstance(warnings, (list, tuple)) else []
            index = block.get("index")
            references = list(self._source_assets.get(index, ())) if type(index) is int else []
            for field in ("asset_refs", "assets"):
                values = block.get(field, ())
                if isinstance(values, (list, tuple)):
                    references.extend(values)
            asset_ids = set()
            unnumbered = 0
            for reference in references:
                asset_id = reference.get("asset_id") if isinstance(reference, dict) else reference
                if isinstance(asset_id, str) and asset_id:
                    asset_ids.add(asset_id)
                elif reference:
                    unnumbered += 1
            image_count = len(asset_ids) + unnumbered
            parts.append(
                f"[区块 {block.get('index')}]\n{_text(block.get('text'))}"
                + ("\n待核对：" + "；".join(warnings) if warnings else "")
                + (
                    f"\n原图引用：{image_count} 项。范围页为文字预览，原图请在逐题预览/原Word核对。"
                    if image_count else ""
                )
            )
        return "\n\n".join(parts)

    def _range_key(self) -> tuple:
        return tuple(field.value() for field in self.fields.values())

    def _invalidate_range_preview(self, *_args):
        self.ranges = None
        self._preview_key = None
        self._preview_ranges = None
        self.save_button.setEnabled(False)
        self.range_preview.clear()
        self.reading_tabs.setCurrentIndex(0)
        self.reading_tabs.setTabEnabled(1, False)
        for checkbox in self.review_checks.values():
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
        set_status(self.status, "attention", "范围已变化；请重新预览，之前的勾选确认已清除。")

    def _locate(self):
        self.reading_tabs.setCurrentIndex(0)
        cursor = self.original.document().find(
            f"[区块 {self.fields['block_start'].value()}]"
        )
        if not cursor.isNull():
            cursor.setPosition(cursor.selectionStart())
            self.original.setTextCursor(cursor)
            self.original.verticalScrollBar().setValue(cursor.block().firstLineNumber())

    def _validated_ranges(self) -> dict:
        if not self._source_valid:
            raise ValueError("完整来源区块无效，请重新读取原文。")
        ranges = {key: field.value() for key, field in self.fields.items()}
        start, question_end, answer_start, end = (
            ranges[key]
            for key in ("block_start", "question_end", "answer_start", "block_end")
        )
        context_start, context_end = ranges["context_start"], ranges["context_end"]
        for value in (start, question_end, end):
            if value not in self._positions:
                raise ValueError("题面与本题起止必须选择来源中实际存在的区块。")
        positions = self._positions
        if not positions[start] <= positions[question_end] <= positions[end]:
            raise ValueError("题面与本题起止顺序须与完整来源中的区块顺序一致。")
        if answer_start:
            if (answer_start not in positions
                    or positions[answer_start] != positions[question_end] + 1
                    or positions[answer_start] > positions[end]):
                raise ValueError("题面与答案必须按来源顺序紧邻且不能重叠；同一段题答不能用整区块范围拆开。")
        elif question_end != end:
            raise ValueError("未选择答案时，本题结束须等于题面结束。")
        if bool(context_start) != bool(context_end):
            raise ValueError("共同材料须同时填写有效的开始与结束，或都设为 0。")
        if context_start:
            if context_start not in positions or context_end not in positions:
                raise ValueError("共同材料必须选择来源中实际存在的区块。")
            cstart, cend = positions[context_start], positions[context_end]
            if cstart > cend:
                raise ValueError("共同材料起止须遵守完整来源中的区块顺序。")
            if not (cend < positions[start] or cstart > positions[end]):
                raise ValueError("共同材料不能与本题题面或答案区块重叠。")
        for key in ("answer_start", "context_start", "context_end"):
            ranges[key] = ranges[key] or None
        return ranges

    def _selected_blocks(self, start: int | None, end: int | None) -> list[dict]:
        if start is None or end is None:
            return []
        return self._blocks[self._positions[start]: self._positions[end] + 1]

    def _preview_range(self):
        try:
            ranges = self._validated_ranges()
        except ValueError as exc:
            self._invalidate_range_preview()
            set_status(self.status, "error", str(exc))
            return
        sections = []
        for title, start, end in (
            ("题面 · 学生作答所见", ranges["block_start"], ranges["question_end"]),
            ("答案与解析 · 教师参考，不进入学生题面", ranges["answer_start"], ranges["block_end"]),
            ("共同材料 · 随题面保留", ranges["context_start"], ranges["context_end"]),
        ):
            blocks = self._selected_blocks(start, end)
            sections.append(title + "\n" + (self._block_text(blocks) if blocks else "（本次未选择）"))
        self.range_preview.setPlainText("\n\n────────────────\n\n".join(sections))
        self._preview_ranges = ranges
        self._preview_key = self._range_key()
        self.reading_tabs.setTabEnabled(1, True)
        self.reading_tabs.setCurrentIndex(1)
        for checkbox in self.review_checks.values():
            checkbox.setChecked(False)
            checkbox.setEnabled(True)
        self.save_button.setEnabled(True)
        set_status(self.status, "success", "已列出完整所选文字。请核对题面、答案和共同材料；如需确认提醒，请明确勾选后保存。")
        self.scroll.ensureWidgetVisible(self.reading_tabs, 0, 10)

    def _confirm(self):
        if self._preview_key is None or self._preview_key != self._range_key():
            set_status(self.status, "attention", "请先点击“查看本次范围预览”，核对后再保存。")
            return
        try:
            ranges = self._validated_ranges()
        except ValueError as exc:
            self._invalidate_range_preview()
            set_status(self.status, "error", str(exc))
            return
        if ranges != self._preview_ranges:
            self._invalidate_range_preview()
            return
        reviewed = [key for key, checkbox in self.review_checks.items() if checkbox.isChecked()]
        if reviewed:
            ranges["reviewed_issues"] = reviewed
        self.ranges = ranges
        self.accept()

    def reject(self):
        self.ranges = None
        super().reject()


class WordQuestionDialog(QDialog):
    """Browse source questions, persist choices, and confirm an exact reference."""

    basket_changed = Signal(int)

    def __init__(
        self,
        facade: Any,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
        *,
        initial_source_id: str | None = None,
        batch_id: str | None = None,
        lesson_topic: str = "",
    ):
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self.reference: dict | None = None
        self.preparation_reference: dict | None = None
        self._initial_source_id = initial_source_id
        self._initial_batch_id = batch_id
        self._lesson_topic = _text(lesson_topic).strip()
        self._lesson_suggestions: list[dict] = []
        self._closed = False
        self._jobs: dict[int, str | None] = {}
        self._job_serial = 0
        self._catalog_epoch = 0
        self._detail_epoch = 0
        self._reference_epoch = 0
        self._catalog_busy = False
        self._reference_busy = False
        self._export_busy = False
        self._basket_busy = False
        self._unified_basket_available = callable(getattr(facade, "add_word_questions_to_basket", None))
        self._basket_preview_key: tuple = ()
        self._basket_preview_dialog = None
        self._selection_save_busy = False
        self._selection_to_save: list[dict] | None = None
        self._loaded_once = False
        self._catalog_revision = ""
        self._items: dict[str, dict] = {}
        self._selected: dict[str, str] = {}
        self._points: dict[str, int] = {}
        self._range_busy = False
        self._attributes_busy = False
        self._closing_result: QDialog.DialogCode | None = None
        self._rendering_list = False
        self._current_key: str | None = None
        self._preview_reference: dict | None = None
        self._preview_selection: tuple = ()
        self._image_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._derived_image_keys: set[tuple] = set()
        self._image_targets: list[tuple[QLabel, QPixmap]] = []
        self._image_task_ids: set[str] = set()
        self._loaded_tabs: set[int] = set()
        self._export_paths: dict[str, str] = {}
        self.setWindowTitle("Word 逐题浏览与选题")
        self.resize(1200, 820)
        self.setMinimumSize(400, 600)
        content = QWidget()
        root = QVBoxLayout(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body_scroll = page_scroll(content)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.body_scroll)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(
            section_title(
                "Word 逐题浏览与选题",
                "按题核对已导入的原文与图片；答案单独查看。勾选可跨来源保留，所有读取均在本机完成。",
            )
        )
        self.catalog_note = _label("", muted=True)
        self.catalog_note.setAccessibleName("Word 来源目录读取提醒")
        self.catalog_note.hide()
        root.addWidget(self.catalog_note)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(0)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.lesson_suggestion_panel = QWidget()
        lesson_layout = QVBoxLayout(self.lesson_suggestion_panel)
        lesson_layout.setContentsMargins(0, 0, 0, 6)
        lesson_layout.setSpacing(6)
        self.lesson_topic_label = _label("本课：" + self._lesson_topic)
        self.lesson_topic_label.setObjectName("CardTitle")
        self.lesson_topic_label.setAccessibleName("本次备课课题")
        lesson_layout.addWidget(self.lesson_topic_label)
        self.lesson_suggestion_note = _label(
            "正在与已保存的主辅知识点标签比较；不会自动筛选或勾选。", muted=True
        )
        self.lesson_suggestion_note.setAccessibleName(
            "本课知识点建议的匹配依据与标签状态"
        )
        lesson_layout.addWidget(self.lesson_suggestion_note)
        self.lesson_suggestion_combo = QComboBox()
        self.lesson_suggestion_combo.setAccessibleName("选择本课知识点建议，不自动应用")
        self.lesson_suggestion_combo.setMinimumContentsLength(8)
        self.lesson_suggestion_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.lesson_suggestion_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.lesson_suggestion_combo.currentIndexChanged.connect(
            self._show_lesson_suggestion
        )
        lesson_layout.addWidget(self.lesson_suggestion_combo)
        self.lesson_suggestion_button = QPushButton("应用此知识点筛选")
        self.lesson_suggestion_button.setObjectName("QuietButton")
        self.lesson_suggestion_button.setAccessibleName(
            "确认应用本课知识点建议，保留其他筛选及勾选"
        )
        self.lesson_suggestion_button.clicked.connect(self._apply_lesson_suggestion)
        self.lesson_suggestion_button.setEnabled(False)
        lesson_layout.addWidget(self.lesson_suggestion_button)
        self.lesson_suggestion_panel.setVisible(bool(self._lesson_topic))
        left_layout.addWidget(self.lesson_suggestion_panel)
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("按 Word 来源筛选题目")
        self.source_combo.setMinimumContentsLength(10)
        self.source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.source_combo.addItem("全部来源", None)
        left_layout.addWidget(self.source_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题面、章节、知识点、年级或原考试")
        self.search.setAccessibleName("搜索 Word 题面、章节与教学属性")
        left_layout.addWidget(self.search)
        self.knowledge_filter = QComboBox()
        self.grade_filter = QComboBox()
        self.exam_filter = QComboBox()
        for widget, label in (
            (self.knowledge_filter, "按主辅知识点筛选"),
            (self.grade_filter, "按适用年级筛选，不是原题年级"),
            (self.exam_filter, "按原考试类型筛选"),
        ):
            widget.setAccessibleName(label)
            widget.setMinimumWidth(0)
            widget.setMinimumContentsLength(8)
            widget.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            left_layout.addWidget(widget)
        self.knowledge_filter.addItem("全部知识点（含主辅标签）", None)
        self.grade_filter.addItem("全部适用年级", None)
        self.exam_filter.addItem("全部原考试类型", None)
        self.result_count = _label("正在读取题目…", muted=True)
        left_layout.addWidget(self.result_count)
        self.question_list = QListWidget()
        self.question_list.setAccessibleName("Word 逐题列表，可跨来源勾选")
        self.question_list.setMinimumSize(0, 115)
        self.question_list.setWordWrap(True)
        self.question_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        left_layout.addWidget(self.question_list, 1)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("上一题")
        self.next_button = QPushButton("下一题")
        self.reload_button = QPushButton("刷新")
        for button in (self.previous_button, self.next_button, self.reload_button):
            button.setObjectName("QuietButton")
            navigation.addWidget(button)
        left_layout.addLayout(navigation)
        self.splitter.addWidget(left)
        right = QWidget()
        right.setMinimumWidth(0)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_title = _label("请选择一道题")
        self.detail_title.setObjectName("CardTitle")
        self.detail_note = _label("", muted=True)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_note)
        self.attribute_note = _label("", muted=True)
        self.attribute_note.setAccessibleName("当前 Word 题目知识点与出处摘要")
        right_layout.addWidget(self.attribute_note)
        points_row = QHBoxLayout()
        points_row.addWidget(_label("本次练习分值"))
        self.points = QSpinBox()
        self.points.setRange(1, 100)
        self.points.setValue(2)
        self.points.setAccessibleName("当前题本次练习分值，不是原卷分值")
        points_row.addWidget(self.points)
        points_row.addWidget(_label("非原卷分值", muted=True))
        points_row.addStretch(1)
        right_layout.addLayout(points_row)
        self.range_button = QPushButton("调整本题范围")
        self.range_button.setObjectName("QuietButton")
        self.range_button.setVisible(
            callable(getattr(facade, "word_question_source", None))
            and callable(getattr(facade, "word_question_update_range", None))
        )
        self.range_button.clicked.connect(self._adjust_range)
        right_layout.addWidget(self.range_button)
        self.attributes_button = QPushButton("修改教学标签")
        self.attributes_button.setObjectName("QuietButton")
        self.attributes_button.setVisible(
            callable(getattr(facade, "word_question_attribute_options", None))
            and callable(getattr(facade, "word_question_save_attributes", None))
        )
        self.attributes_button.clicked.connect(self._edit_attributes)
        right_layout.addWidget(self.attributes_button)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 130)
        self._panels: list[QWidget] = []
        self._panel_layouts: list[QVBoxLayout] = []
        for title in ("题面", "答案与解析"):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)
            self._panels.append(panel)
            self._panel_layouts.append(layout)
            self.tabs.addTab(page_scroll(panel), title)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("勾选题目将带入备课的完整参考")
        self.preview.setPlaceholderText("勾选题目后点击“预览选题”。")
        self.tabs.addTab(self.preview, "选题预览")
        self.attributes_preview = QPlainTextEdit()
        self.attributes_preview.setReadOnly(True)
        self.attributes_preview.setAccessibleName("当前 Word 题目属性及标注依据")
        self.tabs.addTab(self.attributes_preview, "教学属性与依据")
        right_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([380, 770])
        root.addWidget(self.splitter, 1)
        self.selection_count = _label("已选 0 题；筛选或切换来源会保留勾选。")
        root.addWidget(self.selection_count)
        self.basket_preview_button = QPushButton("查看入篮完整题面与答案")
        self.basket_preview_button.setObjectName("PrimaryAction")
        self.basket_add_button = QPushButton("加入统一题篮")
        self.basket_add_button.setEnabled(False)
        self.basket_preview_button.clicked.connect(self._preview_for_basket)
        self.basket_add_button.clicked.connect(self._add_to_basket)
        for button in (self.basket_preview_button, self.basket_add_button):
            button.setVisible(callable(getattr(facade, "add_word_questions_to_basket", None)))
            root.addWidget(button)
        self.include_images = QCheckBox("同时带入原图（用于本地课件排版）")
        self.include_images.setChecked(True)
        self.include_images.setAccessibleName("带入所选 Word 题目的原图")
        self.include_images.toggled.connect(self._image_mode_changed)
        root.addWidget(self.include_images)
        self.image_mode_note = _label(
            "原图随备课保存在本机，模型只收到图注、来源和用途，不会看到图片像素。"
            "如仅需文字，可取消勾选后重新预览。",
            muted=True,
        )
        root.addWidget(self.image_mode_note)
        self.action_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.preview_button = QPushButton("预览选题")
        self.import_button = QPushButton("确认带入备课")
        self.clear_button = QPushButton("清空勾选")
        self.clear_button.setObjectName("QuietButton")
        for button in (self.preview_button, self.import_button, self.clear_button):
            self.action_layout.addWidget(button)
        root.addLayout(self.action_layout)
        self.export_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.export_title = QLineEdit("Word 选题练习")
        self.export_title.setAccessibleName("导出练习名称")
        self.export_title.setPlaceholderText("练习名称")
        self.export_button = QPushButton("导出练习")
        self.export_button.setObjectName("QuietButton")
        self.export_layout.addWidget(self.export_title, 1)
        self.export_layout.addWidget(self.export_button)
        root.addLayout(self.export_layout)
        self.show_student_scores = QCheckBox("题面显示分数")
        self.show_student_scores.setChecked(False)
        self.show_student_scores.setToolTip(
            "默认不在学生卷额外添加分数；教师答案始终保留各题分值。"
        )
        self.show_student_scores.toggled.connect(self._invalidate_preview)
        root.addWidget(self.show_student_scores)
        for control in (self.export_title, self.export_button, self.show_student_scores):
            control.setVisible(not self._unified_basket_available)
        if self._unified_basket_available:
            root.addWidget(_label("导出统一到组卷页：先预览并加入同一题篮，再调整顺序、分数并确认两版 DOCX。备课引用仍可单独使用。", muted=True))
        self.export_result_layout = QHBoxLayout()
        self.student_button = QPushButton("打开学生版")
        self.teacher_button = QPushButton("打开教师版")
        for button in (self.student_button, self.teacher_button):
            button.setObjectName("QuietButton")
            button.hide()
            self.export_result_layout.addWidget(button)
        root.addLayout(self.export_result_layout)
        self.export_note = _label("", muted=True)
        self.export_note.setAccessibleName("练习导出提醒")
        self.export_note.hide()
        root.addWidget(self.export_note)
        self.status = _label("正在读取已保存的 Word 题目…")
        self.status.setAccessibleName("Word 选题状态")
        root.addWidget(self.status)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        root.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.source_combo.currentIndexChanged.connect(self._filter_items)
        for widget in (self.knowledge_filter, self.grade_filter, self.exam_filter):
            widget.currentIndexChanged.connect(self._filter_items)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._filter_items)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self.question_list.itemChanged.connect(self._checked)
        self.question_list.currentItemChanged.connect(self._current_changed)
        self.points.valueChanged.connect(self._points_changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        self.reload_button.clicked.connect(self._load_catalog)
        self.clear_button.clicked.connect(self._clear_selection)
        self.preview_button.clicked.connect(self._preview_selected)
        self.import_button.clicked.connect(self._confirm)
        self.export_button.clicked.connect(self._export)
        self.student_button.clicked.connect(lambda: self._open_export("student_path"))
        self.teacher_button.clicked.connect(lambda: self._open_export("teacher_path"))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(180)
        self._save_timer.timeout.connect(self._persist_selection)
        self.tasks.task_finished.connect(self._task_finished)
        self._load_catalog()

    @property
    def selections(self) -> list[dict[str, Any]]:
        return [
            {"key": key, "revision": revision, "points": self._points.get(key, 2)}
            for key, revision in self._selected.items()
        ]

    def _selection_key(self) -> tuple:
        return tuple(
            (key, revision, self._points.get(key, 2))
            for key, revision in self._selected.items()
        )

    def _submit(self, label, operation, success, failure=None) -> str | None:
        self._job_serial += 1
        token = self._job_serial
        self._jobs[token] = None

        def completed(value, failed=False):
            self._jobs.pop(token, None)
            if self._closed:
                return
            if failed:
                if failure is not None:
                    failure(value)
                else:
                    set_status(self.status, "error", value)
            else:
                success(value)

        try:
            task_id = self.tasks.submit(
                label,
                operation,
                on_success=completed,
                on_failure=lambda message: completed(message, True),
            )
            if token in self._jobs:
                self._jobs[token] = task_id
            return task_id
        except (RuntimeError, TypeError):
            completed("本地任务暂时无法启动，请重试。", True)
            return None

    def _task_finished(self, task_id: str) -> None:
        self._image_task_ids.discard(task_id)
        for token, pending in tuple(self._jobs.items()):
            if pending == task_id:
                self._jobs.pop(token, None)

    def _load_catalog(self) -> None:
        if self._catalog_busy or self._closed:
            return
        self._catalog_epoch += 1
        epoch = self._catalog_epoch
        restore = not self._loaded_once
        self._catalog_busy = True
        self._invalidate_preview()
        self._update_actions()
        set_status(self.status, "info", "正在本机读取逐题目录…")
        facade = self.facade

        def read():
            catalog = facade.word_question_catalog()
            saved = facade.word_question_saved_selection() if restore else None
            return catalog, saved

        self._submit(
            "读取 Word 逐题目录",
            read,
            lambda result: self._catalog_ready(epoch, result),
            lambda message: self._catalog_failed(epoch, message),
        )

    def _catalog_failed(self, epoch: int, message: str) -> None:
        if epoch != self._catalog_epoch:
            return
        self._catalog_busy = False
        set_status(self.status, "error", message)
        self._update_actions()

    def _catalog_ready(self, epoch: int, result: Any) -> None:
        if epoch != self._catalog_epoch:
            return
        try:
            catalog, saved = result
            if (
                not isinstance(catalog, dict)
                or not isinstance(catalog.get("items"), list)
                or not isinstance(catalog.get("sources"), list)
            ):
                raise TypeError("invalid catalog")
            items = {}
            for item in catalog["items"]:
                if (
                    not isinstance(item, dict)
                    or not _text(item.get("key"))
                    or not _text(item.get("revision"))
                    or item["key"] in items
                ):
                    raise ValueError("invalid question")
                items[item["key"]] = deepcopy(item)
            if not self._loaded_once:
                if not isinstance(saved, list):
                    raise ValueError("invalid saved selection")
                self._selected = {
                    item["key"]: item["revision"]
                    for item in saved
                    if isinstance(item, dict)
                    and isinstance(item.get("key"), str)
                    and isinstance(item.get("revision"), str)
                }
                self._points.update(
                    {
                        item["key"]: item.get("points", 2)
                        for item in saved
                        if isinstance(item, dict)
                        and item.get("key") in self._selected
                        and type(item.get("points", 2)) is int
                        and 1 <= item.get("points", 2) <= 100
                    }
                )
            before = len(self._selected)
            self._selected = {
                key: revision
                for key, revision in self._selected.items()
                if key in items
                and items[key]["revision"] == revision
                and items[key].get("selection_ready") is True
            }
            removed = before - len(self._selected)
            self._items = items
            self._catalog_revision = _text(catalog.get("revision"))
            selected_source = self.source_combo.currentData()
            if not self._loaded_once:
                selected_source = self._initial_source_id or next(
                    (
                        item.get("source_id")
                        for item in items.values()
                        if self._initial_batch_id
                        and item.get("batch_id") == self._initial_batch_id
                    ),
                    selected_source,
                )
            self.source_combo.blockSignals(True)
            self.source_combo.clear()
            self.source_combo.addItem("全部来源", None)
            for source in catalog["sources"]:
                if isinstance(source, dict) and _text(source.get("source_id")):
                    name = (
                        _text(source.get("source_name"))
                        .replace("\\", "/")
                        .rsplit("/", 1)[-1]
                    )
                    self.source_combo.addItem(name or "Word 来源", source["source_id"])
            self.source_combo.setCurrentIndex(
                max(0, self.source_combo.findData(selected_source))
            )
            self.source_combo.blockSignals(False)
            self._loaded_once = True
            self._catalog_busy = False
            self._refresh_attribute_filters()
            self._filter_items()
            warnings = [
                warning
                for warning in catalog.get("warnings", ())
                if isinstance(warning, str) and warning
            ]
            self.catalog_note.setText(
                "来源读取提醒：\n" + "\n".join(warnings) if warnings else ""
            )
            self.catalog_note.setVisible(bool(warnings))
            message = (
                f"已读取 {len(items)} 道题。题目分段和图片仍须逐题核对。"
                if items
                else "尚无可用题目，请先导入 Word 或查看来源读取提醒。"
            )
            if removed:
                message += f" 有 {removed} 道历史选题已变化，已取消勾选，请重新核对。"
                self._queue_selection_save()
            if warnings:
                message += f" 有 {len(warnings)} 条来源读取提醒，请先核对。"
            set_status(
                self.status,
                "attention" if removed or warnings or not items else "success",
                message,
            )
        except (KeyError, TypeError, ValueError):
            self.source_combo.blockSignals(False)
            self._catalog_failed(epoch, "题目目录暂时无法读取，请刷新或重新导入来源。")

    def _refresh_attribute_filters(self) -> None:
        knowledge = {}
        grades = set()
        exams = set()
        for item in self._items.values():
            attributes = item.get("attributes") or {}
            points = [attributes.get("primary_knowledge", {})] + attributes.get(
                "supporting_knowledge", []
            )
            for point in points:
                key = point.get("id") or "unknown"
                knowledge[key] = (
                    point.get("label") if key != "unknown" else "知识点待映射"
                )
            grades.update(
                attributes.get("applicable_grades", {}).get("values") or ["unknown"]
            )
            exams.add(
                attributes.get("original_source", {}).get("exam_type", {}).get("value")
                or "unknown"
            )
        groups = (
            (
                self.knowledge_filter,
                "全部知识点（含主辅标签）",
                [
                    (key, f"{key} · {label}" if key != "unknown" else label)
                    for key, label in sorted(knowledge.items())
                ],
            ),
            (
                self.grade_filter,
                "全部适用年级",
                [
                    (key, "适用 " + _GRADE_LABELS.get(key, "待确认"))
                    for key in sorted(grades)
                ],
            ),
            (
                self.exam_filter,
                "全部原考试类型",
                [
                    (key, EXAM_TYPE_LABELS.get(key, "原考试待确认"))
                    for key in sorted(exams)
                ],
            ),
        )
        for widget, title, options in groups:
            current, label = widget.currentData(), widget.currentText()
            widget.blockSignals(True)
            widget.clear()
            widget.addItem(title, None)
            for key, text in options:
                widget.addItem(text, key)
            if current is not None and widget.findData(current) < 0:
                widget.addItem(label, current)
            widget.setCurrentIndex(max(0, widget.findData(current)))
            widget.blockSignals(False)
        self._refresh_lesson_suggestions()

    def _refresh_lesson_suggestions(self) -> None:
        if not self._lesson_topic:
            return
        current = self.lesson_suggestion_combo.currentData()
        self._lesson_suggestions = lesson_knowledge_suggestions(
            self._lesson_topic, self._items.values()
        )
        self.lesson_suggestion_combo.blockSignals(True)
        self.lesson_suggestion_combo.clear()
        for suggestion in self._lesson_suggestions:
            self.lesson_suggestion_combo.addItem(
                f"{suggestion['knowledge_id']} · {suggestion['label']}",
                suggestion["knowledge_id"],
            )
        self.lesson_suggestion_combo.setCurrentIndex(
            max(0, self.lesson_suggestion_combo.findData(current))
        )
        self.lesson_suggestion_combo.blockSignals(False)
        self.lesson_suggestion_combo.setVisible(bool(self._lesson_suggestions))
        self.lesson_suggestion_button.setVisible(bool(self._lesson_suggestions))
        self._show_lesson_suggestion()

    def _show_lesson_suggestion(self, *_args) -> None:
        suggestion = next(
            (
                row
                for row in self._lesson_suggestions
                if row["knowledge_id"] == self.lesson_suggestion_combo.currentData()
            ),
            None,
        )
        if suggestion is None:
            self.lesson_suggestion_note.setText(
                "未找到课题与现有标签名称的明确词面匹配。可用下面的知识点、年级、考试筛选或搜索；"
                "知识点待映射的题目仍可查看。本次没有调用 AI，也没有自动筛选。"
            )
        else:
            states = suggestion["status_counts"]
            state_text = "\n".join(
                f"{label} {states[name]} 条"
                for name, label in (
                    ("auto_suggested", "标签自动建议、待教师确认"),
                    ("teacher_confirmed", "标签经教师确认"),
                    ("unknown", "标签状态待确认"),
                )
                if states.get(name)
            )
            match_text = (
                "标签完整匹配：“" + suggestion["label"] + "”"
                if suggestion["exact_label_match"]
                else "词项匹配：“" + "、".join(suggestion["matched_terms"]) + "”"
            )
            self.lesson_suggestion_note.setText(
                "\n".join(
                    (
                        match_text,
                        f"主标签命中 {suggestion['primary_count']} 条 · 辅标签命中 {suggestion['supporting_count']} 条",
                        f"完整题目/主题 {suggestion['question_count']} 个 · 当前可勾选 {suggestion['selectable_count']} 个",
                        state_text,
                        "本机文字匹配，不是 AI 判断。",
                        "确认应用后才筛选；不会自动勾选或修改标签。",
                    )
                )
            )
        self._update_actions()

    def _apply_lesson_suggestion(self) -> None:
        if not self.lesson_suggestion_button.isEnabled():
            return
        knowledge_id = self.lesson_suggestion_combo.currentData()
        index = self.knowledge_filter.findData(knowledge_id)
        if index < 0:
            return
        self.knowledge_filter.setCurrentIndex(index)
        self._filter_items()
        set_status(
            self.status,
            "info",
            "已按确认的知识点筛选；来源、年级、考试、搜索条件及原有勾选均保留。"
            "请完整预览后自行勾选；若结果为空，可调整其他筛选。尚未调用模型。",
        )

    def _matches_attribute_filters(self, value):
        attributes = value.get("attributes") or {}
        knowledge = self.knowledge_filter.currentData()
        if knowledge:
            points = [attributes.get("primary_knowledge", {})] + attributes.get(
                "supporting_knowledge", []
            )
            if knowledge not in {point.get("id") or "unknown" for point in points}:
                return False
        grade = self.grade_filter.currentData()
        if grade and grade not in (
            attributes.get("applicable_grades", {}).get("values") or ["unknown"]
        ):
            return False
        exam = self.exam_filter.currentData()
        return not exam or exam == (
            attributes.get("original_source", {}).get("exam_type", {}).get("value")
            or "unknown"
        )

    def _filter_items(self, *_args) -> None:
        current = self._current_key
        source = self.source_combo.currentData()
        query = self.search.text().strip().casefold()
        self._rendering_list = True
        self.question_list.blockSignals(True)
        self.question_list.clear()
        row_to_select = 0
        for key, value in self._items.items():
            if source and value.get("source_id") != source:
                continue
            if not self._matches_attribute_filters(value):
                continue
            question_text = " ".join(
                _text(block.get("text"))
                for block in value.get("question_blocks", ())
                if isinstance(block, dict)
            )
            searchable = " ".join(
                [
                    _text(value.get("title")),
                    _text(value.get("chapter")),
                    _text(value.get("source_name")),
                    question_text,
                    _attribute_text(value, details=True),
                ]
            ).casefold()
            if query and query not in searchable:
                continue
            title = _text(value.get("title")) or "原文题目"
            chapter = _text(value.get("chapter")) or "章节待核对"
            source_label = _text(value.get("source_label")) or _text(
                value.get("source_name")
            )
            state = (
                "可勾选"
                if value.get("selection_ready") is True
                else "分段待核对，暂不能勾选"
            )
            blocks = list(value.get("question_blocks", ())) + list(
                value.get("answer_blocks", ())
            )
            assets = [
                asset
                for block in blocks
                if isinstance(block, dict)
                for asset in block.get("assets", ())
                if isinstance(asset, dict)
            ]
            if assets:
                state += f" · 来源图 {len(assets)} 处"
            if (
                value.get("warnings")
                or any(
                    block.get("warnings") for block in blocks if isinstance(block, dict)
                )
                or any(
                    not (
                        asset.get("preview_supported") is True
                        or can_attempt_metafile(asset)
                    )
                    for asset in assets
                )
            ):
                state += " · 有原文缺口/待核对提示"
            excerpt = " ".join(question_text.split())
            if excerpt.startswith(title):
                excerpt = excerpt[len(title) :].lstrip()
            label = "\n".join(
                part
                for part in (title, excerpt[:90], f"{chapter} · {source_label}", state)
                if part
            )
            widget = QListWidgetItem(label)
            widget.setData(Qt.ItemDataRole.UserRole, key)
            if value.get("selection_ready") is True:
                widget.setFlags(widget.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                widget.setCheckState(
                    Qt.CheckState.Checked
                    if key in self._selected
                    else Qt.CheckState.Unchecked
                )
            self.question_list.addItem(widget)
            if key == current:
                row_to_select = self.question_list.count() - 1
        self.question_list.blockSignals(False)
        self._rendering_list = False
        self.result_count.setText(
            f"显示 {self.question_list.count()} / {len(self._items)} 题"
        )
        if self.question_list.count():
            self.question_list.setCurrentRow(row_to_select)
        else:
            self._show_question(None)
        self._update_actions()

    def _current_changed(self, item: QListWidgetItem | None, *_args) -> None:
        if not self._rendering_list:
            self._show_question(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _clear_panel(self, index: int) -> None:
        layout = self._panel_layouts[index]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _show_question(self, key: str | None) -> None:
        for task_id in self._image_task_ids:
            self.tasks.cancel(task_id)
        self._image_task_ids.clear()
        self._detail_epoch += 1
        self._current_key = key
        self._image_targets = []
        self._loaded_tabs.clear()
        for index in (0, 1):
            self._clear_panel(index)
        value = self._items.get(key, {})
        self.detail_title.setText(_text(value.get("title")) or "没有符合条件的题目")
        source = _text(value.get("source_label")) or _text(value.get("source_name"))
        self.detail_note.setText(
            " · ".join(part for part in (source, _text(value.get("chapter"))) if part)
        )
        self.attribute_note.setText(_attribute_text(value))
        self.attribute_note.setVisible(bool(self.attribute_note.text()))
        self.attributes_preview.setPlainText(
            _attribute_text(value, details=True) if value else ""
        )
        self.points.blockSignals(True)
        self.points.setValue(self._points.get(key, 2))
        self.points.blockSignals(False)
        self.points.setEnabled(bool(value))
        self.tabs.setCurrentIndex(0)
        self._render_tab(0)
        self._update_actions()

    def _tab_changed(self, index: int) -> None:
        if index in (0, 1):
            self._render_tab(index)

    def _render_tab(self, index: int) -> None:
        if index in self._loaded_tabs:
            return
        self._loaded_tabs.add(index)
        layout = self._panel_layouts[index]
        value = self._items.get(self._current_key, {})
        if not value:
            layout.addWidget(_label("请调整来源筛选或搜索词。", muted=True))
            layout.addStretch(1)
            return
        blocks = (
            list(value.get("answer_blocks", ()))
            if index
            else list(value.get("context_blocks", ()))
            + list(value.get("question_blocks", ()))
        )
        if not blocks:
            layout.addWidget(
                _label(
                    "本题未提取到参考答案，请核对来源。"
                    if index
                    else "本题暂无可显示的题面。",
                    muted=True,
                )
            )
        for block in blocks:
            if not isinstance(block, dict):
                continue
            layout.addWidget(
                _label(f"来源区块 {block.get('index', '待核对')}", muted=True)
            )
            layout.addWidget(_label(_text(block.get("text"))))
            for asset in block.get("assets", ()):
                if not isinstance(asset, dict):
                    continue
                label = _label(_text(asset.get("label")) or "原文图片", muted=True)
                layout.addWidget(label)
                image_widget = _label("正在读取来源图片…", muted=True)
                image_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image_widget.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
                )
                layout.addWidget(image_widget)
                if not (
                    asset.get("preview_supported") is True
                    or can_attempt_metafile(asset)
                ):
                    image_widget.setText("此对象暂不能直接预览，请核对原始 Word。")
                elif _text(asset.get("asset_id")):
                    self._load_image(
                        value, asset, image_widget, self._detail_epoch, label
                    )
            for warning in block.get("warnings", ()):
                if isinstance(warning, str) and warning:
                    layout.addWidget(_label("待核对：" + warning, muted=True))
        for warning in value.get("warnings", ()):
            if isinstance(warning, str) and warning:
                layout.addWidget(_label("本题提醒：" + warning, muted=True))
        layout.addStretch(1)

    def _load_image(
        self,
        question: dict,
        asset: dict,
        widget: QLabel,
        epoch: int,
        caption: QLabel | None = None,
    ) -> None:
        cache_key = (question["key"], question["revision"], asset["asset_id"])
        if cache_key in self._image_cache:
            self._image_cache.move_to_end(cache_key)
            self._display_image(widget, self._image_cache[cache_key])
            if caption is not None and cache_key in self._derived_image_keys:
                caption.setText(caption.text() + "\n原 Word 矢量图的本地转换预览")
            return
        facade = self.facade

        def ready(result):
            if epoch != self._detail_epoch or self._current_key != question["key"]:
                return
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("bytes"), bytes)
                or result.get("mime_type")
                not in {
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/gif",
                    "image/bmp",
                    "image/tiff",
                }
            ):
                failed("")
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(result["bytes"]) or pixmap.isNull():
                failed("")
                return
            self._image_cache[cache_key] = pixmap
            if result.get("derived_preview") is True:
                self._derived_image_keys.add(cache_key)
                if caption is not None:
                    caption.setText(caption.text() + "\n原 Word 矢量图的本地转换预览")
            while len(self._image_cache) > 32:
                evicted, _ = self._image_cache.popitem(last=False)
                self._derived_image_keys.discard(evicted)
            self._display_image(widget, pixmap)

        def failed(_message):
            if epoch == self._detail_epoch and self._current_key == question["key"]:
                widget.setText("此来源图片暂时无法读取，请重新选题或核对原始 Word。")

        task_id = self._submit(
            "读取 Word 题目来源图片",
            lambda: facade.word_question_image(*cache_key),
            ready,
            failed,
        )
        if task_id:
            self._image_task_ids.add(task_id)

    def _display_image(self, widget: QLabel, pixmap: QPixmap) -> None:
        self._image_targets.append((widget, pixmap))
        self._scale_images()

    def _scale_images(self) -> None:
        if self._closed:
            return
        for widget, pixmap in self._image_targets:
            available = max(40, widget.parentWidget().width() - 28)
            scaled = pixmap.scaled(
                min(pixmap.width(), available),
                min(640, pixmap.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            widget.setPixmap(scaled)
            widget.setFixedHeight(scaled.height() + 8)

    def _checked(self, item: QListWidgetItem) -> None:
        if self._rendering_list:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        value = self._items.get(key, {})
        if (
            item.checkState() == Qt.CheckState.Checked
            and value.get("selection_ready") is True
        ):
            self._selected[key] = value["revision"]
        else:
            self._selected.pop(key, None)
        self._invalidate_preview()
        self._queue_selection_save()
        self._update_actions()

    def _clear_selection(self) -> None:
        self._selected.clear()
        self._invalidate_preview()
        self._queue_selection_save()
        self._filter_items()

    def _points_changed(self, value: int) -> None:
        if self._current_key not in self._items:
            return
        self._points[self._current_key] = value
        if self._current_key in self._selected:
            self._invalidate_preview()
            self._queue_selection_save()
            self._update_actions()

    def _edit_attributes(self) -> None:
        value = self._items.get(self._current_key)
        if (
            not value
            or self._attributes_busy
            or self._range_busy
            or self._catalog_busy
            or self._export_busy
        ):
            return
        question = deepcopy(value)
        key, revision = question["key"], question["revision"]
        facade = self.facade
        self._attributes_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在本机读取标签目录与修订历史…")

        def failed(message):
            self._attributes_busy = False
            set_status(self.status, "error", message)
            self._update_actions()

        def options_ready(options):
            if (
                self._current_key != key
                or self._items.get(key, {}).get("revision") != revision
            ):
                self._attributes_busy = False
                self._update_actions()
                return
            try:
                editor = WordQuestionAttributesDialog(question, options, self)
            except (KeyError, TypeError, ValueError):
                failed("教学属性暂时无法读取，请刷新后重试。")
                return
            accepted = editor.exec() == editor.DialogCode.Accepted
            updates = editor.updates
            expected = editor.expected_attribute_revision
            stored = editor.expected_stored_revision
            editor.deleteLater()
            if not accepted or not updates:
                self._attributes_busy = False
                self._update_actions()
                set_status(self.status, "info", "已取消修改；教学标签未写入。")
                return

            def saved(result):
                self._attributes_busy = False
                try:
                    row = validate_attributes(result)
                    if row["key"] != key or row["question_revision"] != revision:
                        raise ValueError("mismatched attributes")
                    if self._items.get(key, {}).get("revision") != revision:
                        raise ValueError("changed question")
                    self._items[key]["attributes"] = row
                except (KeyError, TypeError, ValueError):
                    failed("标签保存结果与当前题目不一致，请刷新后核对。")
                    return
                self._invalidate_preview()
                self._refresh_attribute_filters()
                self._filter_items()
                set_status(
                    self.status,
                    "success",
                    "教学标签已保存在本机，自动建议与修订历史均保留；题篮和完整题目未改变。",
                )

            set_status(self.status, "info", "正在保存个人教学标签…")
            self._submit(
                "保存 Word 教学标签",
                lambda: facade.word_question_save_attributes(
                    key,
                    revision,
                    updates,
                    expected_attribute_revision=expected,
                    expected_stored_revision=stored,
                ),
                saved,
                failed,
            )

        self._submit(
            "读取 Word 教学标签",
            lambda: facade.word_question_attribute_options(key, revision),
            options_ready,
            failed,
        )

    def _adjust_range(self) -> None:
        value = self._items.get(self._current_key)
        if not value or self._range_busy or self._attributes_busy:
            return
        question = deepcopy(value)
        key, revision = question["key"], question["revision"]
        facade = self.facade
        self._range_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在读取完整 Word 来源以核对题目范围…")

        def failed(message):
            self._range_busy = False
            set_status(self.status, "error", message)
            self._update_actions()

        def source_ready(source):
            if self._current_key != key:
                self._range_busy = False
                self._update_actions()
                return
            if not isinstance(source, dict) or not isinstance(
                source.get("blocks"), list
            ):
                failed("完整 Word 来源暂时无法读取。")
                return
            editor = WordQuestionRangeDialog(question, source, self)
            accepted = editor.exec() == editor.DialogCode.Accepted
            ranges = editor.ranges
            editor.deleteLater()
            if not accepted or ranges is None:
                self._range_busy = False
                self._update_actions()
                return

            def updated(item):
                self._range_busy = False
                if (
                    not isinstance(item, dict)
                    or not _text(item.get("key"))
                    or not _text(item.get("revision"))
                ):
                    failed("题目范围的保存结果无效，请刷新后重新核对。")
                    return
                self._items.pop(key, None)
                self._items[item["key"]] = deepcopy(item)
                if key in self._selected:
                    self._selected.pop(key)
                    self._selected[item["key"]] = item["revision"]
                    self._points[item["key"]] = self._points.get(key, 2)
                    self._queue_selection_save()
                self._invalidate_preview()
                if self._current_key == key:
                    self._current_key = item["key"]
                self._filter_items()
                set_status(
                    self.status,
                    "success",
                    "本题范围已更新，请重新核对题面与答案后预览选题。",
                )

            self._submit(
                "保存 Word 题目范围",
                lambda: facade.word_question_update_range(key, revision, **ranges),
                updated,
                failed,
            )

        self._submit(
            "读取 Word 完整来源",
            lambda: facade.word_question_source(key, revision),
            source_ready,
            failed,
        )

    def _queue_selection_save(self) -> None:
        self._selection_to_save = deepcopy(self.selections)
        self._save_timer.start()

    def _persist_selection(self) -> None:
        if self._selection_save_busy or self._selection_to_save is None or self._closed:
            return
        selections = self._selection_to_save
        self._selection_to_save = None
        self._selection_save_busy = True
        facade = self.facade

        def finished(_value, failed=False):
            self._selection_save_busy = False
            if failed:
                self._selection_to_save = self.selections
                self._closing_result = None
                set_status(
                    self.status,
                    "attention",
                    "当前勾选仍在本窗口中，但未能保存；关闭前请重试勾选。",
                )
                return
            if self._selection_to_save is not None:
                self._persist_selection()
            elif self._closing_result is not None:
                self._finish_dialog(self._closing_result)

        self._submit(
            "保存 Word 勾选",
            lambda: facade.word_question_save_selection(selections),
            finished,
            lambda message: finished(message, True),
        )

    def _invalidate_preview(self) -> None:
        self._basket_preview_key = ()
        if self._basket_preview_dialog is not None:
            self._basket_preview_dialog.reject()
            self._basket_preview_dialog = None
        self._reference_epoch += 1
        self._reference_busy = False
        self.reference = None
        self.preparation_reference = None
        if self._closing_result == QDialog.DialogCode.Accepted:
            self._closing_result = None
        self._preview_reference = None
        self._preview_selection = ()
        self.preview.clear()
        self.import_button.setEnabled(False)

    def _image_mode_changed(self) -> None:
        self._invalidate_preview()
        self._update_actions()

    @staticmethod
    def _valid_reference(value: Any) -> bool:
        from ..desktop_preparation_images import (
            PreparationImageError,
            normalize_image_assets,
        )

        valid = (
            isinstance(value, dict)
            and isinstance(value.get("materials"), str)
            and bool(value["materials"].strip())
            and isinstance(value.get("warnings", []), (list, tuple))
            and all(isinstance(item, str) for item in value.get("warnings", []))
            and type(value.get("include_images")) is bool
            and isinstance(value.get("selections"), list)
            and isinstance(value.get("image_assets"), list)
            and isinstance(value.get("image_issues"), list)
            and all(isinstance(item, str) for item in value.get("image_issues", []))
        )
        if not valid or (not value["include_images"] and value["image_assets"]):
            return False
        try:
            # A large batch must remain visible in preview, not be truncated.
            for asset in value["image_assets"]:
                if normalize_image_assets([asset]) != [asset]:
                    return False
        except PreparationImageError:
            return False
        return True

    def _reference_can_import(self) -> bool:
        value = self._preview_reference
        return bool(
            value is not None
            and (
                not value["include_images"]
                or (
                    not value["image_issues"]
                    and len(value["image_assets"]) <= MAX_IMAGES
                )
            )
        )

    def _preview_selected(self) -> None:
        if (
            not self._selected
            or self._catalog_busy
            or self._reference_busy
            or self._export_busy
            or self._range_busy
            or self._attributes_busy
        ):
            return
        self._invalidate_preview()
        self._reference_busy = True
        epoch = self._reference_epoch
        selection_key = self._selection_key()
        selections = self.selections
        include_images = self.include_images.isChecked()
        facade = self.facade
        set_status(self.status, "info", "正在生成完整选题参考…")
        self._update_actions()

        def ready(value):
            if epoch != self._reference_epoch or selection_key != self._selection_key():
                return
            self._reference_busy = False
            if (
                not self._valid_reference(value)
                or value["include_images"] is not include_images
                or value["selections"] != selections
            ):
                self._reference_failed(
                    epoch, "选题参考暂时无法生成，请重新核对所选题目。"
                )
                return
            self._preview_reference = deepcopy(value)
            self._preview_selection = selection_key
            missing = [
                warning
                for warning in value.get("warnings", [])
                if warning and warning not in value["materials"]
            ]
            self.preview.setPlainText(
                value["materials"]
                + ("\n\n待核对提醒：\n" + "\n".join(missing) if missing else "")
                + "\n\n带入方式："
                + ("文字与原图" if include_images else "仅文字（不带原图）")
                + f"\n将带入 {len(value['image_assets'])} 张原图；已有图片保留，备课最多 {MAX_IMAGES} 张。"
                + "\n模型只收到图片说明，不会看到图片像素。"
                + (
                    "\n图片待处理：\n" + "\n".join(value["image_issues"])
                    if value["image_issues"]
                    else ""
                )
            )
            self.tabs.setCurrentIndex(2)
            set_status(
                self.status,
                "success" if self._reference_can_import() else "attention",
                f"已生成 {len(selections)} 道题的完整参考。请核对后确认；确认时会重新读取并比较。"
                if self._reference_can_import()
                else "原图存在未解决项或数量超限，本次不能完整带入。请调整选题，或取消勾选原图后重新预览。",
            )
            self._update_actions()

        self._submit(
            "预览 Word 选题",
            lambda: facade.word_question_reference(
                selections, include_images=include_images
            ),
            ready,
            lambda message: self._reference_failed(epoch, message),
        )

    def _reference_failed(self, epoch, message):
        if epoch != self._reference_epoch:
            return
        self._reference_busy = False
        self._preview_reference = None
        self._preview_selection = ()
        set_status(self.status, "error", message)
        self._update_actions()

    def _confirm(self) -> None:
        if (
            self._preview_reference is None
            or not self._reference_can_import()
            or self._preview_selection != self._selection_key()
            or self._reference_busy
            or self._export_busy
            or self._range_busy
            or self._attributes_busy
        ):
            return
        epoch = self._reference_epoch
        previous = deepcopy(self._preview_reference)
        selections = self.selections
        include_images = self.include_images.isChecked()
        selection_key = self._selection_key()
        facade = self.facade
        self._reference_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在再次核对所选题目和完整参考…")

        def ready(current):
            if epoch != self._reference_epoch or selection_key != self._selection_key():
                return
            self._reference_busy = False
            if not self._valid_reference(current) or current != previous:
                self._invalidate_preview()
                set_status(
                    self.status,
                    "attention",
                    "选题内容已变化，请刷新目录并重新预览后确认。",
                )
                self._update_actions()
                return
            self.reference = deepcopy(current)
            self.preparation_reference = self.reference
            self._finish_dialog(QDialog.DialogCode.Accepted)

        self._submit(
            "确认 Word 选题参考",
            lambda: facade.word_question_reference(
                selections, include_images=include_images
            ),
            ready,
            lambda message: self._reference_failed(epoch, message),
        )

    def _preview_for_basket(self) -> None:
        if not self.basket_preview_button.isEnabled():
            return
        from .assembly_page import MixedPaperPreviewDialog

        self._basket_preview_key = ()
        if self._basket_preview_dialog is not None:
            self._basket_preview_dialog.reject()
        selection_key = self._selection_key()
        sections, images, blockers = [], {}, []

        def blocks_for(question, blocks):
            result = []
            for block in blocks:
                result.append({"kind": "text", "text": _text(block.get("text"))})
                if not block.get("assets") and any(marker in _text(block.get("text")) for marker in (
                    "【待查看原文：图片或图形】", "【待查看原文：图片或旧式图形】", "【待查看原文：嵌入对象或旧公式】"
                )):
                    blockers.append("所选原文有未能显示的图片或旧式对象，请先核对并修订来源。")
                for warning in block.get("warnings", []):
                    if isinstance(warning, str):
                        result.append({"kind": "text", "text": "原文提示：" + warning})
                for asset in block.get("assets", []):
                    token = f"selected-image-{len(images)}"
                    images[token] = (question["key"], question["revision"], asset.get("asset_id"))
                    result.append({"kind": "image", "image_id": token, "caption_zh": _text(asset.get("label")) or "Word 来源原图"})
            return result

        for chosen in self.selections:
            question = deepcopy(self._items[chosen["key"]])
            student = blocks_for(question, list(question.get("context_blocks", [])) + list(question.get("question_blocks", [])))
            answers = blocks_for(question, question.get("answer_blocks", []))
            sections.append({
                "kind": "word_question", "title_zh": question.get("title_zh") or question.get("title") or "Word 完整题",
                "source_zh": question.get("source_name", "Word 原文"), "points": chosen["points"],
                "student_blocks": student,
                "teacher_blocks": student + [{"kind": "text", "text": "参考答案与解析（来源原文）"}] + answers,
            })
        facade = self.facade
        dialog = MixedPaperPreviewDialog(
            {"title": "Word 选题 · 入统一题篮前预览", "sections": sections, "show_question_scores": False, "blockers": blockers},
            self.tasks, lambda key: facade.word_question_image(*images[key]), self,
        )
        self._basket_preview_dialog = dialog

        def confirmed():
            if self._closed or selection_key != self._selection_key() or self._basket_preview_dialog is not dialog:
                return
            self._basket_preview_key = selection_key
            dialog.mark_confirmed()
            set_status(self.status, "success", "已查看本次完整图文；返回后可明确点击加入统一题篮。")
            self._update_actions()

        dialog.preview_confirmed.connect(confirmed)
        dialog.show()
        self._update_actions()

    def _add_to_basket(self) -> None:
        if not self.basket_add_button.isEnabled() or self._basket_preview_key != self._selection_key():
            return
        selections, selection_key = deepcopy(self.selections), self._selection_key()
        self._basket_busy = True
        self._update_actions()
        facade = self.facade

        def ready(count):
            self._basket_busy = False
            if type(count) is not int or count < 0:
                failed("题篮保存结果无法确认，请刷新统一题篮。")
                return
            self.basket_changed.emit(count)
            if selection_key == self._selection_key():
                set_status(self.status, "success", f"已加入统一题篮（共 {count} 项）；跨来源勾选保留，可到组卷页编排。")
            self._update_actions()

        def failed(message):
            self._basket_busy = False
            self._basket_preview_key = ()
            set_status(self.status, "error", message)
            self._update_actions()

        self._submit("加入统一题篮", lambda: facade.add_word_questions_to_basket(selections), ready, failed)

    def _export(self) -> None:
        if not self.export_button.isEnabled():
            return
        selections = self.selections
        title = self.export_title.text().strip() or "Word 选题练习"
        show_scores = self.show_student_scores.isChecked()
        facade = self.facade
        self._export_busy = True
        self._export_paths = {}
        self.student_button.hide()
        self.teacher_button.hide()
        self.export_note.hide()
        self.export_note.clear()
        self._update_actions()
        set_status(self.status, "info", "正在本机导出学生版与教师版练习…")

        def ready(value):
            self._export_busy = False
            if not isinstance(value, dict) or not all(
                isinstance(value.get(key), str)
                for key in ("student_path", "teacher_path")
            ):
                failed("练习导出结果不完整，请重试。")
                return
            self._export_paths = dict(value)
            self.student_button.show()
            self.teacher_button.show()
            warnings = [
                warning
                for warning in value.get("warnings", ())
                if isinstance(warning, str) and warning
            ]
            self.export_note.setText(
                "导出提醒：\n" + "\n".join(warnings) if warnings else ""
            )
            self.export_note.setVisible(bool(warnings))
            set_status(
                self.status,
                "attention" if warnings else "success",
                f"已导出 {len(selections)} 题的学生版与教师版练习，请检查图片、题目范围及参考答案。",
            )
            self._update_actions()

        def failed(message):
            self._export_busy = False
            set_status(self.status, "error", message)
            self._update_actions()

        self._submit(
            "导出 Word 选题练习",
            lambda: facade.word_question_export(
                title, selections, show_student_scores=show_scores
            ),
            ready,
            failed,
        )

    def _open_export(self, key: str) -> None:
        value = self._export_paths.get(key)
        if not value or not Path(value).is_file():
            set_status(self.status, "attention", "导出的文件暂时不可用，请重新导出。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(value).resolve())))

    def _step(self, direction: int) -> None:
        index = self.question_list.currentRow() + direction
        if 0 <= index < self.question_list.count():
            self.question_list.setCurrentRow(index)

    def _update_actions(self) -> None:
        count = len(self._selected)
        basket_available = callable(getattr(self.facade, "add_word_questions_to_basket", None))
        basket_idle = not (self._basket_busy or self._catalog_busy or self._reference_busy or self._export_busy or self._range_busy or self._attributes_busy)
        basket_ready = count > 0 and all(self._items.get(key, {}).get("export_ready") is True for key in self._selected)
        self.basket_preview_button.setEnabled(basket_available and basket_idle and basket_ready)
        self.basket_add_button.setEnabled(basket_available and basket_idle and basket_ready and bool(self._basket_preview_key) and self._basket_preview_key == self._selection_key())
        self.lesson_suggestion_button.setEnabled(
            bool(self._lesson_suggestions)
            and not self._catalog_busy
            and not self._reference_busy
            and not self._export_busy
            and not self._range_busy
            and not self._attributes_busy
        )
        self.selection_count.setText(f"已选 {count} 题；筛选或切换来源会保留勾选。")
        self.question_list.setEnabled(not self._catalog_busy)
        self.reload_button.setEnabled(not self._catalog_busy and not self._export_busy)
        self.reload_button.setEnabled(
            self.reload_button.isEnabled() and not self._attributes_busy
        )
        self.attributes_button.setEnabled(
            self._current_key in self._items
            and not self._attributes_busy
            and not self._range_busy
            and not self._catalog_busy
            and not self._export_busy
        )
        self.range_button.setEnabled(
            self._current_key in self._items
            and not self._range_busy
            and not self._catalog_busy
            and not self._export_busy
            and not self._attributes_busy
        )
        self.preview_button.setEnabled(
            count > 0
            and not self._catalog_busy
            and not self._reference_busy
            and not self._export_busy
            and not self._range_busy
            and not self._attributes_busy
        )
        self.import_button.setEnabled(
            self._preview_reference is not None
            and self._reference_can_import()
            and self._preview_selection == self._selection_key()
            and not self._reference_busy
            and not self._catalog_busy
            and not self._export_busy
            and not self._range_busy
            and not self._attributes_busy
        )
        self.clear_button.setEnabled(count > 0)
        self.show_student_scores.setEnabled(not self._export_busy)
        exportable = count > 0 and all(
            self._items.get(key, {}).get("export_ready") is True
            for key in self._selected
        )
        self.export_button.setEnabled(
            exportable
            and not self._unified_basket_available
            and not self._export_busy
            and not self._catalog_busy
            and not self._reference_busy
            and not self._range_busy
            and not self._attributes_busy
            and callable(getattr(self.facade, "word_question_export", None))
        )
        self.export_button.setToolTip(
            "所选题目中有暂不能导出的条目，请先核对其分段与缺口。"
            if count and not exportable
            else "在本机生成学生版与教师版练习"
        )
        row = self.question_list.currentRow()
        self.previous_button.setEnabled(row > 0)
        self.next_button.setEnabled(0 <= row < self.question_list.count() - 1)
        if self._basket_busy:
            for control in (self.question_list, self.points, self.reload_button, self.range_button, self.attributes_button, self.clear_button, self.preview_button, self.import_button, self.export_button):
                control.setEnabled(False)

    def _finish_dialog(self, result: QDialog.DialogCode) -> None:
        if self._closed:
            return
        if self._selection_save_busy or self._selection_to_save is not None:
            self._closing_result = result
            self._save_timer.stop()
            self._persist_selection()
            set_status(self.status, "info", "正在保存本次勾选…")
            return
        self._closed = True
        if self._basket_preview_dialog is not None:
            self._basket_preview_dialog.reject()
            self._basket_preview_dialog = None
        self._search_timer.stop()
        self._save_timer.stop()
        self._detail_epoch += 1
        for task_id in tuple(self._jobs.values()):
            if task_id:
                self.tasks.cancel(task_id)
        self.done(result)

    def reject(self) -> None:
        if self._export_busy or self._range_busy or self._attributes_busy or self._basket_busy:
            set_status(
                self.status,
                "attention",
                "本地导出、题目范围或标签任务尚未完成，请稍候再关闭。",
            )
            return
        self._finish_dialog(QDialog.DialogCode.Rejected)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._export_busy or self._range_busy or self._attributes_busy or self._basket_busy:
            event.ignore()
            set_status(
                self.status,
                "attention",
                "本地导出、题目范围或标签任务尚未完成，请稍候再关闭。",
            )
            return
        self._finish_dialog(QDialog.DialogCode.Rejected)
        event.accept() if self._closed else event.ignore()

    def resizeEvent(self, event: QResizeEvent) -> None:
        narrow = event.size().width() < 760
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([210, 370] if narrow else [380, 770])
        self.action_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        self.export_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        super().resizeEvent(event)
        QTimer.singleShot(0, self._scale_images)


__all__ = ["WordQuestionDialog"]
