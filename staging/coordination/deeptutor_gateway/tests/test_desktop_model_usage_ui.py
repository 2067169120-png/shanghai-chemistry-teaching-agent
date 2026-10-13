from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_desktop_ui import qt_app as qt_app  # noqa: PLC0414

from integrations.deeptutor_shchem_v1.desktop_facade import ProviderProfileSummary


def _profile() -> ProviderProfileSummary:
    return ProviderProfileSummary(
        "desktop-default", "合成离线服务", "https://example.invalid/v1",
        "synthetic-model", "responses", ("text",), False, "synthetic-revision",
    )


def _call(**changes: Any) -> dict[str, Any]:
    return {
        "call_id": "synthetic-call-1", "occurred_at": "2026-10-13T02:00:00Z",
        "profile_id": "desktop-default", "provider_id": "synthetic-provider",
        "model_id": "synthetic-model", "api_style": "responses", "task_type": "connection_test",
        "outcome": "response_received", "model_invoked": True, "http_status": 200, "latency_ms": 12,
        "local_input_estimate": 42, "input_tokens": None, "output_tokens": None,
        "total_tokens": None, "cached_input_tokens": None, "reasoning_tokens": None,
        "usage_status": "unknown", "estimated_cost_amount": None,
        "estimated_cost_currency": None, "cost_status": "not_configured", "price_version": None,
        **changes,
    }


class _Facade:
    """In-memory data only; never loads real profiles, keys, files or transport."""

    def __init__(self):
        self.calls = [_call()]
        self.prices: list[dict[str, Any]] = []
        self.profiles = (_profile(),)
        self.summary = {
            "request_count": 1, "actual_usage_request_count": 0, "unknown_usage_request_count": 1,
            "local_estimated_input_tokens": 42, "input_tokens": None, "output_tokens": None,
            "total_tokens": None, "estimated_cost_totals": [], "recording_failure_count": 0,
            "last_recording_error": None,
        }
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.removed: list[str] = []
        self.exports: list[Path] = []
        self.call_limits: list[int] = []

    def list_provider_profiles(self):
        return self.profiles

    def model_usage_summary(self):
        return self.summary

    def model_usage_calls(self, *, limit):
        self.call_limits.append(limit)
        return self.calls[:limit]

    def model_usage_prices(self):
        return self.prices

    def save_model_usage_price(self, profile_id, **values):
        self.saved.append((profile_id, values))
        self.prices = [{
            "profile_id": profile_id, "provider_id": "synthetic-provider", "model_id": "synthetic-model",
            "version": "synthetic-price-1", **values,
        }]

    def remove_model_usage_price(self, profile_id):
        self.removed.append(profile_id)
        self.prices = []

    def export_model_usage(self, destination):
        self.exports.append(destination)
        return destination


def _queued_tasks():
    from PySide6.QtCore import QObject, Signal

    class QueuedTasks(QObject):
        task_finished = Signal(str)

        def __init__(self):
            super().__init__()
            self.pending = []
            self.cancelled = []
            self.serial = 0

        def submit(self, label, operation, **callbacks):
            self.serial += 1
            task_id = f"synthetic-task-{self.serial}"
            self.pending.append((task_id, operation, callbacks))
            return task_id

        def finish(self):
            task_id, operation, callbacks = self.pending.pop(0)
            callbacks["on_success"](operation())
            self.task_finished.emit(task_id)

        def fail(self, message):
            task_id, _operation, callbacks = self.pending.pop(0)
            callbacks["on_failure"](message)
            self.task_finished.emit(task_id)

        def cancel(self, task_id):
            self.cancelled.append(task_id)
            return True

    return QueuedTasks()


@pytest.fixture
def usage_factory(qt_app, monkeypatch):
    import socket

    from integrations.deeptutor_shchem_v1.desktop_workbench.model_usage_dialog import (
        ModelUsageDialog,
    )

    def network_forbidden(*_args, **_kwargs):
        pytest.fail("Model usage UI must remain offline")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    dialogs = []

    def create(*, facade=None, finish_load=True):
        facade = facade or _Facade()
        tasks = _queued_tasks()
        dialog = ModelUsageDialog(facade, tasks)
        dialogs.append(dialog)
        dialog.show()
        qt_app.processEvents()
        if finish_load:
            tasks.finish()
            qt_app.processEvents()
        return dialog, facade, tasks

    yield create
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    qt_app.processEvents()


def _table_text(table):
    return "\n".join(table.item(row, column).text()
                     for row in range(table.rowCount()) for column in range(table.columnCount()))


def test_unknown_usage_is_never_zero_actual_usage_and_internal_material_is_not_rendered(usage_factory):
    from PySide6.QtWidgets import QLabel

    facade = _Facade()
    facade.summary.update(input_tokens=0, output_tokens=0, total_tokens=0)
    facade.calls[0].update(prompt="PRIVATE-PROMPT", response="PRIVATE-RESPONSE",
                           student_name="PRIVATE-STUDENT", storage_path="E:/PRIVATE-STORAGE")
    dialog, facade, _tasks = usage_factory(facade=facade)
    assert "未知" in dialog.known_tokens_label.text()
    assert "0" not in dialog.known_tokens_label.text()
    assert "本地估算" in dialog.local_estimate_label.text()
    assert "未知或部分用量 1" in dialog.usage_counts_label.text()
    assert dialog.calls_table.item(0, 12).text() == "未知"
    assert dialog.calls_table.item(0, 18).text() == "未配置价格"
    assert "UTC" in dialog.calls_table.horizontalHeaderItem(1).text()
    assert facade.call_limits == [500]
    rendered = _table_text(dialog.calls_table) + "\n".join(label.text() for label in dialog.findChildren(QLabel))
    for private in ("PRIVATE-PROMPT", "PRIVATE-RESPONSE", "PRIVATE-STUDENT", "PRIVATE-STORAGE"):
        assert private not in rendered
    assert not facade.saved and not facade.exports


def test_reported_zero_is_preserved_and_cost_totals_keep_separate_currencies(usage_factory):
    facade = _Facade()
    facade.summary.update(actual_usage_request_count=1, unknown_usage_request_count=0,
                          input_tokens=0, output_tokens=4, total_tokens=4,
                          estimated_cost_totals=[{"amount": "0.04", "currency": "CNY"},
                                                 {"amount": "0.01", "currency": "USD"}])
    facade.calls = [_call(input_tokens=0, output_tokens=4, total_tokens=4, usage_status="reported",
                          estimated_cost_amount="0.04", estimated_cost_currency="CNY",
                          cost_status="estimated_from_configured_price", price_version="synthetic-price-1")]
    dialog, _, _ = usage_factory(facade=facade)
    assert "输入 0" in dialog.known_tokens_label.text()
    assert dialog.calls_table.item(0, 12).text() == "0"
    assert "服务端已提供" in dialog.calls_table.item(0, 17).text()
    assert "0.04 CNY（按配置价估算）" in dialog.estimated_cost_label.text()
    assert "0.01 USD（按配置价估算）" in dialog.estimated_cost_label.text()
    assert "按配置价估算" in dialog.calls_table.item(0, 18).text()


def test_partial_usage_and_cache_usage_unknown_explain_cost_limit(usage_factory):
    facade = _Facade()
    facade.summary.update(actual_usage_request_count=1, input_tokens=5, output_tokens=None, total_tokens=None)
    facade.calls = [
        _call(input_tokens=5, usage_status="partial", cost_status="usage_unknown", price_version="price-1"),
        _call(call_id="synthetic-call-2", input_tokens=2, output_tokens=3, total_tokens=5,
              usage_status="reported", cost_status="cache_usage_unknown", price_version="price-1"),
    ]
    dialog, _, _ = usage_factory(facade=facade)
    assert "输出 未知" in dialog.known_tokens_label.text()
    assert "服务端部分提供" in dialog.calls_table.item(0, 17).text()
    assert dialog.calls_table.item(0, 18).text() == "用量未知，无法估算"
    assert dialog.calls_table.item(1, 18).text() == "缓存用量未知，无法估算"


def test_call_metadata_uses_chinese_labels_without_mutating_original_enums(usage_factory):
    facade = _Facade()
    tasks = {
        "unknown": "未知任务", "preparation": "备课", "visual_import": "视觉导入",
        "visual_crop_review": "裁片复核", "textbook_knowledge": "教材知识", "exam_advice": "考试建议",
        "student_analysis": "学生分析", "semantic_tags": "语义标签", "connection_test": "连接测试",
        "blueprint_generation": "蓝图生成", "blueprint_diagnosis": "蓝图诊断", "blueprint_revision": "蓝图修订",
    }
    outcomes = {"response_received": "收到响应", "failed": "运输失败",
                "cancelled": "已取消", "not_sent": "未发送"}
    api_styles = {"responses": "响应接口", "chat_completions": "聊天补全接口"}
    original = [
        _call(call_id=f"synthetic-call-{index}", task_type=task,
              outcome=list(outcomes)[index % len(outcomes)],
              api_style=list(api_styles)[index % len(api_styles)])
        for index, task in enumerate(tasks)
    ]
    facade.calls = [dict(row) for row in original]
    dialog, _, _ = usage_factory(facade=facade)
    for index, call in enumerate(original):
        assert dialog.calls_table.item(index, 5).text() == api_styles[call["api_style"]]
        assert dialog.calls_table.item(index, 6).text() == tasks[call["task_type"]]
        assert dialog.calls_table.item(index, 7).text() == outcomes[call["outcome"]]
    assert facade.calls == original


def test_temporarily_unavailable_price_is_distinct_from_unconfigured_price(usage_factory):
    facade = _Facade()
    facade.calls = [_call(cost_status="price_unavailable")]
    dialog, _, _ = usage_factory(facade=facade)
    assert dialog.calls_table.item(0, 18).text() == "价格暂时无法读取，无法估算"


@pytest.mark.parametrize("reported_input", [0, 5])
def test_partial_summary_without_fully_reported_request_preserves_known_fields(usage_factory, reported_input):
    facade = _Facade()
    facade.summary.update(actual_usage_request_count=0, input_tokens=reported_input,
                          output_tokens=None, total_tokens=None)
    facade.calls = [_call(input_tokens=reported_input, usage_status="partial")]
    dialog, _, _ = usage_factory(facade=facade)
    assert f"输入 {reported_input}" in dialog.known_tokens_label.text()
    assert "输出 未知" in dialog.known_tokens_label.text()
    assert "总计 未知" in dialog.known_tokens_label.text()


def test_save_remove_price_uses_exact_decimal_and_refresh_preserves_historical_call(usage_factory):
    dialog, facade, tasks = usage_factory()
    assert dialog.price_state.text() == "未配置价格"
    assert not dialog.remove_price_button.isEnabled()
    dialog.currency_input.setText("usd")
    dialog.input_price.setText("1.2300")
    dialog.output_price.setText("0")
    dialog.save_price_button.click()
    assert not dialog.save_price_button.isEnabled()
    tasks.finish()
    tasks.finish()
    assert facade.saved == [("desktop-default", {
        "currency": "USD", "input_price_per_million": "1.2300", "output_price_per_million": "0",
        "cached_input_price_per_million": None,
    })]
    assert "synthetic-price-1" in dialog.price_state.text()
    assert dialog.prices_table.rowCount() == 1
    assert dialog.calls_table.item(0, 18).text() == "未配置价格"
    assert dialog.remove_price_button.isEnabled()
    dialog.remove_price_button.click()
    tasks.finish()
    tasks.finish()
    assert facade.removed == ["desktop-default"]
    assert dialog.price_state.text() == "未配置价格"
    assert not dialog.remove_price_button.isEnabled()
    assert dialog.prices_table.rowCount() == 0


@pytest.mark.parametrize("invalid", ["", "-1", "NaN", "Infinity", "1e3", "１", "1,000"])
def test_invalid_price_never_mutates_saved_configuration(usage_factory, invalid):
    dialog, facade, tasks = usage_factory()
    dialog.currency_input.setText("USD")
    dialog.input_price.setText(invalid)
    dialog.output_price.setText("2")
    dialog.save_price_button.click()
    assert not tasks.pending
    assert not facade.saved
    assert "十进制数" in dialog.status.text()


def test_cached_price_and_profile_selection_do_not_display_base_url_or_keys(usage_factory):
    facade = _Facade()
    facade.prices = [{"profile_id": "desktop-default", "provider_id": "synthetic-provider",
                      "model_id": "synthetic-model", "currency": "CNY", "input_price_per_million": "2",
                      "output_price_per_million": "3", "cached_input_price_per_million": "0.4",
                      "version": "old-price"}]
    dialog, facade, tasks = usage_factory(facade=facade)
    assert "example.invalid" not in dialog.profile_combo.currentText()
    assert dialog.cached_input_price.text() == "0.4"
    dialog.save_price_button.click()
    tasks.finish()
    tasks.finish()
    assert facade.saved[0][1]["cached_input_price_per_million"] == "0.4"


def test_changed_model_does_not_inherit_old_model_price_in_current_profile_form(usage_factory):
    facade = _Facade()
    facade.prices = [{"profile_id": "desktop-default", "provider_id": "synthetic-provider",
                      "model_id": "synthetic-model", "currency": "CNY", "input_price_per_million": "2",
                      "output_price_per_million": "3", "cached_input_price_per_million": None,
                      "version": "old-model-price"}]
    dialog, facade, tasks = usage_factory(facade=facade)
    assert "old-model-price" in dialog.price_state.text()
    facade.profiles = (replace(_profile(), model_id="synthetic-new-model"),)
    dialog.refresh_button.click()
    tasks.finish()
    assert dialog.price_state.text() == "未配置价格"
    assert not dialog.currency_input.text()
    assert not dialog.input_price.text()
    assert not dialog.remove_price_button.isEnabled()
    assert dialog.prices_table.rowCount() == 1
    assert "old-model-price" in _table_text(dialog.prices_table)


def test_export_uses_user_json_destination_and_does_not_show_internal_storage_path(usage_factory, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from integrations.deeptutor_shchem_v1.desktop_workbench.model_usage_dialog import (
        DEFAULT_EXPORT_PATH,
    )

    dialog, facade, tasks = usage_factory()
    selected = tmp_path / "synthetic-usage-export.json"
    chooser_args = []

    def choose(*args):
        chooser_args.append(args)
        return str(selected), "JSON (*.json)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    dialog.export_button.click()
    assert chooser_args[0][2] == DEFAULT_EXPORT_PATH
    assert not dialog.export_button.isEnabled()
    tasks.finish()
    assert facade.exports == [selected]
    assert "已导出 JSON" in dialog.status.text()
    assert str(selected) not in dialog.status.text()
    assert dialog.export_button.isEnabled()


def test_cancelled_export_does_not_submit_or_write(usage_factory, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    dialog, facade, tasks = usage_factory()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    dialog.export_button.click()
    assert not tasks.pending and not facade.exports


def test_refresh_reports_recording_failures_with_safe_error_and_recovers_controls(usage_factory):
    dialog, facade, tasks = usage_factory()
    facade.summary.update(recording_failure_count=3,
                          last_recording_error="C:/PRIVATE-PATH PRIVATE-TOKEN PRIVATE-PROMPT")
    dialog.refresh_button.click()
    tasks.finish()
    assert "记录失败 3 次" in dialog.recording_failure_label.text()
    assert "最近错误" in dialog.recording_failure_label.text()
    assert "已发送请求不会自动重发" in dialog.recording_failure_label.text()
    assert "累计统计可能不完整" in dialog.recording_failure_label.text()
    assert "PRIVATE" not in dialog.recording_failure_label.text()
    dialog.refresh_button.click()
    tasks.fail("C:/PRIVATE-PATH PRIVATE-TOKEN")
    assert "PRIVATE" not in dialog.status.text()
    assert "未完成" in dialog.status.text()
    assert dialog.refresh_button.isEnabled()
    assert dialog.export_button.isEnabled()


def test_no_profiles_disables_price_actions_and_empty_ledger_has_no_actual_usage_claim(usage_factory):
    facade = _Facade()
    facade.profiles = ()
    facade.calls = []
    facade.summary.update(request_count=0, actual_usage_request_count=0, unknown_usage_request_count=0,
                          input_tokens=0, output_tokens=0, total_tokens=0)
    dialog, _, _ = usage_factory(facade=facade)
    assert "尚无" in dialog.price_state.text()
    assert "尚无调用记录" == dialog.estimated_cost_label.text()
    assert not dialog.save_price_button.isEnabled()
    assert not dialog.remove_price_button.isEnabled()


def test_close_cancels_pending_local_read_and_late_callback_is_ignored(usage_factory):
    dialog, _, tasks = usage_factory(finish_load=False)
    task_id = dialog._active
    dialog.reject()
    assert tasks.cancelled == [task_id]
    tasks.finish()
    assert not dialog._loaded_once


def test_price_form_scrolls_at_small_window_size_and_footer_remains_accessible(usage_factory, qt_app):
    from PySide6.QtCore import QPoint

    dialog, _, _ = usage_factory()
    dialog.tabs.setCurrentIndex(2)
    dialog.resize(520, 440)
    qt_app.processEvents()
    assert dialog.prices_scroll.verticalScrollBar().maximum() > 0
    dialog.prices_scroll.ensureWidgetVisible(dialog.input_price)
    qt_app.processEvents()
    point = dialog.input_price.mapTo(dialog.prices_scroll.viewport(), QPoint(0, 0))
    assert 0 <= point.y() < dialog.prices_scroll.viewport().height()
    for button in (dialog.refresh_button, dialog.export_button, dialog.close_button):
        point = button.mapTo(dialog, QPoint(0, 0))
        assert point.y() + button.height() <= dialog.height()


def test_settings_footer_opens_independent_usage_dialog_without_model_request(qt_app, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.model_usage_dialog as usage_module
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import (
        SettingsDialog,
    )

    facade = _Facade()
    tasks = _queued_tasks()
    settings = SettingsDialog(facade, tasks)
    tasks.finish()
    opened = []

    class FakeDialog:
        def __init__(self, received_facade, received_tasks, parent):
            opened.append((received_facade, received_tasks, parent))

        def exec(self):
            opened.append("exec")

        def deleteLater(self):
            opened.append("deleteLater")

    monkeypatch.setattr(usage_module, "ModelUsageDialog", FakeDialog)
    try:
        assert settings.model_usage_button.text() == "用量与费用"
        assert "不调用模型" in settings.model_usage_button.accessibleName()
        assert settings.model_usage_button.parent() is settings.environment_button.parent()
        settings.model_usage_button.click()
        assert opened == [(facade, tasks, settings), "exec", "deleteLater"]
        assert not tasks.pending
    finally:
        settings.close()
        settings.deleteLater()
        qt_app.processEvents()
