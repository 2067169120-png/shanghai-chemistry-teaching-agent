from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    apply_question_range,
    index_word_questions,
)


def preview(*texts, assets=None):
    return {
        "source_name": "合成边界样例.docx",
        "source_sha256": "a" * 64,
        "revision": "source-preview-v1",
        "extraction_revision": "synthetic-extractor-v1",
        "blocks": [
            {"index": index, "text": text, "warnings": []}
            for index, text in enumerate(texts, 1)
        ],
        "assets": assets or [],
        "sections": [],
    }


@pytest.mark.parametrize(
    "marker",
    [
        "【即学即练1】",
        "【例1】",
        "【典例1】",
        "【例题 2】",
        "【同步练习1】",
        "【变式1-1】",
        "【变式2-2】",
        "【变式训练1·变载体】",
        "【变式训练2·变考法】",
        "例1．",
        "例题2：",
        "典例3 ",
        "[例一]",
    ],
)
def test_explicit_question_markers_are_distinct_candidates(marker):
    data = preview(
        "知识点01 分类",
        "1．定义：这是讲解。",
        marker + "选择正确的是（ ）",
        "A．甲 B．乙",
        "【答案】A",
        "【解析】示例参考。",
        "知识点02 性质",
    )
    items = index_word_questions(data)
    assert len(items) == 1
    item = items[0]
    assert (
        item["block_start"],
        item["question_end"],
        item["answer_start"],
        item["block_end"],
    ) == (3, 4, 5, 6)
    assert item["chapter"] == "知识点01 分类"
    assert item["selection_ready"] and item["export_ready"]


def test_knowledge_numbering_does_not_start_or_swallow_questions():
    data = preview(
        "1.2 有机化合物的结构",
        "1．原子结构特点",
        "知识说明。",
        "3．共价键的类型",
        "（1）σ键",
        "4．键的极性与有机反应的关系",
        "知识点05 强电解质和弱电解质",
        "2．弱电解质：在水中不能全部电离。",
        "【即学即练1】选择正确的是（ ）",
        "A．甲 B．乙",
        "【答案】B",
    )
    items = index_word_questions(data)
    assert len(items) == 1 and items[0]["block_start"] == 9
    assert "弱电解质：" not in str(items[0]["question_blocks"])


def test_whether_inside_a_knowledge_explanation_is_not_a_question_cue():
    data = preview(
        "必杀技",
        "1．根据在水溶液中或熔融状态下是否电离，可把化合物分类。",
        "2．根据电离程度，可把电解质分类。",
        "【同步练习1】下列说法正确的是（ ）",
        "【答案】A",
    )
    (item,) = index_word_questions(data)
    assert item["block_start"] == 4


def test_generic_number_requires_question_options_or_real_exercise_heading():
    data = preview(
        "1．原子概述",
        "这里是普通知识说明。",
        "2．某实验如下。",
        "A．甲 B．乙",
        "【答案】A",
        "题组A 基础过关练",
        "3．【待查看原文：图片或图形】",
        "【答案】B",
    )
    items = index_word_questions(data)
    assert [item["block_start"] for item in items] == [3, 7]
    assert items[1]["selection_ready"]


def test_subquestions_stay_inside_parent_and_analysis_only_is_an_answer_range():
    data = preview(
        "【例1】观察图示。",
        "【待查看原文：图片或图形】",
        "回答下列问题：",
        "（1）填写____。",
        "",
        "(2)为什么？",
        "",
        "【解析】（1）示例。",
        "（2）示例理由。",
        "必杀技",
        "1．知识说明。",
    )
    (item,) = index_word_questions(data)
    assert (
        item["block_start"],
        item["question_end"],
        item["answer_start"],
        item["block_end"],
    ) == (1, 7, 8, 9)
    assert [block["index"] for block in item["question_blocks"]] == list(range(1, 8))


def test_table_and_assets_keep_original_order_and_all_metadata_without_mutation():
    assets = [
        {
            "asset_id": "img-a",
            "block_index": 2,
            "preview_supported": False,
            "mime_type": "image/x-wmf",
        },
        {"asset_id": "img-b", "block_index": 2, "preview_supported": True},
    ]
    data = preview(
        "【例1】下表哪项正确？",
        "【表格开始】\n〔第1行·第1列〕图形\n【表格结束】",
        "【答案】A",
        assets=assets,
    )
    data["blocks"][1]["custom_metadata"] = {"nested": [1, 2]}
    data["blocks"][1]["warnings"] = ["原图需查看。"]
    original = deepcopy(data)
    (item,) = index_word_questions(data)
    assert item["question_blocks"][1]["assets"] == assets
    assert item["question_blocks"][1]["custom_metadata"] == {"nested": [1, 2]}
    assert "原图需查看。" in item["warnings"]
    assert item["selection_ready"] and item["export_ready"]
    item["question_blocks"][1]["assets"][0]["mime_type"] = "changed"
    item["question_blocks"][1]["custom_metadata"]["nested"].append(3)
    assert data == original


def test_explicit_image_only_question_is_selectable_without_multimodal_configuration():
    data = preview(
        "【例1】",
        "【待查看原文：图片或图形】",
        assets=[{"asset_id": "image", "block_index": 2}],
    )
    (item,) = index_word_questions(data)
    assert item["selection_ready"] is True
    assert item["answer_start"] is None
    assert item["block_end"] == item["question_end"] == 2


def test_shared_material_attaches_only_from_explicit_markers():
    data = preview(
        "普通题前说明，不自动关联。",
        "【共同材料】用于后面各题。",
        "共享表格内容。",
        "【例1】根据上述材料判断。",
        "【答案】A",
        "【例2】根据上述材料判断。",
        "【答案】B",
        "知识点02 新部分",
        "【例3】根据上述材料判断。",
        "【答案】C",
    )
    items = index_word_questions(data)
    assert [[block["index"] for block in item["context_blocks"]] for item in items] == [
        [2, 3],
        [2, 3],
        [],
    ]
    assert items[0]["export_ready"] and items[1]["export_ready"]
    assert items[2]["boundary_status"] == "needs_review"
    assert any("引用前文" in warning for warning in items[2]["warnings"])


def test_single_material_is_not_assumed_to_be_shared_by_later_questions():
    data = preview("【材料】单题材料。", "【例1】判断。", "【答案】A", "【例2】判断。")
    first, second = index_word_questions(data)
    assert [block["index"] for block in first["context_blocks"]] == [1]
    assert second["context_blocks"] == []


def test_combined_question_answer_block_is_display_only_and_never_export_ready():
    data = preview("【例1】问题？【答案】A【解析】参考理由。")
    (item,) = index_word_questions(data)
    assert item["question_blocks"][0]["text"] == "【例1】问题？"
    assert item["answer_blocks"][0]["text"] == "【答案】A【解析】参考理由。"
    assert item["question_blocks"][0]["source_text"] == data["blocks"][0]["text"]
    assert item["question_end"] == item["answer_start"] == 1
    assert item["export_ready"] is False and item["selection_ready"] is True
    # Choosing that same complete paragraph cannot hide its answer marker.
    changed = apply_question_range(
        data, item, block_start=1, question_end=1, answer_start=None, block_end=1
    )
    assert changed["question_blocks"][0]["text"] == data["blocks"][0]["text"]
    assert changed["export_ready"] is False


def test_multiple_questions_inside_one_block_are_not_silently_exported():
    (item,) = index_word_questions(
        preview("【例1】问题一？【答案】A【例2】问题二？【答案】B")
    )
    assert item["boundary_status"] == "needs_review"
    assert any("多个明确题目标记" in warning for warning in item["warnings"])
    assert item["export_ready"] is False


def test_repeated_printed_labels_have_stable_distinct_keys_and_content_sensitive_revisions():
    data = preview(
        "【例1】问题甲？", "【答案】A", "知识点02", "【例1】问题乙？", "【答案】B"
    )
    first = index_word_questions(data)
    again = index_word_questions(deepcopy(data))
    assert first == again and first[0]["key"] != first[1]["key"]
    data["blocks"][0]["text"] += "变化"
    changed = index_word_questions(data)
    assert changed[0]["key"] == first[0]["key"]
    assert changed[0]["revision"] != first[0]["revision"]
    data["extraction_revision"] = "extractor-v2"
    assert index_word_questions(data)[1]["revision"] != first[1]["revision"]


def test_manual_range_rebuilds_whole_blocks_context_and_version_but_preserves_key():
    data = preview(
        "材料首段",
        "材料次段",
        "【例1】问题？",
        "选项补充",
        "【答案】A",
        "【解析】理由",
        "额外结尾",
    )
    (original,) = index_word_questions(data)
    changed = apply_question_range(
        data,
        original,
        block_start=3,
        question_end=4,
        answer_start=5,
        block_end=6,
        context_start=1,
        context_end=2,
    )
    assert changed["key"] == original["key"]
    assert changed["revision"] != original["revision"]
    assert changed["boundary_status"] == "manual_range"
    assert [block["index"] for block in changed["context_blocks"]] == [1, 2]
    assert [block["index"] for block in changed["answer_blocks"]] == [5, 6]
    assert changed["export_ready"]
    again = apply_question_range(
        data,
        changed,
        block_start=3,
        question_end=4,
        answer_start=5,
        block_end=6,
        context_start=1,
        context_end=2,
    )
    assert again == changed


@pytest.mark.parametrize("context_index", [2, 3, 4])
def test_manual_context_cannot_relabel_another_questions_answers(context_index):
    data = preview(
        "【例1】问题一？",
        "【答案】A",
        "【解析】理由。",
        "无标记的答案续段。",
        "【例2】问题二？",
        "【答案】B",
    )
    first, second = index_word_questions(data)
    assert [block["index"] for block in first["answer_blocks"]] == [2, 3, 4]
    changed = apply_question_range(
        data,
        second,
        block_start=5,
        question_end=5,
        answer_start=6,
        block_end=6,
        context_start=context_index,
        context_end=context_index,
    )
    assert changed["key"] == second["key"]
    assert changed["revision"] != second["revision"]
    assert changed["selection_ready"] is True
    assert changed["export_ready"] is False
    assert changed["boundary_status"] == "needs_review"
    assert any("共同材料范围包含答案" in warning for warning in changed["warnings"])
    assert [block["index"] for block in changed["context_blocks"]] == [context_index]


def test_automatic_shared_material_with_answer_marker_is_not_export_ready():
    data = preview(
        "【共同材料】题前文字。",
        "【解析】不应作为学生材料的内容。",
        "【例1】问题？",
        "【答案】A",
    )
    (item,) = index_word_questions(data)
    assert [block["index"] for block in item["context_blocks"]] == [1, 2]
    assert item["selection_ready"] is True
    assert item["export_ready"] is False
    assert item["boundary_status"] == "needs_review"
    assert any("共同材料范围包含答案" in warning for warning in item["warnings"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"block_start": True},
        {"block_start": 0},
        {"question_end": 20},
        {"block_start": 3, "question_end": 2},
        {"answer_start": 2},
        {"answer_start": 4},
        {"answer_start": None},
        {"context_start": 1},
        {"context_start": 2, "context_end": 1},
        {"context_start": 2, "context_end": 3},
    ],
)
def test_invalid_manual_ranges_fail_explicitly(kwargs):
    data = preview("材料", "【例1】问题？", "【答案】A", "【解析】理由")
    (item,) = index_word_questions(data)
    bounds = {"block_start": 2, "question_end": 2, "answer_start": 3, "block_end": 4}
    bounds.update(kwargs)
    with pytest.raises(ValueError):
        apply_question_range(data, item, **bounds)


def test_wrong_source_stale_preview_or_mutated_item_cannot_be_rebound():
    data = preview("【例1】问题？", "【答案】A")
    (item,) = index_word_questions(data)
    wrong = deepcopy(data)
    wrong["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="同一来源"):
        apply_question_range(
            wrong, item, block_start=1, question_end=1, answer_start=2, block_end=2
        )
    stale = deepcopy(data)
    stale["revision"] = "new"
    with pytest.raises(ValueError, match="变化"):
        apply_question_range(
            stale, item, block_start=1, question_end=1, answer_start=2, block_end=2
        )
    item["question_blocks"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="版本"):
        apply_question_range(
            data, item, block_start=1, question_end=1, answer_start=2, block_end=2
        )


def test_duplicate_or_out_of_order_blocks_and_asset_references_are_rejected():
    data = preview("【例1】问题？", "【答案】A")
    for indexes in ([1, 1], [2, 1]):
        changed = deepcopy(data)
        for block, index in zip(changed["blocks"], indexes):
            block["index"] = index
        with pytest.raises(ValueError, match="唯一"):
            index_word_questions(changed)
    data["assets"] = [{"asset_id": "img", "block_index": 99}]
    with pytest.raises(ValueError, match="不存在"):
        index_word_questions(data)


def test_synthetic_one_based_boundaries_match_real_layout_patterns():
    # These are synthetic layout patterns, with no private paths or source text.
    blocks = [""] * 120
    for index, text in {
        10: "【即学即练1】问题？",
        11: "A．选项",
        14: "D．选项",
        15: "【答案】D",
        16: "【解析】理由",
        18: "知识点02",
        28: "【即学即练2】问题？",
        29: "【待查看原文：图片或图形】",
        33: "D．选项",
        34: "【答案】C",
        35: "【解析】理由",
        37: "知识点03",
        90: "【例1】分类？",
        91: "【表格开始】表格【表格结束】",
        92: "【答案】B",
        93: "【解析】理由",
        94: "必杀技",
        97: "【同步练习1】问题？",
        101: "D．选项",
        102: "【答案】D",
        103: "【解析】理由",
        105: "常见考法二",
        106: "【例1】如图所示。",
        107: "【待查看原文：图片或图形】",
        108: "回答问题：",
        115: "（3）为什么？",
        118: "【解析】（1）理由",
        119: "（2）理由",
        120: "（3）理由",
    }.items():
        blocks[index - 1] = text
    items = index_word_questions(preview(*blocks))
    assert [
        (
            item["block_start"],
            item["question_end"],
            item["answer_start"],
            item["block_end"],
        )
        for item in items
    ] == [
        (10, 14, 15, 16),
        (28, 33, 34, 35),
        (90, 91, 92, 93),
        (97, 101, 102, 103),
        (106, 117, 118, 120),
    ]
