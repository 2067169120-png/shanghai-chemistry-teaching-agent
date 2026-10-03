"""Real local business records survive fresh restore; archive data never runs."""
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from test_student_review_desk_core import case  # noqa: F401
from test_classroom_registry import submitted_work
from integrations.deeptutor_shchem_v1.desktop_backup import (
    BackupError, create_backup, inspect_backup, manifest_revision, plan_backup, restore_backup, restored_profile,
)
from integrations.deeptutor_shchem_v1.desktop_business_backup import CONTENT, plan_business_backup
from integrations.deeptutor_shchem_v1.desktop_classroom_registry import ClassroomRegistry
from integrations.deeptutor_shchem_v1.desktop_exam_data import ExamStore
from integrations.deeptutor_shchem_v1.desktop_exam_fixture import example
from integrations.deeptutor_shchem_v1.desktop_exam_mapping_profiles import ExamMappingProfiles
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

# ruff: noqa: F811


def content(tmp_path):
    root = tmp_path / "original-content"
    path = root / "knowledge/textbook/knowledge.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"candidate_only":true,"human_reviewed":false,"teaching_use_allowed":false}\n', encoding="utf-8")
    bank = root / "sh-chem-db/catalog.csv"
    bank.parent.mkdir(parents=True)
    bank.write_text("title,sha256\n合成来源,待核验\n", encoding="utf-8")
    return root


def archive_profile(f, root, tmp_path):
    plan = plan_backup(f.paths.state_root, include_business=True, content_root=root)
    archive = tmp_path / "business.zip"
    result = create_backup(plan, archive)
    checked = inspect_backup(archive)
    assert result["manifest"] == checked
    target = tmp_path / "independent-profile"
    restore_backup(archive, target, expected_manifest=manifest_revision(checked))
    return plan, target, archive


def test_real_sources_score_chains_classrooms_and_exams_survive_without_model_calls(case, tmp_path):
    f, students, subs, transport, _ = case
    registry, work = submitted_work(f, subs[0])
    refs = {"student_id": subs[0].student_id, "submission_id": subs[0].submission_id}
    review = f.student_analysis_review(**refs)
    f.record_student_score(**refs, expected_revision=review.revision, match_id=review.items[0].match_id,
                           teacher_score=1, reason="合成教师评分链依据")
    book, config, exam = example(tmp_path / "scores.xlsx")
    ExamMappingProfiles(f._state).save("合成列映射", book, config)
    ExamStore(f.paths.state_root).save({"exam": exam, "paper": {"text": "", "pages": []},
                                      "notes": "合成说明", "advice": None, "followups": []})
    root = content(tmp_path)
    calls = transport.calls
    before = f._state.path.read_bytes()
    plan, target, _ = archive_profile(f, root, tmp_path)
    paths = DesktopPaths.from_workspace(f.paths.workspace_root, state_root=target)
    paths = replace(paths, workspace_root=paths.content_root, shchem_root=paths.content_root / "sh-chem-db")
    restored = DesktopWorkbenchFacade(paths)
    try:
        assert {p.student_id for p in restored.student_profiles()} == {p.student_id for p in students}
        assert restored.student_analysis_review(**refs).items[0].latest_teacher_score == 1
        assert registry.snapshot() == ClassroomRegistry(restored).snapshot()
        assert ClassroomRegistry(restored).work_overview(work["id"])["counts"]["submitted"] == 1
        assert ExamStore(target).load(exam["id"])["exam"] == exam
        assert len(ExamMappingProfiles(restored._state).profiles()) == 1
        assert paths.content_root != root and paths.source_root == f.paths.workspace_root
        assert (paths.content_root / "knowledge/textbook/knowledge.jsonl").read_bytes() == (root / "knowledge/textbook/knowledge.jsonl").read_bytes()
        assert f._state.path.read_bytes() == before and transport.calls == calls
        for entry in plan.files:
            if entry.name.startswith("student-visual-v1/"):
                assert hashlib.sha256((target / entry.name).read_bytes()).hexdigest() == entry.sha256
    finally:
        restored.shutdown()


def test_sqlite_wal_is_captured_as_consistent_database_not_sidecars(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    path = state / "word-question-attributes.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE labels (value TEXT)")
        database.execute("INSERT INTO labels VALUES ('已提交但仍在WAL中的标签')")
        database.commit()
        assert Path(str(path) + "-wal").exists()
        plan = plan_business_backup(state)
        archive = tmp_path / "sqlite.zip"
        create_backup(plan, archive)
    checked = inspect_backup(archive)
    target = tmp_path / "restored"
    restore_backup(archive, target, expected_manifest=manifest_revision(checked))
    with sqlite3.connect(target / path.name) as database:
        assert database.execute("SELECT value FROM labels").fetchall() == [("已提交但仍在WAL中的标签",)]
        assert database.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert not any(row["path"].endswith(("-wal", "-shm")) for row in checked["files"])


def test_credentials_executables_and_temporary_files_are_outside_archive(tmp_path):
    state = tmp_path / "state"
    DesktopStateStore(state)._update(lambda value: None)
    for relative in ("model-settings/private-key.json", "student-visual-v1/temporary/work.json", "tasks/preparation-v1/script.exe"):
        path = state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a supported business file")
    plan = plan_business_backup(state)
    assert [row.name for row in plan.files] == ["desktop-state.v1.json"]


def test_changed_record_or_new_source_after_preview_blocks_publication(tmp_path):
    state = tmp_path / "state"
    DesktopStateStore(state)._update(lambda value: None)
    plan = plan_business_backup(state)
    DesktopStateStore(state)._update(lambda value: value.update(window={"changed": True}))
    with pytest.raises(BackupError):
        create_backup(plan, tmp_path / "not-published.zip")
    assert not (tmp_path / "not-published.zip").exists()
    plan = plan_business_backup(state)
    (state / "exam-analyses").mkdir()
    (state / "exam-analyses/new.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BackupError):
        create_backup(plan, tmp_path / "not-published.zip")


def changed_archive(source, target, mutate):
    with ZipFile(source) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = json.loads(files["manifest.json"])
    mutate(manifest, files)
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)


@pytest.mark.parametrize("attack", ["traversal", "case_collision", "unlisted", "hash"])
def test_untrusted_archive_cannot_write_outside_new_profile(tmp_path, attack):
    state = tmp_path / "state"
    DesktopStateStore(state)._update(lambda value: None)
    archive = tmp_path / "clean.zip"
    create_backup(plan_business_backup(state), archive)
    def mutate(manifest, files):
        if attack == "traversal":
            old = "files/" + manifest["files"][0]["path"]
            manifest["files"][0]["path"] = "../../escaped.json"
            files["files/../../escaped.json"] = files.pop(old)
        elif attack == "case_collision":
            files["files/DESKTOP-STATE.V1.JSON"] = files["files/desktop-state.v1.json"]
        elif attack == "unlisted":
            files["files/model-settings/key.json"] = b"{}"
        else:
            manifest["files"][0]["sha256"] = "0" * 64
    corrupt = tmp_path / "corrupt.zip"
    changed_archive(archive, corrupt, mutate)
    with pytest.raises(BackupError):
        inspect_backup(corrupt)
    target = tmp_path / "never-restored"
    with pytest.raises(BackupError):
        restore_backup(corrupt, target, expected_manifest="0" * 64)
    assert not target.exists() and not (tmp_path.parent / "escaped.json").exists()


def test_no_clobber_and_cancel_leave_original_profile_and_zip_unchanged(tmp_path):
    state = tmp_path / "state"
    DesktopStateStore(state)._update(lambda value: None)
    before = (state / "desktop-state.v1.json").read_bytes()
    plan = plan_business_backup(state)
    archive = tmp_path / "backup.zip"
    create_backup(plan, archive)
    raw = archive.read_bytes()
    with pytest.raises(BackupError):
        create_backup(plan, archive)
    with pytest.raises(BackupError):
        create_backup(plan, tmp_path / "cancelled.zip", cancel=lambda: True)
    checked = inspect_backup(archive)
    with pytest.raises(BackupError):
        restore_backup(archive, state, expected_manifest=manifest_revision(checked))
    target = tmp_path / "cancelled-restore"
    with pytest.raises(BackupError):
        restore_backup(archive, target, expected_manifest=manifest_revision(checked), cancel=lambda: True)
    assert not target.exists() and archive.read_bytes() == raw
    assert (state / "desktop-state.v1.json").read_bytes() == before


def test_restored_profile_keeps_independent_content_for_second_backup(case, tmp_path):
    f, _, _, _, _ = case
    root = content(tmp_path)
    _, target, _ = archive_profile(f, root, tmp_path)
    assert restored_profile(target) == target
    paths = DesktopPaths.from_workspace(f.paths.workspace_root, state_root=target)
    plan = plan_business_backup(target, content_root=paths.content_root)
    assert any(row.name.startswith(CONTENT) for row in plan.files)


@pytest.mark.parametrize("folder", ["node_modules", "NODE_MODULES", ".cache", "temporary"])
def test_dependency_directory_is_pruned_before_link_or_child_access(tmp_path, monkeypatch, folder):
    state = tmp_path / "state"
    root = content(tmp_path)
    excluded = root / "sh-chem-db" / folder
    excluded.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.json").write_text('{"api_key":"never-read"}', encoding="utf-8")
    original_iterdir = Path.iterdir
    original_symlink = Path.is_symlink
    def iterdir(path):
        if path == excluded:
            raise AssertionError("excluded directory must not be traversed")
        return original_iterdir(path)
    def is_symlink(path):
        if path == excluded:
            return True
        return original_symlink(path)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    plan = plan_business_backup(state, content_root=root)
    assert any(entry.name.endswith("catalog.csv") for entry in plan.files)
    assert all(folder.casefold() not in entry.name.casefold() for entry in plan.files)
    assert plan.skipped >= 1


def test_original_json_with_duplicate_display_keys_restores_byte_for_byte(tmp_path):
    root = content(tmp_path)
    original = root / "sh-chem-db/evidence.json"
    raw = b'{"review_status":"candidate","review_status":"candidate"}'
    original.write_bytes(raw)
    plan = plan_business_backup(tmp_path / "state", content_root=root)
    archive = tmp_path / "original-evidence.zip"
    create_backup(plan, archive)
    checked = inspect_backup(archive)
    target = tmp_path / "restored"
    restore_backup(archive, target, expected_manifest=manifest_revision(checked))
    assert (target / CONTENT / "sh-chem-db/evidence.json").read_bytes() == raw
    assert original.read_bytes() == raw


def test_duplicate_source_fields_cannot_shadow_credentials_or_relax_state_json(tmp_path):
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError
    root = content(tmp_path)
    original = root / "sh-chem-db/evidence.json"
    original.write_bytes(b'{"api_key":"synthetic-private","api_key":null}')
    with pytest.raises((BackupError, DesktopStateError)):
        plan_business_backup(tmp_path / "state", content_root=root)
    original.write_bytes(b'{"status":"candidate","status":"candidate"}')
    state = tmp_path / "state"
    record = state / "exam-analyses/invalid.json"
    record.parent.mkdir(parents=True)
    record.write_bytes(b'{"status":"old","status":"new"}')
    with pytest.raises(BackupError):
        plan_business_backup(state, content_root=root)


@pytest.mark.parametrize("flag", ["api_key_persisted", "weread_api_key_written", "api_key_accessed", "api_key_access_allowed"])
@pytest.mark.parametrize("schema", [{"type":"boolean","const":False},{"const":False}])
def test_source_audit_booleans_are_data_but_credential_values_still_block(tmp_path, flag, schema):
    root = content(tmp_path)
    record = root / "sh-chem-db/audit.json"
    record.write_text(json.dumps({flag:False,"properties":{"api_key_accessed":schema}}), encoding="utf-8")
    plan = plan_business_backup(tmp_path / "state", content_root=root)
    assert any(entry.name.endswith("audit.json") for entry in plan.files)
    record.write_text(json.dumps({flag:"synthetic-private-value"}), encoding="utf-8")
    with pytest.raises(BackupError):
        plan_business_backup(tmp_path / "state", content_root=root)


@pytest.mark.parametrize("business", [False, True])
def test_restored_bootstrap_keeps_running_source_when_data_checkout_is_different(tmp_path, monkeypatch, business):
    from types import SimpleNamespace
    from integrations.deeptutor_shchem_v1.desktop_workbench import app as module
    root = content(tmp_path)
    state = tmp_path / "state"
    plan = plan_backup(state, include_business=business, content_root=root)
    archive = tmp_path / "bootstrap.zip"
    create_backup(plan, archive)
    checked = inspect_backup(archive)
    target = tmp_path / "restored"
    restore_backup(archive, target, expected_manifest=manifest_revision(checked))
    captured = []
    monkeypatch.setattr(module, "create_application", lambda _: SimpleNamespace(exec=lambda:0))
    monkeypatch.setattr(module, "_acquire_desktop_instance_lock", lambda _:SimpleNamespace(unlock=lambda:None))
    monkeypatch.setattr(module, "build_default_facade", lambda paths: captured.append(paths))
    monkeypatch.setattr(module, "TeacherWorkbenchWindow", lambda _:SimpleNamespace(show=lambda:None))
    assert module.run_desktop_workbench(["program","--personal-state",str(target)], workspace_root=root) == 0
    assert captured[0].source_root == Path(module.__file__).resolve().parents[3]
    assert captured[0].source_root != root
    assert captured[0].state_root == target.resolve()
    assert captured[0].workspace_root == (target / "library/workspace" if business else root)
    assert captured[0].shchem_root == (target / "library/workspace/sh-chem-db" if business else target / "library/sh-chem-db")
