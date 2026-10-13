"""Cross-chapter selection management uses identities and never imports material."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from test_textbook_bookshelf_ui import app as app, leaf, painted
from test_textbook_reading_preparation_ui import notes_page as notes_page
from test_textbook_reading_preparation import shelf_fixture as shelf_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_bookshelf import disk_bytes
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_selection_dialog import TextbookSelectionDialog


def choose(value, kind):
    widget = {'concept': value.knowledge_list, 'reading_note': value.note_list,
              'asset': value.asset_list}[kind]
    widget.item(0).setCheckState(Qt.CheckState.Checked)
    selected = widget.item(0).data(Qt.ItemDataRole.UserRole)
    key = {'concept': 'concept_id', 'reading_note': 'note_id', 'asset': 'visual_asset_id'}[kind]
    return next(row for row in value._selection_list_rows()
                if row['kind'] == kind and row['selection'][key] == selected[key])


def second_concept(value, f):
    """A second UI candidate in the already verified synthetic directory."""
    section = next(row for row in value._snapshot['sections']
                   if row['volume_id'] == f.row['volume_id'] and row['section_key'] != f.row['section_key'])
    row = deepcopy(value._snapshot['concepts'][0])
    row.update(concept_id='SYNTHETIC-SECOND-CONCEPT', title='合成B节知识', revision='synthetic-second-revision',
               section_key=section['section_key'], chapter_id=section['chapter_id'], pdf_pages=section['pdf_pages'][:1])
    value._snapshot['concepts'].append(row)
    value.tree.setCurrentItem(leaf(value, section['section_key']))
    return section, choose(value, 'concept')


def prepared(value):
    # UI receipt only; real source validation remains covered by bookshelf tests.
    value._reference = {'materials': '合成已核对预览'}
    value._prepared_key = value._selection_key()
    value.reference_preview.setPlainText('合成已核对预览')
    value._preparation_receipt_controls()


def test_remove_hidden_a_preserves_b_reader_position_target_and_invalidates_text(notes_page, app):
    value, bridge, f = notes_page
    a = choose(value, 'concept')
    section, b = second_concept(value, f)
    value.set_preparation_context({'topic': '合成接收课', 'period_count': 2, 'period_minutes': 40}, dirty=True)
    value._open_section()
    app.processEvents()
    bridge.succeed(bridge.take('读取本节教材原页'), run=True)
    assert painted(app, value._reader) > 10
    reader = value._reader
    reader.pages.setCurrentIndex(1)
    before = (reader, reader.pages.currentData(), reader.zoom.currentIndex(), value._preparation_target_epoch,
              value.preparation_context_bar.title.text(), value.tree.currentItem())
    prepared(value)
    assert value.confirm_button.isEnabled()
    value._open_selection_list()
    dialog = value._selection_dialog
    assert dialog.items.count() == 2
    dialog.items.setCurrentRow(0)
    dialog.remove_button.click()
    assert value._checked == {b['selection']['concept_id']: b['selection']}
    assert (value._reader, reader.pages.currentData(), reader.zoom.currentIndex(), value._preparation_target_epoch,
            value.preparation_context_bar.title.text(), value.tree.currentItem()) == before
    assert value.knowledge_list.item(0).checkState() == Qt.CheckState.Checked
    assert value._reference is None and value._prepared_key is None
    assert not value.reference_preview.toPlainText() and not value.confirm_button.isEnabled()
    assert not value.cards_button.isEnabled() and dialog.items.count() == 1
    assert not value._remove_selection_entry(a)


def test_rows_show_all_three_types_with_bound_book_chapter_and_actual_pages(notes_page):
    value, _, f = notes_page
    for kind in ('concept', 'reading_note', 'asset'):
        choose(value, kind)
    rows = value._selection_list_rows()
    assert [row['kind'] for row in rows] == ['concept', 'reading_note', 'asset']
    for row in rows:
        assert '合成教材册' in row['location'] and '合成第1.1节' in row['location']
        assert row['can_locate'] and row['volume_id'] == f.row['volume_id']
    assert 'PDF第7、8、10页' in rows[0]['location']
    assert 'PDF第10页' in rows[1]['location'] and '印刷第6页' in rows[1]['location']
    assert 'PDF第9页' in rows[2]['location'] and '印刷第5页' in rows[2]['location']


@pytest.mark.parametrize('kind', ['concept', 'reading_note', 'asset'])
def test_remove_only_one_identity_and_respects_existing_text_image_contract(notes_page, kind):
    value, _, _ = notes_page
    entries = {name: choose(value, name) for name in ('concept', 'reading_note', 'asset')}
    value._excerpts[entries['concept']['selection']['concept_id']] = {'revision': entries['concept']['selection']['revision'], 'text': '合成暂存摘录'}
    prepared(value)
    epoch = value._asset_selection_epoch
    assert value._remove_selection_entry(entries[kind])
    assert {row['kind'] for row in value._selection_list_rows()} == set(entries)-{kind}
    assert value._excerpts  # ordinary unchecking retains the teacher's temporary excerpt
    if kind == 'asset':
        assert value._reference is not None and value.confirm_button.isEnabled()
        assert value._asset_selection_epoch == epoch+1
    else:
        assert value._reference is None and not value.confirm_button.isEnabled()
        assert value._asset_selection_epoch == epoch


@pytest.mark.parametrize('kind', ['concept', 'reading_note', 'asset'])
def test_locate_switches_to_real_original_item_without_reading_or_import(notes_page, kind):
    value, bridge, f = notes_page
    entry = choose(value, kind)
    second_concept(value, f)
    choices = (deepcopy(value._checked), deepcopy(value._note_checked), deepcopy(value._asset_checked))
    received = []
    value.preparation_reference_requested.connect(received.append)
    value.preparation_cards_requested.connect(received.append)
    value.preparation_images_requested.connect(received.append)
    value._open_selection_list()
    dialog = value._selection_dialog
    for index in range(dialog.items.count()):
        if dialog.items.item(index).data(Qt.ItemDataRole.UserRole)['selection'] == entry['selection']:
            dialog.items.setCurrentRow(index)
            break
    dialog.locate_button.click()
    assert value.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == f.row['section_key']
    widget, tab = {'concept': (value.knowledge_list, value.knowledge_tab),
                   'reading_note': (value.note_list, value.note_tab), 'asset': (value.asset_list, value.asset_tab)}[kind]
    assert value.tabs.currentIndex() == tab and widget.currentRow() == 0
    assert widget.currentItem().checkState() == Qt.CheckState.Checked
    assert (value._checked, value._note_checked, value._asset_checked) == choices
    assert dialog.result() == QDialog.DialogCode.Accepted and value._selection_dialog is None
    assert value._reader is None and not received
    assert all(task.label == '预览教材图表原页' for task in bridge.pending)


def test_missing_binding_never_guesses_location_from_title(notes_page):
    value, _, _ = notes_page
    entry = choose(value, 'concept')
    row = value._snapshot['concepts'][0]
    row.update(volume_id=None, section_key=None, pdf_pages=[], printed_pages=[],
               title='必修2 第1章 PDF第42页')
    entry = value._selection_list_rows()[0]
    assert not entry['can_locate'] and '书：待核对' in entry['location'] and 'PDF页待核对' in entry['location']
    assert '42' not in entry['location'] and not value._locate_selection_entry(entry)
    assert value._remove_selection_entry(entry)


def test_locating_a_from_reading_b_clears_old_reader_and_keeps_all_choices(notes_page, app):
    value, bridge, f = notes_page
    a = choose(value, 'concept')
    second_concept(value, f)
    value._open_section()
    app.processEvents()
    bridge.succeed(bridge.take('读取本节教材原页'), run=True)
    assert value._reader is not None
    selected = deepcopy(value._checked)
    assert value._locate_selection_entry(a)
    assert value._reader is None and value._checked == selected
    assert value.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == f.row['section_key']


@pytest.mark.parametrize('busy', ['loading', 'preparation'])
def test_busy_disables_dialog_actions_and_refuses_direct_changes(notes_page, busy):
    value, _, _ = notes_page
    entry = choose(value, 'concept')
    value._open_selection_list()
    dialog = value._selection_dialog
    if busy == 'loading':
        value._loading = True
        value._set_read_controls(False)
    else:
        value.set_preparation_context({'topic': '合成备课'}, busy=True)
    assert not value.selection_list_button.isEnabled()
    assert not dialog.remove_button.isEnabled() and not dialog.locate_button.isEnabled()
    assert not value._remove_selection_entry(entry) and not value._locate_selection_entry(entry)
    dialog.remove_requested.emit(entry)
    assert value._checked
    value._loading = False
    value.set_preparation_context({'topic': '合成备课'}, busy=False)
    assert dialog.remove_button.isEnabled()


def test_closed_dialog_and_stale_revision_cannot_change_choices(notes_page):
    value, _, _ = notes_page
    entry = choose(value, 'concept')
    value._open_selection_list()
    dialog = value._selection_dialog
    dialog.reject()
    dialog.remove_requested.emit(entry)
    assert value._checked and value._selection_dialog is None
    stale = deepcopy(entry)
    stale['selection']['revision'] = 'old'
    assert not value._remove_selection_entry(stale) and not value._locate_selection_entry(stale)
    value._open_selection_list()
    active = value._selection_dialog
    value.close()
    assert not active._active and value._selection_dialog is None
    assert not value._remove_selection_entry(entry)


def test_late_preview_after_remove_cannot_restore_confirmation(notes_page):
    value, bridge, _ = notes_page
    entry = choose(value, 'concept')
    value._preview_reference()
    task = bridge.take('核对教材备课参考')
    assert value._remove_selection_entry(entry)
    bridge.succeed(task, run=True)
    assert value._reference is None and not value.confirm_button.isEnabled()


@pytest.mark.parametrize('width', [360, 420, 1100])
def test_compact_entry_and_dialog_actions_fit_at_narrow_and_wide_sizes(notes_page, app, width):
    value, _, _ = notes_page
    choose(value, 'concept')
    value.resize(width, 900)
    app.processEvents()
    button = value.selection_list_button
    assert button.isVisible()
    left = button.mapTo(value, button.rect().topLeft())
    right = button.mapTo(value, button.rect().bottomRight())
    assert 0 <= left.x() <= right.x() < value.width()
    value._open_selection_list()
    dialog = value._selection_dialog
    dialog.resize(width, 500)
    app.processEvents()
    assert dialog.width() == width
    for control in (dialog.items, dialog.locate_button, dialog.remove_button, dialog.close_button):
        first = control.mapTo(dialog, control.rect().topLeft())
        last = control.mapTo(dialog, control.rect().bottomRight())
        assert 0 <= first.x() <= last.x() < dialog.width()
        assert 0 <= first.y() <= last.y() < dialog.height()


@pytest.mark.parametrize('change', ['remove-readd', 'clear-readd', 'target-change'])
def test_old_crop_batch_cannot_import_after_selection_or_target_epoch_change(notes_page, monkeypatch, change):
    value, bridge, f = notes_page
    entry = choose(value, 'asset')
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_batch_crop_dialog as module
    received, composition_received, opened = [], [], []
    original_bytes = disk_bytes(f.root)
    expected_choices = [{'selection': {key: row[key] for key in ('visual_asset_id', 'revision')},
                         'label': row['label']} for row in value._asset_checked.values()]
    class TestDialog:
        selection_requests = [{'synthetic': True}]
        handoff_mode = 'assets'
        def __init__(self, facade, tasks, choices, parent, *, allow_composition_handoff=False):
            assert facade is value.facade and tasks is bridge and parent is value
            assert choices == expected_choices and allow_composition_handoff is True
            opened.append(deepcopy(choices))
        def exec(self):
            if change == 'remove-readd':
                value._remove_selection_entry(entry)
                value.asset_list.item(0).setCheckState(Qt.CheckState.Checked)
            elif change == 'clear-readd':
                value._clear_selection()
                value.asset_list.item(0).setCheckState(Qt.CheckState.Checked)
            else:
                value.set_preparation_context({'topic': '另一个合成课'}, target_changed=True)
            return QDialog.DialogCode.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'TextbookBatchCropDialog', TestDialog)
    value.preparation_images_requested.connect(received.append)
    value.composition_crops_requested.connect(composition_received.append)
    frozen_requests = deepcopy(TestDialog.selection_requests)
    value._crop_assets()
    assert opened == [expected_choices]
    assert not received and not composition_received
    assert TestDialog.selection_requests == frozen_requests and disk_bytes(f.root) == original_bytes


def test_personal_mode_hides_fixed_textbook_selection_entry(notes_page):
    value, _, _ = notes_page
    choose(value, 'concept')
    value._personal_mode(True)
    assert value.selection_list_button.isHidden()


@pytest.mark.parametrize('change', ['accepted', 'cancelled', 'target-change', 'book-change', 'closed',
                                  'loading', 'refreshed', 'source-change', 'unrelated-uncheck'])
def test_single_crop_checks_original_source_and_destination_without_coupling_other_choices(notes_page, monkeypatch, change):
    value, bridge, f = notes_page
    entry = choose(value, 'asset')
    from integrations.deeptutor_shchem_v1.desktop_workbench import textbook_preparation_crop_dialog as module
    received, composition_received, opened = [], [], []
    original_bytes = disk_bytes(f.root)
    class TestDialog:
        selection_request = {'selection': deepcopy(entry['selection']), 'synthetic': True}
        handoff_mode = 'assets'
        def __init__(self, facade, tasks, selection, parent, *, allow_composition_handoff=False):
            assert facade is value.facade and tasks is bridge and parent is value
            assert selection == entry['selection']
            assert allow_composition_handoff is True
            opened.append(deepcopy(selection))
        def exec(self):
            if change == 'target-change':
                value.set_preparation_context({'topic': '另一个合成课'}, target_changed=True)
            elif change == 'book-change':
                value.book.setCurrentIndex(0)
            elif change == 'closed':
                value.close()
            elif change == 'loading':
                value._loading = True
            elif change == 'refreshed':
                value._load_request += 1
            elif change == 'source-change':
                row = deepcopy(value.asset_list.currentItem().data(Qt.ItemDataRole.UserRole))
                row['revision'] = 'changed-source-revision'
                value.asset_list.currentItem().setData(Qt.ItemDataRole.UserRole, row)
            elif change == 'unrelated-uncheck':
                value.asset_list.item(0).setCheckState(Qt.CheckState.Unchecked)
            return QDialog.DialogCode.Rejected if change == 'cancelled' else QDialog.DialogCode.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(module, 'TextbookPreparationCropDialog', TestDialog)
    value.preparation_image_requested.connect(received.append)
    value.composition_crops_requested.connect(composition_received.append)
    frozen_request = deepcopy(TestDialog.selection_request)
    value._crop_asset()
    assert opened == [entry['selection']] and not composition_received
    assert len(received) == (1 if change in ('accepted', 'unrelated-uncheck') else 0)
    if received:
        assert received == [frozen_request] and received[0] is not TestDialog.selection_request
    assert TestDialog.selection_request == frozen_request and disk_bytes(f.root) == original_bytes
