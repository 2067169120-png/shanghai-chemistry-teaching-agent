"""Search both existing work stores before paging; never create a second library."""
from __future__ import annotations

from datetime import datetime, timezone


_TASK_LABELS = {"completed": "候选已生成", "failed": "生成失败", "cancelled": "已停止",
                "running": "生成中", "prepared": "已准备", "cancel_requested": "正在停止"}


def _field(value, key, default=""):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _timestamp(value: str) -> float:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (ValueError, TypeError, OverflowError):
        return float("-inf")


def search_preparation_work(facade, *, query: str = "", kind: str = "all", order: str = "newest",
                            offset: int = 0, limit: int = 25) -> dict:
    """Titles are matched at the source, then kinds are merged and paginated."""
    if (not isinstance(query, str) or kind not in {"all", "draft", "task"}
            or order not in {"newest", "oldest", "title"} or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError("备课检索参数不正确。")
    records, warnings = [], []
    if kind in {"all", "draft"}:
        try:
            result = facade.search_preparation_drafts(query=query, limit=None)
            for option in result["items"]:
                records.append({"kind": "draft", "id": option["draft_id"], "title": option["title"],
                                "date": option["created_at"], "status": "离线草稿", "value": option})
            if result["invalid_count"]:
                warnings.append(f"{result['invalid_count']}份草稿格式有问题，未计入结果；原记录保留。")
        except Exception:  # each source remains usable if the other is unavailable
            warnings.append("离线草稿未能读取，当前结果不是完整作品库。")
    if kind in {"all", "task"}:
        try:
            result = facade.search_preparation_tasks(query=query)
            for task in result["items"]:
                records.append({"kind": "task", "id": _field(task, "task_id"),
                                "title": _field(task, "title_zh"),
                                "date": _field(task, "updated_at") or _field(task, "created_at"),
                                "status": _TASK_LABELS.get(_field(task, "status"), "状态待查看"), "value": task})
            if result["invalid_count"]:
                warnings.append(f"{result['invalid_count']}份任务记录无法读取，已保留原文件供核对。")
        except Exception:
            warnings.append("生成任务未能读取，当前结果不是完整作品库。")
    if order == "title":
        records.sort(key=lambda row: (row["title"].casefold(), row["kind"], row["id"]))
    else:
        records.sort(key=lambda row: (_timestamp(row["date"]), row["kind"], row["id"]), reverse=order == "newest")
    total = len(records)
    offset = min(offset, ((total - 1) // limit) * limit) if total else 0
    return {"items": records[offset:offset + limit], "total": total, "offset": offset, "limit": limit,
            "has_more": offset + limit < total, "warnings": warnings}
