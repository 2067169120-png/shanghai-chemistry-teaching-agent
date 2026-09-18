from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import json
import pytest
from test_lesson_design import sample
from test_desktop_preparation_drafts import _record
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore
from integrations.deeptutor_shchem_v1.desktop_backup import (plan_backup, create_backup, inspect_backup,
    restore_backup, manifest_revision, _allowed, summary_text, BackupError)
from integrations.deeptutor_shchem_v1.desktop_lesson_output import export_design, checked_file, FILES
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftService


def fixture(root, *, recovery_only=False):
    state=DesktopStateStore(root)
    facade=SimpleNamespace(paths=SimpleNamespace(task_root=root/'tasks'))
    p=sample();bundle=export_design(facade,p);p['lesson_design']['exports'].append(bundle)
    if recovery_only:
        PreparationRecoveryStore(root).save(p,dirty=True,sequence=1)
    else:
        record=_record(p);record['lesson_design']=deepcopy(p['lesson_design'])
        state.save_draft('lesson-101',record)
    return state,facade,p,bundle


def test_all_three_files_restore_and_open_without_original_folder(tmp_path):
    state,f,p,out=fixture(tmp_path/'original')
    before={name:checked_file(f,p['lesson_design'],out['id'],name).read_bytes() for name in FILES}
    plan=plan_backup(state.root,include_tasks=True)
    assert plan.summary['node_output_bundles']==1 and plan.summary['node_output_files']==3
    archive=tmp_path/'backup.zip';create_backup(plan,archive);report=inspect_backup(archive)
    restored=tmp_path/'restored';restore_backup(archive,restored,expected_manifest=manifest_revision(report))
    loaded=PreparationDraftService._payload(DesktopStateStore(restored).snapshot()['drafts']['lesson-101'])
    f2=SimpleNamespace(paths=SimpleNamespace(task_root=restored/'tasks'))
    for name in FILES:assert checked_file(f2,loaded['lesson_design'],out['id'],name).read_bytes()==before[name]
    assert '1 套完整文件 / 3 个文件' in summary_text(report)
    with ZipFile(archive) as z:assert not any('actual-pptx' in n or n.endswith('output.json') for n in z.namelist())


def test_not_selected_outputs_are_reported_not_silently_included(tmp_path):
    state,f,p,out=fixture(tmp_path/'original');plan=plan_backup(state.root)
    assert plan.summary['node_output_files']==0
    assert any(r['kind']=='lesson_output' and r['key']==out['id'] for r in plan.references)
    assert not any('/node-exports/' in r.name for r in plan.files)


def test_recovery_only_output_and_duplicate_references(tmp_path):
    state,f,p,out=fixture(tmp_path/'original',recovery_only=True)
    plan=plan_backup(state.root,include_tasks=True)
    assert plan.summary['node_output_files']==3
    record=_record(p);record['lesson_design']=deepcopy(p['lesson_design']);state.save_draft('lesson-101',record)
    assert plan_backup(state.root,include_tasks=True).summary['node_output_files']==3


def test_missing_output_and_modified_output_are_distinguished(tmp_path):
    state,f,p,out=fixture(tmp_path/'original')
    file=checked_file(f,p['lesson_design'],out['id'],FILES[0]);raw=file.read_bytes();file.unlink()
    plan=plan_backup(state.root,include_tasks=True)
    assert plan.summary['node_output_bundles']==0 and plan.summary['missing_files']==1
    file.write_bytes(raw+b'changed')
    with pytest.raises(BackupError,match='不一致'):plan_backup(state.root,include_tasks=True)


def test_unregistered_bundle_or_other_file_never_copied(tmp_path):
    state,f,p,out=fixture(tmp_path/'original')
    other=export_design(f,p)
    (f.paths.task_root/'preparation-v1/node-exports'/out['id']/'private.txt').write_text('not in record')
    plan=plan_backup(state.root,include_tasks=True)
    assert all(other['id'] not in r.name and not r.name.endswith('private.txt') for r in plan.files)
    assert not _allowed('tasks/preparation-v1/node-exports/'+out['id']+'/private.txt')
    assert not _allowed('tasks/preparation-v1/node-exports/'+out['id']+'/output.json')

