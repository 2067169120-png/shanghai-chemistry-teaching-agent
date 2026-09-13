from __future__ import annotations

"""Small persisted projection cache for the native desktop home screen."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


CACHE_SCHEMA = "shchem.desktop-registry-cache.v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


class DesktopRegistryCache:
    def __init__(self, state_root: str | Path) -> None:
        self.root = Path(state_root).resolve()
        self.path = self.root / "desktop-registry-cache.v1.json"

    def read(self, *, max_age_seconds: int = 6 * 60 * 60) -> dict[str, Any] | None:
        if max_age_seconds < 0 or not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("schema_version") != CACHE_SCHEMA:
            return None
        created_at = _parse_utc(value.get("created_at"))
        payload = value.get("registry")
        if created_at is None or not isinstance(payload, dict):
            return None
        age = (datetime.now(UTC) - created_at).total_seconds()
        if age < 0 or age > max_age_seconds:
            return None
        return dict(payload)

    def write(self, registry: Mapping[str, Any]) -> None:
        value = {
            "schema_version": CACHE_SCHEMA,
            "created_at": _utc_now(),
            "registry": dict(registry),
        }
        encoded = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".desktop-registry-cache-", suffix=".tmp", dir=self.root
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            return


__all__ = ["CACHE_SCHEMA", "DesktopRegistryCache"]
