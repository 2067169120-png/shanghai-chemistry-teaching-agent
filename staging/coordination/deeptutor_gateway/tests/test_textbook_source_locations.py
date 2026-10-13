"""Registered originals relocate through independent records, never rewritten Originals."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image

from test_textbook_preparation_crops import crop_fixture, request
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_section_reader import section_fixture
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_textbook_crop
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest
from integrations.deeptutor_shchem_v1 import desktop_textbook_crop_reading as reading
from integrations.deeptutor_shchem_v1 import desktop_textbook_source_locations as locations
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import _json
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, TaskCancellationEvent, read_cancel_scope


@pytest.fixture(autouse=True)
def forbid_business_constructors(monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore
    def forbidden(*args,**kwargs):
        pytest.fail('Source relocation QA must not construct business state')
    for cls in (DesktopWorkbenchFacade,DesktopStateStore,PreparationRecoveryStore):
        monkeypatch.setattr(cls,'__init__',forbidden)


def files(*roots):
    return {str(p):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_size,p.stat().st_mtime_ns)
            for root in roots for p in Path(root).rglob('*') if p.is_file()}


@pytest.fixture
def moved(crop_fixture):
    f = crop_fixture
    result = import_textbook_crop(f.facade,request(f),[])
    f.item,f.assets = result['resources'][0],result['image_assets']
    new_root = f.root.parent/(f.root.name+'-moved-library')
    new_tasks = f.task_root.parent/(f.task_root.name+'-moved-profile')
    shutil.copytree(f.root,new_root)
    shutil.copytree(f.task_root,new_tasks)
    f.moved_facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=new_root,task_root=new_tasks))
    f.moved_root,f.moved_tasks = new_root,new_tasks
    f.moved_book = new_root/f.book.relative_to(f.root)
    f.receipt = next((new_tasks/'preparation-v1/textbook-crops').glob('*.json'))
    return f


def preview(f):
    return locations.preview_textbook_source_relocation(f.moved_facade,f.item,f.assets)


def publish(f):
    value = preview(f)
    publication = locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,
        expected_preview=value['preview_id'])
    return value,publication.publish()


def replace_receipt(f,record):
    asset = record['image_asset']
    provenance = {key:row for key,row in record.items() if key not in {'crop_id','image_asset','created_at_utc'}}
    record['crop_id'] = hashlib.sha256(_json({'provenance':provenance,'caption':asset['caption'],
                                           'purpose':asset['purpose']})).hexdigest()
    new_path = f.receipt.with_name(record['crop_id']+'.json')
    f.receipt.unlink()
    new_path.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    f.receipt = new_path


def test_moved_library_requires_explicit_link_and_preview_staging_are_read_only(moved):
    f=moved
    before=files(f.root,f.task_root,f.moved_root,f.moved_tasks)
    originals=deepcopy((f.item,f.assets))
    with pytest.raises(locations.TextbookSourceRelocationRequired):
        reading.textbook_crop_reader(f.moved_facade,f.item,f.assets)
    value=preview(f)
    staged=locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,expected_preview=value['preview_id'])
    assert not staged.published and value['old_path']==str(f.book.absolute())
    assert value['current_path']==str(f.moved_book.absolute())
    assert files(f.root,f.task_root,f.moved_root,f.moved_tasks)==before and (f.item,f.assets)==originals


def test_explicit_link_reopens_original_without_changing_receipt_or_original(moved):
    f=moved
    before=files(f.root,f.task_root,f.moved_root,f.moved_tasks)
    originals=deepcopy((f.item,f.assets))
    value,result=publish(f)
    assert result['linked'] and result['original_unchanged']
    opened=reading.textbook_crop_reader(f.moved_facade,f.item,f.assets)
    assert opened['crop_pixels_verified'] and opened['source']['pdf_pages']==[9]
    assert opened['source']['pdf_bytes']==f.book.read_bytes()
    assert opened['binding']['source_location']['current_path']==value['current_path']
    after=files(f.root,f.task_root,f.moved_root,f.moved_tasks)
    assert {k:after[k] for k in before}==before
    added=set(after)-set(before)
    assert len(added)==1 and '/textbook-source-locations/' in next(iter(added)).replace('\\','/')
    assert (f.item,f.assets)==originals
    assert reading.textbook_crop_reader(f.moved_facade,f.item,f.assets,
        expected_binding=opened['binding_sha256'])==opened


def test_same_book_link_can_read_another_original_teaching_use(moved):
    f=moved
    publish(f)
    other=deepcopy(f.item)
    other['original']['title']='同一教材裁片的另一讲解问题'
    other['original_sha256']=digest(other['original'])
    other['title']='教师编辑的另一用途'
    assert reading.textbook_crop_reader(f.moved_facade,other,f.assets)['crop_pixels_verified']
    assert len(list((f.moved_tasks/'preparation-v1/textbook-source-locations').glob('*.json')))==1


@pytest.mark.parametrize('change',['pdf','receipt','image','original','location'])
def test_changes_after_stage_cannot_publish(moved,change):
    f=moved
    value=preview(f)
    publication=locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,expected_preview=value['preview_id'])
    if change=='pdf': f.moved_book.write_bytes(f.moved_book.read_bytes()+b'\n% changed\n')
    elif change=='receipt': f.receipt.write_bytes(f.receipt.read_bytes()+b' ')
    elif change=='image':
        (f.moved_tasks/'preparation-v1/images'/(f.assets[0]['sha256']+'.image')).write_bytes(b'changed')
    elif change=='original':
        publication.item['original']['title']='changed'
        publication.item['original_sha256']=digest(publication.item['original'])
    else:
        publish(f)
    before=files(f.moved_root,f.moved_tasks)
    with pytest.raises(ValueError): publication.publish()
    assert files(f.moved_root,f.moved_tasks)==before and not publication.published


def test_preview_version_and_cancel_cannot_write_location(moved):
    f=moved
    before=files(f.moved_root,f.moved_tasks)
    with pytest.raises(ValueError,match='确认期间变化'):
        locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,expected_preview='a'*64)
    value=preview(f)
    publication=locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,expected_preview=value['preview_id'])
    event=TaskCancellationEvent();event.set()
    with pytest.raises(ReadCancelled),read_cancel_scope(event): publication.publish()
    assert files(f.moved_root,f.moved_tasks)==before


@pytest.mark.parametrize('change',['item','assets'])
def test_caller_inputs_changed_after_stage_cannot_publish(moved,change):
    f=moved
    value=preview(f)
    publication=locations.stage_textbook_source_relocation(f.moved_facade,f.item,f.assets,expected_preview=value['preview_id'])
    if change=='item': f.item['title']='调用者在确认期间修改了课堂标题'
    else: f.assets[0]['purpose']='调用者在确认期间修改了图片用途'
    before=files(f.moved_tasks)
    with pytest.raises(ValueError,match='课堂素材在确认期间变化'): publication.publish()
    assert files(f.moved_tasks)==before


def test_location_record_revision_is_part_of_reader_expected_binding(moved):
    f=moved
    publish(f)
    opened=reading.textbook_crop_reader(f.moved_facade,f.item,f.assets)
    path=next((f.moved_tasks/'preparation-v1/textbook-source-locations').glob('*.json'))
    value=json.loads(path.read_text(encoding='utf-8'))
    value['created_at_utc']='2026-10-09T00:00:00+00:00'
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError,match='来源在阅读期间变化'):
        reading.textbook_crop_reader(f.moved_facade,f.item,f.assets,expected_binding=opened['binding_sha256'])


@pytest.mark.parametrize('field,value',[('volume_id','TB-M1'),('source_kind','personal_textbook_page'),
                                      ('source_sha256','a'*64)])
def test_location_cannot_substitute_another_typed_source(moved,field,value):
    f=moved
    publish(f)
    path=next((f.moved_tasks/'preparation-v1/textbook-source-locations').glob('*.json'))
    data=json.loads(path.read_text(encoding='utf-8'))
    data['source_identity'][field]=value
    path.write_text(json.dumps(data),encoding='utf-8')
    before=files(f.moved_tasks)
    with pytest.raises(ValueError): reading.textbook_crop_reader(f.moved_facade,f.item,f.assets)
    assert files(f.moved_tasks)==before


@pytest.mark.parametrize('change',['duplicate_key','nan','whole_page_raster'])
def test_malicious_receipt_does_not_pass_even_with_self_consistent_crop_id(moved,change):
    f=moved
    data=json.loads(f.receipt.read_text(encoding='utf-8'))
    if change=='duplicate_key':
        raw=f.receipt.read_text(encoding='utf-8')
        f.receipt.write_text(raw.replace('{','{"schema_version":"duplicate",',1),encoding='utf-8')
    else:
        if change=='nan': data['recipe']['scale']=float('nan')
        else: data['recipe']['raster_sha256']='a'*64
        data['page_revision']=hashlib.sha256(_json({'origin':reading._origin(data),'recipe':data['recipe']})).hexdigest()
        replace_receipt(f,data)
    before=files(f.moved_tasks)
    with pytest.raises(ValueError): preview(f)
    assert files(f.moved_tasks)==before


def test_transparent_same_rgb_crop_cannot_substitute_visible_original(moved):
    f=moved
    original=f.moved_tasks/'preparation-v1/images'/(f.assets[0]['sha256']+'.image')
    with Image.open(io.BytesIO(original.read_bytes())) as picture:
        rgba=picture.convert('RGBA');rgba.putalpha(0)
        output=io.BytesIO();rgba.save(output,format='PNG')
    raw=output.getvalue();sha=hashlib.sha256(raw).hexdigest()
    f.assets[0].update(asset_id='IMG-'+sha,sha256=sha)
    (original.parent/(sha+'.image')).write_bytes(raw)
    for rows in (f.item['original']['blocks'],f.item['blocks']):
        next(row for row in rows if row['kind']=='image')['value']='IMG-'+sha
    f.item['original_sha256']=digest(f.item['original'])
    record=json.loads(f.receipt.read_text(encoding='utf-8'))
    record.update(image_sha256=sha,image_asset=deepcopy(f.assets[0]))
    replace_receipt(f,record)
    before=files(f.moved_tasks)
    with pytest.raises(ValueError,match='^教材裁片的原书、页码或裁选版本缺失、变化或身份重复，请重新关联原教材。$'):
        preview(f)
    assert files(f.moved_tasks)==before
