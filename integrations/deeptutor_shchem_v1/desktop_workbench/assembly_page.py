from __future__ import annotations

"""Native Qt widgets for the teacher-first paper assembly workflow.

The page is intentionally composed from ordinary Qt Widgets.  It does not
embed the previous browser prototype: the directory, continuous paper view and
current-item inspector are independent, keyboard-focusable widgets that share
the pure :class:`PaperComposerModel` projection.
"""

from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable, Mapping

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import DesktopFacadeError, PaperPreview
from .components import CardFrame, page_scroll, section_title
from .paper_composer import (
    ANSWER_STATUS_LABELS,
    ComposerQuestion,
    ComposerTheme,
    PaperComposerModel,
    answer_status,
    difficulty_label,
    response_label,
)
from .tasks import DesktopTaskBridge


def _label(text: str, object_name: str | None = None) -> QLabel:
    value = QLabel(text)
    if object_name:
        value.setObjectName(object_name)
    value.setWordWrap(True)
    value.setMinimumWidth(0)
    # Ignore unwrapped text width when calculating the page minimum.  Labels
    # still expand to available space and word-wrap, but cannot push a 375px
    # native window into a hidden horizontal canvas.
    value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return value


def _metric(value: Any, unit: str = "") -> str:
    if value is None:
        return "待确认"
    return f"{value}{unit}"


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, Mapping):
        value = value.get("value") or value.get("score") or value.get("minutes")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _quiet_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("QuietButton")
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    return button


def _menu_button(parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setText("⋯")
    button.setToolTip("更多操作")
    button.setAccessibleName("更多操作")
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button


def normalize_preview_projection(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Adapt the formal blueprint preview DTO to the Qt paper renderer.

    The service-side blueprint keeps the required ``theme_groups →
    printed_questions → atomic_rows`` hierarchy, while the first native draft
    used a friendlier ``themes → questions`` shape.  The adapter accepts both
    without flattening away the printed/atomic relationship; the flattened
    list is only a convenience for the compact row renderer.
    """

    if not isinstance(value, Mapping):
        return {}
    result = dict(value)
    cover = value.get("cover") if isinstance(value.get("cover"), Mapping) else {}
    info = value.get("exam_information") if isinstance(value.get("exam_information"), Mapping) else {}
    stats = value.get("stats") if isinstance(value.get("stats"), Mapping) else {}
    if not result.get("title"):
        result["title"] = cover.get("title_zh") or "整卷预览"
    if not result.get("mode_label"):
        result["mode_label"] = "模拟考试" if value.get("mode") == "mock_exam" else "平时练习"
    raw_group_count = len(value.get("theme_groups", [])) if isinstance(value.get("theme_groups"), list) else 0
    result["stats"] = {
        "theme_count": stats.get("theme_count") or info.get("theme_count") or raw_group_count,
        "question_count": stats.get("question_count") or info.get("atomic_count") or 0,
        "total_score": stats.get("total_score") or info.get("total_score") or 0,
        "total_time_minutes": stats.get("total_time_minutes") or info.get("estimated_duration_minutes") or info.get("scheduled_duration_minutes") or 0,
    }
    raw_themes = value.get("themes")
    if isinstance(raw_themes, list):
        result["themes"] = [
            _normalize_theme_dict(item, index)
            for index, item in enumerate(raw_themes, start=1)
            if isinstance(item, Mapping)
        ]
        return result
    raw_groups = value.get("theme_groups")
    if not isinstance(raw_groups, list):
        return result
    themes: list[dict[str, Any]] = []
    for index, group in enumerate(raw_groups, start=1):
        if not isinstance(group, Mapping):
            continue
        theme_info = group.get("theme") if isinstance(group.get("theme"), Mapping) else {}
        textbook = group.get("textbook")
        textbook_label = (
            textbook.get("display_zh") or textbook.get("section_zh") or textbook.get("label_zh")
            if isinstance(textbook, Mapping)
            else group.get("textbook_section_zh")
        )
        source_value = group.get("source")
        if isinstance(source_value, Mapping):
            source_value = source_value.get("display_zh") or source_value.get("label_zh") or "来源待确认"
        difficulty_value = group.get("difficulty")
        if isinstance(difficulty_value, Mapping):
            difficulty_value = difficulty_value.get("display_zh") or difficulty_value.get("label_zh") or "难度待确认"
        printed = group.get("printed_questions")
        printed_rows: list[dict[str, Any]] = []
        flat_rows: list[dict[str, Any]] = []
        if isinstance(printed, list):
            for printed_index, printed_item in enumerate(printed, start=1):
                if not isinstance(printed_item, Mapping):
                    continue
                atomic_rows = printed_item.get("atomic_rows")
                normalized_atomic: list[dict[str, Any]] = []
                if isinstance(atomic_rows, list):
                    for atomic in atomic_rows:
                        if not isinstance(atomic, Mapping):
                            continue
                        row = _normalize_preview_row(
                            atomic,
                            len(flat_rows) + 1,
                            theme_number=index,
                        )
                        normalized_atomic.append(row)
                        flat_rows.append(row)
                printed_rows.append(
                    {
                        "display_number": printed_item.get("display_number") or f"第{printed_index}问",
                        "title": printed_item.get("title") or printed_item.get("preview_text") or "题目",
                        "atomic_rows": normalized_atomic,
                    }
                )
        compact = group.get("compact_rows")
        # ``compact_rows`` is often a convenience projection of the same
        # atomic rows.  Prefer the explicit printed→atomic hierarchy and only
        # use compact rows when no printed rows were provided.
        if not printed_rows and isinstance(compact, list):
            for row_value in compact:
                if isinstance(row_value, Mapping):
                    row = _normalize_preview_row(
                        row_value,
                        len(flat_rows) + 1,
                        theme_number=index,
                    )
                    flat_rows.append(row)
        shared = group.get("shared_materials")
        if not isinstance(shared, list):
            shared = []
        themes.append(
            {
                "display_number": group.get("display_number") or f"第{index}题",
                "title": _display_value(
                    group.get("title_zh") or group.get("title") or theme_info.get("title"),
                    "display_zh",
                    "label_zh",
                    fallback="未命名大题",
                ),
                "source": _display_value(
                    group.get("source_zh") or source_value,
                    "display_zh",
                    "label_zh",
                    fallback="来源待确认",
                ),
                "chapter": _display_value(
                    textbook_label or group.get("chapter"),
                    "display_zh",
                    "section_zh",
                    "label_zh",
                    fallback="教材章节待确认",
                ),
                "score": _number_or_none(group.get("score", group.get("total_score"))),
                "time_minutes": _number_or_none(
                    group.get("time_minutes", group.get("estimated_time_minutes"))
                ),
                "difficulty": difficulty_value or "难度待确认",
                "question_count": group.get("question_count") or len(flat_rows),
                "shared_materials": [
                    {
                        **dict(item),
                        "text": _display_value(
                            item,
                            "text",
                            "display_zh",
                            "summary_zh",
                            "summary",
                            fallback="共享材料图片待展开。",
                        ),
                    }
                    for item in shared
                    if isinstance(item, Mapping)
                ],
                "printed_questions": printed_rows,
                "questions": flat_rows,
            }
        )
    result["themes"] = themes
    return result


def _normalize_preview_row(
    value: Mapping[str, Any],
    index: int,
    *,
    theme_number: int | None = None,
) -> dict[str, Any]:
    source_number = value.get("display_number") or value.get("source_display_number")
    answer_space = value.get("answer_space")
    if isinstance(answer_space, Mapping):
        lines = answer_space.get("lines", 0)
    else:
        lines = answer_space if isinstance(answer_space, int) else None
    if not isinstance(lines, int) or lines < 0:
        lines = None
    eligibility = value.get("answer_eligibility")
    if isinstance(eligibility, Mapping):
        answer_label = (
            eligibility.get("display_zh")
            or eligibility.get("label_zh")
            or eligibility.get("status_zh")
            or ("参考答案可用" if eligibility.get("eligible") is True else "参考答案待教师核对")
        )
    else:
        answer_label = str(eligibility or "参考答案待教师核对")
    section = value.get("textbook_section")
    if isinstance(section, Mapping):
        section = (
            section.get("display_zh")
            or section.get("section_zh")
            or section.get("label_zh")
        )
    raw_score = value.get("score")
    if isinstance(raw_score, Mapping):
        raw_score = raw_score.get("value") or raw_score.get("score")
    score = raw_score if isinstance(raw_score, (int, float)) else None
    raw_difficulty = value.get("difficulty")
    difficulty = _display_value(
        raw_difficulty,
        "display_zh",
        "label_zh",
        "value_zh",
        "code",
        "value",
        fallback="难度待确认",
    )
    difficulty = difficulty_label(difficulty)
    raw_response = value.get("response_type")
    response = _display_value(
        raw_response,
        "display_zh",
        "label_zh",
        "value_zh",
        "code",
        "value",
        fallback=_display_value(
            value.get("response_requirement_zh"), fallback="题型待确认"
        ),
    )
    response = response_label(response)
    if isinstance(source_number, str) and source_number.strip():
        display_number = source_number.strip()
    else:
        display_number = f"第{index}问"
    if theme_number is not None and not ("题·" in display_number or "题·" in str(display_number)):
        display_number = f"第{theme_number}题·{display_number}"
    raw_answer = value.get("answer") or value.get("answer_text")
    answer_text = _display_value(
        raw_answer,
        "display_zh",
        "text_zh",
        "value_zh",
        "reference_answer",
        fallback="暂无参考答案",
    )
    return {
        **dict(value),
        "source_display_number": source_number,
        "display_number": display_number,
        "response_type": response,
        "score": score,
        "section": section or value.get("section") or "教材章节待确认",
        "difficulty": difficulty,
        "answer_space": lines,
        "stem": value.get("preview_text_zh") or value.get("preview_text") or value.get("stem") or value.get("response_requirement_zh") or "题目图片待展开。",
        "answer_label": answer_label,
        "answer": answer_text,
    }


def _display_value(value: Any, *keys: str, fallback: str = "") -> str:
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return fallback
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _normalize_theme_dict(value: Mapping[str, Any], index: int) -> dict[str, Any]:
    theme = dict(value)
    source = _display_value(value.get("source"), "display_zh", "label_zh", fallback="来源待确认")
    textbook = _display_value(value.get("textbook"), "display_zh", "section_zh", "label_zh", fallback="教材章节待确认")
    difficulty = _display_value(value.get("difficulty"), "display_zh", "label_zh", fallback="难度待确认")
    rows = value.get("questions") if isinstance(value.get("questions"), list) else []
    normalized_rows = [
        _normalize_preview_row(row, row_index, theme_number=index)
        for row_index, row in enumerate(rows, start=1)
        if isinstance(row, Mapping)
    ]
    printed_rows: list[dict[str, Any]] = []
    raw_printed = value.get("printed_questions")
    if isinstance(raw_printed, list):
        for printed_index, printed in enumerate(raw_printed, start=1):
            if not isinstance(printed, Mapping):
                continue
            atomic = printed.get("atomic_rows")
            atomic_rows = [
                _normalize_preview_row(
                    row,
                    len(normalized_rows) + row_index,
                    theme_number=index,
                )
                for row_index, row in enumerate(atomic or [], start=1)
                if isinstance(row, Mapping)
            ] if isinstance(atomic, list) else []
            printed_rows.append(
                {
                    **dict(printed),
                    "display_number": printed.get("display_number") or f"题目{printed_index}",
                    "atomic_rows": atomic_rows,
                }
            )
            if not normalized_rows:
                normalized_rows.extend(atomic_rows)
    theme.update(
        {
            "display_number": value.get("display_number") or f"第{index}题",
            "title": _display_value(value.get("title"), "display_zh", "label_zh", fallback="未命名大题"),
            "source": source,
            "chapter": _display_value(value.get("chapter"), fallback=textbook),
            "difficulty": difficulty,
            "score": _number_or_none(value.get("score")),
            "time_minutes": _number_or_none(value.get("time_minutes")),
            "questions": normalized_rows,
            "question_count": value.get("question_count") or len(normalized_rows),
            "printed_questions": printed_rows,
            "shared_materials": [
                {
                    **dict(item),
                    "text": _display_value(item, "text", "display_zh", "summary_zh", fallback="共享材料图片待展开。"),
                }
                for item in (value.get("shared_materials") or [])
                if isinstance(item, Mapping)
            ],
        }
    )
    return theme


class ThemeDirectory(QListWidget):
    """A natural-numbered, keyboard-operable theme directory."""

    theme_selected = Signal(str)
    order_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ThemeDirectory")
        self.setAccessibleName("大题目录")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setWordWrap(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setMinimumWidth(0)
        self.currentItemChanged.connect(self._selection_changed)

    def set_themes(self, themes: list[ComposerTheme], selected_key: str | None) -> None:
        current = selected_key
        self.blockSignals(True)
        self.clear()
        for index, theme in enumerate(themes, start=1):
            item = QListWidgetItem(
                f"第{index}题  {theme.title}\n{_metric(theme.score, ' 分')} · "
                f"{theme.question_count} 小问"
            )
            item.setData(Qt.ItemDataRole.UserRole, theme.key)
            item.setToolTip("拖动整道大题调整顺序；编号会自动更新。")
            self.addItem(item)
        if self.count():
            row = next(
                (
                    index
                    for index in range(self.count())
                    if self.item(index).data(Qt.ItemDataRole.UserRole) == current
                ),
                0,
            )
            self.setCurrentRow(row)
        self.blockSignals(False)

    def _selection_changed(self, item: QListWidgetItem | None, _old: QListWidgetItem | None) -> None:
        if item is not None:
            key = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(key, str):
                self.theme_selected.emit(key)

    def _emit_order(self) -> None:
        keys = [
            self.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.count())
        ]
        self.order_changed.emit([key for key in keys if isinstance(key, str)])

    def dropEvent(self, event: Any) -> None:  # QDropEvent varies between Qt builds
        self.blockSignals(True)
        super().dropEvent(event)
        self.blockSignals(False)
        self._emit_order()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self.currentRow()
                target = row - 1 if event.key() == Qt.Key.Key_Up else row + 1
                if row >= 0 and 0 <= target < self.count():
                    selected_key = self.item(row).data(Qt.ItemDataRole.UserRole)
                    self.blockSignals(True)
                    item = self.takeItem(row)
                    self.insertItem(target, item)
                    self.setCurrentRow(target)
                    self.blockSignals(False)
                    # Restoring the item explicitly avoids Qt's transient
                    # currentItemChanged emission selecting the last row while
                    # QListWidget is being rearranged.
                    if isinstance(selected_key, str):
                        for index in range(self.count()):
                            if self.item(index).data(Qt.ItemDataRole.UserRole) == selected_key:
                                self.blockSignals(True)
                                self.setCurrentRow(index)
                                self.blockSignals(False)
                                break
                    self._emit_order()
                    event.accept()
                    return
        super().keyPressEvent(event)


class QuestionRowWidget(QFrame):
    """Compact one-line question row with an opt-in detail disclosure."""

    selected = Signal(str, str)
    edit_requested = Signal(str, str)
    action_requested = Signal(str, str, str)
    expanded_changed = Signal(str, bool)

    def __init__(
        self,
        theme_key: str,
        question: ComposerQuestion,
        number: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme_key = theme_key
        self.question = question
        self.number = number
        self.setObjectName("QuestionRow")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.disclosure = QToolButton()
        self.disclosure.setCheckable(True)
        self.disclosure.setChecked(False)
        self.disclosure.setArrowType(Qt.ArrowType.RightArrow)
        self.disclosure.setToolTip("展开题面与答案")
        self.disclosure.setAccessibleName(f"{number} 题面展开")
        self.disclosure.clicked.connect(self._toggle)
        header.addWidget(self.disclosure)
        self.number_label = _label(number)
        self.number_label.setObjectName("QuestionNumber")
        self.number_label.setMinimumWidth(0)
        header.addWidget(self.number_label)
        self.response_label = _label(question.response_type)
        self.response_label.setObjectName("QuestionResponse")
        self.response_label.setMinimumWidth(0)
        header.addWidget(self.response_label)
        self.dependency_label = _label(question.dependency)
        self.dependency_label.setObjectName("MutedLabel")
        header.addWidget(self.dependency_label, 1)
        self.meta_label = _label("")
        self.meta_label.setObjectName("QuestionMeta")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.meta_label)
        self.menu_button = _menu_button(self)
        self._build_menu()
        header.addWidget(self.menu_button)
        root.addLayout(header)

        self.details = QFrame()
        self.details.setObjectName("QuestionDetails")
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(30, 4, 8, 4)
        detail_layout.setSpacing(6)
        self.stem_label = _label("", "QuestionStem")
        detail_layout.addWidget(self.stem_label)
        self.options_label = _label("")
        self.options_label.setObjectName("QuestionOptions")
        detail_layout.addWidget(self.options_label)
        answer_row = QHBoxLayout()
        self.space_label = _label("")
        self.space_label.setObjectName("MutedLabel")
        answer_row.addWidget(self.space_label, 1)
        self.crop_label = _label("")
        self.crop_label.setObjectName("MutedLabel")
        answer_row.addWidget(self.crop_label)
        detail_layout.addLayout(answer_row)
        self.answer_label = _label("")
        self.answer_label.setObjectName("QuestionAnswer")
        detail_layout.addWidget(self.answer_label)
        self.analysis_label = _label("")
        self.analysis_label.setObjectName("MutedLabel")
        detail_layout.addWidget(self.analysis_label)
        self.details.setVisible(False)
        root.addWidget(self.details)
        self.refresh(question, number)

    def _build_menu(self) -> None:
        menu = QMenu(self.menu_button)
        menu.setAccessibleName("小问操作")
        edit = QAction("编辑当前小问", menu)
        edit.triggered.connect(lambda: self.edit_requested.emit(self.theme_key, self.question.key))
        up = QAction("上移小问", menu)
        up.triggered.connect(lambda: self.action_requested.emit(self.theme_key, self.question.key, "move_up"))
        down = QAction("下移小问", menu)
        down.triggered.connect(lambda: self.action_requested.emit(self.theme_key, self.question.key, "move_down"))
        score = QAction("修改分值与答题空间", menu)
        score.triggered.connect(lambda: self.edit_requested.emit(self.theme_key, self.question.key))
        replace = QAction("换一个小问", menu)
        replace.triggered.connect(lambda: self.action_requested.emit(self.theme_key, self.question.key, "replace"))
        remove = QAction("删除小问", menu)
        remove.triggered.connect(lambda: self.action_requested.emit(self.theme_key, self.question.key, "delete"))
        for action in (edit, up, down, score, replace, remove):
            menu.addAction(action)
        self.menu_button.setMenu(menu)

    def _toggle(self, expanded: bool | None = None) -> None:
        if expanded is None:
            expanded = not self.details.isVisible()
        self.details.setVisible(expanded)
        self.disclosure.setChecked(bool(expanded))
        self.disclosure.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.disclosure.setToolTip("收起题面与答案" if expanded else "展开题面与答案")
        self.expanded_changed.emit(self.question.key, bool(expanded))

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.theme_key, self.question.key)
        super().mousePressEvent(event)

    def refresh(self, question: ComposerQuestion, number: str) -> None:
        self.question = question
        self.number = number
        self.number_label.setText(number)
        self.disclosure.setAccessibleName(f"{number} 题面展开")
        self.response_label.setText(question.response_type)
        self.dependency_label.setText(question.dependency)
        answer_text = answer_status(question.answer_status)[0]
        self.meta_label.setText(
            f"{_metric(question.score, ' 分')} · {question.section} · "
            f"{question.difficulty} · {answer_text}"
        )
        self.stem_label.setText(f"题目：{question.stem}")
        self.options_label.setText(
            "选项：" + "；".join(question.options) if question.options else ""
        )
        if question.answer_space is None:
            self.space_label.setText("答题空间：待确认")
        else:
            lines = max(0, int(question.answer_space))
            self.space_label.setText(
                f"答题空间：{'—' if lines == 0 else '□ ' * min(lines, 8) + f'（{lines} 行）'}"
            )
        self.crop_label.setText("题目图片已就绪" if question.crop_available else "题目图片待识别")
        self.answer_label.setText(
            f"参考答案：{question.answer or '当前没有可展示的参考答案。'}\n状态：{answer_text}"
        )
        self.analysis_label.setText(
            f"解析提示：{question.analysis}" if question.analysis else ""
        )


class ThemeCardWidget(QFrame):
    """A first-class theme card; subquestions remain visually subordinate."""

    selected = Signal(str)
    question_selected = Signal(str, str)
    edit_requested = Signal(str, str)
    action_requested = Signal(str, str)
    expanded_changed = Signal(str, bool)
    question_expanded_changed = Signal(str, str, bool)

    def __init__(
        self,
        theme: ComposerTheme,
        theme_number: int,
        numbers: Mapping[str, str],
        *,
        expanded: bool = False,
        preview_mode: bool = False,
        expanded_questions: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.theme_number = theme_number
        self.numbers = numbers
        self.preview_mode = preview_mode
        self.expanded_questions = set(expanded_questions or ())
        self.question_rows: dict[str, QuestionRowWidget] = {}
        self.setObjectName("ThemeCard")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 13, 14, 13)
        root.setSpacing(9)

        heading = QHBoxLayout()
        heading.setSpacing(7)
        self.title_button = QPushButton(f"第{theme_number}题　{theme.title}")
        self.title_button.setObjectName("ThemeTitleButton")
        self.title_button.setFlat(True)
        self.title_button.setStyleSheet("text-align:left; font-weight:700; padding:0;")
        self.title_button.setMinimumWidth(0)
        self.title_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.title_button.setToolTip("编辑当前大题")
        self.title_button.clicked.connect(lambda: self.edit_requested.emit("theme", theme.key))
        heading.addWidget(self.title_button, 1)
        self.menu_button = _menu_button(self)
        self._build_menu()
        heading.addWidget(self.menu_button)
        root.addLayout(heading)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(4)
        self.source_label = _label("")
        self.chapter_label = _label("")
        self.score_label = _label("")
        self.difficulty_label = _label("")
        self.time_label = _label("")
        self.count_label = _label("")
        for value in (
            self.source_label,
            self.chapter_label,
            self.score_label,
            self.difficulty_label,
            self.time_label,
            self.count_label,
        ):
            value.setObjectName("ThemeMeta")
        metrics.addWidget(self.source_label, 0, 0, 1, 2)
        metrics.addWidget(self.chapter_label, 1, 0)
        metrics.addWidget(self.score_label, 1, 1)
        metrics.addWidget(self.time_label, 2, 0)
        metrics.addWidget(self.difficulty_label, 2, 1)
        metrics.addWidget(self.count_label, 3, 0, 1, 2)
        root.addLayout(metrics)
        self.shared_summary_label = _label("")
        self.shared_summary_label.setObjectName("SharedSummary")
        root.addWidget(self.shared_summary_label)

        self.disclosure = QPushButton()
        self.disclosure.setObjectName("LinkButton")
        self.disclosure.setFlat(True)
        self.disclosure.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.disclosure.clicked.connect(self._toggle)
        root.addWidget(self.disclosure, 0, Qt.AlignmentFlag.AlignLeft)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        shared = QFrame()
        shared.setObjectName("SharedMaterial")
        shared_layout = QVBoxLayout(shared)
        shared_layout.setContentsMargins(10, 8, 10, 8)
        shared_layout.addWidget(_label("共享材料 · 本大题仅显示一次", "CardTitle"))
        self.shared_text_label = _label("")
        shared_layout.addWidget(self.shared_text_label)
        body_layout.addWidget(shared)
        table_head = _label("小问　　题型　　分值 · 教材章节 · 难度 · 参考答案状态", "MutedLabel")
        body_layout.addWidget(table_head)
        for question in theme.questions:
            row = QuestionRowWidget(theme.key, question, numbers.get(question.key, "第?问"), self.body)
            row.selected.connect(self.question_selected)
            row.edit_requested.connect(
                lambda theme_key, question_key: self.edit_requested.emit(
                    "question", f"{theme_key}:{question_key}"
                )
            )
            row.action_requested.connect(self._question_action)
            row.expanded_changed.connect(
                lambda question_key, value, theme_key=theme.key: self.question_expanded_changed.emit(
                    theme_key, question_key, value
                )
            )
            if preview_mode:
                row._toggle(True)
            elif f"{theme.key}:{question.key}" in self.expanded_questions:
                row._toggle(True)
            body_layout.addWidget(row)
            self.question_rows[question.key] = row
        self.body.setVisible(expanded or preview_mode)
        root.addWidget(self.body)
        self._refresh()

    def _build_menu(self) -> None:
        menu = QMenu(self.menu_button)
        menu.setAccessibleName("大题操作")
        edit = QAction("编辑当前大题", menu)
        edit.triggered.connect(lambda: self.edit_requested.emit("theme", self.theme.key))
        up = QAction("上移大题", menu)
        up.triggered.connect(lambda: self.action_requested.emit(self.theme.key, "move_up"))
        down = QAction("下移大题", menu)
        down.triggered.connect(lambda: self.action_requested.emit(self.theme.key, "move_down"))
        replace = QAction("换一道大题", menu)
        replace.triggered.connect(lambda: self.action_requested.emit(self.theme.key, "replace"))
        remove = QAction("删除大题", menu)
        remove.triggered.connect(lambda: self.action_requested.emit(self.theme.key, "delete"))
        for action in (edit, up, down, replace, remove):
            menu.addAction(action)
        self.menu_button.setMenu(menu)

    def _question_action(self, theme_key: str, question_key: str, action: str) -> None:
        self.action_requested.emit(f"{theme_key}:{question_key}", action)

    def _toggle(self, _checked: bool = False) -> None:
        visible = not self.body.isVisible()
        self.body.setVisible(visible)
        self.disclosure.setChecked(visible)
        self.expanded_changed.emit(self.theme.key, visible)
        self.disclosure.setText(
            f"收起 {self.theme.question_count} 个小问" if visible else f"查看 {self.theme.question_count} 个小问"
        )

    def _refresh(self) -> None:
        theme = self.theme
        self.title_button.setText(f"第{self.theme_number}题　{theme.title}")
        self.source_label.setText(f"来源：{theme.source}")
        self.chapter_label.setText(f"教材章节：{theme.chapter}")
        self.score_label.setText(f"总分：{_metric(theme.score, ' 分')}")
        self.time_label.setText(f"预计用时：{_metric(theme.time_minutes, ' 分钟')}")
        self.difficulty_label.setText(f"难度：{theme.difficulty}")
        self.count_label.setText(f"小问：{theme.question_count} 个")
        self.shared_summary_label.setText(f"共享材料摘要：{theme.shared_summary}")
        materials = theme.shared_materials
        if materials:
            material_lines = []
            for index, material in enumerate(materials, start=1):
                if not isinstance(material, Mapping):
                    continue
                text = str(
                    material.get("text")
                    or material.get("summary")
                    or "共享材料图片待展开。"
                )
                material_lines.append(f"材料{index}：{text}")
            self.shared_text_label.setText("\n".join(material_lines) or theme.shared_text)
        else:
            self.shared_text_label.setText(theme.shared_text)
        self.disclosure.setText(
            f"收起 {theme.question_count} 个小问"
            if self.body.isVisible()
            else f"查看 {theme.question_count} 个小问"
        )

    def refresh(
        self,
        theme: ComposerTheme,
        theme_number: int,
        numbers: Mapping[str, str],
        expanded_questions: set[str] | None = None,
    ) -> None:
        self.theme = theme
        self.theme_number = theme_number
        self.numbers = numbers
        self.expanded_questions = set(expanded_questions or ()) if expanded_questions is not None else self.expanded_questions
        # Rebuilding rows keeps the projection simple and prevents stale
        # source numbering after a move/delete operation.
        parent_layout = self.body.layout()
        # Keep the shared-material block and the compact-table header.  Only
        # question rows are rebuilt after a reorder/edit.
        while parent_layout.count() > 2:
            item = parent_layout.takeAt(2)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.question_rows.clear()
        for question in theme.questions:
            row = QuestionRowWidget(theme.key, question, numbers.get(question.key, "第?问"), self.body)
            row.selected.connect(self.question_selected)
            row.edit_requested.connect(
                lambda theme_key, question_key: self.edit_requested.emit(
                    "question", f"{theme_key}:{question_key}"
                )
            )
            row.action_requested.connect(self._question_action)
            row.expanded_changed.connect(
                lambda question_key, value, theme_key=theme.key: self.question_expanded_changed.emit(
                    theme_key, question_key, value
                )
            )
            if self.preview_mode:
                row._toggle(True)
            elif f"{theme.key}:{question.key}" in self.expanded_questions:
                row._toggle(True)
            parent_layout.addWidget(row)
            self.question_rows[question.key] = row
        self._refresh()


class ContinuousPaperPreview(QScrollArea):
    """Scrollable, paper-order preview in the centre pane."""

    theme_selected = Signal(str)
    question_selected = Signal(str, str)
    edit_requested = Signal(str, str)
    action_requested = Signal(str, str)
    theme_expanded_changed = Signal(str, bool)
    question_expanded_changed = Signal(str, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContinuousPaperPreview")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(0)
        self.viewport_widget = QWidget()
        self.viewport_widget.setObjectName("PaperPreviewViewport")
        self.viewport_widget.setMinimumWidth(0)
        self.viewport_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.layout = QVBoxLayout(self.viewport_widget)
        self.layout.setContentsMargins(7, 7, 7, 18)
        self.layout.setSpacing(12)
        self.setWidget(self.viewport_widget)
        self.theme_cards: dict[str, ThemeCardWidget] = {}

    def _theme_expanded(self, key: str, expanded: bool) -> None:
        self.theme_expanded_changed.emit(key, expanded)

    def _question_expanded(self, theme_key: str, question_key: str, expanded: bool) -> None:
        self.question_expanded_changed.emit(theme_key, question_key, expanded)

    def _fit_width(self) -> None:
        width = self.viewport().width()
        if width > 0:
            # Explicitly pin the canvas to the visible viewport.  This keeps
            # long Chinese labels wrapping inside the card instead of making a
            # hidden horizontal scrollbar appear on narrow windows.
            self.viewport_widget.setFixedWidth(width)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_width()

    def render(self, model: PaperComposerModel) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.theme_cards.clear()
        if model.mode == "mock_exam":
            cover = CardFrame()
            cover_layout = QVBoxLayout(cover)
            cover_layout.setContentsMargins(14, 13, 14, 13)
            cover_layout.addWidget(_label(model.title.strip() or "未命名模拟考试", "PageTitle"))
            cover_layout.addWidget(_label("模拟考试 · 姓名：________　班级：________", "MutedLabel"))
            cover_layout.addWidget(
                _label(
            f"大题 {len(model.themes)} 道 · {model.total_questions} 个小问 · "
                    f"{_metric(model.total_score, ' 分')} · 预计 {_metric(model.total_time, ' 分钟')}",
                    "MutedLabel",
                )
            )
            self.layout.addWidget(cover)
        else:
            header = CardFrame()
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(14, 13, 14, 13)
            header_layout.addWidget(_label(model.title.strip() or "未命名平时练习", "PageTitle"))
            header_layout.addWidget(
                _label(
                    f"平时练习 · {len(model.themes)} 道大题 · {model.total_questions} 个小问 · "
                    f"预计 {_metric(model.total_time, ' 分钟')}",
                    "MutedLabel",
                )
            )
            self.layout.addWidget(header)
        numbers = model.question_numbers()
        for index, theme in enumerate(model.themes, start=1):
            card = ThemeCardWidget(
                theme,
                index,
                numbers,
                # The editor keeps the compact rows visible but leaves each
                # stem/answer disclosure closed.  The dedicated dialog is the
                # place where the complete paper is expanded for review.
                expanded=True,
                preview_mode=False,
                expanded_questions={
                    f"{theme.key}:{question.key}"
                    for question in theme.questions
                    if f"{theme.key}:{question.key}" in model.expanded_question_keys
                },
                parent=self.viewport_widget,
            )
            card.selected.connect(self.theme_selected)
            card.question_selected.connect(self.question_selected)
            card.edit_requested.connect(self.edit_requested)
            card.action_requested.connect(self.action_requested)
            card.expanded_changed.connect(self._theme_expanded)
            card.question_expanded_changed.connect(self._question_expanded)
            self.layout.addWidget(card)
            self.theme_cards[theme.key] = card
        if not model.themes:
            empty = CardFrame()
            empty_layout = QVBoxLayout(empty)
            empty_layout.addWidget(_label("还没有加入大题", "CardTitle"))
            empty_layout.addWidget(_label("请先到题库加入完整大题；这里会按最终顺序连续预览。"))
            self.layout.addWidget(empty)
        self.layout.addStretch(1)
        self._fit_width()


class PaperInspector(QWidget):
    """Edit only the selected theme or question."""

    changed = Signal(str, str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PaperInspector")
        self.setMinimumWidth(0)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self.context = _label("从左侧目录或中间预览选择当前项。", "MutedLabel")
        root.addWidget(self.context)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        root.addWidget(self.stack, 1)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.addWidget(_label("当前未选择编辑项", "CardTitle"))
        empty_layout.addWidget(_label("右侧只保留一个当前编辑表单，避免全卷表单同时堆叠。"))
        empty_layout.addStretch(1)
        self.stack.addWidget(self.empty)
        self.theme_form = self._build_theme_form()
        self.question_form = self._build_question_form()
        self.stack.addWidget(self.theme_form)
        self.stack.addWidget(self.question_form)
        self._theme_key: str | None = None
        self._question_key: str | None = None
        self._loading = False

    @staticmethod
    def _add_field(layout: QVBoxLayout, title: str, widget: QWidget) -> None:
        layout.addWidget(_label(title, "MutedLabel"))
        layout.addWidget(widget)

    def _build_theme_form(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.theme_title = QLineEdit()
        self.theme_source = QPlainTextEdit()
        self.theme_source.setMaximumHeight(66)
        self.theme_chapter = QLineEdit()
        self.theme_time = QSpinBox()
        self.theme_time.setRange(1, 300)
        self.theme_difficulty = QComboBox()
        self.theme_difficulty.addItems(["难度待确认", "基础", "中档", "挑战"])
        self.theme_shared_summary = QPlainTextEdit()
        self.theme_shared_summary.setMaximumHeight(66)
        self.theme_shared_text = QPlainTextEdit()
        self.theme_shared_text.setMaximumHeight(110)
        for title, widget in (
            ("大题标题", self.theme_title),
            ("来源（教师可读）", self.theme_source),
            ("教材章节", self.theme_chapter),
            ("预计用时（分钟）", self.theme_time),
            ("难度", self.theme_difficulty),
            ("共享材料摘要", self.theme_shared_summary),
            ("共享材料正文", self.theme_shared_text),
        ):
            self._add_field(layout, title, widget)
        save = QPushButton("保存大题修改")
        save.clicked.connect(self._emit_theme)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def _build_question_form(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.question_response = QComboBox()
        self.question_response.addItems(
            [
                "题型待确认",
                "单项选择",
                "多项选择",
                "填空",
                "方程式书写",
                "电极反应式",
                "计算",
                "原因解释",
                "实验评价",
                "流程分析",
                "结构推断",
                "同分异构体",
                "合成路线",
            ]
        )
        self.question_score = QSpinBox()
        self.question_score.setRange(1, 30)
        self.question_space = QSpinBox()
        self.question_space.setRange(0, 20)
        self.question_section = QLineEdit()
        self.question_difficulty = QComboBox()
        self.question_difficulty.addItems(["难度待确认", "基础", "中档", "挑战"])
        self.question_answer_status = QComboBox()
        for value, (text, _tone) in ANSWER_STATUS_LABELS.items():
            if self.question_answer_status.findText(text) < 0:
                self.question_answer_status.addItem(text, value)
        self.question_stem = QPlainTextEdit()
        self.question_stem.setMaximumHeight(110)
        self.question_answer = QPlainTextEdit()
        self.question_answer.setMaximumHeight(82)
        self.question_analysis = QPlainTextEdit()
        self.question_analysis.setMaximumHeight(82)
        for title, widget in (
            ("题型", self.question_response),
            ("分值", self.question_score),
            ("答题空间（行）", self.question_space),
            ("教材章节", self.question_section),
            ("难度", self.question_difficulty),
            ("参考答案状态", self.question_answer_status),
            ("题面", self.question_stem),
            ("参考答案", self.question_answer),
            ("解析提示", self.question_analysis),
        ):
            self._add_field(layout, title, widget)
        note = _label("完整题面和答案只在展开或编辑当前小问时显示。", "MutedLabel")
        layout.addWidget(note)
        save = QPushButton("保存小问修改")
        save.clicked.connect(self._emit_question)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def show_theme(self, theme: ComposerTheme, number: int) -> None:
        self._loading = True
        self._theme_key = theme.key
        self._question_key = None
        self.context.setText(f"第{number}题 · 当前编辑大题")
        self.theme_title.setText(theme.title)
        self.theme_source.setPlainText(theme.source)
        self.theme_chapter.setText(theme.chapter)
        self.theme_time.setValue(max(1, theme.time_minutes or 1))
        self._set_combo(self.theme_difficulty, theme.difficulty)
        self.theme_shared_summary.setPlainText(theme.shared_summary)
        self.theme_shared_text.setPlainText(theme.shared_text)
        self.stack.setCurrentWidget(self.theme_form)
        self._loading = False

    def show_question(self, theme: ComposerTheme, question: ComposerQuestion, number: str) -> None:
        self._loading = True
        self._theme_key = theme.key
        self._question_key = question.key
        self.context.setText(f"{number} · {theme.title} · 当前编辑小问")
        self._set_combo(self.question_response, question.response_type)
        self.question_score.setValue(max(1, question.score or 1))
        self.question_space.setValue(max(0, question.answer_space or 0))
        self.question_section.setText(question.section)
        self._set_combo(self.question_difficulty, question.difficulty)
        answer_index = self.question_answer_status.findData(question.answer_status)
        if answer_index < 0:
            answer_index = self.question_answer_status.findText(question.answer_label)
        self.question_answer_status.setCurrentIndex(max(0, answer_index))
        self.question_stem.setPlainText(question.stem)
        self.question_answer.setPlainText(question.answer)
        self.question_analysis.setPlainText(question.analysis)
        self.stack.setCurrentWidget(self.question_form)
        self._loading = False

    def _emit_theme(self) -> None:
        if self._loading or not self._theme_key or self._question_key:
            return
        self.changed.emit(
            "theme",
            self._theme_key,
            {
                "title": self.theme_title.text(),
                "source": self.theme_source.toPlainText(),
                "chapter": self.theme_chapter.text(),
                "time_minutes": self.theme_time.value(),
                "difficulty": self.theme_difficulty.currentText(),
                "shared_summary": self.theme_shared_summary.toPlainText(),
                "shared_text": self.theme_shared_text.toPlainText(),
            },
        )

    def _emit_question(self) -> None:
        if self._loading or not self._theme_key or not self._question_key:
            return
        self.changed.emit(
            "question",
            f"{self._theme_key}:{self._question_key}",
            {
                "response_type": self.question_response.currentText(),
                "score": self.question_score.value(),
                "answer_space": self.question_space.value(),
                "section": self.question_section.text(),
                "difficulty": self.question_difficulty.currentText(),
                "answer_status": str(self.question_answer_status.currentData() or "review"),
                "stem": self.question_stem.toPlainText(),
                "answer": self.question_answer.toPlainText(),
                "analysis": self.question_analysis.toPlainText(),
            },
        )


class PaperPreviewDialog(QDialog):
    """Frozen full-paper preview with an explicit teacher confirmation step."""

    preview_confirmed = Signal()

    def __init__(
        self,
        preview: PaperPreview | Mapping[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preview = preview
        self.confirmed = False
        self.setObjectName("PaperPreviewDialog")
        self.setWindowTitle("整卷预览")
        self.resize(860, 720)
        self.setMinimumSize(340, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(9)
        data = self._projection(preview)
        self._data = data
        title = _label(str(data.get("title") or data.get("title_zh") or "整卷预览"), "PageTitle")
        root.addWidget(title)
        stats = data.get("stats") if isinstance(data.get("stats"), Mapping) else {}
        mode = str(data.get("mode_label") or data.get("mode_zh") or "组卷")
        self.meta = _label(
            f"{mode} · {_metric(stats.get('theme_count', data.get('theme_count')), ' 道大题')} · "
            f"{_metric(stats.get('question_count'), ' 个小问')} · {_metric(stats.get('total_score'), ' 分')} · "
            f"预计 {_metric(stats.get('total_time_minutes'), ' 分钟')}",
            "MutedLabel",
        )
        root.addWidget(self.meta)
        exam_info = data.get("exam_information")
        pagination = data.get("pagination")
        if isinstance(exam_info, Mapping) or isinstance(pagination, Mapping):
            info_lines: list[str] = []
            if isinstance(exam_info, Mapping):
                info_lines.append(
                    "考试设置："
                    + _metric(exam_info.get("scheduled_duration_minutes"), " 分钟")
                    + " · 编号按大题内连续重排"
                )
                scoring = exam_info.get("scoring_rules")
                if isinstance(scoring, Mapping):
                    rule = _display_value(
                        scoring,
                        "selection_rule_zh",
                        "partial_credit_rule_zh",
                        "other_rule_zh",
                        fallback="评分规则按当前模板",
                    )
                    if rule:
                        info_lines.append(f"评分说明：{rule}")
            if isinstance(pagination, Mapping):
                info_lines.append(
                    "分页状态："
                    + str(pagination.get("message_zh") or "物理页码待渲染后确认")
                )
            root.addWidget(_label("\n".join(info_lines), "MutedLabel"))
        switches = QHBoxLayout()
        switches.addWidget(_label("预览版本", "MutedLabel"))
        self.student_button = QRadioButton("学生版")
        self.teacher_button = QRadioButton("教师版（含参考答案状态）")
        self.student_button.setChecked(True)
        self.edition_group = QButtonGroup(self)
        self.edition_group.addButton(self.student_button)
        self.edition_group.addButton(self.teacher_button)
        switches.addWidget(self.student_button)
        switches.addWidget(self.teacher_button)
        switches.addStretch(1)
        root.addLayout(switches)
        self.paper_scroll = QScrollArea()
        self.paper_scroll.setWidgetResizable(True)
        self.paper_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.paper_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.paper_body = QWidget()
        self.paper_layout = QVBoxLayout(self.paper_body)
        self.paper_layout.setContentsMargins(4, 4, 4, 12)
        self.paper_layout.setSpacing(10)
        self.paper_scroll.setWidget(self.paper_body)
        root.addWidget(self.paper_scroll, 1)
        self._render_edition()
        self.teacher_button.toggled.connect(lambda _checked: self._render_edition())

        self.blocker_label = _label("", "MutedLabel")
        self.blocker_label.setObjectName("PreviewStatus")
        root.addWidget(self.blocker_label)
        buttons = QDialogButtonBox()
        self.back_button = buttons.addButton("返回编辑", QDialogButtonBox.ButtonRole.RejectRole)
        self.confirm_button = buttons.addButton("确认本次预览", QDialogButtonBox.ButtonRole.AcceptRole)
        self.back_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self._confirm)
        root.addWidget(buttons)
        blockers = self._blockers(preview)
        if blockers:
            self.blocker_label.setText("预览提示：" + "；".join(blockers))
        else:
            self.blocker_label.setText("当前预览已固定；确认后才可以导出。")
        structural_blockers = [
            item
            for item in blockers
            if any(token in item for token in ("题篮为空", "无法", "无效", "缺少可导出"))
        ]
        if data.get("status") in {"infeasible", "blocked", "invalid"}:
            structural_blockers.append("当前预览状态未通过组卷检查。")
        conflicts = data.get("conflicts")
        if isinstance(conflicts, list) and conflicts:
            structural_blockers.append("题目关联或来源还有未解决的冲突。")
        self._structural_blockers = structural_blockers
        self.confirm_button.setEnabled(not structural_blockers and bool(data.get("themes")))

    @staticmethod
    def _projection(preview: PaperPreview | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(preview, PaperPreview):
            value = getattr(preview, "preview_model", None)
            if isinstance(value, Mapping) and value:
                return normalize_preview_projection(value)
            themes = [
                {
                    "display_number": f"第{index}题",
                    "title": title,
                    "source": "来源待确认",
                    "chapter": "教材章节待确认",
                    "score": None,
                    "time_minutes": None,
                    "difficulty": "难度待确认",
                    "question_count": 0,
                    "shared_materials": [],
                    "questions": [],
                }
                for index, title in enumerate(preview.theme_titles, start=1)
            ]
            return {
                "title": preview.title_zh,
                "mode_label": preview.mode_zh,
                "theme_count": preview.theme_count,
                "themes": themes,
                "stats": {"theme_count": preview.theme_count},
            }
        return normalize_preview_projection(preview)

    @staticmethod
    def _blockers(preview: PaperPreview | Mapping[str, Any]) -> list[str]:
        if isinstance(preview, PaperPreview):
            return [str(item) for item in preview.blockers]
        value = preview.get("blockers") if isinstance(preview, Mapping) else []
        return [str(item) for item in value] if isinstance(value, list) else []

    def _render_edition(self) -> None:
        while self.paper_layout.count():
            item = self.paper_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        themes = self._data.get("themes")
        if not isinstance(themes, list):
            themes = []
        teacher = self.teacher_button.isChecked()
        for index, raw in enumerate(themes, start=1):
            if not isinstance(raw, Mapping):
                continue
            card = CardFrame()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            title = str(raw.get("display_number") or f"第{index}题") + "　" + str(raw.get("title") or "未命名大题")
            layout.addWidget(_label(title, "CardTitle"))
            layout.addWidget(
                _label(
                    f"来源：{raw.get('source', '来源待确认')} · 教材章节：{raw.get('chapter', '教材章节待确认')}\n"
                    f"总分：{_metric(raw.get('score'), ' 分')} · 用时：{_metric(raw.get('time_minutes'), ' 分钟')} · "
                    f"难度：{raw.get('difficulty', '难度待确认')} · 小问：{_metric(raw.get('question_count'), ' 个')}"
                )
            )
            materials = raw.get("shared_materials")
            if isinstance(materials, list) and materials:
                rendered_material = False
                for material_index, material in enumerate(materials, start=1):
                    if not isinstance(material, Mapping):
                        continue
                    # A formal blueprint may mark a cross-theme material as a
                    # reuse.  It is acknowledged once without copying the
                    # source text into every theme.
                    if material.get("render") is False:
                        text = str(material.get("display_zh") or "共享材料已在前一道大题显示。")
                    else:
                        text = str(
                            material.get("text")
                            or material.get("display_zh")
                            or material.get("summary")
                            or "共享材料图片待展开。"
                        )
                    if not rendered_material:
                        layout.addWidget(_label("共享材料（本大题仅出现一次）", "CardTitle"))
                        rendered_material = True
                    layout.addWidget(_label(f"材料{material_index}：{text}"))
            # Formal blueprints retain printed-question groups.  Use those in
            # the full preview when present, falling back to compact rows for
            # the native draft projection.
            printed_questions = raw.get("printed_questions")
            questions = raw.get("questions")
            if isinstance(printed_questions, list) and printed_questions:
                grouped_rows: list[Mapping[str, Any]] = []
                for printed_item in printed_questions:
                    if not isinstance(printed_item, Mapping):
                        continue
                    label = printed_item.get("display_number") or "题目"
                    layout.addWidget(_label(f"{label}　{printed_item.get('title', '')}", "MutedLabel"))
                    atomic_rows = printed_item.get("atomic_rows")
                    if isinstance(atomic_rows, list):
                        grouped_rows.extend(
                            row for row in atomic_rows if isinstance(row, Mapping)
                        )
                questions = grouped_rows
            if isinstance(questions, list):
                for question in questions:
                    if not isinstance(question, Mapping):
                        continue
                    row = QFrame()
                    row.setObjectName("PreviewQuestionRow")
                    row_layout = QVBoxLayout(row)
                    row_layout.setContentsMargins(8, 7, 8, 7)
                    row_layout.addWidget(
                        _label(
                            f"{question.get('display_number', '第?问')}　{question.get('response_type', '题型待确认')}　"
                            f"{_metric(question.get('score'), ' 分')} · {question.get('section', '教材章节待确认')} · "
                            f"{question.get('difficulty', '难度待确认')}",
                            "QuestionMeta",
                        )
                    )
                    row_layout.addWidget(_label(f"题目：{question.get('stem', '题目内容待展开。')}"))
                    row_layout.addWidget(
                        _label(
                            f"答题空间：{_metric(question.get('answer_space'), ' 行')}"
                            + (
                                f" · 参考答案：{question.get('answer', '暂无参考答案')}"
                                if teacher
                                else ""
                            )
                        )
                    )
                    if teacher:
                        row_layout.addWidget(
                            _label(
                                f"参考答案状态：{question.get('answer_label', '参考答案待教师核对')}"
                            )
                        )
                    layout.addWidget(row)
            self.paper_layout.addWidget(card)
        self.paper_layout.addStretch(1)

    def _confirm(self) -> None:
        if not self.confirm_button.isEnabled():
            return
        self.blocker_label.setText("正在保存本次预览确认…")
        self.confirm_button.setEnabled(False)
        # The owner performs the exact-hash approval.  Keeping the dialog open
        # until that call succeeds prevents a stale preview from looking
        # confirmed when the local snapshot has changed in the meantime.
        self.preview_confirmed.emit()

    def mark_confirmed(self) -> None:
        self.confirmed = True
        self.accept()

    def mark_confirmation_failed(self, message: str) -> None:
        self.confirmed = False
        self.blocker_label.setText(message)
        self.confirm_button.setEnabled(not getattr(self, "_structural_blockers", []))


class PaperPage(QWidget):
    """Three-pane native assembly page used by ``TeacherWorkbenchWindow``."""

    preparation_requested = Signal(dict)

    def __init__(
        self,
        facade: Any,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        basket = facade.basket()
        self._basket_signature = PaperComposerModel.basket_signature(basket)
        self.model = PaperComposerModel.from_basket(basket, mode="mock_exam")
        self._restored_draft = False
        state_store = getattr(facade, "state_store", None)
        if state_store is not None and callable(getattr(state_store, "snapshot", None)):
            try:
                snapshot = state_store.snapshot()
                draft = snapshot.get("drafts", {}).get("paper-current") if isinstance(snapshot, Mapping) else None
                payload = draft.get("payload") if isinstance(draft, Mapping) else None
                restored = PaperComposerModel.from_draft_payload(payload, basket) if isinstance(payload, Mapping) else None
                if restored is not None:
                    self.model = restored
                    self._restored_draft = True
            except Exception:
                # A corrupt/legacy personal draft must not prevent the current
                # basket from opening; the state store remains the source of
                # truth for recovery diagnostics.
                self._restored_draft = False
        self._last_preview: PaperPreview | None = None
        self._preview_dialog: PaperPreviewDialog | None = None
        self._catalog_loading = False
        self._catalog_request_signature: list[str] | None = None
        self._compact_splitter = False
        self._rendering = False
        self._mobile_panel = 1
        self.setObjectName("PaperPage")
        self.setMinimumWidth(0)
        self._build_ui()
        self._render_all()
        if self._restored_draft:
            self.preview_state.setText("已恢复上次编排；请重新打开整卷预览后导出")
        self._load_catalog_if_available()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(10)
        root.addWidget(
            section_title(
                "组卷工作台",
                "整道大题是一级单位：左侧排顺序，中间看连续整卷，右侧只编辑当前大题或小问。",
            )
        )

        controls = CardFrame()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        mode_row = QHBoxLayout()
        mode_row.addWidget(_label("组卷模式", "CardTitle"))
        self.mock_mode = QRadioButton("模拟考试")
        self.practice_mode = QRadioButton("平时练习")
        self.mock_mode.setChecked(self.model.mode == "mock_exam")
        self.practice_mode.setChecked(self.model.mode == "daily_practice")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mock_mode)
        self.mode_group.addButton(self.practice_mode)
        mode_row.addWidget(self.mock_mode)
        mode_row.addWidget(self.practice_mode)
        mode_row.addStretch(1)
        controls_layout.addLayout(mode_row)
        fields = QHBoxLayout()
        self.fields_layout = fields
        self.title = QLineEdit()
        self.title.setPlaceholderText("例如：电化学阶段测试")
        self.title.setClearButtonEnabled(True)
        self.subtitle = QLineEdit()
        self.subtitle.setPlaceholderText("可选：本次练习范围或班级说明")
        self.subtitle.setClearButtonEnabled(True)
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("可选：教材章节、知识点或题型")
        self.duration_label = _label("时长", "MutedLabel")
        self.duration = QSpinBox()
        self.duration.setRange(1, 300)
        self.duration.setSuffix(" 分钟")
        self.duration.setFixedWidth(110)
        self.hot_topic = QCheckBox("已核验热点情境")
        self.duration_label.setText("考试时长" if self.model.mode == "mock_exam" else "练习时长")
        fields.addWidget(_label("名称", "MutedLabel"))
        fields.addWidget(self.title, 2)
        fields.addWidget(_label("副标题", "MutedLabel"))
        fields.addWidget(self.subtitle, 2)
        fields.addWidget(_label("筛选关键词", "MutedLabel"))
        fields.addWidget(self.keywords, 2)
        fields.addWidget(self.duration_label)
        fields.addWidget(self.duration)
        fields.addWidget(self.hot_topic)
        controls_layout.addLayout(fields)
        self.summary_label = _label("", "MutedLabel")
        controls_layout.addWidget(self.summary_label)
        self.prompt_blueprint_button = _quiet_button("教材命题提示")
        self.prompt_blueprint_button.setToolTip("选择教材章节和学习目标，离线编译资料提示，不调用模型")
        self.prompt_blueprint_button.clicked.connect(self._open_prompt_blueprint)
        controls_layout.addWidget(self.prompt_blueprint_button)
        self.preparation_button = _quiet_button("将当前组卷带入备课…")
        self.preparation_button.setAccessibleName("预览当前编排文字并追加备课，不调用模型")
        self.preparation_button.clicked.connect(self._prepare_lesson)
        controls_layout.addWidget(self.preparation_button)
        self.title.setText(self.model.title)
        self.subtitle.setText(self.model.subtitle)
        self.keywords.setText(self.model.keywords)
        self.duration.setValue(max(1, min(300, int(self.model.duration_minutes or 60))))
        self.hot_topic.setChecked(self.model.hot_topic)
        root.addWidget(controls)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("PaperComposerSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setMinimumWidth(0)
        self.splitter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        self.directory_card = CardFrame()
        directory_card = self.directory_card
        directory_layout = QVBoxLayout(directory_card)
        directory_layout.setContentsMargins(10, 10, 10, 10)
        directory_layout.addWidget(_label("大题目录", "CardTitle"))
        directory_layout.addWidget(_label("拖动手柄或按 Ctrl+↑/↓ 调整顺序；编号自动重排。", "MutedLabel"))
        self.directory = ThemeDirectory()
        directory_layout.addWidget(self.directory, 1)
        directory_actions = QHBoxLayout()
        self.clear_button = _quiet_button("清空题篮")
        self.clear_button.clicked.connect(self._clear_basket)
        directory_actions.addWidget(self.clear_button)
        directory_actions.addStretch(1)
        directory_layout.addLayout(directory_actions)
        self.splitter.addWidget(directory_card)

        self.center_card = CardFrame()
        center_card = self.center_card
        center_layout = QVBoxLayout(center_card)
        center_layout.setContentsMargins(10, 10, 10, 10)
        center_head = QHBoxLayout()
        center_head.addWidget(_label("连续整卷预览", "CardTitle"))
        center_head.addStretch(1)
        center_head.addWidget(_label("共享材料每道大题只显示一次", "MutedLabel"))
        center_layout.addLayout(center_head)
        self.continuous_preview = ContinuousPaperPreview()
        center_layout.addWidget(self.continuous_preview, 1)
        self.splitter.addWidget(center_card)

        self.inspector_card = CardFrame()
        inspector_card = self.inspector_card
        inspector_layout = QVBoxLayout(inspector_card)
        inspector_layout.setContentsMargins(8, 8, 8, 8)
        inspector_layout.addWidget(_label("当前编辑", "CardTitle"))
        self.inspector = PaperInspector()
        self.inspector.setMinimumHeight(0)
        self.inspector.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setObjectName("InspectorScroll")
        # Keep the inspector's natural form height so the scroll area can
        # expose the save button on narrow/mobile panes.  Width is fitted to
        # the viewport in ``_fit_inspector`` below.
        self.inspector_scroll.setWidgetResizable(False)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inspector_scroll.setMinimumSize(0, 0)
        self.inspector_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.inspector_scroll.setWidget(self.inspector)
        self.inspector.stack.currentChanged.connect(lambda _index: self._fit_inspector())
        inspector_layout.addWidget(self.inspector_scroll, 1)
        self.splitter.addWidget(inspector_card)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([220, 520, 320])
        self._splitter_cards = [self.directory_card, self.center_card, self.inspector_card]
        self.mobile_tabs = QWidget()
        mobile_layout = QHBoxLayout(self.mobile_tabs)
        mobile_layout.setContentsMargins(0, 0, 0, 0)
        mobile_layout.setSpacing(5)
        self.mobile_tab_buttons: list[QPushButton] = []
        for index, text in enumerate(("大题目录", "整卷预览", "当前编辑")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setObjectName("QuietButton")
            button.clicked.connect(
                lambda _checked=False, panel=index: self._show_mobile_panel(panel)
            )
            mobile_layout.addWidget(button, 1)
            self.mobile_tab_buttons.append(button)
        self.mobile_tabs.setVisible(False)
        root.addWidget(self.mobile_tabs)
        root.addWidget(self.splitter, 1)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("整卷预览")
        self.preview_button.setToolTip("冻结当前顺序、分值和答题空间后查看整卷")
        self.preview_button.clicked.connect(self._preview)
        self.export_button = QPushButton("导出")
        self.export_button.setEnabled(False)
        self.export_button.setToolTip("请先打开整卷预览并确认本次预览")
        self.export_button.clicked.connect(self._export)
        self.preview_state = _label("修改后需重新预览", "MutedLabel")
        self.preview_state.setObjectName("PreviewState")
        actions.addWidget(self.preview_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.preview_state, 1)
        root.addLayout(actions)

        self.mock_mode.toggled.connect(self._mode_changed)
        self.practice_mode.toggled.connect(self._mode_changed)
        self.title.editingFinished.connect(self._title_changed)
        self.subtitle.editingFinished.connect(self._settings_changed)
        self.keywords.editingFinished.connect(self._settings_changed)
        self.duration.valueChanged.connect(lambda _value: self._settings_changed())
        self.hot_topic.toggled.connect(lambda _checked: self._settings_changed())
        self.directory.theme_selected.connect(self._select_theme)
        self.directory.order_changed.connect(self._order_changed)
        self.continuous_preview.theme_selected.connect(self._select_theme)
        self.continuous_preview.question_selected.connect(self._select_question)
        self.continuous_preview.edit_requested.connect(self._edit_requested)
        self.continuous_preview.action_requested.connect(self._action_requested)
        self.continuous_preview.theme_expanded_changed.connect(self._theme_expanded)
        self.continuous_preview.question_expanded_changed.connect(self._question_expanded)
        self.inspector.changed.connect(self._inspector_changed)
        self.setTabOrder(self.title, self.keywords)
        self.setTabOrder(self.keywords, self.directory)
        self.setTabOrder(self.directory, self.continuous_preview)
        self.setTabOrder(self.continuous_preview, self.inspector)

    def _load_catalog_if_available(self) -> None:
        loader = getattr(self.facade, "paper_theme_catalog", None)
        basket = self.facade.basket()
        if not callable(loader) or not basket or self._catalog_loading:
            return
        scopes = {str(item.get("scope") or "master") for item in basket if isinstance(item, Mapping)}
        if len(scopes) != 1:
            self.preview_state.setText("题篮含多个题库范围；请分开组卷后再读取完整大题。")
            return
        scope = next(iter(scopes))
        request_signature = PaperComposerModel.basket_signature(basket)
        self._catalog_request_signature = request_signature
        self._catalog_loading = True
        self.tasks.submit(
            "读取组卷大题详情",
            lambda: {
                "signature": request_signature,
                "scope": scope,
                "catalog": loader(scope),
            },
            on_success=self._catalog_loaded,
            on_failure=lambda _message: self._catalog_failed(),
        )

    def _show_mobile_panel(self, panel: int) -> None:
        if not 0 <= panel < len(self._splitter_cards):
            return
        for index, card in enumerate(self._splitter_cards):
            card.setVisible(index == panel)
        for index, button in enumerate(self.mobile_tab_buttons):
            button.setChecked(index == panel)
        self.splitter.setSizes([max(1, self.splitter.height()), 0, 0] if panel == 0 else [0, max(1, self.splitter.height()), 0] if panel == 1 else [0, 0, max(1, self.splitter.height())])
        self._mobile_panel = panel

    def _catalog_loaded(self, catalog: Any) -> None:
        self._catalog_loading = False
        if isinstance(catalog, Mapping) and "catalog" in catalog:
            if catalog.get("signature") != PaperComposerModel.basket_signature(self.facade.basket()):
                # The basket changed while the reader was running; discard the
                # stale result and let the next navigation request refresh it.
                self._catalog_request_signature = None
                return
            catalog = catalog.get("catalog")
        old_themes = list(self.model.themes)
        old_selected_key = self.model.selected_theme_key
        rebuilt = PaperComposerModel.from_basket(
            self.facade.basket(),
            catalog=catalog if isinstance(catalog, Mapping) else None,
            mode=self.model.mode,
            title=self.model.title,
            keywords=self.model.keywords,
            hot_topic=self.model.hot_topic,
        )
        rebuilt.subtitle = self.model.subtitle
        rebuilt.duration_minutes = self.model.duration_minutes
        if not rebuilt.themes:
            return
        # The first lightweight render uses hashed fallback keys; once the
        # catalogue arrives, replace those rows with source-backed keys while
        # preserving the teacher's current order and edits.  Matching by an
        # opaque source digest is preferred; a title is accepted only when it
        # is unique, otherwise the row stays visibly pending.
        rebuilt_by_identity = {
            theme.source_identity_sha256: theme
            for theme in rebuilt.themes
            if theme.source_identity_sha256
        }
        rebuilt_by_title: dict[str, list[ComposerTheme]] = {}
        for theme in rebuilt.themes:
            rebuilt_by_title.setdefault(theme.title, []).append(theme)
        used_rebuilt: set[int] = set()
        ordered: list[ComposerTheme] = []
        selected_new_key: str | None = None
        for old in old_themes:
            theme = (
                rebuilt_by_identity.get(old.source_identity_sha256)
                if old.source_identity_sha256
                else None
            )
            if theme is None:
                matches = rebuilt_by_title.get(old.title, [])
                theme = matches[0] if len(matches) == 1 else None
            if theme is None or id(theme) in used_rebuilt:
                continue
            used_rebuilt.add(id(theme))
            old_is_fallback = old.key.startswith("theme-") or all(
                question.key.startswith("pending-")
                for question in old.questions
            )
            if not old_is_fallback:
                for field_name in (
                    "title",
                    "source",
                    "chapter",
                    "difficulty",
                    "time_minutes",
                    "shared_summary",
                    "shared_text",
                    "shared_materials",
                    "match_status",
                ):
                    setattr(theme, field_name, deepcopy(getattr(old, field_name)))
                for q_index, question in enumerate(theme.questions):
                    if q_index < len(old.questions):
                        previous = old.questions[q_index]
                        for field_name in (
                            "response_type",
                            "score",
                            "section",
                            "difficulty",
                            "answer_status",
                            "stem",
                            "answer",
                            "analysis",
                            "answer_space",
                            "dependency",
                        ):
                            setattr(question, field_name, deepcopy(getattr(previous, field_name)))
            ordered.append(theme)
            if old.key == old_selected_key:
                selected_new_key = theme.key
        ordered.extend(theme for theme in rebuilt.themes if id(theme) not in used_rebuilt)
        if ordered:
            rebuilt.themes = ordered
            rebuilt.revision = self.model.revision
            rebuilt.basket_signature_value = list(self._basket_signature)
            rebuilt.selected_theme_key = selected_new_key or ordered[0].key
            rebuilt.selected_question_key = None
            rebuilt.expanded_theme_keys = set(self.model.expanded_theme_keys)
            rebuilt.expanded_question_keys = set(self.model.expanded_question_keys)
            self.model = rebuilt
            self._render_all()

    def _catalog_failed(self) -> None:
        self._catalog_loading = False
        self.preview_state.setText("题面详情仍待读取；可先调整顺序，预览会保留缺口提示。")

    def _open_prompt_blueprint(self) -> None:
        from .prompt_blueprint_dialog import PromptBlueprintDialog

        dialog = PromptBlueprintDialog(self.facade, self.tasks, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _mode_changed(self, checked: bool) -> None:
        if not checked:
            return
        self.model.set_mode("mock_exam" if self.mock_mode.isChecked() else "daily_practice")
        self.duration_label.setText("考试时长" if self.model.mode == "mock_exam" else "练习时长")
        self._mark_dirty()
        self._render_all()

    def _prepare_lesson(self) -> None:
        # Snapshot only. Do not freeze/approve a paper export, save a draft, or
        # reload the basket (which would discard the teacher's current edits).
        if self._catalog_loading:
            self.preview_state.setText("正在读取所选题目，请待题面加载完成后带入备课。")
            return
        if not self.model.themes:
            self.preview_state.setText("请先从题库加入大题，再带入备课。")
            return
        snapshot = self.model.draft_payload()
        snapshot.update(
            title=self.title.text(),
            subtitle=self.subtitle.text(),
            keywords=self.keywords.text(),
        )
        self.preparation_requested.emit(snapshot)

    def _title_changed(self) -> None:
        value = self.title.text().strip()
        if value != self.model.title:
            self.model.title = value
            self._mark_dirty()
            self._render_all()

    def _settings_changed(self) -> None:
        self.model.subtitle = self.subtitle.text().strip()
        self.model.keywords = self.keywords.text().strip()
        self.model.hot_topic = self.hot_topic.isChecked()
        self.model.duration_minutes = max(1, min(300, int(self.duration.value())))
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self.model.invalidate_preview()
        self._last_preview = None
        self.export_button.setEnabled(False)
        self.preview_state.setText("修改后需重新预览")
        self._save_draft()

    def _theme_expanded(self, theme_key: str, expanded: bool) -> None:
        if expanded:
            self.model.expanded_theme_keys.add(theme_key)
        else:
            self.model.expanded_theme_keys.discard(theme_key)
        # Expansion is view state, not paper content; it should survive a
        # redraw without invalidating a confirmed content snapshot.
        self._save_draft()

    def _question_expanded(
        self, theme_key: str, question_key: str, expanded: bool
    ) -> None:
        token = f"{theme_key}:{question_key}"
        if expanded:
            self.model.expanded_question_keys.add(token)
        else:
            self.model.expanded_question_keys.discard(token)
        self._save_draft()

    def _save_draft(self) -> None:
        store = getattr(self.facade, "state_store", None)
        if store is None:
            return
        try:
            store.save_draft(
                "paper-current",
                {
                    "kind": "paper",
                    "payload": self.model.draft_payload(),
                    "status": "draft",
                },
            )
        except Exception:
            # A transient personal-state failure should not make the visible
            # editor unusable; the next explicit save/preview reports it.
            return

    def _render_all(self) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            self.summary_label.setText(
            f"当前 {len(self.model.themes)} 道大题 · {self.model.total_questions} 个小问 · "
            f"{_metric(self.model.total_score, ' 分')} · 预计 {_metric(self.model.total_time, ' 分钟')}"
            )
            self.directory.set_themes(self.model.themes, self.model.selected_theme_key)
            self.continuous_preview.render(self.model)
            selected = self.model.selected_theme()
            if selected is not None:
                if self.model.selected_question_key:
                    question = self.model.selected_question()
                    if question is not None:
                        number = self.model.question_numbers().get(question.key, "第?问")
                        self.inspector.show_question(selected, question, number)
                    else:
                        self.inspector.show_theme(selected, self.model.theme_number(selected.key) or 1)
                else:
                    self.inspector.show_theme(selected, self.model.theme_number(selected.key) or 1)
            else:
                self.inspector.stack.setCurrentWidget(self.inspector.empty)
            self._fit_inspector()
        finally:
            self._rendering = False

    def _select_theme(self, key: str) -> None:
        if key == self.model.selected_theme_key and self.model.selected_question_key is None:
            return
        self.model.select_theme(key)
        self._render_all()

    def _select_question(self, theme_key: str, question_key: str) -> None:
        if (
            theme_key == self.model.selected_theme_key
            and question_key == self.model.selected_question_key
        ):
            return
        self.model.select_question(theme_key, question_key)
        self._render_all()

    def _edit_requested(self, kind: str, key: str) -> None:
        if kind == "theme":
            self.model.select_theme(key)
            self.model.selected_question_key = None
        elif kind == "question" and ":" in key:
            theme_key, question_key = key.split(":", 1)
            self.model.select_question(theme_key, question_key)
        self._render_all()

    def _inspector_changed(self, kind: str, key: str, changes: dict) -> None:
        if kind == "theme":
            self.model.update_theme(key, **changes)
        else:
            if ":" not in key:
                return
            theme_key, question_key = key.split(":", 1)
            self.model.update_question(theme_key, question_key, **changes)
        self._mark_dirty()
        self._render_all()

    def _order_changed(self, keys: list) -> None:
        normalized = [key for key in keys if isinstance(key, str)]
        by_key = {theme.key: theme for theme in self.model.themes}
        if set(normalized) != set(by_key) or len(normalized) != len(by_key):
            return
        self.model.themes = [by_key[key] for key in normalized]
        self._mark_dirty()
        self._render_all()

    def _action_requested(self, key: str, action: str) -> None:
        if ":" in key:
            theme_key, question_key = key.split(":", 1)
            if action == "move_up":
                changed = self.model.move_question(theme_key, question_key, -1)
            elif action == "move_down":
                changed = self.model.move_question(theme_key, question_key, 1)
            elif action == "delete":
                changed = self._confirm_delete("小问") and self.model.remove_question(theme_key, question_key)
            elif action == "replace":
                changed = self._replace_question(theme_key, question_key)
            else:
                changed = False
        else:
            theme_key = key
            if action == "move_up":
                changed = self.model.move_theme(theme_key, -1)
            elif action == "move_down":
                changed = self.model.move_theme(theme_key, 1)
            elif action == "delete":
                changed = self._confirm_delete("大题") and self.model.remove_theme(theme_key)
            elif action == "replace":
                changed = self._replace_theme(theme_key)
            else:
                changed = False
        if changed:
            self._mark_dirty()
            self._render_all()

    def _replace_theme(self, theme_key: str) -> bool:
        choices = [theme for theme in self.model.themes if theme.key != theme_key]
        if not choices:
            self.preview_state.setText("题篮中没有可替换大题；请先到题库加入另一道完整大题。")
            return False
        labels = [f"{theme.title}（{_metric(theme.score, ' 分')}）" for theme in choices]
        value, ok = QInputDialog.getItem(
            self,
            "换一道大题",
            "选择题篮中用于替换的大题：",
            labels,
            0,
            False,
        )
        if not ok:
            return False
        selected = choices[labels.index(value)]
        target_index = next(
            (index for index, theme in enumerate(self.model.themes) if theme.key == theme_key),
            -1,
        )
        if target_index < 0:
            return False
        replacement = deepcopy(selected)
        replacement.key = f"{selected.key}-copy-{self.model.revision + 1}"
        self.model.themes[target_index] = replacement
        self.model.selected_theme_key = replacement.key
        self.model.selected_question_key = None
        return True

    def _replace_question(self, theme_key: str, question_key: str) -> bool:
        candidates = [
            (theme, question)
            for theme in self.model.themes
            for question in theme.questions
            if not (theme.key == theme_key and question.key == question_key)
        ]
        if not candidates:
            self.preview_state.setText("题篮中没有可替换小问；请先加入更多大题。")
            return False
        labels = [f"{theme.title} · {question.response_type}" for theme, question in candidates]
        value, ok = QInputDialog.getItem(
            self,
            "换一个小问",
            "选择题篮中用于替换的小问：",
            labels,
            0,
            False,
        )
        if not ok:
            return False
        _source_theme, source_question = candidates[labels.index(value)]
        target_theme = next((theme for theme in self.model.themes if theme.key == theme_key), None)
        target_index = next(
            (
                index
                for index, question in enumerate(target_theme.questions if target_theme else [])
                if question.key == question_key
            ),
            -1,
        )
        if target_theme is None or target_index < 0:
            return False
        replacement = deepcopy(source_question)
        replacement.key = f"{source_question.key}-copy-{self.model.revision + 1}"
        target_theme.questions[target_index] = replacement
        self.model.selected_question_key = replacement.key
        return True

    def _confirm_delete(self, label: str) -> bool:
        answer = QMessageBox.question(
            self,
            f"删除{label}",
            f"确定从本次组卷中删除这道{label}吗？源题库内容不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _clear_basket(self) -> None:
        if not self.model.themes:
            return
        if not self._confirm_delete("全部大题"):
            return
        clear = getattr(self.facade, "clear_basket", None)
        if callable(clear):
            clear()
        self.model = PaperComposerModel.from_basket(
            [],
            mode=self.model.mode,
            title=self.model.title,
            keywords=self.model.keywords,
            hot_topic=self.model.hot_topic,
        )
        self.model.subtitle = self.subtitle.text().strip()
        self.model.duration_minutes = int(self.duration.value())
        self._basket_signature = []
        self._mark_dirty()
        self._render_all()

    def _preview_payload(self) -> dict[str, Any]:
        assembly = deepcopy(self.model.preview or self.model.make_preview())
        return {
            "mode": self.model.mode,
            "title": self.title.text().strip(),
            "subtitle": self.subtitle.text().strip(),
            "keywords": self.keywords.text().strip(),
            "hot_topic": self.hot_topic.isChecked(),
            "duration_minutes": int(self.duration.value()),
            # Send the exact frozen projection (including its snapshot hash)
            # to the facade; rebuilding a second projection here would make
            # approval fail closed because the two hashes differ.
            "assembly": assembly,
        }

    @staticmethod
    def _with_projection(value: Any, projection: dict[str, Any]) -> PaperPreview:
        if isinstance(value, PaperPreview):
            supplied = getattr(value, "preview_model", None)
            source_projection = (
                normalize_preview_projection(supplied)
                if isinstance(supplied, Mapping) and supplied
                else normalize_preview_projection(projection)
            )
            # Keep the formal service snapshot when one is supplied.  The
            # local model remains responsible for edit invalidation, while the
            # dialog renders the authoritative hierarchy unchanged.
            supplied_hash = str(getattr(value, "preview_hash", "") or "")
            if supplied_hash:
                source_projection["preview_snapshot_sha256"] = supplied_hash
            blockers = tuple(
                item
                for item in value.blockers
                if "桌面导出适配仍在迁移" not in str(item)
            )
            return replace(
                value,
                title_zh=str(source_projection.get("title") or value.title_zh),
                mode_zh=str(source_projection.get("mode_label") or value.mode_zh),
                theme_count=len(source_projection.get("themes", [])) or value.theme_count,
                theme_titles=tuple(
                    str(theme.get("title") or "未命名大题")
                    for theme in source_projection.get("themes", [])
                    if isinstance(theme, Mapping)
                ),
                export_ready=False,
                blockers=blockers,
                preview_model=source_projection,
                preview_hash=str(
                    source_projection.get("preview_snapshot_sha256")
                    or projection.get("preview_snapshot_sha256")
                    or ""
                ),
            )
        return PaperPreview(
            preview_id="desktop-preview",
            title_zh=str(projection.get("title") or "未命名组卷"),
            mode_zh=str(projection.get("mode_label") or "组卷"),
            theme_count=len(projection.get("themes", [])),
            theme_titles=tuple(
                str(theme.get("title") or "未命名大题")
                for theme in projection.get("themes", [])
                if isinstance(theme, Mapping)
            ),
            export_ready=False,
            blockers=(),
            preview_model=projection,
            preview_hash=str(projection.get("preview_snapshot_sha256") or ""),
        )

    def _preview(self) -> None:
        if not self.title.text().strip():
            QMessageBox.warning(self, "无法预览", "请先填写试卷或练习名称。")
            self.title.setFocus()
            return
        self.model.keywords = self.keywords.text().strip()
        self.model.hot_topic = self.hot_topic.isChecked()
        projection = self.model.make_preview()
        try:
            value = self.facade.create_paper_preview(self._preview_payload())
        except DesktopFacadeError as exc:
            QMessageBox.warning(self, "无法预览", exc.message_zh)
            return
        except Exception as exc:
            message = getattr(exc, "message_zh", "整卷预览暂时无法生成，请稍后重试。")
            QMessageBox.warning(self, "无法预览", str(message))
            return
        self._last_preview = self._with_projection(value, projection)
        if self._last_preview.preview_hash:
            self.model.preview_hash = self._last_preview.preview_hash
            self.model.preview = normalize_preview_projection(
                self._last_preview.preview_model
            )
        dialog = PaperPreviewDialog(self._last_preview, self)
        self._preview_dialog = dialog
        dialog.preview_confirmed.connect(lambda: self._approve_preview(dialog))
        dialog.exec()
        self._preview_dialog = None

    def _approve_preview(self, dialog: PaperPreviewDialog | None = None) -> None:
        if self._last_preview is None or not self.model.preview_hash:
            return
        approver = getattr(self.facade, "approve_paper_preview", None)
        if callable(approver):
            try:
                result = approver(self._last_preview.preview_id, self.model.preview_hash)
                if isinstance(result, Mapping) and result.get("status") not in {
                    None,
                    "approved",
                }:
                    raise RuntimeError("本次预览确认未被接受")
            except Exception as exc:
                message = getattr(exc, "message_zh", "本次预览确认未保存，请重新打开预览。")
                self.preview_state.setText(str(message))
                self.export_button.setEnabled(False)
                if dialog is not None:
                    dialog.mark_confirmation_failed(str(message))
                return
        self.model.approve(self.model.preview_hash)
        self.preview_state.setText("已确认本次预览，可导出")
        self.export_button.setEnabled(self.model.can_export)
        self._save_draft()
        if dialog is not None:
            dialog.mark_confirmed()

    def _export(self) -> None:
        if not self.model.can_export or self._last_preview is None:
            QMessageBox.information(self, "需要先预览", "请先生成并确认整卷预览，再导出。")
            return
        exporter = getattr(self.facade, "export_paper_preview", None)
        if not callable(exporter):
            self.preview_state.setText("当前桌面导出服务未接入；预览确认已保留在草稿中。")
            return
        self.export_button.setEnabled(False)
        self.preview_state.setText("正在保存导出稿…")
        self.tasks.submit(
            "导出整卷预览稿",
            lambda: exporter(self._last_preview.preview_id, self.model.preview_hash or "", self.model.draft_payload()),
            on_success=self._exported,
            on_failure=self._export_failed,
        )

    def _exported(self, value: Any) -> None:
        self.export_button.setEnabled(True)
        if isinstance(value, Mapping):
            self.preview_state.setText(str(value.get("message_zh") or "导出稿已保存，可在个人草稿中查看。"))
        else:
            self.preview_state.setText("导出稿已保存，可在个人草稿中查看。")

    def _export_failed(self, message: str) -> None:
        self.export_button.setEnabled(True)
        self.preview_state.setText(message)

    def update_basket_count(self, _count: int | None = None) -> None:
        basket = self.facade.basket()
        incoming_signature = PaperComposerModel.basket_signature(basket)
        current_signature = list(self._basket_signature)
        if not basket:
            if self.model.themes:
                self.model = PaperComposerModel.from_basket(
                    [],
                    mode=self.model.mode,
                    title=self.model.title,
                    keywords=self.model.keywords,
                    hot_topic=self.model.hot_topic,
                )
                self.model.subtitle = self.subtitle.text().strip()
                self.model.duration_minutes = int(self.duration.value())
                self._basket_signature = []
                self._mark_dirty()
                self._render_all()
            return
        if incoming_signature != current_signature:
            self.model = PaperComposerModel.from_basket(
                basket,
                mode=self.model.mode,
                title=self.model.title,
                keywords=self.model.keywords,
                hot_topic=self.model.hot_topic,
            )
            self.model.subtitle = self.subtitle.text().strip()
            self.model.duration_minutes = int(self.duration.value())
            self._basket_signature = incoming_signature
            self._mark_dirty()
            self._render_all()
            self._load_catalog_if_available()

    def resizeEvent(self, event: QResizeEvent) -> None:
        width = event.size().width()
        compact = width < 920
        mobile = width < 700
        if mobile:
            self.fields_layout.setDirection(QBoxLayout.Direction.TopToBottom)
        else:
            self.fields_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        if compact != self._compact_splitter:
            self._compact_splitter = compact
            self.splitter.setOrientation(
                Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
            )
            if compact:
                self.splitter.setSizes([150, 480, 250])
            else:
                self.splitter.setSizes([220, 520, 320])
        if mobile:
            self.mobile_tabs.setVisible(True)
            self._show_mobile_panel(getattr(self, "_mobile_panel", 1))
        else:
            self.mobile_tabs.setVisible(False)
            for card in self._splitter_cards:
                card.setVisible(True)
        super().resizeEvent(event)
        self._fit_inspector()

    def _fit_inspector(self) -> None:
        """Give the nested inspector scroll area a real content rectangle."""

        scroll = getattr(self, "inspector_scroll", None)
        inspector = getattr(self, "inspector", None)
        if scroll is None or inspector is None:
            return
        viewport = scroll.viewport()
        width = max(0, viewport.width())
        if width <= 0:
            return
        hint = inspector.sizeHint()
        height = max(viewport.height(), hint.height())
        inspector.setMinimumWidth(0)
        inspector.setMaximumWidth(16777215)
        inspector.resize(width, height)

    def minimumSizeHint(self) -> QSize:
        # The parent window owns the narrow layout.  Returning a zero-width
        # hint lets the vertical compact splitter fit a 375px viewport instead
        # of expanding the whole window to a child form's long-text width.
        return QSize(0, 0)


__all__ = [
    "ContinuousPaperPreview",
    "PaperInspector",
    "PaperPage",
    "PaperPreviewDialog",
    "QuestionRowWidget",
    "ThemeCardWidget",
    "ThemeDirectory",
]
