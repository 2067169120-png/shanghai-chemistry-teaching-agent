"""Native teacher-owned lesson nodes; reuse the preparation form and recovery."""
from __future__ import annotations
from copy import deepcopy
import json
from uuid import uuid4
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)
from ..desktop_lesson_design import (KINDS, DesignHistory, bind_material,
    content_fingerprint, coverage, new_design, new_node, update_goal, update_node)
from ..desktop_lesson_output import (FILES, actual_ppt_preview, actual_word_preview,
    checked_file, export_design, copy_output_bundle, copy_primary_bundle, primary_output_names)
from .components import page_scroll
from .actual_ppt_preview_dialog import ActualPptPreview
from .lesson_node_preview import LessonNodePreview
from .lesson_composition_panel import LessonCompositionPanel
from .lesson_editor_session import remember_editor, restore_editor
from ..desktop_teaching_framework import FIELDS as FRAMEWORK_FIELDS, SCHEMA as FRAMEWORK_SCHEMA, framework_from_design


def label(text):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


class LessonDesignDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.facade, self.tasks = page, page.facade, page.tasks
        self.history = DesignHistory(page._lesson_design or new_design(page._payload()))
        self.loading, self.busy = True, False
        self._task = None
        self._close_when_finished = False
        self._stopping = False
        self._cancellable = False
        if hasattr(self.tasks, "task_finished"):
            self.tasks.task_finished.connect(self.task_finished)
        if hasattr(self.tasks, "task_cancelled_detail"):
            self.tasks.task_cancelled_detail.connect(self.task_cancelled)
        self.current = None
        self._preview_wide = None
        self._layout_narrow = None
        self.setWindowTitle("教学设计 · " + (page.topic.text() or "未命名课题"))
        self.resize(1200, 790)
        self.setMinimumSize(320, 400)
        root = QVBoxLayout(self)
        heading = label("教学设计  /  目标、活动与评价")
        heading.setObjectName("CardTitle")
        root.addWidget(heading)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        main = self.main_splitter = QSplitter()
        self.tabs.addTab(main, "教学环节")
        left = QWidget(); l = QVBoxLayout(left)
        self.nodes = QListWidget()
        self.nodes.setAccessibleName("本课教学环节顺序")
        l.addWidget(self.nodes, 1)
        self.import_candidate = QPushButton("从已有初稿加入…")
        self.import_candidate.setObjectName("QuietButton")
        self.import_candidate.setToolTip("读取已完成的备课初稿，预览后加入，不重复调用API。")
        self.import_candidate.clicked.connect(self.import_existing_candidate)
        l.addWidget(self.import_candidate)
        self.save_structure_template = QPushButton("存为我的教学结构模板…")
        self.save_structure_template.setObjectName("QuietButton")
        self.save_structure_template.clicked.connect(self.save_personal_teaching_template)
        l.addWidget(self.save_structure_template)
        self.apply_structure_template = QPushButton("从我的模板预览应用…")
        self.apply_structure_template.setObjectName("QuietButton")
        self.apply_structure_template.clicked.connect(self.open_personal_teaching_templates)
        l.addWidget(self.apply_structure_template)
        self.to_canvas = QPushButton('把所选环节加入课件画布')
        self.to_canvas.clicked.connect(self.add_current_activity); l.addWidget(self.to_canvas)
        actions = QGridLayout(); l.addLayout(actions)
        for i, (name, action) in enumerate((("新增", self.add_node), ("删除", self.delete_node),
            ("上移", lambda: self.move_node(-1)), ("下移", lambda: self.move_node(1)),
            ("撤销", lambda: self.travel(False)), ("重做", lambda: self.travel(True)))):
            b = QPushButton(name); b.setObjectName("QuietButton")
            b.clicked.connect(action); actions.addWidget(b, i//2, i%2)
        self.node_scroll = page_scroll(left)
        main.addWidget(self.node_scroll)
        self.properties = QTabWidget()
        main.addWidget(self.properties)
        main.setSizes([250, 900])
        main.setChildrenCollapsible(False)
        self.fields = {}
        self.title_edit = QLineEdit()
        self.kind = QComboBox(); self.kind.addItems(KINDS)
        self.minutes = QSpinBox(); self.minutes.setRange(-1, 180)
        self.minutes.setSpecialValueText("未估时"); self.minutes.setSuffix(" 分钟")
        w = QWidget(); f = QFormLayout(w)
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        f.addRow("环节名称", self.title_edit); f.addRow("课堂作用", self.kind); f.addRow("预计用时", self.minutes)
        for key, text in (("teacher_action", "教师活动"), ("student_task", "学生任务")):
            e = QPlainTextEdit(); e.setMinimumHeight(100); e.setAccessibleName(text)
            self.fields[key] = e; f.addRow(text, e)
        self.properties.addTab(page_scroll(w), "任务与活动")
        w = QWidget(); f = QVBoxLayout(w)
        f.addWidget(label("勾选本环节对应的目标；填写可观察的产出与判断依据。"))
        self.goal_links = QListWidget(); self.goal_links.setMaximumHeight(130)
        f.addWidget(self.goal_links)
        for key, text in (("expected_output", "预期产出"), ("criteria", "评价依据")):
            f.addWidget(label(text))
            e = QPlainTextEdit(); e.setMinimumHeight(75); e.setMaximumHeight(120)
            e.setAccessibleName(text); self.fields[key] = e; f.addWidget(e)
        self.confirmed = QCheckBox("我已核对：任务与评价能检查所选目标")
        f.addWidget(self.confirmed); f.addStretch(1)
        self.properties.addTab(page_scroll(w), "目标与评价")
        w = QWidget(); f = QVBoxLayout(w)
        self.bind = QPushButton("引用当前备课资料（完整内容）")
        self.bind.setObjectName("QuietButton"); self.bind.clicked.connect(self.bind_source); f.addWidget(self.bind)
        self.locked = QCheckBox("锁定材料、教师答案、图片与学生可见范围")
        f.addWidget(self.locked)
        self.student_material = QCheckBox("材料和勾选图片用于学生页（已核对不含答案）")
        f.addWidget(self.student_material)
        for key, text in (("material_text", "材料 / 完整题目与公共条件"),
                          ("teacher_answer", "教师答案（不写入学习单和投影正文）")):
            f.addWidget(label(text)); e = QPlainTextEdit()
            e.setMinimumHeight(75); e.setMaximumHeight(150); e.setAccessibleName(text)
            self.fields[key] = e; f.addWidget(e)
        self.images = QListWidget(); self.images.setMaximumHeight(100); f.addWidget(self.images)
        f.addWidget(label("教师备注"))
        e = QPlainTextEdit(); e.setMinimumHeight(65); e.setMaximumHeight(110)
        self.fields["notes"] = e; f.addWidget(e)
        self.properties.addTab(page_scroll(w), "材料与备注")
        goals_page = QWidget(); g = QVBoxLayout(goals_page)
        g.addWidget(label("每行一个可观察目标。修改目标后，相关环节须重新核对；不会自动认定学生已经掌握。"))
        self.goals = QListWidget(); g.addWidget(self.goals, 1)
        self.goal_text = QPlainTextEdit(); self.goal_text.setMaximumHeight(110); g.addWidget(self.goal_text)
        ar = QHBoxLayout(); g.addLayout(ar)
        for name, action in (("新增目标", self.add_goal), ("修改所选目标", self.edit_goal), ("删除目标", self.delete_goal)):
            b = QPushButton(name); b.setObjectName("QuietButton"); b.clicked.connect(action); ar.addWidget(b)
        self.source = QPlainTextEdit(page.materials.toPlainText())
        self.source.setReadOnly(True); self.source.setMinimumHeight(90)
        g.addWidget(label("当前备课资料 · 原文与考试讲评摘要（引用后保留快照）"))
        g.addWidget(self.source, 1)
        self.tabs.addTab(page_scroll(goals_page), "目标与资料")
        out = QWidget(); o = QVBoxLayout(out)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True); o.addWidget(self.report, 1)
        self.outputs = QComboBox(); o.addWidget(self.outputs)
        self.classroom_notes=QCheckBox('Word 学生文档按课件顺序生成课堂讲义（含分步讲解）')
        self.classroom_notes.setChecked(self.history.value.get('classroom_notes',False))
        self.classroom_notes.toggled.connect(self.set_classroom_notes);o.addWidget(self.classroom_notes)
        o.addWidget(label('教案 Word用于备课与编辑，课堂演示 PPT用于上课展示。'))
        primary = QGridLayout(); o.addLayout(primary)
        self.open_word_button = QPushButton('打开教案 Word')
        self.open_word_button.clicked.connect(lambda: self.open_file('lesson_plan.docx'))
        self.preview_word_button = QPushButton('预览教案 Word')
        self.preview_word_button.clicked.connect(self.preview_word)
        self.open_classroom_button = QPushButton('打开课堂演示 PPT')
        self.open_classroom_button.clicked.connect(self.open_classroom_ppt)
        self.export_primary = QPushButton('导出教案 Word 和课堂演示 PPT…')
        self.export_primary.setObjectName('PrimaryAction')
        self.export_primary.clicked.connect(self.copy_primary_files)
        for index, button in enumerate((self.open_word_button, self.preview_word_button, self.open_classroom_button)):
            primary.addWidget(button, index // 2, index % 2)
        primary.addWidget(self.export_primary, 2, 0, 1, 2)
        o.addWidget(label('其他成品'))
        ar = QHBoxLayout(); o.addLayout(ar)
        for name, filename in (("打开教师讲解 PPT", FILES[0]), ("打开学习单", FILES[2])):
            b = QPushButton(name); b.setObjectName("QuietButton")
            b.clicked.connect(lambda _=False, file=filename: self.open_file(file)); ar.addWidget(b)
        self.export_copy = QPushButton("导出所选版本的全部文件…")
        self.export_copy.setObjectName("QuietButton")
        self.export_copy.clicked.connect(self.copy_files)
        o.addWidget(self.export_copy)
        self.selected_output_status = label("")
        o.addWidget(self.selected_output_status)
        self.preview_audience = QComboBox()
        self._preview_audience_explicit = False
        self.preview_audience.activated.connect(self._choose_preview_audience)
        self.preview_audience.setAccessibleName("预览和朗读的课件版本")
        o.addWidget(label("预览／朗读课件"))
        o.addWidget(self.preview_audience)
        self.outputs.currentIndexChanged.connect(self.show_selected_output)
        self.tabs.addTab(page_scroll(out), "输出与检查")
        self.content_preview = LessonNodePreview(self.facade)
        self.preview_page = QWidget()
        preview_layout = QVBoxLayout(self.preview_page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.content_preview)
        self.tabs.addTab(self.preview_page, "内容预览")
        self.composition_panel = LessonCompositionPanel(self)
        self.tabs.addTab(self.composition_panel, "素材与页面编排")
        pdf_actions = QHBoxLayout()
        for name, filename in (("打开教师PDF", "lesson_canvas.pdf"), ("打开学生PDF", "student_canvas.pdf"),
                               ("打开学生PPTX", "student_presentation.pptx")):
            b = QPushButton(name); b.setObjectName("QuietButton")
            b.clicked.connect(lambda _=False, file=filename: self.open_file(file)); pdf_actions.addWidget(b)
        o.addLayout(pdf_actions)
        framework_page = QWidget(); framework_form = QFormLayout(framework_page)
        framework_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        framework_form.addRow(label('填写教学目标、过程和板书。保存或生成会自动应用编辑；先同步到课件后，栏目内容可与教案一起更新。'))
        self.framework_fields = {}
        for key, text in FRAMEWORK_FIELDS:
            editor = QPlainTextEdit(); editor.setMinimumHeight(70); editor.setMaximumHeight(150)
            editor.setAccessibleName('教案框架 · ' + text)
            self.framework_fields[key] = editor; framework_form.addRow(text, editor)
        self.apply_framework_button = QPushButton('应用教案框架')
        self.apply_framework_button.clicked.connect(self.apply_framework)
        framework_form.addRow(self.apply_framework_button)
        self.framework_slides_button = QPushButton('预览并同步教案栏目到课件…')
        self.framework_slides_button.clicked.connect(self.framework_to_slides)
        framework_form.addRow(self.framework_slides_button)
        self.framework_scroll = page_scroll(framework_page)
        self.tabs.addTab(self.framework_scroll, '教案框架')
        self.status = label("本地编辑，不调用模型。保存草稿沿用原备课；未保存编辑进入原恢复副本。")
        root.addWidget(self.status)
        self.stop_button = QPushButton("停止本次任务")
        self.stop_button.clicked.connect(self.cancel)
        self.stop_button.hide()
        root.addWidget(self.stop_button)
        bottom = self.bottom_actions = QGridLayout(); root.addLayout(bottom)
        self.save = QPushButton("保存草稿"); self.save.clicked.connect(self.save_draft)
        self.generate = QPushButton("生成教案 Word / 课堂 PPT"); self.generate.setObjectName("PrimaryAction")
        self.generate.clicked.connect(self.generate_outputs)
        self.preview = QPushButton("预览课堂 PPT"); self.preview.clicked.connect(self.preview_ppt)
        self.return_button = QPushButton("返回"); self.return_button.clicked.connect(self.reject)
        self._bottom_buttons = (self.save, self.generate, self.preview, self.return_button)
        for i, b in enumerate(self._bottom_buttons):
            bottom.addWidget(b, 0, i)
        self.nodes.currentRowChanged.connect(self.select_node)
        self.goals.currentRowChanged.connect(self.select_goal)
        self.title_edit.textEdited.connect(lambda v: self.change({"title": v}))
        self.kind.currentTextChanged.connect(lambda v: self.change({"kind": v}))
        self.minutes.valueChanged.connect(lambda v: self.change({"minutes": None if v < 0 else v}))
        for key, widget in self.fields.items():
            widget.textChanged.connect(lambda k=key, e=widget: self.change({k: e.toPlainText()}))
        self.goal_links.itemChanged.connect(lambda _: self.change({"objective_ids": self.checked(self.goal_links)}))
        self.images.itemChanged.connect(lambda _: self.change({"image_ids": self.checked(self.images)}))
        self.confirmed.toggled.connect(lambda v: self.change({"confirmed": v}))
        self.locked.toggled.connect(lambda v: self.change({"locked": v}))
        self.student_material.toggled.connect(lambda v: self.change({"student_material": v}))
        self.loading = False
        self.refresh_lists()
        self.sync_page()
        # Restore before any external material handoff: its new selection wins.
        restore_editor(self)
        self.finished.connect(lambda _result: remember_editor(self))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_bottom_buttons"):
            return
        narrow = event.size().width() < 700
        self.nodes.setMaximumHeight(96 if narrow else 16777215)
        self.main_splitter.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        wide = event.size().width() >= 1100
        changed = self._preview_wide != wide or self._layout_narrow != narrow
        if self._preview_wide != wide:
            if wide:
                index = self.tabs.indexOf(self.preview_page)
                preview_selected = self.tabs.currentWidget() is self.preview_page
                if index >= 0:
                    self.tabs.removeTab(index)
                self.main_splitter.addWidget(self.content_preview)
                if preview_selected:
                    self.tabs.setCurrentWidget(self.main_splitter)
            else:
                self.preview_page.layout().addWidget(self.content_preview)
                if self.tabs.indexOf(self.preview_page) < 0:
                    self.tabs.addTab(self.preview_page, "内容预览")
            self._preview_wide = wide
        if changed and narrow:
            self.main_splitter.setSizes([120, 200])
        elif changed:
            self.main_splitter.setSizes([200, 510, 450] if wide else [220, 900])
        self._layout_narrow = narrow
        for button in self._bottom_buttons:
            self.bottom_actions.removeWidget(button)
        for i, button in enumerate(self._bottom_buttons):
            self.bottom_actions.addWidget(button, i // 2 if narrow else 0, i % 2 if narrow else i)

    @staticmethod
    def checked(widget):
        return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())
                if widget.item(i).checkState() == Qt.CheckState.Checked]

    def checked_items(self, widget, rows, selected):
        widget.clear()
        for identity, text in rows:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if identity in selected else Qt.CheckState.Unchecked)
            widget.addItem(item)

    def sync_page(self):
        self.classroom_notes.blockSignals(True)
        self.classroom_notes.setChecked(self.history.value.get('classroom_notes',False))
        self.classroom_notes.blockSignals(False)
        self.export_primary.setText('导出教案、课堂讲义 Word 和课堂演示 PPT…' if self.classroom_notes.isChecked()
            else '导出教案 Word 和课堂演示 PPT…')
        self.page._lesson_design = deepcopy(self.history.value)
        self.page.setWindowModified(self.page._payload() != self.page._form_baseline)
        self.refresh_report()
        self.content_preview.set_payload(self.page._payload(), self.current)
        self.composition_panel.set_payload(self.page._payload())

    def set_classroom_notes(self,value):
        if self.loading or self.busy:return
        design=deepcopy(self.history.value);design['classroom_notes']=value
        self.history.put(design);self.sync_page()

    def refresh_lists(self, selected=None):
        self.loading = True
        self.nodes.clear(); self.goals.clear()
        for n in self.history.value["nodes"]:
            self.nodes.addItem(n["title"] or "未命名环节")
        for g in self.history.value["objectives"]:
            self.goals.addItem(g["text"] or "待填写目标")
        self.loading = False
        index = next((i for i, n in enumerate(self.history.value["nodes"]) if n["id"] == selected), 0)
        self.nodes.setCurrentRow(index if self.nodes.count() else -1)
        framework = framework_from_design({**self.page._payload(), 'lesson_design': self.history.value})
        for key, editor in self.framework_fields.items(): editor.setPlainText(framework[key])

    def _framework_editor_plan(self):
        value = deepcopy(self.history.value)
        value['teaching_framework'] = {'schema_version': FRAMEWORK_SCHEMA,
            **{key: editor.toPlainText() for key, editor in self.framework_fields.items()}}
        from ..desktop_lesson_design import validate_design
        return validate_design(value)

    def _teaching_template_fingerprint(self):
        from ..desktop_lesson_design import digest
        return digest({"payload": self.page._payload(), "history": self.history.value,
                       "framework_editor": self._framework_editor_plan()["teaching_framework"]})

    def save_personal_teaching_template(self):
        if self.busy: return False
        from ..desktop_teaching_templates import TeachingTemplateService
        from .teaching_template_dialogs import SaveTeachingTemplateDialog
        try:
            expected = self._teaching_template_fingerprint()
            dialog = SaveTeachingTemplateDialog(self._framework_editor_plan(), self,
                suggested_name=(self.page.topic.text() + " · 教学结构").strip())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if self._teaching_template_fingerprint() != expected:
                raise ValueError("当前教学设计已变化，请重新预览要保存的模板。")
            record = TeachingTemplateService(self.facade.state_store).save(dialog.name.text(), dialog.chosen_structure())
            self.status.setText("已保存我的教学结构模板：" + record["name"])
            return True
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc)); return False

    def open_personal_teaching_templates(self):
        if self.busy: return
        from .teaching_template_dialogs import TeachingTemplateLibraryDialog
        dialog = TeachingTemplateLibraryDialog(self.facade, self, apply_callback=self.apply_personal_teaching_template)
        dialog.exec(); dialog.deleteLater()

    def apply_personal_teaching_template(self, record):
        if self.busy: return False
        from ..desktop_lesson_design import digest
        from ..desktop_teaching_templates import TeachingTemplateService, apply_structure, validate_record
        from .teaching_template_dialogs import ApplyTeachingTemplateDialog
        try:
            record = validate_record(record)
            service = TeachingTemplateService(self.facade.state_store)
            if service.load(record["id"]) != record:
                raise ValueError("模板已变化，请重新读取后预览。")
            expected = self._teaching_template_fingerprint()
            plan = self._framework_editor_plan()
            dialog = ApplyTeachingTemplateDialog(record, plan, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if self._teaching_template_fingerprint() != expected:
                raise ValueError("当前教学设计已变化，请重新预览模板。")
            if service.load(record["id"]) != record:
                raise ValueError("模板已变化，请重新读取后预览。")
            value = apply_structure(plan, record["structure"], expected_fingerprint=digest(plan),
                mode=dialog.mode.currentData(), fill_framework=dialog.fill_framework.isChecked())
            self.history.put(value); self.sync_page(); self.refresh_lists()
            self.tabs.setCurrentWidget(self.main_splitter)
            self.status.setText("已应用教学结构模板，可继续编辑；撤销可恢复应用前的设计。")
            return True
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc)); return False

    def apply_framework(self, quiet=False):
        if self.busy: return False
        try:
            from ..desktop_framework_composition import refresh_linked_framework
            value = self._framework_editor_plan()
            if quiet and 'teaching_framework' not in self.history.value and value['teaching_framework'] == framework_from_design(
                    {**self.page._payload(), 'lesson_design': self.history.value}):
                return True
            value = refresh_linked_framework(value)
            if value != self.history.value:
                self.history.put(value)
            self.sync_page()
            if not quiet:
                self.status.setText('教案框架已应用，已联动的课件栏目同步更新；单独编辑的课件文字已保留。')
        except ValueError as exc:
            self.status.setText(str(exc))
            return False
        self.select_node(self.nodes.currentRow())
        self.refresh_report()
        return True

    def framework_to_slides(self):
        if self.busy: return
        from ..desktop_lesson_composition import digest
        from ..desktop_framework_composition import synchronize_framework
        from .framework_slide_dialog import FrameworkSlideDialog
        try:
            expected = digest(self.history.value)
            plan = self._framework_editor_plan()
            dialog = FrameworkSlideDialog(plan, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if digest(self.history.value) != expected:
                raise ValueError('作品已变化，请重新预览教案栏目。')
            result = synchronize_framework(plan, dialog.chosen_sections(), replace_edited=not dialog.keep_edits.isChecked())
            self.history.put(result['plan']); self.sync_page(); self.refresh_lists()
            self.tabs.setCurrentWidget(self.composition_panel)
            kept = sum(row['action'] == 'kept_copy' for row in result['changes'])
            self.status.setText('教案栏目已同步到课件，可在页面编排中预览、调整并生成。'
                                + (f' 保留了{kept}项已编辑的课件副本。' if kept else ''))
        except ValueError as exc:
            self.status.setText(str(exc))

    def select_node(self, index):
        if self.loading:
            return
        self.loading = True
        self.properties.setEnabled(index >= 0)
        self.to_canvas.setEnabled(index >= 0 and not self.busy)
        self.current = self.history.value["nodes"][index]["id"] if index >= 0 else None
        n = self.history.value["nodes"][index] if index >= 0 else new_node()
        self.title_edit.setText(n["title"])
        self.kind.setCurrentText(n["kind"]); self.minutes.setValue(-1 if n["minutes"] is None else n["minutes"])
        for k, e in self.fields.items():
            e.setPlainText(n[k])
        self.checked_items(self.goal_links, [(g["id"], g["text"]) for g in self.history.value["objectives"]], n["objective_ids"])
        self.checked_items(self.images, [(a["asset_id"], a["caption"]) for a in self.page.image_assets_widget.assets()], n["image_ids"])
        self.locked.setChecked(n["locked"]); self.confirmed.setChecked(n["confirmed"])
        self.student_material.setChecked(n["student_material"])
        self.lock_controls(n["locked"]); self.loading = False
        self.content_preview.set_payload(self.page._payload(), self.current)

    def lock_controls(self, locked):
        for key in ("material_text", "teacher_answer"):
            self.fields[key].setReadOnly(locked)
        self.images.setEnabled(not locked); self.student_material.setEnabled(not locked)
        self.bind.setEnabled(not locked)

    def change(self, fields):
        if self.loading or self.busy or self.current is None:
            return
        try:
            value = update_node(self.history.value, self.current, fields)
            self.history.put(value)
            n = next(n for n in value["nodes"] if n["id"] == self.current)
            self.nodes.currentItem().setText(n["title"] or "未命名环节")
            self.loading = True
            self.confirmed.setChecked(n["confirmed"]); self.lock_controls(n["locked"])
            self.loading = False
            self.sync_page()
        except ValueError as exc:
            self.status.setText(str(exc))

    def import_existing_candidate(self):
        if self.busy:
            return
        from .lesson_import_dialog import LessonImportDialog
        dialog = LessonImportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_value is not None:
            plan, assets = dialog.result_value
            selected = next((n["id"] for n in plan["nodes"]
                             if n["id"] not in {old["id"] for old in self.history.value["nodes"]}), self.current)
            self.page.image_assets_widget.set_assets(assets)
            self.history.put(plan)
            self.refresh_lists(selected); self.sync_page()
            self.status.setText("初稿所选环节已加入。原设计和原稿保留，可撤销；请核对目标、材料可见范围，再生成并保存。")
        dialog.deleteLater()

    def add_node(self):
        n = new_node()
        value = deepcopy(self.history.value); value["nodes"].append(n)
        self.history.put(value); self.refresh_lists(n["id"]); self.sync_page()

    def add_current_activity(self):
        if self.busy or self.current is None:
            return
        panel = self.composition_panel
        existing = next((r for r in panel.composition()['resources']
            if r['original']['kind'] == 'lesson_activity' and r['activity_link']['mode'] == 'linked'
            and r['activity_link']['design_id'] == self.history.value['id']
            and r['activity_link']['node_id'] == self.current), None)
        try:
            if existing:
                panel.selected = existing['id']; panel.refresh(); panel.fit_view()
            else:
                panel.import_activity_nodes([self.current])
            self.tabs.setCurrentWidget(panel)
        except ValueError as exc:
            self.status.setText(str(exc))

    def delete_node(self):
        if not self.current:
            return
        value = deepcopy(self.history.value)
        value["nodes"] = [n for n in value["nodes"] if n["id"] != self.current]
        self.history.put(value); self.refresh_lists(); self.sync_page()

    def move_node(self, delta):
        i = self.nodes.currentRow()
        value = deepcopy(self.history.value)
        if 0 <= i+delta < len(value["nodes"]):
            value["nodes"][i], value["nodes"][i+delta] = value["nodes"][i+delta], value["nodes"][i]
            self.history.put(value); self.refresh_lists(self.current); self.sync_page()

    def travel(self, redo):
        selected = self.current
        self.history.travel(redo); self.refresh_lists(selected); self.sync_page()
        self.composition_panel.fit_view()

    def select_goal(self, index):
        if not self.loading:
            self.goal_text.setPlainText(self.history.value["objectives"][index]["text"] if index >= 0 else "")

    def add_goal(self):
        value = deepcopy(self.history.value)
        value["objectives"].append({"id": "O-" + uuid4().hex, "text": self.goal_text.toPlainText()})
        self.history.put(value); self.refresh_lists(self.current); self.sync_page()

    def edit_goal(self):
        i = self.goals.currentRow()
        if i >= 0:
            goal = self.history.value["objectives"][i]
            self.history.put(update_goal(self.history.value, goal["id"], self.goal_text.toPlainText()))
            self.refresh_lists(self.current); self.sync_page()

    def delete_goal(self):
        i = self.goals.currentRow()
        if i >= 0:
            value = deepcopy(self.history.value)
            goal = value["objectives"].pop(i)
            for n in value["nodes"]:
                if goal["id"] in n["objective_ids"]:
                    n["objective_ids"].remove(goal["id"]); n["confirmed"] = False
            self.history.put(value); self.refresh_lists(self.current); self.sync_page()

    def bind_source(self):
        if self.current:
            self.history.put(bind_material(self.history.value, self.current, self.page.materials.toPlainText(),
                                           [a["asset_id"] for a in self.page.image_assets_widget.assets()]))
            self.refresh_lists(self.current); self.sync_page()
            self.status.setText("已引用完整备课资料并锁定。默认仅教师可见；解锁后核对答案隔离，再决定是否用于学生页。")

    def refresh_report(self):
        report = coverage(self.history.value, self.page.lesson_count.value()*self.page.lesson_minutes.value())
        lines = [g["status"] + " · " + g["text"] for g in report["objectives"]]
        lines += ["", f"已估时 {report['known_minutes']} 分钟；{report['unestimated']} 个环节未估时。",
                  "超过计划课时，请调整。" if report["over_budget"] else "",
                  "覆盖仅指教师明确的安排关系，不是学生掌握度；未完成设计仍可保存。"]
        if self.history.value["exports"]:
            latest = self.history.value["exports"][-1]
            stale = latest["fingerprint"] != content_fingerprint(self.page._payload(), self.history.value)
            lines.append("最近成品：" + ("内容已变更，三类输出需更新；旧文件保留。" if stale else "与当前设计版本一致，仍须查看实际版式。"))
        self.report.setPlainText("\n".join(lines))
        chosen = self.outputs.currentData()
        self.outputs.clear()
        for out in reversed(self.history.value["exports"]):
            count = len(out["files"])
            self.outputs.addItem(out["created_at"][:19].replace("T", " ") + f" · {count} 份成品", out["id"])
        if chosen:
            self.outputs.setCurrentIndex(max(0, self.outputs.findData(chosen)))
        self.preview.setEnabled(not self.busy and self.outputs.count() > 0)
        self.show_selected_output()

    def show_selected_output(self, *_):
        record = next((r for r in self.history.value["exports"] if r["id"] == self.outputs.currentData()), None)
        name = self.preview_audience.currentData() if self._preview_audience_explicit else "student_presentation.pptx"
        self.preview_audience.blockSignals(True);self.preview_audience.clear()
        files = {f["name"] for f in record["files"]} if record else set()
        for title, value in (("课堂演示 PPT", "student_presentation.pptx"),
                             ("教师讲解 PPT · 答案与讲评", "lesson_presentation.pptx")):
            if value in files:self.preview_audience.addItem(title, value)
        index = self.preview_audience.findData(name)
        if index >= 0:self.preview_audience.setCurrentIndex(index)
        self.preview_audience.blockSignals(False)
        self.preview_audience.setEnabled(not self.busy and record is not None)
        self.export_copy.setEnabled(not self.busy and record is not None)
        for button in (self.open_word_button, self.preview_word_button, self.open_classroom_button, self.export_primary):
            button.setEnabled(not self.busy and record is not None)
        if record is None:
            self.selected_output_status.setText("尚无成品。先生成，再查看或导出。")
        elif record["fingerprint"] != content_fingerprint(self.page._payload(), self.history.value):
            self.selected_output_status.setText("所选为历史内容：与当前设计不同。可保留或导出旧版；需要最新版请重新生成。")
        else:
            self.selected_output_status.setText("所选成品与当前设计一致。导出会新建独立副本，不改工作台中的原版本。")

    def _choose_preview_audience(self, *_):
        self._preview_audience_explicit = True

    def open_classroom_ppt(self):
        record = next((r for r in self.history.value['exports'] if r['id'] == self.outputs.currentData()), None)
        if record:
            self.open_file(primary_output_names(record)[1])

    def copy_primary_files(self):
        identity = self.outputs.currentData()
        if self.busy or not identity:
            return
        destination = QFileDialog.getExistingDirectory(self, '选择教案 Word 和课堂演示 PPT的导出位置')
        if not destination:
            return
        plan = deepcopy(self.history.value)
        self.run('正在导出教案 Word 和课堂演示 PPT…',
                 lambda: copy_primary_bundle(self.facade, plan, identity, destination),
                 lambda folder: self.status.setText('已导出到：' + folder + '；教案.docx和课堂演示.pptx可直接编辑。'),
                 cancellable=True)

    def copy_files(self):
        identity = self.outputs.currentData()
        if self.busy or not identity:
            return
        destination = QFileDialog.getExistingDirectory(self, "选择教学成品的导出位置")
        if not destination:
            return
        plan = deepcopy(self.history.value)
        self.run("正在导出所选版本…", lambda: copy_output_bundle(self.facade, plan, identity, destination),
                 lambda folder: self.status.setText("已导出到：" + folder + "；所选版本的全部成品已保存。"),
                 cancellable=True)

    def set_busy(self, value):
        self.busy = value
        self.stop_button.setVisible(value and self._cancellable)
        self.stop_button.setEnabled(value and self._cancellable and not self._stopping)
        for w in (self.tabs, self.save, self.generate, self.preview):
            w.setEnabled(not value)
        self.return_button.setText(("停止并返回" if self._cancellable else "完成后返回") if value else "返回")
        self.preview.setEnabled(not value and self.outputs.count() > 0)
        self.show_selected_output()

    def run(self, text, operation, success, *, cancellable=False):
        if self.busy:
            return
        self._cancellable = cancellable
        self.set_busy(True); self.status.setText(text)
        def finish(value):
            if not self._stopping:
                self.set_busy(False)
                try: success(value)
                except (ValueError, OSError) as exc:
                    self.status.setText(str(exc) + "；当前输入与已有成品保留。")
        def fail(message):
            self.set_busy(False); self.status.setText(message + "；当前输入与已有成品保留。")
        # Translate known local errors without exposing generic task-bridge errors.
        def work():
            try:
                if cancellable:
                    return operation()
                # Short durable operations have no Stop action. Closing waits
                # for their callback; global shutdown cannot relabel a commit.
                from ..reader_cancellation import publication_guard
                with publication_guard():
                    return operation()
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                from ..desktop_preparation import DesktopPreparationError
                raise DesktopPreparationError("lesson_design_operation", str(exc)) from exc
        import subprocess
        self._task = self.tasks.submit(text, work, on_success=finish, on_failure=fail)

    def cancel(self):
        if self._task and self._cancellable and not self._stopping:
            if self.tasks.cancel(self._task) is False:
                return
            self._stopping = True
            self.stop_button.setEnabled(False)
            self.status.setText("正在停止本次任务；当前输入与已有成品保留…")

    def task_cancelled(self, identity, _label, message):
        if identity == self._task:
            self.status.setText(message)

    def task_finished(self, identity):
        if identity == self._task:
            self._task = None
            self._stopping = False
            self.set_busy(False)
            if self._close_when_finished:
                self._close_when_finished = False
                self.reject()

    def save_draft(self):
        if not self.apply_framework(quiet=True): return
        self.sync_page()
        # Explicitly selected full materials already live in the design. Give
        # the core request a readable scope instead of making the teacher type
        # that same selection again. Existing teacher text stays authoritative.
        resources = self.history.value.get('composition', {}).get('resources', [])
        if resources and not self.page.materials.toPlainText().strip():
            names = {'complete_question': '完整题', 'knowledge_note': '知识卡',
                     'textbook_image': '教材图', 'lesson_activity': '教学环节',
                     'personal_text': '个人文字', 'lesson_framework': '教案栏目',
                     'handout_knowledge': '讲义知识段', 'preparation_image': '教学图片',
                     'handout_image': '讲义原图与知识网'}
            lines = ['本次使用已明确加入“素材与页面编排”的资料：']
            lines.extend(f"{index}. {names[item['original']['kind']]}：{item['title']}"
                         for index, item in enumerate(resources, 1))
            lines.append('完整题答、图像、页码和来源版本保存在各素材原始快照中。')
            self.page.materials.setPlainText('\n'.join(lines))
        payload = deepcopy(self.page._payload())
        def done(receipt):
            self.page._form_baseline = deepcopy(payload)
            self.page.setWindowModified(self.page._payload() != payload)
            self.status.setText("已保存备课草稿。教学环节、素材编排和成品版本记录一起保留。")
        self.run("正在保存教学设计…", lambda: self.facade.create_preparation_draft(payload), done)

    def generate_outputs(self):
        if not self.apply_framework(quiet=True): return
        self.sync_page()
        payload = deepcopy(self.page._payload())
        def done(record):
            self.history.value["exports"].append(record)
            self.sync_page()
            self.outputs.setCurrentIndex(self.outputs.findData(record["id"]))
            self.tabs.setCurrentIndex(2)
            self.status.setText("教案 Word和课堂演示 PPT已生成。可预览、打开编辑或导出，保存草稿可保留这次成品版本。")
        from ..reader_cancellation import cancellation_requested
        self.run("正在生成本地成品（不调用模型）…",
                 lambda: export_design(self.facade, payload, cancelled=cancellation_requested), done,
                 cancellable=True)

    def open_file(self, name):
        try:
            path = checked_file(self.facade, self.history.value, self.outputs.currentData(), name)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except ValueError as exc:
            self.status.setText(str(exc))

    def preview_ppt(self):
        plan = deepcopy(self.history.value); identity = self.outputs.currentData()
        name = self.preview_audience.currentData()
        def show(report):
            from ..desktop_lesson_narration import narration_scripts, put_narration
            def load(count):
                return narration_scripts(self.history.value, identity, name, report["source_sha256"], count)
            def save(scripts):
                self.history.put(put_narration(self.history.value, identity, name, report["source_sha256"], scripts))
                self.sync_page()
                self.show_selected_output()
            ActualPptPreview(report, self, tasks=self.tasks, script_load=load, script_save=save,
                verify_presentation=lambda: checked_file(self.facade, plan, identity, name)).exec()
        self.run("正在转换实际PPTX…", lambda: actual_ppt_preview(self.facade, plan, identity, name=name),
                 show, cancellable=True)

    def preview_word(self):
        plan = deepcopy(self.history.value); identity = self.outputs.currentData()
        if self.busy or not identity:
            return
        def show(report):
            ActualPptPreview(report, self, tasks=self.tasks).exec()
        self.run('正在生成教案 Word预览…', lambda: actual_word_preview(self.facade, plan, identity),
                 show, cancellable=True)

    def reject(self):
        if self.busy:
            self._close_when_finished = True
            self.cancel()
            if not self._cancellable:
                self.status.setText("正在保存；完成后返回，保存结果会保留。")
            return
        if not self.apply_framework(quiet=True): return
        self.sync_page()
        if self.page.recovery is not None and not self.page.recovery.flush():
            self.status.setText("恢复副本未保存成功。请先保存草稿或重试；当前输入仍在。")
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self._close_when_finished = True
            self.cancel()
            if not self._cancellable:
                self.status.setText("正在保存；完成后返回，保存结果会保留。")
            event.ignore()
        else:
            super().closeEvent(event)
