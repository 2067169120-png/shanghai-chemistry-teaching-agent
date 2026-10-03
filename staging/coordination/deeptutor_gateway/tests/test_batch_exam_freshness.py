from copy import deepcopy

import pytest

from test_student_review_desk_core import case  # noqa: F401
from integrations.deeptutor_shchem_v1.desktop_batch_exam import collect_batch_exams
from integrations.deeptutor_shchem_v1.desktop_batch_exam_freshness import (
    batch_source_status, refresh_batch_exam, require_batch_current,
)
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamError
from integrations.deeptutor_shchem_v1.desktop_work_batches import WorkBatchStore

# Imported pytest fixtures are intentionally addressed by their registered name.
# ruff: noqa: F811


def snapshot(f, subs):
    batch = WorkBatchStore(f).save_batch("合成评分快照", "合成班", [
        {"student_id": sub.student_id, "submission_id": sub.submission_id} for sub in subs])
    return batch, collect_batch_exams(f, batch["batch_id"])["groups"][0]["exam"]


def change_first(f, exam):
    member = exam["provenance"]["members"][0]
    refs = {key: member[key] for key in ("student_id", "submission_id")}
    review = f.student_analysis_review(**refs)
    f.record_student_score(**refs, expected_revision=review.revision, match_id=review.items[0].match_id,
                           teacher_score=1, reason="合成教师正式评分")
    return refs


def test_changed_formal_score_marks_old_snapshot_and_rebuilds_without_overwriting(case):
    f, _, subs, transport, _ = case
    _, exam = snapshot(f, subs)
    before, calls = deepcopy(exam), transport.calls
    assert batch_source_status(f, exam)["current"]
    change_first(f, exam)
    assert batch_source_status(f, exam)["state"] == "stale"
    with pytest.raises(ExamError):
        require_batch_current(f, exam)
    refreshed = refresh_batch_exam(f, exam)
    assert refreshed["id"] != exam["id"] and refreshed["students"][0]["scores"] != exam["students"][0]["scores"]
    assert batch_source_status(f, refreshed)["current"] and exam == before and transport.calls == calls


def test_condition_change_and_batch_change_invalidate_source(case):
    f, _, subs, _, _ = case
    store = WorkBatchStore(f)
    batch, exam = snapshot(f, subs)
    member = exam["provenance"]["members"][0]
    summary = f.student_submission(student_id=member["student_id"], submission_id=member["submission_id"])
    store.save_condition(summary, summary.matches[0].match_id, "missing_page", "合成缺页依据", expected_revision=None)
    assert not batch_source_status(f, exam)["current"]
    fresh = refresh_batch_exam(f, exam)
    assert batch_source_status(f, fresh)["current"]
    store.save_batch("改名", "合成班", batch["members"], batch_id=batch["batch_id"], expected_revision=batch["revision"])
    assert not batch_source_status(f, fresh)["current"]


def test_legacy_unversioned_source_and_wrong_member_remain_blocked(case):
    f, _, subs, _, _ = case
    _, exam = snapshot(f, subs)
    legacy = deepcopy(exam)
    legacy["provenance"]["members"][0].pop("condition_revision")
    assert batch_source_status(f, legacy)["state"] == "needs_recheck"
    broken = deepcopy(exam)
    broken["provenance"]["members"][0]["student_id"] = "not-a-member"
    assert batch_source_status(f, broken)["state"] == "blocked"


def test_actual_dashboard_blocks_stale_actions_and_rereads_to_new_history(case):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from test_desktop_studio_ui import settle
    from integrations.deeptutor_shchem_v1.desktop_workbench.exam_dashboard import ExamDashboard
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    f, _, subs, _, _ = case
    _, exam = snapshot(f, subs)
    app = QApplication.instance() or QApplication([])
    tasks = DesktopTaskBridge()
    dashboard = ExamDashboard(f, tasks)
    try:
        dashboard.accept_exam(exam)
        original = dashboard.store.load(exam["id"])
        change_first(f, exam)
        dashboard.render()
        assert not dashboard.ai_button.isEnabled() and not dashboard.followup_panel.new.isEnabled()
        assert "历史快照" in dashboard.source_note.text()
        dashboard.refresh_formal_scores()
        settle(app, lambda: dashboard._task is None and dashboard.exam["id"] != exam["id"])
        assert dashboard._batch_source["current"] and dashboard.followup_panel.new.isEnabled()
        assert dashboard.store.load(exam["id"]) == original and len(dashboard.store.entries()) == 2
    finally:
        tasks.wait_for_done(5000)
        dashboard.dirty = False; dashboard.close(); dashboard.deleteLater()
        tasks.shutdown(); app.processEvents()
