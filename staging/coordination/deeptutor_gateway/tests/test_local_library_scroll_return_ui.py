"""Real visible Qt scrollbar preservation across asynchronous import returns.

All sources and state are in-memory synthetic namespaces. No real MainWindow,
Facade, StateStore, Recovery, Office or user profile is constructed.
"""
from copy import deepcopy
from types import SimpleNamespace

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QAbstractSlider, QLabel, QLayout, QVBoxLayout, QWidget
import pytest

from test_local_word_library_entry_ui import app, Tasks, dispose, memory_facade, receipt
from integrations.deeptutor_shchem_v1.desktop_workbench.dialogs import ImportDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
from integrations.deeptutor_shchem_v1.desktop_workbench import main_window
from integrations.deeptutor_shchem_v1.desktop_workbench import question_explorer_page as explorer_ui


def settle(app):
    for _ in range(12):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def row(number):
    return {'key': f'synthetic-scroll-question-{number}', 'title': f'合成题{number}',
            'source_id': 'synthetic-source', 'source_name': '合成讲义.docx',
            'question_blocks': [{'index': number + 1,
                                 'text': f'合成完整题{number}。' + '完整条件与题面文字。' * 12,
                                 'assets': []}],
            'context_blocks': [], 'answer_blocks': [], 'selection_ready': False}


@pytest.fixture
def visible_library(app):
    saved = receipt()
    facade, memory = memory_facade(lane='word_native', receipts=(saved,))
    memory.catalog['items'] = [row(index) for index in range(6)]
    tasks = Tasks()
    owner = QWidget()
    owner.resize(980, 620)
    owner.facade, owner.tasks = facade, tasks
    layout = QVBoxLayout(owner)
    owner.library_page = QuestionExplorerPage(facade, tasks, owner)
    layout.addWidget(owner.library_page)
    page = owner.library_page
    page.query.setText('合成')
    page.filters = {'source': {'synthetic-source'}}
    page.curriculum = {'section': 'synthetic-current-section'}
    page._curriculum_label = '合成当前章节'
    owner.paper_page = SimpleNamespace(update_basket_count=lambda _: None)
    owner.preparation_page = SimpleNamespace(import_word_reference=lambda _: True)
    owner.routes = []
    owner.navigate = owner.routes.append
    owner.show()
    settle(app)
    assert page.isVisible() and len(tasks.jobs) == 1
    tasks.finish(0)
    settle(app)
    bar = page.scroll.verticalScrollBar()
    assert len(page.cards) == 6 and bar.maximum() > 0
    anchor = page.cards[2]
    bar.setValue(anchor.geometry().top() + 30)
    assert bar.value() > 0
    expected = page._reading_position()
    assert expected['key'] == anchor.entry['key'] and expected['offset'] == 30
    case = SimpleNamespace(owner=owner, page=page, tasks=tasks, facade=facade,
                           memory=memory, saved=saved, before=expected, app=app)
    yield case
    dispose(owner, app)


def return_from_import(case, monkeypatch, action):
    class InteractiveImport(ImportDialog):
        def exec(self):
            def user_action():
                self._visual_batch_saved(case.saved)
                if action == 'saved_close':
                    # The confirmed synthetic source changed the inventory;
                    # closing this dialog still returns Rejected, as in use.
                    case.memory.catalog['items'].insert(0, row(99))
                if action == 'goto':
                    self.word_library_button.click()
                else:
                    self.close_button.click()
            QTimer.singleShot(0, user_action)
            return super().exec()
    monkeypatch.setattr(main_window, 'ImportDialog', InteractiveImport)
    main_window.TeacherWorkbenchWindow.open_import(case.owner)


@pytest.mark.parametrize('action', ['close', 'saved_close'])
def test_visible_import_return_restores_same_item_after_actual_async_refresh(visible_library, monkeypatch, action):
    case = visible_library
    original_catalog_session = case.page._sessions['word_native']
    original_basket = deepcopy(case.memory.basket)
    return_from_import(case, monkeypatch, action)
    assert case.page.isVisible() and not case.owner.routes
    assert len(case.tasks.jobs) == 2 and case.page._loading
    assert case.page._sessions['word_native'] is not original_catalog_session
    assert case.page._reading_context() == case.before['context']
    case.tasks.finish(1)
    # The actual product _apply resets scroll first; restoration must wait for
    # Qt card height and QScrollArea layout events, not overwrite synchronously.
    assert case.page.scroll.verticalScrollBar().value() == 0
    settle(case.app)
    anchor = next(card for card in case.page.cards if card.entry['key'] == case.before['key'])
    actual = case.page.scroll.verticalScrollBar().value()
    assert actual == anchor.geometry().top() + case.before['offset'] and actual > 0
    assert case.memory.reads == ['ordinary-current-profile-word-catalog'] * 2
    assert len(case.page.cards) == (7 if action == 'saved_close' else 6)
    assert case.memory.basket == original_basket and not case.memory.writes
    if action == 'saved_close':
        assert actual != case.before['value']  # New first row moved the old item.


def test_visible_explicit_goto_starts_at_top_and_rejects_late_old_result(visible_library, monkeypatch):
    case = visible_library
    return_from_import(case, monkeypatch, 'goto')
    assert case.owner.routes == ['library'] and len(case.tasks.jobs) == 3
    assert case.page.query.text() == '' and case.page.filters == {} and case.page.curriculum == {}
    assert len(case.memory.writes) == 1
    _, old_operation, old_callbacks = case.tasks.jobs[1]
    old_result = old_operation(lambda _: None, lambda: False)
    case.tasks.finish(2)
    settle(case.app)
    final_entries = [card.entry['key'] for card in case.page.cards]
    assert final_entries and case.page.scroll.verticalScrollBar().value() == 0
    old_callbacks['on_success'](old_result)
    settle(case.app)
    assert [card.entry['key'] for card in case.page.cards] == final_entries
    assert case.page.scroll.verticalScrollBar().value() == 0
    assert 'synthetic-job-2' in case.tasks.cancelled


@pytest.mark.parametrize('change', ['query', 'filters', 'curriculum', 'new_search', 'scope'])
def test_pending_restore_does_not_rewrite_new_query_or_scope(visible_library, monkeypatch, change):
    case = visible_library
    return_from_import(case, monkeypatch, 'close')
    case.tasks.finish(1)
    assert case.page.scroll.verticalScrollBar().value() == 0
    epoch = case.page._epoch
    if change == 'query':
        case.page.query.setText('用户已修改的关键词')
    elif change == 'filters':
        case.page.filters = {'knowledge': {'K01'}}
    elif change == 'curriculum':
        case.page.curriculum = {'section': 'synthetic-user-changed-section'}
    elif change == 'new_search':
        # Identical conditions still get a new request token. Deliver its
        # result before the old restoration timer gets an event-loop turn.
        case.page.search()
        assert case.page._reading_context() == case.before['context']
        case.tasks.finish(2)
    else:
        case.page.scope.setCurrentIndex(case.page.scope.findData('master'))
    settle(case.app)
    assert case.page.scroll.verticalScrollBar().value() == 0
    if change in {'query', 'filters', 'curriculum'}:
        assert case.page._epoch == epoch  # Context guard, without new token.
    else:
        assert case.page._epoch > epoch


def test_current_word_result_page_and_nonzero_scroll_survive_import_reload(visible_library, monkeypatch):
    case = visible_library
    case.memory.catalog['items'] = [row(index) for index in range(18)]
    case.page.reload()
    case.tasks.finish(1)
    settle(case.app)
    case.page.turn_page(1)
    case.tasks.finish(2)
    settle(case.app)
    assert case.page._page == 1 and '第2页' in case.page.result_summary.text()
    anchor = case.page.cards[2]
    case.page.scroll.verticalScrollBar().setValue(anchor.geometry().top() + 25)
    case.before = case.page._reading_position()
    assert case.before['value'] > 0 and case.before['offset'] == 25
    return_from_import(case, monkeypatch, 'close')
    case.tasks.finish(3)
    settle(case.app)
    assert case.page._page == 1 and '第2页' in case.page.result_summary.text()
    anchor = next(card for card in case.page.cards if card.entry['key'] == case.before['key'])
    assert case.page.scroll.verticalScrollBar().value() == anchor.geometry().top() + 25


@pytest.mark.parametrize('detail_outcome', ['success', 'failure', 'user_scroll'])
def test_original_paper_reader_finishes_before_position_is_restored(visible_library, monkeypatch, detail_outcome):
    case = visible_library
    details = []
    def submit_detail(_label, operation, **callbacks):
        details.append((operation, callbacks))
        return f'synthetic-detail-{len(details)}'
    case.tasks.submit = submit_detail
    summaries = [SimpleNamespace(key=f'synthetic-core-{index}', scope='master',
        title_zh=f'合成原卷主题{index}', paper_title_zh='合成原卷（测试）', source_zh='纯内存测试',
        preview_zh='合成主题的全部条件与题面文字。' * 16,
        shared_context_zh='', atomic_total=1) for index in range(8)]
    case.facade.search_themes = lambda **_: SimpleNamespace(
        cards=summaries, scope='master', total_themes=8, has_more=False, next_cursor=None)
    case.facade.library_theme_detail = lambda summary: SimpleNamespace(key=summary.key, scope=summary.scope)
    case.facade.library_image = lambda *_: None
    class CoreReader(QWidget):
        preview_readiness_changed = Signal(bool, str)
        preparation_image_requested = Signal(dict)
        def __init__(self, _detail, _tasks, _loader, parent, **_kwargs):
            super().__init__(parent)
            QVBoxLayout(self).addWidget(QLabel('合成完整主题阅读器\n' * 12))
            self.preview_readiness = (True, '')
    monkeypatch.setattr(explorer_ui, 'LibraryDetailDialog', CoreReader)
    case.page.scope.setCurrentIndex(case.page.scope.findData('master'))
    case.tasks.finish(1)
    operation, callbacks = details[0]
    callbacks['on_success'](operation())
    settle(case.app)
    anchor = case.page.cards[2]
    case.page.scroll.verticalScrollBar().setValue(anchor.geometry().top() + 22)
    case.before = case.page._reading_position()
    assert case.before['value'] > 0
    return_from_import(case, monkeypatch, 'close')
    case.tasks.finish(2)
    settle(case.app)
    assert case.page._reading_restore is not None
    assert case.page.scroll.verticalScrollBar().value() == 0
    user_value = None
    if detail_outcome == 'user_scroll':
        bar = case.page.scroll.verticalScrollBar()
        bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd)
        user_value = bar.value()
        assert user_value > 0 and case.page._reading_restore is None
    operation, callbacks = details[1]
    if detail_outcome != 'failure':
        callbacks['on_success'](operation())
    else:
        callbacks['on_failure']('合成阅读器未就绪，原题仍可查看。')
    settle(case.app)
    anchor = next(card for card in case.page.cards if card.entry['key'] == case.before['key'])
    if user_value is None:
        assert case.page.scroll.verticalScrollBar().value() == anchor.geometry().top() + 22
    else:
        assert case.page.scroll.verticalScrollBar().value() == user_value
    assert case.page._reading_restore is None


@pytest.mark.parametrize('intervention', [None, 'scroll', 'query', 'filters', 'curriculum'])
def test_narrow_word_image_readiness_preserves_anchor_without_rearming_user_cancel(visible_library, monkeypatch, intervention):
    case = visible_library
    case.owner.setStyleSheet(main_window.WORKBENCH_STYLE)
    case.owner.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    case.page.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    case.owner.setMinimumSize(0, 0)
    case.page.setMinimumSize(0, 0)
    case.owner.resize(420, 800)
    data = QByteArray()
    output = QBuffer(data)
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(0xff4488aa)
    assert image.save(output, 'PNG')
    output.close()
    item = case.memory.catalog['items'][0]
    item['revision'], item['selection_ready'] = 'synthetic-word-revision', True
    item['question_blocks'][0]['assets'] = [
        {'asset_id': 'synthetic-only-png', 'preview_supported': True, 'mime_type': 'image/png'}]
    case.facade.word_question_image = lambda *_: {'bytes': bytes(data), 'mime_type': 'image/png',
                                                  'label': '合成原图（内存测试）'}
    def finish_images(index):
        _, operation, callbacks = case.tasks.jobs[index]
        result = operation(callbacks['on_progress'], lambda: False)
        assert result['completed'] == 1 and result['failed'] == 0
        callbacks['on_success'](result)
    case.page.reload()
    case.tasks.finish(1)
    finish_images(2)
    settle(case.app)
    assert case.page.cards[0].ready and case.page._reader.images[0].loaded
    anchor = case.page.cards[2]
    case.page.scroll.verticalScrollBar().setValue(anchor.geometry().top() + 16)
    case.before = case.page._reading_position()
    assert case.before['value'] > 0
    return_from_import(case, monkeypatch, 'close')
    case.tasks.finish(3)
    settle(case.app)
    assert case.page._reading_restore is None and case.page._reading_restore_epoch == case.page._epoch
    assert not case.page.cards[0].ready
    anchor = next(card for card in case.page.cards if card.entry['key'] == case.before['key'])
    bar = case.page.scroll.verticalScrollBar()
    assert bar.value() == anchor.geometry().top() + 16
    before_height = case.page.cards[0].minimumHeight()
    user_value = None
    if intervention == 'scroll':
        bar.triggerAction(QAbstractSlider.SliderAction.SliderSingleStepAdd)
        user_value = bar.value()
        assert user_value > 0 and case.page._reading_restore_epoch is None
    elif intervention is not None:
        user_value = bar.value()
        if intervention == 'query':
            case.page.query.setText('用户正在输入的新词')
        elif intervention == 'filters':
            case.page.filters = {'knowledge': {'K02'}}
        else:
            case.page.curriculum = {'section': 'synthetic-new-user-section'}
    finish_images(4)
    settle(case.app)
    assert case.page.cards[0].ready and case.page.cards[0].minimumHeight() < before_height, (
        case.owner.width(), case.page.width(), case.page.cards[0].width(), case.page.cards[0].note.width())
    anchor = next(card for card in case.page.cards if card.entry['key'] == case.before['key'])
    if intervention is not None:
        assert bar.value() == user_value
        if intervention == 'scroll':
            assert case.page._reading_restore_epoch is None
    else:
        assert bar.value() == anchor.geometry().top() + 16
    assert case.page._reading_restore is None
