"""Real styled Qt and temporary draft state. No real provider or source access."""
from copy import deepcopy
import os

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from test_word_question_dialog import _isolated_qt_app
from test_desktop_mixed_paper_ui import Facade as PreviewFacade, Tasks as PreviewTasks, confirm_preview
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import (
    DRAFT_ID, SyntheticFacade, Tasks, MixedPaperPanel, PaperComposerModel,
    create_application, footer_checks, reachable_controls, settle,
)


@pytest.fixture
def qt_app():
    with _isolated_qt_app() as app:
        create_application(["draft-undo-test"])
        yield app


@pytest.fixture
def panel(qt_app, tmp_path):
    facade, tasks = SyntheticFacade(tmp_path / "state"), Tasks()
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    yield widget, facade, tasks
    widget.close()
    tasks.flush()


def saved(facade):
    return facade.state_store.draft_snapshot(DRAFT_ID).record


@pytest.mark.parametrize("edit", ["remove", "move", "word_points", "core_points", "space", "scores", "duration", "mode"])
def test_actual_editor_saves_then_undo_restores_current_paper_and_selected_identity(panel, edit):
    widget, facade, _ = panel
    before = saved(facade)
    basket = facade.state_store.basket_snapshot()
    others = facade.state_store.draft_snapshot("other-paper")
    row = 1 if edit in {"remove", "move", "word_points"} else 0
    widget.sections.setCurrentRow(row)
    selected = widget._current_key()
    if edit == "remove":
        widget.remove_button.click()
    elif edit == "move":
        widget.up_button.click()
    elif edit in {"word_points", "core_points"}:
        widget.points.setValue(6)
    elif edit == "space":
        widget.space.setValue(4)
    elif edit == "scores":
        widget.show_scores.setChecked(True)
    elif edit == "duration":
        widget.duration.setValue(80)
    else:
        widget.mode.setCurrentIndex(1 - widget.mode.currentIndex())
    assert saved(facade) != before and widget._draft_session.undo_count == 1
    assert "已保存" in widget.status.text()
    widget.undo_button.click()
    assert saved(facade) == before and widget._current_key() == selected
    assert not widget.undo_button.isEnabled() and "已撤销" in widget.status.text()
    assert facade.state_store.basket_snapshot() == basket
    assert facade.state_store.draft_snapshot("other-paper") == others


def test_removed_all_can_undo_and_restore_all_is_itself_undoable(panel):
    widget, facade, _ = panel
    initial = saved(facade)
    while widget.model.order:
        widget.remove_button.click()
    empty = saved(facade)
    assert not widget.preview_button.isEnabled() and widget.undo_button.isEnabled()
    widget.restore_button.click()
    assert widget.model.order == initial["payload"]["order"]
    widget.undo_button.click()
    assert saved(facade) == empty and not widget.model.order
    widget.undo_button.click()
    assert len(widget.model.order) == 1 and widget._current_key() == "visual-synthetic"


def test_keyboard_draft_undo_restores_saved_settings_from_focused_field(panel, qt_app):
    widget, facade, _ = panel
    widget.resize(360, 520)
    widget.show()
    widget.points.setValue(8)
    widget.scroll.ensureWidgetVisible(widget.points)
    widget.points.setFocus()
    QTest.qWait(30)
    QTest.keyClick(widget.points, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    settle(qt_app)
    assert saved(facade)["payload"]["settings"]["core-synthetic"]["score_per_atomic"] == 2
    assert not widget._draft_session.undo_count
    assert "撤销当前卷上一步" in widget.undo_button.accessibleName()


def test_noop_and_other_draft_changes_preserve_history_reopen_clears_it(panel, qt_app):
    widget, facade, tasks = panel
    initial = facade.state_store.path.read_bytes()
    widget._move(-1)
    assert facade.state_store.path.read_bytes() == initial and not widget.undo_button.isEnabled()
    widget.down_button.click()
    facade.state_store.save_draft("unrelated", {"title": "不相关"})
    widget.load()
    tasks.flush()
    assert widget.undo_button.isEnabled()
    widget.close()
    second = MixedPaperPanel(facade, tasks, PaperComposerModel())
    second.load()
    tasks.flush()
    assert second.model.order == saved(facade)["payload"]["order"]
    assert not second.undo_button.isEnabled()
    second.close()


@pytest.mark.parametrize("failure", ["external", "write", "read"])
def test_failed_or_conflicting_undo_blocks_edit_and_reread_is_real_recovery(panel, failure):
    widget, facade, tasks = panel
    store = facade.state_store
    widget.down_button.click()
    if failure == "external":
        current = saved(facade)
        current["payload"]["settings_ui"]["title"] = "另一窗口的标题"
        DesktopStateStore(store.root).save_draft(DRAFT_ID, current)
    else:
        setattr(store, failure + "_error", True)
    frozen = store.path.read_bytes()
    widget.undo_button.click()
    assert widget._restore_failed and not widget.undo_button.isEnabled()
    assert not widget.preview_button.isEnabled() and widget.reload_button.isEnabled()
    assert "未能确认" in widget.status.text() and store.path.read_bytes() == frozen
    widget._undo()
    assert store.path.read_bytes() == frozen
    store.write_error = store.read_error = False
    widget.reload_button.click()
    tasks.flush()
    assert not widget._restore_failed and not widget.undo_button.isEnabled()
    assert widget.model.order == saved(facade)["payload"]["order"]


def test_source_add_remove_refresh_rebases_saved_draft_and_invalidates_old_history(panel):
    widget, facade, tasks = panel
    widget.sections.setCurrentRow(1)
    widget.points.setValue(9)
    widget.up_button.click()
    extra = deepcopy(facade.rows[1])
    extra["key"] = "new-word"
    extra["source_ref"] = {"key": "new-word", "revision": "new-r1"}
    facade.rows.append(extra)
    facade.state_store.add_to_basket(extra)
    facade.state_store.remove_basket_item("visual-synthetic")
    widget.reload_button.click()
    tasks.flush()
    assert widget.model.order == ["word-synthetic", "core-synthetic", "new-word"]
    assert widget.model.settings["word-synthetic"] == {"points": 9}
    assert not widget.undo_button.isEnabled() and "失效" in widget.status.text()
    assert saved(facade)["payload"]["order"] == widget.model.order
    assert "visual-synthetic" not in saved(facade)["payload"]["settings"]


def make_recovery(widget, facade, tasks):
    facade.rows[0]["source_ref"]["revision"] = "new-source-r2"
    before = facade.state_store.path.read_bytes()
    widget.load()
    tasks.flush()
    assert widget._restore_failed and widget.restart_button.isEnabled()
    assert facade.state_store.path.read_bytes() == before


def test_recovery_cancel_then_confirm_archives_old_draft_and_starts_defaults(panel, monkeypatch):
    widget, facade, tasks = panel
    widget.points.setValue(9)
    original = saved(facade)
    make_recovery(widget, facade, tasks)
    before = facade.state_store.path.read_bytes()
    monkeypatch.setattr(widget, "_confirm_restart", lambda: False)
    widget.restart_button.click()
    assert not tasks.pending and facade.state_store.path.read_bytes() == before
    monkeypatch.setattr(widget, "_confirm_restart", lambda: True)
    widget.restart_button.click()
    assert widget._busy and not widget.restart_button.isEnabled() and not widget.back_button.isEnabled()
    tasks.flush()
    archives = [value for key, value in facade.state_store.snapshot()["drafts"].items() if key.startswith("paper-mixed-before-reset-")]
    assert archives == [original]
    assert saved(facade)["payload"]["settings"]["core-synthetic"]["score_per_atomic"] == 2
    assert not widget._restore_failed and not widget.undo_button.isEnabled()
    assert widget.preview_button.isEnabled() and not widget.export_button.isEnabled()


@pytest.mark.parametrize("change", ["projection", "draft", "close"])
def test_recovery_confirmation_late_result_cannot_override_changed_or_closed_editor(panel, monkeypatch, change):
    widget, facade, tasks = panel
    make_recovery(widget, facade, tasks)
    monkeypatch.setattr(widget, "_confirm_restart", lambda: True)
    widget.restart_button.click()
    if change == "projection":
        facade.rows[0]["source_ref"]["revision"] = "newer-source-r3"
    elif change == "draft":
        record = saved(facade)
        record["teacher_note"] = "newer external edit"
        DesktopStateStore(facade.state_store.root).save_draft(DRAFT_ID, record)
    else:
        widget.close()
    before = facade.state_store.path.read_bytes()
    tasks.flush()
    assert facade.state_store.path.read_bytes() == before
    assert widget._closed if change == "close" else widget._restore_failed


@pytest.mark.parametrize("width", [360, 420])
def test_real_restart_confirmation_is_cancellable_and_fits_small_width(panel, qt_app, width):
    widget, facade, tasks = panel
    widget.resize(width, 520)
    widget.show()
    make_recovery(widget, facade, tasks)
    frozen = facade.state_store.path.read_bytes()
    observed = []
    def dismiss():
        boxes = [item for item in qt_app.topLevelWidgets() if isinstance(item, QMessageBox) and item.isVisible()]
        assert len(boxes) == 1
        box = boxes[0]
        observed.append((box.width(), box.height(), box.defaultButton() == box.button(QMessageBox.StandardButton.No)))
        box.button(QMessageBox.StandardButton.No).click()
    QTimer.singleShot(30, dismiss)
    widget.restart_button.click()
    assert observed and observed[0][0] <= width and observed[0][1] <= 520 and observed[0][2]
    assert facade.state_store.path.read_bytes() == frozen


@pytest.mark.parametrize("width", [360, 420])
@pytest.mark.parametrize("state", ["ready", "empty", "error", "recovery"])
def test_short_real_styled_panel_footer_and_body_actions_remain_reachable(panel, qt_app, width, state):
    widget, facade, tasks = panel
    widget.resize(width, 520)
    widget.show()
    if state == "empty":
        while widget.model.order:
            widget.remove_button.click()
    elif state == "error":
        facade.state_store.write_error = True
        widget.remove_button.click()
    elif state == "recovery":
        make_recovery(widget, facade, tasks)
    settle(qt_app)
    assert (widget.width(), widget.height()) == (width, 520)
    assert footer_checks(widget)["status_complete"]
    reached = reachable_controls(widget, qt_app)
    assert "reload_button" in reached
    if state == "recovery":
        assert "restart_button" in reached
    if state == "ready":
        assert "preview_button" in reached and "remove_button" in reached
        assert widget._points_label.width() > 200 and widget._space_label.width() > 200
        assert widget._points_label.height() >= widget._points_label.heightForWidth(widget._points_label.width())
    image = widget.grab()
    assert image.width() == round(width * image.devicePixelRatio())


def test_undo_invalidates_confirmed_preview_and_late_approval_cannot_enable_export(qt_app):
    facade, tasks = PreviewFacade(), PreviewTasks()
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    widget.points.setValue(6)
    dialog = confirm_preview(widget, tasks)
    assert widget.export_button.isEnabled()
    widget.undo_button.click()
    assert dialog._closed and not widget.export_button.isEnabled()
    assert widget._preview is None and not widget._approved
    widget.preview_button.click()
    widget.title.setText("异步期间新标题")
    tasks.flush()
    assert widget._preview_dialog is None and not widget.export_button.isEnabled()
    widget.close()
