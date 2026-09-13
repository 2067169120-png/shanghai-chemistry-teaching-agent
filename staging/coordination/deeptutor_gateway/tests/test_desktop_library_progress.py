from copy import deepcopy
from types import SimpleNamespace

from integrations.deeptutor_shchem_v1.desktop_library_progress import (
    collect_library_progress, summarize_word_catalog,
)


def sample():
    return {"key": "q1", "revision": "v1", "source_sha256": "a" * 64,
            "source_name": "合成.docx", "attributes": {
                "source_sha256": "a" * 64, "question_revision": "v1",
                "primary_knowledge": {"id": "K01"},
                "curriculum_candidates": [{"section_key": "s1"}],
                "applicable_grades": {"values": ["grade_11"]},
                "original_source": {"exam_type": {"value": "second_mock"}},
            }}


def test_complete_fields_are_not_teacher_approval():
    item = sample()
    before = deepcopy(item)
    result = summarize_word_catalog({"items": [item], "sources": [{"id": "s1"}]})
    assert result["counts"]["complete_candidates"] == 1
    assert "teacher_reviewed" not in result["rows"][0]
    assert item == before


def test_stale_labels_do_not_count():
    item = sample()
    item["attributes"]["question_revision"] = "old"
    result = summarize_word_catalog({"items": [item]})
    assert result["counts"]["primary"] == 0
    assert result["counts"]["stale_labels"] == 1
    assert "重核旧标签" in result["rows"][0]["todo"]


def test_unknown_exam_remains_a_gap():
    item = sample()
    item["attributes"]["original_source"]["exam_type"]["value"] = "unknown"
    result = summarize_word_catalog({"items": [item]})
    assert result["counts"]["complete_candidates"] == 0
    assert "原考试类型" in result["rows"][0]["todo"]


def test_duplicates_and_missing_attributes():
    item = sample()
    other = {"key": "q2", "source_name": "other"}
    result = summarize_word_catalog({"items": [item, item, other]})
    assert result["counts"]["candidates"] == 2
    assert result["counts"]["primary"] == 1
    assert result["warnings"]


def test_material_gaps_remain_visible():
    item = sample()
    item["attributes"]["material_status"] = {"missing_context": True}
    result = summarize_word_catalog({"items": [item]})
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
