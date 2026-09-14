"""Read-only view adapters for one faceted picker; existing identities stay intact."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .desktop_word_question_filters import compile_filter_options, compile_question_matcher
from .desktop_personal_visual_questions import matches_personal_visual_filters

PERSONAL_LANES = {"word_native", "visual_native"}
PERSONAL_LABELS = {"book": "教材册", "chapter": "教材章", "section": "教材节",
                   "knowledge": "知识点", "grade": "适用年级", "exam": "原考试类型",
                   "source": "资料来源", "teaching_use": "教学用途"}


def personal_options(catalog: Mapping, lane: str) -> dict:
    if lane == "word_native":
        groups = compile_filter_options(catalog["items"], catalog.get("attribute_catalog"))["groups"]
        return {key: {"label_zh": PERSONAL_LABELS[key], "values": [
            {"value": row["id"], "label_zh": row["label"], "atomic_count": None}
            for row in rows]} for key, rows in groups.items()}
    if lane != "visual_native":
        raise ValueError("Unknown personal source")
    return {key: {"label_zh": PERSONAL_LABELS.get(key, key), "values": [
        {"value": row["value"], "label_zh": row["label"], "atomic_count": None}
        for row in rows]} for key, rows in catalog.get("filter_options", {}).items()}


def personal_results(catalog: Mapping, lane: str, selection: Mapping, query: str,
                     page: int = 0, page_size: int = 8) -> dict:
    """Search question/context only; never use answers as query matches."""
    if lane not in PERSONAL_LANES or type(page) is not int or page < 0:
        raise ValueError("Invalid personal search")
    if not isinstance(catalog.get("items"), list):
        raise ValueError("Personal catalog is unavailable")
    chosen = {**selection, "query": query}
    matcher = compile_question_matcher(chosen, catalog.get("attribute_catalog")) if lane == "word_native" else None
    matches, seen = [], set()
    for row in catalog["items"]:
        if row.get("key") in seen:
            continue
        seen.add(row.get("key"))
        if lane == "word_native":
            keep = matcher(row)
        else:
            search_text = "\n".join(str(row.get(name, "")) for name in
                                    ("title", "source_name", "question_text", "shared_text"))
            keep = (matches_personal_visual_filters(row, selection)
                    and query.strip().casefold() in search_text.casefold())
        if keep:
            matches.append(row)
    start = page * page_size
    rows = matches[start:start + page_size]
    entries = []
    for row in rows:
        if lane == "word_native":
            # This is a search excerpt, not a reconstructed Word layout.
            excerpt = "\n".join(str(block.get("text", "")) for block in
                                row.get("context_blocks", []) + row.get("question_blocks", []))
        else:
            excerpt = "\n".join(filter(None, [row.get("shared_text"), row.get("question_text")]))
        entries.append({"key": row["key"], "lane": lane, "title": row.get("title", "未命名题目"),
                        "subtitle": row.get("source_name", "来源待核对"),
                        "excerpt": excerpt[:480], "payload": row,
                        "unit": "Word原题" if lane == "word_native" else "图片印刷题 · 入篮保留整主题"})
    return {"entries": entries, "total": len(matches), "has_more": start + len(rows) < len(matches),
            "next_cursor": None, "facets": personal_options(catalog, lane),
            "warnings": catalog.get("warnings", []), "count_unit": "条原题"}


def core_results(result) -> dict:
    entries = [{"key": card.key, "lane": getattr(card, "scope", result.scope), "title": card.title_zh,
                "subtitle": card.paper_title_zh + " · " + card.source_zh,
                "excerpt": getattr(card, "preview_zh", "") or card.shared_context_zh,
                "payload": card, "unit": f"完整主题 · {getattr(card, "display_atomic_units", 0) or card.atomic_total}个作答单元"}
               for card in result.cards]
    return {"entries": entries, "total": result.total_themes, "has_more": result.has_more,
            "next_cursor": getattr(result, "next_cursor", None), "facets": deepcopy(getattr(result, "facets", {})),
            "warnings": ([f"另有{result.pending_atomic_parts}个小问待补主题归属，未计入完整主题。"]
                         if getattr(result, "pending_atomic_parts", 0) else []), "count_unit": "个完整主题"}


def entry_is_selected(entry: Mapping, basket) -> bool:
    for item in basket:
        if entry["lane"] == "word_native":
            if item.get("word_selection", {}).get("key") == entry["key"]:
                return True
        elif entry["lane"] == "visual_native":
            if any(row.get("key") == entry["key"] for row in item.get("visual_selections", [])):
                return True
        elif item.get("key") == entry["key"]:
            return True
    return False
