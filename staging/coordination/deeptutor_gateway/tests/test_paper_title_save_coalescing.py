"""Focused Qt title saves, using actual source-bound paper sessions on E: tmp."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QStackedWidget, QWidget

from test_persistent_paper_history import paper_history
from test_desktop_mixed_paper_service import setup
from test_word_question_dialog import _isolated_qt_app
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import Tasks, settle
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.independent_paper_workspace import IndependentPaperWorkspace
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import TeacherWorkbenchWindow, ROUTE_ORDER
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import PaperComposerModel


@pytest.fixture
def title_editor(paper_history):
    service, store, words, paper, _ = paper_history
    with _isolated_qt_app() as app:
        calls = []
        projection = paper.service.projection
        def counted():
            calls.append(True)
            return projection()
        paper.service.projection = counted
        tasks = Tasks()
        panel = MixedPaperPanel(paper, tasks, PaperComposerModel())
        panel.load(); tasks.flush()
        panel.show(); settle(app)
        calls.clear()
        yield SimpleNamespace(app=app, service=service, store=store, words=words, paper=paper,
                              panel=panel, tasks=tasks, calls=calls)
        panel.close()
        panel.deleteLater()
        settle(app)


def typed(panel, text="chemistry123"):
    panel.title.selectAll()
    QTest.keyClicks(panel.title, text)
    assert panel.title.text() == text


def current(data):
    return data.store.draft_snapshot(data.paper.paper_id + ":" + DRAFT_ID).record


def shell(data):
    facade = data.service.facade
    facade.basket = data.store.basket
    facade.independent_paper_library = lambda: IndependentPaperLibrary(facade)
    workspace = IndependentPaperWorkspace(facade, data.tasks)
    workspace._mixed_panel = data.panel
    workspace.layout().addWidget(data.panel)
    workspace.refresh(selected=data.paper.paper_id)
    return workspace


def test_twelve_keystrokes_do_not_resolve_sources_until_one_editing_finished_save(title_editor):
    data = title_editor
    before = deepcopy(current(data))
    typed(data.panel)
    assert not data.calls and data.panel._draft_session.undo_count == 0
    assert current(data) == before and "尚未保存" in data.panel.status.text()
    data.panel.title.editingFinished.emit()
    assert len(data.calls) == 1 and data.panel._draft_session.undo_count == 1
    assert not data.panel._pending_title_fields
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    data.panel.undo_button.click()
    assert current(data) == before
    data.panel.redo_button.click()
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"


def test_title_and_range_coalesce_and_reverting_text_is_a_noop(title_editor):
    data = title_editor
    before = deepcopy(current(data))
    data.panel.title.setText("One title edit")
    data.panel.subtitle.setText("One range edit")
    assert not data.calls
    assert data.panel.prepare_leave()
    assert len(data.calls) == 1 and data.panel._draft_session.undo_count == 1
    data.panel.undo_button.click()
    assert current(data) == before
    data.calls.clear()
    data.panel.title.setText("temporary")
    data.panel.title.setText(before["payload"]["settings_ui"]["title"])
    assert data.panel.prepare_leave()
    assert not data.calls and data.panel._draft_session.redo_count == 1


def test_shortcut_undo_flushes_title_as_one_step_and_redo_keeps_it(title_editor):
    from PySide6.QtCore import Qt
    data = title_editor
    before = deepcopy(current(data))
    typed(data.panel)
    data.panel.title.setFocus(); settle(data.app)
    QTest.keyClick(data.panel.title, Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert current(data) == before and data.panel._draft_session.redo_count == 1
    assert len(data.calls) == 2  # Save plus source-verified undo.
    QTest.keyClick(data.panel.title, Qt.Key.Key_Y,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"


@pytest.mark.parametrize("action", ["load", "_preview_request", "_export_request", "request", "_open_details", "_save_explicitly", "_return_to_library", "close"])
def test_every_boundary_flushes_pending_title_before_operation(title_editor, action, monkeypatch):
    data = title_editor
    navigated = []
    if action == "_return_to_library":
        monkeypatch.setattr(data.panel, "window", lambda: SimpleNamespace(navigate=navigated.append))
    typed(data.panel)
    getattr(data.panel, action)()
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    assert data.panel._draft_session.undo_count == (0 if action == "close" else 1)
    assert not data.panel._pending_title_fields
    if action == "_return_to_library": assert navigated == ["library"]
    data.tasks.pending.clear()


@pytest.mark.parametrize("action", ["load", "_preview_request", "_export_request", "_undo", "_redo", "_open_details", "_save_explicitly", "_return_to_library", "close"])
def test_failed_flush_blocks_boundary_keeps_text_and_never_retries_implicitly(title_editor, action, monkeypatch):
    data = title_editor
    before = data.store.path.read_bytes()
    navigated, attempts = [], []
    if action == "_return_to_library":
        monkeypatch.setattr(data.panel, "window", lambda: SimpleNamespace(navigate=navigated.append))
    def fail(*_args, **_kwargs):
        attempts.append(True)
        raise OSError("synthetic denied save")
    monkeypatch.setattr(data.store, "_write_unlocked", fail)
    typed(data.panel)
    getattr(data.panel, action)()
    assert data.panel.title.text() == "chemistry123" and not data.panel._closed
    assert data.panel._pending_title_fields and data.panel._pending_title_failed
    assert data.store.path.read_bytes() == before and len(attempts) == 1
    assert not navigated and not data.tasks.pending
    assert not data.panel.prepare_leave() and len(attempts) == 1
    assert "尚未保存" in data.panel.status.text()


def test_explicit_recovery_read_preserves_unsaved_text_until_deliberate_save(title_editor, monkeypatch):
    data = title_editor
    before = deepcopy(current(data))
    typed(data.panel)
    with monkeypatch.context() as context:
        context.setattr(data.store, "_write_unlocked", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic denied")))
        assert not data.panel.prepare_leave()
    data.panel.load(); data.tasks.flush()
    assert data.panel.title.text() == "chemistry123" and current(data) == before
    assert not data.panel._restore_failed and data.panel._pending_title_fields
    assert "仍未保存" in data.panel.status.text()
    data.panel.save_button.click()
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    assert data.panel._draft_session.undo_count == 1


def test_source_change_blocks_title_flush_and_preserves_original_scope(title_editor, monkeypatch):
    data = title_editor
    before = deepcopy(current(data))
    typed(data.panel)
    monkeypatch.setitem(data.words.row, "revision", "changed-source")
    assert not data.panel.prepare_leave()
    assert current(data) == before and data.panel.title.text() == "chemistry123"
    assert data.panel._restore_failed and not data.panel.preview_button.isEnabled()


def test_pending_title_is_separate_from_following_reorder_history(title_editor):
    data = title_editor
    before = deepcopy(current(data))
    typed(data.panel)
    data.panel._move(1)
    assert data.panel._draft_session.undo_count == 2
    data.panel.undo_button.click()
    assert current(data)["payload"]["order"] == before["payload"]["order"]
    assert data.panel.title.text() == "chemistry123"
    data.panel.undo_button.click()
    assert current(data) == before


def test_title_edit_invalidates_old_load_callback_before_it_can_replace_text(title_editor):
    data = title_editor
    data.panel.load()
    assert data.tasks.pending
    # Real typing is disabled during a load; a queued programmatic edit can
    # still arrive and must retire the older callback before it restores UI.
    data.panel.title.setText("new text while read pending")
    data.tasks.flush()
    assert data.panel.title.text() == "new text while read pending"
    assert data.panel._pending_title_fields and not data.panel._busy
    assert data.panel.prepare_leave()
    assert current(data)["payload"]["settings_ui"]["title"] == "new text while read pending"


def test_new_input_during_source_verification_blocks_close_until_latest_text_is_saved(title_editor):
    data = title_editor
    typed(data.panel)
    loader = data.panel._draft_session._projection_loader
    delivered = []
    def newer_input():
        projection = loader()
        if not delivered:
            delivered.append(True)
            data.panel.title.setText("new input during verification")
        return projection
    data.panel._draft_session._projection_loader = newer_input
    assert not data.panel.close()
    assert data.panel.title.text() == "new input during verification" and not data.panel._closed
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    assert data.panel._pending_title_fields and "尚未保存" in data.panel.status.text()
    assert data.panel.close()
    assert current(data)["payload"]["settings_ui"]["title"] == "new input during verification"


@pytest.mark.parametrize("late", [False, True])
def test_switch_flush_failure_keeps_old_panel_and_input(title_editor, monkeypatch, late):
    data = title_editor
    workspace = shell(data)
    if late:
        workspace._run(lambda: workspace.library.open(data.paper.paper_id))
        operation, success, _failure = data.tasks.pending.pop(0)
        candidate = operation()
    typed(data.panel)
    def fail(*_args, **_kwargs): raise OSError("synthetic denied save")
    monkeypatch.setattr(data.store, "_write_unlocked", fail)
    if late: success(candidate)
    else: workspace._run(lambda: workspace.library.open(data.paper.paper_id))
    assert workspace._mixed_panel is data.panel and not data.panel._closed
    assert not data.tasks.pending and data.panel.title.text() == "chemistry123"
    assert data.panel._pending_title_failed
    workspace.deleteLater()


def test_ready_switch_flushes_newer_edit_and_new_panel_reads_latest_saved_text(title_editor):
    data = title_editor
    workspace = shell(data)
    workspace._run(lambda: workspace.library.open(data.paper.paper_id))
    operation, success, _failure = data.tasks.pending.pop(0)
    candidate = operation()
    typed(data.panel, "newer title during open")
    success(candidate); data.tasks.flush()
    new = workspace._mixed_panel
    assert new is not data.panel and data.panel._closed
    assert new.title.text() == "newer title during open"
    assert current(data)["payload"]["settings_ui"]["title"] == new.title.text()
    workspace.close(); workspace.deleteLater()


def test_pending_title_before_redo_is_saved_and_clears_old_redo_branch(title_editor):
    data = title_editor
    data.panel.duration.setValue(80)
    data.panel.undo_button.click()
    assert data.panel._draft_session.redo_count == 1
    typed(data.panel)
    data.panel._redo()
    assert data.panel.title.text() == "chemistry123" and data.panel.duration.value() == 40
    assert data.panel._draft_session.redo_count == 0 and data.panel._draft_session.undo_count == 1


@pytest.mark.parametrize("denied", [False, True])
def test_workspace_close_flushes_or_blocks_without_destroying_pending_input(title_editor, monkeypatch, denied):
    data = title_editor
    workspace = shell(data)
    workspace.show()
    typed(data.panel)
    if denied:
        monkeypatch.setattr(data.store, "_write_unlocked", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic denied")))
    accepted = workspace.close()
    assert accepted != denied and workspace._closed != denied
    assert data.panel.title.text() == "chemistry123"
    if denied:
        assert not data.panel._closed and data.panel._pending_title_failed
    else:
        assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123" and data.panel._closed
    workspace.deleteLater()


def navigation_window(data):
    window = QMainWindow()
    window.paper_page = shell(data)
    window.stack = QStackedWidget(window)
    library = QWidget()
    window.pages = {"paper": window.paper_page, "library": library}
    for page in window.pages.values(): window.stack.addWidget(page)
    window.stack.setCurrentWidget(window.paper_page)
    window.nav_buttons = [QPushButton() for _ in ROUTE_ORDER]
    for button in window.nav_buttons: button.setCheckable(True)
    window.studio_nav_buttons = {}
    window.top_title = QLabel()
    window.library_page = SimpleNamespace(cards=[object()], _loading=False)
    window.navigate = lambda route: TeacherWorkbenchWindow.navigate(window, route)
    window._prepare_paper_navigation = lambda route: TeacherWorkbenchWindow._prepare_paper_navigation(window, route)
    log = []
    window.preparation_page = SimpleNamespace(recovery=None, _cancel_textbook_image_import=lambda: log.append("cancel"))
    window.textbook_page = SimpleNamespace(prepare_close=lambda: True, close=lambda: log.append("textbook-close"))
    window._save_window_state = lambda: log.append("window-save")
    window._textbook_context_timer = SimpleNamespace(stop=lambda: log.append("timer-stop"))
    window.facade = SimpleNamespace(stop_background_readers=lambda: log.append("readers-stop"), shutdown=lambda: log.append("shutdown"))
    window.tasks = SimpleNamespace(shutdown=lambda _timeout: log.append("tasks-stop"))
    return window, log


@pytest.mark.parametrize("operation", ["navigate", "exit"])
def test_main_window_boundary_blocks_failed_flush_and_preserves_paper_input(title_editor, monkeypatch, operation):
    data = title_editor
    window, log = navigation_window(data)
    typed(data.panel)
    monkeypatch.setattr(data.store, "_write_unlocked", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic denied")))
    if operation == "navigate": window.navigate("library")
    else:
        event = QCloseEvent()
        TeacherWorkbenchWindow.closeEvent(window, event)
        assert not event.isAccepted()
    assert window.stack.currentWidget() is window.paper_page and not log
    assert data.panel.title.text() == "chemistry123" and not data.panel._closed
    assert data.panel._pending_title_failed
    window.deleteLater()


@pytest.mark.parametrize("operation", ["navigate", "exit"])
def test_main_window_boundary_saves_before_leaving_or_shutdown(title_editor, operation):
    data = title_editor
    window, log = navigation_window(data)
    typed(data.panel)
    if operation == "navigate":
        window.navigate("library")
        assert window.stack.currentWidget() is window.pages["library"]
    else:
        event = QCloseEvent()
        TeacherWorkbenchWindow.closeEvent(window, event)
        assert event.isAccepted() and log[-1] == "shutdown"
    assert current(data)["payload"]["settings_ui"]["title"] == "chemistry123"
    assert not data.panel._pending_title_fields and data.panel._draft_session.undo_count == 1
    window.deleteLater()
