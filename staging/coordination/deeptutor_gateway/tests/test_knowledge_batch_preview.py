"""A complete, ordered preview is rechecked before any lesson handoff."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from integrations.deeptutor_shchem_v1 import desktop_knowledge_batch_preview as preview
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block
from test_textbook_concept_cards import concept, shelf_fixture
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture


def card(title='合成知识',text='先比较条件，再给出解释',role='knowledge',**evidence):
    return resource('knowledge_note',title,[block('text',role,text)],
        {'source_path':'合成原书.pdf','source_sha256':'a'*64,'source_revision':'b'*64,
         'record_id':'SYNTHETIC-'+title,'pdf_pages':[8],'printed_pages':'unknown',**evidence})


def test_full_content_and_order_bound_without_binding_allocated_work_ids():
    first={'resources':[card('甲'),card('乙','完整内容\n第二行')]};before=deepcopy(first)
    other={'resources':[card('甲'),card('乙','完整内容\n第二行')]}
    assert first!=other and preview.batch_signature(first)==preview.batch_signature(other)
    assert preview.batch_signature(first)!=preview.batch_signature({'resources':list(reversed(other['resources']))})
    assert '完整内容\n第二行' in preview.preview_text(first) and first==before


@pytest.mark.parametrize('change',['body','role','source','pages','title'])
def test_recheck_rejects_semantic_changes_even_when_fresh_copy_ids_are_expected(monkeypatch,change):
    result={'resources':[card()]};expected=preview.batch_signature(result)
    fields={}
    if change=='body':fields['text']='内容改变'
    elif change=='role':fields['role']='teacher_note'
    elif change=='source':fields['source_sha256']='c'*64
    elif change=='pages':fields['pdf_pages']=[9]
    else:fields['title']='题名改变'
    monkeypatch.setattr(preview,'import_knowledge_cards',lambda *a:{'resources':[card(**fields)]})
    with pytest.raises(ValueError,match='预览后变化'):preview.recheck_batch(SimpleNamespace(),[{}],expected)


def test_real_source_revision_change_fails_before_handoff_without_writing_profile(concept):
    f=concept;selection=f.option['selection'];prepared=preview.preview_batch(f.facade,[selection])
    f.row['statement']+='源记录改动';import json
    f.catalog.write_text(json.dumps(f.row),encoding='utf-8')
    with pytest.raises(ValueError):preview.recheck_batch(f.facade,[selection],prepared['signature'])
    assert not f.facade.paths.task_root.exists()


def test_teaching_preview_hides_teacher_notes_and_defensive_boilerplate_without_mutating_source():
    item=card(text='组成和结构相似时比较。待教师核对。');item['title']='合成课件知识'
    result={'resources':[item,card('教师备注','仅教师查看内容','teacher_note')]};before=deepcopy(result)
    text=preview.preview_text(result)
    assert '组成和结构相似时比较' in text and '待教师核对' not in text and '仅教师查看内容' not in text
    assert result==before
