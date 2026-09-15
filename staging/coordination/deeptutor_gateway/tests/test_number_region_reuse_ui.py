import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.scan_number_dialog import ScanNumberDialog
from integrations.deeptutor_shchem_v1.desktop_raster_numbers import image_catalog
from integrations.deeptutor_shchem_v1.desktop_number_regions import NumberRegionStore
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from test_raster_numbers import fixture_documents


@pytest.fixture
def setup(tmp_path):
    app=create_application(['reuse194-test']);docs,_=fixture_documents()
    catalog=image_catalog(docs);catalog['images'][0]['suggested_number']=7
    state=DesktopStateStore(tmp_path);store=NumberRegionStore(state)
    windows=[]
    def open_dialog():
        d=ScanNumberDialog(catalog,region_store=store);windows.append(d);d.show();app.processEvents();return d
    yield app,catalog,state,store,open_dialog
    for d in windows:d.close();d.deleteLater()
    app.processEvents()


def test_opening_only_reads_and_number_suggestion_is_not_an_edit(setup):
    _,_,state,_,open_dialog=setup;d=open_dialog()
    assert d.number.value()==7 and d.edits==[] and not state.path.exists()


def test_remember_cancel_reopen_and_reordered_number(setup):
    app,cat,state,store,open_dialog=setup;d=open_dialog()
    d.canvas.selection=[10,10,42,37];d._add_region();d.remember.click()
    assert d.saved_selector.count()==1 and d.load_saved.isEnabled()
    d.cancel.click()
    # A different paper owns this image as question 2, not the old question 7.
    cat['images'][0]['suggested_number']=2
    d2=open_dialog();assert not d2.edits and d2.number.value()==2
    d2.load_saved.click();assert d2.canvas.selection==[10,10,42,37] and d2.edits==[]
    d2.add.click();assert d2.edits[0]['number']==2
    assert store.load(cat['images'][0])==[{'box':[10,10,42,37],'punctuation':'.'}]


def test_forget_preserves_current_replacements(setup):
    _,cat,_,store,open_dialog=setup;d=open_dialog()
    d.canvas.selection=[10,10,42,37];d._add_region();d.remember.click();d.forget.click()
    assert len(d.edits)==1 and store.load(cat['images'][0])==[] and not d.load_saved.isEnabled()


def test_manual_reference_number_can_override_owner_hint(setup):
    _,_,_,_,open_dialog=setup;d=open_dialog()
    d.number.setValue(11);d.canvas.selection=[70,10,100,37];d._add_region()
    assert d.edits[0]['number']==11


def test_saved_read_failure_does_not_disable_manual_numbering(setup,monkeypatch):
    _,_,_,store,open_dialog=setup
    def fail(*_):raise OSError('private-path')
    monkeypatch.setattr(store,'load',fail);d=open_dialog()
    assert 'private-path' not in d.status.text() and '未能读取' in d.status.text()
    d.canvas.selection=[10,10,42,37];d._add_region();assert len(d.edits)==1


def test_saved_controls_visible_at_800_by_700(setup):
    app,_,_,_,open_dialog=setup;d=open_dialog();d.resize(800,700);app.processEvents()
    for button in (d.load_saved,d.remember,d.forget,d.add,d.apply,d.cancel):
        assert button.isVisible() and d.rect().contains(button.mapTo(d,button.rect().bottomRight()))
