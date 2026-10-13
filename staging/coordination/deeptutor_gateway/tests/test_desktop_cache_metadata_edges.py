"""Unverified maintenance metadata must preserve readable local sources."""

import hashlib
import json
import os
import threading

import pytest

from test_desktop_visual_import_facade import (
    _facade,
    desktop_paths as desktop_paths,
)
from test_word_questions_service import _import

from integrations.deeptutor_shchem_v1.desktop_cache_maintenance import (
    CATEGORIES,
    EPOCH_SCHEMA,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unknown_generation_bytes():
    value = {
        "schema_version": EPOCH_SCHEMA,
        "generations": {
            **{category: 0 for category in CATEGORIES},
            "other": {"marker": "preserve-unknown-generation"},
        },
    }
    # The revision is correct: the unknown generation itself rejects this
    # record, rather than an unrelated checksum error.
    value["revision"] = hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


@pytest.fixture
def qt_app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.mark.parametrize(
    "metadata",
    [b"[" * 7000 + b"]" * 7000, _unknown_generation_bytes()],
    ids=["deep-json-below-size-limit", "unknown-generation-dictionary"],
)
def test_unverified_generation_preserves_window_word_reads_and_cleanup(
    qt_app, desktop_paths, tmp_path, metadata
):
    from integrations.deeptutor_shchem_v1.desktop_workbench.home_page import HomePage
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        TeacherWorkbenchWindow,
    )

    original_facade, original, provider = _import(desktop_paths, tmp_path, image=True)
    original_items = original_facade.word_question_listing()["items"]
    assert len(original_items) == 2
    chosen = original_items[0]
    original_detail = original_facade.word_question_detail(chosen["key"], chosen["revision"])
    prior_plan = original_facade.plan_desktop_cache_cleanup()
    assert {entry["category"] for entry in prior_plan["entries"]} >= {
        "word_previews", "word_catalog"
    }
    sources = [original, *(
        desktop_paths.state_root / "visual-import-v2" / "sources"
    ).glob("*")]
    source_shas = {path: _sha(path) for path in sources}
    generation_path = desktop_paths.state_root / "desktop-cache-generation.v1.json"
    assert len(metadata) < 16 * 1024
    generation_path.write_bytes(metadata)
    generation_sha = _sha(generation_path)

    # A new facade and the actual home page exercise the cold startup path.
    # _facade uses the isolated state and an unconfigured fake provider store.
    reader = _facade(desktop_paths, provider)
    window = TeacherWorkbenchWindow(reader)
    try:
        assert isinstance(window.home_page, HomePage)
        assert window.stack.currentWidget() is window.home_page
        registry_epoch = reader.desktop_cache_epoch("registry")
        assert window.home_page._registry_cache_epoch == registry_epoch
        assert reader.desktop_cache_epoch("registry") == registry_epoch
        word_epoch = reader.desktop_cache_epoch("word")
        assert reader.desktop_cache_epoch("word") == word_epoch
        assert len(word_epoch) == len(registry_epoch) == 64

        listing = reader.word_question_listing()
        assert [(item["key"], item["revision"]) for item in listing["items"]] == [
            (item["key"], item["revision"]) for item in original_items
        ]
        detail = reader.word_question_detail(chosen["key"], chosen["revision"])
        for field in ("key", "revision", "question_blocks", "answer_blocks"):
            assert detail[field] == original_detail[field]
        assert listing["items"][0]["search_provider"].matches(
            chosen["key"], chosen["revision"], "电解质"
        )
        assert _sha(generation_path) == generation_sha
    finally:
        window.tasks.shutdown()
        window.close()
        window.deleteLater()
        qt_app.processEvents()

    with pytest.raises(ValueError):
        reader.plan_desktop_cache_cleanup()
    with pytest.raises(ValueError):
        original_facade.plan_desktop_cache_cleanup()

    # The valid plan predates the unverified metadata. No live GUI readers
    # remain, so busy is caused by refusal to verify/advance the generation.
    cache_shas = {
        desktop_paths.state_root / entry["path"]: _sha(
            desktop_paths.state_root / entry["path"]
        )
        for entry in prior_plan["entries"]
    }
    result = original_facade.clear_desktop_caches(
        prior_plan["plan_id"], prior_plan["revision"], ["word_previews", "word_catalog"]
    )
    assert result["status"] == "busy"
    assert result["removed_files"] == result["removed_rows"] == 0
    assert result["removed_bytes"] == result["cleared_record_bytes"] == 0
    assert result["items"] and all(item["status"] == "busy" for item in result["items"])
    assert result["affected_categories"] == []
    assert all(_sha(path) == digest for path, digest in cache_shas.items())
    assert all(_sha(path) == digest for path, digest in source_shas.items())
    assert _sha(generation_path) == generation_sha
    assert generation_path.read_bytes() == metadata
    assert provider.borrow_calls == 0


class _GateLock:
    """Model an active reader: maintenance may try, but never wait on it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.RLock()
        self._guard = threading.Lock()
        self.entries = 0

    def __enter__(self):
        with self._guard:
            self.entries += 1
        raise AssertionError("invalidation entered the active reader lock")

    def __exit__(self, *_args):
        self._lock.release()

    def acquire(self, *, blocking=True):
        assert blocking is False, "invalidation must not wait for an active reader"
        self.entered.set()
        return False

def _invalidate_thread(facade, errors):
    def run():
        try:
            facade._invalidate_maintained_caches({"word_catalog"})
        except Exception as error:
            errors.append(error)

    return threading.Thread(target=run)


def test_old_invalidator_does_not_erase_a_fresh_word_service(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    gate = _GateLock()
    old._lock = gate
    errors = []
    worker = _invalidate_thread(facade, errors)
    worker.start()
    try:
        assert gate.entered.wait(5), "invalidator did not capture the old service"
        worker.join(5)
        assert not worker.is_alive(), "invalidator waited for the old reader"
        assert "_word_question_service" not in facade.__dict__
        # Install an actual new service after the old reader was detached.
        # The active old object remains private until its task drains.
        fresh = type(old)(facade)
        fresh._cache["fresh"] = object()
        fresh._locations["fresh"] = object()
        fresh._last_index_statistics["fresh"] = 1
        facade._word_question_service = fresh
    finally:
        gate.release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    assert facade._word_questions() is fresh
    assert set(fresh._cache) == set(fresh._locations) == {"fresh"}
    assert fresh._last_index_statistics == {"fresh": 1}
    assert old._locations and old._last_index_statistics
    assert gate.entries == 0


def test_two_concurrent_invalidators_capture_old_service_only_once(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    gate = _GateLock()
    old._lock = gate
    errors = []
    first = _invalidate_thread(facade, errors)
    second = _invalidate_thread(facade, errors)
    first.start()
    try:
        assert gate.entered.wait(5), "first invalidator did not capture the old service"
        first.join(5)
        assert not first.is_alive(), "first invalidator waited on the active reader"
        second.start()
        second.join(5)
        assert not second.is_alive(), "second invalidator also waited on the old service"
        assert gate.entries == 0
    finally:
        gate.release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert not errors, errors
    assert old._locations and old._last_index_statistics
    assert facade._word_questions() is not old


def test_factory_captured_service_survives_detach_and_fresh_publication(
    desktop_paths, tmp_path
):
    facade, _, provider = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    old = facade._word_questions()
    captured, release, refreshed = threading.Event(), threading.Event(), threading.Event()
    errors, returned_services, fresh_services = [], [], []

    class CapturingServiceDict(dict):
        def __init__(self, values):
            super().__init__(values)
            self._guard = threading.Lock()
            self._paused = False

        def get(self, key, default=None):
            service = super().get(key, default)
            with self._guard:
                pause = key == "_word_question_service" and service is old and not self._paused
                if pause:
                    self._paused = True
            if pause:
                # The factory has already captured a local reference. Pause
                # before returning it to model a reader already in progress.
                captured.set()
                if not release.wait(10):
                    raise TimeoutError("captured factory read was not released")
            return service

    facade.__dict__ = CapturingServiceDict(facade.__dict__)

    def query_factory():
        try:
            returned_services.append(facade._word_questions())
        except Exception as error:
            errors.append(error)

    def invalidate_and_recreate():
        try:
            facade._invalidate_maintained_caches({"word_catalog"})
            fresh = facade._word_questions()
            fresh._cache["fresh"] = object()
            fresh._locations["fresh"] = object()
            fresh._last_index_statistics["fresh"] = 1
            fresh_services.append(fresh)
        except Exception as error:
            errors.append(error)
        finally:
            refreshed.set()

    getter = threading.Thread(target=query_factory)
    invalidator = threading.Thread(target=invalidate_and_recreate)
    getter.start()
    try:
        assert captured.wait(5), "factory did not capture the old service"
        invalidator.start()
        assert refreshed.wait(5), "invalidation/fresh factory did not complete"
        assert not errors, errors
        assert len(fresh_services) == 1
        fresh = fresh_services[0]
        assert fresh is not old
        assert facade.__dict__["_word_question_service"] is fresh
    finally:
        release.set()
        getter.join(5)
        if invalidator.ident is not None:
            invalidator.join(5)

    assert not getter.is_alive() and not invalidator.is_alive()
    assert not errors, errors
    assert returned_services == [old]
    assert facade._word_questions() is fresh
    assert set(fresh._cache) == set(fresh._locations) == {"fresh"}
    assert fresh._last_index_statistics == {"fresh": 1}
    assert not old._cache and not old._locations and not old._last_index_statistics
    assert provider.borrow_calls == 0


def test_curriculum_getter_captured_catalog_survives_registry_invalidation(
    desktop_paths, tmp_path
):
    facade, _, provider = _import(desktop_paths, tmp_path)
    cached_catalog = {
        "volumes": [{"id": "isolated-volume", "title": "缓存教材目录", "chapters": []}],
        "counts": {"volumes": 1, "chapters": 0, "sections": 0},
    }
    facade._curriculum_catalog_cache = cached_catalog
    facade._library_views["cached-view"] = object()
    captured, release = threading.Event(), threading.Event()
    errors, returned_catalogs, fallback_reads = [], [], []

    class NoFallbackCurriculum:
        def catalog(self):
            fallback_reads.append(True)
            raise AssertionError("captured cache must not require a source fallback")

    class CapturingCurriculumFacade(type(facade)):
        def __getattribute__(self, name):
            value = super().__getattribute__(name)
            if (name == "_curriculum_catalog_cache" and value is cached_catalog
                    and not captured.is_set()):
                # Capture the first actual getter read before an invalidator
                # sets the facade field to None. The local snapshot is valid.
                captured.set()
                if not release.wait(10):
                    raise TimeoutError("captured curriculum read was not released")
            return value

    facade._curriculum = NoFallbackCurriculum()
    facade.__class__ = CapturingCurriculumFacade

    def read_catalog():
        try:
            returned_catalogs.append(facade._read_curriculum_catalog(force_refresh=False))
        except Exception as error:
            errors.append(error)

    getter = threading.Thread(target=read_catalog)
    getter.start()
    try:
        assert captured.wait(5), "curriculum getter did not capture the catalog"
        facade._invalidate_maintained_caches({"registry"})
        assert facade._curriculum_catalog_cache is None
        assert not facade._library_views
    finally:
        release.set()
        getter.join(5)

    assert not getter.is_alive()
    assert not errors, errors
    assert returned_catalogs == [cached_catalog]
    assert returned_catalogs[0] is not cached_catalog
    assert returned_catalogs[0]["volumes"] is not cached_catalog["volumes"]
    assert not fallback_reads
    assert provider.borrow_calls == 0
