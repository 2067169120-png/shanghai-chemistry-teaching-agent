"""One source-bound knowledge card hands off every selected crop together."""
from copy import deepcopy
import json

import pytest

from test_note_page_crops import notes as notes, request, section_fixture as section_fixture, asset_fixture as asset_fixture, shelf_fixture as shelf_fixture
from test_textbook_bookshelf import disk_bytes
from test_textbook_reading_hints import write_packet
from test_personal_textbook_knowledge import native_fixture as native_fixture, draft_for, choose
from test_composition_inline_personal import personal_facade
from test_textbook_concept_cards import concept as concept
from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import concept_page_selection
from integrations.deeptutor_shchem_v1 import desktop_composition_sources as sources
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import validate_resource
from integrations.deeptutor_shchem_v1.desktop_preparation_images import MAX_IMAGES, PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_personal_textbook_crops import book_page_selection
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService
from integrations.deeptutor_shchem_v1.reader_cancellation import TaskCancellationEvent, ReadCancelled, read_cancel_scope


def batch(f):
    choices = [request(f, 8), request(f, 9)]
    choices[1]['bounds'] = dict(choices[1]['bounds'], left=110, right=660)
    choices[1]['caption'] = '第二张原页裁片'
    preview = f.facade.preparation_textbook_crop_preview(**{
        k: choices[1][k] for k in ('selection', 'page_revision', 'bounds', 'caption', 'purpose')})
    choices[1]['expected_preview_revision'] = preview['preview_revision']
    choices[1]['expected_image_sha256'] = preview['image_asset']['sha256']
    assert choices[0]['expected_image_sha256'] != choices[1]['expected_image_sha256']
    return choices


def counted(facade, monkeypatch):
    calls = []
    actual = facade.import_textbook_preparation_crops

    def publish(requests, existing_assets):
        calls.append((deepcopy(requests), deepcopy(existing_assets)))
        return actual(requests, existing_assets)

    monkeypatch.setattr(facade, 'import_textbook_preparation_crops', publish)
    return calls


def test_two_crops_use_one_transaction_and_every_resource_binds_same_original_card(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    before, card_before = disk_bytes(f.root), deepcopy(f.item)
    f.item['title'] = '教师修改的工作标题'
    calls = counted(f.facade, monkeypatch)
    result = sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert calls == [(choices, [])] and len(result['resources']) == len(result['image_assets']) == 2
    assert [r['original']['evidence']['pdf_pages'] for r in result['resources']] == [[8], [9]]
    store = PreparationImageStore(f.task_root / 'preparation-v1/images')
    for row, asset, chosen in zip(result['resources'], result['image_assets'], choices, strict=True):
        preview = f.crops.preview(**{k: chosen[k] for k in ('selection', 'page_revision', 'bounds', 'caption', 'purpose')})
        assert store.load(asset) == preview['image_bytes'] and asset['sha256'] == chosen['expected_image_sha256']
        selected = row['original']['selected_from_knowledge']
        assert selected['resource_id'] == f.item['id'] and selected['original_sha256'] == card_before['original_sha256']
        assert selected['title'] == card_before['original']['title']
        assert selected['source_sha256'] == row['original']['evidence']['source_sha256']
        assert validate_resource(row) == row and not row['human_reviewed'] and not row['publication_allowed']
    assert f.item['original'] == card_before['original'] and disk_bytes(f.root) == before
    assert choices == calls[0][0]


def test_single_knowledge_entry_delegates_to_batch_and_keeps_one_facade_call(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); chosen = request(f)
    actual = sources.import_knowledge_textbook_crops; delegated = []

    def batch_entry(*args):
        delegated.append(deepcopy(args[3]))
        return actual(*args)

    monkeypatch.setattr(sources, 'import_knowledge_textbook_crops', batch_entry)
    calls = counted(f.facade, monkeypatch)
    result = sources.import_knowledge_textbook_crop(f.facade, f.item, options['binding'], chosen, [])
    assert delegated == [[chosen]] and calls == [([chosen], [])]
    assert len(result['resources']) == 1 and result['resources'][0]['original']['selected_from_knowledge']['resource_id'] == f.item['id']


def test_duplicate_requests_keep_order_and_existing_asset_metadata_without_cache_rewrites(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    first = sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assets = deepcopy(first['image_assets']); assets[0]['caption'] = '旧图的教师工作标题'
    before = disk_bytes(f.task_root); calls = counted(f.facade, monkeypatch)
    selected = [choices[1], choices[0], choices[1]]
    result = sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], selected, assets)
    assert calls == [(selected, assets)] and result['image_assets'] == assets and len(result['resources']) == 3
    assert [r['original']['evidence']['pdf_pages'] for r in result['resources']] == [[9], [8], [9]]
    assert all(r['original']['selected_from_knowledge']['resource_id'] == f.item['id'] for r in result['resources'])
    assert disk_bytes(f.task_root) == before


@pytest.mark.parametrize('requests', [None, {}, [], (), [None] * (MAX_IMAGES + 1)])
def test_invalid_batch_shape_rejects_before_reading_or_writing(notes, monkeypatch, requests):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item)
    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_preview', lambda *_: pytest.fail('invalid batch read a page'))
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], requests, [])
    assert not calls and not f.task_root.exists()


@pytest.mark.parametrize('change', ['preview', 'sha', 'confirmation', 'outside', 'missing', 'extra'])
def test_invalid_later_crop_never_publishes_earlier_crop(notes, monkeypatch, change):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    if change == 'preview': choices[1]['expected_preview_revision'] = 'a' * 64
    elif change == 'sha': choices[1]['expected_image_sha256'] = 'a' * 64
    elif change == 'confirmation': choices[1]['selection_confirmed'] = False
    elif change == 'outside': choices[1]['selection'] = {'visual_asset_id': 'UNRELATED', 'revision': 'a' * 64}
    elif change == 'missing': choices[1].pop('purpose')
    else: choices[1]['path'] = str(f.book)
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert not calls and not f.task_root.exists()


@pytest.mark.parametrize('change', ['binding', 'sealed-card', 'note'])
def test_stale_card_or_binding_rejects_before_publication(notes, monkeypatch, change):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    binding = options['binding']
    if change == 'binding': binding = 'a' * 64
    elif change == 'sealed-card': f.item['original']['title'] += '静默换原内容'
    else: f.note['summary'] += '修改笔记'; write_packet(f.root, [f.note])
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, binding, choices, [])
    assert not calls and not f.task_root.exists()


def test_note_changes_during_last_read_are_rechecked_before_batch_commit(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    preview = f.facade.preparation_textbook_crop_preview; reads = 0

    def changed(*args):
        nonlocal reads
        value = preview(*args); reads += 1
        if reads == 2:
            f.note['summary'] += '最后预检后改变'; write_packet(f.root, [f.note])
        return value

    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_preview', changed)
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert reads == 2 and not calls and not f.task_root.exists()


def test_single_crop_rechecks_after_preview_before_existing_public_import(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); chosen = request(f)
    preview = f.facade.preparation_textbook_crop_preview

    def changed(*args):
        value = preview(*args)
        f.note['summary'] += '预检后改变'; write_packet(f.root, [f.note])
        return value

    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_preview', changed)
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crop(f.facade, f.item, options['binding'], chosen, [])
    assert not calls and not f.task_root.exists()


def test_note_changes_after_transaction_return_no_partial_bound_resources(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    actual = f.facade.import_textbook_preparation_crops; calls = []

    def late(requests, existing_assets):
        calls.append(deepcopy(requests))
        result = actual(requests, existing_assets)
        f.note['summary'] += '写入后改变'; write_packet(f.root, [f.note])
        return result

    monkeypatch.setattr(f.facade, 'import_textbook_preparation_crops', late)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert calls == [choices]
    records = [json.loads(p.read_text(encoding='utf8')) for p in (f.task_root / 'preparation-v1/textbook-crops').glob('*.json')]
    assert len(records) == 2 and all(r['candidate_only'] and not r['human_reviewed'] and not r['publication_allowed'] for r in records)


def test_binding_changes_during_memory_binding_are_rechecked_at_final_return(notes, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_lesson_composition as composition
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    actual = composition.bind_knowledge_selection; bound = []

    def changed(image, card):
        result = actual(image, card); bound.append(result)
        if len(bound) == 2:
            f.note['summary'] += '绑定后改变'; write_packet(f.root, [f.note])
        return result

    monkeypatch.setattr(composition, 'bind_knowledge_selection', changed)
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert calls == [(choices, [])] and len(bound) == 2
    assert all(row['original']['selected_from_knowledge']['resource_id'] == f.item['id'] for row in bound)


@pytest.mark.parametrize('malformed', ['count', 'missing-image'])
def test_incomplete_facade_result_cannot_return_resource_subset(notes, monkeypatch, malformed):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    actual = f.facade.import_textbook_preparation_crops

    def partial(*args):
        result = actual(*args)
        if malformed == 'count': result['requested_count'] = 1
        else: result['image_assets'] = result['image_assets'][:1]
        return result

    monkeypatch.setattr(f.facade, 'import_textbook_preparation_crops', partial)
    with pytest.raises(ValueError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])


@pytest.mark.parametrize('when', ['before-read', 'after-last-read', 'after-save'])
def test_cancellation_boundary_matches_existing_batch_transaction(notes, monkeypatch, when):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f); stop = TaskCancellationEvent()
    reads = 0; actual_preview = f.facade.preparation_textbook_crop_preview

    def preview(*args):
        nonlocal reads
        value = actual_preview(*args); reads += 1
        if reads == 2 and when == 'after-last-read': stop.set()
        return value

    monkeypatch.setattr(f.facade, 'preparation_textbook_crop_preview', preview)
    actual_import = f.facade.import_textbook_preparation_crops; calls = []

    def publish(*args):
        calls.append(deepcopy(args[0])); result = actual_import(*args)
        if when == 'after-save': stop.set()
        return result

    monkeypatch.setattr(f.facade, 'import_textbook_preparation_crops', publish)
    if when == 'before-read': stop.set()
    if when == 'after-save':
        # The durable transaction won: TaskCancellationEvent intentionally
        # rejects a late Stop rather than misreporting a committed task.
        with read_cancel_scope(stop):
            result = sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
        assert len(result['resources']) == 2 and not stop.is_set() and not stop.request_cancel()
    else:
        with pytest.raises(ReadCancelled), read_cancel_scope(stop):
            sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert calls == ([choices] if when == 'after-save' else [])
    if when != 'after-save': assert not f.task_root.exists()


def test_second_image_io_failure_keeps_closed_receipts_and_no_partial_handoff(notes, monkeypatch):
    f = notes; options = sources.knowledge_textbook_options(f.facade, f.item); choices = batch(f)
    actual = PreparationImageStore.import_bytes; writes = 0

    def fail_second(store, *args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2: raise OSError('synthetic later image write failure')
        return actual(store, *args, **kwargs)

    monkeypatch.setattr(PreparationImageStore, 'import_bytes', fail_second)
    calls = counted(f.facade, monkeypatch)
    with pytest.raises(DesktopFacadeError):
        sources.import_knowledge_textbook_crops(f.facade, f.item, options['binding'], choices, [])
    assert calls == [(choices, [])] and len(list(f.task_root.rglob('*.image'))) == 1
    records = [json.loads(p.read_text(encoding='utf8')) for p in (f.task_root / 'preparation-v1/textbook-crops').glob('*.json')]
    assert len(records) == 2 and all(not r['human_reviewed'] and not r['publication_allowed'] for r in records)


def test_personal_multpage_knowledge_card_binds_every_real_original_page(native_fixture, monkeypatch):
    f = native_fixture; service, view, draft = draft_for(f)
    current = service.adopt(f.selection, view['study_revision'], draft['draft_id'], draft['revision'], choose(draft))['view']
    facade = personal_facade(f)
    row = next(r for r in sources.knowledge_options(facade)['reading_notes'] if r['pdf_pages'] == [2, 3])
    card = sources.import_knowledge_note(facade, row['selection']); options = sources.knowledge_textbook_options(facade, card)
    crops = TextbookPreparationCropService(f.paths.workspace_root, f.paths.task_root, personal_books=f.workspace)
    choices = []
    for page_number in (2, 3):
        selected = book_page_selection(current['book'], page_number); page = crops.page(selected)
        bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
        preview = crops.preview(selected, page['page_revision'], bounds, f'原页图{page_number}', '比较知识卡原页')
        choices.append({'selection': selected, 'page_revision': page['page_revision'], 'bounds': bounds,
            'caption': preview['image_asset']['caption'], 'purpose': preview['image_asset']['purpose'],
            'expected_preview_revision': preview['preview_revision'], 'expected_image_sha256': preview['image_asset']['sha256'],
            'selection_confirmed': True})
    state_before, archive_before = f.state.path.read_bytes(), disk_bytes(f.workspace.root)
    calls = counted(facade, monkeypatch)
    result = sources.import_knowledge_textbook_crops(facade, card, options['binding'], choices, [])
    assert calls == [(choices, [])] and len(result['resources']) == 2
    assert [r['original']['evidence']['pdf_pages'] for r in result['resources']] == [[2], [3]]
    assert all(r['original']['evidence']['printed_pages'] == 'unknown' for r in result['resources'])
    assert all(r['original']['selected_from_knowledge']['resource_id'] == card['id'] for r in result['resources'])
    assert f.state.path.read_bytes() == state_before and disk_bytes(f.workspace.root) == archive_before
    assert f.source.read_bytes() == f.original


def test_concept_card_batch_retains_exact_record_source_and_each_page(concept, monkeypatch):
    f = concept; card = sources.import_knowledge_note(f.facade, f.option['selection'])
    options = sources.knowledge_textbook_options(f.facade, card); choices = []
    crops = TextbookPreparationCropService(f.root, f.facade.paths.task_root)
    for page_number in (7, 8):
        selected = concept_page_selection(f.option, page_number); page = crops.page(selected)
        bounds = {'left': 100, 'top': 100, 'right': 610 + page_number, 'bottom': 630}
        preview = crops.preview(selected, page['page_revision'], bounds, f'知识摘要原图{page_number}', '讲解知识摘要')
        choices.append({'selection': selected, 'page_revision': page['page_revision'], 'bounds': bounds,
            'caption': preview['image_asset']['caption'], 'purpose': preview['image_asset']['purpose'],
            'expected_preview_revision': preview['preview_revision'], 'expected_image_sha256': preview['image_asset']['sha256'],
            'selection_confirmed': True})
    before = disk_bytes(f.root); calls = counted(f.facade, monkeypatch)
    result = sources.import_knowledge_textbook_crops(f.facade, card, options['binding'], choices, [])
    assert calls == [(choices, [])] and len(result['resources']) == 2
    assert [r['original']['evidence']['pdf_pages'] for r in result['resources']] == [[7], [8]]
    assert all(r['original']['selected_from_knowledge']['record_id'] == f.row['concept_id'] for r in result['resources'])
    assert disk_bytes(f.root) == before
