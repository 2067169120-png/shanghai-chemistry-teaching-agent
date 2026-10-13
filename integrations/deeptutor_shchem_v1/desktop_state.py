from __future__ import annotations

"""Small, crash-safe personal state store for the native window."""

import hashlib
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_SCHEMA = "shchem.desktop-state.v1"
_SENSITIVE_FIELD_PARTS = ("api_key", "secret_value", "credential_value")
_LOCKS: dict[str, Any] = {}
_LOCKS_GUARD = threading.Lock()
_UNCHECKED = object()


class DesktopStateError(RuntimeError):
    pass


class BasketConflictError(DesktopStateError):
    """The displayed basket is no longer the durable basket."""


class DraftConflictError(DesktopStateError):
    """Only the addressed draft participates in this comparison."""


@dataclass(frozen=True)
class DraftSnapshot:
    draft_id: str
    record: dict[str, Any] | None
    revision: str
    content_sha256: str


def _draft_snapshot(value, draft_id):
    if not isinstance(draft_id, str) or not draft_id:
        raise DesktopStateError("草稿标识不正确。")
    record = value["drafts"].get(draft_id)
    if draft_id in value["drafts"] and not isinstance(record, dict):
        raise DesktopStateError("当前草稿无法读取，已保留原记录。")
    digest = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    revision = value.get("draft_revisions", {}).get(draft_id, "legacy:" + digest)
    return DraftSnapshot(draft_id, deepcopy(record), revision, digest)


def _shared_lock(path: Path):
    from .desktop_state_locking import canonical_lock_identity

    identity = canonical_lock_identity(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(identity, threading.RLock())


@contextmanager
def _state_file_lock(path: Path):
    """Every store writer uses one OS lock; crashes do not leave a live lock.

    A canonical named mutex on Windows keeps all processes in one namespace,
    including processes with different temporary directories or path aliases.
    Rejected edits do not create a personal state directory.
    """
    from .desktop_state_locking import process_state_lock

    try:
        with process_state_lock(path):
            yield
    except OSError as exc:
        raise DesktopStateError("个人状态正被使用或无法保存，请重新读取后重试。") from exc


def _basket_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows = value["basket"]
    if not isinstance(rows, list) or len(rows) > 100:
        raise DesktopStateError("题篮记录不完整，请保留原记录并核对备份。")
    keys = [row.get("key") if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise DesktopStateError("题篮项目标识缺失或重复，请保留原记录并核对备份。")
    return rows


@dataclass(frozen=True)
class BasketSnapshot:
    rows: tuple[dict[str, Any], ...]
    revision: str
    content_sha256: str
    history_reset: bool = False


def _basket_snapshot(value: dict[str, Any], *, history_reset=False) -> BasketSnapshot:
    rows = _basket_rows(value)
    digest = hashlib.sha256(json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    revision = value.get("basket_revision")
    if revision is None and "basket_revision" not in value:
        revision = "legacy:" + digest
    elif not isinstance(revision, str) or len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
        raise DesktopStateError("题篮修订记录无法核验，请保留原记录并核对备份。")
    return BasketSnapshot(tuple(deepcopy(rows)), revision, digest, history_reset)


def _same_basket(left: BasketSnapshot, right: BasketSnapshot) -> bool:
    return left.revision == right.revision and left.content_sha256 == right.content_sha256


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "window": {},
        "basket": [],
        "drafts": {},
        "updated_at": None,
    }


def _reject_sensitive_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            folded = str(key).casefold()
            if any(part in folded for part in _SENSITIVE_FIELD_PARTS):
                raise DesktopStateError("个人状态中不能保存模型密钥。")
            _reject_sensitive_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_sensitive_fields(nested)


class DesktopStateStore:
    def __init__(self, state_root: str | Path) -> None:
        self.root = Path(state_root).resolve()
        self.path = self.root / "desktop-state.v1.json"
        self._lock = _shared_lock(self.path)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_state()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DesktopStateError("个人工作台状态无法读取。") from exc
        if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA:
            raise DesktopStateError("个人工作台状态版本不受支持。")
        result = _default_state()
        result.update(value)
        if not isinstance(result["window"], dict):
            result["window"] = {}
        # Missing optional fields retain the legacy defaults. Present but
        # malformed durable data must not be "repaired" by an unrelated save:
        # that would overwrite recoverable questions or lesson drafts.
        if not isinstance(result["basket"], list):
            raise DesktopStateError("题篮数据格式异常，已停止保存；请保留原状态文件并核对备份。")
        if not isinstance(result["drafts"], dict):
            raise DesktopStateError("草稿数据格式异常，已停止保存；请保留原状态文件并核对备份。")
        revisions = result.get("draft_revisions", {})
        if (not isinstance(revisions, dict) or any(
            not isinstance(key, str) or not key or not isinstance(revision, str)
            or len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision)
            for key, revision in revisions.items()
        )):
            raise DesktopStateError("草稿修订记录无法核验，已停止保存。")
        _reject_sensitive_fields(result)
        if "teaching_templates" in result:
            from .desktop_teaching_templates import validate_library
            try:
                validate_library(result["teaching_templates"])
            except ValueError as exc:
                raise DesktopStateError("个人教学模板无法完整读取，已保留原记录。" ) from exc
        _basket_snapshot(result)  # Never silently drop malformed durable rows.
        return result

    def _disk_token(self):
        try:
            with self.path.open("rb") as handle:
                content = handle.read()
                stat = os.fstat(handle.fileno())
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DesktopStateError("个人工作台状态无法读取。") from exc
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns,
                hashlib.sha256(content).hexdigest())

    def _write_unlocked(self, value: dict[str, Any], *, expected_token=_UNCHECKED) -> None:
        _reject_sensitive_fields(value)
        if "teaching_templates" in value:
            from .desktop_teaching_templates import validate_library
            validate_library(value["teaching_templates"])
        value = deepcopy(value)
        value["schema_version"] = STATE_SCHEMA
        value["updated_at"] = utc_now()
        self.root.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".desktop-state-", suffix=".tmp", dir=self.root)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # Cooperating writers hold the OS lock. Also reject an observed
            # direct file replacement instead of overwriting it with our read.
            if expected_token is not _UNCHECKED and self._disk_token() != expected_token:
                raise BasketConflictError("个人状态在保存前发生变化，请重新读取后核对。")
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise DesktopStateError("个人工作台状态无法保存。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked())

    def _update(self, operation: Callable[[dict[str, Any]], None | bool], *, basket_write=False, finalize=None) -> dict[str, Any]:
        with self._lock, _state_file_lock(self.path):
            token = self._disk_token()
            value = self._read_unlocked()
            if self._disk_token() != token:
                raise BasketConflictError("个人状态在读取时发生变化，请重新读取后核对。")
            before = deepcopy(value["basket"])
            old_drafts = deepcopy(value["drafts"])
            if operation(value) is False:
                return deepcopy(value)
            if basket_write or value["basket"] != before:
                value["basket_revision"] = uuid.uuid4().hex
            changed_drafts = {key for key in old_drafts.keys() | value["drafts"].keys()
                              if old_drafts.get(key, _UNCHECKED) != value["drafts"].get(key, _UNCHECKED)}
            if changed_drafts:
                revisions = dict(value.get("draft_revisions", {}))
                for key in changed_drafts:
                    revisions[key] = uuid.uuid4().hex
                value["draft_revisions"] = revisions
            if finalize is not None:
                finalize(value)
            _basket_snapshot(value)
            self._write_unlocked(value, expected_token=token)
            return deepcopy(value)

    def window_state(self) -> dict[str, str]:
        value = self.snapshot().get("window")
        return {key: item for key, item in value.items()
                if key in {"geometry", "layout"} and isinstance(item, str)}

    def save_window_state(self, *, geometry: str, layout: str) -> None:
        def operation(value: dict[str, Any]) -> None:
            value["window"] = {"geometry": geometry, "layout": layout}
        self._update(operation)

    def basket(self) -> list[dict[str, Any]]:
        return list(self.basket_snapshot().rows)

    def basket_snapshot(self) -> BasketSnapshot:
        return _basket_snapshot(self.snapshot())

    def open_basket_session(self) -> BasketEditSession:
        return BasketEditSession(self)

    def _compare_basket(self, expected: BasketSnapshot, operation) -> BasketSnapshot:
        def checked(value):
            if not _same_basket(_basket_snapshot(value), expected):
                raise BasketConflictError("题篮已被其他操作更新，请重新读取；本窗口的撤销记录已失效。")
            return operation(value)
        return _basket_snapshot(self._update(checked, basket_write=True))

    def add_to_basket(self, item: dict[str, Any]) -> int:
        return self.add_many_to_basket([item])

    def clear_basket(self) -> None:
        def operation(value):
            if not value["basket"]:
                return False
            value["basket"] = []
        self._update(operation, basket_write=True)

    def add_many_to_basket(self, items: list[dict[str, Any]]) -> int:
        """Add a validated batch atomically, without dropping older selections."""
        if not isinstance(items, list) or not items or len(items) > 100:
            raise DesktopStateError("请一次加入 1 至 100 个题篮项目。")
        keys: set[str] = set()
        for item in items:
            key = item.get("key") if isinstance(item, dict) else None
            if not isinstance(key, str) or not key or key in keys:
                raise DesktopStateError("题篮项目标识缺失或重复。")
            keys.add(key)
        incoming = deepcopy(items)
        _reject_sensitive_fields(incoming)
        def operation(value: dict[str, Any]) -> None | bool:
            if any(not isinstance(entry, dict) for entry in value["basket"]):
                raise DesktopStateError("现有题篮记录不完整，未加入本批题目。")
            current_by_key = {entry["key"]: entry for entry in value["basket"]}
            # Re-adding only identical items is not an implicit reorder, even
            # when those items are at the beginning/middle of the basket.
            if all(current_by_key.get(entry["key"]) == entry for entry in incoming):
                return False
            basket = [entry for entry in value["basket"] if entry.get("key") not in keys]
            basket.extend(incoming)
            if len(basket) > 100:
                raise DesktopStateError("题篮最多保留 100 个项目，请先移除部分题目；现有题目未删减。")
            if basket == value["basket"]:
                return False
            value["basket"] = basket
        return len(self._update(operation, basket_write=True)["basket"])

    def remove_basket_item(self, key: str) -> int:
        """Remove exactly one identity without replacing the rest of the state."""
        if not isinstance(key, str) or not key:
            raise DesktopStateError("题篮项目标识不正确。")
        def operation(value):
            basket = [row for row in value["basket"] if row.get("key") != key]
            if basket == value["basket"]:
                return False
            value["basket"] = basket
        return len(self._update(operation, basket_write=True)["basket"])

    def move_basket_item(self, key: str, delta: int) -> int:
        if not isinstance(key, str) or not key or type(delta) is not int or delta not in (-1, 1):
            raise DesktopStateError("题篮排序参数不正确。")
        def operation(value):
            rows = value["basket"]
            index = next((i for i, row in enumerate(rows) if row.get("key") == key), None)
            if index is not None and 0 <= index + delta < len(rows):
                rows[index], rows[index + delta] = rows[index + delta], rows[index]
            else:
                return False
        return len(self._update(operation, basket_write=True)["basket"])

    def save_studio_favorites(self, keys: list[str]) -> None:
        if not isinstance(keys, list) or any(not isinstance(key, str) or len(key) > 60 for key in keys):
            raise DesktopStateError("模板收藏标识不正确。")
        def operation(value):
            studio = dict(value.get("studio", {}))
            studio["favorites"] = sorted(set(keys))
            value["studio"] = studio
        self._update(operation)

    def save_draft(self, draft_id: str, payload: dict[str, Any]) -> None:
        if not draft_id or not isinstance(draft_id, str):
            raise DesktopStateError("草稿标识不正确。")
        _reject_sensitive_fields(payload)
        def operation(value: dict[str, Any]) -> None:
            if value["drafts"].get(draft_id, _UNCHECKED) == payload:
                return False
            drafts = dict(value["drafts"])
            drafts[draft_id] = deepcopy(payload)
            value["drafts"] = drafts
        self._update(operation)

    def draft_snapshot(self, draft_id: str) -> DraftSnapshot:
        return _draft_snapshot(self.snapshot(), draft_id)

    def compare_draft(self, expected: DraftSnapshot, record: dict[str, Any], *, guard=None) -> DraftSnapshot:
        """CAS one draft under the same OS lock as every legacy state writer."""
        if not isinstance(record, dict):
            raise DesktopStateError("草稿数据格式不正确。")
        _reject_sensitive_fields(record)
        def operation(value):
            current = _draft_snapshot(value, expected.draft_id)
            if (current.revision, current.content_sha256) != (expected.revision, expected.content_sha256):
                raise DraftConflictError("当前草稿已被其他操作修改，请重新读取。")
            if guard is not None:
                guard(value)
            if current.record == record:
                return False
            value["drafts"][expected.draft_id] = deepcopy(record)
        return _draft_snapshot(self._update(operation), expected.draft_id)


class BasketEditSession:
    """Durable bounded moves/removals; callers cannot supply restore rows."""

    HISTORY_LIMIT = 30

    def __init__(self, store: DesktopStateStore, source_validator=None):
        from .desktop_paper_history import BASKET_HISTORY_ID, PaperHistory
        self._store = store
        self._source_validator = source_validator
        self._journal = PaperHistory(store, BASKET_HISTORY_ID, "public-basket")
        self._current: BasketSnapshot | None = None
        self._closed = False

    @property
    def undo_count(self) -> int:
        return len(self._journal.undo)

    @property
    def undo_action(self) -> str:
        return self._journal.undo[-1]["action"] if self._journal.undo else ""

    @property
    def redo_count(self) -> int:
        return len(self._journal.redo)

    @property
    def redo_action(self) -> str:
        return self._journal.redo[-1]["action"] if self._journal.redo else ""

    def invalidate(self) -> None:
        self._current = None
        self._journal.clear()

    def close(self) -> None:
        self.invalidate()
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise DesktopStateError("本次题篮窗口已关闭，请重新打开。")

    def read(self) -> BasketSnapshot:
        self._require_open()
        try:
            value = self._store.snapshot()
            current = _basket_snapshot(value)
            self._journal.read(value, current, {}, lambda rows: _basket_rows({"basket": rows}))
        except Exception:
            self.invalidate()
            raise
        reset = bool(self._current and not _same_basket(self._current, current))
        self._current = current
        return BasketSnapshot(deepcopy(current.rows), current.revision, current.content_sha256,
                              reset or self._journal.reset)

    def _verified_sources(self, rows):
        if self._source_validator is not None:
            return self._source_validator(deepcopy(list(rows)))
        return None

    def _commit(self, rows, undo, redo, expected_sources=None):
        actual_sources = self._verified_sources(rows)
        if expected_sources is not None and actual_sources != expected_sources:
            raise BasketConflictError("原题内容或来源依据已变化，不能恢复旧题篮记录；已保存题篮保留。")
        def operation(value):
            if not _same_basket(_basket_snapshot(value), self._current):
                raise BasketConflictError("题篮已被其他操作更新，请重新读取；当前状态未覆盖。")
            self._journal.stage(value, undo, redo)
            value["basket"] = deepcopy(rows)
        try:
            value = self._store._update(operation, basket_write=True,
                finalize=lambda value: self._journal.finalize(value, _basket_snapshot(value), {}))
        except OSError as exc:
            raise DesktopStateError("题篮与撤销历史保存未能确认，请重新读取后核对。") from exc
        self._current = _basket_snapshot(value)
        self._journal.read(value, self._current, {}, lambda rows: _basket_rows({"basket": rows}))
        return self._current

    def change(self, key: str, delta: int) -> dict[str, Any]:
        self._require_open()
        if self._current is None:
            raise DesktopStateError("请先重新读取题篮。")
        if not isinstance(key, str) or not key or type(delta) is not int or delta not in (-1, 0, 1):
            raise DesktopStateError("题篮操作参数不正确。")
        before = self._current
        action = {-1: "上移", 1: "下移", 0: "移出"}[delta]

        def operation(rows):
            index = next((i for i, row in enumerate(rows) if row["key"] == key), None)
            if index is None:
                raise BasketConflictError("选中的题目已不在当前题篮，请重新读取。")
            if delta:
                target = index + delta
                if not 0 <= target < len(rows):
                    return False
                rows[index], rows[target] = rows[target], rows[index]
            else:
                rows.pop(index)

        try:
            from .desktop_paper_history import entry
            rows = deepcopy(list(before.rows))
            if operation(rows) is False:
                current = self._store._compare_basket(before, lambda value: False)
            else:
                verified = self._verified_sources(before.rows)
                expected = {row["key"]: verified[row["key"]] for row in rows} if verified is not None else None
                current = self._commit(rows, self._journal.undo + [entry(list(before.rows), action, key, verified)], [], expected)
        except Exception:
            self.invalidate()
            raise
        changed = not _same_basket(before, current)
        self._current = current
        return {"changed": changed, "count": len(current.rows), "selected_key": key, "action": action}

    def undo(self) -> dict[str, Any]:
        self._require_open()
        return self._travel(False)

    def redo(self) -> dict[str, Any]:
        return self._travel(True)

    def _travel(self, redo):
        self._require_open()
        stack = self._journal.redo if redo else self._journal.undo
        if self._current is None or not stack:
            raise DesktopStateError("当前题篮没有可重做的操作。" if redo else "当前题篮没有可撤销的操作。")
        step = stack[-1]
        try:
            if self._source_validator is not None and step["source_basis"] is None:
                raise BasketConflictError("旧撤销记录缺少可核验的原题依据，已保存题篮保留；请重新核对来源。")
            from .desktop_paper_history import entry
            reverse = entry(list(self._current.rows), step["action"], step["selected"],
                            self._verified_sources(self._current.rows))
            undo = self._journal.undo + [reverse] if redo else self._journal.undo[:-1]
            forward = self._journal.redo[:-1] if redo else self._journal.redo + [reverse]
            current = self._commit(step["state"], undo, forward, step["source_basis"])
        except Exception:
            self.invalidate()
            raise
        self._current = current
        return {"changed": True, "count": len(current.rows), "selected_key": step["selected"], "action": step["action"]}


__all__ = ["STATE_SCHEMA", "DesktopStateError", "BasketConflictError", "BasketSnapshot", "DraftConflictError", "DraftSnapshot",
           "BasketEditSession", "DesktopStateStore", "utc_now"]
