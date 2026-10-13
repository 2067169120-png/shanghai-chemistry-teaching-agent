"""Selected original knowledge can manage examples without selecting materials."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QDialog

from test_textbook_bookshelf_ui import (app as app, page as page, shelf_fixture as shelf_fixture,
    section_fixture as section_fixture, asset_fixture as asset_fixture, leaf)
from test_personal_textbook_ui import (panel as panel, book_fixture as book_fixture,
    enter_chapter, enter_note, complete, Bridge)
from test_handout_question_handoff import scoped_lecture as scoped_lecture, desktop_paths as desktop_paths
from test_note_page_crops import notes as notes
from test_personal_textbook_knowledge import native_fixture as native_fixture
from test_personal_textbook_study import chapter, note as personal_note
from test_textbook_bookshelf import disk_bytes
from test_knowledge_question_links_ui import Links
from integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog import KnowledgeQuestionLinksDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_bookshelf_page import TextbookBookshelfPage
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_panel import PersonalTextbookPanel


LABEL = '读取所选知识的关联入口'
PERSONAL_LABEL = '读取已保存笔记的关联入口'


class Contexts:
    def __init__(self, facade):
        self.facade = facade
        self.calls = []

    def knowledge_context(self, selection):
        self.calls.append(deepcopy(selection))
        return {'endpoint': {'kind': selection.get('kind', 'reading_note'), 'selection': deepcopy(selection),
                             'id': 'exact-saved-endpoint', 'source_revision': 'a'*64},
                'card': {}, 'browser_context': {}}

    def choices(self, *_args, **_kwargs):
        pytest.fail('an entry must not enumerate the knowledge and Word libraries')


@pytest.fixture
def route(monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_knowledge_question_links as service_module
    import integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog as dialog_module
    services, dialogs = [], []
    action = {'accepted': False, 'request': None, 'inside': None}

    def service(facade):
        value = Contexts(facade); services.append(value); return value

    class Dialog:
        def __init__(self, facade, tasks, parent, **kwargs):
            self.facade, self.tasks, self.parent, self.kwargs = facade, tasks, parent, kwargs
            self.course_request = None
            self.deleted = False
            dialogs.append(self)

        def exec(self):
            self.kwargs['guard']()
            if action['inside']:
                action['inside'](self)
            self.course_request = (None if action['request'] is None else {
                key: value if key == 'scoped_facade' else deepcopy(value)
                for key, value in action['request'].items()})
            return QDialog.DialogCode.Accepted if action['accepted'] else QDialog.DialogCode.Rejected

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(service_module, 'KnowledgeQuestionLinks', service)
    monkeypatch.setattr(dialog_module, 'KnowledgeQuestionLinksDialog', Dialog)
    return services, dialogs, action


def browsing_state(page):
    return (deepcopy(page._checked), deepcopy(page._note_checked), deepcopy(page._asset_checked),
            deepcopy(page._excerpts), deepcopy(page._reference), page._reader,
            page.knowledge_detail.toPlainText(), page.note_detail.toPlainText())


def add_reading_note(page):
    section = page._section()
    row = {'note_id': 'SYNTHETIC-READING-NOTE', 'section_key': section['section_key'],
           'volume_id': page.book.currentData(), 'title': '合成研读提示', 'summary': '所选的完整摘要',
           'classroom_check': '课堂比较', 'limitation': '保存版本', 'pdf_pages': section['pdf_pages'][:1],
           'printed_pages': section['printed_pages'][:1], 'revision': 'b'*64, 'directory_revision': 'c'*64}
    page._snapshot['reading_notes'] = [row]
    page._section_changed()
    return row


@pytest.mark.parametrize('kind', ['concept', 'readingnote'])
def test_read_and_manage_selected_registered_knowledge_preserves_materials(page, route, kind):
    value, bridge, f = page
    services, dialogs, _action = route
    if kind == 'readingnote':
        add_reading_note(value)
    value.knowledge_list.item(0).setCheckState(Qt.CheckState.Checked)
    before, disk = browsing_state(value), disk_bytes(f.root)
    received = []; value.knowledge_question_requested.connect(received.append)
    button = value.concept_links_button if kind == 'concept' else value.note_links_button
    assert button.isEnabled()
    button.click()
    assert not value.concept_links_button.isEnabled() and not value.note_links_button.isEnabled()
    assert not dialogs and not received
    bridge.succeed(bridge.take(LABEL), run=True)
    assert len(dialogs) == len(services) == 1
    dialog, selected = dialogs[0], services[0].calls[0]
    assert dialog.kwargs['knowledge_resource']['selection'] == selected
    assert dialog.kwargs['allow_course_add'] and dialog.deleted
    if kind == 'concept':
        assert selected['kind'] == 'textbook_concept'
        assert selected['concept_id'] == f.row['concept_id'] and selected['revision'] == value.knowledge_list.item(0).data(Qt.ItemDataRole.UserRole)['revision']
        assert selected['directory_revision'] and selected['location']['scope_revision']
    else:
        assert selected == {key: value.note_list.item(0).data(Qt.ItemDataRole.UserRole)[key]
            for key in ('note_id', 'section_key', 'revision', 'directory_revision')}
    assert not received and browsing_state(value) == before and disk_bytes(f.root) == disk
    assert button.isEnabled()


@pytest.mark.parametrize('kind', ['concept', 'readingnote'])
def test_explicit_registered_course_action_carries_initial_exact_endpoint(page, route, kind):
    value, bridge, _f = page
    _services, dialogs, action = route
    if kind == 'readingnote': add_reading_note(value)
    action.update(accepted=True, request={'link_id': 'KQL1', 'snapshot': {'revision': 7},
                                         'selection': {'key': 'original-question', 'revision': 'r'}})
    received = []; value.knowledge_question_requested.connect(received.append)
    value._open_knowledge_links(kind); bridge.succeed(bridge.take(LABEL), run=True)
    assert len(received) == 1
    assert received[0]['knowledge_endpoint'] == dialogs[0].kwargs['knowledge_resource']
    assert {key: received[0][key] for key in action['request']} == action['request']
    dialogs[0].kwargs['knowledge_resource']['source_revision'] = 'caller mutation'
    assert received[0]['knowledge_endpoint']['source_revision'] == 'a'*64


@pytest.mark.parametrize('change', ['section', 'book', 'selection', 'refresh', 'close'])
def test_registered_old_context_cannot_open_after_selection_changes(page, route, change):
    value, bridge, f = page
    _services, dialogs, _action = route
    value._open_knowledge_links('concept'); pending = bridge.take(LABEL)
    if change == 'section': value.tree.setCurrentItem(leaf(value, f.row['chapter_id'] + ':1.2'))
    elif change == 'book': value.book.setCurrentIndex(0)
    elif change == 'selection': value.knowledge_list.setCurrentRow(-1)
    elif change == 'refresh': value.refresh()
    else: value.close()
    bridge.succeed(pending, {'endpoint': {'old': True}})
    assert not dialogs and pending.task_id in bridge.cancelled


def test_registered_return_after_switching_away_and_back_is_rejected(page, route):
    value, bridge, f = page
    _services, dialogs, action = route
    received = []; value.knowledge_question_requested.connect(received.append)
    action.update(accepted=True, request={'link_id': 'old-link'}, inside=lambda _dialog: (
        value.tree.setCurrentItem(leaf(value, f.row['chapter_id'] + ':1.2')),
        value.tree.setCurrentItem(leaf(value, f.row['section_key']))))
    value._open_knowledge_links('concept'); bridge.succeed(bridge.take(LABEL), run=True)
    assert len(dialogs) == 1 and not received
    with pytest.raises(ValueError, match='变化'): dialogs[0].kwargs['guard']()


def test_registered_finished_old_task_cannot_replace_next_same_selection_request(page, route):
    value, bridge, _f = page
    _services, dialogs, _action = route
    value._open_knowledge_links('concept'); old = bridge.take(LABEL)
    value._relation_finished(old.task_id)
    value._open_knowledge_links('concept'); new = bridge.take(LABEL)
    bridge.succeed(old, {'endpoint': {'old': True}})
    assert not dialogs and value._relation_task == new.task_id
    bridge.succeed(new, run=True)
    assert len(dialogs) == 1


def personal_state(value):
    return (deepcopy(value._checked), deepcopy(value._reference), deepcopy(value._view), value._reader,
            value.note_title.text(), value.note_text.toPlainText(), value.note_page.value())


def saved_panel(panel):
    value, bridge, f = panel
    enter_chapter(value, bridge); enter_note(value, bridge)
    return value, bridge, f


def test_personal_saved_note_manages_exact_version_without_editor_changes(panel, route):
    value, bridge, f = saved_panel(panel)
    services, dialogs, _action = route
    before, disk = personal_state(value), disk_bytes(f.paths.state_root)
    received, busy = [], []
    value.knowledge_question_requested.connect(received.append); value.busy_changed.connect(busy.append)
    assert value.knowledge_links_button.isEnabled()
    value.knowledge_links_button.click()
    assert value._busy and not value.knowledge_links_button.isEnabled()
    complete(bridge, PERSONAL_LABEL)
    row = value._view['notes'][0]
    assert services[0].calls == [{'kind': 'personal', 'book': value._selection(),
        'study_revision': value._view['study_revision'], 'note': {'note_id': row['note_id'], 'revision': row['revision']}}]
    assert dialogs[0].kwargs['knowledge_resource']['selection'] == services[0].calls[0]
    assert not received and personal_state(value) == before and disk_bytes(f.paths.state_root) == disk
    assert busy[0] is True and busy[-1] is False and not value._busy


@pytest.mark.parametrize('field', ['title', 'text', 'page', 'new'])
def test_personal_unsaved_edits_cannot_claim_saved_endpoint(panel, route, field):
    value, bridge, _f = saved_panel(panel)
    services, dialogs, _action = route
    if field == 'title': value.note_title.setText('未保存的标题')
    elif field == 'text': value.note_text.setPlainText('未保存正文')
    elif field == 'page': value.note_page.setValue(3)
    else: value._new_note()
    assert not value.knowledge_links_button.isEnabled()
    value._open_knowledge_links()
    assert not services and not dialogs and not bridge.pending


@pytest.mark.parametrize('change', ['editor', 'section', 'book', 'close', 'cancel'])
def test_personal_context_late_return_does_not_open_or_emit(panel, route, change):
    value, bridge, _f = saved_panel(panel)
    _services, dialogs, _action = route
    value._open_knowledge_links(); pending = bridge.take(PERSONAL_LABEL)
    if change == 'editor': value.note_text.setPlainText('变更草稿')
    elif change == 'section': value.tree.setCurrentItem(None)
    elif change == 'book': value.set_book(deepcopy(value._book))
    elif change == 'close': value.close()
    else: bridge.task_cancelled.emit(pending.task_id, pending.label)
    bridge.succeed(pending, {'endpoint': {'old': True}})
    assert not dialogs and pending.task_id in bridge.cancelled


def test_personal_explicit_course_action_has_exact_initial_endpoint(panel, route):
    value, bridge, _f = saved_panel(panel)
    _services, dialogs, action = route
    action.update(accepted=True, request={'link_id': 'KQL-personal', 'snapshot': {'revision': 8}})
    received = []; value.knowledge_question_requested.connect(received.append)
    value._open_knowledge_links(); complete(bridge, PERSONAL_LABEL)
    assert len(received) == 1
    assert received[0]['knowledge_endpoint'] == dialogs[0].kwargs['knowledge_resource']


def test_personal_modal_return_after_editor_change_does_not_emit(panel, route):
    value, bridge, _f = saved_panel(panel)
    _services, dialogs, action = route
    action.update(accepted=True, request={'link_id': 'old'}, inside=lambda _dialog: value.note_text.setPlainText('尚未保存'))
    received = []; value.knowledge_question_requested.connect(received.append)
    value._open_knowledge_links(); complete(bridge, PERSONAL_LABEL)
    assert len(dialogs) == 1 and not received and not value.knowledge_links_button.isEnabled()


def test_personal_signal_is_forwarded_by_bookshelf(page):
    value, _bridge, _f = page
    received = []; value.knowledge_question_requested.connect(received.append)
    request = {'link_id': 'exact', 'knowledge_endpoint': {'id': 'saved-note'}}
    value.personal.knowledge_question_requested.emit(request)
    assert received == [request]


class OpaqueScopedFacade:
    def __deepcopy__(self, memo):
        raise TypeError('A live facade with native locks cannot be deep copied')


@pytest.mark.parametrize('owner', ['concept', 'readingnote', 'personal'])
@pytest.mark.parametrize('port_kind', ['native_facade', 'opaque_sentinel'])
def test_explicit_handoff_preserves_live_facade_and_copies_only_evidence(page, panel, route, scoped_lecture,
                                                                       owner, port_kind):
    value, bridge, _f = saved_panel(panel) if owner == 'personal' else page
    _services, dialogs, action = route
    if owner == 'readingnote': add_reading_note(value)
    scoped = scoped_lecture[1] if port_kind == 'native_facade' else OpaqueScopedFacade()
    if port_kind == 'native_facade':
        from integrations.deeptutor_shchem_v1.desktop_handout_questions import HandoutQuestionFacade
        assert isinstance(scoped, HandoutQuestionFacade)
        assert scoped._words._lock is scoped._scoped_words._lock
    with pytest.raises((TypeError, RecursionError)):
        deepcopy(scoped)
    action.update(accepted=True, request={'link_id': 'KQL-native-port', 'snapshot': {'revision': 4},
        'selection': {'key': 'exact-question', 'revision': 'exact-version'},
        'reference': {'materials': ['original-complete-reference']}, 'scoped_facade': scoped})
    received = []; value.knowledge_question_requested.connect(received.append)
    if owner == 'personal':
        before = personal_state(value)
        value._open_knowledge_links(); complete(bridge, PERSONAL_LABEL)
        assert personal_state(value) == before
    else:
        before = browsing_state(value)
        value._open_knowledge_links(owner); bridge.succeed(bridge.take(LABEL), run=True)
        assert browsing_state(value) == before
    assert len(received) == 1
    emitted, dialog = received[0], dialogs[0]
    assert emitted['scoped_facade'] is scoped is dialog.course_request['scoped_facade']
    assert emitted['knowledge_endpoint'] == dialog.kwargs['knowledge_resource']
    dialog.course_request['snapshot']['revision'] = -1
    dialog.course_request['selection']['key'] = 'caller changed'
    dialog.course_request['reference']['materials'].clear()
    assert emitted['snapshot']['revision'] == 4 and emitted['selection']['key'] == 'exact-question'
    assert emitted['reference']['materials'] == ['original-complete-reference']


@pytest.mark.parametrize('kind', ['concept', 'readingnote'])
def test_registered_420_entry_buttons_remain_reachable_and_selected(page, app, kind):
    value, _bridge, _f = page
    if kind == 'readingnote': add_reading_note(value)
    value.resize(420, 820)
    value.tabs.setCurrentIndex(value.knowledge_tab if kind == 'concept' else value.note_tab)
    app.processEvents()
    button = value.concept_links_button if kind == 'concept' else value.note_links_button
    top = button.mapTo(value, button.rect().topLeft())
    assert value.width() == 420 and button.isVisible() and button.isEnabled()
    assert value.rect().contains(QRect(top, button.size()))


def test_actual_relations_dialog_420_footer_and_busy_task_routing(app):
    from test_word_question_dialog import _Tasks, _cleanup
    links, tasks = Links(), _Tasks()
    endpoint = deepcopy(links.knowledge)
    dialog = KnowledgeQuestionLinksDialog(links.facade, tasks, knowledge_resource=endpoint,
        allow_course_add=True, service=links)
    dialog.resize(420, 740); dialog.show(); app.processEvents()
    assert not dialog.course_button.isEnabled()
    tasks.flush(); app.processEvents()
    assert dialog.width() == 420 and dialog.course_button.isEnabled()
    for button in (dialog.course_button, dialog.cancel_button):
        top = button.mapTo(dialog, button.rect().topLeft())
        assert button.isVisible() and dialog.rect().contains(QRect(top, button.size()))
    assert dialog.course_request is None
    _cleanup(dialog, tasks)


@pytest.mark.parametrize('kind', ['concept', 'readingnote'])
def test_actual_registered_entry_resolves_complete_source_read_only(app, scoped_lecture, notes, monkeypatch, kind):
    from dataclasses import replace
    import integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog as module
    facade = scoped_lecture[0]
    facade.paths = replace(facade.paths, workspace_root=notes.root)
    bridge, dialogs = Bridge(), []
    class ReadOnlyDialog:
        course_request = None
        def __init__(self, _facade, _tasks, _parent, **kwargs):
            dialogs.append(kwargs)
        def exec(self): return QDialog.DialogCode.Rejected
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'KnowledgeQuestionLinksDialog', ReadOnlyDialog)
    value = TextbookBookshelfPage(facade, bridge)
    value.refresh(); complete(bridge, '读取教材书架')
    value.book.setCurrentIndex(value.book.findData(notes.row['volume_id']))
    before = (disk_bytes(notes.root), disk_bytes(facade.paths.state_root))
    value._open_knowledge_links(kind); complete(bridge, LABEL)
    assert len(dialogs) == 1
    endpoint = dialogs[0]['knowledge_resource']
    assert endpoint['kind'] == ('registered_concept' if kind == 'concept' else 'reading_note')
    assert endpoint['source_sha256'] == notes.row['source_sha256']
    assert endpoint['original']['blocks'] and endpoint['content_sha256']
    assert (disk_bytes(notes.root), disk_bytes(facade.paths.state_root)) == before
    value.close(); value.deleteLater(); app.processEvents()


def test_actual_personal_entry_resolves_saved_bnote_read_only(app, scoped_lecture, native_fixture, monkeypatch):
    from dataclasses import replace
    import integrations.deeptutor_shchem_v1.desktop_workbench.knowledge_question_links_dialog as module
    f, facade = native_fixture, scoped_lecture[0]
    view = personal_note(f, chapter(f), title='具体原知识')
    facade.paths = replace(facade.paths, workspace_root=f.paths.workspace_root)
    facade.personal_textbook_study = lambda: f.study
    facade.textbook_workspace = lambda: f.workspace
    bridge, dialogs = Bridge(), []
    class ReadOnlyDialog:
        course_request = None
        def __init__(self, _facade, _tasks, _parent, **kwargs): dialogs.append(kwargs)
        def exec(self): return QDialog.DialogCode.Rejected
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'KnowledgeQuestionLinksDialog', ReadOnlyDialog)
    value = PersonalTextbookPanel(facade, bridge)
    value.set_book(view['book']); complete(bridge, '读取个人教材章节与笔记')
    value.note_list.setCurrentRow(0)
    before = (disk_bytes(f.paths.state_root), disk_bytes(facade.paths.state_root), f.source.read_bytes())
    value._open_knowledge_links(); complete(bridge, PERSONAL_LABEL)
    assert len(dialogs) == 1
    endpoint = dialogs[0]['knowledge_resource']
    assert endpoint['kind'] == 'personal_note' and endpoint['id'] == view['notes'][0]['note_id']
    assert endpoint['source_revision'] == view['notes'][0]['revision']
    assert endpoint['selection']['study_revision'] == view['study_revision']
    assert (disk_bytes(f.paths.state_root), disk_bytes(facade.paths.state_root), f.source.read_bytes()) == before
    value.close(); value.deleteLater(); app.processEvents()
