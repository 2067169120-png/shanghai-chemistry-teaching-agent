from copy import deepcopy

import pytest
from test_desktop_preparation import _payload, _raw_candidate

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    normalize_preparation_candidate,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_revision import (
    apply_preparation_text_edits,
    preparation_text_fields,
)


def revision_candidate():
    raw = _raw_candidate()
    raw["slides"][1]["visual"] = {
        "kind": "comparison",
        "steps": [],
        "comparison": {
            "dimension_label": "维度",
            "columns": ["甲", "乙"],
            "rows": [
                {"label": "定义", "values": ["甲的定义", "乙的定义"]},
                {"label": "条件", "values": ["甲的条件", "乙的条件"]},
            ],
        },
    }
    raw["activities"][0]["worksheet"] = {
        "title": "课堂记录",
        "instructions": ["记录要点"],
        "sections": [
            {
                "heading": "概念",
                "prompt": "填写区别",
                "response_kind": "table",
                "response_lines": 0,
                "columns": ["甲", "乙"],
                "row_labels": ["定义"],
            }
        ],
    }
    return normalize_preparation_candidate(raw, _payload())


def test_changes_explicit_table_prose_lesson_and_worksheet_without_other_mutation():
    candidate = revision_candidate()
    before = deepcopy(candidate)
    edits = [
        {
            "path": ["slides", 1, "visual", "comparison", "rows", 1, "values", 0],
            "text": "明确适用条件",
        },
        {"path": ["slides", 1, "teacher_notes"], "text": "讲解后留出笔记时间。"},
        {
            "path": ["lesson_stages", 0, "assessment"],
            "text": "检查定义和条件是否完整。",
        },
        {
            "path": ["activities", 0, "worksheet", "instructions", 0],
            "text": "记录两页要点。",
        },
    ]
    changed = apply_preparation_text_edits(candidate, edits, _payload())
    expected = deepcopy(before)
    for edit in edits:
        target = expected
        for part in edit["path"][:-1]:
            target = target[part]
        target[edit["path"][-1]] = edit["text"]
    expected["candidate_id"] = changed["candidate_id"]
    assert candidate == before and changed == expected
    assert changed["candidate_id"] != before["candidate_id"]
    assert changed["candidate_only"] and not changed["publication_allowed"]


@pytest.mark.parametrize(
    "path",
    [
        ["topic"],
        ["candidate_id"],
        ["publication_allowed"],
        ["slides", 0, "title"],
        ["slides", 1, "minutes"],
        ["slides", 1, "activity_ids", 0],
        ["uncertainties", 0, "description"],
        ["slides", -1, "title"],
        ["slides", True, "title"],
        ["slides", {}, "title"],
        ["missing"],
    ],
)
def test_non_text_fields_and_malformed_paths_rejected(path):
    with pytest.raises(DesktopPreparationError):
        apply_preparation_text_edits(
            revision_candidate(), [{"path": path, "text": "不可改"}], _payload()
        )


@pytest.mark.parametrize("text", ["", " " * 20, "字" * 121, "x\x00y", 3])
def test_comparison_cell_does_not_silently_truncate_or_allow_blank(text):
    with pytest.raises(DesktopPreparationError):
        apply_preparation_text_edits(
            revision_candidate(),
            [
                {
                    "path": [
                        "slides",
                        1,
                        "visual",
                        "comparison",
                        "rows",
                        0,
                        "values",
                        0,
                    ],
                    "text": text,
                }
            ],
            _payload(),
        )


def test_noop_and_duplicate_paths_rejected():
    candidate = revision_candidate()
    edit = {
        "path": ["slides", 1, "content", 0],
        "text": candidate["slides"][1]["content"][0],
    }
    with pytest.raises(DesktopPreparationError, match="内容没有变化"):
        apply_preparation_text_edits(candidate, [edit], _payload())
    edit["text"] = "实际修改"
    with pytest.raises(DesktopPreparationError):
        apply_preparation_text_edits(candidate, [edit, edit], _payload())


def test_field_inventory_names_and_limits_are_shared_with_ui():
    fields = preparation_text_fields(revision_candidate())
    lookup = {tuple(f["path"]): f for f in fields}
    assert len(lookup) == len(fields)
    assert lookup[("activities", 0, "worksheet", "instructions", 0)]["limit"] == 300
    assert (
        lookup[("activities", 0, "worksheet", "sections", 0, "columns", 0)]["limit"]
        == 40
    )
    assert (
        "知识表"
        in lookup[("slides", 1, "visual", "comparison", "rows", 0, "values", 0)][
            "label"
        ]
    )
