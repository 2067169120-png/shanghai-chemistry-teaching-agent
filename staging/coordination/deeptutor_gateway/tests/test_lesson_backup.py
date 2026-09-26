"""A08 first delivery: real saved lesson state, checked bytes, independent restore."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_backup import (
    BackupError, BackupCancelled, MARKER, PREP, _bytes, plan_backup, create_backup,
    inspect_backup, manifest_revision, restore_backup, missing_lesson_images,
    reconnect_lesson_image, restored_profile,
)
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore
from integrations.deeptutor_shchem_v1.desktop_preparation_images import PreparationImageStore
from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftService
from integrations.deeptutor_shchem_v1.desktop_preparation import DesktopPreparationManager
from test_desktop_preparation_drafts import _payload, _record
from test_desktop_preparation import RecordingProvider, FileRenderer, _payload as task_payload
from test_phase_a_core import saved_state


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def seed(root):
    root.mkdir()
    state = DesktopStateStore(root)
    original = io.BytesIO(); Image.new('RGB', (16, 12)).save(original, format='PNG')
    data = original.getvalue()
    store = PreparationImageStore(root / PREP / 'images')
    asset = store.import_bytes(data, '合成图', '软件测试', '验证精确恢复')
    record = _record(_payload()); record['image_assets'] = [asset]
    for name in ('current', 'archived', 'trash'):
        state.save_draft('prep-' + name, record)
    state._update(lambda s: s.update(work_organization={
        'draft:prep-archived': {'shelf': 'archived', 'title': '明年再讲'},
        'draft:prep-trash': {'shelf': 'trash', 'previous_shelf': 'archived', 'title': '仍可找回'},
    }))
    state.save_draft('student-private', {'kind':'student-analysis', 'student_id':'private-student-marker'})
    state.add_to_basket({'key':'synthetic-original-question', 'item_kind':'core_theme', 'title_zh':'完整主题'})
    payload = {**_payload(), 'topic':'未完成', 'materials':'', 'image_assets':[asset]}
    recovery = PreparationRecoveryStore(root)
    recovery.save(payload, dirty=True, sequence=1)
    (root/'model-settings').mkdir(); (root/'model-settings/private.json').write_text('not-for-backup')
    return state, asset, data


def roundtrip(root, tmp_path, **options):
    plan = plan_backup(root, **options)
    archive = tmp_path/'backup.zip'
    create_backup(plan, archive)
    report = inspect_backup(archive)
    restored = tmp_path/'restored'
    restore_backup(archive, restored, expected_manifest=manifest_revision(report))
    return plan, archive, report, restored


def test_default_roundtrip_retains_identity_shelves_recovery_and_excludes_private_domains(tmp_path):
    state, asset, _ = seed(tmp_path/'original')
    before = files(state.root)
    plan, archive, report, restored = roundtrip(state.root, tmp_path)
    assert plan.summary['drafts'] == 3 and plan.summary['images_included'] == 0
    assert files(state.root) == before
    out = DesktopStateStore(restored).snapshot()
    assert out['drafts'] == {k:v for k,v in state.snapshot()['drafts'].items() if k.startswith('prep-')}
    assert out['work_organization'] == state.snapshot()['work_organization']
    assert out['basket'] == state.basket()
    assert PreparationRecoveryStore(restored).load() == PreparationRecoveryStore(state.root).load()
    assert missing_lesson_images(restored)[0]['asset_id'] == asset['asset_id']
    assert restored_profile(restored) == restored
    with ZipFile(archive) as z:
        assert not any('model-settings' in n or 'student' in n for n in z.namelist())
        assert b'private-student-marker' not in z.read('files/desktop-state.v1.json')
    assert report['summary']['basket_references'] == 1


def test_opt_in_images_are_bytes_not_merely_references_and_can_be_loaded(tmp_path):
    state, asset, data = seed(tmp_path/'original')
    _, _, report, restored = roundtrip(state.root, tmp_path, include_images=True)
    assert report['summary']['images_included'] == 1
    assert PreparationImageStore(restored/PREP/'images').load(asset) == data
    assert not missing_lesson_images(restored)


def test_terminal_task_and_registered_artifacts_can_be_opened_after_restore(tmp_path):
    state, _, _ = seed(tmp_path/'original')
    manager = DesktopPreparationManager(state.root/PREP, FileRenderer())
    task = manager.prepare(task_payload(output_kind='ppt'), 'LOCAL', 'REV')
    result = manager.run(task['task_id'], RecordingProvider(), lambda _:None, lambda:False)
    assert result['status']=='completed'
    before = files(state.root)
    plan, _, report, restored = roundtrip(state.root, tmp_path, include_images=True, include_tasks=True)
    assert report['summary']['tasks']==1 and files(state.root)==before
    other = DesktopPreparationManager(restored/PREP, FileRenderer())
    restored_path, _ = other.artifact_path(task['task_id'], 'pptx')
    original_path, _ = manager.artifact_path(task['task_id'], 'pptx')
    assert restored_path.read_bytes() == original_path.read_bytes()


def test_active_task_is_not_recovered_or_modified_by_backup(tmp_path):
    state, _, _ = seed(tmp_path/'original')
    manager = DesktopPreparationManager(state.root/PREP, FileRenderer())
    task = manager.prepare(task_payload(), 'LOCAL', 'REV')
    value = manager._read_task(task['task_id']); value['status']='running'; manager._write_task(value)
    before = files(state.root)
    plan, _, _, restored = roundtrip(state.root, tmp_path, include_tasks=True)
    assert plan.summary['tasks']==0 and files(state.root)==before
    assert not list((restored/PREP/'tasks').glob('*'))


def test_missing_file_is_visible_and_hash_drift_does_not_pass_as_missing(tmp_path):
    state, asset, _ = seed(tmp_path/'original')
    picture = state.root/PREP/'images'/(asset['sha256']+'.image')
    picture.unlink()
    plan = plan_backup(state.root, include_images=True)
    assert plan.summary['missing_files']==1 and plan.summary['images_included']==0
    picture.write_bytes(b'wrong-image')
    with pytest.raises(ValueError): plan_backup(state.root, include_images=True)


def test_reconnect_requires_identical_content_and_preserves_original_records(tmp_path):
    state, asset, data = seed(tmp_path/'original')
    picture = state.root/PREP/'images'/(asset['sha256']+'.image'); picture.unlink()
    source = tmp_path/'original.png'; source.write_bytes(data)
    before_state = state.path.read_bytes(); before_recovery=PreparationRecoveryStore(state.root).path.read_bytes()
    reconnect_lesson_image(state.root, asset, source)
    assert picture.read_bytes()==data
    assert state.path.read_bytes()==before_state and PreparationRecoveryStore(state.root).path.read_bytes()==before_recovery
    assert reconnect_lesson_image(state.root, asset, source)['already_present']
    wrong = tmp_path/'same-name.png'; wrong.write_bytes(b'not-the-original')
    with pytest.raises(BackupError, match='不一致'): reconnect_lesson_image(state.root, asset, wrong)
    assert picture.read_bytes()==data


def test_backup_refuses_existing_destination_and_late_source_change(tmp_path):
    state, asset, _ = seed(tmp_path/'original')
    plan=plan_backup(state.root,include_images=True)
    out=tmp_path/'exists.zip'; out.write_bytes(b'old-backup')
    with pytest.raises(BackupError,match='已存在'):create_backup(plan,out)
    assert out.read_bytes()==b'old-backup'
    (state.root/PREP/'images'/(asset['sha256']+'.image')).write_bytes(b'changed')
    with pytest.raises(BackupError,match='变化'):create_backup(plan,tmp_path/'new.zip')
    assert not (tmp_path/'new.zip').exists() and not list(tmp_path.glob('.shchem-backup-*'))


def test_restore_refuses_existing_directory_and_stale_confirmation(tmp_path):
    state, _, _=seed(tmp_path/'original')
    plan=plan_backup(state.root); out=tmp_path/'b.zip'; create_backup(plan,out)
    manifest=inspect_backup(out)
    with pytest.raises(BackupError,match='已存在'):
        restore_backup(out,state.root,expected_manifest=manifest_revision(manifest))
    with pytest.raises(BackupError,match='变化'):
        restore_backup(out,tmp_path/'new',expected_manifest='f'*64)
    assert not (tmp_path/'new').exists()


def rewrite(source, target, edit):
    with ZipFile(source) as z: entries={n:z.read(n) for n in z.namelist()}
    edit(entries)
    with ZipFile(target,'w',ZIP_DEFLATED) as z:
        for k,v in entries.items():z.writestr(k,v)


@pytest.mark.parametrize('name', ['../outside','/absolute','C:/outside','files/../../escape','files/CON.txt','files/model-settings/x.json','files/tasks/preparation-v1/artifacts/PREP-'+'a'*32+'/attempt-0001/run.exe'])
def test_undeclared_or_unsafe_files_rejected(tmp_path,name):
    state,_,_=seed(tmp_path/'original');good=tmp_path/'good.zip';create_backup(plan_backup(state.root),good)
    bad=tmp_path/'bad.zip';rewrite(good,bad,lambda rows:rows.update({name:b'bad'}))
    with pytest.raises(BackupError):inspect_backup(bad)


def test_truncated_corrupt_and_tampered_files_do_not_restore(tmp_path):
    state,_,_=seed(tmp_path/'original');good=tmp_path/'good.zip';create_backup(plan_backup(state.root),good)
    manifest=inspect_backup(good)
    truncated=tmp_path/'truncated.zip';truncated.write_bytes(good.read_bytes()[:40])
    with pytest.raises(BackupError):inspect_backup(truncated)
    bad=tmp_path/'bad.zip'
    rewrite(good,bad,lambda rows: rows.update({'files/desktop-state.v1.json': rows['files/desktop-state.v1.json'].replace('未完成'.encode(),'未完了'.encode())}))
    # State may not contain that word; corrupt a guaranteed ASCII schema token.
    rewrite(good,bad,lambda rows: rows.update({'files/desktop-state.v1.json': rows['files/desktop-state.v1.json'].replace(b'current',b'currenX')}))
    with pytest.raises(BackupError):inspect_backup(bad)
    with pytest.raises(BackupError):restore_backup(bad,tmp_path/'new',expected_manifest=manifest_revision(manifest))
    assert not (tmp_path/'new').exists() and not list(tmp_path.glob('.shchem-restore-*'))


def test_cancel_and_failed_write_leave_original_and_old_backup_untouched(tmp_path,monkeypatch):
    state,_,_=seed(tmp_path/'original');before=files(state.root)
    plan=plan_backup(state.root);out=tmp_path/'new.zip'
    with pytest.raises(BackupCancelled):create_backup(plan,out,cancel=lambda:True)
    assert not out.exists() and files(state.root)==before
    import integrations.deeptutor_shchem_v1.desktop_backup as module
    monkeypatch.setattr(module,'_publish_file',lambda *a: (_ for _ in ()).throw(OSError('full')))
    with pytest.raises(BackupError):create_backup(plan,out)
    assert not out.exists() and not list(tmp_path.glob('.shchem-backup-*'))


def test_all_501_drafts_survive_restore_and_oldest_is_searchable(tmp_path):
    state=saved_state(tmp_path/'state')
    _,_,_,restored=roundtrip(state.root,tmp_path)
    result=PreparationDraftService(DesktopStateStore(restored)).search(query='课题-0000')
    assert result['total']==1 and result['items'][0]['draft_id']=='prep-0000'


def test_future_schema_and_symlink_are_not_followed(tmp_path):
    state,asset,_=seed(tmp_path/'original');out=tmp_path/'future.zip'
    good=tmp_path/'good.zip';create_backup(plan_backup(state.root),good)
    def change(entries):
        value=json.loads(entries['manifest.json']);value['schema_version']='future';entries['manifest.json']=_bytes(value)
    rewrite(good,out,change)
    with pytest.raises(BackupError,match='版本'):inspect_backup(out)
    image=state.root/PREP/'images'/(asset['sha256']+'.image');data=image.read_bytes();image.unlink()
    other=tmp_path/'external';other.write_bytes(data)
    try:image.symlink_to(other)
    except OSError: return  # Windows non-admin host: other unsafe paths still tested.
    with pytest.raises(BackupError,match='链接'):plan_backup(state.root,include_images=True)
