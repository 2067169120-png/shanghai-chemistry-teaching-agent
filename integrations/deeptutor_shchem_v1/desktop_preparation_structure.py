"""Explicit, offline classroom structure edits on an existing candidate.

No inference of activity links or model call. Text edits are applied first;
these operations use stable IDs so inserting a page cannot retarget an edit.
The same pure preview is used by the form and by the saving endpoint.
"""

from collections.abc import Mapping
from copy import deepcopy

from .desktop_preparation import (
    DesktopPreparationError,
    _allocate_minutes,
    _canonical_candidate_digest,
    _exclusive_activity_groups,
    _grouped_stage_times_agree,
    _validate_canonical_candidate,
)
from .desktop_preparation_insert import insert_teacher_page, next_local_slide_id
from .desktop_preparation_revision import apply_preparation_text_edits
from .desktop_preparation_worksheet import normalize_worksheet


def _invalid(message):
    raise DesktopPreparationError("preparation_structure_invalid", message)


def _find(rows, identity, label):
    if not isinstance(identity, str):
        _invalid(f"请选择{label}。")
    matches = [row for row in rows if row["id"] == identity]
    if len(matches) != 1:
        _invalid(f"{label}不存在或编号重复，请重新打开原稿。")
    return matches[0]


def _split_image(candidate, operation):
    slide = _find(candidate["slides"], operation["slide_id"], "PPT页")
    if slide is candidate["slides"][0] or not slide.get("image"):
        _invalid("请选择首页以外、有教材图片的页面。")
    if not slide.get("content") or slide["minutes"] < 2:
        _invalid("图文拆页需要原页有正文、且至少2分钟；先核对活动归属并调整时间。")
    second = deepcopy(slide)
    # Keep every original question paragraph verbatim on the adjacent page.
    second.pop("image", None)
    second["minutes"] -= 1
    second["id"] = next_local_slide_id(candidate["slides"])
    slide["minutes"] = 1
    # The image renderer already displays observation_prompt; repeating it in
    # content would print it twice and unnecessarily shrink the source image.
    slide["content"] = ["读图后，结合下一页文字继续讨论。"]
    position = candidate["slides"].index(slide)
    candidate["slides"].insert(position + 1, second)
    for order, row in enumerate(candidate["slides"], 1):
        row["order"] = order


def _move_slides(candidate, operation):
    slides = candidate["slides"]
    identities = operation["slide_ids"]
    if (
        not isinstance(identities, list)
        or not identities
        or any(not isinstance(identity, str) for identity in identities)
        or len(set(identities)) != len(identities)
    ):
        _invalid("请选择一页或相邻的多页，页面编号不能重复。")
    selected = [_find(slides, identity, "PPT页") for identity in identities]
    positions = sorted(slides.index(slide) for slide in selected)
    if positions[0] == 0:
        _invalid("章节首页固定在第一位，不能移动。")
    if positions != list(range(positions[0], positions[-1] + 1)):
        _invalid("多页移动需要选择相邻页面；例题和解析可一起移动。")
    target = operation["before_slide_id"]
    if target is not None:
        anchor = _find(slides, target, "目标页面")
        if anchor is slides[0]:
            _invalid("不能插入到章节首页之前。")
        if target in identities:
            _invalid("目标页面不能在本次选中的页面组内。")
    # Always preserve the current order within the block, even if the caller
    # provides IDs in a different selection order.
    block = slides[positions[0] : positions[-1] + 1]
    remaining = [slide for slide in slides if slide["id"] not in identities]
    destination = (
        next(i for i, slide in enumerate(remaining) if slide["id"] == target)
        if target is not None
        else len(remaining)
    )
    moved = remaining[:destination] + block + remaining[destination:]
    if [slide["id"] for slide in moved] == [slide["id"] for slide in slides]:
        _invalid("页面已在这个位置，无需移动。")
    candidate["slides"] = moved
    for order, slide in enumerate(moved, 1):
        slide["order"] = order


def _align_time(candidate):
    activities, slides, stages = (
        candidate[key] for key in ("activities", "slides", "lesson_stages")
    )
    budgets = {row["id"]: row["minutes"] for row in activities}
    groups = _exclusive_activity_groups(slides, list(budgets))
    if len(budgets) != len(activities) or groups is None:
        _invalid("还有未关联、跨活动页面或未覆盖活动；请先逐页确认活动归属。")
    for activity_id, indexes in groups.items():
        if len(indexes) > budgets[activity_id]:
            _invalid(f"活动 {activity_id} 的页数多于分钟数，无法为每页保留至少1分钟。")
        values = _allocate_minutes(
            [slides[index]["minutes"] for index in indexes],
            budgets[activity_id],
            "教师确认的活动内PPT时间",
        )
        for index, minutes in zip(indexes, values, strict=True):
            slides[index]["minutes"] = minutes
    if not _grouped_stage_times_agree(activities, slides, stages):
        _invalid("教案分组、活动顺序或预算仍不一致；本操作不会重排页面或改写教案。")


def _append_notes(candidate, operation):
    activity = _find(candidate["activities"], operation["activity_id"], "教学活动")
    section = deepcopy(operation["section"])
    worksheet = deepcopy(activity.get("worksheet")) or {
        "title": operation["worksheet_title"],
        "instructions": ["随课堂讲练填写，记录判断依据、适用条件和纠错要点。"],
        "sections": [],
    }
    worksheet["sections"].append(section)
    try:
        activity["worksheet"] = normalize_worksheet(worksheet)
    except ValueError:
        _invalid("学习单内容超长或格式不正确；每个活动最多6小节，留白2至10行。")


def preview_structure_edits(candidate, operations):
    """Apply an explicit ordered operation list, without changing the input."""
    if not isinstance(operations, list) or len(operations) > 100:
        _invalid("结构修订格式不正确。")
    updated = deepcopy(candidate)
    split_ids = set()
    actions = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            _invalid("结构修订格式不正确。")
        kind = operation.get("kind")
        if not isinstance(kind, str):
            _invalid("请选择支持的结构修订操作。")
        keys = {
            "link_slide_activity": {"kind", "slide_id", "activity_id"},
            "split_image": {"kind", "slide_id"},
            "move_slides": {"kind", "slide_ids", "before_slide_id"},
            "insert_page": {"kind", "anchor_slide_id", "position", "minutes", "page"},
            "align_timing": {"kind"},
            "append_worksheet": {"kind", "activity_id", "worksheet_title", "section"},
        }.get(kind)
        if keys is None or set(operation) != keys:
            _invalid(
                "仅支持页面归属、图文拆页、相邻页组移动、补充页面、按活动校时和添加学习单留白。"
            )
        if kind == "link_slide_activity":
            slide = _find(updated["slides"], operation["slide_id"], "PPT页")
            activity = _find(
                updated["activities"], operation["activity_id"], "教学活动"
            )
            slide["activity_ids"] = [activity["id"]]
            actions.append(f"页面 {slide['id']} 归属 → {activity['title']}")
        elif kind == "split_image":
            identity = operation["slide_id"]
            if not isinstance(identity, str) or identity in split_ids:
                _invalid("同一原页在本次修订中只能拆分一次。")
            split_ids.add(identity)
            _split_image(updated, operation)
            actions.append(
                f"页面 {identity} → 前页读图、后页保留全部原正文；原页总分钟数不变"
            )
        elif kind == "move_slides":
            _move_slides(updated, operation)
            moved_ids = set(operation["slide_ids"])
            titles = [
                row["title"] for row in updated["slides"] if row["id"] in moved_ids
            ]
            target = operation["before_slide_id"]
            destination = (
                "「" + _find(updated["slides"], target, "目标页面")["title"] + "」之前"
                if target is not None
                else "课件末尾"
            )
            actions.append(
                "移动「"
                + "、".join(titles)
                + "」至"
                + destination
                + "；组内顺序、全部内容与分钟数不变"
            )
        elif kind == "insert_page":
            new = insert_teacher_page(updated, operation)
            actions.append(
                f"补充页面「{new['title']}」：从 {operation['anchor_slide_id']} 分配{new['minutes']}分钟，"
                "沿用所选页的活动、目标和评价关联；来源说明由修订者填写，未自动核验"
            )
        elif kind == "align_timing":
            _align_time(updated)
            actions.append("按已确认活动预算分配页内分钟数；活动和教案预算不变")
        else:
            _append_notes(updated, operation)
            actions.append(
                f"活动 {operation['activity_id']} 学习单增加：{operation['section']['heading']}"
            )
    return updated, actions


def classroom_timeline(candidate):
    """Readable exact minute boundaries; never guess period ownership by title."""
    timing = candidate["timing"]
    period = timing["minutes_per_period"]
    lines = [f"{timing['periods']}课时 × {period}分钟，共{timing['total_minutes']}分钟"]
    for key, label in (("slides", "PPT"), ("lesson_stages", "教案")):
        elapsed = 0
        for number, row in enumerate(candidate[key], 1):
            end = elapsed + row["minutes"]
            if elapsed and elapsed % period == 0:
                lines.append(
                    f"{label} 第{elapsed // period + 1}课时从第{number}项「{row['title']}」开始"
                )
            if elapsed // period != (end - 1) // period:
                lines.append(
                    f"{label} 第{number}项「{row['title']}」跨越课时边界（{elapsed}—{end}分钟）"
                )
            elapsed = end
    for activity in candidate["activities"]:
        linked = [
            slide
            for slide in candidate["slides"]
            if slide.get("activity_ids") == [activity["id"]]
        ]
        lines.append(
            f"{activity['id']} {activity['title']}：活动{activity['minutes']}分钟 / "
            f"明确关联PPT {sum(slide['minutes'] for slide in linked)}分钟、{len(linked)}页"
        )
    missing = [
        slide["id"]
        for slide in candidate["slides"]
        if len(slide.get("activity_ids", [])) != 1
    ]
    if missing:
        lines.append("尚需确认页面归属：" + "、".join(missing))
    return "\n".join(lines)


def apply_preparation_revision(candidate, text_edits, structure_edits, payload):
    """Canonical save path, preserving source evidence and existing warnings."""
    if structure_edits is not None and not isinstance(structure_edits, list):
        _invalid("结构修订格式不正确。")
    if not structure_edits:
        return apply_preparation_text_edits(candidate, text_edits, payload)
    original = _validate_canonical_candidate(candidate, payload)
    updated = (
        apply_preparation_text_edits(original, text_edits, payload)
        if text_edits
        else deepcopy(original)
    )
    updated, actions = preview_structure_edits(updated, structure_edits)
    if updated == original:
        raise DesktopPreparationError(
            "preparation_revision_unchanged", "内容没有变化，无需另存修订版。"
        )
    # Retain the raw generation's warnings as provenance. Add the concrete
    # local change without claiming teaching readiness or erasing prior gaps.
    updated["uncertainties"].append(
        {
            "field": "teacher_structure_revision",
            "description": "教师本地结构修订：" + "；".join(actions),
            "teacher_action": "原生成待核验事项保留；以本修订版核对页序、讲练时间、学习单和全部题面。",
        }
    )
    updated["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(updated)[:32]
    return _validate_canonical_candidate(updated, payload)
