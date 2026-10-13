"""Visible card origin and exact navigation do not rewrite the composition."""
from copy import deepcopy
from io import BytesIO
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_composition_ui import desk, window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource,block,bind_knowledge_selection
from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore

def add_pair(d,panel):
    evidence={'source_path':'合成教材.pdf','source_sha256':'a'*64,'source_revision':'b'*64,
              'pdf_pages':[39],'printed_pages':[35]}
    card=resource('knowledge_note','合成知识卡：保留选图时的版本',[block('text','knowledge','完整合成正文')],
                  {**evidence,'record_id':'SYNTHETIC-READ-39'})
    buf=BytesIO();Image.new('RGB',(240,80),'#4b7791').save(buf,format='PNG')
    store=PreparationImageStore(d.facade.paths.task_root/'preparation-v1/images')
    asset=store.import_bytes(buf.getvalue(),'合成图','合成教材','界面回归')
    image=resource('textbook_image','合成原页裁片',[block('image','material',asset['asset_id'])],evidence)
    image=bind_knowledge_selection(image,card)
    panel.add_result({'resources':[card,image],'image_assets':[asset]},digest(d.history.value))
    panel.selected=image['id'];panel.refresh()
    return card,image

def test_visible_origin_returns_to_exact_saved_card_without_history_change(desk):
    d,panel,app,page=desk;card,image=add_pair(d,panel);settle(app)
    assert panel.knowledge_origin.isVisible() and card['title'] in panel.knowledge_origin.text()
    assert panel.knowledge_card_button.isVisible() and panel.knowledge_card_button.isEnabled()
    before=deepcopy(d.history.value);payload=deepcopy(page._payload())
    QTest.mouseClick(panel.knowledge_card_button,Qt.MouseButton.LeftButton);settle(app)
    assert panel.selected==card['id'] and d.history.value==before and page._payload()==payload
    assert panel.knowledge_textbook_button.isVisible() and not panel.knowledge_origin.isVisible()

def test_removed_card_disables_navigation_but_preserves_crop_provenance(desk):
    d,panel,app,page=desk;card,image=add_pair(d,panel);original=deepcopy(image['original'])
    panel.selected=card['id'];panel.remove_selected();panel.selected=image['id'];panel.refresh();settle(app)
    assert panel.knowledge_origin.isVisible() and card['title'] in panel.knowledge_origin.text()
    assert panel.knowledge_card_button.isVisible() and not panel.knowledge_card_button.isEnabled()
    assert panel.composition()['resources'][0]['original']==original
    before=deepcopy(d.history.value);panel.open_selected_knowledge_card();assert d.history.value==before
