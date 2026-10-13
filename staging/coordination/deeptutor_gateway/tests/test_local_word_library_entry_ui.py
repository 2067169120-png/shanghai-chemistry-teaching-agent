"""Synthetic Qt routing evidence, with no profile/Office/recovery construction.

The production methods run against a small Qt owner and an in-memory namespace.
Every fixture in this module is synthetic; no inventory count is authority.
"""
from copy import deepcopy
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopVisualImportReceipt, DesktopVisualImportSourceSummary,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
from integrations.deeptutor_shchem_v1.desktop_workbench import main_window


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class Tasks(QObject):
    task_finished = Signal(str)
    task_cancelled = Signal(str)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.cancelled = []

    def submit_progress(self, title, operation, **callbacks):
        self.jobs.append((title, operation, callbacks))
        return f'synthetic-job-{len(self.jobs)}'

    def cancel(self, job):
        self.cancelled.append(job)

    def finish(self, index):
        _, operation, callbacks = self.jobs[index]
        value = operation(lambda _: None, lambda: False)
        callbacks['on_success'](value)
        return value


def receipt(*, word=True, pending=False):
    return DesktopVisualImportReceipt(
        batch_id='synthetic-saved-batch', source_type='合成教师讲义',
        status='pending' if pending else 'saved',
        visual_status='awaiting_visual_provider' if pending else 'completed',
        source_count=1, native_quick_count=0 if pending or not word else 1,
        visual_queue_count=int(pending),
        sources=(DesktopVisualImportSourceSummary(
            'handout', 1, '合成讲义.docx' if word else '合成扫描.pdf',
            'pending' if pending else 'native_text_complete'),),
        message_zh='合成回执：已保存；处理状态待核对。',
    )


def memory_facade(*, lane=None, receipts=()):
    state = {'unrelated': {'teacher_choice': 'keep'}}
    if lane is not None:
        state['question_explorer'] = {'lane': lane}
    writes, discarded, reads = [], [], []
    basket = [{'key': 'synthetic-existing-basket-item'}]
    catalog = {'items': [
        {'key': 'synthetic-unknown-question', 'title': '无标签合成题',
         'source_id': 'synthetic-source', 'source_name': '合成讲义.docx',
         'question_blocks': [{'index': 1, 'text': '合成题正文完整。', 'assets': []}],
         'context_blocks': [], 'answer_blocks': [], 'selection_ready': False},
        {'key': 'synthetic-stale-question', 'title': '旧标签合成题',
         'source_id': 'synthetic-source', 'source_name': '合成讲义.docx',
         'source_sha256': 'a' * 64, 'revision': 'current-question',
         'attributes': {'key': 'synthetic-stale-question', 'source_sha256': 'a' * 64,
                        'question_revision': 'old-question',
                        'primary_knowledge': {'id': 'K01', 'label': '合成旧标签'}},
         'question_blocks': [{'index': 2, 'text': '另一条合成题正文。', 'assets': []}],
         'context_blocks': [], 'answer_blocks': [], 'selection_ready': False},
    ], 'warnings': []}
    def update(mutator):
        mutator(state)
        writes.append(deepcopy(state))
    def word_catalog():
        reads.append('ordinary-current-profile-word-catalog')
        return deepcopy(catalog)
    def forbidden_core(**kwargs):
        raise AssertionError('Explicit Word entry must not fetch a core lane')
    facade = SimpleNamespace(
        state_store=SimpleNamespace(snapshot=lambda: deepcopy(state), _update=update),
        basket=lambda: deepcopy(basket),
        list_import_batches=lambda: (), list_imported_word_batches=lambda: receipts,
        list_provider_profiles=lambda: (), discard_import_preview=discarded.append,
        word_question_catalog=word_catalog, search_themes=forbidden_core,
    )
    return facade, SimpleNamespace(state=state, writes=writes, discarded=discarded,
                                    reads=reads, catalog=catalog, basket=basket)


def dispose(widget, app):
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_pending_word_receipt_offers_explicit_entry_without_reclassifying(app):
    saved = receipt(pending=True)
    before = saved.as_dict()
    facade, memory = memory_facade()
    dialog = ImportDialog(facade, Tasks())
    accepted = []
    dialog.accepted.connect(lambda: accepted.append('accepted-by-user'))
    assert dialog.word_library_button.isHidden()
    dialog._show_saved_receipt(saved)
    assert not dialog.word_library_button.isHidden()
    assert dialog.word_library_button.isEnabled()
    assert '当次快速导入统计：完整读取 Word 来源 0 份' in dialog.status.text()
    assert '已保存 Word 可去本地 Word 题库查看实际题目' in dialog.status.text()
    dialog._import_preview_session = {'preview_id': 'synthetic-unused-preview'}
    epoch = dialog._import_preview_epoch
    dialog.word_library_button.click()
    assert accepted == ['accepted-by-user']
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.open_word_library_requested
    assert memory.discarded == ['synthetic-unused-preview']
    assert dialog._import_preview_session is None and dialog._import_preview_epoch == epoch + 1
    assert saved.as_dict() == before
    assert not memory.writes
    dispose(dialog, app)


def test_saved_word_close_preserves_receipt_and_has_no_goto(app):
    saved = receipt()
    facade, memory = memory_facade(receipts=(saved,))
    dialog = ImportDialog(facade, Tasks())
    assert not dialog.word_library_button.isHidden()
    dialog._visual_batch_saved(saved)
    dialog.close_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.open_word_library_requested
    assert dialog._word_receipts[saved.batch_id] is saved
    assert not memory.writes
    dispose(dialog, app)


def test_nonword_receipt_has_no_word_goto_and_busy_guard_blocks_handoff(app):
    facade, _ = memory_facade()
    dialog = ImportDialog(facade, Tasks())
    dialog._show_saved_receipt(receipt(word=False))
    assert dialog.word_library_button.isHidden()
    assert not dialog.open_word_library_requested
    dialog._show_saved_receipt(receipt())
    dialog._active_task_id = 'synthetic-running-task'
    dialog._set_busy(True)
    dialog.word_library_button.click()
    dialog._open_word_library()
    assert not dialog.word_library_button.isEnabled()
    assert not dialog.open_word_library_requested
    dialog._active_task_id = None
    dialog._set_busy(False)
    assert dialog.word_library_button.isEnabled()
    dispose(dialog, app)


@pytest.mark.parametrize('lane', [None, 'master', 'wave1', 'supplemental', 'word_native', 'visual_native'])
def test_initial_page_respects_preference_without_implicit_save(app, lane):
    facade, memory = memory_facade(lane=lane)
    before = deepcopy(memory.state)
    page = QuestionExplorerPage(facade, Tasks())
    assert page.scope.currentData() == (lane or 'master')
    assert memory.state == before and not memory.writes
    dispose(page, app)


@pytest.mark.parametrize('lane', ['master', 'word_native', 'visual_native'])
def test_explicit_word_entry_resets_old_conditions_and_reads_unlabelled_rows(app, lane):
    facade, memory = memory_facade(lane=lane)
    tasks = Tasks()
    page = QuestionExplorerPage(facade, tasks)
    page.query.setText('前次不存在的关键词')
    page.filters = {'knowledge': {'K02'}}
    page.curriculum = {'section': 'old-synthetic-section'}
    page._page, page._cursors = 4, [None, 'synthetic-old-cursor']
    page._started = True
    page._needs_reload = True
    before = deepcopy(memory.catalog), deepcopy(memory.basket)
    page.open_word_library()
    assert page.scope.currentData() == 'word_native'
    assert page.query.text() == '' and page.filters == {} and page.curriculum == {}
    assert page._page == 0 and page._cursors == [None]
    assert len(memory.writes) == 1 and memory.state['question_explorer']['lane'] == 'word_native'
    assert memory.state['unrelated'] == {'teacher_choice': 'keep'}
    assert len(tasks.jobs) == 1
    assert not page._needs_reload
    tasks.finish(0)
    assert memory.reads == ['ordinary-current-profile-word-catalog']
    assert len(page.cards) == 2 and '2 条原题' in page.result_summary.text()
    assert [card.entry['key'] for card in page.cards] == ['synthetic-unknown-question', 'synthetic-stale-question']
    assert (memory.catalog, memory.basket) == before
    page.show()
    app.processEvents()
    assert len(tasks.jobs) == 1
    dispose(page, app)


@pytest.mark.parametrize('action, expected', [
    ('goto', 'library'), ('close', None), ('nonword_close', None),
    ('preparation', 'preparation'), ('textbooks', 'textbooks'),
    ('stale_goto_rejected', None),
])
def test_main_route_runs_real_dialog_action_on_minimal_owner(app, monkeypatch, action, expected):
    saved = receipt(word=action != 'nonword_close')
    facade, memory = memory_facade(lane='wave1', receipts=(saved,) if action != 'nonword_close' else ())
    tasks = Tasks()
    owner = QWidget()
    owner.facade, owner.tasks = facade, tasks
    owner.library_page = QuestionExplorerPage(facade, tasks)
    owner.library_page.query.setText('保留的普通检索条件')
    owner.library_page.filters = {'knowledge': {'K02'}}
    owner.library_page.curriculum = {'section': 'synthetic-section'}
    owner.library_page.scroll.verticalScrollBar().setValue(0)
    basket_counts, references, routes, dialogs = [], [], [], []
    owner.paper_page = SimpleNamespace(update_basket_count=basket_counts.append)
    owner.preparation_page = SimpleNamespace(import_word_reference=lambda value: references.append(value) or True)
    owner.navigate = routes.append
    original_invalidate = owner.library_page.invalidate_catalogs
    events = []
    def invalidate():
        events.append('invalidate')
        original_invalidate()
    owner.library_page.invalidate_catalogs = invalidate
    original_word_entry = owner.library_page.open_word_library
    def enter_word():
        events.append('word-entry')
        original_word_entry()
    owner.library_page.open_word_library = enter_word
    class InteractiveImport(ImportDialog):
        def exec(self):
            def user_action():
                self._show_saved_receipt(saved)
                self.basket_changed.emit(3)
                if action == 'goto':
                    self.word_library_button.click()
                elif action == 'preparation':
                    self.preparation_reference = {'materials': '合成原文参考'}
                    self.accept()
                elif action == 'textbooks':
                    self._open_textbooks()
                else:
                    if action == 'stale_goto_rejected':
                        self.open_word_library_requested = True
                    self.close_button.click()
            QTimer.singleShot(0, user_action)
            return super().exec()
    def create_dialog(facade_arg, tasks_arg, parent):
        assert facade_arg is facade and tasks_arg is tasks and parent is owner
        dialog = InteractiveImport(facade_arg, tasks_arg, parent)
        dialogs.append(dialog)
        return dialog
    monkeypatch.setattr(main_window, 'ImportDialog', create_dialog)
    main_window.TeacherWorkbenchWindow.open_import(owner)
    assert basket_counts == [3]
    assert routes == ([expected] if expected else [])
    if action == 'goto':
        assert not owner.library_page._needs_reload
        assert events == ['invalidate', 'word-entry']
        assert owner.library_page.scope.currentData() == 'word_native'
        assert owner.library_page.query.text() == '' and owner.library_page.filters == {}
        assert memory.state['question_explorer']['lane'] == 'word_native'
        assert len(memory.writes) == 1
    else:
        assert owner.library_page._needs_reload
        assert events == ['invalidate']
        assert owner.library_page.scope.currentData() == 'wave1'
        assert owner.library_page.query.text() == '保留的普通检索条件'
        assert owner.library_page.filters == {'knowledge': {'K02'}}
        assert owner.library_page.curriculum == {'section': 'synthetic-section'}
        assert not memory.writes
    assert references == ([{'materials': '合成原文参考'}] if action == 'preparation' else [])
    assert memory.basket == [{'key': 'synthetic-existing-basket-item'}]
    dispose(owner.library_page, app)
    dispose(owner, app)
