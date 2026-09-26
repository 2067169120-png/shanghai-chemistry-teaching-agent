import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QRect
from test_question_explorer_ui import window
from test_desktop_studio_ui import settle
from integrations.deeptutor_shchem_v1.desktop_lesson_probe import sample_payload
from integrations.deeptutor_shchem_v1.desktop_lesson_design import coverage
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery import apply_editor_payload
from integrations.deeptutor_shchem_v1.desktop_workbench.lesson_design_dialog import LessonDesignDialog


@pytest.fixture
def desk(window):
    win,app=window
    page=win.preparation_page
    win.navigate("preparation")
    apply_editor_payload(page,sample_payload())
    d=LessonDesignDialog(page);d.show();settle(app)
    yield d,app,page
    d.reject();d.deleteLater();settle(app)


def test_teacher_edit_reaches_original_payload_and_recovery(desk):
    d,app,page=desk
    d.fields["student_task"].setPlainText("更新的学生任务")
    settle(app)
    assert page._payload()["lesson_design"]["nodes"][0]["student_task"]=="更新的学生任务"
    assert not page._payload()["lesson_design"]["nodes"][0]["confirmed"]
    assert page.recovery.flush()
    assert page.recovery.store.load()["payload"]["lesson_design"]==d.history.value


def test_switch_nodes_preserves_inputs_and_undo_order(desk):
    d,app,page=desk
    first=d.current;d.fields["notes"].setPlainText("第一环节备注")
    d.add_node();second=d.current;d.fields["student_task"].setPlainText("第二环节任务")
    d.nodes.setCurrentRow(0);settle(app)
    assert d.fields["notes"].toPlainText()=="第一环节备注"
    d.move_node(1);d.travel(False)
    assert [n["id"] for n in d.history.value["nodes"]]==[first,second]


def test_locked_fields_read_only_but_explanation_editable(desk):
    d,app,page=desk
    d.locked.setChecked(True);settle(app)
    assert d.fields["material_text"].isReadOnly()
    assert d.fields["teacher_answer"].isReadOnly()
    assert not d.fields["teacher_action"].isReadOnly()
    assert not d.student_material.isEnabled()
    d.locked.setChecked(False);settle(app)
    assert not d.fields["material_text"].isReadOnly()


def test_uncovered_goal_visible_without_blocking_draft(desk):
    d,app,page=desk
    assert "未安排" in d.report.toPlainText()
    assert d.save.isEnabled()
    d.save.click();settle(app,lambda:not d.busy)
    assert "已保存" in d.status.text()


def test_compact_editor_keeps_actions_and_main_work_area(desk):
    d,app,page=desk
    d.resize(800,700);settle(app)
    for index in range(3):
        d.tabs.setCurrentIndex(index);settle(app)
        assert d.tabs.height() >=400
        for widget in (d.tabs,d.save,d.generate,d.preview,d.return_button):
            assert d.rect().contains(QRect(widget.mapTo(d,widget.rect().topLeft()),widget.size()))


def test_regeneration_selects_new_output_and_old_has_explicit_status(desk):
    d,app,page=desk
    d.generate.click();settle(app,lambda:not d.busy)
    assert len(d.history.value['exports'])==1,d.status.text()
    old=d.outputs.currentData()
    d.fields['student_task'].setPlainText('第二版可见任务，保留原材料。')
    assert '历史内容' in d.selected_output_status.text()
    d.generate.click();settle(app,lambda:not d.busy)
    assert len(d.history.value['exports'])==2,d.status.text()
    assert d.outputs.currentData()==d.history.value['exports'][-1]['id']!=old
    assert '一致' in d.selected_output_status.text()
    d.outputs.setCurrentIndex(d.outputs.findData(old));settle(app)
    assert '历史内容' in d.selected_output_status.text()


def test_copy_button_uses_selected_version_and_cancel_does_not_write(desk, tmp_path, monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    from integrations.deeptutor_shchem_v1.desktop_lesson_output import FILES, checked_file
    d,app,page=desk
    assert not d.export_copy.isEnabled()
    d.generate.click();settle(app,lambda:not d.busy)
    assert d.export_copy.isEnabled()
    monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a,**k:'')
    d.export_copy.click();settle(app)
    assert not list(tmp_path.glob('教学成品-*'))
    monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a,**k:str(tmp_path))
    d.export_copy.click();settle(app,lambda:not d.busy)
    folder=next(tmp_path.glob('教学成品-*'))
    for name in FILES:
        assert (folder/name).read_bytes()==checked_file(d.facade,d.history.value,d.outputs.currentData(),name).read_bytes()
    assert '已导出到' in d.status.text()
