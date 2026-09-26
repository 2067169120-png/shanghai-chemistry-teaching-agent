"""Disposable search projection for one already-read personal catalogue.

No source authority is cached here: selection/export still use the existing
source-bound services. Replace this index after import, tag/range edits or an
explicit reload. Answers and image bytes are never copied into the projection.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from threading import RLock
from time import perf_counter

from .desktop_question_explorer import PERSONAL_LANES, personal_options
from .desktop_personal_visual_questions import matches_personal_visual_filters
from .desktop_word_question_filters import (
    FILTER_GROUPS, UNKNOWN_ID, _bound_attributes, _chosen, _directory,
    _exam, _grades, _knowledge, _mappings, _search_text, _text,
    chapter_filter_id, section_filter_id,
)


class SearchCancelled(RuntimeError):
    """An obsolete query stopped before it published a result."""


def check_cancelled(cancelled):
    if cancelled():
        raise SearchCancelled("已停止旧筛选。")


class PersonalSearchIndex:
    """Reuse normalized tags/search text; cache at most four result ID lists."""
    MAX_QUERY_RESULTS = 4

    def __init__(self, catalog, lane, *, cancelled=lambda: False):
        if lane not in PERSONAL_LANES or not isinstance(catalog.get("items"), list):
            raise ValueError("Personal catalogue is unavailable")
        start = perf_counter()
        self.catalog, self.lane = catalog, lane
        self._lock, self._matches = RLock(), OrderedDict()
        self._rows = []
        nodes = _directory(catalog.get("attribute_catalog"))
        seen = set()
        for i, row in enumerate(catalog["items"]):
            if i % 64 == 0:
                check_cancelled(cancelled)
            key = row.get("key")
            if key in seen:
                continue
            seen.add(key)
            if lane == "word_native":
                attributes = _bound_attributes(row)
                mappings = _mappings(attributes, nodes)
                values = {
                    "source": {_text(row.get("source_id")) or UNKNOWN_ID},
                    "knowledge": set(_knowledge(attributes)) or {UNKNOWN_ID},
                    "grade": _grades(attributes) or {UNKNOWN_ID},
                    "exam": {_exam(attributes)},
                }
                paths = [{"book": n["volume_id"],
                          "chapter": chapter_filter_id(n["volume_id"], n["chapter_id"]),
                          "section": section_filter_id(n["volume_id"], n["chapter_id"], n["node_key"])}
                         for n in mappings] or [{g: UNKNOWN_ID for g in ("book", "chapter", "section")}]
                text = _search_text(row, attributes, mappings)
                excerpt = "\n".join(str(b.get("text", "")) for b in
                                    row.get("context_blocks", []) + row.get("question_blocks", []))[:480]
            else:
                values, paths = {}, []
                text = "\n".join(str(row.get(n, "")) for n in
                                 ("title", "source_name", "question_text", "shared_text")).casefold()
                excerpt = "\n".join(filter(None, [row.get("shared_text"), row.get("question_text")]))[:480]
            # Keep a reference to the existing row for the eight visible cards;
            # do not clone full answer/source blocks for every search or page.
            self._rows.append((row, values, paths, text, excerpt))
        check_cancelled(cancelled)
        self._facets = personal_options(catalog, lane)
        check_cancelled(cancelled)
        self.build_ms = (perf_counter() - start) * 1000

    def search(self, selection, query, page=0, page_size=8, *, cancelled=lambda: False):
        if not isinstance(selection, Mapping) or not isinstance(query, str):
            raise ValueError("Invalid personal search")
        if type(page) is not int or page < 0 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Invalid page")
        start = perf_counter()
        check_cancelled(cancelled)
        normalized = {k: _chosen(v) for k, v in selection.items() if k != "query"}
        # knowledge_mode is a mode string, not a selected facet.
        mode = selection.get("knowledge_mode", "any")
        normalized.pop("knowledge_mode", None)
        valid = all(v is not None for v in normalized.values()) and mode in ("any", "all")
        token = (tuple(sorted((k, tuple(sorted(v or ()))) for k, v in normalized.items())),
                 query.strip().casefold(), mode, valid)
        with self._lock:
            found = self._matches.get(token)
            cache_hit = found is not None
            if cache_hit:
                self._matches.move_to_end(token)
        if found is None:
            result = []
            chosen = {g: normalized.get(g, set()) for g in FILTER_GROUPS}
            if valid:
                for i, (row, values, paths, text, _) in enumerate(self._rows):
                    if i % 64 == 0:
                        check_cancelled(cancelled)
                    if self.lane == "word_native":
                        if any(chosen[g] and not chosen[g].intersection(v) for g, v in values.items()):
                            continue
                        if mode == "all" and not chosen["knowledge"].issubset(values["knowledge"]):
                            continue
                        if not any(all(not chosen[g] or p[g] in chosen[g] for g in ("book", "chapter", "section")) for p in paths):
                            continue
                    elif not matches_personal_visual_filters(row, selection):
                        continue
                    if token[1] and token[1] not in text:
                        continue
                    result.append(i)
            check_cancelled(cancelled)
            found = tuple(result)
            with self._lock:
                self._matches[token] = found
                self._matches.move_to_end(token)
                while len(self._matches) > self.MAX_QUERY_RESULTS:
                    self._matches.popitem(last=False)
        check_cancelled(cancelled)
        start_row = page * page_size
        entries = []
        for i in found[start_row:start_row + page_size]:
            row, _, _, _, excerpt = self._rows[i]
            entries.append({"key": row["key"], "lane": self.lane,
                "title": row.get("title", "未命名题目"), "subtitle": row.get("source_name", "来源待核对"),
                "excerpt": excerpt, "payload": row,
                "unit": "Word原题" if self.lane == "word_native" else "图片印刷题 · 入篮保留整主题"})
        return {"entries": entries, "total": len(found),
            "has_more": start_row + len(entries) < len(found), "next_cursor": None,
            "facets": deepcopy(self._facets), "warnings": list(self.catalog.get("warnings", [])),
            "count_unit": "条原题",
            "performance": {"projection_build_ms": round(self.build_ms, 2),
                            "filter_ms": round((perf_counter() - start) * 1000, 2),
                            "query_cache_hit": cache_hit, "indexed_items": len(self._rows)}}


class PersonalCatalogSession:
    """Coalesce a cold catalogue read when the teacher changes filters quickly.

    A cancelled request may finish its file read; the next request reuses that
    same read. Explicit reload replaces this entire session, so an older worker
    cannot repopulate the newly selected catalogue with its stale result.
    """
    def __init__(self, lane):
        self.lane = lane
        self._lock = RLock()
        self.catalog = None
        self.index = None

    def get(self, loader, *, cancelled=lambda: False):
        started = perf_counter()
        while not self._lock.acquire(timeout=.05):
            check_cancelled(cancelled)
        try:
            check_cancelled(cancelled)
            reused = self.catalog is not None
            read_started = perf_counter()
            if self.catalog is None:
                self.catalog = loader()
            read_ms = (perf_counter() - read_started) * 1000
            check_cancelled(cancelled)
            build_started = perf_counter()
            if self.index is None:
                self.index = PersonalSearchIndex(self.catalog, self.lane, cancelled=cancelled)
            return self.index, {"catalog_ms": round(read_ms, 2),
                "index_ms": round((perf_counter() - build_started) * 1000, 2),
                "catalogue_reused": reused,
                "catalog_wait_and_prepare_ms": round((perf_counter() - started) * 1000, 2)}
        finally:
            self._lock.release()
