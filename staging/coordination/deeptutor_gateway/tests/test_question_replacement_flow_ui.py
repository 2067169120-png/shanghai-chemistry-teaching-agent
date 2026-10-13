"""Actual D09 Word-source flow with synthetic pagination; E: test state only."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QLabel

from mixed_pagination_test_support import synthetic_pagination
from test_desktop_visual_import_facade import StaticV2Renderer
from test_word_question_dialog import _isolated_qt_app
from runtime.deeptutor_shchem.independent_paper_fixture import seed
from runtime.deeptutor_shchem.mixed_paper_draft_undo_qa import settle
from runtime.deeptutor_shchem.word_import_identity_qa import QueuedTasks
from integrations.deeptutor_shchem_v1 import desktop_question_replacements as replacement_module
from integrations.deeptutor_shchem_v1 import paper_export_renderer
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperService, _word_key
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_question_replacements import QuestionReplacementService
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_visual_import_adapters import LocalPageRendererV2Adapter
from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
from integrations.deeptutor_shchem_v1.desktop_workbench.explorer_reader import PersonalQuestionReader
from integrations.deeptutor_shchem_v1.desktop_workbench.scan_paper_page import ScanPaperPage


@pytest.fixture
def qt_app():
    with _isolated_qt_app() as app:
        create_application(["question-replacement-real-flow-ui"])
        yield app


@pytest.fixture
def flow(tmp_path, monkeypatch, qt_app):
    assert tmp_path.resolve().drive.upper() == "E:", "Run this fixture with an E: --basetemp."
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setenv("TEMP", str(temporary))
    monkeypatch.setenv("TMP", str(temporary))
    monkeypatch.setattr(replacement_module, "_TEMP_ROOT", temporary / "source-preflight")
    pagination_calls, ui_errors = [], []
    original_pagination = MixedPaperService.prepare_pagination
    def paginate(service, identity, fingerprint):
        service._pagination_builder = synthetic_pagination
        pagination_calls.append((identity, fingerprint))
        return original_pagination(service, identity, fingerprint)
    monkeypatch.setattr(MixedPaperService, "prepare_pagination", paginate)
    # seed uses the normal Word importer, which also archives a page bitmap.
    # Inject only that rasterizer; the DOCX bytes and native parsing stay real.
    source_renderer = StaticV2Renderer()
    monkeypatch.setattr(LocalPageRendererV2Adapter, "render", lambda _self, source, *, source_role:
        source_renderer.render(source, source_role=source_role))
    monkeypatch.setattr(paper_export_renderer, "_render_with_canonical_docx_tool",
        lambda *_args, **_kwargs: pytest.fail("This flow must never invoke Office or an external converter."))
    monkeypatch.setattr(sys, "excepthook", lambda typ, value, tb: ui_errors.append((typ.__name__, str(value))))
    facade, source, rows, providers = seed(tmp_path / "real-word-fixture")
    assert providers.calls == 0 and facade.list_provider_profiles() == ()
    original = source.read_bytes()
    first, second = sorted(rows, key=lambda row: row["block_start"])
    facade.state_store.clear_basket()
    facade.add_word_questions_to_basket([{"key": first["key"], "revision": first["revision"], "points": 3}])
    tasks = QueuedTasks()
    page = ScanPaperPage(facade, tasks)
    page.resize(1000, 780)
    page.show()
    settle(qt_app)
    shell = page._composer
    shell.new_button.click()
    tasks.flush()
    panel = shell._mixed_panel
    assert panel is not None and panel._loaded_once and not panel._restore_failed
    assert panel.model.order == [_word_key(first["key"])]
    facade.state_store.clear_basket()
    page.update_basket_count()
    assert not facade.basket() and panel.model.order == [_word_key(first["key"])]
    try:
        yield SimpleNamespace(facade=facade, source=source, first=first, second=second,
            providers=providers, tasks=tasks, page=page, shell=shell, original=original,
            pagination_calls=pagination_calls, ui_errors=ui_errors, root=tmp_path.resolve())
    finally:
        page.close()
        shell.close()
        tasks.flush()
        facade.shutdown()
        assert source.read_bytes() == original
        assert providers.calls == 0
        assert ui_errors == []


def _approved_preview(panel, tasks, app):
    panel.preview_button.click()
    tasks.flush()
    settle(app)
    preview, dialog = panel._preview, panel._preview_dialog
    assert preview is not None, panel.status.text()
    assert dialog is not None and dialog.review is not None and not dialog.review.failed
    for index in (0, 1):
        dialog.tabs.setCurrentIndex(index)
        tasks.flush()
        settle(app)
        assert dialog.review_page_button.isEnabled(), dialog.status.text()
        dialog.review_page_button.click()
    assert dialog.review.can_confirm and dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    tasks.flush()
    assert panel._approved and panel.export_button.isEnabled()
    assert dialog.confirmed
    dialog.reject()
    return preview


def _restore(shell, tasks, *, version_id):
    shell.versions_button.click()
    tasks.flush()
    dialog = shell._versions_dialog
    assert dialog is not None and dialog.versions.count() >= 1, shell._mixed_panel.status.text()
    from PySide6.QtCore import Qt
    index = next(index for index in range(dialog.versions.count())
        if dialog.versions.item(index).data(Qt.ItemDataRole.UserRole) == version_id)
    dialog.versions.setCurrentRow(index)
    dialog.confirm.setChecked(True)
    assert dialog.restore_button.isEnabled()
    results = []
    dialog.saved.connect(results.append)
    dialog.restore_button.click()
    tasks.flush()
    assert len(results) == 1, dialog.status.text()
    assert shell._versions_dialog is None
    return results[0], shell._mixed_panel


def test_real_word_replacement_reopen_details_preview_restore_and_undo(flow, monkeypatch, qt_app):
    facade, shell, tasks = flow.facade, flow.shell, flow.tasks
    panel = shell._mixed_panel
    identity, old_key, new_key = panel.facade.paper_id, _word_key(flow.first["key"]), _word_key(flow.second["key"])
    panel.title.setText("完整题段替换 · 合成验收卷")
    panel.title.editingFinished.emit()
    panel.points.setValue(7)
    panel.save_button.click()
    old_payload = deepcopy(panel._draft_session.current.record["payload"])
    old_undo_count = panel._draft_session.undo_count
    assert old_undo_count >= 2 and old_payload["settings"][old_key]["points"] == 7
    old_preview = _approved_preview(panel, tasks, qt_app)
    assert flow.pagination_calls and flow.providers.calls == 0

    # Obtain the actual D08 metadata-only entry and its source-version vector.
    result = facade.search_unified_questions(lanes=["word_native"], query="观测记录",
        filters={"knowledge": [], "knowledge_mode": "all"}, limit=8)
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["key"] == flow.second["key"] and entry["lane"] == "word_native"
    assert entry["source_locator"]["revision"] == flow.second["revision"]
    assert not {"question_blocks", "context_blocks", "answer_blocks"}.intersection(entry["payload"])
    assert entry["match_evidence"]["terms"] == ["观测记录"]
    assert entry["match_evidence"]["answer_searched"] is False
    requests = []
    def search(**request):
        requests.append(deepcopy(request))
        return deepcopy(result)
    monkeypatch.setattr(facade, "search_unified_questions", search)
    panel.replace_button.click()
    dialog = panel._replacement_dialog
    assert dialog is not None
    dialog.goal.setPlainText("练习记录观测证据，说明仍缺少哪些判断依据")
    dialog.query.setText("观测记录")
    dialog.search_button.click()
    tasks.flush()
    assert dialog.results.count() == 1 and requests[0]["query"] == "观测记录"
    dialog.read_button.click()
    tasks.flush()
    assert isinstance(dialog._reader, PersonalQuestionReader) and dialog._candidate_ready
    read_labels = "\n".join(label.text() for label in dialog._reader.findChildren(QLabel))
    assert "观测记录" in read_labels and "合成答案乙" in read_labels
    dialog.prepare_button.click()
    tasks.flush()
    plan = deepcopy(dialog._plan)
    assert plan is not None, dialog.status.text()
    assert isinstance(facade._question_replacements(), QuestionReplacementService)
    assert facade._question_replacements().service._word_validator is None
    assert plan["paper_id"] == identity and plan["target_key"] == old_key and plan["new_key"] == new_key
    assert plan["match_evidence"]["terms"] == ["观测记录"]
    assert plan["match_evidence"]["difficulty"] == "unknown"
    assert plan["match_evidence"]["student_diagnosis"] == "not_claimed"
    assert "观测记录" in dialog.evidence.text()
    assert all(reader.ready and isinstance(reader.native, PersonalQuestionReader)
        for reader in dialog._section_readers)
    assert [reader.native.entry["payload"]["key"] for reader in dialog._section_readers] == [
        flow.first["key"], flow.second["key"]]
    assert not facade.basket() and panel.model.order == [old_key]
    assert flow.source.read_bytes() == flow.original
    saved = []
    dialog.saved.connect(saved.append)
    for checkbox in (dialog.read_confirm, dialog.goal_confirm, dialog.replace_confirm):
        checkbox.setChecked(True)
    assert dialog.commit_button.isEnabled()
    dialog.commit_button.click()
    tasks.flush()
    assert len(saved) == 1 and saved[0]["status"] == "saved", panel.status.text()
    fresh = shell._mixed_panel
    assert fresh is not panel and fresh.facade.paper_id == identity and not fresh._restore_failed
    assert panel._preview is None and not panel._approved
    assert fresh.model.order == [new_key] and fresh._current_key() == new_key
    assert fresh._preview is None and not fresh._approved and not fresh.export_button.isEnabled()
    assert fresh.model.settings[new_key] == plan["after_item"]["settings"]
    assert fresh._draft_session.undo_count == 0 and not fresh.undo_button.isEnabled()
    assert not facade.basket() and flow.providers.calls == 0

    fresh.details_button.click()
    tasks.flush()
    details = fresh._details_dialog.details
    assert details is not None and details["section_count"] == 1
    assert [row["key"] for row in details["rows"]] == [new_key]
    assert details["rows"][0]["source_ref"]["key"] == flow.second["key"]
    fresh._details_dialog.reject()
    new_preview = _approved_preview(fresh, tasks, qt_app)
    assert new_preview.preview_id != old_preview.preview_id and new_preview.preview_hash != old_preview.preview_hash
    sections = new_preview.preview_model["sections"]
    assert len(sections) == 1 and sections[0]["key"] == new_key
    assert "观测记录" in str(sections[0]["student_blocks"])
    assert "合成答案乙" not in str(sections[0]["student_blocks"])
    assert "合成答案乙" in str(sections[0]["teacher_blocks"])
    assert "合成材料甲" not in str(sections[0])
    assert len(flow.pagination_calls) == 2
    versions = facade.list_question_replacement_versions(identity)
    assert [version["version_id"] for version in versions["versions"]] == [saved[0]["version_id"]]

    # New-source edits belong to that source version. Restoring the old source
    # also restores its genuine earlier editing journal, without merging them.
    fresh.points.setValue(5)
    assert fresh._draft_session.undo_count == 1
    restored, old = _restore(shell, tasks, version_id=saved[0]["version_id"])
    assert old is not fresh and old.facade.paper_id == identity and old.model.order == [old_key]
    assert old._draft_session.current.record["payload"] == old_payload
    assert old._draft_session.undo_count == old_undo_count
    assert old._preview is None and not old._approved and not old.export_button.isEnabled()
    old.undo_button.click()
    assert old.model.order == [old_key] and old.model.settings[old_key]["points"] == 3
    old.redo_button.click()
    assert old._draft_session.current.record["payload"] == old_payload
    settle(qt_app)
    assert shell.versions_button.isEnabled(), "The version entry must recover after an ordinary undo/redo finishes."
    chain = facade.list_question_replacement_versions(identity)
    assert [version["version_id"] for version in chain["versions"]] == [
        restored["version_id"], saved[0]["version_id"]]
    assert chain["versions"][0]["action"] == "恢复完整来源版本"
    returned, second = _restore(shell, tasks, version_id=restored["version_id"])
    assert second.facade.paper_id == identity and second.model.order == [new_key]
    assert second.model.settings[new_key]["points"] == 5 and second._draft_session.undo_count == 1
    assert second._preview is None and not second._approved
    final_chain = facade.list_question_replacement_versions(identity)
    assert [version["version_id"] for version in final_chain["versions"]] == [
        returned["version_id"], restored["version_id"], saved[0]["version_id"]]
    assert not facade.basket() and flow.providers.calls == 0 and facade.list_provider_profiles() == ()
    assert hashlib.sha256(flow.source.read_bytes()).hexdigest() == hashlib.sha256(flow.original).hexdigest()
    assert flow.ui_errors == []
    for path in flow.root.rglob("*"):
        assert Path(path).resolve().drive.upper() == "E:"


def test_real_cas_rejection_keeps_external_title_and_invalidates_old_approved_preview(flow, monkeypatch, qt_app):
    facade, shell, tasks = flow.facade, flow.shell, flow.tasks
    panel = shell._mixed_panel
    identity, old_key = panel.facade.paper_id, panel.model.order[0]
    old_preview = _approved_preview(panel, tasks, qt_app)
    assert panel._approved and panel.export_button.isEnabled()
    result = facade.search_unified_questions(lanes=["word_native"], query="观测记录",
        filters={"knowledge": [], "knowledge_mode": "all"}, limit=8)
    assert len(result["entries"]) == 1 and result["entries"][0]["key"] == flow.second["key"]
    monkeypatch.setattr(facade, "search_unified_questions", lambda **_kwargs: deepcopy(result))
    panel.replace_button.click()
    dialog = panel._replacement_dialog
    dialog.goal.setPlainText("完整题段改为练习填写观测记录")
    dialog.query.setText("观测记录")
    dialog.search_button.click()
    tasks.flush()
    dialog.read_button.click()
    tasks.flush()
    dialog.prepare_button.click()
    tasks.flush()
    plan = deepcopy(dialog._plan)
    assert plan is not None and all(reader.ready for reader in dialog._section_readers), dialog.status.text()
    assert panel._preview is old_preview and panel._approved
    other_window = DesktopStateStore(facade.state_store.root)
    draft_id = identity + ":" + DRAFT_ID
    external_record = other_window.draft_snapshot(draft_id).record
    external_record["payload"]["settings_ui"]["title"] = "另一窗口已保存的真实外部题名"
    other_window.save_draft(draft_id, external_record)
    frozen = other_window.path.read_bytes()
    assert facade.unified_question_search_is_current(result["version_vector"]), (
        "A saved-paper title edit must leave the unchanged Word source vector current.")
    saved, unconfirmed = [], []
    dialog.saved.connect(saved.append)
    dialog.save_unconfirmed.connect(unconfirmed.append)
    for checkbox in (dialog.read_confirm, dialog.goal_confirm, dialog.replace_confirm):
        checkbox.setChecked(True)
    assert dialog.commit_button.isEnabled()
    dialog.commit_button.click()
    tasks.flush()
    assert not saved and len(unconfirmed) == 1
    assert "当前卷已变化" in dialog.status.text()
    assert "不会自动重试" in dialog.status.text() and dialog._uncertain
    assert other_window.path.read_bytes() == frozen
    assert other_window.draft_snapshot(draft_id).record == external_record
    assert shell._mixed_panel is panel and panel.model.order == [old_key]
    assert panel._restore_failed and panel._draft_session.current is None
    assert panel._preview is None and not panel._approved and panel._artifact_paths == {}
    assert not panel.preview_button.isEnabled() and not panel.export_button.isEnabled()
    assert not panel.title.isEnabled() and not panel.save_button.isEnabled()
    assert not shell.versions_button.isEnabled()
    dialog.commit()
    assert not tasks.pending and len(unconfirmed) == 1
    assert other_window.path.read_bytes() == frozen
    with pytest.raises(DesktopFacadeError):
        facade.commit_question_replacement(plan["plan_id"], plan["plan_sha256"])
    assert other_window.path.read_bytes() == frozen
    dialog.reject()
    assert panel._replacement_dialog is None and panel._restore_failed
    assert not panel.export_button.isEnabled() and not panel.title.isEnabled()
    assert not facade.basket() and flow.providers.calls == 0
    assert flow.source.read_bytes() == flow.original and flow.ui_errors == []
