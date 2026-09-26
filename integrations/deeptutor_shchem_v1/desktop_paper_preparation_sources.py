"""Read-only source enrichment for the current paper-composer draft.

The paper composer owns a mutable teacher draft.  The persisted selection
basket is only the starting point and must not be used to rebuild a draft that
has already been reordered, edited, replaced, or partially removed.  This
module therefore takes the draft payload as its input and adds a separate
``source_detail`` projection when an exact source binding can be resolved.

Only text from :class:`LibraryThemeDetail` / :class:`LibraryPartDetail` is
projected here.  Image descriptors, local paths, crop objects, and model
calls deliberately stay outside this contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .desktop_library import LibraryPartDetail, LibraryThemeDetail

_DESKTOP_SCOPES = frozenset({"master", "wave1", "supplemental"})
_IDENTITY_WARNING = "来源身份校验失败，未读取其他题目的答案或解析。"
_DETAIL_WARNING = "来源详情暂时无法读取，当前组卷文字保留；未补写题面、答案或解析。"
_PERSONAL_WARNING = "个人讲义暂未接入来源详情读取，当前组卷文字保留。"
_NO_THEME_REF_WARNING = "当前大题缺少明确 source_ref，未猜测来源；当前组卷文字保留。"
_NO_QUESTION_REF_WARNING = "当前小问缺少明确 atomic_part_id，未猜测来源；当前题面、答案和解析保留。"
_PART_MISSING_WARNING = "当前小问未在该大题来源详情中找到同身份作答单元，未借用其他小问答案或解析。"
_SNAPSHOT_MISSING_WARNING = "来源快照标识缺失，详情按当前题库读取；请在题库变化后重新确认。"


def _text(value: Any, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _theme_identity(scope: str, paper_id: str, theme_id: str) -> str:
    return _canonical_digest(
        {"scope": scope, "paper": paper_id, "theme": theme_id}
    )


def _append_warning(container: dict[str, Any], message: str) -> None:
    """Add one stable human-readable warning without exposing exceptions."""

    if not message:
        return
    current = container.get("source_detail_warning")
    if isinstance(current, str) and current.strip():
        values = [item.strip() for item in current.split("；") if item.strip()]
    elif isinstance(current, list):
        values = [item.strip() for item in current if isinstance(item, str) and item.strip()]
    else:
        values = []
    if message not in values:
        values.append(message)
    container["source_detail_warning"] = "；".join(values)


def _safe_text_list(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values] if values.strip() else []
    if not isinstance(values, (list, tuple)):
        return []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def _theme_detail_text(detail: LibraryThemeDetail) -> dict[str, Any]:
    return {
        "paper_title": _text(detail.paper_title_zh, "来源卷名称待核验"),
        "page": _text(detail.page_zh, "页码待核验"),
        "source_fields": [
            {"label": _text(label), "value": _text(value)}
            for label, value in detail.source_fields
            if _text(label) or _text(value)
        ],
        "context": _text(detail.context_zh, "共同材料摘要待整理"),
        "notes": _safe_text_list(detail.notes_zh),
    }


def _part_detail_text(part: LibraryPartDetail) -> dict[str, Any]:
    analysis = _safe_text_list(part.analysis_zh)
    if analysis:
        # The Qt detail view supplies this label as a separate heading.  Keep
        # it in the serialized handoff so a formatter cannot mistake a model
        # candidate solution path for an official explanation.
        analysis = ["模型候选解路（非官方解析）", *analysis]
    return {
        "summary": _text(part.summary_zh, "题意摘要待整理"),
        "requirement": _text(part.requirement_zh, "作答要求待整理"),
        "reference_answer": _text(part.reference_answer_zh),
        "answer_boundary": _text(
            part.answer_boundary_zh,
            "未找到可对齐的参考答案；不补写模型答案。",
        ),
        "analysis": analysis,
        "notes": _safe_text_list(part.quality_notes_zh),
    }


def _make_theme_card(theme: Mapping[str, Any], source_ref: Mapping[str, Any], identity: str) -> Any:
    """Build the existing detail-loader DTO without importing facade eagerly."""

    # Imported lazily to avoid a desktop_facade -> this module -> facade cycle
    # during application startup.  The facade is fully initialized by the
    # time this function is called.
    from .desktop_facade import ThemeCard

    questions = theme.get("questions")
    count = len(questions) if isinstance(questions, list) else 0
    current_title = _text(theme.get("title"), "未命名大题")
    current_source = _text(theme.get("source"), "来源待核验")
    current_context = _text(
        theme.get("shared_text") or theme.get("shared_summary"),
        "共同材料摘要待整理",
    )
    return ThemeCard(
        key=identity,
        scope=_text(source_ref.get("scope")),
        title_zh=current_title,
        paper_title_zh=current_source,
        source_zh=current_source,
        atomic_total=count,
        atomic_matched=count,
        shared_context_zh=current_context,
        page_zh="页码待核验",
        source_identity_sha256=identity,
        data_snapshot_id=_text(source_ref.get("data_snapshot_id")),
        display_atomic_units=count,
    )


def _parts_by_key(detail: LibraryThemeDetail) -> dict[str, LibraryPartDetail]:
    result: dict[str, LibraryPartDetail] = {}
    ambiguous: set[str] = set()
    for part in detail.parts:
        key = _text(part.key)
        if not key or key in ambiguous:
            continue
        if key in result:
            # Once a key has appeared twice, every occurrence of that key is
            # ambiguous.  Keep it removed even if a third duplicate follows.
            result.pop(key, None)
            ambiguous.add(key)
        else:
            result[key] = part
    return result


def enrich_paper_preparation_sources(
    snapshot: Mapping[str, Any],
    *,
    detail_loader: Callable[[Any], LibraryThemeDetail],
) -> dict[str, Any]:
    """Deep-copy and enrich a current ``PaperComposerModel.draft_payload``.

    ``detail_loader`` is normally ``DesktopWorkbenchFacade.library_theme_detail``.
    It is injected so this pure projection can be tested without touching
    state, readers, files, credentials, or a model provider.
    """

    if not isinstance(snapshot, Mapping):
        raise TypeError("paper preparation source snapshot must be a mapping")
    if not callable(detail_loader):
        raise TypeError("paper preparation source detail loader must be callable")

    result = deepcopy(dict(snapshot))
    raw_themes = result.get("themes")
    if not isinstance(raw_themes, list):
        _append_warning(result, "当前组卷快照没有可读取的 themes，未补充来源详情。")
        return result

    for theme in raw_themes:
        if not isinstance(theme, dict):
            continue
        _enrich_theme(theme, detail_loader=detail_loader)
    return result


def _enrich_theme(
    theme: dict[str, Any],
    *,
    detail_loader: Callable[[Any], LibraryThemeDetail],
) -> None:
    _clear_previous_source_details(theme)
    source_ref = theme.get("source_ref")
    if not isinstance(source_ref, Mapping):
        _append_warning(theme, _NO_THEME_REF_WARNING)
        _warn_questions(theme, _NO_QUESTION_REF_WARNING)
        return

    scope = _text(source_ref.get("scope"))
    if scope == "personal_handouts":
        _append_warning(theme, _PERSONAL_WARNING)
        _warn_questions(theme, _PERSONAL_WARNING)
        return
    if scope not in _DESKTOP_SCOPES:
        _append_warning(theme, "题库范围缺少可读来源详情接口，当前组卷文字保留。")
        _warn_questions(theme, "题库范围缺少可读来源详情接口，当前题面、答案和解析保留。")
        return

    paper_id = _text(source_ref.get("paper_id"))
    theme_id = _text(source_ref.get("theme_id"))
    identity = _text(theme.get("source_identity_sha256"))
    if not paper_id or not theme_id or not identity:
        _append_warning(theme, _IDENTITY_WARNING)
        _warn_questions(theme, _IDENTITY_WARNING)
        return
    expected_identity = _theme_identity(scope, paper_id, theme_id)
    if identity != expected_identity:
        _append_warning(theme, _IDENTITY_WARNING)
        _warn_questions(theme, _IDENTITY_WARNING)
        return

    snapshot_id = _text(source_ref.get("data_snapshot_id"))
    card = _make_theme_card(theme, source_ref, identity)
    try:
        detail = detail_loader(card)
    except Exception:  # noqa: BLE001 - source gaps become explicit warnings
        _append_warning(theme, _DETAIL_WARNING)
        _warn_questions(theme, _DETAIL_WARNING)
        return
    if not isinstance(detail, LibraryThemeDetail):
        _append_warning(theme, _DETAIL_WARNING)
        _warn_questions(theme, _DETAIL_WARNING)
        return
    if detail.scope != scope or detail.key != identity:
        _append_warning(theme, _IDENTITY_WARNING)
        _warn_questions(theme, _IDENTITY_WARNING)
        return

    theme["source_detail"] = _theme_detail_text(detail)
    if not snapshot_id:
        _append_warning(theme, _SNAPSHOT_MISSING_WARNING)

    parts = _parts_by_key(detail)
    questions = theme.get("questions")
    if not isinstance(questions, list):
        return
    for question in questions:
        if not isinstance(question, dict):
            continue
        _enrich_question(question, parts)


def _enrich_question(
    question: dict[str, Any], parts: Mapping[str, LibraryPartDetail]
) -> None:
    source_ref = question.get("source_ref")
    if not isinstance(source_ref, Mapping):
        _append_warning(question, _NO_QUESTION_REF_WARNING)
        return
    atomic_id = _text(source_ref.get("atomic_part_id"))
    if not atomic_id:
        _append_warning(question, _NO_QUESTION_REF_WARNING)
        return
    part = parts.get(atomic_id)
    if part is None:
        _append_warning(question, _PART_MISSING_WARNING)
        return
    question["source_detail"] = _part_detail_text(part)


def _warn_questions(theme: Mapping[str, Any], message: str) -> None:
    questions = theme.get("questions")
    if not isinstance(questions, list):
        return
    for question in questions:
        if isinstance(question, dict):
            _append_warning(question, message)


def _clear_previous_source_details(theme: dict[str, Any]) -> None:
    """Prevent stale source text from surviving a second enrichment attempt."""

    theme.pop("source_detail", None)
    theme.pop("source_detail_warning", None)
    questions = theme.get("questions")
    if not isinstance(questions, list):
        return
    for question in questions:
        if isinstance(question, dict):
            question.pop("source_detail", None)
            question.pop("source_detail_warning", None)


__all__ = ["enrich_paper_preparation_sources"]
