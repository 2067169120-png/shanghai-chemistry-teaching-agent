"""Render one verified Word picture scene with a shared, bounded geometry.

The three source occurrences stay sealed. The instruction is retained as source
evidence; the authored result viewport and its separate anchor are the visual.
No field is updated and no source image bytes are rewritten.
"""
from copy import deepcopy
from math import ceil

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from .desktop_preparation_images import verify_image_bytes
from .reader_cancellation import check_read_cancelled
from .word_static_quote import validate_scene

PX_MM = 96 / 25.4


def transparent_image(image, effect):
    """Apply only the source's exact white-to-transparent DrawingML effect."""
    if effect is None:
        return image
    if effect != {'from': 'FFFFFF', 'to': 'FFFFFF', 'alpha': 0}:
        raise ValueError('原Word图片效果超出已验证的显示范围。')
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = image.bits()
    stride = image.bytesPerLine()
    for y in range(image.height()):
        check_read_cancelled()
        for x in range(image.width()):
            pos = y * stride + x * 4
            if pixels[pos] == pixels[pos + 1] == pixels[pos + 2] == 255:
                pixels[pos + 3] = 0
    return image


def scene_geometry(scene):
    """Source column coordinates, normalized as one complete figure."""
    frame = scene['frame']
    if scene['projection']['coordinate_policy'] != 'centered_column_cache_top_with_paragraph_anchor.v1':
        raise ValueError('原Word图片组合尚未取得完整的布局依据。')
    cache = scene['occurrences'][scene['quote']['cache_image_position'] - 1]
    cache_x = (frame['column_width_twips'] + frame['paragraph_left_twips']
               - frame['paragraph_right_twips']) * 25.4 / 1440 / 2 - cache['width_mm'] / 2
    authored = []
    for position in scene['projection']['visible_positions']:
        occurrence = scene['occurrences'][position - 1]
        anchor = occurrence['anchor']
        x = anchor['position_h']['offset_emu'] / 36000 if anchor is not None else cache_x
        y = anchor['position_v']['offset_emu'] / 36000 if anchor is not None else 0
        authored.append({'position': position, 'x': x, 'y': y,
                         'width': occurrence['width_mm'], 'height': occurrence['height_mm']})
    left = min(p['x'] for p in authored)
    top = min(p['y'] for p in authored)
    width = max(p['x'] + p['width'] for p in authored) - left
    height = max(p['y'] + p['height'] for p in authored) - top
    if not 0 < width <= 10000 or not 0 < height <= 10000:
        raise ValueError('原Word图片组合的整体画幅无效。')
    return width, height, [{**p, 'x': p['x'] - left, 'y': p['y'] - top} for p in authored]


def layout_scene(token, assets, image_data, *, width, height, font_px=15, block_id=None, image_limits=None):
    scene, images = token['scene'], token['images']
    validate_scene(scene, scene['text'], images=images)
    natural_w, natural_h, geometry = scene_geometry(scene)
    factor = min(font_px / 15, width / natural_w, height / natural_h)
    for box in geometry:
        limit = (image_limits or {}).get(images[box['position'] - 1]['image_block_id'])
        if limit:
            from .desktop_composition_image_display import cap_image_size
            capped_w, capped_h = cap_image_size(box['width'] * factor, box['height'] * factor, limit)
            factor = min(factor, capped_w / box['width'], capped_h / box['height'])
    if factor <= 0:
        raise ValueError('请为完整知识结构图保留可用画幅。')
    parts = []
    for box in geometry:
        check_read_cancelled()
        position = box['position']
        source = scene['occurrences'][position - 1]
        image_ref = images[position - 1]
        identity = image_ref['asset_id']
        if identity not in assets or identity not in image_data:
            raise ValueError('原Word图片组合缺少完整原图。')
        raw = verify_image_bytes(assets[identity], image_data[identity])
        if assets[identity]['sha256'] != source['original_sha256']:
            raise ValueError('原Word图片组合的源字节与出现位置失配。')
        image = QImage.fromData(raw)
        if image.isNull():
            raise ValueError('原Word图片组合无法完整显示。')
        image = transparent_image(image, source['transparency'])
        parts.append({'kind': 'image', 'asset_id': identity,
                      'image_block_id': image_ref['image_block_id'], 'block_id': block_id,
                      'offset_x': box['x'] * factor, 'offset_y': box['y'] * factor,
                      'width': box['width'] * factor, 'height': box['height'] * factor,
                      'source_scene_position': position, '_image': image,
                      **({'source_transparency': deepcopy(source['transparency'])}
                         if source['transparency'] is not None else {})})
    return {'kind': 'diagram', 'width': natural_w * factor, 'height': natural_h * factor,
            'block_id': block_id, '_parts': parts}


def scene_picture(scene, *, scale=4):
    """A Word-compatible local figure, using exactly the same two primitives."""
    width, height = scene['width'], scene['height']
    image = QImage(ceil(width * PX_MM * scale), ceil(height * PX_MM * scale), QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(PX_MM * scale, PX_MM * scale)
        for part in scene['_parts']:
            check_read_cancelled()
            painter.drawImage(QRectF(part['offset_x'], part['offset_y'], part['width'], part['height']), part['_image'])
    finally:
        painter.end()
    return image


def add_ppt_transparency(shape, effect):
    if effect != {'from': 'FFFFFF', 'to': 'FFFFFF', 'alpha': 0}:
        raise ValueError('原Word图片效果超出已验证的显示范围。')
    from pptx.oxml.xmlchemy import OxmlElement
    change = OxmlElement('a:clrChange')
    for tag in ('clrFrom', 'clrTo'):
        parent = OxmlElement('a:' + tag)
        color = OxmlElement('a:srgbClr'); color.set('val', 'FFFFFF')
        if tag == 'clrTo':
            alpha = OxmlElement('a:alpha'); alpha.set('val', '0'); color.append(alpha)
        parent.append(color); change.append(parent)
    shape._element.blipFill.blip.insert(0, change)
