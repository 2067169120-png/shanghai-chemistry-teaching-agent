"""Isolated mixed-image transactions; no application/state-store constructors."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture, selection
from test_textbook_bookshelf import shelf_fixture, disk_bytes
from test_textbook_preparation_crops import request
from integrations.deeptutor_shchem_v1 import desktop_composition_image_batch as batch
from integrations.deeptutor_shchem_v1.desktop_textbook_preparation_crops import TextbookPreparationCropService
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource
from integrations.deeptutor_shchem_v1.reader_cancellation import TaskCancellationEvent, ReadCancelled, read_cancel_scope


@pytest.fixture
def crop_fixture(shelf_fixture, tmp_path):
    f = shelf_fixture
    f.task_root = tmp_path.parent / (tmp_path.name+'-crop-state')
    f.crops = TextbookPreparationCropService(f.root, f.task_root)
    f.selection = selection(f)
    f.page = f.crops.page(f.selection)
    f.bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    f.facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=f.root, task_root=f.task_root),
        preparation_textbook_crop_preview=f.crops.preview)
    return f


def photo(path, color='navy', title='观察颜色变化'):
    out = BytesIO(); Image.new('RGB', (640, 360), color).save(out, format='PNG'); data = out.getvalue()
    path.write_bytes(data)
    local = {'file_path': str(path), 'sha256': sha256(data).hexdigest(), 'caption': title,
        'source': str(path.absolute()), 'purpose': '比较图中两个区域。'}
    return {'kind': 'image', 'selection': {'local': local, 'title': title,
        'purpose': local['purpose'], 'teacher_only': False}}


def facade(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(task_root=tmp_path/'state'))


def test_prepare_and_cancel_are_pure_and_keep_photos_and_order(tmp_path):
    f = facade(tmp_path); first = photo(tmp_path/'first.png'); second = photo(tmp_path/'second.png', 'red', '先看红色')
    before = disk_bytes(tmp_path)
    value = batch.prepare_image_batch(f, [], [], [second, first])
    assert [r['title'] for r in value.result['resources']] == ['先看红色', '观察颜色变化']
    assert disk_bytes(tmp_path) == before and not f.paths.task_root.exists()
    assert str((tmp_path/'first.png').absolute()) in value.result['resources'][1]['original']['evidence']['source_path']
    value.discard(); assert disk_bytes(tmp_path) == before


def test_same_pixels_have_distinct_uses_and_one_stored_image(tmp_path):
    f = facade(tmp_path); first = photo(tmp_path/'photo.png'); other = deepcopy(first)
    other.update(title='这时出现了什么变化？', purpose='解释另一阶段。')
    value = batch.prepare_image_batch(f, [], [], [first, first, other])
    assert len(value.result['resources']) == 2 and len(value.result['image_assets']) == 1
    result = value.publish(); value.accept(); value.discard()
    assert len(list(f.paths.task_root.rglob('*.image'))) == 1
    assert result['resources'][1]['original']['title'] == first['selection']['local']['caption']
    assert result['resources'][1]['original']['blocks'][1]['value'] == first['selection']['purpose']
    assert result['resources'][1]['blocks'][1]['value'] == '解释另一阶段。'
    again = batch.prepare_image_batch(f, result['image_assets'], result['resources'], [first, other])
    assert not again.result['resources']


def test_later_source_change_never_publishes_earlier_photo(tmp_path):
    f = facade(tmp_path); first = photo(tmp_path/'one.png'); second = photo(tmp_path/'two.png', 'red')
    value = batch.prepare_image_batch(f, [], [], [first, second]); (tmp_path/'two.png').unlink()
    with pytest.raises(ValueError): value.publish()
    assert not f.paths.task_root.exists(); value.discard()


def test_final_guard_failure_rolls_back_only_owned_files(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'image.png')
    value = batch.prepare_image_batch(f, [], [], [choice])
    def guard():
        if list(f.paths.task_root.rglob('*.image')): raise ValueError('insertion changed')
    with pytest.raises(ValueError, match='insertion changed'): value.publish(guard)
    assert not list(f.paths.task_root.rglob('*.image'))
    assert not list(f.paths.task_root.rglob('.image-batch-*'))


def test_cancel_before_publish_and_mutated_result_do_not_write(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'image.png'); value = batch.prepare_image_batch(f, [], [], [choice])
    stop = TaskCancellationEvent(); stop.request_cancel()
    with read_cancel_scope(stop, check_boundaries=False), pytest.raises(ReadCancelled): value.publish()
    assert not f.paths.task_root.exists()
    value.result['resources'][0]['title'] = 'changed'
    with pytest.raises(ValueError, match='暂存内容'): value.publish()
    assert not f.paths.task_root.exists()


def test_accepted_competing_batch_protects_shared_owned_file(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'image.png')
    first = batch.prepare_image_batch(f, [], [], [choice]); second = batch.prepare_image_batch(f, [], [], [choice])
    first.publish(); second.publish(); second.accept(); first.discard()
    target = next(f.paths.task_root.rglob('*.image'))
    assert target.read_bytes() == (tmp_path/'image.png').read_bytes()


def test_preexisting_file_and_referenced_history_survive_discard(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'image.png')
    value = batch.prepare_image_batch(f, [], [], [choice]); result = value.publish()
    value.discard(referenced_assets=result['image_assets'])
    before = disk_bytes(f.paths.task_root)
    other = batch.prepare_image_batch(f, [], [], [choice]); other.publish(); other.discard()
    assert disk_bytes(f.paths.task_root) == before


def test_mid_link_failure_cleans_first_picture_but_keeps_source(tmp_path, monkeypatch):
    f = facade(tmp_path); choices = [photo(tmp_path/'one.png'), photo(tmp_path/'two.png', 'red')]
    value = batch.prepare_image_batch(f, [], [], choices); original = batch.os.link; count = 0
    def fail(source, target):
        nonlocal count
        count += 1
        if count == 2: raise OSError('disk full')
        return original(source, target)
    monkeypatch.setattr(batch.os, 'link', fail)
    with pytest.raises(OSError, match='disk full'): value.publish()
    assert not list(f.paths.task_root.rglob('*.image'))
    assert (tmp_path/'one.png').is_file() and (tmp_path/'two.png').is_file()


def test_teacher_alias_stays_teacher_only_in_any_queue_position(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'image.png'); local = choice['selection']['local']
    data = Path(local['file_path']).read_bytes()
    asset = {'asset_id': 'IMG-'+local['sha256'], **{k: local[k] for k in ('sha256','caption','source','purpose')}, **image_info(data)}
    folder = f.paths.task_root/'preparation-v1/images'; folder.mkdir(parents=True)
    (folder/(asset['sha256']+'.image')).write_bytes(data)
    evidence = {'source_path': asset['source'], 'source_sha256': asset['sha256'],
        'source_revision': batch.digest(asset), 'image_asset': asset, 'source_roles': ['teacher_note']}
    previous = resource('preparation_image', '原教师图', [block('image','teacher_note',asset['asset_id']),
        block('text','teacher_note','教师讲解')], evidence)
    value = batch.prepare_image_batch(f, [asset], [previous], [choice])
    assert all(b['role']=='teacher_note' for b in value.result['resources'][0]['blocks'])
    assert value.result['image_assets'] == [asset]


def test_same_pixels_from_two_local_paths_keep_each_original(tmp_path):
    f = facade(tmp_path); first = photo(tmp_path/'first.png'); second = photo(tmp_path/'second.png')
    value = batch.prepare_image_batch(f, [], [], [first, second])
    assert len(value.result['image_assets']) == 1 and len(value.result['resources']) == 2
    originals = [row['original']['evidence'] for row in value.result['resources']]
    assert originals[0]['source_path'] == str((tmp_path/'first.png').absolute())
    assert originals[1]['source_path'] == str((tmp_path/'second.png').absolute())
    assert originals[0]['source_revision'] != originals[1]['source_revision']


@pytest.mark.parametrize('reverse', [False, True])
def test_local_and_saved_alias_preserve_binding_and_teacher_scope(tmp_path, reverse):
    from test_saved_preparation_images import source_payload, image_card
    from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved
    f = facade(tmp_path); local = photo(tmp_path/'local.png')
    details = local['selection']['local']; data = Path(details['file_path']).read_bytes()
    asset = {'asset_id':'IMG-'+details['sha256'], **{k:details[k] for k in ('sha256','caption','purpose')},
        'source':'保存备课的原照片来源', **image_info(data)}
    payload = source_payload([asset], [image_card(asset, 'teacher_note')])
    revision = batch.digest(payload)
    f.load_preparation_draft = lambda identity, expected: {'draft_id':identity,'revision':expected,'payload':deepcopy(payload)}
    folder = f.paths.task_root/'preparation-v1/images'; folder.mkdir(parents=True)
    (folder/(asset['sha256']+'.image')).write_bytes(data)
    binding = saved._binding('saved-image-course', revision, payload, asset, saved._references(payload, [asset]))
    selected = {'kind':'image','selection':{'saved_preparation':binding,
        'title':local['selection']['title'],'purpose':local['selection']['purpose'],'teacher_only':False}}
    choices = [local, selected] if not reverse else [selected, local]
    value = batch.prepare_image_batch(f, [], [], choices)
    assert len(value.result['image_assets']) == 1 and len(value.result['resources']) == 2
    for row in value.result['resources']: assert all(b['role']=='teacher_note' for b in row['blocks'])
    originals = [r['original'] for r in value.result['resources']]
    saved_original = next(r for r in originals if 'selected_from_preparation' in r)
    local_original = next(r for r in originals if 'selected_from_preparation' not in r)
    assert saved_original['evidence']['image_asset'] == asset
    assert saved_original['selected_from_preparation'] == binding
    assert local_original['evidence']['source_path'] == str((tmp_path/'local.png').absolute())
    payload['topic'] = '已变化的原备课'
    with pytest.raises(ValueError, match='变化'): value.publish()
    assert not list(f.paths.task_root.rglob('*.json'))


def test_same_page_two_bounds_and_photo_keep_order_and_receipts(crop_fixture, tmp_path):
    f = crop_fixture; first = request(f); second = deepcopy(first)
    second['bounds']['right'] += 30
    preview = f.crops.preview(second['selection'],second['page_revision'],second['bounds'],second['caption'],second['purpose'])
    second.update(expected_preview_revision=preview['preview_revision'],expected_image_sha256=preview['image_asset']['sha256'])
    picture = photo(tmp_path/'scene.png')
    choices = [picture, {'kind':'textbook','request':second,'title':'先观察第二裁片'},
        {'kind':'textbook','request':first,'title':'比较第一裁片','purpose':'按教学顺序解释。'}]
    before = disk_bytes(f.root)
    value = batch.prepare_image_batch(f.facade, [], [], choices)
    assert not f.task_root.exists() and disk_bytes(f.root) == before
    assert [r['title'] for r in value.result['resources']] == ['观察颜色变化','先观察第二裁片','比较第一裁片']
    result = value.publish(); value.accept()
    assert len(list(f.task_root.rglob('*.image'))) == 3 and len(list(f.task_root.rglob('*.json'))) == 2
    assert result['resources'][2]['original']['title'] == first['caption']
    assert result['resources'][2]['original']['blocks'][1]['value'] == first['purpose']
    assert result['resources'][2]['original']['evidence']['pdf_pages'] == [9]
    assert disk_bytes(f.root) == before


def test_mixed_batch_failure_cleans_owned_crop_receipt(crop_fixture, tmp_path):
    f = crop_fixture; choices = [{'kind':'textbook','request':request(f)}, photo(tmp_path/'scene.png')]
    value = batch.prepare_image_batch(f.facade, [], [], choices)
    def guard():
        if list(f.task_root.rglob('*.json')): raise ValueError('late target change')
    with pytest.raises(ValueError,match='late target change'): value.publish(guard)
    assert not list(f.task_root.rglob('*.json')) and not list(f.task_root.rglob('*.image'))


def test_unique_capacity_is_checked_before_publication(tmp_path, monkeypatch):
    f = facade(tmp_path); choices = [photo(tmp_path/'one.png'), photo(tmp_path/'two.png','red')]
    monkeypatch.setattr(batch, 'MAX_BATCH_IMAGE_BYTES', 1)
    with pytest.raises(ValueError, match='总量'): batch.prepare_image_batch(f, [], [], choices)
    assert not f.paths.task_root.exists()


def test_capacity_counts_unique_assets_and_preserves_existing_metadata(tmp_path, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_preparation_images as images
    f = facade(tmp_path); first = photo(tmp_path/'one.png'); second = photo(tmp_path/'two.png','red')
    value = batch.prepare_image_batch(f, [], [], [first]); result = value.publish(); value.accept()
    metadata = deepcopy(result['image_assets']); metadata[0]['caption'] = '本课已保存图题'
    before = disk_bytes(f.paths.task_root)
    monkeypatch.setattr(images, 'MAX_IMAGES', 1)
    other = deepcopy(first); other['title'] = '另一教学用途'
    compatible = batch.prepare_image_batch(f, metadata, result['resources'], [other])
    assert compatible.result['image_assets'] == metadata
    assert compatible.result['resources'][0]['original']['evidence']['image_asset']['caption'] == first['selection']['local']['caption']
    with pytest.raises(ValueError): batch.prepare_image_batch(f, metadata, result['resources'], [second])
    assert disk_bytes(f.paths.task_root) == before


@pytest.mark.parametrize('reverse', [False, True])
def test_last_cancelled_shared_batch_cleans_pixels_and_receipt(crop_fixture, reverse):
    f = crop_fixture; choices = [{'kind': 'textbook', 'request': request(f)}]
    first = batch.prepare_image_batch(f.facade, [], [], choices)
    second = batch.prepare_image_batch(f.facade, [], [], choices)
    first.publish(); second.publish()
    if reverse: first, second = second, first
    first.discard()
    assert len(list(f.task_root.rglob('*.image'))) == 1
    assert len(list(f.task_root.rglob('*.json'))) == 1
    second.discard()
    assert not list(f.task_root.rglob('*.image')) and not list(f.task_root.rglob('*.json'))


@pytest.mark.parametrize('change', ['delete', 'replace'])
def test_receipt_change_after_publish_rejects_ui_handoff(crop_fixture, change):
    f = crop_fixture
    value = batch.prepare_image_batch(f.facade, [], [], [{'kind': 'textbook', 'request': request(f)}])
    value.publish(); receipt = next(f.task_root.rglob('*.json'))
    if change == 'delete': receipt.unlink()
    else: receipt.write_bytes(b'{"changed_by_another_writer": true}\n')
    with pytest.raises(ValueError, match='凭据|保存'):
        value.recheck_published()
    value.discard()
    assert not list(f.task_root.rglob('*.image'))
    if change == 'replace': assert receipt.read_bytes() == b'{"changed_by_another_writer": true}\n'


def test_target_collision_is_preserved_without_partial_files(tmp_path):
    f = facade(tmp_path); choices = [photo(tmp_path/'one.png'), photo(tmp_path/'two.png', 'red')]
    value = batch.prepare_image_batch(f, [], [], choices)
    folder = f.paths.task_root/'preparation-v1/images'; folder.mkdir(parents=True)
    collision = folder/(choices[1]['selection']['local']['sha256']+'.image')
    collision.write_bytes(b'an unrelated writer owns this path')
    before = disk_bytes(f.paths.task_root)
    with pytest.raises(ValueError, match='变化'):
        value.publish()
    assert disk_bytes(f.paths.task_root) == before


def test_source_changes_during_publication_roll_back_owned_files(tmp_path, monkeypatch):
    f = facade(tmp_path); choice = photo(tmp_path/'scene.png')
    value = batch.prepare_image_batch(f, [], [], [choice]); link = batch.os.link
    original_pixels = (tmp_path/'scene.png').read_bytes()
    def change_source(source, target):
        link(source, target)
        photo(tmp_path/'scene.png', 'orange')
    monkeypatch.setattr(batch.os, 'link', change_source)
    with pytest.raises(ValueError, match='变化'): value.publish()
    assert not list(f.paths.task_root.rglob('*.image'))
    assert (tmp_path/'scene.png').read_bytes() != original_pixels


@pytest.mark.parametrize('change', ['resource_id', 'block_id', 'original_digest'])
def test_even_identity_only_changes_cannot_publish_mutated_result(tmp_path, change):
    f = facade(tmp_path); value = batch.prepare_image_batch(f, [], [], [photo(tmp_path/'photo.png')])
    item = value.result['resources'][0]
    if change == 'resource_id': item['id'] = 'CR-'+'a'*32
    elif change == 'block_id': item['blocks'][0]['id'] = 'CB-'+'b'*32
    else: item['original_sha256'] = 'c'*64
    with pytest.raises(ValueError, match='暂存内容'): value.publish()
    assert not f.paths.task_root.exists()


def test_picker_classroom_edits_do_not_replace_source_purpose(tmp_path):
    f = facade(tmp_path); choice = photo(tmp_path/'photo.png')
    choice['selection'].update(title='本次问题', purpose='本课重新安排的用途。')
    value = batch.prepare_image_batch(f, [], [], [choice])
    item = value.result['resources'][0]
    assert item['original']['blocks'][1]['value'] == choice['selection']['local']['purpose']
    assert item['blocks'][1]['value'] == '本课重新安排的用途。'
    assert item['title'] == '本次问题'


def test_same_page_same_pixels_different_bounds_keep_both_sources(crop_fixture):
    f = crop_fixture; choices = []; previews = []
    for left in (200, 400):
        bounds = {'left': left, 'top': left, 'right': left+100, 'bottom': left+100}
        preview = f.crops.preview(f.selection, f.page['page_revision'], bounds, '合成等色区域', '比较不同原页位置。')
        selected = request(f, preview); selected['bounds'] = bounds
        choices.append({'kind': 'textbook', 'request': selected}); previews.append(preview)
    assert previews[0]['image_bytes'] == previews[1]['image_bytes']
    assert previews[0]['provenance']['crop_id'] != previews[1]['provenance']['crop_id']
    value = batch.prepare_image_batch(f.facade, [], [], choices)
    assert len(value.result['resources']) == 2 and len(value.result['image_assets']) == 1
    assert [r['original']['evidence']['crop_id'] for r in value.result['resources']] == [p['provenance']['crop_id'] for p in previews]
    result = value.publish(); value.recheck_published(); value.accept()
    assert len(list(f.task_root.rglob('*.image'))) == 1 and len(list(f.task_root.rglob('*.json'))) == 2
    (f.root/'same-pixel-case.json').write_text(json.dumps({'requests': choices, 'result': result,
        'workspace_root': str(f.root), 'task_root': str(f.task_root)}, ensure_ascii=False, indent=2), encoding='utf-8')


def test_receipt_alias_accepted_after_creator_cancel_remains_durable(crop_fixture):
    f = crop_fixture; choices = [{'kind': 'textbook', 'request': request(f)}]
    first = batch.prepare_image_batch(f.facade, [], [], choices)
    second = batch.prepare_image_batch(f.facade, [], [], choices)
    first.publish(); second.publish(); first.discard(); second.recheck_published(); second.accept()
    second.discard()
    assert len(list(f.task_root.rglob('*.image'))) == 1 and len(list(f.task_root.rglob('*.json'))) == 1
    assert not any(key.startswith(batch._path_key(f.task_root)) for key in batch._CLAIMS)
    assert not any(key.startswith(batch._path_key(f.task_root)) for key in batch._OWNED)


def test_existing_receipt_with_duplicate_json_keys_is_not_silently_reused(crop_fixture):
    f = crop_fixture; choice = {'kind': 'textbook', 'request': request(f)}
    value = batch.prepare_image_batch(f.facade, [], [], [choice]); value.publish(); value.accept()
    receipt = next(f.task_root.rglob('*.json')); raw = receipt.read_bytes()
    receipt.write_bytes(b'{"candidate_only": false, '+raw[1:])
    before = disk_bytes(f.task_root)
    again = batch.prepare_image_batch(f.facade, [], [], [choice])
    with pytest.raises(ValueError, match='重复字段'): again.publish()
    assert disk_bytes(f.task_root) == before


def test_publication_keeps_static_teacher_scope_and_candidate_flags(crop_fixture):
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import FLAGS, visible_blocks
    f = crop_fixture; chosen = {'kind': 'textbook', 'request': request(f), 'teacher_only': True}
    value = batch.prepare_image_batch(f.facade, [], [], [chosen]); row = value.result['resources'][0]
    assert all(row[key] is flag for key, flag in FLAGS.items())
    assert all(part['role'] == 'teacher_note' for part in row['original']['blocks'])
    assert visible_blocks(row, student=True) == []
    result = value.publish(); value.recheck_published(); value.accept()
    record = json.loads(next(f.task_root.rglob('*.json')).read_text(encoding='utf-8'))
    assert record['candidate_only'] is True
    assert all(record[key] is False for key in ('human_reviewed', 'retrieval_ready', 'generation_allowed', 'publication_allowed'))
    assert result['resources'][0]['original'] == row['original']


def test_target_replaced_at_final_guard_is_detected_and_not_deleted(tmp_path):
    f = facade(tmp_path); chosen = photo(tmp_path/'photo.png')
    value = batch.prepare_image_batch(f, [], [], [chosen])
    target = f.paths.task_root/'preparation-v1/images'/(chosen['selection']['local']['sha256']+'.image')
    def guard():
        if target.exists(): target.write_bytes(b'a later writer replaced this file')
    with pytest.raises(ValueError, match='已保存'): value.publish(guard)
    assert target.read_bytes() == b'a later writer replaced this file'
    assert not list(f.paths.task_root.rglob('.image-batch-*'))


def test_history_reference_is_recognized_after_leaving_current_page(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_lesson_composition import new_composition
    f = facade(tmp_path); value = batch.prepare_image_batch(f, [], [], [photo(tmp_path/'photo.png')])
    history = {'composition': new_composition()}
    history['composition']['resources'] = deepcopy(value.result['resources'])
    assert batch.image_batch_is_referenced(value.result, None, {}, history)
    assert not batch.image_batch_is_referenced(value.result, {'composition': new_composition()}, {})
