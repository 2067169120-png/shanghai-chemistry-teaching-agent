"""Supplement navigation, exact native PDF pages and explicit reference selection."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_supplements import supplement_fixture as supplement_fixture
from test_textbook_bookshelf_ui import facade_for, leaf, painted
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, supplement_fixture):
    f, bridge = supplement_fixture, _ManualBridge()
    value = TextbookBookshelfPage(facade_for(f), bridge)
    value.resize(1040, 820)
    value.show()
    value.refresh()
    bridge.succeed(bridge.take('读取教材书架'), run=True)
    value.book.setCurrentIndex(value.book.findData(f.row['volume_id']))
    app.processEvents()
    yield value, bridge, f
    value.close(); value.deleteLater(); app.processEvents()


def test_project_groups_knowledge_and_asset_without_unmapped_fallback(page, app):
    value, bridge, f = page
    before = disk_bytes(f.root)
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    assert value.knowledge_list.count() == value.asset_list.count() == 1
    assert value.knowledge_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert value.read_button.text() == '阅读栏目原页' and value.read_button.isEnabled()
    assert not bridge.pending and value._checked == {}
    assert '@unmapped' not in [value.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                              for i in range(value.tree.topLevelItemCount())]
    assert disk_bytes(f.root) == before


def test_project_pdf_paints_three_pages_and_switch_releases_reader(page, app):
    value, bridge, f = page
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value._open_section()
    reader = value._reader
    app.processEvents()
    bridge.succeed(bridge.take('读取补充栏目原页'), run=True)
    assert painted(app, reader) > 10
    assert reader.reading_mode == 'supplement' and reader.pages.count() == 3
    assert [reader.pages.itemData(i) for i in range(3)] == [43, 44, 45]
    reader._jump(43); app.processEvents()
    assert reader.pages.currentData() == 44 and '印刷第39页' in reader.position.text()
    reader._jump(41); app.processEvents()
    assert reader.pages.currentData() == 44
    assert reader.excerpt_button.isHidden()
    value.tree.setCurrentItem(leaf(value, 'declared-appendix'))
    assert value._reader is None and reader._closed and value._checked == {}


def test_book_level_appendix_keeps_unknown_printed_pages(page, app):
    value, bridge, f = page
    value.tree.setCurrentItem(leaf(value, 'declared-appendix'))
    assert value.tree.currentItem().parent().text(0) == '本册导读与附录'
    value._open_section()
    app.processEvents()
    bridge.succeed(bridge.take('读取补充栏目原页'), run=True)
    app.processEvents()
    reader = value._reader
    assert reader.pages.count() == 1 and '印刷待核对' in reader.pages.itemText(0)
    assert not reader.previous.isEnabled() and not reader.next.isEnabled()
    assert '印刷页：待核对' in reader.summary.toPlainText()


def test_search_matches_supplement_concept_and_keeps_declared_parent(page, app):
    value, bridge, f = page
    value.query.setText('合成知识摘要'); app.processEvents()
    node = leaf(value, 'declared-project')
    assert node.parent().text(0).startswith('合成第1章')
    assert value.tree.topLevelItemCount() == 1 and node.parent().childCount() == 1
    assert value._reader is None and not bridge.pending


def test_explicit_reference_retains_concept_scope_and_rechecks_confirmation(page, app):
    value, bridge, f = page
    received = []
    value.preparation_reference_requested.connect(received.append)
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value.knowledge_list.item(0).setCheckState(Qt.CheckState.Checked)
    value._preview_reference()
    bridge.succeed(bridge.take('核对教材备课参考'), run=True)
    assert 'PDF文件页序：[43, 45]' in value._reference['materials']
    assert not received
    value._confirm_reference()
    bridge.succeed(bridge.take('核对教材备课参考'), run=True)
    assert len(received) == 1 and received[0]['materials'] == value._reference['materials']
    assert f.row['pdf_pages'] == [43, 45]


def test_late_reader_result_after_chapter_change_is_discarded(page, app):
    value, bridge, f = page
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value._open_section()
    reader = value._reader
    app.processEvents()
    task = bridge.take('读取补充栏目原页')
    source = task.operation()
    value.tree.setCurrentItem(leaf(value, 'declared-appendix'))
    bridge.succeed(task, source)
    assert reader._closed and reader._source is None and value._reader is None


@pytest.mark.parametrize('width', [420, 1040])
def test_supplement_controls_fit_both_widths(page, app, width):
    value, bridge, f = page
    value.resize(width, 820); app.processEvents()
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    assert value.splitter.orientation() == (Qt.Orientation.Vertical if width == 420 else Qt.Orientation.Horizontal)
    assert value.read_button.width() <= value.width()
    assert value.read_button.mapTo(value, value.read_button.rect().bottomRight()).y() <= value.height()


def test_invalid_source_callback_is_rejected_before_pdf_load(page, app):
    value, bridge, f = page
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value._open_section()
    app.processEvents()
    task = bridge.take('读取补充栏目原页')
    wrong = deepcopy(task.operation())
    wrong['supplement_node_key'] = 'declared-appendix'
    bridge.succeed(task, wrong)
    assert not value._reader._loaded_pages and value._reader.pages.count() == 0


def test_nonconsecutive_declared_pages_navigate_by_list(page, app):
    value, bridge, f = page
    node = f.product['supplement_nodes'][0]
    node['pdf_page_list'] = [43, 45]
    node['printed_pages'] = []
    f.write_product()
    value.refresh()
    bridge.succeed(bridge.take('读取教材书架'), run=True)
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value._open_section(); app.processEvents()
    bridge.succeed(bridge.take('读取补充栏目原页'), run=True)
    app.processEvents()
    reader = value._reader
    assert reader.pages.currentData() == 43
    reader.next.click(); app.processEvents()
    assert reader.pages.currentData() == 45
    reader.previous.click(); app.processEvents()
    assert reader.pages.currentData() == 43


def test_narrow_reading_has_room_for_pdf_and_restores_selection_actions(page, app):
    value, bridge, f = page
    value.resize(420, 920)
    value.tree.setCurrentItem(leaf(value, 'declared-project'))
    value._open_section(); app.processEvents()
    bridge.succeed(bridge.take('读取补充栏目原页'), run=True)
    app.processEvents()
    assert value._reader.view.viewport().height() >= 160
    assert value.preview_button.isHidden() and value.confirm_button.isHidden()
    value.tabs.setCurrentIndex(value.knowledge_tab); app.processEvents()
    assert not value.preview_button.isHidden() and not value.confirm_button.isHidden()
