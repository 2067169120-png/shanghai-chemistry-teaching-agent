"""Memory-only note editing layout and action routing, without UI fixture imports.

Uses the real panel, typography and style in a QWidget host. No MainWindow,
Recovery, DesktopStateStore, real Facade, profile or source file is constructed.
"""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QLayout, QVBoxLayout, QWidget

from integrations.deeptutor_shchem_v1.desktop_workbench.personal_textbook_panel import PersonalTextbookPanel
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font, typography_report


def synthetic_view():
    return {
        'book': {'source_id': 'synthetic-book', 'revision': 'a' * 64,
                 'source_name': '合成教材：实验观察', 'page_count': 8,
                 'section_count': 2, 'note_count': 3},
        'study_revision': 'memory-study-1',
        'sections': [dict(section_id='S1', title='第一节 实验记录', pdf_start=1, pdf_end=4,
                          revision='section-1', range_confirmed=True),
                     dict(section_id='S2', title='第二节 观察与解释', pdf_start=5, pdf_end=8,
                          revision='section-2', range_confirmed=True)],
        'notes': [dict(note_id='N1', section_id='S1', title='实验条件与单位',
                       text='合成示例：记录实验条件。', pdf_page=2, revision='note-1'),
                  dict(note_id='N2', section_id='S1', title='观察与解释',
                       text='合成示例：观察和解释分开记录。', pdf_page=3, revision='note-2'),
                  dict(note_id='N3', section_id='S2', title='对照记录',
                       text='合成示例：比较记录。', pdf_page=6, revision='note-3')],
    }


class MemoryBridge(QObject):
    task_finished = Signal(str)
    task_cancelled = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.pending, self.cancelled = [], []
        self.serial = 0

    def submit(self, title, operation, *, on_success, on_failure):
        self.serial += 1
        task = SimpleNamespace(id=str(self.serial), title=title, operation=operation,
                               success=on_success, failure=on_failure)
        self.pending.append(task)
        return task.id

    def cancel(self, task_id):
        self.cancelled.append(task_id)

    def complete(self, title):
        task = next(task for task in self.pending if task.title == title)
        self.pending.remove(task)
        try:
            value = task.operation()
        except Exception as exc:
            task.failure(str(exc))
            self.task_finished.emit(task.id)
            raise
        task.success(value)
        self.task_finished.emit(task.id)
        return value


class MemoryService:
    def __init__(self):
        self.view = synthetic_view()
        self.calls = []

    def save_note(self, selection, revision, **fields):
        assert selection == {key: self.view['book'][key] for key in ('source_id', 'revision')}
        assert revision == self.view['study_revision']
        assert fields['teacher_confirmed']
        self.calls.append((deepcopy(selection), revision, deepcopy(fields)))
        note = next(row for row in self.view['notes'] if row['note_id'] == fields['note_id'])
        note.update({key: fields[key] for key in ('title', 'text', 'pdf_page')})
        note['revision'] = 'saved-note'
        self.view['study_revision'] = 'saved-study'
        return deepcopy(self.view)


class MemoryFacade:
    def __init__(self):
        self.study = MemoryService()
        self.references = []

    def personal_textbook_study(self):
        return self.study

    def personal_textbook_reference(self, selection, revision, notes):
        assert selection['revision'] == self.study.view['book']['revision']
        assert revision == self.study.view['study_revision']
        self.references.append(deepcopy(notes))
        return {'materials': '合成教材笔记参考'}


def flush(app):
    for _ in range(4):
        app.processEvents()


def make_workspace(app, panel_class=PersonalTextbookPanel):
    install_ui_font(app)
    facade, bridge = MemoryFacade(), MemoryBridge()
    host = QWidget()
    host.setObjectName('PageContent')
    layout = QVBoxLayout(host)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    panel = panel_class(facade, bridge)
    layout.addWidget(panel)
    host.setStyleSheet(WORKBENCH_STYLE)
    panel._loaded(deepcopy(facade.study.view))
    panel.note_list.setCurrentRow(0)
    panel.tabs.setCurrentIndex(2)
    host.resize(420, 960)
    host.show()
    flush(app)
    return SimpleNamespace(host=host, panel=panel, facade=facade, bridge=bridge)


def destroy_workspace(workspace, app):
    panel = workspace.panel
    # This suite never constructs a real reader; remove the read-layout sentinel.
    panel._reader = None
    panel._note_edits.discard_all()
    panel._note_editor_owner = None
    workspace.host.close()
    workspace.host.deleteLater()
    flush(app)


def edit_note(panel):
    panel.note_title.setText('实验条件与单位（未保存）')
    panel.note_text.setPlainText('合成示例：记录浓度、温度和观察现象。\n'
                                '切换笔记、章节或教材后，可回到这里继续编辑。\n'
                                '完整保留条件、符号与单位。')
    panel.note_page.setValue(4)
    panel.note_confirmed.setChecked(True)


def action_pairs(panel):
    return [(getattr(panel, f'note_{name}_action'), getattr(panel, button)) for name, button in (
        ('read_book', 'read_book_button'), ('read_section', 'read_section_button'),
        ('preview', 'preview_button'), ('confirm', 'confirm_button'),
        ('cards', 'cards_button'), ('clear', 'clear_button'))]


def preserved(workspace):
    p = workspace.panel
    return deepcopy({
        'fields': (p.note_title.text(), p.note_text.toPlainText(), p.note_page.value(),
                   p.note_confirmed.isChecked()),
        'owner': p._note_editor_owner,
        'draft': asdict(p._note_edits.get(p._note_editor_owner)),
        'view': p._view, 'checked': p._checked, 'reference': p._reference,
        'reference_key': p._reference_key, 'reference_added_key': p._reference_added_key,
        'cards_added_key': p._cards_added_key, 'target': p._preparation_target_epoch,
        'crop_pages': p._crop_pages, 'epoch': p._epoch,
        'preview': p.reference_preview.toPlainText(),
        'details': p.reference_details.toPlainText(),
        'reader': p._reader, 'service_calls': workspace.facade.study.calls,
        'reference_calls': workspace.facade.references,
        'pending': [(task.id, task.title) for task in workspace.bridge.pending],
    })


def geometry(workspace):
    p = workspace.panel
    scroll = p.tabs.widget(2)
    controls = {}
    for name in ('note_title', 'note_text', 'note_edit_status', 'save_note', 'cancel_note_edits'):
        widget = getattr(p, name)
        rect = QRect(widget.mapTo(scroll.viewport(), QPoint()), widget.size())
        controls[name] = {'x': rect.x(), 'y': rect.y(), 'width': rect.width(),
                          'height': rect.height(), 'visible_height': rect.intersected(scroll.viewport().rect()).height()}
    return {'host': [workspace.host.width(), workspace.host.height()],
            'panel': [p.width(), p.height()], 'actions_height': p.actions.geometry().height(),
            'notes_viewport_height': scroll.viewport().height(),
            'body_viewport_height': p.note_text.viewport().height(),
            'scroll_maximum': scroll.verticalScrollBar().maximum(), 'controls': controls}


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle('Fusion')
    install_ui_font(application)
    return application


@pytest.fixture
def workspace(app):
    workspace = make_workspace(app)
    yield workspace
    destroy_workspace(workspace, app)


def test_420_editor_has_body_space_and_full_save_cancel_visibility(workspace, app):
    p = workspace.panel
    edit_note(p)
    flush(app)
    measured = geometry(workspace)
    assert measured['host'] == measured['panel'] == [420, 960]
    assert measured['notes_viewport_height'] >= 550
    assert measured['controls']['note_text']['height'] >= 180
    assert measured['body_viewport_height'] >= 160
    assert measured['actions_height'] <= 45
    assert p.note_actions_button.isVisible()
    assert all(not button.isVisible() for _action, button in action_pairs(p))
    assert not p.reading_actions_button.isVisible()
    scroll = p.tabs.widget(2)
    scroll.ensureWidgetVisible(p.cancel_note_edits)
    flush(app)
    for control in (p.note_title, p.note_page, p.note_text, p.note_confirmed,
                    p.note_edit_status, p.save_note, p.cancel_note_edits):
        rect = QRect(control.mapTo(scroll.viewport(), QPoint()), control.size())
        assert scroll.viewport().rect().contains(rect), (control.accessibleName(), rect)
    fonts = typography_report(workspace.host)
    assert fonts['chinese_sample_supported'] and fonts['han']['missing_glyphs'] == 0
    assert fonts['body_points'] == 11.0


def test_menu_open_escape_preserves_exact_edit_selection_reference_and_target(workspace, app):
    p = workspace.panel
    edit_note(p)
    p.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    p._reference = {'materials': '合成预览，不触发任何加载'}
    p._reference_key = p._key()
    p._preparation_target_epoch = 7
    p._crop_pages = [2, 6]
    p._controls()
    before = preserved(workspace)
    menu = p.note_actions_menu
    menu.popup(p.note_actions_button.mapToGlobal(QPoint()))
    flush(app)
    assert menu.isVisible() and len(menu.actions()) == 6
    assert [action.text() for action, _button in action_pairs(p)] == [button.text() for _action, button in action_pairs(p)]
    assert '保存和取消修改仍在笔记正文下方' in p.note_actions_button.toolTip()
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    flush(app)
    assert not menu.isVisible()
    assert preserved(workspace) == before


@pytest.mark.parametrize('name,method,expected', [
    ('read_book', '_read', (False, {})), ('read_section', '_read', (True, {})),
    ('preview', '_compile', (False, {})), ('confirm', '_compile', (True, {})),
    ('cards', '_compile', (True, {'cards': True})),
])
def test_each_menu_action_uses_original_button_and_route(workspace, app, monkeypatch, name, method, expected):
    p = workspace.panel
    p.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    p._reference = {'materials': '合成参考'}
    p._reference_key = p._key()
    p._controls()
    calls, clicked = [], []
    # Stub only the destination to avoid constructing even a lazy source reader.
    monkeypatch.setattr(p, method, lambda value, **kwargs: calls.append((value, kwargs)))
    action = getattr(p, f'note_{name}_action')
    button = next(button for candidate, button in action_pairs(p) if candidate is action)
    button.clicked.connect(lambda: clicked.append(name))
    assert action.isEnabled() and not button.isVisible()
    before = preserved(workspace)
    action.trigger()
    flush(app)
    assert calls == [expected] and clicked == [name]
    assert preserved(workspace) == before


def test_preview_menu_retains_dirty_selected_note_guard(workspace, app):
    p = workspace.panel
    edit_note(p)
    wanted = preserved(workspace)['draft']
    p.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert p.note_preview_action.isEnabled()
    p.note_preview_action.trigger()
    flush(app)
    assert '请先保存或取消本次修改' in p.status.text()
    assert p.tabs.currentIndex() == 2
    assert asdict(p._note_edits.get(p._note_editor_owner)) == wanted
    assert not workspace.facade.references and not workspace.bridge.pending


def test_saved_preview_and_reference_use_guarded_memory_route(workspace, app):
    p = workspace.panel
    p.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    p.note_preview_action.trigger()
    assert len(workspace.bridge.pending) == 1
    workspace.bridge.complete('核对个人教材备课参考')
    flush(app)
    assert p.tabs.currentIndex() == 3 and p._reference == {'materials': '合成教材笔记参考'}
    assert all(button.isVisible() for _action, button in action_pairs(p))
    assert not p.note_actions_button.isVisible()
    p.tabs.setCurrentIndex(2)
    received = []
    p.reference_requested.connect(received.append)
    assert p.note_confirm_action.isEnabled()
    p.note_confirm_action.trigger()
    workspace.bridge.complete('核对个人教材备课参考')
    assert received == [{'materials': '合成教材笔记参考'}]
    p.reference_appended()
    assert p.note_confirm_action.text() == p.confirm_button.text() == '本次参考已加入'
    assert not p.note_confirm_action.isEnabled()


def test_card_reference_receipts_and_invalidation_sync_without_reopening_menu(workspace):
    p = workspace.panel
    p.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    p._reference = {'materials': '合成参考'}
    p._reference_key = p._key()
    p._controls()
    assert p.note_cards_action.isEnabled() and p.note_confirm_action.isEnabled()
    p.cards_appended()
    assert p.note_cards_action.text() == p.cards_button.text() == '本次知识卡已加入'
    assert not p.note_cards_action.isEnabled()
    p.reference_appended()
    assert p.note_confirm_action.text() == p.confirm_button.text() == '本次参考已加入'
    assert not p.note_confirm_action.isEnabled()
    p._invalidate()
    assert p.note_cards_action.text() == '知识卡加入课件…'
    assert p.note_confirm_action.text() == '加入当前备课'
    assert not p.note_cards_action.isEnabled() and not p.note_confirm_action.isEnabled()


@pytest.mark.parametrize('mode', ['busy', 'closed', 'preparation_busy', 'no_chapter', 'no_selection', 'target_changed'])
def test_actions_reflect_current_native_button_gates(workspace, app, mode):
    p = workspace.panel
    p.note_list.item(0).setCheckState(Qt.CheckState.Checked)
    p._reference = {'materials': '合成参考'}
    p._reference_key = p._key()
    p._controls()
    if mode == 'busy':
        p._busy = True
    elif mode == 'closed':
        p._closed = True
    elif mode == 'preparation_busy':
        p.set_preparation_context({}, busy=True)
    elif mode == 'no_chapter':
        p.tree.setCurrentItem(None)
    elif mode == 'no_selection':
        p.clear_button.click()
    else:
        p.cards_appended()
        p.reference_appended()
        p.set_preparation_context({}, target_changed=True)
    p._controls()
    flush(app)
    assert all(action.isEnabled() == button.isEnabled() and action.text() == button.text()
               for action, button in action_pairs(p))
    assert p.note_actions_button.isEnabled() == any(button.isEnabled() for _action, button in action_pairs(p))
    if mode in ('busy', 'closed'):
        assert not any(action.isEnabled() for action, _button in action_pairs(p))
    elif mode == 'preparation_busy':
        assert p.note_preview_action.isEnabled() and not p.note_confirm_action.isEnabled() and not p.note_cards_action.isEnabled()
    elif mode == 'no_chapter':
        assert p.note_read_book_action.isEnabled() and not p.note_read_section_action.isEnabled()
    elif mode == 'no_selection':
        assert not p.note_preview_action.isEnabled() and not p.note_clear_action.isEnabled()
    else:
        assert p.note_confirm_action.isEnabled() and p.note_cards_action.isEnabled()
    p._closed = p._busy = False


def test_clear_menu_only_clears_selection_and_preview_preserving_unsaved_edit(workspace):
    p = workspace.panel
    edit_note(p)
    p.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    p._reference = {'materials': '合成预览'}
    p._reference_key = p._key()
    p._controls()
    before = preserved(workspace)
    clicks = []
    p.clear_button.clicked.connect(lambda: clicks.append('clear'))
    p.note_clear_action.trigger()
    assert clicks == ['clear'] and p._checked == {} and p._reference is None
    assert preserved(workspace)['draft'] == before['draft']
    assert p._note_editor_owner == before['owner'] and p._preparation_target_epoch == before['target']
    assert not workspace.bridge.pending and not workspace.facade.study.calls
    assert p.note_actions_button.isVisible() and p.note_read_book_action.isEnabled()
    assert not p.note_clear_action.isEnabled()


def test_save_and_cancel_still_operate_on_current_draft_in_the_form(workspace, app):
    p = workspace.panel
    edit_note(p)
    saved = p.note_text.toPlainText()
    p.tabs.widget(2).ensureWidgetVisible(p.save_note)
    flush(app)
    p.save_note.click()
    workspace.bridge.complete('保存个人教材笔记')
    assert workspace.facade.study.view['notes'][0]['text'] == saved
    assert not p._note_edits.dirty_keys()
    p.note_text.setPlainText('这次修改要取消')
    p.cancel_note_edits.click()
    assert p.note_text.toPlainText() == saved
    assert len(workspace.facade.study.calls) == 1
    assert not p._note_edits.dirty_keys()


@pytest.mark.parametrize('width', [420, 719, 720, 1100])
def test_responsive_boundary_and_tab_roundtrip_preserve_session(workspace, app, width):
    p = workspace.panel
    edit_note(p)
    p.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    before = preserved(workspace)
    workspace.host.resize(width, 960)
    flush(app)
    assert workspace.host.width() == p.width() == width
    assert p.note_actions_button.isVisible() == (width < 720)
    assert all(button.isVisible() == (width >= 720) for _action, button in action_pairs(p))
    assert p.note_text.maximumHeight() == (360 if width < 720 else 135)
    for index in (0, 1, 3, 4, 5, 2):
        p.tabs.setCurrentIndex(index)
        flush(app)
        assert p.note_actions_button.isVisible() == (index == 2 and width < 720)
        if index != 2:
            assert all(button.isVisible() for _action, button in action_pairs(p))
            assert p.note_text.minimumHeight() == 90 and p.note_text.maximumHeight() == 135
    assert preserved(workspace) == before


def test_loaded_reading_tab_retains_its_existing_menu_and_directory_behavior(workspace, app):
    p = workspace.panel
    edit_note(p)
    p.note_list.item(1).setCheckState(Qt.CheckState.Checked)
    # Only the two fields consumed by the layout, never a native/lazy reader.
    reader = SimpleNamespace(_loaded_pages=True)
    p._reader = reader
    p.tabs.setCurrentIndex(0)
    flush(app)
    assert p._reader is reader and not p.tree.isVisible()
    assert not p.note_actions_button.isVisible() and p.reading_actions_button.isVisible()
    assert p.confirm_button.isVisible() and not p.read_book_button.isVisible()
    assert p.actions.direction() == QBoxLayout.Direction.LeftToRight
    assert len(p.reading_actions_menu.actions()) == 3
    assert p.note_text.maximumHeight() == 135
    p.tabs.setCurrentIndex(2)
    flush(app)
    assert p.tree.isVisible() and p.note_actions_button.isVisible() and not p.reading_actions_button.isVisible()
    assert p._reader is reader
    p.tabs.setCurrentIndex(0)
    flush(app)
    assert not p.tree.isVisible() and p.reading_actions_button.isVisible() and p._reader is reader
    p._reader = None
