"""Complete batch gates, real crop bytes and unchanged source/current metadata."""
import json
from copy import deepcopy

import pytest

from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_bookshelf import shelf_fixture as shelf_fixture, disk_bytes
from test_textbook_preparation_crops import crop_fixture as crop_fixture, request
from test_personal_textbook_study import book_fixture as book_fixture
from test_personal_textbook_ui import facade_for
from test_personal_textbook_crops import selection as personal_selection
from integrations.deeptutor_shchem_v1 import desktop_textbook_preparation_crops as crops
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    TaskCancellationEvent, ReadCancelled, read_cancel_scope,
)


def batch(f):
    first = request(f)
    bounds = dict(f.bounds, left=110, right=650)
    value = f.crops.preview(f.selection, f.page['page_revision'], bounds, '第二张合成图', '比较另一选区')
    second = dict(first, bounds=bounds, caption=value['image_asset']['caption'],
                  purpose=value['image_asset']['purpose'], expected_preview_revision=value['preview_revision'],
                  expected_image_sha256=value['image_asset']['sha256'])
    assert first['expected_image_sha256'] != second['expected_image_sha256']
    return [first, second]


def test_two_real_crops_publish_complete_ordered_result_and_closed_receipts(crop_fixture):
    f = crop_fixture; before = disk_bytes(f.root); choices = batch(f)
    result = f.facade.import_textbook_preparation_crops(choices)
    assert (result['requested_count'], result['added_count'], result['duplicate_count']) == (2, 2, 0)
    assert result['candidate_only'] and not result['human_reviewed']
    store = PreparationImageStore(f.task_root / 'preparation-v1/images')
    for asset, chosen in zip(result['image_assets'], choices, strict=True):
        value = f.crops.preview(chosen['selection'], chosen['page_revision'], chosen['bounds'], chosen['caption'], chosen['purpose'])
        assert asset == value['image_asset'] and store.load(asset) == value['image_bytes']
    receipts = [json.loads(p.read_text(encoding='utf-8')) for p in f.task_root.rglob('*.json')]
    assert len(receipts) == 2 and all(r['source_sha256'] == f.asset['source_sha256'] for r in receipts)
    assert all(not r[k] for r in receipts for k in ('human_reviewed', 'retrieval_ready', 'generation_allowed', 'publication_allowed'))
    assert disk_bytes(f.root) == before


def test_duplicates_keep_first_choice_and_existing_metadata_without_rewriting(crop_fixture):
    f = crop_fixture; choices = batch(f)
    first = f.facade.import_textbook_preparation_crop(choices[0])
    first['caption'] = '教师已改的旧图题'
    result = f.facade.import_textbook_preparation_crops([choices[1], choices[0], choices[1]], [first])
    assert result['image_assets'][0] == first
    assert [a['sha256'] for a in result['image_assets']] == [c['expected_image_sha256'] for c in choices]
    assert (result['added_count'], result['duplicate_count']) == (1, 2)
    saved = disk_bytes(f.task_root)
    repeated = f.facade.import_textbook_preparation_crops(choices, result['image_assets'])
    assert repeated['image_assets'] == result['image_assets'] and repeated['added_count'] == 0
    assert disk_bytes(f.task_root) == saved


@pytest.mark.parametrize('change', ['caption', 'confirmation', 'missing', 'extra', 'digest'])
def test_invalid_later_crop_does_not_publish_the_first(crop_fixture, change):
    f = crop_fixture; choices = batch(f)
    if change == 'caption': choices[1]['caption'] = '改变图题'
    elif change == 'confirmation': choices[1]['selection_confirmed'] = False
    elif change == 'missing': choices[1].pop('purpose')
    elif change == 'extra': choices[1]['path'] = str(f.book)
    else: choices[1]['expected_image_sha256'] = 'a' * 64
    with pytest.raises(DesktopFacadeError): f.facade.import_textbook_preparation_crops(choices)
    assert not f.task_root.exists()


@pytest.mark.parametrize('choices', [None, {}, [], [None]])
def test_batch_shape_and_limit_fail_before_any_write(crop_fixture, choices):
    f = crop_fixture
    with pytest.raises(DesktopFacadeError): f.facade.import_textbook_preparation_crops(choices)
    assert not f.task_root.exists()


def test_merged_lesson_capacity_and_byte_limit_fail_before_any_write(crop_fixture, monkeypatch):
    f = crop_fixture; choices = batch(f); value = f.crops.preview(**{k: choices[0][k] for k in ('selection', 'page_revision', 'bounds', 'caption', 'purpose')})
    before = disk_bytes(f.root)
    previous = []
    for index in range(crops.MAX_IMAGES - 1):
        digest = f'{index:064x}'
        previous.append(dict(value['image_asset'], asset_id='IMG-' + digest, sha256=digest))
    frozen_choices, frozen_previous = deepcopy(choices), deepcopy(previous)
    original_byte_limit = crops.MAX_BATCH_IMAGE_BYTES
    save_calls = []
    def blocked_save(service, *args, **kwargs):
        save_calls.append(service)
        raise AssertionError('class-save-probe')
    monkeypatch.setattr(crops.TextbookPreparationCropService, '_save_preview', blocked_save)
    with pytest.raises(DesktopFacadeError, match=f'最多选择{crops.MAX_IMAGES}张'):
        f.facade.import_textbook_preparation_crops(choices, previous)
    assert save_calls == []
    assert not f.task_root.exists() and disk_bytes(f.root) == before
    assert choices == frozen_choices and previous == frozen_previous
    monkeypatch.setattr(crops, 'MAX_BATCH_IMAGE_BYTES', 1)
    with pytest.raises(DesktopFacadeError, match='本批裁片总量过大'):
        f.facade.import_textbook_preparation_crops(choices)
    assert save_calls == []
    assert not f.task_root.exists() and disk_bytes(f.root) == before
    assert choices == frozen_choices and previous == frozen_previous
    # The facade constructs a fresh service; prove that the class probe catches it.
    monkeypatch.setattr(crops, 'MAX_BATCH_IMAGE_BYTES', original_byte_limit)
    with pytest.raises(DesktopFacadeError) as probe:
        f.facade.import_textbook_preparation_crops(choices)
    assert isinstance(probe.value.__cause__, AssertionError)
    assert str(probe.value.__cause__) == 'class-save-probe'
    assert len(save_calls) == 1 and save_calls[0] is not f.crops
    assert not f.task_root.exists() and disk_bytes(f.root) == before
    assert choices == frozen_choices and previous == frozen_previous


def test_raw_batch_over_current_maximum_is_rejected_before_preview_or_write(crop_fixture, monkeypatch):
    f = crop_fixture
    chosen = request(f)
    choices = [deepcopy(chosen) for _ in range(crops.MAX_IMAGES + 1)]
    frozen_choices = deepcopy(choices); before = disk_bytes(f.root)
    calls = {'preview': [], 'save': []}
    def blocked_preview(service, *args, **kwargs):
        calls['preview'].append(service)
        raise AssertionError('class-preview-probe')
    def blocked_save(service, *args, **kwargs):
        calls['save'].append(service)
        raise AssertionError('class-save-probe')
    monkeypatch.setattr(crops.TextbookPreparationCropService, 'preview', blocked_preview)
    monkeypatch.setattr(crops.TextbookPreparationCropService, '_save_preview', blocked_save)
    with pytest.raises(DesktopFacadeError, match=f'1至{crops.MAX_IMAGES}张'):
        f.facade.import_textbook_preparation_crops(choices)
    assert calls == {'preview': [], 'save': []}
    assert choices == frozen_choices and not f.task_root.exists()
    assert disk_bytes(f.root) == before
    with pytest.raises(DesktopFacadeError) as probe:
        f.facade.import_textbook_preparation_crops([chosen])
    assert isinstance(probe.value.__cause__, AssertionError)
    assert str(probe.value.__cause__) == 'class-preview-probe'
    assert len(calls['preview']) == 1 and calls['preview'][0] is not f.crops
    assert calls['save'] == []
    assert choices == frozen_choices and not f.task_root.exists()
    assert disk_bytes(f.root) == before


def test_merged_lesson_at_current_maximum_keeps_existing_metadata_and_two_real_crops(crop_fixture):
    f = crop_fixture; choices = batch(f)
    value = f.crops.preview(**{k: choices[0][k] for k in ('selection', 'page_revision', 'bounds', 'caption', 'purpose')})
    previous = [dict(value['image_asset'], asset_id='IMG-' + f'{index:064x}', sha256=f'{index:064x}')
                for index in range(crops.MAX_IMAGES - 2)]
    frozen_choices, frozen_previous = deepcopy(choices), deepcopy(previous)
    before = disk_bytes(f.root)
    result = f.facade.import_textbook_preparation_crops(choices, previous)
    assert len(result['image_assets']) == crops.MAX_IMAGES
    assert result['image_assets'][:-2] == previous
    assert [a['sha256'] for a in result['image_assets'][-2:]] == [c['expected_image_sha256'] for c in choices]
    assert (result['requested_count'], result['added_count'], result['duplicate_count']) == (2, 2, 0)
    store = PreparationImageStore(f.task_root / 'preparation-v1/images')
    for asset, chosen in zip(result['image_assets'][-2:], choices, strict=True):
        preview = f.crops.preview(**{k: chosen[k] for k in ('selection', 'page_revision', 'bounds', 'caption', 'purpose')})
        assert asset == preview['image_asset'] and store.load(asset) == preview['image_bytes']
    assert choices == frozen_choices and previous == frozen_previous
    assert disk_bytes(f.root) == before
    assert result['candidate_only'] and not result['human_reviewed']


def test_source_changed_after_last_preview_cannot_release_earlier_crop(crop_fixture, monkeypatch):
    f = crop_fixture; choices = batch(f); actual = f.crops.preview; calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        value = actual(*args, **kwargs); calls += 1
        if calls == 2:
            f.asset['description'] = 'changed after final preview'
            f.write_assets([f.asset])
        return value
    monkeypatch.setattr(f.crops, 'preview', changed)
    with pytest.raises(ValueError): f.crops.confirm_batch(choices)
    assert not f.task_root.exists()


def test_cancellation_before_batch_publication_leaves_no_partial_images(crop_fixture, monkeypatch):
    f = crop_fixture; choices = batch(f); stop = TaskCancellationEvent(); actual = f.crops.preview; calls = 0
    def cancelled(*args, **kwargs):
        nonlocal calls
        value = actual(*args, **kwargs); calls += 1
        if calls == 2: stop.set()
        return value
    monkeypatch.setattr(f.crops, 'preview', cancelled)
    with pytest.raises(ReadCancelled), read_cancel_scope(stop): f.crops.confirm_batch(choices)
    assert not f.task_root.exists()


def test_broken_existing_image_prevents_new_batch_publication(crop_fixture):
    f = crop_fixture; choices = batch(f)
    old = f.facade.import_textbook_preparation_crop(choices[0])
    next(f.task_root.rglob('*.image')).write_bytes(b'broken previous image')
    before = disk_bytes(f.task_root)
    with pytest.raises(DesktopFacadeError): f.facade.import_textbook_preparation_crops(choices, [old])
    assert disk_bytes(f.task_root) == before


def test_later_io_failure_keeps_closed_candidates_and_returns_no_partial_batch(crop_fixture, monkeypatch):
    f = crop_fixture; choices = batch(f); original = PreparationImageStore.import_bytes; calls = 0
    def fail_second(store, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError('synthetic second image write failure')
        return original(store, *args, **kwargs)
    monkeypatch.setattr(PreparationImageStore, 'import_bytes', fail_second)
    with pytest.raises(DesktopFacadeError): f.facade.import_textbook_preparation_crops(choices)
    records = [json.loads(p.read_text(encoding='utf-8')) for p in f.task_root.rglob('*.json')]
    assert len(records) == 2 and len(list(f.task_root.rglob('*.image'))) == 1
    assert all(r['candidate_only'] and not r['human_reviewed'] and not r['publication_allowed'] for r in records)
    saved_receipts = {p: p.read_bytes() for p in f.task_root.rglob('*.json')}
    monkeypatch.setattr(PreparationImageStore, 'import_bytes', original)
    result = f.facade.import_textbook_preparation_crops(choices)
    assert result['added_count'] == 2 and len(result['image_assets']) == 2
    assert all(p.read_bytes() == data for p, data in saved_receipts.items())


def test_new_personal_book_two_pages_keep_unknowns_and_original_archive(book_fixture):
    f = book_fixture; facade = facade_for(f); before = disk_bytes(f.workspace.root); choices = []
    for page_number in (2, 4):
        selected = personal_selection(f, page_number); page = facade.preparation_textbook_crop_page(selected)
        bounds = {'left': 100, 'top': 400, 'right': 700, 'bottom': 1000}
        value = facade.preparation_textbook_crop_preview(selected, page['page_revision'], bounds,
            f'个人合成图{page_number}', '比较可见色块')
        choices.append({'selection': selected, 'page_revision': page['page_revision'], 'bounds': bounds,
            'caption': value['image_asset']['caption'], 'purpose': value['image_asset']['purpose'],
            'expected_preview_revision': value['preview_revision'], 'expected_image_sha256': value['image_asset']['sha256'],
            'selection_confirmed': True})
    result = facade.import_textbook_preparation_crops(choices)
    assert result['added_count'] == 2 and disk_bytes(f.workspace.root) == before
    records = [json.loads(p.read_text(encoding='utf-8')) for p in (f.paths.task_root/'preparation-v1/textbook-crops').glob('*.json')]
    assert {r['pdf_page'] for r in records} == {2, 4}
    assert all(r['source_id'] == f.selection['source_id'] and r['volume_id'] is None and r['printed_page'] is None for r in records)
    assert f.source.read_bytes() == f.original and not f.study.book_study(f.selection)['sections']
