"""Protocol and consumer regressions using isolated synthetic local state.

Windows executes handle-based cleanup. Other platforms verify the production
fallback preserves every cache rather than claiming those files were deleted.
"""
import gzip
import hashlib
import os
import sqlite3
import threading

import pytest

from test_desktop_visual_import_facade import (
    _facade,
    desktop_paths as desktop_paths,
)
from test_word_questions_service import _import, _choice
from integrations.deeptutor_shchem_v1.desktop_cache_maintenance import DesktopCacheMaintenance


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(service, plan, categories):
    return service.execute(plan['plan_id'], plan['revision'], categories)


def assert_platform_cleanup(result):
    if os.name == 'nt':
        assert result['status'] == 'complete', result
        assert result['removed_files'] or result['removed_rows']
    else:
        assert result['status'] == 'partial', result
        assert result['removed_files'] == result['removed_rows'] == 0
        assert result['items'] and all(item['status'] == 'preserved' for item in result['items'])


def test_real_word_search_rebuilds_new_service_and_preserves_evidence(desktop_paths, tmp_path, monkeypatch):
    facade, original, provider = _import(desktop_paths, tmp_path, image=True)
    original_epoch = facade.desktop_cache_epoch('word')
    full = facade.word_question_catalog()
    facade.word_question_save_selection([_choice(full['items'][0])])
    first = facade.word_question_listing()
    old_service = facade._word_questions()
    old_provider = first['items'][0]['search_provider']
    assert old_provider.matches(first['items'][0]['key'], first['items'][0]['revision'], '电解质')
    root = desktop_paths.state_root
    protected = [original, facade.state_store.path]
    protected += list((root / 'visual-import-v2/sources').glob('*'))
    for relative in ('model-usage-v1/ledger.json', 'student-analysis-v1/student.pdf',
                     'mixed-paper-previews/snapshot.json', 'word-question-attributes/evidence.json',
                     'word-handout-import/documents/import.json', 'exports/teacher.docx'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'protected authoritative evidence')
        protected.append(path)
    unknown_preview = root / 'word-question-previews/teacher.docx.json.gz'
    unknown_preview.write_bytes(gzip.compress(b'{"source":"teacher"}'))
    protected.append(unknown_preview)
    index_path = root / 'word-question-catalog-index/catalog.sqlite3'
    with sqlite3.connect(index_path) as connection:
        observations = connection.execute('SELECT * FROM observations ORDER BY identity').fetchall()
        assert observations
        connection.execute('INSERT INTO sources VALUES (?,?,?)', ('unknown', 'v1', '{"original":true}'))
    before = {path: sha(path) for path in protected}
    cache_before = {path: sha(path) for path in (index_path, *facade._word_questions().preview_cache.root.glob('*.gz'))}
    plan = facade.plan_desktop_cache_cleanup()
    assert set(entry['category'] for entry in plan['entries']) >= {'word_previews', 'word_catalog'}
    index_entry = next(entry for entry in plan['entries'] if entry['category'] == 'word_catalog')
    assert index_entry['preserved_observations'] == len(observations)
    assert index_entry['preserved_unknown_rows'] == 1
    result = facade.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_previews', 'word_catalog'])
    assert_platform_cleanup(result)
    assert all(sha(path) == digest for path, digest in before.items())
    with sqlite3.connect(index_path) as connection:
        assert connection.execute('SELECT * FROM observations ORDER BY identity').fetchall() == observations
        if os.name == 'nt':
            assert connection.execute('SELECT identity FROM sources').fetchall() == [('unknown',)]
            assert connection.execute('SELECT count(*) FROM searches').fetchone() == (0,)
        else:
            assert all(sha(path) == digest for path, digest in cache_before.items())
    # Reading the shared generation is the consumer invalidation boundary even
    # when a non-Windows attempt conservatively retained the on-disk cache.
    assert facade.desktop_cache_epoch('word') != original_epoch
    assert facade._word_questions() is not old_service
    new_service = facade._word_questions()
    calls = []
    original_reader = new_service.reader.word_preview_bytes
    monkeypatch.setattr(new_service.reader, 'word_preview_bytes', lambda *args: (calls.append(1), original_reader(*args))[1])
    rebuilt = facade.word_question_listing()
    assert bool(calls) == (os.name == 'nt')
    assert len(rebuilt['items']) == len(first['items'])
    assert [(item['key'], item['revision']) for item in rebuilt['items']] == [(item['key'], item['revision']) for item in first['items']]
    assert rebuilt['items'][0]['search_provider'] is not old_provider
    assert rebuilt['items'][0]['search_provider'].matches(rebuilt['items'][0]['key'], rebuilt['items'][0]['revision'], '电解质')
    assert new_service.catalog_index_statistics()[
        'rebuilt_indexes' if os.name == 'nt' else 'persistent_hits'] >= 1
    assert all(sha(path) == digest for path, digest in before.items())
    assert provider.borrow_calls == 0


def test_plan_cas_busy_changed_file_and_partial_results(desktop_paths, tmp_path, monkeypatch):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    service = facade._cache_maintenance_service()
    plan = service.plan()
    monkeypatch.setattr(service, 'busy', lambda: True)
    result = execute(service, plan, ['word_previews', 'word_catalog'])
    assert result['status'] == 'busy' and all(item['status'] == 'busy' for item in result['items'])
    monkeypatch.setattr(service, 'busy', lambda: False)
    path = desktop_paths.state_root / next(entry['path'] for entry in plan['entries'] if entry['category'] == 'word_previews')
    path.write_bytes(path.read_bytes() + b'changed')
    changed_sha = sha(path)
    result = execute(service, plan, ['word_previews', 'word_catalog'])
    assert result['status'] == 'partial' and len(result['items']) == len(plan['entries'])
    assert sha(path) == changed_sha
    if os.name == 'nt':
        assert any(item['status'] == 'cleared' for item in result['items'])
    else:
        assert result['removed_files'] == result['removed_rows'] == 0
        assert all(item['status'] == 'preserved' for item in result['items'])
    with pytest.raises(ValueError):
        execute(service, plan, ['word_catalog'])
    current = service.plan()
    with pytest.raises(ValueError):
        service.execute(current['plan_id'], 'forged', ['word_previews'])
    with pytest.raises(ValueError):
        execute(service, current, ['student'])


def test_sqlite_changed_plan_and_unknown_trigger_never_delete(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    service = facade._cache_maintenance_service()
    path = desktop_paths.state_root / 'word-question-catalog-index/catalog.sqlite3'
    plan = service.plan()
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO sources VALUES (?,?,?)', ('other', 'unknown', '{}'))
    before = sha(path)
    result = execute(service, plan, ['word_catalog'])
    assert result['status'] == 'partial' and sha(path) == before
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TRIGGER unexpected AFTER DELETE ON sources BEGIN DELETE FROM observations; END')
    before = sha(path)
    plan = service.plan()
    assert not any(entry['category'] == 'word_catalog' for entry in plan['entries'])
    assert sha(path) == before


def test_links_hardlink_and_unknown_files_retained(tmp_path):
    root = tmp_path / 'state'
    cache = root / 'word-question-previews'
    cache.mkdir(parents=True)
    outside = tmp_path / 'original.docx'
    outside.write_bytes(b'original')
    link = cache / 'linked.json.gz'
    try:
        link.symlink_to(outside)
    except OSError:
        link = None
    hard = cache / 'hard.json.gz'
    try:
        os.link(outside, hard)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'Fixture filesystem does not support hardlinks: {exc}')
    unknown = cache / 'legacy.json.gz'
    unknown.write_bytes(b'not a protocol')
    service = DesktopCacheMaintenance(root)
    plan = service.plan()
    assert not plan['entries']
    result = execute(service, plan, ['word_previews'])
    assert result['removed_files'] == 0 and outside.read_bytes() == b'original'
    assert hard.exists() and unknown.exists()
    if link:
        assert link.is_symlink()


def test_live_word_reader_reports_busy(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    entered, release = threading.Event(), threading.Event()
    original = facade._word_questions().reader.word_preview_bytes
    # A real service frame remains in flight while this narrow reader pauses.
    def waiting(*args):
        entered.set()
        release.wait(10)
        return original(*args)
    service = facade._word_questions()
    service._cache.clear()
    service.preview_cache.load = lambda *_: None
    service.reader.word_preview_bytes = waiting
    thread = threading.Thread(target=facade.word_question_catalog)
    thread.start()
    assert entered.wait(5)
    try:
        plan = facade.plan_desktop_cache_cleanup()
        result = facade.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_previews', 'word_catalog'])
        assert result['status'] == 'busy'
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()


def test_two_facades_share_generation_and_both_memory_services_invalidate(desktop_paths, tmp_path):
    first, _, provider = _import(desktop_paths, tmp_path)
    first.desktop_cache_epoch('word')
    first.word_question_catalog()
    second = _facade(desktop_paths, provider)
    second.word_question_catalog()
    old_first, old_second = first._word_questions(), second._word_questions()
    epoch = second.desktop_cache_epoch('word')
    plan = first.plan_desktop_cache_cleanup()
    result = first.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_previews', 'word_catalog'])
    assert_platform_cleanup(result)
    assert first.desktop_cache_epoch('word') == second.desktop_cache_epoch('word') != epoch
    assert first._word_questions() is not old_first
    assert second._word_questions() is not old_second
    listing = second.word_question_listing()
    assert len(listing['items']) == 2
    assert second._word_questions().catalog_index_statistics()[
        'rebuilt_indexes' if os.name == 'nt' else 'persistent_hits'] >= 1


def test_failed_generation_write_prevents_any_cleanup(desktop_paths, tmp_path, monkeypatch):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    service = facade._cache_maintenance_service()
    plan = service.plan()
    paths = [desktop_paths.state_root / entry['path'] for entry in plan['entries']]
    before = {path: sha(path) for path in paths}
    monkeypatch.setattr(service, '_advance', lambda *_: (_ for _ in ()).throw(OSError('locked')))
    result = execute(service, plan, ['word_previews', 'word_catalog'])
    assert result['status'] == 'busy' and len(result['items']) == len(plan['entries'])
    assert all(sha(path) == digest for path, digest in before.items())


@pytest.fixture
def qt_app():
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def until(app, predicate, timeout=10):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Qt operation did not finish')


def test_settings_cache_dialog_category_selection_and_receipts(qt_app, desktop_paths, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_workbench.cache_maintenance_dialog import CacheMaintenanceDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    facade, _, provider = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    tasks = DesktopTaskBridge()
    dialog = CacheMaintenanceDialog(facade, tasks)
    dialog.show()
    dialog.inspect_button.click()
    until(qt_app, lambda: dialog._plan is not None and not tasks._workers)
    assert set(dialog.category_checks) == {'word_previews', 'word_catalog', 'registry'}
    dialog.category_checks['word_catalog'].setChecked(False)
    index = desktop_paths.state_root / 'word-question-catalog-index/catalog.sqlite3'
    before = sha(index)
    dialog.clear_button.click()
    until(qt_app, lambda: dialog.last_result is not None and not tasks._workers)
    assert_platform_cleanup(dialog.last_result)
    assert (dialog.last_result['removed_files'] > 0) == (os.name == 'nt')
    assert dialog.last_result['removed_rows'] == 0 and sha(index) == before
    assert dialog.table.rowCount() == len(dialog.last_result['items']) + len(dialog.last_result['preserved'])
    assert not dialog.clear_button.isEnabled() and dialog.inspect_button.isEnabled()
    assert provider.borrow_calls == 0
    dialog.close()
    tasks.shutdown()


def test_existing_word_and_explorer_views_drop_cached_indexes_keep_choices(qt_app, desktop_paths, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    facade, _, _ = _import(desktop_paths, tmp_path)
    tasks = DesktopTaskBridge()
    word = WordQuestionDialog(facade, tasks)
    word.show()
    until(qt_app, lambda: word._loaded_once and not tasks._workers)
    chosen = next(iter(word._items))
    word._selected = {chosen: word._items[chosen]['revision']}
    word._points[chosen] = 7
    word.search.setText('电解质')
    word.hide()
    explorer = QuestionExplorerPage(facade, tasks)
    explorer.scope.setCurrentIndex(explorer.scope.findData('word_native'))
    explorer.query.setText('电解质')
    explorer.search()
    until(qt_app, lambda: not explorer._loading and not tasks._workers)
    old_session = explorer._sessions['word_native']
    old_index = old_session.index
    first_total = len(explorer._entries)
    assert first_total > 0 and old_index is not None
    plan = facade.plan_desktop_cache_cleanup()
    result = facade.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_previews', 'word_catalog'])
    assert_platform_cleanup(result)
    # The second window notices the shared persisted generation on its next
    # search even without receiving the first window's settings callback.
    explorer.search()
    until(qt_app, lambda: not explorer._loading and not tasks._workers)
    assert explorer._sessions['word_native'] is not old_session
    assert explorer._sessions['word_native'].index is not old_index
    assert len(explorer._entries) == first_total and explorer.query.text() == '电解质'
    word.invalidate_cache_projection()
    assert not word._items and word._selected[chosen] and word._points[chosen] == 7
    assert word.search.text() == '电解质' and '需重新读取' in word.result_count.text()
    assert not tasks._workers
    word.show()
    until(qt_app, lambda: not word._cache_projection_stale and not tasks._workers)
    assert chosen in word._items and word._points[chosen] == 7
    word.close()
    explorer.close()
    tasks.shutdown()


def test_registry_real_rebuild_settings_entry_and_late_registry_rejected(qt_app, desktop_paths, tmp_path, monkeypatch):
    from test_desktop_facade import FakeThemeReader, FakeSupplementalReader, FakeCurriculumReader
    from integrations.deeptutor_shchem_v1.desktop_workbench.home_page import HomePage
    from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import SettingsDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    first, _, provider = _import(desktop_paths, tmp_path)
    first._themes = FakeThemeReader()
    first._supplemental = FakeSupplementalReader()
    first._curriculum = FakeCurriculumReader()
    old_registry = first.load_desktop_registry()
    assert old_registry.fully_loaded
    registry_path = desktop_paths.state_root / 'desktop-registry-cache.v1.json'
    assert registry_path.exists()
    tasks = DesktopTaskBridge()
    home = HomePage(first, tasks)
    home._registry_ready(old_registry)
    epoch = home._registry_cache_epoch
    request = home._registry_request_epoch
    until(qt_app, lambda: not tasks._workers)
    settings = SettingsDialog(first, tasks)
    until(qt_app, lambda: not tasks._workers)
    assert settings.cache_maintenance_button.isEnabled()
    assert settings.model_usage_button.text() == '用量与费用'
    second = _facade(desktop_paths, provider)
    second._themes, second._supplemental, second._curriculum = first._themes, first._supplemental, first._curriculum
    plan = second.plan_desktop_cache_cleanup()
    registry_before = sha(registry_path)
    result = second.clear_desktop_caches(plan['plan_id'], plan['revision'], ['registry'])
    assert_platform_cleanup(result)
    if os.name == 'nt':
        assert result['removed_files'] == 1 and not registry_path.exists()
    else:
        assert sha(registry_path) == registry_before
    home._registry_ready(old_registry, request, epoch)
    assert not home._registry_loaded and '已清理' in home.registry_text.text()
    calls = len(first._themes.calls)
    rebuilt = first.load_desktop_registry(force_refresh=os.name != 'nt')
    assert rebuilt.fully_loaded and registry_path.exists()
    assert len(first._themes.calls) > calls
    assert provider.borrow_calls == 0
    settings.close()
    home.close()
    tasks.shutdown()


def test_late_catalog_callback_and_hidden_scope_never_reuse_old_provider(qt_app, desktop_paths, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    facade, _, _ = _import(desktop_paths, tmp_path)
    tasks = DesktopTaskBridge()
    word = WordQuestionDialog(facade, tasks)
    word.show()
    until(qt_app, lambda: word._loaded_once and not tasks._workers)
    old_catalog = facade.word_question_listing()
    old_request = word._catalog_epoch
    word.hide()
    explorer = QuestionExplorerPage(facade, tasks)
    explorer._started = True
    explorer.scope.setCurrentIndex(explorer.scope.findData('word_native'))
    until(qt_app, lambda: not tasks._workers)
    old_token = facade.desktop_cache_epoch('word')
    plan = facade.plan_desktop_cache_cleanup()
    result = facade.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_catalog'])
    assert_platform_cleanup(result)
    assert facade.desktop_cache_epoch('word') != old_token
    word._catalog_ready(old_request, (old_catalog, None))
    assert word._cache_projection_stale and not word._items
    explorer.invalidate_cache_projection()
    assert explorer._needs_reload and not explorer._sessions and not tasks._workers
    assert '需重新读取' in explorer.result_summary.text()
    word.close()
    explorer.close()
    tasks.shutdown()


@pytest.mark.skipif(os.name != 'nt', reason='Requires the Windows junction API')
def test_windows_junction_inserted_after_plan_preserves_target(desktop_paths, tmp_path):
    import _winapi
    create_junction = getattr(_winapi, 'CreateJunction', None)
    if create_junction is None:
        pytest.skip('Windows junction API unavailable')
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    service = facade._cache_maintenance_service()
    plan = service.plan()
    directory = desktop_paths.state_root / 'word-question-previews'
    target = tmp_path / 'preserved-external-previews'
    directory.rename(target)
    create_junction(str(target), str(directory))
    before = {path: sha(path) for path in target.iterdir()}
    result = execute(service, plan, ['word_previews'])
    assert result['status'] == 'partial' and result['removed_files'] == 0
    assert all(sha(path) == digest for path, digest in before.items())


@pytest.mark.skipif(os.name != 'nt', reason='Requires Windows deny-sharing handles and database pinning')
def test_active_windows_cache_handle_and_sqlite_writer_are_preserved(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    facade.word_question_listing()
    service = facade._cache_maintenance_service()
    plan = service.plan()
    preview = desktop_paths.state_root / next(entry['path'] for entry in plan['entries'] if entry['category'] == 'word_previews')
    before = sha(preview)
    with preview.open('rb'):
        result = execute(service, plan, ['word_previews'])
    assert result['status'] == 'partial' and sha(preview) == before
    plan = service.plan()
    index = desktop_paths.state_root / 'word-question-catalog-index/catalog.sqlite3'
    with sqlite3.connect(index, timeout=0) as writer:
        writer.execute('BEGIN IMMEDIATE')
        before = sha(index)
        result = execute(service, plan, ['word_catalog'])
        assert result['status'] == 'partial' and sha(index) == before
        writer.rollback()


def test_actual_teacher_attribute_store_ranges_and_selection_sha_survive(desktop_paths, tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_word_questions
    monkeypatch.setattr(desktop_word_questions, 'load_attribute_catalog', lambda _root, **_kwargs:
        {'knowledge_points': [{'id': 'K10', 'name': '电离与离子反应'}], 'nodes': []})
    facade, original, provider = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()['items'][0]
    updated = facade.word_question_update_range(item['key'], item['revision'],
        block_start=3, question_end=4, answer_start=5, block_end=5)
    options = facade.word_question_attribute_options(updated['key'], updated['revision'])
    saved = facade.word_question_save_attributes(updated['key'], updated['revision'],
        {'teacher_note': '教师已核对该题范围；本测试只保存本机标签'},
        expected_attribute_revision=options['attributes']['revision'],
        expected_stored_revision=options['stored_revision'])
    assert saved['annotation_source'] == 'teacher_modified'
    facade.word_question_save_selection([_choice(updated)])
    first = facade.word_question_listing()
    store = facade._word_questions().attribute_store
    history = store.history(updated['key'])
    before = {path: sha(path) for path in (original, store.path, facade.state_store.path)}
    plan = facade.plan_desktop_cache_cleanup()
    assert any(row['table'] == 'ranges' for entry in plan['entries'] for row in entry['rows'])
    result = facade.clear_desktop_caches(plan['plan_id'], plan['revision'], ['word_previews', 'word_catalog'])
    assert_platform_cleanup(result)
    assert all(sha(path) == digest for path, digest in before.items())
    rebuilt = facade.word_question_listing()
    assert [(row['key'], row['revision']) for row in rebuilt['items']] == [(row['key'], row['revision']) for row in first['items']]
    restored = next(row for row in rebuilt['items'] if row['key'] == updated['key'])
    assert restored['attributes'] == saved
    assert facade._word_questions().attribute_store.history(updated['key']) == history
    assert facade.word_question_saved_selection() == [_choice(updated)]
    assert all(sha(path) == digest for path, digest in before.items())
    assert provider.borrow_calls == 0
