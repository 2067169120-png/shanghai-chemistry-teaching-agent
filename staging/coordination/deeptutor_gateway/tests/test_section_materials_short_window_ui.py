"""Short native section windows retain real material actions and ordered handoff.

The original book and crop ports use the existing owned synthetic source fixture.
Only Word/handout modal choices are controlled; no application/profile, Office or
network service is opened. Each Qt file is run in its own offscreen process.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from test_section_material_sources import empty_personal
from test_section_materials_no_card_ui import (
    Tasks, SECTION_READ, VERIFY_CROP, OPEN_WORD, OPEN_ALL_WORD, VERIFY_WORD,
    OPEN_HANDOUT, VERIFY_HANDOUT, COURSE_READ, real_crop, word_reader, lecture_reader,
    FORBIDDEN_KNOWLEDGE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench import section_materials_dialog as ui
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


def events(application):
    # Let Qt process ordinary layout/focus events. Do not force object deletion.
    for _ in range(8):
        application.processEvents()


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    install_ui_font(application)
    return application


@pytest.fixture
def case(app, monkeypatch, empty_personal):
    source = empty_personal
    guards = []
    def no_card(*_args):
        raise AssertionError('Section materials must not manufacture a knowledge card')
    monkeypatch.setattr(ui, 'import_knowledge_note', no_card)
    tasks = Tasks()
    dialog = ui.SectionMaterialsDialog(source.facade, tasks, source.context,
        guard=lambda: guards.append(True), course_resources=[], image_assets=[], batch_mode=True)
    dialog.setStyleSheet(WORKBENCH_STYLE)
    tasks.finish(SECTION_READ)
    value = SimpleNamespace(dialog=dialog, tasks=tasks, source=source, app=app,
                            monkeypatch=monkeypatch, guards=guards)
    yield value
    dialog.reject()
    dialog.deleteLater()
    events(app)


def populate_queue(case):
    dialog = case.dialog
    real_crop(case)
    dialog.image_list.setCurrentRow(0)
    dialog.choose_image()
    case.tasks.finish(VERIFY_CROP)
    word_reader(case)
    dialog.browse_questions()
    case.tasks.finish(OPEN_WORD)
    case.tasks.finish(OPEN_ALL_WORD)
    case.tasks.finish(VERIFY_WORD)
    assert [row['kind'] for row in dialog._queued_requests] == ['textbook_images', 'questions']


def fully_visible(widget, dialog):
    assert widget.isVisible(), widget.objectName() or type(widget).__name__
    assert widget.width() > 0 and widget.height() > 0
    ancestor = widget.parentWidget()
    while ancestor is not None:
        bounds = QRect(widget.mapTo(ancestor, QPoint(0, 0)), widget.size())
        assert ancestor.rect().contains(bounds), (type(widget).__name__, bounds,
                                                   type(ancestor).__name__, ancestor.rect())
        if ancestor is dialog:
            break
        ancestor = ancestor.parentWidget()
    assert ancestor is dialog


def reveal(case, widget):
    case.dialog.body_scroll.ensureWidgetVisible(widget, 0, 0)
    events(case.app)
    fully_visible(widget, case.dialog)


@pytest.mark.parametrize('size', [(420, 570), (430, 600)])
def test_short_nonempty_section_queue_reaches_all_three_material_pages_and_confirmation(case, size, tmp_path):
    dialog = case.dialog
    original = case.source.workspace.books()[0]['source_sha256']
    original_path = case.source.workspace.root / (original + '.pdf')
    assert sha256(original_path.read_bytes()).hexdigest() == original
    state = case.source.state.snapshot()
    context = deepcopy(dialog.context)
    browser = deepcopy(dialog._section_question_context)
    populate_queue(case)
    queued = deepcopy(dialog._queued_requests)
    dialog.show()
    dialog.resize(*size)
    events(case.app)
    if any(task.title == '预览教材图表原页' for task in case.tasks.pending):
        case.tasks.finish('预览教材图表原页')
        events(case.app)
    assert (dialog.width(), dialog.height()) == size
    assert dialog.tab_choice.isVisible() and not dialog.tabs.tabBar().isVisible()
    assert dialog.knowledge_list.currentRow() == -1
    assert dialog._card is None and dialog._selection is None
    assert dialog.page_preview.canvas.minimumHeight() == 120
    assert dialog.image_list.minimumHeight() == 70
    assert dialog.body_scroll.verticalScrollBar().maximum() > 0
    fixed = (dialog.queue_list, dialog.queue_count, dialog.queue_up, dialog.queue_down,
             dialog.queue_remove, dialog.queue_confirm, dialog.cancel_button, dialog.close_button)
    screens = []
    folder = Path(os.environ.get('SECTION_SHORT_UI_OWN', str(tmp_path))) / 'screenshots'
    folder.mkdir(parents=True, exist_ok=True)
    for index, actions in [(1, (dialog.crop_button,)), (2, (dialog.browse_questions_button,)),
                           (3, (dialog.browse_handouts_button, dialog.reload_handouts_button))]:
        dialog.tab_choice.setCurrentIndex(index)
        events(case.app)
        if index == 1:
            assert dialog.page_preview.receipt is not None
            assert dialog.page_preview.canvas.height() >= 120
            reveal(case, dialog.page_preview.canvas)
            reveal(case, dialog.image_list)
        if index == 3:
            case.tasks.finish(COURSE_READ)
            events(case.app)
        for button in actions:
            assert button.isEnabled(), button.text()
            reveal(case, button)
            button.setFocus(Qt.FocusReason.TabFocusReason)
            events(case.app)
            assert button.hasFocus()
            fully_visible(button, dialog)
        for widget in fixed:
            fully_visible(widget, dialog)
        filename = f'section-{size[0]}x{size[1]}-tab-{index}-action-visible.png'
        assert dialog.grab().save(str(folder / filename))
        screens.append(filename)
    dialog.tab_choice.setCurrentIndex(0)
    events(case.app)
    assert dialog.knowledge_empty.isVisible() and '本节尚无已保存知识讲解' in dialog.knowledge_empty.text()
    assert not dialog.knowledge_text.isVisible() and not dialog.add_knowledge.isVisible()
    reveal(case, dialog.knowledge_empty)
    filename = f'section-{size[0]}x{size[1]}-tab-0-no-card-message.png'
    assert dialog.grab().save(str(folder / filename))
    screens.append(filename)
    with pytest.raises(ValueError, match='知识'):
        dialog._base_request('knowledge')
    assert dialog._queued_requests == queued and dialog.context == context
    assert dialog._section_question_context == browser
    assert case.source.state.snapshot() == state
    assert case.source.workspace.books()[0]['source_sha256'] == original
    assert sha256(original_path.read_bytes()).hexdigest() == original
    assert all(not FORBIDDEN_KNOWLEDGE.intersection(row) for row in queued)
    metadata = {'synthetic_source': True, 'requested_size': list(size),
                'actual_size': [dialog.width(), dialog.height()], 'scope': 'section',
                'source_sha256': original, 'queue_kinds': [row['kind'] for row in queued],
                'screenshots': {name: sha256((folder / name).read_bytes()).hexdigest() for name in screens}}
    (folder / f'section-{size[0]}x{size[1]}-metadata.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fully_visible(dialog.queue_confirm, dialog)
    QTest.mouseClick(dialog.queue_confirm, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.requests == queued
    dialog._queued_requests[0]['crop_requests'][0]['caption'] = 'caller edit after confirmation'
    assert dialog.requests == queued


def test_ordinary_resize_preserves_user_split_ratios_and_exact_mixed_queue(case):
    dialog = case.dialog
    populate_queue(case)
    material = lecture_reader(case)
    dialog.browse_handouts()
    case.tasks.finish(OPEN_HANDOUT)
    case.tasks.finish(VERIFY_HANDOUT)
    assert len(dialog._queued_requests) == 5
    assert dialog._queued_requests[2]['material_request']['image_selections'][0] == material['image_selections'][0]
    queued = deepcopy(dialog._queued_requests)
    dialog.show()
    for size, next_size in [((1160, 820), (1200, 840)), ((500, 780), (520, 800))]:
        dialog.resize(*size)
        dialog.tabs.setCurrentIndex(1)
        events(case.app)
        dialog.split.setSizes([330, 650])
        dialog.image_split.setSizes([160, 270])
        events(case.app)
        ratios = [split.sizes()[0] / sum(split.sizes()) for split in (dialog.split, dialog.image_split)]
        dialog.resize(*next_size)
        events(case.app)
        after = [split.sizes()[0] / sum(split.sizes()) for split in (dialog.split, dialog.image_split)]
        assert all(abs(old - new) < 0.04 for old, new in zip(ratios, after)), (ratios, after)
        assert dialog._queued_requests == queued
    dialog.resize(420, 570)
    events(case.app)
    dialog.queue_list.setCurrentRow(4)
    QTest.mouseClick(dialog.queue_up, Qt.MouseButton.LeftButton)
    queued[3], queued[4] = queued[4], queued[3]
    assert dialog._queued_requests == queued
    QTest.mouseClick(dialog.queue_confirm, Qt.MouseButton.LeftButton)
    assert dialog.requests == queued


def test_short_window_cancelled_read_and_return_keep_existing_scope_guard(case):
    dialog = case.dialog
    populate_queue(case)
    queued = deepcopy(dialog._queued_requests)
    dialog.show()
    dialog.resize(420, 570)
    events(case.app)
    reveal(case, dialog.reload_section_button)
    QTest.mouseClick(dialog.reload_section_button, Qt.MouseButton.LeftButton)
    pending = case.tasks.take(SECTION_READ)
    value = pending.operation()
    assert dialog.cancel_button.isEnabled() and not dialog.queue_confirm.isEnabled()
    fully_visible(dialog.cancel_button, dialog)
    QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    pending.success(value)
    assert not dialog._section_loaded and dialog._queued_requests == queued
    assert dialog._card is None and dialog._selection is None
    assert not dialog.requests and case.guards
    fully_visible(dialog.close_button, dialog)
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.requests and not dialog._queued_requests
