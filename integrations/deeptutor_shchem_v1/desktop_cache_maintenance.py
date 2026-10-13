"""Protocol allowlist for disposable local projections; never a tree cleaner.

SQLite observations are evidence and stay in place. Unknown files, rows,
versions, interrupted writes and quarantined databases are deliberately kept.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import gzip
import hashlib
import json
import io
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from uuid import uuid4
from weakref import WeakSet

from .desktop_registry_cache import CACHE_SCHEMA, _parse_utc
from .desktop_state import _shared_lock, _state_file_lock
from .desktop_word_preview_cache import CACHE_REVISION, MAX_PREVIEW_BYTES, WordPreviewCache
from .desktop_word_catalog_index import (
    CATALOG_INDEX_REVISION, MAX_RECORD_BYTES, QUESTION_RULE_SHA256,
    WordCatalogIndex, _digest, _positions,
)
from .desktop_word_question_index import QUESTION_INDEX_REVISION, _key as question_key
from .word_native_text import NATIVE_WORD_TEXT_REVISION
from .word_static_eq import REVISION as STATIC_EQ_REVISION

PLAN_SCHEMA = "shchem.desktop-cache-maintenance-plan.v1"
CATEGORIES = {
    "word_previews": "Word 原生预览",
    "word_catalog": "Word 目录与检索索引",
    "registry": "首页资料统计",
}
_TABLES = ("sources", "searches", "ranges")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 256 * 1024 * 1024
EPOCH_SCHEMA = "shchem.desktop-cache-generation.v1"
_EPOCH_FILE = "desktop-cache-generation.v1.json"
_SERVICES = WeakSet()
_SERVICES_LOCK = threading.RLock()


def _fingerprint(path):
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError("非独立普通文件，已保留")
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def _safe_path(root, path):
    root, path = Path(root), Path(path)
    if not path.is_relative_to(root):
        raise ValueError("路径超出维护范围，已保留")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("链接或联接路径，已保留")
    if path.resolve() != path.absolute() or root.resolve() != root.absolute():
        raise ValueError("路径无法核验，已保留")


def _read(root, path):
    _safe_path(root, path)
    before = _fingerprint(path)
    if before[2] > _MAX_FILE_BYTES:
        raise ValueError("超过维护读取上限，已保留")
    raw = path.read_bytes()
    if _fingerprint(path) != before:
        raise ValueError("文件正在变化，已保留")
    return raw, before, hashlib.sha256(raw).hexdigest()


def _preview_valid(path, raw):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        decoded = stream.read(MAX_PREVIEW_BYTES + 1)
    if len(decoded) > MAX_PREVIEW_BYTES:
        return False
    value = json.loads(decoded)
    if not isinstance(value, dict) or set(value) != {"cache_revision", "static_eq_revision", "preview"}:
        return False
    preview = value["preview"]
    if not isinstance(preview, dict) or not _SHA.fullmatch(str(preview.get("source_sha256", ""))):
        return False
    name = preview.get("source_name")
    return (isinstance(name, str) and bool(name)
        and value["cache_revision"] == CACHE_REVISION
        and value["static_eq_revision"] == STATIC_EQ_REVISION
        and WordPreviewCache._valid(preview, preview["source_sha256"], name)
        and WordPreviewCache(path.parent)._path(preview["source_sha256"], name) == path)


def _registry_valid(raw):
    from .desktop_facade import DesktopWorkbenchFacade
    value = json.loads(raw)
    return (isinstance(value, dict)
        and set(value) == {"schema_version", "created_at", "registry"}
        and value["schema_version"] == CACHE_SCHEMA
        and _parse_utc(value["created_at"]) is not None
        and isinstance(value["registry"], dict)
        and DesktopWorkbenchFacade._registry_from_cache(value["registry"]) is not None)


def _binding_valid(binding):
    return (isinstance(binding, dict)
        and binding.get("catalog_index_revision") == CATALOG_INDEX_REVISION
        and binding.get("question_index_revision") == QUESTION_INDEX_REVISION
        and binding.get("question_rule_sha256") == QUESTION_RULE_SHA256
        and binding.get("native_word_text_revision") == NATIVE_WORD_TEXT_REVISION
        and binding.get("static_eq_revision") == STATIC_EQ_REVISION
        and binding.get("extraction_revision") in {
            NATIVE_WORD_TEXT_REVISION, NATIVE_WORD_TEXT_REVISION + "+" + STATIC_EQ_REVISION}
        and all(_SHA.fullmatch(str(binding.get(key, ""))) for key in ("source_sha256", "source_revision"))
        and isinstance(binding.get("source_name"), str) and bool(binding["source_name"])
        and type(binding.get("source_block_count")) is int and binding["source_block_count"] >= 0)


def _row_valid(table, identity, revision, payload):
    if not isinstance(payload, str) or len(payload.encode()) > MAX_RECORD_BYTES:
        return False
    record = WordCatalogIndex._decode((revision, payload))
    if not record or not _binding_valid(record.get("binding")):
        return False
    binding = record["binding"]
    source_id = WordCatalogIndex._identity(binding)
    if table == "searches":
        if set(record) != {"binding", "key", "question_revision", "projection"}:
            return False
        if not isinstance(record["key"], str) or not _SHA.fullmatch(str(record["question_revision"])):
            return False
        projection = record["projection"]
        if (not isinstance(projection, dict) or set(projection) != {"revision", "positions"}
                or projection["revision"] != "word-search-character-postings-v1"
                or not isinstance(projection["positions"], dict)):
            return False
        for token, offsets in projection["positions"].items():
            if not re.fullmatch(r"[0-9a-f]{16}", token) or not isinstance(offsets, str):
                return False
            _positions(offsets)
        return identity == WordCatalogIndex.search_identity(binding, record["key"], record["question_revision"])
    expected_fields = {"binding", "items"} | ({"ranges_revision"} if table == "ranges" else set())
    if set(record) != expected_fields or not isinstance(record["items"], list):
        return False
    if table == "ranges":
        if not _SHA.fullmatch(str(record["ranges_revision"])):
            return False
        source_id = _digest([source_id, record["ranges_revision"]])
    if identity != source_id:
        return False
    for item in record["items"]:
        if (not isinstance(item, dict) or item.get("source_sha256") != binding["source_sha256"]
                or item.get("source_revision") != binding["source_revision"]
                or item.get("extraction_revision") != binding["extraction_revision"]
                or item.get("index_revision") != QUESTION_INDEX_REVISION
                or type(item.get("origin_block_start")) is not int
                or item.get("key") != question_key(binding["source_sha256"], item["origin_block_start"])
                or not _SHA.fullmatch(str(item.get("revision", "")))):
            return False
        for group in ("question_blocks", "answer_blocks", "context_blocks"):
            refs = item.get(group)
            if not isinstance(refs, list):
                return False
            for ref in refs:
                index = ref.get("index") if isinstance(ref, dict) else ref
                if type(index) is not int or not 0 < index <= binding["source_block_count"]:
                    return False
                if isinstance(ref, dict) and (set(ref) != {"index", "text_range"}
                        or not isinstance(ref["text_range"], list) or len(ref["text_range"]) != 2
                        or any(type(number) is not int for number in ref["text_range"])
                        or not 0 <= ref["text_range"][0] < ref["text_range"][1]):
                    return False
    return True


def _index_rows(path):
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("索引有活动日志或未知伴随文件，已保留")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise ValueError("索引日志模式不在已知协议内，已保留")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("索引完整性无法核验，已保留")
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names != set((*_TABLES, "observations")):
            raise ValueError("索引协议无法核验，已保留")
        for table in (*_TABLES, "observations"):
            if [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] != ["identity", "revision", "payload"]:
                raise ValueError("索引列协议无法核验，已保留")
            if connection.execute(f"PRAGMA foreign_key_list({table})").fetchone():
                raise ValueError("索引有未知外键，已保留")
        # Triggers can turn a cache-row deletion into evidence deletion.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():
            raise ValueError("索引有未知触发器，已保留")
        rows, kept = [], 0
        for table in _TABLES:
            for identity, revision, payload in connection.execute(f"SELECT identity,revision,payload FROM {table} ORDER BY identity"):
                try:
                    valid = _row_valid(table, identity, revision, payload)
                except (ValueError, TypeError, KeyError, RecursionError):
                    valid = False
                if valid:
                    rows.append({"table": table, "identity": identity, "revision": revision,
                        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(), "bytes": len(payload.encode())})
                else:
                    kept += 1
        observations = connection.execute("SELECT count(*) FROM observations").fetchone()[0]
        return rows, kept, observations
    finally:
        connection.close()


def _delete_verified_file(root, path, entry):
    """On Windows, delete the verified file handle with deny-sharing semantics."""
    _safe_path(root, path)
    if os.name != "nt":
        # Windows is the supported desktop platform. Refuse an unsafe portable
        # path-unlink fallback rather than promise a cross-process exclusion.
        raise OSError("当前平台不支持独占文件清理，已保留")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x00010000, 0, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("文件正被使用或无法独占，已保留")
    try:
        resolved = ctypes.create_unicode_buffer(32768)
        length = kernel.GetFinalPathNameByHandleW(handle, resolved, len(resolved), 0)
        if not length or length >= len(resolved) or os.path.normcase(resolved.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path)):
            raise ValueError("文件目标路径发生变化，已保留")
        _safe_path(root, path)
        if _fingerprint(path) != entry["fingerprint"]:
            raise ValueError("计划后文件发生变化，已保留")
        import msvcrt
        duplicate = wintypes.HANDLE()
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        process = kernel.GetCurrentProcess()
        if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
            raise OSError("无法核对独占文件，已保留")
        descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("计划后文件内容发生变化，已保留")
        valid = _preview_valid(path, raw) if entry["category"] == "word_previews" else _registry_valid(raw)
        if not valid:
            raise ValueError("执行时协议无法核验，已保留")
        disposition = wintypes.BOOL(True)
        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise OSError("文件无法清理，已保留")
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _pin_database(root, path):
    """Deny SQLite-file replacement while allowing SQLite's own row locking."""
    _safe_path(root, path)
    if os.name != "nt":
        raise OSError("当前平台不支持安全索引维护，已保留")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    handle = kernel.CreateFileW(str(path), 0x80, 3, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("索引文件正在使用或无法核验，已保留")
    try:
        target = ctypes.create_unicode_buffer(32768)
        length = kernel.GetFinalPathNameByHandleW(handle, target, len(target), 0)
        if (not length or length >= len(target)
                or os.path.normcase(target.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path))):
            raise ValueError("索引目标路径发生变化，已保留")
        _safe_path(root, path)
        yield
    finally:
        kernel.CloseHandle(handle)


class DesktopCacheMaintenance:
    def __init__(self, state_root, *, busy=None, invalidate=None):
        self.root = Path(state_root).absolute()
        self.busy, self.invalidate = busy, invalidate
        self._plans, self._lock = {}, threading.RLock()
        with _SERVICES_LOCK:
            _SERVICES.add(self)

    def _generations(self):
        path = self.root / _EPOCH_FILE
        _safe_path(self.root, path)
        if not path.exists():
            return {key: 0 for key in CATEGORIES}
        raw, _, _ = _read(self.root, path)
        if len(raw) > 16 * 1024:
            raise ValueError("缓存维护代次无法核验，请保留记录")
        try:
            value = json.loads(raw)
        except (RecursionError, UnicodeError, ValueError) as exc:
            raise ValueError("缓存维护代次无法解析，请保留记录") from exc
        if (not isinstance(value, dict) or set(value) != {"schema_version", "generations", "revision"}
                or value["schema_version"] != EPOCH_SCHEMA or not isinstance(value["generations"], dict)
                or set(value["generations"]) != set(CATEGORIES)
                or any(type(number) is not int or number < 0 for number in value["generations"].values())
                or value["revision"] != _digest({key: data for key, data in value.items() if key != "revision"})):
            raise ValueError("缓存维护代次协议无法核验，请保留记录")
        return value["generations"]

    def epoch(self, scope="word"):
        try:
            generations = self._generations()
        except (OSError, ValueError, RecursionError):
            # Unknown maintenance metadata blocks cleanup, not source reading.
            return _digest({"status": "unverified-generation", "scope": scope})
        keys = ("word_previews", "word_catalog") if scope == "word" else ("registry",)
        return _digest({key: generations[key] for key in keys})

    def _advance(self, affected):
        generations = self._generations()
        for key in affected:
            generations[key] += 1
        value = {"schema_version": EPOCH_SCHEMA, "generations": generations}
        value["revision"] = _digest(value)
        path = self.root / _EPOCH_FILE
        _safe_path(self.root, path)
        temporary = self.root / (".cache-generation-" + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            _safe_path(self.root, path)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return generations

    def plan(self):
        with self._lock:
            entries, kept = [], []
            candidates = [("registry", self.root / "desktop-registry-cache.v1.json")]
            preview_root = self.root / "word-question-previews"
            try:
                _safe_path(self.root, preview_root)
                if preview_root.is_dir():
                    candidates.extend(("word_previews", path) for path in sorted(preview_root.iterdir()))
            except (OSError, ValueError) as exc:
                kept.append({"path": "word-question-previews", "reason": str(exc)})
            candidates.append(("word_catalog", self.root / "word-question-catalog-index" / "catalog.sqlite3"))
            for category, path in candidates:
                if not path.exists() and not path.is_symlink():
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    raw, fingerprint, digest = _read(self.root, path)
                    entry = {"category": category, "path": relative, "bytes": len(raw),
                        "fingerprint": fingerprint, "sha256": digest, "rows": []}
                    if category == "word_catalog":
                        rows, unknown, observations = _index_rows(path)
                        if _fingerprint(path) != fingerprint or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                            raise ValueError("索引正在变化，已保留")
                        entry.update(rows=rows, bytes=sum(row["bytes"] for row in rows),
                            disk_bytes=len(raw), preserved_observations=observations, preserved_unknown_rows=unknown)
                        if observations or unknown:
                            kept.append({"path": relative, "reason": f"保留 {observations} 条历史观察及 {unknown} 条未知记录"})
                        if not rows:
                            continue
                    elif not (_preview_valid(path, raw) if category == "word_previews" else _registry_valid(raw)):
                        raise ValueError("不符合已知可重建缓存协议，已保留")
                    entries.append(entry)
                except (OSError, EOFError, ValueError, TypeError, KeyError, RecursionError, sqlite3.DatabaseError) as exc:
                    kept.append({"path": relative, "reason": str(exc) if isinstance(exc, ValueError) else "无法完整核验，已保留"})
            value = {"schema_version": PLAN_SCHEMA, "plan_id": uuid4().hex, "entries": entries,
                "generations": self._generations(),
                "preserved": kept, "categories": [{"key": key, "label": label,
                    "files": sum(entry["category"] == key for entry in entries),
                    "bytes": sum(entry["bytes"] for entry in entries if entry["category"] == key),
                    "rows": sum(len(entry["rows"]) for entry in entries if entry["category"] == key)} for key, label in CATEGORIES.items()],
                "notes": ["仅核验已知协议；其他文件不进入清理范围。", "索引字节为可重建记录大小；保留SQLite文件与历史观察，磁盘文件大小可能不减少。",
                    "原Word/PDF、教师标签与范围、学生资料、考试、费用账本、导出及损坏隔离记录保留。"]}
            value["revision"] = _digest(value)
            self._plans = {value["plan_id"]: deepcopy(value)}
            return value

    def execute(self, plan_id, revision, categories):
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None or plan["revision"] != revision:
                raise ValueError("清理计划已变化，请重新查看")
            selected = set(categories)
            if not selected or selected - CATEGORIES.keys():
                raise ValueError("请选择有效的缓存类别")
            entries = [entry for entry in plan["entries"] if entry["category"] in selected]
            result = {"plan_id": plan_id, "revision": revision, "items": [], "preserved": deepcopy(plan["preserved"]),
                "removed_files": 0, "removed_rows": 0, "removed_bytes": 0, "cleared_record_bytes": 0, "status": "complete"}
            if callable(self.busy) and self.busy():
                result["status"] = "busy"
                result["items"] = [self._outcome(entry, "busy", "后台任务正在使用资料，请等待完成后重新查看") for entry in entries]
                return result
            affected = set()
            try:
                with ExitStack() as stack:
                    lock = _shared_lock(self.root / "desktop-state.v1.json")
                    if not lock.acquire(blocking=False):
                        raise OSError("个人状态正在使用")
                    stack.callback(lock.release)
                    stack.enter_context(_state_file_lock(self.root / "desktop-state.v1.json"))
                    if self._generations() != plan["generations"]:
                        raise OSError("缓存维护代次已经变化")
                    # Publish a new read generation before any mutation. If it
                    # cannot be saved, no cache file/row is removed. A failed
                    # cleanup may conservatively require a harmless reread.
                    result["generations"] = self._advance(selected)
                    for entry in entries:
                        try:
                            path = self.root / entry["path"]
                            if entry["category"] == "word_catalog":
                                self._clear_rows(path, entry)
                                result["removed_rows"] += len(entry["rows"])
                                result["cleared_record_bytes"] += entry["bytes"]
                            else:
                                _delete_verified_file(self.root, path, entry)
                                result["removed_files"] += 1
                                result["removed_bytes"] += entry["bytes"]
                            affected.add(entry["category"])
                            result["items"].append(self._outcome(entry, "cleared", "已清理可重建记录"))
                        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                            result["items"].append(self._outcome(entry, "preserved", str(exc) if isinstance(exc, ValueError) else "正在使用或无法安全清理，已保留"))
            except (OSError, RuntimeError, ValueError):
                result["status"] = "busy"
                result["items"] = [self._outcome(entry, "busy", "状态正在使用或无法锁定，已保留") for entry in entries]
            if affected:
                with _SERVICES_LOCK:
                    owners = tuple(service for service in _SERVICES if service.root == self.root)
                for owner in owners:
                    if callable(owner.invalidate):
                        try:
                            owner.invalidate(affected)
                        except (OSError, RuntimeError, ValueError, AttributeError):
                            result["status"] = "partial"
                            result["items"].append({"path": "内存目录", "category": "word_catalog", "bytes": 0,
                                "rows": 0, "status": "preserved", "message": "缓存清理完成；此窗口刷新未完成，请重新打开工作台"})
            result["affected_categories"] = sorted(affected)
            if result["status"] == "complete" and any(item["status"] != "cleared" for item in result["items"]):
                result["status"] = "partial"
            if result["status"] != "busy":
                self._plans.pop(plan_id, None)
            return result

    @staticmethod
    def _outcome(entry, status, message):
        return {"path": entry["path"], "category": entry["category"], "bytes": entry["bytes"],
            "rows": len(entry["rows"]), "status": status, "message": message}

    def _clear_rows(self, path, entry):
        with _pin_database(self.root, path):
            return self._clear_pinned_rows(path, entry)

    def _clear_pinned_rows(self, path, entry):
        raw, fingerprint, digest = _read(self.root, path)
        if fingerprint != entry["fingerprint"] or digest != entry["sha256"]:
            raise ValueError("计划后索引发生变化，请重新查看；已保留")
        current, _, _ = _index_rows(path)
        if current != entry["rows"]:
            raise ValueError("索引记录发生变化，已保留")
        # Never unlink a live SQLite file, journal, WAL or SHM.
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            _safe_path(self.root, path)
            if _fingerprint(path) != fingerprint or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("执行前索引发生变化，已保留")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():
                raise ValueError("未知索引触发器，已保留")
            for row in entry["rows"]:
                table = row["table"]
                found = connection.execute(f"SELECT revision,payload FROM {table} WHERE identity=?", (row["identity"],)).fetchone()
                if (found is None or found[0] != row["revision"]
                        or hashlib.sha256(found[1].encode()).hexdigest() != row["payload_sha256"]
                        or not _row_valid(table, row["identity"], *found)):
                    raise ValueError("执行时索引记录发生变化，已保留")
                connection.execute(f"DELETE FROM {table} WHERE identity=? AND revision=?", (row["identity"], row["revision"]))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
