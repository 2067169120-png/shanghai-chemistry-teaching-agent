"""Archived-book crop pixels, source bindings, refusal and reuse."""
from copy import deepcopy
import hashlib
import io
import json

import pytest
from PIL import Image

from test_personal_textbook_study import book_fixture as book_fixture
from test_personal_textbook_ui import facade_for
from test_textbook_bookshelf import disk_bytes
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_crops import book_page_selection
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropError


def selection(f, page=2):
    return book_page_selection(f.study.books()[0], page)


def request(f, facade=None):
    facade = facade or facade_for(f)
    chosen = selection(f)
    page = facade.preparation_textbook_crop_page(chosen)
    bounds = {'left':100,'top':400,'right':700,'bottom':1000}
    value = facade.preparation_textbook_crop_preview(chosen, page['page_revision'], bounds,
                                                   '个人合成图表', '比较可见色块和边界')
    return {'selection': chosen, 'page_revision': page['page_revision'], 'bounds': bounds,
            'caption': value['image_asset']['caption'], 'purpose': value['image_asset']['purpose'],
            'expected_preview_revision': value['preview_revision'],
            'expected_image_sha256': value['image_asset']['sha256'], 'selection_confirmed': True}, page, value


def test_archived_book_page_and_crop_are_read_only_then_exact_source_and_pixels_are_saved(book_fixture):
    f=book_fixture; facade=facade_for(f); before=disk_bytes(f.state_root)
    choice,page,value=request(f,facade)
    assert disk_bytes(f.state_root)==before and f.source.read_bytes()==f.original
    assert page['source_kind']=='personal_textbook_page' and page['printed_page'] is None
    assert page['source_id']==f.selection['source_id'] and page['volume_id'] is None
    with Image.open(io.BytesIO(page['page_data'])) as original, Image.open(io.BytesIO(value['image_bytes'])) as cut:
        assert cut.tobytes()==original.crop((100,400,700,1000)).tobytes()
    asset=facade.import_textbook_preparation_crop(choice)
    assert facade.preparation_image_bytes(asset)==value['image_bytes']
    saved=disk_bytes(f.state_root)
    assert facade.import_textbook_preparation_crop(choice)==asset and disk_bytes(f.state_root)==saved
    receipts=list((f.paths.task_root/'preparation-v1/textbook-crops').glob('*.json'))
    assert len(receipts)==1
    record=json.loads(receipts[0].read_text(encoding='utf-8'))
    assert record['source_kind']=='personal_textbook_page'
    assert record['source_id']==f.selection['source_id'] and record['source_sha256']==hashlib.sha256(f.original).hexdigest()
    assert record['pdf_page']==2 and record['printed_page'] is None and record['bounds']==choice['bounds']
    assert record['candidate_only'] is True
    assert all(record[k] is False for k in ('human_reviewed','retrieval_ready','generation_allowed','publication_allowed'))
    assert f.study.book_study(f.selection)['sections']==[] and f.source.read_bytes()==f.original


@pytest.mark.parametrize('page',[True,0,-1,5,2.0,None])
def test_book_page_selector_rejects_invalid_absolute_page(book_fixture,page):
    with pytest.raises(TextbookPreparationCropError):selection(book_fixture,page)


@pytest.mark.parametrize('change',['revision','missing-book','outside-book','extra-path','bad-page-id'])
def test_personal_crop_cannot_resolve_wrong_identity_or_supplied_path(book_fixture,change):
    f=book_fixture; before=disk_bytes(f.state_root); chosen=selection(f)
    if change=='revision':chosen['revision']='f'*64
    elif change=='missing-book':chosen={'visual_asset_id':'BOOKPAGE-BOOK-'+'f'*64+'-2','revision':'f'*64}
    elif change=='outside-book':chosen['visual_asset_id']=chosen['visual_asset_id'][:-1]+'5'
    elif change=='extra-path':chosen['path']=str(f.source)
    else:chosen['visual_asset_id']=chosen['visual_asset_id'][:-1]+'02'
    with pytest.raises(DesktopFacadeError):facade_for(f).preparation_textbook_crop_page(chosen)
    assert disk_bytes(f.state_root)==before


@pytest.mark.parametrize('change',['archive','caption','page','confirmation'])
def test_personal_crop_rechecks_original_and_preview_before_any_write(book_fixture,change):
    f=book_fixture; facade=facade_for(f); choice,_,_=request(f,facade)
    if change=='archive':
        path=f.workspace.root/(f.selection['revision']+'.pdf'); path.write_bytes(f.original+b'\n% changed\n')
    elif change=='caption':choice['caption']='变更图题'
    elif change=='page':choice['selection']=selection(f,3)
    else:choice['selection_confirmed']=False
    before=disk_bytes(f.state_root)
    with pytest.raises(DesktopFacadeError):facade.import_textbook_preparation_crop(choice)
    assert disk_bytes(f.state_root)==before


def test_book_without_chapters_can_crop_last_page_and_forged_book_metadata_is_not_used(book_fixture):
    f=book_fixture; facade=facade_for(f); chosen=selection(f,4)
    page=facade.preparation_textbook_crop_page(chosen)
    assert page['pdf_page']==4 and f.study.book_study(f.selection)['sections']==[]
    forged=deepcopy(f.study.books()[0]);forged['source_id']='BOOK-'+'a'*64
    with pytest.raises(TextbookPreparationCropError):book_page_selection(forged,2)
