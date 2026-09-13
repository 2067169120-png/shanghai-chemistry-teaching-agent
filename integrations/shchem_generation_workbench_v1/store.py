"""Append-only task, freeze, run-event, and plan-artifact storage."""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import re
import shutil
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import (
    canonical_json_bytes,
    parse_json_object,
    validate_id_sequence,
    validate_identifier,
    validate_task_card,
)
from .errors import (
    ContractError,
    IntegrityError,
    StateTransitionError,
    StoreConflictError,
)
from .preflight import evidence_preflight

_SELF_HASH_KEY = "self_hash_sha256"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EVENT_SEQUENCE = (
    "task_card_candidate",
    "task_card_frozen",
    "evidence_preflight",
    "generation_started",
    "candidate_generated",
    "deterministic_machine_pass",
    "independent_machine_review",
    "adversarial_check",
    "automated_verified_candidate",
)
_MACHINE_REFERENCE_EVENTS = frozenset(
    {
        "deterministic_machine_pass",
        "independent_machine_review",
        "adversarial_check",
        "automated_verified_candidate",
    }
)
_AUTHORITY = {
    "human_reviewed": False,
    "publication_allowed": False,
    "official": False,
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ContractError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _stat_marker(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _stat_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def validate_relative_path(value: Any) -> str:
    """Validate one canonical POSIX relative path for workspace-confined records."""

    if type(value) is not str or not value or len(value) > 1024:
        raise ContractError("relative path must be a non-empty bounded string")
    if "\\" in value or "\x00" in value or ":" in value:
        raise ContractError("backslashes, NUL, and drive/stream separators are forbidden")
    if value.startswith(("/", "//")) or value.endswith("/") or "//" in value:
        raise ContractError("path must be canonical and relative")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise ContractError("path must use canonical POSIX relative form")
    if not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ContractError("path traversal and empty components are forbidden")
    return value


def _is_reparse(path: Path) -> bool:
    info = os.lstat(path)
    return _stat_is_reparse(info)


def _stat_is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_existing_chain_no_reparse(path: Path, name: str) -> None:
    """lstat every existing lexical component without resolving through it."""

    chain = list(reversed((path, *path.parents)))
    for component in chain:
        if not os.path.lexists(component):
            raise ContractError(f"{name} has a missing lexical path component")
        try:
            info = os.lstat(component)
        except OSError as exc:
            raise ContractError(f"{name} path component cannot be lstat-verified") from exc
        if _stat_is_reparse(info):
            raise ContractError(f"{name} path chain must not contain a symlink/reparse point")


def _make_record(
    record_type: str,
    *,
    task_id: str,
    payload: Mapping[str, Any],
    run_id: str | None = None,
    raw_input: bytes | None = None,
) -> tuple[dict[str, Any], bytes]:
    record: dict[str, Any] = {
        "record_schema_version": "generation_workbench_record_v1",
        "record_type": record_type,
        "created_at_utc": _utc_now(),
        "task_id": task_id,
        "run_id": run_id,
        "payload": copy.deepcopy(dict(payload)),
        "raw_input_base64": None,
        "raw_input_size_bytes": None,
        "raw_input_sha256": None,
        **_AUTHORITY,
        _SELF_HASH_KEY: None,
    }
    if raw_input is not None:
        record["raw_input_base64"] = base64.b64encode(raw_input).decode("ascii")
        record["raw_input_size_bytes"] = len(raw_input)
        record["raw_input_sha256"] = _sha256(raw_input)
    record[_SELF_HASH_KEY] = _sha256(canonical_json_bytes(record))
    encoded = canonical_json_bytes(record)
    return record, encoded


def _verify_record(record: Any, *, expected_type: str | None = None) -> dict[str, Any]:
    if type(record) is not dict:
        raise IntegrityError("stored record is not an object")
    required = {
        "record_schema_version",
        "record_type",
        "created_at_utc",
        "task_id",
        "run_id",
        "payload",
        "raw_input_base64",
        "raw_input_size_bytes",
        "raw_input_sha256",
        "human_reviewed",
        "publication_allowed",
        "official",
        _SELF_HASH_KEY,
    }
    if set(record) != required:
        raise IntegrityError("stored record keys are not exact")
    if record["record_schema_version"] != "generation_workbench_record_v1":
        raise IntegrityError("stored record schema version is invalid")
    if expected_type is not None and record["record_type"] != expected_type:
        raise IntegrityError("stored record type mismatch")
    if any(record[name] is not False for name in _AUTHORITY):
        raise IntegrityError("stored record attempted to elevate authority")
    supplied = record[_SELF_HASH_KEY]
    if type(supplied) is not str or not _HASH_RE.fullmatch(supplied):
        raise IntegrityError("stored record self hash is invalid")
    unsigned = copy.deepcopy(record)
    unsigned[_SELF_HASH_KEY] = None
    if _sha256(canonical_json_bytes(unsigned)) != supplied:
        raise IntegrityError("stored record self hash mismatch")
    raw_fields = (
        record["raw_input_base64"],
        record["raw_input_size_bytes"],
        record["raw_input_sha256"],
    )
    if raw_fields == (None, None, None):
        pass
    elif any(item is None for item in raw_fields):
        raise IntegrityError("stored record raw-input binding is incomplete")
    else:
        try:
            raw = base64.b64decode(record["raw_input_base64"], validate=True)
        except Exception as exc:
            raise IntegrityError("stored record raw input is not canonical base64") from exc
        if len(raw) != record["raw_input_size_bytes"] or _sha256(raw) != record["raw_input_sha256"]:
            raise IntegrityError("stored record raw-input bytes/hash binding failed")
    return copy.deepcopy(record)


class AppendOnlyWorkbenchStore:
    """Workspace-confined immutable record store with hash-chained run events."""

    def __init__(self, workspace_root: Path, state_root: str):
        raw_root = Path(workspace_root)
        # This lstat is intentionally before exists(), is_dir(), absolute(), or
        # resolve(): each of those may follow the caller's leaf symlink.
        try:
            raw_info = os.lstat(raw_root)
        except OSError as exc:
            raise ContractError("workspace_root must be an existing directory") from exc
        if _stat_is_reparse(raw_info):
            raise ContractError("raw workspace_root must not be a symlink/reparse point")
        if not stat.S_ISDIR(raw_info.st_mode):
            raise ContractError("workspace_root must be an existing directory")

        lexical_root = Path(os.path.abspath(os.fspath(raw_root)))
        _assert_existing_chain_no_reparse(lexical_root, "raw workspace_root")
        try:
            resolved_root = lexical_root.resolve(strict=True)
        except OSError as exc:
            raise ContractError("workspace_root could not be resolved safely") from exc
        _assert_existing_chain_no_reparse(resolved_root, "resolved workspace_root")
        resolved_info = os.lstat(resolved_root)
        if _stat_is_reparse(resolved_info) or not stat.S_ISDIR(resolved_info.st_mode):
            raise ContractError("resolved workspace_root must be a real directory")
        self.workspace_root = resolved_root
        self.state_relative = validate_relative_path(state_root)
        self.state_root = self._ensure_directory_tree(self.state_relative)
        self._ensure_directory_tree(f"{self.state_relative}/tasks")
        self._ensure_directory_tree(f"{self.state_relative}/runs")
        # Captured inside the exclusive writer before a caller-side fault hook can
        # mutate and restore a file.  Plan-run transactions compare these markers
        # as well as bytes, which makes same-size and ABA drift fail closed.
        self._exclusive_write_markers: dict[str, tuple[int, ...]] = {}

    def _ensure_directory_tree(self, relative: str) -> Path:
        relative = validate_relative_path(relative)
        current = self.workspace_root
        for component in PurePosixPath(relative).parts:
            current = current / component
            try:
                os.mkdir(current, mode=0o700)
            except FileExistsError:
                if not current.is_dir():
                    raise ContractError("store path collides with a non-directory")
            if _is_reparse(current):
                raise ContractError("reparse points are forbidden in store paths")
        return current

    def _path(self, relative: str, *, require_exists: bool = False) -> Path:
        relative = validate_relative_path(relative)
        path = self.workspace_root.joinpath(*PurePosixPath(relative).parts)
        current = self.workspace_root
        for component in PurePosixPath(relative).parts:
            current = current / component
            if current.exists() or os.path.lexists(current):
                if _is_reparse(current):
                    raise IntegrityError("reparse point encountered in record path")
            else:
                break
        if require_exists and not path.is_file():
            raise IntegrityError(f"immutable record is missing: {relative}")
        return path

    def _mkdir_exclusive(self, relative: str) -> Path:
        path = self._path(relative)
        parent = path.parent
        if not parent.is_dir() or _is_reparse(parent):
            raise IntegrityError("exclusive directory parent is unavailable or unsafe")
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError as exc:
            raise StoreConflictError(f"append-only directory already exists: {relative}") from exc
        return path

    def _write_exclusive(self, relative: str, data: bytes) -> dict[str, Any]:
        path = self._path(relative)
        if not path.parent.is_dir() or _is_reparse(path.parent):
            raise IntegrityError("immutable record parent is unavailable or unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise StoreConflictError(f"append-only record already exists: {relative}") from exc
        # A failed write deliberately retains its partial record so append-only
        # verification fails closed; enclosing transactions remove their owned
        # pending package before propagating the exception.
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        written = os.lstat(path)
        if _stat_is_reparse(written) or not stat.S_ISREG(written.st_mode):
            raise IntegrityError("exclusive write did not create a regular record")
        if "/runs/" in f"/{relative}" and ".pending-" in relative:
            self._exclusive_write_markers[relative] = _stat_marker(written)
        return {"relative_path": relative, "file_size_bytes": len(data), "file_sha256": _sha256(data)}

    def _read_bytes(self, relative: str) -> bytes:
        path = self._path(relative, require_exists=True)
        before = os.stat(path, follow_symlinks=False)
        data = path.read_bytes()
        after = os.stat(path, follow_symlinks=False)
        if _stat_marker(before) != _stat_marker(after) or len(data) != after.st_size:
            raise IntegrityError("record drifted while being read")
        return data

    @staticmethod
    def _parse_record_bytes(raw: bytes, *, expected_type: str | None = None) -> dict[str, Any]:
        try:
            parsed = parse_json_object(raw, maximum_bytes=4_000_000)
        except ContractError as exc:
            raise IntegrityError(f"stored record is not strict JSON: {exc}") from exc
        if canonical_json_bytes(parsed) != raw:
            raise IntegrityError("stored record bytes are not canonical JSON")
        return _verify_record(parsed, expected_type=expected_type)

    def _read_record(self, relative: str, *, expected_type: str | None = None) -> tuple[dict[str, Any], bytes]:
        raw = self._read_bytes(relative)
        return self._parse_record_bytes(raw, expected_type=expected_type), raw

    @staticmethod
    def _validate_candidate_record(record: dict[str, Any], task_id: str) -> dict[str, Any]:
        if record["task_id"] != task_id or record["run_id"] is not None:
            raise IntegrityError("candidate record identity mismatch")
        card = validate_task_card(record["payload"])
        try:
            original = base64.b64decode(record["raw_input_base64"], validate=True)
            original_card = validate_task_card(parse_json_object(original))
        except (ContractError, TypeError, ValueError) as exc:
            raise IntegrityError("candidate original bytes do not contain a valid task card") from exc
        if original_card != card:
            raise IntegrityError("candidate original bytes and normalized payload differ")
        return card

    @staticmethod
    def _read_descriptor_all(descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _open_candidate_observation(self, task_id: str) -> dict[str, Any]:
        """Open and verify one candidate while retaining its descriptor for freeze checks."""

        validate_identifier(task_id, "task_id")
        relative = f"{self.state_relative}/tasks/{task_id}/candidate/task_card_candidate.json"
        path = self._path(relative, require_exists=True)
        before = os.lstat(path)
        if _stat_is_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise IntegrityError("candidate path is not a regular non-reparse file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise IntegrityError("candidate could not be opened without following links") from exc
        try:
            opened = os.fstat(descriptor)
            if _stat_identity(opened) != _stat_identity(before):
                raise IntegrityError("candidate changed between lstat and open")
            raw = self._read_descriptor_all(descriptor)
            after_read = os.fstat(descriptor)
            path_after_read = os.lstat(path)
            if (
                _stat_marker(after_read) != _stat_marker(opened)
                or _stat_identity(path_after_read) != _stat_identity(opened)
                or path_after_read.st_size != opened.st_size
                or len(raw) != opened.st_size
            ):
                raise IntegrityError("candidate drifted during stable observation")
            record = self._parse_record_bytes(raw, expected_type="task_card_candidate")
            self._validate_candidate_record(record, task_id)
            return {
                "descriptor": descriptor,
                "path": path,
                "relative_path": relative,
                "marker": _stat_marker(opened),
                "raw": raw,
                "record": record,
                "file_size_bytes": len(raw),
                "file_sha256": _sha256(raw),
            }
        except Exception:
            os.close(descriptor)
            raise

    def _assert_candidate_observation_unchanged(self, observation: Mapping[str, Any]) -> None:
        descriptor = observation["descriptor"]
        path = observation["path"]
        try:
            descriptor_info = os.fstat(descriptor)
            path_info = os.lstat(path)
            current = self._read_descriptor_all(descriptor)
            descriptor_after = os.fstat(descriptor)
        except OSError as exc:
            raise IntegrityError("candidate disappeared during freeze") from exc
        marker = observation["marker"]
        if (
            _stat_is_reparse(path_info)
            or _stat_marker(descriptor_info) != marker
            or _stat_marker(descriptor_after) != marker
            or _stat_identity(path_info) != _stat_identity(descriptor_info)
            or path_info.st_size != descriptor_info.st_size
            or current != observation["raw"]
        ):
            raise IntegrityError("candidate changed after freeze confirmation")

    @staticmethod
    def _new_task_id() -> str:
        return f"task-{uuid.uuid4().hex}"

    @staticmethod
    def _new_run_id() -> str:
        return f"run-{uuid.uuid4().hex}"

    def create_task_card_candidate(self, raw_task_card: bytes) -> dict[str, Any]:
        card = validate_task_card(parse_json_object(raw_task_card))
        task_id = self._new_task_id()
        task_base = f"{self.state_relative}/tasks/{task_id}"
        self._mkdir_exclusive(task_base)
        self._mkdir_exclusive(f"{task_base}/candidate")
        record, encoded = _make_record(
            "task_card_candidate",
            task_id=task_id,
            payload=card,
            raw_input=raw_task_card,
        )
        relative = f"{task_base}/candidate/task_card_candidate.json"
        descriptor = self._write_exclusive(relative, encoded)
        return {
            "task_id": task_id,
            "record": record,
            **descriptor,
            **_AUTHORITY,
        }

    def read_task_card_candidate(self, task_id: str) -> dict[str, Any]:
        validate_identifier(task_id, "task_id")
        relative = f"{self.state_relative}/tasks/{task_id}/candidate/task_card_candidate.json"
        record, raw = self._read_record(relative, expected_type="task_card_candidate")
        self._validate_candidate_record(record, task_id)
        return {
            "task_id": task_id,
            "record": record,
            "relative_path": relative,
            "file_size_bytes": len(raw),
            "file_sha256": _sha256(raw),
            **_AUTHORITY,
        }

    def _rollback_freeze_transaction(self, path: Path, task_base: str) -> None:
        """Remove only an unreturned transaction package; no public delete API exists."""

        if not os.path.lexists(path):
            return
        expected_parent = self._path(task_base)
        if path.parent != expected_parent or not (
            path.name == "frozen" or path.name.startswith("frozen.pending-")
        ):
            raise IntegrityError("freeze rollback target escaped its task directory")
        if _is_reparse(path):
            raise IntegrityError("freeze rollback refuses a reparse target")
        shutil.rmtree(path)

    def freeze_task_card(self, task_id: str, expected_candidate_file_sha256: str) -> dict[str, Any]:
        _validate_sha256(expected_candidate_file_sha256, "expected_candidate_file_sha256")
        observation = self._open_candidate_observation(task_id)
        task_base = f"{self.state_relative}/tasks/{task_id}"
        freeze_base = f"{task_base}/frozen"
        pending_base = f"{task_base}/frozen.pending-{uuid.uuid4().hex}"
        pending_path: Path | None = None
        final_path = self._path(freeze_base)
        renamed = False
        try:
            if observation["file_sha256"] != expected_candidate_file_sha256:
                raise IntegrityError("candidate hash is stale")
            if os.path.lexists(final_path):
                raise StoreConflictError(f"append-only directory already exists: {freeze_base}")

            pending_path = self._mkdir_exclusive(pending_base)
            self._mkdir_exclusive(f"{pending_base}/snapshots")
            snapshot_name = f"sha256-{observation['file_sha256']}.json"
            pending_snapshot_relative = f"{pending_base}/snapshots/{snapshot_name}"
            final_snapshot_relative = f"{freeze_base}/snapshots/{snapshot_name}"
            snapshot_descriptor = self._write_exclusive(
                pending_snapshot_relative, observation["raw"]
            )
            if (
                snapshot_descriptor["file_size_bytes"] != observation["file_size_bytes"]
                or snapshot_descriptor["file_sha256"] != observation["file_sha256"]
            ):
                raise IntegrityError("candidate snapshot write binding failed")
            snapshot_raw = self._read_bytes(pending_snapshot_relative)
            if snapshot_raw != observation["raw"]:
                raise IntegrityError("candidate snapshot bytes differ from the observation")
            snapshot_record = self._parse_record_bytes(
                snapshot_raw, expected_type="task_card_candidate"
            )
            self._validate_candidate_record(snapshot_record, task_id)

            payload = {
                "schema_version": "generation_task_card_freeze_v1",
                "candidate_snapshot_relpath": final_snapshot_relative,
                "candidate_snapshot_size_bytes": observation["file_size_bytes"],
                "candidate_snapshot_sha256": observation["file_sha256"],
                "candidate_payload_sha256": _sha256(
                    canonical_json_bytes(observation["record"]["payload"])
                ),
                "candidate_record_self_hash_sha256": observation["record"][_SELF_HASH_KEY],
            }
            freeze_record, freeze_encoded = _make_record(
                "task_card_frozen", task_id=task_id, payload=payload
            )
            self._write_exclusive(
                f"{pending_base}/task_card_frozen.json", freeze_encoded
            )

            # Detect same-size changes and ordinary ABA writes after the snapshot.
            self._assert_candidate_observation_unchanged(observation)
            try:
                os.rename(pending_path, final_path)
            except OSError as exc:
                if os.path.lexists(final_path):
                    raise StoreConflictError(
                        f"append-only directory already exists: {freeze_base}"
                    ) from exc
                raise
            renamed = True
            self._assert_candidate_observation_unchanged(observation)

            freeze_relative = f"{freeze_base}/task_card_frozen.json"
            committed_freeze, committed_freeze_raw = self._read_record(
                freeze_relative, expected_type="task_card_frozen"
            )
            if committed_freeze != freeze_record or committed_freeze_raw != freeze_encoded:
                raise IntegrityError("freeze record drifted during atomic directory commit")
            commit_payload = {
                "schema_version": "generation_task_card_freeze_commit_v1",
                "freeze_record_relpath": freeze_relative,
                "freeze_file_size_bytes": len(committed_freeze_raw),
                "freeze_file_sha256": _sha256(committed_freeze_raw),
                "freeze_record_self_hash_sha256": committed_freeze[_SELF_HASH_KEY],
                "candidate_snapshot_relpath": final_snapshot_relative,
                "candidate_snapshot_size_bytes": observation["file_size_bytes"],
                "candidate_snapshot_sha256": observation["file_sha256"],
            }
            commit_record, commit_encoded = _make_record(
                "task_card_freeze_commit", task_id=task_id, payload=commit_payload
            )
            self._write_exclusive(
                f"{freeze_base}/freeze_committed.json", commit_encoded
            )
            self._assert_candidate_observation_unchanged(observation)
            result = self.read_frozen_task_card(task_id)
            self._assert_candidate_observation_unchanged(observation)
            if result["commit_record"] != commit_record:
                raise IntegrityError("freeze commit record changed before API return")
            return result
        except Exception:
            rollback_path = final_path if renamed else pending_path
            if rollback_path is not None:
                self._rollback_freeze_transaction(rollback_path, task_base)
            raise
        finally:
            os.close(observation["descriptor"])

    def read_frozen_task_card(self, task_id: str) -> dict[str, Any]:
        validate_identifier(task_id, "task_id")
        freeze_base = f"{self.state_relative}/tasks/{task_id}/frozen"
        commit_relative = f"{freeze_base}/freeze_committed.json"
        commit_record, commit_raw = self._read_record(
            commit_relative, expected_type="task_card_freeze_commit"
        )
        if commit_record["task_id"] != task_id or commit_record["run_id"] is not None:
            raise IntegrityError("freeze commit identity mismatch")
        relative = f"{freeze_base}/task_card_frozen.json"
        record, raw = self._read_record(relative, expected_type="task_card_frozen")
        if record["task_id"] != task_id or record["run_id"] is not None:
            raise IntegrityError("freeze record identity mismatch")
        payload = record["payload"]
        expected_keys = {
            "schema_version",
            "candidate_snapshot_relpath",
            "candidate_snapshot_size_bytes",
            "candidate_snapshot_sha256",
            "candidate_payload_sha256",
            "candidate_record_self_hash_sha256",
        }
        if type(payload) is not dict or set(payload) != expected_keys:
            raise IntegrityError("freeze payload keys are invalid")
        if payload["schema_version"] != "generation_task_card_freeze_v1":
            raise IntegrityError("freeze payload version mismatch")
        validate_relative_path(payload["candidate_snapshot_relpath"])
        expected_snapshot_relative = (
            f"{freeze_base}/snapshots/sha256-{payload['candidate_snapshot_sha256']}.json"
        )
        if payload["candidate_snapshot_relpath"] != expected_snapshot_relative:
            raise IntegrityError("freeze snapshot path is not content-addressed")
        bound, snapshot_raw = self._read_record(
            expected_snapshot_relative, expected_type="task_card_candidate"
        )
        card = self._validate_candidate_record(bound, task_id)
        if payload["candidate_snapshot_size_bytes"] != len(snapshot_raw):
            raise IntegrityError("frozen snapshot size binding failed")
        if payload["candidate_snapshot_sha256"] != _sha256(snapshot_raw):
            raise IntegrityError("frozen snapshot byte hash binding failed")
        if payload["candidate_record_self_hash_sha256"] != bound[_SELF_HASH_KEY]:
            raise IntegrityError("frozen candidate self-hash binding failed")
        if payload["candidate_payload_sha256"] != _sha256(canonical_json_bytes(bound["payload"])):
            raise IntegrityError("frozen candidate payload binding failed")

        commit_payload = commit_record["payload"]
        expected_commit_keys = {
            "schema_version",
            "freeze_record_relpath",
            "freeze_file_size_bytes",
            "freeze_file_sha256",
            "freeze_record_self_hash_sha256",
            "candidate_snapshot_relpath",
            "candidate_snapshot_size_bytes",
            "candidate_snapshot_sha256",
        }
        if type(commit_payload) is not dict or set(commit_payload) != expected_commit_keys:
            raise IntegrityError("freeze commit payload keys are invalid")
        if commit_payload != {
            "schema_version": "generation_task_card_freeze_commit_v1",
            "freeze_record_relpath": relative,
            "freeze_file_size_bytes": len(raw),
            "freeze_file_sha256": _sha256(raw),
            "freeze_record_self_hash_sha256": record[_SELF_HASH_KEY],
            "candidate_snapshot_relpath": expected_snapshot_relative,
            "candidate_snapshot_size_bytes": len(snapshot_raw),
            "candidate_snapshot_sha256": _sha256(snapshot_raw),
        }:
            raise IntegrityError("freeze commit does not bind the immutable package")
        return {
            "task_id": task_id,
            "record": record,
            "relative_path": relative,
            "file_size_bytes": len(raw),
            "file_sha256": _sha256(raw),
            "commit_record": commit_record,
            "commit_relative_path": commit_relative,
            "commit_file_size_bytes": len(commit_raw),
            "commit_file_sha256": _sha256(commit_raw),
            "candidate_snapshot_record": bound,
            "candidate_snapshot_relative_path": expected_snapshot_relative,
            "candidate_snapshot_size_bytes": len(snapshot_raw),
            "candidate_snapshot_sha256": _sha256(snapshot_raw),
            "task_card": card,
            **_AUTHORITY,
        }

    def _write_event(
        self,
        run_id: str,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        previous_event_sha256: str | None,
        sequence: int,
    ) -> dict[str, Any]:
        event_payload = {
            "event_sequence": sequence,
            "event_type": event_type,
            "previous_event_file_sha256": previous_event_sha256,
            "event_data": copy.deepcopy(dict(payload)),
        }
        record, encoded = _make_record(
            "generation_run_event",
            task_id=task_id,
            run_id=run_id,
            payload=event_payload,
        )
        relative = f"{self.state_relative}/runs/{run_id}/events/{sequence:06d}.json"
        descriptor = self._write_exclusive(relative, encoded)
        return {"run_id": run_id, "task_id": task_id, "record": record, **descriptor, **_AUTHORITY}

    def create_run(self, task_id: str, expected_candidate_file_sha256: str) -> dict[str, Any]:
        _validate_sha256(expected_candidate_file_sha256, "expected_candidate_file_sha256")
        candidate = self.read_task_card_candidate(task_id)
        if candidate["file_sha256"] != expected_candidate_file_sha256:
            raise IntegrityError("candidate hash is stale when creating run")
        run_id = self._new_run_id()
        run_base = f"{self.state_relative}/runs/{run_id}"
        self._mkdir_exclusive(run_base)
        self._mkdir_exclusive(f"{run_base}/events")
        self._mkdir_exclusive(f"{run_base}/artifacts")
        payload = {
            "candidate_record_relpath": candidate["relative_path"],
            "candidate_file_size_bytes": candidate["file_size_bytes"],
            "candidate_file_sha256": candidate["file_sha256"],
            "candidate_record_self_hash_sha256": candidate["record"][_SELF_HASH_KEY],
        }
        return self._write_event(run_id, task_id, "task_card_candidate", payload, None, 1)

    @staticmethod
    def _is_pending_run_entry(name: str) -> bool:
        return name.endswith(".pending-lock") or ".pending-" in name

    def _read_run_events_at(
        self,
        run_id: str,
        run_base_relative: str,
        *,
        expected_task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        validate_identifier(run_id, "run_id")
        run_base_relative = validate_relative_path(run_base_relative)
        if expected_task_id is not None:
            validate_identifier(expected_task_id, "expected_task_id")
        events_relative = f"{run_base_relative}/events"
        events_dir = self._path(events_relative)
        if not events_dir.is_dir() or _is_reparse(events_dir):
            raise IntegrityError("run events directory is missing or unsafe")
        paths = list(events_dir.iterdir())
        if not paths:
            raise IntegrityError("run has no events")
        expected_names = [f"{index:06d}.json" for index in range(1, len(paths) + 1)]
        actual_names = sorted(path.name for path in paths)
        if actual_names != expected_names or any(not path.is_file() or _is_reparse(path) for path in paths):
            raise IntegrityError("run event files are non-contiguous or unsafe")
        output: list[dict[str, Any]] = []
        previous_hash: str | None = None
        task_id: str | None = None
        for index, name in enumerate(expected_names, start=1):
            relative = f"{events_relative}/{name}"
            record, raw = self._read_record(relative, expected_type="generation_run_event")
            if record["run_id"] != run_id:
                raise IntegrityError("event run identity mismatch")
            if task_id is None:
                task_id = record["task_id"]
            if record["task_id"] != task_id:
                raise IntegrityError("cross-task event detected")
            if expected_task_id is not None and task_id != expected_task_id:
                raise IntegrityError("run belongs to a different task")
            payload = record["payload"]
            if type(payload) is not dict or set(payload) != {
                "event_sequence",
                "event_type",
                "previous_event_file_sha256",
                "event_data",
            }:
                raise IntegrityError("event payload keys are invalid")
            if payload["event_sequence"] != index:
                raise IntegrityError("event sequence number mismatch")
            if index > len(_EVENT_SEQUENCE) or payload["event_type"] != _EVENT_SEQUENCE[index - 1]:
                raise IntegrityError("event state sequence is invalid")
            if payload["previous_event_file_sha256"] != previous_hash:
                raise IntegrityError("event hash-chain predecessor mismatch")
            previous_hash = _sha256(raw)
            output.append(
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "record": record,
                    "relative_path": relative,
                    "file_size_bytes": len(raw),
                    "file_sha256": previous_hash,
                    **_AUTHORITY,
                }
            )
        candidate_data = output[0]["record"]["payload"]["event_data"]
        if type(candidate_data) is not dict or set(candidate_data) != {
            "candidate_record_relpath",
            "candidate_file_size_bytes",
            "candidate_file_sha256",
            "candidate_record_self_hash_sha256",
        }:
            raise IntegrityError("initial run event candidate binding is invalid")
        candidate = self.read_task_card_candidate(task_id)
        if candidate_data != {
            "candidate_record_relpath": candidate["relative_path"],
            "candidate_file_size_bytes": candidate["file_size_bytes"],
            "candidate_file_sha256": candidate["file_sha256"],
            "candidate_record_self_hash_sha256": candidate["record"][_SELF_HASH_KEY],
        }:
            raise IntegrityError("run candidate dependency binding failed")
        if len(output) >= 2:
            freeze_data = output[1]["record"]["payload"]["event_data"]
            if type(freeze_data) is not dict or set(freeze_data) != {
                "freeze_record_relpath",
                "freeze_file_size_bytes",
                "freeze_file_sha256",
                "freeze_record_self_hash_sha256",
            }:
                raise IntegrityError("second run event freeze binding is invalid")
            freeze = self.read_frozen_task_card(task_id)
            if freeze_data != {
                "freeze_record_relpath": freeze["relative_path"],
                "freeze_file_size_bytes": freeze["file_size_bytes"],
                "freeze_file_sha256": freeze["file_sha256"],
                "freeze_record_self_hash_sha256": freeze["record"][_SELF_HASH_KEY],
            }:
                raise IntegrityError("run freeze dependency binding failed")
        return output

    def read_run_events(
        self, run_id: str, *, expected_task_id: str | None = None
    ) -> list[dict[str, Any]]:
        if type(run_id) is str and self._is_pending_run_entry(run_id):
            raise IntegrityError("pending run transactions are not readable")
        return self._read_run_events_at(
            run_id,
            f"{self.state_relative}/runs/{run_id}",
            expected_task_id=expected_task_id,
        )

    @staticmethod
    def _validate_expected_size(value: Any, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ContractError(f"{name} must be a positive integer")
        return value

    def _observe_immutable_dependency(
        self,
        relative: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> dict[str, Any]:
        relative = validate_relative_path(relative)
        _validate_sha256(expected_sha256, "dependency_sha256")
        self._validate_expected_size(expected_size_bytes, "dependency_size_bytes")
        path = self._path(relative, require_exists=True)
        before = os.lstat(path)
        if _stat_is_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise IntegrityError("transaction dependency is not a regular record")
        raw = self._read_bytes(relative)
        after = os.lstat(path)
        if _stat_marker(before) != _stat_marker(after):
            raise IntegrityError("transaction dependency drifted during observation")
        if len(raw) != expected_size_bytes or _sha256(raw) != expected_sha256:
            raise IntegrityError("transaction dependency descriptor is stale")
        return {
            "relative_path": relative,
            "raw": raw,
            "marker": _stat_marker(after),
        }

    def _assert_dependencies_unchanged(
        self, observations: list[dict[str, Any]]
    ) -> None:
        for observation in observations:
            relative = observation["relative_path"]
            path = self._path(relative, require_exists=True)
            before = os.lstat(path)
            if (
                _stat_is_reparse(before)
                or not stat.S_ISREG(before.st_mode)
                or _stat_marker(before) != observation["marker"]
            ):
                raise IntegrityError("transaction dependency changed before commit")
            raw = self._read_bytes(relative)
            after = os.lstat(path)
            if (
                _stat_marker(after) != observation["marker"]
                or raw != observation["raw"]
            ):
                raise IntegrityError("transaction dependency changed before commit")

    def _plan_transaction_checkpoint(
        self, stage: str, context: Mapping[str, Any]
    ) -> None:
        """Fault-injection seam; production execution intentionally does nothing."""

        del stage, context

    def _write_plan_transaction_record(
        self,
        run_base_relative: str,
        suffix: str,
        encoded: bytes,
        *,
        expected_type: str,
        run_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        relative = validate_relative_path(f"{run_base_relative}/{suffix}")
        descriptor = self._write_exclusive(relative, encoded)
        marker = self._exclusive_write_markers.get(relative)
        if marker is None:
            raise IntegrityError("transaction write marker is missing")
        path = self._path(relative, require_exists=True)
        current = os.lstat(path)
        if _stat_marker(current) != marker:
            raise IntegrityError("transaction record changed immediately after write")
        raw = self._read_bytes(relative)
        if raw != encoded:
            raise IntegrityError("transaction record bytes differ from fixed bytes")
        record = self._parse_record_bytes(raw, expected_type=expected_type)
        if record["run_id"] != run_id or record["task_id"] != task_id:
            raise IntegrityError("transaction record identity mismatch")
        return {
            "suffix": suffix,
            "encoded": encoded,
            "marker": marker,
            "record": record,
            "file_size_bytes": descriptor["file_size_bytes"],
            "file_sha256": descriptor["file_sha256"],
        }

    def _assert_plan_transaction_tree(
        self,
        run_base_relative: str,
        *,
        event_count: int,
        artifact_present: bool,
        directory_identities: Mapping[str, tuple[int, int]],
    ) -> None:
        root = self._path(run_base_relative)
        if not root.is_dir() or _is_reparse(root):
            raise IntegrityError("plan transaction root is missing or unsafe")
        for suffix in ("", "events", "artifacts"):
            path = root if not suffix else root / suffix
            if not path.is_dir() or _is_reparse(path):
                raise IntegrityError("plan transaction directory is missing or unsafe")
            if _stat_identity(os.lstat(path)) != directory_identities[suffix]:
                raise IntegrityError("plan transaction directory identity changed")
        if sorted(item.name for item in root.iterdir()) != ["artifacts", "events"]:
            raise IntegrityError("plan transaction root entries are not exact")
        expected_events = [
            f"{index:06d}.json" for index in range(1, event_count + 1)
        ]
        events_dir = root / "events"
        event_entries = list(events_dir.iterdir())
        if sorted(item.name for item in event_entries) != expected_events or any(
            not item.is_file() or _is_reparse(item) for item in event_entries
        ):
            raise IntegrityError("plan transaction event entries are not exact")
        artifacts_dir = root / "artifacts"
        artifact_entries = list(artifacts_dir.iterdir())
        expected_artifacts = ["plan.json"] if artifact_present else []
        if sorted(item.name for item in artifact_entries) != expected_artifacts or any(
            not item.is_file() or _is_reparse(item) for item in artifact_entries
        ):
            raise IntegrityError("plan transaction artifact entries are not exact")

    def _assert_plan_transaction_files(
        self,
        run_base_relative: str,
        expected_files: list[dict[str, Any]],
    ) -> None:
        for expected in expected_files:
            relative = validate_relative_path(
                f"{run_base_relative}/{expected['suffix']}"
            )
            path = self._path(relative, require_exists=True)
            before = os.lstat(path)
            if (
                _stat_is_reparse(before)
                or not stat.S_ISREG(before.st_mode)
                or _stat_marker(before) != expected["marker"]
            ):
                raise IntegrityError("plan transaction record identity or metadata drifted")
            raw = self._read_bytes(relative)
            after = os.lstat(path)
            if (
                _stat_marker(after) != expected["marker"]
                or raw != expected["encoded"]
                or len(raw) != expected["file_size_bytes"]
                or _sha256(raw) != expected["file_sha256"]
            ):
                raise IntegrityError("plan transaction fixed-byte verification failed")
            record = self._parse_record_bytes(
                raw, expected_type=expected["record"]["record_type"]
            )
            if record != expected["record"]:
                raise IntegrityError("plan transaction record changed semantically")

    def _verify_plan_transaction_stage(
        self,
        stage: str,
        context: dict[str, Any],
        *,
        run_base_relative: str,
        expected_files: list[dict[str, Any]],
        event_count: int,
        artifact_present: bool,
        directory_identities: Mapping[str, tuple[int, int]],
        dependency_observations: list[dict[str, Any]],
    ) -> None:
        context["stage"] = stage
        context["active_run_base_relative"] = run_base_relative
        self._plan_transaction_checkpoint(stage, context)
        self._assert_dependencies_unchanged(dependency_observations)
        self._assert_plan_transaction_tree(
            run_base_relative,
            event_count=event_count,
            artifact_present=artifact_present,
            directory_identities=directory_identities,
        )
        self._assert_plan_transaction_files(run_base_relative, expected_files)

    def _rollback_plan_transaction(
        self,
        *,
        pending_path: Path | None,
        final_path: Path,
        owned_run_identity: tuple[int, int] | None,
        lock_path: Path | None,
        owned_lock_identity: tuple[int, int] | None,
    ) -> None:
        runs_parent = self._path(f"{self.state_relative}/runs")
        for path in (pending_path, final_path):
            if path is None or not os.path.lexists(path):
                continue
            if path.parent != runs_parent:
                raise IntegrityError("plan transaction rollback escaped runs directory")
            info = os.lstat(path)
            if (
                owned_run_identity is None
                or _stat_is_reparse(info)
                or _stat_identity(info) != owned_run_identity
            ):
                if path == final_path:
                    # A pre-existing final belongs to another transaction and is
                    # never a rollback target.
                    continue
                raise IntegrityError("plan transaction rollback ownership changed")
            shutil.rmtree(path)
        if lock_path is not None and os.path.lexists(lock_path):
            if lock_path.parent != runs_parent:
                raise IntegrityError("plan transaction lock escaped runs directory")
            info = os.lstat(lock_path)
            if (
                owned_lock_identity is None
                or _stat_is_reparse(info)
                or _stat_identity(info) != owned_lock_identity
            ):
                raise IntegrityError("plan transaction lock ownership changed")
            os.rmdir(lock_path)

    @staticmethod
    def _plan_artifact_payload(
        card: Mapping[str, Any], report: Mapping[str, Any], head_sha256: str
    ) -> dict[str, Any]:
        return {
            "schema_version": "generation_plan_artifact_v1",
            "run_head_event_sha256": head_sha256,
            "target_kind": card["target_kind"],
            "effective_mode": report["effective_mode"],
            "knowledge_scope": copy.deepcopy(card["knowledge_scope"]),
            "ability_scope": copy.deepcopy(card["ability_scope"]),
            "theme_count": copy.deepcopy(card["theme_count"]),
            "duration_minutes": copy.deepcopy(card["duration_minutes"]),
            "total_score": copy.deepcopy(card["total_score"]),
            "difficulty_targets": copy.deepcopy(card["difficulty_targets"]),
            "paper_structure": copy.deepcopy(card["paper_structure"]),
            "numbering_rules": copy.deepcopy(card["numbering_rules"]),
            "selection_scoring_rules": copy.deepcopy(
                card["selection_scoring_rules"]
            ),
            "prohibited_content": copy.deepcopy(card["prohibited_content"]),
            "figure_types": copy.deepcopy(card["figure_types"]),
            "evidence_record_ids": copy.deepcopy(card["evidence_record_ids"]),
            "blockers": copy.deepcopy(report["blockers"]),
            "contains_generated_questions": False,
            "next_state_requires_new_verified_records": True,
        }

    def create_plan_run_transaction(
        self,
        task_id: str,
        *,
        expected_candidate_file_sha256: str,
        expected_candidate_file_size_bytes: int,
        expected_freeze_file_sha256: str,
        expected_freeze_file_size_bytes: int,
        evidence_summaries: Any,
        provider_status: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically commit the three-event, plan-only run package.

        Nothing under the final ``runs/<run_id>`` path is visible until every
        record has passed fixed-byte, identity, topology, and dependency checks.
        Any failure before this method returns removes only the package owned by
        this transaction, including failures after the atomic rename.
        """

        validate_identifier(task_id, "task_id")
        _validate_sha256(
            expected_candidate_file_sha256, "expected_candidate_file_sha256"
        )
        _validate_sha256(expected_freeze_file_sha256, "expected_freeze_file_sha256")
        self._validate_expected_size(
            expected_candidate_file_size_bytes,
            "expected_candidate_file_size_bytes",
        )
        self._validate_expected_size(
            expected_freeze_file_size_bytes,
            "expected_freeze_file_size_bytes",
        )

        candidate = self.read_task_card_candidate(task_id)
        frozen = self.read_frozen_task_card(task_id)
        if (
            candidate["file_sha256"] != expected_candidate_file_sha256
            or candidate["file_size_bytes"] != expected_candidate_file_size_bytes
        ):
            raise IntegrityError("candidate descriptor is stale when creating plan run")
        if (
            frozen["file_sha256"] != expected_freeze_file_sha256
            or frozen["file_size_bytes"] != expected_freeze_file_size_bytes
        ):
            raise IntegrityError("freeze descriptor is stale when creating plan run")

        dependency_specs = (
            (
                candidate["relative_path"],
                candidate["file_sha256"],
                candidate["file_size_bytes"],
            ),
            (
                frozen["relative_path"],
                frozen["file_sha256"],
                frozen["file_size_bytes"],
            ),
            (
                frozen["commit_relative_path"],
                frozen["commit_file_sha256"],
                frozen["commit_file_size_bytes"],
            ),
            (
                frozen["candidate_snapshot_relative_path"],
                frozen["candidate_snapshot_sha256"],
                frozen["candidate_snapshot_size_bytes"],
            ),
        )
        dependency_observations = [
            self._observe_immutable_dependency(
                relative,
                expected_sha256=digest,
                expected_size_bytes=size,
            )
            for relative, digest, size in dependency_specs
        ]
        self._assert_dependencies_unchanged(dependency_observations)

        run_id = self._new_run_id()
        validate_identifier(run_id, "run_id")
        if self._is_pending_run_entry(run_id):
            raise ContractError("generated run_id collides with a reserved pending name")
        runs_relative = f"{self.state_relative}/runs"
        final_relative = f"{runs_relative}/{run_id}"
        final_path = self._path(final_relative)
        lock_relative = f"{runs_relative}/{run_id}.pending-lock"
        pending_relative = f"{runs_relative}/{run_id}.pending-{uuid.uuid4().hex}"
        lock_path: Path | None = None
        pending_path: Path | None = None
        owned_lock_identity: tuple[int, int] | None = None
        owned_run_identity: tuple[int, int] | None = None
        written_relatives: list[str] = []
        expected_files: list[dict[str, Any]] = []

        try:
            lock_path = self._mkdir_exclusive(lock_relative)
            owned_lock_identity = _stat_identity(os.lstat(lock_path))
            if os.path.lexists(final_path):
                raise StoreConflictError(
                    f"append-only run already exists: {final_relative}"
                )
            pending_path = self._mkdir_exclusive(pending_relative)
            owned_run_identity = _stat_identity(os.lstat(pending_path))
            events_path = self._mkdir_exclusive(f"{pending_relative}/events")
            artifacts_path = self._mkdir_exclusive(f"{pending_relative}/artifacts")
            directory_identities = {
                "": owned_run_identity,
                "events": _stat_identity(os.lstat(events_path)),
                "artifacts": _stat_identity(os.lstat(artifacts_path)),
            }
            context: dict[str, Any] = {
                "run_id": run_id,
                "task_id": task_id,
                "pending_path": pending_path,
                "final_path": final_path,
            }

            candidate_data = {
                "candidate_record_relpath": candidate["relative_path"],
                "candidate_file_size_bytes": candidate["file_size_bytes"],
                "candidate_file_sha256": candidate["file_sha256"],
                "candidate_record_self_hash_sha256": candidate["record"][
                    _SELF_HASH_KEY
                ],
            }
            first_record, first_bytes = _make_record(
                "generation_run_event",
                task_id=task_id,
                run_id=run_id,
                payload={
                    "event_sequence": 1,
                    "event_type": "task_card_candidate",
                    "previous_event_file_sha256": None,
                    "event_data": candidate_data,
                },
            )
            first = self._write_plan_transaction_record(
                pending_relative,
                "events/000001.json",
                first_bytes,
                expected_type="generation_run_event",
                run_id=run_id,
                task_id=task_id,
            )
            written_relatives.append(f"{pending_relative}/events/000001.json")
            if first["record"] != first_record:
                raise IntegrityError("created event differs from fixed record")
            expected_files.append(first)
            self._verify_plan_transaction_stage(
                "creation",
                context,
                run_base_relative=pending_relative,
                expected_files=expected_files,
                event_count=1,
                artifact_present=False,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )

            freeze_data = {
                "freeze_record_relpath": frozen["relative_path"],
                "freeze_file_size_bytes": frozen["file_size_bytes"],
                "freeze_file_sha256": frozen["file_sha256"],
                "freeze_record_self_hash_sha256": frozen["record"][_SELF_HASH_KEY],
            }
            second_record, second_bytes = _make_record(
                "generation_run_event",
                task_id=task_id,
                run_id=run_id,
                payload={
                    "event_sequence": 2,
                    "event_type": "task_card_frozen",
                    "previous_event_file_sha256": first["file_sha256"],
                    "event_data": freeze_data,
                },
            )
            second = self._write_plan_transaction_record(
                pending_relative,
                "events/000002.json",
                second_bytes,
                expected_type="generation_run_event",
                run_id=run_id,
                task_id=task_id,
            )
            written_relatives.append(f"{pending_relative}/events/000002.json")
            if second["record"] != second_record:
                raise IntegrityError("attached event differs from fixed record")
            expected_files.append(second)
            self._verify_plan_transaction_stage(
                "attach",
                context,
                run_base_relative=pending_relative,
                expected_files=expected_files,
                event_count=2,
                artifact_present=False,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )

            report = evidence_preflight(
                frozen["task_card"], evidence_summaries, provider_status
            )
            third_record, third_bytes = _make_record(
                "generation_run_event",
                task_id=task_id,
                run_id=run_id,
                payload={
                    "event_sequence": 3,
                    "event_type": "evidence_preflight",
                    "previous_event_file_sha256": second["file_sha256"],
                    "event_data": report,
                },
            )
            third = self._write_plan_transaction_record(
                pending_relative,
                "events/000003.json",
                third_bytes,
                expected_type="generation_run_event",
                run_id=run_id,
                task_id=task_id,
            )
            written_relatives.append(f"{pending_relative}/events/000003.json")
            if third["record"] != third_record:
                raise IntegrityError("preflight event differs from fixed record")
            expected_files.append(third)
            self._verify_plan_transaction_stage(
                "preflight",
                context,
                run_base_relative=pending_relative,
                expected_files=expected_files,
                event_count=3,
                artifact_present=False,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )

            plan_payload = self._plan_artifact_payload(
                frozen["task_card"], report, third["file_sha256"]
            )
            plan_record, plan_bytes = _make_record(
                "generation_plan_artifact",
                task_id=task_id,
                run_id=run_id,
                payload=plan_payload,
            )
            plan = self._write_plan_transaction_record(
                pending_relative,
                "artifacts/plan.json",
                plan_bytes,
                expected_type="generation_plan_artifact",
                run_id=run_id,
                task_id=task_id,
            )
            written_relatives.append(f"{pending_relative}/artifacts/plan.json")
            if plan["record"] != plan_record:
                raise IntegrityError("plan artifact differs from fixed record")
            expected_files.append(plan)
            self._verify_plan_transaction_stage(
                "artifact",
                context,
                run_base_relative=pending_relative,
                expected_files=expected_files,
                event_count=3,
                artifact_present=True,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )
            self._verify_plan_transaction_stage(
                "before_rename",
                context,
                run_base_relative=pending_relative,
                expected_files=expected_files,
                event_count=3,
                artifact_present=True,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )
            if os.path.lexists(final_path):
                raise StoreConflictError(
                    f"append-only run already exists: {final_relative}"
                )
            os.rename(pending_path, final_path)
            if (
                not final_path.is_dir()
                or _is_reparse(final_path)
                or _stat_identity(os.lstat(final_path)) != owned_run_identity
            ):
                raise IntegrityError("atomic plan-run rename identity verification failed")
            self._verify_plan_transaction_stage(
                "after_rename",
                context,
                run_base_relative=final_relative,
                expected_files=expected_files,
                event_count=3,
                artifact_present=True,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )
            first_postcommit = self.read_plan_run(
                run_id, expected_task_id=task_id
            )
            self._verify_plan_transaction_stage(
                "postcommit",
                context,
                run_base_relative=final_relative,
                expected_files=expected_files,
                event_count=3,
                artifact_present=True,
                directory_identities=directory_identities,
                dependency_observations=dependency_observations,
            )
            committed = self.read_plan_run(run_id, expected_task_id=task_id)
            if committed != first_postcommit:
                raise IntegrityError("postcommit plan-run verification was not stable")
            os.rmdir(lock_path)
            lock_path = None
            owned_lock_identity = None
            return committed
        except Exception:
            self._rollback_plan_transaction(
                pending_path=pending_path,
                final_path=final_path,
                owned_run_identity=owned_run_identity,
                lock_path=lock_path,
                owned_lock_identity=owned_lock_identity,
            )
            raise
        finally:
            for relative in written_relatives:
                self._exclusive_write_markers.pop(relative, None)
            prefix = f"{pending_relative}/"
            for relative in list(self._exclusive_write_markers):
                if relative.startswith(prefix):
                    self._exclusive_write_markers.pop(relative, None)

    def _head(self, run_id: str, task_id: str, expected_head_event_sha256: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        _validate_sha256(expected_head_event_sha256, "expected_head_event_sha256")
        events = self.read_run_events(run_id, expected_task_id=task_id)
        head = events[-1]
        if head["file_sha256"] != expected_head_event_sha256:
            raise IntegrityError("run head hash is stale")
        return events, head

    def _advance(
        self,
        run_id: str,
        task_id: str,
        event_type: str,
        event_data: Mapping[str, Any],
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        events, head = self._head(run_id, task_id, expected_head_event_sha256)
        next_index = len(events)
        if next_index >= len(_EVENT_SEQUENCE) or _EVENT_SEQUENCE[next_index] != event_type:
            raise StateTransitionError(
                f"illegal transition from {head['record']['payload']['event_type']} to {event_type}"
            )
        return self._write_event(
            run_id,
            task_id,
            event_type,
            event_data,
            head["file_sha256"],
            next_index + 1,
        )

    def attach_frozen_task_card(
        self,
        run_id: str,
        task_id: str,
        expected_freeze_file_sha256: str,
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        _validate_sha256(expected_freeze_file_sha256, "expected_freeze_file_sha256")
        freeze = self.read_frozen_task_card(task_id)
        if freeze["file_sha256"] != expected_freeze_file_sha256:
            raise IntegrityError("freeze hash is stale")
        return self._advance(
            run_id,
            task_id,
            "task_card_frozen",
            {
                "freeze_record_relpath": freeze["relative_path"],
                "freeze_file_size_bytes": freeze["file_size_bytes"],
                "freeze_file_sha256": freeze["file_sha256"],
                "freeze_record_self_hash_sha256": freeze["record"][_SELF_HASH_KEY],
            },
            expected_head_event_sha256,
        )

    def record_evidence_preflight(
        self,
        run_id: str,
        task_id: str,
        evidence_summaries: Any,
        provider_status: Mapping[str, Any],
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        # Use only the content-addressed snapshot committed by the freeze package.
        card = self.read_frozen_task_card(task_id)["task_card"]
        report = evidence_preflight(card, evidence_summaries, provider_status)
        return self._advance(
            run_id,
            task_id,
            "evidence_preflight",
            report,
            expected_head_event_sha256,
        )

    def record_generation_started(
        self,
        run_id: str,
        task_id: str,
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        events, _ = self._head(run_id, task_id, expected_head_event_sha256)
        if len(events) != 3 or events[-1]["record"]["payload"]["event_type"] != "evidence_preflight":
            raise StateTransitionError("generation may start only after evidence preflight")
        report = events[-1]["record"]["payload"]["event_data"]
        if report.get("effective_mode") != "machine_candidate" or report.get("ready_for_machine_candidate") is not True:
            raise StateTransitionError("preflight did not authorize machine-candidate generation")
        provider = report["provider_configuration"]
        return self._advance(
            run_id,
            task_id,
            "generation_started",
            {
                "provider_profile_id": provider["provider_profile_id"],
                "requested_model": provider["requested_model"],
                "requested_reasoning_effort": provider["requested_reasoning_effort"],
                "configuration_status": "requested_configuration",
                "platform_actual_reported": False,
                "signed": False,
            },
            expected_head_event_sha256,
        )

    def record_candidate_generated(
        self,
        run_id: str,
        task_id: str,
        candidate_record_id: str,
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        validate_identifier(candidate_record_id, "candidate_record_id")
        return self._advance(
            run_id,
            task_id,
            "candidate_generated",
            {"candidate_record_id": candidate_record_id, "content_embedded": False},
            expected_head_event_sha256,
        )

    def record_machine_reference_stage(
        self,
        run_id: str,
        task_id: str,
        event_type: str,
        check_record_ids: list[str],
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        if event_type not in _MACHINE_REFERENCE_EVENTS:
            raise ContractError("unsupported machine-reference event type")
        ids = validate_id_sequence(check_record_ids, "check_record_ids")
        return self._advance(
            run_id,
            task_id,
            event_type,
            {
                "check_record_ids": ids,
                "machine_stage_passed": True,
                "authority_scope": "automated_candidate_only",
            },
            expected_head_event_sha256,
        )

    def create_plan_artifact(
        self,
        run_id: str,
        task_id: str,
        expected_head_event_sha256: str,
    ) -> dict[str, Any]:
        events, head = self._head(run_id, task_id, expected_head_event_sha256)
        if head["record"]["payload"]["event_type"] != "evidence_preflight":
            raise StateTransitionError("plan artifact must bind the evidence-preflight head")
        card = self.read_frozen_task_card(task_id)["task_card"]
        report = events[-1]["record"]["payload"]["event_data"]
        payload = self._plan_artifact_payload(card, report, head["file_sha256"])
        record, encoded = _make_record(
            "generation_plan_artifact",
            task_id=task_id,
            run_id=run_id,
            payload=payload,
        )
        relative = f"{self.state_relative}/runs/{run_id}/artifacts/plan.json"
        descriptor = self._write_exclusive(relative, encoded)
        return {"run_id": run_id, "task_id": task_id, "record": record, **descriptor, **_AUTHORITY}

    def read_plan_artifact(self, run_id: str, task_id: str) -> dict[str, Any]:
        validate_identifier(run_id, "run_id")
        validate_identifier(task_id, "task_id")
        relative = f"{self.state_relative}/runs/{run_id}/artifacts/plan.json"
        record, raw = self._read_record(relative, expected_type="generation_plan_artifact")
        if record["run_id"] != run_id or record["task_id"] != task_id:
            raise IntegrityError("plan artifact identity mismatch")
        payload = record["payload"]
        expected_keys = {
            "schema_version",
            "run_head_event_sha256",
            "target_kind",
            "effective_mode",
            "knowledge_scope",
            "ability_scope",
            "theme_count",
            "duration_minutes",
            "total_score",
            "difficulty_targets",
            "paper_structure",
            "numbering_rules",
            "selection_scoring_rules",
            "prohibited_content",
            "figure_types",
            "evidence_record_ids",
            "blockers",
            "contains_generated_questions",
            "next_state_requires_new_verified_records",
        }
        if type(payload) is not dict or set(payload) != expected_keys:
            raise IntegrityError("plan artifact payload keys are invalid")
        if payload["schema_version"] != "generation_plan_artifact_v1":
            raise IntegrityError("plan artifact version mismatch")
        if payload["contains_generated_questions"] is not False:
            raise IntegrityError("plan artifact contains unauthorized generated content")
        if payload["next_state_requires_new_verified_records"] is not True:
            raise IntegrityError("plan artifact attempts to bypass new-record gates")
        events = self.read_run_events(run_id, expected_task_id=task_id)
        matching = [
            event
            for event in events
            if event["file_sha256"] == payload["run_head_event_sha256"]
            and event["record"]["payload"]["event_type"] == "evidence_preflight"
        ]
        if len(matching) != 1:
            raise IntegrityError("plan artifact preflight-head binding failed")
        return {
            "run_id": run_id,
            "task_id": task_id,
            "record": record,
            "relative_path": relative,
            "file_size_bytes": len(raw),
            "file_sha256": _sha256(raw),
            **_AUTHORITY,
        }

    def read_plan_run(
        self, run_id: str, *, expected_task_id: str | None = None
    ) -> dict[str, Any]:
        """Read one fully committed plan run; partial finals fail closed."""

        validate_identifier(run_id, "run_id")
        if self._is_pending_run_entry(run_id):
            raise IntegrityError("pending run transactions are not readable")
        if expected_task_id is not None:
            validate_identifier(expected_task_id, "expected_task_id")
        run_relative = f"{self.state_relative}/runs/{run_id}"
        run_path = self._path(run_relative)
        if not run_path.is_dir() or _is_reparse(run_path):
            raise IntegrityError("committed plan run is missing or unsafe")
        root_before = os.lstat(run_path)
        events_dir = run_path / "events"
        artifacts_dir = run_path / "artifacts"
        for directory in (events_dir, artifacts_dir):
            if not directory.is_dir() or _is_reparse(directory):
                raise IntegrityError("committed plan run directory is incomplete")
        events_before = os.lstat(events_dir)
        artifacts_before = os.lstat(artifacts_dir)
        if sorted(item.name for item in run_path.iterdir()) != ["artifacts", "events"]:
            raise IntegrityError("committed plan run root entries are not exact")
        event_entries = list(events_dir.iterdir())
        expected_event_names = ["000001.json", "000002.json", "000003.json"]
        if sorted(item.name for item in event_entries) != expected_event_names or any(
            not item.is_file() or _is_reparse(item) for item in event_entries
        ):
            raise IntegrityError("committed plan run events are incomplete or non-exact")
        artifact_entries = list(artifacts_dir.iterdir())
        if sorted(item.name for item in artifact_entries) != ["plan.json"] or any(
            not item.is_file() or _is_reparse(item) for item in artifact_entries
        ):
            raise IntegrityError("committed plan run artifacts are incomplete or non-exact")

        events = self._read_run_events_at(
            run_id,
            run_relative,
            expected_task_id=expected_task_id,
        )
        if [event["record"]["payload"]["event_type"] for event in events] != [
            "task_card_candidate",
            "task_card_frozen",
            "evidence_preflight",
        ]:
            raise IntegrityError("committed plan run event sequence is not exact")
        task_id = events[0]["task_id"]
        artifact = self.read_plan_artifact(run_id, task_id)

        if (
            _stat_marker(os.lstat(run_path)) != _stat_marker(root_before)
            or _stat_marker(os.lstat(events_dir)) != _stat_marker(events_before)
            or _stat_marker(os.lstat(artifacts_dir)) != _stat_marker(artifacts_before)
            or sorted(item.name for item in run_path.iterdir())
            != ["artifacts", "events"]
            or sorted(item.name for item in events_dir.iterdir())
            != expected_event_names
            or sorted(item.name for item in artifacts_dir.iterdir()) != ["plan.json"]
        ):
            raise IntegrityError("committed plan run drifted while being read")
        return {
            "run_id": run_id,
            "task_id": task_id,
            "events": events,
            "artifact": artifact,
            **_AUTHORITY,
        }

    def list_plan_runs(self) -> list[dict[str, Any]]:
        """List committed plan runs, ignoring uncommitted pending packages."""

        runs_root = self._path(f"{self.state_relative}/runs")
        if not runs_root.is_dir() or _is_reparse(runs_root):
            raise IntegrityError("runs root is missing or unsafe")
        output: list[dict[str, Any]] = []
        for entry in sorted(runs_root.iterdir(), key=lambda item: item.name):
            if self._is_pending_run_entry(entry.name):
                continue
            if not entry.is_dir() or _is_reparse(entry):
                raise IntegrityError("unexpected committed-runs entry")
            output.append(self.read_plan_run(entry.name))
        return output
