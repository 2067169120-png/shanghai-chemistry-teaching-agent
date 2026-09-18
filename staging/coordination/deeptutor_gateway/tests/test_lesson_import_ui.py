from copy import deepcopy
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtWidgets import QApplication, QDialog
from test_lesson_design_ui import desk
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from test_desktop_preparation import _payload, RecordingProvider
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_import_dialog import LessonImportDialog


@pytest.fixture
def prepared(desk):
    editor,app,page=desk
    manager=page.facade._preparation_manager_instance()
    task=manager.prepare(_payload(),'LOCAL-FIXTURE','REV')
    provider=RecordingProvider();value=manager.run(task['task_id'],provider,lambda _:None,lambda:False)
    assert value['status']=='completed'
    page._preparation_task_id=task['task_id']
    d=LessonImportDialog(editor);d.show();settle(app,lambda:not d.busy)
    yield d,editor,app,page,provider,task
    d.busy=False;d.reject();d.deleteLater();settle(app)


def test_no_completed_results_gives_useful_empty_state(desk):
    editor,app,page=desk
    d=LessonImportDialog(editor);d.show();settle(app,lambda:not d.busy)
    assert '没有已完成初稿' in d.status.text() and not d.read.isEnabled()
    d.reject();d.deleteLater()


def test_preview_cancel_does_not_mutate_current_design(prepared):
    d,e,app,page,provider,task=prepared;before=deepcopy(e.history.value)
    d.read.click();settle(app,lambda:not d.busy and d.proposal is not None)
    assert d.options.count()==3 and '来源任务' in d.detail.toPlainText()
    d.reject();assert e.history.value==before and len(provider.calls)==1


def test_select_subset_does_not_overwrite_existing_nodes(prepared):
    d,e,app,page,provider,task=prepared;before=deepcopy(e.history.value)
    d.read.click();settle(app,lambda:not d.busy and d.proposal is not None)
    for i in (1,2):d.options.item(i).setCheckState(Qt.CheckState.Unchecked)
    d.apply_button.click();settle(app,lambda:not d.busy)
    assert d.result()==QDialog.DialogCode.Accepted
    plan,assets=d.result_value
    assert len(plan['nodes'])==2 and plan['nodes'][0]==before['nodes'][0]
    assert e.history.value==before and len(provider.calls)==1


def test_parent_entry_commits_single_undo_step_and_original_payload(prepared,monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_lesson_import import apply_import
    d,e,app,page,provider,task=prepared
    d.read.click();settle(app,lambda:not d.busy and d.proposal is not None)
    before=deepcopy(e.history.value)
    result=apply_import(before,d.proposal,[d.proposal['options'][0]['id']])
    def accepted(dialog):dialog.result_value=result;return QDialog.DialogCode.Accepted
    monkeypatch.setattr(LessonImportDialog,'exec',accepted)
    monkeypatch.setattr(LessonImportDialog,'load_tasks',lambda self:None)
    e.import_candidate.click();settle(app)
    assert page._payload()['lesson_design']==result[0]
    e.travel(False);assert e.history.value==before


def test_changed_source_cannot_apply_stale_preview(prepared,monkeypatch):
    d,e,app,page,provider,task=prepared
    d.read.click();settle(app,lambda:not d.busy and d.proposal is not None)
    original=page.facade.preparation_revision_source(task['task_id'])
    original['source_revision']='f'*64
    monkeypatch.setattr(page.facade,'preparation_revision_source',lambda _:original)
    d.apply_button.click();settle(app,lambda:not d.busy)
    assert d.result_value is None and '已变化' in d.status.text()


def test_import_preview_compact_layout_keeps_content_and_actions(prepared):
    d,e,app,page,provider,task=prepared
    d.read.click();settle(app,lambda:not d.busy and d.proposal is not None)
    d.resize(800,700);settle(app)
    assert page.topic.text() in d.intro.text()
    assert d.detail.height()>200
    for w in (d.detail,d.options,d.apply_button,d.cancel):
        assert d.rect().contains(QRect(w.mapTo(d,w.rect().topLeft()),w.size()))
