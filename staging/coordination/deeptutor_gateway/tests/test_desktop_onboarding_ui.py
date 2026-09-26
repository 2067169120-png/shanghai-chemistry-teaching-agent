"""Real Qt contracts; explicitly skipped when PySide6 is absent, never mocked as a pass."""
from __future__ import annotations
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge


def settle(app, condition=lambda: True):
    end = time.monotonic() + 10
    for _ in range(4):
        app.processEvents(); time.sleep(.03)
    while not condition() and time.monotonic() < end:
        app.processEvents(); time.sleep(.02)
    assert condition()


class FakeFacade:
    def __init__(self):
        self.profile = ProviderProfileSummary("desktop-default", "合成测试配置", "https://example.com/v1",
            "synthetic-model", "chat_completions", ("text",), False, "test-revision",
            max_input_tokens=12345, max_output_tokens=6789)
        self.requests = []
        self.calls = 0
    def list_provider_profiles(self):
        return (self.profile,)
    def save_provider_profile(self, request):
        self.requests.append(request)
        self.profile = replace(self.profile, provider_name=request.provider_name,
            base_url=request.base_url, model_id=request.model_id, api_style=request.api_style,
            capabilities=("text", "vision", "structured_output") if request.vision_enabled else ("text",),
            max_input_tokens=request.max_input_tokens, max_output_tokens=request.max_output_tokens)
        return self.profile
    def test_provider_connection(self, *args, **kwargs):
        self.calls += 1
        pytest.fail("No implicit network/model request is allowed")


@pytest.fixture
def settings():
    app = QApplication.instance() or QApplication([])
    tasks = DesktopTaskBridge()
    facade = FakeFacade()
    dialog = SettingsDialog(facade, tasks)
    dialog.show()
    settle(app, lambda: dialog._active_task_id is None)
    yield dialog, facade, app
    dialog.close()
    tasks.shutdown(2000)
    app.processEvents()


def test_connection_fields_visible_and_advanced_budgets_collapsed(settings):
    dialog, _, app = settings
    assert dialog.advanced.content.isHidden()
    assert dialog.api_style.isVisibleTo(dialog.scroll.widget())
    assert not dialog.max_input_tokens.isVisibleTo(dialog.scroll.widget())
    assert dialog.max_input_tokens.text() == "12345"
    dialog.advanced.toggle.click()
    settle(app)
    assert dialog.max_input_tokens.isVisibleTo(dialog.scroll.widget())


def test_hidden_budgets_survive_unrelated_basic_save(settings):
    dialog, facade, app = settings
    dialog.provider_name.setText("改名后的合成配置")
    dialog.save_button.click()
    settle(app, lambda: dialog._active_task_id is None)
    assert facade.requests[-1].max_input_tokens == 12345
    assert facade.requests[-1].max_output_tokens == 6789
    assert facade.calls == 0


def test_blank_budget_retains_none_semantics(settings):
    dialog, facade, app = settings
    dialog.max_input_tokens.clear()
    dialog.max_output_tokens.clear()
    dialog.save_button.click()
    settle(app, lambda: dialog._active_task_id is None)
    assert facade.requests[-1].max_input_tokens is None
    assert facade.requests[-1].max_output_tokens is None


def test_invalid_hidden_budget_opens_advanced_without_saving(settings):
    dialog, facade, app = settings
    dialog.max_input_tokens.setText("0")
    dialog.save_button.click()
    settle(app)
    assert dialog.advanced.toggle.isChecked()
    assert not facade.requests
    assert "整数" in dialog.status.text()


def test_permission_is_never_displayed_as_verified_vision(settings):
    dialog, facade, _ = settings
    dialog.vision.setChecked(True)
    assert "不是识图测试通过" in dialog.capability_note.text()
    assert facade.calls == 0


def test_both_forms_reflow_on_narrow_settings(settings):
    from PySide6.QtWidgets import QFormLayout
    dialog, _, app = settings
    dialog.resize(480, 640)
    settle(app)
    assert dialog._settings_form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
    assert dialog._budget_form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows


def test_environment_button_disabled_for_facade_without_real_paths(settings):
    dialog, facade, _ = settings
    assert not dialog.environment_button.isEnabled()
    assert facade.calls == 0
