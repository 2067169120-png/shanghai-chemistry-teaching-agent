"""Usable identity decisions at small sizes and actual original-source routing."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog
from test_desktop_visual_import_ui import qt_app as qt_app, _settle
from test_desktop_visual_import_facade import desktop_paths as desktop_paths
from test_desktop_import_preview import _stored
from test_word_import_identity import setup, preview, teacher_label, catalog as catalog
from runtime.deeptutor_shchem.word_import_identity_qa import PreviewFixture, QueuedTasks
from integrations.deeptutor_shchem_v1.desktop_workbench.import_preview_dialog import ImportPreviewDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog


@pytest.mark.parametrize("mode", ["existing", "version", "mixed", "context"])
@pytest.mark.parametrize("width,height", [(360, 520), (420, 520), (900, 760)])
def test_identity_preview_can_select_scroll_and_confirm_in_short_windows(qt_app, mode, width, height):
    facade, tasks = PreviewFixture(mode), QueuedTasks()
    dialog = ImportPreviewDialog(facade, tasks, facade.preview)
    dialog.resize(width, height); dialog.show(); tasks.flush()
    dialog.select_all.click(); _settle(qt_app, 20)
    assert (dialog.width(), dialog.height()) == (width, height)
    assert dialog.confirm_button.isEnabled() == (mode != "context")
    assert "本次新保存" in dialog.action_summary.text()
    if mode == "existing":
        assert dialog.confirm_button.text() == "继续已有资料"
        assert "原范围和标签" in dialog.selection_note.text()
    if mode == "version":
        assert "不自动转移到新题" in dialog.identity_note.text()
        assert dialog.confirm_button.text() == "确认另存新版本"
    for amount in (0, dialog.scroll.verticalScrollBar().maximum()):
        dialog.scroll.verticalScrollBar().setValue(amount); _settle(qt_app)
        for button in (dialog.confirm_button, dialog.cancel_button):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
    if mode != "context":
        dialog.confirm_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.selected_source_ids == [row["source_id"] for row in facade.preview["sources"]]
    else:
        assert "关联" in dialog.status.text()
        dialog.cancel_button.click()
        assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.deleteLater()


def test_loading_and_late_result_do_not_enable_or_reopen_cancelled_dialog(qt_app):
    facade, tasks = PreviewFixture("existing"), QueuedTasks()
    dialog = ImportPreviewDialog(facade, tasks, facade.preview)
    dialog.select_all.click()
    assert not dialog.confirm_button.isEnabled()
    identifier, label, operation, success, failure = tasks.pending.pop()
    dialog.cancel_button.click()
    success(operation())
    assert dialog.result() == QDialog.DialogCode.Rejected and dialog.selected_source_ids == []
    assert dialog._source_value is None
    dialog.deleteLater()


def test_real_confirmation_routes_to_original_batch_source_and_preserves_teacher_edits(
    qt_app, desktop_paths, tmp_path, monkeypatch
):
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    label = teacher_label(facade, item)
    alias = tmp_path / "换了名称的讲义.docx"; alias.write_bytes(source.read_bytes())
    plan = preview(facade, alias)
    annotation_calls = []
    monkeypatch.setattr(facade, "annotate_imported_word_batch", lambda batch: annotation_calls.append(batch))
    tasks = QueuedTasks()
    def choose(dialog):
        tasks.flush(); dialog.select_all.click()
        assert dialog.confirm_button.text() == "继续已有资料"
        dialog.confirm_button.click()
        return dialog.result()
    monkeypatch.setattr(ImportPreviewDialog, "exec", choose)
    parent = ImportDialog(facade, tasks); parent.resize(360, 520); parent.show()
    before = _stored(desktop_paths.state_root)
    parent._open_import_preview(plan, parent._import_preview_epoch)
    tasks.flush(); _settle(qt_app, 20)
    assert parent._saved_visual_receipt.batch_id == old.batch_id
    assert parent.word_batch_combo.currentData() == old.batch_id
    assert parent._pending_word_annotation is None and not tasks.pending
    assert annotation_calls == []
    assert parent.continued_source_combo.currentData()["source_sha256"] == item["source_sha256"]
    assert _stored(desktop_paths.state_root) == before
    assert facade.word_question_catalog()["items"][0]["attributes"] == label
    called = []
    class RoutedDialog(QDialog):
        basket_changed = Signal(int)
        def __init__(self, *args, **kwargs):
            super().__init__()
            called.append((args, kwargs))
            self.reference = self.preparation_reference = None
        def exec(self):
            return QDialog.DialogCode.Rejected
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog.WordQuestionDialog", RoutedDialog)
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog.ImportWordDialog", RoutedDialog)
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_workbench.import_batch_dialog.ImportBatchDialog", RoutedDialog)
    parent.continue_questions_button.click()
    assert called[-1][1] == {"batch_id": old.batch_id, "initial_source_id": item["source_sha256"], "required_source_id": item["source_sha256"]}
    parent.continue_original_button.click()
    assert called[-1][0][1] == old.batch_id
    assert called[-1][1]["initial_source_id"] == parent.continued_source_combo.currentData()["archive_source_id"]
    parent.continue_progress_button.click()
    assert called[-1][0][2] == old.batch_id
    assert provider.borrow_calls == 0
    parent.close(); parent.deleteLater()


@pytest.mark.parametrize("missing", [False, True])
def test_continuation_source_scope_never_falls_back_or_clears_other_saved_choices(qt_app, missing):
    from test_word_question_dialog import _Facade, _Tasks
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
    facade, tasks = _Facade(saved=[{"key": "Q2", "revision": "revision-Q2", "points": 4}]), _Tasks()
    target = "MISSING-SOURCE" if missing else "A"
    original = facade.word_question_saved_selection()
    dialog = WordQuestionDialog(facade, tasks, initial_source_id=target, required_source_id=target)
    dialog.show(); tasks.flush(); _settle(qt_app)
    assert dialog.source_combo.currentData() == target
    assert dialog.source_combo.count() == 1 and not dialog.source_combo.isEnabled()
    assert [row["id"] for row in dialog.multi_filter_panel.options["source"]] == [target]
    assert not dialog.multi_filter_panel.buttons["source"].isEnabled()
    visible = [dialog.question_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.question_list.count())]
    assert all(dialog._items[key]["source_id"] == target for key in visible)
    if missing:
        assert visible == [] and "未显示其他来源" in dialog.status.text()
        assert dialog._panel_layouts[0].itemAt(0).widget().text() == "原来源当前没有可用题目，请返回查看原文或原批次进度。"
    else:
        assert visible
    dialog.multi_filter_panel.clear(); tasks.flush(); _settle(qt_app)
    assert all(dialog._items[dialog.question_list.item(i).data(Qt.ItemDataRole.UserRole)]["source_id"] == target
               for i in range(dialog.question_list.count()))
    assert facade.word_question_saved_selection() == original
    dialog.search.setText("葡萄糖")
    dialog.reload_button.click(); tasks.flush(); _settle(qt_app)
    assert dialog.source_combo.currentData() == target and dialog.question_list.count() == 0
    assert dialog.source_combo.count() == 1 and not dialog.source_combo.isEnabled()
    assert [row["id"] for row in dialog.multi_filter_panel.options["source"]] == [target]
    assert not dialog.multi_filter_panel.buttons["source"].isEnabled()
    empty_text = dialog._panel_layouts[0].itemAt(0).widget().text()
    assert empty_text == ("原来源当前没有可用题目，请返回查看原文或原批次进度。" if missing else "请调整筛选条件或搜索词。")
    assert facade.word_question_saved_selection() == original
    dialog.close(); tasks.flush(); dialog.deleteLater()


def test_same_name_versions_have_distinct_history_choices(qt_app, desktop_paths, tmp_path):
    from test_desktop_import_preview import _word_bytes
    from test_word_import_identity import commit
    facade, source, old, _ = setup(desktop_paths, tmp_path)
    source.write_bytes(_word_bytes(text="合成新版本"))
    result = commit(facade, preview(facade, source))
    parent = ImportDialog(facade, QueuedTasks())
    parent._visual_batch_saved(result)
    assert parent.word_batch_combo.count() == 2
    assert parent.word_batch_combo.itemText(0) != parent.word_batch_combo.itemText(1)
    assert parent.word_batch_combo.currentData() == result.batch_id != old.batch_id
    assert "未迁移教师修订" in parent.import_identity_summary.text()
    parent.tasks.flush(); parent.close(); parent.deleteLater()


def test_mixed_real_confirmation_saves_only_new_and_auto_labels_only_new_batch(
    qt_app, desktop_paths, tmp_path, monkeypatch
):
    from test_desktop_import_preview import _word_bytes
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    teacher = teacher_label(facade, item)
    old_history = facade._word_questions().attribute_store.history(item["key"])
    new = tmp_path / "独立新讲义.docx"; new.write_bytes(_word_bytes(text="另一个合成练习"))
    plan = preview(facade, source, new)
    tasks = QueuedTasks()
    def choose(dialog):
        tasks.flush(); dialog.select_all.click()
        assert dialog.confirm_button.text() == "确认保存与继续"
        dialog.confirm_button.click()
        return dialog.result()
    monkeypatch.setattr(ImportPreviewDialog, "exec", choose)
    parent = ImportDialog(facade, tasks)
    parent._open_import_preview(plan, parent._import_preview_epoch)
    tasks.flush()
    result = parent._saved_visual_receipt
    assert result.batch_id != old.batch_id and result.source_count == 1
    assert parent.continued_source_combo.currentData()["batch_id"] == old.batch_id
    assert parent.word_batch_combo.currentData() == result.batch_id
    assert "新保存 1 份，继续已有 1 份" in parent.import_identity_summary.text()
    current = facade.word_question_catalog()["items"]
    assert next(row for row in current if row["key"] == item["key"])["attributes"] == teacher
    assert facade._word_questions().attribute_store.history(item["key"]) == old_history
    new_row = next(row for row in current if row["key"] != item["key"])
    assert new_row["attributes"]["annotation_source"] == "auto_suggested"
    assert len(facade.list_imported_word_batches()) == 2 and provider.borrow_calls == 0
    parent.close(); parent.deleteLater()
