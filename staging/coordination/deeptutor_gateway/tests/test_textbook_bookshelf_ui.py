"""Book/section navigation, actual PDF paint, and explicit reference transfer."""
from copy import deepcopy
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture, candidate
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def facade_for(f):
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = SimpleNamespace(workspace_root=f.root)
    return facade


def leaf(page, key):
    for index in range(page.tree.topLevelItemCount()):
        parent = page.tree.topLevelItem(index)
        if parent.data(0, Qt.ItemDataRole.UserRole) == key:
            return parent
        for child in range(parent.childCount()):
            item = parent.child(child)
            if item.data(0, Qt.ItemDataRole.UserRole) == key:
                return item
    raise AssertionError(key)


@pytest.fixture
def page(app, shelf_fixture):
    f, bridge = shelf_fixture, _ManualBridge()
    value = TextbookBookshelfPage(facade_for(f), bridge)
    value.resize(1000, 820)
    value.show()
    value.refresh()
    bridge.succeed(bridge.take('读取教材书架'), run=True)
    value.book.setCurrentIndex(value.book.findData(f.row['volume_id']))
    app.processEvents()
    yield value, bridge, f
    value.close();value.deleteLater()
    app.processEvents()


def select_and_preview(page, bridge):
    page.knowledge_list.item(0).setCheckState(Qt.CheckState.Checked)
    page._preview_reference()
    bridge.succeed(bridge.take('核对教材备课参考'), run=True)


def painted(app, reader):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        app.processEvents()
        image = reader.view.viewport().grab().toImage()
        saturated = sum(max(image.pixelColor(x,y).red(),image.pixelColor(x,y).green(),image.pixelColor(x,y).blue())
                        - min(image.pixelColor(x,y).red(),image.pixelColor(x,y).green(),image.pixelColor(x,y).blue()) > 100
                        for x in range(0,image.width(),20) for y in range(0,image.height(),20))
        if saturated > 10:
            return saturated
        QTest.qWait(30)
    pytest.fail('PDF metadata loaded, but the native viewport did not paint colored page content')


def test_book_navigation_does_not_read_pages_or_select_knowledge(page, app):
    value, bridge, f = page
    before = disk_bytes(f.root)
    assert value.book.count() == 5 and len(value._snapshot['sections']) == 60
    assert value._reader is None and not bridge.pending and value._checked == {}
    assert value.knowledge_list.count() == value.asset_list.count() == 1
    assert value.knowledge_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert value.tree.currentItem().text(0) == '1.1 合成第1.1节'
    value.query.setText('合成知识摘要')
    assert value.tree.topLevelItemCount() == 1 and value.tree.topLevelItem(0).childCount() == 1
    assert value._reader is None and not bridge.pending
    value.query.clear()
    value.book.setCurrentIndex(0)
    assert not value.knowledge_list.count() and value._reader is None and value._checked == {}
    assert disk_bytes(f.root) == before


def test_direct_section_reader_keeps_tree_and_clamps_exact_pages(page, app):
    value, bridge, f = page
    before = disk_bytes(f.root)
    # This second section has no concept; it still has a declared directory range.
    value.tree.setCurrentItem(leaf(value, f.row['chapter_id']+':1.2'))
    assert value.knowledge_list.count() == 0
    value.read_button.click();app.processEvents()
    reader = value._reader
    bridge.succeed(bridge.take('读取本节教材原页'), run=True)
    app.processEvents()
    assert value.tabs.currentWidget() is value.reading and value.tree.isVisible()
    assert not reader.isWindow() and reader.pages.count() == 9
    assert reader.pages.currentData() == 16 and reader._source['concept_pdf_pages'] == []
    assert reader._source['entry_origin'] == 'textbook_bookshelf'
    assert '所选知识点' not in reader.summary.toPlainText()
    assert painted(app, reader) > 10
    reader.view.pageNavigator().jump(0,QPointF());app.processEvents()
    assert reader.view.pageNavigator().currentPage() == 15
    reader._jump(200)
    assert reader.view.pageNavigator().currentPage() == 15
    assert value._checked == {} and reader.excerpt_panel is None and not reader.excerpt_button.isVisible()
    value.tree.setCurrentItem(leaf(value,f.row['section_key']))
    assert value._reader is None and reader._closed and reader._source is None
    assert disk_bytes(f.root) == before


def test_changing_section_ignores_pending_old_reader(page, app):
    value, bridge, f = page
    value._open_section();app.processEvents()
    old_reader, pending = value._reader, bridge.take('读取本节教材原页')
    value.tree.setCurrentItem(leaf(value,f.row['chapter_id']+':1.2'))
    bridge.succeed(pending, run=True)
    assert value._reader is None and old_reader._source is None and old_reader.buffer.size() == 0
    value._open_section();app.processEvents()
    bridge.succeed(bridge.take('读取本节教材原页'), run=True)
    assert value._reader.pages.currentData() == 16


def test_supplementary_asset_is_visible_without_inventing_section(page, app):
    value, bridge, f = page
    f.write_assets([f.asset, candidate(f,visual_asset_id='SYNTHETIC-APPENDIX',pdf_page=122,printed_page=None,
        section_key=None,chapter_id=None,supplement_node_key='SYNTHETIC-APPENDIX',label='合成附录图表')])
    value.refresh();bridge.succeed(bridge.take('读取教材书架'),run=True)
    value.tree.setCurrentItem(leaf(value,'@supplement'))
    assert value.tabs.currentIndex() == value.asset_tab and not value.read_button.isEnabled()
    assert value.knowledge_list.count() == 0 and value.asset_list.count() == 1
    value._open_asset();app.processEvents()
    reader = value._reader
    bridge.succeed(bridge.take('读取教材素材原页'),run=True)
    assert reader.pages.count() == 1 and reader.pages.currentData() == 122
    assert not reader.previous.isEnabled() and not reader.next.isEnabled()
    assert painted(app,reader) > 10 and value._checked == {}
    assert '待核对' in value.asset_detail.toPlainText()


def test_reference_is_explicit_rechecked_and_reading_does_not_select(page):
    value, bridge, f = page
    before = disk_bytes(f.root)
    received = []
    value.preparation_reference_requested.connect(received.append)
    assert not value.confirm_button.isEnabled()
    select_and_preview(value,bridge)
    assert '合成知识摘要' in value.reference_preview.toPlainText()
    assert 'SHA-256' not in value.reference_preview.toPlainText()
    assert 'SHA-256' in value.reference_details.toPlainText()
    assert received == [] and value.confirm_button.isEnabled()
    value._confirm_reference()
    assert received == []
    bridge.succeed(bridge.take('核对教材备课参考'),run=True)
    assert len(received)==1 and 'candidate_only' in received[0]['materials']
    value.reference_appended()
    assert not value.confirm_button.isEnabled()
    value._clear_selection()
    assert value._checked == {} and not value.preview_button.isEnabled()
    assert len(received)==1 and disk_bytes(f.root)==before


def test_selection_changes_and_closed_page_discard_stale_preview(page):
    value,bridge,_ = page
    item = value.knowledge_list.item(0)
    item.setCheckState(Qt.CheckState.Checked)
    value._preview_reference();pending = bridge.take('核对教材备课参考')
    item.setCheckState(Qt.CheckState.Unchecked)
    bridge.succeed(pending,run=True)
    assert value._reference is None and not value.confirm_button.isEnabled()
    item.setCheckState(Qt.CheckState.Checked)
    value._preview_reference();pending = bridge.take('核对教材备课参考')
    value.close();bridge.succeed(pending,run=True)
    assert value._reference is None and value._closed


def test_confirmation_detects_source_change_and_allows_retry(page):
    value,bridge,_ = page
    received=[];value.preparation_reference_requested.connect(received.append)
    select_and_preview(value,bridge)
    value._confirm_reference();pending=bridge.take('核对教材备课参考')
    changed=deepcopy(pending.operation());changed['materials']+='\nchanged source'
    bridge.succeed(pending,changed)
    assert received==[] and value._reference is None and not value.confirm_button.isEnabled()
    assert value.preview_button.isEnabled() and '变化' in value.status.text()


def test_refresh_drops_changed_choices_and_failure_clears_stale_projection(page):
    value,bridge,_=page
    select_and_preview(value,bridge)
    old=deepcopy(value._snapshot)
    value.refresh();pending=bridge.take('读取教材书架')
    assert not value.preview_button.isEnabled() and not value.confirm_button.isEnabled()
    old['concepts'][0]['revision']='b'*64
    bridge.succeed(pending,old)
    assert value._checked=={} and '版本变化' in value.status.text()
    value.refresh();bridge.fail(bridge.take('读取教材书架'),'目录无法核对')
    assert value._snapshot is None and value.tree.topLevelItemCount()==0
    assert not value.book.isEnabled() and not value.preview_button.isEnabled()


@pytest.mark.parametrize('field,bad',[('section_key','OTHER'),('revision','f'*64),('entry_origin','concept')])
def test_direct_section_identity_mismatch_never_loads_pdf(app,shelf_fixture,field,bad):
    f=shelf_fixture;facade=facade_for(f);bridge=_ManualBridge()
    section=next(row for row in facade.textbook_bookshelf()['sections'] if row['section_key']==f.row['section_key'])
    choice={key:section[key] for key in ('section_key','revision')}
    reader=TextbookSourceDialog(facade,choice,tasks=bridge,reading_mode='section')
    reader.show();app.processEvents()
    pending=bridge.take('读取本节教材原页')
    payload=pending.operation();payload[field]=bad
    bridge.succeed(pending,payload)
    assert not reader._loaded_pages and reader.buffer.size()==0 and '未能核对' in reader.status.text()
    reader.reject();reader.deleteLater()


def test_narrow_desktop_keeps_source_and_selection_controls_within_bounds(page,app):
    value,_,_=page
    from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    install_ui_font(app)
    value.setStyleSheet(WORKBENCH_STYLE)
    value.resize(420,780);app.processEvents()
    assert value.width()==420 and value.splitter.orientation()==Qt.Orientation.Vertical
    for control in (value.book,value.query,value.tree,value.read_button,value.preview_button,value.confirm_button):
        origin=control.mapTo(value,control.rect().topLeft())
        assert 0 <= origin.x() and origin.x()+control.width() <= value.width()
        assert control.isVisible()


def test_real_async_bridge_completes_reference_and_embedded_info_toggle(page,app):
    value,_,f=page
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    bridge=DesktopTaskBridge(value)
    value.tasks=bridge
    try:
        value.knowledge_list.item(0).setCheckState(Qt.CheckState.Checked)
        value._preview_reference()
        deadline=time.monotonic()+5
        while value._reference is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.02)
        assert value._reference is not None and value.confirm_button.isEnabled()
        value._open_section()
        while not value._reader._loaded_pages and time.monotonic()<deadline:
            app.processEvents();time.sleep(.02)
        reader=value._reader
        assert reader._loaded_pages and not reader.summary_tabs.isVisible()
        reader.reading_details.setChecked(True)
        assert reader.summary_tabs.isVisible()
        reader.reading_details.setChecked(False)
        assert not reader.summary_tabs.isVisible() and painted(app,reader)>10
    finally:
        value._clear_reader();bridge.shutdown(1000)
