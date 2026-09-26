from copy import deepcopy
from types import SimpleNamespace

from integrations.deeptutor_shchem_v1.desktop_library_progress import (
    collect_library_progress, summarize_word_catalog,
)


def sample():
    return {"key": "q1", "revision": "v1", "source_sha256": "a" * 64,
            "source_name": "合成.docx", "source_id": "s1", "title": "原文印刷题标题", "attributes": {
                "key": "q1",
                "source_sha256": "a" * 64, "question_revision": "v1",
                "primary_knowledge": {"id": "K01", "status": "auto_suggested"},
                "curriculum_status": "auto_suggested",
                "curriculum_candidates": [{"section_key": "s1", "volume_id": "v", "chapter_id": "c", "status": "auto_suggested"}],
                "applicable_grades": {"values": ["grade_11"], "status": "usage_positioning"},
                "original_source": {"exam_type": {"value": "second_mock", "status": "source_observed"}},
            }}


def catalog(*items):
    return {"items": list(items), "sources": [{"source_id": "s1"}], "attribute_catalog": {
        "nodes": [{"node_key": "s1", "volume_id": "v", "chapter_id": "c"}]}}


def test_complete_fields_are_not_teacher_approval():
    item = sample()
    before = deepcopy(item)
    result = summarize_word_catalog(catalog(item))
    assert result["counts"]["complete_candidates"] == 1
    assert "teacher_reviewed" not in result["rows"][0]
    assert item == before


def test_stale_labels_do_not_count():
    item = sample()
    item["attributes"]["question_revision"] = "old"
    result = summarize_word_catalog(catalog(item))
    assert result["counts"]["primary"] == 0
    assert result["counts"]["stale_labels"] == 1
    assert "重核旧标签" in result["rows"][0]["todo"]


def test_unknown_exam_remains_a_gap():
    item = sample()
    item["attributes"]["original_source"]["exam_type"]["value"] = "unknown"
    result = summarize_word_catalog(catalog(item))
    assert result["counts"]["complete_candidates"] == 0
    assert "原考试类型" in result["rows"][0]["todo"]


def test_duplicates_and_missing_attributes():
    item = sample()
    other = {"key": "q2", "source_name": "other"}
    result = summarize_word_catalog(catalog(item, item, other))
    assert result["counts"]["candidates"] == 2
    assert result["counts"]["primary"] == 1
    assert result["warnings"]


def test_material_gaps_remain_visible():
    item = sample()
    item["attributes"]["material_status"] = {"missing_context": True}
    result = summarize_word_catalog(catalog(item))
    assert result["counts"]["material_gaps"] == 1


def test_unavailable_is_not_zero_or_cross_bank_sum():
    def failed():
        raise OSError("private path must not leak")
    facade = SimpleNamespace(word_question_catalog=failed,
        personal_visual_questions=lambda: {"items": [], "warnings": []})
    report = collect_library_progress(facade)
    assert report["word"] is None
    assert report["visual"]["printed_questions"] == 0
    assert len(report["errors"]) == 1
    assert "private path" not in str(report)
    assert "total_questions" not in report


def test_progress_keeps_exact_identity_and_observed_title_without_inferring_exam():
    item = sample()
    item["source_name"] = "2025-二模-高三.docx"
    item["attributes"]["original_source"]["exam_type"] = {"value": "unknown", "status": "unknown"}
    item["question_blocks"] = [{"text": "合成题干", "assets": [{"id": "figure"}]}]
    item["answer_blocks"] = [{"text": "私有参考答案，不应出现在进度摘要中"}]
    row = summarize_word_catalog(catalog(item))["rows"][0]
    assert (row["key"], row["source_id"], row["source_sha256"], row["revision"]) == ("q1", "s1", "a" * 64, "v1")
    assert row["question_title"] == "原文印刷题标题"
    assert row["excerpt"] == "合成题干"
    assert row["has_visual"] and "material" not in row["todo_keys"]
    assert "exam" in row["todo_keys"]


def test_wrong_identity_unknown_status_and_invalid_curriculum_are_not_complete():
    item = sample()
    item["attributes"]["key"] = "another-question"
    assert summarize_word_catalog(catalog(item))["counts"]["primary"] == 0
    item["attributes"]["key"] = item["key"]
    item["attributes"]["primary_knowledge"]["status"] = "unknown"
    item["attributes"]["supporting_knowledge"] = [{"id": "K11", "status": "teacher_confirmed"}]
    item["attributes"]["curriculum_candidates"][0]["chapter_id"] = "wrong-parent"
    item["attributes"]["applicable_grades"]["status"] = "unknown"
    item["attributes"]["original_source"]["exam_type"]["status"] = "unknown"
    counts = summarize_word_catalog(catalog(item))["counts"]
    assert all(counts[key] == 0 for key in ("primary", "curriculum", "grade", "exam", "complete_candidates"))


def test_missing_directory_is_unknown_and_stale_teacher_tags_remain_protected():
    item = sample()
    missing = catalog(item)
    missing.pop("attribute_catalog")
    result = summarize_word_catalog(missing)
    assert result["counts"]["curriculum"] == 0 and result["warnings"]
    item["attributes"]["annotation_source"] = "teacher_modified"
    item["attributes"]["question_revision"] = "old"
    row = summarize_word_catalog(catalog(item))["rows"][0]
    assert row["protected"] and "stale" in row["todo_keys"]


def test_service_stale_projection_is_pending_without_attaching_old_attributes():
    item = sample()
    item.pop("attributes")
    item.update(attribute_stale=True, attribute_protected=True)
    result = summarize_word_catalog(catalog(item))
    assert result["counts"]["stale_labels"] == 1
    assert result["rows"][0]["protected"] and "stale" in result["rows"][0]["todo_keys"]
    assert result["counts"]["primary"] == 0
