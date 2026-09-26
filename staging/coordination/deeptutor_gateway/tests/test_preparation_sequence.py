from copy import deepcopy

import pytest
from test_desktop_preparation import _payload, _raw_candidate
from test_preparation_structure import notes_operation
from test_preparation_text_revision import revision_candidate

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    normalize_preparation_candidate,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_sequence import (
    classroom_sequence,
    student_page_text,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_structure import (
    apply_preparation_revision,
    preview_structure_edits,
)


def sequence_candidate():
    raw = _raw_candidate()
    raw["slides"] = [raw["slides"][0]] + [deepcopy(raw["slides"][1]) for _ in range(5)]
    for number, (slide, minutes) in enumerate(
        zip(raw["slides"], [2, 4, 4, 10, 10, 10], strict=True)
    ):
        slide["title"] = f"页面{number + 1}"
        slide["content"] = [f"学生正文{number + 1}"]
        slide["teacher_notes"] = f"教师备注{number + 1}"
        slide["minutes"] = minutes
        slide["activity_numbers"] = [1] if number < 3 else [2]
    raw["lesson_stages"][0]["minutes"] = 10
    raw["lesson_stages"][1]["minutes"] = 30
    return normalize_preparation_candidate(raw, _payload())


def move(ids, target=None):
    return {"kind": "move_slides", "slide_ids": ids, "before_slide_id": target}


def test_block_move_preserves_all_content_internal_order_time_and_source():
    original = sequence_candidate()
    before = deepcopy(original)
    revised = apply_preparation_revision(
        original, [], [move(["S03", "S02"], "S06")], _payload()
    )
    assert [slide["id"] for slide in revised["slides"]] == [
        "S01",
        "S04",
        "S05",
        "S02",
        "S03",
        "S06",
    ]
    assert [slide["order"] for slide in revised["slides"]] == list(range(1, 7))
    original_by_id = {slide["id"]: slide for slide in original["slides"]}
    for slide in revised["slides"]:
        assert {k: v for k, v in slide.items() if k != "order"} == {
            k: v for k, v in original_by_id[slide["id"]].items() if k != "order"
        }
    assert revised["activities"] == original["activities"]
    assert revised["lesson_stages"] == original["lesson_stages"]
    assert revised["source_basis"] == original["source_basis"]
    assert revised["uncertainties"][:-1] == original["uncertainties"]
    assert revised["candidate_id"] != original["candidate_id"]
    assert not revised["publication_allowed"]
    assert original == before
    assert sum(slide["minutes"] for slide in revised["slides"]) == 40
    assert any("顺序" in warning for warning in classroom_sequence(revised)["warnings"])


def test_text_edits_remain_on_original_id_when_moving_to_end():
    original = sequence_candidate()
    revised = apply_preparation_revision(
        original,
        [{"path": ["slides", 1, "content", 0], "text": "仍属于第二页原稿"}],
        [move(["S02"])],
        _payload(),
    )
    assert revised["slides"][-1]["id"] == "S02"
    assert revised["slides"][-1]["content"] == ["仍属于第二页原稿"]
    assert revised["slides"][1]["content"] == original["slides"][2]["content"]


@pytest.mark.parametrize(
    "operation",
    [
        move([]),
        move("S02"),
        move([True]),
        move(["S02", "S02"]),
        move(["missing"]),
        move(["S01"]),
        move(["S02"], "S01"),
        move(["S02"], "S02"),
        move(["S02"], "missing"),
        move(["S02"], False),
        move(["S02", "S04"]),
        move(["S02"], "S03"),
        move(["S06"]),
        {**move(["S02"]), "teacher_review_passed": True},
    ],
)
def test_invalid_moves_are_atomic_even_after_another_operation(operation):
    original = sequence_candidate()
    before = deepcopy(original)
    with pytest.raises(DesktopPreparationError):
        preview_structure_edits(original, [notes_operation(), operation])
    assert original == before


def test_preview_roundtrip_restores_exact_candidate_but_save_rejects_noop():
    candidate = sequence_candidate()
    operations = [move(["S02"]), move(["S02"], "S03")]
    updated, _ = preview_structure_edits(candidate, operations)
    assert updated == candidate
    with pytest.raises(DesktopPreparationError, match="没有变化"):
        apply_preparation_revision(candidate, [], operations, _payload())


def test_sequence_uses_exact_links_and_visible_table_not_teacher_answers():
    candidate = revision_candidate()
    before = deepcopy(candidate)
    page = classroom_sequence(candidate)["pages"][1]
    assert (
        "甲：甲的定义" in page["student_text"]
        and "乙：乙的条件" in page["student_text"]
    )
    assert candidate["slides"][1]["teacher_notes"] not in page["student_text"]
    assert candidate["slides"][1]["purpose"] not in page["student_text"]
    assert page["activities"][0]["worksheet"]["sections"][0]["heading"] == "概念"
    assert [row["id"] for row in page["assessments"]] == ["E01"]
    page["activities"][0]["title"] = "should not mutate source"
    assert candidate == before
    assert not classroom_sequence(candidate)["pages"][0]["activities"]


def test_student_process_and_image_prompt_are_visible_but_pixels_not_inferred():
    slide = {
        "title": "过程",
        "content": ["问题"],
        "teacher_notes": "秘密答案",
        "image": {"observation_prompt": "观察图片"},
        "visual": {"kind": "process", "steps": [{"label": "判断", "detail": "条件"}]},
    }
    text = student_page_text(slide)
    assert "1. 判断\n条件" in text and "观察图片" in text
    assert "秘密答案" not in text and "未在文字视图中显示" in text


def test_exact_period_boundaries_and_no_auto_teaching_approval():
    candidate = sequence_candidate()
    candidate["timing"].update(periods=2, minutes_per_period=20)
    report = classroom_sequence(candidate)
    assert report["pages"][3]["end_minute"] == 20
    assert report["pages"][4]["period_label"] == "第2课时"
    assert not report["warnings"]
    candidate["slides"][3]["minutes"] = 11
    assert "跨" in classroom_sequence(candidate)["pages"][3]["period_label"]
