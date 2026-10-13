"""True SECTIONPAGE sources feed existing pixels, receipts and relocation gates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from test_section_material_sources import empty_registered, empty_supplement, facade
from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_supplements import supplement_fixture

from integrations.deeptutor_shchem_v1.desktop_section_material_sources import section_textbook_options
from integrations.deeptutor_shchem_v1.desktop_section_page_crops import SectionTextbookPageCatalog
from integrations.deeptutor_shchem_v1.desktop_composition_sources import import_textbook_crop
from integrations.deeptutor_shchem_v1.desktop_section_materials import section_material_context
from integrations.deeptutor_shchem_v1.desktop_textbook_crop_reading import textbook_crop_reader
from integrations.deeptutor_shchem_v1.desktop_textbook_source_locations import (
    TextbookSourceRelocationRequired, preview_textbook_source_relocation, stage_textbook_source_relocation)


def request(f, row=None):
    row = row or next(row for row in section_textbook_options(f.facade, f.context)['rows']
                     if row['origin'] == 'section_textbook_page')
    page = f.facade.preparation_textbook_crop_page(row['selection'])
    bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    preview = f.facade.preparation_textbook_crop_preview(row['selection'], page['page_revision'],
        bounds, row['caption'], '核对完整原页可见条件')
    return {'selection': row['selection'], 'page_revision': page['page_revision'], 'bounds': bounds,
        'caption': row['caption'], 'purpose': '核对完整原页可见条件',
        'expected_preview_revision': preview['preview_revision'],
        'expected_image_sha256': preview['image_asset']['sha256'], 'selection_confirmed': True}


@pytest.mark.parametrize('fixture', ['empty_registered', 'empty_supplement'])
def test_new_pages_have_sealed_section_receipt_and_original_reader(request, fixture):
    f = request.getfixturevalue(fixture)
    selected = globals()['request'](f)
    result = import_textbook_crop(f.facade, selected, [])
    item, assets = result['resources'][0], result['image_assets']
    assert 'selected_from_knowledge' not in item['original'] and item['original']['kind'] == 'textbook_image'
    original = textbook_crop_reader(f.facade, item, assets)
    assert original['crop_pixels_verified'] and original['binding']['source']['source_kind'] == 'section_textbook_page'
    assert original['binding']['source']['section_selection'] == f.context['section_selection']
    assert original['binding']['source']['section_pdf_pages'] == f.context['source_binding']['pdf_pages']
    scope = section_material_context(f.facade, item, [item], assets)
    assert scope['section_filter_key'] == f.context['section_filter_key'] and scope['knowledge_selection'] is None
    record = json.loads(next((f.facade.paths.task_root / 'preparation-v1/textbook-crops').glob('*.json')).read_text(encoding='utf8'))
    assert record['source_kind'] == 'section_textbook_page' and record['candidate_only'] is True
    assert record['human_reviewed'] is False and record['publication_allowed'] is False


@pytest.mark.parametrize('change', ['page', 'revision', 'scope', 'book'])
def test_typed_page_rejects_other_scope_or_stale_identity(empty_registered, change):
    f = empty_registered
    row = next(row for row in section_textbook_options(f.facade, f.context)['rows'] if row['origin'] == 'section_textbook_page')
    chosen = deepcopy(row['selection'])
    if change == 'page': chosen['visual_asset_id'] = chosen['visual_asset_id'].rsplit('-', 1)[0] + '-1'
    elif change == 'revision': chosen['revision'] = 'f' * 64
    elif change == 'scope': chosen['visual_asset_id'] = 'SECTIONPAGE-registered-' + 'unselected'.encode().hex() + '-9'
    else:
        f.registry['volumes'][0]['volume_title'] += 'changed'; f.activate()
    with pytest.raises(ValueError): SectionTextbookPageCatalog(f.root).source(**chosen)
    assert not f.facade.paths.task_root.exists()


def test_unknown_printed_pages_remain_unknown_and_noncontiguous_hole_rejects(empty_supplement):
    f = empty_supplement
    f.product['supplement_nodes'][0]['printed_pages'] = []; f.write_product()
    row = next(row for row in f.shelf.snapshot()['supplements'] if row['supplement_node_key'] == 'declared-project')
    from integrations.deeptutor_shchem_v1.desktop_section_materials import textbook_section_material_context
    f.context = textbook_section_material_context(f.facade, f.context['book_key'], row['supplement_node_key'],
        {'mode': 'supplement', 'supplement_node_key': row['supplement_node_key'], 'revision': row['revision']})
    rows = section_textbook_options(f.facade, f.context)['rows']
    assert [row['pdf_page'] for row in rows] == [43, 45] and all(row['printed_page'] is None for row in rows)
    hole = {**rows[0]['selection'], 'visual_asset_id': rows[0]['selection']['visual_asset_id'].rsplit('-', 1)[0] + '-44'}
    with pytest.raises(ValueError): SectionTextbookPageCatalog(f.root).source(**hole)


def test_new_section_crop_relocation_requires_explicit_position_confirmation(empty_registered):
    f = empty_registered; result = import_textbook_crop(f.facade, request(f), [])
    item, assets = result['resources'][0], result['image_assets']
    before = deepcopy(item['original'])
    moved_root, moved_tasks = f.root.parent / 'moved-library', f.facade.paths.task_root.parent / 'moved-tasks'
    shutil.copytree(f.root, moved_root); shutil.copytree(f.facade.paths.task_root, moved_tasks)
    moved = facade(moved_root, moved_tasks)
    with pytest.raises(TextbookSourceRelocationRequired): textbook_crop_reader(moved, item, assets)
    preview = preview_textbook_source_relocation(moved, item, assets)
    publication = stage_textbook_source_relocation(moved, item, assets, expected_preview=preview['preview_id'])
    publication.publish()
    read = textbook_crop_reader(moved, item, assets)
    assert read['crop_pixels_verified'] and read['binding']['source_location']['current_path'] == preview['current_path']
    assert item['original'] == before


@pytest.mark.parametrize('change', ['receipt', 'pixels', 'directory'])
def test_new_section_original_changes_still_reject(empty_registered, change):
    f = empty_registered; result = import_textbook_crop(f.facade, request(f), [])
    item, assets = result['resources'][0], result['image_assets']
    if change == 'receipt':
        path = next((f.facade.paths.task_root / 'preparation-v1/textbook-crops').glob('*.json'))
        row = json.loads(path.read_text(encoding='utf8')); row['section_pdf_pages'] = [99]
        path.write_text(json.dumps(row), encoding='utf8')
    elif change == 'pixels':
        path = f.facade.paths.task_root / 'preparation-v1/images' / (assets[0]['sha256'] + '.image')
        path.write_bytes(b'changed')
    else: f.registry['nodes'][0]['section_title'] += 'changed'; f.activate()
    with pytest.raises(ValueError): textbook_crop_reader(f.facade, item, assets)


def test_same_pdf_bytes_and_pixels_do_not_make_other_book_or_section_selection(empty_registered):
    f = empty_registered
    selection = next(row['selection'] for row in section_textbook_options(f.facade, f.context)['rows']
                     if row['origin'] == 'section_textbook_page')
    other_key = next(row['node_key'] for row in f.registry['nodes'] if row['volume_id'] != f.row['volume_id'])
    page = selection['visual_asset_id'].rsplit('-', 1)[1]
    forged = {**selection, 'visual_asset_id': 'SECTIONPAGE-registered-' + other_key.encode().hex() + '-' + page}
    with pytest.raises(ValueError): SectionTextbookPageCatalog(f.root).source(**forged)


@pytest.mark.parametrize('fixture', ['empty_registered', 'empty_supplement'])
def test_confirmed_same_pdf_new_path_reopens_current_section_without_rewriting_seals(request, fixture):
    f = request.getfixturevalue(fixture)
    # No asset dependency changes: this case is the independent full-page path.
    f.write_assets([])
    from integrations.deeptutor_shchem_v1.desktop_section_materials import textbook_section_material_context
    f.context = textbook_section_material_context(f.facade, f.context['book_key'], f.context['section_filter_key'], f.context['section_selection'])
    result = import_textbook_crop(f.facade, globals()['request'](f), [])
    item, assets = result['resources'][0], result['image_assets']
    original = deepcopy(item['original'])
    receipt_path = next((f.facade.paths.task_root / 'preparation-v1/textbook-crops').glob('*.json'))
    receipt = receipt_path.read_bytes()
    new_relative = 'books/moved-original.pdf'
    moved_pdf = f.root / new_relative
    moved_pdf.write_bytes(f.book.read_bytes()); f.book.unlink()
    for row in f.registry['volumes'] + f.registry['nodes']:
        if row['volume_id'] == f.row['volume_id']: row['source_path'] = new_relative
    f.activate()
    if fixture == 'empty_supplement':
        f.product['source_registry_sha256'] = hashlib.sha256(f.registry_path.read_bytes()).hexdigest()
        for node in f.product['supplement_nodes']:
            if node['volume_id'] == f.row['volume_id']: node['source_path'] = new_relative
        f.write_product()
    with pytest.raises(TextbookSourceRelocationRequired): textbook_crop_reader(f.facade, item, assets)
    preview = preview_textbook_source_relocation(f.facade, item, assets)
    stage_textbook_source_relocation(f.facade, item, assets, expected_preview=preview['preview_id']).publish()
    assert textbook_crop_reader(f.facade, item, assets)['crop_pixels_verified']
    context = section_material_context(f.facade, item, [item], assets)
    assert context['section_filter_key'] == f.context['section_filter_key']
    assert context['section_choices'][0]['selection']['revision'] == f.shelf._directory()[1]
    assert item['original'] == original and receipt_path.read_bytes() == receipt
