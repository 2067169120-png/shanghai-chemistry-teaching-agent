"""Pure lexical lesson navigation; no files, personal state, or model calls."""

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_recommendations import (
    lesson_knowledge_suggestions,
)


def _point(knowledge_id="K10", label="电解质与电离", status="auto_suggested"):
    return {"id": knowledge_id, "label": label, "status": status}


def _item(key="q1", primary=None, supporting=None, *, ready=True):
    return {
        "key": key,
        "revision": "revision-" + key,
        "source_sha256": "a" * 64,
        "selection_ready": ready,
        "selection_unit": "theme_big_question",
        "printed_subpart_starts": [2, 5, 7],
        "context_blocks": [{"text": "保留整段共同材料"}],
        "attributes": {
            "question_revision": "revision-" + key,
            "source_sha256": "a" * 64,
            "primary_knowledge": primary or _point(),
            "supporting_knowledge": supporting or [],
            "annotation_source": "teacher_modified",
        },
    }


def test_whole_component_match_counts_whole_units_and_keeps_input_unchanged():
    items = [
        _item(),
        _item("q2", _point("K11", "氧化还原与电化学"), [_point()], ready=False),
    ]
    before = deepcopy(items)
    result = lesson_knowledge_suggestions("第2课时：电离平衡常数", items)
    assert len(result) == 1
    suggestion = result[0]
    assert suggestion["knowledge_id"] == "K10"
    assert suggestion["matched_terms"] == ["电离"]
    assert suggestion["primary_count"] == 1
    assert suggestion["supporting_count"] == 1
    assert suggestion["question_count"] == 2  # Not six embedded subparts.
    assert suggestion["selectable_count"] == 1
    assert suggestion["status_counts"] == {"auto_suggested": 2}
    assert items == before


@pytest.mark.parametrize(
    "topic", ["", "   ", None, "物质研究", "原电池", "电", "教师讲义.docx"]
)
def test_no_fuzzy_synonym_filename_or_partial_character_matches(topic):
    item = _item()
    item.update(title="原电池", source_name="教师讲义.docx", chapter="物质研究")
    item["question_blocks"] = [{"text": "原电池"}]
    item["answer_blocks"] = [{"text": "物质研究"}]
    assert lesson_knowledge_suggestions(topic, [item]) == []


def test_normalization_is_only_width_case_and_whitespace():
    item = _item(primary=_point("K01", "pH与物质分类"))
    assert lesson_knowledge_suggestions("ＰＨ 的 比较", [item])[0]["matched_terms"] == [
        "pH"
    ]
    assert lesson_knowledge_suggestions("alphabet", [item]) == []


def test_single_character_name_only_matches_the_exact_title():
    item = _item(primary=_point("K16", "烃"))
    assert lesson_knowledge_suggestions("烃", [item])[0]["knowledge_id"] == "K16"
    assert lesson_knowledge_suggestions("卤代烃", [item]) == []


def test_stale_binding_and_unknown_labels_are_not_recommended():
    stale_revision, stale_source, unknown = _item("q1"), _item("q2"), _item("q3")
    stale_revision["attributes"]["question_revision"] = "old"
    stale_source["attributes"]["source_sha256"] = "b" * 64
    unknown["attributes"]["primary_knowledge"] = _point("unknown", "电离")
    assert (
        lesson_knowledge_suggestions("电离", [stale_revision, stale_source, unknown])
        == []
    )


def test_status_is_each_label_status_not_whole_row_teacher_modified_flag():
    items = [
        _item(),
        _item("q2", _point(status="teacher_confirmed")),
        _item("q3", _point(status="unknown")),
    ]
    suggestion = lesson_knowledge_suggestions("电离", items)[0]
    assert suggestion["status_counts"] == {
        "auto_suggested": 1,
        "teacher_confirmed": 1,
        "unknown": 1,
    }


def test_same_id_different_names_count_the_whole_filter_not_only_matching_name():
    items = [_item(), _item("q2", _point(label="水溶液中的离子反应与平衡"))]
    suggestion = lesson_knowledge_suggestions("电离", items)[0]
    assert suggestion["question_count"] == 2
    assert suggestion["primary_count"] == 2
    assert suggestion["label"] == "电解质与电离"


def test_order_is_deterministic_and_exact_names_rank_first():
    items = [
        _item("q1", _point("K11", "氧化还原与电化学")),
        _item("q2", _point("K10", "电化学")),
    ]
    result = lesson_knowledge_suggestions("电化学", items)
    assert [row["knowledge_id"] for row in result] == ["K10", "K11"]
    assert lesson_knowledge_suggestions("电化学", reversed(items)) == result


def test_malformed_rows_do_not_hide_other_current_labels():
    item = _item()
    result = lesson_knowledge_suggestions(
        "电离", [None, {}, {"key": "q2", "attributes": []}, item]
    )
    assert len(result) == 1 and result[0]["question_count"] == 1


def test_duplicate_input_and_multiple_roles_do_not_inflate_question_count():
    item = _item(supporting=[_point()])
    result = lesson_knowledge_suggestions("电离", [item, deepcopy(item)])[0]
    assert (
        result["question_count"]
        == result["primary_count"]
        == result["supporting_count"]
        == 1
    )
