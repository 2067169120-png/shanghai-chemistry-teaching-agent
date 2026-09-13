from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_desktop_preparation_drafts import _payload, _record, _state
from test_desktop_ui import _PreparationFacade, _wait_until

from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import (
    PreparationDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog import (
    PreparationDraftDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def source(tmp_path):
    state = _state(tmp_path / "state", ("prep-old", _record(_payload())))
    service = PreparationDraftService(state)
    return SimpleNamespace(
        preparation_draft_options=service.options,
        load_preparation_draft=service.load,
        state=state,
    )


def test_dialog_preview_load_is_read_only_and_narrow(app, source):
    before = source.state.path.read_bytes()
    dialog = PreparationDraftDialog(source)
    dialog.resize(420, 700)
    dialog.show()
    app.processEvents()
    assert dialog.load_button.isEnabled()
    assert "BLUEPRINT-1" in dialog.preview.toPlainText()
    assert "课后教师复核" in dialog.preview.toPlainText()
    assert dialog.preview.horizontalScrollBar().maximum() == 0
    dialog.load_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected["payload"] == _payload()
    assert before == source.state.path.read_bytes()
    dialog.close()


def test_dialog_stale_source_and_empty_history(app, source):
    dialog = PreparationDraftDialog(source)
    changed = _record(_payload(topic="新版本"))
    source.state.save_draft("prep-old", changed)
    dialog.load_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.load_button.isEnabled()
    assert dialog.selected is None
    dialog.close()
    empty = PreparationDraftDialog(SimpleNamespace(preparation_draft_options=list))
    assert not empty.load_button.isEnabled()
    assert "暂无" in empty.status.text()
    empty.close()


def _chooser(monkeypatch, payload, *, accepted=True):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog as module

    class Choice:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.selected = {"payload": deepcopy(payload)}

        def exec(self):
            return self.DialogCode.Accepted if accepted else self.DialogCode.Rejected

    monkeypatch.setattr(module, "PreparationDraftDialog", Choice)


@pytest.mark.parametrize("discard", [False, True])
def test_loading_asks_before_replacing_unsaved_form(
    app, tmp_path, monkeypatch, discard
):
    payload = _payload()
    _chooser(monkeypatch, payload)
    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page.topic.setText("未保存内容")
    page.materials.setPlainText("先前填写，不可静默丢弃")
    before = page._payload()
    prompts = []

    def ask(*args):
        prompts.append(args)
        return (
            QMessageBox.StandardButton.Yes if discard else QMessageBox.StandardButton.No
        )

    monkeypatch.setattr(QMessageBox, "question", ask)
    page.open_draft_button.click()
    assert len(prompts) == 1
    assert page._payload() == (payload if discard else before)
    assert not facade.prepare_calls and not facade.generate_calls
    page.close()
    bridge.shutdown()


def test_loaded_draft_requires_separate_generation_confirmation(
    app, tmp_path, monkeypatch
):
    payload = _payload()
    _chooser(monkeypatch, payload)
    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page._context_ready(
        (facade.preparation_availability(), facade.preparation_profiles(), ())
    )
    page.open_draft_button.click()
    assert page._payload() == payload
    assert not facade.prepare_calls and not facade.generate_calls
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No
    )
    page.generate_button.click()
    assert not facade.prepare_calls and not facade.generate_calls
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    page.generate_button.click()
    assert _wait_until(
        app, lambda: bool(facade.generate_calls) and page._generation_qt_task_id is None
    )
    assert facade.prepare_calls[0][0] == payload
    assert facade.generate_calls[0]["teacher_confirmed"]
    page.close()
    bridge.shutdown()


def test_loading_legal_nondefault_timing_does_not_clamp(app, tmp_path, monkeypatch):
    payload = _payload()
    payload["lesson_timing"] = "12课时×180分钟"
    payload["lesson_route"] = "专题"
    _chooser(monkeypatch, payload)
    bridge = DesktopTaskBridge()
    page = PreparationPage(_PreparationFacade(tmp_path), bridge)
    page._availability_timer.stop()
    page.open_draft_button.click()
    assert page._payload() == payload
    page.close()
    bridge.shutdown()


def test_saving_freezes_ui_payload_and_keeps_later_edits_dirty(
    app, tmp_path, monkeypatch
):
    facade = _PreparationFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    _chooser(monkeypatch, _payload())
    page.open_draft_button.click()
    pending = []

    def submit(_label, operation, **callbacks):
        pending.append((operation, callbacks))
        return "pending-save"

    monkeypatch.setattr(bridge, "submit", submit)
    page.save_button.click()
    page.topic.setText("保存过程中继续编辑")
    operation, callbacks = pending[0]
    receipt = operation()
    callbacks["on_success"](receipt)
    assert facade.saved_preparation_payloads[0] == _payload()
    assert page._payload() != page._form_baseline
    assert page.topic.text() == "保存过程中继续编辑"
    page.close()
    bridge.shutdown()


def test_open_is_blocked_during_active_generation(app, tmp_path, monkeypatch):
    def forbidden(*_):
        raise AssertionError("must not open chooser")

    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog as module

    monkeypatch.setattr(module, "PreparationDraftDialog", forbidden)
    bridge = DesktopTaskBridge()
    page = PreparationPage(_PreparationFacade(tmp_path), bridge)
    page._availability_timer.stop()
    page._active_preparation_task_id = "running"
    page.open_draft_button.click()
    assert "等待" in page.status.text()
    page._active_preparation_task_id = None
    page.close()
    bridge.shutdown()
