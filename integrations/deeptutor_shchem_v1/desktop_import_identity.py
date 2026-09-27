"""Read-only Word identity comparisons; continuation never rewrites old labels."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path

_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PREFIX = "visual-import-v2:"
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _fail(code, text):
    from .desktop_facade import DesktopFacadeError
    raise DesktopFacadeError(code, text)


@contextmanager
def identity_guard(root):
    """Serialize saves/confirmations across facades and processes, not previews.

    The advisory file lives in the OS temporary directory: a cancelled final
    check does not create personal import records or a personal-state directory.
    """
    from .desktop_import_recovery import import_batch_lock
    from .desktop_visual_import_v2 import DesktopImportBridgeError
    key = str(Path(root).resolve()).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    if not lock.acquire(blocking=False):
        _fail("import_identity_busy", "另一个导入正在保存，请完成后重新预览。")
    active = getattr(_LOCAL, "active", set())
    try:
        if key in active:
            yield
            return
        _LOCAL.active = active | {key}
        lock_root = Path(tempfile.gettempdir()) / "shchem-import-identity" / hashlib.sha256(key.encode()).hexdigest()
        try:
            with import_batch_lock(lock_root, "WORD-SOURCES"):
                yield
        except DesktopImportBridgeError as exc:
            _fail(exc.code, exc.message_zh)
        finally:
            _LOCAL.active = active
    finally:
        lock.release()


def serialized_import(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with identity_guard(self.paths.state_root):
            return method(self, *args, **kwargs)
    return guarded


def _archive_matches(facade, row):
    """Bounded byte verification; no native reader, cache, model, or DB writes."""
    digest, size = row.get("source_sha256"), row.get("size_bytes")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int or not 0 < size <= 40 * 1024 * 1024
            or row.get("archive_relative_path") != f"sources/{digest}.docx"):
        _fail("import_identity_archive", "已有 Word 的来源记录无法核验，请在导入历史核对后再试。")
    folder = facade._visual_import_root / "sources"
    path = folder / f"{digest}.docx"
    try:
        for part in (facade._visual_import_root, folder, path):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ValueError("linked archive")
        if path.stat().st_size != size:
            raise ValueError("changed size")
        current, total = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(min(1024 * 1024, size + 1 - total)):
                total += len(chunk)
                if total > size:
                    raise ValueError("oversized archive")
                current.update(chunk)
        if total != size or current.hexdigest() != digest:
            raise ValueError("changed archive")
    except (OSError, ValueError) as exc:
        from .desktop_facade import DesktopFacadeError
        raise DesktopFacadeError("import_identity_archive",
            "已有 Word 原件缺失或已变化；尚未另建来源，请在导入历史核对。") from exc


def inspect_identities(facade, sources):
    """Compare selected Word metadata against source-bound saved batches.

    Only relevant matching/filename-conflicting archives are read. The earliest
    saved alias follows the Word catalogue's existing stable ordering; no new
    title, source revision, range, or attribute is manufactured on continuation.
    """
    words = [row for row in sources if row["kind"] == "docx"]
    hashes = {row["source_sha256"] for row in words}
    names = {row["source_name"].casefold() for row in words}
    candidates, bindings = [], []
    if words:
        drafts = facade._state.snapshot().get("drafts", {})
        batches = []
        for key, value in drafts.items():
            if not isinstance(key, str) or not key.startswith(_PREFIX):
                continue
            if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
                _fail("import_identity_history", "已有导入目录不完整，请先在导入历史核对。")
            if any(not isinstance(row, dict) for row in value["sources"]):
                _fail("import_identity_history", "已有导入来源目录不完整，请先核对。")
            relevant = [row for row in value["sources"] if row.get("mime_type") == _MIME
                        and (row.get("source_sha256") in hashes
                             or str(row.get("filename", "")).casefold() in names)]
            if relevant:
                batches.append((value, relevant))
        batches.sort(key=lambda value: str(value[0].get("created_at") or ""))
        verified_archives, total_bytes = set(), 0
        for descriptor, relevant in batches:
            saved = facade._saved_visual_import_batch(descriptor.get("batch_id"))
            if saved != descriptor:
                _fail("import_identity_changed", "已有导入记录已变化，请重新预览后确认。")
            manifest, revision = facade._import_batch_manifest(saved)
            bindings.append([_digest(saved), revision])
            for row in relevant:
                identity = (row.get("source_sha256"), row.get("size_bytes"), row.get("archive_relative_path"))
                if identity not in verified_archives:
                    size = row.get("size_bytes")
                    if type(size) is not int or not 0 < size <= 40 * 1024 * 1024:
                        _fail("import_identity_archive", "已有 Word 的文件大小记录不正确，请核对导入历史。")
                    total_bytes += size
                    if total_bytes > 512 * 1024 * 1024:
                        _fail("import_identity_budget", "本次需核对的已有 Word 超过512MB，请分批选择。")
                    _archive_matches(facade, row)
                    verified_archives.add(identity)
                candidates.append({
                    "batch_id": saved["batch_id"],
                    "archive_source_id": row["source_file_id"],
                    "source_sha256": row["source_sha256"],
                    "source_name": row["filename"],
                    "role": row["role"],
                    "visual_status": manifest.get("visual_status"),
                })
    items = {}
    for source in words:
        matches = [row for row in candidates if row["source_sha256"] == source["source_sha256"]]
        versions = [row for row in candidates if row["source_name"].casefold() == source["source_name"].casefold()
                    and row["source_sha256"] != source["source_sha256"]]
        items[source["source_id"]] = {
            "status": "existing" if matches else "new_version" if versions else "new",
            "existing": deepcopy(matches[0]) if matches else None,
            "same_name_versions": deepcopy(versions),
        }
    return {"schema": "word-import-identity.v1", "items": items,
            "revision": _digest({"items": items, "bindings": bindings})}


def selection_plan(sources, identities, selected_ids):
    """Pure preview/commit policy. Never detach an answer from its new context."""
    chosen = [row for row in sources if row["source_id"] in set(selected_ids)]
    new, reused, repeated, seen = [], [], [], {}
    for row in chosen:
        identity = identities.get("items", {}).get(row["source_id"], {})
        if row["kind"] == "docx" and row["source_sha256"] in seen:
            repeated.append(row)
        elif identity.get("status") == "existing":
            reused.append({**deepcopy(identity["existing"]), "input_source_id": row["source_id"],
                           "input_name": row["source_name"]})
            seen[row["source_sha256"]] = row
        else:
            new.append(row)
            if row["kind"] == "docx":
                seen[row["source_sha256"]] = row
    conflict = bool((reused or repeated) and new and
                    any(row["kind"] != "docx" or row["role"] != "handout" for row in chosen))
    # Repeated bytes in different roles cannot silently collapse their relation.
    conflict = conflict or any(row["role"] != seen[row["source_sha256"]]["role"] for row in repeated)
    return {"new": new, "reused": reused, "repeated": repeated, "context_conflict": conflict}
