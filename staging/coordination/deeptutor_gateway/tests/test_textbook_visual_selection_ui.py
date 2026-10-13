"""Real pixels and source rechecks in the lazy, read-only textbook selector."""
from copy import deepcopy
from hashlib import sha256
import io

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from test_desktop_library_ui import _ManualBridge
from test_textbook_preparation_crops import crop_fixture as crop_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_bookshelf_ui import page as page
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_section_reader import section_fixture as section_fixture
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_asset_preview import (
    TextbookAssetPreview, page_thumbnail)
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_composition_panel import ResourcePicker


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def row(index=1):
    return {'visual_asset_id':f'SYNTHETIC-{index}', 'revision':str(index)*64,
            'label':f'合成图表{index}', 'pdf_page':index, 'volume_title':'合成教材'}


class Facade:
    def __init__(self):
        self.calls = []
        self.bad = None

    def preparation_textbook_crop_page(self, selection):
        self.calls.append(deepcopy(selection))
        stream = io.BytesIO()
        with Image.new('RGB', (800, 1200), 'navy') as value:
            value.save(stream, format='PNG')
        raw = stream.getvalue()
        return {'visual_asset_id':selection['visual_asset_id'],
                'revision':self.bad or selection['revision'], 'width':800, 'height':1200,
                'page_data':raw, 'recipe':{'raster_sha256':sha256(raw).hexdigest()},
                'pdf_page':1, 'printed_page':None, 'source_title':'合成教材',
                'source_sha256':'a'*64, 'page_revision':'b'*64}


def test_preview_actual_pdf_is_rechecked_without_state_write(crop_fixture):
    f = crop_fixture
    before = disk_bytes(f.root)
    value = page_thumbnail(f.facade, f.selection)
    with Image.open(io.BytesIO(value['data'])) as image:
        assert image.width <= 640 and image.height <= 960
        assert any(low < high for low, high in image.getextrema())
    assert value['selection'] == f.selection and value['pdf_page'] == 9
    assert disk_bytes(f.root) == before and not f.task_root.exists()
    f.book.write_bytes(f.book.read_bytes()+b'\n% replaced source\n')
    with pytest.raises(Exception):
        page_thumbnail(f.facade, f.selection)
    assert not f.task_root.exists()


def test_lazy_hidden_selection_only_reads_when_shown_and_icons_do_not_check(app):
    facade, bridge = Facade(), _ManualBridge()
    preview = TextbookAssetPreview(facade, tasks=bridge)
    items = QListWidget()
    preview.bind_list(items)
    item = QListWidgetItem('合成图表')
    item.setData(Qt.ItemDataRole.UserRole, row())
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Unchecked)
    items.addItem(item)
    items.setCurrentRow(0)
    assert not bridge.pending and not facade.calls
    preview.show(); app.processEvents()
    changes = []
    items.itemChanged.connect(lambda *_: changes.append(True))
    bridge.succeed(bridge.take('预览教材图表原页'), run=True)
    assert preview.receipt and not preview.canvas.pixmap.isNull() and not item.icon().isNull()
    assert not changes and item.checkState() == Qt.CheckState.Unchecked
    assert len(facade.calls) == 1
    preview.release(); preview.close(); items.close()


def test_out_of_order_and_closed_preview_cannot_restore_wrong_pixels(app):
    facade, bridge = Facade(), _ManualBridge()
    preview = TextbookAssetPreview(facade, tasks=bridge)
    preview.show(); preview.set_row(row(1)); first = bridge.take('预览教材图表原页')
    preview.set_row(row(2)); latest = bridge.take('预览教材图表原页')
    bridge.succeed(first, run=True)
    assert preview.receipt is None and preview.canvas.pixmap.isNull()
    bridge.succeed(latest, run=True)
    assert preview.receipt['selection']['visual_asset_id'] == 'SYNTHETIC-2'
    preview.set_row(row(3)); last = bridge.take('预览教材图表原页')
    preview.release(); bridge.succeed(last, run=True)
    assert preview.receipt is None and preview.canvas.pixmap.isNull() and not preview._icons
    preview.close()


@pytest.mark.parametrize('change',['revision','recipe','size','invalid-png'])
def test_invalid_original_page_never_produces_thumbnail(app, change):
    facade = Facade()
    original = facade.preparation_textbook_crop_page
    def changed(selection):
        value = original(selection)
        if change == 'revision': value['revision'] = 'z'*64
        elif change == 'recipe': value['recipe']['raster_sha256'] = 'c'*64
        elif change == 'size': value['width'] = 500
        else:
            value['page_data'] = b'corrupt'
            value['recipe']['raster_sha256'] = sha256(b'corrupt').hexdigest()
        return value
    facade.preparation_textbook_crop_page = changed
    with pytest.raises(Exception): page_thumbnail(facade, {k:row()[k] for k in ('visual_asset_id','revision')})


def test_only_eight_small_icons_retained_and_each_selection_rechecks_source(app):
    facade, bridge = Facade(), _ManualBridge()
    preview = TextbookAssetPreview(facade, tasks=bridge)
    preview.show()
    for index in range(1, 11):
        preview.set_row(row(index)); bridge.succeed(bridge.take('预览教材图表原页'), run=True)
    assert len(preview._icons) == 8 and len(facade.calls) == 10
    preview.set_row(row(10)); bridge.succeed(bridge.take('预览教材图表原页'), run=True)
    assert len(facade.calls) == 11
    preview.release(); preview.close()


def test_failed_source_recheck_clears_previous_thumbnail_without_checking_material(app):
    facade, bridge = Facade(), _ManualBridge()
    preview = TextbookAssetPreview(facade, tasks=bridge)
    items = QListWidget(); preview.bind_list(items)
    item = QListWidgetItem('合成图表'); item.setData(Qt.ItemDataRole.UserRole, row())
    items.addItem(item); items.setCurrentRow(0); preview.show()
    bridge.succeed(bridge.take('预览教材图表原页'), run=True)
    assert not item.icon().isNull()
    preview.set_row(row()); task = bridge.take('预览教材图表原页')
    assert item.icon().isNull() and preview.canvas.pixmap.isNull() and preview.receipt is None
    bridge.fail(task, '原书版本已变化')
    assert item.icon().isNull() and not preview._icons and '变化' in preview.status.text()
    preview.release(); preview.close(); items.close()


def test_visual_picker_narrow_preview_and_selection_are_independent(app):
    facade = Facade()
    picker = ResourcePicker([row()], '合成教材图', lambda r:r['label'], None, visual_facade=facade)
    picker.resize(420, 680); picker.show(); app.processEvents()
    assert picker.width() == 420 and picker.visual_split.orientation() == Qt.Orientation.Vertical
    assert picker.chosen is None
    picker.choose()
    assert picker.chosen == row() and picker.page_preview._closed
    picker.deleteLater(); app.processEvents()


def test_bookshelf_thumbnail_is_not_clipped_and_narrow_actions_scroll_into_view(page, app, monkeypatch):
    value, bridge, _ = page
    facade = Facade()
    monkeypatch.setattr(value.facade, 'preparation_textbook_crop_page', facade.preparation_textbook_crop_page)
    value.tabs.setCurrentIndex(value.asset_tab); value.asset_list.setCurrentRow(0)
    app.processEvents(); bridge.succeed(bridge.take('预览教材图表原页'), run=True); app.processEvents()
    assert value.asset_list.viewport().height() >= 108
    rectangle = value.asset_list.visualItemRect(value.asset_list.currentItem())
    assert rectangle.top() >= 0 and rectangle.bottom() < value.asset_list.viewport().height()
    assert value.asset_preview.canvas.height() >= 120 and not value.asset_list.item(0).icon().isNull()
    assert not value._asset_checked
    value.resize(420, 1000); app.processEvents()
    assert value.width() == 420 and value.asset_splitter.orientation() == Qt.Orientation.Vertical
    assert value.section_status.width() >= 350 and value.section_status.height() < 100
    assert not value.read_button.isVisible() and not value.cards_button.isVisible()
    assert value.asset_preview.canvas.height() >= 120
    scroll = value.asset_splitter.widget(1)
    scroll.ensureWidgetVisible(value.crop_button); app.processEvents()
    point = value.crop_button.mapTo(scroll.viewport(), value.crop_button.rect().center())
    assert 0 <= point.x() < scroll.viewport().width() and 0 <= point.y() < scroll.viewport().height()
    value.tabs.setCurrentIndex(value.knowledge_tab); app.processEvents()
    assert value.read_button.isVisible() and value.cards_button.isVisible()
