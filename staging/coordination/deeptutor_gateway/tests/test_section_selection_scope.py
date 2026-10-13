"""Explicit section selection compiles real materials with no knowledge anchor."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from test_section_material_sources import (
    empty_registered, empty_supplement, empty_personal, facade,
    shelf_fixture, section_fixture, asset_fixture, supplement_fixture)
from test_forward_section_materials import lecture_forward, forward, lecture_request
from test_textbook_reading_hints import write_packet
from test_textbook_crop_reading import files

from integrations.deeptutor_shchem_v1 import desktop_section_material_handoff as handoff
from integrations.deeptutor_shchem_v1 import desktop_section_material_sources as sources
from integrations.deeptutor_shchem_v1.desktop_section_materials import textbook_section_material_context
from integrations.deeptutor_shchem_v1.desktop_section_source_stage import source_stage_reporter
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import FLAGS, digest, validate_resource, visible_blocks


def base(context, kind, **extra):
    return dict(selection_scope='section', kind=kind, context=deepcopy(context), **extra)


def prepare(f, requests, *, resources=None, assets=None):
    return handoff.prepare_section_material_requests(f.facade, None,
        resources if resources is not None else [], assets if assets is not None else [], requests)


def crop(f, row=None):
    options = sources.section_textbook_options(f.facade, f.context)
    row = deepcopy(row or options['rows'][0])
    selected = row['selection']; page = f.facade.preparation_textbook_crop_page(selected)
    bounds = dict(left=100, top=100, right=610, bottom=630)
    preview = f.facade.preparation_textbook_crop_preview(selected, page['page_revision'], bounds,
        '本节完整原图', '保留原图完整来源')
    return base(f.context, 'textbook_images', crop_binding=options['binding'], crop_requests=[
        dict(selection=selected, page_revision=page['page_revision'], bounds=bounds,
            caption='本节完整原图', purpose='保留原图完整来源',
            expected_preview_revision=preview['preview_revision'],
            expected_image_sha256=preview['image_asset']['sha256'], selection_confirmed=True)])


def assert_no_knowledge(stage_rows):
    assert stage_rows
    for row in stage_rows:
        assert row['knowledge_checks'] == row['native_knowledge_tail'] == 0
        assert row['context_checks'] >= 1 and row['native_context_tail'] >= 1


def closed_result(result):
    assert not any(row['original']['kind']=='knowledge_note' for row in result['resources'])
    assert all('selected_from_knowledge' not in row['original'] for row in result['resources'])
    for row in result['resources']:
        assert validate_resource(row) == row and digest(row['original']) == row['original_sha256']
        assert all(row[key] is val for key, val in FLAGS.items())


def source_files(f):
    """The owned publication root can be nested in a synthetic source workspace."""
    return {key: val for key, val in files(f.root).items()
        if not Path(key).is_relative_to(f.facade.paths.task_root)}


def no_card(f):
    f.catalog.write_text('\n', encoding='utf8'); write_packet(f.root, [])
    f.context = textbook_section_material_context(f.facade, f.book_key, f.section_key, f.selection)
    assert f.context['knowledge_options'] == []
    return f


def browse(f):
    selected = [deepcopy(f.question_choice)]
    return base(f.context, 'questions', question_mode='browse', question_selections=selected,
        question_context=sources.section_question_context(f.facade, f.context),
        reference=f.facade.word_question_reference(selected, include_images=True))


def section_lecture(f, **choices):
    request = lecture_request(f, **choices)
    for key in ('knowledge_selection','knowledge_card','knowledge_original'): request.pop(key)
    request['selection_scope'] = 'section'; request['context'] = deepcopy(f.context)
    return request


@pytest.mark.parametrize('scope', [None, '', 'all', False])
def test_unknown_scope_rejected_before_any_word_port(scope):
    context = dict(entry_kind='textbook_section', status='ready', resource_id=None,
        book_key='registered:synthetic', section_filter_key='synthetic',
        section_selection=dict(mode='registered', section_key='synthetic', revision='a'*64))
    request = base(context, 'questions', question_mode='browse', question_context={},
        question_selections=[{}], reference={})
    request['selection_scope'] = scope
    def forbidden(): raise AssertionError('Invalid scope must not ask for Word')
    with pytest.raises(ValueError):
        handoff.prepare_section_material_requests(SimpleNamespace(_word_questions=forbidden), None, [], [], [request])


@pytest.mark.parametrize('present', [
    ('knowledge_selection',), ('knowledge_card',), ('knowledge_original',),
    ('knowledge_selection','knowledge_card'), ('knowledge_card','knowledge_original'),
    ('knowledge_selection','knowledge_original'),
    ('knowledge_selection','knowledge_card','knowledge_original')])
def test_section_scope_rejects_every_partial_or_full_knowledge_evidence(present):
    context = dict(entry_kind='textbook_section', status='ready', resource_id=None,
        book_key='registered:synthetic', section_filter_key='synthetic', section_selection=dict(mode='registered'))
    request = base(context, 'handout_materials', course_handout_source=None, material_request={})
    request.update(dict.fromkeys(present, {}))
    def forbidden(): raise AssertionError('Rejected evidence must not read Word')
    with pytest.raises(ValueError):
        handoff.prepare_section_material_requests(SimpleNamespace(_word_questions=forbidden), None, [], [], [request])


@pytest.mark.parametrize('kind,extra', [('knowledge', {}), ('questions', dict(question_mode='linked', linked_question={}))])
def test_section_scope_cannot_invent_knowledge_or_saved_link(kind, extra):
    context = dict(entry_kind='textbook_section', status='ready', resource_id=None,
        book_key='registered:synthetic', section_filter_key='synthetic', section_selection=dict(mode='registered'))
    def forbidden(): raise AssertionError('No linked browse without real knowledge')
    with pytest.raises(ValueError):
        handoff.prepare_section_material_requests(SimpleNamespace(_word_questions=forbidden), None, [], [], [base(context, kind, **extra)])


@pytest.mark.parametrize('fixture_name,origin', [
    ('empty_registered','textbook_catalog_asset'), ('empty_registered','section_textbook_page'),
    ('empty_supplement','section_textbook_page'), ('empty_personal','personal_textbook_page')])
def test_zero_card_crop_publishes_full_original_without_knowledge(fixture_name, origin, request, monkeypatch):
    f = request.getfixturevalue(fixture_name)
    options = sources.section_textbook_options(f.facade, f.context)
    chosen = next(row for row in options['rows'] if row['origin']==origin)
    selection = crop(f, chosen); before = source_files(f); originals = deepcopy(selection)
    def forbidden(*_a, **_kw): raise AssertionError('Section scope must not import or target knowledge')
    monkeypatch.setattr(handoff, '_checked_knowledge', forbidden)
    monkeypatch.setattr(handoff, '_classroom_target', forbidden)
    stages = []
    with source_stage_reporter(stages.append):
        publication = prepare(f, [selection]); result = publication.publish()
        publication.recheck_published(); publication.accept()
    assert [row['original']['kind'] for row in result['resources']] == ['textbook_image']
    closed_result(result); assert_no_knowledge(stages)
    assert all(row['native_read_tail']['section_textbook_options'] >= 1 for row in stages)
    assert selection == originals and source_files(f) == before
    image = result['resources'][0]
    original = f.facade.preparation_textbook_crop_original(image, result['image_assets'])
    assert original['crop_pixels_verified'] and original['source']['pdf_pages']==[chosen['pdf_page']]
    assert image['original']['evidence']['source_sha256'] == f.context['source_binding']['source_sha256']
    with pytest.raises(ValueError): publication.publish()


@pytest.mark.parametrize('change', ['binding', 'other_page', 'context_binding', 'fake_resource_entry'])
def test_section_image_request_rejects_changed_full_binding_membership_or_entry(empty_registered, change):
    f = empty_registered; selected = crop(f)
    if change == 'binding': selected['crop_binding'] = 'f'*64
    elif change == 'other_page': selected['crop_requests'][0]['selection']['visual_asset_id'] += '-outside'
    elif change == 'context_binding': selected['context']['binding_sha256'] = 'f'*64
    else:
        selected['context']['entry_kind'] = 'classroom_resource'
        selected['context']['resource_id'] = 'CR-'+ '1'*32
    before = files(f.root, f.facade.paths.task_root)
    with pytest.raises(ValueError): prepare(f, [selected])
    assert files(f.root, f.facade.paths.task_root) == before and not f.facade.paths.task_root.exists()


def test_no_card_mixed_book_handout_and_browse_preserve_order_roles_originals_and_fresh_tails(lecture_forward):
    f = no_card(lecture_forward)
    requests = [crop(f), section_lecture(f, knowledge=[f.lecture_knowledge]), browse(f)]
    before = files(f.root); inputs = deepcopy(requests); stages = []
    with source_stage_reporter(stages.append):
        publication = prepare(f, requests)
        assert files(f.root) == before and not f.task_root.exists()
        result = publication.publish(); publication.recheck_published(); publication.accept()
    assert [row['original']['kind'] for row in result['resources']] == ['textbook_image','handout_knowledge','complete_question']
    closed_result(result); assert_no_knowledge(stages)
    assert all(row['native_read_tail']['section_question_context'] >= 1 for row in stages)
    assert all(row['native_read_tail']['section_textbook_options'] >= 1 for row in stages)
    question = result['resources'][-1]
    assert sum(b['kind']=='image' for b in question['original']['blocks']) == 2
    assert any(b['role']=='answer' for b in question['original']['blocks'])
    assert all(b['role']!='answer' for b in visible_blocks(question, student=True))
    assert result['resources'][1]['original']['evidence']['section_snapshot']['section'] == f.lecture_knowledge['section']
    assert requests == inputs and not f.facade.paths.state_root.exists()
    assert {key:val for key,val in files(f.root).items() if not Path(key).is_relative_to(f.task_root)} == before


def test_second_material_failure_rejects_the_whole_zero_card_batch(lecture_forward):
    f = no_card(lecture_forward); bad = browse(f)
    bad['question_selections'][0]['revision'] = 'f'*64
    bad['reference']['selections'] = deepcopy(bad['question_selections'])
    before = files(f.root)
    with pytest.raises(ValueError): prepare(f, [crop(f), bad])
    assert files(f.root) == before and not f.task_root.exists()


def test_section_browse_full_context_cannot_be_downgraded_to_all_questions(lecture_forward):
    f = no_card(lecture_forward); selected = browse(f)
    selected['question_context']['curriculum_context'] = None
    selected['question_context']['search_query'] = ''
    before = files(f.root)
    with pytest.raises(ValueError): prepare(f, [selected])
    assert files(f.root) == before and not f.task_root.exists()


@pytest.mark.parametrize('domain', ['section_textbook_options','section_question_context'])
def test_zero_card_new_domain_fresh_native_tail_rejects_late_memory_change(lecture_forward, domain, monkeypatch):
    f = no_card(lecture_forward)
    selected = crop(f) if domain=='section_textbook_options' else browse(f)
    attribute = '_options_native' if domain=='section_textbook_options' else '_question_native'
    native = getattr(sources, attribute)
    def late(port, context):
        value = native(port, context)
        stage = getattr(port, '_section_source_stage', None)
        if stage is not None and not stage._reads_enabled:
            if domain=='section_textbook_options': value['rows'][0]['label'] += 'native late port change'
            else: value['original_title'] += 'native late port change'
        return value
    monkeypatch.setattr(sources, attribute, late)
    before = files(f.root); stages = []
    with pytest.raises(ValueError, match='精确读取结果'), source_stage_reporter(stages.append):
        prepare(f, [selected])
    assert stages[-1]['native_read_tail'][domain] >= 1
    assert stages[-1]['knowledge_checks'] == stages[-1]['native_knowledge_tail'] == 0
    assert files(f.root) == before and not f.task_root.exists()


def test_published_section_context_change_rejects_detached_edit_and_cleans_only_owned(empty_registered):
    f = empty_registered; selected = crop(f)
    publication = prepare(f, [selected]); publication.publish()
    f.asset['label'] += 'late source catalog change'; f.write_assets([f.asset])
    changed_sources = source_files(f)
    with pytest.raises(ValueError): publication.recheck_published()
    publication.discard()
    assert source_files(f) == changed_sources and not f.facade.paths.task_root.exists()
