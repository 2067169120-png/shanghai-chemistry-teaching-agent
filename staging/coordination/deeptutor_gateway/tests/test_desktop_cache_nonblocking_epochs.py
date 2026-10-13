"""GUI maintenance gates must never wait for an old source reader's lock."""

import threading
import time
import hashlib
from pathlib import Path

import pytest

from test_desktop_cache_callback_epochs import word_context as word_context
from test_desktop_cache_maintenance import qt_app as qt_app
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths, _facade, FakeProviderStore,
)
from test_word_questions_service import _import
from integrations.deeptutor_shchem_v1.desktop_state import _shared_lock


@pytest.mark.parametrize("gate", ["epoch", "query", "late_callback", "show"])
def test_actual_gui_epoch_gates_do_not_wait_for_old_word_service(
    qt_app, word_context, gate, monkeypatch,
):
    facade, _, _, _, dialog, question = word_context
    old = facade._word_questions()
    requested = facade.desktop_cache_epoch("word")
    held, release, gate_started = threading.Event(), threading.Event(), threading.Event()
    unlocked = threading.Event()

    def hold_old_reader():
        with old._lock:
            held.set()
            assert release.wait(5)
        unlocked.set()

    reader = threading.Thread(target=hold_old_reader)
    reader.start()
    assert held.wait(5)
    # A second process can publish maintenance while this captured reader is
    # still alive. No cache deletion is needed to exercise the actual GUI gate.
    facade._cache_maintenance_service()._advance({"word_catalog"})
    # Keep this test on the GUI gate, without launching replacement work.
    monkeypatch.setattr(dialog, "_load_catalog", lambda: None)

    def watchdog():
        assert gate_started.wait(5)
        if not release.wait(0.4):
            release.set()

    watchdog_thread = threading.Thread(target=watchdog)
    watchdog_thread.start()
    try:
        gate_started.set()
        start = time.monotonic()
        if gate == "epoch":
            assert facade.desktop_cache_epoch("word") != requested
        elif gate == "query":
            dialog._filter_items()
        elif gate == "late_callback":
            assert not dialog._maintenance_callback_current(requested)
        else:
            dialog.show()
        duration = time.monotonic() - start
        assert duration < 0.15, f"{gate} blocked GUI for {duration:.3f}s"
        # An actual queued GUI heartbeat can run while the old reader still
        # owns its lock, including after query/show/callback invalidation.
        from PySide6.QtCore import QTimer
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qt_app.processEvents()
        assert heartbeat
        assert not unlocked.is_set(), "gate waited until the old reader drained"
        if gate != "epoch":
            assert dialog._cache_projection_stale and not dialog._items
            assert dialog._selected[question["key"]] == question["revision"]
            assert dialog._points[question["key"]] == 7
    finally:
        release.set()
        reader.join(5)
        watchdog_thread.join(5)
    assert not reader.is_alive() and not watchdog_thread.is_alive()


def test_epoch_is_read_only_while_word_state_and_maintenance_locks_are_held(
    desktop_paths, tmp_path,
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    maintenance = facade._cache_maintenance_service()
    held, release = threading.Event(), threading.Event()

    def hold_locks():
        with old._lock, facade.state_store._lock, maintenance._lock, facade._library_view_lock:
            held.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold_locks)
    holder.start()
    assert held.wait(5)
    try:
        maintenance._advance({"word_catalog", "registry"})
        before = {key: value for key, value in facade.__dict__.items()}
        start = time.monotonic()
        word_epoch = facade.desktop_cache_epoch("word")
        registry_epoch = facade.desktop_cache_epoch("registry")
        assert time.monotonic() - start < 0.15
        assert word_epoch == facade.desktop_cache_epoch("word")
        assert registry_epoch == facade.desktop_cache_epoch("registry")
        assert facade.__dict__ == before
        assert facade.__dict__["_word_question_service"] is old
        assert not release.is_set()
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive()


def test_factory_detaches_active_old_reader_and_delayed_notice_keeps_new_service(
    desktop_paths, tmp_path,
):
    facade, original, provider = _import(desktop_paths, tmp_path)
    first = facade.word_question_listing()
    old = facade._word_questions()
    old_provider = first["items"][0]["search_provider"]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (original, facade.state_store.path)}
    held, release = threading.Event(), threading.Event()
    completed = []

    def active_reader():
        with old._lock:
            held.set()
            assert release.wait(5)
            # The captured old operation still finishes normally after detach.
            old._cache["late-old-result"] = object()
            completed.append(True)

    reader = threading.Thread(target=active_reader)
    reader.start()
    assert held.wait(5)
    try:
        maintenance = facade._cache_maintenance_service()
        maintenance._advance({"word_catalog"})
        start = time.monotonic()
        fresh = facade._word_questions()
        assert time.monotonic() - start < 0.15
        assert fresh is not old and not completed
        listing = facade.word_question_listing()
        assert listing["items"][0]["search_provider"] is not old_provider
        assert listing["items"][0]["search_provider"].matches(
            listing["items"][0]["key"], listing["items"][0]["revision"], "电解质"
        )
        fresh._cache["fresh"] = object()
        maintenance.invalidate({"word_catalog"})
        assert facade._word_questions() is fresh and "fresh" in fresh._cache
        assert not facade._reader_stop_event.is_set()
    finally:
        release.set()
        reader.join(5)
    assert not reader.is_alive() and completed == [True]
    assert "late-old-result" in old._cache
    assert facade._word_questions() is fresh
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in before.items())
    assert provider.borrow_calls == 0


def test_invalidator_cannot_deadlock_state_owner_and_old_reader(
    desktop_paths, tmp_path,
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    state_lock = _shared_lock(facade.state_store.path)
    service_held, state_held = threading.Event(), threading.Event()
    invalidated, reader_finished = threading.Event(), threading.Event()
    timings = []

    def state_owner():
        assert service_held.wait(5)
        with state_lock:
            state_held.set()
            start = time.monotonic()
            facade._invalidate_maintained_caches({"word_catalog"})
            timings.append(time.monotonic() - start)
            invalidated.set()

    def source_reader():
        with old._lock:
            service_held.set()
            assert state_held.wait(5)
            # This is the real service->state order used by range edits.
            facade.state_store.snapshot()
        reader_finished.set()

    owner = threading.Thread(target=state_owner, daemon=True)
    reader = threading.Thread(target=source_reader, daemon=True)
    reader.start()
    owner.start()
    owner.join(2)
    reader.join(2)
    assert not owner.is_alive() and not reader.is_alive(), "state/service lock-order deadlock"
    assert invalidated.is_set() and reader_finished.is_set()
    assert timings[0] < 0.15
    assert facade._word_questions() is not old


def test_epoch_rejects_oversize_before_open_and_never_uses_cache_hash_reader(
    desktop_paths, tmp_path, monkeypatch,
):
    from integrations.deeptutor_shchem_v1 import desktop_cache_maintenance as cache
    facade, _, _ = _import(desktop_paths, tmp_path)
    maintenance = facade._cache_maintenance_service()
    epoch_path = desktop_paths.state_root / "desktop-cache-generation.v1.json"
    epoch_path.write_bytes(b"x" * (cache._MAX_EPOCH_BYTES + 1))
    original_open = Path.open

    def no_oversize_open(path, *args, **kwargs):
        assert path != epoch_path, "oversized epoch metadata was opened"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_oversize_open)
    monkeypatch.setattr(cache, "_read", lambda *_: pytest.fail("epoch entered bulk cache hash reader"))
    unknown = facade.desktop_cache_epoch("word")
    assert len(unknown) == 64 and facade.desktop_cache_epoch("word") == unknown
    with pytest.raises(ValueError):
        maintenance._generations()


def test_epoch_valid_record_uses_bounded_metadata_read_only(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_cache_maintenance as cache
    facade, _, _ = _import(desktop_paths, tmp_path)
    maintenance = facade._cache_maintenance_service()
    initial = facade.desktop_cache_epoch("word")
    maintenance._advance({"word_catalog"})
    monkeypatch.setattr(cache, "_read", lambda *_: pytest.fail("epoch entered bulk cache hash reader"))
    assert facade.desktop_cache_epoch("word") != initial


def test_concurrent_first_epoch_read_publishes_one_maintenance_plan_owner(
    desktop_paths, monkeypatch,
):
    from integrations.deeptutor_shchem_v1.desktop_cache_maintenance import DesktopCacheMaintenance
    facade = _facade(desktop_paths, FakeProviderStore(configured=False))
    barrier = threading.Barrier(2)
    original_init = DesktopCacheMaintenance.__init__
    owners, errors = [], []

    def gated_init(instance, *args, **kwargs):
        barrier.wait(timeout=5)
        original_init(instance, *args, **kwargs)

    monkeypatch.setattr(DesktopCacheMaintenance, "__init__", gated_init)

    def initialize():
        try:
            facade.desktop_cache_epoch("word")
            owners.append(facade._cache_maintenance_service())
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=initialize) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert not any(worker.is_alive() for worker in workers)
    assert not errors and len(owners) == 2
    assert owners[0] is owners[1] is facade._cache_maintenance_service()
    plan = owners[0].plan()
    assert owners[1]._plans[plan["plan_id"]]["revision"] == plan["revision"]


@pytest.mark.parametrize("late", ["factory", "notification"])
def test_older_generation_publication_cannot_erase_later_factory_service(
    desktop_paths, tmp_path, monkeypatch, late,
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    maintenance = facade._cache_maintenance_service()
    maintenance._advance({"word_catalog"})
    slot_lock = facade._word_cache_identity_lock()
    paused, release = threading.Event(), threading.Event()
    slow_thread_ids, errors, returned = [], [], []

    class BeforeSlotPublication:
        def __enter__(self):
            paused.set()
            assert release.wait(5)
            slot_lock.acquire()

        def __exit__(self, *_args):
            slot_lock.release()

    def publication_lock():
        if threading.get_ident() in slow_thread_ids and not paused.is_set():
            return BeforeSlotPublication()
        return slot_lock

    monkeypatch.setattr(facade, "_word_cache_identity_lock", publication_lock)

    def older_generation():
        slow_thread_ids.append(threading.get_ident())
        try:
            if late == "factory":
                returned.append(facade._word_questions())
            else:
                maintenance.invalidate({"word_catalog"})
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=older_generation)
    worker.start()
    try:
        assert paused.wait(5), "older generation did not reach publication gap"
        assert facade.__dict__["_word_question_service"] is old
        maintenance._advance({"word_catalog"})
        fresh = facade._word_questions()
        assert fresh is not old
        fresh._cache["newer-generation"] = object()
        fresh._locations["newer-generation"] = object()
        fresh._last_index_statistics["newer-generation"] = 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert facade._word_questions() is fresh
    assert set(fresh._cache) == set(fresh._locations) == {"newer-generation"}
    assert fresh._last_index_statistics == {"newer-generation": 1}
    if late == "factory":
        assert returned == [fresh]


def test_factory_old_first_token_keeps_a_captured_new_service(desktop_paths, tmp_path, monkeypatch):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    maintenance = facade._cache_maintenance_service()
    maintenance._advance({"word_catalog"})
    original_epoch = facade.desktop_cache_epoch
    paused, release = threading.Event(), threading.Event()
    slow_thread_ids, returned, errors = [], [], []

    def pause_first_token(scope="word"):
        epoch = original_epoch(scope)
        if threading.get_ident() in slow_thread_ids and not paused.is_set():
            paused.set()
            assert release.wait(5)
        return epoch

    monkeypatch.setattr(facade, "desktop_cache_epoch", pause_first_token)

    def slow_factory():
        slow_thread_ids.append(threading.get_ident())
        try:
            returned.append(facade._word_questions())
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=slow_factory)
    worker.start()
    try:
        assert paused.wait(5)
        maintenance._advance({"word_catalog"})
        fresh = facade._word_questions()
        fresh._cache["already-current"] = object()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert returned == [fresh] and facade._word_questions() is fresh
    assert "already-current" in fresh._cache
