"""Repeat Word selection through real entry methods and isolated minimal Qt hosts.

No full page/window constructor, Facade/StateStore/Recovery or profile fixture.
The synthetic reader responses deliberately exercise the actual Workspace's
catalog/outline/confirmation checks, without importing an existing test fixture.
"""
import os
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from integrations.deeptutor_shchem_v1.desktop_workbench import handout_material_workspace as workspace_ui
from integrations.deeptutor_shchem_v1.desktop_workbench.question_explorer_page import QuestionExplorerPage
from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import LibraryPage
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


CATALOG = '读取讲义选材资料目录'
OUTLINE = '读取当前讲义选材原文'
CONFIRM = '核对本次讲义选材来源'


class Tasks:
    def __init__(self):
        self.pending = []
        self.cancelled = []
        self.serial = 0

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        key = f'isolated-{self.serial}'
        self.pending.append(SimpleNamespace(key=key, label=label, operation=operation,
                                            on_success=on_success, on_failure=on_failure))
        return key

    def cancel(self, key):
        self.cancelled.append(key)

    def take(self, label):
        for index, pending in enumerate(self.pending):
            if pending.label == label:
                return self.pending.pop(index)
        raise AssertionError(f'No pending task: {label}')

    def finish(self, label):
        pending = self.take(label)
        try:
            value = pending.operation()
        except (ValueError, OSError) as exc:
            pending.on_failure(str(exc))
        else:
            pending.on_success(value)
        return pending


def digest(text):
    return sha256(text.encode()).hexdigest()


class SyntheticReader:
    def __init__(self):
        self.sources = [dict(schema='shchem.handout-knowledge-source.v1', batch_id='isolated-batch',
            source_id=f'source-{index}', source_name=f'合成讲义{index}.docx',
            source_sha256=digest(f'bytes-{index}'), source_role='handout') for index in range(2)]
        self.outlines = {}
        for source in self.sources:
            bound = {**source, 'source_path': f"C:/synthetic-only/{source['source_name']}",
                     'source_revision': digest(source['source_id'] + '-revision')}
            sections = [dict(id='word-heading-b1', level=2, title='完整知识栏目', start=1, end=3)]
            self.outlines[source['source_id']] = dict(source=bound,
                outline_revision=digest(source['source_id'] + '-outline'), sections=sections,
                preview=dict(source_name=source['source_name'], source_sha256=source['source_sha256'],
                    revision=bound['source_revision'], sections=deepcopy(sections), assets=[], warnings=[],
                    blocks=[dict(index=index, text=text, label=f'区块{index}', warnings=[])
                            for index, text in enumerate(('完整知识栏目', '条件与知识正文完整。', '栏目结尾。'), 1)]))
        self.catalog_calls = 0
        self.outline_calls = []
        self.context_calls = []

    def catalog(self, _facade):
        self.catalog_calls += 1
        return {'sources': deepcopy(self.sources), 'issues': []}

    def outline(self, _facade, source):
        self.outline_calls.append(deepcopy(source))
        return deepcopy(self.outlines[source['source_id']])

    def context(self, _facade, source):
        self.context_calls.append(deepcopy(source))
        return {'document_source': deepcopy(self.outlines[source['source_id']]['source'])}


def init_host(host, tasks):
    QWidget.__init__(host)
    host.facade = SimpleNamespace()
    host.tasks = tasks
    host._closed = False
    host._handout_materials_closed = False
    host._handout_materials_dialog = None
    host._handout_materials_source = None
    host._epoch = 0
    host._search_task = None
    host._search_timer = SimpleNamespace(stop=lambda: None)
    host.received = []
    host.handout_materials_requested.connect(lambda value: host.received.append(deepcopy(value)))


class MinimalExplorer(QuestionExplorerPage):
    def __init__(self, tasks):
        init_host(self, tasks)

    def resizeEvent(self, event): QWidget.resizeEvent(self, event)
    def showEvent(self, event): QWidget.showEvent(self, event)
    def close_reader(self): pass


class MinimalLibrary(LibraryPage):
    def __init__(self, tasks):
        init_host(self, tasks)

    def resizeEvent(self, event): QWidget.resizeEvent(self, event)
    def _invalidate_detail(self, **_kwargs): pass


@pytest.fixture(scope='module')
def app():
    value = QApplication.instance() or QApplication([])
    value.setStyle('Fusion')
    install_ui_font(value)
    return value


@pytest.fixture
def make_case(app, monkeypatch):
    hosts, dialogs = [], []

    def make(kind='default', *, strict=False):
        library, tasks = SyntheticReader(), Tasks()
        monkeypatch.setattr(workspace_ui, 'handout_knowledge_sources', library.catalog)
        monkeypatch.setattr(workspace_ui, 'handout_knowledge_outline', library.outline)
        monkeypatch.setattr(workspace_ui, 'handout_source_context', library.context)
        host = (MinimalExplorer if kind == 'default' else MinimalLibrary)(tasks)
        hosts.append(host)
        if strict:
            dialog = workspace_ui.HandoutMaterialWorkspace(SimpleNamespace(), tasks,
                initial_source=deepcopy(library.outlines['source-0']['source']))
            dialogs.append(dialog)
        else:
            host.open_handout_materials()
            dialog = host._handout_materials_dialog
        return SimpleNamespace(host=host, dialog=dialog, tasks=tasks, library=library, app=app)

    yield make
    for host in hosts:
        host.close()
        host.deleteLater()
    for dialog in dialogs:
        dialog.reject()
        dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def ready(case, number=0):
    case.tasks.finish(CATALOG)
    case.dialog.source.setCurrentIndex(number + 1)
    case.tasks.finish(OUTLINE)
    assert case.dialog._document == case.library.outlines[f'source-{number}']['source']


def selected(case):
    outline = case.dialog._outline
    case.dialog._add_selections('knowledge', [dict(source=deepcopy(outline['source']),
        outline_revision=outline['outline_revision'], section=deepcopy(outline['sections'][0]),
        title='链结构怎样影响性质？', teacher_only=False)])


def resume(case):
    case.host.open_handout_materials()
    case.dialog = case.host._handout_materials_dialog


@pytest.mark.parametrize('kind', ['default', 'legacy'])
@pytest.mark.parametrize('accepted', [False, True])
def test_finished_batch_resumes_verified_source_with_fresh_read_and_empty_selection(make_case, kind, accepted):
    case = make_case(kind)
    ready(case)
    expected = deepcopy(case.dialog._document)
    selected(case)
    if accepted:
        case.dialog.confirm.click()
        case.tasks.finish(CONFIRM)
    else:
        case.dialog.cancel_button.click()
    assert case.host._handout_materials_source == expected and len(expected) == 8
    assert len(case.host.received) == int(accepted)
    first = case.dialog
    resume(case)
    assert case.dialog is not first and case.dialog.initial_source == expected
    assert case.dialog.request is None and not case.dialog._selections
    assert not case.dialog.confirm.isEnabled() and case.dialog._document is None
    case.tasks.finish(CATALOG)
    assert case.dialog.source.currentData() == expected and case.dialog._document is None
    case.tasks.finish(OUTLINE)
    assert case.library.catalog_calls == 2 and case.library.outline_calls[-1] == expected
    assert case.dialog.continuation_source() == expected
    assert case.dialog.initial_source is None and case.dialog.source.isEnabled()
    assert not case.dialog._selections and case.dialog.request is None
    assert not case.dialog.confirm.isEnabled() and len(case.host.received) == int(accepted)
    case.host.open_handout_materials()
    assert case.host._handout_materials_dialog is case.dialog
    case.dialog.source.setCurrentIndex(2)
    case.tasks.finish(OUTLINE)
    assert case.dialog._document['source_id'] == 'source-1'


@pytest.mark.parametrize('kind', ['default', 'legacy'])
@pytest.mark.parametrize('stage', ['catalog_only', 'unread_outline', 'switched_unread'])
def test_unread_or_switched_source_and_late_read_cannot_become_continuation(make_case, kind, stage):
    case = make_case(kind)
    if stage == 'switched_unread':
        ready(case)
        case.dialog.source.setCurrentIndex(2)
    else:
        case.tasks.finish(CATALOG)
        if stage == 'unread_outline':
            case.dialog.source.setCurrentIndex(1)
    pending = case.tasks.take(OUTLINE) if stage != 'catalog_only' else None
    case.dialog.reject()
    assert case.host._handout_materials_source is None and not case.host.received
    if pending is not None:
        pending.on_success(pending.operation())
        assert case.host._handout_materials_source is None and not case.host.received
    resume(case)
    case.tasks.finish(CATALOG)
    assert case.dialog.initial_source is None and case.dialog.source.currentData() is None


@pytest.mark.parametrize('change', ['missing', 'same_name_different_sha', 'ambiguous'])
def test_resume_catalog_change_returns_manual_choice_without_guessing(make_case, change):
    case = make_case()
    ready(case)
    case.dialog.reject()
    if change == 'missing':
        case.library.sources.pop(0)
    elif change == 'same_name_different_sha':
        case.library.sources[0]['source_sha256'] = digest('changed bytes')
    else:
        case.library.sources.append(deepcopy(case.library.sources[0]))
    resume(case)
    case.tasks.finish(CATALOG)
    assert case.dialog.source.currentData() is None and case.dialog.initial_source is None
    assert case.dialog.source.isEnabled() and case.dialog.source_search.isEnabled()
    assert case.dialog._document is None and case.dialog.continuation_source() is None
    assert case.dialog.request is None and not case.dialog._selections
    assert len(case.library.outline_calls) == 1 and '重新选择' in case.dialog.status.text()
    assert case.dialog.source.count() == len(case.library.sources) + 1
    other = next(index for index in range(case.dialog.source.count())
                 if (case.dialog.source.itemData(index) or {}).get('source_id') == 'source-1')
    case.dialog.source.setCurrentIndex(other)
    case.tasks.finish(OUTLINE)
    assert case.dialog.continuation_source()['source_id'] == 'source-1'


@pytest.mark.parametrize('field,value', [
    ('source_path', 'C:/different/source.docx'), ('source_revision', 'f' * 64),
    ('source_sha256', 'e' * 64), ('source_role', 'answer')])
def test_resume_outline_change_never_silently_rebinds_saved_source(make_case, field, value):
    case = make_case()
    ready(case)
    expected = deepcopy(case.dialog._document)
    case.dialog.reject()
    changed = case.library.outlines['source-0']
    changed['source'][field] = value
    if field == 'source_revision': changed['preview']['revision'] = value
    if field == 'source_sha256': changed['preview']['source_sha256'] = value
    resume(case)
    case.tasks.finish(CATALOG)
    case.tasks.finish(OUTLINE)
    assert case.library.outline_calls[-1] == expected
    assert case.dialog.initial_source is None and case.dialog.source.currentData() is None
    assert case.dialog._document is None and case.dialog.continuation_source() is None
    assert not case.dialog.confirm.isEnabled() and not case.host.received
    case.dialog.reject()
    assert case.host._handout_materials_source is None


def test_resume_worker_failure_returns_manual_choice_and_clears_stale_binding(make_case):
    case = make_case()
    ready(case)
    case.dialog.reject()
    resume(case)
    case.tasks.finish(CATALOG)
    pending = case.tasks.take(OUTLINE)
    pending.on_failure('原文件已变化，请重新选择。')
    assert case.dialog.source.currentData() is None and case.dialog.source.isEnabled()
    assert case.dialog.continuation_source() is None and not case.dialog.confirm.isEnabled()
    pending.on_success(pending.operation())
    assert case.dialog._document is None and case.dialog.request is None


@pytest.mark.parametrize('change', ['missing', 'outline_revision'])
def test_strict_section_preset_keeps_exact_rejection_without_ordinary_fallback(make_case, change):
    case = make_case(strict=True)
    expected = deepcopy(case.dialog.initial_source)
    if change == 'missing':
        case.library.sources.pop(0)
    else:
        case.library.outlines['source-0']['source']['source_revision'] = 'd' * 64
        case.library.outlines['source-0']['preview']['revision'] = 'd' * 64
    case.tasks.finish(CATALOG)
    if change == 'outline_revision': case.tasks.finish(OUTLINE)
    assert case.dialog.initial_source == expected
    assert not case.dialog.source.isEnabled() and not case.dialog.source_search.isEnabled()
    assert case.dialog._document is None and case.dialog.continuation_source() is None
    assert not case.dialog.confirm.isEnabled() and case.dialog.request is None


@pytest.mark.parametrize('kind', ['default', 'legacy'])
def test_parent_close_and_late_confirm_cannot_update_resume_or_submit(make_case, kind):
    case = make_case(kind)
    ready(case)
    selected(case)
    case.dialog.confirm.click()
    pending = case.tasks.take(CONFIRM)
    old = deepcopy(case.library.outlines['source-1']['source'])
    case.host._handout_materials_source = deepcopy(old)
    case.host.close()
    pending.on_success(pending.operation())
    case.dialog.request = {'late': True}
    case.dialog.finished.emit(QDialog.DialogCode.Accepted)
    assert case.host._handout_materials_source == old and not case.host.received
    assert case.host._handout_materials_dialog is None
    case.host.open_handout_materials()
    assert case.host._handout_materials_dialog is None


@pytest.mark.parametrize('kind', ['default', 'legacy'])
def test_old_finished_callback_cannot_replace_newer_source_or_submit(make_case, kind):
    case = make_case(kind)
    ready(case)
    old = case.dialog
    old.reject()
    resume(case)
    case.tasks.finish(CATALOG)
    case.tasks.finish(OUTLINE)
    case.dialog.source.setCurrentIndex(2)
    case.tasks.finish(OUTLINE)
    case.dialog.reject()
    expected = deepcopy(case.host._handout_materials_source)
    old.request = {'late': True}
    old.finished.emit(QDialog.DialogCode.Accepted)
    assert case.host._handout_materials_source == expected
    assert expected['source_id'] == 'source-1' and not case.host.received


def test_continuation_is_detached_and_failed_final_source_check_revokes_it(make_case):
    case = make_case()
    ready(case)
    expected = deepcopy(case.dialog._document)
    copied = case.dialog.continuation_source()
    copied['source_path'] = 'changed-by-caller.docx'
    assert case.dialog.continuation_source() == expected
    selected(case)
    case.dialog.confirm.click()
    pending = case.tasks.take(CONFIRM)
    pending.on_success({'document_source': {**expected, 'source_revision': 'c' * 64}})
    assert case.dialog.continuation_source() is None and case.dialog.request is None
    case.dialog.reject()
    assert case.host._handout_materials_source is None and not case.host.received
