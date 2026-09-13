"""Workspace-confined append-only storage for candidate-only tag patches."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .contracts import (
    PATCH_SCHEMA_VERSION,
    canonical_json_bytes,
    parse_json_object,
    require_canonical_object,
    validate_identifier,
    validate_patch_request,
    validate_sha256,
)
from .errors import ContractError, IntegrityError, StoreConflictError

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SELF_HASH_KEY = "self_hash_sha256"
_PRODUCTION_STATE_RELATIVE = (
    "staging/coordination/tagging_workbench/candidate_store_v1"
)
_TEST_STATE_RELATIVE = ".tagging-workbench-test-candidates-v1"
_AUTHORITY_FLAG_KEYS = frozenset(
    {
        "candidate_only",
        "human_reviewed",
        "retrieval_ready",
        "generation_allowed",
        "publication_allowed",
        "official",
    }
)
_PROTECTED_WORKSPACE_COMPONENTS = frozenset(
    {"sh-chem-db", "kb", "central", "central-kb", "central_kb"}
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _stat_is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _stat_marker(info: os.stat_result) -> tuple[int, ...]:
    # Some Windows filesystems briefly expose ctime just before the final
    # mtime on a new file, then converge them.  max() normalizes only that
    # benign direction; a later rewrite still advances ctime and is detected.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        max(info.st_ctime_ns, info.st_mtime_ns),
    )


def _stat_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def validate_relative_path(value: Any) -> str:
    """Accept only canonical POSIX relative paths confined below the workspace."""

    if type(value) is not str or not value or len(value) > 2048:
        raise ContractError("relative path must be a non-empty bounded string")
    if "\\" in value or "\x00" in value or ":" in value:
        raise ContractError("backslash, NUL, drive, URI, and stream syntax is forbidden")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise ContractError("path must be canonical and relative")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise ContractError("path must use canonical POSIX relative form")
    if not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ContractError("path traversal and empty components are forbidden")
    return value


def _assert_existing_chain_no_reparse(path: Path, name: str) -> None:
    for component in reversed((path, *path.parents)):
        if not os.path.lexists(component):
            raise ContractError(f"{name} has a missing lexical path component")
        try:
            info = os.lstat(component)
        except OSError as exc:
            raise ContractError(f"{name} path component cannot be lstat-verified") from exc
        if _stat_is_reparse(info):
            raise ContractError(f"{name} path chain contains a symlink/reparse point")


def _self_hashed(value: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    record = copy.deepcopy(dict(value))
    record[_SELF_HASH_KEY] = None
    record[_SELF_HASH_KEY] = _sha256(canonical_json_bytes(record))
    return record, canonical_json_bytes(record)


def _verify_self_hash(value: Any, expected_keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected_keys:
        raise IntegrityError(f"{name} keys are not exact")
    supplied = value.get(_SELF_HASH_KEY)
    if type(supplied) is not str or not _HASH_RE.fullmatch(supplied):
        raise IntegrityError(f"{name} self hash is invalid")
    unsigned = copy.deepcopy(value)
    unsigned[_SELF_HASH_KEY] = None
    if _sha256(canonical_json_bytes(unsigned)) != supplied:
        raise IntegrityError(f"{name} self hash mismatch")
    return copy.deepcopy(value)


def _flags_are_closed(value: Mapping[str, Any], name: str) -> None:
    # Fixed literals are intentionally independent of the public
    # contracts.AUTHORITY_FLAGS introspection alias.
    if value.get("candidate_only") is not True:
        raise IntegrityError(f"{name} attempted to elevate candidate_only")
    for key in (
        "human_reviewed",
        "retrieval_ready",
        "generation_allowed",
        "publication_allowed",
        "official",
    ):
        if value.get(key) is not False:
            raise IntegrityError(f"{name} attempted to elevate {key}")


def _closed_authority_flags() -> dict[str, bool]:
    """Return fresh fixed candidate-only flags for every emitted object."""

    return {
        "candidate_only": True,
        "human_reviewed": False,
        "retrieval_ready": False,
        "generation_allowed": False,
        "publication_allowed": False,
        "official": False,
    }


def _is_protected_workspace_path(path: Path) -> bool:
    for component in path.parts:
        lowered = component.casefold().rstrip(" .")
        if (
            lowered in _PROTECTED_WORKSPACE_COMPONENTS
            or lowered.startswith(
                ("live", "private", ".private", "central-kb", "central_kb", "centralkb")
            )
        ):
            return True
    return False


class AppendOnlyTagPatchStore:
    """Append-only, content-bound candidate patch store.

    The store accepts caller-loaded bytes only.  It never accepts a source path,
    never opens the central master index, and exposes no apply/promote/publish
    operation.  A final package directory is exclusively reserved before any
    record is written; ``commit.json`` is written last and every failure before
    return rolls the uncommitted directory back without following reparse points.
    """

    def __init__(
        self,
        workspace_root: Path,
        state_root: str | None = None,
        *,
        _test_only: bool = False,
    ):
        if state_root is not None:
            raise ContractError(
                "state_root is code-derived; caller-selected state paths are forbidden"
            )
        raw_root = Path(workspace_root)
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
        if _is_protected_workspace_path(resolved_root):
            raise ContractError(
                "workspace_root must not be inside central-KB/live/private namespaces"
            )

        if _test_only:
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                resolved_root.relative_to(temp_root)
            except ValueError as exc:
                raise ContractError(
                    "test-only workspace_root must be an explicit temporary directory"
                ) from exc
            if resolved_root == temp_root:
                raise ContractError(
                    "test-only workspace_root must be a dedicated child temporary directory"
                )

        self.workspace_root = resolved_root
        self.state_relative = validate_relative_path(
            _TEST_STATE_RELATIVE if _test_only else _PRODUCTION_STATE_RELATIVE
        )
        self.state_root = self._ensure_directory_tree(self.state_relative)
        self.patches_relative = f"{self.state_relative}/patches"
        self._ensure_directory_tree(self.patches_relative)

    @classmethod
    def for_test_workspace(cls, workspace_root: Path) -> AppendOnlyTagPatchStore:
        """Create a store only in a dedicated OS-temporary test workspace."""

        return cls(workspace_root, _test_only=True)

    def _ensure_directory_tree(self, relative: str) -> Path:
        relative = validate_relative_path(relative)
        current = self.workspace_root
        for component in PurePosixPath(relative).parts:
            current = current / component
            try:
                os.mkdir(current, mode=0o700)
            except FileExistsError:
                try:
                    info = os.lstat(current)
                except OSError as exc:
                    raise ContractError("store path cannot be lstat-verified") from exc
                if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise ContractError("store path collides with an unsafe non-directory")
            info = os.lstat(current)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ContractError("reparse points are forbidden in store paths")
        return current

    def _path(self, relative: str, *, require_file: bool = False) -> Path:
        relative = validate_relative_path(relative)
        path = self.workspace_root.joinpath(*PurePosixPath(relative).parts)
        current = self.workspace_root
        for component in PurePosixPath(relative).parts:
            current = current / component
            if not os.path.lexists(current):
                break
            info = os.lstat(current)
            if _stat_is_reparse(info):
                raise IntegrityError("reparse point encountered in store path")
        if require_file:
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise IntegrityError(f"immutable record is missing: {relative}") from exc
            if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode):
                raise IntegrityError(f"immutable record is not a regular file: {relative}")
        return path

    def _mkdir_exclusive(self, relative: str) -> Path:
        path = self._path(relative)
        parent_info = os.lstat(path.parent)
        if _stat_is_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise IntegrityError("exclusive directory parent is unsafe")
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError as exc:
            raise StoreConflictError(
                f"append-only patch already exists: {PurePosixPath(relative).name}"
            ) from exc
        return path

    def _mkdir_owned(self, relative: str) -> Path:
        path = self._path(relative)
        parent_info = os.lstat(path.parent)
        if _stat_is_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise IntegrityError("package directory parent is unsafe")
        try:
            os.mkdir(path, mode=0o700)
        except FileExistsError as exc:
            raise IntegrityError("new package unexpectedly contains an existing directory") from exc
        return path

    def _write_exclusive(self, relative: str, data: bytes) -> dict[str, Any]:
        if type(data) is not bytes:
            raise ContractError("immutable write payload must be bytes")
        path = self._path(relative)
        parent = path.parent
        parent_info = os.lstat(parent)
        if _stat_is_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise IntegrityError("immutable record parent is unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise StoreConflictError(f"append-only record already exists: {relative}") from exc
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            # Caller owns rollback of the entire uncommitted package.
            raise
        # Reopen/read once before freezing the marker.  On Windows this also
        # flushes the metadata view after the just-closed writer handle, avoiding
        # a stale ctime observation while retaining ctime as an ABA signal.
        observed_raw, _ = self._read_stable_bytes(relative)
        if observed_raw != data:
            raise IntegrityError("exclusive write readback mismatch")
        info = os.lstat(path)
        marker = _stat_marker(info)
        if _stat_is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise IntegrityError("exclusive write did not produce a regular file")
        return {
            "relative_path": relative,
            "size_bytes": len(data),
            "sha256": _sha256(data),
            "marker": marker,
            "data": data,
        }

    @staticmethod
    def _read_descriptor_all(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _read_stable_bytes(self, relative: str) -> tuple[bytes, tuple[int, ...]]:
        path = self._path(relative, require_file=True)
        before = os.lstat(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise IntegrityError("immutable record could not be opened safely") from exc
        try:
            opened = os.fstat(descriptor)
            if _stat_identity(before) != _stat_identity(opened):
                raise IntegrityError("immutable record changed between lstat and open")
            raw = self._read_descriptor_all(descriptor)
            after = os.fstat(descriptor)
            path_after = os.lstat(path)
        finally:
            os.close(descriptor)
        if (
            _stat_marker(opened) != _stat_marker(after)
            or _stat_identity(opened) != _stat_identity(path_after)
            or _stat_is_reparse(path_after)
            or len(raw) != after.st_size
        ):
            raise IntegrityError("immutable record drifted while being read")
        return raw, _stat_marker(after)

    def _assert_write_unchanged(self, observation: Mapping[str, Any]) -> None:
        raw, marker = self._read_stable_bytes(observation["relative_path"])
        if marker != observation["marker"] or raw != observation["data"]:
            raise IntegrityError("same-size, ABA, or post-commit package drift detected")

    def _remove_uncommitted_tree(self, root: Path) -> None:
        """Rollback one exclusively owned package without following links."""

        try:
            root_info = os.lstat(root)
        except FileNotFoundError:
            return
        if _stat_is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            # Never follow or recursively delete an attacker-replaced root.
            raise IntegrityError("cannot safely roll back replaced package root")
        for entry in os.scandir(root):
            child = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(info):
                try:
                    os.unlink(child)
                except IsADirectoryError:
                    os.rmdir(child)
            elif stat.S_ISDIR(info.st_mode):
                self._remove_uncommitted_tree(child)
            else:
                os.unlink(child)
        os.rmdir(root)

    @staticmethod
    def _request_bytes(request: Mapping[str, Any] | bytes) -> tuple[dict[str, Any], bytes]:
        if type(request) is bytes:
            value = require_canonical_object(request, "patch_request")
            return value, request
        if type(request) is not dict:
            raise ContractError("patch_request must be an object or canonical bytes")
        raw = canonical_json_bytes(request)
        return parse_json_object(raw), raw

    @staticmethod
    def _evidence_bindings(
        evidence_ids: list[str],
        evidence_snapshots: Mapping[str, bytes] | None,
        evidence_hash_bindings: Mapping[str, str] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        snapshots = {} if evidence_snapshots is None else dict(evidence_snapshots)
        hashes = {} if evidence_hash_bindings is None else dict(evidence_hash_bindings)
        expected = set(evidence_ids)
        if set(snapshots) | set(hashes) != expected or set(snapshots) & set(hashes):
            raise ContractError(
                "each evidence_binding_id needs exactly one caller-loaded snapshot or exact hash"
            )
        for binding_id in [*snapshots, *hashes]:
            validate_identifier(binding_id, "evidence binding key")
        output: list[dict[str, Any]] = []
        raw_by_id: dict[str, bytes] = {}
        for binding_id in evidence_ids:
            if binding_id in snapshots:
                raw = snapshots[binding_id]
                if type(raw) is not bytes or not raw or len(raw) > 32_000_000:
                    raise ContractError("evidence snapshot must be non-empty immutable bytes")
                digest = _sha256(raw)
                raw_by_id[binding_id] = raw
                output.append(
                    {
                        "evidence_binding_id": binding_id,
                        "binding_mode": "content_addressed_snapshot",
                        "snapshot_relative_path": None,
                        "snapshot_size_bytes": len(raw),
                        "snapshot_sha256": digest,
                    }
                )
            else:
                digest = validate_sha256(
                    hashes[binding_id], f"evidence_hash_bindings.{binding_id}"
                )
                output.append(
                    {
                        "evidence_binding_id": binding_id,
                        "binding_mode": "exact_hash_only",
                        "snapshot_relative_path": None,
                        "snapshot_size_bytes": None,
                        "snapshot_sha256": digest,
                    }
                )
        return output, raw_by_id

    @staticmethod
    def _snapshot_descriptor(role: str, relative: str, raw: bytes) -> dict[str, Any]:
        return {
            "role": role,
            "relative_path": relative,
            "size_bytes": len(raw),
            "sha256": _sha256(raw),
        }

    def create_candidate_patch(
        self,
        request: Mapping[str, Any] | bytes,
        *,
        base_index_manifest_raw: bytes,
        target_record_raw: bytes,
        taxonomy_raw: bytes,
        evidence_snapshots: Mapping[str, bytes] | None = None,
        evidence_hash_bindings: Mapping[str, str] | None = None,
        source_snapshots: Mapping[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Commit one immutable candidate patch; never mutate its target.

        ``source_snapshots`` is a descriptive alias for ``evidence_snapshots``;
        callers must not supply both.
        """

        if source_snapshots is not None:
            if evidence_snapshots is not None:
                raise ContractError("supply evidence_snapshots or source_snapshots, not both")
            evidence_snapshots = source_snapshots
        request_value, request_raw = self._request_bytes(request)
        normalized = validate_patch_request(
            request_value,
            base_index_manifest_raw=base_index_manifest_raw,
            target_record_raw=target_record_raw,
            taxonomy_raw=taxonomy_raw,
        )
        evidence_bindings, evidence_raw = self._evidence_bindings(
            normalized["evidence_binding_ids"],
            evidence_snapshots,
            evidence_hash_bindings,
        )

        identity_core = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "request": normalized,
            "request_raw_sha256": _sha256(request_raw),
            "evidence_bindings": [
                {
                    "evidence_binding_id": row["evidence_binding_id"],
                    "binding_mode": row["binding_mode"],
                    "snapshot_sha256": row["snapshot_sha256"],
                }
                for row in evidence_bindings
            ],
        }
        patch_id = f"TAGPATCH-{_sha256(canonical_json_bytes(identity_core))}"
        validate_identifier(patch_id, "patch_id")
        patch_relative = f"{self.patches_relative}/{patch_id}"
        patch_root = self._mkdir_exclusive(patch_relative)
        observations: list[dict[str, Any]] = []
        committed = False
        try:
            for directory in (
                "snapshots",
                "snapshots/base_index_manifest",
                "snapshots/target_record",
                "snapshots/taxonomy",
                "snapshots/evidence",
            ):
                self._mkdir_owned(f"{patch_relative}/{directory}")

            request_relative = f"{patch_relative}/request.json"
            request_descriptor = self._snapshot_descriptor(
                "patch_request", request_relative, request_raw
            )
            observations.append(self._write_exclusive(request_relative, request_raw))

            snapshot_inputs = {
                "base_index_manifest": base_index_manifest_raw,
                "target_record": target_record_raw,
                "taxonomy": taxonomy_raw,
            }
            bound_snapshots: list[dict[str, Any]] = []
            for role, raw in snapshot_inputs.items():
                digest = _sha256(raw)
                relative = f"{patch_relative}/snapshots/{role}/sha256-{digest}.json"
                descriptor = self._snapshot_descriptor(role, relative, raw)
                bound_snapshots.append(descriptor)
                observations.append(self._write_exclusive(relative, raw))

            for row in evidence_bindings:
                if row["binding_mode"] != "content_addressed_snapshot":
                    continue
                binding_id = row["evidence_binding_id"]
                raw = evidence_raw[binding_id]
                self._mkdir_owned(f"{patch_relative}/snapshots/evidence/{binding_id}")
                relative = (
                    f"{patch_relative}/snapshots/evidence/{binding_id}/"
                    f"sha256-{row['snapshot_sha256']}.snapshot"
                )
                row["snapshot_relative_path"] = relative
                observations.append(self._write_exclusive(relative, raw))

            patch_record_base: dict[str, Any] = {
                "record_schema_version": PATCH_SCHEMA_VERSION,
                "record_type": "atomic_part_tag_patch_candidate",
                "patch_id": patch_id,
                "created_at_utc": _utc_now(),
                "node_type": normalized["node_type"],
                "node_id": normalized["node_id"],
                "paper_id": normalized["paper_id"],
                "base_index_manifest_id": normalized["base_index_manifest_id"],
                "base_index_manifest_size_bytes": normalized[
                    "base_index_manifest_size_bytes"
                ],
                "base_node_kind": normalized["base_node_kind"],
                "base_index_manifest_sha256": normalized[
                    "base_index_manifest_sha256"
                ],
                "target_record_sha256": normalized["target_record_sha256"],
                "target_record_size_bytes": normalized["target_record_size_bytes"],
                "taxonomy_sha256": normalized["taxonomy_sha256"],
                "changes": copy.deepcopy(normalized["changes"]),
                "reason": normalized["reason"],
                "evidence_binding_ids": copy.deepcopy(
                    normalized["evidence_binding_ids"]
                ),
                "request_snapshot": request_descriptor,
                "bound_snapshots": bound_snapshots,
                "evidence_bindings": copy.deepcopy(evidence_bindings),
                **_closed_authority_flags(),
            }
            patch_record, patch_raw = _self_hashed(patch_record_base)
            patch_record_relative = f"{patch_relative}/patch.json"
            observations.append(self._write_exclusive(patch_record_relative, patch_raw))

            commit_files = [
                {
                    "relative_path": item["relative_path"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                }
                for item in observations
            ]
            commit_base = {
                "record_schema_version": "shchem_tag_patch_commit_v1",
                "record_type": "atomic_part_tag_patch_commit_marker",
                "patch_id": patch_id,
                "patch_record_relative_path": patch_record_relative,
                "patch_record_sha256": _sha256(patch_raw),
                "package_file_count_excluding_commit": len(commit_files),
                "files": commit_files,
                **_closed_authority_flags(),
            }
            commit_record, commit_raw = _self_hashed(commit_base)
            commit_relative = f"{patch_relative}/commit.json"
            commit_observation = self._write_exclusive(commit_relative, commit_raw)
            observations.append(commit_observation)

            # Recheck original caller bytes, every stored copy, and commit marker.
            # Exact bytes (not size or parsed equality) are the concurrency unit.
            if (
                _sha256(base_index_manifest_raw)
                != normalized["base_index_manifest_sha256"]
                or _sha256(target_record_raw) != normalized["target_record_sha256"]
                or _sha256(taxonomy_raw) != normalized["taxonomy_sha256"]
            ):
                raise StoreConflictError("caller snapshot drifted before commit return")
            for observation in observations:
                self._assert_write_unchanged(observation)
            verified = self.get_patch(patch_id)
            committed = True
            return verified
        except Exception:
            if not committed:
                self._remove_uncommitted_tree(patch_root)
            raise

    # Concise alias for callers that already use candidate-only terminology.
    create_patch = create_candidate_patch

    @staticmethod
    def _parse_canonical_record(raw: bytes, name: str) -> dict[str, Any]:
        try:
            value = parse_json_object(raw)
            if canonical_json_bytes(value) != raw:
                raise IntegrityError(f"{name} bytes are not canonical JSON")
            return value
        except ContractError as exc:
            raise IntegrityError(f"{name} is not strict JSON: {exc}") from exc

    def _verify_descriptor(self, descriptor: Any, expected_role: str | None = None) -> bytes:
        keys = {"role", "relative_path", "size_bytes", "sha256"}
        if type(descriptor) is not dict or set(descriptor) != keys:
            raise IntegrityError("snapshot descriptor keys are invalid")
        if expected_role is not None and descriptor["role"] != expected_role:
            raise IntegrityError("snapshot descriptor role mismatch")
        validate_relative_path(descriptor["relative_path"])
        validate_sha256(descriptor["sha256"], "snapshot descriptor sha256")
        if type(descriptor["size_bytes"]) is not int or descriptor["size_bytes"] < 0:
            raise IntegrityError("snapshot descriptor size is invalid")
        raw, _ = self._read_stable_bytes(descriptor["relative_path"])
        if len(raw) != descriptor["size_bytes"] or _sha256(raw) != descriptor["sha256"]:
            raise IntegrityError("content-addressed snapshot bytes/hash binding failed")
        return raw

    def _safe_file_inventory(self, patch_relative: str) -> set[str]:
        patch_root = self._path(patch_relative)
        try:
            root_info = os.lstat(patch_root)
        except OSError as exc:
            raise IntegrityError("patch directory is missing") from exc
        if _stat_is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise IntegrityError("patch root is unsafe")
        files: set[str] = set()

        def visit(directory: Path) -> None:
            for entry in os.scandir(directory):
                info = entry.stat(follow_symlinks=False)
                if _stat_is_reparse(info):
                    raise IntegrityError("patch package contains a reparse point")
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    files.add(path.relative_to(self.workspace_root).as_posix())
                else:
                    raise IntegrityError("patch package contains a non-regular entry")

        visit(patch_root)
        return files

    def get_patch(self, patch_id: str) -> dict[str, Any]:
        """Read and fully verify one committed candidate package."""

        validate_identifier(patch_id, "patch_id")
        if not patch_id.startswith("TAGPATCH-"):
            raise ContractError("patch_id is outside the tagging workbench namespace")
        patch_relative = f"{self.patches_relative}/{patch_id}"
        commit_relative = f"{patch_relative}/commit.json"
        commit_raw, _ = self._read_stable_bytes(commit_relative)
        commit = self._parse_canonical_record(commit_raw, "commit marker")
        commit_keys = {
            "record_schema_version",
            "record_type",
            "patch_id",
            "patch_record_relative_path",
            "patch_record_sha256",
            "package_file_count_excluding_commit",
            "files",
            *_AUTHORITY_FLAG_KEYS,
            _SELF_HASH_KEY,
        }
        commit = _verify_self_hash(commit, commit_keys, "commit marker")
        _flags_are_closed(commit, "commit marker")
        if (
            commit["record_schema_version"] != "shchem_tag_patch_commit_v1"
            or commit["record_type"] != "atomic_part_tag_patch_commit_marker"
            or commit["patch_id"] != patch_id
        ):
            raise IntegrityError("commit marker identity is invalid")
        files = commit["files"]
        if type(files) is not list or not files:
            raise IntegrityError("commit marker file manifest is invalid")
        if commit["package_file_count_excluding_commit"] != len(files):
            raise IntegrityError("commit marker file count mismatch")
        file_map: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(files):
            if type(row) is not dict or set(row) != {"relative_path", "size_bytes", "sha256"}:
                raise IntegrityError(f"commit marker file row {index} is invalid")
            relative = validate_relative_path(row["relative_path"])
            if not relative.startswith(f"{patch_relative}/") or relative == commit_relative:
                raise IntegrityError("commit marker file escapes or includes itself")
            if relative in file_map:
                raise IntegrityError("commit marker contains duplicate file paths")
            validate_sha256(row["sha256"], "commit file sha256")
            if type(row["size_bytes"]) is not int or row["size_bytes"] < 0:
                raise IntegrityError("commit file size is invalid")
            raw, _ = self._read_stable_bytes(relative)
            if len(raw) != row["size_bytes"] or _sha256(raw) != row["sha256"]:
                raise IntegrityError("commit-bound file bytes/hash mismatch")
            file_map[relative] = row

        actual_files = self._safe_file_inventory(patch_relative)
        if actual_files != set(file_map) | {commit_relative}:
            raise IntegrityError("patch package contains missing or uncommitted extra files")

        patch_record_relative = commit["patch_record_relative_path"]
        expected_patch_relative = f"{patch_relative}/patch.json"
        if patch_record_relative != expected_patch_relative:
            raise IntegrityError("commit marker patch path is invalid")
        patch_raw, _ = self._read_stable_bytes(patch_record_relative)
        if _sha256(patch_raw) != commit["patch_record_sha256"]:
            raise IntegrityError("commit marker patch hash mismatch")
        patch = self._parse_canonical_record(patch_raw, "patch record")
        patch_keys = {
            "record_schema_version",
            "record_type",
            "patch_id",
            "created_at_utc",
            "node_type",
            "node_id",
            "paper_id",
            "base_index_manifest_id",
            "base_index_manifest_size_bytes",
            "base_node_kind",
            "base_index_manifest_sha256",
            "target_record_sha256",
            "target_record_size_bytes",
            "taxonomy_sha256",
            "changes",
            "reason",
            "evidence_binding_ids",
            "request_snapshot",
            "bound_snapshots",
            "evidence_bindings",
            *_AUTHORITY_FLAG_KEYS,
            _SELF_HASH_KEY,
        }
        patch = _verify_self_hash(patch, patch_keys, "patch record")
        _flags_are_closed(patch, "patch record")
        if (
            patch["record_schema_version"] != PATCH_SCHEMA_VERSION
            or patch["record_type"] != "atomic_part_tag_patch_candidate"
            or patch["patch_id"] != patch_id
            or patch["node_type"] != "atomic_part"
        ):
            raise IntegrityError("patch record identity is invalid")

        request_raw = self._verify_descriptor(
            patch["request_snapshot"], expected_role="patch_request"
        )
        bound = patch["bound_snapshots"]
        if type(bound) is not list or len(bound) != 3:
            raise IntegrityError("patch must bind exactly three control snapshots")
        bound_by_role: dict[str, bytes] = {}
        for descriptor in bound:
            if type(descriptor) is not dict:
                raise IntegrityError("bound snapshot descriptor is invalid")
            role = descriptor.get("role")
            if role not in {"base_index_manifest", "target_record", "taxonomy"}:
                raise IntegrityError("unknown bound control snapshot role")
            if role in bound_by_role:
                raise IntegrityError("duplicate bound control snapshot role")
            bound_by_role[role] = self._verify_descriptor(descriptor, expected_role=role)

        try:
            request_value = require_canonical_object(request_raw, "stored patch request")
            normalized = validate_patch_request(
                request_value,
                base_index_manifest_raw=bound_by_role["base_index_manifest"],
                target_record_raw=bound_by_role["target_record"],
                taxonomy_raw=bound_by_role["taxonomy"],
            )
        except StoreConflictError as exc:
            raise IntegrityError(f"stored optimistic-concurrency binding failed: {exc}") from exc
        except ContractError as exc:
            raise IntegrityError(f"stored request contract failed: {exc}") from exc
        for key in (
            "node_type",
            "node_id",
            "paper_id",
            "base_index_manifest_id",
            "base_index_manifest_size_bytes",
            "base_node_kind",
            "base_index_manifest_sha256",
            "target_record_sha256",
            "target_record_size_bytes",
            "taxonomy_sha256",
            "changes",
            "reason",
            "evidence_binding_ids",
        ):
            if patch[key] != normalized[key]:
                raise IntegrityError(f"patch/request binding mismatch: {key}")

        evidence = patch["evidence_bindings"]
        if type(evidence) is not list or len(evidence) != len(patch["evidence_binding_ids"]):
            raise IntegrityError("evidence binding list length mismatch")
        evidence_keys = {
            "evidence_binding_id",
            "binding_mode",
            "snapshot_relative_path",
            "snapshot_size_bytes",
            "snapshot_sha256",
        }
        for binding_id, row in zip(patch["evidence_binding_ids"], evidence, strict=True):
            if type(row) is not dict or set(row) != evidence_keys:
                raise IntegrityError("evidence binding row keys are invalid")
            if row["evidence_binding_id"] != binding_id:
                raise IntegrityError("evidence binding order/identity mismatch")
            validate_identifier(binding_id, "stored evidence binding ID")
            validate_sha256(row["snapshot_sha256"], "stored evidence snapshot sha256")
            if row["binding_mode"] == "content_addressed_snapshot":
                relative = validate_relative_path(row["snapshot_relative_path"])
                expected_prefix = f"{patch_relative}/snapshots/evidence/{binding_id}/"
                if not relative.startswith(expected_prefix):
                    raise IntegrityError("evidence snapshot path is not ID-confined")
                raw, _ = self._read_stable_bytes(relative)
                if (
                    type(row["snapshot_size_bytes"]) is not int
                    or len(raw) != row["snapshot_size_bytes"]
                    or _sha256(raw) != row["snapshot_sha256"]
                ):
                    raise IntegrityError("evidence source snapshot was tampered")
            elif row["binding_mode"] == "exact_hash_only":
                if row["snapshot_relative_path"] is not None or row["snapshot_size_bytes"] is not None:
                    raise IntegrityError("hash-only evidence binding contains a fake snapshot")
            else:
                raise IntegrityError("evidence binding mode is invalid")

        # Recompute deterministic identity; timestamps and authority wrappers do
        # not influence ID, so concurrent identical requests have one winner.
        identity_core = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "request": normalized,
            "request_raw_sha256": _sha256(request_raw),
            "evidence_bindings": [
                {
                    "evidence_binding_id": row["evidence_binding_id"],
                    "binding_mode": row["binding_mode"],
                    "snapshot_sha256": row["snapshot_sha256"],
                }
                for row in evidence
            ],
        }
        if patch_id != f"TAGPATCH-{_sha256(canonical_json_bytes(identity_core))}":
            raise IntegrityError("patch content-addressed ID mismatch")
        return {
            "patch_id": patch_id,
            "record": patch,
            "commit_record": commit,
            "relative_path": patch_record_relative,
            "file_size_bytes": len(patch_raw),
            "file_sha256": _sha256(patch_raw),
            "commit_relative_path": commit_relative,
            "commit_file_size_bytes": len(commit_raw),
            "commit_file_sha256": _sha256(commit_raw),
            **_closed_authority_flags(),
        }

    read_patch = get_patch

    def list_patches(self) -> list[dict[str, Any]]:
        """Return verified summaries only; this method cannot mutate state."""

        patches_root = self._path(self.patches_relative)
        root_info = os.lstat(patches_root)
        if _stat_is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise IntegrityError("patches root is unsafe")
        output: list[dict[str, Any]] = []
        for entry in sorted(os.scandir(patches_root), key=lambda item: item.name):
            info = entry.stat(follow_symlinks=False)
            if _stat_is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise IntegrityError("patches root contains an unsafe entry")
            verified = self.get_patch(entry.name)
            record = verified["record"]
            output.append(
                {
                    "patch_id": verified["patch_id"],
                    "node_type": record["node_type"],
                    "node_id": record["node_id"],
                    "created_at_utc": record["created_at_utc"],
                    "changed_fields": sorted(record["changes"]),
                    "file_sha256": verified["file_sha256"],
                    **_closed_authority_flags(),
                }
            )
        return output

    def status(self) -> dict[str, Any]:
        """Read-only integrity status with permanently closed authority gates."""

        rows = self.list_patches()
        return {
            "schema_version": "shchem_tagging_workbench_status_v1",
            "store_mode": "append_only_candidate_patch_no_apply_interface",
            "valid_committed_patch_count": len(rows),
            "patch_ids": [row["patch_id"] for row in rows],
            "integrity_ok": True,
            **_closed_authority_flags(),
        }
