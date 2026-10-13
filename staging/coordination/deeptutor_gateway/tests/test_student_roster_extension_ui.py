"""Actual Qt import -> reopen -> bind -> release -> undo -> review -> export."""
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
pytest.importorskip('PySide6')
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError, digest
from integrations.deeptutor_shchem_v1.desktop_exam_ai import model_payload
from integrations.deeptutor_shchem_v1.desktop_exam_followup import create_followup, link_basket, record_attempt
from integrations.deeptutor_shchem_v1.desktop_exam_revision import task_freshness
from integrations.deeptutor_shchem_v1.desktop_workbench.student_roster_dialog import StudentRosterDialog
from test_student_roster_ui import dashboard, editor, attach_through_button, choose_member, save_dialog, settle
from test_student_roster_import import HEADERS, csv_file
from test_student_roster_import_ui import map_and_preview, decide, confirm


def bind_in_ui(desk,app,member_id):
    desk.bind_student_button.click();settle(app)
    dialog=desk._identity_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    choose_member(dialog,member_id)
    dialog.reason.setPlainText('合成教师核对原成绩行、稳定成员和原作答')
    save_dialog(dialog);settle(app)
    assert dialog.result()==QDialog.DialogCode.Accepted


def action_in_ui(desk,app,mode,*,confirm_action=True):
    button=desk.unbind_student_button if mode=='unbind' else desk.undo_identity_button
    button.click();settle(app)
    dialog=desk._identity_action_dialog
    dialog.row.setCurrentIndex(dialog.row.findData('S0001'))
    if mode=='undo':
        index=next(i for i in range(1,dialog.action.count()) if dialog.action.itemData(i)['action']=='unbind_row')
        dialog.action.setCurrentIndex(index)
    dialog.reason.setPlainText('合成教师复核原行及归属后'+('解除' if mode=='unbind' else '撤销解除'))
    dialog.confirm.setChecked(confirm_action)
    return dialog


def test_parent_import_button_saves_and_reopens_then_full_identity_loop_and_anonymous_export(dashboard,tmp_path,monkeypatch):
    desk,app,roster,store,original=dashboard
    source_path=tmp_path/'synthetic.xlsx';source_bytes=source_path.read_bytes()
    store.submission=lambda **kwargs:{'student_id':'anonymous-A','submission_id':'work-A','answer':'原合成作答'}
    desk.facade.student_submissions=lambda **kwargs:(SimpleNamespace(submission_id='work-A',created_at='2026-10-13'),)
    roster_editor=StudentRosterDialog(desk.facade)
    roster_editor.show();settle(app)
    roster_editor.import_button.click();settle(app)
    incoming=roster_editor._import_dialog
    path=csv_file(tmp_path,[HEADERS,['PRIVATE_UPDATED_ALIAS',roster['members'][0]['id'],'anonymous-A','001'],
        ['PRIVATE_SAME_ALIAS','','','002'],['PRIVATE_SAME_ALIAS','','','003'],['排除未知UUID','f'*32,'','004']])
    original_list=path.read_bytes()
    map_and_preview(incoming,path)
    decide(incoming,2,'update');decide(incoming,3,'new');decide(incoming,4,'new');decide(incoming,5,'exclude')
    confirm(incoming);incoming.save_button.click();settle(app)
    saved=store.load(roster['id'])
    assert incoming.result()==QDialog.DialogCode.Accepted
    assert roster_editor.draft==saved and roster_editor.saved and not roster_editor.dirty
    assert saved['members'][0]['id']==roster['members'][0]['id']
    assert saved['members'][0]['profile_student_id']=='anonymous-A'
    same=[member for member in saved['members'] if member['local_alias']=='PRIVATE_SAME_ALIAS']
    assert len(same)==2 and same[0]['id']!=same[1]['id']
    assert [(value['source_row'],value['action']) for value in saved['import_history'][-1]['rows']]==[(2,'update'),(3,'new'),(4,'new'),(5,'exclude')]
    roster_editor.reject();roster_editor.deleteLater()
    reopened=StudentRosterDialog(desk.facade)
    try:
        reopened.show();settle(app)
        assert reopened.draft==saved and reopened.members.rowCount()==4
    finally:reopened.reject();reopened.deleteLater()
    attach_through_button(desk,monkeypatch)
    assert desk.exam['roster_snapshots'][0]['roster']==saved
    bind_in_ui(desk,app,saved['members'][0]['id'])
    # Explicitly associate the existing anonymous original answer through Qt.
    desk.bind_student_button.click();settle(app)
    binding=desk._identity_dialog
    binding.row.setCurrentIndex(binding.row.findData('S0001'))
    binding.submission.setCurrentIndex(binding.submission.findData('work-A'))
    binding.reason.setPlainText('合成教师再次核对原作答归属');save_dialog(binding);settle(app)
    baseline=deepcopy(desk.bundle())
    task=create_followup(desk.exam,'1',['S0001'],'合成核对条件',date.today().isoformat())
    task=link_basket(task,[{'key':'synthetic-owned-question','title_zh':'合成复测','source_zh':'合成夹具'}],['synthetic-owned-question'])
    task=record_attempt(task,'S0001',date.today().isoformat(),'completed',8,10,'合成已完成观察',submission_id='work-A',roster_store=store)
    desk.followups=[task];assert desk.save_current();desk.render();desk.followup_panel.refresh(task['id'])
    before=deepcopy(desk.bundle());old_binding=next(row for row in desk.exam['students'] if row['id']=='S0001')['identity_binding']
    released=action_in_ui(desk,app,'unbind');released.save_button.click();settle(app)
    assert released.result()==QDialog.DialogCode.Accepted
    assert task_freshness(desk.exam,desk.followups[0])['state']=='blocked'
    assert desk.followups==before['followups'] and desk.exam['roster_snapshots']==before['exam']['roster_snapshots']
    assert len(desk.exam['students'])==len(before['exam']['students'])+1
    identity=desk.exam['id'];desk.open_saved(identity,task['id']);settle(app)
    undo=action_in_ui(desk,app,'undo');undo.save_button.click();settle(app)
    assert undo.result()==QDialog.DialogCode.Accepted
    row=next(value for value in desk.exam['students'] if value['id']=='S0001')
    assert row['scores']==original['students'][0]['scores'] and row['excel_row']==original['students'][0]['excel_row']
    assert row['identity_binding']['member_id']==old_binding['member_id']
    assert row['identity_binding']['binding_revision']!=old_binding['binding_revision']
    assert desk.followups==before['followups'] and task_freshness(desk.exam,desk.followups[0])['state']=='stale'
    assert len(desk.exam['students'])==len(before['exam']['students'])
    desk.followup_panel.refresh(task['id']);desk.followup_panel.evidence_button.click();settle(app)
    review=desk.followup_panel._evidence_dialog
    review.reason.setText('合成教师核对新身份依据、保留原复练安排')
    review.confirmed.setChecked(True);review.commit_button.click();settle(app)
    assert review.result()==QDialog.DialogCode.Accepted
    assert task_freshness(desk.exam,desk.followups[0])['state']=='current'
    assert desk.followups[0]['attempts']==task['attempts'] and desk.followups[0]['_baseline_evidence']==task['_baseline_evidence']
    output=tmp_path/'current-anonymous.html'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(output),'HTML'))
    monkeypatch.setattr(QMessageBox,'question',lambda *args,**kwargs:QMessageBox.StandardButton.No)
    desk.export_button.click();settle(app)
    exported=output.read_text(encoding='utf-8')
    payload=json.dumps(model_payload(desk.report,{},'',True),ensure_ascii=False)
    private=[saved['label'],saved['id'],saved['revision'],'PRIVATE_UPDATED_ALIAS','PRIVATE_SAME_ALIAS','anonymous-A','work-A',*[member['id'] for member in saved['members']]]
    assert all(value not in text for value in private for text in (exported,payload))
    assert '复练' in exported and 'S0001' in exported
    desk.open_saved(identity,task['id']);settle(app)
    assert task_freshness(desk.exam,desk.followups[0])['state']=='current'
    assert source_path.read_bytes()==source_bytes and path.read_bytes()==original_list
    assert baseline['exam']['students'][0]['scores']==row['scores']


def test_import_entry_keeps_existing_unsaved_teacher_input(editor,tmp_path):
    dialog,app,facade,store=editor
    from integrations.deeptutor_shchem_v1.desktop_student_roster import new_roster,new_member
    roster=new_roster('已存合成班级');roster['members']=[new_member('已存合成成员')]
    roster=store.save(roster);dialog.refresh(roster['id'])
    dialog.members.selectRow(0);dialog.alias.setText('待保存的教师输入');dialog.edit_button.click()
    before=deepcopy(dialog.draft);disk=store.path.read_bytes()
    dialog.import_button.click();settle(app)
    assert dialog.dirty and dialog.draft==before and '请先保存' in dialog.status.text()
    assert store.path.read_bytes()==disk and not hasattr(dialog,'_import_dialog')


@pytest.mark.parametrize('failure',['confirmation','cancel','save','disk_cas','window_change'])
def test_action_dialog_failure_preserves_teacher_input_and_disk(dashboard,monkeypatch,failure):
    desk,app,roster,_,_=dashboard
    attach_through_button(desk,monkeypatch);bind_in_ui(desk,app,roster['members'][0]['id'])
    dialog=action_in_ui(desk,app,'unbind',confirm_action=failure!='confirmation')
    before=deepcopy(desk.bundle());disk=desk.store.load(desk.exam['id']);reason=dialog.reason.toPlainText()
    if failure=='cancel':
        dialog.reject();assert desk.bundle()==before and desk.store.load(desk.exam['id'])==disk;return
    if failure=='save':
        def fail(*args,**kwargs):raise ExamError('合成保存失败，当前输入保留')
        monkeypatch.setattr(desk.store,'save',fail)
    elif failure=='disk_cas':
        disk['notes']='其他窗口最新说明';desk.store.save(disk,expected_revision=digest(before))
    elif failure=='window_change':
        desk.notes.setPlainText('主窗口新输入');before=deepcopy(desk.bundle())
    dialog.save_button.click();settle(app)
    assert dialog.isVisible() and dialog.result()!=QDialog.DialogCode.Accepted
    assert dialog.reason.toPlainText()==reason and dialog.row.currentData()=='S0001'
    assert desk.bundle()==before and desk.store.load(desk.exam['id'])==disk
    assert dialog.status.text();dialog.reject()


def test_action_dialog_visual_evidence(dashboard,monkeypatch,tmp_path,request):
    desk,app,roster,_,_=dashboard
    attach_through_button(desk,monkeypatch);bind_in_ui(desk,app,roster['members'][0]['id'])
    previous=QFont(app.font());font_id=QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    assert font_id>=0;app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],10))
    request.addfinalizer(lambda:app.setFont(previous))
    dialog=action_in_ui(desk,app,'unbind')
    output=Path(os.environ.get('SHCHEM_ROSTER_EXTENSION_QA_ROOT',str(tmp_path/'visual-qa')));output.mkdir(parents=True,exist_ok=True)
    for size in [(760,600),(680,540)]:
        dialog.resize(*size);settle(app);assert dialog.grab().save(str(output/f'identity-unbind-{size[0]}x{size[1]}.png'))
    dialog.save_button.click();settle(app)
    undo=action_in_ui(desk,app,'undo');settle(app)
    assert undo.grab().save(str(output/'identity-undo-760x600.png'));undo.reject()
