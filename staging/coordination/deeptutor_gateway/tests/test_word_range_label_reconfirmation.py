"""Explicit review of changed source ranges, with real private-store transactions."""

from copy import deepcopy

import pytest

from test_word_question_attribute_service import catalog as catalog
from test_desktop_visual_import_facade import desktop_paths as desktop_paths
from test_word_question_dialog import _Tasks, qt_app as qt_app
from test_word_questions_service import _import

from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    WordQuestionAttributeError,
)
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionError
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_attributes_dialog import (
    WordQuestionAttributesDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionDialog,
)


def _changed(desktop_paths, tmp_path, *, teacher=False):
    facade, source, provider = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    store = facade._word_questions().attribute_store
    old = store.save_many([options["attributes"]])[0]
    if teacher:
        old = store.save_teacher_edit(item["key"], {"teacher_note": "旧范围用途"},
                                      expected_revision=old["revision"])
    changed = facade.word_question_update_range(
        item["key"], item["revision"], block_start=3, question_end=4,
        answer_start=5, block_end=5,
    )
    options = facade.word_question_attribute_options(changed["key"], changed["revision"])
    return facade, source, provider, changed, options, store, old


def _save(facade, item, options, updates, **kwargs):
    return facade.word_question_save_attributes(
        item["key"], item["revision"], updates,
        expected_attribute_revision=options["attributes"]["revision"],
        expected_stored_revision=options["stored_revision"], **kwargs,
    )


@pytest.mark.parametrize("teacher", [False, True])
def test_reconfirm_unchanged_new_labels_preserves_history_unknowns_and_source(
    desktop_paths, tmp_path, teacher,
):
    facade, source, provider, item, options, store, old = _changed(
        desktop_paths, tmp_path, teacher=teacher,
    )
    original = source.read_bytes()
    history = store.history(item["key"])
    assert options["range_review_required"] and options["previous_attributes"] == old
    saved = _save(facade, item, options, {}, reconfirm_range=True)
    for field, value in options["attributes"].items():
        if field not in {"revision", "edit_version", "annotation_source"}:
            assert saved[field] == value
    assert saved["teacher_note"] == ""
    assert saved["annotation_source"] == "teacher_modified"
    assert saved["question_revision"] == item["revision"]
    assert store.history(item["key"])[:len(history)] == history
    assert len(store.history(item["key"])) == len(history) + 2
    current = facade.word_question_catalog()["items"][0]
    assert not current.get("attribute_stale") and current["attributes"] == saved
    reopened = facade.word_question_attribute_options(item["key"], item["revision"])
    assert not reopened["range_review_required"] and reopened["previous_attributes"] is None
    with pytest.raises(WordQuestionError, match="新版本"):
        _save(facade, item, options, {}, reconfirm_range=True)
    assert source.read_bytes() == original and provider.borrow_calls == 0


@pytest.mark.parametrize("updates,flag", [({}, False), ({}, "true"), (None, True),
                                        ({"teacher_note": 123}, True)])
def test_no_accidental_or_invalid_empty_rebinding(desktop_paths, tmp_path, updates, flag):
    facade, _, _, item, options, store, old = _changed(desktop_paths, tmp_path)
    history = store.history(item["key"])
    with pytest.raises(WordQuestionError):
        _save(facade, item, options, updates, reconfirm_range=flag)
    assert store.get(item["key"]) == old and store.history(item["key"]) == history


def test_current_or_unsaved_labels_cannot_use_range_confirmation(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    store = facade._word_questions().attribute_store
    for save_initial in (False, True):
        options = facade.word_question_attribute_options(item["key"], item["revision"])
        if save_initial:
            store.save_many([options["attributes"]])
            options = facade.word_question_attribute_options(item["key"], item["revision"])
        before = store.history(item["key"])
        with pytest.raises(WordQuestionError, match="已变化"):
            _save(facade, item, options, {}, reconfirm_range=True)
        assert store.history(item["key"]) == before


def test_reconfirm_rejects_intervening_teacher_edit(desktop_paths, tmp_path):
    facade, _, _, item, options, store, old = _changed(desktop_paths, tmp_path)
    newer = store.save_teacher_edit(item["key"], {"teacher_note": "其他窗口修改"},
                                    expected_revision=old["revision"])
    before = store.history(item["key"])
    with pytest.raises(WordQuestionError, match="新版本"):
        _save(facade, item, options, {}, reconfirm_range=True)
    assert store.get(item["key"]) == newer and store.history(item["key"]) == before


def test_reconfirm_rechecks_archived_source_before_write(desktop_paths, tmp_path):
    facade, _, _, item, options, store, old = _changed(desktop_paths, tmp_path)
    archive = desktop_paths.state_root / "visual-import-v2" / "sources" / f"{item['source_sha256']}.docx"
    archive.write_bytes(b"changed synthetic source")
    with pytest.raises(WordQuestionError, match="变化"):
        _save(facade, item, options, {}, reconfirm_range=True)
    assert store.get(item["key"]) == old


@pytest.mark.parametrize("width", [360, 720])
def test_real_editor_requires_review_then_allows_empty_proposal(
    qt_app, desktop_paths, tmp_path, width,
):
    _, _, _, item, options, store, old = _changed(desktop_paths, tmp_path, teacher=True)
    before = deepcopy(options)
    editor = WordQuestionAttributesDialog(item, options)
    editor.resize(width, 650)
    editor.show()
    qt_app.processEvents()
    assert editor.width() == width and editor.body_scroll.horizontalScrollBar().maximum() == 0
    editor._preview()
    assert editor.pages.currentIndex() == 0 and editor._proposal is None
    editor.range_review_check.setChecked(True)
    editor._preview()
    assert editor.pages.currentIndex() == 1 and editor._proposal == {}
    assert "旧范围用途" in editor.comparison.toPlainText()
    assert "本次将标签绑定" in editor.comparison.toPlainText()
    editor._confirm()
    assert editor.updates == {} and editor.reconfirm_range
    assert options == before and store.get(item["key"]) == old
    editor.deleteLater()


def test_review_cancel_and_back_do_not_commit(qt_app, desktop_paths, tmp_path):
    _, _, _, item, options, store, old = _changed(desktop_paths, tmp_path)
    editor = WordQuestionAttributesDialog(item, options)
    editor.range_review_check.setChecked(True)
    editor._preview()
    editor._back()
    editor._confirm()
    assert editor.updates is None and not editor.reconfirm_range
    editor._preview()
    editor.range_review_check.setChecked(False)
    editor._confirm()
    assert editor.pages.currentIndex() == 0 and editor.updates is None
    editor.reject()
    assert editor.updates is None and not editor.reconfirm_range
    assert store.get(item["key"]) == old


def test_old_comparison_must_match_stored_identity(qt_app, desktop_paths, tmp_path):
    _, _, _, item, options, _, _ = _changed(desktop_paths, tmp_path)
    options["stored_revision"] = "f" * 64
    with pytest.raises(WordQuestionAttributeError, match="旧标签"):
        WordQuestionAttributesDialog(item, options)


def test_actual_question_editor_commits_empty_review_and_clears_stale_markers(
    qt_app, desktop_paths, tmp_path, monkeypatch,
):
    facade, _, _, item, _, store, _ = _changed(desktop_paths, tmp_path)
    tasks = _Tasks()
    dialog = WordQuestionDialog(facade, tasks)
    tasks.flush()
    before = len(store.history(item["key"]))

    def accept(editor):
        editor.range_review_check.setChecked(True)
        editor._preview()
        editor._confirm()
        return editor.result()

    monkeypatch.setattr(WordQuestionAttributesDialog, "exec", accept)
    assert dialog._current_key == item["key"]
    assert dialog._items[item["key"]]["attribute_stale"]
    dialog._edit_attributes()
    tasks.flush()
    assert len(store.history(item["key"])) == before + 2
    assert not dialog._items[item["key"]].get("attribute_stale")
    assert dialog._items[item["key"]]["attributes"]["question_revision"] == item["revision"]
    assert "已保存在本机" in dialog.status.text()
    dialog.reject()
