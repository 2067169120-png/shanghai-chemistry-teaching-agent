"""Tests for the offline preparation source/text comparison helper."""

from copy import deepcopy

from integrations.deeptutor_shchem_v1.desktop_preparation_source_compare import (
    compare_source_text,
)


def _report(*, materials: str | None = None) -> dict:
    report = {
        "title": "离线课件",
        "pages": [
            {
                "page": 1,
                "title": "电解质导入",
                "visible_text": ["学生可见：NH₄Cl 溶液。", "保留这一行。"],
                "teacher_notes": "教师备注：NH_{4}Cl 的来源需核对。",
                "image": {"caption": "图片说明：不属于学生正文。"},
            },
            {
                "page": 2,
                "title": "记录与订正",
                "visible_text": ["学生可见的第二页正文。"],
                "teacher_notes": "备注独有词：只在教师备注中出现。",
            },
        ],
    }
    if materials is not None:
        report["source_reference"] = {
            "materials": materials,
            "topic": "电解质",
        }
    return report


def test_empty_query_returns_original_source_and_all_student_pages_without_mutation():
    materials = "<p>第一行</p>\nNH_{4}Cl 的原资料\n末行\n"
    report = _report(materials=materials)
    before = deepcopy(report)

    result = compare_source_text(report, "")

    assert result["source_text"] == materials
    assert result["source_found"] is True
    assert result["has_source"] is True
    assert result["visible_pages"] == [1, 2]
    assert result["notes_only_pages"] == []
    assert "第 1 页：电解质导入" in result["student_text"]
    assert "第 2 页：记录与订正" in result["student_text"]
    assert report == before


def test_pure_text_html_is_not_parsed_and_matching_context_keeps_full_lines():
    long_line = "<p>这是纯文本 HTML，不是要执行的文档；NH_{4}Cl 后仍保留整行。</p>"
    report = _report(materials=f"前一行\n{long_line}\n后一行")

    result = compare_source_text(report, "NH₄Cl")

    assert result["source_found"] is True
    assert result["source_text"] == f"前一行\n{long_line}\n后一行"
    assert "<p>" in result["source_text"]
    assert "</p>" in result["source_text"]


def test_subscript_and_superscript_word_extraction_normalize_only_for_search():
    report = _report(materials="前\nNH_{4}Cl 与 SO^{2-}\n后")

    result = compare_source_text(report, "NH₄Cl")
    second = compare_source_text(report, "SO²⁻")

    assert result["source_found"] is True
    assert second["source_found"] is True
    assert "NH_{4}Cl" in result["source_text"]
    assert "SO^{2-}" in second["source_text"]


def test_missing_source_is_distinct_from_a_source_with_no_search_result():
    missing = compare_source_text(_report(), "NH₄Cl")
    no_result = compare_source_text(
        _report(materials="资料中没有这个检索词。"), "NH₄Cl"
    )

    assert missing["source_text"] == ""
    assert missing["source_found"] is False
    assert missing["has_source"] is False
    assert no_result["source_text"] == ""
    assert no_result["source_found"] is False
    assert no_result["has_source"] is True


def test_nonempty_query_selects_only_visible_pages_and_keeps_all_text_on_each_page():
    result = compare_source_text(_report(), "第二页正文")

    assert result["visible_pages"] == [2]
    assert result["notes_only_pages"] == []
    assert "第 2 页：记录与订正" in result["student_text"]
    assert "学生可见的第二页正文。" in result["student_text"]
    assert "第 1 页：电解质导入" not in result["student_text"]


def test_notes_only_match_is_reported_but_never_added_to_student_text():
    result = compare_source_text(_report(), "只在教师备注中出现")

    assert result["visible_pages"] == []
    assert result["notes_only_pages"] == [2]
    assert result["student_text"] == ""


def test_image_caption_is_not_included_when_not_in_visible_text():
    result = compare_source_text(_report(), "图片说明")

    assert result["visible_pages"] == []
    assert result["notes_only_pages"] == []
    assert "图片说明：不属于学生正文。" not in result["student_text"]


def test_no_search_result_keeps_student_and_source_outputs_empty_for_nonempty_query():
    result = compare_source_text(
        _report(materials="原资料只有电离定义。"), "完全不存在的检索词"
    )

    assert result == {
        "source_text": "",
        "student_text": "",
        "source_found": False,
        "visible_pages": [],
        "notes_only_pages": [],
        "query": "完全不存在的检索词",
        "has_source": True,
    }


def test_chemical_symbol_case_and_equation_relation_are_not_conflated():
    report = _report(materials="Co 是元素符号；H₂S ⇌ H⁺ + HS⁻")
    assert not compare_source_text(report, "CO")["source_found"]
    assert compare_source_text(report, "Co")["source_found"]
    assert not compare_source_text(report, "H2S=")["source_found"]
    assert compare_source_text(report, "H2S⇌")["source_found"]
