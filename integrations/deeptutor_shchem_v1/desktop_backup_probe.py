"""Synthetic acceptance only; called explicitly by CI/source and EXE probes."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import io
from pathlib import Path
from typing import Any

def _candidate() -> dict[str, Any]:
    return {'title': '氧化还原反应：守恒与迁移', 'objectives': [{'statement': '能从化合价变化识别氧化剂和还原剂。'}, {'statement': '能用电子守恒完成定量推理。'}], 'activities': [{'title': '证据辨析', 'objective_numbers': [1], 'minutes': 10, 'teacher_action': '呈现反应并追问化合价变化。', 'student_action': '标注化合价并说明判断依据。', 'materials': ['教师提供的反应实例']}, {'title': '守恒迁移', 'objective_numbers': [2], 'minutes': 30, 'teacher_action': '组织分步列式并比较两种解法。', 'student_action': '独立计算后互相核对电子得失。', 'materials': ['教师提供的迁移题']}], 'assessments': [{'title': '概念出口条', 'objective_numbers': [1], 'activity_numbers': [1], 'evidence_of_learning': '学生能写出判断及依据。', 'success_criteria': ['氧化剂判断正确', '依据包含化合价变化']}, {'title': '守恒计算检查', 'objective_numbers': [2], 'activity_numbers': [2], 'evidence_of_learning': '学生的电子得失和数量关系闭合。', 'success_criteria': ['电子守恒关系正确', '单位完整']}], 'slides': [{'title': '课题与目标', 'purpose': '建立学习方向', 'objective_numbers': [1, 2], 'activity_numbers': [], 'assessment_numbers': [], 'minutes': 5, 'content': ['识别角色', '应用电子守恒'], 'teacher_notes': '先让学生说出已有判断方法。'}, {'title': '从证据到守恒', 'purpose': '完成核心学习活动', 'objective_numbers': [1, 2], 'activity_numbers': [1, 2], 'assessment_numbers': [1], 'minutes': 25, 'content': ['化合价变化是可见证据', '电子得失必须守恒'], 'teacher_notes': '化学事实和例题数据使用前由教师复核。'}, {'title': '检测与作业', 'purpose': '收集学习证据并迁移', 'objective_numbers': [2], 'activity_numbers': [2], 'assessment_numbers': [2], 'minutes': 10, 'content': ['先独立作答', '再核对守恒关系'], 'teacher_notes': '不得表述为官方评分点。'}], 'lesson_stages': [{'title': '证据建模', 'objective_numbers': [1], 'activity_numbers': [1], 'assessment_numbers': [1], 'minutes': 15, 'teacher_action': '提出问题并记录学生依据。', 'student_action': '完成标注、交流与修正。', 'materials': ['反应实例'], 'assessment': '依据出口条即时调整追问。'}, {'title': '守恒迁移', 'objective_numbers': [2], 'activity_numbers': [2], 'assessment_numbers': [2], 'minutes': 25, 'teacher_action': '组织独立作答和同伴核验。', 'student_action': '列式、计算并说明数量关系。', 'materials': ['迁移题'], 'assessment': '检查电子守恒、计算与单位。'}], 'homework': {'title': '课后迁移', 'tasks': [{'instruction': '完成一道同结构迁移题并写出守恒依据。', 'objective_numbers': [2]}], 'estimated_minutes': 15}, 'uncertainties': [{'field': 'example_data', 'description': '具体例题数据需由教师补入并核对。', 'teacher_action': '授课前核对题面、答案与单位。'}]}

def exercise(window, settle, capture, output):
    from PIL import Image
    from docx import Document
    from pptx import Presentation
    from PySide6.QtWidgets import QFileDialog
    from .desktop_backup import PREP, missing_lesson_images
    from .desktop_paths import DesktopPaths
    from .desktop_facade import build_default_facade
    from .desktop_preparation_renderer import NativePreparationRenderer
    from .desktop_workbench import backup_dialog as dialog_module
    from .desktop_workbench.backup_dialog import BackupDialog
    from .desktop_workbench.main_window import TeacherWorkbenchWindow
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    facade=window.facade;prep=window.preparation_page
    prep.topic.setText('合成备份验收课题')
    prep.audience.setText('软件验收')
    prep.materials.setPlainText('合成材料：恢复后仍保留公共材料，不用于正式授课。')
    image=io.BytesIO();Image.new('RGB',(80,60),'white').save(image,format='PNG')
    original=output/'原始合成图片.png';original.write_bytes(image.getvalue())
    asset=facade.import_preparation_image(str(original),'合成图片','软件验收','验证备课引用恢复')
    prep.image_assets_widget.set_assets([asset])
    payload=deepcopy(prep._payload())
    ids=[]
    for shelf in ('current','archived','trash'):
        receipt=facade.create_preparation_draft({**payload,'topic':'备份演示-'+shelf})
        identity=receipt.draft_id;ids.append(identity)
        def change(action,current='current'):
            row=next(r for r in facade.search_preparation_work(shelf=current,limit=100)['items'] if r['id']==identity)
            facade.organize_preparation_work('draft',identity,action,
                expected_source=row['source_revision'],expected_organization=row['organization_revision'])
        if shelf!='current':change('archive')
        if shelf=='trash':change('trash','archived')
    # The provider is a local fixed fixture; it never opens a socket or reads a key.
    manager=facade._preparation_manager_instance()
    task=manager.prepare(payload,'LOCAL-BACKUP-FIXTURE','REV-1')
    complete=manager.run(task['task_id'],lambda *a,**k:_candidate(),lambda _:None,lambda:False)
    assert complete['status']=='completed',complete.get('error')
    prep.topic.setText('合成的未完成备课')
    prep.recovery.flush()
    before=deepcopy(facade.state_store.snapshot())
    recovery_before=prep.recovery.store.path.read_bytes()
    tasks_before={p.name:p.read_bytes() for p in manager.tasks_root.glob('*.json')}
    archive=output/'备课备份.zip'
    dialog=BackupDialog(facade.paths,window.tasks,window,flush_editor=prep.recovery.flush)
    saved_picker,open_picker,folder_picker=QFileDialog.getSaveFileName,QFileDialog.getOpenFileName,QFileDialog.getExistingDirectory
    restored=None
    real_restore = dialog_module.restore_backup
    def traced_restore(*args, **kwargs):
        # Only this explicit synthetic acceptance helper prints detailed traces.
        # The production dialog continues to present its actionable safe message.
        try:
            return real_restore(*args, **kwargs)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
    dialog_module.restore_backup = traced_restore
    try:
        dialog.show();dialog.images.setChecked(True);dialog.outputs.setChecked(True)
        dialog.plan_button.click();settle(lambda:dialog._active is None and dialog.plan is not None)
        capture(dialog,'backup-plan.png')
        QFileDialog.getSaveFileName=lambda *a,**k:(str(archive),'')
        dialog.save_button.click();settle(lambda:dialog._active is None and archive.exists())
        dialog.tabs.setCurrentIndex(1)
        QFileDialog.getOpenFileName=lambda *a,**k:(str(archive),'')
        dialog.inspect_button.click();settle(lambda:dialog._active is None and dialog.checked is not None)
        capture(dialog,'backup-checked.png')
        QFileDialog.getExistingDirectory=lambda *a,**k:str(output)
        dialog.restore_button.click();settle(lambda:dialog._active is None)
        assert dialog.restored_directory is not None, dialog.status.text()
        restored=Path(dialog.restored_directory)
        capture(dialog,'backup-restored.png')
        dialog.resize(520,580);settle();capture(dialog,'backup-compact.png')
        dialog.close();dialog.deleteLater()
        assert facade.state_store.snapshot()['drafts']==before['drafts']
        assert facade.state_store.snapshot()['work_organization']==before.get('work_organization',{})
        assert prep.recovery.store.path.read_bytes()==recovery_before
        assert {p.name:p.read_bytes() for p in manager.tasks_root.glob('*.json')}==tasks_before
        assert not (restored/'model-settings').exists()
        paths=DesktopPaths.from_workspace(facade.paths.workspace_root,state_root=restored)
        restored_facade=build_default_facade(paths)
        child=TeacherWorkbenchWindow(restored_facade);child.show();child.navigate('mywork')
        try:
            settle(lambda:not child.my_work_page._loading)
            assert '独立恢复副本' in child.windowTitle()
            assert child.preparation_page.topic.text()=='合成的未完成备课'
            for shelf,identity in zip(('current','archived','trash'),ids):
                assert any(r['id']==identity for r in restored_facade.search_preparation_work(shelf=shelf,limit=100)['items'])
            capture(child,'backup-opened.png')
            assert restored_facade.preparation_image_bytes(asset)==original.read_bytes()
            old_path=facade.preparation_artifact_path(task['task_id'],'pptx')
            new_path=restored_facade.preparation_artifact_path(task['task_id'],'pptx')
            assert old_path.read_bytes()==new_path.read_bytes() and len(Presentation(new_path).slides)==3
            candidate=restored_facade._preparation_manager_instance().revision_source(task['task_id'])['candidate']
            regenerated=NativePreparationRenderer().render(candidate,output_kind='joint',output_dir=output/'restored-render')
            doc=next(a['path'] for a in regenerated['artifacts'] if a['artifact_id']=='lesson_plan_docx')
            assert Document(doc).paragraphs
            picture=restored/PREP/'images'/(asset['sha256']+'.image');picture.unlink()
            relink=BackupDialog(paths,child.tasks,child);relink.show();relink.tabs.setCurrentIndex(2)
            try:
                relink.scan_button.click();settle(lambda:relink._active is None and relink.missing_list.count()>0)
                relink.missing_list.setCurrentRow(0);capture(relink,'backup-missing-image.png')
                QFileDialog.getOpenFileName=lambda *a,**k:(str(original),'')
                drafts=restored_facade.state_store.snapshot()['drafts']
                relink.relink_button.click();settle(lambda:relink._active is None and relink.missing_list.count()==0)
                assert restored_facade.preparation_image_bytes(asset)==original.read_bytes()
                assert restored_facade.state_store.snapshot()['drafts']==drafts
            finally:relink.close();relink.deleteLater()
        finally:child.close();settle()
        # A stable, manifest-owned target name for the parent EXE CLI launch test.
        marker=output/'restored-directory.txt';marker.write_text(restored.name,encoding='utf-8')
        return dict(backup_created=True,restore_checked=True,source_records_unchanged=True,
            original_artifacts_equal=True,shelves_preserved=True,recovery_restored=True,
            image_reconnected=True,restored_production_export=True,restored_profile=restored.name,model_calls=0)
    finally:
        dialog_module.restore_backup = real_restore
        QFileDialog.getSaveFileName=saved_picker;QFileDialog.getOpenFileName=open_picker;QFileDialog.getExistingDirectory=folder_picker
