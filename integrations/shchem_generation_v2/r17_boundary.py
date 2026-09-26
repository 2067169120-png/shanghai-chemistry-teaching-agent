"""R17 integration/publication trust boundary.

The controller owns the R17 schemas and root attestations.  This module is a
strict consumer: it never creates a root attestation, cryptographic proof, or
positive publication authority.  Every referenced file is opened once per
validation phase; the same immutable byte snapshot supplies its length, hash,
and JSON parse.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

R17_SCHEMA_VERSION = "3.0.0-r17"
FORMAL_SCHEMA_RELATIVE = Path(
    "sh-chem-db/tests/generation_publication_v2/schemas/formal_freeze_receipt_r17.schema.json"
)
FORMAL_SIDECAR_SCHEMA_RELATIVE = Path(
    "sh-chem-db/kb/machine_governance_v2/schemas/formal_freeze_sidecar_r17.schema.json"
)
GENERATOR_PROVENANCE_SCHEMA_RELATIVE = Path(
    "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r17.schema.json"
)
GENERATOR_PROVENANCE_R18_SCHEMA_RELATIVE = Path(
    "sh-chem-db/kb/machine_governance_v2/schemas/generator_provenance_receipt_r18.schema.json"
)
ROOT_RECONCILIATION_SCHEMA_RELATIVE = Path(
    "sh-chem-db/kb/machine_governance_v2/schemas/root_provenance_reconciliation_attestation_r17.schema.json"
)
CODEX_METADATA_OBSERVATION_SCHEMA_RELATIVE = Path(
    "sh-chem-db/kb/machine_governance_v2/schemas/codex_metadata_observation_r17.schema.json"
)

FORMAL_AUTHORITY_FALSE_FIELDS = (
    "review_dispatch_prepared",
    "review_dispatch_authorized",
    "review_task_creation_authorized",
    "adversarial_dispatch_authorized",
    "chain_creation_authorized",
    "controller_registration_authorized",
    "batch_registration_authorized",
    "publication_authorized",
    "delivery_authorized",
    "human_reviewed",
)
SIDECAR_AUTHORITY_FALSE_FIELDS = (
    "dispatch_prepared",
    "tasks_created",
    "chain_creation_authorized",
    "registration_authorized",
    "publication_authorized",
    "human_reviewed",
)
PROJECTION_COMPONENTS = (
    "prompt",
    "options",
    "score",
    "evidence",
    "shared_material",
    "answer",
    "solver",
    "rubric",
)
PROJECTION_CONTRACT_ID = "atomic_child_exact_projection_v1"


class R17BoundaryError(RuntimeError):
    """Raised when an R17 trust-boundary precondition fails closed."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def subject_pair_sha256(question_sha256: str, answer_sha256: str) -> str:
    return canonical_sha256(
        {"answer_sha256": answer_sha256, "question_sha256": question_sha256}
    )


def _self_hash(value: dict[str, Any], field: str = "self_hash") -> str:
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _casefold_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def _inside(base: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([_casefold_path(base), _casefold_path(candidate)]) == _casefold_path(base)
    except ValueError:
        return False


def portable_relative_path_error(value: object) -> str | None:
    """Return why ``value`` is not a canonical portable relative path.

    Governance records are exchanged across Windows and POSIX consumers.  A
    stored path therefore has one spelling: non-empty forward-slash-separated
    workspace-relative segments, with no traversal or platform-specific
    absolute/drive/UNC syntax.
    """

    if not isinstance(value, str) or not value or "\x00" in value:
        return "empty_or_nul"
    if "\\" in value or ":" in value or value.startswith(("/", "//")):
        return "absolute_or_platform_specific_syntax"
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        return "noncanonical_segment"
    return None


def _has_reparse_attribute(st: os.stat_result) -> bool:
    # FILE_ATTRIBUTE_REPARSE_POINT.  ``st_file_attributes`` is Windows-only.
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _stat_identity(st: os.stat_result) -> tuple[object, ...]:
    """Return the fields shared by path and descriptor observations.

    The final path component and the opened descriptor must name the same
    regular file.  Keeping this tuple in one helper prevents a future field
    drift from silently reopening the guard-to-open race.
    """

    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_size,
        st.st_mtime_ns,
    )


def _descriptor_change_signature(st: os.stat_result) -> tuple[object, ...]:
    """Descriptor-local mutation signature.

    Windows can expose a different ``st_ctime_ns`` through ``lstat(path)`` and
    ``fstat(fd)`` for an otherwise identical file (notably after ``copy2``).
    We therefore use ctime for before/after checks within each observation
    channel, while the cross-channel identity uses the five fields both APIs
    report consistently.
    """

    return (*_stat_identity(st), st.st_ctime_ns)


def _guard_leaf_identity(
    signature: tuple[tuple[object, ...], ...],
) -> tuple[object, ...]:
    if not signature:
        raise R17BoundaryError("referenced_path_has_no_guarded_leaf")
    # The last guard column is the Windows-only path attribute bitset.  It is
    # deliberately guarded for reparse/mutation detection but is not returned
    # consistently by ``fstat`` for an already-open CRT descriptor.
    return tuple(signature[-1][1:6])


def _windows_descriptor_final_path(descriptor: int) -> Path | None:
    """Return a normalized DOS path for an open descriptor on Windows.

    ``fstat`` identity is the mandatory cross-platform binding.  Windows also
    exposes the kernel-resolved final handle path, which closes drive/junction
    alias ambiguity without following a second user-controlled path.
    """

    if os.name != "nt":
        return None
    try:
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        get_final_path.restype = ctypes.c_uint32
        required = get_final_path(handle, None, 0, 0)
        if required == 0:
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW")
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = get_final_path(handle, buffer, len(buffer), 0)
        if written == 0 or written >= len(buffer):
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW")
        rendered = buffer.value
        if rendered.startswith("\\\\?\\UNC\\"):
            rendered = "\\\\" + rendered[8:]
        elif rendered.startswith("\\\\?\\"):
            rendered = rendered[4:]
        return Path(os.path.abspath(rendered))
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        raise R17BoundaryError(
            f"descriptor_final_path_unavailable:{type(exc).__name__}"
        ) from exc


def _guard_signature(base: Path, candidate: Path) -> tuple[tuple[object, ...], ...]:
    """Reject symlink/junction traversal and fingerprint every path component."""

    lexical_base = Path(os.path.abspath(os.fspath(base)))
    lexical_candidate = Path(os.path.abspath(os.fspath(candidate)))
    if not _inside(lexical_base, lexical_candidate):
        raise R17BoundaryError("path_outside_allowlisted_root")
    relative = lexical_candidate.relative_to(lexical_base)
    rows: list[tuple[object, ...]] = []
    current = lexical_base
    components = relative.parts
    for index, component in enumerate(components):
        current = current / component
        try:
            observed = os.lstat(current)
        except OSError as exc:
            raise R17BoundaryError(f"path_component_unreadable:{component}:{type(exc).__name__}") from exc
        if stat.S_ISLNK(observed.st_mode) or _has_reparse_attribute(observed):
            raise R17BoundaryError(f"path_component_reparse_forbidden:{component}")
        attributes = getattr(observed, "st_file_attributes", 0)
        if index == len(components) - 1:
            rows.append(
                (
                    component.casefold(),
                    *_stat_identity(observed),
                    observed.st_ctime_ns,
                    attributes,
                )
            )
        else:
            # Creating a trusted sibling output legitimately changes parent
            # directory size/timestamps.  Directory traversal security depends
            # on object identity, mode and reparse status, not content times.
            rows.append(
                (
                    component.casefold(),
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_mode,
                    attributes,
                )
            )
    return tuple(rows)


def _mkdir_guarded(base: Path, directory: Path) -> None:
    lexical_base = Path(os.path.abspath(os.fspath(base)))
    lexical_directory = Path(os.path.abspath(os.fspath(directory)))
    if not _inside(lexical_base, lexical_directory):
        raise R17BoundaryError("directory_outside_allowlisted_root")
    current = lexical_base
    for component in lexical_directory.relative_to(lexical_base).parts:
        current = current / component
        try:
            observed = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current)
            observed = os.lstat(current)
        if (
            stat.S_ISLNK(observed.st_mode)
            or _has_reparse_attribute(observed)
            or not stat.S_ISDIR(observed.st_mode)
        ):
            raise R17BoundaryError(f"directory_component_unsafe:{component}")


def exclusive_create_bundle(
    *,
    workspace: Path,
    files: Iterable[tuple[Path, bytes]],
    input_graph: SnapshotGraph | None = None,
) -> dict[str, Any]:
    """Create an append-only bundle atomically from the caller's perspective.

    All destinations are guarded against escape/reparse traversal before any
    leaf is created.  Leaves use ``O_EXCL`` and are read back byte-for-byte.  A
    failure removes only leaves created by this call.  One caller-owned input
    graph is revalidated immediately before and after the transaction.
    """

    root = workspace.resolve()
    rows = [
        (Path(os.path.abspath(os.fspath(path))), bytes(payload))
        for path, payload in files
    ]
    if not rows:
        raise R17BoundaryError("exclusive_bundle:no_files")
    normalized = [_casefold_path(path) for path, _ in rows]
    if len(normalized) != len(set(normalized)):
        raise R17BoundaryError("exclusive_bundle:duplicate_output_path")
    for path, _ in rows:
        if not _inside(root, path):
            raise R17BoundaryError("exclusive_bundle:output_outside_workspace")
        _mkdir_guarded(root, path.parent)
        _guard_signature(root, path.parent)
        if path.exists():
            raise FileExistsError(f"exclusive bundle output already exists: {path}")
    if input_graph is not None:
        before = input_graph.revalidate()
        if before.get("status") != "pass":
            raise R17BoundaryError(
                "exclusive_bundle:input_changed_before_write:"
                + ",".join(before.get("errors", []))
            )

    created: list[Path] = []
    try:
        for path, payload in rows:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(path, flags, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

        readback = SnapshotGraph(root)
        refs: dict[str, dict[str, object]] = {}
        for path, expected in rows:
            snapshot = readback.read(path)
            if snapshot.data != expected:
                raise R17BoundaryError(f"exclusive_bundle:readback_mismatch:{path.name}")
            refs[path.as_posix()] = snapshot.ref(root)
        if input_graph is not None:
            after = input_graph.revalidate()
            if after.get("status") != "pass":
                raise R17BoundaryError(
                    "exclusive_bundle:input_changed_after_write:"
                    + ",".join(after.get("errors", []))
                )
        return {
            "created": True,
            "created_count": len(created),
            "output_snapshot_graph_sha256": readback.digest(),
            "refs": refs,
        }
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def assert_safe_regular_tree(root: Path) -> dict[str, Any]:
    """Validate an immutable-package tree without following aliases.

    Attempt packages are evidence containers.  A symlink, Windows reparse
    point, hard-linked leaf, case-fold alias, or non-regular leaf would make
    the bytes selected by a stored relative path depend on host state.  This
    check is deliberately stricter than the older per-file readers.
    """

    lexical_root = Path(os.path.abspath(os.fspath(root)))
    root_stat = os.lstat(lexical_root)
    if stat.S_ISLNK(root_stat.st_mode) or _has_reparse_attribute(root_stat):
        raise R17BoundaryError("immutable_tree:root_reparse_forbidden")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise R17BoundaryError("immutable_tree:root_not_directory")
    seen: dict[str, str] = {}
    file_count = 0
    total_bytes = 0
    identity_rows: list[tuple[object, ...]] = []
    for current, directory_names, file_names in os.walk(lexical_root, followlinks=False):
        current_path = Path(current)
        _guard_signature(lexical_root, current_path)
        for name in [*directory_names, *file_names]:
            candidate = current_path / name
            observed = os.lstat(candidate)
            relative = candidate.relative_to(lexical_root).as_posix()
            folded = relative.casefold()
            previous = seen.setdefault(folded, relative)
            if previous != relative:
                raise R17BoundaryError(
                    f"immutable_tree:casefold_alias:{previous}:{relative}"
                )
            if stat.S_ISLNK(observed.st_mode) or _has_reparse_attribute(observed):
                raise R17BoundaryError(f"immutable_tree:reparse_forbidden:{relative}")
            if name in {".", ".."} or "/../" in f"/{relative}/":
                raise R17BoundaryError(f"immutable_tree:traversal_name:{relative}")
            if candidate.is_dir():
                if not stat.S_ISDIR(observed.st_mode):
                    raise R17BoundaryError(
                        f"immutable_tree:directory_identity_invalid:{relative}"
                    )
                continue
            if not stat.S_ISREG(observed.st_mode):
                raise R17BoundaryError(f"immutable_tree:non_regular_leaf:{relative}")
            if observed.st_nlink != 1:
                raise R17BoundaryError(f"immutable_tree:hardlink_forbidden:{relative}")
            file_count += 1
            total_bytes += observed.st_size
            identity_rows.append(
                (
                    relative,
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_mode,
                    observed.st_size,
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                    getattr(observed, "st_file_attributes", 0),
                )
            )
    return {
        "status": "pass",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "identity_sha256": canonical_sha256(sorted(identity_rows)),
    }


def atomic_commit_directory(
    *,
    workspace: Path,
    staged_root: Path,
    destination: Path,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Publish one validated directory using a same-volume atomic rename.

    The destination is never replaced.  Case-only aliases are rejected before
    the rename, and both trees are checked through guarded lexical paths.  The
    optional callback is a test/fault-injection seam and runs before any rename.
    """

    workspace_root = Path(os.path.abspath(os.fspath(workspace)))
    staged = Path(os.path.abspath(os.fspath(staged_root)))
    final = Path(os.path.abspath(os.fspath(destination)))
    if not _inside(workspace_root, staged) or not _inside(workspace_root, final):
        raise R17BoundaryError("atomic_directory_commit:path_outside_workspace")
    if _casefold_path(staged) == _casefold_path(final):
        raise R17BoundaryError("atomic_directory_commit:source_equals_destination")
    if not staged.is_dir():
        raise R17BoundaryError("atomic_directory_commit:staged_root_missing")
    _mkdir_guarded(workspace_root, final.parent)
    _guard_signature(workspace_root, staged)
    parent_signature = _guard_signature(workspace_root, final.parent)
    folded_name = final.name.casefold()
    aliases = [
        child.name
        for child in final.parent.iterdir()
        if child.name.casefold() == folded_name
    ]
    if aliases:
        raise FileExistsError(
            f"atomic_directory_commit:destination_or_case_alias_exists:{aliases}"
        )
    staged_stat = os.lstat(staged)
    parent_stat = os.lstat(final.parent)
    if staged_stat.st_dev != parent_stat.st_dev:
        raise R17BoundaryError("atomic_directory_commit:cross_device_forbidden")
    tree = assert_safe_regular_tree(staged)
    if before_commit is not None:
        before_commit()
    if parent_signature != _guard_signature(workspace_root, final.parent):
        raise R17BoundaryError("atomic_directory_commit:destination_parent_changed")
    os.rename(staged, final)
    try:
        committed_tree = assert_safe_regular_tree(final)
        if committed_tree != tree:
            raise R17BoundaryError("atomic_directory_commit:tree_changed_during_rename")
    except Exception as original:
        if final.exists() and not staged.exists():
            try:
                os.rename(final, staged)
            except OSError as rollback_error:
                # A caller must never confuse a failed rollback with a clean
                # pre-commit failure.  The attempt layer requires a separate
                # commit attestation, so an orphaned destination is
                # unselectable; surface the rollback failure explicitly for
                # cleanup/quarantine handling.
                raise R17BoundaryError(
                    "atomic_directory_commit:post_validate_rollback_failed"
                ) from rollback_error
        raise original
    return {
        "committed": True,
        "destination": final,
        "file_count": tree["file_count"],
        "total_bytes": tree["total_bytes"],
    }


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    data: bytes
    sha256: str
    byte_length: int
    descriptor_identity: tuple[object, ...]
    guard_signature: tuple[tuple[object, ...], ...]

    def json_value(self) -> Any:
        return json.loads(self.data.decode("utf-8-sig"))

    def ref(self, base: Path) -> dict[str, object]:
        return {
            "path": self.path.relative_to(base.resolve()).as_posix(),
            "sha256": self.sha256,
            "bytes": self.byte_length,
        }


@dataclass
class SnapshotGraph:
    """A per-phase cache: one file path, one open/read, one immutable view."""

    root: Path
    _snapshots: dict[str, FileSnapshot] = field(default_factory=dict)

    def read(self, path: Path) -> FileSnapshot:
        lexical = Path(os.path.abspath(os.fspath(path)))
        key = _casefold_path(lexical)
        cached = self._snapshots.get(key)
        if cached is not None:
            return cached
        before_guard = _guard_signature(self.root, lexical)
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lexical, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise R17BoundaryError("referenced_path_not_regular_file")
            identity_before = _stat_identity(before)
            descriptor_signature_before = _descriptor_change_signature(before)
            if identity_before != _guard_leaf_identity(before_guard):
                raise R17BoundaryError("descriptor_identity_not_guarded_path")
            final_path = _windows_descriptor_final_path(descriptor)
            if final_path is not None and _casefold_path(final_path) != _casefold_path(
                lexical
            ):
                raise R17BoundaryError("descriptor_final_path_mismatch")
            chunks: list[bytes] = []
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
            after = os.fstat(descriptor)
            identity_after = _stat_identity(after)
            descriptor_signature_after = _descriptor_change_signature(after)
        finally:
            os.close(descriptor)
        after_guard = _guard_signature(self.root, lexical)
        if (
            descriptor_signature_before != descriptor_signature_after
            or before_guard != after_guard
            or identity_after != _guard_leaf_identity(after_guard)
        ):
            raise R17BoundaryError("referenced_file_changed_during_single_read")
        data = b"".join(chunks)
        if len(data) != before.st_size:
            raise R17BoundaryError("referenced_file_length_changed_during_single_read")
        snapshot = FileSnapshot(
            path=lexical,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
            descriptor_identity=descriptor_signature_before,
            guard_signature=before_guard,
        )
        self._snapshots[key] = snapshot
        return snapshot

    def verify_ref(
        self,
        reference: object,
        *,
        label: str,
        expected_path: Path | None = None,
    ) -> FileSnapshot:
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256", "bytes"}:
            raise R17BoundaryError(f"{label}:file_ref_not_exact")
        raw_path = reference.get("path")
        path_error = portable_relative_path_error(raw_path)
        if path_error is not None:
            raise R17BoundaryError(f"{label}:path_invalid:{path_error}")
        assert isinstance(raw_path, str)
        path = Path(
            os.path.abspath(os.fspath(self.root.joinpath(*raw_path.split("/"))))
        )
        if not _inside(self.root, path):
            raise R17BoundaryError(f"{label}:path_outside_allowlisted_root")
        if expected_path is not None and _casefold_path(path) != _casefold_path(expected_path):
            raise R17BoundaryError(f"{label}:path_not_allowlisted")
        snapshot = self.read(path)
        if reference.get("sha256") != snapshot.sha256:
            raise R17BoundaryError(f"{label}:sha256_mismatch")
        expected_bytes = reference.get("bytes")
        if isinstance(expected_bytes, bool) or expected_bytes != snapshot.byte_length:
            raise R17BoundaryError(f"{label}:bytes_mismatch")
        return snapshot

    def snapshots(self) -> tuple[FileSnapshot, ...]:
        return tuple(self._snapshots.values())

    def digest(self) -> str:
        rows = sorted(
            (
                snapshot.path.relative_to(self.root.resolve()).as_posix(),
                snapshot.sha256,
                snapshot.byte_length,
                snapshot.descriptor_identity,
            )
            for snapshot in self.snapshots()
        )
        return canonical_sha256(rows)

    def revalidate(self) -> dict[str, Any]:
        """Open every bound path once in a new phase and compare all snapshots."""

        refreshed = SnapshotGraph(self.root)
        errors: list[str] = []
        for expected in self.snapshots():
            try:
                actual = refreshed.read(expected.path)
            except (OSError, R17BoundaryError) as exc:
                errors.append(f"snapshot_refresh_failed:{expected.path.name}:{type(exc).__name__}")
                continue
            if (
                actual.sha256 != expected.sha256
                or actual.byte_length != expected.byte_length
                or actual.descriptor_identity != expected.descriptor_identity
                or actual.guard_signature != expected.guard_signature
            ):
                errors.append(f"snapshot_changed:{expected.path.name}")
        return {
            "status": "pass" if not errors else "fail",
            "original_snapshot_graph_sha256": self.digest(),
            "refreshed_snapshot_graph_sha256": refreshed.digest(),
            "errors": sorted(dict.fromkeys(errors)),
        }


def _load_object(snapshot: FileSnapshot, label: str) -> dict[str, Any]:
    try:
        value = snapshot.json_value()
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise R17BoundaryError(f"{label}:json_unreadable:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise R17BoundaryError(f"{label}:json_object_required")
    return value


def _permissive_schema_paths(value: object, pointer: str = "#") -> list[str]:
    """Reject object schemas that silently admit undeclared security fields."""

    errors: list[str] = []
    if isinstance(value, dict):
        is_object_schema = value.get("type") == "object" or "properties" in value
        if is_object_schema and value.get("additionalProperties") is not False:
            errors.append(pointer)
        for key, child in value.items():
            errors.extend(_permissive_schema_paths(child, f"{pointer}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_permissive_schema_paths(child, f"{pointer}/{index}"))
    return errors


def _validate_with_strict_schema(
    schema_snapshot: FileSnapshot,
    document: dict[str, Any],
    *,
    label: str,
) -> None:
    schema = _load_object(schema_snapshot, f"{label}.schema")
    permissive = _permissive_schema_paths(schema)
    if permissive:
        raise R17BoundaryError(f"{label}:permissive_schema:{','.join(permissive)}")
    try:
        import jsonschema

        validator = jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        )
        errors = sorted(
            validator.iter_errors(document),
            key=lambda error: "/".join(str(item) for item in error.absolute_path),
        )
    except (OSError, ValueError, R17BoundaryError) as exc:
        raise R17BoundaryError(f"{label}:schema_unreadable:{type(exc).__name__}") from exc
    if errors:
        rendered = ";".join(
            f"{'/'.join(str(item) for item in error.absolute_path) or '$'}:{error.message}"
            for error in errors
        )
        raise R17BoundaryError(f"{label}:schema_invalid:{rendered}")


def _exact_false_fields(value: dict[str, Any], fields: Iterable[str], label: str) -> None:
    mismatches = [field for field in fields if field not in value or value[field] is not False]
    if mismatches:
        raise R17BoundaryError(f"{label}:authority_fields_not_explicit_false:{','.join(mismatches)}")


def _validate_formal_freeze_package_with_graph(
    receipt_path: Path,
    sidecar_path: Path,
    *,
    workspace: Path,
    graph: SnapshotGraph,
    revalidate_before_return: bool,
) -> dict[str, Any]:
    """Strictly consume the root-owned R17 formal-freeze package.

    Passing this check proves only an inert formal freeze.  It deliberately
    returns every downstream authority as ``False``.
    """

    workspace = workspace.resolve()
    errors: list[str] = []
    receipt: dict[str, Any] = {}
    sidecar: dict[str, Any] = {}
    try:
        if _casefold_path(graph.root) != _casefold_path(workspace):
            raise R17BoundaryError("formal_freeze:snapshot_graph_root_mismatch")
        formal_schema_path = workspace / FORMAL_SCHEMA_RELATIVE
        sidecar_schema_path = workspace / FORMAL_SIDECAR_SCHEMA_RELATIVE
        formal_schema_snapshot = graph.read(formal_schema_path)
        sidecar_schema_snapshot = graph.read(sidecar_schema_path)
        receipt_snapshot = graph.read(receipt_path)
        sidecar_snapshot = graph.read(sidecar_path)
        receipt = _load_object(receipt_snapshot, "formal_freeze")
        sidecar = _load_object(sidecar_snapshot, "formal_freeze_sidecar")

        graph.verify_ref(
            receipt.get("schema_binding"),
            label="formal_freeze.schema_binding",
            expected_path=formal_schema_path,
        )
        _validate_with_strict_schema(
            formal_schema_snapshot, receipt, label="formal_freeze"
        )
        _validate_with_strict_schema(
            sidecar_schema_snapshot, sidecar, label="formal_freeze_sidecar"
        )
        if receipt.get("schema_version") != R17_SCHEMA_VERSION:
            raise R17BoundaryError("formal_freeze:schema_version_drift")
        if receipt.get("status") != "active_formal_freeze_dispatch_not_prepared":
            raise R17BoundaryError("formal_freeze:not_inert_active_freeze")
        if receipt.get("active") is not True:
            raise R17BoundaryError("formal_freeze:not_active")
        root_go = receipt.get("root_go") if isinstance(receipt.get("root_go"), dict) else {}
        if (
            root_go.get("attestation_kind")
            != "root_codex_metadata_attestation_not_signature"
            or root_go.get("authority_scope") != "formal_freeze_only"
        ):
            raise R17BoundaryError("formal_freeze:root_go_authority_scope_invalid")
        _exact_false_fields(receipt, FORMAL_AUTHORITY_FALSE_FIELDS, "formal_freeze")
        _exact_false_fields(sidecar, SIDECAR_AUTHORITY_FALSE_FIELDS, "formal_freeze_sidecar")
        if receipt.get("self_hash") != _self_hash(receipt):
            raise R17BoundaryError("formal_freeze:self_hash_mismatch")
        if sidecar.get("self_hash") != _self_hash(sidecar):
            raise R17BoundaryError("formal_freeze_sidecar:self_hash_mismatch")

        payload = receipt.get("freeze_payload")
        if not isinstance(payload, dict):
            raise R17BoundaryError("formal_freeze:payload_missing")
        payload_bytes = canonical_json_bytes(payload)
        payload_sha = hashlib.sha256(payload_bytes).hexdigest()
        if receipt.get("freeze_payload_sha256") != payload_sha:
            raise R17BoundaryError("formal_freeze:payload_hash_mismatch")
        for field in (
            "version_id",
            "paper_id",
            "prefreeze_receipt",
            "r17_producer_receipt",
            "sol_generator_receipt",
            "controller_subject",
            "formal_freeze_sidecar_schema",
        ):
            if payload.get(field) != receipt.get(field):
                raise R17BoundaryError(f"formal_freeze:payload_{field}_mismatch")
        if payload.get("root_authorization_thread_id") != root_go.get(
            "root_authorization_thread_id"
        ):
            raise R17BoundaryError("formal_freeze:payload_root_thread_mismatch")

        sidecar_schema_ref = receipt.get("formal_freeze_sidecar_schema")
        graph.verify_ref(
            sidecar_schema_ref,
            label="formal_freeze.formal_freeze_sidecar_schema",
            expected_path=sidecar_schema_path,
        )
        if sidecar.get("schema_binding") != sidecar_schema_ref:
            raise R17BoundaryError("formal_freeze_sidecar:schema_binding_mismatch")
        if sidecar.get("formal_freeze") != receipt_snapshot.ref(workspace.resolve()):
            raise R17BoundaryError("formal_freeze_sidecar:receipt_binding_mismatch")
        expected_payload_binding = {
            "json_pointer": "#/freeze_payload",
            "canonical_sha256": payload_sha,
            "canonical_bytes": len(payload_bytes),
        }
        if sidecar.get("freeze_payload") != expected_payload_binding:
            raise R17BoundaryError("formal_freeze_sidecar:payload_binding_mismatch")
        for field in ("version_id", "paper_id"):
            if sidecar.get(field) != receipt.get(field):
                raise R17BoundaryError(f"formal_freeze_sidecar:{field}_mismatch")

        for field in ("prefreeze_receipt", "r17_producer_receipt", "sol_generator_receipt"):
            graph.verify_ref(receipt.get(field), label=f"formal_freeze.{field}")
        subject = receipt.get("controller_subject")
        if not isinstance(subject, dict):
            raise R17BoundaryError("formal_freeze:controller_subject_missing")
        question = graph.verify_ref(subject.get("question"), label="formal_freeze.question")
        answer = graph.verify_ref(subject.get("answer"), label="formal_freeze.answer")
        if subject.get("subject_pair_sha256") != subject_pair_sha256(
            question.sha256, answer.sha256
        ):
            raise R17BoundaryError("formal_freeze:subject_pair_mismatch")
        if revalidate_before_return:
            checkpoint = graph.revalidate()
            if checkpoint.get("status") != "pass":
                raise R17BoundaryError(
                    "formal_freeze:snapshot_changed_before_return:"
                    + ",".join(checkpoint.get("errors", []))
                )
    except (OSError, ValueError, R17BoundaryError) as exc:
        errors.append(str(exc))

    return {
        "check": "r17_root_owned_formal_freeze_package",
        "status": "pass" if not errors else "fail",
        "active_formal_freeze_only": not errors,
        "snapshot_graph_sha256": graph.digest(),
        "review_dispatch_authorized": False,
        "review_task_creation_authorized": False,
        "adversarial_dispatch_authorized": False,
        "chain_creation_authorized": False,
        "controller_registration_authorized": False,
        "batch_registration_authorized": False,
        "publication_authorized": False,
        "delivery_authorized": False,
        "human_reviewed": False,
        "errors": sorted(dict.fromkeys(errors)),
        "_receipt": receipt,
        "_sidecar": sidecar,
        "_snapshot_graph": graph,
    }


def validate_formal_freeze_package(
    receipt_path: Path,
    sidecar_path: Path,
    *,
    workspace: Path,
) -> dict[str, Any]:
    """Validate a formal freeze with an internally owned, final-revalidated graph."""

    resolved_workspace = workspace.resolve()
    return _validate_formal_freeze_package_with_graph(
        receipt_path,
        sidecar_path,
        workspace=resolved_workspace,
        graph=SnapshotGraph(resolved_workspace),
        revalidate_before_return=True,
    )


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise R17BoundaryError(f"{label}:timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise R17BoundaryError(f"{label}:timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise R17BoundaryError(f"{label}:timezone_required")
    return parsed.astimezone(timezone.utc)


EXECUTION_CORE_FIELDS = (
    "provider",
    "thread_id",
    "turn_id",
    "receipt_id",
    "model",
    "reasoning_effort",
    "root_dispatch_id",
)
OBSERVED_TASK_METADATA_FIELDS = (
    *EXECUTION_CORE_FIELDS,
    "host_id",
    "author_role",
    "turn_status",
)


def _execution_core(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(EXECUTION_CORE_FIELDS):
        raise R17BoundaryError(f"{label}:execution_fields_not_exact")
    return {field: value[field] for field in EXECUTION_CORE_FIELDS}


def _expected_observation_metadata(
    value: object,
    *,
    expected_execution: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(OBSERVED_TASK_METADATA_FIELDS):
        raise R17BoundaryError(
            "root_reconciliation:expected_observation_metadata_fields_not_exact"
        )
    expected = copy.deepcopy(value)
    if {field: expected[field] for field in EXECUTION_CORE_FIELDS} != expected_execution:
        raise R17BoundaryError(
            "root_reconciliation:expected_observation_execution_mismatch"
        )
    if (
        not isinstance(expected.get("host_id"), str)
        or not expected["host_id"].strip()
        or expected.get("author_role") != "root"
        or expected.get("turn_status") != "final"
    ):
        raise R17BoundaryError(
            "root_reconciliation:expected_observation_root_final_host_invalid"
        )
    return expected


def _require_root_owned_observation_path(workspace: Path, path: Path) -> None:
    try:
        relative = Path(os.path.abspath(os.fspath(path))).relative_to(workspace.resolve())
    except ValueError as exc:
        raise R17BoundaryError(
            "root_reconciliation:metadata_observation_not_root_owned_path"
        ) from exc
    parts = tuple(part.casefold() for part in relative.parts)
    required_prefix = ("staging", "coordination", "root", "formal_freeze")
    if len(parts) <= len(required_prefix) or parts[: len(required_prefix)] != required_prefix:
        raise R17BoundaryError(
            "root_reconciliation:metadata_observation_not_root_owned_path"
        )


def validate_root_provenance_reconciliation(
    attestation_path: Path,
    *,
    workspace: Path,
    expected_artifact_id: str,
    expected_subject_pair_sha256: str,
    expected_execution: dict[str, Any],
    expected_observation_metadata: dict[str, Any] | None = None,
    expected_formal_artifact_id: str | None = None,
    expected_formal_subject_pair_sha256: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Consume, but never mint, one root reconciliation snapshot graph.

    ``expected_artifact_id`` and ``expected_subject_pair_sha256`` identify the
    chain being reconciled.  For a paper chain the formal-freeze identity
    defaults to that same pair.  An atomic child must explicitly supply the
    parent paper's formal-freeze identity through the two
    ``expected_formal_*`` arguments; its attestation and generator receipt
    remain bound to the child identity.
    """

    workspace = workspace.resolve()
    graph = SnapshotGraph(workspace)
    errors: list[str] = []
    attestation: dict[str, Any] = {}
    attestation_snapshot: FileSnapshot | None = None
    try:
        execution = _execution_core(
            expected_execution,
            label="root_reconciliation.expected_execution",
        )
        observation_metadata = _expected_observation_metadata(
            expected_observation_metadata,
            expected_execution=execution,
        )
        formal_artifact_id = (
            expected_artifact_id
            if expected_formal_artifact_id is None
            else expected_formal_artifact_id
        )
        formal_subject_pair = (
            expected_subject_pair_sha256
            if expected_formal_subject_pair_sha256 is None
            else expected_formal_subject_pair_sha256
        )
        schema_path = workspace / ROOT_RECONCILIATION_SCHEMA_RELATIVE
        generator_schema_path = workspace / GENERATOR_PROVENANCE_SCHEMA_RELATIVE
        observation_schema_path = workspace / CODEX_METADATA_OBSERVATION_SCHEMA_RELATIVE
        schema_snapshot = graph.read(schema_path)
        generator_schema_snapshot = graph.read(generator_schema_path)
        observation_schema_snapshot = graph.read(observation_schema_path)
        attestation_snapshot = graph.read(attestation_path)
        attestation = _load_object(attestation_snapshot, "root_reconciliation")
        _validate_with_strict_schema(schema_snapshot, attestation, label="root_reconciliation")
        if attestation.get("self_hash") != _self_hash(attestation):
            raise R17BoundaryError("root_reconciliation:self_hash_mismatch")
        if (
            attestation.get("attestation_kind")
            != "root_codex_metadata_attestation_not_signature"
            or attestation.get("external_reconciled") is not True
            or attestation.get("cryptographic_signature") is not False
            or attestation.get("private_key_used") is not False
            or attestation.get("human_reviewed") is not False
        ):
            raise R17BoundaryError("root_reconciliation:authority_boundary_invalid")
        if attestation.get("artifact_id") != expected_artifact_id:
            raise R17BoundaryError("root_reconciliation:artifact_reuse_rejected")
        if attestation.get("subject_pair_sha256") != expected_subject_pair_sha256:
            raise R17BoundaryError("root_reconciliation:subject_reuse_rejected")
        if attestation.get("actual_task_metadata") != execution:
            raise R17BoundaryError("root_reconciliation:actual_task_metadata_mismatch")

        issued = _parse_utc(attestation.get("issued_at"), "root_reconciliation.issued_at")
        expires = _parse_utc(attestation.get("expires_at"), "root_reconciliation.expires_at")
        observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if issued > observed_now + timedelta(minutes=5):
            raise R17BoundaryError("root_reconciliation:issued_in_future")
        if expires <= observed_now or expires <= issued:
            raise R17BoundaryError("root_reconciliation:expired_or_invalid_interval")
        if expires - issued > timedelta(days=7):
            raise R17BoundaryError("root_reconciliation:validity_exceeds_seven_days")

        observation_snapshot = graph.verify_ref(
            attestation.get("codex_metadata_observation"),
            label="root_reconciliation.codex_metadata_observation",
        )
        _require_root_owned_observation_path(workspace, observation_snapshot.path)
        observation = _load_object(
            observation_snapshot,
            "root_reconciliation.codex_metadata_observation",
        )
        graph.verify_ref(
            observation.get("schema_binding"),
            label="root_reconciliation.codex_metadata_observation.schema_binding",
            expected_path=observation_schema_path,
        )
        _validate_with_strict_schema(
            observation_schema_snapshot,
            observation,
            label="root_reconciliation.codex_metadata_observation",
        )
        if observation.get("self_hash") != _self_hash(observation):
            raise R17BoundaryError(
                "root_reconciliation:codex_metadata_observation_self_hash_mismatch"
            )
        if observation.get("observed_task_metadata") != observation_metadata:
            raise R17BoundaryError(
                "root_reconciliation:codex_metadata_observation_task_metadata_mismatch"
            )
        if (
            observation.get("observation_kind")
            != "non_signing_root_metadata_observation"
            or observation.get("observation_source")
            != "root_codex_app_read_thread_result"
            or observation.get("non_signing") is not True
            or observation.get("cryptographic_signature") is not False
            or observation.get("private_key_used") is not False
            or observation.get("human_reviewed") is not False
        ):
            raise R17BoundaryError(
                "root_reconciliation:codex_metadata_observation_authority_boundary_invalid"
            )
        observed_at = _parse_utc(
            observation.get("observed_at"),
            "root_reconciliation.codex_metadata_observation.observed_at",
        )
        if observed_at > observed_now + timedelta(minutes=5):
            raise R17BoundaryError(
                "root_reconciliation:codex_metadata_observation_in_future"
            )
        if observed_now - observed_at > timedelta(days=7):
            raise R17BoundaryError(
                "root_reconciliation:codex_metadata_observation_stale"
            )
        formal_snapshot = graph.verify_ref(
            attestation.get("formal_freeze"),
            label="root_reconciliation.formal_freeze",
        )
        sidecar_snapshot = graph.verify_ref(
            attestation.get("formal_freeze_sidecar"),
            label="root_reconciliation.formal_freeze_sidecar",
        )
        formal_freeze = _load_object(formal_snapshot, "root_reconciliation.formal_freeze")
        formal_validation = _validate_formal_freeze_package_with_graph(
            formal_snapshot.path,
            sidecar_snapshot.path,
            workspace=workspace,
            graph=graph,
            revalidate_before_return=False,
        )
        if formal_validation.get("status") != "pass":
            raise R17BoundaryError(
                "root_reconciliation:formal_freeze_package_invalid:"
                + ",".join(formal_validation.get("errors", []))
            )
        freeze_payload = formal_freeze.get("freeze_payload")
        if not isinstance(freeze_payload, dict):
            raise R17BoundaryError("root_reconciliation:formal_freeze_payload_missing")
        payload_bytes = canonical_json_bytes(freeze_payload)
        expected_payload_binding = {
            "json_pointer": "#/freeze_payload",
            "canonical_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "canonical_bytes": len(payload_bytes),
        }
        if attestation.get("formal_freeze_payload") != expected_payload_binding:
            raise R17BoundaryError("root_reconciliation:formal_freeze_payload_mismatch")
        if formal_freeze.get("paper_id") != formal_artifact_id:
            raise R17BoundaryError("root_reconciliation:formal_freeze_artifact_mismatch")
        if (
            formal_freeze.get("controller_subject", {}).get("subject_pair_sha256")
            != formal_subject_pair
        ):
            raise R17BoundaryError("root_reconciliation:formal_freeze_subject_mismatch")
        expected_formal_ref = formal_snapshot.ref(workspace)
        expected_sidecar_ref = sidecar_snapshot.ref(workspace)
        for field, expected in (
            ("formal_freeze", expected_formal_ref),
            ("formal_freeze_sidecar", expected_sidecar_ref),
            ("formal_freeze_payload", expected_payload_binding),
        ):
            if observation.get(field) != expected:
                raise R17BoundaryError(
                    f"root_reconciliation:codex_metadata_observation_{field}_mismatch"
                )
            if attestation.get(field) != observation.get(field):
                raise R17BoundaryError(
                    f"root_reconciliation:attestation_observation_{field}_mismatch"
                )

        generator_snapshot = graph.verify_ref(
            attestation.get("generator_provenance_receipt"),
            label="root_reconciliation.generator_provenance_receipt",
        )
        generator_receipt = _load_object(generator_snapshot, "generator_provenance_receipt")
        _validate_with_strict_schema(
            generator_schema_snapshot,
            generator_receipt,
            label="generator_provenance_receipt",
        )
        if generator_receipt.get("self_hash") != _self_hash(generator_receipt):
            raise R17BoundaryError("generator_provenance_receipt:self_hash_mismatch")
        if generator_receipt.get("provenance_status") != "self_reported":
            raise R17BoundaryError("generator_provenance_receipt:not_self_reported")
        if generator_receipt.get("artifact_id") != expected_artifact_id:
            raise R17BoundaryError("generator_provenance_receipt:artifact_mismatch")
        if generator_receipt.get("subject_pair_sha256") != expected_subject_pair_sha256:
            raise R17BoundaryError("generator_provenance_receipt:subject_mismatch")
        if generator_receipt.get("reported_execution") != execution:
            raise R17BoundaryError("generator_provenance_receipt:execution_mismatch")
        for field in ("r17_producer_receipt", "sol_generator_receipt"):
            if generator_receipt.get(field) != attestation.get(field):
                raise R17BoundaryError(f"root_reconciliation:{field}_binding_mismatch")
            if formal_freeze.get(field) != attestation.get(field):
                raise R17BoundaryError(
                    f"root_reconciliation:formal_freeze_{field}_binding_mismatch"
                )
            graph.verify_ref(attestation.get(field), label=f"root_reconciliation.{field}")
        checkpoint = graph.revalidate()
        if checkpoint.get("status") != "pass":
            raise R17BoundaryError(
                "root_reconciliation:snapshot_changed_before_return:"
                + ",".join(checkpoint.get("errors", []))
            )
    except (OSError, ValueError, R17BoundaryError) as exc:
        errors.append(str(exc))

    return {
        "check": "r17_root_external_generator_metadata_reconciliation",
        "status": "pass" if not errors else "fail",
        "external_reconciled": not errors,
        "assurance": (
            "root_codex_metadata_attestation_reconciled_not_cryptographic_signature"
            if not errors
            else "unreconciled_fail_closed"
        ),
        "attestation_id": attestation.get("attestation_id"),
        "attestation_ref": (
            attestation_snapshot.ref(workspace)
            if not errors and attestation_snapshot is not None
            else None
        ),
        "snapshot_graph_sha256": graph.digest(),
        "errors": sorted(dict.fromkeys(errors)),
        "_snapshot_graph": graph,
    }


def write_generator_provenance_receipt(
    output_path: Path,
    *,
    workspace: Path,
    artifact_id: str,
    subject_pair_sha256_value: str,
    reported_execution: dict[str, Any],
    r17_producer_receipt_path: Path,
    sol_generator_receipt_path: Path,
) -> dict[str, Any]:
    """Write the generator's immutable *self-reported* receipt, never a root proof."""

    graph = SnapshotGraph(workspace.resolve())
    schema_path = workspace / GENERATOR_PROVENANCE_SCHEMA_RELATIVE
    schema_snapshot = graph.read(schema_path)
    producer_snapshot = graph.read(r17_producer_receipt_path)
    sol_snapshot = graph.read(sol_generator_receipt_path)
    if not artifact_id or not re_fullmatch_sha256(subject_pair_sha256_value):
        raise R17BoundaryError("generator_provenance_receipt:identity_invalid")
    receipt: dict[str, Any] = {
        "schema_version": R17_SCHEMA_VERSION,
        "record_type": "generator_execution_provenance_receipt",
        "receipt_id": f"GENERATOR-PROVENANCE-{artifact_id}-{subject_pair_sha256_value[:16]}",
        "artifact_id": artifact_id,
        "subject_pair_sha256": subject_pair_sha256_value,
        "provenance_status": "self_reported",
        "reported_execution": copy.deepcopy(reported_execution),
        "r17_producer_receipt": producer_snapshot.ref(workspace.resolve()),
        "sol_generator_receipt": sol_snapshot.ref(workspace.resolve()),
        "human_reviewed": False,
    }
    receipt["self_hash"] = _self_hash(receipt)
    _validate_with_strict_schema(
        schema_snapshot,
        receipt,
        label="generator_provenance_receipt",
    )
    checkpoint = graph.revalidate()
    if checkpoint["status"] != "pass":
        raise R17BoundaryError(
            f"generator_provenance_receipt:input_changed_before_write:{checkpoint['errors']}"
        )
    content = json.dumps(
        receipt,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    output = Path(os.path.abspath(os.fspath(output_path)))
    if not _inside(workspace, output):
        raise R17BoundaryError("generator_provenance_receipt:output_outside_workspace")
    _mkdir_guarded(workspace, output.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(output, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    stored = SnapshotGraph(workspace.resolve()).read(output)
    if stored.data != content or _load_object(stored, "generator_provenance_receipt") != receipt:
        output.unlink(missing_ok=True)
        raise R17BoundaryError("generator_provenance_receipt:post_write_mismatch")
    return {
        "created": True,
        "receipt": receipt,
        "receipt_ref": stored.ref(workspace.resolve()),
        "provenance_status": "self_reported",
        "external_reconciled": False,
        "cryptographic_signature": False,
        "private_key_used": False,
        "human_reviewed": False,
        "chain_creation_authorized": False,
        "publication_authorized": False,
    }


def write_generator_provenance_receipt_r18(
    output_path: Path,
    *,
    workspace: Path,
    artifact_id: str,
    version_id: str,
    paper_id: str,
    subject_pair_sha256_value: str,
    reported_execution: dict[str, Any],
    r18_producer_receipt_path: Path,
    sol_generator_receipt_path: Path,
    question_path: Path,
    answer_path: Path,
) -> dict[str, Any]:
    """Write the R18 generator self-report with no root-observation claim."""

    root = workspace.resolve()
    graph = SnapshotGraph(root)
    schema_path = root / GENERATOR_PROVENANCE_R18_SCHEMA_RELATIVE
    schema_snapshot = graph.read(schema_path)
    producer_snapshot = graph.read(r18_producer_receipt_path)
    sol_snapshot = graph.read(sol_generator_receipt_path)
    question_snapshot = graph.read(question_path)
    answer_snapshot = graph.read(answer_path)
    if not artifact_id or not version_id.endswith("-R18") or not re_fullmatch_sha256(
        subject_pair_sha256_value
    ):
        raise R17BoundaryError("generator_provenance_receipt_r18:identity_invalid")
    expected_pair = subject_pair_sha256(question_snapshot.sha256, answer_snapshot.sha256)
    if expected_pair != subject_pair_sha256_value:
        raise R17BoundaryError("generator_provenance_receipt_r18:subject_pair_mismatch")
    execution = _execution_core(
        reported_execution,
        label="generator_provenance_receipt_r18.reported_execution",
    )
    receipt: dict[str, Any] = {
        "schema_version": "3.0.0-r18",
        "record_type": "generator_execution_provenance_receipt",
        "schema_binding": schema_snapshot.ref(root),
        "receipt_id": f"GENERATOR-PROVENANCE-R18-{artifact_id}-{subject_pair_sha256_value[:16]}",
        "artifact_id": artifact_id,
        "version_id": version_id,
        "paper_id": paper_id,
        "subject_pair_sha256": subject_pair_sha256_value,
        "provenance_status": "self_reported",
        "reported_execution": execution,
        "r18_producer_receipt": producer_snapshot.ref(root),
        "sol_generator_receipt": sol_snapshot.ref(root),
        "controller_subject": {
            "question": question_snapshot.ref(root),
            "answer": answer_snapshot.ref(root),
            "subject_pair_sha256": subject_pair_sha256_value,
        },
        "human_reviewed": False,
    }
    receipt["self_hash"] = _self_hash(receipt)
    _validate_with_strict_schema(
        schema_snapshot,
        receipt,
        label="generator_provenance_receipt_r18",
    )
    content = json.dumps(
        receipt,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    transaction = exclusive_create_bundle(
        workspace=root,
        files=[(output_path, content)],
        input_graph=graph,
    )
    output_snapshot = SnapshotGraph(root).read(output_path)
    return {
        "created": transaction["created"],
        "receipt": receipt,
        "receipt_ref": output_snapshot.ref(root),
        "provenance_status": "self_reported",
        "external_reconciled": False,
        "cryptographic_signature": False,
        "private_key_used": False,
        "human_reviewed": False,
        "chain_creation_authorized": False,
        "publication_authorized": False,
    }


def re_fullmatch_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def json_pointer_get(document: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise ValueError(f"absolute JSON pointer required: {pointer}")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def atomic_projection_contract(
    *,
    parent_question: dict[str, Any],
    parent_answer: dict[str, Any],
    child_question: dict[str, Any],
    child_answer: dict[str, Any],
    theme_index: int,
    printed_index: int,
    part_index: int,
    answer_index: int,
) -> dict[str, Any]:
    """Create the exact eight-component parent-to-child canonical projection."""

    question_prefix = (
        f"/themes/{theme_index}/printed_questions/{printed_index}/atomic_parts/{part_index}"
    )
    specifications = (
        ("prompt", "question", f"{question_prefix}/prompt", "/atomic_part/prompt"),
        ("options", "question", f"{question_prefix}/options", "/atomic_part/options"),
        ("score", "question", f"{question_prefix}/score", "/atomic_part/score"),
        (
            "evidence",
            "question",
            f"{question_prefix}/evidence_refs",
            "/atomic_part/evidence_refs",
        ),
        (
            "shared_material",
            "question",
            f"/themes/{theme_index}/shared_material",
            "/theme/shared_material",
        ),
        ("answer", "answer", f"/answers/{answer_index}/answer", "/answer/answer"),
        ("solver", "answer", f"/answers/{answer_index}/solver", "/answer/solver"),
        (
            "rubric",
            "answer",
            f"/answers/{answer_index}/answer/suggested_scoring",
            "/answer/answer/suggested_scoring",
        ),
    )
    rows: list[dict[str, Any]] = []
    for component, source_document, parent_pointer, child_pointer in specifications:
        parent_document = parent_question if source_document == "question" else parent_answer
        child_document = child_question if source_document == "question" else child_answer
        try:
            parent_value = json_pointer_get(parent_document, parent_pointer)
            child_value = json_pointer_get(child_document, child_pointer)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise R17BoundaryError(f"atomic_projection:{component}:pointer_missing") from exc
        if canonical_json_bytes(parent_value) != canonical_json_bytes(child_value):
            raise R17BoundaryError(f"atomic_projection:{component}:value_mismatch")
        payload = canonical_json_bytes(parent_value)
        rows.append(
            {
                "component": component,
                "source_document": source_document,
                "parent_json_pointer": parent_pointer,
                "child_json_pointer": child_pointer,
                "canonical_sha256": hashlib.sha256(payload).hexdigest(),
                "canonical_bytes": len(payload),
            }
        )
    if tuple(row["component"] for row in rows) != PROJECTION_COMPONENTS:
        raise R17BoundaryError("atomic_projection:component_order_mismatch")
    canonical_projection = canonical_json_bytes(rows)
    return {
        "contract_id": PROJECTION_CONTRACT_ID,
        "components": rows,
        "canonical_projection_sha256": hashlib.sha256(canonical_projection).hexdigest(),
        "canonical_projection_bytes": len(canonical_projection),
    }
