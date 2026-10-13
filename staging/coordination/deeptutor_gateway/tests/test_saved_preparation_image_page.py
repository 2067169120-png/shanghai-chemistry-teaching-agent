"""All-history course search reads one metadata page and never image bytes."""
from copy import deepcopy
from threading import Event
import pytest

from test_composition_teaching_images import picture
from test_saved_preparation_images import SavedFacade, source_payload, image_card, snapshot, course
from test_composition_teaching_images import source_question
from integrations.deeptutor_shchem_v1 import desktop_saved_preparation_images as saved
from integrations.deeptutor_shchem_v1.desktop_work_organization import change_work
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, read_cancel_scope


class SearchFacade(SavedFacade):
    def __init__(self, root): super().__init__(root); self.searches = []
    def search_preparation_drafts(self, **kwargs):
        self.searches.append(deepcopy(kwargs)); return self.service.search(**kwargs)


@pytest.fixture
def history(tmp_path):
    facade = SearchFacade(tmp_path); data, metadata = picture()
    asset = facade.store.import_bytes(data, metadata['caption'], metadata['source'], metadata['purpose'])
    for index in range(52):
        payload = source_payload([asset], [image_card(asset)])
        payload['topic'] = f'第{index:02}份备课原课题'
        facade.put(payload, f'prep-{index:02}')
    facade.loads = 0; facade.byte_reads = 0
    return facade, asset


def test_page_beyond_recent_fifty_is_reachable_without_pixels_or_source_writes(history):
    facade, asset = history; before = snapshot(facade)
    assert len(facade.preparation_draft_options()) == 50
    first = saved.saved_preparation_image_page(facade)
    assert first['total'] == 52 and len(first['items']) == 25 and first['has_more']
    assert facade.loads == 25 and facade.byte_reads == 0
    last = saved.saved_preparation_image_page(facade, offset=50)
    assert [r['draft_id'] for r in last['items']] == ['prep-01', 'prep-00']
    assert [r['image_count'] for r in last['items']] == [1, 1] and not last['has_more']
    assert facade.loads == 27 and facade.byte_reads == 0
    assert facade.searches[-1] == dict(query='', offset=50, limit=25, include_hidden=False)
    assert snapshot(facade) == before and facade.import_calls == []
    assert len(saved.saved_preparation_image_options(facade)) == 50  # Old contract remains recent.


def test_display_name_original_topic_and_current_shelf_are_searched(history):
    facade, asset = history; option = facade.service.option('prep-00')
    renamed = change_work(facade.state, 'draft', 'prep-00', 'rename', title='装置的比较与评价',
        expected_source=option['revision'], expected_organization=option['organization_revision'])
    before = snapshot(facade)
    by_name = saved.saved_preparation_image_page(facade, query='装置的比较')
    by_topic = saved.saved_preparation_image_page(facade, query='第00份备课')
    assert by_name['items'] == by_topic['items'] and by_name['items'][0]['title'] == '装置的比较与评价'
    assert by_name['items'][0]['original_title'] == '第00份备课原课题'
    assert snapshot(facade) == before and facade.byte_reads == 0
    option = facade.service.option('prep-00')
    change_work(facade.state, 'draft', 'prep-00', 'archive', expected_source=option['revision'],
        expected_organization=option['organization_revision'])
    assert saved.saved_preparation_image_page(facade, query='装置的比较')['total'] == 0


def test_empty_courses_have_explicit_zero_count_without_distorting_pagination(history):
    facade, _ = history; facade.put(source_payload([]), 'prep-empty')
    page = saved.saved_preparation_image_page(facade)
    assert page['total'] == 53 and len(page['items']) == 25
    assert page['items'][0]['draft_id'] == 'prep-empty' and page['items'][0]['image_count'] == 0
    assert facade.byte_reads == 0


def test_changed_course_is_skipped_and_reported_without_loading_other_pages(history):
    facade, _ = history; load = facade.load_preparation_draft
    def changed(identity, revision):
        if identity == 'prep-51': raise ValueError('版本变化')
        return load(identity, revision)
    facade.load_preparation_draft = changed; before = snapshot(facade)
    page = saved.saved_preparation_image_page(facade, limit=3)
    assert page['total'] == 52 and page['unavailable_count'] == 1
    assert [r['draft_id'] for r in page['items']] == ['prep-50', 'prep-49']
    assert facade.loads == 2 and facade.byte_reads == 0 and snapshot(facade) == before


@pytest.mark.parametrize('values', [dict(offset=-1), dict(offset=True), dict(limit=0), dict(limit=51), dict(query=None)])
def test_bad_page_parameters_do_not_read(history, values):
    facade, _ = history
    with pytest.raises(ValueError): saved.saved_preparation_image_page(facade, **values)
    assert not facade.searches and facade.loads == 0 and facade.byte_reads == 0


@pytest.mark.parametrize('late', [False, True])
def test_cancel_before_search_or_during_metadata_load_never_returns_partial_rows(history, late):
    facade, _ = history; event = Event(); before = snapshot(facade)
    if late:
        facade.load_override = lambda result:(event.set() or result)
    else: event.set()
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        saved.saved_preparation_image_page(facade)
    assert facade.loads == (1 if late else 0) and facade.byte_reads == 0 and snapshot(facade) == before


def test_search_result_cannot_smuggle_duplicate_course_or_unbounded_page(history):
    facade, _ = history; search = facade.search_preparation_drafts
    def bad(**kwargs):
        page = search(**kwargs); page['items'][1] = deepcopy(page['items'][0]); return page
    facade.search_preparation_drafts = bad
    with pytest.raises(ValueError, match='不完整'): saved.saved_preparation_image_page(facade, limit=2)
    assert facade.loads == 1 and facade.byte_reads == 0


@pytest.mark.parametrize('role', ['material', 'teacher_note', 'answer', 'analysis', 'rubric'])
def test_lazy_catalog_retains_exact_alias_source_and_scope_of_full_reader(course, role):
    facade, asset, _ = course
    second = image_card(asset, role, 'native-second') if role in {'material', 'teacher_note'} else source_question(asset, role)
    rows = [image_card(asset, 'material', 'native-material'), second]
    option = facade.put(source_payload([asset], rows)); before = snapshot(facade)
    facade.byte_reads = 0
    catalog = saved.saved_preparation_image_catalog(facade, option['draft_id'], option['revision'])
    assert facade.byte_reads == 0 and len(catalog[0]['saved_preparation']['source_resources']) == 2
    full = saved.saved_preparation_images(facade, option['draft_id'], option['revision'])
    assert catalog == full and facade.byte_reads == 1
    assert catalog[0]['teacher_only'] is (role != 'material')
    assert catalog[0]['via_question'] is (role not in {'material', 'teacher_note'})
    assert snapshot(facade) == before


def test_lazy_catalog_still_blocks_full_question_scope_before_any_pixel_read(course):
    facade, asset, _ = course
    option = facade.put(source_payload([asset], [image_card(asset), source_question(asset, 'answer')]))
    rows = saved.saved_preparation_image_catalog(facade, option['draft_id'], option['revision'])
    assert rows[0]['via_question'] and rows[0]['teacher_only'] and facade.byte_reads == 0


def test_lazy_catalog_rechecks_complete_source_digest_and_cancel_before_return(course):
    facade, asset, option = course; original = facade.load_preparation_draft; count = 0
    def changed(identity, revision):
        nonlocal count
        count += 1; value = original(identity, revision)
        if count == 2: value['payload']['materials'] += '\n新的教师材料'
        return value
    facade.load_preparation_draft = changed; before = snapshot(facade)
    with pytest.raises(ValueError, match='内容在读取'): saved.saved_preparation_image_catalog(facade, option['draft_id'], option['revision'])
    assert facade.byte_reads == 0 and snapshot(facade) == before
    event = Event(); event.set()
    with read_cancel_scope(event, check_boundaries=False), pytest.raises(ReadCancelled):
        saved.saved_preparation_image_catalog(facade, option['draft_id'], option['revision'])
    assert count == 2 and facade.byte_reads == 0
