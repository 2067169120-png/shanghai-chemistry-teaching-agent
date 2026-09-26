from copy import deepcopy

import pytest
from test_desktop_preparation import _payload
from test_preparation_sequence import sequence_candidate
from test_preparation_text_revision import revision_candidate

from integrations.deeptutor_shchem_v1.desktop_preparation import DesktopPreparationError
from integrations.deeptutor_shchem_v1.desktop_preparation_structure import (
    apply_preparation_revision,
    preview_structure_edits,
)


def insertion():
    return {
        "kind": "insert_page",
        "anchor_slide_id": "S04",
        "position": "before",
        "minutes": 2,
        "page": {
            "title": "补充知识",
            "purpose": "建立练习所需的前置认识",
            "content": ["完整知识句。", "说明适用条件。"],
            "teacher_notes": "先提问，再组织学生解释。",
            "source_reference": "测试讲义第5页，非真实来源",
            "visual": None,
        },
    }


def test_insertion_keeps_total_and_links_without_copying_anchor_content():
    original = sequence_candidate()
    before = deepcopy(original)
    revised = apply_preparation_revision(original, [], [insertion()], _payload())
    new, anchor = revised["slides"][3:5]
    assert new["id"] == "SLOCAL001" and anchor["id"] == "S04"
    assert new["minutes"] == 2 and anchor["minutes"] == 8
    assert sum(slide["minutes"] for slide in revised["slides"]) == 40
    assert new["content"] == insertion()["page"]["content"]
    for key in ("activity_ids", "objective_ids", "assessment_ids"):
        assert new[key] == anchor[key]
    assert "未自动核验" in new["teacher_notes"]
    assert "测试讲义第5页" in new["teacher_notes"] and "测试讲义" not in str(
        new["content"]
    )
    assert revised["activities"] == original["activities"]
    assert revised["lesson_stages"] == original["lesson_stages"]
    assert revised["source_basis"] == original["source_basis"]
    assert not revised["publication_allowed"] and revised["teacher_review_required"]
    assert revised["candidate_id"] != original["candidate_id"] and original == before


def test_new_table_is_complete_and_original_position_text_edit_still_targets_anchor():
    operation = insertion()
    operation["page"]["visual"] = revision_candidate()["slides"][1]["visual"]
    revised = apply_preparation_revision(
        sequence_candidate(),
        [{"path": ["slides", 3, "content", 0], "text": "原稿本页改字"}],
        [operation],
        _payload(),
    )
    assert revised["slides"][3]["visual"] == operation["page"]["visual"]
    assert revised["slides"][4]["content"] == ["原稿本页改字"]


def test_insertion_can_follow_split_and_then_move_new_page_with_unique_ids():
    candidate = sequence_candidate()
    candidate["slides"][3]["image"] = {
        "asset_id": "test",
        "observation_prompt": "合成测试图",
    }
    operation = insertion()
    operation["anchor_slide_id"] = "SLOCAL001"
    operation["position"] = "after"
    updated, _ = preview_structure_edits(
        candidate,
        [
            {"kind": "split_image", "slide_id": "S04"},
            operation,
            {
                "kind": "move_slides",
                "slide_ids": ["SLOCAL002"],
                "before_slide_id": "S04",
            },
        ],
    )
    assert len(updated["slides"]) == 8
    assert [slide["order"] for slide in updated["slides"]] == list(range(1, 9))
    assert len({slide["id"] for slide in updated["slides"]}) == 8
    new = next(slide for slide in updated["slides"] if slide["id"] == "SLOCAL002")
    assert not new.get("image") and not new.get("visual")
    assert sum(slide["minutes"] for slide in updated["slides"]) == 40


@pytest.mark.parametrize(
    "change",
    [
        {"anchor_slide_id": "S01"},
        {"anchor_slide_id": "unknown"},
        {"anchor_slide_id": None},
        {"position": "start"},
        {"minutes": True},
        {"minutes": 0},
        {"minutes": 10},
        {"teacher_review_passed": True},
        {"page": {}},
    ],
)
def test_invalid_insertion_is_atomic(change):
    candidate = sequence_candidate()
    before = deepcopy(candidate)
    with pytest.raises(DesktopPreparationError):
        preview_structure_edits(candidate, [{**insertion(), **change}])
    assert candidate == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("source_reference", ""),
        ("content", []),
        ("content", ["x"] * 13),
        ("content", ["x\x00y"]),
        ("title", "字" * 81),
        ("visual", {"kind": "comparison"}),
    ],
)
def test_invalid_new_page_does_not_discard_or_truncate_content(field, value):
    candidate = sequence_candidate()
    operation = insertion()
    operation["page"][field] = value
    before = deepcopy(candidate)
    with pytest.raises(DesktopPreparationError):
        preview_structure_edits(candidate, [operation])
    assert candidate == before


def test_requires_single_activity_and_rejects_control_chars_in_table():
    candidate = sequence_candidate()
    candidate["slides"][3]["activity_ids"] = ["A01", "A02"]
    with pytest.raises(DesktopPreparationError, match="单一"):
        preview_structure_edits(candidate, [insertion()])
    operation = insertion()
    operation["page"]["visual"] = revision_candidate()["slides"][1]["visual"]
    operation["page"]["visual"]["comparison"]["rows"][0]["values"][0] = "a\x00"
    with pytest.raises(DesktopPreparationError, match="无效"):
        preview_structure_edits(sequence_candidate(), [operation])
