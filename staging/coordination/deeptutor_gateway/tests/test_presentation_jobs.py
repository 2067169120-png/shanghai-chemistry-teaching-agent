from __future__ import annotations

import hashlib
import inspect
import json
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1 import presentation_workbench as core
from integrations.deeptutor_shchem_v1.presentation_jobs import (
    ARTIFACT_FILENAMES,
    PresentationJobManager,
    PresentationWorkbenchError,
)

OWNER = "teacher@example.invalid"
SESSION = "a" * 64
ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44"
    "ae426082"
)


class FakeToolchain:
    def __init__(self) -> None:
        self.validate_calls = 0

    def validate(self) -> None:
        self.validate_calls += 1


class FakePipeline:
    def __init__(
        self,
        *,
        fail_attempts: int = 0,
        machine_checks_passed: bool = True,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.fail_attempts = fail_attempts
        self.machine_checks_passed = machine_checks_passed
        self.started = started
        self.release = release
        self.render_calls = 0

    @staticmethod
    def validate_input(value: Mapping[str, Any], *, asset_root: Path) -> dict[str, Any]:
        assert asset_root.name == "assets"
        assert asset_root.is_dir()
        return deepcopy(dict(value))

    @staticmethod
    def compose(value: Mapping[str, Any], *, asset_root: Path) -> dict[str, Any]:
        assert asset_root.name == "assets"
        return {
            "schema_version": "fixture.deck.v1",
            "deck_id": "DECK-FIXTURE",
            "lesson_title": value["lesson_title"],
            "slides": [
                {
                    "slide_number": 1,
                    "slide_id": "SLIDE-1",
                    "title": value["lesson_title"],
                    "content": ["candidate only"],
                }
            ],
            "publication_allowed": False,
            "teacher_confirmation_required": True,
        }

    @staticmethod
    def validate_deck(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or not isinstance(value.get("slides"), list):
            raise PresentationWorkbenchError("fixture_deck_invalid", "fixture deck invalid", 400)
        return deepcopy(dict(value))

    def render(
        self,
        deck: Mapping[str, Any],
        *,
        asset_root: Path,
        output_dir: Path,
        toolchain: FakeToolchain,
        filename: str = "lesson_presentation.pptx",
    ) -> dict[str, Any]:
        self.render_calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(10)
        if self.render_calls <= self.fail_attempts:
            raise PresentationWorkbenchError(
                "fixture_render_failed", "fixture renderer failed", 409
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        deck_path = output_dir / ARTIFACT_FILENAMES["deck_json"]
        deck_path.write_text(
            json.dumps(deck, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        pptx_path = output_dir / filename
        pptx_path.write_bytes(b"PK\x03\x04fixture-pptx")
        montage_path = output_dir / ARTIFACT_FILENAMES["preview_montage"]
        montage_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture-montage")
        qa = {
            "schema_version": "fixture.qa.v1",
            "deck_id": deck["deck_id"],
            "artifact_id": deck["deck_id"],
            "qa_status": (
                "pending_full_page_visual_review"
                if self.machine_checks_passed
                else "failed_machine_qa"
            ),
            "machine_checks_passed": self.machine_checks_passed,
            "checks": [],
            "visual_review": {
                "status": "pending",
                "slides": [
                    {
                        "slide_number": 1,
                        "slide_id": "SLIDE-1",
                        "status": "pending",
                    }
                ],
            },
            "chemistry_review": {"human_reviewed": False},
            "publication_allowed": False,
            "teacher_confirmation_required": True,
        }
        qa_path = output_dir / ARTIFACT_FILENAMES["qa_report"]
        qa_path.write_text(
            json.dumps(qa, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "deck_json": deck_path,
            "pptx": pptx_path,
            "author_previews": output_dir / "author_previews",
            "rendered_slides": output_dir / "rendered_slides",
            "rendered_montage": montage_path,
            "qa_report": qa_path,
            "qa_status": qa["qa_status"],
            "artifact_id": deck["deck_id"],
        }

    @staticmethod
    def write_manifest(output_dir: Path) -> dict[str, Any]:
        rows = []
        for artifact_id, filename in ARTIFACT_FILENAMES.items():
            path = output_dir / filename
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "filename": filename,
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        deck = json.loads((output_dir / ARTIFACT_FILENAMES["deck_json"]).read_text(encoding="utf-8"))
        manifest = {
            "schema_version": "fixture.output-manifest.v1",
            "artifact_id": deck["deck_id"],
            "publication_allowed": False,
            "teacher_confirmation_required": True,
            "artifacts": rows,
        }
        (output_dir / "output_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest


def _manager(
    state_root: Path,
    pipeline: FakePipeline | None = None,
    *,
    max_workers: int = 1,
) -> tuple[PresentationJobManager, FakePipeline, FakeToolchain]:
    pipeline = pipeline or FakePipeline()
    toolchain = FakeToolchain()
    manager = PresentationJobManager(
        state_root,
        toolchain,
        max_workers=max_workers,
        input_validator=pipeline.validate_input,
        deck_composer=pipeline.compose,
        deck_validator=pipeline.validate_deck,
        renderer=pipeline.render,
        manifest_writer=pipeline.write_manifest,
    )
    return manager, pipeline, toolchain


def _create_version(
    manager: PresentationJobManager,
    *,
    owner: str = OWNER,
    session: str = SESSION,
    title: str = "化学平衡",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = manager.create_project(
        owner,
        session,
        {"title_zh": title, "description_zh": "fixture"},
    )
    outline = manager.create_outline(
        owner,
        session,
        project["project_id"],
        {"schema_version": "fixture.input.v1", "lesson_title": title},
    )
    version = manager.create_version(
        owner,
        session,
        project["project_id"],
        expected_outline_revision=outline["revision"],
    )
    return project, outline, version


def _wait_job(
    manager: PresentationJobManager,
    job_id: str,
    *,
    owner: str = OWNER,
    session: str = SESSION,
    statuses: set[str] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    statuses = statuses or {"completed", "failed", "cancelled"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(owner, session, job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {statuses}")


def _record_sha256(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "record_sha256"}
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _real_core_input() -> dict[str, Any]:
    return {
        "schema_version": "shchem.presentation-input.v1",
        "lesson_title": "合成课时",
        "grade": "高中",
        "duration_minutes": 40,
        "candidate_use": {
            "intended_use": "local_personal_lesson_preparation_candidate",
            "publication_allowed": False,
            "teacher_confirmation_required": True,
        },
        "textbook": {
            "book_title": "教材",
            "volume": "必修",
            "chapter": "第1章",
            "section": "第1节",
            "publisher": "出版社",
            "evidence_level": "L1_LOCAL_TEXTBOOK",
            "evidence_anchors": [
                {
                    "anchor_id": "TB-1",
                    "source_path": "book.pdf",
                    "source_sha256": "0" * 64,
                    "page_number": 1,
                    "printed_page": 1,
                    "evidence_text": "本页只作合成结构测试。",
                    "source_ref": "教材第1页",
                }
            ],
            "notes": ["合成证据。"],
        },
        "lesson_goals": {
            "learning_objectives": ["说明核心关系。"],
            "key_points": ["关系"],
            "difficult_points": ["证据转化"],
            "prerequisites": ["基础概念"],
            "lesson_emphasis": "先证据后结论。",
        },
        "theme": {
            "paper": {
                "paper_id": "P-SYN",
                "title": "合成主题",
                "year": 2026,
                "region_or_school": "合成",
                "paper_type": "结构测试",
                "source_ref": "synthetic fixture",
                "source_authority": "synthetic_fixture_only",
            },
            "theme_id": "T-SYN",
            "title": "合成主题一",
            "order": 1,
            "page_span": [1, 1],
            "context_summary": "用于工作流结构测试。",
            "source_authority": "synthetic_fixture_only",
            "answer_authority": "teacher_candidate",
            "human_reviewed": False,
            "publication_allowed": False,
            "assets": [],
            "shared_materials": [
                {
                    "shared_material_id": "SM-1",
                    "title": "共同材料",
                    "summary": "合成材料摘要。",
                    "source_page": 1,
                    "asset_ids": [],
                    "source_ref": "synthetic fixture",
                }
            ],
            "printed_questions": [
                {
                    "printed_question_id": "PRINT-A",
                    "display_number": "甲",
                    "prompt": "依据材料完成任务。",
                    "source_page": 1,
                    "asset_ids": [],
                    "shared_material_ids": ["SM-1"],
                    "atomic_parts": [
                        {
                            "atomic_part_id": "ATOM-A",
                            "label": "甲-1",
                            "task": "说明理由。",
                            "response_mode": "short_answer",
                            "answer_status": "teacher_candidate",
                            "answer_text": "候选答案。",
                            "answer_quality_note": None,
                            "answer_authority": "teacher_candidate",
                            "dependencies": ["SM-1"],
                            "chemical_expressions": [],
                        }
                    ],
                }
            ],
        },
        "diagnosis": {
            "label": "匿名合成摘要",
            "anonymized": True,
            "synthetic": True,
            "evidence_status": "synthetic_fixture_only",
            "summary": "仅测试结构。",
            "common_errors": [
                {
                    "error_id": "ERR-1",
                    "description": "证据不足。",
                    "linked_atomic_part_ids": ["ATOM-A"],
                    "cause_hypothesis": "未建立证据链。",
                    "confidence": 0.5,
                }
            ],
            "counterevidence": ["单一合成样本不能形成真实诊断。"],
        },
        "classroom_plan": {
            "teacher_questions": ["证据是什么？"],
            "anticipated_responses": ["从材料读取。"],
            "practice_atomic_part_ids": ["ATOM-A"],
            "homework": ["完成迁移练习。"],
        },
    }


def test_real_presentation_core_interface_is_locked() -> None:
    expected = {
        "validate_presentation_input": ["value", "asset_root"],
        "compose_deck_json": ["value", "asset_root"],
        "validate_deck_json": ["value"],
        "render_presentation_bundle": [
            "deck",
            "asset_root",
            "output_dir",
            "toolchain",
            "filename",
        ],
        "finalize_slide_visual_review": ["qa_path", "slide_reviews"],
        "write_output_manifest": ["output_dir"],
    }
    for name, parameters in expected.items():
        value = getattr(core, name)
        assert callable(value)
        assert list(inspect.signature(value).parameters) == parameters
    toolchain_parameters = list(inspect.signature(core.PresentationToolchain).parameters)
    assert toolchain_parameters == [
        "node",
        "node_modules",
        "bin_dir",
        "python",
        "skill_dir",
    ]
    assert issubclass(core.PresentationWorkbenchError, Exception)


def test_manager_uses_real_core_validation_and_composition_contract(tmp_path: Path) -> None:
    pipeline = FakePipeline()
    toolchain = FakeToolchain()
    manager = PresentationJobManager(
        tmp_path / "state",
        toolchain,
        renderer=pipeline.render,
        manifest_writer=pipeline.write_manifest,
    )
    project = manager.create_project(OWNER, SESSION, {"title_zh": "合成课时"})
    stored_asset = manager.store_asset(
        OWNER,
        SESSION,
        project["project_id"],
        filename="question.png",
        data=ONE_PIXEL_PNG,
    )
    presentation_input = _real_core_input()
    presentation_input["theme"]["assets"] = [
        {
            "asset_id": stored_asset["asset_id"],
            "role": "question_crop",
            "path": stored_asset["path"],
            "sha256": stored_asset["sha256"],
            "source_page": 1,
            "source_bbox": [0, 0, 1, 1],
            "pixel_dimensions": stored_asset["pixel_dimensions"],
            "alt_text": "合成题面裁片",
            "source_ref": "synthetic fixture",
            "rights_boundary": "合成测试素材，仅限本机候选。",
            "publication_allowed": False,
        }
    ]
    presentation_input["theme"]["shared_materials"][0]["asset_ids"] = [
        stored_asset["asset_id"]
    ]
    presentation_input["theme"]["printed_questions"][0]["asset_ids"] = [
        stored_asset["asset_id"]
    ]
    outline = manager.create_outline(
        OWNER,
        SESSION,
        project["project_id"],
        presentation_input,
    )
    assert outline["deck_json"]["schema_version"] == core.DECK_SCHEMA_VERSION
    assert len(outline["deck_json"]["slides"]) >= 12
    assert outline["deck_json"]["candidate_boundary"]["publication_allowed"] is False
    assert any(slide["assets"] for slide in outline["deck_json"]["slides"])
    version = manager.create_version(
        OWNER,
        SESSION,
        project["project_id"],
        expected_outline_revision=outline["revision"],
    )
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    completed = _wait_job(manager, started["job_id"])
    assert completed["status"] == "completed"
    assert pipeline.render_calls == 1
    manager.shutdown()


def test_project_outline_versions_jobs_and_artifacts_persist(tmp_path: Path) -> None:
    state = tmp_path / "state"
    manager, _, toolchain = _manager(state)
    assert toolchain.validate_calls == 1
    project, outline, version = _create_version(manager)
    same = manager.create_version(
        OWNER,
        SESSION,
        project["project_id"],
        expected_outline_revision=outline["revision"],
    )
    assert same["version_id"] == version["version_id"]
    assert same["idempotent_replay"] is True

    updated_deck = deepcopy(outline["deck_json"])
    updated_deck["slides"][0]["title"] = "勒夏特列原理"
    updated = manager.update_outline(
        OWNER,
        SESSION,
        project["project_id"],
        updated_deck,
        expected_revision=outline["revision"],
    )
    assert updated["revision"] != outline["revision"]
    newer = manager.create_version(
        OWNER,
        SESSION,
        project["project_id"],
        expected_outline_revision=updated["revision"],
    )
    assert newer["version_id"] != version["version_id"]
    assert manager.get_version(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )["deck_json"]["slides"][0]["title"] == "化学平衡"

    started = manager.start_render(
        OWNER, SESSION, project["project_id"], newer["version_id"]
    )
    completed = _wait_job(manager, started["job_id"])
    assert completed["status"] == "completed"
    assert completed["candidate_only"] is True
    assert completed["candidate_status"] == "candidate_only"
    assert completed["teacher_review_status"] == "pending_teacher_review"
    assert completed["publication_allowed"] is False
    assert completed["qa_status"] == "pending_full_page_visual_review"
    assert {item["artifact_id"] for item in completed["artifacts"]} == set(
        ARTIFACT_FILENAMES
    )
    for row in completed["artifacts"]:
        data, content_type, filename = manager.artifact_bytes(
            OWNER, SESSION, started["job_id"], row["artifact_id"]
        )
        assert filename == ARTIFACT_FILENAMES[row["artifact_id"]]
        assert content_type == row["content_type"]
        assert len(data) == row["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
    manager.shutdown()

    reopened, reopened_pipeline, reopened_toolchain = _manager(state)
    assert reopened_toolchain.validate_calls == 1
    assert reopened.get_project(OWNER, SESSION, project["project_id"])["title_zh"] == "化学平衡"
    assert reopened.get_outline(OWNER, SESSION, project["project_id"])["revision"] == updated["revision"]
    assert len(reopened.list_versions(OWNER, SESSION, project["project_id"])) == 2
    assert reopened.get_job(OWNER, SESSION, started["job_id"])["status"] == "completed"
    replay = reopened.start_render(
        OWNER, SESSION, project["project_id"], newer["version_id"]
    )
    assert replay["job_id"] == started["job_id"]
    assert replay["idempotent_replay"] is True
    assert reopened_pipeline.render_calls == 0
    reopened.shutdown()


def test_outline_update_uses_real_cas_under_concurrency(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path / "state", max_workers=2)
    project, outline, _ = _create_version(manager)
    barrier = threading.Barrier(3)
    successes: list[dict[str, Any]] = []
    failures: list[str] = []

    def update(title: str) -> None:
        deck = deepcopy(outline["deck_json"])
        deck["slides"][0]["title"] = title
        barrier.wait()
        try:
            successes.append(
                manager.update_outline(
                    OWNER,
                    SESSION,
                    project["project_id"],
                    deck,
                    expected_revision=outline["revision"],
                )
            )
        except PresentationWorkbenchError as exc:
            failures.append(exc.code)

    threads = [threading.Thread(target=update, args=(title,)) for title in ("A", "B")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(10)
    assert len(successes) == 1
    assert failures == ["presentation_outline_revision_conflict"]
    assert manager.get_outline(OWNER, SESSION, project["project_id"])["revision"] == successes[0]["revision"]
    manager.shutdown()


def test_outline_cas_is_shared_across_two_manager_instances(tmp_path: Path) -> None:
    state = tmp_path / "state"
    manager_a, _, _ = _manager(state)
    project, outline, _ = _create_version(manager_a)
    manager_b, _, _ = _manager(state)
    barrier = threading.Barrier(3)
    successes: list[dict[str, Any]] = []
    failures: list[str] = []

    def update(manager: PresentationJobManager, title: str) -> None:
        deck = deepcopy(outline["deck_json"])
        deck["slides"][0]["title"] = title
        barrier.wait()
        try:
            successes.append(
                manager.update_outline(
                    OWNER,
                    SESSION,
                    project["project_id"],
                    deck,
                    expected_revision=outline["revision"],
                )
            )
        except PresentationWorkbenchError as exc:
            failures.append(exc.code)

    threads = [
        threading.Thread(target=update, args=(manager_a, "A")),
        threading.Thread(target=update, args=(manager_b, "B")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()

    assert len(successes) == 1
    assert failures == ["presentation_outline_revision_conflict"]
    persisted = manager_b.get_outline(OWNER, SESSION, project["project_id"])
    assert persisted["revision"] == successes[0]["revision"]
    manager_a.shutdown()
    manager_b.shutdown()


def test_failed_render_requires_explicit_retry_and_reuses_job_id(tmp_path: Path) -> None:
    pipeline = FakePipeline(fail_attempts=1)
    manager, pipeline, _ = _manager(tmp_path / "state", pipeline)
    project, _, version = _create_version(manager)
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    failed = _wait_job(manager, started["job_id"])
    assert failed["status"] == "failed"
    assert failed["retryable"] is True
    assert failed["retry_required"] is True
    assert failed["error"]["code"] == "fixture_render_failed"
    replay = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    assert replay["status"] == "failed"
    assert replay["idempotent_replay"] is True
    assert pipeline.render_calls == 1

    retried = manager.retry_job(OWNER, SESSION, started["job_id"])
    assert retried["job_id"] == started["job_id"]
    assert retried["attempt"] == 2
    completed = _wait_job(manager, started["job_id"])
    assert completed["status"] == "completed"
    assert completed["attempt"] == 2
    assert pipeline.render_calls == 2
    with pytest.raises(PresentationWorkbenchError) as captured:
        manager.retry_job(OWNER, SESSION, started["job_id"])
    assert captured.value.code == "presentation_job_not_retryable"
    manager.shutdown()


def test_machine_qa_failure_never_becomes_downloadable_candidate(tmp_path: Path) -> None:
    pipeline = FakePipeline(machine_checks_passed=False)
    manager, _, _ = _manager(tmp_path / "state", pipeline)
    project, _, version = _create_version(manager)
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    failed = _wait_job(manager, started["job_id"])
    assert failed["status"] == "failed"
    assert failed["retryable"] is True
    assert failed["artifacts"] == []
    assert failed["error"]["code"] == "presentation_machine_qa_failed"
    with pytest.raises(PresentationWorkbenchError) as captured:
        manager.artifact_bytes(OWNER, SESSION, started["job_id"], "pptx")
    assert captured.value.code == "presentation_artifact_not_ready"
    manager.shutdown()


def test_cancel_during_renderer_discards_candidate_artifacts(tmp_path: Path) -> None:
    started_event = threading.Event()
    release_event = threading.Event()
    pipeline = FakePipeline(started=started_event, release=release_event)
    manager, _, _ = _manager(tmp_path / "state", pipeline)
    project, _, version = _create_version(manager)
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    assert started_event.wait(10)
    requested = manager.cancel_job(OWNER, SESSION, started["job_id"])
    assert requested["status"] == "cancel_requested"
    release_event.set()
    cancelled = _wait_job(manager, started["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["artifacts"] == []
    assert cancelled["retryable"] is True
    with pytest.raises(PresentationWorkbenchError) as captured:
        manager.artifact_bytes(OWNER, SESSION, started["job_id"], "pptx")
    assert captured.value.code == "presentation_artifact_not_ready"
    manager.shutdown()


def test_owner_and_session_are_non_enumerating_isolation_boundaries(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path / "state")
    project, _, version = _create_version(manager)
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    _wait_job(manager, started["job_id"])
    for owner, session in (("other@example.invalid", SESSION), (OWNER, "b" * 64)):
        with pytest.raises(PresentationWorkbenchError) as project_error:
            manager.get_project(owner, session, project["project_id"])
        assert project_error.value.code == "presentation_project_not_found"
        with pytest.raises(PresentationWorkbenchError) as job_error:
            manager.get_job(owner, session, started["job_id"])
        assert job_error.value.code == "presentation_job_not_found"
        with pytest.raises(PresentationWorkbenchError) as artifact_error:
            manager.artifact_bytes(owner, session, started["job_id"], "pptx")
        assert artifact_error.value.code == "presentation_job_not_found"
    manager.shutdown()


def test_content_addressed_assets_are_atomic_idempotent_and_outline_frozen(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    manager, _, _ = _manager(state)
    project = manager.create_project(OWNER, SESSION, {"title_zh": "素材测试"})
    asset = manager.store_asset(
        OWNER,
        SESSION,
        project["project_id"],
        filename="question.png",
        data=ONE_PIXEL_PNG,
    )
    assert asset["path"] == f"{hashlib.sha256(ONE_PIXEL_PNG).hexdigest()}.png"
    assert asset["sha256"] == hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
    assert asset["size_bytes"] == len(ONE_PIXEL_PNG)
    assert asset["pixel_dimensions"] == [1, 1]
    assert asset["idempotent_replay"] is False
    replay = manager.store_asset(
        OWNER,
        SESSION,
        project["project_id"],
        filename="same-content.png",
        data=ONE_PIXEL_PNG,
    )
    assert replay["path"] == asset["path"]
    assert replay["idempotent_replay"] is True
    assert len(list(state.glob("tenants/*/projects/PPTPRJ-*/assets/*"))) == 1

    for filename in ("../escape.png", "subdir/question.png", "bad?.png"):
        with pytest.raises(PresentationWorkbenchError) as invalid_name:
            manager.store_asset(
                OWNER,
                SESSION,
                project["project_id"],
                filename=filename,
                data=ONE_PIXEL_PNG,
            )
        assert invalid_name.value.code == "presentation_asset_filename_invalid"
    with pytest.raises(PresentationWorkbenchError) as mismatch:
        manager.store_asset(
            OWNER,
            SESSION,
            project["project_id"],
            filename="question.jpg",
            data=ONE_PIXEL_PNG,
        )
    assert mismatch.value.code == "presentation_asset_extension_mismatch"
    with pytest.raises(PresentationWorkbenchError) as isolated:
        manager.store_asset(
            "other@example.invalid",
            SESSION,
            project["project_id"],
            filename="question.png",
            data=ONE_PIXEL_PNG,
        )
    assert isolated.value.code == "presentation_project_not_found"

    manager.create_outline(
        OWNER,
        SESSION,
        project["project_id"],
        {"schema_version": "fixture.input.v1", "lesson_title": "素材测试"},
    )
    frozen_replay = manager.store_asset(
        OWNER,
        SESSION,
        project["project_id"],
        filename="question-again.png",
        data=ONE_PIXEL_PNG,
    )
    assert frozen_replay["idempotent_replay"] is True
    with pytest.raises(PresentationWorkbenchError) as frozen:
        manager.store_asset(
            OWNER,
            SESSION,
            project["project_id"],
            filename="new.png",
            data=ONE_PIXEL_PNG[:-12]
            + b"distinct-content-address"
            + ONE_PIXEL_PNG[-12:],
        )
    assert frozen.value.code == "presentation_assets_frozen"
    manager.shutdown()


def test_artifact_hash_whitelist_and_path_attacks_fail_closed(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path / "state")
    project = manager.create_project(OWNER, SESSION, {"title_zh": "路径测试"})
    with pytest.raises(PresentationWorkbenchError) as output_path_error:
        manager.create_outline(
            OWNER,
            SESSION,
            project["project_id"],
            {
                "schema_version": "fixture.input.v1",
                "lesson_title": "路径测试",
                "output_dir": "../../outside",
            },
        )
    assert output_path_error.value.code == "presentation_client_output_path_forbidden"
    with pytest.raises(PresentationWorkbenchError) as traversal:
        manager.get_project(OWNER, SESSION, "../../another-project")
    assert traversal.value.code == "presentation_project_not_found"

    outline = manager.create_outline(
        OWNER,
        SESSION,
        project["project_id"],
        {"schema_version": "fixture.input.v1", "lesson_title": "路径测试"},
    )
    version = manager.create_version(
        OWNER,
        SESSION,
        project["project_id"],
        expected_outline_revision=outline["revision"],
    )
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    _wait_job(manager, started["job_id"])
    with pytest.raises(PresentationWorkbenchError) as invalid_artifact:
        manager.artifact_bytes(OWNER, SESSION, started["job_id"], "../pptx")
    assert invalid_artifact.value.code == "presentation_artifact_invalid"

    pptx_path, _ = manager.artifact_path(OWNER, SESSION, started["job_id"], "pptx")
    pptx_path.write_bytes(pptx_path.read_bytes() + b"tamper")
    with pytest.raises(PresentationWorkbenchError) as drift:
        manager.artifact_bytes(OWNER, SESSION, started["job_id"], "pptx")
    assert drift.value.code == "presentation_artifact_drift"
    invalidated = manager.get_job(OWNER, SESSION, started["job_id"])
    assert invalidated["status"] == "failed"
    assert invalidated["retryable"] is True
    assert invalidated["error"]["code"] == "presentation_artifact_drift"
    manager.retry_job(OWNER, SESSION, started["job_id"])
    assert _wait_job(manager, started["job_id"])["status"] == "completed"
    manager.shutdown()


def test_restart_recovers_running_job_as_failed_and_retryable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    manager, _, _ = _manager(state)
    project, _, version = _create_version(manager)
    started = manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    _wait_job(manager, started["job_id"])
    manager.shutdown()

    job_paths = list(state.glob("tenants/*/jobs/PPTJOB-*/job.json"))
    assert len(job_paths) == 1
    path = job_paths[0]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "running"
    value["retryable"] = False
    value["completed_at"] = None
    value["progress"] = {
        "stage": "rendering",
        "percent": 50,
        "message_zh": "simulated crash",
    }
    value["record_sha256"] = _record_sha256(value)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    recovered, _, _ = _manager(state)
    job = recovered.get_job(OWNER, SESSION, started["job_id"])
    assert job["status"] == "failed"
    assert job["retryable"] is True
    assert job["retry_required"] is True
    assert job["error"]["code"] == "presentation_render_interrupted"
    retried = recovered.retry_job(OWNER, SESSION, started["job_id"])
    assert retried["attempt"] == 2
    assert _wait_job(recovered, started["job_id"])["status"] == "completed"
    recovered.shutdown()


def test_live_stale_worker_cannot_commit_after_recovery_and_retry(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    old_started = threading.Event()
    release_old = threading.Event()
    old_pipeline = FakePipeline(started=old_started, release=release_old)
    old_manager, _, _ = _manager(state, old_pipeline)
    project, _, version = _create_version(old_manager)
    started = old_manager.start_render(
        OWNER, SESSION, project["project_id"], version["version_id"]
    )
    assert old_started.wait(10)

    new_pipeline = FakePipeline(fail_attempts=1)
    new_manager, _, _ = _manager(state, new_pipeline)
    recovered = new_manager.get_job(OWNER, SESSION, started["job_id"])
    assert recovered["status"] == "failed"
    assert recovered["attempt"] == 1
    assert recovered["error"]["code"] == "presentation_render_interrupted"

    retried = new_manager.retry_job(OWNER, SESSION, started["job_id"])
    assert retried["attempt"] == 2
    failed_attempt_two = _wait_job(new_manager, started["job_id"])
    assert failed_attempt_two["status"] == "failed"
    assert failed_attempt_two["attempt"] == 2
    assert failed_attempt_two["error"]["code"] == "fixture_render_failed"

    release_old.set()
    old_manager.shutdown(wait=True)
    after_old_worker = new_manager.get_job(OWNER, SESSION, started["job_id"])
    assert after_old_worker["status"] == "failed"
    assert after_old_worker["attempt"] == 2
    assert after_old_worker["artifacts"] == []
    assert after_old_worker["error"]["code"] == "fixture_render_failed"

    new_pipeline.fail_attempts = 0
    third = new_manager.retry_job(OWNER, SESSION, started["job_id"])
    assert third["attempt"] == 3
    completed = _wait_job(new_manager, started["job_id"])
    assert completed["status"] == "completed"
    assert completed["attempt"] == 3
    data, _, _ = new_manager.artifact_bytes(
        OWNER, SESSION, started["job_id"], "pptx"
    )
    assert data.startswith(b"PK")
    new_manager.shutdown()
