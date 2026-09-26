import io
from copy import deepcopy

import pytest
from pypdf import PdfWriter
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_library_ui import _ManualBridge

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import (
    TextbookSourceDialog,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def source(pages=None, data=None):
    if data is None:
        writer = PdfWriter()
        writer.add_blank_page(600, 800)
        writer.add_blank_page(600, 800)
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
    return {
        "concept_id": "C01",
        "title": "电离",
        "source_name": "教材.pdf",
        "statement": "仅为蒸馏候选",
        "pdf_bytes": data,
        "pdf_pages": pages if pages is not None else [2],
    }


def test_actual_pdf_navigation_uses_one_based_file_pages_and_zoom(app):
    bridge = _ManualBridge()
    dialog = TextbookSourceDialog(
        object(), {"concept_id": "C01", "revision": "hash"}, tasks=bridge
    )
    app.processEvents()
    assert len(bridge.pending) == 1
    assert not dialog.pages.isEnabled()
    bridge.succeed(bridge.pending.pop(), source())
    app.processEvents()
    assert dialog.document.pageCount() == 2
    assert dialog.view.pageNavigator().currentPage() == 1
    assert dialog.pages.currentData() == 2
    assert dialog.previous.isEnabled() and not dialog.next.isEnabled()
    assert "不是教材原句" in dialog.summary.toPlainText()
    dialog.previous.click()
    assert dialog.view.pageNavigator().currentPage() == 0
    assert not dialog.previous.isEnabled() and dialog.next.isEnabled()
    dialog.next.click()
    dialog.zoom.setCurrentIndex(3)
    assert dialog.view.zoomMode() == QPdfView.ZoomMode.Custom
    assert dialog.view.zoomFactor() == 1.5
    dialog.zoom.setCurrentIndex(1)
    assert dialog.view.zoomMode() == QPdfView.ZoomMode.FitInView
    dialog.reject()
    assert not dialog.buffer.isOpen() and dialog.buffer.size() == 0


@pytest.mark.parametrize(
    "value, message",
    [
        (source(pages=[3]), "超出教材范围"),
        (source(data=b"%PDF- broken"), "无法打开"),
    ],
)
def test_bad_page_or_corrupt_pdf_is_not_silently_replaced(app, value, message):
    bridge = _ManualBridge()
    dialog = TextbookSourceDialog(object(), {}, tasks=bridge)
    app.processEvents()
    bridge.succeed(bridge.pending.pop(), value)
    app.processEvents()
    assert message in dialog.status.text()
    assert not dialog.pages.isEnabled()
    assert not dialog.next.isEnabled()
    dialog.reject()


def test_close_during_read_ignores_late_success(app):
    bridge = _ManualBridge()
    dialog = TextbookSourceDialog(object(), {}, tasks=bridge)
    app.processEvents()
    pending = bridge.pending.pop()
    dialog.reject()
    bridge.succeed(pending, source())
    assert not dialog.buffer.isOpen() and dialog._source is None
    assert not dialog._loaded_pages


def test_original_page_button_uses_current_item_without_changing_import(
    app, monkeypatch
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog as module

    options = [
        {
            "concept_id": "C01",
            "revision": "one",
            "title": "电离",
            "statement": "候选一",
        },
        {
            "concept_id": "C02",
            "revision": "two",
            "title": "电解质",
            "statement": "候选二",
        },
    ]

    class Facade:
        def preparation_concept_options(self, _query):
            return deepcopy(options)

        def preparation_textbook_source(self, *_args):
            raise AssertionError("the fake dialog must not actually load")

    seen = []

    class Preview:
        def __init__(self, _facade, concept, _parent):
            seen.append(concept)

        def exec(self):
            return QDialog.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "TextbookSourceDialog", Preview)
    dialog = PreparationSourcesDialog(Facade())
    dialog.concept_list.setCurrentRow(1)
    assert dialog.original_button.isEnabled()
    before = dialog.selected_concepts
    dialog.original_button.click()
    assert seen == [options[1]]
    assert dialog.selected_concepts == before == []
    assert dialog.reference is None
    dialog.concept_list.setCurrentRow(-1)
    assert not dialog.original_button.isEnabled()
    dialog.reject()
