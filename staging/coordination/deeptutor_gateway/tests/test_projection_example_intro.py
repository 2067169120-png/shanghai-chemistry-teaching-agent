"""Explicit example prompts follow the next whole figure in projected knowledge.

Pure canonical synthetic section snapshots; no real materials or desktop state.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1 import desktop_composition_render as render
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import (
    FLAGS, FORMATS, add_resource, block, digest, new_composition, resource, validate_resource)
from integrations.deeptutor_shchem_v1.desktop_preparation_images import image_info, normalize_image_assets
from integrations.deeptutor_shchem_v1.desktop_composition_rich_render import flatten_elements


@pytest.fixture(autouse=True)
def forbidden_complete_constructors(monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow
    def forbidden(*_args, **_kwargs):
        raise AssertionError('No complete window, facade, state or recovery constructor')
    for cls in (DesktopWorkbenchFacade, DesktopStateStore, PreparationRecoveryStore, TeacherWorkbenchWindow):
        monkeypatch.setattr(cls, '__init__', forbidden)


@pytest.fixture(scope='module')
def qt_app():
    from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font
    app = QApplication.instance() or QApplication([])
    install_ui_font(app)
    return app


def _picture(size, color):
    stream = BytesIO(); Image.new('RGB', size, color).save(stream, 'PNG')
    raw = stream.getvalue(); checksum = sha256(raw).hexdigest()
    asset = normalize_image_assets([dict(asset_id='IMG-'+checksum, sha256=checksum,
        caption='合成图', source='synthetic fixture', purpose='完整图片', **image_info(raw))])[0]
    return asset, raw


def fixture_section(intro='例如：', *, next_size=(600, 160), inline_intro=False, inline_next=False,
                    only_picture_next=False, native_next_size=None, only_picture_first=False, rich_intro=False):
    first, first_raw = _picture((600, 160), '#17638a')
    second, second_raw = _picture(next_size, '#886237')
    assets = [first, second]; data = {first['asset_id']: first_raw, second['asset_id']: second_raw}
    source = dict(schema='shchem.handout-knowledge-source.v1', batch_id='SYNTHETIC',
        source_id='SYNTHETIC', source_name='合成完整栏目.docx', source_sha256='a'*64,
        source_role='handout', source_path='synthetic-source.docx', source_revision='b'*64)
    rows, native = [], []
    ids = {}
    marker = '【待查看原文：图片或图形】'

    def append(index, text, images=(), inline=False, native_size=None):
        text_row = block('text', 'knowledge', text, index)
        rows.append(text_row); ids[index] = text_row['id']
        refs, presentations = [], []
        for position, asset in enumerate(images, 1):
            image = block('image', 'knowledge', asset['asset_id'], index)
            rows.append(image); ids['image'+str(index)] = image['id']
            refs.append(dict(block_index=index, position=position, source_asset_id='native-'+str(index),
                source_sha256=source['source_sha256'], original_sha256=asset['sha256'],
                original_mime_type=asset['content_type'], asset_id=asset['asset_id'],
                preview_sha256=asset['sha256'], derived_preview=False, renderer_revision=None,
                source_role='handout', source_branch='direct', status='included',
                container_xml_sha256='c'*64, width_mm=(native_size or (12, 3.2))[0] if inline else 'unknown',
                height_mm=(native_size or (12, 3.2))[1] if inline else 'unknown'))
            if inline:
                presentations.append(dict(image_block_id=image['id'], placeholder=marker,
                    width_mm=(native_size or (12, 3.2))[0], height_mm=(native_size or (12, 3.2))[1]))
        if inline:
            text_row['presentation'] = dict(schema='shchem.native-word-paragraph.v1', images=presentations)
        native.append(dict(index=index, text=text, images=refs))

    append(37, '前图说明。')
    if only_picture_first:
        append(38, marker, [first], inline=True, native_size=(600/render.PX_MM, 160/render.PX_MM))
    else:
        append(38, '', [first])
    if inline_intro:
        small, raw = _picture((30, 8), '#337756')
        assets.append(small); data[small['asset_id']] = raw
        append(39, intro+marker, [small], inline=True)
    else:
        append(39, intro, inline=rich_intro)
    if only_picture_next:
        append(40, marker, [second], inline=True,
            native_size=native_next_size or (next_size[0]/render.PX_MM, next_size[1]/render.PX_MM))
    else:
        append(40, '图前'+marker+'图后条件。' if inline_next else '', [second], inline=inline_next)
    append(41, '后图图注。')
    section = dict(id='word-heading-b37', level=3, title='完整示例栏目', start=37, end=41)
    evidence = dict(source_path=source['source_path'], source_sha256=source['source_sha256'],
        source_revision=source['source_revision'], handout_source=source,
        section_snapshot=dict(schema='shchem.handout-knowledge-section.v1',
            outline_revision='d'*64, section=section, blocks=native), image_assets=deepcopy(assets),
        source_roles=dict(schema='shchem.handout-knowledge-source-roles.v2', teacher_only=False,
            document_role='handout', images=[dict(asset_id=a['asset_id'], roles=['knowledge']) for a in assets]))
    item = resource('handout_knowledge', source['source_name']+' · '+section['title'], rows, evidence)
    item['title'] = '示例观察'
    return validate_resource(item), assets, data, ids


def composition(item, format='16:9', *, paper_frames=False):
    value = new_composition(format); width, height = FORMATS[format]
    frame = dict(page_id=value['pages'][0], x=12, y=12, width=width-24, height=height-24)
    if paper_frames:
        second = 'CP-'+uuid4().hex; value['pages'].append(second)
        frame['height'] = 80
        frame['continuations'] = [dict(page_id=second, x=12, y=12, width=width-24, height=height-24)]
    return add_resource(value, item, frame)


def source_page(layout, identity):
    found = [key for key, elements in layout['pages'].items()
        if any(e.get('block_id') == identity and e['kind'] != 'space' for e in elements)]
    assert len(found) == 1
    return found[0]


def flat(layout):
    return [e for page in layout['pages'].values() for e in flatten_elements(page)]


def assert_sealed(item, value, laid, before, assets, data):
    assert not laid['errors'] and value['resources'][0] == item and item == before
    assert item['original_sha256'] == digest(item['original'])
    assert all(item[key] is val for key, val in FLAGS.items())
    assert [b['role'] for b in item['blocks']] == [b['role'] for b in item['original']['blocks']]
    assert [b['source_index'] for b in item['blocks']] == [b['source_index'] for b in item['original']['blocks']]
    for b in item['blocks']:
        if b['kind'] == 'text':
            assert any(e.get('block_id') == b['id'] for e in flat(laid))
        if b['kind'] == 'text' and 'presentation' not in b:
            assert ''.join(e.get('text', '') for e in flat(laid) if e.get('block_id') == b['id']) == b['value']
    assert sum(e['kind'] == 'image' for e in flat(laid)) == sum(b['kind'] == 'image' for b in item['blocks'])
    assert all(sha256(data[a['asset_id']]).hexdigest() == a['sha256'] for a in assets)
    for fragment in laid['fragments']:
        assert max(e['y']+e['height'] for e in fragment['elements']) <= fragment['frame']['y']+fragment['frame']['height']-render.PADDING+.001


def test_example_prompt_follows_next_image_in_projection(qt_app):
    item, assets, data, ids = fixture_section(); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    assert source_page(laid, ids[37]) == source_page(laid, ids['image38'])
    assert source_page(laid, ids[39]) == source_page(laid, ids['image40'])
    assert source_page(laid, ids[39]) != source_page(laid, ids['image38'])
    assert source_page(laid, ids[41]) == source_page(laid, ids['image40'])
    assert_sealed(item, value, laid, before, assets, data)


@pytest.mark.parametrize('intro', ['例如：', '例如:', '如：', '如:', '举例：', '举例:', '  如：  '])
def test_only_complete_bounded_example_intro_forms_move_forward(qt_app, intro):
    item, assets, data, ids = fixture_section(intro); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    assert source_page(laid, ids[39]) == source_page(laid, ids['image40'])
    assert source_page(laid, ids[39]) != source_page(laid, ids['image38'])
    assert_sealed(item, value, laid, before, assets, data)


@pytest.mark.parametrize('intro', ['图中结构：', '因此：', '例如：完整条件。', '如图所示：', '例如', '这是短说明'])
def test_regular_caption_and_unmatched_short_prose_keep_prior_picture_flow(qt_app, intro):
    item, assets, data, ids = fixture_section(intro); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    assert source_page(laid, ids[37]) == source_page(laid, ids['image38'])
    assert source_page(laid, ids[39]) == source_page(laid, ids['image38'])
    assert_sealed(item, value, laid, before, assets, data)


@pytest.mark.parametrize('format', ['A4', 'A3'])
def test_paper_authored_frames_keep_existing_caption_geometry(qt_app, format):
    item, assets, data, ids = fixture_section(); before = deepcopy(item)
    value = composition(item, format, paper_frames=True)
    laid = render.layout_composition(value, assets, data, student=False)
    assert source_page(laid, ids[39]) == source_page(laid, ids['image38'])
    assert source_page(laid, ids[39]) != source_page(laid, ids['image40'])
    pictures = [e for e in flat(laid) if e['kind'] == 'image']
    assert all(e['width'] == pytest.approx(600/render.PX_MM) for e in pictures)
    assert all(e['height'] == pytest.approx(160/render.PX_MM) for e in pictures)
    assert_sealed(item, value, laid, before, assets, data)


def test_complete_question_keeps_its_existing_flow_and_answer_scope(qt_app):
    section, assets, data, _ = fixture_section()
    rows = deepcopy(section['blocks'])
    for row in rows: row['role'] = 'question'
    answer = block('text', 'answer', '仅教师原答案', 42); rows.append(answer)
    item = resource('complete_question', '完整题来源', rows, dict(source_path='synthetic-question.docx',
        source_sha256='a'*64, source_revision='b'*64, question_key='synthetic-full',
        scope_review_sha256='c'*64, scope_status='complete_native_scope',
        hierarchy=dict.fromkeys(('paper_id','theme_big_question_id','printed_question_id','atomic_part_id'), 'unknown')))
    item['title'] = '完整题观察'; before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    intro = next(b['id'] for b in rows if b['source_index'] == 39)
    first_image = next(b['id'] for b in rows if b['source_index'] == 38 and b['kind']=='image')
    assert source_page(laid, intro) == source_page(laid, first_image)
    assert not any(e.get('block_id') == answer['id'] for e in flat(laid))
    assert value['resources'][0] == item and item == before
    assert sum(e['kind']=='image' for e in flat(laid)) == 2


@pytest.mark.parametrize('variant', ['inline_intro', 'inline_next'])
def test_inline_formula_or_next_inline_picture_never_acquires_a_standalone_keep(qt_app, variant):
    item, assets, data, ids = fixture_section(**{variant: True}); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    assert_sealed(item, value, laid, before, assets, data)
    native = next(e for e in flat(laid) if e['kind']=='image' and e.get('image_block_id') == ids['image39' if variant=='inline_intro' else 'image40'])
    assert native['width'] == pytest.approx(12*32/15)
    assert native['height'] == pytest.approx(3.2*32/15)
    assert not native.get('center_in_frame')
    if variant == 'inline_intro':
        assert source_page(laid, ids[39]) == source_page(laid, ids['image38'])


def test_too_tall_next_image_keeps_whole_original_flow_without_shrinking_for_intro(qt_app):
    item, assets, data, ids = fixture_section(next_size=(600, 1600)); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    assert source_page(laid, ids[39]) == source_page(laid, ids['image38'])
    assert source_page(laid, ids[39]) != source_page(laid, ids['image40'])
    image = next(e for e in flat(laid) if e['kind']=='image' and e.get('image_block_id') == ids['image40'])
    assert image['height'] > 125 and image['width']/image['height'] == pytest.approx(600/1600)
    assert_sealed(item, value, laid, before, assets, data)


def test_native_picture_only_rich_line_moves_with_its_example_intro(qt_app):
    item, assets, data, ids = fixture_section('例如:', only_picture_first=True, rich_intro=True,
        only_picture_next=True); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    line = next(e for page in laid['pages'].values() for e in page if e.get('block_id')==ids[40] and e['kind']=='rich_line')
    assert len(line['_parts']) == 1 and line['_parts'][0]['kind']=='image'
    assert line['_parts'][0]['height'] > 2*32/render.PX_MM
    assert source_page(laid, ids[39]) == source_page(laid, ids[40])
    assert source_page(laid, ids[39]) != source_page(laid, ids[38])
    assert_sealed(item, value, laid, before, assets, data)


def test_picture_only_small_formula_keeps_native_inline_scale(qt_app):
    item, assets, data, ids = fixture_section(only_picture_next=True, native_next_size=(12, 3.2)); before = deepcopy(item)
    value = render.arrange_composition(composition(item), assets, data)
    laid = render.layout_composition(value, assets, data)
    image = next(e for e in flat(laid) if e['kind']=='image' and e.get('image_block_id')==ids['image40'])
    assert image['height'] < 2*32/render.PX_MM
    assert image['width'] == pytest.approx(12*32/15)
    assert image['height'] == pytest.approx(3.2*32/15)
    assert_sealed(item, value, laid, before, assets, data)
