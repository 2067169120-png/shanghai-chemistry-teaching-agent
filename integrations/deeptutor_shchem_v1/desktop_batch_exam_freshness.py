"""Check derived statistics against the formal decisions they actually consumed."""

from __future__ import annotations

from .desktop_exam_data import ExamError
from .desktop_work_batches import WorkBatchStore


def batch_source_status(facade, exam):
    provenance = (exam or {}).get("provenance", {})
    if provenance.get("kind") != "teacher_work_batch":
        return {
            "current": True,
            "state": "not_applicable",
            "message": "本机导入分析副本",
        }
    try:
        store = WorkBatchStore(facade)
        batch = store.get_batch(provenance.get("batch_id"))
        if not batch or batch["revision"] != provenance.get("batch_revision"):
            return {
                "current": False,
                "state": "stale",
                "message": "原作业批次名单已变化，请重新读取正式评分。",
            }
        members = provenance.get("members")
        if not isinstance(members, list) or not members or len(members) > 500:
            raise ValueError("invalid source scope")
        selected = {
            (row["student_id"], row["submission_id"]) for row in batch["members"]
        }
        seen = set()
        for member in members:
            pair = (member["student_id"], member["submission_id"])
            if pair not in selected or pair in seen:
                raise ValueError("source membership mismatch")
            seen.add(pair)
            if "condition_revision" not in member:
                return {
                    "current": False,
                    "state": "needs_recheck",
                    "message": "旧评分快照未记录作答状态版本，请重新读取正式评分。",
                }
            refs = {"student_id": pair[0], "submission_id": pair[1]}
            summary = facade.student_submission(**refs)
            review = facade.student_analysis_review(**refs)
            conditions = store.conditions(summary)
            if (
                review.revision != member["review_revision"]
                or conditions.get("revision") != member["condition_revision"]
            ):
                return {
                    "current": False,
                    "state": "stale",
                    "message": "原作答、正式评分或作答状态已有更新；当前统计为历史快照，请重新读取。",
                }
        # Recheck the batch after reading source domains; do not accept a mixed
        # membership snapshot if another window edited it during inspection.
        if store.get_batch(batch["batch_id"])["revision"] != batch["revision"]:
            raise ValueError("source changed while reading")
        return {
            "current": True,
            "state": "current",
            "message": "已核对原批次、正式评分与作答状态版本",
        }
    except (ValueError, RuntimeError, KeyError, TypeError, OSError):
        return {
            "current": False,
            "state": "blocked",
            "message": "原评分来源暂时无法完整核对；保留历史快照，请检查原作答。",
        }


def require_batch_current(facade, exam):
    status = batch_source_status(facade, exam)
    if not status["current"]:
        raise ExamError(status["message"])
    return status


def refresh_batch_exam(facade, exam, *, cancelled=lambda: False):
    from .desktop_batch_exam import collect_batch_exams

    source = exam.get("provenance", {})
    if source.get("kind") != "teacher_work_batch":
        raise ExamError("当前考试没有可重新读取的作业批次。")
    result = collect_batch_exams(facade, source["batch_id"], cancelled=cancelled)
    matching = [
        row["exam"]
        for row in result["groups"]
        if row["group"] == source.get("paper_group")
    ]
    if len(matching) != 1:
        raise ExamError(
            "原题匹配范围或满分已改变，请回作业批次重新核对统计分组；旧快照保留。"
        )
    require_batch_current(facade, matching[0])
    return matching[0]
