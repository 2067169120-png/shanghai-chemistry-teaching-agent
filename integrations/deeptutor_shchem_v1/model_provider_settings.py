from __future__ import annotations

import contextlib
import ctypes
import getpass
import ipaddress
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

SETTINGS_SCHEMA_VERSION = "shchem.model-provider-settings.v1"
SETTINGS_FILE_NAME = "model-provider-settings.v1.json"
LOCK_FILE_NAME = ".model-provider-settings.v1.lock"
CREDENTIAL_TARGET_PREFIX = "ShanghaiChemWorkbench/model-provider/"
MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY = "model_provider_settings_write"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}$")
_REVISION = re.compile(r"^rev_[0-9a-f]{32}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z$"
)
_MAX_SETTINGS_BYTES = 1024 * 1024
_MAX_PROFILES = 32
_MAX_DISPLAY_NAME_LENGTH = 80
_ALLOWED_PROVIDER_KINDS = frozenset({"preset", "openai_compatible"})
_ALLOWED_API_STYLES = frozenset({"chat_completions", "responses"})
_ALLOWED_LOCAL_ENDPOINT_POLICIES = frozenset({"deny", "allow_loopback_http"})
_ALLOWED_CAPABILITIES = frozenset({"text", "vision", "structured_output"})
_ALLOWED_DATA_CLASSES = frozenset(
    {
        "synthetic_only",
        "question_text_redacted",
        "question_image_redacted",
        "source_page_image",
        "student_answer_image",
        "deidentified_student_text",
    }
)
_ALLOWED_IMAGE_EGRESS = frozenset(
    {
        "deny",
        "redacted_question_only",
        "teacher_confirmed_source_pages",
        "teacher_confirmed_student_pages",
        "teacher_confirmed_visual_pages",
    }
)
_ALLOWED_PROBE_STATUS = frozenset(
    {"never", "ready_not_invoked", "succeeded", "failed", "cancelled", "stale"}
)
_ALLOWED_PROBE_ERROR_CODES = frozenset(
    {
        "cancelled",
        "dns_failure",
        "endpoint_address_not_allowed",
        "endpoint_policy_mismatch",
        "invalid_credentials",
        "network_unavailable",
        "permission_denied",
        "provider_error",
        "provider_rejected",
        "provider_unavailable",
        "rate_limited",
        "redirect_refused",
        "response_encoding_unsupported",
        "response_invalid",
        "response_mime_invalid",
        "response_too_large",
        "request_invalid",
        "receipt_write_failed",
        "settings_commit_failed",
        "stale_result",
        "tls_policy_invalid",
        "tls_failure",
        "timeout",
    }
)
_ALLOWED_CONNECTION_STATES = frozenset(
    {"not_started", "connecting", "connected", "failed", "cancelled", "stale"}
)
_PROBE_RUN_ID = re.compile(r"^probe_[0-9a-f]{32}$")
_RECEIPT_ID = re.compile(r"^probe-receipt-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_USAGE_TOKENS = 1_000_000


class ModelProviderSettingsError(ValueError):
    """A sanitized, API-safe settings error.

    Messages deliberately contain neither submitted values nor backend exception text.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


class CredentialBackendUnavailable(ModelProviderSettingsError):
    def __init__(self) -> None:
        super().__init__(
            "credential_store_unavailable",
            "the operating-system credential store is unavailable",
            503,
        )


class CredentialBackend(Protocol):
    """Minimal injectable credential backend.

    Test suites must inject an in-memory implementation. Production uses
    :class:`WindowsCredentialManagerBackend`.
    """

    def available(self) -> bool: ...

    def write(self, target_name: str, secret: str) -> None: ...

    def exists(self, target_name: str) -> bool: ...

    def read(self, target_name: str) -> str | None: ...

    def delete(self, target_name: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    model_id: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    provider_id: str
    base_url_policy: str
    base_url: str
    models: tuple[ModelPolicy, ...]
    follow_redirects: bool = False
    api_style: str = "chat_completions"
    display_name: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ModelProviderProbeContext:
    """Short-lived, repr-safe provider material borrowed under revision control.

    Python strings cannot promise physical zeroization.  The store therefore
    guarantees the narrower, auditable boundary: the secret is read only for
    this context, is never returned by metadata/status methods, and is neither
    persisted nor logged by this module.
    """

    profile_id: str
    provider_id: str
    model_id: str
    base_url_policy: str
    base_url: str
    revision: str
    api_key: str
    provider_kind: str = "preset"
    api_style: str = ""
    local_endpoint_policy: str = "deny"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(profile_id={self.profile_id!r}, "
            f"provider_id={self.provider_id!r}, model_id={self.model_id!r}, "
            "credential_borrowed=True)"
        )


DEFAULT_PROVIDER_POLICIES: Mapping[str, ProviderPolicy] = MappingProxyType(
    {
        "openai": ProviderPolicy(
            provider_id="openai",
            base_url_policy="openai_official_https_v1",
            base_url="https://api.openai.com/v1",
            models=(
                ModelPolicy("gpt-5", ("text", "vision", "structured_output")),
                ModelPolicy("gpt-5-mini", ("text", "vision", "structured_output")),
                ModelPolicy("gpt-4.1", ("text", "vision", "structured_output")),
                ModelPolicy("gpt-4.1-mini", ("text", "vision", "structured_output")),
            ),
            api_style="responses",
            display_name="OpenAI",
        ),
        "deepseek": ProviderPolicy(
            provider_id="deepseek",
            base_url_policy="deepseek_official_https_v1",
            base_url="https://api.deepseek.com",
            models=(
                ModelPolicy("deepseek-v4-flash", ("text", "structured_output")),
                ModelPolicy("deepseek-v4-pro", ("text", "structured_output")),
            ),
            api_style="chat_completions",
            display_name="DeepSeek",
        ),
    }
)


class WindowsCredentialManagerBackend:
    """Current-user, non-roaming Windows Generic Credential backend.

    ``CRED_PERSIST_LOCAL_MACHINE`` means that the credential persists for the
    current Windows user on this machine and is not roamed to other machines.
    The API key is never included in an exception or object representation.
    """

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168
    _UNAVAILABLE_ERRORS = frozenset({50, 120, 1312})
    _MAX_SECRET_BYTES = 2560

    def __init__(self) -> None:
        self._loaded = False
        self._advapi32: Any = None
        self._cred_struct: Any = None
        self._pcred_struct: Any = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(current_user=True, roaming=False)"

    def available(self) -> bool:
        return os.name == "nt"

    def _load(self) -> None:
        if os.name != "nt":
            raise CredentialBackendUnavailable()
        if self._loaded:
            return

        from ctypes import wintypes

        class _CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        pcredential = ctypes.POINTER(_CREDENTIALW)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        advapi32.CredWriteW.restype = wintypes.BOOL
        advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(pcredential),
        ]
        advapi32.CredReadW.restype = wintypes.BOOL
        advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        advapi32.CredDeleteW.restype = wintypes.BOOL
        advapi32.CredFree.argtypes = [ctypes.c_void_p]
        advapi32.CredFree.restype = None

        self._advapi32 = advapi32
        self._cred_struct = _CREDENTIALW
        self._pcred_struct = pcredential
        self._loaded = True

    def _raise_last_error(self) -> None:
        code = ctypes.get_last_error()
        if code in self._UNAVAILABLE_ERRORS:
            raise CredentialBackendUnavailable()
        raise ModelProviderSettingsError(
            "credential_operation_failed",
            "the operating-system credential operation failed",
            503,
        )

    @staticmethod
    def _validate_target(target_name: str) -> None:
        if (
            not isinstance(target_name, str)
            or not target_name.startswith(CREDENTIAL_TARGET_PREFIX)
            or len(target_name) > 256
            or "\x00" in target_name
        ):
            raise ModelProviderSettingsError(
                "credential_reference_invalid", "invalid credential reference"
            )

    def write(self, target_name: str, secret: str) -> None:
        self._validate_target(target_name)
        self._load()
        encoded = secret.encode("utf-8")
        if not encoded or len(encoded) > self._MAX_SECRET_BYTES:
            raise ModelProviderSettingsError("credential_invalid", "invalid credential")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        try:
            credential = self._cred_struct()
            credential.Flags = 0
            credential.Type = self._CRED_TYPE_GENERIC
            credential.TargetName = target_name
            credential.Comment = "Shanghai chemistry workbench model credential"
            credential.CredentialBlobSize = len(encoded)
            credential.CredentialBlob = ctypes.cast(
                blob, ctypes.POINTER(ctypes.c_ubyte)
            )
            credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
            credential.AttributeCount = 0
            credential.Attributes = None
            credential.TargetAlias = None
            credential.UserName = getpass.getuser()[:512]
            ctypes.set_last_error(0)
            if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
                self._raise_last_error()
        finally:
            ctypes.memset(blob, 0, len(encoded))

    def read(self, target_name: str) -> str | None:
        self._validate_target(target_name)
        self._load()
        pointer = self._pcred_struct()
        ctypes.set_last_error(0)
        if not self._advapi32.CredReadW(
            target_name,
            self._CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return None
            self._raise_last_error()
        try:
            size = int(pointer.contents.CredentialBlobSize)
            if size <= 0 or size > self._MAX_SECRET_BYTES:
                raise ModelProviderSettingsError(
                    "credential_corrupt", "stored credential is invalid", 503
                )
            raw = ctypes.string_at(pointer.contents.CredentialBlob, size)
            try:
                return raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise ModelProviderSettingsError(
                    "credential_corrupt", "stored credential is invalid", 503
                ) from None
        finally:
            if bool(pointer):
                size = int(pointer.contents.CredentialBlobSize)
                if 0 < size <= self._MAX_SECRET_BYTES:
                    ctypes.memset(pointer.contents.CredentialBlob, 0, size)
                self._advapi32.CredFree(pointer)

    def exists(self, target_name: str) -> bool:
        """Check for a credential entry without decoding its secret blob."""

        self._validate_target(target_name)
        self._load()
        pointer = self._pcred_struct()
        ctypes.set_last_error(0)
        if not self._advapi32.CredReadW(
            target_name,
            self._CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return False
            self._raise_last_error()
        try:
            size = int(pointer.contents.CredentialBlobSize)
            return 0 < size <= self._MAX_SECRET_BYTES
        finally:
            if bool(pointer):
                self._advapi32.CredFree(pointer)

    def delete(self, target_name: str) -> bool:
        self._validate_target(target_name)
        self._load()
        ctypes.set_last_error(0)
        if self._advapi32.CredDeleteW(target_name, self._CRED_TYPE_GENERIC, 0):
            return True
        if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
            return False
        self._raise_last_error()
        return False


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def _process_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.absolute()))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)


def _assert_existing_path_safe(path: Path, *, regular_file: bool | None = None) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return
    if _is_reparse_or_symlink(path_stat):
        raise ModelProviderSettingsError(
            "unsafe_settings_path", "settings path is not a regular local path", 403
        )
    if regular_file is True and not stat.S_ISREG(path_stat.st_mode):
        raise ModelProviderSettingsError(
            "unsafe_settings_path", "settings path is not a regular local file", 403
        )
    if regular_file is False and not stat.S_ISDIR(path_stat.st_mode):
        raise ModelProviderSettingsError(
            "unsafe_settings_path", "settings root is not a directory", 403
        )
    if regular_file is True and path_stat.st_nlink != 1:
        raise ModelProviderSettingsError(
            "unsafe_settings_path", "settings file has an unsafe link count", 403
        )


def _assert_components_not_reparse(path: Path) -> None:
    absolute = path.absolute()
    chain = tuple(reversed(absolute.parents)) + (absolute,)
    for component in chain:
        if component == component.parent:
            continue
        try:
            component_stat = component.lstat()
        except FileNotFoundError:
            continue
        if _is_reparse_or_symlink(component_stat):
            raise ModelProviderSettingsError(
                "unsafe_settings_path",
                "settings path contains a link or reparse point",
                403,
            )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_text = os.path.normcase(str(left.resolve(strict=False)))
    right_text = os.path.normcase(str(right.resolve(strict=False)))
    try:
        common = os.path.normcase(os.path.commonpath([left_text, right_text]))
    except ValueError:
        return False
    return common == left_text or common == right_text


def _apply_owner_only_permissions(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return

    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert.restype = wintypes.BOOL
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    # Protected DACL; full control is granted only to the object's owner.
    if not convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise ModelProviderSettingsError(
            "settings_permissions_failed",
            "could not apply owner-only settings permissions",
            503,
        )
    try:
        if not set_security(str(path), 0x00000004, descriptor):
            raise ModelProviderSettingsError(
                "settings_permissions_failed",
                "could not apply owner-only settings permissions",
                503,
            )
    finally:
        kernel32.LocalFree(descriptor)


def _validate_policy_catalog(
    catalog: Mapping[str, ProviderPolicy],
) -> Mapping[str, ProviderPolicy]:
    if not isinstance(catalog, Mapping) or not catalog:
        raise ModelProviderSettingsError(
            "provider_catalog_invalid", "invalid provider policy catalog"
        )
    validated: dict[str, ProviderPolicy] = {}
    for provider_id, policy in catalog.items():
        if (
            not isinstance(policy, ProviderPolicy)
            or provider_id != policy.provider_id
            or not _SAFE_ID.fullmatch(provider_id)
            or not _SAFE_ID.fullmatch(policy.base_url_policy)
            or policy.follow_redirects is not False
            or policy.api_style not in _ALLOWED_API_STYLES
            or (
                policy.display_name is not None
                and (
                    not isinstance(policy.display_name, str)
                    or not policy.display_name.strip()
                    or len(policy.display_name.strip()) > _MAX_DISPLAY_NAME_LENGTH
                    or any(ord(character) < 0x20 for character in policy.display_name)
                )
            )
        ):
            raise ModelProviderSettingsError(
                "provider_catalog_invalid", "invalid provider policy catalog"
            )
        parsed = urlsplit(policy.base_url)
        if (
            not policy.base_url.isascii()
            or any(ord(character) < 0x21 for character in policy.base_url)
            or "\\" in policy.base_url
            or "%" in policy.base_url
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or parsed.netloc not in {parsed.hostname, f"{parsed.hostname}:443"}
            or "//" in parsed.path
            or (parsed.path and not parsed.path.startswith("/"))
            or ".." in parsed.path.split("/")
            or parsed.path.endswith("/")
        ):
            raise ModelProviderSettingsError(
                "provider_catalog_invalid", "invalid provider policy catalog"
            )
        seen_models: set[str] = set()
        if not policy.models:
            raise ModelProviderSettingsError(
                "provider_catalog_invalid", "invalid provider policy catalog"
            )
        for model in policy.models:
            if (
                not isinstance(model, ModelPolicy)
                or not _SAFE_ID.fullmatch(model.model_id)
                or model.model_id in seen_models
                or not model.capabilities
                or len(model.capabilities) != len(set(model.capabilities))
                or not set(model.capabilities).issubset(_ALLOWED_CAPABILITIES)
                or "text" not in model.capabilities
            ):
                raise ModelProviderSettingsError(
                    "provider_catalog_invalid", "invalid provider policy catalog"
                )
            seen_models.add(model.model_id)
        validated[provider_id] = policy
    return MappingProxyType(validated)


def _validate_model_id(value: Any, *, code: str = "model_not_allowed") -> str:
    """Validate a model identifier as inert data, not as a catalog allowlist.

    Colons and slashes cover common local/provider namespaces.  URL/query syntax,
    whitespace and traversal-like segments remain rejected so the identifier can
    never become an endpoint or header value by accident.
    """

    if (
        not isinstance(value, str)
        or not _MODEL_ID.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ":"))
    ):
        raise ModelProviderSettingsError(code, "model identifier is invalid")
    return value


def _validate_capabilities(
    value: Any,
    *,
    required: bool,
) -> tuple[str, ...] | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) != len(set(value))
        or any(not isinstance(item, str) for item in value)
        or not set(value).issubset(_ALLOWED_CAPABILITIES)
        or "text" not in value
    ):
        raise ModelProviderSettingsError(
            "capabilities_invalid", "model capability declarations are invalid"
        )
    return tuple(value)


def _normalize_display_name(value: Any, *, fallback: str | None = None) -> str:
    if value is None:
        value = fallback
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > _MAX_DISPLAY_NAME_LENGTH
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ModelProviderSettingsError(
            "display_name_invalid", "provider display name is invalid"
        )
    return value.strip()


def _host_scope(hostname: str) -> str:
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost":
        return "loopback"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if "." not in normalized or normalized.endswith(
            (".local", ".internal", ".lan", ".home")
        ):
            return "non_public"
        return "hostname"
    if address in {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}:
        return "loopback"
    return "public_ip" if address.is_global else "non_public"


def _normalize_custom_base_url(
    value: Any, *, local_endpoint_policy: str
) -> tuple[str, str]:
    """Return one canonical base URL and its endpoint scope.

    Public providers are HTTPS-only.  Plain HTTP is an explicit exception for
    loopback-only OpenAI-compatible servers and cannot be used for LAN, metadata
    or public hosts.
    """

    if local_endpoint_policy not in _ALLOWED_LOCAL_ENDPOINT_POLICIES:
        raise ModelProviderSettingsError(
            "local_endpoint_policy_invalid", "local endpoint policy is invalid"
        )
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or not value.isascii()
        or any(ord(character) < 0x21 for character in value)
        or "\\" in value
        or "%" in value
    ):
        raise ModelProviderSettingsError("base_url_invalid", "base URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ModelProviderSettingsError("base_url_invalid", "base URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname.endswith(".")
        or "//" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or (parsed.path and not parsed.path.startswith("/"))
        or (
            parsed.path
            and not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)+/?", parsed.path)
        )
        or port is not None and not 1 <= port <= 65535
    ):
        raise ModelProviderSettingsError("base_url_invalid", "base URL is invalid")
    try:
        hostname = parsed.hostname.encode("ascii").decode("ascii").casefold()
    except UnicodeError:
        raise ModelProviderSettingsError("base_url_invalid", "base URL is invalid") from None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname
        ) or any(
            not label or label.startswith("-") or label.endswith("-")
            for label in hostname.split(".")
        ):
            raise ModelProviderSettingsError(
                "base_url_invalid", "base URL is invalid"
            ) from None
    scope = _host_scope(hostname)
    if scope == "loopback":
        if local_endpoint_policy != "allow_loopback_http":
            raise ModelProviderSettingsError(
                "local_endpoint_not_allowed",
                "loopback endpoint requires the explicit local endpoint policy",
            )
        endpoint_scope = "loopback"
    else:
        if local_endpoint_policy != "deny":
            raise ModelProviderSettingsError(
                "local_endpoint_policy_invalid",
                "the local endpoint policy may only be used with loopback",
            )
        if parsed.scheme != "https":
            raise ModelProviderSettingsError(
                "base_url_https_required", "public provider base URL must use HTTPS"
            )
        if scope == "non_public":
            raise ModelProviderSettingsError(
                "private_endpoint_not_allowed", "private network endpoint is not allowed"
            )
        endpoint_scope = "public_https"
    if parsed.scheme == "http" and endpoint_scope != "loopback":
        raise ModelProviderSettingsError(
            "base_url_https_required", "public provider base URL must use HTTPS"
        )
    default_port = 443 if parsed.scheme == "https" else 80
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = rendered_host if port in {None, default_port} else f"{rendered_host}:{port}"
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, netloc, path, "", ""))
    return normalized, endpoint_scope


def _new_revision() -> str:
    return "rev_" + secrets.token_hex(16)


def _credential_ref(profile_id: str) -> str:
    return CREDENTIAL_TARGET_PREFIX + profile_id


def _validate_profile_id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or ".." in value:
        raise ModelProviderSettingsError("profile_invalid", "invalid profile")
    return value


def _validate_last_probe(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    allowed = {
        "status",
        "checked_at",
        "error_code",
        "model_invoked",
        "connection_state",
        "probe_run_id",
        "receipt_id",
        "receipt_sha256",
        "latency_ms",
        "usage",
    }
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise ModelProviderSettingsError(
            "probe_metadata_invalid", "invalid probe metadata"
        )
    status_value = value.get("status")
    checked_at = value.get("checked_at")
    error_code = value.get("error_code")
    model_invoked = value.get("model_invoked")
    connection_state = value.get("connection_state")
    if connection_state is None:
        connection_state = {
            "never": "not_started",
            "ready_not_invoked": "not_started",
            "succeeded": "connected",
            "failed": "failed",
            "cancelled": "cancelled",
            "stale": "stale",
        }.get(status_value)
    probe_run_id = value.get("probe_run_id")
    receipt_id = value.get("receipt_id")
    receipt_sha256 = value.get("receipt_sha256")
    latency_ms = value.get("latency_ms")
    usage = value.get("usage")
    normalized_usage: dict[str, int] | None = None
    if usage is not None:
        if not isinstance(usage, Mapping) or set(usage) != {
            "input_tokens",
            "output_tokens",
            "total_tokens",
        }:
            raise ModelProviderSettingsError(
                "probe_metadata_invalid", "invalid probe metadata"
            )
        if any(
            isinstance(usage[key], bool)
            or not isinstance(usage[key], int)
            or usage[key] < 0
            or usage[key] > _MAX_USAGE_TOKENS
            for key in ("input_tokens", "output_tokens", "total_tokens")
        ) or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise ModelProviderSettingsError(
                "probe_metadata_invalid", "invalid probe metadata"
            )
        normalized_usage = {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        }
    if (
        status_value not in _ALLOWED_PROBE_STATUS
        or not isinstance(model_invoked, bool)
        or connection_state not in _ALLOWED_CONNECTION_STATES
        or (
            checked_at is not None
            and (
                not isinstance(checked_at, str)
                or not _UTC_TIMESTAMP.fullmatch(checked_at)
            )
        )
        or (error_code is not None and error_code not in _ALLOWED_PROBE_ERROR_CODES)
        or (status_value == "never" and model_invoked)
        or (status_value == "ready_not_invoked" and model_invoked)
        or (status_value == "succeeded" and not model_invoked)
        or (status_value in {"never", "ready_not_invoked", "succeeded"} and error_code is not None)
        or (status_value == "failed" and error_code is None)
        or (status_value == "cancelled" and error_code != "cancelled")
        or (status_value == "stale" and error_code != "stale_result")
        or (
            probe_run_id is not None
            and (not isinstance(probe_run_id, str) or not _PROBE_RUN_ID.fullmatch(probe_run_id))
        )
        or (
            receipt_id is not None
            and (not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id))
        )
        or (
            receipt_sha256 is not None
            and (not isinstance(receipt_sha256, str) or not _SHA256.fullmatch(receipt_sha256))
        )
        or ((receipt_id is None) != (receipt_sha256 is None))
        or (
            latency_ms is not None
            and (
                isinstance(latency_ms, bool)
                or not isinstance(latency_ms, int)
                or not 0 <= latency_ms <= 60_000
            )
        )
        or (status_value in {"never", "ready_not_invoked"} and any(
            item is not None
            for item in (probe_run_id, receipt_id, latency_ms, normalized_usage)
        ))
        or (status_value in {"succeeded", "failed", "cancelled", "stale"} and (
            checked_at is None or probe_run_id is None or receipt_id is None or latency_ms is None
        ))
        or (status_value == "succeeded" and normalized_usage is None)
    ):
        raise ModelProviderSettingsError(
            "probe_metadata_invalid", "invalid probe metadata"
        )
    return {
        "status": status_value,
        "checked_at": checked_at,
        "error_code": error_code,
        "model_invoked": model_invoked,
        "connection_state": connection_state,
        "probe_run_id": probe_run_id,
        "receipt_id": receipt_id,
        "receipt_sha256": receipt_sha256,
        "latency_ms": latency_ms,
        "usage": normalized_usage,
    }


class ModelProviderSettingsStore:
    """Secure metadata and Windows Credential Manager coordinator.

    ``metadata_root`` and ``project_root`` must be disjoint. The caller should
    normally use a per-user directory below ``LOCALAPPDATA`` for metadata_root.
    No model/network operation is implemented here.
    """

    __slots__ = (
        "_backend",
        "_lock_path",
        "_metadata_root",
        "_policies",
        "_probe_receipt_root",
        "_project_root",
        "_settings_path",
        "_thread_lock",
    )

    def __init__(
        self,
        metadata_root: str | Path,
        *,
        project_root: str | Path,
        credential_backend: CredentialBackend | None = None,
        provider_policies: Mapping[str, ProviderPolicy] | None = None,
    ) -> None:
        self._metadata_root = Path(metadata_root).absolute()
        self._project_root = Path(project_root).absolute()
        _assert_components_not_reparse(self._metadata_root)
        _assert_components_not_reparse(self._project_root)
        if _paths_overlap(self._metadata_root, self._project_root):
            raise ModelProviderSettingsError(
                "settings_root_not_external",
                "settings root must be outside the project",
                403,
            )
        self._settings_path = self._metadata_root / SETTINGS_FILE_NAME
        self._lock_path = self._metadata_root / LOCK_FILE_NAME
        self._probe_receipt_root = self._metadata_root / "probe-receipts-v1"
        self._backend = credential_backend or WindowsCredentialManagerBackend()
        self._policies = _validate_policy_catalog(
            provider_policies or DEFAULT_PROVIDER_POLICIES
        )
        self._thread_lock = _process_lock(self._lock_path)
        self._ensure_root()

    @property
    def probe_receipt_root(self) -> Path:
        """External owner-only root reserved for sanitized probe receipts."""

        return self._probe_receipt_root

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(external_metadata=True, "
            f"credential_backend={type(self._backend).__name__!r})"
        )

    def _ensure_root(self) -> None:
        _assert_components_not_reparse(self._metadata_root)
        self._metadata_root.mkdir(parents=True, exist_ok=True)
        _assert_components_not_reparse(self._metadata_root)
        _assert_existing_path_safe(self._metadata_root, regular_file=False)
        _apply_owner_only_permissions(self._metadata_root, directory=True)

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        self._ensure_root()
        _assert_existing_path_safe(self._lock_path, regular_file=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            descriptor_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or descriptor_stat.st_nlink != 1
            ):
                raise ModelProviderSettingsError(
                    "unsafe_settings_path", "settings lock is unsafe", 403
                )
            path_stat = self._lock_path.lstat()
            if _is_reparse_or_symlink(path_stat) or not os.path.samestat(
                descriptor_stat, path_stat
            ):
                raise ModelProviderSettingsError(
                    "unsafe_settings_path", "settings lock is unsafe", 403
                )
            if descriptor_stat.st_size == 0:
                os.write(descriptor, b"\x00")
                os.fsync(descriptor)
            _apply_owner_only_permissions(self._lock_path, directory=False)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock, self._file_lock():
            yield

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        _assert_components_not_reparse(self._metadata_root)
        _assert_existing_path_safe(self._settings_path, regular_file=True)
        if not self._settings_path.exists():
            return {}
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._settings_path, flags)
            try:
                descriptor_stat = os.fstat(descriptor)
                path_stat = self._settings_path.lstat()
                if (
                    not stat.S_ISREG(descriptor_stat.st_mode)
                    or descriptor_stat.st_nlink != 1
                    or _is_reparse_or_symlink(path_stat)
                    or not os.path.samestat(descriptor_stat, path_stat)
                ):
                    raise ModelProviderSettingsError(
                        "unsafe_settings_path", "settings file is unsafe", 403
                    )
                size = descriptor_stat.st_size
                if size <= 0 or size > _MAX_SETTINGS_BYTES:
                    raise ModelProviderSettingsError(
                        "settings_corrupt",
                        "model provider settings are invalid",
                        503,
                    )
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    serialized = stream.read(_MAX_SETTINGS_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(serialized) != size or len(serialized) > _MAX_SETTINGS_BYTES:
                raise ModelProviderSettingsError(
                    "settings_corrupt", "model provider settings are invalid", 503
                )
            raw = json.loads(serialized.decode("utf-8", errors="strict"))
        except ModelProviderSettingsError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ModelProviderSettingsError(
                "settings_corrupt", "model provider settings are invalid", 503
            ) from None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema_version", "profiles"}
            or raw.get("schema_version") != SETTINGS_SCHEMA_VERSION
            or not isinstance(raw.get("profiles"), list)
            or len(raw["profiles"]) > _MAX_PROFILES
        ):
            raise ModelProviderSettingsError(
                "settings_corrupt", "model provider settings are invalid", 503
            )
        profiles: dict[str, dict[str, Any]] = {}
        for value in raw["profiles"]:
            profile = self._validate_stored_profile(value)
            if profile["profile_id"] in profiles:
                raise ModelProviderSettingsError(
                    "settings_corrupt", "model provider settings are invalid", 503
                )
            profiles[profile["profile_id"]] = profile
        return profiles

    def _save_unlocked(self, profiles: Mapping[str, Mapping[str, Any]]) -> None:
        _assert_existing_path_safe(self._settings_path, regular_file=True)
        payload = {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "profiles": [dict(profiles[key]) for key in sorted(profiles)],
        }
        serialized = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        if len(serialized) > _MAX_SETTINGS_BYTES:
            raise ModelProviderSettingsError(
                "settings_too_large", "model provider settings are too large"
            )
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{SETTINGS_FILE_NAME}.", dir=self._metadata_root
            )
        except OSError:
            raise ModelProviderSettingsError(
                "settings_write_failed", "could not save model provider settings", 503
            ) from None
        temporary_path = Path(temporary_name)
        descriptor_open = True
        try:
            try:
                descriptor_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(descriptor_stat.st_mode)
                    or descriptor_stat.st_nlink != 1
                ):
                    raise ModelProviderSettingsError(
                        "unsafe_settings_path", "temporary settings file is unsafe", 403
                    )
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    descriptor_open = False
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())
                _assert_existing_path_safe(temporary_path, regular_file=True)
                _apply_owner_only_permissions(temporary_path, directory=False)
                _assert_existing_path_safe(self._settings_path, regular_file=True)
                os.replace(temporary_path, self._settings_path)
                _assert_existing_path_safe(self._settings_path, regular_file=True)
                _apply_owner_only_permissions(self._settings_path, directory=False)
            except ModelProviderSettingsError:
                raise
            except OSError:
                raise ModelProviderSettingsError(
                    "settings_write_failed",
                    "could not save model provider settings",
                    503,
                ) from None
        finally:
            if descriptor_open:
                os.close(descriptor)
            if temporary_path.exists():
                try:
                    _assert_existing_path_safe(temporary_path, regular_file=True)
                    temporary_path.unlink()
                except ModelProviderSettingsError:
                    pass

    def _catalog_model_policy(
        self, provider_id: str, model_id: str
    ) -> ModelPolicy | None:
        provider = self._policies.get(provider_id)
        if provider is None:
            return None
        for model in provider.models:
            if model.model_id == model_id:
                return model
        return None

    def _normalize_profile_input(
        self, value: Mapping[str, Any], *, stored: bool = False
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) - {
            "profile_id",
            "provider_kind",
            "provider_id",
            "display_name",
            "model_id",
            "base_url_policy",
            "base_url",
            "api_style",
            "local_endpoint_policy",
            "endpoint_scope",
            "credential_ref",
            "capabilities",
            "allowed_data_classes",
            "image_egress",
            "last_probe",
        }:
            raise ModelProviderSettingsError(
                "profile_invalid", "invalid model provider profile"
            )
        profile_id = _validate_profile_id(value.get("profile_id"))
        provider_kind = value.get("provider_kind", "preset")
        if provider_kind not in _ALLOWED_PROVIDER_KINDS:
            raise ModelProviderSettingsError(
                "provider_kind_invalid", "provider kind is invalid"
            )
        provider_id = value.get("provider_id")
        model_id = _validate_model_id(value.get("model_id"))
        base_url_policy = value.get("base_url_policy")
        supplied_capabilities = value.get("capabilities")
        if provider_kind == "preset":
            if not isinstance(provider_id, str):
                raise ModelProviderSettingsError(
                    "profile_invalid", "invalid model provider profile"
                )
            provider = self._policies.get(provider_id)
            if provider is None:
                raise ModelProviderSettingsError(
                    "provider_not_allowed", "provider is not allowed"
                )
            if base_url_policy != provider.base_url_policy:
                raise ModelProviderSettingsError(
                    "base_url_policy_not_allowed", "base URL policy is not allowed"
                )
            if value.get("base_url") not in {None, provider.base_url}:
                raise ModelProviderSettingsError(
                    "base_url_policy_not_allowed", "preset base URL cannot be changed"
                )
            if value.get("api_style") not in {None, provider.api_style}:
                raise ModelProviderSettingsError(
                    "api_style_invalid", "preset API style cannot be changed"
                )
            if value.get("local_endpoint_policy") not in {None, "deny"}:
                raise ModelProviderSettingsError(
                    "local_endpoint_policy_invalid", "preset endpoint is not local"
                )
            if value.get("endpoint_scope") not in {None, "public_https"}:
                raise ModelProviderSettingsError(
                    "base_url_policy_not_allowed", "preset endpoint scope cannot be changed"
                )
            catalog_model = self._catalog_model_policy(provider_id, model_id)
            declared = _validate_capabilities(
                supplied_capabilities,
                required=catalog_model is None and supplied_capabilities is not None,
            )
            if catalog_model is not None:
                if declared is not None and declared != catalog_model.capabilities and not stored:
                    raise ModelProviderSettingsError(
                        "capabilities_invalid", "model capabilities do not match policy"
                    )
                capabilities = declared if stored and declared is not None else catalog_model.capabilities
            else:
                # Text is the protocol baseline for a manually entered model.
                # Vision and structured output remain unknown unless declared
                # or established by the fixed structured synthetic probe.
                capabilities = declared or ("text",)
            display_name = _normalize_display_name(
                value.get("display_name"),
                fallback=provider.display_name or provider.provider_id,
            )
            normalized_base_url = provider.base_url
            api_style = provider.api_style
            local_endpoint_policy = "deny"
            endpoint_scope = "public_https"
            normalized_base_url_policy = provider.base_url_policy
        else:
            if provider_id not in {None, "openai_compatible"}:
                raise ModelProviderSettingsError(
                    "provider_not_allowed", "custom profile must be OpenAI-compatible"
                )
            provider_id = "openai_compatible"
            api_style = value.get("api_style")
            if api_style not in _ALLOWED_API_STYLES:
                raise ModelProviderSettingsError(
                    "api_style_invalid", "OpenAI-compatible API style is invalid"
                )
            local_endpoint_policy = value.get("local_endpoint_policy", "deny")
            normalized_base_url, endpoint_scope = _normalize_custom_base_url(
                value.get("base_url"),
                local_endpoint_policy=local_endpoint_policy,
            )
            expected_policy = (
                "openai_compatible_loopback_v1"
                if endpoint_scope == "loopback"
                else "openai_compatible_public_https_v1"
            )
            if base_url_policy not in {None, expected_policy}:
                raise ModelProviderSettingsError(
                    "base_url_policy_not_allowed", "base URL policy is not allowed"
                )
            if value.get("endpoint_scope") not in {None, endpoint_scope}:
                raise ModelProviderSettingsError(
                    "base_url_policy_not_allowed", "endpoint scope does not match base URL"
                )
            normalized_base_url_policy = expected_policy
            capabilities_value = _validate_capabilities(
                supplied_capabilities,
                required=True,
            )
            assert capabilities_value is not None
            capabilities = capabilities_value
            display_name = _normalize_display_name(value.get("display_name"))
        data_classes = value.get("allowed_data_classes")
        if (
            not isinstance(data_classes, (list, tuple))
            or not data_classes
            or len(data_classes) != len(set(data_classes))
            or not set(data_classes).issubset(_ALLOWED_DATA_CLASSES)
            or "synthetic_only" not in data_classes
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid", "invalid allowed data classes"
            )
        image_egress = value.get("image_egress")
        if image_egress not in _ALLOWED_IMAGE_EGRESS:
            raise ModelProviderSettingsError(
                "data_policy_invalid", "invalid image egress policy"
            )
        if image_egress == "redacted_question_only" and (
            "vision" not in capabilities
            or "question_image_redacted" not in data_classes
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid", "image egress is not allowed for this profile"
            )
        if image_egress == "teacher_confirmed_source_pages" and (
            "vision" not in capabilities or "source_page_image" not in data_classes
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid", "source-page image egress is not allowed for this profile"
            )
        if image_egress == "teacher_confirmed_student_pages" and (
            "vision" not in capabilities or "student_answer_image" not in data_classes
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid",
                "student-page image egress is not allowed for this profile",
            )
        if image_egress == "teacher_confirmed_visual_pages" and (
            "vision" not in capabilities
            or not {"source_page_image", "student_answer_image"}.intersection(data_classes)
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid",
                "teacher-confirmed visual-page egress is not allowed for this profile",
            )
        if (
            "question_image_redacted" in data_classes
            and image_egress != "redacted_question_only"
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid", "image data class requires image egress policy"
            )
        if (
            "source_page_image" in data_classes
            and image_egress
            not in {"teacher_confirmed_source_pages", "teacher_confirmed_visual_pages"}
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid", "source-page data class requires source-page egress policy"
            )
        if (
            "student_answer_image" in data_classes
            and image_egress
            not in {"teacher_confirmed_student_pages", "teacher_confirmed_visual_pages"}
        ):
            raise ModelProviderSettingsError(
                "data_policy_invalid",
                "student-page data class requires student-page egress policy",
            )
        expected_ref = _credential_ref(profile_id)
        supplied_ref = value.get("credential_ref")
        if supplied_ref is not None and supplied_ref != expected_ref:
            raise ModelProviderSettingsError(
                "credential_reference_invalid", "invalid credential reference"
            )
        return {
            "profile_id": profile_id,
            "provider_kind": provider_kind,
            "provider_id": provider_id,
            "display_name": display_name,
            "model_id": model_id,
            "base_url_policy": normalized_base_url_policy,
            "base_url": normalized_base_url,
            "api_style": api_style,
            "local_endpoint_policy": local_endpoint_policy,
            "endpoint_scope": endpoint_scope,
            "credential_ref": expected_ref,
            "revision": _new_revision(),
            "capabilities": list(capabilities),
            "allowed_data_classes": list(data_classes),
            "image_egress": image_egress,
            "last_probe": _validate_last_probe(value.get("last_probe")),
        }

    def _validate_stored_profile(self, value: Any) -> dict[str, Any]:
        legacy_fields = {
            "profile_id",
            "provider_id",
            "model_id",
            "base_url_policy",
            "credential_ref",
            "revision",
            "capabilities",
            "allowed_data_classes",
            "image_egress",
            "last_probe",
        }
        current_fields = legacy_fields | {
            "provider_kind",
            "display_name",
            "base_url",
            "api_style",
            "local_endpoint_policy",
            "endpoint_scope",
        }
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset(legacy_fields),
            frozenset(current_fields),
        }:
            raise ModelProviderSettingsError(
                "settings_corrupt", "model provider settings are invalid", 503
            )
        revision = value.get("revision")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ModelProviderSettingsError(
                "settings_corrupt", "model provider settings are invalid", 503
            )
        try:
            input_value = dict(value)
            input_value.pop("revision", None)
            normalized = self._normalize_profile_input(input_value, stored=True)
        except ModelProviderSettingsError:
            raise ModelProviderSettingsError(
                "settings_corrupt", "model provider settings are invalid", 503
            ) from None
        normalized["revision"] = revision
        return normalized

    @staticmethod
    def _check_revision(
        profile: Mapping[str, Any] | None, expected_revision: str | None
    ) -> None:
        if expected_revision is not None and (
            not isinstance(expected_revision, str)
            or not _REVISION.fullmatch(expected_revision)
        ):
            raise ModelProviderSettingsError(
                "revision_invalid", "invalid settings revision"
            )
        if profile is None:
            if expected_revision is not None:
                raise ModelProviderSettingsError(
                    "revision_conflict", "settings changed; reload and retry", 409
                )
            return
        if expected_revision is None or profile["revision"] != expected_revision:
            raise ModelProviderSettingsError(
                "revision_conflict", "settings changed; reload and retry", 409
            )

    def _backend_available(self) -> bool:
        try:
            return bool(self._backend.available())
        except Exception:  # noqa: BLE001 - injected backends are untrusted.
            return False

    def _read_credential_strict(self, target_name: str) -> str | None:
        if not self._backend_available():
            raise CredentialBackendUnavailable()
        try:
            secret = self._backend.read(target_name)
        except CredentialBackendUnavailable:
            raise CredentialBackendUnavailable() from None
        except Exception:  # noqa: BLE001 - suppress backend secret-bearing errors.
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            ) from None
        if secret is not None and not isinstance(secret, str):
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            )
        return secret

    def _credential_exists_strict(self, target_name: str) -> bool:
        if not self._backend_available():
            raise CredentialBackendUnavailable()
        try:
            exists = self._backend.exists(target_name)
        except CredentialBackendUnavailable:
            raise CredentialBackendUnavailable() from None
        except Exception:  # noqa: BLE001 - suppress backend secret-bearing errors.
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            ) from None
        if not isinstance(exists, bool):
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            )
        return exists

    def _write_credential_strict(self, target_name: str, secret: str) -> None:
        if not self._backend_available():
            raise CredentialBackendUnavailable()
        try:
            self._backend.write(target_name, secret)
        except CredentialBackendUnavailable:
            raise CredentialBackendUnavailable() from None
        except Exception:  # noqa: BLE001 - suppress backend secret-bearing errors.
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            ) from None

    def _delete_credential_strict(self, target_name: str) -> bool:
        if not self._backend_available():
            raise CredentialBackendUnavailable()
        try:
            return bool(self._backend.delete(target_name))
        except CredentialBackendUnavailable:
            raise CredentialBackendUnavailable() from None
        except Exception:  # noqa: BLE001 - suppress backend secret-bearing errors.
            raise ModelProviderSettingsError(
                "credential_operation_failed",
                "the operating-system credential operation failed",
                503,
            ) from None

    def _credential_state(self, target_name: str) -> str:
        try:
            exists = self._credential_exists_strict(target_name)
        except ModelProviderSettingsError:
            return "credential_store_unavailable"
        return "configured" if exists else "not_configured"

    def _capability_evidence(
        self, profile: Mapping[str, Any]
    ) -> tuple[str, dict[str, list[str]], list[str]]:
        catalog_model = (
            self._catalog_model_policy(profile["provider_id"], profile["model_id"])
            if profile["provider_kind"] == "preset"
            else None
        )
        catalog = list(catalog_model.capabilities) if catalog_model is not None else []
        declared = list(profile["capabilities"])
        last_probe = profile.get("last_probe")
        probed = (
            ["text", "structured_output"]
            if isinstance(last_probe, Mapping)
            and last_probe.get("status") == "succeeded"
            and last_probe.get("connection_state") == "connected"
            and last_probe.get("model_invoked") is True
            else []
        )
        effective = [
            capability
            for capability in ("text", "vision", "structured_output")
            if capability in set(catalog) | set(declared) | set(probed)
        ]
        unknown = [
            capability
            for capability in ("text", "vision", "structured_output")
            if capability not in effective
        ]
        return (
            "catalog_model" if catalog_model is not None else "unverified_custom_model",
            {
                "declared": declared,
                "catalog": catalog,
                "probed": probed,
                "unknown": unknown,
            },
            effective,
        )

    def _public_profile(
        self, profile: Mapping[str, Any], *, credential_state: str | None = None
    ) -> dict[str, Any]:
        state = credential_state or self._credential_state(profile["credential_ref"])
        result = dict(profile)
        result["capabilities"] = list(profile["capabilities"])
        result["allowed_data_classes"] = list(profile["allowed_data_classes"])
        result["last_probe"] = None
        if profile["last_probe"] is not None:
            result["last_probe"] = dict(profile["last_probe"])
            if isinstance(profile["last_probe"].get("usage"), Mapping):
                result["last_probe"]["usage"] = dict(profile["last_probe"]["usage"])
        model_status, capability_evidence, effective_capabilities = (
            self._capability_evidence(profile)
        )
        result["model_status"] = model_status
        result["capability_evidence"] = capability_evidence
        result["effective_capabilities"] = effective_capabilities
        result["follow_redirects"] = False
        result["credential_state"] = state
        result["model_configured"] = state == "configured"
        result["offline_workbench_available"] = True
        return result

    def list_metadata(self) -> list[dict[str, Any]]:
        with self._locked():
            profiles = self._load_unlocked()
        return [self._public_profile(profiles[key]) for key in sorted(profiles)]

    def upsert_metadata(
        self,
        value: Mapping[str, Any],
        *,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        normalized = self._normalize_profile_input(value)
        profile_id = normalized["profile_id"]
        with self._locked():
            profiles = self._load_unlocked()
            previous = profiles.get(profile_id)
            self._check_revision(previous, expected_revision)
            if previous is not None and any(
                previous[key] != normalized[key]
                for key in (
                    "provider_kind",
                    "provider_id",
                    "base_url_policy",
                    "base_url",
                    "api_style",
                    "local_endpoint_policy",
                )
            ):
                state = self._credential_state(previous["credential_ref"])
                if state != "not_configured":
                    raise ModelProviderSettingsError(
                        "credential_rotation_required",
                        "delete the existing credential before changing provider policy",
                        409,
                    )
            profiles[profile_id] = normalized
            if len(profiles) > _MAX_PROFILES:
                raise ModelProviderSettingsError(
                    "profile_limit_reached", "model provider profile limit reached"
                )
            self._save_unlocked(profiles)
        return self._public_profile(normalized)

    @staticmethod
    def _validate_secret(secret: Any) -> str:
        if (
            not isinstance(secret, str)
            or not 8 <= len(secret) <= 2048
            or any(not 0x21 <= ord(character) <= 0x7E for character in secret)
        ):
            raise ModelProviderSettingsError("credential_invalid", "invalid credential")
        return secret

    def put_credential(
        self, profile_id: str, secret: str, *, expected_revision: str
    ) -> dict[str, Any]:
        profile_id = _validate_profile_id(profile_id)
        validated_secret = self._validate_secret(secret)
        with self._locked():
            profiles = self._load_unlocked()
            profile = profiles.get(profile_id)
            if profile is None:
                raise ModelProviderSettingsError(
                    "profile_not_found", "model provider profile was not found", 404
                )
            self._check_revision(profile, expected_revision)
            previous_secret = self._read_credential_strict(profile["credential_ref"])
            self._write_credential_strict(profile["credential_ref"], validated_secret)
            updated = dict(profile)
            updated["revision"] = _new_revision()
            updated["last_probe"] = {
                "status": "ready_not_invoked",
                "checked_at": None,
                "error_code": None,
                "model_invoked": False,
                "connection_state": "not_started",
                "probe_run_id": None,
                "receipt_id": None,
                "receipt_sha256": None,
                "latency_ms": None,
                "usage": None,
            }
            profiles[profile_id] = updated
            try:
                self._save_unlocked(profiles)
            except Exception:
                try:
                    if previous_secret is None:
                        self._delete_credential_strict(profile["credential_ref"])
                    else:
                        self._write_credential_strict(
                            profile["credential_ref"], previous_secret
                        )
                except ModelProviderSettingsError:
                    raise ModelProviderSettingsError(
                        "credential_rollback_failed",
                        "credential rollback failed after settings save failure",
                        503,
                    ) from None
                raise
            finally:
                previous_secret = None
        return self._public_profile(updated, credential_state="configured")

    def delete_credential(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]:
        profile_id = _validate_profile_id(profile_id)
        with self._locked():
            profiles = self._load_unlocked()
            profile = profiles.get(profile_id)
            if profile is None:
                raise ModelProviderSettingsError(
                    "profile_not_found", "model provider profile was not found", 404
                )
            self._check_revision(profile, expected_revision)
            self._delete_credential_strict(profile["credential_ref"])
            updated = dict(profile)
            updated["revision"] = _new_revision()
            updated["last_probe"] = {
                "status": "never",
                "checked_at": None,
                "error_code": None,
                "model_invoked": False,
                "connection_state": "not_started",
                "probe_run_id": None,
                "receipt_id": None,
                "receipt_sha256": None,
                "latency_ms": None,
                "usage": None,
            }
            profiles[profile_id] = updated
            try:
                self._save_unlocked(profiles)
            except Exception:  # noqa: BLE001 - preserve fail-closed key deletion.
                # A successful deletion is never undone.  Restoring a key after
                # a metadata failure would silently re-enable outbound access.
                # The old metadata may remain, but all public readiness paths
                # check CredentialBackend.exists and therefore fail closed.
                raise ModelProviderSettingsError(
                    "credential_deleted_metadata_stale",
                    "credential was deleted but settings metadata could not be updated",
                    503,
                ) from None
        return self._public_profile(updated, credential_state="not_configured")

    def credential_exists(self, profile_id: str) -> bool:
        """Return credential presence without reading or decoding its value."""

        profile_id = _validate_profile_id(profile_id)
        with self._locked():
            profile = self._load_unlocked().get(profile_id)
            if profile is None:
                return False
            return self._credential_exists_strict(profile["credential_ref"])

    @contextlib.contextmanager
    def borrow_probe_context(
        self, profile_id: str, *, expected_revision: str
    ) -> Iterator[ModelProviderProbeContext]:
        """Borrow one credential for a fixed synthetic probe only.

        The file/credential lock is released before the network operation so a
        concurrent credential rotation or deletion is never blocked.  Such a
        change makes the later CAS commit stale.
        """

        profile_id = _validate_profile_id(profile_id)
        secret: str | None = None
        context: ModelProviderProbeContext | None = None
        with self._locked():
            profile = self._load_unlocked().get(profile_id)
            if profile is None:
                raise ModelProviderSettingsError(
                    "model_not_configured", "model is not configured", 409
                )
            self._check_revision(profile, expected_revision)
            secret = self._read_credential_strict(profile["credential_ref"])
            if not secret:
                raise ModelProviderSettingsError(
                    "model_not_configured", "model is not configured", 409
                )
            context = ModelProviderProbeContext(
                profile_id=profile_id,
                provider_id=profile["provider_id"],
                model_id=profile["model_id"],
                base_url_policy=profile["base_url_policy"],
                base_url=profile["base_url"],
                revision=profile["revision"],
                api_key=secret,
                provider_kind=profile["provider_kind"],
                api_style=profile["api_style"],
                local_endpoint_policy=profile["local_endpoint_policy"],
            )
        try:
            yield context
        finally:
            # Python cannot guarantee physical zeroization of immutable strings;
            # dropping both references is the accurate boundary we can promise.
            context = None
            secret = None

    @contextlib.contextmanager
    def borrow_invocation_context(
        self, profile_id: str, *, expected_revision: str
    ) -> Iterator[ModelProviderProbeContext]:
        """Borrow one credential for a separately gated production adapter.

        The caller must independently enforce its task capability, data policy,
        model capability, and egress confirmation before entering this context.
        Revision control and the no-persistence/no-logging secret boundary are
        identical to the fixed synthetic probe path.
        """

        with self.borrow_probe_context(
            profile_id, expected_revision=expected_revision
        ) as context:
            yield context

    def commit_probe_result(
        self,
        profile_id: str,
        *,
        expected_revision: str,
        last_probe: Mapping[str, Any],
    ) -> bool:
        """CAS-commit a sanitized terminal result if config and key are current."""

        profile_id = _validate_profile_id(profile_id)
        normalized = _validate_last_probe(last_probe)
        if normalized is None or normalized["status"] not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ModelProviderSettingsError(
                "probe_metadata_invalid", "invalid probe metadata"
            )
        with self._locked():
            profiles = self._load_unlocked()
            profile = profiles.get(profile_id)
            if (
                profile is None
                or profile["revision"] != expected_revision
                or not self._credential_exists_strict(profile["credential_ref"])
            ):
                return False
            updated = dict(profile)
            updated["revision"] = _new_revision()
            updated["last_probe"] = normalized
            profiles[profile_id] = updated
            self._save_unlocked(profiles)
        return True

    def synthetic_test_status(self, profile_id: str) -> dict[str, Any]:
        """Readiness preflight only; this method never performs a network call."""

        profile_id = _validate_profile_id(profile_id)
        with self._locked():
            profile = self._load_unlocked().get(profile_id)
        if profile is None:
            return {
                "profile_id": profile_id,
                "status": "model_not_configured",
                "ready": False,
                "model_invoked": False,
                "offline_workbench_available": True,
            }
        state = self._credential_state(profile["credential_ref"])
        status_value = {
            "configured": "ready_for_synthetic_test",
            "not_configured": "model_not_configured",
            "credential_store_unavailable": "model_not_configured",
        }[state]
        return {
            "profile_id": profile_id,
            "provider_id": profile["provider_id"],
            "model_id": profile["model_id"],
            "base_url_policy": profile["base_url_policy"],
            "capabilities": list(profile["capabilities"]),
            "status": status_value,
            "credential_state": state,
            "ready": state == "configured",
            "model_invoked": False,
            "follow_redirects": False,
            "offline_workbench_available": True,
        }

    def invocation_policy(
        self, profile_id: str, *, expected_revision: str
    ) -> dict[str, Any]:
        """Return a non-secret, closed endpoint descriptor for a future caller."""

        profile_id = _validate_profile_id(profile_id)
        with self._locked():
            profile = self._load_unlocked().get(profile_id)
        if profile is None:
            raise ModelProviderSettingsError(
                "model_not_configured", "model is not configured", 409
            )
        self._check_revision(profile, expected_revision)
        model_status, capability_evidence, effective_capabilities = (
            self._capability_evidence(profile)
        )
        return {
            "profile_id": profile_id,
            "provider_kind": profile["provider_kind"],
            "provider_id": profile["provider_id"],
            "display_name": profile["display_name"],
            "model_id": profile["model_id"],
            "model_status": model_status,
            "base_url_policy": profile["base_url_policy"],
            "base_url": profile["base_url"],
            "api_style": profile["api_style"],
            "local_endpoint_policy": profile["local_endpoint_policy"],
            "endpoint_scope": profile["endpoint_scope"],
            "follow_redirects": False,
            "capabilities": list(profile["capabilities"]),
            "capability_evidence": capability_evidence,
            "effective_capabilities": effective_capabilities,
            "allowed_data_classes": list(profile["allowed_data_classes"]),
            "image_egress": profile["image_egress"],
            "revision": profile["revision"],
        }


__all__ = [
    "CREDENTIAL_TARGET_PREFIX",
    "DEFAULT_PROVIDER_POLICIES",
    "LOCK_FILE_NAME",
    "MODEL_PROVIDER_SETTINGS_WRITE_CAPABILITY",
    "SETTINGS_FILE_NAME",
    "SETTINGS_SCHEMA_VERSION",
    "CredentialBackend",
    "CredentialBackendUnavailable",
    "ModelPolicy",
    "ModelProviderProbeContext",
    "ModelProviderSettingsError",
    "ModelProviderSettingsStore",
    "ProviderPolicy",
    "WindowsCredentialManagerBackend",
]
