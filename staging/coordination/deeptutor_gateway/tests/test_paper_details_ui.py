"""Real styled Qt with temporary state; no provider, Office or private source."""
from copy import deepcopy
import os

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_word_question_dialog import _isolated_qt_app
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import PaperPage
from runtime.deeptutor_shchem.paper_details_qa import (
    DRAFT_ID, create_application, geometry_checks, make_panel, settle,
)


@pytest.fixture
def app():
    with _isolated_qt_app() as value:
        create_application(["paper-details-test"])
        yield value


@pytest.fixture
def panel(app, tmp_path):
    facade, tasks, window, widget = make_panel(app, tmp_path / "state")
    yield widget, facade, tasks
    widget.close()
    window.close()
    tasks.flush()
    settle(app)


def open_details(panel):
    widget, facade, tasks = panel
    before = facade.state_store.path.read_bytes()
    widget.details_button.click()
    dialog = widget._details_dialog
    assert dialog is not None and dialog.details is None
    tasks.flush()
    assert facade.state_store.path.read_bytes() == before
    assert dialog.details is not None
    return dialog


def test_open_is_read_only_has_no_preview_or_provider_calls_and_preserves_other_drafts(panel):
    widget, facade, _ = panel
    basket = facade.state_store.basket_snapshot()
    other = facade.state_store.draft_snapshot("other-paper")
    dialog = open_details(panel)
    assert dialog.details["current_score"]["known"] == 13.5
    assert not widget._preview and not widget._approved
    assert facade.state_store.basket_snapshot() == basket
    assert facade.state_store.draft_snapshot("other-paper") == other


@pytest.mark.parametrize("edit", ["remove", "move", "points", "duration", "restore"])
def test_actual_saved_edits_undo_and_reopen_show_current_draft(panel, edit):
    widget, facade, _ = panel
    if edit == "restore":
        widget.sections.setCurrentRow(1)
        widget.remove_button.click()
    before = widget.model.details(duration_minutes=widget.duration.value())
    widget.sections.setCurrentRow(1 if edit in {"remove", "move", "points"} else 0)
    if edit == "remove":
        widget.remove_button.click()
    elif edit == "move":
        widget.up_button.click()
    elif edit == "points":
        widget.points.setValue(9)
    elif edit == "restore":
        widget.restore_button.click()
    else:
        widget.duration.setValue(75)
    dialog = open_details(panel)
    assert dialog.details != before
    assert [row["key"] for row in dialog.details["rows"]] == facade.state_store.draft_snapshot(DRAFT_ID).record["payload"]["order"]
    dialog.close()
    widget.undo_button.click()
    restored = open_details(panel)
    assert restored.details == before


def test_empty_after_last_removal_is_visible_and_restore_is_available(panel):
    widget, _, _ = panel
    while widget.model.order:
        widget.remove_button.click()
    dialog = open_details(panel)
    assert dialog.details["section_count"] == 0
    assert "本卷暂时没有题目" in dialog.summary.toPlainText()
    assert not dialog.edit_button.isEnabled()
    dialog.close()
    assert widget.restore_button.isEnabled() and widget.undo_button.isEnabled()


@pytest.mark.parametrize("change", ["source_content", "source_version", "source_failure", "state_failure", "draft", "basket_aba"])
def test_change_during_verification_clears_old_statistics_and_never_writes(panel, change):
    widget, facade, tasks = panel
    dialog = open_details(panel)
    dialog.close()
    widget.details_button.click()
    dialog = widget._details_dialog
    assert dialog.details is None and not widget.details_button.isEnabled()
    store = facade.state_store
    if change == "source_content":
        facade.rows[0]["content"]["atomic_chain"].pop()
    elif change == "source_version":
        facade.rows[0]["source_ref"]["data_snapshot_id"] = "f" * 64
    elif change == "source_failure":
        facade.read_failure = True
    elif change == "state_failure":
        store.read_error = True
    elif change == "draft":
        other = DesktopStateStore(store.root)
        record = other.draft_snapshot(DRAFT_ID).record
        record["payload"]["settings_ui"]["duration_minutes"] = 77
        other.save_draft(DRAFT_ID, record)
    else:
        other = DesktopStateStore(store.root)
        existing = other.basket()
        other.clear_basket()
        other.add_many_to_basket(existing)
    before = store.path.read_bytes()
    tasks.flush()
    assert store.path.read_bytes() == before
    assert dialog.details is None and "旧统计" in dialog.summary.toPlainText()
    assert not dialog.edit_button.isEnabled() and not widget.export_button.isEnabled()
    assert widget._restore_failed
    store.read_error = False


def test_read_failure_has_working_retry_without_automatic_provider_or_save(panel):
    widget, facade, tasks = panel
    dialog = open_details(panel)
    facade.read_failure = True
    before = facade.state_store.path.read_bytes()
    dialog.refresh_button.click()
    assert dialog.details is None
    tasks.flush()
    assert dialog.details is None and facade.state_store.path.read_bytes() == before
    facade.read_failure = False
    dialog.refresh_button.click()
    tasks.flush()
    assert dialog.details is not None and not widget._restore_failed
    assert dialog.details["section_count"] == 3


@pytest.mark.parametrize("close_parent", [False, True])
def test_close_while_reading_ignores_late_callback(panel, close_parent):
    widget, facade, tasks = panel
    widget.details_button.click()
    before = facade.state_store.path.read_bytes()
    if close_parent:
        widget.close()
    else:
        widget._details_dialog.close()
    tasks.flush()
    assert widget._details_dialog is None
    assert facade.state_store.path.read_bytes() == before
    assert not widget._restore_failed


def test_newer_details_window_is_not_replaced_by_old_callback(panel):
    widget, _, tasks = panel
    widget.details_button.click()
    old = tasks.pending.pop()
    old_projection = old[0]()
    widget._details_dialog.close()
    widget.sections.setCurrentRow(1)
    widget.remove_button.click()
    widget.details_button.click()
    tasks.flush()
    dialog = widget._details_dialog
    assert dialog.details["section_count"] == 2
    old[1](old_projection)
    assert dialog.details["section_count"] == 2 and not widget._busy


def test_return_to_edit_preserves_selected_source_identity_and_is_keyboard_reachable(panel, app):
    widget, _, _ = panel
    dialog = open_details(panel)
    dialog.tabs.setCurrentIndex(1)
    dialog.selection.setCurrentIndex(1)
    dialog.edit_button.setFocus()
    QTest.keyClick(dialog.edit_button, Qt.Key.Key_Space)
    settle(app)
    assert widget._details_dialog is None and widget._current_key() == "word-synthetic"


@pytest.mark.parametrize("width,height", [(360, 520), (420, 520), (900, 650)])
def test_small_and_wide_window_has_complete_fixed_status_and_reachable_scroll(panel, app, width, height):
    dialog = open_details(panel)
    dialog.resize(width, height)
    settle(app)
    assert (dialog.width(), dialog.height()) == (width, height)
    assert geometry_checks(dialog)["fixed_actions_visible"]
    assert dialog.summary.verticalScrollBar().maximum() > 0
    dialog.summary.verticalScrollBar().setValue(dialog.summary.verticalScrollBar().maximum())
    dialog.tabs.setCurrentIndex(1)
    dialog.selection.setCurrentIndex(1)
    settle(app)
    assert dialog.item_detail.verticalScrollBar().maximum() > 0
    dialog.item_detail.verticalScrollBar().setValue(dialog.item_detail.verticalScrollBar().maximum())
    assert geometry_checks(dialog)["status_complete"]
    assert "来源字节 SHA-256" in dialog.trace.toPlainText()
    assert dialog.close_button.accessibleName() == "关闭细目表"


def test_source_strings_are_plain_text_and_cannot_create_images_or_links(panel):
    widget, facade, tasks = panel
    value = '<img src="file:///synthetic-secret"><a href="https://invalid">合成</a>'
    facade.rows[1]["title_zh"] = value
    facade.rows[1]["source_zh"] = value
    facade.rows[1]["source_ref"]["revision"] = value
    # Build a new, source-bound draft to test rendering rather than bypass CAS.
    widget.load()
    tasks.flush()
    widget._confirm_restart = lambda: True
    widget._restart()
    tasks.flush()
    dialog = open_details(panel)
    dialog.selection.setCurrentIndex(1)
    assert value in dialog.item_detail.toPlainText()
    assert '<img src="file:' not in dialog.item_detail.toHtml()
    assert 'href="https://invalid"' not in dialog.item_detail.toHtml()
    assert value in dialog.trace.toPlainText()
    assert '<img src="file:' not in dialog.trace.toHtml()


def test_legacy_single_scope_entry_opens_same_draft_without_preview_or_restoring_excluded(panel, app):
    widget, facade, tasks = panel
    widget.sections.setCurrentRow(1)
    widget.remove_button.click()
    expected = deepcopy(widget.model.order)
    widget.close()
    facade.basket = lambda: tuple(facade.state_store.basket())
    page = PaperPage(facade, tasks)
    tasks.flush()
    page._confirm_open_saved_mixed_details = lambda: True
    page.request_current_details()
    tasks.flush()
    actual = page._mixed_panel
    assert actual is not None and actual._details_dialog is not None
    assert actual.model.order == expected
    assert actual._details_dialog.details["excluded_count"] == 1
    assert not actual._preview and not actual._approved
    actual.close()
    page.close()
    settle(app)


def _edited_legacy_page():
    from test_desktop_mixed_paper_ui import Facade, Tasks
    facade, tasks = Facade(), Tasks()
    facade.rows = [{"key": key, "scope": "master", "source_identity_sha256": key,
                    "title_zh": "同名合成主题", "atomic_total": 1}
                   for key in ("core-a", "core-b", "core-c")]
    raw_projection = facade.paper_basket_projection

    def projection():
        value = raw_projection()
        for row in value["items"]:
            node = row["key"]
            row["source_ref"].update(scope="master", paper_id="P", theme_id=node)
            row["content"] = {"theme": {"id": node}, "atomic_chain": [
                {"printed_question_id": "printed-" + node, "atomic_part_id": "atomic-" + node}]}
        return value

    facade.paper_basket_projection = projection
    projection()  # Seed the fake facade's new basket before read-only assertions.
    page = PaperPage(facade, tasks)
    assert page._mixed_panel is None
    themes = list(page.model.themes)
    for theme in themes:
        theme.questions[0].key = "atomic-" + theme.source_identity_sha256
        theme.questions[0].score = 7 if theme.source_identity_sha256 == "core-b" else 3
    page.model.themes = [themes[2], themes[1]]
    page.model.duration_minutes = 73
    page.model.title = "合成旧版已编辑草稿"
    page._mark_dirty()
    return page, facade, tasks


def test_edited_legacy_full_themes_keep_order_removal_points_and_duration_in_details(app):
    page, facade, tasks = _edited_legacy_page()
    old_record = deepcopy(facade.state_store.snapshot()["drafts"].get("paper-current"))
    page.current_details_button.click()
    tasks.flush()
    panel = page._mixed_panel
    assert panel.model.order == ["core-c", "core-b"]
    assert panel.model.excluded == {"core-a"}
    details = panel._details_dialog.details
    assert details["current_score"]["total"] == 10
    assert details["counts"]["atomic"]["total"] == 2
    assert details["duration_minutes"] == 73
    assert facade.state_store.snapshot()["drafts"].get("paper-current") == old_record
    panel.close()
    page.close()
    settle(app)


@pytest.mark.parametrize("change", ["partial", "reordered", "draft_race"])
def test_legacy_unrepresentable_units_or_changed_version_preserve_original_and_do_not_save_new_draft(app, change):
    page, facade, tasks = _edited_legacy_page()
    if change == "partial":
        page.model.themes[0].questions.clear()
    elif change == "reordered":
        # A caller may have reordered/changed selected atomic identities in
        # the older editor. It cannot be claimed as a full-source selection.
        page.model.themes[0].questions[0].key = "another-unit"
    before = facade.state_store.path.read_bytes()
    page.current_details_button.click()
    if change == "draft_race":
        from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import MixedPaperComposerModel
        projection = facade.paper_basket_projection()
        current = MixedPaperComposerModel()
        current.merge(projection)
        external = facade.open_mixed_paper_draft_session()
        external.read(projection)
        external.save({**current.draft(), "settings_ui": {
            "title": "另一窗口的有效新稿", "subtitle": "", "mode": "daily_practice",
            "duration_minutes": 45, "show_question_scores": False}})
        before = facade.state_store.path.read_bytes()
    tasks.flush()
    assert facade.state_store.path.read_bytes() == before
    assert page._mixed_panel is None
    assert "原编排保留" in page.preview_state.text()
    page.close()
    settle(app)


def test_legacy_existing_mixed_draft_requires_explicit_choice_and_cancel_is_read_only(app):
    page, facade, tasks = _edited_legacy_page()
    facade.state_store.save_draft(DRAFT_ID, {"kind": "paper", "payload": {"foreign": "existing"}})
    asked = []
    page._confirm_open_saved_mixed_details = lambda: asked.append(True) or False
    before = facade.state_store.path.read_bytes()
    page.current_details_button.click()
    tasks.flush()
    assert asked == [True] and page._mixed_panel is None
    assert facade.state_store.path.read_bytes() == before
    page.close()
    settle(app)
