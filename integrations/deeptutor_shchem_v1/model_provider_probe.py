from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import ssl
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .model_provider_settings import (
    ModelProviderProbeContext,
    ModelProviderSettingsError,
    ModelProviderSettingsStore,
    _apply_owner_only_permissions,
    _assert_components_not_reparse,
    _assert_existing_path_safe,
)

MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY = (
    "model_provider_synthetic_probe_execute"
)
PROBE_RECEIPT_SCHEMA_VERSION = "shchem.model-provider-synthetic-probe-receipt.v1"

FIXED_SYNTHETIC_PROMPT = (
    'Connectivity check only. Return exactly this JSON object: {"status":"ok"}. '
    "Do not add any other text."
)
FIXED_SYNTHETIC_PROMPT_BYTES = FIXED_SYNTHETIC_PROMPT.encode("utf-8")
FIXED_SYNTHETIC_PROMPT_SHA256 = hashlib.sha256(
    FIXED_SYNTHETIC_PROMPT_BYTES
).hexdigest()

_PROBE_ID = re.compile(r"^probe_[0-9a-f]{32}$")
_MAX_RESPONSE_BYTES = 128 * 1024
_MAX_USAGE_TOKENS = 1_000_000
_CONNECT_TIMEOUT_SECONDS = 4.0
_TOTAL_TIMEOUT_SECONDS = 10.0
_MAX_REQUEST_BYTES = 32 * 1024
_MAX_MODEL_LIST_ITEMS = 2_000
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "stale"})
_ACTIVE = frozenset({"queued", "running", "cancel_requested"})

_COST = {
    "amount": None,
    "currency": None,
    "reason": "not_estimated_without_pinned_price",
}
_EGRESS = {
    "prompt_sha256": FIXED_SYNTHETIC_PROMPT_SHA256,
    "prompt_bytes": len(FIXED_SYNTHETIC_PROMPT_BYTES),
    "data_class": "synthetic_only",
    "question_data_sent": False,
    "student_data_sent": False,
    "commercial_material_sent": False,
    "image_sent": False,
}


def synthetic_probe_contract() -> dict[str, Any]:
    """Return the immutable, non-secret preview shown by settings clients."""

    return {
        "prompt": {
            "text": FIXED_SYNTHETIC_PROMPT,
            "sha256": FIXED_SYNTHETIC_PROMPT_SHA256,
            "bytes": len(FIXED_SYNTHETIC_PROMPT_BYTES),
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


class ModelProviderProbeError(ValueError):
    """Sanitized probe error suitable for an API response or stored code."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        model_invoked: bool = False,
        connection_state: str = "failed",
        http_status: int | None = None,
        response_body_sha256: str | None = None,
        response_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.model_invoked = model_invoked
        self.connection_state = connection_state
        self.http_status = http_status
        self.response_body_sha256 = response_body_sha256
        self.response_bytes = response_bytes

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticProbeRequest:
    provider_id: str
    model_id: str
    host: str
    port: int
    path: str
    body: bytes
    api_key: str
    scheme: str = "https"
    api_style: str = ""
    base_url_policy: str = ""
    endpoint_scope: str = "public_https"
    method: str = "POST"
    request_kind: str = "synthetic_probe"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider_id={self.provider_id!r}, "
            f"model_id={self.model_id!r}, method={self.method!r}, "
            f"host={self.host!r}, path={self.path!r}, "
            "authorization_present=True)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ProbeTransportResponse:
    http_status: int
    content_type: str | None
    content_encoding: str | None
    body: bytes
    latency_ms: int
    model_invoked: bool

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(http_status={self.http_status!r}, "
            f"response_bytes={len(self.body)!r}, model_invoked={self.model_invoked!r})"
        )


class ProbeTransport(Protocol):
    def send(
        self,
        request: SyntheticProbeRequest,
        *,
        cancel_event: threading.Event,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse: ...


class _ResolvedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection using an already bounded DNS result.

    Host remains the official DNS name for the HTTP Host header and TLS SNI;
    only the TCP destination comes from the prior system resolver result.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
        addresses: tuple[tuple[int, int, int, tuple[Any, ...]], ...],
        connect_deadline: float,
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._probe_addresses = addresses
        self._probe_connect_deadline = connect_deadline
        self._probe_monotonic = monotonic

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise OSError("tunneling is disabled")
        last_error: OSError | None = None
        for family, socktype, proto, sockaddr in self._probe_addresses:
            raw: socket.socket | None = None
            try:
                remaining = self._probe_connect_deadline - self._probe_monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                raw = socket.socket(family, socktype, proto)
                raw.settimeout(min(float(self.timeout), remaining))
                if self.source_address:
                    raw.bind(self.source_address)
                raw.connect(sockaddr)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    try:
                        raw.close()
                    except OSError:
                        pass
        if last_error is not None:
            raise last_error
        raise socket.gaierror()


class _ResolvedHTTPConnection(http.client.HTTPConnection):
    """Plain HTTP connection restricted to pre-validated loopback addresses."""

    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: float,
        addresses: tuple[tuple[int, int, int, tuple[Any, ...]], ...],
        connect_deadline: float,
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._probe_addresses = addresses
        self._probe_connect_deadline = connect_deadline
        self._probe_monotonic = monotonic

    def connect(self) -> None:
        if self._tunnel_host is not None:
            raise OSError("tunneling is disabled")
        last_error: OSError | None = None
        for family, socktype, proto, sockaddr in self._probe_addresses:
            raw: socket.socket | None = None
            try:
                remaining = self._probe_connect_deadline - self._probe_monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                raw = socket.socket(family, socktype, proto)
                raw.settimeout(min(float(self.timeout), remaining))
                raw.connect(sockaddr)
                self.sock = raw
                return
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    try:
                        raw.close()
                    except OSError:
                        pass
        if last_error is not None:
            raise last_error
        raise socket.gaierror()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_loads(raw: bytes | str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="strict")
    else:
        text = raw
    return json.loads(
        text,
        object_pairs_hook=reject_duplicate,
        parse_constant=reject_constant,
    )


def _build_request(context: ModelProviderProbeContext) -> SyntheticProbeRequest:
    api_style = context.api_style or (
        "responses" if context.provider_id == "openai" else "chat_completions"
    )
    if api_style not in {"responses", "chat_completions"}:
        raise ModelProviderProbeError(
            "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
        )
    if context.provider_kind == "preset":
        expected = {
            "openai": (
                "https://api.openai.com/v1",
                "openai_official_https_v1",
                "responses",
            ),
            "deepseek": (
                "https://api.deepseek.com",
                "deepseek_official_https_v1",
                "chat_completions",
            ),
        }.get(context.provider_id)
        if expected != (context.base_url, context.base_url_policy, api_style):
            raise ModelProviderProbeError(
                "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
            )
        endpoint_scope = "public_https"
    elif context.provider_kind == "openai_compatible":
        if context.provider_id != "openai_compatible" or context.base_url_policy not in {
            "openai_compatible_public_https_v1",
            "openai_compatible_loopback_v1",
        }:
            raise ModelProviderProbeError(
                "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
            )
        endpoint_scope = (
            "loopback"
            if context.base_url_policy == "openai_compatible_loopback_v1"
            else "public_https"
        )
    else:
        raise ModelProviderProbeError(
            "provider_not_supported", "provider is not supported", 409
        )
    try:
        parsed = urlsplit(context.base_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise ModelProviderProbeError(
            "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
        ) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
        or parsed.scheme == "http" and endpoint_scope != "loopback"
        or context.local_endpoint_policy == "allow_loopback_http"
        and endpoint_scope != "loopback"
        or endpoint_scope == "loopback"
        and context.local_endpoint_policy != "allow_loopback_http"
    ):
        raise ModelProviderProbeError(
            "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
        )
    host = parsed.hostname.casefold()
    path = parsed.path.rstrip("/") + (
        "/responses" if api_style == "responses" else "/chat/completions"
    )
    if api_style == "responses":
        payload = {
            "background": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": FIXED_SYNTHETIC_PROMPT}
                    ],
                }
            ],
            "max_output_tokens": 64,
            "model": context.model_id,
            "store": False,
            "stream": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "shchem_synthetic_probe",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string", "const": "ok"}},
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                }
            },
            "tool_choice": "none",
            "tools": [],
        }
    else:
        payload = {
            "max_tokens": 64,
            "messages": [{"role": "user", "content": FIXED_SYNTHETIC_PROMPT}],
            "model": context.model_id,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if context.provider_id == "deepseek":
            payload["thinking"] = {"type": "disabled"}
    body = _canonical_json_bytes(payload)
    if len(body) > _MAX_REQUEST_BYTES:
        raise ModelProviderProbeError("request_invalid", "synthetic request is invalid")
    return SyntheticProbeRequest(
        provider_id=context.provider_id,
        model_id=context.model_id,
        host=host,
        port=port,
        path=path,
        body=body,
        api_key=context.api_key,
        scheme=parsed.scheme,
        api_style=api_style,
        base_url_policy=context.base_url_policy,
        endpoint_scope=endpoint_scope,
    )


def _build_model_list_request(
    context: ModelProviderProbeContext,
) -> SyntheticProbeRequest:
    probe_request = _build_request(context)
    parsed = urlsplit(context.base_url)
    return SyntheticProbeRequest(
        provider_id=probe_request.provider_id,
        model_id=context.model_id,
        host=probe_request.host,
        port=probe_request.port,
        path=parsed.path.rstrip("/") + "/models",
        body=b"",
        api_key=context.api_key,
        scheme=probe_request.scheme,
        api_style=probe_request.api_style,
        base_url_policy=probe_request.base_url_policy,
        endpoint_scope=probe_request.endpoint_scope,
        method="GET",
        request_kind="model_list",
    )


def _remaining(deadline: float, monotonic: Callable[[], float]) -> float:
    value = deadline - monotonic()
    if value <= 0:
        raise ModelProviderProbeError("timeout", "synthetic probe timed out", 504)
    return value


def _validate_content_type(value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelProviderProbeError(
            "response_mime_invalid", "provider response MIME type is invalid", 502,
            model_invoked=True,
        )
    parts = [part.strip().lower() for part in value.split(";")]
    if parts[0] != "application/json" or any(
        part not in {"charset=utf-8", "charset=\"utf-8\""} for part in parts[1:]
    ):
        raise ModelProviderProbeError(
            "response_mime_invalid", "provider response MIME type is invalid", 502,
            model_invoked=True,
        )


class PinnedHttpsProbeTransport:
    """One-shot direct HTTPS transport with a closed endpoint and TLS policy."""

    __slots__ = (
        "_connection_factory",
        "_max_response_bytes",
        "_monotonic",
        "_ssl_context_factory",
        "_total_timeout_seconds",
        "_user_agent",
    )

    def __init__(
        self,
        *,
        connection_factory: Callable[..., http.client.HTTPSConnection] | None = None,
        ssl_context_factory: Callable[[], ssl.SSLContext] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        total_timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        user_agent: str = "ShanghaiChemWorkbench-ProviderProbe/2",
    ) -> None:
        # Existing probe/intake callers retain their shorter defaults. Long
        # structured lessons may explicitly request up to ten minutes.
        if not 1 <= total_timeout_seconds <= 600:
            raise ValueError("transport timeout is outside the supported range")
        if not 1024 <= max_response_bytes <= 8 * 1024 * 1024:
            raise ValueError("transport response limit is outside the supported range")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_-]{0,95}", user_agent):
            raise ValueError("transport user agent is invalid")
        self._connection_factory = connection_factory
        self._ssl_context_factory = ssl_context_factory or self._secure_context
        self._monotonic = monotonic
        self._total_timeout_seconds = float(total_timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._user_agent = user_agent

    @staticmethod
    def _secure_context() -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    @staticmethod
    def _validate_endpoint(request: SyntheticProbeRequest) -> None:
        suffix = (
            "/models"
            if request.request_kind == "model_list"
            else (
                "/responses"
                if request.api_style
                in {"responses", "" if request.provider_id == "openai" else "-"}
                else "/chat/completions"
            )
        )
        if (
            request.method not in {"GET", "POST"}
            or request.request_kind not in {"synthetic_probe", "model_list"}
            or (request.request_kind == "model_list") != (request.method == "GET")
            or (request.request_kind == "model_list" and request.body)
            or (request.request_kind == "synthetic_probe" and not request.body)
            or not request.path.startswith("/")
            or "?" in request.path
            or "#" in request.path
            or "\\" in request.path
            or any(part in {".", ".."} for part in request.path.split("/"))
            or not request.path.endswith(suffix)
            or not 1 <= request.port <= 65535
        ):
            raise ModelProviderProbeError(
                "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
            )
        if request.provider_id == "openai":
            expected = (
                "https",
                "api.openai.com",
                443,
                "openai_official_https_v1",
                "public_https",
            )
        elif request.provider_id == "deepseek":
            expected = (
                "https",
                "api.deepseek.com",
                443,
                "deepseek_official_https_v1",
                "public_https",
            )
        else:
            expected = None
        actual = (
            request.scheme,
            request.host,
            request.port,
            request.base_url_policy,
            request.endpoint_scope,
        )
        if expected is not None:
            if actual != expected:
                raise ModelProviderProbeError(
                    "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
                )
            return
        if request.provider_id != "openai_compatible":
            raise ModelProviderProbeError(
                "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
            )
        try:
            address = ipaddress.ip_address(request.host)
        except ValueError:
            address = None
        is_loopback_host = (
            request.host.casefold() == "localhost"
            or address is not None
            and address
            in {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
        )
        if request.base_url_policy == "openai_compatible_loopback_v1":
            valid = (
                request.endpoint_scope == "loopback"
                and request.scheme in {"http", "https"}
                and is_loopback_host
            )
        else:
            valid = (
                request.base_url_policy == "openai_compatible_public_https_v1"
                and request.endpoint_scope == "public_https"
                and request.scheme == "https"
                and not is_loopback_host
                and (address is None or address.is_global)
            )
        if not valid:
            raise ModelProviderProbeError(
                "endpoint_policy_mismatch", "provider endpoint policy mismatch", 409
            )

    @staticmethod
    def _resolve(
        host: str,
        port: int,
        timeout: float,
        endpoint_scope: str = "public_https",
    ) -> tuple[tuple[int, int, int, tuple[Any, ...]], ...]:
        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                result_queue.put(
                    socket.getaddrinfo(
                        host,
                        port,
                        family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    )
                )
            except OSError:
                result_queue.put(None)

        threading.Thread(
            target=resolve,
            name="shchem-provider-probe-dns",
            daemon=True,
        ).start()
        try:
            raw = result_queue.get(timeout=timeout)
        except queue.Empty:
            raise ModelProviderProbeError(
                "timeout", "synthetic probe timed out", 504
            ) from None
        if not isinstance(raw, list):
            raise ModelProviderProbeError(
                "dns_failure", "provider host could not be resolved", 503
            )
        addresses: list[tuple[int, int, int, tuple[Any, ...]]] = []
        seen: set[tuple[int, int, int, tuple[Any, ...]]] = set()
        unsafe_address = False
        for item in raw:
            if not isinstance(item, tuple) or len(item) != 5:
                continue
            family, socktype, proto, _canonical_name, sockaddr = item
            identity = (family, socktype, proto, sockaddr)
            try:
                address = ipaddress.ip_address(str(sockaddr[0]))
            except (ValueError, IndexError):
                unsafe_address = True
                continue
            if (
                family not in {socket.AF_INET, socket.AF_INET6}
                or socktype != socket.SOCK_STREAM
                or not isinstance(sockaddr, tuple)
                or identity in seen
            ):
                continue
            if (
                endpoint_scope == "loopback"
                and not address.is_loopback
                or endpoint_scope == "public_https"
                and not address.is_global
            ):
                unsafe_address = True
                continue
            seen.add(identity)
            addresses.append(identity)
        if unsafe_address or not addresses:
            raise ModelProviderProbeError(
                "endpoint_address_not_allowed",
                "provider host resolved outside its allowed network scope",
                403,
            )
        return tuple(addresses)

    @staticmethod
    def _status_error(
        status: int, *, model_invoked: bool = True
    ) -> ModelProviderProbeError | None:
        table = {
            401: ("invalid_credentials", "provider rejected the credential", 401),
            403: ("permission_denied", "provider denied the request", 403),
            429: ("rate_limited", "provider rate limit reached", 429),
        }
        if status in table:
            code, message, api_status = table[status]
            return ModelProviderProbeError(
                code,
                message,
                api_status,
                model_invoked=model_invoked,
                http_status=status,
            )
        if 300 <= status <= 399:
            return ModelProviderProbeError(
                "redirect_refused",
                "provider redirect was refused",
                502,
                model_invoked=model_invoked,
                http_status=status,
            )
        if status >= 500:
            return ModelProviderProbeError(
                "provider_unavailable",
                "provider is temporarily unavailable",
                503,
                model_invoked=model_invoked,
                http_status=status,
            )
        if status != 200:
            return ModelProviderProbeError(
                "provider_rejected",
                "provider rejected the synthetic request",
                502,
                model_invoked=model_invoked,
                http_status=status,
            )
        return None

    def send(
        self,
        request: SyntheticProbeRequest,
        *,
        cancel_event: threading.Event,
        deadline_monotonic: float,
    ) -> ProbeTransportResponse:
        self._validate_endpoint(request)
        started = self._monotonic()
        deadline_monotonic = min(
            deadline_monotonic, started + self._total_timeout_seconds
        )
        if cancel_event.is_set():
            raise ModelProviderProbeError(
                "cancelled", "synthetic probe was cancelled", 409,
                connection_state="cancelled",
            )
        context: ssl.SSLContext | None = None
        if request.scheme == "https":
            context = self._ssl_context_factory()
            if (
                context.check_hostname is not True
                or context.verify_mode != ssl.CERT_REQUIRED
                or context.minimum_version is None
                or context.minimum_version < ssl.TLSVersion.TLSv1_2
            ):
                raise ModelProviderProbeError(
                    "tls_policy_invalid", "TLS policy is invalid", 503
                )
        connection: http.client.HTTPConnection | None = None
        model_invoked = False
        try:
            connect_timeout = min(
                _CONNECT_TIMEOUT_SECONDS,
                _remaining(deadline_monotonic, self._monotonic),
            )
            if self._connection_factory is None:
                connect_deadline = min(
                    deadline_monotonic, started + _CONNECT_TIMEOUT_SECONDS
                )
                addresses = self._resolve(
                    request.host,
                    request.port,
                    min(connect_timeout, _remaining(connect_deadline, self._monotonic)),
                    request.endpoint_scope,
                )
                if request.scheme == "https":
                    assert context is not None
                    connection = _ResolvedHTTPSConnection(
                        request.host,
                        port=request.port,
                        timeout=min(
                            connect_timeout,
                            _remaining(connect_deadline, self._monotonic),
                        ),
                        context=context,
                        addresses=addresses,
                        connect_deadline=connect_deadline,
                        monotonic=self._monotonic,
                    )
                else:
                    connection = _ResolvedHTTPConnection(
                        request.host,
                        port=request.port,
                        timeout=min(
                            connect_timeout,
                            _remaining(connect_deadline, self._monotonic),
                        ),
                        addresses=addresses,
                        connect_deadline=connect_deadline,
                        monotonic=self._monotonic,
                    )
            else:
                if request.scheme == "https":
                    connection = self._connection_factory(
                        request.host,
                        port=request.port,
                        timeout=connect_timeout,
                        context=context,
                    )
                else:
                    connection = self._connection_factory(
                        request.host,
                        port=request.port,
                        timeout=connect_timeout,
                    )
            connection.connect()
            if cancel_event.is_set():
                raise ModelProviderProbeError(
                    "cancelled", "synthetic probe was cancelled", 409,
                    connection_state="cancelled",
                )
            remaining = _remaining(deadline_monotonic, self._monotonic)
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            connection.putrequest(
                request.method,
                request.path,
                skip_host=False,
                skip_accept_encoding=True,
            )
            connection.putheader("Authorization", f"Bearer {request.api_key}")
            connection.putheader("Accept", "application/json")
            connection.putheader("Accept-Encoding", "identity")
            if request.method == "POST":
                connection.putheader(
                    "Content-Type", "application/json; charset=utf-8"
                )
                connection.putheader("Content-Length", str(len(request.body)))
            connection.putheader("Connection", "close")
            connection.putheader("User-Agent", self._user_agent)
            connection.endheaders(request.body if request.method == "POST" else None)
            model_invoked = request.request_kind == "synthetic_probe"
            response = connection.getresponse()
            status_error = self._status_error(
                int(response.status), model_invoked=model_invoked
            )
            if status_error is not None:
                raise status_error
            content_type = response.getheader("Content-Type")
            content_encoding = response.getheader("Content-Encoding")
            _validate_content_type(content_type)
            if content_encoding is not None and content_encoding.strip().lower() != "identity":
                raise ModelProviderProbeError(
                    "response_encoding_unsupported",
                    "compressed provider responses are not accepted",
                    502,
                    model_invoked=model_invoked,
                    http_status=int(response.status),
                )
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    declared = int(length, 10)
                except (TypeError, ValueError):
                    declared = -1
                if declared < 0:
                    raise ModelProviderProbeError(
                        "response_invalid", "provider response is invalid", 502,
                        model_invoked=model_invoked, http_status=int(response.status),
                    )
                if declared > self._max_response_bytes:
                    raise ModelProviderProbeError(
                        "response_too_large", "provider response is too large", 502,
                        model_invoked=model_invoked, http_status=int(response.status),
                    )
            body = response.read(self._max_response_bytes + 1)
            if len(body) > self._max_response_bytes:
                raise ModelProviderProbeError(
                    "response_too_large", "provider response is too large", 502,
                    model_invoked=model_invoked, http_status=int(response.status),
                    response_body_sha256=hashlib.sha256(body).hexdigest(),
                    response_bytes=len(body),
                )
            _remaining(deadline_monotonic, self._monotonic)
            if cancel_event.is_set():
                raise ModelProviderProbeError(
                    "cancelled", "synthetic probe was cancelled", 409,
                    model_invoked=model_invoked, connection_state="cancelled",
                    http_status=int(response.status),
                    response_body_sha256=hashlib.sha256(body).hexdigest(),
                    response_bytes=len(body),
                )
            return ProbeTransportResponse(
                http_status=int(response.status),
                content_type=content_type,
                content_encoding=content_encoding,
                body=body,
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
                model_invoked=model_invoked,
            )
        except ModelProviderProbeError as exc:
            exc.model_invoked = exc.model_invoked or model_invoked
            raise
        except socket.gaierror:
            raise ModelProviderProbeError(
                "dns_failure", "provider host could not be resolved", 503,
                model_invoked=model_invoked,
            ) from None
        except (ssl.CertificateError, ssl.SSLError):
            raise ModelProviderProbeError(
                "tls_failure", "provider TLS verification failed", 503,
                model_invoked=model_invoked,
            ) from None
        except TimeoutError:
            raise ModelProviderProbeError(
                "timeout", "synthetic probe timed out", 504,
                model_invoked=model_invoked,
            ) from None
        except http.client.HTTPException:
            raise ModelProviderProbeError(
                "response_invalid", "provider response is invalid", 502,
                model_invoked=model_invoked,
            ) from None
        except OSError:
            raise ModelProviderProbeError(
                "network_unavailable", "provider network is unavailable", 503,
                model_invoked=model_invoked,
            ) from None
        finally:
            if connection is not None:
                with contextlib.suppress(OSError):
                    connection.close()


def _response_api_style(value: str) -> str:
    if value == "openai":
        return "responses"
    if value == "deepseek":
        return "chat_completions"
    if value in {"responses", "chat_completions"}:
        return value
    raise ModelProviderProbeError(
        "response_invalid", "provider response protocol is invalid", 502,
        model_invoked=True,
    )


def _usage(api_style: str, payload: Mapping[str, Any]) -> dict[str, int]:
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        raise ModelProviderProbeError(
            "response_invalid", "provider response usage is invalid", 502,
            model_invoked=True,
        )
    if _response_api_style(api_style) == "responses":
        names = ("input_tokens", "output_tokens", "total_tokens")
    else:
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
    values: list[int] = []
    for name in names:
        value = raw.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > _MAX_USAGE_TOKENS
        ):
            raise ModelProviderProbeError(
                "response_invalid", "provider response usage is invalid", 502,
                model_invoked=True,
            )
        values.append(value)
    if values[2] != values[0] + values[1]:
        raise ModelProviderProbeError(
            "response_invalid", "provider response usage is invalid", 502,
            model_invoked=True,
        )
    return {
        "input_tokens": values[0],
        "output_tokens": values[1],
        "total_tokens": values[2],
    }


def _validate_provider_response(
    api_style: str, response: ProbeTransportResponse
) -> tuple[dict[str, int], str, int]:
    _validate_content_type(response.content_type)
    if response.content_encoding is not None and response.content_encoding.strip().lower() != "identity":
        raise ModelProviderProbeError(
            "response_encoding_unsupported",
            "compressed provider responses are not accepted",
            502,
            model_invoked=response.model_invoked,
            http_status=response.http_status,
        )
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise ModelProviderProbeError(
            "response_too_large", "provider response is too large", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
            response_body_sha256=hashlib.sha256(response.body).hexdigest(),
            response_bytes=len(response.body),
        )
    try:
        payload = _strict_json_loads(response.body)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ModelProviderProbeError(
            "response_invalid", "provider response is invalid", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
            response_body_sha256=hashlib.sha256(response.body).hexdigest(),
            response_bytes=len(response.body),
        ) from None
    if not isinstance(payload, Mapping):
        raise ModelProviderProbeError(
            "response_invalid", "provider response is invalid", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
        )
    resolved_api_style = _response_api_style(api_style)
    usage = _usage(resolved_api_style, payload)
    output_text: str | None = None
    if resolved_api_style == "responses":
        output = payload.get("output")
        if isinstance(output, list):
            texts = [
                content.get("text")
                for item in output
                if isinstance(item, Mapping) and isinstance(item.get("content"), list)
                for content in item["content"]
                if isinstance(content, Mapping) and content.get("type") == "output_text"
            ]
            if len(texts) == 1 and isinstance(texts[0], str):
                output_text = texts[0]
    else:
        choices = payload.get("choices")
        if isinstance(choices, list) and len(choices) == 1:
            choice = choices[0]
            if isinstance(choice, Mapping) and isinstance(choice.get("message"), Mapping):
                content = choice["message"].get("content")
                if isinstance(content, str):
                    output_text = content
    if output_text is None or len(output_text.encode("utf-8")) > 4096:
        raise ModelProviderProbeError(
            "response_invalid", "provider response output is invalid", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
        )
    try:
        output = _strict_json_loads(output_text)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ModelProviderProbeError(
            "response_invalid", "provider response output is invalid", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
        ) from None
    if output != {"status": "ok"}:
        raise ModelProviderProbeError(
            "response_invalid", "provider response output is invalid", 502,
            model_invoked=response.model_invoked, http_status=response.http_status,
        )
    return usage, hashlib.sha256(response.body).hexdigest(), len(response.body)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(slots=True)
class _ProbeRun:
    probe_run_id: str
    profile_id: str
    provider_id: str
    model_id: str
    provider_kind: str
    api_style: str
    base_url_policy: str
    base_url: str
    endpoint_scope: str
    expected_revision: str
    idempotency_key: str
    submitted_at: str
    status: str = "queued"
    connection_state: str = "not_started"
    started_at: str | None = None
    completed_at: str | None = None
    model_invoked: bool = False
    error_code: str | None = None
    latency_ms: int | None = None
    usage: dict[str, int] | None = None
    receipt_id: str | None = None
    receipt_sha256: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    future: concurrent.futures.Future[None] | None = field(default=None, repr=False)


class ModelProviderSyntheticProbeManager:
    """Asynchronous synthetic-only probe coordinator with per-profile exclusion."""

    def __init__(
        self,
        settings: ModelProviderSettingsStore,
        *,
        transport: ProbeTransport | None = None,
        max_workers: int = 4,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], str] = _utc_now,
    ) -> None:
        if not 1 <= max_workers <= 8:
            raise ModelProviderProbeError("worker_count_invalid", "invalid worker count")
        self._settings = settings
        self._transport = transport or PinnedHttpsProbeTransport(monotonic=monotonic)
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._lock = threading.RLock()
        self._runs: dict[str, _ProbeRun] = {}
        self._latest_by_profile: dict[str, str] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._shutdown = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="shchem-provider-probe",
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(synthetic_only=True, production=False)"

    @staticmethod
    def _public(run: _ProbeRun) -> dict[str, Any]:
        return {
            "probe_run_id": run.probe_run_id,
            "profile_id": run.profile_id,
            "provider_id": run.provider_id,
            "model_id": run.model_id,
            "status": run.status,
            "connection_state": run.connection_state,
            "submitted_at": run.submitted_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "model_invoked": run.model_invoked,
            "error_code": run.error_code,
            "latency_ms": run.latency_ms,
            "usage": dict(run.usage) if run.usage is not None else None,
            "cost": dict(_COST),
            "egress": dict(_EGRESS),
            "receipt_id": run.receipt_id,
            "receipt_sha256": run.receipt_sha256,
            "production_model_invocation_enabled": False,
        }

    @staticmethod
    def _validate_id(value: str, *, code: str) -> str:
        if not isinstance(value, str) or not _PROBE_ID.fullmatch(value):
            raise ModelProviderProbeError(code, "invalid probe identifier")
        return value

    def start(
        self,
        profile_id: str,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._validate_id(idempotency_key, code="idempotency_key_invalid")
        idempotency_identity = (profile_id, idempotency_key)
        with self._lock:
            replay_id = self._idempotency.get(idempotency_identity)
            if replay_id is not None:
                replay = self._runs[replay_id]
                if replay.expected_revision != expected_revision:
                    raise ModelProviderProbeError(
                        "idempotency_conflict",
                        "idempotency key was used for another settings revision",
                        409,
                    )
                return {"probe": self._public(replay), "idempotent_replay": True}
            existing_identity = self._runs.get(idempotency_key)
            if existing_identity is not None:
                raise ModelProviderProbeError(
                    "idempotency_conflict",
                    "idempotency key was used for another probe",
                    409,
                )
            if self._shutdown:
                raise ModelProviderProbeError(
                    "probe_manager_shutdown", "probe manager is shut down", 503
                )
            latest_id = self._latest_by_profile.get(profile_id)
            if latest_id is not None and self._runs[latest_id].status in _ACTIVE:
                raise ModelProviderProbeError(
                    "probe_in_progress", "a probe is already in progress", 409
                )
        try:
            policy = self._settings.invocation_policy(
                profile_id, expected_revision=expected_revision
            )
            if not self._settings.credential_exists(profile_id):
                raise ModelProviderProbeError(
                    "model_not_configured", "model is not configured", 409
                )
        except ModelProviderSettingsError as exc:
            raise ModelProviderProbeError(exc.code, str(exc), exc.status) from None
        with self._lock:
            # Recheck after the settings read so simultaneous submits cannot
            # create duplicate work or overwrite another profile's run id.
            replay_id = self._idempotency.get(idempotency_identity)
            if replay_id is not None:
                replay = self._runs[replay_id]
                if replay.expected_revision != expected_revision:
                    raise ModelProviderProbeError(
                        "idempotency_conflict",
                        "idempotency key was used for another settings revision",
                        409,
                    )
                return {"probe": self._public(replay), "idempotent_replay": True}
            if self._shutdown:
                raise ModelProviderProbeError(
                    "probe_manager_shutdown", "probe manager is shut down", 503
                )
            existing_identity = self._runs.get(idempotency_key)
            if existing_identity is not None:
                raise ModelProviderProbeError(
                    "idempotency_conflict",
                    "idempotency key was used for another probe",
                    409,
                )
            latest_id = self._latest_by_profile.get(profile_id)
            if latest_id is not None and self._runs[latest_id].status in _ACTIVE:
                raise ModelProviderProbeError(
                    "probe_in_progress", "a probe is already in progress", 409
                )
            # The browser-chosen idempotency identity is also the public run
            # identity, making submit and polling a single closed binding.
            run_id = idempotency_key
            run = _ProbeRun(
                probe_run_id=run_id,
                profile_id=profile_id,
                provider_id=str(policy["provider_id"]),
                model_id=str(policy["model_id"]),
                provider_kind=str(policy["provider_kind"]),
                api_style=str(policy["api_style"]),
                base_url_policy=str(policy["base_url_policy"]),
                base_url=str(policy["base_url"]),
                endpoint_scope=str(policy["endpoint_scope"]),
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                submitted_at=self._utc_now(),
            )
            self._runs[run_id] = run
            self._latest_by_profile[profile_id] = run_id
            self._idempotency[idempotency_identity] = run_id
            run.future = self._executor.submit(self._run, run_id)
            return {"probe": self._public(run), "idempotent_replay": False}

    def get(self, probe_run_id: str) -> dict[str, Any]:
        self._validate_id(probe_run_id, code="probe_run_id_invalid")
        with self._lock:
            run = self._runs.get(probe_run_id)
            if run is None:
                raise ModelProviderProbeError(
                    "probe_not_found", "synthetic probe was not found", 404
                )
            return self._public(run)

    def get_for_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            run_id = self._latest_by_profile.get(profile_id)
            return self._public(self._runs[run_id]) if run_id is not None else None

    def cancel(
        self, probe_run_id: str, *, expected_revision: str | None = None
    ) -> dict[str, Any]:
        self._validate_id(probe_run_id, code="probe_run_id_invalid")
        with self._lock:
            run = self._runs.get(probe_run_id)
            if run is None:
                raise ModelProviderProbeError(
                    "probe_not_found", "synthetic probe was not found", 404
                )
            # A terminal run has no work left to cancel.  Its start revision
            # may legitimately differ because a successful CAS commit advances
            # profile metadata; it must not block a subsequent key deletion.
            if run.status in _TERMINAL:
                return self._public(run)
            if expected_revision is not None and run.expected_revision != expected_revision:
                raise ModelProviderProbeError(
                    "revision_conflict", "settings changed; reload and retry", 409
                )
            if run.status in {"queued", "running"}:
                run.cancel_event.set()
                run.status = "cancel_requested"
            return self._public(run)

    def cancel_profile(
        self, profile_id: str, *, expected_revision: str | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            run_id = self._latest_by_profile.get(profile_id)
        if run_id is None:
            return None
        return self.cancel(run_id, expected_revision=expected_revision)

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            for run in self._runs.values():
                if run.status in {"queued", "running"}:
                    run.cancel_event.set()
                    run.status = "cancel_requested"
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _write_receipt(self, payload: Mapping[str, Any]) -> tuple[str, str]:
        receipt_id = "probe-receipt-" + secrets.token_hex(16)
        receipt_root = self._settings.probe_receipt_root
        _assert_components_not_reparse(receipt_root)
        receipt_root.mkdir(parents=True, exist_ok=True)
        _assert_components_not_reparse(receipt_root)
        _assert_existing_path_safe(receipt_root, regular_file=False)
        _apply_owner_only_permissions(receipt_root, directory=True)
        canonical_payload = dict(payload)
        canonical_payload["receipt_id"] = receipt_id
        canonical_payload["payload_sha256"] = hashlib.sha256(
            _canonical_json_bytes(canonical_payload)
        ).hexdigest()
        serialized = (
            json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        target = receipt_root / f"{receipt_id}.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{receipt_id}.", dir=receipt_root
        )
        temporary = Path(temporary_name)
        descriptor_open = True
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor_open = False
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            _assert_existing_path_safe(temporary, regular_file=True)
            _apply_owner_only_permissions(temporary, directory=False)
            if target.exists():
                raise ModelProviderProbeError(
                    "receipt_write_failed", "probe receipt could not be saved", 503
                )
            os.replace(temporary, target)
            _assert_existing_path_safe(target, regular_file=True)
            _apply_owner_only_permissions(target, directory=False)
        except ModelProviderProbeError:
            raise
        except (OSError, ModelProviderSettingsError):
            raise ModelProviderProbeError(
                "receipt_write_failed", "probe receipt could not be saved", 503
            ) from None
        finally:
            if descriptor_open:
                os.close(descriptor)
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return receipt_id, hashlib.sha256(serialized).hexdigest()

    def _receipt_payload(
        self,
        run: _ProbeRun,
        *,
        status: str,
        error_code: str | None,
        request: SyntheticProbeRequest | None,
        response: ProbeTransportResponse | None,
        response_sha256: str | None,
        response_bytes: int | None,
        response_http_status: int | None,
    ) -> dict[str, Any]:
        parsed_base = urlsplit(run.base_url)
        fallback_port = parsed_base.port or (
            443 if parsed_base.scheme == "https" else 80
        )
        fallback_path = parsed_base.path.rstrip("/") + (
            "/responses"
            if run.api_style == "responses"
            else "/chat/completions"
        )
        request_summary = {
            "method": request.method if request is not None else "POST",
            "scheme": request.scheme if request is not None else parsed_base.scheme,
            "host": request.host if request is not None else parsed_base.hostname,
            "port": request.port if request is not None else fallback_port,
            "path": request.path if request is not None else fallback_path,
            "body_sha256": hashlib.sha256(request.body).hexdigest() if request else None,
            "body_bytes": len(request.body) if request else None,
            "prompt_sha256": FIXED_SYNTHETIC_PROMPT_SHA256,
            "prompt_bytes": len(FIXED_SYNTHETIC_PROMPT_BYTES),
            "stream": False,
            "background": False if run.api_style == "responses" else None,
            "store": False if run.api_style == "responses" else None,
            "tools_enabled": False,
            "redirects_followed": False,
            "retries": 0,
            "compression_accepted": False,
        }
        response_summary = {
            "http_status": (
                response.http_status if response is not None else response_http_status
            ),
            "body_sha256": response_sha256,
            "body_bytes": response_bytes,
            "content_type_accepted": (
                response is not None and error_code != "response_mime_invalid"
            ),
            "content_encoding_accepted": (
                response is not None
                and error_code != "response_encoding_unsupported"
            ),
            "error_code": error_code,
        }
        return {
            "schema_version": PROBE_RECEIPT_SCHEMA_VERSION,
            "probe_run_id": run.probe_run_id,
            "profile_id": run.profile_id,
            "provider_id": run.provider_id,
            "provider_kind": run.provider_kind,
            "api_style": run.api_style,
            "model_id": run.model_id,
            "base_url_policy": run.base_url_policy,
            "endpoint_scope": run.endpoint_scope,
            "status": status,
            "connection_state": run.connection_state,
            "submitted_at": run.submitted_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "latency_ms": run.latency_ms,
            "model_invoked": run.model_invoked,
            "error_code": error_code,
            "request": {
                **request_summary,
                "summary_sha256": hashlib.sha256(
                    _canonical_json_bytes(request_summary)
                ).hexdigest(),
            },
            "response": {
                **response_summary,
                "summary_sha256": hashlib.sha256(
                    _canonical_json_bytes(response_summary)
                ).hexdigest(),
            },
            "usage": dict(run.usage) if run.usage is not None else None,
            "cost": dict(_COST),
            "egress": dict(_EGRESS),
            "credential_persisted_in_receipt": False,
            "authorization_header_persisted_in_receipt": False,
            "response_body_persisted_in_receipt": False,
            "production_model_invocation_enabled": False,
        }

    def _run(self, run_id: str) -> None:
        request: SyntheticProbeRequest | None = None
        response: ProbeTransportResponse | None = None
        response_sha256: str | None = None
        response_bytes: int | None = None
        response_http_status: int | None = None
        started_monotonic = self._monotonic()
        with self._lock:
            run = self._runs[run_id]
            run.status = "running" if not run.cancel_event.is_set() else "cancel_requested"
            run.connection_state = "connecting"
            run.started_at = self._utc_now()
        outcome = "failed"
        error_code: str | None = None
        try:
            if run.cancel_event.is_set():
                raise ModelProviderProbeError(
                    "cancelled", "synthetic probe was cancelled", 409,
                    connection_state="cancelled",
                )
            try:
                borrowed = self._settings.borrow_probe_context(
                    run.profile_id, expected_revision=run.expected_revision
                )
                with borrowed as context:
                    request = _build_request(context)
                    response = self._transport.send(
                        request,
                        cancel_event=run.cancel_event,
                        deadline_monotonic=started_monotonic + _TOTAL_TIMEOUT_SECONDS,
                    )
                context = None
            except ModelProviderSettingsError:
                outcome = "stale"
                error_code = "stale_result"
                raise
            with self._lock:
                run.model_invoked = response.model_invoked
                run.connection_state = "connected"
                run.latency_ms = response.latency_ms
            usage, response_sha256, response_bytes = _validate_provider_response(
                run.api_style, response
            )
            with self._lock:
                run.usage = usage
            outcome = "succeeded"
        except ModelProviderSettingsError:
            pass
        except ModelProviderProbeError as exc:
            error_code = exc.code
            outcome = "cancelled" if exc.code == "cancelled" else "failed"
            response_http_status = exc.http_status
            response_sha256 = exc.response_body_sha256 or (
                hashlib.sha256(response.body).hexdigest()
                if response is not None
                else None
            )
            response_bytes = (
                exc.response_bytes
                if exc.response_bytes is not None
                else (len(response.body) if response is not None else None)
            )
            with self._lock:
                run.model_invoked = exc.model_invoked
                run.connection_state = exc.connection_state
        except Exception:  # noqa: BLE001 - injected transports are untrusted.
            error_code = "provider_error"
            outcome = "failed"
            with self._lock:
                run.connection_state = "failed"
        with self._lock:
            run.completed_at = self._utc_now()
            if run.latency_ms is None:
                run.latency_ms = max(
                    0, round((self._monotonic() - started_monotonic) * 1000)
                )
            if outcome == "succeeded":
                run.connection_state = "connected"
                error_code = None
            elif outcome == "cancelled":
                run.connection_state = "cancelled"
                error_code = "cancelled"
            elif outcome == "stale":
                run.connection_state = "stale"
                error_code = "stale_result"
            else:
                run.connection_state = "failed"
            run.error_code = error_code
        try:
            payload = self._receipt_payload(
                run,
                status=outcome,
                error_code=error_code,
                request=request,
                response=response,
                response_sha256=response_sha256,
                response_bytes=response_bytes,
                response_http_status=response_http_status,
            )
            receipt_id, receipt_sha = self._write_receipt(payload)
            with self._lock:
                run.receipt_id = receipt_id
                run.receipt_sha256 = receipt_sha
            if outcome != "stale":
                last_probe = {
                    "status": outcome,
                    "checked_at": run.completed_at,
                    "error_code": error_code,
                    "model_invoked": run.model_invoked,
                    "connection_state": run.connection_state,
                    "probe_run_id": run.probe_run_id,
                    "receipt_id": receipt_id,
                    "receipt_sha256": receipt_sha,
                    "latency_ms": run.latency_ms,
                    "usage": dict(run.usage) if run.usage is not None else None,
                }
                committed = self._settings.commit_probe_result(
                    run.profile_id,
                    expected_revision=run.expected_revision,
                    last_probe=last_probe,
                )
                if not committed:
                    outcome = "stale"
                    error_code = "stale_result"
                    with self._lock:
                        run.connection_state = "stale"
                        run.error_code = error_code
        except ModelProviderProbeError:
            outcome = "failed"
            error_code = "receipt_write_failed"
            with self._lock:
                run.connection_state = "failed"
                run.error_code = error_code
        except ModelProviderSettingsError:
            outcome = "failed"
            error_code = "settings_commit_failed"
            with self._lock:
                run.connection_state = "failed"
                run.error_code = error_code
        finally:
            request = None
            response = None
            with self._lock:
                run.status = outcome


def _model_ids_from_response(response: ProbeTransportResponse) -> list[str]:
    if (
        response.http_status != 200
        or response.model_invoked is not False
        or len(response.body) > _MAX_RESPONSE_BYTES
    ):
        raise ModelProviderProbeError(
            "response_invalid",
            "provider model list response is invalid",
            502,
            model_invoked=False,
            http_status=response.http_status,
        )
    _validate_content_type(response.content_type)
    if (
        response.content_encoding is not None
        and response.content_encoding.strip().lower() != "identity"
    ):
        raise ModelProviderProbeError(
            "response_encoding_unsupported",
            "compressed provider responses are not accepted",
            502,
            model_invoked=False,
            http_status=response.http_status,
        )
    try:
        payload = _strict_json_loads(response.body)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ModelProviderProbeError(
            "response_invalid",
            "provider model list response is invalid",
            502,
            model_invoked=False,
            http_status=response.http_status,
        ) from None
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list) or len(data) > _MAX_MODEL_LIST_ITEMS:
        raise ModelProviderProbeError(
            "response_invalid",
            "provider model list response is invalid",
            502,
            model_invoked=False,
            http_status=response.http_status,
        )
    identifiers: list[str] = []
    seen: set[str] = set()
    for item in data:
        identifier = item.get("id") if isinstance(item, Mapping) else None
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}", identifier)
            or ".." in identifier
            or "//" in identifier
            or identifier.endswith(("/", ":"))
        ):
            raise ModelProviderProbeError(
                "response_invalid",
                "provider model list response is invalid",
                502,
                model_invoked=False,
                http_status=response.http_status,
            )
        if identifier not in seen:
            seen.add(identifier)
            identifiers.append(identifier)
    return sorted(identifiers, key=str.casefold)


class ModelProviderModelListProbe:
    """Explicit, synchronous `/models` discovery without capability inference."""

    def __init__(
        self,
        settings: ModelProviderSettingsStore,
        *,
        transport: ProbeTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport or PinnedHttpsProbeTransport(monotonic=monotonic)
        self._monotonic = monotonic

    def __repr__(self) -> str:
        return f"{type(self).__name__}(capabilities_inferred=False)"

    def probe(self, profile_id: str, *, expected_revision: str) -> dict[str, Any]:
        started = self._monotonic()
        try:
            with self._settings.borrow_probe_context(
                profile_id, expected_revision=expected_revision
            ) as context:
                request = _build_model_list_request(context)
                response = self._transport.send(
                    request,
                    cancel_event=threading.Event(),
                    deadline_monotonic=started + _TOTAL_TIMEOUT_SECONDS,
                )
            # A credential rotation or profile mutation during the request
            # invalidates the discovery result.  No profile state is written.
            self._settings.invocation_policy(
                profile_id, expected_revision=expected_revision
            )
        except ModelProviderSettingsError as exc:
            raise ModelProviderProbeError(exc.code, str(exc), exc.status) from None
        return {
            "model_ids": _model_ids_from_response(response),
            "capabilities_not_inferred": True,
        }


__all__ = [
    "FIXED_SYNTHETIC_PROMPT",
    "FIXED_SYNTHETIC_PROMPT_BYTES",
    "FIXED_SYNTHETIC_PROMPT_SHA256",
    "MODEL_PROVIDER_SYNTHETIC_PROBE_EXECUTE_CAPABILITY",
    "PROBE_RECEIPT_SCHEMA_VERSION",
    "ModelProviderModelListProbe",
    "ModelProviderProbeError",
    "ModelProviderSyntheticProbeManager",
    "PinnedHttpsProbeTransport",
    "ProbeTransport",
    "ProbeTransportResponse",
    "SyntheticProbeRequest",
    "_build_model_list_request",
    "synthetic_probe_contract",
]
