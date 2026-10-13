"""Closed, local display evidence for Paint objects and vertical picture flips.

OLE bytes are hashed as opaque source material. They are never interpreted,
opened, executed, or converted into a derived image.
"""
from hashlib import sha256
import re

from docx.oxml.ns import qn

from .desktop_preparation_images import image_info

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
V = 'urn:schemas-microsoft-com:vml'
O = 'urn:schemas-microsoft-com:office:office'
W10 = 'urn:schemas-microsoft-com:office:word'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
OBJECT_SCHEMA = 'shchem.handout-object-preview.v1'
TRANSFORM_SCHEMA = 'shchem.word-picture-transform.v1'
SHA = re.compile(r'[a-f0-9]{64}\Z')
RELATION_FIELDS = {'relationship_id', 'relationship_type', 'package_part',
                   'mime_type', 'sha256', 'bytes_count', 'is_external'}
PAINT_FIELDS = {'schema', 'prog_id', 'shape_id', 'coverage', 'binary_interpreted',
                'container_xml_sha256', 'image', 'ole', 'object_type',
                'draw_aspect', 'derived_preview'}


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and all(ord(c) >= 32 for c in value)


def validate_paint_preview(value, ref):
    if (not isinstance(value, dict) or set(value) != PAINT_FIELDS
            or value['schema'] != OBJECT_SCHEMA or value['prog_id'] != 'PBrush'
            or value['object_type'] != 'Embed' or value['draw_aspect'] != 'Content'
            or not _text(value['shape_id'], 256)
            or value['coverage'] != 'same_object_visible_preview_only'
            or value['binary_interpreted'] is not False or value['derived_preview'] is not False
            or ref['derived_preview'] is not False
            or value['container_xml_sha256'] != ref['container_xml_sha256']):
        raise ValueError('PBrush同对象原PNG显示证据不完整。')
    for key in ('image', 'ole'):
        relation = value[key]
        if (not isinstance(relation, dict) or set(relation) != RELATION_FIELDS
                or not _text(relation['relationship_id'], 256)
                or relation['relationship_type'] != R + ('/image' if key == 'image' else '/oleObject')
                or not _text(relation['package_part'], 2000) or not relation['package_part'].startswith('/word/')
                or '..' in relation['package_part'].split('/') or '\\' in relation['package_part']
                or not SHA.fullmatch(str(relation['sha256'])) or type(relation['bytes_count']) is not int
                or not 0 < relation['bytes_count'] <= 40 * 1024 * 1024 or relation['is_external'] is not False):
            raise ValueError('PBrush原图或对象的内部关系证据不完整。')
    image, ole = value['image'], value['ole']
    if (image['relationship_id'] == ole['relationship_id'] or image['package_part'] == ole['package_part']
            or image['mime_type'] != 'image/png' or image['mime_type'] != ref['original_mime_type']
            or image['sha256'] != ref['original_sha256'] or image['sha256'] != ref['preview_sha256']
            or ole['mime_type'] != 'application/vnd.openxmlformats-officedocument.oleObject'):
        raise ValueError('PBrush原PNG与对象的来源摘要不一致。')


def paint_preview(node, document, ref):
    from .offline_word_image_label_review import _xml_sha
    allowed = {f'{{{W}}}object', f'{{{V}}}shape', f'{{{V}}}imagedata', f'{{{V}}}stroke',
               f'{{{O}}}lock', f'{{{W10}}}anchorlock', f'{{{O}}}OLEObject'}
    if (node.tag != f'{{{W}}}object' or node.attrib or any(n.tag not in allowed for n in node.iter())
            or any(n.tag.rsplit('}', 1)[-1] in {'drawing', 'pict', 'object', 'AlternateContent'} for n in node.iterancestors())):
        raise ValueError('PBrush显示对象含尚不能完整绑定的结构。')
    shapes, images, oles = (node.findall(f'.//{{{ns}}}{name}') for ns, name in ((V, 'shape'), (V, 'imagedata'), (O, 'OLEObject')))
    if (len(shapes) != 1 or len(images) != 1 or len(oles) != 1
            or shapes[0].getparent() is not node or images[0].getparent() is not shapes[0]
            or oles[0].getparent() is not node):
        raise ValueError('PBrush对象必须有唯一的同容器原PNG和内嵌对象。')
    shape, image, ole = shapes[0], images[0], oles[0]
    if (shape.get('type') != '#_x0000_t75' or not _text(shape.get('id'), 256)
            or set(shape.attrib) - {'id', 'type', 'alt', 'style', f'{{{O}}}ole', 'coordsize', f'{{{O}}}preferrelative', 'filled', 'stroked'}
            or shape.get('filled') not in {None, 'f', 'false', '0'} or shape.get('stroked') not in {None, 'f', 'false', '0'}
            or any(not re.fullmatch(r'\s*(?:width|height)\s*:\s*(?:\d+(?:\.\d+)?|\.\d+)(?:pt|in|cm|mm|px)\s*', p)
                   for p in shape.get('style', '').split(';') if p.strip())
            or set(image.attrib) - {qn('r:id'), f'{{{O}}}title'} or list(image)):
        raise ValueError('PBrush原图含未知变换、效果或外链，未导入栏目。')
    for child in shape:
        if child is image:
            continue
        valid = not list(child) and (
            (child.tag == f'{{{V}}}stroke' and shape.get('stroked') in {'f', 'false', '0'} and dict(child.attrib) == {'joinstyle': 'miter'})
            or (child.tag == f'{{{O}}}lock' and dict(child.attrib) == {f'{{{V}}}ext': 'edit', 'aspectratio': 't'})
            or (child.tag == f'{{{W10}}}anchorlock' and not child.attrib))
        if not valid:
            raise ValueError('PBrush原图的形状效果尚不能完整绑定。')
    if (set(ole.attrib) - {'Type', 'ProgID', 'ShapeID', 'DrawAspect', 'ObjectID', qn('r:id')}
            or list(ole) or ole.get('Type') != 'Embed' or ole.get('DrawAspect') != 'Content'
            or ole.get('ProgID') != 'PBrush' or ole.get('ShapeID') != shape.get('id')):
        raise ValueError('PBrush显示预览与内嵌对象并非同一对象。')
    relations = {}
    for key, part_node, expected_type in (('image', image, R + '/image'), ('ole', ole, R + '/oleObject')):
        rid = part_node.get(qn('r:id')); relation = document.part.rels.get(rid)
        if relation is None or relation.is_external or relation.reltype != expected_type:
            raise ValueError('PBrush对象缺少完整的本地内嵌关系。')
        part = relation.target_part
        if key == 'image' and (part.content_type != 'image/png' or image_info(part.blob)['content_type'] != 'image/png'):
            raise ValueError('PBrush显示图片必须为有效的单帧原PNG。')
        relations[key] = {'relationship_id': rid, 'relationship_type': relation.reltype,
            'package_part': str(part.partname), 'mime_type': part.content_type,
            'sha256': sha256(part.blob).hexdigest(), 'bytes_count': len(part.blob), 'is_external': False}
    value = {'schema': OBJECT_SCHEMA, 'prog_id': 'PBrush', 'shape_id': shape.get('id'),
        'object_type': 'Embed', 'draw_aspect': 'Content', 'derived_preview': False,
        'coverage': 'same_object_visible_preview_only', 'binary_interpreted': False,
        'container_xml_sha256': _xml_sha(node), **relations}
    validate_paint_preview(value, {**ref, 'container_xml_sha256': _xml_sha(node)})
    return value


def validate_picture_transform(value, ref=None):
    if (not isinstance(value, dict) or set(value) != {'schema', 'flip_vertical', 'container_xml_sha256', 'original_sha256'}
            or value['schema'] != TRANSFORM_SCHEMA or value['flip_vertical'] is not True
            or any(not SHA.fullmatch(str(value[k])) for k in ('container_xml_sha256', 'original_sha256'))
            or (ref is not None and any(value[k] != ref[k] for k in ('container_xml_sha256', 'original_sha256')))):
        raise ValueError('原图垂直翻转必须保留已验证的逐次出现证据。')


def picture_transform(node, ref):
    """Accept only a native inline picture's explicit vertical flip."""
    transforms = [n for n in node.iter() if n.tag.rsplit('}', 1)[-1] == 'xfrm']
    expected = node.findall(f'.//{{{PIC}}}spPr/{{{A}}}xfrm')
    if (transforms != expected or len(transforms) > 1
            or any(set(n.attrib) - {'flipV'} or n.get('flipV') not in {None, '0', '1', 'false', 'true'}
                or len(n) != 2 or [c.tag for c in n] != [f'{{{A}}}off', f'{{{A}}}ext']
                or set(n[0].attrib) != {'x', 'y'} or set(n[1].attrib) != {'cx', 'cy'}
                or any(not re.fullmatch(r'-?\d+', v) for c in n for v in c.attrib.values())
                for n in transforms)):
        raise ValueError('原图包含尚未验证的变换，未导入本批栏目。')
    # VML transform styles/attributes are not projected through this path.
    for shape in node.findall(f'.//{{{V}}}shape'):
        if (any(k.rsplit('}', 1)[-1].lower() in {'rotation', 'flip', 'fliph', 'flipv', 'transform', 'skew'} for k in shape.attrib)
                or any(p.split(':', 1)[0].strip().lower() in {'rotation', 'flip', 'transform', 'skew'} for p in shape.get('style', '').split(';'))
                or shape.findall(f'.//{{{V}}}skew')):
            raise ValueError('旧式原图包含尚未验证的变换，未导入本批栏目。')
    if not transforms or transforms[0].get('flipV') not in {'1', 'true'}:
        return None
    from .offline_word_image_label_review import _xml_sha
    value = {'schema': TRANSFORM_SCHEMA, 'flip_vertical': True,
             'container_xml_sha256': _xml_sha(node), 'original_sha256': ref['original_sha256']}
    validate_picture_transform(value, {**ref, 'container_xml_sha256': _xml_sha(node)})
    return value
