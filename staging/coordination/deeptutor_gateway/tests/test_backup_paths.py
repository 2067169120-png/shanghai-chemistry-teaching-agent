"""Canonical reader roots retain containment without rejecting the same directory."""
from pathlib import Path

from integrations.deeptutor_shchem_v1.desktop_backup import (
    PREP, _task_reader, plan_backup, create_backup, inspect_backup,
    manifest_revision, restore_backup,
)
from integrations.deeptutor_shchem_v1.desktop_preparation import DesktopPreparationManager
from test_desktop_preparation import RecordingProvider, FileRenderer, _payload
from test_lesson_backup import seed


def test_read_only_task_reader_canonicalizes_equivalent_root_without_recovering_tasks(tmp_path):
    state, _, _ = seed(tmp_path / "original")
    (state.root / "path-component").mkdir()
    manager = DesktopPreparationManager(state.root / PREP, FileRenderer())
    task = manager.prepare(_payload(), "LOCAL", "REV")
    manager.cancel(task["task_id"])
    path = manager.tasks_root / (task["task_id"] + ".json")
    before = path.read_bytes()
    reader = _task_reader(state.root / "path-component" / "..")
    assert reader.root == (state.root / PREP).resolve()
    assert reader._read_task(task["task_id"])["status"] == "cancelled"
    assert path.read_bytes() == before


def test_terminal_artifacts_restore_under_windows_temp_alias_or_equivalent_path(tmp_path):
    state, _, _ = seed(tmp_path / "original")
    manager = DesktopPreparationManager(state.root / PREP, FileRenderer())
    task = manager.prepare(_payload(output_kind="ppt"), "LOCAL", "REV")
    assert manager.run(task["task_id"], RecordingProvider(), lambda _: None, lambda: False)["status"] == "completed"
    archive = tmp_path / "backup.zip"
    create_backup(plan_backup(state.root, include_tasks=True), archive)
    # Windows CI's TEMP commonly contains RUNNER~1. Keep the actual returned
    # spelling here rather than resolving it in the fixture before production.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="shchem-backup-path-") as temporary:
        parent = Path(temporary)
        (parent / "component").mkdir()
        destination = parent / "component" / ".." / "restored"
        report = inspect_backup(archive)
        restored = restore_backup(archive, destination, expected_manifest=manifest_revision(report))
        reader = _task_reader(Path(restored["directory"]))
        assert reader._read_task(task["task_id"])["status"] == "completed"
