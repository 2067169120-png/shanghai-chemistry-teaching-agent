"""Select and inspect existing AI/local-revised results before adding nodes."""
from __future__ import annotations
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout)
from ..desktop_lesson_import import prepare_import, apply_import


class LessonImportDialog(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor, self.facade, self.tasks = editor, editor.facade, editor.tasks
        self.proposal = self.selected_record = self.result_value = None
        self.busy = False
        self.setWindowTitle("从已有初稿加入教学环节")
        self.resize(1050, 750); self.setMinimumSize(760, 620)
        layout = QVBoxLayout(self)
        self.intro = QLabel("当前教学设计：" + editor.page.topic.text() + "\n选择已完成初稿，先预览再加入；不覆盖现有环节、不重新调用API。")
        self.intro.setWordWrap(True); layout.addWidget(self.intro)
        row = QHBoxLayout(); layout.addLayout(row)
        self.query = QLineEdit(); self.query.setPlaceholderText("按课题或作品名称查找")
        self.shelf = QComboBox(); self.shelf.addItem("当前作品", "current"); self.shelf.addItem("已归档", "archived")
        self.search = QPushButton("查找初稿"); self.search.clicked.connect(self.load_tasks)
        self.search.setObjectName("QuietButton")
        row.addWidget(self.query, 1); row.addWidget(self.shelf); row.addWidget(self.search)
        self.sources = QComboBox(); self.sources.setMinimumWidth(0)
        self.sources.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sources.setMinimumContentsLength(15); layout.addWidget(self.sources)
        self.read = QPushButton("读取所选初稿并预览"); self.read.clicked.connect(self.load_source)
        self.read.setObjectName("QuietButton")
        layout.addWidget(self.read)
        splitter = QSplitter(); layout.addWidget(splitter, 1)
        self.options = QListWidget(); self.options.setAccessibleName("要加入的教学环节")
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        self.detail.setAccessibleName("转换内容、来源和待核对事项")
        splitter.addWidget(self.options); splitter.addWidget(self.detail)
        splitter.setSizes([300, 700]); splitter.setChildrenCollapsible(False)
        self.status = QLabel("尚未读取初稿。"); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        row = QHBoxLayout(); layout.addLayout(row)
        self.apply_button = QPushButton("加入勾选环节")
        self.apply_button.setObjectName("PrimaryAction"); self.apply_button.clicked.connect(self.commit_import)
        self.cancel = QPushButton("返回，不加入"); self.cancel.clicked.connect(self.reject)
        self.cancel.setObjectName("QuietButton")
        row.addWidget(self.apply_button); row.addWidget(self.cancel)
        self.sources.currentIndexChanged.connect(self.clear_proposal)
        self.shelf.currentIndexChanged.connect(self.clear_sources)
        self.query.returnPressed.connect(self.load_tasks)
        self.options.currentRowChanged.connect(self.show_detail)
        self.set_busy(False)
        self.load_tasks()

    def clear_proposal(self, *_):
        self.proposal = None; self.options.clear(); self.detail.clear()
        self.apply_button.setEnabled(False)

    def clear_sources(self, *_):
        self.sources.clear(); self.clear_proposal(); self.read.setEnabled(False)

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.query, self.shelf, self.search, self.sources, self.read,
                       self.options, self.apply_button, self.cancel):
            widget.setEnabled(not busy)
        self.read.setEnabled(not busy and self.sources.currentData() is not None)
        self.apply_button.setEnabled(not busy and self.proposal is not None)

    def run(self, text, operation, success):
        self.set_busy(True); self.status.setText(text)
        def done(value):
            self.set_busy(False); success(value); self.set_busy(False)
        def failed(message):
            self.set_busy(False); self.status.setText(message + "；现有教学设计未改变。")
        def work():
            from ..desktop_preparation import DesktopPreparationError
            try:
                return operation()
            except ValueError as exc:
                raise DesktopPreparationError("lesson_import_invalid", str(exc)) from exc
        self.tasks.submit(text, work, on_success=done, on_failure=failed)

    def load_tasks(self):
        if self.busy:
            return
        query, shelf = self.query.text(), self.shelf.currentData()
        self.clear_sources()
        def work():
            offset, rows, warnings = 0, [], []
            while True:
                value = self.facade.search_preparation_work(query=query, kind="task", shelf=shelf,
                                                           offset=offset, limit=100)
                rows.extend(r for r in value["items"] if getattr(r["value"], "status", "") == "completed")
                warnings.extend(value["warnings"])
                if not value["has_more"]:
                    break
                offset = value["offset"] + value["limit"]
            return rows, list(dict.fromkeys(warnings))
        def done(value):
            rows, warnings = value
            for record in rows:
                self.sources.addItem(record["title"] + " · " + record["date"][:19].replace("T", " "), record)
            current = getattr(self.editor.page, "_preparation_task_id", None)
            for index in range(self.sources.count()):
                if self.sources.itemData(index)["id"] == current:
                    self.sources.setCurrentIndex(index); break
            self.status.setText((f"找到{len(rows)}份已完成初稿。请选择并读取。" if rows else
                                 "没有已完成初稿。可先用原备课生成流程生成，或继续手工编写环节。") +
                                ("\n" + "\n".join(warnings) if warnings else ""))
        self.run("正在读取已有作品…", work, done)

    def load_source(self):
        if self.busy or self.sources.currentData() is None:
            return
        record = deepcopy(self.sources.currentData())
        plan = deepcopy(self.editor.history.value)
        self.clear_proposal()
        def work():
            self.facade.resolve_preparation_work(record)
            return prepare_import(plan, self.facade.preparation_revision_source(record["id"]))
        def done(proposal):
            self.proposal, self.selected_record = proposal, record
            for option in proposal["options"]:
                item = QListWidgetItem(option["node"]["title"] + ("（已加入）" if option["already_present"] else ""))
                item.setData(Qt.ItemDataRole.UserRole, option["id"])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked if option["already_present"] else Qt.CheckState.Checked)
                if option["already_present"]:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.options.addItem(item)
            self.options.setCurrentRow(0)
            self.status.setText("\n".join(proposal["warnings"]))
        self.run("正在转换教学结构（本地，不调用模型）…", work, done)

    def show_detail(self, index):
        if not self.proposal or index < 0:
            return
        option = self.proposal["options"][index]; n = option["node"]
        texts = [n["title"], "状态：待核对 · 材料锁定且仅教师可见", ""]
        for key, caption in (("teacher_action", "教师活动"), ("student_task", "学生任务"),
                             ("expected_output", "预期产出"), ("criteria", "评价依据"),
                             ("material_text", "原材料与参考页"), ("notes", "教师备注与来源")):
            texts += [caption, n[key] or "尚未填写", ""]
        texts += option["warnings"]
        self.detail.setPlainText("\n".join(texts))

    def commit_import(self):
        if self.busy or self.proposal is None:
            return
        selected = [self.options.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.options.count())
                    if self.options.item(i).checkState() == Qt.CheckState.Checked]
        plan = deepcopy(self.editor.history.value)
        assets = self.editor.page.image_assets_widget.assets()
        proposal, record = deepcopy(self.proposal), deepcopy(self.selected_record)
        def work():
            self.facade.resolve_preparation_work(record)
            fresh = self.facade.preparation_revision_source(proposal["task_id"])
            if fresh["source_revision"] != proposal["source_revision"]:
                raise ValueError("原稿在预览后已变化，请重新读取。")
            return apply_import(plan, proposal, selected, assets)
        def done(value):
            self.result_value = value
            self.accept()
        self.run("正在核对原稿并加入所选环节…", work, done)

    def reject(self):
        if not self.busy:
            super().reject()
