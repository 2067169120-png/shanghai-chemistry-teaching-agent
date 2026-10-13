"""Destination-only UI changes use memory fixtures and never touch a profile.

The PDF is synthetic and held in QBuffer. It tests page preservation, not the
appearance or chemistry of any actual textbook.
"""
from copy import deepcopy
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from PySide6.QtCore import QByteArray, QIODevice, QPointF, QRect, QSignalBlocker, Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QHBoxLayout, QTreeWidgetItem, QVBoxLayout, QWidget

from test_personal_textbook_ui import Bridge, complete
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE


TOPIC_A = {'topic': '弱电解质的电离平衡', 'lesson_timing': '2课时×40分钟'}
TOPIC_B = {'topic': '盐类水解', 'lesson_timing': '1课时×45分钟'}
CONCEPT = {'concept_id': 'C1', 'revision': 'a' * 64, 'volume_id': 'V1',
           'section_key': 'S1', 'title': '合成知识', 'statement': '条件完整的合成知识。', 'pdf_pages': [1, 2]}
BOOK = {'source_id': 'BOOK-' + 'b' * 64, 'revision': 'b' * 64, 'source_name': '合成个人教材',
        'page_count': 3, 'section_count': 1, 'note_count': 1}
SECTION = {'section_id': 'SEC1', 'title': '合成章节', 'pdf_start': 1, 'pdf_end': 3,
           'revision': 'c' * 64, 'range_confirmed': True}
NOTE = {'note_id': 'N1', 'revision': 'd' * 64, 'section_id': 'SEC1', 'title': '合成笔记',
        'text': '依据完整的合成笔记。', 'pdf_page': 2}


class MemoryFacade:
    def __init__(self):
        self.paths = SimpleNamespace(workspace_root=Path('memory-only-no-file-access'))
        self.calls = []
        self.changed = False

    def preparation_source_reference(self, *args, **kwargs):
        self.calls.append(('builtin', deepcopy(args), deepcopy(kwargs)))
        return {'materials': '已验证的内存参考' + (' changed source' if self.changed else '')}

    def personal_textbook_reference(self, selection, revision, notes):
        assert selection == {key: BOOK[key] for key in ('source_id', 'revision')}
        assert revision == 'e' * 64 and notes == [{'note_id': 'N1', 'revision': 'd' * 64}]
        self.calls.append(('personal', deepcopy(selection), revision, deepcopy(notes)))
        return {'materials': '已验证的内存参考' + (' changed source' if self.changed else '')}

    def textbook_reading_reference(self, notes, **kwargs):
        self.calls.append(('reading-notes', deepcopy(notes), deepcopy(kwargs)))
        return {'materials': '已验证的内存参考' + (' changed source' if self.changed else '')}


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=['builtin', 'personal'])
def workspace(app, request):
    facade, bridge = MemoryFacade(), Bridge()
    page = TextbookBookshelfPage(facade, bridge)
    page.resize(1000, 950)
    page.show()
    if request.param == 'builtin':
        with QSignalBlocker(page.book):
            page.book.addItem('合成教材', 'V1')
        page._snapshot = {'concepts': [deepcopy(CONCEPT)], 'assets': [], 'reading_notes': [],
                          'sections': [{'section_key': 'S1', 'title': '教材节名不能猜作课题',
                                        'revision': 'f' * 64, 'pdf_pages': [1, 2, 3]}], 'local_books': []}
        with QSignalBlocker(page.tree):
            item = QTreeWidgetItem(['教材节名不能猜作课题'])
            item.setData(0, Qt.ItemDataRole.UserRole, 'S1')
            page.tree.addTopLevelItem(item)
            page.tree.setCurrentItem(item)
        page._section_changed()
        owner = page
        owner.knowledge_list.item(0).setCheckState(Qt.CheckState.Checked)
    else:
        with QSignalBlocker(page.book):
            page.book.addItem(BOOK['source_name'], BOOK['source_id'])
        page._snapshot = {'local_books': [deepcopy(BOOK)]}
        page._personal_mode(True)
        owner = page.personal
        owner._loaded({'book': deepcopy(BOOK), 'study_revision': 'e' * 64,
                       'sections': [deepcopy(SECTION)], 'notes': [deepcopy(NOTE)]})
        owner.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    yield SimpleNamespace(page=page, owner=owner, bridge=bridge, facade=facade, mode=request.param)
    page.close()
    page.deleteLater()
    app.processEvents()


def preview(w):
    w.owner.preview_button.click()
    complete(w.bridge, '核对教材备课参考' if w.mode == 'builtin' else '核对个人教材备课参考')


def confirmation(w, *, cards=False):
    if cards:
        w.owner.cards_button.click()
    else:
        w.owner.confirm_button.click()
    return w.bridge.take('核对教材备课参考' if w.mode == 'builtin' else '核对个人教材备课参考')


def card_backend(monkeypatch, w):
    from integrations.deeptutor_shchem_v1 import desktop_composition_sources, desktop_textbook_concept_cards
    calls = []
    monkeypatch.setattr(desktop_textbook_concept_cards, 'concept_options',
                        lambda _root: [{**CONCEPT, 'selection': {'kind': 'textbook', 'concept': deepcopy(CONCEPT)}}])
    def compile_cards(facade, selections):
        assert facade is w.facade
        calls.append(deepcopy(selections))
        return {'resources': [{'resource_id': 'MEM-CARD'}], 'image_assets': []}
    monkeypatch.setattr(desktop_composition_sources, 'import_knowledge_cards', compile_cards)
    return calls


def add_memory_pdf(monkeypatch, w, app, *, full_controls=False):
    # Use the real embedded reader and real native page navigator, with no source loader.
    monkeypatch.setattr(TextbookSourceDialog, '_load', lambda _self: None)
    if full_controls:
        positions = SimpleNamespace(latest=lambda: None)
        w.facade.textbook_reading_positions = lambda: positions
        w.page._positions = positions
    reader = TextbookSourceDialog(w.facade, {'source_id': BOOK['source_id'], 'revision': BOOK['revision']},
                                  w.owner.reading, tasks=w.bridge, reading_mode='book', embedded=True,
                                  remember_position=full_controls, allow_current_page_crop=full_controls)
    reader.document.statusChanged.disconnect(reader._document_status)
    writer, buffer = PdfWriter(), io.BytesIO()
    for _number in range(3):
        writer.add_blank_page(600, 800)
    writer.write(buffer)
    pdf = buffer.getvalue()
    reader._source = {'pdf_pages': [1, 2, 3], 'source_sha256': BOOK['revision'], 'revision': BOOK['revision']}
    reader.buffer.setData(QByteArray(pdf))
    reader.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader.document.load(reader.buffer)
    reader._loaded_pages = True
    with QSignalBlocker(reader.pages):
        for number in (1, 2, 3):
            reader.pages.addItem(f'PDF第{number}页', number)
    reader.view.pageNavigator().jump(1, QPointF())
    w.owner.reading_prompt.hide()
    if w.mode == 'builtin':
        w.owner.reading_layout.addWidget(reader, 1)
    else:
        w.owner.reading_box.addWidget(reader, 1)
    w.owner._reader = reader
    reader.show()
    app.processEvents()
    assert reader.document.pageCount() == 3
    assert reader.pages.currentData() == 2 and reader.view.pageNavigator().currentPage() == 1
    return reader


def reading_state(w):
    owner = w.owner
    return (deepcopy(owner._checked), owner.tree.currentItem(), owner._reader,
            deepcopy(getattr(owner, '_asset_checked', getattr(owner, '_crop_pages', []))),
            deepcopy(getattr(owner, '_note_checked', {})), deepcopy(getattr(owner, '_excerpts', {})),
            deepcopy(owner._reference), owner.reference_preview.toPlainText(), owner.reference_details.toPlainText())


def test_context_hidden_without_main_window_and_uses_original_timing(workspace):
    w = workspace
    bar = w.page.preparation_context_bar
    assert bar.isHidden() and not w.facade.calls and not w.bridge.pending
    payload = {**TOPIC_A, 'topic': '<课题>电离平衡', 'timing': {'periods': 99}}
    original = deepcopy(payload)
    before = reading_state(w)
    w.page.set_preparation_context(payload, dirty=True)
    assert bar.topic_label.text() == '正在接收素材：<课题>电离平衡'
    assert bar.topic_label.textFormat() == Qt.TextFormat.PlainText
    assert bar.detail_label.text() == '课时：2课时×40分钟 · 未保存修改'
    assert reading_state(w) == before and payload == original and not w.bridge.pending and not w.facade.calls
    w.page.set_preparation_context({'topic': '', 'lesson_timing': ''})
    assert bar.topic_label.text() == '正在接收素材：未命名备课'
    assert '教材节名' not in bar.topic_label.text() and bar.detail_label.text() == '课时：未设置 · 无未保存修改'
    w.page.set_preparation_context(None)
    assert bar.isHidden() and reading_state(w) == before


def test_context_buttons_emit_once_and_cancelled_open_new_leave_selection(workspace):
    w = workspace
    w.page.set_preparation_context(TOPIC_A, dirty=True)
    preview(w)
    before = reading_state(w)
    received = []
    for name in ('edit', 'open', 'new'):
        getattr(w.page, f'preparation_{name}_requested').connect(lambda action=name: received.append(action))
        getattr(w.page.preparation_context_bar, f'{name}_button').click()
    # A cancelled destination picker provides no new context; emitted requests are inert here.
    assert received == ['edit', 'open', 'new']
    assert reading_state(w) == before and not w.bridge.pending


def test_same_topic_distinct_saved_work_titles_identify_actual_destination(workspace):
    w = workspace
    old = {**TOPIC_A, 'work_title': '<旧作品>电离平衡复习'}
    new = {**TOPIC_A, 'work_title': '<完整作品>教材与完整讲义'}
    w.page.set_preparation_context(old)
    preview(w)
    w.owner.reference_appended()
    before = reading_state(w)
    bar = w.page.preparation_context_bar
    assert bar.title.text() == '正在接收素材：<旧作品>电离平衡复习'
    assert bar.detail.text() == '课题：弱电解质的电离平衡 · 课时：2课时×40分钟 · 无未保存修改'
    w.page.set_preparation_context(new, target_changed=True)
    assert bar.title.text() == '正在接收素材：<完整作品>教材与完整讲义'
    assert bar.detail.textFormat() == Qt.TextFormat.PlainText and bar.title.textFormat() == Qt.TextFormat.PlainText
    assert bar.detail.text() == '课题：弱电解质的电离平衡 · 课时：2课时×40分钟 · 无未保存修改'
    assert w.owner.confirm_button.isEnabled() and reading_state(w) == before
    assert old == {**TOPIC_A, 'work_title': '<旧作品>电离平衡复习'}
    assert new == {**TOPIC_A, 'work_title': '<完整作品>教材与完整讲义'}


def test_busy_disables_destination_and_confirm_without_mutating_preview(workspace):
    w = workspace
    w.page.set_preparation_context(TOPIC_A)
    preview(w)
    before = reading_state(w)
    calls = deepcopy(w.facade.calls)
    received = []
    w.page.preparation_new_requested.connect(lambda: received.append('new'))
    w.page.set_preparation_context(TOPIC_A, busy=True)
    for name in ('edit', 'open', 'new'):
        button = getattr(w.page.preparation_context_bar, f'{name}_button')
        assert not button.isEnabled()
        button.click()
    w.owner.confirm_button.click()
    w.owner.cards_button.click()
    assert not w.owner.confirm_button.isEnabled() and not w.owner.cards_button.isEnabled()
    assert not received and not w.bridge.pending and w.facade.calls == calls and reading_state(w) == before
    w.page.set_preparation_context(TOPIC_A, busy=False)
    assert w.owner.confirm_button.isEnabled() and w.owner.cards_button.isEnabled() and reading_state(w) == before


def test_switch_target_resets_receipts_preserving_pdf_source_and_all_choices(workspace, app, monkeypatch):
    w = workspace
    w.page.set_preparation_context(TOPIC_A)
    if w.mode == 'builtin':
        w.owner._snapshot['reading_notes'] = [
            {'volume_id': 'V1', 'section_key': 'S1', 'note_id': 'READ1', 'revision': '1' * 64,
             'directory_revision': '2' * 64, 'title': '合成研读提示', 'summary': '原条件',
             'classroom_check': '观察', 'limitation': '本节范围', 'pdf_pages': [2], 'printed_pages': [1]}]
        w.owner._snapshot['assets'] = [
            {'visual_asset_id': 'FIG1', 'revision': '3' * 64, 'volume_id': 'V1', 'section_key': 'S1',
             'label': '合成图', 'description': '完整图', 'pdf_page': 2, 'printed_page': 1}]
        w.owner._section_changed()
        w.owner.note_list.item(0).setCheckState(Qt.CheckState.Checked)
        w.owner.asset_list.item(0).setCheckState(Qt.CheckState.Checked)
        w.owner._excerpts['C1'] = {'revision': CONCEPT['revision'], 'text': '完整条件的手工摘录'}
    else:
        w.owner.crop_page.setValue(3)
        w.owner.queue_page_button.click()
    preview(w)
    reader = add_memory_pdf(monkeypatch, w, app)
    w.owner.reference_appended()
    w.owner.cards_appended()
    before = reading_state(w)
    binding = deepcopy(reader._source)
    w.page.set_preparation_context({**TOPIC_A, 'topic': '同目标改名'}, dirty=True)
    assert not w.owner.confirm_button.isEnabled() and not w.owner.cards_button.isEnabled()
    w.page.set_preparation_context(TOPIC_B, dirty=True, target_changed=True)
    app.processEvents()
    assert w.owner.confirm_button.isEnabled() and w.owner.cards_button.isEnabled()
    assert w.owner.confirm_button.text() == '加入当前备课' and w.owner.cards_button.text() == '知识卡加入课件…'
    assert reading_state(w) == before and reader._source == binding
    assert reader.pages.currentData() == 2 and reader.view.pageNavigator().currentPage() == 1
    assert not w.bridge.pending and len(w.facade.calls) == 1


@pytest.mark.parametrize('cards', [False, True])
def test_inflight_confirm_never_sends_a_to_b_and_explicit_retry_works(workspace, monkeypatch, cards):
    w = workspace
    card_calls = card_backend(monkeypatch, w)
    w.page.set_preparation_context(TOPIC_A)
    preview(w)
    received = []
    signal = w.page.preparation_cards_requested if cards else (
        w.page.preparation_reference_requested if w.mode == 'builtin' else w.owner.reference_requested)
    signal.connect(received.append)
    pending = confirmation(w, cards=cards)
    before = reading_state(w)
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    w.bridge.succeed(pending, run=True)
    assert not received and reading_state(w) == before
    assert '备课已切换' in w.owner.status.text()
    assert w.owner.confirm_button.isEnabled() and w.owner.cards_button.isEnabled()
    retry = confirmation(w, cards=cards)
    w.bridge.succeed(retry, run=True)
    assert len(received) == 1
    if cards:
        assert len(card_calls) == 2


def test_inflight_ordinary_preview_can_complete_for_new_target(workspace):
    w = workspace
    w.page.set_preparation_context(TOPIC_A)
    w.owner.preview_button.click()
    pending = w.bridge.take('核对教材备课参考' if w.mode == 'builtin' else '核对个人教材备课参考')
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    w.bridge.succeed(pending, run=True)
    assert w.owner._reference is not None and w.owner.confirm_button.isEnabled()
    assert w.owner._checked and w.owner.tree.currentItem() is not None


def test_old_confirmation_failure_after_target_switch_preserves_preview(workspace):
    w = workspace
    w.page.set_preparation_context(TOPIC_A)
    preview(w)
    pending = confirmation(w)
    before = reading_state(w)
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    w.bridge.fail(pending, '旧目标确认失败')
    assert reading_state(w) == before and w.owner.confirm_button.isEnabled()
    assert '备课已切换' in w.owner.status.text()


def test_target_reset_still_requires_source_version_recheck(workspace):
    w = workspace
    w.page.set_preparation_context(TOPIC_A)
    preview(w)
    w.owner.reference_appended()
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    w.facade.changed = True
    received = []
    signal = w.page.preparation_reference_requested if w.mode == 'builtin' else w.owner.reference_requested
    signal.connect(received.append)
    pending = confirmation(w)
    w.bridge.succeed(pending, run=True)
    assert not received and w.owner._reference is None and not w.owner.confirm_button.isEnabled()
    assert '变化' in w.owner.status.text() and w.owner._checked


def test_target_change_does_not_enable_unpreviewed_material(workspace):
    w = workspace
    before = reading_state(w)
    w.page.set_preparation_context(TOPIC_B, target_changed=True)
    assert not w.owner.confirm_button.isEnabled() and not w.owner.cards_button.isEnabled()
    assert reading_state(w) == before and not w.facade.calls and not w.bridge.pending


@pytest.mark.parametrize('width', [360, 420])
def test_narrow_context_remains_readable_and_buttons_reachable(workspace, app, width):
    w = workspace
    w.page.setStyleSheet(WORKBENCH_STYLE)
    w.page.set_preparation_context({'topic': '<完整课题>弱电解质的电离平衡及盐类水解' * 3,
                                    'lesson_timing': '2课时×40分钟'}, dirty=True)
    w.page.resize(width, 1050)
    app.processEvents()
    app.processEvents()
    bar = w.page.preparation_context_bar
    assert w.page.width() == width and bar.isVisible()
    for control in (bar, bar.topic_label, bar.detail_label, bar.edit_button, bar.open_button, bar.new_button):
        assert control.isVisible()
        geometry = QRect(control.mapTo(w.page, control.rect().topLeft()), control.size())
        assert w.page.rect().contains(geometry), (control.objectName(), geometry)
    for button in (bar.edit_button, bar.open_button, bar.new_button):
        assert button.width() >= button.sizeHint().width(), (button.text(), button.width(), button.sizeHint().width())
    assert bar.topic_label.wordWrap() and bar.detail_label.wordWrap()
    assert bar.topic_label.height() >= bar.topic_label.fontMetrics().height() * 2
    assert not w.facade.calls and not w.bridge.pending


def test_wide_context_keeps_labels_left_and_actions_in_same_row(workspace, app):
    w = workspace
    w.page.setStyleSheet(WORKBENCH_STYLE)
    w.page.set_preparation_context(TOPIC_A, dirty=True)
    w.page.resize(1152, 900)
    app.processEvents()
    app.processEvents()
    bar = w.page.preparation_context_bar
    assert bar.body.direction() == QBoxLayout.Direction.LeftToRight
    title_bottom = bar.title.mapTo(bar, bar.title.rect().bottomRight()).y()
    assert bar.edit_button.mapTo(bar, bar.edit_button.rect().topLeft()).y() < title_bottom
    assert bar.actions_panel.geometry().left() > bar.text_panel.geometry().right()
    tops = {button.mapTo(bar, button.rect().topLeft()).y()
            for button in (bar.edit_button, bar.open_button, bar.new_button)}
    assert len(tops) == 1 and bar.height() <= 60
    for button in (bar.edit_button, bar.open_button, bar.new_button):
        assert button.width() >= button.sizeHint().width()
    assert not w.bridge.pending and not w.facade.calls


@pytest.mark.parametrize('workspace', ['personal'], indirect=True)
@pytest.mark.parametrize('main_window', [True, False], ids=['1366x960-host', '540x900-page'])
def test_context_visible_personal_reading_space_and_return_restore(workspace, app, monkeypatch, main_window, record_property):
    w = workspace
    w.page.setStyleSheet(WORKBENCH_STYLE)
    w.page.set_preparation_context(TOPIC_A, dirty=True)
    preview(w)
    host = None
    if main_window:
        # Match the main window's available page rectangle without constructing
        # a MainWindow or loading its state/source services.
        host = QWidget()
        body = QHBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = QWidget()
        sidebar.setFixedWidth(214)
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
        host.resize(1366, 960)
        host.show()
        w.page.show()
    else:
        w.page.resize(540, 900)
    app.processEvents()
    reader = add_memory_pdf(monkeypatch, w, app, full_controls=True)
    # Leave reference selection and source bindings in place while reading.
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    app.processEvents()
    before = reading_state(w)
    source = deepcopy(reader._source)
    assert w.page.preparation_context_bar.isVisible() and w.page.book.isVisible()
    assert not w.owner.introduction.isVisible()
    assert not w.page.introduction.isVisible() and not w.page.summary.isVisible() and not w.page.resume_bar.isVisible()
    if main_window:
        assert w.page.width() == 1152 and w.page.height() == 863
        assert w.page.preparation_context_bar.body.direction() == QBoxLayout.Direction.LeftToRight
        assert w.owner.tree.isVisible()
    assert reader.current_page_crop_button.isVisible() and reader.reading_memory.isVisible()
    # This slice's 300px regression belongs to the existing built-in notes
    # reader. Personal 540px reading keeps its existing operation structure.
    required = 250 if main_window else 150
    record_property('synthetic_pdf_viewport_height', reader.view.viewport().height())
    record_property('page_width', w.page.width())
    record_property('page_height', w.page.height())
    record_property('context_height', w.page.preparation_context_bar.height())
    assert reader.view.viewport().height() >= required, (reader.view.viewport().size(), w.page.size())
    reading_controls = (w.owner.preview_button, w.owner.clear_button) if main_window else (w.owner.reading_actions_button,)
    for control in (w.page.book, w.page.preparation_context_bar, w.owner.confirm_button, *reading_controls):
        geometry = QRect(control.mapTo(w.page, control.rect().topLeft()), control.size())
        assert control.isVisible() and w.page.rect().contains(geometry), (control.objectName(), geometry)
    if not main_window:
        assert w.owner.reading_preview_action.isEnabled() and w.owner.reading_clear_action.isEnabled()
    w.owner.tabs.setCurrentIndex(3)
    app.processEvents()
    assert w.owner.introduction.isVisible()
    assert w.page.introduction.isVisible() and w.page.summary.isVisible() and w.page.resume_bar.isVisible()
    assert reading_state(w) == before and reader._source == source
    assert reader.pages.currentData() == 2 and reader.view.pageNavigator().currentPage() == 1
    w.owner.tabs.setCurrentWidget(w.owner.reading)
    app.processEvents()
    assert reader.view.viewport().height() >= required and reading_state(w) == before
    assert not w.bridge.pending
    if host is not None:
        # The fixture still owns the page and closes it after this test.
        w.page.setParent(None)
        host.close()
        host.deleteLater()
