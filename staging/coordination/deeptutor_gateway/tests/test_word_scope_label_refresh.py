"""Current-range automatic labels keep history and reject source/teacher races."""
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_desktop_visual_import_facade import desktop_paths as desktop_paths
from test_word_questions_service import _import
from test_word_question_dialog import qt_app as qt_app, _Tasks, _cleanup
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionError
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    _seal, rebase_automatic_scope_attributes, WordQuestionAttributeError,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog, WordQuestionRangeDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_attributes_dialog import WordAutomaticScopeLabelsDialog


@pytest.fixture
def example(desktop_paths, tmp_path):
    facade, source, provider = _import(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    store = facade._word_questions().attribute_store
    old = store.save_many([facade.word_question_attribute_options(item["key"], item["revision"])["attributes"]])[0]
    updated = facade.word_question_update_range(item["key"], item["revision"],
        block_start=3, question_end=4, answer_start=5, block_end=5)
    yield facade, source, provider, old, updated
    facade.shutdown()


def preview(facade, item):
    return facade.word_question_preview_automatic_scope_labels(item["key"], item["revision"])


def save(facade, item, plan):
    return facade.word_question_save_automatic_scope_labels(
        item["key"], item["revision"], expected_plan_sha256=plan["plan_sha256"])


def test_preview_is_read_only_and_save_rebinds_only_automatic_history(example):
    facade, source, provider, old, item = example
    store = facade._word_questions().attribute_store
    original = source.read_bytes();history = store.history(item["key"])
    facade.word_question_save_selection([{"key":item["key"],"revision":item["revision"],"points":2}])
    selected = facade.word_question_saved_selection()
    plan = preview(facade, item)
    assert plan["current_range"]["block_end"] == 5 and plan["previous_range"]["block_end"] == 6
    assert plan["teacher_confirmed"] is False and plan["automatic_only"] is True
    assert store.get(item["key"]) == old and store.history(item["key"]) == history
    new = save(facade, item, plan)
    assert new["annotation_source"] == "auto_suggested" and new["teacher_note"] == ""
    assert new["question_revision"] == item["revision"] and new["edit_version"] == old["edit_version"]+1
    assert store.history(item["key"]) == history+[new]
    assert not facade.word_question_attribute_options(item["key"],item["revision"])["range_review_required"]
    assert facade.word_question_catalog()["items"][0]["attributes"] == new
    assert facade.word_question_saved_selection() == selected
    assert source.read_bytes() == original and provider.borrow_calls == 0
    with pytest.raises(WordQuestionError, match="无需"):
        save(facade, item, plan)
    assert store.history(item["key"]) == history+[new]


@pytest.mark.parametrize("protection", ["teacher_edit", "pinned", "legacy_note", "legacy_answer_confirmation"])
def test_protected_old_tags_remain_stale_and_cannot_be_rebound(example, protection):
    facade, _, _, old, item = example
    store = facade._word_questions().attribute_store
    if protection == "teacher_edit":
        protected = store.save_teacher_edit(item["key"], {"teacher_note":"原范围教师备注"}, expected_revision=old["revision"])
    else:
        row = deepcopy(old)
        if protection == "pinned":row["rule_revision"] += ".pinned"
        if protection == "legacy_note":row["teacher_note"] = "旧导入备注应保留"
        if protection == "legacy_answer_confirmation":row["answer_status"]["status"] = "teacher_confirmed"
        protected = store.save_many([_seal(row)])[0]
    history = store.history(item["key"])
    with pytest.raises(WordQuestionError, match="教师"):
        preview(facade,item)
    assert store.get(item["key"]) == protected and store.history(item["key"]) == history
    listing = facade.word_question_catalog()["items"][0]
    assert listing["attribute_stale"] and not listing["automatic_scope_refresh_allowed"]


@pytest.mark.parametrize("changed", ["source_revision", "index_revision", "extraction_revision"])
def test_new_scope_cannot_change_source_or_extraction_binding(example, changed):
    facade, _, _, old, item = example
    new = preview(facade,item)["attributes"]
    altered = _seal({**deepcopy(new),changed:"another-version"})
    with pytest.raises(WordQuestionAttributeError, match="来源版本"):
        rebase_automatic_scope_attributes(old,altered)


def test_intervening_teacher_edit_is_not_replaced_by_saved_preview(example):
    facade, _, _, old, item = example
    plan = preview(facade,item);store = facade._word_questions().attribute_store
    edited = store.save_teacher_edit(item["key"],{"teacher_note":"另一个窗口的修改"},expected_revision=old["revision"])
    history = store.history(item["key"])
    with pytest.raises(WordQuestionError, match="教师"):
        save(facade,item,plan)
    assert store.get(item["key"]) == edited and store.history(item["key"]) == history


def test_store_cas_rejects_edit_between_revalidation_and_commit(example,monkeypatch):
    facade, _, _, old, item = example
    plan = preview(facade,item);store = facade._word_questions().attribute_store
    original = store.save_many;edited = []
    def raced(rows,**kwargs):
        edited.append(store.save_teacher_edit(item["key"],{"teacher_note":"保存前另一个修改"},expected_revision=old["revision"]))
        return original(rows,**kwargs)
    monkeypatch.setattr(store,"save_many",raced)
    with pytest.raises(WordQuestionError, match="未保存"):
        save(facade,item,plan)
    assert store.get(item["key"]) == edited[0]
    assert store.history(item["key"])[-1] == edited[0]


def test_changed_source_is_rejected_before_auto_label_write(example):
    facade, _, _, old, item = example
    plan = preview(facade,item);store = facade._word_questions().attribute_store
    archive = facade.paths.state_root/'visual-import-v2/sources'/f"{item['source_sha256']}.docx"
    archive.write_bytes(b"changed archived source fixture")
    with pytest.raises(WordQuestionError):save(facade,item,plan)
    assert store.get(item["key"]) == old and len(store.history(item["key"])) == 1


def test_changed_range_or_preview_hash_does_not_save(example):
    facade, _, _, old, item = example
    plan = preview(facade,item)
    with pytest.raises(WordQuestionError,match="重新预览"):
        save(facade,item,{**plan,"plan_sha256":"0"*64})
    facade.word_question_update_range(item["key"],item["revision"],block_start=3,question_end=4,answer_start=5,block_end=6)
    with pytest.raises(WordQuestionError):save(facade,item,plan)
    assert facade._word_questions().attribute_store.get(item["key"]) == old


def test_changed_catalog_invalidates_exact_preview_even_if_labels_match(example,monkeypatch):
    facade, _, _, old, item = example
    plan = preview(facade,item)
    catalog = deepcopy(facade.word_question_attribute_options(item["key"],item["revision"])["catalog"])
    catalog.setdefault("warnings",[]).append("different catalog snapshot fixture")
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_word_questions.load_attribute_catalog",
        lambda *_args,**_kwargs:catalog)
    with pytest.raises(WordQuestionError,match="重新预览"):
        save(facade,item,plan)
    assert facade._word_questions().attribute_store.get(item["key"]) == old


@pytest.mark.parametrize("width", [420,1000])
def test_scope_preview_resizes_and_cancel_never_writes(qt_app,example,width):
    facade, _, _, old, item = example
    dialog = WordAutomaticScopeLabelsDialog(preview(facade,item))
    dialog.resize(width,760);dialog.show();qt_app.processEvents()
    assert dialog.save_button.isVisible() and dialog.save_button.width()<width
    assert "旧范围" in dialog.comparison.toPlainText() and dialog.evidence.toPlainText()
    assert dialog.current_summary.width() <= width
    QTest.mouseClick(dialog.cancel_button,Qt.MouseButton.LeftButton)
    assert dialog.save_requested is False
    assert facade._word_questions().attribute_store.get(item["key"]) == old
    dialog.deleteLater()


@pytest.mark.parametrize("accepted", [False,True])
def test_native_question_entry_preserves_auto_provenance_and_selection(qt_app,example,monkeypatch,accepted):
    facade, _, _, old, item = example
    facade.word_question_save_selection([{"key":item["key"],"revision":item["revision"],"points":2}])
    selected = facade.word_question_saved_selection();tasks = _Tasks()
    dialog = WordQuestionDialog(facade,tasks,initial_question_key=item["key"])
    dialog.show();tasks.flush();qt_app.processEvents()
    assert dialog._current_key == item["key"] and dialog.scope_labels_button.isEnabled()
    def decide(editor):
        editor._confirm() if accepted else editor.reject()
        return editor.result()
    monkeypatch.setattr(WordAutomaticScopeLabelsDialog,"exec",decide)
    dialog.scope_labels_button.click();tasks.finish("预览当前范围自动建议")
    if accepted:tasks.finish("保存当前范围自动建议")
    tasks.flush()
    saved = facade._word_questions().attribute_store.get(item["key"])
    if accepted:
        assert saved["annotation_source"] == "auto_suggested" and saved["question_revision"] == item["revision"]
        assert not dialog._items[item["key"]].get("attribute_stale")
        assert dialog.scope_labels_button.isHidden()
    else:assert saved == old and dialog._items[item["key"]]["attribute_stale"]
    assert facade.word_question_saved_selection() == selected
    _cleanup(dialog,tasks)


def test_late_scope_preview_cannot_open_or_save_for_another_question(qt_app,example,monkeypatch):
    facade, _, _, old, item = example
    tasks = _Tasks();dialog = WordQuestionDialog(facade,tasks,initial_question_key=item["key"])
    dialog.show();tasks.flush();qt_app.processEvents()
    def unexpected(_editor):raise AssertionError("late preview must not open a dialog")
    monkeypatch.setattr(WordAutomaticScopeLabelsDialog,"exec",unexpected)
    dialog.scope_labels_button.click()
    dialog.question_list.setCurrentRow(1)
    assert dialog._current_key != item["key"]
    tasks.finish("预览当前范围自动建议");tasks.flush()
    assert facade._word_questions().attribute_store.get(item["key"]) == old
    assert not dialog._attributes_busy
    _cleanup(dialog,tasks)


def test_auto_refresh_entry_appears_immediately_after_range_editor_save(qt_app,desktop_paths,tmp_path,monkeypatch):
    facade, _, _ = _import(desktop_paths,tmp_path)
    item = facade.word_question_catalog()["items"][0]
    store = facade._word_questions().attribute_store
    old = store.save_many([facade.word_question_attribute_options(item["key"],item["revision"])["attributes"]])[0]
    tasks = _Tasks();dialog = WordQuestionDialog(facade,tasks,initial_question_key=item["key"])
    dialog.show();tasks.flush();qt_app.processEvents()
    assert dialog.scope_labels_button.isHidden()
    def change_range(editor):
        editor.fields["block_end"].setValue(5)
        editor.preview_button.click();editor.save_button.click()
        return editor.result()
    monkeypatch.setattr(WordQuestionRangeDialog,"exec",change_range)
    dialog.range_button.click();tasks.finish("读取 Word 完整来源");tasks.finish("保存 Word 题目范围")
    tasks.flush()
    assert dialog._items[item["key"]]["attribute_stale"]
    assert dialog.scope_labels_button.isEnabled() and not dialog.scope_labels_button.isHidden()
    assert store.get(item["key"]) == old and len(store.history(item["key"])) == 1
    _cleanup(dialog,tasks);facade.shutdown()
