from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    PRODUCT_PATH,
    HandoutCandidateError,
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


def item(key="first", classification="native_text_complete"):
    return {
        "key": key,
        "revision": "a" * 64,
        "batch_id": "fixture",
        "package_id": "fixture-package",
        "title": "原生题组 · 第 2 题",
        "classification": classification,
        "question_text": "下列哪项符合所给条件？\nA. 甲 B. 乙",
        "answer_text": "【答案】B",
        "printed_number": "2",
        "atomic_count": 1,
        "parent_title": "原生题组",
        "source_name": "讲义（原卷版）.docx",
        "question_pages": [],
        "answer_pages": [],
        "blockers": []
        if classification == "native_text_complete"
        else ["non_text_dependency_present"],
        "source_document": {"sha256": "b" * 64},
        "candidate_only": True,
    }


class Reader:
    def __init__(self):
        self.items = [item(), item("second", "hybrid_visual_required")]

    def catalog(self):
        return {"items": deepcopy(self.items), "batches": [], "warnings": []}

    def get(self, key):
        return deepcopy(next(x for x in self.items if x["key"] == key))


@pytest.fixture
def service(tmp_path):
    reader = Reader()
    state = DesktopStateStore(tmp_path / "personal")
    service = HandoutCandidateService(tmp_path, state, reader_factory=lambda _: reader)
    return service, reader, state


def test_personal_review_survives_reopen_without_changing_sources(service):
    service, reader, state = service
    before = deepcopy(reader.items)
    event = service.record_review(
        "first",
        "a" * 64,
        decision="checked",
        question_checked=True,
        answer_checked=True,
        note="已逐页对应",
    )
    reopened = HandoutCandidateService(
        service.workspace,
        DesktopStateStore(state.root),
        reader_factory=lambda _: reader,
    )
    detail = reopened.detail("first")
    assert (
        detail["review_state"] == "checked" and detail["review"]["note"] == "已逐页对应"
    )
    assert (
        event["bank_ingest_allowed"] is False
        and event["answer_correctness_verified"] is False
    )
    assert reader.items == before and state.basket() == []
    assert len(state.snapshot()["drafts"]) == 1
    reopened.record_review(
        "first",
        "a" * 64,
        decision="needs_correction",
        question_checked=False,
        answer_checked=True,
        note="重新检查",
    )
    assert reopened.detail("first")["review_state"] == "needs_correction"
    assert len(state.snapshot()["drafts"]) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"decision": "official"},
        {"question_checked": 1},
        {"answer_checked": "yes"},
        {"note": "字" * 2001},
        {"question_checked": False},
        {"answer_checked": False},
    ],
)
def test_invalid_review_is_not_written(service, kwargs):
    service, _, state = service
    values = dict(decision="checked", question_checked=True, answer_checked=True)
    with pytest.raises(HandoutCandidateError):
        service.record_review("first", "a" * 64, **{**values, **kwargs})
    assert state.snapshot()["drafts"] == {}


def test_changed_source_invalidates_review_and_stale_operations(service):
    service, reader, state = service
    service.record_review(
        "first",
        "a" * 64,
        decision="checked",
        question_checked=True,
        answer_checked=True,
    )
    reader.items[0]["revision"] = "c" * 64
    assert service.detail("first")["review_state"] == "stale"
    with pytest.raises(HandoutCandidateError, match="变化"):
        service.record_review(
            "first",
            "a" * 64,
            decision="checked",
            question_checked=True,
            answer_checked=True,
        )
    with pytest.raises(HandoutCandidateError):
        service.copy_text("first", "a" * 64)
    assert len(state.snapshot()["drafts"]) == 1


def test_copy_does_not_require_promotion_and_marks_incomplete_or_unreviewed(service):
    service, _, _ = service
    full = service.copy_text("first", "a" * 64)
    assert "尚未完成本机核对" in full and "来源：讲义" in full
    partial = service.copy_text("second", "a" * 64)
    assert "原生文字片段" in partial and "不能替代完整原题" in partial
    assert "【答案】B" in partial


def test_page_bytes_bound_to_source_and_hash_and_both_roles(service):
    service, reader, _ = service
    for role in ("question", "answer"):
        relative = f"batches/render/{role}.png"
        path = service.workspace / PRODUCT_PATH / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture-image-bytes")
        reader.items[0][role + "_pages"] = [
            {
                "page": 1,
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ]
        assert service.page_bytes("first", "a" * 64, role, 0) == b"fixture-image-bytes"
        path.write_bytes(b"changed")
        with pytest.raises(HandoutCandidateError):
            service.page_bytes("first", "a" * 64, role, 0)


@pytest.mark.parametrize(
    "path",
    ["../outside.png", "batches/../../outside.png", "C:/outside.png", "notes.png"],
)
def test_source_page_cannot_escape_render_batches(service, path):
    service, reader, _ = service
    reader.items[0]["question_pages"] = [{"page": 1, "path": path, "sha256": "a" * 64}]
    with pytest.raises(HandoutCandidateError):
        service.page_bytes("first", "a" * 64, "question", 0)


def test_loader_errors_are_safe(service):
    service, _, _ = service
    service.reader_factory = lambda _: (_ for _ in ()).throw(
        RuntimeError("internal private details")
    )
    with pytest.raises(HandoutCandidateError) as exc:
        service.catalog()
    assert "internal" not in exc.value.message_zh


class Tasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        try:
            result = operation()
        except HandoutCandidateError as exc:
            on_failure(exc.message_zh)
        else:
            on_success(result)
        return "fixture-task"


def make_dialog(service, tasks=None):
    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_workbench.handout_candidate_dialog import (
        HandoutCandidateDialog,
    )

    app = QApplication.instance() or QApplication([])
    facade = SimpleNamespace(
        handout_candidate_catalog=service.catalog,
        handout_candidate_detail=service.detail,
        record_handout_candidate_review=service.record_review,
        handout_candidate_copy_text=service.copy_text,
        handout_candidate_page=service.page_bytes,
    )
    return app, HandoutCandidateDialog(facade, tasks or Tasks())


def test_native_browse_filter_review_and_copy(service):
    from PySide6.QtWidgets import QApplication

    service, _, _ = service
    app, dialog = make_dialog(service)
    dialog.resize(420, 900)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 420
    assert dialog.results.count() == 1 and "共 2 题" in dialog.summary.text()
    assert "所给条件" in dialog.question.toPlainText()
    assert "非官方" in dialog.answer.toPlainText()
    dialog.question_checked.setChecked(True)
    dialog.answer_checked.setChecked(True)
    dialog.note.setPlainText("原页对应记录")
    dialog.save_button.click()
    assert dialog._detail["review_state"] == "checked" and not dialog._dirty
    dialog.copy_button.click()
    assert "已记录题面与答案对应核对" in QApplication.clipboard().text()
    dialog.state_filter.setCurrentIndex(dialog.state_filter.findData("incomplete"))
    assert dialog.results.count() == 1 and "片段" in dialog.question.toPlainText()
    dialog.query.setText("不存在的关键词")
    dialog.search_button.click()
    assert dialog.results.count() == 0 and not dialog.copy_button.isEnabled()
    dialog.close()


def test_unsaved_edits_prevent_accidental_filter_change_or_close(service, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    service, _, _ = service
    _, dialog = make_dialog(service)
    dialog.note.setPlainText("未保存")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog.state_filter.setCurrentIndex(dialog.state_filter.findData("all"))
    assert dialog.state_filter.currentData() == "native_text_complete"
    assert dialog._detail["key"] == "first"
    dialog.close()
    assert not dialog._closed
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    dialog.close()
    assert dialog._closed


def test_late_detail_and_page_callbacks_do_not_replace_current_selection(service):
    service, _, _ = service
    _, dialog = make_dialog(service)
    generation = dialog._generation
    dialog.state_filter.setCurrentIndex(dialog.state_filter.findData("incomplete"))
    assert dialog._detail["key"] == "second"
    dialog._show_detail(generation, service.detail("first"))
    dialog._page_ready(generation, "旧页", b"invalid")
    assert dialog._detail["key"] == "second" and dialog._zoom is None
    dialog.close()
    dialog._show_detail(dialog._generation, {})


def test_native_review_tab_fits_narrow_window_with_real_workbench_style(service):
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        WORKBENCH_STYLE,
    )

    service, _, _ = service
    app, dialog = make_dialog(service)
    previous_style = app.styleSheet()
    try:
        app.setStyleSheet(WORKBENCH_STYLE)
        dialog.resize(420, 900)
        dialog.show()
        dialog.tabs.setCurrentIndex(3)
        app.processEvents()
        assert dialog.width() == 420
        assert (
            dialog.question_checked.width()
            >= dialog.question_checked.sizeHint().width()
        )
        assert dialog.save_button.isVisible()
    finally:
        dialog.close()
        app.setStyleSheet(previous_style)
