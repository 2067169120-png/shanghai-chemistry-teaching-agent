"""Local, catalogue-bound teaching edits with an explicit comparison step."""

from __future__ import annotations

from copy import deepcopy

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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_word_question_attributes import (
    EXAM_TYPE_LABELS,
    GRADE_LABELS,
    WordQuestionAttributeError,
    apply_teacher_edits,
    build_teacher_updates,
    validate_attributes,
)
from .components import page_scroll, set_status


def _label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


def _combo(name):
    widget = QComboBox()
    widget.setAccessibleName(name)
    widget.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    widget.setMinimumContentsLength(8)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return widget


def _checklist(name, rows, selected):
    widget = QListWidget()
    widget.setAccessibleName(name)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setFixedHeight(142)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for key, label in rows:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked
        )
        widget.addItem(item)
    return widget


def _checked(widget):
    return [
        widget.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(widget.count())
        if widget.item(index).checkState() == Qt.CheckState.Checked
    ]


def attribute_summary(row):
    """Teaching fields only; source paths and source images are not displayed here."""
    return {
        "主知识点": row["primary_knowledge"]["label"],
        "辅助知识点": "、".join(item["label"] for item in row["supporting_knowledge"])
        or "无",
        "适用年级": "、".join(
            GRADE_LABELS[value] for value in row["applicable_grades"]["values"]
        )
        or "待确认",
        "原考试类型": EXAM_TYPE_LABELS.get(
            row["original_source"]["exam_type"]["value"], "原考试待确认"
        ),
        "教材节": "；".join(
            f"{item['section_key']} {item['label']}"
            for item in row["curriculum_candidates"]
        )
        or "待映射",
        "教师备注": row["teacher_note"] or "未填写",
    }


class WordQuestionAttributesDialog(QDialog):
    """Accepting returns an edit proposal; this dialog never writes a store."""

    def __init__(self, item, options, parent=None):
        super().__init__(parent)
        self.attributes = validate_attributes(options["attributes"])
        if (
            self.attributes["key"] != item["key"]
            or self.attributes["question_revision"] != item["revision"]
        ):
            raise WordQuestionAttributeError("教学属性与当前题目版本不一致。")
        self.catalog = deepcopy(options["catalog"])
        self.expected_attribute_revision = self.attributes["revision"]
        self.expected_stored_revision = options.get("stored_revision")
        self.updates = None
        self._proposal = None
        self.setWindowTitle("修改教学标签")
        self.resize(720, 780)
        self.setMinimumSize(400, 520)
        outer = QVBoxLayout(self)
        outer.addWidget(_label(item.get("title") or "当前 Word 题目"))
        outer.addWidget(
            _label(
                "只修改个人教学标签，不改原题或公共材料。教师确认仅表示标注来源，不代表化学正确性或正式审核通过。"
            )
        )
        warning = options.get("warning")
        if isinstance(warning, str) and warning:
            outer.addWidget(_label(warning))
        self.pages = QStackedWidget()
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        form = QFormLayout(content)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.primary_combo = _combo("主知识点，从现有知识目录选择")
        self.primary_combo.addItem("待标记 / 暂不映射规范知识点", "unknown")
        knowledge = self.catalog.get("knowledge_points", [])
        for entry in knowledge:
            self.primary_combo.addItem(f"{entry['id']} · {entry['name']}", entry["id"])
        self.primary_combo.setCurrentIndex(
            max(
                0,
                self.primary_combo.findData(self.attributes["primary_knowledge"]["id"]),
            )
        )
        form.addRow("主知识点", self.primary_combo)
        self.supporting_list = _checklist(
            "辅助知识点，可多选",
            [(entry["id"], f"{entry['id']} · {entry['name']}") for entry in knowledge],
            {entry["id"] for entry in self.attributes["supporting_knowledge"]},
        )
        form.addRow("辅助知识点（不得重复主知识点）", self.supporting_list)
        grades = QWidget()
        grade_layout = QHBoxLayout(grades)
        grade_layout.setContentsMargins(0, 0, 0, 0)
        self.grade_checks = {}
        for key, label in GRADE_LABELS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in self.attributes["applicable_grades"]["values"])
            checkbox.setAccessibleName("适用年级 " + label)
            self.grade_checks[key] = checkbox
            grade_layout.addWidget(checkbox)
        grade_layout.addStretch(1)
        form.addRow("适用年级（可多选，不等于原题年级）", grades)
        self.exam_combo = _combo("原考试类型，不确定时保留待确认")
        for key, label in EXAM_TYPE_LABELS.items():
            self.exam_combo.addItem(label, key)
        self.exam_combo.setCurrentIndex(
            max(
                0,
                self.exam_combo.findData(
                    self.attributes["original_source"]["exam_type"]["value"]
                ),
            )
        )
        form.addRow("原考试类型", self.exam_combo)
        form.addRow(
            _label(
                "原题年份、地区、年级及引文保持原样。修订考试类型请在备注说明依据；不要按资料包名称推断。"
            )
        )
        self.curriculum_search = QLineEdit()
        self.curriculum_search.setPlaceholderText("搜索教材节名称或编号")
        self.curriculum_search.setAccessibleName("筛选教材节候选")
        form.addRow("教材节（可多选；不勾选表示待映射）", self.curriculum_search)
        self.curriculum_list = _checklist(
            "现有教材节目录，可多选",
            [
                (entry["node_key"], f"{entry['node_key']} · {entry['section_title']}")
                for entry in self.catalog.get("nodes", [])
            ],
            {
                entry["section_key"]
                for entry in self.attributes["curriculum_candidates"]
            },
        )
        form.addRow(self.curriculum_list)
        self.curriculum_search.textChanged.connect(self._filter_sections)
        self.teacher_note = QPlainTextEdit(self.attributes["teacher_note"])
        self.teacher_note.setAccessibleName("教师备注，最多 2000 字")
        self.teacher_note.setPlaceholderText(
            "教学用途、原考试修订依据或仍需核对的事项（最多 2000 字）"
        )
        self.teacher_note.setFixedHeight(112)
        form.addRow("教师备注", self.teacher_note)
        self.history_view = QPlainTextEdit()
        self.history_view.setReadOnly(True)
        self.history_view.setAccessibleName("自动建议与教师标签修订历史")
        self.history_view.setFixedHeight(156)
        history = options.get("history") or [self.attributes]
        entries = []
        for raw in history:
            entry = validate_attributes(raw)
            if entry["key"] != self.attributes["key"]:
                raise WordQuestionAttributeError("标签历史与当前题目不一致。")
            origin = (
                "教师修订"
                if entry["annotation_source"] == "teacher_modified"
                else "自动建议（未教师确认）"
            )
            entries.append(
                f"版本 {entry['edit_version']} · {origin}\n"
                + "\n".join(
                    f"{key}：{value}" for key, value in attribute_summary(entry).items()
                )
            )
        self.history_view.setPlainText("\n\n".join(entries))
        form.addRow("原自动建议与修订历史（只读）", self.history_view)
        self.body_scroll = page_scroll(content)
        self.pages.addWidget(self.body_scroll)
        comparison = QWidget()
        comparison_layout = QVBoxLayout(comparison)
        comparison_layout.addWidget(
            _label("核对以下变更。未列出的标签、原题内容、引文与公共材料均不改变。")
        )
        self.comparison = QPlainTextEdit()
        self.comparison.setReadOnly(True)
        self.comparison.setAccessibleName("教学标签保存前后对照")
        comparison_layout.addWidget(self.comparison)
        self.pages.addWidget(comparison)
        outer.addWidget(self.pages, 1)
        self.status = _label("")
        outer.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("QuietButton")
        self.cancel_button.clicked.connect(self.reject)
        self.back_button = QPushButton("返回修改")
        self.back_button.setObjectName("QuietButton")
        self.back_button.clicked.connect(self._back)
        self.back_button.hide()
        self.preview_button = QPushButton("预览变更")
        self.preview_button.clicked.connect(self._preview)
        self.save_button = QPushButton("确认保存修改")
        self.save_button.clicked.connect(self._confirm)
        self.save_button.hide()
        for button in (
            self.cancel_button,
            self.back_button,
            self.preview_button,
            self.save_button,
        ):
            controls.addWidget(button)
        outer.addLayout(controls)

    def _filter_sections(self, text):
        query = text.strip().casefold()
        for index in range(self.curriculum_list.count()):
            item = self.curriculum_list.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))

    def _preview(self):
        try:
            proposal = build_teacher_updates(
                self.attributes,
                {
                    "primary_knowledge_id": self.primary_combo.currentData(),
                    "supporting_knowledge_ids": _checked(self.supporting_list),
                    "applicable_grades": [
                        key
                        for key, widget in self.grade_checks.items()
                        if widget.isChecked()
                    ],
                    "original_exam_type": self.exam_combo.currentData(),
                    "curriculum_section_keys": _checked(self.curriculum_list),
                    "teacher_note": self.teacher_note.toPlainText(),
                },
                self.catalog,
            )
            if not proposal:
                set_status(self.status, "info", "尚无变更；可继续修改或取消。")
                return
            after = apply_teacher_edits(
                self.attributes, proposal, curriculum_entries=self.catalog
            )
        except (KeyError, TypeError, WordQuestionAttributeError) as error:
            set_status(
                self.status,
                "error",
                getattr(error, "message_zh", "标签候选暂时无法读取，请关闭后重试。"),
            )
            return
        old, new = attribute_summary(self.attributes), attribute_summary(after)
        self.comparison.setPlainText(
            "\n\n".join(
                f"{field}\n修改前：{old[field]}\n修改后：{new[field]}"
                for field in old
                if old[field] != new[field]
            )
        )
        self._proposal = proposal
        self.pages.setCurrentIndex(1)
        self.preview_button.hide()
        self.back_button.show()
        self.save_button.show()
        set_status(
            self.status, "info", "确认后由本机保存个人标签并保留历史。此处尚未写入。"
        )

    def _back(self):
        self._proposal = None
        self.pages.setCurrentIndex(0)
        self.preview_button.show()
        self.back_button.hide()
        self.save_button.hide()
        self.status.clear()

    def _confirm(self):
        if self.pages.currentIndex() == 1 and self._proposal:
            self.updates = deepcopy(self._proposal)
            self.accept()

    def reject(self):
        self.updates = None
        self._proposal = None
        super().reject()
