"""Small read-only summary for the teacher's desk. No library scan or model call."""
from __future__ import annotations


def desk_snapshot(facade) -> dict:
    result = {"works": [], "total": None, "basket": None, "warnings": []}
    try:
        found = facade.search_preparation_work(shelf="current", order="newest", limit=5)
        result.update(works=found["items"], total=found["total"])
        result["warnings"].extend(found.get("warnings", []))
    except Exception:
        result["warnings"].append("最近备课未能读取，请刷新或从“我的备课”检查。")
    try:
        result["basket"] = list(facade.basket())
    except Exception:
        result["warnings"].append("选题篮未能读取；已选记录未被清空。")
    return result
