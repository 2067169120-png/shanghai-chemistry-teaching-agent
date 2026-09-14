"""Save the editor locally; a recovery copy is not a formal draft or an AI job."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..desktop_editor_recovery import PreparationRecoveryStore, validate_editor_payload


def apply_editor_payload(page, payload):
    """Restore only editor fields, including partial text and image roles."""
    payload = validate_editor_payload(payload)
    periods, minutes = payload["lesson_timing"].replace("分钟", "").replace("课时", "").split("×")
    page.output_kind.setCurrentIndex(page.output_kind.findData(payload["output_kind"]))
    page.topic.setText(payload["topic"])
    page.audience.setText(payload["audience"])
    if page.route.findText(payload["lesson_route"]) < 0:
        page.route.addItem(payload["lesson_route"])
    page.route.setCurrentText(payload["lesson_route"])
    page.lesson_count.setValue(int(periods))
    page.lesson_minutes.setValue(int(minutes))
    page.objective.setPlainText(payload["objective"])
    page.materials.setPlainText(payload["materials"])
    page.image_assets_widget.set_assets(payload.get("image_assets", []))
    page.image_assets_widget.set_image_input_mode(payload.get("image_input_mode"))
    for key, widget in (("learning_and_experiment", page.learning_detail),
                        ("template_and_delivery", page.template_detail),
                        ("homework_and_strategy", page.strategy_detail)):
        widget.setText(payload["advanced"][key])


class PreparationRecoveryController(QObject):
    INTERVAL_MS = 2000

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.store = PreparationRecoveryStore(page.facade.paths.state_root)
        self.default = deepcopy(page._payload())
        self._last_saved = (deepcopy(self.default), False)
        self._sequence = 0
        self._inflight = False
        self._closed = False
        self._blocked = False
        self.panel = QWidget(page)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel('已启用自动恢复。需要保留当前版本时，请点击“保存草稿”。')
        self.status.setObjectName("StatusInfo")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.save_now = QPushButton('立即更新自动恢复副本')
        self.save_now.setObjectName("QuietButton")
        self.save_now.clicked.connect(self.flush)
        self.new_button = QPushButton("新建空白备课")
        self.new_button.setObjectName("QuietButton")
        self.new_button.clicked.connect(self.new_blank)
        buttons.addWidget(self.save_now)
        buttons.addWidget(self.new_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        page.content_layout.insertWidget(1, self.panel)
        try:
            record = self.store.load()
            if record is not None:
                apply_editor_payload(page, record["payload"])
                page._form_baseline = {} if record["dirty"] else deepcopy(page._payload())
                self._last_saved = self._snapshot()
                page.setWindowModified(self._last_saved[1])
                self.status.setText("已恢复上次编辑（" + self._stamp(record["saved_at"]) +
                                    "）。未调用模型；图片引用已保留，原图缺失仍须重新关联。")
        except Exception:
            apply_editor_payload(page, self.default)
            self._blocked = True
            self.status.setText("上次恢复副本无法完整读取，已保留原文件且暂停自动覆盖。仍可正式保存草稿；请先备份并检查个人目录 recovery/preparation.v1.json。")
        self.timer = QTimer(self)
        self.timer.setInterval(self.INTERVAL_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    @staticmethod
    def _stamp(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M:%S")
        except ValueError:
            return "保存时间待核对"

    def _snapshot(self):
        payload = self.page._payload()  # GUI thread only
        return payload, payload != self.page._form_baseline

    def tick(self):
        if self._closed:
            return
        snapshot = self._snapshot()
        self.page.setWindowModified(snapshot[1])
        if self._blocked or self._inflight or snapshot == self._last_saved:
            return
        self._sequence += 1
        sequence = self._sequence
        self._inflight = True
        self.status.setText("正在保存本机恢复副本…")
        self.page.tasks.submit(
            "保存备课恢复副本", lambda: self.store.save(snapshot[0], dirty=snapshot[1], sequence=sequence),
            on_success=lambda value: self._saved(sequence, snapshot, value),
            on_failure=lambda message: self._failed(sequence, message),
        )

    def _saved(self, sequence, snapshot, value):
        self._inflight = False
        if self._closed or sequence != self._sequence or value is None:
            return
        self._last_saved = deepcopy(snapshot)
        self.status.setText("本机恢复副本已更新 · " + self._stamp(value["saved_at"]) +
                            (" · 尚有修改未另存正式草稿。" if snapshot[1] else " · 当前表单与已保存版本一致。"))

    def _failed(self, sequence, _message):
        self._inflight = False
        if not self._closed and sequence == self._sequence:
            self.status.setText("恢复副本保存失败。请检查磁盘空间和目录权限，或点击“立即保存恢复副本”重试；退出前会再次检查。")

    def flush(self):
        """Final synchronous snapshot; sequence ordering defeats a late worker."""
        snapshot = self._snapshot()
        if self._blocked:
            return snapshot == (self.default, False)
        if snapshot == self._last_saved and not self._inflight:
            return True
        self._sequence += 1
        try:
            value = self.store.save(snapshot[0], dirty=snapshot[1], sequence=self._sequence)
        except Exception:
            self._failed(self._sequence, "")
            return False
        self._last_saved = deepcopy(snapshot)
        if value is not None:
            self.status.setText("本机恢复副本已更新 · " + self._stamp(value["saved_at"]) + "。没有生成或覆盖正式草稿。")
        return True

    def prepare_close(self):
        while not self.flush():
            choice = QMessageBox.warning(
                self.page, "最新备课尚未保存在恢复副本中",
                "本机恢复副本未能保存。取消退出可继续编辑；重试会再次保存；放弃仅表示退出时不保留本次最新修改，原有草稿不变。",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Discard:
                break
        self._closed = True
        self.timer.stop()
        return True

    def new_blank(self):
        if self.page.studio_busy() or self._blocked:
            self.status.setText("请先完成当前操作或处理不可读恢复副本；当前填写未改变。")
            return
        if self.page._payload() != self.default:
            answer = QMessageBox.question(
                self.page, "新建空白备课",
                "这会清空当前表单及其恢复副本；未另存的修改将被放弃。已保存的正式草稿、图片文件和题篮不变。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._sequence += 1
        try:
            self.store.save(self.default, dirty=False, sequence=self._sequence)
        except Exception:
            self.status.setText("空白备课未能保存，原表单和恢复副本保留。")
            return
        apply_editor_payload(self.page, self.default)
        self.page._form_baseline = deepcopy(self.default)
        self.page._preparation_task_id = None
        self.page._current_task_status = ""
        self.page._hide_result_artifacts()
        self.page.result_card.hide()
        self.page.progress_card.hide()
        self.page.setWindowModified(False)
        self._last_saved = (deepcopy(self.default), False)
        self.status.setText("已开始空白备课；已保存的正式草稿可从“我的备课”找回。")
