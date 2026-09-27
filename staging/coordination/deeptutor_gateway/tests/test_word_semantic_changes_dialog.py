"""Teacher-visible tag changes, selection receipts and compact native layouts."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from test_word_question_dialog import _isolated_qt_app

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import WORKBENCH_STYLE, install_font_fallbacks
from integrations.deeptutor_shchem_v1.desktop_workbench.word_semantic_tags_dialog import WordSemanticTagsDialog
from runtime.deeptutor_shchem.word_semantic_changes_qa import SyntheticFacade, SyntheticTasks, loaded_dialog


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        install_font_fallbacks()
        yield app


def analyzed(*, recheck=True):
    dialog, facade, tasks = loaded_dialog(recheck=recheck)
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.finish()
    return dialog, facade, tasks


def test_readable_old_new_changes_distinguish_kept_added_removed_and_bound_evidence(qt_app):
    dialog, _facade, _tasks = analyzed()
    text = dialog.tags.toPlainText()
    assert "主考点【替换】：氧化还原反应 → 电化学" in text
    assert "教材映射【替换】：电解质与电离、原电池 → 电解质与电离、电解池" in text
    assert "保留：电解质与电离" in text and "补充：电解池" in text and "移除：原电池" in text
    assert "原文区块2" in text and "原创合成题面中的电解示意图" in text
    assert "本次第1题" in text and "原创合成标签对照样例.docx" in text
    assert "synthetic-volume" not in text and "synthetic-chapter" not in text
    assert "选项陈述正确" in text and "未作教师审核" in text
    dialog.reject()


def test_missing_mode_shows_fill_comparison_and_never_offers_existing_value_replacement(qt_app):
    dialog, _facade, _tasks = analyzed(recheck=False)
    assert not (dialog.questions.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert "建议不可采用" in dialog.tags.toPlainText()
    dialog.questions.setCurrentRow(1)
    text = dialog.tags.toPlainText()
    assert "主考点【补充】：主考点待确认 → 电化学" in text
    assert "教材映射【补充】：待映射 → 电解质与电离、电解池" in text
    assert "已有非空字段保持原值" in text
    assert dialog.questions.item(1).checkState() == Qt.CheckState.Checked
    assert "本次第2题 · 原创合成" in dialog.selection_summary.text()
    assert "本次第1题 · 原创合成" not in dialog.selection_summary.text()
    dialog.reject()


def test_selection_summary_and_apply_include_only_checked_changed_eligible_units(qt_app):
    dialog, facade, tasks = analyzed()
    assert not dialog.apply_button.isEnabled()
    dialog.questions.item(1).setCheckState(Qt.CheckState.Checked)
    summary = dialog.selection_summary.text()
    assert "已勾选1题" in summary and "主考点【补充】" in summary
    assert "本次第2题 · 原创合成" in summary and "本次第1题 · 原创合成" not in summary
    assert "移除：原电池" not in summary
    for index in (2, 3, 4, 5):
        assert not (dialog.questions.item(index).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        dialog.questions.item(index).setCheckState(Qt.CheckState.Checked)
    assert dialog.selection_summary.text() == summary
    dialog._apply()
    assert not dialog.questions.isEnabled() and not dialog.close_button.isEnabled()
    tasks.finish()
    assert facade.calls[-2] == ("apply", "synthetic-comparison", ["synthetic-q1"])
    assert dialog.saved == [{"key": "synthetic-q1"}]
    assert "已保存1题" in dialog.status.text()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_protected_failed_and_unchanged_details_never_claim_saved(qt_app):
    dialog, _facade, _tasks = analyzed()
    assert dialog.status.objectName() == "StatusAttention"
    assert "失败1题" in dialog.status.text() and "尚未分析1题" in dialog.status.text()
    for index, expected in ((2, "主考点【保留】"), (3, "本题不保存建议"), (4, "本题未写入")):
        dialog.questions.setCurrentRow(index)
        assert expected in dialog.tags.toPlainText()
        assert "已保存建议" not in dialog.tags.toPlainText()
        assert not dialog.apply_button.isEnabled()
    dialog.reject()


@pytest.mark.parametrize("field", ["source_sha256", "question_revision", "source_revision"])
def test_changed_source_or_range_cannot_be_selected_or_previewed_as_usable(qt_app, field):
    dialog, _facade, _tasks = analyzed()
    value = deepcopy(dialog.analysis_result)
    value["items"][0]["proposed"][field] = "different"
    dialog._result_ready(value)
    dialog._actions()
    assert "建议不可采用" in dialog.tags.toPlainText()
    assert "→ 电化学" not in dialog.tags.toPlainText()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_other_plan_result_is_rejected(qt_app):
    dialog, facade, _tasks = loaded_dialog()
    value = facade.completed_result()
    value["plan_id"] = "other-preview"
    dialog._result_ready(value)
    assert dialog.analysis_result is None and "不一致" in dialog.status.text()
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_unfinished_result_never_claims_completion_or_allows_save(qt_app):
    dialog, facade, _tasks = loaded_dialog()
    value = facade.completed_result()
    value["finished"] = False
    dialog._result_ready(value)
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    assert "尚未完成" in dialog.status.text()
    assert dialog.status.objectName() == "StatusError"
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_model_text_remains_plain_text(qt_app):
    dialog, _facade, _tasks = analyzed()
    value = deepcopy(dialog.analysis_result)
    value["items"][0]["note"] = '<img src="https://example.invalid/pixel"> <b>不是界面标记</b>'
    dialog._result_ready(value)
    assert '<img src="https://example.invalid/pixel">' in dialog.tags.toPlainText()
    assert dialog.status.textFormat() == Qt.TextFormat.PlainText
    assert dialog.selection_summary.textFormat() == Qt.TextFormat.PlainText
    dialog.reject()


@pytest.mark.parametrize("reply", [[], [{"key": "unselected"}], [{"key": "synthetic-q0"}, {"key": "synthetic-q0"}]])
def test_empty_or_inconsistent_save_receipt_does_not_claim_success(qt_app, reply):
    dialog, facade, tasks = analyzed()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    facade.save_response = reply
    dialog._apply()
    tasks.finish()
    assert dialog.status.objectName() != "StatusSuccess"
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.saved == []
    assert dialog.questions.item(1).checkState() == Qt.CheckState.Unchecked
    dialog.reject()


def test_partial_save_keeps_unselected_items_out_and_confirms_only_returned_keys(qt_app):
    dialog, facade, tasks = analyzed()
    for index in (0, 1):
        dialog.questions.item(index).setCheckState(Qt.CheckState.Checked)
    facade.save_response = [{"key": "synthetic-q0"}]
    dialog._apply()
    tasks.finish()
    assert dialog.saved == [{"key": "synthetic-q0"}]
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.status.objectName() == "StatusAttention"
    assert "仅收到1题" in dialog.status.text()
    assert "本次第1题 · 原创合成" not in dialog.selection_summary.text()
    assert "本次第2题 · 原创合成" in dialog.selection_summary.text()
    dialog.reject()


def test_apply_error_leaves_suggestions_reviewable_without_success(qt_app):
    dialog, facade, tasks = analyzed()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    facade.fail_apply = True
    dialog._apply()
    tasks.finish()
    assert dialog.status.objectName() == "StatusError"
    assert dialog.questions.isEnabled() and dialog.close_button.isEnabled()
    assert dialog.saved == [] and dialog.apply_button.isEnabled()
    dialog.reject()


def test_cancel_awaits_finished_recovers_partial_and_ignores_late_progress(qt_app):
    dialog, facade, tasks = loaded_dialog()
    dialog.allow_send.setChecked(True)
    dialog._run()
    pending = tasks.jobs[0]
    dialog._stop()
    assert "正在停止" in dialog.status.text() and not dialog.stop.isEnabled()
    assert not dialog.close_button.isEnabled()
    pending["progress"]({"message_zh": "迟到的进度，不应覆盖停止状态"})
    assert "正在停止" in dialog.status.text()
    facade.analysis = facade.completed_result()
    facade.analysis["items"] = facade.analysis["items"][:1]
    tasks.finish(invoke=False)
    assert "分析已停止" in dialog.status.text() and "可采用变更1题" in dialog.status.text()
    assert dialog.close_button.isEnabled() and not dialog.apply_button.isEnabled()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog.apply_button.isEnabled()
    status = dialog.status.text()
    pending["progress"]({"message_zh": "已结束任务的迟到进度"})
    assert dialog.status.text() == status
    dialog.reject()


def test_result_cache_error_is_not_a_completion_or_automatic_retry(qt_app):
    dialog, facade, tasks = loaded_dialog()
    dialog.allow_send.setChecked(True)
    dialog._run()
    facade.fail_result = True
    dialog._stop()
    tasks.finish(invoke=False)
    assert dialog.status.objectName() == "StatusError"
    assert not dialog.run_button.isEnabled() and not dialog.apply_button.isEnabled()
    assert dialog.close_button.isEnabled()
    dialog.reject()


def test_late_callback_after_dialog_close_is_ignored(qt_app):
    facade, tasks = SyntheticFacade(), SyntheticTasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [])
    callback = tasks.jobs[0]
    tasks.finish()
    count, status = dialog.profile.count(), dialog.status.text()
    dialog.reject()
    callback["success"]([])
    callback["failure"]("不应写入已关闭窗口")
    assert dialog.profile.count() == count and dialog.status.text() == status


def test_selection_order_identity_is_explicit_and_consistent_on_all_four_surfaces(qt_app):
    dialog, _facade, _tasks = analyzed()
    # Numbers in the filename and keys are not verified original printed numbers.
    for unit in dialog.plan["units"]:
        unit["source_name"] = "2025-原创合成来源-第99题.docx"
    dialog._populate()
    for index in (0, 1):
        dialog.questions.setCurrentRow(index)
        dialog.questions.item(index).setCheckState(Qt.CheckState.Checked)
        identity = f"本次第{index + 1}题 · 2025-原创合成来源-第99题.docx"
        assert dialog.questions.item(index).text().startswith(identity + " · ")
        assert dialog.paper_layout.itemAt(0).widget().text() == identity
        assert identity in dialog.tags.toPlainText()
        assert identity in dialog.selection_summary.text()
        assert "本次第99题" not in dialog.tags.toPlainText()
    dialog.reject()


@pytest.mark.parametrize("width,height", [(360, 520), (420, 580), (900, 820)])
def test_compact_result_and_all_actions_scroll_and_fit(qt_app, width, height):
    dialog, _facade, _tasks = analyzed()
    dialog.questions.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.resize(width, height)
    dialog.show()
    qt_app.processEvents()
    assert (dialog.width(), dialog.height()) == (width, height)
    assert dialog.body_scroll.verticalScrollBar().maximum() > 0
    for widget in (dialog.profile, dialog.recheck, dialog.allow_send, dialog.tabs, dialog.selection_summary):
        dialog.body_scroll.ensureWidgetVisible(widget)
        qt_app.processEvents()
        assert not widget.visibleRegion().isEmpty()
    for button in (dialog.run_button, dialog.stop, dialog.apply_button, dialog.close_button):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
        assert not button.visibleRegion().isEmpty()
        assert button.width() >= button.minimumSizeHint().width()
    for checkbox in (dialog.recheck, dialog.allow_send):
        assert checkbox.width() >= checkbox.minimumSizeHint().width()
    assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.tags.horizontalScrollBar().maximum() == 0
    dialog.reject()
