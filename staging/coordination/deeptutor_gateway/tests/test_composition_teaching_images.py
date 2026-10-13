"""Local image handoff, provenance, repeated uses and immutable answer roles."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
import pytest
from PIL import Image
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore, image_info
from integrations.deeptutor_shchem_v1.desktop_composition_images import image_options, import_image_result, local_image_request
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import resource, block, validate_resource, digest, new_composition, set_click_reveal
from integrations.deeptutor_shchem_v1.desktop_composition_render import arrange_composition, layout_composition

def picture(color='blue'):
    out=BytesIO(); Image.new('RGB',(800,450),color).save(out,format='PNG');data=out.getvalue();sha=sha256(data).hexdigest()
    return data,{'asset_id':'IMG-'+sha,'sha256':sha,'caption':'合成排版图片','source':'合成测试图片',
                 'purpose':'观察合成图的两个区域',**image_info(data)}

def selection(asset,roles=(),**changes):
    return {'asset':deepcopy(asset),'source_roles':list(roles),'title':'课堂观察','purpose':'比较两个区域', 'teacher_only':False,**changes}

def source_question(asset,role):
    return resource('complete_question','合成完整题',[block('text','question','合成题干',1),block('image',role,asset['asset_id'],2)],
        {'source_path':'合成原题.docx','source_sha256':'a'*64,'source_revision':'b'*64,'question_key':'SYNTHETIC',
         'scope_review_sha256':'c'*64,'scope_status':'complete_native_scope',
         'hierarchy':dict.fromkeys(('paper_id','theme_big_question_id','printed_question_id','atomic_part_id'),'unknown')})

def test_exact_pixels_and_source_snapshot_follow_repeated_classroom_uses():
    data,asset=picture();facade=SimpleNamespace(preparation_image_bytes=lambda _:data)
    assets=[asset];resources=[];before=deepcopy(assets)
    one=import_image_result(facade,assets,resources,selection(asset));row=one['resources'][0]
    assert assets==before and one['image_assets']==before
    assert row['original']['evidence']['image_asset']==asset
    assert row['original']['evidence']['source_revision']==digest(asset)
    assert row['locked'] and row['candidate_only'] and not row['human_reviewed'] and not row['publication_allowed']
    again=import_image_result(facade,assets,[row],selection(asset,['material']))
    assert again['resources']==[] and again['duplicate']
    other=import_image_result(facade,assets,[row],selection(asset,['material'],purpose='用图说明下一阶段'))
    assert len(other['resources'])==1 and other['image_assets']==before

@pytest.mark.parametrize('role',['answer','analysis','rubric','teacher_note'])
def test_original_teacher_image_cannot_be_unlocked_by_picker_or_modified_snapshot(role):
    data,asset=picture();original=source_question(asset,role);before=deepcopy(original)
    row=import_image_result(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[original],selection(asset,[role]))['resources'][0]
    assert all(b['role']=='teacher_note' for b in row['blocks']) and original==before
    bad=deepcopy(row)
    for b in bad['original']['blocks']:b['role']='material'
    for b in bad['blocks']:b['role']='material'
    bad['original_sha256']=digest(bad['original'])
    with pytest.raises(ValueError,match='教师答案'):validate_resource(bad)

def test_missing_question_scope_stays_unknown_and_requires_complete_question_entry():
    data,asset=picture();asset['purpose']='完整题原图；按每次题面或教师答案引用的角色排版。'
    assert image_options([asset],[])[0]['via_question']
    with pytest.raises(ValueError,match='完整题'):import_image_result(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[],selection(asset))
    known=source_question(asset,'question')
    row=import_image_result(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[known],selection(asset,['question']))['resources'][0]
    assert row['blocks'][0]['role']=='material'

def test_changed_pixels_or_changed_metadata_cannot_be_added(tmp_path):
    data,asset=picture();path=tmp_path/'image.png';path.write_bytes(data)
    local=local_image_request(path,'合成图','合成来源','观察区域');path.write_bytes(picture('red')[0]);calls=[]
    facade=SimpleNamespace(import_preparation_image=lambda *args:calls.append(args))
    with pytest.raises(ValueError,match='预览后变化'):
        import_image_result(facade,[],[],{'local':local,'title':'本次图题','purpose':'本次用途','teacher_only':False})
    assert calls==[]
    with pytest.raises(ValueError,match='记录已变化'):
        import_image_result(SimpleNamespace(),[{**asset,'source':'来源已修订'}],[],selection(asset))

def test_local_import_preserves_existing_asset_metadata_and_full_pixel_bytes(tmp_path):
    data,asset=picture();path=tmp_path/'source.png';path.write_bytes(data);store=PreparationImageStore(tmp_path/'assets')
    facade=SimpleNamespace(import_preparation_image=store.import_image,preparation_image_bytes=store.load)
    request=local_image_request(path,'新的课堂图题','新的来源说明','新的课堂用途')
    result=import_image_result(facade,[asset],[],{'local':request,'title':'新的课堂图题','purpose':'新的课堂用途','teacher_only':False})
    assert result['image_assets']==[asset] and result['resources'][0]['original']['evidence']['image_asset']==asset
    assert store.load(asset)==data and path.read_bytes()==data

def test_picture_and_short_purpose_share_page_and_click_policy_keeps_static_handout_hidden(qt_app):
    data,asset=picture();row=import_image_result(SimpleNamespace(preparation_image_bytes=lambda _:data),[asset],[],selection(asset))['resources'][0]
    value=new_composition('16:9');value['resources']=[row]
    value['placements'][row['id']]={'page_id':value['pages'][0],'x':12,'y':12,'width':296,'height':156}
    value=arrange_composition(value,[asset],{asset['asset_id']:data})
    plain=layout_composition(value,[asset],{asset['asset_id']:data},student=True)
    assert not plain['errors'] and len(plain['pages'])==1
    assert len({f['frame']['page_id'] for f in plain['fragments']})==1
    clicked=set_click_reveal(value,row['id'],True)
    static=layout_composition(clicked,[asset],{asset['asset_id']:data},student=True)
    classroom=layout_composition(clicked,[asset],{asset['asset_id']:data},student=True,include_reveals=True)
    assert not static['fragments']
    assert not classroom['errors']
    assert any(e['kind']=='image' for fragment in classroom['fragments'] for e in fragment['elements'])

@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
