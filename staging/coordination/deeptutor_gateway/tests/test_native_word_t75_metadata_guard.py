"""Standard t75 metadata binds cached pixels without evaluating formulas."""
from copy import deepcopy
from io import BytesIO

import pytest
from docx import Document
from docx.oxml import parse_xml
from lxml import etree

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import _body_blocks, _word_images
from integrations.deeptutor_shchem_v1.offline_word_image_label_review import OfflineWordLabelReviewError, _objects, _q
from staging.coordination.deeptutor_gateway.tests.test_offline_word_image_label_review import _document

TEMPLATE = '''<v:shapetype xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:o="urn:schemas-microsoft-com:office:office" id="_x0000_t75"
 coordsize="21600,21600" o:spt="75" o:preferrelative="t"
 path="m@4@5l@4@11@9@11@9@5xe" filled="f" stroked="f">
 <v:stroke joinstyle="miter"/><v:formulas>
 <v:f eqn="if lineDrawn pixelLineWidth 0"/><v:f eqn="sum @0 1 0"/>
 <v:f eqn="sum 0 0 @1"/><v:f eqn="prod @2 1 2"/>
 <v:f eqn="prod @3 21600 pixelWidth"/><v:f eqn="prod @3 21600 pixelHeight"/>
 <v:f eqn="sum @0 0 1"/><v:f eqn="prod @6 1 2"/>
 <v:f eqn="prod @7 21600 pixelWidth"/><v:f eqn="sum @8 21600 0"/>
 <v:f eqn="prod @7 21600 pixelHeight"/><v:f eqn="sum @10 21600 0"/>
 </v:formulas><v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect"/>
 <o:lock v:ext="edit" aspectratio="t"/></v:shapetype>'''


def source():
    document = Document(BytesIO(_document(wmf=True, invisible_outline=True)))
    elements = list(_body_blocks(document._element.body))
    index, paragraph = next((i, e) for i, e in enumerate(elements, 1) if list(e.iter(_q('w:object'))))
    obj = next(paragraph.iter(_q('w:object')))
    template = parse_xml(TEMPLATE);obj.insert(0, template)
    shape = next(obj.iter(_q('v:shape')))
    shape.set('coordsize', '21600,21600');shape.set(_q('o:preferrelative'), 't')
    return document, index, paragraph, obj, template, shape


def test_complete_t75_preserves_source_and_binds_same_object_image_and_binary():
    doc, index, paragraph, _obj, _template, _shape = source()
    before = etree.tostring(paragraph)
    result = _objects(paragraph, doc, index, _word_images(list(_body_blocks(doc._element.body)), doc))
    assert etree.tostring(paragraph) == before
    assert len(result) == 1
    assert result[0]['coverage'] == 'same_object_visible_preview_only'
    assert result[0]['ole']['binary_interpreted'] is False
    assert result[0]['image']['relationship_id'] != result[0]['ole']['relationship_id']


@pytest.mark.parametrize('mutation', [
    'unknown_id', 'unknown_spt', 'custom_coordinates', 'custom_path',
    'unknown_formula', 'missing_formula', 'extra_formula', 'formula_attribute',
    'visible_template_fill', 'unknown_template_attribute', 'unknown_path_attribute',
    'changed_lock', 'unknown_template_child', 'duplicate_template', 'nested_template',
    'relationship_in_template', 'custom_shape_coordinates', 'rotated_shape',
    'floating_shape', 'external_image', 'mismatched_ole_shape',
    'extra_formula_on_shape', 'extra_formulas_on_object', 'extra_path_on_shape',
    'formula_nonempty_text', 'nested_shape',
])
def test_custom_or_unbound_template_still_fails_without_source_mutation(mutation):
    doc, index, paragraph, obj, template, shape = source()
    formulas = next(template.iter(_q('v:formulas')))
    path = next(template.iter(_q('v:path')));lock = next(template.iter(_q('o:lock')))
    if mutation == 'unknown_id': template.set('id', 'custom')
    elif mutation == 'unknown_spt': template.set(_q('o:spt'), '76')
    elif mutation == 'custom_coordinates': template.set('coordsize', '100,100')
    elif mutation == 'custom_path': template.set('path', 'm 0,0 l 1,1')
    elif mutation == 'unknown_formula': formulas[0].set('eqn', 'sum 5 6 7')
    elif mutation == 'missing_formula': formulas.remove(formulas[-1])
    elif mutation == 'extra_formula': formulas.append(deepcopy(formulas[-1]))
    elif mutation == 'formula_attribute': formulas[0].set('unknown', '1')
    elif mutation == 'visible_template_fill': template.set('filled', 't')
    elif mutation == 'unknown_template_attribute': template.set('unknown', '1')
    elif mutation == 'unknown_path_attribute': path.set('unknown', '1')
    elif mutation == 'changed_lock': lock.set('aspectratio', 'f')
    elif mutation == 'unknown_template_child': etree.SubElement(template, _q('v:shadow'))
    elif mutation == 'duplicate_template': obj.insert(0, deepcopy(template))
    elif mutation == 'nested_template': obj.remove(template);shape.insert(0, template)
    elif mutation == 'relationship_in_template': template.set(_q('r:id'), 'rIdExternal')
    elif mutation == 'custom_shape_coordinates': shape.set('coordsize', '1,2')
    elif mutation == 'rotated_shape': shape.set('style', 'width:40pt;height:20pt;rotation:90')
    elif mutation == 'floating_shape': shape.set('style', 'width:40pt;height:20pt;position:absolute')
    elif mutation == 'external_image': next(shape.iter(_q('v:imagedata'))).set(_q('r:link'), 'rIdExternal')
    elif mutation == 'mismatched_ole_shape': next(obj.iter(_q('o:OLEObject'))).set('ShapeID', 'different')
    elif mutation == 'extra_formula_on_shape': shape.append(deepcopy(formulas[0]))
    elif mutation == 'extra_formulas_on_object': obj.append(deepcopy(formulas))
    elif mutation == 'extra_path_on_shape': shape.append(deepcopy(path))
    elif mutation == 'formula_nonempty_text': formulas[0].text = 'custom rendering data'
    elif mutation == 'nested_shape': obj.remove(shape); next(obj.iter(_q('o:OLEObject'))).append(shape)
    before = etree.tostring(paragraph)
    with pytest.raises(OfflineWordLabelReviewError):
        _objects(paragraph, doc, index, _word_images(list(_body_blocks(doc._element.body)), doc))
    assert etree.tostring(paragraph) == before
