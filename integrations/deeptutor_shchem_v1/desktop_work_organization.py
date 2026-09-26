"""Names and shelves for existing works; never modify source payloads or artifacts.

Metadata lives in the existing desktop state. Identities are type-qualified;
archive/trash are reversible views, not physical file operations.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
from typing import Any

from .desktop_state import DesktopStateError, utc_now

SHELVES = ("current", "archived", "trash")
TERMINAL_TASKS = frozenset({"completed", "failed", "cancelled"})
TITLE_LIMIT = 160


class WorkOrganizationError(ValueError):
    def __init__(self, message: str):
        self.message_zh = message
        super().__init__(message)


def revision(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def field(value: Any, name: str, default: Any = "") -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def key_for(kind: str, identity: str) -> str:
    if kind not in {"draft", "task"} or not isinstance(identity, str) or not identity:
        raise WorkOrganizationError("作品身份不正确，请刷新后重新选择。")
    return f"{kind}:{identity}"


def validate_title(title: str) -> str:
    if not isinstance(title, str):
        raise WorkOrganizationError("请填写作品名称。")
    title = title.strip()
    if not title or len(title) > TITLE_LIMIT or any(ord(c) < 32 or ord(c) == 127 for c in title):
        raise WorkOrganizationError(f"作品名称须为1—{TITLE_LIMIT}个字符，不能含换行或控制字符。")
    return title


def metadata(snapshot: Mapping, kind: str, identity: str) -> dict:
    key = key_for(kind, identity)
    values = snapshot.get("work_organization", {})
    if not isinstance(values, Mapping):
        raise WorkOrganizationError("作品分类记录无法读取，请保留原文件并检查本机状态。")
    value = values.get(key, {})
    if not isinstance(value, dict) or value.get("shelf", "current") not in SHELVES:
        raise WorkOrganizationError("这份作品的分类记录不完整，请保留原文件并检查本机状态。")
    if value.get("previous_shelf", "current") not in {"current", "archived"}:
        raise WorkOrganizationError("这份作品的还原位置不正确，原文件未改变。")
    if "title" in value:
        validate_title(value["title"])
    return deepcopy(value)


def presentation(snapshot: Mapping, kind: str, identity: str, original_title: str,
                 source_revision: str, task_status: str = "") -> dict:
    meta = metadata(snapshot, kind, identity)
    shelf = meta.get("shelf", "current")
    active = kind == "task" and task_status not in TERMINAL_TASKS
    changed = kind == "task" and shelf != "current" and meta.get("source_revision") != source_revision
    # A retry outside this view must not hide a running or newly changed task.
    effective = "current" if active or changed else shelf
    return {"title": meta.get("title", original_title), "original_title": original_title,
            "shelf": effective, "organization_revision": revision(meta),
            "source_revision": source_revision, "manageable": not active,
            "restore_shelf": meta.get("previous_shelf", "current"),
            "organization_note": "任务有新状态，已显示在当前作品。" if changed else ""}


def draft_presentation(snapshot: Mapping, identity: str, record: Mapping, source_revision: str) -> dict:
    return presentation(snapshot, "draft", identity, str(record["core_fields"]["topic"]), source_revision)


def change_work(state, kind: str, identity: str, action: str, *, expected_source: str,
                expected_organization: str, title: str | None = None, task_reader=None) -> dict:
    """Caller holds the task manager lock for tasks; state mutation is atomic.

    Draft source/version and organization/version are compared inside the same
    state transaction. A task reader returns a fresh public summary under its
    manager's lock. No source records, returned candidates or outputs are written.
    """
    from .desktop_preparation_drafts import PreparationDraftService, _revision

    key = key_for(kind, identity)
    if action not in {"rename", "archive", "unarchive", "trash", "restore"}:
        raise WorkOrganizationError("不支持的作品操作。")
    if not expected_source or not expected_organization:
        raise WorkOrganizationError("作品版本缺失，请刷新后再操作。")
    if action == "rename":
        title = validate_title(title)
    result = {}

    def update(snapshot):
        if kind == "draft":
            record = snapshot["drafts"].get(identity)
            if not isinstance(record, dict):
                raise WorkOrganizationError("草稿已不存在，请刷新；没有改动其他作品。")
            PreparationDraftService._payload(record)
            current = draft_presentation(snapshot, identity, record, _revision(record))
        else:
            if task_reader is None:
                raise WorkOrganizationError("任务读取服务不可用。")
            task = task_reader(identity)
            current = presentation(snapshot, kind, identity, field(task, "title_zh"), revision(task), field(task, "status"))
        if current["source_revision"] != expected_source or current["organization_revision"] != expected_organization:
            raise WorkOrganizationError("作品内容或分类已经变化，请刷新后重选；本次未改动任何作品。")
        if not current["manageable"]:
            raise WorkOrganizationError("任务尚未结束，暂不能整理；此操作不会取消或重试生成。")
        old = metadata(snapshot, kind, identity)
        shelf = current["shelf"]
        allowed = {"rename": {"current", "archived"}, "archive": {"current"},
                   "unarchive": {"archived"}, "trash": {"current", "archived"}, "restore": {"trash"}}
        if shelf not in allowed[action]:
            raise WorkOrganizationError("作品所在范围已不适用此操作，请刷新；回收站作品需先还原。")
        target = {"archive": "archived", "unarchive": "current", "trash": "trash",
                  "restore": current["restore_shelf"]}.get(action, shelf)
        new = {**old, "shelf": target, "source_revision": current["source_revision"], "edited_at": utc_now()}
        if action == "rename":
            new["title"] = title
        if action == "trash":
            new["previous_shelf"] = shelf
        elif action == "restore":
            new.pop("previous_shelf", None)
        snapshot.setdefault("work_organization", {})[key] = new
        result.update(kind=kind, id=identity, action=action, title=new.get("title", current["title"]), shelf=target)
    try:
        state._update(update)
    except DesktopStateError as exc:
        raise WorkOrganizationError(str(exc)) from exc
    return result
