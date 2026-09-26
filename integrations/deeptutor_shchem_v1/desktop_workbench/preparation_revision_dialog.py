"""Plain-language offline lesson editor; never delegates a revision to AI."""

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_preparation_revision import preparation_text_fields
from .preparation_insert_page_dialog import PreparationInsertPageDialog
from .preparation_sequence_widget import PreparationSequenceWidget
from .preparation_structure_widget import PreparationStructureWidget


class PreparationRevisionDialog(QDialog):
    revision_saved = Signal(object)

    def __init__(self, facade, tasks, source, parent=None):
        super().__init__(parent)
        self.facade, self.tasks = facade, tasks
        self.source = deepcopy(source)
        self.fields = preparation_text_fields(self.source["candidate"])
        self.pending = {}
        self.editors = {}
        self.busy = False
        self.setWindowTitle("修订课件与教案 · 本地另存")
        self.resize(920, 800)
        self.setMinimumSize(420, 520)
        root = QVBoxLayout(self)
        intro = QLabel(
            "修改定义、完整知识表、教师讲解、教案和学习单，再另存一套文件。"
            "不调用模型，不覆盖原稿。若同一内容出现在多个位置，请分别核对并修改；不会自动替换相似句子。"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        root.addWidget(self.tabs, 1)
        edit_page = QWidget()
        layout = QVBoxLayout(edit_page)
        self.group = QComboBox()
        self.group.setAccessibleName("选择要修订的PPT页或教案内容")
        self.group.setMinimumWidth(0)
        self.group.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.group.setMinimumContentsLength(12)
        for name in dict.fromkeys(f["group"] for f in self.fields):
            self.group.addItem(name)
        layout.addWidget(self.group)
        self.links = QLabel()
        self.links.setWordWrap(True)
        self.links.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.links)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.edit_page = edit_page
        self.tabs.addTab(edit_page, "逐项修订")
        self.structure = PreparationStructureWidget(self.source["candidate"])
        self.structure.changed.connect(self._structure_changed)
        self.sequence = PreparationSequenceWidget(self.source["candidate"])
        self.sequence.operation_requested.connect(self._sequence_operation)
        self.sequence.undo_requested.connect(self.structure.undo)
        self.sequence.page_edit_requested.connect(self._edit_slide)
        self.sequence.page_insert_requested.connect(self._insert_page)
        self.tabs.insertTab(0, self.sequence, "授课顺序")
        self.tabs.addTab(self.structure, "图文课时与学习单")
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        self.review.setAccessibleName("保存前核对修改前后文字")
        self.tabs.addTab(self.review, "修改前后")
        self.source_notes = QPlainTextEdit()
        self.source_notes.setReadOnly(True)
        self.source_notes.setAccessibleName("原稿来源与待核验事项，只读")
        notes = [
            f"章节课题：{self.source['candidate']['topic']}",
            "逐项文字修订按原稿页定位；授课顺序显示当前页码。可移动相邻页组，或调整页面归属、图文拆页、时间与学习单。来源图片及原待核验事项保留。",
        ]
        for item in self.source["candidate"].get("uncertainties", []):
            notes.extend(
                ["", item["description"], "教师核对：" + item["teacher_action"]]
            )
        self.source_notes.setPlainText("\n".join(notes))
        self.tabs.addTab(self.source_notes, "原稿待核验")
        root.addWidget(QLabel("修订说明（可选，最多2000字）"))
        self.note = QPlainTextEdit()
        self.note.setMaximumHeight(66)
        self.note.setAccessibleName("本次课件修订说明")
        root.addWidget(self.note)
        self.status = QLabel("先选择页面或环节，再修改需要订正的文字。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.save_button = QPushButton("另存修订版并导出")
        self.save_button.setObjectName("PrimaryAction")
        self.save_button.setAccessibleName("本地另存课件教案修订版，不调用模型")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("QuietButton")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        root.addLayout(actions)
        self.group.currentTextChanged.connect(self._select_group)
        self.tabs.currentChanged.connect(self._refresh_review)
        self._select_group(self.group.currentText())
        self.tabs.setCurrentWidget(self.sequence)

    def _select_group(self, name):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.editors = {}
        fields = [f for f in self.fields if f["group"] == name]
        if fields:
            path = fields[0]["path"]
            row = (
                self.source["candidate"][path[0]][path[1]]
                if isinstance(path[1], int)
                else {}
            )
            refs = [
                f"{label}：{'、'.join(row[key])}"
                for key, label in (
                    ("activity_ids", "关联活动"),
                    ("objective_ids", "关联目标"),
                    ("assessment_ids", "关联评价"),
                )
                if row.get(key)
            ]
            self.links.setText(
                "按原稿页面定位编辑；当前页码请看「授课顺序」。\n"
                + ("；".join(refs) or "正文修订不会改变分钟数。")
            )
        for field in fields:
            key = tuple(field["path"])
            label = QLabel(f"{field['label']}（最多{field['limit']}字）")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
            editor = QPlainTextEdit()
            editor.setMinimumWidth(0)
            editor.setFixedHeight(90 if field["limit"] > 120 else 66)
            editor.setAccessibleName(field["label"])
            editor.setPlainText(self.pending.get(key, field["text"]))
            editor.textChanged.connect(lambda f=field, e=editor: self._changed(f, e))
            self.editors[key] = editor
            layout.addWidget(editor)
        layout.addStretch(1)
        self.scroll.setWidget(panel)

    def _changed(self, field, editor):
        key, text = tuple(field["path"]), editor.toPlainText()
        if text.strip() == field["text"]:
            self.pending.pop(key, None)
        else:
            self.pending[key] = text
        preview = deepcopy(self.source["candidate"])
        for path, value in self.pending.items():
            target = preview
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value.strip()
        self.structure.set_candidate(preview)
        self._structure_changed()

    def _has_changes(self):
        return bool(self.pending or self.structure.operations)

    def _structure_changed(self):
        self.sequence.set_candidate(
            self.structure.current_candidate,
            has_operations=bool(self.structure.operations),
            error=self.structure.preview_error,
        )
        self.save_button.setEnabled(self._has_changes() and not self.busy)
        self.status.setText(
            f"文字{len(self.pending)}处、结构{len(self.structure.operations)}项尚未保存。"
        )

    def _sequence_operation(self, operation):
        if not self.structure._queue(operation):
            self.sequence.status.setText(self.structure.status.text())

    def _edit_slide(self, identity):
        index = next(
            (
                i
                for i, slide in enumerate(self.source["candidate"]["slides"])
                if slide["id"] == identity
            ),
            None,
        )
        if index is None:
            return
        field = next(
            (field for field in self.fields if field["path"][:2] == ["slides", index]),
            None,
        )
        if field:
            self.group.setCurrentText(field["group"])
            self.tabs.setCurrentWidget(self.edit_page)

    def _insert_page(self, identity):
        candidate = self.structure.current_candidate
        if self.busy or candidate is None:
            return
        dialog = PreparationInsertPageDialog(candidate, identity, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._sequence_operation(dialog.operation)

    def _refresh_review(self, _index=0):
        lines = []
        for field in self.fields:
            key = tuple(field["path"])
            if key in self.pending:
                lines.extend(
                    [
                        field["group"] + " / " + field["label"],
                        "原文：" + field["text"],
                        "修订：" + self.pending[key],
                        "",
                    ]
                )
        if self.structure.operations:
            lines.append(self.structure.preview.toPlainText())
        self.review.setPlainText("\n".join(lines) or "还没有修改。")

    def _set_busy(self, busy):
        self.busy = busy
        self.tabs.setEnabled(not busy)
        self.note.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self._has_changes())

    def _save(self):
        if self.busy or not self._has_changes():
            return
        if len(self.note.toPlainText().strip()) > 2000:
            self.status.setText("修订说明超过2000字，请缩短后再保存。")
            return
        for field in self.fields:
            text = self.pending.get(tuple(field["path"]))
            if text is not None and (
                len(text.strip()) > field["limit"]
                or (not text.strip() and not field["allow_empty"])
            ):
                self.status.setText(
                    f"{field['label']}为空或超过{field['limit']}字，请先修改。"
                )
                return
        edits = [
            {"path": list(path), "text": text} for path, text in self.pending.items()
        ]
        task_id, revision = self.source["task_id"], self.source["source_revision"]
        note = self.note.toPlainText().strip()
        options = {"note": note}
        if self.structure.operations:
            options["structure_edits"] = deepcopy(self.structure.operations)
        self._set_busy(True)
        self.status.setText("正在本地另存并导出修订版，不调用模型…")
        try:
            self.tasks.submit(
                "本地导出备课修订版",
                lambda: self.facade.revise_preparation(
                    task_id, revision, edits, **options
                ),
                on_success=self._saved,
                on_failure=self._failed,
            )
        except Exception:  # noqa: BLE001 - never leak renderer paths or errors
            self._failed("本地任务暂时无法启动，编辑内容仍保留。")

    def _saved(self, summary):
        self._set_busy(False)
        self.revision_saved.emit(summary)
        status = (
            summary.get("status")
            if isinstance(summary, dict)
            else getattr(summary, "status", "")
        )
        if status == "completed":
            self.pending.clear()
            self.structure.clear()
            self.accept()
        else:
            self.status.setText(
                "修订版导出未完成。原稿未变；可关闭此窗口，从最近备课显式重试本地导出。"
            )

    def _failed(self, _message):
        self._set_busy(False)
        # Do not display arbitrary exception strings or filesystem paths.
        self.status.setText(
            "本地修订未完成，请核对文字长度和原稿状态。编辑内容仍保留，原稿未修改。"
        )

    def reject(self):
        if self.busy:
            return
        if (
            self._has_changes()
            and QMessageBox.question(
                self,
                "未保存的修订",
                "关闭后将放弃本次未保存的文字和结构修改，是否关闭？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
