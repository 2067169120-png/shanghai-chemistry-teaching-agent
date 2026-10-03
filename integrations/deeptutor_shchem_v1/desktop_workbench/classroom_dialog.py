"""Saved local rosters, assignment attendance, and explicit exam-row links."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..desktop_classroom_registry import (
    STATUS_LABELS,
    ClassroomRegistry,
    preview_roster_csv,
)
from ..desktop_work_batches import WorkBatchStore


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().hide()
    table.setMinimumHeight(180)
    return table


def _item(text, *, editable=True, identity=None):
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setData(Qt.ItemDataRole.UserRole, identity)
    return item


class ClassroomDialog(QDialog):
    batch_requested = Signal(str)

    def __init__(self, facade, parent=None):
        super().__init__(parent)
        self.registry = ClassroomRegistry(facade)
        self.batch_store = WorkBatchStore(facade)
        self.facade = facade
        self.profiles = facade.student_profiles()
        self.classroom = self.work = None
        self.class_dirty = self.work_dirty = self._reading = False
        self.setWindowTitle("班级名册与作业收交")
        self.resize(1080, 760)
        self.setMinimumSize(720, 550)
        root = QVBoxLayout(self)
        title = QLabel("班级名册与作业收交")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel(
            "按保存的名单逐人核对收交；建立作业时固定名单版本。姓名、班内编号与匿名作答档案由教师明确关联。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.classes = QComboBox()
        root.addWidget(self.classes)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._roster_page()
        self._work_page()
        self.message = QLabel("")
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        close = QPushButton("返回工作台")
        close.setObjectName("QuietButton")
        close.clicked.connect(self.close)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.classes.currentIndexChanged.connect(self._select_class)
        self._reload_classes(None)
        self._load_class(None)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def _roster_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.label, self.term = QLineEdit(), QLineEdit()
        self.label.setMaxLength(100)
        self.term.setMaxLength(120)
        self.label.setPlaceholderText("如 高二（1）班")
        self.term.setPlaceholderText("如 2026—2027 第一学期")
        form = QFormLayout()
        form.addRow("班级", self.label)
        form.addRow("学期", self.term)
        layout.addLayout(form)
        self.members = _table(
            ["姓名或代号（本机）", "班内编号（可选）", "关联的匿名作答档案"]
        )
        self.members.setAccessibleName("班级成员名册")
        layout.addWidget(self.members, 1)
        actions = QGridLayout()
        for index, (label, callback) in enumerate(
            (
                ("添加学生", self.add_member),
                ("移除所选行", self.remove_member),
                ("导入名册 CSV…", self.import_roster),
                ("保存名册模板…", self.roster_template),
                ("保存班级名册", self.save_class),
            )
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button, index // 3, index % 3)
        layout.addLayout(actions)
        tip = QLabel(
            "同名学生保留为不同成员。班内编号有值时须唯一；同一班级的一份匿名档案只能关联一名成员。旧作业继续使用建单时的名单。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.tabs.addTab(page, "班级名册")
        self.label.textEdited.connect(self._class_edited)
        self.term.textEdited.connect(self._class_edited)
        self.members.itemChanged.connect(self._class_edited)

    def _work_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.works = QComboBox()
        layout.addWidget(self.works)
        new = QHBoxLayout()
        self.work_title = QLineEdit()
        self.work_title.setPlaceholderText("本次作业名称")
        self.work_title.setMaxLength(120)
        self.due_date = QLineEdit()
        self.due_date.setPlaceholderText("截止日期 YYYY-MM-DD（可选）")
        self.due_date.setMaxLength(10)
        new.addWidget(self.work_title, 2)
        new.addWidget(self.due_date, 1)
        create = QPushButton("按已保存名单建立作业")
        create.clicked.connect(self.create_work)
        new.addWidget(create)
        layout.addLayout(new)
        self.work_summary = QLabel("先保存班级名册，再建立作业。")
        self.work_summary.setWordWrap(True)
        layout.addWidget(self.work_summary)
        self.entries = _table(["名单成员", "收交状态", "实际作答", "状态依据 / 备注"])
        self.entries.setAccessibleName("作业逐人收交状态")
        layout.addWidget(self.entries, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("重新读取收交与作答")
        refresh.setObjectName("QuietButton")
        refresh.clicked.connect(self.refresh_work)
        save = QPushButton("保存收交记录")
        save.clicked.connect(self.save_work)
        review = QPushButton("保存并打开已交作答批改")
        review.clicked.connect(self.open_review)
        for button in (refresh, save, review):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.tabs.addTab(page, "作业收交")
        self.works.currentIndexChanged.connect(self._select_work)
        self.entries.itemChanged.connect(self._work_edited)

    def _class_edited(self, *_):
        if not self._reading:
            self.class_dirty = True

    def _work_edited(self, *_):
        if not self._reading:
            self.work_dirty = True

    def _guard(self, scope):
        dirty = self.class_dirty if scope == "class" else self.work_dirty
        if not dirty:
            return True
        result = QMessageBox.question(
            self,
            "保留当前修改",
            "当前名册或收交记录尚未保存。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Cancel:
            return False
        if result == QMessageBox.StandardButton.Save:
            return self.save_class() if scope == "class" else self.save_work()
        if scope == "class":
            self.class_dirty = False
        else:
            self.work_dirty = False
        return True

    def _reload_classes(self, selected):
        self.classes.blockSignals(True)
        self.classes.clear()
        self.classes.addItem("新建班级名册", None)
        for row in self.registry.classes():
            self.classes.addItem(
                f"{row['label']} · {row['term'] or '未指定学期'}", row["id"]
            )
        self.classes.setCurrentIndex(max(0, self.classes.findData(selected)))
        self.classes.blockSignals(False)

    def _select_class(self):
        cid = self.classes.currentData()
        if not self._guard("class") or not self._guard("work"):
            self.classes.blockSignals(True)
            self.classes.setCurrentIndex(
                max(0, self.classes.findData((self.classroom or {}).get("id")))
            )
            self.classes.blockSignals(False)
            return
        self._load_class(cid)

    def _load_class(self, cid):
        self._reading = True
        try:
            self.classroom = self.registry.snapshot()["classes"].get(cid)
            self.label.setText((self.classroom or {}).get("label", ""))
            self.term.setText((self.classroom or {}).get("term", ""))
            self.members.setRowCount(0)
            for row in (self.classroom or {}).get("members", []):
                self._add_member(row)
            self._reload_works(None)
            self.class_dirty = self.work_dirty = False
            self.message.setText(
                "名册信息保存在本机；选择匿名档案后，作业可引用该档案中已有的实际作答。"
            )
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))
        finally:
            self._reading = False

    def _add_member(self, row):
        index = self.members.rowCount()
        self.members.insertRow(index)
        self.members.setItem(
            index, 0, _item(row.get("label", ""), identity=row.get("member_id"))
        )
        self.members.setItem(index, 1, _item(row.get("external_ref", "")))
        profiles = QComboBox()
        profiles.addItem("尚未关联", "")
        for profile in self.profiles:
            profiles.addItem(
                f"{profile.label_zh} · {profile.student_id[:8]}", profile.student_id
            )
        identity = row.get("profile_id", "")
        if identity and profiles.findData(identity) < 0:
            profiles.addItem("已关联档案不可读取 · " + identity[:8], identity)
        profiles.setCurrentIndex(max(0, profiles.findData(identity)))
        profiles.currentIndexChanged.connect(self._class_edited)
        self.members.setCellWidget(index, 2, profiles)

    def add_member(self):
        self._add_member({})
        self.class_dirty = True

    def remove_member(self):
        if self.members.currentRow() >= 0:
            self.members.removeRow(self.members.currentRow())
            self.class_dirty = True

    def save_class(self):
        rows = [
            {
                "member_id": self.members.item(r, 0).data(Qt.ItemDataRole.UserRole),
                "label": self.members.item(r, 0).text(),
                "external_ref": self.members.item(r, 1).text(),
                "profile_id": self.members.cellWidget(r, 2).currentData(),
            }
            for r in range(self.members.rowCount())
        ]
        try:
            saved = self.registry.save_class(
                self.label.text(),
                self.term.text(),
                rows,
                class_id=(self.classroom or {}).get("id"),
                expected_revision=(self.classroom or {}).get("revision"),
            )
            self.classroom = saved
            self._reading = True
            for r, row in enumerate(saved["members"]):
                self.members.item(r, 0).setData(
                    Qt.ItemDataRole.UserRole, row["member_id"]
                )
            self._reading = False
            self.class_dirty = False
            self._reload_classes(saved["id"])
            self._reload_works(
                (self.work or {}).get("id"), preserve_editor=self.work is not None
            )
            self.message.setText(
                f"已保存 {saved['label']}，{len(saved['members'])}名成员。"
            )
            return True
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self._reading = False
            self.message.setText(getattr(exc, "message_zh", str(exc)))
            return False

    def roster_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存名册模板", "班级名册模板.csv", "CSV (*.csv)"
        )
        if path:
            try:
                Path(path).write_bytes(
                    "姓名或代号,班内编号\n示例学生,001\n".encode("utf-8-sig")
                )
                self.message.setText("模板已保存；请替换示例行后导入核对。")
            except OSError:
                self.message.setText("模板未能保存，请选择可写位置。")

    def import_roster(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择班级名册", "", "CSV (*.csv)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 1024 * 1024:
                raise ValueError("名册 CSV 需小于1MB。")
            rows = preview_roster_csv(Path(path).read_bytes())
            preview = QDialog(self)
            preview.setWindowTitle("核对待添加名册")
            preview.resize(640, 500)
            layout = QVBoxLayout(preview)
            layout.addWidget(
                QLabel(
                    f"将添加 {len(rows)}名成员。已有成员保留；匿名档案须逐人明确选择。"
                )
            )
            table = _table(["姓名或代号", "班内编号"])
            table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                table.setItem(r, 0, _item(row["label"], editable=False))
                table.setItem(r, 1, _item(row["external_ref"], editable=False))
            layout.addWidget(table, 1)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
                "将这些成员添加到编辑中的名册"
            )
            buttons.accepted.connect(preview.accept)
            buttons.rejected.connect(preview.reject)
            layout.addWidget(buttons)
            if preview.exec() == QDialog.DialogCode.Accepted:
                for row in rows:
                    self._add_member(row)
                self.class_dirty = True
            preview.deleteLater()
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))

    def _reload_works(self, selected, *, preserve_editor=False):
        self.works.blockSignals(True)
        self.works.clear()
        self.works.addItem("选择已建立的作业", None)
        if self.classroom:
            for row in self.registry.works(self.classroom["id"]):
                self.works.addItem(
                    f"{row['title']} · {row['due_date'] or '未定截止日期'}", row["id"]
                )
        self.works.setCurrentIndex(max(0, self.works.findData(selected)))
        self.works.blockSignals(False)
        if not preserve_editor:
            self._load_work(selected)

    def _select_work(self):
        wid = self.works.currentData()
        if not self._guard("work"):
            self.works.blockSignals(True)
            self.works.setCurrentIndex(
                max(0, self.works.findData((self.work or {}).get("id")))
            )
            self.works.blockSignals(False)
            return
        self._load_work(wid)

    def create_work(self):
        if not self._guard("class") or not self._guard("work"):
            return
        try:
            saved = self.registry.create_work(
                (self.classroom or {}).get("id"),
                self.work_title.text(),
                self.due_date.text(),
                expected_class_revision=(self.classroom or {}).get("revision"),
            )
            self._reload_works(saved["id"])
            self.work_title.clear()
            self.message.setText("作业已建立，全部成员初始为未录入；请逐人核对收交。")
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))

    def _load_work(self, wid):
        self._reading = True
        self.work_summary.setText("正在读取收交记录…")
        try:
            self.entries.setRowCount(0)
            self.work = None
            if not wid:
                self.work_summary.setText(
                    "请选择已保存作业，或按已保存的班级名册建立新作业。"
                )
                return
            overview = self.registry.work_overview(wid)
            self.work = overview["work"]
            submissions = self.batch_store.available_submissions()
            self.entries.setRowCount(len(overview["rows"]))
            for r, row in enumerate(overview["rows"]):
                self.entries.setItem(
                    r,
                    0,
                    _item(
                        row["label"]
                        + (" · " + row["external_ref"] if row["external_ref"] else ""),
                        editable=False,
                        identity=row["member_id"],
                    ),
                )
                status = QComboBox()
                for key, label in STATUS_LABELS.items():
                    status.addItem(label, key)
                status.setCurrentIndex(status.findData(row["status"]))
                self.entries.setCellWidget(r, 1, status)
                source = QComboBox()
                source.addItem("未选择实际作答", "")
                for sub in submissions:
                    if sub["student_id"] == row["profile_id"]:
                        source.addItem(
                            f"{sub['created_at'][:16]} · {sub['match_count']}题 · {sub['submission_id'][-8:]}",
                            sub["submission_id"],
                        )
                if row["submission_id"] and source.findData(row["submission_id"]) < 0:
                    source.addItem(
                        "原作答不可读 · " + row["submission_id"][-8:],
                        row["submission_id"],
                    )
                source.setCurrentIndex(max(0, source.findData(row["submission_id"])))
                self.entries.setCellWidget(r, 2, source)
                self.entries.setItem(r, 3, _item(row["note"]))
                if row["issue"]:
                    self.entries.item(r, 0).setToolTip(row["issue"])
                status.currentIndexChanged.connect(
                    lambda _, s=status, c=source: self._status_changed(s, c)
                )
                source.currentIndexChanged.connect(self._work_edited)
            counts = overview["counts"]
            self.work_summary.setText(
                f"名单 {overview['roster_total']}人 · 应交 {overview['expected']}人 · "
                + " · ".join(
                    f"{label} {counts[key]}" for key, label in STATUS_LABELS.items()
                )
                + f" · 引用需核对 {overview['invalid_references']}"
                + (
                    "\n班级名册已有更新；本任务仍按建单名单核对。"
                    if overview["roster_changed"]
                    else ""
                )
            )
            self.work_dirty = False
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))
            self.work_summary.setText("收交记录未能完整读取，请重新核对作业。")
        finally:
            self.work_dirty = False
            self._reading = False

    def _status_changed(self, status, source):
        if status.currentData() != "submitted":
            source.setCurrentIndex(0)
        self._work_edited()

    def save_work(self):
        if not self.work:
            self.message.setText("请先建立或选择作业。")
            return False
        entries = {
            self.entries.item(r, 0).data(Qt.ItemDataRole.UserRole): {
                "status": self.entries.cellWidget(r, 1).currentData(),
                "submission_id": self.entries.cellWidget(r, 2).currentData(),
                "note": self.entries.item(r, 3).text(),
            }
            for r in range(self.entries.rowCount())
        }
        try:
            saved = self.registry.save_work(
                self.work["id"], entries, expected_revision=self.work["revision"]
            )
            self._load_work(saved["id"])
            self.message.setText("收交记录已保存；已交均对应明确选定的实际作答。")
            return True
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))
            return False

    def refresh_work(self):
        if self._guard("work"):
            self._load_work((self.work or {}).get("id"))

    def open_review(self):
        if not self._guard("class") or not self.save_work():
            return
        try:
            batch = self.registry.make_review_batch(
                self.work["id"], self.work["revision"]
            )
            self.batch_requested.emit(batch["batch_id"])
            self.accept()
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))

    def closeEvent(self, event):
        if self._guard("class") and self._guard("work"):
            event.accept()
        else:
            event.ignore()

    def reject(self):
        if self._guard("class") and self._guard("work"):
            super().reject()


class ExamRosterBindingDialog(QDialog):
    def __init__(self, facade, exam, parent=None):
        super().__init__(parent)
        self.registry = ClassroomRegistry(facade)
        self.exam = exam
        self.original = self.registry.exam_binding(exam)
        self._dirty = False
        self._previous_class = None
        self._previous_clear = False
        self.setWindowTitle("将考试行关联班级成员")
        self.resize(950, 660)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "逐行选择实际对应的班级成员。同名不自动关联；未关联的考试行仍可用于本次考试统计。可分班保存。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.classes = QComboBox()
        for row in self.registry.classes():
            self.classes.addItem(f"{row['label']} · {row['term']}", row)
        layout.addWidget(self.classes)
        self.clear_existing = QCheckBox("清除本考试全部旧关联，按本次核对重新建立")
        layout.addWidget(self.clear_existing)
        self.table = _table(["考试行（本机标识）", "导入班级", "明确关联的成员"])
        layout.addWidget(self.table, 1)
        self.message = QLabel(self.original["reason"])
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.commit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.classes.currentIndexChanged.connect(self.populate)
        self.clear_existing.toggled.connect(self.populate)
        self.populate()

    def populate(self):
        classroom = self.classes.currentData()
        changed_scope = (
            classroom != self._previous_class
            or self.clear_existing.isChecked() != self._previous_clear
        )
        if self._dirty and changed_scope:
            answer = QMessageBox.question(
                self,
                "保留行选择",
                "更换班级或清除旧关联会重置尚未保存的行选择。",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                self.classes.blockSignals(True)
                self.classes.setCurrentIndex(
                    self.classes.findData(self._previous_class)
                )
                self.classes.blockSignals(False)
                self.clear_existing.blockSignals(True)
                self.clear_existing.setChecked(self._previous_clear)
                self.clear_existing.blockSignals(False)
                return
        self._previous_class = classroom
        self._previous_clear = self.clear_existing.isChecked()
        self.table.setRowCount(len(self.exam["students"]))
        links = (self.original.get("binding") or {}).get("links", {})
        for r, student in enumerate(self.exam["students"]):
            self.table.setItem(
                r,
                0,
                _item(
                    student["local_label"] + " · " + student["id"],
                    editable=False,
                    identity=student["id"],
                ),
            )
            self.table.setItem(r, 1, _item(student["class"], editable=False))
            combo = QComboBox()
            combo.addItem("暂不关联", "")
            for member in (classroom or {}).get("members", []):
                combo.addItem(
                    f"{member['label']} · {member['external_ref'] or member['member_id'][:8]}",
                    member["member_id"],
                )
            old = links.get(student["id"])
            if old and not self.clear_existing.isChecked():
                if classroom and old["class_id"] == classroom["id"]:
                    found = combo.findData(old["member_id"])
                    if found >= 0:
                        combo.setCurrentIndex(found)
                else:
                    combo.addItem("已关联其他班级；须先在原班级取消", "")
                    combo.setCurrentIndex(combo.count() - 1)
                    combo.setEnabled(False)
            self.table.setCellWidget(r, 2, combo)
            combo.currentIndexChanged.connect(self.mark_dirty)
        self._dirty = False

    def mark_dirty(self):
        self._dirty = True

    def commit(self):
        classroom = self.classes.currentData()
        if not classroom:
            self.message.setText("请先在“作业批改 → 班级名册与收交”保存班级名册。")
            return
        pairs = {
            self.table.item(r, 0).data(Qt.ItemDataRole.UserRole): self.table.cellWidget(
                r, 2
            ).currentData()
            for r in range(self.table.rowCount())
            if self.table.cellWidget(r, 2).currentData()
        }
        try:
            self.registry.bind_exam(
                self.exam,
                classroom["id"],
                pairs,
                expected_class_revision=classroom["revision"],
                expected_revision=(self.original.get("binding") or {}).get("revision"),
                clear_existing=self.clear_existing.isChecked(),
            )
            self.accept()
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self.message.setText(getattr(exc, "message_zh", str(exc)))
