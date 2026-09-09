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

        def submit_progress(
            self,
            label: str,
            operation: Any,
            *,
            on_progress: Any = None,
            on_success: Any = None,
            on_failure: Any = None,
        ) -> str:
            task_id = f"task-{len(self.pending) + 1}"
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
    qt_app: Any, tmp_path: Path
) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog

    facade = _Facade(profiles=(_visual_profile(),))
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
    assert "原生文字候选 1" in dialog.status.text()
    assert "待视觉资料 2" in dialog.status.text()
    assert dialog.provider_card.isVisible() is False  # dialog itself was never shown


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
