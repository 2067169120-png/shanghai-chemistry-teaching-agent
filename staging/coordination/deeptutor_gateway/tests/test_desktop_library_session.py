from concurrent.futures import ThreadPoolExecutor
from threading import RLock

import pytest

from integrations.deeptutor_shchem_v1.desktop_library_session import (
    snapshot_reader_graph,
)


class FixtureReader:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.child = None

    def _snapshot(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("validation failed")
        return {"version": self.calls}


FixtureReader.__module__ = "integrations.deeptutor_shchem_v1.fixture_for_test"


def test_private_reader_copy_does_not_patch_shared_reader():
    original = FixtureReader()
    (first,) = snapshot_reader_graph((original,))
    assert first is not original
    assert first._snapshot() is first._snapshot()
    assert first.calls == 1
    assert original.calls == 0
    assert original._snapshot() != original._snapshot()
    assert original.calls == 2


def test_each_view_validates_again_and_graph_identity_is_preserved():
    original, child = FixtureReader(), FixtureReader()
    original.child = child
    child.child = original
    first, second = snapshot_reader_graph((original, child))
    assert first.child is second
    assert second.child is first
    (new_first,) = snapshot_reader_graph((original,))
    assert new_first is not first
    first._snapshot()
    new_first._snapshot()
    assert first.calls == new_first.calls == 1
    assert original.calls == 0


def test_failed_validation_not_cached_as_success():
    (reader,) = snapshot_reader_graph((FixtureReader(),))
    reader.fail = True
    with pytest.raises(RuntimeError):
        reader._snapshot()
    reader.fail = False
    assert reader._snapshot() == {"version": 2}
    assert reader.calls == 2


def test_parallel_reads_share_one_successful_validation():
    (reader,) = snapshot_reader_graph((FixtureReader(),))
    with ThreadPoolExecutor(max_workers=4) as executor:
        snapshots = list(executor.map(lambda _: reader._snapshot(), range(20)))
    assert all(value is snapshots[0] for value in snapshots)
    assert reader.calls == 1


def test_prewarmed_registry_does_not_leak_into_new_view():
    class CachedReader(FixtureReader):
        def __init__(self):
            super().__init__()
            self._snapshot_lock = RLock()
            self._registry_snapshot_cache = None

        def _registry_snapshot(self):
            with self._snapshot_lock:
                if self._registry_snapshot_cache is None:
                    self._registry_snapshot_cache = self._snapshot()
                return self._registry_snapshot_cache

    CachedReader.__module__ = FixtureReader.__module__
    original = CachedReader()
    warmed = original._registry_snapshot()
    (reader,) = snapshot_reader_graph((original,))
    assert reader._registry_snapshot_cache is None
    assert reader._snapshot_lock is not original._snapshot_lock
    fresh = reader._registry_snapshot()
    assert fresh is reader._registry_snapshot()
    assert fresh != warmed
    assert original._registry_snapshot() is warmed
    assert original.calls == 1
