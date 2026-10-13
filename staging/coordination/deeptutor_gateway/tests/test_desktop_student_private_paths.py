"""Restored lesson copies retain isolated, external student artifact roots."""
from dataclasses import replace
from pathlib import Path
import os
import subprocess
import time

import pytest
from PIL import Image

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError, DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.model_provider_settings import ModelProviderSettingsError


class NoProviders:
    def list_metadata(self):
        return []


class Curriculum:
    def catalog(self):
        return {"counts": {"sections": 1}, "volumes": [{
            "volume_id": "TB-M1", "volume_title": "合成教材", "chapters": [{
                "chapter_id": "TB-M1-C1", "chapter_title": "合成章", "sections": [{
                    "section_key": "TB-M1-C1:1.1", "section_number": "1.1",
                    "section_title": "合成章节",
                }],
            }],
        }]}


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-userdata"))
    normal = DesktopPaths.from_workspace(workspace)
    restored = replace(
        DesktopPaths.from_workspace(workspace, state_root=workspace / "outputs" / "restore-a"),
        settings_root=normal.settings_root,
    )
    return normal, restored


def facade(paths):
    # Use the production manager, renderer, and disk store; no provider or
    # credential backend is needed to create and reopen local student work.
    return DesktopWorkbenchFacade(
        paths, theme_reader=object(), supplemental_reader=object(),
        curriculum_reader=Curriculum(), search_reader=object(), provider_store=NoProviders(),
    )


def test_external_normal_and_restore_keep_existing_student_root(roots, tmp_path):
    normal, restored = roots
    external = replace(restored, state_root=tmp_path / "external-restore")
    for paths in (normal, external):
        service = facade(paths)
        try:
            assert service.student_profiles() == ()
            assert paths.student_visual_root == paths.state_root / "student-visual-v1"
            assert service._student_manager_instance().root == paths.student_visual_root.resolve()
        finally:
            service.shutdown()


def test_two_project_restores_are_stable_isolated_and_preserve_normal(roots):
    normal, first = roots
    second = replace(first, state_root=first.state_root.with_name("restore-b"))
    canonical_alias = replace(first, state_root=first.state_root / ".." / "restore-a")
    assert first.student_visual_root == canonical_alias.student_visual_root
    assert first.student_visual_root != second.student_visual_root
    assert first.student_visual_root != normal.student_visual_root
    assert first.student_visual_root.parent == normal.settings_root.parent / "restored-student-visual-v1"
    services = [facade(paths) for paths in (normal, first, second)]
    try:
        normal_profile = services[0].create_student_profile(
            grade="高三", retention_days=30, consent_recorded=True,
        )
        normal_bytes = {path: path.read_bytes() for path in normal.student_visual_root.rglob("*") if path.is_file()}
        restored_profile = services[1].create_student_profile(
            grade="高二", retention_days=30, consent_recorded=True,
        )
        assert services[1].student_profiles() == (restored_profile,)
        assert services[2].student_profiles() == ()
        assert services[0].student_profiles() == (normal_profile,)
        assert all(path.read_bytes() == body for path, body in normal_bytes.items())
        assert not (first.state_root / "student-visual-v1").exists()
        assert not (second.state_root / "student-visual-v1").exists()
    finally:
        for service in services:
            service.shutdown()
    reopened = facade(canonical_alias)
    try:
        assert reopened.student_profiles() == (restored_profile,)
    finally:
        reopened.shutdown()


@pytest.mark.parametrize("base", ["state", "workspace", "workspace_parent"])
def test_project_overlapping_private_base_fails_closed(roots, base):
    _, restored = roots
    private_base = {
        "state": restored.state_root,
        "workspace": restored.workspace_root,
        "workspace_parent": restored.workspace_root.parent,
    }[base]
    unsafe = replace(restored, settings_root=private_base / "model-settings")
    service = facade(unsafe)
    try:
        with pytest.raises(DesktopFacadeError) as rejected:
            service.student_profiles()
        assert rejected.value.code == "student_data_root_not_private_external"
        assert not (private_base / "restored-student-visual-v1").exists()
        assert not (restored.state_root / "student-visual-v1").exists()
        assert service._student_analysis_manager is None
    finally:
        service.shutdown()


def test_external_reparse_base_still_rejected_by_manager_guard(roots, tmp_path):
    _, restored = roots
    target = tmp_path / "external-target"
    target.mkdir()
    linked = tmp_path / "linked-private-base"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(target)],
            capture_output=True, check=False,
        )
        assert result.returncode == 0, "owned junction fixture could not be created"
    else:
        linked.symlink_to(target, target_is_directory=True)
    unsafe = replace(restored, settings_root=linked / "model-settings")
    assert unsafe.student_visual_root.is_relative_to(linked)
    service = facade(unsafe)
    try:
        with pytest.raises(ModelProviderSettingsError) as rejected:
            service.student_profiles()
        assert rejected.value.code == "unsafe_settings_path"
        assert service._student_analysis_manager is None
    finally:
        service.shutdown()


def test_real_student_page_prepares_local_media_and_reopens_same_restore(roots, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.student_page import StudentPage
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge

    application = QApplication.instance() or QApplication([])

    def settle(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            application.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        assert predicate(), "real student page task did not complete"

    _, restored = roots
    question, work = tmp_path / "question.png", tmp_path / "work.png"
    Image.new("RGB", (96, 96), "white").save(question)
    Image.new("RGB", (96, 96), "lightblue").save(work)
    service = facade(restored)
    tasks = DesktopTaskBridge()
    page = StudentPage(service, tasks)
    successes, failures = [], []
    tasks.task_succeeded.connect(lambda *args: successes.append(args))
    tasks.task_failed.connect(lambda *args: failures.append(args))
    try:
        settle(lambda: bool(successes) and page._context_task_id is None)
        assert not failures and page.student_combo.count() == 0
        profile = service.create_student_profile(grade="高三", retention_days=30, consent_recorded=True)
        page._student_created(profile)
        page.question_files.file_list._append_paths([str(question)])
        page.student_files.file_list._append_paths([str(work)])
        page._prepare_locally()
        settle(lambda: page._current_summary is not None and page._local_task_id is None)
        summary = page._current_summary
        assert not failures
        assert {item.role for item in summary.pages} == {"question_pages", "student_work_pages"}
        raw = service._student_manager_instance().get_submission(profile.student_id, summary.submission_id)
        assert raw["schema_version"] == "shchem.student-visual-submission.v1"
        media = summary.pages[0]
        before_body, before_mime = service.student_submission_page(
            student_id=profile.student_id, submission_id=summary.submission_id,
            file_id=media.file_id, page_sha256=media.sha256,
        )
        assert before_body and before_mime == "image/png"
        assert not (restored.state_root / "student-visual-v1").exists()
    finally:
        assert tasks.shutdown()
        page.close()
        service.shutdown()

    reopened = facade(restored)
    reopened_tasks = DesktopTaskBridge()
    reopened_page = StudentPage(reopened, reopened_tasks)
    reopened_successes = []
    reopened_tasks.task_succeeded.connect(lambda *args: reopened_successes.append(args))
    try:
        settle(lambda: bool(reopened_successes) and reopened_page._context_task_id is None)
        assert reopened_page.student_combo.count() == 1
        assert reopened_page._selected_student().student_id == profile.student_id
        assert reopened.student_submissions(student_id=profile.student_id)[0].submission_id == summary.submission_id
        assert reopened.student_submission_page(
            student_id=profile.student_id, submission_id=summary.submission_id,
            file_id=media.file_id, page_sha256=media.sha256,
        ) == (before_body, before_mime)
    finally:
        assert reopened_tasks.shutdown()
        reopened_page.close()
        reopened.shutdown()
