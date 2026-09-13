from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    return QApplication.instance() or QApplication([])


class _DeferredTasks(QObject):
    """A deterministic task bridge for exercising page atomicity."""

    task_finished = Signal(str)
    task_cancelled = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.pending: list[dict[str, Any]] = []
        self.cancelled: set[str] = set()
        self._serial = 0

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self._serial += 1
        task_id = f"personal-task-{self._serial}"
        self.pending.append(
            {
                "task_id": task_id,
                "label": label,
                "operation": operation,
                "on_success": on_success,
                "on_failure": on_failure,
            }
        )
        return task_id

    def submit_progress(
        self,
        label,
        operation,
        *,
        on_progress=None,
        on_success=None,
        on_failure=None,
    ):
        del on_progress
        return self.submit(label, operation, on_success=on_success, on_failure=on_failure)

    def cancel(self, task_id: str) -> None:
        self.cancelled.add(task_id)

    def finish(self, label: str | None = None) -> None:
        index = next(
            (
                index
                for index, task in enumerate(self.pending)
                if label is None or task["label"] == label
            ),
            None,
        )
        assert index is not None, (label, [task["label"] for task in self.pending])
        task = self.pending.pop(index)
        if task["task_id"] not in self.cancelled:
            try:
                value = task["operation"]()
            except Exception as exc:  # noqa: BLE001 - deliberately tests UI failure handling
                if task["on_failure"] is not None:
                    task["on_failure"](str(exc))
            else:
                if task["on_success"] is not None:
                    task["on_success"](value)
        self.task_finished.emit(task["task_id"])


def _asset(number: int) -> dict[str, Any]:
    sha = f"{number:064x}"
    return {
        "asset_id": "IMG-" + sha,
        "sha256": sha,
        "caption": f"个人题图 {number}",
        "source": "合成个人图片题来源",
        "purpose": "教师核对题干、公共材料和化学图形",
        "width": 120,
        "height": 90,
        "content_type": "image/png",
    }


def _reference(*assets: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_kind": "personal_visual",
        "materials": "【个人图片题完整主题】\n主题材料、全部小题与独立参考答案。\n"
        "待核对提醒：题面与原图须由教师复核。",
        "warnings": ["题面与原图须由教师复核"],
        "selections": [
            {
                "batch_id": "DESKTOPBATCH-" + "a" * 32,
                "key": "visual-question:q1",
                "revision": "revision-q1",
                "points": 2,
            }
        ],
        "include_images": True,
        "image_assets": list(assets),
        "image_issues": [],
        "theme_count": 1,
        "question_count": 2,
    }


class _PersonalPreparationFacade:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.personal_calls: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        self.word_calls: list[tuple[Any, ...]] = []
        self.provider_calls = 0

    def import_word_question_reference(self, *_args, **_kwargs):
        self.word_calls.append((_args, _kwargs))
        raise AssertionError("personal_visual reference must not use the Word import path")

    def import_personal_visual_question_reference(self, reference, existing_assets):
        self.personal_calls.append((deepcopy(reference), deepcopy(existing_assets)))
        if self.failure == "exception":
            raise RuntimeError("private-source-diagnostic")
        value = {
            "materials": reference["materials"],
            "warnings": list(reference["warnings"]),
            "image_assets": deepcopy(existing_assets) + deepcopy(reference["image_assets"]),
        }
        if self.failure == "materials":
            value["materials"] += "\n来源已变化"
        elif self.failure == "warnings":
            value["warnings"] = []
        elif self.failure == "images":
            value["image_assets"] = value["image_assets"][:-1]
        return value


def _preparation_page(existing: list[dict[str, Any]] | None = None):
    facade = _PersonalPreparationFacade()
    tasks = _DeferredTasks()
    page = PreparationPage(facade, tasks)
    page._availability_timer.stop()
    page.topic.setText("原课题")
    page.audience.setText("高二")
    page.objective.setPlainText("原目标")
    page.materials.setPlainText("原资料\n保留原表单")
    page.image_assets_widget.set_assets_strict(list(existing or []))
    return page, facade, tasks


def test_facade_personal_visual_api_delegates_to_service(monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_personal_visual_questions as service_module

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class RecordingService:
        def __init__(self, facade):
            calls.append(("init", (facade,), {}))

        def catalog(self, batch_id=None):
            calls.append(("catalog", (batch_id,), {}))
            return {"method": "catalog"}

        def detail(self, batch_id, key, revision):
            calls.append(("detail", (batch_id, key, revision), {}))
            return {"method": "detail"}

        def image(self, batch_id, key, revision, image_id, original=False):
            calls.append(("image", (batch_id, key, revision, image_id), {"original": original}))
            return {"method": "image"}

        def save_selection(self, selections):
            calls.append(("save_selection", (selections,), {}))
            return {"method": "save_selection"}

        def reference(self, selections):
            calls.append(("reference", (selections,), {}))
            return {"method": "reference"}

        def import_reference(self, reference, existing_assets):
            calls.append(("import_reference", (reference, existing_assets), {}))
            return {"method": "import_reference"}

    monkeypatch.setattr(service_module, "PersonalVisualQuestionService", RecordingService)
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    selections = [{"batch_id": "B", "key": "K", "revision": "R", "points": 2}]
    reference = _reference(_asset(1))

    assert facade.personal_visual_questions() == {"method": "catalog"}
    assert facade.personal_visual_questions("B") == {"method": "catalog"}
    assert facade.personal_visual_question_detail("B", "K", "R") == {"method": "detail"}
    assert facade.personal_visual_question_image(
        "B", "K", "R", "I", original=True
    ) == {"method": "image"}
    assert facade.save_personal_visual_selection(selections) == {"method": "save_selection"}
    assert facade.personal_visual_question_reference(selections) == {"method": "reference"}
    assert facade.import_personal_visual_question_reference(reference, [_asset(2)]) == {
        "method": "import_reference"
    }

    forwarded = [entry for entry in calls if entry[0] != "init"]
    methods = [entry[0] for entry in forwarded]
    assert methods == [
        "catalog",
        "catalog",
        "detail",
        "image",
        "save_selection",
        "reference",
        "import_reference",
    ]
    assert forwarded[0] == ("catalog", (None,), {})
    assert forwarded[3] == ("image", ("B", "K", "R", "I"), {"original": True})


@pytest.mark.parametrize(
    ("service_error", "expected_code", "expected_message"),
    [
        ("domain", "personal_visual_question_invalid", "图片题版本已变化，请重新核对。"),
        ("os", "personal_visual_questions_unavailable", "图片题暂时无法读取，请重新打开原页核对；没有调用模型。"),
    ],
)
def test_facade_personal_visual_errors_are_safe_chinese(
    monkeypatch, service_error, expected_code, expected_message
):
    import integrations.deeptutor_shchem_v1.desktop_personal_visual_questions as service_module
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
        PersonalVisualQuestionError,
    )

    class BrokenService:
        def __init__(self, _facade):
            pass

        def catalog(self, _batch_id=None):
            if service_error == "domain":
                raise PersonalVisualQuestionError(expected_message)
            raise OSError("C:/private/credentials-or-source.png")

    monkeypatch.setattr(service_module, "PersonalVisualQuestionService", BrokenService)
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)

    with pytest.raises(DesktopFacadeError) as caught:
        facade.personal_visual_questions("B")
    assert caught.value.code == expected_code
    assert caught.value.message_zh == expected_message
    assert "private" not in caught.value.message_zh
    assert "credentials" not in caught.value.message_zh


def test_preparation_personal_visual_success_is_atomic_and_never_uses_word_path(qt_app):
    page, facade, tasks = _preparation_page([_asset(1)])
    selected = _reference(_asset(2), _asset(3))
    before = deepcopy(page._payload())

    assert page.import_word_reference(selected)
    assert not page.isEnabled()
    assert page._payload() == before
    assert facade.word_calls == []
    assert len(tasks.pending) == 1
    assert "图片题" in tasks.pending[0]["label"]

    tasks.finish()

    after = page._payload()
    assert page.isEnabled()
    assert page._library_image_task_id is None
    assert facade.word_calls == []
    assert facade.personal_calls == [(selected, [_asset(1)])]
    assert after["image_assets"] == [_asset(1), _asset(2), _asset(3)]
    assert after["materials"] == before["materials"] + "\n\n" + selected["materials"]
    for key in before.keys() - {"materials", "image_assets"}:
        assert after[key] == before[key]
    assert "尚未调用模型" in page.status.text()
    assert facade.provider_calls == 0
    page.close()


@pytest.mark.parametrize("failure", ["exception", "materials", "warnings", "images"])
def test_preparation_personal_visual_failure_keeps_form_and_images(qt_app, failure):
    page, facade, tasks = _preparation_page([_asset(1)])
    facade.failure = failure
    selected = _reference(_asset(2), _asset(3))
    before = deepcopy(page._payload())

    assert page.import_word_reference(selected)
    tasks.finish()

    assert page.isEnabled()
    assert page._library_image_task_id is None
    assert page._payload() == before
    assert facade.word_calls == []
    assert len(facade.personal_calls) == 1
    assert "未能完整导入" in page.status.text()
    assert "private-source" not in page.status.text()
    page.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.pop("batch_id"),
        lambda item: item.update(batch_id=""),
        lambda item: item.pop("key"),
        lambda item: item.pop("revision"),
        lambda item: item.update(points=0),
        lambda item: item.update(points=101),
        lambda item: item.update(points=True),
        lambda item: item.update(points=float("nan")),
    ],
)
def test_preparation_personal_visual_selection_validation_rejects_without_tasks(qt_app, mutate):
    page, facade, tasks = _preparation_page([_asset(1)])
    selected = _reference(_asset(2))
    mutate(selected["selections"][0])
    before = deepcopy(page._payload())

    assert not page.import_word_reference(selected)
    assert page._payload() == before
    assert tasks.pending == []
    assert facade.personal_calls == []
    assert facade.word_calls == []
    page.close()


def test_preparation_personal_visual_rejects_confusing_word_source_selection(qt_app):
    page, facade, tasks = _preparation_page([_asset(1)])
    selected = _reference(_asset(2))
    selected["source_selection"] = {
        "batch_id": "word-batch",
        "source_id": "source.docx",
        "source_sha256": "b" * 64,
        "revision": "word-revision",
        "block_start": 1,
        "block_end": 2,
    }
    before = deepcopy(page._payload())

    assert not page.import_word_reference(selected)
    assert page._payload() == before
    assert tasks.pending == []
    assert facade.personal_calls == []
    assert facade.word_calls == []
    assert "图片题" in page.status.text()
    page.close()


def test_preparation_personal_visual_cannot_downgrade_to_word_text_only(qt_app):
    page, facade, tasks = _preparation_page([_asset(1)])
    selected = _reference()
    selected["include_images"] = False
    before = deepcopy(page._payload())

    assert not page.import_word_reference(selected)
    assert page._payload() == before
    assert tasks.pending == []
    assert facade.personal_calls == []
    assert facade.word_calls == []
    page.close()


class _EntryFacade:
    def personal_visual_questions(self, batch_id=None):
        return {"items": [], "selection": [], "batch_id": batch_id}

    def list_resumable_visual_import_batches(self):
        return ()

    def basket(self):
        return []


class _AcceptedPersonalDialog:
    DialogCode = QDialog.DialogCode

    def __init__(self, facade, tasks, parent=None, batch_id=None):
        self.opened_with = (facade, tasks, parent, batch_id)
        self.preparation_reference = _reference(_asset(7))

    def exec(self):
        return self.DialogCode.Accepted

    def deleteLater(self):
        pass


def test_library_and_import_entrypoints_forward_personal_preparation_reference(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        personal_visual_question_dialog as dialog_module,
    )

    monkeypatch.setattr(dialog_module, "PersonalVisualQuestionDialog", _AcceptedPersonalDialog)

    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    facade = _EntryFacade()
    library_tasks = _DeferredTasks()
    library = LibraryPage(facade, library_tasks)
    received: list[dict[str, Any]] = []
    library.word_reference_requested.connect(received.append)
    assert not library.personal_visual_questions_button.isHidden()
    library._open_personal_visual_questions()
    assert received == [_AcceptedPersonalDialog(facade, library_tasks).preparation_reference]
    library.close()

    import_tasks = _DeferredTasks()
    dialog = ImportDialog(facade, import_tasks)
    assert not dialog.personal_visual_questions_button.isHidden()
    dialog._open_personal_visual_questions()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.preparation_reference == received[0]
    dialog.close()
