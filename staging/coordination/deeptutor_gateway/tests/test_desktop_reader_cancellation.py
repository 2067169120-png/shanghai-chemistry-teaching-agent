from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from integrations.deeptutor_shchem_v1.curriculum_workbench import (
    CurriculumWorkbenchReader,
)
from integrations.deeptutor_shchem_v1.public_kb import (
    ReadOnlyDataError,
    _checked_exact_path,
)
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled,
    ReaderThreadPoolExecutor,
    check_read_cancelled,
    read_cancel_scope,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_facade import (
    FakeThemeReader,
    build_facade,
    desktop_paths,  # noqa: F401 - pytest fixture
)


def _until_cancelled(started: Event) -> None:
    started.set()
    deadline = monotonic() + 4
    pause = Event()
    while monotonic() < deadline:
        check_read_cancelled()
        pause.wait(0.002)
    raise AssertionError("running reader did not receive cancellation")


def test_scope_restores_context_and_preserves_path_checks(tmp_path: Path) -> None:
    stop = Event()
    stop.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(stop):
        raise AssertionError("cancelled scope must not start")
    # A cancelled desktop must not poison an unrelated HTTP/read operation.
    check_read_cancelled()
    with pytest.raises(ReadOnlyDataError, match="allowlisted"):
        _checked_exact_path(tmp_path, "../outside")


def test_cancellation_reaches_running_nested_pool_and_skips_queued_work() -> None:
    stop, started, queued_ran = Event(), Event(), Event()

    def nested() -> None:
        with ReaderThreadPoolExecutor(max_workers=1) as inner:
            inner.submit(_until_cancelled, started).result(timeout=5)

    with read_cancel_scope(Event()):
        with ReaderThreadPoolExecutor(max_workers=1) as outer:
            with read_cancel_scope(stop):
                running = outer.submit(nested)
                queued = outer.submit(queued_ran.set)
            assert started.wait(2)
            stop.set()
            with pytest.raises(ReadCancelled):
                running.result(timeout=2)
            with pytest.raises(ReadCancelled):
                queued.result(timeout=2)
            assert not queued_ran.is_set()
            # Same reusable worker, a different request context.
            assert outer.submit(lambda: 42).result(timeout=2) == 42


def test_cancellation_after_read_does_not_publish_curriculum_cache(desktop_paths) -> None:
    stop = Event()

    class LastMomentReader:
        def catalog(self) -> dict:
            stop.set()
            return {"volumes": []}

    facade = build_facade(desktop_paths, curriculum=LastMomentReader())
    facade._reader_stop_event = stop
    with pytest.raises(ReadCancelled):
        facade.curriculum_catalog()
    assert facade._curriculum_catalog_cache is None
    facade.shutdown()


def test_reader_snapshot_does_not_cache_a_cancelled_build(desktop_paths, monkeypatch) -> None:
    reader = CurriculumWorkbenchReader(desktop_paths.shchem_root)
    stop = Event()
    value = object()

    def build() -> object:
        stop.set()
        return value

    monkeypatch.setattr(reader, "_build_snapshot", build)
    with pytest.raises(ReadCancelled), read_cancel_scope(stop):
        reader._snapshot()
    assert reader._snapshot_cache is None
    monkeypatch.setattr(reader, "_build_snapshot", lambda: value)
    assert reader._snapshot() is value


def test_registry_stop_aborts_nested_read_without_persisting_partial_status(desktop_paths) -> None:
    started = Event()

    class SlowTheme(FakeThemeReader):
        def groups(self, scope: str) -> dict:
            if scope == "master":
                with ReaderThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(_until_cancelled, started).result(timeout=5)
            return super().groups(scope)

    facade = build_facade(desktop_paths, theme=SlowTheme())
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(facade.load_desktop_registry)
        assert started.wait(2)
        facade.stop_background_readers()
        with pytest.raises(ReadCancelled):
            future.result(timeout=2)
    assert facade._registry_cache.read() is None
    with pytest.raises(ReadCancelled):
        facade.load_desktop_registry()
    facade.shutdown()


def test_shutdown_signals_before_waiting_for_student_initialization_lock(desktop_paths) -> None:
    started = Event()

    class SlowCurriculum:
        def catalog(self) -> dict:
            _until_cancelled(started)
            raise AssertionError("must be cancelled")

    facade = build_facade(desktop_paths, curriculum=SlowCurriculum())
    with ThreadPoolExecutor(max_workers=2) as caller:
        initializing = caller.submit(facade._student_manager_instance)
        assert started.wait(2)
        caller.submit(facade.shutdown).result(timeout=2)
        with pytest.raises(ReadCancelled):
            initializing.result(timeout=2)
    assert facade._student_analysis_manager is None
    assert facade._student_analysis_closed


@pytest.mark.parametrize("close_after_ms", [0, 1000])
def test_real_cold_start_closes_process_with_no_live_qt_workers(close_after_ms: int) -> None:
    pytest.importorskip("PySide6")
    root = Path(__file__).resolve().parents[4]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "staging.coordination.deeptutor_gateway.native_desktop_exit_smoke",
         "--close-after-ms", str(close_after_ms)],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=25,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    stages = {row["stage"]: row for line in result.stdout.splitlines()
              if line.startswith("{") for row in [json.loads(line)]}
    assert stages["close_returned"]["active_threads"] == 0
    assert stages["close_returned"]["seconds"] - stages["close_requested"]["seconds"] < 8
    assert "event_loop_returned" in stages
