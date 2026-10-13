"""Source-bound navigation, separate from selection and review state."""
from copy import deepcopy
from hashlib import sha256
import json
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from test_personal_textbook_study import book_fixture as book_fixture, chapter
from test_personal_textbook_ui import Bridge, complete, facade_for
from test_textbook_bookshelf import shelf_fixture as shelf_fixture
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf_ui import leaf
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_textbook_workspace import TextbookWorkspaceService
from integrations.deeptutor_shchem_v1.desktop_textbook_reading_positions import (
    DRAFT_KEY, SCHEMA, MAX_POSITIONS, TextbookReadingPositions, bind_source, validated_record)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_panel import PersonalTextbookPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def binding(index=0, pages=None):
    return {'selector': {'mode': 'section', 'section_key': f'volume:section-{index}',
        'revision': f'version-{index}', 'volume_id': 'volume'}, 'source_sha256': sha256(b'original').hexdigest(),
        'pdf_pages': pages if pages is not None else [2, 3, 4]}


def test_exact_version_scope_and_source_required_and_reads_do_not_write(tmp_path):
    store = DesktopStateStore(tmp_path/'state');positions = TextbookReadingPositions(store)
    assert positions.latest() is None and not store.root.exists()
    original = binding();positions.remember(original, 3, '本节')
    before = store.path.read_bytes()
    assert positions.lookup(original)['pdf_page'] == 3
    for field, value in [('source_sha256', 'a'*64), ('pdf_pages', [2, 3]), ('selector', {**original['selector'], 'revision': 'new'})]:
        changed = deepcopy(original);changed[field] = value
        assert positions.lookup(changed) is None
    assert store.path.read_bytes() == before
    for page in [1, 5, True, '3']:
        with pytest.raises(ValueError):positions.remember(original, page, '本节')
    assert store.path.read_bytes() == before


def test_binding_checks_current_bytes_and_refuses_other_reader_modes():
    source = {'reading_mode': 'section', 'entry_origin': 'textbook_bookshelf',
        **binding()['selector'], 'source_sha256': sha256(b'original').hexdigest(),
        'pdf_pages': [2, 3, 4], 'pdf_bytes': b'original'}
    assert bind_source(source) == binding()
    for changes in [{'pdf_bytes': b'other'}, {'reading_mode': 'asset'}, {'entry_origin': 'other'}, {'pdf_pages': [2, 2]}]:
        with pytest.raises(ValueError):bind_source({**source, **changes})


def test_interleaved_stores_merge_locations_and_preserve_other_drafts(tmp_path):
    first = DesktopStateStore(tmp_path/'state');second = DesktopStateStore(tmp_path/'state')
    first.save_draft('prep-saved', {'materials': '保留内容'})
    a, b = TextbookReadingPositions(first), TextbookReadingPositions(second)
    a.remember(binding(1), 2, '第一节');b.remember(binding(2), 3, '第二节');a.remember(binding(1), 4, '第一节')
    assert b.lookup(binding(2))['pdf_page'] == 3 and b.latest()['pdf_page'] == 4
    assert first.snapshot()['drafts']['prep-saved'] == {'materials': '保留内容'}
    assert first.snapshot()['basket'] == []
    for index in range(3, MAX_POSITIONS+5):b.remember(binding(index), 2, '章节')
    assert len(a.snapshot()['entries']) == MAX_POSITIONS and a.lookup(binding(1)) is None


def test_malformed_navigation_is_not_used_or_rewritten(tmp_path):
    good = {'binding': binding(), 'pdf_page': 3, 'label': '节'}
    for bad in [{**good, 'pdf_page': 0}, {**good, 'path': 'arbitrary'}, {**good, 'label': 'bad\nlabel'},
                {**good, 'binding': {**binding(), 'pdf_pages': [True]}}]:
        assert validated_record({'schema': SCHEMA, 'entries': [bad]})['entries'] == []
    store = DesktopStateStore(tmp_path/'state');store.save_draft(DRAFT_KEY, {'unexpected': 'value'})
    before = store.path.read_bytes()
    assert TextbookReadingPositions(store).latest() is None and store.path.read_bytes() == before


def open_book(app, facade, bridge, selection, **options):
    reader = TextbookSourceDialog(facade, selection, tasks=bridge, reading_mode='book', embedded=True,
        remember_position=True, **options)
    reader.show();app.processEvents();complete(bridge, '读取教材原页');app.processEvents()
    assert reader._loaded_pages
    return reader


def test_personal_reader_survives_new_store_and_explicit_anchor_wins(app, book_fixture):
    f = book_fixture;bridge = Bridge();facade = facade_for(f)
    reader = open_book(app, facade, bridge, f.selection)
    reader.pages.setCurrentIndex(reader.pages.findData(3));reader.reject();app.processEvents()
    facade._state = DesktopStateStore(f.state_root)
    resumed = open_book(app, facade, bridge, f.selection)
    assert resumed.pages.currentData() == 3 and resumed.view.pageNavigator().currentPage() == 2
    assert '继续' in resumed.reading_memory.text()
    resumed.reject()
    anchored = open_book(app, facade, bridge, {**f.selection, 'initial_pdf_page': 2})
    assert anchored.pages.currentData() == 2
    anchored.reject()
    assert f.state.snapshot()['textbook_workspace']['books'][0]['human_reviewed'] is False
    assert f.source.read_bytes() == f.original


def test_reader_failure_does_not_save_pending_or_apply_saved_wrong_scope(app, book_fixture):
    f = book_fixture;facade = facade_for(f);bridge = Bridge()
    reader = open_book(app, facade, bridge, f.selection)
    reader.pages.setCurrentIndex(reader.pages.findData(3));reader._failed('测试失败');reader.reject()
    assert facade.textbook_reading_positions().latest() is None
    whole = open_book(app, facade, bridge, f.selection)
    whole.pages.setCurrentIndex(whole.pages.findData(4));whole.reject()
    view = chapter(f)
    selection = {**f.selection, 'section_id': view['sections'][0]['section_id'], 'study_revision': view['study_revision']}
    section = open_book(app, facade, bridge, selection)
    assert section.pages.currentData() == 2 and section.pages.count() == 2
    section.reject()


def test_bookshelf_return_and_new_page_resume_preserve_explicit_selection(app, shelf_fixture, tmp_path):
    f = shelf_fixture
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    from types import SimpleNamespace
    facade.paths = SimpleNamespace(workspace_root=f.root)
    facade._state = DesktopStateStore(tmp_path/'navigation')
    # This fixture has no personal workspace paths; keep its shelf authoritative.
    facade.personal_textbook_study = lambda: SimpleNamespace(books=lambda: [])
    def load_page():
        bridge = _ManualBridge();page = TextbookBookshelfPage(facade, bridge)
        page.show();page.refresh();bridge.succeed(bridge.take('读取教材书架'), run=True);app.processEvents()
        return page, bridge
    page, bridge = load_page()
    page.book.setCurrentIndex(page.book.findData(f.row['volume_id']))
    page.tree.setCurrentItem(leaf(page, f.row['chapter_id']+':1.2'))
    page._open_section();app.processEvents();bridge.succeed(bridge.take('读取本节教材原页'), run=True);app.processEvents()
    page._reader.pages.setCurrentIndex(page._reader.pages.findData(20));page._clear_reader()
    page._open_section();app.processEvents();bridge.succeed(bridge.take('读取本节教材原页'), run=True);app.processEvents()
    assert page._reader.pages.currentData() == 20 and not page._checked
    page.close();app.processEvents()
    reopened, other = load_page()
    assert reopened.resume_button.isEnabled()
    reopened.resume_button.click();app.processEvents();other.succeed(other.take('读取本节教材原页'), run=True);app.processEvents()
    assert reopened.book.currentData() == f.row['volume_id']
    assert reopened.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == f.row['chapter_id']+':1.2'
    assert reopened._reader.pages.currentData() == 20 and not reopened._checked and not reopened._asset_checked
    reopened.close()


def test_personal_panel_resume_waits_for_chapters_and_keeps_review_false(app, book_fixture):
    f = book_fixture;view = chapter(f);bridge = Bridge();facade = facade_for(f)
    source = f.study.read(f.selection, section_id=view['sections'][0]['section_id'], study_revision=view['study_revision'])
    record = facade.textbook_reading_positions().remember(bind_source(source), 3, '合成章一')
    panel = PersonalTextbookPanel(facade, bridge)
    panel.set_book(f.study.books()[0]);panel.resume_position(record)
    complete(bridge, '读取个人教材章节与笔记');app.processEvents();complete(bridge, '读取教材原页');app.processEvents()
    assert panel._reader.pages.currentData() == 3 and not panel._checked and not panel.range_confirmed.isChecked()
    panel.close()


def test_personal_narrow_reading_leaves_space_and_directory_toggle_keeps_page(app, book_fixture):
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    f = book_fixture;chapter(f);bridge = Bridge();panel = PersonalTextbookPanel(facade_for(f), bridge)
    panel.setStyleSheet(WORKBENCH_STYLE);panel.resize(540,900);panel.show()
    panel.set_book(f.study.books()[0]);complete(bridge,'读取个人教材章节与笔记')
    panel._read(True);app.processEvents();complete(bridge,'读取教材原页');app.processEvents()
    reader = panel._reader;reader.pages.setCurrentIndex(reader.pages.findData(3));app.processEvents()
    assert not panel.tree.isVisible() and panel.directory_button.isVisible()
    assert reader.view.viewport().height() >= 300
    panel.directory_button.click();app.processEvents()
    assert panel.tree.isVisible() and panel._reader is reader and reader.pages.currentData() == 3
    panel.directory_button.click();panel.resize(1000,900);app.processEvents()
    assert panel.tree.isVisible() and panel.read_section_button.isVisible() and reader.pages.currentData() == 3
    reader._failed('原文件已变化');panel.resize(540,900);app.processEvents()
    assert panel.read_book_button.isVisible() and reader.status.isVisible()
    panel.close()


def test_personal_backup_retains_only_present_personal_versions_and_reads_old_bundle(app, book_fixture, tmp_path):
    f = book_fixture;source = f.study.read(f.selection);positions = TextbookReadingPositions(f.state)
    positions.remember(binding(), 3, '中央教材节')
    positions.remember(bind_source(source), 3, '个人书')
    bundle = tmp_path/'with-reading.zip';f.workspace.export_bundle(bundle)
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['reading_positions']['entries']) == 1
        contents = {name:archive.read(name) for name in archive.namelist()}
    restored = tmp_path/'restored';TextbookWorkspaceService.restore_bundle(bundle, restored)
    assert TextbookReadingPositions(DesktopStateStore(restored)).latest()['pdf_page'] == 3
    manifest.pop('reading_positions');contents['manifest.json'] = json.dumps(manifest, ensure_ascii=False).encode()
    old = tmp_path/'old-compatible.zip'
    with ZipFile(old, 'w', compression=ZIP_DEFLATED) as archive:
        for name, raw in contents.items():archive.writestr(name, raw)
    destination = tmp_path/'old-restored';TextbookWorkspaceService.restore_bundle(old, destination)
    assert TextbookReadingPositions(DesktopStateStore(destination)).latest() is None
