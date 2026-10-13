"""Explicit current-page crop entry uses the displayed book and reader lifetime."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QSignalBlocker, QPointF
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QDialog

from test_personal_textbook_ui import app as app, panel as panel, complete, enter_chapter
from test_personal_textbook_study import book_fixture as book_fixture
from test_desktop_library_ui import _ManualBridge
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_crops import book_page_selection
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE


def open_reader(panel, app, *, section=False):
    value, tasks, _ = panel
    value._read(section)
    app.processEvents()
    reader = value._reader
    assert not reader.current_page_crop_button.isEnabled()
    complete(tasks, '读取教材原页')
    app.processEvents()
    assert reader.current_page_crop_button.isEnabled()
    return reader


def crop_dialog(monkeypatch, callback, *, accepted=False):
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_preparation_crop_dialog as module
    seen = []

    class Dialog:
        selection_request = None

        def __init__(self, facade, tasks, selection, parent, *, allow_composition_handoff=False):
            assert allow_composition_handoff
            self.selection_request = {'selection': deepcopy(selection), 'expected_image_sha256': 'a' * 64}
            seen.append(deepcopy(selection))

        def exec(self):
            callback(self)
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, 'TextbookPreparationCropDialog', Dialog)
    return seen


def test_crop_action_is_opt_in_and_only_allowed_for_embedded_personal_book(app, book_fixture):
    f = book_fixture
    reader = TextbookSourceDialog(object(), f.selection, tasks=_ManualBridge(), reading_mode='book', embedded=True)
    assert reader.current_page_crop_button.isHidden() and reader.current_page_crop_request() is None
    reader.reject()
    for kwargs in ({'reading_mode': 'book'}, {'reading_mode': 'section', 'embedded': True}):
        with pytest.raises(ValueError, match='嵌入阅读'):
            TextbookSourceDialog(object(), f.selection, tasks=_ManualBridge(), allow_current_page_crop=True, **kwargs)


def test_click_uses_actual_navigator_page_and_cancel_preserves_reading(panel, app, monkeypatch):
    value, tasks, f = panel
    reader = open_reader(panel, app)
    reader.next.click(); reader.next.click(); reader.zoom.setCurrentIndex(3)
    app.processEvents()
    # A stale selector value must not replace the actual displayed page.
    with QSignalBlocker(reader.pages):
        reader.pages.setCurrentIndex(0)
    old_book = deepcopy(f.state.snapshot()['textbook_workspace']['books'])
    old_checked, old_view = deepcopy(value._checked), deepcopy(value._view)
    received = []; value.image_requested.connect(received.append)
    seen = crop_dialog(monkeypatch, lambda _: None)
    reader.current_page_crop_button.click()
    assert seen == [book_page_selection(value._book, 3)] and not received
    assert value.crop_page.value() == 3 and value._reader is reader
    assert reader.view.pageNavigator().currentPage() == 2
    assert reader.view.zoomMode() == QPdfView.ZoomMode.Custom and reader.view.zoomFactor() == 1.5
    assert value.tabs.currentWidget() is value.reading and not tasks.pending
    assert value._view == old_view and value._checked == old_checked
    assert f.state.snapshot()['textbook_workspace']['books'] == old_book and f.source.read_bytes() == f.original


def test_turning_pages_never_selects_material_or_moves_graphics_selector(panel, app, monkeypatch):
    value, _, _ = panel
    enter_chapter(value, panel[1])
    reader = open_reader(panel, app, section=True)
    seen = crop_dialog(monkeypatch, lambda _: None)
    received = []; value.image_requested.connect(received.append)
    assert value.crop_page.value() == 2
    reader.next.click(); app.processEvents()
    assert reader.current_page_crop_request()['pdf_page'] == 3
    assert value.crop_page.value() == 2 and not value._checked and not seen and not received
    reader.current_page_crop_button.click()
    assert seen == [book_page_selection(value._book, 3)] and value.crop_page.value() == 3


def test_confirmed_current_page_handoff_keeps_exact_selection_without_persisting(panel, app, monkeypatch):
    value, _, f = panel
    reader = open_reader(panel, app)
    reader._jump(3)
    before = deepcopy(value._view)
    seen = crop_dialog(monkeypatch, lambda _: None, accepted=True)
    received = []; value.image_requested.connect(received.append)
    reader.current_page_crop_button.click()
    assert seen == [book_page_selection(value._book, 4)] and received[0]['selection'] == seen[0]
    assert len(received) == 1 and value._view == before and value._reader is reader
    assert reader.view.pageNavigator().currentPage() == 3 and f.source.read_bytes() == f.original


@pytest.mark.parametrize('change', ['page', 'range', 'identity', 'epoch', 'busy', 'reader', 'section-version'])
def test_stale_request_is_rejected_before_opening_crop(panel, app, monkeypatch, change):
    value, _, _ = panel
    enter_chapter(value, panel[1])
    reader = open_reader(panel, app, section=True)
    request = reader.current_page_crop_request()
    epoch = value._epoch
    seen = crop_dialog(monkeypatch, lambda _: None, accepted=True)
    received = []; value.image_requested.connect(received.append)
    if change == 'page':
        reader.next.click()
    elif change == 'range':
        reader._source['pdf_pages'] = [2]
    elif change == 'identity':
        reader._source['source_sha256'] = 'b' * 64
    elif change == 'epoch':
        value._epoch += 1
    elif change == 'busy':
        value._busy = True
    elif change == 'reader':
        value._read(True); app.processEvents()
    else:
        value._view['study_revision'] = 'b' * 64
    value._crop_current_reader_page(reader, epoch, request)
    assert not seen and not received


@pytest.mark.parametrize('change', ['page', 'range', 'identity', 'epoch', 'busy', 'reader', 'section-version'])
def test_stale_context_during_crop_cannot_handoff_after_confirmation(panel, app, monkeypatch, change):
    value, _, _ = panel
    enter_chapter(value, panel[1])
    reader = open_reader(panel, app, section=True)

    def changed(_):
        if change == 'page':
            reader.next.click()
        elif change == 'range':
            reader._source['pdf_pages'] = [2]
        elif change == 'identity':
            value._book['revision'] = 'b' * 64
        elif change == 'epoch':
            value._epoch += 1
        elif change == 'busy':
            value._busy = True
        elif change == 'reader':
            value._read(True); app.processEvents()
        else:
            value._view['study_revision'] = 'b' * 64

    seen = crop_dialog(monkeypatch, changed, accepted=True)
    received = []; value.image_requested.connect(received.append)
    reader.current_page_crop_button.click()
    assert seen == [book_page_selection(value._view['book'], 2)] and not received


def test_out_of_scope_navigation_clamps_before_crop_and_full_book_range_cannot_shrink(panel, app, monkeypatch):
    value, _, _ = panel
    enter_chapter(value, panel[1])
    reader = open_reader(panel, app, section=True)
    reader.view.pageNavigator().jump(0, QPointF()); app.processEvents()
    assert reader.current_page_crop_request()['pdf_page'] == 2
    reader = open_reader(panel, app)
    reader._source['pdf_pages'] = [1, 2]
    seen = crop_dialog(monkeypatch, lambda _: None, accepted=True)
    reader.current_page_crop_button.click()
    assert not seen


def test_failed_or_released_reader_has_no_crop_action(panel, app):
    value, tasks, _ = panel
    value._read(False); app.processEvents()
    reader = value._reader
    tasks.fail(tasks.take('读取教材原页'), '读取失败')
    assert not reader.current_page_crop_button.isEnabled() and reader.current_page_crop_request() is None
    reader = open_reader(panel, app)
    request = reader.current_page_crop_request(); epoch = value._epoch
    value._clear_reader()
    assert not reader.current_page_crop_button.isEnabled() and reader.current_page_crop_request() is None
    value._crop_current_reader_page(reader, epoch, request)
    assert value._reader is None


@pytest.mark.parametrize('bad', [None, {}, {'pdf_page': True}, {'pdf_pages': [True, 2, 3, 4]}])
def test_invalid_or_boolean_page_request_cannot_open_crop(panel, app, monkeypatch, bad):
    value, _, _ = panel
    reader = open_reader(panel, app)
    request = reader.current_page_crop_request() if bad is not None else None
    if isinstance(bad, dict):
        request = {**request, **bad} if bad else {}
    seen = crop_dialog(monkeypatch, lambda _: None, accepted=True)
    value._crop_current_reader_page(reader, value._epoch, request)
    assert not seen


def test_current_page_button_and_pdf_remain_reachable_at_420_width(panel, app):
    value, _, _ = panel
    value.setStyleSheet(WORKBENCH_STYLE); value.resize(420, 800); app.processEvents()
    reader = open_reader(panel, app)
    button = reader.current_page_crop_button
    app.processEvents()
    origin = button.mapTo(value, button.rect().topLeft())
    assert value.width() == 420 and button.isVisible() and button.isEnabled()
    assert 0 <= origin.x() and origin.x() + button.width() <= value.width()
    assert 0 <= origin.y() and origin.y() + button.height() <= value.height()
    assert reader.view.viewport().height() >= 250
