"""Identity, frozen scope and attendance against existing student sources."""
from copy import deepcopy
from dataclasses import replace

import pytest

from test_student_review_desk_core import case
from integrations.deeptutor_shchem_v1.desktop_classroom_registry import (
    ClassroomRegistry, ClassroomError, preview_roster_csv,
)
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_work_batches import WorkBatchStore


def roster(f, *, label="合成班A", count=4):
    profiles = f.student_profiles()
    return ClassroomRegistry(f).save_class(label, "合成学期", [
        {"label": "同名学生" if i < 2 else f"学生{i}", "external_ref": str(i),
         "profile_id": profiles[i].student_id if i < len(profiles) else ""}
        for i in range(count)])


def work(f, classroom):
    registry = ClassroomRegistry(f)
    return registry.create_work(classroom["id"], "合成作业", expected_class_revision=classroom["revision"])


def test_same_name_identities_survive_reopen_without_model_or_source_edits(case):
    f, _, _, transport, sources = case
    before, calls = [p.read_bytes() for p in sources], transport.calls
    classroom = roster(f)
    assert classroom["members"][0]["member_id"] != classroom["members"][1]["member_id"]
    assert ClassroomRegistry(f).classes() == [classroom]
    assert [p.read_bytes() for p in sources] == before and transport.calls == calls


def test_duplicate_external_id_or_profile_is_rejected_without_write(case):
    f, _, _, _, _ = case
    classroom = roster(f)
    registry = ClassroomRegistry(f)
    before = f._state.path.read_bytes()
    for field in ("member_id", "external_ref", "profile_id"):
        members = deepcopy(classroom["members"])
        members[1][field] = members[0][field]
        with pytest.raises(ClassroomError):
            registry.save_class("bad", "", members, class_id=classroom["id"], expected_revision=classroom["revision"])
        assert f._state.path.read_bytes() == before


def test_roster_compare_and_swap_preserves_other_namespaces(case):
    f, _, _, _, _ = case
    f._state._update(lambda value: value.update(unrelated={"retained": True}))
    registry = ClassroomRegistry(f)
    classroom = roster(f)
    newer = registry.save_class("新班名", "", classroom["members"], class_id=classroom["id"], expected_revision=classroom["revision"])
    with pytest.raises(ClassroomError):
        registry.save_class("旧输入", "", classroom["members"], class_id=classroom["id"], expected_revision=classroom["revision"])
    assert registry.classes() == [newer]
    assert f._state.snapshot()["unrelated"] == {"retained": True}


def test_assignment_freezes_roster_and_initially_counts_unrecorded(case):
    f, _, _, _, _ = case
    registry = ClassroomRegistry(f)
    classroom = roster(f)
    saved = work(f, classroom)
    registry.save_class("合成班A", "", classroom["members"][:-1], class_id=classroom["id"], expected_revision=classroom["revision"])
    overview = registry.work_overview(saved["id"])
    assert overview["roster_total"] == overview["expected"] == overview["counts"]["unrecorded"] == 4
    assert overview["counts"]["submitted"] == 0 and overview["roster_changed"]


def test_attendance_requires_actual_owner_and_reasons_and_keeps_unknown_out_of_zero(case):
    f, _, subs, transport, _ = case
    registry = ClassroomRegistry(f)
    classroom = roster(f)
    saved = work(f, classroom)
    entries = deepcopy(saved["entries"])
    submitted = next(row for row in classroom["members"] if row["profile_id"] == subs[0].student_id)
    others = [row for row in classroom["members"] if row["member_id"] != submitted["member_id"]]
    mid = submitted["member_id"]
    entries[mid].update(status="submitted", submission_id=subs[1].submission_id)
    with pytest.raises(ClassroomError):
        registry.save_work(saved["id"], entries, expected_revision=saved["revision"])
    entries[mid]["submission_id"] = subs[0].submission_id
    for member, status in zip(others, ("missing", "absent", "exempt")):
        entries[member["member_id"]].update(status=status, note="")
    with pytest.raises(ClassroomError):
        registry.save_work(saved["id"], entries, expected_revision=saved["revision"])
    for member in others:
        entries[member["member_id"]]["note"] = "教师记录的合成依据"
    calls = transport.calls
    updated = registry.save_work(saved["id"], entries, expected_revision=saved["revision"])
    overview = registry.work_overview(saved["id"])
    assert overview["expected"] == 3
    assert overview["counts"] == {"unrecorded": 0, "submitted": 1, "missing": 1, "absent": 1, "exempt": 1}
    assert transport.calls == calls
    assert f.student_analysis_review(student_id=subs[0].student_id, submission_id=subs[0].submission_id).items[0].latest_teacher_score is None
    with pytest.raises(ClassroomError):
        registry.save_work(saved["id"], entries, expected_revision=saved["revision"])
    assert registry.works(classroom["id"])[0] == updated


def submitted_work(f, sub):
    classroom = roster(f)
    registry = ClassroomRegistry(f)
    saved = work(f, classroom)
    entries = deepcopy(saved["entries"])
    member = next(row for row in classroom["members"] if row["profile_id"] == sub.student_id)
    entries[member["member_id"]].update(status="submitted", submission_id=sub.submission_id)
    return registry, registry.save_work(saved["id"], entries, expected_revision=saved["revision"])


def test_review_handoff_only_includes_submitted_and_reuses_same_batch(case):
    f, _, subs, _, _ = case
    registry, saved = submitted_work(f, subs[0])
    first = registry.make_review_batch(saved["id"], saved["revision"])
    second = registry.make_review_batch(saved["id"], saved["revision"])
    assert first == second
    assert first["members"] == [{"student_id": subs[0].student_id, "submission_id": subs[0].submission_id}]
    assert len(WorkBatchStore(f).list_batches()) == 1


def test_source_changes_invalidate_attendance_but_score_changes_do_not(case, monkeypatch):
    f, _, subs, _, _ = case
    registry, saved = submitted_work(f, subs[0])
    sub = subs[0]
    review = f.student_analysis_review(student_id=sub.student_id, submission_id=sub.submission_id)
    f.record_student_score(student_id=sub.student_id, submission_id=sub.submission_id, expected_revision=review.revision,
                           match_id=review.items[0].match_id, teacher_score=1, reason="合成教师评分")
    assert registry.work_overview(saved["id"])["counts"]["submitted"] == 1
    live = f.student_submission(student_id=sub.student_id, submission_id=sub.submission_id)
    changed = replace(live, matches=tuple(replace(m, maximum_score=m.maximum_score + 1) for m in live.matches))
    monkeypatch.setattr(f, "student_submission", lambda **kwargs: changed)
    overview = registry.work_overview(saved["id"])
    assert overview["invalid_references"] == 1 and overview["counts"]["submitted"] == 0
    with pytest.raises(ClassroomError):
        registry.make_review_batch(saved["id"], saved["revision"])


def test_exam_bindings_allow_multiple_classes_and_never_use_name_inference(case, tmp_path):
    f, _, _, _, _ = case
    registry = ClassroomRegistry(f)
    a, b = roster(f, count=2), roster(f, label="合成班B", count=2)
    _, _, exam = example(tmp_path / "scores.xlsx")
    assert registry.exam_binding(exam)["binding"] is None
    first = registry.bind_exam(exam, a["id"], {"S0001": a["members"][0]["member_id"]}, expected_class_revision=a["revision"])
    registry.bind_exam(exam, b["id"], {"S0002": b["members"][0]["member_id"]},
                       expected_class_revision=b["revision"], expected_revision=first["revision"])
    linked = registry.exam_binding(exam)
    assert linked["valid"] and set(linked["rows"]) == {"S0001", "S0002"}
    assert linked["rows"]["S0001"]["member"]["label"] == "同名学生"
    assert linked["rows"]["S0002"]["member"]["label"] == "同名学生"


def test_exam_duplicate_row_links_and_other_class_takeover_rejected(case, tmp_path):
    f, _, _, _, _ = case
    registry = ClassroomRegistry(f)
    a, b = roster(f, count=2), roster(f, label="合成班B", count=2)
    _, _, exam = example(tmp_path / "scores.xlsx")
    with pytest.raises(ClassroomError):
        registry.bind_exam(exam, a["id"], {"S0001": a["members"][0]["member_id"], "S0002": a["members"][0]["member_id"]}, expected_class_revision=a["revision"])
    saved = registry.bind_exam(exam, a["id"], {"S0001": a["members"][0]["member_id"]}, expected_class_revision=a["revision"])
    with pytest.raises(ClassroomError):
        registry.bind_exam(exam, b["id"], {"S0001": b["members"][0]["member_id"]},
                           expected_class_revision=b["revision"], expected_revision=saved["revision"])
    assert registry.exam_binding(exam)["binding"] == saved


def test_exam_score_edits_preserve_binding_but_source_or_roster_change_requires_review(case, tmp_path):
    f, _, _, _, _ = case
    registry = ClassroomRegistry(f)
    classroom = roster(f)
    _, _, exam = example(tmp_path / "scores.xlsx")
    registry.bind_exam(exam, classroom["id"], {"S0001": classroom["members"][0]["member_id"]}, expected_class_revision=classroom["revision"])
    corrected = deepcopy(exam)
    corrected["students"][0]["scores"]["1"] = 1
    assert registry.exam_binding(corrected)["valid"]
    corrected["students"][0]["local_label"] = "行已变化"
    assert not registry.exam_binding(corrected)["valid"]
    registry.save_class("新名称", "", classroom["members"], class_id=classroom["id"], expected_revision=classroom["revision"])
    assert not registry.exam_binding(exam)["valid"]


def test_csv_preview_keeps_same_names_and_requires_unique_supplied_ids():
    rows = preview_roster_csv("姓名或代号,班内编号\n同名,1\n同名,2\n".encode("utf-8-sig"))
    assert len(rows) == 2 and all(row["member_id"] is None and not row["profile_id"] for row in rows)
    with pytest.raises(ClassroomError):
        preview_roster_csv(b"label,external_ref\na,1\nb,1\n")


def test_corrupt_namespace_blocks_unrelated_edit_without_overwriting(case):
    f, _, _, _, _ = case
    f._state._update(lambda value: value.update(classroom_registry={"schema": "broken"}))
    before = f._state.path.read_bytes()
    with pytest.raises(ClassroomError):
        roster(f)
    assert f._state.path.read_bytes() == before


@pytest.mark.parametrize("row", [b"a,1,unexpected", b"a"])
def test_csv_preview_rejects_extra_or_missing_columns_without_truncating_members(row):
    with pytest.raises(ClassroomError):
        preview_roster_csv(b"label,external_ref\n" + row + b"\n")
