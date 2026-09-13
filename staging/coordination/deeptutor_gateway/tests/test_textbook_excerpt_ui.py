from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_library_ui import _ManualBridge
from test_textbook_source_ui import source

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import (
    PreparationSourcesDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_excerpt_widget import (
    TextbookExcerptWidget,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import (
    TextbookSourceDialog,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def verified_source():
    return {**source(pages=[1, 2]), "source_sha256": "a" * 64}


def excerpt():
    return {
        "concept_id": "C01",
        "revision": "r1",
        "source_sha256": "a" * 64,
        "pdf_page": 2,
        "text": "教师手工输入的定义。\n保留条件与符号。",
        "confirmed": True,
    }


def test_excerpt_starts_blank_not_distilled_and_requires_explicit_confirmation(app):
    widget = TextbookExcerptWidget(verified_source(), "r1", 2)
    assert widget.text.toPlainText() == ""
    assert not widget.confirmed.isChecked() and not widget.use.isEnabled()
    assert not widget.remove.isEnabled()
    widget.text.setPlainText(excerpt()["text"])
    assert not widget.use.isEnabled()
    widget.confirmed.setChecked(True)
    received = []
    widget.applied.connect(received.append)
    widget.use.click()
    assert received == [excerpt()]


def test_editing_text_or_source_page_invalidates_confirmation_and_requests_view(app):
    widget = TextbookExcerptWidget(verified_source(), "r1", 2, excerpt())
    assert widget.text.toPlainText() == excerpt()["text"]
    assert not widget.confirmed.isChecked()
    widget.confirmed.setChecked(True)
    widget.text.insertPlainText("补充")
    assert not widget.confirmed.isChecked() and not widget.use.isEnabled()
    widget.confirmed.setChecked(True)
    requested = []
    widget.pageRequested.connect(requested.append)
    widget.page.setCurrentIndex(0)
    assert requested == [1] and not widget.confirmed.isChecked()


@pytest.mark.parametrize("text", ["", " \n ", "字" * 1201])
def test_invalid_excerpt_does_not_enable_or_truncate(app, text):
    widget = TextbookExcerptWidget(verified_source(), "r1", 2)
    widget.text.setPlainText(text)
    widget.confirmed.setChecked(True)
    assert not widget.use.isEnabled()
    assert widget.text.toPlainText() == text


def test_stale_excerpt_not_prefilled_and_remove_is_explicit(app):
    widget = TextbookExcerptWidget(verified_source(), "r2", 2, excerpt())
    assert not widget.text.toPlainText()
    received = []
    widget.applied.connect(received.append)
    widget.remove.click()
    assert received == [None]


def test_real_viewer_applies_excerpt_releases_buffer_but_cancel_does_not_apply(app):
    bridge = _ManualBridge()
    viewer = TextbookSourceDialog(
        object(), {"concept_id": "C01", "revision": "r1"}, tasks=bridge
    )
    app.processEvents()
    bridge.succeed(bridge.pending.pop(), verified_source())
    app.processEvents()
    viewer.excerpt_button.click()
    panel = viewer.excerpt_panel
    assert panel is not None and not panel.text.toPlainText()
    panel.page.setCurrentIndex(1)
    assert viewer.view.pageNavigator().currentPage() == 1
    panel.text.setPlainText(excerpt()["text"])
    panel.hide_button.click()
    assert panel.isHidden() and viewer.excerpt is None
    viewer.excerpt_button.click()
    assert panel.text.toPlainText() == excerpt()["text"]
    panel.confirmed.setChecked(True)
    panel.use.click()
    assert viewer.result() == QDialog.DialogCode.Accepted
    assert viewer.excerpt == excerpt()
    assert viewer._closed and viewer.buffer.size() == 0 and viewer._source is None


class Facade:
    def __init__(self):
        self.calls = []

    def preparation_concept_options(self, _query):
        return [
            {
                "concept_id": "C01",
                "revision": "r1",
                "title": "概念",
                "statement": "候选",
            }
        ]

    def preparation_textbook_source(self, *_args):
        raise AssertionError("stub viewer only")

    def preparation_source_reference(self, *_args, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return {"materials": str(kwargs) or "参考", "warnings": []}


def install_preview(monkeypatch, value, *, accepted=True):
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog as module

    class Preview:
        def __init__(self, *_args, **_kwargs):
            self.excerpt = value

        def exec(self):
            return (
                QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
            )

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "TextbookSourceDialog", Preview)


def test_excerpt_flows_to_reference_recompile_uncheck_excludes_recheck_restores(
    app, monkeypatch
):
    facade = Facade()
    selector = PreparationSourcesDialog(facade)
    selector.concept_list.setCurrentRow(0)
    item = selector.concept_list.currentItem()
    install_preview(monkeypatch, excerpt())
    selector.original_button.click()
    assert item.checkState() == Qt.CheckState.Checked
    assert selector.selected_excerpts == [excerpt()]
    selector.preview_button.click()
    assert facade.calls[-1] == {"textbook_excerpts": [excerpt()]}
    selector._confirm()
    assert len(facade.calls) == 2 and facade.calls[-1] == facade.calls[-2]
    item.setCheckState(Qt.CheckState.Unchecked)
    assert selector.selected_excerpts == [] and selector.reference is None
    item.setCheckState(Qt.CheckState.Checked)
    assert selector.selected_excerpts == [excerpt()]


def test_cancel_keeps_preview_and_removal_invalidates_preview_not_concept(
    app, monkeypatch
):
    facade = Facade()
    selector = PreparationSourcesDialog(facade)
    selector.concept_list.setCurrentRow(0)
    install_preview(monkeypatch, excerpt())
    selector.original_button.click()
    selector.preview_button.click()
    before = deepcopy(selector.reference)
    install_preview(monkeypatch, None, accepted=False)
    selector.original_button.click()
    assert selector.reference == before and selector.selected_excerpts == [excerpt()]
    install_preview(monkeypatch, None)
    selector.original_button.click()
    assert selector.reference is None and selector.selected_excerpts == []
    assert len(selector.selected_concepts) == 1
    selector.preview_button.click()
    assert facade.calls[-1] == {}
