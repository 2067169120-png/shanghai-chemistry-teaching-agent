"""Complete PKG072 source occurrences, immutable pixels and real Office output."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
import pytest
from PySide6.QtGui import QImage

from test_handout_knowledge import make_facade, request_for
from test_composition_render import app
from test_composition_image_display import word_output, word_image_hashes
from integrations.deeptutor_shchem_v1 import desktop_handout_knowledge as knowledge
from integrations.deeptutor_shchem_v1 import desktop_handout_material_handoff as handoff
from integrations.deeptutor_shchem_v1.desktop_composition_inline import display_tokens
from integrations.deeptutor_shchem_v1.desktop_handout_table_projection import source_table_projection
from integrations.deeptutor_shchem_v1.desktop_composition_rich_render import flatten_elements
from integrations.deeptutor_shchem_v1.desktop_composition_render import (
    arrange_composition, layout_composition, write_pptx)
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    add_resource, digest, new_composition, validate_resource)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourcesService

RAW = Path('E:/项目/上海高中化学等级考/sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-072/第08讲 高分子有机物（复习讲义）（解析版）.docx')
RAW_SHA = '01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'


def real_facade(root):
    if not RAW.is_file():
        pytest.skip('Exact local PKG072 source is unavailable')
    raw = RAW.read_bytes()
    assert sha256(raw).hexdigest() == RAW_SHA
    facade = make_facade(raw, root)
    facade.value.name = RAW.name
    preview = PreparationSourcesService(facade.paths.workspace_root).word_preview_bytes(raw, RAW.name)
    words = SimpleNamespace(_lock=RLock())
    def catalog(*args, **kwargs):
        native = facade._imported_word_source('BATCH', 'SOURCE')
        return {'items': []}, {RAW_SHA: (native, 'BATCH', preview, [])}
    words._catalog = catalog
    facade._word_questions = lambda: words
    return facade


def whole_request(facade):
    return request_for(facade, '知识点2 通用高分子材料', title='通用高分子材料的组成、性能与应用')


def material_request(request):
    return {'schema': handoff.SCHEMA, 'source': request['source'],
            'knowledge_requests': [request], 'image_selections': [], 'question_selections': [],
            'selection_order': [{'kind': 'knowledge', 'index': 0}]}


@pytest.fixture(scope='module')
def real_imported(tmp_path_factory):
    facade = real_facade(tmp_path_factory.mktemp('real-polymer-import'))
    result = knowledge.import_handout_knowledge(facade, [whole_request(facade)], [], [])
    return facade, result


def assert_complete(result):
    assert len(result['resources']) == 1 and len(result['image_assets']) == 21
    item = result['resources'][0]
    assert validate_resource(item) == item and item['blocks'] == item['original']['blocks']
    snapshot = item['original']['evidence']['section_snapshot']
    assert (snapshot['section']['start'], snapshot['section']['end']) == (254, 297)
    assert [b['index'] for b in snapshot['blocks']] == list(range(254, 298))
    refs = [r for b in snapshot['blocks'] for r in b['images']]
    assert len(refs) == 32 and len({r['original_sha256'] for r in refs}) == 21
    assert len([b for b in snapshot['blocks'] if 'table_rows' in b]) == 3
    assert [(b['index'], len(b['images'])) for b in snapshot['blocks'] if b['index'] in (283, 284)] == [(283, 7), (284, 7)]
    paints = [r for r in refs if 'source_object' in r]
    assert all(not r['derived_preview'] and r['renderer_revision'] is None for r in paints)
    assert [(r['source_object']['shape_id'], r['source_object']['ole']['relationship_id']) for r in paints] == [('_x0000_i1032', 'rId111'), ('_x0000_i1033', 'rId112')]
    assert len({r['asset_id'] for r in paints}) == 1
    assert len({r['container_xml_sha256'] for r in paints}) == 2
    assert len({r['source_object']['ole']['package_part'] for r in paints}) == 2
    assert all(r['source_object']['prog_id'] == 'PBrush'
        and r['source_object']['binary_interpreted'] is False
        and r['source_object']['coverage'] == 'same_object_visible_preview_only'
        and r['source_object']['derived_preview'] is False for r in paints)
    flips = [r for r in refs if 'transform' in r]
    assert [(r['block_index'], r['position']) for r in flips] == [(283, 7), (284, 7)]
    assert len({r['asset_id'] for r in flips}) == 1
    by_id = {b['id']: b for b in item['blocks']}
    tokens = []
    for b in item['blocks']:
        if b['kind'] != 'text': continue
        table = source_table_projection(item, b)
        if table is not None:
            tokens.extend(t for cell in table['cell_tokens'].values() for t in cell)
        else:
            tokens.extend(display_tokens(b, by_id))
    assert len([t for t in tokens if t['kind'] == 'image']) == 32
    assert len([t for t in tokens if 'transform' in t]) == 2
    return item, refs


def test_real_complete_unit_handoff_preserves_44_blocks_32_occurrences_21_media(tmp_path):
    before = (RAW.read_bytes(), RAW.stat().st_mtime_ns)
    facade = real_facade(tmp_path/'handoff')
    request = whole_request(facade)
    publication = handoff.prepare_handout_materials(facade, material_request(request), [], [])
    assert not facade.paths.task_root.exists()
    result = publication.publish(); publication.accept()
    item, refs = assert_complete(result)
    native = Document(BytesIO(before[0]))
    originals = {sha256(r.target_part.blob).hexdigest(): r.target_part.blob
        for r in native.part.rels.values() if not r.is_external and r.reltype.endswith('/image')}
    store = PreparationImageStore(facade.paths.task_root/'preparation-v1/images')
    direct = {r['asset_id'] for r in refs if not r['derived_preview']}
    assert all(store.load(a) == originals[a['sha256']] for a in result['image_assets'] if a['asset_id'] in direct)
    assert len(list(store.root.glob('*.image'))) == 21
    assert (RAW.read_bytes(), RAW.stat().st_mtime_ns) == before


def test_real_canvas_and_office_keep_original_media_and_two_vertical_flips(tmp_path, app):
    facade = real_facade(tmp_path/'office')
    result = knowledge.import_handout_knowledge(facade, [whole_request(facade)], [], [])
    item, refs = assert_complete(result)
    assets = result['image_assets']; store = PreparationImageStore(facade.paths.task_root/'preparation-v1/images')
    data = {a['asset_id']: store.load(a) for a in assets}
    composition = new_composition('16:9')
    composition = add_resource(composition, item, {'page_id': composition['pages'][0],
        'x': 12, 'y': 12, 'width': 296, 'height': 156})
    composition = arrange_composition(composition, assets, data)
    layout = layout_composition(composition, assets, data, student=False)
    assert not layout['errors']
    images = [e for values in layout['pages'].values() for e in flatten_elements(values) if e['kind'] == 'image']
    assert len(images) == 32
    flips = [e for e in images if 'transform' in e]
    assert len(flips) == 2
    for e in flips:
        raw = QImage.fromData(data[e['asset_id']])
        assert e['_image'] == raw.mirrored(False, True)
        assert e['_image'] != raw
    path = tmp_path/'full.pptx'; write_pptx(layout, path, data)
    with ZipFile(path) as package:
        media = {sha256(package.read(n)).hexdigest(): package.read(n) for n in package.namelist() if n.startswith('ppt/media/')}
        assert set(media) == {a['sha256'] for a in assets}
        assert all(media[a['sha256']] == data[a['asset_id']] for a in assets)
        slide_xml = [etree.fromstring(package.read(n)) for n in package.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
        assert sum(len(x.findall(f'.//{{{A}}}xfrm[@flipV="1"]')) for x in slide_xml) == 2
        assert sum(len(x.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}pic')) for x in slide_xml) == 32
    teacher, student = word_output(composition, assets, data, tmp_path/'word')
    expected = [r['preview_sha256'] for r in refs]
    for doc in (teacher, student):
        assert word_image_hashes(doc) == expected
        assert len(doc._element.findall(f'.//{{{A}}}xfrm[@flipV="1"]')) == 2
        assert len(doc.tables) == 3
        for blip in doc._element.findall('.//' + qn('a:blip')):
            pixels = doc.part.rels[blip.get(qn('r:embed'))].target_part.blob
            assert pixels == data['IMG-' + sha256(pixels).hexdigest()]


@pytest.mark.parametrize('change', ['transform_sha', 'container_sha', 'false_flip', 'presentation_only', 'source_only', 'ole_sha', 'png_sha'])
def test_real_frozen_occurrence_evidence_rejects_tampering(real_imported, change):
    facade, result = real_imported
    item = deepcopy(result['resources'][0])
    refs = [r for b in item['original']['evidence']['section_snapshot']['blocks'] for r in b['images']]
    ref = next(r for r in refs if 'transform' in r)
    if change == 'transform_sha': ref['transform']['original_sha256'] = 'f'*64
    elif change == 'container_sha': ref['transform']['container_xml_sha256'] = 'f'*64
    elif change == 'false_flip': ref['transform']['flip_vertical'] = False
    elif change == 'source_only': del ref['transform']
    elif change == 'presentation_only':
        for b in item['original']['blocks']:
            for p in b.get('presentation', {}).get('images', []): p.pop('transform', None)
    else:
        paint = next(r for r in refs if 'source_object' in r)
        paint['source_object']['ole' if change == 'ole_sha' else 'image']['sha256'] = 'invalid'
    item['blocks'] = deepcopy(item['original']['blocks']); item['original_sha256'] = digest(item['original'])
    with pytest.raises(ValueError): validate_resource(item)
