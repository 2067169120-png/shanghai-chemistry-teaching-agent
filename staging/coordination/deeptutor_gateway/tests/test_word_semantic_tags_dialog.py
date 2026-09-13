"""Actual native widgets; synthetic questions/providers, no external requests."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox
from test_word_question_dialog import _isolated_qt_app, _question, _Tasks

from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    suggest_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_semantic_tags_dialog import (
    WordSemanticTagsDialog,
)


class Tasks(_Tasks):
    def submit_progress(self, label, operation, *, on_success, on_failure, on_progress):
        return self.submit(
            label,
            lambda: operation(on_progress, lambda: bool(self.cancelled)),
            on_success=on_success,
            on_failure=on_failure,
        )


class Facade:
    def __init__(self):
        self.calls = []
        self.profiles = tuple(
            ProviderProfileSummary(
                "synthetic-" + str(i),
                "合成演示模型",
                "https://example.invalid/v1",
                "synthetic-model",
                "responses",
                ("text", "structured_output"),
                True,
                "profile-r1",
            )
            for i in range(2)
        )
        image = QImage(640, 160, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.lightGray)
        pixels = QByteArray()
        buffer = QBuffer(pixels)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        self.pixels = bytes(pixels)
        self.plan = {
            "plan_id": "synthetic-plan",
            "revision": "plan-r1",
            "model_label": "合成演示模型 / synthetic-model",
            "request_count": 2,
            "request_policy": {"max_output_tokens": 32000, "timeout_seconds": 300},
            "units": [],
        }
        for i in range(3):
            row = _question("Q" + str(i), "A")
            row["source_sha256"] = "a" * 64
            row["question_blocks"][0]["text"] = (
                "合成题面：判断金属钠的性质。A. 示例选项；B. 示例选项。"
            )
            attributes = suggest_attributes(row, {})
            self.plan["units"].append(
                {
                    "key": row["key"],
                    "source_name": "合成预览材料.docx",
                    "status": "ready" if i < 2 else "blocked",
                    "reason": "原图待核对，本题不发送" if i == 2 else "",
                    "images": [{"sha256": "b" * 64}] if i == 0 else [],
                    "input": {
                        "blocks": [
                            {
                                "index": 1,
                                "kind": "shared_context",
                                "text": "公共材料：以下各问共用这段材料。",
                                "image_sha256s": [],
                            },
                            {
                                "index": 2,
                                "kind": "question_text",
                                "text": "合成题面：判断金属钠的性质。A. 示例选项；B. 示例选项。",
                                "image_sha256s": ["b" * 64] if i == 0 else [],
                            },
                        ]
                    },
                    "attributes": attributes,
                }
            )
        self.analysis = {"plan_id": "synthetic-plan", "finished": False, "items": []}

    def preparation_profiles(self):
        return self.profiles

    def word_semantic_tag_preview(self, selections, profile_id, revision):
        self.calls.append(("preview", deepcopy(selections), profile_id, revision))
        return deepcopy(self.plan)

    def word_semantic_tag_image(self, plan_id, sha):
        assert plan_id == "synthetic-plan" and sha == "b" * 64
        return self.pixels

    def word_semantic_tag_run(
        self, plan_id, revision, *, confirmed, progress, cancelled
    ):
        self.calls.append(("run", plan_id, revision, confirmed))
        progress({"message_zh": "合成测试正在返回建议"})
        self.analysis["finished"] = True
        for unit in self.plan["units"][:2]:
            proposed = deepcopy(unit["attributes"])
            proposed["primary_knowledge"].update(
                id="K05",
                label="常见的金属及其化合物",
                evidence=[
                    {"kind": "question_text", "quote": "金属钠", "block_index": 2}
                ],
            )
            self.analysis["items"].append(
                {
                    "key": unit["key"],
                    "status": "ready",
                    "changed": True,
                    "proposed": proposed,
                    "note": "依据题面提问提供的合成建议。",
                }
            )
        return deepcopy(self.analysis)

    def word_semantic_tag_result(self, plan_id):
        return deepcopy(self.analysis)

    def word_semantic_tag_apply(self, plan_id, keys):
        self.calls.append(("apply", plan_id, keys))
        return [{"key": k} for k in keys]

    def word_semantic_tag_discard(self, plan_id):
        self.calls.append(("discard", plan_id))


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        install_font_fallbacks()
        yield app


def loaded():
    facade, tasks = Facade(), Tasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [{"key": "Q0", "revision": "r1"}])
    assert not dialog.prepare.isEnabled()
    tasks.flush()
    assert dialog.prepare.isEnabled()
    dialog._prepare()
    tasks.flush()
    return dialog, facade, tasks


def test_full_question_images_consent_then_selected_save(qt_app):
    dialog, facade, tasks = loaded()
    assert [c[0] for c in facade.calls] == ["preview"]
    labels = dialog.paper.findChildren(QLabel)
    assert any("公共材料" in label.text() for label in labels)
    assert any("A. 示例选项" in label.text() for label in labels)
    assert any(not label.pixmap().isNull() for label in labels)
    assert "2次" in dialog.disclosure.text() and "1题不发送" in dialog.disclosure.text()
    assert "32000 token" in dialog.disclosure.text()
    assert "300秒" in dialog.disclosure.text()
    assert "部分模型包含推理" in dialog.disclosure.text()
    assert not dialog.run_button.isEnabled()
    dialog._run()
    assert not tasks.pending
    dialog.allow_send.setChecked(True)
    assert dialog.run_button.isEnabled()
    dialog._run()
    assert not dialog.close_button.isEnabled()
    dialog.reject()
    assert dialog.plan is not None and "任务执行中" in dialog.status.text()
    tasks.flush()
    assert (
        dialog.result() == QDialog.DialogCode.Rejected
    )  # Do not shadow QDialog.result().
    assert not any(c[0] == "apply" for c in facade.calls)
    assert "AI建议" in dialog.tags.toPlainText()
    assert "本次补全：主考点" in dialog.tags.toPlainText()
    assert "模型原始说明（可能包含未采用的建议）" in dialog.tags.toPlainText()
    assert "依据：金属钠" in dialog.tags.toPlainText()
    assert dialog.apply_button.isEnabled() and not dialog.run_button.isEnabled()
    dialog.questions.item(1).setCheckState(Qt.CheckState.Unchecked)
    dialog._apply()
    tasks.flush()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert ("apply", "synthetic-plan", ["Q0"]) in facade.calls
    assert facade.calls[-1] == ("discard", "synthetic-plan")


def test_model_change_discards_previous_preview_and_consent(qt_app):
    dialog, facade, tasks = loaded()
    dialog.allow_send.setChecked(True)
    dialog.profile.setCurrentIndex(1)
    assert dialog.plan is None and not dialog.allow_send.isChecked()
    assert not dialog.run_button.isEnabled() and dialog.questions.count() == 0
    assert facade.calls[-1] == ("discard", "synthetic-plan")
    dialog._prepare()
    tasks.flush()
    assert facade.calls[-1][2] == "synthetic-1"
    dialog.reject()


def test_broken_image_never_falls_back_to_text_only(qt_app):
    facade, tasks = Facade(), Tasks()
    facade.pixels = b"invalid-image"
    dialog = WordSemanticTagsDialog(facade, tasks, [])
    tasks.flush()
    dialog._prepare()
    tasks.flush()
    assert dialog._image_preview_failed
    dialog.allow_send.setChecked(True)
    assert not dialog.run_button.isEnabled()
    dialog._run()
    assert not tasks.pending and not any(c[0] == "run" for c in facade.calls)
    dialog.reject()


def test_cancel_recovers_completed_candidates_from_service_cache(qt_app):
    dialog, facade, tasks = loaded()
    dialog.allow_send.setChecked(True)
    dialog._run()
    task_id = dialog._job
    # Model service completed one item just before cancellation; bridge drops its return.
    facade.word_semantic_tag_run(
        "synthetic-plan",
        "plan-r1",
        confirmed=True,
        progress=lambda *_: None,
        cancelled=lambda: False,
    )
    facade.analysis["items"] = facade.analysis["items"][:1]
    tasks.cancel(task_id)
    tasks.finish()
    assert dialog.analysis_result["finished"]
    assert len(dialog.analysis_result["items"]) == 1 and dialog.apply_button.isEnabled()
    assert "尚未分析" in dialog.questions.item(1).text()
    assert not any(c[0] == "apply" for c in facade.calls)
    dialog.reject()


def test_unsaved_suggestions_close_requires_explicit_discard(qt_app, monkeypatch):
    dialog, facade, tasks = loaded()
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.flush()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No
    )
    dialog.reject()
    assert dialog.plan is not None and not any(c[0] == "discard" for c in facade.calls)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    dialog.reject()
    assert dialog.plan is None and not any(c[0] == "apply" for c in facade.calls)


@pytest.mark.parametrize("width", [420, 900])
def test_paper_preview_and_actions_fit_supported_width(qt_app, width):
    dialog, _facade, _tasks = loaded()
    dialog.resize(width, 840)
    dialog.show()
    qt_app.processEvents()
    assert dialog.width() == width
    paper_labels = dialog.paper.findChildren(QLabel)
    assert any(
        "A. 示例选项" in label.text() and label.isVisible() for label in paper_labels
    )
    assert any(
        not label.pixmap().isNull() and label.isVisible() for label in paper_labels
    )
    before_labels = list(dialog._image_labels)
    assert all(
        label.height() >= label.pixmap().height() > 0 for label, _ in before_labels
    )
    dialog.resize(width, 850)
    qt_app.processEvents()
    assert (
        dialog._image_labels == before_labels
    )  # Resizing must not recreate/hide the question.
    scroll = dialog.tabs.widget(0)
    assert scroll.horizontalScrollBar().maximum() == 0
    for control in (
        dialog.run_button,
        dialog.apply_button,
        dialog.close_button,
        dialog.profile,
    ):
        point = control.mapTo(dialog, control.rect().topLeft())
        assert point.x() >= 0 and point.x() + control.width() <= dialog.width()
    dialog.reject()


def test_no_saved_eligible_model_shows_settings_guidance(qt_app):
    facade, tasks = Facade(), Tasks()
    facade.profiles = ()
    dialog = WordSemanticTagsDialog(facade, tasks, [])
    tasks.flush()
    assert not dialog.prepare.isEnabled()
    assert "模型设置" in dialog.status.text()
    dialog.reject()
