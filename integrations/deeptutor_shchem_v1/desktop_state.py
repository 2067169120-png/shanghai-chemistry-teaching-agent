from __future__ import annotations

"""Small, crash-safe personal state store for the native window."""

import json
import os
import tempfile
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_SCHEMA = "shchem.desktop-state.v1"
_SENSITIVE_FIELD_PARTS = ("api_key", "secret_value", "credential_value")


class DesktopStateError(RuntimeError):
    pass


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
        self._lock = threading.RLock()

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
        if not isinstance(result["basket"], list):
            result["basket"] = []
        if not isinstance(result["drafts"], dict):
            result["drafts"] = {}
        _reject_sensitive_fields(result)
        return result

    def _write_unlocked(self, value: dict[str, Any]) -> None:
        _reject_sensitive_fields(value)
        value = deepcopy(value)
        value["schema_version"] = STATE_SCHEMA
        value["updated_at"] = utc_now()
        self.root.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".desktop-state-", suffix=".tmp", dir=self.root
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
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

    def _update(self, operation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            value = self._read_unlocked()
            operation(value)
            self._write_unlocked(value)
            return deepcopy(value)

    def window_state(self) -> dict[str, str]:
        value = self.snapshot().get("window")
        return {
            key: item
            for key, item in value.items()
            if key in {"geometry", "layout"} and isinstance(item, str)
        }

    def save_window_state(self, *, geometry: str, layout: str) -> None:
        def operation(value: dict[str, Any]) -> None:
            value["window"] = {"geometry": geometry, "layout": layout}

        self._update(operation)

    def basket(self) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in self.snapshot().get("basket", [])
            if isinstance(item, dict)
        ]

    def add_to_basket(self, item: dict[str, Any]) -> int:
        return self.add_many_to_basket([item])

    def clear_basket(self) -> None:
        self._update(lambda value: value.__setitem__("basket", []))

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

        def operation(value: dict[str, Any]) -> None:
            if any(not isinstance(entry, dict) for entry in value["basket"]):
                raise DesktopStateError("现有题篮记录不完整，未加入本批题目。")
            basket = [
                entry for entry in value["basket"] if entry.get("key") not in keys
            ]
            basket.extend(incoming)
            if len(basket) > 100:
                raise DesktopStateError(
                    "题篮最多保留 100 个项目，请先移除部分题目；现有题目未删减。"
                )
            value["basket"] = basket

        return len(self._update(operation)["basket"])

    def save_draft(self, draft_id: str, payload: dict[str, Any]) -> None:
        if not draft_id or not isinstance(draft_id, str):
            raise DesktopStateError("草稿标识不正确。")
        _reject_sensitive_fields(payload)

        def operation(value: dict[str, Any]) -> None:
            drafts = dict(value["drafts"])
            drafts[draft_id] = deepcopy(payload)
            value["drafts"] = drafts

        self._update(operation)


__all__ = [
    "STATE_SCHEMA",
    "DesktopStateError",
    "DesktopStateStore",
    "utc_now",
]
