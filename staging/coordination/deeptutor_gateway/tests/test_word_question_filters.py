"""Synthetic stable-ID multi-select and curriculum path invariants; no IO."""

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_filters import (
    FILTER_GROUPS,
    UNKNOWN_ID,
    chapter_filter_id,
    compile_filter_options,
    matches_question,
    section_filter_id,
)


def _catalog():
    return {
        "knowledge_points": [],
        "nodes": [
            {
                "node_key": "S1",
                "volume_id": "V1",
                "volume_title": "第一册",
                "chapter_id": "C1",
                "chapter_title": "同名章",
                "section_title": "同名节",
                "section_number": "1.1",
            },
            {
                "node_key": "S2",
                "volume_id": "V2",
                "volume_title": "第二册",
                "chapter_id": "C1",
                "chapter_title": "同名章",
                "section_title": "同名节",
                "section_number": "1.1",
            },
            {
                "node_key": "S3",
                "volume_id": "V1",
                "volume_title": "第一册",
                "chapter_id": "C2",
                "chapter_title": "下一章",
                "section_title": "新节",
                "section_number": "2.1",
            },
        ],
    }


def _row(
    *,
    key="Q1",
    source="SRC1",
    mappings=("S1",),
    knowledge=("K09", "K11"),
    grades=("grade_11", "grade_12"),
    exam="first_mock",
):
    row = {
        "key": key,
        "revision": "q-revision",
        "source_id": source,
        "source_sha256": "a" * 64,
        "source_name": "同名讲义.docx",
        "source_label": "合成练习",
        "question_blocks": [{"text": "原题题干 ALPHA"}],
        "context_blocks": [{"text": "公共材料 CONTEXT"}],
        "answer_blocks": [{"text": "仅答案中的 OMEGA 关键词"}],
    }
    candidates = []
    for section in mappings:
        node = next(n for n in _catalog()["nodes"] if n["node_key"] == section)
        candidates.append(
            {
                "section_key": section,
                "volume_id": node["volume_id"],
                "chapter_id": node["chapter_id"],
                "status": "auto_suggested",
            }
        )
    points = [
        {"id": value, "label": "知识" + value, "status": "auto_suggested"}
        for value in knowledge
    ]
    row["attributes"] = {
        "key": key,
        "source_sha256": row["source_sha256"],
        "question_revision": row["revision"],
        "primary_knowledge": points[0]
        if points
        else {"id": "unknown", "status": "unknown"},
        "supporting_knowledge": points[1:],
        "applicable_grades": {"values": list(grades), "status": "auto_suggested"},
        "original_source": {
            "exam_type": {"value": exam, "status": "source_observed"},
            "display_label": "合成来源",
        },
        "curriculum_status": "auto_suggested",
        "curriculum_candidates": candidates,
        "source": {"package_id": "SYNTHETIC-PACK"},
    }
    return row


def test_options_use_stable_identity_keep_same_titles_separate_and_include_unknown():
    rows = [_row(), _row(key="Q2", source="SRC2", mappings=("S2",), exam="second_mock")]
    result = compile_filter_options(rows, _catalog())
    assert result["catalog_valid"] and not result["warnings"]
    assert set(result["groups"]) == set(FILTER_GROUPS)
    for values in result["groups"].values():
        assert len(values) == len({value["id"] for value in values})
        assert any(value["id"] == UNKNOWN_ID for value in values)
    sources = result["groups"]["source"]
    assert {value["id"] for value in sources} == {"SRC1", "SRC2", UNKNOWN_ID}
    chapters = {value["id"]: value for value in result["groups"]["chapter"]}
    first, second = chapter_filter_id("V1", "C1"), chapter_filter_id("V2", "C1")
    assert first != second
    assert chapters[first]["volume_id"] == "V1"
    assert chapters[second]["volume_id"] == "V2"
    assert chapters[first]["chapter_id"] == chapters[second]["chapter_id"] == "C1"
    assert chapters[first]["label"] != chapters[second]["label"]
    section = next(
        value
        for value in result["groups"]["section"]
        if value["id"] == section_filter_id("V2", "C1", "S2")
    )
    assert section["section_key"] == "S2" and section["volume_id"] == "V2"
    assert "任意一个" in result["rule_zh"] and "同时满足" in result["rule_zh"]


def test_same_group_or_different_groups_and_and_primary_supporting_equivalence():
    row = _row()
    selection = {
        "source": {"SRC1", "SRC2"},
        "knowledge": {"K11", "K16"},
        "grade": {"grade_10", "grade_12"},
        "exam": {"first_mock", "school_exam"},
    }
    assert matches_question(row, selection, _catalog())
    assert not matches_question(row, {**selection, "exam": {"second_mock"}}, _catalog())
    assert not matches_question(row, {**selection, "knowledge": {"K16"}}, _catalog())
    assert matches_question(row, {"knowledge": {"K09"}}, _catalog())
    assert matches_question(row, {"knowledge": {"K11"}}, _catalog())


def test_optional_all_knowledge_requires_complete_primary_supporting_union():
    row = _row()
    assert matches_question(
        row, {"knowledge": {"K09", "K11"}, "knowledge_mode": "all"}, _catalog()
    )
    assert not matches_question(
        row, {"knowledge": {"K09", "K16"}, "knowledge_mode": "all"}, _catalog()
    )
    assert matches_question(row, {"knowledge": {"K09", "K16"}}, _catalog())
    assert matches_question(
        row, {"knowledge": set(), "knowledge_mode": "all"}, _catalog()
    )
    assert not matches_question(row, {"knowledge_mode": "invalid"}, _catalog())


def test_cross_book_or_paths_are_legal_but_candidates_cannot_cross_mix():
    row = _row(mappings=("S1", "S2"))
    chapters = {chapter_filter_id("V1", "C1"), chapter_filter_id("V2", "C1")}
    assert matches_question(
        row, {"book": {"V1", "V2"}, "chapter": chapters}, _catalog()
    )
    assert matches_question(
        _row(mappings=("S2",)), {"book": {"V1", "V2"}, "chapter": chapters}, _catalog()
    )
    assert not matches_question(
        row, {"book": {"V1"}, "chapter": {chapter_filter_id("V2", "C1")}}, _catalog()
    )
    assert not matches_question(
        row,
        {
            "chapter": {chapter_filter_id("V1", "C1")},
            "section": {section_filter_id("V2", "C1", "S2")},
        },
        _catalog(),
    )
    assert matches_question(
        row,
        {
            "book": {"V2"},
            "chapter": {chapter_filter_id("V2", "C1")},
            "section": {section_filter_id("V2", "C1", "S2")},
        },
        _catalog(),
    )


@pytest.mark.parametrize(
    "field,value",
    [("key", "OTHER"), ("question_revision", "old"), ("source_sha256", "b" * 64)],
)
def test_stale_or_cross_source_attributes_are_only_unknown(field, value):
    row = _row()
    row["attributes"][field] = value
    options = compile_filter_options([row], _catalog())
    assert [option["id"] for option in options["groups"]["knowledge"]] == [UNKNOWN_ID]
    assert not matches_question(row, {"knowledge": {"K11"}}, _catalog())
    assert not matches_question(row, {"book": {"V1"}}, _catalog())
    selection = {group: {UNKNOWN_ID} for group in FILTER_GROUPS if group != "source"}
    assert matches_question(row, selection, _catalog())
    assert matches_question(row, {"source": {"SRC1"}}, _catalog())


@pytest.mark.parametrize(
    "catalog",
    [None, {}, {"nodes": []}, {"nodes": "invalid"}, {"nodes": [{}]}, {"nodes": [None]}],
)
def test_unavailable_directory_retains_all_questions_and_unknown_curriculum(catalog):
    row = _row()
    result = compile_filter_options([row], catalog)
    assert not result["catalog_valid"] and result["warnings"]
    for group in ("book", "chapter", "section"):
        assert [option["id"] for option in result["groups"][group]] == [UNKNOWN_ID]
    assert matches_question(row, {}, catalog)
    assert matches_question(row, {"knowledge": {"K11"}}, catalog)
    assert matches_question(row, {"book": {UNKNOWN_ID}}, catalog)
    assert not matches_question(row, {"book": {"V1"}}, catalog)


def test_unlabelled_unknown_or_known_selection_and_unknown_all_behavior():
    row = _row()
    row.pop("attributes")
    assert matches_question(row, {}, _catalog())
    assert matches_question(row, {"knowledge": {UNKNOWN_ID, "K11"}}, _catalog())
    assert not matches_question(
        row, {"knowledge": {UNKNOWN_ID, "K11"}, "knowledge_mode": "all"}, _catalog()
    )
    assert matches_question(
        row, {"knowledge": {UNKNOWN_ID}, "knowledge_mode": "all"}, _catalog()
    )
    assert not matches_question(_row(), {"knowledge": {UNKNOWN_ID}}, _catalog())


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("volume_id", "V2"),
        ("chapter_id", "C2"),
        ("section_key", "MISSING"),
        ("status", "unknown"),
    ],
)
def test_candidate_parent_identity_and_status_are_verified(bad_field, bad_value):
    row = _row()
    row["attributes"]["curriculum_candidates"][0][bad_field] = bad_value
    assert not matches_question(row, {"book": {"V1"}}, _catalog())
    assert matches_question(row, {"book": {UNKNOWN_ID}}, _catalog())


def test_pending_mapping_cannot_become_known_by_filename_chapter_or_question_text():
    row = _row()
    row["attributes"]["curriculum_status"] = "pending_mapping"
    row["source_name"] = "第一册同名章.docx"
    row["chapter"] = "同名章"
    row["question_blocks"] = [{"text": "同名节 S1 V1 C1"}]
    assert matches_question(row, {"book": {UNKNOWN_ID}}, _catalog())
    assert not matches_question(row, {"book": {"V1"}}, _catalog())


def test_contradictory_directory_parents_fail_closed_without_hiding_questions():
    catalog = _catalog()
    conflict = deepcopy(catalog["nodes"][0])
    conflict["volume_id"] = "V2"
    catalog["nodes"].append(conflict)
    assert not compile_filter_options([_row()], catalog)["catalog_valid"]
    assert matches_question(_row(), {}, catalog)
    assert matches_question(_row(), {"section": {UNKNOWN_ID}}, catalog)


def test_search_uses_question_context_and_valid_labels_not_answer_or_evidence():
    row = _row()
    row["attributes"]["teacher_note"] = "教师答案 SECRET"
    row["attributes"]["primary_knowledge"]["evidence"] = [{"quote": "证据答案 HIDDEN"}]
    assert matches_question(row, {"query": "alpha"}, _catalog())
    assert matches_question(row, {"query": " context "}, _catalog())
    assert matches_question(row, {"query": "SYNTHETIC-PACK"}, _catalog())
    assert matches_question(row, {"query": "第一册"}, _catalog())
    for query in ("OMEGA", "SECRET", "HIDDEN"):
        assert not matches_question(row, {"query": query}, _catalog())
    assert not matches_question(
        row, {"query": "alpha", "exam": {"second_mock"}}, _catalog()
    )


def test_inputs_are_immutable_and_composite_ids_do_not_collide():
    rows, catalog = [_row()], _catalog()
    selection = {"book": {"V1"}, "knowledge": {"K09", "K11"}, "knowledge_mode": "all"}
    original = deepcopy((rows, catalog, selection))
    compile_filter_options(rows, catalog)
    assert matches_question(rows[0], selection, catalog)
    assert (rows, catalog, selection) == original
    assert chapter_filter_id("a:b", "c") != chapter_filter_id("a", "b:c")
    assert section_filter_id("a", "b:c", "d") != section_filter_id("a:b", "c", "d")


@pytest.mark.parametrize(
    "selection",
    [
        {"source": [None]},
        {"grade": 4},
        {"query": None},
        {"knowledge": {"not-an-id"}},
        {"book": {"same title"}},
    ],
)
def test_invalid_or_nonexistent_selection_does_not_widen_results(selection):
    assert not matches_question(_row(), selection, _catalog())


def test_missing_items_and_missing_attributes_are_safe():
    result = compile_filter_options(None, None)
    assert all(len(options) == 1 for options in result["groups"].values())
    row = {"question_blocks": [{"text": "仍能浏览"}]}
    assert matches_question(row, {}, None)
    assert matches_question(row, {group: {UNKNOWN_ID} for group in FILTER_GROUPS}, None)


@pytest.mark.parametrize("distinct_editions", [True, False])
def test_same_book_titles_show_explicit_edition_or_stable_id(distinct_editions):
    catalog = _catalog()
    for node in catalog["nodes"]:
        node["volume_title"] = "必修第一册"
        node["edition_title"] = (
            ("甲版" if node["volume_id"] == "V1" else "乙版")
            if distinct_editions
            else "同版"
        )
    result = compile_filter_options([_row()], catalog)
    books = {row["id"]: row["label"] for row in result["groups"]["book"]}
    assert books["V1"] != books["V2"]
    if distinct_editions:
        assert "甲版" in books["V1"] and "乙版" in books["V2"]
    else:
        assert "ID：V1" in books["V1"] and "ID：V2" in books["V2"]
    for group in ("chapter", "section"):
        for option in result["groups"][group]:
            if option["id"] != UNKNOWN_ID:
                assert books[option["volume_id"]] in option["label"]


def test_native_title_is_searchable_without_replacing_bilingual_title_support():
    row = _row()
    row["title"] = "Native title"
    row["title_zh"] = "兼容标题"
    assert matches_question(row, {"query": "native title"}, _catalog())
    assert matches_question(row, {"query": "兼容标题"}, _catalog())


def test_malformed_status_values_are_unknown_instead_of_crashing():
    row = _row(knowledge=("K09",))
    row["attributes"]["primary_knowledge"]["status"] = []
    row["attributes"]["curriculum_status"] = []
    row["attributes"]["applicable_grades"]["status"] = []
    row["attributes"]["original_source"]["exam_type"]["status"] = []
    assert matches_question(row, {}, _catalog())
    assert matches_question(
        row,
        {
            "book": {UNKNOWN_ID},
            "knowledge": {UNKNOWN_ID},
            "exam": {UNKNOWN_ID},
            "grade": {UNKNOWN_ID},
        },
        _catalog(),
    )
    assert compile_filter_options([row], _catalog())["catalog_valid"]
