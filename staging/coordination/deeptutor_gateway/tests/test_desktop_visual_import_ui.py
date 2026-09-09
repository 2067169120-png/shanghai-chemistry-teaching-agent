from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopVisualImportReceipt,
    DesktopVisualImportSourceSummary,
    ProviderProfileSummary,
)


def _qt_available() -> bool:
    return importlib.util.find_spec("PySide6") is not None


@pytest.fixture
def qt_app() -> Any:
    if not _qt_available():
        pytest.skip("PySide6 is available only in the desktop runtime")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _settle(app: Any, rounds: int = 5) -> None:
    for _ in range(rounds):
        app.processEvents()


def _receipt(
    *,
    batch_id: str = "BATCH-INTERNAL-DO-NOT-SHOW",
    status: str = "pending",
    visual_status: str = "awaiting_visual_provider",
    visual_queue_count: int = 2,
) -> DesktopVisualImportReceipt:
    sources = (
        DesktopVisualImportSourceSummary(
            "question", 1, "q1.png", "visual_only_required"
        ),
        DesktopVisualImportSourceSummary(
            "question", 2, "q2.pdf", "visual_only_required"
        ),
        DesktopVisualImportSourceSummary("answer", 1, "a1.png", "visual_only_required"),
        DesktopVisualImportSourceSummary(
            "handout", 1, "h1.docx", "native_text_complete"
        ),
    )
    return DesktopVisualImportReceipt(
        batch_id=batch_id,
        source_type="上海教师资料",
        status=status,
        visual_status=visual_status,
        source_count=4,
        native_quick_count=1,
        visual_queue_count=visual_queue_count,
        sources=sources,
        message_zh="候选状态",
    )


def _visual_profile(
    *,
    profile_id: str = "PROFILE-INTERNAL-DO-NOT-SHOW",
    revision: str = "REVISION-INTERNAL-DO-NOT-SHOW",
    key_saved: bool = True,
    capabilities: tuple[str, ...] = ("text", "vision", "structured_output"),
) -> ProviderProfileSummary:
    return ProviderProfileSummary(
        profile_id=profile_id,
        provider_name="教师视觉服务",
        base_url="https://example.invalid/v1",
        model_id="chem-vision",
        api_style="responses",
        capabilities=capabilities,
        key_saved=key_saved,
        revision=revision,
    )


class _Facade:
    def __init__(
        self,
        *,
        profiles: tuple[ProviderProfileSummary, ...] = (),
        resumable: tuple[DesktopVisualImportReceipt, ...] = (),
    ) -> None:
        self.profiles = profiles
        self.resumable = resumable
        self.save_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []
        self.preview_calls = []
        self.commit_calls = []
        self.discard_calls = []

    def preview_import_files(self, **kwargs):
        self.preview_calls.append(kwargs)
        sources = []
        for role, field in (
            ("question", "question_files"),
            ("answer", "answer_files"),
            ("handout", "handout_files"),
        ):
            for index, path in enumerate(kwargs[field], 1):
                sources.append(
                    {
                        "source_id": f"{role}-{index}",
                        "source_name": Path(path).name,
                        "role": role,
                        "kind": "docx" if str(path).endswith(".docx") else "image",
                        "source_sha256": "a" * 64,
                        "order_index": index,
                    }
                )
        return {
            "preview_id": "preview",
            "revision": "revision",
            "sources": sources,
            "warnings": [],
        }

    def commit_import_preview(self, preview_id, revision, selected, **kwargs):
        self.commit_calls.append((preview_id, revision, selected))
        return self.save_visual_import_batch(**self.preview_calls[-1], **kwargs)

    def discard_import_preview(self, preview_id):
        self.discard_calls.append(preview_id)

    def teaching_pack_analysis_files(self):
        return tuple(f"C:/demo/PKG-{i:03d}/解析版.docx" for i in range(1, 99))

    def list_provider_profiles(self) -> tuple[ProviderProfileSummary, ...]:
        return self.profiles

    def list_resumable_visual_import_batches(
        self,
    ) -> tuple[DesktopVisualImportReceipt, ...]:
        return self.resumable

    def save_visual_import_batch(self, **kwargs: Any) -> DesktopVisualImportReceipt:
        callback = kwargs["progress_callback"]
        callback({"stage": "planned", "source_count": 4})
        callback({"stage": "visual_pages"})
        callback({"stage": "completed"})
        self.save_calls.append(dict(kwargs))
        return _receipt()

    def run_saved_visual_import_batch(
        self, **kwargs: Any
    ) -> DesktopVisualImportReceipt:
        callback = kwargs["progress_callback"]
        callback({"stage": "planned"})
        callback({"stage": "visual_pages"})
        callback({"stage": "completed"})
        self.run_calls.append(dict(kwargs))
        return _receipt(
            batch_id=str(kwargs["batch_id"]),
            status="candidate_ready_for_review",
            visual_status="completed",
        )

    def run_one_round_review_corpus_import(self, **_kwargs: Any) -> dict[str, int]:
        return {
            "documents_completed": 196,
            "quick_import_candidates": 120,
            "visual_completion_candidates": 76,
            "paired_question_candidates": 100,
            "documents_failed": 0,
        }


@dataclass
class _PendingTask:
    task_id: str
    label: str
    operation: Any
    on_progress: Any
    on_success: Any
    on_failure: Any


def _manual_task_bridge() -> Any:
    from PySide6.QtCore import QObject, Signal

    class ManualTaskBridge(QObject):
        task_finished = Signal(str)
        task_cancelled = Signal(str, str)

        def __init__(self) -> None:
            super().__init__()
            self.pending: list[_PendingTask] = []
            self.cancelled: set[str] = set()
            self.serial = 0

        def submit_progress(
            self,
            label: str,
            operation: Any,
            *,
            on_progress: Any = None,
            on_success: Any = None,
            on_failure: Any = None,
        ) -> str:
            self.serial += 1
            task_id = f"task-{self.serial}"
            self.pending.append(
                _PendingTask(
                    task_id,
                    label,
                    operation,
                    on_progress,
                    on_success,
                    on_failure,
                )
            )
            return task_id

        def cancel(self, task_id: str) -> None:
            self.cancelled.add(task_id)

        def finish_next(self) -> None:
            pending = self.pending.pop(0)
            if pending.task_id in self.cancelled:
                self.task_cancelled.emit(pending.task_id, pending.label)
                self.task_finished.emit(pending.task_id)
                return
            try:
                result = pending.operation(
                    pending.on_progress or (lambda _value: None),
                    lambda: pending.task_id in self.cancelled,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                if pending.on_failure is not None:
                    pending.on_failure(str(exc))
            else:
                if pending.on_success is not None:
                    pending.on_success(result)
            self.task_finished.emit(pending.task_id)

    return ManualTaskBridge()


def _visible_text(dialog: Any) -> str:
    from PySide6.QtWidgets import QLabel, QPushButton

    labels = [item.text() for item in dialog.findChildren(QLabel)]
    buttons = [item.text() for item in dialog.findChildren(QPushButton)]
    combo_items = [
        dialog.provider_combo.itemText(index)
        for index in range(dialog.provider_combo.count())
    ]
    return "\n".join(labels + buttons + combo_items)


def test_three_role_lists_keep_order_filter_formats_and_save_offline_args(
    qt_app: Any, tmp_path: Path, monkeypatch
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    facade = _Facade(profiles=(_visual_profile(),))
    _preview_choice(monkeypatch)
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)  # type: ignore[arg-type]
    files: dict[str, Path] = {}
    for name in (
        "q1.png",
        "q2.pdf",
        "q3.webp",
        "a1.jpeg",
        "h1.docx",
        "unsupported.pptx",
        "unsupported.heic",
    ):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        files[name] = path

    dialog.question_files.file_list._append_paths(
        [str(files["q1.png"]), str(files["q2.pdf"]), str(files["q3.webp"])]
    )
    dialog.answer_files.file_list._append_paths(
        [str(files["a1.jpeg"]), str(files["unsupported.pptx"])]
    )
    dialog.handout_files.file_list._append_paths(
        [str(files["h1.docx"]), str(files["unsupported.heic"])]
    )
    dialog.question_files.file_list.item(1).setSelected(True)
    dialog.question_files.file_list.move_selected_paths(-1)

    assert [Path(path).name for path in dialog.question_files.paths()] == [
        "q2.pdf",
        "q1.png",
        "q3.webp",
    ]
    assert [Path(path).name for path in dialog.answer_files.paths()] == ["a1.jpeg"]
    assert [Path(path).name for path in dialog.handout_files.paths()] == ["h1.docx"]

    dialog._save()
    assert facade.run_calls == []
    tasks.finish_next()
    assert not facade.save_calls
    assert len(facade.preview_calls) == 1
    assert not dialog.cancel_button.isEnabled()
    tasks.finish_next()
    _settle(qt_app)

    assert len(facade.save_calls) == 1
    call = facade.save_calls[0]
    assert tuple(Path(path).name for path in call["question_files"]) == (
        "q2.pdf",
        "q1.png",
        "q3.webp",
    )
    assert tuple(Path(path).name for path in call["answer_files"]) == ("a1.jpeg",)
    assert tuple(Path(path).name for path in call["handout_files"]) == ("h1.docx",)
    assert call["source_type"] == "试卷与答案"
    assert callable(call["progress_callback"])
    assert callable(call["should_cancel"])
    assert "离线保存完成" in dialog.status.text()
    assert "可完整读取的 Word 来源 1 份" in dialog.status.text()
    assert not dialog.word_reference_button.isHidden()
    assert "待视觉资料 2" in dialog.status.text()
    assert dialog.provider_card.isVisible() is False  # dialog itself was never shown


def _preview_choice(monkeypatch, *, selected=None, accepted=True, during=None):
    from PySide6.QtWidgets import QDialog

    import integrations.deeptutor_shchem_v1.desktop_workbench.import_preview_dialog as module

    class Choice:
        def __init__(self, _facade, _tasks, preview, parent):
            self.selected_source_ids = (
                selected
                if selected is not None
                else [source["source_id"] for source in preview["sources"]]
            )
            self.parent = parent

        def exec(self):
            if during:
                during(self.parent)
            return (
                QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
            )

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "ImportPreviewDialog", Choice)


def test_cancel_preimport_has_zero_saved_candidates(qt_app, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    _preview_choice(monkeypatch, accepted=False)
    facade, tasks = _Facade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    source = tmp_path / "demo.docx"
    source.write_bytes(b"fake")
    dialog.handout_files.file_list._append_paths([str(source)])
    dialog._save()
    assert not facade.preview_calls and not facade.save_calls
    tasks.finish_next()
    assert not facade.save_calls and not facade.commit_calls and not tasks.pending
    assert facade.discard_calls == ["preview"]
    assert "未保存" in dialog.status.text()
    dialog.close()


def test_preimport_commit_receives_only_explicit_file_selection(
    qt_app, tmp_path, monkeypatch
):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    _preview_choice(monkeypatch, selected=["handout-2"])
    facade, tasks = _Facade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    paths = [tmp_path / name for name in ("first.docx", "second.docx")]
    for path in paths:
        path.write_bytes(b"fixture")
    dialog.handout_files.file_list._append_paths([str(path) for path in paths])
    dialog._save()
    tasks.finish_next()
    dialog._cancel_active()
    assert not tasks.cancelled and not dialog.close_button.isEnabled()
    tasks.finish_next()
    assert facade.commit_calls[0][2] == ["handout-2"]
    assert facade.discard_calls == ["preview"]
    dialog.close()


def test_98_analysis_entry_only_previews_and_does_not_run_196_import(
    qt_app, monkeypatch
):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    _preview_choice(monkeypatch, accepted=False)
    facade, tasks = _Facade(), _manual_task_bridge()
    facade.run_one_round_review_corpus_import = lambda **_kwargs: pytest.fail(
        "old 196-file import must never run"
    )
    dialog = ImportDialog(facade, tasks)
    dialog.corpus_button.click()
    tasks.finish_next()
    assert len(facade.preview_calls[0]["handout_files"]) == 98
    assert not facade.preview_calls[0]["question_files"]
    assert not facade.save_calls and not facade.commit_calls
    dialog.close()


def test_input_change_invalidates_preimport_confirmation(qt_app, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    _preview_choice(
        monkeypatch, during=lambda parent: parent.source_type.setCurrentText("教师讲义")
    )
    facade, tasks = _Facade(), _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)
    path = tmp_path / "first.docx"
    path.write_bytes(b"fixture")
    dialog.handout_files.file_list._append_paths([str(path)])
    dialog._save()
    tasks.finish_next()
    assert not facade.commit_calls and not tasks.pending
    assert facade.discard_calls == ["preview"]
    dialog.close()


def test_visual_confirmation_no_does_not_call_model_and_yes_binds_batch_revision_once(
    qt_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    receipt = _receipt()
    profile = _visual_profile()
    facade = _Facade(profiles=(profile,))
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)  # type: ignore[arg-type]
    dialog._saved_visual_receipt = receipt
    dialog._show_saved_receipt(receipt)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    dialog._run_visual()
    assert facade.run_calls == []
    assert tasks.pending == []
    assert "已取消发送" in dialog.status.text()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._run_visual()
    assert len(tasks.pending) == 1
    tasks.finish_next()
    _settle(qt_app)

    assert len(facade.run_calls) == 1
    call = facade.run_calls[0]
    assert call["batch_id"] == receipt.batch_id
    assert call["profile_id"] == profile.profile_id
    assert call["expected_profile_revision"] == profile.revision
    assert call["teacher_confirmed"] is True
    assert "候选已生成，待教师逐页复核" in dialog.status.text()
    assert "导入完成" not in dialog.status.text()
    assert "正式入库" not in dialog.status.text()


def test_model_filter_empty_hint_and_internal_values_never_rendered(
    qt_app: Any,
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    receipt = _receipt()
    invalid_profiles = (
        _visual_profile(profile_id="NO-KEY", key_saved=False),
        _visual_profile(profile_id="NO-STRUCTURED", capabilities=("text", "vision")),
    )
    facade = _Facade(profiles=invalid_profiles)
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)  # type: ignore[arg-type]
    dialog._saved_visual_receipt = receipt
    dialog._show_saved_receipt(receipt)
    _settle(qt_app)

    assert dialog.provider_combo.count() == 0
    assert not dialog.generate_button.isEnabled()
    assert "设置" in dialog.provider_note.text()
    assert "离线候选不受影响" in dialog.provider_note.text()
    text = _visible_text(dialog)
    assert receipt.batch_id not in text
    assert "NO-KEY" not in text
    assert "NO-STRUCTURED" not in text


def test_resumable_batch_has_teacher_card_without_restoring_source_paths(
    qt_app: Any,
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    receipt = _receipt(status="failed", visual_status="failed")
    facade = _Facade(profiles=(_visual_profile(),), resumable=(receipt,))
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)  # type: ignore[arg-type]
    dialog.show()
    _settle(qt_app)

    assert dialog.resume_card.isVisible()
    assert "上次生成未完成" in dialog.resume_summary.text()
    assert receipt.batch_id not in dialog.resume_summary.text()
    assert all(panel.paths() == [] for panel in dialog._role_panels())
    dialog.resume_button.click()
    _settle(qt_app)
    assert dialog._saved_visual_receipt is receipt
    assert not dialog.resume_card.isVisible()
    assert dialog.provider_card.isVisible()
    assert all(panel.paths() == [] for panel in dialog._role_panels())
    dialog.close()


def test_active_visual_cancel_wording_close_guard_and_narrow_scroll(
    qt_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    facade = _Facade(profiles=(_visual_profile(),))
    tasks = _manual_task_bridge()
    dialog = ImportDialog(facade, tasks)  # type: ignore[arg-type]
    receipt = _receipt()
    dialog._saved_visual_receipt = receipt
    dialog._show_saved_receipt(receipt)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.show()
    dialog.resize(420, 680)
    _settle(qt_app)

    assert (
        dialog.scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
    assert dialog.question_files.file_list.accessibleName() == "题目页或试卷列表"
    assert dialog.answer_files.file_list.accessibleName() == "参考答案页列表"
    assert dialog.handout_files.file_list.accessibleName() == "教师讲义列表"

    dialog._run_visual()
    assert dialog._active_task_id is not None
    dialog.reject()
    assert dialog.isVisible()
    assert "等待当前页处理结束" in dialog.status.text()
    dialog.cancel_button.click()
    assert "将在当前页处理结束后停止" in dialog.status.text()
    tasks.finish_next()
    _settle(qt_app)
    assert facade.run_calls == []
    assert dialog._active_task_id is None
    dialog.close()
