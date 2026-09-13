from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from test_desktop_ui import qt_app as qt_app  # noqa: PLC0414

from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary
from integrations.deeptutor_shchem_v1.desktop_provider_probe import (
    ProviderConnectionResult,
)


def _profile(**changes: Any) -> ProviderProfileSummary:
    return replace(
        ProviderProfileSummary(
            "desktop-default", "合成测试服务", "https://example.invalid/v1",
            "synthetic-vision", "responses", ("text", "vision"), True, "revision-1",
        ),
        **changes,
    )


class _Facade:
    """In-memory UI double: no real profiles, credentials or transport."""

    def __init__(self, profile: ProviderProfileSummary) -> None:
        self.profile = profile
        self.saved_requests: list[Any] = []
        self.probe_requests: list[Any] = []

    def list_provider_profiles(self):
        return (self.profile,)

    def save_provider_profile(self, request):
        self.saved_requests.append(request)
        self.profile = replace(
            self.profile,
            provider_name=request.provider_name,
            base_url=request.base_url,
            model_id=request.model_id,
            api_style=request.api_style,
            capabilities=("text", "vision") if request.vision_enabled else ("text",),
            max_input_tokens=request.max_input_tokens,
            max_output_tokens=request.max_output_tokens,
            revision=f"revision-{len(self.saved_requests) + 1}",
        )
        return self.profile

    def test_provider_connection(self, profile_id, **kwargs):
        self.probe_requests.append((profile_id, kwargs))
        return ProviderConnectionResult("succeeded", "合成连接测试完成"), self.profile


def _queued_tasks():
    from PySide6.QtCore import QObject, Signal

    class QueuedTasks(QObject):
        task_finished = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.pending = []
            self.serial = 0

        def submit(self, label, operation, **callbacks):
            self.serial += 1
            task_id = f"synthetic-task-{self.serial}"
            self.pending.append((task_id, operation, callbacks))
            return task_id

        def finish(self):
            task_id, operation, callbacks = self.pending.pop(0)
            result = operation()
            callbacks["on_success"](result)
            self.task_finished.emit(task_id)

    return QueuedTasks()


@pytest.fixture
def dialog_factory(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import (
        SettingsDialog,
    )

    dialogs = []

    def create(profile=None, *, finish_load=True):
        facade = _Facade(profile or _profile())
        tasks = _queued_tasks()
        dialog = SettingsDialog(facade, tasks)
        dialogs.append(dialog)
        dialog.show()
        qt_app.processEvents()
        if finish_load:
            tasks.finish()
            qt_app.processEvents()
        return dialog, facade, tasks

    yield create
    for dialog in dialogs:
        dialog._active_task_id = None
        dialog.close()
        dialog.deleteLater()
    qt_app.processEvents()


def _budget_controls(dialog):
    return (dialog.max_input_tokens, dialog.max_output_tokens, dialog.use_reference_button)


def test_budget_fields_start_unset_and_explain_estimate_and_optional_references(dialog_factory):
    from PySide6.QtWidgets import QLabel, QLineEdit

    dialog, facade, tasks = dialog_factory(finish_load=False)
    assert all(not control.isEnabled() for control in _budget_controls(dialog))
    tasks.finish()
    assert all(control.isEnabled() for control in _budget_controls(dialog))
    assert dialog.max_input_tokens.text() == ""
    assert dialog.max_output_tokens.text() == ""
    assert "64,000" in dialog.max_input_tokens.placeholderText()
    assert "8,000" in dialog.max_output_tokens.placeholderText()
    assert dialog.use_reference_button.text() == "采用参考值"
    text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    for required in ("未设置", "各功能默认值", "图片", "本地估算", "服务端真实", "计费", "服务商限制", "仅供参考"):
        assert required in text
    assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password
    assert facade.saved_requests == []
    assert facade.probe_requests == []
    assert dialog.test_button.isEnabled()


@pytest.mark.parametrize("values", [(None, None), (1, 1_000_000), (1_000_000, 1), (987654, 13579)])
def test_budget_fields_load_and_save_exact_optional_values(dialog_factory, values):
    input_tokens, output_tokens = values
    dialog, facade, tasks = dialog_factory(
        _profile(max_input_tokens=input_tokens, max_output_tokens=output_tokens)
    )
    assert dialog.max_input_tokens.text() == ("" if input_tokens is None else str(input_tokens))
    assert dialog.max_output_tokens.text() == ("" if output_tokens is None else str(output_tokens))
    assert dialog._matches_saved()
    dialog.save_button.click()
    assert all(not control.isEnabled() for control in _budget_controls(dialog))
    tasks.finish()
    request = facade.saved_requests[0]
    assert (request.max_input_tokens, request.max_output_tokens) == values
    assert dialog._profile.revision == "revision-2"
    assert dialog._matches_saved()
    assert dialog.test_button.isEnabled()


def test_clear_saved_budget_values_saves_none_without_materializing_references(dialog_factory):
    dialog, facade, tasks = dialog_factory(_profile(max_input_tokens=64000, max_output_tokens=8000))
    dialog.max_input_tokens.clear()
    dialog.max_output_tokens.clear()
    assert not dialog.test_button.isEnabled()
    dialog.save_button.click()
    tasks.finish()
    assert facade.profile.max_input_tokens is None
    assert facade.profile.max_output_tokens is None
    assert dialog.max_input_tokens.text() == ""
    assert dialog.max_output_tokens.text() == ""


@pytest.mark.parametrize("field_name", ["max_input_tokens", "max_output_tokens"])
def test_each_unsaved_budget_blocks_connection_confirmation(dialog_factory, monkeypatch, field_name):
    dialog, facade, tasks = dialog_factory()
    field = getattr(dialog, field_name)
    field.setText("12345")
    assert not dialog.test_button.isEnabled()
    monkeypatch.setattr(dialog, "_confirm_connection_test", lambda _: pytest.fail("unsaved budget confirmed"))
    dialog._test_connection()
    assert tasks.pending == []
    assert facade.probe_requests == []
    assert "先保存" in dialog.status.text()
    field.clear()
    assert dialog.test_button.isEnabled()


@pytest.mark.parametrize("field_name", ["max_input_tokens", "max_output_tokens"])
@pytest.mark.parametrize("invalid", ["0", "-1", "1000001", "1.5", "abc", "1_000", "1e3", "５"])
def test_invalid_budget_never_saves_or_clears_pending_key(dialog_factory, field_name, invalid):
    dialog, facade, tasks = dialog_factory()
    getattr(dialog, field_name).setText(invalid)
    dialog.key_input.setText("synthetic-ui-key-not-a-real-secret")
    assert not dialog._matches_saved()
    dialog.save_button.click()
    assert tasks.pending == []
    assert facade.saved_requests == []
    assert dialog.key_input.text() == "synthetic-ui-key-not-a-real-secret"
    assert "1–1,000,000" in dialog.status.text()
    assert all(control.isEnabled() for control in _budget_controls(dialog))


def test_reference_button_uses_route_suggestion_but_never_locks_or_overwrites_user_values(dialog_factory):
    dialog, facade, tasks = dialog_factory()
    dialog.use_reference_button.click()
    assert dialog.max_input_tokens.text() == "64000"
    assert dialog.max_output_tokens.text() == "8000"
    dialog.base_url.setText("https://api.deepseek.com")
    dialog.model_id.setText("deepseek-v4-flash-vision-exp")
    assert "32,000" in dialog.budget_reference.text()
    assert dialog.max_output_tokens.text() == "8000"
    dialog.use_reference_button.click()
    assert dialog.max_output_tokens.text() == "32000"
    dialog.max_input_tokens.setText("999999")
    dialog.max_output_tokens.setText("123456")
    dialog.api_style.setCurrentIndex(dialog.api_style.findData("chat_completions"))
    assert "8,000" in dialog.budget_reference.text()
    assert dialog.max_input_tokens.text() == "999999"
    assert dialog.max_output_tokens.text() == "123456"
    assert not dialog.max_input_tokens.isReadOnly()
    assert not dialog.max_output_tokens.isReadOnly()
    dialog.save_button.click()
    tasks.finish()
    assert (facade.profile.max_input_tokens, facade.profile.max_output_tokens) == (999999, 123456)


def test_saved_custom_budgets_use_new_revision_and_stay_disabled_during_probe(dialog_factory, monkeypatch):
    dialog, facade, tasks = dialog_factory()
    dialog.max_input_tokens.setText("45678")
    dialog.max_output_tokens.setText("23456")
    dialog.save_button.click()
    tasks.finish()
    monkeypatch.setattr(dialog, "_confirm_connection_test", lambda _: True)
    dialog.test_button.click()
    assert all(not control.isEnabled() for control in _budget_controls(dialog))
    tasks.finish()
    assert facade.probe_requests[0][1]["expected_revision"] == "revision-2"
    assert facade.probe_requests[0][1]["confirmed"] is True
    assert all(control.isEnabled() for control in _budget_controls(dialog))
    assert dialog.test_button.isEnabled()
    assert (dialog.max_input_tokens.text(), dialog.max_output_tokens.text()) == ("45678", "23456")


def test_settings_budget_form_scrolls_without_hiding_footer_actions(dialog_factory, qt_app):
    from PySide6.QtCore import QPoint

    dialog, _, _ = dialog_factory()
    dialog.resize(420, 520)
    qt_app.processEvents()
    assert dialog.scroll.viewport().height() > 0
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    for button in (dialog.save_button, dialog.test_button, dialog.close_button):
        assert button.isVisible()
        top_left = button.mapTo(dialog, QPoint(0, 0))
        assert 0 <= top_left.x() < dialog.width()
        assert 0 <= top_left.y() < dialog.height()
        assert top_left.x() + button.width() <= dialog.width()
        assert top_left.y() + button.height() <= dialog.height()
