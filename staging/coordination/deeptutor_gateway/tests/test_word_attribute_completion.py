from copy import deepcopy

import pytest
from test_word_question_attributes import metadata, question

from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    suggest_attributes,
)


def directory(*titles):
    return {
        "nodes": [
            {
                "section_title": title,
                "node_key": f"s-{i}",
                "chapter_id": f"c-{i}",
                "volume_id": "v",
            }
            for i, title in enumerate(titles)
        ]
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("比较两种核素的中子数。", "K04"),
        ("根据同周期元素第一电离能判断电负性大小。", "K12"),
        ("从题给反应确定氧化剂和还原剂。", "K11"),
        ("利用盖斯定律计算中和热。", "K08"),
        ("判断稀释后电离度与电离常数如何变化。", "K10"),
        ("利用溶度积判断沉淀转化。", "K10"),
        ("说明两种物质形成氢键的条件。", "K13"),
        ("设计萃取与蒸馏的分离方案。", "K01"),
        ("说明过氧化钠参与反应的作用。", "K05"),
        ("判断肽键数量。", "K18"),
    ],
)
def test_explicit_subject_terms_are_candidate_only(text, expected):
    item = question(text)
    before = deepcopy(item)
    result = suggest_attributes(item, metadata())
    assert result["primary_knowledge"]["id"] == expected
    assert result["primary_knowledge"]["status"] == "auto_suggested"
    assert item == before
    assert result["original_source"]["exam_type"]["value"] == "unknown"


def test_actual_question_precedes_lecture_heading_and_shared_material():
    item = question("根据电离常数比较弱酸的强弱。", chapter="氧化还原反应")
    item["context_blocks"] = [{"index": 1, "text": "氯气性质的教学材料", "assets": []}]
    result = suggest_attributes(
        item,
        {},
        {
            "knowledge_points": [
                {"id": "K02", "name": "卤素", "subtopics": ["氧化还原"]}
            ]
        },
    )
    assert result["primary_knowledge"]["id"] == "K10"
    assert {v["id"] for v in result["supporting_knowledge"]} >= {"K02", "K11"}


def test_redox_generic_term_does_not_create_halogen_tag():
    result = suggest_attributes(
        question("判断氧化还原反应。"),
        {},
        {"knowledge_points": [{"id": "K02", "subtopics": ["氧化还原"]}]},
    )
    assert result["primary_knowledge"]["id"] == "K11"
    assert "K02" not in {v["id"] for v in result["supporting_knowledge"]}


def test_directory_alias_uses_current_question_block_and_real_parent_ids():
    result = suggest_attributes(
        question("写出基态原子的轨道表示式。"),
        {},
        directory("多电子原子核外电子的排布"),
    )
    mapped = result["curriculum_candidates"]
    assert len(mapped) == 1
    assert mapped[0]["section_key"] == "s-0"
    assert mapped[0]["chapter_id"] == "c-0"
    assert mapped[0]["evidence"][0]["block_index"] == 2


def test_alias_needs_existing_title_and_not_lecture_heading_alone():
    catalog = directory("多电子原子核外电子的排布")
    assert not suggest_attributes(
        question("解释原因。", chapter="轨道表示式"), {}, catalog
    )["curriculum_candidates"]
    assert not suggest_attributes(
        question("写出轨道表示式。"), {}, directory("未定义教材节")
    )["curriculum_candidates"]


@pytest.mark.parametrize("text", ["电离平衡常数", "水解平衡常数", "沉淀溶解平衡常数"])
def test_ionic_constants_do_not_match_general_reaction_limit(text):
    result = suggest_attributes(
        question("比较" + text), {}, directory("化学反应的限度")
    )
    assert not result["curriculum_candidates"]
    assert result["primary_knowledge"]["id"] != "K09"


def test_unsaturated_title_does_not_also_match_saturated_title():
    result = suggest_attributes(
        question("讨论不饱和烃的结构。"), {}, directory("饱和烃", "不饱和烃")
    )
    assert [r["label"] for r in result["curriculum_candidates"]] == ["不饱和烃"]


def test_generic_hydrolysis_and_answer_text_do_not_create_salt_mapping():
    item = question("比较两种物质的水解。")
    item["answer_blocks"] = [{"index": 3, "text": "盐类水解是干扰答案", "assets": []}]
    result = suggest_attributes(item, {}, directory("酸碱中和与盐类水解"))
    assert not result["curriculum_candidates"]
    assert result["primary_knowledge"]["id"] == "unknown"


def test_option_only_keyword_does_not_decide_primary_knowledge():
    item = question("下列说法正确的是。")
    item["question_blocks"].append(
        {"index": 3, "text": "A．反应的热效应决定所有现象。", "assets": []}
    )
    result = suggest_attributes(item, {})
    assert result["primary_knowledge"]["id"] == "unknown"
    assert {r["id"] for r in result["supporting_knowledge"]} == {"K08"}
