from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_teacher_workspace import (
    TeacherWorkspaceStore,
)


def test_task_origin_survives_context_switch_and_restart_without_result_payload(tmp_path):
    state = DesktopStateStore(tmp_path)
    workspace = TeacherWorkspaceStore(state)
    workspace.save_context(term="秋季", class_label="高二A", work_label="平衡练习")
    workspace.start("first", "导入本地资料", route="library")
    workspace.transition("first", "running")
    workspace.save_context(term="秋季", class_label="高二B", work_label="复练")
    workspace.start("second", "保存备课", route="preparation")
    workspace.transition("second", "completed", "已保存")
    reopened = TeacherWorkspaceStore(DesktopStateStore(tmp_path))
    reopened.recover_interrupted()
    first, second = reopened.snapshot()["tasks"]
    assert first["status"] == "interrupted"
    assert first["context"]["class_label"] == "高二A"
    assert first["route"] == "library"
    assert second["status"] == "completed"
    assert reopened.snapshot()["context"]["class_label"] == "高二B"
    assert "result" not in first and "operation" not in first


def test_receipt_retention_preserves_active_tasks_and_existing_drafts(tmp_path):
    state = DesktopStateStore(tmp_path)
    state._update(lambda value: value["drafts"].update({"lesson": {"topic": "原稿"}}))
    workspace = TeacherWorkspaceStore(state)
    workspace.start("active", "仍在处理")
    for i in range(205):
        workspace.start(str(i), "读取资料")
        workspace.transition(str(i), "completed")
    rows = workspace.snapshot()["tasks"]
    assert any(row["task_id"] == "active" for row in rows)
    assert len([row for row in rows if row["status"] == "completed"]) == 200
    assert state.snapshot()["drafts"]["lesson"] == {"topic": "原稿"}


def test_terminal_task_cannot_be_reactivated_by_a_late_started_signal(tmp_path):
    workspace = TeacherWorkspaceStore(DesktopStateStore(tmp_path))
    workspace.start("task", "读取资料")
    workspace.transition("task", "cancelled")
    workspace.transition("task", "running")
    assert workspace.snapshot()["tasks"][0]["status"] == "cancelled"
