"""Both textbook reading surfaces dispatch explicit destinations and epochs."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_bookshelf_ui import app, page
from test_personal_textbook_study import book_fixture
from test_personal_textbook_ui import panel
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow


@pytest.mark.parametrize('origin', ['personal', 'bookshelf'])
@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('mode', ['assets', 'composition'])
@pytest.mark.parametrize('stale', [False, True])
def test_reading_surfaces_keep_both_actions_and_reject_changed_target(request, monkeypatch, origin, batch, mode, stale):
    value, _tasks, _f = request.getfixturevalue('panel' if origin == 'personal' else 'page')
    selected = []
    if origin == 'personal':
        value.queue_page_button.click()
        if batch: value.crop_page.setValue(2); value.queue_page_button.click()
        image_signal, images_signal = value.image_requested, value.images_requested
    else:
        value.asset_list.item(0).setCheckState(Qt.CheckState.Checked)
        image_signal, images_signal = value.preparation_image_requested, value.preparation_images_requested
    received = []
    image_signal.connect(lambda row: received.append(('assets-single', row)))
    images_signal.connect(lambda rows: received.append(('assets-batch', rows)))
    value.composition_crops_requested.connect(lambda rows: received.append(('composition', rows)))
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog, textbook_preparation_crop_dialog
    class ChosenDialog:
        handoff_mode = mode
        selection_request = {'synthetic_exact_review': 'single'}
        selection_requests = [{'synthetic_exact_review': 'batch-1'}, {'synthetic_exact_review': 'batch-2'}]
        def __init__(self, facade, tasks, choices, parent, *, allow_composition_handoff=False):
            assert allow_composition_handoff
            selected.append(deepcopy(choices))
        def exec(self):
            if stale: value._preparation_target_epoch += 1
            return QDialog.DialogCode.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(textbook_batch_crop_dialog, 'TextbookBatchCropDialog', ChosenDialog)
    monkeypatch.setattr(textbook_preparation_crop_dialog, 'TextbookPreparationCropDialog', ChosenDialog)
    (value.batch_crop_button if batch else value.crop_button).click()
    assert len(selected) == 1
    if stale: assert not received
    else:
        kind = 'composition' if mode == 'composition' else 'assets-batch' if batch else 'assets-single'
        rows = ChosenDialog.selection_requests if batch else [ChosenDialog.selection_request] if mode == 'composition' else ChosenDialog.selection_request
        assert received == [(kind, rows)]


@pytest.mark.parametrize('accepted', [False, True])
def test_native_main_window_slot_uses_new_canvas_route_without_instantiating_window(accepted):
    calls = []; requests = [{'sealed_review': 'synthetic'}]
    def deliver(rows): calls.append(('canvas', rows)); return accepted
    target = SimpleNamespace(preparation_page=SimpleNamespace(import_textbook_crops_for_composition=deliver),
                             navigate=lambda key: calls.append(('navigate', key)))
    TeacherWorkbenchWindow._textbook_crops_to_composition(target, requests)
    assert calls == [('canvas', requests)] + ([('navigate', 'preparation')] if accepted else [])
