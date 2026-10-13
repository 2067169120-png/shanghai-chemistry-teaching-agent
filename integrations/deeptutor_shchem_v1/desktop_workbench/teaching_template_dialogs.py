"""Native previews for teacher-owned structures; all writes are explicit."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget)

from ..desktop_teaching_templates import (TeachingTemplateService, capture_structure,
    can_replace_empty_structure, structure_text, validate_record)


def label(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class SaveTeachingTemplateDialog(QDialog):
    def __init__(self, plan, parent=None, *, suggested_name=""):
        super().__init__(parent)
        self.plan = deepcopy(plan)
        self.setWindowTitle("存为我的教学结构模板")
        self.resize(900, 680)
        root = QVBoxLayout(self)
        self.name = QLineEdit(suggested_name[:80]); self.name.setMaxLength(80)
        self.name.setAccessibleName("我的教学结构模板名称")
        self.name.setPlaceholderText("给结构起一个便于查找的名称")
        root.addWidget(self.name)
        checks = QHBoxLayout(); root.addLayout(checks)
        self.framework = QCheckBox("保存教案框架"); self.framework.setChecked(True)
        self.objectives = QCheckBox("保存目标列表与环节关联"); self.objectives.setChecked(True)
        checks.addWidget(self.framework); checks.addWidget(self.objectives)
        split = QSplitter(); root.addWidget(split, 1)
        self.nodes = QListWidget(); self.nodes.setAccessibleName("选择要保存的教学环节")
        for node in plan["nodes"]:
            item = QListWidgetItem(node["title"] or node["kind"])
            item.setData(Qt.ItemDataRole.UserRole, node["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked); self.nodes.addItem(item)
        split.addWidget(self.nodes)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setAccessibleName("将保存的教学结构完整预览")
        split.addWidget(self.preview); split.setSizes([240, 650])
        root.addWidget(label("保存所选教师组织文字和时长。原题、材料正文、答案、图片、来源引用与成品留在当前备课。模板应用后可继续修改教师文字。"))
        self.status = label(""); root.addWidget(self.status)
        row = QHBoxLayout(); root.addLayout(row)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        self.save = QPushButton("保存我的模板"); self.save.setObjectName("PrimaryAction")
        self.save.clicked.connect(self.accept); row.addWidget(self.save)
        self.nodes.itemChanged.connect(self.refresh)
        self.framework.toggled.connect(self.refresh); self.objectives.toggled.connect(self.refresh)
        self.name.textChanged.connect(self.refresh); self.refresh()

    def chosen_structure(self):
        ids = [self.nodes.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.nodes.count())
               if self.nodes.item(index).checkState() == Qt.CheckState.Checked]
        return capture_structure(self.plan, node_ids=ids, include_framework=self.framework.isChecked(),
                                 include_objectives=self.objectives.isChecked())

    def refresh(self, *_):
        try:
            structure = self.chosen_structure()
            self.preview.setPlainText(structure_text(structure))
            self.status.setText(f"已选择 {len(structure['nodes'])} 个环节、{len(structure['objectives'])} 个目标。")
            has_content = bool(structure["nodes"] or structure["objectives"] or
                (structure["framework"] and any(value for key, value in structure["framework"].items() if key != "schema_version")))
            self.save.setEnabled(bool(self.name.text().strip()) and has_content)
        except ValueError as exc:
            self.status.setText(str(exc)); self.save.setEnabled(False)


class ApplyTeachingTemplateDialog(QDialog):
    def __init__(self, record, plan, parent=None):
        super().__init__(parent)
        self.record = validate_record(record)
        if self.record["deleted_at"] is not None:
            raise ValueError("该模板已删除，请先恢复。")
        self.setWindowTitle("预览应用 · " + self.record["name"])
        self.resize(820, 680)
        root = QVBoxLayout(self)
        root.addWidget(label(self.record["name"]))
        self.mode = QComboBox(); self.mode.setAccessibleName("教学模板应用方式")
        self.mode.addItem("在已有环节之后新增模板环节", "append")
        self.mode.addItem("替换当前空白环节结构", "replace_empty")
        self.mode.model().item(1).setEnabled(can_replace_empty_structure(plan))
        root.addWidget(self.mode)
        self.fill_framework = QCheckBox("将模板教案文字补入当前空白栏目")
        self.fill_framework.setChecked(True); root.addWidget(self.fill_framework)
        preview = QPlainTextEdit(); preview.setReadOnly(True)
        preview.setAccessibleName("待应用教学结构完整预览")
        preview.setPlainText(structure_text(self.record["structure"])); root.addWidget(preview, 1)
        root.addWidget(label("当前课题、教材、原题、图片与课件页面保留。已有教师文字保留；模板新增的环节可继续修改，并可整体撤销。"))
        row = QHBoxLayout(); root.addLayout(row)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        apply = QPushButton("应用到当前教学设计"); apply.setObjectName("PrimaryAction")
        apply.clicked.connect(self.accept); row.addWidget(apply)


class TeachingTemplateLibraryDialog(QDialog):
    def __init__(self, facade, parent=None, *, apply_callback=None):
        super().__init__(parent)
        self.service = TeachingTemplateService(facade.state_store)
        self.apply_callback = apply_callback
        self.records = []; self.current_record = None
        self.setWindowTitle("我的教学结构模板")
        self.resize(940, 690)
        root = QVBoxLayout(self)
        row = QHBoxLayout(); root.addLayout(row)
        self.query = QLineEdit(); self.query.setMaxLength(200)
        self.query.setPlaceholderText("搜索名称、知识内容或教师组织文字")
        self.query.setAccessibleName("搜索我的教学结构模板"); self.query.setClearButtonEnabled(True)
        row.addWidget(self.query, 1)
        self.deleted = QCheckBox("查看已删除模板"); row.addWidget(self.deleted)
        split = QSplitter(); root.addWidget(split, 1)
        self.items = QListWidget(); self.items.setAccessibleName("我的模板搜索结果")
        split.addWidget(self.items)
        pane = QWidget(); pane_layout = QVBoxLayout(pane)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setAccessibleName("我的模板完整结构")
        pane_layout.addWidget(self.preview, 1)
        row = QHBoxLayout(); pane_layout.addLayout(row)
        self.name = QLineEdit(); self.name.setMaxLength(80); self.name.setAccessibleName("重命名我的模板")
        row.addWidget(self.name, 1)
        self.rename = QPushButton("重命名"); self.rename.clicked.connect(self.rename_current); row.addWidget(self.rename)
        self.undo_rename = QPushButton("撤销重命名"); self.undo_rename.clicked.connect(self.undo_current_name)
        pane_layout.addWidget(self.undo_rename)
        self.remove = QPushButton("移到已删除模板"); self.remove.clicked.connect(self.delete_or_restore)
        pane_layout.addWidget(self.remove)
        split.addWidget(pane); split.setSizes([270, 650])
        self.status = label("从教学设计的“存为我的教学结构模板…”保存常用结构；普通备课备份会包含这些模板。"); root.addWidget(self.status)
        row = QHBoxLayout(); root.addLayout(row)
        close = QPushButton("返回"); close.clicked.connect(self.reject); row.addWidget(close)
        self.apply = QPushButton("预览应用到当前备课…"); self.apply.setObjectName("PrimaryAction")
        self.apply.clicked.connect(self.apply_current); row.addWidget(self.apply)
        self.items.currentRowChanged.connect(self.select)
        self.query.textChanged.connect(self.refresh); self.deleted.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        identity = self.current_record["id"] if self.current_record else None
        try:
            self.records = self.service.search(self.query.text(), include_deleted=self.deleted.isChecked())
            if self.deleted.isChecked():
                self.records = [record for record in self.records if record["deleted_at"] is not None]
            self.items.blockSignals(True); self.items.clear()
            for record in self.records:
                self.items.addItem(record["name"] + (" · 已删除" if record["deleted_at"] else ""))
            self.items.blockSignals(False)
            index = next((i for i, record in enumerate(self.records) if record["id"] == identity), 0)
            self.items.setCurrentRow(index if self.records else -1)
            self.select(self.items.currentRow())
        except (ValueError, RuntimeError) as exc:
            self.records = []; self.items.clear(); self.select(-1); self.status.setText(str(exc))

    def select(self, index):
        self.current_record = deepcopy(self.records[index]) if 0 <= index < len(self.records) else None
        record = self.current_record
        self.name.setText(record["name"] if record else "")
        self.preview.setPlainText(structure_text(record["structure"]) if record else "还没有匹配的模板。")
        self.name.setEnabled(record is not None); self.rename.setEnabled(record is not None)
        self.undo_rename.setEnabled(bool(record and record["name_history"]))
        self.remove.setEnabled(record is not None)
        self.remove.setText("恢复这个模板" if record and record["deleted_at"] else "移到已删除模板")
        self.apply.setEnabled(bool(record and record["deleted_at"] is None and self.apply_callback))

    def rename_current(self):
        if self.current_record is None: return
        try:
            self.current_record = self.service.rename(self.current_record, self.name.text())
            self.refresh(); self.status.setText("模板名称已保存。")
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def delete_or_restore(self):
        if self.current_record is None: return
        try:
            deleted = self.current_record["deleted_at"] is None
            self.current_record = (self.service.delete(self.current_record) if deleted
                                   else self.service.restore(self.current_record))
            self.refresh(); self.status.setText("模板已移到已删除列表，可在那里恢复。" if deleted else "模板已恢复。")
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def undo_current_name(self):
        if self.current_record is None: return
        try:
            self.current_record = self.service.undo_rename(self.current_record)
            self.refresh(); self.status.setText("已恢复上一个模板名称。")
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))

    def apply_current(self):
        if self.current_record is None or self.apply_callback is None: return
        try:
            if self.apply_callback(deepcopy(self.current_record)):
                self.accept()
        except (ValueError, RuntimeError) as exc: self.status.setText(str(exc))
