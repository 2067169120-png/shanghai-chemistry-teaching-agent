"""Real complete QUOTE handout sections through private native service ports.

No desktop facade/state/recovery is constructed. The two registered originals
are read only; every archive copy, temporary compiler file and image is private.
These are engineering/transaction checks, not chemistry or visual approval.
"""
from copy import deepcopy
from hashlib import sha256
from importlib import import_module
from io import BytesIO
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import posixpath
import tempfile
import json
from zipfile import ZipFile

from lxml import etree
import pytest

from integrations.deeptutor_shchem_v1 import desktop_handout_knowledge as knowledge
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as handoff
from integrations.deeptutor_shchem_v1.desktop_composition_inline import NativeParagraphDisplay
from integrations.deeptutor_shchem_v1.desktop_handout_sources import handout_source_context
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import FLAGS, digest, validate_resource
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_source_quality import apply_source_quality
from integrations.deeptutor_shchem_v1.desktop_word_question_index import index_word_questions
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionService
from integrations.deeptutor_shchem_v1.reader_cancellation import (
    ReadCancelled, TaskCancellationEvent, check_read_cancelled, read_cancel_scope)

RAW = Path('E:/项目/上海高中化学等级考/sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-072')
SOURCES = {
    'original': ('第08讲 高分子有机物（复习讲义）（原卷版）.docx', 33, 46, 44,
                 'dd19914c1a0ced6c4a3f0dbfa357a31a168a9ca65d4a2c6f93aa0821a37e9527'),
    'solution': ('第08讲 高分子有机物（复习讲义）（解析版）.docx', 34, 47, 45,
                 '01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33'),
}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


class FrozenState:
    def __init__(self):
        self._value = {'drafts': {}, 'review_fixture': 'no persistent state'}

    def snapshot(self):
        return deepcopy(self._value)


@pytest.fixture(autouse=True)
def protected_runtime(monkeypatch, tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore

    def forbidden(*args, **kwargs):
        raise AssertionError('Persistent facade, state and recovery constructors are forbidden')

    for cls in (DesktopWorkbenchFacade, DesktopStateStore, PreparationRecoveryStore):
        monkeypatch.setattr(cls, '__init__', forbidden)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))


@pytest.fixture
def parser_ready():
    try:
        parser = import_module('integrations.deeptutor_shchem_v1.word_static_quote')
    except ModuleNotFoundError:
        pytest.skip('QUOTE implementation is still being installed')
    if not hasattr(NativeParagraphDisplay, 'source_scene') or not hasattr(parser, 'validate_scene'):
        pytest.skip('QUOTE composition contract is still being installed')
    return parser


@pytest.fixture(scope='module')
def originals():
    values, before = {}, {}
    for name, (filename, start, end, quote, expected) in SOURCES.items():
        path = RAW / filename
        if not path.is_file():
            pytest.skip('Exact local PKG-072 source is unavailable')
        data = path.read_bytes()
        assert sha256(data).hexdigest() == expected
        before[path] = (expected, len(data), path.stat().st_mtime_ns)
        values[name] = SimpleNamespace(raw=data, filename=filename, start=start, end=end, quote=quote)
    yield values
    for path, expected in before.items():
        data = path.read_bytes()
        assert (sha256(data).hexdigest(), len(data), path.stat().st_mtime_ns) == expected


def private_case(root, source, *, raw=None, role='handout'):
    root.mkdir(parents=True)
    archive = root / source.filename
    archive.write_bytes(source.raw if raw is None else raw)
    current = SimpleNamespace(role=role, name=source.filename, effective='SOURCE')
    reader = PreparationSourcesService(root / 'workspace')
    frozen = FrozenState()
    calls = []
    facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=root/'workspace', task_root=root/'task'),
                             _state=frozen, archive=archive, current=current)

    def original(batch, identity):
        if (batch, identity) != ('BATCH', 'SOURCE'):
            raise ValueError('Exact private archive required')
        data = archive.read_bytes()
        return SimpleNamespace(content=data, filename=current.name, role=current.role,
                               source_sha256=sha256(data).hexdigest(), effective_source_file_id=current.effective)

    def preview(batch, identity, *, read_only=False):
        assert read_only is True
        source = original(batch, identity)
        return reader.word_preview_bytes(source.content, source.filename)

    def asset(batch, identity, asset_id):
        return reader.word_asset_bytes(original(batch, identity).content, asset_id, render_metafiles=True)

    def catalog(locations=None, read_only=False):
        assert read_only is True
        assert locations == {'BATCH': {'SOURCE'}}
        calls.append(('scoped_catalog', read_only))
        source = original('BATCH', 'SOURCE')
        value = preview('BATCH', 'SOURCE', read_only=True)
        indexed = index_word_questions(value)
        rows = []
        for entry in indexed:
            row = apply_source_quality(entry, {})
            row.update(batch_id='BATCH', archive_source_id='SOURCE', source_name=current.name,
                       source_id=source.source_sha256, source_block_count=len(value['blocks']),
                       shared_material_policy='self_contained')
            rows.append(row)
        return {'items': rows}, {source.source_sha256: (source, 'BATCH', value, indexed)}

    words = SimpleNamespace(_lock=RLock(), reader=reader, _catalog=catalog, state=frozen,
                            attribute_store=SimpleNamespace(get=lambda *a, **k: None),
                            _read_attribute_catalog=lambda: {'knowledge_points': [], 'nodes': []})

    def resolve(selections, **kwargs):
        values, inventory = catalog({'BATCH': {'SOURCE'}}, read_only=kwargs.get('read_only', False))
        result = []
        for selection in selections:
            matches = [row for row in values['items'] if row['key'] == selection['key']
                       and row['revision'] == selection['revision']]
            if len(matches) != 1:
                raise ValueError('Question version changed')
            result.append({**matches[0], 'points': selection.get('points', 2)})
        return result, inventory

    words._resolve = resolve
    words.reference = lambda selections, include_images=True: WordQuestionService._compile_reference(
        words, selections, include_images=include_images)[0]
    facade._imported_word_source = original
    facade.imported_word_path = lambda batch, identity: str(archive) if (batch, identity) == ('BATCH', 'SOURCE') else None
    facade.list_imported_word_batches = lambda: [SimpleNamespace(batch_id='BATCH')]
    facade.imported_word_sources = lambda batch: [{'source_id': 'SOURCE', 'source_name': current.name,
        'source_sha256': sha256(archive.read_bytes()).hexdigest(), 'role': current.role}]
    facade.imported_word_preview, facade.imported_word_asset = preview, asset
    facade._word_questions = lambda: words
    facade.word_question_reference = words.reference
    chosen = knowledge.handout_knowledge_sources(facade)['sources'][0]
    outline = knowledge.handout_knowledge_outline(facade, chosen)
    section = next(row for row in outline['sections'] if row['title'] == '知识点1 高分子')
    assert (section['start'], section['end']) == (source.start, source.end)
    request = {'source': outline['source'], 'outline_revision': outline['outline_revision'],
               'section': deepcopy(section), 'title': '完整原栏目：高分子', 'teacher_only': False}
    outer = {'schema': handoff.SCHEMA, 'source': deepcopy(outline['source']),
             'knowledge_requests': [request], 'image_selections': [], 'question_selections': [],
             'selection_order': [{'kind': 'knowledge', 'index': 0}]}
    return SimpleNamespace(facade=facade, source=source, request=request, outer=outer,
                           preview=deepcopy(outline['preview']), calls=calls, before_state=frozen.snapshot())


def image_files(case):
    root = case.facade.paths.task_root / 'preparation-v1/images'
    return {path.name: path.read_bytes() for path in root.glob('*.image')}


def scene_row(item):
    return next(row for row in item['original']['evidence']['section_snapshot']['blocks'] if 'source_scene' in row)


def compile_case(case):
    return knowledge.import_handout_knowledge(case.facade, [case.request], [], [])


_COMPILED_QUOTE = None


@pytest.fixture
def compiled_quote(tmp_path, originals, parser_ready):
    """One native truth for pure reseal counterexamples; return a fresh copy."""
    global _COMPILED_QUOTE
    if _COMPILED_QUOTE is None:
        f = private_case(tmp_path/'compiled-truth', originals['solution'])
        _COMPILED_QUOTE = deepcopy(compile_case(f)['resources'][0])
    return deepcopy(_COMPILED_QUOTE)


@pytest.mark.parametrize('variant', ['original', 'solution'])
def test_real_complete_section_retains_14_blocks_7_occurrences_and_original_preview(tmp_path, originals, parser_ready, variant):
    f = private_case(tmp_path/variant, originals[variant])
    result = compile_case(f)
    assert len(result['resources']) == 1 and len(result['image_assets']) == 5
    item = result['resources'][0]
    assert validate_resource(item) == item
    evidence = item['original']['evidence']
    rows = evidence['section_snapshot']['blocks']
    refs = [ref for row in rows for ref in row['images']]
    assert [row['index'] for row in rows] == list(range(f.source.start, f.source.end + 1))
    assert len(rows) == 14 and len(refs) == 7 and len({r['original_sha256'] for r in refs}) == 5
    assert evidence['handout_source'] == f.request['source']
    assert evidence['source_sha256'] == sha256(f.source.raw).hexdigest()
    assert evidence['source_revision'] == f.preview['revision']
    native = scene_row(item)
    scene = native['source_scene']
    assert native['index'] == f.source.quote
    assert [r['position'] for r in scene['occurrences']] == [1, 2, 3]
    assert [r['phase'] for r in scene['occurrences']] == ['outside_field', 'instruction', 'cache']
    assert len({r['container_xml_sha256'] for r in scene['occurrences']}) == 3
    assert scene['occurrences'][1]['original_sha256'] == scene['occurrences'][2]['original_sha256']
    assert scene['occurrences'][1]['wp_extent'] != scene['occurrences'][2]['wp_extent']
    assert scene == NativeParagraphDisplay(f.source.raw).source_scene(f.source.quote)
    parser_ready.validate_scene(scene, native['text'], refs=native['images'])
    assert all(row['position'] == i for i, row in enumerate(native['images'], 1))
    assert all(item[key] is expected for key, expected in FLAGS.items())
    assert len(image_files(f)) == 5
    assert f.facade.imported_word_preview('BATCH', 'SOURCE', read_only=True) == f.preview
    assert f.facade.archive.read_bytes() == f.source.raw and f.facade._state.snapshot() == f.before_state


@pytest.mark.parametrize('variant', ['original', 'solution'])
def test_prepare_publish_stamp_and_repeat_keep_teacher_edits(tmp_path, originals, parser_ready, variant):
    f = private_case(tmp_path/variant, originals[variant])
    first = handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    second = handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    assert image_files(f) == {}
    assert first.result != second.result and handoff._stamp(first.result) == handoff._stamp(second.result)
    assert knowledge._same_section(first.result['resources'][0], second.result['resources'][0])
    second.discard()
    result = first.publish(); first.accept(); first.discard()
    assets, cards = deepcopy(result['image_assets']), deepcopy(result['resources'])
    cards[0]['title'] = '教师保留的课堂问题'
    cards[0]['blocks'][0]['value'] += '\n教师已编辑的引导。'
    assets[0]['caption'] = '教师保留的图题'
    before = deepcopy((cards, assets, f.outer)); files = image_files(f)
    repeated = handoff.prepare_handout_materials(f.facade, f.outer, assets, cards)
    assert repeated.publish() == {'resources': [], 'image_assets': assets}
    repeated.accept(); repeated.discard()
    assert (cards, assets, f.outer) == before and image_files(f) == files
    with pytest.raises(ValueError): first.publish()
    assert f.facade._state.snapshot() == f.before_state


@pytest.mark.parametrize('change', ['anchor_geometry', 'instruction_geometry', 'cache_geometry', 'run_position',
    'transparent_effect', 'field_instruction', 'field_digest', 'paragraph_digest', 'occurrence_position',
    'occurrence_phase', 'source_image_bytes', 'source_container', 'visible_projection', 'unknown_scene_key',
    'frame_column', 'frame_indent', 'frame_digest', 'frame_alignment'])
def test_resealed_scene_semantic_tampering_is_rejected(compiled_quote, change):
    item = compiled_quote
    bad = deepcopy(item); native = scene_row(bad); scene = native['source_scene']; rows = scene['occurrences']
    if change == 'anchor_geometry': rows[0]['wp_extent']['cx'] += 1
    elif change == 'instruction_geometry': rows[1]['wp_extent']['cx'] += 1
    elif change == 'cache_geometry': rows[2]['xfrm']['ext']['cy'] += 1
    elif change == 'run_position': rows[2]['run_position_half_points'] += 1
    elif change == 'transparent_effect': rows[2]['transparency']['alpha'] = 1
    elif change == 'field_instruction': scene['quote']['instruction_parts'][0]['text'] = ' DATE '
    elif change == 'field_digest': scene['quote']['field_xml_sha256'] = 'f'*64
    elif change == 'paragraph_digest': scene['paragraph_xml_sha256'] = 'f'*64
    elif change == 'occurrence_position': rows[2]['position'] = 2
    elif change == 'occurrence_phase': rows[2]['phase'] = 'instruction'
    elif change == 'source_image_bytes': rows[2]['original_sha256'] = 'f'*64
    elif change == 'source_container': rows[2]['container_xml_sha256'] = rows[1]['container_xml_sha256']
    elif change == 'visible_projection': scene['projection']['visible_positions'] = [1, 2]
    elif change == 'frame_column': scene['frame']['column_width_twips'] += 1
    elif change == 'frame_indent': scene['frame']['paragraph_left_twips'] += 1
    elif change == 'frame_digest': scene['frame']['section_xml_sha256'] = 'f'*64
    elif change == 'frame_alignment': scene['frame']['alignment'] = 'left'
    else: scene['unverified_extra'] = True
    text = next(row for row in bad['original']['blocks'] if row['kind'] == 'text' and row['source_index'] == native['index'])
    text['presentation']['scene'] = deepcopy(scene)
    bad['blocks'] = deepcopy(bad['original']['blocks']); bad['original_sha256'] = digest(bad['original'])
    with pytest.raises(ValueError): validate_resource(bad)
    assert not knowledge._same_section(item, bad)


@pytest.mark.parametrize('change', ['swap_same_bytes_occurrences', 'same_bytes_container_alias', 'missing_occurrence'])
def test_same_bytes_do_not_alias_distinct_source_occurrences(compiled_quote, change):
    item = compiled_quote; bad = deepcopy(item); native = scene_row(bad)
    assert native['images'][1]['original_sha256'] == native['images'][2]['original_sha256']
    if change == 'swap_same_bytes_occurrences': native['images'][1], native['images'][2] = native['images'][2], native['images'][1]
    elif change == 'same_bytes_container_alias': native['images'][2]['container_xml_sha256'] = native['images'][1]['container_xml_sha256']
    else: native['images'].pop()
    bad['original_sha256'] = digest(bad['original'])
    with pytest.raises(ValueError): validate_resource(bad)
    assert not knowledge._same_section(item, bad)


def changed_archive(raw, quote, mutation):
    with ZipFile(BytesIO(raw)) as archive:
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    document = etree.fromstring(entries['word/document.xml'][1])
    body = document.find(W+'body')
    paragraph = [node for node in body if node.tag in {W+'p', W+'tbl'}][quote-1]
    drawings = list(paragraph.iter(W+'drawing'))
    if mutation == 'anchor_geometry':
        offset = drawings[0].find('.//'+WP+'positionH/'+WP+'posOffset'); offset.text = str(int(offset.text)+36000)
    elif mutation == 'field_command': next(paragraph.iter(W+'instrText')).text = ' DATE '
    elif mutation == 'unknown_cache':
        run = drawings[2].getparent(); node = etree.SubElement(run, W+'t'); node.text = 'unexpected cached text'
    elif mutation == 'external_link': next(drawings[2].iter(A+'blip')).set(R+'link', 'rId9999')
    elif mutation == 'transparent_effect': next(drawings[2].iter(A+'alpha')).set('val', '50000')
    elif mutation == 'image_bytes':
        rid = next(drawings[2].iter(A+'blip')).get(R+'embed')
        rels = etree.fromstring(entries['word/_rels/document.xml.rels'][1])
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        member = posixpath.normpath('word/'+target)
        info, data = entries[member]; entries[member] = (info, data+b'private-counterexample')
    else: raise AssertionError(mutation)
    info, _ = entries['word/document.xml']
    entries['word/document.xml'] = (info, etree.tostring(document, encoding='UTF-8', xml_declaration=True, standalone=True))
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        for info, data in entries.values(): archive.writestr(info, data)
    return buffer.getvalue()


@pytest.mark.parametrize('mutation', ['anchor_geometry', 'field_command', 'unknown_cache', 'external_link', 'transparent_effect', 'image_bytes'])
def test_source_changes_between_prepare_and_publish_leave_no_images(tmp_path, originals, parser_ready, mutation):
    f = private_case(tmp_path/mutation, originals['solution'])
    publication = handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    f.facade.archive.write_bytes(changed_archive(f.source.raw, f.source.quote, mutation))
    with pytest.raises(ValueError): publication.publish()
    publication.discard()
    assert image_files(f) == {} and f.facade._state.snapshot() == f.before_state


@pytest.mark.parametrize('mutation', ['field_command', 'unknown_cache', 'external_link', 'transparent_effect'])
def test_unknown_or_external_source_shape_rejects_complete_batch_even_with_fresh_source_sha(tmp_path, originals, parser_ready, mutation):
    source = originals['solution']
    raw = changed_archive(source.raw, source.quote, mutation)
    f = private_case(tmp_path/mutation, source, raw=raw)
    assert NativeParagraphDisplay(raw).source_scene(source.quote) is None
    with pytest.raises(ValueError): handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    assert image_files(f) == {} and f.facade._state.snapshot() == f.before_state


@pytest.mark.parametrize('field', ['batch_id', 'source_id', 'source_path', 'source_sha256', 'source_revision', 'source_name', 'source_role'])
def test_mixed_archive_binding_cannot_enter_same_source_batch(tmp_path, originals, parser_ready, field):
    f = private_case(tmp_path/field, originals['solution']); request = deepcopy(f.outer)
    other = deepcopy(f.request)
    other['source'][field] = {'source_sha256': 'f'*64, 'source_revision': 'f'*64, 'source_role': 'answer'}.get(field, 'OTHER-SOURCE')
    request['knowledge_requests'].append(other); request['selection_order'].append({'kind': 'knowledge', 'index': 1})
    with pytest.raises(ValueError): handoff.prepare_handout_materials(f.facade, request, [], [])
    assert image_files(f) == {} and f.facade._state.snapshot() == f.before_state


def test_cancel_and_late_parent_guard_rollback_retain_preexisting_image(tmp_path, originals, parser_ready):
    f = private_case(tmp_path/'cancel', originals['solution'])
    event = TaskCancellationEvent(); event.set()
    with read_cancel_scope(event, check_boundaries=False):
        with pytest.raises(ReadCancelled): handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    assert image_files(f) == {}
    prepared = handoff.prepare_handout_materials(f.facade, f.outer, [], [])
    stop_before_publish = TaskCancellationEvent(); stop_before_publish.set()
    with read_cancel_scope(stop_before_publish, check_boundaries=False):
        with pytest.raises(ReadCancelled): prepared.publish()
    assert image_files(f) == {}
    asset = deepcopy(prepared.result['image_assets'][0]); pixels = prepared._payloads[asset['sha256']]
    store = PreparationImageStore(f.facade.paths.task_root/'preparation-v1/images')
    assert store.import_bytes(pixels, asset['caption'], asset['source'], asset['purpose']) == asset
    prepared.discard(); before = image_files(f)
    publication = handoff.prepare_handout_materials(f.facade, f.outer, [asset], [])
    observed = []
    def guard():
        if len(image_files(f)) > len(before):
            observed.append(True)
            raise ReadCancelled('Parent intent cancelled after private image installation')
    with pytest.raises(ReadCancelled): publication.publish(guard)
    publication.discard()
    assert observed and image_files(f) == before and f.facade._state.snapshot() == f.before_state


def test_mixed_knowledge_then_bad_image_rolls_back_staging_without_partial_publication(tmp_path, originals, parser_ready, monkeypatch):
    f = private_case(tmp_path/'mixed', originals['solution']); request = deepcopy(f.outer)
    source = next(row for row in handout_source_context(f.facade, f.request['source'])['images'] if row['block_index'] == f.source.quote)
    source['sha256'] = 'f'*64
    request['image_selections'] = [{'source': source, 'title': '源图', 'purpose': '完整对照', 'teacher_only': False}]
    request['selection_order'].append({'kind': 'image', 'index': 0})
    actual = handoff.import_handout_knowledge; compiled = []
    def native(*args, **kwargs):
        result = actual(*args, **kwargs); compiled.append(deepcopy(result)); return result
    monkeypatch.setattr(handoff, 'import_handout_knowledge', native)
    with pytest.raises(ValueError): handoff.prepare_handout_materials(f.facade, request, [], [])
    assert len(compiled) == 1 and len(compiled[0]['image_assets']) == 5
    assert image_files(f) == {} and f.facade._state.snapshot() == f.before_state


@pytest.mark.parametrize('role,teacher_only', [('handout', True), ('answer', False)])
def test_real_complete_quote_preserves_teacher_scope(tmp_path, originals, parser_ready, role, teacher_only):
    f = private_case(tmp_path/(role+str(teacher_only)), originals['solution'], role=role)
    f.request['teacher_only'] = teacher_only
    result = compile_case(f); item = result['resources'][0]
    assert all(row['role'] == 'teacher_note' for row in item['blocks'])
    assert item['original']['evidence']['source_roles']['teacher_only'] is teacher_only
    assert item['original']['evidence']['source_roles']['document_role'] == role
    assert all(item[key] is expected for key, expected in FLAGS.items())


def test_existing_six_daily_course_sources_and_teacher_edits_still_bind_exactly(tmp_path, originals, parser_ready):
    from integrations.deeptutor_shchem_v1.desktop_section_handout_sources import (
        course_handout_sources, recheck_course_handout_source)
    profile = Path('C:/Users/20671/AppData/Local/ShanghaiChem/LocalCandidates/2026-10-07-same-lecture-questions/profile')
    path = profile/'desktop-state.v1.json'
    if not path.is_file(): pytest.skip('Exact local daily course is unavailable')
    data = path.read_bytes(); before = (sha256(data).hexdigest(), len(data), path.stat().st_mtime_ns)
    state = json.loads(data.decode('utf-8-sig'))
    course = state['drafts']['prep-f13ba905c7eb46fcabd69279219b763e']
    resources = deepcopy(course['lesson_design']['composition']['resources'])
    cards = [r for r in resources if r['original']['kind'] == 'handout_knowledge']
    assert len(cards) == 6
    source = deepcopy(cards[0]['original']['evidence']['handout_source'])
    assert all(row['original']['evidence']['handout_source'] == source for row in cards)
    registered = Path(source['source_path']).resolve()
    assert profile.resolve() in registered.parents
    raw = registered.read_bytes()
    source_before = (sha256(raw).hexdigest(), len(raw), registered.stat().st_mtime_ns)
    assert raw == originals['solution'].raw and sha256(raw).hexdigest() == source['source_sha256']
    archive = tmp_path/'sealed-daily-source.docx'; archive.write_bytes(raw)
    facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=tmp_path/'workspace', task_root=tmp_path/'task'))
    def reader(batch, identity):
        if (batch, identity) != (source['batch_id'], source['source_id']):
            raise ValueError('Exact existing archive identity required')
        content = archive.read_bytes()
        return SimpleNamespace(content=content, filename=source['source_name'], role=source['source_role'],
            effective_source_file_id=source['source_id'], source_sha256=sha256(content).hexdigest())
    facade._imported_word_source = reader
    frozen = deepcopy(resources)
    result = course_handout_sources(facade, resources)
    assert result['unavailable'] == [] and len(result['sources']) == 1
    candidate = result['sources'][0]
    assert candidate['source'] == source and set(candidate['resource_ids']) == {r['id'] for r in cards}
    for row in cards:
        row['title'] += ' · 私有教师工作编辑'
        next(b for b in row['blocks'] if b['kind'] == 'text')['value'] += '\n私有讲解，不改Original。'
        validate_resource(row)
    assert recheck_course_handout_source(facade, resources, candidate) is True
    assert [r['original'] for r in resources] == [r['original'] for r in frozen]
    assert [r['original_sha256'] for r in resources] == [r['original_sha256'] for r in frozen]
    assert NativeParagraphDisplay(raw).source_scene(originals['solution'].quote) is not None
    fresh = path.read_bytes()
    assert (sha256(fresh).hexdigest(), len(fresh), path.stat().st_mtime_ns) == before
    fresh_source = registered.read_bytes()
    assert (sha256(fresh_source).hexdigest(), len(fresh_source), registered.stat().st_mtime_ns) == source_before
