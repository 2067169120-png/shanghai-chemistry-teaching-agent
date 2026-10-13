"""Explicit, local teaching pairs without rearranging the rest of a lesson."""
from uuid import uuid4

from .desktop_lesson_composition import (
    FORMATS, resource_frames, validate_composition, visible_blocks,
)
from .desktop_composition_render import (
    PADDING, _content, _continuation_heading, _fit_end,
    _has_visible_content, layout_composition,
)


def _frame_count(item, width, first_height, full_height, format, assets, data, *, student):
    """Measure the same complete lines and whole pictures as the saved renderer."""
    chunks = _content(item, width, assets, data, student=student,
        image_height=min(155, full_height-2*PADDING), format=format,
        figure_frame_height=first_height, first_flow_height=first_height-2*PADDING)
    if not chunks:
        return 0
    heading = _continuation_heading(item, width, format)
    cursor = 0
    count = 0
    while cursor < len(chunks):
        height = first_height if not count else full_height
        reserve = (sum(c['height'] for c in heading)
                   if cursor and _has_visible_content(chunks[cursor:]) else 0.0)
        capacity = height-2*PADDING-reserve
        end = _fit_end(chunks, cursor, capacity, strict=student)
        # Student filtering may move an intact task to a later, larger frame.
        # Preserve that first frame for the teacher, while reserving the later
        # page the student renderer needs. Nothing in either scope is skipped.
        if end == cursor and (count or first_height >= full_height):
            raise ValueError(item['title'] + '：完整文字行或图片放不下所选画幅，请调整画幅。')
        cursor = end
        count += 1
        if count > 160:
            raise ValueError('完整作品超过160页，请按课时拆分。')
    return count


def _placement(frames):
    return {**frames[0], **({'continuations': frames[1:]} if len(frames) > 1 else {})}


def compose_teaching_pair(composition, assets, image_data, first_id, second_id, *,
                          mode='side_by_side', classroom_uses_pair=True):
    """Place two teacher-selected resources together, keeping all source data.

    The first selection occupies the left/top; the second the right/bottom.
    Only these placements and their newly allocated pages change. Shared old
    pages and intentional blanks survive. Long resources continue at the same
    width and existing readable font; no relationship or source is inferred.

    ``classroom_uses_pair`` is an explicit audience-layout choice: true removes
    independent student flow, while false retains that saved policy unchanged.
    All three render scopes are checked before returning the detached result.
    """
    if not isinstance(mode, str) or mode not in {'side_by_side', 'stacked'}:
        raise ValueError('请选择左右讲解或上下讲解。')
    if type(classroom_uses_pair) is not bool:
        raise ValueError('课堂是否沿用组合位置须明确选择。')
    value = validate_composition(composition)
    if (not isinstance(first_id, str) or not isinstance(second_id, str)
            or first_id == second_id
            or first_id not in value['placements'] or second_id not in value['placements']):
        raise ValueError('请选择当前作品中两个不同的素材。')
    identities = (first_id, second_id)
    items = {r['id']: r for r in value['resources']}
    for identity in identities:
        if not any(b['kind'] == 'image' or b['value'].strip()
                   for b in visible_blocks(items[identity], student=False)):
            raise ValueError(items[identity]['title'] + '：请先补充内容，再组合讲解页。')
    assets_by_id = {a['asset_id']: a for a in assets}
    page_width, page_height = FORMATS[value['format']]
    margin, gap = 12.0, 8.0
    full_height = page_height-2*margin
    width = (page_width-2*margin-gap)/2 if mode == 'side_by_side' else page_width-2*margin
    first_height = full_height if mode == 'side_by_side' else (full_height-gap)/2
    counts = []
    for identity in identities:
        counts.append(max(_frame_count(items[identity], width, first_height,
            full_height, value['format'], assets_by_id, image_data, student=student)
            for student in (False, True)))
    page_count = max(counts) if mode == 'side_by_side' else 1+sum(c-1 for c in counts)
    if page_count > 160:
        raise ValueError('完整作品超过160页，请按课时拆分。')
    pair_pages = ['CP-'+uuid4().hex for _ in range(page_count)]
    continuation_index = 1
    for position, (identity, count) in enumerate(zip(identities, counts)):
        frames = []
        for index in range(count):
            if mode == 'side_by_side':
                page_id = pair_pages[index]
                x, y, height = margin+position*(width+gap), margin, full_height
            elif index == 0:
                page_id = pair_pages[0]
                x, y, height = margin, margin+position*(first_height+gap), first_height
            else:
                page_id = pair_pages[continuation_index]
                continuation_index += 1
                x, y, height = margin, margin, full_height
            frames.append({'page_id': page_id, 'x': x, 'y': y,
                           'width': width, 'height': height})
        value['placements'][identity] = _placement(frames)

    old_pair_pages = {f['page_id'] for identity in identities
                      for f in resource_frames(composition['placements'][identity])}
    occupied_by_others = {f['page_id'] for identity, placement in value['placements'].items()
                          if identity not in identities for f in resource_frames(placement)}
    removed = old_pair_pages-occupied_by_others
    first_old_page = next(p for p in value['pages'] if p in old_pair_pages)
    pages = []
    for page_id in value['pages']:
        if page_id == first_old_page:
            pages.extend(pair_pages)
        if page_id not in removed:
            pages.append(page_id)
    value['pages'] = pages
    if len(pages) > 160:
        raise ValueError('完整作品超过160页，请按课时拆分。')
    if classroom_uses_pair:
        value.pop('student_arrangement', None)
    value = validate_composition(value)
    errors = []
    for student, reveals, label in (
            (False, False, '教师版'), (True, False, '学生静态版'), (True, True, '课堂点击版')):
        try:
            layout = layout_composition(value, list(assets_by_id.values()), image_data,
                                        student=student, include_reveals=reveals)
        except ValueError as exc:
            errors.append(label+'：'+str(exc))
        else:
            errors.extend(label+'：'+error for error in layout['errors'])
    if errors:
        raise ValueError('\n'.join(dict.fromkeys(errors)))
    return value
