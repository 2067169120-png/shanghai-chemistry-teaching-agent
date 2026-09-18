"""Native teacher-owned lesson nodes; reuse the preparation form and recovery."""
from __future__ import annotations
from copy import deepcopy
import json
from uuid import uuid4
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)
from ..desktop_lesson_design import (KINDS, DesignHistory, bind_material,
    content_fingerprint, coverage, new_design, new_node, update_goal, update_node)
from ..desktop_lesson_output import FILES, actual_ppt_preview, checked_file, export_design
from .components import page_scroll


def label(text):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


class ActualPptPreview(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("实际PPTX预览 · LibreOffice")
        self.resize(1080, 760)
        root = QVBoxLayout(self)
        root.addWidget(label("来自已生成的PPTX，不是结构示意图。LibreOffice与PowerPoint可能存在呈现差异。"))
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.picture)
        root.addWidget(scroll, 1)
        self.scroll = scroll
        import pypdfium2 as pdfium
        from ..desktop_local_pagination import _PDF_LOCK
        with _PDF_LOCK, pdfium.PdfDocument(report["path"]) as doc:
            self.count = len(doc)
        row = QHBoxLayout()
        self.prev, self.next = QPushButton("上一页"), QPushButton("下一页")
        self.caption = QLabel()
        row.addWidget(self.prev); row.addWidget(self.caption); row.addWidget(self.next)
        open_pdf = QPushButton("打开完整PDF")
        open_pdf.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(report["path"])))
        row.addWidget(open_pdf)
        root.addLayout(row)
        self.index = 0
        self.prev.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        self.move(0)

    def move(self, delta):
        import pypdfium2 as pdfium
        from ..desktop_local_pagination import _PDF_LOCK
        from io import BytesIO
        self.index = max(0, min(self.count - 1, self.index + delta))
        with _PDF_LOCK, pdfium.PdfDocument(self.report["path"]) as doc:
            page = doc[self.index]
            bitmap = page.render(scale=1.25)
            image = bitmap.to_pil()
            stream = BytesIO(); image.save(stream, format="PNG")
            image.close(); bitmap.close(); page.close()
        pixmap = QPixmap()
        pixmap.loadFromData(stream.getvalue())
        self.picture.setPixmap(pixmap)
        self.caption.setText(f"{self.index+1} / {self.count}")
        self.prev.setEnabled(self.index > 0); self.next.setEnabled(self.index+1 < self.count)


class LessonDesignDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.facade, self.tasks = page, page.facade, page.tasks
        self.history = DesignHistory(page._lesson_design or new_design(page._payload()))
        self.loading, self.busy = True, False
        self.current = None
        self.setWindowTitle("教学设计 · " + (page.topic.text() or "未命名课题"))
        self.resize(1200, 790)
        self.setMinimumSize(800, 650)
        root = QVBoxLayout(self)
        heading = label("教学设计  /  目标、活动与评价")
        heading.setObjectName("CardTitle")
        root.addWidget(heading)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        main = QSplitter()
        self.tabs.addTab(main, "教学环节")
        left = QWidget(); l = QVBoxLayout(left)
        self.nodes = QListWidget()
        self.nodes.setAccessibleName("本课教学环节顺序")
        l.addWidget(self.nodes, 1)
        actions = QGridLayout(); l.addLayout(actions)
        for i, (name, action) in enumerate((("新增", self.add_node), ("删除", self.delete_node),
            ("上移", lambda: self.move_node(-1)), ("下移", lambda: self.move_node(1)),
            ("撤销", lambda: self.travel(False)), ("重做", lambda: self.travel(True)))):
            b = QPushButton(name); b.setObjectName("QuietButton")
            b.clicked.connect(action); actions.addWidget(b, i//2, i%2)
        main.addWidget(left)
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
        self.tabs.addTab(goals_page, "目标与资料")
        out = QWidget(); o = QVBoxLayout(out)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True); o.addWidget(self.report, 1)
        self.outputs = QComboBox(); o.addWidget(self.outputs)
        ar = QHBoxLayout(); o.addLayout(ar)
        for name, filename in (("打开教案", FILES[1]), ("打开PPTX", FILES[0]), ("打开学习单", FILES[2])):
            b = QPushButton(name); b.setObjectName("QuietButton")
            b.clicked.connect(lambda _=False, file=filename: self.open_file(file)); ar.addWidget(b)
        self.tabs.addTab(out, "输出与检查")
        self.status = label("本地编辑，不调用模型。保存草稿沿用原备课；未保存编辑进入原恢复副本。")
        root.addWidget(self.status)
        bottom = QHBoxLayout(); root.addLayout(bottom)
        self.save = QPushButton("保存草稿"); self.save.clicked.connect(self.save_draft)
        self.generate = QPushButton("生成教案 / PPT / 学习单"); self.generate.setObjectName("PrimaryAction")
        self.generate.clicked.connect(self.generate_outputs)
        self.preview = QPushButton("核对实际PPTX"); self.preview.clicked.connect(self.preview_ppt)
        self.return_button = QPushButton("返回"); self.return_button.clicked.connect(self.reject)
        for b in (self.save, self.generate, self.preview, self.return_button):
            bottom.addWidget(b)
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
        self.page._lesson_design = deepcopy(self.history.value)
        self.page.setWindowModified(self.page._payload() != self.page._form_baseline)
        self.refresh_report()

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
        self.select_node(self.nodes.currentRow())
        self.refresh_report()

    def select_node(self, index):
        if self.loading:
            return
        self.loading = True
        self.properties.setEnabled(index >= 0)
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

    def add_node(self):
        n = new_node()
        value = deepcopy(self.history.value); value["nodes"].append(n)
        self.history.put(value); self.refresh_lists(n["id"]); self.sync_page()

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
            self.outputs.addItem(out["created_at"][:19].replace("T", " ") + " · 三类成品", out["id"])
        if chosen:
            self.outputs.setCurrentIndex(max(0, self.outputs.findData(chosen)))
        self.preview.setEnabled(not self.busy and self.outputs.count() > 0)

    def set_busy(self, value):
        self.busy = value
        for w in (self.tabs, self.save, self.generate, self.preview, self.return_button):
            w.setEnabled(not value)
        self.preview.setEnabled(not value and self.outputs.count() > 0)

    def run(self, text, operation, success):
        self.set_busy(True); self.status.setText(text)
        def finish(value):
            self.set_busy(False); success(value)
        def fail(message):
            self.set_busy(False); self.status.setText(message + "；当前输入与已有成品保留。")
        # Translate known local errors without exposing generic task-bridge errors.
        def work():
            try:
                return operation()
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                from ..desktop_preparation import DesktopPreparationError
                raise DesktopPreparationError("lesson_design_operation", str(exc)) from exc
        import subprocess
        self.tasks.submit(text, work, on_success=finish, on_failure=fail)

    def save_draft(self):
        self.sync_page()
        payload = deepcopy(self.page._payload())
        def done(receipt):
            self.page._form_baseline = deepcopy(payload)
            self.page.setWindowModified(self.page._payload() != payload)
            self.status.setText("已保存备课草稿。教学环节和三类输出的版本记录一起保留。")
        self.run("正在保存教学设计…", lambda: self.facade.create_preparation_draft(payload), done)

    def generate_outputs(self):
        self.sync_page()
        payload = deepcopy(self.page._payload())
        def done(record):
            self.history.value["exports"].append(record)
            self.sync_page()
            self.tabs.setCurrentIndex(2)
            self.status.setText("三类文件已生成；请查看版式并保存草稿。PPT备注含教师答案，不是匿名学生文件。")
        self.run("正在生成三类文件（本地，不调用模型）…", lambda: export_design(self.facade, payload), done)

    def open_file(self, name):
        try:
            path = checked_file(self.facade, self.history.value, self.outputs.currentData(), name)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except ValueError as exc:
            self.status.setText(str(exc))

    def preview_ppt(self):
        plan = deepcopy(self.history.value); identity = self.outputs.currentData()
        self.run("正在转换实际PPTX…", lambda: actual_ppt_preview(self.facade, plan, identity),
                 lambda report: ActualPptPreview(report, self).exec())

    def reject(self):
        if self.busy:
            return
        self.sync_page()
        if self.page.recovery is not None and not self.page.recovery.flush():
            self.status.setText("恢复副本未保存成功。请先保存草稿或重试；当前输入仍在。")
            return
        super().reject()
