"""Five-lane search contracts using only small, source-bound memory catalogues.

No native Office, configured provider, personal source file, or image is opened.
The Word fixture deliberately exposes only listing metadata and a postings port.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from threading import Event, Thread

import pytest

from integrations.deeptutor_shchem_v1 import desktop_unified_question_search as unified
from integrations.deeptutor_shchem_v1.desktop_explorer_index import SearchCancelled
from integrations.deeptutor_shchem_v1.desktop_facade import ThemeCard, ThemeSearchResult
from integrations.deeptutor_shchem_v1.desktop_unified_question_search import (
    UnifiedQuestionSearch,
    UnifiedQuestionSearchError,
)
from integrations.deeptutor_shchem_v1.desktop_word_question_filters import (
    chapter_filter_id,
    section_filter_id,
)
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, check_read_cancelled

LANES = ("master", "wave1", "supplemental", "word_native", "visual_native")
FORMAL_LANES = LANES[:3]
PERSONAL_LANES = LANES[3:]


def _directory():
    return {
        "knowledge_points": [
            {"id": "K01", "name": "合成知识甲"},
            {"id": "K02", "name": "合成知识乙"},
        ],
        "nodes": [
            {
                "node_key": "S1", "volume_id": "V1", "chapter_id": "C1",
                "volume_title": "合成第一册", "chapter_title": "同名章",
                "section_title": "同名节", "section_number": "1.1",
            },
            {
                "node_key": "S2", "volume_id": "V2", "chapter_id": "C1",
                "volume_title": "合成第二册", "chapter_title": "同名章",
                "section_title": "同名节", "section_number": "1.1",
            },
        ],
        "teaching_use_tags": [{"id": "practice", "name": "合成练习"}],
    }


def _card(lane, key, *, text="合成题面", snapshot="snapshot-1"):
    return ThemeCard(
        key=key, scope=lane, title_zh=f"合成主题 {key}",
        paper_title_zh="合成原卷", source_zh="合成来源", atomic_total=3,
        atomic_matched=2, shared_context_zh=text, page_zh="第 2 页",
        source_identity_sha256=f"original-identity-{lane}-{key}",
        data_snapshot_id=snapshot, preview_zh=text, tags_zh=("合成标签",),
    )


class _ListingProvider:
    """A tiny bound-postings stand-in; answers are intentionally unavailable."""

    def __init__(self):
        self.texts = {}
        self.calls = []

    def __deepcopy__(self, memo):
        return self

    def matches(self, key, revision, query):
        self.calls.append((key, revision, query))
        text = self.texts.get((key, revision))
        return text is not None and query.casefold() in text.casefold()


def _word_row(key, provider, *, text="合成题面", knowledge=("K01", "K02"),
              section="S1", source="WORD-SOURCE", revision="word-r1"):
    node = next((n for n in _directory()["nodes"] if n["node_key"] == section), None)
    provider.texts[(key, revision)] = text
    points = [{"id": k, "label": f"合成知识{k}", "status": "auto_suggested"}
              for k in knowledge]
    row = {
        "key": key, "revision": revision, "source_sha256": "a" * 64,
        "batch_id": "word-batch", "source_id": source,
        "archive_source_id": "original-word-file", "source_name": "合成讲义.docx",
        "source_revision": "original-word-revision", "source_block_count": 30,
        "title": f"合成Word题 {key}", "chapter": "同名章",
        "block_start": 4, "question_end": 7, "answer_start": 8,
        "block_end": 9, "context_start": 2, "context_end": 3,
        "selection_unit": "question_segment", "selection_ready": True,
        "lightweight": True, "excerpt": text[:40], "search_provider": provider,
    }
    row["attributes"] = {
        "key": key, "source_sha256": row["source_sha256"],
        "question_revision": revision, "revision": "attributes-r1",
        "primary_knowledge": points[0] if points else {"id": "unknown", "status": "unknown"},
        "supporting_knowledge": points[1:],
        "applicable_grades": {"values": ["grade_12"], "status": "auto_suggested"},
        "original_source": {
            "exam_type": {"value": "second_mock", "status": "source_observed"},
            "display_label": "合成原考试",
        },
        "teaching_use_tags": ["practice"], "source": {"package_id": "WORD-PACK"},
        "curriculum_status": "auto_suggested" if node else "pending_mapping",
        "curriculum_candidates": [{
            "section_key": node["node_key"], "volume_id": node["volume_id"],
            "chapter_id": node["chapter_id"], "status": "auto_suggested",
        }] if node else [],
    }
    return row


def _visual_row(key, *, text="合成题面", knowledge=("K01", "K02"), section="S1"):
    node = next((n for n in _directory()["nodes"] if n["node_key"] == section), None)
    return {
        "key": key, "batch_id": "visual-batch", "revision": "visual-r1",
        "candidate_sha256": "b" * 64, "source_sha256": "c" * 64,
        "title": f"合成印刷小题 {key}", "source_name": "合成原图.pdf",
        "theme_key": "original-parent-theme", "theme_title": "原题面主题",
        "question_number": key, "question_text": text,
        "shared_text": "合成共同材料", "answer_text": "ONLY_ANSWER_OMEGA",
        "images": [{"image_id": "original-image-id", "role": "question", "page_number": 2}],
        "facets": {
            "source": ["VISUAL-SOURCE"], "book": [node["volume_id"]] if node else [],
            "knowledge": list(knowledge), "grade": ["grade_12"],
            "exam": ["second_mock"], "teaching_use": ["practice"],
        },
        "curriculum_paths": [{
            "volume_id": node["volume_id"], "chapter_id": node["chapter_id"],
            "section_key": node["node_key"],
        }] if node else [],
        "selection_ready": True,
    }


class FakeFacade:
    """Only catalogue/search ports work; any body or image read fails the test."""

    def __init__(self):
        self.provider = _ListingProvider()
        self.formal = {lane: [] for lane in FORMAL_LANES}
        self.formal_labels = {}
        self.formal_snapshots = {lane: f"{lane}-snapshot-1" for lane in FORMAL_LANES}
        self.word = {"items": [], "revision": "word-catalog-r1", "warnings": [],
                     "attribute_catalog": _directory(), "sources": [],
                     "projection": "source-bound-lightweight-word-listing-v1"}
        self.visual = {"items": [], "revision": "visual-catalog-r1", "warnings": [],
                       "attribute_catalog": _directory(), "filter_options": {}}
        self.formal_calls = []
        self.word_calls = 0
        self.visual_calls = 0
        self.failures = {}
        self.body_calls = []
        self.cache_epochs = {"word": 0, "registry": 0}
        self.epoch_calls = []
        self.source_snapshots = {lane: {
            "source": "source-r1", "ranges": "ranges-r1",
            "attrs": "attrs-r1", "taxonomy": "taxonomy-r1",
        } for lane in LANES}
        self.snapshot_calls = []

    def desktop_cache_epoch(self, domain):
        self.epoch_calls.append(domain)
        return f"{domain}-epoch-{self.cache_epochs[domain]}"

    def unified_question_source_snapshot(self, lane, *, cancelled=None, progress=None):
        if cancelled and cancelled():
            raise ReadCancelled("合成元数据取消")
        self.snapshot_calls.append(lane)
        return deepcopy(self.source_snapshots[lane])

    def add_formal(self, lane, key, *, text="合成题面", knowledge=("K01", "K02"),
                   ability=("A01",), section="S1"):
        card = _card(lane, key, text=text, snapshot=self.formal_snapshots[lane])
        self.formal[lane].append(card)
        self.formal_labels[(lane, key)] = {"K": set(knowledge), "A": set(ability),
                                          "section": section}
        return card

    def add_word(self, key, **kwargs):
        row = _word_row(key, self.provider, **kwargs)
        self.word["items"].append(row)
        return row

    def add_visual(self, key, **kwargs):
        row = _visual_row(key, **kwargs)
        self.visual["items"].append(row)
        self.visual["filter_options"] = {
            group: [{"value": value, "label": value} for value in sorted({
                value for item in self.visual["items"]
                for value in item["facets"].get(group, [])
            })] for group in ("source", "book", "knowledge", "grade", "exam", "teaching_use")
        }
        return row

    def search_themes(self, *, scope, query="", limit=50, filters=None,
                      curriculum=None, cursor=None):
        self.formal_calls.append({"scope": scope, "query": query, "limit": limit,
                                  "filters": deepcopy(filters or {}),
                                  "curriculum": deepcopy(curriculum), "cursor": cursor})
        if scope in self.failures:
            raise self.failures[scope]
        assert 1 <= limit <= 50
        rows = []
        for card in self.formal[scope]:
            labels = self.formal_labels[(scope, card.key)]
            # Native search is one substring and native axes use same-axis OR.
            if query.casefold() not in (card.title_zh + "\n" + card.shared_context_zh).casefold():
                continue
            if any(set(values) and not set(values).intersection(labels.get(axis, set()))
                   for axis, values in (filters or {}).items()):
                continue
            if curriculum:
                node = next((n for n in _directory()["nodes"]
                             if n["node_key"] == labels["section"]), None)
                path = {"volume_id": node["volume_id"], "chapter_id": node["chapter_id"],
                        "section": node["node_key"]} if node else {}
                if any(value != path.get(field) for field, value in curriculum.items()
                       if field != "mapping_status"):
                    continue
            rows.append(replace(card, data_snapshot_id=self.formal_snapshots[scope]))
        offset = int(cursor.split(":", 1)[1]) if cursor else 0
        visible = tuple(rows[offset:offset + limit])
        has_more = offset + len(visible) < len(rows)
        return ThemeSearchResult(
            scope=scope, query=query, total_themes=len(rows),
            matched_atomic_parts=len(rows) * 2, cards=visible, has_more=has_more,
            next_cursor=f"offset:{offset + len(visible)}" if has_more else None,
            facets={"K": {"values": [{"value": "K01", "label_zh": "合成知识甲"}]}},
        )

    def word_question_listing(self):
        self.word_calls += 1
        if "word_native" in self.failures:
            raise self.failures["word_native"]
        return deepcopy(self.word)

    def word_question_listing_progress(self, progress, cancelled):
        if cancelled():
            raise ReadCancelled("合成取消")
        if progress:
            progress({"message": "合成轻量目录进度"})
        return self.word_question_listing()

    def personal_visual_questions(self, batch_id=None):
        self.visual_calls += 1
        if "visual_native" in self.failures:
            raise self.failures["visual_native"]
        return deepcopy(self.visual)

    def curriculum_catalog(self, **kwargs):
        return {"volumes": [
            {"volume_id": node["volume_id"], "volume_title": node["volume_title"],
             "chapters": [{"chapter_id": node["chapter_id"],
                           "chapter_title": node["chapter_title"],
                           "sections": [{"section_key": node["node_key"],
                                         "section_title": node["section_title"]}]}]}
            for node in _directory()["nodes"]
        ]}

    def _body(self, *args, **kwargs):
        self.body_calls.append((args, kwargs))
        raise AssertionError("Unified search must not expand source bodies or images")

    word_question_catalog = _body
    word_question_catalog_progress = _body
    word_question_detail = _body
    word_question_source = _body
    word_question_image = _body
    imported_word_source_preview = _body
    personal_visual_question_detail = _body
    personal_visual_question_image = _body
    theme_question_reference = _body
    theme_reference = _body


class _FrozenState:
    """Synthetic state descriptor; it never instantiates DesktopStateStore."""

    def __init__(self, path, snapshot):
        self.path = path
        self.value = deepcopy(snapshot)
        self.path.write_text(json.dumps(self.value), encoding="utf-8")

    def snapshot(self):
        return deepcopy(self.value)


@pytest.fixture
def fallback_context(tmp_path):
    """Tiny E: authority inputs hashed by the real fallback, never parsed."""
    state_root, workspace_root = tmp_path / "state", tmp_path / "workspace"
    state_root.mkdir()
    workspace_root.mkdir()
    source_bytes = b"SYNTHETIC HASH-ONLY SOURCE; NOT A WORD DOCUMENT"
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    batch = "DESKTOPBATCH-" + "d" * 32
    visual_root = state_root / "visual-import-v2"
    source = visual_root / "sources" / (source_sha + ".docx")
    source.parent.mkdir(parents=True)
    source.write_bytes(source_bytes)
    batch_path = visual_root / "batches" / (batch + ".json")
    batch_path.parent.mkdir(parents=True)
    batch_path.write_text('{"fixture":"synthetic-import-metadata"}', encoding="utf-8")
    descriptor = {
        "kind": "desktop_visual_import_v2", "batch_id": batch,
        "visual_status": "pending", "sources": [{
            "filename": "synthetic.docx", "source_sha256": source_sha,
            "archive_relative_path": "sources/" + source_sha + ".docx",
        }],
    }
    state = _FrozenState(state_root / "desktop-state.v1.json", {"drafts": {
        "synthetic-import": descriptor, "native-word-question-ranges-v1": {"ranges": {}},
    }})
    files = {
        "attributes_db": state_root / "word-question-attributes.sqlite3",
        "attributes_wal": state_root / "word-question-attributes.sqlite3-wal",
        "taxonomy": workspace_root / "sh-chem-db/kb/knowledge_taxonomy.json",
        "directory": workspace_root / (
            "sh-chem-db/kb/classification/"
            "supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json"
        ),
        "quality": workspace_root / "knowledge/lectures/source-quality-notes.json",
    }
    for name, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic-metadata-" + name + "-r1").encode())
    facade = FakeFacade()
    facade.unified_question_source_snapshot = None
    facade.paths = SimpleNamespace(state_root=state_root, workspace_root=workspace_root)
    facade.state_store = state
    facade.word["sources"] = [{
        "source_id": source_sha, "source_sha256": source_sha,
        "source_name": "synthetic.docx", "batch_id": batch,
        "source_revision": "original-word-revision",
    }]
    context = {"facade": facade, "source": source, "source_sha": source_sha,
               "state": state, "files": files, "batch": batch}
    _replace_fallback_word_rows(context, "old")
    return context


def _replace_fallback_word_rows(context, prefix):
    facade = context["facade"]
    facade.provider = _ListingProvider()
    facade.word["items"] = []
    for suffix in ("one", "two"):
        row = facade.add_word(prefix + "-" + suffix, text="Alpha", revision=prefix + "-r1")
        row.update(source_id=context["source_sha"], source_sha256=context["source_sha"],
                   batch_id=context["batch"])
        row["attributes"]["source_sha256"] = context["source_sha"]


def _keys(result):
    return [(entry["lane"], entry["key"]) for entry in result["entries"]]


def _values(value):
    """Inspect opaque locator values without prescribing presentation keys."""
    if isinstance(value, dict):
        return [nested for item in value.values() for nested in _values(item)]
    if isinstance(value, (list, tuple)):
        return [nested for item in value for nested in _values(item)]
    return [value]


def test_five_lanes_round_robin_preserve_payloads_and_distinct_count_units():
    facade = FakeFacade()
    for lane in FORMAL_LANES:
        facade.add_formal(lane, "shared-key")
        facade.add_formal(lane, "second-key")
    facade.add_word("shared-key")
    facade.add_word("second-key")
    facade.add_visual("shared-key")
    facade.add_visual("second-key")

    result = UnifiedQuestionSearch(facade).search(limit=8)

    assert _keys(result) == [(lane, "shared-key") for lane in LANES] + [
        (lane, "second-key") for lane in FORMAL_LANES
    ]
    assert result["total"] == result["total_available"] == 10
    assert result["counts_by_unit"] == {
        "theme": 6, "word_question_segment": 2, "visual_printed_question": 2,
    }
    assert result["known_counts_by_unit"] == result["counts_by_unit"]
    assert set(result["source_status"]) == set(LANES)
    assert all(status["status"] == "ok" for status in result["source_status"].values())
    assert result["partial"] is False and result["incomplete"] is False
    assert result["next_cursor"]
    assert len({entry["entry_id"] for entry in result["entries"]}) == 8
    for entry in result["entries"]:
        assert entry["entry_id"].startswith(entry["lane"] + ":")
        assert entry["source_locator"]
        assert entry["match_evidence"] is not None
        if entry["lane"] in FORMAL_LANES:
            assert isinstance(entry["payload"], ThemeCard)
            assert entry["payload"].key == entry["key"]
            assert entry["payload"].source_identity_sha256 in _values(entry["source_locator"])
        else:
            assert entry["payload"]["key"] == entry["key"]
            assert entry["payload"]["revision"] in _values(entry["source_locator"])
    assert not facade.body_calls


def test_lane_order_is_fixed_and_public_source_selects_lanes_only():
    facade = FakeFacade()
    for lane in FORMAL_LANES:
        facade.add_formal(lane, lane)
    facade.add_word("word", source="LOCAL-WORD-SOURCE")
    facade.add_visual("visual")
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=list(reversed(LANES)), limit=8)
    assert [entry["lane"] for entry in result["entries"]] == list(LANES)

    facade = FakeFacade()
    facade.add_word("word", source="LOCAL-WORD-SOURCE")
    result = UnifiedQuestionSearch(facade).search(
        lanes=["master", "word_native", "visual_native"],
        filters={"source": ["word_native"]},
    )
    assert _keys(result) == [("word_native", "word")]
    assert facade.word_calls == 1 and facade.visual_calls == 0 and not facade.formal_calls


def test_same_lane_duplicate_keys_collapse_without_cross_lane_collapse():
    facade = FakeFacade()
    word = facade.add_word("same")
    facade.word["items"].append(deepcopy(word))
    visual = facade.add_visual("same")
    facade.visual["items"].append(deepcopy(visual))
    facade.add_formal("master", "same")
    result = UnifiedQuestionSearch(facade).search(lanes=["master", *PERSONAL_LANES])
    assert _keys(result) == [("master", "same"), ("word_native", "same"), ("visual_native", "same")]
    assert result["total"] == 3
    assert len({entry["entry_id"] for entry in result["entries"]}) == 3


def test_formal_53_item_stream_fetches_pages_on_demand_and_never_skips_or_repeats():
    facade = FakeFacade()
    for i in range(53):
        facade.add_formal("master", f"T{i:02d}")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["master"], limit=8)
    assert _keys(first) == [("master", f"T{i:02d}") for i in range(8)]
    assert first["total_available"] == first["total"] == 53
    assert len(facade.formal_calls) == 1
    assert facade.formal_calls[0]["cursor"] is None
    assert all(call["cursor"] is None for call in facade.formal_calls)
    seen = list(_keys(first))
    page = first
    while page["next_cursor"]:
        page = search.search(lanes=["master"], limit=8, cursor=page["next_cursor"])
        seen.extend(_keys(page))
    assert seen == [("master", f"T{i:02d}") for i in range(53)]
    assert any(call["cursor"] is not None for call in facade.formal_calls)
    assert not facade.body_calls
    assert facade.word_calls == facade.visual_calls == 0


def test_round_robin_cursor_continues_stream_after_short_lanes_are_exhausted():
    facade = FakeFacade()
    for key in ("M1", "M2", "M3"):
        facade.add_formal("master", key)
    facade.add_formal("wave1", "W1")
    for key in ("D1", "D2"):
        facade.add_word(key)
    facade.add_visual("P1")
    search = UnifiedQuestionSearch(facade)
    page = search.search(limit=2)
    seen = _keys(page)
    while page["next_cursor"]:
        page = search.search(limit=2, cursor=page["next_cursor"])
        seen.extend(_keys(page))
    assert seen == [
        ("master", "M1"), ("wave1", "W1"), ("word_native", "D1"),
        ("visual_native", "P1"), ("master", "M2"), ("word_native", "D2"),
        ("master", "M3"),
    ]
    assert len(seen) == page["total"] == 7


def test_formal_and_query_intersection_includes_match_beyond_first_native_page():
    facade = FakeFacade()
    for i in range(52):
        facade.add_formal("master", f"T{i:02d}", text="Alpha only")
    facade.add_formal("master", "tail-match", text="Alpha material Beta")
    result = UnifiedQuestionSearch(facade).search(lanes=["master"], query="alpha beta")
    assert _keys(result) == [("master", "tail-match")]
    assert result["total"] == 1
    assert any(call["cursor"] is not None for call in facade.formal_calls)
    assert not facade.body_calls


@pytest.mark.parametrize("lane", LANES)
def test_multiword_query_is_casefolded_and_not_required_to_be_contiguous(lane):
    facade = FakeFacade()
    add = (lambda key, **kwargs: facade.add_formal(lane, key, **kwargs)) if lane in FORMAL_LANES else (
        facade.add_word if lane == "word_native" else facade.add_visual
    )
    add("both", text="Alpha 中间共享内容 Beta")
    add("alpha", text="Alpha only")
    add("beta", text="Beta only")
    result = UnifiedQuestionSearch(facade).search(lanes=[lane], query="  ALPHA   beta  ")
    assert _keys(result) == [(lane, "both")]
    assert result["total"] == 1
    assert not facade.body_calls


def test_word_postings_are_lightweight_and_visual_answers_never_match():
    facade = FakeFacade()
    word = facade.add_word("word", text="Alpha 合成题面 Beta")
    visual = facade.add_visual("visual", text="Alpha 合成题面 Beta")
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=PERSONAL_LANES, query="alpha beta")
    assert _keys(result) == [("word_native", "word"), ("visual_native", "visual")]
    assert facade.provider.calls
    assert all(not ({"question_blocks", "context_blocks", "answer_blocks"} & set(row))
               for row in facade.word["items"])
    for entry in result["entries"]:
        assert "ONLY_ANSWER_OMEGA" not in entry["excerpt"]
    answer = search.search(lanes=PERSONAL_LANES, query="ONLY_ANSWER_OMEGA")
    assert answer["entries"] == [] and answer["total"] == 0
    assert word["revision"] == "word-r1" and visual["theme_key"] == "original-parent-theme"
    assert not facade.body_calls


def test_formal_knowledge_all_and_common_local_groups_use_theme_key_intersections():
    facade = FakeFacade()
    facade.add_formal("master", "both", knowledge=("K01", "K02"))
    facade.add_formal("master", "one", knowledge=("K01",))
    facade.add_formal("master", "two", knowledge=("K02",))
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=["master"], filters={"knowledge": ["K01", "K02"], "knowledge_mode": "all"})
    assert _keys(result) == [("master", "both")]
    assert result["total"] == 1
    assert all("knowledge" not in call["filters"] for call in facade.formal_calls)
    assert {tuple(call["filters"].get("K", [])) for call in facade.formal_calls} >= {("K01",), ("K02",)}

    grouped = search.search(lanes=["master"], filters={"knowledge": ["K01"]},
                            lane_filters={"master": {"K": ["K02"], "A": ["A01"]}})
    assert _keys(grouped) == [("master", "both")]
    assert all(call["filters"].get("A") == ["A01"] for call in facade.formal_calls[-2:])
    assert not facade.body_calls


def test_personal_common_filters_and_lane_filters_keep_same_axis_or_cross_axis_and():
    facade = FakeFacade()
    facade.add_word("word-both")
    facade.add_word("word-one", knowledge=("K01",))
    facade.add_visual("visual-both")
    facade.add_visual("visual-one", knowledge=("K01",))
    result = UnifiedQuestionSearch(facade).search(
        lanes=PERSONAL_LANES,
        filters={"knowledge": ["K01", "K02"], "knowledge_mode": "all",
                 "grade": ["grade_12"], "exam": ["second_mock"], "teaching_use": ["practice"]},
        lane_filters={"word_native": {"source": ["WORD-SOURCE"]},
                      "visual_native": {"source": ["VISUAL-SOURCE"]}},
    )
    assert _keys(result) == [("word_native", "word-both"), ("visual_native", "visual-both")]
    assert result["total"] == 2
    assert "K02" in json.dumps(result["facets"], ensure_ascii=False)


@pytest.mark.parametrize("field", ["key", "question_revision", "source_sha256"])
def test_stale_word_labels_cannot_unlock_known_knowledge_filters(field):
    facade = FakeFacade()
    stale = facade.add_word("stale")
    stale["attributes"][field] = "mismatched-current-binding"
    facade.add_word("current")
    result = UnifiedQuestionSearch(facade).search(lanes=["word_native"], filters={"knowledge": ["K02"]})
    assert _keys(result) == [("word_native", "current")]


def test_visual_matching_uses_current_facets_and_retains_original_parent_theme():
    facade = FakeFacade()
    row = facade.add_visual("printed", knowledge=("K02",))
    row["attributes"] = {"primary_knowledge": {"id": "K01"}, "question_revision": "old"}
    result = UnifiedQuestionSearch(facade).search(lanes=["visual_native"], filters={"knowledge": ["K02"]})
    (entry,) = result["entries"]
    assert entry["unit_kind"] == "visual_printed_question"
    assert entry["payload"]["theme_key"] == "original-parent-theme"
    assert entry["payload"]["theme_title"] == "原题面主题"
    assert entry["payload"]["images"] == row["images"]
    assert result["counts_by_unit"]["theme"] == 0
    assert result["counts_by_unit"]["visual_printed_question"] == 1
    assert "印刷" in entry["unit"]


@pytest.mark.parametrize("axis,value", [
    ("grade", "grade_12"), ("exam", "second_mock"), ("teaching_use", "practice"),
])
def test_unsupported_formal_filter_retains_known_personal_counts_and_unknown_total(axis, value):
    facade = FakeFacade()
    facade.add_formal("master", "formal")
    facade.add_word("word")
    result = UnifiedQuestionSearch(facade).search(lanes=["master", "word_native"], filters={axis: [value]})
    assert _keys(result) == [("word_native", "word")]
    assert result["source_status"]["master"]["status"] == "unsupported_filter"
    assert result["source_status"]["master"]["total"] is None
    assert result["total"] is None and result["total_available"] == 1
    assert result["counts_by_unit"]["theme"] is None
    assert result["known_counts_by_unit"]["theme"] == 0
    assert result["partial"] is False and result["incomplete"] is True
    assert not facade.formal_calls


def test_unavailable_source_is_partial_and_does_not_report_zero_as_known():
    facade = FakeFacade()
    facade.add_word("word")
    facade.failures["master"] = OSError("synthetic source unavailable")
    result = UnifiedQuestionSearch(facade).search(lanes=["master", "word_native"])
    assert _keys(result) == [("word_native", "word")]
    assert result["source_status"]["master"]["status"] == "unavailable"
    assert result["source_status"]["master"]["total"] is None
    assert result["total"] is None and result["total_available"] == 1
    assert result["counts_by_unit"]["theme"] is None
    assert result["partial"] is True and result["incomplete"] is True


def test_explicit_curriculum_parents_match_personal_paths_without_title_inference():
    facade = FakeFacade()
    facade.add_formal("master", "formal-v1", section="S1")
    facade.add_formal("master", "formal-v2", section="S2")
    facade.add_word("word-v1", section="S1")
    facade.add_word("word-v2", section="S2")
    unassigned = facade.add_word("word-title-only", section=None)
    unassigned["source_name"] = "合成第一册同名章同名节.docx"
    facade.add_visual("visual-v1", section="S1")
    facade.add_visual("visual-v2", section="S2")
    legacy = facade.add_visual("visual-flat-only", section=None)
    legacy["facets"]["book"] = ["V1"]
    legacy["facets"]["chapter"] = [chapter_filter_id("V1", "C1")]
    legacy["facets"]["section"] = [section_filter_id("V1", "C1", "S1")]
    selector = {"volume_id": "V1", "chapter_id": "C1", "section": "S1"}
    result = UnifiedQuestionSearch(facade).search(lanes=["master", *PERSONAL_LANES], curriculum=selector)
    assert _keys(result) == [("master", "formal-v1"), ("word_native", "word-v1"), ("visual_native", "visual-v1")]
    assert facade.formal_calls[0]["curriculum"] == selector
    assert unassigned["attributes"]["curriculum_candidates"] == []
    assert legacy["curriculum_paths"] == []


@pytest.mark.parametrize("selector", [{"chapter_id": "C1"}, {"section": "S1"},
    {"volume_id": "V1", "section": "S1"}])
def test_curriculum_children_require_explicit_parents(selector):
    with pytest.raises(UnifiedQuestionSearchError):
        UnifiedQuestionSearch(FakeFacade()).search(lanes=["master"], curriculum=selector)


def test_personal_mapping_status_is_explicitly_unsupported_not_inferred():
    facade = FakeFacade()
    facade.add_word("word")
    facade.add_visual("visual")
    result = UnifiedQuestionSearch(facade).search(
        lanes=PERSONAL_LANES, curriculum={"volume_id": "V1", "mapping_status": "confirmed"},
    )
    assert result["entries"] == [] and result["total"] is None
    assert result["total_available"] == 0
    assert all(status["status"] == "unsupported_filter" for status in result["source_status"].values())
    assert result["partial"] is False and result["incomplete"] is True


def test_empty_catalogues_and_empty_lane_selection_return_real_zero_without_fake_cards():
    facade = FakeFacade()
    result = UnifiedQuestionSearch(facade).search()
    assert result["entries"] == [] and result["next_cursor"] is None
    assert result["total"] == result["total_available"] == 0
    assert all(value == 0 for value in result["counts_by_unit"].values())
    assert all(status["status"] == "ok" for status in result["source_status"].values())
    empty = UnifiedQuestionSearch(facade).search(lanes=[])
    assert empty["entries"] == [] and empty["total"] == 0
    assert empty["source_status"] == {}


@pytest.mark.parametrize("change", [
    {"query": "different"}, {"filters": {"knowledge": ["K02"]}},
    {"lanes": ["word_native"]}, {"limit": 2},
    {"lane_filters": {"master": {"A": ["A02"]}}},
    {"curriculum": {"volume_id": "V1"}},
])
def test_cursor_is_bound_to_request(change):
    facade = FakeFacade()
    for key in ("one", "two", "three"):
        facade.add_formal("master", key)
    search = UnifiedQuestionSearch(facade)
    request = {"lanes": ["master"], "limit": 1}
    first = search.search(**request)
    assert first["next_cursor"]
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(**{**request, **change, "cursor": first["next_cursor"]})


def test_cursor_rejects_changed_formal_snapshot_and_invalidation_reloads_same_query():
    facade = FakeFacade()
    facade.add_formal("master", "one")
    facade.add_formal("master", "two")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["master"], limit=1)
    facade.formal_snapshots["master"] = "master-snapshot-2"
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["master"], limit=1, cursor=first["next_cursor"])

    facade.add_word("one", text="Alpha")
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    assert _keys(first) == [("word_native", "one")]
    assert facade.word_calls == 1
    facade.add_word("two", text="Alpha")
    facade.word["revision"] = "word-catalog-r2"
    search.invalidate(lanes=["word_native"])
    current = search.search(lanes=["word_native"], query="alpha", limit=1)
    assert current["total"] == 2 and facade.word_calls == 2


def test_invalidated_personal_projection_rejects_old_cursor_and_keeps_other_session():
    facade = FakeFacade()
    for key in ("one", "two"):
        facade.add_word(key)
        facade.add_visual(key)
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=PERSONAL_LANES, limit=1)
    assert first["next_cursor"]
    facade.word["items"][0]["attributes"]["revision"] = "attributes-r2"
    search.invalidate(lanes=["word_native"])
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=PERSONAL_LANES, limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=PERSONAL_LANES, limit=1)
    assert current["total"] == 4
    assert facade.word_calls == 2 and facade.visual_calls == 1


def test_word_epoch_rejects_old_cursor_and_provider_without_loading_visual():
    facade = FakeFacade()
    for key in ("old-one", "old-two"):
        facade.add_word(key, text="Alpha")
    facade.add_visual("unused-visual")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    assert first["next_cursor"]
    old_provider = facade.provider
    old_calls = len(old_provider.calls)

    facade.cache_epochs["word"] += 1
    facade.provider = _ListingProvider()
    facade.word["items"] = []
    facade.word["revision"] = "word-catalog-new-epoch"
    facade.add_word("new", text="Beta", revision="word-r2")
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["word_native"], query="alpha", limit=1,
                      cursor=first["next_cursor"])
    current_alpha = search.search(lanes=["word_native"], query="alpha", limit=1)
    current_beta = search.search(lanes=["word_native"], query="beta", limit=1)
    assert current_alpha["entries"] == [] and current_alpha["total"] == 0
    assert _keys(current_beta) == [("word_native", "new")]
    assert len(old_provider.calls) == old_calls
    assert facade.word_calls == 2 and facade.visual_calls == 0
    assert "word" in facade.epoch_calls
    assert not facade.body_calls


@pytest.mark.parametrize("domain", ["word", "registry"])
def test_visual_cursor_binds_both_word_and_registry_epochs(domain):
    facade = FakeFacade()
    for key in ("one", "two"):
        facade.add_visual(key)
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["visual_native"], limit=1)
    assert first["next_cursor"]
    facade.cache_epochs[domain] += 1
    facade.visual["items"] = [facade.visual["items"][1]]
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["visual_native"], limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=["visual_native"], limit=1)
    assert _keys(current) == [("visual_native", "two")]
    assert current["total"] == 1 and facade.visual_calls == 2
    assert facade.word_calls == 0
    assert {"word", "registry"}.issubset(facade.epoch_calls)


def test_formal_cursor_binds_registry_epoch_even_when_cards_and_snapshot_are_equal():
    facade = FakeFacade()
    facade.add_formal("master", "one")
    facade.add_formal("master", "two")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["master"], limit=1)
    facade.cache_epochs["registry"] += 1
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["master"], limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=["master"], limit=1)
    assert _keys(current) == [("master", "one")]
    assert current["total"] == 2
    assert facade.word_calls == facade.visual_calls == 0


def test_explicit_word_reload_with_same_epochs_preserves_visual_session():
    facade = FakeFacade()
    facade.add_word("old-word")
    facade.add_visual("same-visual")
    search = UnifiedQuestionSearch(facade)
    assert search.search(lanes=PERSONAL_LANES)["total"] == 2
    facade.word["items"] = []
    facade.add_word("new-word", revision="word-r2")
    search.invalidate(lanes=["word_native"])
    result = search.search(lanes=PERSONAL_LANES)
    assert _keys(result) == [("word_native", "new-word"), ("visual_native", "same-visual")]
    assert facade.word_calls == 2 and facade.visual_calls == 1
    assert facade.cache_epochs == {"word": 0, "registry": 0}


def test_unrelated_registry_epoch_keeps_word_cursor_and_session_valid():
    facade = FakeFacade()
    facade.add_word("one")
    facade.add_word("two")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], limit=1)
    facade.cache_epochs["registry"] += 1
    second = search.search(lanes=["word_native"], limit=1, cursor=first["next_cursor"])
    assert _keys(second) == [("word_native", "two")]
    assert facade.word_calls == 1 and facade.visual_calls == 0


@pytest.mark.parametrize("lane", PERSONAL_LANES)
@pytest.mark.parametrize("changed_axis", ["source", "ranges", "attrs", "taxonomy"])
def test_external_source_metadata_drift_rejects_cursor_and_reloads_only_selected_lane(lane, changed_axis):
    facade = FakeFacade()
    add = facade.add_word if lane == "word_native" else facade.add_visual
    for key in ("old-one", "old-two"):
        add(key, text="Alpha")
    other_lane = "visual_native" if lane == "word_native" else "word_native"
    (facade.add_visual if lane == "word_native" else facade.add_word)("unused-other")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=[lane], query="alpha", limit=1)
    assert first["next_cursor"]
    old_provider_calls = len(facade.provider.calls)
    old_provider = facade.provider

    facade.source_snapshots[lane][changed_axis] = changed_axis + "-r2"
    catalogue = facade.word if lane == "word_native" else facade.visual
    catalogue["items"] = []
    if lane == "word_native":
        facade.provider = _ListingProvider()
    for key in ("fresh-one", "fresh-two"):
        add(key, text="Alpha")
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=[lane], query="alpha", limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=[lane], query="alpha", limit=1)
    assert _keys(current) == [(lane, "fresh-one")]
    assert current["total"] == 2
    assert facade.cache_epochs == {"word": 0, "registry": 0}
    assert set(facade.snapshot_calls) == {lane}
    assert other_lane not in facade.snapshot_calls
    assert (facade.word_calls, facade.visual_calls) == ((2, 0) if lane == "word_native" else (0, 2))
    if lane == "word_native":
        assert len(old_provider.calls) == old_provider_calls
    assert not facade.body_calls


def test_ui_currentness_gate_uses_epoch_without_calling_worker_metadata_port():
    facade = FakeFacade()
    facade.add_word("word")
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=["word_native"])
    snapshot_calls = list(facade.snapshot_calls)

    def fail_metadata(*args, **kwargs):
        raise AssertionError("UI gate must not attest full source metadata")

    facade.unified_question_source_snapshot = fail_metadata
    assert search.is_current(result["version_vector"])
    facade.cache_epochs["word"] += 1
    assert not search.is_current(result["version_vector"])
    assert facade.snapshot_calls == snapshot_calls
    assert facade.word_calls == 1 and facade.visual_calls == 0
    assert not facade.body_calls


def test_fallback_source_bytes_drift_with_preserved_mtime_rejects_old_cursor(fallback_context, monkeypatch):
    context = fallback_context
    facade, source = context["facade"], context["source"]
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    old_provider = facade.provider
    old_calls = len(old_provider.calls)
    before = source.stat()
    original_stat = unified._stat
    frozen_source_stat = original_stat(source)
    content = source.read_bytes()
    source.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert source.stat().st_size == before.st_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() != context["source_sha"]
    # Freeze every cheap stat field for this source as well. The worker must
    # still notice changed bytes through its SHA attestation.
    monkeypatch.setattr(unified, "_stat", lambda path: (
        frozen_source_stat if path == source else original_stat(path)
    ))
    # A fresh source-bound listing rejects the tampered source rather than
    # presenting its old postings as current question text.
    facade.word["items"] = []
    facade.word["sources"] = []
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["word_native"], query="alpha", limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=["word_native"], query="alpha", limit=1)
    assert current["entries"] == []
    assert current["total"] is None and current["partial"] is True
    assert len(old_provider.calls) == old_calls
    assert facade.word_calls == 2 and facade.visual_calls == 0
    assert facade.cache_epochs == {"word": 0, "registry": 0}
    assert not facade.body_calls


@pytest.mark.parametrize("changed_axis", [
    "ranges", "attributes_db", "attributes_wal", "taxonomy", "directory",
])
def test_fallback_range_and_metadata_drift_invalidates_cursor_without_epoch_change(fallback_context, changed_axis):
    context = fallback_context
    facade = context["facade"]
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    old_provider = facade.provider
    old_calls = len(old_provider.calls)
    if changed_axis == "ranges":
        # Keep the state file bytes unchanged to isolate the current range
        # snapshot from the generic state-file fingerprint.
        context["state"].value["drafts"]["native-word-question-ranges-v1"] = {
            "ranges": {"old-one": {"source_revision": "original-word-revision",
                                   "range": {"block_start": 5, "question_end": 7}}},
        }
    else:
        context["files"][changed_axis].write_bytes(("synthetic-metadata-" + changed_axis + "-r2").encode())
    _replace_fallback_word_rows(context, "fresh")
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["word_native"], query="alpha", limit=1, cursor=first["next_cursor"])
    current = search.search(lanes=["word_native"], query="alpha", limit=1)
    assert _keys(current) == [("word_native", "fresh-one")]
    assert current["total"] == 2
    assert len(old_provider.calls) == old_calls
    assert facade.word_calls == 2 and facade.visual_calls == 0
    assert facade.cache_epochs == {"word": 0, "registry": 0}
    assert facade.snapshot_calls == []
    assert not facade.body_calls


def test_fallback_ui_gate_reads_stat_and_epochs_without_streaming_source_sha(fallback_context, monkeypatch):
    context = fallback_context
    facade = context["facade"]
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=["word_native"])

    def fail_sha(*args, **kwargs):
        raise AssertionError("UI currentness gate must not hash source bytes")

    monkeypatch.setattr(unified, "_file_sha", fail_sha)
    assert search.is_current(result["version_vector"])
    before = context["source"].stat()
    os.utime(context["source"], ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    assert not search.is_current(result["version_vector"])
    assert facade.word_calls == 1 and facade.visual_calls == 0
    assert not facade.body_calls


def test_fallback_missing_word_source_retains_other_lane_hits_with_unknown_total(fallback_context):
    context = fallback_context
    facade = context["facade"]
    context["source"].unlink()
    facade.word["items"] = []
    facade.word["sources"] = []
    facade.add_visual("available-visual", text="Alpha")
    result = UnifiedQuestionSearch(facade).search(lanes=PERSONAL_LANES, query="alpha")
    assert _keys(result) == [("visual_native", "available-visual")]
    assert result["total"] is None and result["total_available"] == 1
    assert result["source_status"]["word_native"]["status"] == "unavailable"
    assert result["source_status"]["word_native"]["total"] is None
    assert result["source_status"]["visual_native"]["status"] == "ok"
    assert result["partial"] is True and result["incomplete"] is True
    assert result["counts_by_unit"]["word_question_segment"] is None
    assert result["counts_by_unit"]["visual_printed_question"] == 1
    assert not facade.formal_calls and not facade.body_calls


def test_repeated_personal_queries_reuse_listing_and_invalidate_all_reloads_both():
    facade = FakeFacade()
    facade.add_word("word", text="Alpha Beta")
    facade.add_visual("visual", text="Alpha Beta")
    search = UnifiedQuestionSearch(facade)
    for query in ("alpha", "beta", "alpha beta"):
        assert search.search(lanes=PERSONAL_LANES, query=query)["total"] == 2
    assert facade.word_calls == facade.visual_calls == 1
    search.invalidate()
    assert search.search(lanes=PERSONAL_LANES, query="alpha beta")["total"] == 2
    assert facade.word_calls == facade.visual_calls == 2


@pytest.mark.parametrize("exception", [SearchCancelled("synthetic cancel"), ReadCancelled("synthetic cancel")])
def test_cancellation_propagates_and_never_publishes_source_failure(exception):
    facade = FakeFacade()
    facade.failures["word_native"] = exception
    search = UnifiedQuestionSearch(facade)
    with pytest.raises(type(exception)):
        search.search(lanes=["word_native"])
    facade.failures.clear()
    facade.add_word("current")
    assert search.search(lanes=["word_native"])["total"] == 1
    assert facade.word_calls == 2


def test_cooperative_cancel_during_terms_does_not_cache_partial_query():
    facade = FakeFacade()
    facade.add_word("both", text="Alpha Beta")
    facade.add_visual("both", text="Alpha Beta")
    search = UnifiedQuestionSearch(facade)
    stopped = False
    original_matches = facade.provider.matches

    def stop_after_posting(key, revision, query):
        nonlocal stopped
        matched = original_matches(key, revision, query)
        stopped = True
        return matched

    facade.provider.matches = stop_after_posting
    with pytest.raises((SearchCancelled, ReadCancelled)):
        search.search(lanes=PERSONAL_LANES, query="alpha beta", cancelled=lambda: stopped)
    facade.provider.matches = original_matches
    stopped = False
    result = search.search(lanes=PERSONAL_LANES, query="alpha beta")
    assert _keys(result) == [("word_native", "both"), ("visual_native", "both")]
    assert result["total"] == 2 and result["partial"] is False


def test_progress_is_cooperative_and_does_not_require_source_expansion():
    facade = FakeFacade()
    facade.add_word("word")
    events = []
    result = UnifiedQuestionSearch(facade).search(lanes=["word_native"], progress=events.append)
    assert _keys(result) == [("word_native", "word")]
    assert events and all(isinstance(event, dict) for event in events)
    assert not facade.body_calls


@pytest.mark.parametrize("invalid_request", [
    {"lanes": ["unknown"]}, {"limit": 0}, {"limit": 101},
    {"query": None}, {"filters": {"knowledge_mode": "invalid"}},
    {"cursor": "not-a-unified-cursor"},
])
def test_invalid_requests_fail_before_reading_sources(invalid_request):
    facade = FakeFacade()
    with pytest.raises(UnifiedQuestionSearchError):
        UnifiedQuestionSearch(facade).search(**invalid_request)
    assert not facade.formal_calls and facade.word_calls == facade.visual_calls == 0


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("mode, expected", [("any", ["both", "one"]), ("all", ["both"])])
def test_common_knowledge_any_all_has_the_same_source_unit_semantics(lane, mode, expected):
    facade = FakeFacade()
    add = (lambda key, **kwargs: facade.add_formal(lane, key, **kwargs)) if lane in FORMAL_LANES else (
        facade.add_word if lane == "word_native" else facade.add_visual)
    add("both", knowledge=("K01", "K02"))
    add("one", knowledge=("K01",))
    add("unrelated", knowledge=("K03",))
    result = UnifiedQuestionSearch(facade).search(lanes=[lane], filters={
        "knowledge": ["K01", "K02"], "knowledge_mode": mode})
    assert _keys(result) == [(lane, key) for key in expected]
    assert result["total"] == len(expected)
    assert not facade.body_calls


def test_custom_knowledge_tag_retains_visual_hits_and_reports_other_sources_unsupported():
    facade = FakeFacade()
    for lane in FORMAL_LANES:
        facade.add_formal(lane, "formal", knowledge=("K01",))
    facade.add_word("word")
    facade.add_visual("custom", knowledge=("custtag:teacher-owned",))
    result = UnifiedQuestionSearch(facade).search(filters={"knowledge": ["custtag:teacher-owned"]})
    assert _keys(result) == [("visual_native", "custom")]
    assert result["total"] is None and result["total_available"] == 1
    assert all(result["source_status"][lane]["status"] == "unsupported_filter"
               for lane in (*FORMAL_LANES, "word_native"))
    assert not facade.formal_calls and facade.word_calls == 0 and facade.visual_calls == 1


def test_unknown_knowledge_uses_original_unannotated_evidence_on_all_sources():
    facade = FakeFacade()
    for lane in FORMAL_LANES:
        facade.add_formal(lane, "unknown", knowledge=("unknown",))
        facade.add_formal(lane, "known", knowledge=("K01",))
    word = facade.add_word("unknown", knowledge=())
    word["attributes"]["primary_knowledge"] = {"id": "unknown", "status": "unknown"}
    facade.add_word("known", knowledge=("K01",))
    facade.add_visual("unknown", knowledge=("unknown",))
    facade.add_visual("known", knowledge=("K01",))
    result = UnifiedQuestionSearch(facade).search(filters={"knowledge": ["unknown"]})
    assert _keys(result) == [(lane, "unknown") for lane in LANES]
    assert result["total"] == 5 and result["incomplete"] is False


@pytest.mark.parametrize("lane", FORMAL_LANES)
def test_formal_source_port_versions_cover_tail_changes_and_empty_results(lane):
    facade = FakeFacade()
    for i in range(53):
        facade.add_formal(lane, f"T{i:02d}")
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=[lane], limit=1)
    # The result projection and first native page remain byte-identical.
    facade.source_snapshots[lane]["source"] = "changed-unmatched-tail-input"
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=[lane], limit=1, cursor=first["next_cursor"])
    empty_before = search.search(lanes=[lane], query="no-such-question")
    facade.source_snapshots[lane]["attrs"] = "changed-empty-result-input"
    empty_after = search.search(lanes=[lane], query="no-such-question")
    assert empty_before["entries"] == empty_after["entries"] == []
    assert empty_before["version_vector"] != empty_after["version_vector"]
    assert set(facade.snapshot_calls) == {lane}
    assert facade.word_calls == facade.visual_calls == 0


@pytest.fixture
def formal_fallback_context(tmp_path):
    root = tmp_path / "sh-chem-db"
    product = root / "kb/fixture-wave"
    product.mkdir(parents=True)
    source = root / "kb/fixture-source.docx"
    source.write_bytes(b"HASH-ONLY FIXTURE; NOT A WORD DOCUMENT")
    output = product / "question_candidates.jsonl"
    output.write_text('{"synthetic":"compact"}\n', encoding="utf-8")
    manifest = product / "manifest.json"
    manifest.write_text(json.dumps({
        "output_files": [{"path": output.name, "sha256": "fixture-declared-output"}],
        "source_bindings": [{"path": "kb/fixture-source.docx", "sha256": "fixture-declared-source"}],
    }), encoding="utf-8")
    facade = FakeFacade()
    facade.unified_question_source_snapshot = None
    facade._themes = SimpleNamespace(shchem_root=root,
        wave_scans=SimpleNamespace(_batch_readers=(SimpleNamespace(product_root=product),)),
        wave_review=SimpleNamespace(batch_root=product / "review"))
    for key in ("one", "two"):
        facade.add_formal("wave1", key)
    return {"facade": facade, "root": root, "product": product,
            "source": source, "output": output, "manifest": manifest}


def test_formal_worker_hashes_declared_source_bytes_even_when_every_stat_is_frozen(formal_fallback_context, monkeypatch):
    context = formal_fallback_context
    facade, source = context["facade"], context["source"]
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["wave1"], limit=1)
    assert first["version_vector"]["wave1"]["source_snapshot"]["kind"] == "formal_source_sha256_worker"
    original_stat = unified._stat
    frozen = original_stat(source)
    monkeypatch.setattr(unified, "_stat", lambda path: frozen if path == source else original_stat(path))
    raw = source.read_bytes()
    source.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["wave1"], limit=1, cursor=first["next_cursor"])
    assert facade.word_calls == facade.visual_calls == 0 and not facade.body_calls


def test_formal_ui_gate_checks_declared_input_stats_without_reading_source_bytes(formal_fallback_context, monkeypatch):
    context = formal_fallback_context
    search = UnifiedQuestionSearch(context["facade"])
    result = search.search(lanes=["wave1"])
    monkeypatch.setattr(unified, "_file_sha", lambda *args: pytest.fail("GUI gate must not stream SHA"))
    assert search.is_current(result["version_vector"])
    source = context["source"]
    before = source.stat()
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    assert not search.is_current(result["version_vector"])


def test_control_manifest_binding_shapes_keep_explicit_source_roots(formal_fallback_context):
    context = formal_fallback_context
    root, product = context["root"], context["product"]
    workspace_source = root.parent / "staging/source.dat"
    workspace_source.parent.mkdir()
    workspace_source.write_bytes(b"WORKSPACE SOURCE")
    compact_asset = root / "kb/evidence.jsonl"
    compact_asset.write_bytes(b"COMPACT EVIDENCE")
    candidate = product / "candidate_manifest.json"
    candidate.write_text(json.dumps({
        "candidate_file": "kb/fixture-wave/question_candidates.jsonl",
        "source_assets": [{"asset": "kb/evidence.jsonl", "source_asset": "kb/fixture-source.docx"}],
        "upstream_theme_evidence": {"bindings": [{"asset": "staging/source.dat"}]},
    }), encoding="utf-8")
    (product / "source_manifest.json").write_text(json.dumps({
        "files": [{"path": "staging/source.dat"}],
        "protected_inputs": [{"path": "kb/evidence.jsonl"}],
    }), encoding="utf-8")
    metadata, files = unified._source_metadata(context["facade"], "wave1", lambda: False, None)
    assert {workspace_source, compact_asset, context["source"], context["output"], candidate}.issubset(set(files))
    assert metadata["kind"] == "formal_source_sha256_worker"
    assert not context["facade"].formal_calls


def test_source_set_change_after_an_earlier_file_was_hashed_cancels_the_whole_attestation(tmp_path, monkeypatch):
    one, two = tmp_path / "one.bin", tmp_path / "two.bin"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    original = unified._file_sha
    def mutate_earlier(path, cancelled):
        digest = original(path, cancelled)
        if path == two:
            one.write_bytes(b"new earlier input")
        return digest
    monkeypatch.setattr(unified, "_file_sha", mutate_earlier)
    with pytest.raises(SearchCancelled):
        unified._authority_hashes({one, two}, "word_native", lambda: False, None)


def test_discovery_manifest_change_before_sha_pass_cancels(formal_fallback_context, monkeypatch):
    context = formal_fallback_context
    original = unified._authority_hashes
    def mutate_discovery(files, lane, cancelled, progress, discovered=None):
        context["manifest"].write_text('{"changed":"after-file-discovery"}', encoding="utf-8")
        return original(files, lane, cancelled, progress, discovered)
    monkeypatch.setattr(unified, "_authority_hashes", mutate_discovery)
    with pytest.raises(SearchCancelled):
        UnifiedQuestionSearch(context["facade"]).search(lanes=["wave1"])


def test_cyclic_native_cursor_fails_before_an_infinite_empty_page_loop():
    facade = FakeFacade()
    facade.add_formal("master", "first")
    original = facade.search_themes
    def cycling(**kwargs):
        result = original(**{**kwargs, "cursor": None})
        return replace(result, cards=result.cards if kwargs.get("cursor") is None else (),
                       total_themes=100, has_more=True,
                       next_cursor="cycle:b" if kwargs.get("cursor") == "cycle:a" else "cycle:a")
    facade.search_themes = cycling
    with pytest.raises(UnifiedQuestionSearchError, match="分页没有继续前进"):
        UnifiedQuestionSearch(facade).search(lanes=["master"], limit=8)
    assert len(facade.formal_calls) == 3


def test_invalidation_during_entry_projection_cannot_publish_or_restore_old_provider(monkeypatch):
    facade = FakeFacade()
    facade.add_word("old")
    search = UnifiedQuestionSearch(facade)
    original = search._entry
    def invalidate_late(*args):
        entry = original(*args)
        search.invalidate(["word_native"])
        return entry
    monkeypatch.setattr(search, "_entry", invalidate_late)
    with pytest.raises(SearchCancelled):
        search.search(lanes=["word_native"])
    assert search._searches == {} and "word_native" not in search._personal
    monkeypatch.setattr(search, "_entry", original)
    facade.word["items"] = []
    facade.provider = _ListingProvider()
    facade.add_word("current")
    assert _keys(search.search(lanes=["word_native"])) == [("word_native", "current")]


def test_gui_currentness_does_not_wait_for_worker_search_lock():
    facade = FakeFacade()
    facade.add_word("current")
    search = UnifiedQuestionSearch(facade)
    result = search.search(lanes=["word_native"])
    locked, release, completed = Event(), Event(), Event()
    def hold_worker_lock():
        with search._lock:
            locked.set()
            release.wait(2)
    worker = Thread(target=hold_worker_lock)
    worker.start()
    assert locked.wait(1)
    outcome = []
    def gate():
        outcome.append(search.is_current(result["version_vector"]))
        completed.set()
    gui = Thread(target=gate)
    gui.start()
    try:
        assert completed.wait(.5), "GUI gate blocked behind worker I/O"
        assert outcome == [True]
    finally:
        release.set()
        worker.join(2)
        gui.join(2)


@pytest.mark.parametrize("lane", LANES)
def test_source_verification_progress_cancel_is_never_reported_as_source_failure(lane):
    facade = FakeFacade()
    search = UnifiedQuestionSearch(facade)
    stopped = False
    events = []
    def progress(event):
        nonlocal stopped
        events.append(event)
        stopped = True
    with pytest.raises(SearchCancelled):
        search.search(lanes=[lane], progress=progress, cancelled=lambda: stopped)
    assert events and events[0]["lane"] == lane
    assert search._searches == {}
    assert not facade.formal_calls and facade.word_calls == facade.visual_calls == 0


@pytest.mark.parametrize("lane", LANES)
def test_unavailable_authority_cannot_reuse_or_publish_cached_cards_or_old_postings(lane):
    facade = FakeFacade()
    add = (lambda key, **kwargs: facade.add_formal(lane, key, **kwargs)) if lane in FORMAL_LANES else (
        facade.add_word if lane == "word_native" else facade.add_visual)
    add("old", text="Alpha")
    search = UnifiedQuestionSearch(facade)
    assert search.search(lanes=[lane], query="alpha")["total"] == 1
    calls = (len(facade.formal_calls), facade.word_calls, facade.visual_calls, len(facade.provider.calls))
    def authority_unavailable(*args, **kwargs):
        raise OSError("synthetic authority inaccessible")
    facade.unified_question_source_snapshot = authority_unavailable
    result = search.search(lanes=[lane], query="alpha")
    assert result["entries"] == [] and result["total"] is None
    assert result["source_status"][lane]["status"] == "unavailable"
    assert result["source_status"][lane]["total"] is None
    assert result["total_available"] == 0 and result["partial"] is True
    assert lane not in search._personal
    assert calls == (len(facade.formal_calls), facade.word_calls, facade.visual_calls, len(facade.provider.calls))


def test_unrelated_basket_and_task_state_writes_keep_current_cursor_and_postings(fallback_context, monkeypatch):
    context = fallback_context
    facade, state = context["facade"], context["state"]
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    state.value.update(basket=[{"key": "ordinary-basket-write"}], tasks={"task": {"status": "saved"}})
    state.path.write_text(json.dumps(state.value), encoding="utf-8")
    # The GUI comparison must not call the state-store's lock-taking port or
    # source hash function, even when its broad state-file stat changed.
    original_snapshot = state.snapshot
    monkeypatch.setattr(state, "snapshot", lambda: pytest.fail("GUI gate must not take state snapshot lock"))
    with monkeypatch.context() as patch:
        patch.setattr(unified, "_file_sha", lambda *args: pytest.fail("GUI gate must not stream source SHA"))
        assert search.is_current(first["version_vector"])
    monkeypatch.setattr(state, "snapshot", original_snapshot)
    second = search.search(lanes=["word_native"], query="alpha", limit=1, cursor=first["next_cursor"])
    assert _keys(second) == [("word_native", "old-two")]
    assert second["version_vector"] == first["version_vector"]
    assert facade.word_calls == 1


@pytest.mark.parametrize("field", ["ranges", "descriptors"])
def test_relevant_state_writes_reject_gui_result_and_cursor(fallback_context, field):
    context = fallback_context
    facade, state = context["facade"], context["state"]
    search = UnifiedQuestionSearch(facade)
    first = search.search(lanes=["word_native"], query="alpha", limit=1)
    if field == "ranges":
        state.value["drafts"]["native-word-question-ranges-v1"] = {"ranges": {"changed": "range-r2"}}
    else:
        state.value["drafts"]["synthetic-import"]["sources"][0]["filename"] = "revised-synthetic.docx"
    state.path.write_text(json.dumps(state.value), encoding="utf-8")
    assert not search.is_current(first["version_vector"])
    with pytest.raises(UnifiedQuestionSearchError):
        search.search(lanes=["word_native"], query="alpha", limit=1, cursor=first["next_cursor"])


def test_changed_state_gate_rejects_oversized_or_unparseable_metadata_without_a_snapshot_lock(fallback_context, monkeypatch):
    context = fallback_context
    search = UnifiedQuestionSearch(context["facade"])
    first = search.search(lanes=["word_native"])
    state = context["state"]
    monkeypatch.setattr(state, "snapshot", lambda: pytest.fail("GUI gate must not take snapshot lock"))
    state.path.write_text("INVALID JSON", encoding="utf-8")
    assert not search.is_current(first["version_vector"])
    state.path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    assert not search.is_current(first["version_vector"])


@pytest.mark.parametrize("lane", ["master", *PERSONAL_LANES])
def test_cancellation_callback_reaches_native_reader_checkpoints_without_progress(lane):
    facade = FakeFacade()
    stopped = False
    def native_read(*args, **kwargs):
        nonlocal stopped
        stopped = True
        check_read_cancelled()
        pytest.fail("native read checkpoint ignored its search stop signal")
    if lane == "master":
        facade.search_themes = native_read
    elif lane == "word_native":
        facade.word_question_listing = native_read
    else:
        facade.personal_visual_questions = native_read
    search = UnifiedQuestionSearch(facade)
    with pytest.raises(ReadCancelled):
        search.search(lanes=[lane], cancelled=lambda: stopped)
    assert search._searches == {} and lane not in search._personal
