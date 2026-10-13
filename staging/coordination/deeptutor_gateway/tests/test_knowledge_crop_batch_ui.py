"""Explicit multi-page choices enter the same native composition together."""
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from test_composition_ui import desk, window, personal
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block, bind_knowledge_selection
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_textbook_picker import KnowledgeTextbookPicker


def rows():
    return [{'relation':'知识卡原页', 'label':f'第{page}页', 'source_name':'合成教材',
             'selection':{'visual_asset_id':f'SYNTHETIC-PAGE-{page}', 'revision':str(page)*64}}
            for page in (8, 9)]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_ordered_multi_select_survives_search_and_is_independent_of_highlight(app):
    picker = KnowledgeTextbookPicker(object(), rows())
    picker.show(); app.processEvents()
    assert not picker.confirm.isEnabled() and not picker.chosen_rows
    picker.list.item(1).setCheckState(Qt.CheckState.Checked)
    picker.search.setText('第8页'); app.processEvents()
    picker.list.item(0).setCheckState(Qt.CheckState.Checked)
    picker.search.clear(); app.processEvents()
    assert [picker.list.item(i).checkState() for i in range(2)] == [Qt.CheckState.Checked]*2
    picker.list.setCurrentRow(0); picker.choose()
    assert picker.chosen_rows == rows()[::-1] and picker.page_preview._closed
    picker.deleteLater(); app.processEvents()


def test_empty_search_clears_preview_and_cancel_drops_all_choices(app):
    picker = KnowledgeTextbookPicker(object(), rows()); picker.show(); app.processEvents()
    picker.list.item(0).setCheckState(Qt.CheckState.Checked)
    picker.search.setText('没有这个标题'); app.processEvents()
    assert picker.list.count() == 0 and picker.page_preview._selection is None
    assert picker.confirm.isEnabled()
    picker.reject()
    assert not picker.chosen_rows and not picker._checked and picker.page_preview._closed
    picker.deleteLater(); app.processEvents()


def test_clear_and_narrow_layout_preserve_visible_controls(app):
    picker = KnowledgeTextbookPicker(object(), rows())
    picker.resize(420, 800); picker.show(); app.processEvents()
    assert picker.width() == 420 and picker.visual_split.orientation() == Qt.Orientation.Vertical
    assert picker.list.viewport().height() >= 108 and picker.page_preview.canvas.height() >= 120
    picker.list.item(0).setCheckState(Qt.CheckState.Checked); picker.clear_choices()
    assert not picker.confirm.isEnabled() and not picker._checked
    picker.choose(); assert picker.result() != QDialog.DialogCode.Accepted
    assert picker.confirm.mapTo(picker, picker.confirm.rect().center()).y() < picker.height()
    picker.reject(); picker.deleteLater(); app.processEvents()


def test_duplicate_selection_shown_once_and_limit_is_explicit(app, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_textbook_picker as ui
    monkeypatch.setattr(ui, 'MAX_IMAGES', 1)
    picker = KnowledgeTextbookPicker(object(), rows()+rows()[:1])
    assert picker.list.count() == 2
    picker.list.item(0).setCheckState(Qt.CheckState.Checked)
    picker.list.item(1).setCheckState(Qt.CheckState.Checked)
    assert len(picker._checked) == 1 and picker.list.item(1).checkState() == Qt.CheckState.Unchecked
    assert '最多1张' in picker.count.text()
    picker.reject(); picker.deleteLater(); app.processEvents()


def setup_card(d, panel):
    card = resource('knowledge_note', '合成多原页知识卡', [block('text', 'knowledge', '有完整原页依据的知识')],
        {'source_path':'合成教材.pdf', 'source_sha256':'a'*64, 'source_revision':'b'*64,
         'record_id':'SYNTHETIC-READ', 'pdf_pages':[8, 9], 'printed_pages':[4, 5]})
    panel.add_result({'resources':[card]}, digest(d.history.value))
    panel.selected = card['id']; panel.refresh()
    return card


def install_flow(monkeypatch, d, card, *, crop_accept=True, after_picker=None, after_crop=None):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as batch_ui
    snapshot = {'rows':rows(), 'binding':'c'*64, 'notices':[]}
    monkeypatch.setattr(sources, 'knowledge_textbook_options', lambda *_:deepcopy(snapshot))
    monkeypatch.setattr(d, 'run', lambda _label, task, done, **_kwargs:done(task()))
    def picker_exec(picker):
        picker.chosen_rows = deepcopy(rows())
        if after_picker: after_picker()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(KnowledgeTextbookPicker, 'exec', picker_exec)
    calls = []
    class Batch:
        def __init__(self, facade, tasks, choices, parent):
            calls.append(deepcopy(choices))
            self.selection_requests = [{'selection':deepcopy(row['selection'])} for row in choices]
        def exec(self):
            if after_crop: after_crop()
            return QDialog.DialogCode.Accepted if crop_accept else QDialog.DialogCode.Rejected
        def deleteLater(self): pass
    monkeypatch.setattr(batch_ui, 'TextbookBatchCropDialog', Batch)
    received = []
    def imported(_facade, item, binding, requests, assets):
        received.append((deepcopy(item), binding, deepcopy(requests)))
        new_assets = deepcopy(assets); images = []
        store = PreparationImageStore(d.facade.paths.task_root/'preparation-v1/images')
        for i, colour in enumerate(('navy', 'coral')):
            stream = BytesIO(); Image.new('RGB', (240, 80), colour).save(stream, format='PNG')
            asset = store.import_bytes(stream.getvalue(), f'合成教材图{i}', '合成教材', '比较解释')
            new_assets.append(asset)
            image = resource('textbook_image', f'这两幅图有什么不同{i}？',
                [block('image', 'material', asset['asset_id'])],
                {k:v for k,v in card['original']['evidence'].items() if k != 'record_id'})
            images.append(bind_knowledge_selection(image, card))
        return {'resources':images, 'image_assets':new_assets}
    monkeypatch.setattr(sources, 'import_knowledge_textbook_crops', imported)
    return calls, received


def test_whole_batch_keeps_card_and_enters_as_one_undoable_handoff(desk, monkeypatch):
    d, panel, app, page = desk; card = setup_card(d, panel); settle(app)
    before = deepcopy(d.history.value)
    calls, received = install_flow(monkeypatch, d, card)
    panel.add_knowledge_textbook(); settle(app)
    assert calls == [rows()] and received[0][0] == card
    value = panel.composition(); assert len(value['resources']) == 3, panel.status.text()
    assert value['resources'][0] == card
    assert all(r['original']['selected_from_knowledge']['resource_id'] == card['id'] for r in value['resources'][1:])
    after = deepcopy(d.history.value)
    d.travel(False); assert d.history.value == before
    d.travel(True); assert d.history.value == after


def test_cancel_batch_keeps_full_work_copy_and_images(desk, monkeypatch):
    d, panel, app, page = desk; card = setup_card(d, panel); settle(app)
    calls, received = install_flow(monkeypatch, d, card, crop_accept=False)
    before = deepcopy(d.history.value); payload = deepcopy(page._payload())
    panel.add_knowledge_textbook(); settle(app)
    assert calls == [rows()] and not received
    assert d.history.value == before and page._payload() == payload


@pytest.mark.parametrize('point', ['picker', 'crop'])
def test_draft_change_during_choice_rejects_whole_batch(desk, monkeypatch, point):
    d, panel, app, _page = desk; card = setup_card(d, panel)
    change = lambda:panel.add_result({'resources':[personal()]}, digest(d.history.value))
    calls, received = install_flow(monkeypatch, d, card,
        after_picker=change if point == 'picker' else None, after_crop=change if point == 'crop' else None)
    panel.add_knowledge_textbook(); settle(app)
    assert not received and len(panel.composition()['resources']) == 2
    assert len(calls) == (0 if point == 'picker' else 1) and '作品在选图期间变化' in panel.status.text()


def test_image_form_change_during_choice_is_not_overwritten(desk, monkeypatch):
    d, panel, app, page = desk; card = setup_card(d, panel)
    def change():
        stream = BytesIO(); Image.new('RGB', (240, 80), 'olive').save(stream, format='PNG')
        asset = PreparationImageStore(d.facade.paths.task_root/'preparation-v1/images').import_bytes(
            stream.getvalue(), '其他入口新加入图', '合成输入', '历史变化')
        page.image_assets_widget.set_assets([asset])
    calls, received = install_flow(monkeypatch, d, card, after_crop=change)
    panel.add_knowledge_textbook(); settle(app)
    assert not received and len(panel.composition()['resources']) == 1
    assert page.image_assets_widget.assets()[0]['caption'] == '其他入口新加入图'
    assert '作品在选图期间变化' in panel.status.text()
