from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)

_TRACE_LOCK = Lock()
_TRACE: list[tuple[str, int, object]] = []


def _record(kind: str, reader: object, value: object) -> None:
    with _TRACE_LOCK:
        _TRACE.append((kind, id(reader), value))


def _reset_trace() -> None:
    with _TRACE_LOCK:
        _TRACE.clear()


def _events(kind: str) -> list[tuple[str, int, object]]:
    with _TRACE_LOCK:
        return [event for event in _TRACE if event[0] == kind]


class CloneThemeReader:
    """A reader-shaped fixture whose expensive snapshot is called twice."""

    def __init__(self, *, revision: int = 1) -> None:
        self.revision = revision
        self.fail = False

    def _snapshot(self) -> dict[str, int]:
        _record("theme_snapshot", self, self.revision)
        if self.fail:
            raise RuntimeError("fixture validation failed")
        return {"revision": self.revision}

    def groups(self, scope: str) -> dict[str, object]:
        first = self._snapshot()
        second = self._snapshot()
        assert first is second
        _record("theme_groups", self, scope)
        return {
            "scope": scope,
            "revision": first["revision"],
            "snapshot_identity": id(first),
        }


class CloneSupplementalReader:
    """A supplemental reader-shaped fixture using the production cache name."""

    def __init__(self, *, revision: int = 1) -> None:
        self.revision = revision
        self.fail = False
        self._registry_snapshot_cache = None
        self._snapshot_lock = Lock()

    def _registry_snapshot(self) -> dict[str, int]:
        _record("supplemental_snapshot", self, self.revision)
        if self.fail:
            raise RuntimeError("fixture supplemental validation failed")
        return {"revision": self.revision}

    def theme_groups(self) -> dict[str, object]:
        first = self._registry_snapshot()
        second = self._registry_snapshot()
        assert first is second
        _record("supplemental_groups", self, "supplemental")
        return {
            "scope": "supplemental",
            "revision": first["revision"],
            "snapshot_identity": id(first),
        }


# snapshot_reader_graph deliberately recognizes production reader modules only.
# Mark these fixtures as reader implementations without touching production code.
CloneThemeReader.__module__ = (
    "integrations.deeptutor_shchem_v1.test_desktop_catalog_read_session"
)
CloneSupplementalReader.__module__ = (
    "integrations.deeptutor_shchem_v1.test_desktop_catalog_read_session"
)


def _facade(theme: CloneThemeReader, supplemental: CloneSupplementalReader):
    facade = object.__new__(DesktopWorkbenchFacade)
    facade._themes = theme
    facade._supplemental = supplemental
    return facade


@pytest.fixture(autouse=True)
def _clean_trace() -> None:
    _reset_trace()
    yield
    _reset_trace()


def test_theme_scope_reuses_snapshot_within_call_but_revalidates_next_call() -> None:
    theme = CloneThemeReader(revision=1)
    facade = _facade(theme, CloneSupplementalReader())

    first = facade._load_theme_scope("master")
    assert first["scope"] == "master"
    assert first["revision"] == 1
    assert len(_events("theme_snapshot")) == 1
    assert len(_events("theme_groups")) == 1

    # The source reader is not patched with the per-call wrapper.
    assert "_snapshot" not in theme.__dict__
    theme.revision = 2
    second = facade._load_theme_scope("wave1")
    assert second["scope"] == "wave1"
    assert second["revision"] == 2
    assert len(_events("theme_snapshot")) == 2
    assert len(_events("theme_groups")) == 2


def test_theme_scope_next_call_does_not_reuse_a_previous_success_after_failure() -> None:
    theme = CloneThemeReader(revision=1)
    facade = _facade(theme, CloneSupplementalReader())

    assert facade._load_theme_scope("master")["revision"] == 1
    theme.revision = 2
    theme.fail = True

    with pytest.raises(RuntimeError, match="fixture validation failed"):
        facade._load_theme_scope("master")

    assert len(_events("theme_snapshot")) == 2
    assert "_snapshot" not in theme.__dict__


def test_supplemental_theme_groups_reuses_registry_snapshot_within_call() -> None:
    supplemental = CloneSupplementalReader(revision=3)
    facade = _facade(CloneThemeReader(), supplemental)

    result = facade._load_theme_scope("supplemental")

    assert result["scope"] == "supplemental"
    assert result["revision"] == 3
    assert len(_events("supplemental_snapshot")) == 1
    assert len(_events("supplemental_groups")) == 1
    assert "_registry_snapshot" not in supplemental.__dict__


def test_scope_routes_are_isolated_and_invalid_scope_is_rejected() -> None:
    theme = CloneThemeReader()
    supplemental = CloneSupplementalReader()
    facade = _facade(theme, supplemental)

    assert facade._load_theme_scope("master")["scope"] == "master"
    assert facade._load_theme_scope("wave1")["scope"] == "wave1"
    assert facade._load_theme_scope("supplemental")["scope"] == "supplemental"
    assert [event[2] for event in _events("theme_groups")] == ["master", "wave1"]
    assert [event[2] for event in _events("supplemental_groups")] == [
        "supplemental"
    ]

    with pytest.raises(DesktopFacadeError, match="题库范围不正确"):
        facade._load_theme_scope("unknown")


def test_concurrent_calls_use_independent_per_call_snapshots() -> None:
    theme = CloneThemeReader(revision=7)
    facade = _facade(theme, CloneSupplementalReader())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: facade._load_theme_scope("master"), range(2))
        )

    assert [result["revision"] for result in results] == [7, 7]
    # Each invocation owns a fresh clone/session; no cross-call cache is shared.
    assert len(_events("theme_snapshot")) == 2
    assert len(_events("theme_groups")) == 2
    assert len({event[1] for event in _events("theme_snapshot")}) == 2
    assert "_snapshot" not in theme.__dict__
