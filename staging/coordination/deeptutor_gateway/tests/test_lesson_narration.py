"""Narration belongs to the actual selected version, not an arbitrary next page."""
from copy import deepcopy
from enum import Enum
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QObject, Signal, QLocale, Qt
from PySide6.QtTest import QTest
from test_preview_navigation import qt_app, pdf_report
from test_word_question_dialog import _Tasks
from test_lesson_design import sample
from test_lesson_output_backup import fixture
from test_question_explorer_ui import window
from integrations.deeptutor_shchem_v1.desktop_lesson_design import validate_design, content_fingerprint, DesignHistory
from integrations.deeptutor_shchem_v1.desktop_lesson_narration import put_narration, narration_scripts, MAX_PAGE_TEXT
from integrations.deeptutor_shchem_v1.desktop_preview_pdf import FrozenPdfPages
from integrations.deeptutor_shchem_v1.desktop_workbench.actual_ppt_preview_dialog import ActualPptPreview


class FakeSpeech(QObject):
    stateChanged = Signal(object)
    errorOccurred = Signal(object)
    class State(Enum):
        Ready=0;Speaking=1;Paused=2;Error=3
    def __init__(self,parent):
        super().__init__(parent);self.current=self.State.Ready;self.said=[];self.stops=0
    def availableVoices(self):
        return [SimpleNamespace(name=lambda:"合成中文声音",locale=lambda:QLocale("zh_CN"))]
    def state(self):return self.current
    def setVoice(self,_):pass
    def setRate(self,_):pass
    def switch(self,state):self.current=state;self.stateChanged.emit(state)
    def say(self,text):self.said.append(text);self.switch(self.State.Speaking)
    def stop(self):self.stops+=1;self.switch(self.State.Ready)
    def pause(self):self.switch(self.State.Paused)
    def resume(self):self.switch(self.State.Speaking)


def registered():
    plan=sample()["lesson_design"]
    identity="OUT-"+"a"*32
    plan["exports"]=[{"id":identity,"fingerprint":"c"*64,"created_at":"synthetic",
        "files":[{"name":"lesson_presentation.pptx","sha256":"a"*64},
                 {"name":"student_presentation.pptx","sha256":"b"*64}]}]
    return plan,identity


def test_scripts_do_not_cross_audience_or_make_existing_PPT_outdated():
    plan,key=registered();payload=sample();payload["lesson_design"]=plan
    before=deepcopy(plan);fingerprint=content_fingerprint(payload)
    teacher=put_narration(plan,key,"lesson_presentation.pptx","a"*64,["教师答案","教师解析"])
    assert plan==before and content_fingerprint(payload,teacher)==fingerprint
    assert narration_scripts(teacher,key,"student_presentation.pptx","b"*64,2)==["",""]
    student=put_narration(teacher,key,"student_presentation.pptx","b"*64,["学生观察","学生任务"])
    assert narration_scripts(student,key,"lesson_presentation.pptx","a"*64,2)==["教师答案","教师解析"]
    history=DesignHistory(plan);history.put(student)
    assert history.travel()==plan and history.travel(True)==student


@pytest.mark.parametrize("edit",["source","export","extra","pages","bool","duplicate","too_long"])
def test_invalid_or_misbound_records_fail_without_dropping_original(edit):
    plan,key=registered()
    good=put_narration(plan,key,"lesson_presentation.pptx","a"*64,["讲稿"])
    changed=deepcopy(good);row=changed["narrations"][0]
    if edit=="source":row["source_sha256"]="f"*64
    if edit=="export":row["export_id"]="OUT-"+"f"*32
    if edit=="extra":row["guessed_answer"]=True
    if edit=="pages":row["page_count"]=2
    if edit=="bool":row["page_count"]=True
    if edit=="duplicate":changed["narrations"].append(deepcopy(row))
    if edit=="too_long":row["scripts"]=["文"*(MAX_PAGE_TEXT+1)]
    with pytest.raises(ValueError):validate_design(changed)
    assert good["narrations"][0]["scripts"]==["讲稿"]


def test_another_conversion_page_count_preserves_previous_scripts():
    plan,key=registered()
    value=put_narration(plan,key,"student_presentation.pptx","b"*64,["一","二"])
    assert narration_scripts(value,key,"student_presentation.pptx","b"*64,3)==["","",""]
    other=put_narration(value,key,"student_presentation.pptx","b"*64,["甲","乙","丙"])
    assert narration_scripts(other,key,"student_presentation.pptx","b"*64,2)==["一","二"]


def test_scripts_round_trip_with_original_draft_recovery_and_output_backup(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_backup import plan_backup,create_backup,inspect_backup,restore_backup,manifest_revision
    from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import PreparationDraftService
    from integrations.deeptutor_shchem_v1.desktop_editor_recovery import PreparationRecoveryStore
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
    from integrations.deeptutor_shchem_v1.desktop_lesson_output import checked_file,FILES
    from test_desktop_preparation_drafts import _record
    state,facade,payload,out=fixture(tmp_path/"original")
    source=next(r["sha256"] for r in out["files"] if r["name"]=="lesson_presentation.pptx")
    payload["lesson_design"]=put_narration(payload["lesson_design"],out["id"],"lesson_presentation.pptx",source,["合成讲稿第一页","第二页"])
    record=_record(payload);record["lesson_design"]=deepcopy(payload["lesson_design"]);state.save_draft("lesson-101",record)
    PreparationRecoveryStore(state.root).save(payload,dirty=True,sequence=2)
    before={name:checked_file(facade,payload["lesson_design"],out["id"],name).read_bytes() for name in FILES}
    archive=tmp_path/"narration.zip";create_backup(plan_backup(state.root,include_tasks=True),archive)
    report=inspect_backup(archive);restored=tmp_path/"restored"
    restore_backup(archive,restored,expected_manifest=manifest_revision(report))
    loaded=PreparationDraftService._payload(DesktopStateStore(restored).snapshot()["drafts"]["lesson-101"])
    assert loaded["lesson_design"]==payload["lesson_design"]
    recovered=PreparationRecoveryStore(restored).load()["payload"]
    assert recovered["lesson_design"]==payload["lesson_design"]
    other=SimpleNamespace(paths=SimpleNamespace(task_root=restored/"tasks"))
    for name in FILES:assert checked_file(other,loaded["lesson_design"],out["id"],name).read_bytes()==before[name]


def reader(tmp_path, *, save=None, factory=FakeSpeech, verify=None):
    tasks=_Tasks();saved=[]
    report=pdf_report(tmp_path,2);report["audience"]="student"
    dialog=ActualPptPreview(report,tasks=tasks,script_save=save or (lambda value:saved.append(value)),
        script_load=lambda n:[""]*n,speech_factory=factory,verify_presentation=verify)
    tasks.flush();dialog.show();dialog.narration_toggle.setChecked(True)
    return dialog,dialog.narration,tasks,saved


def test_actual_pdf_text_is_page_specific_and_never_guessed(qt_app,tmp_path):
    source=FrozenPdfPages(pdf_report(tmp_path,2))
    assert "PAGE 1" in source.text(1) and "PAGE 2" not in source.text(1)
    assert "PAGE 2" in source.text(2)
    with pytest.raises(ValueError):source.text(True)


def test_page_edits_append_and_apply_to_parent_on_return(qt_app,tmp_path):
    dialog,panel,tasks,saved=reader(tmp_path)
    panel.editor.setPlainText("教师已写：");panel.read_text();tasks.flush()
    assert panel.editor.toPlainText().startswith("教师已写：") and "PAGE 1" in panel.editor.toPlainText()
    first=panel.editor.toPlainText();dialog.move(1);tasks.flush()
    assert panel.editor.toPlainText()=="";panel.editor.setPlainText("第二页自写讲稿")
    dialog.reject()
    assert saved[-1]==[first,"第二页自写讲稿"]


def test_late_read_callback_cannot_replace_another_page_or_user_edit(qt_app,tmp_path):
    dialog,panel,tasks,_=reader(tmp_path)
    panel.read_text();dialog.move(1)
    tasks.finish("读取本页讲稿文字",allow_cancelled=True);tasks.flush()
    assert panel.editor.toPlainText()==""
    panel.read_text();panel.editor.setPlainText("正在修改的讲稿")
    tasks.finish("读取本页讲稿文字",allow_cancelled=True)
    assert panel.editor.toPlainText()=="正在修改的讲稿"
    dialog.reject()


def test_explicit_play_pause_and_page_change_stop_without_autoplay(qt_app,tmp_path):
    dialog,panel,tasks,_=reader(tmp_path)
    assert panel.speech.said==[]
    panel.editor.setPlainText("第一页面讲稿");panel.play();tasks.flush()
    assert panel.speech.said==["第一页面讲稿"] and panel.pause_button.isEnabled()
    panel.pause();assert panel.speech.state().name=="Paused"
    panel.pause();assert panel.speech.state().name=="Speaking"
    dialog.move(1);tasks.flush()
    assert panel.speech.state().name=="Ready" and panel.speech.said==["第一页面讲稿"]
    dialog.reject();assert panel.closed


@pytest.mark.parametrize("changed",["pdf","ppt"])
def test_changed_source_blocks_speech_and_preserves_manuscript(qt_app,tmp_path,changed):
    def verify():
        if changed=="ppt":raise ValueError("synthetic PPT changed")
    dialog,panel,tasks,_=reader(tmp_path,verify=verify)
    panel.editor.setPlainText("尚未朗读的讲稿")
    if changed=="pdf":panel.source.path.write_bytes(b"changed")
    panel.play();tasks.flush()
    assert panel.speech.said==[] and panel.editor.toPlainText()=="尚未朗读的讲稿"
    dialog.reject()


def test_unavailable_voice_keeps_manuscript_editing_and_saving(qt_app,tmp_path):
    def unavailable(_):raise RuntimeError("synthetic missing engine")
    dialog,panel,tasks,saved=reader(tmp_path,factory=unavailable)
    panel.editor.setPlainText("没有语音也保留讲稿")
    assert not panel.play_button.isEnabled() and panel.commit()
    assert saved[-1][0]=="没有语音也保留讲稿";dialog.reject()


def test_open_manuscript_keeps_actual_page_readable_in_desktop_windows(qt_app,tmp_path):
    dialog,panel,tasks,_=reader(tmp_path)
    dialog.resize(1250,900);QTest.qWait(40)
    assert dialog.content_splitter.orientation()==Qt.Orientation.Horizontal
    assert dialog.scroll.viewport().height()>350 and panel.editor.height()>=70
    dialog.resize(800,900);QTest.qWait(40)
    assert dialog.width()==800 and dialog.content_splitter.orientation()==Qt.Orientation.Vertical
    assert dialog.scroll.viewport().height()>180 and panel.editor.height()>=70
    assert not dialog.navigator.isVisible()
    dialog.navigation_toggle.click();QTest.qWait(40)
    assert dialog.navigator.isVisible()
    dialog.reject()


def test_save_failure_keeps_dialog_open_and_supports_discard(qt_app,tmp_path):
    def fail(_):raise ValueError("synthetic save failed")
    dialog,panel,tasks,_=reader(tmp_path,save=fail)
    panel.editor.setPlainText("不能丢失的输入");dialog.reject()
    assert not dialog._closed and panel.editor.toPlainText()=="不能丢失的输入"
    panel.discard();dialog.reject();assert dialog._closed


def test_new_student_preview_default_and_explicit_teacher_choice(window):
    from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog
    win,app=window;dialog=LessonDesignDialog(win.preparation_page);plan,key=registered()
    dialog.history.value=plan;dialog.outputs.addItem("合成输出",key);dialog.show_selected_output()
    assert dialog.preview_audience.currentData()=="student_presentation.pptx"
    dialog.preview_audience.setCurrentIndex(1);dialog.preview_audience.activated.emit(1)
    dialog.show_selected_output();assert dialog.preview_audience.currentData()=="lesson_presentation.pptx"
    dialog.reject()
