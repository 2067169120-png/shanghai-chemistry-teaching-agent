"""Saved-course reuse keeps exact pixels, all source scopes and zero source writes."""
from copy import deepcopy
from hashlib import sha256
from threading import Event

import pytest

from test_composition_teaching_images import picture, source_question
from test_desktop_preparation_drafts import _payload, _record
from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved
from integrations.deeptutor_shchem_v1.desktop_composition_images import import_image_result
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FLAGS, DESIGN_SCHEMA, block, resource, digest, new_composition, validate_resource, visible_blocks)
from integrations.deeptutor_shchem_v1.desktop_lesson_design import new_design, new_node, validate_design
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftService
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore, MAX_IMAGES
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


class SavedFacade:
    def __init__(self, root):
        self.root = root; self.state = DesktopStateStore(root / 'state')
        self.store = PreparationImageStore(root / 'images')
        self.service = PreparationDraftService(self.state)
        self.import_calls = []; self.loads = 0; self.byte_reads = 0
        self.after_bytes = None; self.load_override = None

    def preparation_draft_options(self): return self.service.options()
    def preparation_draft_option(self, identity): return self.service.option(identity)

    def load_preparation_draft(self, identity, revision):
        self.loads += 1; result = self.service.load(identity, revision)
        return self.load_override(result) if self.load_override else result

    def preparation_image_bytes(self, asset):
        self.byte_reads += 1; data = self.store.load(asset)
        if self.after_bytes: self.after_bytes(self.byte_reads)
        return data

    def import_preparation_image(self, *args):
        self.import_calls.append(args); raise AssertionError('saved bytes must never be imported')

    def put(self, payload, identity='prep-source'):
        record = _record(payload)
        for name in ('image_assets', 'lesson_design'):
            if name in payload: record[name] = deepcopy(payload[name])
        self.state.save_draft(identity, record)
        return self.service.option(identity)


def snapshot(facade):
    return {str(p.relative_to(facade.root)): (p.stat().st_mtime_ns, sha256(p.read_bytes()).hexdigest())
            for p in facade.root.rglob('*') if p.is_file()}


def source_payload(assets, resources=(), nodes=()):
    payload = _payload(topic='为什么冰会浮在水面？'); payload['image_assets'] = deepcopy(assets)
    plan = new_design(payload); plan['nodes'] = deepcopy(list(nodes))
    plan['schema_version'] = DESIGN_SCHEMA; comp = new_composition('16:9')
    for item in resources:
        comp['resources'].append(deepcopy(item))
        comp['placements'][item['id']] = dict(page_id=comp['pages'][0], x=10, y=10, width=100, height=100)
    plan['composition'] = comp; payload['lesson_design'] = validate_design(plan)
    return payload


def image_card(asset, role='material', native_id=None):
    if native_id is None:
        evidence = {'source_path': asset['source'], 'source_sha256': asset['sha256'],
                    'source_revision': digest(asset), 'image_asset': deepcopy(asset), 'source_roles': []}
        return resource('preparation_image', asset['caption'], [block('image', role, asset['asset_id'])], evidence)
    native = {'schema': 'shchem.handout-picture.v1', 'batch_id': 'batch-local', 'source_id': 'source-local',
              'source_name': '本地合成讲义', 'source_path': 'original.docx', 'source_sha256': 'a'*64,
              'source_revision': 'b'*64, 'asset_id': native_id, 'block_index': 2,
              'mime_type': asset['content_type'], 'sha256': asset['sha256'], 'bytes_count': len(picture()[0])}
    evidence = {'source_path': native['source_path'], 'source_sha256': native['source_sha256'],
                'source_revision': native['source_revision'], 'handout_source': native,
                'image_asset': deepcopy(asset), 'source_roles': []}
    return resource('handout_image', '同一原图的另一处引用', [block('image', role, asset['asset_id'], 2)], evidence)


@pytest.fixture
def course(tmp_path):
    facade = SavedFacade(tmp_path); data, metadata = picture()
    asset = facade.store.import_bytes(data, metadata['caption'], metadata['source'], metadata['purpose'])
    option = facade.put(source_payload([asset], [image_card(asset)]))
    return facade, asset, option


def choose(facade, option, **changes):
    row = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    return {'saved_preparation': row['saved_preparation'], 'title': '冰的结构怎样改变密度？',
            'purpose': '比较两幅微观示意图，再解释冰为什么浮起', 'teacher_only': False, **changes}


def test_recent_courses_full_preview_and_reuse_never_copy_or_write_source(course):
    facade, asset, option = course; before = snapshot(facade)
    options = saved.saved_preparation_image_options(facade)
    assert options[0]['draft_id'] == option['draft_id'] and options[0]['image_count'] == 1
    row = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    assert row['saved_title'] == option['title'] and row['asset'] == asset
    assert row['source_roles'] == ['material'] and not row['teacher_only'] and not row['via_question']
    binding = row['saved_preparation']; assert binding['payload_sha256'] == digest(facade.service.load(option['draft_id'], option['revision'])['payload'])
    assert binding['source_resources'][0]['original_sha256'] == digest(binding['source_resources'][0]['original'])
    result = import_image_result(facade, [], [], choose(facade, option)); item = result['resources'][0]
    assert item['original']['selected_from_preparation'] == binding
    assert item['title'] == '冰的结构怎样改变密度？' and result['image_assets'] == [asset]
    assert all(item[k] is v for k, v in FLAGS.items()) and item['locked']
    assert facade.import_calls == [] and snapshot(facade) == before


def test_same_pixels_native_aliases_keep_every_original_and_any_teacher_scope(course):
    facade, asset, _option = course
    rows = [image_card(asset, 'material', 'native-first'), image_card(asset, 'teacher_note', 'native-answer')]
    option = facade.put(source_payload([asset], rows))
    chosen = choose(facade, option); refs = chosen['saved_preparation']['source_resources']
    assert [r['original']['evidence']['handout_source']['asset_id'] for r in refs] == ['native-first', 'native-answer']
    preview = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    assert preview['teacher_only'] and preview['source_roles'] == ['material', 'teacher_note']
    before = snapshot(facade); item = import_image_result(facade, [], [], chosen)['resources'][0]
    assert visible_blocks(item) == [] and all(b['role'] == 'teacher_note' for b in item['blocks'])
    bad = deepcopy(item)
    for b in [*bad['blocks'], *bad['original']['blocks']]: b['role'] = 'material'
    bad['original_sha256'] = digest(bad['original'])
    with pytest.raises(ValueError, match='教师'): validate_resource(bad)
    assert snapshot(facade) == before


def test_removing_answer_alias_and_resealing_preview_still_fails_current_source_binding(course):
    facade, asset, _option = course
    option = facade.put(source_payload([asset], [image_card(asset, 'material'), image_card(asset, 'teacher_note')]))
    selection = choose(facade, option); binding = selection['saved_preparation']
    binding['source_resources'] = binding['source_resources'][:1]; binding['source_roles'] = ['material']
    saved.validate_saved_preparation_binding(binding)  # Still structurally valid, but incomplete for this course.
    before = snapshot(facade)
    with pytest.raises(ValueError, match='关联或范围已变化'):
        import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before


def test_reuse_flag_promotion_and_other_resource_kind_binding_remain_invalid(course):
    facade, asset, option = course
    item = import_image_result(facade, [], [], choose(facade, option))['resources'][0]
    promoted = deepcopy(item); promoted['human_reviewed'] = True
    with pytest.raises(ValueError): validate_resource(promoted)
    text = resource('personal_text', '课堂导入', [block('text', 'personal', '观察材料')],
                    {'source_path':'teacher input', 'source_sha256':'unknown', 'source_revision':'unknown'})
    text['original']['selected_from_preparation'] = item['original']['selected_from_preparation']
    text['original_sha256'] = digest(text['original'])
    with pytest.raises(ValueError, match='只能绑定教学图片'): validate_resource(text)


@pytest.mark.parametrize('role', ['question', 'shared_material', 'answer', 'analysis', 'rubric', 'teacher_note'])
def test_any_complete_question_alias_blocks_ordinary_saved_entry(course, role):
    facade, asset, _option = course
    option = facade.put(source_payload([asset], [image_card(asset, native_id='native-material'), source_question(asset, role)]))
    row = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    assert row['via_question'] and len(row['saved_preparation']['source_resources']) == 2
    before = snapshot(facade)
    with pytest.raises(ValueError, match='完整题'): import_image_result(facade, [], [], choose(facade, option))
    assert snapshot(facade) == before and facade.import_calls == []


def test_unreferenced_complete_question_asset_still_requires_question_entry(course):
    facade, asset, _option = course; asset = {**asset, 'purpose': '完整题原图；保留原题范围'}
    option = facade.put(source_payload([asset])); row = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    assert row['via_question']
    with pytest.raises(ValueError, match='完整题'): import_image_result(facade, [], [], choose(facade, option))


def test_old_node_only_teacher_picture_keeps_source_snapshot(course):
    facade, asset, _option = course; node = new_node('观察与提问')
    node.update(image_ids=[asset['asset_id']], student_material=False, title='教师核对原图')
    option = facade.put(source_payload([asset], nodes=[node]))
    selection = choose(facade, option); ref = selection['saved_preparation']['source_resources'][0]
    assert ref['node_id'] == node['id'] and ref['snapshot']['node'] == node
    row = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])[0]
    assert row['teacher_only'] and not row['via_question']
    item = import_image_result(facade, [], [], selection)['resources'][0]
    assert visible_blocks(item) == []


def test_target_teacher_alias_is_retained_even_for_student_source(course):
    facade, asset, option = course
    result = import_image_result(facade, [asset], [image_card(asset, 'material'), image_card(asset, 'teacher_note')], choose(facade, option))
    item = result['resources'][0]; assert visible_blocks(item) == []
    assert item['original']['evidence']['source_roles'] == ['material', 'teacher_note']


def test_target_complete_question_alias_blocks_saved_entry(course):
    facade, asset, option = course
    with pytest.raises(ValueError, match='完整题'):
        import_image_result(facade, [asset], [source_question(asset, 'question')], choose(facade, option))


@pytest.mark.parametrize('change', ['payload', 'metadata', 'roles', 'references', 'original', 'revision', 'id', 'extra', 'alias'])
def test_modified_preview_binding_is_rejected_without_writes(course, change):
    facade, asset, option = course; selection = choose(facade, option); binding = selection['saved_preparation']
    if change == 'payload': binding['payload_sha256'] = 'f'*64
    elif change == 'metadata': binding['source_asset']['source'] = '替换来源说明'
    elif change == 'roles': binding['source_roles'] = []
    elif change == 'references': binding['source_resources'] = []; binding['source_roles'] = []
    elif change == 'original': binding['source_resources'][0]['original']['title'] = '替换原卡标题'
    elif change == 'revision': binding['draft_revision'] = 'e'*64
    elif change == 'id': binding['draft_id'] = 'prep-missing'
    elif change == 'extra': binding['candidate_only'] = False
    elif change == 'alias': binding['source_asset']['asset_id'] = 'IMG-' + 'e'*64
    before = snapshot(facade)
    with pytest.raises(ValueError): import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before and facade.import_calls == []


def test_changed_full_payload_digest_is_checked_even_if_facade_reports_same_revision(course):
    facade, asset, option = course; selection = choose(facade, option)
    def changed(result):
        result['payload']['advanced']['learning_and_experiment'] = '课前新的实验安排'
        return result
    facade.load_override = changed; before = snapshot(facade)
    with pytest.raises(ValueError, match='内容已变化'): import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before


def test_source_payload_change_after_pixel_read_is_rejected_at_final_recheck(course):
    facade, asset, option = course; selection = choose(facade, option); facade.byte_reads = 0
    def changed(_count):
        def override(result):
            result['payload']['materials'] += '\n新增课堂材料'
            return result
        facade.load_override = override
    facade.after_bytes = changed; before = snapshot(facade)
    with pytest.raises(ValueError, match='内容已变化'): import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before and facade.import_calls == []


def test_payload_change_during_last_pixel_read_is_rejected_before_handoff(course):
    facade, asset, option = course; selection = choose(facade, option); facade.byte_reads = 0
    def changed(count):
        if count == 2:
            def override(result):
                result['payload']['materials'] += '\n最后读图时的修改'
                return result
            facade.load_override = override
    facade.after_bytes = changed; before = snapshot(facade)
    with pytest.raises(ValueError, match='内容已变化'): import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before


def test_sealed_same_pixels_metadata_cannot_claim_different_dimensions(course):
    facade, asset, _option = course; wrong = {**asset, 'width': asset['width']+1}
    option = facade.put(source_payload([asset], [image_card(wrong)])); before = snapshot(facade)
    with pytest.raises(ValueError, match='尺寸或格式失配'):
        saved.saved_preparation_images(facade, option['draft_id'], option['revision'])
    assert snapshot(facade) == before


def test_changed_pixels_at_preview_or_final_read_are_rejected(course):
    facade, asset, option = course; selection = choose(facade, option); before = snapshot(facade)
    original = facade.preparation_image_bytes
    for final in (False, True):
        count = 0
        def bytes_for(_asset):
            nonlocal count
            count += 1
            return picture('red')[0] if not final or count == 2 else original(_asset)
        facade.preparation_image_bytes = bytes_for
        with pytest.raises(ValueError, match='内容已经变化'): import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before


@pytest.mark.parametrize('late', [False, True])
def test_cancellation_has_no_copy_no_course_write_no_handoff(course, late):
    facade, asset, option = course; selection = choose(facade, option); event = Event()
    before = snapshot(facade)
    if late: facade.after_bytes = lambda _count: event.set()
    else: event.set()
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        import_image_result(facade, [], [], selection)
    assert snapshot(facade) == before and facade.import_calls == []


def test_readonly_catalog_cancelled_mid_preview_never_returns_rows(course):
    facade, asset, option = course; event = Event(); before = snapshot(facade)
    facade.after_bytes = lambda _count: event.set()
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        saved.saved_preparation_images(facade, option['draft_id'], option['revision'])
    assert snapshot(facade) == before


def test_catalog_pre_cancel_does_not_read_courses(course):
    facade, _asset, _option = course; event = Event(); event.set(); before = snapshot(facade)
    facade.preparation_draft_options = lambda: pytest.fail('pre-cancel should not enumerate drafts')
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        saved.saved_preparation_image_options(facade)
    assert snapshot(facade) == before


def test_duplicate_and_max_capacity_retain_original_target_metadata(course):
    facade, asset, option = course; selection = choose(facade, option)
    target = {**asset, 'caption': '已有课程图题', 'source': '已有来源备注', 'purpose': '已有用途'}
    first = import_image_result(facade, [target], [], selection)
    assert first['image_assets'] == [target] and first['resources'][0]['original']['evidence']['image_asset'] == target
    assert first['resources'][0]['original']['selected_from_preparation']['source_asset'] == asset
    current = [target] + [{**asset, 'asset_id': 'IMG-'+f'{i:064x}', 'sha256': f'{i:064x}'} for i in range(MAX_IMAGES-1)]
    before = deepcopy((current, first['resources'])); receipt = snapshot(facade)
    repeated = import_image_result(facade, current, first['resources'], selection)
    assert repeated['duplicate'] and repeated['resources'] == [] and repeated['image_assets'] == current
    assert (current, first['resources']) == before and snapshot(facade) == receipt
    full = [{**asset, 'asset_id': 'IMG-'+f'{i:064x}', 'sha256': f'{i:064x}'} for i in range(MAX_IMAGES)]
    with pytest.raises(ValueError): import_image_result(facade, full, [], selection)
    assert snapshot(facade) == receipt and facade.import_calls == []


def test_saved_binding_survives_actual_draft_save_restart_and_reuse_chain(course):
    facade, asset, option = course; source_record = facade.state.snapshot()['drafts']['prep-source']
    result = import_image_result(facade, [], [], choose(facade, option)); item = result['resources'][0]
    target_option = facade.put(source_payload(result['image_assets'], [item]), 'prep-target')
    fresh = SavedFacade(facade.root)
    reopened = fresh.service.load(target_option['draft_id'], target_option['revision'])['payload']
    assert reopened['lesson_design']['composition']['resources'][0] == item
    assert validate_resource(item)['original']['selected_from_preparation']['draft_id'] == 'prep-source'
    before = snapshot(fresh)
    second = import_image_result(fresh, [], [], choose(fresh, target_option))['resources'][0]
    assert second['original']['selected_from_preparation']['draft_id'] == 'prep-target'
    assert second['original']['selected_from_preparation']['source_resources'][0]['original'] == item['original']
    assert snapshot(fresh) == before and fresh.state.snapshot()['drafts']['prep-source'] == source_record


def test_invalid_dangling_alias_and_non_picture_courses_are_not_offered(course):
    facade, asset, _option = course
    facade.put(_payload(), 'prep-no-pictures')
    # A syntactically sealed textbook card may refer to a missing local pixel ID.
    row = resource('textbook_image', '缺失原图引用', [block('image', 'material', 'IMG-'+'f'*64)],
                   {'source_path':'synthetic.pdf', 'source_sha256':'a'*64, 'source_revision':'b'*64,
                    'pdf_pages':[1], 'printed_pages':'unknown'})
    bad_option = facade.put(source_payload([asset], [row]), 'prep-dangling')
    before = snapshot(facade)
    options = saved.saved_preparation_image_options(facade)
    assert [o['draft_id'] for o in options] == ['prep-source']
    with pytest.raises(ValueError, match='引用不完整'):
        saved.saved_preparation_images(facade, bad_option['draft_id'], bad_option['revision'])
    assert snapshot(facade) == before
