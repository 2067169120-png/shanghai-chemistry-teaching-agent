from copy import deepcopy

import pytest
from test_desktop_preparation import _payload, _raw_candidate

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    _canonical_candidate_digest,
    _grouped_stage_times_agree,
    normalize_preparation_candidate,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_structure import (
    apply_preparation_revision,
    classroom_timeline,
    preview_structure_edits,
)


def grouped_candidate():
    raw = _raw_candidate()
    raw["slides"][1]["activity_numbers"] = [1]
    raw["lesson_stages"] = [raw["lesson_stages"][0]]
    raw["lesson_stages"][0]["activity_numbers"] = [1, 2]
    raw["lesson_stages"][0]["minutes"] = 40
    return normalize_preparation_candidate(raw, _payload())


def notes_operation(activity="A01"):
    return {
        "kind": "append_worksheet",
        "activity_id": activity,
        "worksheet_title": "课堂笔记",
        "section": {
            "heading": "判断依据",
            "prompt": "记录判断条件，再写一个反例。",
            "response_kind": "lines",
            "response_lines": 4,
            "columns": [],
            "row_labels": [],
        },
    }


def test_explicit_link_then_grouped_timing_preserves_budgets_and_source():
    original = grouped_candidate()
    frozen = deepcopy(original)
    assert not _grouped_stage_times_agree(
        original["activities"], original["slides"], original["lesson_stages"]
    )
    with pytest.raises(DesktopPreparationError, match="归属"):
        preview_structure_edits(original, [{"kind": "align_timing"}])
    revised = apply_preparation_revision(
        original,
        [],
        [
            {"kind": "link_slide_activity", "slide_id": "S01", "activity_id": "A01"},
            {"kind": "align_timing"},
        ],
        _payload(),
    )
    assert _grouped_stage_times_agree(
        revised["activities"], revised["slides"], revised["lesson_stages"]
    )
    assert revised["activities"] == original["activities"]
    assert revised["lesson_stages"] == original["lesson_stages"]
    assert revised["source_basis"] == original["source_basis"]
    assert revised["uncertainties"][:-1] == original["uncertainties"]
    assert original == frozen
    assert revised["candidate_id"] != original["candidate_id"]
    assert not revised["publication_allowed"]


def test_split_preserves_whole_question_image_and_minutes_after_text_edit():
    original = grouped_candidate()
    asset = {
        "asset_id": "IMG-" + "a" * 64,
        "sha256": "a" * 64,
        "caption": "合成测试图",
        "source": "本地测试",
        "purpose": "布局测试",
        "width": 800,
        "height": 200,
        "content_type": "image/png",
    }
    payload = _payload()
    payload["image_assets"] = [asset]
    original["image_assets"] = [asset]
    original["source_basis"] = normalize_preparation_payload(payload)["source_basis"]
    original["slides"][1]["image"] = {
        "asset_id": asset["asset_id"],
        "observation_prompt": "观察离子分布。",
    }
    original["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(original)[:32]
    before = deepcopy(original)
    revised = apply_preparation_revision(
        original,
        [
            {"path": ["slides", 1, "content", 0], "text": "经教师修订的完整题干"},
        ],
        [{"kind": "split_image", "slide_id": "S02"}],
        payload,
    )
    picture, question = revised["slides"][1:3]
    assert picture["image"] == original["slides"][1]["image"]
    assert picture["content"] == ["读图后，结合下一页文字继续讨论。"]
    assert question["content"] == [
        "经教师修订的完整题干",
        *original["slides"][1]["content"][1:],
    ]
    assert not question.get("image")
    assert question["activity_ids"] == picture["activity_ids"]
    assert picture["minutes"] + question["minutes"] == original["slides"][1]["minutes"]
    assert [row["order"] for row in revised["slides"]] == list(range(1, 5))
    assert len({row["id"] for row in revised["slides"]}) == 4
    assert original == before
    with pytest.raises(DesktopPreparationError, match="一次"):
        preview_structure_edits(
            original, [{"kind": "split_image", "slide_id": "S02"}] * 2
        )


def test_notes_append_without_overwriting_existing_or_copying_teacher_answer():
    candidate = grouped_candidate()
    first = apply_preparation_revision(candidate, [], [notes_operation()], _payload())
    original_worksheet = deepcopy(first["activities"][0]["worksheet"])
    second = apply_preparation_revision(first, [], [notes_operation()], _payload())
    assert (
        second["activities"][0]["worksheet"]["sections"][:1]
        == original_worksheet["sections"]
    )
    assert len(second["activities"][0]["worksheet"]["sections"]) == 2
    assert first["activities"][0]["worksheet"] == original_worksheet
    with pytest.raises(DesktopPreparationError, match="最多6"):
        preview_structure_edits(candidate, [notes_operation()] * 7)


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "change_source"},
        {"kind": "align_timing", "approved": True},
        {"kind": "link_slide_activity", "slide_id": "S01", "activity_id": "missing"},
        {"kind": "split_image", "slide_id": "S01"},
        {
            "kind": "append_worksheet",
            "activity_id": "A01",
            "worksheet_title": "x",
            "section": {},
        },
    ],
)
def test_invalid_operations_are_atomic(operation):
    candidate = grouped_candidate()
    before = deepcopy(candidate)
    with pytest.raises(DesktopPreparationError):
        preview_structure_edits(candidate, [operation])
    assert candidate == before


def test_alignment_rejects_interleaving_and_conflicting_stage_budgets():
    candidate = grouped_candidate()
    candidate["slides"][0]["activity_ids"] = ["A02"]
    with pytest.raises(DesktopPreparationError, match="顺序"):
        preview_structure_edits(candidate, [{"kind": "align_timing"}])
    candidate["slides"][0]["activity_ids"] = ["A01"]
    candidate["lesson_stages"][0]["minutes"] = 39
    with pytest.raises(DesktopPreparationError, match="预算"):
        preview_structure_edits(candidate, [{"kind": "align_timing"}])


def test_timeline_flags_exact_boundary_crossing_without_guessing_from_title():
    candidate = grouped_candidate()
    candidate["timing"] = {"periods": 2, "minutes_per_period": 20, "total_minutes": 40}
    assert "跨越课时边界" in classroom_timeline(candidate)
