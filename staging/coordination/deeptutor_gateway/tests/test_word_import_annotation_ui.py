"""A saved Word batch remains readable even if automatic labels fail."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from test_desktop_import_preview import _files
from test_desktop_visual_import_facade import FakeProviderStore, _facade
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)
from test_desktop_visual_import_ui import _Facade, _manual_task_bridge, _receipt

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    previous = set(app.topLevelWidgets())
    yield app
    for widget in set(app.topLevelWidgets()) - previous:
        if isValid(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class _LabelsFacade(_Facade):
    def __init__(self):
        super().__init__()
        self.annotation_calls = []
        self.annotation_error = False
        self.result_patch = {}

    def annotate_imported_word_batch(self, batch_id):
        self.annotation_calls.append(batch_id)
        if self.annotation_error:
            raise ValueError("internal-error-detail-must-not-leak")
        return {
            "batch_id": batch_id,
            "method": "local_rules",
            "model_invoked": False,
            "status": "completed",
            "question_count": 2,
            "valid_label_counts": {"source_bound_questions": 2},
            **self.result_patch,
        }


def test_save_finished_then_annotations_start_without_losing_new_task(qt_app):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    dialog._active_task_id = "save-task"
    dialog._active_task_kind = "commit"
    dialog._import_preview_session = {"preview_id": "old-preview"}
    receipt = _receipt()
    dialog._visual_batch_saved(receipt)
    assert not tasks.pending
    assert facade.annotation_calls == []
    tasks.task_finished.emit("unrelated")
    assert not tasks.pending
    tasks.task_finished.emit("save-task")
    assert facade.discard_calls == ["old-preview"]
    assert dialog._active_task_kind == "word_labels"
    assert dialog._active_task_id == tasks.pending[0].task_id
    assert not dialog.word_questions_button.isEnabled()
    tasks.task_finished.emit("save-task")
    assert dialog._active_task_id is not None
    tasks.finish_next()
    assert facade.annotation_calls == [receipt.batch_id]
    assert dialog._active_task_id is None
    assert dialog.word_questions_button.isEnabled()
    assert "2 道题" in dialog.word_annotation_status.text()
    assert "本地规则建议" in dialog.word_annotation_status.text()
    assert facade.run_calls == []


def test_label_failure_keeps_word_access_and_explicit_retry(qt_app):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    facade.annotation_error = True
    dialog = ImportDialog(facade, tasks)
    dialog._visual_batch_saved(_receipt())
    tasks.finish_next()
    assert "原件已保存" in dialog.status.text()
    assert "internal-error-detail" not in dialog.status.text()
    assert dialog.word_questions_button.isEnabled()
    assert dialog.word_annotation_button.isEnabled()
    facade.annotation_error = False
    dialog.word_annotation_button.click()
    tasks.finish_next()
    assert len(facade.annotation_calls) == 2
    assert "2 道题" in dialog.status.text()


def test_labels_are_not_written_when_only_opening_import_history(qt_app):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    dialog._show_saved_receipt(_receipt(), resumed=True)
    assert not tasks.pending
    assert not facade.annotation_calls


def test_image_only_batch_does_not_start_word_annotations(qt_app):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    receipt = _receipt()
    receipt = replace(receipt, sources=receipt.sources[:3], source_count=3, native_quick_count=0)
    dialog._visual_batch_saved(receipt)
    assert not tasks.pending
    assert not facade.annotation_calls
    assert not dialog.provider_card.isHidden()


@pytest.mark.parametrize("patch", [
    {"batch_id": "other-batch"},
    {"model_invoked": True},
    {"method": "unspecified"},
    {"question_count": True},
    {"question_count": -1},
    {"status": "not_completed"},
    {"valid_label_counts": None},
    {"valid_label_counts": {"source_bound_questions": 3}},
])
def test_invalid_receipt_never_claims_label_completion(qt_app, patch):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    facade.result_patch = patch
    dialog = ImportDialog(facade, tasks)
    dialog._visual_batch_saved(_receipt())
    tasks.finish_next()
    assert "标签未完成" in dialog.status.text()
    assert dialog.word_questions_button.isEnabled()


def test_no_questions_preserves_original_word_access(qt_app):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    facade.result_patch = {
        "status": "no_questions", "question_count": 0,
        "valid_label_counts": {"source_bound_questions": 0},
    }
    dialog = ImportDialog(facade, tasks)
    dialog._visual_batch_saved(_receipt())
    tasks.finish_next()
    assert "未识别到独立题目" in dialog.status.text()
    assert dialog.word_reference_button.isEnabled()


def test_failed_task_submission_keeps_import_success(qt_app, monkeypatch):
    facade, tasks = _LabelsFacade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    def fail(*_args, **_kwargs):
        raise RuntimeError("closed fake bridge")
    monkeypatch.setattr(tasks, "submit_progress", fail)
    dialog._visual_batch_saved(_receipt())
    assert dialog._active_task_id is None
    assert "原件已保存" in dialog.status.text()
    assert dialog.word_questions_button.isEnabled()


def test_facade_annotation_forwards_only_the_explicit_batch():
    facade = object.__new__(DesktopWorkbenchFacade)
    seen = []
    class Service:
        def annotate_imported_batch(self, batch_id):
            seen.append(batch_id)
            return {"batch_id": batch_id}
    facade._word_question_service = Service()
    assert facade.annotate_imported_word_batch("selected-batch") == {"batch_id": "selected-batch"}
    assert seen == ["selected-batch"]


def test_real_saved_word_then_ui_auto_labels_persist_without_provider(
    qt_app, desktop_paths, tmp_path, monkeypatch
):
    from integrations.deeptutor_shchem_v1 import desktop_word_questions as module

    monkeypatch.setattr(module, "load_attribute_catalog", lambda _root: {
        "knowledge_points": [], "nodes": [],
    })
    word, _, _ = _files(tmp_path)
    source_bytes = word.read_bytes()
    provider = FakeProviderStore(configured=False)
    facade = _facade(desktop_paths, provider)
    preview = facade.preview_import_files(handout_files=(word,), source_type="合成讲义")
    receipt = facade.commit_import_preview(
        preview["preview_id"], preview["revision"], [preview["sources"][0]["source_id"]]
    )
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    dialog._visual_batch_saved(receipt)
    tasks.finish_next()
    assert "1 道题" in dialog.status.text()
    first = facade.word_question_catalog()["items"][0]
    assert first["attributes"]["annotation_source"] == "auto_suggested"
    assert first["attributes"]["question_revision"] == first["revision"]
    assert first["attributes"]["original_source"]["exam_type"]["value"] == "unknown"
    assert provider.borrow_calls == 0
    assert word.read_bytes() == source_bytes
    reopened = _facade(desktop_paths, provider)
    assert reopened.word_question_catalog()["items"][0]["attributes"] == first["attributes"]


def test_word_image_handoff_copy_does_not_deny_later_visual_generation():
    import inspect

    from integrations.deeptutor_shchem_v1.desktop_workbench import word_question_dialog

    source = inspect.getsource(word_question_dialog.WordQuestionDialog)
    assert "不会看到图片像素" not in source
    assert source.count("后续是否发送图片像素") == 2
    assert source.count("由生成时的读图模式及发送预览决定") == 2
