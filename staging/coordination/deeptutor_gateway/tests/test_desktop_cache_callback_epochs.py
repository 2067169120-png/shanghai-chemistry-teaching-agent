"""Real local worker results delivered after another facade's maintenance."""
from copy import deepcopy
import hashlib
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel

from test_desktop_cache_maintenance import qt_app as qt_app, until
from test_desktop_visual_import_facade import _facade, desktop_paths as desktop_paths
from test_word_questions_service import _import
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage


class _QueuedDelivery(QObject):
    deliver = Signal(object)

    def __init__(self):
        super().__init__()
        self.deliver.connect(lambda entry: entry[0](entry[1]), Qt.ConnectionType.QueuedConnection)


class _DeferredTasks(DesktopTaskBridge):
    """Keep native worker results, then replay their GUI delivery explicitly."""
    def __init__(self):
        super().__init__()
        self.defer_labels = set()
        self.held = []
        self.delivery = _QueuedDelivery()

    def _defer(self, label, callback):
        if callback is None or label not in self.defer_labels:
            return callback
        return lambda value: self.held.append((callback, value))

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        return super().submit(label, operation,
            on_success=self._defer(label, on_success), on_failure=self._defer(label, on_failure))

    def submit_progress(self, label, operation, *, on_progress=None, on_success=None, on_failure=None):
        return super().submit_progress(label, operation,
            on_progress=self._defer(label, on_progress), on_success=self._defer(label, on_success),
            on_failure=self._defer(label, on_failure))

    def replay(self, app):
        held, self.held = self.held, []
        for entry in held:
            self.delivery.deliver.emit(entry)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _clear_other(facade, provider):
    other = _facade(facade.paths, provider)
    plan = other.plan_desktop_cache_cleanup()
    result = other.clear_desktop_caches(plan["plan_id"], plan["revision"], ["word_previews", "word_catalog"])
    assert result["status"] == ("complete" if os.name == "nt" else "partial"), result
    if os.name != "nt":
        assert result["removed_files"] == result["removed_rows"] == 0
    return result


@pytest.fixture
def word_context(qt_app, desktop_paths, tmp_path):
    facade, original, provider = _import(desktop_paths, tmp_path, image=True)
    tasks = _DeferredTasks()
    dialog = WordQuestionDialog(facade, tasks)
    dialog.show()
    until(qt_app, lambda: dialog._loaded_once and not tasks._workers)
    dialog.hide()
    dialog._persist_choices = False
    full = facade.word_question_catalog()
    question = next(row for row in full["items"] if any(block.get("assets") for block in row["question_blocks"]))
    dialog._items = {row["key"]: deepcopy(row) for row in full["items"]}
    dialog._current_key = question["key"]
    dialog._selected = {question["key"]: question["revision"]}
    dialog._points[question["key"]] = 7
    dialog._image_cache.clear()
    dialog._current_displayed_images.clear()
    yield facade, original, provider, tasks, dialog, question
    tasks.defer_labels.clear()
    dialog.close()
    tasks.shutdown()
    qt_app.processEvents()


@pytest.mark.parametrize("pending", [False, True])
def test_late_image_cannot_refill_cache_or_display_pixmap(qt_app, word_context, pending):
    facade, original, provider, tasks, dialog, question = word_context
    asset = next(asset for block in question["question_blocks"] for asset in block.get("assets", []))
    widget = QLabel("queued source image", dialog)
    old_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    if pending:
        callbacks = []
        dialog._load_image(question, asset, widget, dialog._detail_epoch, pending=callbacks)
        result = facade.word_question_image(question["key"], question["revision"], asset["asset_id"])
        tasks.held.append((callbacks[0][1], result))
    else:
        tasks.defer_labels.add("读取 Word 题目来源图片")
        dialog._load_image(question, asset, widget, dialog._detail_epoch)
        until(qt_app, lambda: bool(tasks.held) and not tasks._workers)
    assert tasks.held and not dialog._image_cache
    _clear_other(facade, provider)
    tasks.replay(qt_app)
    assert not dialog._image_cache and not dialog._current_displayed_images
    assert widget.pixmap() is None or widget.pixmap().isNull()
    assert dialog._cache_projection_stale and not dialog._items
    assert dialog._selected[question["key"]] == question["revision"]
    assert dialog._points[question["key"]] == 7
    assert hashlib.sha256(original.read_bytes()).hexdigest() == old_sha and provider.borrow_calls == 0


@pytest.mark.parametrize("failure", [False, True])
def test_late_inline_success_or_failure_does_not_restore_old_reader(qt_app, word_context, monkeypatch, failure):
    from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionError
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_inline_reader import WordInlineReader
    facade, _, provider, tasks, dialog, question = word_context
    tasks.defer_labels.add("读取 Word 原文图文位置")
    if failure:
        def fail(*args, **kwargs):
            raise WordQuestionError("合成延迟失败")
        monkeypatch.setattr(facade, "word_question_reading_layout", fail)
    dialog._clear_panel(0)
    fallback = []
    original_render = dialog._render_tab
    def render(index, *, native=True):
        if not native:
            fallback.append(index)
        return original_render(index, native=native)
    monkeypatch.setattr(dialog, "_render_tab", render)
    dialog._render_inline_tab(question, 0, question["context_blocks"] + question["question_blocks"])
    until(qt_app, lambda: bool(tasks.held) and not tasks._workers)
    _clear_other(facade, provider)
    tasks.replay(qt_app)
    assert not fallback and not dialog.findChildren(WordInlineReader)
    assert not dialog._current_displayed_images and not dialog._items
    assert dialog._cache_projection_stale and provider.borrow_calls == 0


@pytest.mark.parametrize("confirm", [False, True])
def test_late_reference_cannot_restore_preview_or_accept_dialog(qt_app, word_context, confirm):
    facade, _, provider, tasks, dialog, question = word_context
    dialog._catalog_busy = False
    if confirm:
        dialog._preview_selected()
        until(qt_app, lambda: dialog._preview_reference is not None and not tasks._workers)
        tasks.defer_labels.add("确认 Word 选题参考")
        dialog._confirm_reference(composition=False)
    else:
        tasks.defer_labels.add("预览 Word 选题")
        dialog._preview_selected()
    until(qt_app, lambda: bool(tasks.held) and not tasks._workers)
    _clear_other(facade, provider)
    tasks.replay(qt_app)
    assert dialog._preview_reference is None and dialog.preparation_reference is None
    assert dialog.result() != QDialog.DialogCode.Accepted and not dialog.import_button.isEnabled()
    assert dialog._selected[question["key"]] == question["revision"] and provider.borrow_calls == 0


@pytest.mark.parametrize("action", ["_edit_attributes", "_adjust_range", "_confirm_reference", "_add_to_basket", "_export"])
def test_stale_mutation_entry_cannot_submit_work(qt_app, word_context, action):
    facade, _, provider, tasks, dialog, question = word_context
    state_sha = hashlib.sha256(facade.state_store.path.read_bytes()).hexdigest()
    _clear_other(facade, provider)
    serial = dialog._job_serial
    getattr(dialog, action)(composition=False) if action == "_confirm_reference" else getattr(dialog, action)()
    assert dialog._job_serial == serial and not tasks._workers and not tasks.held
    assert dialog._cache_projection_stale and not dialog._items
    assert dialog._selected[question["key"]] == question["revision"] and dialog._points[question["key"]] == 7
    assert hashlib.sha256(facade.state_store.path.read_bytes()).hexdigest() == state_sha
    assert provider.borrow_calls == 0


def test_late_explorer_image_readiness_cannot_reactivate_old_card(qt_app, desktop_paths, tmp_path):
    facade, _, provider = _import(desktop_paths, tmp_path, image=True)
    tasks = _DeferredTasks()
    tasks.defer_labels.add("读取本题图片")
    page = QuestionExplorerPage(facade, tasks)
    page.scope.setCurrentIndex(page.scope.findData("word_native"))
    page.search()
    until(qt_app, lambda: bool(tasks.held) and not tasks._workers and page._reader is not None)
    card = page._active_card
    assert card is not None and not card.ready
    _clear_other(facade, provider)
    tasks.replay(qt_app)
    assert not card.ready and page._active_card is None and page._reader is None
    assert not page._sessions and not page.cards and page._needs_reload
    assert "需重新读取" in page.result_summary.text() and provider.borrow_calls == 0
    page.close()
    tasks.shutdown()


def test_knowledge_link_modal_worker_guard_rejects_changed_cache(qt_app, word_context, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog import KnowledgeQuestionLinksDialog
    facade, _, provider, tasks, word, _ = word_context
    word._allow_knowledge_links = True
    word.knowledge_links_button.setEnabled(True)
    checked = []

    def modal_exec(dialog):
        until(qt_app, lambda: not tasks._workers)
        dialog._check()
        state_sha = hashlib.sha256(facade.state_store.path.read_bytes()).hexdigest()
        _clear_other(facade, provider)
        # The actual link-service guard is also called on backend workers.
        # It must reject the old binding without reloading Qt from that thread.
        def run_guard():
            try:
                dialog._check()
            except ValueError as exc:
                checked.append(exc)
        thread = threading.Thread(target=run_guard)
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and len(checked) == 1
        assert hashlib.sha256(facade.state_store.path.read_bytes()).hexdigest() == state_sha
        assert not word._cache_projection_stale
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(KnowledgeQuestionLinksDialog, "exec", modal_exec)
    word._manage_knowledge_links()
    assert len(checked) == 1 and provider.borrow_calls == 0
