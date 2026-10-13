"""Catalogue isolation, cancellation and native progress routing contracts."""
from copy import deepcopy
from threading import Event

import pytest

from test_word_questions_service import _import
from test_desktop_visual_import_facade import desktop_paths as desktop_paths
from test_word_question_dialog import _Facade, _Tasks, _question, qt_app as qt_app
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import WordQuestionDialog


def test_catalogue_and_source_returns_cannot_poison_reused_cached_trees(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path, image=True)
    original = facade.word_question_catalog()
    changed = facade.word_question_catalog()
    changed['items'][0]['question_blocks'][0]['text'] = 'untrusted edit'
    changed['items'][0]['question_blocks'][-1]['assets'].clear()
    changed['items'][0]['warnings'].append('untrusted warning')
    changed['sources'][0]['source_name'] = 'untrusted source'
    row = original['items'][0]
    source = facade.word_question_source(row['key'], row['revision'])
    saved_source = deepcopy(source)
    source['blocks'][0]['text'] = 'untrusted preview'
    source['assets'].clear()
    assert facade.word_question_catalog() == original
    assert facade.word_question_source(row['key'], row['revision']) == saved_source


def test_progress_returns_same_complete_catalogue_and_revalidates_source(desktop_paths, tmp_path):
    facade, _, _ = _import(desktop_paths, tmp_path)
    original = facade.word_question_catalog()
    events = []
    assert facade.word_question_catalog_progress(events.append, lambda: False) == original
    assert any('2 道题' in event['message'] for event in events)
    row = original['items'][0]
    archive = desktop_paths.state_root/'visual-import-v2'/'sources'/f"{row['source_sha256']}.docx"
    archive.write_bytes(b'changed source')
    result = facade.word_question_catalog_progress(events.append, lambda: False)
    assert not result['items'] and result['warnings']


def test_cancel_during_indexing_publishes_no_partial_locations_or_labels(desktop_paths, tmp_path, monkeypatch):
    facade, _, _ = _import(desktop_paths, tmp_path)
    service = facade._word_questions()
    stopped = Event()
    label_reads = []
    monkeypatch.setattr(service.attribute_store, 'get_many', lambda keys: label_reads.append(keys))
    def progress(value):
        if '道题' in value['message']:
            stopped.set()
    with pytest.raises(ReadCancelled):
        facade.word_question_catalog_progress(progress, stopped.is_set)
    assert service._locations == {} and label_reads == []


class ProgressTasks(_Tasks):
    def submit_progress(self, label, operation, *, on_progress=None, on_success=None, on_failure=None):
        self.progress_callback = on_progress
        return self.submit(label, lambda: operation(on_progress, lambda: False),
                           on_success=on_success, on_failure=on_failure)


def progressive_facade():
    facade = _Facade()
    def read(progress, cancelled):
        assert not cancelled()
        progress({'message': '正在核对 3 道题的已保存教学标签…'})
        return facade.word_question_catalog()
    facade.word_question_catalog_progress = read
    return facade


def test_native_progress_does_not_publish_rows_and_late_signal_cannot_replace_success(qt_app):
    tasks = ProgressTasks()
    dialog = WordQuestionDialog(progressive_facade(), tasks)
    tasks.progress_callback({'message': '已整理 1 份 Word 来源，继续核对…'})
    assert '1 份' in dialog.status.text() and not dialog._items
    tasks.finish('读取 Word 逐题目录')
    assert dialog._loaded_once and len(dialog._items) == 3
    success = dialog.status.text()
    tasks.progress_callback({'message': 'late progress'})
    assert dialog.status.text() == success
    dialog.reject()


def test_closed_native_reader_ignores_late_progress(qt_app):
    tasks = ProgressTasks()
    dialog = WordQuestionDialog(progressive_facade(), tasks)
    dialog.reject()
    before = dialog.status.text()
    tasks.progress_callback({'message': 'late progress'})
    assert dialog.status.text() == before


def test_filter_failure_restores_native_updates_and_signal_state(qt_app, monkeypatch):
    tasks = _Tasks()
    dialog = WordQuestionDialog(_Facade(), tasks)
    tasks.flush()
    def fail(*args):
        raise ValueError('synthetic rebuild failure')
    monkeypatch.setattr(dialog, '_replace_list_rows', fail)
    with pytest.raises(ValueError):
        dialog._filter_items()
    assert dialog.question_list.updatesEnabled()
    assert not dialog.question_list.signalsBlocked() and not dialog._rendering_list
    dialog.reject()


def test_complete_large_directory_can_scroll_to_a_late_question(qt_app):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView

    facade = _Facade()
    facade.catalog['items'] = [_question(f'Q{n}', 'A') for n in range(3500)]
    tasks = _Tasks()
    dialog = WordQuestionDialog(facade, tasks)
    dialog.resize(1100, 900)
    dialog.show()
    tasks.flush()
    qt_app.processEvents()
    item = dialog.question_list.item(3400)
    dialog.question_list.setCurrentRow(3400)
    dialog.question_list.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
    QTest.qWait(100)
    rect = dialog.question_list.visualItemRect(item)
    assert rect.isValid() and rect.intersects(dialog.question_list.viewport().rect())
    assert dialog.question_list.currentItem().data(256) == 'Q3400'
    dialog.reject()
