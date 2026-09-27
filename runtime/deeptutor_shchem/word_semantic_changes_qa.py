"""Render the tag comparison UI with synthetic services; no provider or database."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal


class SyntheticTasks(QObject):
    task_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.cancelled = set()
        self.serial = 0

    def submit(self, label, operation, *, on_success, on_failure):
        self.serial += 1
        job = dict(id=str(self.serial), label=label, operation=operation,
                   success=on_success, failure=on_failure)
        self.jobs.append(job)
        return job["id"]

    def submit_progress(self, label, operation, *, on_success, on_failure, on_progress):
        task_id = self.submit(label, lambda: operation(on_progress, lambda: task_id in self.cancelled),
                              on_success=on_success, on_failure=on_failure)
        self.jobs[-1]["progress"] = on_progress
        return task_id

    def cancel(self, task_id):
        self.cancelled.add(task_id)

    def finish(self, *, invoke=True):
        job = self.jobs.pop(0)
        if invoke and job["id"] not in self.cancelled:
            try:
                value = job["operation"]()
            except RuntimeError:
                job["failure"]("合成保存失败；未保存标签。")
            else:
                job["success"](value)
        self.task_finished.emit(job["id"])
        return job


def _evidence():
    return [{"kind": "question_text", "quote": "原创合成题面中的电解示意图", "block_index": 2}]


def _section(key, label):
    return {"section_key": key, "chapter_id": "synthetic-chapter", "volume_id": "synthetic-volume",
            "label": label, "status": "auto_suggested", "evidence": _evidence()}


def _attributes(key):
    return {
        "key": key, "source_sha256": "a" * 64, "source_revision": "synthetic-source-r1",
        "question_revision": "synthetic-question-r1", "index_revision": "synthetic-index-r1",
        "extraction_revision": "synthetic-extraction-r1", "rule_revision": "synthetic-rule-r1",
        "annotation_source": "auto_suggested", "curriculum_status": "auto_suggested",
        "supporting_knowledge": [],
        "primary_knowledge": {"id": "K01", "label": "氧化还原反应", "status": "auto_suggested", "evidence": _evidence()},
        "curriculum_candidates": [_section("keep", "电解质与电离"), _section("remove", "原电池")],
    }


class SyntheticFacade:
    """Only local, generated data. Methods record exactly what the dialog asks."""

    def __init__(self):
        self.calls = []
        self.fail_apply = False
        self.save_response = None
        self.fail_result = False
        self.plan = {
            "plan_id": "synthetic-comparison", "revision": "synthetic-plan-r1", "mode": "recheck_automatic",
            "model_label": "合成演示模型 / synthetic-only", "request_count": 5,
            "request_policy": {"max_output_tokens": 32000, "timeout_seconds": 300}, "units": [],
        }
        for index in range(6):
            key = f"synthetic-q{index}"
            attrs = _attributes(key)
            if index == 1:
                attrs["primary_knowledge"].update(id="unknown", label="主考点待确认", evidence=[])
                attrs["curriculum_candidates"] = []
            if index == 3:
                attrs["annotation_source"] = "teacher_modified"
            self.plan["units"].append({
                "key": key, "source_name": "原创合成标签对照样例.docx", "revision": "synthetic-question-r1",
                "status": "skipped" if index == 3 else "ready",
                "reason": "已有教师修改、确认标签或固定修订" if index == 3 else "",
                "images": [], "attributes": attrs,
                "input": {"source_sha256": "a" * 64, "blocks": [
                    {"index": 1, "kind": "shared_context", "text": "公共材料：这是原创合成界面样例，不含真实试卷。", "image_sha256s": []},
                    {"index": 2, "kind": "question_text", "text": "原创合成题面中的电解示意图仅用于界面布局与证据位置测试，不进行化学判断。", "image_sha256s": []},
                ]},
            })
        self.analysis = {"plan_id": self.plan["plan_id"], "finished": False, "items": []}

    def preparation_profiles(self):
        return [SimpleNamespace(profile_id="synthetic-profile", revision="synthetic-profile-r1",
                                provider_name="合成演示模型", model_id="synthetic-only")]

    def word_semantic_tag_preview(self, selections, profile_id, revision, **options):
        self.calls.append(("preview", deepcopy(selections), profile_id, revision, dict(options)))
        self.plan["mode"] = options.get("mode", "missing_only")
        return deepcopy(self.plan)

    def completed_result(self):
        result = {"plan_id": self.plan["plan_id"], "finished": True, "items": []}
        for index in (0, 1, 2):
            unit = self.plan["units"][index]
            proposed = deepcopy(unit["attributes"])
            if index != 2:
                proposed["primary_knowledge"].update(id="K09", label="电化学", evidence=_evidence())
                proposed["curriculum_candidates"] = [_section("keep", "电解质与电离"), _section("add", "电解池")]
            result["items"].append({"key": unit["key"], "status": "ready", "changed": index != 2,
                                    "proposed": proposed, "note": "合成模型说明：未采用的建议不能作为已保存结果。"})
        result["items"].append({"key": self.plan["units"][4]["key"], "status": "failed", "changed": False,
                                "note": "合成模型未完成本题；本题未写入，批次停止。"})
        return result

    def word_semantic_tag_run(self, plan_id, revision, *, confirmed, progress, cancelled):
        self.calls.append(("run", plan_id, revision, confirmed))
        progress({"message_zh": "合成服务正在返回结果；未调用真实模型。"})
        self.analysis = self.completed_result()
        return deepcopy(self.analysis)

    def word_semantic_tag_result(self, plan_id):
        if self.fail_result:
            raise RuntimeError("synthetic result failure")
        return deepcopy(self.analysis)

    def word_semantic_tag_apply(self, plan_id, keys):
        self.calls.append(("apply", plan_id, list(keys)))
        if self.fail_apply:
            raise RuntimeError("synthetic apply failure")
        return deepcopy(self.save_response) if self.save_response is not None else [{"key": key} for key in keys]

    def word_semantic_tag_discard(self, plan_id):
        self.calls.append(("discard", plan_id))


def loaded_dialog(*, recheck=True):
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_semantic_tags_dialog import WordSemanticTagsDialog
    facade, tasks = SyntheticFacade(), SyntheticTasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [{"key": "synthetic-q0", "revision": "synthetic-question-r1"}])
    tasks.finish()
    dialog.recheck.setChecked(recheck)
    dialog._prepare()
    tasks.finish()
    return dialog, facade, tasks


def capture(output):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import WORKBENCH_STYLE, install_font_fallbacks

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(WORKBENCH_STYLE)
    install_font_fallbacks()
    captures = []
    QMessageBox.question = lambda *_: QMessageBox.StandardButton.Yes

    def save(dialog, name, width, height, focus):
        dialog.resize(width, height)
        dialog.show()
        app.processEvents()
        dialog.body_scroll.ensureWidgetVisible(focus)
        app.processEvents()
        assert (dialog.width(), dialog.height()) == (width, height)
        assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
        assert dialog.tabs.widget(0).horizontalScrollBar().maximum() == 0
        assert dialog.tags.horizontalScrollBar().maximum() == 0
        index = dialog.questions.currentRow()
        unit = dialog.plan["units"][index]
        identity = f"本次第{index + 1}题 · {unit['source_name']}"
        assert dialog.questions.item(index).text().startswith(identity + " · ")
        assert dialog.paper_layout.itemAt(0).widget().text() == identity
        assert identity in dialog.tags.toPlainText()
        for button in (dialog.run_button, dialog.stop, dialog.apply_button, dialog.close_button):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
            assert not button.visibleRegion().isEmpty()
            assert button.width() >= button.minimumSizeHint().width()
        image = dialog.grab()
        path = output / name
        assert image.save(str(path), "PNG")
        captures.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "logical_width": width, "logical_height": height, "pixel_width": image.width(),
                         "pixel_height": image.height(), "device_pixel_ratio": image.devicePixelRatio(),
                         "status": dialog.status.text(), "status_role": dialog.status.objectName(),
                         "question_identity": identity,
                         "summary": dialog.selection_summary.text(), "footer_reachable": True,
                         "horizontal_overflow": False, "save_enabled": dialog.apply_button.isEnabled()})

    def close(dialog):
        dialog.analysis_result = None
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()

    for width, height in ((900, 820), (420, 720), (360, 520)):
        dialog, _facade, tasks = loaded_dialog()
        dialog.allow_send.setChecked(True)
        dialog._run()
        tasks.finish()
        dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.questions.item(1).setCheckState(Qt.CheckState.Checked)
        save(dialog, f"comparison-{width}.png", width, height, dialog.tabs)
        if width == 360:
            save(dialog, "selection-summary-360.png", width, height, dialog.selection_summary)
            dialog.questions.setCurrentRow(3)
            save(dialog, "protected-360.png", width, height, dialog.tabs)
        close(dialog)

    dialog, facade, tasks = loaded_dialog()
    dialog.allow_send.setChecked(True)
    dialog._run()
    save(dialog, "processing-360.png", 360, 520, dialog.status)
    dialog._stop()
    save(dialog, "stopping-360.png", 360, 520, dialog.status)
    facade.analysis = facade.completed_result()
    facade.analysis["items"] = facade.analysis["items"][:1]
    tasks.finish(invoke=False)
    save(dialog, "stopped-360.png", 360, 520, dialog.status)
    close(dialog)

    dialog, facade, tasks = loaded_dialog()
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.finish()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    facade.fail_apply = True
    dialog._apply()
    tasks.finish()
    save(dialog, "save-error-420.png", 420, 600, dialog.status)
    close(dialog)
    report = {"synthetic_only": True, "provider_requests": 0, "personal_db_operations": 0,
              "source_documents_read": 0, "captures": captures}
    (output / "capture-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"captures": len(captures), "output": str(output)}, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scale", choices=("1", "2"), default="2")
    arguments = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = arguments.scale
    capture(arguments.output)
