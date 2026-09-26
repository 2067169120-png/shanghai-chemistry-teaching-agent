from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from staging.coordination.deeptutor_gateway.tests.test_desktop_library_ui import (
    _card,
    _detail,
    _Facade,
    _image,
    _ManualBridge,
    _png_bytes,
    _search_result,
    _visible_text,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(qt_app):
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    bridge = _ManualBridge()
    facade = _Facade()
    page = LibraryPage(facade, bridge)
    yield page, facade, bridge
    page.close()
    qt_app.processEvents()


def _load_selected(page, facade, bridge, *, detail=None):
    page._apply_results(_search_result(_card("A", "主题甲"), _card("B", "主题乙")))
    task = bridge.pending.pop(0)
    bridge.succeed(task, detail or facade.details["A"])


def _finish_images(bridge, *, bad_index=None, decode_failure=False):
    tasks = list(bridge.pending)
    bridge.pending.clear()
    for index, task in enumerate(tasks):
        if index == bad_index:
            if decode_failure:
                bridge.succeed(task, b"NOT-A-DECODABLE-IMAGE")
            else:
                bridge.fail(task, "合成题图读取失败")
        else:
            bridge.succeed(task, _png_bytes())


def test_detail_loading_and_open_window_alone_cannot_add_to_basket(library):
    page, facade, bridge = library
    page._apply_results(_search_result(_card("A", "主题甲")))
    assert not page.add_button.isEnabled()
    page._add_current()
    assert facade.added == []
    bridge.succeed(bridge.pending.pop(0), facade.details["A"])
    page._add_current()
    assert facade.added == [] and not page.add_button.isEnabled()
    page._open_detail()
    assert page._detail_dialog is not None
    assert len(bridge.pending) == 3  # One shared image plus two question images.
    assert not page._detail_dialog.preview_readiness[0]
    assert page._previewed_detail_key is None
    page._add_current()
    assert facade.added == []
    first = bridge.pending.pop(0)
    bridge.succeed(first, _png_bytes())
    assert not page.add_button.isEnabled()
    _finish_images(bridge)
    assert page.add_button.isEnabled()
    assert page._previewed_detail_key == "A"
    page._add_current()
    assert facade.added == ["A"]
    assert "不代表参考答案已核验" in page._detail_dialog.preview_readiness[1]


@pytest.mark.parametrize("bad_index", [0, 1, 2])
@pytest.mark.parametrize("decode_failure", [False, True])
def test_each_question_or_shared_image_failure_blocks_basket(
    library, bad_index, decode_failure
):
    page, facade, bridge = library
    _load_selected(page, facade, bridge)
    page._open_detail()
    _finish_images(bridge, bad_index=bad_index, decode_failure=decode_failure)
    assert not page.add_button.isEnabled()
    assert not page._detail_dialog.preview_readiness[0]
    assert "失败" in page._detail_dialog.preview_readiness[1]
    page._add_current()
    assert facade.added == []


@pytest.mark.parametrize("failure", ["load", "key", "scope", "empty"])
def test_failed_or_mismatched_detail_cannot_enter_basket(library, failure):
    page, facade, bridge = library
    page._apply_results(_search_result(_card("A", "主题甲")))
    task = bridge.pending.pop(0)
    if failure == "load":
        bridge.fail(task, "合成详情读取失败")
    else:
        changes = {"key": {"key": "B"}, "scope": {"scope": "wave1"}, "empty": {"parts": ()}}
        bridge.succeed(task, replace(facade.details["A"], **changes[failure]))
    assert not page.add_button.isEnabled()
    page._open_detail()
    page._add_current()
    assert facade.added == []


def test_stale_detail_and_preview_completion_cannot_unlock_new_selection(library):
    page, facade, bridge = library
    _load_selected(page, facade, bridge)
    page._open_detail()
    old_dialog = page._detail_dialog
    old_generation = page._detail_generation
    old_tasks = list(bridge.pending)
    bridge.pending.clear()
    page.results.setCurrentRow(1)
    current = bridge.pending.pop(0)
    for task in old_tasks:
        bridge.succeed(task, _png_bytes())
    page._preview_readiness_changed(
        old_generation, facade.details["A"], old_dialog, True, "陈旧预览已完成"
    )
    assert not page.add_button.isEnabled()
    bridge.succeed(current, facade.details["B"])
    page._add_current()
    assert facade.added == [] and page._previewed_detail_key is None
    page._open_detail()
    _finish_images(bridge)
    assert page.add_button.isEnabled()
    page._add_current()
    assert facade.added == ["B"]


@pytest.mark.parametrize("first_ready", [False, True])
def test_reopen_uses_new_image_results_and_old_destroy_cannot_clear_it(
    library, first_ready
):
    page, facade, bridge = library
    _load_selected(page, facade, bridge)
    page._open_detail()
    old = page._detail_dialog
    _finish_images(bridge, bad_index=None if first_ready else 0)
    assert page.add_button.isEnabled() is first_ready
    old.close()
    page._open_detail()
    current = page._detail_dialog
    assert current is not old and current is not None
    assert not page.add_button.isEnabled()
    page._detail_dialog_destroyed(old)
    assert page._detail_dialog is current
    page._preview_readiness_changed(
        page._detail_generation, facade.details["A"], old, True, "旧窗口信号"
    )
    assert not page.add_button.isEnabled()
    _finish_images(bridge)
    assert page.add_button.isEnabled()
    page._add_current()
    assert facade.added == ["A"]


def test_closed_pending_preview_ignores_late_image_callbacks(library):
    page, facade, bridge = library
    _load_selected(page, facade, bridge)
    page._open_detail()
    dialog = page._detail_dialog
    dialog.close()
    _finish_images(bridge)
    assert not page.add_button.isEnabled()
    page._add_current()
    assert facade.added == []


def test_replacement_detail_with_same_key_requires_a_new_preview(library):
    page, facade, bridge = library
    _load_selected(page, facade, bridge)
    page._open_detail()
    old = page._detail_dialog
    _finish_images(bridge)
    assert page.add_button.isEnabled()
    replacement = replace(facade.details["A"], context_zh="更新后的合成共同材料")
    page._detail_loaded(page._detail_generation, "A", replacement)
    assert not page.add_button.isEnabled()
    page._preview_readiness_changed(
        page._detail_generation, facade.details["A"], old, True, "旧详情已完成"
    )
    page._add_current()
    assert facade.added == []
    page._open_detail()
    assert page._detail_dialog.detail is replacement
    _finish_images(bridge)
    assert page.add_button.isEnabled()


def test_source_text_can_preview_but_summary_cannot_replace_missing_question(library):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    page, facade, bridge = library
    original = _detail("A", "主题甲", with_images=False)
    _load_selected(page, facade, bridge, detail=original)
    page._open_detail()
    assert not bridge.pending
    assert not page.add_button.isEnabled()
    assert "摘要不能代替原题" in page._detail_dialog.preview_readiness[1]
    page._add_current()
    assert facade.added == []
    source_text = replace(
        original,
        parts=tuple(
            replace(part, question_text_zh=f"源绑定合成原题 {part.key}：写出给定反应的离子方程式。")
            for part in original.parts
        ),
    )
    page._apply_results(_search_result(_card("A", "主题甲")))
    bridge.succeed(bridge.pending.pop(0), source_text)
    page._open_detail()
    assert not bridge.pending
    assert page.add_button.isEnabled()
    assert "源绑定合成原题" in _visible_text(page._detail_dialog)
    labels = page._detail_dialog.findChildren(QLabel, "LibraryOriginalQuestionText")
    assert len(labels) == len(source_text.parts)
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
    assert "摘要不是原题替代品" not in _visible_text(page._detail_dialog)
    assert all(part.question_text_zh == "" for part in original.parts)
    page._add_current()
    assert facade.added == ["A"]


def test_missing_answer_and_failed_answer_image_do_not_claim_or_block_question_ready(library):
    from PySide6.QtWidgets import QPushButton

    page, facade, bridge = library
    original = facade.details["A"]
    first = replace(original.parts[0], answer_images=(_image("answer-a", "answer"),))
    detail = replace(original, parts=(first, original.parts[1]))
    facade.library_answer_image = lambda image: b"INVALID-ANSWER-IMAGE"
    _load_selected(page, facade, bridge, detail=detail)
    page._open_detail()
    _finish_images(bridge)
    assert page.add_button.isEnabled()
    dialog = page._detail_dialog
    assert "未找到可对齐的参考答案" in _visible_text(dialog)
    button = dialog.findChild(QPushButton, "LibraryShowAnswerImage")
    button.click()
    assert len(bridge.pending) == 1
    bridge.fail(bridge.pending.pop(0), "合成答案图读取失败")
    assert page.add_button.isEnabled()
    assert "不代表参考答案已核验" in dialog.preview_readiness[1]
    page._add_current()
    assert facade.added == ["A"]


@pytest.mark.parametrize("entry", ["library", "preparation", "import"])
def test_three_word_entrypoints_forward_basket_signal_to_paper_page(
    qt_app, monkeypatch, entry
):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QDialog

    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        dialogs,
        main_window,
        word_question_dialog,
        workflow_pages,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from staging.coordination.deeptutor_gateway.tests.test_desktop_ui import (
        _Facade as WindowFacade,
    )

    opened = []
    counts = []

    class QuestionChoice(QObject):
        basket_changed = Signal(int)
        DialogCode = QDialog.DialogCode

        def __init__(self, facade, tasks, parent=None, **kwargs):
            super().__init__()
            self.preparation_reference = None
            opened.append(kwargs)

        def exec(self):
            self.basket_changed.emit(7)
            return self.DialogCode.Rejected  # Added questions survive closing the reader.

    class ImportChoice(QObject):
        basket_changed = Signal(int)
        DialogCode = QDialog.DialogCode

        def __init__(self, facade, tasks, parent=None):
            super().__init__()
            self.facade, self.tasks = facade, tasks
            self._active_task_id = None
            self.word_batch_combo = SimpleNamespace(currentData=lambda: "synthetic-batch")
            self.preparation_reference = None

        def exec(self):
            dialogs.ImportDialog._open_word_questions(self)
            return self.DialogCode.Rejected

    monkeypatch.setattr(word_question_dialog, "WordQuestionDialog", QuestionChoice)
    monkeypatch.setattr(main_window, "ImportDialog", ImportChoice)
    monkeypatch.setattr(workflow_pages.PaperPage, "update_basket_count", lambda self, count=None: counts.append(count))
    # A real native window and signal wiring, but no worker or real state reads.
    monkeypatch.setattr(DesktopTaskBridge, "submit", lambda *args, **kwargs: "synthetic-task")
    window = main_window.TeacherWorkbenchWindow(WindowFacade())
    try:
        counts.clear()
        if entry == "library":
            window.library_page._open_word_questions()
        elif entry == "preparation":
            window.preparation_page.topic.setText("合成课题")
            window.preparation_page._import_word_questions()
        else:
            window.open_import()
        assert counts == [7]
        assert len(opened) == 1
        if entry == "preparation":
            assert opened == [{"lesson_topic": "合成课题"}]
        elif entry == "import":
            assert opened == [{"batch_id": "synthetic-batch"}]
    finally:
        window.close()
        qt_app.processEvents()
