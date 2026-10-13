"""Complete local reflow for one resource, preserving the rest of the lesson."""
from copy import deepcopy

from .desktop_lesson_composition import (
    FORMATS, new_composition, resource_frames, validate_composition,
)

_EMPTY_STUDENT = '学生版没有可见内容，请加入学生任务或材料后再生成。'
_SCOPES = ((False, False), (True, False), (True, True))


def _selected(value, resource_id):
    if not isinstance(resource_id, str) or resource_id not in value['placements']:
        raise ValueError('请选择当前作品中的素材。')
    return next(r for r in value['resources'] if r['id'] == resource_id)


def _scope_errors(singleton, assets, image_data):
    from .desktop_composition_render import layout_composition
    errors = []
    for student, reveals in _SCOPES:
        try:
            layout = layout_composition(singleton, assets, image_data,
                student=student, include_reveals=reveals)
            errors.extend(e for e in layout['errors'] if e != _EMPTY_STUDENT)
        except ValueError as exc:
            # Independent student flow can fail before returning a layout.
            errors.append(str(exc))
    return list(dict.fromkeys(errors))


def resource_layout_errors(composition, resource_id, assets, image_data):
    """Check only this resource's authored frames in all three visible scopes.

    An explicit independent student arrangement is retained while measuring its
    student scopes. Unrelated resource frames, content and images are not read.
    A legitimate empty student scope is not an overflow error.
    """
    value = validate_composition(composition)
    item = _selected(value, resource_id)
    used = {f['page_id'] for f in resource_frames(value['placements'][resource_id])}
    value['resources'] = [item]
    value['placements'] = {resource_id: value['placements'][resource_id]}
    value['pages'] = [p for p in value['pages'] if p in used]
    if 'click_reveals' in value:
        value['click_reveals'] = [r for r in value['click_reveals'] if r == resource_id]
    return _scope_errors(value, assets, image_data)


def arrange_resource_layout(composition, resource_id, assets, image_data):
    """Remeasure one complete resource at the existing full-size layout settings.

    Renewed pages are inserted before its first former page. Only pages solely
    occupied by this resource are removed; shared and intentional blank pages,
    other resources and every unrelated placement remain exactly as authored.
    """
    from .desktop_composition_render import arrange_composition
    value = validate_composition(composition)
    item = _selected(value, resource_id)
    singleton = new_composition(value['format'])
    width, height = FORMATS[value['format']]
    singleton['resources'] = [deepcopy(item)]
    singleton['placements'] = {resource_id: {'page_id': singleton['pages'][0],
        'x': 12, 'y': 12, 'width': width - 24, 'height': height - 24}}
    if resource_id in value.get('click_reveals', []):
        singleton['click_reveals'] = [resource_id]
    arranged = arrange_composition(singleton, assets, image_data)
    errors = _scope_errors(arranged, assets, image_data)
    if errors:
        raise ValueError('\n'.join(errors))
    old_pages = {f['page_id'] for f in resource_frames(value['placements'][resource_id])}
    occupied = {f['page_id'] for identity, placement in value['placements'].items()
        if identity != resource_id for f in resource_frames(placement)}
    removed = old_pages - occupied
    first_page = next(p for p in value['pages'] if p in old_pages)
    pages = []
    for page_id in value['pages']:
        if page_id == first_page:
            pages.extend(arranged['pages'])
        if page_id not in removed:
            pages.append(page_id)
    if len(pages) > 160:
        raise ValueError('完整作品超过160页，请按课时拆分。')
    value['pages'] = pages
    value['placements'][resource_id] = deepcopy(arranged['placements'][resource_id])
    return validate_composition(value)


def refresh_resource_layout_if_needed(composition, resource_id, assets, image_data):
    """Return the exact detached value if it fits; otherwise reflow only this item.

    Failed source measurement, unsafe merge pagination or output-scope overflow
    leaves the input unchanged. No content, text size or display limit is edited.
    """
    value = validate_composition(composition)
    if not resource_layout_errors(value, resource_id, assets, image_data):
        return value
    result = arrange_resource_layout(value, resource_id, assets, image_data)
    errors = resource_layout_errors(result, resource_id, assets, image_data)
    if errors:
        raise ValueError('\n'.join(errors))
    return result
