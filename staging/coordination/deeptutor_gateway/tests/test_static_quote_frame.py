"""Source XML frame regression for measured Word character-indent precedence."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from lxml import etree
import pytest

from integrations.deeptutor_shchem_v1.desktop_composition_inline import NativeParagraphDisplay
from integrations.deeptutor_shchem_v1 import word_static_quote as quote

RAW = Path('E:/项目/上海高中化学等级考/sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-072')
SOURCES = [
    ('第08讲 高分子有机物（复习讲义）（原卷版）.docx', 44,
     'dd19914c1a0ced6c4a3f0dbfa357a31a168a9ca65d4a2c6f93aa0821a37e9527'),
    ('第08讲 高分子有机物（复习讲义）（解析版）.docx', 45,
     '01f91a44ebbd0376c857cdbf8c5a186764a36f4a011c327a724b037c7c4f9c33'),
]
W = quote.W


@pytest.fixture(scope='module')
def actual_scenes():
    result, before = [], {}
    for name, index, expected in SOURCES:
        path = RAW / name
        if not path.is_file():
            pytest.skip('Exact PKG-072 source unavailable')
        data = path.read_bytes()
        assert sha256(data).hexdigest() == expected
        before[path] = (expected, len(data), path.stat().st_mtime_ns)
        scene = NativeParagraphDisplay(data).source_scene(index)
        assert scene is not None
        result.append(scene)
    yield result
    for path, expected in before.items():
        data = path.read_bytes()
        assert (sha256(data).hexdigest(), len(data), path.stat().st_mtime_ns) == expected


def parts(scene):
    frame = scene['frame']
    paragraph = etree.fromstring(scene['paragraph_xml'].encode())
    section = etree.fromstring(frame['section_xml'].encode())
    styles = [etree.fromstring(text.encode()) for text in frame['paragraph_style_chain_xml']]
    normal = next(node for node in styles if node.get(W + 'styleId') == 'Normal')
    return paragraph, section, styles, normal, frame['document_defaults_xml']


def derive(values):
    paragraph, section, styles, _normal, defaults = values
    return quote._frame(paragraph, section, [quote._xml(style) for style in styles], defaults)


def size(properties, value):
    node = properties.find(W + 'sz')
    if node is None:
        node = etree.SubElement(properties, W + 'sz')
    node.set(W + 'val', value)


def test_two_actual_frames_keep_native_measured_origin_and_full_reparse(actual_scenes):
    for scene in actual_scenes:
        assert scene['parser_revision'] == '20261009-static-image-quote-v2'
        assert scene['frame']['paragraph_left_twips'] == 210
        assert scene['frame']['character_indent_unit_twips'] == 210
        assert quote.validate_scene(scene, scene['text']) is scene


@pytest.mark.parametrize('case', [
    'chars_200_stale_210', 'twips_420_stale_chars_100', 'chars_zero_stale_210',
    'right_chars_100_stale_zero', 'missing_left_twips', 'normal_24_stale_210',
    'missing_normal_size', 'unmeasured_normal_size', 'unknown_normal_font',
    'normal_font_theme', 'non_default_normal', 'multiple_default_styles',
    'normal_spacing', 'normal_scale', 'grid_char_space', 'first_line_chars',
    'duplicate_normal_size', 'duplicate_style_indent',
])
def test_character_units_never_silently_use_stale_twips(actual_scenes, case):
    values = parts(actual_scenes[1]); paragraph, section, styles, normal, _defaults = values
    indent = paragraph.find(W + 'pPr/' + W + 'ind')
    if case == 'chars_200_stale_210': indent.set(W + 'leftChars', '200')
    elif case == 'twips_420_stale_chars_100': indent.set(W + 'left', '420')
    elif case == 'chars_zero_stale_210': indent.set(W + 'leftChars', '0')
    elif case == 'right_chars_100_stale_zero': indent.set(W + 'rightChars', '100'); indent.set(W + 'right', '0')
    elif case == 'missing_left_twips': indent.attrib.pop(W + 'left')
    elif case == 'normal_24_stale_210': size(normal.find(W + 'rPr'), '24')
    elif case == 'missing_normal_size': normal.find(W + 'rPr').remove(normal.find(W + 'rPr/' + W + 'sz'))
    elif case == 'unmeasured_normal_size': size(normal.find(W + 'rPr'), '25')
    elif case == 'unknown_normal_font': normal.find(W + 'rPr/' + W + 'rFonts').set(W + 'eastAsia', 'UnknownSceneFont')
    elif case == 'normal_font_theme': normal.find(W + 'rPr/' + W + 'rFonts').set(W + 'eastAsiaTheme', 'minorEastAsia')
    elif case == 'non_default_normal': normal.set(W + 'default', '0')
    elif case == 'multiple_default_styles': styles[0].set(W + 'default', '1')
    elif case in {'normal_spacing', 'normal_scale'}:
        node = etree.SubElement(normal.find(W + 'rPr'), W + ('spacing' if case == 'normal_spacing' else 'w')); node.set(W + 'val', '100')
    elif case == 'grid_char_space': section.find(W + 'docGrid').set(W + 'charSpace', '10')
    elif case == 'first_line_chars': indent.set(W + 'firstLineChars', '100')
    elif case == 'duplicate_normal_size':
        node = etree.SubElement(normal.find(W + 'rPr'), W + 'sz'); node.set(W + 'val', '24')
    elif case == 'duplicate_style_indent': styles[0].find(W + 'pPr').append(deepcopy(styles[0].find(W + 'pPr/' + W + 'ind')))
    with pytest.raises(ValueError): derive(values)


@pytest.mark.parametrize('case,left,right,unit', [
    ('two_chars', 420, 0, 210), ('normal_24', 240, 0, 240),
    ('mark_24', 210, 0, 210), ('runs_24', 210, 0, 210),
    ('twips_only', 420, 0, None), ('zero_chars', 0, 0, 210),
    ('right_chars', 210, 210, 210),
])
def test_only_measured_document_unit_supplies_character_indent(actual_scenes, case, left, right, unit):
    values = parts(actual_scenes[1]); paragraph, _section, _styles, normal, _defaults = values
    indent = paragraph.find(W + 'pPr/' + W + 'ind')
    indent.set(W + 'left', str(left))
    if case == 'two_chars': indent.set(W + 'leftChars', '200')
    elif case == 'normal_24': size(normal.find(W + 'rPr'), '24')
    elif case == 'mark_24': size(paragraph.find(W + 'pPr/' + W + 'rPr'), '24')
    elif case == 'runs_24':
        for run in paragraph.findall(W + 'r'):
            properties = run.find(W + 'rPr')
            if properties is None: properties = etree.SubElement(run, W + 'rPr')
            size(properties, '24')
    elif case == 'twips_only': indent.attrib.pop(W + 'leftChars')
    elif case == 'zero_chars': indent.set(W + 'leftChars', '0')
    elif case == 'right_chars': indent.set(W + 'right', str(right)); indent.set(W + 'rightChars', '100')
    frame = derive(values)
    assert (frame['paragraph_left_twips'], frame['paragraph_right_twips']) == (left, right)
    assert frame.get('character_indent_unit_twips') == unit


def test_resealed_character_unit_drift_is_rejected(actual_scenes):
    scene = deepcopy(actual_scenes[1]); scene['frame']['character_indent_unit_twips'] = 240
    with pytest.raises(ValueError): quote.validate_scene(scene, scene['text'])
