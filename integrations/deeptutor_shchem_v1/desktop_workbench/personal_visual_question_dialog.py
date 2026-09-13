"""Teacher review UI for complete personal question image selections.

The dialog deliberately keeps catalog metadata, detail text, and image bytes
separate.  A row is never made selectable merely because its catalog record
exists: the current detail and every question/shared-material image must be
readable first.  Answer text and answer images remain behind an explicit tab.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_personal_visual_questions import matches_personal_visual_filters
from .components import page_scroll, section_title, set_status
from .preparation_images_widget import _LocalImagePreview
from .word_question_filter_panel import WordQuestionFilterPanel


class _PersonalVisualFilterPanel(WordQuestionFilterPanel):
    GROUPS: ClassVar = {**WordQuestionFilterPanel.GROUPS, "teaching_use": "教学用途"}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if muted:
        label.setObjectName("MutedLabel")
    return label


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()


def _row_token(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _text(row.get("batch_id")),
        _text(row.get("key")),
        _text(row.get("revision")),
    )


def _bound_presentation(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Reject a stale/incomplete readable view without hiding the source text."""
    presentation = row.get("presentation")
    if not isinstance(presentation, Mapping):
        return None
    if (presentation.get("format_version") != "personal-visual-presentation-v1"
            or presentation.get("binding_revision") != row.get("revision")):
        return None
    full_text = presentation.get("full_text")
    if not isinstance(full_text, Mapping):
        return None
    for field in ("question_text", "shared_text", "answer_text"):
        if (full_text.get(field) != row.get(field, "")
                or not isinstance(presentation.get(field), str)
                or (full_text.get(field) and not presentation[field].strip())):
            return None
    return presentation


def _normalise_row(value: object, *, default_batch_id: str = "") -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    row = deepcopy(dict(value))
    if not _text(row.get("batch_id")):
        row["batch_id"] = default_batch_id
    if not _text(row.get("key")) or not _text(row.get("revision")):
        return None
    for field in ("title", "theme_key", "theme_title", "source_name", "question_text", "shared_text", "answer_text"):
        row[field] = _text(row.get(field))
    row["question_number"] = _text(row.get("question_number"))
    row["warnings"] = [item for item in row.get("warnings", ()) if isinstance(item, str) and item]
    facets = row.get("facets")
    row["facets"] = {
        str(group): [str(item) for item in values if item is not None]
        for group, values in facets.items()
        if isinstance(group, str) and isinstance(values, (list, tuple, set, frozenset))
    } if isinstance(facets, Mapping) else {}
    images = row.get("images")
    row["images"] = [deepcopy(item) for item in images if isinstance(item, Mapping)] if isinstance(images, (list, tuple)) else []
    row["selection_ready"] = row.get("selection_ready") is True
    return row


def _normalise_filter_groups(value: object, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    source = value if isinstance(value, Mapping) else {}
    for group in _PersonalVisualFilterPanel.GROUPS:
        options: list[dict[str, str]] = []
        raw_options = source.get(group, ())
        if isinstance(raw_options, (list, tuple)):
            for raw in raw_options:
                if not isinstance(raw, Mapping):
                    continue
                option_id = _text(raw.get("value")) or _text(raw.get("id"))
                label = _text(raw.get("label")) or option_id
                if not option_id:
                    continue
                option = {"id": option_id, "label": label}
                for field in ("volume_id", "chapter_id"):
                    if _text(raw.get(field)):
                        option[field] = _text(raw.get(field))
                options.append(option)
        if not options:
            seen: set[str] = set()
            for row in rows:
                for item in row.get("facets", {}).get(group, ()):
                    item = str(item)
                    if item and item not in seen:
                        seen.add(item)
                        options.append({"id": item, "label": item})
        groups[group] = options
    return groups


def _materials_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "materials", "content"):
            if isinstance(value.get(key), str):
                return value[key]
        return "\n".join(
            part
            for part in (_materials_text(item) for item in value.values())
            if part
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(part for part in (_materials_text(item) for item in value) if part)
    return ""


class PersonalVisualQuestionDialog(QDialog):
    """Review and explicitly save personal visual-question selections."""

    def __init__(
        self,
        facade: Any,
        tasks: Any,
        parent: QWidget | None = None,
        batch_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self.batch_id = batch_id
        self.preparation_reference: dict[str, Any] | None = None
        self.saved = False
        self._closed = False
        self._jobs: dict[int, str | None] = {}
        self._job_serial = 0
        self._catalog_generation = 0
        self._detail_generation = 0
        self._reference_generation = 0
        self._catalog_busy = False
        self._catalog_loaded = False
        self._detail_busy = False
        self._save_busy = False
        self._reference_busy = False
        self._attributes_busy = False
        self._attributes_saving = False
        self._crop_busy = False
        self._rendering_list = False
        self._rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._visible_tokens: list[tuple[str, str, str]] = []
        self._selected: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._current_token: tuple[str, str, str] | None = None
        self._current_detail: dict[str, Any] | None = None
        self._detail_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._detail_failures: set[tuple[str, str, str]] = set()
        self._image_failures: set[tuple[str, str, str]] = set()
        self._image_states: dict[tuple[tuple[str, str, str], str, str], str] = {}
        self._image_original_states: dict[tuple[tuple[str, str, str], str, str], str] = {}
        self._image_bytes: dict[tuple[tuple[str, str, str], str, str], tuple[bytes, str]] = {}
        self._image_original_bytes: dict[tuple[tuple[str, str, str], str, str], tuple[bytes, str]] = {}
        self._image_previews: dict[tuple[tuple[str, str, str], str, str], _LocalImagePreview] = {}
        self._image_original_buttons: dict[tuple[tuple[str, str, str], str, str], QPushButton] = {}
        self._image_crop_buttons: dict[tuple[tuple[str, str, str], str, str], QPushButton] = {}
        self._image_edit_crop_buttons: dict[tuple[tuple[str, str, str], str, str], QPushButton] = {}
        self._rendered_tabs: set[int] = set()
        self._reference_preview: dict[str, Any] | None = None
        self._reference_selection_key: tuple = ()

        self.setWindowTitle("个人题库·图文题选择")
        self.resize(1220, 820)
        self.setMinimumSize(420, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(9)
        root.addWidget(
            section_title(
                "个人题库·图文题选择",
                "先逐题核对完整主题、公共材料和原图，再保存选择；答案默认隐藏，所有读取均在本机完成。",
            )
        )
        self.scope_note = _label(
            "备课参考会带入选中题所属的完整主题、全部小题与公共材料；这不表示统一组卷或整批资料已经完成。",
            muted=True,
        )
        self.scope_note.setAccessibleName("个人图文题备课范围说明")
        root.addWidget(self.scope_note)

        top_row = QHBoxLayout()
        self.batch_combo = QComboBox()
        self.batch_combo.setAccessibleName("按个人题库批次筛选")
        self.batch_combo.setMinimumContentsLength(10)
        self.batch_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        top_row.addWidget(self.batch_combo, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题面、公共材料、来源或教学标签")
        self.search.setAccessibleName("搜索个人图文题")
        top_row.addWidget(self.search, 2)
        self.reload_button = QPushButton("刷新")
        self.reload_button.setObjectName("QuietButton")
        self.reload_button.setAccessibleName("刷新个人图文题目录")
        top_row.addWidget(self.reload_button)
        root.addLayout(top_row)

        self.filter_panel = _PersonalVisualFilterPanel()
        self.filter_panel.setAccessibleName("个人图文题标签叠加筛选")
        # Keep the descriptive alias used by the native Word browser.
        self.multi_filter_panel = self.filter_panel
        root.addWidget(self.filter_panel)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.result_count = _label("正在读取个人题目录…", muted=True)
        self.result_count.setAccessibleName("个人图文题筛选结果数量")
        left_layout.addWidget(self.result_count)
        self.question_list = QListWidget()
        self.question_list.setAccessibleName("个人图文题列表，可勾选")
        self.question_list.setWordWrap(True)
        self.question_list.setMinimumSize(0, 150)
        self.question_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_layout.addWidget(self.question_list, 1)
        self.selection_count = _label("已选 0 题；勾选不会自动保存。", muted=True)
        self.selection_count.setAccessibleName("个人图文题已选数量")
        left_layout.addWidget(self.selection_count)
        self.splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_title = _label("请选择一道题")
        self.detail_title.setObjectName("CardTitle")
        self.detail_title.setAccessibleName("当前个人图文题标题")
        right_layout.addWidget(self.detail_title)
        self.detail_note = _label("题目详情尚未读取。", muted=True)
        self.detail_note.setAccessibleName("当前个人图文题读取状态")
        right_layout.addWidget(self.detail_note)

        self.attributes_button = QPushButton("编辑本题标签…")
        self.attributes_button.setObjectName("QuietButton")
        self.attributes_button.setAccessibleName("编辑当前图片题的个人教学标签")
        self.attributes_button.setToolTip("修改主辅知识点、教材、年级与考试属性；先对照变更，再保存。不会修改原图或原档案。")
        self.attributes_button.setVisible(
            callable(getattr(facade, "personal_visual_question_attribute_options", None))
            and callable(getattr(facade, "personal_visual_question_save_attributes", None))
        )
        self.attributes_button.clicked.connect(self._edit_attributes)
        right_layout.addWidget(self.attributes_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("个人图文题内容标签页")
        self._tab_layouts: list[QVBoxLayout] = []
        for title, placeholder in (
            ("题目", "打开一道题后读取完整题面。"),
            ("共同材料", "本题没有单独的共同材料，或尚未读取。"),
            ("答案", "答案默认隐藏；切换到此页后才读取答案文字和图片。"),
        ):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(8)
            layout.addWidget(_label(placeholder, muted=True))
            layout.addStretch(1)
            self._tab_layouts.append(layout)
            self.tabs.addTab(page_scroll(panel), title)
        reference_panel = QWidget()
        reference_layout = QVBoxLayout(reference_panel)
        reference_layout.setContentsMargins(10, 10, 10, 10)
        reference_layout.setSpacing(8)
        self.reference_note = _label(
            "预览只读，不保存选择；确认“带入备课”后才会接受本次参考。",
            muted=True,
        )
        reference_layout.addWidget(self.reference_note)
        self.reference_materials = QPlainTextEdit()
        self.reference_materials.setReadOnly(True)
        self.reference_materials.setAccessibleName("备课选材完整材料预览")
        self.reference_materials.setPlaceholderText("点击“预览备课选材”后显示完整材料。")
        reference_layout.addWidget(self.reference_materials, 1)
        self.reference_image_note = _label("", muted=True)
        self.reference_image_note.setAccessibleName("备课选材图片数量与警告")
        reference_layout.addWidget(self.reference_image_note)
        self.tabs.addTab(reference_panel, "备课选材预览")
        right_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([380, 760])
        root.addWidget(self.splitter, 1)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("保存选择")
        self.save_button.setObjectName("PrimaryAction")
        self.save_button.setAccessibleName("明确保存个人图文题选择")
        self.add_basket_button = QPushButton("整主题加入题篮")
        self.add_basket_button.setObjectName("PrimaryAction")
        self.add_basket_button.setAccessibleName("将所选图片题及完整主题加入统一题篮")
        self.add_basket_button.setToolTip("带入该题所属完整主题、全部小问和公共材料；答案仅用于教师版。")
        self.add_basket_button.setVisible(callable(getattr(self.facade, "add_personal_visual_questions_to_basket", None)))
        self.preview_reference_button = QPushButton("预览备课选材")
        self.preview_reference_button.setObjectName("QuietButton")
        self.preview_reference_button.setAccessibleName("预览选中个人图文题备课材料")
        self.accept_preparation_button = QPushButton("带入备课")
        self.accept_preparation_button.setObjectName("PrimaryAction")
        self.accept_preparation_button.setAccessibleName("明确带入个人图文题备课材料")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.setAccessibleName("取消个人图文题选择")
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.add_basket_button)
        action_row.addWidget(self.preview_reference_button)
        action_row.addWidget(self.accept_preparation_button)
        action_row.addStretch(1)
        action_row.addWidget(self.cancel_button)
        root.addLayout(action_row)

        self.status = _label("正在读取个人图文题目录…")
        self.status.setAccessibleName("个人图文题状态")
        root.addWidget(self.status)

        self.batch_combo.currentIndexChanged.connect(self._filters_changed)
        self.search.textChanged.connect(self._filters_changed)
        self.filter_panel.selection_changed.connect(self._filters_changed)
        self.reload_button.clicked.connect(self._load_catalog)
        self.question_list.itemChanged.connect(self._item_changed)
        self.question_list.currentItemChanged.connect(self._current_changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.save_button.clicked.connect(self._save_selection)
        self.add_basket_button.clicked.connect(self._add_to_basket)
        self.preview_reference_button.clicked.connect(self._preview_reference)
        self.accept_preparation_button.clicked.connect(self._accept_preparation)
        self.cancel_button.clicked.connect(self.reject)
        self._update_actions()
        self._load_catalog()

    @property
    def selections(self) -> list[dict[str, Any]]:
        result = []
        for token, row in self._selected.items():
            result.append(
                {
                    "batch_id": token[0],
                    "key": token[1],
                    "revision": token[2],
                    "points": int(row.get("points", 2)) if isinstance(row.get("points", 2), int) else 2,
                }
            )
        return result

    def _selection_key(self) -> tuple:
        return tuple(
            (item["batch_id"], item["key"], item["revision"], item["points"])
            for item in self.selections
        )

    def _submit(self, label: str, operation, success, failure=None) -> str | None:
        self._job_serial += 1
        token = self._job_serial
        self._jobs[token] = None

        def completed(value: object, failed: bool = False) -> None:
            self._jobs.pop(token, None)
            if self._closed:
                return
            if failed:
                if failure is not None:
                    failure(value)
                else:
                    set_status(self.status, "error", str(value))
            else:
                success(value)

        try:
            task_id = self.tasks.submit(
                label,
                operation,
                on_success=completed,
                on_failure=lambda message: completed(message, True),
            )
        except (RuntimeError, TypeError):
            completed("本地任务暂时无法启动，请重试。", True)
            return None
        if token in self._jobs:
            self._jobs[token] = task_id
        return task_id

    def _load_catalog(self) -> None:
        if self._catalog_busy or self._attributes_busy or self._crop_busy or self._closed:
            return
        self._catalog_generation += 1
        generation = self._catalog_generation
        selection_snapshot = deepcopy(self.selections) if self._catalog_loaded else None
        self._catalog_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在本机读取个人图文题目录…")

        def ready(value):
            if selection_snapshot is not None and isinstance(value, Mapping):
                value = {**value, "selection": selection_snapshot}
            self._catalog_ready(generation, value)

        self._submit(
            "读取个人图文题目录",
            lambda: self.facade.personal_visual_questions(self.batch_id),
            ready,
            lambda message: self._catalog_failed(generation, message),
        )

    def _catalog_failed(self, generation: int, message: object) -> None:
        if generation != self._catalog_generation:
            return
        self._catalog_busy = False
        set_status(self.status, "error", str(message) or "个人图文题目录暂时无法读取，请刷新。")
        self._update_actions()

    def _catalog_ready(self, generation: int, value: object) -> None:
        if generation != self._catalog_generation:
            return
        try:
            if not isinstance(value, Mapping) or not isinstance(value.get("items"), list):
                raise TypeError("invalid catalog")
            rows: dict[tuple[str, str, str], dict[str, Any]] = {}
            for raw in value["items"]:
                row = _normalise_row(raw, default_batch_id=self.batch_id or "")
                if row is None:
                    raise ValueError("invalid question")
                token = _row_token(row)
                if token in rows:
                    raise ValueError("duplicate question")
                if self.batch_id and token[0] != self.batch_id:
                    continue
                rows[token] = row
            selected: dict[tuple[str, str, str], dict[str, Any]] = {}
            raw_selection = value.get("selection", ())
            if isinstance(raw_selection, list):
                for raw in raw_selection:
                    if not isinstance(raw, Mapping):
                        continue
                    batch = _text(raw.get("batch_id")) or (self.batch_id or "")
                    key = _text(raw.get("key"))
                    revision = _text(raw.get("revision"))
                    if not key or not revision:
                        continue
                    points = raw.get("points", 2)
                    selected[(batch, key, revision)] = {
                        "points": points if type(points) is int and 1 <= points <= 100 else 2,
                    }
            self._rows = rows
            self._selected = {
                token: selection
                for token, selection in selected.items()
                if token in rows
            }
            self._detail_cache.clear()
            self._detail_failures.clear()
            self._image_failures.clear()
            self._image_states.clear()
            self._image_original_states.clear()
            self._image_bytes.clear()
            self._image_original_bytes.clear()
            self._image_previews.clear()
            self._image_original_buttons.clear()
            self._image_crop_buttons.clear()
            self._image_edit_crop_buttons.clear()
            self._populate_batches()
            self.filter_panel.set_options(
                _normalise_filter_groups(value.get("filter_options"), list(rows.values()))
            )
            self._catalog_busy = False
            self._catalog_loaded = True
            warnings = [
                warning
                for warning in value.get("warnings", ())
                if isinstance(warning, str) and warning
            ]
            message = f"已读取 {len(rows)} 道个人图文题。勾选不会自动保存。"
            if warnings:
                message += f" 另有 {len(warnings)} 条来源读取提醒。"
            set_status(self.status, "attention" if warnings else "success", message)
            self._apply_filters()
        except (KeyError, TypeError, ValueError):
            self._catalog_failed(generation, "个人图文题目录暂时无法读取，请刷新或核对本地题库。")

    def _populate_batches(self) -> None:
        current = self.batch_combo.currentData()
        batches: dict[str, str] = {}
        for row in self._rows.values():
            batch = _text(row.get("batch_id"))
            if not batch:
                continue
            name = _text(row.get("source_name")) or "个人题库批次"
            batches.setdefault(batch, name)
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        self.batch_combo.addItem("全部批次", None)
        for batch, name in sorted(batches.items(), key=lambda item: item[1]):
            self.batch_combo.addItem(name, batch)
        target = self.batch_id or current
        if target:
            index = self.batch_combo.findData(target)
            if index >= 0:
                self.batch_combo.setCurrentIndex(index)
        self.batch_combo.blockSignals(False)

    def _filters_changed(self, *_args) -> None:
        self._apply_filters()

    def _filter_items(self, *_args) -> None:
        """Compatibility name for the native Word question browser."""

        self._apply_filters()

    def _apply_filters(self) -> None:
        if self._closed:
            return
        query = self.search.text().strip().casefold()
        batch = self.batch_combo.currentData()
        selection = self.filter_panel.matching_selection()
        visible: list[tuple[str, str, str]] = []
        for token, row in self._rows.items():
            if batch and token[0] != batch:
                continue
            if query and query not in self._search_blob(row):
                continue
            if not self._matches_filters(row, selection):
                continue
            visible.append(token)
        self._visible_tokens = visible
        self._render_list()

    @staticmethod
    def _search_blob(row: Mapping[str, Any]) -> str:
        pieces = [
            row.get("title"),
            row.get("theme_title"),
            row.get("source_name"),
            row.get("question_number"),
            row.get("question_text"),
            row.get("shared_text"),
        ]
        for values in (row.get("facets", {}) or {}).values():
            if isinstance(values, (list, tuple, set, frozenset)):
                pieces.extend(values)
        return " ".join(str(piece) for piece in pieces if piece is not None).casefold()

    @staticmethod
    def _matches_filters(row: Mapping[str, Any], selection: Mapping[str, Any]) -> bool:
        return matches_personal_visual_filters(row, selection)

    def _render_list(self) -> None:
        current = self._current_token if self._current_token in self._visible_tokens else None
        self._rendering_list = True
        self.question_list.blockSignals(True)
        self.question_list.clear()
        for token in self._visible_tokens:
            row = self._rows[token]
            title = _text(row.get("title")) or _text(row.get("theme_title")) or token[1]
            prefix = f"{row['question_number']} · " if row.get("question_number") else ""
            source = _text(row.get("source_name")) or "个人题库来源待补"
            state = "已选" if token in self._selected else "未选"
            if not row.get("selection_ready"):
                state = "暂不可选·范围或素材待核对"
            item = QListWidgetItem(f"{prefix}{title}\n{source} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, token)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if token in self._selected else Qt.CheckState.Unchecked
            )
            item.setToolTip("\n".join(row.get("warnings", ())) or "打开后读取完整题面与原图。")
            self.question_list.addItem(item)
        self.question_list.blockSignals(False)
        self._rendering_list = False
        if current is not None:
            self.question_list.setCurrentRow(self._visible_tokens.index(current))
        elif self._visible_tokens:
            self.question_list.setCurrentRow(0)
        else:
            self._clear_current_detail()
        self.result_count.setText(
            f"显示 {len(self._visible_tokens)} / {len(self._rows)} 道；筛选条件可叠加，勾选保留。"
        )
        self._update_actions()

    def _current_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        token = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if isinstance(token, list):
            token = tuple(token)
        if not isinstance(token, tuple) or len(token) != 3:
            self._clear_current_detail()
            return
        self._show_detail(tuple(str(item) for item in token))

    def _clear_current_detail(self) -> None:
        self._detail_generation += 1
        self._retain_current_image_payloads(None)
        self._current_token = None
        self._current_detail = None
        self._rendered_tabs.clear()
        self.detail_title.setText("请选择一道题")
        self.detail_note.setText("题目详情尚未读取。")
        for layout, text in zip(
            self._tab_layouts,
            ("打开一道题后读取完整题面。", "本题没有单独的共同材料，或尚未读取。", "答案默认隐藏；切换到此页后才读取答案文字和图片。"),
            strict=True,
        ):
            _clear_layout(layout)
            layout.addWidget(_label(text, muted=True))
            layout.addStretch(1)
        self._update_actions()

    def _retain_current_image_payloads(
        self, token: tuple[str, str, str] | None
    ) -> None:
        """Bound image-byte memory to the currently viewed question.

        Detail metadata remains cached for quick navigation, but source and
        full-page bytes are discarded as soon as another question is opened.
        A later return therefore re-reads the current source instead of
        retaining an unbounded collection of page rasters.
        """

        for mapping in (
            self._image_bytes,
            self._image_original_bytes,
            self._image_previews,
            self._image_original_buttons,
            self._image_crop_buttons,
            self._image_edit_crop_buttons,
        ):
            for image_key in tuple(mapping):
                if image_key[0] != token:
                    mapping.pop(image_key, None)

    def _show_detail(self, token: tuple[str, str, str]) -> None:
        if token not in self._rows or self._closed:
            return
        self._detail_generation += 1
        generation = self._detail_generation
        self._retain_current_image_payloads(token)
        self._current_token = token
        self._current_detail = None
        self._rendered_tabs.clear()
        self._detail_busy = True
        row = self._rows[token]
        self.detail_title.setText(_text(row.get("title")) or _text(row.get("theme_title")) or token[1])
        self.detail_note.setText("正在读取完整题面、公共材料和图片目录…")
        for layout, text in zip(
            self._tab_layouts,
            ("正在读取完整题面…", "正在读取共同材料…", "答案默认隐藏；切换到此页后才读取。"),
            strict=True,
        ):
            _clear_layout(layout)
            layout.addWidget(_label(text, muted=True))
            layout.addStretch(1)
        cached = self._detail_cache.get(token)
        if cached is not None:
            self._detail_ready(generation, token, deepcopy(cached))
            return
        self._update_actions()
        self._submit(
            "读取个人图文题详情",
            lambda: self.facade.personal_visual_question_detail(token[0], token[1], token[2]),
            lambda value: self._detail_ready(generation, token, value),
            lambda message: self._detail_failed(generation, token, message),
        )

    def _detail_failed(self, generation: int, token: tuple[str, str, str], message: object) -> None:
        if generation != self._detail_generation or token != self._current_token:
            return
        self._detail_busy = False
        self._detail_failures.add(token)
        self._selected.pop(token, None)
        self._sync_item_check(token)
        self.detail_note.setText("题目详情读取失败；本题暂不能选择。")
        set_status(self.status, "error", str(message) or "个人图文题详情暂时无法读取。")
        self._update_actions()

    def _detail_ready(self, generation: int, token: tuple[str, str, str], value: object) -> None:
        if generation != self._detail_generation or token != self._current_token:
            return
        detail = _normalise_row(value, default_batch_id=token[0])
        if detail is None or _row_token(detail) != token:
            self._detail_failed(generation, token, "题目详情版本已变化，请刷新目录。")
            return
        self._detail_busy = False
        self._current_detail = detail
        self._detail_cache[token] = deepcopy(detail)
        self._detail_failures.discard(token)
        self.detail_title.setText(_text(detail.get("title")) or _text(detail.get("theme_title")) or token[1])
        warnings = detail.get("warnings", ())
        note = "完整题面、公共材料与答案分栏显示。答案图片只在切换答案页后读取。"
        if warnings:
            note += "\n本题提醒：" + "；".join(warnings)
        self.detail_note.setText(note)
        self._render_tab(0)
        self._render_tab(1)
        self.tabs.setCurrentIndex(0)
        self._update_actions()

    def _render_tab(self, index: int) -> None:
        if index in self._rendered_tabs or self._current_detail is None:
            return
        if index not in (0, 1, 2):
            return
        self._rendered_tabs.add(index)
        layout = self._tab_layouts[index]
        _clear_layout(layout)
        detail = self._current_detail
        field = ("question_text", "shared_text", "answer_text")[index]
        full_text = detail.get(field, "")
        presentation = _bound_presentation(detail)
        text = presentation[field] if presentation is not None else full_text
        images = detail.get("images", ())
        role = "question" if index == 0 else "shared_material" if index == 1 else "answer"
        selected_images = [
            image
            for image in (images if isinstance(images, (list, tuple)) else ())
            if isinstance(image, Mapping) and _text(image.get("role")) == role
        ]

        def add_text() -> None:
            if index == 2 and not text:
                layout.addWidget(_label("来源未提供完整答案文字；不自动补写。", muted=True))
            elif text:
                editor = QPlainTextEdit()
                editor.setReadOnly(True)
                editor.setPlainText(text)
                editor.setAccessibleName("完整题面" if index == 0 else "完整共同材料" if index == 1 else "来源答案文字")
                editor.setMinimumHeight(130)
                editor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                layout.addWidget(editor)
                if text != full_text:
                    toggle = QPushButton("展开完整识别文本")
                    toggle.setObjectName("QuietButton")
                    toggle.setAccessibleName("展开完整识别文本：" + ("题面", "共同材料", "答案")[index])
                    toggle.setCheckable(True)

                    def show_full(checked: bool) -> None:
                        editor.setPlainText(full_text if checked else text)
                        toggle.setText("返回易读文本" if checked else "展开完整识别文本")

                    toggle.toggled.connect(show_full)
                    layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
                    layout.addWidget(_label("仅折叠重复识别文字；原始图文、分值和来源记录保持不变。", muted=True))
            elif index == 1:
                layout.addWidget(_label("本题未记录可显示的共同材料关联；请对照整页原图确认是否还需材料。", muted=True))
            else:
                layout.addWidget(_label("本题暂无可显示的题面。", muted=True))

        images_first = index in (0, 1) and bool(selected_images)
        if not images_first:
            add_text()
        for image in selected_images:
            self._add_image(layout, image, required=index in (0, 1))
        if images_first and text:
            layout.addWidget(_label("识别文字（对照原图）", muted=True))
            add_text()
        layout.addStretch(1)

    def _add_image(self, layout: QVBoxLayout, image: Mapping[str, Any], *, required: bool) -> None:
        token = self._current_token
        if token is None:
            return
        image_id = _text(image.get("image_id"))
        role = _text(image.get("role"))
        image_key = (token, image_id, role)
        frame = QWidget()
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 4, 0, 4)
        caption = _text(image.get("caption")) or "来源图片"
        dimensions = ""
        if type(image.get("width")) is int and type(image.get("height")) is int:
            dimensions = f"（{image['width']}×{image['height']}像素）"
        frame_layout.addWidget(_label(caption + dimensions, muted=True))
        preview = _LocalImagePreview(frame)
        preview.setAccessibleName("个人图文题真实图片预览：" + caption)
        frame_layout.addWidget(preview)
        image_status = _label("正在读取来源图片…", muted=True)
        image_status.setAccessibleName("个人图文题图片读取状态")
        frame_layout.addWidget(image_status)
        original_button = QPushButton("查看整页原图")
        original_button.setObjectName("QuietButton")
        original_button.setAccessibleName("查看个人图文题整页原图：" + caption)
        original_button.setEnabled(False)
        frame_layout.addWidget(original_button, alignment=Qt.AlignmentFlag.AlignLeft)
        crop_button = QPushButton("恢复题面裁片")
        crop_button.setObjectName("QuietButton")
        crop_button.setAccessibleName("恢复个人图文题题面裁片：" + caption)
        crop_button.setEnabled(False)
        frame_layout.addWidget(crop_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(frame)
        self._image_previews[image_key] = preview
        self._image_original_buttons[image_key] = original_button
        self._image_crop_buttons[image_key] = crop_button
        edit_button = QPushButton("调整这张裁片…")
        edit_button.setObjectName("QuietButton")
        edit_button.setAccessibleName("调整个人图文题当前这张裁片：" + caption)
        edit_button.setEnabled(False)
        edit_button.setVisible(all(callable(getattr(self.facade, name, None)) for name in (
            "personal_visual_question_crop_options", "personal_visual_question_preview_crop",
            "personal_visual_question_save_crop", "personal_visual_question_discard_crop",
        )))
        frame_layout.addWidget(edit_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self._image_edit_crop_buttons[image_key] = edit_button
        edit_button.clicked.connect(lambda: self._edit_crop(token, image))
        self._load_image(
            token,
            image,
            preview,
            image_status,
            original_button,
            crop_button,
            required=required,
            original=False,
        )
        original_button.clicked.connect(
            lambda: self._load_image(
                token,
                image,
                preview,
                image_status,
                original_button,
                crop_button,
                required=False,
                original=True,
            )
        )
        crop_button.clicked.connect(
            lambda: self._load_image(
                token,
                image,
                preview,
                image_status,
                original_button,
                crop_button,
                required=False,
                original=False,
            )
        )

    def _load_image(
        self,
        token: tuple[str, str, str],
        image: Mapping[str, Any],
        preview: _LocalImagePreview,
        status: QLabel,
        original_button: QPushButton,
        crop_button: QPushButton,
        *,
        required: bool,
        original: bool,
    ) -> None:
        if self._closed or token != self._current_token:
            return
        image_id = _text(image.get("image_id"))
        role = _text(image.get("role"))
        image_key = (token, image_id, role)
        if not image_id:
            self._image_failed(
                token,
                image_key,
                preview,
                status,
                original_button,
                crop_button,
                required,
                original,
                "图片标识缺失。",
            )
            return
        states = self._image_original_states if original else self._image_states
        payloads = self._image_original_bytes if original else self._image_bytes
        ready_state = "ready"
        if states.get(image_key) == ready_state and image_key in payloads:
            data, caption = payloads[image_key]
            try:
                preview.set_bytes(data, caption)
            except Exception:  # noqa: BLE001 - cached bytes should remain fail-closed
                self._image_failed(
                    token,
                    image_key,
                    preview,
                    status,
                    original_button,
                    crop_button,
                    required and not original,
                    original,
                    "图片无法解码显示。",
                )
                return
            if original:
                status.setText("整页原图已显示；可能包含其他题目或答案，请核对范围。")
            else:
                status.setText("来源图片裁片已显示；可放大或查看整页原图。")
            original_button.setEnabled(True)
            crop_button.setEnabled(True)
            return
        states[image_key] = "loading"
        status.setText("正在读取整页原图…" if original else "正在读取来源图片…")
        generation = self._detail_generation

        def ready(value: object) -> None:
            if generation != self._detail_generation or token != self._current_token:
                return
            if not isinstance(value, Mapping) or not isinstance(value.get("bytes"), (bytes, bytearray)):
                self._image_failed(
                    token,
                    image_key,
                    preview,
                    status,
                    original_button,
                    crop_button,
                    required and not original,
                    original,
                    "未返回可显示的图片字节。",
                )
                return
            try:
                data = bytes(value["bytes"])
                caption = _text(value.get("caption")) or _text(image.get("caption")) or "来源图片"
                preview.set_bytes(data, caption)
            except Exception:  # noqa: BLE001 - decoder errors stay local and concise
                self._image_failed(
                    token,
                    image_key,
                    preview,
                    status,
                    original_button,
                    crop_button,
                    required and not original,
                    original,
                    "图片无法解码显示。",
                )
                return
            payloads[image_key] = (data, caption)
            states[image_key] = "ready"
            status.setText(
                "整页原图已显示；可能包含其他题目或答案，请核对范围。"
                if original
                else "来源图片裁片已显示；可放大或查看整页原图。"
            )
            crop_button.setEnabled(not original or image_key in self._image_bytes)
            if not original:
                original_button.setEnabled(True)
                self._image_failures.discard(token)
            self._update_actions()

        def failed(message: object) -> None:
            if generation != self._detail_generation or token != self._current_token:
                return
            self._image_failed(
                token,
                image_key,
                preview,
                status,
                original_button,
                crop_button,
                required and not original,
                original,
                str(message) or "图片暂时无法读取。",
            )

        self._submit(
            "读取个人图文题来源图片",
            lambda: self.facade.personal_visual_question_image(
                token[0], token[1], token[2], image_id, original=original
            ),
            ready,
            failed,
        )

    def _image_failed(
        self,
        token: tuple[str, str, str],
        image_key: tuple[tuple[str, str, str], str, str],
        preview: _LocalImagePreview,
        status: QLabel,
        original_button: QPushButton,
        crop_button: QPushButton,
        required: bool,
        original: bool,
        message: str,
    ) -> None:
        states = self._image_original_states if original else self._image_states
        states[image_key] = "failed"
        if original and image_key in self._image_bytes:
            data, caption = self._image_bytes[image_key]
            try:
                preview.set_bytes(data, caption)
                status.setText("整页原图暂时无法显示，已保留已验证的题面裁片。")
            except Exception:  # noqa: BLE001 - leave the failure visible
                preview.clear("整页原图暂时无法显示，题面裁片也无法恢复。")
                status.setText("图片读取失败：" + (message or "请核对本地来源。"))
        else:
            preview.clear("此来源图片读取失败，本题暂不能选择。" if required else "此来源图片暂时无法显示。")
            status.setText("图片读取失败：" + (message or "请核对本地来源。"))
        original_button.setEnabled(image_key in self._image_bytes)
        crop_button.setEnabled(image_key in self._image_bytes)
        if required:
            self._image_failures.add(token)
            self._selected.pop(token, None)
            self.saved = False
            self._sync_item_check(token)
            set_status(self.status, "attention", "本题有必需图片读取失败，未自动标记为可选。")
        self._update_actions()

    def _tab_changed(self, index: int) -> None:
        if index == 2:
            self._render_tab(2)

    def _required_images_ready(self, token: tuple[str, str, str]) -> bool:
        detail = self._current_detail
        if token != self._current_token or detail is None:
            return False
        for image in detail.get("images", ()):
            if not isinstance(image, Mapping) or _text(image.get("role")) not in {"question", "shared_material"}:
                continue
            image_id = _text(image.get("image_id"))
            image_key = (token, image_id, _text(image.get("role")))
            if not image_id or self._image_states.get(image_key) != "ready":
                return False
        return True

    def _is_selectable(self, token: tuple[str, str, str]) -> bool:
        row = self._rows.get(token)
        return bool(
            row
            and row.get("selection_ready") is True
            and token not in self._detail_failures
            and token not in self._image_failures
            and self._required_images_ready(token)
        )

    def _item_changed(self, item: QListWidgetItem) -> None:
        if self._rendering_list:
            return
        token = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(token, list):
            token = tuple(token)
        if not isinstance(token, tuple) or len(token) != 3:
            return
        token = tuple(str(value) for value in token)
        row = self._rows.get(token)
        if item.checkState() == Qt.CheckState.Checked:
            if not row or not self._is_selectable(token):
                self._rendering_list = True
                item.setCheckState(Qt.CheckState.Unchecked)
                self._rendering_list = False
                set_status(self.status, "attention", "请先打开本题并确认题面、公共材料及必需原图均已显示。")
                return
            old = self._selected.get(token, {})
            self._selected[token] = {"points": old.get("points", 2)}
        else:
            self._selected.pop(token, None)
        self.saved = False
        self._invalidate_reference()
        self._update_actions()

    def _sync_item_check(self, token: tuple[str, str, str]) -> None:
        for index in range(self.question_list.count()):
            item = self.question_list.item(index)
            value = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(value, list):
                value = tuple(value)
            if value != token:
                continue
            self._rendering_list = True
            item.setCheckState(
                Qt.CheckState.Checked if token in self._selected else Qt.CheckState.Unchecked
            )
            self._rendering_list = False
            return

    def _invalidate_reference(self) -> None:
        if self._reference_preview is None:
            return
        self._reference_preview = None
        self._reference_selection_key = ()
        self.reference_note.setText("选择已变化，请重新点击“预览备课选材”。")
        self.reference_materials.clear()
        self.reference_image_note.clear()

    def _update_actions(self) -> None:
        busy = self._catalog_busy or self._detail_busy or self._save_busy or self._reference_busy or self._attributes_busy or self._crop_busy
        has_selection = bool(self._selected)
        ref_ready = (
            self._reference_preview is not None
            and self._reference_selection_key == self._selection_key()
        )
        # An empty explicit save is meaningful: it clears an older persisted
        # selection.  Never make that mutation implicit on cancel or refresh.
        self.save_button.setEnabled(not busy and not self._closed)
        self.add_basket_button.setEnabled(has_selection and not busy and not self._closed)
        self.preview_reference_button.setEnabled(has_selection and not busy)
        self.accept_preparation_button.setEnabled(ref_ready and not busy)
        self.reload_button.setEnabled(not self._catalog_busy and not self._attributes_busy and not self._crop_busy and not self._closed)
        self.question_list.setEnabled(not self._catalog_busy and not self._attributes_busy and not self._crop_busy and not self._closed)
        for control in (self.batch_combo, self.search, self.filter_panel):
            control.setEnabled(not self._attributes_busy and not self._crop_busy and not self._closed)
        for image_key, button in self._image_edit_crop_buttons.items():
            button.setEnabled(not busy and not self._closed and image_key[0] == self._current_token
                              and self._current_detail is not None
                              and self._image_states.get(image_key) == "ready")
        token = self._current_token
        self.attributes_button.setEnabled(
            not busy and not self._closed and self._current_detail is not None
            and token is not None and token not in self._detail_failures
            and self._required_images_ready(token)
        )
        self.cancel_button.setEnabled(not self._attributes_saving and not self._crop_busy)
        self.selection_count.setText(
            f"已选 {len(self._selected)} 题；勾选不会自动保存，答案默认隐藏。"
        )

    def _edit_crop(self, token, image) -> None:
        image_id, role = _text(image.get("image_id")), _text(image.get("role"))
        button = self._image_edit_crop_buttons.get((token, image_id, role))
        if token != self._current_token or button is None or not button.isEnabled():
            return
        from .personal_visual_crop_dialog import PersonalVisualCropDialog

        generation = self._detail_generation
        self._crop_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在本机读取这张裁片的原页、当前范围及关联题目…")

        def failed(_message):
            self._crop_busy = False
            set_status(self.status, "error", "这张裁片的原页或关联范围暂时无法核对，尚未保存。请刷新后重试。")
            self._update_actions()

        def ready(options):
            if token != self._current_token or generation != self._detail_generation:
                failed(None)
                return
            try:
                if (not isinstance(options, Mapping)
                        or tuple(options.get(key) for key in ("batch_id", "key", "revision")) != token
                        or options.get("image_id") != image_id or options.get("role") != role):
                    raise ValueError("Crop target changed")
                editor = PersonalVisualCropDialog(self.facade, self.tasks, options, self)
            except (KeyError, TypeError, ValueError, RuntimeError):
                failed(None)
                return
            accepted = editor.exec() == QDialog.DialogCode.Accepted
            result = deepcopy(editor.saved_result)
            editor.deleteLater()
            self._crop_busy = False
            if accepted and result is not None:
                self._crop_saved(token, options, result)
            else:
                set_status(self.status, "info", "已取消裁剪调整；来源原页、裁片与本窗口选题均未改动。")
                self._update_actions()

        self._submit(
            "读取个人图片题裁剪范围",
            lambda: self.facade.personal_visual_question_crop_options(*token, image_id),
            ready, failed,
        )

    def _crop_saved(self, token, options, result) -> None:
        # The backend freezes the full dependency scope, including questions
        # hidden by current filters. Never invalidate an unrelated basket entry.
        affected_keys = set(options["affected_question_keys"])
        removed = sum(1 for entry in self._selected if entry[0] == token[0] and entry[1] in affected_keys)
        self._selected = {entry: choice for entry, choice in self._selected.items()
                          if entry[0] != token[0] or entry[1] not in affected_keys}
        self._invalidate_reference()
        self._detail_generation += 1
        affected_tokens = [entry for entry in self._rows if entry[0] == token[0] and entry[1] in affected_keys]
        for entry in affected_tokens:
            self._detail_cache.pop(entry, None)
            self._detail_failures.add(entry)
            self._rows[entry]["selection_ready"] = False
            self._rows[entry]["warnings"] = ["裁剪已修订；请刷新并重新核对题面。"]
        self._clear_current_detail()
        try:
            if result["batch_id"] != token[0]:
                raise ValueError("Changed batch")
            changes = result["revision_changes"]
            rows = [_normalise_row(row, default_batch_id=token[0]) for row in result["affected_rows"]]
            if (not isinstance(changes, list) or len(changes) != len(affected_keys)
                    or {item["key"] for item in changes} != affected_keys
                    or len(rows) != len(affected_keys) or any(row is None for row in rows)):
                raise ValueError("Changed affected scope")
            row_map = {row["key"]: row for row in rows}
            if set(row_map) != affected_keys:
                raise ValueError("Changed affected rows")
            for change in changes:
                row = row_map[change["key"]]
                if (row["batch_id"] != token[0] or row["revision"] != change["new_revision"]
                        or not change["new_revision"] or change["new_revision"] == change["old_revision"]):
                    raise ValueError("Changed crop revision")
                if change["key"] == token[1] and change["old_revision"] != token[2]:
                    raise ValueError("Changed original question")
            for entry in affected_tokens:
                self._rows.pop(entry, None)
            for row in rows:
                self._rows[_row_token(row)] = row
            self._current_token = _row_token(row_map[token[1]])
        except (KeyError, TypeError, ValueError):
            self._apply_filters()
            set_status(self.status, "attention", "裁剪保存结果暂时无法完整核对。受影响题已取消选择，请刷新后重核；其他未保存选题仍保留。")
            self._update_actions()
            return
        selections = deepcopy(self.selections)
        self._catalog_generation += 1
        generation = self._catalog_generation
        self._catalog_busy = self._crop_busy = True
        self._update_actions()

        def refreshed(value):
            self._crop_busy = False
            if not isinstance(value, Mapping):
                refresh_failed(None)
                return
            self._catalog_ready(generation, {**value, "selection": selections})
            if self.status.objectName() != "StatusError":
                set_status(self.status, "success", f"裁剪已保存，影响 {len(affected_keys)} 道题；其中 {removed} 道已取消勾选，请重新看图选用。旧标签保留待重核；其他选题保留。已带入备课的副本请重新带入。")
            self._update_actions()

        def refresh_failed(_message):
            self._crop_busy = self._catalog_busy = False
            self._apply_filters()
            set_status(self.status, "attention", "裁剪已保存，但目录刷新暂时失败。受影响题已取消选择，旧标签保留待重核；其他选题保留。请刷新；备课副本需重新带入。")
            self._update_actions()

        self._submit("刷新裁剪修订后的图片题", lambda: self.facade.personal_visual_questions(self.batch_id), refreshed, refresh_failed)

    def _edit_attributes(self) -> None:
        token = self._current_token
        if (token is None or not self._current_detail or not self.attributes_button.isEnabled()
                or not callable(getattr(self.facade, "personal_visual_question_attribute_options", None))):
            return
        from .personal_visual_attributes_dialog import PersonalVisualAttributesDialog

        question = deepcopy(self._current_detail)
        question["title"] = question.get("title") or "当前图片题"
        generation = self._catalog_generation
        self._attributes_busy = True
        self._update_actions()
        set_status(self.status, "info", "正在本机读取本题标签与修订历史；原图和题篮保持不变…")

        def failed(message):
            self._attributes_busy = self._attributes_saving = False
            set_status(self.status, "error", str(message) or "标签暂时无法保存，请重试。原图与选择未改变。")
            self._update_actions()

        def options_ready(options):
            if token != self._current_token or generation != self._catalog_generation:
                failed("当前题目已变化，请重新打开本题标签。尚未保存。")
                return
            try:
                editor = PersonalVisualAttributesDialog(question, options, self)
            except (KeyError, TypeError, ValueError):
                failed("本题教学属性暂时无法读取，请刷新后重试。")
                return
            accepted = editor.exec() == QDialog.DialogCode.Accepted
            updates = deepcopy(editor.updates)
            expected = editor.expected_attribute_revision
            confirmed = editor.teacher_confirmed
            editor.deleteLater()
            if not accepted or not updates:
                self._attributes_busy = False
                self._update_actions()
                set_status(self.status, "info", "已取消标签修改；原图、标签及题篮均未改变。")
                return
            self._attributes_saving = True
            self._update_actions()
            set_status(self.status, "info", "正在保存本题个人教学标签…")
            self._submit(
                "保存图片题个人教学标签",
                lambda: self.facade.personal_visual_question_save_attributes(
                    *token, updates, expected_attribute_revision=expected,
                    teacher_confirmed=confirmed,
                ),
                lambda result: self._attributes_saved(token, result),
                failed,
            )

        self._submit(
            "读取图片题教学标签",
            lambda: self.facade.personal_visual_question_attribute_options(*token),
            options_ready,
            failed,
        )

    def _attributes_saved(self, token, result) -> None:
        self._attributes_saving = False
        try:
            detail = _normalise_row(result["detail"], default_batch_id=token[0])
            if detail is None or _row_token(detail) != token:
                raise ValueError("Changed content revision")
            if result["attribute_revision"] != result["attributes"]["revision"]:
                raise ValueError("Changed attribute revision")
        except (KeyError, TypeError, ValueError):
            self._attributes_busy = False
            set_status(self.status, "error", "标签保存结果暂时无法核对，请刷新目录确认。当前原图与题篮已保留。")
            self._update_actions()
            return
        self._rows[token] = detail
        self._detail_cache[token] = deepcopy(detail)
        if token == self._current_token:
            self._detail_ready(self._detail_generation, token, detail)
        self._invalidate_reference()
        # A label-only refresh must never restore an older persisted selection
        # over the teacher's still-unsaved basket in this window.
        selections = deepcopy(self.selections)
        self._catalog_generation += 1
        generation = self._catalog_generation
        self._catalog_busy = True
        self._update_actions()

        def refreshed(value):
            self._attributes_busy = False
            if not isinstance(value, Mapping):
                refresh_failed("标签已保存，但目录刷新未返回可读结果。")
                return
            local_view = dict(value)
            local_view["selection"] = selections
            self._catalog_ready(generation, local_view)
            if self.status.objectName() != "StatusError":
                set_status(self.status, "success", "本题个人标签已保存，筛选已刷新；原图、原档案与本窗口其他选题均未改动。")
            self._update_actions()

        def refresh_failed(_message):
            self._attributes_busy = self._catalog_busy = False
            set_status(self.status, "attention", "标签已保存，目录刷新暂时失败；当前题面与其他选题仍保留。可点击刷新重试。")
            self._update_actions()

        self._submit(
            "刷新图片题教学标签筛选",
            lambda: self.facade.personal_visual_questions(self.batch_id),
            refreshed,
            refresh_failed,
        )

    def _add_to_basket(self) -> None:
        if self._save_busy or self._closed or not self._selected:
            return
        payload = deepcopy(self.selections)
        self._save_busy = True
        self._update_actions()

        def added(count):
            self._save_busy = False
            set_status(self.status, "success", f"所选题目已按完整主题加入；题篮现有 {count} 项。到组卷页可调整顺序并预览实际分页，已有主题不会重复加入。")
            self._update_actions()

        self._submit("图片题整主题加入统一题篮",
                     lambda: self.facade.add_personal_visual_questions_to_basket(payload),
                     added, self._selection_save_failed)

    def _save_selection(self) -> None:
        if self._save_busy or self._closed:
            return
        self._save_busy = True
        payload = deepcopy(self.selections)
        self._update_actions()
        self._submit(
            "保存个人图文题选择",
            lambda: self.facade.save_personal_visual_selection(payload),
            self._selection_saved,
            self._selection_save_failed,
        )

    def _selection_saved(self, _value: object) -> None:
        self._save_busy = False
        self.saved = True
        message = (
            f"已保存 {len(self._selected)} 道个人图文题选择；可稍后重新打开恢复。"
            if self._selected
            else "已清空个人图文题选择；可稍后重新打开恢复。"
        )
        set_status(self.status, "success", message)
        self._update_actions()

    def _selection_save_failed(self, message: object) -> None:
        self._save_busy = False
        set_status(self.status, "error", str(message) or "个人图文题选择暂时无法保存。")
        self._update_actions()

    def _preview_reference(self) -> None:
        if self._reference_busy or self._closed:
            return
        if not self._selected:
            set_status(self.status, "attention", "请先核对并勾选至少一道题，再预览备课选材。")
            return
        self._reference_generation += 1
        generation = self._reference_generation
        selection_key = self._selection_key()
        payload = deepcopy(self.selections)
        self._reference_busy = True
        self._reference_preview = None
        self._reference_selection_key = ()
        self.reference_note.setText("正在编译选中题所属完整主题、全部小题与公共材料…")
        self.reference_materials.clear()
        self.reference_image_note.clear()
        self._update_actions()
        self._submit(
            "预览个人图文题备课选材",
            lambda: self.facade.personal_visual_question_reference(payload),
            lambda value: self._reference_ready(generation, selection_key, value),
            lambda message: self._reference_failed(generation, message),
        )

    def _reference_ready(self, generation: int, selection_key: tuple, value: object) -> None:
        if generation != self._reference_generation:
            return
        self._reference_busy = False
        if not isinstance(value, Mapping):
            self._reference_failed(generation, "备课选材返回格式无法核对。")
            return
        reference = deepcopy(dict(value))
        self._reference_preview = reference
        self._reference_selection_key = selection_key
        materials = _materials_text(reference.get("materials"))
        self.reference_materials.setPlainText(materials or "来源未返回可显示的完整材料。")
        image_count = reference.get("image_count")
        if type(image_count) is not int:
            image_values = reference.get("images", reference.get("image_assets", ()))
            image_count = len(image_values) if isinstance(image_values, (list, tuple)) else 0
        warnings = [
            warning
            for warning in reference.get("warnings", ())
            if isinstance(warning, str) and warning
        ]
        image_note = f"参考包含 {image_count} 张图片。"
        if warnings:
            image_note += "\n图片或范围提醒：" + "；".join(warnings)
        self.reference_image_note.setText(image_note)
        self.reference_note.setText(
            "材料为只读预览，包含选中题所属完整主题、全部小题与公共材料；不表示统一组卷已完成。"
        )
        self.tabs.setCurrentIndex(3)
        set_status(self.status, "success", "备课选材预览已生成；请核对完整材料后明确点击“带入备课”。")
        self._update_actions()

    def _reference_failed(self, generation: int, message: object) -> None:
        if generation != self._reference_generation:
            return
        self._reference_busy = False
        set_status(self.status, "error", str(message) or "备课选材暂时无法预览。")
        self.reference_note.setText("备课选材预览失败；没有接受任何参考。")
        self._update_actions()

    def _accept_preparation(self) -> None:
        if (
            self._reference_preview is None
            or self._reference_selection_key != self._selection_key()
            or self._closed
        ):
            return
        self.preparation_reference = deepcopy(self._reference_preview)
        self._closed = True
        self.done(QDialog.DialogCode.Accepted)

    def _cancel_jobs(self) -> None:
        for task_id in tuple(self._jobs.values()):
            if not task_id:
                continue
            try:
                cancel = getattr(self.tasks, "cancel", None)
                if callable(cancel):
                    cancel(task_id)
            except (RuntimeError, TypeError):
                pass
        self._jobs.clear()

    def reject(self) -> None:
        if self._closed:
            return
        if self._attributes_saving or self._crop_busy:
            set_status(self.status, "attention", "请先完成或取消当前标签、裁剪操作，再关闭题库。")
            return
        self._closed = True
        self._catalog_generation += 1
        self._detail_generation += 1
        self._reference_generation += 1
        self._cancel_jobs()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.reject()
        if self._closed:
            event.accept()
        else:
            event.ignore()


__all__ = ["PersonalVisualQuestionDialog"]
