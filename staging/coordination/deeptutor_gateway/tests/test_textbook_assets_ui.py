"""Synthetic PDF/asset integration: viewing stays separate from model material."""
import json
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from test_textbook_section_reader import section_fixture, chosen, ui_source
from integrations.deeptutor_shchem_v1.desktop_textbook_assets import load_textbook_assets
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

ASSETS = 'sh-chem-db/kb/textbook_knowledge_map_v1_2026-08-28/visual_assets.jsonl'

def candidate(asset_id, page, *, printed=None, source_sha='a' * 64, section='TB-E2-C1:1.1'):
    return {'visual_asset_id': asset_id, 'label': '素材索引专用内容 ' + asset_id,
            'description': '仅合成元数据，整页定位，不是裁图或审核记录。',
            'asset_type': 'synthetic_diagram', 'volume_id': 'TB-E2',
            'chapter_id': 'TB-E2-C1', 'section_key': section, 'supplement_node_key': None,
            'pdf_page': page, 'printed_page': printed, 'evidence_refs': ['SYNTHETIC'],
            'page_image_path': 'unused/synthetic.png', 'page_image_sha256': 'b' * 64,
            'source_sha256': source_sha, 'anchor_type': 'whole_page', 'bbox': None,
            'cropped': False, 'tags': [], 'upstream_index_sha256': 'c' * 64,
            'observation_method': 'synthetic_test_fixture',
            'asset_localization_status': 'whole_page_only_pending_bbox_review',
            'review_status': 'candidate-only', 'candidate_only': True,
            'human_reviewed': False, 'retrieval_ready': False, 'teaching_use_allowed': False,
            'generation_allowed': False, 'publication_allowed': False}

def write_assets(root, rows):
    path = root / ASSETS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    return path

def test_verified_source_scopes_assets_and_excludes_them_from_reference(section_fixture):
    f = section_fixture
    rows = [candidate('V1', 7, source_sha=f.row['source_sha256']),
            candidate('V2', 11, source_sha=f.row['source_sha256']),
            candidate('WRONG-BOOK', 8, source_sha='f' * 64)]
    path = write_assets(f.root, rows)
    before = [p.read_bytes() for p in (f.book, f.catalog, f.registry_path, path)]
    selection = chosen(f)
    concept = f.service.textbook_source(**selection)
    assert [r['visual_asset_id'] for r in concept['visual_assets']['assets']] == ['V1']
    section = f.service.textbook_section_source(**selection)
    assert [r['visual_asset_id'] for r in section['visual_assets']['assets']] == ['V1', 'V2']
    reference = f.service.reference(None, None, 1, 1, [selection])
    assert '素材索引专用内容' not in reference['materials']
    assert [p.read_bytes() for p in (f.book, f.catalog, f.registry_path, path)] == before
    path.write_text('{broken\n', encoding='utf-8')
    source = f.service.textbook_source(**selection)
    assert source['pdf_bytes'] == before[0]
    assert source['visual_assets']['assets'] == [] and source['visual_assets']['notices']

def test_real_pdf_asset_activation_and_page_changes_are_read_only(tmp_path):
    app = QApplication.instance() or QApplication([])
    rows = [candidate('V3', 3), candidate('V4', 4, printed=12)]
    write_assets(tmp_path, rows)
    catalog = load_textbook_assets(tmp_path, volume_id='TB-E2', section_key='TB-E2-C1:1.1',
                                  source_sha256='a' * 64, pdf_pages=[2, 3, 4])
    assert len(catalog['assets']) == 2
    class Bridge:
        def submit(self, _label, _operation, **callbacks):
            self.callbacks = callbacks
    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {'concept_id': 'C1', 'revision': 'r'},
                                  tasks=bridge, reading_mode='section')
    dialog.resize(420, 620)
    dialog.show()
    app.processEvents()
    source = ui_source()
    source['visual_assets'] = catalog
    frozen = deepcopy(source)
    bridge.callbacks['on_success'](source)
    app.processEvents()
    assert dialog.asset_list.count() == 2 and dialog.asset_list.isEnabled()
    assert '待核对' in dialog.asset_list.item(0).text()
    assert 'V3' not in dialog.asset_details.toPlainText()
    dialog.asset_list.itemActivated.emit(dialog.asset_list.item(0))
    app.processEvents()
    assert dialog.view.pageNavigator().currentPage() == 2
    assert 'V3' in dialog.asset_details.toPlainText()
    assert '整页' in dialog.asset_details.toPlainText()
    assert dialog.excerpt is None and not dialog.excerpt_button.isVisible()
    dialog._jump(1)
    assert 'V3' not in dialog.asset_details.toPlainText()
    forged = QListWidgetItem('out of scope')
    forged.setData(Qt.ItemDataRole.UserRole, 5)
    dialog._asset_page(forged)
    assert dialog.view.pageNavigator().currentPage() == 1
    assert dialog.next.mapTo(dialog, dialog.next.rect().bottomRight()).y() < dialog.height()
    assert source == frozen
    dialog.reject()
    dialog._asset_page(forged)
    assert dialog._source is None and not dialog.buffer.isOpen()
