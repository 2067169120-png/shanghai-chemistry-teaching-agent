"""Durable navigation context and safe task receipts, sharing the state lock.

Receipts contain no callable, model input, result payload or credentials. A
restart marks unfinished work for explicit inspection at its original page.
"""
from __future__ import annotations

import uuid
from copy import deepcopy

from .desktop_state import DesktopStateError, utc_now

SCHEMA = "shchem.teacher-workspace.v1"
ACTIVE = frozenset({"queued", "running", "cancel_requested"})
STATUSES = ACTIVE | {"completed", "failed", "cancelled", "interrupted"}


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise DesktopStateError("教学上下文或任务记录格式不正确。")
    return value.strip()


class TeacherWorkspaceStore:
    def __init__(self, state):
        self.state = state

    @staticmethod
    def _workspace(value):
        record = value.get("teacher_workspace", {"schema": SCHEMA, "context": {}, "tasks": []})
        if (not isinstance(record, dict) or record.get("schema") != SCHEMA
                or not isinstance(record.get("context"), dict) or not isinstance(record.get("tasks"), list)):
            raise DesktopStateError("教学工作记录无法读取，请保留原记录并核对备份。")
        ids = set()
        for task in record["tasks"]:
            if (not isinstance(task, dict) or task.get("status") not in STATUSES
                    or not isinstance(task.get("task_id"), str) or task["task_id"] in ids):
                raise DesktopStateError("任务记录无法读取，请保留原记录并核对备份。")
            ids.add(task["task_id"])
        return record

    def snapshot(self):
        return deepcopy(self._workspace(self.state.snapshot()))

    def save_context(self, *, term="", class_label="", work_label=""):
        context = {"term": _text(term, 80), "class_label": _text(class_label, 100),
                   "work_label": _text(work_label, 120)}
        def edit(value):
            record = self._workspace(value)
            record["context"] = context
            record["revision"] = uuid.uuid4().hex
            value["teacher_workspace"] = record
        self.state._update(edit)
        return context

    def start(self, task_id, label, *, route="home"):
        task_id, label, route = _text(task_id, 64), _text(label, 160), _text(route, 40)
        if not task_id or not label:
            raise DesktopStateError("任务标识或名称缺失。")
        def edit(value):
            record = self._workspace(value)
            if any(task["task_id"] == task_id for task in record["tasks"]):
                raise DesktopStateError("任务已经登记，请重新读取任务列表。")
            record["tasks"].append({"task_id": task_id, "label": label, "route": route,
                "context": deepcopy(record["context"]), "status": "queued", "created_at": utc_now(),
                "updated_at": utc_now(), "message": "等待处理"})
            # Preserve every active task and the most recent 200 terminal receipts.
            terminal = [row for row in record["tasks"] if row["status"] not in ACTIVE][-200:]
            keep = {row["task_id"] for row in terminal}
            record["tasks"] = [row for row in record["tasks"] if row["status"] in ACTIVE or row["task_id"] in keep]
            value["teacher_workspace"] = record
        self.state._update(edit)

    def transition(self, task_id, status, message=""):
        if status not in STATUSES:
            raise DesktopStateError("未知任务状态。")
        message = _text(message, 500)
        def edit(value):
            record = self._workspace(value)
            task = next((row for row in record["tasks"] if row["task_id"] == task_id), None)
            if task is None or (task["status"] not in ACTIVE and status in ACTIVE):
                return False
            task.update(status=status, message=message, updated_at=utc_now())
            terminal = [row for row in record["tasks"] if row["status"] not in ACTIVE][-200:]
            keep = {row["task_id"] for row in terminal}
            record["tasks"] = [row for row in record["tasks"] if row["status"] in ACTIVE or row["task_id"] in keep]
            value["teacher_workspace"] = record
        self.state._update(edit)

    def recover_interrupted(self):
        def edit(value):
            record = self._workspace(value)
            active = [row for row in record["tasks"] if row["status"] in ACTIVE]
            if not active:
                return False
            for task in active:
                task.update(status="interrupted", updated_at=utc_now(),
                    message="上次处理未完成，请回原页面核对已保存结果后继续。")
            value["teacher_workspace"] = record
        self.state._update(edit)


__all__ = ["ACTIVE", "STATUSES", "TeacherWorkspaceStore"]
