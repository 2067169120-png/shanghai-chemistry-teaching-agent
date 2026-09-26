from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_desktop_ui import _wait_until, qt_app  # noqa: F401
from test_model_provider_probe import (
    SECRET,
    FakeCredentialBackend,
    FakeTransport,
    _deepseek_response,
)

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
    ProviderProfileSummary,
)
from integrations.deeptutor_shchem_v1.desktop_provider_probe import (
    DesktopConnectionTestError,
    ProviderConnectionResult,
    run_connection_test,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    FIXED_SYNTHETIC_PROMPT,
    ModelProviderProbeError,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)


@pytest.fixture
def configured_desktop(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        tmp_path / "settings", project_root=project, credential_backend=backend
    )
    metadata = store.upsert_metadata(
        {
            "profile_id": "desktop-default",
            "display_name": "DeepSeek",
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "base_url": "https://api.deepseek.com",
            "model_id": "deepseek-v4-flash-vision-exp",
            "api_style": "chat_completions",
            "local_endpoint_policy": "deny",
            "capabilities": ["text"],
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
        },
        expected_revision=None,
    )
    metadata = store.put_credential(
        "desktop-default", SECRET, expected_revision=metadata["revision"]
    )
    backend.read_calls.clear()  # Exclude the credential write verification.
    return store, backend, metadata


def test_unconfirmed_and_pre_cancelled_never_read_key_or_send(configured_desktop):
    store, backend, metadata = configured_desktop
    transport = FakeTransport(_deepseek_response())
    with pytest.raises(DesktopConnectionTestError, match="确认"):
        run_connection_test(
            store,
            "desktop-default",
            expected_revision=metadata["revision"],
            confirmed=False,
            transport=transport,
        )
    result = run_connection_test(
        store,
        "desktop-default",
        expected_revision=metadata["revision"],
        confirmed=True,
        is_cancelled=lambda: True,
        transport=transport,
    )
    assert result.status == "cancelled"
    assert backend.read_calls == []
    assert transport.requests == []


def test_facade_probe_returns_new_revision_for_repeat_test_and_no_secrets(
    configured_desktop,
):
    store, backend, metadata = configured_desktop
    transport = FakeTransport(_deepseek_response())
    # Exercise actual facade methods without constructing unrelated data readers.
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._providers = store
    facade._connection_test_transport = transport
    facade._connection_test_lock = threading.Lock()
    revision = metadata["revision"]
    for _ in range(2):
        result, profile = facade.test_provider_connection(
            "desktop-default", expected_revision=revision, confirmed=True
        )
        assert result.status == "succeeded"
        assert result.total_tokens == 15
        assert result.latency_ms == 7
        assert result.receipt_id
        assert profile.revision != revision
        assert profile.last_connection_test.status == "succeeded"
        assert profile.last_connection_test.total_tokens == 15
        revision = profile.revision
        assert SECRET not in repr((result, profile))
    assert len(backend.read_calls) == 2
    assert len(transport.requests) == 2
    for request in transport.requests:
        assert request.path == "/chat/completions"
        assert request.model_id == "deepseek-v4-flash-vision-exp"
        assert json.loads(request.body)["messages"] == [
            {"role": "user", "content": FIXED_SYNTHETIC_PROMPT}
        ]
    for file in store.probe_receipt_root.glob("*.json"):
        assert SECRET not in file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "code, phrase",
    [
        ("dns_failure", "域名解析"),
        ("tls_failure", "证书"),
        ("invalid_credentials", "401"),
        ("rate_limited", "429"),
        ("provider_rejected", "接口格式"),
        ("timeout", "超时"),
    ],
)
def test_actionable_errors_do_not_leak_transport_text(configured_desktop, code, phrase):
    store, _, metadata = configured_desktop
    transport = FakeTransport(failure=ModelProviderProbeError(code, SECRET, 503))
    result = run_connection_test(
        store,
        "desktop-default",
        expected_revision=metadata["revision"],
        confirmed=True,
        transport=transport,
    )
    assert result.status == "failed"
    assert phrase in result.message_zh
    assert SECRET not in repr(asdict(result))


def test_running_probe_cancels_and_closes_worker(configured_desktop):
    store, _, metadata = configured_desktop
    transport = FakeTransport(block=True)
    cancelled = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_connection_test,
            store,
            "desktop-default",
            expected_revision=metadata["revision"],
            confirmed=True,
            transport=transport,
            is_cancelled=cancelled.is_set,
        )
        assert transport.entered.wait(2)
        cancelled.set()
        assert future.result(timeout=3).status == "cancelled"


def _profile():
    return ProviderProfileSummary(
        "desktop-default",
        "DeepSeek",
        "https://api.deepseek.com",
        "deepseek-v4-flash-vision-exp",
        "chat_completions",
        ("text",),
        True,
        "rev-before",
    )


def test_native_settings_requires_saved_fields_and_confirmation(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import (
        SettingsDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    calls = []
    profile = _profile()
    refreshed = replace(profile, revision="rev-after")

    def probe(profile_id, **kwargs):
        calls.append((profile_id, kwargs))
        return ProviderConnectionResult(
            "succeeded", "连接成功", total_tokens=15
        ), refreshed

    facade = SimpleNamespace(
        list_provider_profiles=lambda: (profile,), test_provider_connection=probe
    )
    bridge = DesktopTaskBridge()
    dialog = SettingsDialog(facade, bridge)
    dialog.show()
    assert _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
    assert dialog.test_button.isEnabled()
    dialog.model_id.setText("changed")
    assert not dialog.test_button.isEnabled()
    dialog.model_id.setText(profile.model_id)
    dialog.key_input.setText("unsaved-secret")
    assert not dialog.test_button.isEnabled()
    dialog.key_input.clear()
    monkeypatch.setattr(dialog, "_confirm_connection_test", lambda _: False)
    dialog.test_button.click()
    assert calls == []
    monkeypatch.setattr(dialog, "_confirm_connection_test", lambda _: True)
    dialog.test_button.click()
    assert not dialog.model_id.isEnabled()
    assert _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
    assert calls[0][0] == profile.profile_id
    assert calls[0][1]["expected_revision"] == "rev-before"
    assert calls[0][1]["confirmed"] is True
    assert dialog._profile.revision == "rev-after"
    assert "15 tokens" in dialog.status.text()
    assert dialog.test_button.isEnabled()
    dialog.close()
    bridge.shutdown()


def test_native_settings_running_close_guard_and_stop(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import (
        SettingsDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    entered = threading.Event()

    def probe(profile_id, **kwargs):
        import time

        entered.set()
        for _ in range(300):
            if kwargs["is_cancelled"]():
                return ProviderConnectionResult("cancelled", "测试已停止"), _profile()
            time.sleep(0.005)
        raise RuntimeError("test cancellation was not delivered")

    facade = SimpleNamespace(
        list_provider_profiles=lambda: (_profile(),), test_provider_connection=probe
    )
    bridge = DesktopTaskBridge()
    dialog = SettingsDialog(facade, bridge)
    dialog.show()
    assert _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
    monkeypatch.setattr(dialog, "_confirm_connection_test", lambda _: True)
    dialog.test_button.click()
    assert entered.wait(1)
    dialog.close()
    assert dialog.isVisible()
    dialog.stop_test_button.click()
    assert bridge.wait_for_done(2000)
    assert _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
    assert "已停止" in dialog.status.text()
    assert not dialog.stop_test_button.isVisible()
    dialog.close()
    bridge.shutdown()


def test_native_settings_waits_for_delayed_profile_without_sending(qt_app):
    """Background read completion, not a fixed event-loop count, enables testing."""
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    entered, release = threading.Event(), threading.Event()
    def load():
        entered.set()
        assert release.wait(3), "test did not release profile reader"
        return (_profile(),)
    calls = []
    facade = SimpleNamespace(list_provider_profiles=load,
                             test_provider_connection=lambda *a, **k: calls.append(a))
    bridge = DesktopTaskBridge()
    dialog = SettingsDialog(facade, bridge)
    try:
        dialog.show()
        assert _wait_until(qt_app, entered.is_set, timeout=2)
        assert not dialog.test_button.isEnabled()
        assert not calls
        release.set()
        assert _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
        assert dialog.test_button.isEnabled()
        assert dialog._profile.profile_id == "desktop-default"
        assert not calls
    finally:
        release.set()
        bridge.wait_for_done(3000)
        _wait_until(qt_app, lambda: dialog._active_task_id is None, timeout=3)
        dialog.close()
        bridge.shutdown()
