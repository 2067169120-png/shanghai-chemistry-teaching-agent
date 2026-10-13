"""Real textbook handoffs preserve sources and the retained classroom identity."""
from copy import deepcopy
import json

import pytest

from test_section_materials_context import (
    registered_note, notes, section_fixture, asset_fixture, shelf_fixture)
from test_textbook_crop_reading import files

from integrations.deeptutor_shchem_v1.desktop_composition_sources import (
    import_knowledge_note, knowledge_selection, knowledge_textbook_options)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import FLAGS
from integrations.deeptutor_shchem_v1.desktop_section_material_handoff import prepare_section_material_request
from integrations.deeptutor_shchem_v1.desktop_section_materials import section_material_context
from integrations.deeptutor_shchem_v1.desktop_textbook_concept_cards import concept_page_selection
from integrations.deeptutor_shchem_v1.desktop_workbench.section_materials_dialog import classroom_knowledge_card
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, read_cancel_scope)


@pytest.fixture
def handoff(registered_note):
    """One existing reading-note card and another real same-section concept."""
    f = registered_note
    f.row['statement'] = ('合成条件 A：完整保留 2.0 g、状态符号与条件标签。\n'
                          '合成条件 B：原页第二段及全部适用范围也须保留。\n\n') * 8
    f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    f.anchor = f.item
    f.course = [f.anchor]
    f.assets = []
    context = section_material_context(f.facade, f.anchor, f.course, f.assets)
    f.option = next(row for row in context['knowledge_options']
                    if row.get('concept_id') == f.row['concept_id'])
    f.raw_card = import_knowledge_note(f.facade, knowledge_selection(f.option))
    f.request = {'kind': 'knowledge', 'context': context,
        'knowledge_selection': knowledge_selection(f.option),
        'knowledge_card': classroom_knowledge_card(f.raw_card),
        'knowledge_original': deepcopy(f.raw_card['original'])}
    return f


def _prepare(f, request=None, course=None):
    return prepare_section_material_request(
        f.facade, f.anchor, f.course if course is None else course, f.assets,
        f.request if request is None else request)


def _images_request(f):
    request = deepcopy(f.request)
    selection = concept_page_selection(f.option, 8)
    page = f.facade.preparation_textbook_crop_page(selection)
    bounds = {'left': 100, 'top': 100, 'right': 610, 'bottom': 630}
    preview = f.facade.preparation_textbook_crop_preview(
        selection, page['page_revision'], bounds, '合成完整知识原图', '用原图核对知识说明')
    crop = {'selection': selection, 'page_revision': page['page_revision'], 'bounds': bounds,
        'caption': '合成完整知识原图', 'purpose': '用原图核对知识说明',
        'expected_preview_revision': preview['preview_revision'],
        'expected_image_sha256': preview['image_asset']['sha256'], 'selection_confirmed': True}
    request.update(kind='textbook_images', crop_requests=[crop],
        crop_binding=knowledge_textbook_options(f.facade, request['knowledge_card'])['binding'])
    return request


def _closed(resource):
    assert all(resource[key] is expected for key, expected in FLAGS.items())


def test_knowledge_handoff_keeps_full_original_and_clears_only_working_reminders(handoff):
    f = handoff
    before = files(f.root, f.task_root)
    inputs = deepcopy((f.course, f.assets, f.request, f.raw_card))
    publication = _prepare(f)
    assert files(f.root, f.task_root) == before
    result = publication.publish()
    publication.accept()
    assert len(result['resources']) == 1 and result['image_assets'] == []
    card = result['resources'][0]
    assert card['original'] == f.raw_card['original']
    assert card['original_sha256'] == f.raw_card['original_sha256']
    assert card['original']['evidence']['pdf_pages'] == [7, 8, 10]
    assert card['original']['evidence']['source_sha256'] == f.row['source_sha256']
    assert [row['value'] for row in card['blocks'] if row['role'] == 'knowledge'] == [f.row['statement']]
    reminders = [row for row in card['original']['blocks'] if row['role'] == 'teacher_note']
    assert reminders and all(row['value'] for row in reminders)
    assert all(row['value'] == '' for row in card['blocks'] if row['role'] == 'teacher_note')
    _closed(card)
    assert (f.course, f.assets, f.request, f.raw_card) == inputs
    assert files(f.root, f.task_root) == before and not f.task_root.exists()
    with pytest.raises(ValueError):
        publication.publish()


def test_duplicate_knowledge_keeps_teacher_edits_and_never_overwrites_classroom(handoff):
    f = handoff
    retained = import_knowledge_note(f.facade, f.request['knowledge_selection'])
    retained['title'] = '教师已编辑的课堂知识标题'
    retained['blocks'][0]['value'] = '教师保留的工作讲解，包含完整条件。'
    next(row for row in retained['blocks'] if row['role'] == 'teacher_note')['value'] = '教师自己的提醒'
    existing = [*f.course, retained]
    before = deepcopy(existing)
    disk = files(f.root, f.task_root)
    publication = _prepare(f, course=existing)
    result = publication.publish()
    publication.accept()
    assert result == {'resources': [], 'image_assets': []}
    assert existing == before and files(f.root, f.task_root) == disk


@pytest.mark.parametrize('stage', ['prepare', 'publish'])
@pytest.mark.parametrize('source', ['knowledge_record', 'original_pdf'])
def test_selected_source_change_rejects_prepare_and_publish_without_classroom_writes(handoff, stage, source):
    f = handoff
    publication = _prepare(f) if stage == 'publish' else None
    if source == 'knowledge_record':
        f.row['statement'] += '来自新版本的真实改动'
        f.catalog.write_text(json.dumps(f.row), encoding='utf8')
    else:
        f.book.write_bytes(f.book.read_bytes() + b'\n% changed original fixture\n')
    before = files(f.root, f.task_root)
    inputs = deepcopy((f.course, f.assets, f.request))
    with pytest.raises(ValueError):
        publication.publish() if publication is not None else _prepare(f)
    assert (f.course, f.assets, f.request) == inputs
    assert files(f.root, f.task_root) == before and not f.task_root.exists()


def test_cancel_and_late_stale_guard_leave_source_and_classroom_untouched(handoff):
    f = handoff
    before = files(f.root, f.task_root)
    inputs = deepcopy((f.course, f.assets, f.request))
    cancelled = TaskCancellationEvent()
    cancelled.set()
    with pytest.raises(ReadCancelled), read_cancel_scope(cancelled):
        _prepare(f)
    publication = _prepare(f)
    calls = []
    def guard():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('课堂插入位置已变化')
    with pytest.raises(ValueError, match='插入位置'):
        publication.publish(guard)
    assert len(calls) == 2
    assert (f.course, f.assets, f.request) == inputs
    assert files(f.root, f.task_root) == before and not f.task_root.exists()


@pytest.mark.parametrize('already_in_course', [False, True], ids=['new-knowledge-and-image', 'retained-knowledge'])
def test_real_crop_binds_actual_retained_knowledge_identity_and_publishes_one_batch(handoff, already_in_course):
    f = handoff
    request = _images_request(f)
    existing = list(f.course)
    retained = None
    if already_in_course:
        retained = import_knowledge_note(f.facade, request['knowledge_selection'])
        retained['title'] = '教师已改的知识标题'
        retained['blocks'][0]['value'] = '继续保留的教师工作副本'
        existing.append(retained)
        assert retained['id'] != request['knowledge_card']['id']
        assert retained['original_sha256'] != request['knowledge_card']['original_sha256']
    inputs = deepcopy((existing, f.assets, request))
    original_files = files(f.root)
    cache_before = files(f.task_root)
    publication = _prepare(f, request, existing)
    assert files(f.task_root) == cache_before
    result = publication.publish()
    publication.accept()
    kinds = [row['original']['kind'] for row in result['resources']]
    assert kinds == (['textbook_image'] if already_in_course else ['knowledge_note', 'textbook_image'])
    card = retained if already_in_course else result['resources'][0]
    image = result['resources'][-1]
    binding = image['original']['selected_from_knowledge']
    assert binding['resource_id'] == card['id']
    assert binding['original_sha256'] == card['original_sha256']
    assert binding['record_id'] == card['original']['evidence']['record_id']
    if already_in_course:
        assert binding['resource_id'] != request['knowledge_card']['id']
        assert binding['original_sha256'] != request['knowledge_card']['original_sha256']
    verified = f.facade.preparation_textbook_crop_original(image, result['image_assets'])
    assert verified['crop_pixels_verified'] is True
    assert verified['source']['source_sha256'] == f.row['source_sha256']
    assert verified['source']['pdf_bytes'] == f.book.read_bytes()
    assert verified['source']['pdf_pages'] == [8]
    assert len(result['image_assets']) == 1
    for row in result['resources']:
        _closed(row)
    assert (existing, f.assets, request) == inputs and files(f.root) == original_files


@pytest.mark.parametrize('case', ['missing_crop_requests', 'missing_crop_binding', 'empty_crop_requests',
                                'wrong_binding', 'incomplete_crop', 'missing_knowledge_original'])
def test_empty_mismatched_and_incomplete_requests_raise_value_error(handoff, case):
    f = handoff
    request = _images_request(f)
    if case == 'missing_crop_requests': request.pop('crop_requests')
    elif case == 'missing_crop_binding': request.pop('crop_binding')
    elif case == 'empty_crop_requests': request['crop_requests'] = []
    elif case == 'wrong_binding': request['crop_binding'] = 'f' * 64
    elif case == 'incomplete_crop': request['crop_requests'][0].pop('expected_image_sha256')
    else: request.pop('knowledge_original')
    before = files(f.root, f.task_root)
    inputs = deepcopy((f.course, f.assets, request))
    with pytest.raises(ValueError):
        _prepare(f, request).publish()
    assert (f.course, f.assets, request) == inputs
    assert files(f.root, f.task_root) == before and not f.task_root.exists()
