"""Cross-process-safe append-only ledger for candidate theme review work."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .contracts import (
    CHANGE_ARRAY_FIELDS,
    LEGACY_CHANGE_ARRAY_FIELDS,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    SUPPORTED_SCHEMA_VERSIONS,
    SYSTEM_CATALOG_PRINCIPAL,
    canonical_json_bytes,
    parse_json_object,
    request_object_and_bytes,
    validate_change_set,
    validate_claim,
    validate_create_task,
    validate_decision,
    validate_identifier,
    validate_idempotency_key,
    validate_principal,
    validate_release,
    validate_revision,
    validate_sha256,
)
from .errors import (
    AuthorizationError,
    ConflictError,
    ContractError,
    IdempotencyConflictError,
    IntegrityError,
    NotFoundError,
    RevisionConflictError,
)

_SELF_HASH_KEY = "self_sha256"
_EVENT_ID_RE = re.compile(r"^TREVENT-[0-9a-f]{64}$")
_CHANGE_SET_ID_RE = re.compile(r"^TRCHANGE-[0-9a-f]{64}$")
_DECISION_ID_RE = re.compile(r"^TRDECISION-[0-9a-f]{64}$")
_EVENT_DIR_RE = re.compile(r"^([0-9]{12})-(TREVENT-[0-9a-f]{64})$")
_PROTECTED_COMPONENTS = frozenset(
    {"sh-chem-db", "kb", "central", "central-kb", "central_kb"}
)

_EVENT_FIELDS = frozenset(
    {
        "record_schema_version",
        "record_type",
        "event_id",
        "event_type",
        "operation",
        "task_id",
        "sequence",
        "actor_id",
        "created_at_utc",
        "idempotency_key",
        "request_body_sha256",
        "request_body_size_bytes",
        "previous_revision",
        "resulting_revision",
        "payload",
        "candidate_only",
        "central_master_mutated",
        "human_reviewed",
        "official",
        "retrieval_ready",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        _SELF_HASH_KEY,
    }
)

_COMMIT_FIELDS = frozenset(
    {
        "record_schema_version",
        "record_type",
        "task_id",
        "sequence",
        "event_id",
        "event_type",
        "event_record_relative_path",
        "event_record_sha256",
        "request_body_relative_path",
        "request_body_sha256",
        "package_file_count_excluding_commit",
        "files",
        "candidate_only",
        "central_master_mutated",
        "human_reviewed",
        "official",
        "retrieval_ready",
        "teaching_use_allowed",
        "generation_allowed",
        "publication_allowed",
        _SELF_HASH_KEY,
    }
)

_OPERATION_EVENT_TYPES = {
    "create_task": "task",
    "claim_task": "claim",
    "release_task": "release",
    "submit_change_set": "change_set",
    "create_decision": "decision",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _closed_flags() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "central_master_mutated": False,
        "human_reviewed": False,
        "official": False,
        "retrieval_ready": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
    }


def _assert_closed_flags(value: Mapping[str, Any], name: str) -> None:
    expected = _closed_flags()
    for key, required in expected.items():
        if value.get(key) is not required:
            raise IntegrityError(f"{name} attempted to elevate {key}")


def _stat_is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _marker(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        max(info.st_ctime_ns, info.st_mtime_ns),
    )


def _is_protected_path(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold().rstrip(" .")
        if lowered in _PROTECTED_COMPONENTS or lowered.startswith(
            ("live", "private", ".private", "centralkb")
        ):
            return True
    return False


def _validate_relative(value: str) -> str:
    if type(value) is not str or not value or len(value) > 2048:
        raise ContractError("internal relative path is invalid")
    if "\\" in value or "\x00" in value or ":" in value:
        raise ContractError("internal relative path contains forbidden syntax")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise ContractError("internal relative path is not canonical")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise ContractError("internal relative path must be canonical POSIX")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ContractError("internal path traversal is forbidden")
    return value


def _assert_chain_no_reparse(path: Path, name: str) -> None:
    for component in reversed((path, *path.parents)):
        if not os.path.lexists(component):
            continue
        try:
            info = os.lstat(component)
        except OSError as exc:
            raise ContractError(f"{name} cannot be lstat-verified") from exc
        if _stat_is_reparse(info):
            raise ContractError(f"{name} contains a symlink/reparse point")


def _self_hashed(value: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    result = copy.deepcopy(dict(value))
    result[_SELF_HASH_KEY] = None
    result[_SELF_HASH_KEY] = _sha256(canonical_json_bytes(result))
    return result, canonical_json_bytes(result)


def _verify_self_hash(value: Any, expected_keys: frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected_keys:
        raise IntegrityError(f"{name} exact-key contract failed")
    supplied = value.get(_SELF_HASH_KEY)
    try:
        validate_sha256(supplied, f"{name}.{_SELF_HASH_KEY}")
    except ContractError as exc:
        raise IntegrityError(str(exc)) from exc
    unsigned = copy.deepcopy(value)
    unsigned[_SELF_HASH_KEY] = None
    if _sha256(canonical_json_bytes(unsigned)) != supplied:
        raise IntegrityError(f"{name} self hash mismatch")
    return copy.deepcopy(value)


class AppendOnlyThemeReviewStore:
    """Independent immutable event ledger; it never opens the central KB.

    ``state_root`` is a trusted server configuration value, not request data.
    Every mutating request is exact-body validated.  The caller supplies only a
    server-authenticated principal; actor identity and UTC time are never read
    from the request body.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw_text = os.fspath(state_root)
        if (
            type(raw_text) is not str
            or not raw_text
            or "\x00" in raw_text
            or "://" in raw_text
            or raw_text.startswith(("\\\\", "//"))
        ):
            raise ContractError("state_root must be a trusted local filesystem path")
        lexical = Path(os.path.abspath(raw_text))
        if _is_protected_path(lexical):
            raise ContractError("state_root may not be inside central/live/private namespaces")
        _assert_chain_no_reparse(lexical, "state_root")
        self._mkdir_chain(lexical)
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise ContractError("state_root could not be safely resolved") from exc
        _assert_chain_no_reparse(resolved, "resolved state_root")
        root_info = os.lstat(resolved)
        if _stat_is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise ContractError("state_root must be a real directory")
        if _is_protected_path(resolved):
            raise ContractError("resolved state_root is protected")

        self.state_root = resolved
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ensure_directory("tasks")
        self._lock_path = self.state_root / ".ledger.lock"
        self._initialize_lock_file()

    @classmethod
    def for_test_workspace(
        cls,
        temporary_workspace: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "AppendOnlyThemeReviewStore":
        root = Path(temporary_workspace).resolve(strict=True)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        try:
            root.relative_to(temp_root)
        except ValueError as exc:
            raise ContractError("test workspace must be an OS-temporary directory") from exc
        if root == temp_root:
            raise ContractError("test workspace must be a dedicated temporary child")
        return cls(root / ".theme-review-workbench-test-store-v1", clock=clock)

    @staticmethod
    def _mkdir_chain(path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while not os.path.lexists(cursor):
            missing.append(cursor)
            cursor = cursor.parent
        _assert_chain_no_reparse(cursor, "state_root ancestor")
        ancestor_info = os.lstat(cursor)
        if not stat.S_ISDIR(ancestor_info.st_mode):
            raise ContractError("state_root ancestor is not a directory")
        for directory in reversed(missing):
            try:
                os.mkdir(directory, mode=0o700)
            except FileExistsError:
                pass
            info = os.lstat(directory)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ContractError("state_root creation collided with an unsafe entry")

    def _initialize_lock_file(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
        except FileExistsError:
            info = os.lstat(self._lock_path)
            if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_size != 1:
                raise ContractError("ledger lock file is unsafe")
            return
        try:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        info = os.lstat(self._lock_path)
        if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_size != 1:
            raise ContractError("ledger lock initialization failed")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._assert_store_root_safe()
        before = os.lstat(self._lock_path)
        if _stat_is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size != 1:
            raise IntegrityError("ledger lock file was replaced")
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._lock_path, flags)
        except OSError as exc:
            raise IntegrityError("ledger lock could not be opened") from exc
        locked = False
        try:
            opened = os.fstat(descriptor)
            after_path = os.lstat(self._lock_path)
            if (
                _identity(before) != _identity(opened)
                or _identity(opened) != _identity(after_path)
                or _stat_is_reparse(after_path)
            ):
                raise IntegrityError("ledger lock identity changed during open")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            self._assert_store_root_safe()
            yield
        finally:
            if locked:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            else:
                os.close(descriptor)

    def _assert_store_root_safe(self) -> None:
        info = os.lstat(self.state_root)
        if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("state_root was replaced")
        _assert_chain_no_reparse(self.state_root, "state_root")

    def _path(self, relative: str, *, require_file: bool = False) -> Path:
        relative = _validate_relative(relative)
        path = self.state_root.joinpath(*PurePosixPath(relative).parts)
        cursor = self.state_root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if not os.path.lexists(cursor):
                break
            info = os.lstat(cursor)
            if _stat_is_reparse(info):
                raise IntegrityError("store path contains a symlink/reparse point")
        if require_file:
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise IntegrityError(f"immutable file is missing: {relative}") from exc
            if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode):
                raise IntegrityError(f"immutable path is not a regular file: {relative}")
        return path

    def _ensure_directory(self, relative: str) -> Path:
        relative = _validate_relative(relative)
        cursor = self.state_root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            try:
                os.mkdir(cursor, mode=0o700)
            except FileExistsError:
                pass
            info = os.lstat(cursor)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ContractError("store directory collided with an unsafe entry")
        return cursor

    def _mkdir_exclusive(self, relative: str) -> Path:
        path = self._path(relative)
        parent = os.lstat(path.parent)
        if _stat_is_reparse(parent) or not stat.S_ISDIR(parent.st_mode):
            raise IntegrityError("immutable package parent is unsafe")
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError as exc:
            raise ConflictError("append-only package already exists") from exc
        return path

    def _write_exclusive(self, relative: str, data: bytes) -> dict[str, Any]:
        if type(data) is not bytes:
            raise ContractError("immutable write requires bytes")
        path = self._path(relative)
        parent = os.lstat(path.parent)
        if _stat_is_reparse(parent) or not stat.S_ISDIR(parent.st_mode):
            raise IntegrityError("immutable file parent is unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ConflictError("append-only file already exists") from exc
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            raise
        raw, marker = self._read_stable(relative)
        if raw != data:
            raise IntegrityError("immutable write readback mismatch")
        return {
            "relative_path": relative,
            "size_bytes": len(data),
            "sha256": _sha256(data),
            "marker": marker,
            "data": data,
        }

    @staticmethod
    def _read_all(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _read_stable(self, relative: str) -> tuple[bytes, tuple[int, ...]]:
        path = self._path(relative, require_file=True)
        before = os.lstat(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise IntegrityError("immutable file could not be safely opened") from exc
        try:
            opened = os.fstat(descriptor)
            if _identity(before) != _identity(opened):
                raise IntegrityError("immutable file changed between lstat and open")
            raw = self._read_all(descriptor)
            after = os.fstat(descriptor)
            path_after = os.lstat(path)
        finally:
            os.close(descriptor)
        if (
            _marker(opened) != _marker(after)
            or _identity(after) != _identity(path_after)
            or _stat_is_reparse(path_after)
            or len(raw) != after.st_size
        ):
            raise IntegrityError("immutable file drifted while being read")
        return raw, _marker(after)

    def _assert_observation(self, observation: Mapping[str, Any]) -> None:
        raw, marker = self._read_stable(observation["relative_path"])
        if raw != observation["data"] or marker != observation["marker"]:
            raise IntegrityError("same-size/ABA drift detected before commit return")

    def _remove_uncommitted_tree(self, root: Path) -> None:
        try:
            root_info = os.lstat(root)
        except FileNotFoundError:
            return
        if _stat_is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise IntegrityError("cannot safely roll back a replaced package root")
        for entry in os.scandir(root):
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(info):
                try:
                    os.unlink(path)
                except IsADirectoryError:
                    os.rmdir(path)
            elif stat.S_ISDIR(info.st_mode):
                self._remove_uncommitted_tree(path)
            else:
                os.unlink(path)
        os.rmdir(root)

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise IntegrityError("server clock must return datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise IntegrityError("server clock must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _task_projection(request: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            key: copy.deepcopy(request[key])
            for key in (
                "task_id",
                "paper_id",
                "theme_id",
                "title_zh",
                "base_snapshot_sha256",
                "target_atomic_part_ids",
                "target_printed_question_ids",
                "evidence_binding_ids",
            )
        }
        if "source_binding_candidates" in request:
            result["source_binding_candidates"] = copy.deepcopy(
                request["source_binding_candidates"]
            )
        return result

    @staticmethod
    def _event_subject(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(record[key])
            for key in (
                "record_schema_version",
                "record_type",
                "event_type",
                "operation",
                "task_id",
                "sequence",
                "actor_id",
                "created_at_utc",
                "idempotency_key",
                "request_body_sha256",
                "request_body_size_bytes",
                "previous_revision",
                "payload",
                "candidate_only",
                "central_master_mutated",
                "human_reviewed",
                "official",
                "retrieval_ready",
                "teaching_use_allowed",
                "generation_allowed",
                "publication_allowed",
            )
        }

    @staticmethod
    def _revision(sequence: int, event_head_sha256: str) -> str:
        return f"TRREV-{sequence:012d}-{event_head_sha256}"

    def _append_event_locked(
        self,
        *,
        task_id: str,
        sequence: int,
        previous_revision: str | None,
        actor_id: str,
        operation: str,
        request_raw: bytes,
        request_value: Mapping[str, Any],
        payload: Mapping[str, Any],
        schema_version: str,
        task_root_precreated: bool = False,
    ) -> dict[str, Any]:
        event_type = _OPERATION_EVENT_TYPES[operation]
        created_at = self._now()
        base_record = {
            "record_schema_version": schema_version,
            "record_type": "theme_review_immutable_event",
            "event_type": event_type,
            "operation": operation,
            "task_id": task_id,
            "sequence": sequence,
            "actor_id": actor_id,
            "created_at_utc": created_at,
            "idempotency_key": request_value["idempotency_key"],
            "request_body_sha256": _sha256(request_raw),
            "request_body_size_bytes": len(request_raw),
            "previous_revision": previous_revision,
            "payload": copy.deepcopy(dict(payload)),
            **_closed_flags(),
        }
        head_sha = _sha256(canonical_json_bytes(self._event_subject(base_record)))
        event_id = f"TREVENT-{head_sha}"
        revision = self._revision(sequence, head_sha)
        record, event_raw = _self_hashed(
            {
                **base_record,
                "event_id": event_id,
                "resulting_revision": revision,
            }
        )
        event_dir_name = f"{sequence:012d}-{event_id}"
        event_relative = f"tasks/{task_id}/events/{event_dir_name}"
        event_root = self._mkdir_exclusive(event_relative)
        observations: list[dict[str, Any]] = []
        committed = False
        try:
            request_relative = f"{event_relative}/request.json"
            event_record_relative = f"{event_relative}/event.json"
            observations.append(self._write_exclusive(request_relative, request_raw))
            observations.append(self._write_exclusive(event_record_relative, event_raw))
            files = [
                {
                    "relative_path": row["relative_path"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                }
                for row in observations
            ]
            commit, commit_raw = _self_hashed(
                {
                    "record_schema_version": schema_version,
                    "record_type": "theme_review_event_commit_marker",
                    "task_id": task_id,
                    "sequence": sequence,
                    "event_id": event_id,
                    "event_type": event_type,
                    "event_record_relative_path": event_record_relative,
                    "event_record_sha256": _sha256(event_raw),
                    "request_body_relative_path": request_relative,
                    "request_body_sha256": _sha256(request_raw),
                    "package_file_count_excluding_commit": len(files),
                    "files": files,
                    **_closed_flags(),
                }
            )
            commit_relative = f"{event_relative}/commit.json"
            observations.append(self._write_exclusive(commit_relative, commit_raw))
            for observation in observations:
                self._assert_observation(observation)
            verified = self._verify_event_package(
                task_id, event_dir_name, expected_sequence=sequence
            )
            if verified["event"] != record or verified["commit"] != commit:
                raise IntegrityError("new event verification projection mismatch")
            committed = True
            return verified
        except Exception:
            if not committed:
                self._remove_uncommitted_tree(event_root)
                if task_root_precreated:
                    task_root = self._path(f"tasks/{task_id}")
                    try:
                        events_dir = task_root / "events"
                        if events_dir.exists() and not any(os.scandir(events_dir)):
                            os.rmdir(events_dir)
                        if task_root.exists() and not any(os.scandir(task_root)):
                            os.rmdir(task_root)
                    except OSError:
                        pass
            raise

    @staticmethod
    def _parse_canonical(raw: bytes, name: str) -> dict[str, Any]:
        try:
            value = parse_json_object(raw)
            if canonical_json_bytes(value) != raw:
                raise IntegrityError(f"{name} bytes are not canonical JSON")
            return value
        except ContractError as exc:
            raise IntegrityError(f"{name} is not strict canonical JSON: {exc}") from exc

    def _safe_inventory(self, event_relative: str) -> set[str]:
        root = self._path(event_relative)
        info = os.lstat(root)
        if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("event package root is unsafe")
        files: set[str] = set()
        for entry in os.scandir(root):
            child_info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(child_info) or not stat.S_ISREG(child_info.st_mode):
                raise IntegrityError("event package contains a non-regular entry")
            files.add(Path(entry.path).relative_to(self.state_root).as_posix())
        return files

    def _verify_event_package(
        self, task_id: str, event_dir_name: str, *, expected_sequence: int
    ) -> dict[str, Any]:
        match = _EVENT_DIR_RE.fullmatch(event_dir_name)
        if match is None or int(match.group(1)) != expected_sequence:
            raise IntegrityError("event directory name/sequence is invalid")
        expected_event_id = match.group(2)
        event_relative = f"tasks/{task_id}/events/{event_dir_name}"
        commit_relative = f"{event_relative}/commit.json"
        commit_raw, _ = self._read_stable(commit_relative)
        commit = _verify_self_hash(
            self._parse_canonical(commit_raw, "commit marker"),
            _COMMIT_FIELDS,
            "commit marker",
        )
        _assert_closed_flags(commit, "commit marker")
        if (
            commit["record_schema_version"] not in SUPPORTED_SCHEMA_VERSIONS
            or commit["record_type"] != "theme_review_event_commit_marker"
            or commit["task_id"] != task_id
            or commit["sequence"] != expected_sequence
            or commit["event_id"] != expected_event_id
        ):
            raise IntegrityError("commit marker identity mismatch")
        files = commit["files"]
        if type(files) is not list or len(files) != 2:
            raise IntegrityError("commit marker must bind exactly request and event files")
        if commit["package_file_count_excluding_commit"] != len(files):
            raise IntegrityError("commit marker file count mismatch")
        file_map: dict[str, bytes] = {}
        for row in files:
            if type(row) is not dict or set(row) != {"relative_path", "size_bytes", "sha256"}:
                raise IntegrityError("commit marker file descriptor is invalid")
            relative = _validate_relative(row["relative_path"])
            if not relative.startswith(f"{event_relative}/") or relative == commit_relative:
                raise IntegrityError("commit marker file escapes/includes commit")
            if relative in file_map:
                raise IntegrityError("commit marker contains duplicate files")
            try:
                validate_sha256(row["sha256"], "commit file sha256")
            except ContractError as exc:
                raise IntegrityError(str(exc)) from exc
            if type(row["size_bytes"]) is not int or type(row["size_bytes"]) is bool:
                raise IntegrityError("commit file size is invalid")
            raw, _ = self._read_stable(relative)
            if len(raw) != row["size_bytes"] or _sha256(raw) != row["sha256"]:
                raise IntegrityError("commit-bound file bytes were tampered")
            file_map[relative] = raw
        if self._safe_inventory(event_relative) != set(file_map) | {commit_relative}:
            raise IntegrityError("event package has missing or uncommitted extra files")

        event_record_relative = f"{event_relative}/event.json"
        request_relative = f"{event_relative}/request.json"
        if (
            commit["event_record_relative_path"] != event_record_relative
            or commit["request_body_relative_path"] != request_relative
        ):
            raise IntegrityError("commit marker fixed file paths mismatch")
        event_raw = file_map[event_record_relative]
        request_raw = file_map[request_relative]
        if (
            _sha256(event_raw) != commit["event_record_sha256"]
            or _sha256(request_raw) != commit["request_body_sha256"]
        ):
            raise IntegrityError("commit marker top-level hash mismatch")
        event = _verify_self_hash(
            self._parse_canonical(event_raw, "event record"),
            _EVENT_FIELDS,
            "event record",
        )
        _assert_closed_flags(event, "event record")
        request_value = self._parse_canonical(request_raw, "stored request body")
        if (
            event["record_schema_version"] not in SUPPORTED_SCHEMA_VERSIONS
            or event["record_schema_version"] != commit["record_schema_version"]
            or event["record_type"] != "theme_review_immutable_event"
            or event["task_id"] != task_id
            or event["sequence"] != expected_sequence
            or event["event_id"] != expected_event_id
            or event["event_type"] != commit["event_type"]
            or _OPERATION_EVENT_TYPES.get(event["operation"]) != event["event_type"]
            or event["idempotency_key"] != request_value.get("idempotency_key")
            or event["request_body_sha256"] != _sha256(request_raw)
            or event["request_body_size_bytes"] != len(request_raw)
        ):
            raise IntegrityError("event/request/package identity mismatch")
        try:
            validate_principal(event["actor_id"])
        except ContractError as exc:
            raise IntegrityError(str(exc)) from exc
        try:
            parsed_time = datetime.fromisoformat(event["created_at_utc"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise IntegrityError("event server timestamp is invalid") from exc
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise IntegrityError("event server timestamp is not timezone-aware")
        subject_sha = _sha256(canonical_json_bytes(self._event_subject(event)))
        if (
            event["event_id"] != f"TREVENT-{subject_sha}"
            or event["resulting_revision"] != self._revision(expected_sequence, subject_sha)
        ):
            raise IntegrityError("event head/revision content binding mismatch")
        return {
            "event": event,
            "commit": commit,
            "request": request_value,
            "request_raw": request_raw,
            "event_relative_path": event_record_relative,
            "event_sha256": _sha256(event_raw),
            "commit_relative_path": commit_relative,
            "commit_sha256": _sha256(commit_raw),
        }

    def _task_event_directories(self, task_id: str) -> list[str]:
        task_relative = f"tasks/{task_id}"
        task_root = self._path(task_relative)
        try:
            task_info = os.lstat(task_root)
        except FileNotFoundError as exc:
            raise NotFoundError("theme review task does not exist") from exc
        if _stat_is_reparse(task_info) or not stat.S_ISDIR(task_info.st_mode):
            raise IntegrityError("task root is unsafe")
        entries = list(os.scandir(task_root))
        if len(entries) != 1 or entries[0].name != "events":
            raise IntegrityError("task root contains an unexpected entry")
        events_info = entries[0].stat(follow_symlinks=False)
        if _stat_is_reparse(events_info) or not stat.S_ISDIR(events_info.st_mode):
            raise IntegrityError("task events root is unsafe")
        names: list[str] = []
        for entry in os.scandir(entries[0].path):
            info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError("task events root contains an unsafe entry")
            if _EVENT_DIR_RE.fullmatch(entry.name) is None:
                raise IntegrityError("task events root contains an invalid event directory")
            names.append(entry.name)
        if not names:
            raise IntegrityError("task has no committed creation event")
        names.sort()
        return names

    @staticmethod
    def _expected_change_set_id(
        task_id: str,
        actor_id: str,
        request_value: Mapping[str, Any],
        *,
        schema_version: str = SCHEMA_VERSION_V2,
    ) -> str:
        identity = {
            "schema_version": schema_version,
            "task_id": task_id,
            "actor_id": actor_id,
            "request": copy.deepcopy(dict(request_value)),
        }
        return f"TRCHANGE-{_sha256(canonical_json_bytes(identity))}"

    @staticmethod
    def _expected_decision_id(
        task_id: str,
        actor_id: str,
        request_value: Mapping[str, Any],
        *,
        schema_version: str = SCHEMA_VERSION_V2,
    ) -> str:
        identity = {
            "schema_version": schema_version,
            "task_id": task_id,
            "actor_id": actor_id,
            "request": copy.deepcopy(dict(request_value)),
        }
        return f"TRDECISION-{_sha256(canonical_json_bytes(identity))}"

    def _load_task_locked(self, task_id: str) -> dict[str, Any]:
        validate_identifier(task_id, "task_id")
        event_dirs = self._task_event_directories(task_id)
        events: list[dict[str, Any]] = []
        for sequence, name in enumerate(event_dirs, start=1):
            match = _EVENT_DIR_RE.fullmatch(name)
            if match is None or int(match.group(1)) != sequence:
                raise IntegrityError("task event sequence is not contiguous")
            events.append(
                self._verify_event_package(task_id, name, expected_sequence=sequence)
            )

        task: dict[str, Any] | None = None
        revision: str | None = None
        claimed_by: str | None = None
        change_sets: dict[str, dict[str, Any]] = {}
        decisions: dict[str, dict[str, Any]] = {}
        decision_by_change_set: dict[str, str] = {}
        idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}
        for sequence, package in enumerate(events, start=1):
            event = package["event"]
            request = package["request"]
            operation = event["operation"]
            actor = event["actor_id"]
            if event["previous_revision"] != revision:
                raise IntegrityError("task revision chain is broken")
            identity = (actor, operation, event["idempotency_key"])
            if identity in idempotency:
                raise IntegrityError("ledger contains duplicate idempotency identity")
            idempotency[identity] = package

            try:
                if operation == "create_task":
                    if sequence != 1 or task is not None or actor != SYSTEM_CATALOG_PRINCIPAL:
                        raise IntegrityError("task creation event identity is invalid")
                    normalized = validate_create_task(request)
                    if normalized["task_id"] != task_id:
                        raise IntegrityError("task creation body/task directory mismatch")
                    task = self._task_projection(normalized)
                    expected_payload = {"task": copy.deepcopy(task)}
                else:
                    if task is None or sequence == 1 or actor == SYSTEM_CATALOG_PRINCIPAL:
                        raise IntegrityError("non-creation event actor/position is invalid")
                    if operation == "claim_task":
                        normalized = validate_claim(request)
                        if normalized["expected_revision"] != revision or claimed_by is not None:
                            raise IntegrityError("stored claim violates revision/single-claim rules")
                        expected_payload = {"claimed_by_principal_id": actor}
                        claimed_by = actor
                    elif operation == "release_task":
                        normalized = validate_release(request)
                        if normalized["expected_revision"] != revision or claimed_by != actor:
                            raise IntegrityError("stored release violates owner/revision rules")
                        expected_payload = {
                            "released_by_principal_id": actor,
                            "reason": normalized["reason"],
                        }
                        claimed_by = None
                    elif operation == "submit_change_set":
                        normalized = validate_change_set(request, task)
                        if normalized["expected_revision"] != revision or claimed_by != actor:
                            raise IntegrityError("stored change-set violates owner/revision rules")
                        change_set_id = self._expected_change_set_id(
                            task_id,
                            actor,
                            normalized,
                            schema_version=event["record_schema_version"],
                        )
                        expected_payload = {
                            "change_set_id": change_set_id,
                            "change_counts": {
                                name: len(normalized.get(name, []))
                                for name in (
                                    CHANGE_ARRAY_FIELDS
                                    if event["record_schema_version"] == SCHEMA_VERSION_V2
                                    else LEGACY_CHANGE_ARRAY_FIELDS
                                )
                            },
                            "candidate_overlay_only": True,
                            "central_master_mutated": False,
                            "human_reviewed": False,
                        }
                        if change_set_id in change_sets:
                            raise IntegrityError("duplicate change_set_id in immutable ledger")
                        change_sets[change_set_id] = {
                            "change_set_id": change_set_id,
                            "submitted_by_principal_id": actor,
                            "request": copy.deepcopy(normalized),
                            "event_id": event["event_id"],
                            "sequence": sequence,
                            "created_at_utc": event["created_at_utc"],
                            "decision_id": None,
                            "verdict": None,
                            "decision_evidence_binding_ids": None,
                            **_closed_flags(),
                        }
                    elif operation == "create_decision":
                        normalized = validate_decision(request, task)
                        if normalized["expected_revision"] != revision or claimed_by != actor:
                            raise IntegrityError("stored decision violates owner/revision rules")
                        change_set_id = normalized["change_set_id"]
                        if change_set_id not in change_sets or change_set_id in decision_by_change_set:
                            raise IntegrityError("stored decision references missing/decided change-set")
                        decision_id = self._expected_decision_id(
                            task_id,
                            actor,
                            normalized,
                            schema_version=event["record_schema_version"],
                        )
                        expected_payload = {
                            "decision_id": decision_id,
                            "change_set_id": change_set_id,
                            "verdict": normalized["verdict"],
                            "reason": normalized["reason"],
                            "evidence_binding_ids": copy.deepcopy(
                                normalized["evidence_binding_ids"]
                            ),
                            "candidate_overlay_only": True,
                            "central_master_mutated": False,
                            "human_reviewed": False,
                        }
                        if decision_id in decisions:
                            raise IntegrityError("duplicate decision_id in immutable ledger")
                        decisions[decision_id] = {
                            "decision_id": decision_id,
                            "change_set_id": change_set_id,
                            "decided_by_principal_id": actor,
                            "verdict": normalized["verdict"],
                            "reason": normalized["reason"],
                            "evidence_binding_ids": copy.deepcopy(
                                normalized["evidence_binding_ids"]
                            ),
                            "event_id": event["event_id"],
                            "sequence": sequence,
                            "created_at_utc": event["created_at_utc"],
                            **_closed_flags(),
                        }
                        decision_by_change_set[change_set_id] = decision_id
                        change_sets[change_set_id]["decision_id"] = decision_id
                        change_sets[change_set_id]["verdict"] = normalized["verdict"]
                        change_sets[change_set_id][
                            "decision_evidence_binding_ids"
                        ] = copy.deepcopy(normalized["evidence_binding_ids"])
                    else:
                        raise IntegrityError("stored event has an unsupported operation")
            except ContractError as exc:
                raise IntegrityError(f"stored event request failed contract: {exc}") from exc
            if event["payload"] != expected_payload:
                raise IntegrityError("stored event payload does not match its exact request")
            revision = event["resulting_revision"]

        if task is None or revision is None:
            raise IntegrityError("task ledger did not derive a task state")
        return {
            "task": task,
            "revision": revision,
            "sequence": len(events),
            "claimed_by_principal_id": claimed_by,
            "change_sets": change_sets,
            "decisions": decisions,
            "events": events,
            "idempotency": idempotency,
        }

    @staticmethod
    def _public_state(state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "task": copy.deepcopy(state["task"]),
            "revision": state["revision"],
            "sequence": state["sequence"],
            "claimed": state["claimed_by_principal_id"] is not None,
            "claimed_by_principal_id": state["claimed_by_principal_id"],
            "change_sets": [
                copy.deepcopy(state["change_sets"][key])
                for key in sorted(
                    state["change_sets"],
                    key=lambda key: state["change_sets"][key]["sequence"],
                )
            ],
            "decisions": [
                copy.deepcopy(state["decisions"][key])
                for key in sorted(
                    state["decisions"],
                    key=lambda key: state["decisions"][key]["sequence"],
                )
            ],
            **_closed_flags(),
        }

    def _result(
        self,
        package: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "event": copy.deepcopy(package["event"]),
            "task_state": self._public_state(state),
            "idempotent_replay": replayed,
            "event_relative_path": package["event_relative_path"],
            "event_sha256": package["event_sha256"],
            "commit_relative_path": package["commit_relative_path"],
            "commit_sha256": package["commit_sha256"],
            **_closed_flags(),
        }

    def _idempotency_replay(
        self,
        state: Mapping[str, Any],
        *,
        actor_id: str,
        operation: str,
        key: str,
        request_raw: bytes,
    ) -> dict[str, Any] | None:
        package = state["idempotency"].get((actor_id, operation, key))
        if package is None:
            return None
        if package["request_raw"] != request_raw:
            raise IdempotencyConflictError(
                "the same principal/task/operation/idempotency key was used with a different payload"
            )
        return self._result(package, state, replayed=True)

    @staticmethod
    def _require_non_system_actor(principal_id: str) -> str:
        actor = validate_principal(principal_id)
        if actor == SYSTEM_CATALOG_PRINCIPAL:
            raise AuthorizationError("_system_catalog may only create server-derived tasks")
        return actor

    @staticmethod
    def _assert_current_revision(state: Mapping[str, Any], expected: str) -> None:
        if expected != state["revision"]:
            raise RevisionConflictError(
                "expected_revision is stale; immutable event head CAS failed"
            )

    def create_task(
        self,
        body: Mapping[str, Any] | bytes,
        *,
        principal_id: str,
    ) -> dict[str, Any]:
        actor = validate_principal(principal_id)
        if actor != SYSTEM_CATALOG_PRINCIPAL:
            raise AuthorizationError("only the trusted _system_catalog actor may create tasks")
        request_value, request_raw = request_object_and_bytes(body)
        # Resolve only the immutable idempotency identity before full contract
        # validation.  A previously used key with different bytes is always a
        # 409, even when the changed body would also fail another field rule.
        task_id = validate_identifier(request_value.get("task_id"), "task_id")
        idempotency_key = validate_idempotency_key(
            request_value.get("idempotency_key")
        )
        with self._exclusive_lock():
            task_path = self._path(f"tasks/{task_id}")
            if os.path.lexists(task_path):
                state = self._load_task_locked(task_id)
                replay = self._idempotency_replay(
                    state,
                    actor_id=actor,
                    operation="create_task",
                    key=idempotency_key,
                    request_raw=request_raw,
                )
                if replay is not None:
                    return replay
                # Preserve exact-body errors for never-used keys before the
                # more general task-id conflict.
                validate_create_task(request_value)
                raise ConflictError("task_id already exists in the append-only ledger")
            normalized = validate_create_task(request_value)
            task_root = self._mkdir_exclusive(f"tasks/{task_id}")
            try:
                os.mkdir(task_root / "events", mode=0o700)
                package = self._append_event_locked(
                    task_id=task_id,
                    sequence=1,
                    previous_revision=None,
                    actor_id=actor,
                    operation="create_task",
                    request_raw=request_raw,
                    request_value=normalized,
                    payload={"task": self._task_projection(normalized)},
                    schema_version=(
                        SCHEMA_VERSION_V2
                        if "source_binding_candidates" in normalized
                        else SCHEMA_VERSION_V1
                    ),
                    task_root_precreated=True,
                )
                state = self._load_task_locked(task_id)
                return self._result(package, state, replayed=False)
            except Exception:
                if os.path.lexists(task_root):
                    try:
                        self._remove_uncommitted_tree(task_root)
                    except IntegrityError:
                        pass
                raise

    create_review_task = create_task

    def _mutate(
        self,
        task_id: str,
        body: Mapping[str, Any] | bytes,
        *,
        principal_id: str,
        operation: str,
    ) -> dict[str, Any]:
        task_id = validate_identifier(task_id, "task_id")
        actor = self._require_non_system_actor(principal_id)
        request_value, request_raw = request_object_and_bytes(body)
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            idempotency_key = validate_idempotency_key(
                request_value.get("idempotency_key")
            )
            replay = self._idempotency_replay(
                state,
                actor_id=actor,
                operation=operation,
                key=idempotency_key,
                request_raw=request_raw,
            )
            if replay is not None:
                return replay
            if operation == "claim_task":
                normalized = validate_claim(request_value)
            elif operation == "release_task":
                normalized = validate_release(request_value)
            elif operation == "submit_change_set":
                normalized = validate_change_set(request_value, state["task"])
            elif operation == "create_decision":
                normalized = validate_decision(request_value, state["task"])
            else:
                raise ContractError("unsupported theme review operation")

            self._assert_current_revision(state, normalized["expected_revision"])

            if operation == "claim_task":
                if state["claimed_by_principal_id"] is not None:
                    raise ConflictError("task already has one active claimant")
                payload = {"claimed_by_principal_id": actor}
            elif operation == "release_task":
                if state["claimed_by_principal_id"] != actor:
                    raise AuthorizationError("only the active claimant may release this task")
                payload = {
                    "released_by_principal_id": actor,
                    "reason": normalized["reason"],
                }
            elif operation == "submit_change_set":
                if state["claimed_by_principal_id"] != actor:
                    raise AuthorizationError("only the active claimant may submit a change-set")
                task_schema_version = (
                    SCHEMA_VERSION_V2
                    if "source_binding_candidates" in state["task"]
                    else SCHEMA_VERSION_V1
                )
                change_set_id = self._expected_change_set_id(
                    task_id, actor, normalized, schema_version=task_schema_version
                )
                payload = {
                    "change_set_id": change_set_id,
                    "change_counts": {
                        name: len(normalized.get(name, []))
                        for name in (
                            CHANGE_ARRAY_FIELDS
                            if "source_binding_candidates" in state["task"]
                            else LEGACY_CHANGE_ARRAY_FIELDS
                        )
                    },
                    "candidate_overlay_only": True,
                    "central_master_mutated": False,
                    "human_reviewed": False,
                }
            else:
                if state["claimed_by_principal_id"] != actor:
                    raise AuthorizationError("only the active claimant may decide this task")
                change_set_id = normalized["change_set_id"]
                if change_set_id not in state["change_sets"]:
                    raise NotFoundError("decision references an unknown change_set_id")
                if state["change_sets"][change_set_id]["decision_id"] is not None:
                    raise ConflictError("change-set already has an immutable decision")
                task_schema_version = (
                    SCHEMA_VERSION_V2
                    if "source_binding_candidates" in state["task"]
                    else SCHEMA_VERSION_V1
                )
                decision_id = self._expected_decision_id(
                    task_id, actor, normalized, schema_version=task_schema_version
                )
                payload = {
                    "decision_id": decision_id,
                    "change_set_id": change_set_id,
                    "verdict": normalized["verdict"],
                    "reason": normalized["reason"],
                    "evidence_binding_ids": copy.deepcopy(
                        normalized["evidence_binding_ids"]
                    ),
                    "candidate_overlay_only": True,
                    "central_master_mutated": False,
                    "human_reviewed": False,
                }

            package = self._append_event_locked(
                task_id=task_id,
                sequence=state["sequence"] + 1,
                previous_revision=state["revision"],
                actor_id=actor,
                operation=operation,
                request_raw=request_raw,
                request_value=normalized,
                payload=payload,
                schema_version=(
                    SCHEMA_VERSION_V2
                    if "source_binding_candidates" in state["task"]
                    else SCHEMA_VERSION_V1
                ),
            )
            updated = self._load_task_locked(task_id)
            return self._result(package, updated, replayed=False)

    def claim_task(
        self, task_id: str, body: Mapping[str, Any] | bytes, *, principal_id: str
    ) -> dict[str, Any]:
        return self._mutate(task_id, body, principal_id=principal_id, operation="claim_task")

    def release_task(
        self, task_id: str, body: Mapping[str, Any] | bytes, *, principal_id: str
    ) -> dict[str, Any]:
        return self._mutate(task_id, body, principal_id=principal_id, operation="release_task")

    def submit_change_set(
        self, task_id: str, body: Mapping[str, Any] | bytes, *, principal_id: str
    ) -> dict[str, Any]:
        return self._mutate(
            task_id, body, principal_id=principal_id, operation="submit_change_set"
        )

    create_change_set = submit_change_set

    def create_decision(
        self, task_id: str, body: Mapping[str, Any] | bytes, *, principal_id: str
    ) -> dict[str, Any]:
        return self._mutate(
            task_id, body, principal_id=principal_id, operation="create_decision"
        )

    decide_change_set = create_decision

    def get_task(self, task_id: str) -> dict[str, Any]:
        task_id = validate_identifier(task_id, "task_id")
        with self._exclusive_lock():
            return self._public_state(self._load_task_locked(task_id))

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._exclusive_lock():
            tasks_root = self._path("tasks")
            info = os.lstat(tasks_root)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError("tasks root is unsafe")
            task_ids: list[str] = []
            for entry in os.scandir(tasks_root):
                child = entry.stat(follow_symlinks=False)
                if _stat_is_reparse(child) or not stat.S_ISDIR(child.st_mode):
                    raise IntegrityError("tasks root contains an unsafe entry")
                task_ids.append(validate_identifier(entry.name, "stored task_id"))
            return [
                self._public_state(self._load_task_locked(task_id))
                for task_id in sorted(task_ids)
            ]

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        task_id = validate_identifier(task_id, "task_id")
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            return [copy.deepcopy(package["event"]) for package in state["events"]]

    def get_event(self, task_id: str, event_id: str) -> dict[str, Any]:
        task_id = validate_identifier(task_id, "task_id")
        validate_identifier(event_id, "event_id")
        if not _EVENT_ID_RE.fullmatch(event_id):
            raise ContractError("event_id is outside the theme review namespace")
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            for package in state["events"]:
                if package["event"]["event_id"] == event_id:
                    return {
                        "event": copy.deepcopy(package["event"]),
                        "event_sha256": package["event_sha256"],
                        "commit_sha256": package["commit_sha256"],
                        **_closed_flags(),
                    }
            raise NotFoundError("event does not exist")

    def list_change_sets(self, task_id: str) -> list[dict[str, Any]]:
        return self.get_task(task_id)["change_sets"]

    def list_decisions(self, task_id: str) -> list[dict[str, Any]]:
        return self.get_task(task_id)["decisions"]

    def get_change_set(self, task_id: str, change_set_id: str) -> dict[str, Any]:
        task_id = validate_identifier(task_id, "task_id")
        validate_identifier(change_set_id, "change_set_id")
        if not _CHANGE_SET_ID_RE.fullmatch(change_set_id):
            raise ContractError("change_set_id is outside the theme review namespace")
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            try:
                value = state["change_sets"][change_set_id]
            except KeyError as exc:
                raise NotFoundError("change-set does not exist") from exc
            return {**copy.deepcopy(value), **_closed_flags()}

    def get_decision(self, task_id: str, decision_id: str) -> dict[str, Any]:
        task_id = validate_identifier(task_id, "task_id")
        validate_identifier(decision_id, "decision_id")
        if not _DECISION_ID_RE.fullmatch(decision_id):
            raise ContractError("decision_id is outside the theme review namespace")
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            try:
                value = state["decisions"][decision_id]
            except KeyError as exc:
                raise NotFoundError("decision does not exist") from exc
            return {**copy.deepcopy(value), **_closed_flags()}

    def preview_candidate_overlay(
        self, task_id: str, change_set_id: str
    ) -> dict[str, Any]:
        """Project one immutable change-set without applying it anywhere."""

        task_id = validate_identifier(task_id, "task_id")
        validate_identifier(change_set_id, "change_set_id")
        if not _CHANGE_SET_ID_RE.fullmatch(change_set_id):
            raise ContractError("change_set_id is outside the theme review namespace")
        with self._exclusive_lock():
            state = self._load_task_locked(task_id)
            try:
                change_set = state["change_sets"][change_set_id]
            except KeyError as exc:
                raise NotFoundError("change-set does not exist") from exc
            request = change_set["request"]
            verdict = change_set["verdict"]
            status = {
                None: "pending_decision",
                "accept_candidate_overlay": "accepted_candidate_overlay_only",
                "reject": "rejected",
                "request_changes": "changes_requested",
                "blocked": "blocked",
            }[verdict]
            return {
                "task_id": task_id,
                "paper_id": state["task"]["paper_id"],
                "theme_id": state["task"]["theme_id"],
                "change_set_id": change_set_id,
                "base_snapshot_sha256": request["base_snapshot_sha256"],
                "reason": request["reason"],
                "tag_replacements": copy.deepcopy(request["tag_replacements"]),
                "hierarchy_replacements": copy.deepcopy(
                    request["hierarchy_replacements"]
                ),
                "atomic_boundary_candidates": copy.deepcopy(
                    request["atomic_boundary_candidates"]
                ),
                "dependency_replacements": copy.deepcopy(
                    request["dependency_replacements"]
                ),
                "source_binding_candidates": copy.deepcopy(
                    request.get("source_binding_candidates", [])
                ),
                "decision_id": change_set["decision_id"],
                "verdict": verdict,
                "decision_evidence_binding_ids": copy.deepcopy(
                    change_set["decision_evidence_binding_ids"]
                ),
                "overlay_status": status,
                "eligible_for_central_apply": False,
                **_closed_flags(),
            }

    def status(self) -> dict[str, Any]:
        tasks = self.list_tasks()
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "supported_schema_versions": sorted(SUPPORTED_SCHEMA_VERSIONS),
            "task_count": len(tasks),
            "claimed_task_count": sum(1 for task in tasks if task["claimed"]),
            "change_set_count": sum(len(task["change_sets"]) for task in tasks),
            "decision_count": sum(len(task["decisions"]) for task in tasks),
            "central_kb_accessed": False,
            **_closed_flags(),
        }
