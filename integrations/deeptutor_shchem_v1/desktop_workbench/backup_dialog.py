"""A teacher-facing backup/restore workflow on the existing local task bridge."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import threading
from uuid import uuid4

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QTabWidget, QVBoxLayout, QWidget)

from ..desktop_backup import (BackupCancelled, create_backup, inspect_backup, manifest_revision,
    missing_lesson_images, plan_backup, reconnect_lesson_image, restore_backup, restored_profile,
    summary_text)
from .components import set_status


class BackupDialog(QDialog):
    def __init__(self, paths, tasks, parent=None, flush_editor=None):
        super().__init__(parent)
        self.paths, self.tasks, self.flush_editor = paths, tasks, flush_editor
        self.plan = self.checked = self.archive_path = self.restored_directory = None
        self._active = None
        self._cancel = threading.Event()
        self.setWindowTitle("备课作品备份与恢复")
        self.resize(880, 700)
        self.setMinimumSize(500, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("把备课成果带走，先确认备份范围")
        title.setObjectName("CardTitle")
        root.addWidget(title)
        self.status = QLabel("本地操作，不调用AI。备份含教学正文，请自行妥善保管；原题库与学生档案不在本版范围。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.actions = []
        backup = QWidget()
        lay = QVBoxLayout(backup)
        hint = QLabel("默认：全部备课草稿（含归档/回收站）、作品分类、题篮引用、最近恢复副本。\n可选文件不勾选时只保留引用；不会自动打包原Word题库或公众号原图。")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.images = QCheckBox("包含这些备课引用的本地图片")
        self.outputs = QCheckBox("包含已结束的生成任务、返回稿及成品文件")
        lay.addWidget(self.images); lay.addWidget(self.outputs)
        self.plan_output = QPlainTextEdit()
        self.plan_output.setReadOnly(True)
        self.plan_output.setPlaceholderText("先整理清单，查看包括的数量、文件大小和缺失资料。")
        lay.addWidget(self.plan_output, 1)
        row = QHBoxLayout()
        self.plan_button = self._button("整理备份清单", self.prepare_plan, row)
        self.save_button = self._button("保存备份 ZIP", self.save_backup, row)
        self.save_button.setEnabled(False)
        lay.addLayout(row)
        self.tabs.addTab(backup, "创建备份")
        restore = QWidget(); lay = QVBoxLayout(restore)
        hint = QLabel("先检查清单与每个文件的SHA256，再恢复到新建的独立目录。\n不覆盖当前作品，不合并同名记录，不恢复密钥，不自动运行生成任务。")
        hint.setWordWrap(True); lay.addWidget(hint)
        self.checked_output = QPlainTextEdit(); self.checked_output.setReadOnly(True)
        self.checked_output.setPlaceholderText("选择工作台备份 ZIP 后显示检查结果。")
        lay.addWidget(self.checked_output, 1)
        row = QHBoxLayout()
        self.inspect_button = self._button("选择并检查备份", self.check_backup, row)
        self.restore_button = self._button("恢复到新目录", self.restore, row)
        self.restore_button.setEnabled(False)
        lay.addLayout(row)
        self.open_button = QPushButton("在独立窗口打开恢复副本")
        self.open_button.clicked.connect(self.open_restored)
        self.open_button.setEnabled(False); self.actions.append(self.open_button)
        lay.addWidget(self.open_button)
        self.tabs.addTab(restore, "检查与恢复")
        missing = QWidget(); lay = QVBoxLayout(missing)
        hint = QLabel("这里只检查备课图片引用；原Word/题库重新关联仍需在原导入流程处理。\n补回时必须是相同内容和尺寸的原图，不按文件名猜替换，不修改原草稿。")
        hint.setWordWrap(True); lay.addWidget(hint)
        self.missing_list = QListWidget(); self.missing_list.setWordWrap(True)
        self.missing_list.setAccessibleName("缺失的备课图片")
        lay.addWidget(self.missing_list, 1)
        row = QHBoxLayout()
        self.scan_button = self._button("检查缺失备课图片", self.scan_missing, row)
        self.relink_button = self._button("选择原图补回", self.relink, row)
        self.relink_button.setEnabled(False)
        lay.addLayout(row)
        self.tabs.addTab(missing, "补回原图")
        row = QHBoxLayout()
        self.cancel_button = QPushButton("取消当前操作")
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button); row.addStretch(1); row.addWidget(self.close_button)
        root.addLayout(row)
        self.images.toggled.connect(self.invalidate_plan)
        self.outputs.toggled.connect(self.invalidate_plan)
        self.missing_list.currentItemChanged.connect(lambda *_: self._buttons())

    def _button(self, title, slot, row):
        button = QPushButton(title)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        row.addWidget(button); self.actions.append(button)
        return button

    def invalidate_plan(self, *_):
        self.plan = None
        self.plan_output.clear()
        self._buttons()

    def _buttons(self):
        busy = self._active is not None
        self.tabs.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.save_button.setEnabled(not busy and self.plan is not None)
        self.restore_button.setEnabled(not busy and self.checked is not None)
        self.open_button.setEnabled(not busy and self.restored_directory is not None)
        self.relink_button.setEnabled(not busy and self.missing_list.currentItem() is not None)

    def _run(self, label, operation, apply):
        if self._active:
            return
        self._cancel = threading.Event()
        cancel = self._cancel.is_set
        def work():
            try:
                return {"result": operation(cancel)}
            except BackupCancelled:
                return {"cancelled": True}
        def success(value):
            self._active = None
            if value.get("cancelled"):
                set_status(self.status, "info", "已取消。未覆盖当前作品或旧备份。")
            else:
                apply(value["result"])
            self._buttons()
        self._active = self.tasks.submit(label, work, on_success=success, on_failure=self._failed)
        self._buttons()
        set_status(self.status, "info", label + "…")

    def _failed(self, message):
        self._active = None
        set_status(self.status, "error", message)
        self._buttons()

    def _flush(self):
        if self.flush_editor and not self.flush_editor():
            set_status(self.status, "error", "当前编辑的恢复副本未能保存，请先在备课页检查；未创建不完整备份。")
            return False
        return True

    def prepare_plan(self):
        if self._active or not self._flush():
            return
        self.plan = None
        images, tasks = self.images.isChecked(), self.outputs.isChecked()
        def apply(plan):
            self.plan = plan
            self.plan_output.setPlainText(summary_text(plan.report()))
            set_status(self.status, "success", "清单已就绪。检查范围与缺失项后，可保存本机备份。")
        self._run("整理备份清单", lambda cancel: plan_backup(self.paths.state_root,
                  include_images=images, include_tasks=tasks, cancel=cancel), apply)

    def save_backup(self):
        if not self.plan or self._active:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存新备份，不覆盖旧文件",
            "备课作品-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip", "备份 ZIP (*.zip)")
        if not path:
            return
        plan = self.plan
        self._run("写入并校验备份", lambda cancel: create_backup(plan, path, cancel=cancel),
                  lambda result: set_status(self.status, "success", "备份已保存并通过文件校验：" + Path(result["path"]).name + "。原题库未包含。"))

    def check_backup(self):
        if self._active:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择本机备份", "", "备份 ZIP (*.zip)")
        if not path:
            return
        self.checked = self.archive_path = self.restored_directory = None
        self.checked_output.clear()
        def apply(manifest):
            self.checked, self.archive_path = manifest, path
            self.checked_output.setPlainText(summary_text(manifest))
            set_status(self.status, "success", "文件校验通过；只表示备份可读，不代表教学内容已审定。可恢复到新目录。")
        self._run("检查备份完整性", lambda cancel: inspect_backup(path, cancel=cancel), apply)

    def restore(self):
        if not self.checked or self._active:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择恢复位置；将在其下创建独立子目录")
        if not parent:
            return
        target = Path(parent) / ("备课恢复-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
        project = self.paths.workspace_root.resolve()
        destination = target.resolve()
        if destination.is_relative_to(project) or project.is_relative_to(destination):
            set_status(self.status, "attention", "请选择软件或源码目录以外的位置，例如“文档”中的备份目录；当前没有写入恢复文件。")
            return
        archive, rev = self.archive_path, manifest_revision(self.checked)
        def apply(result):
            self.restored_directory = result["directory"]
            self.checked_output.appendPlainText("\n恢复目录：" + self.restored_directory + "\n当前工作台未被切换或覆盖。打开后检查作品与图片；该副本没有模型设置。")
            set_status(self.status, "success", "独立恢复完成。现在可在新窗口检查，原窗口继续保留。")
        self._run("恢复到独立目录", lambda cancel: restore_backup(archive, target, expected_manifest=rev, cancel=cancel), apply)

    def open_restored(self):
        if not self.restored_directory or self._active:
            return
        try:
            root = restored_profile(self.restored_directory)
            if getattr(sys, "frozen", False):
                program, args = sys.executable, ["--personal-state", str(root)]
            else:
                program = sys.executable
                args = [str(self.paths.workspace_root / "runtime/deeptutor_shchem/desktop_teacher_workbench.pyw"),
                        "--personal-state", str(root)]
            success, _pid = QProcess.startDetached(program, args, str(self.paths.workspace_root))
            if not success:
                raise OSError("launch failed")
        except Exception:
            set_status(self.status, "error", "恢复文件已保留，但新窗口未能启动。请检查程序位置。")
        else:
            set_status(self.status, "success", "已请求打开独立恢复窗口。标题带“恢复副本”；原窗口和默认资料不变。")

    def scan_missing(self):
        if self._active or not self._flush():
            return
        self.missing_list.clear()
        def apply(images):
            for asset in images:
                item = QListWidgetItem(asset["caption"] + "\n" + asset["asset_id"])
                item.setData(Qt.ItemDataRole.UserRole, asset)
                self.missing_list.addItem(item)
            set_status(self.status, "info", f"发现{len(images)}张缺失备课图片。未检查原Word、公众号题库或学生附件。")
        self._run("检查备课图片引用", lambda _cancel: missing_lesson_images(self.paths.state_root), apply)

    def relink(self):
        item = self.missing_list.currentItem()
        if not item or self._active:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择与原引用完全一致的图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.image)")
        if not path:
            return
        asset = item.data(Qt.ItemDataRole.UserRole)
        def apply(_result):
            self.missing_list.takeItem(self.missing_list.row(item))
            self.plan = None
            set_status(self.status, "success", "原图已补回，内容哈希和尺寸一致；草稿身份与正文未改写。")
        self._run("核对并补回原图", lambda _cancel: reconnect_lesson_image(self.paths.state_root, asset, path), apply)

    def cancel_operation(self):
        self._cancel.set()
        set_status(self.status, "info", "正在取消；若文件已完成写入，将如实显示成功。")

    def done(self, result):
        if self._active:
            self.cancel_operation()
            return
        super().done(result)
