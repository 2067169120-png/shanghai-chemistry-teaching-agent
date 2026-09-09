from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_desktop_preparation import (
    FileRenderer,
    RecordingProvider,
    _payload,
    _raw_candidate,
)
from test_preparation_revision_ui import HeldTasks

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationError,
    DesktopPreparationManager,
    normalize_preparation_candidate,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_recovery import (
    apply_returned_comparison_edits,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_recovery_dialog import (
    PreparationRecoveryDialog,
)

pytest_plugins = ("test_desktop_ui",)


def long_candidate():
    raw = _raw_candidate()
    for slide in raw["slides"][1:]:
        slide["visual"] = {
            "kind": "comparison",
            "steps": [],
            "comparison": {
                "dimension_label": "比较维度",
                "columns": ["甲", "乙"],
                "rows": [
                    {"label": f"维度{i}", "values": [f"甲{i}", f"乙{i}"]}
                    for i in range(5)
                ],
            },
        }
    return raw


def split_edit(raw, number=2, cut=2, first=1):
    return {
        "slide_number": number,
        "comparison": deepcopy(raw["slides"][number - 1]["visual"]["comparison"]),
        "split_after_row": cut,
        "first_page_minutes": first,
    }


def test_multiple_splits_use_original_numbers_and_preserve_every_row_context_and_minute():
    raw = long_candidate()
    before = deepcopy(raw)
    edited = apply_returned_comparison_edits(
        raw, [split_edit(raw, 3, 3, 4), split_edit(raw, 2, 2, 12)]
    )
    assert raw == before
    assert len(edited["slides"]) == 5
    assert edited["slides"][0] == raw["slides"][0]
    for original_index, new_index in ((1, 1), (2, 3)):
        original = raw["slides"][original_index]
        pair = edited["slides"][new_index : new_index + 2]
        assert [
            r for page in pair for r in page["visual"]["comparison"]["rows"]
        ] == original["visual"]["comparison"]["rows"]
        assert sum(page["minutes"] for page in pair) == original["minutes"]
        for part, page in enumerate(pair, 1):
            assert page["title"] == original["title"] + f"（{part}/2）"
            for key in (
                "content",
                "teacher_notes",
                "objective_numbers",
                "activity_numbers",
                "assessment_numbers",
            ):
                assert page[key] == original[key]
    for key in raw.keys() - {"slides"}:
        assert edited[key] == raw[key]
    normalized = normalize_preparation_candidate(edited, _payload())
    assert [s["minutes"] for s in normalized["slides"]] == [5, 12, 13, 4, 6]
    assert [s["id"] for s in normalized["slides"]] == [
        "S01",
        "S02",
        "S03",
        "S04",
        "S05",
    ]


@pytest.mark.parametrize(
    "cut,first",
    [(1, 1), (4, 1), (True, 1), (2.0, 1), (2, 0), (2, 25), (2, True), (2, 1.5)],
)
def test_split_rejects_bad_row_boundary_or_time_without_mutating(cut, first):
    raw = long_candidate()
    before = deepcopy(raw)
    with pytest.raises(DesktopPreparationError):
        apply_returned_comparison_edits(raw, [split_edit(raw, cut=cut, first=first)])
    assert raw == before


def test_split_validates_second_half_and_requires_both_explicit_fields():
    raw = long_candidate()
    edit = split_edit(raw)
    edit["comparison"]["rows"][-1]["values"].pop()
    with pytest.raises(DesktopPreparationError, match="比较表"):
        apply_returned_comparison_edits(raw, [edit])
    edit = split_edit(raw)
    del edit["first_page_minutes"]
    with pytest.raises(DesktopPreparationError):
        apply_returned_comparison_edits(raw, [edit])


def test_split_child_normalizes_and_exports_locally_preserving_failed_parent(tmp_path):
    raw, renderer = long_candidate(), FileRenderer()
    provider = RecordingProvider(raw)
    manager = DesktopPreparationManager(tmp_path / "preparation", renderer)
    task = manager.prepare(_payload(), "PROFILE-TEXT", "REV-SPLIT")
    assert (
        manager.run(task["task_id"], provider, lambda _: None, lambda: False)["status"]
        == "failed"
    )
    source = manager.returned_source(task["task_id"])
    repaired = apply_returned_comparison_edits(
        raw, [split_edit(raw), split_edit(raw, 3)]
    )
    child = manager.create_returned_revision(
        task["task_id"],
        source["source_revision"],
        normalize_preparation_candidate(repaired, source["payload"]),
    )
    assert (
        manager.run(child["task_id"], None, lambda _: None, lambda: False)["status"]
        == "completed"
    )
    assert len(provider.calls) == 1 and len(renderer.calls) == 1
    assert len(renderer.calls[0]["candidate"]["slides"]) == 5
    assert manager.get_task(task["task_id"])["status"] == "failed"
    assert manager.returned_source(task["task_id"])["candidate"] == raw


def test_native_split_review_navigation_and_submission_preserve_source(qt_app):
    raw, tasks, calls = long_candidate(), HeldTasks(), []
    source = {"candidate": raw, "task_id": "failed", "source_revision": "a" * 64}
    before = deepcopy(source)
    facade = SimpleNamespace(
        repair_returned_preparation=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or SimpleNamespace(status="completed")
        )
    )
    dialog = PreparationRecoveryDialog(facade, tasks, source)
    dialog.show()
    try:
        assert dialog.current_number == 2 and dialog.table.rowCount() == 6
        assert dialog.split_enabled.isEnabled() and not dialog.save_button.isEnabled()
        dialog.split_enabled.click()
        dialog.split_after.setValue(3)
        dialog.first_minutes.setValue(10)
        assert not dialog.pending and dialog.save_button.isEnabled()
        dialog.group.setCurrentIndex(1)
        dialog.split_enabled.click()
        dialog.group.setCurrentIndex(0)
        assert dialog.split_after.value() == 3 and dialog.first_minutes.value() == 10
        assert "10＋15＝25" in dialog.split_hint.text()
        dialog.tabs.setCurrentIndex(2)
        assert "第一页完整表" in dialog.review.toPlainText()
        assert "第二页完整表" in dialog.review.toPlainText()
        assert "维度4" in dialog.review.toPlainText()
        dialog._save()
        assert dialog.busy and not calls
        tasks.success(tasks.operation())
        assert calls[0][0][2] == [split_edit(raw, 2, 3, 10), split_edit(raw, 3)]
        assert not dialog.splits and not dialog.isVisible()
        assert source == before
    finally:
        dialog.splits.clear()
        dialog.pending.clear()
        dialog.busy = False
        dialog.close()


def test_native_invalid_split_keeps_edits_and_uncheck_restores_no_changes(qt_app):
    raw, tasks = long_candidate(), HeldTasks()
    dialog = PreparationRecoveryDialog(SimpleNamespace(), tasks, {"candidate": raw})
    try:
        dialog.split_enabled.click()
        dialog.split_after.setValue(4)
        dialog._save()
        assert "2—4行" in dialog.status.text() and not hasattr(tasks, "operation")
        assert dialog.splits
        dialog.split_enabled.click()
        assert not dialog.splits and not dialog.save_button.isEnabled()
    finally:
        dialog.splits.clear()
        dialog.close()
