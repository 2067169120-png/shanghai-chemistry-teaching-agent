"""Complete source scopes and current work copies survive library handoff."""
from copy import deepcopy
import pytest
from test_word_question_images import _fixture, desktop_paths as desktop_paths
from test_composition_resource_titles import question
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_questions, merge_question_result
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block, visible_blocks
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore, MAX_IMAGES


def labelled_fixture(desktop_paths,tmp_path,**kwargs):
    facade,choices,provider=_fixture(desktop_paths,tmp_path,**kwargs)
    # Exercise the retained stored-attribute branch with ordinary automatic
    # suggestions for synthetic documents. No teacher review is granted.
    rows=[facade.word_question_attribute_options(c['key'],c['revision'])['attributes'] for c in choices]
    facade._word_questions().attribute_store.save_many(rows)
    choices=[{'key':r['key'],'revision':r['revision'],'points':3}
             for r in facade.word_question_catalog()['items']]
    return facade,choices,provider


def test_complete_reference_keeps_native_occurrences_and_teacher_answer(desktop_paths,tmp_path):
    facade,choices,provider=labelled_fixture(desktop_paths,tmp_path,count=2,answer_image=True)
    reference=facade.word_question_reference(choices)
    result=import_questions(facade,choices,[],expected_reference=reference)
    assert len(result['resources'])==2 and len(result['image_assets'])==2
    for item in result['resources']:
        images=[b for b in item['blocks'] if b['kind']=='image']
        assert len(images)==2 and images[0]['value']==images[1]['value']
        assert [b['role'] for b in images]==['question','answer']
        assert any('参考答案' in b['value'] for b in item['blocks'] if b['kind']=='text')
        assert all(b['role']!='answer' for b in visible_blocks(item))
        assert item['blocks']==item['original']['blocks'] and item['locked']
        assert set(item['original']['evidence']['hierarchy'].values())=={'unknown'}
        assert not item['human_reviewed'] and not item['publication_allowed']
    store=PreparationImageStore(desktop_paths.task_root/'preparation-v1/images')
    assert all(store.load(a) for a in result['image_assets'])
    assert provider.borrow_calls==0


def test_unsaved_teaching_labels_do_not_block_complete_native_source(desktop_paths,tmp_path):
    facade,choices,provider=_fixture(desktop_paths,tmp_path)
    store=facade._word_questions().attribute_store
    assert store.get(choices[0]['key']) is None
    reference=facade.word_question_reference(choices)
    result=import_questions(facade,choices,[],expected_reference=reference)
    assert len(result['resources'])==1 and store.get(choices[0]['key']) is None
    assert not result['resources'][0]['human_reviewed'] and provider.borrow_calls==0


def test_adjusted_answer_scope_reads_current_images_and_keeps_old_labels(desktop_paths,tmp_path):
    facade,choices,provider=labelled_fixture(desktop_paths,tmp_path,answer_image=True)
    store=facade._word_questions().attribute_store
    old=store.get(choices[0]['key'])
    assert old['material_status']['answer_image_count']==1
    row=facade.word_question_catalog()['items'][0]
    changed=facade.word_question_update_range(row['key'],row['revision'],
        block_start=row['block_start'],question_end=row['question_end'],
        answer_start=row['answer_start'],block_end=row['block_end']-1,
        context_start=row['context_start'],context_end=row['context_end'])
    current=[{'key':changed['key'],'revision':changed['revision'],'points':3}]
    assert current[0]['revision']!=choices[0]['revision']
    reference=facade.word_question_reference(current)
    result=import_questions(facade,current,[],expected_reference=reference)
    item=result['resources'][0]
    assert [b['role'] for b in item['blocks'] if b['kind']=='image']==['question']
    assert item['original']['evidence']['question_key']==changed['key']
    assert store.get(choices[0]['key'])==old
    assert not item['human_reviewed'] and provider.borrow_calls==0


@pytest.mark.parametrize('change',['text','images','selection'])
def test_changed_displayed_reference_cannot_copy_images(desktop_paths,tmp_path,change):
    facade,choices,_=labelled_fixture(desktop_paths,tmp_path)
    reference=facade.word_question_reference(choices)
    if change=='text':reference['materials']+='原预览已经改变'
    elif change=='images':reference['include_images']=False
    else:reference['selections'][0]['revision']='f'*64
    with pytest.raises(ValueError):
        import_questions(facade,choices,[],expected_reference=reference)
    assert not (desktop_paths.task_root/'preparation-v1/images').exists()


def test_merge_preserves_current_edited_copy_and_deduplicates_same_source():
    old=question();old['title']='教师自己的标题'
    new=question();new['original']['evidence']=deepcopy(old['original']['evidence'])
    # These two independent imports have distinct block and resource IDs.
    result={'resources':[new,new],'image_assets':[]};before=deepcopy((old,result))
    merged=merge_question_result(result,[old],[])
    assert merged=={'resources':[],'image_assets':[]}
    assert (old,result)==before


def test_new_source_revision_is_kept_alongside_existing_work_copy():
    old=question();new=question()
    new['original']['evidence']['source_revision']='d'*64
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    new['original_sha256']=digest(new['original'])
    merged=merge_question_result({'resources':[new],'image_assets':[]},[old],[])
    assert merged['resources']==[new] and old['original']['evidence']['source_revision']=='b'*64


def moved_copy(old):
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    new=question();new['original']['evidence']=deepcopy(old['original']['evidence'])
    new['original']['evidence']['source_path']='另一个候选库/完整原题.docx'
    new['original_sha256']=digest(new['original'])
    return new


def test_source_copy_after_restore_preserves_edited_work_without_duplicate_questions():
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import edit_resource,new_composition,add_resource
    value=new_composition();old=question()
    value=add_resource(value,old,{'page_id':value['pages'][0],'x':12,'y':12,'width':186,'height':273})
    value=edit_resource(value,old['id'],locked=False)
    value=edit_resource(value,old['id'],title='教师已经整理的标题',values={old['blocks'][0]['id']:'补充的课堂导语。'+old['blocks'][0]['value']})
    old=value['resources'][0];new=moved_copy(old);before=deepcopy((old,new))
    assert old['original']['evidence']['source_path']!=new['original']['evidence']['source_path']
    assert merge_question_result({'resources':[new,new],'image_assets':[]},[old],[])=={'resources':[],'image_assets':[]}
    assert (old,new)==before


@pytest.mark.parametrize('field',['source_sha256','source_revision','scope_review_sha256','question_key'])
def test_moved_source_with_a_new_verified_version_or_range_remains_separate(field):
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    old=question();new=moved_copy(old)
    new['original']['evidence'][field]='another-question' if field=='question_key' else 'e'*64
    new['original_sha256']=digest(new['original'])
    before=deepcopy((old,new));result=merge_question_result({'resources':[new],'image_assets':[]},[old],[])
    assert result['resources']==[new] and (old,new)==before


@pytest.mark.parametrize('field',['source_sha256','source_revision','scope_review_sha256'])
def test_unknown_evidence_is_rejected_before_merging_distinct_storage_paths(field):
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    old=question();old['original']['evidence'][field]='unknown';old['original_sha256']=digest(old['original'])
    new=moved_copy(old);before=deepcopy((old,new))
    with pytest.raises(ValueError):
        merge_question_result({'resources':[new],'image_assets':[]},[old],[])
    assert (old,new)==before


def test_two_copied_sources_in_one_batch_are_kept_once_by_verified_identity():
    old=question();new=moved_copy(old)
    result=merge_question_result({'resources':[old,new],'image_assets':[]},[],[])
    assert result['resources']==[old]


def test_missing_original_image_and_wrong_material_kind_are_rejected():
    item=question();image=block('image','question','IMG-'+'9'*64,94)
    item['blocks'].insert(2,image);item['original']['blocks']=deepcopy(item['blocks'])
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    item['original_sha256']=digest(item['original'])
    with pytest.raises(ValueError,match='原图记录缺失'):
        merge_question_result({'resources':[item],'image_assets':[]},[],[])
    personal=resource('personal_text','个人文字',[block('text','personal','文字')],
        {'source_path':'作者输入','source_sha256':'unknown','source_revision':'unknown'})
    with pytest.raises(ValueError,match='只接收'):
        merge_question_result({'resources':[personal],'image_assets':[]},[],[])


def test_image_capacity_failure_preserves_existing_records():
    from test_word_question_dialog import _preparation_asset
    old=[_preparation_asset(i+1) for i in range(MAX_IMAGES)]
    new_asset=_preparation_asset(MAX_IMAGES+1)
    item=question();image=block('image','question',new_asset['asset_id'],94)
    item['blocks'].insert(2,image);item['original']['blocks']=deepcopy(item['blocks'])
    from integrations.deeptutor_shchem_v1.desktop_lesson_design import digest
    item['original_sha256']=digest(item['original']);before=deepcopy(old)
    with pytest.raises(ValueError,match='最多'):
        merge_question_result({'resources':[item],'image_assets':[new_asset]},[],old)
    assert old==before
