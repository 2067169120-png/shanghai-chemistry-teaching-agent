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
