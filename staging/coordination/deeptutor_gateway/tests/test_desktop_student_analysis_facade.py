from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
    StudentAnalysisConfirmation,
    StudentAnalysisReview,
    StudentProfileSummary,
    StudentSubmissionSummary,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

STUDENT_ID = "11111111-1111-1111-1111-111111111111"
SUBMISSION_ID = "SUB-" + "2" * 32
PROFILE_ID = "vision-profile"
PROFILE_REVISION = "rev_" + "3" * 32
SENTINEL_SECRET = "SENTINEL-SECRET-MUST-NEVER-ESCAPE"
CREATED_AT = "2026-09-05T12:00:00Z"


class _UnusedThemeReader:
    pass


class _UnusedSupplementalReader:
    pass


class _UnusedSearchReader:
    pass


class _CurriculumReader:
    def catalog(self) -> dict[str, Any]:
        return {
            "counts": {
                "volumes": 1,
                "chapters": 1,
                "sections": 2,
                "active_atomic_mappings": 0,
            },
            "volumes": [
                {
                    "volume_id": "TB-M1",
                    "volume_title": "必修第一册",
                    "display_label_zh": "必修第一册",
                    "chapters": [
                        {
                            "chapter_id": "TB-M1-C1",
                            "chapter_title": "第1章",
                            "display_label_zh": "第1章",
                            "sections": [
                                {
                                    "section_key": "TB-M1-C1:1.1",
                                    "section_number": "1.1",
                                    "section_title": "物质的分类",
                                    "display_label_zh": "1.1 物质的分类",
                                },
                                {
                                    "section_key": "TB-M1-C1:1.2",
                                    "section_number": "1.2",
                                    "section_title": "物质的转化",
                                    "display_label_zh": "1.2 物质的转化",
                                },
                            ],
                        }
                    ],
                }
            ],
        }


class _ProviderStore:
    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "profile_id": PROFILE_ID,
                "display_name": "教师视觉模型",
                "base_url": "https://provider.invalid/v1",
                "model_id": "vision-model",
                "api_style": "responses",
                "capabilities": ["text", "vision", "structured_output"],
                "effective_capabilities": ["text", "vision", "structured_output"],
                "capability_evidence": {
                    "catalog": [],
                    "declared": ["text", "vision", "structured_output"],
                    "probed": [],
                    "unknown": [],
                },
                "allowed_data_classes": [
                    "synthetic_only",
                    "source_page_image",
                    "student_answer_image",
                ],
                "image_egress": "teacher_confirmed_visual_pages",
                "credential_state": "configured",
                "revision": PROFILE_REVISION,
            }
        ]


class _FakeStudentAnalysisManager:
    """Small stateful double for testing only the desktop translation boundary."""

    def __init__(self) -> None:
        self.profile: dict[str, Any] | None = None
        self.submission: dict[str, Any] | None = None
        self.revision_counter = 10
        self.page_bodies: dict[str, tuple[bytes, str]] = {}
        self.register_calls: list[dict[str, Any]] = []
        self.upload_calls: list[tuple[str, str, bytes, str]] = []
        self.matching_calls: list[dict[str, Any]] = []
        self.privacy_calls: list[dict[str, Any]] = []
        self.analysis_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.score_calls: list[dict[str, Any]] = []
        self.diagnosis_calls: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.scoring_decisions: list[dict[str, Any]] = []
        self.diagnostic_decisions: list[dict[str, Any]] = []
        self.privacy_result_revision: str | None = None
        self.shutdown_calls = 0

    def _next_revision(self) -> str:
        self.revision_counter += 1
        return f"rev_{self.revision_counter:032x}"

    def _current(self) -> dict[str, Any]:
        assert self.submission is not None
        return deepcopy(self.submission)

    def _bump(self) -> None:
        assert self.submission is not None
        self.submission["revision"] = self._next_revision()
        self.submission["updated_at"] = f"2026-09-05T12:00:{self.revision_counter:02d}Z"

    def create_student(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.events.append("create_student")
        self.profile = {
            "student_id": STUDENT_ID,
            "alias": "匿名学生-111111",
            "grade": payload["grade"],
            "retention_days": payload["retention_days"],
            "created_at": CREATED_AT,
            "anonymous": True,
            "contains_direct_identifiers": False,
        }
        return deepcopy(self.profile)

    def list_students(self) -> list[dict[str, Any]]:
        return [] if self.profile is None else [deepcopy(self.profile)]

    def create_submission(
        self, student_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        assert student_id == STUDENT_ID
        assert payload == {"workflow": "quick_single_work", "assignment_id": None}
        self.events.append("create_submission")
        revision = self._next_revision()
        self.submission = {
            "schema_version": "shchem.student-visual-submission.v1",
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": revision,
            "workflow": "quick_single_work",
            "assignment_id": None,
            "status": "awaiting_upload",
            "created_at": CREATED_AT,
            "updated_at": CREATED_AT,
            "files": [],
            "matching": {
                "status": "awaiting_pages",
                "teacher_confirmed": False,
                "matches": [],
            },
            "privacy_decision": None,
            "analysis_runs": [],
            "active_run_id": None,
            "analysis": None,
            "review": {
                "required": True,
                "status": "not_ready",
                "scoring_decision_count": 0,
            },
            "events": [],
            "long_term_materialization": {
                "allowed": False,
                "attempt_written": False,
                "mastery_written": False,
                "recommendation_written": False,
                "reason": "single_submission_teacher_review_candidate_only",
            },
            "private_state": {"secret": SENTINEL_SECRET},
        }
        return self._current()

    def list_submissions(self, student_id: str) -> list[dict[str, Any]]:
        assert student_id == STUDENT_ID
        return [] if self.submission is None else [self._current()]

    def get_submission(self, student_id: str, submission_id: str) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        return self._current()

    def register_file(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.events.append("register_file")
        self.register_calls.append(deepcopy(payload))
        file_id = f"SVF-{len(self.register_calls):032x}"
        record = {
            "file_id": file_id,
            "role": payload["role"],
            "filename_sha256": hashlib.sha256(payload["filename"].encode()).hexdigest(),
            "mime_type": payload["mime_type"],
            "expected_size_bytes": payload["expected_size_bytes"],
            "expected_sha256": payload["expected_sha256"],
            "state": "awaiting_content",
            "size_bytes": None,
            "sha256": None,
            "private_relative_path": f"private/{SENTINEL_SECRET}/{file_id}",
            "pages": [],
            "created_at": CREATED_AT,
        }
        self.submission["files"].append(record)
        self._bump()
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "file": deepcopy(record),
        }

    def upload_file_content(
        self,
        student_id: str,
        submission_id: str,
        file_id: str,
        data: bytes,
        *,
        content_type: str,
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        record = next(
            item for item in self.submission["files"] if item["file_id"] == file_id
        )
        digest = hashlib.sha256(data).hexdigest()
        record.update(
            {
                "state": "stored",
                "size_bytes": len(data),
                "sha256": digest,
                "pages": [
                    {
                        "page": 1,
                        "role": record["role"],
                        "mime_type": "image/png",
                        "sha256": digest,
                        "size_bytes": len(data),
                        "width": 640,
                        "height": 480,
                        "private_relative_path": f"pages/{SENTINEL_SECRET}/{digest}.png",
                    }
                ],
            }
        )
        self.page_bodies[digest] = (data, "image/png")
        self.upload_calls.append((file_id, record["role"], data, content_type))
        self.events.append("upload_file")
        roles = {
            item["role"]
            for item in self.submission["files"]
            if item["state"] == "stored"
        }
        if {"question_pages", "student_work_pages"}.issubset(roles):
            question_page = next(
                item["pages"][0]
                for item in self.submission["files"]
                if item["role"] == "question_pages" and item["state"] == "stored"
            )
            student_page = next(
                item["pages"][0]
                for item in self.submission["files"]
                if item["role"] == "student_work_pages" and item["state"] == "stored"
            )
            reference = next(
                (
                    item["pages"][0]
                    for item in self.submission["files"]
                    if item["role"] == "reference_answer_pages"
                    and item["state"] == "stored"
                ),
                None,
            )
            self.submission["matching"] = {
                "status": "provisional_teacher_editable",
                "teacher_confirmed": False,
                "matches": [
                    {
                        "match_id": "MATCH-opaque-1",
                        "atomic_part_id": "ATOMIC-opaque-1",
                        "printed_question_id": "PRINTED-opaque-1",
                        "question_number_hint": "1",
                        "question_page_sha256": question_page["sha256"],
                        "student_work_page_sha256": student_page["sha256"],
                        "reference_answer_page_sha256": (
                            reference["sha256"] if reference is not None else None
                        ),
                        "maximum_score": 1.0,
                        "allowed_scoring_point_ids": ["SCORE-opaque-1"],
                        "provisional": True,
                    }
                ],
            }
            self.submission["status"] = "awaiting_privacy_review"
        self._bump()
        return self._current()

    def get_matching(self, student_id: str, submission_id: str) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "matching": deepcopy(self.submission["matching"]),
        }

    def update_matching(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.matching_calls.append(deepcopy(payload))
        self.events.append("update_matching")
        self.submission["matching"] = {
            "status": "teacher_confirmed",
            "teacher_confirmed": True,
            "matches": deepcopy(payload["matches"]),
        }
        self.submission["status"] = "awaiting_privacy_review"
        self._bump()
        return self.get_matching(student_id, submission_id)

    def record_privacy_decision(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.privacy_calls.append(deepcopy(payload))
        self.events.append("record_privacy_decision")
        self.submission["privacy_decision"] = deepcopy(payload)
        self.submission["status"] = "ready_for_analysis"
        self._bump()
        self.privacy_result_revision = self.submission["revision"]
        return self._current()

    def analyze(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.analysis_calls.append(deepcopy(payload))
        self.events.append("analyze")
        self.submission["status"] = "queued_for_analysis"
        self.submission["active_run_id"] = "SVRUN-opaque"
        self.submission["analysis_runs"] = [
            {
                "request": {"body_sha256": "f" * 64, "secret": SENTINEL_SECRET},
                "response": {"usage": {"total_tokens": 99}},
            }
        ]
        self._bump()
        return self._current()

    def cancel(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.cancel_calls.append(deepcopy(payload))
        self.events.append("cancel")
        self.submission["status"] = "cancelled"
        self.submission["active_run_id"] = None
        self._bump()
        return self._current()

    def read_submission_page(
        self,
        student_id: str,
        submission_id: str,
        *,
        file_id: str,
        page_sha256: str,
    ) -> tuple[bytes, str]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert any(
            file_record["file_id"] == file_id
            and any(page["sha256"] == page_sha256 for page in file_record["pages"])
            for file_record in self._current()["files"]
        )
        return self.page_bodies[page_sha256]

    def set_blocked_candidate(self) -> None:
        assert self.submission is not None
        match = self.submission["matching"]["matches"][0]
        student_hash = match["student_work_page_sha256"]
        bbox = {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.2}
        self.submission["status"] = "awaiting_teacher_review"
        self.submission["analysis"] = {
            "analysis_id": "SVAN-opaque",
            "provider_profile_id": PROFILE_ID,
            "provider_revision": PROFILE_REVISION,
            "model_id": f"model-{SENTINEL_SECRET}",
            "candidate": {
                "schema_version": "shchem.student-visual-analysis-candidate.v1",
                "page_quality": [
                    {
                        "page_sha256": student_hash,
                        "quality": "blurred",
                        "issues": ["blurred"],
                        "confidence": 0.2,
                    }
                ],
                "matches": [
                    {
                        "match_id": match["match_id"],
                        "atomic_part_id": match["atomic_part_id"],
                        "printed_question_id": match["printed_question_id"],
                        "question_number": "1",
                        "question_anchor": {
                            "page_sha256": match["question_page_sha256"],
                            "bbox": bbox,
                        },
                        "student_answer_anchor": {
                            "page_sha256": student_hash,
                            "bbox": bbox,
                        },
                        "reference_answer_anchor": None,
                        "answer_region_anchor": {
                            "page_sha256": student_hash,
                            "bbox": bbox,
                        },
                        "visual_response_observation": "图像模糊，暂不能判定作答。",
                        "chemistry_observations": {
                            "formulas": ["H2"],
                            "charges": [],
                            "conditions": [],
                            "units": [],
                            "other_visible_details": [],
                        },
                        "scoring_points": [
                            {
                                "scoring_point_id": "SCORE-opaque-1",
                                "evidence": [
                                    {"page_sha256": student_hash, "bbox": bbox}
                                ],
                                "suggested_score": 0.0,
                                "maximum_score": 1.0,
                                "confidence": 0.2,
                                "blockers": ["page_blurred"],
                            }
                        ],
                        "suggested_score": 0.0,
                        "maximum_score": 1.0,
                        "confidence": 0.2,
                        "blockers": ["page_blurred"],
                        "error_hypotheses": [
                            {
                                "hypothesis": "页面模糊，不能形成可靠错误诊断。",
                                "supporting_evidence": [],
                                "counterevidence": ["页面模糊"],
                                "confidence": 0.1,
                            }
                        ],
                    }
                ],
                "blockers": ["one_or_more_pages_need_teacher_review"],
                "requires_teacher_review": True,
                "final_score": None,
                "long_term_update_allowed": False,
            },
            "requires_teacher_review": True,
            "final_score": None,
            "long_term_update_allowed": False,
        }
        self.submission["review"] = {
            "required": True,
            "status": "pending",
            "scoring_decision_count": len(self.scoring_decisions),
        }
        self._bump()

    def get_review(self, student_id: str, submission_id: str) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "status": self.submission["status"],
            "review": deepcopy(self.submission["review"]),
            "analysis": deepcopy(self.submission["analysis"]),
            "scoring_decisions": deepcopy(self.scoring_decisions),
            "append_only": True,
            "requires_teacher_review": True,
            "final_score": None,
            "long_term_update_allowed": False,
        }

    def get_diagnostic_review(
        self, student_id: str, submission_id: str
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        latest: dict[str, dict[str, Any]] = {}
        for decision in self.diagnostic_decisions:
            latest[decision["match_id"]] = decision
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "status": self.submission["status"],
            "diagnostic_decisions": deepcopy(self.diagnostic_decisions),
            "latest_diagnostic_decisions": list(deepcopy(latest).values()),
            "review": {
                "status": "teacher_decisions_recorded" if latest else "pending",
                "decision_count": len(self.diagnostic_decisions),
                "distinct_match_count": len(latest),
                "match_count": 1,
            },
            "long_term_update_allowed": False,
        }

    def append_scoring_decision(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.score_calls.append(deepcopy(payload))
        decision = {
            "decision_id": f"SVDEC-{len(self.scoring_decisions) + 1:032x}",
            "match_id": payload["match_id"],
            "teacher_score": float(payload["teacher_score"]),
            "reason": payload["reason"],
        }
        self.scoring_decisions.append(decision)
        self.submission["review"]["scoring_decision_count"] = len(
            self.scoring_decisions
        )
        self._bump()
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "decision": deepcopy(decision),
            "requires_teacher_review": True,
            "final_score": None,
            "long_term_update_allowed": False,
        }

    def append_diagnostic_decision(
        self,
        student_id: str,
        submission_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert (student_id, submission_id) == (STUDENT_ID, SUBMISSION_ID)
        assert self.submission is not None
        self.diagnosis_calls.append(deepcopy(payload))
        decision = {
            "decision_id": f"SVDIAG-{len(self.diagnostic_decisions) + 1:032x}",
            **deepcopy(payload),
        }
        self.diagnostic_decisions.append(decision)
        self._bump()
        return {
            "student_id": STUDENT_ID,
            "submission_id": SUBMISSION_ID,
            "revision": self.submission["revision"],
            "decision": deepcopy(decision),
            "review": {
                "status": "teacher_decisions_recorded",
                "decision_count": len(self.diagnostic_decisions),
                "distinct_match_count": 1,
                "match_count": 1,
            },
            "append_only": True,
            "long_term_update_allowed": False,
        }

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.fixture
def desktop_paths(tmp_path: Path) -> DesktopPaths:
    workspace = tmp_path / "workspace"
    (workspace / "sh-chem-db").mkdir(parents=True)
    (workspace / "integrations" / "deeptutor_shchem_v1").mkdir(parents=True)
    return DesktopPaths.from_workspace(
        workspace, state_root=tmp_path / "personal-state"
    )


def _build_facade(
    paths: DesktopPaths,
    manager: _FakeStudentAnalysisManager,
) -> DesktopWorkbenchFacade:
    return DesktopWorkbenchFacade(
        paths,
        theme_reader=_UnusedThemeReader(),
        supplemental_reader=_UnusedSupplementalReader(),
        curriculum_reader=_CurriculumReader(),
        search_reader=_UnusedSearchReader(),
        provider_store=_ProviderStore(),
        state_store=DesktopStateStore(paths.state_root),
        student_analysis_manager=manager,
    )


def _create_profile_and_submission(
    facade: DesktopWorkbenchFacade,
) -> tuple[StudentProfileSummary, StudentSubmissionSummary]:
    profile = facade.create_student_profile(
        grade="高三", retention_days=30, consent_recorded=True
    )
    submission = facade.create_student_submission(profile.student_id)
    return profile, submission


def _write_source(path: Path, marker: bytes) -> Path:
    path.write_bytes(b"fixture-" + marker)
    return path


def _add_three_roles(
    facade: DesktopWorkbenchFacade,
    submission: StudentSubmissionSummary,
    tmp_path: Path,
    *,
    two_question_files: bool = False,
) -> StudentSubmissionSummary:
    question_files = [_write_source(tmp_path / "question-1.png", b"question-1")]
    if two_question_files:
        question_files.append(_write_source(tmp_path / "question-2.PNG", b"question-2"))
    current = facade.add_student_submission_files(
        student_id=submission.student_id,
        submission_id=submission.submission_id,
        role="question_pages",
        files=question_files,
        expected_revision=submission.revision,
    )
    current = facade.add_student_submission_files(
        student_id=current.student_id,
        submission_id=current.submission_id,
        role="reference_answer_pages",
        files=[_write_source(tmp_path / "reference.jpeg", b"reference")],
        expected_revision=current.revision,
    )
    return facade.add_student_submission_files(
        student_id=current.student_id,
        submission_id=current.submission_id,
        role="student_work_pages",
        files=[_write_source(tmp_path / "student.webp", b"student")],
        expected_revision=current.revision,
    )


def _confirm_matching(
    facade: DesktopWorkbenchFacade, submission: StudentSubmissionSummary
) -> StudentSubmissionSummary:
    match = submission.matches[0]
    return facade.confirm_student_matching(
        student_id=submission.student_id,
        submission_id=submission.submission_id,
        expected_revision=submission.revision,
        edits=[
            {
                "match_id": match.match_id,
                "question_number_hint": "1（教师核对）",
                "maximum_score": 5.0,
                "question_page_sha256": match.question_page_sha256,
                "student_work_page_sha256": match.student_work_page_sha256,
                "reference_answer_page_sha256": match.reference_answer_page_sha256,
            }
        ],
    )


def _assert_no_raw_private_projection(value: Any) -> None:
    assert is_dataclass(value)
    payload = asdict(value)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert SENTINEL_SECRET not in serialized
    banned_keys = {
        "private_relative_path",
        "private_state",
        "analysis_runs",
        "events",
        "request",
        "response",
        "usage",
        "candidate",
        "model_id",
    }

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            assert banned_keys.isdisjoint(item)
            for nested in item.values():
                walk(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                walk(nested)

    walk(payload)


def test_three_roles_preserve_file_order_and_never_start_provider_implicitly(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    profile, initial = _create_profile_and_submission(facade)

    summary = _add_three_roles(facade, initial, tmp_path, two_question_files=True)

    assert facade.student_profiles() == (profile,)
    assert [call["role"] for call in manager.register_calls] == [
        "question_pages",
        "question_pages",
        "reference_answer_pages",
        "student_work_pages",
    ]
    assert [call["filename"] for call in manager.register_calls] == [
        "question-1.png",
        "question-2.PNG",
        "reference.jpeg",
        "student.webp",
    ]
    assert [role for _file_id, role, _data, _mime in manager.upload_calls] == [
        "question_pages",
        "question_pages",
        "reference_answer_pages",
        "student_work_pages",
    ]
    assert manager.privacy_calls == []
    assert manager.analysis_calls == []
    assert summary.page_counts_by_role == {
        "question_pages": 2,
        "reference_answer_pages": 1,
        "student_work_pages": 1,
    }
    assert [(page.role, page.ordinal) for page in summary.pages] == [
        ("question_pages", 1),
        ("question_pages", 2),
        ("reference_answer_pages", 1),
        ("student_work_pages", 1),
    ]
    page = summary.pages[-1]
    raw, mime_type = facade.student_submission_page(
        student_id=summary.student_id,
        submission_id=summary.submission_id,
        file_id=page.file_id,
        page_sha256=page.sha256,
    )
    assert raw == b"fixture-student"
    assert mime_type == "image/png"


def test_file_addition_rejects_unknown_role_and_suffix_before_manager_mutation(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    _profile, submission = _create_profile_and_submission(facade)
    png = _write_source(tmp_path / "valid.png", b"valid")
    text = _write_source(tmp_path / "forbidden.txt", b"forbidden")

    with pytest.raises(DesktopFacadeError):
        facade.add_student_submission_files(
            student_id=submission.student_id,
            submission_id=submission.submission_id,
            role="student_answer",
            files=[png],
            expected_revision=submission.revision,
        )
    with pytest.raises(DesktopFacadeError):
        facade.add_student_submission_files(
            student_id=submission.student_id,
            submission_id=submission.submission_id,
            role="question_pages",
            files=[text],
            expected_revision=submission.revision,
        )

    assert manager.register_calls == []
    assert manager.upload_calls == []


def test_matching_edits_preserve_domain_owned_ids_and_scoring_allowlist(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    _profile, initial = _create_profile_and_submission(facade)
    uploaded = _add_three_roles(facade, initial, tmp_path)

    confirmed = _confirm_matching(facade, uploaded)

    assert len(manager.matching_calls) == 1
    call = manager.matching_calls[0]
    assert call["expected_revision"] == uploaded.revision
    assert call["matches"] == [
        {
            "match_id": "MATCH-opaque-1",
            "atomic_part_id": "ATOMIC-opaque-1",
            "printed_question_id": "PRINTED-opaque-1",
            "question_number_hint": "1（教师核对）",
            "question_page_sha256": uploaded.matches[0].question_page_sha256,
            "student_work_page_sha256": uploaded.matches[0].student_work_page_sha256,
            "reference_answer_page_sha256": (
                uploaded.matches[0].reference_answer_page_sha256
            ),
            "maximum_score": 5.0,
            "allowed_scoring_point_ids": ["SCORE-opaque-1"],
            "provisional": True,
        }
    ]
    assert confirmed.matches[0].maximum_score == 5.0
    assert confirmed.matches[0].question_number_hint == "1（教师核对）"
    assert "MATCH-opaque-1" not in repr(confirmed.matches[0])


@pytest.mark.parametrize(
    ("identifiers_clear", "student_page_egress_confirmed"),
    [(False, True), (True, False), (False, False)],
)
def test_start_requires_both_explicit_confirmations_before_privacy_or_analysis(
    desktop_paths: DesktopPaths,
    tmp_path: Path,
    identifiers_clear: bool,
    student_page_egress_confirmed: bool,
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    _profile, initial = _create_profile_and_submission(facade)
    confirmed = _confirm_matching(facade, _add_three_roles(facade, initial, tmp_path))
    confirmation = facade.prepare_student_analysis_confirmation(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
        profile_id=PROFILE_ID,
    )

    with pytest.raises(DesktopFacadeError):
        facade.start_student_analysis(
            student_id=confirmed.student_id,
            confirmation=confirmation,
            identifiers_clear=identifiers_clear,
            student_page_egress_confirmed=student_page_egress_confirmed,
        )

    assert manager.privacy_calls == []
    assert manager.analysis_calls == []


def test_confirmation_binds_all_pages_and_start_records_privacy_before_analysis(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    profile, initial = _create_profile_and_submission(facade)
    confirmed = _confirm_matching(
        facade,
        _add_three_roles(facade, initial, tmp_path, two_question_files=True),
    )

    confirmation = facade.prepare_student_analysis_confirmation(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
        profile_id=PROFILE_ID,
    )

    assert isinstance(confirmation, StudentAnalysisConfirmation)
    assert confirmation.student_id == confirmed.student_id
    assert confirmation.submission_id == confirmed.submission_id
    assert confirmation.expected_revision == confirmed.revision
    assert confirmation.provider_profile_id == PROFILE_ID
    assert confirmation.provider_revision == PROFILE_REVISION
    assert confirmation.page_sha256 == tuple(page.sha256 for page in confirmed.pages)
    assert confirmation.pages == confirmed.pages
    assert confirmation.total_page_count == 4
    assert confirmation.page_counts_by_role == confirmed.page_counts_by_role
    assert confirmation.student_label_zh == profile.label_zh
    assert confirmation.retention_days == 30
    for hidden in (
        STUDENT_ID,
        SUBMISSION_ID,
        confirmed.revision,
        PROFILE_ID,
        PROFILE_REVISION,
        *confirmation.page_sha256,
    ):
        assert hidden not in repr(confirmation)
        assert hidden not in confirmation.message_zh

    queued = facade.start_student_analysis(
        student_id=confirmed.student_id,
        confirmation=confirmation,
        identifiers_clear=True,
        student_page_egress_confirmed=True,
    )

    assert manager.events[-2:] == ["record_privacy_decision", "analyze"]
    assert manager.privacy_calls == [
        {
            "expected_revision": confirmed.revision,
            "decision": "approved",
            "contains_direct_identifiers": False,
            "confirmed_page_sha256": list(confirmation.page_sha256),
            "provider_profile_id": PROFILE_ID,
            "provider_revision": PROFILE_REVISION,
            "teacher_confirmed_student_page_egress": True,
        }
    ]
    assert manager.analysis_calls == [
        {
            "expected_revision": manager.privacy_result_revision,
            "provider_profile_id": PROFILE_ID,
            "provider_revision": PROFILE_REVISION,
        }
    ]
    assert manager.analysis_calls[0]["expected_revision"] != confirmed.revision
    assert queued.status == "queued_for_analysis"
    assert queued.can_cancel is True


def test_projection_is_closed_and_blocked_page_withholds_zero_score(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    _profile, initial = _create_profile_and_submission(facade)
    confirmed = _confirm_matching(facade, _add_three_roles(facade, initial, tmp_path))
    manager.set_blocked_candidate()

    summary = facade.student_submission(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
    )
    review = facade.student_analysis_review(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
    )

    assert isinstance(summary, StudentSubmissionSummary)
    assert isinstance(review, StudentAnalysisReview)
    assert tuple(item.name for item in fields(summary)) == (
        "student_id",
        "submission_id",
        "revision",
        "status",
        "status_zh",
        "message_zh",
        "created_at",
        "updated_at",
        "files",
        "pages",
        "matches",
        "page_counts_by_role",
        "match_count",
        "scoring_confirmed_count",
        "diagnostic_confirmed_count",
        "candidate_available",
        "can_confirm_matching",
        "can_analyze",
        "can_retry",
        "can_cancel",
    )
    _assert_no_raw_private_projection(summary)
    _assert_no_raw_private_projection(review)
    assert review.candidate_only is True
    assert review.long_term_update_allowed is False
    assert len(review.items) == 1
    item = review.items[0]
    assert item.suggested_score is None
    assert item.suggested_score_withheld is True
    assert item.latest_teacher_score is None
    assert item.blockers_zh
    opaque_values = (
        STUDENT_ID,
        SUBMISSION_ID,
        summary.revision,
        PROFILE_ID,
        PROFILE_REVISION,
        summary.pages[0].sha256,
        summary.matches[0].match_id,
    )
    visible_text = "\n".join(
        [
            summary.status_zh,
            summary.message_zh,
            *(page.label_zh for page in summary.pages),
            *(match.label_zh for match in summary.matches),
            review.status_zh,
            review.message_zh,
            item.label_zh,
            item.observation_zh,
            *item.blockers_zh,
        ]
    )
    assert all(value not in visible_text for value in opaque_values)


def test_score_and_diagnosis_are_append_only_mappings_returning_fresh_review(
    desktop_paths: DesktopPaths, tmp_path: Path
) -> None:
    manager = _FakeStudentAnalysisManager()
    facade = _build_facade(desktop_paths, manager)
    _profile, initial = _create_profile_and_submission(facade)
    confirmed = _confirm_matching(facade, _add_three_roles(facade, initial, tmp_path))
    manager.set_blocked_candidate()
    before = facade.student_analysis_review(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
    )
    match_id = before.items[0].match_id

    scored = facade.record_student_score(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
        expected_revision=before.revision,
        match_id=match_id,
        teacher_score=0.5,
        reason="教师已核对可见作答。",
    )

    assert manager.score_calls == [
        {
            "expected_revision": before.revision,
            "match_id": match_id,
            "teacher_score": 0.5,
            "reason": "教师已核对可见作答。",
        }
    ]
    assert scored.items[0].latest_teacher_score == 0.5
    decision_id = scored.items[0].latest_scoring_decision_id
    assert decision_id is not None
    sections = facade.student_curriculum_sections()
    assert [section.display_label_zh for section in sections] == [
        "1.1 物质的分类",
        "1.2 物质的转化",
    ]
    assert all(section.section_key not in repr(section) for section in sections)

    diagnosed = facade.record_student_diagnosis(
        student_id=confirmed.student_id,
        submission_id=confirmed.submission_id,
        expected_revision=scored.revision,
        match_id=match_id,
        scoring_decision_id=decision_id,
        decision="edit",
        result="partial",
        primary_error_type="concept",
        secondary_error_types=("chemical_language",),
        curriculum_section_keys=(sections[0].section_key,),
        teacher_note="教师确认概念与化学用语均需订正。",
    )

    assert manager.diagnosis_calls == [
        {
            "expected_revision": scored.revision,
            "match_id": match_id,
            "scoring_decision_id": decision_id,
            "decision": "edit",
            "result": "partial",
            "primary_error_type": "concept",
            "secondary_error_types": ["chemical_language"],
            "curriculum_section_keys": [sections[0].section_key],
            "teacher_note": "教师确认概念与化学用语均需订正。",
        }
    ]
    assert diagnosed.items[0].latest_diagnostic_decision is not None
    assert diagnosed.candidate_only is True
    assert diagnosed.long_term_update_allowed is False


def test_recent_submission_survives_facade_recreation_and_shutdown_is_idempotent(
    desktop_paths: DesktopPaths,
) -> None:
    manager = _FakeStudentAnalysisManager()
    first = _build_facade(desktop_paths, manager)
    _profile, created = _create_profile_and_submission(first)
    assert first.student_submissions(student_id=STUDENT_ID, limit=1) == (created,)

    restarted = _build_facade(desktop_paths, manager)
    restored = restarted.student_submissions(student_id=STUDENT_ID, limit=1)

    assert len(restored) == 1
    assert restored[0].submission_id == SUBMISSION_ID
    assert restored[0].status == "awaiting_upload"
    restarted.shutdown()
    restarted.shutdown()
    assert manager.shutdown_calls == 1
