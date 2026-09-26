"""Native semantic-tag dialog contracts for explicit automatic recheck mode."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox
from test_word_question_dialog import _isolated_qt_app
from test_word_semantic_tags_dialog import Facade as BaseFacade
from test_word_semantic_tags_dialog import Tasks as BaseTasks

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_semantic_tags_dialog import (
    WordSemanticTagsDialog,
)


class ModeFacade(BaseFacade):
    def __init__(self):
        super().__init__()
        self.preview_modes = []

    def word_semantic_tag_preview(self, selections, profile_id, revision, **kwargs):
        self.preview_modes.append(dict(kwargs))
        mode = kwargs.get("mode", "missing_only")
        value = super().word_semantic_tag_preview(selections, profile_id, revision)
        value["mode"] = mode
        for unit in value["units"]:
            unit["mode"] = mode
        return value


class Tasks(BaseTasks):
    def submit_progress(self, label, operation, *, on_success, on_failure, on_progress):
        return self.submit(
            label,
            lambda: operation(on_progress, lambda: bool(self.cancelled)),
            on_success=on_success,
            on_failure=on_failure,
        )


@pytest.fixture
def qt_app():
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        install_font_fallbacks()
        yield app


def _loaded(mode=False):
    facade, tasks = ModeFacade(), Tasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [{"key": "Q0", "revision": "r1"}])
    tasks.flush()
    if mode:
        dialog.recheck.setChecked(True)
    dialog._prepare()
    tasks.flush()
    return dialog, facade, tasks


def _close(dialog):
    # The shared Qt fixture calls reject() once more during cleanup. Clear the
    # synthetic result first so that cleanup never opens an unmocked discard
    # confirmation dialog after this test has already asserted its behavior.
    dialog.analysis_result = None
    dialog.done(QDialog.DialogCode.Accepted)


def test_missing_mode_keeps_old_preview_call_and_checked_result_behavior(qt_app):
    dialog, facade, tasks = _loaded()
    assert facade.preview_modes == [{}]
    assert dialog.plan["mode"] == "missing_only"
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.flush()
    changed = [
        dialog.questions.item(i)
        for i in range(dialog.questions.count())
        if dialog.questions.item(i).checkState() == Qt.CheckState.Checked
    ]
    assert changed
    assert dialog.recheck.isEnabled() is False
    _close(dialog)


def test_checking_recheck_invalidates_old_preview_and_consent(qt_app):
    facade, tasks = ModeFacade(), Tasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [{"key": "Q0", "revision": "r1"}])
    tasks.flush()
    dialog._prepare()
    tasks.flush()
    dialog.allow_send.setChecked(True)
    assert dialog.plan is not None and dialog.allow_send.isChecked()

    dialog.recheck.setChecked(True)
    assert dialog.plan is None
    assert dialog.analysis_result is None
    assert not dialog.allow_send.isChecked()
    assert not dialog.questions.count()
    assert facade.preview_modes == [{}]
    _close(dialog)


def test_recheck_preview_passes_explicit_mode_and_result_defaults_unchecked(
    qt_app,
):
    dialog, facade, tasks = _loaded(mode=True)
    assert facade.preview_modes == [{"mode": "recheck_automatic"}]
    assert dialog.plan["mode"] == "recheck_automatic"
    assert all(unit["mode"] == "recheck_automatic" for unit in dialog.plan["units"])
    dialog.allow_send.setChecked(True)
    dialog._run()
    assert dialog.recheck.isEnabled() is False
    tasks.flush()
    assert dialog.analysis_result is not None
    assert dialog.recheck.isEnabled() is False
    assert "修改对照" in dialog.tags.toPlainText()
    assert "本次修改" in dialog.tags.toPlainText()
    assert "仅核对自动标签" in dialog.tags.toPlainText()
    checkable = [
        dialog.questions.item(i)
        for i in range(dialog.questions.count())
        if dialog.questions.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable
    ]
    assert checkable
    assert all(item.checkState() == Qt.CheckState.Unchecked for item in checkable)
    assert not dialog.apply_button.isEnabled()
    _close(dialog)


def test_recheck_apply_requires_explicit_yes_and_defaults_to_no(qt_app, monkeypatch):
    dialog, facade, tasks = _loaded(mode=True)
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.flush()
    item = dialog.questions.item(0)
    item.setCheckState(Qt.CheckState.Checked)
    assert dialog.apply_button.isEnabled()
    seen = {}

    def ask(*args):
        seen["default"] = args[-1]
        seen["buttons"] = args[-2]
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", ask)
    dialog._apply()
    assert seen["default"] == QMessageBox.StandardButton.No
    assert not tasks.pending
    assert not any(call[0] == "apply" for call in facade.calls)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_: QMessageBox.StandardButton.Yes,
    )
    dialog._apply()
    tasks.flush()
    assert any(call[0] == "apply" for call in facade.calls)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_recheck_mode_is_disabled_during_analysis_and_after_result(qt_app):
    facade, tasks = ModeFacade(), Tasks()
    dialog = WordSemanticTagsDialog(facade, tasks, [{"key": "Q0", "revision": "r1"}])
    tasks.flush()
    dialog.recheck.setChecked(True)
    dialog._prepare()
    tasks.flush()
    dialog.allow_send.setChecked(True)
    dialog._run()
    assert dialog._job is not None
    assert not dialog.recheck.isEnabled()
    tasks.flush()
    assert dialog.analysis_result is not None
    assert not dialog.recheck.isEnabled()
    _close(dialog)


@pytest.mark.parametrize("width", [420, 900])
def test_recheck_dialog_comparison_and_protection_text_fit_width(qt_app, width):
    dialog, _facade, tasks = _loaded(mode=True)
    dialog.allow_send.setChecked(True)
    dialog._run()
    tasks.flush()
    dialog.resize(width, 840)
    dialog.show()
    qt_app.processEvents()
    assert dialog.width() == width
    assert "修改对照" in dialog.tags.toPlainText()
    assert "教师修改、教师确认、固定修订" in dialog.introduction.text()
    assert dialog.tabs.widget(0).horizontalScrollBar().maximum() == 0
    assert dialog.tabs.widget(1).horizontalScrollBar().maximum() == 0
    for control in (
        dialog.recheck,
        dialog.prepare,
        dialog.run_button,
        dialog.apply_button,
        dialog.close_button,
    ):
        point = control.mapTo(dialog, control.rect().topLeft())
        assert point.x() >= 0 and point.x() + control.width() <= dialog.width()
    _close(dialog)
