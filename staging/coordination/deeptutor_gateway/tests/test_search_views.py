from copy import deepcopy
import pytest
from integrations.deeptutor_shchem_v1.desktop_search_views import (
    SearchViewStore, SearchViewError, normalize_conditions, STATE_KEY)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


def conditions():
    return {'lane': 'word_native', 'query': '平衡', 'filters': {'knowledge': ['K09'], 'exam': ['second_mock']},
            'curriculum': {}, 'curriculum_label': ''}


def test_roundtrip_keeps_real_basket_and_other_state(tmp_path):
    state = DesktopStateStore(tmp_path)
    state._update(lambda s: s.update(marker={'unchanged': True}))
    before = deepcopy(state.snapshot())
    identity = SearchViewStore(state).save('二模平衡', conditions())
    other = SearchViewStore(DesktopStateStore(tmp_path))
    assert other.list() == [{'id': identity, 'name': '二模平衡', 'conditions': conditions()}]
    after = state.snapshot()
    assert after['basket'] == before['basket'] and after['drafts'] == before['drafts']
    assert after['marker'] == before['marker']


def test_duplicate_name_requires_explicit_replace(tmp_path):
    store = SearchViewStore(DesktopStateStore(tmp_path))
    identity = store.save('方案', conditions())
    with pytest.raises(SearchViewError): store.save('方案', conditions())
    changed = {**conditions(), 'query': '温度'}
    assert store.save('方案', changed, replace_id=identity) == identity
    assert len(store.list()) == 1 and store.list()[0]['conditions']['query'] == '温度'


def test_removed_view_cannot_be_silently_recreated_as_replace(tmp_path):
    store = SearchViewStore(DesktopStateStore(tmp_path))
    identity = store.save('方案', conditions()); store.remove(identity)
    with pytest.raises(SearchViewError): store.save('方案', conditions(), replace_id=identity)
    assert store.list() == []


def test_limit_is_explicit_not_silent_eviction(tmp_path):
    store = SearchViewStore(DesktopStateStore(tmp_path))
    for i in range(30): store.save(str(i), conditions())
    before = store.list()
    with pytest.raises(SearchViewError): store.save('overflow', conditions())
    assert store.list() == before


@pytest.mark.parametrize('bad', [None, {'lane': 'invented'}, {**conditions(), 'query': 8},
    {**conditions(), 'filters': {'knowledge': 'K09'}}, {**conditions(), 'curriculum': {'secret': 'value'}}])
def test_invalid_input_does_not_write(tmp_path, bad):
    state = DesktopStateStore(tmp_path); before = state.snapshot()
    with pytest.raises(SearchViewError): SearchViewStore(state).save('无效', bad)
    assert state.snapshot() == before


def test_condition_normalization_never_stores_question_payload():
    source = {**conditions(), 'answers': ['private'], 'image_bytes': b'private', 'results': ['q1']}
    result = normalize_conditions(source)
    assert set(result) == {'lane', 'query', 'filters', 'curriculum', 'curriculum_label'}
    assert 'private' not in str(result)


def test_corrupt_library_not_overwritten(tmp_path):
    state = DesktopStateStore(tmp_path)
    state._update(lambda s: s.update({STATE_KEY: {'schema_version': 99, 'items': []}}))
    before = state.snapshot()
    with pytest.raises(SearchViewError): SearchViewStore(state).save('新方案', conditions())
    assert state.snapshot() == before


def test_invalid_names(tmp_path):
    store = SearchViewStore(DesktopStateStore(tmp_path))
    for name in ('', '  ', 'x'*61, None):
        with pytest.raises(SearchViewError): store.save(name, conditions())
    assert store.list() == []
