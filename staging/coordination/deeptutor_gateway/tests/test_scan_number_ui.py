import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.scan_number_dialog import ScanNumberDialog
from integrations.deeptutor_shchem_v1.desktop_raster_numbers import image_catalog
from test_raster_numbers import fixture_documents


@pytest.fixture
def dialog():
    app=create_application(['scan-ui-test'])
    docs,_=fixture_documents()
    d=ScanNumberDialog(image_catalog(docs));d.show();app.processEvents()
    yield d,app
    d.close();d.deleteLater();app.processEvents()


def choose(d,app,box):
    rect=d.canvas.view_rect()
    def pt(x,y):
        return QPoint(round(rect.left()+x*rect.width()/d.canvas.image.width()),
                      round(rect.top()+y*rect.height()/d.canvas.image.height()))
    QTest.mousePress(d.canvas,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pt(box[0],box[1]))
    QTest.mouseMove(d.canvas,pt(box[2],box[3]))
    QTest.mouseRelease(d.canvas,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,pt(box[2],box[3]))
    app.processEvents()


def test_mouse_selection_uses_original_pixel_coordinates(dialog):
    d,app=dialog
    choose(d,app,[10,10,42,37])
    assert d.add.isEnabled()
    assert all(abs(a-b)<=1 for a,b in zip(d.canvas.selection,[10,10,42,37]))


def test_add_undo_clear_and_original_comparison(dialog):
    d,app=dialog
    original=d.images[0]['data']
    choose(d,app,[10,10,42,37]);d.number.setValue(2);d.add.click()
    assert len(d.edits)==1 and d.edits[0]['number']==2
    d.original.setChecked(True);d.original.setChecked(False)
    d.undo.click();assert d.edits==[]
    choose(d,app,[10,10,42,37]);d.add.click();d.clear.click();assert d.edits==[]
    d.undo.click();assert len(d.edits)==1
    assert d.images[0]['data']==original


def test_cancel_does_not_write_source_or_invoke_api(dialog):
    d,app=dialog
    choose(d,app,[10,10,42,37]);d.add.click()
    before=d.images[0]['data'];d.cancel.click()
    assert d.result()==0 and d.images[0]['data']==before


def test_two_regions_and_removal_preserve_the_other(dialog):
    d,app=dialog
    for box in ([10,10,42,37],[70,10,100,37]):
        choose(d,app,box);d.add.click()
    keep=d.edits[1].copy()
    d.regions.setCurrentRow(0);d.remove.click()
    assert d.edits==[keep]


def test_narrow_window_keeps_apply_and_cancel_visible(dialog):
    d,app=dialog
    d.resize(800,700);app.processEvents()
    for button in (d.apply,d.cancel,d.add):
        assert button.isVisible()
        assert d.rect().contains(button.mapTo(d,button.rect().bottomRight()))


def test_repeated_app_setup_does_not_repolish_existing_windows(dialog, monkeypatch):
    _,app=dialog
    before=app.font().toString();calls=[]
    monkeypatch.setattr(app,'setStyle',lambda *_:calls.append('style'))
    monkeypatch.setattr(app,'setStyleSheet',lambda *_:calls.append('sheet'))
    monkeypatch.setattr(app,'setFont',lambda *_:calls.append('font'))
    assert create_application(['again']) is app
    assert calls==[] and app.font().toString()==before
