from __future__ import annotations

"""Immutable local release-store core for the teacher workbench.

This module intentionally has no HTTP routes, no subprocess runner, and no
authority-promotion surface.  It stores only a caller-materialized browse
closure, validates a structured fixed-recipe regression receipt, and changes
the selected local candidate release after an explicit compare-and-swap call.

The active pointer is not treated as a release by itself: every pointer read
revalidates the immutable candidate bytes, the PASS receipt, and the append-only
activation event that it binds.  A rollback therefore selects real historical
bytes instead of merely changing a version label.
"""

import contextlib
import ctypes
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import uuid
from collections.abc import Iterator, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from jsonschema import Draft202012Validator


RELEASE_CANDIDATE_SCHEMA_VERSION = "shchem.workbench.release_candidate.v1"
REGRESSION_RECEIPT_SCHEMA_VERSION = "shchem.workbench.regression_receipt.v1"
ACTIVATION_EVENT_SCHEMA_VERSION = "shchem.workbench.activation_event.v1"
ACTIVE_POINTER_SCHEMA_VERSION = "shchem.workbench.active_pointer.v1"
RELEASE_CANDIDATE_COMMIT_SCHEMA_VERSION = (
    "shchem.workbench.release_candidate_commit.v1"
)
RECIPE_SCHEMA_VERSION = "shchem.workbench.release_recipe.v1"
RECIPE_ID = "SHCHEM-WORKBENCH-RELEASE-REGRESSION-V1"

CONTRACT_RELATIVE_ROOT = Path(
    "sh-chem-db/kb/workbench/workbench_release_control_v1"
)
SCHEMA_FILES = {
    "release_candidate": "release_candidate.schema.json",
    "regression_receipt": "regression_receipt.schema.json",
    "activation_event": "activation_event.schema.json",
    "active_pointer": "active_pointer.schema.json",
}

# These values are deliberately byte-pinned.  They are filled with the hashes
# of the four Draft 2020-12 contracts and the fixed, non-executable recipe.
CONTRACT_FILE_SHA256 = {
    "release_candidate.schema.json": "9266fea878f2b94ae5440d2e1ea4c319665ce6dc8fe306283efc4a3b4c9916e6",
    "regression_receipt.schema.json": "acd59a75e4592f2db14c86e45ceba92f02d2c194b9f769aec280f420feb6767b",
    "activation_event.schema.json": "23233b131aa43e7ec2aeb08702e7170efd4dac829ee023034dc0ddf6310cf755",
    "active_pointer.schema.json": "75155fd55bb2a332881b171f86efee9e63e8142f2ba24f0ca1dfabc088f20dd1",
    "release_recipe.json": "879163cc8c1170b2fce32a0fe64df1bd3f4bd173ddbf341ef3085782003a6ab0",
}
RECIPE_SELF_SHA256 = (
    "40b8f9dc9b6f594029643d62b3e0ff0b2c68428e65d7e8ac98c0c6018cbb6e19"
)

CLOSURE_ALGORITHM = (
    "canonical-sha256-of-sorted-relative-path-byte-descriptors-v1"
)
MAX_ARTIFACTS = 20_000
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_CLOSURE_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^WBREL-[0-9a-f]{64}$")
_RECEIPT_ID = re.compile(r"^WBRR-[0-9a-f]{32}$")
_EVENT_ID = re.compile(r"^WBAE-[0-9a-f]{32}$")
_REVISION = re.compile(r"^WBREV-[0-9a-f]{32}$")
_PRINCIPAL_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")
_EVENT_FILENAME = re.compile(
    r"^(?P<sequence>[0-9]{12})-(?P<event_id>WBAE-[0-9a-f]{32})\.json$"
)
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

AUTHORITY = {
    "scope": "local_candidate_browse_release_only",
    "candidate_only": True,
    "read_only": True,
    "human_reviewed": False,
    "official": False,
    "retrieval_ready": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "external_publication_allowed": False,
}

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class WorkbenchReleaseControlError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int = 409) -> WorkbenchReleaseControlError:
    return WorkbenchReleaseControlError(code, message, status)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error(
            "release_record_not_json",
            "release-control record is not finite JSON",
            400,
        ) from exc


def canonical_json_sha256(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _record_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(value)) + b"\n"


def _self_hash(value: Mapping[str, Any]) -> str:
    projected = deepcopy(dict(value))
    projected.pop("self_sha256", None)
    return canonical_json_sha256(projected)


def _with_self_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("self_sha256", None)
    result["self_sha256"] = canonical_json_sha256(result)
    return result


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise _error("release_record_too_large", f"{label} exceeds the JSON limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _error(
                    "release_record_json_invalid",
                    f"{label} contains a duplicate JSON key",
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except WorkbenchReleaseControlError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(
            "release_record_json_invalid",
            f"{label} is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "release_record_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _parse_canonical_record(raw: bytes, label: str) -> dict[str, Any]:
    value = _strict_json_object(raw, label)
    if _record_bytes(value) != raw:
        raise _error(
            "release_record_not_canonical", f"{label} bytes are not canonical"
        )
    if value.get("self_sha256") != _self_hash(value):
        raise _error(
            "release_record_self_hash_mismatch", f"{label} self hash mismatched"
        )
    return value


def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)


def _assert_components_not_reparse(path: Path) -> None:
    absolute = path.absolute()
    for component in tuple(reversed(absolute.parents)) + (absolute,):
        if component == component.parent:
            continue
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        if _is_reparse_or_symlink(details):
            raise _error(
                "release_store_path_unsafe",
                "release-store path contains a symlink or reparse point",
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

    current_sid = _windows_current_user_sid_string()
    descriptor = ctypes.c_void_p()
    sddl = f"O:{current_sid}D:P(A;;FA;;;{current_sid})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise _error(
            "release_store_permissions_failed",
            "could not apply owner-only release-store permissions",
            503,
        )
    try:
        if not set_security(str(path), 0x00000001 | 0x00000004, descriptor):
            raise _error(
                "release_store_permissions_failed",
                "could not apply owner-only release-store permissions",
                503,
            )
    finally:
        kernel32.LocalFree(descriptor)


def _windows_current_user_sid_string() -> str:
    if os.name != "nt":
        raise _error(
            "release_store_permissions_unverifiable",
            "Windows user SID requested on a non-Windows host",
            503,
        )
    from ctypes import wintypes

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token_query = 0x0008
    token_user = 1
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise _error(
            "release_store_permissions_unverifiable",
            "current Windows security token is unavailable",
            503,
        )
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(needed))
        if needed.value <= 0:
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID is unavailable",
                503,
            )
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID is unavailable",
                503,
            )
        token_user_value = ctypes.cast(
            buffer, ctypes.POINTER(TOKEN_USER)
        ).contents
        if not token_user_value.User.Sid:
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID is unavailable",
                503,
            )
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
            token_user_value.User.Sid, ctypes.byref(sid_text)
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID could not be serialized",
                503,
            )
        try:
            return sid_text.value
        finally:
            kernel32.LocalFree(sid_text)
    finally:
        kernel32.CloseHandle(token)


def _assert_owner_only(path: Path, *, directory: bool) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise _error("release_store_path_missing", "release-store path is missing") from exc
    if _is_reparse_or_symlink(details):
        raise _error("release_store_path_unsafe", "release-store path is unsafe", 403)
    if directory and not stat.S_ISDIR(details.st_mode):
        raise _error("release_store_path_unsafe", "release-store directory is invalid", 403)
    if not directory and (
        not stat.S_ISREG(details.st_mode) or getattr(details, "st_nlink", 1) != 1
    ):
        raise _error("release_store_path_unsafe", "release-store file is unsafe", 403)
    if os.name != "nt":
        if hasattr(os, "geteuid") and details.st_uid != os.geteuid():
            raise _error(
                "release_store_owner_mismatch",
                "release-store path is not owned by the current user",
                403,
            )
        if stat.S_IMODE(details.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            raise _error(
                "release_store_permissions_broad",
                "release-store permissions are broader than the owner",
                403,
            )
    if os.name == "nt":
        if not _windows_path_owner_is_current(path):
            raise _error(
                "release_store_owner_mismatch",
                "release-store path is not owned by the current Windows user",
                403,
            )
        if not _windows_path_dacl_is_owner_only(path):
            raise _error(
                "release_store_permissions_broad",
                "release-store ACL is not owner-only",
                403,
            )


def _windows_path_owner_is_current(path: Path) -> bool:
    if os.name != "nt":
        return True
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token_query = 0x0008
    token_user = 1
    owner_security_information = 0x00000001
    se_file_object = 1

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.EqualSid.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise _error(
            "release_store_permissions_unverifiable",
            "current Windows security token is unavailable",
            503,
        )
    security_descriptor = ctypes.c_void_p()
    owner_sid = ctypes.c_void_p()
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(needed))
        if needed.value <= 0:
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID is unavailable",
                503,
            )
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "current Windows user SID is unavailable",
                503,
            )
        current_sid = ctypes.c_void_p.from_buffer(buffer).value
        result = advapi32.GetNamedSecurityInfoW(
            str(path),
            se_file_object,
            owner_security_information,
            ctypes.byref(owner_sid),
            None,
            None,
            None,
            ctypes.byref(security_descriptor),
        )
        if result != 0 or not current_sid or not owner_sid.value:
            raise _error(
                "release_store_permissions_unverifiable",
                "release-store owner SID is unavailable",
                503,
            )
        return bool(advapi32.EqualSid(current_sid, owner_sid))
    finally:
        if security_descriptor.value:
            kernel32.LocalFree(security_descriptor)
        kernel32.CloseHandle(token)


def _windows_path_dacl_is_owner_only(path: Path) -> bool:
    """Inspect the DACL structurally; never depend on localized ``icacls`` text."""

    if os.name != "nt":
        return True
    from ctypes import wintypes

    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    acl_size_information = 2
    access_allowed_ace_type = 0
    file_all_access = 0x001F01FF
    se_dacl_protected = 0x1000

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.EqualSid.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    owner_sid = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    owner_rights_sid = ctypes.c_void_p()
    try:
        result = advapi32.GetNamedSecurityInfoW(
            str(path),
            se_file_object,
            owner_security_information | dacl_security_information,
            ctypes.byref(owner_sid),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if result != 0 or not owner_sid.value or not dacl.value:
            raise _error(
                "release_store_permissions_unverifiable",
                "release-store DACL is unavailable",
                503,
            )
        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        if not advapi32.GetSecurityDescriptorControl(
            security_descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "release-store DACL control is unavailable",
                503,
            )
        if not (control.value & se_dacl_protected):
            return False
        information = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            acl_size_information,
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "release-store DACL entries are unavailable",
                503,
            )
        if information.AceCount != 1:
            return False
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, 0, ctypes.byref(ace)) or not ace.value:
            raise _error(
                "release_store_permissions_unverifiable",
                "release-store DACL entry is unavailable",
                503,
            )
        header = ACE_HEADER.from_address(ace.value)
        if header.AceType != access_allowed_ace_type or header.AceSize < 12:
            return False
        access_mask = ctypes.c_uint32.from_address(ace.value + 4).value
        if access_mask & file_all_access != file_all_access:
            return False
        ace_sid = ctypes.c_void_p(ace.value + 8)
        if advapi32.EqualSid(ace_sid, owner_sid):
            return True
        if not advapi32.ConvertStringSidToSidW(
            "S-1-3-4", ctypes.byref(owner_rights_sid)
        ):
            raise _error(
                "release_store_permissions_unverifiable",
                "Windows owner-rights SID is unavailable",
                503,
            )
        return bool(advapi32.EqualSid(ace_sid, owner_rights_sid))
    finally:
        if owner_rights_sid.value:
            kernel32.LocalFree(owner_rights_sid)
        if security_descriptor.value:
            kernel32.LocalFree(security_descriptor)


def _mkdir_secure(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    _assert_components_not_reparse(path)
    if not parents:
        path.mkdir(parents=False, exist_ok=exist_ok)
        created = [path]
    else:
        missing: list[Path] = []
        cursor = path
        while not os.path.lexists(cursor):
            missing.append(cursor)
            cursor = cursor.parent
        if not missing and not exist_ok:
            raise FileExistsError(path)
        created = []
        for candidate in reversed(missing):
            candidate.mkdir(parents=False, exist_ok=False)
            created.append(candidate)
        if not created:
            created = [path]
    for candidate in created:
        _assert_components_not_reparse(candidate)
        _apply_owner_only_permissions(candidate, directory=True)
        _assert_owner_only(candidate, directory=True)


def _sync_parent_directory(path: Path) -> None:
    """Persist a directory entry on POSIX.

    Windows does not permit ``FlushFileBuffers`` on the directory handles used
    here.  Windows moves therefore use ``MOVEFILE_WRITE_THROUGH`` below.
    """

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _windows_move_file(source: Path, target: Path, *, replace: bool) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file_ex.restype = wintypes.BOOL
    movefile_replace_existing = 0x00000001
    movefile_write_through = 0x00000008
    flags = movefile_write_through
    if replace:
        flags |= movefile_replace_existing
    if not move_file_ex(str(source), str(target), flags):
        code = ctypes.get_last_error()
        if not replace and code in {80, 183}:
            raise _error(
                "release_store_append_conflict",
                "append-only path already exists",
            )
        raise OSError(code, "durable Windows move failed", str(source), str(target))


def _durable_move_no_replace(source: Path, target: Path) -> None:
    """Move a completed same-volume object into view without replacing a peer."""

    if os.name == "nt":
        _windows_move_file(source, target, replace=False)
        return
    if os.path.lexists(target):
        raise _error(
            "release_store_append_conflict", "append-only path already exists"
        )
    # The release store is serialized by its OS lock.  The precondition plus a
    # same-directory rename gives no-replace behavior for cooperating writers;
    # the post-read identity checks still fail closed against hostile races.
    os.rename(source, target)
    _sync_parent_directory(target)


def _durable_replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        _windows_move_file(source, target, replace=True)
        return
    os.replace(source, target)
    _sync_parent_directory(target)


def _process_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.absolute()))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _read_descriptor_all(descriptor: int, maximum_bytes: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum_bytes:
            raise _error("release_store_file_too_large", "release-store file is too large")
        chunks.append(chunk)


def _read_file_stable(
    path: Path,
    *,
    maximum_bytes: int = MAX_JSON_BYTES,
    require_owner_only: bool = True,
) -> bytes:
    _assert_components_not_reparse(path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise _error("release_store_file_missing", "release-store file is missing") from exc
    if (
        _is_reparse_or_symlink(before)
        or not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_nlink", 1) != 1
    ):
        raise _error("release_store_file_unsafe", "release-store file is unsafe", 403)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _error("release_store_file_unsafe", "release-store file could not be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if not os.path.samestat(before, opened):
            raise _error("release_store_file_drift", "release-store file identity changed")
        raw = _read_descriptor_all(descriptor, maximum_bytes)
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        marker = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        after_marker = (
            after_descriptor.st_dev,
            after_descriptor.st_ino,
            after_descriptor.st_size,
            after_descriptor.st_mtime_ns,
            after_descriptor.st_ctime_ns,
        )
        if (
            marker != after_marker
            or _is_reparse_or_symlink(after_path)
            or not os.path.samestat(after_descriptor, after_path)
            or len(raw) != opened.st_size
        ):
            raise _error("release_store_file_drift", "release-store file drifted during read")
        if require_owner_only:
            _assert_owner_only(path, directory=False)
        return raw
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, raw: bytes) -> tuple[int, int]:
    if not isinstance(raw, bytes):
        raise _error("release_store_write_invalid", "release-store bytes are invalid")
    _mkdir_secure(path.parent, parents=True, exist_ok=True)
    temp_path = path.parent / f".pending-write-{uuid.uuid4().hex}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temp_path, flags, 0o600)
    except FileExistsError as exc:
        raise _error(
            "release_store_pending_collision", "temporary append path collided"
        ) from exc
    moved = False
    identity: tuple[int, int] | None = None
    try:
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(descriptor)
            details = os.fstat(descriptor)
            identity = (int(details.st_dev), int(details.st_ino))
        finally:
            os.close(descriptor)
        _apply_owner_only_permissions(temp_path, directory=False)
        if _read_file_stable(temp_path, maximum_bytes=max(len(raw), 1)) != raw:
            raise _error(
                "release_store_write_drift", "release-store write did not round-trip"
            )
        _durable_move_no_replace(temp_path, path)
        moved = True
        if _read_file_stable(path, maximum_bytes=max(len(raw), 1)) != raw:
            raise _error(
                "release_store_write_drift", "release-store write did not round-trip"
            )
    except Exception:
        if moved and identity is not None:
            try:
                current = path.lstat()
                if (int(current.st_dev), int(current.st_ino)) == identity:
                    path.unlink()
                    _sync_parent_directory(path)
            except OSError:
                pass
        raise
    finally:
        if os.path.lexists(temp_path):
            try:
                temp_path.unlink()
            except OSError:
                pass
    assert identity is not None
    return identity


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise _error("release_closure_path_invalid", "browse-closure path is invalid", 400)
    if (
        "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or "//" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise _error("release_closure_path_invalid", "browse-closure path is invalid", 400)
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or str(parsed) != value or any(
        part in {"", ".", ".."} for part in parsed.parts
    ):
        raise _error("release_closure_path_invalid", "browse-closure path escapes its root", 400)
    for part in parsed.parts:
        if (
            part.endswith((" ", "."))
            or ":" in part
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise _error("release_closure_path_invalid", "browse-closure path is not portable", 400)
    return value


def _tree_inventory(root: Path) -> tuple[set[str], set[str]]:
    if not root.is_dir():
        raise _error("release_candidate_incomplete", "release candidate directory is incomplete")
    files: set[str] = set()
    directories: set[str] = set()
    for current_name, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_name)
        current_details = current.lstat()
        if _is_reparse_or_symlink(current_details):
            raise _error("release_store_path_unsafe", "release candidate contains a reparse directory", 403)
        for directory_name in directory_names:
            child = current / directory_name
            details = child.lstat()
            if _is_reparse_or_symlink(details) or not stat.S_ISDIR(details.st_mode):
                raise _error("release_store_path_unsafe", "release candidate contains an unsafe directory", 403)
            _assert_owner_only(child, directory=True)
            directories.add(child.relative_to(root).as_posix())
        for file_name in file_names:
            child = current / file_name
            details = child.lstat()
            if (
                _is_reparse_or_symlink(details)
                or not stat.S_ISREG(details.st_mode)
                or getattr(details, "st_nlink", 1) != 1
            ):
                raise _error("release_store_file_unsafe", "release candidate contains an unsafe file", 403)
            files.add(child.relative_to(root).as_posix())
    return files, directories


def _expected_directories(file_relatives: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in file_relatives:
        parent = PurePosixPath(relative).parent
        while str(parent) not in {"", "."}:
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


class WorkbenchReleaseStore:
    """Content-addressed immutable local candidate-release store."""

    def __init__(
        self,
        release_root: str | Path,
        *,
        project_root: str | Path,
        contract_root: str | Path | None = None,
        pass_receipt_verifier: Callable[[Mapping[str, Any]], bool] | None = None,
        historical_replacement_receipt_verifier: (
            Callable[[Mapping[str, Any]], Mapping[str, Any]] | None
        ) = None,
    ) -> None:
        actual_project_root = Path(__file__).resolve().parents[2]
        try:
            supplied_project_root = Path(project_root).resolve(strict=True)
        except OSError as exc:
            raise _error(
                "release_project_root_invalid",
                "project root is unavailable",
                400,
            ) from exc
        if os.path.normcase(str(supplied_project_root)) != os.path.normcase(
            str(actual_project_root)
        ):
            raise _error(
                "release_project_root_identity_mismatch",
                "project root must be the workspace that owns release control",
                403,
            )
        self.release_root = Path(release_root).absolute()
        self.project_root = actual_project_root
        self.contract_root = Path(
            contract_root
            or actual_project_root / CONTRACT_RELATIVE_ROOT
        ).absolute()
        _assert_components_not_reparse(self.project_root)
        _assert_components_not_reparse(self.release_root)
        if _paths_overlap(self.release_root, self.project_root):
            raise _error(
                "release_store_not_external",
                "release store must remain outside the project",
                403,
            )
        self.candidates_root = self.release_root / "candidates"
        self.receipts_root = self.release_root / "regression-receipts"
        self.events_root = self.release_root / "activation-events"
        self.active_pointer_path = self.release_root / "active-pointer.json"
        self.lock_path = self.release_root / ".release-control.v1.lock"
        self._thread_lock = _process_lock(self.lock_path)
        self._pass_receipt_verifier = pass_receipt_verifier
        self._historical_replacement_receipt_verifier = (
            historical_replacement_receipt_verifier
        )
        self._ensure_store()
        self._schemas, self._recipe, self._recipe_binding = self._load_contracts()

    def _ensure_store(self) -> None:
        _mkdir_secure(self.release_root, parents=True, exist_ok=True)
        for path in (self.candidates_root, self.receipts_root, self.events_root):
            _mkdir_secure(path, parents=False, exist_ok=True)

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        self._ensure_store()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or getattr(details, "st_nlink", 1) != 1
            ):
                raise _error("release_store_lock_unsafe", "release-store lock is unsafe", 403)
            path_details = self.lock_path.lstat()
            if _is_reparse_or_symlink(path_details) or not os.path.samestat(
                details, path_details
            ):
                raise _error("release_store_lock_unsafe", "release-store lock is unsafe", 403)
            if details.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            _apply_owner_only_permissions(self.lock_path, directory=False)
            _assert_owner_only(self.lock_path, directory=False)
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

    def _load_contracts(
        self,
    ) -> tuple[dict[str, Draft202012Validator], dict[str, Any], dict[str, Any]]:
        schemas: dict[str, Draft202012Validator] = {}
        for name, filename in SCHEMA_FILES.items():
            path = self.contract_root / filename
            raw = _read_file_stable(
                path, maximum_bytes=1024 * 1024, require_owner_only=False
            )
            expected = CONTRACT_FILE_SHA256[filename]
            if _sha256(raw) != expected:
                raise _error(
                    "release_contract_hash_mismatch",
                    f"release-control contract drifted: {filename}",
                )
            schema = _strict_json_object(raw, filename)
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as exc:
                raise _error(
                    "release_contract_schema_invalid",
                    f"release-control schema is invalid: {filename}",
                ) from exc
            schemas[name] = Draft202012Validator(schema)

        recipe_path = self.contract_root / "release_recipe.json"
        recipe_raw = _read_file_stable(
            recipe_path, maximum_bytes=1024 * 1024, require_owner_only=False
        )
        if _sha256(recipe_raw) != CONTRACT_FILE_SHA256["release_recipe.json"]:
            raise _error("release_recipe_hash_mismatch", "fixed release recipe drifted")
        recipe = _strict_json_object(recipe_raw, "release recipe")
        required_recipe_keys = {
            "schema_version",
            "recipe_id",
            "execution_policy",
            "execution_implemented",
            "steps",
            "automatic_activation",
            "client_supplied_commands_allowed",
            "model_invocation_required",
            "human_review_claim_allowed",
            "teaching_use_allowed",
            "publication_allowed",
            "self_sha256",
        }
        expected_steps = {
            "gateway_full_pytest",
            "central_status",
            "central_validate",
            "node_check_overlay",
            "openapi_contract",
            "release_closure",
            "launcher_smoke",
        }
        step_ids = {
            step.get("check_id")
            for step in recipe.get("steps", [])
            if isinstance(step, dict)
            and set(step) == {"check_id", "kind"}
            and step.get("kind") == "fixed_future_runner_step"
        }
        if (
            set(recipe) != required_recipe_keys
            or recipe.get("schema_version") != RECIPE_SCHEMA_VERSION
            or recipe.get("recipe_id") != RECIPE_ID
            or recipe.get("execution_policy") != "fixed_allowlist_no_client_commands"
            or recipe.get("execution_implemented") is not False
            or recipe.get("automatic_activation") is not False
            or recipe.get("client_supplied_commands_allowed") is not False
            or recipe.get("model_invocation_required") is not False
            or recipe.get("human_review_claim_allowed") is not False
            or recipe.get("teaching_use_allowed") is not False
            or recipe.get("publication_allowed") is not False
            or step_ids != expected_steps
            or len(recipe.get("steps", [])) != len(expected_steps)
            or recipe.get("self_sha256") != RECIPE_SELF_SHA256
            or _self_hash(recipe) != RECIPE_SELF_SHA256
        ):
            raise _error("release_recipe_invalid", "fixed release recipe is invalid")
        binding = {
            "recipe_id": RECIPE_ID,
            "file_sha256": _sha256(recipe_raw),
            "file_bytes": len(recipe_raw),
            "self_sha256": RECIPE_SELF_SHA256,
        }
        return schemas, recipe, binding

    @property
    def recipe_binding(self) -> dict[str, Any]:
        return deepcopy(self._recipe_binding)

    @property
    def recipe_check_ids(self) -> tuple[str, ...]:
        return tuple(step["check_id"] for step in self._recipe["steps"])

    @staticmethod
    def _validate_schema(
        validator: Draft202012Validator, value: Mapping[str, Any], label: str
    ) -> None:
        errors = sorted(
            validator.iter_errors(dict(value)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path)
            raise _error(
                "release_contract_validation_failed",
                f"{label} does not satisfy its contract at {location or '<root>'}",
            )

    @staticmethod
    def _materialize_closure(
        closure: Mapping[str, bytes],
    ) -> tuple[dict[str, bytes], dict[str, Any]]:
        if not isinstance(closure, Mapping):
            raise _error(
                "release_closure_invalid", "browse closure must be a mapping", 400
            )
        try:
            keys = list(closure.keys())
        except Exception as exc:
            raise _error("release_closure_invalid", "browse closure could not be read", 400) from exc
        if not keys or len(keys) > MAX_ARTIFACTS:
            raise _error(
                "release_closure_invalid",
                "browse closure must contain between 1 and 20000 artifacts",
                400,
            )
        materialized: dict[str, bytes] = {}
        portable_names: set[str] = set()
        for raw_path in keys:
            relative = _safe_relative_path(raw_path)
            try:
                raw = closure[raw_path]
            except Exception as exc:
                raise _error("release_closure_changed", "browse closure changed during observation") from exc
            if type(raw) is not bytes or not raw or len(raw) > MAX_ARTIFACT_BYTES:
                raise _error(
                    "release_closure_bytes_invalid",
                    "browse closure values must be non-empty immutable bytes within the limit",
                    400,
                )
            if relative in materialized:
                raise _error("release_closure_path_duplicate", "browse closure path is duplicated", 400)
            portable = relative.casefold()
            if portable in portable_names:
                raise _error(
                    "release_closure_path_duplicate",
                    "browse closure paths collide on a case-insensitive filesystem",
                    400,
                )
            materialized[relative] = raw
            portable_names.add(portable)
        ordered = [
            {
                "relative_path": relative,
                "sha256": _sha256(materialized[relative]),
                "bytes": len(materialized[relative]),
            }
            for relative in sorted(materialized)
        ]
        total = sum(item["bytes"] for item in ordered)
        if total > MAX_CLOSURE_BYTES:
            raise _error("release_closure_too_large", "browse closure exceeds the total size limit", 413)
        descriptor = {
            "algorithm": CLOSURE_ALGORITHM,
            "closure_sha256": canonical_json_sha256(ordered),
            "artifact_count": len(ordered),
            "total_bytes": total,
            "artifacts": ordered,
        }
        return materialized, descriptor

    @staticmethod
    def _closure_unchanged(
        closure: Mapping[str, bytes],
        expected_materialized: Mapping[str, bytes],
        expected_descriptor: Mapping[str, Any],
    ) -> None:
        current_materialized, current_descriptor = WorkbenchReleaseStore._materialize_closure(
            closure
        )
        if (
            current_descriptor != expected_descriptor
            or current_materialized != expected_materialized
        ):
            raise _error(
                "release_closure_changed",
                "browse closure changed after freeze observation",
            )

    def _candidate_manifest(
        self, descriptor: Mapping[str, Any]
    ) -> tuple[dict[str, Any], bytes]:
        subject = {
            "schema_version": RELEASE_CANDIDATE_SCHEMA_VERSION,
            "browse_closure": deepcopy(dict(descriptor)),
            "recipe_binding": deepcopy(self._recipe_binding),
            "authority": deepcopy(AUTHORITY),
        }
        subject_sha256 = canonical_json_sha256(subject)
        manifest = _with_self_hash(
            {
                "schema_version": RELEASE_CANDIDATE_SCHEMA_VERSION,
                "release_id": "WBREL-" + subject_sha256,
                "created_at": _utc_now(),
                "subject_sha256": subject_sha256,
                "browse_closure": deepcopy(dict(descriptor)),
                "recipe_binding": deepcopy(self._recipe_binding),
                "authority": deepcopy(AUTHORITY),
            }
        )
        self._validate_schema(
            self._schemas["release_candidate"], manifest, "release candidate"
        )
        return manifest, _record_bytes(manifest)

    @staticmethod
    def _validate_candidate_subject(manifest: Mapping[str, Any]) -> None:
        subject = {
            "schema_version": manifest["schema_version"],
            "browse_closure": manifest["browse_closure"],
            "recipe_binding": manifest["recipe_binding"],
            "authority": manifest["authority"],
        }
        expected = canonical_json_sha256(subject)
        if (
            manifest.get("subject_sha256") != expected
            or manifest.get("release_id") != "WBREL-" + expected
            or manifest.get("authority") != AUTHORITY
        ):
            raise _error(
                "release_candidate_identity_mismatch",
                "release candidate content identity mismatched",
            )

    def _pending_candidate_verify(
        self,
        path: Path,
        manifest: Mapping[str, Any],
        manifest_raw: bytes,
        materialized: Mapping[str, bytes],
        *,
        commit_raw: bytes | None = None,
    ) -> None:
        expected = {
            "release.manifest.json",
            *(f"browse-closure/{relative}" for relative in materialized),
        }
        if commit_raw is not None:
            expected.add("release.committed.json")
        files, directories = _tree_inventory(path)
        if files != expected or directories != _expected_directories(expected):
            raise _error("release_candidate_inventory_mismatch", "pending candidate inventory mismatched")
        if _read_file_stable(path / "release.manifest.json") != manifest_raw:
            raise _error("release_candidate_manifest_drift", "pending candidate manifest drifted")
        for relative, raw in materialized.items():
            artifact_path = path / "browse-closure" / Path(*PurePosixPath(relative).parts)
            if _read_file_stable(
                artifact_path, maximum_bytes=max(MAX_ARTIFACT_BYTES, len(raw))
            ) != raw:
                raise _error("release_candidate_artifact_drift", "pending candidate artifact drifted")
        parsed = _parse_canonical_record(manifest_raw, "release candidate")
        if parsed != manifest:
            raise _error("release_candidate_manifest_drift", "pending candidate manifest changed")
        if commit_raw is not None:
            observed_commit_raw = _read_file_stable(
                path / "release.committed.json"
            )
            if observed_commit_raw != commit_raw:
                raise _error(
                    "release_candidate_commit_drift",
                    "pending candidate commit drifted",
                )
            commit = _parse_canonical_record(
                observed_commit_raw, "release candidate commit"
            )
            self._validate_candidate_commit(commit, manifest, manifest_raw)

    @staticmethod
    def _candidate_commit(
        manifest: Mapping[str, Any], manifest_raw: bytes
    ) -> tuple[dict[str, Any], bytes]:
        commit = _with_self_hash(
            {
                "schema_version": RELEASE_CANDIDATE_COMMIT_SCHEMA_VERSION,
                "release_id": manifest["release_id"],
                "candidate_manifest_sha256": _sha256(manifest_raw),
                "candidate_manifest_bytes": len(manifest_raw),
                "candidate_manifest_self_sha256": manifest["self_sha256"],
                "browse_closure_sha256": manifest["browse_closure"]["closure_sha256"],
                "artifact_count": manifest["browse_closure"]["artifact_count"],
                "committed_at": _utc_now(),
                "authority": deepcopy(AUTHORITY),
            }
        )
        return commit, _record_bytes(commit)

    @staticmethod
    def _validate_candidate_commit(
        commit: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_raw: bytes,
    ) -> None:
        expected_keys = {
            "schema_version",
            "release_id",
            "candidate_manifest_sha256",
            "candidate_manifest_bytes",
            "candidate_manifest_self_sha256",
            "browse_closure_sha256",
            "artifact_count",
            "committed_at",
            "authority",
            "self_sha256",
        }
        if (
            set(commit) != expected_keys
            or commit.get("schema_version") != RELEASE_CANDIDATE_COMMIT_SCHEMA_VERSION
            or commit.get("release_id") != manifest.get("release_id")
            or commit.get("candidate_manifest_sha256") != _sha256(manifest_raw)
            or commit.get("candidate_manifest_bytes") != len(manifest_raw)
            or commit.get("candidate_manifest_self_sha256")
            != manifest.get("self_sha256")
            or commit.get("browse_closure_sha256")
            != manifest["browse_closure"]["closure_sha256"]
            or commit.get("artifact_count")
            != manifest["browse_closure"]["artifact_count"]
            or commit.get("authority") != AUTHORITY
            or not isinstance(commit.get("committed_at"), str)
            or not _UTC_TIMESTAMP.fullmatch(commit["committed_at"])
        ):
            raise _error("release_candidate_commit_invalid", "release candidate commit is invalid")

    def _read_candidate_unlocked(self, release_id: str) -> dict[str, Any]:
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            raise _error("release_id_invalid", "release id is invalid", 400)
        base = self.candidates_root / release_id
        try:
            details = base.lstat()
        except OSError as exc:
            raise _error("release_candidate_not_found", "release candidate was not found", 404) from exc
        if _is_reparse_or_symlink(details) or not stat.S_ISDIR(details.st_mode):
            raise _error("release_candidate_unsafe", "release candidate directory is unsafe", 403)
        _assert_owner_only(base, directory=True)
        manifest_path = base / "release.manifest.json"
        commit_path = base / "release.committed.json"
        manifest_raw = _read_file_stable(manifest_path)
        manifest = _parse_canonical_record(manifest_raw, "release candidate")
        self._validate_schema(
            self._schemas["release_candidate"], manifest, "release candidate"
        )
        self._validate_candidate_subject(manifest)
        if manifest.get("release_id") != release_id:
            raise _error("release_candidate_identity_mismatch", "release directory identity mismatched")
        if manifest.get("recipe_binding") != self._recipe_binding:
            raise _error("release_recipe_binding_mismatch", "release candidate recipe binding mismatched")

        descriptors = manifest["browse_closure"]["artifacts"]
        expected_files = {
            "release.manifest.json",
            "release.committed.json",
            *(f"browse-closure/{item['relative_path']}" for item in descriptors),
        }
        files, directories = _tree_inventory(base)
        if (
            files != expected_files
            or directories != _expected_directories(expected_files)
        ):
            raise _error("release_candidate_inventory_mismatch", "release candidate inventory mismatched")
        observed: dict[str, bytes] = {}
        for item in descriptors:
            relative = item["relative_path"]
            _safe_relative_path(relative)
            path = base / "browse-closure" / Path(*PurePosixPath(relative).parts)
            raw = _read_file_stable(path, maximum_bytes=MAX_ARTIFACT_BYTES)
            if len(raw) != item["bytes"] or _sha256(raw) != item["sha256"]:
                raise _error("release_candidate_artifact_mismatch", "release candidate artifact mismatched")
            observed[relative] = raw
        recalculated = [
            {
                "relative_path": relative,
                "sha256": _sha256(observed[relative]),
                "bytes": len(observed[relative]),
            }
            for relative in sorted(observed)
        ]
        if (
            manifest["browse_closure"]["closure_sha256"]
            != canonical_json_sha256(recalculated)
            or manifest["browse_closure"]["artifact_count"] != len(recalculated)
            or manifest["browse_closure"]["total_bytes"]
            != sum(item["bytes"] for item in recalculated)
        ):
            raise _error("release_candidate_closure_mismatch", "release candidate closure mismatched")

        commit_raw = _read_file_stable(commit_path)
        commit = _parse_canonical_record(commit_raw, "release candidate commit")
        self._validate_candidate_commit(commit, manifest, manifest_raw)

        # A second complete byte observation catches same-size mutation and path
        # replacement during verification.  No response is returned from a mixed read.
        if _read_file_stable(manifest_path) != manifest_raw or _read_file_stable(
            commit_path
        ) != commit_raw:
            raise _error("release_candidate_drift", "release candidate drifted during verification")
        for relative, raw in observed.items():
            path = base / "browse-closure" / Path(*PurePosixPath(relative).parts)
            if _read_file_stable(path, maximum_bytes=MAX_ARTIFACT_BYTES) != raw:
                raise _error("release_candidate_drift", "release candidate drifted during verification")
        return {
            "release_id": release_id,
            "manifest": deepcopy(manifest),
            "manifest_sha256": _sha256(manifest_raw),
            "manifest_bytes": len(manifest_raw),
            "manifest_self_sha256": manifest["self_sha256"],
            "commit": deepcopy(commit),
            "commit_sha256": _sha256(commit_raw),
            "commit_bytes": len(commit_raw),
            "authority": deepcopy(AUTHORITY),
        }

    def read_candidate(self, release_id: str) -> dict[str, Any]:
        with self._locked():
            return self._read_candidate_unlocked(release_id)

    def _recover_postrename_candidate_unlocked(
        self,
        final_path: Path,
        release_id: str,
        materialized: Mapping[str, bytes],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Finish only the exact crash window after verified rename, before commit.

        A directory with a commit file, a partial closure, a rogue entry, or a
        different content subject is never repaired or overwritten.  This makes
        a genuine post-rename crash retryable without turning generic corruption
        into an implicit delete/replace operation.
        """

        if os.path.lexists(final_path / "release.committed.json"):
            raise _error(
                "release_candidate_corrupt",
                "existing release candidate has an invalid committed package",
            )
        manifest_raw = _read_file_stable(final_path / "release.manifest.json")
        manifest = _parse_canonical_record(
            manifest_raw, "recoverable release candidate"
        )
        self._validate_schema(
            self._schemas["release_candidate"], manifest, "release candidate"
        )
        self._validate_candidate_subject(manifest)
        if (
            manifest.get("release_id") != release_id
            or manifest.get("browse_closure") != descriptor
            or manifest.get("recipe_binding") != self._recipe_binding
        ):
            raise _error(
                "release_candidate_incomplete_conflict",
                "incomplete release directory does not match the retried closure",
            )
        self._pending_candidate_verify(
            final_path, manifest, manifest_raw, materialized
        )
        commit, commit_raw = self._candidate_commit(manifest, manifest_raw)
        commit_path = final_path / "release.committed.json"
        commit_identity: tuple[int, int] | None = None
        try:
            commit_identity = _write_exclusive(commit_path, commit_raw)
            result = self._read_candidate_unlocked(release_id)
            if result["commit"] != commit:
                raise _error(
                    "release_candidate_recovery_mismatch",
                    "recovered candidate commit mismatched",
                )
            result["idempotent"] = True
            result["recovered_postrename_commit"] = True
            return result
        except Exception:
            if commit_identity is not None and os.path.lexists(commit_path):
                details = commit_path.lstat()
                if (
                    _is_reparse_or_symlink(details)
                    or (int(details.st_dev), int(details.st_ino))
                    != commit_identity
                ):
                    raise _error(
                        "release_candidate_recovery_rollback_failed",
                        "candidate recovery commit ownership changed",
                        500,
                    )
                commit_path.unlink()
            raise

    def list_candidates(self) -> list[dict[str, Any]]:
        visible: list[dict[str, Any]] = []
        with self._locked():
            for entry in sorted(self.candidates_root.iterdir(), key=lambda item: item.name):
                if not _RELEASE_ID.fullmatch(entry.name):
                    continue
                try:
                    visible.append(self._read_candidate_unlocked(entry.name))
                except WorkbenchReleaseControlError:
                    # Partial, damaged, and uncommitted candidates are deliberately
                    # invisible; direct reads still fail closed with the exact reason.
                    continue
        return visible

    def _remove_owned_tree(
        self, path: Path, *, expected_identity: tuple[int, int] | None
    ) -> None:
        if not os.path.lexists(path):
            return
        if path.parent != self.candidates_root or not (
            path.name.startswith(".pending-") or _RELEASE_ID.fullmatch(path.name)
        ):
            raise _error("release_candidate_rollback_unsafe", "candidate rollback target escaped")
        details = path.lstat()
        if (
            _is_reparse_or_symlink(details)
            or not stat.S_ISDIR(details.st_mode)
            or (
                expected_identity is not None
                and (int(details.st_dev), int(details.st_ino)) != expected_identity
            )
        ):
            raise _error("release_candidate_rollback_unsafe", "candidate rollback ownership changed")
        # Refuse to recurse through an attacker-inserted link, junction, hard
        # link, or otherwise unsafe child.  A failed cleanup is safer than
        # deleting through an identity outside this transaction.
        _tree_inventory(path)
        shutil.rmtree(path)

    def freeze_candidate(
        self,
        closure: Mapping[str, bytes],
        *,
        expected_closure_sha256: str | None = None,
    ) -> dict[str, Any]:
        materialized, descriptor = self._materialize_closure(closure)
        if expected_closure_sha256 is not None and (
            not isinstance(expected_closure_sha256, str)
            or not _SHA256.fullmatch(expected_closure_sha256)
            or descriptor["closure_sha256"] != expected_closure_sha256
        ):
            raise _error("release_closure_cas_mismatch", "browse closure CAS hash mismatched")
        manifest, manifest_raw = self._candidate_manifest(descriptor)
        release_id = manifest["release_id"]
        final_path = self.candidates_root / release_id
        pending_path: Path | None = None
        owned_path: Path | None = None
        owned_identity: tuple[int, int] | None = None
        with self._locked():
            self._closure_unchanged(closure, materialized, descriptor)
            if os.path.lexists(final_path):
                try:
                    existing = self._read_candidate_unlocked(release_id)
                except WorkbenchReleaseControlError:
                    existing = self._recover_postrename_candidate_unlocked(
                        final_path,
                        release_id,
                        materialized,
                        descriptor,
                    )
                if (
                    existing["manifest"]["subject_sha256"]
                    != manifest["subject_sha256"]
                    or existing["manifest"]["browse_closure"] != descriptor
                ):
                    raise _error("release_candidate_collision", "release id collided with different bytes")
                self._closure_unchanged(closure, materialized, descriptor)
                existing["idempotent"] = True
                return existing
            try:
                pending_path = self.candidates_root / f".pending-{uuid.uuid4().hex}"
                _mkdir_secure(pending_path, parents=False, exist_ok=False)
                pending_details = pending_path.lstat()
                owned_path = pending_path
                owned_identity = (int(pending_details.st_dev), int(pending_details.st_ino))
                _mkdir_secure(pending_path / "browse-closure", parents=False, exist_ok=False)
                for relative, raw in materialized.items():
                    target = pending_path / "browse-closure" / Path(
                        *PurePosixPath(relative).parts
                    )
                    _write_exclusive(target, raw)
                _write_exclusive(pending_path / "release.manifest.json", manifest_raw)
                self._pending_candidate_verify(
                    pending_path, manifest, manifest_raw, materialized
                )
                self._closure_unchanged(closure, materialized, descriptor)
                _, commit_raw = self._candidate_commit(manifest, manifest_raw)
                _write_exclusive(
                    pending_path / "release.committed.json", commit_raw
                )
                self._pending_candidate_verify(
                    pending_path,
                    manifest,
                    manifest_raw,
                    materialized,
                    commit_raw=commit_raw,
                )
                self._closure_unchanged(closure, materialized, descriptor)
                _durable_move_no_replace(pending_path, final_path)
                owned_path = final_path
                details = final_path.lstat()
                owned_identity = (int(details.st_dev), int(details.st_ino))
                self._closure_unchanged(closure, materialized, descriptor)
                result = self._read_candidate_unlocked(release_id)
                self._closure_unchanged(closure, materialized, descriptor)
                if result["manifest_sha256"] != _sha256(manifest_raw):
                    raise _error("release_candidate_postcommit_mismatch", "committed candidate mismatched")
                result["idempotent"] = False
                return result
            except Exception:
                if owned_path is not None:
                    try:
                        self._remove_owned_tree(
                            owned_path, expected_identity=owned_identity
                        )
                    except Exception as rollback_exc:
                        raise _error(
                            "release_candidate_rollback_failed",
                            "candidate freeze failed and rollback was incomplete",
                            500,
                        ) from rollback_exc
                raise

    def _validate_pass_receipt_structure_unlocked(
        self,
        receipt: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        if not isinstance(receipt, Mapping):
            raise _error("regression_receipt_invalid", "regression receipt must be an object", 400)
        normalized = deepcopy(dict(receipt))
        raw = _record_bytes(normalized)
        parsed = _parse_canonical_record(raw, "regression receipt")
        self._validate_schema(
            self._schemas["regression_receipt"], parsed, "regression receipt"
        )
        check_ids = [item["check_id"] for item in parsed["checks"]]
        if (
            parsed.get("verdict") != "PASS"
            or parsed.get("failures") != []
            or any(item.get("status") != "pass" for item in parsed["checks"])
            or len(check_ids) != len(set(check_ids))
            or set(check_ids) != set(self.recipe_check_ids)
            or parsed.get("release_id") != candidate["release_id"]
            or parsed.get("candidate_manifest_sha256")
            != candidate["manifest_sha256"]
            or parsed.get("candidate_manifest_bytes") != candidate["manifest_bytes"]
            or parsed.get("recipe_id") != RECIPE_ID
            or parsed.get("recipe_sha256") != self._recipe_binding["file_sha256"]
            or parsed.get("recipe_bytes") != self._recipe_binding["file_bytes"]
            or parsed.get("model_invoked") is not False
            or parsed.get("human_reviewed") is not False
            or parsed.get("teaching_use_allowed") is not False
            or parsed.get("publication_allowed") is not False
        ):
            raise _error(
                "regression_receipt_not_eligible",
                "regression receipt is not an exact fixed-recipe PASS for this release",
            )
        return parsed, raw

    def _validate_pass_receipt_unlocked(
        self,
        receipt: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        parsed, raw = self._validate_pass_receipt_structure_unlocked(
            receipt, candidate
        )
        if self._pass_receipt_verifier is None:
            raise _error(
                "regression_receipt_verifier_unavailable",
                "no trusted fixed-runner receipt verifier is configured",
                503,
            )
        try:
            trusted = self._pass_receipt_verifier(deepcopy(parsed))
        except Exception as exc:
            raise _error(
                "regression_receipt_verification_failed",
                "trusted regression receipt verification failed",
            ) from exc
        if trusted is not True:
            raise _error(
                "regression_receipt_untrusted",
                "regression receipt was not issued by the trusted fixed runner",
            )
        return parsed, raw

    def _validate_historical_pass_receipt_for_replacement_unlocked(
        self,
        receipt: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        parsed, raw = self._validate_pass_receipt_structure_unlocked(
            receipt, candidate
        )
        verifier = self._historical_replacement_receipt_verifier
        if verifier is None:
            raise _error(
                "historical_replacement_verifier_unavailable",
                "historical replacement verifier is unavailable",
                503,
            )
        try:
            projection = verifier(deepcopy(parsed))
        except Exception as exc:
            raise _error(
                "historical_replacement_receipt_verification_failed",
                "historical replacement receipt verification failed",
            ) from exc
        if (
            not isinstance(projection, Mapping)
            or projection.get("historical_for_replacement_only") is not True
            or projection.get("current_trusted") is not False
            or projection.get("serving_eligible") is not False
            or projection.get("rollback_eligible") is not False
            or projection.get("receipt_id") != parsed.get("receipt_id")
            or projection.get("release_id") != parsed.get("release_id")
        ):
            raise _error(
                "historical_replacement_receipt_untrusted",
                "receipt is not trusted solely for historical replacement",
            )
        return parsed, raw

    def _store_receipt_unlocked(
        self, receipt: Mapping[str, Any], raw: bytes
    ) -> dict[str, Any]:
        receipt_id = receipt["receipt_id"]
        if not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error("regression_receipt_invalid", "regression receipt id is invalid")
        path = self.receipts_root / f"{receipt_id}.json"
        if os.path.lexists(path):
            if _read_file_stable(path) != raw:
                raise _error("regression_receipt_collision", "regression receipt id collided")
        else:
            _write_exclusive(path, raw)
        return {
            "receipt_id": receipt_id,
            "receipt_sha256": _sha256(raw),
            "receipt_bytes": len(raw),
        }

    def _read_stored_receipt_unlocked(
        self,
        receipt_id: str,
        candidate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error("regression_receipt_invalid", "regression receipt id is invalid")
        path = self.receipts_root / f"{receipt_id}.json"
        raw = _read_file_stable(path)
        parsed = _parse_canonical_record(raw, "stored regression receipt")
        validated, canonical = self._validate_pass_receipt_unlocked(
            parsed,
            candidate,
        )
        if canonical != raw:
            raise _error("regression_receipt_drift", "stored regression receipt drifted")
        return validated, raw

    def _read_stored_receipt_for_replacement_unlocked(
        self,
        receipt_id: str,
        candidate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise _error("regression_receipt_invalid", "regression receipt id is invalid")
        path = self.receipts_root / f"{receipt_id}.json"
        raw = _read_file_stable(path)
        parsed = _parse_canonical_record(raw, "stored regression receipt")
        validated, canonical = (
            self._validate_historical_pass_receipt_for_replacement_unlocked(
                parsed, candidate
            )
        )
        if canonical != raw:
            raise _error("regression_receipt_drift", "stored regression receipt drifted")
        return validated, raw

    def _read_event_unlocked(self, sequence: int, event_id: str) -> tuple[dict[str, Any], bytes]:
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(event_id, str)
            or not _EVENT_ID.fullmatch(event_id)
        ):
            raise _error("activation_event_identity_invalid", "activation event identity is invalid")
        path = self.events_root / f"{sequence:012d}-{event_id}.json"
        raw = _read_file_stable(path)
        event = _parse_canonical_record(raw, "activation event")
        self._validate_schema(self._schemas["activation_event"], event, "activation event")
        if (
            event.get("sequence") != sequence
            or event.get("event_id") != event_id
            or event.get("authority") != AUTHORITY
        ):
            raise _error("activation_event_identity_invalid", "activation event identity mismatched")
        return event, raw

    def _verify_active_pointer_dependencies_unlocked(
        self,
        pointer: Mapping[str, Any],
    ) -> None:
        candidate = self._read_candidate_unlocked(pointer["release_id"])
        if (
            pointer["candidate_manifest_sha256"] != candidate["manifest_sha256"]
            or pointer["candidate_manifest_bytes"] != candidate["manifest_bytes"]
        ):
            raise _error("active_pointer_candidate_mismatch", "active pointer candidate binding mismatched")
        receipt, receipt_raw = self._read_stored_receipt_unlocked(
            pointer["regression_receipt_id"],
            candidate,
        )
        self._verify_active_pointer_receipt_and_event_unlocked(
            pointer, receipt, receipt_raw
        )

    def _verify_active_pointer_dependencies_for_replacement_unlocked(
        self,
        pointer: Mapping[str, Any],
    ) -> None:
        candidate = self._read_candidate_unlocked(pointer["release_id"])
        if (
            pointer["candidate_manifest_sha256"] != candidate["manifest_sha256"]
            or pointer["candidate_manifest_bytes"] != candidate["manifest_bytes"]
        ):
            raise _error("active_pointer_candidate_mismatch", "active pointer candidate binding mismatched")
        receipt, receipt_raw = self._read_stored_receipt_for_replacement_unlocked(
            pointer["regression_receipt_id"], candidate
        )
        self._verify_active_pointer_receipt_and_event_unlocked(
            pointer, receipt, receipt_raw
        )

    def _verify_active_pointer_receipt_and_event_unlocked(
        self,
        pointer: Mapping[str, Any],
        receipt: Mapping[str, Any],
        receipt_raw: bytes,
    ) -> None:
        if (
            pointer["regression_receipt_sha256"] != _sha256(receipt_raw)
            or pointer["regression_receipt_bytes"] != len(receipt_raw)
            or receipt["release_id"] != pointer["release_id"]
        ):
            raise _error("active_pointer_receipt_mismatch", "active pointer receipt binding mismatched")
        event, event_raw = self._read_event_unlocked(
            pointer["activation_sequence"], pointer["activation_event_id"]
        )
        if (
            pointer["activation_event_sha256"] != _sha256(event_raw)
            or event["release_id"] != pointer["release_id"]
            or event["new_revision"] != pointer["revision"]
            or event["action"] != pointer["action"]
            or event["candidate_manifest_sha256"]
            != pointer["candidate_manifest_sha256"]
            or event["regression_receipt_sha256"]
            != pointer["regression_receipt_sha256"]
        ):
            raise _error("active_pointer_event_mismatch", "active pointer event binding mismatched")

    def _read_active_pointer_unlocked(self, *, verify_dependencies: bool) -> dict[str, Any] | None:
        if not os.path.lexists(self.active_pointer_path):
            return None
        raw = _read_file_stable(self.active_pointer_path)
        pointer = _parse_canonical_record(raw, "active release pointer")
        self._validate_schema(self._schemas["active_pointer"], pointer, "active release pointer")
        if pointer.get("authority") != AUTHORITY:
            raise _error("active_pointer_authority_invalid", "active pointer widened authority")
        if verify_dependencies:
            self._verify_active_pointer_dependencies_unlocked(pointer)
        return pointer

    def read_active_pointer(self) -> dict[str, Any] | None:
        with self._locked():
            value = self._read_active_pointer_unlocked(verify_dependencies=True)
            return deepcopy(value) if value is not None else None

    def read_active_pointer_for_replacement(self) -> dict[str, Any] | None:
        """Read a stale head only for CAS replacement by a newly trusted release.

        The old receipt still has to satisfy its schema, candidate binding,
        self-hash and activation-event chain.  Current runner trust is
        deliberately not claimed, so callers must not serve or roll back from
        this projection.
        """

        with self._locked():
            value = self._read_active_pointer_unlocked(verify_dependencies=False)
            if value is not None:
                self._verify_active_pointer_dependencies_for_replacement_unlocked(
                    value
                )
            return deepcopy(value) if value is not None else None

    def _history_unlocked(self, pointer: Mapping[str, Any]) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        expected_sequence = pointer["activation_sequence"]
        expected_event_id: str | None = pointer["activation_event_id"]
        expected_event_sha: str | None = pointer["activation_event_sha256"]
        expected_revision: str | None = pointer["revision"]
        expected_release_id: str | None = pointer["release_id"]
        seen: set[str] = set()
        while expected_event_id is not None:
            if expected_event_id in seen or expected_sequence < 1:
                raise _error("activation_history_cycle", "activation history is cyclic")
            seen.add(expected_event_id)
            event, raw = self._read_event_unlocked(expected_sequence, expected_event_id)
            if (
                _sha256(raw) != expected_event_sha
                or event["new_revision"] != expected_revision
                or event["release_id"] != expected_release_id
            ):
                raise _error("activation_history_mismatch", "activation history binding mismatched")
            history.append(deepcopy(event))
            expected_event_id = event["previous_event_id"]
            expected_event_sha = event["previous_event_sha256"]
            expected_revision = event["previous_revision"]
            expected_release_id = event["previous_release_id"]
            expected_sequence -= 1
        if any(
            value is not None
            for value in (
                expected_event_sha,
                expected_revision,
                expected_release_id,
            )
        ) or expected_sequence != 0:
            raise _error("activation_history_root_invalid", "activation history root is invalid")
        return history

    def list_activation_history(self) -> list[dict[str, Any]]:
        with self._locked():
            pointer = self._read_active_pointer_unlocked(verify_dependencies=True)
            if pointer is None:
                return []
            return self._history_unlocked(pointer)

    def _atomic_replace_pointer(self, raw: bytes, previous_raw: bytes | None) -> None:
        handle, temp_name = tempfile.mkstemp(
            prefix=".active-pointer.", suffix=".pending", dir=self.release_root
        )
        temp_path = Path(temp_name)
        replaced = False
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _apply_owner_only_permissions(temp_path, directory=False)
            _assert_owner_only(temp_path, directory=False)
            _durable_replace(temp_path, self.active_pointer_path)
            replaced = True
            _apply_owner_only_permissions(self.active_pointer_path, directory=False)
            if _read_file_stable(self.active_pointer_path) != raw:
                raise _error("active_pointer_postwrite_mismatch", "active pointer post-write check failed")
        except Exception:
            if replaced:
                try:
                    if previous_raw is None:
                        self.active_pointer_path.unlink()
                        _sync_parent_directory(self.active_pointer_path)
                    else:
                        rollback_handle, rollback_name = tempfile.mkstemp(
                            prefix=".active-pointer.rollback.",
                            suffix=".pending",
                            dir=self.release_root,
                        )
                        rollback_path = Path(rollback_name)
                        with os.fdopen(rollback_handle, "wb") as stream:
                            stream.write(previous_raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        _apply_owner_only_permissions(rollback_path, directory=False)
                        _durable_replace(rollback_path, self.active_pointer_path)
                        _apply_owner_only_permissions(
                            self.active_pointer_path, directory=False
                        )
                        if _read_file_stable(self.active_pointer_path) != previous_raw:
                            raise OSError("pointer rollback did not round-trip")
                except Exception as rollback_exc:
                    raise _error(
                        "active_pointer_rollback_failed",
                        "active pointer write failed and rollback was incomplete",
                        500,
                    ) from rollback_exc
            raise
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _select_release(
        self,
        release_id: str,
        receipt: Mapping[str, Any],
        *,
        action: str,
        expected_revision: str | None,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        if action not in {"activate", "rollback"}:
            raise _error("activation_action_invalid", "activation action is invalid", 400)
        if not isinstance(principal_id, str) or not _PRINCIPAL_ID.fullmatch(principal_id):
            raise _error("activation_principal_invalid", "activation principal is invalid", 400)
        if (
            not isinstance(reason_zh, str)
            or not reason_zh.strip()
            or len(reason_zh) > 500
            or any(ord(character) < 32 for character in reason_zh)
        ):
            raise _error("activation_reason_invalid", "activation reason is invalid", 400)
        with self._locked():
            if action == "activate":
                current = self._read_active_pointer_unlocked(
                    verify_dependencies=False
                )
                if current is not None:
                    try:
                        self._verify_active_pointer_dependencies_unlocked(current)
                    except WorkbenchReleaseControlError:
                        self._verify_active_pointer_dependencies_for_replacement_unlocked(
                            current
                        )
            else:
                current = self._read_active_pointer_unlocked(
                    verify_dependencies=True
                )
            previous_raw = (
                _read_file_stable(self.active_pointer_path)
                if current is not None
                else None
            )
            if current is None:
                if expected_revision is not None:
                    raise _error("active_revision_conflict", "active release changed; reload and retry")
                if action != "activate":
                    raise _error("rollback_history_required", "rollback requires activation history")
                history: list[dict[str, Any]] = []
            else:
                if (
                    not isinstance(expected_revision, str)
                    or not _REVISION.fullmatch(expected_revision)
                    or expected_revision != current["revision"]
                ):
                    raise _error("active_revision_conflict", "active release changed; reload and retry")
                if current["release_id"] == release_id:
                    raise _error("release_already_active", "target release is already active")
                history = self._history_unlocked(current)
                historical_ids = {event["release_id"] for event in history}
                if action == "rollback" and release_id not in historical_ids:
                    raise _error(
                        "rollback_target_not_historical",
                        "rollback target was never an active release",
                    )
                if action == "activate" and release_id in historical_ids:
                    raise _error(
                        "historical_release_requires_rollback",
                        "a historical release must be selected through rollback",
                    )

            candidate = self._read_candidate_unlocked(release_id)
            if action == "rollback" and not any(
                event["release_id"] == release_id
                and event["candidate_manifest_sha256"]
                == candidate["manifest_sha256"]
                and event["candidate_manifest_bytes"]
                == candidate["manifest_bytes"]
                for event in history
            ):
                raise _error(
                    "rollback_candidate_binding_mismatch",
                    "rollback target bytes do not match a historical activation event",
                )
            receipt_record, receipt_raw = self._validate_pass_receipt_unlocked(
                receipt, candidate
            )
            receipt_descriptor = self._store_receipt_unlocked(
                receipt_record, receipt_raw
            )
            sequence = 1 if current is None else current["activation_sequence"] + 1
            if any(self.events_root.glob(f"{sequence:012d}-*.json")):
                raise _error(
                    "activation_sequence_occupied",
                    "next activation sequence contains an unreferenced event; reconcile before retry",
                )
            used_event_ids = {event["event_id"] for event in history}
            used_revisions = {
                value
                for event in history
                for value in (event["new_revision"], event["previous_revision"])
                if value is not None
            }
            if current is not None:
                used_revisions.add(current["revision"])
            event_id: str | None = None
            for _ in range(32):
                proposed = "WBAE-" + secrets.token_hex(16)
                if proposed not in used_event_ids:
                    event_id = proposed
                    break
            if event_id is None:
                raise _error(
                    "activation_event_identity_exhausted",
                    "could not allocate a unique activation event identity",
                    503,
                )
            revision: str | None = None
            for _ in range(32):
                proposed = "WBREV-" + secrets.token_hex(16)
                if proposed not in used_revisions:
                    revision = proposed
                    break
            if revision is None:
                raise _error(
                    "active_revision_identity_exhausted",
                    "could not allocate a unique active revision",
                    503,
                )
            event = _with_self_hash(
                {
                    "schema_version": ACTIVATION_EVENT_SCHEMA_VERSION,
                    "event_id": event_id,
                    "sequence": sequence,
                    "action": action,
                    "release_id": release_id,
                    "candidate_manifest_sha256": candidate["manifest_sha256"],
                    "candidate_manifest_bytes": candidate["manifest_bytes"],
                    "regression_receipt_id": receipt_descriptor["receipt_id"],
                    "regression_receipt_sha256": receipt_descriptor[
                        "receipt_sha256"
                    ],
                    "regression_receipt_bytes": receipt_descriptor[
                        "receipt_bytes"
                    ],
                    "previous_release_id": (
                        current["release_id"] if current is not None else None
                    ),
                    "previous_revision": (
                        current["revision"] if current is not None else None
                    ),
                    "new_revision": revision,
                    "previous_event_id": (
                        current["activation_event_id"] if current is not None else None
                    ),
                    "previous_event_sha256": (
                        current["activation_event_sha256"]
                        if current is not None
                        else None
                    ),
                    "principal_id": principal_id,
                    "reason_zh": reason_zh.strip(),
                    "created_at": _utc_now(),
                    "authority": deepcopy(AUTHORITY),
                }
            )
            self._validate_schema(
                self._schemas["activation_event"], event, "activation event"
            )
            event_raw = _record_bytes(event)
            event_path = self.events_root / f"{sequence:012d}-{event_id}.json"
            event_identity: tuple[int, int] | None = None
            pointer_raw: bytes | None = None
            pointer_committed = False
            try:
                event_identity = _write_exclusive(event_path, event_raw)
                stored_event, stored_event_raw = self._read_event_unlocked(
                    sequence, event_id
                )
                if stored_event != event or stored_event_raw != event_raw:
                    raise _error("activation_event_drift", "activation event drifted before pointer commit")
                pointer = _with_self_hash(
                    {
                        "schema_version": ACTIVE_POINTER_SCHEMA_VERSION,
                        "revision": revision,
                        "release_id": release_id,
                        "candidate_manifest_sha256": candidate["manifest_sha256"],
                        "candidate_manifest_bytes": candidate["manifest_bytes"],
                        "regression_receipt_id": receipt_descriptor["receipt_id"],
                        "regression_receipt_sha256": receipt_descriptor[
                            "receipt_sha256"
                        ],
                        "regression_receipt_bytes": receipt_descriptor[
                            "receipt_bytes"
                        ],
                        "activation_event_id": event_id,
                        "activation_event_sha256": _sha256(event_raw),
                        "activation_sequence": sequence,
                        "action": action,
                        "previous_release_id": (
                            current["release_id"] if current is not None else None
                        ),
                        "updated_at": event["created_at"],
                        "authority": deepcopy(AUTHORITY),
                    }
                )
                self._validate_schema(
                    self._schemas["active_pointer"], pointer, "active release pointer"
                )
                pointer_raw = _record_bytes(pointer)
                self._atomic_replace_pointer(pointer_raw, previous_raw)
                pointer_committed = True
                committed = self._read_active_pointer_unlocked(
                    verify_dependencies=True
                )
                if committed != pointer:
                    raise _error("active_pointer_postcommit_mismatch", "active pointer changed after commit")
                return {
                    "active_pointer": deepcopy(pointer),
                    "activation_event": deepcopy(event),
                    "candidate": candidate,
                    "regression_receipt": deepcopy(receipt_record),
                    "authority": deepcopy(AUTHORITY),
                    "automatic_activation": False,
                    "teaching_use_allowed": False,
                    "publication_allowed": False,
                }
            except Exception:
                if pointer_committed:
                    try:
                        current_raw = _read_file_stable(self.active_pointer_path)
                        if current_raw != pointer_raw:
                            raise _error(
                                "active_pointer_rollback_unsafe",
                                "failed activation pointer ownership changed",
                                500,
                            )
                        if previous_raw is None:
                            self.active_pointer_path.unlink()
                            _sync_parent_directory(self.active_pointer_path)
                            if os.path.lexists(self.active_pointer_path):
                                raise OSError("new active pointer remained after rollback")
                        else:
                            self._atomic_replace_pointer(previous_raw, current_raw)
                    except Exception as pointer_rollback_exc:
                        raise _error(
                            "active_pointer_rollback_failed",
                            "activation failed after pointer commit and rollback was incomplete",
                            500,
                        ) from pointer_rollback_exc
                # Only the event created by this unreturned transaction may be
                # removed.  Historical committed events are never deletion targets.
                if event_identity is not None and os.path.lexists(event_path):
                    details = event_path.lstat()
                    if (
                        _is_reparse_or_symlink(details)
                        or (int(details.st_dev), int(details.st_ino))
                        != event_identity
                    ):
                        raise _error(
                            "activation_event_rollback_failed",
                            "failed activation event ownership changed",
                            500,
                        )
                    event_path.unlink()
                raise

    def activate_release(
        self,
        release_id: str,
        receipt: Mapping[str, Any],
        *,
        expected_revision: str | None,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        return self._select_release(
            release_id,
            receipt,
            action="activate",
            expected_revision=expected_revision,
            principal_id=principal_id,
            reason_zh=reason_zh,
        )

    def rollback_to_release(
        self,
        release_id: str,
        receipt: Mapping[str, Any],
        *,
        expected_revision: str,
        principal_id: str,
        reason_zh: str,
    ) -> dict[str, Any]:
        return self._select_release(
            release_id,
            receipt,
            action="rollback",
            expected_revision=expected_revision,
            principal_id=principal_id,
            reason_zh=reason_zh,
        )


__all__ = [
    "ACTIVE_POINTER_SCHEMA_VERSION",
    "ACTIVATION_EVENT_SCHEMA_VERSION",
    "AUTHORITY",
    "CLOSURE_ALGORITHM",
    "CONTRACT_FILE_SHA256",
    "RECIPE_ID",
    "RECIPE_SELF_SHA256",
    "REGRESSION_RECEIPT_SCHEMA_VERSION",
    "RELEASE_CANDIDATE_SCHEMA_VERSION",
    "WorkbenchReleaseControlError",
    "WorkbenchReleaseStore",
    "canonical_json_bytes",
    "canonical_json_sha256",
]
