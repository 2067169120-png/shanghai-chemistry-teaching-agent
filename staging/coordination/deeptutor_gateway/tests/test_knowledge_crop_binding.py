"""Explicit card selection is durable provenance, never inferred relevance."""
from copy import deepcopy
import pytest
from test_note_page_crops import notes, section_fixture, asset_fixture, shelf_fixture, request
from integrations.deeptutor_shchem_v1.desktop_composition_sources import knowledge_textbook_options, import_knowledge_textbook_crop, import_textbook_crop
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import validate_resource, validate_composition, digest, edit_resource
from integrations.deeptutor_shchem_v1.desktop_composition_source_summary import source_summary, raw_source_text
from test_composition_flow import composition

def linked(f):
    options=knowledge_textbook_options(f.facade,f.item)
    return import_knowledge_textbook_crop(f.facade,f.item,options['binding'],request(f),[])['resources'][0]

def test_real_crop_handoff_binds_original_card_even_after_work_copy_edit(notes):
    f=notes;original=deepcopy(f.item)
    f.item['title']='教师在作品中修改的标题'
    image=linked(f);choice=image['original']['selected_from_knowledge']
    assert choice=={'resource_id':f.item['id'],'title':original['original']['title'],
        'record_id':original['original']['evidence']['record_id'],'original_sha256':original['original_sha256'],
        'source_sha256':original['original']['evidence']['source_sha256'],
        'source_revision':original['original']['evidence']['source_revision']}
    assert choice['title'] in source_summary(image) and '用途待教师核对' in source_summary(image)
    assert 'selected_from_knowledge' in raw_source_text(image,full=True)
    assert not image['human_reviewed'] and not image['generation_allowed'] and not image['publication_allowed']
    assert validate_resource(image)==image

@pytest.mark.parametrize('change',['source','digest','extra','kind'])
def test_mismatched_selection_metadata_cannot_be_restored(notes,change):
    image=linked(notes)
    if change=='source':image['original']['selected_from_knowledge']['source_sha256']='c'*64
    elif change=='digest':image['original']['selected_from_knowledge']['title']+='静默更改'
    elif change=='extra':image['original']['selected_from_knowledge']['chemical_relevance_approved']=True
    else:image['original']['kind']='personal_text'
    if change!='digest':image['original_sha256']=digest(image['original'])
    with pytest.raises(ValueError):validate_resource(image)

def test_card_removal_and_explicit_image_edits_preserve_origin_snapshot(notes):
    image=linked(notes);original=deepcopy(image['original'])
    value=composition(notes.item,image);card_id=notes.item['id']
    value['resources']=[r for r in value['resources'] if r['id']!=card_id];del value['placements'][card_id]
    value=validate_composition(value)
    unlocked=edit_resource(value,image['id'],locked=False)
    updated=edit_resource(unlocked,image['id'],title='教师的新图题')
    assert updated['resources'][0]['original']==original
    assert notes.item['original']['title'] in source_summary(updated['resources'][0])

def test_independent_and_legacy_crops_do_not_claim_a_card_selection(notes):
    f=notes;image=import_textbook_crop(f.facade,request(f),[])['resources'][0]
    assert 'selected_from_knowledge' not in image['original'] and '选图来源知识卡' not in source_summary(image)
    assert validate_resource(image)==image
