"""Source-bound range confirmations, using synthetic text only."""

from copy import deepcopy

import pytest
from test_word_question_index import preview

from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    BOUNDARY_REVIEW_REVISION,
    apply_question_range,
    index_word_questions,
)


def _review(data, item, issues=None, **overrides):
    fields = {
        name: item[name]
        for name in (
            "block_start",
            "question_end",
            "answer_start",
            "block_end",
            "context_start",
            "context_end",
        )
    }
    fields.update(overrides)
    return apply_question_range(data, item, reviewed_issues=issues, **fields)


@pytest.mark.parametrize(
    "choice", ["（多选）", "(多选)", "（单项选择）", "（不定项选择）"]
)
def test_choice_editorial_prefix_does_not_hide_next_question(choice):
    data = preview(
        "【例1】第一道题，写出产物____。",
        "【答案】产物甲。",
        "【解析】只属于第一题的解析。",
        "",
        "【反应过程分析】" + choice + "【变式训练3】第二道题，选择正确的是（ ）。",
        "A．条件甲 B．条件乙",
        "【答案】AB",
        "【解析】只属于第二题的解析。",
    )
    frozen = deepcopy(data)
    first, second = index_word_questions(data)
    assert (first["block_start"], first["block_end"]) == (1, 3)
    assert (
        second["block_start"],
        second["question_end"],
        second["answer_start"],
        second["block_end"],
    ) == (5, 6, 7, 8)
    assert first["export_ready"] and second["export_ready"]
    assert "第二道题" not in str(first["answer_blocks"])
    assert second["question_blocks"][0]["text"] == frozen["blocks"][4]["text"]
    assert data == frozen


def test_answer_label_is_not_skipped_when_it_quotes_editorial_question_prefix():
    data = preview("【例1】原题？", "【答案】（多选）【变式训练3】仅为答案引用。")
    (item,) = index_word_questions(data)
    assert item["block_start"] == 1 and item["answer_start"] == 2


@pytest.mark.parametrize(
    ("issue", "texts"),
    [
        (
            "unmarked_answer",
            (
                "【例1】哪一项符合条件？",
                "A．甲 B．乙 C．丙",
                "(3)C",
                "(3)这是原文已有的解析，说明选择丙符合给定条件。",
            ),
        ),
        (
            "nonstandard_label",
            (
                "【变式训练3·变题型某装置的条件如下。",
                "（1）产物是____。",
                "【答案】产物甲。",
            ),
        ),
        (
            "self_contained_reference",
            (
                "【例1】先给出本题的完整观点：以给定定义判断。根据上述观点回答问题。",
                "（1）选择正确项____。",
                "【答案】A。",
            ),
        ),
    ],
)
def test_specific_confirmation_can_resolve_intact_source_boundary(issue, texts):
    data = preview(*texts)
    frozen = deepcopy(data)
    (item,) = index_word_questions(data)
    assert not item["export_ready"]
    assert not _review(data, item)["export_ready"]
    changed = _review(data, item, [issue])
    assert changed["boundary_status"] == "manual_range"
    assert changed["export_ready"]
    assert changed["boundary_review"] == {
        "revision": BOUNDARY_REVIEW_REVISION,
        "issues": [issue],
        "scope": "selected_source_ranges_only",
    }
    assert changed["key"] == item["key"]
    assert changed["revision"] != item["revision"]
    for role in ("question_blocks", "answer_blocks", "context_blocks"):
        assert changed[role] == item[role]
    assert _review(data, changed, [issue]) == changed
    assert data == frozen


@pytest.mark.parametrize(
    "issues",
    [
        True,
        "nonstandard_label",
        ["unknown"],
        ["unmarked_answer", "unmarked_answer"],
        [{}],
    ],
)
def test_invalid_confirmation_values_do_not_authorize_export(issues):
    data = preview("【例1】题面？", "【答案】A")
    (item,) = index_word_questions(data)
    with pytest.raises(ValueError, match="确认项"):
        _review(data, item, issues)


def test_confirmation_must_match_current_selected_source_issue():
    data = preview("【例1】题面？", "【答案】A")
    (item,) = index_word_questions(data)
    with pytest.raises(ValueError, match="当前所选原文"):
        _review(data, item, ["nonstandard_label"])


def test_unmarked_answer_cannot_be_moved_into_question_by_confirmation():
    data = preview(
        "【例1】选择哪项？",
        "A．甲 B．乙 C．丙",
        "(3)C",
        "(3)原文完整解析，在这里解释选择丙的依据。",
    )
    (item,) = index_word_questions(data)
    changed = _review(data, item, ["unmarked_answer"], question_end=3, answer_start=4)
    assert not changed["export_ready"]
    assert any("答案或解析区块" in warning for warning in changed["warnings"])


def test_confirmation_never_clears_inline_answer_or_contaminated_material():
    data = preview("【变式训练3·变题型原问题？【答案】A", "【解析】原理由。")
    (item,) = index_word_questions(data)
    changed = _review(data, item, ["nonstandard_label"], question_end=1, answer_start=2)
    assert not changed["export_ready"]
    assert any("仍含答案标记" in warning for warning in changed["warnings"])


def test_answer_continuation_cannot_be_relabelled_as_question_or_shared_material():
    data = preview(
        "【例1】题一？",
        "【答案】A",
        "没有标记的原解析续段。",
        "【变式训练3·变题型第二题？",
        "【答案】B",
    )
    _, item = index_word_questions(data)
    leaked_question = _review(data, item, None, block_start=3)
    assert not leaked_question["export_ready"]
    assert any("题面范围包含" in warning for warning in leaked_question["warnings"])
    leaked_context = _review(
        data, item, ["nonstandard_label"], context_start=3, context_end=3
    )
    assert not leaked_context["export_ready"]
    assert any(
        "共同材料范围包含答案" in warning for warning in leaked_context["warnings"]
    )
