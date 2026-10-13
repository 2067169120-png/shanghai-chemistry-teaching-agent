"""Textbook reading keeps the lesson intact and rechecks before selecting crops."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
from test_composition_ui import desk as desk, personal
from test_question_explorer_ui import window as window
from test_word_question_dialog import _preparation_asset
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_card_reader import KnowledgeCardReader


class FakeReader(QDialog):
    reading_state_changed = Signal()

    def __init__(self, facade, selection, parent, **kwargs):
        super().__init__(parent)
        self.selection, self.options = deepcopy(selection), kwargs
        self._loaded_pages = self._closed = False
        self.releases = 0
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('合成教材原页，只读'))

    def reject(self):
        self.releases += 1
        self._closed = True
        super().reject()


def setup(desk, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_reading as backend
    from integrations.deeptutor_shchem_v1.desktop_workbench import knowledge_card_reader as ui
    d, panel, app, page = desk
    card = resource('knowledge_note', '原知识标题',
        [block('text', 'knowledge', '完整定义\n第二段条件\n第三段比较'),
         block('text', 'teacher_note', '内部来源记录')],
        {'source_path': '合成教材.pdf', 'source_sha256': 'a'*64,
         'source_revision': 'b'*64, 'record_id': 'SYNTHETIC-READER',
         'pdf_pages': [8, 9], 'printed_pages': [4, 5]})
    card['title'] = '为什么同一现象需要比较条件？'
    panel.add_result({'resources': [card]}, digest(d.history.value))
    panel.selected = card['id']; panel.refresh(); app.processEvents()
    context = {'reading_mode': 'section', 'selection': {'section_key': 'SYNTHETIC'},
               'original_title': '原知识标题', 'binding_sha256': 'c'*64}
    calls = []
    monkeypatch.setattr(backend, 'knowledge_card_reader',
        lambda facade, value: calls.append(('open', deepcopy(value))) or deepcopy(context))
    monkeypatch.setattr(backend, 'recheck_knowledge_card_reader',
        lambda facade, value, result: calls.append(('recheck', deepcopy(value), deepcopy(result))) or True)
    monkeypatch.setattr(ui, 'TextbookSourceDialog', FakeReader)
    monkeypatch.setattr(d, 'run', lambda _title, operation, done, **kwargs: done(operation()))
    return d, panel, app, page, card, context, calls


def test_cancel_reads_sealed_source_and_full_lesson_without_changes(desk, monkeypatch):
    d, panel, app, page, card, context, calls = setup(desk, monkeypatch)
    before, assets = deepcopy(d.history.value), page.image_assets_widget.assets()
    readers = []
    def cancel(dialog):
        readers.append(dialog.reader)
        assert dialog.reader.selection == context['selection']
        assert dialog.reader.options == {'reading_mode': 'section', 'embedded': True}
        assert dialog.heading.text().endswith(card['title'])
        assert dialog.body.toPlainText() == '完整定义\n第二段条件\n第三段比较'
        assert not dialog.pick.isEnabled()
        dialog.reject()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(KnowledgeCardReader, 'exec', cancel)
    panel.knowledge_read_button.click(); app.processEvents()
    assert calls == [('open', card)]
    assert readers[0]._closed and readers[0].releases == 1
    assert d.history.value == before and page.image_assets_widget.assets() == assets
    panel.add_result({'resources': [personal()]}, digest(d.history.value))
    assert not panel.knowledge_read_button.isVisible()


@pytest.mark.parametrize('result', [QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected])
def test_reader_resizes_and_releases_once(desk, monkeypatch, result):
    d, panel, app, page, card, context, calls = setup(desk, monkeypatch)
    dialog = KnowledgeCardReader(d.facade, card, context, panel)
    dialog.show(); dialog.resize(1160, 820); app.processEvents()
    assert dialog.split.orientation() == Qt.Orientation.Horizontal
    dialog.resize(420, 780); app.processEvents()
    assert dialog.split.orientation() == Qt.Orientation.Vertical
    assert dialog.reader.height() >= 300 and dialog.body.height() >= 60
    assert dialog.pick.isVisible() and dialog.close_button.isVisible()
    dialog.reader._loaded_pages = True; dialog.reader.reading_state_changed.emit()
    assert dialog.pick.isEnabled()
    dialog.reader._loaded_pages = False; dialog.reader.reading_state_changed.emit()
    assert not dialog.pick.isEnabled()
    dialog.done(result); dialog.done(result)
    assert dialog.reader.releases == 1
    dialog.deleteLater(); app.processEvents()


def test_continue_to_images_rechecks_and_keeps_original_lesson(desk, monkeypatch):
    d, panel, app, page, card, context, calls = setup(desk, monkeypatch)
    before = deepcopy(d.history.value); picked = []
    def choose(dialog):
        dialog.accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeCardReader, 'exec', choose)
    monkeypatch.setattr(panel, 'add_knowledge_textbook', lambda: picked.append(panel.selected))
    panel.read_knowledge_original(); app.processEvents()
    assert calls == [('open', card), ('recheck', card, context)]
    assert picked == [card['id']] and d.history.value == before


@pytest.mark.parametrize('change', ['draft', 'assets', 'selection', 'stopping', 'closing'])
@pytest.mark.parametrize('phase', ['before_open', 'after_reading'])
def test_changed_lesson_blocks_open_or_image_handoff(desk, monkeypatch, change, phase):
    d, panel, app, page, card, context, calls = setup(desk, monkeypatch)
    pending, opened, picked = [], [], []
    monkeypatch.setattr(d, 'run', lambda _title, operation, done, **kwargs: pending.append((operation, done)))
    monkeypatch.setattr(panel, 'add_knowledge_textbook', lambda: picked.append(True))
    def changed():
        if change == 'draft': panel.add_result({'resources': [personal()]}, digest(d.history.value))
        elif change == 'assets': page.image_assets_widget.set_assets([_preparation_asset(777)])
        elif change == 'selection': panel.selected = None; panel.refresh()
        elif change == 'stopping': d._stopping = True
        else: d._close_when_finished = True
    def choose(dialog):
        opened.append(True)
        if phase == 'after_reading': changed()
        dialog.accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeCardReader, 'exec', choose)
    panel.read_knowledge_original(); result = pending[0][0]()
    if phase == 'before_open': changed()
    pending[0][1](result)
    assert opened == ([] if phase == 'before_open' else [True])
    assert len(pending) == 1 and not picked
    d._stopping = d._close_when_finished = False


@pytest.mark.parametrize('change', ['source', 'closing'])
def test_stale_source_or_late_callback_does_not_open_picker(desk, monkeypatch, change):
    from integrations.deeptutor_shchem_v1 import desktop_knowledge_reading as backend
    d, panel, app, page, card, context, calls = setup(desk, monkeypatch)
    pending, picked = [], []
    monkeypatch.setattr(d, 'run', lambda _title, operation, done, **kwargs: pending.append((operation, done)))
    monkeypatch.setattr(panel, 'add_knowledge_textbook', lambda: picked.append(True))
    def choose(dialog):
        dialog.accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeCardReader, 'exec', choose)
    if change == 'source':
        def stale(*args): raise ValueError('原教材版本已变化')
        monkeypatch.setattr(backend, 'recheck_knowledge_card_reader', stale)
    panel.read_knowledge_original(); pending[0][1](pending[0][0]())
    if change == 'source':
        with pytest.raises(ValueError, match='原教材版本'): pending[1][0]()
    else:
        result = pending[1][0]()
        d._close_when_finished = True
        before = deepcopy((d.history.value, d.history.back, d.history.forward, page._payload()))
        with pytest.raises(ValueError, match='选材窗口已关闭或停止，本批未加入。'): pending[1][1](result)
        assert (d.history.value, d.history.back, d.history.forward, page._payload()) == before
    assert not picked
    d._close_when_finished = False
