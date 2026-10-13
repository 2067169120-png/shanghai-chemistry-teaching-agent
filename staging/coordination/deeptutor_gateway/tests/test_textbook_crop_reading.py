"""Real fixed/personal PDF receipts select an original page without writing."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from test_textbook_preparation_crops import crop_fixture as crop_fixture, request
from test_textbook_bookshelf import shelf_fixture as shelf_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_section_reader import section_fixture as section_fixture
from test_personal_textbook_study import book_fixture as book_fixture
from test_personal_textbook_ui import facade_for
from test_personal_textbook_crops import request as personal_request
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_textbook_crop
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import digest
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_crops import book_page_selection
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService, _json
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, TaskCancellationEvent, read_cancel_scope


def files(*roots):
    return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size,
                        path.stat().st_mtime_ns)
            for root in roots for path in Path(root).rglob('*') if path.is_file()}


def fixed(f):
    result = import_textbook_crop(f.facade, request(f), [])
    return f.facade, result['resources'][0], result['image_assets']


def personal(f):
    facade = facade_for(f)
    choice, _page, _preview = personal_request(f, facade)
    result = import_textbook_crop(facade, choice, [])
    return facade, result['resources'][0], result['image_assets']


def test_fixed_original_page_uses_sealed_receipt_and_preserves_every_file(crop_fixture):
    f = crop_fixture; facade, item, assets = fixed(f)
    before = files(f.root, f.task_root); originals = deepcopy((item, assets))
    result = facade.preparation_textbook_crop_original(item, assets)
    assert result['selection'] == f.selection and result['reading_mode'] == 'asset'
    assert result['source']['pdf_pages'] == [9] and result['source']['printed_page'] == 5
    assert result['source']['pdf_bytes'] == f.book.read_bytes() and result['crop_pixels_verified']
    assert result['binding']['source']['volume_id'] == f.row['volume_id']
    assert (item, assets) == originals and files(f.root, f.task_root) == before
    assert facade.preparation_textbook_crop_original(item, assets,
        expected_binding=result['binding_sha256']) == result
    with pytest.raises(ValueError,match='来源在阅读期间变化'):
        facade.preparation_textbook_crop_original(item,assets,expected_binding='f'*64)
    assert files(f.root,f.task_root)==before


def test_personal_original_page_reads_archived_book_and_keeps_study_state(book_fixture):
    f = book_fixture; facade, item, assets = personal(f); before = files(f.root, f.state_root)
    result = facade.preparation_textbook_crop_original(item, assets)
    assert result['source']['pdf_bytes'] == f.original and result['source']['pdf_pages'] == [2]
    assert result['binding']['source']['source_id'] == f.selection['source_id']
    assert result['source']['printed_page'] is None and files(f.root, f.state_root) == before
    assert f.study.book_study(f.selection)['sections'] == []


def test_same_crop_reused_for_another_teaching_question_keeps_original_binding(crop_fixture):
    f=crop_fixture;facade,item,assets=fixed(f)
    item['original']['title']='同一张图可以支持怎样的新问题？'
    next(row for row in item['original']['blocks'] if row['kind']=='text')['value']='另一完整教学用途'
    item['original_sha256']=digest(item['original'])
    item['title']='教师另改了课件标题'
    next(row for row in item['blocks'] if row['kind']=='text')['value']='教师另改了讲解文字'
    before=files(f.root,f.task_root)
    result=facade.preparation_textbook_crop_original(item,assets)
    assert result['source']['pdf_pages']==[9] and result['crop_pixels_verified']
    assert files(f.root,f.task_root)==before


@pytest.mark.parametrize('change', ['missing_source', 'source_bytes', 'source_revision',
                                  'missing_receipt', 'receipt', 'image', 'missing_asset', 'crop_page'])
def test_missing_or_changed_binding_fails_read_only(crop_fixture, change):
    f = crop_fixture; facade, item, assets = fixed(f)
    receipt = next((f.task_root / 'preparation-v1/textbook-crops').glob('*.json'))
    if change == 'missing_source': f.book.unlink()
    elif change == 'source_bytes': f.book.write_bytes(f.book.read_bytes() + b'\n% different\n')
    elif change == 'source_revision':
        f.asset['description'] = 'different indexed source'; f.write_assets([f.asset])
    elif change == 'missing_receipt': receipt.unlink()
    elif change == 'receipt':
        record = json.loads(receipt.read_text(encoding='utf-8')); record['image_sha256'] = 'a'*64
        receipt.write_text(json.dumps(record), encoding='utf-8')
    elif change == 'image':
        (f.task_root / 'preparation-v1/images' / (assets[0]['sha256'] + '.image')).write_bytes(b'changed')
    elif change == 'missing_asset': assets = []
    else:
        item['original']['evidence']['pdf_pages'] = [10]
        item['original_sha256'] = digest(item['original'])
    before = files(f.root, f.task_root); original = deepcopy((item, assets))
    with pytest.raises(ValueError): facade.preparation_textbook_crop_original(item, assets)
    assert files(f.root, f.task_root) == before and (item, assets) == original


def test_same_image_bytes_from_two_books_keep_distinct_original_identity(book_fixture, tmp_path):
    f = book_fixture; facade, first, assets = personal(f)
    other = tmp_path / 'other.pdf'; other.write_bytes(f.original + b'\n% distinct original identity\n')
    f.workspace.commit_books(f.workspace.preview_books([other]))
    book = next(row for row in f.study.books() if row['source_id'] != f.selection['source_id'])
    selection = book_page_selection(book, 2); page = facade.preparation_textbook_crop_page(selection)
    value = facade.preparation_textbook_crop_preview(selection, page['page_revision'],
        {'left':100,'top':400,'right':700,'bottom':1000}, '另一原书的相同像素', '比较可见色块和边界')
    choice = {'selection':selection, 'page_revision':page['page_revision'],
        'bounds':{'left':100,'top':400,'right':700,'bottom':1000}, 'caption':value['image_asset']['caption'],
        'purpose':value['image_asset']['purpose'], 'expected_preview_revision':value['preview_revision'],
        'expected_image_sha256':value['image_asset']['sha256'], 'selection_confirmed':True}
    second = import_textbook_crop(facade, choice, [])['resources'][0]
    assert value['image_asset']['sha256'] == assets[0]['sha256']
    before = files(f.root, f.state_root)
    one = facade.preparation_textbook_crop_original(first, assets)
    two = facade.preparation_textbook_crop_original(second, assets)
    assert one['selection'] != two['selection'] and one['binding_sha256'] != two['binding_sha256']
    assert one['source']['source_sha256'] == f.selection['revision']
    assert two['source']['source_sha256'] == book['revision'] and two['source']['pdf_pages'] == [2]
    assert files(f.root, f.state_root) == before


def test_pixel_comparison_accepts_equivalent_png_serialization(crop_fixture):
    f = crop_fixture; facade, item, assets = fixed(f)
    path = next((f.task_root/'preparation-v1/textbook-crops').glob('*.json'))
    record = json.loads(path.read_text(encoding='utf-8'))
    original = f.task_root/'preparation-v1/images'/(assets[0]['sha256']+'.image')
    with Image.open(io.BytesIO(original.read_bytes())) as picture:
        output = io.BytesIO(); picture.save(output, format='PNG', compress_level=0)
    raw = output.getvalue(); sha = hashlib.sha256(raw).hexdigest()
    assert sha != assets[0]['sha256']
    assets[0]['sha256'] = sha; assets[0]['asset_id'] = 'IMG-'+sha
    for rows in (item['original']['blocks'], item['blocks']):
        next(row for row in rows if row['kind']=='image')['value'] = 'IMG-'+sha
    item['original_sha256'] = digest(item['original'])
    record['image_sha256'] = sha; record['image_asset'] = deepcopy(assets[0])
    provenance = {key:value for key,value in record.items() if key not in {'crop_id','image_asset','created_at_utc'}}
    record['crop_id'] = hashlib.sha256(_json({'provenance':provenance,
        'caption':assets[0]['caption'],'purpose':assets[0]['purpose']})).hexdigest()
    # A deliberately resealed image has a new exact crop identity as well.
    item['original']['evidence']['crop_id'] = record['crop_id']
    item['original_sha256'] = digest(item['original'])
    new_path = path.with_name(record['crop_id']+'.json'); path.unlink()
    new_path.write_text(json.dumps(record), encoding='utf-8')
    (original.parent/(sha+'.image')).write_bytes(raw)
    before = files(f.root, f.task_root)
    assert facade.preparation_textbook_crop_original(item, assets)['crop_pixels_verified']
    assert files(f.root, f.task_root) == before


def test_original_changed_after_first_read_and_cancellation_cannot_release_reader(crop_fixture, monkeypatch):
    f = crop_fixture; facade, item, assets = fixed(f)
    original = TextbookPreparationCropService._source; count = [0]
    def changed(service, choice):
        result = original(service, choice); count[0] += 1
        if count[0] == 1: f.book.write_bytes(f.book.read_bytes() + b'\n% changed during read\n')
        return result
    monkeypatch.setattr(TextbookPreparationCropService, '_source', changed)
    with pytest.raises(ValueError): facade.preparation_textbook_crop_original(item, assets)
    before = files(f.root, f.task_root); event = TaskCancellationEvent(); event.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(event):
        facade.preparation_textbook_crop_original(item, assets)
    assert files(f.root, f.task_root) == before
