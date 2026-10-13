"""Real source-bound pages even without an asset entry; stale notes cannot hand off."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture, candidate
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_reading_hints import note as hint, write_packet
from test_personal_textbook_knowledge import native_fixture as native_fixture
from test_personal_textbook_study import chapter, note as personal_note
from test_composition_inline_personal import personal_facade
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_composition_sources import (
    import_knowledge_note, knowledge_textbook_options, import_knowledge_textbook_crop)
from integrations.deeptutor_shchem_v1.desktop_note_page_crops import note_page_selection
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService
from integrations.deeptutor_shchem_v1.reader_cancellation import TaskCancellationEvent, ReadCancelled, read_cancel_scope


@pytest.fixture
def notes(shelf_fixture, tmp_path):
    f = shelf_fixture
    f.note = hint(volume_id=f.row['volume_id'], section_key=f.row['section_key'],
                  pdf_pages=[8, 9], printed_pages=[4, 5],
                  textbook_evidence={'source_sha256': f.row['source_sha256'],
                                     'pdf_pages': [8, 9], 'printed_pages': [4, 5]})
    f.note_path = write_packet(f.root, [f.note])
    f.task_root = tmp_path.parent / (tmp_path.name+'-note-page-state')
    f.facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    f.facade.paths = SimpleNamespace(workspace_root=f.root, task_root=f.task_root)
    f.current = f.shelf.snapshot()['reading_notes'][0]
    f.selected = {k: f.current[k] for k in ('note_id', 'section_key', 'revision', 'directory_revision')}
    f.item = import_knowledge_note(f.facade, f.selected)
    f.crops = TextbookPreparationCropService(f.root, f.task_root)
    return f


def request(f, page=8):
    selected = note_page_selection(f.current, page)
    source = f.crops.page(selected)
    bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    preview = f.crops.preview(selected, source['page_revision'], bounds, '合成裁片', '核对原图边界')
    return {'selection': selected, 'page_revision': source['page_revision'], 'bounds': bounds,
            'caption': '合成裁片', 'purpose': '核对原图边界',
            'expected_preview_revision': preview['preview_revision'],
            'expected_image_sha256': preview['image_asset']['sha256'], 'selection_confirmed': True}


def test_card_lists_same_page_first_and_unindexed_explicit_pages_without_pdf_read(notes, monkeypatch):
    f = notes
    other = candidate(f, visual_asset_id='SYNTHETIC-ASSET-2', pdf_page=10, printed_page=6)
    f.write_assets([other, f.asset])
    before = disk_bytes(f.root)
    monkeypatch.setattr('integrations.deeptutor_shchem_v1.desktop_textbook_asset_catalog._read_pdf',
                        lambda *_: pytest.fail('option listing must not read the PDF'))
    value = knowledge_textbook_options(f.facade, f.item)
    assert [r['relation'] for r in value['rows']] == ['同页图表', '本节其他图表', '知识卡原页', '知识卡原页']
    assert [r['pdf_page'] for r in value['rows'][-2:]] == [8, 9]
    assert value['rows'][0]['visual_asset_id'] == f.asset['visual_asset_id']
    assert disk_bytes(f.root) == before and not f.task_root.exists()


def test_unindexed_page_crop_preserves_note_receipt_and_original_card(notes):
    f = notes; before = disk_bytes(f.root); original = deepcopy(f.item)
    options = knowledge_textbook_options(f.facade, f.item); chosen = request(f)
    assert not f.task_root.exists()  # Original-page and crop previews are read-only.
    result = import_knowledge_textbook_crop(f.facade, f.item, options['binding'], chosen, [])
    assert len(result['resources']) == len(result['image_assets']) == 1
    assert result['resources'][0]['original']['evidence']['pdf_pages'] == [8]
    record = json.loads(next((f.task_root/'preparation-v1/textbook-crops').glob('*.json')).read_text(encoding='utf8'))
    assert record['source_kind'] == 'knowledge_note_page' and record['record_id'] == f.current['note_id']
    assert record['note_revision'] == f.current['revision'] and record['directory_revision'] == f.current['directory_revision']
    assert record['printed_page'] == 4 and record['candidate_only'] is True
    assert all(record[k] is False for k in ('human_reviewed','retrieval_ready','generation_allowed','publication_allowed'))
    assert f.item == original and disk_bytes(f.root) == before


@pytest.mark.parametrize('change', ['note', 'book', 'directory', 'outside_page', 'bool_page'])
def test_stale_or_unlisted_page_cannot_save(notes, change):
    f = notes; selected = note_page_selection(f.current, 8)
    if change == 'note':
        f.note['summary'] += '已修改'; write_packet(f.root, [f.note])
    elif change == 'book':
        f.book.write_bytes(f.book.read_bytes()+b'changed')
    elif change == 'directory':
        f.registry['volumes'][0]['volume_title'] += '已修改'; f.activate()
    elif change == 'outside_page':
        selected['visual_asset_id'] = selected['visual_asset_id'].rsplit('-', 1)[0]+'-10'
    else:
        with pytest.raises(ValueError): note_page_selection(f.current, True)
        return
    with pytest.raises(ValueError): f.crops.page(selected)
    assert not f.task_root.exists()


def test_late_note_change_during_original_read_is_rejected(notes, monkeypatch):
    f = notes; selected = note_page_selection(f.current, 8)
    real = f.shelf.section_source
    def changed(*args):
        value = real(*args); f.note['summary'] += '读取后改动'; write_packet(f.root, [f.note]); return value
    monkeypatch.setattr(type(f.shelf), 'section_source', lambda _self, *args: changed(*args))
    with pytest.raises(ValueError): f.crops.page(selected)
    assert not f.task_root.exists()


def test_linked_asset_selection_rechecks_note_and_rejects_unrelated_page(notes):
    f = notes; options = knowledge_textbook_options(f.facade, f.item); chosen = request(f)
    f.note['summary'] += '已修改'; write_packet(f.root, [f.note])
    with pytest.raises(ValueError):
        import_knowledge_textbook_crop(f.facade, f.item, options['binding'], chosen, [])
    assert not f.task_root.exists()


def test_cancelled_page_read_has_no_side_effects(notes):
    f = notes; event = TaskCancellationEvent(); event.set()
    before = disk_bytes(f.root)
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        f.crops.page(note_page_selection(f.current, 8))
    assert disk_bytes(f.root) == before and not f.task_root.exists()


def test_personal_card_scopes_pages_and_note_edit_invalidates_handoff(native_fixture):
    from integrations.deeptutor_shchem_v1.desktop_composition_sources import knowledge_options
    f=native_fixture;view=personal_note(f,chapter(f));facade=personal_facade(f)
    card=import_knowledge_note(facade,knowledge_options(facade)['reading_notes'][0]['selection'])
    options=knowledge_textbook_options(facade,card)
    assert len(options['rows'])==1 and options['rows'][0]['pdf_page']==2
    assert options['rows'][0]['selection']['visual_asset_id'].startswith('BOOKPAGE-')
    f.study.save_note(f.selection,view['study_revision'],section_id=view['sections'][0]['section_id'],
        note_id=view['notes'][0]['note_id'],title=view['notes'][0]['title'],text='修改后的个人笔记',
        pdf_page=2,teacher_confirmed=True)
    with pytest.raises(ValueError,match='已变化'):knowledge_textbook_options(facade,card)
    assert f.source.read_bytes()==f.original


def test_unrelated_choice_and_late_note_edit_cannot_return_partial_handoff(notes,monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
    f=notes;options=knowledge_textbook_options(f.facade,f.item);chosen=request(f)
    wrong=deepcopy(chosen);wrong['selection']={'visual_asset_id':'UNRELATED','revision':'a'*64}
    with pytest.raises(ValueError):import_knowledge_textbook_crop(f.facade,f.item,options['binding'],wrong,[])
    assert not f.task_root.exists()
    real=sources.import_textbook_crop
    def late(*args):
        result=real(*args);f.note['summary']+='后改动';write_packet(f.root,[f.note]);return result
    monkeypatch.setattr(sources,'import_textbook_crop',late)
    with pytest.raises(ValueError):import_knowledge_textbook_crop(f.facade,f.item,options['binding'],chosen,[])
    # Only closed candidate receipts may remain; no composition result escaped.
    records=list((f.task_root/'preparation-v1/textbook-crops').glob('*.json'));assert len(records)==1
    record=json.loads(records[0].read_text(encoding='utf8'));assert record['human_reviewed'] is False
