"""Full local lessons retain each source when materials cross the old limit."""
from copy import deepcopy
import pytest
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    MAX_COMPOSITION_RESOURCES, block,new_composition,resource,validate_composition,
)
from integrations.deeptutor_shchem_v1.desktop_composition_insertion import insert_composition_batch

def lesson(count, *, start=0):
    value=new_composition('16:9')
    for i in range(start, start + count):
        item=resource('personal_text',f'知识点{i}',[block('text','personal',f'完整内容{i}：保留末句')],
            {'source_path':f'合成教师输入-{i}','source_sha256':'unknown','source_revision':'unknown'})
        value['resources'].append(item)
        value['placements'][item['id']]={'page_id':value['pages'][0],'x':12,'y':12,'width':296,'height':156}
    return value

@pytest.mark.parametrize('count',sorted({80,81,96,MAX_COMPOSITION_RESOURCES}))
def test_source_units_above_old_limit_are_kept_in_order(count):
    value=lesson(count);before=deepcopy(value)
    result=validate_composition(value)
    assert result==before==value
    assert [r['blocks'][0]['value'] for r in result['resources']]==[
        f'完整内容{i}：保留末句' for i in range(count)]
    assert len({r['id'] for r in result['resources']}) == count
    assert len({r['original_sha256'] for r in result['resources']}) == count
    assert len({r['original']['evidence']['source_path'] for r in result['resources']}) == count
    assert all(r['original']['blocks']==r['blocks'] and not r['human_reviewed'] for r in result['resources'])

def test_oversized_lesson_is_rejected_without_partial_result_or_input_mutation():
    value=lesson(MAX_COMPOSITION_RESOURCES + 1);before=deepcopy(value)
    assert len({r['id'] for r in value['resources']}) == MAX_COMPOSITION_RESOURCES + 1
    assert len({r['original_sha256'] for r in value['resources']}) == MAX_COMPOSITION_RESOURCES + 1
    with pytest.raises(ValueError,match=f'最多{MAX_COMPOSITION_RESOURCES}项素材'):
        validate_composition(value)
    assert value==before


@pytest.mark.parametrize('prior_count', [MAX_COMPOSITION_RESOURCES - 2, MAX_COMPOSITION_RESOURCES - 1])
def test_full_batch_capacity_boundary_keeps_prior_and_incoming_originals(prior_count):
    prior, incoming = lesson(prior_count), lesson(2, start=prior_count)
    before, original = deepcopy(prior), deepcopy(incoming)
    all_rows = prior['resources'] + incoming['resources']
    assert len({row['id'] for row in all_rows}) == prior_count + 2
    assert len({row['original_sha256'] for row in all_rows}) == prior_count + 2
    assert len({row['original']['evidence']['source_path'] for row in all_rows}) == prior_count + 2
    assert not set(prior['pages']) & set(incoming['pages'])
    if prior_count + 2 > MAX_COMPOSITION_RESOURCES:
        with pytest.raises(ValueError, match=f'最多{MAX_COMPOSITION_RESOURCES}项素材'):
            insert_composition_batch(prior, incoming)
    else:
        result = insert_composition_batch(prior, incoming)
        assert len(result['resources']) == MAX_COMPOSITION_RESOURCES
        assert result['resources'] == before['resources'] + original['resources']
        assert result['placements'] == {**before['placements'], **original['placements']}
    assert prior == before and incoming == original
