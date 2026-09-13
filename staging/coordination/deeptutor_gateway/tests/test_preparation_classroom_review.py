"""Structural hints must not masquerade as answer/source verification."""

from copy import deepcopy

from integrations.deeptutor_shchem_v1.desktop_preparation_review import (
    classroom_review,
    format_classroom_review,
)


def _slide(
    title, *, activity="A01", objective="O01", notes="依据：Word区块42；教材C05。"
):
    return {
        "title": title,
        "purpose": "",
        "minutes": 2,
        "content": ["本页学生可见的具体概念与适用条件。"],
        "teacher_notes": notes,
        "activity_ids": [activity],
        "objective_ids": [objective],
        "assessment_ids": ["E01"],
    }


def _candidate():
    return {
        "title": "示例课件",
        "objectives": [{"id": "O01", "statement": "说明概念与成立条件"}],
        "slides": [_slide("独立练习"), _slide("逐题核对"), _slide("笔记整理")],
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
    }


def test_review_is_read_only_and_never_declares_teaching_pass():
    c = _candidate()
    before = deepcopy(c)
    report = classroom_review(c)
    assert c == before
    assert report["status"] == "teacher_review_required"
    assert "passed" not in report
    assert report["task_links"] == [{"task_page": 1, "possible_feedback_pages": [2]}]
    assert report["objectives"][0]["note_hint_pages"] == [3]
    assert "有关联不等于答案匹配" in format_classroom_review(report)


def test_feedback_requires_later_page_and_explicit_shared_activity_and_objective():
    c = _candidate()
    c["slides"] = [
        _slide("反馈"),
        _slide("独立练习"),
        _slide("核对", activity="A02"),
        _slide("反馈", objective="O02"),
    ]
    report = classroom_review(c)
    assert report["task_links"][0]["possible_feedback_pages"] == []
    assert "later_feedback_not_detected" in {r["code"] for r in report["findings"]}


def test_teacher_notes_never_become_student_visible_notes_or_role_hints():
    c = _candidate()
    c["slides"] = [_slide("概念讲解", notes="安排笔记整理，反馈并核对练习。")]
    report = classroom_review(c)
    assert report["pages"][0]["role_hints"] == []
    assert "安排笔记整理" not in "".join(report["pages"][0]["visible_text"])
    assert "objective_note_not_detected" in {r["code"] for r in report["findings"]}


def test_source_mention_is_only_a_hint_not_evidence_verification():
    c = _candidate()
    c["slides"][0]["teacher_notes"] = "参考Word和教材，做好笔记。"
    report = classroom_review(c)
    assert not report["pages"][0]["source_locator_mentioned"]
    assert report["pages"][1]["source_locator_mentioned"]
    assert "有来源编号不等于来源已核验" in report["note"]


def test_dense_visible_text_and_missing_objective_links_are_reported_not_truncated():
    c = _candidate()
    c["objectives"].append({"id": "O02", "statement": "尚未安排的目标"})
    c["slides"][0]["content"] = ["长" * 421]
    report = classroom_review(c)
    codes = {r["code"] for r in report["findings"]}
    assert {"visible_text_dense", "objective_without_slide"} <= codes
    assert report["pages"][0]["visible_text"] == ["长" * 421]


def test_same_page_feedback_hint_requests_answer_leak_review():
    c = _candidate()
    c["slides"][0]["title"] = "独立练习及答案"
    report = classroom_review(c)
    assert "task_feedback_same_page" in {r["code"] for r in report["findings"]}


def test_visual_comparison_text_is_included_without_layout_metadata():
    c = _candidate()
    c["slides"][2]["visual"] = {
        "kind": "comparison",
        "steps": [],
        "comparison": {
            "dimension_label": "条件",
            "columns": ["电离", "导电"],
            "rows": [{"label": "原因", "values": ["生成离子", "定向移动"]}],
        },
    }
    visible = classroom_review(c)["pages"][2]["visible_text"]
    assert "定向移动" in visible
    assert "comparison" not in visible


def test_empty_legacy_candidate_remains_inspectable_without_auto_approval():
    report = classroom_review({"title": "旧候选"})
    text = format_classroom_review(report)
    assert "仍须检查" in text
    assert report["status"] == "teacher_review_required"


def test_numbered_exercise_analysis_is_feedback_not_another_task():
    c = _candidate()
    c["slides"] = [_slide("练习1 分类"), _slide("练习1 解析与依据")]
    report = classroom_review(c)
    assert report["task_links"] == [{"task_page": 1, "possible_feedback_pages": [2]}]
    assert report["pages"][1]["role_hints"] == ["反馈核对线索"]
    assert not any("feedback" in row["code"] for row in report["findings"])


def test_correction_task_without_solution_is_not_feedback():
    c = _candidate()
    c["slides"] = [_slide("练习5 纠错"), _slide("练习5 讲评")]
    report = classroom_review(c)
    assert report["pages"][0]["role_hints"] == ["学生任务线索"]
    assert report["task_links"][0]["possible_feedback_pages"] == [2]


def test_cross_activity_pair_requires_number_assessment_and_objective():
    c = _candidate()
    c["slides"] = [_slide("练习2 分类"), _slide("练习2 解析", activity="A02")]
    assert classroom_review(c)["task_links"][0]["possible_feedback_pages"] == [2]
    for field, value in (("assessment_ids", []), ("objective_ids", ["O02"])):
        other = deepcopy(c)
        other["slides"][1][field] = value
        assert classroom_review(other)["task_links"][0]["possible_feedback_pages"] == []


def test_other_number_does_not_count_as_feedback_even_in_same_activity():
    c = _candidate()
    c["slides"] = [_slide("练习1 分类"), _slide("练习10 解析")]
    assert classroom_review(c)["task_links"][0]["possible_feedback_pages"] == []


def test_independent_purpose_and_mixed_heading_retain_answer_leak_hint():
    for title, purpose in (("练习2 解析", "独立练习"), ("练习与答案", "")):
        c = _candidate()
        c["slides"] = [_slide(title)]
        c["slides"][0]["purpose"] = purpose
        assert "task_feedback_same_page" in {
            item["code"] for item in classroom_review(c)["findings"]
        }


def test_exact_word_body_locator_remains_mention_not_source_approval():
    c = _candidate()
    c["slides"] = [_slide("讲义题", notes="来源：Word正文子节点94—97；题源待核验。")]
    report = classroom_review(c)
    assert report["pages"][0]["source_locator_mentioned"]
    assert report["status"] == "teacher_review_required"
    assert "passed" not in report
