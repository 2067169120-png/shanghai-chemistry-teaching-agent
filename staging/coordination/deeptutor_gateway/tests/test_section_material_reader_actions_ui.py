"""New reader intent is enabled only after a current original page loads."""
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, Qt, Signal
from PySide6.QtGui import QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout

from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
from integrations.deeptutor_shchem_v1.desktop_workbench import knowledge_card_reader as knowledge_ui
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_crop_source_dialog import TextbookCropSourceDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class FakeReader(QDialog):
    reading_state_changed = Signal()

    def __init__(self, facade, selection, parent, **options):
        super().__init__(parent)
        self._loaded_pages = self._closed = False
        self.selection, self.options = deepcopy(selection), options
        self.releases = 0
        QVBoxLayout(self).addWidget(QLabel('合成完整原页'))

    def reject(self):
        self.releases += 1
        self._closed = True
        self.reading_state_changed.emit()
        super().reject()


def knowledge_card():
    return resource('knowledge_note', '原知识',
        [block('text', 'knowledge', '完整课堂讲解\n条件和适用范围'),
         block('text', 'teacher_note', '后台来源')],
        {'source_path': '合成原书.pdf', 'source_sha256': 'a' * 64,
         'source_revision': 'b' * 64, 'record_id': 'SYNTHETIC-1',
         'pdf_pages': [1], 'printed_pages': [1]})


def cleanup(dialog, app):
    dialog.reject()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize('action', ['section', 'old_pick', 'close'])
def test_knowledge_reader_explicit_intent_preserves_old_pick_semantics(app, monkeypatch, action):
    monkeypatch.setattr(knowledge_ui, 'TextbookSourceDialog', FakeReader)
    calls = []
    dialog = knowledge_ui.KnowledgeCardReader(SimpleNamespace(), knowledge_card(),
        {'selection': {'section_key': 'S1'}, 'reading_mode': 'section'}, current=lambda: calls.append(True))
    try:
        assert not dialog.section_materials_button.isEnabled() and not dialog.pick.isEnabled()
        dialog._choose_section_materials()
        assert not dialog.section_materials_requested and not dialog._released
        dialog.reader._loaded_pages = True
        dialog.reader.reading_state_changed.emit()
        assert dialog.section_materials_button.isEnabled() and dialog.pick.isEnabled()
        if action == 'section':
            dialog.section_materials_button.click()
            assert dialog.result() == QDialog.DialogCode.Accepted
            assert dialog.action == 'section_materials' and dialog.section_materials_requested
        elif action == 'old_pick':
            dialog.pick.click()
            assert dialog.result() == QDialog.DialogCode.Accepted
            assert dialog.action == 'pick' and not dialog.section_materials_requested
        else:
            dialog.close_button.click()
            assert dialog.result() == QDialog.DialogCode.Rejected
            assert dialog.action is None and not dialog.section_materials_requested
        assert dialog.reader.releases == 1 and calls
        dialog.done(dialog.result())
        assert dialog.reader.releases == 1
    finally:
        cleanup(dialog, app)


def test_knowledge_reader_stale_guard_prevents_new_flow_and_keeps_full_body(app, monkeypatch):
    monkeypatch.setattr(knowledge_ui, 'TextbookSourceDialog', FakeReader)
    current = {'valid': True}
    def guard():
        if not current['valid']:
            raise ValueError('课件已变化')
    dialog = knowledge_ui.KnowledgeCardReader(SimpleNamespace(), knowledge_card(),
        {'selection': {'section_key': 'S1'}, 'reading_mode': 'section'}, current=guard)
    try:
        dialog.show()
        dialog.resize(420, 780)
        app.processEvents()
        assert dialog.split.orientation() == Qt.Orientation.Vertical
        assert dialog.body.toPlainText() == '完整课堂讲解\n条件和适用范围'
        assert dialog.body.height() >= 60 and dialog.reader.height() >= 300
        dialog.reader._loaded_pages = True
        dialog._reading_ready()
        current['valid'] = False
        dialog._choose_section_materials()
        assert dialog._released and not dialog.section_materials_requested
        assert not dialog.section_materials_button.isEnabled() and not dialog.pick.isEnabled()
    finally:
        cleanup(dialog, app)


def pdf_bytes():
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    painter = QPainter(writer)
    painter.drawText(100, 500, 'Original textbook page - synthetic standalone UI test')
    painter.end()
    del writer
    buffer.close()
    return bytes(data)


@pytest.fixture
def crop(app, monkeypatch):
    # Keep source scheduling explicit, while rendering a real PDF in QPdfView.
    monkeypatch.setattr(TextbookSourceDialog, '_load', lambda self: None)
    state = {'valid': True, 'calls': 0}
    def guard():
        state['calls'] += 1
        if not state['valid']:
            raise ValueError('裁片原页的当前课件已变化')
    selection = {'visual_asset_id': 'SYNTHETIC-PAGE', 'revision': 'a' * 64}
    context = {'selection': selection, 'binding_sha256': 'b' * 64}
    source = {**selection, 'reading_mode': 'asset', 'volume_title': '合成原教材',
              'title': '原图', 'source_name': '合成原教材.pdf', 'pdf_pages': [1],
              'printed_page': 1, 'statement': '合成原页读取', 'pdf_bytes': pdf_bytes()}
    facade = SimpleNamespace(preparation_textbook_crop_original=lambda *a, **kw: {'source': deepcopy(source)})
    dialog = TextbookCropSourceDialog(facade, {'title': '裁片标题'}, [], context, current=guard)
    yield SimpleNamespace(dialog=dialog, source=source, state=state, app=app)
    cleanup(dialog, app)


def test_crop_section_action_waits_for_real_pdf_load_then_closes_reader(crop):
    d = crop.dialog
    assert not d.section_materials_button.isEnabled()
    d._choose_section_materials()
    assert not d.section_materials_requested and not d._closed
    d._source_ready(deepcopy(crop.source))
    crop.app.processEvents()
    assert d._loaded_pages and d.document.pageCount() == 1
    assert d.section_materials_button.isEnabled()
    d.section_materials_button.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.action == 'section_materials' and d.section_materials_requested
    assert d._closed and d._source is None and not d._stale_timer.isActive()
    assert not d.section_materials_button.isEnabled()


@pytest.mark.parametrize('case', ['stale_before_load', 'stale_after_load', 'close', 'pdf_error'])
def test_crop_stale_cancel_or_bad_pdf_never_requests_new_flow(crop, case):
    d = crop.dialog
    if case == 'stale_before_load':
        crop.state['valid'] = False
    source = deepcopy(crop.source)
    if case == 'pdf_error':
        source['pdf_bytes'] = b'not a PDF'
    d._source_ready(source)
    crop.app.processEvents()
    if case == 'stale_after_load':
        assert d.section_materials_button.isEnabled()
        crop.state['valid'] = False
        d._guard_current()
    elif case == 'close':
        d.close_button.click()
    d._choose_section_materials()
    assert not d.section_materials_requested
    assert d.action is None and not d.section_materials_button.isEnabled()
    assert crop.state['calls'] > 0
