"""Pure synthetic source metadata for the current-paper details QA."""

def rows():
    return [
        {"key": "core-synthetic", "kind": "core_theme", "title_zh": "合成主题甲 · 三个作答单元",
         "source_zh": "自编验收原卷甲", "source_ref": {"kind": "core_theme", "scope": "master",
          "paper_id": "P-SYNTH", "theme_id": "T-SYNTH", "data_snapshot_id": "c" * 64},
         "content": {"paper": {"id": "P-SYNTH"}, "theme": {"id": "T-SYNTH"}, "atomic_chain": [
             {"printed_question_id": "Q-A", "atomic_part_id": "A-1"},
             {"printed_question_id": "Q-A", "atomic_part_id": "A-2"},
             {"printed_question_id": "Q-B", "atomic_part_id": "A-3"}]},
         "settings": {"score_per_atomic": 2, "answer_space_lines": 0, "atomic_settings": {}}},
        {"key": "word-synthetic", "kind": "word_question", "title_zh": "合成 Word 完整题乙 · 保留题段整体",
         "source_zh": "自编验收讲义乙.docx", "source_ref": {"kind": "word_question", "key": "WORD-SYNTH",
          "batch_id": "BATCH-SYNTH", "archive_source_id": "SOURCE-SYNTH", "revision": "word-r1",
          "source_sha256": "a" * 64, "origin_block_start": 2},
         "content": {"question_blocks": [{"index": 2}, {"index": 3}], "context_blocks": [{"index": 1}],
                     "answer_blocks": [{"index": 4}], "selection_ready": True}, "settings": {"points": 4.5}},
        {"key": "visual-synthetic", "kind": "personal_visual_theme", "title_zh": "合成图片主题丙 · 一项原分值缺失",
         "source_zh": "自编验收图片丙", "source_ref": {"kind": "personal_visual_theme", "theme_id": "V-THEME",
          "batch_id": "VISUAL-BATCH-SYNTH", "candidate_revision": "v1", "candidate_sha256": "b" * 64,
          "crop_head_sha256": "d" * 64, "selections": [{"key": "V-A1", "revision": "v1"}, {"key": "V-A2", "revision": "v1"}]},
         "content": {"theme": {"theme_big_question_id": "V-THEME", "printed_questions": [
             {"printed_question_id": "V-Q1", "atomic_parts": [{"atomic_part_id": "V-A1"}]},
             {"printed_question_id": "V-Q2", "atomic_parts": [{"atomic_part_id": "V-A2"}]}]},
             "source_scores": [{"printed_question_id": "V-Q1", "atomic_part_id": "V-A1", "status": "present", "max_score": 3},
                               {"printed_question_id": "V-Q2", "atomic_part_id": "V-A2", "status": "missing", "max_score": None}]},
         "settings": {"use_source_scores": True}},
    ]
