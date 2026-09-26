"""Real local-source/facade round trips; no configured provider or teacher data."""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from test_desktop_visual_import_facade import (
    desktop_paths as desktop_paths,  # noqa: PLC0414
)
from test_word_questions_service import _import

from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionError


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    value = {"knowledge_points": [{"id": "K10", "name": "电离与离子反应"}], "nodes": []}
    monkeypatch.setattr(
        "integrations.deeptutor_shchem_v1.desktop_word_questions.load_attribute_catalog",
        lambda _root: value,
    )
    return value


def _save(facade, item, options, **updates):
    return facade.word_question_save_attributes(
        item["key"],
        item["revision"],
        updates,
        expected_attribute_revision=options["attributes"]["revision"],
        expected_stored_revision=options["stored_revision"],
    )


def test_options_are_read_only_then_save_preserves_baseline_and_source(
    desktop_paths, tmp_path
):
    facade, source, provider = _import(desktop_paths, tmp_path)
    before = source.read_bytes()
    item = facade.word_question_catalog()["items"][0]
    store = facade._word_questions().attribute_store
    assert not store.path.exists()
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    assert options["stored_revision"] is None and options["history"] == []
    assert not store.path.exists()
    saved = _save(facade, item, options, teacher_note="用于高一复习")
    assert saved["annotation_source"] == "teacher_modified"
    assert saved["teacher_note"] == "用于高一复习"
    history = store.history(item["key"])
    assert [row["edit_version"] for row in history] == [1, 2]
    assert history[0]["annotation_source"] == "auto_suggested"
    assert history[0]["teacher_note"] == ""
    assert source.read_bytes() == before and provider.borrow_calls == 0
    listed = facade.word_question_catalog()["items"][0]
    assert listed["attributes"] == saved
    with pytest.raises(WordQuestionError, match="新版本"):
        _save(facade, item, options, teacher_note="过时窗口")
    assert len(store.history(item["key"])) == 2


def test_save_rechecks_source_and_rejects_invented_taxonomy(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    invalid = {
        **options["attributes"]["primary_knowledge"],
        "id": "K10",
        "label": "invented",
    }
    with pytest.raises(WordQuestionError, match="知识标签"):
        _save(facade, item, options, primary_knowledge=invalid)
    assert facade._word_questions().attribute_store.history(item["key"]) == []
    archive = (
        desktop_paths.state_root
        / "visual-import-v2"
        / "sources"
        / f"{item['source_sha256']}.docx"
    )
    archive.write_bytes(b"changed source fixture")
    with pytest.raises(WordQuestionError, match="变化"):
        _save(facade, item, options, teacher_note="不应保存")
    assert facade._word_questions().attribute_store.history(item["key"]) == []


def test_changed_ranges_keep_old_teacher_history_until_explicit_reconfirmation(
    desktop_paths, tmp_path
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    old = _save(facade, item, options, teacher_note="旧范围教师标注")
    updated = facade.word_question_update_range(
        item["key"],
        item["revision"],
        block_start=3,
        question_end=4,
        answer_start=5,
        block_end=5,
    )
    new_options = facade.word_question_attribute_options(
        updated["key"], updated["revision"]
    )
    assert new_options["warning"] and new_options["attributes"]["teacher_note"] == ""
    assert new_options["stored_revision"] == old["revision"]
    store = facade._word_questions().attribute_store
    assert store.get(item["key"]) == old
    listing = facade.word_question_catalog()["items"][0]
    assert "attributes" not in listing and listing["attribute_warning"]
    saved = _save(facade, updated, new_options, teacher_note="已重新核对新范围")
    assert saved["question_revision"] == updated["revision"]
    assert [v["edit_version"] for v in store.history(item["key"])] == [1, 2, 3, 4]
    assert store.history(item["key"])[1]["teacher_note"] == "旧范围教师标注"
    assert facade.word_question_catalog()["items"][0]["attributes"] == saved


def test_stale_range_dialog_cannot_overwrite_intervening_old_label_edit(
    desktop_paths, tmp_path
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    old = _save(facade, item, options, teacher_note="原标签")
    updated = facade.word_question_update_range(
        item["key"],
        item["revision"],
        block_start=3,
        question_end=4,
        answer_start=5,
        block_end=5,
    )
    view = facade.word_question_attribute_options(updated["key"], updated["revision"])
    store = facade._word_questions().attribute_store
    newer = store.save_teacher_edit(
        item["key"],
        {"teacher_note": "另一个窗口的教师修改"},
        expected_revision=old["revision"],
    )
    with pytest.raises(WordQuestionError, match="新版本"):
        _save(facade, updated, view, teacher_note="旧窗口的重新标注")
    assert store.get(item["key"]) == newer


def test_same_service_range_commit_cannot_interleave_with_label_commit(
    desktop_paths, tmp_path, monkeypatch
):
    facade, _, _ = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    store = facade._word_questions().attribute_store
    entered, release, range_started = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    original = store.save_teacher_edit

    def held_save(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise AssertionError("test did not release label transaction")
        return original(*args, **kwargs)

    def revise():
        range_started.set()
        return facade.word_question_update_range(
            item["key"],
            item["revision"],
            block_start=3,
            question_end=4,
            answer_start=5,
            block_end=5,
        )

    monkeypatch.setattr(store, "save_teacher_edit", held_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(
            _save, facade, item, options, teacher_note="先保存的教师标签"
        )
        assert entered.wait(5)
        revised = pool.submit(revise)
        assert range_started.wait(5)
        try:
            with pytest.raises(TimeoutError):
                revised.result(timeout=0.1)
        finally:
            release.set()
        assert saved.result(timeout=5)["question_revision"] == item["revision"]
        assert revised.result(timeout=5)["revision"] != item["revision"]
    assert len(store.history(item["key"])) == 2
