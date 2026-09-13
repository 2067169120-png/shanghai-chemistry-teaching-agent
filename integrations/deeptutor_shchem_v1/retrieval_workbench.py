from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from integrations.shchem_retrieval_gateway_v1 import (
    RetrievalGateway,
    RetrievalGatewayError,
)

KB_RETRIEVAL_READ_CAPABILITY = "kb_retrieval_read"
RETRIEVAL_SCHEMA_VERSION = "deeptutor_shchem_retrieval_workbench_v1"

_ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "query",
        "purpose",
        "limit",
        "K",
        "A",
        "C",
        "R",
        "D",
        "strict_tags",
        "source_family",
        "temporal_role",
        "official_only",
        "authority_scope",
        "claim_year",
    }
)
_SAFE_BOUNDARY_KEYS = frozenset(
    {
        "content_exposed",
        "source_file_exposed",
        "human_reviewed",
        "snapshot_verified",
    }
)
_PATH_KEY_RE = re.compile(
    r"(?:^|_)(?:path|root|directory|filename|file_name|db|database|config)(?:_|$)",
    re.IGNORECASE,
)
_PRIVATE_KEY_RE = re.compile(
    r"(?:^|_)(?:private|student|profile|raw_input|secret|token)(?:_|$)",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:^|[\s'\"(])[a-z]:[\\/][^\s]*")
_UNC_PATH_RE = re.compile(r"(?:^|[\s'\"(])(?:\\\\|//)[^\\/\s]+[\\/][^\s]*")
_FILE_URI_RE = re.compile(r"(?i)(?:^|[\s'\"(])file:(?:/{1,3}|[a-z]:)[^\s]*")
_POSIX_PATH_RE = re.compile(
    r"(?i)(?:^|[\s'\"(])/(?:etc|home|users|var|tmp|private|mnt|root|opt|srv)(?:/[^\s]*)?"
)
_LOCAL_PATH_RE = re.compile(
    r"(?i)(?:^|[\s'\"(])(?:sh-chem-db|private_runtime|private_state|\.intake)[\\/][^\s]*"
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:private(?:_runtime|_state)?|student(?:s|_[a-z0-9_]*)?|profile_id)(?:$|[^a-z0-9])"
)
_PRIVATE_CJK = ("学生错题", "学生档案", "学生作答", "私人数据", "隐私数据")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DROP = object()


class _Gateway(Protocol):
    def query(self, payload: Any) -> dict[str, Any]: ...


class RetrievalWorkbenchError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        projected = _safe_projection(dict(details or {}))
        self.details = projected if isinstance(projected, dict) else {}


def _sensitive_key(key: str) -> bool:
    if key in _SAFE_BOUNDARY_KEYS or key == "source_url":
        return False
    return bool(_PATH_KEY_RE.search(key) or _PRIVATE_KEY_RE.search(key))


def _safe_string(value: str) -> str | object:
    if any(marker in value for marker in _PRIVATE_CJK) or _PRIVATE_TEXT_RE.search(value):
        return _DROP
    if any(
        pattern.search(value)
        for pattern in (
            _WINDOWS_PATH_RE,
            _UNC_PATH_RE,
            _FILE_URI_RE,
            _POSIX_PATH_RE,
            _LOCAL_PATH_RE,
        )
    ):
        return _DROP
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value)[:4096]


def _safe_projection(value: Any) -> Any:
    """Recursively remove host-path and private/student-bearing values."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _sensitive_key(key):
                continue
            if key == "read_only":
                result[key] = True
                continue
            if key in {"content_exposed", "source_file_exposed", "human_reviewed"}:
                result[key] = False
                continue
            clean_key = _safe_string(key)
            if clean_key is _DROP:
                continue
            clean = _safe_projection(item)
            if clean is not _DROP:
                result[str(clean_key)] = clean
        return result
    if isinstance(value, (list, tuple)):
        return [
            clean
            for item in value
            if (clean := _safe_projection(item)) is not _DROP
        ]
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _DROP


def _safe_snapshot(value: Any) -> dict[str, dict[str, Any]]:
    """Keep only the gateway's public byte/hash bindings, never a source path."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for source in ("index_db", "config", "retrieval_core"):
        binding = value.get(source)
        if not isinstance(binding, Mapping):
            continue
        digest = binding.get("sha256")
        byte_count = binding.get("bytes")
        if (
            isinstance(digest, str)
            and _SHA256_RE.fullmatch(digest)
            and isinstance(byte_count, int)
            and not isinstance(byte_count, bool)
            and byte_count > 0
        ):
            result[source] = {"sha256": digest, "bytes": byte_count}
    return result


class RetrievalWorkbenchGateway:
    """DeepTutor-facing, read-only projection of the pinned retrieval gateway."""

    def __init__(
        self,
        workspace_root: str | os.PathLike[str],
        *,
        gateway: _Gateway | None = None,
    ) -> None:
        workspace = Path(os.path.abspath(os.fspath(workspace_root)))
        # The live service always supplies the configured workspace parent. No
        # request, environment variable, or browser field can replace it.
        self._gateway: _Gateway = gateway or RetrievalGateway(workspace)

    @staticmethod
    def status(*, capability_granted: bool) -> dict[str, Any]:
        return {
            "schema_version": RETRIEVAL_SCHEMA_VERSION,
            "status": "ready" if capability_granted else "capability_required",
            "teacher_only": True,
            "read_capability_required": KB_RETRIEVAL_READ_CAPABILITY,
            "read_capability_granted": capability_granted,
            "request_fields": sorted(_ALLOWED_REQUEST_FIELDS),
            "read_only": True,
            "snapshot_verified": False,
            "content_exposed": False,
            "source_file_exposed": False,
            "human_reviewed": False,
            "collection_enabled": False,
            "ingest_endpoint_present": False,
            "rebuild_endpoint_present": False,
            "write_endpoint_present": False,
            "official_claim_upgrade_present": False,
            "availability_claim": "configured_only_query_revalidates_snapshot",
        }

    def search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise RetrievalWorkbenchError(
                "INVALID_PAYLOAD", "retrieval payload must be a JSON object"
            )
        if any(not isinstance(key, str) for key in payload):
            raise RetrievalWorkbenchError(
                "UNKNOWN_FIELD", "retrieval fields must use string names"
            )
        unknown = set(payload) - _ALLOWED_REQUEST_FIELDS
        if unknown:
            raise RetrievalWorkbenchError(
                "UNKNOWN_FIELD",
                "retrieval payload contains a non-allowlisted field",
                details={"unknown_field_count": len(unknown)},
            )
        try:
            response = self._gateway.query(dict(payload))
        except RetrievalGatewayError as exc:
            raise RetrievalWorkbenchError(
                exc.code,
                exc.message,
                details=exc.details,
            ) from exc
        except RetrievalWorkbenchError:
            raise
        except Exception as exc:
            raise RetrievalWorkbenchError(
                "RETRIEVAL_UNAVAILABLE",
                "read-only retrieval failed closed",
                status=503,
            ) from exc

        projected = _safe_projection(response)
        if not isinstance(projected, dict):
            raise RetrievalWorkbenchError(
                "UNSAFE_RETRIEVAL_RESPONSE",
                "retrieval response could not be safely projected",
                status=503,
            )
        projected.update(
            {
                "read_only": True,
                "content_exposed": False,
                "source_file_exposed": False,
                "human_reviewed": False,
            }
        )
        projected["snapshot"] = _safe_snapshot(response.get("snapshot"))
        projected["public_projection"] = {
            "absolute_host_paths_removed": True,
            "private_student_markers_removed": True,
            "path_fields_removed": True,
        }
        return projected


__all__ = [
    "KB_RETRIEVAL_READ_CAPABILITY",
    "RETRIEVAL_SCHEMA_VERSION",
    "RetrievalWorkbenchError",
    "RetrievalWorkbenchGateway",
]
