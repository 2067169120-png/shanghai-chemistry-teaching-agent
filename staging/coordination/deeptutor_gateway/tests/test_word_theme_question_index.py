from test_word_question_index import preview

from integrations.deeptutor_shchem_v1.desktop_word_question_index import (
    apply_question_range,
    index_word_questions,
)


def test_printed_theme_preserves_interleaved_shared_material_and_answers():
    data = preview("考试时间：60分钟，满分：100分", "可能用到的相对原子质量：H-1", "一、金属应用(20分)",
                   "先阅读公共材料", "(1)写出化学式____", "后续实验数据", "(2)计算质量____", "【答案】(1)示例", "(2)示例解析",
                   "二、反应原理（20分）", "（1）解释原因____", "【答案】示例")
    items = index_word_questions(data)
    assert len(items) == 2
    assert items[0]["selection_unit"] == "theme_big_question"
    assert items[0]["printed_subpart_starts"] == [5, 7]
    assert [b["index"] for b in items[0]["question_blocks"]] == [3, 4, 5, 6, 7]
    assert [b["index"] for b in items[0]["answer_blocks"]] == [8, 9]
    assert [b["index"] for b in items[1]["context_blocks"]] == [2]
    changed = apply_question_range(data, items[0], block_start=3, question_end=7, answer_start=8, block_end=9, context_start=2, context_end=2)
    assert changed["selection_unit"] == "theme_big_question"
    assert changed["printed_subpart_starts"] == [5, 7]


def test_scored_lecture_heading_without_paper_header_does_not_replace_index():
    data = preview("一、知识概念(20分)", "(1)电解质定义", "【例1】写出____", "【答案】示例")
    items = index_word_questions(data)
    assert len(items) == 1 and items[0]["block_start"] == 3


def test_scored_header_without_subpart_evidence_not_a_theme():
    assert not index_word_questions(preview("考试时间60分钟，满分100分", "一、知识概念(20分)", "【答案】目录不是题目"))
