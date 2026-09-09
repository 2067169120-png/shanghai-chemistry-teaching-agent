import json
import sqlite3
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeError,
    WordQuestionAttributeStore,
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


def test_teacher_edits_revision_history_preserved_until_source_question_changes(
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
    assert refreshed["edit_version"] == 3
    assert refreshed["annotation_source"] == "auto_suggested"
    assert [row["edit_version"] for row in store.history(saved["key"])] == [1, 2, 3]


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
