"""Per-view read-only snapshots, without mutating the application's readers.

The evidence readers intentionally fully validate on each call. A native theme
view makes many related calls, so its private copies reuse each successful
validation for that one view. Frozen source bytes stay in those validated
snapshots; reopening a theme creates a new session and revalidates its inputs.
"""

from __future__ import annotations

from copy import copy
from functools import wraps
from threading import RLock
from typing import Any


def snapshot_reader_graph(readers: tuple[Any, ...]) -> tuple[Any, ...]:
    clones: dict[int, Any] = {}

    def clone(value: Any) -> Any:
        if isinstance(value, tuple):
            return tuple(clone(item) for item in value)
        if isinstance(value, list):
            return [clone(item) for item in value]
        if isinstance(value, dict):
            return {key: clone(item) for key, item in value.items()}
        cls = type(value)
        if not (
            cls.__module__.startswith("integrations.deeptutor_shchem_v1.")
            and cls.__name__.endswith("Reader")
            and hasattr(value, "__dict__")
        ):
            return value
        if id(value) in clones:
            return clones[id(value)]
        result = copy(value)
        clones[id(value)] = result
        for name, child in vars(value).items():
            setattr(result, name, clone(child))
        # Supplemental readers already memoize a validated registry on the
        # application instance. A new view/job must validate afresh, not inherit
        # that earlier registry and its lock from a shallow copy.
        if hasattr(result, "_registry_snapshot_cache"):
            result._registry_snapshot_cache = None
            result._snapshot_lock = RLock()
        for name in (
            "_snapshot",
            "_validated_catalog",
            "_catalogs",
            "_registry_snapshot",
        ):
            method = getattr(result, name, None)
            if callable(method):
                setattr(result, name, once(method))
        return result

    def once(method: Any) -> Any:
        lock = RLock()
        cache: list[Any] = []

        @wraps(method)
        def frozen() -> Any:
            with lock:
                if not cache:
                    # An exception is never cached as a successful validation.
                    cache.append(method())
                return cache[0]

        return frozen

    return tuple(clone(reader) for reader in readers)
