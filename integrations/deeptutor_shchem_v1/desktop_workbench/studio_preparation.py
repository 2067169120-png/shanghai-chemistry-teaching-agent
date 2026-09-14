"""Additive studio entry points on the existing preparation generation workflow."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox
from .workflow_pages import PreparationPage
from .components import set_status


class StudioPreparationPage(PreparationPage):
    def __init__(self, facade, tasks, parent=None):
        super().__init__(facade, tasks, parent)
        from .preparation_recovery import PreparationRecoveryController
        state_root = getattr(getattr(facade, "paths", None), "state_root", None)
        self.recovery = PreparationRecoveryController(self) if state_root is not None else None

    def studio_busy(self) -> bool:
        return bool(self._save_task_id or self._generation_qt_task_id
                    or self._active_preparation_task_id or self._word_import_in_flight)

    def apply_studio_template(self, key: str, topic: str = "", audience: str = "") -> bool:
        from ..desktop_studio import template_brief
        if self.studio_busy():
            set_status(self.status, "attention", "请先等待当前保存、导入或生成结束。")
            return False
        value = template_brief(self._payload(), key, topic=topic, audience=audience)
        self.topic.setText(value["topic"])
        self.audience.setText(value["audience"])
        self.objective.setPlainText(value["objective"])
        self.template_detail.setText(value["advanced"]["template_and_delivery"])
        self.route.setCurrentText(value["lesson_route"])
        if "timing" in value:
            self.lesson_count.setValue(value["timing"]["periods"])
            self.lesson_minutes.setValue(value["timing"]["minutes_per_period"])
        self.setWindowModified(self._payload() != self._form_baseline)
        set_status(self.status, "success", "已加入教学模板，可继续编辑。原资料、原图和已有课题保留；尚未保存或调用模型。")
        return True

    def append_classroom_feedback(self, text: str) -> bool:
        if self.studio_busy():
            set_status(self.status, "attention", "请等待当前备课操作结束，再带入课堂反馈。")
            return False
        old = self.materials.toPlainText()
        if text not in old:
            self.materials.setPlainText("\n\n".join(filter(None, (old, text))))
        self.setWindowModified(self._payload() != self._form_baseline)
        set_status(self.status, "success", "教师录入的课堂反馈已追加；未改变题目、原图或评分，未调用模型。请保存草稿。")
        return True

    def _open_draft(self, draft_id: str | None = None) -> None:
        from ..desktop_preparation import normalize_preparation_payload
        from .preparation_draft_dialog import PreparationDraftDialog

        if self.studio_busy():
            set_status(self.status, "attention", "请先等待保存、导入或生成结束，再打开其他草稿。")
            return
        dialog = PreparationDraftDialog(self.facade, self)
        if isinstance(draft_id, str) and not dialog.select_draft_id(draft_id):
            dialog.deleteLater()
            set_status(self.status, "attention", "这份草稿已不在当前草稿列表或已无法读取。请刷新后重选；当前填写未改变，也没有改开其他草稿。")
            return
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.selected is None:
            dialog.deleteLater()
            return
        selected = dialog.selected
        dialog.deleteLater()
        if self._payload() != self._form_baseline:
            decision = QMessageBox.question(
                self, "确认载入另一份草稿",
                "当前表单有尚未保存的修改。载入所选草稿会替换当前表单，已保存的草稿不会改变。是否放弃这些未保存修改并载入？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
        payload = selected["payload"]
        normalized = normalize_preparation_payload(payload)
        self.output_kind.setCurrentIndex(self.output_kind.findData(payload["output_kind"]))
        self.topic.setText(payload["topic"])
        self.audience.setText(payload["audience"])
        if self.route.findText(payload["lesson_route"]) < 0:
            self.route.addItem(payload["lesson_route"])
        self.route.setCurrentText(payload["lesson_route"])
        self.lesson_count.setValue(normalized["timing"]["periods"])
        self.lesson_minutes.setValue(normalized["timing"]["minutes_per_period"])
        self.objective.setPlainText(payload["objective"])
        self.materials.setPlainText(payload["materials"])
        self.image_assets_widget.set_assets(payload.get("image_assets", ()))
        self.image_assets_widget.set_image_input_mode(payload.get("image_input_mode"))
        for key, widget in (
            ("learning_and_experiment", self.learning_detail),
            ("template_and_delivery", self.template_detail),
            ("homework_and_strategy", self.strategy_detail),
        ):
            widget.setText(payload["advanced"].get(key, ""))
        self._form_baseline = self._payload()
        self.setWindowModified(False)
        self._preparation_task_id = None
        self._current_task_status = ""
        self._current_task_retryable = False
        self.progress_card.hide()
        self._hide_result_artifacts()
        self.result_card.hide()
        set_status(self.status, "success", "备课草稿已载入，可继续修改并另存；生成仍需确认。原草稿未改写，尚未调用模型。")
        self.topic.setFocus()
