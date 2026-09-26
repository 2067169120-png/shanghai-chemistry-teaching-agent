from __future__ import annotations

import hashlib
import json
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest

import integrations.deeptutor_shchem_v1.model_provider_probe as probe_module
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    FIXED_SYNTHETIC_PROMPT,
    FIXED_SYNTHETIC_PROMPT_BYTES,
    FIXED_SYNTHETIC_PROMPT_SHA256,
    MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY,
    ModelProviderProbeError,
    ModelProviderSyntheticProbeManager,
    PinnedHttpsProbeTransport,
    ProbeTransportResponse,
    synthetic_probe_contract,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderSettingsStore,
)

SECRET = "sk-fake-probe-only-NOT-REAL-123456789"


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.read_calls: list[str] = []
        self.exists_calls: list[str] = []

    def available(self) -> bool:
        return True

    def write(self, target_name: str, secret: str) -> None:
        self.values[target_name] = secret

    def exists(self, target_name: str) -> bool:
        self.exists_calls.append(target_name)
        return target_name in self.values

    def read(self, target_name: str) -> str | None:
        self.read_calls.append(target_name)
        return self.values.get(target_name)

    def delete(self, target_name: str) -> bool:
        return self.values.pop(target_name, None) is not None


def _profile(provider_id: str = "openai") -> dict[str, Any]:
    if provider_id == "openai":
        model_id = "gpt-5-mini"
        policy = "openai_official_https_v1"
    else:
        model_id = "deepseek-v4-flash"
        policy = "deepseek_official_https_v1"
    return {
        "profile_id": "teacher-default",
        "provider_id": provider_id,
        "model_id": model_id,
        "base_url_policy": policy,
        "allowed_data_classes": ["synthetic_only"],
        "image_egress": "deny",
        "last_probe": None,
    }


@pytest.fixture
def configured(
    tmp_path: Path,
) -> tuple[ModelProviderSettingsStore, FakeCredentialBackend, Path, dict[str, Any]]:
    project = tmp_path / "project"
    metadata = tmp_path / "external"
    project.mkdir()
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        metadata, project_root=project, credential_backend=backend
    )
    created = store.upsert_metadata(_profile(), expected_revision=None)
    profile = store.put_credential(
        "teacher-default", SECRET, expected_revision=created["revision"]
    )
    return store, backend, metadata, profile


def _openai_response(
    *,
    output: str = '{"status":"ok"}',
    usage: dict[str, int] | None = None,
) -> bytes:
    return json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": output}],
                }
            ],
            "usage": usage
            or {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
        },
        separators=(",", ":"),
    ).encode()


def _deepseek_response() -> bytes:
    return json.dumps(
        {
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        },
        separators=(",", ":"),
    ).encode()


class FakeTransport:
    def __init__(
        self,
        body: bytes | None = None,
        *,
        content_type: str | None = "application/json; charset=utf-8",
        content_encoding: str | None = None,
        failure: ModelProviderProbeError | None = None,
        block: bool = False,
    ) -> None:
        self.body = body or _openai_response()
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.failure = failure
        self.block = block
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requests: list[Any] = []

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        self.requests.append(request)
        self.entered.set()
        if self.block:
            while not self.release.wait(0.005):
                if cancel_event.is_set():
                    raise ModelProviderProbeError(
                        "cancelled",
                        "synthetic probe was cancelled",
                        409,
                        connection_state="cancelled",
                    )
                if time.monotonic() > deadline_monotonic:
                    raise ModelProviderProbeError(
                        "timeout", "synthetic probe timed out", 504
                    )
        if self.failure is not None:
            raise self.failure
        return ProbeTransportResponse(
            http_status=200,
            content_type=self.content_type,
            content_encoding=self.content_encoding,
            body=self.body,
            latency_ms=7,
            model_invoked=True,
        )


def _wait(manager: ModelProviderSyntheticProbeManager, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = manager.get(run_id)
        if status["status"] in {"succeeded", "failed", "cancelled", "stale"}:
            return status
        time.sleep(0.005)
    raise AssertionError("probe did not finish")


def test_fixed_prompt_contract_is_exact_and_has_no_user_data() -> None:
    assert MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY == (
        "model_provider_synthetic_probe_execute"
    )
    assert FIXED_SYNTHETIC_PROMPT_BYTES == FIXED_SYNTHETIC_PROMPT.encode("utf-8")
    assert len(FIXED_SYNTHETIC_PROMPT_BYTES) == 101
    assert hashlib.sha256(FIXED_SYNTHETIC_PROMPT_BYTES).hexdigest() == (
        FIXED_SYNTHETIC_PROMPT_SHA256
    )
    assert FIXED_SYNTHETIC_PROMPT_SHA256 == (
        "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f"
    )
    assert synthetic_probe_contract() == {
        "prompt": {
            "text": FIXED_SYNTHETIC_PROMPT,
            "sha256": FIXED_SYNTHETIC_PROMPT_SHA256,
            "bytes": 101,
        },
        "data_class": "synthetic_only",
        "question_data_sent": False,
        "student_data_sent": False,
        "commercial_material_sent": False,
        "image_sent": False,
        "user_prompt_override_allowed": False,
        "endpoint_override_allowed": False,
        "production_model_invocation_enabled": False,
    }


@pytest.mark.parametrize("provider_id", ["openai", "deepseek"])
def test_request_body_host_and_path_are_fully_closed(provider_id: str) -> None:
    base_url = (
        "https://api.openai.com/v1"
        if provider_id == "openai"
        else "https://api.deepseek.com"
    )
    model = "gpt-5-mini" if provider_id == "openai" else "deepseek-v4-flash"
    policy = (
        "openai_official_https_v1"
        if provider_id == "openai"
        else "deepseek_official_https_v1"
    )
    context = probe_module.ModelProviderProbeContext(
        "teacher-default", provider_id, model, policy, base_url, "rev_" + "a" * 32, SECRET
    )
    request = probe_module._build_request(context)
    body = json.loads(request.body)
    assert request.port == 443
    assert request.host == (
        "api.openai.com" if provider_id == "openai" else "api.deepseek.com"
    )
    assert request.path == (
        "/v1/responses" if provider_id == "openai" else "/chat/completions"
    )
    assert body["stream"] is False
    assert SECRET.encode() not in request.body
    assert repr(request).find(SECRET) == -1
    assert "student" not in request.body.decode().lower()
    if provider_id == "openai":
        assert body["store"] is False
        assert body["background"] is False
        assert body["tools"] == []
        assert body["tool_choice"] == "none"
        assert body["max_output_tokens"] == 64
    else:
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 64
        assert "tools" not in body


@pytest.mark.parametrize(
    ("base_url", "api_style", "local_policy", "expected"),
    [
        (
            "https://gateway.example.test/v1",
            "responses",
            "deny",
            ("https", "gateway.example.test", 443, "/v1/responses"),
        ),
        (
            "http://127.0.0.1:1234/v1",
            "chat_completions",
            "allow_loopback_http",
            ("http", "127.0.0.1", 1234, "/v1/chat/completions"),
        ),
    ],
)
def test_custom_probe_request_uses_only_the_saved_endpoint_and_api_style(
    base_url: str,
    api_style: str,
    local_policy: str,
    expected: tuple[str, str, int, str],
) -> None:
    context = probe_module.ModelProviderProbeContext(
        "teacher-default",
        "openai_compatible",
        "lab/model:preview",
        (
            "openai_compatible_loopback_v1"
            if local_policy == "allow_loopback_http"
            else "openai_compatible_public_https_v1"
        ),
        base_url,
        "rev_" + "a" * 32,
        SECRET,
        provider_kind="openai_compatible",
        api_style=api_style,
        local_endpoint_policy=local_policy,
    )
    request = probe_module._build_request(context)
    assert (request.scheme, request.host, request.port, request.path) == expected
    assert request.provider_id == "openai_compatible"
    assert request.endpoint_scope == (
        "loopback" if local_policy == "allow_loopback_http" else "public_https"
    )
    body = json.loads(request.body)
    assert body["model"] == "lab/model:preview"
    assert body["stream"] is False
    if api_style == "responses":
        assert body["store"] is False
        assert body["background"] is False
    else:
        assert body["response_format"] == {"type": "json_object"}
        assert "thinking" not in body
    model_list = probe_module._build_model_list_request(context)
    assert model_list.method == "GET"
    assert model_list.path == "/v1/models"
    assert model_list.body == b""


def test_custom_chat_probe_success_is_bound_to_manual_model_and_not_vision(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    metadata = tmp_path / "external"
    project.mkdir()
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        metadata, project_root=project, credential_backend=backend
    )
    created = store.upsert_metadata(
        {
            "profile_id": "teacher-default",
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "display_name": "实验服务",
            "model_id": "lab/model:preview",
            "base_url": "https://gateway.example.test/v1",
            "api_style": "chat_completions",
            "capabilities": ["text", "vision", "structured_output"],
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
            "last_probe": None,
        },
        expected_revision=None,
    )
    configured_profile = store.put_credential(
        "teacher-default", SECRET, expected_revision=created["revision"]
    )
    transport = FakeTransport(body=_deepseek_response())
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + "e" * 32
    manager.start(
        "teacher-default",
        expected_revision=configured_profile["revision"],
        idempotency_key=run_id,
    )
    result = _wait(manager, run_id)
    manager.shutdown()
    assert result["status"] == "succeeded"
    assert transport.requests[0].path == "/v1/chat/completions"
    profile = store.list_metadata()[0]
    assert profile["model_status"] == "unverified_custom_model"
    assert profile["capability_evidence"]["probed"] == [
        "text",
        "structured_output",
    ]
    assert "vision" not in profile["capability_evidence"]["probed"]


def test_list_and_readiness_use_exists_without_decoding_key(configured: Any) -> None:
    store, backend, _metadata, profile = configured
    reads_before = len(backend.read_calls)
    listed = store.list_metadata()
    status = store.synthetic_test_status("teacher-default")
    assert listed[0]["credential_state"] == "configured"
    assert status["ready"] is True
    assert len(backend.read_calls) == reads_before
    assert len(backend.exists_calls) >= 2
    assert store.credential_exists("teacher-default") is True
    with store.borrow_probe_context(
        "teacher-default", expected_revision=profile["revision"]
    ) as context:
        assert context.api_key == SECRET
        assert SECRET not in repr(context)
    assert len(backend.read_calls) == reads_before + 1


def test_success_is_async_idempotent_persisted_and_receipt_is_secret_free(
    configured: Any,
) -> None:
    store, _backend, metadata, profile = configured
    transport = FakeTransport()
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + "1" * 32
    started = manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    replay = manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    assert started["probe"]["probe_run_id"] == run_id
    assert replay["idempotent_replay"] is True
    result = _wait(manager, run_id)
    replay_after_commit = manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    manager.shutdown()
    assert result["status"] == "succeeded"
    assert result["connection_state"] == "connected"
    assert result["model_invoked"] is True
    assert result["usage"] == {
        "input_tokens": 11,
        "output_tokens": 4,
        "total_tokens": 15,
    }
    assert result["cost"] == {
        "amount": None,
        "currency": None,
        "reason": "not_estimated_without_pinned_price",
    }
    assert result["egress"]["data_class"] == "synthetic_only"
    assert result["egress"]["question_data_sent"] is False
    assert result["production_model_invocation_enabled"] is False
    assert replay_after_commit["idempotent_replay"] is True
    assert replay_after_commit["probe"]["status"] == "succeeded"
    assert result["receipt_id"].startswith("probe-receipt-")
    receipt = metadata / "probe-receipts-v1" / f"{result['receipt_id']}.json"
    assert receipt.is_file()
    raw = receipt.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result["receipt_sha256"]
    assert SECRET.encode() not in raw
    lowered = raw.lower()
    assert b"authorization\"" not in lowered
    assert b"response_body\"" not in lowered
    parsed = json.loads(raw)
    assert parsed["response_body_persisted_in_receipt"] is False
    assert parsed["request"]["prompt_sha256"] == FIXED_SYNTHETIC_PROMPT_SHA256
    assert parsed["request"]["prompt_bytes"] == 101
    assert parsed["request"]["summary_sha256"]
    assert parsed["response"]["summary_sha256"]
    saved = store.list_metadata()[0]["last_probe"]
    assert saved["status"] == "succeeded"
    assert saved["probe_run_id"] == run_id
    assert saved["receipt_sha256"] == result["receipt_sha256"]


def test_deepseek_success_normalizes_usage(tmp_path: Path) -> None:
    project = tmp_path / "project"
    metadata = tmp_path / "external"
    project.mkdir()
    backend = FakeCredentialBackend()
    store = ModelProviderSettingsStore(
        metadata, project_root=project, credential_backend=backend
    )
    created = store.upsert_metadata(_profile("deepseek"), expected_revision=None)
    profile = store.put_credential(
        "teacher-default", SECRET, expected_revision=created["revision"]
    )
    transport = FakeTransport(_deepseek_response())
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + "2" * 32
    manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    result = _wait(manager, run_id)
    manager.shutdown()
    assert result["status"] == "succeeded"
    assert result["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }
    assert transport.requests[0].path == "/chat/completions"


@pytest.mark.parametrize(
    ("transport", "expected_code"),
    [
        (FakeTransport(content_type="text/plain"), "response_mime_invalid"),
        (FakeTransport(content_encoding="gzip"), "response_encoding_unsupported"),
        (FakeTransport(b"{" + b"x" * (128 * 1024) + b"}"), "response_too_large"),
        (FakeTransport(b'{"a":1,"a":2}'), "response_invalid"),
        (
            FakeTransport(
                _openai_response(
                    usage={"input_tokens": -1, "output_tokens": 2, "total_tokens": 1}
                )
            ),
            "response_invalid",
        ),
    ],
)
def test_mime_encoding_size_duplicate_json_and_usage_fail_closed(
    configured: Any, transport: FakeTransport, expected_code: str
) -> None:
    store, _backend, _metadata, profile = configured
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + hashlib.sha256(expected_code.encode()).hexdigest()[:32]
    manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    result = _wait(manager, run_id)
    manager.shutdown()
    assert result["status"] == "failed"
    assert result["error_code"] == expected_code
    assert result["production_model_invocation_enabled"] is False


@pytest.mark.parametrize(
    "code",
    [
        "invalid_credentials",
        "permission_denied",
        "rate_limited",
        "redirect_refused",
        "provider_unavailable",
        "dns_failure",
        "tls_failure",
        "timeout",
        "network_unavailable",
    ],
)
def test_transport_error_codes_are_stable_and_never_echo_exception(
    configured: Any, code: str
) -> None:
    store, _backend, metadata, profile = configured
    hostile = SECRET + "-HOSTILE-RESPONSE"
    failure = ModelProviderProbeError(code, hostile, 503)
    manager = ModelProviderSyntheticProbeManager(
        store, transport=FakeTransport(failure=failure)
    )
    run_id = "probe_" + hashlib.sha256((code + "x").encode()).hexdigest()[:32]
    manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    result = _wait(manager, run_id)
    manager.shutdown()
    assert result["error_code"] == code
    assert hostile not in json.dumps(result)
    for path in metadata.rglob("*"):
        if path.is_file():
            assert hostile.encode() not in path.read_bytes()


def test_single_inflight_cancel_and_shutdown_are_bounded(configured: Any) -> None:
    store, _backend, _metadata, profile = configured
    transport = FakeTransport(block=True)
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + "3" * 32
    manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    assert transport.entered.wait(1)
    with pytest.raises(ModelProviderProbeError) as caught:
        manager.start(
            "teacher-default",
            expected_revision=profile["revision"],
            idempotency_key="probe_" + "4" * 32,
        )
    assert caught.value.code == "probe_in_progress"
    requested = manager.cancel(run_id, expected_revision=profile["revision"])
    assert requested["status"] == "cancel_requested"
    result = _wait(manager, run_id)
    assert result["status"] == "cancelled"
    assert result["connection_state"] == "cancelled"
    manager.shutdown()
    with pytest.raises(ModelProviderProbeError) as stopped:
        manager.start(
            "teacher-default",
            expected_revision=store.list_metadata()[0]["revision"],
            idempotency_key="probe_" + "5" * 32,
        )
    assert stopped.value.code == "probe_manager_shutdown"


def test_rotation_during_probe_makes_old_success_stale(configured: Any) -> None:
    store, _backend, _metadata, profile = configured
    transport = FakeTransport(block=True)
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    run_id = "probe_" + "6" * 32
    manager.start(
        "teacher-default",
        expected_revision=profile["revision"],
        idempotency_key=run_id,
    )
    assert transport.entered.wait(1)
    rotated = store.put_credential(
        "teacher-default",
        "sk-fake-rotated-NOT-REAL-987654321",
        expected_revision=profile["revision"],
    )
    transport.release.set()
    result = _wait(manager, run_id)
    manager.shutdown()
    assert result["status"] == "stale"
    assert result["error_code"] == "stale_result"
    current = store.list_metadata()[0]
    assert current["revision"] == rotated["revision"]
    assert current["last_probe"]["status"] == "ready_not_invoked"


def test_bad_id_revision_and_unknown_run_fail_without_network(configured: Any) -> None:
    store, _backend, _metadata, profile = configured
    transport = FakeTransport()
    manager = ModelProviderSyntheticProbeManager(store, transport=transport)
    with pytest.raises(ModelProviderProbeError) as bad_id:
        manager.start(
            "teacher-default",
            expected_revision=profile["revision"],
            idempotency_key="user-controlled-text",
        )
    assert bad_id.value.code == "idempotency_key_invalid"
    with pytest.raises(ModelProviderProbeError) as bad_revision:
        manager.start(
            "teacher-default",
            expected_revision="rev_" + "0" * 32,
            idempotency_key="probe_" + "7" * 32,
        )
    assert bad_revision.value.code == "revision_conflict"
    with pytest.raises(ModelProviderProbeError) as missing:
        manager.get("probe_" + "8" * 32)
    assert missing.value.code == "probe_not_found"
    manager.shutdown()
    assert transport.requests == []


class _FakeSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value


class _FakeHttpResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def getheader(self, name: str) -> str | None:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Encoding": None,
            "Content-Length": str(len(self.body)),
        }.get(name)

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class _FakeHttpsConnection:
    instances: ClassVar[list[Any]] = []

    def __init__(self, host: str, *, port: int, timeout: float, context: Any) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.sock = _FakeSocket()
        self.headers: list[tuple[str, str]] = []
        self.body: bytes | None = None
        self.path: str | None = None
        self.__class__.instances.append(self)

    def connect(self) -> None:
        return None

    def putrequest(self, method: str, path: str, **_kwargs: Any) -> None:
        assert method == "POST"
        self.path = path

    def putheader(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def endheaders(self, body: bytes) -> None:
        self.body = body

    def getresponse(self) -> _FakeHttpResponse:
        return _FakeHttpResponse(_openai_response())

    def close(self) -> None:
        return None


def test_real_transport_uses_direct_https_identity_encoding_and_secure_tls() -> None:
    _FakeHttpsConnection.instances.clear()
    context = PinnedHttpsProbeTransport._secure_context()
    transport = PinnedHttpsProbeTransport(
        connection_factory=_FakeHttpsConnection,
        ssl_context_factory=lambda: context,
    )
    probe_context = probe_module.ModelProviderProbeContext(
        "teacher-default",
        "openai",
        "gpt-5-mini",
        "openai_official_https_v1",
        "https://api.openai.com/v1",
        "rev_" + "a" * 32,
        SECRET,
    )
    request = probe_module._build_request(probe_context)
    response = transport.send(
        request,
        cancel_event=threading.Event(),
        deadline_monotonic=time.monotonic() + 10,
    )
    connection = _FakeHttpsConnection.instances[-1]
    assert connection.host == "api.openai.com"
    assert connection.port == 443
    assert connection.timeout <= 4
    assert connection.path == "/v1/responses"
    headers = dict(connection.headers)
    assert headers["Accept-Encoding"] == "identity"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert headers["Connection"] == "close"
    assert context.check_hostname is True
    assert context.verify_mode == probe_module.ssl.CERT_REQUIRED
    assert context.minimum_version >= probe_module.ssl.TLSVersion.TLSv1_2
    assert response.model_invoked is True
    assert SECRET not in repr(connection)


@pytest.mark.parametrize(
    ("http_status", "error_code"),
    [
        (301, "redirect_refused"),
        (401, "invalid_credentials"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
    ],
)
def test_real_transport_http_status_mapping_is_stable(
    http_status: int, error_code: str
) -> None:
    error = PinnedHttpsProbeTransport._status_error(http_status)
    assert error is not None
    assert error.code == error_code
    assert error.http_status == http_status
    assert error.model_invoked is True
    assert str(http_status) not in str(error)


@pytest.mark.parametrize(
    ("endpoint_scope", "resolved_ip"),
    [
        ("public_https", "10.0.0.8"),
        ("public_https", "127.0.0.1"),
        ("loopback", "203.0.113.10"),
    ],
)
def test_dns_resolution_cannot_escape_saved_endpoint_scope(
    monkeypatch: pytest.MonkeyPatch,
    endpoint_scope: str,
    resolved_ip: str,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (resolved_ip, 443),
            )
        ],
    )
    with pytest.raises(ModelProviderProbeError) as caught:
        PinnedHttpsProbeTransport._resolve(
            "gateway.example.test",
            443,
            1.0,
            endpoint_scope,
        )
    assert caught.value.code == "endpoint_address_not_allowed"


@pytest.mark.parametrize(
    ("raised", "error_code"),
    [
        (socket.gaierror(), "dns_failure"),
        (ssl.SSLError(), "tls_failure"),
        (TimeoutError(), "timeout"),
        (OSError(), "network_unavailable"),
    ],
)
def test_real_transport_network_exceptions_are_sanitized(
    raised: BaseException, error_code: str
) -> None:
    class FailingConnection:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.sock = None

        def connect(self) -> None:
            raise raised

        def close(self) -> None:
            return None

    context = probe_module.ModelProviderProbeContext(
        "teacher-default",
        "openai",
        "gpt-5-mini",
        "openai_official_https_v1",
        "https://api.openai.com/v1",
        "rev_" + "a" * 32,
        SECRET,
    )
    transport = PinnedHttpsProbeTransport(connection_factory=FailingConnection)
    with pytest.raises(ModelProviderProbeError) as caught:
        transport.send(
            probe_module._build_request(context),
            cancel_event=threading.Event(),
            deadline_monotonic=time.monotonic() + 10,
        )
    assert caught.value.code == error_code
    assert caught.value.__cause__ is None
