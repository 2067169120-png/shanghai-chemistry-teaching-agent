import json
import sqlite3
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeError,
    WordQuestionAttributeStore,
    apply_teacher_edits,
    build_teacher_updates,
    suggest_attributes,
    validate_attributes,
)


def question(
    text="请判断 NaCl 是否为电解质。", *, key="word-question-test", chapter=""
):
    return {
        "key": key,
        "source_sha256": "a" * 64,
        "revision": "q-v1",
        "source_revision": "source-v1",
        "index_revision": "index-v3",
        "extraction_revision": "extract-v1",
        "chapter": chapter,
        "question_blocks": [{"index": 2, "text": text, "assets": []}],
        "answer_blocks": [],
        "context_blocks": [],
        "warnings": [],
    }


def metadata(**updates):
    return {
        "collection_name": "上海专用高三一轮复习",
        "package_id": "PKG-001",
        "source_name": "2026年上海等级考.docx",
        "lecture_topic": "氧化还原",
        "usage_context": "高三一轮复习",
        **updates,
    }


def test_usage_grade_does_not_invent_original_exam_or_knowledge_from_filename():
    row = suggest_attributes(question(), metadata())
    assert row["applicable_grades"]["values"] == ["grade_12"]
    assert row["applicable_grades"]["status"] == "usage_positioning"
    assert "不等于原题年级" in row["applicable_grades"]["basis"]
    assert all(
        row["original_source"][name]["value"] == "unknown"
        for name in ("grade", "exam_type", "year", "region")
    )
    assert row["original_source"]["display_label"] == "讲义收录题·原考试待确认"
    assert row["primary_knowledge"]["id"] == "unknown"
    assert row["primary_knowledge"]["label"] == "电解质与电离"
    assert row["primary_knowledge"]["evidence"][0]["block_index"] == 2
    assert row["curriculum_status"] == "pending_mapping"
    assert row["answer_status"]["value"] == "not_detected_pending_review"


@pytest.mark.parametrize(
    ("text", "identifier"),
    [("烷烃与烯烃的反应有何不同？", "K16"), ("配平下列氧化还原反应的方程式。", "K11")],
)
def test_explicit_knowledge_is_only_an_evidenced_suggestion(text, identifier):
    row = suggest_attributes(question(text), metadata())
    assert row["primary_knowledge"]["id"] == identifier
    assert row["primary_knowledge"]["status"] == "auto_suggested"
    assert row["primary_knowledge"]["evidence"][0]["quote"] in text
    assert row["annotation_source"] == "auto_suggested"


def test_exact_curriculum_heading_and_answer_context_signals():
    item = question("根据上述材料及如图装置，解释原因。", chapter="物质的分类")
    item["answer_blocks"] = [{"index": 3, "text": "【答案】略。", "assets": []}]
    catalog = [
        {
            "node_key": "TB-M1-C1:1.1",
            "section_title": "物质的分类",
            "chapter_id": "TB-M1-C1",
            "volume_id": "TB-M1",
        }
    ]
    row = suggest_attributes(item, {}, catalog)
    assert row["curriculum_candidates"][0]["section_key"] == "TB-M1-C1:1.1"
    assert row["curriculum_candidates"][0]["evidence"][0]["kind"] == "source_chapter"
    assert row["material_status"]["missing_context"]
    assert row["material_status"]["missing_visual"]
    assert row["answer_status"]["value"] == "present_nonofficial_unverified"
    item["context_blocks"] = [{"index": 1, "text": "本题公共装置", "assets": [{}]}]
    updated = suggest_attributes(item, {})
    assert updated["material_status"]["has_shared_context"]
    assert not updated["material_status"]["missing_context"]
    assert not updated["material_status"]["missing_visual"]


def test_original_facts_only_from_question_attribution_and_conflicts_stay_unknown():
    row = suggest_attributes(
        question("（2024·上海·徐汇区·高三二模）解释氧化还原反应。"), metadata()
    )
    original = row["original_source"]
    assert original["grade"]["value"] == "grade_12"
    assert original["exam_type"]["value"] == "second_mock"
    assert original["year"]["value"] == "2024"
    assert original["region"]["value"] == "上海·徐汇区"
    assert original["exam_type"]["status"] == "source_observed"
    assert original["citation_quotes"][0]["block_index"] == 2
    changed = suggest_attributes(
        question("（2026届高三）某反应。（2024·江苏·一模）（2025·山东·二模）"),
        metadata(),
    )["original_source"]
    assert changed["exam_type"]["value"] == "unknown"
    assert changed["year"]["value"] == "unknown"
    assert changed["region"]["value"] == "unknown"
    cohort = suggest_attributes(question("（2026届高三）某反应"), {})["original_source"]
    assert cohort["year"]["value"] == "unknown"


def test_empty_facts_can_save_and_reads_do_not_create_missing_state(tmp_path):
    store = WordQuestionAttributeStore(tmp_path / "missing")
    assert store.get("missing") is None
    assert store.get_many(["missing"]) == {}
    assert store.history("missing") == []
    assert not store.root.exists()
    row = suggest_attributes(question("待核对"), {})
    assert row["primary_knowledge"]["status"] == "unknown"
    saved = store.save_many([row])[0]
    assert store.get(row["key"]) == saved
    assert store.save_many([row]) == [saved]
    assert len(store.history(row["key"])) == 1
    assert store.get(row["key"], source_sha256="b" * 64) is None
    assert store.get(row["key"], question_revision="stale") is None


def test_get_many_uses_one_readonly_connection_across_chunks_and_copies(
    tmp_path, monkeypatch
):
    store = WordQuestionAttributeStore(tmp_path)
    saved = store.save_many([suggest_attributes(question(), {})])[0]
    connect = sqlite3.connect
    calls = []

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    rows = store.get_many(
        [saved["key"], *[f"missing-{i}" for i in range(1200)], saved["key"]]
    )
    assert rows == {saved["key"]: saved}
    assert len(calls) == 1
    assert calls[0][0][0].endswith("?mode=ro")
    assert calls[0][1]["uri"] is True
    rows[saved["key"]]["teacher_note"] = "not saved"
    assert store.get(saved["key"])["teacher_note"] == ""


def test_closed_schema_and_digest_fail_before_save_and_bulk_read_rejects_tamper(
    tmp_path,
):
    store = WordQuestionAttributeStore(tmp_path / "state")
    row = suggest_attributes(question(), {})
    for invalid in (
        {**row, "bytes": "unexpected"},
        {**row, "teacher_note": "tampered"},
    ):
        with pytest.raises(WordQuestionAttributeError):
            store.save_many([invalid])
    assert not store.root.exists()
    saved = store.save_many([row])[0]
    saved["teacher_note"] = "tampered"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE attributes SET payload=?", (json.dumps(saved),))
    with pytest.raises(WordQuestionAttributeError, match="版本不一致"):
        store.get_many([row["key"]])


def test_teacher_edits_preserved_even_when_auto_question_revision_changes(
    tmp_path,
):
    store = WordQuestionAttributeStore(tmp_path)
    suggestion = suggest_attributes(question(), {})
    saved = store.save_many([suggestion])[0]
    primary = deepcopy(saved["primary_knowledge"])
    primary["label"] = "教师核对后的电解质"
    edited = store.save_teacher_edit(
        saved["key"],
        {"primary_knowledge": primary, "teacher_note": "课堂适用"},
        expected_revision=saved["revision"],
    )
    assert edited["primary_knowledge"]["status"] == "teacher_confirmed"
    assert edited["edit_version"] == 2
    assert store.save_many([suggestion]) == [edited]
    with pytest.raises(WordQuestionAttributeError, match="新版本"):
        store.save_teacher_edit(
            saved["key"], {"teacher_note": "stale"}, expected_revision=saved["revision"]
        )
    with pytest.raises(WordQuestionAttributeError, match="不能改写"):
        store.save_teacher_edit(
            saved["key"],
            {"source_sha256": "b" * 64},
            expected_revision=edited["revision"],
        )
    changed = question()
    changed["revision"] = "q-v2"
    refreshed = store.save_many([suggest_attributes(changed, {})])[0]
    assert refreshed == edited
    assert store.get(saved["key"], question_revision="q-v2") is None
    assert [row["edit_version"] for row in store.history(saved["key"])] == [1, 2]


def test_batch_source_binding_conflict_rolls_back(tmp_path):
    store = WordQuestionAttributeStore(tmp_path)
    saved = store.save_many([suggest_attributes(question(), {})])[0]
    changed = question()
    changed["source_sha256"] = "b" * 64
    new = suggest_attributes(question(key="new-question"), {})
    with pytest.raises(WordQuestionAttributeError, match="绑定不能改变"):
        store.save_many([new, suggest_attributes(changed, {})])
    assert store.get("new-question") is None
    assert validate_attributes(store.get(saved["key"])) == saved


def edit_catalog():
    return {
        "knowledge_points": [
            {"id": "K01", "name": "物质分类与计量"},
            {"id": "K11", "name": "氧化还原反应和电化学"},
            {"id": "K16", "name": "烃及其衍生物"},
        ],
        "nodes": [
            {
                "node_key": "TB-M1-C1:1.1",
                "chapter_id": "TB-M1-C1",
                "volume_id": "TB-M1",
                "section_title": "物质的分类",
            }
        ],
    }


def edit_selection(row, **changes):
    return {
        "primary_knowledge_id": row["primary_knowledge"]["id"],
        "supporting_knowledge_ids": [
            item["id"] for item in row["supporting_knowledge"]
        ],
        "applicable_grades": row["applicable_grades"]["values"],
        "original_exam_type": row["original_source"]["exam_type"]["value"],
        "curriculum_section_keys": [
            item["section_key"] for item in row["curriculum_candidates"]
        ],
        "teacher_note": row["teacher_note"],
        **changes,
    }


def test_teacher_proposal_catalogue_evidence_and_untouched_original_facts():
    row = suggest_attributes(
        question("（2024·上海·高二期中）烷烃与烯烃的反应"), metadata(), edit_catalog()
    )
    before = deepcopy(row)
    assert build_teacher_updates(row, edit_selection(row), edit_catalog()) == {}
    changes = build_teacher_updates(
        row,
        edit_selection(
            row,
            primary_knowledge_id="K11",
            supporting_knowledge_ids=["K16"],
            applicable_grades=["grade_11", "grade_12"],
            original_exam_type="school_exam",
            curriculum_section_keys=["TB-M1-C1:1.1"],
            teacher_note="教师核对学校原卷后修订；用于高三复习",
        ),
        edit_catalog(),
    )
    edited = apply_teacher_edits(row, changes, curriculum_entries=edit_catalog())
    assert row == before
    assert edited["primary_knowledge"]["label"] == "氧化还原反应和电化学"
    assert edited["primary_knowledge"]["status"] == "teacher_confirmed"
    assert edited["original_source"]["exam_type"]["status"] == "teacher_confirmed"
    for field in ("year", "region", "grade", "citation_quotes"):
        assert edited["original_source"][field] == row["original_source"][field]
    assert edited["applicable_grades"]["values"] == ["grade_11", "grade_12"]
    assert edited["source"] == row["source"]
    assert edited["answer_status"] == row["answer_status"]
    assert edited["material_status"] == row["material_status"]


@pytest.mark.parametrize(
    "changes",
    [
        {"primary_knowledge_id": "K99"},
        {"primary_knowledge_id": "自定义"},
        {"primary_knowledge_id": "K11", "supporting_knowledge_ids": ["K11"]},
        {"supporting_knowledge_ids": ["K01", "K01"]},
        {"applicable_grades": ["高三"]},
        {"original_exam_type": "上海专用"},
        {"curriculum_section_keys": ["invented-section"]},
        {"teacher_note": "a" * 2001},
    ],
)
def test_teacher_builder_rejects_invalid_or_invented_catalogue_values(changes):
    row = suggest_attributes(question(), {})
    with pytest.raises(WordQuestionAttributeError):
        build_teacher_updates(row, edit_selection(row, **changes), edit_catalog())


def test_save_enforces_catalogue_labels_and_preserves_original_citation(tmp_path):
    store = WordQuestionAttributeStore(tmp_path)
    row = store.save_many([suggest_attributes(question(), {})])[0]
    changes = build_teacher_updates(
        row, edit_selection(row, primary_knowledge_id="K11"), edit_catalog()
    )
    changes["primary_knowledge"]["label"] = "伪装成规范编号的任意标签"
    with pytest.raises(WordQuestionAttributeError, match="编号和名称一致"):
        store.save_teacher_edit(
            row["key"],
            changes,
            expected_revision=row["revision"],
            curriculum_entries=edit_catalog(),
        )
    original = deepcopy(row["original_source"])
    original["citation_quotes"] = [
        {"kind": "question_text", "quote": "编造原题出处", "block_index": 2}
    ]
    with pytest.raises(WordQuestionAttributeError, match="原题引文须保留"):
        store.save_teacher_edit(
            row["key"], {"original_source": original}, expected_revision=row["revision"]
        )
    assert store.get(row["key"]) == row
    assert len(store.history(row["key"])) == 1


def test_note_only_does_not_confirm_unedited_auto_suggestions(tmp_path):
    store = WordQuestionAttributeStore(tmp_path)
    row = store.save_many([suggest_attributes(question("氧化还原反应"), metadata())])[0]
    edited = store.save_teacher_edit(
        row["key"],
        {"teacher_note": "暂不核定知识点"},
        expected_revision=row["revision"],
    )
    assert edited["primary_knowledge"] == row["primary_knowledge"]
    assert edited["applicable_grades"] == row["applicable_grades"]
    assert WordQuestionAttributeStore(tmp_path).get(row["key"]) == edited
    assert store.save_many([row]) == [edited]
    assert store.history(row["key"])[0] == row


def test_initial_edit_atomic_history_and_conflicting_suggestion_does_not_overwrite(
    tmp_path,
):
    store = WordQuestionAttributeStore(tmp_path)
    initial = suggest_attributes(question(), {})
    edited = store.save_teacher_edit(
        initial["key"],
        {"teacher_note": "第一次人工标注"},
        expected_revision=initial["revision"],
        initial_attributes=initial,
    )
    history = store.history(initial["key"])
    assert [entry["edit_version"] for entry in history] == [1, 2]
    assert history[0]["annotation_source"] == "auto_suggested"
    assert history[1] == edited
    assert edited["question_revision"] == initial["question_revision"]
    with pytest.raises(WordQuestionAttributeError, match="新版本"):
        store.save_teacher_edit(
            initial["key"],
            {"teacher_note": "冲突"},
            expected_revision=initial["revision"],
            initial_attributes=initial,
        )
    assert store.get(initial["key"]) == edited


def test_invalid_initial_proposal_leaves_no_partial_attributes_or_history(tmp_path):
    store = WordQuestionAttributeStore(tmp_path)
    initial = suggest_attributes(question(), {})
    with pytest.raises(WordQuestionAttributeError):
        store.save_teacher_edit(
            initial["key"],
            {"teacher_note": "a" * 2001},
            expected_revision=initial["revision"],
            initial_attributes=initial,
        )
    assert store.get(initial["key"]) is None
    assert store.history(initial["key"]) == []


def test_explicit_resegmentation_edit_keeps_old_teacher_and_new_auto_baselines(
    tmp_path,
):
    store = WordQuestionAttributeStore(tmp_path)
    initial = suggest_attributes(question(), {})
    old = store.save_teacher_edit(
        initial["key"],
        {"teacher_note": "旧题教师备注"},
        expected_revision=initial["revision"],
        initial_attributes=initial,
    )
    changed = question("烷烃结构")
    changed["revision"] = "q-v2"
    replacement = suggest_attributes(changed, {}, edit_catalog())
    assert store.save_many([replacement]) == [old]
    with pytest.raises(WordQuestionAttributeError, match="新版本"):
        store.save_teacher_edit(
            old["key"],
            {"teacher_note": "过期窗口"},
            expected_revision=initial["revision"],
            replacement_attributes=replacement,
        )
    edited = store.save_teacher_edit(
        old["key"],
        {"teacher_note": "重新核对新题"},
        expected_revision=old["revision"],
        replacement_attributes=replacement,
    )
    history = store.history(old["key"])
    assert [row["edit_version"] for row in history] == [1, 2, 3, 4]
    assert history[1] == old
    assert history[2]["annotation_source"] == "auto_suggested"
    assert history[2]["teacher_note"] == ""
    assert edited["question_revision"] == "q-v2"
    assert edited["primary_knowledge"]["id"] == "K16"
    assert edited["teacher_note"] == "重新核对新题"


def test_resegmentation_requires_same_source_and_new_question_revision(tmp_path):
    store = WordQuestionAttributeStore(tmp_path)
    old = store.save_many([suggest_attributes(question(), {})])[0]
    for changed in (
        question(),
        {**question(), "source_sha256": "b" * 64, "revision": "q-v2"},
        {**question(key="another"), "revision": "q-v2"},
    ):
        replacement = suggest_attributes(changed, {})
        with pytest.raises(WordQuestionAttributeError, match="同一来源的新题目版本"):
            store.save_teacher_edit(
                old["key"],
                {"teacher_note": "bad"},
                expected_revision=old["revision"],
                replacement_attributes=replacement,
            )
    assert store.get(old["key"]) == old
    assert len(store.history(old["key"])) == 1
