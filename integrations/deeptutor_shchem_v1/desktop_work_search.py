"""Search existing work identities with reversible organization, then paginate."""
from __future__ import annotations

from datetime import datetime, timezone
from .desktop_work_organization import field, presentation, revision, SHELVES, WorkOrganizationError

_TASK_LABELS = {"completed": "候选已生成", "failed": "生成失败", "cancelled": "已停止",
                "running": "生成中", "prepared": "已准备", "cancel_requested": "正在停止"}


def _timestamp(value: str) -> float:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (ValueError, TypeError, OverflowError):
        return float("-inf")


def search_preparation_work(facade, *, query: str = "", kind: str = "all", order: str = "newest",
                            offset: int = 0, limit: int = 25, shelf: str = "current") -> dict:
    """Match display name OR original topic; scope/type filtering precedes paging."""
    if (not isinstance(query, str) or kind not in {"all", "draft", "task"}
            or shelf not in SHELVES or order not in {"newest", "oldest", "title"}
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError("备课检索参数不正确。")
    records, warnings = [], []
    if kind in {"all", "draft"}:
        try:
            result = facade.search_preparation_drafts(query="", limit=None, include_hidden=True)
            for option in result["items"]:
                view = {key: option[key] for key in ("title", "original_title", "shelf", "organization_revision",
                        "source_revision", "manageable", "restore_shelf", "organization_note") if key in option}
                # Legacy read-only clients without organization still use their existing records.
                if "shelf" not in view:
                    view = presentation({}, "draft", option["draft_id"], option["title"], option["revision"])
                records.append({"kind": "draft", "id": option["draft_id"], "date": option["created_at"],
                                "status": "离线草稿", "value": option, **view})
            if result["invalid_count"]:
                warnings.append(f"{result['invalid_count']}份草稿格式有问题，未计入结果；原记录保留。")
        except WorkOrganizationError as exc:
            warnings.append(str(exc))
        except Exception:
            warnings.append("离线草稿未能读取，当前结果不是完整作品库。")
    if kind in {"all", "task"}:
        try:
            state = getattr(facade, "state_store", None)
            snapshot = state.snapshot() if state is not None else {}
            result = facade.search_preparation_tasks(query="")
            task_rows = []
            for task in result["items"]:
                identity, title, status = field(task, "task_id"), field(task, "title_zh"), field(task, "status")
                view = presentation(snapshot, "task", identity, title, revision(task), status)
                task_rows.append({"kind": "task", "id": identity,
                                  "date": field(task, "updated_at") or field(task, "created_at"),
                                  "status": _TASK_LABELS.get(status, "状态待查看"), "value": task, **view})
            records.extend(task_rows)
            if result["invalid_count"]:
                warnings.append(f"{result['invalid_count']}份任务记录无法读取，已保留原文件供核对。")
        except WorkOrganizationError as exc:
            warnings.append(str(exc))
        except Exception:
            warnings.append("生成任务未能读取，当前结果不是完整作品库。")
    needle = query.strip().casefold()
    records = [row for row in records if needle in row["title"].casefold() or needle in row["original_title"].casefold()]
    counts = {value: sum(row["shelf"] == value for row in records) for value in SHELVES}
    records = [row for row in records if row["shelf"] == shelf]
    if order == "title":
        records.sort(key=lambda row: (row["title"].casefold(), row["kind"], row["id"]))
    else:
        records.sort(key=lambda row: (_timestamp(row["date"]), row["kind"], row["id"]), reverse=order == "newest")
    total = len(records)
    offset = min(offset, ((total - 1) // limit) * limit) if total else 0
    return {"items": records[offset:offset + limit], "total": total, "offset": offset, "limit": limit,
            "has_more": offset + limit < total, "warnings": warnings, "shelf_counts": counts}
