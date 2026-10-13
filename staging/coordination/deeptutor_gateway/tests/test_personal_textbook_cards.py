"""Version-bound personal notes, native handoff and portable deduplication."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from test_personal_textbook_ui import app, panel, enter_chapter, enter_note, complete, Bridge, facade_for
from test_personal_textbook_study import book_fixture
from test_desktop_studio_ui import window
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from integrations.deeptutor_shchem_v1.desktop_lesson_composition import block, resource, validate_resource
from integrations.deeptutor_shchem_v1.desktop_composition_sources import merge_knowledge_result


def prepared(panel):
    value, bridge, f = panel
    enter_chapter(value, bridge); enter_note(value, bridge)
    value.facade.textbook_bookshelf = lambda: {'concepts': [], 'sections': [], 'reading_notes': []}
    value.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    return value, bridge, f


def preview(value, bridge):
    value.preview_button.click(); complete(bridge, '核对个人教材备课参考')


def test_preview_required_and_real_note_card_preserves_source_and_flags(panel):
    value, bridge, f = prepared(panel); received = []
    value.cards_requested.connect(received.append)
    assert not value.cards_button.isEnabled()
    value._compile(True, cards=True)
    assert not bridge.pending and not received
    preview(value, bridge)
    before = f.study.book_study(f.selection)
    value.cards_button.click(); complete(bridge, '核对个人教材备课参考')
    card = received[0]['resources'][0]
    assert card['original']['kind'] == 'knowledge_note'
    assert card['original']['evidence']['source_sha256'] == f.selection['revision']
    assert card['original']['evidence']['source_revision'] == before['notes'][0]['revision']
    assert card['original']['evidence']['pdf_pages'] == [2]
    assert card['original']['evidence']['printed_pages'] == 'unknown'
    assert card['blocks'][0]['value'] == before['notes'][0]['text']
    assert card['human_reviewed'] is False and card['publication_allowed'] is False
    assert f.study.book_study(f.selection) == before and f.source.read_bytes() == f.original


def test_changed_selection_invalidates_preview_and_cards(panel):
    value, bridge, _ = prepared(panel); preview(value, bridge)
    assert value.cards_button.isEnabled()
    value.note_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert value._reference is None and not value.cards_button.isEnabled()


def test_note_change_after_preview_refuses_entire_handoff(panel):
    value, bridge, f = prepared(panel); preview(value, bridge); received = []
    value.cards_requested.connect(received.append)
    old = f.study.book_study(f.selection); note = old['notes'][0]
    f.study.save_note(f.selection, old['study_revision'], section_id=note['section_id'],
        title=note['title'], text=note['text']+'已修改。', pdf_page=note['pdf_page'],
        teacher_confirmed=True, note_id=note['note_id'])
    value.cards_button.click(); task = bridge.take('核对个人教材备课参考')
    with pytest.raises(Exception) as exc:
        task.operation()
    bridge.fail(task, str(exc.value)); bridge.task_finished.emit(task.task_id)
    assert not received and value._reference is None and not value.cards_button.isEnabled()


def test_changed_reference_rejected_before_card_import(panel, monkeypatch):
    value, bridge, _ = prepared(panel); preview(value, bridge)
    expected = deepcopy(value._reference); expected['materials'] += '变化'
    monkeypatch.setattr(value.facade, 'personal_textbook_reference', lambda *args: expected)
    import integrations.deeptutor_shchem_v1.desktop_composition_sources as sources
    calls = []
    monkeypatch.setattr(sources, 'import_knowledge_cards', lambda *args: calls.append(args))
    value.cards_button.click(); task = bridge.take('核对个人教材备课参考')
    with pytest.raises(ValueError, match='重新预览') as exc:
        task.operation()
    bridge.fail(task, str(exc.value)); bridge.task_finished.emit(task.task_id)
    assert not calls and value._reference is None


def test_close_ignores_late_card_callback(panel):
    value, bridge, _ = prepared(panel); preview(value, bridge); received = []
    value.cards_requested.connect(received.append)
    value.cards_button.click(); task = bridge.take('核对个人教材备课参考')
    result = task.operation(); value.close()
    bridge.succeed(task, result); bridge.task_finished.emit(task.task_id)
    assert not received and task.task_id in bridge.cancelled


def test_acknowledged_cards_stay_disabled_after_task_finishes(panel):
    value, bridge, _ = prepared(panel); preview(value, bridge)
    value.cards_requested.connect(lambda result: value.cards_appended())
    value.cards_button.click(); complete(bridge, '核对个人教材备课参考')
    assert value.cards_button.text() == '本次知识卡已加入'
    assert not value.cards_button.isEnabled() and value.confirm_button.isEnabled()
    value._compile(True, cards=True); assert not bridge.pending


def test_rejected_handoff_retains_preview_for_retry(panel):
    value, bridge, _ = prepared(panel); preview(value, bridge); reference = deepcopy(value._reference)
    value.cards_requested.connect(lambda result: value.cards_rejected())
    value.cards_button.click(); complete(bridge, '核对个人教材备课参考')
    assert value._reference == reference and value.cards_button.isEnabled()


@pytest.mark.parametrize('ack', ['cards_appended', 'cards_rejected'])
def test_personal_ack_routes_after_bookshelf_is_hidden(panel, app, monkeypatch, ack):
    _, _, f = panel; page = TextbookBookshelfPage(facade_for(f), Bridge())
    calls = []; monkeypatch.setattr(page.personal, ack, lambda: calls.append('personal'))
    page.show(); page.personal.show(); app.processEvents()
    page.personal.cards_requested.emit({'resources': [knowledge()]})
    page.hide(); app.processEvents(); assert not page.personal.isVisible()
    getattr(page, ack)(); assert calls == ['personal'] and page._card_sender is None
    page._deliver_cards({'resources': [knowledge()]}); getattr(page, ack)()
    assert calls == ['personal']
    page.close(); page.deleteLater(); app.processEvents()


def test_narrow_actions_are_accessible(panel, app):
    value, bridge, _ = prepared(panel); preview(value, bridge)
    value.resize(360, 900); app.processEvents()
    assert value.width() == 360 and value.cards_button.isVisible()
    assert value.actions.direction() == value.actions.Direction.TopToBottom
    for button in (value.preview_button, value.confirm_button, value.cards_button, value.clear_button):
        left = button.mapTo(value, button.rect().topLeft()).x()
        assert left >= 0 and left + button.width() <= value.width()


def knowledge(path='original/book.pdf', revision='b'*64, pages=None, record='note-known'):
    return resource('knowledge_note', '合成知识卡', [block('text', 'knowledge', '原笔记正文。')],
        {'source_path': path, 'source_sha256': 'a'*64, 'source_revision': revision,
         'record_id': record, 'pdf_pages': pages or [2], 'printed_pages': 'unknown'})


def test_portable_duplicate_preserves_edited_existing_work_copy():
    old = knowledge(); old['locked'] = False; old['title'] = '教师已改标题'
    old['blocks'][0]['value'] = '教师已改正文'; validate_resource(old)
    existing = [old]; before = deepcopy(existing)
    merged = merge_knowledge_result({'resources': [knowledge('restored/book.pdf')]}, existing)
    assert merged == {'resources': []} and existing == before


def test_missing_classroom_picture_returns_retryable_error_before_dialog(window, monkeypatch):
    win, _ = window; page = win.preparation_page; before = deepcopy(page._payload())
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
    called = []
    monkeypatch.setattr(page.image_assets_widget, 'assets', lambda: [{'asset_id': 'missing'}])
    def missing(asset):
        raise DesktopFacadeError('preparation_image_missing', '本地图片副本已丢失，请重新选择图片。')
    monkeypatch.setattr(page.facade, 'preparation_image_bytes', missing)
    monkeypatch.setattr(LessonDesignDialog, '__init__', lambda *args: called.append(args))
    assert page.import_textbook_cards({'resources': [knowledge()]}) is False
    assert not called and not page.studio_busy() and '图片副本已丢失' in page.status.text()
    monkeypatch.undo()
    assert page._payload() == before


@pytest.mark.parametrize('incoming', [knowledge(revision='c'*64), knowledge(pages=[3]), knowledge(record='other')])
def test_distinct_note_version_page_or_identity_is_kept(incoming):
    assert merge_knowledge_result({'resources': [incoming]}, [knowledge()]) == {'resources': [incoming]}


def test_unknown_identity_cannot_deduplicate_relocated_source_and_batch_deduplicates():
    first, other = knowledge(record='unknown'), knowledge('restored/book.pdf', record='unknown')
    assert merge_knowledge_result({'resources': [other]}, [first])['resources'] == [other]
    known, relocated = knowledge(), knowledge('restored/book.pdf')
    assert merge_knowledge_result({'resources': [known, relocated]}, [])['resources'] == [known]


@pytest.mark.parametrize('result', [None, {'resources': []}, {'resources': 'bad'},
    {'resources': [knowledge()]*31}, {'resources': [knowledge()], 'extra': True},
    {'resources': [knowledge(), resource('personal_text', '手工文字', [block('text', 'personal', '正文')],
        {'source_path': 'teacher', 'source_sha256': 'unknown', 'source_revision': 'unknown'})]}])
def test_invalid_or_mixed_batch_is_rejected_atomically(result):
    old = [knowledge()]; before = deepcopy(old)
    with pytest.raises((ValueError, TypeError)):
        merge_knowledge_result(result, old)
    assert old == before
