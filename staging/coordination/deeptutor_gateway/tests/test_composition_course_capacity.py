"""An illustrated existing lesson can grow without losing its source cards."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from integrations.deeptutor_shchem_v1.desktop_composition_insertion import insert_composition_batch
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    MAX_COMPOSITION_RESOURCES, block, new_composition, resource, validate_composition,
)


def cards(count, format='16:9'):
    value = new_composition(format)
    for index in range(count):
        item = resource('personal_text', f'知识点{index + 1}', [block('text', 'personal', f'完整知识解释{index + 1}')],
                        {'source_path': '教师课堂文字', 'source_sha256': 'unknown', 'source_revision': 'unknown'})
        value['resources'].append(item)
        value['placements'][item['id']] = {'page_id': value['pages'][0],
            'x': 12, 'y': 12, 'width': 180 if format == 'A4' else 296, 'height': 156}
    return value


@pytest.mark.parametrize('format', ['A4', '16:9'])
def test_bounded_capacity_accepts_128_and_never_truncates(format):
    value = cards(MAX_COMPOSITION_RESOURCES, format)
    before = deepcopy(value)
    checked = validate_composition(value)
    assert checked == before == value
    assert len(checked['resources']) == 128


def test_129_is_rejected_without_partial_removal():
    value = cards(MAX_COMPOSITION_RESOURCES + 1)
    before = deepcopy(value)
    with pytest.raises(ValueError, match='最多128项'):
        validate_composition(value)
    assert value == before


def test_existing_actual_95_card_lesson_accepts_seven_as_one_batch():
    path = Path('E:/项目/上海高中化学等级考/outputs/2026-10-09-word-quote-materials/'
                'native-question-guided-delivery-v2/profile/desktop-state.v1.json')
    state = json.loads(path.read_text(encoding='utf-8'))
    value = state['drafts']['prep-65ca51a279c44d76b212defdedf66465']['lesson_design']['composition']
    before = deepcopy(value)
    assert len(value['resources']) == 95
    added = cards(7)
    result = insert_composition_batch(value, added, 'after', value['resources'][13]['id'])
    assert len(result['resources']) == 102
    assert value == before
    assert [r for r in result['resources'] if r['id'] in before['placements']] == before['resources']
    assert all(result['placements'][key] == frame for key, frame in before['placements'].items())
    assert result.get('click_reveals', []) == before.get('click_reveals', [])


def test_larger_capacity_keeps_resource_roles_and_160_page_boundary():
    value = cards(128)
    broken = deepcopy(value)
    broken['resources'][-1]['blocks'][0]['role'] = 'answer'
    with pytest.raises(ValueError):
        validate_composition(broken)
    broken = deepcopy(value)
    broken['pages'] = ['CP-' + uuid4().hex for _ in range(161)]
    with pytest.raises(ValueError, match='页面标识'):
        validate_composition(broken)
