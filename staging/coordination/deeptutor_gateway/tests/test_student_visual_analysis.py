from __future__ import annotations

import contextlib
import copy
import json
import threading
import weakref
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from integrations.deeptutor_shchem_v1 import student_visual_analysis as student_module
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ProbeTransportResponse,
)
from integrations.deeptutor_shchem_v1.model_provider_settings import (
    ModelProviderProbeContext,
    ModelProviderSettingsError,
)
from integrations.deeptutor_shchem_v1.student_visual_analysis import (
    StudentVisualAnalysisError,
    StudentVisualAnalysisManager,
    student_visual_provider_schema,
)

REVISION = "rev_" + "1" * 32


def test_request_is_released_before_credential_context_exit_and_response_parse(
    tmp_path, monkeypatch
):
    request_refs = []
    lifetime_checks = []
    original_build = student_module.build_structured_visual_request
    original_parse = student_module.parse_structured_visual_response

    class TrackedRequest:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    def build(*args, **kwargs):
        request = TrackedRequest(original_build(*args, **kwargs))
        request_refs.append(weakref.ref(request))
        return request

    class LifetimeStore(FakeProviderStore):
        @contextlib.contextmanager
        def borrow_invocation_context(self, profile_id, *, expected_revision):
            with super().borrow_invocation_context(
                profile_id, expected_revision=expected_revision
            ) as context:
                yield context
                assert request_refs[-1]() is None
                lifetime_checks.append("context_exit")

    class NonRetainingTransport(CapturingTransport):
        def send(self, request, **kwargs):
            try:
                return super().send(request, **kwargs)
            finally:
                self.requests.clear()

    def parse(api_style, body):
        assert request_refs[-1]() is None
        assert lifetime_checks == ["context_exit"]
        lifetime_checks.append("parse")
        return original_parse(api_style, body)

    monkeypatch.setattr(student_module, "build_structured_visual_request", build)
    monkeypatch.setattr(student_module, "parse_structured_visual_response", parse)
    transport = NonRetainingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=LifetimeStore(),
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        transport.candidate = candidate_for(submission)
        manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        completed = manager.wait_for_terminal(
            student["student_id"], submission["submission_id"]
        )
        assert completed["status"] == "awaiting_teacher_review"
        assert lifetime_checks == ["context_exit", "parse"]
        assert "secret-not-persisted" not in json.dumps(completed)
    finally:
        manager.shutdown()


def png_bytes(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (480, 320), color).save(output, "PNG")
    return output.getvalue()


def test_submission_history_is_private_owned_and_newest_first(tmp_path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, first = create_uploaded_submission(manager)
        second = manager.create_submission(student["student_id"], {})
        other = manager.create_student({"consent_recorded": True})
        manager.create_submission(other["student_id"], {})
        for index, submission in enumerate((first, second)):
            value = manager._load_submission_for_student(
                student["student_id"], submission["submission_id"]
            )
            value["created_at"] = f"2026-09-0{index + 1}T00:00:00Z"
            manager._save_submission(value, bump=False)
        values = manager.list_submissions(student["student_id"])
        assert [value["submission_id"] for value in values] == [
            second["submission_id"],
            first["submission_id"],
        ]
        assert "private_relative_path" not in json.dumps(values)
        assert "secret-not-persisted" not in json.dumps(values)
        with pytest.raises(StudentVisualAnalysisError):
            manager.list_submissions("../other")
        corrupt = manager._load_submission_for_student(
            student["student_id"], first["submission_id"]
        )
        corrupt["student_id"] = other["student_id"]
        manager._submission_path(
            student["student_id"], first["submission_id"]
        ).write_text(json.dumps(corrupt), encoding="utf-8")
        with pytest.raises(
            StudentVisualAnalysisError, match="submission record is invalid"
        ):
            manager.list_submissions(student["student_id"])
    finally:
        manager.shutdown()


def test_page_reader_checks_ownership_and_exact_file_membership(tmp_path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission = create_uploaded_submission(manager)
        question, work = submission["files"]
        page_hash = question["pages"][0]["sha256"]
        body, mime = manager.read_submission_page(
            student["student_id"],
            submission["submission_id"],
            file_id=question["file_id"],
            page_sha256=page_hash,
        )
        assert mime == "image/png"
        with Image.open(BytesIO(body)) as opened:
            assert opened.size == (480, 320)
        assert manager.transport is not None  # local read needs no provider
        with pytest.raises(StudentVisualAnalysisError):
            manager.read_submission_page(
                student["student_id"],
                submission["submission_id"],
                file_id=work["file_id"],
                page_sha256=page_hash,
            )
        other = manager.create_student({"consent_recorded": True})
        with pytest.raises(StudentVisualAnalysisError) as error:
            manager.read_submission_page(
                other["student_id"],
                submission["submission_id"],
                file_id=question["file_id"],
                page_sha256=page_hash,
            )
        assert error.value.code == "student_scope_denied"
        assert str(tmp_path) not in str(error.value)
    finally:
        manager.shutdown()


def test_page_reader_accepts_legacy_files_pages_storage_root(tmp_path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, public = create_uploaded_submission(manager)
        stored = manager._load_submission_for_student(
            student["student_id"], public["submission_id"]
        )
        record = stored["files"][0]
        page = record["pages"][0]
        current_path = manager.root / page["private_relative_path"]
        legacy_root = (
            manager._submission_path(
                student["student_id"], public["submission_id"]
            ).parent
            / "files"
            / "pages"
            / record["file_id"]
        )
        legacy_root.mkdir(parents=True)
        legacy_path = legacy_root / current_path.name
        current_path.replace(legacy_path)
        page["private_relative_path"] = legacy_path.relative_to(manager.root).as_posix()
        manager._save_submission(stored, bump=False)

        body, mime = manager.read_submission_page(
            student["student_id"],
            public["submission_id"],
            file_id=record["file_id"],
            page_sha256=page["sha256"],
        )
        assert mime == "image/png"
        assert student_module._sha256_bytes(body) == page["sha256"]
    finally:
        manager.shutdown()


def test_shutdown_is_not_blocked_by_slow_page_renderer(tmp_path):
    renderer = BlockingPageRenderer()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=None,
        renderer=renderer,
    )
    student, submission, registered = create_registered_file(manager)
    upload_errors: list[BaseException] = []

    def upload() -> None:
        try:
            manager.upload_file_content(
                student["student_id"],
                submission["submission_id"],
                registered["file"]["file_id"],
                png_bytes("white"),
                content_type="image/png",
            )
        except StudentVisualAnalysisError as exc:
            upload_errors.append(exc)

    upload_thread = threading.Thread(target=upload)
    upload_thread.start()
    assert renderer.entered.wait(2)
    with pytest.raises(StudentVisualAnalysisError) as duplicate:
        manager.upload_file_content(
            student["student_id"],
            submission["submission_id"],
            registered["file"]["file_id"],
            png_bytes("white"),
            content_type="image/png",
        )
    assert duplicate.value.code == "submission_file_upload_in_progress"

    shutdown_done = threading.Event()
    shutdown_thread = threading.Thread(
        target=lambda: (manager.shutdown(), shutdown_done.set())
    )
    shutdown_thread.start()
    closed_without_renderer = shutdown_done.wait(0.75)
    renderer.release.set()
    shutdown_thread.join(2)
    upload_thread.join(2)

    assert closed_without_renderer
    assert not shutdown_thread.is_alive()
    assert not upload_thread.is_alive()
    assert len(upload_errors) == 1
    assert isinstance(upload_errors[0], StudentVisualAnalysisError)
    assert upload_errors[0].code == "student_visual_manager_closed"
    stored = manager._load_submission_for_student(
        student["student_id"], submission["submission_id"]
    )
    assert (
        student["student_id"],
        submission["submission_id"],
        registered["file"]["file_id"],
    ) not in manager._uploads_in_progress
    assert stored["files"][0]["state"] == "awaiting_content"
    content_root = (
        manager._submission_path(
            student["student_id"], submission["submission_id"]
        ).parent
        / "files"
        / "content"
    )
    assert not content_root.exists()
    staging_root = content_root.parent / "staging"
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


def test_slow_upload_rechecks_submission_revision_before_commit(tmp_path):
    renderer = BlockingPageRenderer()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=None,
        renderer=renderer,
    )
    try:
        student, submission, registered = create_registered_file(manager)
        upload_errors: list[BaseException] = []

        def upload() -> None:
            try:
                manager.upload_file_content(
                    student["student_id"],
                    submission["submission_id"],
                    registered["file"]["file_id"],
                    png_bytes("white"),
                    content_type="image/png",
                )
            except StudentVisualAnalysisError as exc:
                upload_errors.append(exc)

        upload_thread = threading.Thread(target=upload)
        upload_thread.start()
        assert renderer.entered.wait(2)
        manager.register_file(
            student["student_id"],
            submission["submission_id"],
            {
                "role": "student_work_pages",
                "filename": "later-student-page.png",
                "mime_type": "image/png",
                "expected_size_bytes": None,
                "expected_sha256": None,
                "expected_revision": registered["revision"],
            },
        )
        renderer.release.set()
        upload_thread.join(2)

        assert not upload_thread.is_alive()
        assert len(upload_errors) == 1
        assert isinstance(upload_errors[0], StudentVisualAnalysisError)
        assert upload_errors[0].code == "revision_conflict"
        stored = manager._load_submission_for_student(
            student["student_id"], submission["submission_id"]
        )
        assert [item["state"] for item in stored["files"]] == [
            "awaiting_content",
            "awaiting_content",
        ]
        content_root = (
            manager._submission_path(
                student["student_id"], submission["submission_id"]
            ).parent
            / "files"
            / "content"
        )
        assert not content_root.exists()
        staging_root = content_root.parent / "staging"
        assert not staging_root.exists() or list(staging_root.iterdir()) == []
    finally:
        renderer.release.set()
        manager.shutdown()


def test_new_registered_page_invalidates_approvals_and_blocks_analysis_race(tmp_path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    renderer = BlockingPageRenderer()
    upload_errors: list[BaseException] = []
    try:
        student, submission = create_uploaded_submission(manager)
        ready = approve_privacy(manager, student, submission)
        assert ready["status"] == "ready_for_analysis"

        registered = manager.register_file(
            student["student_id"],
            ready["submission_id"],
            {
                "role": "reference_answer_pages",
                "filename": "late-reference.png",
                "mime_type": "image/png",
                "expected_size_bytes": None,
                "expected_sha256": None,
                "expected_revision": ready["revision"],
            },
        )
        invalidated = manager.get_submission(
            student["student_id"], ready["submission_id"]
        )
        assert invalidated["status"] == "awaiting_upload"
        assert invalidated["matching"] == {
            "status": "awaiting_pages",
            "matches": [],
            "teacher_confirmed": False,
        }
        assert invalidated["privacy_decision"] is None

        manager.renderer = renderer

        def upload() -> None:
            try:
                manager.upload_file_content(
                    student["student_id"],
                    ready["submission_id"],
                    registered["file"]["file_id"],
                    png_bytes("silver"),
                    content_type="image/png",
                )
            except StudentVisualAnalysisError as exc:
                upload_errors.append(exc)

        upload_thread = threading.Thread(target=upload)
        upload_thread.start()
        assert renderer.entered.wait(2)

        blocked = manager.analyze(
            student["student_id"],
            ready["submission_id"],
            {
                "expected_revision": registered["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        assert blocked["status"] == "awaiting_upload"
        assert blocked["analysis_runs"][-1]["status"] == "blocked"
        assert blocked["analysis_runs"][-1]["blocker"]["code"] == (
            "submission_pages_incomplete"
        )
        assert blocked["analysis_runs"][-1]["model_invoked"] is False
        assert blocked["analysis_runs"][-1]["transport_attempt_count"] == 0
        assert transport.calls == 0

        renderer.release.set()
        upload_thread.join(2)
        assert not upload_thread.is_alive()
        assert len(upload_errors) == 1
        assert isinstance(upload_errors[0], StudentVisualAnalysisError)
        assert upload_errors[0].code == "revision_conflict"
    finally:
        renderer.release.set()
        manager.shutdown()


def test_matching_and_privacy_reject_legacy_pending_file_state(tmp_path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission = create_uploaded_submission(manager)
        stored = manager._load_submission_for_student(
            student["student_id"], submission["submission_id"]
        )
        stored["files"].append(
            {
                "file_id": "SVF-" + "f" * 32,
                "role": "reference_answer_pages",
                "filename_sha256": "0" * 64,
                "mime_type": "image/png",
                "expected_size_bytes": None,
                "expected_sha256": None,
                "state": "awaiting_content",
                "size_bytes": None,
                "sha256": None,
                "private_relative_path": None,
                "pages": [],
                "local_hold": None,
                "created_at": "2026-09-05T00:00:00Z",
            }
        )
        manager._save_submission(stored)

        with pytest.raises(StudentVisualAnalysisError) as matching:
            manager.update_matching(
                student["student_id"],
                submission["submission_id"],
                {
                    "expected_revision": stored["revision"],
                    "matches": submission["matching"]["matches"],
                },
            )
        assert matching.value.code == "submission_pages_incomplete"

        hashes = [
            page["sha256"]
            for record in stored["files"]
            if record["state"] == "stored"
            for page in record["pages"]
        ]
        with pytest.raises(StudentVisualAnalysisError) as privacy:
            manager.record_privacy_decision(
                student["student_id"],
                submission["submission_id"],
                {
                    "expected_revision": stored["revision"],
                    "decision": "approved",
                    "contains_direct_identifiers": False,
                    "confirmed_page_sha256": hashes,
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                    "teacher_confirmed_student_page_egress": True,
                },
            )
        assert privacy.value.code == "submission_pages_incomplete"
    finally:
        manager.shutdown()


def test_analyze_rechecks_closed_state_after_waiting_for_manager_lock(
    tmp_path, monkeypatch
):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    student, submission = create_uploaded_submission(manager)
    ready = approve_privacy(manager, student, submission)
    reached_prelock_check = threading.Event()
    analysis_errors: list[BaseException] = []
    original_ensure_open = manager._ensure_open
    analysis_thread: threading.Thread

    def traced_ensure_open() -> None:
        original_ensure_open()
        if threading.current_thread() is analysis_thread:
            reached_prelock_check.set()

    monkeypatch.setattr(manager, "_ensure_open", traced_ensure_open)

    def analyze() -> None:
        try:
            manager.analyze(
                student["student_id"],
                ready["submission_id"],
                {
                    "expected_revision": ready["revision"],
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                },
            )
        except StudentVisualAnalysisError as exc:
            analysis_errors.append(exc)

    analysis_thread = threading.Thread(target=analyze)
    with manager._lock:
        analysis_thread.start()
        assert reached_prelock_check.wait(2)
        manager.shutdown()
    analysis_thread.join(2)

    assert not analysis_thread.is_alive()
    assert len(analysis_errors) == 1
    assert isinstance(analysis_errors[0], StudentVisualAnalysisError)
    assert analysis_errors[0].code == "student_visual_manager_closed"
    stored = manager._load_submission_for_student(
        student["student_id"], ready["submission_id"]
    )
    assert stored["status"] == "ready_for_analysis"
    assert stored["analysis_runs"] == []
    assert transport.calls == 0


def test_analysis_scheduler_failure_is_terminal_and_sanitized(tmp_path, monkeypatch):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        ready = approve_privacy(manager, student, submission)
        private_detail = r"C:\private\scheduler\broken-worker.log"

        def reject_submit(*_args, **_kwargs):
            raise RuntimeError(private_detail)

        monkeypatch.setattr(manager._executor, "submit", reject_submit)

        with pytest.raises(StudentVisualAnalysisError) as caught:
            manager.analyze(
                student["student_id"],
                ready["submission_id"],
                {
                    "expected_revision": ready["revision"],
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                },
            )

        assert caught.value.code == "analysis_scheduler_unavailable"
        assert caught.value.status == 503
        assert str(caught.value) == "student visual analysis could not be started"
        assert private_detail not in repr(caught.value)
        stored = manager.get_submission(student["student_id"], ready["submission_id"])
        run = stored["analysis_runs"][-1]
        assert stored["status"] == "analysis_failed"
        assert stored["active_run_id"] is None
        assert run["status"] == "failed"
        assert run["model_invoked"] is False
        assert run["transport_attempt_count"] == 0
        assert run["blocker"] == {
            "code": "analysis_scheduler_unavailable",
            "message": "analysis background worker is unavailable",
        }
        assert stored["events"][-1]["event_type"] == "analysis_schedule_failed"
        assert ready["submission_id"] not in manager._cancel_events
        assert private_detail not in json.dumps(stored)
        assert transport.calls == 0
    finally:
        manager.shutdown()


def test_render_failure_persists_only_raw_upload_and_releases_staging(tmp_path):
    renderer = PartialFailingPageRenderer()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=None,
        renderer=renderer,
    )
    try:
        student, submission, registered = create_registered_file(manager)
        raw = png_bytes("white")
        upload_key = (
            student["student_id"],
            submission["submission_id"],
            registered["file"]["file_id"],
        )

        with pytest.raises(StudentVisualAnalysisError) as caught:
            manager.upload_file_content(
                student["student_id"],
                submission["submission_id"],
                registered["file"]["file_id"],
                raw,
                content_type="image/png",
            )

        assert caught.value.code == "page_render_failed"
        assert caught.value.status == 409
        assert str(caught.value) == "student pages could not be prepared"
        assert renderer.private_failure_detail not in repr(caught.value)

        public = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
        assert public["status"] == "upload_processing_failed"
        record = public["files"][0]
        assert record["state"] == "render_failed"
        assert record["size_bytes"] == len(raw)
        assert record["sha256"] == student_module._sha256_bytes(raw)
        assert record["pages"] == []
        assert record["local_hold"] == {
            "contract_version": "shchem.student-image-local-hold.v1",
            "state": "awaiting_visual_provider",
            "raw_local_save_allowed": True,
            "raw_model_access_allowed": False,
            "model_egress_allowed": False,
            "ocr_invoked": False,
            "transport_attempt_count": 0,
            "legacy_derived_evidence_allowed": False,
        }
        assert "private_relative_path" not in record
        assert public["events"][-1]["event_type"] == ("submission_file_render_failed")
        assert public["events"][-1]["details"] == {
            "file_id": record["file_id"],
            "code": "page_render_failed",
        }
        assert renderer.private_failure_detail not in json.dumps(public)

        stored = manager._load_submission_for_student(
            student["student_id"], submission["submission_id"]
        )
        stored_record = stored["files"][0]
        raw_path = manager.root / stored_record["private_relative_path"]
        content_root = (
            manager._submission_path(
                student["student_id"], submission["submission_id"]
            ).parent
            / "files"
            / "content"
            / record["file_id"]
        )
        assert raw_path == content_root / "source.png"
        assert raw_path.read_bytes() == raw
        assert sorted(path.name for path in content_root.iterdir()) == ["source.png"]
        assert not (content_root / "pages").exists()
        staging_root = content_root.parent.parent / "staging"
        assert not staging_root.exists() or list(staging_root.iterdir()) == []
        assert upload_key not in manager._uploads_in_progress
    finally:
        manager.shutdown()


def test_post_rename_permission_failure_rolls_back_upload(tmp_path, monkeypatch):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission, registered = create_registered_file(manager)
        file_id = registered["file"]["file_id"]
        upload_key = (
            student["student_id"],
            submission["submission_id"],
            file_id,
        )
        submission_root = manager._submission_path(
            student["student_id"], submission["submission_id"]
        ).parent
        final_root = submission_root / "files" / "content" / file_id
        staging_root = submission_root / "files" / "staging"
        original_apply_permissions = student_module._apply_owner_only_permissions
        failures = 0

        def fail_after_final_rename(path, *, directory):
            nonlocal failures
            if Path(path) == final_root and directory and final_root.is_dir():
                failures += 1
                raise OSError("fixture post-rename permission failure")
            original_apply_permissions(path, directory=directory)

        monkeypatch.setattr(
            student_module,
            "_apply_owner_only_permissions",
            fail_after_final_rename,
        )

        with pytest.raises(OSError, match="fixture post-rename permission failure"):
            manager.upload_file_content(
                student["student_id"],
                submission["submission_id"],
                file_id,
                png_bytes("white"),
                content_type="image/png",
            )

        assert failures == 1
        current = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
        record = current["files"][0]
        assert current["status"] == "awaiting_upload"
        assert current["revision"] == registered["revision"]
        assert record["state"] == "awaiting_content"
        assert record["size_bytes"] is None
        assert record["sha256"] is None
        assert record["pages"] == []
        assert record["local_hold"] is None
        assert not final_root.exists()
        assert not final_root.parent.exists() or list(final_root.parent.iterdir()) == []
        assert not staging_root.exists() or list(staging_root.iterdir()) == []
        assert upload_key not in manager._uploads_in_progress
    finally:
        manager.shutdown()


def test_private_json_permission_failure_keeps_metadata_and_content_atomic(
    tmp_path, monkeypatch
):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission, registered = create_registered_file(manager)
        file_id = registered["file"]["file_id"]
        upload_key = (
            student["student_id"],
            submission["submission_id"],
            file_id,
        )
        submission_root = manager._submission_path(
            student["student_id"], submission["submission_id"]
        ).parent
        final_root = submission_root / "files" / "content" / file_id
        staging_root = submission_root / "files" / "staging"
        original_apply_permissions = student_module._apply_owner_only_permissions
        failures = 0

        def fail_private_json_before_replace(path, *, directory):
            nonlocal failures
            candidate = Path(path)
            if (
                not directory
                and candidate.parent == submission_root
                and candidate.name.startswith(".submission.json.")
                and final_root.is_dir()
            ):
                failures += 1
                raise OSError("fixture private JSON permission failure")
            original_apply_permissions(path, directory=directory)

        monkeypatch.setattr(
            student_module,
            "_apply_owner_only_permissions",
            fail_private_json_before_replace,
        )

        with pytest.raises(OSError, match="fixture private JSON permission failure"):
            manager.upload_file_content(
                student["student_id"],
                submission["submission_id"],
                file_id,
                png_bytes("white"),
                content_type="image/png",
            )

        assert failures == 1
        current = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
        record = current["files"][0]
        assert current["status"] == "awaiting_upload"
        assert current["revision"] == registered["revision"]
        assert record["state"] == "awaiting_content"
        assert record["size_bytes"] is None
        assert record["sha256"] is None
        assert record["pages"] == []
        assert record["local_hold"] is None
        assert not final_root.exists()
        assert not final_root.parent.exists() or list(final_root.parent.iterdir()) == []
        assert not staging_root.exists() or list(staging_root.iterdir()) == []
        assert list(submission_root.glob(".submission.json.*")) == []
        assert upload_key not in manager._uploads_in_progress
    finally:
        manager.shutdown()


def test_system_exit_after_content_rename_rolls_back_upload(tmp_path, monkeypatch):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission, registered = create_registered_file(manager)
        file_id = registered["file"]["file_id"]
        submission_root = manager._submission_path(
            student["student_id"], submission["submission_id"]
        ).parent
        final_root = submission_root / "files" / "content" / file_id
        staging_root = submission_root / "files" / "staging"
        original_save = manager._save_submission

        def interrupt_before_metadata_save(value, *, bump=True):
            if final_root.is_dir():
                raise SystemExit("fixture interruption after content rename")
            return original_save(value, bump=bump)

        monkeypatch.setattr(manager, "_save_submission", interrupt_before_metadata_save)

        with pytest.raises(
            SystemExit, match="fixture interruption after content rename"
        ):
            manager.upload_file_content(
                student["student_id"],
                submission["submission_id"],
                file_id,
                png_bytes("white"),
                content_type="image/png",
            )

        current = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
        assert current["status"] == "awaiting_upload"
        assert current["revision"] == registered["revision"]
        assert current["files"][0]["state"] == "awaiting_content"
        assert not final_root.exists()
        assert not staging_root.exists() or list(staging_root.iterdir()) == []
        assert (
            student["student_id"],
            submission["submission_id"],
            file_id,
        ) not in manager._uploads_in_progress
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    "mutation",
    [
        "role",
        "size",
        "width",
        "mime",
        "bytes",
        "escape",
        "other_file",
        "not_stored",
        "hardlink",
        "symlink",
    ],
)
def test_page_reader_rejects_tampered_page_binding(tmp_path, mutation):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, public = create_uploaded_submission(manager)
        submission = manager._load_submission_for_student(
            student["student_id"], public["submission_id"]
        )
        record = submission["files"][0]
        page = record["pages"][0]
        digest = page["sha256"]
        path = manager.root / page["private_relative_path"]
        if mutation == "role":
            page["role"] = "student_work_pages"
        elif mutation == "size":
            page["size_bytes"] += 1
        elif mutation == "width":
            page["width"] += 1
        elif mutation == "mime":
            page["mime_type"] = "image/jpeg"
        elif mutation == "bytes":
            path.write_bytes(png_bytes("red"))
        elif mutation == "escape":
            page["private_relative_path"] = "../secret.png"
        elif mutation == "other_file":
            page["private_relative_path"] = submission["files"][1]["pages"][0][
                "private_relative_path"
            ]
        elif mutation == "not_stored":
            record["state"] = "awaiting_content"
        elif mutation == "hardlink":
            (tmp_path / "extra-link.png").hardlink_to(path)
        elif mutation == "symlink":
            target = tmp_path / "external.png"
            target.write_bytes(path.read_bytes())
            path.unlink()
            try:
                path.symlink_to(target)
            except OSError:
                pytest.skip("symlink privilege unavailable")
        manager._save_submission(submission, bump=False)
        with pytest.raises(StudentVisualAnalysisError) as error:
            manager.read_submission_page(
                student["student_id"],
                submission["submission_id"],
                file_id=record["file_id"],
                page_sha256=digest,
            )
        assert str(tmp_path) not in str(error.value)
    finally:
        manager.shutdown()


def test_student_outbound_schema_is_lean_but_local_validation_is_strict(tmp_path):
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=FakeProviderStore(),
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        strict = manager._analysis_schema_for(submission)
        outbound = student_visual_provider_schema(strict)
        Draft202012Validator.check_schema(strict)
        Draft202012Validator.check_schema(outbound)
        allowed = {
            "type",
            "enum",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "anyOf",
        }

        def check(node):
            assert set(node) <= allowed
            assert node.get("enum") != []
            if "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
                for value in node["properties"].values():
                    check(value)
            if "items" in node:
                check(node["items"])
            for value in node.get("anyOf", []):
                check(value)

        check(outbound)
        candidate = candidate_for(submission)
        Draft202012Validator(outbound).validate(candidate)
        manager._validate_analysis_candidate(candidate, submission)
        invalid = copy.deepcopy(candidate)
        invalid["matches"][0]["confidence"] = 3
        Draft202012Validator(outbound).validate(invalid)
        with pytest.raises(StudentVisualAnalysisError):
            manager._validate_analysis_candidate(invalid, submission)
        invalid = copy.deepcopy(candidate)
        invalid["matches"] = []
        Draft202012Validator(outbound).validate(invalid)
        with pytest.raises(StudentVisualAnalysisError):
            manager._validate_analysis_candidate(invalid, submission)
        transport.candidate = candidate
        manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        terminal = manager.wait_for_terminal(
            student["student_id"], submission["submission_id"]
        )
        assert terminal["status"] == "awaiting_teacher_review"
        sent_schema = json.loads(transport.requests[0].body)["text"]["format"]["schema"]
        assert sent_schema == outbound
        assert (
            strict["properties"]["schema_version"]["const"]
            == candidate["schema_version"]
        )
    finally:
        manager.shutdown()


def test_analysis_pages_preserve_registration_order_within_role(tmp_path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, public = create_uploaded_submission(manager)
        submission = manager._load_submission_for_student(
            student["student_id"], public["submission_id"]
        )
        first = copy.deepcopy(submission["files"][0])
        second = copy.deepcopy(submission["files"][1])
        first["file_id"] = "SVF-" + "f" * 32
        second["file_id"] = "SVF-" + "0" * 32
        second["role"] = "question_pages"
        second["pages"][0]["role"] = "question_pages"
        submission["files"] = [first, second]
        result = manager._analysis_pages(submission)
        assert [page["sha256"] for page in result] == [
            first["pages"][0]["sha256"],
            second["pages"][0]["sha256"],
        ]
    finally:
        manager.shutdown()


class FakeProviderStore:
    def __init__(
        self,
        *,
        allowed_data_classes: tuple[str, ...] = (
            "synthetic_only",
            "source_page_image",
            "student_answer_image",
        ),
        image_egress: str = "teacher_confirmed_visual_pages",
    ) -> None:
        self.revision = REVISION
        self.credential = True
        self.allowed_data_classes = allowed_data_classes
        self.image_egress = image_egress

    def invocation_policy(self, profile_id: str, *, expected_revision: str):
        if profile_id != "vision-profile" or expected_revision != self.revision:
            raise ModelProviderSettingsError(
                "revision_conflict", "settings changed", 409
            )
        return {
            "profile_id": profile_id,
            "revision": self.revision,
            "capability_evidence": {
                "catalog": ["text", "vision", "structured_output"],
                "declared": ["text", "vision", "structured_output"],
                "probed": [],
                "unknown": [],
            },
            "effective_capabilities": ["text", "vision", "structured_output"],
            "allowed_data_classes": list(self.allowed_data_classes),
            "image_egress": self.image_egress,
        }

    def credential_exists(self, profile_id: str) -> bool:
        return profile_id == "vision-profile" and self.credential

    @contextlib.contextmanager
    def borrow_invocation_context(self, profile_id: str, *, expected_revision: str):
        self.invocation_policy(profile_id, expected_revision=expected_revision)
        yield ModelProviderProbeContext(
            profile_id=profile_id,
            provider_id="openai",
            model_id="gpt-5",
            base_url_policy="openai_official_https_v1",
            base_url="https://api.openai.com/v1",
            revision=expected_revision,
            api_key="secret-not-persisted",
            api_style="responses",
        )


class CapturingTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []
        self.candidate = None
        self.after_send = None

    def send(self, request, *, cancel_event, deadline_monotonic):
        del cancel_event, deadline_monotonic
        self.calls += 1
        self.requests.append(request)
        if self.after_send is not None:
            self.after_send()
        text = json.dumps(self.candidate, ensure_ascii=False)
        body = json.dumps(
            {
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            }
        ).encode()
        return ProbeTransportResponse(
            http_status=200,
            content_type="application/json",
            content_encoding=None,
            body=body,
            latency_ms=3,
            model_invoked=True,
        )


class BlockingPageRenderer:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def render(self, _source_path, *, mime_type, work_root):
        from integrations.deeptutor_shchem_v1.intake_imports import RenderedPage

        assert mime_type == "image/png"
        self.entered.set()
        assert self.release.wait(5), "blocking renderer was not released"
        work_root.mkdir(parents=True, exist_ok=False)
        target = work_root / "page-0001.png"
        target.write_bytes(png_bytes("white"))
        return [RenderedPage(target, "image/png")]


class PartialFailingPageRenderer:
    private_failure_detail = r"C:\private\renderer\secret-trace.log"

    def render(self, _source_path, *, mime_type, work_root):
        assert mime_type == "image/png"
        work_root.mkdir(parents=True, exist_ok=False)
        (work_root / "page-0001.png").write_bytes(png_bytes("red"))
        raise RuntimeError(self.private_failure_detail)


def create_uploaded_submission(
    manager: StudentVisualAnalysisManager, *, duplicate_role_pixels: bool = False
):
    student = manager.create_student(
        {"grade": "高三", "retention_days": 30, "consent_recorded": True}
    )
    submission = manager.create_submission(student["student_id"], {})
    work_color = "white" if duplicate_role_pixels else "ivory"
    for role, color in (
        ("question_pages", "white"),
        ("student_work_pages", work_color),
    ):
        registered = manager.register_file(
            student["student_id"],
            submission["submission_id"],
            {
                "role": role,
                "filename": f"{role}.png",
                "mime_type": "image/png",
                "expected_size_bytes": None,
                "expected_sha256": None,
                "expected_revision": submission["revision"],
            },
        )
        submission = manager.upload_file_content(
            student["student_id"],
            submission["submission_id"],
            registered["file"]["file_id"],
            png_bytes(color),
            content_type="image/png",
        )
    return student, submission


def create_registered_file(
    manager: StudentVisualAnalysisManager,
    *,
    role: str = "question_pages",
):
    student = manager.create_student({"consent_recorded": True})
    submission = manager.create_submission(student["student_id"], {})
    registered = manager.register_file(
        student["student_id"],
        submission["submission_id"],
        {
            "role": role,
            "filename": f"{role}.png",
            "mime_type": "image/png",
            "expected_size_bytes": None,
            "expected_sha256": None,
            "expected_revision": submission["revision"],
        },
    )
    return student, submission, registered


def approve_privacy(manager, student, submission, *, confirm_matching=True):
    if confirm_matching:
        manager.update_matching(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "matches": submission["matching"]["matches"],
            },
        )
        submission = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
    hashes = [
        page["sha256"]
        for file_record in submission["files"]
        for page in file_record["pages"]
    ]
    return manager.record_privacy_decision(
        student["student_id"],
        submission["submission_id"],
        {
            "expected_revision": submission["revision"],
            "decision": "approved",
            "contains_direct_identifiers": False,
            "confirmed_page_sha256": hashes,
            "provider_profile_id": "vision-profile",
            "provider_revision": REVISION,
            "teacher_confirmed_student_page_egress": True,
        },
    )


def test_domain_privacy_gate_rejects_duplicate_pixels_across_roles(tmp_path: Path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        student, submission = create_uploaded_submission(
            manager, duplicate_role_pixels=True
        )
        manager.update_matching(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "matches": submission["matching"]["matches"],
            },
        )
        submission = manager.get_submission(
            student["student_id"], submission["submission_id"]
        )
        unique_hashes = sorted(
            {
                page["sha256"]
                for file_record in submission["files"]
                for page in file_record["pages"]
            }
        )

        with pytest.raises(StudentVisualAnalysisError) as caught:
            manager.record_privacy_decision(
                student["student_id"],
                submission["submission_id"],
                {
                    "expected_revision": submission["revision"],
                    "decision": "approved",
                    "contains_direct_identifiers": False,
                    "confirmed_page_sha256": unique_hashes,
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                    "teacher_confirmed_student_page_egress": True,
                },
            )

        assert caught.value.code == "submission_page_duplicate"
        assert str(tmp_path) not in str(caught.value)
    finally:
        manager.shutdown()


def curriculum_catalog(section_count: int = 60):
    sections = [
        {
            "section_key": f"TB-M1-C1:{1 + index // 10}.{1 + index % 10}",
            "section_id": None,
            "section_number": f"{1 + index // 10}.{1 + index % 10}",
            "section_title": f"测试教材节{index + 1}",
            "display_label_zh": f"{1 + index // 10}.{1 + index % 10} 测试教材节{index + 1}",
        }
        for index in range(section_count)
    ]
    return {
        "counts": {"sections": section_count},
        "volumes": [
            {
                "volume_id": "TB-M1",
                "volume_title": "必修第一册",
                "display_label_zh": "必修第一册",
                "chapters": [
                    {
                        "chapter_id": "TB-M1-C1",
                        "chapter_title": "第1章 测试章",
                        "display_label_zh": "第1章 测试章",
                        "sections": sections,
                    }
                ],
            }
        ],
    }


def candidate_for(submission):
    match = submission["matching"]["matches"][0]
    page_hashes = [
        page["sha256"]
        for file_record in submission["files"]
        for page in file_record["pages"]
    ]
    bbox = {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.3}
    return {
        "schema_version": "shchem.student-visual-analysis-candidate.v1",
        "page_quality": [
            {
                "page_sha256": digest,
                "quality": "clear",
                "issues": [],
                "confidence": 0.95,
            }
            for digest in page_hashes
        ],
        "matches": [
            {
                "match_id": match["match_id"],
                "atomic_part_id": match["atomic_part_id"],
                "printed_question_id": None,
                "question_number": None,
                "question_anchor": {
                    "page_sha256": match["question_page_sha256"],
                    "bbox": bbox,
                },
                "student_answer_anchor": {
                    "page_sha256": match["student_work_page_sha256"],
                    "bbox": bbox,
                },
                "reference_answer_anchor": (
                    {
                        "page_sha256": match["reference_answer_page_sha256"],
                        "bbox": bbox,
                    }
                    if match["reference_answer_page_sha256"] is not None
                    else None
                ),
                "answer_region_anchor": {
                    "page_sha256": match["student_work_page_sha256"],
                    "bbox": bbox,
                },
                "visual_response_observation": "Visible equation and a numeric result.",
                "chemistry_observations": {
                    "formulas": ["H2"],
                    "charges": [],
                    "conditions": [],
                    "units": ["mol"],
                    "other_visible_details": [],
                },
                "scoring_points": [
                    {
                        "scoring_point_id": match["allowed_scoring_point_ids"][0],
                        "evidence": [
                            {
                                "page_sha256": match["student_work_page_sha256"],
                                "bbox": bbox,
                            }
                        ],
                        "suggested_score": 1.0,
                        "maximum_score": 1.0,
                        "confidence": 0.9,
                        "blockers": [],
                    }
                ],
                "suggested_score": 1.0,
                "maximum_score": 1.0,
                "confidence": 0.9,
                "blockers": [],
                "error_hypotheses": [
                    {
                        "hypothesis": "No error is visible in this provisional part.",
                        "supporting_evidence": ["equation is visibly balanced"],
                        "counterevidence": ["reference answer page was not supplied"],
                        "confidence": 0.6,
                    }
                ],
            }
        ],
        "blockers": [],
        "requires_teacher_review": True,
        "final_score": None,
        "long_term_update_allowed": False,
    }


def complete_and_score(manager, transport, *, teacher_score=0.0):
    student, submission = create_uploaded_submission(manager)
    submission = approve_privacy(manager, student, submission)
    transport.candidate = candidate_for(submission)
    queued = manager.analyze(
        student["student_id"],
        submission["submission_id"],
        {
            "expected_revision": submission["revision"],
            "provider_profile_id": "vision-profile",
            "provider_revision": REVISION,
        },
    )
    completed = manager.wait_for_terminal(
        student["student_id"], queued["submission_id"]
    )
    scoring = manager.append_scoring_decision(
        student["student_id"],
        completed["submission_id"],
        {
            "expected_revision": completed["revision"],
            "match_id": completed["matching"]["matches"][0]["match_id"],
            "teacher_score": teacher_score,
            "reason": "教师已核对可见作答。",
        },
    )
    return (
        student,
        manager.get_submission(student["student_id"], completed["submission_id"]),
        scoring,
    )


def test_no_provider_keeps_local_pages_and_transport_zero(tmp_path: Path):
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=None,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        blocked = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        assert blocked["status"] == "awaiting_visual_provider"
        assert blocked["analysis_runs"][-1]["model_invoked"] is False
        assert blocked["analysis_runs"][-1]["transport_attempt_count"] == 0
        assert transport.calls == 0
        assert all(
            file_record["local_hold"]["ocr_invoked"] is False
            for file_record in blocked["files"]
        )
    finally:
        manager.shutdown()


def test_student_only_egress_policy_rejects_mixed_pages_without_transport(
    tmp_path: Path,
):
    store = FakeProviderStore(
        allowed_data_classes=("synthetic_only", "student_answer_image"),
        image_egress="teacher_confirmed_student_pages",
    )
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        blocked = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )

        assert blocked["status"] == "awaiting_visual_provider"
        assert blocked["analysis_runs"][-1]["blocker"]["code"] == (
            "source_image_data_class_not_allowed"
        )
        assert blocked["analysis_runs"][-1]["model_invoked"] is False
        assert blocked["analysis_runs"][-1]["transport_attempt_count"] == 0
        assert transport.calls == 0
    finally:
        manager.shutdown()


def test_direct_multimodal_request_strict_result_and_append_only_cas(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        registered = manager.register_file(
            student["student_id"],
            submission["submission_id"],
            {
                "role": "reference_answer_pages",
                "filename": "reference_answer_pages.png",
                "mime_type": "image/png",
                "expected_size_bytes": None,
                "expected_sha256": None,
                "expected_revision": submission["revision"],
            },
        )
        submission = manager.upload_file_content(
            student["student_id"],
            submission["submission_id"],
            registered["file"]["file_id"],
            png_bytes("lightgray"),
            content_type="image/png",
        )
        submission = approve_privacy(manager, student, submission)
        transport.candidate = candidate_for(submission)
        queued = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        completed = manager.wait_for_terminal(
            student["student_id"], queued["submission_id"]
        )
        assert completed["status"] == "awaiting_teacher_review"
        assert transport.calls == 1
        request_body = json.loads(transport.requests[0].body)
        content = request_body["input"][0]["content"]
        assert sum(item["type"] == "input_image" for item in content) == 3
        assert "ocr" not in transport.requests[0].body.decode().casefold()
        assert completed["analysis"]["requires_teacher_review"] is True
        assert completed["analysis"]["final_score"] is None
        assert completed["analysis"]["long_term_update_allowed"] is False
        request_summary = completed["analysis_runs"][-1]["request"]
        assert request_summary["data_classes"] == [
            "source_page_image",
            "student_answer_image",
        ]
        assert request_summary["role_classifications"] == [
            {
                "page_sha256": completed["files"][0]["pages"][0]["sha256"],
                "role": "question_pages",
                "data_class": "source_page_image",
            },
            {
                "page_sha256": completed["files"][2]["pages"][0]["sha256"],
                "role": "reference_answer_pages",
                "data_class": "source_page_image",
            },
            {
                "page_sha256": completed["files"][1]["pages"][0]["sha256"],
                "role": "student_work_pages",
                "data_class": "student_answer_image",
            },
        ]
        assert "data_class" not in request_summary
        assert request_summary["egress_policy"] == ("teacher_confirmed_visual_pages.v2")
        decision = manager.append_scoring_decision(
            student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": completed["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "teacher_score": 1,
                "reason": "Teacher checked the visible work.",
            },
        )
        assert decision["decision"]["append_only"] is True
        with pytest.raises(StudentVisualAnalysisError, match="submission changed"):
            manager.append_scoring_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "match_id": completed["matching"]["matches"][0]["match_id"],
                    "teacher_score": 0,
                    "reason": "stale decision",
                },
            )
        review = manager.get_review(student["student_id"], completed["submission_id"])
        assert review["mastery_written"] is False
        assert review["recommendation_written"] is False
    finally:
        manager.shutdown()


def test_cross_student_and_invalid_model_ids_are_rejected(tmp_path: Path):
    manager = StudentVisualAnalysisManager(
        tmp_path / "private", project_root=None, provider_store=None
    )
    try:
        first, submission = create_uploaded_submission(manager)
        second = manager.create_student(
            {"grade": "高二", "retention_days": 30, "consent_recorded": True}
        )
        with pytest.raises(StudentVisualAnalysisError, match="another student"):
            manager.get_submission(second["student_id"], submission["submission_id"])
        matching = submission["matching"]["matches"]
        matching[0]["atomic_part_id"] = "../../bad"
        with pytest.raises(StudentVisualAnalysisError, match="identifier"):
            manager.update_matching(
                first["student_id"],
                submission["submission_id"],
                {"expected_revision": submission["revision"], "matches": matching},
            )
    finally:
        manager.shutdown()


def test_stale_provider_response_is_discarded(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        transport.candidate = candidate_for(submission)
        transport.after_send = lambda: setattr(store, "revision", "rev_" + "2" * 32)
        queued = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        terminal = manager.wait_for_terminal(
            student["student_id"], queued["submission_id"]
        )
        assert terminal["status"] == "awaiting_visual_provider"
        assert terminal["analysis"] is None
        assert terminal["analysis_runs"][-1]["status"] == "stale"
    finally:
        manager.shutdown()


def test_invalid_provider_identifier_is_not_accepted_as_success(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(manager, student, submission)
        transport.candidate = candidate_for(submission)
        transport.candidate["matches"][0]["atomic_part_id"] = "unknown_atomic_id"
        queued = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        terminal = manager.wait_for_terminal(
            student["student_id"], queued["submission_id"]
        )
        assert terminal["status"] == "analysis_failed"
        assert terminal["analysis"] is None
        assert terminal["analysis_runs"][-1]["blocker"]["code"] == (
            "provider_output_schema_invalid"
        )
    finally:
        manager.shutdown()


def test_restart_recovers_active_run_without_accepting_a_result(tmp_path: Path):
    root = tmp_path / "private"
    manager = StudentVisualAnalysisManager(root, project_root=None, provider_store=None)
    student, submission = create_uploaded_submission(manager)
    with manager._lock:
        stored = manager._load_submission_for_student(
            student["student_id"], submission["submission_id"]
        )
        run = manager._new_run(profile_id="vision-profile", revision=REVISION)
        run["status"] = "running"
        run["model_invoked"] = None
        run["transport_attempt_count"] = 1
        stored["analysis_runs"].append(run)
        stored["active_run_id"] = run["run_id"]
        stored["status"] = "analyzing"
        manager._save_submission(stored)
    manager.shutdown()

    recovered = StudentVisualAnalysisManager(
        root, project_root=None, provider_store=None
    )
    try:
        current = recovered.get_submission(
            student["student_id"], submission["submission_id"]
        )
        assert current["status"] == "analysis_failed"
        assert current["analysis"] is None
        assert current["active_run_id"] is None
        assert current["analysis_runs"][-1]["blocker"]["code"] == (
            "analysis_interrupted_by_restart"
        )
    finally:
        recovered.shutdown()


def test_analysis_requires_explicit_teacher_confirmed_matching(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, submission = create_uploaded_submission(manager)
        submission = approve_privacy(
            manager, student, submission, confirm_matching=False
        )
        blocked = manager.analyze(
            student["student_id"],
            submission["submission_id"],
            {
                "expected_revision": submission["revision"],
                "provider_profile_id": "vision-profile",
                "provider_revision": REVISION,
            },
        )
        assert blocked["status"] == "awaiting_matching_confirmation"
        assert blocked["analysis_runs"][-1]["blocker"]["code"] == (
            "matching_teacher_confirmation_required"
        )
        assert blocked["analysis_runs"][-1]["model_invoked"] is False
        assert blocked["analysis_runs"][-1]["transport_attempt_count"] == 0
        assert transport.calls == 0
    finally:
        manager.shutdown()


@pytest.mark.parametrize("terminal_status", ["cancelled", "cancel_requested"])
def test_cancelled_states_cannot_be_revived_by_privacy_or_matching(
    tmp_path: Path, terminal_status: str
):
    manager = StudentVisualAnalysisManager(
        tmp_path / terminal_status, project_root=None, provider_store=None
    )
    try:
        student, submission = create_uploaded_submission(manager)
        if terminal_status == "cancel_requested":
            manager._cancel_events[submission["submission_id"]] = threading.Event()
        terminal = manager.cancel(
            student["student_id"],
            submission["submission_id"],
            {"expected_revision": submission["revision"]},
        )
        assert terminal["status"] == terminal_status
        hashes = [
            page["sha256"]
            for file_record in terminal["files"]
            for page in file_record["pages"]
        ]
        with pytest.raises(StudentVisualAnalysisError) as privacy_error:
            manager.record_privacy_decision(
                student["student_id"],
                terminal["submission_id"],
                {
                    "expected_revision": terminal["revision"],
                    "decision": "approved",
                    "contains_direct_identifiers": False,
                    "confirmed_page_sha256": hashes,
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                    "teacher_confirmed_student_page_egress": True,
                },
            )
        assert privacy_error.value.code == "privacy_decision_closed"
        with pytest.raises(StudentVisualAnalysisError) as matching_error:
            manager.update_matching(
                student["student_id"],
                terminal["submission_id"],
                {
                    "expected_revision": terminal["revision"],
                    "matches": terminal["matching"]["matches"],
                },
            )
        assert matching_error.value.code == "matching_closed"
    finally:
        manager.shutdown()


def test_teacher_review_state_closes_pre_analysis_mutations(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
    )
    try:
        student, completed, _scoring = complete_and_score(manager, transport)
        hashes = [
            page["sha256"]
            for file_record in completed["files"]
            for page in file_record["pages"]
        ]
        with pytest.raises(StudentVisualAnalysisError) as privacy_error:
            manager.record_privacy_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "decision": "approved",
                    "contains_direct_identifiers": False,
                    "confirmed_page_sha256": hashes,
                    "provider_profile_id": "vision-profile",
                    "provider_revision": REVISION,
                },
            )
        assert privacy_error.value.code == "privacy_decision_closed"
        with pytest.raises(StudentVisualAnalysisError) as matching_error:
            manager.update_matching(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "matches": completed["matching"]["matches"],
                },
            )
        assert matching_error.value.code == "matching_closed"
    finally:
        manager.shutdown()


def test_diagnostic_journal_binds_latest_score_candidate_and_catalog(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
        curriculum_catalog=curriculum_catalog(),
    )
    try:
        student, completed, scoring = complete_and_score(manager, transport)
        section_a = "TB-M1-C1:1.1"
        section_b = "TB-M1-C1:1.2"
        first = manager.append_diagnostic_decision(
            student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": completed["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "scoring_decision_id": scoring["decision"]["decision_id"],
                "decision": "accept",
                "result": "incorrect",
                "primary_error_type": "concept",
                "secondary_error_types": ["expression", "chemical_language"],
                "curriculum_section_keys": [section_b, section_a],
                "teacher_note": "  教师确认概念与表达均有问题。  ",
            },
        )
        record = first["decision"]
        assert record["analysis_id"] == completed["analysis"]["analysis_id"]
        assert len(record["candidate_sha256"]) == 64
        assert record["scoring_decision_id"] == scoring["decision"]["decision_id"]
        assert record["secondary_error_types"] == [
            "chemical_language",
            "expression",
        ]
        assert [item["section_key"] for item in record["curriculum_sections"]] == [
            section_a,
            section_b,
        ]
        assert record["curriculum_sections"][0]["volume_title_zh"] == "必修第一册"
        assert record["curriculum_sections"][0]["chapter_title_zh"] == "第1章 测试章"
        assert record["teacher_note"] == "教师确认概念与表达均有问题。"
        second = manager.append_diagnostic_decision(
            student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": first["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "scoring_decision_id": scoring["decision"]["decision_id"],
                "decision": "edit",
                "result": "partial",
                "primary_error_type": "evidence_reasoning",
                "secondary_error_types": [],
                "curriculum_section_keys": [section_a],
                "teacher_note": "第二次决定覆盖首次分类。",
            },
        )
        review = manager.get_diagnostic_review(
            student["student_id"], completed["submission_id"]
        )
        assert review["curriculum_section_allowlist_count"] == 60
        assert review["review"] == {
            "status": "teacher_decisions_recorded",
            "decision_count": 2,
            "distinct_match_count": 1,
            "match_count": 1,
        }
        assert len(review["diagnostic_decisions"]) == 2
        assert len(review["latest_diagnostic_decisions"]) == 1
        assert (
            review["latest_diagnostic_decisions"][0]["decision_id"]
            == (second["decision"]["decision_id"])
        )
        assert review["chain_head_sha256"] == second["decision"]["record_sha256"]
        assert review["final_score"] is None
        assert review["mastery_written"] is False
        assert review["recommendation_written"] is False
    finally:
        manager.shutdown()


def test_diagnostic_rejects_unknown_section_stale_revision_and_old_score(
    tmp_path: Path,
):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
        curriculum_catalog=curriculum_catalog(),
    )
    try:
        student, completed, first_score = complete_and_score(manager, transport)
        match_id = completed["matching"]["matches"][0]["match_id"]
        with pytest.raises(StudentVisualAnalysisError) as unknown:
            manager.append_diagnostic_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "match_id": match_id,
                    "scoring_decision_id": first_score["decision"]["decision_id"],
                    "decision": "accept",
                    "result": "incorrect",
                    "primary_error_type": "concept",
                    "secondary_error_types": [],
                    "curriculum_section_keys": ["TB-M1-C9:9.9"],
                    "teacher_note": "unknown",
                },
            )
        assert unknown.value.code == "curriculum_section_unknown"
        second_score = manager.append_scoring_decision(
            student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": completed["revision"],
                "match_id": match_id,
                "teacher_score": 0,
                "reason": "教师复核后更新评分。",
            },
        )
        with pytest.raises(StudentVisualAnalysisError) as stale:
            manager.append_diagnostic_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "match_id": match_id,
                    "scoring_decision_id": second_score["decision"]["decision_id"],
                    "decision": "accept",
                    "result": "incorrect",
                    "primary_error_type": "concept",
                    "secondary_error_types": [],
                    "curriculum_section_keys": ["TB-M1-C1:1.1"],
                    "teacher_note": "stale",
                },
            )
        assert stale.value.code == "revision_conflict"
        current = manager.get_submission(
            student["student_id"], completed["submission_id"]
        )
        with pytest.raises(StudentVisualAnalysisError) as old_score:
            manager.append_diagnostic_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": current["revision"],
                    "match_id": match_id,
                    "scoring_decision_id": first_score["decision"]["decision_id"],
                    "decision": "accept",
                    "result": "incorrect",
                    "primary_error_type": "concept",
                    "secondary_error_types": [],
                    "curriculum_section_keys": ["TB-M1-C1:1.1"],
                    "teacher_note": "old score",
                },
            )
        assert old_score.value.code == "scoring_decision_not_latest"
    finally:
        manager.shutdown()


@pytest.mark.parametrize(
    ("decision", "result", "primary", "sections", "expected_code"),
    [
        ("accept", "correct", "concept", [], "diagnostic_error_not_allowed"),
        ("reject", "incorrect", "concept", [], "diagnostic_error_not_allowed"),
        ("pending", "blank", "concept", [], "diagnostic_error_not_allowed"),
        ("accept", "partial", None, [], "diagnostic_evidence_required"),
    ],
)
def test_diagnostic_cross_field_rules_fail_closed(
    tmp_path: Path,
    decision: str,
    result: str,
    primary: str | None,
    sections: list[str],
    expected_code: str,
):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / f"{decision}-{result}",
        project_root=None,
        provider_store=store,
        transport=transport,
        curriculum_catalog=curriculum_catalog(),
    )
    try:
        student, completed, scoring = complete_and_score(manager, transport)
        with pytest.raises(StudentVisualAnalysisError) as exc:
            manager.append_diagnostic_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": completed["revision"],
                    "match_id": completed["matching"]["matches"][0]["match_id"],
                    "scoring_decision_id": scoring["decision"]["decision_id"],
                    "decision": decision,
                    "result": result,
                    "primary_error_type": primary,
                    "secondary_error_types": [],
                    "curriculum_section_keys": sections,
                    "teacher_note": "cross-field",
                },
            )
        assert exc.value.code == expected_code
    finally:
        manager.shutdown()


def test_diagnostic_chain_tampering_and_cancelled_write_are_rejected(tmp_path: Path):
    store = FakeProviderStore()
    transport = CapturingTransport()
    manager = StudentVisualAnalysisManager(
        tmp_path / "private",
        project_root=None,
        provider_store=store,
        transport=transport,
        curriculum_catalog=curriculum_catalog(),
    )
    try:
        student, completed, scoring = complete_and_score(manager, transport)
        appended = manager.append_diagnostic_decision(
            student["student_id"],
            completed["submission_id"],
            {
                "expected_revision": completed["revision"],
                "match_id": completed["matching"]["matches"][0]["match_id"],
                "scoring_decision_id": scoring["decision"]["decision_id"],
                "decision": "accept",
                "result": "incorrect",
                "primary_error_type": "concept",
                "secondary_error_types": [],
                "curriculum_section_keys": ["TB-M1-C1:1.1"],
                "teacher_note": "original",
            },
        )
        path = manager._diagnostic_decision_path(
            student["student_id"], completed["submission_id"]
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        value["teacher_note"] = "tampered"
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        with pytest.raises(StudentVisualAnalysisError) as corrupt:
            manager.get_diagnostic_review(
                student["student_id"], completed["submission_id"]
            )
        assert corrupt.value.code == "diagnostic_decision_store_corrupt"

        # Restore the valid immutable record, then force a terminal state to
        # exercise the manager's fail-closed write boundary.
        path.write_text(
            json.dumps(
                appended["decision"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with manager._lock:
            stored = manager._load_submission_for_student(
                student["student_id"], completed["submission_id"]
            )
            stored["status"] = "cancelled"
            manager._save_submission(stored)
        cancelled = manager.get_submission(
            student["student_id"], completed["submission_id"]
        )
        with pytest.raises(StudentVisualAnalysisError) as closed:
            manager.append_diagnostic_decision(
                student["student_id"],
                completed["submission_id"],
                {
                    "expected_revision": cancelled["revision"],
                    "match_id": completed["matching"]["matches"][0]["match_id"],
                    "scoring_decision_id": scoring["decision"]["decision_id"],
                    "decision": "accept",
                    "result": "incorrect",
                    "primary_error_type": "concept",
                    "secondary_error_types": [],
                    "curriculum_section_keys": ["TB-M1-C1:1.1"],
                    "teacher_note": "closed",
                },
            )
        assert closed.value.code == "diagnostic_decision_closed"
    finally:
        manager.shutdown()
