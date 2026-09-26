"""Existing completed result -> reviewed nodes -> saved/restored real outputs."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


def run_probe(output, *, lite=False):
    from PySide6.QtCore import QTimer, Qt, QRect
    from .desktop_facade import build_default_facade
    from .desktop_paths import DesktopPaths
    from .desktop_version import DESKTOP_VERSION
    from .desktop_backup_probe import _candidate
    from .desktop_lesson_probe import sample_payload
    from .desktop_lesson_output import checked_file, FILES, actual_ppt_preview
    from .desktop_lesson_design import coverage, content_fingerprint
    from .desktop_backup import plan_backup, create_backup, inspect_backup, restore_backup, manifest_revision, summary_text
    from .desktop_preparation_drafts import PreparationDraftService
    from .desktop_editor_recovery import PreparationRecoveryStore
    from .desktop_workbench.app import create_application
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    from .desktop_workbench.lesson_design_dialog import LessonDesignDialog
    from .desktop_workbench.lesson_import_dialog import LessonImportDialog
    from .desktop_workbench.preparation_recovery import apply_editor_payload
    from PySide6.QtWidgets import QApplication
    app=create_application(['lesson-import-acceptance'])
    target=Path(output);target.mkdir(parents=True,exist_ok=True)
    shots=[];errors=[];evidence={}
    previous=sys.excepthook
    sys.excepthook=lambda typ,error,tb:errors.append(typ.__name__+': '+str(error))
    def settle(test=lambda:True):
        deadline=time.monotonic()+60
        for _ in range(5):app.processEvents();time.sleep(.025)
        while not test() and time.monotonic()<deadline:app.processEvents();time.sleep(.025)
        assert test(), 'Lesson import did not finish'
        assert not errors, errors
    def capture(widget,name):
        path=target/name;assert widget.grab().save(str(path))
        shots.append({'file':name,'sha256':sha256(path.read_bytes()).hexdigest()})
    with tempfile.TemporaryDirectory(prefix='lesson101-') as folder:
        folder=Path(folder)
        workspace=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        facade=build_default_facade(DesktopPaths.from_workspace(workspace,state_root=folder/'state'))
        win=TeacherWorkbenchWindow(facade);win.resize(1366,820);win.show();win.navigate('preparation')
        page=win.preparation_page
        payload=sample_payload();apply_editor_payload(page,payload)
        manager=facade._preparation_manager_instance()
        seed={**payload,'topic':'氧化还原反应复习（合成转换验收）','objective':'识别氧化剂与还原剂\n应用电子守恒'}
        seed.pop('lesson_design')
        task=manager.prepare(seed,'LOCAL-SYNTHETIC-ONLY','REV-1')
        fixture_calls=[]
        def provider(*a,**kw):fixture_calls.append(1);return _candidate()
        completed=manager.run(task['task_id'],provider,lambda _:None,lambda:False)
        assert completed['status']=='completed',completed
        original=facade.preparation_revision_source(task['task_id'])
        page._preparation_task_id=task['task_id']
        editor=LessonDesignDialog(page);editor.show();settle()
        before=deepcopy(editor.history.value)
        def drive_import():
            dialog=next((w for w in QApplication.topLevelWidgets() if isinstance(w,LessonImportDialog) and w.isVisible()),None)
            try:
                assert dialog is not None
                settle(lambda:not dialog.busy and dialog.sources.count()==1)
                dialog.read.click();settle(lambda:not dialog.busy and dialog.proposal is not None)
                assert dialog.options.count()==3
                dialog.options.item(2).setCheckState(Qt.CheckState.Unchecked)
                capture(dialog,'lesson-import-preview.png')
                dialog.resize(800,700);settle()
                assert dialog.detail.height()>200
                for w in (dialog.options,dialog.detail,dialog.apply_button,dialog.cancel):
                    assert dialog.rect().contains(QRect(w.mapTo(dialog,w.rect().topLeft()),w.size()))
                capture(dialog,'lesson-import-compact.png')
                dialog.apply_button.click();settle(lambda:not dialog.busy)
                assert dialog.result_value is not None,dialog.status.text()
            except Exception as exc:
                errors.append(type(exc).__name__+': '+str(exc))
                if dialog:dialog.busy=False;dialog.reject()
        try:
            QTimer.singleShot(100,drive_import);editor.import_candidate.click();settle()
            assert len(editor.history.value['nodes'])==len(before['nodes'])+2
            assert editor.history.value['nodes'][0]==before['nodes'][0]
            assert all(n['locked'] and not n['confirmed'] for n in editor.history.value['nodes'][1:])
            imported=deepcopy(editor.history.value)
            editor.travel(False);assert editor.history.value==before
            editor.travel(True);assert editor.history.value==imported
            editor.fields['teacher_action'].setPlainText('教师修订：请学生逐步说明证据，保留原材料。')
            assert editor.history.value['nodes'][1]['material_text']==imported['nodes'][1]['material_text']
            editor.generate.click();settle(lambda:not editor.busy)
            assert editor.history.value['exports'],editor.status.text()
            bundle=editor.history.value['exports'][-1]
            assert bundle['fingerprint']==content_fingerprint(page._payload())
            editor.save.click();settle(lambda:not editor.busy)
            assert '已保存' in editor.status.text()
            capture(editor,'lesson-import-linked.png')
            editor.reject();settle()
            original_after=facade.preparation_revision_source(task['task_id'])
            assert original_after==original and len(fixture_calls)==1
            service=PreparationDraftService(facade.state_store)
            item=service.search()['items'][0]
            saved=service.load(item['draft_id'],item['revision'])['payload']
            assert saved['lesson_design']==page._lesson_design
            assert PreparationRecoveryStore(facade.paths.state_root).load()['payload']['lesson_design']==saved['lesson_design']
            backup=plan_backup(facade.paths.state_root,include_images=True,include_tasks=True)
            assert backup.summary['node_output_bundles']==1 and backup.summary['node_output_files']==3
            archive=target/'synthetic-lesson-backup.zip';create_backup(backup,archive)
            checked=inspect_backup(archive);restored=folder/'restored'
            restore_backup(archive,restored,expected_manifest=manifest_revision(checked))
            recovered=build_default_facade(DesktopPaths.from_workspace(workspace,state_root=restored))
            try:
                again=PreparationDraftService(recovered.state_store).load(item['draft_id'],item['revision'])['payload']
                assert again==saved
                for name in FILES:
                    path=checked_file(recovered,again['lesson_design'],bundle['id'],name)
                    assert path.read_bytes()==checked_file(facade,saved['lesson_design'],bundle['id'],name).read_bytes()
                    shutil.copyfile(path,target/name)
                assert recovered.preparation_revision_source(task['task_id'])==original
                if not lite:
                    preview=actual_ppt_preview(recovered,again['lesson_design'],bundle['id'])
                    shutil.copyfile(preview['path'],target/'restored-actual-pptx.pdf')
                evidence.update(source_task_unchanged=True,original_nodes_preserved=True,
                    selected_nodes_only=True,teacher_confirmation_not_inferred=True,undo_redo=True,
                    locked_material_unchanged=True,three_outputs_generated=True,
                    saved_draft_and_recovery=True,three_files_restored=True,restored_source_readable=True,
                    compact_content_visible=True,no_extra_provider_calls=True)
            finally:recovered.shutdown()
            (target/'backup-summary.txt').write_text(summary_text(checked),encoding='utf-8')
            (target/'lesson-import-probe.json').write_text(json.dumps({
                'version':DESKTOP_VERSION,'source_commit':os.environ.get('GITHUB_SHA','local'),
                'frozen':bool(getattr(sys,'frozen',False)),'qt_platform':app.platformName(),
                'checks':evidence,'uncaught_errors':errors,'network_requests':0,
                'fixture_calls':len(fixture_calls),'import_model_calls':0,'actual_restored_pptx':not lite,
                'screenshots':shots,'scope':'Synthetic completed task from fixed local callback; no real model grading or lesson-quality evaluation.'
            },ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            if errors:(target/'errors.json').write_text(json.dumps(errors,ensure_ascii=False),encoding='utf-8')
            editor.busy=False;editor.reject();win.close();app.processEvents();sys.excepthook=previous
    return 0
