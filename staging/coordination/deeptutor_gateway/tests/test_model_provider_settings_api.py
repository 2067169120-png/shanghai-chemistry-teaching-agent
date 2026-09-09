from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY,
    ModelProviderModelListProbe,
    ModelProviderProbeError,
    ModelProviderSyntheticProbeManager,
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY,
    SETTINGS_FILE_NAME,
    ModelProviderSettingsStore,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    STUDENT_A,
    STUDENT_B,
    TOKEN_A,
    TOKEN_B,
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "model-provider-student-token-0123456789"
SETTINGS_ONLY_TOKEN = "model-provider-settings-only-token-0123456789"
PROFILE_ID = "teacher-default"
SETTINGS_ROUTE = "/api/v1/settings/model-providers"
PROFILE_ROUTE = f"{SETTINGS_ROUTE}/{PROFILE_ID}"
CREDENTIAL_ROUTE = f"{PROFILE_ROUTE}/credential"
TEST_ROUTE = f"{PROFILE_ROUTE}/test"
MODELS_ROUTE = f"{PROFILE_ROUTE}/models"


class FakeCredentialBackend:
    """In-memory credential backend; no test may touch the host key store."""

    def __init__(self, *, available: bool = True) -> None:
        self.is_available = available
        self.secrets: dict[str, str] = {}
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.deletes: list[str] = []
        self.fail_write = False

    def available(self) -> bool:
        return self.is_available

    def write(self, target_name: str, secret: str) -> None:
        self.writes.append(target_name)
        if self.fail_write:
            # The production boundary must suppress even a hostile backend error
            # containing the submitted credential.
            raise RuntimeError(f"backend failure included secret: {secret}")
        self.secrets[target_name] = secret

    def read(self, target_name: str) -> str | None:
        self.reads.append(target_name)
        return self.secrets.get(target_name)

    def exists(self, target_name: str) -> bool:
        return target_name in self.secrets

    def delete(self, target_name: str) -> bool:
        self.deletes.append(target_name)
        return self.secrets.pop(target_name, None) is not None


class FakeSuccessProbeTransport:
    """Deterministic provider stand-in; API tests never open a socket."""

    def __init__(self) -> None:
        self.calls = 0
        self.request_reprs: list[str] = []

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del deadline_monotonic
        self.calls += 1
        self.request_reprs.append(repr(request))
        if cancel_event.is_set():
            raise ModelProviderProbeError(
                "cancelled",
                "synthetic probe was cancelled",
                409,
                connection_state="cancelled",
            )
        body = json.dumps(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": '{"status":"ok"}'}
                        ]
                    }
                ],
                "usage": {
                    "input_tokens": 17,
                    "output_tokens": 5,
                    "total_tokens": 22,
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=body,
            latency_ms=7,
            model_invoked=True,
        )


class FakeBlockingProbeTransport(FakeSuccessProbeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        self.started.set()
        while not self.release.wait(0.005):
            if cancel_event.is_set():
                raise ModelProviderProbeError(
                    "cancelled",
                    "synthetic probe was cancelled",
                    409,
                    model_invoked=True,
                    connection_state="cancelled",
                )
        return super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )


class FakeModelListTransport:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        del deadline_monotonic
        assert cancel_event.is_set() is False
        assert request.method == "GET"
        assert request.request_kind == "model_list"
        assert request.body == b""
        self.requests.append(request)
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json; charset=utf-8",
            content_encoding=None,
            body=json.dumps(
                {
                    "object": "list",
                    "data": [
                        {"id": "experimental-zeta", "capabilities": ["vision"]},
                        {"id": "experimental-alpha"},
                        {"id": "experimental-alpha"},
                    ],
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            latency_ms=3,
            model_invoked=False,
        )


class FakeInvalidModelListTransport(FakeModelListTransport):
    def send(self, request: Any, *, cancel_event: Any, deadline_monotonic: float):
        response = super().send(
            request,
            cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic,
        )
        return ProbeTransportResponse(
            http_status=response.http_status,
            content_type=response.content_type,
            content_encoding=response.content_encoding,
            body=b'{"data":[{"id":"https://evil.invalid?token=x"}]}',
            latency_ms=response.latency_ms,
            model_invoked=False,
        )


class _FakeProductRegistry:
    """Small deterministic stand-in proving provider failure does not take KB down."""

    activated = True

    @staticmethod
    def readiness() -> dict[str, Any]:
        return {
            "status": "ready_candidate_browse",
            "ready_for_candidate_browse": True,
            "offline_browse": True,
            "blockers": [
                {
                    "code": "model_provider_not_configured",
                    "blocking": False,
                }
            ],
        }

    @staticmethod
    def registry() -> dict[str, Any]:
        return {
            "registry_id": "fake-provider-isolation-registry",
            "product_count": 3,
            "cross_scope_sum_allowed": False,
        }


def _browser_headers(server: Any) -> dict[str, str]:
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    return {"Origin": origin, "Sec-Fetch-Site": "same-origin"}


def _profile_payload(*, expected_revision: str | None = None) -> dict[str, Any]:
    return {
        "provider_id": "openai",
        "model_id": "gpt-5-mini",
        "base_url_policy": "openai_official_https_v1",
        "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
        "image_egress": "deny",
        "expected_revision": expected_revision,
    }


@contextmanager
def _provider_server(
    *,
    backend: FakeCredentialBackend | None = None,
    install_store: bool = True,
    transport: FakeSuccessProbeTransport | None = None,
) -> Iterator[tuple[Any, Path, Path, FakeCredentialBackend]]:
    """Start an isolated server with external temporary metadata and fake secrets."""

    fake = backend or FakeCredentialBackend()
    with tempfile.TemporaryDirectory() as temp_name:
        temp_root = Path(temp_name)
        state_root = temp_root / "private-state"
        metadata_root = temp_root / "external-model-metadata"
        config = build_config(state_root)
        config.model_provider_metadata_root = None
        config.principals = [
            Principal(
                "teacher-a",
                "teacher",
                token_digest(TOKEN_A),
                (STUDENT_A,),
                (
                    MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY,
                    MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY,
                ),
            ),
            Principal(
                "teacher-b",
                "teacher",
                token_digest(TOKEN_B),
                (STUDENT_B,),
                (),
            ),
            Principal(
                "provider-settings-student",
                "student",
                token_digest(STUDENT_TOKEN),
                (),
                (),
            ),
            Principal(
                "teacher-settings-only",
                "teacher",
                token_digest(SETTINGS_ONLY_TOKEN),
                (),
                (MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY,),
            ),
        ]
        config.validate()
        with running_server(config) as server:
            server.service.workbench_product_registry = _FakeProductRegistry()
            if install_store:
                store = ModelProviderSettingsStore(
                    metadata_root,
                    project_root=WORKSPACE,
                    credential_backend=fake,
                )
                server.service.model_provider_settings = store
                probe_transport = transport or FakeSuccessProbeTransport()
                server.service.model_provider_probe_manager = (
                    ModelProviderSyntheticProbeManager(
                        store,
                        transport=probe_transport,
                    )
                )
                model_list_transport = FakeModelListTransport()
                server.service.model_provider_model_list_client = (
                    ModelProviderModelListProbe(
                        store,
                        transport=model_list_transport,
                    )
                )
                server.fake_model_provider_model_list_transport = model_list_transport
                server.fake_model_provider_probe_transport = probe_transport
                server.service.model_provider_settings_error = None
            else:
                server.service.model_provider_settings = None
                server.service.model_provider_probe_manager = None
                server.service.model_provider_settings_error = (
                    "credential_store_unavailable"
                )
            yield server, state_root, metadata_root, fake


def _raw_request(
    server: Any,
    method: str,
    path: str,
    *,
    token: str | None = TOKEN_A,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=10
    )
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, raw, response_headers


def _data(body: dict[str, Any]) -> dict[str, Any]:
    return body["data"]


def _error_code(body: dict[str, Any]) -> str:
    return body["error"]["code"]


def _assert_secret_absent(secret: str, *values: object) -> None:
    for value in values:
        serialized = (
            value
            if isinstance(value, bytes)
            else json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        assert secret.encode("utf-8") not in serialized


def _assert_tree_does_not_contain(root: Path, secret: str) -> None:
    if not root.exists():
        return
    needle = secret.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), path


def _create_profile(server: Any) -> dict[str, Any]:
    status, body, _ = request(
        server,
        "PUT",
        PROFILE_ROUTE,
        payload=_profile_payload(),
        headers=_browser_headers(server),
    )
    assert status == 200, body
    return _data(body)["profile"]


def _current_profile(server: Any) -> dict[str, Any]:
    status, body, _ = request(
        server,
        "GET",
        SETTINGS_ROUTE,
        headers=_browser_headers(server),
    )
    assert status == 200, body
    profiles = _data(body)["profiles"]
    assert len(profiles) == 1
    return profiles[0]


def _wait_probe_terminal(server: Any, probe_run_id: str) -> dict[str, Any]:
    route = f"{TEST_ROUTE}/{probe_run_id}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, body, _ = request(
            server,
            "GET",
            route,
            headers=_browser_headers(server),
        )
        assert status == 200, body
        probe = _data(body)
        if probe["status"] in {"succeeded", "failed", "cancelled", "stale"}:
            return probe
        time.sleep(0.01)
    raise AssertionError("synthetic probe did not reach a terminal state")


def test_get_requires_teacher_and_trusted_loopback_but_not_write_capability():
    with _provider_server() as (server, _state, _metadata, _backend):
        headers = _browser_headers(server)

        status, body, _ = request(
            server, "GET", SETTINGS_ROUTE, token=TOKEN_A, headers=headers
        )
        assert status == 200
        assert _data(body)["offline_workbench_available"] is True
        assert _data(body)["production_model_invocation_enabled"] is False
        assert _data(body)["synthetic_probe"] == {
            "prompt": {
                "text": 'Connectivity check only. Return exactly this JSON object: {"status":"ok"}. Do not add any other text.',
                "sha256": "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f",
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
        assert _data(body)["credential_storage"] == {
            "kind": "windows_credential_manager_generic",
            "current_user": True,
            "roaming": False,
            "project_file_storage": False,
            "browser_storage": False,
            "secret_values_exposed": False,
        }

        # A teacher may inspect non-secret status without the write capability.
        status, body, _ = request(
            server, "GET", SETTINGS_ROUTE, token=TOKEN_B, headers=headers
        )
        assert status == 200, body

        status, body, _ = request(
            server, "GET", SETTINGS_ROUTE, token=STUDENT_TOKEN, headers=headers
        )
        assert status == 403
        assert _error_code(body) == "teacher_scope_required"

        status, body, _ = request(server, "GET", SETTINGS_ROUTE, token=TOKEN_A)
        assert status == 403
        assert _error_code(body) == "origin_required"

        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            token=TOKEN_B,
            payload=_profile_payload(),
            headers=headers,
        )
        assert status == 403
        assert _error_code(body) == (
            "model_provider_settings_write_capability_required"
        )


def test_synthetic_probe_requires_its_independent_execute_capability():
    with _provider_server() as (server, _state, _metadata, _backend):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-INDEPENDENT-CAPABILITY-123456789",
                "expected_revision": profile["revision"],
            },
            headers=_browser_headers(server),
        )
        assert status == 200, body
        revision = _data(body)["profile"]["revision"]

        status, body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            token=SETTINGS_ONLY_TOKEN,
            payload={
                "expected_revision": revision,
                "idempotency_key": "probe_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            headers=_browser_headers(server),
        )
        assert status == 403
        assert _error_code(body) == (
            "model_provider_synthetic_probe_execute_capability_required"
        )
        assert server.fake_model_provider_probe_transport.calls == 0


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"provider_id": "arbitrary-provider"}, "provider_not_allowed"),
        ({"base_url_policy": "user_supplied_url"}, "base_url_policy_not_allowed"),
        ({"base_url": "https://example.invalid/v1"}, "model_provider_profile_request_invalid"),
        ({"allowed_data_classes": ["synthetic_only", "raw_student_image"]}, "data_policy_invalid"),
        ({"image_egress": "allow_everything"}, "data_policy_invalid"),
    ],
)
def test_profile_fields_and_provider_model_base_policy_are_closed(
    mutation: dict[str, Any], expected_code: str
):
    with _provider_server() as (server, _state, _metadata, _backend):
        payload = _profile_payload()
        payload.update(mutation)
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=payload,
            headers=_browser_headers(server),
        )
        assert status == 400
        assert _error_code(body) == expected_code


def test_preset_unknown_model_is_saved_as_unverified_custom_model():
    with _provider_server() as (server, _state, _metadata, _backend):
        payload = _profile_payload()
        payload["model_id"] = "gpt-6-experimental-2026-08"
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=payload,
            headers=_browser_headers(server),
        )
        assert status == 200, body
        profile = _data(body)["profile"]
        assert profile["model_id"] == "gpt-6-experimental-2026-08"
        assert profile["model_status"] == "unverified_custom_model"
        assert profile["capabilities"] == ["text"]
        assert profile["effective_capabilities"] == ["text"]
        assert profile["capability_evidence"] == {
            "declared": ["text"],
            "catalog": [],
            "probed": [],
            "unknown": ["vision", "structured_output"],
        }
        assert _data(body)["secret_received"] is False

        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-UNKNOWN-PRESET-123456789",
                "expected_revision": profile["revision"],
            },
            headers=_browser_headers(server),
        )
        configured = _data(body)["profile"]
        probe_id = "probe_99999999999999999999999999999999"
        status, body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": configured["revision"],
                "idempotency_key": probe_id,
            },
            headers=_browser_headers(server),
        )
        assert status == 202, body
        terminal = _wait_probe_terminal(server, probe_id)
        assert terminal["status"] == "succeeded"
        after_probe = _current_profile(server)
        assert after_probe["model_status"] == "unverified_custom_model"
        assert after_probe["capability_evidence"]["probed"] == [
            "text",
            "structured_output",
        ]
        assert after_probe["effective_capabilities"] == [
            "text",
            "structured_output",
        ]


def test_custom_openai_compatible_https_profile_is_normalized_and_keyless():
    with _provider_server() as (server, _state, metadata_root, backend):
        payload = {
            "provider_kind": "openai_compatible",
            "display_name": "  实验模型网关  ",
            "base_url": "https://Gateway.Example.test:443/v1/",
            "api_style": "chat_completions",
            "model_id": "lab/model:preview",
            "capabilities": ["text", "structured_output"],
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
            "expected_revision": None,
        }
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=payload,
            headers=_browser_headers(server),
        )
        assert status == 200, body
        profile = _data(body)["profile"]
        assert profile["provider_kind"] == "openai_compatible"
        assert profile["provider_id"] == "openai_compatible"
        assert profile["display_name"] == "实验模型网关"
        assert profile["base_url"] == "https://gateway.example.test/v1"
        assert profile["base_url_policy"] == "openai_compatible_public_https_v1"
        assert profile["api_style"] == "chat_completions"
        assert profile["follow_redirects"] is False
        assert profile["credential_state"] == "not_configured"
        assert backend.secrets == {}
        _assert_tree_does_not_contain(metadata_root, "Authorization")


@pytest.mark.parametrize(
    ("base_url", "local_policy", "expected_code"),
    [
        ("http://gateway.example.test/v1", None, "base_url_https_required"),
        ("https://user:pass@gateway.example.test/v1", None, "base_url_invalid"),
        ("https://gateway.example.test/v1?token=x", None, "base_url_invalid"),
        ("https://gateway.example.test/v1#fragment", None, "base_url_invalid"),
        ("https://gateway.example.test/v1%2fmodels", None, "base_url_invalid"),
        ("http://127.0.0.1:1234/v1", None, "local_endpoint_not_allowed"),
        ("https://10.0.0.8/v1", None, "private_endpoint_not_allowed"),
        (
            "https://gateway.example.test/v1",
            "allow_loopback_http",
            "local_endpoint_policy_invalid",
        ),
    ],
)
def test_custom_provider_rejects_unsafe_or_implicit_endpoint_policy(
    base_url: str,
    local_policy: str | None,
    expected_code: str,
):
    with _provider_server() as (server, _state, _metadata, _backend):
        payload = {
            "provider_kind": "openai_compatible",
            "display_name": "实验网关",
            "base_url": base_url,
            "api_style": "responses",
            "model_id": "model-preview",
            "capabilities": ["text", "structured_output"],
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
            "expected_revision": None,
        }
        if local_policy is not None:
            payload["local_endpoint_policy"] = local_policy
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=payload,
            headers=_browser_headers(server),
        )
        assert status == 400
        assert _error_code(body) == expected_code


def test_explicit_loopback_openai_compatible_profile_is_controlled():
    with _provider_server() as (server, _state, _metadata, _backend):
        payload = {
            "provider_kind": "openai_compatible",
            "display_name": "本机模型",
            "base_url": "http://127.0.0.1:1234/v1/",
            "api_style": "chat_completions",
            "local_endpoint_policy": "allow_loopback_http",
            "model_id": "local-model:latest",
            "capabilities": ["text"],
            "allowed_data_classes": ["synthetic_only"],
            "image_egress": "deny",
            "expected_revision": None,
        }
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=payload,
            headers=_browser_headers(server),
        )
        assert status == 200, body
        profile = _data(body)["profile"]
        assert profile["base_url"] == "http://127.0.0.1:1234/v1"
        assert profile["base_url_policy"] == "openai_compatible_loopback_v1"
        assert profile["endpoint_scope"] == "loopback"


def test_profile_and_credential_lifecycle_never_leaks_api_key():
    secret = "sk-LEAK-SENTINEL-model-provider-123456789"
    hostile_secret = "sk-HOSTILE-BACKEND-LEAK-987654321"
    with _provider_server() as (server, state_root, metadata_root, backend):
        profile = _create_profile(server)
        assert profile["credential_state"] == "not_configured"
        assert profile["model_configured"] is False
        first_revision = profile["revision"]

        status, credential_body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={"api_key": secret, "expected_revision": first_revision},
            headers=_browser_headers(server),
        )
        assert status == 200, credential_body
        configured = _data(credential_body)["profile"]
        assert configured["credential_state"] == "configured"
        assert configured["model_configured"] is True
        assert _data(credential_body)["secret_received"] is True
        assert _data(credential_body)["secret_exposed"] is False
        assert secret in backend.secrets.values()

        status, list_body, _ = request(
            server,
            "GET",
            SETTINGS_ROUTE,
            headers=_browser_headers(server),
        )
        assert status == 200
        assert _data(list_body)["profiles"][0]["credential_state"] == "configured"
        _assert_secret_absent(secret, credential_body, list_body)
        _assert_tree_does_not_contain(metadata_root, secret)
        _assert_tree_does_not_contain(state_root, secret)
        assert (metadata_root / SETTINGS_FILE_NAME).is_file()
        audit_path = state_root / "audit" / "gateway.jsonl"
        assert audit_path.is_file()
        assert secret.encode("utf-8") not in audit_path.read_bytes()

        # Even if an injected backend raises an exception containing the key,
        # the HTTP error, settings file, and append-only gateway audit are clean.
        backend.fail_write = True
        status, error_body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": hostile_secret,
                "expected_revision": configured["revision"],
            },
            headers=_browser_headers(server),
        )
        assert status == 503
        assert _error_code(error_body) == "credential_operation_failed"
        _assert_secret_absent(hostile_secret, error_body)
        _assert_tree_does_not_contain(metadata_root, hostile_secret)
        _assert_tree_does_not_contain(state_root, hostile_secret)
        assert hostile_secret.encode("utf-8") not in audit_path.read_bytes()


def test_model_list_probe_returns_only_ids_and_never_changes_profile():
    secret = "sk-MODEL-LIST-ONLY-123456789"
    with _provider_server() as (server, state_root, metadata_root, _backend):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={"api_key": secret, "expected_revision": profile["revision"]},
            headers=_browser_headers(server),
        )
        assert status == 200, body
        configured = _data(body)["profile"]
        before_revision = configured["revision"]
        before_probe = configured["last_probe"]

        status, body, _ = request(
            server,
            "POST",
            MODELS_ROUTE,
            payload={"expected_revision": before_revision},
            headers=_browser_headers(server),
        )
        assert status == 200, body
        assert _data(body) == {
            "model_ids": ["experimental-alpha", "experimental-zeta"],
            "capabilities_not_inferred": True,
        }
        transport = server.fake_model_provider_model_list_transport
        assert len(transport.requests) == 1
        assert transport.requests[0].path == "/v1/models"
        assert transport.requests[0].method == "GET"
        assert secret not in repr(transport.requests[0])

        current = _current_profile(server)
        assert current["revision"] == before_revision
        assert current["last_probe"] == before_probe
        assert current["capability_evidence"]["probed"] == []
        _assert_secret_absent(secret, body, current)
        _assert_tree_does_not_contain(metadata_root, secret)
        _assert_tree_does_not_contain(state_root, secret)


def test_model_list_probe_requires_explicit_capability_and_current_revision():
    with _provider_server() as (server, _state, _metadata, _backend):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-MODELS-PERMISSION-123456789",
                "expected_revision": profile["revision"],
            },
            headers=_browser_headers(server),
        )
        revision = _data(body)["profile"]["revision"]

        status, body, _ = request(
            server,
            "POST",
            MODELS_ROUTE,
            token=SETTINGS_ONLY_TOKEN,
            payload={"expected_revision": revision},
            headers=_browser_headers(server),
        )
        assert status == 403
        assert _error_code(body) == (
            "model_provider_synthetic_probe_execute_capability_required"
        )

        status, body, _ = request(
            server,
            "POST",
            MODELS_ROUTE,
            payload={"expected_revision": "rev_" + "0" * 32},
            headers=_browser_headers(server),
        )
        assert status == 409
        assert _error_code(body) == "revision_conflict"
        assert server.fake_model_provider_model_list_transport.requests == []


def test_failed_model_list_discovery_does_not_block_manual_model_id_update():
    with _provider_server() as (server, _state, _metadata, _backend):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-MODELS-FAILURE-123456789",
                "expected_revision": profile["revision"],
            },
            headers=_browser_headers(server),
        )
        configured = _data(body)["profile"]
        invalid_transport = FakeInvalidModelListTransport()
        server.service.model_provider_model_list_client = ModelProviderModelListProbe(
            server.service.model_provider_settings,
            transport=invalid_transport,
        )

        status, body, _ = request(
            server,
            "POST",
            MODELS_ROUTE,
            payload={"expected_revision": configured["revision"]},
            headers=_browser_headers(server),
        )
        assert status == 502
        assert _error_code(body) == "response_invalid"
        unchanged = _current_profile(server)
        assert unchanged["revision"] == configured["revision"]
        assert unchanged["last_probe"] == configured["last_probe"]

        manual = _profile_payload(expected_revision=unchanged["revision"])
        manual["model_id"] = "manually-entered-preview-model"
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=manual,
            headers=_browser_headers(server),
        )
        assert status == 200, body
        saved = _data(body)["profile"]
        assert saved["model_id"] == "manually-entered-preview-model"
        assert saved["model_status"] == "unverified_custom_model"


def test_fixed_synthetic_probe_uses_fake_transport_and_delete_fails_closed():
    secret = "sk-SYNTHETIC-ONLY-123456789"
    with _provider_server() as (server, state_root, metadata_root, backend):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={"api_key": secret, "expected_revision": profile["revision"]},
            headers=_browser_headers(server),
        )
        assert status == 200
        configured = _data(body)["profile"]

        probe_run_id = "probe_0123456789abcdef0123456789abcdef"
        status, test_body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": configured["revision"],
                "idempotency_key": probe_run_id,
            },
            headers=_browser_headers(server),
        )
        assert status == 202, test_body
        start = _data(test_body)
        assert start["idempotent_replay"] is False
        assert start["probe"]["probe_run_id"] == probe_run_id
        assert start["probe"]["production_model_invocation_enabled"] is False
        assert start["probe"]["egress"] == {
            "prompt_sha256": "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f",
            "prompt_bytes": 101,
            "data_class": "synthetic_only",
            "question_data_sent": False,
            "student_data_sent": False,
            "commercial_material_sent": False,
            "image_sent": False,
        }

        terminal = _wait_probe_terminal(server, probe_run_id)
        assert terminal["status"] == "succeeded"
        assert terminal["model_invoked"] is True
        assert terminal["usage"] == {
            "input_tokens": 17,
            "output_tokens": 5,
            "total_tokens": 22,
        }
        assert terminal["receipt_id"].startswith("probe-receipt-")
        assert len(terminal["receipt_sha256"]) == 64
        assert server.fake_model_provider_probe_transport.calls == 1
        assert all(secret not in value for value in server.fake_model_provider_probe_transport.request_reprs)

        # The same browser idempotency key replays the same run without a
        # second provider invocation.
        status, replay_body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": configured["revision"],
                "idempotency_key": probe_run_id,
            },
            headers=_browser_headers(server),
        )
        assert status == 202, replay_body
        assert _data(replay_body)["idempotent_replay"] is True
        assert _data(replay_body)["probe"]["probe_run_id"] == probe_run_id
        assert server.fake_model_provider_probe_transport.calls == 1

        current = _current_profile(server)
        assert current["last_probe"]["status"] == "succeeded"
        assert current["last_probe"]["probe_run_id"] == probe_run_id

        status, delete_body, _ = request(
            server,
            "DELETE",
            CREDENTIAL_ROUTE,
            payload={"expected_revision": current["revision"]},
            headers=_browser_headers(server),
        )
        assert status == 200
        deleted = _data(delete_body)["profile"]
        assert _data(delete_body)["secret_deleted"] is True
        assert deleted["credential_state"] == "not_configured"
        assert deleted["model_configured"] is False
        assert not backend.secrets

        status, body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": deleted["revision"],
                "idempotency_key": "probe_fedcba9876543210fedcba9876543210",
            },
            headers=_browser_headers(server),
        )
        assert status == 409
        assert _error_code(body) == "model_not_configured"
        _assert_tree_does_not_contain(metadata_root, secret)
        _assert_tree_does_not_contain(state_root, secret)


def test_probe_cancel_and_credential_rotation_make_old_result_non_current():
    transport = FakeBlockingProbeTransport()
    with _provider_server(transport=transport) as (
        server,
        _state_root,
        _metadata_root,
        backend,
    ):
        profile = _create_profile(server)
        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-ROTATION-OLD-123456789",
                "expected_revision": profile["revision"],
            },
            headers=_browser_headers(server),
        )
        assert status == 200, body
        configured = _data(body)["profile"]
        run_id = "probe_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        status, body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": configured["revision"],
                "idempotency_key": run_id,
            },
            headers=_browser_headers(server),
        )
        assert status == 202, body
        assert transport.started.wait(2)

        status, body, _ = request(
            server,
            "POST",
            f"{TEST_ROUTE}/{run_id}/cancel",
            payload={"expected_revision": configured["revision"]},
            headers=_browser_headers(server),
        )
        assert status == 200, body
        assert _data(body)["status"] in {"cancel_requested", "cancelled"}
        cancelled = _wait_probe_terminal(server, run_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["connection_state"] == "cancelled"
        assert cancelled["error_code"] == "cancelled"
        assert cancelled["receipt_id"].startswith("probe-receipt-")

        # A second in-flight probe is invalidated by a Key rotation.  Its
        # terminal status may be physically written as a receipt, but the
        # settings CAS must mark it stale and keep it out of last_probe.
        current = _current_profile(server)
        second_id = "probe_cccccccccccccccccccccccccccccccc"
        transport.started.clear()
        status, body, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": current["revision"],
                "idempotency_key": second_id,
            },
            headers=_browser_headers(server),
        )
        assert status == 202, body
        assert transport.started.wait(2)

        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-ROTATION-NEW-987654321",
                "expected_revision": current["revision"],
            },
            headers=_browser_headers(server),
        )
        assert status == 200, body
        rotated = _data(body)["profile"]
        assert rotated["last_probe"]["status"] == "ready_not_invoked"
        assert "sk-ROTATION-NEW-987654321" in backend.secrets.values()

        stale = _wait_probe_terminal(server, second_id)
        assert stale["status"] == "stale"
        assert stale["connection_state"] == "stale"
        assert stale["error_code"] == "stale_result"
        after = _current_profile(server)
        assert after["revision"] == rotated["revision"]
        assert after["last_probe"]["status"] == "ready_not_invoked"


def test_revision_token_prevents_stale_and_aba_writes():
    with _provider_server() as (server, _state, _metadata, _backend):
        profile_a = _create_profile(server)
        revision_a = profile_a["revision"]

        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=_profile_payload(expected_revision=revision_a),
            headers=_browser_headers(server),
        )
        assert status == 200
        revision_b = _data(body)["profile"]["revision"]
        assert revision_b != revision_a

        # Return to the same logical values: the revision still advances, so a
        # client holding A cannot overwrite the intervening B/A sequence.
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=_profile_payload(expected_revision=revision_b),
            headers=_browser_headers(server),
        )
        assert status == 200
        revision_a2 = _data(body)["profile"]["revision"]
        assert revision_a2 not in {revision_a, revision_b}

        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=_profile_payload(expected_revision=revision_a),
            headers=_browser_headers(server),
        )
        assert status == 409
        assert _error_code(body) == "revision_conflict"

        status, body, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-STALE-REVISION-123456789",
                "expected_revision": revision_b,
            },
            headers=_browser_headers(server),
        )
        assert status == 409
        assert _error_code(body) == "revision_conflict"


def test_query_origin_content_type_and_duplicate_json_fail_closed():
    with _provider_server() as (server, _state, _metadata, _backend):
        good_headers = _browser_headers(server)

        status, body, _ = request(
            server, "GET", f"{SETTINGS_ROUTE}?x=", headers=good_headers
        )
        assert status == 400
        assert _error_code(body) == "model_provider_settings_query_unsupported"

        status, body, _ = request(
            server,
            "PUT",
            f"{PROFILE_ROUTE}?x=1",
            payload=_profile_payload(),
            headers=good_headers,
        )
        assert status == 400
        assert _error_code(body) == "model_provider_settings_query_unsupported"

        for method, route, payload in (
            (
                "PUT",
                f"{CREDENTIAL_ROUTE}?x=1",
                {"api_key": "sk-QUERY-REJECT-123456789", "expected_revision": None},
            ),
            ("DELETE", f"{CREDENTIAL_ROUTE}?x=1", {"expected_revision": None}),
            (
                "POST",
                f"{TEST_ROUTE}?x=1",
                {
                    "expected_revision": "rev_00000000000000000000000000000000",
                    "idempotency_key": "probe_00000000000000000000000000000000",
                },
            ),
            (
                "POST",
                f"{MODELS_ROUTE}?x=1",
                {"expected_revision": "rev_00000000000000000000000000000000"},
            ),
            (
                "POST",
                f"{TEST_ROUTE}/probe_00000000000000000000000000000000/cancel?x=1",
                {"expected_revision": "rev_00000000000000000000000000000000"},
            ),
        ):
            status, body, _ = request(
                server,
                method,
                route,
                payload=payload,
                headers=good_headers,
            )
            assert status == 400
            assert _error_code(body) == "model_provider_settings_query_unsupported"

        status, body, _ = request(
            server,
            "GET",
            f"{TEST_ROUTE}/probe_00000000000000000000000000000000?x=1",
            headers=good_headers,
        )
        assert status == 400
        assert _error_code(body) == "model_provider_settings_query_unsupported"

        missing_field = _profile_payload()
        missing_field.pop("expected_revision")
        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=missing_field,
            headers=good_headers,
        )
        assert status == 400
        assert _error_code(body) == "model_provider_profile_request_invalid"

        status, body, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=_profile_payload(),
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403
        assert _error_code(body) in {"origin_denied", "csrf_origin_denied"}

        raw_profile = json.dumps(_profile_payload()).encode("utf-8")
        raw_headers = {
            **good_headers,
            "Content-Type": "text/plain",
            "Content-Length": str(len(raw_profile)),
        }
        status, raw, _ = _raw_request(
            server, "PUT", PROFILE_ROUTE, body=raw_profile, headers=raw_headers
        )
        assert status == 415
        assert json.loads(raw)["error"]["code"] == "unsupported_content_type"

        duplicate = (
            b'{"provider_id":"openai","provider_id":"deepseek",'
            b'"model_id":"gpt-5-mini",'
            b'"base_url_policy":"openai_official_https_v1",'
            b'"allowed_data_classes":["synthetic_only"],'
            b'"image_egress":"deny","expected_revision":null}'
        )
        duplicate_headers = {
            **good_headers,
            "Content-Type": "application/json",
            "Content-Length": str(len(duplicate)),
        }
        status, raw, _ = _raw_request(
            server, "PUT", PROFILE_ROUTE, body=duplicate, headers=duplicate_headers
        )
        assert status == 400
        assert json.loads(raw)["error"]["code"] == "invalid_json"


def test_no_key_or_unavailable_store_keeps_get_readiness_and_kb_registry_online():
    with _provider_server() as (server, _state, _metadata, _backend):
        profile = _create_profile(server)
        assert profile["credential_state"] == "not_configured"
        assert profile["offline_workbench_available"] is True

        for route in (
            SETTINGS_ROUTE,
            "/api/v1/readiness",
            "/api/v1/workbench/product-registry",
        ):
            status, body, _ = request(
                server, "GET", route, headers=_browser_headers(server)
            )
            assert status == 200, (route, body)
        assert _data(body)["cross_scope_sum_allowed"] is False

    with _provider_server(install_store=False) as (
        server,
        _state,
        _metadata,
        _backend,
    ):
        status, body, _ = request(
            server, "GET", SETTINGS_ROUTE, headers=_browser_headers(server)
        )
        assert status == 200
        assert _data(body)["available"] is False
        assert _data(body)["offline_workbench_available"] is True
        assert _data(body)["blockers"] == ["credential_store_unavailable"]

        for route in (
            "/api/v1/readiness",
            "/api/v1/workbench/product-registry",
        ):
            status, response, _ = request(
                server, "GET", route, headers=_browser_headers(server)
            )
            assert status == 200, (route, response)


def test_cors_preflight_allows_only_the_implemented_http_methods():
    with _provider_server() as (server, _state, _metadata, _backend):
        origin = _browser_headers(server)["Origin"]
        status, raw, headers = _raw_request(
            server,
            "OPTIONS",
            SETTINGS_ROUTE,
            token=None,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        assert status == 204
        assert raw == b""
        assert headers["Access-Control-Allow-Origin"] == origin
        assert headers["Access-Control-Allow-Methods"] == (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        assert "PATCH" in headers["Access-Control-Allow-Methods"]


def _validate_openapi_instance(
    contract: dict[str, Any], schema_name: str, instance: dict[str, Any]
) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{schema_name}",
        "components": contract["components"],
    }
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, [error.message for error in errors]


def _assert_object_graph_is_closed(
    contract: dict[str, Any], schema: dict[str, Any], seen: set[str]
) -> None:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return
        seen.add(name)
        _assert_object_graph_is_closed(
            contract, contract["components"]["schemas"][name], seen
        )
        return
    if schema.get("type") == "object" or "properties" in schema:
        assert schema.get("additionalProperties") is False, schema
        properties = schema.get("properties", {})
        for child in properties.values():
            _assert_object_graph_is_closed(contract, child, seen)
    if isinstance(schema.get("items"), dict):
        _assert_object_graph_is_closed(contract, schema["items"], seen)
    for keyword in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(keyword, []):
            _assert_object_graph_is_closed(contract, child, seen)


def test_openapi_exposes_probe_lifecycle_with_strict_dtos_and_real_envelopes():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = contract["paths"]
    assert SETTINGS_ROUTE in paths
    assert "/api/v1/settings/model-providers/{profile_id}" in paths
    assert "/api/v1/settings/model-providers/{profile_id}/credential" in paths
    assert "/api/v1/settings/model-providers/{profile_id}/models" in paths
    assert "/api/v1/settings/model-providers/{profile_id}/test" in paths
    assert "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}" in paths
    assert (
        "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}/cancel"
        in paths
    )
    assert set(paths[SETTINGS_ROUTE]) == {"get"}
    assert set(paths["/api/v1/settings/model-providers/{profile_id}"]) == {"put"}
    assert set(paths["/api/v1/settings/model-providers/{profile_id}/credential"]) == {
        "put",
        "delete",
    }
    assert set(paths["/api/v1/settings/model-providers/{profile_id}/models"]) == {
        "post"
    }
    assert set(paths["/api/v1/settings/model-providers/{profile_id}/test"]) == {
        "post"
    }
    assert set(
        paths["/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}"]
    ) == {"get"}
    assert set(
        paths[
            "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}/cancel"
        ]
    ) == {"post"}

    schemas = contract["components"]["schemas"]
    schema_names = {
        "ModelProviderProfileId",
        "ModelProviderProfileUpsertRequest",
        "ModelProviderCredentialPutRequest",
        "ModelProviderRevisionRequest",
        "ModelProviderSyntheticProbeStartRequest",
        "ModelProviderSettingsListEnvelope",
        "ModelProviderProfileWriteEnvelope",
        "ModelProviderCredentialPutEnvelope",
        "ModelProviderCredentialDeleteEnvelope",
        "ModelProviderSyntheticProbeStartEnvelope",
        "ModelProviderSyntheticProbeEnvelope",
        "ModelProviderModelsEnvelope",
        "ModelProviderModelsData",
    }
    assert schema_names <= set(schemas)
    for name in schema_names - {"ModelProviderProfileId"}:
        _assert_object_graph_is_closed(contract, schemas[name], {name})
    for name in (
        "ModelProviderCredentialPutRequest",
        "ModelProviderRevisionRequest",
        "ModelProviderSyntheticProbeStartRequest",
    ):
        schema = schemas[name]
        assert set(schema["required"]) == set(schema["properties"])
    assert len(schemas["ModelProviderProfileUpsertRequest"]["oneOf"]) == 4

    with _provider_server() as (server, _state, _metadata, _backend):
        headers = _browser_headers(server)
        status, list_envelope, _ = request(
            server, "GET", SETTINGS_ROUTE, headers=headers
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderSettingsListEnvelope", list_envelope
        )

        status, profile_envelope, _ = request(
            server,
            "PUT",
            PROFILE_ROUTE,
            payload=_profile_payload(),
            headers=headers,
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderProfileWriteEnvelope", profile_envelope
        )
        revision = _data(profile_envelope)["profile"]["revision"]

        status, credential_envelope, _ = request(
            server,
            "PUT",
            CREDENTIAL_ROUTE,
            payload={
                "api_key": "sk-OPENAPI-VALIDATION-123456789",
                "expected_revision": revision,
            },
            headers=headers,
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderCredentialPutEnvelope", credential_envelope
        )
        revision = _data(credential_envelope)["profile"]["revision"]

        status, models_envelope, _ = request(
            server,
            "POST",
            MODELS_ROUTE,
            payload={"expected_revision": revision},
            headers=headers,
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderModelsEnvelope", models_envelope
        )
        assert _data(models_envelope)["capabilities_not_inferred"] is True

        probe_run_id = "probe_dddddddddddddddddddddddddddddddd"
        status, test_envelope, _ = request(
            server,
            "POST",
            TEST_ROUTE,
            payload={
                "expected_revision": revision,
                "idempotency_key": probe_run_id,
            },
            headers=headers,
        )
        assert status == 202
        _validate_openapi_instance(
            contract, "ModelProviderSyntheticProbeStartEnvelope", test_envelope
        )

        status, probe_envelope, _ = request(
            server,
            "GET",
            f"{TEST_ROUTE}/{probe_run_id}",
            headers=headers,
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderSyntheticProbeEnvelope", probe_envelope
        )

        _wait_probe_terminal(server, probe_run_id)
        revision = _current_profile(server)["revision"]

        status, delete_envelope, _ = request(
            server,
            "DELETE",
            CREDENTIAL_ROUTE,
            payload={"expected_revision": revision},
            headers=headers,
        )
        assert status == 200
        _validate_openapi_instance(
            contract, "ModelProviderCredentialDeleteEnvelope", delete_envelope
        )


def test_openapi_accepts_manual_models_and_rejects_unsafe_custom_urls():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/components/schemas/ModelProviderProfileUpsertRequest",
        "components": contract["components"],
    }
    validator = Draft202012Validator(schema)
    preset = _profile_payload()
    preset["model_id"] = "future/model:preview"
    assert not list(validator.iter_errors(preset))

    custom = {
        "provider_kind": "openai_compatible",
        "display_name": "实验服务",
        "base_url": "https://gateway.example.test/v1",
        "api_style": "responses",
        "model_id": "future/model:preview",
        "capabilities": ["text", "structured_output"],
        "allowed_data_classes": ["synthetic_only"],
        "image_egress": "deny",
        "expected_revision": None,
    }
    assert not list(validator.iter_errors(custom))

    for invalid_url in (
        "http://gateway.example.test/v1",
        "https://user:pass@gateway.example.test/v1",
        "https://gateway.example.test/v1?token=x",
        "https://gateway.example.test/v1#fragment",
        "https://10.0.0.8/v1",
    ):
        invalid = dict(custom)
        invalid["base_url"] = invalid_url
        assert list(validator.iter_errors(invalid)), invalid_url

    loopback = dict(custom)
    loopback.update(
        {
            "base_url": "http://127.0.0.1:1234/v1",
            "local_endpoint_policy": "allow_loopback_http",
            "api_style": "chat_completions",
        }
    )
    assert not list(validator.iter_errors(loopback))
    loopback.pop("local_endpoint_policy")
    assert list(validator.iter_errors(loopback))

    models_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/components/schemas/ModelProviderModelsData",
        "components": contract["components"],
    }
    models_validator = Draft202012Validator(models_schema)
    assert not list(
        models_validator.iter_errors(
            {"model_ids": ["future/model:preview"], "capabilities_not_inferred": True}
        )
    )
    assert list(
        models_validator.iter_errors(
            {
                "model_ids": ["future/model:preview"],
                "capabilities_not_inferred": True,
                "capabilities": {"vision": True},
            }
        )
    )
