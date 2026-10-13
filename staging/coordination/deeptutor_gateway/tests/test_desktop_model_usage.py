from __future__ import annotations

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_model_usage import (
    TASK_TYPES,
    ModelUsageError,
    ModelUsageStore,
    make_usage_metadata,
    record_transport_call,
)
from integrations.deeptutor_shchem_v1.intake_imports import (
    PinnedVisualTransport,
    VisualProviderRequest,
)
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ModelProviderProbeError,
    PinnedHttpsProbeTransport,
    _build_request,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
)
from integrations.deeptutor_shchem_v1.visual_provider_runtime import (
    build_structured_text_request,
    build_structured_visual_request,
    parse_structured_visual_response,
)

SECRET = "sk-fake-ledger-only-NOT-REAL-123456789"
PRIVATE = "PRIVATE_PROMPT_AND_STUDENT_NAME_NEVER_PERSIST"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def context(tmp_path, *, style="responses"):
    return ModelProviderProbeContext(
        "teacher-default", "openai", "gpt-5-mini", "openai_official_https_v1",
        "https://api.openai.com/v1", "rev_" + "a" * 32, SECRET,
        api_style=style, usage_root=tmp_path / "usage",
    )


def envelope(style="responses", *, usage=True, candidate='{"ok":true}'):
    value = {"private_unrecognized_field": PRIVATE}
    if style == "responses":
        value["output"] = [{"type": "message", "content": [{"type": "output_text", "text": candidate}]}]
        value["status"] = "completed"
        counts = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                  "input_tokens_details": {"cached_tokens": 4}, "output_tokens_details": {"reasoning_tokens": 5}}
    else:
        value["choices"] = [{"finish_reason": "stop", "message": {"content": candidate}}]
        counts = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                  "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 5}}
    if usage is not False:
        value["usage"] = counts if usage is True else usage
    return json.dumps(value).encode()


class FakeSocket:
    def settimeout(self, timeout):
        pass


def fake_connection(body, *, cancel_on_read=None, status=200):
    instances = []

    class Response:
        def __init__(self):
            self.status = status

        def getheader(self, name):
            return {"Content-Type": "application/json", "Content-Length": str(len(body))}.get(name)

        def read(self, limit):
            if cancel_on_read is not None:
                cancel_on_read.set()
            return body[:limit]

    class Connection:
        def __init__(self, *_args, **_kwargs):
            self.sock = FakeSocket()
            self.sends = 0
            self.closed = False
            instances.append(self)

        def connect(self):
            pass

        def putrequest(self, *args, **kwargs):
            pass

        def putheader(self, *args):
            pass

        def endheaders(self, body):
            self.sends += 1

        def getresponse(self):
            return Response()

        def close(self):
            self.closed = True

    return Connection, instances


def send(request, body, *, cancel_event=None, status=200, cancel_on_read=False):
    event = cancel_event if cancel_event is not None else threading.Event()
    factory, instances = fake_connection(body, cancel_on_read=event if cancel_on_read else None, status=status)
    transport = PinnedHttpsProbeTransport(connection_factory=factory)
    if isinstance(request, VisualProviderRequest):
        visual_transport = PinnedVisualTransport()
        visual_transport._transport = transport
        transport = visual_transport
    response = transport.send(request, cancel_event=event, deadline_monotonic=time.monotonic() + 10)
    assert len(instances) == 1 and instances[0].sends == 1 and instances[0].closed
    return response


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_original_transport_records_safe_usage_and_export_without_content(tmp_path, style):
    current = context(tmp_path, style=style)
    request = build_structured_text_request(current, prompt=PRIVATE, schema=SCHEMA,
                                            schema_name="ledger_test", task_type="preparation")
    send(request, envelope(style))
    store = ModelUsageStore(current.usage_root)
    call = store.list_calls()[0]
    assert call["task_type"] == "preparation"
    assert (call["input_tokens"], call["output_tokens"], call["total_tokens"]) == (10, 20, 30)
    assert (call["cached_input_tokens"], call["reasoning_tokens"]) == (4, 5)
    assert call["local_input_estimate"] > 0 and call["local_input_estimate"] != call["input_tokens"]
    assert call["estimated_cost_amount"] is None and call["cost_status"] == "not_configured"
    exported = store.export(tmp_path / "export.json").read_text(encoding="utf-8")
    stored = store.path.read_bytes()
    for marker in (PRIVATE, SECRET, str(tmp_path), "prompt", "response_body", "student_name"):
        assert marker not in exported
        assert marker.encode() not in stored
    assert PRIVATE not in repr(request.usage_metadata) and str(tmp_path) not in repr(current)


@pytest.mark.parametrize("task_type", sorted(TASK_TYPES - {"unknown", "connection_test"}))
def test_shared_builder_and_visual_wrapper_keep_every_task_and_exactly_one_request(tmp_path, task_type):
    current = context(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    request = build_structured_visual_request(current, prompt=PRIVATE, schema=SCHEMA,
                                              schema_name="ledger_test", pages=[("image/png", stream.getvalue())],
                                              task_type=task_type)
    factory, instances = fake_connection(envelope())
    transport = PinnedVisualTransport()
    transport._transport = PinnedHttpsProbeTransport(connection_factory=factory)
    transport.send(request, cancel_event=threading.Event(), deadline_monotonic=time.monotonic() + 10)
    calls = ModelUsageStore(current.usage_root).list_calls()
    assert len(calls) == 1 and calls[0]["task_type"] == task_type
    assert calls[0]["call_id"] == request.usage_metadata.call_id
    assert sum(item.sends for item in instances) == 1


def test_connection_test_uses_same_original_transport_without_content(tmp_path):
    current = context(tmp_path)
    send(_build_request(current), envelope(candidate='{"status":"ok"}'))
    assert ModelUsageStore(current.usage_root).list_calls()[0]["task_type"] == "connection_test"


def test_provider_usage_survives_candidate_parse_failure_and_after_response_cancellation(tmp_path):
    current = context(tmp_path)
    request = build_structured_text_request(current, prompt=PRIVATE, schema=SCHEMA, schema_name="ledger_test")
    response = send(request, envelope(candidate="not valid JSON"))
    with pytest.raises(ValueError):
        parse_structured_visual_response(request.api_style, response.body)
    assert ModelUsageStore(current.usage_root).list_calls()[0]["total_tokens"] == 30
    request = build_structured_text_request(current, prompt=PRIVATE, schema=SCHEMA, schema_name="ledger_test")
    with pytest.raises(ModelProviderProbeError, match="cancelled"):
        send(request, envelope(), cancel_on_read=True)
    calls = ModelUsageStore(current.usage_root).list_calls()
    assert len(calls) == 2 and all(call["total_tokens"] == 30 for call in calls)


def test_unknown_partial_zero_and_invalid_usage_do_not_become_local_estimates(tmp_path):
    current = context(tmp_path)
    usages = [False, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
              {"input_tokens": 7}, {"input_tokens": True, "output_tokens": -1, "total_tokens": "30"}]
    for usage in usages:
        send(_build_request(current), envelope(usage=usage))
    calls = list(reversed(ModelUsageStore(current.usage_root).list_calls()))
    assert [call["usage_status"] for call in calls] == ["unknown", "reported", "partial", "unknown"]
    assert calls[0]["input_tokens"] is None and calls[0]["total_tokens"] is None
    assert calls[1]["total_tokens"] == 0
    assert calls[2]["input_tokens"] == 7 and calls[2]["total_tokens"] is None
    assert calls[3]["input_tokens"] is None
    summary = ModelUsageStore(current.usage_root).summary()
    assert summary["actual_usage_request_count"] == 1 and summary["unknown_usage_request_count"] == 3


@pytest.mark.parametrize("has_usage", [True, False])
def test_http_failure_is_one_attempt_and_preserves_any_reported_usage(tmp_path, has_usage):
    current = context(tmp_path)
    with pytest.raises(ModelProviderProbeError):
        send(_build_request(current), envelope(usage=has_usage), status=503)
    call = ModelUsageStore(current.usage_root).list_calls()[0]
    assert call["outcome"] == "failed" and call["http_status"] == 503
    assert call["usage_status"] == ("reported" if has_usage else "unknown")
    assert call["total_tokens"] == (30 if has_usage else None)


def test_configured_prices_pin_currency_version_and_do_not_reprice_old_calls(tmp_path):
    current = context(tmp_path)
    store = ModelUsageStore(current.usage_root)
    first = store.set_price(current.profile_id, current.provider_id, current.model_id, currency="CNY",
                            input_price_per_million="2", output_price_per_million="4", cached_input_price_per_million="1")
    in_flight = _build_request(current)
    second = store.set_price(current.profile_id, current.provider_id, current.model_id, currency="USD",
                             input_price_per_million="10", output_price_per_million="20")
    # A settings change while an outbound request is in flight cannot reprice it.
    send(in_flight, envelope())
    send(_build_request(current), envelope())
    store.remove_price(current.profile_id, current.provider_id, current.model_id)
    send(_build_request(current), envelope())
    calls = list(reversed(store.list_calls()))
    assert calls[0]["price_version"] == first["version"] and calls[0]["estimated_cost_currency"] == "CNY"
    assert calls[0]["estimated_cost_amount"] == "0.000096"
    assert calls[1]["price_version"] == second["version"] and calls[1]["estimated_cost_amount"] == "0.0005"
    assert calls[2]["price_version"] is None and calls[2]["estimated_cost_amount"] is None
    assert store.summary()["estimated_cost_totals"] == [{"currency": "CNY", "amount": "0.000096"}, {"currency": "USD", "amount": "0.0005"}]


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", "invalid", "0.0000000000001", True])
def test_price_validation_is_local_and_sanitized(tmp_path, bad):
    with pytest.raises(ModelUsageError):
        ModelUsageStore(tmp_path).set_price("profile", "openai", "model", currency="CNY",
                                           input_price_per_million=bad, output_price_per_million="1")


def test_recording_failure_returns_provider_response_and_reports_safe_local_warning(tmp_path, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_model_usage as usage_module
    current = context(tmp_path)
    def fail(*args, **kwargs):
        raise OSError(PRIVATE + SECRET)
    monkeypatch.setattr(ModelUsageStore, "record", fail)
    response = send(_build_request(current), envelope())
    assert response.model_invoked is True
    summary = ModelUsageStore(current.usage_root).summary()
    assert summary["request_count"] == 0 and summary["recording_failure_count"] == 1
    assert "不会自动重发" in summary["last_recording_error"]
    assert PRIVATE not in json.dumps(summary) and SECRET not in json.dumps(summary)
    # A restart loses in-process diagnostics but keeps the safe warning marker.
    with usage_module._FAILURE_LOCK:
        usage_module._FAILURES.clear()
    assert ModelUsageStore(current.usage_root).summary()["recording_failure_count"] == 1
    marker = (current.usage_root / "recording-status.json").read_text(encoding="utf-8")
    assert PRIVATE not in marker and SECRET not in marker and str(tmp_path) not in marker


def test_local_ledger_delay_does_not_change_original_network_deadline_or_latency(tmp_path, monkeypatch):
    current = context(tmp_path)
    original_record = ModelUsageStore.record
    clock = [time.monotonic()]
    start = clock[0]
    def delayed_record(self, *args, **kwargs):
        original_record(self, *args, **kwargs)
        clock[0] += 20
    monkeypatch.setattr(ModelUsageStore, "record", delayed_record)
    factory, instances = fake_connection(envelope(candidate='{"status":"ok"}'))
    transport = PinnedHttpsProbeTransport(connection_factory=factory, monotonic=lambda: clock[0])
    response = transport.send(_build_request(current), cancel_event=threading.Event(), deadline_monotonic=start + 10)
    assert response.latency_ms == 0 and response.model_invoked is True
    assert ModelUsageStore(current.usage_root).list_calls()[0]["latency_ms"] == 0
    assert len(instances) == 1 and instances[0].sends == 1


def test_concurrent_appends_are_atomic_idempotent_and_profile_roots_are_isolated(tmp_path):
    current = context(tmp_path)
    metadata = [make_usage_metadata(current, "semantic_tags", 123) for _ in range(32)]
    def append(item):
        record_transport_call(item, api_style="responses", raw=envelope(), outcome="response_received", model_invoked=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, metadata))
        list(pool.map(append, metadata))
    summary = ModelUsageStore(current.usage_root).summary()
    assert summary["request_count"] == 32 and summary["total_tokens"] == 960
    assert summary["recording_failure_count"] == 0
    other = replace(current, usage_root=tmp_path / "restored" / "model-usage-v1")
    send(_build_request(other), envelope())
    assert ModelUsageStore(other.usage_root).summary()["request_count"] == 1
    assert ModelUsageStore(current.usage_root).summary()["request_count"] == 32


def test_legacy_context_has_no_ledger_or_changed_request_body(tmp_path):
    current = context(tmp_path)
    legacy = replace(current, usage_root=None)
    old = build_structured_text_request(legacy, prompt=PRIVATE, schema=SCHEMA, schema_name="ledger_test")
    new = build_structured_text_request(current, prompt=PRIVATE, schema=SCHEMA, schema_name="ledger_test")
    assert old.usage_metadata is None and old.body == new.body
    send(old, envelope())
    assert not current.usage_root.exists()


def test_facade_connection_price_query_and_export_close_the_local_loop(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.model_provider_settings import (
        ModelProviderSettingsStore,
    )

    class Credentials:
        def __init__(self):
            self.values = {}
        def available(self):
            return True
        def write(self, target, secret):
            self.values[target] = secret
        def read(self, target):
            return self.values.get(target)
        def exists(self, target):
            return target in self.values
        def delete(self, target):
            return self.values.pop(target, None) is not None

    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "isolated-profile"
    settings = ModelProviderSettingsStore(state / "model-settings", project_root=project,
                                          credential_backend=Credentials(), usage_root=state / "model-usage-v1")
    saved = settings.upsert_metadata({
        "profile_id": "teacher-default", "provider_id": "openai", "model_id": "gpt-5-mini",
        "base_url_policy": "openai_official_https_v1", "allowed_data_classes": ["synthetic_only"],
        "image_egress": "deny", "last_probe": None,
    }, expected_revision=None)
    saved = settings.put_credential("teacher-default", SECRET, expected_revision=saved["revision"])
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = SimpleNamespace(state_root=state)
    facade._providers = settings
    facade._connection_test_lock = threading.Lock()
    factory, instances = fake_connection(envelope(candidate='{"status":"ok"}'))
    facade._connection_test_transport = PinnedHttpsProbeTransport(connection_factory=factory)
    facade.save_model_usage_price("teacher-default", currency="CNY", input_price_per_million="2", output_price_per_million="4")
    result, refreshed = facade.test_provider_connection("teacher-default", expected_revision=saved["revision"], confirmed=True)
    assert result.status == "succeeded" and refreshed is not None
    assert len(instances) == 1 and instances[0].sends == 1
    assert facade.model_usage_summary()["total_tokens"] == 30
    assert facade.model_usage_calls()[0]["cost_status"] == "estimated_from_configured_price"
    assert len(facade.model_usage_prices()) == 1
    exported = json.loads(facade.export_model_usage(tmp_path / "usage-export.json").read_text(encoding="utf-8"))
    assert len(exported["calls"]) == 1 and exported["calls"][0]["task_type"] == "connection_test"
    assert SECRET not in json.dumps(exported) and PRIVATE not in json.dumps(exported)
    facade.remove_model_usage_price("teacher-default")
    assert facade.model_usage_prices() == []
    assert facade.model_usage_calls()[0]["estimated_cost_amount"] == "0.0001"
