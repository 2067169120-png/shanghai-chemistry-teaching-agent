"""Insert an already arranged batch without changing any existing paper geometry."""
from copy import deepcopy

from .desktop_lesson_composition import resource_frames, validate_composition


INSERTION_LABELS = (
    ('end', '作品末尾'),
    ('before', '所选素材之前'),
    ('after', '所选素材之后'),
)


def insert_composition_batch(composition, fresh, mode='end', anchor_resource_id=None):
    """Use the anchor's physical teacher pages, including all continuations.

    A shared anchor page stays whole: a new batch goes before its first page or
    after its last page. Reading order and physical page order are both updated,
    while all old resource fields and placement frames remain exactly intact.
    """
    value = validate_composition(composition)
    batch = validate_composition(fresh)
    if mode not in {key for key, _ in INSERTION_LABELS}:
        raise ValueError('请选择作品末尾、所选素材之前或所选素材之后。')
    if value['format'] != batch['format']:
        raise ValueError('作品画幅已变化，请重新选择素材。')
    identities = [row['id'] for row in value['resources']]
    resource_index, page_index = len(identities), len(value['pages'])
    if mode != 'end':
        if anchor_resource_id not in identities:
            raise ValueError('加入位置所选的素材已移除，请重新选择位置。')
        resource_index = identities.index(anchor_resource_id) + (mode == 'after')
        pages = [value['pages'].index(frame['page_id'])
                 for frame in resource_frames(value['placements'][anchor_resource_id])]
        page_index = min(pages) if mode == 'before' else max(pages) + 1
    if not batch['resources']:
        return value
    new_ids = {row['id'] for row in batch['resources']}
    if new_ids.intersection(identities):
        raise ValueError('本批素材已在作品中，请重新核对选材。')
    if set(value['pages']).intersection(batch['pages']):
        raise ValueError('本批页面已在作品中，请重新核对选材。')
    value['resources'][resource_index:resource_index] = deepcopy(batch['resources'])
    if not identities:
        value['pages'] = deepcopy(batch['pages'])
    else:
        value['pages'][page_index:page_index] = deepcopy(batch['pages'])
    value['placements'].update(deepcopy(batch['placements']))
    reveals = list(value.get('click_reveals', []))
    reveals.extend(identity for identity in batch.get('click_reveals', []) if identity not in reveals)
    if reveals:
        value['click_reveals'] = reveals
    return validate_composition(value)
