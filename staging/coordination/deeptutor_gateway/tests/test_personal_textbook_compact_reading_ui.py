"""Compact actions route through original buttons; full native PDF tools remain.

All new fixtures use synthetic in-memory PDF bytes, never a normal profile or
actual textbook. Geometry is not evidence of actual source-page appearance.
"""
from copy import deepcopy
import io

import pytest
from pypdf import PdfWriter
from PySide6.QtCore import QByteArray, QIODevice, QPoint, QPointF, QRect, QSignalBlocker, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from test_textbook_preparation_context_ui import (
    app as app, workspace as workspace, BOOK, TOPIC_A, TOPIC_B, add_memory_pdf,
    card_backend, preview, reading_state,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.components import set_status
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


pytestmark = pytest.mark.parametrize('workspace', ['personal'], indirect=True)


@pytest.fixture
def compact(workspace, app, monkeypatch):
    w = workspace
    install_ui_font(app)
    w.page.setStyleSheet(WORKBENCH_STYLE)
    w.page.set_preparation_context({**TOPIC_A, 'work_title': '弱电解质的电离平衡：教材与完整讲义'})
    preview(w)
    host = QMainWindow()
    host.setMinimumSize(360, 560)
    root_widget = QWidget()
    body = QHBoxLayout(root_widget)
    host.setCentralWidget(root_widget)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(0)
    sidebar = QWidget()
    sidebar.setFixedWidth(112)
    body.addWidget(sidebar)
    content = QWidget()
    column = QVBoxLayout(content)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    toolbar, footer = QWidget(), QWidget()
    toolbar.setFixedHeight(64)
    footer.setFixedHeight(33)
    column.addWidget(toolbar)
    column.addWidget(w.page, 1)
    column.addWidget(footer)
    body.addWidget(content, 1)
    host.resize(700, 960)
    host.show()
    w.page.show()
    app.processEvents()
    reader = add_memory_pdf(monkeypatch, w, app, full_controls=True)
    writer, buffer = PdfWriter(), io.BytesIO()
    for _number in range(129):
        writer.add_blank_page(600, 800)
    writer.write(buffer)
    reader._loaded_pages = False
    reader.document.close()
    reader.buffer.close()
    reader.buffer.setData(QByteArray(buffer.getvalue()))
    reader.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader._source = {'source_id': BOOK['source_id'], 'revision': BOOK['revision'],
                      'source_sha256': BOOK['revision'], 'pdf_pages': [68, 69, 70],
                      'section_id': 'SEC1', 'study_revision': 'e' * 64,
                      'entry_origin': 'personal_textbook', 'reading_mode': 'book'}
    reader.concept.update(section_id='SEC1', study_revision='e' * 64)
    reader.document.load(reader.buffer)
    reader._loaded_pages = True
    with QSignalBlocker(reader.pages):
        reader.pages.clear()
        for number in (68, 69, 70):
            reader.pages.addItem(f'PDF第{number}页 · 印刷待核对', number)
    long_title = '沪科技化学选修第一册【高清教材】.pdf · 隔离验收：教材仪器与弱电解质电离平衡原页'
    reader.heading.setText(long_title)
    w.page.book.setItemText(w.page.book.currentIndex(), '已导入 · ' + long_title)
    reader.reading_memory.setText('已记住当前阅读位置 · PDF第70页')
    reader.view.pageNavigator().jump(69, QPointF())
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    app.processEvents()
    assert reader.document.pageCount() == 129
    assert reader.pages.currentData() == 70 and reader.view.pageNavigator().currentPage() == 69
    assert reader.current_page_crop_button.isEnabled()
    assert reader.position.text() == 'PDF第70页 · 印刷待核对\n当前范围 3 / 3 页'
    w.reader, w.host = reader, host
    yield w
    w.owner.reading_actions_menu.close()
    w.page.setParent(None)
    host.close()
    host.deleteLater()
    app.processEvents()


def preserved(w):
    return (reading_state(w), deepcopy(w.reader._source), w.reader.buffer.data(),
            w.reader.pages.currentData(), w.reader.view.pageNavigator().currentPage(),
            w.reader.view.zoomMode(), w.reader.view.zoomFactor())


def trigger_menu(w, name, app):
    menu = w.owner.reading_actions_menu
    menu.popup(w.owner.reading_actions_button.mapToGlobal(QPoint(0, w.owner.reading_actions_button.height())))
    app.processEvents()
    getattr(w.owner, f'reading_{name}_action').trigger()
    menu.close()
    app.processEvents()



def assert_note_menu_reachable(w, app):
    owner = w.owner
    assert owner.note_actions_button.isVisible()
    rect = QRect(owner.note_actions_button.mapTo(w.page, QPoint()), owner.note_actions_button.size())
    assert w.page.rect().contains(rect)
    pairs = [(getattr(owner, f'note_{name}_action'), getattr(owner, button)) for name, button in (
        ('read_book', 'read_book_button'), ('read_section', 'read_section_button'),
        ('preview', 'preview_button'), ('confirm', 'confirm_button'),
        ('cards', 'cards_button'), ('clear', 'clear_button'))]
    owner.note_actions_menu.popup(owner.note_actions_button.mapToGlobal(QPoint()))
    app.processEvents()
    assert owner.note_actions_menu.isVisible()
    assert owner.note_actions_menu.actions() == [action for action, button in pairs]
    assert all(action.text() == button.text() and action.isEnabled() == button.isEnabled()
               for action, button in pairs)
    for action, button in pairs:
        assert owner.note_actions_menu.rect().contains(owner.note_actions_menu.actionGeometry(action))
    QTest.keyClick(owner.note_actions_menu, Qt.Key.Key_Escape)
    app.processEvents()
    assert not owner.note_actions_menu.isVisible()


def test_700_main_window_full_reader_tools_and_menu_geometry(compact, app, record_property):
    w, reader = compact, compact.reader
    assert w.page.size().width() == 588 and w.page.size().height() == 863
    assert w.owner.reading_actions_button.isVisible() and w.owner.confirm_button.isVisible()
    assert w.owner.actions.direction() == QBoxLayout.Direction.LeftToRight
    assert not w.owner.preview_button.isVisible() and not w.owner.cards_button.isVisible() and not w.owner.clear_button.isVisible()
    record_property('page_width', w.page.width())
    record_property('page_height', w.page.height())
    record_property('synthetic_pdf_viewport_height', reader.view.viewport().height())
    record_property('reader_heading_height', reader.heading.height())
    record_property('reader_position_height', reader.position.height())
    record_property('context_height', w.page.preparation_context_bar.height())
    assert reader.view.viewport().height() >= 250, reader.view.viewport().size()
    for control in (w.page.preparation_context_bar, w.page.book, reader.heading, reader.reading_details,
                    reader.pages, reader.zoom, reader.previous, reader.next, reader.position,
                    reader.current_page_crop_button, reader.reading_memory, w.owner.directory_button,
                    w.owner.confirm_button, w.owner.reading_actions_button):
        rect = QRect(control.mapTo(w.page, control.rect().topLeft()), control.size())
        assert control.isVisible() and w.page.rect().contains(rect), (type(control).__name__, rect)
    assert reader.heading.height() >= reader.heading.fontMetrics().height() * 2
    assert reader.position.height() >= reader.position.fontMetrics().height() * 2
    assert w.owner.reading_preview_action.isEnabled() and w.owner.reading_cards_action.isEnabled() and w.owner.reading_clear_action.isEnabled()
    assert w.owner.reading_actions_button.accessibleName() == '预览备课参考、知识卡加入课件或清空勾选'


def test_open_and_dismiss_menu_have_no_selection_reference_or_handoff_effect(compact, app):
    w = compact
    before, calls = preserved(w), deepcopy(w.facade.calls)
    received = []
    w.owner.reference_requested.connect(received.append)
    w.owner.cards_requested.connect(received.append)
    w.owner.image_requested.connect(received.append)
    menu = w.owner.reading_actions_menu
    menu.popup(w.owner.reading_actions_button.mapToGlobal(QPoint()))
    app.processEvents()
    assert menu.isVisible()
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    app.processEvents()
    assert not menu.isVisible()
    assert preserved(w) == before and not received and w.facade.calls == calls and not w.bridge.pending


def test_embedded_details_share_page_zoom_row_and_preserve_reading(compact, app):
    w, reader = compact, compact.reader
    before = preserved(w)
    row_centres = [control.mapTo(reader, control.rect().center()).y()
                   for control in (reader.reading_details, reader.pages, reader.zoom)]
    assert max(row_centres) - min(row_centres) <= 1
    assert reader.reading_details.accessibleName() == '展开或收起阅读范围、研读提示与素材索引'
    assert '完整阅读范围' in reader.reading_details.toolTip()
    assert '完整页码与范围' in reader.pages.toolTip()
    reader.reading_details.click()
    app.processEvents()
    assert reader.summary_tabs.isVisible() and reader.reading_details.text() == '收起详情'
    assert preserved(w) == before
    reader.reading_details.click()
    app.processEvents()
    assert not reader.summary_tabs.isVisible() and reader.reading_details.text() == '阅读详情'
    assert preserved(w) == before and reader.view.viewport().height() >= 250


def test_menu_preview_uses_original_button_and_reference_route(compact, app):
    w = compact
    clicked, received = [], []
    w.owner.preview_button.clicked.connect(lambda: clicked.append('preview'))
    w.owner.reference_requested.connect(received.append)
    source, chosen = deepcopy(w.reader._source), deepcopy(w.owner._checked)
    trigger_menu(w, 'preview', app)
    assert clicked == ['preview'] and len(w.bridge.pending) == 1 and not received
    assert w.owner.tabs.currentIndex() == 0
    w.bridge.succeed(w.bridge.take('核对个人教材备课参考'), run=True)
    assert w.owner.tabs.currentIndex() == 3 and w.owner._reference is not None
    assert w.owner.preview_button.isVisible() and w.owner.cards_button.isVisible() and w.owner.clear_button.isVisible()
    assert not w.owner.reading_actions_button.isVisible()
    assert w.reader._source == source and w.owner._checked == chosen and w.owner._reader is w.reader
    assert w.reader.pages.currentData() == 70 and w.reader.view.pageNavigator().currentPage() == 69
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    assert w.owner.reading_actions_button.isVisible() and not w.owner.preview_button.isVisible()


def test_menu_cards_uses_original_button_and_same_guarded_handoff(compact, app, monkeypatch):
    w = compact
    card_calls = card_backend(monkeypatch, w)
    clicked, received = [], []
    w.owner.cards_button.clicked.connect(lambda: clicked.append('cards'))
    w.owner.cards_requested.connect(received.append)
    before = preserved(w)
    trigger_menu(w, 'cards', app)
    assert clicked == ['cards'] and not received
    w.bridge.succeed(w.bridge.take('核对个人教材备课参考'), run=True)
    assert len(card_calls) == len(received) == 1 and preserved(w) == before
    w.owner.cards_appended()
    w.owner._controls()
    assert w.owner.reading_cards_action.text() == w.owner.cards_button.text() == '本次知识卡已加入'
    assert not w.owner.reading_cards_action.isEnabled()


def test_menu_clear_uses_original_button_and_notes_can_be_checked_again(compact, app):
    w = compact
    clicked = []
    w.owner.clear_button.clicked.connect(lambda: clicked.append('clear'))
    reader, source = w.reader, deepcopy(w.reader._source)
    trigger_menu(w, 'clear', app)
    assert clicked == ['clear'] and not w.owner._checked and w.owner._reference is None
    assert not w.owner.reading_actions_button.isVisible() and not w.owner.confirm_button.isVisible()
    assert not w.bridge.pending and w.owner._reader is reader and reader._source == source
    assert reader.pages.currentData() == 70 and reader.view.pageNavigator().currentPage() == 69
    w.owner.tabs.setCurrentIndex(2)
    app.processEvents()
    assert_note_menu_reachable(w, app)
    assert not w.owner.note_preview_action.isEnabled() and not w.owner.note_clear_action.isEnabled()
    w.owner.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert w.owner._checked and w.owner.preview_button.isEnabled()
    assert w.owner.note_preview_action.isEnabled() and w.owner.note_clear_action.isEnabled()
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    assert w.owner.reading_actions_button.isVisible() and w.owner.reading_preview_action.isEnabled()


def test_busy_and_about_to_show_sync_actions_without_side_effects(compact, app):
    w = compact
    before, calls = preserved(w), deepcopy(w.facade.calls)
    w.owner._busy = True
    w.owner._controls()
    assert not w.owner.reading_actions_button.isEnabled() and not w.owner.confirm_button.isEnabled()
    for action in w.owner.reading_actions_menu.actions():
        assert not action.isEnabled()
        action.trigger()
    assert preserved(w) == before and w.facade.calls == calls and not w.bridge.pending
    w.owner._busy = False
    w.owner._controls()
    w.owner.cards_appended()
    # aboutToShow must refresh after a receipt callback outside _controls.
    w.owner.reading_actions_menu.popup(w.owner.reading_actions_button.mapToGlobal(QPoint()))
    app.processEvents()
    assert not w.owner.reading_cards_action.isEnabled() and w.owner.reading_cards_action.text() == '本次知识卡已加入'
    w.owner.reading_actions_menu.close()
    assert preserved(w) == before


def test_wide_resize_and_return_to_notes_restore_original_buttons(compact, app):
    w = compact
    before = preserved(w)
    w.host.resize(1366, 960)
    app.processEvents()
    for button in (w.owner.read_book_button, w.owner.read_section_button, w.owner.preview_button,
                   w.owner.confirm_button, w.owner.cards_button, w.owner.clear_button):
        assert button.isVisible()
    assert not w.owner.reading_actions_button.isVisible() and preserved(w) == before
    w.host.resize(700, 960)
    app.processEvents()
    assert w.owner.reading_actions_button.isVisible() and preserved(w) == before
    w.owner.tabs.setCurrentIndex(2)
    app.processEvents()
    assert w.owner.actions.direction() == QBoxLayout.Direction.LeftToRight
    assert_note_menu_reachable(w, app)
    for button in (w.owner.read_book_button, w.owner.read_section_button, w.owner.preview_button,
                   w.owner.confirm_button, w.owner.cards_button, w.owner.clear_button):
        assert not button.isVisible()
    assert not w.owner.reading_actions_button.isVisible() and preserved(w) == before
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    assert w.owner.reading_actions_button.isVisible() and preserved(w) == before


def test_stale_source_menu_confirmation_still_rejects(compact, app, monkeypatch):
    w = compact
    card_backend(monkeypatch, w)
    w.facade.changed = True
    received = []
    w.owner.cards_requested.connect(received.append)
    trigger_menu(w, 'cards', app)
    pending = w.bridge.take('核对个人教材备课参考')
    with pytest.raises(ValueError, match='变化'):
        pending.operation()
    w.bridge.fail(pending, '参考内容已变化，请重新预览。')
    assert not received and w.owner._reference is None and w.owner._checked
    assert not w.owner.reading_cards_action.isEnabled()


def test_late_target_menu_confirmation_cannot_reach_new_course(compact, app, monkeypatch):
    w = compact
    card_backend(monkeypatch, w)
    received = []
    w.owner.cards_requested.connect(received.append)
    before = preserved(w)
    trigger_menu(w, 'cards', app)
    pending = w.bridge.take('核对个人教材备课参考')
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    w.bridge.succeed(pending, run=True)
    assert not received and preserved(w) == before and '备课已切换' in w.owner.status.text()
    assert w.owner.reading_cards_action.isEnabled()


def test_cancelled_menu_request_cannot_restore_or_handoff(compact, app, monkeypatch):
    w = compact
    card_backend(monkeypatch, w)
    received = []
    w.owner.cards_requested.connect(received.append)
    source, chosen = deepcopy(w.reader._source), deepcopy(w.owner._checked)
    trigger_menu(w, 'cards', app)
    pending = w.bridge.take('核对个人教材备课参考')
    w.bridge.task_cancelled.emit(pending.task_id, pending.label)
    w.bridge.task_finished.emit(pending.task_id)
    w.bridge.succeed(pending, run=True)
    assert not received and w.owner._reference is None and w.owner._checked == chosen
    assert w.owner._reader is w.reader and w.reader._source == source and w.reader.pages.currentData() == 70


@pytest.mark.parametrize('scenario', ['cards-success', 'late-target', 'source-error'])
def test_long_success_late_or_error_status_keeps_reading_space(compact, app, monkeypatch, record_property, scenario):
    w = compact
    card_backend(monkeypatch, w)
    received = []
    w.owner.cards_requested.connect(received.append)
    source, chosen = deepcopy(w.reader._source), deepcopy(w.owner._checked)
    trigger_menu(w, 'cards', app)
    pending = w.bridge.take('核对个人教材备课参考')
    if scenario == 'cards-success':
        w.bridge.succeed(pending, run=True)
        w.owner.cards_appended()
        w.owner._controls()
        assert len(received) == 1
        assert w.owner.status.text() == '所选知识卡已加入当前课件；可编排内容，并保存或导出教案、PPT和学习单。'
        assert w.owner.reading_cards_action.text() == '本次知识卡已加入' and not w.owner.reading_cards_action.isEnabled()
        app.processEvents()
        record_property('natural_success_viewport_height', w.reader.view.viewport().height())
        # Stress the same success wording on two explicit lines. Native wrapping
        # depends on font metrics; the unmodified product string was checked above.
        set_status(w.owner.status, 'success', w.owner.status.text().replace('；', '；\n', 1))
    elif scenario == 'late-target':
        w.page.set_preparation_context({**TOPIC_A, 'work_title': '弱电解质的电离平衡：新的完整课堂设计'}, target_changed=True)
        w.bridge.succeed(pending, run=True)
        assert not received and '备课已切换' in w.owner.status.text()
        # Also exercise a two-line explanatory status; this is display geometry,
        # not a new product message or another course operation.
        set_status(w.owner.status, 'attention', '备课已切换，请再次明确加入当前备课。教材原页与勾选笔记保持，重新确认后才能加入新备课。')
    else:
        w.facade.changed = True
        with pytest.raises(ValueError, match='变化'):
            pending.operation()
        w.bridge.fail(pending, '教材原书或章节笔记版本已变化，请重新预览后，再把所选知识卡加入当前备课。原页和勾选已保留。')
        assert not received and w.owner._reference is None and not w.owner.reading_cards_action.isEnabled()
    app.processEvents()
    app.processEvents()
    record_property('status_scenario', scenario)
    record_property('synthetic_pdf_viewport_height', w.reader.view.viewport().height())
    record_property('status_height', w.owner.status.height())
    assert w.owner.status.height() >= 2 * w.owner.status.fontMetrics().height()
    assert w.reader.view.viewport().height() >= 250, (w.reader.view.viewport().height(), w.owner.status.height())
    assert w.owner._reader is w.reader and w.reader._source == source and w.owner._checked == chosen
    assert w.reader.pages.currentData() == 70 and w.reader.view.pageNavigator().currentPage() == 69
