"""Whole arranged batches follow physical teacher pages without rewriting old work."""
from copy import deepcopy
from uuid import uuid4

import pytest

from integrations.deeptutor_shchem_v1.desktop_composition_insertion import insert_composition_batch
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    block, new_composition, resource, set_click_reveal, set_student_arrangement,
    validate_composition)


def text_resource(title, body='原有内容', role='personal'):
    return resource('personal_text', title, [block('text', role, body)],
        {'source_path': '合成作者输入', 'source_sha256': 'unknown', 'source_revision': 'unknown'})


def frame(page, x=12, y=12, width=130, height=45):
    return {'page_id': page, 'x': x, 'y': y, 'width': width, 'height': height}


def fixture_compositions():
    old = new_composition('16:9')
    old['pages'] += ['CP-'+uuid4().hex for _ in range(4)]
    first, anchor, shared, teacher, last = [text_resource('相同标题', role=role)
        for role in ('personal', 'personal', 'personal', 'teacher_note', 'personal')]
    old['resources'] = [first, anchor, shared, teacher, last]
    # The first frame is deliberately on a later physical page than its tail.
    old['placements'] = {first['id']: frame(old['pages'][0]),
        anchor['id']: {**frame(old['pages'][2], 25, 33, 130, 60),
                       'continuations': [frame(old['pages'][1], 27, 35, 129, 61)]},
        shared['id']: frame(old['pages'][1], 165, 35, 120, 50),
        teacher['id']: frame(old['pages'][3]), last['id']: frame(old['pages'][4])}
    old = set_student_arrangement(set_click_reveal(old, first['id'], True), 2)
    fresh = new_composition('16:9')
    fresh['pages'] += ['CP-'+uuid4().hex for _ in range(4)]
    a, b = text_resource('新素材甲'), text_resource('新素材乙')
    fresh['resources'] = [a, b]
    fresh['placements'] = {a['id']: {**frame(fresh['pages'][0], 13, 15),
        'continuations': [frame(fresh['pages'][1], 14, 16), frame(fresh['pages'][2], 15, 17)]},
        b['id']: {**frame(fresh['pages'][3], 16, 18), 'continuations': [frame(fresh['pages'][4], 17, 19)]}}
    fresh = set_click_reveal(fresh, b['id'], True)
    return validate_composition(old), validate_composition(fresh), anchor['id']


@pytest.mark.parametrize('mode,resource_index,page_index', [('before', 1, 1), ('after', 2, 3), ('end', 5, 5)])
def test_full_multi_resource_batch_keeps_every_old_field_and_geometry(mode, resource_index, page_index):
    old, fresh, anchor = fixture_compositions()
    original, incoming = deepcopy(old), deepcopy(fresh)
    value = insert_composition_batch(old, fresh, mode, anchor)
    assert value['resources'] == original['resources'][:resource_index] + incoming['resources'] + original['resources'][resource_index:]
    assert value['pages'] == original['pages'][:page_index] + incoming['pages'] + original['pages'][page_index:]
    assert {key: value['placements'][key] for key in original['placements']} == original['placements']
    assert {key: value['placements'][key] for key in incoming['placements']} == incoming['placements']
    assert value['student_arrangement'] == original['student_arrangement']
    assert value['click_reveals'] == original['click_reveals'] + incoming['click_reveals']
    assert old == original and fresh == incoming
    value['resources'][0]['title'] = '调用方继续编辑'
    assert old == original and fresh == incoming


def test_shared_anchor_page_is_whole_and_teacher_only_page_is_a_valid_anchor():
    old, fresh, anchor = fixture_compositions()
    shared = old['resources'][2]['id']
    before = insert_composition_batch(old, fresh, 'before', shared)
    assert before['pages'][1:6] == fresh['pages']
    assert before['placements'][shared] == old['placements'][shared]
    assert before['placements'][anchor] == old['placements'][anchor]
    teacher = old['resources'][3]['id']
    after = insert_composition_batch(old, fresh, 'after', teacher)
    assert after['pages'][4:9] == fresh['pages']


def test_zero_batch_keeps_exact_work_copy_and_initial_end_replaces_unused_page():
    old, fresh, anchor = fixture_compositions()
    empty = new_composition('16:9')
    assert insert_composition_batch(old, empty, 'after', anchor) == old
    assert insert_composition_batch(new_composition('16:9'), fresh) == fresh


@pytest.mark.parametrize('fault', ['missing-anchor', 'mode', 'resource-collision', 'page-collision', 'format'])
def test_invalid_destination_or_batch_is_atomic(fault):
    old, fresh, anchor = fixture_compositions()
    mode = 'after'
    if fault == 'missing-anchor': anchor = 'RES-'+uuid4().hex
    elif fault == 'mode': mode = 'PDF-page-8'
    elif fault == 'resource-collision':
        old = deepcopy(fresh); anchor = old['resources'][0]['id']
    elif fault == 'page-collision':
        previous = fresh['pages'][0]; fresh['pages'][0] = old['pages'][0]
        for placement in fresh['placements'].values():
            if placement['page_id'] == previous: placement['page_id'] = fresh['pages'][0]
    else: fresh = new_composition('A4')
    before = deepcopy(old)
    with pytest.raises(ValueError): insert_composition_batch(old, fresh, mode, anchor)
    assert old == before
