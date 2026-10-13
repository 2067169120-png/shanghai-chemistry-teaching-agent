"""Composition identity, source retention, audience, recovery and real backup."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_lesson_design import sample
from test_desktop_preparation_drafts import _record
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import *
from integrations.deeptutor_shchem_v1.desktop_lesson_design import validate_design, content_fingerprint, candidate_from_design
from integrations.deeptutor_shchem_v1.desktop_lesson_output import export_design, write_documents
from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore, validate_editor_payload
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftService
from integrations.deeptutor_shchem_v1.desktop_backup import plan_backup, create_backup, inspect_backup, restore_backup, manifest_revision
from integrations.deeptutor_shchem_v1.desktop_preparation_images import MAX_IMAGES, normalize_image_assets


def question():
    return resource('complete_question', '完整合成题', [block('text', 'shared_material', '公共条件：温度固定。', 1),
        block('text', 'question', '题面条件；' * 200 + '完整尾部条件', 2),
        block('image', 'question', 'IMG-' + 'b' * 64, 2), block('text', 'answer', '仅教师答案标记', 3)],
        {'source_path': '原题.docx', 'source_sha256': 'a'*64, 'source_revision': 'c'*64,
         'question_key': 'synthetic-question', 'scope_review_sha256': 'd'*64, 'scope_status': 'complete_native_scope',
         'hierarchy': dict.fromkeys(('paper_id','theme_big_question_id','printed_question_id','atomic_part_id'), 'unknown')})


def composition():
    value = new_composition()
    item = question()
    return add_resource(value, item, {'page_id':value['pages'][0], 'x':14,'y':14,'width':180,'height':120})


def payload():
    p = sample();p['lesson_design'] = enable_composition(p['lesson_design'])
    p['lesson_design']['composition'] = composition()
    return p


def test_positions_order_and_source_are_independent():
    value=composition();first=value['resources'][0]
    second=resource('personal_text','个人文字',[block('text','personal','教师手写文字')],
        {'source_path':'教师输入','source_sha256':'unknown','source_revision':'unknown'})
    value=add_resource(value,second,{'page_id':value['pages'][0],'x':14,'y':140,'width':180,'height':50})
    moved=place_resource(value,first['id'],{**value['placements'][first['id']],'x':20,'width':174})
    assert moved['resources']==value['resources']
    reordered=reorder_resources(moved,[second['id'],first['id']])
    assert reordered['placements']==moved['placements']
    assert reordered['resources'][1]['original']==first['original']
    assert '完整尾部条件' in visible_blocks(first)[1]['value']


def test_teacher_scope_and_source_snapshot_survive_explicit_edit():
    value=composition();item=value['resources'][0];answer=item['blocks'][-1]
    with pytest.raises(ValueError,match='锁定'):
        edit_resource(value,item['id'],values={answer['id']:'修改后的教师答案'})
    unlocked=edit_resource(value,item['id'],locked=False)
    changed=edit_resource(unlocked,item['id'],values={answer['id']:'修改后的教师答案'})
    assert changed['resources'][0]['original']==item['original']
    assert changed['resources'][0]['original_sha256']==item['original_sha256']
    assert all('教师答案' not in row['value'] for row in visible_blocks(changed['resources'][0]))
    assert visible_blocks(changed['resources'][0],student=False)[-1]['value']=='修改后的教师答案'
    with pytest.raises(ValueError,match='版本'):
        place_resource(changed,item['id'],changed['placements'][item['id']],expected=digest(value))


@pytest.mark.parametrize('corruption',['snapshot','order','role','flag','scope','hierarchy'])
def test_corruption_cannot_unlock_or_silently_discard_source(corruption):
    value=composition();item=value['resources'][0]
    if corruption=='snapshot':item['original']['blocks'][0]['value']='替换原文'
    elif corruption=='order':item['blocks'].reverse()
    elif corruption=='role':item['blocks'][-1]['role']='question'
    elif corruption=='flag':item['human_reviewed']=True
    else:
        if corruption=='scope':item['original']['evidence']['scope_status']='partial'
        else:del item['original']['evidence']['hierarchy']['theme_big_question_id']
        item['original_sha256']=digest(item['original'])
    with pytest.raises(ValueError):validate_composition(value)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,-1,999])
def test_invalid_placement_is_rejected_without_clipping_original(bad):
    value=composition();item=value['resources'][0]
    with pytest.raises(ValueError):place_resource(value,item['id'],{**value['placements'][item['id']],'x':bad})
    assert '完整尾部条件' in item['original']['blocks'][1]['value']


def test_recovery_draft_and_real_backup_retain_the_same_composition(tmp_path):
    p=payload();state=DesktopStateStore(tmp_path/'original')
    PreparationRecoveryStore(state.root).save(p,dirty=True,sequence=1)
    record=_record(p);record['lesson_design']=deepcopy(p['lesson_design']);state.save_draft('composition',record)
    assert PreparationRecoveryStore(state.root).load()['payload']==p
    assert PreparationDraftService._payload(state.snapshot()['drafts']['composition'])['lesson_design']==p['lesson_design']
    plan=plan_backup(state.root);archive=tmp_path/'backup.zip';create_backup(plan,archive)
    report=inspect_backup(archive);restored=tmp_path/'restored'
    restore_backup(archive,restored,expected_manifest=manifest_revision(report))
    assert PreparationRecoveryStore(restored).load()['payload']==p
    assert PreparationDraftService._payload(DesktopStateStore(restored).snapshot()['drafts']['composition'])['lesson_design']==p['lesson_design']


def test_legacy_reading_and_fingerprint_unchanged_until_explicit_conversion():
    p=sample();before=deepcopy(p);fingerprint=content_fingerprint(p)
    assert validate_design(p['lesson_design'])==before['lesson_design']
    assert content_fingerprint(p)==fingerprint and p==before
    upgraded=enable_composition(p['lesson_design'])
    assert upgraded['nodes']==p['lesson_design']['nodes'] and upgraded['exports']==p['lesson_design']['exports']
    assert p==before and upgraded['schema_version']==DESIGN_SCHEMA


def test_legacy_outputs_cannot_omit_composition_resources(tmp_path):
    p=payload();facade=SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path))
    with pytest.raises(ValueError,match='编排'):candidate_from_design(p)
    for action in (lambda:write_documents(p,tmp_path,{}),lambda:export_design(facade,p)):
        with pytest.raises(ValueError,match='图片缺失'):action()
    assert not list(tmp_path.iterdir())


def test_recovery_keeps_all_supported_image_references(tmp_path):
    p=sample();p['image_assets']=[{'asset_id':'IMG-'+f'{i:064x}','sha256':f'{i:064x}',
        'caption':'图题','source':'合成测试','purpose':'恢复容量核对','content_type':'image/png','width':40,'height':30}
        for i in range(MAX_IMAGES)]
    before = deepcopy(p)
    assert len({a['asset_id'] for a in p['image_assets']}) == MAX_IMAGES
    assert len({a['sha256'] for a in p['image_assets']}) == MAX_IMAGES
    assert normalize_image_assets(p['image_assets']) == p['image_assets']
    assert validate_editor_payload(p)['image_assets']==p['image_assets']
    store = PreparationRecoveryStore(tmp_path / 'capacity-recovery')
    store.save(p, dirty=True, sequence=1)
    assert store.load()['payload'] == before and p == before
    saved_files = {path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    oversized = deepcopy(p)
    oversized['image_assets'].append({**p['image_assets'][-1],
        'asset_id':'IMG-'+f'{MAX_IMAGES:064x}', 'sha256':f'{MAX_IMAGES:064x}'})
    frozen_oversized = deepcopy(oversized)
    assert len({a['asset_id'] for a in oversized['image_assets']}) == MAX_IMAGES + 1
    assert all(len(a['asset_id']) == 68 and len(a['sha256']) == 64 for a in oversized['image_assets'])
    with pytest.raises(ValueError, match='图片列表不受支持'):
        validate_editor_payload(oversized)
    with pytest.raises(ValueError, match='图片列表不受支持'):
        store.save(oversized, dirty=True, sequence=2)
    assert oversized == frozen_oversized and p == before
    assert {path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == saved_files
    assert store.load()['payload'] == before
