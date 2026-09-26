"""Explicit recovery edits for a provider response rejected before freezing.

The ordinary revision helper edits a validated canonical candidate by an
explicit field path.  A candidate rejected during normalization has no
canonical IDs yet, so recovery intentionally exposes one small, semantic edit
surface: replace the complete comparison table on a numbered slide, optionally
splitting it at an explicitly chosen row with an explicit time allocation. No
arbitrary JSON path, automatic row deletion, answer inference, or chemistry
correction is performed here.  A teacher may explicitly submit a complete
replacement table, including a deliberate row/column change; the caller must
run the normal candidate normalizer after this operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .desktop_preparation import DesktopPreparationError
from .desktop_preparation_visual import (
    ComparisonRowCountError,
    normalize_slide_visual,
)

_EDIT_ERROR = "preparation_returned_repair_invalid"


def _invalid(message_zh: str) -> DesktopPreparationError:
    return DesktopPreparationError(_EDIT_ERROR, message_zh, True)


def apply_returned_comparison_edits(
    candidate: Mapping[str, Any], edits: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply explicit full-table replacements to a non-canonical response.

    ``edits`` deliberately has the narrow shape
    ``[{slide_number, comparison}]``, optionally adding both ``split_after_row``
    and ``first_page_minutes``. Page numbers always refer to the original return,
    even when several pages are split. The table itself is validated with the
    same relationship/length checks used by the production visual normalizer;
    invalid rows are rejected rather than padded, truncated, or inferred.
    """

    if not isinstance(candidate, Mapping):
        raise _invalid("模型返回稿不是可修订的结构化对象。")
    slides = candidate.get("slides")
    if not isinstance(slides, list) or not slides:
        raise _invalid("模型返回稿缺少可修订的PPT页面。")
    if not isinstance(edits, list) or not edits or len(edits) > len(slides):
        raise _invalid("请至少选择一页比较表进行明确修订。")

    updated = deepcopy(dict(candidate))
    updated_slides = updated.get("slides")
    if not isinstance(updated_slides, list):  # defensive after deepcopy
        raise _invalid("模型返回稿的PPT页面结构不正确。")
    seen: set[int] = set()
    replacements: dict[int, list[dict[str, Any]]] = {}

    for edit in edits:
        fields = {"slide_number", "comparison"}
        if not isinstance(edit, Mapping) or set(edit) not in (
            fields,
            fields | {"split_after_row", "first_page_minutes"},
        ):
            raise _invalid(
                "修订应包含页码和完整比较表；拆页时还需指定断行位置与第一页分钟数。"
            )
        slide_number = edit.get("slide_number")
        if (
            type(slide_number) is not int
            or slide_number < 1
            or slide_number > len(slides)
        ):
            raise _invalid("比较表页码不在模型返回稿范围内。")
        if slide_number in seen:
            raise _invalid("同一页比较表不能重复提交修订。")
        seen.add(slide_number)

        original_slide = slides[slide_number - 1]
        updated_slide = updated_slides[slide_number - 1]
        if not isinstance(original_slide, Mapping) or not isinstance(
            updated_slide, dict
        ):
            raise _invalid(f"PPT页面{slide_number}结构不正确。")
        original_visual = original_slide.get("visual")
        updated_visual = updated_slide.get("visual")
        if not isinstance(original_visual, Mapping) or not isinstance(
            updated_visual, dict
        ):
            raise _invalid(f"PPT页面{slide_number}没有可修订的比较表。")
        if original_visual.get("kind") != "comparison":
            raise _invalid(f"PPT页面{slide_number}不是比较表版式。")

        tables = [deepcopy(edit["comparison"])]
        minutes = [original_slide.get("minutes")]
        if "split_after_row" in edit:
            table = tables[0]
            cut, first = edit["split_after_row"], edit["first_page_minutes"]
            total = minutes[0]
            rows = table.get("rows") if isinstance(table, Mapping) else None
            if (
                not isinstance(rows, list)
                or type(cut) is not int
                or not 2 <= cut <= 4
                or not 2 <= len(rows) - cut <= 4
            ):
                raise _invalid(
                    f"PPT页面{slide_number}拆页后每页须保留2—4行，不能丢弃内容。"
                )
            if (
                type(total) is not int
                or type(first) is not int
                or not 1 <= first < total
            ):
                raise _invalid(
                    f"PPT页面{slide_number}须至少2分钟；拆页后每页至少1分钟且合计不变。"
                )
            if not isinstance(original_slide.get("title"), str):
                raise _invalid(f"PPT页面{slide_number}缺少有效标题。")
            tables = [deepcopy(table), deepcopy(table)]
            tables[0]["rows"], tables[1]["rows"] = rows[:cut], rows[cut:]
            minutes = [first, total - first]

        pages = []
        for index, table in enumerate(tables):
            visual_for_check = dict(updated_visual)
            visual_for_check["comparison"] = table
            try:
                normalized_visual = normalize_slide_visual(visual_for_check)
            except ComparisonRowCountError as exc:
                raise _invalid(f"PPT页面{slide_number}：{exc.message_zh}") from None
            except ValueError:
                raise _invalid(
                    f"PPT页面{slide_number}的比较表尚不完整：数据列2—3列、内容行2—4行；"
                    "标题1—32字、内容1—120字，每格均需核对填写。"
                ) from None
            if normalized_visual is None:
                raise _invalid(f"PPT页面{slide_number}的比较表不能为空。")
            page = deepcopy(updated_slide)
            page["visual"]["comparison"] = normalized_visual["comparison"]
            if len(tables) == 2:
                page["title"] = f"{original_slide['title']}（{index + 1}/2）"
                page["minutes"] = minutes[index]
            # Retain shared context, notes and objective/activity/assessment
            # links on each half. No free-text page references are rewritten.
            pages.append(page)
        replacements[slide_number] = pages

    updated["slides"] = [
        page
        for number, slide in enumerate(updated_slides, 1)
        for page in replacements.get(number, [slide])
    ]

    if updated == candidate:
        raise DesktopPreparationError(
            "preparation_revision_unchanged", "内容没有变化，无需另存修订版。"
        )
    return updated
