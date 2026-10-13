"""Teacher-selected display limits for complete, source-bound image occurrences."""
from copy import deepcopy
import math

from .desktop_lesson_composition import (
    validate_composition, validate_resource,
)

SCHEMA = 'shchem.composition-image-display.v1'
MAX_LIMIT_MM = 400
_AXES = {'max_width_mm', 'max_height_mm'}


def validate_image_display(item):
    """Validate only the optional display field, without recursive resource validation."""
    display = item['image_display']
    image_ids = {b['id'] for b in item['original']['blocks'] if b['kind'] == 'image'}
    if (not isinstance(display, dict) or set(display) != {'schema', 'limits'}
            or display['schema'] != SCHEMA or not isinstance(display['limits'], dict)
            or not display['limits'] or not set(display['limits']) <= image_ids):
        raise ValueError('图片显示设置须绑定当前资源中的确切原图块。')
    for limits in display['limits'].values():
        if (not isinstance(limits, dict) or not limits or not set(limits) <= _AXES
                or any(type(n) not in (int, float) or not 0 < n <= MAX_LIMIT_MM
                       or not math.isfinite(n) for n in limits.values())):
            raise ValueError('原图显示上限须为大于0且不超过400毫米的有限数。')
    return deepcopy(display)


def image_display_rows(item):
    """List every original image occurrence, including repeated bytes and hidden roles."""
    item = validate_resource(item)
    limits = item.get('image_display', {}).get('limits', {})
    return [{'image_block_id': b['id'], 'asset_id': b['value'],
             'source_index': b['source_index'], 'role': b['role'],
             'limits': deepcopy(limits.get(b['id'], {}))}
            for b in item['original']['blocks'] if b['kind'] == 'image']


def set_image_display(composition, resource_id, limits):
    """Change display limits on a detached work copy; an empty mapping resets them."""
    value = validate_composition(composition)
    if (not isinstance(resource_id, str) or resource_id not in value['placements']
            or not isinstance(limits, dict)):
        raise ValueError('请选择当前作品中的原图素材及显示上限。')
    item = next(r for r in value['resources'] if r['id'] == resource_id)
    if limits:
        item['image_display'] = {'schema': SCHEMA, 'limits': deepcopy(limits)}
    else:
        item.pop('image_display', None)
    return validate_composition(value)


def cap_image_size(width, height, limits=None):
    """Apply limits after ordinary font scaling and fitting, keeping the whole image."""
    limits = limits or {}
    factor = min(1.0, limits.get('max_width_mm', width)/width,
                 limits.get('max_height_mm', height)/height)
    return width*factor, height*factor


def arrange_image_resource(composition, resource_id, assets, image_data):
    """Remeasure just one selected resource, preserving every unrelated placement.

    The detached singleton measures all of the selected resource's contents,
    not just the selected pictures. Teacher-only or click-hidden student scopes
    may be empty. Other unfinished resources stay editable in their own frames;
    a whole-lesson export still performs its usual complete layout validation.
    """
    from .desktop_composition_resource_layout import arrange_resource_layout
    value = validate_composition(composition)
    if not isinstance(resource_id, str) or resource_id not in value['placements']:
        raise ValueError('请选择当前作品中的原图素材。')
    item = next(r for r in value['resources'] if r['id'] == resource_id)
    if not image_display_rows(item):
        raise ValueError('请选择含有原图的素材。')
    return arrange_resource_layout(value, resource_id, assets, image_data)
