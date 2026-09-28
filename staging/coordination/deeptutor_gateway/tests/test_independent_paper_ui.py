"""Actual styled Qt controls with the real scoped draft service, temp state only."""
from copy import deepcopy
import os
from threading import Event

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from test_word_question_dialog import _isolated_qt_app
from test_desktop_mixed_paper_service import setup, _add_word, _core_item
from runtime.deeptutor_shchem.word_import_identity_qa import QueuedTasks
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import settle, reachable_controls
from runtime.deeptutor_shchem.independent_paper_qa import geometry
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.scan_paper_page import ScanPaperPage


@pytest.fixture
def qt_app():
    with _isolated_qt_app() as app:
        create_application(["independent-paper-ui"])
        yield app


@pytest.fixture
def workspace(setup, qt_app):
    service, store, words, _, _ = setup
    facade = service.facade
    facade._reader_stop_event = Event()
    facade.basket = store.basket
    facade.independent_paper_library = lambda: IndependentPaperLibrary(facade)
    store.add_to_basket(_core_item("master"))
    _add_word(service, words)
    tasks = QueuedTasks()
    page = ScanPaperPage(facade, tasks)
    page.resize(360, 520)
    page.show()
    settle(qt_app)
    yield page._composer, page, service, store, words, tasks
    page.close()
    page._composer.close()
    tasks.flush()


def create(workspace):
    shell, _, _, _, _, tasks = workspace
    shell.new_button.click()
    tasks.flush()
    panel = shell._mixed_panel
    assert panel and not panel._restore_failed
    return panel


def test_entry_is_read_only_until_explicit_new_and_reopens_from_selected_saved_paper(workspace):
    shell, page, _, store, _, tasks = workspace
    assert shell._mixed_panel is None and not shell.open_button.isEnabled()
    assert store.snapshot()["drafts"] == {}
    panel = create(workspace)
    first = panel.facade.paper_id
    panel.sections.setCurrentRow(1)
    panel.points.setValue(8)
    panel.up_button.click()
    panel.title.setText("已保存的合成卷")
    panel.save_button.click()
    assert "已保存的合成卷 · 2 段" in shell.saved_papers.currentText()
    payload = deepcopy(panel._draft_session.current.record["payload"])
    store.clear_basket()
    store.add_to_basket({"key": "unrelated-new-item", "title_zh": "新题"})
    page.update_basket_count()
    assert panel.model.order == payload["order"]
    shell.refresh(selected=first)
    shell.open_button.click()
    tasks.flush()
    panel = shell._mixed_panel
    assert panel.facade.paper_id == first and panel.title.text() == "已保存的合成卷"
    assert panel.model.draft()["order"] == payload["order"]
    assert panel.model.settings == payload["settings"]
    assert [row["key"] for row in store.basket()] == ["unrelated-new-item"]
    panel.details_button.click()
    tasks.flush()
    assert panel._details_dialog.details["section_count"] == 2
    panel._details_dialog.close()


def test_second_new_does_not_overwrite_first_and_empty_basket_can_continue(workspace):
    shell, page, _, store, _, tasks = workspace
    panel = create(workspace)
    first = panel.facade.paper_id
    panel.title.setText("第一份合成卷")
    old = store.draft_snapshot(first + ":" + DRAFT_ID)
    panel = create(workspace)
    assert panel.facade.paper_id != first
    panel.title.setText("第二份合成卷")
    assert store.draft_snapshot(first + ":" + DRAFT_ID) == old
    store.clear_basket()
    page.update_basket_count()
    shell.refresh(selected=first)
    assert not shell.new_button.isEnabled() and shell.open_button.isEnabled()
    shell.open_button.click()
    tasks.flush()
    assert shell._mixed_panel.title.text() == "第一份合成卷"
    assert len(shell._mixed_panel.model.order) == 2


def test_saved_selector_tracks_name_remove_and_undo_without_reloading_current_editor(workspace):
    shell, _, _, _, _, tasks = workspace
    panel = create(workspace)
    panel.sections.setCurrentRow(1)
    selected = panel._current_key()
    panel.title.setText("即时保存题名")
    panel.save_button.click()
    assert "即时保存题名 · 2 段" in shell.saved_papers.currentText()
    assert panel._current_key() == selected and not tasks.pending
    panel.remove_button.click()
    assert "即时保存题名 · 1 段" in shell.saved_papers.currentText()
    panel.undo_button.click()
    assert "即时保存题名 · 2 段" in shell.saved_papers.currentText()
    assert shell._mixed_panel is panel and panel._current_key() == selected and not tasks.pending


def test_source_dependency_error_clears_stale_counts_and_preview_actions(workspace):
    shell, _, _, store, words, tasks = workspace
    panel = create(workspace)
    panel.details_button.click()
    tasks.flush()
    assert panel._details_dialog.details is not None
    words.row["attributes"] = {"revision": "new-labels"}
    frozen = store.path.read_bytes()
    panel.reload_button.click()
    tasks.flush()
    assert panel._restore_failed and panel._details_dialog.details is None
    assert not panel.preview_button.isEnabled() and not panel.export_button.isEnabled()
    assert "暂不统计" in panel.summary.text()
    assert store.path.read_bytes() == frozen
    panel._details_dialog.close()


def test_incompatible_saved_paper_never_offers_legacy_restart_and_new_retains_original(workspace):
    shell, _, _, store, _, tasks = workspace
    panel = create(workspace)
    key = panel.facade.paper_id + ":" + DRAFT_ID
    record = store.draft_snapshot(key).record
    record["payload"]["schema_version"] = "future-version"
    store.save_draft(key, record)
    frozen = store.path.read_bytes()
    panel.reload_button.click()
    tasks.flush()
    assert panel._restore_failed and not panel.restart_button.isVisible()
    assert not panel.restart_button.isEnabled() and "顶部题篮新建" in panel.status.text()
    panel._restart()
    assert not tasks.pending and store.path.read_bytes() == frozen
    create(workspace)
    assert store.draft_snapshot(key).record == record


def test_image_number_entry_stops_if_current_draft_save_conflicts(workspace):
    _, _, _, store, _, tasks = workspace
    panel = create(workspace)
    key = panel.facade.paper_id + ":" + DRAFT_ID
    record = store.draft_snapshot(key).record
    record["payload"]["settings_ui"]["title"] = "另一窗口"
    store.save_draft(key, record)
    frozen = store.path.read_bytes()
    panel.number_button.click()
    assert panel._restore_failed and not tasks.pending and store.path.read_bytes() == frozen


@pytest.mark.parametrize("width", [360, 420])
@pytest.mark.parametrize("state", ["ready", "empty", "error", "reopen"])
def test_narrow_fixed_controls_status_and_body_actions_are_reachable(workspace, qt_app, width, state):
    shell, page, _, store, _, tasks = workspace
    panel = create(workspace)
    page.resize(width, 520)
    if state == "empty":
        while panel.model.order: panel.remove_button.click()
    elif state == "error":
        other = DesktopStateStore(store.root)
        key = panel.facade.paper_id + ":" + DRAFT_ID
        record = other.draft_snapshot(key).record
        record["payload"]["settings_ui"]["title"] = "并发编辑"
        other.save_draft(key, record)
        panel.save_button.click()
        assert panel._restore_failed
    elif state == "reopen":
        store.clear_basket()
        shell.refresh(selected=panel.facade.paper_id)
        shell.open_button.click()
        tasks.flush()
        panel = shell._mixed_panel
    settle(qt_app)
    assert (page.width(), page.height()) == (width, 520)
    geometry(shell, page)
    reachable_controls(panel, qt_app)
