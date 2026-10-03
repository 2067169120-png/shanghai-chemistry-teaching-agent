"""Actual PDF/catalog UI navigation stays independent of preparation selection."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtWidgets import QApplication, QDialog

from test_textbook_section_reader import section_fixture
from test_textbook_assets_ui import candidate, write_assets
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import PreparationSourcesDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_asset_catalog_dialog import TextbookAssetCatalogDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def facade_for(root):
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = SimpleNamespace(workspace_root=root)
    return facade


def supplementary(f, aid='APPENDIX', page=122):
    row = candidate(aid, page, source_sha=f.row['source_sha256'], section=None)
    row['chapter_id'] = None
    row['supplement_node_key'] = 'SYNTHETIC-APPENDIX'
    return row


def test_facade_catalog_single_page_and_no_excerpt_or_out_of_range_navigation(app, section_fixture):
    f = section_fixture
    path = write_assets(f.root, [supplementary(f)])
    before = [p.read_bytes() for p in (f.book, f.catalog, f.registry_path, path)]
    facade = facade_for(f.root)
    result = facade.preparation_textbook_asset_options()
    assert len(result['assets']) == 1
    row = result['assets'][0]
    selection = {key: row[key] for key in ('visual_asset_id','revision')}
    payload = facade.preparation_textbook_asset_source(**selection)
    assert payload['pdf_pages'] == [122]
    frozen = deepcopy(payload)
    bridge = _ManualBridge()
    dialog = TextbookSourceDialog(facade, selection, tasks=bridge, reading_mode='asset')
    dialog.resize(420, 620)
    dialog.show()
    app.processEvents()
    bridge.succeed(bridge.pending.pop(), payload)
    app.processEvents()
    assert dialog.pages.count() == 1 and dialog.pages.currentData() == 122
    assert dialog.view.pageNavigator().currentPage() == 121
    assert not dialog.previous.isEnabled() and not dialog.next.isEnabled()
    assert not dialog.excerpt_button.isVisible() and dialog.excerpt is None
    dialog._edit_excerpt()
    assert dialog.excerpt_panel is None
    dialog._jump(0)
    assert dialog.view.pageNavigator().currentPage() == 121
    dialog.view.pageNavigator().jump(0, QPointF())
    app.processEvents()
    assert dialog.view.pageNavigator().currentPage() == 121
    assert '待核对' in dialog.summary.toPlainText()
    assert payload == frozen
    dialog.reject()
    assert dialog._source is None and not dialog.buffer.isOpen()
    assert [p.read_bytes() for p in (f.book, f.catalog, f.registry_path, path)] == before
    with pytest.raises(ValueError):
        TextbookSourceDialog(facade, selection, tasks=_ManualBridge(), reading_mode='asset', excerpt={'text':'forbidden'})


def test_catalog_search_ignores_stale_callbacks_and_closed_window(app, section_fixture, monkeypatch):
    f = section_fixture
    write_assets(f.root, [supplementary(f)])
    facade = facade_for(f.root)
    bridge = _ManualBridge()
    dialog = TextbookAssetCatalogDialog(facade, tasks=bridge)
    dialog.resize(420, 620)
    dialog.show()
    app.processEvents()
    first = bridge.pending.pop()
    dialog.search.setText('APPENDIX')
    dialog._timer.stop()
    dialog._load()
    latest = bridge.pending.pop()
    result = facade.preparation_textbook_asset_options('APPENDIX')
    bridge.succeed(first, result)
    assert dialog.results.count() == 0
    bridge.succeed(latest, result)
    assert dialog.results.count() == 1 and not dialog.open_button.isEnabled()
    dialog.results.setCurrentRow(0)
    assert dialog.open_button.isEnabled() and '待核对' in dialog.details.toPlainText()
    assert dialog.open_button.mapTo(dialog, dialog.open_button.rect().bottomRight()).y() < dialog.height()
    captured = []
    class Reader:
        def __init__(self, facade, selection, parent, **kwargs):
            captured.append((selection, kwargs))
        def exec(self):
            return QDialog.DialogCode.Rejected
        def deleteLater(self):
            pass
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog as reader_module
    monkeypatch.setattr(reader_module, 'TextbookSourceDialog', Reader)
    dialog.results.itemActivated.emit(dialog.results.item(0))
    assert len(captured) == 1 and captured[0][1] == {'reading_mode':'asset'}
    dialog.search.setText('missing')
    dialog._timer.stop()
    dialog._load()
    pending = bridge.pending.pop()
    dialog.reject()
    bridge.succeed(pending, result)
    assert dialog.results.count() == 0 and not dialog.open_button.isEnabled()


def test_catalog_entry_does_not_change_preparation_checks_excerpts_or_reference(app, section_fixture, monkeypatch):
    f = section_fixture
    write_assets(f.root, [supplementary(f)])
    native = facade_for(f.root)
    service = PreparationSourcesService(f.root)
    class Facade:
        preparation_textbook_asset_options = native.preparation_textbook_asset_options
        preparation_textbook_asset_source = native.preparation_textbook_asset_source
        def preparation_concept_options(self, query=''):
            return service.concept_options(query)
    dialog = PreparationSourcesDialog(Facade())
    app.processEvents()
    dialog.concept_list.item(0).setCheckState(Qt.CheckState.Checked)
    before = deepcopy((dialog._selected_concepts, dialog._textbook_excerpts, dialog.reference, dialog._preview_key))
    calls = []
    class Catalog:
        def __init__(self, facade, parent):
            calls.append('opened')
        def exec(self):
            return QDialog.DialogCode.Rejected
        def deleteLater(self):
            pass
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_asset_catalog_dialog as module
    monkeypatch.setattr(module, 'TextbookAssetCatalogDialog', Catalog)
    assert dialog.asset_catalog_button.isEnabled()
    dialog.asset_catalog_button.click()
    assert calls == ['opened']
    assert (dialog._selected_concepts, dialog._textbook_excerpts, dialog.reference, dialog._preview_key) == before
    dialog.reject()
