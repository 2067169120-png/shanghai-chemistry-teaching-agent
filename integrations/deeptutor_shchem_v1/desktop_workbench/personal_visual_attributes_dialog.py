"""Local visual-question labels with a separate before/after confirmation."""

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

from ..desktop_personal_visual_attributes import (
    EXAM_TYPE_LABELS,
    GRADE_LABELS,
    TEACHING_USE_LABELS,
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


def _combo(label, values, selected):
    widget = QComboBox()
    widget.setAccessibleName(label)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    for value, text in values:
        widget.addItem(text, value)
    widget.setCurrentIndex(max(0, widget.findData(selected)))
    return widget


def _checklist(label, values, selected):
    widget = QListWidget()
    widget.setAccessibleName(label)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setFixedHeight(116)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for value, text in values:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if value in selected else Qt.CheckState.Unchecked
        )
        widget.addItem(item)
    return widget


def _checked(widget):
    return [
        widget.item(index).data(Qt.ItemDataRole.UserRole)
        for index in range(widget.count())
        if widget.item(index).checkState() == Qt.CheckState.Checked
    ]


def _summary(row):
    source = row["original_source"]
    return {
        "主知识点": row["primary_knowledge"]["label"],
        "辅助知识点": "、".join(item["label"] for item in row["supporting_knowledge"])
        or "无",
        "教材节": "；".join(item["label"] for item in row["curriculum_candidates"])
        or "待映射",
        "适用年级": "、".join(
            GRADE_LABELS[value] for value in row["applicable_grades"]["values"]
        )
        or "待确认",
        "原题年级（个人修订）": GRADE_LABELS.get(source["grade"]["value"], "待确认"),
        "考试类型（个人修订）": EXAM_TYPE_LABELS.get(
            source["exam_type"]["value"], "待确认"
        ),
        **{
            label: source[field]["value"]
            if source[field]["value"] != "unknown"
            else "待确认"
            for field, label in (
                ("year", "年份（个人修订）"),
                ("region", "地区（个人修订）"),
                ("school", "学校（个人修订）"),
            )
        },
        "教学用途": "、".join(
            TEACHING_USE_LABELS.get(value, value) for value in row["teaching_use_tags"]
        )
        or "未指定",
        "教师备注": row["teacher_note"] or "未填写",
        "本次标签经教师确认": "是（不代表化学审核）"
        if row["teacher_confirmed"]
        else "否",
    }


class PersonalVisualAttributesDialog(QDialog):
    """Returns a proposal only; persistence belongs to the personal service."""

    def __init__(self, item, options, parent=None):
        super().__init__(parent)
        self.attributes = validate_attributes(options["attributes"])
        if (
            self.attributes["key"] != item["key"]
            or self.attributes["question_revision"] != item["revision"]
            or self.attributes["source_binding"]["batch_id"] != item["batch_id"]
        ):
            raise ValueError("标签与当前图片题版本不一致。")
        self.catalog = deepcopy(options["catalog"])
        self.expected_attribute_revision = self.attributes["revision"]
        self.updates = self._proposal = None
        self.teacher_confirmed = False
        self._proposal_confirmed = False
        self.setWindowTitle("编辑本题标签 · 图片题")
        self.resize(760, 780)
        self.setMinimumSize(420, 520)
        root = QVBoxLayout(self)
        root.addWidget(_label(item.get("title") or "当前图片题"))
        root.addWidget(
            _label(
                "只修订个人教学标签；年份、地区、学校与考试类型也是个人修订，不改原图、公共材料或原档案出处。"
            )
        )
        if options.get("warning"):
            root.addWidget(_label(str(options["warning"])))
        self.pages = QStackedWidget()
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        knowledge = [
            (row["id"], row["name"]) for row in self.catalog.get("knowledge_points", [])
        ]
        self.primary_combo = _combo(
            "主知识点",
            [("unknown", "待标记"), *knowledge],
            self.attributes["primary_knowledge"]["id"],
        )
        form.addRow("主知识点", self.primary_combo)
        self.supporting_list = _checklist(
            "辅助知识点，可多选",
            knowledge,
            {row["id"] for row in self.attributes["supporting_knowledge"]},
        )
        form.addRow("辅助知识点（不重复主知识点）", self.supporting_list)
        self.curriculum_search = QLineEdit()
        self.curriculum_search.setPlaceholderText("搜索教材册、章、节名称")
        self.curriculum_search.setAccessibleName("筛选教材节")
        form.addRow("对应教材节（可多选）", self.curriculum_search)
        nodes = self.catalog.get("nodes", [])
        self.curriculum_list = _checklist(
            "教材册章节，可多选",
            [
                (
                    row["node_key"],
                    " / ".join(
                        str(value)
                        for value in (
                            row.get("volume_title") or row.get("volume_id"),
                            row.get("chapter_title") or row.get("chapter_id"),
                            row["section_title"],
                        )
                        if value
                    ),
                )
                for row in nodes
            ],
            {row["section_key"] for row in self.attributes["curriculum_candidates"]},
        )
        form.addRow(self.curriculum_list)
        self.curriculum_search.textChanged.connect(self._filter_sections)
        grades = QWidget()
        grade_layout = QHBoxLayout(grades)
        grade_layout.setContentsMargins(0, 0, 0, 0)
        self.grade_checks = {}
        for value, label in GRADE_LABELS.items():
            checkbox = QCheckBox(label)
            checkbox.setAccessibleName("适用年级 " + label)
            checkbox.setChecked(value in self.attributes["applicable_grades"]["values"])
            self.grade_checks[value] = checkbox
            grade_layout.addWidget(checkbox)
        grade_layout.addStretch(1)
        form.addRow("适用年级（不等于原题年级）", grades)
        source = self.attributes["original_source"]
        self.original_grade = _combo(
            "原题年级个人修订",
            [("unknown", "待确认"), *GRADE_LABELS.items()],
            source["grade"]["value"],
        )
        self.exam_combo = _combo(
            "考试类型个人修订", EXAM_TYPE_LABELS.items(), source["exam_type"]["value"]
        )
        form.addRow("原题年级（个人修订）", self.original_grade)
        form.addRow("考试类型（个人修订）", self.exam_combo)
        self.source_fields = {}
        for field, label in (("year", "年份"), ("region", "地区"), ("school", "学校")):
            value = source[field]["value"]
            editor = QLineEdit("" if value == "unknown" else value)
            editor.setPlaceholderText("不明确时留空，保留待确认")
            editor.setAccessibleName(label + "个人修订")
            self.source_fields[field] = editor
            form.addRow(label + "（个人修订）", editor)
        self.use_list = _checklist(
            "教学用途，可多选",
            [
                (row["id"], row["name"])
                for row in self.catalog.get("teaching_use_tags", [])
            ],
            self.attributes["teaching_use_tags"],
        )
        form.addRow("教学用途", self.use_list)
        self.teacher_note = QPlainTextEdit(self.attributes["teacher_note"])
        self.teacher_note.setAccessibleName("教师备注及个人出处修订依据")
        self.teacher_note.setPlaceholderText("说明教学用途或出处修订依据；最多 2000 字")
        self.teacher_note.setFixedHeight(96)
        form.addRow("备注 / 修订依据", self.teacher_note)
        self.confirm_tags = QCheckBox("本次修改已由我核对，标记为“教师确认的教学标签”")
        self.confirm_tags.setAccessibleName("明确确认本次教学标签，不代表化学审核")
        form.addRow(self.confirm_tags)
        form.addRow(
            _label(
                "默认不勾选；确认仅针对本次个人标签，不代表原题、答案或化学正确性已审核。"
            )
        )
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setAccessibleName("图片题个人标签修订历史")
        self.history.setFixedHeight(116)
        entries = []
        for raw in options.get("history") or [self.attributes]:
            row = validate_attributes(raw)
            if row["key"] != self.attributes["key"]:
                raise ValueError("标签历史与当前图片题不一致。")
            entries.append(
                "\n".join(f"{label}：{value}" for label, value in _summary(row).items())
            )
        self.history.setPlainText("\n\n".join(entries))
        form.addRow("修订历史（只读）", self.history)
        self.pages.addWidget(page_scroll(form_widget))
        self.comparison = QPlainTextEdit()
        self.comparison.setReadOnly(True)
        self.comparison.setAccessibleName("标签保存前后完整对照")
        self.pages.addWidget(self.comparison)
        root.addWidget(self.pages, 1)
        self.status = _label("先修改标签，再预览变更；本窗口不会调用模型。")
        root.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.back_button = QPushButton("返回修改")
        self.preview_button = QPushButton("预览变更")
        self.save_button = QPushButton("确认保存修改")
        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self._back)
        self.preview_button.clicked.connect(self._preview)
        self.save_button.clicked.connect(self._confirm)
        for button in (
            self.cancel_button,
            self.back_button,
            self.preview_button,
            self.save_button,
        ):
            button.setAutoDefault(False)
            controls.addWidget(button)
        self.cancel_button.setDefault(True)
        self.back_button.hide()
        self.save_button.hide()
        root.addLayout(controls)

    def _filter_sections(self, value):
        query = value.strip().casefold()
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
                        for key, checkbox in self.grade_checks.items()
                        if checkbox.isChecked()
                    ],
                    "original_grade": self.original_grade.currentData(),
                    "original_exam_type": self.exam_combo.currentData(),
                    **{
                        "original_" + field: widget.text().strip() or "unknown"
                        for field, widget in self.source_fields.items()
                    },
                    "curriculum_section_keys": _checked(self.curriculum_list),
                    "teaching_use_tags": _checked(self.use_list),
                    "teacher_note": self.teacher_note.toPlainText(),
                },
                self.catalog,
            )
            if not proposal:
                set_status(self.status, "info", "尚无标签变更；可继续修改或取消。")
                return
            confirmed = self.confirm_tags.isChecked()
            after = apply_teacher_edits(
                self.attributes,
                proposal,
                curriculum_entries=self.catalog,
                teacher_confirmed=confirmed,
            )
            before, after = _summary(self.attributes), _summary(after)
            self.comparison.setPlainText(
                "只保存以下个人标签修改；原图、原档案、答案及题篮不变。\n\n"
                + "\n\n".join(
                    f"{label}\n修改前：{before[label]}\n修改后：{after[label]}"
                    for label in before
                    if before[label] != after[label]
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            set_status(
                self.status,
                "error",
                getattr(exc, "message_zh", str(exc)) or "标签内容暂时无法核对。",
            )
            return
        self._proposal = proposal
        self._proposal_confirmed = confirmed
        self.pages.setCurrentIndex(1)
        self.preview_button.hide()
        self.back_button.show()
        self.save_button.show()
        set_status(
            self.status, "info", "已生成前后对照；点击确认后才会保存到个人标签库。"
        )

    def _back(self):
        self._proposal = None
        self.pages.setCurrentIndex(0)
        self.preview_button.show()
        self.back_button.hide()
        self.save_button.hide()

    def _confirm(self):
        if self.pages.currentIndex() == 1 and self._proposal:
            self.updates = deepcopy(self._proposal)
            self.teacher_confirmed = self._proposal_confirmed
            self.accept()

    def reject(self):
        self.updates = self._proposal = None
        self.teacher_confirmed = False
        super().reject()
