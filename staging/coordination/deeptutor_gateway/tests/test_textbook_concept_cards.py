"""Exact summary records, explicit selection and stale-source rejection."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture,disk_bytes
from test_textbook_supplements import supplement_fixture as supplement_fixture
from test_textbook_bookshelf_ui import facade_for
from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import concept_options,concept_page_selection
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import validate_resource,digest,edit_resource
from integrations.deeptutor_shchem_v1.desktop_composition_source_summary import raw_source_text
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService
from test_composition_flow import composition


def ready(f,tmp_path):
    f.facade=facade_for(f);f.facade.paths.task_root=tmp_path.parent/(tmp_path.name+'-concept-state')
    f.facade.personal_textbook_study=lambda:SimpleNamespace(books=lambda:[])
    f.option=concept_options(f.root)[0];return f


@pytest.fixture
def concept(shelf_fixture,tmp_path):return ready(shelf_fixture,tmp_path)


@pytest.fixture
def supplement(supplement_fixture,tmp_path):return ready(supplement_fixture,tmp_path)


def test_metadata_listing_includes_summaries_without_changing_reading_contract_or_reading_pdf(concept,monkeypatch):
    f=concept;before=disk_bytes(f.root)
    monkeypatch.setattr(type(f.service),'textbook_source',lambda *_:pytest.fail('listing read original'))
    options=sources.knowledge_options(f.facade)
    assert len(options['concepts'])==1 and options['reading_notes']==[]
    row=options['concepts'][0]
    assert row['summary']==f.row['statement'] and row['origin']=='textbook_concept'
    assert row['printed_pages']==[3,4,6] and row['section_title']=='1.1 合成第1.1节'
    assert row['selection']['kind']=='textbook_concept' and 'note_id' not in row
    assert disk_bytes(f.root)==before and not f.facade.paths.task_root.exists()


def test_import_exact_record_and_display_body_keep_source_and_closed_flags(concept):
    f=concept;before=disk_bytes(f.root)
    card=sources.import_knowledge_note(f.facade,f.option['selection'])
    evidence=card['original']['evidence']
    assert evidence['concept_source']['record']==f.row
    assert evidence['record_id']==f.row['concept_id'] and evidence['printed_pages']==[3,4,6]
    assert [b['value'] for b in card['blocks'] if b['role']=='knowledge']==[f.row['statement']]
    assert 'concept_source' in raw_source_text(card)
    assert not any(evidence['source_sha256'] in b['value'] for b in card['blocks'])
    assert all(card[k] is False for k in ('human_reviewed','generation_allowed','publication_allowed','official'))
    value=edit_resource(composition(card),card['id'],locked=False)
    value=edit_resource(value,card['id'],values={card['blocks'][0]['id']:'教师工作副本'})
    assert value['resources'][0]['original']==card['original'] and validate_resource(value['resources'][0])
    assert disk_bytes(f.root)==before and not f.facade.paths.task_root.exists()


@pytest.mark.parametrize('change',['record','statement','location'])
def test_invalid_sealed_concept_receipt_cannot_be_restored(concept,change):
    card=sources.import_knowledge_note(concept.facade,concept.option['selection'])
    if change=='record':card['original']['evidence']['concept_source']['record']['statement']+='换原文'
    elif change=='statement':card['original']['blocks'][0]['value']='换原文'
    else:card['original']['evidence']['concept_source']['location']['source_guess']='guess'
    card['original_sha256']=digest(card['original'])
    with pytest.raises(ValueError):validate_resource(card)


@pytest.mark.parametrize('change',['concept','directory','book'])
def test_stale_summary_directory_and_book_do_not_import(concept,change):
    f=concept
    if change=='concept':f.row['statement']+='变化';f.catalog.write_text(json.dumps(f.row),encoding='utf8')
    elif change=='directory':f.registry['volumes'][0]['volume_title']+='变化';f.activate()
    else:f.book.write_bytes(f.book.read_bytes()+b'changed')
    with pytest.raises(ValueError):sources.import_knowledge_note(f.facade,f.option['selection'])
    assert not f.facade.paths.task_root.exists()


def test_supplement_summary_keeps_binding_and_only_literal_pages(supplement):
    f=supplement;card=sources.import_knowledge_note(f.facade,f.option['selection'])
    evidence=card['original']['evidence'];location=evidence['concept_source']['location']
    assert location['section_key'] is None and location['supplement_node_key']=='declared-project'
    assert evidence['pdf_pages']==[43,45] and evidence['printed_pages']==[38,40]
    options=sources.knowledge_textbook_options(f.facade,card)
    assert [r['pdf_page'] for r in options['rows'][-2:]]==[43,45]
    assert options['rows'][0]['pdf_page']==44 and options['rows'][0]['relation']=='本栏目其他图表'
    f.product['supplement_nodes'][0]['title']+='变化';f.write_product()
    with pytest.raises(ValueError):sources.knowledge_textbook_options(f.facade,card)


def test_conflicting_and_unmapped_printed_pages_are_unknown(concept):
    f=concept;f.row['printed_pages']=[999,1000];f.catalog.write_text(json.dumps(f.row),encoding='utf8')
    option=concept_options(f.root)[0];assert option['printed_pages']=='unknown'
    card=sources.import_knowledge_note(f.facade,option['selection'])
    assert card['original']['evidence']['concept_source']['record']['printed_pages']==[999,1000]
    f.row['section_key']=None;f.row.pop('printed_pages');f.catalog.write_text(json.dumps(f.row),encoding='utf8')
    option=concept_options(f.root)[0]
    assert option['section_title']=='待归章节' and option['printed_pages']=='unknown'
    assert option['selection']['location']['section_key'] is None


def test_concept_page_crop_retains_record_version_card_and_original_pdf(concept):
    f=concept;card=sources.import_knowledge_note(f.facade,f.option['selection']);before=disk_bytes(f.root)
    options=sources.knowledge_textbook_options(f.facade,card)
    chosen=concept_page_selection(f.option,8)
    service=TextbookPreparationCropService(f.root,f.facade.paths.task_root)
    page=service.page(chosen);bounds={'left':100,'top':100,'right':610,'bottom':630}
    preview=service.preview(chosen,page['page_revision'],bounds,'合成图','合成用途')
    request={'selection':chosen,'page_revision':page['page_revision'],'bounds':bounds,
             'caption':'合成图','purpose':'合成用途','expected_preview_revision':preview['preview_revision'],
             'expected_image_sha256':preview['image_asset']['sha256'],'selection_confirmed':True}
    assert not f.facade.paths.task_root.exists()
    result=sources.import_knowledge_textbook_crop(f.facade,card,options['binding'],request,[])
    receipt=json.loads(next((f.facade.paths.task_root/'preparation-v1/textbook-crops').glob('*.json')).read_text(encoding='utf8'))
    assert receipt['source_kind']=='textbook_concept_page' and receipt['record_id']==f.row['concept_id']
    assert receipt['concept_revision']==f.option['revision'] and receipt['printed_page']==4
    assert result['resources'][0]['original']['selected_from_knowledge']['record_id']==f.row['concept_id']
    assert disk_bytes(f.root)==before and not receipt['human_reviewed'] and not receipt['generation_allowed']


def test_batch_rechecks_earlier_card_after_later_import(concept,monkeypatch):
    f=concept;other=deepcopy(f.row);other['concept_id']='second-summary'
    f.catalog.write_text(json.dumps(f.row)+'\n'+json.dumps(other),encoding='utf8')
    selections=[r['selection'] for r in concept_options(f.root)]
    real=sources.import_knowledge_note;count=0
    def late(*args):
        nonlocal count
        card=real(*args);count+=1
        if count==2:
            f.row['statement']+='后改动';f.catalog.write_text(json.dumps(f.row)+'\n'+json.dumps(other),encoding='utf8')
        return card
    monkeypatch.setattr(sources,'import_knowledge_note',late)
    with pytest.raises(ValueError,match='变化'):sources.import_knowledge_cards(f.facade,selections)
    assert not f.facade.paths.task_root.exists()


def test_original_reader_uses_typed_identity_not_display_page(concept):
    option=deepcopy(concept.option);option['pdf_pages']=[999];option['section_key']='forged'
    result=sources.knowledge_reader_selection(concept.facade,option)
    assert result=={'reading_mode':'concept_card','selection':concept.option['selection']}
    with pytest.raises(ValueError):concept_page_selection(concept.option,True)
    with pytest.raises(ValueError):concept_page_selection(concept.option,99)
