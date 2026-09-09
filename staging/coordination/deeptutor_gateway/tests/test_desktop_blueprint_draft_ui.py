from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from test_desktop_blueprint_generation import (  # noqa: F401
    candidate,
    configured,
    desktop_paths,
)

from integrations.deeptutor_shchem_v1.desktop_blueprint_drafts import (
    BlueprintDraftService,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_workbench.blueprint_draft_dialog import (
    BlueprintDraftDialog,
)


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    state = DesktopStateStore(tmp_path / "state")
    original = {
        "kind": "textbook_prompt_blueprint",
        "status": "completed",
        "preview": {
            "evidence": [{"scope": "教材定义", "supports": ["ONLY_SOURCE_EVIDENCE"]}]
        },
        "result": {"candidate": candidate()},
    }
    state.save_draft("original", original)
    service = BlueprintDraftService(state)
    facade = SimpleNamespace(
        prompt_blueprint_draft_sources=service.sources,
        save_prompt_blueprint_draft=service.save,
    )
    dialog = BlueprintDraftDialog(facade, "original")
    dialog.show()
    app.processEvents()
    yield dialog, service, state, original
    dialog._dirty = False
    dialog.close()
    app.processEvents()


def test_edit_save_reopen_and_copy_without_source_changes(editor):
    dialog, service, state, original = editor
    dialog.theme.setPlainText("教师修改主题")
    dialog.purpose.setPlainText("样品S1；条件仍待补齐")
    dialog.fields["answer_outline"].setPlainText("条件不足，暂不作唯一结论")
    dialog.note.setPlainText("本地修改原因")
    assert dialog._dirty
    dialog.save_button.click()
    assert not dialog._dirty
    assert dialog.source.count() == 2
    assert state.snapshot()["drafts"]["original"] == original
    saved = service.load_source("original", dialog.source.currentData())
    assert saved["candidate"]["theme_center"] == "教师修改主题"
    assert (
        saved["candidate"]["shared_material_plan"][0]["purpose"]
        == "样品S1；条件仍待补齐"
    )
    assert (
        saved["candidate"]["question_chain"][0]["answer_outline"]
        == "条件不足，暂不作唯一结论"
    )
    assert "ONLY_SOURCE_EVIDENCE" in dialog.evidence.toPlainText()
    assert dialog.note.toPlainText() == "本地修改原因"
    assert "教师修改主题" not in dialog.original.toPlainText()
    dialog.copy_button.click()
    assert "教师修改主题" in QGuiApplication.clipboard().text()
    assert "不是正确性认证" in QGuiApplication.clipboard().text()
    # Moving back to the initial source does not mutate saved content.
    dialog.source.setCurrentIndex(0)
    assert dialog.theme.toPlainText() == original["result"]["candidate"]["theme_center"]
    dialog.source.setCurrentIndex(1)
    assert dialog.theme.toPlainText() == "教师修改主题"


def test_invalid_reference_retains_unsaved_editor_text(editor):
    dialog, _service, state, _original = editor
    before = deepcopy(state.snapshot())
    dialog.evidence_refs.setText("E999")
    dialog.save_button.click()
    assert dialog._dirty and dialog.evidence_refs.text() == "E999"
    assert state.snapshot() == before
    assert "无效" in dialog.status.text()


def test_reference_navigation_does_not_edit_content(editor):
    dialog, *_ = editor
    dialog.inspect_references.click()
    assert dialog.tabs.currentWidget() is dialog.evidence_panel
    assert dialog.evidence_pick.currentIndex() == 1
    assert "E1" in dialog.evidence.toPlainText()
    assert "ONLY_SOURCE_EVIDENCE" in dialog.evidence.toPlainText()
    assert not dialog._dirty


def _enable_page_fixture(editor):
    dialog, _service, state, original = editor
    root = deepcopy(original)
    evidence = {
        "scope": "讲义样例",
        "source_type": "user_handout_question_reference",
        "supports": ["原文字证据"],
    }
    root["preview"] = {
        "evidence": [evidence],
        "handout_reference": {
            "evidence": [evidence],
            "include_answers": False,
            "local_provenance": [
                {
                    "key": "candidate",
                    "revision": "revision",
                    "source_document": {},
                    "editable_source": {},
                }
            ],
        },
    }
    state.save_draft("original", root)
    dialog._reload_sources()
    return dialog


def test_evidence_page_open_preserves_unsaved_draft(editor):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QPixmap

    dialog = _enable_page_fixture(editor)
    calls = []
    pixmap = QPixmap(20, 20)
    pixmap.fill()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert pixmap.save(buffer, "PNG")
    data = bytes(buffer.data())

    class Immediate:
        def submit(self, _label, operation, *, on_success, on_failure):
            on_success(operation())

    dialog.tasks = Immediate()
    dialog.facade.prompt_blueprint_evidence_pages = lambda *args: [
        {"role": "question", "index": 0, "caption": "E1 · 题面第1页"}
    ]
    dialog.facade.prompt_blueprint_evidence_page = lambda *args: (
        calls.append(args) or data
    )
    dialog.theme.setPlainText("未保存的教师修改")
    dialog.evidence_pick.setCurrentIndex(1)
    assert dialog.source_open.isEnabled()
    dialog.source_open.click()
    assert dialog._source_zoom is not None
    assert calls == [("original", "E1", "question", 0)]
    assert dialog._dirty and dialog.theme.toPlainText() == "未保存的教师修改"
    dialog.evidence_pick.setCurrentIndex(0)
    assert not dialog.source_open.isEnabled()


def test_evidence_page_late_results_do_not_replace_other_selection(editor):
    dialog = _enable_page_fixture(editor)
    pending = []

    class Deferred:
        def submit(self, _label, operation, *, on_success, on_failure):
            pending.append((on_success, on_failure))

    dialog.tasks = Deferred()
    dialog.evidence_pick.setCurrentIndex(1)
    assert len(pending) == 1
    dialog.evidence_pick.setCurrentIndex(0)
    pending[0][0]([{"caption": "OLD", "role": "question", "index": 0}])
    pending[0][1]("OLD ERROR")
    assert dialog.source_page.count() == 0
    assert "OLD" not in dialog.source_page_hint.text()
    dialog.evidence_pick.setCurrentIndex(1)
    assert dialog.close()
    pending[1][0]([{"caption": "AFTER CLOSE", "role": "question", "index": 0}])
    assert dialog.source_page.count() == 0


def test_evidence_page_failure_keeps_source_and_draft_visible(editor):
    dialog = _enable_page_fixture(editor)

    class Failed:
        def submit(self, _label, operation, *, on_success, on_failure):
            on_failure("讲义来源已更新，原页不可替代旧依据。")

    dialog.tasks = Failed()
    dialog.evidence_pick.setCurrentIndex(1)
    assert not dialog.source_open.isEnabled()
    assert "已更新" in dialog.source_page_hint.text()
    assert "原文字证据" in dialog.evidence.toPlainText()
    assert not dialog._dirty


def test_cancel_source_switch_and_close_keeps_unsaved_edits(editor, monkeypatch):
    dialog, _service, _state, _original = editor
    dialog.save_button.click()
    selected = dialog.source.currentIndex()
    dialog.theme.setPlainText("尚未保存")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    dialog.source.setCurrentIndex(0)
    assert dialog.source.currentIndex() == selected
    assert dialog.theme.toPlainText() == "尚未保存"
    assert not dialog.close()
    dialog.reject()
    assert dialog.isVisible()


def test_switching_questions_does_not_lose_edits(editor):
    dialog, _service, _state, _original = editor
    first = dialog._candidate["question_chain"][0]
    second = deepcopy(first)
    second.update(atomic_part_id="Q1-A2", answer_outline="第二题")
    dialog._candidate["question_chain"].append(second)
    dialog.question.addItem("Q1 / Q1-A2")
    dialog.fields["answer_outline"].setPlainText("第一题修改")
    dialog.question.setCurrentIndex(1)
    assert dialog.fields["answer_outline"].toPlainText() == "第二题"
    dialog.fields["answer_outline"].setPlainText("第二题修改")
    dialog.question.setCurrentIndex(0)
    assert dialog.fields["answer_outline"].toPlainText() == "第一题修改"


@pytest.mark.parametrize("width", [420, 920])
def test_native_editor_respects_requested_width(editor, width):
    dialog, *_ = editor
    dialog.resize(width, 820)
    QApplication.processEvents()
    assert dialog.width() == width
    for index in range(3):
        scroll = dialog.tabs.widget(index)
        assert scroll.horizontalScrollBar().maximum() == 0


def test_parent_entry_uses_facade_and_respects_unsaved_child(configured, monkeypatch):  # noqa: F811
    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    facade, provider, local = configured
    generated = facade.generate_prompt_blueprint(
        local["preview_id"],
        "teacher-text",
        provider.revision,
        teacher_confirmed=True,
    )
    calls_before = list(provider.borrow_calls)
    app = QApplication.instance() or QApplication([])

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

    parent = PromptBlueprintDialog(facade, Tasks())
    parent.show()
    try:
        parent._compiled(local)
        parent._generated(generated)
        parent.profile.clear()
        parent.profile.addItem("离线", None)
        assert parent.edit_button.isEnabled()
        parent.edit_button.click()
        child = parent._edit_dialog
        assert child is not None
        child.theme.setPlainText("从真实facade入口编辑")
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
        )
        assert not parent.close()
        assert child.theme.toPlainText() == "从真实facade入口编辑"
        child.save_button.click()
        assert not child._dirty
        assert len(facade.prompt_blueprint_draft_sources(local["preview_id"])) == 2
        assert provider.borrow_calls == calls_before
    finally:
        if parent._edit_dialog is not None:
            parent._edit_dialog._dirty = False
        parent.close()
        app.processEvents()


class ImmediateTasks:
    def submit(self, label, operation, *, on_success, on_failure):
        on_success(operation())

    def submit_progress(self, label, operation, *, on_success, on_failure, on_progress):
        on_success(operation(on_progress, lambda: False))


@pytest.fixture
def review_editor(configured):  # noqa: F811
    from test_desktop_blueprint_review import ReviewTransport

    facade, provider, local = configured
    facade.generate_prompt_blueprint(
        local["preview_id"],
        "teacher-text",
        provider.revision,
        teacher_confirmed=True,
    )
    facade._blueprint_review_transport = ReviewTransport()
    app = QApplication.instance() or QApplication([])
    profile = facade.preparation_profiles()[0]
    dialog = BlueprintDraftDialog(
        facade,
        local["preview_id"],
        tasks=ImmediateTasks(),
        profile=profile,
    )
    dialog.show()
    app.processEvents()
    yield dialog, facade, provider
    if dialog._review_dialog is not None:
        dialog._review_dialog._running = False
        dialog._review_dialog.close()
    dialog._dirty = False
    dialog.close()
    app.processEvents()


def test_saved_draft_review_entry_and_confirmation_use_edited_source(
    review_editor, monkeypatch
):
    dialog, facade, provider = review_editor
    borrowed = len(provider.borrow_calls)
    assert not dialog.review_button.isEnabled()
    dialog.theme.setPlainText("本次实际待审教师稿")
    dialog.save_button.click()
    selected_id = dialog.source.currentData()
    assert dialog.review_button.isEnabled()
    dialog.review_button.click()
    child = dialog._review_dialog
    assert child.source_draft_id == selected_id
    assert "本次实际待审教师稿" in child.original.toPlainText()
    assert child.tabs.tabText(0) == "教师待审稿"
    assert len(provider.borrow_calls) == borrowed
    prompts = []

    def confirm(_parent, _title, text, *_args):
        prompts.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    child.start.click()
    assert selected_id == child._result["source_draft_id"]
    assert "教师草稿" in prompts[0] and "两次模型调用" in prompts[0]
    assert len(facade._blueprint_review_transport.requests) == 2
    child.close()
    assert dialog.source.currentData() == selected_id
    assert any(s["source_kind"] == "textbook_blueprint_review" for s in dialog._sources)
    # Selecting the AI revision doesn't silently approve or submit it; save first.
    dialog.source.setCurrentIndex(
        next(
            i
            for i, s in enumerate(dialog._sources)
            if s["source_kind"] == "textbook_blueprint_review"
        )
    )
    assert not dialog.review_button.isEnabled()


def test_unsaved_changes_disable_review_and_history_remains_offline(review_editor):
    dialog, _facade, provider = review_editor
    dialog.save_button.click()
    borrowed = len(provider.borrow_calls)
    dialog.theme.setPlainText("未保存不可送审")
    assert not dialog.review_button.isEnabled()
    dialog._review()
    assert dialog._review_dialog is None
    dialog.save_button.click()
    dialog.profile = None
    assert dialog.review_button.isEnabled()
    dialog.review_button.click()
    child = dialog._review_dialog
    assert not child.start.isEnabled()
    assert "未保存不可送审" in child.original.toPlainText()
    assert len(provider.borrow_calls) == borrowed


def test_editor_close_waits_for_active_review(review_editor):
    dialog, _facade, _provider = review_editor
    dialog.save_button.click()
    dialog.review_button.click()
    child = dialog._review_dialog
    child._running = True
    assert not dialog.close()
    assert child._stop.is_set()
    dialog.reject()
    assert dialog.isVisible()
    child._running = False
    child.close()
    assert dialog.close()
