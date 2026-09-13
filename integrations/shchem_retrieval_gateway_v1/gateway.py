"""Fail-closed, read-only gateway for the Shanghai chemistry FTS index.

The gateway deliberately has no path-bearing request fields.  Its three live
inputs are code-derived, byte-pinned, opened without following reparse points,
and held for the complete request.  SQLite receives only a deserialized copy
of the database in ``:memory:``; the live database is never opened by SQLite.
"""

from __future__ import annotations

import ast
import ctypes
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Self

SCHEMA_VERSION: Final = "shchem_retrieval_gateway_v1"


@dataclass(frozen=True)
class _SourceSpec:
    name: str
    relative_path: tuple[str, ...]
    expected_bytes: int
    expected_sha256: str


# This is the trust root.  It is intentionally code, not a caller-supplied
# manifest, environment variable, request path, URI, or mutable sidecar.
_SOURCE_SPECS: Final = (
    _SourceSpec(
        name="index_db",
        relative_path=("sh-chem-db", "kb", "retrieval", "index.sqlite3"),
        expected_bytes=2_367_488,
        expected_sha256="ede1daef065967446156fcf98c461a988f48d6a1f4f985681288c6fdfeee82ca",
    ),
    _SourceSpec(
        name="config",
        relative_path=("sh-chem-db", "kb", "retrieval", "config.json"),
        expected_bytes=7_998,
        expected_sha256="8a2160d5f2518eda187dc1d49e678fec925b07eb1c4c737b8210fa4aa8cf272f",
    ),
    _SourceSpec(
        name="retrieval_core",
        relative_path=("sh-chem-db", "kb", "retrieval", "retrieval_core.py"),
        expected_bytes=93_593,
        expected_sha256="1860e4456663c19b35cf8dc5dd139c4e4dbaf62d872ae320ed0823fc1981ad97",
    ),
)

_ALLOWED_FIELDS: Final = frozenset(
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
_TAG_PATTERNS: Final = {
    "K": re.compile(r"K\d{2}\Z"),
    "A": re.compile(r"A\d{2}\Z"),
    "C": re.compile(r"C\d{2}\Z"),
    "R": re.compile(r"R\d{2}\Z"),
    "D": re.compile(r"D[1-5]\Z"),
}
_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9_]+\Z")
_URI_RE: Final = re.compile(r"(?i)(?:https?|file|ftp|smb|data|javascript):(?:/{0,2})")
_DRIVE_RE: Final = re.compile(r"(?i)(?:^|[\s'\"(])[A-Z]:[\\/]")
_UNC_RE: Final = re.compile(r"(?:^|[\s'\"(])(?:\\\\|//)[^\\/\s]+[\\/]")
_RELATIVE_PATH_RE: Final = re.compile(r"(?:^|[\s'\"(])(?:\.{1,2}[\\/]|~[\\/])")
_POSIX_PATH_RE: Final = re.compile(
    r"(?i)(?:^|[\s'\"(])/(?:etc|home|users|var|tmp|private|mnt|root|opt|srv|dev|proc)(?:/|\Z)"
)
_PRIVACY_RE: Final = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:private(?:_runtime)?|student(?:s|_[a-z0-9_]*)?|profile_id)(?:\Z|[^a-z0-9])"
)
_PRIVACY_CJK: Final = ("学生错题", "学生档案", "学生作答", "私人数据", "隐私数据")
_WINDOWS_PATH_IN_TEXT: Final = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\s]+")
_POSIX_PATH_IN_TEXT: Final = re.compile(
    r"(?i)(?:^|\s)/(?:etc|home|users|var|tmp|private|mnt|root|opt|srv)(?:/[^\s]*)?"
)
_LOCAL_RELATIVE_PATH_IN_TEXT: Final = re.compile(
    r"(?i)(?:^|\s)(?:sh-chem-db|private_runtime|\.intake)[\\/][^\s]+"
)

_EXPECTED_RECORD_COLUMNS: Final = (
    "row_id",
    "record_id",
    "source_family",
    "unit_type",
    "title",
    "content",
    "source_path",
    "record_path",
    "evidence_level",
    "temporal_role",
    "official_status",
    "answer_authority",
    "pilot_formal_status",
    "schema_status",
    "eligibility",
    "eligibility_reasons_json",
    "year",
    "region_or_school",
    "paper_type",
    "source_account",
    "source_url",
    "k_tags",
    "a_tags",
    "c_tags",
    "r_tags",
    "d_tags",
    "knowledge_labels_json",
    "evidence_refs_json",
    "quality_json",
    "copyright_use",
    "source_sha256",
    "content_sha256",
    "built_at",
)
_EXPECTED_FTS_COLUMNS: Final = ("record_id", "title", "content", "tag_text")
_EXPECTED_META_KEYS: Final = frozenset(
    {"schema_version", "built_at", "config_sha256", "workspace_root"}
)


class RetrievalGatewayError(ValueError):
    """A structured, path-redacted gateway failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "rejected",
            "error": {
                "code": self.code,
                "message": self.message,
                "details": dict(self.details),
            },
            "read_only": True,
            "content_exposed": False,
            "source_file_exposed": False,
            "human_reviewed": False,
            "snapshot_verified": False,
        }


@dataclass(frozen=True)
class _NodeState:
    path: Path
    is_directory: bool
    signature: tuple[int, ...]


@dataclass
class _SourceState:
    spec: _SourceSpec
    path: Path
    fd: int
    data: bytes
    signature: tuple[int, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _default_workspace_root() -> Path:
    # abspath is lexical here; resolve() is forbidden because it follows links.
    return Path(os.path.abspath(os.fspath(Path(__file__).parents[2])))


def _is_reparse_or_link(path: Path, value: os.stat_result | None = None) -> bool:
    current = value if value is not None else path.lstat()
    attributes = int(getattr(current, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(current.st_mode) or bool(attributes & reparse_flag)


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    # Windows reports different ctime meanings for path stat and handle fstat,
    # so ctime cannot participate in their cross-view identity comparison.
    # File identity, mtime, pinned bytes, held deny-write/delete handles, and
    # parent-directory state still jointly reject same-size and ABA changes.
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _read_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return b"".join(chunks)


def _open_windows_handle(path: Path, *, directory: bool) -> int:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE

    file_read_attributes = 0x0080
    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_flag_sequential_scan = 0x08000000

    desired_access = file_read_attributes if directory else generic_read
    # Directory guards allow child activity but deny rename/delete of the node.
    # Source handles deny both write and delete for the request lifetime.
    share_mode = (file_share_read | file_share_write) if directory else file_share_read
    flags = file_attribute_normal | file_flag_open_reparse_point
    if directory:
        flags |= file_flag_backup_semantics
    else:
        flags |= file_flag_sequential_scan
    handle = create_file(
        str(path),
        desired_access,
        share_mode,
        None,
        open_existing,
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "secure source open failed")
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _open_source_fd(path: Path) -> int:
    if os.name == "nt":
        import msvcrt

        handle = _open_windows_handle(path, directory=False)
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            _close_windows_handle(handle)
            raise
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    return os.open(path, flags)


def _open_directory_guard(path: Path) -> tuple[str, int]:
    if os.name == "nt":
        return ("windows", _open_windows_handle(path, directory=True))
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_DIRECTORY", 0))
    return ("fd", os.open(path, flags))


class _SnapshotGraph:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(os.path.abspath(os.fspath(workspace_root)))
        self._nodes: list[_NodeState] = []
        self._directory_guards: list[tuple[str, int]] = []
        self._sources: dict[str, _SourceState] = {}

    def __enter__(self) -> Self:
        try:
            self._capture()
            return self
        except RetrievalGatewayError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RetrievalGatewayError(
                "SNAPSHOT_OPEN_FAILED",
                "The pinned retrieval snapshot could not be opened safely.",
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for source in self._sources.values():
            try:
                os.close(source.fd)
            except OSError:
                pass
        self._sources.clear()
        for kind, handle in reversed(self._directory_guards):
            try:
                if kind == "windows":
                    _close_windows_handle(handle)
                else:
                    os.close(handle)
            except OSError:
                pass
        self._directory_guards.clear()

    def source_bytes(self, name: str) -> bytes:
        return self._sources[name].data

    def source_path(self, name: str) -> Path:
        return self._sources[name].path

    def public_manifest(self) -> dict[str, dict[str, Any]]:
        return {
            spec.name: {
                "sha256": self._sources[spec.name].sha256,
                "bytes": len(self._sources[spec.name].data),
            }
            for spec in _SOURCE_SPECS
        }

    def _capture(self) -> None:
        root_stat = self._safe_lstat(self.workspace_root, "root")
        if not stat.S_ISDIR(root_stat.st_mode):
            self._reject_reparse("root")
        if _is_reparse_or_link(self.workspace_root, root_stat):
            self._reject_reparse("root")

        paths: dict[Path, bool] = {self.workspace_root: True}
        source_paths: dict[str, Path] = {}
        for spec in _SOURCE_SPECS:
            current = self.workspace_root
            for part in spec.relative_path[:-1]:
                current = current / part
                paths[current] = True
            leaf = current / spec.relative_path[-1]
            paths[leaf] = False
            source_paths[spec.name] = leaf

        self._nodes = []
        for path, is_directory in sorted(paths.items(), key=lambda item: len(item[0].parts)):
            value = self._safe_lstat(path, "parent" if is_directory else "leaf")
            if _is_reparse_or_link(path, value):
                self._reject_reparse("root" if path == self.workspace_root else ("parent" if is_directory else "leaf"))
            if is_directory and not stat.S_ISDIR(value.st_mode):
                self._reject_reparse("parent")
            if not is_directory and not stat.S_ISREG(value.st_mode):
                self._reject_reparse("leaf")
            self._nodes.append(_NodeState(path, is_directory, _stat_signature(value)))

        for node in self._nodes:
            if node.is_directory:
                self._directory_guards.append(_open_directory_guard(node.path))

        for spec in _SOURCE_SPECS:
            path = source_paths[spec.name]
            before = path.lstat()
            fd = _open_source_fd(path)
            try:
                opened = os.fstat(fd)
                data = _read_fd(fd)
                after = path.lstat()
                if (
                    _stat_signature(before) != _stat_signature(opened)
                    or _stat_signature(opened) != _stat_signature(after)
                ):
                    raise RetrievalGatewayError(
                        "SNAPSHOT_DRIFT",
                        "A retrieval source changed while its snapshot was captured.",
                        details={"source": spec.name},
                    )
                digest = hashlib.sha256(data).hexdigest()
                if len(data) != spec.expected_bytes or digest != spec.expected_sha256:
                    raise RetrievalGatewayError(
                        "SNAPSHOT_PIN_MISMATCH",
                        "A retrieval source does not match the code-derived trust root.",
                        details={"source": spec.name},
                    )
                self._sources[spec.name] = _SourceState(
                    spec=spec,
                    path=path,
                    fd=fd,
                    data=data,
                    signature=_stat_signature(opened),
                )
            except Exception:
                os.close(fd)
                raise
        self.revalidate()

    def revalidate(self) -> None:
        for node in self._nodes:
            value = self._safe_lstat(node.path, "parent" if node.is_directory else "leaf")
            if _is_reparse_or_link(node.path, value) or _stat_signature(value) != node.signature:
                raise RetrievalGatewayError(
                    "SNAPSHOT_DRIFT",
                    "The retrieval source graph changed during the request.",
                )
        for source in self._sources.values():
            current_stat = os.fstat(source.fd)
            path_stat = self._safe_lstat(source.path, "leaf")
            current_data = _read_fd(source.fd)
            if (
                _is_reparse_or_link(source.path, path_stat)
                or _stat_signature(current_stat) != source.signature
                or _stat_signature(path_stat) != source.signature
                or len(current_data) != source.spec.expected_bytes
                or hashlib.sha256(current_data).hexdigest() != source.spec.expected_sha256
                or current_data != source.data
            ):
                raise RetrievalGatewayError(
                    "SNAPSHOT_DRIFT",
                    "A retrieval source changed during the request.",
                    details={"source": source.spec.name},
                )

    @staticmethod
    def _safe_lstat(path: Path, role: str) -> os.stat_result:
        try:
            return path.lstat()
        except OSError as exc:
            raise RetrievalGatewayError(
                "SNAPSHOT_GRAPH_INVALID",
                "The code-derived retrieval source graph is unavailable.",
                details={"role": role},
            ) from exc

    @staticmethod
    def _reject_reparse(role: str) -> None:
        raise RetrievalGatewayError(
            "REPARSE_POINT_REJECTED",
            "Reparse points and symbolic links are forbidden in the retrieval source graph.",
            details={"role": role},
        )


def _reject_unsafe_text(value: str, *, field: str) -> None:
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise RetrievalGatewayError(
            "UNSAFE_INPUT",
            "Control characters are forbidden in retrieval input.",
            details={"field": field},
        )
    if (
        _URI_RE.search(value)
        or _DRIVE_RE.search(value)
        or _UNC_RE.search(value)
        or _RELATIVE_PATH_RE.search(value)
        or _POSIX_PATH_RE.search(value)
    ):
        raise RetrievalGatewayError(
            "PATH_OR_URI_REJECTED",
            "Paths, drives, UNC names, and URIs are forbidden in retrieval input.",
            details={"field": field},
        )
    if _PRIVACY_RE.search(value) or any(fragment in value for fragment in _PRIVACY_CJK):
        raise RetrievalGatewayError(
            "PRIVATE_OR_STUDENT_SCOPE_REJECTED",
            "Private and student-data scopes are outside this public knowledge gateway.",
            details={"field": field},
        )


def _validate_payload_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise RetrievalGatewayError("INVALID_PAYLOAD", "The retrieval payload must be an object.")
    if any(not isinstance(key, str) for key in payload):
        raise RetrievalGatewayError("UNKNOWN_FIELD", "Only named allowlisted fields are accepted.")
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise RetrievalGatewayError(
            "UNKNOWN_FIELD",
            "The retrieval payload contains a non-allowlisted field.",
            details={"unknown_field_count": len(unknown)},
        )

    query = payload.get("query", "")
    if not isinstance(query, str):
        raise RetrievalGatewayError("INVALID_TYPE", "query must be a string.", details={"field": "query"})
    if len(query) > 256:
        raise RetrievalGatewayError("INPUT_TOO_LONG", "query exceeds 256 characters.", details={"field": "query"})
    _reject_unsafe_text(query, field="query")

    purpose = payload.get("purpose", "general_research")
    if not isinstance(purpose, str):
        raise RetrievalGatewayError("INVALID_TYPE", "purpose must be a string.", details={"field": "purpose"})
    if not purpose or len(purpose) > 48 or not _IDENTIFIER_RE.fullmatch(purpose):
        raise RetrievalGatewayError("INVALID_PURPOSE", "purpose is not a legal purpose identifier.")
    _reject_unsafe_text(purpose, field="purpose")

    limit = payload.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise RetrievalGatewayError("INVALID_TYPE", "limit must be an integer.", details={"field": "limit"})
    if not 1 <= limit <= 50:
        raise RetrievalGatewayError("INVALID_LIMIT", "limit must be between 1 and 50.")

    tags: dict[str, list[str]] = {}
    for group, pattern in _TAG_PATTERNS.items():
        raw_values = payload.get(group, [])
        if not isinstance(raw_values, list) or any(not isinstance(value, str) for value in raw_values):
            raise RetrievalGatewayError(
                "INVALID_TYPE",
                f"{group} must be an array of tag strings.",
                details={"field": group},
            )
        if len(raw_values) > 16:
            raise RetrievalGatewayError("INPUT_TOO_LONG", f"{group} has too many tags.", details={"field": group})
        if len(set(raw_values)) != len(raw_values) or any(not pattern.fullmatch(value) for value in raw_values):
            raise RetrievalGatewayError("INVALID_TAG", f"{group} contains an invalid or duplicate tag.")
        tags[group] = sorted(raw_values)

    strict_tags = payload.get("strict_tags", False)
    official_only = payload.get("official_only", False)
    for field, value in (("strict_tags", strict_tags), ("official_only", official_only)):
        if not isinstance(value, bool):
            raise RetrievalGatewayError("INVALID_TYPE", f"{field} must be a boolean.", details={"field": field})
    if strict_tags and not any(tags.values()):
        raise RetrievalGatewayError("INVALID_FILTER", "strict_tags requires at least one K/A/C/R/D tag.")

    filters: dict[str, str | None] = {}
    for field, maximum in (("source_family", 80), ("temporal_role", 64), ("authority_scope", 96)):
        value = payload.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise RetrievalGatewayError("INVALID_TYPE", f"{field} must be a string.", details={"field": field})
            if not value or len(value) > maximum or not _IDENTIFIER_RE.fullmatch(value):
                raise RetrievalGatewayError("INVALID_FILTER", f"{field} is not a legal identifier.", details={"field": field})
            _reject_unsafe_text(value, field=field)
        filters[field] = value

    claim_year = payload.get("claim_year")
    if claim_year is not None:
        if isinstance(claim_year, bool) or not isinstance(claim_year, int):
            raise RetrievalGatewayError("INVALID_TYPE", "claim_year must be an integer.", details={"field": "claim_year"})
        if not 1900 <= claim_year <= 2100:
            raise RetrievalGatewayError("INVALID_FILTER", "claim_year is outside the accepted range.")

    if not query.strip() and not any(tags.values()):
        raise RetrievalGatewayError("EMPTY_QUERY", "Provide a query or at least one K/A/C/R/D tag.")
    if purpose == "official_claim":
        if filters["authority_scope"] is None or claim_year is None:
            raise RetrievalGatewayError(
                "OFFICIAL_CLAIM_SCOPE_REQUIRED",
                "official_claim requires both authority_scope and claim_year.",
            )
        query_years = {int(value) for value in re.findall(r"(?:19|20)\d{2}", query)}
        if (
            filters["source_family"] in {"official_exam_schedule", "official_exam_requirements"}
            and query_years
            and query_years != {claim_year}
        ):
            raise RetrievalGatewayError(
                "OFFICIAL_CLAIM_YEAR_CONFLICT",
                "The query year conflicts with claim_year for year-bound official evidence.",
            )
    elif filters["authority_scope"] is not None or claim_year is not None:
        raise RetrievalGatewayError(
            "UNUSED_AUTHORITY_FILTER",
            "authority_scope and claim_year are accepted only for official_claim.",
        )

    return {
        "query": query.strip(),
        "purpose": purpose,
        "limit": limit,
        "tags": tags,
        "strict_tags": strict_tags,
        "source_family": filters["source_family"],
        "temporal_role": filters["temporal_role"],
        "official_only": official_only,
        "authority_scope": filters["authority_scope"],
        "claim_year": claim_year,
    }


def _load_config(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalGatewayError("CONFIG_INVALID", "The pinned retrieval config is invalid.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("purpose_rules"), dict):
        raise RetrievalGatewayError("CONFIG_INVALID", "The pinned retrieval config is incomplete.")
    return value


def _load_core(raw: bytes, source_path: Path) -> types.ModuleType:
    try:
        source = raw.decode("utf-8-sig")
        tree = ast.parse(source, filename="<pinned_retrieval_core>")
        # The pinned hash is primary.  This second check documents and enforces
        # that loading the module cannot execute a top-level command block.
        allowed_nodes = (
            ast.Import,
            ast.ImportFrom,
            ast.Assign,
            ast.AnnAssign,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
        )
        if any(not isinstance(node, allowed_nodes) for node in tree.body):
            raise ValueError("unexpected top-level statement")
        module_name = f"_shchem_retrieval_core_snapshot_{id(tree):x}"
        module = types.ModuleType(module_name)
        module.__dict__.update(
            {
                "__file__": str(source_path),
                "__name__": module_name,
                "__package__": "",
            }
        )
        sys.modules[module_name] = module
        try:
            exec(compile(tree, "<pinned_retrieval_core>", "exec"), module.__dict__)  # noqa: S102
        finally:
            if sys.modules.get(module_name) is module:
                sys.modules.pop(module_name, None)
    except Exception as exc:
        raise RetrievalGatewayError("CORE_INVALID", "The pinned retrieval core could not be loaded.") from exc
    for name in ("query_records", "purpose_preflight"):
        if not callable(getattr(module, name, None)):
            raise RetrievalGatewayError("CORE_INVALID", "The pinned retrieval core interface is incomplete.")
    return module


def _rows_are_ok(rows: list[tuple[Any, ...]]) -> bool:
    return bool(rows) and all(len(row) == 1 and row[0] == "ok" for row in rows)


def _validate_database(connection: sqlite3.Connection, config: dict[str, Any], config_raw: bytes) -> None:
    quick_rows = connection.execute("PRAGMA quick_check").fetchall()
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    if not _rows_are_ok(quick_rows) or not _rows_are_ok(integrity_rows):
        raise RetrievalGatewayError("SQLITE_INTEGRITY_FAILED", "The in-memory index failed SQLite integrity checks.")

    records_columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(records)"))
    fts_columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(search_fts)"))
    if records_columns != _EXPECTED_RECORD_COLUMNS or fts_columns != _EXPECTED_FTS_COLUMNS:
        raise RetrievalGatewayError("SQLITE_SCHEMA_INVALID", "The in-memory index schema is not the pinned FTS schema.")
    unsafe_schema = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')"
    ).fetchone()[0]
    fts_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone()
    if unsafe_schema or not fts_sql_row or "using fts5" not in str(fts_sql_row[0]).lower():
        raise RetrievalGatewayError("SQLITE_SCHEMA_INVALID", "The in-memory FTS schema is unsafe or incomplete.")

    meta_rows = connection.execute("SELECT key,value FROM meta").fetchall()
    meta = {str(key): str(value) for key, value in meta_rows}
    if len(meta) != len(meta_rows) or set(meta) != _EXPECTED_META_KEYS:
        raise RetrievalGatewayError("SQLITE_META_INVALID", "The index metadata set is invalid.")
    config_sha256 = hashlib.sha256(config_raw).hexdigest()
    if (
        meta.get("config_sha256") != config_sha256
        or meta.get("schema_version") != config.get("schema_version")
        or not meta.get("built_at")
        or not meta.get("workspace_root")
    ):
        raise RetrievalGatewayError("SQLITE_META_INVALID", "The index is not bound to the pinned config.")

    record_count = int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])
    fts_count = int(connection.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0])
    duplicate_fts = int(
        connection.execute(
            "SELECT COUNT(*)-COUNT(DISTINCT record_id) FROM search_fts"
        ).fetchone()[0]
    )
    orphan_fts = int(
        connection.execute(
            "SELECT COUNT(*) FROM search_fts f LEFT JOIN records r ON r.record_id=f.record_id "
            "WHERE r.record_id IS NULL"
        ).fetchone()[0]
    )
    searchable = tuple(config.get("searchable_eligibility", ()))
    if not searchable or any(not isinstance(value, str) for value in searchable):
        raise RetrievalGatewayError("CONFIG_INVALID", "searchable_eligibility is invalid.")
    placeholders = ",".join("?" for _ in searchable)
    ineligible_fts = int(
        connection.execute(
            "SELECT COUNT(*) FROM search_fts f JOIN records r ON r.record_id=f.record_id "
            f"WHERE r.eligibility NOT IN ({placeholders})",
            searchable,
        ).fetchone()[0]
    )
    missing_fts = int(
        connection.execute(
            "SELECT COUNT(*) FROM records r LEFT JOIN search_fts f ON f.record_id=r.record_id "
            f"WHERE r.eligibility IN ({placeholders}) AND f.record_id IS NULL",
            searchable,
        ).fetchone()[0]
    )
    if record_count != 337 or fts_count != 274 or any(
        (duplicate_fts, orphan_fts, ineligible_fts, missing_fts)
    ):
        raise RetrievalGatewayError("FTS_META_INVALID", "The FTS/searchability metadata is inconsistent.")

    try:
        connection.execute("INSERT INTO search_fts(search_fts) VALUES('integrity-check')").fetchall()
        connection.rollback()
    except sqlite3.DatabaseError as exc:
        raise RetrievalGatewayError("FTS_INTEGRITY_FAILED", "The in-memory FTS index failed integrity-check.") from exc


def _make_memory_connection(db_raw: bytes, config: dict[str, Any], config_raw: bytes) -> sqlite3.Connection:
    if not hasattr(sqlite3.Connection, "deserialize"):
        raise RetrievalGatewayError("SQLITE_DESERIALIZE_UNAVAILABLE", "SQLite deserialize support is required.")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(db_raw)
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        _validate_database(connection, config, config_raw)
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise RetrievalGatewayError("READ_ONLY_ENFORCEMENT_FAILED", "SQLite query_only could not be enabled.")

        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", -1),
        }

        def authorizer(
            action: int,
            arg1: str | None,
            arg2: str | None,
            _db: str | None,
            _trigger: str | None,
        ) -> int:
            if action in allowed_actions:
                return sqlite3.SQLITE_OK
            # FTS5's MATCH implementation reads data_version internally.  No
            # other PRAGMA is admitted once the authorizer is installed.
            if action == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and arg2 is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        connection.set_authorizer(authorizer)
        operation_ticks = 0

        def progress() -> int:
            nonlocal operation_ticks
            operation_ticks += 1
            return int(operation_ticks > 50_000)

        connection.set_progress_handler(progress, 1_000)
        return connection
    except Exception:
        connection.close()
        raise


def _semantic_payload_checks(
    normalized: dict[str, Any],
    connection: sqlite3.Connection,
    config: dict[str, Any],
) -> None:
    purpose_rules = config.get("purpose_rules", {})
    if normalized["purpose"] not in purpose_rules:
        raise RetrievalGatewayError("INVALID_PURPOSE", "purpose is not allowlisted by the pinned retrieval config.")
    source_family = normalized["source_family"]
    if source_family is not None:
        allowed = {row[0] for row in connection.execute("SELECT DISTINCT source_family FROM records")}
        if source_family not in allowed:
            raise RetrievalGatewayError("INVALID_FILTER", "source_family is not present in the pinned index.")
    temporal_role = normalized["temporal_role"]
    if temporal_role is not None:
        allowed = {row[0] for row in connection.execute("SELECT DISTINCT temporal_role FROM records")}
        if temporal_role not in allowed:
            raise RetrievalGatewayError("INVALID_FILTER", "temporal_role is not present in the pinned index.")


def _safe_string(value: Any, maximum: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = _WINDOWS_PATH_IN_TEXT.sub("[local path withheld]", text)
    text = _POSIX_PATH_IN_TEXT.sub(" [local path withheld]", text)
    text = _LOCAL_RELATIVE_PATH_IN_TEXT.sub(" [local path withheld]", text)
    return text[:maximum]


def _safe_source_url(value: Any) -> str:
    text = _safe_string(value, 2_048)
    if not text:
        return ""
    if not re.match(r"(?i)https?://", text):
        return ""
    return text


def _safe_preflight(raw: Any) -> dict[str, list[str]]:
    value = raw if isinstance(raw, Mapping) else {}
    return {
        "warnings": [_safe_string(item, 500) for item in value.get("warnings", []) if isinstance(item, str)],
        "denials": [_safe_string(item, 500) for item in value.get("denials", []) if isinstance(item, str)],
        "blocked_claims": [
            _safe_string(item, 160) for item in value.get("blocked_claims", []) if isinstance(item, str)
        ],
    }


def _teacher_safe_record(record: Mapping[str, Any]) -> dict[str, Any]:
    raw_score = record.get("score", 0.0)
    score = float(raw_score) if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool) else 0.0
    if not math.isfinite(score):
        score = 0.0
    tags = {
        group: [str(value) for value in record.get(f"{group}_tags", []) if isinstance(value, str)]
        for group in ("K", "A", "C", "R", "D")
    }
    return {
        "record_id": _safe_string(record.get("record_id"), 256),
        "title": _safe_string(record.get("title"), 500),
        "year": record.get("year") if isinstance(record.get("year"), int) else None,
        "region": _safe_string(record.get("region_or_school"), 160),
        "source_account": _safe_string(record.get("source_account"), 300),
        "source_url": _safe_source_url(record.get("source_url")),
        "source_family": _safe_string(record.get("source_family"), 100),
        "evidence_level": _safe_string(record.get("evidence_level"), 100),
        "official_status": _safe_string(record.get("official_status"), 100),
        "temporal_role": _safe_string(record.get("temporal_role"), 100),
        "eligibility": _safe_string(record.get("eligibility"), 40),
        "copyright_use": _safe_string(record.get("copyright_use"), 100),
        "answer_authority": _safe_string(record.get("answer_authority"), 100),
        "tags": tags,
        "preview": _safe_string(record.get("content_preview"), 360),
        "score": round(score, 6),
        "preflight": _safe_preflight(record.get("preflight")),
    }


def _merge_preflights(checks: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {"warnings": set(), "denials": set(), "blocked_claims": set()}
    for check in checks:
        safe = _safe_preflight(check)
        for key, values in merged.items():
            values.update(value for value in safe[key] if value)
    return {key: sorted(values) for key, values in merged.items()}


class RetrievalGateway:
    """Execute one-shot, byte-pinned FTS queries against an in-memory database."""

    def __init__(self, workspace_root: str | os.PathLike[str] | None = None) -> None:
        self._workspace_root = (
            _default_workspace_root()
            if workspace_root is None
            else Path(os.path.abspath(os.fspath(workspace_root)))
        )

    def query(self, payload: Any) -> dict[str, Any]:
        normalized = _validate_payload_shape(payload)
        try:
            with _SnapshotGraph(self._workspace_root) as snapshot:
                config_raw = snapshot.source_bytes("config")
                config = _load_config(config_raw)
                core = _load_core(
                    snapshot.source_bytes("retrieval_core"),
                    snapshot.source_path("retrieval_core"),
                )
                connection = _make_memory_connection(
                    snapshot.source_bytes("index_db"),
                    config,
                    config_raw,
                )
                try:
                    _semantic_payload_checks(normalized, connection, config)
                    query_kwargs = {
                        "text": normalized["query"],
                        "purpose": normalized["purpose"],
                        "limit": normalized["limit"],
                        "tags": normalized["tags"],
                        "strict_tags": normalized["strict_tags"],
                        "source_family": normalized["source_family"],
                        "temporal_role": normalized["temporal_role"],
                        "official_only": normalized["official_only"],
                        "include_excluded": False,
                        "authority_scope": normalized["authority_scope"],
                        "claim_year": normalized["claim_year"],
                    }
                    records = core.query_records(connection, config, **query_kwargs)

                    audit_checks: list[Mapping[str, Any]] = [
                        record.get("preflight", {}) for record in records if isinstance(record, Mapping)
                    ]
                    # query_records correctly removes denied rows.  A second,
                    # general-research candidate pass supplies only denial and
                    # warning reasons, never content or paths, for fail-closed
                    # teacher feedback such as question_reuse or wrong scope/year.
                    if not records:
                        audit_candidates = core.query_records(
                            connection,
                            config,
                            **{
                                **query_kwargs,
                                "purpose": "general_research",
                                "limit": min(50, max(20, normalized["limit"])),
                                "authority_scope": None,
                                "claim_year": None,
                            },
                        )
                        for candidate in audit_candidates:
                            audit_checks.append(
                                core.purpose_preflight(
                                    candidate,
                                    normalized["purpose"],
                                    config,
                                    authority_scope=normalized["authority_scope"],
                                    claim_year=normalized["claim_year"],
                                )
                            )
                    preflight = _merge_preflights(audit_checks)
                    teacher_records = [_teacher_safe_record(record) for record in records]
                finally:
                    connection.close()

                # No result crosses the boundary until every root/parent/leaf,
                # open handle, byte count, and digest has been checked again.
                snapshot.revalidate()
                snapshot_manifest = snapshot.public_manifest()
                return {
                    "schema_version": SCHEMA_VERSION,
                    "status": "denied" if not teacher_records and preflight["denials"] else "ok",
                    "purpose": normalized["purpose"],
                    "count": len(teacher_records),
                    "results": teacher_records,
                    "preflight": preflight,
                    "read_only": True,
                    "content_exposed": False,
                    "source_file_exposed": False,
                    "human_reviewed": False,
                    "snapshot": snapshot_manifest,
                    "snapshot_verified": True,
                }
        except RetrievalGatewayError:
            raise
        except Exception as exc:
            raise RetrievalGatewayError(
                "RETRIEVAL_FAILED",
                "The read-only retrieval request failed closed.",
            ) from exc


def query_retrieval(payload: Any) -> dict[str, Any]:
    """Convenience entry point using the code-derived workspace root."""

    return RetrievalGateway().query(payload)


__all__ = ["RetrievalGateway", "RetrievalGatewayError", "query_retrieval"]
